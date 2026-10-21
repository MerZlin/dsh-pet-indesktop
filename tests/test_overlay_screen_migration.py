# -*- coding: utf-8 -*-
"""屏迁移两缺陷回归：可见性/clip 暂停（缺陷 24）+ 比例位置被旧边界截断（缺陷 25）。

**缺陷 24**（实机探针：迁移后 ``overlay.isVisible()=True`` 而
``sprite.clip_paused=True`` —— 宠物冻在首帧、行为照常推着它动）：
``_migrate_to_screen`` 对 ``was_started`` 的壳无条件 ``show()``，用户隐藏
（托盘 ``set_pet_visible(False)`` / 全屏避让 ``_auto_hidden``）中的窗口被迁移
重新显示出来；而这条路径不恢复 clip 暂停，隐藏期被压住的播放节拍留在暂停。
契约：迁移前不可见 → 不 show、不动 clip 暂停（等真正的显示路径放行）；
迁移前可见 → show + 续播节拍。

**缺陷 25**：几何变化的迁移必须先在旧边界上取比例（先换边界时
``PetSprite.set_bounds`` 会就地钳一次），再在新边界下落点（``set_pos`` 受当前
域钳制）：从小屏/低分迁到大屏/高分时，靠右/靠下的宠物若先被旧边界截断，扩边界
后不会恢复（实测宽 1000→1600：期望中心 x≈1358，实得旧边界钳出的 744+128）。
契约：比例取自旧矩形，落点写在新矩形下（``_geometry_plan`` → ``_place_body_ratio``）。

纪律：offscreen；真壳 + 真 PetSprite + 真 FrameSeqClip（节拍断言看真实 QTimer
是否激活）；同步直调 handler / 真信号推进，不 sleep。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, QRect, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet.frameseq_clip import FrameSeqClip
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from tests.test_frameseq_clip import _make_frames
from tests.test_overlay_geometry_parity import _place_center, _ratio_of

app = QApplication.instance() or QApplication([])


class FakeScreen(QObject):
    """假屏：显式 set_rects + 真实 Qt 信号（走产品真接线，不直调 handler）。"""

    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, geo, avail, *, dpr=1.0, refresh=60.0, name="mig-screen"):
        super().__init__()
        self._geo = QRect(*geo)
        self._avail = QRect(*avail)
        self._dpr = float(dpr)
        self._refresh = float(refresh)
        self._name = name

    def set_rects(self, geo, avail, *, dpr=None):
        self._geo = QRect(*geo)
        self._avail = QRect(*avail)
        if dpr is not None:
            self._dpr = float(dpr)

    def geometry(self):
        return QRect(self._geo)

    def availableGeometry(self):
        return QRect(self._avail)

    def devicePixelRatio(self):
        return self._dpr

    def refreshRate(self):
        return self._refresh

    def name(self):
        return self._name


def _make_shell(tmp_path, screen, values=None):
    config = cap.CapConfig(tmp_path, values)
    from pet import pet_profiles

    pet_profiles.write_profile_settings(tmp_path, 0, values or {"scale": 0.5})
    shell = OverlayShell(
        app, cap.CapInstance(config), screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    lib = fac.RichLibrary()
    shell.lib = lib
    shell.sprite.library = lib
    return shell


def _make_clip_shell(tmp_path, screen=None):
    """主宠绑**真** FrameSeqClip 的壳：播放节拍断言看真实 QTimer，不看替身标记。

    隐藏/挂起的暂停链路（``sprite.pause_clip`` → ``clip.pause`` → 停表）只有真
    clip 才能观测；替身 FakeClip 的 ``pause`` 是空实现，暂停态可由 ``clip_paused``
    自证，但"节拍真的在跑"必须看真定时器。
    """
    shell = _make_shell(tmp_path, screen or FakeScreen((0, 0, 1000, 600),
                                                       (0, 0, 1000, 600)))
    frames_dir = tmp_path / "idle1"
    _make_frames(frames_dir)
    clip = FrameSeqClip(frames_dir)
    shell.lib._clips["idle1"] = clip
    assert shell.sprite.bind_clip("idle1") is True, "前提：真 clip 起播被接受"
    assert clip._timer.isActive() is True, "前提：起播后播放节拍在跑"
    return shell, clip


def _migrate(shell):
    """按真实入口把"家"迁到主屏（fake screen → primaryScreen）。

    单合成窗覆盖整个虚拟桌面：换家**不重建窗口**（HWND、气泡、岛墙、在途会话
    都留在原地），故这里的前提断言是"窗口还是同一张"。
    """
    primary = QGuiApplication.primaryScreen()
    old = shell.overlay
    shell._migrate_to_screen(primary)
    assert shell.overlay is old, "前提：换家不重建合成窗"
    assert shell._screen is primary
    return old


def _teardown(shell, clip=None):
    shell.stop()
    shell.overlay.close()
    if clip is not None:
        clip.close()
    shell._delete_runtime_marker()


# ---------------------------------------------------------------- 缺陷 24：隐藏中迁移
def test_migrate_while_hidden_keeps_window_hidden_and_clips_paused(tmp_path):
    """托盘隐藏中迁移：不得 show、不得解除 clip 暂停（宠物不冻着乱跑）。"""
    shell, clip = _make_clip_shell(tmp_path)
    try:
        shell.start()
        assert shell.overlay.isVisible() is True
        shell.set_pet_visible(False)              # 托盘隐藏：整窗 hide + 逐只 pause
        assert shell.overlay.isVisible() is False
        assert shell.sprite.clip_paused is True
        assert clip._timer.isActive() is False

        _migrate(shell)

        assert shell.overlay.isVisible() is False, "隐藏中迁移不得把窗口显示出来"
        assert shell.sprite.clip_paused is True, "隐藏中迁移不得解除 clip 暂停"
        assert clip._timer.isActive() is False, "隐藏期零帧推进：迁移后也不得起表"
        # 窗口不重建，但驱动器仍必须驱动它（隐藏期的档位/锁屏探测都挂在它上面）。
        assert shell.overlay in shell.driver._overlays, "窗口必须仍被驱动器驱动"
        assert shell.driver.timer.isActive() is True, "迁移不得让 tick 时钟停摆"
    finally:
        _teardown(shell, clip)


def test_showing_after_hidden_migration_resumes_clip_cadence(tmp_path):
    """隐藏迁移只"顺延放行"：真正的显示路径（``set_pet_visible(True)``）恢复节拍。"""
    shell, clip = _make_clip_shell(tmp_path)
    try:
        shell.start()
        shell.set_pet_visible(False)
        _migrate(shell)

        shell.set_pet_visible(True)

        assert shell.overlay.isVisible() is True
        assert shell.sprite.clip_paused is False
        assert clip._timer.isActive() is True, "显示路径必须把迁移期压住的节拍放行"
    finally:
        _teardown(shell, clip)


# ---------------------------------------------------------------- 缺陷 24：全屏避让中迁移
def test_migrate_while_auto_hidden_does_not_show_window(tmp_path):
    """全屏避让（``_auto_hidden``）中迁移：不 show、clip 保持暂停。"""
    shell, clip = _make_clip_shell(tmp_path)
    try:
        shell.start()
        shell._on_fullscreen_changed(True)        # 全屏出现 → 自动隐藏 + 逐只 pause
        assert shell._auto_hidden is True
        assert shell.overlay.isVisible() is False

        _migrate(shell)

        assert shell._auto_hidden is True, "迁移不改全屏避让状态（仍由 watcher 重评）"
        assert shell.overlay.isVisible() is False, "全屏避让中迁移不得 show"
        assert shell.sprite.clip_paused is True
        assert clip._timer.isActive() is False
    finally:
        _teardown(shell, clip)


# ---------------------------------------------------------------- 缺陷 24：可见中迁移
def test_migrate_while_visible_keeps_window_shown_and_clips_running(tmp_path):
    """可见中迁移：窗口保持显示且播放节拍仍在跑（不得被闸门误压成暂停）。"""
    shell, clip = _make_clip_shell(tmp_path)
    try:
        shell.start()
        assert shell.overlay.isVisible() is True
        assert shell.sprite.clip_paused is False

        _migrate(shell)

        assert shell.overlay.isVisible() is True
        assert shell.sprite.clip_paused is False, "可见迁移必须保持续播"
        assert shell.sprite._clip is clip, "前提：迁移不换绑 clip"
        assert clip._timer.isActive() is True
    finally:
        _teardown(shell, clip)


# ---------------------------------------------------------------- 缺陷 25：小屏 → 大屏
def test_growing_bounds_keep_right_bottom_ratio(tmp_path):
    """可用区变大（同屏几何变化）：靠右/靠下的宠物比例位置不得被旧边界截断。

    旧序（比例 → ``set_pos`` 受旧边界钳 → 再换边界）下，本夹具的落点是旧边界
    钳出的左上 (744, 456)；正确落点 (1230, 646) 只在**新**边界下合法。
    """
    screen = FakeScreen((0, 0, 1000, 600), (0, 0, 1000, 600))
    shell = _make_shell(tmp_path, screen, {"scale": 0.4})
    try:
        old_bounds = QRect(shell._bounds)
        sprite = shell.sprite
        placed = _place_center(sprite,
                               old_bounds.x() + 0.85 * old_bounds.width(),
                               old_bounds.y() + 0.8 * old_bounds.height())
        rx, ry = _ratio_of(placed, old_bounds)
        assert 0.8 < rx < 0.9, "夹具前提：靠右但未贴边（旧边界内合法）"
        assert 0.75 < ry < 0.85, "夹具前提：靠下但未贴边（旧边界内合法）"

        screen.set_rects((0, 0, 1600, 900), (0, 0, 1600, 900))
        screen.geometryChanged.emit(screen.geometry())   # 真信号接线

        new_bounds = QRect(shell._bounds)
        assert new_bounds == QRect(0, 0, 1600, 900)
        rect = sprite.rect()
        assert rect.center().x() == pytest.approx(
            new_bounds.x() + rx * new_bounds.width(), abs=2.0), "比例位置必须在新边界下落地"
        assert rect.center().y() == pytest.approx(
            new_bounds.y() + ry * new_bounds.height(), abs=2.0)
        # 反例锚点：先受旧边界钳制会把落点钉在 old_w - rect.w(=744) 附近
        assert rect.left() > old_bounds.width() - rect.width(), "不得停在旧边界钳出的位置"
    finally:
        shell._delete_runtime_marker()


@pytest.mark.usefixtures("non_macos_overlay_topology")
def test_migrate_home_to_another_screen_keeps_every_pet_in_place(tmp_path, monkeypatch):
    """换家（``_migrate_to_screen``）不搬宠：窗口是两屏可用区并集、宠的局部位置不动。

    单窗覆盖整个虚拟桌面，"换家"只换关闭跨屏时的那块活动区与屏拔出时的兜底判据；
    窗口矩形与宠的位置都不该因此跳一下（旧实现的"重建窗口 + 按比例重摆"在新拓扑
    下没有对应物）。
    """
    small = FakeScreen((0, 0, 1000, 600), (0, 0, 1000, 600), name="small")
    big = FakeScreen((1000, 0, 1600, 900), (1000, 0, 1600, 900), name="big")
    monkeypatch.setattr(QGuiApplication, "screens",
                        staticmethod(lambda: [small, big]))
    shell = _make_shell(tmp_path, small, {"scale": 0.4, "allow_cross_screen": True})
    try:
        sprite = shell.sprite
        pos_before = QPointF(sprite.pos)

        shell._migrate_to_screen(big)

        assert shell._screen is big
        assert shell._bounds == QRect(0, 0, 2600, 900), "窗口 = 两屏可用区并集"
        assert sprite.pos == pos_before, "换家不搬宠"
        assert [screen for screen, _rect in shell._main_host.activity] == [small, big]
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 缺陷 25：大屏 → 小屏
def test_shrinking_bounds_still_clamp_into_new_bounds(tmp_path):
    """大屏 → 小屏：比例落点越界时仍按**新**边界钳制（钳制不因换序被跳过）。"""
    screen = FakeScreen((0, 0, 1600, 900), (0, 0, 1600, 900))
    shell = _make_shell(tmp_path, screen, {"scale": 0.4})
    try:
        old_bounds = QRect(shell._bounds)
        sprite = shell.sprite
        placed = _place_center(sprite,
                               old_bounds.x() + 0.92 * old_bounds.width(),
                               old_bounds.y() + 0.9 * old_bounds.height())
        rx, ry = _ratio_of(placed, old_bounds)

        screen.set_rects((0, 0, 1000, 600), (0, 0, 1000, 600))
        screen.availableGeometryChanged.emit(screen.availableGeometry())

        new_bounds = QRect(shell._bounds)
        assert new_bounds == QRect(0, 0, 1000, 600)
        rect = sprite.rect()
        assert rect.right() <= new_bounds.right(), "身体必须完整落在新可用区内"
        assert rect.bottom() <= new_bounds.bottom()
        # 比例理想值越界 → 钳到新右/下界（与 PetSprite._clamp_axis 同口径）
        assert rect.left() == pytest.approx(new_bounds.width() - rect.width(), abs=2.0)
        assert rect.top() == pytest.approx(new_bounds.height() - rect.height(), abs=2.0)
        assert rect.center().x() < new_bounds.x() + rx * new_bounds.width()
        assert rect.center().y() < new_bounds.y() + ry * new_bounds.height()
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 迁移前的在途会话
def test_screen_removed_finishes_inflight_grab_through_the_grab_protocol(tmp_path, monkeypatch):
    """拔屏前的会话收尾走既有 grab 协议：旗标与穿透轮询档位一起复位。

    ``handle_screen_removed`` 在重算几何**之前**收掉窗口的输入会话；旧实现只清
    ``_mouse_grab``/``_press_global`` 两个字段就返回——click-only 与拖拽提交
    旗标留在窗上，穿透轮询的拖拽降频（``set_drag_active``）也没复位。
    """
    fake = FakeScreen((0, 0, 1000, 600), (0, 0, 1000, 600), name="gone")
    other = FakeScreen((1000, 0, 1000, 600), (1000, 0, 1000, 600), name="alive")
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [fake, other]))
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: other))
    shell = _make_shell(tmp_path, fake)
    try:
        overlay = shell.overlay
        overlay._mouse_grab = shell.sprite
        overlay._press_click_only = True
        overlay._drag_committed = True
        overlay._press_global = QPointF(10, 10).toPoint()

        shell.handle_screen_removed(fake)

        assert overlay._mouse_grab is None
        assert overlay._press_click_only is False
        assert overlay._drag_committed is False
        assert overlay._press_global is None
        assert shell._screen is other
    finally:
        _teardown(shell)
