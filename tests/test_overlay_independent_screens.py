# -*- coding: utf-8 -*-
"""每只宠物独立放任意屏：**一张**合成窗覆盖整个虚拟桌面、逐只屏归属。

用户口径：**每只宠物可以独立放在任意一块屏上**，而全进程只有**一张**合成窗
（窗口 = 各屏可用工作区的并集包围矩形；进程、tick 时钟与共享服务仍只有一份）。
本次立的是"逐只屏归属"这个合同，两条公开接缝：

- ``shell.host_for_sprite(sprite)`` → 这只宠的宿主（单窗下恒为唯一宿主：
  ``.overlay`` 是整桌面那张窗，``.activity`` 是各屏可用区的矩形集合）；
- ``shell.move_sprite_to_screen(sprite, screen, global_pos=None)`` → 把**这一只**
  放到那块屏（``global_pos`` = 目标全局左上角；不给则按"它在当前屏可用区里的比例"
  落到目标屏的同一相对位置）。跨屏不再是换窗口，只是同一张窗内的移动。

本文件只测最关键的那几条合同与反例：

1. 搬走一只，另一只的位置/边界一个字节不动；
2. 一张窗、活动区 = 各屏可用区矩形集合（关跨屏时只剩家那块屏）；
3. 同一屏内局部坐标相同 = 接触；各自屏内局部坐标相同（错开一整块屏）= 零碰撞；
4. 主屏切换不搬任何宠；
5. 拔屏只迁这块屏上的宠，别的屏一个像素不动；
6. 一次 tick 每只各推进一次（一张窗、一个 timer）；
7. 换屏不是"退出这只宠"：不广播移除（不注销行为/探头/彩蛋状态）、不释放 clip；
8. ``screen_name`` 与 ``rx/ry`` 逐只独立落盘（比例是相对**它自己那块屏**算的）、
   逐只独立恢复；
9. 带落点的迁移把这只放到指定的全局左上角。

纪律：offscreen；同步直调 handler / ``driver.on_tick`` / ``driver.tick_sim``，
不 sleep、不赌目录枚举；真壳 + 真 ``PetSprite`` + 假屏（真 QTimer 的节拍断言看
``FrameSeqClip._timer``）。假屏不是 ``QScreen``，屏事件走产品自己的 handler 入参
口径（``handle_screen_removed`` / ``handle_primary_screen_changed``）——不能经
``QGuiApplication`` 的真信号；产品按 ``screen_name`` 找回屏时唯一看得到屏的地方
是 Qt 的屏枚举（``app.screens()`` / ``QGuiApplication.screens()``），故
``two_screens`` 把两处枚举都换成这两块假屏。

产品接缝未就绪时本文件整片红（缺 ``host_for_sprite`` / ``move_sprite_to_screen``
时是 AttributeError），红就是缺实现的记录，不靠改断言掩盖。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import tests.test_sprite_menu_facade as fac
from pet.config import Config
from pet.frameseq_clip import FrameSeqClip
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from pet.tick_driver import TIER_ACTIVE
from tests.test_frameseq_clip import _make_frames
from tests.test_overlay_geometry_parity import _place_center, _ratio_of
from tests.test_overlay_screen_migration import FakeScreen

app = QApplication.instance() or QApplication([])

PRIMARY_GEO = (0, 0, 1920, 1080)
PRIMARY_AVAIL = (0, 0, 1920, 1040)
SECONDARY_GEO = (1920, 0, 1600, 900)
SECONDARY_AVAIL = (1920, 0, 1600, 860)


@pytest.fixture(autouse=True)
def two_screens(monkeypatch):
    """一块主屏（1920×1080）+ 一块右屏（1600×900）：本文件全部用例都在这台机器上跑。

    假屏不在 Qt 的屏枚举里，而产品按 ``screen_name`` 找回屏、按全局坐标判"落在
    哪块屏"都读它——故把 ``screens()`` / ``primaryScreen()`` 换成假屏，桌面上就
    真的只有这两块屏，没有第三块真屏漏进来。

    **只补类级的 ``QGuiApplication``**（产品读的就是这两条类级静态方法）：那两条
    是类级静态方法，产品读的是类；补实例属性（``app.screens``）会在 PySide6 对象
    上留下一个影子实例属性，``monkeypatch`` 撤回时写回的是"已被补成假屏的值"，
    于是补丁泄漏到下一条用例——本文件里表现为后续用例莫名多出两块屏。
    """
    primary = FakeScreen(PRIMARY_GEO, PRIMARY_AVAIL, name="DISPLAY1")
    secondary = FakeScreen(SECONDARY_GEO, SECONDARY_AVAIL, name="DISPLAY2")
    screens = [primary, secondary]
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: list(screens)))
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: screens[0]))
    return primary, secondary


class _Instance:
    """真壳最小宿主：只提供 per-pet 库工厂（每只宠一份 RichLibrary）。"""

    def __init__(self, config):
        self.config = config

    def _create_library(self, _character_id):
        return fac.RichLibrary()


def _make_shell(tmp_path, screen, **values):
    """**真落盘 Config** + 真 PetSprite 的壳：子宠走真 slot 档案，几何能落盘。

    与 ``tests.test_overlay_screen_migration._make_shell`` 同形，只把库工厂换成
    ``RichLibrary``——那条夹具的 ``CapLibrary`` 没有 ``movie()``，子宠一 tick 就炸。
    本文件整体测"允许跨屏"下的合同，故这台"机器"上把开关显式打开（全局键、重启级
    进程快照）；不打开的话壳体只在启动那块屏建窗，多屏断言无从成立。
    """
    cfg = Config(base=tmp_path)
    cfg.set("allow_cross_screen", True)
    for key, value in values.items():
        cfg.set(key, value)
    cfg.save()
    shell = OverlayShell(
        app, _Instance(cfg), screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    return shell, cfg


def _pets(shell):
    """进程内全部宠物 = 驱动器聚合（每只有宠的屏一张窗，合一即全部）。"""
    return list(shell.driver.sprites)


def _child(shell):
    """唯一那只子宠（主宠之外的）。"""
    others = [sprite for sprite in _pets(shell) if sprite is not shell.sprite]
    assert len(others) == 1, f"前提：这壳上应当只有一只子宠，实得 {len(others)}"
    return others[0]


def _activity_of(shell, screen) -> QRect:
    """该屏在活动区里的矩形（窗口局部坐标）。"""
    for candidate, rect in shell._main_host.activity:
        if candidate is screen:
            return QRect(rect)
    raise AssertionError(f"{screen.name()} 不在活动区里")


def _screen_of(shell, sprite):
    """这只宠当前所在的屏（产品按身体中心判定，与落盘的分母同源）。"""
    return shell._screen_of_sprite(sprite)


def _ratio_of_pet(shell, sprite) -> tuple[float, float]:
    """这只宠身体中心相对**它那块屏**可用区的比例（= 落盘口径）。"""
    return _ratio_of(sprite.body_rect(), _activity_of(shell, _screen_of(shell, sprite)))


def _profile(config_dir, instance_id):
    """按实例身份读这只宠的档案（产品命名规则 ``config-<实例 id>.json``）。"""
    path = config_dir / f"config-{instance_id}.json"
    assert path.exists(), f"前提：这只宠的档案应当已落盘：{path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _host_worlds(shell, pets):
    """这些宠各自宿主上的碰撞世界（一屏一份，按宿主去重）。

    每块屏一个仿真世界正是"不同屏同局部坐标不算接触"的物质基础，故断言必须打在
    **各宿主自己的**世界上（``shell.collision`` 只是主宿主那一份）。
    """
    worlds = []
    for pet in pets:
        world = shell.host_for_sprite(pet).collision
        if all(world is not other for other in worlds):
            worlds.append(world)
    return worlds


def _park_overlapping(shell, pets, left, right):
    """把两只摆成同一组 local 坐标上的迎面重叠（口径同 test_overlay_collision_toggle）。

    位置是测试直接摆放的瞬移，故清空帧间扫掠快照（``_prev_circles``）——否则"瞬移
    到重叠位"会被当成高速掠过；本用例要测的是同一时刻的重叠判定。
    """
    left.set_pos(QPointF(300.0, 300.0))
    left.set_velocity(QPointF(200.0, 0.0))
    left.interaction_state = "normal"
    right.set_pos(QPointF(300.0 + left.rect().width() - 20.0, 300.0))
    right.set_velocity(QPointF(-200.0, 0.0))
    right.interaction_state = "normal"
    for world in _host_worlds(shell, pets):
        world._prev_circles = {}


def _impact_ticks(shell, pets, left, right, times=2):
    """摆一次迎面重叠 → 经真 tick 入口推一次，重复 ``times`` 次；返回收到的事件。

    每次 tick 前都要重摆：行为链在**状态表新建的那一 tick**会把速度写回 0（它是
    仿真段的第一步），不重摆就没有迎面速度，重叠只按位置分离结算、不产生"真撞"
    事件。重摆后每只都带着迎面速度进碰撞步，事件判据才真的被激励——本文件的
    "同屏必撞"对照组与"跨屏不撞"都被这一条口径同时约束（前者有事件、后者无）。
    """
    events = []
    for world in _host_worlds(shell, pets):
        world.add_collision_listener(events.append)
    for _ in range(times):
        _park_overlapping(shell, pets, left, right)
        shell.driver.on_tick(0.016)
    return events


def _count_advances(sprites):
    """给每只 sprite 的 ``advance`` 套一层计数（真推进照走，只数次数）。"""
    counts = {sprite: 0 for sprite in sprites}
    for sprite in sprites:
        original = sprite.advance

        def counted(dt, _sprite=sprite, _original=original):
            counts[_sprite] += 1
            return _original(dt)

        sprite.advance = counted
    return counts


def _teardown(shell, clip=None):
    """逐壳收口：停壳 → 关掉合成窗 → 删 runtime 标记。"""
    shell.stop()
    for overlay in list(shell.driver.overlays):
        overlay.close()
    if clip is not None:
        clip.close()
    shell._delete_runtime_marker()


# ---------------------------------------------------------------- 1. 一次只动一只
def test_moving_one_pet_leaves_the_other_pet_untouched(tmp_path, two_screens):
    """两屏各一只：把子宠搬到右屏，主宠的位置/边界一个字节不动。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)

        overlay = shell.overlay
        main_pos = QPointF(main.pos)
        main_rect = QRect(main.rect())

        shell.move_sprite_to_screen(child, secondary)

        assert _screen_of(shell, child) is secondary
        assert _activity_of(shell, secondary).contains(child.body_rect().center())
        assert shell.host_for_sprite(child).overlay is overlay, "单窗：两屏共用同一张窗"
        assert child in overlay.sprites
        assert child.home_screen is secondary, "屏归属要写进这只宠自己的字段（落盘取它）"

        assert _screen_of(shell, main) is primary
        assert QPointF(main.pos) == main_pos, "另一只的位置一个像素都不许动"
        assert QRect(main.rect()) == main_rect, "另一只的矩形（含钳制域）都不许动"
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 2. 每个有宠屏一张窗
def test_one_window_with_activity_rects_per_screen(tmp_path, two_screens):
    """单窗：窗口 = 各屏可用区并集，活动区 = 每屏一块；关跨屏后只剩家那块。

    #302 的修法就在这里：窗口矩形取**可用工作区**的并集（不含任务栏），否则按
    窗口矩形自判全屏的第三方软件会把桌宠当全屏应用。
    """
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        shell.spawn_pet()
        child = _child(shell)
        shell.start()
        assert shell.driver.applied_tier == TIER_ACTIVE, "前提：可见态才跑仿真段"
        overlay = shell.overlay
        assert overlay.isVisible() is True

        assert shell.hosts() == [shell._main_host], "全进程只有一张合成窗"
        assert len(shell.driver.overlays) == 1
        assert overlay.geometry() == QRect(0, 0, 3520, 1040), "两屏可用区并集"
        assert [screen for screen, _r in shell._main_host.activity] == [
            primary, secondary]
        assert _activity_of(shell, secondary) == QRect(1920, 0, 1600, 860)

        shell.move_sprite_to_screen(child, secondary)
        assert overlay.isVisible() is True, "搬到另一块屏的宠就在这张窗里，不会被藏起来"

        # 关跨屏 = 活动区只留家那块屏（窗仍铺满全桌面，宠出不去）
        shell._cross_screen_allowed = False
        shell.handle_geometry_changed()
        assert [screen for screen, _r in shell._main_host.activity] == [primary]
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 3. 一次 tick 一只一次
def test_one_tick_advances_each_pet_once_on_a_single_timer(tmp_path, two_screens):
    """两屏各一只：一次 tick 每只各推进一次，两屏共用同一个 timer（不双重推进）。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.move_sprite_to_screen(child, secondary)
        shell.start()
        assert shell.driver.applied_tier == TIER_ACTIVE, "前提：可见态才跑仿真段"

        assert shell.host_for_sprite(main) is shell.host_for_sprite(child)
        assert shell.overlay.tick_driver is shell.driver, "合成窗挂唯一驱动器"
        assert shell.overlay._timer is shell.driver.timer, "不许第二张表"
        assert shell.overlay in shell.driver.overlays

        counts = _count_advances([main, child])
        shell.driver.on_tick(0.016)

        assert counts == {main: 1, child: 1}, (
            "一次 tick 每只恰好推进一次：0 = 漏推进（那只所在屏没被驱动），"
            "2 = 同一只被两张窗各推一次")
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 4. 同 local 坐标零碰撞
def test_same_local_position_on_one_screen_still_collides(tmp_path, two_screens):
    """对照（有牙）：两只在同一块屏的同一组 local 坐标上迎面重叠 → 必须结算。"""
    primary, _secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.start()
        assert shell.driver.applied_tier == TIER_ACTIVE, "前提：可见态才跑仿真段"

        assert _impact_ticks(shell, [main, child], main, child) != [], (
            "前提：同屏同一组 local 坐标上迎面重叠必须撞（否则下面那条跨屏用例是空断言）")
    finally:
        _teardown(shell)


def test_same_position_within_each_screen_does_not_collide(tmp_path, two_screens):
    """两只各自站在**自己那块屏**的同一屏内坐标上：零碰撞（真机上相距一整块屏）。

    单窗把全部宠放进同一个世界，局部坐标就是窗口全局坐标，所以这条合同变成了
    "相隔一整块屏的两只不许被判成接触"——与 #303 的反面同源：真正要紧的是它们
    在同一套坐标里算距离，而不是按屏各算一份。
    """
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.start()
        assert shell.driver.applied_tier == TIER_ACTIVE, "前提：可见态才跑仿真段"

        shell.move_sprite_to_screen(child, secondary)
        assert _screen_of(shell, child) is secondary, "前提：子宠已在另一块屏"

        events = []
        for world in _host_worlds(shell, [main, child]):
            world.add_collision_listener(events.append)
        for _ in range(2):
            # 各自屏内的同一相对位置 + 迎面速度（行为链会在状态表新建那一拍把
            # 速度写回 0，故每拍重摆，口径同 ``_impact_ticks``）
            main.set_pos(QPointF(_activity_of(shell, primary).topLeft())
                         + QPointF(300.0, 300.0))
            main.set_velocity(QPointF(200.0, 0.0))
            child.set_pos(QPointF(_activity_of(shell, secondary).topLeft())
                          + QPointF(300.0, 300.0))
            child.set_velocity(QPointF(-200.0, 0.0))
            assert abs(main.pos.x() - child.pos.x()) > 1000.0, "前提：两只相隔一整块屏"
            for world in _host_worlds(shell, [main, child]):
                world._prev_circles = {}
            shell.driver.on_tick(0.016)

        assert events == [], "相隔一整块屏的两只不许被判成接触（世界是窗口全局坐标）"
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 5. 主屏切换不搬
def test_primary_screen_change_does_not_move_any_pet(tmp_path, two_screens, monkeypatch):
    """主屏换成右屏：每只宠留在自己那块屏上，屏/宿主/位置都不动。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.move_sprite_to_screen(child, secondary)
        main_pos, child_pos = QPointF(main.pos), QPointF(child.pos)
        home = shell._screen
        bounds = QRect(shell._bounds)
        monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: secondary))
        monkeypatch.setattr(app, "primaryScreen", lambda: secondary)

        shell.handle_primary_screen_changed(secondary)

        assert _screen_of(shell, main) is primary, "主屏换人不搬家"
        assert _screen_of(shell, child) is secondary
        assert shell._screen is home, "家那块屏也不因主屏换人而改写"
        assert QRect(shell._bounds) == bounds
        assert QPointF(main.pos) == main_pos
        assert QPointF(child.pos) == child_pos
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 6. 拔屏只迁该屏
def test_unplugging_a_screen_moves_only_the_pets_on_it(tmp_path, two_screens):
    """拔掉右屏：只有右屏上那只迁走，左屏上的宠一个像素不动。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.move_sprite_to_screen(child, secondary)

        main_pos, main_rect = QPointF(main.pos), QRect(main.rect())

        shell.handle_screen_removed(secondary)

        assert shell.host_for_screen(secondary) is None, "拔掉的屏不再被窗覆盖"
        assert _screen_of(shell, main) is primary, "别的屏上的宠不许跟着迁"
        assert QPointF(main.pos) == main_pos and QRect(main.rect()) == main_rect

        assert _screen_of(shell, child) is primary, "被拔屏上的宠必须迁走"
        assert _activity_of(shell, primary).contains(child.body_rect().center()), (
            "迁到活着的那块屏后必须完整落在可用区内")
        assert shell.hosts() == [shell._main_host], "单窗：拔屏不多出/不少宿主"
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- 7. 迁移不是退出
def test_transfer_keeps_clip_playing_and_never_broadcasts_removal(tmp_path, two_screens):
    """搬到另一屏 = 换个宿主，不是"移除再新建"：不广播移除、不释放 clip、仍被驱动。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    frames_dir = tmp_path / "idle1"
    _make_frames(frames_dir)
    clip = FrameSeqClip(frames_dir)
    try:
        main = shell.sprite
        main.library._clips["idle1"] = clip          # 真 clip 替掉假库里的占位
        shell.driver.tick_sim(0.016)                 # 让行为控制器为这只建状态表条目
        assert main in shell.host_for_sprite(main).behavior._states, "前提：状态条目已建"
        assert main.bind_clip("idle1") is True
        assert main._clip is clip and clip._timer.isActive() is True, "前提：真节拍在跑"

        removed = []
        shell.overlay.add_sprite_removed_listener(removed.append)

        shell.move_sprite_to_screen(main, secondary)

        assert removed == [], (
            "换屏不得广播 sprite 移除（那正是行为/探头/彩蛋状态的注销链）")
        assert _screen_of(shell, main) is secondary
        assert main._clip is clip, "换屏不得释放这只的 clip（换屏不等于换动画）"
        assert clip._timer.isActive() is True, "换屏后播放节拍必须还在跑"

        shell.driver.tick_sim(0.016)
        assert main in shell.host_for_sprite(main).behavior._states, (
            "换屏后这只仍要由行为链驱动")
    finally:
        _teardown(shell, clip)


# ---------------------------------------------------------------- 8. 逐只屏记忆
def test_each_pet_persists_its_own_screen_and_ratio(tmp_path, two_screens):
    """落盘：逐只记下自己的 ``screen_name`` 与比例（比例相对**它自己那块屏**算）。

    ``screen_name`` 是**屏归属**：首次落位即钉死（没被换过屏的宠也要有归属，否则
    重启时子宠只能跟着主宠走），之后只由"有意把它拖到另一块屏"改写。位置自存
    （退出/拖动收尾）在承载屏 == 归属时照旧写比例，两者不一致（临时承载）时一个
    几何键都不写——见 ``test_overlay_cross_screen_switch`` 的 round trip。
    """
    primary, secondary = two_screens
    shell, cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = shell.sprite
        shell.spawn_pet()
        child = _child(shell)
        shell.move_sprite_to_screen(child, secondary)
        expected_ratio = _ratio_of_pet(shell, child)

        shell.save_position()            # 主宠（slot-0）
        shell.save_spawned_positions()   # 子宠各写自己那份

        assert shell.sprite_instance_id(child) != shell.sprite_instance_id(main), (
            "前提：两只宠各有各的档案")
        main_profile = _profile(cfg.dir, shell.sprite_instance_id(main))
        child_profile = _profile(cfg.dir, shell.sprite_instance_id(child))
        assert main_profile["screen_name"] == "DISPLAY1", (
            "没换过屏的那只也在首次落位就记下自己的屏（不记就会跟着别的宠跑）")
        assert main_profile["rx"] is not None and main_profile["ry"] is not None, (
            "比例照旧逐只更新（单屏普通拖动持久化靠它）")
        assert child_profile["screen_name"] == "DISPLAY2", "子宠记的是它自己那块屏"
        assert (child_profile["rx"], child_profile["ry"]) == pytest.approx(
            expected_ratio, abs=0.01), (
            "比例必须相对它自己那块屏的可用区算——否则恢复时落点整体偏掉")
    finally:
        _teardown(shell)


def test_restored_pets_land_on_their_own_saved_screen(tmp_path, two_screens):
    """重启复活：每只按它自己档案里的 ``screen_name`` 回到各自那块屏。"""
    primary, secondary = two_screens
    shell, cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        shell.spawn_pet()
        child = _child(shell)
        shell.move_sprite_to_screen(child, secondary)
        shell.save_position()
        shell.save_spawned_positions()
        assert _profile(cfg.dir, shell.sprite_instance_id(child))["screen_name"] == "DISPLAY2"
    finally:
        _teardown(shell)

    revived, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        main = revived.sprite
        child = _child(revived)
        assert _screen_of(revived, main) is primary, "主宠按自己那份回主屏"
        assert _screen_of(revived, child) is secondary, (
            "子宠按自己那份回右屏——屏归属是逐只的，不是全局一份")
        assert revived.hosts() == [revived._main_host], "单窗：复活也不多出宿主"
    finally:
        _teardown(revived)


# ---------------------------------------------------------------- 9. 带落点的迁移
def test_move_with_drop_point_lands_the_pet_on_the_pointer(tmp_path, two_screens):
    """带落点的迁移（拖动跨屏）：这只落到指定的全局左上角，而不是只换坐标系。"""
    primary, secondary = two_screens
    shell, _cfg = _make_shell(tmp_path, primary, collision_enabled=True)
    try:
        shell.spawn_pet()
        child = _child(shell)
        _place_center(child, 0.15 * PRIMARY_AVAIL[2], 0.5 * PRIMARY_AVAIL[3])
        keep_global_local = QPointF(child.pos) - QPointF(
            QPoint(secondary.geometry().x(), secondary.geometry().y()))
        drop = QPoint(2720, 450)
        origin = shell.overlay.geometry().topLeft()
        drop_local = QPointF(drop.x() - origin.x(), drop.y() - origin.y())
        assert abs(drop_local.x() - keep_global_local.x()) > child.rect().width(), (
            "前提：落点与「只换坐标系」的落点相距超过一个宠身，本断言才有牙")

        shell.move_sprite_to_screen(child, secondary, global_pos=drop)

        assert _screen_of(shell, child) is secondary
        assert (child.pos.x(), child.pos.y()) == pytest.approx(
            (drop_local.x(), drop_local.y()), abs=1.0), (
            "给了落点就把这只放到该全局左上角（拖到哪儿落哪儿，不按旧屏坐标平移）")
        assert _activity_of(shell, secondary).contains(child.body_rect().center())
    finally:
        _teardown(shell)
