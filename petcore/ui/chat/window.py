"""Core 聊天窗口：现代双栏（会话侧栏 + 消息画布 + 浮动输入区）。

移植自主分支 ``pet/chat/widgets.py`` 的 ``ChatWindow``：窗口、Markdown、气泡、
附件与无边框拖拽/缩放都在 core；mod 只通过 ``ChatPort``（``ChatController``）驱动。
窗口本身不持有会话存储，只负责渲染 controller 给的消息列表。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QRegion,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedLayout,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...chat.history_buckets import buckets
from . import themes
from .dsh_view import DshView
from .icons import tool_icon
from .styles import DEFAULT_ACCENT, clamp_font_size, modern_stylesheet, safe_accent
from .widgets import (
    ChatComposer,
    ChatTitleBar,
    MessageBubble,
    SessionListRow,
    SidebarScrim,
    _initial,
)

logger = logging.getLogger("petcore.ui.chat.window")

FLUSH_INTERVAL_MS = 50
COMPACT_WIDTH = 880
SIDEBAR_WIDTH = 238
SHELL_MARGIN = 7
#: 必须与 QSS ``QFrame#phone-shell`` 的 ``border-radius: 14px`` 一致：
#: 两处不一致时壁纸裁剪会露出一角白边。
SHELL_RADIUS = 14.0
#: 扩展视图的统一内缩：原生子窗口（QWebView/QWindowContainer）不会被圆角 mask
#: 裁掉，12px 内缩在几何上已落在 14px 圆弧内侧。
CHAT_VIEW_INSET = 12
#: 标题栏内联的扩展视图按钮上限，多出来的进 ``»`` 菜单。
MAX_INLINE_VIEW_BUTTONS = 2
#: 跟随桌宠时与身体框的间距；40ms 的单次合并定时器避免 60Hz 移动时反复重排。
FOLLOW_GAP = 14
FOLLOW_COALESCE_MS = 40
#: 侧栏组头行：``UserRole`` 留给会话 id，组头用独立 role。
GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class ChatWindow(QDialog):
    submitted = Signal(str)
    stop_requested = Signal()
    cleared = Signal()
    session_selected = Signal(str)
    closed = Signal()
    pin_toggled = Signal(bool)
    record_requested = Signal()
    record_stopped = Signal()
    session_archive_requested = Signal(str, bool)
    session_delete_requested = Signal(str)
    archived_toggled = Signal(bool)
    dsh_session_started = Signal(str)
    dsh_submitted = Signal(str)
    #: 用户切了 AI/DSH 视图：controller 据此把「当前会话」切到同通道的会话，
    #: 否则关掉 DSH 后输入框仍往 DSH 会话里写（实测过的错位）。
    view_changed = Signal(str)
    #: 任意视图切换（含扩展的 ``chat_view``）。``view_changed`` 只发 ai/dsh，
    #: 因为那条信号会切换会话通道；扩展视图不改通道。
    view_activated = Signal(str)
    #: 用户点了标题栏的深色按钮：controller 负责落盘 ``chat_dark_mode``。
    dark_toggled = Signal(bool)

    def __init__(
        self,
        *,
        title: str = "AI 对话",
        font_size: int = 13,
        font_family: str = "",
        dsh=None,
        dsh_hub=None,
        dsh_enabled: bool = False,
        character_id: str = "default",
        accent: str = DEFAULT_ACCENT,
        background: str = "",
        always_on_top: bool = False,
        provider: str = "",
        parent: QWidget | None = None,
        pet_window: QWidget | None = None,
        follow_pet: bool = False,
        views=(),
        dark: bool = False,
        pet_slot: int = 0,
    ) -> None:
        super().__init__(parent)
        self.character_id = str(character_id or "default")
        self._pet_slot = max(0, int(pet_slot or 0))
        self._font_size = clamp_font_size(font_size)
        self._font_family = str(font_family or "").strip()
        self._dsh_client = dsh
        self._dsh_hub = dsh_hub
        self._dsh_enabled = bool(dsh_enabled)
        self._base_accent = safe_accent(accent)
        self.accent_color = self._base_accent
        self._bg_value = str(background or "").strip()
        self._bg_pixmap = None
        self._bg_theme = None
        self._bg_scaled = None
        self._bg_scaled_size: QSize | None = None
        self._messages: list[dict[str, str]] = []
        self._stream_text = ""
        self._streaming = False
        self._stream_bubble: MessageBubble | None = None
        self._last_thinking: tuple[str, list[str]] = ("", [])
        self._bubbles: list[MessageBubble] = []
        self._pending_attachments: list[dict] = []
        self._last_error = ""
        self._active_session_id = ""
        self._session_titles: dict[str, str] = {}
        self._compact_layout = False
        self._sidebar_open = True
        self._show_archived = False
        self._collapsed_groups: set[str] = {"month", "older"}
        self._group_rows: dict[str, list[int]] = {}
        self._group_headers: dict[str, QListWidgetItem] = {}
        self._group_labels: dict[str, str] = {}
        self._channel_rows: dict[str, list[int]] = {}
        self._channel_headers: dict[str, QListWidgetItem] = {}
        self._channel_labels: dict[str, str] = {}
        self._row_by_session: dict[str, int] = {}
        self._scroll_pin = True
        self._auto_scrolling = False
        self._stream_follow_output = True
        self._resize_edges: set[str] = set()
        self._resize_global_start: QPoint | None = None
        self._resize_start_geometry: QRect | None = None
        self._pet_window = pet_window
        self._follow_pet = bool(follow_pet)
        self._pet_follow_wired = False
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(FOLLOW_COALESCE_MS)
        self._follow_timer.timeout.connect(self._on_follow_tick)

        self.setObjectName("chat-window")
        self.setWindowTitle(title)
        # 窗口级图标主题：QSS 表面为浅色，深色系统 palette 前景是白色，
        # 会让关闭/最小化/新建等图标白底白图不可见。
        self.setProperty("menuStyle", "modern")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        if always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # Desktop AI-chat workspace: conversation navigation on the left and
        # the focused message canvas on the right.
        self.setMinimumSize(560, 480)
        # 4K 上限：原来的 1600×1200 会把大屏上的手动缩放卡住（最大化本身不受
        # maximumSize 限制，但边拖 resize 会被它 clamp）。
        self.setMaximumSize(3840, 2160)
        self.resize(960, 700)
        # Frameless window has no system resize border on Windows; track the
        # mouse so edge hover can switch to the resize cursor and drags resize.
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 无边框圆角窗：窗口自身透明（QSS #chat-window 背景同步置 transparent），
        # 只显示圆角的 phone-shell，去掉窗外一圈方形背景。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        # 边缘缩放光标改用 hover 事件持续刷新：子控件无鼠标跟踪时窗口的
        # mouseMoveEvent 会停更，导致从窗口外进入后光标卡在缩放双箭头。
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._provider = str(provider or "")
        self._dark = bool(dark)
        self._view_specs = list(views or ())
        self._view_spec_map = {str(spec.id): spec for spec in self._view_specs}
        self._view_pages: dict[str, QWidget] = {}
        self._view_widgets: dict[str, QWidget] = {}
        self._view_buttons: dict[str, QToolButton] = {}
        self._current_view = "ai"
        self._build()
        self._apply_icon_theme()
        self._connect()
        self._resolve_background()
        self._style()
        self._update_responsive_layout()
        self._wire_pet_follow()

    # ---------------------------------------------------------------- 跟随桌宠
    def follows_pet(self) -> bool:
        return bool(self._follow_pet)

    def set_pet_window(self, pet_window: QWidget | None) -> None:
        """换绑跟随对象；重复调用幂等，对象已销毁时安静跳过。"""
        if pet_window is self._pet_window:
            self._wire_pet_follow()
            return
        self._unwire_pet_follow()
        self._pet_window = pet_window
        self._wire_pet_follow()
        if self._follow_pet and self.isVisible():
            self.position_near_pet()

    def set_follow_pet(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._follow_pet = enabled
        if enabled:
            self._wire_pet_follow()
            if self.isVisible():
                self.position_near_pet()
            return
        self._follow_timer.stop()
        self._unwire_pet_follow()

    def position_near_pet(self) -> None:
        """贴到桌宠身体框旁边：右侧优先，溢出换左侧，再不行上下落位并钳制在屏内。"""
        if self.isMaximized():
            # 跟随桌宠每 40ms 调一次 move()；最大化时 move() 会取消最大化。
            return
        pet = self._pet_window
        if pet is None:
            return
        try:
            if not pet.isVisible():
                return
            rect = pet.body_rect()
        except RuntimeError:
            return
        if not rect:
            return
        body = QRect(*rect)
        center = body.center()
        screen = QGuiApplication.screenAt(center) or self.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width, height = self.width(), self.height()
        right = area.x() + area.width()
        bottom = area.y() + area.height()
        top_y = body.y() + (body.height() - height) // 2
        right_x = body.x() + body.width() + FOLLOW_GAP
        left_x = body.x() - FOLLOW_GAP - width
        if right_x + width <= right:
            x, y = right_x, top_y
        elif left_x >= area.x():
            x, y = left_x, top_y
        else:
            x = body.x() + (body.width() - width) // 2
            below_y = body.y() + body.height() + FOLLOW_GAP
            above_y = body.y() - FOLLOW_GAP - height
            if below_y + height <= bottom:
                y = below_y
            elif above_y >= area.y():
                y = above_y
            else:
                y = top_y
        # 最终位置必须整窗落在可用区内：先偏好落位，再钳制。
        x = max(area.x(), min(x, right - width))
        y = max(area.y(), min(y, bottom - height))
        self.move(x, y)

    def _wire_pet_follow(self) -> None:
        if self._pet_follow_wired or not self._follow_pet:
            return
        pet = self._pet_window
        if pet is None:
            return
        try:
            pet.pet_moved.connect(self._queue_follow)
        except (AttributeError, RuntimeError, TypeError):
            return
        self._pet_follow_wired = True

    def _unwire_pet_follow(self) -> None:
        if not self._pet_follow_wired:
            return
        self._pet_follow_wired = False
        pet = self._pet_window
        if pet is None:
            return
        try:
            pet.pet_moved.disconnect(self._queue_follow)
        except (AttributeError, RuntimeError, TypeError):
            return

    def _queue_follow(self) -> None:
        """桌宠移动信号：只排一次 40ms 合并刷新，60Hz 移动不会拖着窗口抖。"""
        if not self._follow_pet or not self.isVisible():
            return
        if not self._follow_timer.isActive():
            self._follow_timer.start()

    def _on_follow_tick(self) -> None:
        if not self._follow_pet or not self.isVisible():
            return
        self.position_near_pet()

    # ---------------------------------------------------------------- 公开属性
    def messages(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._messages]

    def bubbles(self) -> list[MessageBubble]:
        """当前时间线上的气泡（测试与调试用；顺序同消息顺序）。"""
        return list(self._bubbles)

    def stream_text(self) -> str:
        return self._stream_text

    def is_streaming(self) -> bool:
        return self._streaming

    def transcript_text(self) -> str:
        """可见消息的原始文本（assistant 不渲染 Markdown，返回源码）。"""
        return "\n".join(str(item.get("content") or "") for item in self._messages)

    def pending_attachments(self) -> list[dict]:
        return [dict(item) for item in self._pending_attachments]

    def take_attachments(self) -> list[dict]:
        items = self._pending_attachments
        self._pending_attachments = []
        return [dict(item) for item in items]

    def is_always_on_top(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def background_value(self) -> str:
        return self._bg_value

    def font_size(self) -> int:
        return self._font_size

    def font_family(self) -> str:
        return self._font_family

    def has_background(self) -> bool:
        return self._bg_pixmap is not None

    # ---------------------------------------------------------------- 外观
    def set_font_size(self, size: int) -> None:
        self._font_size = clamp_font_size(size)
        self._style()

    def set_font_family(self, family: str) -> None:
        self._font_family = str(family or "").strip()
        self._style()

    def set_recording(self, recording: bool, hint: str = "") -> None:
        self.composer.set_recording(bool(recording), hint)

    def set_partial_transcript(self, text: str) -> None:
        self.composer.set_partial_transcript(text)

    def insert_transcript(self, text: str, *, auto_send: bool = False) -> None:
        self.composer.insert_text(text)
        if auto_send:
            self.composer.send_requested.emit()

    def set_accent(self, accent: str) -> None:
        self._base_accent = safe_accent(accent)
        # ``_style()`` 读的是 ``accent_color``：只改 ``_base_accent`` 会让实时换色静默失效。
        self.accent_color = self._base_accent
        self._style()

    def set_background(self, background: str) -> None:
        self._bg_value = str(background or "").strip()
        self._resolve_background()
        self._style()

    # ---------------------------------------------------------------- 深色模式
    def is_dark(self) -> bool:
        return bool(self._dark)

    def set_dark(self, dark: bool) -> None:
        """切换对话窗深色模式；不落盘，由 controller 的 ``dark_toggled`` 负责。"""
        dark = bool(dark)
        if dark == self._dark:
            self.dark_button.blockSignals(True)
            self.dark_button.setChecked(dark)
            self.dark_button.blockSignals(False)
            return
        self._dark = dark
        self.dark_button.blockSignals(True)
        self.dark_button.setChecked(dark)
        self.dark_button.blockSignals(False)
        self._style()
        self._apply_icon_theme()
        for widget in self._view_widgets.values():
            self._apply_view_dark(widget)
        self.update()

    def _apply_icon_theme(self) -> None:
        """自绘图标按底色换墨色：深色面板上默认墨色看不见。"""
        dark = self._dark
        self.sidebar_toggle_button.setIcon(
            tool_icon(self.sidebar_toggle_button, "sidebar", 17, dark=dark)
        )
        self.dark_button.setIcon(
            tool_icon(self.dark_button, "sun" if dark else "moon", 16, dark=dark)
        )
        self.dark_button.setToolTip(
            "切回浅色（只影响对话窗）" if dark else "深色模式（只影响对话窗）"
        )
        self.pin_button.setIcon(tool_icon(self.pin_button, "pin", 16, dark=dark))
        maximized = bool(self.isMaximized())
        self.maximize_button.setIcon(
            tool_icon(
                self.maximize_button, "restore" if maximized else "maximize", 16, dark=dark
            )
        )
        self.minimize_button.setIcon(
            tool_icon(self.minimize_button, "minimize", 16, dark=dark)
        )
        self.close_button.setIcon(tool_icon(self.close_button, "exit", 16, dark=dark))
        for view_id, button in self._view_buttons.items():
            spec = self._view_spec_map.get(view_id)
            icon = str(getattr(spec, "icon", "") or "") if spec is not None else ""
            if icon:
                button.setIcon(tool_icon(button, icon, 16, dark=dark))
        more = getattr(self, "view_more_button", None)
        if more is not None:
            more.setIcon(tool_icon(more, "more", 16, dark=dark))

    def toggle_always_on_top(self) -> None:
        on = self.pin_button.isChecked()
        self.set_always_on_top(on)
        self.pin_toggled.emit(on)

    def set_always_on_top(self, on: bool) -> None:
        """切换聊天窗置顶；保留位置、尺寸、最大化状态与可见/激活状态。"""
        was_visible = self.isVisible()
        was_active = self.isActiveWindow()
        was_maximized = self.isMaximized()
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(bool(on))
        self.pin_button.blockSignals(False)
        if was_maximized:
            # 最大化时 setGeometry 会被 Qt 当成「取消最大化」，还会把还原尺寸
            # 写成整屏几何（实测：之后 showNormal() 回不到原尺寸）。
            if was_visible:
                self.show()
                self.showMaximized()
        else:
            self.setGeometry(geometry)
            if was_visible:
                self.show()
        if was_visible and was_active:
            self.activateWindow()
        self._sync_maximized()

    # ---------------------------------------------------------------- 最大化
    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_maximized()

    def _sync_maximized(self) -> None:
        """最大化 = 真铺满：去掉 7px 透明外环与圆角，缩放边停用。

        不做这一步时，``showMaximized()`` 只会把窗口撑到工作区，但
        ``phone_shell`` 仍带着 ``SHELL_MARGIN`` 与 14px 圆角——用户看到的是
        「四周漏 7px 桌面 + 四个圆角」，不是最大化。
        """
        maximized = bool(self.isMaximized())
        margin = 0 if maximized else SHELL_MARGIN
        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(margin, margin, margin, margin)
        self._shell_radius = 0.0 if maximized else SHELL_RADIUS
        self.phone_shell.setProperty("maximized", maximized)
        style = self.phone_shell.style()
        style.unpolish(self.phone_shell)
        style.polish(self.phone_shell)
        self._apply_icon_theme()
        self.maximize_button.setToolTip("还原" if maximized else "最大化")
        self.maximize_button.setAccessibleName(
            "还原聊天窗口" if maximized else "最大化聊天窗口"
        )
        self._apply_shell_mask()
        self.update()

    def _apply_shell_mask(self) -> None:
        """把子控件裁进圆角：QSS 不裁剪子控件，方形白底会盖掉四角。

        实测（960×700）右下角圆角外有 30 个不透明纯白像素，元凶是
        ``#chat-view-stack`` / ``#chat-ai-view`` 的方形白底。把这几层改成透明
        是主修法，这个 mask 是兜底（音频/皮肤等以后新增的表面同样被裁住）。
        """
        shell = getattr(self, "phone_shell", None)
        if shell is None:
            return
        radius = float(getattr(self, "_shell_radius", SHELL_RADIUS))
        width, height = shell.width(), shell.height()
        if radius <= 0.5 or width <= 0 or height <= 0:
            shell.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0.0, 0.0, float(width), float(height)), radius, radius
        )
        shell.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _resolve_background(self) -> None:
        self._bg_pixmap = themes.resolve_background_pixmap(self._bg_value)
        self._bg_theme = (
            themes.get_theme(self._bg_value[8:]) if self._bg_value.startswith("builtin:") else None
        )
        self._bg_scaled = None
        self._bg_scaled_size = None

    def _style(self) -> None:
        overlay = ""
        if self._bg_pixmap is not None:
            overlay = themes.build_modern_custom_overlay_qss(
                self.accent_color, dark=self._dark
            )
        self.setStyleSheet(
            modern_stylesheet(
                self.accent_color,
                self._font_size,
                overlay,
                self._font_family,
                dark=self._dark,
            )
        )
        self._set_background_surface_transparency(self._bg_pixmap is not None)
        self._apply_header_avatar_style()
        self.update()

    def _set_background_surface_transparency(self, enabled: bool) -> None:
        """Prevent palette auto-fill from painting over a configured wallpaper."""
        self.phone_shell.setAutoFillBackground(not enabled)
        for widget in (
            self.scroll,
            self.scroll.viewport(),
            self.message_view,
            self.empty_page,
            self.timeline_host,
        ):
            widget.setAutoFillBackground(False)

    def _apply_header_avatar_style(self) -> None:
        self.avatar_label.setStyleSheet("background: transparent; border: none; padding: 0;")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._bg_pixmap is None:
            super().paintEvent(event)
            return
        target = self.phone_shell.geometry()
        if target.isEmpty():
            target = self.rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(target), SHELL_RADIUS, SHELL_RADIUS)
        painter.setClipPath(clip)
        painter.fillPath(clip, QColor("#15171c" if self._dark else "#ffffff"))
        dpr = float(self.devicePixelRatioF() or 1.0)
        if (
            self._bg_scaled is None
            or self._bg_scaled_size != target.size()
            or abs(getattr(self, "_bg_dpr", 0.0) - dpr) > 1e-6
        ):
            self._bg_scaled = themes.scale_background_pixmap(
                self._bg_pixmap, target.width(), target.height(), "cover", dpr=dpr
            )
            self._bg_scaled_size = target.size()
            self._bg_dpr = dpr
        focus = themes.background_focus_rect(self._bg_theme, {}, self._bg_value)
        # 壁纸 pixmap 带 DPR：几何一律用逻辑尺寸，别拿物理宽高去算偏移。
        logical = self._bg_scaled.deviceIndependentSize()
        x, y = themes.background_draw_offset(
            target.x(),
            target.y(),
            target.width(),
            target.height(),
            int(round(logical.width())),
            int(round(logical.height())),
            focus,
            "cover",
        )
        painter.drawPixmap(x, y, self._bg_scaled)
        if self._bg_theme is not None:
            painter.fillPath(clip, QColor(*themes.scrim_rgba(self._bg_theme)))
        painter.end()

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHELL_MARGIN, SHELL_MARGIN, SHELL_MARGIN, SHELL_MARGIN)
        outer.setSpacing(0)
        self.phone_shell = QFrame(self)
        self.phone_shell.setObjectName("phone-shell")
        self.phone_shell.setAutoFillBackground(True)
        outer.addWidget(self.phone_shell)

        root = QHBoxLayout(self.phone_shell)
        self.root_layout = root
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # DeepSeek-style persistent navigation rail.
        self.sidebar = QFrame(self.phone_shell)
        self.sidebar.setObjectName("deepseek-sidebar")
        self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 14)
        sidebar_layout.setSpacing(10)

        brand = QHBoxLayout()
        brand.setContentsMargins(2, 0, 2, 2)
        brand.setSpacing(9)
        self.avatar_label = QLabel(_initial(self.character_id), self.sidebar)
        self.avatar_label.setObjectName("avatar-label")
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setFixedSize(34, 34)
        brand.addWidget(self.avatar_label)
        self.brand_label = QLabel("鲸语 AI", self.sidebar)
        self.brand_label.setObjectName("brand-label")
        brand.addWidget(self.brand_label)
        brand.addStretch(1)
        sidebar_layout.addLayout(brand)

        self.new_session_button = QPushButton("开启新对话", self.sidebar)
        self.new_session_button.setObjectName("new-conversation-button")
        self.new_session_button.setIcon(tool_icon(self.new_session_button, "add", 16))
        self.new_session_button.setToolTip("新建会话")
        self.new_session_button.setAccessibleName("新建会话")
        self.new_session_button.setFixedHeight(34)
        new_session_shadow = QGraphicsDropShadowEffect(self.new_session_button)
        new_session_shadow.setBlurRadius(14)
        new_session_shadow.setOffset(0, 4)
        new_session_shadow.setColor(QColor(35, 45, 68, 30))
        self.new_session_button.setGraphicsEffect(new_session_shadow)
        sidebar_layout.addWidget(self.new_session_button)

        self.session_caption = QLabel("会话", self.sidebar)
        self.session_caption.setObjectName("session-section-title")
        sidebar_layout.addWidget(self.session_caption)
        self.session_list = QListWidget(self.sidebar)
        self.session_list.setObjectName("session-list")
        self.session_list.setFrameShape(QFrame.Shape.NoFrame)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.setSpacing(2)
        sidebar_layout.addWidget(self.session_list, 1)

        sidebar_footer = QFrame(self.sidebar)
        sidebar_footer.setObjectName("sidebar-footer")
        footer_layout = QHBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(9, 8, 9, 8)
        footer_layout.setSpacing(6)
        footer_layout.addStretch(1)
        self.archive_button = QToolButton(sidebar_footer)
        self.archive_button.setObjectName("archive-toggle-button")
        self.archive_button.setText("已归档")
        self.archive_button.setCheckable(True)
        self.archive_button.setToolTip("显示或隐藏已归档会话")
        self.archive_button.setAccessibleName("显示已归档会话")
        footer_layout.addWidget(self.archive_button)
        self.clear_button = QToolButton(sidebar_footer)
        self.clear_button.setObjectName("clear-session-button")
        self.clear_button.setIcon(tool_icon(self.clear_button, "clear", 15))
        self.clear_button.setToolTip("清空当前会话")
        self.clear_button.setAccessibleName("清空当前会话")
        footer_layout.addWidget(self.clear_button)
        sidebar_layout.addWidget(sidebar_footer)
        root.addWidget(self.sidebar)

        chat_main = QFrame(self.phone_shell)
        chat_main.setObjectName("chat-main")
        self.chat_main = chat_main
        chat_main_layout = QVBoxLayout(chat_main)
        self.chat_main_layout = chat_main_layout
        chat_main_layout.setContentsMargins(0, 0, 0, 0)
        chat_main_layout.setSpacing(0)
        root.addWidget(chat_main, 1)

        # Compact conversation header doubles as the frameless drag handle.
        self.title_bar = ChatTitleBar(chat_main)
        self.title_bar.setObjectName("chat-main-header")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(20, 13, 13, 11)
        title_layout.setSpacing(8)
        title_text = QVBoxLayout()
        title_text.setSpacing(1)
        self.title_label = QLabel("初次问候", self.title_bar)
        self.title_label.setObjectName("title-label")
        self.subtitle_label = QLabel("✦ 快速模式", self.title_bar)
        self.subtitle_label.setObjectName("subtitle-label")
        title_text.addWidget(self.title_label)
        self.header_status = QFrame(self.title_bar)
        self.header_status.setObjectName("header-status")
        header_status_layout = QHBoxLayout(self.header_status)
        header_status_layout.setContentsMargins(0, 0, 0, 0)
        header_status_layout.setSpacing(5)
        header_status_layout.addWidget(self.subtitle_label)
        self.status_dot = QLabel("●", self.header_status)
        self.status_dot.setObjectName("status-dot")
        header_status_layout.addWidget(self.status_dot)
        self.status_label = QLabel("就绪", self.header_status)
        self.status_label.setObjectName("status-label")
        header_status_layout.addWidget(self.status_label)
        self.provider_label = QLabel(self._provider, self.header_status)
        self.provider_label.setObjectName("provider-label")
        self.provider_label.setMaximumWidth(150)
        self.provider_label.setToolTip(self._provider)
        for label in (self.title_label, self.subtitle_label, self.status_label, self.provider_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_status_layout.addWidget(self.provider_label)
        header_status_layout.addStretch(1)
        title_text.addWidget(self.header_status)
        title_layout.addLayout(title_text)
        title_layout.addStretch(1)
        self.sidebar_toggle_button = QToolButton(self.title_bar)
        self.sidebar_toggle_button.setObjectName("sidebar-toggle-button")
        self.sidebar_toggle_button.setIcon(tool_icon(self.sidebar_toggle_button, "sidebar", 17))
        self.sidebar_toggle_button.setToolTip("显示或隐藏会话侧栏")
        self.sidebar_toggle_button.setAccessibleName("显示或隐藏会话侧栏")
        self.view_button = QToolButton(self.title_bar)
        self.view_button.setObjectName("view-toggle-button")
        self.view_button.setText("DSH")
        self.view_button.setCheckable(True)
        self.view_button.setToolTip("切换到 DSH 对话")
        self.view_button.setAccessibleName("切换 AI / DSH 对话")
        self.view_button.clicked.connect(self._on_view_toggled)
        title_layout.addWidget(self.view_button)
        self._build_view_buttons(title_layout)
        title_layout.addWidget(self.sidebar_toggle_button)
        self.dark_button = QToolButton(self.title_bar)
        self.dark_button.setObjectName("window-dark-button")
        self.dark_button.setCheckable(True)
        self.dark_button.setChecked(self._dark)
        self.dark_button.setToolTip("深色模式（只影响对话窗）")
        self.dark_button.setAccessibleName("切换对话窗深色模式")
        title_layout.addWidget(self.dark_button)
        self.pin_button = QToolButton(self.title_bar)
        self.pin_button.setObjectName("window-pin-button")
        self.pin_button.setIcon(tool_icon(self.pin_button, "pin", 16))
        self.pin_button.setToolTip("窗口置顶")
        self.pin_button.setAccessibleName("切换聊天窗口置顶")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(self.is_always_on_top())
        title_layout.addWidget(self.pin_button)
        self.maximize_button = QToolButton(self.title_bar)
        self.maximize_button.setObjectName("window-maximize-button")
        self.maximize_button.setIcon(tool_icon(self.maximize_button, "maximize", 16))
        self.maximize_button.setToolTip("最大化")
        self.maximize_button.setAccessibleName("最大化聊天窗口")
        self.minimize_button = QToolButton(self.title_bar)
        self.minimize_button.setObjectName("window-minimize-button")
        self.minimize_button.setIcon(tool_icon(self.minimize_button, "minimize", 16))
        self.minimize_button.setToolTip("最小化")
        self.minimize_button.setAccessibleName("最小化聊天窗口")
        self.close_button = QToolButton(self.title_bar)
        self.close_button.setObjectName("window-close-button")
        self.close_button.setIcon(tool_icon(self.close_button, "exit", 16))
        self.close_button.setToolTip("关闭")
        self.close_button.setAccessibleName("关闭聊天窗口")
        title_layout.addWidget(self.minimize_button)
        # Windows 习惯：最小化 → 最大化/还原 → 关闭。少这一行时按钮是没有布局
        # 的裸子控件，停在标题栏 (0,0)（用户截图里标题左侧那个方块）。
        title_layout.addWidget(self.maximize_button)
        title_layout.addWidget(self.close_button)
        chat_main_layout.addWidget(self.title_bar)

        self.view_stack = QStackedWidget(chat_main)
        self.view_stack.setObjectName("chat-view-stack")
        self.ai_view = QWidget(self.view_stack)
        self.ai_view.setObjectName("chat-ai-view")
        ai_layout = QVBoxLayout(self.ai_view)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(0)
        # 富余高度只给滚动区：spacer 与输入区都在这个布局里，stretch 必须
        # 设到它身上（设到 chat_main_layout 是静默无效的）。
        self.ai_layout = ai_layout
        self.view_stack.addWidget(self.ai_view)
        self.dsh_view = DshView(
            self._dsh_client, self.view_stack, hub=self._dsh_hub
        )
        self.dsh_view.context_ready.connect(self._use_dsh_context)
        self.dsh_view.session_started.connect(self.dsh_session_started)
        self.dsh_view.send_requested.connect(self.dsh_submitted)
        self.view_stack.addWidget(self.dsh_view)
        chat_main_layout.addWidget(self.view_stack, 1)

        self.content_top_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        ai_layout.addSpacerItem(self.content_top_spacer)

        self.scroll = QScrollArea(chat_main)
        self.scroll.setObjectName("message-scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_view = QWidget()
        self.message_view.setObjectName("message-view")
        self.message_stack = QStackedLayout(self.message_view)
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        # The empty prompt lives in a deliberately compact viewport so the
        # prompt and composer can be centred as one unit without creating a
        # meaningless scrollbar before any conversation exists.
        empty_layout.setContentsMargins(60, 20, 60, 20)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state = QLabel("有什么可以帮你的吗？\n和桌宠开始一段新的对话")
        self.empty_state.setObjectName("empty-state")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        empty_layout.addWidget(self.empty_state)
        self.timeline_host = QWidget()
        self.timeline_host.setObjectName("message-timeline")
        self.message_host_layout = QVBoxLayout(self.timeline_host)
        self.message_horizontal_margin = 44
        self.message_host_layout.setContentsMargins(
            self.message_horizontal_margin, 30, self.message_horizontal_margin, 30
        )
        self.message_host_layout.setSpacing(24)
        # 短会话把消息压到输入区上方（微信/DeepSeek 的阅读动线）；长会话时
        # 这个 Expanding 会被内容吃掉，仍从顶部开始滚动。
        self.message_top_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.message_host_layout.addSpacerItem(self.message_top_spacer)
        self.message_bottom_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.message_host_layout.addSpacerItem(self.message_bottom_spacer)
        self.message_stack.addWidget(self.empty_page)
        self.message_stack.addWidget(self.timeline_host)
        self.scroll.setWidget(self.message_view)
        # 用户上翻阅读历史时暂停自动滚底；回到底部或开始新回复时恢复跟随
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        # 内容高度（maximum）变化时补上未完成的贴底
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        self.scroll.setAutoFillBackground(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ai_layout.addWidget(self.scroll)

        self.composer_card = QFrame(chat_main)
        self.composer_card.setObjectName("floating-composer")
        # 输入区只吃自然高度：富余高度归滚动区，否则 toolbar 会吸收空白。
        self.composer_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        composer_card_layout = QVBoxLayout(self.composer_card)
        self.composer_card_layout = composer_card_layout
        composer_card_layout.setContentsMargins(44, 8, 44, 20)
        self.composer = ChatComposer(self.composer_card)
        self.composer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        composer_shadow = QGraphicsDropShadowEffect(self.composer)
        composer_shadow.setBlurRadius(22)
        composer_shadow.setOffset(0, 5)
        composer_shadow.setColor(QColor(32, 42, 61, 35))
        self.composer.setGraphicsEffect(composer_shadow)
        composer_card_layout.addWidget(self.composer)
        self.input = self.composer.input
        self.send_button = self.composer.send
        self.send = self.send_button
        ai_layout.addWidget(self.composer_card)
        self.content_bottom_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        ai_layout.addSpacerItem(self.content_bottom_spacer)

        self.sidebar_scrim = SidebarScrim(self.phone_shell)
        self.sidebar_scrim.setObjectName("sidebar-scrim")
        self.sidebar_scrim.hide()

        self.title = self.title_label
        self._set_empty_state(True)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_stream)

    def _on_view_toggled(self, checked: bool) -> None:
        self.set_view("dsh" if checked else "ai")
        self.view_changed.emit(self.current_view())

    # ---------------------------------------------------------------- 扩展视图
    def view_ids(self) -> list[str]:
        """本窗口可用的扩展视图 id（``<owner>:<contribution id>``）。"""
        return [str(spec.id) for spec in self._view_specs]

    def show_view(self, view_id: str) -> bool:
        """切到扩展视图；未知 id 返回 False 且不改当前页。"""
        target = str(view_id or "")
        if target not in self._view_spec_map:
            return False
        self.set_view(target)
        return True

    def _build_view_buttons(self, layout: QHBoxLayout) -> None:
        if not self._view_specs:
            return
        inline = self._view_specs[:MAX_INLINE_VIEW_BUTTONS]
        extra = self._view_specs[MAX_INLINE_VIEW_BUTTONS:]
        for spec in inline:
            button = self._make_view_button(spec, self.title_bar)
            layout.addWidget(button)
            self._view_buttons[str(spec.id)] = button
        if not extra:
            return
        # 标题栏宽度有限（最小窗宽 560），多出来的视图进「»」菜单。
        self.view_more_button = QToolButton(self.title_bar)
        self.view_more_button.setObjectName("chat-view-more-button")
        self.view_more_button.setIcon(tool_icon(self.view_more_button, "more", 16))
        self.view_more_button.setToolTip("更多视图")
        self.view_more_button.setAccessibleName("更多对话视图")
        menu = QMenu(self.view_more_button)
        self.view_more_menu = menu
        for spec in extra:
            action = menu.addAction(str(spec.label))
            action.triggered.connect(
                lambda _checked=False, view_id=str(spec.id): self._activate_view_button(
                    view_id
                )
            )
        self.view_more_button.setMenu(menu)
        self.view_more_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        layout.addWidget(self.view_more_button)

    def _make_view_button(self, spec, parent) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName("chat-view-button")
        button.setProperty("viewId", str(spec.id))
        button.setCheckable(True)
        button.setText(str(spec.label))
        button.setToolTip(f"{spec.label}（再次点击回到 AI 对话）")
        button.setAccessibleName(f"切换到{spec.label}，再次点击回到 AI 对话")
        icon = str(getattr(spec, "icon", "") or "")
        if icon:
            button.setIcon(tool_icon(button, icon, 16, dark=self._dark))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.clicked.connect(
            lambda _checked=False, view_id=str(spec.id): self._activate_view_button(
                view_id
            )
        )
        return button

    def _activate_view_button(self, view_id: str) -> None:
        """标题栏视图按钮/溢出菜单项的统一入口。

        再点当前视图 = 回到 AI 对话（按钮此时已选中，不处理就成了死点）。
        """
        if view_id == self._current_view:
            self.set_view("ai")
            return
        self.set_view(view_id)

    def _ensure_view_page(self, view_id: str) -> QWidget | None:
        """懒建扩展视图页；工厂抛异常时退化成错误占位，绝不带走对话窗。"""
        page = self._view_pages.get(view_id)
        if page is not None:
            return page
        spec = self._view_spec_map.get(view_id)
        if spec is None:
            return None
        host = QFrame(self.view_stack)
        host.setObjectName("chat-view-host")
        box = QVBoxLayout(host)
        # 内缩：原生子窗口（QWebView / QWindowContainer）不会被圆角 mask 裁掉。
        box.setContentsMargins(CHAT_VIEW_INSET, 0, CHAT_VIEW_INSET, CHAT_VIEW_INSET)
        box.setSpacing(0)
        widget = None
        try:
            widget = spec.factory()
        except Exception:  # noqa: BLE001 - 扩展崩溃隔离在这一个槽位里
            logger.warning("chat view %s factory failed", view_id, exc_info=True)
        if isinstance(widget, QWidget):
            setter = getattr(widget, "set_pet_slot", None)
            if callable(setter):
                try:
                    setter(int(self._pet_slot))
                except Exception:  # noqa: BLE001 - 槽位注入失败不影响视图加载
                    logger.warning(
                        "chat view %s set_pet_slot failed", view_id, exc_info=True
                    )
        if not isinstance(widget, QWidget):
            if widget is not None:
                logger.warning(
                    "chat view %s factory returned %s, expected QWidget",
                    view_id,
                    type(widget).__name__,
                )
            widget = self._view_error_widget(spec)
        box.addWidget(widget)
        self.view_stack.addWidget(host)
        self._view_pages[view_id] = host
        self._view_widgets[view_id] = widget
        self._apply_view_dark(widget)
        return host

    def _view_error_widget(self, spec) -> QWidget:
        label = QLabel(
            f"「{getattr(spec, 'label', '')}」视图加载失败\n可以让扩展作者查看日志。",
            self.view_stack,
        )
        label.setObjectName("chat-view-error")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label

    def _call_view(self, view_id: str, hook: str) -> None:
        widget = self._view_widgets.get(view_id)
        if widget is None:
            return
        callback = getattr(widget, hook, None)
        if not callable(callback):
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - 扩展的生命周期钩子失败只记日志
            logger.warning("chat view %s %s failed", view_id, hook, exc_info=True)

    def _apply_view_dark(self, widget) -> None:
        callback = getattr(widget, "set_dark", None)
        if not callable(callback):
            return
        try:
            callback(bool(self._dark))
        except Exception:  # noqa: BLE001
            logger.warning("chat view set_dark failed", exc_info=True)

    def _deactivate_view(self, view_id: str) -> None:
        if not view_id:
            return
        if view_id == "dsh":
            self.dsh_view.deactivate()
            return
        self._call_view(view_id, "deactivate")

    def _shutdown_view(self, view_id: str) -> None:
        """关窗：让扩展视图停加载并释放页面（网页不该在后台继续跑脚本）。"""
        if not view_id or view_id in ("ai", "dsh"):
            return
        self._call_view(view_id, "shutdown")

    def set_view(self, view: str) -> None:
        requested = str(view or "").strip()
        lowered = requested.lower()
        key = lowered if lowered in ("ai", "dsh") else requested
        page: QWidget | None = None
        if key == "dsh":
            if not self._dsh_enabled:
                # 第一次点 DSH 就等于打开模式：侧栏 DSH 分组随之出现。
                self.set_dsh_enabled(True)
            page = self.dsh_view
        elif key in self._view_spec_map:
            page = self._ensure_view_page(key)
            if page is None:
                key = "ai"
        if page is None:
            key, page = "ai", self.ai_view
        previous = str(self._current_view)
        if previous != key:
            self._deactivate_view(previous)
        self._current_view = key
        self.view_stack.setCurrentWidget(page)
        dsh = key == "dsh"
        self.view_button.blockSignals(True)
        self.view_button.setChecked(dsh)
        self.view_button.blockSignals(False)
        self.view_button.setText("AI" if dsh else "DSH")
        self.view_button.setToolTip("切回 AI 对话" if dsh else "切换到 DSH 对话")
        if dsh:
            self.dsh_view.activate()
        elif key == "ai":
            self.dsh_view.deactivate()
        if key in self._view_widgets:
            self._call_view(key, "activate")
        self._sync_view_buttons()
        self._sync_view_vs_drawer()
        if previous != key:
            self.view_activated.emit(key)

    def _sync_view_buttons(self) -> None:
        for view_id, button in self._view_buttons.items():
            button.blockSignals(True)
            button.setChecked(view_id == self._current_view)
            button.blockSignals(False)

    def _sync_view_vs_drawer(self) -> None:
        """紧凑宽度下侧栏是覆盖层：原生子窗口盖在抽屉上面（airspace），临时隐藏。"""
        if not self._view_pages:
            return
        current = self.view_stack.currentWidget()
        hidden = bool(self._compact_layout and self.sidebar.isVisible())
        for page in self._view_pages.values():
            page.setVisible(bool(page is current) and not hidden)

    def dsh_enabled(self) -> bool:
        return bool(self._dsh_enabled)

    def set_dsh_enabled(self, enabled: bool) -> None:
        """DSH 模式开关：侧栏分组可见性 + 共享 SSE 的常驻订阅。"""
        enabled = bool(enabled)
        if enabled == self._dsh_enabled and self._dsh_hub is None:
            return
        self._dsh_enabled = enabled
        hub = self._dsh_hub
        if hub is not None:
            # 常驻订阅：用户停在 AI 视图也要收得到审批（否则卡片直接过期）。
            if enabled:
                hub.acquire_persistent()
            else:
                hub.release_persistent()
        self._apply_collapsed_groups()
        self._restore_active_row()

    def current_view(self) -> str:
        return str(self._current_view)

    def _use_dsh_context(self, text: str) -> None:
        self.set_view("ai")
        self.composer.insert_text(str(text or ""))

    def _connect(self) -> None:
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button.clicked.connect(self.close)
        self.pin_button.clicked.connect(self.toggle_always_on_top)
        self.dark_button.toggled.connect(self._on_dark_toggled)
        self.new_session_button.clicked.connect(self._on_new)
        self.archive_button.toggled.connect(self._on_archived_toggled)
        self.clear_button.clicked.connect(self._on_clear)
        self.sidebar_toggle_button.clicked.connect(self.toggle_sidebar)
        self.sidebar_scrim.clicked.connect(self.close_sidebar)
        self.session_list.currentRowChanged.connect(self._on_session_row_changed)
        self.session_list.itemClicked.connect(self._on_session_item_clicked)
        self.composer.send_requested.connect(self._on_send_requested)
        self.composer.record_requested.connect(self.record_requested)
        self.composer.record_stopped.connect(self.record_stopped)

    # ---------------------------------------------------------------- 侧栏
    def is_showing_archived(self) -> bool:
        return bool(self._show_archived)

    def _on_dark_toggled(self, checked: bool) -> None:
        self.set_dark(bool(checked))
        self.dark_toggled.emit(bool(checked))

    def _style_group_header(self, item: QListWidgetItem, *, channel: bool) -> None:
        """组头字体分层：渠道组头比会话标题小一号，日期组头再小一号且不加粗。

        以前组头吃列表默认字体，和会话标题一模一样，用户分不清「哪一行是可以
        点的会话」。
        """
        base = int(self._font_size or 13)
        font = QFont(self.font())
        font.setPixelSize(max(9, base - 2 if channel else base - 3))
        font.setWeight(QFont.Weight.DemiBold if channel else QFont.Weight.Normal)
        item.setFont(font)
        if self._dark:
            item.setForeground(QColor("#c8cfda" if channel else "#8b93a1"))
        else:
            item.setForeground(QColor("#5a6472" if channel else "#98a1ad"))

    def set_history(self, sessions, current_id: str = "") -> None:
        sessions = list(sessions or [])
        self._session_titles = {}
        self._row_by_session = {}
        self._group_rows = {}
        self._group_headers = {}
        self._group_labels = {}
        self._channel_rows = {}
        self._channel_headers = {}
        self._channel_labels = {}
        self.session_list.blockSignals(True)
        self.session_list.clear()
        current_id = str(current_id or "")
        by_channel: dict[str, list] = {}
        for session in sessions:
            channel = str(getattr(session, "channel", "ai") or "ai")
            by_channel.setdefault(channel, []).append(session)
        order = [("ai", "AI 对话"), ("dsh", "DSH 对话"), ("qq", "QQ 消息")]
        known = {key for key, _label in order}
        extra = [(key, key.upper()) for key in by_channel if key not in known]
        for channel, label in order + extra:
            channel_sessions = by_channel.get(channel, [])
            if not channel_sessions:
                continue
            channel_key = f"channel:{channel}"
            header = QListWidgetItem()
            header.setData(GROUP_ROLE, channel_key)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header.setSizeHint(QSize(1, 30))
            self.session_list.addItem(header)
            self._style_group_header(header, channel=True)
            self._channel_headers[channel_key] = header
            self._channel_labels[channel_key] = label
            channel_rows: list[int] = []
            if any(
                str(getattr(session, "session_id", "") or "") == current_id
                for session in channel_sessions
            ):
                self._collapsed_groups.discard(channel_key)
            for bucket in buckets(channel_sessions):
                bucket_key = f"{channel}:{bucket.key}"
                if any(
                    str(getattr(session, "session_id", "") or "") == current_id
                    for session in bucket.sessions
                ):
                    self._collapsed_groups.discard(bucket.key)
                date_header = QListWidgetItem()
                date_header.setData(GROUP_ROLE, bucket_key)
                date_header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                date_header.setSizeHint(QSize(1, 30))
                self.session_list.addItem(date_header)
                self._style_group_header(date_header, channel=False)
                self._group_headers[bucket_key] = date_header
                self._group_labels[bucket_key] = bucket.label
                channel_rows.append(self.session_list.row(date_header))
                for session in bucket.sessions:
                    session_id = str(getattr(session, "session_id", "") or "")
                    title = str(getattr(session, "title", "") or "新会话")
                    archived = bool(float(getattr(session, "archived_at", 0.0) or 0.0))
                    self._session_titles[session_id] = title
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, session_id)
                    item.setToolTip(title + ("（已归档）" if archived else ""))
                    item.setSizeHint(QSize(1, 36))
                    self.session_list.addItem(item)
                    row = self.session_list.row(item)
                    self._row_by_session[session_id] = row
                    self._group_rows.setdefault(bucket_key, []).append(row)
                    channel_rows.append(row)
                    row_widget = SessionListRow(
                        session_id, title, self.session_list, archived=archived
                    )
                    row_widget.archive_requested.connect(self.session_archive_requested)
                    row_widget.delete_requested.connect(self.session_delete_requested)
                    self.session_list.setItemWidget(item, row_widget)
            self._channel_rows[channel_key] = channel_rows
        self._apply_collapsed_groups()

        selected = self._row_by_session.get(current_id, -1)
        if selected < 0 or self.session_list.isRowHidden(selected):
            visible = [
                row
                for row in self._row_by_session.values()
                if not self.session_list.isRowHidden(row)
            ]
            selected = visible[0] if visible else -1
        if selected >= 0:
            self.session_list.setCurrentRow(selected)
        self.session_list.blockSignals(False)
        self._active_session_id = current_id
        if not self._active_session_id and selected >= 0:
            item = self.session_list.item(selected)
            if item is not None:
                self._active_session_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.title_label.setText(self._session_titles.get(self._active_session_id) or "新对话")
        self.archive_button.blockSignals(True)
        self.archive_button.setChecked(self._show_archived)
        self.archive_button.blockSignals(False)

    def _apply_collapsed_groups(self) -> None:
        for key, header in self._channel_headers.items():
            # DSH 分组只在「DSH 模式」打开时出现：没开 DSH 的人不该看到
            # 一堆与自己无关的 DSH 会话（用户明确要求的默认）。
            hidden_channel = key == "channel:dsh" and not self._dsh_enabled
            header.setHidden(hidden_channel)
            if hidden_channel:
                for row in self._channel_rows.get(key, ()):
                    self.session_list.setRowHidden(row, True)
                continue
            collapsed = key in self._collapsed_groups
            label = self._channel_labels.get(key, key)
            header.setText(f"{'▸' if collapsed else '▾'} {label}")
            for row in self._channel_rows.get(key, ()):
                self.session_list.setRowHidden(row, collapsed)
        for key, header in self._group_headers.items():
            channel_key = f"channel:{str(key).split(':', 1)[0]}"
            if channel_key in self._collapsed_groups:
                continue
            if channel_key == "channel:dsh" and not self._dsh_enabled:
                header.setHidden(True)
                for row in self._group_rows.get(key, ()):
                    self.session_list.setRowHidden(row, True)
                continue
            header.setHidden(False)
            collapsed = key.rsplit(":", 1)[-1] in self._collapsed_groups
            label = self._group_labels.get(key, key)
            header.setText(f"{'▸' if collapsed else '▾'} {label}")
            for row in self._group_rows.get(key, ()):
                self.session_list.setRowHidden(row, collapsed)

    def _toggle_group(self, key: str) -> None:
        target = key if str(key).startswith("channel:") else str(key).rsplit(":", 1)[-1]
        if target in self._collapsed_groups:
            self._collapsed_groups.discard(target)
        else:
            self._collapsed_groups.add(target)
        self._apply_collapsed_groups()
        self._restore_active_row()

    def _restore_active_row(self) -> None:
        row = self._row_by_session.get(self._active_session_id, -1)
        if row < 0 or self.session_list.isRowHidden(row):
            return
        self.session_list.blockSignals(True)
        self.session_list.setCurrentRow(row)
        self.session_list.blockSignals(False)

    def _on_archived_toggled(self, checked: bool) -> None:
        self._show_archived = bool(checked)
        self.archived_toggled.emit(self._show_archived)

    def toggle_sidebar(self) -> None:
        if self._compact_layout:
            if self.sidebar.isVisible():
                self.close_sidebar()
            else:
                self._show_overlay_sidebar()
            return
        self._sidebar_open = not self.sidebar.isVisible()
        self.sidebar.setVisible(self._sidebar_open)
        self._sync_view_vs_drawer()

    def close_sidebar(self) -> None:
        if not self._compact_layout:
            return
        self.sidebar.hide()
        self.sidebar_scrim.hide()
        self._sync_view_vs_drawer()

    def _show_overlay_sidebar(self) -> None:
        if not self._compact_layout:
            return
        self._position_overlay_sidebar()
        self.sidebar_scrim.show()
        self.sidebar_scrim.raise_()
        self.sidebar.show()
        self.sidebar.raise_()
        self._sync_view_vs_drawer()

    def _position_overlay_sidebar(self) -> None:
        rect = self.phone_shell.rect()
        drawer_width = min(286, max(220, int(rect.width() * 0.76)))
        self.sidebar.setFixedWidth(drawer_width)
        self.sidebar.setGeometry(0, 0, drawer_width, rect.height())
        actual_width = self.sidebar.width()
        self.sidebar_scrim.setGeometry(
            actual_width, 0, max(0, rect.width() - actual_width), rect.height()
        )

    def _update_responsive_layout(self) -> None:
        compact = self.width() < COMPACT_WIDTH
        self.setProperty("compactLayout", compact)
        if compact != self._compact_layout:
            self._compact_layout = compact
            if compact:
                self.root_layout.removeWidget(self.sidebar)
                self.sidebar.setParent(self.phone_shell)
                self.sidebar.setProperty("overlayDrawer", True)
                self.sidebar.hide()
                self.sidebar_scrim.hide()
            else:
                self.sidebar_scrim.hide()
                self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
                self.root_layout.insertWidget(0, self.sidebar)
                self.sidebar.setProperty("overlayDrawer", False)
                self.sidebar.show()
                self._sidebar_open = True
            self.sidebar.style().unpolish(self.sidebar)
            self.sidebar.style().polish(self.sidebar)
        if compact and self.sidebar.isVisible():
            self._position_overlay_sidebar()
        self._apply_view_button_layout()
        self._sync_view_vs_drawer()

    def _apply_view_button_layout(self) -> None:
        """紧凑宽度只留图标：标题栏按钮 + 标题在 560px 里放不下文字版。"""
        for view_id, button in self._view_buttons.items():
            spec = self._view_spec_map.get(view_id)
            has_icon = bool(str(getattr(spec, "icon", "") or ""))
            if self._compact_layout and has_icon:
                button.setText("")
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            elif has_icon:
                button.setText(str(getattr(spec, "label", "")))
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                )
            else:
                button.setText(str(getattr(spec, "label", "")))
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

    def _on_new(self) -> None:
        self.session_selected.emit("")

    def _on_clear(self) -> None:
        self.clear()

    def _on_session_row_changed(self, index: int) -> None:
        item = self.session_list.item(index)
        if item is None:
            return
        if item.data(GROUP_ROLE):
            # 组头不是会话：把选中态还回当前会话，点击由 itemClicked 负责折叠。
            self._restore_active_row()
            return
        self._emit_session_selection(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _on_session_item_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        group_key = item.data(GROUP_ROLE)
        if group_key:
            self._toggle_group(str(group_key))
            return
        self._emit_session_selection(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _emit_session_selection(self, session_id: str) -> None:
        if not session_id or session_id == self._active_session_id:
            return
        self._active_session_id = session_id
        self.title_label.setText(self._session_titles.get(session_id) or "新对话")
        self.session_selected.emit(session_id)

    # ---------------------------------------------------------------- 消息渲染
    def load_messages(self, messages) -> None:
        self._flush_timer.stop()
        self._messages = [dict(item) for item in (messages or [])]
        self._stream_text = ""
        self._streaming = False
        self._stream_bubble = None
        self._last_thinking = ("", [])
        self._clear_message_rows()
        for item in self._messages:
            bubble = self._add_bubble(
                str(item.get("role") or "user"), str(item.get("content") or "")
            )
            reasoning = str(item.get("reasoning") or "")
            activity = item.get("activity") or []
            if reasoning or activity:
                bubble.restore_thinking(reasoning, list(activity))
        self._animate_latest_images()
        self._set_empty_state(not self._messages)
        self._set_busy(False)
        self._bottom()

    def append_message(self, role: str, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        role = str(role or "user")
        self._messages.append({"role": role, "content": text})
        self._add_bubble(role, text)
        self._animate_latest_images()
        self._bottom(always=role == "user")

    def begin_stream(self) -> None:
        self._flush_timer.stop()
        self._stream_text = ""
        self._messages.append({"role": "assistant", "content": ""})
        self._stream_bubble = self._add_bubble("assistant", "")
        self._stream_bubble.set_state(MessageBubble.STATE_STREAMING)
        self._set_busy(True)
        self._bottom()

    def append_token(self, delta: str) -> None:
        delta = str(delta or "")
        if not self._streaming and self._stream_bubble is None:
            self.begin_stream()
        self._stream_text += delta
        if self._messages and self._messages[-1].get("role") == "assistant":
            self._messages[-1]["content"] = self._stream_text
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def append_reasoning(self, delta: str) -> None:
        """思维链增量：只进助手气泡的折叠面板，不拼进正文。"""
        delta = str(delta or "")
        if not delta:
            return
        if self._stream_bubble is None:
            self.begin_stream()
        if self._stream_bubble is not None:
            self._stream_bubble.append_reasoning(delta)
        self._bottom()

    def add_activity(self, text: str) -> None:
        """联网检索等进度行：进助手气泡的活动面板。"""
        text = str(text or "").strip()
        if not text:
            return
        if self._stream_bubble is None:
            self.begin_stream()
        if self._stream_bubble is not None:
            self._stream_bubble.add_activity(text)
        self._bottom(always=True)

    def last_thinking(self) -> tuple[str, list[str]]:
        """最近一次流式回复的 (思维链, 检索进度) 副本（落会话用）。"""
        reasoning, activity = self._last_thinking
        return reasoning, list(activity)

    def _flush_stream(self) -> None:
        if self._stream_bubble is None:
            return
        self._stream_bubble.set_content(self._stream_text)
        self._stream_bubble.set_state(MessageBubble.STATE_STREAMING)
        self._update_conversation_height()
        if self._stream_follow_output:
            self._bottom()

    def finish_stream(self, text: str = "") -> None:
        self._flush_timer.stop()
        final = str(text) if text else self._stream_text
        if self._stream_bubble is None:
            if not final:
                self._drop_empty_assistant()
                self._set_busy(False)
                return
            self.begin_stream()
        self._stream_text = final
        if self._messages and self._messages[-1].get("role") == "assistant":
            self._messages[-1]["content"] = final
        bubble = self._stream_bubble
        self._stream_bubble = None
        if bubble is not None:
            self._last_thinking = bubble.thinking_record
            bubble.finish_thinking()
            bubble.set_content(final)
            bubble.set_state(MessageBubble.STATE_NORMAL)
        self._animate_latest_images()
        self._set_busy(False)
        self._bottom()

    def show_error(self, reason: str, message: str = "") -> None:
        detail = str(message or reason or "未知错误")
        self._last_error = f"{reason}: {message}" if message else str(reason)
        self._flush_timer.stop()
        bubble = self._stream_bubble
        self._stream_bubble = None
        if bubble is None:
            bubble = self._add_bubble("assistant", "")
        bubble.finish_thinking()
        bubble.set_content(f"请求失败：{detail}", markdown=False)
        bubble.set_state(MessageBubble.STATE_ERROR)
        self._drop_empty_assistant()
        self._stream_text = ""
        self._set_busy(False)
        self._set_status(f"出错：{detail}", state="error")
        self._bottom()

    def clear(self) -> None:
        self._flush_timer.stop()
        self._messages = []
        self._stream_text = ""
        self._stream_bubble = None
        self._last_thinking = ("", [])
        self._pending_attachments = []
        self._clear_message_rows()
        self._set_empty_state(True)
        self._set_busy(False)
        self.cleared.emit()

    def _drop_empty_assistant(self) -> None:
        if (
            self._messages
            and self._messages[-1].get("role") == "assistant"
            and not self._messages[-1].get("content")
        ):
            self._messages.pop()

    def _set_busy(self, busy: bool) -> None:
        self._streaming = bool(busy)
        self.composer.set_busy(busy)
        self._set_status(
            "正在生成…" if busy else "就绪",
            state="busy" if busy else "ready",
        )

    def _set_status(self, text: str, *, state: str = "ready") -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_dot.setProperty("state", "busy" if state == "busy" else "ready")
        for widget in (self.status_label, self.status_dot):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # ---------------------------------------------------------------- 发送
    def _on_send_requested(self) -> None:
        if self._streaming:
            self.stop_requested.emit()
            return
        text = self.input.toPlainText().strip()
        attachments = self.composer.attachment_payloads()
        if not text and not attachments:
            return
        payload = text or "请查看附件"
        self._pending_attachments = attachments
        self.input.clear()
        self.composer.clear_attachments()
        self.submitted.emit(payload)

    # ---------------------------------------------------------------- 行布局
    def _add_bubble(self, role: str, text: str) -> MessageBubble:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubble(role, text, self.character_id, self.timeline_host)
        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        self.message_host_layout.insertLayout(self.message_host_layout.count() - 1, row)
        self._bubbles.append(bubble)
        self._set_empty_state(False)
        self._update_bubble_widths()
        QTimer.singleShot(0, self, self._update_conversation_height)
        self._bottom(always=role == "user")
        return bubble

    def _animate_latest_images(self) -> None:
        """只有最后一条助手消息的图起播 GIF；更早的消息画首帧。"""
        latest = None
        for bubble in self._bubbles:
            if bubble.role == "assistant":
                latest = bubble
        for bubble in self._bubbles:
            if bubble.has_inline_images:
                bubble.set_animate_images(bubble is latest)

    def _clear_message_rows(self) -> None:
        # 首尾两个 spacer 保留：顶部负责短会话底部对齐，底部固定 0。
        while self.message_host_layout.count() > 2:
            item = self.message_host_layout.takeAt(1)
            self._delete_layout_item(item)
        self._bubbles.clear()
        self._stream_bubble = None

    @staticmethod
    def _delete_layout_item(item) -> None:
        if item is None:
            return
        layout = item.layout()
        if layout is not None:
            ChatWindow._delete_layout(layout)
            return
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()

    @staticmethod
    def _delete_layout(layout) -> None:
        while layout.count():
            ChatWindow._delete_layout_item(layout.takeAt(0))

    def _set_empty_state(self, empty: bool) -> None:
        self.message_stack.setCurrentWidget(self.empty_page if empty else self.timeline_host)
        self.empty_state.setVisible(empty)
        QTimer.singleShot(0, self, self._update_conversation_height)

    def _update_conversation_height(self) -> None:
        """Center an empty composer; otherwise pin it below a top-down timeline."""
        empty = self.message_stack.currentWidget() is self.empty_page
        if empty:
            self.content_top_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
            self.content_bottom_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
            self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.scroll.setFixedHeight(150)
            self.ai_layout.setStretchFactor(self.scroll, 0)
        else:
            self.content_top_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            self.content_bottom_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            self.scroll.setMinimumHeight(0)
            self.scroll.setMaximumHeight(16777215)
            self.scroll.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self.ai_layout.setStretchFactor(self.scroll, 1)
            self.message_top_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
            self.message_bottom_spacer.changeSize(
                0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
        self.ai_layout.invalidate()
        self.chat_main_layout.invalidate()
        self.message_host_layout.invalidate()

    def _update_bubble_widths(self) -> None:
        available = max(
            320,
            self.scroll.viewport().width() - 2 * self.message_horizontal_margin,
        )
        user_cap = max(240, int(available * 0.86))
        for bubble in self._bubbles:
            if bubble.role == "assistant":
                bubble.setFixedWidth(available)
            else:
                bubble.setFixedWidth(bubble.preferred_width(user_cap))

    # ---------------------------------------------------------------- 滚动
    def _is_near_bottom(self, threshold: int = 24) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - threshold

    def _on_scroll_value_changed(self, _value: int) -> None:
        """用户滚动位置决定是否继续跟随输出；上翻阅读时暂停自动滚底。

        程序自己贴底（``_auto_scrolling`` 置位）不改写跟随状态：贴底动作本身
        也发 valueChanged，若在这里当作用户意图，就会把「跟随」误判成 False。
        """
        if self._auto_scrolling:
            return
        following = self._is_near_bottom()
        self._stream_follow_output = following
        if not following:
            # 用户主动上翻 = 明确的阅读意图：撤掉未完成的贴底欠账
            self._scroll_pin = False

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        """内容高度变化后补上未完成的贴底（布局晚于写入的最大值在此追平）。"""
        if self._scroll_pin and self._stream_follow_output:
            self._apply_bottom()

    def _bottom(self, *, always: bool = False) -> None:
        """把会话钉到最新内容底部，并在布局随后长高时继续跟到底。"""
        if always:
            self._stream_follow_output = True
        self._scroll_pin = True
        self._apply_bottom()
        QTimer.singleShot(0, self, self._apply_bottom)
        # 布局更新可能晚于 singleShot(0)（内容高度尚未重算，maximum 仍为 0）；
        # 再加一拍兜底，保证长会话落在底部。
        QTimer.singleShot(80, self, self._apply_bottom)

    def _apply_bottom(self) -> None:
        if self._scroll_pin and not self._stream_follow_output:
            return
        bar = self.scroll.verticalScrollBar()
        self._auto_scrolling = True
        try:
            bar.setValue(bar.maximum())
        finally:
            self._auto_scrolling = False
        # 真的贴到底了才销账：maximum 还没追上内容高度时留着欠账
        if bar.value() >= bar.maximum():
            self._scroll_pin = False

    # ---------------------------------------------------------------- 窗口行为
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "composer_card_layout"):
            return
        self._update_responsive_layout()
        margin = 12 if self._compact_layout else 44
        self.composer_card_layout.setContentsMargins(
            margin, 7, margin, 14 if self._compact_layout else 20
        )
        self.message_horizontal_margin = 12 if self._compact_layout else 44
        self.message_host_layout.setContentsMargins(
            self.message_horizontal_margin, 24, self.message_horizontal_margin, 24
        )
        self._update_bubble_widths()
        self._apply_shell_mask()
        QTimer.singleShot(0, self, self._update_conversation_height)
        # 改尺寸会让所有气泡重新折行，内容高度随之变化；仍在跟随就重新贴底。
        if self._stream_follow_output:
            self._bottom()

    _EDGE_GRIP = 8

    def _edge_at(self, pos) -> set[str]:
        edges: set[str] = set()
        if self.isMaximized():
            # 最大化时窗口边缘就是屏幕边缘：这里若还能起拖，setGeometry 会把
            # 最大化状态静默取消（实测）。
            return edges
        x, y = pos.x(), pos.y()
        if x < self._EDGE_GRIP:
            edges.add("left")
        elif x > self.width() - self._EDGE_GRIP:
            edges.add("right")
        if y < self._EDGE_GRIP:
            edges.add("top")
        elif y > self.height() - self._EDGE_GRIP:
            edges.add("bottom")
        return edges

    def _edge_cursor(self, edges: set[str]) -> Qt.CursorShape:
        if edges == {"left", "top"} or edges == {"right", "bottom"}:
            return Qt.CursorShape.SizeFDiagCursor
        if edges == {"right", "top"} or edges == {"left", "bottom"}:
            return Qt.CursorShape.SizeBDiagCursor
        if "left" in edges or "right" in edges:
            return Qt.CursorShape.SizeHorCursor
        if "top" in edges or "bottom" in edges:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def event(self, event) -> bool:  # noqa: N802 - Qt API
        # Hover 事件（WA_Hover）会持续投递到本窗口，即使光标位于未启用鼠标
        # 跟踪的子控件上方；用它刷新边缘缩放光标，避免进入窗口后光标卡死。
        if event.type() == QEvent.Type.HoverMove:
            self.setCursor(self._edge_cursor(self._edge_at(event.position())))
        elif event.type() == QEvent.Type.HoverLeave:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(event.position())
            if edges:
                self._resize_edges = edges
                self._resize_global_start = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                self.setCursor(self._edge_cursor(edges))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._resize_global_start is not None and self._resize_start_geometry is not None:
            start = self._resize_start_geometry
            delta = event.globalPosition().toPoint() - self._resize_global_start
            x, y, w, h = start.x(), start.y(), start.width(), start.height()
            if "left" in self._resize_edges:
                x = start.x() + delta.x()
                w = start.width() - delta.x()
            if "right" in self._resize_edges:
                w = start.width() + delta.x()
            if "top" in self._resize_edges:
                y = start.y() + delta.y()
                h = start.height() - delta.y()
            if "bottom" in self._resize_edges:
                h = start.height() + delta.y()
            w = min(max(w, self.minimumWidth()), self.maximumWidth())
            h = min(max(h, self.minimumHeight()), self.maximumHeight())
            # 尺寸被 clamp 时位置必须随尺寸回退：左/上边缘止步于对侧边缘，
            # 否则窗口会被推出屏幕（如左边缘右拖越过最小宽度时整体右移）。
            if "left" in self._resize_edges:
                x = start.right() - w
            if "top" in self._resize_edges:
                y = start.bottom() - h
            self.setGeometry(QRect(x, y, w, h))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._resize_edges = set()
        self._resize_global_start = None
        self._resize_start_geometry = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self._update_bubble_widths)
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        # 显示前先落位：跟随开启时窗口直接在桌宠旁边出现，不在旧位置闪一下。
        if self._follow_pet and not event.spontaneous():
            self.position_near_pet()
        super().showEvent(event)
        # 窗口是复用的（关闭=隐藏）：每次打开都回到最新一条回复，
        # 而不是停在上次关闭时的位置。
        if not event.spontaneous():
            self._bottom(always=True)
            self._sync_maximized()
            if self.current_view() == "dsh":
                self.dsh_view.activate()
            elif self.current_view() in self._view_widgets:
                self._call_view(self.current_view(), "activate")

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_maximized()
        elif event.type() == QEvent.Type.StyleChange:
            self._apply_shell_mask()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """关闭=隐藏并复用窗口：收尾流式状态，保留会话供下次打开。"""
        self._flush_timer.stop()
        self._follow_timer.stop()
        self.dsh_view.deactivate()
        # 扩展视图也要停：网页不再加载/渲染，隐藏的窗口不该有后台开销。
        current = self.current_view()
        self._deactivate_view(current)
        self._shutdown_view(current)
        self.closed.emit()
        self.hide()
        event.ignore()
