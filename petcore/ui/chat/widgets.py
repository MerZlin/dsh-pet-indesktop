"""现代聊天窗的复用控件：气泡、输入区、标题栏、会话行、侧栏遮罩。

移植自主分支 ``pet/chat/widgets.py``，只保留 core 聊天窗真正用到的部分：
去掉了服务/桌宠跟随/重试/批量管理等主分支专属接线，渲染与交互细节保持一致。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from .icons import tool_button, tool_icon
from .markdown_view import markdown_to_html

#: 附件上限（与发送逻辑共用，超限时明确提示而非静默跳过）
MAX_ATTACHMENTS = 10
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 单张图片 10MB 上限
MAX_IMAGE_TOTAL_BYTES = 20 * 1024 * 1024  # 图片总大小 20MB 上限
MAX_TEXT_TOTAL_CHARS = 200_000  # 文本总长 20 万字符上限
_TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".py", ".log", ".yaml", ".yml", ".xml"}

_logger = logging.getLogger("petcore.ui.chat")


def _attachment_is_image(path: Path) -> bool:
    mime = mimetypes.guess_type(path.name)[0] or ""
    return mime.startswith("image/")


def _attachment_is_text(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _attachment_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _attachment_text_chars(path: Path) -> int:
    """文本附件的字符数；只读前 MAX_TEXT_TOTAL_CHARS+1 字符，超大文件不整读。"""
    if not _attachment_is_text(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return len(handle.read(MAX_TEXT_TOTAL_CHARS + 1))
    except OSError:
        return 0


def validate_attachment_additions(current, candidates) -> tuple[list[Path], list[str]]:
    """校验新增附件，返回（应接受的新增路径, 超限警告文案）。

    规则：
    - 附件总量 ≤ MAX_ATTACHMENTS；
    - 必须为图片或文本（_attachment_is_image / _attachment_is_text 之一），否则拒绝并提示暂不支持；
    - 图片单张 ≤ MAX_IMAGE_BYTES，图片总大小 ≤ MAX_IMAGE_TOTAL_BYTES；
    - 文本总长度 ≤ MAX_TEXT_TOTAL_CHARS；
    - 重复路径 / 非文件直接忽略（不视为超限）。
    """
    accepted: list[Path] = []
    warnings: list[str] = []
    seen = set(current)
    total_count = len(current)
    image_bytes = sum(_attachment_file_size(p) for p in current if _attachment_is_image(p))
    text_chars = sum(_attachment_text_chars(p) for p in current if _attachment_is_text(p))

    for value in candidates:
        path = Path(value).expanduser().resolve()
        if path in seen:
            continue
        if not path.is_file():
            continue
        if not (_attachment_is_image(path) or _attachment_is_text(path)):
            ext = path.suffix.lower() or path.name
            warnings.append(f"暂不支持 {ext} 的文件格式（支持：文本和图片）")
            continue
        if total_count >= MAX_ATTACHMENTS:
            warnings.append(f"附件最多 {MAX_ATTACHMENTS} 个，{path.name} 未添加")
            continue
        if _attachment_is_image(path):
            size = _attachment_file_size(path)
            if size > MAX_IMAGE_BYTES:
                warnings.append(f"图片 {path.name} 超过 10MB，未添加")
                continue
            if image_bytes + size > MAX_IMAGE_TOTAL_BYTES:
                warnings.append(f"图片总大小超过 20MB，{path.name} 未添加")
                continue
            image_bytes += size
        elif _attachment_is_text(path):
            chars = _attachment_text_chars(path)
            if text_chars + chars > MAX_TEXT_TOTAL_CHARS:
                warnings.append(f"文本总长超过 20 万字符，{path.name} 未添加")
                continue
            text_chars += chars
        accepted.append(path)
        seen.add(path)
        total_count += 1
    return accepted, warnings


class SessionListRow(QFrame):
    """会话列表的一行：标题 + 三点菜单（点击行由 QListWidget 统一处理）。"""

    archive_requested = Signal(str, bool)
    delete_requested = Signal(str)

    def __init__(
        self, session_id: str, title: str, parent=None, *, archived: bool = False
    ):
        super().__init__(parent)
        self.session_id = str(session_id)
        self.archived = bool(archived)
        self.setObjectName("session-row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(5)
        self.title = QLabel(str(title or "新会话"), self)
        self.title.setObjectName("session-row-title")
        if self.archived:
            self.title.setProperty("archived", True)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.title, 1)
        self.more = tool_button(self, "session-more-button", "more", "更多操作")
        self.more.setFixedSize(24, 24)
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.clicked.connect(self._show_menu)
        self.more.hide()
        layout.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.more.show()
        if event is not None:
            super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.more.hide()
        if event is not None:
            super().leaveEvent(event)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        label = "取消归档" if self.archived else "归档会话"
        archive = menu.addAction(label)
        archive.triggered.connect(
            lambda _checked=False: self.archive_requested.emit(
                self.session_id, not self.archived
            )
        )
        menu.addSeparator()
        delete = menu.addAction("删除会话…")
        delete.triggered.connect(
            lambda _checked=False: self.delete_requested.emit(self.session_id)
        )
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))


class ChatTitleBar(QFrame):
    """独立聊天窗的自绘标题栏（无边框窗的拖拽手柄）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat-title-bar")
        self._drag_offset: QPoint | None = None
        self._dragging = False

    @staticmethod
    def _global_position(event: QMouseEvent) -> QPoint:
        position = getattr(event, "globalPosition", None)
        if position is not None:
            return position().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                self._global_position(event) - self.window().frameGeometry().topLeft()
            )
            self._dragging = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._dragging and self._drag_offset is not None:
            self.window().move(self._global_position(event) - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MessageBubble(QFrame):
    """一条消息：assistant 走 Markdown，user 保持纯文本；复制按钮给原始文本。"""

    STATE_STREAMING = "streaming"
    STATE_ERROR = "error"
    STATE_STOPPED = "stopped"
    STATE_NORMAL = "normal"

    def __init__(self, role: str, content: str = "", character_id: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.character_id = character_id
        self.state = self.STATE_NORMAL
        self.setObjectName("message-bubble")
        self.setProperty("role", role)
        self.setProperty("state", self.state)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if role == "assistant" else QSizePolicy.Policy.Maximum,
            # 纵向必须可收缩（Preferred）：Minimum 会把折行文本的 sizeHint 当最小高度，
            # QScrollArea 取整条时间线最小高度撑大内容，最新消息下方留出大片空白。
            QSizePolicy.Policy.Preferred,
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.avatar = QLabel("你" if role == "user" else _initial(character_id))
        self.avatar.setObjectName("bubble-avatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(34, 34)
        self.avatar.setProperty("role", role)
        self.avatar.hide()

        panel = QVBoxLayout()
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(5)
        self.meta = QLabel("你" if role == "user" else "桌宠")
        self.meta.setObjectName("bubble-meta")
        self.meta.hide()

        self.surface = QFrame(self)
        self.surface.setObjectName("message-surface")
        self.surface.setProperty("role", role)
        self.surface.setProperty("state", self.state)
        surface_layout = QVBoxLayout(self.surface)
        self.surface_layout = surface_layout
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)

        # 思考过程 / 联网检索过程：默认隐藏，收到对应事件才出现；可点击折叠。
        self._reasoning_text = ""
        self._activity_lines: list[str] = []
        self._thinking_done = False
        self.thinking_toggle = QToolButton(self.surface)
        self.thinking_toggle.setObjectName("thinking-toggle")
        self.thinking_toggle.setCheckable(True)
        self.thinking_toggle.setChecked(True)
        self.thinking_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thinking_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.thinking_toggle.setStyleSheet(
            "QToolButton#thinking-toggle { border: none; background: transparent; padding: 2px 0;"
            " color: rgba(128,128,128,0.95); font-size: 12px; text-align: left; }"
            "QToolButton#thinking-toggle:hover { color: rgba(90,140,220,1); }"
        )
        self.thinking_toggle.toggled.connect(self._on_thinking_toggled)
        self.thinking_toggle.hide()
        surface_layout.addWidget(self.thinking_toggle)
        self.thinking_body = QLabel(self.surface)
        self.thinking_body.setObjectName("thinking-body")
        self.thinking_body.setWordWrap(True)
        self.thinking_body.setTextFormat(Qt.TextFormat.PlainText)
        self.thinking_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.thinking_body.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.thinking_body.setStyleSheet(
            "QLabel#thinking-body { color: rgba(128,128,128,0.95); font-size: 12px;"
            " border-left: 2px solid rgba(128,128,128,0.35); padding: 2px 0 2px 10px;"
            " margin: 2px 0 8px 0; }"
        )
        self.thinking_body.hide()
        surface_layout.addWidget(self.thinking_body)
        # 思维链增量很密：合并到 ~80ms 刷一次，避免长文本每个 token 都重排
        self._thinking_refresh = QTimer(self)
        self._thinking_refresh.setSingleShot(True)
        self._thinking_refresh.setInterval(80)
        self._thinking_refresh.timeout.connect(self._refresh_thinking)

        self.body = QLabel(self.surface)
        self.body.setObjectName("bubble-body")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # AI 回复按 Markdown 渲染（链接可点、可选中复制）；用户消息保持纯文本
        if role == "assistant":
            self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            self.body.setOpenExternalLinks(True)
        self._raw_text = ""
        self._markdown = False
        self._render_cost_ms = 0.0
        # 流式输出时 Markdown 重排的节流：长回答单次渲染 + 排版可达 20ms 以上，
        # 渲染变慢时合并到定时器里做。
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_body)
        self.set_content(content)
        self.body.setMinimumWidth(360 if role == "assistant" else 120)
        surface_layout.addWidget(self.body)
        panel.addWidget(self.surface)

        self.tools = QFrame(self)
        self.tools.setObjectName("message-tools")
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        self.copy_button = tool_button(self.tools, "message-copy-button", "copy", "复制")
        self.copy_button.clicked.connect(self._copy_content)
        tools.addWidget(self.copy_button)
        if role == "user":
            tools.insertStretch(0, 1)
        else:
            tools.addStretch(1)
        panel.addWidget(self.tools)

        self.error_actions = QFrame(self.surface)
        self.error_actions.setObjectName("error-actions")
        error_actions_layout = QHBoxLayout(self.error_actions)
        error_actions_layout.setContentsMargins(0, 6, 0, 0)
        error_actions_layout.setSpacing(8)
        self.status_label = QLabel(self.error_actions)
        self.status_label.setObjectName("bubble-status")
        error_actions_layout.addWidget(self.status_label)
        error_actions_layout.addStretch(1)
        self.error_actions.hide()
        surface_layout.addWidget(self.error_actions)
        root.addLayout(panel, 1)

        if role == "user":
            root.setDirection(QHBoxLayout.Direction.RightToLeft)

    @property
    def content(self) -> str:
        """原始文本（Markdown 源码）；复制按钮与外部读取都用它，而不是渲染后的 HTML。"""
        return self._raw_text

    def _copy_content(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._raw_text)

    def set_content(self, text: str, *, markdown: bool | None = None) -> None:
        self._raw_text = str(text)
        self._markdown = (self.role == "assistant") if markdown is None else bool(markdown)
        if (
            self._markdown
            and self.state == self.STATE_STREAMING
            and self._render_cost_ms > 8.0
            and not self._render_timer.isActive()
        ):
            self._render_timer.start(int(min(250.0, max(50.0, self._render_cost_ms * 4))))
            return
        self._render_body()

    def _render_body(self) -> None:
        self._render_timer.stop()
        if self._markdown and self._raw_text:
            started = time.perf_counter()
            self.body.setTextFormat(Qt.TextFormat.RichText)
            self.body.setText(markdown_to_html(self._raw_text))
            self.body.heightForWidth(max(1, self.body.width()))
            self._render_cost_ms = (time.perf_counter() - started) * 1000.0
        else:
            self.body.setTextFormat(Qt.TextFormat.PlainText)
            self.body.setText(self._raw_text)

    # ---- 思考 / 联网过程 ----
    @property
    def has_thinking(self) -> bool:
        return bool(self._reasoning_text or self._activity_lines)

    @property
    def thinking_record(self) -> tuple[str, list[str]]:
        """(思考过程, 联网进度行)，随回复一起落盘，重新打开会话时回显。"""
        return self._reasoning_text, list(self._activity_lines)

    def append_reasoning(self, text: str) -> None:
        self._reasoning_text += str(text)
        self._show_thinking_panel()

    def add_activity(self, text: str) -> None:
        self._activity_lines.append(str(text))
        self._show_thinking_panel()

    def restore_thinking(self, reasoning: str, activity: list[str] | None = None) -> None:
        """从会话记录恢复思考 / 联网面板（收起状态）。"""
        self._reasoning_text = str(reasoning or "")
        self._activity_lines = [str(item) for item in (activity or [])]
        if not self.has_thinking:
            return
        self._thinking_done = True
        self.thinking_toggle.setChecked(False)
        self.thinking_toggle.show()
        self._refresh_thinking()

    def finish_thinking(self) -> None:
        """正文开始输出或本轮结束：面板收起为一行摘要，点击可展开。"""
        if self._thinking_done or not self.has_thinking:
            return
        self._thinking_done = True
        self.thinking_toggle.setChecked(False)
        self._refresh_thinking()

    def _show_thinking_panel(self) -> None:
        if self.thinking_toggle.isHidden():
            self.thinking_toggle.show()
            self.thinking_body.setVisible(self.thinking_toggle.isChecked())
        if not self._thinking_refresh.isActive():
            self._thinking_refresh.start()

    def _thinking_summary(self) -> str:
        searched = any(line.startswith(("🔍", "📄")) for line in self._activity_lines)
        if not self._thinking_done:
            label = "💭 深度思考中…" if self._reasoning_text else "🌐 联网检索中…"
        elif self._reasoning_text and searched:
            label = "💭 已联网搜索并深度思考"
        elif self._reasoning_text:
            label = "💭 已深度思考"
        elif searched:
            label = "🔍 已联网搜索"
        else:
            label = "🌐 联网检索过程"
        arrow = "▾" if self.thinking_toggle.isChecked() else "▸"
        if self._reasoning_text:
            return f"{arrow} {label}（{len(self._reasoning_text)} 字）"
        return f"{arrow} {label}"

    def _refresh_thinking(self) -> None:
        parts = []
        if self._activity_lines:
            parts.append("\n".join(self._activity_lines))
        if self._reasoning_text:
            parts.append(self._reasoning_text.strip())
        self.thinking_body.setText("\n\n".join(parts))
        self.thinking_toggle.setText(self._thinking_summary())

    def _on_thinking_toggled(self, checked: bool) -> None:
        if not self.has_thinking:
            return
        self.thinking_body.setVisible(checked)
        self._refresh_thinking()

    def set_state(self, state: str) -> None:
        # 流式每一拍都会调本方法：状态未变时全部视觉副作用都是冗余
        # （unpolish/polish 是重活），直接短路。
        if state == self.state:
            return
        self.state = state
        if self._render_timer.isActive():  # 离开流式状态：立刻渲染最终内容，不等节流
            self._render_body()
        self.setProperty("state", state)
        self.surface.setProperty("state", state)
        if state == self.STATE_ERROR:
            self.surface_layout.setContentsMargins(12, 10, 12, 10)
        else:
            self.surface_layout.setContentsMargins(0, 0, 0, 0)
        # Error/streaming rows own their status controls inside the surface;
        # the ordinary copy toolbar would otherwise float below the card.
        self.tools.setVisible(state not in {self.STATE_ERROR, self.STATE_STREAMING})
        if state == self.STATE_STREAMING:
            self.status_label.setText("正在生成…")
            self.error_actions.show()
        elif state == self.STATE_ERROR:
            self.status_label.setText("本次回复未保存")
            self.error_actions.show()
        elif state == self.STATE_STOPPED:
            self.status_label.setText("已停止生成")
            self.error_actions.show()
        else:
            self.error_actions.hide()
        self.surface.style().unpolish(self.surface)
        self.surface.style().polish(self.surface)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


#: 粘贴/拖拽进来的内存图落盘处（系统临时目录下；附件链只认路径，图必须先有路径）。
_CLIPBOARD_IMAGE_DIR = "dsh-pet-chat-clipboard"
#: 剪贴板缓存的懒清扫阈值：超过一天没人引用的 paste-*.png 在下次落盘时清掉。
_CLIPBOARD_IMAGE_MAX_AGE_S = 24 * 3600


def _clipboard_image_dir() -> Path:
    root = Path(tempfile.gettempdir()) / _CLIPBOARD_IMAGE_DIR
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _CLIPBOARD_IMAGE_MAX_AGE_S
    for stale in root.glob("paste-*.png"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass  # 清扫是顺手的事，占用/权限问题不值得中断粘贴
    return root


def _mime_to_image(mime) -> QImage:
    """mimeData.imageData() 的两种形态（QImage / QPixmap）归一成 QImage。"""
    data = mime.imageData()
    if isinstance(data, QImage):
        return data
    if isinstance(data, QPixmap):
        return data.toImage()
    return QImage()


def _save_clipboard_image(image: QImage) -> Path | None:
    """内存图 → 临时 PNG（附件链只认路径）。失败返回 None（调用方按无图处理）。"""
    if image is None or image.isNull():
        return None
    try:
        path = _clipboard_image_dir() / f"paste-{time.time_ns()}.png"
        if image.save(str(path), "PNG"):
            return path
    except OSError:
        _logger.exception("剪贴板图片落盘失败")
    return None


def _is_clipboard_cache_file(path: Path) -> bool:
    """只认我们自己在剪贴板缓存目录里落盘的文件（用户文件的删除权不在我们）。"""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return (
        resolved.name.startswith("paste-")
        and resolved.suffix == ".png"
        and resolved.parent == (Path(tempfile.gettempdir()) / _CLIPBOARD_IMAGE_DIR).resolve()
    )


def _discard_clipboard_cache(path) -> None:
    """附件移除/发送完成后，把粘贴产生的临时文件收回（用户文件绝不动）。"""
    try:
        resolved = Path(path).resolve()
    except (OSError, TypeError):
        return
    if not _is_clipboard_cache_file(resolved):
        return
    try:
        resolved.unlink(missing_ok=True)
    except OSError:
        pass  # 删除失败只是临时目录里多躺一天，懒清扫会收


def _looks_like_real_text(text: str) -> bool:
    """剪贴板文本是"真内容"还是截图工具附带的文件名占位。

    占位形态：单行、不超过 260 字符、长得像图片文件名/路径。Office 里复制的文字
    （多行/自然语言）一律算真内容——那时剪贴板里的位图是 Office 顺手渲染的，
    不是用户想粘的东西。
    """
    if not text:
        return False
    if "\n" in text or len(text) > 260:
        return True
    tail = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, ext = tail.rpartition(".")
    return not (dot and stem and ext.lower() in ("png", "jpg", "jpeg", "webp", "gif", "bmp"))


class AttachmentTextEdit(QPlainTextEdit):
    """输入框：文本编辑 + 附件入口。

    三条进附件的路：

    - 拖拽本地文件；
    - **Ctrl+V 粘贴**：剪贴板里的图（截图工具/复制图片）优先于文本——截图工具
      常同时塞一段占位文本，按文本粘就把图丢了；复制的本地文件同样进附件；
    - **拖拽内存图**（浏览器里直接拖图，没有本地 URL）：与粘贴同一条落盘路。

    内存图没有路径，先落成临时 PNG 再走统一的附件校验——"附件链只认路径"这一件事不变。
    """

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt API
        if source.hasImage():
            # 有图不一定取图：Office 复制单元格/段落时剪贴板同时带真文本+位图渲染，
            # 一律取图会把"粘文字"变成"粘截图"。只在文本为空或文件名占位时才让图赢。
            text = source.text().strip() if source.hasText() else ""
            if not _looks_like_real_text(text):
                path = _save_clipboard_image(_mime_to_image(source))
                if path is not None:
                    self.files_dropped.emit([str(path)])
                    return
        if source.hasUrls():
            paths = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        mime = event.mimeData()
        if mime.hasImage() or (mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        mime = event.mimeData()
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        if mime.hasImage():
            path = _save_clipboard_image(_mime_to_image(mime))
            if path is not None:
                self.files_dropped.emit([str(path)])
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class AttachmentChip(QFrame):
    remove_requested = Signal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.setObjectName("attachment-chip")
        self.setToolTip(str(self.path))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 5, 4)
        layout.setSpacing(6)

        self.preview = QLabel(self)
        self.preview.setObjectName("attachment-preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(34, 34)
        # 用 QImageReader 限定解码尺寸（34px 预览），避免 10MB 级大图整图解码
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        reader.setScaledSize(self.preview.size())
        image = reader.read()
        if not image.isNull():
            self.preview.setPixmap(
                QPixmap.fromImage(image).scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.preview.setPixmap(tool_icon(self.preview, "attach", 18).pixmap(22, 22))
        layout.addWidget(self.preview)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        self.name_label = QLabel(self.path.name, self)
        self.name_label.setObjectName("attachment-name")
        self.name_label.setMaximumWidth(150)
        labels.addWidget(self.name_label)
        suffix = self.path.suffix.lstrip(".").upper() or "文件"
        self.meta_label = QLabel(f"{suffix} · {_attachment_file_size(self.path)} B", self)
        self.meta_label.setObjectName("attachment-meta")
        labels.addWidget(self.meta_label)
        layout.addLayout(labels)

        self.remove_button = tool_button(
            self, "attachment-remove-button", "exit", "移除附件", dark=True
        )
        self.remove_button.setFixedSize(20, 20)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(str(self.path)))
        self.remove_button.hide()
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.remove_button.show()
        if event is not None:
            super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.remove_button.hide()
        if event is not None:
            super().leaveEvent(event)


class ChatComposer(QFrame):
    send_requested = Signal()
    notice = Signal(str)  # 附件超限等需要明确提示用户的事件
    record_requested = Signal()
    record_stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat-composer")
        self.setAcceptDrops(True)
        self._busy = False
        self._ime_composing = False  # 输入法组合中（防止回车误发送）
        self.attachment_paths: list[Path] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 7, 9, 7)
        root.setSpacing(3)

        self.attachment_strip = QFrame(self)
        self.attachment_strip.setObjectName("attachment-strip")
        self.attachment_layout = QHBoxLayout(self.attachment_strip)
        self.attachment_layout.setContentsMargins(0, 0, 0, 2)
        self.attachment_layout.setSpacing(5)
        self.attachment_strip.hide()
        root.addWidget(self.attachment_strip)

        self.input = AttachmentTextEdit(self)
        self.input.setObjectName("chat-input")
        self._placeholder = "和桌宠说点什么…  Enter 发送，Shift+Enter 换行"
        self.input.setPlaceholderText(self._placeholder)
        self.input.setMinimumHeight(38)
        self.input.setMaximumHeight(68)
        self.input.installEventFilter(self)
        self.input.files_dropped.connect(self.add_attachments)
        root.addWidget(self.input)

        self.partial_label = QLabel("", self)
        self.partial_label.setObjectName("composer-partial")
        self.partial_label.setWordWrap(True)
        self.partial_label.hide()
        root.addWidget(self.partial_label)
        self._partial_mirror = ""

        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("composer-toolbar")
        footer = QHBoxLayout(self.toolbar)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(5)
        self.attach_button = tool_button(
            self.toolbar, "composer-attach-button", "attach", "添加附件"
        )
        self.attach_button.clicked.connect(self.choose_attachments)
        footer.addWidget(self.attach_button)
        self.record_button = tool_button(
            self.toolbar, "composer-record-button", "mic", "语音输入"
        )
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self._toggle_record)
        footer.addWidget(self.record_button)
        self.hint = QLabel("内容会保存到当前角色的本地会话")
        self.hint.setObjectName("composer-hint")
        footer.addWidget(self.hint)
        footer.addStretch(1)
        self.send = QToolButton(self)
        self.send.setObjectName("send-button")
        self.send.setProperty("modernDark", True)  # 强调色底 → 浅色图标
        self.send.setIcon(tool_icon(self.send, "send", 17, dark=True))
        self.send.setFixedSize(34, 34)
        self.send.setToolTip("发送")
        self.send.setAccessibleName("发送")
        self.send.clicked.connect(self.send_requested)
        footer.addWidget(self.send)
        root.addWidget(self.toolbar)
        self.input.textChanged.connect(self._update_enabled)
        self.input.textChanged.connect(self._on_input_text_changed)
        self._update_enabled()

    def choose_attachments(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "选择附件",
            "",
            # 文件选择器只声明真正支持的格式：PDF 等仅能作为文件名占位、没有实际
            # 内容发送的类型不再收录（避免“宣称支持却只发文件名”的误导）。
            "支持的文件 (*.txt *.md *.json *.csv *.py *.log *.jpg *.jpeg *.png"
            " *.webp *.gif);;所有文件 (*)",
        )
        self.add_attachments(paths)

    def add_attachments(self, paths) -> None:
        new_paths, warnings = validate_attachment_additions(self.attachment_paths, paths)
        if new_paths:
            self.attachment_paths.extend(new_paths)
            self._refresh_attachments()
            self._update_enabled()
        if warnings:
            self._flash_notice("；".join(warnings))

    def _toggle_record(self, checked: bool) -> None:
        if checked:
            self.record_requested.emit()
        else:
            self.record_stopped.emit()

    def set_recording(self, recording: bool, hint: str = "") -> None:
        self.record_button.blockSignals(True)
        self.record_button.setChecked(bool(recording))
        self.record_button.blockSignals(False)
        self.record_button.setProperty("recording", bool(recording))
        self.record_button.setIcon(
            tool_icon(
                self.record_button,
                "stop" if recording else "mic",
                17,
                dark=bool(recording),
            )
        )
        label = "停止录音" if recording else "语音输入"
        self.record_button.setToolTip(label)
        self.record_button.setAccessibleName(label)
        style = self.record_button.style()
        style.unpolish(self.record_button)
        style.polish(self.record_button)
        if hint:
            self._flash_notice(hint)

    def set_partial_transcript(self, text: str) -> None:
        """实时预览识别文字：不覆盖用户已经手打的内容。

        输入框为空或只包含上一段预览时，直接把预览镜像进输入框（用户能看到
        逐字增长）；用户已经打了字时只更新提示行，停止录音后最终文本才会
        ``insert_text`` 追加。
        """
        value = str(text or "").strip()
        if not value:
            self.clear_partial()
            return
        current = self.input.toPlainText().strip()
        if not current or current == self._partial_mirror:
            self._partial_mirror = value
            self.input.blockSignals(True)
            self.input.setPlainText(value)
            self.input.blockSignals(False)
            cursor = self.input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input.setTextCursor(cursor)
            self._update_enabled()
            self.partial_label.hide()
            return
        self._partial_mirror = value
        self.partial_label.setText(f"识别中：{value}")
        self.partial_label.show()

    def clear_partial(self) -> None:
        """清掉预览；只有输入框仍然是预览镜像时才清空，别删用户手打的内容。"""
        mirror = self._partial_mirror
        self._partial_mirror = ""
        self.partial_label.clear()
        self.partial_label.hide()
        if mirror and self.input.toPlainText().strip() == mirror:
            self.input.blockSignals(True)
            self.input.clear()
            self.input.blockSignals(False)
            self._update_enabled()

    def insert_text(self, text: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        self.clear_partial()
        current = self.input.toPlainText()
        prefix = "" if not current or current.endswith((" ", "\n")) else " "
        self.input.insertPlainText(prefix + value)
        self.input.setFocus()

    def _flash_notice(self, message: str) -> None:
        """超限提示：发出信号并在输入区底部提示栏短暂显示。"""
        self.notice.emit(message)
        if self.hint is not None:
            self.hint.setText(message)
            QTimer.singleShot(5000, self, lambda: self.hint.setText("内容会保存到当前角色的本地会话"))

    def _refresh_attachments(self) -> None:
        while self.attachment_layout.count():
            item = self.attachment_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for path in self.attachment_paths:
            chip = AttachmentChip(path, self.attachment_strip)
            chip.remove_requested.connect(lambda value: self.remove_attachment(Path(value)))
            self.attachment_layout.addWidget(chip)
        self.attachment_layout.addStretch(1)
        self.attachment_strip.setVisible(bool(self.attachment_paths))

    def remove_attachment(self, path: Path) -> None:
        self.attachment_paths = [item for item in self.attachment_paths if item != path]
        _discard_clipboard_cache(path)
        self._refresh_attachments()
        self._update_enabled()

    def clear_attachments(self) -> None:
        for path in self.attachment_paths:
            _discard_clipboard_cache(path)
        self.attachment_paths.clear()
        self._refresh_attachments()
        self._update_enabled()

    def attachment_payloads(self) -> list[dict]:
        """把附件整理成 mod 可直接消费的字典列表（文本内容 / 图片 data URL）。"""
        payloads: list[dict] = []
        text_budget = MAX_TEXT_TOTAL_CHARS
        for path in self.attachment_paths:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            item = {
                "path": str(path),
                "name": path.name,
                "mime": mime,
                "text": None,
                "image_data_url": None,
            }
            if _attachment_is_text(path):
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = None
                if content is not None:
                    if len(content) > text_budget:
                        content = content[:text_budget]
                    text_budget = max(0, text_budget - len(content))
                    item["text"] = content
            elif mime.startswith("image/"):
                size = _attachment_file_size(path)
                if 0 < size <= MAX_IMAGE_BYTES:
                    try:
                        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    except OSError:
                        encoded = ""
                    if encoded:
                        item["image_data_url"] = f"data:{mime};base64,{encoded}"
            payloads.append(item)
        return payloads

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.add_attachments(paths)
        event.acceptProposedAction()

    def _sync_placeholder(self) -> None:
        """拼音组合期间隐藏 placeholder（preedit 不进文档，Qt 不会自己收）。

        ``QPlainTextEdit`` 只在文档为空时绘制 placeholder，而输入法 preedit 属于
        输入法层、不写入 document——不手动清空就会和拼音叠在一起。
        """
        text = "" if self._ime_composing else self._placeholder
        if self.input.placeholderText() != text:
            self.input.setPlaceholderText(text)

    def _on_input_text_changed(self) -> None:
        # 用户手动清空时恢复提示；组合中保持隐藏。
        if not self._ime_composing and not self.input.toPlainText():
            self._sync_placeholder()

    def eventFilter(self, obj, event):
        # 输入法组合状态跟踪：组合中回车用于上屏候选，且 placeholder 必须隐藏。
        if event.type() == QEvent.Type.InputMethod:
            self._ime_composing = bool(event.preeditString())
            if event.commitString():
                self._ime_composing = False
            self._sync_placeholder()
        elif obj is self.input and event.type() == QEvent.Type.FocusOut:
            self._ime_composing = False
            self._sync_placeholder()
        elif obj is self.input and event.type() == QEvent.Type.KeyPress:
            is_return = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            if is_return and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self._ime_composing:
                    return False  # 交给输入法上屏候选
                self.send_requested.emit()
                return True
        return super().eventFilter(obj, event)

    def _update_enabled(self) -> None:
        self.send.setEnabled(
            self._busy
            or bool(self.input.toPlainText().strip())
            or bool(self.attachment_paths)
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.send.setIcon(tool_icon(self.send, "stop" if busy else "send", 17, dark=True))
        self.send.setToolTip("停止" if busy else "发送")
        self.send.setAccessibleName("停止" if busy else "发送")
        self.send.setProperty("busy", self._busy)
        self._update_enabled()
        self.send.style().unpolish(self.send)
        self.send.style().polish(self.send)


class SidebarScrim(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.clicked.emit()
        event.accept()


def _initial(character_id: str) -> str:
    text = str(character_id or "宠").strip()
    return text[:1].upper() or "宠"


__all__ = [
    "AttachmentChip",
    "AttachmentTextEdit",
    "ChatComposer",
    "ChatTitleBar",
    "MessageBubble",
    "SessionListRow",
    "SidebarScrim",
    "validate_attachment_additions",
]
