"""快速对话气泡的控制器：窗口/会话在 Core，模型调用在 mod。

线程纪律与 ``ChatController`` 相同：``token/done/error`` 可能来自 mod 的后台线程，
一律经排队信号回到 GUI 线程再碰控件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal

from .controller import asr_reason_text
from .quick import QuickChatBubble

logger = logging.getLogger("petcore.ui.chat.quick")

QuickSubmit = Callable[[str, int], None]


@dataclass
class _QuickState:
    owner: str
    slot: int
    bubble: QuickChatBubble
    active: bool = False


class QuickChatController(QObject):
    _token_received = Signal(str, int, str)
    _reasoning_received = Signal(str, int, str)
    _status_received = Signal(str, int, str)
    _done_received = Signal(str, int, str)
    _error_received = Signal(str, int, str, str)

    def __init__(self, host) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self._states: dict[tuple[str, int], _QuickState] = {}
        self._submit_registry: dict[str, QuickSubmit] = {}
        self._recording_key: tuple[str, int] | None = None
        self._asr = getattr(host, "asr", None)
        if self._asr is not None:
            self._asr.recording_changed.connect(self._on_asr_recording)
            self._asr.finished.connect(self._on_asr_finished)
            self._asr.failed.connect(self._on_asr_failed)
            partial = getattr(self._asr, "partial", None)
            if partial is not None:
                partial.connect(self._on_asr_partial)
        self._token_received.connect(
            self._apply_token, Qt.ConnectionType.QueuedConnection
        )
        self._reasoning_received.connect(
            self._apply_reasoning, Qt.ConnectionType.QueuedConnection
        )
        self._status_received.connect(
            self._apply_status, Qt.ConnectionType.QueuedConnection
        )
        self._done_received.connect(
            self._apply_done, Qt.ConnectionType.QueuedConnection
        )
        self._error_received.connect(
            self._apply_error, Qt.ConnectionType.QueuedConnection
        )

    # ---------------------------------------------------------------- 注册
    def on_submit(self, owner: str, callback: QuickSubmit) -> Callable[[], None]:
        owner = str(owner)
        self._submit_registry[owner] = callback

        def unsubscribe() -> None:
            if self._submit_registry.get(owner) is callback:
                self._submit_registry.pop(owner, None)

        return unsubscribe

    def has_handler(self, owner: str) -> bool:
        return str(owner) in self._submit_registry

    # ---------------------------------------------------------------- 窗口
    def bubble_for(self, owner: str, slot: int | None = None) -> QuickChatBubble | None:
        state = self._states.get((str(owner), self._slot(slot)))
        return state.bubble if state is not None else None

    def open(self, owner: str, *, pet_slot: int | None = None) -> bool:
        owner = str(owner)
        state = self._ensure(owner, pet_slot)
        window = self._pet_window(state.slot)
        state.bubble.show_for_pet(window)
        return True

    def close(self, owner: str, *, pet_slot: int | None = None) -> None:
        state = self._states.get((str(owner), self._slot(pet_slot)))
        if state is not None:
            state.bubble.close()

    def is_open(self, owner: str, *, pet_slot: int | None = None) -> bool:
        state = self._states.get((str(owner), self._slot(pet_slot)))
        return bool(state is not None and state.bubble.isVisible())

    def open_full_chat(self, owner: str, *, pet_slot: int | None = None) -> None:
        slot = self._slot(pet_slot)
        state = self._states.get((str(owner), slot))
        chat = self._chat()
        if chat is not None:
            try:
                chat.open(str(owner), pet_slot=slot)
            except Exception:
                logger.warning("failed to open the full chat window", exc_info=True)
        if state is not None:
            state.bubble.close()

    # ---------------------------------------------------------------- ChatPort 风格
    def token(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None:
        self._token_received.emit(str(owner), self._slot(pet_slot), str(delta or ""))

    def reasoning(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None:
        self._reasoning_received.emit(str(owner), self._slot(pet_slot), str(delta or ""))

    def status(self, owner: str, text: str, *, pet_slot: int | None = None) -> None:
        self._status_received.emit(str(owner), self._slot(pet_slot), str(text or ""))

    def done(self, owner: str, text: str = "", *, pet_slot: int | None = None) -> None:
        self._done_received.emit(str(owner), self._slot(pet_slot), str(text or ""))

    def error(
        self, owner: str, reason: str, message: str = "", *, pet_slot: int | None = None
    ) -> None:
        self._error_received.emit(
            str(owner), self._slot(pet_slot), str(reason or ""), str(message or "")
        )

    def history(self, owner: str, *, pet_slot: int | None = None) -> list[dict]:
        chat = self._chat()
        if chat is None:
            return []
        try:
            return list(chat.history(str(owner), pet_slot=self._slot(pet_slot)))
        except Exception:
            return []

    # ---------------------------------------------------------------- 内部
    def _slot(self, slot: int | None) -> int:
        if slot is not None:
            try:
                return max(0, int(slot))
            except (TypeError, ValueError):
                pass
        window = getattr(self.host, "window", None)
        return int(getattr(window, "slot", 0) or 0)

    def _chat(self):
        return getattr(self.host, "chat", None)

    def _pet_window(self, slot: int):
        pets = getattr(self.host, "pets", None)
        if pets is not None:
            try:
                window = pets.window_for_slot(int(slot))
                if window is not None:
                    return window
            except Exception:
                pass
        return getattr(self.host, "window", None)

    def _ensure(self, owner: str, pet_slot: int | None) -> _QuickState:
        owner = str(owner)
        slot = self._slot(pet_slot)
        key = (owner, slot)
        state = self._states.get(key)
        if state is not None:
            return state
        bubble = QuickChatBubble()
        bubble.submitted.connect(lambda text, k=key: self._on_submit(k, text))
        bubble.open_full_requested.connect(
            lambda k=key: self.open_full_chat(k[0], pet_slot=k[1])
        )
        bubble.record_requested.connect(lambda k=key: self._start_recording(k))
        bubble.record_stopped.connect(lambda k=key: self._stop_recording(k))
        if self._asr is None:
            bubble.record_button.hide()
        state = _QuickState(owner=owner, slot=slot, bubble=bubble)
        self._states[key] = state
        return state

    def _start_recording(self, key: tuple[str, int]) -> None:
        asr = self._asr
        state = self._states.get(key)
        if asr is None or state is None:
            return
        if self._recording_key is not None:
            state.bubble.set_recording(False, "上一段语音还没结束")
            return
        self._recording_key = key
        if not asr.start():
            self._recording_key = None
            state.bubble.set_recording(False, "麦克风不可用或正在识别")
            return
        state.bubble.set_recording(True, "正在录音…再点一次停止")

    def _stop_recording(self, key: tuple[str, int]) -> None:
        if self._recording_key == key and self._asr is not None:
            self._asr.stop()

    def _on_asr_recording(self, recording: bool) -> None:
        key = self._recording_key
        state = self._states.get(key) if key is not None else None
        if state is None:
            return
        state.bubble.set_recording(
            bool(recording), "" if recording else "正在识别…"
        )

    def _on_asr_partial(self, text: str) -> None:
        key = self._recording_key
        state = self._states.get(key) if key is not None else None
        if state is not None:
            state.bubble.set_partial_transcript(str(text or ""))

    def _on_asr_finished(self, text: str) -> None:
        key = self._recording_key
        self._recording_key = None
        state = self._states.get(key) if key is not None else None
        if state is None:
            return
        state.bubble.set_partial_transcript("")
        state.bubble.set_recording(False)
        config = getattr(self.host, "config", None)
        auto_send = False
        if config is not None:
            try:
                auto_send = bool(config.get("asr_auto_send", False))
            except Exception:  # noqa: BLE001
                auto_send = False
        state.bubble.insert_transcript(str(text or ""), auto_send=auto_send)

    def _on_asr_failed(self, reason: str) -> None:
        key = self._recording_key
        self._recording_key = None
        state = self._states.get(key) if key is not None else None
        if state is None:
            return
        state.bubble.set_partial_transcript("")
        state.bubble.set_recording(False, asr_reason_text(reason))

    def _on_submit(self, key: tuple[str, int], text: str) -> None:
        text = str(text or "").strip()
        state = self._states.get(key)
        if state is None or not text:
            return
        callback = self._submit_registry.get(state.owner)
        if callback is None:
            state.bubble.show_error("这个气泡还没有接入对话扩展")
            return
        chat = self._chat()
        if chat is not None:
            try:
                chat.append_to_session(
                    state.owner, "user", text, pet_slot=state.slot
                )
            except Exception:
                logger.warning("quick chat user turn failed to persist", exc_info=True)
        state.active = True
        state.bubble.begin()
        try:
            callback(text, state.slot)
        except Exception:
            state.active = False
            state.bubble.show_error("扩展处理消息时出错")
            logger.exception("quick chat submit callback failed")

    def _apply_token(self, owner: str, slot: int, delta: str) -> None:
        state = self._states.get((owner, slot))
        if state is None or not delta:
            return
        state.bubble.append_token(delta)

    def _apply_reasoning(self, owner: str, slot: int, delta: str) -> None:
        state = self._states.get((owner, slot))
        if state is not None and delta:
            state.bubble.set_hint("深度思考中…")

    def _apply_status(self, owner: str, slot: int, text: str) -> None:
        state = self._states.get((owner, slot))
        if state is not None and text:
            state.bubble.set_hint(str(text)[:60])

    def _apply_done(self, owner: str, slot: int, text: str) -> None:
        state = self._states.get((owner, slot))
        if state is None:
            return
        final = str(text or "").strip() or state.bubble.text()
        state.active = False
        state.bubble.finish(final)
        chat = self._chat()
        if chat is not None and final:
            try:
                chat.append_to_session(
                    owner, "assistant", final, pet_slot=slot
                )
            except Exception:
                logger.warning("quick chat reply failed to persist", exc_info=True)

    def _apply_error(self, owner: str, slot: int, reason: str, message: str) -> None:
        state = self._states.get((owner, slot))
        if state is None:
            return
        state.active = False
        detail = str(message or "").strip() or str(reason or "未知错误")
        state.bubble.show_error(detail)
