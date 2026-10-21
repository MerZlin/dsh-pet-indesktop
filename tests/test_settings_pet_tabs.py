# -*- coding: utf-8 -*-
"""「桌宠」域页内标签（显示与音乐 / 动作与碰撞）的聚焦回归（第二刀，2026-10-08）。

口径来源：``.scratch/SETTINGS-UI-DESIGN-K3-20261007.md`` §二（页内标签的判定规则）
与第二刀裁决——桌宠域是**两个互斥任务**：「它长什么样 / 唱不唱歌」（显示与音乐）
与「它怎么动 / 怎么撞」（动作与碰撞）；高级组永远垫底其所属标签页；侧栏 10 域、
深链与搜索跳域语义不变。

本文件钉住五件事：

1. 桌宠域确实用页内标签组织、默认激活标签 1、侧栏域清单不变（域是外部契约）；
2. 标签内组序：主视线组在前、高级组垫底；每行的 ``SettingsSection`` 归属唯一；
3. 每一行都能由标签键路由到，切到所属标签后可见（分页没丢功能）；
4. 搜索命中另一标签的行时自动切标签 + 展开高级组，命中行**实际可见**
   （标签 1：标题格式 / 自动检测播放器；标签 2：碰撞开关 / 碰撞音量）；
5. 保存 round trip 不变——分页是纯呈现层改动，不碰配置键、默认值与保存链。
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

EXPECTED_SIDEBAR = [
    "常规", "桌宠", "互动", "菜单", "桌面组件", "功能", "AI 与对话", "自动化与联动", "语音", "文件识别", "更新",
]
#: 标签键给代码（稳定），标签名给用户（可读）；顺序 = 使用顺序。
EXPECTED_TABS = (("display_music", "显示与音乐"), ("motion_collision", "动作与碰撞"),
                 ("character_packs", "角色与素材"))

#: 标签 1 的主视线组序；高级组标题按平台上有没有「标题格式」行动态取
#: （``settings_music.advanced_group_title``），用例里拼。
TAB1_PRIMARY = ("显示", "音乐关联")
#: 标签 2 的组序：主视线 5 组 + 垫底的「碰撞参数（高级）」。
TAB2_SECTIONS = ("动画与移动", "拖拽与弹射", "边缘探头", "生小肥鱼", "多开碰撞", "碰撞参数（高级）")

TAB1_ROWS = (
    "scale", "bubble_text_scale", "pet_opacity",
    "music_sing", "music_lyric", "music_lyric_lead",
)
TAB1_ADVANCED_ROWS = (
    "music_player_netease", "music_player_qqmusic", "music_player_kugou",
    "music_player_soda", "music_player_detect",
)
TAB2_ROWS = (
    "playback_speed", "animation_gap", "idle_low_fps", "no_move",
    "drag_physics", "drop_gravity", "throw_strength", "slingshot_enabled",
    "lock_position", "shift_drag", "edge_probe",
    "spawn_inherit_size", "spawn_scale", "clear_spawned_pets",
    "collision_enabled", "collision_sound_enabled",
)
TAB2_ADVANCED_ROWS = (
    "collision_restitution", "collision_friction", "collision_mass_scale",
    "collision_impulse_cap", "collision_sound_volume",
)
#: 浏览器媒体会话开关与 foobar 两行同样只在 Windows 建（SMTC / 窗口标题源是
#: Windows 专属能力，非 Windows 建行也没效果）——平台闸门，不是被整理掉了。
if sys.platform == "win32":
    TAB1_ROWS = TAB1_ROWS + ("music_browser_media", "foobar_lyrics")
    TAB1_ADVANCED_ROWS = ("foobar_title_format",) + TAB1_ADVANCED_ROWS


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.settings_slot_config import route_settings_config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    # 生产形态：当前宠的设置页拿 slot0 的路由视图（每宠键写进 config-slot-0.json）
    dlg = settings_mod.ModernSettingsDialog(
        route_settings_config(Config(base=tmp_path, instance_id="slot-0")),
        include_ai=True)
    yield dlg
    dlg.close()
    app.processEvents()


def _pet_page(dialog):
    index = next(
        i for i in range(dialog.sidebar.count()) if dialog.sidebar.item(i).text() == "桌宠"
    )
    return dialog.pages.widget(index)


def _pet_tabs(dialog):
    from pet.settings_widgets import SettingsTabContainer

    tabs = _pet_page(dialog).findChild(SettingsTabContainer, "settingsTaskTabs")
    assert tabs is not None, "桌宠域没有用页内标签组织"
    return tabs


def _row(dialog, key):
    from pet.modern_settings_dialog import SettingRow

    found = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert found is not None, f"缺少设置行 {key}"
    return found


def _row_key(row) -> str:
    return row.objectName()[len("settingRow_"):]


def _sections_of_tab(tabs, tab_key):
    """标签页里按真实布局顺序排列的 ``SettingsSection``（不是 child 顺序）。"""
    from pet.settings_widgets import SettingsSection

    tabs.setCurrentKey(tab_key)
    content = tabs.stack.currentWidget()
    sections = []
    for position in range(content.layout().count()):
        widget = content.layout().itemAt(position).widget()
        if isinstance(widget, SettingsSection):
            sections.append(widget)
    return sections


def _section_title(section) -> str:
    from PySide6.QtWidgets import QLabel

    if section.toggle is not None:  # 高级组：标题在 disclosure 按钮上
        return section.toggle.text()
    label = section.findChild(QLabel, "sectionTitle")
    return label.text() if label is not None else ""


def _section_of(row):
    """行所属的 ``SettingsSection``（走 widget 祖先链）。"""
    from pet.modern_settings_dialog import SettingsSection

    parent = row.parentWidget()
    while parent is not None and not isinstance(parent, SettingsSection):
        parent = parent.parentWidget()
    assert parent is not None, f"{row.objectName()} 必须落在某个 SettingsSection 内"
    return parent


def test_pet_domain_uses_in_page_tabs_without_changing_sidebar(dialog):
    """页内标签组织桌宠域；默认激活标签 1；侧栏域清单不变。"""
    assert [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())] == EXPECTED_SIDEBAR

    tabs = _pet_tabs(dialog)
    assert tabs.keys() == tuple(key for key, _ in EXPECTED_TABS)
    assert tabs.labels() == tuple(label for _, label in EXPECTED_TABS)
    assert tabs.currentKey() == EXPECTED_TABS[0][0], "默认必须激活标签 1"
    assert _pet_page(dialog).isAncestorOf(tabs)


def test_group_order_inside_each_tab_puts_the_advanced_group_last(dialog):
    """标签内组序：主视线组在前、高级组垫底；每行的 SettingsSection 归属唯一。"""
    from pet.music import settings as settings_music

    tabs = _pet_tabs(dialog)
    expected = {
        "display_music": TAB1_PRIMARY + (settings_music.advanced_group_title(dialog),),
        "motion_collision": TAB2_SECTIONS,
    }
    for tab_key, titles in expected.items():
        sections = _sections_of_tab(tabs, tab_key)
        assert [_section_title(section) for section in sections] == list(titles)
        assert all(section.advanced is False for section in sections[:-1])
        assert sections[-1].advanced is True, "高级组必须垫底其所属标签页"

    # 归属唯一：每个桌宠行的 SettingsSection 祖先有且只有一个（不会一组挂两处）。
    from pet.modern_settings_dialog import SettingsSection

    for key in TAB1_ROWS + TAB1_ADVANCED_ROWS + TAB2_ROWS + TAB2_ADVANCED_ROWS:
        parent, ancestors = _row(dialog, key).parentWidget(), []
        while parent is not None:
            if isinstance(parent, SettingsSection):
                ancestors.append(parent)
            parent = parent.parentWidget()
        assert len(ancestors) == 1, f"{key} 的 SettingsSection 祖先必须唯一"


def test_every_pet_row_routes_to_its_tab_and_is_reachable(dialog):
    """每行都由标签键路由到，切到所属标签后可见——分页没有丢掉任何一行。"""
    tabs = _pet_tabs(dialog)
    # 「小肥鱼大小」只在**关闭**「生小肥鱼继承大小」时才显示（既有依赖契约）：
    # 先满足依赖，再断言"切到所属标签后确实看得到"，否则量到的是依赖隐藏而非分页。
    _row(dialog, "spawn_inherit_size").control.setChecked(False)
    _qapp().processEvents()

    routing = {}
    routing.update({key: "display_music" for key in TAB1_ROWS})
    routing.update({key: "display_music" for key in TAB1_ADVANCED_ROWS})
    routing.update({key: "motion_collision" for key in TAB2_ROWS})
    routing.update({key: "motion_collision" for key in TAB2_ADVANCED_ROWS})

    page = _pet_page(dialog)
    for tab_key, keys in (
        ("display_music", TAB1_ROWS + TAB1_ADVANCED_ROWS),
        ("motion_collision", TAB2_ROWS + TAB2_ADVANCED_ROWS),
    ):
        tabs.setCurrentKey(tab_key)
        _qapp().processEvents()
        for key in keys:
            row = _row(dialog, key)
            assert tabs.key_for_descendant(row) == routing[key], (
                f"{key} 期望落在 {routing[key]} 标签，实际 {tabs.key_for_descendant(row)}"
            )
            if key in TAB1_ADVANCED_ROWS or key in TAB2_ADVANCED_ROWS:
                _section_of(row).toggle.setChecked(True)  # 高级组默认收起
            _qapp().processEvents()
            assert row.isVisibleTo(page) is True, f"{key} 在 {tab_key} 标签里不可见"


def test_rows_of_the_other_tab_are_out_of_view_but_not_destroyed(dialog):
    """另一标签的行只是不可见，对象与控件都还在——整理没有删功能/没有动保存链。"""
    tabs = _pet_tabs(dialog)
    tabs.setCurrentKey("display_music")
    hidden_row = _row(dialog, "collision_enabled")
    assert hidden_row.isVisibleTo(tabs) is False
    assert hidden_row.isHidden() is False, "行只是不在当前标签页，不是被隐藏/删除"
    assert hidden_row.control is not None, "控件对象必须仍在（保存链依赖它）"

    tabs.setCurrentKey("motion_collision")
    assert hidden_row.isVisibleTo(tabs) is True


def _search(dialog, query: str):
    """真实搜索框输入（textChanged）+ 事件循环，取当前命中行。"""
    dialog.search_edit.setText("")
    _qapp().processEvents()
    dialog.search_edit.setText(query)
    for _ in range(3):
        _qapp().processEvents()
    matches = dialog._search_matches
    assert matches, f"搜索「{query}」没有命中任何行"
    return matches[dialog._search_index]


def test_search_jumps_to_the_tab_that_owns_the_hit(dialog):
    """搜「碰撞开关」（标签 2 的行）→ 自动切到动作与碰撞标签，命中行可见。"""
    tabs = _pet_tabs(dialog)
    assert tabs.currentKey() == "display_music"

    row = _search(dialog, "碰撞开关")
    assert _row_key(row) == "collision_enabled"
    assert dialog.sidebar.currentItem().text() == "桌宠"
    assert tabs.currentKey() == "motion_collision"
    assert row.isVisibleTo(_pet_page(dialog)) is True


def test_search_on_tab_one_hits_stay_on_tab_one(dialog):
    """标签 1 的行（含默认标签）命中后仍停在标签 1、行可见——回归，不是只测切过去。"""
    from pet.music import settings as settings_music

    tabs = _pet_tabs(dialog)
    queries = [
        ("音乐自动唱歌", "music_sing"),
        # 标签 1 高级组里的行：命中后必须展开高级组，否则"搜到了但看不见"。
        ("自动检测播放器", "music_player_detect"),
    ]
    if sys.platform == "win32":
        queries.append(("标题格式", "foobar_title_format"))
    else:
        assert "标题格式" not in settings_music.advanced_group_title(dialog)

    for query, key in queries:
        tabs.setCurrentKey("motion_collision")  # 先离开标签 1，验证真的切回来
        row = _search(dialog, query)
        assert _row_key(row) == key, f"「{query}」应命中 {key}"
        assert tabs.currentKey() == "display_music", f"「{query}」命中标签 1 的行必须切回标签 1"
        section = _section_of(row)
        if section.advanced:
            assert section.toggle.isChecked() is True, f"「{query}」必须自动展开高级组"
            assert not section.card.isHidden()
            assert _section_title(section) == settings_music.advanced_group_title(dialog)
        assert row.isVisibleTo(_pet_page(dialog)) is True, f"「{query}」命中的行必须实际可见"


def test_search_expands_the_pre_existing_collision_advanced_group(dialog):
    """既有高级组（碰撞参数）回归：搜「碰撞音量」→ 切标签 2 + 展开 + 行可见。"""
    tabs = _pet_tabs(dialog)
    row = _search(dialog, "碰撞音量")
    assert _row_key(row) == "collision_sound_volume"
    assert tabs.currentKey() == "motion_collision"
    section = _section_of(row)
    assert section.advanced is True
    assert _section_title(section) == "碰撞参数（高级）"
    assert section.toggle.isChecked() is True, "命中折叠组内的行必须自动展开"
    assert row.isVisibleTo(_pet_page(dialog)) is True


def test_pet_settings_save_round_trip_is_unchanged(dialog, tmp_path):
    """保存 round trip 不变：分页只改呈现，配置键/默认值/保存链一字未动。"""
    from pet.config import Config

    _row(dialog, "collision_enabled").control.setChecked(False)
    _row(dialog, "drag_physics").control.setChecked(True)
    row = _row(dialog, "music_lyric_lead")
    lead = 2.5 if abs(float(row.control.value()) - 2.5) > 1e-6 else 1.5
    row.control.setValue(lead)

    dialog._save()
    _qapp().processEvents()

    # 每宠键（碰撞/拖拽）真值在 slot0 档案；全局键（歌词提前量）在主配置
    from pet.settings_slot_config import route_settings_config

    saved = route_settings_config(Config(base=tmp_path, instance_id="slot-0"))
    assert saved.get("collision_enabled") is False
    assert saved.get("drag_physics") is True
    assert float(saved.get("music_lyric_lead_seconds")) == pytest.approx(lead)
