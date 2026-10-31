"""语音输入通道：录音线程 + 识别 + Qt 信号。

一次录音一个短命线程（不是常驻 worker）：录音是用户主动、低频的动作，线程在
``stop()`` 后完成识别并退出。GUI 线程只收 ``recording_changed/finished/failed``
三个排队信号，不碰声卡、不做识别。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..platform.mic import MicError, MicRecorder
from ..speech.asr import AsrService

logger = logging.getLogger("petcore.ui.asr")

MAX_SECONDS = 60
MIN_SECONDS = 3


class AsrChannel(QObject):
    recording_changed = Signal(bool)
    partial = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: AsrService,
        config: Any = None,
        *,
        recorder: MicRecorder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._config = config
        self._recorder = recorder or MicRecorder()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._recording = False

    def _cfg(self, key: str, default: Any) -> Any:
        getter = getattr(self._config, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def _max_seconds(self) -> int:
        try:
            value = int(self._cfg("asr_max_seconds", 30) or 30)
        except (TypeError, ValueError):
            value = 30
        return max(MIN_SECONDS, min(MAX_SECONDS, value))

    def _device(self) -> int:
        try:
            return max(0, int(self._cfg("asr_device", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _stream_enabled(self) -> bool:
        return bool(self._cfg("asr_stream_enabled", True))

    def _partial_interval(self) -> float:
        try:
            interval = float(self._cfg("asr_partial_interval_ms", 800)) / 1000.0
        except (TypeError, ValueError):
            interval = 0.8
        interval = max(0.4, min(3.0, interval))
        try:
            engine = str(self._service.engine() or "")
        except Exception:  # noqa: BLE001 - 未知引擎按 HTTP 的节奏
            engine = "http"
        # 系统识别每次都是一次短进程（实测 1–2 秒）；比这更密的增量只会排队。
        if engine == "sapi":
            interval = max(1.5, interval)
        return interval

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(self) -> bool:
        if not bool(self._cfg("asr_enabled", True)):
            self.failed.emit("disabled")
            return False
        with self._lock:
            if self._recording or (self._thread is not None and self._thread.is_alive()):
                return False
            self._stop_event.clear()
            self._cancel_event.clear()
            self._recording = True
            thread = threading.Thread(
                target=self._run,
                name="petcore-asr",
                args=(self._device(), self._max_seconds()),
                daemon=True,
            )
            self._thread = thread
        self.recording_changed.emit(True)
        thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._stop_event.set()

    def _run(self, device: int, max_seconds: int) -> None:
        try:
            started = True
            try:
                self._recorder.start(device, max_seconds=max_seconds)
            except MicError as exc:
                started = False
                self.failed.emit(exc.reason)
                return
            except Exception as exc:  # noqa: BLE001
                started = False
                self.failed.emit("record")
                logger.warning("microphone start failed: %s", type(exc).__name__)
                return
            if not started:
                return
            stream_enabled = self._stream_enabled()
            interval = self._partial_interval()
            deadline = time.monotonic() + float(max_seconds)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not stream_enabled:
                    # 没有实时预览时不要空转；一觉睡到录音结束/用户停止。
                    self._stop_event.wait(timeout=remaining)
                    break
                # 先等一个完整间隔再识别；识别返回后下一轮重新等，慢引擎
                # （sapi 单次 1–2 秒）不会被“已经超过间隔”逼成不间断识别。
                if self._stop_event.wait(timeout=min(interval, remaining)):
                    break
                try:
                    wav = self._recorder.peek_wav()
                except Exception:
                    continue
                if not wav:
                    continue
                try:
                    text = self._service.recognize(wav)
                except Exception:
                    # 预览失败不打断正式识别：停止后还会完整识别一次。
                    continue
                value = str(text or "").strip()
                if value:
                    self.partial.emit(value)
            if self._cancel_event.is_set():
                try:
                    self._recorder.cancel()
                except Exception:  # noqa: BLE001 - 取消失败也要收口
                    pass
                self.failed.emit("cancelled")
                return
            try:
                wav = self._recorder.stop()
            except MicError as exc:
                self.failed.emit(exc.reason)
                return
            except Exception as exc:  # noqa: BLE001
                self.failed.emit("record")
                logger.warning("microphone stop failed: %s", type(exc).__name__)
                return
            try:
                text = self._service.recognize(wav)
            except Exception as exc:  # noqa: BLE001 - AsrError 带 reason
                reason = str(getattr(exc, "reason", "") or "asr")
                self.failed.emit(reason)
                return
            text = str(text or "").strip()
            if text:
                self.finished.emit(text)
            else:
                self.failed.emit("empty")
        finally:
            with self._lock:
                self._recording = False
                self._thread = None
            self.recording_changed.emit(False)

    def shutdown(self) -> None:
        self.cancel()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
