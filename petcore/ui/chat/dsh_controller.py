"""DSH 通道的 Qt 适配：后台线程读 SSE，信号回 GUI。"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from ...net.dsh import DshClient, DshError

logger = logging.getLogger("petcore.ui.chat.dsh")


class DshController(QObject):
    connected = Signal(bool, str)
    history_loaded = Signal(object)
    frame = Signal(object)
    failed = Signal(str)

    def __init__(self, client: DshClient | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def start(self) -> None:
        if self._client is None:
            self.connected.emit(False, "missing")
            return
        stop = threading.Event()
        self._stop = stop
        thread = threading.Thread(
            target=self._run, args=(stop,), name="petcore-dsh", daemon=True
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, stop: threading.Event) -> None:
        client = self._client
        if client is None:
            return
        try:
            info = client.ping()
        except DshError as exc:
            if self._stop is stop:
                self.connected.emit(False, exc.reason)
            return
        except Exception:  # noqa: BLE001
            if self._stop is stop:
                self.connected.emit(False, "network")
            return
        if self._stop is stop:
            self.connected.emit(True, str(info.get("version") or ""))
        try:
            self.history_loaded.emit(client.history(20))
        except Exception:  # noqa: BLE001 - 历史失败不影响实时流
            pass
        try:
            for frame in client.events(stop):
                if stop.is_set():
                    break
                self.frame.emit(frame)
        finally:
            if self._stop is stop:
                self.connected.emit(False, "closed")

    def _post(self, kind: str, payload) -> None:
        client = self._client
        if client is None:
            return

        def run() -> None:
            try:
                if kind == "prompt":
                    client.prompt(str(payload))
                elif kind == "answer":
                    request_id, allow = payload
                    client.answer(str(request_id), bool(allow))
                elif kind == "cancel":
                    client.cancel()
            except DshError as exc:
                self.failed.emit(exc.reason)
            except Exception:  # noqa: BLE001
                self.failed.emit("network")

        threading.Thread(
            target=run, name=f"petcore-dsh-{kind}", daemon=True
        ).start()

    def send(self, text: str) -> None:
        value = str(text or "").strip()
        if value:
            self._post("prompt", value[:4000])

    def answer(self, request_id: str, allow: bool) -> None:
        self._post("answer", (request_id, allow))

    def cancel(self) -> None:
        self._post("cancel", None)

    def shutdown(self) -> None:
        self.stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
