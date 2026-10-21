# -*- coding: utf-8 -*-
"""设置页「按区直找」（2026-10-08）的行为验收。

三件事一起验：

1. **被压平的子分区恢复回来**：域装配原先把旧页的行用 ``findChildren`` 一把捞出、
   整体塞进单张卡片，旧页的「总开关与节奏 / 频率参数 / 触发条件 / 白名单」等子分区
   就此消失。``sections_in_layout_order`` 走真实布局树取回它们（创建顺序 ≠ 布局顺序
   时也得对，见第一个用例的合成页）。
2. **«自动化与联动» 顶层顺序 = 装配表顺序**：倒插（``insertWidget(0/1)``）已删除，
   顺序必须是 待办提醒 → 免打扰 → 主动识屏四组 → 消费统计 → Agent 联动（折叠）→
   文案风格与模板 → 自定义台词（折叠）。
3. **行一个不漏**：拆组只换容器，不换归属；任何一行掉进「待分类（开发期）」都算红。

纪律：断言走真实 ``ModernSettingsDialog`` 这个公开 seam；布局顺序由测试自己走
``layout().itemAt(...)`` 取（不调被测函数自证）。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

import pytest

import pet.modern_settings_dialog as settings_mod
from pet import settings_page_guide
from pet.music import settings as settings_music
from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow, SettingsSection
from pet.settings_widgets import CollapsibleGroup, SettingsTabContainer

app = QApplication.instance() or QApplication([])

#: «自动化与联动» 域顶层顺序（定稿口径，见 ``_rebuild_domain_navigation`` 的装配表）。
_AUTOMATION_ORDER = [
    "待办提醒",
    "免打扰",
    "主动识屏 · 开关与节奏",
    "主动识屏 · 什么时候看",
    "主动识屏 · 看哪些软件",
    "主动识屏 · 细调（高级）",
    "消费统计",
    "box:Agent 联动",
    "文案风格与模板",
    "box:自定义台词（逐事件文案）",
]

#: 同上，去掉主动识屏四组：这四组只在 Windows 构造（``settings_pet_controls`` 的
#: 平台闸门），非 Windows 的装配表里根本没有它们。
_AUTOMATION_ORDER_WITHOUT_PROACTIVE = [
    "待办提醒",
    "免打扰",
    "消费统计",
    "box:Agent 联动",
    "文案风格与模板",
    "box:自定义台词（逐事件文案）",
]

#: 「主动识屏」四组 → 行键（顺序即组内呈现顺序）。
_PROACTIVE_GROUPS = (
    ("主动识屏 · 开关与节奏", ("proactive_enabled", "proactive_preset")),
    ("主动识屏 · 什么时候看",
     ("proactive_require_idle", "proactive_idle_seconds", "proactive_through", "proactive_pre_cue")),
    ("主动识屏 · 看哪些软件",
     ("proactive_whitelist", "proactive_whitelist_add", "proactive_memory_clear")),
    ("主动识屏 · 细调（高级）",
     ("proactive_dry_run", "proactive_dwell", "proactive_cooldown",
      "proactive_min_interval", "proactive_daily_cap", "proactive_free")),
)
_PROACTIVE_TITLES = frozenset(title for title, _keys in _PROACTIVE_GROUPS)


def _has_proactive(dialog) -> bool:
    """本对话框有没有构造出主动识屏控件。

    平台差异不写死在测试里（不判 ``sys.platform``），由**被测对话框的实际构造结果**
    回答：产品哪天改成别的平台也建，这些用例自动跟着跑。
    """
    return hasattr(dialog, "pro_enabled_check")


def _expected_automation_order(dialog) -> list[str]:
    return _AUTOMATION_ORDER if _has_proactive(dialog) else _AUTOMATION_ORDER_WITHOUT_PROACTIVE


def _dialog(tmp_path, monkeypatch, *, include_ai: bool = True):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    return ModernSettingsDialog(Config(base=tmp_path), include_ai=include_ai)


def _page(dialog, domain: str) -> QWidget:
    index = next(i for i in range(dialog.sidebar.count())
                 if dialog.sidebar.item(i).text() == domain)
    return dialog.pages.widget(index)


def _content(dialog, domain: str, tab_key: str | None = None) -> QWidget:
    """域页的可滚动内容；页内标签域取指定标签的页内容。"""
    content = _page(dialog, domain).findChild(QScrollArea, "settingsScroll").widget()
    tabs = content if isinstance(content, SettingsTabContainer) else content.findChild(
        SettingsTabContainer, "settingsTaskTabs")
    if tabs is not None:
        if tab_key is not None:
            tabs.setCurrentKey(tab_key)
        content = tabs.stack.currentWidget()
    return content


def _top_level_titles(content: QWidget) -> list[str]:
    """内容里顶层块的标题，按真实布局顺序；折叠框用 ``box:`` 前缀标出。"""
    titles = []
    layout = content.layout()
    for position in range(layout.count()):
        widget = layout.itemAt(position).widget()
        if isinstance(widget, CollapsibleGroup):
            titles.append(f"box:{widget.toggle.text()}")
        elif isinstance(widget, SettingsSection):
            titles.append(_title_of(widget))
    return titles


def _title_of(section: SettingsSection) -> str:
    if section.toggle is not None:
        return section.toggle.text()
    label = section.findChild(QLabel, "sectionTitle")
    return label.text() if label is not None else ""


def _section(dialog, domain: str, title: str, tab_key: str | None = None) -> SettingsSection:
    content = _content(dialog, domain, tab_key)

    def find(node: QWidget):
        layout = node.layout()
        if layout is None:
            return None
        for position in range(layout.count()):
            widget = layout.itemAt(position).widget()
            if widget is None:
                continue
            if isinstance(widget, CollapsibleGroup):
                hit = find(widget.body)
                if hit is not None:
                    return hit
            elif isinstance(widget, SettingsSection) and _title_of(widget) == title:
                return widget
            elif type(widget) is QWidget:
                # 域装配可能先起一层普通容器再往里放卡片（2026-10-14「语音」域就是：
                # 一张语音报时平卡 + 节日页自建卡片组）。这些容器没有自己的标题，
                # 只是布局层，递归进去找，别让分组在测试眼里"消失"。
                hit = find(widget)
                if hit is not None:
                    return hit
        return None

    found = find(content)
    assert found is not None, f"「{domain}」域里没有标题为「{title}」的分区"
    return found


def _row(dialog, key: str) -> SettingRow:
    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


# ------------------------------------------------------------------ 布局顺序辅助函数
def test_layout_order_helper_follows_the_layout_not_the_creation_order():
    """创建顺序与布局顺序不一致时，取到的必须是用户在页面上看到的顺序。

    旧机制（``findChildren``）给的是创建顺序：两处偶然一致，一处不一致就静默错位
    ——这正是「按区直找」要拆掉的隐患，故用合成页正面钉住。
    """
    page = QWidget()
    layout = QVBoxLayout(page)
    built = [
        SettingsSection(title, [SettingRow(key, title, "", QWidget(), page)], page)
        for key, title in (("c", "第三"), ("a", "第一"), ("b", "第二"))
    ]
    for section in (built[1], built[2], built[0]):  # 布局顺序 第一 / 第二 / 第三
        layout.addWidget(section)

    assert page.findChildren(SettingsSection) == built, "合成页的创建顺序必须是 第三/第一/第二"
    assert [title for title, _rows in settings_page_guide.sections_in_layout_order(page)] == [
        "第一", "第二", "第三"]
    assert [row.objectName() for row in settings_page_guide.rows_in_layout_order(page)] == [
        "settingRow_a", "settingRow_b", "settingRow_c"]


def test_rows_in_layout_order_keeps_rows_outside_any_section():
    """没被分区收走的行不许静默丢失（按创建顺序垫在后面）。"""
    page = QWidget()
    layout = QVBoxLayout(page)
    loose = SettingRow("loose", "散行", "", QWidget(), page)
    section = SettingsSection("有主的", [SettingRow("owned", "有主", "", QWidget(), page)], page)
    layout.addWidget(section)
    layout.addWidget(loose)

    assert [row.objectName() for row in settings_page_guide.rows_in_layout_order(page)] == [
        "settingRow_owned", "settingRow_loose"]


# --------------------------------------------------------------------- 主动识屏四组
def test_proactive_rows_are_split_back_into_four_groups(tmp_path, monkeypatch):
    """15 行拆成 4 组（开关与节奏 / 什么时候看 / 看哪些软件 / 细调（高级）），一个不漏。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        if not _has_proactive(dialog):
            pytest.skip("主动识屏控件只在 Windows 构造（settings_pet_controls 的平台闸门）")
        titles = [title for title, _rows in settings_page_guide.sections_in_layout_order(
            _content(dialog, "自动化与联动"))]
        for title, _keys in _PROACTIVE_GROUPS:
            assert title in titles, f"「{title}」必须作为独立分区出现在页面上"

        for title, keys in _PROACTIVE_GROUPS:
            section = _section(dialog, "自动化与联动", title)
            assert [row.objectName() for row in section.rows] == [
                f"settingRow_{key}" for key in keys], f"「{title}」的成员或顺序不符"
        # 只有「细调」是高级折叠组（低频参数一层披露）。
        assert _section(dialog, "自动化与联动", "主动识屏 · 细调（高级）").advanced is True
        for title, _keys in _PROACTIVE_GROUPS[:3]:
            assert _section(dialog, "自动化与联动", title).advanced is False

        # 15 行全部有主、没有漏进「待分类（开发期）」。
        total = sum(len(keys) for _title, keys in _PROACTIVE_GROUPS)
        assert total == 15, "分组表自身必须覆盖旧页全部 15 行"
        for _title, keys in _PROACTIVE_GROUPS:
            for key in keys:
                assert _page(dialog, "自动化与联动").isAncestorOf(_row(dialog, key))
        assert "待分类（开发期）" not in [
            label.text() for label in dialog.findChildren(QLabel)]
    finally:
        dialog.close()


# --------------------------------------------------------- 自动化与联动：顶层顺序重排
def test_automation_domain_top_level_order_is_the_assembly_table(tmp_path, monkeypatch):
    """顶层顺序 = 装配表顺序；「消费统计」不再靠 insertWidget 倒插到最前面。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        assert _top_level_titles(_content(dialog, "自动化与联动")) == _expected_automation_order(dialog)
        # 折叠框仍在表里、默认收起（口径未变）。
        assert dialog.agent_link_box.is_expanded() is False
        assert dialog.dialogue_phrases_box.is_expanded() is False
    finally:
        dialog.close()


def test_agent_box_and_phrase_box_are_prebuilt_widgets_in_the_table(tmp_path, monkeypatch):
    """两个折叠框作为已构造好的 QWidget 直接进装配表（``page_content`` 可收 widget）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        box = dialog.agent_link_box
        content = _content(dialog, "自动化与联动")
        assert box.parentWidget() is content, "折叠框必须被 page_content 收养进页内容"
        assert dialog.dialogue_phrases_box.parentWidget() is content
        assert box.isAncestorOf(_row(dialog, "agent_sound_enabled"))
        assert not box.isAncestorOf(_row(dialog, "agent_cost")), "消费统计留在折叠框外"
    finally:
        dialog.close()


# ------------------------------------------------------------------- 语音域：3 + 3 组
def test_voice_and_festival_domains_split_into_groups(tmp_path, monkeypatch):
    """语音报时与节日提醒各自**自建卡片**交给「语音」域：语音报时整组占一张平卡
    （含基础设置 / 合成后端折叠编排 / 凭据组 / 语音 / 台词歌词的全部行），节日提醒按
    何时提醒 / 提醒哪些日子 / 怎么提醒 / 你的生日 / 少用项 分卡。行数与行归属一个不变
    ——只是不再由本域按「一组行」拼小分组（那样页面容器会散架、分组标题会跟着丢）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        # 「语音」域现在是一层自建容器：顶层放一张「语音报时」平卡 + 节日页自建的卡片组。
        # _top_level_titles 只看直接子块，所以这里分两段断言：顶层第一块是语音报时，
        # 其余（节日提醒那几张）在它下面的子容器里。
        top = _top_level_titles(_content(dialog, "语音"))
        assert top[:1] == ["语音报时"], top
        all_titles = [label.text() for label in _content(dialog, "语音").findChildren(QLabel, "sectionTitle")]
        for title in ("语音报时", "何时提醒", "提醒哪些日子", "怎么提醒", "你的生日", "少用项"):
            assert title in all_titles, f"「语音」域丢了卡片：{title}"
        # 语音报时整组：既有行一个不少，外加 provider 化带来的引擎/后端字段行。
        voice_rows = [row.objectName().replace("settingRow_", "")
                      for row in _section(dialog, "语音", "语音报时").rows]
        for key in ("voice_chime_enabled", "voice_chime_schedule", "voice_chime_custom_times",
                    "voice_chime_volume", "voice_chime_show_bubble", "voice_chime_show_quote",
                    "voice_chime_custom_quotes_zh", "voice_chime_custom_quotes_en",
                    "voice_chime_preview", "voice_chime_voice", "voice_chime_rate",
                    "voice_chime_pitch"):
            assert key in voice_rows, f"「语音报时」丢了既有行：{key}"
        # 引擎下拉与四家后端的参数行都由 provider 声明生成（加后端不用改设置页）
        for key in ("voice_chime_tts_backend", "voice_chime_tts_fallback",
                    "voice_chime_doubao_speaker", "voice_chime_doubao_pitch",
                    "voice_chime_mimo_model", "voice_chime_mimo_voice", "voice_chime_mimo_style",
                    "voice_chime_sapi_voice", "voice_chime_sapi_rate"):
            assert key in voice_rows, f"「语音报时」缺后端字段行：{key}"
        # 节日提醒按卡归属
        expected = {
            "何时提醒": ("festival_reminder_enabled", "festival_reminder_mode",
                     "festival_reminder_count", "festival_reminder_times"),
            "提醒哪些日子": ("festival_reminder_cn", "festival_reminder_solar_terms",
                        "festival_reminder_west"),
            "怎么提醒": ("festival_reminder_speak", "festival_reminder_animation",
                     "festival_reminder_show_quote"),
            "你的生日": ("festival_birthday",),
            "少用项": ("festival_custom_quotes_cn", "festival_custom_quotes_west",
                    "festival_preview"),
        }
        for title, keys in expected.items():
            rows = [row.objectName().replace("settingRow_", "")
                    for row in _section(dialog, "语音", title).rows]
            for key in keys:
                assert key in rows, f"「{title}」丢了行：{key}"
        # 「立即试听」仍在 SettingRow 里（历史事故：没包进行里就不会被域收集搬运）。
        assert _page(dialog, "语音").isAncestorOf(_row(dialog, "voice_chime_preview"))
        assert "待分类（开发期）" not in [
            label.text() for label in dialog.findChildren(QLabel)]
    finally:
        dialog.close()


# --------------------------------------------------- 桌面胶囊：压平仍是定稿口径（不动）
def test_desktop_island_keeps_its_single_flattened_card(tmp_path, monkeypatch):
    """「桌面胶囊（灵动岛）」的压平是定稿口径（见装配处注释），本刀不拆——只有探测
    间隔（高级）一项另立。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        assert _top_level_titles(_content(dialog, "桌面组件")) == [
            "桌面胶囊（灵动岛）", "网络延迟检测（高级）"]
        card = _section(dialog, "桌面组件", "桌面胶囊（灵动岛）")
        names = {row.objectName() for row in card.rows}
        assert {"settingRow_dynamic_island_enabled", "settingRow_island_network",
                "settingRow_island_cpu"} <= names, "岛行不分家，整表在同一张卡里"
        assert "settingRow_network_probe_interval" not in names
    finally:
        dialog.close()


# ------------------------------------------------------------------ 页内分区跳转条
def _select(dialog, domain: str):
    index = next(i for i in range(dialog.sidebar.count())
                 if dialog.sidebar.item(i).text() == domain)
    dialog.sidebar.setCurrentRow(index)
    app.processEvents()
    page = dialog.pages.widget(index)
    bars = page.findChildren(QWidget, "settingsPageAnchors")
    assert len(bars) == 1, f"「{domain}」页必须有且只有一条跳转条，实得 {len(bars)}"
    return page, bars[0]


def _chips(bar) -> list[QPushButton]:
    return bar.findChildren(QPushButton, "settingsPageAnchor")


def _visible_chips(bar) -> list[QPushButton]:
    return [chip for chip in _chips(bar) if not chip.isHidden()]


def _blocks_of(domain_content: QWidget) -> list[tuple[str, QWidget]]:
    """页内容顶层块（分区 / 折叠框），测试自己走布局顺序（不调被测函数自证）。

    2026-10-14 起域装配可能先起一层**普通容器**再把卡片放进去（「语音」域就是：
    一张「语音报时」平卡 + 节日页自建的卡片组）。这层没有标题、只是布局层，所以
    这里也要穿透——否则测试看到的块比用户看到的少，会得出"跳转条格数不对"的假结论。
    """
    blocks = []

    def walk(node: QWidget) -> None:
        layout = node.layout()
        if layout is None:
            return
        for position in range(layout.count()):
            widget = layout.itemAt(position).widget()
            if isinstance(widget, CollapsibleGroup):
                blocks.append((widget.toggle.text(), widget))
            elif isinstance(widget, SettingsSection):
                blocks.append((_title_of(widget), widget))
            elif type(widget) is QWidget:
                walk(widget)

    walk(domain_content)
    return blocks


def _all_groups(dialog) -> list[tuple[str, QWidget]]:
    """对话框里所有分区 / 折叠框（含折叠框里与普通容器层里的子分区），按域页布局顺序。"""
    found: list[tuple[str, QWidget]] = []

    def walk(node: QWidget) -> None:
        for title, widget in _blocks_of(node):
            found.append((title, widget))
            if isinstance(widget, CollapsibleGroup):
                walk(widget.body)
            # 普通容器层：域装配可能先起一层（「语音」域 = 平卡 + 自建卡片组），
            # 里面的卡片也要算进来，否则它们的描述会被误判成"已不存在"。
            elif type(widget) is QWidget:
                walk(widget)

    for index in range(dialog.pages.count()):
        domain = dialog.sidebar.item(index).text()
        scroll = _page(dialog, domain).findChild(QScrollArea, "settingsScroll")
        if scroll is None:
            continue
        tabs = settings_page_guide.tab_container(scroll.widget())
        if tabs is None:
            walk(scroll.widget())
            continue
        for key in tabs.keys():
            tabs.setCurrentKey(key)
            walk(tabs.stack.currentWidget())
        tabs.setCurrentKey(tabs.keys()[0])
    return found


def _tab_layer_height(scroll: QScrollArea) -> int:
    """页内标签域在**当前标签页**之上真实占掉的那一层（标签栏 + 容器布局间距）。

    **量落版后的实测几何**（``tab_bar.height()`` + 容器布局自己的 ``spacing()``），不抄
    产品那句口径、也不用 hint 相减：视口是**整块滚动内容**（标签容器）的视口，
    漏掉这一层就会在 ``页高 <= 视口 < 页高 + 层高`` 这一小段上说"装得下"（实测 1100 宽、
    桌宠／动作与碰撞：层高 52 = 标签栏 40 + 间距 12）。hint 相减（容器最小高 − 页最小高）
    在"刚改过留白、布局还没落定"的一拍会被缓存值污染（实测量到 1336、真值 52），故取实测。
    """
    tabs = settings_page_guide.tab_container(scroll.widget())
    if tabs is None:
        return 0
    layout = tabs.layout()
    return tabs.tab_bar.height() + (layout.spacing() if layout is not None else 0)


def _needed_height(scroll: QScrollArea) -> int:
    """**整块滚动内容**的真实高度——测试自己量，不调被测函数自证。

    **页尾留白不计入**：``_PastEndPadding`` 挂在内容布局的下外边距上（不是一块占位
    控件），所以“真实内容高”要从量出来的高度里减掉它。这条正是产品的判据（见
    ``settings_page_guide`` 的 ``_SettingsAnchorBar``）：留白**跟着视口**走，把它算进去
    等于拿“跳转条自己造成的视口高度”回答“要不要显示跳转条”。

    页内标签域要取**当前标签页**：内容自己就是 ``SettingsTabContainer`` 时 ``findChild``
    找不到自己，退回容器会把内容高量成容器的高（实测 1750 vs 产品口径 1255）。
    但**整块内容**才是滚动视口对着的那个东西，所以标签栏那一层要加回来
    （:func:`_tab_layer_height`）：只量标签页自己，等于把产品的一个错口径复制进测试。
    """
    content = scroll.widget()
    tabs = settings_page_guide.tab_container(content)
    root = tabs.stack.currentWidget() if tabs is not None else content
    bottom_margin = root.layout().contentsMargins().bottom()
    page = max(
        root.minimumSizeHint().height(),
        root.heightForWidth(scroll.viewport().width()),
    ) - bottom_margin
    return page + _tab_layer_height(scroll)


def _available_height(scroll: QScrollArea, bar: QWidget) -> int:
    """判据里的可用高：当前视口 **加上跳转条自己占掉的那段**。

    跳转条显着的时候视口已经被它压矮了一截；不加回来就等于把结果当输入（两种状态各自
    自洽 ⇒ 往返抖）。判据因此在两个状态下取到同一个可用高。
    """
    return scroll.viewport().height() + (0 if bar.isHidden() else bar.height())


def _expect_bar_hidden(bar: QWidget, blocks: list, scroll: QScrollArea) -> bool:
    """按测得的几何算出跳转条该不该藏（与产品同一判据，但自己量）。"""
    return not (len(blocks) >= 4 and _needed_height(scroll) > _available_height(scroll, bar))


def _set_bar_hidden(bar, hidden: bool) -> None:
    """强行把跳转条设成某个状态，然后把判据跑完（它推到下一拍，且要等布局落定）。"""
    bar.setVisible(not hidden)
    for _turn in range(3):
        app.processEvents()


def _settle(turns: int = 3) -> None:
    """把布局推平：尺寸变化与跳转条的重判都要再落一拍（见 ``_SettingsAnchorBar``）。"""
    for _turn in range(turns):
        app.processEvents()


def test_anchor_bar_shows_only_on_pages_that_actually_scroll(tmp_path, monkeypatch):
    """「当前页（或当前标签）≥4 个区」**且内容真的超过视口**才显示跳转条。

    短页（一屏看得完）上那条横排 chip 只是把页头重复一遍：它指向的每一个区都已经在
    视口里，点它没有任何位移——用户要的是“短页不摆跳转条，长页才摆”。

    **两个尺寸都要过**：720×500（最小窗口）下区数够的页几乎都真的装不下，只在这一档
    量不到"短页不摆"这件事；1000×1000 下「常规」装得下、「语音」（6 区）装不下，
    同一次装配里同时给出两种判据结果（区数按**实际构造**算：非 Windows 的常规只有 3 区，
    那儿"不摆"是区数不够而不是短页，仍由本用例的动态判据覆盖）。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()
        for width, height in ((720, 500), (1000, 1000)):
            dialog.resize(width, height)
            _settle()
            for index in range(dialog.sidebar.count()):
                domain = dialog.sidebar.item(index).text()
                dialog.sidebar.setCurrentRow(index)
                _settle()
                bar = dialog.pages.widget(index).findChild(QWidget, "settingsPageAnchors")
                assert bar is not None, f"「{domain}」页缺少跳转条（不足 4 区时它自己收起）"
                blocks = _blocks_of(_content(dialog, domain))
                assert len(_chips(bar)) == len(blocks), f"「{domain}」chip 数必须等于区数"
                scroll = _page(dialog, domain).findChild(QScrollArea, "settingsScroll")
                expected = _expect_bar_hidden(bar, blocks, scroll)
                assert bar.isHidden() is expected, (
                    f"{width}×{height}「{domain}」{len(blocks)} 个区、内容高 "
                    f"{_needed_height(scroll)}px / 可用 {_available_height(scroll, bar)}px，"
                    f"跳转条的显隐判反了")
        # 1000×1000 下必须真的同时出现"该藏"和"该显"两档（否则本用例会假绿）。
        tall_hidden, tall_shown = set(), set()
        dialog.resize(1000, 1000)
        _settle()
        for index in range(dialog.sidebar.count()):
            domain = dialog.sidebar.item(index).text()
            dialog.sidebar.setCurrentRow(index)
            _settle()
            bar = dialog.pages.widget(index).findChild(QWidget, "settingsPageAnchors")
            if bar is not None and not bar.isHidden():
                tall_shown.add(domain)
            else:
                tall_hidden.add(domain)
        assert tall_shown and tall_hidden, (
            f"1000×1000 下只出现了一档（藏在 {sorted(tall_hidden)} / 显示 {sorted(tall_shown)}），"
            f"本用例量不到短页与长页的差别")
    finally:
        dialog.close()


def test_anchor_bar_converges_in_both_directions(tmp_path, monkeypatch):
    """显隐是**收敛**的：把状态强行反过来，下一拍必须回到同一个答案（不来回抖）。

    这就是“判定真实内容高度、不把页尾留白算进去、也不丢下自己占的那段”的用处：跳转条
    自己占了页头一段高度，判据把它加回来之后与当前显隐无关，两个方向各只有一个不动点。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        # 1000×1000 下「常规」装得下（534px < 视口），「语音」装不下（2456px）——
        # 一个对话框里同时拿到短页与长页两个样本。
        dialog.resize(1000, 1000)
        dialog.show()
        app.processEvents()
        # 一轮一个域（都停在**当前页**上：非当前页不参与布局，量不到真几何）。
        for domain, expect_hidden in (("常规", True), ("语音", False)):
            page, bar = _select(dialog, domain)
            scroll = page.findChild(QScrollArea, "settingsScroll")
            blocks = _blocks_of(_content(dialog, domain))
            assert _expect_bar_hidden(bar, blocks, scroll) is expect_hidden, (
                f"前提：「{domain}」的显隐与量出来的几何不符（内容 {_needed_height(scroll)}px"
                f" / 可用 {_available_height(scroll, bar)}px）")
            for _round in range(3):  # 反复强行反过来：每次都要自己回到同一答案
                _set_bar_hidden(bar, not expect_hidden)
                assert bar.isHidden() is expect_hidden, (
                    f"「{domain}」被强行设成相反状态之后没有回到 {expect_hidden}")
    finally:
        dialog.close()


def test_anchor_bar_follows_the_window_height(tmp_path, monkeypatch):
    """同一个域：窗口高到装得下就不摆跳转条，缩到装不下再摆回来。

    加高那一档按**量出来的**开销算（``dialog.height() - 可用高`` = 页头 + 页边距 + 跳转条），
    不写死"加多少像素够"：字体与平台不同，写死的常数今天恰好够、明天就成假绿。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(1000, 1000)
        dialog.show()
        app.processEvents()
        page, bar = _select(dialog, "语音")
        scroll = page.findChild(QScrollArea, "settingsScroll")
        blocks = _blocks_of(_content(dialog, "语音"))
        assert _expect_bar_hidden(bar, blocks, scroll) is False, (
            f"前提：1000 高时语音域仍然装不下（内容 {_needed_height(scroll)}px / "
            f"可用 {_available_height(scroll, bar)}px）")

        overhead = dialog.height() - _available_height(scroll, bar)
        dialog.resize(1000, _needed_height(scroll) + overhead + 120)
        app.processEvents()
        assert _needed_height(scroll) <= _available_height(scroll, bar), (
            f"前提：加高到 {dialog.height()} 之后语音域装得下（内容 "
            f"{_needed_height(scroll)}px / 可用 {_available_height(scroll, bar)}px）")
        assert bar.isHidden() is True, "装得下的页面不该摆跳转条"

        dialog.resize(720, 500)
        app.processEvents()
        assert _expect_bar_hidden(bar, blocks, scroll) is False
        assert bar.isHidden() is False, "缩到装不下之后跳转条要回来"

        dialog.resize(1000, _needed_height(scroll) + overhead + 120)
        app.processEvents()
        assert bar.isHidden() is True, "放大回去之后跳转条要收掉"
    finally:
        dialog.close()


#: 临界几何（「跳转条藏掉之后视口才长回来」那一段）用哪个域页量。
#:
#: **必须是三平台都有 ``ANCHOR_MIN_BLOCKS`` 个以上「区」的页**：常规域的第 4 个区
#: 「直播捕获（高级）」整组是 Windows 专属（``settings_pet_controls`` 的平台闸门），
#: 非 Windows 上常规域只剩 3 个区 ⇒ 那一页的跳转条恒藏，量不到带（三端必红）。
#: 「自动化与联动」三平台都够：非 Windows 6 个区（``_AUTOMATION_ORDER_WITHOUT_PROACTIVE``）、
#: Windows 10 个区（``_AUTOMATION_ORDER``），两边都由顶层顺序用例钉着。
BAND_DOMAIN = "自动化与联动"


def _gap_band(tmp_path, monkeypatch, *, domain: str = BAND_DOMAIN, width: int = 1100):
    """量出「跳转条自己那段高度」构成的双稳态带，返回 ``(带内起点窗口高, 带宽, 内容高)``。

    带 = 隐态视口 ∈ ``[内容高, 内容高 + 跳转条高)`` 的那一段窗口高。这一段里判据的答案
    是「藏」（藏掉就装得下），但藏掉那一拍视口还没长回来（量到的是 ``隐态视口 − 跳转条高``），
    ``hidden`` 分支又不补自己那段 ⇒ 判据说装不下 ⇒ 又显示回来。实测 1100 宽、离屏平台下
    正是 **1342…1369** 这一段（内容高 1264、跳转条自己 28px；非 Windows 腿这一页矮一成，
    带跟着上移），这一段里旧实现停在显态、页尾多出 1236…1267px 空白。

    两个量都按**实测几何**推，不写死像素：字体/平台一变，写死的尺寸今天落在带内、
    明天就落在带外，用例会假绿。基准窗高也由实测反推（见下），不写死——不同的域页
    内容高差着上千像素，写死一个"装得下"的高度只对某一页成立。
    """
    tall = _dialog(tmp_path, monkeypatch)
    try:
        tall.resize(width, 900)
        tall.show()
        _settle(4)
        page, bar = _select(tall, domain)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        # 先把基准窗拉到一个**按实测**装得下的高度：内容高 + 窗口开销（页头/页边距/
        # 跳转条自己那段）。判据的前提是"装得下时它本来就不摆"，写死 900 只对短页成立。
        needed = _needed_height(scroll)
        overhead = tall.height() - scroll.viewport().height()
        requested = needed + overhead + 60
        tall.resize(width, requested)
        _settle(6)
        if tall.height() < requested:
            # 真机平台会把顶层窗夹到屏幕高（离屏平台不夹，CI 与套件默认都走离屏）。
            # 夹住就量不到带 —— 如实跳过，不伪装成产品缺陷。
            pytest.skip(f"本机窗口拉不到这条带所需的窗口高（请求 {requested}、"
                        f"实测夹到 {tall.height()}）；离屏平台下可量")
        assert bar.isHidden() is True, "前提：基准窗里装得下，跳转条本来就不摆"
        needed = _needed_height(scroll)
        overhead = tall.height() - scroll.viewport().height()
    finally:
        tall.close()
    short = _dialog(tmp_path, monkeypatch)
    try:
        short.resize(width, 520)
        short.show()
        _settle(4)
        _page, bar = _select(short, domain)
        assert bar.isHidden() is False, "前提：520 高时装不下，跳转条本来就在"
        slack = bar.height()
    finally:
        short.close()
    assert slack > 0, "跳转条必须真的占掉一段高度，否则量不到这条带"
    return needed + overhead, slack, needed


def _visibility_history(bar, turns: int = 10) -> list[bool]:
    """连拍若干拍记下显隐（``True`` = 藏着）：用来钉住「两拍之间不许来回翻」。"""
    marks = []
    for _ in range(turns):
        app.processEvents()
        marks.append(bar.isHidden())
    return marks


def _assert_bar_matches_criterion(dialog, domain: str, *, note: str = "") -> None:
    """判据在落定几何上必须与跳转条的实际显隐一致（测试自己量，不调被测函数自证）。"""
    page, bar = _select(dialog, domain)
    scroll = page.findChild(QScrollArea, "settingsScroll")
    expected = _expect_bar_hidden(bar, _blocks_of(_content(dialog, domain)), scroll)
    assert bar.isHidden() is expected, (
        f"「{domain}」{note}：内容高 {_needed_height(scroll)}px / 可用 "
        f"{_available_height(scroll, bar)}px，跳转条显隐判反了")


def test_anchor_bar_does_not_stick_shown_inside_its_own_gap(tmp_path, monkeypatch):
    """带内最坏点起、每一档都不许停在显态（跳过自己占的那 28px 再重判就是错的）。

    视口在跳转条藏掉之后要长回自己那一段，但页头那次重排原本要等下一轮事件循环；
    判据若在这一拍重判，量到的还是矮视口（``hidden`` 分支不补自己那段）⇒ 判据说装不下
    ⇒ 又显示回来。两个稳态里会停在错的显态：用户看到一条该收起的跳转条，页尾还多出
    一大块空白（1100 宽、「自动化与联动」实测：带内 1342…1369 停在显态，页尾空白
    1236…1267px）。
    """
    base, slack, needed = _gap_band(tmp_path, monkeypatch)
    for extra in range(0, slack, 6):
        dialog = _dialog(tmp_path, monkeypatch)
        try:
            dialog.resize(1100, base + extra)
            dialog.show()
            _settle(6)
            page, bar = _select(dialog, BAND_DOMAIN)
            scroll = page.findChild(QScrollArea, "settingsScroll")
            assert needed <= scroll.viewport().height() + (0 if bar.isHidden() else bar.height()), (
                f"前提：1100×{base + extra} 装得下（内容 {needed}px / 可用 "
                f"{_available_height(scroll, bar)}px）")
            assert bar.isHidden() is True, (
                f"1100×{base + extra}：装得下却停在显态（视口还没长回来就重判）")
            assert _past_end_padding(dialog, BAND_DOMAIN) == 0, (
                f"1100×{base + extra}：装得下的页面不该留页尾空白")
            assert _visibility_history(bar) == [True] * 10, "稳定态不许来回翻"
        finally:
            dialog.close()


def test_anchor_bar_converges_from_both_initial_states_inside_the_gap(tmp_path, monkeypatch):
    """带内两种初始状态都要落到同一个答案。

    - **新建**：按这个尺寸直接建窗（构造期判据自己走一遍；最坏点，隐态视口 == 内容高）；
    - **带着显态拉大**：先在小窗口里把跳转条正常摆出来，再 resize 到带内上沿——这是
      用户的真实路径（拉窗口），状态带着走，必须自己收掉。
    """
    base, slack, needed = _gap_band(tmp_path, monkeypatch)
    for start in ("fresh", "carried"):
        dialog = _dialog(tmp_path, monkeypatch)
        try:
            if start == "carried":
                dialog.resize(1100, base - 200)
                dialog.show()
                _settle(6)
                _page_widget, bar = _select(dialog, BAND_DOMAIN)
                scroll = _page_widget.findChild(QScrollArea, "settingsScroll")
                assert needed > scroll.viewport().height(), "前提：小窗口里装不下"
                assert bar.isHidden() is False, "前提：小窗口里跳转条本来就摆着"
                dialog.resize(1100, base + slack - 1)
            else:
                dialog.resize(1100, base)
                dialog.show()
            _settle(6)
            _assert_bar_matches_criterion(dialog, BAND_DOMAIN, note=f"{start} 初始态")
            _page_widget, bar = _select(dialog, BAND_DOMAIN)
            assert bar.isHidden() is True, f"{start}：带内该收起来"
            assert _visibility_history(bar) == [True] * 10, f"{start}：稳定态不许来回翻"
        finally:
            dialog.close()


def test_anchor_bar_holds_across_jump_resize_and_domain_navigation(tmp_path, monkeypatch):
    """按这个尺寸开窗 → 互动（点 chip 跳转 / 换页内标签）→ resize → 换域，判据一路成立。

    这条链路每一步都会改页高或滚动范围：跳转会展开收起的高级组、换标签换一组区、
    换域换一整页、resize 改视口。每一步之后都要重新收敛到「装得下就收、装不下才摆」，
    不许停在上一轮的答案上——带内那一段正是最容易停错的地方。
    """
    base, _slack, _needed = _gap_band(tmp_path, monkeypatch)
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(1100, base)
        dialog.show()
        _settle(6)
        # 开窗这一刀就是带内最坏点（视口还没长回来时判据会把自己显示回来）
        _assert_bar_matches_criterion(dialog, BAND_DOMAIN, note="开窗")
        # 互动 1：跳到收起的高级组（展开会改页高，滚动范围也跟着变）
        _page_widget, bar = _select(dialog, "AI 与对话")
        next(chip for chip in _chips(bar) if chip.text() == "生成参数（高级）").click()
        _settle(4)
        _assert_bar_matches_criterion(dialog, "AI 与对话", note="跳转后")
        # 互动 2：换页内标签（每页一组区，显隐门槛按该标签重算）
        _page_widget, bar = _select(dialog, "桌宠")
        tabs = _page(dialog, "桌宠").findChild(SettingsTabContainer, "settingsTaskTabs")
        for key in tabs.keys():
            tabs.setCurrentKey(key)
            _settle(4)
            _assert_bar_matches_criterion(dialog, "桌宠", note=f"标签 {key}")
        # resize：缩到装不下再拉回带内（状态带着走）
        _page_widget, bar = _select(dialog, BAND_DOMAIN)
        dialog.resize(1100, base - 200)
        _settle(6)
        _assert_bar_matches_criterion(dialog, BAND_DOMAIN, note="缩到装不下")
        dialog.resize(1100, base)
        _settle(6)
        _assert_bar_matches_criterion(dialog, BAND_DOMAIN, note="拉回带内")
        _page_widget, bar = _select(dialog, BAND_DOMAIN)
        assert bar.isHidden() is True, "拉回带内之后该收起来"
        assert _past_end_padding(dialog, BAND_DOMAIN) == 0, "装得下的页面不该留页尾空白"
        # 导航：带内再换域、再换回来，答案不许跟着上一页走
        for domain in ("互动", BAND_DOMAIN, "语音", BAND_DOMAIN):
            _assert_bar_matches_criterion(dialog, domain, note="带内换域")
        _page_widget, bar = _select(dialog, BAND_DOMAIN)
        assert bar.isHidden() is True
        assert _visibility_history(bar) == [True] * 10, "稳定态不许来回翻"
    finally:
        dialog.close()


# ------------------------------------------------------- 页内标签域：整块内容的判据
#: 页内标签域那条带用哪个标签页量：桌宠／动作与碰撞 6 个区，三平台都够
#: （同域的 ``display_music`` 只有 3 个区，跳转条恒藏，量不出带；见
#: ``test_anchor_bar_rebuilds_when_the_in_page_tab_changes``）。
TAB_BAND_DOMAIN = "桌宠"
TAB_BAND_KEY = "motion_collision"
#: 带内用来验「一次点击顶对齐」的普通分区（不用先展开，位置又在页尾附近）。
TAB_BAND_TARGET = "多开碰撞"


def _tabs_of(dialog, domain: str) -> SettingsTabContainer:
    """域页的页内标签容器（不是标签域就断言失败——本用法只在标签域上）。"""
    scroll = _page(dialog, domain).findChild(QScrollArea, "settingsScroll")
    tabs = settings_page_guide.tab_container(scroll.widget())
    assert tabs is not None, f"「{domain}」应当是页内标签域"
    return tabs


def _tab_band(tmp_path, monkeypatch, *, width: int = 1100) -> tuple[int, int, int]:
    """页内标签页那条「漏掉标签那一层」的带起点：``(起始窗口高, 标签页高, 层高)``。

    带 = 隐态视口 ∈ ``[标签页高, 标签页高 + 层高)``：这一段里只量标签页的判据答「装得下」
    而整块滚动内容（标签页 + 标签栏那一层）其实要滚。窗口高按**实测几何**推：
    ``起始窗高 = 标签页高 + 隐态窗口开销``（开销要在跳转条不在的那一态量才是常数）。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(width, 900)
        dialog.show()
        _settle(4)
        page, bar = _select(dialog, TAB_BAND_DOMAIN)
        _tabs_of(dialog, TAB_BAND_DOMAIN).setCurrentKey(TAB_BAND_KEY)
        _settle(4)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        layer = _tab_layer_height(scroll)
        page_height = _needed_height(scroll) - layer
        # 先按实测把窗口拉到真的装得下（跳转条收起来），再在那一态量窗口开销。
        requested = page_height + layer + (dialog.height() - scroll.viewport().height()) + 240
        dialog.resize(width, requested)
        _settle(6)
        if dialog.height() < requested:
            # 真机平台会把顶层窗夹到屏幕高（离屏平台不夹）：夹住就量不到带。
            pytest.skip(f"本机窗口拉不到这条带所需的窗口高（请求 {requested}、"
                        f"实测夹到 {dialog.height()}）；离屏平台下可量")
        assert bar.isHidden() is True, "前提：基准窗里整块内容装得下，跳转条本来就不摆"
        overhead = dialog.height() - scroll.viewport().height()
    finally:
        dialog.close()
    assert layer > 0, "页内标签域必须有标签那一层，否则量不到这条带"
    return page_height + overhead, page_height, layer


def test_tab_page_bar_counts_the_tab_layer_not_just_the_page(tmp_path, monkeypatch):
    """页内标签域：「装得下吗」按**整块滚动内容**答（标签页 + 标签栏那一层）。

    只量当前标签页会漏掉标签容器上面那层「标签栏 + 间距」，于是 ``标签页高 <= 视口 <
    整块内容高`` 这一小段答成"装得下"：跳转条收起来，而用户明明要滚才看得完（实测 1100 宽、
    桌宠／动作与碰撞：标签页 1144、层高 52、整块 1196，窗口高 1222…1278）。

    两种到达方向（从装得下的大窗缩进来 / 从装不下的小窗拉进来）都要落在"摆着"，
    换标签再换回来也不许把答案带走。
    """
    start, page_height, layer = _tab_band(tmp_path, monkeypatch)
    for how in ("down", "up"):
        dialog = _dialog(tmp_path, monkeypatch)
        try:
            tall = how == "down"
            dialog.resize(1100, start + (layer + 240 if tall else -240))
            dialog.show()
            _settle(6)
            page, bar = _select(dialog, TAB_BAND_DOMAIN)
            tabs = _tabs_of(dialog, TAB_BAND_DOMAIN)
            tabs.setCurrentKey(TAB_BAND_KEY)
            _settle(4)
            scroll = page.findChild(QScrollArea, "settingsScroll")
            assert bar.isHidden() is tall, (
                f"前提：{'大窗里整块内容装得下' if tall else '小窗里整块内容装不下'}，"
                f"跳转条该{'收起来' if tall else '摆着'}")

            dialog.resize(1100, start)
            _settle(6)
            assert _needed_height(scroll) > _available_height(scroll, bar), (
                f"前提：到达带内后整块内容仍超过视口（整块 {_needed_height(scroll)}px / 可用 "
                f"{_available_height(scroll, bar)}px：标签页 {page_height}px + 层 {layer}px）")
            assert bar.isHidden() is False, (
                f"{how}：整块内容要滚，跳转条不该收起来（只量标签页会漏掉那 {layer}px）")
            assert _visibility_history(bar) == [False] * 10, "稳定态不许来回翻"

            # 换标签：门槛按该标签重算，答案不许跟着上一个标签走。
            tabs.setCurrentKey("display_music")
            _settle(4)
            assert bar.isHidden() is True, "只有 3 个区的标签页不摆跳转条"
            tabs.setCurrentKey(TAB_BAND_KEY)
            _settle(4)
            assert bar.isHidden() is False, "换回 6 个区的标签页要回来"
        finally:
            dialog.close()


def test_tab_page_last_group_can_be_top_aligned_inside_that_band(tmp_path, monkeypatch):
    """带内点页尾那几组的 chip 也要一次顶对齐：页尾留白同样按整块内容答「装不装得下」。

    只量标签页时留白在这一段是 0，滚动范围只剩几十像素：跳转条摆着、点下去却顶不到
    y=8（实测 1100 宽、桌宠／动作与碰撞、窗口高 1264：点「多开碰撞」只滚到 52，目标 979）。
    """
    start, _page_height, layer = _tab_band(tmp_path, monkeypatch)
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(1100, start)
        dialog.show()
        _settle(6)
        page, bar = _select(dialog, TAB_BAND_DOMAIN)
        target = _section(dialog, TAB_BAND_DOMAIN, TAB_BAND_TARGET, TAB_BAND_KEY)
        _settle(4)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        assert bar.isHidden() is False, "前提：带内跳转条摆着"
        assert _past_end_padding(dialog, TAB_BAND_DOMAIN, TAB_BAND_KEY) > 0, (
            f"整块内容要滚，页尾留白不该是 0（标签页之外还有 {layer}px 的标签那一层）")
        assert target.card.isHidden() is False, "前提：目标是不用先展开的普通分区"

        want = target.mapTo(scroll.widget(), QPoint(0, 0)).y() - 8
        next(chip for chip in _chips(bar) if chip.text() == TAB_BAND_TARGET).click()
        _settle(4)
        landed = scroll.verticalScrollBar().value()
        assert landed == want, (
            f"点「{TAB_BAND_TARGET}」后滚到 {landed}（目标 {want}）：滚动范围仍不够"
            f"（maximum={scroll.verticalScrollBar().maximum()}）")
        assert target.mapTo(scroll.viewport(), QPoint(0, 0)).y() == 8
    finally:
        dialog.close()


def test_chips_name_their_section_and_take_keyboard_focus(tmp_path, monkeypatch):
    """chip 文案 = 组标题；读屏名字说清跳到哪；可聚焦（键盘也能用）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        _page_widget, bar = _select(dialog, "语音")
        blocks = _blocks_of(_content(dialog, "语音"))
        assert [chip.text() for chip in _chips(bar)] == [title for title, _w in blocks]
        for chip in _chips(bar):
            assert chip.objectName() == "settingsPageAnchor"
            assert chip.accessibleName() == f"跳到「{chip.text()}」分区"
            assert chip.focusPolicy() == Qt.FocusPolicy.StrongFocus
        # 桌面胶囊（灵动岛）在装配处被压平成单卡——它也只算一个区。
        _page_widget, island_bar = _select(dialog, "桌面组件")
        assert [chip.text() for chip in _chips(island_bar)] == [
            "桌面胶囊（灵动岛）", "网络延迟检测（高级）"]
    finally:
        dialog.close()


def test_clicking_a_chip_top_aligns_the_section(tmp_path, monkeypatch):
    """点了把该区顶到视口上沿（8px 余量），不是「勉强可见」——长卡片里后者等于没跳。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()
        page, bar = _select(dialog, "语音")
        scroll = page.findChild(QScrollArea, "settingsScroll")
        # 2026-10-14 起「语音」域的语音卡就叫「语音报时」（旧的四张小卡不再进域）。
        # 卡片 0「语音报时」天生在 y=0，验不出"顶对齐"；域尾的卡片又会被滚动范围
        # 夹住（滚到底也够不到 8px）。所以挑一个**中段、且滚得到**的卡片——
        # 「提醒哪些日子」在 1416px 处，滚动范围 2046px，正合适。
        target = _section(dialog, "语音", "提醒哪些日子")
        expected = target.mapTo(scroll.widget(), QPoint(0, 0)).y() - 8
        assert scroll.verticalScrollBar().maximum() > 0, "内容不够长，测不出滚动"
        assert expected <= scroll.verticalScrollBar().maximum(), (
            "所选目标超出一屏滚动范围，顶对齐验不出来（请换一个更靠前的卡片）")

        chip = next(chip for chip in _chips(bar) if chip.text() == "提醒哪些日子")
        chip.click()
        app.processEvents()
        assert scroll.verticalScrollBar().value() == max(0, expected)
        assert target.mapTo(scroll.viewport(), QPoint(0, 0)).y() == 8
    finally:
        dialog.close()


def _advanced_group_targets(dialog) -> list[tuple[str, str | None, str]]:
    """「默认收起的高级组」目标：``(域, 标签键, 组标题)``。

    桌宠域的「碰撞参数（高级）」三平台都有，是本用例的常驻路径；主动识屏的细调组只在
    Windows 构造（``settings_pet_controls`` 的平台闸门），有就一并验——展开后滚动
    是同一条代码路径，覆盖不削弱。
    """
    targets = [("桌宠", "motion_collision", "碰撞参数（高级）")]
    if _has_proactive(dialog):
        targets.append(("自动化与联动", None, "主动识屏 · 细调（高级）"))
    return targets


def test_clicking_a_chip_expands_a_collapsed_group_first(tmp_path, monkeypatch):
    """目标是高级组 / 折叠框时先展开再滚（口径同搜索命中），否则滚过去是一片空白。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        _page_widget, bar = _select(dialog, "自动化与联动")

        # 折叠框（三平台都有这条路径）。
        assert dialog.agent_link_box.is_expanded() is False
        next(chip for chip in _chips(bar) if chip.text() == "Agent 联动").click()
        app.processEvents()
        assert dialog.agent_link_box.is_expanded() is True
        assert dialog.agent_link_box.body.isHidden() is False

        if _has_proactive(dialog):
            dialog.pro_enabled_check.setChecked(True)  # 主动识屏细项要总开关打开才可见
            app.processEvents()
        for domain, tab_key, title in _advanced_group_targets(dialog):
            _page_widget, bar = _select(dialog, domain)
            detail = _section(dialog, domain, title, tab_key)
            assert detail.card.isHidden() is True, "高级组默认收起"
            next(chip for chip in _chips(bar) if chip.text() == title).click()
            app.processEvents()
            assert detail.toggle.isChecked() is True
            assert detail.card.isHidden() is False, "跳过去之后参数必须真的看得见"
    finally:
        dialog.close()


def test_clicking_a_chip_top_aligns_a_collapsed_group_in_one_click(tmp_path, monkeypatch):
    """收起的高级组：**一次**点击就要顶对齐（展开后布局与滚动范围都得先落定）。

    回归：展开只 ``activate()`` 布局时，量到的 y 落在半更新的布局上、滚动范围也还是
    旧的——第一次点击滚不到位（实测页尾目标差 74px），要再点一次才对。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()
        _page_widget, bar = _select(dialog, "AI 与对话")
        scroll = _page(dialog, "AI 与对话").findChild(QScrollArea, "settingsScroll")
        target = _section(dialog, "AI 与对话", "生成参数（高级）")
        assert target.card.isHidden() is True, "高级组默认收起"
        assert scroll.verticalScrollBar().maximum() > 0, "内容不够长，测不出滚动"

        next(chip for chip in _chips(bar) if chip.text() == "生成参数（高级）").click()
        app.processEvents()
        expected = target.mapTo(scroll.widget(), QPoint(0, 0)).y() - 8
        assert scroll.verticalScrollBar().value() == max(0, expected)
        assert target.mapTo(scroll.viewport(), QPoint(0, 0)).y() == 8
    finally:
        dialog.close()


def _past_end_padding(dialog, domain: str, tab_key: str | None = None) -> int:
    """域页（页内标签域则是标签页）滚动内容末尾那块留白的高度（布局下外边距）。"""
    return _content(dialog, domain, tab_key).layout().contentsMargins().bottom()


def test_the_last_group_of_a_page_can_be_top_aligned_in_one_click(tmp_path, monkeypatch):
    """页尾那一组也要一次点击顶到 y=8（普通组与收起的高级组各一条）。

    回归（终审问题 1）：滚动范围的尽头是「内容底边贴视口底边」，页尾那一组最多滚到
    ``视口高 - 该组高``——900×700 实测「碰撞参数（高级）」只能到 y=255（desired 1189 >
    maximum 942）、「余额与服务状态」只能到 y=191。展开时序没问题，是范围不够；
    内容末尾那块留白就是补这个差额的。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.show()
        for size in ((720, 500), (900, 700)):
            dialog.resize(*size)
            app.processEvents()
            for domain, tab_key, title in (
                ("AI 与对话", None, "余额与服务状态"),
                ("桌宠", "motion_collision", "碰撞参数（高级）"),
            ):
                _page_widget, bar = _select(dialog, domain)
                scroll = _page(dialog, domain).findChild(QScrollArea, "settingsScroll")
                target = _section(dialog, domain, title, tab_key)
                assert scroll.verticalScrollBar().maximum() > 0, "内容不够长，测不出滚动"

                next(chip for chip in _chips(bar) if chip.text() == title).click()
                app.processEvents()
                landed = target.mapTo(scroll.viewport(), QPoint(0, 0)).y()
                assert landed == 8, (
                    f"{size} 下「{domain}／{title}」一次点击后落在视口 y={landed}（目标 8）："
                    f"滚动范围仍不够（maximum={scroll.verticalScrollBar().maximum()}）"
                )
    finally:
        dialog.close()


def test_past_end_padding_follows_the_viewport_size(tmp_path, monkeypatch):
    """留白高度跟着视口重算，且不超过一屏（编辑器式 scroll-past-end 的上限）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        _page_widget, _bar = _select(dialog, "AI 与对话")
        scroll = _page(dialog, "AI 与对话").findChild(QScrollArea, "settingsScroll")
        tall = _past_end_padding(dialog, "AI 与对话")
        assert tall > 0, "这一页内容明显超过一屏，留白不该是 0"
        assert tall <= scroll.viewport().height(), "留白超过一屏"

        dialog.resize(720, 500)
        app.processEvents()
        short = _past_end_padding(dialog, "AI 与对话")
        assert 0 < short <= scroll.viewport().height(), "留白超过一屏"
        assert short != tall, (
            f"视口变成 {scroll.viewport().height()} 后留白没重算（还是 {tall}）"
        )
    finally:
        dialog.close()


def test_pages_that_already_fit_get_no_past_end_padding(tmp_path, monkeypatch):
    """本来就装得下的页面留白必须是 0：为「顶对齐」凭空造出一个滚动条是回退。

    宽窗口下这两页装得下——留白一加就会挤出滚动条，页面标题与内容不再居中
    （``test_menu_layout.test_wide_settings_title_tracks_the_centered_page_content``
    就是被这条咬到的）。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(1600, 900)
        dialog.show()
        app.processEvents()
        for domain in ("常规", "文件识别"):
            _page_widget, _bar = _select(dialog, domain)
            scroll = _page(dialog, domain).findChild(QScrollArea, "settingsScroll")
            assert scroll.verticalScrollBar().maximum() == 0, f"「{domain}」本该不滚动"
            assert _past_end_padding(dialog, domain) == 0, (
                f"「{domain}」装得下，留白却给了 {_past_end_padding(dialog, domain)}px"
            )
    finally:
        dialog.close()


def test_chip_follows_its_group_being_hidden_by_a_dependency(tmp_path, monkeypatch):
    """整组被前置开关藏起来时，它的 chip 一起藏（不留在页头当死链）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        if not _has_proactive(dialog):
            pytest.skip("主动识屏控件只在 Windows 构造（settings_pet_controls 的平台闸门）")
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        _page_widget, bar = _select(dialog, "自动化与联动")
        chips = {chip.text(): chip for chip in _chips(bar)}
        watched = ("主动识屏 · 什么时候看", "主动识屏 · 看哪些软件", "主动识屏 · 细调（高级）")

        assert dialog.pro_enabled_check.isChecked() is False
        for title in watched:
            assert chips[title].isHidden() is True, f"「{title}」关了总开关就该藏"
        dialog.pro_enabled_check.setChecked(True)
        app.processEvents()
        for title in watched:
            assert chips[title].isHidden() is False, f"「{title}」开了总开关就该回来"
        dialog.pro_enabled_check.setChecked(False)
        app.processEvents()
        for title in watched:
            assert chips[title].isHidden() is True, f"「{title}」再关要能再藏"
    finally:
        dialog.close()


def test_anchor_bar_rebuilds_when_the_in_page_tab_changes(tmp_path, monkeypatch):
    """页内标签各是一组区：换标签就换成那一组的 chip，显隐门槛按该标签重算。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        page, bar = _select(dialog, "桌宠")
        tabs = page.findChild(SettingsTabContainer)
        # 2026-10-20：「桌宠」域新增「角色与素材」标签页（角色包清单 + 分件下载），
        # 契约随之变化——这里同步期望值，不是放宽断言。
        assert tabs.keys() == ("display_music", "motion_collision", "character_packs")

        assert tabs.currentKey() == "display_music"
        assert [chip.text() for chip in _chips(bar)] == [
            "显示", "音乐关联", settings_music.advanced_group_title(dialog)]
        assert bar.isHidden() is True, "「显示与音乐」只有 3 个区"

        tabs.setCurrentKey("motion_collision")
        app.processEvents()
        assert bar.isHidden() is False
        assert [chip.text() for chip in _chips(bar)] == [
            "动画与移动", "拖拽与弹射", "边缘探头", "生小肥鱼", "多开碰撞", "碰撞参数（高级）"]

        tabs.setCurrentKey("display_music")
        app.processEvents()
        assert bar.isHidden() is True
        assert len(_chips(bar)) == 3
    finally:
        dialog.close()


def test_anchor_bar_and_descriptions_fit_the_compact_window(tmp_path, monkeypatch):
    """720×500（最小窗口）下 chip 不越界、描述不裁切（GATES §3）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()
        for domain in ("AI 与对话", "自动化与联动", "语音"):
            page, bar = _select(dialog, domain)
            header = page.findChild(QWidget, "pageHeader")
            assert bar.isHidden() is False
            assert bar.width() <= header.width()
            for chip in _visible_chips(bar):
                rect = chip.geometry()
                assert rect.left() >= 0 and rect.right() <= bar.width(), (
                    f"「{chip.text()}」chip 超出跳转条宽度")
            for label in page.findChildren(QLabel, "sectionDescription"):
                if not label.isVisible():
                    continue
                assert label.heightForWidth(label.width()) <= label.height(), (
                    f"组描述被纵向裁切：{label.text()[:16]}")
    finally:
        dialog.close()


# ------------------------------------------------------------------------- 组描述
def _titles_absent_on_this_platform(dialog) -> set[str]:
    """本平台**不可能出现**的组标题（``SECTION_GUIDE`` 里的这些键不算过期）。

    三处平台闸门，都由实际构造结果回答：

    - 主动识屏整页只在 Windows 构造 ⇒ 它的四组在本平台不存在；
    - 播放器高级组在「标题格式」行不存在时退化成不提标题格式的组名
      （``settings_music.advanced_group_title``），另一个组名才是本平台的；
    - 「直播捕获（高级）」装的「直播捕获兼容」只在 Windows 建（可枚举的窗口是
      Windows 专属），整组在本平台不存在。
    """
    absent = set()
    if not _has_proactive(dialog):
        absent |= _PROACTIVE_TITLES
    player_titles = {settings_music.PLAYER_GROUP_TITLE, settings_music.PLAYER_GROUP_TITLE_NO_FOOBAR}
    absent |= player_titles - {settings_music.advanced_group_title(dialog)}
    if sys.platform != "win32":
        absent.add("直播捕获（高级）")
    return absent


def test_every_group_has_a_description(tmp_path, monkeypatch):
    """覆盖率守卫：每个可见组都必须有一句描述（新增组漏写 = 红）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        groups = _all_groups(dialog)
        assert len(groups) > 40, f"只扫到 {len(groups)} 个组，装配可能变了，本用例会假绿"
        missing = [title for title, widget in groups if not widget.description_label.text().strip()]
        assert not missing, f"这些组没有描述（请在 SECTION_GUIDE 里补一行）：{missing}"
    finally:
        dialog.close()


def test_section_guide_has_no_stale_entries(tmp_path, monkeypatch):
    """字典不许腐烂：每个键都得对上一个真实存在的组。

    本平台按产品闸门不可能存在的组（见 ``_titles_absent_on_this_platform``）不算过期，
    但这些键本身仍受另一条 CI 腿盯着——Windows 上它们必须全都对得上。
    """
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        titles = {title for title, _widget in _all_groups(dialog)}
        stale = sorted(
            set(settings_page_guide.SECTION_GUIDE) - titles - _titles_absent_on_this_platform(dialog))
        assert not stale, f"这些描述的组已经不存在了（请同步删除）：{stale}"
    finally:
        dialog.close()


def test_section_descriptions_pass_the_copy_lint():
    """描述也过一遍设置页黑话判据（与行文案同一把尺子）。"""
    from tests.test_settings_copy_lint import _tokens

    offenders = [
        f"{title}: {sorted(_tokens(text))}"
        for title, text in settings_page_guide.SECTION_GUIDE.items()
        if _tokens(text)
    ]
    assert not offenders, "组描述里出现黑话（改文案）：\n" + "\n".join(offenders)


def test_quiet_guide_mentions_voice_muting():
    """免打扰的组描述必须写明「语音也静音」（2026-10-08 口径，防文案回退）。"""
    assert "语音" in settings_page_guide.SECTION_GUIDE["免打扰"]


def test_advanced_group_shows_its_description_while_collapsed(tmp_path, monkeypatch):
    """高级组收起时也看得见「这里是什么」，否则要先展开才知道里面有没有要找的。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        dialog.resize(900, 700)
        dialog.show()
        app.processEvents()
        _select(dialog, "AI 与对话")
        advanced = _section(dialog, "AI 与对话", "生成参数（高级）")
        assert advanced.advanced is True
        assert advanced.card.isHidden() is True
        assert advanced.description_label.isVisibleTo(advanced) is True
        assert advanced.description_label.text().strip()
    finally:
        dialog.close()


# --------------------------------------------------------- 行标题重写（2026-10-08）
#: 13 行改写后的定稿标题（行键一个都没动，只改措辞）。
#: 2026-10-08：「菜单版本（新版 / 旧版）」随旧版菜单退役整行删除（不是改名），
#: 故从本表移除——它的旧词别名也一并删除，见 OLD_WORDS 的注释。
REWRITTEN_ROW_TITLES = {
    "proactive_dry_run": "试运行（只记日志、不花钱）",
    "proactive_dwell": "在同一窗口停多久才看（秒）",
    "proactive_cooldown": "两次搭话至少间隔",
    "proactive_min_interval": "两次识屏最短间隔（秒）",
    "proactive_free": "识屏用单独的看图模型",
    "proactive_memory_clear": "清空陪伴记忆",
    "collision_mass_scale": "体重倍率",
    "collision_impulse_cap": "最大撞击力度",
    "collision_restitution": "撞后弹力",
    "dynamic_island_info": "显示中间信息栏",
    "dynamic_island_info_mode": "信息栏显示什么",
    "balance_tier_mode": "高峰 / 空闲时段提示",
    "agent_sound_enabled": "AI 编程助手提示音",
}

#: 只在 Windows 存在的改名行（主动识屏整页的平台闸门）：非 Windows 上它们不存在，
#: 不算「行键丢了」，也不算别名失效。
_WINDOWS_ONLY_ROW_KEYS = frozenset(
    key for key in REWRITTEN_ROW_TITLES if key.startswith("proactive_"))

#: 改标题前用户会照着找的旧说法 → 该落在哪一行（旧词进 ``ROW_ALIASES``）。
#: 「菜单模式 → menu_template」不在本表：那一行已被删除，旧词没有去向，
#: 别名同时从 settings_search.ROW_ALIASES 移除（搜「菜单模式」0 候选是正确行为）。
OLD_WORDS = {
    "dry-run": "proactive_dry_run",
    "验证模式": "proactive_dry_run",
    "门限": "proactive_dwell",
    "冷却": "proactive_cooldown",
    "最小请求": "proactive_min_interval",
    "视觉配置": "proactive_free",
    "质量倍率": "collision_mass_scale",
    "弹性": "collision_restitution",
    "信息槽": "dynamic_island_info",
    "峰谷": "balance_tier_mode",
    "音效联动": "agent_sound_enabled",
}


def test_rewritten_row_titles_match_the_agreed_wording(tmp_path, monkeypatch):
    """13 行标题按定稿措辞呈现（只改措辞，行键一个不动）。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for key, title in REWRITTEN_ROW_TITLES.items():
            row = dialog.findChild(SettingRow, f"settingRow_{key}")
            if row is None:
                assert key in _WINDOWS_ONLY_ROW_KEYS and not _has_proactive(dialog), (
                    f"行键 {key} 不见了（本刀不许动行键）")
                continue
            assert row.label.text() == title, f"{key} 的标题与定稿不符"
    finally:
        dialog.close()


def test_old_words_still_find_the_rewritten_rows(tmp_path, monkeypatch):
    """老用户记忆里的旧说法仍要搜得到：改完标题不能把老用户关在门外。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for query, key in OLD_WORDS.items():
            if key in _WINDOWS_ONLY_ROW_KEYS and not _has_proactive(dialog):
                continue  # 本平台没有这一行（Windows 那条 CI 腿盯住它的旧词别名）
            dialog.search_edit.setText(query)
            app.processEvents()
            hits = [row.objectName() for row in dialog._search_matches]
            assert f"settingRow_{key}" in hits, (
                f"搜「{query}」找不到 {key}（旧词要进 settings_search.ROW_ALIASES）；实得 {hits}")
    finally:
        dialog.close()
