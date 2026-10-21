# -*- coding: utf-8 -*-
"""灵动岛 ↔ 进程内碰撞世界桥（4.3 前段 / M-4 装配合一）。

自 ``.scratch/single-overlay-window/run_overlay_demo.py`` 的
``IslandCollisionBridge``（Phase 3c）迁入 pet/ 的产品化版本：demo 改为 import
本模块，装配不再有第二份实现（M-4：消除 demo / overlay_shell 平行宇宙）。

职责（与 demo 版逐点等价）
------------------------
1. **注册**：把岛几何作为碰撞世界静态成员 ``collision.ISLAND_MEMBER_ID``
   登记（``SpriteCollisionWorld.add_static_member``）。坐标 = 岛全局几何 -
   overlay 全局原点（碰撞世界是 overlay 局部坐标，M-3「每屏逻辑坐标」口径）。
2. **更新**：岛几何/显隐变化 → 重登记或注销（岛隐藏不留幽灵墙）。
3. **反馈**：碰撞 listener 识别 pair 含岛成员且冲量 ``j`` 达到世界侧 dv 门的
   冲量等价（``j > world.static_hit_min_dv × 成员质量下界``，见
   ``_impulse_floor``）→ bump 回调 ``(min(3.0, j / 400.0), dir_x, dir_y)``（口径同
   旧架构 ``island_collision._apply_feedback``：strength 封顶 3、dir = 岛被顶的方向）。
   **撞击音效不在这里派发**：同一个 ``SpriteSoundPlayer`` 由壳注册在世界侧
   （``overlay_shell._build`` 的 ``collision.add_collision_listener``），世界扇出
   已覆盖岛击事件，桥内再调一次 = 每个岛击两遍 ``on_collision``（``sound=`` 入参
   因此只保留调用点兼容）。另有可选 ``kinetic`` 回调：岛在动时同步唤醒 tick 档位
   （M-1，见 ``_notify_kinetic``），与可选 ``drag_state`` 回调：岛侧拖拽状态翻转时
   转达给壳（overlay 穿透轮询降档，与拖鱼同款，见 ``_notify_drag_state``）。
   进程内直连，旧架构「远端墙无信号」死结天然消失。

产品化增强（demo 版没有）
----------------------
- **stadium 胶囊圆链**：静态成员碰撞体走 ``sprite_collision.capsule_circles``
  的体育场等效圆链（4bfa6f3 / D9 口径），对齐旧架构
  ``island_collision._island_stadium``。demo 版只给矩形（世界回退内切三圆），
  宽扁岛（如 400×80）胶囊中段存在可穿入空档；
- **幂等**：重复 ``attach``/``detach``/``update_geometry`` 不产生第二份监听器
  或重复注册；同几何重复同步不再打脏碰撞世界（静止豁免依赖 ``_static_dirty``，
  无变化的重复标脏会让静止期白跑多体求解）；
- **防御**：未喂几何 = 不注册；宽/高 ≤ 0、坐标非有限值（NaN/±inf）、非数值
  一律视为不可注册并撤下既有墙；岛对象/回调异常静默降级（岛本体功能不受影响）；
- **屏迁移**：``IslandWindowBridge.set_origin`` 更新 overlay 全局原点后重算
  局部几何（overlay 重建/换屏时壳层调用，气泡跟随器 ``set_origin`` 同款）。
  ``IslandWindowBridge.rebind`` 换**宿主**（岛跨到另一块屏：碰撞世界与原点一起
  换）——混合缩放下一块屏一张合成窗、各有自己的世界；壳经 ``on_sync`` 挂点
  每次几何同步前判断岛是否换屏，换了才重绑（见 ``overlay_shell._island_screen_host``）。

Qt 耦合面（写清）
----------------
- ``IslandCollisionBridge`` 是核心桥，**模块级零 PySide6 import**：几何经
  ``update_geometry(left, top, width, height)`` 参数喂入，可在没有
  QApplication 的纯逻辑测试里跑（本模块只 import 标准库 + ``.collision`` /
  ``.sprite_collision``）。
- ``IslandWindowBridge`` 是薄适配层，面向 QWidget 形态的岛
  （``pet/dynamic_island.DynamicIsland``）：读 ``geometry()/isVisible()``、
  挂无参几何回调 ``on_geometry_changed``、装 Show/Hide/Move/Resize 事件过滤器，
  把全局坐标换算成本地坐标后喂核心桥。唯一 Qt 落点是惰性构造的事件过滤器
  QObject（``_build_island_event_filter``，模块 import 期不触碰 Qt）。
- 岛若不用 QWidget（自绘 / 远端几何），直接接核心桥：``attach(world)`` +
  ``update_geometry(...)``，Qt 侧零依赖。
- **单槽回调警告**：``island.on_geometry_changed`` 是单槽（旧路径
  ``app.py`` 用它接 ``IslandCollisionBody.submit``）——overlay 拓扑下由本桥
  占用，旧果冻墙路径不得与同一个岛同时挂（覆盖时有 WARNING 日志）。
  ``on_pet_visibility_changed`` 是另一个槽，本桥不碰。

壳层接线（本刀不碰壳层文件）：见交付报告「主人接线清单」。
"""
from __future__ import annotations

import logging
import math
import time

from .. import collision
from ..sprite_collision import STATIC_HIT_MIN_DV, capsule_circles

logger = logging.getLogger(__name__)

__all__ = ["IslandCollisionBridge", "IslandWindowBridge"]

# bump 强度口径（旧架构 island_collision._apply_feedback: bump(min(3.0, j/400.0))）
BUMP_STRENGTH_DIVISOR = 400.0
BUMP_STRENGTH_MAX = 3.0

# 岛速估计参数（口径整体沿用 island_collision._update_motion:204-266）：
# 拖岛拍鱼靠岛速进求解器的相对法向速度，岛速恒 0 就只剩位置分离（平推）。
# - 采样最小间隔（s）：拖拽时几何回调可达 100Hz+，dt 常低于它——"跳过"而不是
#   "清零并刷新采样点"，否则拖拽全程岛速恒 0（实机教训）；
# - 有效采样间隔上限（s）：超过它按首次接触处理，不估计；
# - 瞬移跳变守卫：位移超过"极速×间隔 + 岛宽"即视为瞬移（换屏/夹回）；
# - 采样最小位移（px）：低于它视为静止；
# - 岛速上限（px/s）：仅拖拽中的快速甩动是合法拍鱼，钳到上限。
_MOTION_MIN_DT = 0.01
_MOTION_MAX_DT = 0.5
_MOTION_JUMP_SPEED = 3000.0
_MOTION_MIN_STEP = 1.0
_MAX_ISLAND_SPEED = 1500.0

# 碰撞世界成员质量下界（px/s 换算用）：直接向 collision.calculate_mass 要下钳值
# （scale→0 时返回它的钳制下界 0.5），质量区间调整时桥侧的冲量门自动跟随，
# 不复制魔数。见 _impulse_floor。
_MIN_MEMBER_MASS = collision.calculate_mass(0.0, 0.0, scale=0.0)


def _validated_geometry(left, top, width, height):
    """几何校验：返回 ``(l, t, w, h)`` 或 ``None``（不可注册）。

    防御面：非数值（含 None）→ None；非有限值（NaN/±inf）→ None；
    宽/高 ≤ 0 → None（负尺寸绝不进碰撞世界，否则圆链/半径全乱）。
    """
    try:
        l, t, w, h = float(left), float(top), float(width), float(height)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (l, t, w, h))):
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    return (l, t, w, h)


def _local_geometry(geometry, origin_x: float, origin_y: float):
    """岛全局几何 - overlay 全局原点 → 碰撞世界（overlay 局部）几何。

    ``geometry`` 按鸭子类型消费（QRect 或任何暴露 x()/y()/width()/height()
    的对象），本函数因此不引入 Qt 依赖。
    """
    return (float(geometry.x()) - float(origin_x),
            float(geometry.y()) - float(origin_y),
            float(geometry.width()), float(geometry.height()))


class IslandCollisionBridge:
    """核心桥（零 Qt）：把岛几何注册为碰撞世界静态成员 + 撞击反馈。

    用法::

        bridge = IslandCollisionBridge(bump=island.bump)
        bridge.attach(world)                     # 幂等
        bridge.update_geometry(left, top, w, h)  # 几何经参数喂入（局部坐标）
        bridge.set_visible(False)                # 岛隐藏 → 撤墙
        bridge.detach()                          # 退出收口：零残留

    几何状态与可见性在 ``detach`` 后保留：再次 ``attach`` 会按最新状态复墙。
    """

    def __init__(self, *, bump=None, sound=None, member_id: str | None = None,
                 island=None, clock=None, kinetic=None, drag_state=None) -> None:
        # bump 反馈挂点：callable(strength, dir_x, dir_y)；异常静默降级
        self._bump = bump
        # 音效入参保留**只为调用点兼容**（``overlay_shell.attach_island`` 仍传
        # ``sound=self._sound``）：本桥不再自行派发撞击音效——同一个
        # ``SpriteSoundPlayer`` 已由壳注册在世界侧（``overlay_shell._build`` 的
        # ``collision.add_collision_listener(self._sound.on_collision)``），世界
        # 扇出必然覆盖岛击事件；桥内再调一次 = 每个岛击事件两遍 ``on_collision``
        # （精灵解析/配置读取做两遍，跨过 80ms 节流窗口就是双响）。
        self._sound = sound
        # kinetic 挂点：callable()，岛在动时同步唤醒 tick 档位（M-1 的
        # ``TickDriver.note_kinetic``）；异常静默降级。
        self._kinetic = kinetic
        # drag_state 挂点：callable(dragging)，岛侧拖拽状态**翻转**时调用——壳把
        # 它接到 overlay 的逐像素穿透轮询上，与拖鱼同款降到 100ms 档；异常静默降级。
        self._drag_state = drag_state
        # 已转达给外部的拖拽状态（去重；False = 当前没占着降频档，不回发 False）
        self._drag_reported = False
        self._member_id = str(member_id or collision.ISLAND_MEMBER_ID)
        # 岛对象（可空，鸭子类型）：岛速守卫读它的 ``_geo_to``（几何动画中）与
        # ``_dragging``（拖拽中）。直接喂几何的非 QWidget 岛可不传（视为
        # 非动画、非拖拽）。
        self._island = island
        # 采样时钟：默认 time.monotonic；测试注入假钟即可确定性推进，不 sleep。
        self._clock = clock if callable(clock) else time.monotonic
        self._world = None
        self._visible = True
        self._geometry: tuple[float, float, float, float] | None = None
        # _applied 含岛速：岛停下时 rect 不变但速度要归零，速度必须参与比较
        # （否则早退分支留住旧速度，岛停了还在拍鱼）。
        self._applied: tuple[float, float, float, float, float, float] | None = None
        # 岛速估计采样状态（见 _update_motion）
        self._last_center: tuple[float, float] | None = None
        self._last_motion_ts = 0.0
        self._last_size: tuple[float, float] | None = None
        # 上一次观测到的岛侧拖拽状态（True/False/None=岛没这个属性）：用来识别
        # 「拖拽 → 松手」这一状态切换（见 _update_motion 的松手闸）。不随
        # reset_motion 清空——它是对岛的观测，不是采样基线。
        self._last_dragging: bool | None = None
        self._vx = 0.0
        self._vy = 0.0
        self.bumps = 0

    # ---------------------------------------------------------------- 只读状态
    @property
    def member_id(self) -> str:
        """碰撞世界成员 id（默认 ``collision.ISLAND_MEMBER_ID``）。"""
        return self._member_id

    @property
    def attached(self) -> bool:
        """是否已挂接碰撞世界（监听器在线）。"""
        return self._world is not None

    @property
    def registered(self) -> bool:
        """当前是否有生效的静态成员（可见 + 几何有效）。"""
        return self._applied is not None

    @property
    def geometry(self) -> tuple[float, float, float, float] | None:
        """最近一次喂入且校验通过的几何（局部坐标）；None = 未喂/无效。"""
        return self._geometry

    # ---------------------------------------------------------------- 生命周期
    def attach(self, world) -> None:
        """挂接碰撞世界（幂等）：注册撞击监听器并按已知几何建墙。

        重复 ``attach`` 同一 world 不会产生第二份监听器；换 world 会先清掉旧
        world 的残留（同 ``detach`` 语义），保证同一时刻只挂一个世界。
        """
        if world is None:
            return
        if self._world is not None and self._world is not world:
            self.detach()
        self._world = world
        self._applied = None  # 注册态与当前 world 重新对齐
        world.add_collision_listener(self.on_collision)
        self._sync()

    def detach(self) -> None:
        """摘下监听器与静态成员（幂等，零残留）；几何/可见性状态保留。"""
        world, self._world = self._world, None
        self._applied = None
        # 拖拽中断路径：摘线（岛重建/退出收口）时交回穿透轮询档位，否则 overlay
        # 会永久停在 100ms 档（岛不再发几何回调，桥侧无从纠正）
        self._notify_drag_state(False)
        self.reset_motion()  # 摘线后重挂从静止起步，不把旧岛速带进新世界
        if world is None:
            return
        try:
            world.remove_collision_listener(self.on_collision)
        except Exception:
            logger.debug("island_bridge: 摘除撞击监听器失败", exc_info=True)
        try:
            world.remove_static_member(self._member_id)
        except Exception:
            logger.debug("island_bridge: 摘除岛静态成员失败", exc_info=True)

    # ---------------------------------------------------------------- 几何/显隐
    def update_geometry(self, left, top, width, height) -> bool:
        """喂入岛在碰撞世界坐标系（overlay 局部）里的几何并同步注册。

        返回几何是否有效（有效 = 已按当前可见性登记；无效 = 不作为墙，既有
        注册一并撤下）。

        本入口也是岛速采样点（``sync_geometry``/``update_geometry`` 汇聚处）：
        估计出的 (vx, vy) 随 ``_sync`` → ``add_static_member`` 一并传给世界，
        求解器才有相对接近速度（拖岛拍鱼 = 冲量弹开，而不是只做位置分离）。
        """
        rect = _validated_geometry(left, top, width, height)
        self._geometry = rect
        if rect is None:
            self.reset_motion()
        else:
            self._update_motion(rect)
        self._sync()
        return rect is not None

    def set_visible(self, visible: bool) -> None:
        """岛显隐：隐藏 → 撤墙（不留幽灵墙），显示 → 按最近几何复墙。"""
        self._visible = bool(visible)
        if not self._visible:
            # 拖拽中断路径：拖拽中隐藏（托盘隐藏/全屏避让/换屏重建）后岛不再发
            # 几何回调 → 必须在这里交回穿透轮询档位
            self._notify_drag_state(False)
            self.reset_motion()  # 隐藏期间不残留墙速，复墙从静止起步
        self._sync()

    def _sync(self) -> None:
        """把「可见 + 几何有效」的目标状态落到碰撞世界（无变化则不打脏）。

        目标态含岛速：同 rect 但速度变化（岛停下/起步）也必须重登记，否则
        早退分支会把旧速度留在世界里继续拍鱼。
        """
        world = self._world
        if world is None:
            return
        target = None
        if self._visible and self._geometry is not None:
            target = (*self._geometry, self._vx, self._vy)
        if target == self._applied:
            return  # 幂等：同状态重复同步不重复登记、不打脏静止豁免
        try:
            if target is None:
                world.remove_static_member(self._member_id)
            else:
                left, top, width, height, vx, vy = target
                world.add_static_member(
                    self._member_id, left, top, width, height,
                    circles=capsule_circles(left, top, width, height),
                    vx=vx, vy=vy)
        except Exception:
            logger.debug("island_bridge: 岛静态成员同步失败", exc_info=True)
            return
        self._applied = target

    # ---------------------------------------------------------------- 岛速估计
    def reset_motion(self) -> None:
        """作废岛速采样基线并清零（换屏/原点迁移等坐标系跳变后调用）。"""
        self._vx = 0.0
        self._vy = 0.0
        self._last_center = None
        self._last_motion_ts = 0.0
        self._last_size = None

    def _island_drag_state(self) -> bool | None:
        """岛侧自报的拖拽状态：True 拖拽中 / False 已松手 / None 无从判断。

        读 ``DynamicIsland._dragging``（``mousePressEvent`` 置 False、
        ``mouseMoveEvent`` 起手后置 True、``mouseReleaseEvent`` 在发几何回调
        **之前**置回 False）。岛对象没有这个属性时返回 None（非 QWidget 岛、
        直接喂几何的假对象）——调用方据此退回纯几何判据，不因缺属性误判。
        """
        dragging = getattr(self._island, "_dragging", None)
        if dragging is None:
            return None
        return bool(dragging)

    def _update_motion(self, rect: tuple[float, float, float, float]) -> None:
        """从相邻两次几何采样估计岛速（口径对齐 island_collision._update_motion）。

        守卫逐条（旧代码注释记载的实机教训，一条都不能少）：

        ① 几何动画期间（岛 ``_geo_to`` 非 None）速度清零不采样——展开动画
           峰值约 2600px/s，会把旁边静止的鱼凭空拍飞；
        ② 尺寸变化（展开/收起卡片、停靠切换、文本变长）只重置采样点不估计；
        ③ dt<0.01s 的过密样本"跳过"而不是清零——拖拽几何回调可达 100Hz+，
           清零刷新采样点会让拖拽全程岛速恒 0（实机教训）；其中「同 rect」样本
           只在岛没在拖拽时才当停速（拖拽中的同 rect = 同一次 move 的重复上报，
           见 ``_island_drag_state``）；
        ③′ 松手闸：「拖拽 → 松手」是状态切换而不是「岛在动」，该拍位移（夹回
           屏幕/落位）清零 **并作废采样基线**——否则后面第一份非拖拽几何上报会
           拿拖拽期的旧采样点算出「跨松手的过期位移 / 间隔」当岛速（幽灵撞击）；
           常态非拖拽运动（自动动画、落位、设置改位）不归这条管，语义不变；
        ④ 瞬移跳变守卫：位移超过"极速×间隔 + 岛宽"即视为瞬移，清零重置；
        ⑤ 仅拖拽中允许把超速钳到 ``_MAX_ISLAND_SPEED``；非拖拽的极速位移
           必是瞬移/尺寸变化残留，清零重置。
        """
        # 岛侧拖拽状态先记账（任何早退分支都不许漏记，否则识别不出松手切换）；
        # 同时把状态翻转转达给外部（overlay 穿透轮询降档，见 _notify_drag_state）
        dragging = self._island_drag_state()
        released = dragging is False and self._last_dragging is True
        if dragging is not None:
            self._last_dragging = dragging
            self._notify_drag_state(dragging)
        if getattr(self._island, "_geo_to", None) is not None:
            self.reset_motion()
            return
        if released:
            # 松手闸（状态切换）：拖拽结束后的第一拍位移是岛侧夹回屏幕/落位，
            # 不是用户拖动 → 清零 + 作废采样基线（v 也归零），避免过期延迟把
            # 这段位移算成岛速。
            self.reset_motion()
            return
        left, top, w, h = rect
        size = (w, h)
        if self._last_size is not None and size != self._last_size:
            self._vx = self._vy = 0.0
            self._last_center = None
            self._last_size = size
            return
        self._last_size = size
        cx, cy = left + w / 2.0, top + h / 2.0
        now = self._clock()
        if self._last_center is not None:
            dt = now - self._last_motion_ts
            dx, dy = cx - self._last_center[0], cy - self._last_center[1]
            jump = math.hypot(dx, dy)
            if jump >= _MOTION_MIN_STEP:
                # 「岛在动」：相邻两次采样之间真的位移了（死区内外的样本都算，
                # 只看位移不看是否估出速度）→ 同步唤醒 tick 档位。桌宠静止时
                # tick 已降到 T2/T3（不跑仿真），岛拖拽是几何回调驱动的用户
                # 输入、不经过 overlay 的鼠标事件，没有这条通道甩岛过鱼会整个
                # 手势期间不结算（见 _notify_kinetic）。
                self._notify_kinetic()
            if dt < _MOTION_MIN_DT:
                # 高频回调样本太密：不刷新采样点。同 rect（岛没动）是否清零看岛
                # 侧拖拽状态——拖拽中它是同一次 move 经 ``move()`` 的 Move 事件与
                # ``_emit_geometry_changed()`` 的重复上报，清零会让拖拽全程岛速
                # 恒 0、撞击 j=0 无反馈（用户反馈「拖岛快速撞鱼没反馈」的根因）；
                # 非拖拽 / 取不到拖拽状态（非 QWidget 岛）时沿用既有判据。
                if jump < _MOTION_MIN_STEP and dragging is not True:
                    self._vx = self._vy = 0.0
                return
            if jump > _MOTION_JUMP_SPEED * dt + w:
                self._vx = self._vy = 0.0
                self._last_center = (cx, cy)
                self._last_motion_ts = now
                return
            if dt <= _MOTION_MAX_DT and jump >= _MOTION_MIN_STEP:
                vx, vy = dx / dt, dy / dt
                speed = math.hypot(vx, vy)
                if speed > _MAX_ISLAND_SPEED:
                    if getattr(self._island, "_dragging", False):
                        # 拖拽中的快速甩动是合法拍鱼：钳到上限
                        vx *= _MAX_ISLAND_SPEED / speed
                        vy *= _MAX_ISLAND_SPEED / speed
                    else:
                        self._vx = self._vy = 0.0
                        self._last_center = (cx, cy)
                        self._last_motion_ts = now
                        self._last_size = size
                        return
                self._vx, self._vy = vx, vy
            else:
                self._vx = self._vy = 0.0
        self._last_center = (cx, cy)
        self._last_motion_ts = now

    def _notify_drag_state(self, dragging: bool) -> None:
        """岛侧拖拽状态**翻转** → 外部（overlay 逐像素穿透轮询降档）。

        只在翻转时转达：拖拽中的几何回调可达 100Hz+（一次 ``mouseMoveEvent`` 经
        ``move()`` 的 Move 事件与随后的显式几何回调喂桥两遍），每个样本都转达
        等于每个样本重设一次轮询间隔。未置起过降频档时不回发 False（无从复位）。
        先记账再回调：回调抛异常不重发，与 bump/kinetic 挂点同纪律。
        """
        if dragging == self._drag_reported:
            return
        self._drag_reported = dragging
        callback = self._drag_state
        if not callable(callback):
            return
        try:
            callback(dragging)
        except Exception:
            logger.debug("island_bridge: 岛拖拽状态转达失败", exc_info=True)

    # ---------------------------------------------------------------- 撞击反馈
    def _hit_floor(self) -> float:
        """世界侧真撞击门（dv，px/s）= 静态成员放宽阈值（对齐 demo/旧架构）。"""
        world = self._world
        value = getattr(world, "static_hit_min_dv", STATIC_HIT_MIN_DV)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(STATIC_HIT_MIN_DV)

    def _impulse_floor(self) -> float:
        """桥侧冲量门 = 世界侧 dv 门 × 成员质量下界（见 ``_MIN_MEMBER_MASS``）。

        世界判真撞击用的是 **dv**（相对法向速度，``sprite_collision`` 的
        ``static_hit_min_dv``），而 ``CollisionEvent`` 只带冲量 ``j``——
        两者的换算是 ``j = dv × mass``（``collision.solve_collision_impulse``：
        岛无限质量时 ``jn = -(1+e)·vn / (1/m_鱼)``，故 ``dv = j / m_鱼``）。
        拿 j 直接比 dv 门（本桥旧写法）等于把门按质量放大：轻量小鱼
        （``collision.calculate_mass`` 的下钳 0.5）的岛击 j = dv×0.5 会被吞掉
        （世界已按 dv 放行、事件都发了，桥却不反馈：无 bump、无音效）。

        用质量**下界**换算 ⟹ 世界放行的任何事件都不会在桥侧被二次吞掉；
        取严格不等是因为 ``j = 30`` 恰好是「最轻成员恰好踩在 dv 门上」的冲量，
        边界由世界侧的 ``>=`` 独占判定。
        """
        return self._hit_floor() * _MIN_MEMBER_MASS

    def on_collision(self, event) -> None:
        """碰撞 listener：pair 含岛且冲量过门 → bump 回调。

        未挂接（detach 后）直接返回：摘下的桥不再对外发反馈。门见
        ``_impulse_floor``（与世界侧 dv 门同口径，不用冲量直接比 dv）。
        音效**不在这里派发**：同一个 ``SpriteSoundPlayer`` 由壳注册在世界侧
        （``overlay_shell._build``），世界扇出覆盖本入口收到的每个事件；桥内
        再调一次就是每个岛击两遍 ``on_collision``（见 ``__init__`` 的音效入参）。
        """
        if self._world is None:
            return
        if self._member_id not in (event.a, event.b):
            return
        j = float(getattr(event, "j", 0.0) or 0.0)
        if j <= self._impulse_floor():
            return  # 轻触不反馈（形变纪律同现架构）
        # 岛被顶的方向 = 从对方指向岛（island_collision 从对方 dv 取反的口径
        # 在进程内等价于碰撞法线方向）
        if event.b == self._member_id:
            dir_x, dir_y = event.nx, event.ny
        else:
            dir_x, dir_y = -event.nx, -event.ny
        self._fire_bump(j, float(dir_x), float(dir_y))

    def _notify_kinetic(self) -> None:
        """岛在动 → 同步唤醒 tick 档位（M-1 的 ``TickDriver.note_kinetic``）。

        桌宠静止时 tick 会降到 T2（250ms 心跳）甚至 T3（不跑仿真）；岛拖拽是
        几何回调驱动的用户输入，不经过 overlay 的鼠标事件（岛是独立顶层窗），
        没有这条通道，拖岛撞鱼可能整个手势期间一次碰撞结算都没发生。异常静默
        降级（岛本体功能不受影响），与音效/bump 挂点同纪律。
        """
        callback = self._kinetic
        if not callable(callback):
            return
        try:
            callback()
        except Exception:
            logger.debug("island_bridge: tick 档位唤醒失败", exc_info=True)

    def _fire_bump(self, j: float, dir_x: float, dir_y: float) -> None:
        callback = self._bump
        if not callable(callback):
            return
        try:
            callback(min(BUMP_STRENGTH_MAX, j / BUMP_STRENGTH_DIVISOR), dir_x, dir_y)
            self.bumps += 1
        except Exception:
            logger.debug("island_bridge: 岛 bump 反馈失败", exc_info=True)


def _build_island_event_filter(bridge: "IslandWindowBridge"):
    """惰性构造岛事件过滤器（PySide6 只在这个函数体内落地）。

    模块 import 期不触碰 Qt：核心桥与纯逻辑测试无需 Qt 环境。返回的 QObject
    监听岛的 Show/Hide/Move/Resize——``on_geometry_changed`` 只覆盖岛自己的
    几何弹跳/停靠动画，原生 show/hide/move 得靠事件过滤补齐（demo 口径）。
    """
    from PySide6.QtCore import QEvent, QObject

    class _IslandEventFilter(QObject):
        _GEOMETRY_EVENTS = (
            QEvent.Type.Show, QEvent.Type.Hide,
            QEvent.Type.Move, QEvent.Type.Resize,
        )

        def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
            if obj is bridge.island and event.type() in self._GEOMETRY_EVENTS:
                bridge.sync_geometry()
            return False

    return _IslandEventFilter()


class IslandWindowBridge:
    """Qt 适配层：QWidget 形态的岛（DynamicIsland）→ 零 Qt 核心桥。

    构造即接线（口径同 demo Phase 3c）：``island.on_geometry_changed`` 挂
    ``sync_geometry``、装 Show/Hide/Move/Resize 事件过滤器，把岛全局几何 -
    overlay 原点换算成本地几何喂核心桥。``world`` 给了就 ``attach``（默认
    bump = ``island.bump``）；``overlay`` 给了就以其 ``geometry().topLeft()``
    为原点（``overlay=None`` 时原点为 (0,0)，岛几何即局部几何）。
    ``kinetic`` 透传核心桥：岛在动时唤醒 tick 档位（M-1，壳层传
    ``driver.note_kinetic``）。岛侧拖拽状态同样透传（``drag_state`` 口）：
    壳层给了 overlay 时，桥把拖拽翻转接到它的逐像素穿透轮询上，与拖鱼同款降档
    （见 ``_sync_island_drag``）。

    本类本身不是 QObject：Qt 只出现在惰性事件过滤器里（见
    ``_build_island_event_filter``），岛按鸭子类型消费（geometry/isVisible/
    bump/installEventFilter/removeEventFilter）。
    """

    def __init__(self, island, world=None, overlay=None, *, sound=None,
                 bump=None, member_id: str | None = None, clock=None,
                 kinetic=None, on_sync=None) -> None:
        self._island = island
        # overlay 引用：几何原点来源 + 拖拽期穿透轮询降档的落点（见
        # _sync_island_drag）。None 时降档通道静默降级。
        self._overlay = overlay
        origin = overlay.geometry().topLeft() if overlay is not None else None
        self._origin = (float(origin.x()), float(origin.y())) if origin is not None else (0.0, 0.0)
        # on_sync 挂点：callable()，每次几何/显隐同步**之前**转达——壳据此判断岛
        # 是否换了屏并把墙换到新宿主（见 rebind）。异常静默降级。
        self._on_sync = on_sync
        self._filter = None
        self._filter_installed = False
        self._core = IslandCollisionBridge(
            bump=bump if bump is not None else self._bump_island,
            sound=sound, member_id=member_id, island=island, clock=clock,
            kinetic=kinetic, drag_state=self._sync_island_drag)
        self._wire_island()
        if world is not None:
            self.attach(world)
        else:
            self.sync_geometry()

    # ---------------------------------------------------------------- 只读状态
    @property
    def island(self):
        """被桥接的岛（事件过滤器用它做归属判定）。"""
        return self._island

    @property
    def core(self) -> IslandCollisionBridge:
        """零 Qt 核心桥（高级用法：直接喂几何/查注册态）。"""
        return self._core

    @property
    def member_id(self) -> str:
        return self._core.member_id

    @property
    def attached(self) -> bool:
        return self._core.attached

    @property
    def registered(self) -> bool:
        return self._core.registered

    @property
    def bumps(self) -> int:
        """撞击反馈命中计数（demo/指标口径）。"""
        return self._core.bumps

    # ---------------------------------------------------------------- 接线
    def _bump_island(self, strength: float, dir_x: float, dir_y: float) -> None:
        self._island.bump(strength, dir_x, dir_y)

    def _sync_island_drag(self, dragging: bool) -> None:
        """岛侧拖拽状态 → overlay 逐像素穿透轮询降档（与拖鱼同款 100ms 档）。

        岛是独立顶层窗，overlay 收不到它的鼠标事件（``_press_global`` 恒 None）：
        不喂这条通道，拖岛全程 overlay 保持 10ms 全量逐像素判定，而拖鱼早已降到
        100ms（``WindowsPerPixelInputController.set_drag_active``）。

        鱼拖拽正在进行（``overlay._press_global`` 非空）时不抢档：两条拖拽源共用
        一个轮询档位，岛侧松手若直接 ``set_drag_active(False)`` 会把仍在拖拽的鱼
        打回 10ms 档——档位归属交给鱼侧自己的按-放循环。异常静默降级（岛/桌宠
        功能不受影响），与 bump/kinetic 挂点同纪律。
        """
        overlay = self._overlay
        controller = getattr(overlay, "_input_controller", None)
        if controller is None:
            return  # 无 overlay / 非 Windows / 宿主未建控制器：通道静默降级
        if getattr(overlay, "_press_global", None) is not None:
            return
        try:
            controller.set_drag_active(bool(dragging))
        except Exception:
            logger.debug("island_bridge: 岛拖拽降档同步失败", exc_info=True)

    def _wire_island(self) -> None:
        """挂岛的几何回调 + 事件过滤器（幂等；异常静默降级）。"""
        try:
            previous = getattr(self._island, "on_geometry_changed", None)
            if callable(previous) and previous != self.sync_geometry:
                # 单槽回调被外来方占用：旧 island_collision 路径与 overlay 拓扑
                # 不能同挂一个岛（壳层接线须二选一），这里给出可诊断的告警
                logger.warning("island_bridge: 覆盖岛既有 on_geometry_changed=%r"
                               "（单槽回调，旧果冻墙路径不应与本桥同挂）", previous)
            self._island.on_geometry_changed = self.sync_geometry
        except Exception:
            logger.debug("island_bridge: 挂岛几何回调失败", exc_info=True)
        if self._filter is None:
            try:
                self._filter = _build_island_event_filter(self)
            except Exception:
                self._filter = None  # 无 Qt 环境：几何回调路径仍可用
                logger.debug("island_bridge: 事件过滤器构造失败", exc_info=True)
        if self._filter is not None and not self._filter_installed:
            try:
                self._island.installEventFilter(self._filter)
                self._filter_installed = True
            except Exception:
                logger.debug("island_bridge: 装岛事件过滤器失败", exc_info=True)

    def _unwire_island(self) -> None:
        """摘线：过滤器移除 + 几何回调还原（只还原成我方挂的那个）。"""
        if self._filter is not None and self._filter_installed:
            try:
                self._island.removeEventFilter(self._filter)
            except Exception:
                logger.debug("island_bridge: 摘岛事件过滤器失败", exc_info=True)
            self._filter_installed = False
        try:
            if getattr(self._island, "on_geometry_changed", None) == self.sync_geometry:
                self._island.on_geometry_changed = None
        except Exception:
            logger.debug("island_bridge: 还原岛几何回调失败", exc_info=True)

    # ---------------------------------------------------------------- 生命周期
    def attach(self, world) -> None:
        """挂接碰撞世界（幂等）：核心桥 attach + 岛接线在线 + 立即对齐几何。"""
        self._core.attach(world)
        self._wire_island()
        self.sync_geometry()

    def detach(self) -> None:
        """摘线 + 摘碰撞世界（幂等，零残留）；再次 attach 可恢复。"""
        self._unwire_island()
        self._core.detach()

    def close(self) -> None:
        """退出收口别名（demo/壳层口径同 ``detach``）。"""
        self.detach()

    # ---------------------------------------------------------------- 几何/反馈
    def set_origin(self, origin) -> None:
        """overlay 重建/换屏后更新全局原点并重算局部几何（鸭子类型 x()/y()）。

        坐标系跳变不是岛在动：先作废岛速采样基线，避免把原点平移误当成
        岛速（否则换屏瞬间会把旁边的鱼拍飞）。
        """
        self._origin = (float(origin.x()), float(origin.y()))
        self._core.reset_motion()
        self.sync_geometry()

    def rebind(self, world, overlay=None) -> None:
        """换宿主（岛跨到另一块屏）：世界与坐标原点**一起**换。

        混合缩放下一块屏一张合成窗、每张窗一份碰撞世界与局部原点（见
        ``OverlayShell._multi_window``），所以"墙留在原世界"= 岛在这块屏上不存在：
        这屏的宠直接穿过去。``overlay`` 给了就同时换原点来源，否则沿用旧原点
        （同一坐标系里换世界，例如世界重建）。

        换世界后立即按新原点重算几何：``attach`` 内部先按旧局部几何登记一帧，
        紧接着的 ``sync_geometry`` 会立刻改写成新坐标——同一调用栈内完成，任何
        tick/绘制都看不到中间态。岛速基线作废（跨屏位移不是岛在动）。
        """
        if overlay is not None:
            self._overlay = overlay
            origin = overlay.geometry().topLeft()
            self._origin = (float(origin.x()), float(origin.y()))
        self._core.reset_motion()
        self.attach(world)

    def update_geometry(self, left, top, width, height) -> bool:
        """直接喂局部几何（岛非 QWidget / 几何来自别处时用）。"""
        return self._core.update_geometry(left, top, width, height)

    def sync_geometry(self) -> None:
        """按岛当前可见性/几何刷新注册（无参回调与事件过滤共用入口）。

        不设墙的两类状态（对齐旧 ``island_collision._wall_active``，缺了它们
        停靠细条会按 16×64 注册出直径 44 的怪圆墙、动画中间帧会推挤旁边的
        探头宠）：停靠细条且未悬停探出（stadium 水平轴假设不成立）；几何
        动画进行中（``_geo_to`` 非 None，展开/停靠/归位的中间帧不代表落点）。
        两类状态结束后岛的 Move/Resize/回调会再次触发本方法，按最终几何复墙。

        入口先转达 ``on_sync`` 挂点：岛换屏（拖过去/夹回/换主屏）与显隐一样都是
        几何回调的一种，壳必须在墙落到旧世界**之前**有机会换宿主。
        """
        self._notify_sync()
        try:
            if not self._island.isVisible():
                self._core.set_visible(False)
                return
            if getattr(self._island, "_mode", "") == "docked" \
                    and not getattr(self._island, "_hover_peek", False):
                self._core.set_visible(False)  # 细条态不设墙
                return
            if getattr(self._island, "_geo_to", None) is not None:
                self._core.set_visible(False)  # 几何动画中不设墙
                return
            self._core.set_visible(True)
            left, top, width, height = _local_geometry(
                self._island.geometry(), self._origin[0], self._origin[1])
            self._core.update_geometry(left, top, width, height)
        except Exception:
            logger.debug("island_bridge: 岛几何同步失败", exc_info=True)

    def on_collision(self, event) -> None:
        """碰撞事件入口（转发核心桥；world 直接挂本对象也可）。"""
        self._core.on_collision(event)

    def _notify_sync(self) -> None:
        """几何/显隐即将同步 → 外部（壳按岛当前屏换宿主）。

        与 ``kinetic``/``drag_state`` 挂点同纪律：异常静默降级（岛本体功能不受
        影响），且在墙落盘**之前**调用——壳重绑后 ``sync_geometry`` 才按新原点
        算局部几何。重复回调的幂等由壳侧负责（宿主没变就早退）。
        """
        callback = self._on_sync
        if not callable(callback):
            return
        try:
            callback()
        except Exception:
            logger.debug("island_bridge: 岛同步转达失败", exc_info=True)
