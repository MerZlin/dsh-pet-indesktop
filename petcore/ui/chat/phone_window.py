"""类手机对话窗：单栏消息、顶部返回/更多/关闭、底部视图 chips 与新建会话。

不复制控制器与视图栈：继承 :class:`ChatWindow`，只重排窗口骨架。会话列表仍是
现有的 sidebar（窄宽度下自动变成抽屉），删除/归档/回收站全部沿用同一套信号。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from .contrast import ensure_contrast, readable_ink
from .icons import tool_icon
from .window import ChatWindow


class PhoneChatWindow(ChatWindow):
    """窄屏单栏布局；所有功能仍由 ChatController 的既有信号驱动。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.setMinimumSize(440, 560)
        self.setMaximumSize(560, 1000)
        # 初始比旧版 380 宽 80px：扣掉右侧 12px padding 后正文仍多约 5 个汉字。
        self.resize(460, 780)
        self._install_phone_chrome()
        self._update_responsive_layout()

    # ------------------------------------------------------------ 骨架
    def _install_phone_chrome(self) -> None:
        if getattr(self, "_phone_installed", False):
            return
        self._phone_installed = True
        self.setProperty("phoneLayout", True)
        self.title_bar.layout().setContentsMargins(8, 8, 8, 8)
        empty_layout = self.empty_page.layout()
        if empty_layout is not None:
            empty_layout.setContentsMargins(24, 20, 24, 20)

        for name in (
            "settings_button",
            "layout_button",
            "sidebar_toggle_button",
            "view_button",
            "dark_button",
            "pin_button",
            "maximize_button",
            "minimize_button",
            "close_button",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.hide()
        for button in list(getattr(self, "_view_buttons", {}).values()):
            button.hide()
        more = getattr(self, "view_more_button", None)
        if more is not None:
            more.hide()

        layout = self.title_bar.layout()
        self.phone_back_button = QToolButton(self.title_bar)
        self.phone_back_button.setObjectName("phone-back-button")
        self.phone_back_button.setIcon(
            tool_icon(self.phone_back_button, "sidebar", 18)
        )
        self.phone_back_button.setToolTip("打开会话列表")
        self.phone_back_button.setAccessibleName("打开会话列表")
        self.phone_back_button.clicked.connect(self.toggle_sidebar)
        layout.insertWidget(0, self.phone_back_button)

        self.phone_more_button = QToolButton(self.title_bar)
        self.phone_more_button.setObjectName("phone-more-button")
        self.phone_more_button.setIcon(tool_icon(self.phone_more_button, "more", 18))
        self.phone_more_button.setToolTip("更多")
        self.phone_more_button.setAccessibleName("更多对话窗操作")
        self.phone_more_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._phone_menu = QMenu(self.phone_more_button)
        self._phone_menu.aboutToShow.connect(self._build_phone_menu)
        self.phone_more_button.setMenu(self._phone_menu)
        layout.addWidget(self.phone_more_button)

        self.phone_close_button = QToolButton(self.title_bar)
        self.phone_close_button.setObjectName("phone-close-button")
        self.phone_close_button.setIcon(tool_icon(self.phone_close_button, "exit", 18))
        self.phone_close_button.setToolTip("关闭聊天窗口")
        self.phone_close_button.setAccessibleName("关闭聊天窗口")
        self.phone_close_button.clicked.connect(lambda _checked=False: self.close())
        layout.addWidget(self.phone_close_button)

        self._build_phone_view_bar()
        self._apply_icon_theme()

    def _build_phone_view_bar(self) -> None:
        self.phone_view_bar = QWidget(self.chat_main)
        self.phone_view_bar.setObjectName("phone-view-bar")
        row = QHBoxLayout(self.phone_view_bar)
        row.setContentsMargins(12, 0, 12, 6)
        row.setSpacing(6)
        self._phone_chip_buttons: dict[str, QToolButton] = {}

        def add(key: str, label: str) -> None:
            button = QToolButton(self.phone_view_bar)
            button.setObjectName("phone-view-chip")
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, view=key: self._phone_select_view(view)
            )
            row.addWidget(button)
            self._phone_chip_buttons[key] = button

        add("ai", "对话")
        # DSH 视图即使当前关闭也给出入口：点一下会打开 chat_dsh_mode。
        add("dsh", "DSH")
        for spec in self._view_specs:
            key = str(getattr(spec, "id", "") or "")
            if not key:
                continue
            label = str(
                getattr(spec, "label", "")
                or getattr(spec, "title", "")
                or key.split(":")[-1]
            )
            add(key, label[:12])
        row.addStretch(1)
        # 新建会话靠右常驻：手机上单手可及的操作区在底部 chips 行，不在标题栏。
        self.phone_new_button = QToolButton(self.phone_view_bar)
        self.phone_new_button.setObjectName("phone-new-button")
        self.phone_new_button.setIcon(tool_icon(self.phone_new_button, "add", 18))
        self.phone_new_button.setToolTip("新建会话")
        self.phone_new_button.setAccessibleName("新建会话")
        self.phone_new_button.clicked.connect(lambda _checked=False: self._on_new())
        row.addWidget(self.phone_new_button)
        # 常驻在 view_stack 下方：DSH / 记忆 / 网页 AI 页也要能一键回到对话并换模式。
        # 不要 hide 整条 bar：新建会话按钮是它的子控件，会被一起藏掉。
        self.chat_main_layout.addWidget(self.phone_view_bar)
        self.view_activated.connect(lambda _view: self._sync_phone_chips())
        self._sync_phone_chips()

    # ------------------------------------------------------------ 交互
    def _phone_select_view(self, key: str) -> None:
        self.set_view(key)
        if key in ("ai", "dsh"):
            # 与标题栏 DSH 按钮同一条语义：切视图同时切会话通道。
            self.view_changed.emit(key)
        self._sync_phone_chips()

    def _sync_phone_chips(self) -> None:
        current = self.current_view()
        accent = ensure_contrast(str(self.accent_color or "#326de6"), "#ffffff", 4.5)
        ink = readable_ink(accent, light="#ffffff", dark="#16181d")
        for key, button in getattr(self, "_phone_chip_buttons", {}).items():
            button.blockSignals(True)
            button.setChecked(key == current)
            button.blockSignals(False)
            # 强调色是用户可配置的：白字压在浅色 accent 上会看不见。
            button.setStyleSheet(
                "QToolButton#phone-view-chip:checked{"
                f"background:{accent};color:{ink};"
                "}"
            )

    def _build_phone_menu(self) -> None:
        menu = self._phone_menu
        menu.clear()
        menu.addAction("设置", lambda: self.settings_requested.emit())
        menu.addAction("新建会话", lambda: self._on_new())
        menu.addAction("回到对话", lambda: self._phone_select_view("ai"))
        menu.addAction("桌面版", lambda: self.layout_requested.emit("desktop"))
        pin = menu.addAction("置顶")
        pin.setCheckable(True)
        pin.setChecked(self.is_always_on_top())
        pin.triggered.connect(lambda _checked=False: self.pin_button.click())
        dark = menu.addAction("深色模式")
        dark.setCheckable(True)
        dark.setChecked(bool(self._dark))
        dark.triggered.connect(lambda _checked=False: self.dark_button.click())
        menu.addSeparator()
        menu.addAction("回收站", lambda: self.trash_requested.emit())
        menu.addAction("最小化", self.showMinimized)
        menu.addAction(
            "还原" if self.isMaximized() else "最大化", self.toggle_maximized
        )
        menu.addSeparator()
        menu.addAction("关闭", self.close)

    # ------------------------------------------------------------ 主题
    def _apply_icon_theme(self) -> None:
        super()._apply_icon_theme()
        dark = bool(self._dark)
        for name, icon in (
            ("phone_back_button", "sidebar"),
            ("phone_new_button", "add"),
            ("phone_more_button", "more"),
            ("phone_close_button", "exit"),
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.setIcon(tool_icon(button, icon, 18, dark=dark))
        if getattr(self, "_phone_chip_buttons", None):
            self._sync_phone_chips()
