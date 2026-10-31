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
        self._generation = 0

    @property
    def available(self) -> bool:
        return self._client is not None

    def start(self) -> None:
        if self._client is None:
            self.connected.emit(False, "missing")
            return
        stop = threading.Event()
        self._stop = stop
        self._generation += 1
        generation = self._generation
        thread = threading.Thread(
            target=self._run,
            args=(stop, generation),
            name="petcore-dsh",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._generation += 1
        self._stop.set()
        close = getattr(self._client, "close_active", None)
        if callable(close):
            close()

    def _run(self, stop: threading.Event, generation: int) -> None:
        client = self._client
        if client is None:
            return

        def alive() -> bool:
            return self._generation == generation and self._stop is stop

        try:
            info = client.ping()
        except DshError as exc:
            if alive():
                self.connected.emit(False, exc.reason)
            return
        except Exception:  # noqa: BLE001
            if alive():
                self.connected.emit(False, "network")
            return
        if alive():
            self.connected.emit(True, str(info.get("version") or ""))
        try:
            self.history_loaded.emit(client.history(20))
        except Exception:  # noqa: BLE001 - 历史失败不影响实时流
            pass
        try:
            for frame in client.events(stop):
                if not alive():
                    break
                self.frame.emit(frame)
        finally:
            if alive():
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


class DshHub(QObject):
    """进程内共享的一条 DSH SSE；多个聊天窗只订阅、不各自建连。

    每个窗口各开一条 SSE 时，同一条审批会被广播到所有窗口：在 A 窗点同意后
    B 窗的按钮再点就是 404，而卡片还挂着——看起来就是“审批卡死”。这里把
    连接、历史和帧统一收口，视图只负责渲染。
    """

    connected = Signal(bool, str)
    history_loaded = Signal(object)
    frame = Signal(object)
    failed = Signal(str)

    def __init__(self, client: DshClient | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._controller = DshController(client, self)
        self._controller.connected.connect(self._on_connected)
        self._controller.history_loaded.connect(self._on_history)
        self._controller.frame.connect(self.frame)
        self._controller.failed.connect(self.failed)
        self._views = 0
        self._history: list[dict] | None = None
        self._connected = False
        self._last_detail = ""
        self._version = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def history_cache(self) -> list[dict] | None:
        return None if self._history is None else list(self._history)

    def acquire(self) -> None:
        if self._views > 0:
            # 第二个视图复用已有连接时，补发一次当前连接状态；否则新视图会
            # 一直停在“DSH 未连接”，直到下一次 connected 事件（可能永远不来）。
            if self._connected:
                self.connected.emit(True, self._version)
            elif self._last_detail:
                self.connected.emit(False, self._last_detail)
        self._views += 1
        if self._views == 1:
            self._controller.start()

    def release(self) -> None:
        self._views = max(0, self._views - 1)
        if self._views == 0:
            self._controller.stop()

    def restart(self) -> None:
        """用户点「重试连接」：即使还有别的视图，也重建这条共享连接。"""
        self._controller.stop()
        self._controller.start()

    def send(self, text: str) -> None:
        self._controller.send(text)

    def answer(self, request_id: str, allow: bool) -> None:
        self._controller.answer(request_id, allow)

    def cancel(self) -> None:
        self._controller.cancel()

    def shutdown(self) -> None:
        self._views = 0
        self._controller.shutdown()

    def _on_connected(self, connected: bool, detail: str) -> None:
        self._connected = bool(connected)
        self._last_detail = str(detail or "")
        if connected and detail:
            self._version = str(detail)
        self.connected.emit(bool(connected), detail)

    def _on_history(self, items) -> None:
        try:
            self._history = [dict(item) for item in items if isinstance(item, dict)]
        except Exception:  # noqa: BLE001 - 坏历史不挡实时流
            self._history = []
        self.history_loaded.emit(self.history_cache() or [])
