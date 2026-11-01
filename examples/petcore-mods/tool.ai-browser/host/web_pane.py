"""网页 AI 面板：QtWebView（Edge WebView2 后端）嵌进 core 的对话窗。

core 只给一个槽位和四个**可选**生命周期（``docs/PETCORE-MOD-FORMAT.md`` §5.2）：
``activate`` / ``deactivate`` / ``set_dark`` / ``shutdown``。窗口、标题栏、圆角、
airspace 与深色协调都在 core；这里只管网页本身和它自己的小工具条。

为什么不给每个站点单独开窗：WebView2 的渲染进程按用户数据目录共享，多开只会
多一份内存；一个 ``QStackedWidget`` 换页就能保住各站点的登录态与对话现场。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import engine, sites
from .runtime import SETTING_CUSTOM_SITES, SETTING_DEFAULT_SITE

log = logging.getLogger(sites.OWNER)

DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

#: 加载成功后注入的弹窗补偿脚本。
#:
#: ``QWebView`` 没有 ``createWindow`` 钩子：``target=_blank``、``window.open``
#: 三种写法都毫无反应（4.2.1 同引擎实测），而豆包/DeepSeek 的扫码登录常走弹窗。
#: 用捕获阶段监听，是因为注入发生在文档加载之后，覆盖 ``window.open`` 已经晚了。
_POPUP_SHIM_JS = r"""
(function () {
  try {
    if (window.__dshPopupShim) { return "already"; }
    window.__dshPopupShim = true;
    document.addEventListener("click", function (ev) {
      var el = ev.target;
      var a = (el && el.closest) ? el.closest("a[target=_blank]") : null;
      if (!a || !a.href) { return; }
      ev.preventDefault();
      ev.stopPropagation();
      window.location.href = a.href;
    }, true);
    window.open = function (url) {
      if (url) { window.location.href = String(url); }
      return null;
    };
    return "ok";
  } catch (e) {
    return "err:" + e;
  }
})()
"""

_LIGHT_QSS = """
QFrame#ai-browser-toolbar { background: #ffffff; border: none; border-bottom: 1px solid #f0f1f4; }
QLabel#ai-browser-status { color: #9299a5; font-size: 11px; }
QToolButton#ai-browser-tool, QPushButton#ai-browser-tool {
    min-height: 26px; padding: 0 9px; border: 1px solid #e4e7ee; border-radius: 8px;
    background: #fafbfd; color: #4b5563; font-size: 11px; font-weight: 600;
}
QToolButton#ai-browser-tool:hover, QPushButton#ai-browser-tool:hover {
    background: #f0f3f8; border-color: #ccd7ee; color: #326de6;
}
QToolButton#ai-browser-tool:disabled { color: #b9bec7; background: #fbfcfd; }
QToolButton#ai-browser-tool::menu-indicator { image: none; width: 0; }
QFrame#ai-browser-missing { background: #fafbfd; border: none; }
QLabel#ai-browser-missing-text { color: #4b5563; font-size: 13px; }
"""

_DARK_QSS = """
QFrame#ai-browser-toolbar { background: #16181d; border: none; border-bottom: 1px solid #262b36; }
QLabel#ai-browser-status { color: #8b93a1; font-size: 11px; }
QToolButton#ai-browser-tool, QPushButton#ai-browser-tool {
    min-height: 26px; padding: 0 9px; border: 1px solid #333947; border-radius: 8px;
    background: #1e222b; color: #c9d0da; font-size: 11px; font-weight: 600;
}
QToolButton#ai-browser-tool:hover, QPushButton#ai-browser-tool:hover {
    background: #252b36; border-color: #3f4757; color: #9dc0ff;
}
QToolButton#ai-browser-tool:disabled { color: #5d6572; background: #1a1e26; }
QToolButton#ai-browser-tool::menu-indicator { image: none; width: 0; }
QFrame#ai-browser-missing { background: #16181d; border: none; }
QLabel#ai-browser-missing-text { color: #c9d0da; font-size: 13px; }
"""


class WebPane(QFrame):
    """一个站点一条 ``QWebView``，懒建；切站点只换页。"""

    def __init__(self, parent=None, *, config=None, storage_root=None):
        super().__init__(parent)
        self.setObjectName("ai-browser-pane")
        self._config = config
        self._storage_root = storage_root
        self._views: dict[str, object] = {}
        self._containers: dict[str, QWidget] = {}
        self._current_id = ""
        self._dark = False
        self._engine_ready = False
        self._missing = engine.missing_reason()
        self._build()
        self._apply_theme()
        if self._missing:
            self._show_missing(self._missing)

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame(self)
        bar.setObjectName("ai-browser-toolbar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(2, 4, 2, 4)
        bar_layout.setSpacing(5)
        self._back = self._tool("返回", "ai-browser-back", bar_layout)
        self._back.clicked.connect(self._go_back)
        self._forward = self._tool("前进", "ai-browser-forward", bar_layout)
        self._forward.clicked.connect(self._go_forward)
        self._reload = self._tool("刷新", "ai-browser-reload", bar_layout)
        self._reload.clicked.connect(self._reload_or_stop)
        self._home = self._tool("新对话", "ai-browser-home", bar_layout)
        self._home.clicked.connect(self._go_home)
        self._site_button = QToolButton(bar)
        self._site_button.setObjectName("ai-browser-tool")
        self._site_button.setText("站点")
        self._site_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._site_button.setToolTip("切换网页版 AI 站点")
        self._site_menu = QMenu(self._site_button)
        self._site_button.setMenu(self._site_menu)
        bar_layout.addWidget(self._site_button)
        self._external = self._tool("浏览器打开", "ai-browser-external", bar_layout)
        self._external.clicked.connect(self._open_external)
        bar_layout.addStretch(1)
        self._status = QLabel("", bar)
        self._status.setObjectName("ai-browser-status")
        bar_layout.addWidget(self._status)
        outer.addWidget(bar)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ai-browser-stack")
        outer.addWidget(self._stack, 1)
        self._missing_page: QWidget | None = None
        self._rebuild_site_menu()
        self._sync_buttons()

    def _tool(self, text: str, name: str, layout: QHBoxLayout) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("ai-browser-tool")
        button.setText(text)
        button.setToolTip(text)
        button.setProperty("toolName", name)
        layout.addWidget(button)
        return button

    # ---------------------------------------------------------------- 站点
    def _config_get(self, key: str, default=None):
        config = self._config
        if config is None:
            return default
        try:
            value = config.get(key, default)
        except Exception:  # noqa: BLE001 - 配置端口异常不该拖垮视图
            return default
        return default if value is None else value

    def site_list(self) -> tuple[sites.Site, ...]:
        return sites.all_sites(self._config_get(SETTING_CUSTOM_SITES, ""))

    def _default_site(self) -> sites.Site:
        wanted = str(self._config_get(SETTING_DEFAULT_SITE, sites.DEFAULT_SITE_ID) or "")
        for item in self.site_list():
            if item.id == wanted:
                return item
        return self.site_list()[0]

    def _rebuild_site_menu(self) -> None:
        self._site_menu.clear()
        for item in self.site_list():
            action = self._site_menu.addAction(item.name)
            action.triggered.connect(
                lambda _checked=False, site_id=item.id: self.show_site(site_id)
            )

    def show_site(self, site_id: str) -> None:
        if self._missing:
            self._show_missing(self._missing)
            return
        target = next(
            (item for item in self.site_list() if item.id == site_id), None
        ) or self._default_site()
        try:
            view = self._ensure_view(target)
        except Exception:  # noqa: BLE001 - 引擎初始化失败要说人话，不是白屏
            log.warning("webview create failed", exc_info=True)
            self._missing = "engine"
            self._show_missing("engine")
            return
        self._current_id = target.id
        self._stack.setCurrentWidget(self._containers[target.id])
        if view.url().isEmpty() or view.url().toString() in ("", "about:blank"):
            view.setUrl(QUrl(target.url))
        self._sync_buttons()

    def _ensure_view(self, site: sites.Site):
        cached = self._views.get(site.id)
        if cached is not None:
            return cached
        from PySide6.QtWebView import QWebView, QWebViewLoadingInfo, QWebViewSettings, QtWebView

        if not self._engine_ready:
            QtWebView.initialize()
            self._engine_ready = True
        root = engine.ensure_user_data(self._storage_root or ".")
        log.info("webview user data: %s", root)
        view = QWebView()
        settings = view.settings()
        for attribute in (
            QWebViewSettings.WebAttribute.JavaScriptEnabled,
            QWebViewSettings.WebAttribute.LocalStorageEnabled,
        ):
            settings.setAttribute(attribute, True)
        view.loadingChanged.connect(
            lambda info, site_id=site.id: self._on_loading(
                site_id, info, QWebViewLoadingInfo
            )
        )
        container = QWidget.createWindowContainer(view, self._stack)
        container.setObjectName(f"ai-browser-view-{site.id}")
        container.setStyleSheet("background: #ffffff;")
        self._stack.addWidget(container)
        self._containers[site.id] = container
        self._views[site.id] = view
        return view

    def _current_view(self):
        return self._views.get(self._current_id)

    # ---------------------------------------------------------------- 行为
    def _go_back(self) -> None:
        view = self._current_view()
        if view is not None and view.canGoBack():
            view.goBack()
        self._sync_buttons()

    def _go_forward(self) -> None:
        view = self._current_view()
        if view is not None and view.canGoForward():
            view.goForward()
        self._sync_buttons()

    def _reload_or_stop(self) -> None:
        view = self._current_view()
        if view is None:
            return
        if view.isLoading():
            view.stop()
            self._status.setText("已停止")
        else:
            view.reload()
        self._sync_buttons()

    def _go_home(self) -> None:
        """新对话 = 回到站点首页（站点自己的"新对话"在页面里）。"""
        view = self._current_view()
        site = next(
            (item for item in self.site_list() if item.id == self._current_id), None
        )
        if view is not None and site is not None:
            view.setUrl(QUrl(site.url))
        self._sync_buttons()

    def _open_external(self) -> None:
        view = self._current_view()
        url = view.url().toString() if view is not None else ""
        if not url:
            site = next(
                (item for item in self.site_list() if item.id == self._current_id), None
            )
            url = site.url if site is not None else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _sync_buttons(self) -> None:
        view = self._current_view()
        back = bool(view is not None and view.canGoBack())
        forward = bool(view is not None and view.canGoForward())
        self._back.setEnabled(back)
        self._forward.setEnabled(forward)
        self._reload.setText("停止" if (view is not None and view.isLoading()) else "刷新")

    def _on_loading(self, site_id: str, info, loading_info_cls) -> None:
        """``info.status`` 是**方法**不是属性（PySide6 绑定如此）。"""
        try:
            status = info.status()
        except Exception:  # noqa: BLE001 - 绑定变化时不留白
            return
        states = loading_info_cls.LoadStatus
        if status == states.Started:
            if site_id == self._current_id:
                self._status.setText("加载中…")
        elif status == states.Succeeded:
            self._inject_popup_shim(site_id)
            if site_id == self._current_id:
                self._status.setText("就绪")
        elif status == states.Failed:
            detail = ""
            try:
                detail = str(info.errorString() or "").strip()
            except Exception:  # noqa: BLE001
                detail = ""
            if site_id == self._current_id:
                self._status.setText(
                    f"加载失败（{detail}）" if detail else "加载失败，可点刷新重试"
                )
        self._sync_buttons()

    def _inject_popup_shim(self, site_id: str) -> None:
        view = self._views.get(site_id)
        if view is None:
            return
        try:
            view.runJavaScript(_POPUP_SHIM_JS)
        except Exception:  # noqa: BLE001 - 注入失败不该影响页面
            log.debug("popup shim injection failed: %s", site_id, exc_info=True)

    # ---------------------------------------------------------------- 缺口页
    def _show_missing(self, reason: str) -> None:
        page = self._missing_page
        if page is None:
            page = QFrame(self._stack)
            page.setObjectName("ai-browser-missing")
            box = QVBoxLayout(page)
            box.setContentsMargins(28, 28, 28, 28)
            box.setSpacing(12)
            text = QLabel("", page)
            text.setObjectName("ai-browser-missing-text")
            text.setWordWrap(True)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addStretch(1)
            box.addWidget(text)
            buttons = QHBoxLayout()
            buttons.setSpacing(8)
            buttons.addStretch(1)
            download = QPushButton("打开微软下载页", page)
            download.setObjectName("ai-browser-tool")
            download.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(DOWNLOAD_URL))
            )
            retry = QPushButton("重试", page)
            retry.setObjectName("ai-browser-tool")
            retry.clicked.connect(self._retry)
            buttons.addWidget(download)
            buttons.addWidget(retry)
            buttons.addStretch(1)
            box.addLayout(buttons)
            box.addStretch(2)
            self._missing_text = text
            self._missing_download = download
            self._stack.addWidget(page)
            self._missing_page = page
        self._missing_text.setText(engine.missing_hint(reason))
        self._missing_download.setVisible(reason == "runtime")
        self._stack.setCurrentWidget(page)
        self._status.setText("网页内核不可用")
        for button in (self._back, self._forward, self._reload, self._home, self._site_button):
            button.setEnabled(False)
        self._external.setEnabled(False)

    def _retry(self) -> None:
        self._missing = engine.missing_reason()
        if self._missing:
            self._show_missing(self._missing)
            return
        for button in (self._back, self._forward, self._reload, self._home, self._site_button):
            button.setEnabled(True)
        self._external.setEnabled(True)
        self.show_site(self._current_id or self._default_site().id)

    # ---------------------------------------------------------------- 主题
    def _apply_theme(self) -> None:
        self.setStyleSheet(_DARK_QSS if self._dark else _LIGHT_QSS)

    def set_dark(self, dark: bool) -> None:  # core 的可选生命周期
        self._dark = bool(dark)
        self._apply_theme()

    # ---------------------------------------------------------------- 生命周期
    def activate(self) -> None:
        if self._missing:
            self._show_missing(self._missing)
            return
        # 用户可能在设置里加了自定义站点：每次激活重建菜单，不要求重开窗口。
        self._rebuild_site_menu()
        if not self._current_id:
            self.show_site(self._default_site().id)
            return
        view = self._current_view()
        if view is not None and view.url().toString() in ("", "about:blank"):
            self.show_site(self._current_id)

    def deactivate(self) -> None:
        """切走：停加载，但不销毁页面（回来时保留现场）。"""
        for view in self._views.values():
            try:
                if view.isLoading():
                    view.stop()
            except Exception:  # noqa: BLE001
                continue

    def shutdown(self) -> None:
        """关窗：停加载并导航到空白页，别让 WebView2 在后台继续跑脚本。"""
        for view in self._views.values():
            try:
                if view.isLoading():
                    view.stop()
                view.setUrl(QUrl("about:blank"))
            except Exception:  # noqa: BLE001
                continue


def build_web_pane(context) -> WebPane:
    """core 调用的 view 工厂（GUI 线程、懒调用）。"""
    storage = getattr(context.ports, "storage", None)
    root = "."
    if storage is not None:
        try:
            root = str(storage.data_dir(sites.OWNER))
        except Exception:  # noqa: BLE001
            root = "."
    return WebPane(config=getattr(context.ports, "config", None), storage_root=root)
