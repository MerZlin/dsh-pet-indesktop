# -*- coding: utf-8 -*-
"""节日提醒设置页的呈现回归：常用项平铺、少用项折叠、分组与文案。

机主 2026-10-13 原话（同日先验收语音页整理）：「节日提醒页面也修整一下」——
照语音页那套口径做：常用项平铺在最上面，少用/高级项折成一行（可展开、默认收起），
文案一句话 ≤20 字，界面不出现配置键名/内部机制/文件名/接口实现细节。

这一组用例锁五件事：

1. 「少用项」折叠＝**折叠头行 + 成员行 setVisible**：默认收起、点得开；常见项
   （总开关 / 何时提醒 / 提醒哪些日子 / 怎么提醒 / 生日）全留在平铺区；
2. 分组按心智分（何时提醒 → 提醒哪些日子 → 怎么提醒 → 生日 → 少用项），且行序
   在**真实设置对话框的域卡片**里成立——本页的行会被「语音」域整块收走，那一步只
   搬 ``SettingRow``、不搬容器，所以折叠与分组标题都只能落在行/卡片上；
3. 搜索命中折叠里的行必须自动展开（否则「搜到了却看不见」）；
4. 整页文案黑名单：任何行的标题 + 提示都不许出现配置键名 / 接口与实现细节；
5. 反向断言：从界面删掉的技术细节仍在模块 docstring 里（换地方存档，不是丢失）。

生日是**个人信息**：用例只用合成值（如 10-24），仓库里不得出现任何真实个人日期。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet.festival.settings import FestivalSettingsPage  # noqa: E402
from pet.festival.settings_panel import (  # noqa: E402
    COLLAPSE_TEXT,
    EXPAND_TEXT,
    FOLD_KEY,
    FestivalFoldPanel,
)
from pet.settings_widgets import SettingRow, SettingsCard  # noqa: E402

#: 设置页上不许出现的技术细节（与 2026-10-13 语音页同一口径）：那些只留在模块
#: docstring/注释里，界面不讲课。
TECHNICAL_MARKERS = (
    "http",
    ".com",
    "www.",
    "edge-tts",
    "edge_tts",
    "ffmpeg",
    "keyring",
    "config.json",
    "festival_",
    "接口",
    "错误码",
    "资源号",
    "uuid",
    "配置文件",
    "模块",
)

#: 折叠的成员行（少用/高级项）：自定义文案与试听
FOLDED_KEYS = (
    "festival_custom_quotes_cn",
    "festival_custom_quotes_west",
    "festival_preview",
)

#: 平铺在最上面的常用行，按预期先后顺序排列（含分组边界）
FLAT_KEYS = (
    # 何时提醒
    "festival_reminder_enabled",
    "festival_reminder_mode",
    "festival_reminder_count",
    "festival_reminder_times",
    # 提醒哪些日子
    "festival_reminder_cn",
    "festival_reminder_solar_terms",
    "festival_reminder_west",
    # 怎么提醒
    "festival_reminder_speak",
    "festival_reminder_animation",
    "festival_reminder_show_quote",
    # 生日
    "festival_birthday",
)

#: 分组标题（域卡片里的 ``sectionTitle``，按呈现顺序）
SECTION_TITLES = ("何时提醒", "提醒哪些日子", "怎么提醒", "你的生日", "少用项")

#: 行标题里不许出现的配置键名痕迹
KEY_LIKE = ("festival_", "_reminder", "settingRow")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _rows_by_name(widget) -> dict[str, SettingRow]:
    """行按 key 索引。

    域构建前这些行还没有 parent（页面自身不挂它们，整块要被「语音」域收走），
    所以不能只靠 ``findChildren``；域构建后两者一致。
    """
    page = getattr(widget, "festival_page", None) or widget
    panel = getattr(page, "_festival_panel", None)
    if panel is not None:
        return panel.row_map()
    return {
        row.objectName().replace("settingRow_", ""): row
        for row in widget.findChildren(SettingRow)
    }


def _domain(tmp_path, monkeypatch):
    """真实设置对话框里「语音」域的节日提醒卡片（域构建后的真实形态）。"""
    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
    voice_page = dialog.pages.widget(labels.index("语音"))
    page = dialog.festival_page
    return dialog, voice_page, page, FestivalFoldPanel.card_of(voice_page)


def _keys(card: SettingsCard) -> list[str]:
    return [row.objectName().replace("settingRow_", "") for row in card.rows]


def _section_card_keys(page) -> list[list[str]]:
    """域页里各分段卡片的行 key，按分段顺序（每个 ``SettingsSection`` 一张卡）。"""
    from PySide6.QtWidgets import QLabel

    from pet.settings_widgets import SettingsSection

    ordered: list[list[str]] = []
    for section in page.findChildren(SettingsSection):
        ordered.append(_keys(section.card))
    titles = [label.text() for label in page.findChildren(QLabel, "sectionTitle")]
    assert len(ordered) == len(titles), "每个分段标题都该配一张卡片"
    return ordered


def _flat_keys_of(page, wanted) -> list[str]:
    """域页里属于 ``wanted`` 的行 key，按分段卡片顺序拉平（保留呈现顺序）。"""
    wanted = set(wanted)
    return [key for keys in _section_card_keys(page) for key in keys if key in wanted]


def _section_titles(widget) -> list[str]:
    """域里出现的分段标题（按 QObject 树深度优先顺序）。"""
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in widget.findChildren(QLabel, "sectionTitle")]


# ============================================================ 折叠


def test_rare_rows_are_folded_by_default_and_common_ones_are_flat(tmp_path, monkeypatch):
    """默认：常用项平铺可见，自定义文案与试听收在折叠头后面（折起来≠藏起来）。"""
    dialog, voice_page, page, _card = _domain(tmp_path, monkeypatch)
    try:
        rows = _rows_by_name(voice_page)

        header = rows[FOLD_KEY]
        assert header.isVisibleTo(voice_page), "折叠头必须看得见"
        assert header.control.text() == EXPAND_TEXT
        assert "折叠" in header.label.text(), f"折叠头标题要说清它是什么：{header.label.text()}"

        for key in FLAT_KEYS:
            assert rows[key].isVisibleTo(voice_page), f"{key} 是常用项，必须平铺可见"
        for key in FOLDED_KEYS:
            assert not rows[key].isVisibleTo(voice_page), f"{key} 是少用项，默认应收起"
    finally:
        dialog.reject()


def test_fold_button_expands_and_collapses_the_rare_rows(tmp_path, monkeypatch):
    """点「展开」看得见少用项，再点「收起」收回去（配置键与读写不因此改变）。"""
    dialog, voice_page, _page, _card = _domain(tmp_path, monkeypatch)
    try:
        rows = _rows_by_name(voice_page)
        button = rows[FOLD_KEY].control

        button.click()
        assert button.text() == COLLAPSE_TEXT
        for key in FOLDED_KEYS:
            assert rows[key].isVisibleTo(voice_page), f"展开后 {key} 必须可见"

        button.click()
        assert button.text() == EXPAND_TEXT
        for key in FOLDED_KEYS:
            assert not rows[key].isVisibleTo(voice_page), f"收起后 {key} 必须看不见"
    finally:
        dialog.reject()


def test_fold_hides_only_presentation_never_drops_a_row(tmp_path, monkeypatch):
    """折叠不许吃掉行：整块行数恒定、顺序不变，收起只是 setVisible。"""
    dialog, voice_page, page, _card = _domain(tmp_path, monkeypatch)
    try:
        before = _flat_keys_of(voice_page, (*FLAT_KEYS, FOLD_KEY, *FOLDED_KEYS))
        assert len(before) == len(FLAT_KEYS) + len(FOLDED_KEYS) + 1  # +1 = 折叠头

        page._festival_panel.set_open(True)
        page._festival_panel.set_open(False)

        assert _flat_keys_of(voice_page, (*FLAT_KEYS, FOLD_KEY, *FOLDED_KEYS)) == before, (
            "展开/收起不得改动行清单与顺序"
        )
    finally:
        dialog.reject()


# ============================================================ 行序与分组


def test_group_titles_and_row_order_follow_the_mental_groups(tmp_path, monkeypatch):
    """分组标题按心智分；每组行序固定；折叠头与少用项收尾（最后一张卡）。"""
    dialog, voice_page, _page, _card = _domain(tmp_path, monkeypatch)
    try:
        assert _section_titles(voice_page) == ["语音报时", *SECTION_TITLES]
        keys = _flat_keys_of(voice_page, (*FLAT_KEYS, FOLD_KEY, *FOLDED_KEYS))
        assert keys[: len(FLAT_KEYS)] == list(FLAT_KEYS), "常用项必须按心智顺序平铺在最上面"
        assert keys[len(FLAT_KEYS)] == FOLD_KEY, "折叠头紧随常用项"
        assert keys[len(FLAT_KEYS) + 1 :] == list(FOLDED_KEYS), "少用项收在最后的折叠卡里"
    finally:
        dialog.reject()


def test_domain_card_keeps_the_order_after_the_domain_reparent(tmp_path, monkeypatch):
    """折叠头与折叠成员都落在「语音」域里，且整组没有被「待分类（开发期）」收走。"""
    dialog, voice_page, _page, _card = _domain(tmp_path, monkeypatch)
    try:
        header = dialog.findChild(SettingRow, f"settingRow_{FOLD_KEY}")
        assert header is not None, "折叠头行必须存在"
        assert voice_page.isAncestorOf(header), "折叠头不能在域外"
        assert header.isVisibleTo(voice_page), "折叠头在域卡片里必须看得见"
        for key in (*FLAT_KEYS, *FOLDED_KEYS):
            row = dialog.findChild(SettingRow, f"settingRow_{key}")
            assert row is not None and voice_page.isAncestorOf(row), f"{key} 必须在「语音」域"

        from PySide6.QtWidgets import QLabel

        assert "待分类（开发期）" not in [
            label.text() for label in dialog.findChildren(QLabel)
        ]
    finally:
        dialog.reject()


def test_search_reveals_a_row_inside_the_fold(tmp_path, monkeypatch):
    """搜索命中折叠里的行：必须自动展开，否则「搜到了却看不见」。"""
    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
        voice_page = dialog.pages.widget(labels.index("语音"))
        match_key = "settingRow_festival_custom_quotes_cn"
        assert not dialog.findChild(SettingRow, match_key).isVisibleTo(voice_page), (
            "前提：自定义文案默认是收起的"
        )

        dialog.search_edit.setText("自定义中文文案")
        app.processEvents()
        matched = dialog._search_matches[dialog._search_index]
        assert matched.objectName() == match_key
        assert matched.isVisibleTo(voice_page), "命中折叠里的行必须自动展开"
        assert dialog.sidebar.currentItem().text() == "语音"
    finally:
        dialog.reject()
        app.processEvents()


# ============================================================ 文案


def test_every_row_copy_is_one_short_sentence(tmp_path):
    """每个文案行都是一句话：标题说人话、提示 ≤20 字（机主口径）。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    rows = page._festival_panel.rows()
    assert len(rows) == len(FLAT_KEYS) + len(FOLDED_KEYS) + 1
    for row in rows:
        title = row.label.text()
        hint = row.hint_label.text()
        for marker in KEY_LIKE:
            assert marker not in title, f"{row.objectName()} 标题里出现了配置键名：{title}"
        assert 0 < len(title) <= 14, f"{row.objectName()} 标题太长：{title}"
        # 「语音播报」是唯一允许超长的行：它必须交代**免打扰期间不出声**（否则用户会
        # 以为功能坏了，见 test_festival.test_speak_hint_mentions_quiet_mode），
        # 还要说清与报时共用通道、同一分钟由报时让位。一句话说不完，按信息量让步。
        if row.objectName() == "settingRow_festival_reminder_speak":
            assert len(hint) <= 60, f"{row.objectName()} 提示过长（>60 字）：{hint}"
            continue
        assert 0 < len(hint) <= 20, f"{row.objectName()} 提示必须是一句话 ≤20 字：{hint}"


def test_page_never_teaches_technical_details(tmp_path):
    """接口地址 / 配置键名 / 实现库名这类细节一个字都不许出现在界面上。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    for row in page._festival_panel.rows():
        text = f"{row.label.text()} {row.hint_label.text()}".lower()
        for marker in TECHNICAL_MARKERS:
            assert marker.lower() not in text, (
                f"{row.objectName()} 又把技术细节摆上界面了：{text}"
            )


def test_settings_module_keeps_the_technical_details():
    """从界面删掉的技术细节必须还在模块 docstring 里（换地方存档，不是丢掉）。"""
    from pet.festival import settings as festival_settings

    doc = festival_settings.__doc__ or ""
    assert "festival_reminder_speak" in doc, "配置键清单要留在模块文档里"
    assert "festival_service" in doc, "与报时服务的让位机制要留在模块文档里"
    assert "modern_settings_dialog" in doc, "接线约定要留在模块文档里"


# ============================================================ 功能面不变


def test_controls_and_round_trip_are_unchanged(tmp_path):
    """折叠只改呈现：控件仍在、写回与刷新仍按原语义（生日用合成值）。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    page.enabled_check.setChecked(True)
    page.mode_select.setCurrentData("custom")
    page.times_edit.setText("09:00, 20:00")
    page.birthday_edit.setText("10-24")
    page.custom_cn_edit.setPlainText("今宵剩把银釭照，犹恐相逢是梦中。")
    page.apply_to_config()

    assert page.config.get("festival_reminder_enabled") is True
    assert page.config.get("festival_reminder_mode") == "custom"
    assert page.config.get("festival_reminder_times") == "09:00, 20:00"
    assert page.config.get("festival_birthday") == "10-24"

    page.birthday_edit.setText("99-99")
    page.custom_cn_edit.setPlainText("改脏")
    page.refresh_from_config()
    assert page.birthday_edit.text() == "10-24"
    assert page.custom_cn_edit.toPlainText() == "今宵剩把银釭照，犹恐相逢是梦中。"


def test_fold_members_stay_reachable_when_expanded(tmp_path):
    """折叠成员行展开后仍是可用控件（收起不是禁用）。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    page._festival_panel.set_open(True)
    rows = _rows_by_name(page)
    for key in FOLDED_KEYS:
        assert rows[key].control.isEnabled(), f"{key} 展开后必须是可用控件"
    assert rows["festival_preview"].control.text() == "立即试听"


#: 本页唯一该写进 config 的键（整理不得新增/改名/少写）
CONFIG_KEYS = (
    "festival_reminder_enabled",
    "festival_reminder_cn",
    "festival_reminder_solar_terms",
    "festival_reminder_west",
    "festival_reminder_mode",
    "festival_reminder_count",
    "festival_reminder_times",
    "festival_reminder_show_quote",
    "festival_reminder_speak",
    "festival_reminder_animation",
    "festival_birthday",
    "festival_custom_quotes_cn",
    "festival_custom_quotes_west",
)


class _Recorder:
    """只记录写入的 config 替身（不落盘、不碰 schema）。"""

    def __init__(self):
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return default

    def set(self, key, value) -> None:
        self.writes.append((key, value))


def test_apply_writes_exactly_the_same_thirteen_keys():
    """写回白名单与整理前完全一致：不新增键，也不漏写。"""
    _qapp()
    recorder = _Recorder()
    page = FestivalSettingsPage(recorder)
    page.apply_to_config()

    assert [key for key, _ in recorder.writes] == list(CONFIG_KEYS)


def test_fold_header_is_a_presentation_row_only(tmp_path):
    """折叠头是纯展示行：不是配置键，控件就是那颗展开/收起按钮。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    header = page._festival_panel.row_map()[FOLD_KEY]
    assert header.control is page.fold_toggle, "折叠头的控件就是那颗展开/收起按钮"
    assert header.objectName().replace("settingRow_", "") not in CONFIG_KEYS


def test_mode_switch_keeps_enabling_the_right_time_control(tmp_path):
    """提醒方式的启停逻辑原样保留：次数与自定义时间互斥生效。"""
    from pet.config import Config

    _qapp()
    page = FestivalSettingsPage(Config(base=tmp_path))
    page.mode_select.setCurrentData("times")
    assert page.count_spin.isEnabled() and not page.times_edit.isEnabled()
    page.mode_select.setCurrentData("custom")
    assert not page.count_spin.isEnabled() and page.times_edit.isEnabled()
