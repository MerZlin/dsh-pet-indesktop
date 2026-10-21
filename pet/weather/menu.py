# -*- coding: utf-8 -*-
"""「查看天气」与「天气城市」菜单：建造器、城市对话框、城市列表读写。

**为什么独立成模块**：``context_menus/shared.py``（912 行）与
``modern_settings_dialog.py``（行数预算反复触顶）都不该再长；菜单行为本身又是
"联网搜索 + 模态对话框 + 配置写入"三件事的组合，放在这里可独立单测。

**菜单结构**（注册表动作 id → 建造器）：

    weather          「查看天气」      → pet.on_show_weather(pet)
    weather_cities   「天气城市」子菜单 → 城市清单（当前打勾 / 点击切换 /
                                        添加城市…（联网搜索或手填）/ 删除城市）

为什么是两个平级入口而不是一个「天气」子菜单：规格要求「查看天气」**点一下**
就出气泡，藏进子菜单会多一步；城市列表必须常驻可见（当前城市打勾），所以它自己
是个子菜单。两者同属 ``tools`` 段，默认布局相邻（见 modern-default-v1.json）。

**城市列表的读写口径**：``pet.cfg`` 在两条拓扑下都指向"用户那份全局配置"——
窗口路径是主 ``Config``，overlay 路径是 ``_SpriteConfig`` 视图（``get`` 转发主
配置、``set``/``save`` 经 ``__getattr__`` 转发到主配置对象）。因此城市列表是
全局设置：右键任意一只肥鱼加/删城市，所有肥鱼共用同一份清单。

**城市身份**：每项存的是**显示标签**（同名歧义时形如「朝阳 · 辽宁」），配套的地区
码落在 ``weather_city_codes`` 里——取数按码直取，所以"同名城市选哪项中哪项"
（终审 m1）。删除城市会一并清掉它的地区码。

**名字不改写**（终审 M1）：加城市存的是用户输入/源给出的**原名**，不改后缀；
带后缀的名字在查询侧自动再试一次（``weather.city_name_variants``）。

**联网**：只有「添加城市…」的搜索会联网（走 ``WeatherClient.search_cities``，即
国内源直连那一条），后台线程执行、结果经 queued 信号回填对话框；搜索不阻塞菜单
关闭流程。**查询带版本号**（终审 m2 + W1）：用户一改输入框就自增版本、旧候选与
旧选择立即作废，发起搜索时把当时的版本随结果一起带回，回来时对不上号的结果直接
丢弃——否则"搜北京在飞时改成上海"会让迟到的北京结果回填并抢占选中，点「添加」
加进去的就是北京。

**口径**（与余额/音乐菜单一致）：动作 ``close_on_trigger``，回调在原生菜单跟踪
循环结束后才执行（macOS 上菜单未关时建顶层窗口/弹对话框会被 AppKit 抑制）。
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pet.weather import (
    WeatherClient,
    add_city,
    city_label,
    current_city,
    normalize_city_codes,
    normalize_city_list,
    normalize_city_name,
    remove_city,
    same_city,
)

log = logging.getLogger(__name__)

EMPTY_HINT = "（还没有城市，先点下面的「添加城市…」）"
ADD_LABEL = "添加城市…"
REMOVE_LABEL = "删除城市"
SEARCH_IDLE_TEXT = "输入城市名后点「搜索」，或直接添加手填的名字（如 鹿城区）"


# ---------------------------------------------------------------- 配置读写
def _config_of(pet):
    cfg = getattr(pet, "cfg", None)
    return cfg if cfg is not None and callable(getattr(cfg, "get", None)) else None


def _save(cfg) -> None:
    saver = getattr(cfg, "save", None)
    if callable(saver):
        saver()


def _notify(pet, text: str) -> None:
    """用户可见的提示（城市上限/重名这类"操作没能生效"的原因），绝不静默。"""
    parent = getattr(pet, "dialog_parent", None) or pet
    try:
        QMessageBox.warning(parent, "天气城市", text)
    except Exception:  # noqa: BLE001 - 提示失败不该让菜单回调炸掉
        log.exception("天气城市提示失败")


def _show_weather(pet) -> None:
    callback = getattr(pet, "on_show_weather", None)
    if callable(callback):
        callback(pet)


def _remember_code(cfg, name: str, code: str) -> None:
    """记下城市 → 地区码（同名城市靠它区分身份）；没有码就什么都不动。"""
    code = str(code or "").strip()
    label = normalize_city_name(name)
    if not code or not label:
        return
    codes = normalize_city_codes(cfg.get("weather_city_codes"))
    codes[label] = code
    cfg.set("weather_city_codes", codes)


def apply_added_city(pet, name, *, code: str = "") -> tuple[bool, str]:
    """把城市写进列表并设为当前城市；返回 ``(是否成功, 失败原因)``。

    ``code`` = 选中候选的地区码（m1）：**同名城市选哪项就存哪项的身份**，
    下一次取数直接按码取，而不是回到检索接口的第一条。
    """
    cfg = _config_of(pet)
    if cfg is None:
        return False, "配置不可用"
    cities = normalize_city_list(cfg.get("weather_city_list"))
    updated, error = add_city(cities, name)
    if error:
        return False, error
    stored = normalize_city_name(name)
    cfg.set("weather_city_list", updated)
    cfg.set("weather_city", stored)
    _remember_code(cfg, stored, code)
    _save(cfg)
    return True, ""


def switch_city(pet, city) -> None:
    """切换当前城市并落盘（城市不在列表里时顺手补进列表，保持两者一致）。"""
    cfg = _config_of(pet)
    if cfg is None:
        return
    selected = normalize_city_name(city)
    if not selected:
        return
    cfg.set("weather_city", selected)
    cities = normalize_city_list(cfg.get("weather_city_list"))
    if selected not in cities:
        updated, error = add_city(cities, selected)
        if not error:
            cfg.set("weather_city_list", updated)
    _save(cfg)


def remove_city_from_config(pet, city) -> None:
    """从列表删掉城市；删的是当前城市时当前切到剩下的第一个（列表空了就清空）。

    顺手清掉它的地区码（同名城市删一个不该把另一个的身份也留着）。
    """
    cfg = _config_of(pet)
    if cfg is None:
        return
    cities = normalize_city_list(cfg.get("weather_city_list"))
    remaining, new_current = remove_city(cities, city, current_city(cfg))
    cfg.set("weather_city_list", remaining)
    cfg.set("weather_city", new_current)
    codes = normalize_city_codes(cfg.get("weather_city_codes"))
    kept = {label: code for label, code in codes.items() if not same_city(label, city)}
    if kept != codes:
        cfg.set("weather_city_codes", kept)
    _save(cfg)


# ---------------------------------------------------------------- 城市对话框
class WeatherCityDialog(QDialog):
    """添加城市：联网搜索候选 + 手填兜底（搜索不可用时仍能加城市）。

    **为什么手填必须留着**：源 A（中国天气网）搜不到的输入不该变成死路
    （真机实测「鹿城区」搜不到、「鹿城」才行；国外源也只认部分中文地名）。
    搜不到时状态栏明说"可以直接添加"，输入的垃圾名由取数阶段如实报失败。
    """

    search_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("weatherCityDialog")
        self.setWindowTitle("添加天气城市")
        self.setModal(True)
        self.setMinimumWidth(360)
        # 查询版本号：每次编辑或发起搜索都自增（见 search_epoch / set_search_results）
        self._query_epoch = 0

        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("weatherCityName")
        self.name_edit.setPlaceholderText("城市名，如 鹿城区 / 北京 / Tokyo")
        self.search_btn = QPushButton("搜索", self)
        self.search_btn.setObjectName("weatherCitySearch")

        self.results = QListWidget(self)
        self.results.setObjectName("weatherCityResults")
        self.results.setMinimumHeight(120)
        self.results.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        self.status_label = QLabel(SEARCH_IDLE_TEXT, self)
        self.status_label.setObjectName("weatherCityStatus")
        self.status_label.setWordWrap(True)

        self.add_btn = QPushButton("添加", self)
        self.add_btn.setObjectName("weatherCityAdd")
        self.add_btn.setDefault(True)
        cancel_btn = QPushButton("取消", self)
        cancel_btn.setObjectName("weatherCityCancel")

        query_row = QHBoxLayout()
        query_row.addWidget(self.name_edit, 1)
        query_row.addWidget(self.search_btn)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(self.add_btn)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("城市名", self))
        root.addLayout(query_row)
        root.addWidget(self.results, 1)
        root.addWidget(self.status_label)
        root.addLayout(buttons)

        self.search_btn.clicked.connect(self._request_search)
        self.name_edit.returnPressed.connect(self._request_search)
        self.name_edit.textChanged.connect(self._refresh_add_enabled)
        # 用户改动输入 → 旧候选与旧选择一律作废（m2：搜北京后改成上海，不能再
        # 把北京当结果添加）。用 textEdited（只有真键盘编辑才发）而不是
        # textChanged，否则程序化 setText 也会把我们自己的回填流程打断。
        self.name_edit.textEdited.connect(self._on_text_edited)
        self.results.itemSelectionChanged.connect(self._refresh_add_enabled)
        self.add_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        self._refresh_add_enabled()

    # ---- 状态
    def typed_name(self) -> str:
        return self.name_edit.text().strip()

    def search_epoch(self) -> int:
        """当前的查询版本号（每次编辑输入或发起搜索都自增）。

        ``start_search`` 发起时取一次，结果回来时由 :meth:`set_search_results`
        比对：版本不同的结果属于**已被用户改掉的那次查询**，直接丢弃——终审 m2
        只作废了已显示的候选，管不到还在飞的搜索线程。
        """
        return self._query_epoch

    def chosen_entry(self) -> dict:
        """最终要添加的条目：``{name, code, region}``；取消/空输入返回 ``{}``。

        name 是**显示标签**（同名歧义时带地区，如「朝阳 · 辽宁」），code 是源 A
        地区码——身份跟着它走（m1）。
        """
        item = self.results.currentItem()
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("name"):
                return dict(data)
        typed = normalize_city_name(self.typed_name())
        return {"name": typed, "code": "", "region": ""} if typed else {}

    def chosen_name(self) -> str:
        """最终要添加的城市名（显示标签）。"""
        return str(self.chosen_entry().get("name") or "")

    def _on_text_edited(self, _text: str = "") -> None:
        # 输入变了 = 新的查询版本：在飞的搜索结果一律作废（版本比对见
        # set_search_results），并把搜索按钮放回可点——否则被作废的结果回来时
        # 按钮永远停在「搜索中」，用户想重搜也点不动。
        self._query_epoch += 1
        self.search_btn.setEnabled(True)
        if self.results.count():
            self.results.clear()
        self.status_label.setText(SEARCH_IDLE_TEXT)
        self._refresh_add_enabled()

    def begin_search(self) -> None:
        # 每次发起搜索也推进版本：上一次还在飞的结果（含同一输入重搜）从此作废
        self._query_epoch += 1
        self.search_btn.setEnabled(False)
        self.status_label.setText("搜索中…")

    def set_search_results(self, results, error: str = "", *,
                           epoch: int | None = None) -> None:
        """回填搜索结果（GUI 线程调用）。空结果/失败时保留手填这条路。

        ``epoch`` = 这条结果所属的查询版本（``start_search`` 发起时取）。与当前
        版本不符说明它是**已被用户改掉的那次查询**的迟到结果，原地丢弃——否则
        "搜北京在飞时改成上海"会让北京结果回填并抢占选中，点「添加」加进去的是
        北京。不传 = 视为当前版本（直接调用方不做版本校验）。
        """
        if epoch is not None and int(epoch) != self._query_epoch:
            return
        self.search_btn.setEnabled(True)
        self.results.clear()
        # 同名候选（朝阳 北京 / 辽宁）必须能区分：只有重名时才附地区，避免
        # 平时把列表撑成「鹿城 · 浙江」这种啰嗦标签。
        names = [normalize_city_name(candidate.name) for candidate in results or ()]
        duplicated = {name for name in names if names.count(name) > 1}
        for candidate in results or ():
            base = normalize_city_name(candidate.name)
            label = city_label(candidate.name, candidate.region,
                               ambiguous=base in duplicated)
            item = QListWidgetItem(label, self.results)
            item.setData(Qt.ItemDataRole.UserRole, {
                "name": label,
                "code": str(candidate.code or ""),
                "region": str(candidate.region or ""),
            })
        if self.results.count():
            self.results.setCurrentRow(0)
            self.status_label.setText(
                f"找到 {self.results.count()} 个候选，选中一个后点「添加」；也可以直接添加手填的名字。"
            )
        elif error:
            self.status_label.setText(f"搜索失败：{error}（可以直接添加你输入的城市名）")
        else:
            self.status_label.setText("没有找到这个城市（可以直接添加你输入的城市名）")
        self._refresh_add_enabled()

    def _refresh_add_enabled(self, *_args) -> None:
        self.add_btn.setEnabled(bool(self.chosen_name()))

    def _request_search(self) -> None:
        query = self.typed_name()
        if not query or not self.search_btn.isEnabled():
            return
        # 立即进入"搜索中"：对话框自己管这个状态，不等归属方回调（否则归属方
        # 没接上搜索链路时按钮会永远停在可点状态，用户以为点了没反应）。
        self.begin_search()
        self.search_requested.emit(query)


class _SearchBridge(QObject):
    """搜索线程 → GUI 线程的结果桥（queued 信号，同 weather_service 口径）。"""

    done = Signal(object, str)

    def __init__(self, dialog, query: str, epoch: int) -> None:
        super().__init__()
        self._dialog = dialog
        self.done.connect(self._apply)
        self._query = query
        self._epoch = int(epoch)

    def _apply(self, results, error) -> None:
        # 带上发起时的查询版本：对话框据此丢弃"这一版查询已被用户改掉"的结果
        self._dialog.set_search_results(results, error, epoch=self._epoch)

    def run(self, searcher) -> None:
        try:
            results, error = searcher(self._query)
        except Exception as exc:  # noqa: BLE001 - 搜索失败要如实显示，不炸线程
            log.exception("天气城市搜索异常")
            results, error = [], f"{type(exc).__name__}: {exc}"
        self.done.emit(list(results or ()), str(error or ""))


def start_search(dialog, query: str, searcher, runner=None) -> None:
    """在后台跑一次城市搜索，结果经 queued 信号回填对话框。"""
    if not query:
        return
    runner = runner or _run_in_thread
    dialog.begin_search()
    # 版本号必须在 begin_search **之后**取：对话框的按钮路径已经先自增过一次
    # （见 ``_request_search``），取早了会把自己这次搜索的结果判成迟到。
    bridge = _SearchBridge(dialog, query, dialog.search_epoch())
    dialog._search_bridge = bridge  # 保活到结果落地
    try:
        runner(lambda: bridge.run(searcher))
    except Exception as exc:  # noqa: BLE001 - 起线程失败也要把对话框从"搜索中"放出来
        log.exception("天气城市搜索线程启动失败")
        # 线程没起来 = 这次搜索的版本就是当前版本，无需版本校验（直接报错放行）
        dialog.set_search_results([], f"{type(exc).__name__}: {exc}")


def _run_in_thread(work) -> None:
    threading.Thread(target=work, daemon=True, name="pet-weather-search").start()


def open_add_city_dialog(pet, *, dialog_factory=None, search=None, run_async=None) -> dict:
    """打开「添加城市」对话框，返回用户选定的条目（取消返回 ``{}`` / None）。

    条目 = ``{name, code, region}``：``name`` 是显示标签（同名歧义时带地区），
    ``code`` 是源 A 地区码——取数靠它认人（m1）。
    ``search`` / ``run_async`` / ``dialog_factory`` 可注入：测试用假对话框与假
    搜索函数，生产路径默认走 ``WeatherClient.search_cities``（国内源直连）。
    """
    factory = dialog_factory or (lambda parent=None: WeatherCityDialog(parent))
    searcher = search or (lambda query: WeatherClient().search_cities(query))
    parent = getattr(pet, "dialog_parent", None)
    if not isinstance(parent, QWidget):
        parent = pet if isinstance(pet, QWidget) else None
    dialog = factory(parent)
    dialog.search_requested.connect(
        lambda query: start_search(dialog, query, searcher, runner=run_async)
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return {}
    entry = dialog.chosen_entry()
    return dict(entry) if isinstance(entry, dict) and entry.get("name") else {}


def add_city_flow(pet, *, dialog_factory=None, search=None, run_async=None,
                  notify=None) -> str:
    """「添加城市…」的完整流程：对话框 → 写入列表 → 设为当前 → 冒泡。

    返回实际加进去的城市名（取消/失败返回空串）；失败原因经 ``notify`` 告知用户
    （上限 12、重名这类"没生效"的原因绝不静默）。
    """
    entry = open_add_city_dialog(
        pet, dialog_factory=dialog_factory, search=search, run_async=run_async
    ) or {}
    name = str(entry.get("name") or "")
    if not name:
        return ""
    ok, error = apply_added_city(pet, name, code=str(entry.get("code") or ""))
    if not ok:
        (notify or (lambda text: _notify(pet, text)))(error)
        return ""
    _show_weather(pet)
    return normalize_city_name(name)


# ---------------------------------------------------------------- 菜单建造器
#
# **停用门落在建造器里，不只写在注册表上（B7 的形态）**：注册表的 enabled/
# disabled_reason 只管布局渲染侧，绕过注册表的直接调用（历史形态是旧版扁平菜单，
# 2026-10-08 已退役）就会拿到一个还能进的入口。语义沿用 CONTEXT.md 的
# Runtime Disabled：**入口保留、置灰、写明原因**，
# 不是整条消失（消失会让用户以为功能被删了；置灰 + tooltip 才说明"是开关关的"）。
# 两个拓扑都读 ``pet.cfg``（overlay 是 ``_SpriteConfig`` 视图，get 转发主配置）。

WEATHER_DISABLED_REASON = "天气功能已在设置中关闭"


def weather_menu_enabled(pet) -> bool:
    """「查看天气 / 天气城市」是否可用：只看设置里的天气总开关（默认开）。

    注册表的 ``enabled`` 与建造器写的 tooltip 共用这一份判定，避免"设置里关掉了、
    菜单里还在"这种两套真值。
    """
    cfg = _config_of(pet)
    return bool(cfg.get("weather_enabled", True)) if cfg is not None else True


def _mark_disabled(action) -> None:
    """Runtime Disabled 置灰：保留位置 + 写明原因（恒等幂等，重复调用无害）。"""
    if action is not None:
        action.setEnabled(False)
        action.setToolTip(WEATHER_DISABLED_REASON)


# 为什么这两个建造器在函数内导入 shared：``pet.weather.menu`` 被
# ``context_menus`` 反向引用，而 shared 经包 ``__init__`` 会一起跑——模块级
# 导入就成了循环。建造器只在真正建菜单时才需要那两个原语，故按调用点导入
# （与 ``balance`` 的惰性 import 同一手法）。
def add_show_weather(menu: QMenu, pet, *, icons: bool = True):
    """「查看天气」：点一次就用气泡报当前城市的天气。"""
    from ..context_menus.shared import add_action

    callback = getattr(pet, "on_show_weather", None)
    if callback is None:
        return None
    action = add_action(menu, "查看天气", "weather" if icons else None,
                        lambda: callback(pet), close_on_trigger=True)
    if not weather_menu_enabled(pet):
        _mark_disabled(action)
    return action


def add_weather_cities(menu: QMenu, pet, *, icons: bool = True):
    """「天气城市」子菜单：城市清单 + 切换 + 添加 + 删除。"""
    from ..context_menus.shared import add_action, add_submenu, connect_action

    cfg = _config_of(pet)
    if cfg is None:
        return None
    submenu = add_submenu(menu, "天气城市", "weather" if icons else None)
    if not weather_menu_enabled(pet):
        # 关掉总开关 = 整条入口不可用：置灰子菜单的 menuAction（展不开 ⇒ 添加城市
        # 不可达），与新版 ``populate`` 对子菜单条目的处置逐点一致。
        _mark_disabled(submenu.menuAction())
    cities = normalize_city_list(cfg.get("weather_city_list"))
    current = current_city(cfg)
    if not cities:
        empty = submenu.addAction(EMPTY_HINT)
        empty.setEnabled(False)
    for city in cities:
        action = submenu.addAction(city)
        action.setCheckable(True)
        action.setChecked(city == current)
        action.setProperty("closeOnTrigger", True)
        connect_action(action, lambda city=city: _on_city_picked(pet, city))
    submenu.addSeparator()
    add_action(submenu, ADD_LABEL, None, lambda: add_city_flow(pet), close_on_trigger=True)
    if cities:
        remove_menu = add_submenu(submenu, REMOVE_LABEL)
        for city in cities:
            add_action(remove_menu, city, None,
                       lambda city=city: remove_city_from_config(pet, city),
                       close_on_trigger=True)
    return submenu


def _on_city_picked(pet, city: str) -> None:
    """点城市 = 切换 + 立刻冒一次泡（否则用户还要再点一次「查看天气」）。"""
    switch_city(pet, city)
    _show_weather(pet)
