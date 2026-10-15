# -*- coding: utf-8 -*-
"""「灵动岛指标 · 游戏帧率」的设置页与配置契约（游戏帧率段）。

覆盖两头（都在**公开 seam** 上断言）：

- **配置**：``island_metrics`` 的 ``fps`` 成员准入、``game_fps_target_mode`` 枚举归一、
  ``game_fps_process`` 的「纯 exe 名」归一（路径/引号剥掉、**名字里的空格留着**
  （``My Game.exe`` 是真名字）、明显参数串剥掉、只留镜像名合法字符、限长；
  **永不当命令行用**）、reload 往返、set() 路径归一、出厂默认与重置；
- **设置页**：行存在与文案口径（Present 帧率 / 不是桌宠动画帧率 / 仅 Windows 且可能
  需要授权 / 不承诺零开销）、目标模式下程序名输入的启用态、依赖显隐链（岛 × 信息槽 ×
  帧率开关）、保存写回（既有指标序保留 + fps 追尾）、搜索（FPS / fps / 帧率 / 帧数）、
  默认值标记与单项重置、子宠页可编辑且落主配置、窄窗与放大字体下的布局。

不碰私有调用顺序：走真实 ``ModernSettingsDialog``（真实配置、真实控件、真实搜索框）。
"""
from __future__ import annotations

import json
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

import pet.modern_settings_dialog as settings_mod
from pet import config as config_mod
from pet import settings_network
from pet.config import (
    GAME_FPS_DEFAULT_TARGET_MODE,
    GAME_FPS_PROCESS_MAX_CHARS,
    GAME_FPS_TARGET_MODES,
    Config,
)
from pet.island_segments import BUILTIN_SEGMENT_IDS, CPU, FPS, GPU, MEM, NETWORK
from pet.modern_settings_dialog import SettingRow

app = QApplication.instance() or QApplication([])

_METRIC_ID = FPS
_ROW_KEYS = ("island_fps", "game_fps_target_mode", "game_fps_process")


def _dialog(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    cfg = Config(base=tmp_path)
    for key, value in overrides.items():
        cfg.set(key, value)
    # #271：空身份会话按 slot0 分流，全局键的读源是路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘才进得了对话框（缺省形态不必写盘）。
    if overrides:
        assert cfg.save() is True
    return settings_mod.ModernSettingsDialog(cfg, include_ai=True), cfg


def _row(dialog, key) -> SettingRow:
    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def _current_page(dialog):
    return dialog.pages.widget(dialog.sidebar.currentRow())


def _matches(dialog, query) -> list:
    """真实搜索框输入（textChanged），返回全部候选行。"""
    dialog.search_edit.setText("")
    app.processEvents()
    dialog.search_edit.setText(query)
    app.processEvents()
    return list(dialog._search_matches)


def _keys(rows) -> list:
    return [row.objectName() for row in rows]


# ---------------------------------------------------------------- 配置：准入与默认值
def test_fps_is_a_builtin_segment_id():
    """``fps`` 是内置保留字：第三方段不得占用，配置准入才认它。"""
    assert _METRIC_ID == "fps"
    assert _METRIC_ID in BUILTIN_SEGMENT_IDS


def test_fps_metric_is_accepted_and_survives_a_restart(tmp_path):
    """``island_metrics`` 收 fps：落盘 → 重启加载后仍在列表里（不是被当脏值丢掉）。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [CPU, _METRIC_ID, GPU])
    assert cfg.get("island_metrics") == [CPU, _METRIC_ID, GPU]
    assert cfg.save() is True
    assert Config(base=tmp_path).get("island_metrics") == [CPU, _METRIC_ID, GPU]


def test_default_is_still_the_empty_metric_list(tmp_path):
    """默认列表不变：升级用户不会因为多了这一段而突然在岛上看到它。"""
    cfg = Config(base=tmp_path)
    assert cfg.get("island_metrics") == []
    assert cfg.get("game_fps_target_mode") == "foreground"
    assert cfg.get("game_fps_process") == ""


def test_existing_metric_order_is_not_reshuffled(tmp_path):
    """加 fps 不许重排老指标：既有四员的相对次序逐位保留。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [MEM, NETWORK, CPU, GPU, _METRIC_ID])
    assert cfg.get("island_metrics") == [MEM, NETWORK, CPU, GPU, _METRIC_ID]


@pytest.mark.parametrize("raw,expected", [
    ("foreground", "foreground"),
    ("process", "process"),
    # 脏值一律回落默认（枚举键"先 str() 再判成员"：list/dict 直接 in 会抛 TypeError）
    ("", "foreground"),
    ("FOREGROUND", "foreground"),
    ("Process", "foreground"),
    ("process ", "process"),
    (None, "foreground"),
    (["process"], "foreground"),
    ({"mode": "process"}, "foreground"),
    (7, "foreground"),
])
def test_target_mode_normalization(tmp_path, raw, expected):
    cfg = Config(base=tmp_path)
    cfg.set("game_fps_target_mode", raw)
    assert cfg.get("game_fps_target_mode") == expected


def test_target_mode_is_closed_over_the_enum():
    assert GAME_FPS_TARGET_MODES == ("foreground", "process")
    assert GAME_FPS_DEFAULT_TARGET_MODE == "foreground"


@pytest.mark.parametrize("raw,expected", [
    ("game.exe", "game.exe"),
    ("  game.exe  ", "game.exe"),
    # 路径只取文件名（用户粘整条路径是常态，不是错误）
    (r"C:\Games\My Game\game.exe", "game.exe"),
    ("/usr/local/bin/game.exe", "game.exe"),
    # **名字里的空格是真名字**（Windows 上 "My Game.exe" 合法且常见）：这个值只被拿去
    # 比对进程镜像名，argv 是 list 传、shell=False，没有任何地方按空白切它——分空白
    # 会把合法 exe 名截成 "My"（曾经的缺陷）。首尾空白仍然剥掉。
    ("My Game.exe", "My Game.exe"),
    ("  My Game.exe  ", "My Game.exe"),
    ("我的 游戏.exe", "我的 游戏.exe"),
    ('"My Game.exe"', "My Game.exe"),
    (r'"C:\Games\My Game\game.exe"', "game.exe"),
    # 连续空格收敛成一个（归一化幂等的前提）
    ("My   Game.exe", "My Game.exe"),
    # 明显参数串：第一个以 "-" 开头的空白段起（含它）整段丢掉
    ("game.exe --fullscreen", "game.exe"),
    ("game.exe -windowed", "game.exe"),
    ("My Game.exe -windowed", "My Game.exe"),
    ("game.exe --fullscreen extra", "game.exe"),
    ('"game.exe"', "game.exe"),
    ('"C:\\Games\\game.exe"', "game.exe"),
    # 整个输入就是个参数串（不是程序名）→ 空（= 没有指定），不留下 "--fullscreen" 这种值
    ("--fullscreen", ""),
    ("-windowed My Game.exe", ""),
    # 命令式写法从第一个非法字符处截断：值不可能是命令、不可能是路径、不带参数
    ("game.exe; calc.exe", "game.exe"),
    ("game.exe && calc.exe", "game.exe"),
    ("game.exe|whoami", "game.exe"),
    ("$(whoami).exe", ""),
    # 「名字 + 换行 + 另一个名字」在换行处截断（控制字符不是镜像名合法字符）
    ("game.exe\nevil.exe", "game.exe"),
    # 非 ASCII 程序名照收（判据是 isalnum，不是 ASCII 表）
    ("游戏.exe", "游戏.exe"),
    # 非 str / 空 / 全是非法字符 → ""（= 没有指定，不是"随便挑一个"）
    (None, ""),
    (123, ""),
    (["game.exe"], ""),
    ("", ""),
    ("   ", ""),
    ("@@@", ""),
    (r"C:\Games\\", ""),
    # 结尾那个 / 让"最后一段路径"为空 → 不猜、存空（输入不是干净的名字）
    ("game.exe; rm -rf /", ""),
])
def test_process_name_is_normalized_to_a_bare_executable_name(tmp_path, raw, expected):
    cfg = Config(base=tmp_path)
    cfg.set("game_fps_process", raw)
    assert cfg.get("game_fps_process") == expected


def test_a_real_exe_name_with_spaces_is_not_cut_at_the_first_space(tmp_path):
    """回归：合法 exe 名里的空格曾被 ``split()`` 当参数分隔符——``My Game.exe`` 存成 ``My``。

    Windows 的进程镜像名允许带空格，而这个值只被拿去比对进程镜像名（argv 是 list
    传、``shell=False``），按空白切分没有任何技术依据，只有破坏。**参数串仍然要剥**
    （见上面参数表），两者由"第一个以 ``-`` 开头的空白段"区分。
    """
    cfg = Config(base=tmp_path)
    cfg.set("game_fps_process", "My Game.exe")
    assert cfg.get("game_fps_process") == "My Game.exe"
    # 走一次真实加载路径（reload + 归一化）也不许被切：存进去什么，跑起来就是什么
    cfg._normalize_pet_settings()
    assert cfg.get("game_fps_process") == "My Game.exe"


def test_normalized_process_name_never_looks_like_a_command(tmp_path):
    """归一化是**安全契约**的机器化落点：输出只含镜像名合法字符（判据与实现同源）。

    空格是**唯一**允许的空白（``My Game.exe`` 是真名字），所以把它摘掉之后，输出
    仍须逐字通过"不含任何空白/路径分隔符/命令元字符"这条判据，且首尾与连续空格
    都不许留（名字内部才是它唯一合法的位置）。
    """
    forbidden = re.compile(r"[\s\\/:*?\"<>|;&$`^%\x00-\x1f]")
    dirty = [
        r"C:\Games\game.exe --flag", "game.exe; rm -rf /", "`whoami`.exe",
        "game.exe|calc.exe", "a&&b.exe", '"game.exe"', "game.exe\t-x",
        "game.exe%TEMP%", "game.exe>out.txt", "game.exe:stream",
        "My Game.exe -windowed", "game.exe --flag --other", "-x.exe",
        "  My  Game.exe  ",
    ]
    cfg = Config(base=tmp_path)
    for raw in dirty:
        cfg.set("game_fps_process", raw)
        cleaned = cfg.get("game_fps_process")
        assert not forbidden.search(cleaned.replace(" ", "")), (raw, cleaned)
        assert cleaned == cleaned.strip(), (raw, cleaned)
        assert "  " not in cleaned, (raw, cleaned)
        assert all(config_mod._game_fps_name_char(char) for char in cleaned), (raw, cleaned)


def test_process_name_normalization_is_idempotent(tmp_path):
    """幂等：存进去的值再走一次加载路径仍是同一个值（每次加载都会跑归一化）。"""
    cfg = Config(base=tmp_path)
    for raw in ["game.exe", r"C:\Games\My Game\game.exe -windowed", "游戏.exe", ""]:
        cfg.set("game_fps_process", raw)
        first = cfg.get("game_fps_process")
        for _ in range(3):
            cfg._normalize_pet_settings()
            assert cfg.get("game_fps_process") == first


def test_process_name_is_bounded(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("game_fps_process", "a" * 400 + ".exe")
    cleaned = cfg.get("game_fps_process")
    assert len(cleaned) == GAME_FPS_PROCESS_MAX_CHARS
    assert GAME_FPS_PROCESS_MAX_CHARS > 0


def test_dirty_values_survive_the_real_load_path(tmp_path):
    """手改 config.json 塞脏值：加载路径（reload + 归一化）不抛、值收敛。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps({
        "version": 4,
        "island_metrics": ["fps", "vram", "fps"],
        "game_fps_target_mode": ["process"],
        "game_fps_process": r"D:\Games\My Game\game.exe -windowed",
    }, ensure_ascii=False), encoding="utf-8")
    cfg = Config(base=tmp_path)
    assert cfg.get("island_metrics") == ["fps"]
    assert cfg.get("game_fps_target_mode") == "foreground"
    assert cfg.get("game_fps_process") == "game.exe"


def test_reset_to_defaults_clears_both_keys(tmp_path):
    """出厂重置（全局）必须把这两键带回默认，且 fps 从指标列表里消失。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [CPU, _METRIC_ID])
    cfg.set("game_fps_target_mode", "process")
    cfg.set("game_fps_process", "game.exe")
    assert cfg.reset_to_defaults() is True
    assert cfg.get("island_metrics") == []
    assert cfg.get("game_fps_target_mode") == "foreground"
    assert cfg.get("game_fps_process") == ""


# ---------------------------------------------------------------- 设置页：行与文案
def test_row_exists_in_the_island_domain_with_accurate_copy(tmp_path, monkeypatch):
    """行与 CPU/内存/GPU 同区域（「桌面组件」域），文案说清口径与代价。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog, "island_fps")
        label = row.label.text()
        assert "灵动岛指标" in label and "帧率" in label, "与其他指标行同前缀、同措辞"
        hint = row.hint_label.text()
        assert "Present FPS" in hint, "口径必须点明是 Present 帧率（用户就按这个词找）"
        assert "不是桌宠动画的帧率" in hint, "必须排除「桌宠动画帧率」的误解"
        assert "显卡占用" in hint, "必须排除「显卡占用推算」的误解"
        assert "Windows" in hint and "其他系统" in hint, "能力边界如实说（不是三端都有）"
        assert "权限" in hint and "可能" in hint, "可能要授权——不许承诺权限已就绪"
        assert "已授权" not in hint and "无需授权" not in hint, "不许把权限说成已经拿到"
        assert "开销" in hint, "开销必须如实提（不许承诺零开销）"
        assert "零开销" not in hint
        assert "--" in hint and "0" in hint, "首拍是 --，不是 0"
        assert "默认关闭" in hint or "开启后" in hint
        # 归属：与其他岛指标行同一个域
        names = {r.objectName() for r in dialog._search_rows}
        assert "settingRow_island_fps" in names
    finally:
        dialog.close()


def test_follow_rows_describe_the_two_modes_without_promising_execution(tmp_path, monkeypatch):
    """两行跟随设置：模式选项文案、程序名输入「只匹配、不执行、不猜目标」。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        select = dialog.game_fps_target_mode_select
        assert select.count() == 2
        # 下拉的取值**必须与 config 的枚举逐位一致**（两处字面量最容易漂移：多一个字面量
        # 就会出现"设置页存得进、消费方认不出"的死值）
        assert [select.itemData(i) for i in range(2)] == list(GAME_FPS_TARGET_MODES)
        assert [select.itemData(i) for i in range(2)] == \
            [value for value, _text in settings_network._FPS_TARGET_MODES]
        assert [select.itemText(i) for i in range(2)] == ["跟随前台程序", "指定程序"]
        assert GAME_FPS_TARGET_MODES[0] == GAME_FPS_DEFAULT_TARGET_MODE, "默认项排第一"

        hint = _row(dialog, "game_fps_process").hint_label.text()
        assert "game.exe" in hint, "给一个能照填的例子"
        assert "不会启动" in hint or "不会执行" in hint, "必须写明这个值不被执行"
        assert "匹配" in hint, "说明真实用途 = 匹配进程名"
        assert "指定程序" in hint, "说明启用条件"
    finally:
        dialog.close()


def test_only_the_process_mode_enables_the_name_input(tmp_path, monkeypatch):
    """指定 exe 的输入只在「指定程序」模式下启用（默认跟随前台 → 置灰并说明）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        edit = dialog.game_fps_process_edit
        select = dialog.game_fps_target_mode_select
        select.setCurrentData("foreground")
        app.processEvents()
        assert edit.isEnabled() is False
        assert edit.toolTip(), "置灰必须给出原因，而不是让用户猜"

        select.setCurrentData("process")
        app.processEvents()
        assert edit.isEnabled() is True

        select.setCurrentData("foreground")
        app.processEvents()
        assert edit.isEnabled() is False
        # 隐藏值不丢：置灰是"不生效"，不是"清空"
        edit.setText("game.exe")
        app.processEvents()
        assert edit.text() == "game.exe"
    finally:
        dialog.close()


def test_mode_select_width_follows_the_font(tmp_path, monkeypatch):
    """放大字体后下拉仍装得下最长选项（真实 windows 平台插件下实测：基类固定 132px
    在 1.5 倍字体时会被裁成 154px 才够，自绘文本区没有省略号，裁了就是缺字）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        select = dialog.game_fps_target_mode_select
        assert select.width() >= select.suggestedWidth()
        for scale in (1.5, 2.0):
            font = select.font()
            font.setPointSizeF(max(1.0, font.pointSizeF()) * scale)
            select.setFont(font)
            edit = dialog.game_fps_process_edit
            edit.setFont(font)
            app.processEvents()
            assert select.sizeHint().width() >= select.suggestedWidth(), scale
            assert edit.minimumWidth() >= edit.fontMetrics().horizontalAdvance("game.exe") + 16
    finally:
        dialog.close()


def test_fps_rows_follow_the_island_dependency_chain(tmp_path, monkeypatch):
    """显隐链：岛开关 × 信息槽开关管指标行；帧率开关再管它下面两行。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_enabled_check.setChecked(True)
        dialog.island_info_check.setChecked(True)
        assert not _row(dialog, "island_fps").isHidden()
        assert _row(dialog, "game_fps_target_mode").isHidden(), "帧率没开：两行跟随设置不显示"
        assert _row(dialog, "game_fps_process").isHidden()

        dialog.island_fps_check.setChecked(True)
        assert not _row(dialog, "game_fps_target_mode").isHidden()
        assert not _row(dialog, "game_fps_process").isHidden()

        dialog.island_fps_check.setChecked(False)
        assert _row(dialog, "game_fps_target_mode").isHidden()
        assert _row(dialog, "game_fps_process").isHidden()

        dialog.island_fps_check.setChecked(True)
        dialog.island_info_check.setChecked(False)
        assert _row(dialog, "island_fps").isHidden()
        assert _row(dialog, "game_fps_target_mode").isHidden()

        dialog.island_info_check.setChecked(True)
        dialog.island_enabled_check.setChecked(False)
        assert _row(dialog, "island_fps").isHidden()
        assert _row(dialog, "game_fps_process").isHidden()
    finally:
        dialog.close()


# ---------------------------------------------------------------- 设置页：保存
def test_save_writes_all_three_settings_in_one_pass(tmp_path, monkeypatch):
    dialog, cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_fps_check.setChecked(True)
        dialog.game_fps_target_mode_select.setCurrentData("process")
        dialog.game_fps_process_edit.setText(r"C:\Games\My Game\game.exe")
        assert dialog._write_config() is True
    finally:
        dialog.close()

    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [_METRIC_ID]
    assert cfg.get("game_fps_target_mode") == "process"
    assert cfg.get("game_fps_process") == "game.exe", "保存路径也要过同一份归一化"

    again, _cfg2 = _dialog(tmp_path, monkeypatch)
    try:
        assert again.island_fps_check.isChecked() is True
        assert again.game_fps_target_mode_select.currentData() == "process"
        assert again.game_fps_process_edit.text() == "game.exe"
        assert again.game_fps_process_edit.isEnabled() is True
    finally:
        again.close()


def test_save_appends_fps_after_the_existing_metrics(tmp_path, monkeypatch):
    """既有顺序保留、新勾的 fps 追尾——老指标一个都不许被重排。"""
    kept, _cfg = _dialog(tmp_path, monkeypatch, island_metrics=[MEM, CPU, NETWORK])
    try:
        assert kept.island_fps_check.isChecked() is False
        kept._write_config()
    finally:
        kept.close()
    _cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert _cfg.get("island_metrics") == [MEM, CPU, NETWORK], "没碰过这几行的保存不得改顺序"

    added, cfg_added = _dialog(tmp_path / "added", monkeypatch,
                               island_metrics=[MEM, NETWORK])
    try:
        added.island_fps_check.setChecked(True)
        added._write_config()
    finally:
        added.close()
    cfg_added.reload()
    assert cfg_added.get("island_metrics") == [MEM, NETWORK, _METRIC_ID]

    full, cfg_full = _dialog(tmp_path / "full", monkeypatch)
    try:
        for toggle in (full.island_network_check, full.island_cpu_check,
                       full.island_mem_check, full.island_gpu_check,
                       full.island_fps_check):
            toggle.setChecked(True)
        full._write_config()
    finally:
        full.close()
    cfg_full.reload()
    assert cfg_full.get("island_metrics") == [NETWORK, CPU, MEM, GPU, _METRIC_ID]


def test_dropping_fps_removes_only_that_member(tmp_path, monkeypatch):
    dialog, cfg = _dialog(tmp_path, monkeypatch,
                          island_metrics=[CPU, _METRIC_ID, MEM])
    try:
        dialog.island_fps_check.setChecked(False)
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [CPU, MEM]


# ---------------------------------------------------------------- 设置页：搜索与标记
@pytest.mark.parametrize("query", ["FPS", "fps", "帧率", "帧数", "游戏帧率"])
def test_row_is_findable_by_fps_and_frame_rate_words(tmp_path, monkeypatch, query):
    """「FPS / fps / 帧率 / 帧数」都必须能找到这一行（帧数是别名，文案里没有这个词）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        keys = _keys(_matches(dialog, query))
        assert "settingRow_island_fps" in keys, (query, keys)

        # 真走到那一行（回车遍历），且落在「桌面组件」域
        for presses in range(len(keys)):
            dialog.search_edit.setText("")
            app.processEvents()
            dialog.search_edit.setText(query)
            app.processEvents()
            for _ in range(presses):
                QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
                app.processEvents()
            row = dialog._search_matches[dialog._search_index]
            if row.objectName() == "settingRow_island_fps":
                assert dialog.sidebar.currentItem().text() == "桌面组件"
                assert dialog.search_status.text().startswith(f"{presses + 1}/")
                break
        else:
            raise AssertionError(f"「{query}」的回车遍历走不到 settingRow_island_fps")
    finally:
        dialog.close()


def test_alias_table_carries_the_spoken_word_frame_count():
    """别名表里有「帧数」→ island_fps（文案里没有这个词，只能靠别名兜）。"""
    from pet.settings_search import ROW_ALIASES

    assert ROW_ALIASES["帧数"] == ("island_fps",)


def test_mode_and_name_rows_show_default_marks_and_reset(tmp_path, monkeypatch):
    """默认值标记与单项重置对这两行同样成立（默认值来源仍是 Config 出厂默认）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        mode_row = _row(dialog, "game_fps_target_mode")
        name_row = _row(dialog, "game_fps_process")
        assert mode_row.modified_mark.isHidden() and name_row.modified_mark.isHidden()

        dialog.game_fps_target_mode_select.setCurrentData("process")
        app.processEvents()
        assert mode_row.modified_mark.isHidden() is False
        assert "跟随前台程序" in mode_row.hint_label.text(), "标出默认值文案"

        dialog.game_fps_process_edit.setText("game.exe")
        app.processEvents()
        assert name_row.modified_mark.isHidden() is False
        assert "（空）" in name_row.hint_label.text(), "默认值是空"

        # 程序名先重置：切回「跟随前台程序」后这一行会置灰，置灰行的重置入口按既有
        # 口径收起（不给置灰项提供重置），所以顺序必须先名字后模式。
        name_row.default_reset_button.click()
        app.processEvents()
        assert dialog.game_fps_process_edit.text() == ""
        mode_row.default_reset_button.click()
        app.processEvents()
        assert dialog.game_fps_target_mode_select.currentData() == "foreground"
        assert mode_row.modified_mark.isHidden() and name_row.modified_mark.isHidden()
    finally:
        dialog.close()


# ---------------------------------------------------------------- 子宠页作用域
def test_rows_are_editable_on_the_child_page_and_write_the_main_config(tmp_path, monkeypatch):
    """进程级键：子宠页可编辑，保存落**主配置**（岛读的就是它）。"""
    master, _cfg = _dialog(tmp_path, monkeypatch)
    child = settings_mod.ModernSettingsDialog(
        Config(base=tmp_path, instance_id="slot-1"), include_ai=True)
    try:
        assert master.island_fps_check.isEnabled() is True
        assert child.island_fps_check.isEnabled() is True
        assert child.game_fps_target_mode_select.isEnabled() is True

        child.island_fps_check.setChecked(True)
        child.game_fps_target_mode_select.setCurrentData("process")
        child.game_fps_process_edit.setText("game.exe")
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，
        # 本用例改的键是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["island_metrics"] == [_METRIC_ID]
        assert on_disk["game_fps_target_mode"] == "process"
        assert on_disk["game_fps_process"] == "game.exe"
    finally:
        for dlg in (master, child):
            dlg.close()
            dlg.deleteLater()
        app.processEvents()


def test_both_keys_are_global_not_per_pet():
    """归属护栏：岛与采集链路读**主配置** ⇒ 两键不许进每宠 / 每槽集合。

    进了每宠集合就会变成"保存进 slot 文件、运行期读主配置"的死开关（2026-10-09 那批
    修的就是这类问题），所以这里把归属钉成机器可检的事实，而不是注释里的承诺。
    """
    from pet import overlay_spawn_state, pet_profiles

    for key in ("game_fps_target_mode", "game_fps_process"):
        assert key not in pet_profiles.PER_PET_SETTING_KEYS
        assert key not in overlay_spawn_state.PER_SLOT_SETTING_KEYS


def test_fps_row_still_exists_on_a_non_windows_platform(tmp_path, monkeypatch):
    """非 Windows：控制不撤（暂不可用 ≠ 永久不支持）、文案仍写明能力边界、保存同语义。

    采集链路的平台边界是 Windows，但**设置 ID、默认值、依赖与保存语义三端共享**
    （GATES §4）：撤掉控件会让用户既看不到原因也配不了，而"这段在别的系统上就是
    不占位 + 卡片写原因"是岛侧既有的能力口径（同 GPU）。
    """
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.sys, "platform", "linux")
    cfg = Config(base=tmp_path)
    dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=True)
    try:
        row = _row(dialog, "island_fps")
        assert "Windows" in row.hint_label.text(), "边界必须如实写在文案里"
        dialog.island_fps_check.setChecked(True)
        dialog.game_fps_target_mode_select.setCurrentData("process")
        dialog.game_fps_process_edit.setText("game.exe")
        assert dialog._write_config() is True
        cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
        assert cfg.get("island_metrics") == [_METRIC_ID]
        assert cfg.get("game_fps_target_mode") == "process"
        assert cfg.get("game_fps_process") == "game.exe"
    finally:
        dialog.close()
        app.processEvents()


# ---------------------------------------------------------------- 布局与字体度量
def test_rows_fit_the_compact_window_and_scale_with_the_font(tmp_path, monkeypatch):
    """最小窗口不越界；控件宽度按字体度量给足（长 exe 名在放大字体下不裁字）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_enabled_check.setChecked(True)
        dialog.island_info_check.setChecked(True)
        dialog.island_fps_check.setChecked(True)
        dialog.resize(dialog.minimumSize())
        dialog.show()
        index = next(i for i in range(dialog.sidebar.count())
                     if dialog.sidebar.item(i).text() == "桌面组件")
        dialog.sidebar.setCurrentRow(index)
        app.processEvents()

        edit = dialog.game_fps_process_edit
        sample = settings_network._FPS_PROCESS_SAMPLE
        assert edit.maxLength() == GAME_FPS_PROCESS_MAX_CHARS
        assert edit.minimumWidth() >= edit.fontMetrics().horizontalAdvance(sample) + 16
        assert dialog.game_fps_target_mode_select.width() >= \
            dialog.game_fps_target_mode_select.suggestedWidth()

        for key in _ROW_KEYS:
            row = _row(dialog, key)
            scroll = row.parentWidget()
            while scroll is not None and not isinstance(scroll, QScrollArea):
                scroll = scroll.parentWidget()
            assert scroll is not None
            left = row.mapTo(scroll.viewport(), row.rect().topLeft()).x()
            assert left >= 0, f"{key} 越出内容区左侧"
            assert left + row.width() <= scroll.viewport().width(), f"{key} 越出内容区右侧"

        # 放大字体：行按字体度量加高/换行，不得把控件挤出内容区
        for scale in (1.4, 1.8):
            font = dialog.font()
            font.setPointSizeF(max(1.0, font.pointSizeF()) * scale)
            dialog.setFont(font)
            app.processEvents()
            for key in _ROW_KEYS:
                row = _row(dialog, key)
                scroll = row.parentWidget()
                while scroll is not None and not isinstance(scroll, QScrollArea):
                    scroll = scroll.parentWidget()
                left = row.mapTo(scroll.viewport(), row.rect().topLeft()).x()
                assert left >= 0, (scale, key)
                assert left + row.width() <= scroll.viewport().width() + 1, (scale, key)
                assert row.control.height() >= row.control.fontMetrics().height(), (scale, key)
    finally:
        dialog.close()
