from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .base import SettingsPage
from .page_market import MarketPage
from .page_mods import ModsPage

logger = logging.getLogger("petcore.ui.settings.extensions")

#: 不进组、固定排在最前面的页面（扩展管理本体）。
STANDALONE_GROUP = ""
BASE_GROUP = "base"
ADDON_GROUP = "addon"
CORE_GROUP = "core"
GROUP_TITLES = ((BASE_GROUP, "基础扩展"), (ADDON_GROUP, "加装扩展"))
EMPTY_GROUP_TEXT = "（暂无）"
GROUP_ARROW_EXPANDED = "▾"
GROUP_ARROW_COLLAPSED = "▸"
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

# ---------------------------------------------------------------- 两轴几何
#
# 用户口径：**当前的显示范围就是下限**。
#   · 宽度轴：下限恒为 190（不比现在窄），上限 260 随字号放大 —— 字号变大后
#     长 mod 名有更多空间，但短标签不会把导航撑胖；实际宽度由标签文字量出来。
#   · 高度轴：下限恒为 420，往上由窗口高度给（浏览器吃满整页剩余高度）。
# 实测（900×620 + 字号 18）证明把高度下限也按字号放大到 582 会让整页多出一层
# 外层滚动条，收益为负，所以高度下限固定。

#: 界面字号的基准（``ui_font_size`` 默认 13，范围 10–18）。
BASE_FONT = 13
NAV_MIN_WIDTH = 190
NAV_MAX_WIDTH = 260
STACK_MIN_HEIGHT = 420
#: 导航项里除文字以外的横向占位：QSS ``padding: 0 10px 0 12px``（22）+ 左边框 3
#: + 竖向滚动条与列表内边距（约 10）。实测 190 宽的导航里文字可用约 155px。
NAV_TEXT_CHROME_PX = 35
#: 字号放大系数的上限：18/13 ≈ 1.38，留一点余量给未来更大的字号档。
FONT_SCALE_MAX = 1.6


def font_scale(font_px) -> float:
    """字号 → 放大系数；**小于基准字号一律 1.0**（下限不缩）。"""
    try:
        value = float(font_px)
    except (TypeError, ValueError):
        return 1.0
    return max(1.0, min(FONT_SCALE_MAX, value / BASE_FONT))


def nav_bounds(font_px) -> tuple[int, int]:
    """导航宽度的 ``(下限, 上限)``：字号 13 → ``(190, 260)``，字号 18 → ``(190, 360)``。

    下限**不随字号变**——「当前的显示范围就作为下限」；只放开上限，让放大的
    字号有机会不被省略号裁掉。
    """
    return NAV_MIN_WIDTH, round(NAV_MAX_WIDTH * font_scale(font_px))


def nav_width(widest_label_px: int, font_px) -> int:
    """内容驱动、夹在 ``nav_bounds`` 之间的导航宽度。"""
    low, high = nav_bounds(font_px)
    try:
        wanted = int(widest_label_px) + NAV_TEXT_CHROME_PX
    except (TypeError, ValueError):
        wanted = low
    return max(low, min(high, wanted))


def _group_of(spec) -> str:
    return ADDON_GROUP if str(getattr(spec, "group", "") or "") == ADDON_GROUP else BASE_GROUP


class _NavList(QListWidget):
    """QListWidget 的默认 sizeHint 是 256 宽：导航取「内容驱动、夹在下限与上限之间」的宽度。

    宽度由 :meth:`ExtensionsPage._apply_geometry` 用 ``setFixedWidth`` 定下来，
    这里只把当前宽度报给布局（固定宽度本就优先于 sizeHint，读当前值是为了让
    「改完字号立刻生效」不必重建控件）。
    """

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.width() or NAV_MIN_WIDTH
        return QSize(width, super().sizeHint().height())


class _HostedWidgetPage(SettingsPage):
    """把非 SettingsPage 的第三方控件收进扩展管理，明确标注不参与保存。"""

    def __init__(self, host, widget: QWidget, title: str, parent=None):
        super().__init__(host, parent)
        self.add_note(
            f"「{title}」没有实现 SettingsPage 接口，改动不会随设置保存；"
            "请联系扩展作者改用 petcore.ui.settings.base.SettingsPage。"
        )
        container = QWidget(self.content)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        self.body.insertWidget(self.body.count() - 1, container)


class _NavFacade:
    """兼容旧的页内标签门面：`labels/currentKey/setCurrentKey` 代理到左侧导航。"""

    def __init__(self, owner: "ExtensionsPage"):
        self._owner = owner

    def labels(self) -> tuple[str, ...]:
        return tuple(label for _key, label in self._owner._page_labels)

    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _label in self._owner._page_labels)

    def currentKey(self) -> str:  # noqa: N802
        return self._owner.current_key()

    def setCurrentKey(self, key: str) -> None:  # noqa: N802
        self._owner.select_tab(key)

    def key_for_label(self, label: str) -> str:
        for key, current in self._owner._page_labels:
            if current == label:
                return key
        return ""


class ExtensionsPage(SettingsPage):
    title = "扩展管理"
    #: 两栏浏览器型页面：宽轴跟随设置窗口（不再停在 860 阅读宽度）。
    content_max_width = None

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._pages: list[tuple[str, SettingsPage]] = []
        self._page_labels: list[tuple[str, str]] = []
        self._entries: dict[str, tuple[str, SettingsPage]] = {}
        self._owners: dict[str, str] = {}
        self._grouped: dict[str, list[tuple[str, str]]] = {
            STANDALONE_GROUP: [],
            BASE_GROUP: [],
            ADDON_GROUP: [],
        }
        self._row_keys: dict[int, str] = {}
        self._header_items: dict[str, QListWidgetItem] = {}
        self._group_items: dict[str, list[QListWidgetItem]] = {}
        self._collapsed: dict[str, bool] = {}
        self._current_key = ""
        #: ``(字体指纹, 最宽标签像素宽)`` 的度量缓存（见 ``_measure_widest_label``）。
        self._label_cache: tuple[object, int] = (None, 0)

        container = QWidget(self.content)
        container.setObjectName("extensionsBrowser")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.nav = _NavList(container)
        self.nav.setObjectName("extensionsNav")
        self.nav.setAccessibleName("扩展页面列表")
        self.nav.setMinimumWidth(NAV_MIN_WIDTH)
        self.nav.setMaximumWidth(NAV_MAX_WIDTH)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.nav.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.nav.currentRowChanged.connect(self._on_row_changed)
        self.nav.itemClicked.connect(self._on_nav_clicked)

        self.stack = QStackedWidget(container)
        self.stack.setObjectName("extensionsPages")
        self.stack.currentChanged.connect(self._on_stack_changed)
        # 右侧页面自带滚动体：给一个最小高度，避免被外层滚动区压成 0。
        # 具体数值由 `_apply_geometry()` 按界面字号算（字号 13 → 420）。
        self.stack.setMinimumHeight(STACK_MIN_HEIGHT)
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        # 高度轴：浏览器吃掉整页的剩余高度，而不是沿用页面末尾那个
        # `addStretch(1)`（它会把大窗口的多余高度全吃掉，1600×1000 下方空 464px）。
        self._browser = container
        self.body.insertWidget(self.body.count() - 1, container, 1)
        self.body.setStretch(self.body.count() - 1, 0)

        self.tabs = _NavFacade(self)

        mods_page = ModsPage(host, self)
        mods_page.prepare_embedded()
        self._register("mods", "扩展管理", mods_page, STANDALONE_GROUP)

        market_page = MarketPage(host, self)
        market_page.prepare_embedded()
        self._register("market", "插件市场", market_page, STANDALONE_GROUP)

        for spec, page in self._contributed_pages():
            self._register_contribution(spec, page)

        self._fill_nav()
        self.select_tab("mods")
        self._apply_geometry()

    # ------------------------------------------------------------ 两轴几何
    def _font_px(self) -> int:
        """当前界面字号（px）：优先取 QSS 实际作用到导航上的值。"""
        pixel = self.nav.font().pixelSize()
        if pixel and pixel > 0:
            return int(pixel)
        try:
            value = int(self.host.config.get("ui_font_size") or BASE_FONT)
        except (AttributeError, TypeError, ValueError):
            value = BASE_FONT
        return max(10, min(18, value))

    def _measure_widest_label(self) -> int:
        """导航里最宽的一条文字（含分组标题与占位行）的像素宽度。

        用**导航自己的** ``QFontMetrics``：字号放大后行高与文字都跟着变，用页面
        字体量出来的值会偏小，长 mod 名又被省略号吃掉。按字体指纹缓存——样式
        polish 是异步的，构造期量到的可能还是默认字体，所以每次应用几何时都
        复查一次字体（缓存只省掉重复的 ``horizontalAdvance`` 循环）。
        """
        font_key = self.nav.font().key()
        cached_key, cached = self._label_cache
        if cached_key == font_key:
            return cached
        metrics = QFontMetrics(self.nav.font())
        widest = 0
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item is not None:
                widest = max(widest, metrics.horizontalAdvance(item.text()))
        widest = max(widest, metrics.horizontalAdvance(EMPTY_GROUP_TEXT))
        self._label_cache = (font_key, widest)
        return widest

    def _apply_geometry(self) -> None:
        """把两轴的下限与实际宽度落到控件上（幂等，字号/窗口/标签变化都可调）。"""
        if getattr(self, "nav", None) is None or getattr(self, "stack", None) is None:
            return
        font_px = self._font_px()
        width = nav_width(self._measure_widest_label(), font_px)
        if self.nav.width() != width or self.nav.minimumWidth() != width:
            self.nav.setFixedWidth(width)
        # 高度轴：下限是现值 420，往上由窗口给（容器 stretch=1 吃满剩余高度）。
        if self.stack.minimumHeight() != STACK_MIN_HEIGHT:
            self.stack.setMinimumHeight(STACK_MIN_HEIGHT)

    def reflow(self) -> None:
        """字号或主题变化后重排：作废度量缓存并重算两轴。"""
        self._label_cache = (None, 0)
        self._apply_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 宽度轴由布局给（内容跟视口），这里只在尺寸真的变了以后复算下限，
        # 免得高频 setFixedWidth 打断布局。
        try:
            self._apply_geometry()
        except RuntimeError:
            return

    def changeEvent(self, event) -> None:  # noqa: N802
        """字体/样式变化（含 QSS 延迟 polish）后复算：扩展导航的字号是 QSS 给的。"""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.reflow()


    # ------------------------------------------------------------ 页面装载
    def _contributed_pages(self) -> list[tuple[object, QWidget]]:
        provider = getattr(self.host, "settings_pages", None)
        if not callable(provider):
            return []
        try:
            specs = list(provider() or ())
        except Exception:
            logger.warning("settings_pages provider failed", exc_info=True)
            return []
        try:
            specs.sort(
                key=lambda spec: (
                    int(getattr(spec, "order", 500) or 0),
                    str(getattr(spec, "id", "")),
                )
            )
        except Exception:
            pass
        created: list[tuple[object, QWidget]] = []
        for spec in specs:
            # 已并入 Core 的页由 Core 设置页内嵌，不再在这里建第二份实例。
            if str(getattr(spec, "group", "") or "") == CORE_GROUP:
                continue
            factory = getattr(spec, "factory", None)
            if not callable(factory):
                continue
            try:
                page = factory(self.host)
            except Exception:
                logger.warning(
                    "settings page %s failed to build", getattr(spec, "id", "?"), exc_info=True
                )
                continue
            if isinstance(page, QWidget):
                created.append((spec, page))
        return created

    def _register_contribution(self, spec, page: QWidget) -> None:
        raw_key = str(getattr(spec, "id", "") or getattr(page, "title", ""))
        if not raw_key:
            return
        key = f"extension:{raw_key}"
        if key in self._entries:
            logger.warning("duplicate settings page id: %s", raw_key)
            return
        title = str(getattr(spec, "title", "") or getattr(page, "title", "") or raw_key)
        label = str(getattr(spec, "label", "") or title)
        if isinstance(page, SettingsPage):
            page.prepare_embedded()
        else:
            logger.warning(
                "settings page %s is not a SettingsPage; hosting it read-only", raw_key
            )
            page = _HostedWidgetPage(self.host, page, label, self)
        self._register(key, label, page, _group_of(spec))
        self._owners[key] = str(getattr(spec, "owner", "") or "")

    def _register(self, key: str, label: str, page: SettingsPage, group: str) -> None:
        self._pages.append((key, page))
        self._entries[key] = (label, page)
        self._grouped.setdefault(group, []).append((key, label))
        self.stack.addWidget(page)
        try:
            page.changed.connect(self.changed.emit)
        except Exception:
            pass

    # ------------------------------------------------------------ 卸载
    def uninstall_mod(self, mod_id: str) -> tuple[bool, str]:
        """卸载扩展：先处理该扩展未保存的设置，再交给 Core 删文件并重扫。"""
        from PySide6.QtWidgets import QMessageBox

        uninstall = getattr(self.host, "uninstall_mod", None)
        if not callable(uninstall):
            return False, "当前版本不支持在设置内卸载扩展"
        keys = [key for key, owner in self._owners.items() if owner == mod_id]
        dirty = [
            self._entries[key][1]
            for key in keys
            if key in self._entries and self._entries[key][1].dirty()
        ]
        if dirty:
            answer = QMessageBox.question(
                self,
                "扩展有未保存的修改",
                f"扩展「{mod_id}」的设置页里还有未保存的修改。\n\n"
                "删除扩展前要先处理这些修改。",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False, "已取消卸载"
            for page in dirty:
                if answer == QMessageBox.StandardButton.Save:
                    page.confirm_save()
                else:
                    page.discard_changes()
        ok, message = uninstall(mod_id)
        if not ok:
            return ok, message
        for key in keys:
            self._drop_page(key)
        self._fill_nav()
        self.select_tab("mods")
        mods_page = self._entries.get("mods", (None, None))[1]
        refresh = getattr(mods_page, "_refresh", None)
        if callable(refresh):
            refresh()
        return ok, message

    def _drop_page(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        self._owners.pop(key, None)
        if entry is None:
            return
        page = entry[1]
        self._pages = [(item_key, item) for item_key, item in self._pages if item_key != key]
        for group, items in self._grouped.items():
            self._grouped[group] = [pair for pair in items if pair[0] != key]
        self.stack.removeWidget(page)
        try:
            page.dispose()
        except Exception:
            logger.warning("dispose of %s failed", key, exc_info=True)
        page.deleteLater()

    # ------------------------------------------------------------ 左侧导航
    def _fill_nav(self) -> None:
        self.nav.blockSignals(True)
        self.nav.clear()
        self._row_keys = {}
        self._header_items = {}
        self._group_items = {}
        ordered: list[tuple[str, str]] = []
        for key, label in self._grouped.get(STANDALONE_GROUP, []):
            self._add_nav_page(key, label)
            ordered.append((key, label))
        for group, title in GROUP_TITLES:
            self._header_items[group] = self._add_nav_header(group, title)
            items: list[QListWidgetItem] = []
            entries = self._grouped.get(group, [])
            if not entries:
                items.append(self._add_nav_placeholder())
            for key, label in entries:
                items.append(self._add_nav_page(key, label))
                ordered.append((key, label))
            self._group_items[group] = items
        self._apply_collapsed_groups()
        self.nav.blockSignals(False)
        # labels 与导航同序（分组顺序），不是贡献注册顺序。
        self._page_labels = ordered
        # 标签变了（装载/卸载扩展）：宽度轴的内容依据要重算。
        self._label_cache = (None, 0)
        self._apply_geometry()

    def _add_nav_header(self, group: str, text: str) -> QListWidgetItem:
        item = QListWidgetItem(f"{GROUP_ARROW_EXPANDED} {text}")
        # 可点击（折叠开关）但不可选中：不给选中态，也不进 _row_keys。
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(GROUP_ROLE, group)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setToolTip(f"展开/收起「{text}」")
        self.nav.addItem(item)
        return item

    def _add_nav_placeholder(self) -> QListWidgetItem:
        item = QListWidgetItem(EMPTY_GROUP_TEXT)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.nav.addItem(item)
        return item

    def _add_nav_page(self, key: str, label: str) -> QListWidgetItem:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(label)
        self.nav.addItem(item)
        self._row_keys[self.nav.row(item)] = key
        return item

    def _apply_collapsed_groups(self) -> None:
        for group, title in GROUP_TITLES:
            header = self._header_items.get(group)
            if header is None:
                continue
            collapsed = bool(self._collapsed.get(group, False))
            arrow = GROUP_ARROW_COLLAPSED if collapsed else GROUP_ARROW_EXPANDED
            header.setText(f"{arrow} {title}")
            for item in self._group_items.get(group, ()):
                item.setHidden(collapsed)

    def is_group_collapsed(self, group: str) -> bool:
        return bool(self._collapsed.get(str(group), False))

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        group = item.data(GROUP_ROLE)
        if not group:
            return
        group = str(group)
        self._collapsed[group] = not self._collapsed.get(group, False)
        self._apply_collapsed_groups()
        self._restore_current_row()

    def _restore_current_row(self) -> None:
        """点组头只是折叠开关：把选中还给当前页，别让组头看起来被选中。"""
        row = next(
            (row for row, key in self._row_keys.items() if key == self._current_key),
            None,
        )
        if row is None or self.nav.currentRow() == row:
            return
        self.nav.blockSignals(True)
        try:
            self.nav.setCurrentRow(row)
        finally:
            self.nav.blockSignals(False)

    def _on_row_changed(self, row: int) -> None:
        key = self._row_keys.get(int(row))
        if key is None:
            return
        entry = self._entries.get(key)
        if entry is None:
            return
        page = entry[1]
        if self.stack.currentWidget() is not page:
            self.stack.setCurrentWidget(page)
        self._current_key = key
        self.changed.emit()

    def _on_stack_changed(self, index: int) -> None:
        """搜索定位等外部入口直接切 stack 时，把左侧选中同步过来。"""
        page = self.stack.widget(index)
        if page is None:
            return
        for key, (_label, entry_page) in self._entries.items():
            if entry_page is not page or key == self._current_key:
                continue
            # 搜索命中折叠组里的 mod 页时，左栏必须先展开，否则选中项不可见。
            self._expand_group_of(key)
            for row, row_key in self._row_keys.items():
                if row_key == key:
                    self.nav.setCurrentRow(row)
                    return

    # ------------------------------------------------------------ 选择接口
    def current_key(self) -> str:
        return self._current_key

    def select_tab(self, key: str) -> None:
        key = str(key)
        self._expand_group_of(key)
        for row, row_key in self._row_keys.items():
            if row_key != key:
                continue
            if self.nav.currentRow() == row:
                self._on_row_changed(row)
            else:
                self.nav.setCurrentRow(row)
            return

    def _expand_group_of(self, key: str) -> None:
        """深链/搜索跳到折叠组里的页时先把组展开，否则选中项在导航里看不见。"""
        for group, entries in self._grouped.items():
            if not any(entry_key == key for entry_key, _label in entries):
                continue
            if self._collapsed.get(group):
                self._collapsed[group] = False
                self._apply_collapsed_groups()
            return

    def select_label(self, label: str) -> None:
        for key, current in self._page_labels:
            entry = self._entries.get(key)
            title = str(getattr(entry[1], "title", "")) if entry is not None else ""
            if label in (current, title):
                self.select_tab(key)
                return

    # ------------------------------------------------------------ 聚合接口
    def dirty(self) -> bool:
        return any(page.dirty() for _key, page in self._pages)

    def draft(self) -> None:
        for _key, page in self._pages:
            page.draft()

    def confirm_save(self) -> None:
        for _key, page in self._pages:
            page.confirm_save()

    def discard_changes(self) -> None:
        for _key, page in self._pages:
            page.discard_changes()

    def dispose(self) -> None:
        for _key, page in self._pages:
            page.dispose()

    def keywords(self) -> list[str]:
        values = ["扩展", "模组", "mod", "插件", "安装", "卸载", "启用", "停用", "诊断"]
        for key, page in self._pages:
            values.append(str(getattr(page, "title", "")))
            entry = self._entries.get(key)
            if entry is not None:
                values.append(entry[0])
            getter = getattr(page, "keywords", None)
            if callable(getter):
                try:
                    values.extend(str(item) for item in getter())
                except Exception:
                    pass
        return values
