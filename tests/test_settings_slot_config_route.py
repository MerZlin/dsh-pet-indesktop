# -*- coding: utf-8 -*-
"""子宠设置页的配置读写路由（架构级死开关修复）。

问题：overlay 拓扑下右键子宠 → 桌宠设置拉起的是**独立设置进程**
（``--instance slot-N`` → ``Config(instance_id="slot-N")`` → 读写
``config-slot-N.json``），而运行期只有 ``PER_SLOT_SETTING_KEYS`` 里的键逐宠取源
（``overlay_shell._SpriteConfig.get``），其余键一律读主配置。于是子宠页上「语音
报时 / 节日 / 待办 / 灵动岛 / 菜单外观 / 窗口级 7 键 …」保存成功、回显正确、
运行期零效果——窗口级 7 行还写着「（对所有桌宠生效）」，实际对所有桌宠都不生效。

本文件钉住修法的四件事（走真实 ``ModernSettingsDialog`` 这个公开 seam，不碰私有
调用顺序）：

1. 保存路由：非 per-slot 键落进**主** ``config.json``，per-slot 键落 slot 文件；
2. 初值路由：子宠页非 per-slot 行显示主配置的真实值（不再是 slot 文件里的无效快照）；
3. 恢复默认：子宠页只重置 slot 文件，主配置一个键都不碰；
4. 并发纪律：主配置在设置窗打开后被外部改写 → 保存不盖回该键（reload-merge）。

分流只在 overlay 拓扑下的 slot 身份生效：legacy 单窗（含捕获模式）里那只桌宠读的
就是自己那份 ``config-slot-N.json`` 的**全部**键，分流反而会让它的设置失效。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet import overlay_spawn_state as spawn_state
from pet import settings_slot_config
from pet.catalog import DEFAULT_SCALE
from pet.config import DEFAULT_SELF_TALK_TEXTS, Config


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _dialog(config, *, include_ai: bool = False, standalone: bool = False):
    from pet.modern_settings_dialog import ModernSettingsDialog

    return ModernSettingsDialog(config, include_ai=include_ai,
                                standalone=standalone)


def _read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _slot_path(config_dir, slot: int = 1) -> Path:
    return spawn_state.slot_config_path(config_dir, slot)


# ------------------------------------------------------------ 1. 保存路由
def test_child_save_routes_global_keys_to_main_config(tmp_path, app):
    """子宠页保存：非 per-slot 键落**主** config.json（slot 文件里不许出现新值）。

    这些键运行期只有主配置一个读源（``_SpriteConfig`` 对它们转发主配置），写进
    slot 文件就是"保存成功、回显正确、运行时不生效"的死开关。

    对话框按**独立设置进程**的真实形态构造（``parent=None`` + ``standalone=True``，
    即 ``pet/__main__._run_settings`` 那次构造），路由就在这条路径上。
    """
    main = Config(base=tmp_path)
    main.set("scale", 0.5)
    main.set("pet_opacity", 55)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg, standalone=True)
    try:
        assert dialog.pet_opacity_spin.value() == 55
        dialog.pet_opacity_spin.setValue(35)
        dialog.edge_probe_check.setChecked(True)
        dialog.voice_chime_page.enabled_check.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    on_disk = _read_json(main.path)
    assert on_disk["pet_opacity"] == 35, "窗口级键必须落主配置"
    assert on_disk["edge_probe_enabled"] is True, "拖拽探头必须落主配置"
    assert on_disk["voice_chime_enabled"] is True, "语音报时必须落主配置"
    assert "scale" not in on_disk
    assert _read_json(_slot_path(main.dir, 0))["scale"] == 0.5

    slot_on_disk = _read_json(_slot_path(main.dir))
    assert slot_on_disk.get("pet_opacity") != 35, "非 per-slot 键一个字节都不该进 slot 文件"
    assert slot_on_disk.get("edge_probe_enabled") is not True
    assert slot_on_disk.get("voice_chime_enabled") is not True


def test_child_save_keeps_per_slot_keys_in_the_slot_file(tmp_path, app):
    """子宠页保存：per-slot 键落这只宠自己的 slot 文件，主配置不变。"""
    main = Config(base=tmp_path)
    main.set("scale", 0.5)
    main.set("self_talk_enabled", False)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.self_talk_check.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    slot_on_disk = _read_json(_slot_path(main.dir))
    assert slot_on_disk["scale"] == pytest.approx(1.0)
    assert slot_on_disk["self_talk_enabled"] is True
    assert slot_on_disk["user_customized"] is True, "用户在自己的设置页保存过 ⇒ 置位落种旗标"

    on_disk = _read_json(main.path)
    assert "scale" not in on_disk
    assert "self_talk_enabled" not in on_disk
    assert _read_json(_slot_path(main.dir, 0))["self_talk_enabled"] is False


# ------------------------------------------------------------ 2. 初值路由
def test_child_page_initial_values_come_from_main_config(tmp_path, app):
    """非 per-slot 行的初值必须来自主配置（不再显示 slot 文件里的无效快照）。"""
    main = Config(base=tmp_path)
    main.set("pet_opacity", 80)
    main.set("scale", 0.5)
    main.set("voice_chime_enabled", True)
    assert main.save() is True
    # 先用主配置落种，再把 slot 文件改成**陈旧快照**（主配置改过、slot 文件没跟上，
    # 正是"上一次落种时的样子"）：子宠页以前读的就是这一份。
    Config(base=tmp_path, instance_id="slot-1")
    _slot_path(main.dir).write_text(json.dumps({"version": 4, "pet_opacity": 40,
        "voice_chime_enabled": False, "scale": 0.85}), encoding="utf8")
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        assert dialog.pet_opacity_spin.value() == 80, "窗口级行必须显示主配置的真值"
        assert dialog.voice_chime_page.enabled_check.isChecked() is True, (
            "语音报时行必须显示主配置的真值（slot 文件里那份是陈旧副本）"
        )
        assert float(dialog.scale_combo.currentData()) == pytest.approx(0.85), (
            "per-slot 行仍按这只宠自己的 slot 文件取源"
        )
    finally:
        dialog.close()
        dialog.deleteLater()


# ------------------------------------------------------------ 3. 恢复默认收口
def test_child_reset_touches_slot_file_only(tmp_path, app, monkeypatch):
    """子宠页「恢复默认设置」：只重置这只宠的档案，主配置一个键都不碰。

    未闸住时走到了 ``Config.reset_to_defaults`` 的整表语义，而路由后那份"整表"
    是**所有桌宠共用**的主配置——点一次就把主宠与其它子宠的设置一起抹掉。

    G1b 起落盘走 ``pet_profiles.reset_slot_profile``：**保留位置**（``rx/ry/screen_name``，
    "回到默认落位"是独立操作）、偏好键从档案里**移除**（读侧落回同源默认）、
    ``user_customized`` 是本地旗标而非偏好值，原样保留。
    """
    from pet import pet_profiles, settings_reset_defaults

    main = Config(base=tmp_path)
    main.set("pet_opacity", 66)
    main.set("voice_chime_enabled", True)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")
    assert pet_profiles.write_profile_settings(
        main.dir, 1, {"scale": 1.0, "self_talk_enabled": True},
        user_customized=True).status == "ok"
    assert pet_profiles.write_profile_geometry(
        main.dir, 1, {"rx": 0.2, "ry": 0.7, "screen_name": "primary"}).status == "ok"
    before_main = _read_json(main.path)

    monkeypatch.setattr(settings_reset_defaults, "confirm_reset", lambda _parent, _scope=None: True)
    dialog = _dialog(cfg)
    try:
        assert settings_reset_defaults.on_reset_clicked(dialog) is True
    finally:
        dialog.deleteLater()

    assert _read_json(main.path) == before_main, "子宠页恢复默认不得动主配置"
    slot_on_disk = _read_json(_slot_path(main.dir))
    assert "scale" not in slot_on_disk, "偏好键从档案移除（读侧回同源默认）"
    assert "self_talk_enabled" not in slot_on_disk
    assert slot_on_disk["user_customized"] is True, "本地旗标不是偏好值：原样保留"
    assert (slot_on_disk["rx"], slot_on_disk["ry"]) == (0.2, 0.7), "位置必须保留"
    assert slot_on_disk["screen_name"] == "primary"
    profile = pet_profiles.read_slot_profile(main.dir, 1)
    assert profile.value("scale") == pytest.approx(DEFAULT_SCALE), "读侧回同源默认"
    assert profile.value("self_talk_enabled") is pet_profiles.same_source_default(
        "self_talk_enabled")


def test_child_reset_confirm_copy_promise_equals_actual_scope(tmp_path, app, monkeypatch):
    """R08：确认框承诺的作用域 = 点下去真会变的范围（两边钉在一条用例里）。

    文案若照抄主宠页那套（"形象、系统提示词、自定义台词与角色别名、外观与功能开关
    都会恢复出厂"），而它们在子宠页上走主配置、重置时一个字节都不动——用户就是照着
    一段不成立的话在做不可撤销的决策。这条用例同时取**真确认框**与**真落盘结果**：
    文案说"只重置这只桌宠自己的那份设置（位置保留）"，盘上也确实只有这只的档案变了。
    """
    from pet import pet_profiles, settings_reset_defaults

    main = Config(base=tmp_path)
    main.set("voice_chime_enabled", True)          # 全局键：文案承诺"不受影响"
    main.set("self_talk_texts", ["全局台词"])
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")
    assert pet_profiles.write_profile_settings(
        main.dir, 1, {"self_talk_texts": ["这只的台词"]}).status == "ok"
    assert pet_profiles.write_profile_geometry(
        main.dir, 1, {"rx": 0.3, "ry": 0.6}).status == "ok"
    before_main = _read_json(main.path)

    seen: list = []

    def answer_yes(host, scope=None) -> bool:
        """替掉 exec，但确认框本身走**产品真实构造**（文案/按钮/默认钮都是真的）。

        ``scope`` 是产品这次点的那个作用域（本宠 / 全局）：确认框必须按它出文案，
        所以替身也要把它透传——只转发 host 会把"点了全局动作却看到本宠文案"这类
        错配藏起来。
        """
        box, confirm = settings_reset_defaults.build_reset_confirm_box(host, scope)
        try:
            seen.append((box.text(), box.informativeText()))
            assert box.defaultButton() is not confirm, "默认钮必须仍是取消"
        finally:
            box.deleteLater()
        return True

    monkeypatch.setattr(settings_reset_defaults, "confirm_reset", answer_yes)
    dialog = _dialog(cfg)
    try:
        assert settings_reset_defaults.on_reset_clicked(dialog) is True
    finally:
        dialog.deleteLater()

    assert len(seen) == 1, "前提：子宠页确实弹了确认框"
    copy = "\n".join(seen[0])
    assert "只重置这只桌宠自己的那份设置" in copy
    assert "语音报时、灵动岛、AI 对话等全局设置" in copy
    assert "当前所在位置不变" in copy, "位置保留也是承诺的一部分（G1b）"
    assert "将重置全部设置" not in copy, "主宠页承诺不得出现在子宠页"

    assert _read_json(main.path) == before_main, "文案承诺的「全局不受影响」必须成立"
    slot_on_disk = _read_json(_slot_path(main.dir))
    assert "self_talk_texts" not in slot_on_disk, "偏好键从档案移除"
    profile = pet_profiles.read_slot_profile(main.dir, 1)
    assert profile.value("self_talk_texts") == DEFAULT_SELF_TALK_TEXTS, "这只的台词归默认"
    assert (slot_on_disk["rx"], slot_on_disk["ry"]) == (0.3, 0.6), "位置保留"


# ------------------------------------------------------------ 4. 并发纪律
def test_routed_save_reloads_main_before_writing(tmp_path):
    """路由视图自己的落盘纪律：``reload`` → 重放 pending → ``save``。

    ``Config.save`` 是**整表**写：不先重读，设置窗打开期间主进程/托盘改过的键就
    被陈旧内存快照盖回去。这一条钉在路由视图上（不依赖调用方是否记得先 reload），
    ``_write_config`` 那条端到端路径见下一条。
    """
    main = Config(base=tmp_path)
    main.set("mem_min_size_mb", 50.0)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")
    routed = settings_slot_config.route_settings_config(cfg)
    assert routed is not cfg

    routed.set("pet_opacity", 30)
    external = Config(base=tmp_path)
    external.set("mem_min_size_mb", 123.0)
    assert external.save() is True

    assert routed.save() is True
    on_disk = _read_json(main.path)
    assert on_disk["pet_opacity"] == 30, "本次要改的键要落盘"
    assert on_disk["mem_min_size_mb"] == 123.0, "外部并发改动被陈旧内存快照盖回去了"


def test_child_save_does_not_roll_back_external_main_change(tmp_path, app):
    """设置窗开着时主配置被外部改写 → 子宠页保存（整表写主配置）不得盖回该键。"""
    main = Config(base=tmp_path)
    main.set("mem_min_size_mb", 50.0)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        external = Config(base=tmp_path)
        external.set("mem_min_size_mb", 123.0)
        assert external.save() is True

        dialog.pet_opacity_spin.setValue(30)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    on_disk = _read_json(main.path)
    assert on_disk["pet_opacity"] == 30, "本次要改的键要落盘"
    assert on_disk["mem_min_size_mb"] == 123.0, "外部并发改动被陈旧内存快照盖回去了"


# ------------------------------------------------------------ 5. 分流键的清洗口径
def test_child_page_survives_dirty_slot_values(tmp_path, app):
    """slot JSON 里的脏值必须先过 Config 清洗：设置页构造不许炸。

    分流键的读源是 slot 文件，而设置页各行按「值已过 Config 清洗」的前提消费
    （``float(playback_speed)`` / 遍历 ``self_talk_texts`` / ``int(bubble_text_scale)``，
    见 ``settings_pet_controls``）。手改或半截 JSON 里的 ``"playback_speed": "bad"``
    原样返回就是 ValueError、``"self_talk_texts": 1`` 就是 TypeError——整个设置窗
    构造失败（用户点「桌宠设置」什么也打不开）。

    口径 = **Config 会给出的值**（同一份 ``_normalize_pet_settings``），不是另一套
    兜底：脏值被清洗成该键的默认值，仍然是"slot 文件里有这个键"的那一支（不回退主配置）。
    """
    main = Config(base=tmp_path)
    assert main.save() is True
    Config(base=tmp_path, instance_id="slot-1")  # 落种出 slot 文件
    for key, dirty in (("playback_speed", "bad"),
                       ("self_talk_texts", 1),
                       ("bubble_text_scale", "xx")):
        assert spawn_state.write_slot_setting(main.dir, 1, key, dirty) is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    # 按独立设置进程的真实形态构造（``parent=None`` + ``standalone=True``）：
    # 脏值原样返回时这一行就 ValueError / TypeError（点「桌宠设置」什么也打不开）。
    dialog = _dialog(cfg, standalone=True)
    try:
        assert float(dialog.speed_select.currentData()) == pytest.approx(1.0)
        assert dialog.texts_edit.toPlainText() == "\n".join(DEFAULT_SELF_TALK_TEXTS)
        assert dialog.bubble_text_scale_spin.value() == 100
    finally:
        dialog.deleteLater()


def test_child_page_keeps_clean_slot_values(tmp_path, app):
    """非回归：slot 文件里的合法值照旧优先于主配置（清洗不得变成"回退主配置"）。"""
    main = Config(base=tmp_path)
    main.set("playback_speed", 0.5)
    main.set("bubble_text_scale", 100)
    assert main.save() is True
    Config(base=tmp_path, instance_id="slot-1")
    assert spawn_state.write_slot_setting(main.dir, 1, "playback_speed", 2.0) is True
    assert spawn_state.write_slot_setting(main.dir, 1, "bubble_text_scale", 150) is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        assert float(dialog.speed_select.currentData()) == pytest.approx(2.0)
        assert dialog.bubble_text_scale_spin.value() == 150
    finally:
        dialog.deleteLater()


# ------------------------------------------------------------ 6. 点击台词绑定保存
def _save_click_talk_binding(config, action_id: str, text: str) -> str:
    """走真实对话框保存一条绑定（`编辑…` 按钮那条路），返回实际角色 id。"""
    from pet.click_talk_dialog import ClickTalkBindingsDialog

    dialog = ClickTalkBindingsDialog(config, click_names=[action_id])
    try:
        dialog.text_edit.setPlainText(text)
        dialog._save()
        return dialog.character_id
    finally:
        dialog.deleteLater()


def test_child_click_talk_bindings_save_keeps_external_main_change(tmp_path, app):
    """子宠页保存点击台词绑定：外部并发改动不得被回滚，绑定要真进主配置。

    旧行为：``set_click_talk_bindings`` 经 ``__getattr__`` 转发到 ``Config``——那里
    直接改内存 + ``self.save()``（**整表**写）。设置进程的内存快照从启动起就陈旧，
    那次整表写会把设置窗开着期间主进程/托盘改过的键一起盖回去。
    """
    main = Config(base=tmp_path)
    main.set("mem_min_size_mb", 50.0)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        external = Config(base=tmp_path)
        external.set("mem_min_size_mb", 123.0)
        assert external.save() is True

        character_id = _save_click_talk_binding(dialog.config, "click1", "你好呀")
        assert dialog.config.click_talk_bindings(character_id) == {"click1": ["你好呀"]}
    finally:
        dialog.deleteLater()

    on_disk = _read_json(main.path)
    assert on_disk["mem_min_size_mb"] == 123.0, "外部并发改动被陈旧内存快照盖回去了"
    assert on_disk["character_profiles"][character_id]["click_talk_bindings"] == {
        "click1": ["你好呀"]}, "绑定必须写进主配置（进程级数据）"


def test_click_talk_binding_save_writes_main_config_even_without_page_save(tmp_path, app):
    """绑定对话框自己的「保存」即落盘：设置页没再点保存（或直接关窗）也不丢。

    路由把落盘收口在 ``save`` 的 reload → 重放 pending → save 上，但
    ``Config.set_click_talk_bindings`` 的既契约是"保存即落盘"；两者都要满足。
    """
    main = Config(base=tmp_path)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")
    routed = settings_slot_config.route_settings_config(cfg)

    character_id = _save_click_talk_binding(routed, "click2", "在的")

    on_disk = _read_json(main.path)
    assert on_disk["character_profiles"][character_id]["click_talk_bindings"] == {
        "click2": ["在的"]}


# ------------------------------------------------------------ 7. 置灰摘除（路由接通后真生效）
def test_harness_controls_are_editable_on_the_child_page(tmp_path, app):
    """子宠页：「随桌宠启动 dsh 服务」与其目标下拉可编辑，保存落主配置。

    两个键是进程级的（拉起的是这台机器上的 dsh 服务），子宠页曾经按
    「仅主桌宠可设置」置灰——理由与其余 6 组同源（写了 slot 文件、运行期读主配置）。
    路由接通后写主配置、真生效，故摘掉置灰。
    """
    main = Config(base=tmp_path)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")

    dialog = _dialog(cfg)
    try:
        assert dialog.harness_autostart_check.isEnabled() is True
        assert dialog.harness_launch_target_select.isEnabled() is True
        assert dialog.harness_autostart_check.toolTip() != "仅主桌宠可设置"
        from pet.settings_widgets import SettingRow
        for name in ("harness_autostart", "harness_launch_target"):
            row = dialog.findChild(SettingRow, f"settingRow_{name}")
            assert row is not None
            hint = row.hint_label.text()
            assert "仅主桌宠" not in hint
            assert "影响所有桌宠" in hint

        dialog.harness_autostart_check.setChecked(True)
        dialog.harness_launch_target_select.setCurrentData("service")
        assert dialog.harness_launch_target_select.currentData() == "service"
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    on_disk = _read_json(main.path)
    assert on_disk["harness_autostart"] is True, "子宠页改的键必须真正生效"
    assert on_disk["harness_launch_target"] == "service"


# ------------------------------------------------------------ 8. 分流范围（空身份与 slot 身份都分流）
def test_main_pet_config_is_routed_to_slot0(tmp_path):
    """主宠设置会话（无 --instance 的空身份）也按 slot0 分流（#271 契约变更）。

    旧契约是"空身份原样返回、行为逐位不变"，实机口径已推翻：运行期每宠键只从
    ``config-slot-0.json`` 取源（``_SpriteConfig.get`` 的第一条分支），空身份直写的
    每宠键在 ``Config.save()`` 出闸时被当退役键 pop 掉——写入凭空蒸发、``save()``
    还报成功，用户重开设置看到的是出厂默认（#271：主宠改尺寸/气泡不生效）。
    故空身份与显式 ``slot-0`` 同规：每宠键落 slot0 档案、全局键落主 ``config.json``。
    """
    from pet import pet_profiles

    main = Config(base=tmp_path, instance_id="")
    assert main.save() is True

    routed = settings_slot_config.route_settings_config(main)
    assert isinstance(routed, settings_slot_config.SlotRoutedConfig)
    assert routed.path == spawn_state.slot_config_path(main.dir, 0), \
        "空身份会话的配置面指向 slot0 档案（身份与报错路径都以它为准）"

    routed.set("scale", 0.77)        # 每宠键
    routed.set("pet_opacity", 55)    # 全局键
    assert routed.save() is True

    assert pet_profiles.read_slot_profile(main.dir, 0).value("scale") == pytest.approx(0.77)
    assert "scale" not in _read_json(main.path), "每宠键不许落进主 config.json"
    assert _read_json(main.path)["pet_opacity"] == 55, "全局键照旧落主 config.json"


def test_legacy_topology_slot_config_is_not_routed(tmp_path, monkeypatch):
    """legacy 单窗：那只宠读的就是自己那份 slot 文件的全部键，不许分流。"""
    from pet import overlay_settings_command as cmd_mod

    monkeypatch.setenv(cmd_mod.ENV_TOPOLOGY, "legacy")
    cfg = Config(base=tmp_path, instance_id="slot-1")
    assert isinstance(settings_slot_config.route_settings_config(cfg), settings_slot_config.SlotRoutedConfig)


def test_non_slot_instance_is_not_routed(tmp_path):
    """非 slot-N 的实例身份（测试/诊断用法）：没有可写的 slot 文件，不分流。"""
    cfg = Config(base=tmp_path, instance_id="probe-a")
    assert settings_slot_config.route_settings_config(cfg) is cfg


# ------------------------------------------------------------ 9. 主配置身份（F2 定论）
def test_slot_compat_launch_main_config_is_that_process_main(tmp_path, monkeypatch):
    """F2 定论：``--slot N`` 兼容启动下，路由的"主配置"= 该进程的 config-slot-N.json。

    ``--slot N`` 启动把 ``DSH_PET_INSTANCE=slot-N`` 写进进程环境，设置子进程继承它，
    于是 ``route_settings_config`` 里的主配置构造（``Config(base=...)``，不传
    ``instance_id``）解析出 ``config-slot-N.json``。那就是**该进程**的主配置：运行期
    壳的非 per-slot 键读源是 ``shell._config``（``_SpriteConfig.get`` 的兜底分支），
    而 ``shell._config`` 正是 ``Config(instance_id="slot-N")``。故这不是"读错了主配置"，
    写进去立刻生效（详见模块 docstring 的「主配置身份」一节）。
    """
    from pet import overlay_settings_command as cmd_mod

    monkeypatch.setenv("DSH_PET_INSTANCE", "slot-2")
    monkeypatch.setenv(cmd_mod.ENV_TOPOLOGY, "overlay")

    cfg = Config(base=tmp_path, instance_id="slot-2")
    assert cfg.path == spawn_state.slot_config_path(cfg.dir, 2)
    assert Config(base=tmp_path).path == cfg.path, "env 身份让主配置构造落到同一份文件"

    routed = settings_slot_config.route_settings_config(cfg)
    assert routed is not cfg, "该进程仍是 overlay 拓扑下的 slot 身份，照常分流"
    routed.set("pet_opacity", 42)
    assert routed.save() is True
    assert _read_json(routed._main.path)["pet_opacity"] == 42
    assert routed._main.path.name == "config.json"
    assert not cfg.path.exists()


def test_routed_keys_cover_the_runtime_per_slot_read_surface(tmp_path):
    """分流键集必须覆盖运行期的逐宠取源面，且键集全仓单一来源。

    ``_SpriteConfig.get`` 的 per-slot 读点有两处：``PER_SLOT_SETTING_KEYS`` 分支，
    以及紧随其后 ``collision_enabled`` 的专属分支（``read_slot_collision_enabled``
    读这只宠的 slot 文件）。两处的键都必须分流到 slot 文件，否则子宠页改它等于没改
    （写进主配置既改不动这只宠，又会顺手改掉别的宠的继承值）。
    """
    from pet import overlay_shell, overlay_spawn_state as spawn_state_mod

    assert overlay_shell.PER_SLOT_SETTING_KEYS is spawn_state_mod.PER_SLOT_SETTING_KEYS, (
        "PER_SLOT_SETTING_KEYS 必须全仓单一来源（设置在轻模块、壳侧引用）"
    )
    assert overlay_shell.PER_SLOT_SETTING_KEYS <= settings_slot_config.SLOT_ROUTED_KEYS
    assert "collision_enabled" in settings_slot_config.SLOT_ROUTED_KEYS
    assert "user_customized" in settings_slot_config.SLOT_LOCAL_KEYS
    for main_only in ("pet_opacity", "voice_chime_enabled", "edge_probe_enabled",
                      "dynamic_island", "context_menu_appearance"):
        assert main_only not in settings_slot_config.SLOT_ROUTED_KEYS


# ------------------------------------------------------------ 10. 分流按父进程运行中拓扑（R01）
def test_parent_overlay_topology_routes_child_page_despite_disk_capture_mode(
        tmp_path, monkeypatch):
    """磁盘 ``stream_capture_mode=True``（勾了没重启）+ 父进程正跑 overlay → 照旧分流。

    R01：默认 overlay 运行中，用户在主宠设置勾选「直播捕获兼容」并保存（文案明说
    重启后生效，用户没重启）→ 磁盘上捕获开关为 True。此后打开子宠设置：设置子进程
    按磁盘快照判成 legacy，``route_settings_config`` 原样返回不分流，子宠页把全部
    设置写进 ``config-slot-N.json``——而仍在运行的 overlay 主进程对这些键只读主配置，
    于是"保存成功、实际无效"，还会污染下一次重启的配置身份。

    正解：设置进程按**拉起它的那个主进程的运行中拓扑**分流。
    """
    from pet import overlay_settings_command as cmd_mod

    main = Config(base=tmp_path)
    main.set("stream_capture_mode", True)
    assert main.save() is True
    monkeypatch.delenv(cmd_mod.ENV_TOPOLOGY, raising=False)
    monkeypatch.delenv(cmd_mod.ENV_PARENT_TOPOLOGY, raising=False)
    cmd_mod.reset_topology_snapshot()

    # 主进程拉起设置子进程时把**运行中拓扑**显式传下去（子进程快照是冷的：只按
    # 磁盘读就是 legacy，正是 R01 的错判）。
    monkeypatch.setenv(cmd_mod.ENV_PARENT_TOPOLOGY, "overlay")
    cfg = Config(base=tmp_path, instance_id="slot-1")

    routed = settings_slot_config.route_settings_config(cfg)
    assert routed is not cfg, "父进程还在 overlay 跑：子宠页必须分流"

    routed.set("pet_opacity", 33)
    assert routed.save() is True
    assert _read_json(main.path)["pet_opacity"] == 33, "非 per-slot 键落主配置（真生效）"
    assert not _slot_path(main.dir).exists(), "全局保存不创建宠档案"


def test_parent_legacy_topology_does_not_route_even_when_disk_says_overlay(
        tmp_path, monkeypatch):
    """父进程正跑 legacy（捕获模式/dev 逃生门）→ 即使磁盘捕获开关关着也不分流。

    那只宠读的就是自己那份 ``config-slot-N.json`` 的全部键，分流会让它的设置失效。
    父进程的陈述与磁盘互相矛盾时以**父进程运行中拓扑**为准：那才是这个设置页正在
    编辑的那个进程。
    """
    from pet import overlay_settings_command as cmd_mod

    main = Config(base=tmp_path)
    assert main.save() is True
    cmd_mod.reset_topology_snapshot()
    monkeypatch.delenv(cmd_mod.ENV_TOPOLOGY, raising=False)
    monkeypatch.setenv(cmd_mod.ENV_PARENT_TOPOLOGY, "legacy")

    cfg = Config(base=tmp_path, instance_id="slot-1")
    assert settings_slot_config.route_settings_config(cfg) is not cfg

    # 父进程陈述与 dev 逃生门同时在场且互相矛盾：父进程运行中拓扑优先
    monkeypatch.setenv(cmd_mod.ENV_TOPOLOGY, "overlay")
    assert settings_slot_config.route_settings_config(cfg) is not cfg


def test_missing_parent_topology_env_falls_back_to_existing_verdict(
        tmp_path, monkeypatch):
    """父进程没发话（手工单开设置页/旧主进程）→ 判定逐位回到原有链路。"""
    from pet import overlay_settings_command as cmd_mod

    main = Config(base=tmp_path)
    main.set("stream_capture_mode", True)
    assert main.save() is True
    cmd_mod.reset_topology_snapshot()
    monkeypatch.delenv(cmd_mod.ENV_TOPOLOGY, raising=False)
    monkeypatch.delenv(cmd_mod.ENV_PARENT_TOPOLOGY, raising=False)

    cfg = Config(base=tmp_path, instance_id="slot-1")
    assert settings_slot_config.route_settings_config(cfg) is not cfg, \
        "缺父进程陈述时照旧按磁盘快照判（勾了没重启 = 那个进程就是 legacy）"

    monkeypatch.setenv(cmd_mod.ENV_PARENT_TOPOLOGY, "overlay")
    assert settings_slot_config.route_settings_config(cfg) is not cfg, \
        "同一份磁盘配置，父进程陈述 overlay 就分流"


# ------------------------------------------------------------ 11. 坏档案（G1b 接线后）
@pytest.mark.parametrize("payload", [
    b'{ "scale": 1.0, broken',                      # 非法 JSON
    b'{"scale": 1.0, "note": "\xff\xfe bad"}',      # 非法 UTF-8（不许抛 UnicodeDecodeError）
])
def test_corrupt_slot_file_keeps_the_dialog_usable_and_is_never_overwritten(
        tmp_path, app, payload):
    """坏档案：设置窗照旧能开（读侧同源默认），保存如实失败且**一个字节都不覆盖**。

    档案是手改/半截 JSON 的一手数据。旧实现把它当空 dict 读、再按这份"空"整表重写——
    用户手改坏的文件被静默销毁成只剩这次保存的那几个键，而 ``save()`` 还报成功。
    """
    main = Config(base=tmp_path)
    assert main.save() is True
    cfg = Config(base=tmp_path, instance_id="slot-1")   # 落种出 slot 文件
    path = _slot_path(main.dir)
    path.write_bytes(payload)
    before = path.read_bytes()

    dialog = _dialog(cfg, standalone=True)
    try:
        assert float(dialog.scale_combo.currentData()) == pytest.approx(DEFAULT_SCALE), (
            "坏档案必须按同源默认渲染，而不是把设置窗构造拖垮")
        assert dialog._write_config() is False, "坏档案不得被当成空档案整表覆盖"
    finally:
        dialog.deleteLater()

    assert path.read_bytes() == before, "坏档案一个字节都不许动"


# ------------------------------------------------------------ 12. 表单编辑代次（防回归契约）
def test_form_edit_generation_is_pinned_against_an_external_reset(tmp_path, app):
    """R5：表单打开后外部推进该槽代次 → 旧控件值**不许**以新代次落盘。

    旧实现的链条：``_write_config`` 开头 ``config.reload()`` → 路由视图作废档案快照
    （此刻 ``_pending_slot`` 还空着）→ 保存时 ``_slot_values()`` 重新取快照，拿到的是
    **外部 reset 之后**的新代次 → 表单里的旧控件值带着这个新代次写回，通过代次门，
    外部 reset 被静默抹掉（triage 实测 ``reset_undone=true``）。

    注：今天没有**活的**触发路径——唯一 reset 写者就是这个自关窗的设置页本身，而
    ``settings.lock`` 拦住第二个设置窗。所以这是一条**契约/防回归**测试，不是线上缺陷
    复现：契约是"表单的编辑代次在打开时就钉住，reload 洗不掉它"。
    """
    from pet import pet_profiles

    main = Config(base=tmp_path)
    assert main.save() is True
    assert pet_profiles.write_profile_settings(
        main.dir, 1, {"scale": 0.8, "playback_speed": 1.5},
        user_customized=True).status == "ok"
    cfg = Config(base=tmp_path, instance_id="slot-1")
    dialog = _dialog(cfg)
    try:
        assert float(dialog.scale_combo.currentData()) == pytest.approx(0.8)

        # 外部写者（真实 API）：reset 该槽 → 代次推进、偏好回同源默认
        snapshot = pet_profiles.read_slot_snapshot(main.dir, 1)
        assert snapshot.status == "ok"
        assert pet_profiles.reset_slot_profile(
            main.dir, 1, generation=snapshot.generation).status == "ok"

        assert dialog._write_config() is False, (
            "表单里的旧控件值拿到了新代次：外部 reset 会被静默抹掉")
        profile = pet_profiles.read_slot_profile(main.dir, 1)
        assert profile.value("scale") == pytest.approx(DEFAULT_SCALE), "外部 reset 的结果必须存活"
        assert profile.value("playback_speed") == 1.0
        assert float(dialog.scale_combo.currentData()) == pytest.approx(0.8), (
            "被拒时草稿必须留在表单里（不清 pending、不静默丢用户改动）")
    finally:
        dialog.deleteLater()


def test_form_edit_generation_still_allows_the_normal_save_path(tmp_path, app):
    """对照：没有外部代次推进时，同一条保存路径照常成功（钉住的是代次，不是"总拒绝"）。"""
    from pet import pet_profiles

    main = Config(base=tmp_path)
    assert main.save() is True
    assert pet_profiles.write_profile_settings(
        main.dir, 1, {"scale": 0.8}, user_customized=True).status == "ok"
    dialog = _dialog(Config(base=tmp_path, instance_id="slot-1"))
    try:
        dialog.pet_opacity_spin.setValue(40)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    assert pet_profiles.read_slot_profile(main.dir, 1).value("scale") == pytest.approx(0.8)
