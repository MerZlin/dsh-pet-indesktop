# -*- coding: utf-8 -*-
"""B7b 逐只设置：per-slot 读点/写点/音效/气泡/自言自语/角色切换（offscreen）。

旧架构每只桌宠是**独立进程**，各读自己的 ``config-slot-N.json``：缩放/速度/
拖拽物理/抛掷力度/音效门与音量/气泡族/角色全部逐只独立，各自一个气泡、
各自计时自言自语，右键菜单与设置页都作用在**被点的那一只**身上。单进程
overlay 壳此前只有碰撞资格位走了 slot 配置，其余键一律推主配置（审计
C1/C2/C3/C5/F1：给子宠改设置"保存后毫无反应"，甚至改到主宠头上）。

本文件锁新的逐只契约（真壳 + 真 ``PetSprite`` + 真落盘 ``Config``）：

1. slot 配置事件只重下发**那一个 slot** 的设置，别的宠一个键都不动；
2. 逐只音效：门/音量/音源取触发 sprite 自己那份配置（点击按被点的那只，
   碰撞按参与的第一只宠物）；
3. 逐只气泡：点击台词/余额/识屏的落点 = 被点/发起的那一只；
4. 逐只自言自语：每只子宠一个**单发**定时器、按它自己的间隔与文本池；
5. 逐只角色：``switch_character(cid, sprite)`` 只换被点那只的库并写它的 slot；
6. 逐只持久化：facade 的菜单设置写进该子宠的 slot 文件（不写脏主配置）；
7. 生命周期：退出/提升/屏迁移时子宠气泡与定时器正确收尾或接管。

纪律：同步直调 + 真实信号；不 sleep、不赌目录枚举顺序；所有落盘都在
``tmp_path`` 内；``play_sound`` 全程打桩（不出声）。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import tests.test_overlay_collision_toggle as toggle
import tests.test_sprite_menu_facade as fac
from pet import catalog
from pet import click_sound
from pet import overlay_spawn_state as ovs
from pet.physics import THROW_STRENGTH_CAPS
from pet.sprite_menu_facade import SpriteMenuFacade, build_sprite_full_menu

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 脚手架
def _write_slot(cfg, slot: int, **values) -> None:
    """按"这只子肥鱼自己的设置页保存"的写法改它的 slot 配置（原子写单键）。"""
    for key, value in values.items():
        assert ovs.write_slot_setting(cfg.dir, slot, key, value,
                                      user_customized=True), key


def _character_action(menu, label: str):
    """「切换角色」子菜单里文本为 ``label`` 的勾选动作（递归找子菜单）。"""
    for action in menu.actions():
        submenu = action.menu()
        if submenu is None:
            continue
        for item in submenu.actions():
            if item.text() == label:
                return item
        found = _character_action(submenu, label)
        if found is not None:
            return found
    return None


def _slot_file(cfg, slot: int) -> dict:
    return json.loads((cfg.dir / f"config-slot-{slot}.json").read_text(
        encoding="utf-8"))


def _notify(shell) -> None:
    """走壳体既有的"配置目录有变动"入口（D12 watcher 的目录事件回调）。"""
    shell._on_settings_command_dir_changed(str(shell._config.dir))


def _pump() -> None:
    QApplication.processEvents()
    QApplication.processEvents()


def _silence_sound(monkeypatch) -> list:
    """打桩 ``click_sound.play_sound`` 并返回音量记录（音效用例专用）。"""
    plays: list = []
    monkeypatch.setattr(click_sound, "play_sound",
                        lambda path, volume: plays.append(volume) or True)
    monkeypatch.setattr(click_sound, "resolve_click_sound_candidates",
                        lambda pack, data_dir=None: ["/tmp/click.wav"])
    return plays


def _talk_values(**over) -> dict:
    values = {
        "self_talk_enabled": True,
        "self_talk_texts": ["子宠台词"],
        "self_talk_duration_seconds": 1.0,
        "self_talk_min_interval": 7.0,
        "self_talk_max_interval": 7.0,
        "self_talk_image_chance": 0,
    }
    values.update(over)
    return values


# ---------------------------------------------------------------- 1. per-slot 读点
def test_slot_event_applies_only_to_that_slot(tmp_path):
    """预写 config-slot-1.json → 只有子宠 1 按它自己那份设置变，别的宠不动。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, playback_speed=1.0,
                                         drag_physics=False,
                                         throw_strength="gentle")
    try:
        shell.spawn_pet()
        shell.spawn_pet()
        main, child_a, child_b = list(shell.overlay.sprites)
        assert child_a.playback_speed == 1.0 and child_b.playback_speed == 1.0

        # 运行期手改（模拟其它热改路径留下的值）：三只都改，再看谁被刷回
        for sprite in (main, child_a, child_b):
            sprite.playback_speed = 2.5
            sprite.drag_physics = True
            sprite.throw_speed_cap = 1234.0

        _write_slot(cfg, 1, playback_speed=0.5, drag_physics=False,
                    throw_strength="crazy")
        _notify(shell)

        # 子宠 1：以它自己的文件为准（旧契约"设置保存即覆盖该宠运行态"）
        assert child_a.playback_speed == 0.5
        assert child_a.drag_physics is False
        assert child_a.throw_speed_cap == THROW_STRENGTH_CAPS["crazy"]
        # 主宠与另一只子宠：一个键都不许动（窄契约）
        for sprite in (main, child_b):
            assert sprite.playback_speed == 2.5
            assert sprite.drag_physics is True
            assert sprite.throw_speed_cap == 1234.0
    finally:
        toggle._teardown(shell)


def test_slot_scale_and_playback_survive_main_refresh(tmp_path):
    """子宠按自己那份配置取源：主配置刷新（refresh_settings）不得拖走它。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, scale=1.0, playback_speed=1.0)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        _write_slot(cfg, 1, scale=1.5, playback_speed=0.5)
        _notify(shell)
        assert child.scale == pytest.approx(1.5)

        _write_slot(cfg, 0, scale=0.3, playback_speed=3.0)
        shell.refresh_settings()
        assert shell.sprite.scale == pytest.approx(0.3)
        assert shell.sprite.playback_speed == pytest.approx(3.0)
        assert child.scale == pytest.approx(1.5)          # 子宠各读各的
        assert child.playback_speed == pytest.approx(0.5)
    finally:
        toggle._teardown(shell)


def test_slot_missing_or_broken_uses_defaults_not_main(tmp_path):
    """缺文件/缺键取同源默认；坏档案不覆盖、不碰主配置。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, playback_speed=1.0,
                                         drag_physics=False)
    try:
        shell.spawn_pet()
        main, child = list(shell.overlay.sprites)
        slot_path = cfg.dir / "config-slot-1.json"
        main_bytes = (cfg.dir / "config.json").read_bytes()

        # 缺文件 → 同源默认，不从主宠持续继承
        slot_path.unlink()
        _notify(shell)
        assert child.playback_speed == 1.0 and child.drag_physics is False

        # 损坏 → 逐键回默认（playback 1.0 / drag False），主配置一字不动
        slot_path.write_text("{oops", encoding="utf-8")
        _notify(shell)
        assert child.playback_speed == 1.0
        assert child.drag_physics is False
        assert (cfg.dir / "config.json").read_bytes() == main_bytes

        # 缺键（老存档）→ 同源默认，不继承主宠当前值
        _write_slot(cfg, 0, playback_speed=2.0)
        shell.refresh_settings()
        slot_path.write_text('{"version": 4}', encoding="utf-8")
        _notify(shell)
        assert child.playback_speed == 1.0
        assert main.playback_speed == 2.0
    finally:
        toggle._teardown(shell)


# ---------------------------------------------------------------- 2. 逐只音效
def test_garbage_slot_values_degrade_to_defaults(tmp_path):
    """手改坏的 slot 值（错类型/负数/字符串）不得掀掉同步链，逐键回退/夹取。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, throw_strength="gentle")
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        (cfg.dir / "config-slot-1.json").write_text(json.dumps({
            "version": 4,
            "playback_speed": "loud",
            "drag_physics": "yes",
            "throw_strength": {"nope": 1},
            "scale": -3,
            "ffmpeg_recycle_minutes": "soon",
            "self_talk_texts": "not-a-list",
            "self_talk_min_interval": "x",
            "self_talk_max_interval": -5,
        }), encoding="utf-8")
        _notify(shell)

        assert child.playback_speed == pytest.approx(1.0)          # 坏值 → 缺省
        assert child.throw_speed_cap == THROW_STRENGTH_CAPS["standard"]
        assert child.scale == pytest.approx(0.1)                   # 负数 → 夹到下界
        assert child.drag_physics is True                          # 非空字符串为真
        host = shell._self_talk_hosts[child]
        assert host._self_talk_min_interval >= 5.0
        assert host._self_talk_max_interval >= host._self_talk_min_interval
        assert host._self_talk_texts, "坏文本池回退内置默认池（不空转）"
    finally:
        toggle._teardown(shell)


def test_click_sound_uses_clicked_sprite_config(tmp_path, monkeypatch):
    """点击音效：门/音量取**被点的那一只**的配置（旧版每窗各播自己那一声）。"""
    plays = _silence_sound(monkeypatch)
    shell, cfg = toggle._make_slot_shell(
        tmp_path, click_sound_enabled=True, click_sound_volume=0.5)
    try:
        shell.spawn_pet()
        main, child = list(shell.overlay.sprites)
        _write_slot(cfg, 1, click_sound_enabled=False)
        _notify(shell)

        shell._sound._last_play = 0.0
        shell._on_sprite_click(child, "")
        _pump()
        assert plays == []                      # 子宠自己关了门 → 不响

        shell._sound._last_play = 0.0
        shell._on_sprite_click(main, "")
        _pump()
        assert plays == [0.5]                   # 主宠门仍开 → 按主宠音量响

        _write_slot(cfg, 1, click_sound_enabled=True, click_sound_volume=0.1)
        _notify(shell)
        shell._sound._last_play = 0.0
        shell._on_sprite_click(child, "")
        _pump()
        assert plays[-1] == 0.1                 # 子宠自己的音量
    finally:
        toggle._teardown(shell)


def test_collision_sound_uses_participating_sprite_config(tmp_path, monkeypatch):
    """碰撞音效：按事件成员 id 解析出的**参与宠物**的配置取门/音量。"""
    plays = _silence_sound(monkeypatch)
    shell, cfg = toggle._make_slot_shell(
        tmp_path, collision_sound_volume=0.5, collision_sound_enabled=True)
    try:
        shell.spawn_pet()
        main, child = list(shell.overlay.sprites)
        _write_slot(cfg, 1, collision_sound_volume=0.2, collision_sound_enabled=True)
        _notify(shell)

        from pet.sprite_collision import SpriteCollisionWorld

        child_id = SpriteCollisionWorld._member_id(child)
        event = type("E", (), {"a": child_id, "b": "island", "j": 999.0})()
        shell._sound._last_play = 0.0
        shell._sound.on_collision(event)
        _pump()
        assert plays == [0.2]                   # 子宠自己那份音量

        _write_slot(cfg, 1, collision_sound_enabled=False)
        _notify(shell)
        shell._sound._last_play = 0.0
        shell._sound.on_collision(event)
        _pump()
        assert plays == [0.2]                   # 子宠门关了 → 不再响

        main_id = SpriteCollisionWorld._member_id(main)
        shell._sound._last_play = 0.0
        shell._sound.on_collision(
            type("E", (), {"a": main_id, "b": "island", "j": 999.0})())
        _pump()
        assert plays == [0.2, 0.5]              # 主宠照旧按主配置
    finally:
        toggle._teardown(shell)


# ---------------------------------------------------------------- 3. 逐只气泡
def test_click_self_talk_bubble_lands_on_clicked_child(tmp_path, monkeypatch):
    """子宠点击台词气泡出现在**子宠自己**头顶（此前一律落主宠）。"""
    _silence_sound(monkeypatch)
    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        _write_slot(cfg, 1, click_show_self_talk=True, **_talk_values())
        _notify(shell)
        follower = shell._bubble_followers.get(child)
        assert follower is not None and follower.bubble is not None, \
            "spawn 必须给子宠建它自己的气泡跟随器"

        shell._on_sprite_click(child, "")

        assert follower.bubble._raw_text == "子宠台词"
        main_bubble = shell._speech_bubble
        assert getattr(main_bubble, "_raw_text", "") != "子宠台词"
    finally:
        toggle._teardown(shell)


def test_balance_bubble_uses_clicked_sprite_host(tmp_path, monkeypatch):
    """余额气泡（``AppShell.show_balance``）拿到的是**被点那一只**的宿主。"""
    _silence_sound(monkeypatch)
    shell, cfg = toggle._make_slot_shell(tmp_path)
    hosts: list = []
    shell._instance.shell = type(
        "_Shell", (), {"show_balance": lambda self, parent=None: hosts.append(parent)})()
    try:
        shell.spawn_pet()
        main, child = list(shell.overlay.sprites)
        _write_slot(cfg, 1, click_show_balance=True)
        _notify(shell)

        shell._on_sprite_click(child, "")
        assert len(hosts) == 1 and hosts[0] is not shell
        assert hosts[0].show_bubble("余额：¥1", duration_ms=1000) is True
        assert shell._bubble_followers[child].bubble._raw_text == "余额：¥1"

        # 主宠开同一开关后点击 → 仍传壳本身（主宠行为不变）
        _write_slot(cfg, 0, click_show_balance=True)
        shell.refresh_settings()
        shell._on_sprite_click(main, "")
        assert hosts[1]._sprite is main
        shell.exit_pet(main)
        assert hosts[1].show_bubble("late reply") is False
    finally:
        toggle._teardown(shell)


def test_look_bubble_lands_on_initiator(tmp_path):
    """识屏气泡落**发起者**（B2 记下的 ``_look_sprite``）。"""
    shell, _cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        follower = shell._bubble_followers[child]
        shell._look_sprite = child
        shell._look_bubble("让我看看…")
        assert follower.bubble._raw_text == "让我看看…"
        assert getattr(shell._speech_bubble, "_raw_text", "") != "让我看看…"
    finally:
        toggle._teardown(shell)


# ---------------------------------------------------------------- 4. 逐只自言自语
def test_child_self_talk_uses_own_timer_and_config(tmp_path):
    """子宠自言自语：自己的单发定时器 + 自己的间隔/文本池，到点落自己气泡。"""
    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        _write_slot(cfg, 1, **_talk_values())
        _notify(shell)

        host = shell._self_talk_hosts.get(child)
        assert host is not None, "spawn 必须给子宠建它自己的自言自语宿主"
        assert host._self_talk_timer is not shell._self_talk_timer
        assert host._self_talk_timer.isSingleShot() is True   # 无常驻高频表
        assert host._self_talk_timer.isActive() is True
        assert host._self_talk_timer.interval() == 7000       # 它自己的间隔
        assert shell._self_talk_timer.isActive() is False     # 主宠没开，不排程

        host._self_talk_timer.timeout.emit()
        assert shell._bubble_followers[child].bubble._raw_text == "子宠台词"
        assert host._self_talk_timer.interval() == 8000       # 显示后按 after_display 重排

        # 主宠隐藏 → 子宠计时同样停表；恢复显示重排
        shell.set_pet_visible(False)
        assert host._self_talk_timer.isActive() is False
        shell.set_pet_visible(True)
        assert host._self_talk_timer.isActive() is True
    finally:
        toggle._teardown(shell)


# ---------------------------------------------------------------- 5. 逐只角色
def test_switch_character_child_keeps_main_untouched(tmp_path):
    """子宠切角色：只换它自己的库、只写它自己的 slot 文件，主宠一字不动。"""
    shell, cfg = toggle._make_slot_shell(tmp_path)
    made: list = []

    def _create(cid):
        made.append(cid)
        return fac.RichLibrary()

    shell._instance._create_library = _create
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        main_lib = shell.lib
        old_child_lib = shell._spawned_libs[child]
        main_character = cfg.get("character")

        shell.switch_character("other-character", child)

        assert made[-1] == "other-character"
        assert "other-character" in made
        assert child.library is not old_child_lib
        assert shell._spawned_libs[child] is child.library
        assert old_child_lib.shutdown_calls == 1
        # 主宠与主配置：一字不动
        assert shell.sprite.library is main_lib and shell.lib is main_lib
        assert cfg.get("character") == main_character
        # 该子宠自己的 slot 文件写下了角色（重启复活读它）
        assert _slot_file(cfg, 1)["character"] == "other-character"
    finally:
        toggle._teardown(shell)


def test_character_menu_check_state_follows_clicked_sprite(tmp_path):
    """「切换角色」勾选态读**被点那只**的角色；菜单触发只切被点那只（F1）。"""
    shell, cfg = toggle._make_slot_shell(tmp_path)
    made: list = []

    def _create(cid):
        made.append(cid)
        return fac.RichLibrary()

    shell._instance._create_library = _create
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        main_character = str(cfg.get("character"))
        label = catalog.character_display_name(main_character)

        # 这只子肥鱼自己的角色不是主宠那只 → 勾选态必须按它自己算
        _write_slot(cfg, 1, character="other-character")
        _notify(shell)
        assert _character_action(
            build_sprite_full_menu(shell, shell.sprite), label).isChecked() is True
        assert _character_action(
            build_sprite_full_menu(shell, child), label).isChecked() is False

        old_child_lib = shell._spawned_libs[child]
        _character_action(build_sprite_full_menu(shell, child), label).trigger()
        assert made[-1] == main_character
        assert child.library is not old_child_lib
        assert shell.sprite.library is shell.lib      # 主宠没被换
    finally:
        toggle._teardown(shell)


def test_child_character_is_restored_from_its_slot(tmp_path):
    """复活：子宠按**自己** slot 里的角色建库（缺文件继承主配置）。"""
    shell, cfg = toggle._make_slot_shell(tmp_path)
    made: list = []

    def _create(cid):
        made.append(cid)
        return fac.RichLibrary()

    shell._instance._create_library = _create
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        shell.switch_character("other-character", child)
        assert _slot_file(cfg, 1)["character"] == "other-character"
    finally:
        toggle._teardown(shell)

    made.clear()
    restored, _cfg = toggle._make_slot_shell(tmp_path)
    restored._instance._create_library = _create
    try:
        restored_shell = restored
        restored_shell._restore_spawned_pets()
        assert made == ["other-character"], "复活必须按各 slot 自己的角色建库"
        assert restored_shell._spawned, "复活前置：子宠在活跃清单里"
    finally:
        toggle._teardown(restored)


# ---------------------------------------------------------------- 6. 逐只持久化
def test_facade_persists_child_settings_to_its_slot(tmp_path):
    """右键菜单的逐只设置写进该子宠的 slot 文件，且不写脏主配置。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, scale=1.0, playback_speed=1.0)
    try:
        shell.spawn_pet()
        main, child = list(shell.overlay.sprites)
        facade = SpriteMenuFacade(shell, child)

        facade.change_scale(1.5)
        facade.set_playback_speed(2.0)
        facade.set_drag_physics(True)

        assert child.scale == pytest.approx(1.5)
        assert child.playback_speed == pytest.approx(2.0)
        assert child.drag_physics is True
        # 主宠与主配置：一个键都没被写
        assert main.scale == pytest.approx(1.0)
        assert shell._config.get("scale") == pytest.approx(1.0)
        assert shell._config.get("playback_speed") == pytest.approx(1.0)
        assert shell._config.get("drag_physics") is False
        slot = _slot_file(cfg, 1)
        assert slot["scale"] == pytest.approx(1.5)
        assert slot["playback_speed"] == pytest.approx(2.0)
        assert slot["drag_physics"] is True
        assert slot.get("user_customized") is True   # 旧契约：用户主动设置过

        SpriteMenuFacade(shell, main).change_scale(0.8)
        assert _slot_file(cfg, 0)["scale"] == pytest.approx(0.8)
    finally:
        toggle._teardown(shell)


# ---------------------------------------------------------------- 7. 生命周期
def test_exit_child_releases_its_bubble_and_timer(tmp_path):
    """退出一只子宠：它的气泡窗与自言自语定时器都收掉（不留孤儿）。"""
    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        _write_slot(cfg, 1, **_talk_values())
        _notify(shell)
        follower = shell._bubble_followers[child]
        host = shell._self_talk_hosts[child]
        bubble = follower.bubble
        assert follower.show("在吗", 3000) is True
        assert bubble.isVisible() is True

        assert shell.exit_pet(child) is True

        assert child not in shell._bubble_followers
        assert child not in shell._self_talk_hosts
        assert bubble.isVisible() is False, "子宠气泡窗必须收掉"
        timer = getattr(host, "_self_talk_timer", None)
        assert timer is None or timer.isActive() is False
    finally:
        toggle._teardown(shell)


def test_promotion_adopts_child_bubble_and_keeps_its_settings(tmp_path):
    """主宠退出提升：提升者的气泡被接管（不新建孤儿），它自己的设置随之接管。"""
    shell, cfg = toggle._make_slot_shell(tmp_path, playback_speed=1.0)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        _write_slot(cfg, 1, playback_speed=0.5)
        _notify(shell)
        assert child.playback_speed == pytest.approx(0.5)
        follower = shell._bubble_followers[child]

        assert shell.exit_pet(shell.sprite) is True

        assert shell.sprite is None
        assert shell._bubble_followers[child] is follower
        assert child in shell._self_talk_hosts
        assert child.playback_speed == pytest.approx(0.5)
        assert shell._spawned_slots[child] == 1
    finally:
        toggle._teardown(shell)


def test_migrate_keeps_every_bubble(tmp_path):
    """换家：主 + 每只子宠的气泡跟随器都留在原地（单窗不重建跟随器）。

    跟随器记的是窗口对象；单窗拓扑下换家不动窗口，所以既不重建也不丢气泡。
    """
    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        old_main = shell._bubble_follower
        old_child = shell._bubble_followers[child]

        shell._migrate_to_screen(QGuiApplication.primaryScreen())

        assert shell._bubble_follower is old_main
        assert shell._bubble_followers[child] is old_child
        assert shell.overlay is old_main._overlay
    finally:
        shell.overlay.close()
        toggle._teardown(shell)


# ---------------------------------------------------------------- 8. A5 隐藏提示
def test_user_hide_notifies_host_tray(tmp_path):
    """用户主动隐藏（整窗 / 逐只主宠）→ 走宿主的托盘提示（A5）。"""
    shell, _cfg = toggle._make_slot_shell(tmp_path)
    calls: list = []
    shell._instance._notify_pet_hidden = lambda: calls.append(1)
    try:
        shell.set_pet_visible(False)
        assert calls == [1]
        shell.set_pet_visible(True)
        assert calls == [1], "恢复显示不该再提示"

        shell.set_sprite_visible(shell.sprite, False)
        assert calls == [1, 1]
    finally:
        toggle._teardown(shell)
