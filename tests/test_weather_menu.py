# -*- coding: utf-8 -*-
"""天气菜单与城市子菜单（menu builders + 注册表 + 添加/删除流程 + 城市对话框）。

用例只碰公开面：菜单建造器、注册表 action id、城市对话框的公开方法、配置写入
记录。不断言私有调用顺序，也不真发外网请求（搜索函数注入）。

约定与 ``add_balance`` / ``build_character_menu`` 一致：建造器读 ``pet.cfg``、
经 ``pet.on_show_weather`` 把结果交给冒泡服务；城市列表是**全局设置**（窗口路径
直接写主配置，overlay 路径经 ``_SpriteConfig`` 的 ``__getattr__`` 转发到主配置）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from pet import weather as W  # noqa: E402
from pet.weather import menu as M  # noqa: E402
from pet.context_menus.registry import MENU_ACTIONS  # noqa: E402

app = QApplication.instance() or QApplication([])


class _Cfg:
    """配置视图替身：读 dict，写记账（真实场景是 Config / _SpriteConfig 视图）。"""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes: list[tuple[str, object]] = []
        self.saves = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))

    def save(self):
        self.saves += 1

    def written(self, key):
        return [value for name, value in self.writes if name == key]


class _Pet:
    """菜单宿主替身（PetWindow / SpriteMenuFacade 的公共面）。"""

    def __init__(self, values=None, *, available=True):
        self.cfg = _Cfg(values)
        self.shown: list = []
        self.on_show_weather = (
            (lambda *args: self.shown.append(args)) if available else None
        )


class _FakeDialog(QObject):
    """城市对话框替身：与 ``WeatherCityDialog`` 的公开面同形（``exec`` 不阻塞）。"""

    search_requested = Signal(str)

    def __init__(self, name="", *, code="", region="", query="", accepted=True):
        super().__init__()
        self._entry = {"name": name, "code": code, "region": region} if name else {}
        self._query = query
        self._accepted = accepted
        self.begun = 0
        self.results_seen: list = []

    def exec(self) -> int:  # noqa: A003 - 模仿 QDialog.exec
        if self._query:
            self.search_requested.emit(self._query)
        return 1 if self._accepted else 0

    def chosen_entry(self) -> dict:
        return dict(self._entry)

    def chosen_name(self) -> str:
        return str(self._entry.get("name") or "")

    def begin_search(self) -> None:
        self.begun += 1

    def search_epoch(self) -> int:
        # 版本校验由真对话框负责（见 ``WeatherCityDialog.set_search_results``）；
        # 替身恒 0 = 结果永远属于当前版本，本用例只关心回填链路。
        return 0

    def set_search_results(self, results, error="", *, epoch=None) -> None:
        self.results_seen.append((results, error))


def _labels(menu):
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _find(menu, label):
    for action in menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"菜单缺项 {label}；实际 = {_labels(menu)}")


def _factory(name: str, **kwargs):
    return lambda _parent=None: _FakeDialog(name, **kwargs)


# ---------------------------------------------------------------- 查看天气入口
def test_show_weather_action_calls_through_to_the_window_callback():
    pet = _Pet({})
    menu = QMenu()
    action = M.add_show_weather(menu, pet)
    assert action.text() == "查看天气"
    assert action.property("closeOnTrigger") is True
    action.trigger()
    assert pet.shown == [(pet,)], "窗口/壳侧的 on_show_weather 必须以宿主自身为 parent"


def test_show_weather_action_absent_without_callback():
    pet = _Pet({}, available=False)
    menu = QMenu()
    assert M.add_show_weather(menu, pet) is None
    assert _labels(menu) == []


# ------------------------------------------ 总开关关掉 = Runtime Disabled（置灰）
#
# 门必须落在**共享建造器**里：新版注册表的 enabled/disabled_reason 只管新版树，
# legacy 扁平菜单直调建造器，门只写在注册表上就是"旧版菜单仍然点得进城市搜索并
# 写配置"（两套菜单两个真值）。语义沿用 CONTEXT.md 的 Runtime Disabled：
# 位置保留、置灰、写明原因，**不让入口整条消失**。

def test_weather_entries_are_greyed_when_the_switch_is_off():
    """关闭天气总开关：共享建造器把两个入口都置灰并写明原因。"""
    pet = _Pet({"weather_enabled": False})
    menu = QMenu()

    action = M.add_show_weather(menu, pet)
    assert action is not None and "查看天气" in _labels(menu), "关掉开关不得让入口消失"
    assert action.isEnabled() is False
    assert action.toolTip() == M.WEATHER_DISABLED_REASON

    cities = M.add_weather_cities(menu, pet)
    assert cities is not None and "天气城市" in _labels(menu)
    assert cities.menuAction().isEnabled() is False, "子菜单入口同样置灰"
    assert cities.menuAction().toolTip() == M.WEATHER_DISABLED_REASON
    # 关闭态下「添加城市…」不可达（子菜单入口置灰后根本展不开）
    assert M.weather_menu_enabled(pet) is False


def test_weather_entries_stay_enabled_when_the_switch_is_on():
    """非回归：开关开着时两个入口照常可用（默认值也是开）。"""
    pet = _Pet({"weather_enabled": True})
    menu = QMenu()
    action = M.add_show_weather(menu, pet)
    assert action.isEnabled() is True
    assert action.toolTip() != M.WEATHER_DISABLED_REASON
    cities = M.add_weather_cities(menu, pet)
    assert cities.menuAction().isEnabled() is True

    default_pet = _Pet({})                      # 键缺失 = 默认开
    assert M.weather_menu_enabled(default_pet) is True


# ---------------------------------------------------------------- 城市子菜单
def _city_actions(submenu):
    return [action.text() for action in submenu.actions() if action.isCheckable()]


def test_city_submenu_checks_the_current_city_and_lists_all_of_them():
    pet = _Pet({"weather_city": "上海", "weather_city_list": ["北京", "上海", "鹿城"]})
    menu = QMenu()
    submenu = M.add_weather_cities(menu, pet)
    assert submenu is not None and submenu.title() == "天气城市"
    assert _city_actions(submenu) == ["北京", "上海", "鹿城"]
    checked = [action.text() for action in submenu.actions()
               if action.isCheckable() and action.isChecked()]
    assert checked == ["上海"], "当前城市必须有且只有一个勾"
    assert _labels(submenu) == ["北京", "上海", "鹿城", "添加城市…", "删除城市"]


def test_city_submenu_shows_an_empty_hint_when_no_city_is_configured():
    pet = _Pet({"weather_city": "", "weather_city_list": []})
    menu = QMenu()
    submenu = M.add_weather_cities(menu, pet)
    assert _city_actions(submenu) == []
    assert "还没有城市" in _labels(submenu)[0]


def test_clicking_a_city_switches_persists_and_shows_weather():
    pet = _Pet({"weather_city": "上海", "weather_city_list": ["北京", "上海"]})
    menu = QMenu()
    submenu = M.add_weather_cities(menu, pet)
    _find(submenu, "北京").trigger()
    assert pet.cfg.written("weather_city") == ["北京"]
    assert pet.cfg.saves == 1
    assert len(pet.shown) == 1, "切换城市后顺手把天气冒出来（否则还要再点一次）"


def test_switching_to_a_city_missing_from_the_list_keeps_the_list_consistent():
    pet = _Pet({"weather_city": "上海", "weather_city_list": ["上海"]})
    M.switch_city(pet, "鹿城")
    assert pet.cfg.get("weather_city") == "鹿城"
    assert pet.cfg.get("weather_city_list") == ["上海", "鹿城"]


def test_delete_city_submenu_removes_the_city_and_moves_the_current_one():
    pet = _Pet({"weather_city": "上海", "weather_city_list": ["北京", "上海"]})
    menu = QMenu()
    submenu = M.add_weather_cities(menu, pet)
    remove_menu = _find(submenu, "删除城市").menu()
    assert remove_menu is not None
    _find(remove_menu, "上海").trigger()
    assert pet.cfg.get("weather_city_list") == ["北京"]
    assert pet.cfg.get("weather_city") == "北京"
    assert pet.cfg.saves == 1


# ---------------------------------------------------------------- 添加城市
def test_add_city_flow_adds_selects_persists_and_shows_weather():
    pet = _Pet({"weather_city": "", "weather_city_list": []})
    added = M.add_city_flow(pet, dialog_factory=_factory("鹿城区"))
    assert added == "鹿城区", "存储层保留用户输入的原名（M1：不再剥成「鹿城」）"
    assert pet.cfg.get("weather_city_list") == ["鹿城区"]
    assert pet.cfg.get("weather_city") == "鹿城区"
    assert pet.cfg.saves == 1
    assert len(pet.shown) == 1


def test_add_city_flow_remembers_the_area_code_for_same_name_cities():
    """m1：候选的**地区码**要跟着城市一起存下来，否则同名城市下次查询还中第一条。"""
    pet = _Pet({"weather_city": "", "weather_city_list": []})
    added = M.add_city_flow(
        pet, dialog_factory=_factory("朝阳 · 辽宁", code="101071201", region="辽宁"))
    assert added == "朝阳 · 辽宁"
    assert pet.cfg.get("weather_city_list") == ["朝阳 · 辽宁"]
    assert pet.cfg.get("weather_city_codes") == {"朝阳 · 辽宁": "101071201"}


def test_removing_a_city_also_drops_its_area_code():
    pet = _Pet({
        "weather_city": "北京",
        "weather_city_list": ["北京", "朝阳 · 辽宁"],
        "weather_city_codes": {"朝阳 · 辽宁": "101071201"},
    })
    M.remove_city_from_config(pet, "朝阳 · 辽宁")
    assert pet.cfg.get("weather_city_list") == ["北京"]
    assert pet.cfg.get("weather_city_codes") == {}


def test_add_city_flow_reports_the_cap_instead_of_silently_dropping_it():
    full = [f"城市{i}" for i in range(W.MAX_CITIES)]
    pet = _Pet({"weather_city": full[0], "weather_city_list": list(full)})
    notes: list[str] = []
    added = M.add_city_flow(pet, dialog_factory=_factory("新城市"), notify=notes.append)
    assert added == ""
    assert notes == [W.CITY_LIMIT_TEXT]
    assert pet.cfg.get("weather_city_list") == full
    assert pet.cfg.writes == []


def test_add_city_flow_does_nothing_when_the_dialog_is_cancelled():
    pet = _Pet({"weather_city": "", "weather_city_list": []})
    assert M.add_city_flow(pet, dialog_factory=_factory("", accepted=False)) == ""
    assert pet.cfg.writes == []
    assert pet.shown == []


def test_open_add_city_dialog_threads_search_results_back_into_the_dialog():
    """对话框请求搜索 → 注入的搜索函数在调用线程执行 → 结果回填对话框。"""
    pet = _Pet({})
    dialog = _FakeDialog("鹿城", code="101210710", region="浙江", query="鹿城")
    queries: list[str] = []

    def searcher(query):
        queries.append(query)
        return [W.CityCandidate(code="101210710", name="鹿城", region="浙江")], ""

    chosen = M.open_add_city_dialog(
        pet, dialog_factory=lambda _parent=None: dialog, search=searcher,
        run_async=lambda work: work(),
    )
    assert chosen == {"name": "鹿城", "code": "101210710", "region": "浙江"}
    assert queries == ["鹿城"]
    assert dialog.begun == 1
    assert dialog.results_seen == [
        ([W.CityCandidate(code="101210710", name="鹿城", region="浙江")], "")
    ]


def test_open_add_city_dialog_returns_no_entry_when_cancelled():
    pet = _Pet({})
    assert not M.open_add_city_dialog(
        pet, dialog_factory=_factory("鹿城", accepted=False))


# ---------------------------------------------------------------- 城市对话框
def test_city_dialog_search_button_emits_the_typed_query():
    dialog = M.WeatherCityDialog()
    seen: list[str] = []
    dialog.search_requested.connect(seen.append)
    dialog.name_edit.setText(" 鹿城区 ")
    dialog.search_btn.click()
    assert seen == ["鹿城区"]

    dialog.name_edit.setText("   ")
    dialog.search_btn.click()
    assert seen == ["鹿城区"], "空白输入不触发搜索"


def test_city_dialog_disables_search_while_pending_and_restores_it():
    dialog = M.WeatherCityDialog()
    dialog.name_edit.setText("鹿城")
    dialog.search_btn.click()
    assert dialog.search_btn.isEnabled() is False
    assert "搜索中" in dialog.status_label.text()

    dialog.set_search_results(
        [W.CityCandidate(code="101210710", name="鹿城", region="浙江")], "")
    assert dialog.search_btn.isEnabled() is True
    assert dialog.chosen_name() == "鹿城"
    assert [dialog.results.item(i).text() for i in range(dialog.results.count())] == ["鹿城"]


def test_city_dialog_disambiguates_same_name_candidates_with_their_region():
    """m1：同名候选（朝阳 北京 / 辽宁）必须能区分，且选哪项就带回哪项的地区码。"""
    dialog = M.WeatherCityDialog()
    dialog.name_edit.setText("朝阳")
    dialog.set_search_results([
        W.CityCandidate(code="101010300", name="朝阳", region="北京"),
        W.CityCandidate(code="101071201", name="朝阳", region="辽宁"),
    ], "")
    labels = [dialog.results.item(i).text() for i in range(dialog.results.count())]
    assert labels == ["朝阳 · 北京", "朝阳 · 辽宁"]
    assert dialog.chosen_entry()["code"] == "101010300"   # 默认选中第一项

    dialog.results.setCurrentRow(1)
    assert dialog.chosen_entry() == {"name": "朝阳 · 辽宁", "code": "101071201",
                                     "region": "辽宁"}


def test_city_dialog_keeps_plain_names_when_there_is_no_collision():
    dialog = M.WeatherCityDialog()
    dialog.name_edit.setText("鹿城")
    dialog.set_search_results(
        [W.CityCandidate(code="101210710", name="鹿城", region="浙江")], "")
    assert dialog.chosen_entry() == {"name": "鹿城", "code": "101210710", "region": "浙江"}


def test_editing_the_query_drops_the_stale_candidate_and_selection():
    """m2：搜北京后又改成上海，绝不能还把北京当选择结果添加进去。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog = M.WeatherCityDialog()
    dialog.name_edit.setText("北京")
    dialog.set_search_results(
        [W.CityCandidate(code="101010100", name="北京", region="北京")], "")
    assert dialog.chosen_name() == "北京"

    # 用户动键盘（Backspace 是真实键盘编辑，会发 textEdited）→ 旧候选必须作废
    QTest.keyClick(dialog.name_edit, Qt.Key.Key_Backspace)
    assert dialog.results.count() == 0, "旧候选必须随输入作废"
    assert dialog.results.currentItem() is None

    dialog.name_edit.setText("上海")
    assert dialog.chosen_name() == "上海", "改成上海后不能再把北京当结果"
    assert dialog.status_label.text() == M.SEARCH_IDLE_TEXT


def test_city_dialog_offers_manual_entry_when_the_search_finds_nothing():
    dialog = M.WeatherCityDialog()
    dialog.name_edit.setText("鹿城区")
    dialog.set_search_results([], "请求超时")
    assert dialog.results.count() == 0
    assert "请求超时" in dialog.status_label.text()
    assert "直接添加" in dialog.status_label.text()
    assert dialog.chosen_name() == "鹿城区"
    assert dialog.add_btn.isEnabled() is True


def test_city_dialog_add_button_follows_the_input_state():
    dialog = M.WeatherCityDialog()
    assert dialog.add_btn.isEnabled() is False
    dialog.name_edit.setText("鹿城")
    assert dialog.add_btn.isEnabled() is True
    dialog.name_edit.setText("   ")
    assert dialog.add_btn.isEnabled() is False


def test_start_search_feeds_the_dialog_through_the_injected_searcher():
    dialog = M.WeatherCityDialog()
    queries: list[str] = []

    def searcher(query):
        queries.append(query)
        return [W.CityCandidate(code="101210710", name="鹿城", region="浙江")], ""

    M.start_search(dialog, "鹿城", searcher, runner=lambda work: work())
    assert queries == ["鹿城"]
    assert dialog.chosen_name() == "鹿城"
    assert dialog.search_btn.isEnabled() is True


def _type_into(line_edit, text: str, *, replace: bool = False) -> None:
    """按真键盘路径输入（``textEdited`` 只有真编辑才发，``setText`` 不发）。

    ``replace=True`` 先全选再打（等价用户 Ctrl+A 后重打），输入的字符覆盖旧内容。
    不用 ``QTest.keyClicks``：它对非 ASCII 字符会在 Windows/offscreen 上原生
    崩溃（进程静默退出），手工发 QKeyEvent 走的是同一条 QLineEdit 事件路径。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    if replace:
        line_edit.selectAll()
    for char in text:
        app.sendEvent(line_edit, QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_unknown,
            Qt.KeyboardModifier.NoModifier, char))


def test_late_search_results_from_an_abandoned_query_are_dropped():
    """W1：搜北京在飞时改成上海，迟到的北京结果不得回填、不得抢占选中。

    改前：``_on_text_edited`` 只清**已显示**的候选，搜索线程手里的结果回来时
    仍被无条件灌回（``setCurrentRow(0)``）——用户看到北京候选，点「添加」加进
    去的也是北京，与终审 m2 的承诺相反。
    """
    beijing = W.CityCandidate(code="101010100", name="北京", region="北京")
    dialog = M.WeatherCityDialog()
    in_flight: list = []
    _type_into(dialog.name_edit, "北京")
    M.start_search(dialog, "北京", lambda _query: ([beijing], ""),
                   runner=in_flight.append)
    assert in_flight, "前提：搜索在飞（结果还没落地）"
    assert dialog.search_btn.isEnabled() is False

    _type_into(dialog.name_edit, "上海", replace=True)  # 用户在结果到达前改掉查询

    in_flight[0]()  # 北京的结果现在才到

    # 先钉复现：迟到结果**不得**回填候选（改前这里会拿到北京并选中第一项）
    assert dialog.results.count() == 0, "迟到的旧结果不得回填候选"
    assert dialog.results.currentItem() is None, "更不得抢占选中"

    assert dialog.chosen_name() == "上海", "能添加的只能是当前输入"
    assert dialog.status_label.text() == M.SEARCH_IDLE_TEXT
    assert dialog.search_btn.isEnabled() is True, "改了输入就该能重新搜索，不许锁在「搜索中」"


def test_results_of_the_current_query_still_land():
    """反面：查询没被改过时结果照常回填（版本校验不得误伤正常路径）。"""
    dialog = M.WeatherCityDialog()
    _type_into(dialog.name_edit, "鹿城")
    M.start_search(
        dialog, "鹿城",
        lambda _query: ([W.CityCandidate(code="101210710", name="鹿城", region="浙江")], ""),
        runner=lambda work: work(),
    )
    assert dialog.chosen_name() == "鹿城"
    assert [dialog.results.item(i).text() for i in range(dialog.results.count())] == ["鹿城"]


# ---------------------------------------------------------------- 注册表
def test_registry_exposes_weather_actions_with_labels_and_icons():
    from pet.context_menus.registry import ACTION_ICONS, ACTION_LABELS

    assert {"weather", "weather_cities"} <= MENU_ACTIONS.ids
    assert ACTION_LABELS["weather"] == "查看天气"
    assert ACTION_LABELS["weather_cities"] == "天气城市"
    assert ACTION_ICONS["weather"] == "weather"
    assert MENU_ACTIONS.default_icon("weather") == "weather"


def test_registry_gates_weather_on_the_callback_and_the_switch():
    class _NoCallback:
        cfg = _Cfg({})

    assert "weather" not in MENU_ACTIONS.available_ids(_NoCallback())

    pet = _Pet({"weather_enabled": True})
    assert {"weather", "weather_cities"} <= MENU_ACTIONS.available_ids(pet)
    assert {"weather", "weather_cities"} <= MENU_ACTIONS.enabled_ids(pet)

    off = _Pet({"weather_enabled": False})
    assert "weather" in MENU_ACTIONS.available_ids(off), "关掉开关只降级为置灰，位置保留"
    assert "weather" not in MENU_ACTIONS.enabled_ids(off)
    assert MENU_ACTIONS.disabled_reason("weather") == "天气功能已在设置中关闭"


def test_modern_default_layout_places_weather_beside_the_other_tools():
    from pet.menu_layout import load_default_menu_layout

    nodes = load_default_menu_layout("modern-default-v1")["nodes"]
    ids = [node["id"] for node in nodes]
    assert "weather" in ids and "weather_cities" in ids
    assert ids.index("weather") + 1 == ids.index("weather_cities")
    weather = next(node for node in nodes if node["id"] == "weather")
    assert weather["visible"] is True and weather["section"] == "tools"


# ---------------------------------------------------------------- 两条 facade
def test_real_petwindow_menu_contains_weather_entries(tmp_path):
    """真 PetWindow + 真建造器：两个天气入口都在现代树里。"""
    import tests.test_sprite_menu_parity as parity
    from pet.config import Config

    config = Config(base=tmp_path)
    win = parity._reference_window(config)
    try:
        menu = QMenu()
        parity.build_modern_menu(menu, win)
        labels = _labels(menu)
        assert "查看天气" in labels and "天气城市" in labels
    finally:
        win.close()


def test_real_petwindow_menu_greys_weather_when_switched_off(tmp_path):
    """真窗口 + 真建造器：总开关关掉后两个天气入口都置灰、不可点。

    置灰门必须落在**共享建造器**里而不是只在新版注册表：否则任何直调共享
    建造器的入口都会在关闭态下仍能进入城市搜索、把城市写进配置（与实际停用
    状态自相矛盾）。
    """
    import tests.test_sprite_menu_parity as parity
    from pet.config import Config

    config = Config(base=tmp_path)
    config.set("weather_enabled", False)
    win = parity._reference_window(config)
    try:
        menu = QMenu()
        parity.build_modern_menu(menu, win)
        show = _find(menu, "查看天气")
        cities = _find(menu, "天气城市")
        assert show.isEnabled() is False, "「查看天气」必须置灰"
        assert cities.isEnabled() is False, "「天气城市」必须置灰（添加城市不可达）"
        assert show.toolTip() == M.WEATHER_DISABLED_REASON
    finally:
        win.close()
