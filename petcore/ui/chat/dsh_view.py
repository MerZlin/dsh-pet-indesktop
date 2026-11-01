"""同窗 DSH 视图：输入发到 DSH，实时显示思考/工具/回复与审批。

三条与布局有关的硬约束（2026-11-01 实测后重写）：

- **行宽只由 viewport 决定**：``QLabel(minimumSizeHint=0)`` + ``QSizePolicy.Ignored``。
  之前一条含 64 位哈希/长路径的 tool-call 会把 ``timeline.minimumSizeHint()`` 抬到
  1186px，而横向滚动条是关掉的，于是每行右侧被永久裁掉 450px（用户报的「文本显示
  不全」）。
- **工具过程默认折叠**：一行标题（``▸ pwsh · 完成``），点开才看参数与结果；失败
  自动展开。DSH 桌面端就是这个形态。
- **思考是一行**：``▸ 已思考 129 字``，点开看正文；reasoning 由桥接的
  ``assistant/message`` 帧带来（``channel.js`` 的 ``reasoningOf``）。
"""

from __future__ import annotations

import logging
import threading
from collections import deque

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...net.dsh import DshClient
from .dsh_controller import DshController, DshHub
from .markdown_view import markdown_to_html

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
#: 时间线左右边距（与 ``timeline_layout.setContentsMargins`` 同一对数）。
_TIMELINE_MARGIN = 30
#: 折叠标记。
_COLLAPSED = "▸"
_EXPANDED = "▾"
#: 历史重放每批条数（批间 `QTimer.singleShot(0)` 让出事件循环）。
_HISTORY_BATCH = 20


class _WrapLabel(QLabel):
    """折行标签：最小宽度恒为 0，长 token 不许把整条时间线撑宽。

    ``QLabel`` 的 ``minimumSizeHint()`` 返回「最长不可断词」的宽度；放进
    ``QScrollArea(widgetResizable=True)`` 后这个值会变成内容控件的最小宽度，
    于是内容比视口宽、而横向滚动条又被关掉 = 右边永久看不见。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _ToggleLabel(QLabel):
    """整行可点的标题（QLabel 而不是按钮：QSS 的 ``#dsh-tool-title`` 继续生效）。"""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _Collapsible(QFrame):
    """一行标题 + 可折叠正文（工具卡与思考行共用）。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base = str(title or "")
        self._expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.header = _ToggleLabel(f"{_COLLAPSED} {self._base}", self)
        self.header.setObjectName("dsh-tool-title")
        self.header.setWordWrap(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(3)
        self.body.setVisible(False)
        layout.addWidget(self.body)

    def title_text(self) -> str:
        return self._base

    def set_title(self, title: str) -> None:
        self._base = str(title or "")
        marker = _EXPANDED if self._expanded else _COLLAPSED
        self.header.setText(f"{marker} {self._base}")

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        marker = _EXPANDED if self._expanded else _COLLAPSED
        self.header.setText(f"{marker} {self._base}")
        self.body.setVisible(self._expanded)

    def add_body(self, text: str, *, mono: bool = True) -> _WrapLabel:
        label = _WrapLabel(str(text or ""), self.body)
        label.setObjectName("dsh-tool-detail")
        if mono:
            label.setStyleSheet(
                "font-family:'Consolas','Courier New',monospace; "
                "font-size:11px; color:#4a5260;"
            )
        # 展开就是"我要看全部"：不再限高（`_BODY_MAX_HEIGHT` 会把长输出裁掉，
        # 用户报的「文本显示不全」刚修完，别在折叠区里再造一个）。
        self.body_layout.addWidget(label)
        return label


class DshView(QWidget):
    context_ready = Signal(str)
    session_started = Signal(str)
    session_failed = Signal(str)
    session_finished = Signal()
    send_requested = Signal(str)

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
        #: 历史重放是**分批**的：审批到达时若时间线里已有几十条工具/对话，
        #: 一次性建 200 个控件会把 GUI 线程占住数秒（桌宠同线程 → 看起来卡死）。
        #: 每批 20 条、批间让出事件循环；重放期间的实时帧排队，结束后按序落地。
        self._history_queue: deque[tuple[str, str]] = deque()
        self._history_token = 0
        self._history_draining = False
        self._deferred_frames: list[dict] = []
        #: callId → 工具卡（``tool-call`` 建卡，``tool-result`` 收尾）。
        self._tool_cards: dict[str, _Collapsible] = {}
        #: requestId → (card, allow, deny)；审批卡是交互控件，结构与既有测试一致。
        self._approvals: dict[str, tuple[QWidget, QWidget, QWidget]] = {}
        self._hub = hub if hub is not None and hub.available else None
        self._controller: DshController | None = None
        self._context_provider = None
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(0)
        self._reflow_timer.timeout.connect(self._reflow)
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
        self.session_failed.connect(self._on_new_failed)
        self.session_finished.connect(self._on_new_finished)
        if client is None:
            self.set_status("未安装 DSH 通道；在设置 → 常规 → DSH 连接里安装", False)
        else:
            self._replay_approvals()

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
        self.status = _WrapLabel("DSH 未连接", header)
        self.status.setObjectName("dsh-status")
        header_layout.addWidget(self.status, 1)
        self.new_button = QPushButton("新会话", header)
        self.new_button.setObjectName("dsh-new")
        self.new_button.clicked.connect(self._new_session)
        header_layout.addWidget(self.new_button)
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
        self.timeline.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.timeline_layout = QVBoxLayout(self.timeline)
        self.timeline_layout.setContentsMargins(
            _TIMELINE_MARGIN, 16, _TIMELINE_MARGIN, 16
        )
        self.timeline_layout.setSpacing(10)
        self.timeline_layout.addStretch(1)
        self.scroll.setWidget(self.timeline)
        # 视口尺寸一变就重排：折行标签的高度只在这里重算，否则窗口拉宽后
        # 行高仍是旧值（文本被裁）。
        self.scroll.viewport().installEventFilter(self)
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
            self.new_button.setEnabled(False)
            self.context_button.setEnabled(False)

    # ---------------------------------------------------------------- 尺寸
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._reflow_timer.start()
        if (
            obj is self.input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self._send()
            return True
        return super().eventFilter(obj, event)

    def _row_width(self) -> int:
        viewport = self.scroll.viewport().width()
        return max(120, viewport - 2 * _TIMELINE_MARGIN)

    def _reflow(self) -> None:
        """视口变化后重算时间线高度（折行标签按新宽度重新排版）。"""
        try:
            self.timeline.setFixedWidth(max(1, self.scroll.viewport().width()))
            self.timeline_layout.activate()
            self.timeline.adjustSize()
            self.timeline.updateGeometry()
        except RuntimeError:
            return
        self._scroll_bottom()

    # ---------------------------------------------------------------- 生命周期
    def set_context_provider(self, provider) -> None:
        """设置 ``provider(text) -> dict|None``：返回记忆/人设上下文。"""
        self._context_provider = provider

    def load_local(self, messages) -> None:
        items = []
        for item in messages or ():
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            text = str(item.get("content") or item.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                items.append({"role": role, "text": text})
        self._on_history(items)

    def activate(self) -> None:
        if self._client is None or self._started:
            return
        self._started = True
        if self._hub is not None:
            self._hub.acquire()
            cached = self._hub.history_cache()
            if cached is not None:
                self._on_history(cached)
            else:
                local = self._hub.local_history()
                if local:
                    self._on_history(local)
            # 分批重放会自己补审批卡；重放中再补一遍会插到历史中间。
            if not self._history_draining:
                self._replay_approvals()
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
    def _new_session(self) -> None:
        client = self._client
        if client is None:
            return
        self.new_button.setEnabled(False)
        self.set_status("正在新建 DSH 会话…")

        def run() -> None:
            try:
                session_id = str(client.new_session() or "")
            except Exception:
                logger.debug("dsh new session failed", exc_info=True)
                session_id = ""
            self.session_finished.emit()
            if session_id:
                self.session_started.emit(session_id)
            else:
                self.session_failed.emit("新建 DSH 会话失败")

        threading.Thread(target=run, name="petcore-dsh-new", daemon=True).start()

    def _on_new_finished(self) -> None:
        self.new_button.setEnabled(True)

    def _on_new_failed(self, reason: str) -> None:
        self.set_status(str(reason or "新建 DSH 会话失败"))

    def _send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._add_text("user", text)
        self.set_status("已发送，等待 DSH…")
        # 统一经 ChatWindow/Controller 落会话 + 注入记忆；DshView 不直接调 hub，
        # 否则用户 prompt 只活在界面里，重开就丢。
        self.send_requested.emit(text)

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

    #: 重放期间必须延后的帧（历史快照之后到达的实时帧；按原序落地）。
    _DEFERRED_KINDS = frozenset(
        {
            "user",
            "assistant",
            "tool-call",
            "tool-result",
            "approval-request",
            "approval-resolved",
        }
    )

    def _on_history(self, items) -> None:
        self._history_token += 1
        token = self._history_token
        self._clear_rows()
        self._transcript.clear()
        self._history_queue.clear()
        self._deferred_frames.clear()
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "")
                text = str(item.get("text") or "").strip()
                if role in ("user", "assistant") and text:
                    self._history_queue.append((role, text))
        self._history_draining = bool(self._history_queue)
        if not self._history_draining:
            self._finish_history()
            return
        self._drain_history(token)

    def _drain_history(self, token: int) -> None:
        if token != self._history_token or not self._history_draining:
            return
        for _ in range(_HISTORY_BATCH):
            if not self._history_queue:
                break
            role, text = self._history_queue.popleft()
            self._add_text(role, text, scroll=False)
        if self._history_queue:
            QTimer.singleShot(0, self, lambda: self._drain_history(token))
            return
        self._finish_history()

    def _finish_history(self) -> None:
        # 历史回放会重建整条时间线，但**待审批是 Hub 级状态**：回放完必须把
        # 仍然挂着的卡补回来（桥不会在 /pet/history 里重放 approval 帧）。
        self._history_draining = False
        self._replay_approvals()
        deferred = self._deferred_frames
        self._deferred_frames = []
        for frame in deferred:
            self._on_frame(frame)
        self._scroll_bottom()

    def _on_frame(self, frame) -> None:
        if not isinstance(frame, dict):
            return
        kind = str(frame.get("kind") or "")
        if self._history_draining and kind in self._DEFERRED_KINDS:
            # 历史还没铺完就插实时帧会让时间线错序；排队到 `_finish_history`。
            self._deferred_frames.append(dict(frame))
            return
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
            if frame.get("reset") and self._hub is not None:
                self._hub.clear_history()
                self._hub.clear_pending()
                self._hub.restart()
            return
        if kind == "user":
            text = str(frame.get("text") or "").strip()
            if text and not (
                self._transcript and self._transcript[-1] == ("user", text)
            ):
                self._add_text("user", text)
        elif kind == "assistant":
            self._add_assistant(frame)
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
            self._add_error(str(frame.get("message") or "DSH 报错"))
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

    def _add_text(
        self, role: str, text: str, *, record: bool = True, scroll: bool = True
    ) -> None:
        value = str(text or "").strip()
        if not value:
            return
        label = _WrapLabel("", self.timeline)
        if role == "assistant":
            label.setObjectName("dsh-assistant")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setText(markdown_to_html(value))
        else:
            label.setObjectName("dsh-user")
            label.setText(value)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight if role == "user" else Qt.AlignmentFlag.AlignLeft
        )
        self._append(label)
        if record and role in ("user", "assistant"):
            self._transcript.append((role, value))
        if scroll:
            self._scroll_bottom()

    def _add_assistant(self, frame: dict) -> None:
        """一条 assistant 帧 → 「已思考 N 字」一行 + 正文。"""
        reasoning = str(frame.get("reasoning") or "").strip()
        if reasoning:
            self._add_thinking(reasoning)
        self._add_text("assistant", str(frame.get("text") or ""))

    def _add_thinking(self, text: str) -> _Collapsible:
        row = _Collapsible(f"已思考 {len(text)} 字", self.timeline)
        row.setObjectName("dsh-thinking")
        row.add_body(text, mono=False)
        self._append(row)
        return row

    def _add_error(self, text: str) -> None:
        label = _WrapLabel("", self.timeline)
        label.setObjectName("dsh-error")
        label.setText(str(text or "DSH 报错"))
        self._append(label)
        self._scroll_bottom()

    def _add_tool_call(self, frame: dict) -> None:
        name = str(frame.get("tool") or "tool")
        card = _Collapsible(f"{name} · 运行中…", self.timeline)
        card.setObjectName("dsh-tool")
        card.add_body(str(frame.get("args") or ""))
        self._append(card)
        call_id = str(frame.get("callId") or "")
        if call_id:
            self._tool_cards[call_id] = card
        self._scroll_bottom()

    def _finish_tool(self, frame: dict) -> None:
        call_id = str(frame.get("callId") or "")
        card = self._tool_cards.get(call_id)
        text = str(frame.get("text") or "").strip()
        ok = bool(frame.get("ok", True))
        name = card.title_text().split(" · ", 1)[0] if card is not None else "工具"
        if card is not None:
            try:
                card.set_title(f"{name} · {'完成' if ok else '失败'}")
                if text:
                    card.add_body(text)
                if not ok:
                    # 失败必须可见：错误信息藏在折叠区里等于没有。
                    card.set_expanded(True)
                    card.header.setProperty("state", "error")
                    style = card.header.style()
                    style.unpolish(card.header)
                    style.polish(card.header)
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
        title = _WrapLabel(
            f"需要你确认：{frame.get('title') or frame.get('tool') or '工具调用'}",
            card,
        )
        title.setObjectName("dsh-tool-title")
        layout.addWidget(title)
        detail = _WrapLabel(str(frame.get("args") or ""), card)
        detail.setObjectName("dsh-tool-detail")
        layout.addWidget(detail)
        buttons = QHBoxLayout()
        allow = QPushButton("允许", card)
        allow.setObjectName("dsh-allow")
        allow.clicked.connect(
            lambda _checked=False, rid=request_id: self._answer(rid, True)
        )
        deny = QPushButton("拒绝", card)
        deny.setObjectName("dsh-deny")
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

    def _replay_approvals(self) -> None:
        """把 Hub 里仍挂着的审批卡补进时间线（视图晚于事件创建时也不丢）。"""
        if self._hub is None:
            return
        try:
            pending = self._hub.pending_approvals()
        except Exception:  # noqa: BLE001 - 视图不该因为 Hub 状态异常而空白
            return
        for frame in pending:
            self._add_approval(frame)

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
