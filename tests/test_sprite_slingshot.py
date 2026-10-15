# -*- coding: utf-8 -*-
"""弹弓蓄力瞄准（单合成窗移植）offscreen 回归：进入 / 取消 / 发射三路径。

覆盖（对齐旧窗口路径语义，蓝本 pet/window.py:2987-3087 + 2367-2413）：
- 进入：拖拽中点右键 → 瞄准；sprite 定锚不动、留在 drag 等价态；
- 取消：Esc 回锚点；再点右键就地恢复拖拽；
- 发射：按 physics.slingshot_speed（直调，不复制常量）写初速 → "thrown"，
  随后落进既有 sprite_physics 通道；
- config 热读：进判定前读 slingshot_enabled，禁用时回 V-13 合成 release；
- 瞄准免疫：behavior / collision / physics / advance 四路都不推进瞄准 sprite
  （碰撞世界 FLAG_SLINGSHOT_AIMING 的等价物 = drag 无限质量）；
- 多 sprite 只影响被 grab 者；
- 瞄准 UI：橡皮带 + 轨迹预览只在瞄准期间绘制，取消/发射后零残留，
  脏区走 overlay.update(region) 局部刷新。

纪律（AGENTS.md 时序测试）：同步直调事件处理器与控制器，不 sleep 赌时序；
素材用纯 QImage 假 clip，不依赖 webm/ffmpeg。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from pet import physics as physics_mod
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import (INTERACTION_DRAG, INTERACTION_NORMAL,
                            INTERACTION_THROWN, PetSprite)
from pet.sprite_behavior import BehaviorController
from pet.sprite_collision import SpriteCollisionWorld
from pet.sprite_physics import ThrowPhysicsController
from pet.sprite_slingshot import SlingshotController

app = QApplication.instance() or QApplication([])

SCALE = 0.5
# CANVAS 320x180（scale=0.5）；命中点一律取 sprite 矩形中心（假素材整幅不透明）


class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self, *, opaque: bool = True):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(0xFF112233 if opaque else Qt.GlobalColor.transparent)
        self.started = False

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.started = False


class FakeLibrary:
    def __init__(self, clip=None):
        self._clip = clip or FakeClip()
        self.no_mirror: set[str] = set()

    def movie(self, name):
        return self._clip


class FakeConfig:
    """最小 config 替身：只有 get（controller 只依赖这个协议）。"""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class CountingOverlay(OverlayWindow):
    """记录 update 调用与脏区域的 overlay（验证局部刷新，不整屏 repaint）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.update_calls = 0
        self.update_regions: list[QRegion] = []

    def update(self, *args):
        self.update_calls += 1
        for arg in args:
            if isinstance(arg, QRegion):
                self.update_regions.append(QRegion(arg))
        super().update(*args)


def _make_sprite(pos=(200, 200), scale=SCALE):
    clip = FakeClip()
    sprite = PetSprite(FakeLibrary(clip), pos=QPointF(*pos), scale=scale)
    sprite.bind_clip("idle")
    sprite._rebuild_pixmap()
    return sprite


def _to_qpointf(pos):
    if isinstance(pos, (QPoint, QPointF)):
        return QPointF(pos)
    return QPointF(float(pos[0]), float(pos[1]))


def _mouse(etype, pos, button=Qt.MouseButton.LeftButton, buttons=None):
    btns = button if buttons is None else buttons
    point = _to_qpointf(pos)
    return QMouseEvent(etype, point, point, button, btns,
                       Qt.KeyboardModifier.NoModifier)


def _right_press(pos):
    """真实右键 press：左键仍按住 → buttons 含 Left|Right。"""
    return _mouse(QEvent.Type.MouseButtonPress, pos, Qt.MouseButton.RightButton,
                  Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)


def _hit_point(sprite):
    return QPoint(sprite.rect().center())


def _begin_drag(overlay, sprite, *, offset=(20, 0)):
    """开始拖拽并移动一次：返回（按下点，移动后光标点）。"""
    press = _hit_point(sprite)
    overlay.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    assert overlay._mouse_grab is sprite
    moved = QPoint(press.x() + offset[0], press.y() + offset[1])
    overlay.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, moved))
    return press, moved


def _enter_aim(overlay, sprite, *, offset=(20, 0)):
    """拖拽 → 右键进瞄准；返回（锚点光标，锚点 sprite 位置）。"""
    _press, moved = _begin_drag(overlay, sprite, offset=offset)
    overlay.mousePressEvent(_right_press(moved))
    assert overlay.slingshot.aiming, "右键进瞄准失败"
    return moved, QPointF(sprite.pos)


def _pull(overlay, moved, dx, dy):
    """把光标从锚点光标处移动 (dx, dy) 并返回新光标点（局部坐标）。"""
    cursor = QPoint(moved.x() + dx, moved.y() + dy)
    overlay.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cursor,
                                  Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton))
    return cursor


# ================================================================ 进入
def test_right_press_during_grab_enters_aim_and_anchors_sprite():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)

    assert overlay.slingshot.sprite is sprite
    assert overlay.slingshot.anchor == anchored
    assert overlay._mouse_grab is sprite          # V-13 关切：sprite 没被放下
    assert sprite.pos == anchored                 # 定锚不动
    assert sprite.interaction_state == INTERACTION_DRAG
    assert sprite.velocity == QPointF(0, 0)
    assert overlay._press_global is not None      # 左键仍按住：穿透轮询据此不穿透

    # 瞄准中移动光标：只有拉拽矢量变，sprite 一步不动
    _pull(overlay, moved, -40, -30)
    assert sprite.pos == anchored
    assert overlay.slingshot.pull != QPointF(0, 0)
    assert 0.0 < overlay.slingshot.progress() <= 1.0


def test_aim_pull_clamps_to_max_distance_and_band_end_follows_cursor():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)

    far = _pull(overlay, moved, -2000, 0)     # 远超上限
    assert overlay.slingshot.pull_length() == physics_mod.SLINGSHOT_MAX_DISTANCE * SCALE
    assert overlay.slingshot.progress() == 1.0
    band = overlay.slingshot.band()
    assert band is not None
    assert band[1] == QPointF(far)            # 橡皮带末端 = 真实光标（不随钳制回缩）


# ================================================================ 取消
def test_escape_cancels_to_anchor_and_releases_grab():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -60, -40)

    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                    Qt.KeyboardModifier.NoModifier))

    assert overlay.slingshot.aiming is False
    assert overlay.slingshot.sprite is None
    assert sprite.pos == anchored                 # 回锚点（旧 _cancel_slingshot_to_anchor）
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)
    assert overlay._mouse_grab is None            # 会话结束：grab 收尾
    assert overlay._press_global is None

    # 松左键：会话已结束，不得再发射
    overlay.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, moved))
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)


def test_second_right_press_cancels_in_place_and_resumes_drag():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -50, 0)
    assert overlay.slingshot.aiming

    overlay.mousePressEvent(_right_press(moved))   # 再点右键 = 取消（就地）

    assert overlay.slingshot.aiming is False
    assert sprite.pos == anchored                 # 就地取消：不回锚点也不跳位
    assert sprite.interaction_state == INTERACTION_DRAG
    assert sprite.dragging is True                # 拖拽恢复（旧 _cancel_slingshot_to_drag）
    assert overlay._mouse_grab is sprite

    # 拖拽确实恢复：grab 偏移按「恢复时刻的光标」重建，相对位移 1:1
    # （不把瞄准期间光标走过的距离一次性补跳回来）
    overlay.mouseMoveEvent(_mouse(QEvent.Type.MouseMove,
                                  QPoint(cursor.x() + 10, cursor.y() + 10)))
    assert sprite.pos == QPointF(anchored.x() + 10, anchored.y() + 10)


# ================================================================ 发射
def test_left_release_fires_with_physics_speed_and_enters_thrown():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    sprite.throw_speed_cap = 4800.0
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -72, 0)        # 拉拽 72px（scale 0.5：24..80）

    pull = QPointF(overlay.slingshot.pull)
    pull_length = overlay.slingshot.pull_length()
    expected_speed = physics_mod.slingshot_speed(
        pull_length,
        physics_mod.SLINGSHOT_MIN_DISTANCE * SCALE,
        physics_mod.SLINGSHOT_MAX_DISTANCE * SCALE,
        4800.0,
    )
    assert pull_length == 72.0
    assert expected_speed > 0.0

    overlay.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cursor))

    assert overlay.slingshot.aiming is False
    assert sprite.pos == anchored                 # 发射起点 = 锚点
    assert sprite.interaction_state == INTERACTION_THROWN
    length = pull_length or 1.0
    assert sprite.velocity.x() == expected_speed * pull.x() / length
    assert sprite.velocity.y() == expected_speed * pull.y() / length
    assert overlay._mouse_grab is None            # 发射即结束本次 grab
    assert overlay._press_global is None
    assert sprite.dragging is False               # 交给 thrown 通道，不再算拖拽

    # 既有 sprite_physics 通道接管：下一 tick 起积分（不再"瞄准中不推进"）
    physics = ThrowPhysicsController(QRect(0, 0, 4000, 4000))
    before = QPointF(sprite.pos)
    physics.tick([sprite], 0.05)
    assert sprite.pos.x() > before.x()            # 拉向左 → 向右弹射


def test_fired_sprite_lands_through_existing_thrown_pipeline():
    """发射后完全交给既有 thrown 通道：物理积分 → 落地静止 → 回 "normal"。"""
    overlay = OverlayWindow()
    sprite = _make_sprite(pos=(300, 200))
    sprite.throw_speed_cap = 4800.0
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -60, -30)

    overlay.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cursor))
    assert sprite.interaction_state == INTERACTION_THROWN

    physics = ThrowPhysicsController(QRect(0, 0, 800, 600))
    # 宽预算（实测 480 tick 落地静止；预算给 3 倍，CI 慢机也不赌时序）
    for _ in range(1500):
        physics.tick([sprite], 0.016)
        if sprite.interaction_state == INTERACTION_NORMAL:
            break
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)
    assert sprite.pos != QPointF(300, 200)        # 真的被弹飞了
    assert sprite.dragging is False


def test_fire_below_min_distance_cancels_to_anchor():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -5, 0)         # < 24*0.5 = 12px

    overlay.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cursor))

    assert overlay.slingshot.aiming is False
    assert sprite.pos == anchored
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)


# ================================================================ config 热读
def test_config_hot_read_gates_entry_and_falls_back_to_v13_release():
    config = FakeConfig({"slingshot_enabled": False})
    overlay = OverlayWindow()
    overlay.slingshot.config = config
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    _press, moved = _begin_drag(overlay, sprite)

    overlay.mousePressEvent(_right_press(moved))
    assert overlay.slingshot.aiming is False      # 禁用：不进瞄准
    assert overlay._mouse_grab is None            # V-13 合成 release 照旧
    assert sprite.interaction_state == INTERACTION_NORMAL

    # 热切换（设置页开关）：下一次进入判定即生效，无需重建 controller
    config.set("slingshot_enabled", True)
    _press2, moved2 = _begin_drag(overlay, sprite)
    overlay.mousePressEvent(_right_press(moved2))
    assert overlay.slingshot.aiming is True


def test_controller_defaults_to_enabled_without_config():
    controller = SlingshotController()
    assert controller.enabled is True


def test_config_flipped_off_mid_aim_keeps_exit_paths():
    """瞄准期间把 slingshot_enabled 关掉：会话必须仍有退出路径（Esc/再点右键）。"""
    config = FakeConfig({"slingshot_enabled": True})
    overlay = OverlayWindow()
    overlay.slingshot.config = config
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -40, 0)
    assert overlay.slingshot.aiming

    config.set("slingshot_enabled", False)
    overlay.mousePressEvent(_right_press(cursor))     # 再点右键仍要能取消
    assert overlay.slingshot.aiming is False
    assert sprite.interaction_state == INTERACTION_DRAG

    # Esc 同理：重新进不了（已禁用），但已有会话必须能收
    config.set("slingshot_enabled", True)
    overlay.mousePressEvent(_right_press(cursor))     # 重新进瞄准
    assert overlay.slingshot.aiming is True
    config.set("slingshot_enabled", False)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                    Qt.KeyboardModifier.NoModifier))
    assert overlay.slingshot.aiming is False
    assert sprite.pos == anchored
    assert sprite.interaction_state == INTERACTION_NORMAL


def test_stale_press_watchdog_cancels_aim_to_anchor():
    """V-7 看门狗 + 瞄准：左键按压源消失（release 事件丢失）→ 收会话回锚点，
    绝不发射、绝不留悬空瞄准态。"""
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -70, 0)
    assert overlay._press_global is not None
    assert not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)  # 测试环境无真按键

    overlay._check_stale_press()

    assert overlay.slingshot.aiming is False
    assert sprite.pos == anchored
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.velocity == QPointF(0, 0)
    assert overlay._mouse_grab is None
    assert overlay._press_global is None


def test_stale_press_watchdog_clears_click_only_grab_and_cancels_aim():
    """V-7 看门狗 + click-only：锁定位/未按 SHIFT 的按住**也能进瞄准**（右键
    分支只看 ``_mouse_grab``），丢 release 后看门狗必须把瞄准会话一起收掉
    ——否则 sprite 停在 DRAG、右键菜单被拦（ops5.5 minor 1）。"""
    overlay = OverlayWindow()
    overlay.lock_position = True          # 命中按下只算点击：不记 _press_global
    sprite = _make_sprite()
    overlay.add_sprite(sprite)

    press = _hit_point(sprite)
    overlay.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    assert overlay._mouse_grab is sprite
    assert overlay._press_click_only is True
    assert overlay._press_global is None       # click-only：没有拖拽锚点

    overlay.mousePressEvent(_right_press(press))   # 右键进瞄准（既有行为）
    assert overlay.slingshot.aiming is True
    anchored = QPointF(sprite.pos)
    assert sprite.interaction_state == INTERACTION_DRAG
    # 测试环境无真按键：mouseButtons() 必为 NoButton → 看门狗应判 release 已丢
    assert not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    overlay._check_stale_press()

    assert overlay.slingshot.aiming is False           # 瞄准会话已收
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert sprite.pos == anchored
    assert sprite.velocity == QPointF(0, 0)
    assert overlay._mouse_grab is None
    overlay._context_menu_blocked()                    # 吞掉进瞄准时置的一次性抑制旗标
    assert overlay._context_menu_blocked() is False    # 菜单恢复（瞄准已收）


def test_unconsumed_right_press_clears_stale_menu_suppression():
    """右键菜单抑制是一次性的：未被弹弓消费的右键必须清旗标，否则误吞一次菜单。"""
    overlay = OverlayWindow()

    # 无 grab 的右键（正经菜单手势）：旗标清零
    overlay._context_menu_suppressed = True
    overlay.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, (10, 10),
                                   Qt.MouseButton.RightButton,
                                   Qt.MouseButton.RightButton))
    assert overlay._context_menu_suppressed is False

    # 旗标是一次性的：吞掉一次 ContextMenu 后不再吞（Qt 入口拦截见
    # test_context_menu_suppressed_for_slingshot_right_press）
    overlay._context_menu_suppressed = True
    assert overlay._context_menu_blocked() is True
    assert overlay._context_menu_suppressed is False
    assert overlay._context_menu_blocked() is False


def test_non_aim_sprite_protocol_falls_back_to_v13():
    """极简替身（无完整交互协议）不参与瞄准：overlay 保持 V-13 合成 release。"""

    class MinimalGrab:
        def __init__(self):
            self.released = False

        def on_release(self, _pos):
            self.released = True

    overlay = OverlayWindow()
    grab = MinimalGrab()
    overlay._mouse_grab = grab
    overlay._press_global = QPoint(5, 5)
    overlay.mousePressEvent(_right_press((10, 10)))

    assert overlay.slingshot.aiming is False
    assert grab.released is True
    assert overlay._mouse_grab is None


# ================================================================ 瞄准免疫（四路）
def test_aiming_sprite_not_advanced_by_behavior_collision_and_physics():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -70, -30)

    behavior = BehaviorController(QRect(0, 0, 4000, 4000))
    physics = ThrowPhysicsController(QRect(0, 0, 4000, 4000))
    world = SpriteCollisionWorld()

    for _ in range(20):
        behavior.tick([sprite], 0.016)
        world.tick([sprite], 0.016)
        physics.tick([sprite], 0.016)
        sprite.advance(0.016)

    assert sprite.pos == anchored                 # 四路推进都不动它
    assert sprite.velocity == QPointF(0, 0)
    assert sprite.interaction_state == INTERACTION_DRAG


def test_aiming_sprite_is_infinite_mass_in_collision_world():
    """碰撞世界 FLAG_SLINGSHOT_AIMING 等价物：drag 态 = 无限质量，撞不动。"""
    overlay = OverlayWindow()
    sprite = _make_sprite(pos=(200, 200))
    overlay.add_sprite(sprite)
    moved, anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -70, -30)
    assert SpriteCollisionWorld._is_dragging(sprite) is True

    # 另一个 sprite 直接叠到瞄准者身上并高速撞它
    other = _make_sprite(pos=(int(anchored.x()) + 4, int(anchored.y()) + 4))
    other.set_velocity(QPointF(-2400, 0))
    world = SpriteCollisionWorld()
    for _ in range(6):
        world.tick([sprite, other], 0.016)

    assert sprite.pos == anchored                 # 无限质量：位移恒为 0
    assert sprite.interaction_state == INTERACTION_DRAG
    assert other.pos != anchored                  # 撞人者自己被推开


def test_multi_sprite_only_grabbed_one_is_affected():
    overlay = OverlayWindow()
    grabbed = _make_sprite(pos=(200, 200))
    idle = _make_sprite(pos=(600, 200))
    overlay.add_sprite(idle)
    overlay.add_sprite(grabbed)
    moved, anchored = _enter_aim(overlay, grabbed)
    cursor = _pull(overlay, moved, -60, 0)

    assert overlay.slingshot.sprite is grabbed
    assert idle.interaction_state == INTERACTION_NORMAL
    idle_snapshot = QPointF(idle.pos)

    overlay.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cursor))

    assert grabbed.interaction_state == INTERACTION_THROWN
    assert idle.pos == idle_snapshot              # 旁观者状态/位置都不动
    assert idle.interaction_state == INTERACTION_NORMAL


# ================================================================ 瞄准 UI
def test_preview_geometry_matches_physics_helpers():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    sprite.throw_speed_cap = 4800.0
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -72, 0)
    controller = overlay.slingshot

    pull = QPointF(controller.pull)
    length = controller.pull_length()
    speed = controller.speed()
    assert speed == physics_mod.slingshot_speed(
        length, physics_mod.SLINGSHOT_MIN_DISTANCE * SCALE,
        physics_mod.SLINGSHOT_MAX_DISTANCE * SCALE, 4800.0)

    # 形变系数 = physics.slingshot_deformation 直调（sprite 绘制层未来消费）
    assert controller.deformation() == physics_mod.slingshot_deformation(
        pull.x(), pull.y(), controller.progress())

    # 轨迹 = physics.slingshot_trajectory 原样采样，只做锚点平移
    vx = pull.x() / length * speed
    vy = pull.y() / length * speed
    raw = physics_mod.slingshot_trajectory(vx, vy)
    assert len(controller.trajectory()) == len(raw) == 12
    anchor = controller.trajectory()[0]
    assert controller.trajectory() == [QPointF(anchor.x() + x, anchor.y() + y)
                                       for x, y in raw]
    # 锚点落在身体框边缘：起点在角色朝发射方向那一侧
    body = sprite.body_rect()
    assert anchor.x() >= body.right() - 1


def test_band_starts_at_body_edge_facing_cursor():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)
    cursor = _pull(overlay, moved, -80, 0)        # 光标在左 → 橡皮带从左边起
    band = overlay.slingshot.band()
    assert band is not None
    body = sprite.body_rect()
    assert band[0].x() <= body.left() + 1
    assert band[1] == QPointF(cursor)


def test_paint_only_during_aim_and_leaves_no_residue():
    controller = SlingshotController()
    surface = QImage(200, 120, QImage.Format.Format_ARGB32_Premultiplied)

    def _paint():
        """画一次并返回 (是否有内容, 非透明像素数, alpha=105 的橡皮带像素数)."""
        surface.fill(Qt.GlobalColor.transparent)
        painter = QPainter(surface)
        drew = controller.paint(painter)
        painter.end()
        painted = band_pixels = 0
        for y in range(surface.height()):
            for x in range(surface.width()):
                alpha = (surface.pixel(x, y) >> 24) & 0xFF
                if alpha:
                    painted += 1
                if alpha == 105:      # 橡皮带笔色 alpha（轨迹点按 fade 渐变，不落 105）
                    band_pixels += 1
        return drew, painted, band_pixels

    assert _paint() == (False, 0, 0)              # 未瞄准：一笔不画

    sprite = _make_sprite(pos=(10, 10))
    controller.maybe_enter_on_right_press(sprite, QPointF(40, 40))
    controller.update_pull(QPointF(20, 60))
    drew, painted, band_pixels = _paint()
    assert drew is True and painted > 0           # 瞄准中：橡皮带 + 轨迹
    assert band_pixels > 5                        # 橡皮带确实落笔（角色→光标那条带）

    controller.cancel()
    assert _paint() == (False, 0, 0)              # 取消后零残留


def test_overlay_render_has_no_residue_after_aim():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)

    def _render():
        image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        overlay.render(image)
        return image

    def _pixels(image):
        # PySide6 的 QImage.__eq__ 不比内容：按位比较像素缓冲
        return image.size(), bytes(image.constBits())

    # 基线取「拖拽之后、进瞄准之前」——拖拽本身会挪 sprite，不能拿拖拽前比
    _press, moved = _begin_drag(overlay, sprite)
    before = _pixels(_render())

    overlay.mousePressEvent(_right_press(moved))
    assert overlay.slingshot.aiming
    cursor = _pull(overlay, moved, -70, -20)
    during = _pixels(_render())
    assert during != before                       # 瞄准中确实多画了东西
    assert overlay.slingshot.visual_bounds(overlay.rect()) is not None

    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                    Qt.KeyboardModifier.NoModifier))
    # 取消带回弹 Q 弹（旧 _start_slingshot_rebound 同款 220ms 反馈）：先让它
    # 收势完毕，再要求像素级零残留（瞄准 UI 自己一笔不留）。
    for _ in range(20):
        sprite.advance(0.016)
    assert _pixels(_render()) == before

    # 再点右键取消的路径同样零残留（就地恢复拖拽后不遗留瞄准 UI）
    _press2, moved2 = _begin_drag(overlay, sprite)
    overlay.mousePressEvent(_right_press(moved2))
    assert overlay.slingshot.aiming
    _pull(overlay, moved2, -60, 0)
    overlay.mousePressEvent(_right_press(moved2))
    assert overlay.slingshot.aiming is False
    assert overlay.slingshot.visual_bounds(overlay.rect()) is None


def test_visual_region_is_fine_grained_not_bounding_box():
    """脏区按块取（带宽 + 每个轨迹点的小方框），不是预测弧的外接大矩形。

    预测弧 0.8s × 数千 px/s，外包框能横跨大半屏；按外包框 update 会白刷
    几十个百分点的像素（实测见交付报告：外包框 30-44% vs 精细区 ~0.5-1%）。
    """
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -200, -90)
    controller = overlay.slingshot

    region = controller.visual_region(overlay.rect())
    bounds = controller.visual_bounds(overlay.rect())
    assert not region.isEmpty()

    region_area = sum(rect.width() * rect.height() for rect in region)
    bounds_area = bounds.width() * bounds.height()
    assert region_area > 0
    assert region_area * 8 < bounds_area          # 至少小一个数量级

    # 裁剪到远处一小块（带与弧都不在那儿）：空区域
    assert controller.visual_region(QRect(0, 0, 1, 1)).isEmpty()
    # 未瞄准：区域为空（取消后就是靠它把残留擦干净）
    controller.cancel()
    assert controller.visual_region(overlay.rect()).isEmpty()
    assert controller.visual_bounds(overlay.rect()) is None


def test_visual_dirty_region_is_local_not_full_screen():
    overlay = CountingOverlay()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    overlay.update_calls = 0
    overlay.update_regions.clear()

    moved, _anchored = _enter_aim(overlay, sprite)
    for step in range(1, 6):
        _pull(overlay, moved, -step * 10, -step * 6)

    assert overlay.update_regions, "瞄准 UI 必须走 update(region) 脏区通道"
    area = overlay.rect().width() * overlay.rect().height()
    for region in overlay.update_regions:
        bounds = region.boundingRect()
        assert bounds.width() * bounds.height() < area   # 不是整屏 repaint


def test_removing_aiming_sprite_ends_session():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    moved, _anchored = _enter_aim(overlay, sprite)
    _pull(overlay, moved, -40, 0)

    overlay.remove_sprite(sprite)

    assert overlay.slingshot.aiming is False
    assert overlay.slingshot.sprite is None


# ================================================================ 右键菜单抑制
def test_context_menu_suppressed_for_slingshot_right_press():
    overlay = OverlayWindow()
    sprite = _make_sprite()
    overlay.add_sprite(sprite)
    _moved, _anchored = _enter_aim(overlay, sprite)
    assert overlay._context_menu_suppressed is True

    event = QEvent(QEvent.Type.ContextMenu)       # 只验证 event() 拦截，不弹真菜单
    event.setAccepted(False)
    assert overlay.event(event) is True
    assert overlay._context_menu_suppressed is False
