# -*- coding: utf-8 -*-
"""G2：同屏几何/可用区变化——全部 sprite 按同一 old/new bounds 比例迁移 + 气泡全局原点同步。

覆盖（真实 OverlayWindow + 真实 PetSprite，假屏只包装显式方法）：
- 主 + 2 子肥鱼在 ``geometryChanged`` / ``availableGeometryChanged`` 后**每只**都按
  同一份 old_bounds → new_bounds 比例迁移（三组几何：常规缩放、原点右移、负坐标迁回）；
  迁移的比例取自换域**之前**——先钳到新边界会把旧位置比例抹掉；
- 新可用区比身体还小时钳制退化与既有语义一致（``PetSprite._clamp_axis`` 上界<下界 → 贴左下）；
- 气泡跟随器存在时 overlay 全局原点变化（屏位置变）后锚点 = ``body_rect`` + **新**原点，
  且不重建跟随器/气泡；仅 available 变化（任务栏）原点不变、同样不重建；
- 位置监听通知来自真实 tick 扇出（``driver.advance_overlays`` → ``overlay.tick_advance``），
  不手工调用跟随回调；30Hz 节流尾部补发只持一个单发 timer，不随通知数增长。

纪律：不 sleep 赌时序、不手工调 ``_on_sprite_moved``/``_flush_follow``；配置用
``tmp_path`` 临时 Config（不碰真实配置）。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QTimer, Signal
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet import collision as collision_mod
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from pet.sprite_bubble import sprite_anchor_rect_global

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 假件
class FakeScreen(QObject):
    """假屏：显式 set_rects + 真实 Qt 信号（走产品真接线，不直调 handler）。"""

    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, geo, avail, *, dpr=1.0, refresh=60.0, name="g2-screen"):
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


class RichInstance:
    """每只宠一个假库（真 PetSprite 的 movie/duration 面够用）。"""

    def __init__(self, config):
        self.config = config

    def _create_library(self, character_id):
        return fac.RichLibrary()


class FakeBubble:
    """记录 reposition 的气泡替身（跟随器复用现成 SpriteBubbleFollower）。"""

    def __init__(self):
        self.moves: list[QRect] = []
        self.texts: list = []
        self.closed = 0

    def reposition(self, anchor):
        self.moves.append(QRect(anchor))

    def show_text(self, text, anchor, duration_ms, **kw):
        self.texts.append((text, QRect(anchor), duration_ms, kw))
        return True

    def hide(self):
        pass

    def close(self):
        self.closed += 1


def _make_shell(tmp_path, screen):
    config = cap.CapConfig(tmp_path)
    shell = OverlayShell(
        app, RichInstance(config), screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    shell.lib = fac.RichLibrary()
    shell.sprite.library = shell.lib
    shell.spawn_pet()
    shell.spawn_pet()
    assert len(shell._spawned) == 2, "前提：主 + 2 子确实同进程存在"
    return shell


def _place_center(sprite, cx, cy):
    """把 sprite 摆到指定中心（PetSprite.set_pos 会按 body 钳制，合法位=恒等）。"""
    rect = sprite.rect()
    sprite.set_pos(QPointF(cx - rect.width() / 2.0, cy - rect.height() / 2.0))
    return sprite.rect()


def _ratio_of(rect: QRect, bounds: QRect) -> tuple[float, float]:
    """身体框中心相对可用区的比例（与产品 ``_sprite_geometry`` 同口径）。"""
    return ((rect.x() + rect.width() / 2.0 - bounds.x()) / bounds.width(),
            (rect.y() + rect.height() / 2.0 - bounds.y()) / bounds.height())


def _body_of(sprite) -> QRect:
    return sprite.body_rect()


def _expected_center(rx, ry, new_bounds: QRect) -> tuple[float, float]:
    return (new_bounds.x() + rx * new_bounds.width(),
            new_bounds.y() + ry * new_bounds.height())


def _expected_center_clamped(rx, ry, new_bounds: QRect, rect: QRect) -> tuple[float, float]:
    """比例理想中心 + PetSprite 的合法钳制（全画布 body：整 rect 落在 bounds 内）。

    小屏上比例值越界时产品语义就是把它钳回边界（``_clamp_axis``，上界<下界时贴
    左下），这里按同一文档口径算期望值，避免把"合法钳制"误判成迁移错误。
    """
    w, h = rect.width(), rect.height()
    ideal_x, ideal_y = _expected_center(rx, ry, new_bounds)

    def _axis(value, lo, hi, span):
        lower, upper = lo, hi - span
        if upper < lower:
            return lower
        return min(max(value, lower), upper)

    left = _axis(ideal_x - w / 2.0, new_bounds.x(),
                 new_bounds.x() + new_bounds.width(), w)
    top = _axis(ideal_y - h / 2.0, new_bounds.y(),
                new_bounds.y() + new_bounds.height(), h)
    return (left + w / 2.0, top + h / 2.0)


# 三组几何：常规缩放 / 原点右移 / 负坐标迁回（旧 bounds 起点非零、含负坐标）
GEOMETRY_GROUPS = [
    ((0, 0, 1920, 1080), (0, 0, 1920, 1040), (0, 0, 1280, 720), (0, 0, 1280, 680)),
    ((1920, 0, 1920, 1080), (1920, 40, 1920, 1040),
     (1920, -100, 1600, 900), (1920, 0, 1600, 860)),
    ((-1920, -200, 1600, 900), (-1920, -160, 1600, 860),
     (0, 0, 1024, 768), (0, 0, 1024, 728)),
]


@pytest.mark.parametrize("old_geo,old_avail,new_geo,new_avail", GEOMETRY_GROUPS)
def test_all_pets_migrate_on_same_bounds_ratio(tmp_path, old_geo, old_avail,
                                               new_geo, new_avail):
    """主 + 2 子共用同一 old/new bounds 比例迁移：子肥鱼不再留在原地被钳。"""
    screen = FakeScreen(old_geo, old_avail)
    shell = _make_shell(tmp_path, screen)
    try:
        old_bounds = QRect(shell._bounds)
        pets = [shell.sprite, *shell._spawned]
        # 三只摆在不同比例（都合法落在旧可用区内）
        places = [(0.5, 0.5), (0.25, 0.35), (0.8, 0.75)]
        ratios = []
        for sprite, (fx, fy) in zip(pets, places):
            _place_center(sprite,
                          old_bounds.x() + fx * old_bounds.width(),
                          old_bounds.y() + fy * old_bounds.height())
            ratios.append(_ratio_of(_body_of(sprite), old_bounds))
        before = [sprite.pos for sprite in pets]

        screen.set_rects(new_geo, new_avail)
        screen.geometryChanged.emit(screen.geometry())  # 真信号接线

        new_bounds = QRect(shell._bounds)
        # 合成窗 = 各屏可用工作区并集，局部坐标从 (0,0) 起
        assert new_bounds == QRect(0, 0, new_avail[2], new_avail[3])
        for sprite, (rx, ry), pos_before in zip(pets, ratios, before):
            rect = _body_of(sprite)
            cx, cy = _expected_center_clamped(rx, ry, new_bounds, rect)
            # ±2px 预算：比例取自整数 rect（中心 0.5px 量化）→ 换成新尺寸 → 落位再取整
            assert rect.center().x() == pytest.approx(cx, abs=2.0), "每宠按同一 new_bounds 迁移"
            assert rect.center().y() == pytest.approx(cy, abs=2.0)
            assert sprite.pos != pos_before or old_bounds == new_bounds
            assert sprite.bounds == QRectF(new_bounds)  # 边界照旧应用到全部
    finally:
        shell._delete_runtime_marker()


def test_migration_precedes_bounds_apply_so_ratio_is_not_lost(tmp_path):
    """子肥鱼贴在旧可用区右侧：先应用新边界再迁移会把比例钳掉。

    先换域时 ``PetSprite.set_bounds`` 会就地钳一次（把 0.85 的比例钳成 ~0.71），
    随后迁移按被改过的位置算——本用例锁死"比例在换域之前取"。
    数值刻意选成"比例迁移结果合法落在新可用区内"（不触发 clamp），故观测值
    只能是比例值本身，钳过与没钳过可直接区分。
    """
    screen = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = _make_shell(tmp_path, screen)
    try:
        old_bounds = QRect(shell._bounds)
        child = shell._spawned[0]
        _place_center(child,
                      old_bounds.x() + 0.85 * old_bounds.width(),
                      old_bounds.y() + 0.5 * old_bounds.height())
        rx, ry = _ratio_of(_body_of(child), old_bounds)
        assert 0.8 < rx < 0.9, "夹具前提：贴右但未贴边"

        screen.set_rects((0, 0, 1600, 900), (0, 0, 1600, 860))
        screen.availableGeometryChanged.emit(screen.availableGeometry())

        new_bounds = QRect(shell._bounds)
        cx, cy = _expected_center(rx, ry, new_bounds)
        rect = _body_of(child)
        assert rect.x() + rect.width() / 2.0 == pytest.approx(cx, abs=1.0)
        assert rect.y() + rect.height() / 2.0 == pytest.approx(cy, abs=1.0)
        # 反例：先套新边界会把旧绝对位置钳成 ~0.71 的比例，观测值会塌到新宽 3/4 附近
        assert rect.x() + rect.width() / 2.0 > 0.8 * new_bounds.width(), "不得先 clamp 丢旧位置比例"
    finally:
        shell._delete_runtime_marker()


def test_tiny_available_area_clamp_keeps_existing_semantics(tmp_path):
    """新可用区比身体还小：钳制退化（上界<下界 → 贴左下）与既有语义一致。"""
    screen = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = _make_shell(tmp_path, screen)
    try:
        screen.set_rects((0, 0, 120, 90), (0, 0, 120, 90))
        screen.geometryChanged.emit(screen.geometry())
        new_bounds = QRect(shell._bounds)
        assert new_bounds == QRect(0, 0, 120, 90)
        for sprite in [shell.sprite, *shell._spawned]:
            body = sprite.body_rect()
            assert body.left() == new_bounds.left()
            assert body.top() == new_bounds.top()
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 气泡全局原点
def test_bubble_origin_follows_moved_screen_geometry(tmp_path):
    """屏位置变（同尺寸）→ 跟随器原点必须换成新原点：锚点 = body_rect + 新原点。"""
    screen = FakeScreen((1920, 0, 1920, 1080), (1920, 40, 1920, 1040))
    shell = _make_shell(tmp_path, screen)
    try:
        follower = shell._bubble_follower
        bubble = follower.bubble
        follower.bubble = FakeBubble()
        assert follower._origin == QPoint(1920, 40)

        # 屏整体移到 (1700, 120)，可用区尺寸不变 → bounds 不变、原点变
        screen.set_rects((1700, 120, 1920, 1080), (1700, 160, 1920, 1040))
        screen.geometryChanged.emit(screen.geometry())

        new_origin = QPoint(1700, 160)
        assert shell.overlay.geometry() == QRect(1700, 160, 1920, 1040)
        assert shell._bounds == QRect(0, 0, 1920, 1040)  # 同尺寸：边界不变
        body = shell.sprite.body_rect()
        assert follower.anchor() == QRect(new_origin + body.topLeft(), body.size())
        assert follower.anchor().topLeft() != QPoint(1920, 0) + body.topLeft()
        # 不重建：跟随器与气泡都是同一个对象（重建会丢在显气泡）
        assert shell._bubble_follower is follower
        assert follower.bubble is not None
        # 子肥鱼走同一锚点口径（body_rect + 新原点）
        for child in shell._spawned:
            child_body = child.body_rect()
            assert sprite_anchor_rect_global(child, new_origin) == QRect(
                new_origin + child_body.topLeft(), child_body.size())
    finally:
        shell._delete_runtime_marker()


def test_available_only_change_keeps_origin_and_reuses_bubble(tmp_path):
    """同屏任务栏变化（仅 available）：原点不变，不重建跟随器/气泡。"""
    screen = FakeScreen((1920, 0, 1920, 1080), (1920, 40, 1920, 1040))
    shell = _make_shell(tmp_path, screen)
    try:
        follower = shell._bubble_follower
        follower.bubble = FakeBubble()
        anchor_before = follower.anchor()

        screen.set_rects((1920, 0, 1920, 1080), (1920, 40, 1920, 1000))
        screen.availableGeometryChanged.emit(screen.availableGeometry())

        assert shell._bounds == QRect(0, 0, 1920, 1000)
        assert follower._origin == QPoint(1920, 40)
        assert shell._bubble_follower is follower
        body = shell.sprite.body_rect()
        assert follower.anchor() == QRect(QPoint(1920, 40) + body.topLeft(), body.size())
        assert anchor_before.topLeft() != QPoint(0, 0)  # 夹具本身非空断言
    finally:
        shell._delete_runtime_marker()


def test_bubble_follow_notified_by_tick_fanout_with_bounded_timer(tmp_path):
    """跟随来自真实 tick 扇出（非手工调回调）；节流尾部补发只持一个单发 timer。

    位置监听只在 advance 的"旧|新 rect"通道通知（``overlay_window.tick_advance``）：
    真实位移由 velocity 积分在 advance 内产生，走的正是产品路径。
    """
    screen = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = _make_shell(tmp_path, screen)
    try:
        follower = shell._bubble_follower
        follower.bubble = FakeBubble()
        timers_before = len(shell.overlay.findChildren(QTimer))
        follow_timer = follower._follow_timer
        sprite = shell.sprite

        follower._last_follow = 0.0                # 复位节流窗口（不 sleep 赌 30Hz）
        sprite.set_velocity(QPointF(240.0, 0.0))   # 行为层同款写入通道
        assert follower.bubble.moves == []
        shell.driver.advance_overlays(1 / 60.0)    # 真扇出：advance → 位置监听 → 跟随
        assert len(follower.bubble.moves) == 1, "位置监听必须经 tick 扇出通知跟随方"
        body = sprite.body_rect()
        origin = shell.overlay.geometry().topLeft()
        assert follower.bubble.moves[-1] == QRect(origin + body.topLeft(), body.size())

        # 连续 tick（每拍都停在节流窗口内）：尾部补发复用同一个单发 timer，不累积
        for _ in range(40):
            follower._last_follow = time.monotonic()
            shell.driver.advance_overlays(1 / 60.0)
        sprite.set_velocity(QPointF(0.0, 0.0))
        assert follower._follow_pending is True
        assert follower._follow_timer is follow_timer
        assert follow_timer.isSingleShot() is True
        assert follow_timer.isActive() is True          # 尾部补发已挂上（会补发最后一帧）
        assert len(shell.overlay.findChildren(QTimer)) == timers_before, "不得累积新 timer"
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 可见气泡随全局原点（同尺寸平移屏）
def _bubble_spy(bubble):
    """真实 bubble.reposition 的透传 spy：既记实参又走真实现（不是替身）。"""
    calls = []
    real = bubble.reposition

    def spy(anchor_rect):
        calls.append(QRect(anchor_rect))
        return real(anchor_rect)

    bubble.reposition = spy
    return calls


def test_visible_bubble_follows_same_size_screen_translation(tmp_path):
    """屏等尺寸平移：sprite 不位移、bounds 不变，在显气泡必须随全局原点整体移动。

    真实 ``PetSpeechBubble``（offscreen 下窗口几何真实生效）：只做
    ``handle_geometry_changed``（geometryChanged 的处理体）+ 事件循环处理，
    不人为 ``set_pos``、不手工 ``reposition``。屏尺寸取 760×700 让主宠默认角落
    锚点在真实屏（800×800）内、且平移后仍不触发夹取，于是"气泡几何相对锚点"
    的关系可直接比对。
    """
    screen = FakeScreen((0, 0, 760, 700), (0, 0, 760, 660))
    shell = _make_shell(tmp_path, screen)
    try:
        follower = shell._bubble_follower
        bubble = follower.bubble
        assert bubble is not None, "夹具前提：真实 PetSpeechBubble 构造成功"
        assert follower.show("跟随测试", 1500) is True
        assert bubble.isVisible() is True
        anchor_before = follower.anchor()
        geom_before = QRect(bubble.geometry())
        sprite_rect_before = shell.sprite.rect()
        bounds_before = QRect(shell._bounds)
        calls = _bubble_spy(bubble)
        hide_starts = []
        real_hide_start = bubble._hide_timer.start
        bubble._hide_timer.start = lambda *a, **k: (
            hide_starts.append(a), real_hide_start(*a, **k))[1]
        # 白盒前提：节流窗口里已挂上一拍待补发（断言点是 set_origin 必须取消它）
        follower._follow_pending = True
        follower._follow_timer.start()
        assert follower._follow_timer.isActive() is True

        delta = QPoint(60, 40)
        screen.set_rects((delta.x(), delta.y(), 760, 700),
                         (delta.x(), delta.y(), 760, 660))
        shell.handle_geometry_changed()          # geometryChanged 的处理体
        app.processEvents()                      # 事件循环处理（不 sleep）

        anchor_after = follower.anchor()
        geom_after = QRect(bubble.geometry())
        assert shell.sprite.rect() == sprite_rect_before, "同尺寸平移：sprite 局部位置不变"
        assert shell._bounds == bounds_before, "同尺寸平移：边界不变"
        assert anchor_after.topLeft() == anchor_before.topLeft() + delta
        # 真实 reposition 实参自动触达（不是只读 anchor getter）
        assert calls == [anchor_after], f"应就新锚点重定位一次，实际 {calls}"
        assert follower.bubble is bubble, "不得重建气泡控件"
        assert bubble.isVisible() is True
        # 可见窗口几何真的跟着原点走，且"气泡相对锚点"的关系不变（无夹取场景）
        assert geom_after.topLeft() == geom_before.topLeft() + delta
        assert (geom_after.topLeft() - anchor_after.topLeft()
                == geom_before.topLeft() - anchor_before.topLeft())
        # 隐态/倒计时纪律：没有重新 show、停留倒计时没被重启、没有待补发双发
        assert hide_starts == [], "重定位不得重启停留倒计时"
        assert follower._follow_pending is False
        assert follower._follow_timer.isActive() is False
    finally:
        shell._delete_runtime_marker()


def test_set_origin_keeps_hidden_bubble_hidden(tmp_path):
    """隐藏气泡不因原点刷新被显示，也不产生重定位（不隐态意外 show）。"""
    screen = FakeScreen((0, 0, 760, 700), (0, 0, 760, 660))
    shell = _make_shell(tmp_path, screen)
    try:
        follower = shell._bubble_follower
        bubble = follower.bubble
        assert bubble is not None
        bubble.hide()
        assert bubble.isVisible() is False
        calls = _bubble_spy(bubble)

        screen.set_rects((80, 50, 760, 700), (80, 50, 760, 660))
        shell.handle_geometry_changed()
        app.processEvents()

        assert bubble.isVisible() is False, "隐态不得被 set_origin 显示"
        assert calls == [], "隐态不重定位"
        assert follower._origin == QPoint(80, 50)
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 岛墙原点（同一次 _sync_geometry）
def _island_in_origin_frame(island, origin: QPoint) -> tuple[int, int, int, int]:
    geo = island.geometry()
    return (geo.x() - origin.x(), geo.y() - origin.y(), geo.width(), geo.height())


def test_island_wall_geometry_tracks_origin_change(tmp_path):
    """开态岛墙：同屏原点变化后墙的局部几何必须按新原点重算（岛本体不动）。"""
    from pet.config import Config
    from pet.dynamic_island import DynamicIsland

    screen = FakeScreen((0, 0, 1024, 768), (0, 0, 1024, 728))
    shell = _make_shell(tmp_path, screen)
    island = DynamicIsland(Config(base=tmp_path))
    try:
        island.setGeometry(300, 200, 220, 44)
        island.show()
        shell.attach_island(island)
        assert shell.island_bridge is not None
        assert shell.island_bridge.registered is True
        member = shell.collision._static_members[collision_mod.ISLAND_MEMBER_ID]
        assert tuple(member) == _island_in_origin_frame(island, QPoint(0, 0))
        island_geo_before = QRect(island.geometry())

        delta = QPoint(120, 90)
        screen.set_rects((delta.x(), delta.y(), 1024, 768),
                         (delta.x(), delta.y(), 1024, 728))
        shell.handle_geometry_changed()

        assert island.geometry() == island_geo_before, "岛本体不动（只有坐标原点跳变）"
        member = shell.collision._static_members[collision_mod.ISLAND_MEMBER_ID]
        assert tuple(member) == _island_in_origin_frame(island, delta)
        assert tuple(member) != _island_in_origin_frame(island, QPoint(0, 0)), "墙不得留旧原点"
    finally:
        shell.attach_island(None)
        island.close()
        shell._delete_runtime_marker()


def test_no_island_bridge_means_no_wall_restart(tmp_path):
    """无桥（岛碰撞关/无岛）时几何同步不得凭空建桥或重启岛墙。"""
    screen = FakeScreen((0, 0, 1024, 768), (0, 0, 1024, 728))
    shell = _make_shell(tmp_path, screen)
    try:
        assert getattr(shell, "island_bridge", None) is None
        screen.set_rects((40, 30, 1024, 768), (40, 30, 1024, 728))
        shell.handle_geometry_changed()
        shell.handle_geometry_changed()
        assert getattr(shell, "island_bridge", None) is None, "不得凭空建桥"
        assert collision_mod.ISLAND_MEMBER_ID not in shell.collision._static_members
    finally:
        shell._delete_runtime_marker()
