# -*- coding: utf-8 -*-
"""行为状态机（单合成窗架构 Phase 1b）：游荡/走路/待机/转向/点击反应。

架构背景见 .scratch/single-overlay-window/spec.md。本控制器在 overlay 的
before_sprites_advance(dt) 阶段运行（位置积分仍由 sprite.advance 统一完成）：

    class PetOverlay(OverlayWindow):
        def before_sprites_advance(self, dt):
            self.behavior.tick(self.sprites, dt)

只在 sprite.interaction_state == "normal" 时驱动掷骰链（写 velocity/facing/
bind_clip）；drag/thrown 期间位置归鼠标路由与物理控制器，本控制器只接管
画面——拖拽绑 drag 悬空动画（F1，角色包无 drag 素材回退 idle 池），抛掷
绑定飞行动画（F4）；落地静止切回 "normal" 后本控制器从当前姿态继续。

与旧窗口路径（pet/window.py，仅语义参考、禁止 import）的对应关系：
- 掷骰节奏沿用 catalog 概率：30% 待机 / 10% 转向 / 20% 移动；40% 随机
  动作池（acts）：40% 概率桶已移植（池空回退待机，语义同 window.py
  _pick_next 的 acts 分支）；
- 转向闸门沿用 inward_facing 中线滞回：掷中转向但无需纠正（带内或已朝
  内）降级待机；掷中待机但朝外且有转向素材时改播转向
  （window.py:2735-2745 语义）；
- 需要反向的移动先播 turn clip 再翻 facing（window.py:2631-2633 语义），
  翻向后立即执行已排定的移动计划（pending_move）；
- 走路目标先于朝向、步幅整圈量化（movement.quantize_move），
  velocity = 位移/时长 → 平均速度恒等于动画步态速度（防脚滑）。有
  「圈内逐帧位移曲线」（move_strides.json 的 curve）的素材改由曲线驱动
  每 tick 目标位置（F3）：静帧段不位移、动帧段推进；
- 拖拽悬空动画（F1）：按下命中即绑 drag（无素材回退 idle 池），松手切回
  待机池（window.py:3175-3176 / 3260-3264 语义）；
- 点击反应 = 打断当前行为播 click 池随机 clip，播完回待机
  （window.py:3438 _on_click 语义；音效/黄金回旋等外围效果不在本层）；
- 动画间隔（M5d，config ``animation_gap_seconds``）：动作/移动播完后强制插入
  一段待机/转向氛围步并计时，计时内待机步播完继续播氛围步，到点才回掷骰链
  （window.py:2547-2594 语义）；点击/拖拽/抛掷接管立即取消 gap。

完成判定用墙钟（tick 累加 dt ≥ clip 时长），不连 clip.finished 做状态
推进：clip 是 library 缓存的共享资源，bind_clip 已负责启停；墙钟与解码
进度的漂移上界是一个 clip 时长内的解码节流误差，到点统一 snap 收口，
误差 ≤ 一个 tick 的位移（亚像素）。但圈末 re-arm 必须接 finished（F2）：
WebMClip 圈末交付结束标记后停表，只有 start() 能续圈——多圈移动/长拖拽
的第二个圈起会冻结，故 PetSprite 转发 finished、控制器在「本状态仍有
剩余时长」时调 restart_clip()（等价 window.py:1802-1821）。

第一个例外是 ACTS 的到点收口（F8）：墙钟到点不等于末帧已交付——播放器
定时器取整（24fps→42ms、48fps→21ms，均 ≥ 名义帧长）叠加首帧延迟，一圈
素材实测比 duration 晚 80-164ms 才交付末帧，到点即换绑会把末帧（实测
237-240）一起换掉。故 ACTS 到点后还要过 `_acts_tail_done` 闸门：末帧交付
且已有一个 tick 的绘制机会、或 clip finished、或宽限到期，三者任一即收口；
判据读 clip 的 frameCount/currentFrameNumber，接口缺失或本圈从未交付过帧
则退回纯墙钟语义。收起 clip 的动作一律留在 tick 里做。

宽限本身按**实测缺口**放大（F8 残余，见 `_acts_tail_grace`）：到点那一刻
还缺多少帧，乘名义帧间隔，再夹进 [0.35s, 0.5s]。首帧延迟是负载相关量
（实测 87-384ms），常数预算在 384ms/318ms 那两轮被吃穿、丢了首圈末帧；
缺口驱动的预算随延迟一起长，实测那一档（剩余等待 ≈0.42s）落在上限之内。
超出上限的迟到量是**有界失败**：到点即按旧墙钟语义收口、尾巴几帧上不了屏，
绝不把状态机挂在等帧上（风险与取舍见 `_acts_tail_done`）。

ACTS 也**绝不接 finished 续圈**（P1）：finished 是真实时间口径、elapsed 是
tick 的 dt 累加口径，TickDriver 的 dt 上限与 GUI 卡顿会让后者落后，单圈
clip 播完时 `duration - elapsed` 仍 > 0——续圈会把整个动作从第 0 帧重播。
故 `on_clip_finished` 对 ACTS 只登记「末帧已显示一个帧间隔」的证据（不要求
等待窗口已开），续圈语义原样保留给 MOVE/DRAG/THROWN 的多圈场景——MOVE 侧多一道
`move_loops_done` 记账：计划里的**末圈**同样绝不重播（见 `_move_tail_done`）。

IDLE 的到点是**第二个例外**（与圈末重绑的实机诊断同源）：待机是无限循环的
长驻状态，墙钟到点时本圈通常还差 2-4 帧没交付（实机 frame 237/241），此刻
换绑会让 `bind_clip` 里旧 clip 的 `stop()` 落在「非圈末」——webm_clip 的软停
判据（`_natural_end_pending` + `_reader_parked`）不真，走硬停杀 ffmpeg、
`start()` 换代 spawn 新进程（实机每圈一次进程 churn），且尾巴几帧连交付/绘制
机会都没有。故 IDLE 到点后同样要过 `_idle_tail_done` 闸门：有 `finished`
契约的 clip 等 finished 登记（末帧已交付 ≠ reader 已驻留，跨线程无同步点），
无该接口的有限 clip 才退到「末帧 + 一次绘制机会」，两者都由宽限兜底——等到
的是真圈末，随后的同 clip 重绑就落在软停 + re-arm 上（webm_clip 的 park 判据
一字不动）。拿不到帧证据与损坏素材（永不 finished）按各自边界退回旧墙钟语义，
见 `_idle_tail_done`。

MOVE 是同源的**第三个例外**（`_move_tail_done`），但只推迟状态切换、不推迟
位置：墙钟到点那一 tick 仍照旧 snap 原定终点 + 速度归零（推迟会让线性档继续
积分、走过自己的终点），随后由圈末闸门决定何时进 gap/待机。24fps 的播放器定时器
取整成 42ms ≥ 名义帧长，一圈实测比 duration 晚 96ms 交付末帧、144ms 交付结束
标记，旧实现到点即 `_enter_idle`，末帧（实测 238/241）连交付机会都没有。有
`finished` 契约的 clip 等 finished 登记（末帧已交付 ≠ reader 已驻留，跨线程无
同步点）、无该接口才退到帧证据、宽限兜底；等待期不再跑曲线推进，也不反复走位。
末圈提前到达的 finished 只登记不重播（`move_loops_done` 记账区分中间圈与末圈），
中间圈的 re-arm 语义原样保留。

CLICK / TURN 是同源的**第四、五个例外**（共用 `_tail_done`）：两种一次性有限
素材原先是纯墙钟到点即切，末 2-4 帧被截（"点击没播完就弹回待机 / 转向直接翻面"）
且旧 clip 的 `stop()` 落在非圈末走硬停换代。判据与 IDLE/MOVE 同一套（有
`finished` 契约只认它，无该接口退到末帧 + 一次绘制机会，宽限兜底），TURN 的
facing 翻转随之推迟到真正收口那一刻——旧架构这两条链本来就在真末帧收口
（window.py:2652-2667 点击段、`_on_anim_ended` 的 turns 分支）。

三处「画面归它、位置不归它」的接线也在本模块：① 唱歌续播（`_continue_sing`）
——唱歌素材播完且壳报「音乐仍在放」时原地续播，绝不走 gap/掷骰链（旧机
`_on_anim_ended` 的第一条分支）；判据来自可选的 `sing_continue_provider`
（默认 None = 现状，音乐态归壳）；② 起飞预热（`_warm_landing_idles`）——进入
THROWN 时后台预热全部 idle 首帧并打飞行期 `_ffr_landing_pinned`，落地/飞行被
拖拽打断时按清单精确摘除（旧机 `_warm_landing_idles` / `_unpin_landing_idles`），
同一飞行窗口内重复进入只提交一次（`warm_landing_submitted`）；③ 飞行边沿
（`_sync_flight_edge`）——istate 进出 THROWN 的那一 tick 各回调一次可选的
`on_flight_changed(sprite, flying)`（默认 None = 现状），消费方据此在飞行期
禁气泡：行为层只报边沿，气泡语义归壳（`overlay_shell._bubble_blocked`）。
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from weakref import WeakKeyDictionary

from PySide6.QtCore import QPointF, QRect

from . import catalog, movement
from .pet_sprite import INTERACTION_DRAG, INTERACTION_NORMAL, INTERACTION_THROWN
from .predictive_prewarm import PredictivePrewarm

logger = logging.getLogger(__name__)

# 预测预热共享执行器（单 worker，全控制器共用）：首帧解码（ffmpeg spawn
# ~50ms）绝不占 GUI 线程——py-spy 慢帧归因（2026-09-24）：_maybe_predict →
# _warm → warm_first_frame 曾在 tick_sim 里同步拉起 ffmpeg，是 50-56ms
# 周期性慢帧主力。warm_first_frame 本身按线程安全设计（仅 QImage + 解码
# 进程，webm_clip.py 的 docstring 明言「后台线程…仅 QImage，线程安全」）；
# clip 的获取仍留在 GUI（QObject 线程亲和），只有解码动作下 worker。
_WARM_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="sprite-prewarm")

STATE_IDLE = "idle"
STATE_MOVE = "move"
STATE_TURN = "turn"
STATE_CLICK = "click"
STATE_ACTS = "acts"
#: 拖拽接管态（F1）：画面 = drag 悬空动画，位置归鼠标；不属于掷骰链
STATE_DRAG = "drag"
#: 抛掷飞行接管态（F4）：画面 = drag（悬空）动画循环 + 物理按速度加速播放；
#: 位置归 sprite_physics；落地回 normal 后由 tick 收口回待机
STATE_THROWN = "thrown"

#: 本控制器「画面归它、位置不归它」的状态（F1/F4）：sprite 已回 normal
#: 却停在这些态 = 收尾接线缺失（看门狗/shell 分支没走到），必须自愈回待机
_CAPTURED_STATES = (STATE_DRAG, STATE_THROWN)

#: 圈末 finished 需要原地续播（re-arm）的状态（F2/F4）：这些状态的时长可以
#: 跨多圈（多圈移动 / 循环拖拽 / 飞行悬空循环），中间圈结束必须重播，
#: 否则第 2 圈起冻结
_REARM_STATES = (STATE_MOVE, STATE_DRAG, STATE_ACTS, STATE_THROWN)

#: ACTS 末帧收口（F8）：墙钟到点（elapsed ≥ duration）不代表末帧已交付。
#: 实机缺帧窗口（三条臂，241/481 帧素材）：帧序列臂 239-240 两帧、webm 臂
#: 237-240 四帧、481 帧 @48fps 臂 478-480 三帧——末帧比 duration 晚 80-164ms
#: 才交付（播放器定时器取整 42ms/21ms ≥ 名义帧长，再叠加冷启动首帧延迟）。
#: 宽限 = 常数项与**缺口项**取大，再夹到 [下限, 硬上限]（见 _acts_tail_grace）：
#: 常数项 = 4× 名义帧间隔与下限取大（慢素材的既有余量，口径不变）；缺口项 =
#: 到点那一刻实测还缺的帧数 × 名义帧间隔 × 安全系数（F8 残余：首帧延迟是负载
#: 相关量，实测 87-384ms，常数预算会被吃穿）。到点仍未等到末帧/结束标记就按
#: 旧墙钟语义收口——绝不把状态机挂在这里，也绝不让慢素材把它拖成秒级。
_ACTS_TAIL_GRACE_FRAMES = 4
#: 宽限下限（秒）：24fps 帧间隔 42ms × 4 帧 ≈ 168ms，再留出启动/负载余量
_ACTS_TAIL_GRACE_MIN_S = 0.35
#: 缺口项安全系数（F8 残余）：名义帧间隔取 duration/frames（源帧长），比播放器
#: 定时器的取整口径小 0.8%（24fps：41.67 < 42ms），再乘 1.25 覆盖该取整差、
#: 逐 tick 的量化误差与「估计值恰好等于真实剩余」的临界档。
_ACTS_TAIL_GRACE_SAFETY = 1.25
#: 宽限硬上限（秒）：任何素材、任何负载下 ACTS 收口最多多等这么久
_ACTS_TAIL_GRACE_MAX_S = 0.5

#: IDLE 圈末等待预算（秒，见 `_idle_tail_done`）：墙钟到点后仍等圈末证据
#: （finished / 末帧已交付）的上限，超出即按旧墙钟语义掷骰（有界失败，丢尾巴
#: 几帧）。取值与 ACTS 末帧收口的硬上限同一量级：一圈素材的圈末通常只差 2-4
#: 帧（实机 80-164ms），冷启动首帧迟到实测最长 384ms，两档都在界内；再大的
#: 迟到量按有界失败处理，绝不把掷骰链挂在等帧上。
_IDLE_TAIL_GRACE_S = 0.5

#: MOVE 圈末等待预算：直接复用 IDLE 那一档 0.5s（见 `_move_tail_done`），不另开
#: 常数。位置在到点那一 tick 就 snap 收口，等待只推迟**状态切换**（gap/待机），
#: 不推迟位移；实测缺口 96ms（末帧）/144ms（结束标记）落在界内，与 IDLE 同档。
#: 三档（ACTS/IDLE/MOVE）共用同一条有界失败纪律：拿不到圈末证据时最多多等
#: 这么久，绝不把决策链挂在等帧上。
#: CLICK/TURN（`_tail_done`）同用 IDLE 这一档：它们同样是有限一次性素材、
#: 圈末同样只差 2-4 帧，没有按缺口放大的理由。


def _sing_anim_name() -> str:
    """唱歌动画名（与旧架构同源常量，杜绝第二套字符串）。

    惰性 import：``overlay_shell._sing_anim`` / ``window_alerts.check_music_sing``
    都是 ``from .window import SING_ANIM`` 这一既定依赖面（本模块不 import
    window，只在 ``sing_continue_provider`` 真被接线时才付一次模块查找——未开
    音乐唱歌的进程不多付 ``pet.window`` 的导入）。
    """
    from .window import SING_ANIM
    return SING_ANIM


class _SpriteState:
    """单个 sprite 的行为状态（控制器私有，不落在 sprite 上）。"""

    __slots__ = ("state", "anim", "elapsed", "duration", "move_target",
                 "pending_move", "predictor", "curve", "frames_per_loop",
                 "loops", "loop_duration", "move_start", "suspended",
                 "gap_remaining", "acts_tail_grace", "acts_final_seen",
                 "acts_clip_finished", "idle_tail_grace", "idle_final_seen",
                 "idle_clip_finished", "move_tail_grace", "move_final_seen",
                 "move_clip_finished", "move_loops_done", "click_tail_grace",
                 "click_final_seen", "click_clip_finished", "turn_tail_grace",
                 "turn_final_seen", "turn_clip_finished", "landing_pinned",
                 "was_flying", "warm_landing_submitted")

    def __init__(self) -> None:
        self.state = STATE_IDLE
        self.anim: str | None = None      # 当前绑定的 clip 名（None = 尚未起播）
        self.elapsed = 0.0                # 当前状态已流逝（tick 累加的 dt）
        self.duration = 0.0               # 当前 clip 时长（秒）
        self.move_target: QPointF | None = None
        self.pending_move: dict | None = None  # 反向前先转向的移动计划
        self.predictor = None                  # tick 创建状态时挂 PredictivePrewarm
        # 移动计划（F3）：圈内逐帧位移曲线的位置解算输入。无 curve 的角色
        # 保持线性（curve=None，velocity 在 _start_move 一次算好）
        self.curve: list | None = None    # 圈内累计进度曲线（curve[i] = 源帧 i）
        self.frames_per_loop = 0          # 每圈源帧数（曲线相位折算用）
        self.loops = 1                    # 计划整圈数
        self.loop_duration = 0.0          # 单圈墙钟时长（秒）
        self.move_start: QPointF | None = None  # 计划起点（曲线绝对位置锚点）
        # 被接管标记（F5）：tick 见过非 normal 即置位；回到 normal 的那一
        # tick 据此判断「接管前的移动/转向计划必须撤销，绝不 snap」
        self.suspended = False
        # 动画间隔剩余时长（秒，0 = 不在 gap；M5d，window.py:2547-2594 语义）：
        # 动作/移动播完后强制插入待机/转向氛围步，不让动作连着动作地刷
        self.gap_remaining = 0.0
        # ACTS 末帧收口（F8，见 _acts_tail_done）：墙钟到点后的末帧等待窗口。
        # acts_tail_grace = None 表示本圈尚未到点（窗口未开）；开窗时按**实测
        # 缺口**算好预算（_acts_tail_grace），随后按 tick 递减，到 0 仍未等到
        # 末帧/结束标记就按旧墙钟语义收口（有界失败，见 _acts_tail_done）。
        # 三个字段在每次换绑 clip（_bind_with_gen / play_once）时一律清空——
        # 旧 clip 的末帧证据绝不跨代存活（代次守卫）
        self.acts_tail_grace: float | None = None
        self.acts_final_seen = False       # 本圈末帧已交付（观测过）
        # 本圈 finished 已到（= 末帧已显示一个帧间隔）。可在窗口开启前就置位：
        # dt 上限/卡顿会让 elapsed 落后真实时间，单圈 clip 可能先播完（P1）
        self.acts_clip_finished = False
        # IDLE 圈末收口（见 _idle_tail_done）：墙钟到点后的圈末等待窗口，语义与
        # ACTS 的三字段一一对应（窗口未开 = None；预算固定 _IDLE_TAIL_GRACE_S，
        # 待机不按缺口放大——圈末通常只差 2-4 帧）。同样在每次换绑 clip 时清空：
        # 旧 clip 的圈末证据绝不跨代存活（点击/拖拽打断后的旧 finished 落进新
        # 绑定就是这条路）
        self.idle_tail_grace: float | None = None
        self.idle_final_seen = False       # 本圈末帧已交付（观测过）
        self.idle_clip_finished = False    # 本圈 finished 已到（可早于墙钟到点）
        # MOVE 圈末收口（见 `_move_tail_done`）：与 IDLE 三件套一一对应，差别在
        # 「位置不归它」——到点那一 tick 仍照旧 snap + 速度归零，窗口只决定何时
        # 切 gap/待机。move_loops_done = 本次绑定已收尾的圈数（中间圈续播记账）：
        # 末圈的 finished 提前到达时靠它区分「该续圈」与「该登记末圈证据」，
        # 否则最后一圈会被重播一整圈。四字段同样在每次换绑 clip
        # （_bind_with_gen / play_once）时清空——旧 clip 的圈末证据与续圈记账
        # 绝不跨代存活（代次守卫）
        self.move_tail_grace: float | None = None
        self.move_final_seen = False       # 本圈末帧已交付（观测过）
        self.move_clip_finished = False    # 本圈末圈 finished 已到（可早于墙钟到点）
        self.move_loops_done = 0           # 本次绑定已收尾的圈数（中间圈 re-arm 计数）
        # CLICK / TURN 圈末收口（与 IDLE/ACTS/MOVE 同款三件套，见 `_tail_done`）：
        # 两种一次性有限动画共用一处闸门实现，窗口字段仍按状态各存一组（代次
        # 守卫语义逐位相同：每次换绑一并清空）。旧架构这两条链也由真末帧收口
        # （window.py:2652-2667 点击段、_on_anim_ended 的 turns 分支）
        self.click_tail_grace: float | None = None
        self.click_final_seen = False
        self.click_clip_finished = False
        self.turn_tail_grace: float | None = None
        self.turn_final_seen = False
        self.turn_clip_finished = False
        # 飞行期落地首帧 pin（旧 window.py:4356-4420 语义）：非空 = 本 sprite 的
        # idle 首帧正受「飞行期绝不逐出」保护，落地 / 飞行被拖拽打断时按这份
        # 清单精确摘除（读清单而不是重查 idle 池：飞行中换角色后池已变，重查
        # 会摘错对象、把上一只库的 pin 永久留在全局首帧预算里）
        self.landing_pinned: list = []
        # 飞行边沿状态（B1）：上一次 tick 见到的"在飞"判定，用于在 istate 进出
        # THROWN 的那一 tick 各回调一次注入方（见 _sync_flight_edge）
        self.was_flying = False
        # 本次飞行窗口（起飞 → 落地/被拖拽打断）是否已向 _WARM_EXECUTOR 提交过
        # 落地首帧预热（B2）：同一窗口内重复 _enter_thrown 不再重复 submit。
        # 与 landing_pinned 同生共死（在 _unpin_landing_idles 一并复位）
        self.warm_landing_submitted = False


class BehaviorController:
    """一组 sprite 的行为驱动：tick 推进状态机，on_sprite_clicked 接点击路由。

    bounds：活动边界（overlay 局部坐标的 QRect，语义 = 旧架构的
    availableGeometry）。本模块不查 QScreen，边界由集成层传入，保持
    offscreen 可测。rng：可注入随机源（需有 random/randint/choice，
    默认 random 模块），测试注入确定性实现。
    """

    def __init__(
        self,
        bounds: QRect,
        *,
        rng=None,
        margin: int = catalog.MOVE_MARGIN,
        min_distance: int = catalog.MOVE_MIN_PX,
        max_distance: int = catalog.MOVE_MAX_PX,
    ) -> None:
        self.bounds = QRect(bounds)
        self.rng = rng if rng is not None else random
        self.margin = margin
        self.min_distance = min_distance
        self.max_distance = max_distance
        # 不移动开关（菜单/config 写入口）：移动桶并入动作池（window.py
        # _pick_next 的 no_move 语义）
        self.no_move = False
        # 动画间隔（config animation_gap_seconds，壳 _sync_sprite_settings 注入）：
        # 动作/移动播完后强制插入一段待机/转向氛围步（M5d，window.py:2561-2576）
        self._animation_gap_seconds = 0.0
        # 预测式预热（批10-A1 语义移植）：每 sprite 一个 PredictivePrewarm
        # （预测是按 sprite 的素材池掷的，控制器级共享会跨池串名）。
        # 提前量沿用旧默认 350ms；消费规则单源在 predictive_prewarm.consume
        self._predict_lead_s = 0.35
        # 预测预热总开关（config predict_prewarm_lead_ms>0 映射；测试可关）
        self.predict_enabled = True
        # 唱歌续播判据（window.py:2595-2601 语义）：callable → bool，None = 未接线
        # （默认即现状——唱歌 clip 播完照旧走掷骰链）。音乐态归壳（``_music_sing_active``
        # 由 window_alerts 轮询维护），行为层不查音频，只问这一面。
        # 壳侧接法（一行）：``self.behavior.sing_continue_provider = lambda:
        # self._music_sing_active``
        self.sing_continue_provider = None
        # 飞行边沿回调（B1，可选）：``callable(sprite, flying) -> None``，只在
        # istate 进出 ``INTERACTION_THROWN`` 的那一 tick 各调一次（首次接管、
        # 空中被抓住、落地、飞行中被移除都算边沿）。行为层只报边沿，气泡语义
        # 归消费方（overlay 壳据此禁飞的那只的气泡）；未接线 = 现状。
        self.on_flight_changed = None
        self._states: dict = {}
        # V-9：按库对象弱引用缓存——旧实现以 id(lib) 为键，库销毁后地址
        # 被新库复用会命中陈旧分类池（换角色/多宠生灭时拿到错素材名），
        # 且条目只增不减；弱键字典在库销毁时自动回收
        self._cats_cache: WeakKeyDictionary = WeakKeyDictionary()

    # ---------------------------------------------------------------- 动画间隔（M5d）
    @property
    def animation_gap_seconds(self) -> float:
        """动作/移动播完后的强制间隔（秒；0 = 关，window.py:2561-2576 语义）。"""
        return self._animation_gap_seconds

    @animation_gap_seconds.setter
    def animation_gap_seconds(self, value) -> None:
        """写入口（壳按 config 同步）：改 0 立即取消在跑的 gap（旧机
        ``_cancel_animation_gap`` 的 refresh 分支，window.py:3858-3867）。"""
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            seconds = 0.0
        self._animation_gap_seconds = max(0.0, min(3600.0, seconds))
        if self._animation_gap_seconds <= 0.0:
            for st in self._states.values():
                st.gap_remaining = 0.0

    @staticmethod
    def _cancel_gap(st: _SpriteState) -> None:
        """立即结束 gap（点击/拖拽接管打断氛围步，window.py:_cancel_animation_gap）。"""
        st.gap_remaining = 0.0

    # ---------------------------------------------------------------- 对外 API
    def tick(self, sprites, dt: float) -> None:
        """推进一轮：normal 走掷骰链，drag/thrown 只做画面接管。

        非 normal 的 sprite 不推进状态机（位置归鼠标/物理），但画面必须
        绑对动画（F1 拖拽悬空 / F4 飞行循环），故仍进入本循环。
        """
        for sprite in sprites:
            self._ensure_hooks(sprite)
            st = self._states.get(sprite)
            if st is None:
                st = self._states[sprite] = _SpriteState()
                st.predictor = self._make_predictor(sprite)
            istate = getattr(sprite, "interaction_state", INTERACTION_NORMAL)
            # 飞行边沿（B1）：走路就是这么走的——在既有的 istate 读取处顺带
            # 判一次边沿，零额外查询、不新增 timer/线程（见 _sync_flight_edge）
            self._sync_flight_edge(sprite, st, istate == INTERACTION_THROWN)
            if istate != INTERACTION_NORMAL:
                st.suspended = True
                self._tick_captured(sprite, st)
                continue
            if st.suspended:
                # 接管结束后的收口（F5，window.py:4174-4177 _enter_physics_mode
                # →_cancel_move 的 sprite 版）：接管期间位置已被鼠标/物理改写，
                # 接管前的移动/转向计划一律作废——绝不 set_pos(旧目标) snap，
                # 否则松手后一到 duration 就瞬移回原路线终点
                st.suspended = False
                if st.state in (STATE_MOVE, STATE_TURN):
                    self._enter_idle(sprite, st, self._categories(sprite.library))
            if st.state in _CAPTURED_STATES:
                # 接管已结束却停在接管态（看门狗收尾/shell 分支没走到）：
                # 自愈回待机链，否则悬空动画无限循环
                self._enter_idle(sprite, st, self._categories(sprite.library))
            self._tick_sprite(sprite, st, dt)

    def on_drag_started(self, sprite) -> None:
        """鼠标按下命中 sprite（overlay 拖拽接线入口）：播 drag 悬空动画。

        进 STATE_DRAG 并绑 drag clip；角色包未提供 drag 素材时回退 idle 池
        （旧机 ``if self.drag:`` 同款回退，window.py:3175-3176）。拖拽期间
        位置由鼠标驱动，本方法绝不改写 velocity/pos。
        """
        self._ensure_hooks(sprite)
        st = self._states.get(sprite)
        if st is None:
            st = self._states[sprite] = _SpriteState()
            st.predictor = self._make_predictor(sprite)
        self._enter_drag(sprite, st)

    def on_drag_released(self, sprite) -> None:
        """真拖拽松手（非点击候选）：切回待机池。

        松手被判为甩出（interaction_state == "thrown"）时不得收尾——飞行段
        归 sprite_physics，落地回 normal 后由 tick 自愈回待机
        （window.py:3260-3264 只处理原地放下分支，同语义）。
        """
        if getattr(sprite, "interaction_state", INTERACTION_NORMAL) == INTERACTION_THROWN:
            return
        st = self._states.get(sprite)
        if st is None:
            return
        self._enter_idle(sprite, st, self._categories(sprite.library))

    def on_sprite_clicked(self, sprite) -> bool:
        """点击反应（overlay 鼠标路由接线入口）：播 click 池随机 clip。

        可打断当前任何行为（含进行中的移动/另一次点击）；播完回待机。
        非 normal 状态或无 click 素材返回 False（调用方可据此放行穿透）。
        """
        if getattr(sprite, "interaction_state", INTERACTION_NORMAL) != INTERACTION_NORMAL:
            return False
        cats = self._categories(sprite.library)
        if not cats["clicks"]:
            return False
        st = self._states.get(sprite)
        if st is None:
            st = self._states[sprite] = _SpriteState()
        name = self._pick(cats["clicks"], exclude=st.anim)
        st.state = STATE_CLICK
        st.anim = name
        st.elapsed = 0.0
        st.duration = self._plan_duration(sprite, name)
        st.pending_move = None
        self._cancel_gap(st)  # 点击打断 gap（window.py:2532 点击动画结束取消 gap）
        self._clear_move_plan(st)
        sprite.set_velocity(QPointF(0, 0))
        self._bind_with_gen(sprite, st, name)
        return True

    def play_once(self, sprite, name: str) -> bool:
        """一次性播放指定动画，播完回掷骰链（菜单「播放动画」入口，
        window.py switch_clip 语义）。

        入口守卫（旧 window.py:1624-1629 ``if name not in self.lib.names()``）：
        动画名不在当前素材库时直接失败，绝不置 ACTS——否则 ``library.movie()``
        抛的 KeyError 虽被壳吞掉，``st.state/st.anim`` 已置为 ACTS+陌生名，出现
        一次可长达 0.5s 的「假 ACTS」（期间 agent 联动回待机请求被
        ``_link_anim_busy()`` 挡住）。库未暴露 ``names()``（测试替身/轻量库）
        时跳过守卫，保持既有语义。
        """
        if getattr(sprite, "interaction_state", INTERACTION_NORMAL) != INTERACTION_NORMAL:
            return False
        names = getattr(sprite.library, "names", None)
        if callable(names):
            try:
                known = set(names())
            except Exception:
                known = None
            if known is not None and name not in known:
                return False
        st = self._states.setdefault(sprite, _SpriteState())
        st.state = STATE_ACTS
        st.pending_move = None
        self._clear_move_plan(st)
        self._clear_acts_tail(st)  # 新一圈 ACTS：旧末帧证据作废（F8）
        self._clear_idle_tail(st)  # 新绑定：旧 IDLE 圈末证据作废（同款代次守卫）
        self._clear_move_tail(st)  # 新绑定：旧 MOVE 圈末证据/续圈记账同样作废
        self._clear_tail(st, "click")  # 旧 CLICK 圈末证据不得跨代存活
        self._clear_tail(st, "turn")
        sprite.set_velocity(QPointF(0, 0))
        st.elapsed = 0.0
        st.anim = name
        st.duration = self._plan_duration(sprite, name)
        sprite.bind_clip(name)
        return True

    def play_move_once(self, sprite, name: str) -> bool:
        """以指定移动素材触发一次移动（菜单「移动」类入口；无可达空间
        回退动作池，window.py trigger_move 语义）。"""
        if getattr(sprite, "interaction_state", INTERACTION_NORMAL) != INTERACTION_NORMAL:
            return False
        cats = self._categories(sprite.library)
        if name not in cats["moves"]:
            return False
        st = self._states.setdefault(sprite, _SpriteState())
        if not self._plan_move(sprite, st, cats, anim_override=name):
            self._enter_acts(sprite, st, cats)
            return False
        return True

    def state_of(self, sprite) -> str | None:
        """当前行为状态（idle/move/turn/click/acts/drag）；未接管过返回 None。"""
        st = self._states.get(sprite)
        return st.state if st is not None else None

    def anim_of(self, sprite) -> str | None:
        """当前绑定的 clip 名（None = 尚未接管/未起播）。

        只读访问器：点击台词绑定（``config.click_talk_texts_for``）要按"这次
        点中了哪条 click 动画"查表，而 ``on_sprite_clicked`` 的返回值是
        「是否消费点击」契约，不能挪用；故单开这一面，不改变点击语义。
        """
        st = self._states.get(sprite)
        return st.anim if st is not None else None

    def on_clip_finished(self, sprite) -> None:
        """clip 圈末结束（PetSprite.finished 转发）：IDLE/ACTS/CLICK/TURN/MOVE 只登记证据，其余按剩余时长续圈。

        WebMClip 圈末交付结束标记后停表，只有 start() 能 re-arm
        （webm_clip.py:1521-1567），不续则多圈移动的第 2 圈起、长拖拽过圈末
        全部冻结在末帧。等价旧 window.py:1802-1821 _restart_current_clip。

        续圈判据用剩余时长（墙钟口径，见模块头）：末圈结束（剩余 ≤ 0）绝不
        续——收口交给 tick 的到点 snap，否则已完成的移动会被重新起播一整圈。
        MOVE 另按 ``move_loops_done`` 记账：计划里的最后一圈同样绝不续（见下）。

        ACTS 一律不续圈（P1 修正）：ACTS 计划本就只有一圈，而 finished 是
        **真实时间**口径（播放器定时器 + 解码），``st.elapsed`` 是 tick 的 dt
        累加口径——TickDriver 的 dt 上限/GUI 卡顿会让后者落后，于是单圈 clip
        已播完时 ``duration - elapsed`` 仍 > 0，旧代码在这里 restart_clip()
        把整个动作从第 0 帧重播（视觉闪回）且白付一次解码。故此分支只登记
        「末帧已有一个帧间隔显示机会」的证据（finished 由两种播放器在末帧
        之后一个帧间隔才发），切换留给 tick 的 _acts_tail_done——绝不在信号
        回调里换绑/重启（会重入解码）。

        IDLE 同款（第二个例外，见 _idle_tail_done）：待机是无限循环的长驻状态，
        圈末既要续圈又要按掷骰链换绑，判据归 tick 的圈末闸门；此处只登记
        「本圈已播完」的证据，绝不在信号栈里换绑（同款重入风险）。

        MOVE 是第三个（见 _move_tail_done）：末圈的 finished 只登记、绝不
        restart_clip()。finished 是真实时间口径而 elapsed 是 dt 累加口径，dt 上限/
        卡顿会让**最后一圈**的 finished 在 ``duration - elapsed > 0`` 时到达，
        旧口径在这里把末圈重播一整圈（画面从末帧闪回第 0 帧，位置却已按墙钟走到
        终点）；中间的圈仍照旧 re-arm，多圈轨迹不受影响。

        CLICK / TURN 是第四、五处（见 ``_tail_done``）：一次性有限素材，同样
        只登记证据、不续圈、不在信号栈里换绑（旧架构这两条链也在真末帧收口）。
        """
        st = self._states.get(sprite)
        if st is None:
            return
        if st.state == STATE_IDLE:
            st.idle_clip_finished = True
            return
        if st.state == STATE_CLICK:
            # 点击动画是一次性有限素材：只登记「本圈已播完」的证据，收口
            # 留给 tick 的 ``_tail_done``（信号栈里换绑会重入解码）
            st.click_clip_finished = True
            return
        if st.state == STATE_TURN:
            st.turn_clip_finished = True
            return
        if st.state not in _REARM_STATES:
            return
        if st.state == STATE_ACTS:
            st.acts_clip_finished = True
            return
        if st.state == STATE_MOVE:
            # MOVE 的圈末一律只登记，重播/切换全留给 tick：信号栈里换绑会重入
            # 解码，且此刻状态机可能正开在圈末等待窗口上（见 _move_tail_done）。
            # 两种「不重播」：① 墙钟已到点 = 末圈（位移已由 tick snap 收口）；
            # ② 本圈是计划里的**最后一圈**却提前发 finished——dt 上限/GUI 卡顿
            # 让 elapsed 落后真实时间时会发生（finished 是真实时间口径），旧口径
            # 按「duration - elapsed > 0」在这里 restart_clip() 重播整圈；
            # 中间的圈照旧续圈，多圈轨迹一分不变。
            if st.elapsed >= st.duration or st.move_loops_done + 1 >= st.loops:
                st.move_clip_finished = True
                return
            st.move_loops_done += 1
            sprite.restart_clip()
            return
        if st.duration - st.elapsed <= 0.0:
            return
        sprite.restart_clip()

    def forget(self, sprite) -> None:
        """sprite 从 overlay 移除时清理其状态（可选，防状态表只增不减）。

        同时摘掉飞行期 pin：pin 是打在 clip 上的（全局首帧预算按它免逐出），
        sprite 若在飞行中被移除而清单随状态表一起丢弃，那些首帧会永久挂在
        预算里（库销毁前无人再摘）。

        飞行中被移除（托盘退出/换角色）还要补一次"落地"边沿：消费方（壳的
        飞行集合）靠边沿维护簿记，漏掉这一次就会永久留着这只已注销的 sprite
        （强引用 + 它的气泡门禁状态永远是"在飞"）。

        圈末回调的挂接权一并交还：``_ensure_hooks`` 以 owner 判"是否已挂"，把
        这只 sprite 交给**另一个**世界（跨屏移交宿主）时若仍记着本控制器，
        新世界永不接管圈末续播（多圈移动/长拖拽在圈末冻结）。
        """
        st = self._states.pop(sprite, None)
        if getattr(sprite, "_clip_finished_owner", None) is self:
            sprite._clip_finished_cb = None
            sprite._clip_finished_owner = None
        if st is None:
            return
        self._unpin_landing_idles(st)
        if st.was_flying:
            st.was_flying = False
            self._notify_flight_changed(sprite, False)

    def _ensure_hooks(self, sprite) -> None:
        """把 clip 圈末回调挂到 sprite（F2），每个 sprite 只挂一次。

        挂接方必须是本控制器：只有它知道当前状态的剩余时长（多圈移动的
        中间圈续、末圈不续），sprite 侧只做转发（PetSprite._on_clip_finished）。
        """
        if getattr(sprite, "_clip_finished_owner", None) is self:
            return
        sprite._clip_finished_cb = self.on_clip_finished
        sprite._clip_finished_owner = self

    # ---------------------------------------------------------------- 飞行边沿（B1）
    def _sync_flight_edge(self, sprite, st: _SpriteState, flying: bool) -> None:
        """飞行边沿检测：istate 进/出 THROWN 各回调一次注入方。

        边沿源是**唯一权威**的 ``interaction_state``（飞行由 sprite_physics
        写入、落地也由它复位），故本方法每 tick 只做一次布尔比较——不新增
        timer/线程、不轮询位置、不碰物理。回调只在边沿发生，稳态零开销。
        """
        flying = bool(flying)
        if flying == st.was_flying:
            return
        st.was_flying = flying
        self._notify_flight_changed(sprite, flying)

    def _notify_flight_changed(self, sprite, flying: bool) -> None:
        """调用注入的飞行边沿回调；未接线 = no-op，回调异常绝不掀掉 tick。

        消费失败只降级为「气泡门禁没跟上」（表现 = 飞行期多冒一次泡），
        绝不能因此打断位置积分/状态机。
        """
        callback = self.on_flight_changed
        if callback is None:
            return
        try:
            callback(sprite, bool(flying))
        except Exception:
            logger.exception("飞行边沿回调失败 sprite=%r flying=%s", sprite, flying)

    # ---------------------------------------------------------------- 接管态画面
    def _tick_captured(self, sprite, st: _SpriteState) -> None:
        """非 normal（drag/thrown）阶段的画面接管（F1/F4）。

        位置由鼠标路由与物理控制器负责，本控制器只保证「当前该播什么」：
        拖拽 = 悬空 clip（window.py:3175-3176 进拖拽切 drag），抛掷 = 飞行
        循环 clip（window.py:2487-2499）。绝不推进掷骰链、绝不改写
        velocity——旧断言「非 normal 从不被接管」在此扩为「只接受 drag
        绑定，不接受掷骰驱动」。
        """
        istate = getattr(sprite, "interaction_state", INTERACTION_NORMAL)
        if istate == INTERACTION_DRAG:
            if st.state != STATE_DRAG:
                self._enter_drag(sprite, st)
        elif istate == INTERACTION_THROWN and st.state != STATE_THROWN:
            self._enter_thrown(sprite, st)

    # ---------------------------------------------------------------- 状态机
    def _tick_sprite(self, sprite, st: _SpriteState, dt: float) -> None:
        # 边缘探头会话（sprite.probe_active = 曝光<1）的 sprite：它自己的钳制
        # 域已被放宽（身体按曝光比例藏出屏幕缘），这里不能再按常规 bounds 钳，
        # 否则探头姿态每 tick 被拉回屏内。游荡同样要拦（见 _plan_move）：位置
        # 归探头控制器唯一所有。
        probing = bool(getattr(sprite, "probe_active", False))
        if probing and st.state == STATE_MOVE:
            # 进场时若恰在移动态（速度刚好低于静止阈值）：先收尾回待机，否则
            # 到点 snap 会把探头姿态一脚踹回走路目标点
            self._enter_idle(sprite, st, self._categories(sprite.library))
        if not probing and getattr(sprite, "_clamp_rects", None) is None:
            # 跨屏过渡（这只的合法域是多块屏的并集）期间不按本屏 bounds 硬钳：
            # 身体还在缝上时钳一次就是第二次 snap，而它此刻的合法位置由 sprite
            # 自己的域说了算（角点/空洞仍不可达）。收窄域的时机由壳按"身体已完整
            # 落在某块屏内"决定，不由这里抢先。
            self._clamp_into_bounds(sprite)
        st.elapsed += dt
        if st.gap_remaining > 0.0:
            # gap 按墙钟流逝（旧机 QTimer 口径；dt 由 tick 驱动，降档时同步放大）
            st.gap_remaining = max(0.0, st.gap_remaining - dt)
        if st.state == STATE_IDLE:
            if st.anim is None:
                self._enter_idle(sprite, st, self._categories(sprite.library))
            elif st.elapsed >= st.duration:
                # 到点 ≠ 本圈播完（IDLE 圈末收口）：先过圈末闸门再换绑——换绑要把
                # bind_clip 里旧 clip 的 stop() 落在真圈末上（webm_clip 的软停
                # 判据据此成立 → 同 clip 重绑走 re-arm 而非硬停换代），否则实机
                # 每圈一次 ffmpeg 进程 churn 且尾巴几帧连绘制机会都没有
                # （见 _idle_tail_done）
                if self._idle_tail_done(sprite, st, dt):
                    # gap 内待机步播完继续播氛围步；gap 到点才回掷骰链（旧机
                    # _on_anim_ended 的 _animation_gap_active 分支）
                    if st.gap_remaining > 0.0:
                        self._play_animation_gap_step(sprite, st)
                    else:
                        self._roll_next(sprite, st)
            else:
                self._maybe_predict(sprite, st)
        elif st.state == STATE_MOVE:
            if st.elapsed >= st.duration:
                # 到点那一 tick 仍照旧收口位置：snap 原定终点 + 速度归零。这一步
                # 绝不推迟到等帧之后——等待期继续按曲线/线性积分会走过自己的终点
                # （线性档每 tick 0.32px），位移超发比截尾更难解释。只 snap 一次：
                # 窗口已开（move_tail_grace 非 None）就不再重复走位
                if st.move_tail_grace is None:
                    if st.move_target is not None:
                        sprite.set_pos(st.move_target)  # 到点 snap，消除积分残差
                    sprite.set_velocity(QPointF(0, 0))
                    # 到点收口期间不再 _apply_move_curve：末帧到位前速度恒 0、
                    # 位置钉在终点（等待只推迟状态切换，见 _move_tail_done）
                if self._move_tail_done(sprite, st, dt):
                    if not self._start_animation_gap(sprite, st):
                        self._enter_idle(sprite, st, self._categories(sprite.library))
            else:
                self._apply_move_curve(sprite, st, dt)
        elif st.state == STATE_TURN:
            # 到点不等于本圈播完：先过圈末闸门（`_tail_done`）——否则末 2-4 帧被截、
            # 旧 clip 的 stop() 落在非圈末走硬停换代，facing 也会提前翻
            if (st.elapsed >= st.duration
                    and self._tail_done(sprite, st, dt, "turn")):
                if probing:
                    # 探头会话只允许待机/转向且冻结朝向（F7，
                    # window_optional_services.py:223-233/349-350）：turn 播完
                    # 不翻 facing；排定的移动计划一并作废（会话期间位置归
                    # 探头控制器，绝不起步——起步会同时把朝向翻过去）
                    st.pending_move = None
                    self._enter_idle(sprite, st, self._categories(sprite.library))
                else:
                    # 转向播完才翻朝向（window.py:2631-2633）：turn clip 播完
                    # 即画面已转向，此刻翻 facing 无跳变。
                    sprite.facing = "right" if sprite.facing == "left" else "left"
                    pending = st.pending_move
                    st.pending_move = None
                    if pending is None:
                        # gap 步可能是转向素材（池 = idles+turns）：gap 内续播
                        # 下一段氛围步，到点回待机
                        if not self._start_animation_gap(sprite, st):
                            self._enter_idle(
                                sprite, st, self._categories(sprite.library))
                    elif not self._start_move(sprite, st, pending):
                        # 移动素材开播被拒（F6）：绝不能留在 turn 态——下一个
                        # 到点分支会把朝向再翻一次。直接回收待机
                        self._enter_idle(sprite, st, self._categories(sprite.library))
        elif st.state == STATE_ACTS:
            if st.elapsed >= st.duration:
                # 到点 ≠ 末帧已上屏（F8）：先过末帧收口闸门再切下一个动画
                if self._acts_tail_done(sprite, st, dt):
                    # 唱歌素材刚播完且音乐仍在放 → 原地续播，绝不走 gap/掷骰
                    # （旧机 `_on_anim_ended` 的第一条分支，window.py:2595-2601）
                    if not self._continue_sing(sprite, st):
                        if not self._start_animation_gap(sprite, st):
                            self._roll_next(sprite, st)
            else:
                self._maybe_predict(sprite, st)
        elif st.state == STATE_CLICK:
            if st.elapsed >= st.duration and self._tail_done(sprite, st, dt, "click"):
                self._enter_idle(sprite, st, self._categories(sprite.library))

    # ---------------------------------------------------------------- ACTS 末帧收口（F8）
    @staticmethod
    def _clear_acts_tail(st: _SpriteState) -> None:
        """作废 ACTS 末帧等待窗口（换绑 clip / 菜单播一遍时调）。

        代次守卫：末帧证据（acts_final_seen / acts_clip_finished）只对「产生
        它的那次绑定」有效。换绑后旧 clip 的观测绝不能让新一圈 ACTS 提前
        收口——否则就是本刀要修的那个 bug 换个方向复发。
        """
        st.acts_tail_grace = None
        st.acts_final_seen = False
        st.acts_clip_finished = False

    @staticmethod
    def _bound_clip(sprite):
        """sprite 当前绑定的 clip（``PetSprite._clip``）；替身无该字段返回 None。

        读 sprite 手上的那个而不是 ``library.movie(anim)``：只有它才是正在
        交付帧的对象（库换角色/回收后同名映射可能给出另一个 clip）。
        """
        return getattr(sprite, "_clip", None)

    @staticmethod
    def _clip_frame_count(clip) -> int:
        """clip 的源帧总数（0 = 接口缺失/读取失败 → 回退旧墙钟语义）。"""
        fn = getattr(clip, "frameCount", None)
        if not callable(fn):
            return 0
        try:
            return int(fn() or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clip_frame_index(clip) -> int:
        """clip 已交付的源帧号（-1 = 接口缺失/读取失败 → 视作无证据）。"""
        fn = getattr(clip, "currentFrameNumber", None)
        if not callable(fn):
            return -1
        try:
            return int(fn())
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _clip_has_finished_signal(clip) -> bool:
        """clip 是否暴露可连接的 ``finished`` 信号（挂接方同款判据）。

        ``PetSprite.bind_clip`` 对缺该信号的 clip 静默跳过连接
        （``getattr(clip, "finished", None)``），本判据用同一取法与连接能力检查，
        不引入新契约。有它 ⇒ 该播放器一定会报圈末，行为层就该等它的登记，而不是
        靠帧号猜（见 ``_idle_tail_done``）。
        """
        return callable(getattr(getattr(clip, "finished", None), "connect", None))

    def _acts_tail_grace(self, st: _SpriteState, frames: int,
                         deficit: int) -> float:
        """末帧等待预算（秒）：常数项与实测缺口项取大，硬上限封顶（F8/残余）。

        ``deficit`` = 到点那一刻 clip 还缺的帧数（``frames-1-index``）。剩余
        等待就是「还要等几个帧间隔」——首帧延迟是负载相关量（同一素材实测
        87-384ms），常数预算会被它吃穿：r7-new24 首帧迟到 384ms、r8-new48
        318ms，固定 350ms 在到点 350ms 后就换绑，丢了首圈末帧。故预算的第二
        项 = ``deficit × 名义帧间隔 × _ACTS_TAIL_GRACE_SAFETY``，随实测缺口
        一起长（缺口 10 帧 @24fps ≈ 0.52s、20 帧 @48fps ≈ 0.52s，再大由上限
        截到 0.5s），覆盖实测 0.41-0.42s 的剩余等待。

        第一项保留旧口径（``max(下限, 4× 名义帧间隔)``，慢素材的既有余量）：
        两项取大 ⇒ 本次改动**只会让预算变长、绝不会变短**，快素材/常规轮次的
        （缺 2-4 帧）取值与改动前逐位相同。

        上限 ``_ACTS_TAIL_GRACE_MAX_S`` 是硬边界：缺口再大也只等这么久，超出
        的迟到量按有界失败处理（到点即收口、丢尾巴几帧，绝不挂起）。
        """
        interval = st.duration / frames if frames > 0 and st.duration > 0 else 0.0
        floor = max(_ACTS_TAIL_GRACE_MIN_S, interval * _ACTS_TAIL_GRACE_FRAMES)
        estimated = deficit * interval * _ACTS_TAIL_GRACE_SAFETY
        return min(_ACTS_TAIL_GRACE_MAX_S, max(floor, estimated))

    def _acts_tail_done(self, sprite, st: _SpriteState, dt: float) -> bool:
        """ACTS 到点收口闸门（F8）：返回 True = 可以切下一个动画。

        墙钟到点（elapsed ≥ duration）不等于末帧已交付：播放器定时器取整
        （24fps→42ms、48fps→21ms，均 ≥ 名义帧长）叠加首帧延迟，一圈素材实测
        比 duration 晚 80-164ms 才交付末帧——旧代码在到点那一 tick 直接换绑，
        末帧（实测 237-240）永远没机会上屏。

        判据（命中任一即 True）：
        1) 末帧已交付且是**上一个 tick 之前**观测到的——即已经过至少一个 tick
           的绘制机会（帧回调只登记，切换一律留在这里做：在 clip 的 emit 栈里
           换绑会重入解码）；
        2) clip finished——两种播放器都在末帧之后一个帧间隔才发结束标记，
           等价于末帧已有一个帧周期的显示机会。该证据**不要求窗口已开**：
           finished 可能早于墙钟到点到达（dt 上限/卡顿使 elapsed 落后真实
           时间），记录后由开窗那一 tick 起的下一个 tick 收口，不消耗宽限；
        3) 宽限到期（窗口按实测缺口算，见 _acts_tail_grace）——末帧/结束标记
           始终不来时按旧墙钟语义收口，绝不挂起。

        **有界失败（风险登记）**：宽限封顶 0.5s，故「到点那一刻的剩余等待 >
        0.5s」这一档（≈ 首帧延迟 > 420ms @24fps、或整圈被拖到半秒以上）仍然
        会在末帧之前收口、丢尾巴几帧。取舍是刻意的：宁可丢尾巴，也不让 ACTS
        把动画链挂在等帧上（挂起会连带 gap/掷骰链一起停摆，用户看到的是动作
        卡死）。这一档的可见代价 = 少于 0.5s 的尾巴缺帧，与改动前的行为一致。

        帧数接口缺失、或本圈从未交付过任何帧（currentFrameNumber ≤ 0，异常
        clip）→ 直接 True：保持既有墙钟语义（既有测试假库的 clip 帧号恒为 0，
        走的就是这条）。异常 clip 提前发 finished 且一帧未交付时同样走这条，
        到点即收口，不因缺末帧而挂起。
        """
        clip = self._bound_clip(sprite)
        frames = self._clip_frame_count(clip)
        if frames <= 0:
            return True
        index = self._clip_frame_index(clip)
        if st.acts_tail_grace is None:
            # 首次到点：只开窗口 + 登记本次观测，本 tick 绝不切换。
            # acts_clip_finished 保留（可能已由提前到达的 finished 置位），
            # 它随换绑清零，不随开窗清零
            if index <= 0:
                return True
            st.acts_tail_grace = self._acts_tail_grace(
                st, frames, max(0, frames - 1 - index))
            st.acts_final_seen = index >= frames - 1
            return False
        st.acts_tail_grace = max(0.0, st.acts_tail_grace - dt)
        if st.acts_clip_finished:
            return True
        if index >= frames - 1:
            if st.acts_final_seen:
                return True          # 上一个 tick 就见过末帧：至少一次绘制机会
            st.acts_final_seen = True
        return st.acts_tail_grace <= 0.0

    # ---------------------------------------------------------------- IDLE 圈末收口
    @staticmethod
    def _clear_idle_tail(st: _SpriteState) -> None:
        """作废 IDLE 圈末等待窗口（每次换绑 clip 时调，与 ACTS 同款代次守卫）。

        圈末证据（idle_final_seen / idle_clip_finished）只对「产生它的那次绑定」
        有效：点击/拖拽打断、掷骰换绑、菜单播一遍之后，旧 clip 的观测绝不能让
        新的绑定提前换绑——否则就是本刀要修的问题换个方向复发（旧 clip 的
        finished 一落进新绑定，新一圈待机立刻被换掉）。
        """
        st.idle_tail_grace = None
        st.idle_final_seen = False
        st.idle_clip_finished = False

    def _idle_tail_done(self, sprite, st: _SpriteState, dt: float) -> bool:
        """IDLE 圈末收口闸门：返回 True = 本圈已播完，可以换绑（掷骰 / 续 gap 步）。

        墙钟到点（elapsed ≥ duration）不等于本圈播完：待机是无限循环的长驻状态，
        到点时本圈通常还差 2-4 帧（实机显示停在 frame 237/241，此时圈末结束标记
        已入队、reader 已驻留圈边界）。旧代码在到点那一 tick 就换绑，``bind_clip``
        里旧 clip 的 ``stop()`` 便落在「非圈末」——webm_clip 的软停判据
        （``_natural_end_pending`` + ``_reader_parked``）不真，走 ``_hard_stop``
        杀 ffmpeg、``start()`` 换代 spawn 新进程：实机每圈一次进程 churn（3 次
        重绑 3 个新 PID，re-arm 从未被调用），且尾巴几帧连交付/绘制机会都没有
        （present 未证，见诊断 REPORT §5）。把换绑推迟到真圈末，同 clip 的重绑就
        落在软停 + re-arm 上（webm_clip 一字未改）。

        判据（结构对齐 ``_acts_tail_done``，同一套证据纪律）：
        1) **有 ``finished`` 契约的 clip 只认 finished 登记**（或宽限到期）：末帧
           已交付 ≠ reader 已驻留圈边界——``_reader_parked`` 由 reader 线程写、
           GUI 侧读，两者之间没有同步点，按帧号抢先换绑可能恰落在驻留置位之前，
           于是又走硬停换代（本刀要修的就是它）。等一个帧间隔的 finished 登记，
           驻留必然就位（reader 在结束标记入队时即置位，标记早于末帧被消费）。
        2) 无 ``finished`` 接口的有限 clip（旧替身/降级播放器）→ 退到帧号证据：
           末帧已交付且是**上一个 tick 之前**观测到的（至少一次绘制机会）。
        3) 宽限到期（``_IDLE_TAIL_GRACE_S``）——上述证据始终不来时按旧墙钟语义
           换绑，绝不把掷骰链挂在等帧上。

        **有界失败（风险登记）**：拿不到圈末证据时最多多等 0.5s 就照旧掷骰；若此
        刻连圈末都没到（帧停滞），stop() 仍会硬停换代、尾巴仍会丢——与改动前的
        代价相同（只是晚 ≤0.5s），宁可如此也不让 IDLE 把动画链挂在等帧上。

        帧接口缺失、或本圈从未交付过任何帧（currentFrameNumber ≤ 0，旧替身/异常
        clip）→ 直接 True：保持既有墙钟语义（既有测试假库的 clip 帧号恒为 0，走的
        就是这条；否则一切旧替身都会被拖进等帧）。
        """
        clip = self._bound_clip(sprite)
        frames = self._clip_frame_count(clip)
        if frames <= 0:
            return True
        index = self._clip_frame_index(clip)
        if st.idle_tail_grace is None:
            # 首次到点：只开窗口 + 登记本次观测，本 tick 绝不换绑。
            # idle_clip_finished 保留（可能已由提前到达的 finished 置位，
            # 它随换绑清零，不随开窗清零）
            if index <= 0:
                return True
            st.idle_tail_grace = _IDLE_TAIL_GRACE_S
            st.idle_final_seen = index >= frames - 1
            return False
        st.idle_tail_grace = max(0.0, st.idle_tail_grace - dt)
        if st.idle_clip_finished:
            return True
        if self._clip_has_finished_signal(clip):
            # 有 finished 契约 → 只等它（或宽限到期）。末帧交付不等于 reader 已
            # 驻留圈边界（跨线程无同步点），抢先换绑可能仍落硬停换代
            return st.idle_tail_grace <= 0.0
        if index >= frames - 1:
            if st.idle_final_seen:
                return True          # 无 finished 接口：末帧 + 至少一次绘制机会
            st.idle_final_seen = True
        return st.idle_tail_grace <= 0.0

    # ---------------------------------------------------------------- MOVE 圈末收口
    @staticmethod
    def _clear_move_tail(st: _SpriteState) -> None:
        """作废 MOVE 圈末等待窗口（每次换绑 clip 时调，与 ACTS/IDLE 同款代次守卫）。

        圈末证据（move_final_seen / move_clip_finished）与续圈记账
        （move_loops_done）只对「产生它的那次绑定」有效：点击/拖拽打断、掷骰换绑、
        菜单播一遍之后，旧 clip 的观测绝不能让新的绑定提前收口，也不能让新一圈
        移动少走/多走一圈——否则就是本刀要修的问题换个方向复发。
        """
        st.move_tail_grace = None
        st.move_final_seen = False
        st.move_clip_finished = False
        st.move_loops_done = 0

    def _move_tail_done(self, sprite, st: _SpriteState, dt: float) -> bool:
        """MOVE 到点后的圈末闸门：返回 True = 可以进 gap/待机。

        **位置不归它**：调用方在到点那一 tick 已 snap 原定终点并归零速度，本闸门
        只决定状态何时切走。旧实现在到点那一 tick 直接 `_enter_idle`，而 24fps 的
        播放器定时器取整成 42ms（≥ 名义帧长 41.67ms）⇒ 一圈实际比 duration 晚
        96ms 才交付末帧、144ms 才交付结束标记，末帧（实机 238/241）随换绑一起被
        换掉——与 IDLE 圈末同源的缺口，只是代价从「每圈一次硬停换代」变成「尾巴
        1-3 帧连交付机会都没有」。

        判据（结构对齐 `_idle_tail_done`，同一套证据纪律）：
        1) 有 ``finished`` 契约的 clip 只认 finished 登记（或宽限到期）：末帧已
           交付 ≠ reader 已驻留圈边界（``_reader_parked`` 由 reader 线程写、GUI 侧
           读，两者之间没有同步点），按帧号抢先换绑可能仍落硬停换代；且换绑目标
           通常是 idle 素材，旧 clip 的 ``stop()`` 落在非圈末就会杀进程换代。
        2) 无 ``finished`` 接口的有限 clip（旧替身/降级播放器）→ 退到帧号证据：
           末帧已交付且是**上一个 tick 之前**观测到的（至少一次帧交付/绘制机会）。
        3) 宽限到期（``_IDLE_TAIL_GRACE_S``，与 IDLE 同档）——证据始终不来时按旧
           墙钟语义切走，绝不把决策链挂在等帧上。

        **有界失败（风险登记）**：拿不到圈末证据时最多多等 0.5s；若此刻帧停滞
        （预取断链），尾巴照样丢——代价与改动前相同（只是晚 ≤0.5s），宁可如此也
        不让 MOVE 把决策链挂在等帧上。位移不受影响：到点已 snap、等待期速度恒 0，
        「多等」只影响画面切换时机，不会多走一像素。

        帧接口缺失、或本圈从未交付过任何帧（``currentFrameNumber`` ≤ 0，既有测试
        假库的 clip 帧号恒为 0）→ 直接 True：保持既有墙钟语义（零额外等待）。
        """
        clip = self._bound_clip(sprite)
        frames = self._clip_frame_count(clip)
        if frames <= 0:
            return True
        index = self._clip_frame_index(clip)
        if st.move_tail_grace is None:
            # 首次到点：只开窗口 + 登记本次观测，本 tick 绝不切换。
            # move_clip_finished 保留（可能已由提前到达的末圈 finished 置位，
            # 它随换绑清零，不随开窗清零）
            if index <= 0:
                return True
            st.move_tail_grace = _IDLE_TAIL_GRACE_S
            st.move_final_seen = index >= frames - 1
            return False
        st.move_tail_grace = max(0.0, st.move_tail_grace - dt)
        if st.move_clip_finished:
            return True
        if self._clip_has_finished_signal(clip):
            # 有 finished 契约 → 只等它（或宽限到期），同 IDLE 的理由
            return st.move_tail_grace <= 0.0
        if index >= frames - 1:
            if st.move_final_seen:
                return True          # 无 finished 接口：末帧 + 至少一次绘制机会
            st.move_final_seen = True
        return st.move_tail_grace <= 0.0

    # ---------------------------------------------------------------- CLICK/TURN 圈末收口
    @staticmethod
    def _clear_tail(st: _SpriteState, kind: str) -> None:
        """作废指定状态的圈末等待窗口（每次换绑 clip 时调，代次守卫）。

        与 ``_clear_acts_tail`` / ``_clear_idle_tail`` / ``_clear_move_tail``
        同款：圈末证据只对「产生它的那次绑定」有效，换绑后旧 clip 的观测绝不
        能让新的绑定提前收口。CLICK/TURN 是有限一次性动画，共用本闸门。
        """
        setattr(st, f"{kind}_tail_grace", None)
        setattr(st, f"{kind}_final_seen", False)
        setattr(st, f"{kind}_clip_finished", False)

    def _tail_done(self, sprite, st: _SpriteState, dt: float, kind: str) -> bool:
        """CLICK / TURN 到点后的圈末闸门：返回 True = 可以收口切走。

        与 ``_idle_tail_done`` / ``_move_tail_done`` 同一套证据纪律（模块头已
        说明为什么不能到点即切）：墙钟到点不等于本圈播完。播放器定时器取整
        （24fps→42ms ≥ 名义帧长）叠加首帧延迟，一圈实测比 duration 晚
        80-164ms / 2-4 帧才交付末帧，旧实现到点即切 ⇒

        1. 点击反应、转向动画的收尾 2-4 帧被截（"没播完就弹回待机 / 直接翻面"，
           旧架构这两条链都在真末帧收口：window.py:2652-2667 与
           ``_on_anim_ended`` 的 turns 分支）；
        2. ``bind_clip`` 里旧 clip 的 ``stop()`` 落在「非圈末」→ webm_clip 的软停
           判据不真，走硬停杀 ffmpeg、``start()`` 换代 spawn 新进程（与 IDLE
           已修的每圈一次进程 churn 同源）。

        判据（结构对齐 ``_idle_tail_done``）：有 ``finished`` 契约的 clip 只认
        finished 登记（或宽限到期）——末帧已交付 ≠ reader 已驻留圈边界（跨线程
        无同步点）；无该接口的有限 clip 退到「末帧 + 一次绘制机会」；宽限
        ``_IDLE_TAIL_GRACE_S`` 兜底（有界失败：证据始终不来时按旧墙钟语义切走，
        绝不把动画链挂在等帧上）。帧接口缺失或本圈一帧未交付（旧替身）→ 直接
        True，保持既有墙钟语义。

        ``kind`` 取 "click" / "turn"：两种状态共用本实现，窗口字段各存一组。
        """
        clip = self._bound_clip(sprite)
        frames = self._clip_frame_count(clip)
        if frames <= 0:
            return True
        index = self._clip_frame_index(clip)
        grace = getattr(st, f"{kind}_tail_grace")
        if grace is None:
            # 首次到点：只开窗口 + 登记本次观测，本 tick 绝不切换。
            # *_clip_finished 保留（可能已由提前到达的 finished 置位，它随换绑
            # 清零，不随开窗清零）
            if index <= 0:
                return True
            setattr(st, f"{kind}_tail_grace", _IDLE_TAIL_GRACE_S)
            setattr(st, f"{kind}_final_seen", index >= frames - 1)
            return False
        grace = max(0.0, grace - dt)
        setattr(st, f"{kind}_tail_grace", grace)
        if getattr(st, f"{kind}_clip_finished"):
            return True
        if self._clip_has_finished_signal(clip):
            # 有 finished 契约 → 只等它（或宽限到期），同 IDLE 的理由
            return grace <= 0.0
        if index >= frames - 1:
            if getattr(st, f"{kind}_final_seen"):
                return True          # 无 finished 接口：末帧 + 至少一次绘制机会
            setattr(st, f"{kind}_final_seen", True)
        return grace <= 0.0

    # ---------------------------------------------------------------- 落地首帧（起飞预热 + 飞行期 pin）
    def _warm_landing_idles(self, sprite, st: _SpriteState, cats: dict) -> None:
        """起飞边沿：后台预热全部 idle 首帧并打飞行期 pin（window.py:4356-4404）。

        落地的切换目标是 idle 池（``_enter_idle``），而预测式预热覆盖不到这里
        ——弹射是事件触发（拖拽打断早已作废预测代次），且交互让路闸门在飞行期
        会挡住 ``warm_predicted``；故直接调 clip 级 ``warm_first_frame``（幂等、
        可被取消，见 webm_clip 文档），绕过的是**交互让路**闸门——库级总开关 /
        隐藏暂停闸门照旧要过（见 ``_warm_allowed``），否则设置页"关闭后停止
        后台动画预热"的承诺会被这条路径打破。

        **后台执行**：预热内部要拉起 ffmpeg（~50ms/条），旧机曾在 GUI 线程同步
        预热，碰撞风暴下每次撞飞同步拉起一次（~100ms/只），多鱼互撞时连续
        200ms+ 级卡顿。这里复用预测预热的共享 worker（模块级 ``_WARM_EXECUTOR``，
        不新开常驻线程），GUI 只付一次 submit。

        pin 打的是 ``_ffr_landing_pinned``（独立标志，绝不与 library 常驻的
        ``_ffr_pinned`` 混用：idle 池可与高频交互常驻集重叠，混用会让落地摘
        pin 顺手摘掉常驻保护）。清单落进 ``st.landing_pinned``，摘除按清单精确
        匹配（见 ``_unpin_landing_idles``）。

        **同一次飞行只提交一次**（B2）：``_WARM_EXECUTOR`` 是 max_workers=1 的
        单 worker（模块级共享，与预测式预热共用），重复 submit 只会让后一批
        ffmpeg 排在前一批后面——预热内容一字不变，白白把落地首帧的可用时间
        推后几百毫秒。开关是 ``st.warm_landing_submitted``：起飞置位，落地 /
        被拖拽打断（``_unpin_landing_idles``）复位，故下一次飞行照旧预热。
        """
        if not self._warm_allowed(getattr(sprite, "library", None)):
            # 库级闸门关闭（设置页关预热 / 隐藏挂起）：本段整体不占位——pin 与
            # 去重标记都不置，开闸后的下一次起飞照旧整批提交（不会被一次空跑
            # 吃掉标记，与"无 idle 素材"分支同款纪律）。
            return
        if st.warm_landing_submitted:
            return  # 本次飞行已提交过：pin 仍在位（清单没被摘），无需重打
        clips = self._landing_idle_clips(sprite, cats)
        if not clips:
            return
        st.warm_landing_submitted = True
        st.landing_pinned = clips
        for clip in clips:          # GUI 线程打标记，warm 在后台完成
            clip._ffr_landing_pinned = True
        _WARM_EXECUTOR.submit(self._warm_clips, clips)

    @staticmethod
    def _warm_clips(clips) -> None:
        """后台预热条目（worker 线程）：clip 缺 ``warm_first_frame`` 即跳过。

        预热失败静默（落地切换退化为按需同步解码，语义同旧路径）。
        """
        for clip in clips:
            try:
                warm = getattr(clip, "warm_first_frame", None)
                if callable(warm):
                    warm()
            except Exception:
                pass

    @staticmethod
    def _landing_idle_clips(sprite, cats: dict) -> list:
        """本 sprite 的 idle 池 clip 清单（旧机 ``[lib.movie(n) for n in idles]``）。

        clip 解析留在 GUI 线程（``MovieLibrary.movie`` 不保证线程安全），后台
        只持有引用跑解码。取不到（库/名缺失）静默跳过。
        """
        movie = getattr(getattr(sprite, "library", None), "movie", None)
        if not callable(movie):
            return []
        clips = []
        for name in cats["idles"] or ():
            try:
                clips.append(movie(name))
            except Exception:
                pass
        return clips

    @staticmethod
    def _unpin_landing_idles(st: _SpriteState) -> None:
        """摘掉起飞时给的飞行期 pin（landing / 飞行被拖拽打断）。

        只摘 ``_ffr_landing_pinned``，library 常驻 ``_ffr_pinned`` 绝不动；
        非飞行期调用是 no-op（清单空）。按起飞时记下的 clip 清单摘（不重查
        idle 池：飞行中换角色后池已变）。

        同时复位起飞预热去重标记（B2）：本方法是飞行窗口（起飞 → 落地/被
        拖拽打断）唯一的收口点，两个字段同生共死——下一次飞行照旧预热。
        """
        for clip in st.landing_pinned:
            clip._ffr_landing_pinned = False
        st.landing_pinned = []
        st.warm_landing_submitted = False

    @staticmethod
    def _warm_allowed(lib) -> bool:
        """库级预热闸门（总开关 + 隐藏暂停）：鸭式库无 ``warm_allowed()`` 按放行。

        行为层有两条**直提** ``_WARM_EXECUTOR`` 的路径（预测预热、起飞落地预热），
        它们不经过库的 ``warm_predicted`` / ``_warm_objects``，也就绕过了那里
        的闸门；判据统一问库（``MovieLibrary.warm_allowed``）。库不暴露该接口
        （测试假库 / 轻量替身）、或判定本身抛错时按放行——预热失败从来不致命
        （最坏退化成播放时按需同步解码），绝不能因为一次判定失败改变既有行为。
        """
        allowed = getattr(lib, "warm_allowed", None)
        if not callable(allowed):
            return True
        try:
            return bool(allowed())
        except Exception:
            return True

    # ---------------------------------------------------------------- 唱歌续播（N1）
    def _continue_sing(self, sprite, st: _SpriteState) -> bool:
        """唱歌素材播完且音乐仍在放 → 原地续播（window.py:2595-2601 语义）。

        旧机 ``_on_anim_ended`` 的第一条分支：``name == SING_ANIM and
        _music_sing_enabled and _music_sing_active`` → ``_switch(SING_ANIM)``，
        绝不走掷骰链（"唱一句 → 切随机动作 → 再唱"就是把唱歌循环打碎了）。

        音乐态归壳（``_music_sing_active`` 由 ``window_alerts`` 轮询维护），
        行为层不查音频 COM，只问注入的 ``sing_continue_provider``（默认 None =
        现状：照旧走 gap/掷骰链）。判据抛异常时按「不续播」处理——续唱是附加
        能力，绝不反过来打断动画链。

        返回 True = 已续播，调用方绝不再走 gap/掷骰。
        """
        provider = self.sing_continue_provider
        if provider is None:
            return False
        try:
            if st.anim != _sing_anim_name() or not provider():
                return False
        except Exception:
            return False
        self._enter_acts(sprite, st, self._categories(sprite.library),
                         forced_name=st.anim)
        return True

    # ---------------------------------------------------------------- 预测式预热
    def _make_predictor(self, sprite) -> PredictivePrewarm:
        """每 sprite 一个 PredictivePrewarm（roll/warm 闭包绑定该 sprite 的库）。

        should_predict 恒 True：帧序列时代预热 ~2.5ms 一帧，webm 路径的
        warm_first_frame 本就是旧架构的预热入口；预测掷骰只掷一次、产物
        照存（盲审 P1-1：否则稳态分布漂离 30/10/40/20）。
        """
        def _roll(exclude):
            cats = self._categories(sprite.library)
            pools = {k: cats[k] for k in ("idles", "turns", "moves", "acts", "clicks")}
            from .predictive_prewarm import roll_next as _pp_roll_next
            return _pp_roll_next(pools, exclude, rng=self.rng)

        def _warm(name):
            if not self._warm_allowed(getattr(sprite, "library", None)):
                return  # 库级闸门关闭（设置页关预热 / 隐藏挂起）：连提交都不做
            try:
                clip = sprite.library.movie(name)
                warm = getattr(clip, "warm_first_frame", None)
                if callable(warm):
                    # 解码/拉起 ffmpeg 全部下 worker（GUI 只付一次 submit）。
                    # clip 的原子认领（N4）保证同一 clip 不重复解码。
                    _WARM_EXECUTOR.submit(warm)
            except Exception:
                pass  # 预热失败静默（播放时按需同步解码，语义同旧路径）

        return PredictivePrewarm(roll=_roll, warm=_warm,
                                 should_predict=lambda name: True)

    def _maybe_predict(self, sprite, st: _SpriteState) -> None:
        """墙钟适配的预测触发（语义 = PredictivePrewarm.on_frame 的
        wall_remaining ≤ lead）：行为控制器以 elapsed/duration 墙钟推进，
        等效换算进 on_frame 的帧口径（frames=1000/fps=1000/divisor=1 →
        n = 999 - remaining*1000），不复制其触发逻辑。"""
        if st.predictor is None or not self.predict_enabled or st.duration <= 0:
            return
        remaining = st.duration - st.elapsed
        if remaining > self._predict_lead_s or remaining < 0:
            return
        n = max(0, 999 - int(round(remaining * 1000)))
        st.predictor.on_frame(
            st.anim, n, 1000, 1000.0, 1, self._predict_lead_s,
            exclude=st.anim)

    def _bind_with_gen(self, sprite, st: _SpriteState, name: str) -> bool:
        """bind + 预测代次推进（begin_anim 每次切换自增；作废由 consume 的
        context/gen 校验完成，不手动清预测——GLM A4 单规则）。

        返回 ``sprite.bind_clip`` 是否被接受（F6）：起播被拒时不推进预测
        代次，调用方据此放弃依赖该动画的状态（移动计划等）。

        换绑同时作废 ACTS 末帧等待窗口（F8 代次守卫）、IDLE 圈末等待窗口、MOVE
        圈末等待窗口与 CLICK/TURN 圈末等待窗口：点击/拖拽/掷骰链任何一次换绑之后，
        旧 clip 的末帧/圈末观测都不得让新的绑定提前收口（否则旧 finished 一落进
        新绑定，新一圈待机立刻被换掉 / 新一圈移动少走一圈 / 点击反应被旧证据
        提前收口）。
        """
        ok = sprite.bind_clip(name)
        self._clear_acts_tail(st)
        self._clear_idle_tail(st)
        self._clear_move_tail(st)
        self._clear_tail(st, "click")
        self._clear_tail(st, "turn")
        if ok and st.predictor is not None:
            st.predictor.begin_anim(name)
        return bool(ok)

    # ---------------------------------------------------------------- 动画间隔（M5d）
    def _start_animation_gap(self, sprite, st: _SpriteState) -> bool:
        """动作/移动播完后的强制间隔（window.py:2570-2576 ``_start_animation_gap``）。

        返回 False = 未进 gap（开关关 / 无待机·转向池），调用方按原链继续
        （动作回掷骰、移动回待机）。进入 gap 时先播一段氛围步（池 = idles +
        turns 滤掉 moves，与旧机 ``_play_animation_gap_step`` 同口径）。
        """
        if self._animation_gap_seconds <= 0.0:
            return False
        cats = self._categories(sprite.library)
        if not self._gap_pool(cats):
            return False
        st.gap_remaining = self._animation_gap_seconds
        self._play_animation_gap_step(sprite, st)
        return True

    @staticmethod
    def _gap_pool(cats: dict) -> list:
        """gap 氛围步候选池（window.py:2582：待机 + 转向，滤出同名移动素材）。"""
        return [n for n in (cats["idles"] + cats["turns"]) if n not in cats["moves"]]

    def _play_animation_gap_step(self, sprite, st: _SpriteState) -> None:
        """播一段 gap 氛围步（待机或转向）；池空回退待机（防御）。

        掷中转向素材同样要过朝向闸门（旧机 gap 步走 ``_play_roll``，
        window.py:2699-2707 + :2743-2763）：无需纠正（中线滞回带内或已朝内）时
        降级待机，**朝向绝不由随机数翻转**——否则开启动画间隔后氛围步会背对
        屏内方向随机转身（转向素材占 gap 池的比例即该概率）。
        """
        cats = self._categories(sprite.library)
        name = self._pick(self._gap_pool(cats), exclude=st.anim)
        if name is None:
            self._enter_idle(sprite, st, cats)
            return
        if name in cats["turns"]:
            want = self._facing_want(sprite)
            if cats["idles"] and (want is None or want == sprite.facing):
                self._enter_idle(sprite, st, cats)   # 降级待机（旧 _play_roll 同款）
            else:
                self._enter_turn(sprite, st, cats, forced_name=name)
        else:
            self._enter_idle(sprite, st, cats, forced_name=name)

    def _facing_want(self, sprite) -> str | None:
        """中线滞回判出的「应朝方向」（None = 带内，无需纠正）。

        朝向只跟随屏幕位置与移动目标，绝不由随机数翻转（window.py:2735-2745）。
        掷骰链与 gap 氛围步共用这一处判据，两条链的口径不允许分叉。
        """
        off_x, _off_y, bw, _bh = self._body_geometry(sprite)
        cx, left, right = movement.body_reach(
            self.bounds.left(), self.bounds.right(), sprite.pos.x() + off_x, bw, self.margin)
        return movement.inward_facing(cx, left, right)

    def _roll_next(self, sprite, st: _SpriteState) -> None:
        """待机播完掷骰：30% 待机 / 10% 转向 / 40% 待机（acts 桶让位）/ 20% 移动。

        探头会话期间移动桶必然落空（_plan_move 闸门），沿既有回退链进动作池/
        待机——「只允许待机/转向」的位移语义由此保证；动作池 clip 只播原地动画，
        不改位置。
        """
        cats = self._categories(sprite.library)
        # 批10-A1：先消费预测（context/gen 校验单规则，不符即弃 → 现场掷骰）
        if st.predictor is not None and self.predict_enabled:
            predicted = st.predictor.consume(
                context_anim=st.anim, exclude=st.anim,
                gap_active=st.gap_remaining > 0.0, moves=set(cats["moves"]))
            if predicted is not None:
                self._play_predicted(sprite, st, cats, predicted)
                return
        roll = self.rng.random()
        if roll < catalog.P_TURN:  # P_IDLE 与 P_TURN 是累计阈值（<0.3 待机，<0.4 转向）
            action = STATE_IDLE if roll < catalog.P_IDLE else STATE_TURN
        elif roll < catalog.P_ACTS:
            action = STATE_ACTS  # 40% 随机动作池（acts 为空时 enter 内回退待机）
        else:
            action = STATE_MOVE
        if action == STATE_MOVE:
            if self.no_move or not self._plan_move(sprite, st, cats):
                # 不移动/移动失败回退动作池（window.py:2735 语义，acts 空回待机）
                self._enter_acts(sprite, st, cats)
            return
        # 朝向闸门（window.py:2735-2745）：需要纠正朝向时一律播转向；
        # 掷中转向但无需纠正 → 降级待机。朝向绝不由随机数凭空翻转。
        want = self._facing_want(sprite)
        if action == STATE_ACTS:
            self._enter_acts(sprite, st, cats)
        elif want is not None and want != sprite.facing and cats["turns"]:
            self._enter_turn(sprite, st, cats)
        else:
            self._enter_idle(sprite, st, cats)

    # ---------------------------------------------------------------- 状态进入
    @staticmethod
    def _clear_move_plan(st: _SpriteState) -> None:
        """清移动计划残留（含 curve 通道）。

        任何非移动态都必须调它：残留的 move_start/curve 会让「回到移动态
        之前」的路径读到上一段计划的曲线相位（算出错位置），F5 的「接管后
        绝不 snap」也依赖 move_target 已被清掉。
        """
        st.move_target = None
        st.move_start = None
        st.curve = None
        st.frames_per_loop = 0
        st.loops = 1
        st.loop_duration = 0.0

    def _enter_idle(self, sprite, st: _SpriteState, cats: dict,
                    forced_name: str | None = None) -> None:
        # 离开接管态（含飞行落地收口）→ 摘掉飞行期首帧 pin：pin 只覆盖
        # 「起飞 → 落地」窗口（window.py:4289 _stop_physics 同点）。放在最前，
        # 与落地切待机同一 tick 内完成（无产出点，不会被穿插的逐出利用）
        self._unpin_landing_idles(st)
        st.state = STATE_IDLE
        st.pending_move = None
        self._clear_move_plan(st)
        sprite.set_velocity(QPointF(0, 0))
        name = forced_name if forced_name is not None else self._pick(cats["idles"], exclude=st.anim)
        st.elapsed = 0.0
        st.anim = name
        st.duration = self._plan_duration(sprite, name) if name else 0.0
        if name is not None:
            self._bind_with_gen(sprite, st, name)

    def _play_predicted(self, sprite, st: _SpriteState, cats: dict, name: str) -> None:
        """执行预测产物（window.py _play_roll 语义）：move 名走移动计划
        （失败回退动作池）；其余按归属池进入对应状态。"""
        if name in cats["moves"]:
            if self.no_move or not self._plan_move(sprite, st, cats, anim_override=name):
                self._enter_acts(sprite, st, cats)
            return
        if name in cats["turns"] and cats["turns"]:
            self._enter_turn(sprite, st, cats, forced_name=name)
        elif name in cats["acts"]:
            self._enter_acts(sprite, st, cats, forced_name=name)
        else:
            self._enter_idle(sprite, st, cats, forced_name=name)

    def _enter_drag(self, sprite, st: _SpriteState) -> None:
        """进入拖拽接管态（F1）：绑 drag 悬空 clip（缺素材回退 idle 池）。

        velocity 不动（拖拽位置归鼠标；``on_press`` 已归零）。探针/掷骰链
        的一切排定计划在此作废。飞行被空中抓住（thrown → drag）时同时摘掉
        飞行期首帧 pin（window.py:4318 同点语义）。
        """
        cats = self._categories(sprite.library)
        name = cats["drag"][0] if cats["drag"] else self._pick(cats["idles"])
        self._unpin_landing_idles(st)
        st.state = STATE_DRAG
        st.pending_move = None
        self._cancel_gap(st)  # 拖拽接管打断 gap（window.py:4178 同款取消点）
        self._clear_move_plan(st)
        st.elapsed = 0.0
        st.anim = name
        st.duration = self._plan_duration(sprite, name) if name else 0.0
        if name is not None:
            self._bind_with_gen(sprite, st, name)

    def _enter_thrown(self, sprite, st: _SpriteState) -> None:
        """进入抛掷飞行接管态（F4）：绑 drag（缺素材回退 idle 池）并循环。

        旧实现 window.py:2487-2499：飞行途中当前动作播完固定切「悬空」动画
        并循环（首帧必热的 drag clip，视觉契合被击飞），落地停稳才切回待机
        ——收口由 tick 的接管态自愈完成，本方法不碰速度/位置。播放速率由
        sprite_physics 每 tick 按速度叠加（physics.flight_anim_speed，
        window.py:4328-4340）。

        起飞边沿同时预热 idle 首帧并打飞行期 pin（window.py:4314
        ``_warm_landing_idles``）：落地的切换目标就是 idle 池，冷首帧会在
        GUI 线程同步拉 ffmpeg（实测 ~100ms），pin 则挡住 8MB 首帧预算在
        预热浪涌里把刚暖好的落地首帧挤掉。
        """
        cats = self._categories(sprite.library)
        name = cats["drag"][0] if cats["drag"] else self._pick(cats["idles"])
        st.state = STATE_THROWN
        st.pending_move = None
        self._cancel_gap(st)  # 抛掷接管同款取消点（window.py:4178）
        self._clear_move_plan(st)
        st.elapsed = 0.0
        st.anim = name
        st.duration = self._plan_duration(sprite, name) if name else 0.0
        if name is not None:
            # 起播被拒也静默：飞行段的位置积分不能因动画失败而中断
            self._bind_with_gen(sprite, st, name)
        self._warm_landing_idles(sprite, st, cats)

    def _enter_acts(self, sprite, st: _SpriteState, cats: dict,
                    forced_name: str | None = None) -> None:
        """随机动作（40% acts 桶）：acts 池随机一段，播完回掷骰
        （window.py _pick_next 的 acts 分支语义）；池空回退待机。

        探头会话（F7）把动作桶整体降级待机：会话期间只允许待机/转向
        （window_optional_services.py:223-233 _effects_filter_switch 的 sprite
        版）。闸门收在这一处而不是只收在掷骰分支——预测产物、移动失败回退
        同样经此进入动作池，三处口径必须一致。
        """
        if getattr(sprite, "probe_active", False):
            self._enter_idle(sprite, st, cats)
            return
        name = forced_name if forced_name is not None else self._pick(cats["acts"], exclude=st.anim)
        if name is None:
            self._enter_idle(sprite, st, cats)
            return
        st.state = STATE_ACTS
        st.pending_move = None
        self._clear_move_plan(st)
        sprite.set_velocity(QPointF(0, 0))
        st.elapsed = 0.0
        st.anim = name
        st.duration = self._plan_duration(sprite, name)
        self._bind_with_gen(sprite, st, name)

    def _enter_turn(self, sprite, st: _SpriteState, cats: dict,
                    pending_move: dict | None = None,
                    forced_name: str | None = None) -> None:
        name = forced_name if forced_name is not None else self._pick(cats["turns"], exclude=st.anim)
        if name is None:  # 调用方已保证 turns 非空；防御性回退
            self._enter_idle(sprite, st, cats)
            return
        st.state = STATE_TURN
        st.anim = name
        st.elapsed = 0.0
        st.duration = self._plan_duration(sprite, name)
        st.pending_move = pending_move
        self._clear_move_plan(st)
        sprite.set_velocity(QPointF(0, 0))
        self._bind_with_gen(sprite, st, name)

    def _plan_move(self, sprite, st: _SpriteState, cats: dict,
                   anim_override: str | None = None) -> bool:
        """排定一次移动（window.py _try_move 语义）；返回 False = 未建立计划。
        anim_override：菜单「移动」类指定素材（trigger_move），None = 掷骰随机。

        探头会话（sprite.probe_active）一律拒绝：旧机在 PetWindow._try_move
        入口用 _effects_probe_active 整体拦住位移（防止挂着探头姿态被平移出
        屏幕边缘），sprite 世界把闸门收在这一处——掷骰、预测产物、菜单移动
        三条路径都经此，调用方按既有回退链进动作池/待机。

        计划带上「圈内逐帧位移曲线」的全部解算输入（curve / frames_per_loop
        / loops / loop_duration，F3）：旧架构 window.py:2753-2768 的计划键
        同款，位置由曲线（而非平均速度）决定，静帧段不位移。
        """
        if getattr(sprite, "probe_active", False):
            return False
        moves = cats["moves"]
        if not moves:
            return False
        off_x, off_y, bw, bh = self._body_geometry(sprite)
        cx, left, right = movement.body_reach(
            self.bounds.left(), self.bounds.right(), sprite.pos.x() + off_x, bw, self.margin)
        dir_sign = movement.choose_move_direction(cx, left, right, self.min_distance, self.rng)
        if dir_sign is None:
            return False  # 两侧空间都不足：不建立计划
        room = (cx - left) if dir_sign < 0 else (right - cx)
        far = min(self.max_distance, int(room))
        if far < self.min_distance:
            return False
        distance = self.rng.randint(self.min_distance, far)
        name = anim_override if anim_override is not None else self._pick(moves)
        if name is None:
            return False
        lib = sprite.library
        stride = ((getattr(lib, "move_strides", None) or {}).get(name, catalog.MOVE_STRIDE_DEFAULT_PX)) * float(getattr(sprite, "scale", 1.0))
        loop_duration = self._plan_duration(sprite, name)
        if loop_duration <= 0:
            return False
        # 步幅整圈量化：位移锁到步态整圈，velocity=位移/时长 ⇒ 平均速度
        # 恒等于动画步态速度（防脚滑）；越界时 quantize 内部递减圈数/夹到 room。
        loops, distance, duration = movement.quantize_move(distance, stride, room, loop_duration)
        target_cx = cx + dir_sign * distance
        target_x = target_cx - bw / 2 - off_x
        target_y = movement.wander_target_y(
            sprite.pos.y() + off_y, self.bounds.top(), self.bounds.bottom(),
            bh, self.margin, self.rng) - off_y
        curve = (getattr(lib, "move_curves", None) or {}).get(name)
        plan = {
            "anim": name,
            "target": QPointF(target_x, target_y),
            "duration": duration,
            "facing": "right" if dir_sign > 0 else "left",
            # 圈内逐帧位移曲线（动帧才动、静帧不动）；无曲线 → None → 线性
            "curve": curve,
            "frames_per_loop": self._frames_per_loop(lib, name, curve),
            "loops": loops,
            "loop_duration": loop_duration,
        }
        if plan["facing"] != sprite.facing and cats["turns"]:
            # 需要反向：先播 turn clip，翻朝向后由 turn 完成分支执行本计划
            self._enter_turn(sprite, st, cats, pending_move=plan)
            return True
        return self._start_move(sprite, st, plan)

    @staticmethod
    def _frames_per_loop(lib, name: str, curve) -> int:
        """每圈源帧数：优先库的权威帧数，缺该接口时回退曲线长度。

        曲线本就是「逐源帧」等长的（curve[i] = 源帧 i 的圈内进度），长度即
        每圈帧数；仅当库未暴露 frames() 时才用（测试假库/轻量替身）。
        """
        frames_fn = getattr(lib, "frames", None)
        if callable(frames_fn):
            try:
                count = int(frames_fn(name) or 0)
            except (TypeError, ValueError, KeyError):
                count = 0
            if count > 0:
                return count
        return len(curve) if curve else 0

    def _start_move(self, sprite, st: _SpriteState, plan: dict) -> bool:
        """启动移动计划；返回 False = 开播被拒、计划未建立（F6）。

        无 curve：velocity = 位移/时长 一次算好的匀速直线（旧语义不变）。
        有 curve（F3）：本 tick 先不动，下一 tick 起由 _apply_move_curve 按
        曲线**绝对目标位置**反算 velocity——起步若先按平均速度积分一帧，
        静帧段会被提前推开一帧位移，而曲线位置只增不减、永不回退。
        """
        # 开播确认前不提交任何计划字段：被拒时调用方按既有回退链进动作池/
        # 待机，绝不会出现「动画没播却在按它的 duration 位移」（window.py:
        # 2739-2744 的 _switch 失败语义）
        if not self._bind_with_gen(sprite, st, plan["anim"]):
            return False
        # 开播确认后才提交朝向（朝向只跟随真实发生的移动）。镜像在 paint
        # 重建首帧时才取用 facing，此刻写入不产生首帧镜像错误。
        sprite.facing = plan["facing"]
        st.state = STATE_MOVE
        st.anim = plan["anim"]
        st.elapsed = 0.0
        st.duration = plan["duration"]
        st.move_target = plan["target"]
        st.move_start = QPointF(sprite.pos)
        st.pending_move = None
        st.curve = plan.get("curve")
        st.frames_per_loop = plan.get("frames_per_loop", 0)
        st.loops = plan.get("loops", 1)
        st.loop_duration = plan.get("loop_duration", 0.0)
        if plan["duration"] <= 0:
            sprite.set_pos(plan["target"])
            sprite.set_velocity(QPointF(0, 0))
        elif st.curve:
            sprite.set_velocity(QPointF(0, 0))  # 下一 tick 起由曲线接管
        else:
            vx = (plan["target"].x() - sprite.pos.x()) / plan["duration"]
            vy = (plan["target"].y() - sprite.pos.y()) / plan["duration"]
            sprite.set_velocity(QPointF(vx, vy))
        return True

    def _apply_move_curve(self, sprite, st: _SpriteState, dt: float) -> None:
        """曲线驱动每 tick 目标位置 → 反算 velocity（F3）。

        desired 由曲线进度（锚在计划起点的**绝对**位置）算得，故
        ``v = (desired - pos) / dt`` 积分后恰好落在 desired：既无残差累积，
        也不会因某 tick 的 dt 抖动而失步。静帧段 desired == 当前 pos →
        velocity 归零 → 位置一丝不动（旧 window.py:1773-1778 的帧驱动位移）。

        相位源取舍见 movement.curve_progress_at_time：旧架构用解码帧号，
        新架构用墙钟，漂移上界亚像素。无 curve / 无计划起点 / 无 dt 时
        no-op（线性路径的 velocity 在 _start_move 已设好）。
        """
        if dt <= 0 or st.curve is None or st.move_target is None or st.move_start is None:
            return
        progress = movement.curve_progress_at_time(
            st.curve, st.frames_per_loop, st.loops, st.elapsed, st.loop_duration)
        sx, sy = st.move_start.x(), st.move_start.y()
        dx = st.move_target.x() - sx
        dy = st.move_target.y() - sy
        sprite.set_velocity(QPointF(
            (sx + dx * progress - sprite.pos.x()) / dt,
            (sy + dy * progress - sprite.pos.y()) / dt,
        ))

    # ---------------------------------------------------------------- 边界
    def _clamp_into_bounds(self, sprite) -> None:
        """拖拽/抛掷切回 normal 后落点在界外的兜底。

        口径与 set_pos 一致（V-2）：身体框完整落界内、画布透明边允许
        越界——不再用整 canvas 矩形钳制（那会把按身体框贴边的 sprite
        每 tick 拉回一个透明边距，造成可见瞬移）。sprite 已挂 bounds 时
        set_pos 即唯一权威，这里的计算结果落在其允许域内，不会打架。
        """
        r = sprite.rect()
        body = sprite.body_rect() if hasattr(sprite, "body_rect") else r
        off_x, off_y = body.x() - r.x(), body.y() - r.y()
        lo_x = float(self.bounds.x()) - off_x
        lo_y = float(self.bounds.y()) - off_y
        max_x = lo_x + self.bounds.width() - body.width()
        max_y = lo_y + self.bounds.height() - body.height()
        x = min(max(sprite.pos.x(), lo_x), float(max(lo_x, max_x)))
        y = min(max(sprite.pos.y(), lo_y), float(max(lo_y, max_y)))
        if x != sprite.pos.x() or y != sprite.pos.y():
            sprite.set_pos(QPointF(x, y))

    # ---------------------------------------------------------------- 素材查询
    def _categories(self, lib) -> dict:
        """角色的 idle/turn/move/click/acts/drag 池（按库缓存）。

        真实 MovieLibrary 走 catalog.build_categories（与 window.py:406 同一
        入口，folder_map/folder_files/manifest 全透传）；测试假库直接暴露
        idles/turns/moves/clicks 池属性，drag 为可选单值属性。
        drag 在 catalog 侧是**单值 name 或 None**（一个角色只有一段悬空
        素材），这里统一规范化成列表，调用方按池处理（F1）。
        """
        # 缓存与库的「可播放集合」版本号绑定：`_prune_to_playable` 收敛时 stamp+1
        # （首跑帧序列供给落地、角色包目录变化后的重扫都会触发），新段进池、
        # 播不动的段出池；只按库对象缓存会让这些变化等到换库才生效（终审：装配层
        # 缓存未失效）。
        stamp = getattr(lib, "playable_stamp", 0)
        cached = self._cats_cache.get(lib)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        names = getattr(lib, "names", None)
        if callable(names):
            raw = catalog.build_categories(
                names(), getattr(lib, "manifest", None),
                getattr(lib, "folder_map", None), getattr(lib, "folder_files", None))
            cats = {k: list(raw[k]) for k in ("idles", "turns", "moves", "clicks", "acts")}
            cats["drag"] = [raw["drag"]] if raw.get("drag") else []
        else:
            cats = {k: list(getattr(lib, k, None) or []) for k in ("idles", "turns", "moves", "clicks", "acts")}
            drag = getattr(lib, "drag", None)
            cats["drag"] = [drag] if drag else []
        self._cats_cache[lib] = (stamp, cats)
        return cats

    def _body_geometry(self, sprite) -> tuple[float, float, float, float]:
        """身体框相对 sprite pos 的 (offset_x, offset_y, width, height)。

        优先角色 manifest 的 body_box（乘 scale）；未声明（或假库无
        character_id）回退"窗口即身体"（catalog.character_body_box 文档
        语义）。
        """
        rect = sprite.rect()
        lib = getattr(sprite, "library", None)
        cid = getattr(lib, "character_id", None)
        if cid:
            # 几何绑定**在播库**的素材根（P1：与 pet_sprite 同口径）
            box = catalog.character_body_box(cid, getattr(lib, "geometry_root", None))
            if box is not None:
                s = float(getattr(sprite, "scale", 1.0))
                x1, y1, x2, y2 = box
                return x1 * s, y1 * s, (x2 - x1) * s, (y2 - y1) * s
        return 0.0, 0.0, float(rect.width()), float(rect.height())

    @staticmethod
    def _clip_duration(lib, name: str) -> float:
        dur = getattr(lib, "duration", None)
        return float(dur(name)) if callable(dur) else 0.0

    @staticmethod
    def _plan_duration(sprite, name: str) -> float:
        """按 **sprite 的播放速率** 读素材时长（一切计划/状态时长的唯一读取口）。

        ``clip.duration()`` 除以的是 **clip 当前** 的 ``playback_speed``，而那个
        字段只在 ``PetSprite.bind_clip`` 里才被同步成 ``sprite.playback_speed``
        ——计划读取点全部发生在 bind **之前**，缓存 clip 里残留的是上一次绑定
        的速率（飞行倍率也会留在那里，见 ``reset_playback_speed`` 的注释）。
        直接读库口径会让状态机按错误时长推进：慢动作被提前切走、快动作播完
        长停末帧，移动计划的位移与墙钟失配（脚滑）。这里换算回"按 sprite 速率
        播放"的真实时长：

            clip.duration() × clip.playback_speed ÷ sprite.playback_speed

        速率取不到（鸭式库 / 无该字段的 clip / 非正数）或库自身读不出时长时
        按现状返回库口径——绝不因为一次换算失败改变既有行为。
        """
        lib = getattr(sprite, "library", None)
        duration = BehaviorController._clip_duration(lib, name)
        if duration <= 0.0:
            return duration
        try:
            sprite_speed = float(getattr(sprite, "playback_speed", 1.0))
        except (TypeError, ValueError):
            return duration
        movie = getattr(lib, "movie", None)
        if not callable(movie):
            return duration
        try:
            clip_speed = float(getattr(movie(name), "playback_speed", None))
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
            return duration
        if sprite_speed <= 0.0 or clip_speed <= 0.0:
            return duration
        return duration * clip_speed / sprite_speed

    def _pick(self, pool, exclude: str | None = None):
        entries = [n for n in pool if n != exclude] or list(pool)
        if not entries:
            return None
        return self.rng.choice(entries)
