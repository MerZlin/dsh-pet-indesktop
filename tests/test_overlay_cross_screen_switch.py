# -*- coding: utf-8 -*-
"""「允许桌宠跨屏」开关：默认关、重启生效，关着时活动区只留启动那块屏。

用户口径（2026-10-17）：一张合成窗铺满整个虚拟桌面，**活动区**（宠能站的地方）
由**一个全局开关**控制，默认关（默认关 = 单显示器用户与旧行为逐位相同）；
关着时活动区只剩启动时那块屏，宠拖不过去；开关是**重启级**（进程内快照）。

位置持久化的数据语义（本文件盯住的那一条）：档案里的 ``screen_name`` 是
**屏归属**，只有有意把某只拖到另一块屏时才写（``_remember_sprite_screen``）；
位置自存（拖动收尾 / 退出 / 开局落位）只写比例与朝向。于是：

- 关掉跨屏、或原来那块屏临时不在线，都不会改写用户摆好的多屏归属；
- 重新开启并重启，各宠仍回到各自那块屏（round trip）；
- 单屏下普通拖动的比例持久化不受影响（不是"关闭时一律禁写"）。

纪律：offscreen；假屏只补**类级** ``QGuiApplication``（补实例属性会在 PySide6
对象上留影子，撤不干净）；全部断言走公开接缝（``hosts()`` / ``host_for_sprite``
/ ``move_sprite_to_screen`` / 真鼠标事件 / 真档案文件）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
import pytest

pytestmark = pytest.mark.usefixtures("non_macos_overlay_topology")

import tests.test_overlay_independent_screens as indep
from pet.config import Config
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from tests.test_overlay_hosts_multiscreen import (
    _hittable, _move, _press, _release)
from tests.test_overlay_independent_screens import (
    _child, _pets, _profile, _teardown)
from tests.test_overlay_screen_migration import FakeScreen

app = QApplication.instance() or QApplication([])

PRIMARY_GEO = (0, 0, 1920, 1080)
PRIMARY_AVAIL = (0, 0, 1920, 1040)
# 两块同高：拖拽用例沿 y 平移时身体中心仍落在副屏里（副屏比主屏矮时，从主屏
# 右下角出发的水平拖动会擦过副屏底边之下，"跨不过去"就成了位置问题而不是开关问题）。
SECONDARY_GEO = (1920, 0, 1920, 1080)
SECONDARY_AVAIL = (1920, 0, 1920, 1040)


def two_screens():
    return (FakeScreen(PRIMARY_GEO, PRIMARY_AVAIL, name="DISPLAY1"),
            FakeScreen(SECONDARY_GEO, SECONDARY_AVAIL, name="DISPLAY2"))


def _screen_of(sprite):
    """这只宠当前所在屏（由用例的壳提供，见 ``_shell`` 注入的 resolver）。"""
    return _CURRENT[0]._screen_of_sprite(sprite)


_CURRENT = [None]


def _install(monkeypatch, screens):
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: list(screens)))
    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: screens[0]))


def _config(tmp_path, *, allow_cross_screen=False, **values) -> Config:
    cfg = Config(base=tmp_path)
    cfg.set("allow_cross_screen", allow_cross_screen)
    for key, value in values.items():
        cfg.set(key, value)
    cfg.save()
    return cfg


def _shell(cfg, screen) -> OverlayShell:
    shell = OverlayShell(
        app, indep._Instance(cfg), screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    _CURRENT[0] = shell
    return shell


def _seed_two_pets(config_dir, *, slot0_screen=None, slot1_screen=None):
    """预置两只活跃宠（slot0 + slot1）与各自的屏记忆，供复活路径读。

    ``config_dir`` 必须是 ``Config.dir``（= ``base/<应用目录>``）：档案、活跃清单
    与几何都落在这里，直接拿 ``tmp_path`` 是另一个目录。
    """
    from pet import pet_profiles

    for slot, screen_name in ((0, slot0_screen), (1, slot1_screen)):
        pet_profiles.write_profile_settings(config_dir, slot, {"scale": 0.5})
        if screen_name is not None:
            pet_profiles.write_profile_geometry(
                config_dir, slot, {"rx": 0.25, "ry": 0.75, "screen_name": screen_name})
    pet_profiles.save_active_set(config_dir, [0, 1], on_broken="backup-overwrite")


# ---------------------------------------------------------------- 1. 默认与重启级
def test_switch_defaults_off_and_the_shell_snapshots_it(tmp_path, monkeypatch):
    primary, _secondary = two_screens()
    _install(monkeypatch, [primary])
    cfg = _config(tmp_path)
    assert cfg.get("allow_cross_screen") is False, "出厂默认必须是关"
    shell = _shell(cfg, primary)
    try:
        assert shell._cross_screen_allowed is False
        assert len(shell.hosts()) == 1
        assert shell.overlay is shell.host_for_sprite(shell.sprite).overlay
    finally:
        _teardown(shell)


def test_switch_takes_effect_only_after_restart(tmp_path, monkeypatch):
    """勾上开关只改磁盘：正在跑的进程仍按旧值（进程快照），重启后才放行跨屏。"""
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        cfg.set("allow_cross_screen", True)
        cfg.save()

        assert shell._cross_screen_allowed is False, "运行中的进程不重读这个键"
        assert shell.move_sprite_to_screen(child, secondary) is False
        assert shell.host_for_sprite(child).screen is primary
        assert len(shell.hosts()) == 1, "启动那块屏以外不得建窗"
    finally:
        _teardown(shell)

    shell2 = _shell(cfg, primary)
    try:
        assert shell2._cross_screen_allowed is True
        assert len(_pets(shell2)) == 2, "前提：活跃宠清单让子宠复活"
        assert shell2.move_sprite_to_screen(_child(shell2), secondary) is True
        assert len(shell2.hosts()) == 1, "单窗：允许跨屏也不多开窗口"
        assert shell2._screen_of_sprite(_child(shell2)) is secondary
    finally:
        _teardown(shell2)


# ---------------------------------------------------------------- 2. 关闭 = 单屏一窗
def test_off_keeps_all_pets_on_the_started_screen_in_one_window(tmp_path, monkeypatch):
    primary, _secondary = two_screens()
    _install(monkeypatch, [primary, _secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    _seed_two_pets(cfg.dir, slot0_screen="DISPLAY2", slot1_screen="DISPLAY2")
    shell = _shell(cfg, primary)
    try:
        assert len(_pets(shell)) == 2
        assert len(shell.hosts()) == 1, "关闭跨屏 = 只有启动那块屏一张窗"
        host = shell.host_for_sprite(shell.sprite)
        assert host.screen is primary
        assert shell._screen is primary, "不按记忆里的屏换主宿主的家"
        for sprite in _pets(shell):
            assert shell.host_for_sprite(sprite) is host
            assert sprite in shell.overlay.sprites
        # 记忆只作参考、不被清掉：启动路径一个字节都不写档案
        for slot in (0, 1):
            assert _profile(cfg.dir, f"slot-{slot}")["screen_name"] == "DISPLAY2"
    finally:
        _teardown(shell)


def test_off_refuses_to_move_a_pet_to_another_screen(tmp_path, monkeypatch):
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        slot = shell._spawned_slots[child]
        pos = QPointF(child.pos)

        assert shell.move_sprite_to_screen(child, secondary) is False
        assert shell.host_for_sprite(child).screen is primary
        assert child.pos == pos, "拒绝搬移就是一动都不动"
        assert len(shell.hosts()) == 1
        assert [screen for screen, _r in shell._main_host.activity] == [primary], (
            "拒绝搬移 = 目标屏不在活动区里（窗仍铺满全桌面）")
        shell.save_spawned_positions()
        assert _profile(cfg.dir, f"slot-{slot}").get("screen_name") is None, (
            "被拒绝的搬移不得留下屏归属")
    finally:
        _teardown(shell)


def test_off_keeps_a_cross_screen_drag_inside_the_started_screen(tmp_path, monkeypatch):
    """真鼠标拖到缝的另一侧：宠被活动区（只剩启动那块屏）钳住，拖不过去。"""
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    shell = _shell(cfg, primary)
    try:
        main = shell.sprite
        overlay = shell.overlay
        origin = QPointF(overlay.geometry().topLeft())
        _hittable(main)
        hit = QPointF(main.rect().center())
        overlay.mousePressEvent(_press(hit, origin + hit))
        cursor = origin + hit
        for _ in range(16):
            cursor = QPointF(cursor.x() + 40.0, cursor.y())
            overlay.mouseMoveEvent(_move(cursor - origin, cursor))
            assert _screen_of(main) is primary

        assert secondary.geometry().contains(cursor.toPoint()), (
            "前提：光标确实拖到了另一块屏上——否则本用例测的就不是开关")
        assert len(shell.hosts()) == 1, "单窗：拖拽不会催生第二个窗口"
        assert [screen for screen, _r in shell._main_host.activity] == [primary], (
            "关闭跨屏 = 活动区只剩启动那块屏")
        assert main in overlay.sprites
        # 宠被本屏可用区钳住：身体框仍完整落在启动那块屏的可用区里
        body_center = QPointF(main.body_rect().center())
        assert primary.availableGeometry().contains(body_center.toPoint()), (
            "拖不过去 = 停在启动那块屏里，而不是被送到另一块屏")

        overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
        shell.save_position()
        profile = _profile(cfg.dir, "slot-0")
        assert 0.0 <= profile["rx"] <= 1.0 and 0.0 <= profile["ry"] <= 1.0, (
            "比例按启动那块屏的可用区落盘")
        assert profile.get("screen_name") is None, "拖不过去就没有新的屏归属"
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 3. 位置持久化语义
def _drag_on_current_screen(shell, sprite, *, dx=-30.0, dy=-20.0, steps=6):
    """在这只当前所在那块屏的窗里真鼠标拖一段（会话与宿主同一个窗）。"""
    overlay = shell.host_for_sprite(sprite).overlay
    origin = QPointF(overlay.geometry().topLeft())
    _hittable(sprite)
    hit = QPointF(sprite.rect().center())
    overlay.mousePressEvent(_press(hit, origin + hit))
    cursor = origin + hit
    for _ in range(steps):
        cursor = QPointF(cursor.x() + dx, cursor.y() + dy)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
    overlay.mouseReleaseEvent(_release(cursor - origin, cursor))
    return cursor


def test_off_still_persists_ordinary_drag_ratios(tmp_path, monkeypatch):
    """关闭跨屏 ≠ 禁写位置：单屏普通拖动照旧逐次落盘（既有口径不受影响）。"""
    primary, _secondary = two_screens()
    _install(monkeypatch, [primary, _secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    shell = _shell(cfg, primary)
    try:
        main = shell.sprite
        _drag_on_current_screen(shell, main)

        shell.save_position()
        profile = _profile(cfg.dir, "slot-0")
        body = main.body_rect()
        bounds = indep._activity_of(shell, primary)
        expected_rx = (body.x() + body.width() / 2.0 - bounds.left()) / bounds.width()
        assert profile["rx"] == pytest.approx(expected_rx, abs=0.01), (
            "拖到哪儿就记哪儿的比例——这是单屏普通拖动持久化")
    finally:
        _teardown(shell)


def test_off_writes_no_assignment_for_a_pet_without_memory(tmp_path, monkeypatch):
    """关闭跨屏下的首次落位**不记**归属：那时只是临时承载（比例照旧落盘）。"""
    primary, _secondary = two_screens()
    _install(monkeypatch, [primary, _secondary])
    cfg = _config(tmp_path, allow_cross_screen=False)
    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        slot = shell._spawned_slots[child]
        _drag_on_current_screen(shell, child)
        shell.save_position()
        shell.save_spawned_positions()

        profile = _profile(cfg.dir, f"slot-{slot}")
        assert profile.get("screen_name") is None, (
            "关闭态不盖归属：这一块屏只是临时承载，盖下去就吃掉真实归属")
        assert profile["rx"] is not None and profile["ry"] is not None, (
            "比例照旧逐只落盘（单屏普通拖动持久化不受开关影响）")
    finally:
        _teardown(shell)


def test_off_and_on_round_trip_returns_pets_to_their_own_screens(tmp_path, monkeypatch):
    """关掉再打开：屏归属与**归属那块屏上的比例**都要逐位保留。

    关闭跨屏期间这只被临时摆在启动那块屏上：在这块屏上拖动、收尾落盘都不许改
    档案里那份几何——只保住屏名等于没保住位置（重开后落在错的地方）。
    """
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=True)

    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        assert shell.move_sprite_to_screen(
            child, secondary, global_pos=QPointF(2500.0, 300.0)) is True
        slot = shell._spawned_slots[child]
        shell.save_spawned_positions()
        original = _profile(cfg.dir, f"slot-{slot}")
        assert original["screen_name"] == "DISPLAY2"
        ratio = (original["rx"], original["ry"])
    finally:
        _teardown(shell)

    # 关闭 → 重启：全部宠收到启动那块屏，但记忆不被机械动作改写
    cfg.set("allow_cross_screen", False)
    cfg.save()
    off_shell = _shell(cfg, primary)
    try:
        off_child = _child(off_shell)
        assert off_shell._screen_of_sprite(off_child) is primary
        assert len(off_shell.hosts()) == 1
        _drag_on_current_screen(off_shell, off_child)
        off_shell.save_position()
        off_shell.save_spawned_positions()
        kept = _profile(cfg.dir, f"slot-{slot}")
        assert kept["screen_name"] == "DISPLAY2", "屏归属不得被临时承载改写"
        assert (kept["rx"], kept["ry"]) == ratio, (
            "临时承载那块屏上的比例不得落盘——否则重开后回不到原位置")
    finally:
        _teardown(off_shell)

    # 重新开启 → 再重启：回到各自那块屏，且严格落在原比例上
    cfg.set("allow_cross_screen", True)
    cfg.save()
    on_shell = _shell(cfg, primary)
    try:
        back = _child(on_shell)
        assert on_shell._screen_of_sprite(back) is secondary
        assert len(on_shell.hosts()) == 1, "单窗：回到副屏也不多开窗口"
        body = back.body_rect()
        rect = indep._activity_of(on_shell, secondary)
        got = ((body.x() + body.width() / 2.0 - rect.left()) / rect.width(),
               (body.y() + body.height() / 2.0 - rect.top()) / rect.height())
        assert got == pytest.approx(ratio, abs=0.01), (
            "按原比例回到原来那块屏上的原位置")
        on_profile = _profile(cfg.dir, f"slot-{slot}")
        assert (on_profile["rx"], on_profile["ry"]) == ratio
    finally:
        _teardown(on_shell)


def test_child_without_memory_does_not_follow_the_main_pet(tmp_path, monkeypatch):
    """主宠单独拖到副屏后重启：从没换过屏的子宠留在自己那块屏（不跟着走）。

    子宠没有归属记录时的旧默认是"跟着主宠那块屏"——主宠单独去副屏，子宠也被带到
    副屏。首次落位即钉死归属之后，子宠留在它被生成的那块屏。
    """
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=True)
    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        slot = shell._spawned_slots[child]
        assert _screen_of(child) is primary
        assert shell.move_sprite_to_screen(shell.sprite, secondary) is True
        shell.save_position()
        shell.save_spawned_positions()

        assert _profile(cfg.dir, "slot-0")["screen_name"] == "DISPLAY2"
        assert _profile(cfg.dir, f"slot-{slot}")["screen_name"] == "DISPLAY1", (
            "子宠首次落位就要记下自己那块屏")
    finally:
        _teardown(shell)

    revived = _shell(cfg, primary)
    try:
        assert revived._screen_of_sprite(revived.sprite) is secondary, (
            "主宠按自己的记忆回副屏")
        assert revived._screen_of_sprite(_child(revived)) is primary, (
            "没换过屏的子宠不许跟着主宠跑到副屏")
    finally:
        _teardown(revived)


def test_a_temporarily_missing_screen_keeps_the_remembered_assignment(
        tmp_path, monkeypatch):
    """屏临时不在线与关闭跨屏共用同一条持久化语义：归属与比例都保留。

    拔屏后这只被并到活着的屏上，在那块屏上拖动（含贴边钳制）都不许改档案里
    那份几何；屏插回来仍回到它自己那块屏的原位置。
    """
    primary, secondary = two_screens()
    _install(monkeypatch, [primary, secondary])
    cfg = _config(tmp_path, allow_cross_screen=True)
    shell = _shell(cfg, primary)
    try:
        shell.spawn_pet()
        child = _child(shell)
        assert shell.move_sprite_to_screen(
            child, secondary, global_pos=QPointF(2500.0, 300.0)) is True
        slot = shell._spawned_slots[child]
        shell.save_spawned_positions()
        original = _profile(cfg.dir, f"slot-{slot}")
        assert original["screen_name"] == "DISPLAY2"
        ratio = (original["rx"], original["ry"])

        shell.handle_screen_removed(secondary)

        assert shell._screen_of_sprite(child) is primary, "拔屏后先落在活着的屏上"
        # 贴边拖（钳制把这只按在可用区边缘）+ 收尾落盘：都不许写档案
        _drag_on_current_screen(shell, child, dx=60.0, dy=0.0, steps=10)
        shell.save_spawned_positions()
        kept = _profile(cfg.dir, f"slot-{slot}")
        assert kept["screen_name"] == "DISPLAY2", (
            "临时缺屏是机械搬迁：不得把归属改成当前这块屏")
        assert (kept["rx"], kept["ry"]) == ratio, (
            "缺屏期间钳制后的位置不得落盘——屏插回来要回原位置")
    finally:
        _teardown(shell)

    revived = _shell(cfg, primary)
    try:
        back = _child(revived)
        assert revived._screen_of_sprite(back) is secondary, "屏插回来，这只仍回到它自己那块屏"
        body = back.body_rect()
        rect = indep._activity_of(revived, secondary)
        got = ((body.x() + body.width() / 2.0 - rect.left()) / rect.width(),
               (body.y() + body.height() / 2.0 - rect.top()) / rect.height())
        assert got == pytest.approx(ratio, abs=0.01), "回到原来那块屏上的原位置"
    finally:
        _teardown(revived)
