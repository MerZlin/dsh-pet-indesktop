"""浏览器窗口：内嵌 Chromium + 白名单闸门（受信 UI，同 `fun.divination/panel.py` 先例）。

契约注意：本模块**不在** `runtime.py` 里导入，只有确认宿主有内嵌内核时
才由运行时惰性导入；模块内不 import `petcore.ui.*`（文档 §1.9 只允许设置页用），
外观直接读控件调色板生成一份极简 QSS。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import external
from . import sites as sites_mod

log = logging.getLogger(sites_mod.OWNER)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
DEFAULT_SIZE = (1180, 820)
MIN_SIZE = (820, 560)
GEOMETRY_FILE = "window.json"

_PROFILE: QWebEngineProfile | None = None
_WINDOW: "BrowserWindow | None" = None


def _chrome_stylesheet(window: QWidget) -> str:
    palette = window.palette()
    window_bg = palette.window().color().name()
    base = palette.base().color().name()
    text = palette.windowText().color().name()
    muted = palette.placeholderText().color().name()
    highlight = palette.highlight().color().name()
    return (
        "QToolButton, QPushButton {"
        f" color: {text}; background: transparent; border: 1px solid transparent;"
        " border-radius: 6px; padding: 3px 8px; }"
        f"QToolButton:hover, QPushButton:hover {{ background: {base}; border-color: {muted}; }}"
        f"QToolButton:pressed, QPushButton:pressed {{ background: {base}; }}"
        "QLineEdit {"
        f" color: {text}; background: {base}; border: 1px solid {muted};"
        " border-radius: 6px; padding: 4px 8px; }"
        f"QLineEdit:focus {{ border-color: {highlight}; }}"
        f"QWidget#browserChrome {{ background: {window_bg}; }}"
        f"QLabel#browserStatus {{ color: {muted}; }}"
        f"QWidget#browserBanner {{ background: {base}; border-top: 1px solid {muted}; }}"
    )


class BrowserPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, window: "BrowserWindow"):
        super().__init__(profile)
        self._window = window

    def acceptNavigationRequest(  # noqa: N802
        self, url: QUrl, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if not is_main_frame:
            return True
        if url.scheme().lower() in ("about", "data", "blob", "chrome-error"):
            return True
        if self._window.allows(url):
            return True
        self._window.note_blocked(url)
        return False

    def createWindow(  # noqa: N802
        self, window_type: QWebEnginePage.WebWindowType
    ) -> QWebEnginePage:
        # 轻量浏览器只留一个视图：target=_blank 也回落到本页，白名单在
        # acceptNavigationRequest 里再判一次。
        return self


class BrowserWindow(QMainWindow):
    def __init__(
        self,
        storage_dir: Path,
        *,
        extra_domains=(),
        default_site_id: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._storage_dir = Path(storage_dir)
        self._base_extra = sites_mod.normalize_extra(extra_domains)
        self._session_extra: list[str] = []
        self._pending: QUrl | None = None
        self._current_site: sites_mod.Site | None = None
        self.setWindowTitle("AI 浏览器")
        self.setMinimumSize(*MIN_SIZE)
        self.setStyleSheet(_chrome_stylesheet(self))

        central = QWidget(self)
        central.setObjectName("browserChrome")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget(central)
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(8, 6, 8, 2)
        top_row.setSpacing(4)
        self.back_button = self._tool_button("‹", "后退", self._go_back)
        self.forward_button = self._tool_button("›", "前进", self._go_forward)
        self.reload_button = self._tool_button("⟳", "刷新", self._reload)
        self.home_button = self._tool_button("⌂", "回到默认站点", self._go_home)
        self.address = QLineEdit(top)
        self.address.setClearButtonEnabled(True)
        self.address.setPlaceholderText("站点名或网址，例如 kimi / chat.deepseek.com")
        self.address.returnPressed.connect(self._address_entered)
        self.external_button = self._tool_button(
            "外部打开", "用外部浏览器打开当前页", self._open_current_external
        )
        self.pin_button = self._tool_button("置顶", "窗口保持在最前", self._toggle_pin, checkable=True)
        for widget in (
            self.back_button,
            self.forward_button,
            self.reload_button,
            self.home_button,
        ):
            top_row.addWidget(widget)
        top_row.addWidget(self.address, 1)
        top_row.addWidget(self.external_button)
        top_row.addWidget(self.pin_button)
        root.addWidget(top)

        rail = QWidget(central)
        rail_row = QHBoxLayout(rail)
        rail_row.setContentsMargins(8, 0, 8, 4)
        rail_row.setSpacing(4)
        self._site_buttons: list[QPushButton] = []
        for site in sites_mod.SITES:
            button = QPushButton(site.name, rail)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"打开{site.name}")
            button.clicked.connect(lambda _checked=False, item=site: self.open_site(item))
            rail_row.addWidget(button)
            self._site_buttons.append(button)
        rail_row.addStretch(1)
        root.addWidget(rail)

        self.view = QWebEngineView(central)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.page = BrowserPage(_profile_for(self._storage_dir), self)
        self.view.setPage(self.page)
        root.addWidget(self.view, 1)

        self.banner = QWidget(central)
        self.banner.setObjectName("browserBanner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.banner.setVisible(False)
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(10, 6, 10, 6)
        banner_row.setSpacing(8)
        self.banner_label = QLabel(self.banner)
        self.banner_label.setWordWrap(True)
        self.banner_allow = QPushButton("仅本次允许", self.banner)
        self.banner_allow.clicked.connect(self._allow_pending)
        self.banner_external = QPushButton("用外部浏览器打开", self.banner)
        self.banner_external.clicked.connect(self._open_pending_external)
        banner_row.addWidget(self.banner_label, 1)
        banner_row.addWidget(self.banner_allow)
        banner_row.addWidget(self.banner_external)
        root.addWidget(self.banner)

        self.status = QLabel("", central)
        self.status.setObjectName("browserStatus")
        self.status.setContentsMargins(10, 4, 10, 6)
        root.addWidget(self.status)
        self.setCentralWidget(central)

        self.view.urlChanged.connect(self._on_url_changed)
        self.view.titleChanged.connect(self._on_title_changed)
        self.view.loadStarted.connect(lambda: self._set_status("正在加载…"))
        self.view.loadProgress.connect(self._on_progress)
        self.view.loadFinished.connect(self._on_load_finished)

        self._install_shortcuts()
        self._restore_geometry()
        self.open_site(sites_mod.default_site(default_site_id))

    # ---- 工具栏
    def _tool_button(self, text, tip, slot, *, checkable=False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setCheckable(checkable)
        button.clicked.connect(slot)
        return button

    def _install_shortcuts(self) -> None:
        for keys, slot in (
            ("Ctrl+L", self._focus_address),
            ("F5", self._reload),
            ("Ctrl+R", self._reload),
            ("Alt+Left", self._go_back),
            ("Alt+Right", self._go_forward),
            ("Ctrl+W", self.close),
            ("Ctrl+0", lambda: self.view.setZoomFactor(1.0)),
            ("Ctrl++", lambda: self._zoom(0.1)),
            ("Ctrl+=", lambda: self._zoom(0.1)),
            ("Ctrl+-", lambda: self._zoom(-0.1)),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)

    def _zoom(self, delta: float) -> None:
        factor = max(0.5, min(2.0, self.view.zoomFactor() + delta))
        self.view.setZoomFactor(factor)
        self._set_status(f"缩放 {int(factor * 100)}%")

    def _focus_address(self) -> None:
        self.address.setFocus()
        self.address.selectAll()

    # ---- 导航
    def allowed_extra(self) -> tuple[str, ...]:
        return tuple(self._base_extra) + tuple(self._session_extra)

    def allows(self, url: QUrl) -> bool:
        return sites_mod.is_allowed(url.toString(), self.allowed_extra())

    def open_site(self, site: sites_mod.Site | None) -> None:
        target = site or sites_mod.default_site(None)
        self._current_site = target
        self._navigate(target.url)

    def _navigate(self, text: str) -> None:
        site, url = sites_mod.resolve_target(text, self.allowed_extra())
        if not url:
            self._set_status(f"只允许打开 AI 站点白名单内的地址：{text}")
            return
        if site is not None:
            self._current_site = site
            self.setWindowTitle(f"{site.name} · AI 浏览器")
        self.banner.setVisible(False)
        self._pending = None
        self.view.setUrl(QUrl(url))

    def _address_entered(self) -> None:
        self._navigate(self.address.text().strip())

    def _go_back(self) -> None:
        self.view.back()

    def _go_forward(self) -> None:
        self.view.forward()

    def _reload(self) -> None:
        self.view.reload()

    def _go_home(self) -> None:
        self.open_site(self._current_site or sites_mod.default_site(None))

    def _toggle_pin(self, checked: bool = False) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(checked))
        self.show()

    # ---- 白名单拦截
    def note_blocked(self, url: QUrl) -> None:
        host = url.host() or url.toString()
        self._pending = url
        self.banner_label.setText(f"已阻止跳转到 {host}：它不在 AI 站点白名单里。")
        self.banner.setVisible(True)
        self._set_status(f"已阻止：{host}")

    def _allow_pending(self) -> None:
        if self._pending is None:
            return
        host = self._pending.host()
        if host and host not in self._session_extra:
            self._session_extra.append(host)
        target = self._pending
        self.banner.setVisible(False)
        self._pending = None
        self.view.setUrl(target)

    def _open_pending_external(self) -> None:
        if self._pending is None:
            return
        self._open_external(self._pending)

    def _open_current_external(self) -> None:
        self._open_external(self.view.url())

    def _open_external(self, url: QUrl) -> None:
        channel = external.open_external(url.toString())
        label = {"edge": "Edge 应用窗口", "system": "系统浏览器"}.get(channel, "")
        self._set_status(f"已用{label}打开 {url.toString()}" if label else "外部打开失败")

    # ---- 视图状态
    def _on_url_changed(self, url: QUrl) -> None:
        text = url.toString()
        if text and not text.startswith("about:blank"):
            self.address.setText(text)
        site = sites_mod.site_for_url(text)
        if site is not None:
            self._current_site = site
            self.setWindowTitle(f"{site.name} · AI 浏览器")
        self.back_button.setEnabled(self.view.history().canGoBack())
        self.forward_button.setEnabled(self.view.history().canGoForward())

    def _on_title_changed(self, title: str) -> None:
        if title:
            self._set_status(title)

    def _on_progress(self, _value: int) -> None:
        self.back_button.setEnabled(self.view.history().canGoBack())
        self.forward_button.setEnabled(self.view.history().canGoForward())

    def _on_load_finished(self, ok: bool) -> None:
        self._set_status("加载完成" if ok else "加载失败：检查网络或站点是否要求登录")

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # ---- 几何
    def _geometry_path(self) -> Path:
        return self._storage_dir / GEOMETRY_FILE

    def _restore_geometry(self) -> None:
        data = {}
        try:
            data = json.loads(self._geometry_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        width = max(MIN_SIZE[0], int(data.get("width") or DEFAULT_SIZE[0]))
        height = max(MIN_SIZE[1], int(data.get("height") or DEFAULT_SIZE[1]))
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            # 屏幕比默认尺寸小时（1280×800 这类笔记本）必须缩进来，否则底边出屏。
            width = min(width, max(MIN_SIZE[0], available.width() - 40))
            height = min(height, max(MIN_SIZE[1], available.height() - 40))
        self.resize(width, height)
        x = data.get("x")
        y = data.get("y")
        if screen is not None:
            available = screen.availableGeometry()
            if isinstance(x, int) and isinstance(y, int):
                left = max(available.left(), min(x, available.right() - width))
                top = max(available.top(), min(y, available.bottom() - height))
            else:
                left = available.center().x() - width // 2
                top = available.center().y() - height // 2
            self.move(left, top)

    def _save_geometry(self) -> None:
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            rect = self.normalGeometry() if self.isMaximized() else self.geometry()
            self._geometry_path().write_text(
                json.dumps(
                    {
                        "x": rect.x(),
                        "y": rect.y(),
                        "width": rect.width(),
                        "height": rect.height(),
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            log.warning("cannot persist browser geometry")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        super().closeEvent(event)


def _profile_for(storage_dir: Path) -> QWebEngineProfile:
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    profile = QWebEngineProfile("ai-browser")
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        profile.setPersistentStoragePath(str(storage_dir / "profile"))
        profile.setCachePath(str(storage_dir / "cache"))
    except OSError:
        log.warning("browser profile storage unavailable, using memory profile")
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.setHttpUserAgent(USER_AGENT)
    profile.setHttpAcceptLanguage("zh-CN,zh;q=0.9")
    _PROFILE = profile
    return profile


def open_browser(
    storage_dir,
    *,
    extra_domains=(),
    default_site_id: str | None = None,
) -> BrowserWindow:
    """单例窗口：已开着就抬到前面，不重复建 profile。"""
    global _WINDOW
    try:
        if _WINDOW is not None and _WINDOW.isVisible():
            _WINDOW.raise_()
            _WINDOW.activateWindow()
            return _WINDOW
    except RuntimeError:
        _WINDOW = None
    window = BrowserWindow(
        Path(storage_dir),
        extra_domains=extra_domains,
        default_site_id=default_site_id,
    )
    _WINDOW = window
    window.show()
    window.raise_()
    window.activateWindow()
    return window
