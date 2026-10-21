# -*- coding: utf-8 -*-
"""设置页「音乐播放器路径」（pet/settings_music.py）的聚焦回归。

产品契约：
- **四个播放器**（网易云 / QQ音乐 / 酷狗 / 汽水）各一行，写回
  ``music_player_paths``（空 = 删掉该键，回到自动搜索）；
- 路径变化时清掉播放器路径缓存，让右键菜单下次拿到确定态；
- 「自动检测」在后台预热 + 只读缓存轮询回填，**绝不在 GUI 线程扫盘**；
- 冷缓存时继续等、超时也会收尾（不留一个永远转的定时器）；
- 找不到时给出可见说明（而不是静默什么都不发生）；
- 这些路径**只**决定右键菜单怎么启动播放器；歌词显示走系统媒体会话，与路径无关，
  行文案必须写明这一点（否则用户会以为歌词坏了是路径没填）。
"""

from __future__ import annotations

import pytest

from pet.music import players as music_players, settings as settings_music
from pet.settings_widgets import ToggleSwitch, _line_edit

pytest.importorskip("PySide6")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Messages:
    """捕获 QMessageBox.information 调用（不弹真窗口）。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def information(self, parent, title, text, *args, **kwargs):
        self.calls.append((str(title), str(text)))
        return None


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dlg = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    yield dlg
    dlg.close()
    app.processEvents()


#: 契约：四个播放器的**控件属性名**与**行键**。在这里逐字钉住（而不是从
#: ``settings_music`` 反查），否则产品侧把某个播放器漏掉时用例会跟着一起漏。
PLAYER_ORDER = ("netease", "qqmusic", "kugou", "soda")
PLAYER_ATTRS = {
    "netease": "music_player_netease_picker",
    "qqmusic": "music_player_qqmusic_picker",
    "kugou": "music_player_kugou_picker",
    "soda": "music_player_soda_picker",
}
PLAYER_ROWS = {
    "netease": "music_player_netease",
    "qqmusic": "music_player_qqmusic",
    "kugou": "music_player_kugou",
    "soda": "music_player_soda",
}
#: 用户搜这四个播放器时会打的词（设置页搜索是子串匹配，行标题里必须有它）。
PLAYER_QUERIES = {
    "netease": "网易云",
    "qqmusic": "QQ音乐",
    "kugou": "酷狗",
    "soda": "汽水",
}


def _picker(dialog, key):
    picker = getattr(dialog, PLAYER_ATTRS[key], None)
    assert picker is not None, f"缺少控件 {PLAYER_ATTRS[key]}"
    return picker


def _pickers(dialog):
    return tuple(_picker(dialog, key) for key in PLAYER_ORDER)


def _row(dialog, key):
    from pet.modern_settings_dialog import SettingRow

    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def _section_title(dialog, key):
    from pet.modern_settings_dialog import SettingsSection

    parent = _row(dialog, key).parentWidget()
    while parent is not None and not isinstance(parent, SettingsSection):
        parent = parent.parentWidget()
    assert parent is not None, f"{key} 不在任何设置分区里"
    return parent.toggle.text()


def _search_row_keys(dialog, query: str) -> list[str]:
    """真实搜索框输入（textChanged）+ 事件循环，返回命中行键（按候选次序）。"""
    from PySide6.QtWidgets import QApplication

    dialog.search_edit.setText("")
    QApplication.processEvents()
    dialog.search_edit.setText(query)
    for _ in range(3):
        QApplication.processEvents()
    matches = dialog._search_matches
    assert matches, f"搜索「{query}」没有命中任何行"
    return [row.objectName()[len("settingRow_"):] for row in matches]


def test_rows_sit_in_the_pet_domain_music_group(dialog):
    """四行 + 检测按钮落在「桌宠 · 音乐关联」组里（掉进「待分类」就等于没做）。"""
    from PySide6.QtWidgets import QWidget

    keys = tuple(PLAYER_ROWS[key] for key in PLAYER_ORDER) + ("music_player_detect",)
    rows = [dialog.findChild(QWidget, f"settingRow_{key}") for key in keys]
    assert all(row is not None for row in rows)

    page = None
    for index in range(dialog.pages.count()):
        candidate = dialog.pages.widget(index)
        if candidate.isAncestorOf(rows[0]):
            page = candidate
            break
    assert page is not None
    assert dialog.sidebar.item(dialog.pages.indexOf(page)).text() == "桌宠"
    assert "待分类（开发期）" not in [row.objectName() for row in rows]


def test_saving_paths_persists_for_all_four_players_and_clears_cache(dialog, monkeypatch):
    cleared: list[bool] = []
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: cleared.append(True))

    saved = {
        "netease": r"D:\CloudMusic\cloudmusic.exe",
        "qqmusic": r"D:\QQ音乐\QQMusic\QQMusic.exe",
        "kugou": r"D:\Kugou\kugou.exe",
        "soda": r"D:\SodaMusic\soda.exe",
    }
    for key, path in saved.items():
        _picker(dialog, key).setText(path)
    dialog._save()

    assert dialog.config.get("music_player_paths") == saved
    assert cleared == [True], "路径变了必须清缓存，否则菜单还拿着旧的负缓存"


def test_key_set_matches_the_backend_player_registry():
    """不漏也不多：设置页的播放器集 == 注册表（``music_players`` 是唯一真源）。

    注册表里多一个而设置页少一个 = "用户没地方填这个播放器的路径"（本次报的正是这个：
    注册表有四个、设置页只有两个）；设置页多一个 = 写出一条没人读的路径。后端把键表
    收敛到 ``player_keys()`` 之后，这条用例同时钉住"设置页没有另抄一份固定键表"。
    """
    assert set(settings_music.PLAYER_KEYS) == set(music_players.player_keys())
    reported = {"netease", "qqmusic", "kugou", "soda"}
    assert reported <= set(settings_music.PLAYER_KEYS), "用户报缺的酷狗 / 汽水必须在设置面里"


def test_every_registered_player_has_a_picker_a_row_and_its_own_label(dialog):
    """注册表里的**每一个**播放器都有独立控件、独立行，标题用注册表登记的显示名。"""
    from pet.settings_widgets import ResourcePathPicker

    keys = music_players.player_keys()
    pickers = [_picker(dialog, key) for key in keys]
    assert len({id(picker) for picker in pickers}) == len(keys), "每个播放器必须各有独立控件"
    for key in keys:
        assert settings_music._ROW_KEY[key] == f"music_player_{key}"
        assert settings_music._PICKER_ATTR[key] == f"music_player_{key}_picker"
        assert isinstance(_picker(dialog, key), ResourcePathPicker)

        label = music_players.player_label(key)
        assert label and label != key, f"{key} 没在注册表里登记显示名，行标题会漏出内部键"
        assert _row(dialog, f"music_player_{key}").label.text() == f"{label}程序"


def test_rows_follow_the_registry_order_before_the_detect_button(dialog):
    """行序 = 注册表序（网易云 / QQ音乐 / 酷狗 / 汽水），检测按钮垫底。"""
    from pet.modern_settings_dialog import SettingRow

    card = _row(dialog, "music_player_netease").parentWidget()
    layout = card.layout()
    assert layout is not None
    ordered = [
        layout.itemAt(index).widget().objectName()[len("settingRow_"):]
        for index in range(layout.count())
        if isinstance(layout.itemAt(index).widget(), SettingRow)
    ]
    expected = [f"music_player_{key}" for key in music_players.player_keys()]
    assert ordered[-len(expected) - 1:] == expected + ["music_player_detect"], ordered


def test_the_four_rows_and_the_detect_button_share_the_advanced_player_group(dialog):
    """归属唯一：四行与检测按钮同属「播放器路径…」高级折叠组（不散落、不掉「待分类」）。

    组名随「标题格式」行在不在而退化（那行只在 Windows 建：窗口标题源走 Win32 枚举），
    所以期望值走**平台 contract**（``advanced_group_title`` + 平台支持的
    ``PLAYER_GROUP_TITLE`` / ``PLAYER_GROUP_TITLE_NO_FOOBAR``）：写死 Windows 组名会让
    非 Windows 上的这条断言必红，skip 掉非 Windows 又等于不验证退化路径。
    """
    from pet.modern_settings_dialog import SettingRow

    group = settings_music.advanced_group_title(dialog)
    for key in PLAYER_ORDER:
        assert _section_title(dialog, PLAYER_ROWS[key]) == group
    assert _section_title(dialog, "music_player_detect") == group

    has_title_row = dialog.findChild(SettingRow, "settingRow_foobar_title_format") is not None
    assert has_title_row is settings_music.foobar_lyrics_supported(), (
        "「标题格式」行的在场必须与平台 contract 一致"
    )
    expected = (settings_music.PLAYER_GROUP_TITLE if has_title_row
                else settings_music.PLAYER_GROUP_TITLE_NO_FOOBAR)
    assert group == expected, f"组名与「标题格式」行是否在场不一致：{group}"
    assert group in (settings_music.PLAYER_GROUP_TITLE, settings_music.PLAYER_GROUP_TITLE_NO_FOOBAR)


def test_every_player_is_findable_by_the_name_users_type(dialog):
    """搜索不漏：网易云 / QQ音乐 / 酷狗 / 汽水四个词各自命中自己的那一行。"""
    for key, query in PLAYER_QUERIES.items():
        hits = _search_row_keys(dialog, query)
        assert PLAYER_ROWS[key] in hits, f"「{query}」应命中 {PLAYER_ROWS[key]}，实际 {hits[:5]}"


def test_rows_say_the_paths_only_drive_the_context_menu_launch(dialog):
    """文案必须写清代价与边界：路径只影响右键菜单启动，歌词显示与它无关。"""
    for key in PLAYER_ORDER:
        hint = _row(dialog, PLAYER_ROWS[key]).hint_label.text()
        assert "右键菜单" in hint, f"{PLAYER_ROWS[key]} 没写清路径只作用于右键菜单启动"
        assert "歌词" in hint and "与本路径无关" in hint, (
            f"{PLAYER_ROWS[key]} 没写明歌词显示不依赖这个路径（用户会以为歌词坏了是没填路径）"
        )
    detect_hint = _row(dialog, "music_player_detect").hint_label.text()
    assert "上面四行" in detect_hint, "检测行还在说「上面两行」就等于没补酷狗 / 汽水"
    assert "与本路径无关" in detect_hint


def test_saved_paths_come_back_after_reopening_the_settings(dialog, tmp_path, monkeypatch):
    """保存重开：新开的设置窗要按盘上的值回填（四项都走一遍读回路径）。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    saved = {
        "netease": r"D:\CloudMusic\cloudmusic.exe",
        "kugou": r"D:\Kugou\kugou.exe",
        "soda": r"D:\SodaMusic\soda.exe",
    }
    for key, path in saved.items():
        _picker(dialog, key).setText(path)
    dialog._save()

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    reopened = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        for key in PLAYER_ORDER:
            assert _picker(reopened, key).text() == saved.get(key, ""), key
    finally:
        reopened.close()
        _qapp().processEvents()


def test_partial_config_fills_only_that_picker_and_survives_saving(tmp_path, monkeypatch):
    """缺项：盘上只有一项（旧版本只存过网易云 / QQ）时，其余三行留空且保存不吞不回填。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    partial = {"soda": r"D:\SodaMusic\soda.exe"}
    cfg = Config(tmp_path)
    cfg.set("music_player_paths", partial)
    cfg.save()

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        assert _picker(dialog, "soda").text() == r"D:\SodaMusic\soda.exe"
        for key in ("netease", "qqmusic", "kugou"):
            assert _picker(dialog, key).text() == ""
        dialog._save()
        assert Config(tmp_path).get("music_player_paths") == partial
    finally:
        dialog.close()
        _qapp().processEvents()


def test_malformed_config_value_leaves_all_four_pickers_empty(tmp_path, monkeypatch):
    """错误输入：内存态里不是 dict 的值不能让设置页炸，也不能泄漏进控件。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    for malformed in ("not-a-dict", ["netease"], 42, None):
        cfg = Config(tmp_path)
        cfg.data["music_player_paths"] = malformed
        dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=True)
        try:
            for key in PLAYER_ORDER:
                assert _picker(dialog, key).text() == "", f"{malformed!r} 泄漏进了 {key}"
            dialog._save()
            assert dialog.config.get("music_player_paths") == {}
            # 落盘形态也要干净（对话框的配置面是路由视图，读盘用主配置）。
            assert Config(tmp_path).get("music_player_paths") == {}
        finally:
            dialog.close()
            _qapp().processEvents()


def test_invalid_path_is_kept_verbatim_and_reported_missing(dialog, monkeypatch):
    """错误输入：填了不存在的程序——原样存下（不偷偷回退自动搜索），查询报确定态缺失。"""
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    _picker(dialog, "kugou").setText("  D:/nope/kugou.exe  ")
    dialog._save()

    stored = dialog.config.get("music_player_paths")["kugou"]
    assert stored == "D:/nope/kugou.exe", "首尾空白要 strip"
    state, path = music_players.cached_player("kugou", stored)
    assert (state, path) == (music_players.CACHED_MISSING, None)


def test_whitespace_only_paths_are_dropped_like_an_empty_row(dialog, monkeypatch):
    """错误输入：只填空格等于没填——回到自动搜索，不该写出一个空格路径。"""
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    for key in PLAYER_ORDER:
        _picker(dialog, key).setText("   ")
    dialog._save()
    assert dialog.config.get("music_player_paths") == {}


def test_no_chat_variant_builds_and_saves_all_four_paths(tmp_path, monkeypatch):
    """打包变体（excludes=['pet.chat']）：不依赖 chat 页，四行照建、照存。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        assert dialog.ai_page is None
        saved = {"netease": "D:/a.exe", "qqmusic": "D:/b.exe",
                 "kugou": "D:/c.exe", "soda": "D:/d.exe"}
        for key, path in saved.items():
            _picker(dialog, key).setText(path)
        dialog._save()
        assert Config(tmp_path).get("music_player_paths") == saved
    finally:
        dialog.close()
        _qapp().processEvents()


def test_path_rows_are_not_platform_gated(tmp_path, monkeypatch):
    """平台：路径是交给右键菜单启动的，三方平台都建行（不做"永久不支持就不建控件"处理）。

    非 Windows 上``music_players`` 自己会拒服务（``_search_filesystem`` 直接返回 None），
    设置页不该跟着消失——否则跨平台用户连"手动指定"的入口都没有。
    """
    import sys

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        for key in PLAYER_ORDER:
            assert _picker(dialog, key).isEnabled() is True
            assert _row(dialog, PLAYER_ROWS[key]) is not None
        assert dialog.music_player_detect_btn.isEnabled() is True
    finally:
        dialog.close()
        _qapp().processEvents()


def test_empty_paths_drop_the_key_and_restore_auto_search(dialog, monkeypatch):
    from pet.config import Config
    from pet.music import settings as module

    dialog.config.set("music_player_paths", {"netease": "D:/old/cloudmusic.exe"})
    dialog.config.save()
    cleared: list[bool] = []
    monkeypatch.setattr(module.music_players, "clear_cache", lambda: cleared.append(True))

    for picker in _pickers(dialog):
        picker.setText("")
    dialog._save()

    assert dialog.config.get("music_player_paths") == {}
    assert cleared == [True]
    assert Config(base=dialog.config.dir).get("music_player_paths") == {}


def test_unchanged_paths_do_not_touch_the_cache(dialog, monkeypatch):
    """路径没变就不要清缓存（避免每次保存都让菜单重新扫盘）。"""
    monkeypatch.setattr(
        settings_music.music_players, "warm_cache_async", lambda *a, **k: False
    )
    cleared: list[bool] = []

    netease = _picker(dialog, "netease")
    netease.setText(r"D:\CloudMusic\cloudmusic.exe")
    dialog.config.set("music_player_paths", {"netease": r"D:\CloudMusic\cloudmusic.exe"})

    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: cleared.append(True))
    dialog._save()

    assert cleared == []


def test_detect_fills_pickers_and_stops_the_timer(dialog, monkeypatch):
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    warmed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_music.music_players,
        "warm_cache_async",
        lambda key, manual="": warmed.append((key, manual)) or True,
    )

    found = {"netease": r"D:\CloudMusic\cloudmusic.exe", "kugou": r"D:\Kugou\kugou.exe"}

    def lookup(key, manual=""):
        path = found.get(key)
        return (music_players.CACHED_FOUND, path) if path else (music_players.CACHED_MISSING, None)

    settings_music.start_detect(dialog, lookup=lookup)

    for key in PLAYER_ORDER:
        assert _picker(dialog, key).text() == found.get(key, ""), key
    assert sorted(warmed) == sorted((key, "") for key in PLAYER_ORDER), (
        "四个播放器都要触发一次后台搜索"
    )
    assert dialog._music_detect_timer.isActive() is False, "出结果后必须停掉轮询"
    assert dialog.music_player_detect_btn.isEnabled() is True
    assert dialog.music_player_detect_btn.text() == "自动检测"

    title, text = messages.calls[-1]
    assert title == "自动检测播放器"
    for key in PLAYER_ORDER:
        label = music_players.player_label(key)
        expected = found.get(key) or "未找到"
        assert f"{label}：{expected}" in text, f"结果弹窗漏了 {label}"


def test_detect_result_dialog_lists_every_player_even_when_nothing_is_found(dialog, monkeypatch):
    """缺项：四个都搜不到时，弹窗也要逐项说"未找到"（不是一句"没找到播放器"）。"""
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)

    def missing(key, manual=""):
        return music_players.CACHED_MISSING, None

    settings_music.start_detect(dialog, lookup=missing)

    title, text = messages.calls[-1]
    assert title == "自动检测播放器"
    for key in PLAYER_ORDER:
        assert f"{music_players.player_label(key)}：未找到" in text
    assert text.count("未找到") == len(PLAYER_ORDER), "四项各一行，不许合并成一句"


def test_detect_keeps_waiting_while_cache_is_cold(dialog, monkeypatch):
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)

    def cold(key, manual=""):
        return music_players.CACHED_COLD, None

    settings_music.start_detect(dialog, lookup=cold)

    assert dialog._music_detect_timer.isActive() is True, "还在扫盘时应继续轮询"
    assert messages.calls == [], "没结果就不该弹结论"
    assert dialog.music_player_detect_btn.text() == "检测中…"

    dialog._music_detect_timer.stop()


def test_detect_budget_covers_every_serialized_player_scan(dialog, monkeypatch):
    """四个 key 的扫描是**串行**的：总预算必须按播放器数放大。

    回归的是一个真机测出来的缺陷：总预算写死 20s 时，本机四个 key 串行要 24.95s 才
    全部出结果 ⇒ 第三个还没扫完就超时收尾，把找得到的播放器（酷狗）报成「未找到」。
    """
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)
    clock = {"now": 10_000.0}
    monkeypatch.setattr(settings_music.time, "monotonic", lambda: clock["now"])

    def cold(key, manual=""):
        return music_players.CACHED_COLD, None

    settings_music.start_detect(dialog, lookup=cold)
    per_player = settings_music.DETECT_TIMEOUT_PER_PLAYER_SECONDS
    assert len(settings_music.PLAYER_KEYS) >= 2, "前提：至少两个播放器才谈得上串行放大"

    # 单个播放器的预算用尽（20s）时**还必须等**——后面的播放器还没轮到扫。
    clock["now"] += per_player + 0.5
    settings_music.poll_detect(dialog)
    assert messages.calls == [], "四播放器串行时按旧的两键预算收尾会把结果丢一半"
    assert dialog._music_detect_timer.isActive() is True

    # 全部预算用尽才收尾（兜底仍在：不会留一个永远转的定时器）。
    clock["now"] += per_player * len(settings_music.PLAYER_KEYS)
    settings_music.poll_detect(dialog)
    assert dialog._music_detect_timer.isActive() is False
    assert messages.calls and "未完成" in messages.calls[-1][1]


def test_detect_budget_is_derived_not_hardcoded():
    """预算与键表同源：注册表多一个播放器，串行扫描的兜底预算跟着长。"""
    assert settings_music.DETECT_TIMEOUT_SECONDS == (
        settings_music.DETECT_TIMEOUT_PER_PLAYER_SECONDS * len(settings_music.PLAYER_KEYS)
    )
    assert settings_music.DETECT_TIMEOUT_SECONDS >= 20.0 * len(music_players.player_keys())


def test_detect_timeout_finishes_and_reports(dialog, monkeypatch):
    """超时兜底：不会因为一个永远 cold 的 key 留下永远转的定时器。"""
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)

    def cold(key, manual=""):
        return music_players.CACHED_COLD, None

    dialog._music_detect_lookup = cold
    dialog._music_detect_results = {}
    dialog._music_detect_deadline = 0.0
    settings_music.poll_detect(dialog)

    assert messages.calls and "未完成" in messages.calls[-1][1]
    assert dialog.music_player_detect_btn.isEnabled() is True
    assert messages.calls[-1][1].count("：未完成（") == len(PLAYER_ORDER), "四项都要如实报告扫描未完成"


def test_detected_path_is_written_to_config_on_save(dialog, monkeypatch):
    """检测回填 → 保存 → 配置里真的有了（端到端最小链路，四项都在场）。"""
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)

    found = {"netease": r"D:\CloudMusic\cloudmusic.exe", "soda": r"D:\SodaMusic\soda.exe"}

    def lookup(key, manual=""):
        if key in found:
            return music_players.CACHED_FOUND, found[key]
        return music_players.CACHED_MISSING, None

    settings_music.start_detect(dialog, lookup=lookup)
    dialog._save()

    assert dialog.config.get("music_player_paths") == found


# ------------------------------------------------- 浏览器媒体会话开关：平台契约
#
# SMTC 是 Windows 专属能力。非 Windows 上这个开关既没有效果也不该出现在设置页
# （SETTINGS-CHANGE-GATES §4「永久不支持的能力不创建控件」），所以控件与设置行
# 都只在 Windows 创建——与 cursor_hidden_passthrough 的处置一致。


def test_browser_media_row_is_absent_on_non_windows(monkeypatch):
    """非 Windows 平台：即使控件存在，设置行也不出现（平台闸门优先）。"""
    import sys

    from pet.settings_widgets import ToggleSwitch

    _qapp()
    host = type("H", (), {"music_browser_media_check": ToggleSwitch()})()

    monkeypatch.setattr(sys, "platform", "linux")
    assert settings_music.browser_media_supported() is False
    assert settings_music.browser_media_rows(host) == []

    monkeypatch.setattr(sys, "platform", "darwin")
    assert settings_music.browser_media_rows(host) == []

    monkeypatch.setattr(sys, "platform", "win32")
    assert settings_music.browser_media_supported() is True
    rows = settings_music.browser_media_rows(host)
    assert [row.objectName() for row in rows] == ["settingRow_music_browser_media"]


def test_browser_media_row_is_absent_when_widget_missing():
    """控件没建（非 Windows 的实际形态）时行也不建：不留"只有行没有控件"的残骸。"""
    _qapp()
    host = type("H", (), {"music_browser_media_check": None})()

    assert settings_music.browser_media_rows(host) == []


def test_browser_media_widget_is_windows_only(monkeypatch, tmp_path):
    """控件本体同样只在 Windows 创建（设置页构造路径）。"""
    import sys

    from pet import settings_pet_controls
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_pet_controls.sys, "platform", "linux")
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert dialog.music_browser_media_check is None, "非 Windows 不该建这个控件"
    # 缺控件时保存路径要安全跳过（不许抛，也不许把它写成 False）
    dialog._save()
    dialog.reject()
    app.processEvents()


# ------------------------------------------------------------------ #228
#
# foobar2000 歌词（窗口标题源）。平台闸门与 browser_media 同规：控件与设置行都只在
# Windows 创建，非 Windows 连控件都不建（免得出现"控件在、行不在"的游离开关）。


def test_foobar_lyrics_rows_are_absent_on_non_windows(monkeypatch):
    """非 Windows 平台：即使控件存在，两行也不出现（平台闸门优先）。"""
    import sys

    from pet.settings_widgets import ToggleSwitch

    _qapp()
    host = type(
        "H",
        (),
        {
            "foobar_lyrics_check": ToggleSwitch(),
            "foobar_title_format_edit": _line_edit(""),
        },
    )()

    for platform in ("linux", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        assert settings_music.foobar_lyrics_supported() is False
        assert settings_music.foobar_lyrics_rows(host) == []

    monkeypatch.setattr(sys, "platform", "win32")
    assert settings_music.foobar_lyrics_supported() is True
    rows = settings_music.foobar_lyrics_rows(host)
    assert [row.objectName() for row in rows] == [
        "settingRow_foobar_lyrics",
        "settingRow_foobar_title_format",
    ]


def test_foobar_lyrics_rows_are_absent_when_widgets_missing():
    """控件没建（非 Windows 的实际形态）时行也不建——不留孤儿行。"""
    _qapp()
    host = type("H", (), {"foobar_lyrics_check": None, "foobar_title_format_edit": None})()
    assert settings_music.foobar_lyrics_rows(host) == []


def test_foobar_lyrics_widgets_are_windows_only(monkeypatch, tmp_path):
    """控件本体同样只在 Windows 创建；缺控件时保存路径安全跳过。"""
    import sys

    from pet import settings_pet_controls
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_pet_controls.sys, "platform", "linux")
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert dialog.foobar_lyrics_check is None, "非 Windows 不该建这个控件"
    assert dialog.foobar_title_format_edit is None
    dialog._save()
    dialog.reject()
    app.processEvents()


def test_save_source_settings_writes_both_switches_and_format(tmp_path):
    """保存：两个来源开关与格式串一起写回，格式串去掉首尾空白。"""
    from pet.config import Config

    _qapp()
    cfg = Config(tmp_path)
    host = type(
        "H",
        (),
        {
            "config": cfg,
            "music_browser_media_check": ToggleSwitch(),
            "foobar_lyrics_check": ToggleSwitch(),
            "foobar_title_format_edit": _line_edit("  %artist% - %title%  "),
        },
    )()
    host.music_browser_media_check.setChecked(True)
    host.foobar_lyrics_check.setChecked(True)

    settings_music.save_source_settings(host)

    assert cfg.get("music_browser_media_enabled") is True
    assert cfg.get("foobar_lyrics_enabled") is True
    assert cfg.get("foobar_title_format") == "%artist% - %title%"


def test_save_source_settings_skips_missing_widgets(tmp_path):
    """控件不存在（非 Windows）时**整键跳过**：不许把它写成 False 覆盖用户配置。"""
    from pet.config import Config

    _qapp()
    cfg = Config(tmp_path)
    cfg.set("foobar_lyrics_enabled", True)
    host = type(
        "H",
        (),
        {
            "config": cfg,
            "music_browser_media_check": None,
            "foobar_lyrics_check": None,
            "foobar_title_format_edit": None,
        },
    )()

    settings_music.save_source_settings(host)

    assert cfg.get("foobar_lyrics_enabled") is True, "缺控件不该把开关写成 False"


def test_foobar_controls_are_editable_on_the_child_page(monkeypatch, tmp_path):
    """子宠页：foobar 两键可编辑，保存落**主配置**（歌词源解析读的就是它）。

    这两个键不在 ``PER_SLOT_SETTING_KEYS`` 里（子宠页曾经置灰：写了 slot 文件、
    运行期读主配置 = 死开关）。独立设置进程现在按 key 分流到主配置，故恢复可用。
    """
    import json

    from pet.music import settings as settings_music
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_music.sys, "platform", "win32")   # 两键仅在 Windows 建
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        assert master.foobar_lyrics_check is not None, "用例前置：Windows 下应建出控件"
        for control in (master.foobar_lyrics_check, master.foobar_title_format_edit,
                        child.foobar_lyrics_check, child.foobar_title_format_edit):
            assert control is not None
            assert control.isEnabled() is True
            assert control.toolTip() != "仅主桌宠可设置"

        child.foobar_lyrics_check.setChecked(True)
        child.foobar_title_format_edit.setText("%artist% - %title%")
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["foobar_lyrics_enabled"] is True, "子宠页改的键必须真正生效"
        assert on_disk["foobar_title_format"] == "%artist% - %title%"
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


def test_browser_media_control_is_editable_on_the_child_page(monkeypatch, tmp_path):
    """子宠页：「浏览器媒体会话参与歌词」与 foobar 同组同规——可编辑 + 落主配置。"""
    import json

    from pet.music import settings as settings_music
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_music.sys, "platform", "win32")   # 控件仅在 Windows 建
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        assert master.music_browser_media_check is not None, "用例前置：Windows 下应建出控件"
        assert master.music_browser_media_check.isEnabled() is True
        assert child.music_browser_media_check.isEnabled() is True
        assert child.music_browser_media_check.toolTip() != "仅主桌宠可设置"

        child.music_browser_media_check.setChecked(True)
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["music_browser_media_enabled"] is True
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


def test_music_player_pickers_are_editable_on_the_child_page(tmp_path):
    """子宠页：四条播放器路径与「自动检测」都可编辑，保存落主配置（右键菜单读它）。"""
    import json

    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        for picker in _pickers(master) + _pickers(child):
            assert picker.isEnabled() is True
            assert picker.toolTip() != "仅主桌宠可设置"
        assert master.music_player_detect_btn.isEnabled() is True
        assert child.music_player_detect_btn.isEnabled() is True

        saved = {
            "netease": r"D:\CloudMusic\cloudmusic.exe",
            "kugou": r"D:\Kugou\kugou.exe",
            "soda": r"D:\SodaMusic\soda.exe",
        }
        for key, path in saved.items():
            _picker(child, key).setText(path)
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["music_player_paths"] == saved
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()
