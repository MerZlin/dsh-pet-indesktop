"""聊天窗口的 Core 实现：ChatPort 的唯一实现者。

每个 ``(owner, pet_slot)`` 一个窗口；会话按 slot + 角色落盘，互不串流。
mod 通过端口注册「用户发送 / 停止」回调，窗口关闭只隐藏、不销毁历史。

``record()`` 允许在**没有窗口**的情况下把消息写进会话（「看看屏幕」这类静默同步），
所以每个方法的 ``state.window`` 都可能是 ``None``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from ...chat.session_store import ChatSession, SessionStore
from .styles import safe_accent
from .window import ChatWindow

logger = logging.getLogger("petcore.ui.chat")

#: ASR 失败原因 → 用户能读的一行（不显示异常文本）。
_ASR_REASON_TEXT = {
    "disabled": "语音输入已在设置里关闭",
    "unsupported": "这台机器没有可用的系统语音识别",
    "no_device": "没有检测到麦克风",
    "busy": "上一段语音还在录制",
    "open": "麦克风被其它程序占用",
    "record": "录音失败",
    "unconfigured": "还没有填写本地识别服务地址",
    "insecure": "本地识别服务只允许 127.0.0.1 或 https",
    "network": "识别服务连不上",
    "timeout": "识别超时",
    "auth": "识别服务需要鉴权",
    "rate_limit": "识别请求过于频繁",
    "protocol": "识别服务返回了无法识别的响应",
    "empty": "没有识别出内容",
    "unavailable": "系统识别不可用",
    "too_large": "录音太长，无法识别",
    "not_recording": "没有在录音",
    "cancelled": "已取消录音",
}


def asr_reason_text(reason: object) -> str:
    return _ASR_REASON_TEXT.get(str(reason or ""), "语音输入失败")


@dataclass
class _ChatState:
    owner: str
    slot: int
    store: SessionStore
    session: ChatSession
    window: ChatWindow | None = None
    submit_callback: Callable[[str, int], None] | None = None
    stop_callback: Callable[[int], None] | None = None
    active: bool = False
    extras: dict = field(default_factory=dict)


class ChatController(QObject):
    """窗口 + 会话 + 流式合帧；对外只暴露 ChatPort 的语义。

    ``token/done/error`` 可能由 mod 的后台线程调用：这里用信号跨线程排队，
    GUI 线程只做合并刷新，绝不在 worker 线程碰控件。
    """

    token_received = Signal(str, str, int)
    reasoning_received = Signal(str, str, int)
    status_received = Signal(str, str, int)
    done_received = Signal(str, str, int)
    error_received = Signal(str, str, str, int)
    record_received = Signal(str, str, str, int)

    def __init__(self, host, root: Path) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.root = Path(root)
        self._states: dict[tuple[str, int], _ChatState] = {}
        self._submit_registry: dict[str, Callable[[str, int], None]] = {}
        self._stop_registry: dict[str, Callable[[int], None]] = {}
        self._recording_key: tuple[str, int] | None = None
        self._asr = getattr(host, "asr", None)
        if self._asr is not None:
            self._asr.recording_changed.connect(self._on_asr_recording)
            self._asr.finished.connect(self._on_asr_finished)
            self._asr.failed.connect(self._on_asr_failed)
        self.token_received.connect(self._apply_token)
        self.reasoning_received.connect(self._apply_reasoning)
        self.status_received.connect(self._apply_status)
        self.done_received.connect(self._apply_done)
        self.error_received.connect(self._apply_error)
        self.record_received.connect(self._apply_record)

    # ---------------------------------------------------------------- 公共状态
    def states(self) -> dict[tuple[str, int], _ChatState]:
        return dict(self._states)

    def window_for(self, owner: str, slot: int | None = None) -> ChatWindow | None:
        key = (str(owner), self._slot(slot))
        state = self._states.get(key)
        return state.window if state is not None else None

    # ---------------------------------------------------------------- ChatPort
    def open(self, owner: str, *, pet_slot: int | None = None, title: str = "") -> bool:
        state = self._ensure(owner, pet_slot)
        window = state.window
        if window is None:
            return False
        if title:
            window.setWindowTitle(str(title))
        window.show()
        window.raise_()
        window.activateWindow()
        return True

    def append(
        self, owner: str, role: str, text: str, *, pet_slot: int | None = None
    ) -> None:
        state = self._ensure(owner, pet_slot)
        if state.window is not None:
            state.window.append_message(role, text)
        state.session.append(role, text)
        self._save(state)

    def record(
        self, owner: str, role: str, text: str, *, pet_slot: int | None = None
    ) -> None:
        """静默写入历史：不创建、不显示窗口；窗口已存在时顺带补上气泡。

        ``record`` 可能由后台线程调用（「看看屏幕」的收尾），因此和
        ``token/done/error`` 一样走信号：GUI 线程才碰控件。
        """
        text = str(text or "").strip()
        if not text:
            return
        self.record_received.emit(str(owner), str(role or "user"), text, self._slot(pet_slot))

    def token(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None:
        self.token_received.emit(str(owner), str(delta or ""), self._slot(pet_slot))

    def reasoning(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None:
        """深度思考增量：只进已存在的窗口，不主动唤出对话窗。"""
        self.reasoning_received.emit(str(owner), str(delta or ""), self._slot(pet_slot))

    def status(self, owner: str, text: str, *, pet_slot: int | None = None) -> None:
        """联网检索进度行：只进已存在的窗口。"""
        self.status_received.emit(str(owner), str(text or ""), self._slot(pet_slot))

    def done(self, owner: str, text: str = "", *, pet_slot: int | None = None) -> None:
        self.done_received.emit(str(owner), str(text or ""), self._slot(pet_slot))

    def error(
        self,
        owner: str,
        reason: str,
        message: str = "",
        *,
        pet_slot: int | None = None,
    ) -> None:
        self.error_received.emit(
            str(owner), str(reason), str(message or ""), self._slot(pet_slot)
        )

    def history(self, owner: str, *, pet_slot: int | None = None) -> list[dict[str, str]]:
        state = self._ensure(owner, pet_slot, with_window=False)
        return [dict(item) for item in state.session.messages]

    def append_to_session(
        self, owner: str, role: str, text: str, *, pet_slot: int | None = None
    ) -> None:
        """同步写入会话（快速对话用）：有完整窗口时顺带补气泡。"""
        state = self._ensure(owner, pet_slot, with_window=False)
        if not str(text or "").strip():
            return
        state.session.append(role, text)
        self._save(state)
        if state.window is not None:
            state.window.append_message(role, text)
            self._refresh_history(state)

    def take_attachments(
        self, owner: str, *, pet_slot: int | None = None
    ) -> list[dict]:
        """取走该 ``(owner, slot)`` 上一次提交的附件（取一次即清空）。"""
        state = self._states.get((str(owner), self._slot(pet_slot)))
        if state is None:
            return []
        items = state.extras.pop("attachments", None) or []
        return [dict(item) for item in items if isinstance(item, dict)]

    def close(self, owner: str, *, pet_slot: int | None = None) -> None:
        state = self._states.get((str(owner), self._slot(pet_slot)))
        if state is not None and state.window is not None:
            state.window.hide()

    def cancel(self, owner: str, *, pet_slot: int | None = None) -> None:
        state = self._states.get((str(owner), self._slot(pet_slot)))
        if state is None:
            return
        if state.stop_callback is not None:
            try:
                state.stop_callback(state.slot)
            except Exception:
                logger.exception("chat stop callback failed")
        if state.window is not None:
            state.window.finish_stream(state.window.stream_text())
        state.active = False

    def is_open(self, owner: str, *, pet_slot: int | None = None) -> bool:
        state = self._states.get((str(owner), self._slot(pet_slot)))
        return bool(state is not None and state.window is not None and state.window.isVisible())

    def apply_appearance(
        self,
        *,
        background=None,
        accent=None,
        always_on_top=None,
        follow=None,
        owner=None,
    ) -> None:
        """把外观实时推到已存在的聊天窗；``None`` 表示读当前配置。

        只改窗口外观，不创建窗口、不唤起窗口（``record`` 的静默语义不变）。
        值没变的窗口不动，避免每次保存设置都重建 QSS / 重放置顶。
        """
        target_background = (
            str(self._config_get("chat_background", "") or "")
            if background is None
            else str(background or "").strip()
        )
        target_accent = safe_accent(
            self._config_get("chat_accent", "") if accent is None else accent
        )
        target_on_top = (
            bool(self._config_get("chat_always_on_top", False))
            if always_on_top is None
            else bool(always_on_top)
        )
        target_follow = (
            bool(self._config_get("chat_follow_pet", False))
            if follow is None
            else bool(follow)
        )
        target_font_size = self._font_size()
        target_font_family = self._font_family()
        wanted_owner = None if owner is None else str(owner)
        for (key_owner, _slot), state in self._states.items():
            if wanted_owner is not None and key_owner != wanted_owner:
                continue
            window = state.window
            if window is None:
                continue
            try:
                # 桌宠窗口可能被收回后重建：跟随前先换绑到当前那只。
                pet_window = self._pet_window(state.slot)
                if pet_window is not None:
                    window.set_pet_window(pet_window)
                if window.background_value() != target_background:
                    window.set_background(target_background)
                if str(window.accent_color).lower() != target_accent.lower():
                    window.set_accent(target_accent)
                if window.is_always_on_top() != target_on_top:
                    window.set_always_on_top(target_on_top)
                if window.follows_pet() != target_follow:
                    window.set_follow_pet(target_follow)
                if window.font_size() != target_font_size:
                    window.set_font_size(target_font_size)
                if window.font_family() != target_font_family:
                    window.set_font_family(target_font_family)
            except RuntimeError:
                logger.debug("chat window is already gone", exc_info=True)

    def on_submit(
        self, owner: str, callback: Callable[[str, int], None]
    ) -> Callable[[], None]:
        owner = str(owner)
        self._submit_registry[owner] = callback
        return lambda: self._submit_registry.pop(owner, None)

    def on_stop(self, owner: str, callback: Callable[[int], None]) -> Callable[[], None]:
        owner = str(owner)
        self._stop_registry[owner] = callback
        return lambda: self._stop_registry.pop(owner, None)

    # ---------------------------------------------------------------- 内部
    def _slot(self, slot: int | None) -> int:
        if slot is not None:
            try:
                return max(0, int(slot))
            except (TypeError, ValueError):
                return 0
        return 0

    def _config(self):
        return getattr(self.host, "config", None)

    def _config_get(self, key: str, default=None):
        config = self._config()
        if config is None:
            return default
        try:
            value = config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _pet_window(self, slot: int):
        """该 slot 的桌宠窗口；不可用/已销毁时返回 None。"""
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            slot = 0
        pets = getattr(self.host, "pets", None)
        window = None
        if pets is not None:
            try:
                window = pets.window_for_slot(slot)
            except Exception:
                window = None
        if window is None and slot == 0:
            window = getattr(self.host, "window", None)
        if window is None:
            return None
        probe = getattr(window, "isVisible", None)
        if callable(probe):
            try:
                probe()
            except RuntimeError:
                return None
        return window

    def _character_for(self, slot: int) -> str:
        pets = getattr(self.host, "pets", None)
        if pets is not None:
            window = pets.window_for_slot(slot)
            config = getattr(window, "config", None)
            if config is not None:
                return str(config.get("character") or "default")
        config = self._config()
        if config is not None:
            return str(config.get("character") or "default")
        return "default"

    def _ensure(
        self, owner: str, pet_slot: int | None, *, with_window: bool = True
    ) -> _ChatState:
        owner = str(owner)
        slot = self._slot(pet_slot)
        key = (owner, slot)
        state = self._states.get(key)
        if state is None:
            store = SessionStore(self.root, slot=slot, character_id=self._character_for(slot))
            sessions = store.list_sessions()
            session = sessions[0] if sessions else store.new_session()
            state = _ChatState(
                owner=owner, slot=slot, store=store, session=session, window=None
            )
            self._states[key] = state
        if with_window and state.window is None:
            self._create_window(state)
        return state

    def _create_window(self, state: _ChatState) -> ChatWindow:
        key = (state.owner, state.slot)
        window = ChatWindow(
            title=self._title(state.owner, state.slot),
            font_size=self._font_size(),
            font_family=self._font_family(),
            dsh=getattr(self.host, "dsh", None),
            character_id=self._character_for(state.slot),
            accent=str(self._config_get("chat_accent", "") or ""),
            background=str(self._config_get("chat_background", "") or ""),
            always_on_top=bool(self._config_get("chat_always_on_top", False)),
            provider=str(self._config_get("ai_model", "") or ""),
            pet_window=self._pet_window(state.slot),
            follow_pet=bool(self._config_get("chat_follow_pet", False)),
        )
        window.submitted.connect(lambda text, s=key: self._on_submit(s, text))
        window.stop_requested.connect(lambda s=key: self._on_stop(s))
        window.cleared.connect(lambda s=key: self._on_clear(s))
        window.session_selected.connect(
            lambda session_id, s=key: self._on_session(s, session_id)
        )
        window.closed.connect(lambda s=key: self._on_closed(s))
        window.pin_toggled.connect(self._on_pin_toggled)
        window.record_requested.connect(lambda key=key: self._start_recording(key))
        window.record_stopped.connect(lambda key=key: self._stop_recording(key))
        if self._asr is None:
            window.composer.record_button.hide()
        try:
            sessions = state.store.list_sessions()
        except OSError:
            sessions = []
        window.set_history(sessions, state.session.session_id)
        window.load_messages(state.session.messages)
        state.window = window
        return window

    def _title(self, owner: str, slot: int) -> str:
        suffix = "主桌宠" if slot == 0 else f"桌宠 {slot}"
        return f"AI 对话 · {suffix}"

    def _font_size(self) -> int:
        try:
            override = int(self._config_get("chat_font_size", 0) or 0)
        except (TypeError, ValueError):
            override = 0
        if override > 0:
            return override
        try:
            return int(self._config_get("ui_font_size", 13))
        except (TypeError, ValueError):
            return 13

    def _font_family(self) -> str:
        return str(self._config_get("chat_font_family", "") or "").strip()

    def _save(self, state: _ChatState) -> None:
        try:
            state.store.save(state.session)
        except OSError:
            logger.warning("chat session save failed", exc_info=True)

    def _apply_record(self, owner: str, role: str, text: str, slot: int) -> None:
        self.append_to_session(owner, role, text, pet_slot=slot)

    def _notify_island(self, state: str) -> None:
        """把「AI 工作中 / 完成 / 出错」推给灵动岛状态点（没有岛就静默）。"""
        notify = getattr(self.host, "notify_agent_state", None)
        if not callable(notify):
            return
        try:
            notify(state, source="chat")
        except TypeError:
            try:
                notify(state)
            except Exception:  # noqa: BLE001
                logger.debug("island state notify failed", exc_info=True)
        except Exception:  # noqa: BLE001 - 状态点坏了不许影响对话
            logger.debug("island state notify failed", exc_info=True)

    def _apply_token(self, owner: str, delta: str, slot: int) -> None:
        state = self._ensure(owner, slot)
        window = state.window
        if window is None:
            return
        if not state.active:
            window.begin_stream()
            state.session.append("assistant", "")
            state.active = True
            self._notify_island("working")
        window.append_token(delta)

    def _begin_stream_if_needed(self, state: _ChatState) -> None:
        if state.active or state.window is None:
            return
        state.window.begin_stream()
        state.session.append("assistant", "")
        state.active = True
        self._notify_island("working")

    def _apply_reasoning(self, owner: str, delta: str, slot: int) -> None:
        state = self._ensure(owner, slot, with_window=False)
        if state.window is None or not delta:
            return
        self._begin_stream_if_needed(state)
        state.window.append_reasoning(delta)

    def _apply_status(self, owner: str, text: str, slot: int) -> None:
        state = self._ensure(owner, slot, with_window=False)
        if state.window is None or not text:
            return
        self._begin_stream_if_needed(state)
        state.window.add_activity(text)

    def _apply_done(self, owner: str, text: str, slot: int) -> None:
        state = self._ensure(owner, slot)
        window = state.window
        thinking = ("", [])
        if window is not None:
            window.finish_stream(text)
            thinking = window.last_thinking()
            self._notify_island("done")
        final = str(text or "")
        if not final:
            if window is not None:
                messages = window.messages()
                final = messages[-1]["content"] if messages else ""
            elif state.session.messages:
                final = str(state.session.messages[-1].get("content") or "")
        # 用窗口的最终内容覆盖会话里那条空的 assistant 记录。
        if state.session.messages and state.session.messages[-1].get("role") == "assistant":
            record = state.session.messages[-1]
            record["content"] = final[:24_000]
            self._remember_thinking(record, thinking)
        elif final:
            state.session.append("assistant", final)
            self._remember_thinking(state.session.messages[-1], thinking)
        state.active = False
        self._save(state)
        self._refresh_history(state)

    @staticmethod
    def _remember_thinking(record: dict, thinking: tuple[str, list[str]]) -> None:
        """思维链/检索进度随 assistant 记录落盘；没有就保持旧会话格式。"""
        reasoning, activity = thinking
        if reasoning:
            record["reasoning"] = str(reasoning)[:24_000]
        if activity:
            record["activity"] = [str(line)[:500] for line in activity][-40:]

    def _apply_error(self, owner: str, reason: str, message: str, slot: int) -> None:
        state = self._ensure(owner, slot)
        if state.window is not None:
            state.window.show_error(reason, message)
        self._notify_island("error")
        if state.session.messages and state.session.messages[-1].get("content") == "":
            state.session.messages.pop()
        state.active = False
        self._save(state)

    def _on_submit(self, key: tuple[str, int], text: str) -> None:
        state = self._states.get(key)
        if state is None or state.window is None:
            return
        attachments = state.window.take_attachments()
        callback = self._submit_registry.get(state.owner)
        if callback is None:
            state.window.show_error("no_handler", "没有扩展接管这个对话窗口")
            return
        if attachments:
            state.extras["attachments"] = attachments
        state.window.append_message("user", text)
        state.session.append("user", text)
        self._save(state)
        self._refresh_history(state)
        try:
            callback(text, state.slot)
        except Exception:
            logger.exception("chat submit callback failed")
            state.window.show_error("handler_failed", "扩展处理消息时出错")

    def _on_stop(self, key: tuple[str, int]) -> None:
        state = self._states.get(key)
        if state is None:
            return
        self.cancel(state.owner, pet_slot=state.slot)

    def _on_clear(self, key: tuple[str, int]) -> None:
        state = self._states.get(key)
        if state is None:
            return
        state.session = state.store.new_session()
        state.active = False
        state.extras.pop("attachments", None)
        self._refresh_history(state)

    def _on_session(self, key: tuple[str, int], session_id: str) -> None:
        state = self._states.get(key)
        if state is None:
            return
        if not session_id:
            state.session = state.store.new_session()
            if state.window is not None:
                state.window.load_messages([])
        else:
            loaded = state.store.load(session_id)
            if loaded is None:
                return
            state.session = loaded
            if state.window is not None:
                state.window.load_messages(loaded.messages)
        state.active = False
        self._refresh_history(state)

    # ---------------------------------------------------------------- 语音输入
    def _window_for_key(self, key: tuple[str, int]) -> ChatWindow | None:
        state = self._states.get(key)
        return state.window if state is not None else None

    def _start_recording(self, key: tuple[str, int]) -> None:
        asr = self._asr
        window = self._window_for_key(key)
        if asr is None or window is None:
            return
        if self._recording_key is not None:
            window.set_recording(False, "上一段语音还没结束")
            return
        self._recording_key = key
        if not asr.start():
            self._recording_key = None
            window.set_recording(False, "麦克风不可用或正在识别")
            return
        window.set_recording(True, "正在录音…再点一次停止")

    def _stop_recording(self, key: tuple[str, int]) -> None:
        if self._recording_key != key or self._asr is None:
            return
        self._asr.stop()

    def _on_asr_recording(self, recording: bool) -> None:
        key = self._recording_key
        if key is None:
            return
        window = self._window_for_key(key)
        if window is None:
            return
        window.set_recording(bool(recording), "" if recording else "正在识别…")

    def _on_asr_finished(self, text: str) -> None:
        key = self._recording_key
        self._recording_key = None
        window = self._window_for_key(key) if key is not None else None
        if window is None:
            return
        window.set_recording(False)
        auto_send = bool(self._config_get("asr_auto_send", False))
        window.insert_transcript(str(text or ""), auto_send=auto_send)

    def _on_asr_failed(self, reason: str) -> None:
        key = self._recording_key
        self._recording_key = None
        window = self._window_for_key(key) if key is not None else None
        if window is None:
            return
        window.set_recording(False, asr_reason_text(reason))

    def _on_closed(self, key: tuple[str, int]) -> None:
        state = self._states.get(key)
        if state is not None:
            self._save(state)

    def _on_pin_toggled(self, on: bool) -> None:
        config = self._config()
        if config is None:
            return
        try:
            config.set("chat_always_on_top", bool(on))
            config.save()
        except OSError:
            logger.warning("chat pin preference save failed", exc_info=True)

    def _refresh_history(self, state: _ChatState) -> None:
        if state.window is None:
            return
        try:
            sessions = state.store.list_sessions()
        except OSError:
            sessions = []
        state.window.set_history(sessions, state.session.session_id)
