"""DSH 通道的 Qt 适配：后台线程读 SSE，信号回 GUI。

两条 2026-11-01 修出来的硬约束：

- **关流不许在 GUI 线程做**：``DshController.stop()`` 以前直接调
  ``client.close_active()``，而 ``urllib`` 的 ``response.close()`` 会被同一条
  SSE 读线程的 socket 读挡住（最长到 30s 超时）。用户在「DSH 会话 → AI 会话」
  之间切换时界面就卡在那里。现在 ``stop()`` 只置事件 + 起一条 daemon 线程去关。
- **待审批是 Hub 级状态，不是视图级**：审批只在 DSH 视图可见时才收得到的话，
  用户停在 AI 视图（或窗口关着）就会整条错过——真机实测 00:34 的卡片
  10 分钟没人看见直接作废。Hub 常驻订阅时把 ``approval-request`` 记下来，
  任何视图（以及后来才创建的视图）都能补画。
"""

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

    def __init__(
        self,
        client: DshClient | None,
        parent: QObject | None = None,
        *,
        history_limit: int = 200,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._history_limit = max(20, min(500, int(history_limit)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._cursor = 0
        self._boot_id = ""

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
        """停止读流：调用线程只置标志，关 socket 交给后台线程。

        ``response.close()`` 会去抢读线程手里的 socket 锁；在 GUI 线程上等它
        就是用户看到的「切回 AI 会话卡一下」。这里不再同步关流。
        """
        self._generation += 1
        self._stop.set()
        close = getattr(self._client, "close_active", None)
        if not callable(close):
            return
        thread = threading.Thread(
            target=self._close_streams,
            args=(close,),
            name="petcore-dsh-close",
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _close_streams(close) -> None:
        try:
            close()
        except Exception:  # noqa: BLE001 - 关流失败不影响新连接
            logger.debug("dsh close_active failed", exc_info=True)

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
            boot = str(info.get("bootId") or "")
            if boot and boot != self._boot_id:
                self._boot_id = boot
                self._cursor = 0
            self.connected.emit(True, str(info.get("version") or ""))
        try:
            self.history_loaded.emit(client.history(self._history_limit))
        except Exception:  # noqa: BLE001 - 历史失败不影响实时流
            pass
        try:
            for frame in client.events(stop, since=self._cursor):
                if not alive():
                    break
                seq = frame.get("seq")
                if isinstance(seq, int):
                    self._cursor = max(self._cursor, seq)
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
                    text, context = payload
                    client.prompt(str(text), context=context)
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

    def send(self, text: str, *, context: dict | None = None) -> None:
        value = str(text or "").strip()
        if value:
            self._post("prompt", (value[:4000], dict(context or {})))

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

    连接生命周期由**持有者**决定，不由视图可见性决定：

    - ``acquire_persistent()``：聊天窗存在且 DSH 模式打开时由 controller 持有，
      即使用户停在 AI 视图也保持订阅（审批必须收得到）；
    - ``acquire()/release()``：视图登记，只影响引用计数；
    - 两者都为 0 才真正停流。
    """

    connected = Signal(bool, str)
    history_loaded = Signal(object)
    frame = Signal(object)
    failed = Signal(str)
    #: 新到的审批请求（dict）。ChatController 用它冒泡 + 唤起窗口。
    approval_pending = Signal(object)

    def __init__(
        self,
        client: DshClient | None,
        parent: QObject | None = None,
        *,
        on_frame=None,
        local_history=None,
        history_limit: int = 200,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._controller = DshController(client, self, history_limit=history_limit)
        self._controller.connected.connect(self._on_connected)
        self._controller.history_loaded.connect(self._on_history)
        self._controller.frame.connect(self._relay_frame)
        self._controller.failed.connect(self.failed)
        self._on_frame = on_frame
        self._local_history = local_history
        self._views = 0
        self._persistent = False
        self._running = False
        self._history: list[dict] | None = None
        self._pending: dict[str, dict] = {}
        self._connected = False
        self._last_detail = ""
        self._version = ""
        self._boot_id = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def history_cache(self) -> list[dict] | None:
        return None if self._history is None else list(self._history)

    def clear_history(self) -> None:
        self._history = []

    def pending_approvals(self) -> list[dict]:
        """仍然挂着的审批（按到达顺序）；视图重建时用它补画。"""
        return [dict(item) for item in self._pending.values()]

    def clear_pending(self) -> None:
        self._pending.clear()

    def local_history(self) -> list[dict]:
        provider = self._local_history
        if not callable(provider):
            return []
        try:
            items = provider()
        except Exception:
            return []
        return [dict(item) for item in items if isinstance(item, dict)]

    def _relay_frame(self, frame) -> None:
        if isinstance(frame, dict):
            kind = str(frame.get("kind") or "")
            if kind == "hello":
                boot = str(frame.get("bootId") or "")
                if boot:
                    self._boot_id = boot
                if frame.get("reset"):
                    # 桥重启过：旧会话的待审批已经不可能被答复。
                    self._history = []
                    self._pending.clear()
            elif kind == "approval-request":
                request_id = str(frame.get("id") or "")
                if request_id:
                    self._pending[request_id] = dict(frame)
                    self.approval_pending.emit(dict(frame))
            elif kind == "approval-resolved":
                self._pending.pop(str(frame.get("id") or ""), None)
        if callable(self._on_frame):
            try:
                self._on_frame(frame)
            except Exception:
                pass
        self.frame.emit(frame)

    def boot_id(self) -> str:
        return self._boot_id

    # ---------------------------------------------------------------- 连接
    def _sync_running(self) -> None:
        should = bool(self._persistent or self._views > 0)
        if should and not self._running:
            self._running = True
            # 常驻订阅下历史/连接状态都会进缓存，视图后建时直接补发，不必重连。
            self._controller.start()
        elif not should and self._running:
            self._running = False
            self._controller.stop()

    def acquire_persistent(self) -> None:
        self._persistent = True
        self._sync_running()

    def release_persistent(self) -> None:
        self._persistent = False
        self._sync_running()

    def acquire(self) -> None:
        already = self._running
        self._views += 1
        if already:
            # 连接已经活着（常驻订阅或第二个视图）：补发一次当前状态，否则新
            # 视图会一直停在“DSH 未连接”，直到下一次 connected 事件。
            self._replay_state()
        self._sync_running()

    def release(self) -> None:
        self._views = max(0, self._views - 1)
        self._sync_running()

    def _replay_state(self) -> None:
        if self._connected:
            self.connected.emit(True, self._version)
        elif self._last_detail:
            self.connected.emit(False, self._last_detail)
        if self._history is not None:
            self.history_loaded.emit(self.history_cache() or [])

    def restart(self) -> None:
        """用户点「重试连接」：即使还有别的视图，也重建这条共享连接。"""
        self._controller.stop()
        self._running = True
        self._controller.start()

    def send(self, text: str, *, context: dict | None = None) -> None:
        self._controller.send(text, context=context)

    def answer(self, request_id: str, allow: bool) -> None:
        self._controller.answer(request_id, allow)

    def cancel(self) -> None:
        self._controller.cancel()

    def shutdown(self) -> None:
        self._views = 0
        self._persistent = False
        self._running = False
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
