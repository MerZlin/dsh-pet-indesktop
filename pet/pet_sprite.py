# -*- coding: utf-8 -*-
"""宠物 sprite（单合成窗架构 Phase 1a）：overlay 窗口内的可绘制/可命中单元。

架构背景见 .scratch/single-overlay-window/spec.md：宠物不再是顶层窗口，
而是 overlay 内按 z-order 合成的 sprite；移动 = 改 pos + 上报脏矩形，
不再经过 WM/DWM 的窗口移动路径。

镜像与帧缓存语义与 pet/window.py 一致（facing=='right' 且 clip 不在
library.no_mirror 时镜像；帧签名不变则复用已缩放的 QPixmap），但代码
独立——本模块不 import window.py，避免与旧窗口路径耦合。

Phase 4.1a 硬化（PHASE4_DESIGN.md D2/D3）：
- D2 DPR 归一化：pos/rect/命中全部留在 overlay 局部逻辑坐标系；
  只有渲染像素随 DPR——pixmap 按 CANVAS*scale*dpr 物理像素生成并
  setDevicePixelRatio(dpr)（参照 window.py:2129-2150 旧路径）。
- D3 位置出口收口：set_pos 是唯一位置写入口，按 manifest body_box
  钳制（身体框必须完整落在 bounds 内，画布透明边允许越界贴屏），
  对齐 window_placement.py:35-53 "让角色形象真正碰到边缘"的语义。
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

from . import catalog
from . import physics as physics_mod
from .library import MovieLibrary, clip_current_image
from .window_effects import (begin_rotation, end_rotation, rotated_region_bounds,
                             unrotate_point)

# 交互状态（跨模块协调协议，见 .scratch/single-overlay-window/spec.md）：
# "normal" = 行为状态机驱动（游荡/待机/转向）；"drag" = 被用户拖拽；
# "thrown" = 抛掷物理接管。行为控制器只在 "normal" 下写 velocity；
# 碰撞世界只在双方非 "drag" 时结算；物理控制器只在 "thrown" 下积分。
INTERACTION_NORMAL = "normal"
INTERACTION_DRAG = "drag"
INTERACTION_THROWN = "thrown"

# Q 弹挤压时长（口径源 window.py:665 _squash_duration_ms）
SQUASH_DURATION_MS = 220


class PetSprite(QObject):
    """单个宠物 sprite：位置/朝向/缩放 + 当前 clip 帧的合成、命中与拖拽。"""

    # 抛掷头部跟随角（度；pet/sprite_throw_egg 彩蛋）的惰性实例字段 + 类级默认。
    # 纯加法（并行改动纪律：不进 __init__，首次写入才落到实例 __dict__）；类级
    # 默认让绘制/命中热路径读成一次普通属性访问——角度 0（未激活彩蛋）时
    # 不产生任何变换、不分配对象，与改造前路径同成本。
    _throw_angle = 0.0

    # 几何记忆化（F-PERF：实机 py-spy 下 rect() 2.1% + _logical_size() 1.3%
    # GUI 线程时间）。两个字段同样是「惰性实例字段 + 类级默认」：首次写入
    # 才落到实例 __dict__。签名即输入值本身，故无作废钩子可漏：
    # - ``_logical_size_cache = (scale, (w, h))``（只依赖 scale）；
    # - ``_rect_cache = (x, y, w, h, QRect)``（只依赖整数化 pos + 逻辑尺寸）。
    # 线程语义：rect/_logical_size 只在 GUI 线程被调用（tick 驱动器 QTimer、
    # 绘制、事件、控制器全部在主线程），故不加锁；单个元组赋值在 CPython 里
    # 原子，最坏情况只是并发下重算一次。
    _logical_size_cache = None
    _rect_cache = None

    def __init__(
        self,
        library: MovieLibrary,
        *,
        pos: QPointF | None = None,
        facing: str = "left",
        scale: float = catalog.DEFAULT_SCALE,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.velocity = QPointF(0, 0)
        self.facing = facing
        self._scale = float(scale)  # scale 为 property（V-10）：直写内部字段
        # D2：渲染 DPR（物理像素 = CANVAS*scale*dpr；逻辑几何不随它变）。
        # 由 overlay 按所在屏 QScreen.devicePixelRatio() 写入。
        self._dpr = 1.0
        # D3：钳制域（overlay 局部逻辑坐标的工作区，None = 不钳制）。
        self._bounds: QRectF | None = None
        # 跨屏过渡的**多矩形合法域**（sprite 局部？否——overlay 局部逻辑坐标，
        # 与 ``_bounds`` 同系）：拖拽跨屏时这只正跨在两块屏的缝上，任何**单矩形**
        # 域都会把它在缝上钳住（这正是跨屏跳位的几何来源）。登记的是"真实相接的
        # 那几块屏的可用区"，故外接矩形里的屏间空洞**不在**域内（宠不会被送进
        # 没有显示器的地方）。None = 走常规 ``_bounds`` 单矩形语义。
        self._clamp_rects: tuple[QRectF, ...] | None = None
        # T2 多屏占位：sprite 的归属屏（多屏钳制用，本刀只留字段）。
        self.home_screen = None
        # pos 为 overlay 局部逻辑坐标（Phase 1a 单屏：overlay 铺满主屏，
        # 局部坐标 = 屏幕逻辑坐标）；多屏几何见 spec 开放问题 Q1。
        # 位置变化由外部行为（wander 速度/抛掷物理）写 velocity 或 set_pos；
        # advance 只负责按 velocity 积分，不知道速度从哪来。一切位置写入
        # 收口 set_pos（body_box 钳制的唯一执行点）。
        # 脏上报（V-3/V-4）：overlay 在 add_sprite 时挂 _dirty_cb(old, new)，
        # set_pos 与帧到达直驱重绘，tick 不再是重绘闸门；_last_reported_rect
        # 记"上次上报给 overlay 的绘制外接矩形"（含探头旋转溢出，见
        # paint_bounds），advance 据此捕获 tick 之外（物理控制器/拖拽事件）
        # 发生的位移。
        self._dirty_cb = None
        self._kinetic_cb = None  # M-1：运动信号回调（overlay 挂接）
        self._last_reported_rect: QRect | None = None
        # 边缘探头姿态（pet/sprite_edge_probe.SpriteEdgeProbeWorld 写入）：
        # _probe_angle = 旋转角（度，绕帧绘制矩形中心）；_probe_exposure =
        # 露出比例，1.0 是「无探头」哨兵，同时是钳制放宽的闸门（<1 时该
        # sprite 的身体允许按 probe_body_bounds 语义藏出左右缘）。默认值即
        # 改造前行为：不旋转、不放宽、不加任何绘制成本。必须在 set_pos 之前
        # 就位——构造期 set_pos 会读它选钳制域。
        self._probe_angle = 0.0
        self._probe_exposure = 1.0
        self.pos = QPointF(0, 0)
        self.set_pos(pos if pos is not None else QPointF(0, 0))
        self._clip = None
        self._clip_name: str | None = None
        # 圈末结束回调（F2）：行为控制器挂接（BehaviorController._ensure_hooks），
        # clip 的 finished 直接转给它做多圈/长拖拽的原地续播
        self._clip_finished_cb = None
        self._clip_finished_owner = None
        self._frame_sig: tuple | None = None
        self._frame_dirty = False
        self._pixmap: QPixmap | None = None
        self._hit_image: QImage | None = None
        self._dragging = False
        self._drag_offset: QPointF | None = None
        # 拖拽轨迹（(monotonic_ts, x, y) 光标样本）：松手初速估算的输入，
        # 格式对齐 physics.estimate_release_velocity；松手后清空
        self._drag_trail: list[tuple[float, float, float]] = []
        # 拖拽弹簧状态（drag_physics 开时生效）：on_move 只写目标点，
        # advance 用 physics.spring_velocity 阻尼跟随——legacy
        # window.py _tick_drag_physics 的手感（K=200/C=30，滞后约 4 帧）。
        # 关 = 1:1 直写（overlay 初版行为）。
        self._drag_spring_target: QPointF | None = None
        self._drag_spring_vel = QPointF(0.0, 0.0)
        # 拖拽弹簧系数（比 legacy 的 200/30 活：滞后约减半、微过冲）
        self.drag_spring_k = 300.0
        self.drag_spring_c = 24.0
        # 弹簧活跃期（拖拽中未收敛）：set_pos 的整数矩形跳报（F-PERF P1）
        # 让位——绘制落点是 float pos，亚像素步进也要上屏，否则拖拽掉帧
        self._spring_active = False
        # 时间源：默认 time.monotonic；测试替换为假钟即可确定性构造轨迹，
        # 不用固定 sleep 赌时序
        self._clock = time.monotonic
        # 甩出速度软上限（px/s，对应旧架构 throw_strength 档位）：由集成层
        # 按设置写入，松手初速过 physics.soft_clamp_speed 时以此为渐近值
        self.throw_speed_cap = physics_mod.MAX_THROW_SPEED
        # 播放速率（菜单「播放速率」写入口）：bind_clip 起播时应用到新 clip
        self.playback_speed = 1.0
        # ffmpeg 圈边界回收阈值（分钟，0=关闭；config 缺省 10）：由集成层
        # `_sync_sprite_settings` 按设置写入，bind_clip 起播时推给新 clip。
        # 归一/夹取（0=关、[2,120]）在 webm_clip.set_recycle_minutes，不在此重复。
        self.ffmpeg_recycle_minutes = 10
        # Q 弹挤压（点击/碰撞反馈，window.py _squash_geometry 语义）：
        # None = 未激活；否则 0..1 进度，advance 按 220ms 推进
        self._squash_progress: float | None = None
        # 拖动物理开关（菜单「拖动物理」）：False 时松手原地放下（不抛掷）、
        # 拖拽 1:1 直跟。缺省 False 对齐 config 默认值与集成层同步口径
        #（overlay_shell._sync_sprite_settings 按设置显式写入）；弹簧跟随
        #（on_move 不直写、advance 阻尼积分）只在 True 时生效。
        self.drag_physics = False
        # 静止放手下落（config drop_gravity，#219）：True 时低速松手不再原地
        # 停在半空，而是零初速进抛掷物理（重力落地）。默认关 = 旧行为；
        # 集成层 `_sync_sprite_settings` 按设置写入。
        self.drop_gravity = False
        # 见模块顶部 INTERACTION_* 常量：跨模块协调协议的唯一权威字段
        self.interaction_state = INTERACTION_NORMAL
        # 逐只显隐（M14 / PHASE4_DESIGN 4.1b）：False = 不绘制、不命中、不进
        # 位置 fanout（隐藏是"整只退出合成与交互面"，不是"变透明"）。默认 True。
        self._visible = True
        # 播放节拍暂停（O3）：隐藏/挂起期间为 True——clip 的定时器停摆，当前
        # 帧/播放位置/在途帧全部原地保留，恢复时从暂停处续播（不重放）。
        self._clip_paused = False

    # ---------------------------------------------------------------- 显隐（M14）
    @property
    def visible(self) -> bool:
        """逐只可见性（默认 True）；隐藏 = 绘制/命中/位置 fanout 三处一起排除。"""
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self.set_visible(value)

    def set_visible(self, visible: bool) -> None:
        """逐只显隐：走既有脏矩形通道（sprite 不是窗口，没有 hideEvent）。

        隐藏时把当前绘制外接矩形原地报脏一次，让 overlay 立即擦掉残留；重复
        设置同值是 no-op（与 set_throw_rotation 等通道同纪律）。
        """
        visible = bool(visible)
        if visible == self._visible:
            return
        rect = self.paint_bounds()
        self._visible = visible
        self._notify_dirty(rect, rect)

    # ---------------------------------------------------------------- 播放节拍暂停（O3）
    def pause_clip(self) -> None:
        """停当前 clip 的播放节拍（隐藏/挂起期零帧推进）。

        只停 clip 自身的定时器：当前帧、播放位置（``_cur``）、在途预取帧
        （``_pending``）与显示图全部原地保留——恢复时从暂停处续播，绝不
        ``restart_clip`` / ``jumpToFrame(0)``（隐藏再显示不得闪回第 0 帧，也不
        出现首帧等待）。无 clip / clip 无该接口（GifClip）时按 no-op 处理。
        幂等：重复暂停不做第二次下推（隐藏与挂起可能叠加触发）。
        """
        if self._clip_paused:
            return
        self._clip_paused = True
        clip = self._clip
        pause = getattr(clip, "pause", None)
        if callable(pause):
            pause()

    def resume_clip(self) -> None:
        """恢复播放节拍（从暂停处续播，不重置播放位置）。幂等。"""
        if not self._clip_paused:
            return
        self._clip_paused = False
        clip = self._clip
        resume = getattr(clip, "resume", None)
        if callable(resume):
            resume()

    @property
    def clip_paused(self) -> bool:
        """当前是否处于"播放节拍暂停"（隐藏/挂起；诊断与测试的只读面）。"""
        return self._clip_paused

    # ---------------------------------------------------------------- 几何
    @property
    def scale(self) -> float:
        """渲染/逻辑缩放（菜单"大小"档位写入口）。"""
        return self._scale

    @scale.setter
    def scale(self, value: float) -> None:
        """V-10：缩放变化必须置脏 + 按新身体框补钳 + 上报新旧矩形。

        裸属性时期菜单改大小：不置 _frame_dirty、不重钳、advance 返回
        None——静态素材下永远不重绘，且尺寸变大后可能滞留界外。
        """
        value = float(value)
        if value <= 0:
            raise ValueError(f"scale 必须为正: {value!r}")
        if value == self._scale:
            return
        old_rect = self.paint_bounds()
        self._scale = value
        self._invalidate_frames()  # 签名含 scale：立即按新尺寸重建（D2）
        self.set_pos(self.pos)    # 尺寸变化后按新身体框补钳（内部判变）
        self._notify_dirty(old_rect, self.paint_bounds())

    def _logical_size(self) -> tuple[int, int]:
        """逻辑大小（CANVAS*scale）：rect/命中/碰撞坐标系的尺寸，与 DPR 无关。

        F-PERF 记忆化：只依赖 scale（DPR 不参与，见 ``_scaled_size``）。缓存
        以 scale 为签名做值比较，故任何写入路径（含直接改 ``_scale``）都自动
        失效，不需要额外的作废钩子。
        """
        scale = self._scale
        cached = self._logical_size_cache
        if cached is not None and cached[0] == scale:
            return cached[1]
        size = (
            max(1, int(round(catalog.CANVAS_W * scale))),
            max(1, int(round(catalog.CANVAS_H * scale))),
        )
        self._logical_size_cache = (scale, size)
        return size

    def _scaled_size(self) -> tuple[int, int]:
        """物理像素大小（CANVAS*scale*dpr）：渲染/命中图的像素尺寸。"""
        dpr = self._dpr
        return (
            max(1, int(round(catalog.CANVAS_W * self.scale * dpr))),
            max(1, int(round(catalog.CANVAS_H * self.scale * dpr))),
        )

    def _invalidate_frames(self) -> None:
        """作废帧缓存并按当前 (scale, dpr) 立即重建（D2 唯一作废入口）。

        scale / dpr 任一变化都必须走这里：签名残留会让快路径跳过重建、
        旧成品继续显示（125%/150% 下变糊）；命中图残留更糟——它是物理
        像素图，新 dpr 索引旧图会越界/错位误判穿透。语义对齐旧路径
        window.py:2129-2150（_refresh_frame_for_screen_dpr 强制 _rebuild_frame
        + update）：这里同步重建，首帧未就绪时 _rebuild_pixmap 保持旧成品
        不破坏显示（失败不记账，见 _rebuild_pixmap 的签名写入时机）。
        """
        self._frame_sig = None
        self._hit_image = None
        self._frame_dirty = True
        self._rebuild_pixmap()

    def rect(self) -> QRect:
        """sprite 在 overlay 局部逻辑坐标系下的外接矩形（整数化后的绘制矩形）。

        恒为逻辑坐标（CANVAS*scale 逻辑大小）——位置/命中/碰撞全部留在
        逻辑坐标系，只有渲染像素随 DPR（D2）。

        F-PERF 记忆化：输入只有 ``(int(pos.x), int(pos.y), 逻辑 w, h)``，
        绘制/命中/区域判断/气泡锚点每 tick 每 sprite 各调它一次（实机
        py-spy：2.1% GUI 线程），故按这四元组签名缓存 QRect 本体。
        ``center``/``body_rect``/``radius``/``paint_bounds`` 都经由本方法
        取值，自动同等受益，不各自另建缓存。

        返回的是**共享对象**：签名一变即换新 QRect，旧对象不被原地改写，
        因此调用点拿到的始终是「取值当时的快照」。调用点已 grep 确认没有
        原地修改矩形的地方（产品代码只有 ``translated()`` 这类 const 接口，
        tests 里零处改动动词）；后续若有人写出原地改写，会破坏本契约，须
        改成返回 ``QRect(rect)`` 副本。
        """
        pos = self.pos
        x = int(pos.x())
        y = int(pos.y())
        w, h = self._logical_size()
        cached = self._rect_cache
        if (cached is not None and cached[0] == x and cached[1] == y
                and cached[2] == w and cached[3] == h):
            return cached[4]
        rect = QRect(x, y, w, h)
        self._rect_cache = (x, y, w, h, rect)
        return rect

    def center(self) -> QPointF:
        r = self.rect()
        return QPointF(r.x() + r.width() / 2, r.y() + r.height() / 2)

    def body_rect(self) -> QRect:
        """稳定身体框（overlay 局部逻辑坐标）= rect 原点 + body_box×scale。

        贴边钳制/碰撞体/气泡锚点的统一口径（V-2）：未声明 body_box 的
        角色包回退全画布（= rect()，语义等同"画布即身体"）。
        """
        body = self._body_local_rect()
        r = self.rect()
        return QRect(r.x() + body.x(), r.y() + body.y(),
                     body.width(), body.height())

    def radius(self) -> float:
        """碰撞圆半径（与 Phase 0 探针同一经验系数：0.45 * min(w, h)）。"""
        r = self.rect()
        return 0.45 * min(r.width(), r.height())

    # ---------------------------------------------------------------- DPR（D2）
    @property
    def dpr(self) -> float:
        """当前渲染 DPR（pixmap 物理像素 = 逻辑大小 × dpr）。"""
        return self._dpr

    def set_dpr(self, dpr: float) -> None:
        """设置渲染 DPR（overlay 按所在屏 devicePixelRatio 写入）。

        只影响渲染像素：pixmap 物理尺寸与命中图随 dpr 立即重建（旧成品与
        旧命中图绝不留在缓存里），并报脏要求重绘——屏 DPR 变化时宠物静止
        也要立刻换清晰成品，不等下一个 tick/重绘（旧路径
        window.py:2137-2142 同语义：_rebuild_frame + update）。rect/pos/
        velocity 等逻辑几何不变。
        """
        dpr = float(dpr)
        if dpr <= 0:
            raise ValueError(f"dpr 必须为正: {dpr!r}")
        if dpr == self._dpr:
            return
        self._dpr = dpr
        self._invalidate_frames()
        rect = self.paint_bounds()
        self._notify_dirty(rect, rect)  # 立即重绘（不等 tick）

    # ---------------------------------------------------------------- 钳制（D3）
    @property
    def bounds(self) -> QRectF | None:
        """当前钳制域（overlay 局部逻辑坐标；None = 不钳制）。"""
        return self._bounds

    def set_bounds(self, bounds: QRect | QRectF | None) -> None:
        """设置钳制域（overlay 局部逻辑坐标的工作区）；None 解除钳制。

        设置时对当前位置立即补一次钳制，保证不变量即刻成立。
        """
        self._bounds = None if bounds is None else QRectF(bounds)
        if self._bounds is not None:
            self.set_pos(self.pos)

    def _body_local_rect(self) -> QRect:
        """稳定身体框（sprite 局部逻辑坐标，body_box×scale）。

        与 window_placement.stable_body_local_rect 同一取数口径：角色
        manifest 的 body_box（源像素、已镜像对称化）× scale；未声明的角色包
        回退全画布（语义等同"画布即身体"，钳制退化为整矩形钳制）。sprite 无
        捕获头区/落地绘制偏移（帧即画布对齐左上角），故不加旧路径的
        headroom/PAD 修正。
        """
        character_id = str(getattr(self.library, "character_id", "") or "")
        box = catalog.character_body_box(character_id)
        if box is None:
            w, h = self._logical_size()
            return QRect(0, 0, w, h)
        x1, y1, x2, y2 = box
        s = self.scale
        return QRect(
            int(round(x1 * s)),
            int(round(y1 * s)),
            max(1, int(round((x2 - x1) * s))),
            max(1, int(round((y2 - y1) * s))),
        )

    @staticmethod
    def _clamp_axis(value: float, lo: float, hi: float, off: float, span: float) -> float:
        """把 value 钳到使 [value+off, value+off+span] 完整落在 [lo, hi] 内。

        hi 为连续右/下界（不含端点语义，对齐 QRectF 的 left+width）。可用区
        比身体还窄/矮时上界 < 下界，钳到下界（同 window_placement.clamp_span
        的小屏兜底模式）。
        """
        lower = lo - off
        upper = hi - off - span
        if upper < lower:
            return lower
        return min(max(value, lower), upper)

    def _clamp_domain(self) -> QRectF | None:
        """set_pos 的生效钳制域：探头姿态用放宽域，否则常规 bounds。

        放宽域由本 sprite 的 bounds + 身体框就地算出、只作用于自己（不写共享
        状态），因此不影响其它 sprite 的钳制。

        多矩形跨屏域（``_clamp_rects``）**不**经由本方法返回：那是"多个合法矩形"
        而不是一个矩形，``_at_ground``/``set_pos`` 各取所需（前者要离当前身体最近的
        那一块，后者逐块钳取位移最小的解）。
        """
        relaxed = self.probe_clamp_bounds()
        if relaxed is not None:
            return relaxed
        if self._clamp_rects is not None:
            return self._settling_rect()
        return self._bounds

    def _settling_rect(self) -> QRectF | None:
        """多矩形域里离这只最近的那一块（``_at_ground`` 这类单矩形口径的取法）。

        贴地/落地判据只对"这只此刻所在的那块屏"有意义：取包含身体中心的那块，
        都不包含时取中心距离最近的那块（错位拼接的缝上就是这种情形）。
        """
        rects = self._clamp_rects
        if not rects:
            return None
        body = self._body_local_rect()
        cx = self.pos.x() + body.x() + body.width() / 2.0
        cy = self.pos.y() + body.y() + body.height() / 2.0
        best = None
        for rect in rects:
            if rect.contains(cx, cy):
                return rect
            dx = max(rect.left() - cx, 0.0, cx - (rect.left() + rect.width()))
            dy = max(rect.top() - cy, 0.0, cy - (rect.top() + rect.height()))
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, rect)
        return best[1] if best is not None else None

    def set_clamp_rects(self, rects) -> None:
        """登记/清除**多矩形合法域**（跨屏拖拽过渡期用；``None`` 恢复 ``_bounds``）。

        与 :meth:`set_bounds` 的分工：后者是常规单矩形工作区，并在设置时补一次
        钳制（不变量立刻成立）；本方法**不**补钳制——调用方只在"这只的当前位置
        已经落在新域内"时开域（域总是旧域的**超集**：相邻屏的可用区并集），补
        一次钳制反而是多余的位移风险。收窄（清除）由调用方在"身体已完整落在某块
        屏的可用区内"之后做，那时单矩形域同样不含任何钳制。
        """
        if rects is None:
            self._clamp_rects = None
            return
        self._clamp_rects = tuple(QRectF(rect) for rect in rects)

    def _clamp_targets(self) -> tuple[QRectF, ...]:
        """``set_pos`` 可用的钳制矩形集合。

        **探头姿态优先用放宽域**：探头要「藏半身在屏外」，放宽域是**一个**矩形；
        若让多矩形域优先，探头期间身体会被逐点并集判定钳回屏内——姿态照画、位置
        却在屏里，实测表现是身子露在屏幕外/姿态错位。多矩形域只在常规移动（含
        跨屏拖拽）时生效。
        """
        relaxed = self.probe_clamp_bounds()
        if relaxed is not None:
            return (relaxed,)
        rects = self._clamp_rects
        if rects:
            return rects
        domain = self._clamp_domain()
        return () if domain is None else (domain,)

    @staticmethod
    def _rect_covered(body: QRectF, rects) -> bool:
        """``body`` 是否**逐点**落在 rects 的并集里（轴对齐网格切分，精确）。

        跨屏缝上这只的身体本来就横跨两块屏——"整块落在**某一块**矩形里"会把它钉在
        缝的一侧（这正是跨屏过不去/被钳回来的来源）。合法条件必须是**身体 ⊆ 并集**：
        缝上（身体横跨两块相连屏）合法，错位拼接的屏间空洞不合法。
        用 rects 的边把身体切成网格逐格判中心点，轴对齐下与"逐点"等价且只花几十次
        比较（域最多几块屏，且只有开域期走这条路）。
        """
        xs = {body.left(), body.left() + body.width()}
        ys = {body.top(), body.top() + body.height()}
        for rect in rects:
            for value in (rect.left(), rect.left() + rect.width()):
                if body.left() < value < body.left() + body.width():
                    xs.add(value)
            for value in (rect.top(), rect.top() + rect.height()):
                if body.top() < value < body.top() + body.height():
                    ys.add(value)
        xs = sorted(xs)
        ys = sorted(ys)
        for left, right in zip(xs, xs[1:]):
            for top, bottom in zip(ys, ys[1:]):
                cx = (left + right) / 2.0
                cy = (top + bottom) / 2.0
                if not any(rect.left() <= cx <= rect.left() + rect.width()
                           and rect.top() <= cy <= rect.top() + rect.height()
                           for rect in rects):
                    return False
        return True

    def _at_ground(self) -> bool:
        """是否已贴住活动区地面（松手语义用，容差同 ``physics.is_at_rest``）。

        地面 = 身体框下缘贴 bounds 下缘的那个 y，即 ``set_pos`` 钳制域的**最深
        下界**——直接问钳制函数本身（传 +inf 让 ``_clamp_axis`` 返回它的 upper，
        含小屏兜底），不另抄一份几何式。与抛掷物理的地板线同源：壳对 sprite 与
        ``ThrowPhysicsController`` 喂同一个 bounds，``_sprite_bounds`` 的 bottom
        就是这条线。无 bounds（未挂活动区）时返回 False——宁可进抛掷链，也不假
        定"已经落地"。
        """
        domain = self._clamp_domain()
        if domain is None:
            return False
        body = self._body_local_rect()
        ground = self._clamp_axis(
            math.inf, domain.top(), domain.top() + domain.height(),
            body.y(), body.height())
        return self.pos.y() >= ground - 1.0

    def set_pos(self, pos: QPointF) -> None:
        """唯一位置写入口：写入前按 body_box 钳制（D3）。

        身体框（body_box×scale，相对 sprite 左上角偏移）必须完整落在
        bounds 内；sprite 外接矩形允许溢出（角色形象贴到屏幕边缘，画布
        透明边可以越界）。无 bounds 或无 body_box 声明（回退全画布）时
        等价于整矩形钳制。模块内一切位置写入（velocity 积分/拖拽/外部
        控制器）都必须经此入口。

        探头姿态（probe_exposure<1）改用放宽域（probe_body_bounds 语义），
        身体可整体藏出左右缘；取消探头即恢复常规钳制。

        多矩形域（跨屏过渡，``_clamp_rects``）：身体**逐点落在并集内**就一步不动
        （跨在缝上是合法的：缝两边都有显示器）；只有落进屏间空洞时才把它推到最近的
        那一块屏——域内永远没有"没有显示器"的合法位置。
        """
        x, y = float(pos.x()), float(pos.y())
        targets = self._clamp_targets()
        if targets:
            body = self._body_local_rect()
            if len(targets) > 1 and self._rect_covered(
                    QRectF(x + body.x(), y + body.y(), body.width(), body.height()),
                    targets):
                pass  # 缝上/屏内：合法，不动
            else:
                best = None
                for b in targets:
                    cx = self._clamp_axis(x, b.left(), b.left() + b.width(),
                                          body.x(), body.width())
                    cy = self._clamp_axis(y, b.top(), b.top() + b.height(),
                                          body.y(), body.height())
                    d = (cx - x) * (cx - x) + (cy - y) * (cy - y)
                    if best is None or d < best[0]:
                        best = (d, cx, cy)
                if best is not None:
                    x, y = best[1], best[2]
        new_pos = QPointF(x, y)
        if new_pos == self.pos:
            return
        old_rect = self.paint_bounds()
        self.pos = new_pos
        # V-3/V-4：位移直驱重绘——物理控制器（before_sprites_advance）与
        # 拖拽事件都在 advance 之外移动 sprite，旧位置必须即时上报清除，
        # 否则静态素材冻结、动画素材拖尾。overlay 的 update 天然合并
        # 同一事件轮次内的多次调用，tick 内多次 set_pos 不会放大 paint。
        # 脏矩形口径 = 绘制外接矩形（含探头旋转溢出），45° 姿态下只报
        # rect() 会在旋转角上留残影。
        # F-PERF P1：整数化绘制矩形未变且无待上屏新帧 → 重绘的就是「像素内容
        # 逐位相同」的同一块矩形，纯浪费（慢速爬行/多鱼挤压时每拍都报，用户
        # 主诉帧数明显低）。此时跳过上报，位置仍然写入（换算口径不变）。
        # 待上屏帧（_frame_dirty）照旧上报：帧到达路径的报脏语义不变，新帧
        # 有机会上屏。帧到达/换帧另有 _on_frame_changed 直驱通道，不依赖这里。
        new_rect = self.paint_bounds()
        if new_rect == old_rect and not self._frame_dirty and not self._spring_active:
            return
        self._notify_dirty(old_rect, new_rect)

    def _notify_dirty(self, old: QRect, new: QRect) -> None:
        """向 overlay 上报脏矩形（add_sprite 挂接；未挂接时 no-op）。

        消费方（overlay/其宿主窗口）C++ 侧已销毁时，回调会以
        ``RuntimeError: ... already deleted`` 抛出。本方法被 ``_on_frame_changed``
        （clip 信号槽）与 ``set_pos``（鼠标/行为/物理链路）调用，抛出就落成
        信号槽里的 unraisable（pytest 按未捕获异常判败）。脏上报是
        fire-and-forget：消费方没了就没有上屏对象，就地丢弃。

        **只吞这一种**——RuntimeError 是 shiboken「包装器已死」的口径，其它
        异常照旧外抛，不掩盖消费方活着的真实缺陷；窗口侧还有更早的一道守卫
        （``overlay_window.OverlayWindow._alive``），这层兜的是"回调实现不
        自己守卫"的情况。正常路径（活消费方）零行为变化。
        """
        cb = self._dirty_cb
        if cb is None:
            return
        try:
            cb(old, new)
        except RuntimeError:
            pass

    def set_velocity(self, velocity: QPointF) -> None:
        self.velocity = QPointF(velocity)
        # M-1：非零速度 = 运动信号，同步唤醒 tick 档位——行为掷骰起步、
        # 碰撞写回、松手甩出都不等下一个（可能已降档的）tick
        cb = self._kinetic_cb
        if cb is not None and not self.velocity.isNull():
            cb()

    # ---------------------------------------------------------------- 探头姿态（边缘探头）
    @property
    def probe_angle(self) -> float:
        """探头旋转角（度，正值 = QPainter.rotate 的顺时针方向）。"""
        return self._probe_angle

    @property
    def probe_exposure(self) -> float:
        """探头露出比例（1.0 = 无探头）。"""
        return self._probe_exposure

    @property
    def probe_active(self) -> bool:
        """是否处于探头姿态（曝光 < 1）。

        行为控制器据此跳过贴边钳制与游荡（探头会话期间只允许待机/转向，
        旧机 _effects_probe_active 闸门的 sprite 版）。
        """
        return self._probe_exposure < 1.0

    def probe_clamp_bounds(self) -> QRectF | None:
        """探头放宽后的钳制域 = 旧 edge_probe.probe_body_bounds 同式。

        正常移动时身体被钳在工作区内（角色不会被拖丢）；探头要「藏一半
        出屏」，所以左右各放宽一个完整身体宽。**必须减去 body.x()**：身体框
        在画布里本就右偏（body_box 局部 x>0），不减掉这一项时左向几乎不放宽，
        那不是「藏半边」而是把身体钉在边缘（旧机注释同款坑）。

        未处于探头姿态（曝光 = 1）或未挂 bounds 的 sprite 返回 None——调用方
        据此判断"当前是否用了放宽域"。
        """
        if not self.probe_active:
            return None
        b = self._bounds
        if b is None:
            return None
        body = self._body_local_rect()
        return QRectF(
            b.left() - body.x() - body.width(),
            b.top() - body.y(),
            b.width() + 2 * body.width() + 2 * body.x(),
            b.height() + 2 * body.y(),
        )

    def paint_bounds(self) -> QRect:
        """当前绘制外接矩形（含探头旋转溢出）：角度 0 时恒等于 rect()。

        脏上报与 advance 的 old/new 都用它——旋转让绘制溢出 rect()，只报
        rect() 会在 45° 姿态的旋转角上留残影；同时它恒包含 rect()，保证
        overlay.paintEvent 的 ``region.intersects(sprite.rect())`` 闸门不会
        漏画。角度 0 时无额外计算成本。
        """
        # 热路径直接读惰性字段（一次属性访问，省去 property 调用；见
        # set_throw_rotation）：抛掷角为 0 时与改造前路径同成本。
        throw = self._throw_angle
        if abs(throw) >= 1e-6:
            # 抛掷旋转（pet/sprite_throw_egg 彩蛋，纯加法通道）：与探头旋转同轴
            # （同一旋转中心 = 帧绘制矩形中心），两次同轴旋转的合成就是角度
            # 相加，故投影外接矩形直接按 (探头角 + 抛掷角) 算。抛掷角为 0 时
            # 不进本分支，下面保持改造前的零成本路径。
            rect = self.rect()
            return rotated_region_bounds(
                rect, rect, self._probe_angle + throw).united(rect)
        rect = self.rect()
        if abs(self._probe_angle) < 1e-6:
            return rect
        return rotated_region_bounds(rect, rect, self._probe_angle).united(rect)

    def set_probe_pose(self, angle_deg: float, exposure: float) -> None:
        """写入探头姿态（角度 + 曝光）并按既有脏矩形通道上报。

        角度/曝光都没变时 no-op——探头 tween 每 tick 调用它，稳态零成本。
        曝光 < 1 即放宽钳制（见 probe_clamp_bounds），所以调用方应在写目标
        位置之前先写姿态。
        """
        angle = float(angle_deg)
        exposure = max(0.0, min(1.0, float(exposure)))
        if angle == self._probe_angle and exposure == self._probe_exposure:
            return
        old = self.paint_bounds()
        self._probe_angle = angle
        self._probe_exposure = exposure
        self._notify_dirty(old, self.paint_bounds())

    def clear_probe_pose(self) -> None:
        """清探头姿态并恢复常规钳制（身体被钳回 bounds 内）。

        会话取消（拖拽/碰撞/抛掷/功能关闭）的收口：旧机的窗口 x 恢复值在
        sprite 世界就是「清姿态 + 常规钳制」——set_pos 会把身体钳回边缘，
        与进入探头前的位置一致。未处于探头姿态时是 no-op（幂等）。
        """
        if not self.probe_active and abs(self._probe_angle) < 1e-6:
            return
        old = self.paint_bounds()
        self._probe_angle = 0.0
        self._probe_exposure = 1.0
        self.set_pos(self.pos)  # 常规钳制钳回屏内（位置没变时不重复报脏）
        self._notify_dirty(old, self.paint_bounds())

    # ---------------------------------------------------------------- 抛掷旋转（throw_egg 彩蛋）
    @property
    def throw_rotation(self) -> float:
        """抛掷头部跟随角（度；0.0 = 无抛掷旋转，绘制路径零成本）。

        纯加法通道（并行改动纪律：实例字段不进 __init__，类级默认 0.0，首次
        写入才落到实例 __dict__），与探头姿态（_probe_angle/_probe_exposure）
        完全独立——set/clear 本通道绝不改探头姿态。
        """
        return float(self._throw_angle)

    def set_throw_rotation(self, angle_deg: float) -> None:
        """写入抛掷旋转角（度，绕帧绘制矩形中心），走既有脏矩形上报通道。

        与探头姿态在绘制路径合成：抛掷旋转先入栈（外层）、探头旋转在内层，
        同一旋转中心 → 合成等价于「探头角 + 抛掷角」（paint_bounds 用同一
        口径算投影外接矩形，alpha_at 用同一口径做逆变换）。角度未变时 no-op
        （彩蛋每 tick 调用它）；0.0 = 回正（clear_throw_rotation 的等价物）。
        """
        angle = float(angle_deg)
        if angle == self._throw_angle:
            return
        old = self.paint_bounds()
        self._throw_angle = angle
        self._notify_dirty(old, self.paint_bounds())

    def clear_throw_rotation(self) -> None:
        """清抛掷旋转（回正）；未激活时 no-op（幂等）。"""
        self.set_throw_rotation(0.0)

    def _begin_throw_rotation(self, painter: QPainter, angle_deg: float) -> None:
        """抛掷旋转入栈（paint 的纯加法挂载点；角度 0 时调用方不会进来）。"""
        begin_rotation(painter, self.rect(), angle_deg)

    def _end_throw_rotation(self, painter: QPainter, angle_deg: float) -> None:
        """与 _begin_throw_rotation 配对出栈（同一角度，paint 内不会中途变化）。"""
        end_rotation(painter, angle_deg)

    def close(self) -> None:
        """释放 clip 所有权（V-8）：断开信号 + 停解码 + 清帧缓存。

        overlay.remove_sprite（release_clip=True，默认）调用——sprite 移除
        即停解码，不再靠库 shutdown() 兜底。屏迁移等需要保留 clip 的
        场景走 remove_sprite(release_clip=False)。
        """
        clip = self._clip
        if clip is not None:
            self._disconnect_clip_signals(clip)
            stop = getattr(clip, "stop", None)
            if callable(stop):
                stop()
        self._clip = None
        self._clip_name = None
        self._frame_sig = None
        self._pixmap = None
        self._hit_image = None
        self._dirty_cb = None
        self._kinetic_cb = None
        self._clip_finished_cb = None
        self._clip_finished_owner = None
        self._last_reported_rect = None

    def _disconnect_clip_signals(self, clip) -> None:
        """断开本 sprite 从该 clip 接的信号（换绑/释放共用；缺失即忽略）。

        frameChanged/finished 必须成对断开：只断前者会让旧 clip 的圈末
        finished 继续打到已换绑的 sprite 上（F2 续圈错绑）。
        """
        for name, slot in (("frameChanged", self._on_frame_changed),
                           ("finished", self._on_clip_finished)):
            signal = getattr(clip, name, None)
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass  # 未连接过/对象已毁：忽略

    # ---------------------------------------------------------------- clip 绑定
    def bind_clip(self, name: str) -> bool:
        """绑定并起播一个动画 clip；旧 clip 断开信号并停止（不再后台解码）。

        返回 clip.start() 是否被接受（``False`` = 拒绝；返 None 的播放器
        ——如 GifClip.start——按接受处理，见 F6 的语义）。调用方可据此放弃
        依赖该动画的状态（移动计划等），绝不把「动画状态已切换」与「动画
        真的在播」当成同一件事（旧机 B7 审查 P1-1 同款契约）。

        clip 的 frameChanged/finished 本来就 emit 在 GUI 线程，且 sprite 由
        overlay 持有（同线程），直接连接即可，无需 queued。
        """
        if self._clip is not None:
            self._disconnect_clip_signals(self._clip)
            stop = getattr(self._clip, "stop", None)
            if callable(stop):
                stop()
        self._clip_name = name
        self._clip = self.library.movie(name)
        clip = self._clip
        clip.frameChanged.connect(self._on_frame_changed)
        # F2：圈末结束标记（WebMClip 圈末停表、帧序列播到尾）转发给行为
        # 控制器做原地续播；WebMClip/GifClip/帧序列 clip 都有该信号，缺失时
        # 静默跳过（只影响续圈，不影响单圈播放）
        finished = getattr(clip, "finished", None)
        if finished is not None:
            finished.connect(self._on_clip_finished)
        # 换 clip 后签名/缓存作废；首帧到达前先按脏处理，保证首 tick 上屏。
        # **不清 self._pixmap**：旧 clip 的最后一帧留作兜底（_frame_sig=None
        # 已强制按新签名重建，新帧到货即覆盖）——清掉会让新 clip 首帧异步
        # 到货前 paint 画透明，桌宠闪消失一瞬。
        self._frame_sig = None
        self._hit_image = None
        self._frame_dirty = True
        setter = getattr(clip, "set_playback_speed", None)
        if callable(setter):
            setter(self.playback_speed)
        # 圈边界回收阈值同点推送（旧 window.py `_switch` 的同位置推送，
        # `:1558`）：纯写一个标量，不 start/spawn/重启；缺该方法的 clip
        # （Gif/测量桩）按旧 `_push_recycle` 的 hasattr 门跳过。
        recycle = getattr(clip, "set_recycle_minutes", None)
        if callable(recycle):
            recycle(self.ffmpeg_recycle_minutes)
        # start() 前同步取第 0 帧的策略（py-spy 慢帧归因 2026-09-24）：
        # - 显示槽已有可显示帧（预热/上一圈残留）→ 不跳（start 不清槽，
        #   修复 1 已保证显示连续）；
        # - FrameSeqClip 无帧 → jumpToFrame(0) 是同步磁盘读 ~2.5ms，便宜，
        #   且它的 start 是异步交付首帧，必须先同步跳帧才有帧可画；
        # - WebMClip 无帧 → **不跳**：它的 jumpToFrame(0) 内部走 stop()→
        #   _hard_stop，随后 start 重新 spawn ffmpeg（GUI 线程 50-56ms，
        #   py-spy 实机抓到的慢帧主力）。无帧时靠「保留旧 _pixmap 兜底 +
        #   start 异步交付」过渡（冷启动 60-166ms 是 legacy 既有语义，
        #   不为它在 GUI 线程付进程创建税）。
        cur = getattr(clip, "currentImage", None)
        img = cur() if callable(cur) else None
        if img is None or img.isNull():
            from .frameseq_clip import FrameSeqClip
            if isinstance(clip, FrameSeqClip):
                clip.jumpToFrame(0)
        start = getattr(clip, "start", None)
        if not callable(start):
            return True
        accepted = start() is not False
        if self._clip_paused:
            # 隐藏中换绑（行为链照常推进）：新 clip 立刻回到暂停——否则隐藏
            # 的那只又开始按帧率解码，"隐藏期零推进"的契约被换绑悄悄破坏。
            pause = getattr(clip, "pause", None)
            if callable(pause):
                pause()
        else:
            # 暂停契约归 sprite 持有，clip 的 _paused 不得跨绑定滞留：暂停期
            # 被换绑掉的 clip 在库缓存里一直揣着 _paused（恢复路径只续当前
            # clip），下次 start() 看到滞留标记不起定时器 = 画面永久冻在首帧
            # （拖拽悬空/走路动画变静态图的根因）。sprite 未暂停 = 契约不
            # 成立，必须清掉滞留；未暂停的 clip 上 resume() 是 no-op。
            resume = getattr(clip, "resume", None)
            if callable(resume):
                resume()
        return accepted

    def restart_clip(self) -> bool:
        """原地续播当前 clip（圈末 re-arm，F2）；返回是否被接受。

        走旧机 ``_restart_current_clip`` 的序列（window.py:1809-1828）：先
        ``jumpToFrame(0)`` 再 ``start()``——WebMClip 的软停续圈前提
        ``_soft_parked`` 只在 ``stop()`` 里置位（webm_clip.py:1594-1627），
        而 ``jumpToFrame`` 内部会走 ``stop()``（webm_clip.py:1756-1757）；
        只调 ``start()`` 会落 fresh start：换代、退役 reader、每圈新起一个
        ffmpeg（实跑实证：gen 1->2 retired=1，旧机序列 gen 1->1 retired=0）。
        帧序列 clip 的 ``jumpToFrame(0)`` 是同步取首帧，同样有益。

        同时作废帧签名（M2）：``start`` 把帧号归 0 但 frameseq 的 start 不清
        显示槽（修复 1），留着旧签名会让第一次重建用「帧号 0 + 旧末帧图」
        记下签名，真帧 0 到货时被快路径吞掉——每圈首帧缺一帧（实跑实证：
        rebuild=False 且 awaiting=-1）。

        ``False`` = 起播被拒，调用方按各自场景降级（旧机同契约）。
        """
        clip = self._clip
        if clip is None:
            return False
        start = getattr(clip, "start", None)
        if not callable(start):
            return False
        jump = getattr(clip, "jumpToFrame", None)
        if callable(jump):
            jump(0)
        self._frame_sig = None
        self._frame_dirty = True
        accepted = start() is not False
        if self._clip_paused:
            # 圈末续圈（行为链）打断不了"隐藏期零推进"：重新压回暂停（口径
            # 与 bind_clip 同点，见那里的注释）。
            pause = getattr(clip, "pause", None)
            if callable(pause):
                pause()
        else:
            # 与 bind_clip 的 else 分支对称：本 clip 被 pause 过、又被 stop
            # 过（半暂停态：stop 只停表，不清 _paused）时，restart 的 start()
            # 看到滞留标记不起定时器 = 续圈之后画面冻在首帧。sprite 未暂停
            # = 暂停契约不成立，清掉滞留（未暂停时 resume() 是 no-op）。
            resume = getattr(clip, "resume", None)
            if callable(resume):
                resume()
        return accepted

    def _on_clip_finished(self) -> None:
        """clip 圈末结束（F2）：转发给行为控制器决定是否续圈。

        本回调不自行 restart：只有控制器知道「本状态的剩余时长」（多圈移动
        的中间圈要续、末圈不能续），单一事实来源在 BehaviorController.
        on_clip_finished。
        """
        cb = self._clip_finished_cb
        if cb is not None:
            cb(self)

    # ---------------------------------------------------------------- 飞行期播放速率（F4）
    def set_flight_anim_speed(self, factor: float) -> None:
        """飞行期动画倍率：当前 clip 速率 = 用户播放速率 × factor。

        由 sprite_physics 每 tick 按当拍速度调用（倍率本身是纯函数
        physics.flight_anim_speed，见 window.py:4328-4340）。速率变化小于
        0.05 时跳过写入——省一次跨模块调用；**帧表节拍的保护不在这里**：真正
        "同值不写、真变速顺延到下次 timeout 落地"的判据按取整后的 interval 在
        clip 内部（``WebMClip._apply_interval`` / ``FrameSeqClip._apply_interval``）
        ——只有 clip 知道 fps 与解码节流除数，间隔口径在那里才不会漂。原先是
        每次写入都重设 QTimer，把当拍倒计时截断、帧交付被压慢（实机"上下飞帧数
        上不去"，见 tests/test_flight_frame_pacing.py）。
        """
        clip = self._clip
        if clip is None:
            return
        setter = getattr(clip, "set_playback_speed", None)
        if not callable(setter):
            return
        target = float(self.playback_speed) * float(factor)
        if abs(float(getattr(clip, "playback_speed", target)) - target) > 0.05:
            setter(target)

    def reset_playback_speed(self) -> None:
        """把当前 clip 播放速率复位回用户速率（落地 / 飞行被打断的唯一出口）。

        必须复位：``duration()`` 会除以 clip.playback_speed
        （webm_clip.py:1387-1390），留着飞行期的加速倍率会让下一次
        ``_plan_move`` 按加速后的时长量化位移（步态与墙钟失配）。
        """
        clip = self._clip
        if clip is None:
            return
        setter = getattr(clip, "set_playback_speed", None)
        if callable(setter):
            setter(float(self.playback_speed))

    def _on_frame_changed(self, _frame: int) -> None:
        self._frame_dirty = True
        # V-4：帧到达直驱重绘，不经过 tick——tick 降档后动画帧率不随之掉
        rect = self.paint_bounds()
        self._notify_dirty(rect, rect)

    # ---------------------------------------------------------------- tick
    def advance(self, dt: float) -> tuple[QRect, QRect] | None:
        """推进一 tick：仅在 "normal" 状态下按 velocity 积分位置。

        拖拽（"drag"）位置由 on_move 驱动；抛掷（"thrown"）位置由
        sprite_physics.ThrowPhysicsController 在 before_sprites_advance
        阶段做 ≤8ms 子步积分——两者都不能在此再积一次（否则双重积分）。

        返回 (旧矩形, 新矩形) 供 overlay 合并脏区域与位置监听 fanout；
        位置未动且帧未变（视觉无变化）返回 None——overlay 据此跳过
        update（按需刷新铁律，见 Phase 0 实测：整窗重绘 CPU +36%）。

        旧矩形取 _last_reported_rect（上次上报给 overlay 的绘制外接矩形）
        而非本函数入口的 rect()：物理控制器（before_sprites_advance）与拖拽
        事件都在 advance 之外移动 sprite，入口取 rect 会 old==new 漏报
        旧位置（V-3）。口径用 paint_bounds()（含探头旋转溢出），45° 姿态下
        旋转角的旧像素同样必须被清掉。
        """
        old = (self._last_reported_rect
               if self._last_reported_rect is not None else self.paint_bounds())
        if (self._dragging and self.drag_physics
                and self._drag_spring_target is not None):
            # 拖拽弹簧档：阻尼跟随目标点（physics.spring_velocity）。
            # K/C 比 legacy（200/30）活一点（实机反馈 200/30 阻尼感偏强）：
            # 300/24 滞后量约减半、略带过冲弹性；逐 sprite 可调。
            vx = physics_mod.spring_velocity(
                self._drag_spring_vel.x(), self.pos.x(), self._drag_spring_target.x(), dt,
                k=self.drag_spring_k, c=self.drag_spring_c)
            vy = physics_mod.spring_velocity(
                self._drag_spring_vel.y(), self.pos.y(), self._drag_spring_target.y(), dt,
                k=self.drag_spring_k, c=self.drag_spring_c)
            self._drag_spring_vel = QPointF(vx, vy)
            self._spring_active = True  # 活跃期亚像素步进也要报脏上屏
            self.set_pos(self.pos + self._drag_spring_vel * dt)
            # 收敛即钉死：渐近尾迹不许永远触发重绘
            if (abs(self._drag_spring_target.x() - self.pos.x()) < 0.5
                    and abs(self._drag_spring_target.y() - self.pos.y()) < 0.5
                    and abs(vx) < 5.0 and abs(vy) < 5.0):
                self._drag_spring_vel = QPointF(0.0, 0.0)
                self._spring_active = False
                self.set_pos(self._drag_spring_target)
        if self.interaction_state == INTERACTION_NORMAL and not self.velocity.isNull():
            self.set_pos(self.pos + self.velocity * dt)  # 积分也过 body_box 钳制
        squashing = False
        if self._squash_progress is not None:
            self._squash_progress += dt / (SQUASH_DURATION_MS / 1000.0)
            if self._squash_progress >= 1.0:
                self._squash_progress = None
            squashing = True  # 收势帧也要再画一次（回正）
        new = self.paint_bounds()
        if new != old or self._frame_dirty or self._squash_progress is not None or squashing:
            self._frame_dirty = False
            self._last_reported_rect = new
            return (old, new)
        return None

    # ---------------------------------------------------------------- 帧缓存与绘制
    def _mirror_frame(self) -> bool:
        """朝右且 clip 未登记 no_mirror（含文字素材）时镜像——同 window.py:2096。"""
        if self.facing != "right":
            return False
        no_mirror = getattr(self.library, "no_mirror", frozenset())
        return self._clip_name not in no_mirror

    def _mirror_image(self, img: QImage) -> QImage:
        """左右镜像一帧（缺陷 16：``QImage.flipped`` 是 Qt 6.9+ API）。

        requirements 允许 ``PySide6>=6.5``，6.5-6.8 上没有 ``flipped``——直接调用
        会 AttributeError 把镜像帧绘制路径打炸（朝右的宠一帧都画不出来）。按
        可用性探测并回退 ``mirrored(True, False)``（6.5 起即可用，轴向等价：都是
        左右镜像，逐像素一致）。探测放在每次重建里（一个 ``getattr``，相对本条链
        上的 ``scaled``/``convertToFormat`` 可忽略），这样"运行期掉了 API"也走得通。
        """
        flipped = getattr(img, "flipped", None)
        if callable(flipped):
            return flipped(Qt.Orientation.Horizontal)
        return img.mirrored(True, False)   # 仅 Qt < 6.9 可达（6.9+ 优先走上面的 flipped）

    def _rebuild_pixmap(self) -> bool:
        """按签名缓存重建当前帧：返回是否真正重建（False = 快路径复用）。

        签名 = (clip 身份, 源帧号, 镜像, 缩放, DPR)；任一变化才走
        取帧→镜像→预乘→Smooth 缩放 整条链（每步先判恒等，见下）。转换顺序与
        window.py 一致：先转 ARGB32_Premultiplied 再缩放，避免直通 alpha 缩放
        产生暗边；缩放后的预乘图同时充任命中测试的 alpha 源（预乘不动 alpha
        字节）。镜像见 :meth:`_mirror_image`（Qt 6.9+ 用 ``flipped(Horizontal)``，
        更早的版本回退 ``mirrored(True, False)``，轴向相同：左右镜像）。
        D2：缩放目标是物理像素（CANVAS*scale*dpr），pixmap 携带
        setDevicePixelRatio(dpr)，Qt 按逻辑大小绘制，HiDPI 下不糊。

        O1 恒等短路：源已是 ARGB32_Premultiplied 就不 convert，源尺寸已是目标
        物理尺寸就不 scaled——两步都不再白调（Qt 自己对同格式 convert / 同尺寸
        Smooth 缩放已各短路到 ~1.4µs / ~2µs，故收益只在 µs 级，见证据目录
        BENCH-frame-path.txt 变换级微基准；真素材上的重建耗时差异在各档都落在
        重复测量噪声内）。结果逐位不变：输出仍是 ARGB32_Premultiplied、
        尺寸 = ``_scaled_size()``。
        """
        clip = self._clip
        if clip is None:
            return False
        try:
            frame_n = clip.currentFrameNumber()
        except AttributeError:
            frame_n = None
        sig = (id(clip), frame_n, self._mirror_frame(), self.scale, self._dpr)
        if sig == self._frame_sig:
            return False
        img = clip_current_image(clip)
        if img is None or img.isNull():
            # 首帧未就绪/素材损坏：保留上一帧（若有），跳过本次重建
            return False
        if self._mirror_frame():
            img = self._mirror_image(img)
        w, h = self._scaled_size()
        if img.format() != QImage.Format.Format_ARGB32_Premultiplied:
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if (img.width(), img.height()) != (w, h):
            img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(self._dpr)
        self._pixmap = pm
        # V-15：显式深拷贝——convertToFormat（同格式）与 scaled（同尺寸）都会
        # 返回隐式共享副本，恒等短路后 img 更可能**直接就是 clip 的活帧缓冲**，
        # 不拷贝则 _hit_image 会与解码侧的图别名（scale=1.0&dpr=1 时实测同指针），
        # 后台解码线程写缓冲时命中图被跨线程改。一帧一次 memcpy，成本可忽略
        self._hit_image = img.copy()
        self._frame_sig = sig
        return True

    def squash(self) -> None:
        """启动 Q 弹挤压（点击/真碰撞反馈，window.py _start_squash 语义）。"""
        self._squash_progress = 0.0
        rect = self.paint_bounds()
        self._notify_dirty(rect, rect)

    def _squashed_rect(self) -> QRect:
        """Q 弹帧的目标矩形（window.py:198 _squash_geometry 同式：
        pulse=sin(π·progress)，sy=1-0.15·pulse，sx=1+0.10·pulse，底中锚定）。"""
        r = self.rect()
        progress = max(0.0, min(1.0, float(self._squash_progress or 0.0)))
        pulse = math.sin(math.pi * progress)
        w = max(1, int(round(r.width() * (1.0 + 0.10 * pulse))))
        h = max(1, int(round(r.height() * (1.0 - 0.15 * pulse))))
        x = r.x() + int(round((r.width() - w) / 2))
        y = r.y() + (r.height() - h)
        return QRect(x, y, w, h)

    def paint(self, painter: QPainter) -> None:
        """把当前帧画到 overlay 的 painter 上（pos 即 overlay 局部坐标）。

        探头姿态：角度非 0 时复用 window_effects.begin_rotation/end_rotation
        绕帧绘制矩形中心旋转（与旧 _apply_pose 的 pivot = _frame_draw_rect
        中心同口径）；角度 0 时两个 helper 都是 no-op——不 save/restore、
        不产生变换，改造前路径零额外成本。Q 弹（squash）路径挂在同一层
        变换之下：先旋转，再按压缩矩形绘制。
        """
        self._rebuild_pixmap()
        if self._pixmap is None:
            return
        # 抛掷旋转（pet/sprite_throw_egg 彩蛋）：先入栈（外层），原有探头旋转
        # 在内层；同一旋转中心 → 合成等价于「探头角 + 抛掷角」（paint_bounds
        # 同口径）。角度 0（非彩蛋稳态）只多一次惰性字段读取、不进任何变换，
        # 改造前路径保持零成本。
        throw_angle = self._throw_angle
        if abs(throw_angle) >= 1e-6:
            self._begin_throw_rotation(painter, throw_angle)
        angle = self._probe_angle
        begin_rotation(painter, self.rect(), angle)
        try:
            if self._squash_progress is not None:
                painter.drawPixmap(self._squashed_rect(), self._pixmap)
            else:
                painter.drawPixmap(self.pos, self._pixmap)
        finally:
            end_rotation(painter, angle)
        if abs(throw_angle) >= 1e-6:  # 与上面的 begin 配对出栈
            self._end_throw_rotation(painter, throw_angle)

    def alpha_at(self, local: QPoint | QPointF) -> int:
        """sprite 局部**逻辑**坐标处的 alpha（0-255）。镜像已烘焙进缓存帧，无需再翻转。

        探头姿态下先把查询点逆旋转回未旋转坐标系（window_effects.
        unrotate_point，与绘制变换严格互逆）——否则 45° 探头时"画在哪"和
        "能点到哪"不一致，贴在屏幕缘的可见身体会点不中（点击拉直的入口）。
        角度 0 时逆变换是 no-op。

        命中图是物理像素（CANVAS*scale*dpr），输入逻辑坐标按「命中图物理
        尺寸 ÷ 逻辑尺寸」的实际比例换算（D2）——不用裸 dpr：物理尺寸是
        四舍五入取整，逻辑末列 ×dpr 可能越界误判穿透；按图实际比例换算
        则整幅逻辑矩形恰好覆盖整幅命中图。负数坐标 floor 到图外返回 0。
        """
        # 热路径直接读惰性字段（一次属性访问，省去 property 调用；见
        # set_throw_rotation）：抛掷角为 0 时与改造前路径同成本。
        throw = self._throw_angle
        if abs(throw) >= 1e-6:
            # 抛掷角参与命中逆变换（旧机 _effects_untransform 同口径：画在哪 =
            # 能点到哪）。绘制外层是抛掷旋转、内层是探头旋转且同中心，逆变换
            # 必须先撤外层（本步），再由下面的既有分支撤探头角——同轴逆旋转
            # 叠加，与「逆旋转 (探头角 + 抛掷角)」等价。
            lw0, lh0 = self._logical_size()
            local = unrotate_point(QPointF(local), QRect(0, 0, lw0, lh0), throw)
        img = self._hit_image
        if img is None:
            return 0
        lw, lh = self._logical_size()
        point = local
        if abs(self._probe_angle) >= 1e-6:
            point = unrotate_point(QPointF(local), QRect(0, 0, lw, lh),
                                   self._probe_angle)
        x = math.floor(point.x() * img.width() / lw)
        y = math.floor(point.y() * img.height() / lh)
        if 0 <= x < img.width() and 0 <= y < img.height():
            return (img.pixel(x, y) >> 24) & 0xFF
        return 0

    # ---------------------------------------------------------------- 拖拽协议
    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def drag_trail(self) -> list[tuple[float, float, float]]:
        """当前拖拽轨迹样本（(monotonic_ts, x, y)，只读副本；松手后清空）。"""
        return list(self._drag_trail)

    def on_press(self, pos: QPointF) -> None:
        """按下：记录 grab 偏移、velocity 归零、开始轨迹采样——**只是点击候选**。

        旧机语义（window.py:3169-3171）：位移过 ``catalog.DRAG_THRESHOLD`` 才
        算真拖拽，按下瞬间不置拖拽态（不挪窗、不切悬空动画、探头会话不取消、
        碰撞世界不当无限质量）。拖拽态的升级入口是 ``begin_drag``（由 overlay
        在过阈值时调用）。轨迹样本（时间戳 + 光标位置）是松手时
        physics.estimate_release_velocity 估算甩出初速的输入。
        """
        self._drag_offset = QPointF(pos) - self.pos
        self.velocity = QPointF(0, 0)
        self._drag_trail = [(self._clock(), pos.x(), pos.y())]

    def translate_frame(self, delta: QPointF) -> None:
        """拖拽中换局部坐标系（跨屏移交宿主）：把**绝对坐标类**样本整体平移。

        ``delta`` = 新原点 - 旧原点（全局像素）。换屏后 sprite 的局部落点由调用方
        按 ``pos - delta`` 写入；本方法只管拖拽会话里那些"位置量"：

        - ``_drag_spring_target``（弹簧档的目标点，绝对坐标）；
        - ``_drag_trail``（松手初速的输入，绝对坐标采样）。

        ``_drag_offset`` 刻意**不平移**：它是"光标 - 落点"的**差值**，同一个物理
        手势在两个坐标系里逐位相同（平移了反而把宠从光标下错开）。轨迹同理——
        整体平移后相邻样本的差值不变，``estimate_release_velocity`` 读到的仍是
        真实手势速度，不会把跨屏这一跳当成甩动。
        """
        delta = QPointF(delta)
        if delta.isNull():
            return
        if self._drag_spring_target is not None:
            self._drag_spring_target = self._drag_spring_target - delta
        if self._drag_trail:
            dx, dy = delta.x(), delta.y()
            self._drag_trail = [(t, x - dx, y - dy) for (t, x, y) in self._drag_trail]

    def begin_drag(self) -> None:
        """过 ``DRAG_THRESHOLD`` 升级为真拖拽（幂等）。

        碰撞无限质量（``_is_dragging``）、探头「drag_away」取消、拖拽悬空
        动画全部以这一刻为准——按下即生效会让每次点击闪悬空姿态、探头
        点击拉直失效、按住未动的宠在碰撞世界变成无限质量（旧机三者都不是）。
        """
        if self._dragging:
            return
        self._dragging = True
        self._drag_spring_target = None
        self._drag_spring_vel = QPointF(0.0, 0.0)
        self._spring_active = False
        self.interaction_state = INTERACTION_DRAG

    def on_move(self, pos: QPointF) -> None:
        if not self._dragging or self._drag_offset is None:
            return
        if self.drag_physics:
            # 弹簧档：只写目标点，位置由 advance 的阻尼积分追过去（不直写）
            self._drag_spring_target = QPointF(pos) - self._drag_offset
        else:
            self.set_pos(QPointF(pos) - self._drag_offset)
        now = self._clock()
        self._drag_trail.append((now, pos.x(), pos.y()))
        cutoff = now - physics_mod.TRAIL_KEEP_SEC
        self._drag_trail = [s for s in self._drag_trail if s[0] >= cutoff]

    def on_release(self, pos: QPointF) -> None:
        """松手：落在光标处，按拖拽末段轨迹决定去向（语义对齐 window.py:3352-3366）。

        估算初速 >= DEAD_ZONE_SPEED → 置 "thrown" 并写初速（
        estimate_release_velocity 内部已过 soft_clamp_speed 软膝），
        抛掷物理控制器接管；低于死区 = 原地放下，velocity 归零回
        "normal"（行为控制器随后接管）。松手位置不入轨迹——否则
        estimate 的 RELEASE_STALE_SEC「停顿即静止放下」判定失效。

        ``drop_gravity``（#219）打开时低速这一支改为**零初速**进 "thrown"，
        由现成的重力链自然落地（不再停在半空）——唯一例外是已经贴地：那一
        路走原地放下的老路（详见下方注释）。开关关 = 本方法行为与历史逐字
        相同。
        """
        if not self._dragging:
            return
        # 弹簧档松手**不 snap 到光标**：弹簧跟随的滞后位置就是起抛/落地点
        #（legacy window.py 同语义——瞬移过去再飞出去是 v4.3.0-hotfix 第一版
        # 的错，实机反馈瞬移突兀）。直跟档下宠本来就在光标处，snap 是 no-op。
        if self._drag_offset is not None and not self.drag_physics:
            self.set_pos(QPointF(pos) - self._drag_offset)
        # 初速口径不变（轨迹采样的是光标位置）；弹簧状态随拖拽结束复位。
        self._drag_spring_target = None
        self._drag_spring_vel = QPointF(0.0, 0.0)
        self._spring_active = False
        self._dragging = False
        self._drag_offset = None
        rvx, rvy = physics_mod.estimate_release_velocity(
            self._drag_trail, self._clock(), cap=self.throw_speed_cap)
        self._drag_trail = []
        if not self.drag_physics:
            rvx, rvy = 0.0, 0.0  # 拖动物理关：原地放下（window.py 同语义）
        if math.hypot(rvx, rvy) < physics_mod.DEAD_ZONE_SPEED:
            self.velocity = QPointF(0, 0)
            if self.drop_gravity and not self._at_ground():
                # 静止放手下落：零初速交给抛掷物理，重力负责落地
                self.interaction_state = INTERACTION_THROWN
            else:
                # 贴地松手不进抛掷接管：零初速 + 贴地时物理会在首个 8ms 子步
                # 就判回静止（vy 只到 g·dt≈11px/s，远低于落地截止），白付一次
                # 悬空 clip 绑定 + 整池落地预热提交（实测 3 tick 0.48→2.00ms）。
                self.interaction_state = INTERACTION_NORMAL
        else:
            self.velocity = QPointF(rvx, rvy)
            self.interaction_state = INTERACTION_THROWN

    # ---------------------------------------------------------------- 稳定身份（GLM Q6 / M-2）
    @property
    def collision_id(self) -> str:
        """碰撞世界成员稳定身份：进程内唯一、生命周期内稳定、**永不复用**。

        起因（REVIEW_VERDICT §4 GLM C4 / Q6）：碰撞世界此前用 ``id(sprite)``
        当 runtime_id，对象销毁后 CPython 复用地址 → 新 sprite 拿到老 id，
        世界侧 ``_prev_circles`` / 去抖表把新成员当成老成员，产生"幽灵扫掠"
        （对早已不存在的圆链做扫掠碰撞）。``SpriteCollisionWorld._member_id``
        优先取本字段，没有才回退 ``id()``。

        惰性分配（首次读取时才生成，不进构造路径）：本 property 与文件尾的
        身份源都是**纯加法**，不改既有行（并行改动区隔）。
        """
        cid = self.__dict__.get("_collision_id")
        if cid is None:
            cid = _next_collision_id()
            self.__dict__["_collision_id"] = cid
        return cid


def _make_collision_id_source():
    """碰撞身份序号源：进程内单调递增，永不重复（含对象销毁/地址复用后）。"""
    counter = [0]

    def _next_id() -> str:
        counter[0] += 1
        return f"pet-sprite-{counter[0]}"

    return _next_id


_next_collision_id = _make_collision_id_source()
