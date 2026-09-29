"""Feature-owned manual screen session; Qt windows only provide bound ports.

The optional source-build fallback is deliberately not available to an external
package. Results cross a queued Qt bridge and are scoped to this session/epoch.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QObject, Signal, Slot

from ..common.models import VisionRequestConfig
from .presentation import present_manual_result
from .runtime_context import ScreenRuntimeContext

ResultCallback = Callable[[str, str, bool], None]
ManualRequest = Callable[[VisionRequestConfig, str, str, ResultCallback], bool]


class _ResultBridge(QObject):
    completed = Signal(object)
    finished = Signal()

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self.completed.connect(self.deliver)
        self.finished.connect(self.thread_finished)

    @Slot(object)
    def deliver(self, result):
        self._owner._receive(*result)

    @Slot()
    def thread_finished(self):
        self._owner._thread_finished()


class ManualScreenHost:
    def __init__(
        self,
        context: ScreenRuntimeContext,
        request: ManualRequest,
        cancel_request: Callable[[ResultCallback], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.context = context
        self._request = request
        self._cancel_request = cancel_request
        self._clock = clock
        self.busy = False
        self.last_requested = 0.0
        self.revision: str | None = None
        self._generation = 0
        self._identity = uuid.uuid4().hex
        self._disposed = False
        self._callback: ResultCallback | None = None
        self._canceled = threading.Event()
        self._thread: threading.Thread | None = None
        self._bridge = _ResultBridge(self)
        self._release = context.bind_lifecycle(self.cancel, lambda: None, self.dispose)

    def _authorized(self) -> bool:
        return not self._disposed and self.context.execution_enabled()

    def start(self) -> None:
        if not self._authorized():
            return
        if self.busy or self._thread is not None:
            self.context.show_bubble("上一张还没看完呢…", 4000)
            return
        now = self._clock()
        if now - self.last_requested < 4.0:
            self.context.show_bubble("喘口气嘛，刚看过啦…", 4000)
            return
        resolution = self.context.vision.resolve("manual")
        if not resolution.ready or resolution.request is None:
            self.context.show_bubble("请先在设置 → 自动化与联动 → 屏幕理解中配置或确认迁移", 6000)
            return
        self._generation += 1
        self._canceled = threading.Event()
        self.revision = resolution.revision
        self.last_requested = now
        self.busy = True
        self.context.show_bubble("让我看看…", 6000)
        provider = resolution.request
        pet_name = self.context.window_state().display_name
        self._callback = self.result_callback()
        try:
            if self._request(provider, provider.system_prompt, pet_name, self._callback):
                return
        except Exception:
            # Never include the provider/request payload (which holds a secret).
            logging.warning("通过主动识屏 Worker 发起手动识屏失败")
        # Dispatch can re-enter the GUI loop or synchronously revoke this owner.
        if not self._authorized() or self._canceled.is_set():
            return
        if not self.context.allow_in_process:
            self._callback("识屏服务不可用，请重试或回滚功能包", "", True)
            return
        self._thread = threading.Thread(
            target=self.run_in_process,
            args=(provider, provider.system_prompt, pet_name, self._generation, self._canceled),
            daemon=True,
            name="pet-look-screen",
        )
        self._thread.start()

    def result_callback(self) -> ResultCallback:
        generation = self._generation
        revision = self.revision
        delivered = False

        def result(text: str, user_text: str, error: bool) -> None:
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self._receive(generation, revision, text, user_text, error)

        return result

    def complete(self, text: str, user_text: str, error: bool) -> None:
        """Legacy result signal facade, already dispatched on the GUI thread."""
        self._receive(self._generation, self.revision, text, user_text, error)

    def _receive(self, generation, revision, text, user_text, error):
        if generation != self._generation or not self._authorized():
            return
        self.busy = False
        self._callback = None
        present_manual_result(
            self.context.vision,
            revision,
            text,
            user_text,
            error,
            self.context.show_bubble,
            lambda question, answer: self.context.synchronize(f"{self._identity}:{generation}", "manual", question, answer),
        )

    def run_in_process(self, provider, system_prompt, pet_name="", generation=None, canceled=None) -> None:
        """Source/default compatibility only; no GUI/window/vault access here."""
        if not self.context.allow_in_process:
            return
        generation = self._generation if generation is None else generation
        canceled = self._canceled if canceled is None else canceled
        revision = self.revision
        try:
            if canceled.is_set():
                return
            try:
                from ..worker import vision

                shot = vision.capture_screen_bytes()
                if canceled.is_set():
                    return
                app_info = vision.foreground_app_info()
                reply = vision.ask_about_screen(shot, app_info, system_prompt, provider, pet_name=pet_name)
                user_text = f"[看看屏幕] 前台窗口：{app_info}" if app_info else "[看看屏幕]"
                result = (generation, revision, reply, user_text, False)
            except Exception as exc:
                logging.warning("看看屏幕失败: %s", type(exc).__name__)
                result = (generation, revision, "视觉请求失败，请检查屏幕理解配置或稍后重试", "", True)
            if not canceled.is_set() and shiboken6.isValid(self._bridge):
                self._bridge.completed.emit(result)
        finally:
            # Retain the bridge through cancellation, then clean up on its GUI thread.
            if shiboken6.isValid(self._bridge):
                self._bridge.finished.emit()

    def cancel(self) -> None:
        self._generation += 1
        self._canceled.set()
        self.busy = False
        callback, self._callback = self._callback, None
        if callback is not None:
            self._cancel_request(callback)

    def _thread_finished(self) -> None:
        self._thread = None
        if self._disposed and shiboken6.isValid(self._bridge):
            self._bridge.deleteLater()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.cancel()
        self._release()
        if self._thread is None and shiboken6.isValid(self._bridge):
            self._bridge.deleteLater()
