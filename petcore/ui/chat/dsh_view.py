"""同窗 DSH 视图：输入发到 DSH，实时显示思考/工具/回复与审批。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...net.dsh import DshClient
from .dsh_controller import DshController, DshHub

logger = logging.getLogger("petcore.ui.chat.dsh.view")

_REASON_TEXT = {
    "missing": "未安装 DSH 通道",
    "not_running": "DSH 没有运行，或通道插件未加载",
    "auth": "通道 token 不匹配",
    "network": "连不上 DSH 通道",
    "timeout": "DSH 通道超时",
    "protocol": "DSH 通道返回格式异常",
    "bad_request": "内容为空",
    "closed": "DSH 连接已断开",
}


class DshView(QWidget):
    context_ready = Signal(str)

    def __init__(
        self,
        client: DshClient | None,
        parent: QWidget | None = None,
        *,
        hub: DshHub | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dsh-view")
        self._client = client
        self._started = False
        self._transcript: list[tuple[str, str]] = []
        self._tool_cards: dict[str, QLabel] = {}
        self._approvals: dict[str, tuple[QWidget, QWidget, QWidget]] = {}
        self._hub = hub if hub is not None and hub.available else None
        self._controller: DshController | None = None
        if self._hub is not None:
            self._hub.connected.connect(self._on_connected)
            self._hub.history_loaded.connect(self._on_history)
            self._hub.frame.connect(self._on_frame)
            self._hub.failed.connect(self._on_failed)
        else:
            self._controller = DshController(client, self)
            self._controller.connected.connect(self._on_connected)
            self._controller.history_loaded.connect(self._on_history)
            self._controller.frame.connect(self._on_frame)
            self._controller.failed.connect(self._on_failed)
        self._build()
        if client is None:
            self.set_status("未安装 DSH 通道；在设置 → 常规 → DSH 连接里安装", False)

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("dsh-header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 8, 16, 8)
        header_layout.setSpacing(8)
        self.status = QLabel("DSH 未连接", header)
        self.status.setObjectName("dsh-status")
        self.status.setWordWrap(True)
        header_layout.addWidget(self.status, 1)
        self.context_button = QPushButton("带入 AI 对话", header)
        self.context_button.setObjectName("dsh-context")
        self.context_button.clicked.connect(self._emit_context)
        header_layout.addWidget(self.context_button)
        self.retry_button = QPushButton("重试连接", header)
        self.retry_button.setObjectName("dsh-retry")
        self.retry_button.clicked.connect(self._retry)
        header_layout.addWidget(self.retry_button)
        root.addWidget(header)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("dsh-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline = QWidget(self.scroll)
        self.timeline.setObjectName("dsh-timeline")
        self.timeline_layout = QVBoxLayout(self.timeline)
        self.timeline_layout.setContentsMargins(30, 16, 30, 16)
        self.timeline_layout.setSpacing(10)
        self.timeline_layout.addStretch(1)
        self.scroll.setWidget(self.timeline)
        root.addWidget(self.scroll, 1)

        composer = QFrame(self)
        composer.setObjectName("dsh-composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(20, 8, 20, 14)
        composer_layout.setSpacing(8)
        self.input = QPlainTextEdit(composer)
        self.input.setObjectName("dsh-input")
        self.input.setPlaceholderText("发给 DSH… Enter 发送，Shift+Enter 换行")
        self.input.setMinimumHeight(52)
        self.input.setMaximumHeight(90)
        self.input.installEventFilter(self)
        composer_layout.addWidget(self.input, 1)
        self.send_button = QPushButton("发送", composer)
        self.send_button.setObjectName("dsh-send")
        self.send_button.clicked.connect(self._send)
        composer_layout.addWidget(self.send_button)
        self.stop_button = QPushButton("停止", composer)
        self.stop_button.setObjectName("dsh-stop")
        self.stop_button.clicked.connect(self._stop)
        composer_layout.addWidget(self.stop_button)
        root.addWidget(composer)

        if self._client is None:
            self.input.setEnabled(False)
            self.send_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.context_button.setEnabled(False)

    # ---------------------------------------------------------------- 生命周期
    def activate(self) -> None:
        if self._client is None or self._started:
            return
        self._started = True
        if self._hub is not None:
            self._hub.acquire()
            cached = self._hub.history_cache()
            if cached is not None:
                self._on_history(cached)
            return
        if self._controller is not None:
            self._controller.start()

    def deactivate(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._hub is not None:
            self._hub.release()
            return
        if self._controller is not None:
            self._controller.stop()

    def _retry(self) -> None:
        if self._client is None:
            return
        self.set_status("正在连接 DSH…")
        if self._hub is not None:
            self._started = True
            self._hub.restart()
            return
        if self._controller is None:
            return
        self._controller.stop()
        self._started = False
        self.activate()

    # ---------------------------------------------------------------- 输入
    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._add_text("user", text)
        self.set_status("已发送，等待 DSH…")
        target = self._hub or self._controller
        if target is not None:
            target.send(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if (
            obj is self.input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self._send()
            return True
        return super().eventFilter(obj, event)

    def _stop(self) -> None:
        self.set_status("正在取消…")
        target = self._hub or self._controller
        if target is not None:
            target.cancel()

    def _emit_context(self) -> None:
        if not self._transcript:
            self.set_status("还没有可带入的 DSH 记录")
            return
        lines = ["以下是 DSH 最近对话，可作为本轮 AI 对话的背景："]
        for role, text in self._transcript[-10:]:
            label = "用户" if role == "user" else "DSH"
            lines.append(f"{label}：{text}")
        self.context_ready.emit("\n".join(lines))

    # ---------------------------------------------------------------- 帧
    def _on_connected(self, connected: bool, detail: str) -> None:
        if connected:
            suffix = f" · v{detail}" if detail else ""
            self.set_status(f"已连接 DSH{suffix}", True)
            return
        self.set_status(_REASON_TEXT.get(detail, f"DSH 连接失败（{detail}）"), False)
        # 断线后按钮不能停在“已提交”：重连后用户仍可再点一次。
        self._reopen_approvals()

    def _on_failed(self, reason: str) -> None:
        self._reopen_approvals()
        self.set_status(_REASON_TEXT.get(reason, f"DSH 操作失败（{reason}）"), False)

    def _on_history(self, items) -> None:
        self._clear_rows()
        self._transcript.clear()
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            text = str(item.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                self._add_text(role, text)

    def _on_frame(self, frame) -> None:
        if not isinstance(frame, dict):
            return
        kind = str(frame.get("kind") or "")
        if kind == "connection":
            if frame.get("ok"):
                self.set_status("已连接 DSH", True)
            else:
                reason = str(frame.get("reason") or "network")
                self.set_status(
                    _REASON_TEXT.get(reason, f"DSH 连接断开（{reason}）"), False
                )
            return
        if kind == "hello":
            return
        if kind == "user":
            text = str(frame.get("text") or "").strip()
            if text and not (
                self._transcript and self._transcript[-1] == ("user", text)
            ):
                self._add_text("user", text)
        elif kind == "assistant":
            self._add_text("assistant", str(frame.get("text") or ""))
        elif kind == "tool-call":
            self._add_tool_call(frame)
        elif kind == "tool-result":
            self._finish_tool(frame)
        elif kind == "approval-request":
            self._add_approval(frame)
        elif kind == "approval-resolved":
            self._resolve_approval(frame)
        elif kind == "turn-start":
            self.set_status("DSH 思考中…")
        elif kind == "step":
            self.set_status("DSH 正在执行…")
        elif kind == "busy":
            self.set_status("DSH 仍在运行…")
        elif kind == "turn-end":
            reason = str(frame.get("reason") or "")
            self.set_status("就绪" if reason in ("", "normal", "done") else f"本轮结束：{reason}")
        elif kind == "error":
            self._add_text("error", str(frame.get("message") or "DSH 报错"))
        elif kind == "idle":
            self.set_status("就绪")

    # ---------------------------------------------------------------- 行
    def set_status(self, text: str, ok: bool = True) -> None:
        self.status.setText(str(text or ""))
        self.status.setProperty("state", "ok" if ok else "error")
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def _clear_rows(self) -> None:
        while self.timeline_layout.count() > 1:
            item = self.timeline_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._tool_cards.clear()
        # 历史回放/重连会重建整条时间线；旧审批卡被 deleteLater 后，如果
        # id 还留在 _approvals，同 id 的 approval-request 会被当成重复丢弃，
        # 界面上就再也没有可点的卡片。
        self._approvals.clear()

    def _append(self, widget: QWidget) -> None:
        self.timeline_layout.insertWidget(self.timeline_layout.count() - 1, widget)

    def _add_text(self, role: str, text: str, *, record: bool = True) -> None:
        value = str(text or "").strip()
        if not value:
            return
        label = QLabel(value, self.timeline)
        label.setObjectName("dsh-user" if role == "user" else "dsh-assistant")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight if role == "user" else Qt.AlignmentFlag.AlignLeft
        )
        self._append(label)
        if record and role in ("user", "assistant"):
            self._transcript.append((role, value))
        self._scroll_bottom()

    def _add_tool_call(self, frame: dict) -> None:
        card = QFrame(self.timeline)
        card.setObjectName("dsh-tool")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(3)
        name = str(frame.get("tool") or "tool")
        header = QLabel(f"工具 · {name}", card)
        header.setObjectName("dsh-tool-title")
        layout.addWidget(header)
        detail = QLabel(str(frame.get("args") or ""), card)
        detail.setObjectName("dsh-tool-detail")
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(detail)
        self._append(card)
        call_id = str(frame.get("callId") or "")
        if call_id:
            self._tool_cards[call_id] = detail
        self._scroll_bottom()

    def _finish_tool(self, frame: dict) -> None:
        call_id = str(frame.get("callId") or "")
        label = self._tool_cards.get(call_id)
        text = str(frame.get("text") or "").strip()
        ok = bool(frame.get("ok", True))
        if label is not None:
            prefix = "完成" if ok else f"失败（{frame.get('error') or 'error'}）"
            try:
                label.setText(f"{prefix}\n{text}" if text else prefix)
            except RuntimeError:
                pass
        else:
            self._add_text("assistant", f"工具{'完成' if ok else '失败'}：{text}")
        self._scroll_bottom()

    def _add_approval(self, frame: dict) -> None:
        request_id = str(frame.get("id") or "")
        if request_id and request_id in self._approvals:
            return
        card = QFrame(self.timeline)
        card.setObjectName("dsh-approval")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        title = QLabel(
            f"需要你确认：{frame.get('title') or frame.get('tool') or '工具调用'}",
            card,
        )
        title.setObjectName("dsh-tool-title")
        title.setWordWrap(True)
        layout.addWidget(title)
        detail = QLabel(str(frame.get("args") or ""), card)
        detail.setObjectName("dsh-tool-detail")
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(detail)
        buttons = QHBoxLayout()
        allow = QPushButton("允许", card)
        allow.clicked.connect(
            lambda _checked=False, rid=request_id: self._answer(rid, True)
        )
        deny = QPushButton("拒绝", card)
        deny.clicked.connect(
            lambda _checked=False, rid=request_id: self._answer(rid, False)
        )
        buttons.addWidget(allow)
        buttons.addWidget(deny)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._append(card)
        if not request_id:
            allow.setEnabled(False)
            deny.setEnabled(False)
            self.set_status("审批请求缺少 ID，无法提交", False)
            return
        self._approvals[request_id] = (card, allow, deny)
        # 审批风暴保护：只保留最近 20 张卡，旧卡不再占用交互。
        while len(self._approvals) > 20:
            oldest = next(iter(self._approvals))
            old_card, _allow, _deny = self._approvals.pop(oldest)
            old_card.deleteLater()
        self.set_status("等待审批…")

    def _answer(self, request_id: str, allow: bool) -> None:
        if not request_id:
            self.set_status("审批请求缺少 ID，无法提交", False)
            return
        entry = self._approvals.get(request_id)
        if entry is not None:
            _card, allow_button, deny_button = entry
            try:
                allow_button.setEnabled(False)
                deny_button.setEnabled(False)
            except RuntimeError:
                # 卡片已随历史回放被 deleteLater；审批答案仍要发出去。
                pass
        self.set_status("已提交审批结果")
        target = self._hub or self._controller
        if target is None:
            self.set_status("DSH 通道不可用", False)
            return
        target.answer(request_id, allow)
        QTimer.singleShot(
            8000, self, lambda rid=request_id: self._approval_timeout(rid)
        )

    def _approval_timeout(self, request_id: str) -> None:
        entry = self._approvals.get(request_id)
        if entry is None:
            return
        card, allow_button, deny_button = entry
        if card is None:
            return
        try:
            allow_button.setEnabled(True)
            deny_button.setEnabled(True)
            self.set_status("审批提交超时，请重试或检查连接", False)
        except RuntimeError:
            return

    def _reopen_approvals(self) -> None:
        """提交失败/断线时恢复按钮，避免卡片永久停在“已提交”。"""
        for card, allow_button, deny_button in list(self._approvals.values()):
            try:
                allow_button.setEnabled(True)
                deny_button.setEnabled(True)
            except RuntimeError:
                continue

    def _resolve_approval(self, frame: dict) -> None:
        request_id = str(frame.get("id") or "")
        entry = self._approvals.pop(request_id, None)
        if entry is None:
            self.set_status("审批已处理")
            return
        card, allow_button, deny_button = entry
        try:
            allow_button.setEnabled(False)
            deny_button.setEnabled(False)
            if frame.get("cancelled"):
                text = "审批已取消"
            else:
                text = "审批已允许" if frame.get("allow") else "审批已拒绝"
            self.set_status(text)
            # 保留一小会儿让用户看到终态，再移除卡片。
            QTimer.singleShot(1500, card, card.deleteLater)
        except RuntimeError:
            return

    def _scroll_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
