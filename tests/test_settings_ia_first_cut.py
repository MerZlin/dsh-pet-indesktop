# -*- coding: utf-8 -*-
"""设置页信息架构第一刀（2026-10-07）的行为验收：V1 文案 / V2 高级折叠与组序 /
V3 Agent 折叠框 / V4 搜索自动展开（含依赖隐藏语义）。

口径来源：``.scratch/SETTINGS-IA-SYNTHESIS-20261007.md`` 的「R2 修订」节（逐字定稿，
与设计稿冲突处以该节为准）与 ``docs/SETTINGS-CHANGE-GATES.md``（披露层级、依赖语义）。

纪律：断言全部走真实 ``ModernSettingsDialog`` 这个公开 seam；折叠态按
``tests/test_menu_layout.py:1359`` 的 toggle/card 真实检查法（``CollapsibleGroup``
没有 ``isExpanded()``，不作断言）；搜索走真实搜索框 + 回车，不只调私有方法。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

import pet.modern_settings_dialog as settings_mod
from pet import settings_reset_defaults
from pet.music import settings as settings_music
from pet.config import Config
from pet.modern_settings_dialog import SettingRow, SettingsSection
from pet.settings_widgets import CollapsibleGroup, SettingsTabContainer

app = QApplication.instance() or QApplication([])

_REPO = Path(__file__).resolve().parents[1]
#: 第一刀允许改动的生产文件（V1 的 grep 口径）。
_PRODUCTION_FILES = (
    "pet/modern_settings_dialog.py",
    "pet/music/settings.py",
    "pet/settings_network.py",
    "pet/settings_reset_defaults.py",
)
_FORBIDDEN_TOKENS = ("SMTC", "UDP", "223.5.5.5", "网卡字节计数器", "甩出死区", "零初速",
                     "只装密钥的条目")
#: 桌宠域页内标签（第二刀 2026-10-08）**标签内**的组序；高级组永远垫底其所属标签页。
#: 标签 1 的高级组标题随平台上有没有「标题格式」行动态取（用例里拼）。
_PET_TAB_SECTIONS = {
    "display_music": ("显示", "音乐关联"),
    "motion_collision": ("动画与移动", "拖拽与弹射", "边缘探头", "生小肥鱼", "多开碰撞"),
}


def _dialog(tmp_path, monkeypatch):
    return _cfg_dialog(tmp_path, monkeypatch)


def _cfg_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    cfg = Config(base=tmp_path)
    return settings_mod.ModernSettingsDialog(cfg, include_ai=True), cfg


def _row(dialog, key):
    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def _section_of(dialog, key):
    """行所属的 SettingsSection（走 widget 祖先链，不用 findChildren 的陈旧对象）。"""
    parent = _row(dialog, key).parentWidget()
    while parent is not None and not isinstance(parent, SettingsSection):
        parent = parent.parentWidget()
    assert parent is not None, f"{key} 必须落在某个 SettingsSection 内"
    return parent


def _section_title(section) -> str:
    if section.toggle is not None:  # 高级组：标题在 disclosure 按钮上
        return section.toggle.text()
    label = section.findChild(QLabel, "sectionTitle")
    return label.text() if label is not None else ""


def _page(dialog, domain):
    index = next(i for i in range(dialog.sidebar.count())
                 if dialog.sidebar.item(i).text() == domain)
    return index, dialog.pages.widget(index)


def _page_sections(dialog, domain, tab_key: str | None = None):
    """域页里按布局顺序排列的 SettingsSection（真实呈现顺序，不是 child 顺序）。

    页内标签域（桌宠/互动/菜单）里，组在**标签页内**：给 ``tab_key`` 取该标签的组，
    缺省取当前激活标签。返回的 ``page`` 仍是整个域页（行祖先归属不受标签影响）。
    """
    _index, page = _page(dialog, domain)
    content = page.findChild(QScrollArea, "settingsScroll").widget()
    # 页内标签域的 scroll 内容就是标签容器本身（findChild 不含自身）。
    tabs = content if isinstance(content, SettingsTabContainer) else content.findChild(
        SettingsTabContainer, "settingsTaskTabs")
    if tabs is not None:
        if tab_key is not None:
            tabs.setCurrentKey(tab_key)
        content = tabs.stack.currentWidget()
    sections = []
    for position in range(content.layout().count()):
        widget = content.layout().itemAt(position).widget()
        if isinstance(widget, SettingsSection):
            sections.append(widget)
    return page, sections


def _current_page(dialog):
    return dialog.pages.widget(dialog.sidebar.currentRow())


def _current_match(dialog):
    matches = dialog._search_matches
    assert matches, "搜索没有命中任何行"
    return matches[dialog._search_index]


def _search(dialog, query: str, *, presses: int = 0):
    """真实搜索框输入（textChanged）+ 可选回车遍历（eventFilter）。"""
    dialog.search_edit.setText("")
    app.processEvents()
    dialog.search_edit.setText(query)
    app.processEvents()
    for _ in range(presses):
        QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
        app.processEvents()
    return _current_match(dialog)


def _search_until(dialog, query: str, key: str, *, limit: int = 12):
    """回车遍历直到命中指定行（覆盖多命中场景），返回该行。"""
    dialog.search_edit.setText("")
    app.processEvents()
    dialog.search_edit.setText(query)
    app.processEvents()
    for _ in range(limit):
        row = _current_match(dialog)
        if row.objectName() == f"settingRow_{key}":
            return row
        QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
        app.processEvents()
    raise AssertionError(f"回车遍历 {limit} 次仍未命中 {key}（query={query!r}）")


# --------------------------------------------------------------------------- V1 文案
def test_v1_forbidden_jargon_is_gone_from_the_production_files():
    """7 处禁词在生产文件里一处不留（纯文本 grep 口径，注释也算）。"""
    offenders = []
    for name in _PRODUCTION_FILES:
        text = (_REPO / name).read_text(encoding="utf-8")
        offenders += [f"{name}: {token}" for token in _FORBIDDEN_TOKENS if token in text]
    assert not offenders, "第一刀要清掉的黑话又回来了：\n" + "\n".join(offenders)


def test_v1_required_wording_is_present_where_it_belongs():
    """定稿要求的语义句必须真的落在对的文件里（换个说法就等于没改）。"""
    required = {
        "pet/music/settings.py": ("媒体控制插件", "通常留空即可"),
        "pet/settings_network.py": ("检测目标是固定的境内服务（阿里云）", "延迟检测间隔"),
        "pet/settings_reset_defaults.py": ("各服务商的 API Key", "每个自定义服务商只保留"),
    }
    missing = []
    for name, tokens in required.items():
        text = (_REPO / name).read_text(encoding="utf-8")
        missing += [f"{name}: {token}" for token in tokens if token not in text]
    assert not missing, "定稿语义句缺失：\n" + "\n".join(missing)


def test_v1_rewritten_rows_carry_the_final_wording(tmp_path, monkeypatch):
    """七处定稿的逐处落点（行级，含动态日流量与平台分支）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        # foobar2000 两行只在 Windows 建（窗口标题源走 Win32 枚举）——平台闸门，
        # 不是被整理掉了；非 Windows 上不建行，就没这条文案可查。
        if settings_music.foobar_lyrics_supported():
            lyrics = _row(dialog, "foobar_lyrics").hint_label.text()
            assert "媒体控制插件" in lyrics and "可搜索 foo_mediacontrol 插件" in lyrics
            assert "装好后不需要打开本开关" in lyrics
            assert "停止播放后可能仍显示上一首" in lyrics
            assert "SMTC" not in lyrics

            fmt = _row(dialog, "foobar_title_format")
            assert fmt.label.text() == "播放器标题格式（高级）"
            assert fmt.hint_label.text().startswith("通常留空即可。")
            assert "类似 %artist% - %title% 这样的格式" in fmt.hint_label.text()

        interval = _row(dialog, "network_probe_interval").hint_label.text()
        assert interval.startswith("延迟检测间隔（0.5–60 秒）。间隔越短反应越快、流量越多：")
        assert "当前约 " in interval and "MB/天（随设置实时更新）" in interval
        assert "检测目标是固定的境内服务（阿里云），不是你的访问网站。" in interval

        slot = _row(dialog, "island_network").hint_label.text()
        assert "速率监测不产生额外流量，延迟检测每次约 0.16 KB。" in slot
        # 合并岛段化后：网络是独立指标段，不再与信息栏内容互相挤占（旧架构那句
        # 「关闭后信息槽恢复显示…」随旧耦合一起作废）。
        assert "信息栏内容另有其行，不受本开关影响" in slot
        assert "网卡字节计数器" not in slot and "UDP" not in slot

        drop = _row(dialog, "drop_gravity").hint_label.text()
        assert drop.startswith("松开鼠标时如果宠物几乎没移动，它不再停在半空，而是自然落下、落地静止；")
        assert "贴地松手不会弹起。与「拖动物理」无关：关掉拖动物理后甩出惯性依然不生效。" in drop

        target = _row(dialog, "harness_launch_target").hint_label.text()
        assert "「自动」和「仅 dsh 服务」都会只拉起 dsh 后台服务（不打开界面）；" in target
        assert "右键菜单的手动启动不受此设置影响。" in target
        if sys.platform == "win32":
            assert "也可以显式选择打开 web 界面或桌面端。" in target
        else:
            assert "本平台暂不支持自动拉起桌面端" in target
    finally:
        dialog.close()


def test_v1_interval_hint_follows_the_spin_value(tmp_path, monkeypatch):
    """动态日流量必须跟着真实间隔走（定稿里的 X.X MB/天不是写死的）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog, "network_probe_interval")

        def per_day():
            return float(row.hint_label.text().split("当前约 ")[1].split(" MB/天")[0])

        dialog.network_probe_interval_spin.setValue(1.0)
        fast = per_day()
        dialog.network_probe_interval_spin.setValue(30.0)
        assert fast > per_day() > 0
    finally:
        dialog.close()


def test_v1_reset_confirm_keeps_the_rewritten_kept_items():
    """重置确认框保留项：各服务商密钥仍保留，地址/模型回默认，钥匙串条目不删。"""
    box, _confirm = settings_reset_defaults.build_reset_confirm_box(None)
    try:
        text = box.informativeText()
        assert "保留：AI 对话密钥——各服务商的 API Key 与视觉模型 Key" in text
        assert "每个自定义服务商只保留密钥本身，地址、模型等其它设置会恢复默认。" in text
        assert "系统钥匙串里的条目不会删除。" in text
        assert "只装密钥的条目" not in text
    finally:
        box.deleteLater()
        app.processEvents()


# ------------------------------------------------------------------ V2 高级折叠与组序
def test_v2_new_advanced_groups_are_advanced_and_collapsed(tmp_path, monkeypatch):
    """三个新高级组在**最终域**里 advanced=True 且默认收起；常用开关不得被误藏。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        music_title = settings_music.advanced_group_title(dialog)
        expected = {
            "harness_autostart": "启动项（高级）",
            "harness_launch_target": "启动项（高级）",
            "music_player_netease": music_title,
            "music_player_qqmusic": music_title,
            "music_player_kugou": music_title,
            "music_player_soda": music_title,
            "music_player_detect": music_title,
            "network_probe_interval": "网络延迟检测（高级）",
        }
        for key, title in expected.items():
            section = _section_of(dialog, key)
            assert section.advanced is True, f"{key} 必须在高级折叠组里"
            assert _section_title(section) == title
            assert section.card.isHidden(), f"「{title}」默认必须收起"
            assert section.toggle.isChecked() is False

        # 归属唯一：每个指定行有且只有一个 SettingsSection 祖先。
        for key in expected:
            parent, ancestors = _row(dialog, key).parentWidget(), []
            while parent is not None:
                if isinstance(parent, SettingsSection):
                    ancestors.append(parent)
                parent = parent.parentWidget()
            assert len(ancestors) == 1, f"{key} 的 SettingsSection 祖先必须唯一"

        # 常用开关一律留在主视线（未被误藏）。浏览器媒体会话开关只在 Windows 建行
        # （SMTC 是 Windows 专属能力），非 Windows 上没这一行可查。
        common = ["autostart", "music_sing", "music_lyric",
                  "drag_physics", "collision_enabled", "island_network",
                  "agent_sound_enabled"]
        if settings_music.browser_media_supported():
            common.append("music_browser_media")
        for key in common:
            assert _section_of(dialog, key).advanced is False, f"{key} 是常用开关，不得收进高级组"
        if settings_music.foobar_lyrics_supported():
            assert _section_of(dialog, "foobar_lyrics").advanced is False
            assert _section_of(dialog, "foobar_title_format").advanced is True
    finally:
        dialog.close()


def test_v2_pet_domain_group_order_is_per_tab_with_advanced_last(tmp_path, monkeypatch):
    """桌宠域页内标签（第二刀）的组序：每组只属于一个标签，高级组垫底其标签页。

    第一刀钉的是域级组序（7 个主视线组 + 2 个垫底高级组）；第二刀把域拆成
    「显示与音乐 / 动作与碰撞」两个标签，组序断言下移到标签内——覆盖不削弱：
    主视线组一个不少、相对顺序不变、两个高级组仍各自垫底。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        expected = {
            "display_music": _PET_TAB_SECTIONS["display_music"]
            + (settings_music.advanced_group_title(dialog),),
            "motion_collision": _PET_TAB_SECTIONS["motion_collision"] + ("碰撞参数（高级）",),
        }
        for tab_key, titles in expected.items():
            _page_widget, sections = _page_sections(dialog, "桌宠", tab_key)
            assert [_section_title(section) for section in sections] == list(titles), (
                f"标签 {tab_key} 的组序必须与裁决一致"
            )
            assert all(section.advanced is False for section in sections[:-1])
            assert sections[-1].advanced is True, "高级组必须垫底其所属标签页"
    finally:
        dialog.close()


def test_v2_general_domain_keeps_only_autostart_on_the_first_screen(tmp_path, monkeypatch):
    """常规域首屏：应用启动只剩「开机自启」；长文案的高级项默认收起。

    两组高级项垫底：「直播捕获（高级）」（只在 Windows 构造，其它平台整组不存在，
    按实际构造结果驱动断言）与「启动项（高级）」。「光标隐藏时自动穿透」已回
    「窗口与系统」主组——它是普通窗口旗标偏好，不该被折叠埋着。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        _page_widget, sections = _page_sections(dialog, "常规")
        titles = [_section_title(section) for section in sections]
        expected = ["应用启动", "窗口与系统"]
        if any(title == "直播捕获（高级）" for title in titles):
            expected.append("直播捕获（高级）")
        expected.append("启动项（高级）")
        assert titles == expected
        assert [row.objectName() for row in sections[0].rows] == ["settingRow_autostart"]
        # 高级组垫底且默认收起；「窗口与系统」仍在主视线里（主组，不是折叠）。
        assert sections[1].advanced is False
        for section in sections[2:]:
            assert section.advanced is True and section.card.isHidden(), (
                f"「{_section_title(section)}」必须是一层高级折叠且默认收起")
        window_group = sections[1]
        window_rows = [row.objectName() for row in window_group.rows]
        assert "settingRow_on_top" in window_rows, "置顶仍在主组"
        assert window_rows[-1] == "settingRow_cross_screen", (
            "「允许桌宠跨屏」是全局窗口偏好、三端都有行，排在窗口旗标类之后")
        if any(row == "settingRow_cursor_hidden_passthrough" for row in window_rows):
            assert window_rows[-2] == "settingRow_cursor_hidden_passthrough", (
                "光标穿透要在「窗口与系统」里（Windows 专属行，非 Windows 无此行）")

        capture = next((s for s in sections if _section_title(s) == "直播捕获（高级）"), None)
        if capture is not None:
            assert [row.objectName() for row in capture.rows] == ["settingRow_stream_capture"]
            # 行文案一字未改：重启级与平台限制都还在行内（分层只换位置）。
            row = capture.rows[0]
            assert "重启后生效" in row.hint_label.text()
            assert "Windows 10（1903 及以上）" in row.hint_label.text()
            # 作用域是行内独立标注（不再粘在说明末尾），仍在行内可见。
            assert "影响所有桌宠" in row.scope_note.text()
            assert capture.description_label.text().strip(), "收起时也要看得见一句摘要"
        advanced = sections[-1]
        assert [row.objectName() for row in advanced.rows] == [
            "settingRow_harness_autostart", "settingRow_harness_launch_target"]
    finally:
        dialog.close()


def test_v2_network_interval_is_split_out_in_the_final_domain(tmp_path, monkeypatch):
    """探测间隔在最终域单独成组，其余岛行不动；依赖未满足时不留空组/误导标题。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        interval_section = _section_of(dialog, "network_probe_interval")
        assert _section_title(interval_section) == "网络延迟检测（高级）"
        assert interval_section.advanced is True
        assert [row.objectName() for row in interval_section.rows] == [
            "settingRow_network_probe_interval"]
        # 其余岛行仍在原来的「桌面胶囊（灵动岛）」卡里
        assert _section_title(_section_of(dialog, "island_network")) == "桌面胶囊（灵动岛）"
        assert _section_title(_section_of(dialog, "dynamic_island_enabled")) == "桌面胶囊（灵动岛）"
        # 网络槽默认关着 → 间隔行被依赖隐藏，整组不该以空壳形式留在页面上
        assert _row(dialog, "network_probe_interval").isHidden()
        assert interval_section.isHidden(), "全行被依赖隐藏的高级组必须整组收起"
    finally:
        dialog.close()


def test_v2_new_advanced_groups_fit_the_compact_content_width(tmp_path, monkeypatch):
    """三个新高级组展开后在最小窗口（720×500 量级）里不越界、不裁切（GATES §3）。

    与既有 test_compact_agent_sound_controls_reflow_inside_their_setting_row 同法：
    量行在真实 scroll viewport 里的左右边界。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_network_check.setChecked(True)  # 网络组依赖满足才会显示
        dialog.resize(dialog.minimumSize())
        dialog.show()
        groups = {}
        for key, domain in (("harness_autostart", "常规"),
                            ("music_player_detect", "桌宠"),
                            ("network_probe_interval", "桌面组件")):
            section = _section_of(dialog, key)
            section.toggle.setChecked(True)
            groups[key] = _page(dialog, domain)[1]
        app.processEvents()

        for key, page in groups.items():
            dialog.sidebar.setCurrentRow(dialog.pages.indexOf(page))
            app.processEvents()
            row = _row(dialog, key)
            scroll = row.parentWidget()
            while scroll is not None and not isinstance(scroll, QScrollArea):
                scroll = scroll.parentWidget()
            assert scroll is not None
            left = row.mapTo(scroll.viewport(), row.rect().topLeft()).x()
            assert left >= 0, f"{key} 越出内容区左侧"
            assert left + row.width() <= scroll.viewport().width(), f"{key} 越出内容区右侧"
    finally:
        dialog.close()


def test_v2_group_title_degrades_without_a_foobar_row():
    """非 Windows（无标题格式行）时组名不含不存在的行：「播放器路径（高级）」。"""
    host = type("H", (), {"foobar_title_format_edit": None})()
    assert settings_music.advanced_group_title(host) == "播放器路径（高级）"
    host2 = type("H", (), {"foobar_title_format_edit": object()})()
    expected = ("播放器路径与标题格式（高级）" if sys.platform == "win32"
                else "播放器路径（高级）")
    assert settings_music.advanced_group_title(host2) == expected


def test_v1_long_hints_fit_their_labels_at_compact_width(tmp_path, monkeypatch):
    """长 hint 在最小窗口下按宽度换行、不纵向裁切（GATES §3「长文案」硬项）。

    量的是 ``QLabel.heightForWidth(实际宽) <= 实际高``：换行后需要的行数装不下才会红。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_network_check.setChecked(True)  # 网络组的行依赖满足才参与布局
        dialog.resize(dialog.minimumSize())
        dialog.show()
        keys = ["drop_gravity", "network_probe_interval", "harness_autostart", "harness_launch_target"]
        if sys.platform == "win32":
            keys += ["foobar_lyrics", "foobar_title_format"]
        for key in keys:  # 折叠组先展开，否则子行的几何是陈旧值（量不出裁切）
            section = _section_of(dialog, key)
            if section.advanced:
                section.toggle.setChecked(True)
        for index in range(dialog.pages.count()):  # 每页都过一遍，行才拿到真实几何
            dialog.sidebar.setCurrentRow(index)
            app.processEvents()
            page = dialog.pages.widget(index)
            tabs = page if isinstance(page, SettingsTabContainer) else page.findChild(
                SettingsTabContainer, "settingsTaskTabs")
            if tabs is not None:  # 页内标签域：每个标签都要成为当前页才有真实几何
                for tab_index in range(len(tabs.keys())):
                    tabs.setCurrentIndex(tab_index)
                    app.processEvents()
        for key in keys:
            label = _row(dialog, key).hint_label
            needed = label.heightForWidth(label.width())
            assert needed <= label.height(), f"{key} 的 hint 被纵向裁切（需要 {needed}px，只有 {label.height()}px）"
    finally:
        dialog.close()


# --------------------------------------------------------------------- V3 Agent 折叠框
def test_v3_agent_box_is_collapsed_and_only_holds_the_chime_rows(tmp_path, monkeypatch):
    """Agent 折叠框默认收起、只收提示音组，消费统计留在框外；展开后内容可见。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        chime_keys = ("agent_sound_enabled", "agent_sound_start", "agent_sound_done",
                      "agent_sound_error", "agent_sound_volume", "agent_sound_cooldown")
        box: CollapsibleGroup = dialog.agent_link_box
        assert box.is_expanded() is False
        assert box.body.isHidden()
        for key in chime_keys:
            assert box.isAncestorOf(_row(dialog, key)), f"{key} 必须留在提示音组里"
        assert not box.isAncestorOf(_row(dialog, "agent_cost")), "消费统计必须留在折叠框外"

        page, _sections = _page_sections(dialog, "自动化与联动")
        for key in chime_keys:
            assert _row(dialog, key).isVisibleTo(page) is False, "收起时内容不该可见"

        QTest.keyClick(box.toggle, Qt.Key.Key_Space)  # 键盘也能展开
        app.processEvents()
        assert box.is_expanded() is True
        assert box.body.isHidden() is False
        assert _row(dialog, "agent_sound_enabled").isVisibleTo(page)

        dialog.agent_sound_check.setChecked(True)  # 总开关打开：细项随之可见
        app.processEvents()
        for key in chime_keys:
            assert _row(dialog, key).isVisibleTo(page), f"{key} 展开后必须可见"
    finally:
        dialog.close()


# ------------------------------------------------------------------------ V4 搜索
def test_v4_search_expands_new_advanced_groups_and_reveals_the_row(tmp_path, monkeypatch):
    """搜「标题格式」→ 展开「播放器路径与标题格式（高级）」；搜「自启」回车遍历
    两次到 harness 两行 → 展开「启动项（高级）」。命中行必须**实际可见**。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        # 标题格式（含 foobar 的平台才有这一行；非 Windows 跳过该段）
        if sys.platform == "win32":
            row = _search(dialog, "标题格式")
            assert row.objectName() == "settingRow_foobar_title_format"
            section = _section_of(dialog, "foobar_title_format")
            assert section.toggle.isChecked() is True, "命中折叠组内的行必须自动展开"
            assert not section.card.isHidden()
            assert row.isVisibleTo(_current_page(dialog))

        # 自启：autostart → harness_autostart → harness_launch_target（回车遍历）
        for presses in (1, 2):
            row = _search(dialog, "自启", presses=presses)
            assert row.objectName() == f"settingRow_{('harness_autostart', 'harness_launch_target')[presses - 1]}"
            section = _section_of(dialog, row.objectName()[len("settingRow_"):])
            assert section.toggle.isChecked() is True
            assert row.isVisibleTo(_current_page(dialog)), "回车遍历命中的行必须可见"
            assert dialog.sidebar.currentItem().text() == "常规"
    finally:
        dialog.close()


def test_v4_search_expands_pre_existing_advanced_groups(tmp_path, monkeypatch):
    """既有 3 个高级组（碰撞参数 / 生成参数 / 高级配色）同样接入自动展开。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        for query, key in (("碰撞音量", "collision_sound_volume"),
                           ("超时", "timeout"),
                           ("背景色", "light_background")):
            row = _search_until(dialog, query, key)
            section = _section_of(dialog, key)
            assert section.advanced is True
            assert section.toggle.isChecked() is True, f"搜「{query}」必须展开该高级组"
            assert row.isVisibleTo(_current_page(dialog)), f"搜「{query}」命中的行必须可见"
    finally:
        dialog.close()


def test_v4_search_expands_the_agent_box_multi_match(tmp_path, monkeypatch):
    """搜「提示音」命中 5 行、全部落在同一折叠框内：折叠框自动展开且行可见。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        assert dialog.agent_link_box.is_expanded() is False, "先确认默认是收起的"
        row = _search(dialog, "提示音")
        assert dialog.agent_link_box.is_expanded() is True
        assert dialog.sidebar.currentItem().text() == "自动化与联动"
        assert row.isVisibleTo(_current_page(dialog))

        QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
        app.processEvents()
        second = _current_match(dialog)
        assert dialog.search_status.text().startswith("2/")
        assert dialog.agent_link_box.isAncestorOf(second)
    finally:
        dialog.close()


def test_v4_search_reports_the_hidden_dependency_without_changing_config(tmp_path, monkeypatch):
    """依赖不满足：不改配置、给可见的依赖说明；前置开关打开后同一查询能找到并展开。"""
    dialog, cfg = _cfg_dialog(tmp_path, monkeypatch)
    try:
        assert cfg.get("island_network_enabled") is False
        row = _search(dialog, "探测间隔")
        assert row.objectName() == "settingRow_network_probe_interval"
        page = _current_page(dialog)
        assert dialog.sidebar.currentItem().text() == "桌面组件"
        assert row.isVisibleTo(page) is False, "网络槽没开时这一行本就该隐藏"
        assert dialog.search_status.text() == "1/1 · 延迟探测间隔（需先开启「灵动岛指标 · 网络」）"
        # 依赖不满足绝不替用户改配置
        assert cfg.get("island_network_enabled") is False
        assert dialog.island_network_check.isChecked() is False
        assert dialog._search_matches == [row]

        dialog.island_network_check.setChecked(True)
        app.processEvents()
        row = _search(dialog, "探测间隔")
        section = _section_of(dialog, "network_probe_interval")
        assert section.isHidden() is False, "依赖满足后该组应重新出现"
        assert section.toggle.isChecked() is True, "命中后必须自动展开"
        assert row.isVisibleTo(page)
    finally:
        dialog.close()
