# -*- coding: utf-8 -*-
"""弹弓蓄力瞄准（单合成窗架构移植）：拖拽中右键进瞄准 / 松左键发射 / Esc·右键取消。

旧窗口路径蓝本（只读参考，本刀不改）：pet/window.py:2987-3087
（``_enter_slingshot`` / ``_update_slingshot_aim`` / ``_cancel_slingshot_to_anchor``
/ ``_cancel_slingshot_to_drag`` / ``_launch_slingshot``）与 paintEvent:2367-2413
（橡皮带 + 轨迹预览）。参数与数学**不复制**：拉拽距离上下限、初速曲线、
各向异性形变、抛物弧采样全部直调 pet/physics.py 的弹弓段
（``SLINGSHOT_MIN_DISTANCE`` / ``SLINGSHOT_MAX_DISTANCE`` / ``slingshot_speed``
/ ``slingshot_deformation`` / ``slingshot_trajectory``）；本模块不新增常量。

坐标系：一律 overlay 局部逻辑坐标——sprite 的 ``pos``/``rect``/``body_rect``
本来就在这个系里，旧机的「虚拟窗口坐标 vs 全局鼠标坐标」两套换算在单窗
架构里不存在，本模块不做任何窗口/全局换算（调用方喂 ``event.position()``）。

sprite 侧只走公有交互协议（``interaction_state`` / ``set_pos`` /
``set_velocity`` / ``on_press`` / ``on_release`` / ``squash``），不碰
``PetSprite`` 私有字段：

- **进入瞄准**：sprite 原样留在拖拽态（``interaction_state == "drag"``，
  位置不动）——旧机进入前的 ``_flush_drag_move`` 在单窗架构里是 no-op：
  ``on_move`` 即时应用跟手位置，没有旧机的合帧 pending 目标可冲。
  拖拽态即**瞄准免疫态**，四项豁免一次到齐：``PetSprite.advance`` 只在
  "normal" 积分、``BehaviorController`` 只在 "normal" 驱动、
  ``ThrowPhysicsController`` 只在 "thrown" 积分、``SpriteCollisionWorld``
  把 "drag" 视为无限质量（``_is_dragging``）——瞄准期间 sprite 定锚不动。
  这一条同时是碰撞世界 ``FLAG_SLINGSHOT_AIMING``（collision.py:35）在单窗
  架构里的等价物：无需新旗标，drag 的无限质量语义已覆盖（测试锁定）。
- **发射**：``on_release`` 收掉拖拽态（协议里唯一公有的收尾口：判 false
  ``_dragging`` + 清轨迹），立刻把位置钉回锚点、写初速、置 "thrown"，
  直接落进既有 sprite_physics 通道（``ThrowPhysicsController.tick`` 下一
  tick 起积分）。
- **取消（Esc）**：``cancel()`` 回锚点 + 回 "normal"
  （旧 ``_cancel_slingshot_to_anchor`` 语义）。
- **取消（再点右键）**：``cancel(resume_drag=True)`` 就地恢复拖拽
  （旧 ``_cancel_slingshot_to_drag`` 语义：``on_release``+``on_press`` 背靠背，
  位置不变、grab 偏移按当前光标重建，不跳位）。

config 热读：``enabled`` 每次进入判定前读 ``config.get("slingshot_enabled", True)``
——设置页开关即时生效，不缓存；沿用既有键，不新增
（docs/SETTINGS-CHANGE-GATES.md「准入」）。

坐标所有者：多窗（混合缩放下每块屏一张合成窗）时同一个控制器被所有窗共享，但
每张窗只有**自己那套局部坐标**。会话因此显式记下接住输入的那张窗（``owner``，
由调用方在进入时传入）：只有它能画瞄准 UI，别的窗画出来只会是错位的幽灵弹弓；
Esc 取消也要路由回它——取消会话只是状态，源窗的 grab/按下状态得由它自己收。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRegion

from . import physics as physics_mod
from .pet_sprite import INTERACTION_DRAG, INTERACTION_NORMAL, INTERACTION_THROWN

# 瞄准 UI 观感（旧 paintEvent:2388/2411-2412 同一组取值：橡皮带 105 alpha、
# 轨迹点 150×fade、半径 2.8→1.45）。只搬观感，不搬物理常量。
BAND_RGBA = (104, 174, 196, 105)
TRAJECTORY_RGB = (104, 174, 196)
TRAJECTORY_ALPHA = 150
TRAJECTORY_RADIUS = 2.8
TRAJECTORY_RADIUS_MIN = 1.45
# 脏区外扩：覆盖带宽与最大的轨迹点半径，保证点/线边缘无残影
VISUAL_MARGIN = 3

# 进入瞄准所需的最小 sprite 协议（公有交互协议面）。缺任一即视为「不支持
# 瞄准」的替身 → overlay 维持 V-13 合成 release 行为（假 sprite / 极简测试
# 替身据此安全跳过，不需要实现整套 sprite 协议）。
AIM_PROTOCOL_ATTRS = ("interaction_state", "pos", "set_pos", "set_velocity",
                      "on_press", "on_release")


def supports_aim(sprite) -> bool:
    """sprite 是否具备瞄准所需的最小公有协议（否则调用方走 V-13 兜底）。"""
    return sprite is not None and all(
        hasattr(sprite, name) for name in AIM_PROTOCOL_ATTRS)


def _point(value) -> QPointF:
    if isinstance(value, QPointF):
        return QPointF(value)
    return QPointF(float(value.x()), float(value.y()))


def trajectory_anchor(character_rect: QRect, launch: QPointF) -> QPointF:
    """角色中心沿 ``launch`` 方向射出可见矩形的交点（旧 window.py:2959-2975 同式）。

    轨迹预览的起点：橡皮带把角色拉向一边，弹射方向是 ``pull`` 的反向……
    注意旧机口径就是「沿 pull 方向」——``pull = 锚点光标 - 当前光标``，
    所以 pull 指向的是**弹射方向**（往后拉、往前飞）。
    """
    if character_rect.isEmpty():
        return QPointF(character_rect.center())
    length = math.hypot(launch.x(), launch.y())
    if length <= 1e-6:
        return QPointF(character_rect.center())
    ux, uy = launch.x() / length, launch.y() / length
    half_width = character_rect.width() / 2.0
    half_height = character_rect.height() / 2.0
    distances = []
    if abs(ux) > 1e-6:
        distances.append(half_width / abs(ux))
    if abs(uy) > 1e-6:
        distances.append(half_height / abs(uy))
    if not distances:
        return QPointF(character_rect.center())
    distance = min(distances)
    center = character_rect.center()
    return QPointF(center.x() + ux * distance, center.y() + uy * distance)


class SlingshotController:
    """一次弹弓瞄准会话（单会话、单 sprite）：状态 + 数学 + 瞄准 UI 绘制。

    生命周期（全部由 overlay 事件侧驱动，见 pet/overlay_window.py 的四路调和）：

    - ``maybe_enter_on_right_press(grab_sprite, cursor, owner=...)``：拖拽中右键 →
      进瞄准；已在瞄准 → 取消（就地恢复拖拽）。返回「本次右键是否被消费」。
      ``owner`` = 接住这次输入的窗口（多窗时只有它画瞄准 UI、只有它的 grab 要收）。
    - ``update_pull(pos)``：瞄准中移动 → 拉拽矢量/进度。
    - ``fire(cursor)``：松左键 → 写初速 + "thrown"。
    - ``cancel()`` / ``cancel(resume_drag=True)``：Esc / 再点右键。
    - ``paint(painter)``：橡皮带 + 轨迹预览（只在瞄准期间有内容）。

    config 注入：``config`` 为任何带 ``get(key, default)`` 的对象（产品壳传
    ``instance.config``）；``config=None`` 时 ``enabled`` 取产品默认值 True。
    壳层可直接 ``overlay.slingshot.config = instance.config`` 或整体替换
    ``overlay.slingshot``。
    """

    def __init__(self, config=None, *, scale: float = 1.0) -> None:
        self._config = config
        self._default_scale = float(scale)
        self._sprite = None
        self._owner = None
        self._anchor = QPointF()
        self._anchor_cursor = QPointF()
        self._cursor = QPointF()
        self._pull = QPointF()
        # 瞄准 UI 几何缓存 (scale, band, trajectory, bounds)：拉拽状态一变就作废
        self._geometry_cache = None

    # ---------------------------------------------------------------- config（热读）
    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, value) -> None:
        self._config = value

    @property
    def enabled(self) -> bool:
        """slingshot_enabled 每次进入判定前热读（设置页开关即时生效；不缓存）。

        无 config（裸 overlay / demo）时取产品默认值 True；键沿用既有
        config 键，不新增、不迁移（docs/SETTINGS-CHANGE-GATES.md）。
        """
        config = self._config
        getter = getattr(config, "get", None)
        if not callable(getter):
            return True
        return bool(getter("slingshot_enabled", True))

    # ---------------------------------------------------------------- 会话查询
    @property
    def aiming(self) -> bool:
        """是否在瞄准会话中（overlay 事件调和与外部查询的唯一判据）。"""
        return self._sprite is not None

    @property
    def sprite(self):
        """被瞄准的 sprite（未瞄准为 None）。"""
        return self._sprite

    @property
    def owner(self):
        """接住这次会话的那个窗口（未瞄准 / 直调控制器的裸用例为 None）。

        多窗共用一个控制器时，它就是"哪张窗负责画 + 哪张窗的 grab 要收"的唯一
        回答；单窗下恒等于唯一的 overlay。
        """
        return self._owner

    @property
    def anchor(self) -> QPointF:
        """锚点：进入瞄准时的 sprite 位置（取消回锚点 / 发射起点）。"""
        return QPointF(self._anchor)

    @property
    def anchor_cursor(self) -> QPointF:
        """锚点光标：进瞄准时的光标位置（拉拽矢量的原点）。"""
        return QPointF(self._anchor_cursor)

    @property
    def cursor(self) -> QPointF:
        """当前光标（橡皮带末端，不随拉拽钳制回缩）。"""
        return QPointF(self._cursor)

    @property
    def pull(self) -> QPointF:
        """拉拽矢量（已钳制到上限）：指向弹射方向，长度 = 力度输入。"""
        return QPointF(self._pull)

    def pull_length(self) -> float:
        return math.hypot(self._pull.x(), self._pull.y())

    def progress(self) -> float:
        """蓄力进度 0..1（旧 window.py:2916-2919 同式：拉拽长度 / 上限）。"""
        maximum = self._max_distance()
        distance = min(self.pull_length(), maximum)
        return max(0.0, min(1.0, distance / max(1.0, maximum)))

    def speed(self) -> float:
        """当前拉拽对应的弹射初速（预览与发射同源，直调 physics.slingshot_speed）。"""
        if self._sprite is None:
            return 0.0
        scale = self._sprite_scale()
        return physics_mod.slingshot_speed(
            min(self.pull_length(), self._max_distance()),
            physics_mod.SLINGSHOT_MIN_DISTANCE * scale,
            physics_mod.SLINGSHOT_MAX_DISTANCE * scale,
            self._speed_cap(),
        )

    def deformation(self) -> tuple[float, float]:
        """各向异性拉伸系数（直调 physics.slingshot_deformation）。

        单窗架构里 sprite 形变属于 pet_sprite 绘制路径（本刀不改），此处把
        旧机的数学留在公开面上供 sprite 绘制层消费，避免下游再抄一份常量。
        """
        if self._sprite is None:
            return 1.0, 1.0
        return physics_mod.slingshot_deformation(
            self._pull.x(), self._pull.y(), self.progress())

    # ---------------------------------------------------------------- 会话进入/退出
    def maybe_enter_on_right_press(self, grab_sprite, cursor_pos=None, *,
                                   owner=None) -> bool:
        """拖拽中收到右键按下：进瞄准（已在瞄准 = 取消并就地恢复拖拽）。

        返回 True = 本次右键被弹弓消费（overlay 不再合成 release / 不弹菜单）；
        False = 不消费，调用方维持既有行为（V-13 合成 release）。

        ``owner`` = 接住这次输入的那张窗（多窗时 ctx 不同）；它只回答"谁画、谁
        收 grab"，不参与会话状态。裸控制器（无窗）传 None，绘制照常。

        不消费的情形：``slingshot_enabled`` 为假（每次判定前热读）且当前
        **没有**会话；grab 对象不具备最小公有协议（假 sprite 替身）。

        "本次按下算不算拖拽"不在这里判：进入资格（确有拖拽资格 / 已在瞄准）由
        overlay 事件侧判定（``_grab_drag_qualified``），本方法只认"给了 grab +
        开关允许"，锁定位 / 未按 SHIFT 的点击候选根本走不到这里。

        已在瞄准时**先**判会话再判 config：瞄准期间设置页把开关关掉，右键
        取消手势仍然必须有效（否则会话会失去退出路径）。
        """
        if self.aiming:
            # 再点右键 = 取消（就地，恢复拖拽）——旧 _cancel_slingshot_to_drag
            self.cancel(resume_drag=True)
            return True
        if not self.enabled:
            return False
        if not supports_aim(grab_sprite):
            return False
        cursor = self._resolve_cursor(grab_sprite, cursor_pos)
        self._sprite = grab_sprite
        self._owner = owner
        self._anchor = QPointF(grab_sprite.pos)
        self._anchor_cursor = QPointF(cursor)
        self._cursor = QPointF(cursor)
        self._pull = QPointF(0, 0)
        self._geometry_cache = None
        # 瞄准免疫态：拖拽态本就免积分/免驱动/无限质量（见模块 docstring）。
        # 幂等写入：真实路径下 sprite 已在 "drag"，这里只做不变量声明。
        self._write(grab_sprite, "set_velocity", QPointF(0, 0))
        grab_sprite.interaction_state = INTERACTION_DRAG
        return True

    def update_pull(self, cursor_pos) -> None:
        """瞄准中光标移动：更新拉拽矢量（钳制到上限；sprite 一步不动）。"""
        if self._sprite is None:
            return
        cursor = _point(cursor_pos)
        pull = self._anchor_cursor - cursor
        maximum = self._max_distance()
        length = math.hypot(pull.x(), pull.y())
        if length > maximum and length > 0.0:
            ratio = maximum / length
            pull = QPointF(pull.x() * ratio, pull.y() * ratio)
        self._cursor = cursor
        self._pull = pull
        self._geometry_cache = None   # 拉拽变化：瞄准 UI 几何缓存作废

    def cancel(self, *, resume_drag: bool = False) -> bool:
        """取消瞄准。返回是否真的取消了一个会话。

        ``resume_drag=False``（Esc）：sprite 回锚点 + 回 "normal"
        ——旧 ``_cancel_slingshot_to_anchor``。
        ``resume_drag=True``（再点右键）：sprite 就地不动、恢复拖拽态
        ——旧 ``_cancel_slingshot_to_drag``（``on_release``+``on_press``
        背靠背：位置不变、grab 偏移按当前光标重建）。
        """
        sprite = self._sprite
        if sprite is None:
            return False
        anchor = QPointF(self._anchor)
        cursor = QPointF(self._cursor)
        progress = self.progress()
        self._clear_session()
        self._write(sprite, "on_release", cursor)   # 协议收尾：不留在 _dragging 悬挂态
        if resume_drag:
            # 就地恢复拖拽：on_release 会顺光标放一下位置，先钉回锚点再重新
            # grab（新 grab 偏移 = 当前光标 - 锚点），位置一步不跳。
            self._write(sprite, "set_pos", anchor)
            self._write(sprite, "on_press", cursor)
            # M3 语义：on_press 只是点击候选（不置 _dragging/拖拽态），真拖拽
            # 态由 begin_drag 升级——瞄准前的拖拽是已升级过的，恢复要补齐。
            self._write(sprite, "begin_drag")
            self._write(sprite, "set_velocity", QPointF(0, 0))
        else:
            self._write(sprite, "set_velocity", QPointF(0, 0))
            self._write(sprite, "set_pos", anchor)
            # 过缝通行：取消收口强制 NORMAL 前先吸附——锚点可在空洞里
            # （瞄准起始位置），on_release 的 drop_gravity→THROWN 兜底被这里
            # 的强制 NORMAL 覆盖，不吸附就会把宠钉死在空洞（k3 盲审 S2）
            snap = getattr(sprite, "_snap_into_screen_if_needed", None)
            if callable(snap):
                snap()
            sprite.interaction_state = INTERACTION_NORMAL
        if progress > 0.0:
            self._write(sprite, "squash")            # 旧 _start_slingshot_rebound 回弹反馈
        return True

    def fire(self, cursor_pos=None) -> bool:
        """松左键发射：按当前拉拽矢量写初速并置 "thrown"（既有 sprite_physics 通道）。

        返回 True = 真的弹射出去了；False = 拉拽不足最小距离，已回锚点
        （旧 ``_launch_slingshot`` 同：低于 SLINGSHOT_MIN_DISTANCE 视为取消）。
        """
        sprite = self._sprite
        if sprite is None:
            return False
        distance = min(self.pull_length(), self._max_distance())
        scale = self._sprite_scale()
        if distance < physics_mod.SLINGSHOT_MIN_DISTANCE * scale:
            self.cancel()
            return False
        pull = QPointF(self._pull)
        length = self.pull_length() or 1.0
        anchor = QPointF(self._anchor)
        cursor = self._resolve_cursor(sprite, cursor_pos)
        speed = self.speed()
        progress = self.progress()
        self._clear_session()
        # 先按协议收掉拖拽态，再把位置钉回锚点：on_release 会顺光标放一下，
        # 同一事件轮次内立刻纠正，不产生可见跳位（脏区由 Qt 合并）。
        self._write(sprite, "on_release", cursor)
        self._write(sprite, "set_pos", anchor)
        self._write(sprite, "set_velocity",
                    QPointF(pull.x() / length * speed, pull.y() / length * speed))
        sprite.interaction_state = INTERACTION_THROWN
        if progress > 0.0:
            self._write(sprite, "squash")            # 旧 _start_slingshot_rebound 回弹反馈
        return True

    # ---------------------------------------------------------------- 瞄准 UI
    def band(self) -> tuple[QPointF, QPointF] | None:
        """橡皮带两端：(角色身体框朝光标一侧的边缘, 当前光标)。

        旧 ``_slingshot_band_points``（window.py:2977-2985）同式：起点 = 角色中心
        沿「中心→光标」方向射出可见矩形的交点；终点 = 真实光标（拉拽超上限时
        也不回缩，观感上「拉到头了」）。
        """
        band = self._geometry()[0]
        return None if band is None else (QPointF(band[0]), QPointF(band[1]))

    def trajectory(self) -> list[QPointF]:
        """预测轨迹采样（旧 paintEvent:2390-2413 语义：拉拽长度→初速→抛物弧）。

        平移自 ``physics.slingshot_trajectory`` 的原始采样（相对弹射起点的弧），
        起点锚在身体框朝弹射方向的边缘——只做平移不改弧，参数与点数都跟
        physics 走（不复制常量）。未达最小拉拽距离时无预览（返回空）。
        """
        return list(self._geometry()[1])

    def visual_bounds(self, clip_rect: QRect | None = None) -> QRect | None:
        """当前瞄准 UI 的外接矩形（overlay 脏区外包框用；未瞄准 = None）。

        ``clip_rect`` 传 overlay 局部矩形时按之交裁剪：高速弹射的预测弧可以
        远出屏幕（0.8s × cap 可达数千像素），脏区必须裁回可见范围——
        既是「不乱 repaint 全屏」的闸门，也是取消后零残留的清除范围。
        """
        bounds = self._geometry()[2] if self._sprite is not None else None
        if bounds is None:
            return None
        if clip_rect is None:
            return QRect(bounds)
        rect = QRect(bounds).intersected(QRect(clip_rect))
        return None if rect.isEmpty() else rect

    def visual_region(self, clip_rect: QRect | None = None) -> QRegion:
        """当前瞄准 UI 的精细脏区（橡皮带矩形 + 每个轨迹点的方框）。

        比 ``visual_bounds`` 的单个外接矩形小几个数量级：预测弧可以横跨大半
        个屏幕，但真正落笔的只有带宽与十几个点——按块取区，overlay 的
        update(region) 只重绘真正会被画到的像素（实测见交付报告的脏区占比）。
        """
        if self._sprite is None:
            return QRegion()
        scale = self._sprite_scale()
        band, points, _bounds = self._geometry()
        clip = QRect(clip_rect) if clip_rect is not None else None
        shapes: list[tuple[float, float, float, float]] = []   # (left, top, w, h)
        if band is not None:
            width = max(1, round(scale)) + VISUAL_MARGIN
            line = QRectF(band[0], band[1]).normalized()
            shapes.append((line.left() - width, line.top() - width,
                           line.width() + 2 * width, line.height() + 2 * width))
        radius = int(math.ceil(TRAJECTORY_RADIUS * scale)) + VISUAL_MARGIN
        size = radius * 2 + 1
        for point in points:
            shapes.append((point.x() - radius, point.y() - radius, size, size))
        rects: list[QRect] = []
        for left, top, span_w, span_h in shapes:
            if clip is not None:
                # 便宜的标量预筛：屏幕外的采样点（预测弧常有）根本不进 QRect/
                # 裁剪，逐块 intersected 是这条路径的主要绑定开销之一
                if (left > clip.right() or top > clip.bottom()
                        or left + span_w <= clip.left() or top + span_h <= clip.top()):
                    continue
            rect = QRect(int(math.floor(left)), int(math.floor(top)),
                         int(math.ceil(span_w)), int(math.ceil(span_h)))
            if clip is not None:
                rect = rect.intersected(clip)
            if not rect.isEmpty():
                rects.append(rect)
        if not rects:
            return QRegion()
        region = QRegion(rects[0])
        for rect in rects[1:]:
            region = region.united(QRegion(rect))
        return region

    def paint(self, painter: QPainter) -> bool:
        """把瞄准 UI 画到 overlay 的 painter 上；返回是否有内容被画。

        只在瞄准会话期间有内容（未瞄准直接返回 False，一笔不画）——取消/
        发射后 overlay 用 ``visual_region`` 的旧区域局部重绘即零残留。
        painter 状态 save/restore 收口，不污染调用方（overlay 每个 sprite
        的绘制都复用同一个 painter）。
        """
        if self._sprite is None:
            return False
        scale = self._sprite_scale()
        band, points, _bounds = self._geometry()
        painter.save()
        try:
            if band is not None:
                painter.setPen(QPen(QColor(*BAND_RGBA), max(1, round(scale))))
                painter.drawLine(band[0], band[1])
            if points:
                painter.setPen(Qt.PenStyle.NoPen)
                last = max(1, len(points) - 1)
                for index, point in enumerate(points):
                    fade = 1.0 - index / last
                    radius = (TRAJECTORY_RADIUS
                              - (TRAJECTORY_RADIUS - TRAJECTORY_RADIUS_MIN)
                              * (1.0 - fade)) * scale
                    painter.setBrush(QColor(TRAJECTORY_RGB[0], TRAJECTORY_RGB[1],
                                            TRAJECTORY_RGB[2],
                                            int(TRAJECTORY_ALPHA * fade)))
                    painter.drawEllipse(point, radius, radius)
        finally:
            painter.restore()
        return True

    def _geometry(self):
        """(band, trajectory, 未裁剪外接矩形) 的懒计算缓存。

        瞄准会话里这三样只由 (锚点, 拉拽矢量, scale, cap) 决定：一次鼠标移动
        算一次，随后同一状态的 paint / 脏区查询直接复用——绘制路径不再重算
        轨迹（实测把每次 move 从 ~100us 降到 ~50us，见交付报告性能段）。
        ``scale`` 参与缓存键：菜单改大小会让几何失效，不能沿用旧值。
        """
        scale = self._sprite_scale()
        cache = self._geometry_cache
        if cache is not None and cache[0] == scale:
            return cache[1], cache[2], cache[3]
        band, points, bounds = self._compute_geometry(scale)
        self._geometry_cache = (scale, band, points, bounds)
        return band, points, bounds

    def _compute_geometry(self, scale: float):
        """按当前状态算橡皮带 / 预测轨迹 / 外接矩形（缓存的唯一生产者）。"""
        character = self._character_rect()
        center = self._center(character)
        direction = self._cursor - center
        if direction.isNull():
            direction = QPointF(-self._pull)
        band = (trajectory_anchor(character, direction), QPointF(self._cursor))

        points: list[QPointF] = []
        if self.pull_length() >= physics_mod.SLINGSHOT_MIN_DISTANCE * scale:
            speed = self.speed()
            length = self.pull_length() or 1.0
            vx = self._pull.x() / length * speed
            vy = self._pull.y() / length * speed
            anchor = trajectory_anchor(character, QPointF(self._pull))
            points = [QPointF(anchor.x() + x, anchor.y() + y)
                      for x, y in physics_mod.slingshot_trajectory(vx, vy)]

        # 外接矩形按标量 min/max 算：QRectF.united 每次新建对象，13 次合并实测
        # 占掉几何计算一半的开销（交付报告性能段有实测对比）。
        left = min(band[0].x(), band[1].x())
        right = max(band[0].x(), band[1].x())
        top = min(band[0].y(), band[1].y())
        bottom = max(band[0].y(), band[1].y())
        radius = TRAJECTORY_RADIUS * scale
        for point in points:
            left = min(left, point.x() - radius)
            right = max(right, point.x() + radius)
            top = min(top, point.y() - radius)
            bottom = max(bottom, point.y() + radius)
        x0 = int(math.floor(left)) - VISUAL_MARGIN
        y0 = int(math.floor(top)) - VISUAL_MARGIN
        x1 = int(math.ceil(right)) + VISUAL_MARGIN
        y1 = int(math.ceil(bottom)) + VISUAL_MARGIN
        return band, points, QRect(x0, y0, x1 - x0, y1 - y0)
    # ---------------------------------------------------------------- 内部
    def _clear_session(self) -> None:
        self._sprite = None
        self._owner = None
        self._anchor = QPointF()
        self._anchor_cursor = QPointF()
        self._cursor = QPointF()
        self._pull = QPointF(0, 0)
        self._geometry_cache = None

    def _resolve_cursor(self, sprite, cursor_pos) -> QPointF:
        """会话光标：显式传入优先（事件侧），否则退当前/角色中心（直调 API）。"""
        if cursor_pos is not None:
            return _point(cursor_pos)
        if self._sprite is not None and not self._cursor.isNull():
            return QPointF(self._cursor)
        if sprite is not None:
            return self._center(self._character_rect_of(sprite))
        return QPointF()

    def _character_rect(self) -> QRect:
        return self._character_rect_of(self._sprite)

    @staticmethod
    def _character_rect_of(sprite) -> QRect:
        """瞄准几何用的角色可见框：body_rect()（body_box×scale，V-2 口径）。"""
        getter = getattr(sprite, "body_rect", None)
        if callable(getter):
            return QRect(getter())
        return QRect(sprite.rect())

    @staticmethod
    def _center(rect: QRect) -> QPointF:
        return QPointF(rect.x() + rect.width() / 2.0, rect.y() + rect.height() / 2.0)

    def _sprite_scale(self) -> float:
        """会话缩放：随被瞄准 sprite 现取（菜单改大小/多 sprite 各自档位）。"""
        value = getattr(self._sprite, "scale", None)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return self._default_scale
        return value if value > 0.0 else self._default_scale

    def _speed_cap(self) -> float:
        """弹射速度软上限：sprite 的 throw_speed_cap（菜单「甩出力度」档位）。"""
        try:
            cap = float(getattr(self._sprite, "throw_speed_cap",
                                physics_mod.MAX_THROW_SPEED))
        except (TypeError, ValueError):
            return physics_mod.MAX_THROW_SPEED
        return cap if cap > 0.0 else physics_mod.MAX_THROW_SPEED

    def _max_distance(self) -> float:
        return physics_mod.SLINGSHOT_MAX_DISTANCE * self._sprite_scale()

    @staticmethod
    def _write(sprite, name: str, *args) -> None:
        """调 sprite 的可选协议方法（squash/on_release 等替身可能没有）。"""
        method = getattr(sprite, name, None)
        if callable(method):
            method(*args)
