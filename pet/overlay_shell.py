# -*- coding: utf-8 -*-
"""Phase 4.1a 产品壳：overlay 拓扑（合成窗）的 AppShell 挂载层。

设计稿：.scratch/single-overlay-window/PHASE4_DESIGN.md（T1/T4/T5 + §5 4.1a 行
+ v1.1 §10 屏热插拔/会话事件升级项）。本模块只做骨架与生命周期：

- **全进程一张**合成窗覆盖整个虚拟桌面：窗口矩形 = 各屏**可用工作区**的并集
  包围矩形（不是 ``virtualGeometry``——要排除每屏任务栏，否则按窗口矩形自判全屏
  的第三方软件会把桌宠当成全屏应用，见 issue #302）；精灵坐标是窗口局部坐标
  （= 全局 - 窗口原点），跨屏就是同一张窗内的自由移动，没有"换宿主/换窗口"；
- **活动区（精灵可站立域）= 各屏可用工作区平移到窗口局部坐标后的矩形集合**
  （``ScreenHost.activity`` + ``PetSprite.set_clamp_rects``），不是单个包围矩形：
  错位拼接的屏间空洞没有显示器，单矩形会让宠走进看不见的地方；
- **每宿主一份**三控制器（BehaviorController/SpriteCollisionWorld/
  ThrowPhysicsController），由**统一 tick 驱动器**（tick_driver.TickDriver，M-2/T2）
  在同一个 QTimer 里推进；``self.overlay`` / ``_bounds`` / 三控制器就是这张窗的面；
- 进程级共享面仍只有一份：壳、驱动器时钟、音效池、联动/主动搭话、托盘、监视器、
  配置文件；
- 屏事件：screenAdded/screenRemoved/primaryScreenChanged/geometryChanged →
  窗口几何与活动区重算、sprite 位置按"它那块屏可用区内的比例"迁移；逐只 DPR 按
  它身体中心所在的屏喂（一张窗只有一个 DPR 是 Qt 物理限制）；
- **屏归属**（档案的 ``screen_name``）是每只宠"在哪块屏上"的记录：有意换屏时写，
  位置自存（``_save_child_geometry``）只写比例与朝向——于是关掉跨屏、或原来那块
  屏临时不在线，都不会改写用户摆好的多屏归属，重新开启并重启即回到各自那块屏；
- 跨屏是**可关闭的全局偏好**（主配置 ``allow_cross_screen``，默认关、进程快照）：
  关着时窗仍覆盖全桌面，但活动区只留家那块屏，跨屏路由一律拒绝（拖不过去）；
- 会话结束（Windows 关机/注销）：复用 session_watcher 的闸门与信号链，
  收口本路径 MovieLibrary 的全部 clip（issue #111 等价链，D10 落点）。

T5 切换策略：PET_RENDER_TOPOLOGY=overlay 环境变量是开发期一次性分流 flag，
读取收口在本模块 is_overlay_topology()；T4 Phase A 起判定再叠加主配置的
``stream_capture_mode``（勾选「直播捕获兼容」= 重启级切到 legacy 单窗，OBS 才
枚举得到窗口；见 ``overlay_settings_command``）。
4.1a 明确不做：多 sprite（4.2）、位置持久化（4.2a，本刀恒走默认右下角）、
岛/气泡/聊天/菜单全量 parity（4.1b/4.1c）、捕获模式切换（T4 后续）。

4.2b（本刀后段）：多 sprite 生命周期按 D5/D6 落地——spawn 分配 slot 身份并写
活跃宠清单（``overlay-active-pets.json``，运行时状态文件）；退出即出清单
（slot 配置保留）；重启按v2清单恢复（含各自角色与几何），slot0可空且永不提升。
身份/档案存储在pet_profiles，overlay_spawn_state仅兼容包装。

4.2c 后段（本刀）：托盘聚合（单托盘 + 逐只子菜单，对齐 app.py:3267-3301 的
单托盘多窗语义）、D12「退出子肥鱼」指令通道消费（独立设置进程写指令文件 →
本壳经 config 目录 watcher + 轮询消费，纯逻辑在 ``overlay_settings_command``）、
D13 逐 sprite 设置路由（菜单按被点 sprite 的 config 身份传 ``--instance``）。

4.3 后半（本刀）：把本壳做成进程级共享子系统（agent_link / proactive /
全屏 watcher）的**呈现扇出目标**——overlay 拓扑下 ``instances[].win`` 恒为
None，扇出集合为空等于联动/自说自话静默缺失（D0 的另一半，见
``multi_window_shared.presentation_targets``）。本类因此补齐 PetWindow 的
呈现等价面：

- 气泡：``show_bubble`` / ``hold_bubble`` / ``hide_bubble`` 落主 sprite 头顶
  （``SpriteBubbleFollower.show``）；提醒队列 ``show_alert`` / ``resolve_alert``
  / ``clear_alerts`` 直接复用 ``window_alerts`` 的 host 形函数（同一份队列
  语义，不重复造）；飞行期（``_flying_sprites``）气泡整体禁掉——统一门禁
  ``_bubble_blocked(sprite)``，飞的那只禁泡、其余宠照常（见
  ``_on_sprite_flight_changed``）；
- 聚合状态：``isVisible`` / ``_dragging`` / ``_physics_mode`` /
  ``_click_effect_phase`` / ``mouse_through`` / ``_bubble_busy_until`` /
  ``_bubble_suppressed``（proactive G1 守卫与联动节流门读的就是这些；
  ``_bubble_suppressed`` **不含**飞行态——它同时是联动节流位）；
- 联动动作：``request_link_anim`` / ``request_link_idle`` / ``switch_clip`` /
  ``cats`` / ``idles`` 映射到 ``BehaviorController``；
- 显隐：``set_pet_visible`` 同步 pause/resume proactive 与 agent_link
  （``window.py:1214-1250`` 语义；共享实例的 pause/resume 是 no-op，见
  multi_window_shared 的说明——G1 逐 tick 读可见性）；
- 快速对话：气泡可点 → ``open_quick_chat``，``QuickChatBubble`` 锚定被点
  sprite（``_SpriteChatAnchor`` 提供 pet 形的 ``visible_content_rect`` /
  ``on_open_chat``），回车发送走既有 ChatService/SessionStore 链路；
  无聊天打包变体（``pet.chat`` 被排除）按 app.py 既有 ImportError 守卫
  静默降级为不可点。
"""

from __future__ import annotations

import copy
import logging
import math
import queue
import sys
import threading
import time
import weakref
from collections import deque
from pathlib import Path

_IS_MACOS = sys.platform == "darwin"

import shiboken6
from PySide6.QtCore import (
    QEvent, QObject, QPoint, QPointF, QRect, QRectF, QTimer, Qt, Signal)
from PySide6.QtGui import QBitmap, QGuiApplication, QIcon, QImage, QPixmap, QRegion
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from . import autostart as autostart_mod
from . import catalog
from . import click_sound
from . import feature_activation
from . import overlay_settings_command
from . import overlay_spawn_state
from . import slot_manager
from . import window_alerts
from .context_menus.shared import (
    add_tray_action,
    animation_icon_cache_drop_library,
    release_menu_tree,
)
from .physics import throw_speed_cap
from .config import (
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
)
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset
from .overlay_peripherals import FullscreenCursorWatcher
from .overlay_window import OverlayWindow
from .pet_sprite import INTERACTION_DRAG, INTERACTION_THROWN, PetSprite
from .screen_host import ScreenHost
from .session_watcher import install_session_watcher
from .speech_bubble import (
    SELF_TALK_IMAGE_BOX_H,
    SELF_TALK_IMAGE_BOX_W,
    list_self_talk_images,
)
from .sprite_behavior import (
    STATE_ACTS,
    STATE_CLICK,
    STATE_IDLE,
    STATE_MOVE,
)
from .sprite_bubble import SpriteBubbleFollower, sprite_anchor_rect_global
from .sprite_sound import SpriteSoundPlayer
from .tick_driver import TickDriver

ENV_TOPOLOGY = overlay_settings_command.ENV_TOPOLOGY
TOPOLOGY_OVERLAY = overlay_settings_command.TOPOLOGY_OVERLAY

# 联动动作链里「一次性动作正在播」（window.py _is_one_shot_playing 的 sprite 等价物）：
# 动作池 / 点击回应 / 移动三类都不可被打断，联动请求排进待播槽。
_LINK_ONESHOT_STATES = (STATE_ACTS, STATE_CLICK, STATE_MOVE)

# 托盘首帧取图轮询：sprite 侧没有 legacy 的 ``frame_ready`` 信号，只能用短轮询。
# 预算内 500ms（首帧通常几百毫秒内到），超预算降频到 5s **继续等**——首帧晚于
# 预算（首跑帧序列转码/慢启动/内存压力）时必须还能自愈，绝不永久停在占位图标。
_TRAY_ICON_POLL_MS = 500
_TRAY_ICON_POLL_BUDGET = 20
_TRAY_ICON_SLOW_POLL_MS = 5000

# 自言自语配图解码缓存的安全余量：气泡出图按显示盒等比适配，呼吸气泡的图像
# 安全区（``speech_bubble._breath_size_for_content``）在窄配图大小下会比标准盒
# 略宽（≤ 气泡宽 ×0.7，气泡宽 ≤ 显示盒长边 + 一点），1.1 覆盖这点溢出；同时也
# 吸收 Qt 把逻辑矩形映射到设备像素时的取整。超出部分本来就会被 Smooth
# 缩放掉，不追求精确贴合。
_SELF_TALK_IMAGE_CACHE_SLACK = 1.1


def self_talk_image_cache_edge(image_scale: float, dpr: float = 1.0) -> int:
    """自言自语配图解码缓存的目标长边（像素）＝显示盒长边 × 配图大小 × DPR × 余量。

    旧实现把每张图固定缩到长边 ≤640：用户 ``self_talk_image_scale=146``、DPR=1
    时实际只画 ~321×204（``speech_bubble.SELF_TALK_IMAGE_BOX_*`` × scale），
    多出来的像素纯占内存（实机 24 张 36.8MB）。改成按现算的显示盒定目标后，
    同一批素材降到 ~1/3；2× 屏（DPR=2）则按物理像素放大到 2 倍——够锐利、
    又不为没画的尺寸付内存。

    两个入参都钳位（配图大小 0.5..3.0，与 ``speech_bubble.show_image`` 同口径；
    DPR ≥ 1，读不到 DPR 时不按"更省内存"裁掉物理像素）。
    """
    scale = max(0.5, min(3.0, float(image_scale)))
    ratio = max(1.0, float(dpr))
    box = max(SELF_TALK_IMAGE_BOX_W, SELF_TALK_IMAGE_BOX_H) * scale * ratio
    # 取整前扣掉浮点噪声：220×1.1 在二进制下是 242.00000000000003，直接 ceil
    # 会凭空多要一整像素（内存上无所谓，但边界语义要稳、断言要能复现）。
    return int(math.ceil(box * _SELF_TALK_IMAGE_CACHE_SLACK - 1e-6))


def _resolve_self_talk_image_dir(raw: str) -> str:
    """解析自言自语配图目录（语义逐行对齐 ``window.py:123``）。

    为什么不直接 import 那个私有函数：overlay 拓扑不构造 PetWindow，壳不该
    为了一个 8 行路径换算去依赖 ``pet.window`` 的私有面；这里按源实现的语义
    逐行镜像，行为差异一眼可查。用户显式配置的外部目录被删除后不再回退内置
    彩蛋池（用户删目录的意图就是"不要再看图"），相对路径（内置 assets）保留
    回退以兼容便携包目录迁移。
    """
    raw = str(raw or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() and not candidate.is_dir():
        return ""
    return str(resolve_fun_asset(raw, oijingjing_image_path().parent))


def _import_quick_chat():
    """取 ``QuickChatBubble``；无聊天打包变体返回 None（app.py 既有守卫惯例）。

    打包变体以 ``excludes=['pet.chat']`` 排除聊天模块（见 ``pet/__main__.py
    _chat_available``），此时 ``pet.quick_chat`` 的模块级 ``from .chat.service
    import ChatService`` 抛 ImportError。只吞 ``pet.chat`` 系的 ImportError
    （返回 None = 无聊天变体）；其它导入错误照旧抛出，由壳层统一降级并落
    日志，不在这里静默吞掉（不掩盖真实故障）。
    """
    try:
        from .quick_chat import QuickChatBubble
    except ImportError as exc:
        if str(getattr(exc, "name", "") or "").startswith("pet.chat"):
            return None
        raise
    return QuickChatBubble


class _SpriteChatAnchor:
    """快速对话气泡的 pet 形锚点（``QuickChatBubble.position_near_pet`` 只读两面）。

    ``quick_chat.py`` 只用 ``visible_content_rect()`` 定位（242-285）与
    ``on_open_chat`` 打开完整聊天窗（445-450），不需要 PetWindow 的其余面；
    每个 sprite 一个锚点，被点中的是哪一只就锚在哪一只头顶。
    """

    __slots__ = ("_shell", "_sprite")

    def __init__(self, shell, sprite) -> None:
        self._shell = shell
        self._sprite = sprite

    def presentation_valid(self) -> bool:
        return self._sprite is not None and self._sprite in self._shell._all_sprites()

    def visible_content_rect(self) -> QRect:
        """身体框全局矩形（气泡锚点，口径同 SpriteBubbleFollower.anchor）。"""
        if not self.presentation_valid():
            return QRect()
        overlay = getattr(self._shell, "overlay", None)
        if overlay is None:
            return QRect()
        # 原点取**这只宠自己的宿主窗**：用主窗原点时，启动就恢复到副屏的那只
        # 一打开快速聊天，气泡会定位到主屏对应坐标（不用先拖拽就能撞上）。
        return sprite_anchor_rect_global(
            self._sprite, self._shell._sprite_origin(self._sprite))

    def on_open_chat(self) -> None:
        """气泡内「完整聊天窗」入口（QuickChatBubble._open_full_chat 调用面）。"""
        self._shell.open_full_chat()


class _GoldenSpinSpriteHost:
    """``GoldenSpinController`` 的 sprite 宿主：``update()`` = 把当前角度写进 sprite。

    旧路径由 ``PetWindow.paintEvent`` 读 ``current_angle_deg()`` 应用旋转
    （window_effects.begin/end_rotation）；sprite 世界的绘制入口是
    ``PetSprite.set_throw_rotation``，故控制器每次 ``win.update()`` 都等价地
    写一次角度（0 = 回正）。目标 sprite 由 ``shell._golden_spin_target`` 指定
    ——多宠时点击哪只转哪只（旧架构一窗一宠，天然只有一只）。

    **控制器全壳共享、目标独占**（R06）：角度只写给 ``_golden_spin_target``
    指向的那一只，换目标由 ``shell._ensure_golden_spin`` 先把旧目标回正并脱离
    （见 ``_settle_golden_spin_target``）——``_golden_spin_target`` 为 None 时
    本方法整拍空转，这正是结清过程中"不再往旧目标写角"的闸门。
    """

    __slots__ = ("_shell",)

    def __init__(self, shell) -> None:
        self._shell = shell

    def update(self) -> None:
        shell = self._shell
        spin = getattr(shell, "_golden_spin", None)
        sprite = getattr(shell, "_golden_spin_target", None)
        if spin is None or sprite is None:
            return
        apply = getattr(sprite, "set_throw_rotation", None)
        if callable(apply):
            apply(float(spin.current_angle_deg()))


class _MusicSingChain:
    """音乐唱歌续播（``window.py:2473-2481 _on_anim_ended`` 的 sprite 等价物）。

    sprite 行为控制器没有动画结束回调面（``sprite_behavior`` 的收口在 tick），
    改由驱动器 extras 每 tick 观测：唱歌 clip 自然播完回待机链时，只要音乐
    还在放就立即无缝重播（旧机 ``_music_sing_enabled and _music_sing_active``
    分支，不再每次查音频 COM）。用户点击/拖拽/掷骰接管唱歌时不再续播
    （旧机点击动画结束没有唱歌分支，同款语义）。
    """

    def __init__(self, shell) -> None:
        self._shell = shell
        self._was_singing = False

    def tick(self, sprites, dt: float) -> None:
        shell = self._shell
        sprite = getattr(shell, "sprite", None)
        behavior = getattr(shell, "behavior", None)
        if sprite is None or behavior is None:
            return
        if not bool(getattr(shell, "_music_sing_active", False)):
            self._was_singing = False
            return
        state = behavior.state_of(sprite)
        if state == STATE_ACTS and behavior.anim_of(sprite) == _sing_anim():
            self._was_singing = True
            return
        if self._was_singing and state == STATE_IDLE:
            # 唱歌 clip 播完 → 音乐仍在放则续播（同一 clip，首帧必热）
            self._was_singing = False
            shell.switch_clip(_sing_anim())
            return
        self._was_singing = False


def _sing_anim() -> str:
    """唱歌动画名（与旧架构同源常量，杜绝第二套字符串）。

    惰性 import：``window_alerts.check_music_sing`` 本身就有
    ``from .window import SING_ANIM``（共享实现的既定依赖），这里沿用同一
    依赖面；只在功能真的启用（``_music_sing_active``）时才会被调用，未开启
    该设置的普通 overlay 进程不多付一次 pet.window 导入。
    """
    from .window import SING_ANIM
    return SING_ANIM


class _LinkAnimChain:
    """联动动作链接续（``window.py _on_anim_ended`` → ``_link_next_provider`` 等价物）。

    PetWindow 靠动画结束回调接续联动动作；sprite 世界的行为控制器没有结束
    回调面（``sprite_behavior`` 不在本刀范围），改由驱动器 extras 每 tick
    观测「一次性动作结束」的下降边沿，等价地接续待播动作/下一个联动动作。
    零新线程、每 tick 一次 ``state_of`` 查询（字典取值）。
    """

    def __init__(self, shell) -> None:
        self._shell = shell
        self._was_busy = False

    def tick(self, sprites, dt: float) -> None:
        shell = self._shell
        busy = shell._link_anim_busy()
        was_busy, self._was_busy = self._was_busy, busy
        if busy or not was_busy:
            return
        # 一次性状态（动作/点击/移动）刚播完：黄金回旋 armed 模式在此接续
        # （旧机 _on_anim_ended → _effects_on_click_anim_finished，window.py:2533-2537）
        shell._on_click_anim_finished()
        # 待播优先，否则向 provider 要下一个
        # （顺序同 legacy _on_anim_ended：先消费待播，再问联动链）
        if shell._pending_link_anim:
            shell._play_pending_link_anim()
            return
        provider = shell._link_next_provider
        if not callable(provider):
            return
        try:
            nxt = provider()
        except Exception:
            logging.debug("overlay: 联动动作链取下一个动作失败", exc_info=True)
            return
        if nxt:
            shell.request_link_anim(str(nxt))


# ---------------------------------------------------------------- B7b：per-slot 取源
#
# 旧架构：每只桌宠是**独立进程**，各读自己的 ``config-slot-N.json``
# （``Config(instance_id="slot-N")`` + ``window.py`` 逐键读 ``self.cfg``），
# 所有 sprite 级设置天然各存各的。单进程壳只有一份 ``self._config``，本组常量
# 与视图类把那条身份→配置的对应关系复原：
#
# - 主 sprite → 本进程主配置；
# - 子 sprite → 它自己的档案快照，缺文件/缺键取 Config 同源默认；
# - 窗口级键（``on_top`` / ``mouse_through`` / ``pet_opacity`` / …）不在此列：
#   一次只有一个合成窗，这些键物理上无法 per-pet（审计 C4），继续按主配置
#   **全局**生效。
#
# 键集本体在 ``overlay_spawn_state.PER_SLOT_SETTING_KEYS``（零 Qt 轻模块，取舍与
# 全量注释见该处）：独立设置进程也要按同一份键集分流，而它禁止导入本模块。
PER_SLOT_SETTING_KEYS = overlay_spawn_state.PER_SLOT_SETTING_KEYS


def _clamped_float(raw, default: float, low: float, high: float) -> float:
    """读一个带上下限的浮点值；缺值/坏值回退 ``default``（再夹取）。

    slot 文件可能被手工改坏（旧 Config 会清洗，这里只读不清洗），故每个 per-slot
    数值键都要走这一层——异常/NaN 不能掀掉配置同步链。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default)
    if value != value:  # NaN
        value = float(default)
    return max(low, min(high, value))


class _SpriteConfig:
    """子宠档案的清洗快照；generation 与原值在同一把目录锁内捕获。"""

    def __init__(self, shell, sprite, *, snapshot=None, data=None) -> None:
        from . import pet_profiles
        from .config import normalize_pet_profile_snapshot

        self._shell = shell
        self._sprite = sprite
        slot = 0 if sprite is getattr(shell, "sprite", None) else shell._spawned_slots.get(sprite)
        config_dir = shell._spawn_config_dir()
        if snapshot is None and slot is not None and config_dir:
            snapshot = pet_profiles.read_slot_snapshot(config_dir, slot)
        self.generation = snapshot.generation if snapshot is not None else None
        self.status = snapshot.status if snapshot is not None else "memory"
        if data is None:
            if snapshot is not None and snapshot.profile is not None:
                data = dict(snapshot.profile.data)
            elif not config_dir:
                data = dict(getattr(shell, "_slot_memory", {}).get(slot, {}))
            else:
                data = {}
        self._slot_data = dict(data)
        raw = {"version": 4, **data} if snapshot is None and not config_dir else data
        self._values = normalize_pet_profile_snapshot(raw)

    def get(self, key, default=None):
        if key in self._values:
            return self._values[key]
        return self._shell._config.get(key, default)

    def __getattr__(self, name):
        return getattr(self._shell._config, name)


class _SpritePetHost:
    """被点 sprite 的 pet 形宿主（余额/识屏等 host 形消费方的 per-sprite 落点）。

    ``_show_balance_payload`` / ``window_alerts`` 这类消费方按 PetWindow 面取值
    （``show_bubble`` / ``cfg`` / ``isVisible``），旧架构每窗各有一个这样的宿主，
    所以"点小肥鱼 → 气泡出现在小肥鱼头顶"。这里给子 sprite 一只最小宿主，主
    sprite 仍直接用壳本身（行为与历史逐点一致）。

    刻意不暴露 ``request_link_anim``：联动动作链的落点是主 sprite 的行为控制器，
    子宠没有等价面（``_show_balance_payload`` 对该属性本就有 hasattr 守卫）。
    """

    def __init__(self, shell, sprite) -> None:
        self._shell = shell
        self._sprite = sprite

    @property
    def cfg(self):
        return self._shell._sprite_config(self._sprite)

    @property
    def scale(self) -> float:
        return float(getattr(self._sprite, "scale", 1.0) or 1.0)

    def isVisible(self) -> bool:  # noqa: N802 (Qt/PetWindow 命名)
        return self._shell._sprite_speech_visible(self._sprite)

    def _sprite_in_flight(self, sprite=None) -> bool:
        """这一只是否正在飞行（宿主面：默认就是本宿主绑定的 sprite）。"""
        return self._shell._sprite_in_flight(
            self._sprite if sprite is None else sprite)

    def _bubble_blocked(self, sprite=None) -> bool:
        """本 sprite 的气泡门禁（设置页抑制期或这一只正在飞行）。"""
        return self._shell._bubble_blocked(
            self._sprite if sprite is None else sprite)

    def visible_content_rect(self) -> QRect:
        overlay = getattr(self._shell, "overlay", None)
        if overlay is None:
            return QRect()
        # 原点取**这只宠自己的宿主窗**：用主窗原点时，启动就恢复到副屏的那只
        # 一打开快速聊天，气泡会定位到主屏对应坐标（不用先拖拽就能撞上）。
        return sprite_anchor_rect_global(
            self._sprite, self._shell._sprite_origin(self._sprite))

    def show_bubble(self, text: str, duration_ms: int = 3200, **_kwargs) -> bool:
        if self._bubble_blocked():
            return False  # 飞行期不弹（飞的那只禁泡，见 OverlayShell._bubble_blocked）
        return self._shell._show_bubble_text_for(
            self._sprite, str(text), int(duration_ms), **_kwargs)

    def hold_bubble(self, seconds: float) -> None:
        self._shell.hold_bubble(seconds)

    def hide_bubble(self) -> None:
        self._shell._hide_bubble_for_sprite(self._sprite)


class _SpriteServicePresenter(_SpritePetHost):
    """手动服务结果（余额 / 天气）的**代次呈现器**（2026-10-10 终审 A10）。

    菜单旁路此前把整个壳交给 ``AppShell.show_balance`` / ``AppShell.show_weather``，
    落点由壳的 ``alert_target_sprite()``（= 最小可见槽）在结果到达时**动态决定**：
    右击子肥鱼查天气，气泡冒在主肥鱼头上；请求在飞时把发起的那一只关掉，结果还会
    落到别宠身上。点击路径（``_on_sprite_click`` → ``_sprite_host(target)``）早就按
    发起 sprite 收口了，同一个意图在菜单旁路没有收口。

    与 ``_SpritePetHost`` 的两处差别（其余宿主面逐字沿用）：

    - **主 sprite 也带身份**：``show_bubble`` 走壳自己的完整门（提醒让路 / sticky /
      飞行期），不是"当前最小的可见槽"；子 sprite 走 ``_show_bubble_text_for``。
    - ``presentation_available()``：这只宠**还在不在**。判据是对象身份（同一槽重建
      出来的是另一只宠 ⇒ 旧结果不许投给新住客，比"槽号 + 代次"更强），再叠整窗可见
      与这只自己的可见性。关闭/隐藏后迟到的结果**只取消这一次呈现**：不清全局数据
      缓存、不改别宠状态、不另找落点（规范 §一「关闭后的迟到结果」）。
    """

    def presentation_available(self) -> bool:
        return bool(self._shell._sprite_speech_visible(self._sprite))

    def show_bubble(self, text, duration_ms=3200, **kwargs) -> bool:
        if not self.presentation_available():
            return False
        if self._bubble_blocked(self._sprite):
            # 与 ``_SpritePetHost.show_bubble`` 同一道 per-sprite 门禁（设置页抑制期 /
            # 这一只正在飞行）：override 只查 ``presentation_available`` 会漏掉它——
            # 飞行中或被抑制的子宠照样冒泡（2026-10-10 终审 F04）。主 sprite 那一侧
            # 壳的 ``show_bubble`` 自己也会查一次（``_bubble_blocked()``），两条路同门。
            return False
        if self._sprite is self._shell.sprite:
            return bool(self._shell.show_bubble(text, int(duration_ms), **kwargs))
        return bool(self._shell._show_bubble_text_for(
            self._sprite, str(text), int(duration_ms), **kwargs))

    def request_link_anim(self, name: str) -> None:
        """余额档位动画的落点（仅主 sprite 有等价链）。

        子 sprite 没有联动动作控制器，静默跳过——把动画播在主肥鱼身上等于"点了子肥鱼
        却让主肥鱼转圈"，比不播更不像话。消费方（``_show_balance_payload``）本就对该
        属性有 ``hasattr`` 守卫，不缺这条也能跑。
        """
        if self._sprite is not self._shell.sprite:
            return
        fn = getattr(self._shell, "request_link_anim", None)
        if callable(fn):
            fn(name)


class _SpriteSelfTalkHost:
    """一只**子** sprite 的自言自语宿主（``window_alerts`` host 形函数的适配面）。

    旧架构每只宠各自计时、各自冒泡（``window.py:458-480`` + ``:740``）；主 sprite
    在单进程壳里仍走壳自身的自言自语机（``OverlayShell._self_talk_*``，历史行为
    逐点不变），本类只服务子 sprite：各自一份配置快照、各自一个**单发**定时器
    （不新增常驻高频表——每次冒泡后重排下一次，与旧版同构）。
    """

    def __init__(self, shell, sprite) -> None:
        self._shell = shell
        self._sprite = sprite
        # 属性名必须叫 ``_self_talk_timer``：``window_alerts`` 的 host 形函数
        # 直接读写它（``schedule_self_talk`` 停表 + 起表）。
        self._self_talk_timer = QTimer(shell)
        self._self_talk_timer.setSingleShot(True)
        self._self_talk_timer.timeout.connect(self._on_timeout)
        self._last_self_talk_text: str | None = None
        self._self_talk_images_checked_at = 0.0
        self.reload()

    # ---------------------------------------------------------------- 配置面
    @property
    def cfg(self):
        return self._shell._sprite_config(self._sprite)

    @property
    def on_self_talk_speak(self):
        """朗读通道（与主宠共用同一条：旧架构都是 ``win.on_self_talk_speak``）。"""
        return getattr(self._shell, "on_self_talk_speak", None)

    # ``window_alerts`` 的 host 形函数直接读这些私有属性（同 PetWindow 口径）
    @property
    def _bubble_suppressed(self) -> bool:
        return bool(getattr(self._shell, "_bubble_suppressed", False))

    def _sprite_in_flight(self, sprite=None) -> bool:
        """这一只是否正在飞行（宿主面：默认就是本宿主绑定的 sprite）。"""
        return self._shell._sprite_in_flight(
            self._sprite if sprite is None else sprite)

    def _bubble_blocked(self, sprite=None) -> bool:
        """本 sprite 的气泡门禁（设置页抑制期或这一只正在飞行）。"""
        return self._shell._bubble_blocked(
            self._sprite if sprite is None else sprite)

    @property
    def _sticky_bubble_active(self) -> bool:
        return bool(getattr(self._shell, "_sticky_bubble_active", False))

    @property
    def _alert_current(self):
        return getattr(self._shell, "_alert_current", None)

    @property
    def _bubble_busy_until(self) -> float:
        return float(getattr(self._shell, "_bubble_busy_until", 0.0))

    def isVisible(self) -> bool:  # noqa: N802 (Qt/PetWindow 命名)
        return self._shell._sprite_speech_visible(self._sprite)

    def reload(self) -> None:
        """（重）读该 sprite 自己那份 self_talk 配置（配置同步边界调用）。"""
        cfg = self.cfg
        self._self_talk_enabled = bool(cfg.get("self_talk_enabled", False))
        self._self_talk_texts = window_alerts.read_self_talk_texts(
            cfg.get("self_talk_texts"))
        self._self_talk_duration_seconds = _clamped_float(
            cfg.get("self_talk_duration_seconds"),
            DEFAULT_SELF_TALK_DURATION_SECONDS, 1.0, 300.0)
        self._self_talk_image_dir = str(cfg.get("self_talk_image_dir", "") or "")
        self._self_talk_images = list_self_talk_images(
            _resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_image_scale = _clamped_float(
            cfg.get("self_talk_image_scale"), 100, 50.0, 300.0) / 100.0
        self._self_talk_min_interval = max(5.0, _clamped_float(
            cfg.get("self_talk_min_interval"),
            DEFAULT_SELF_TALK_MIN_INTERVAL, 0.0, 86400.0))
        self._self_talk_max_interval = max(self._self_talk_min_interval, _clamped_float(
            cfg.get("self_talk_max_interval"),
            DEFAULT_SELF_TALK_MAX_INTERVAL, 0.0, 86400.0))
        self._self_talk_images_checked_at = time.monotonic()
        # 配图 decode 走壳共享的路径→QImage 缓存 + 后台预热线程（各 sprite 的
        # 图片清单不同，缓存按绝对路径天然不串号）；**不动**壳的代次计数器，
        # 免得 spawn 一只子宠就把主宠在飞的解码作废。
        self._shell._warm_shared_self_talk_images(
            [str(path) for path in self._self_talk_images])

    # ---------------------------------------------------------------- 调度/冒泡
    def schedule(self, *, after_display: bool = False) -> None:
        window_alerts.schedule_self_talk(self, after_display=after_display)

    def _schedule_self_talk(self, *, after_display: bool = False) -> None:
        """host 形别名（``window_alerts.on_self_talk_timeout`` 读这个私有名）。"""
        self.schedule(after_display=after_display)

    def stop(self) -> None:
        self._self_talk_timer.stop()

    def close(self) -> None:
        self.stop()
        timer, self._self_talk_timer = self._self_talk_timer, None
        if timer is not None:
            try:
                timer.deleteLater()
            except RuntimeError:
                pass

    def _on_timeout(self) -> None:
        window_alerts.on_self_talk_timeout(self)

    def _show_self_talk_text(self, text: str) -> bool:
        if self._bubble_blocked():
            return False
        return self._shell._show_bubble_text_for(
            self._sprite, str(text), int(round(self._self_talk_duration_seconds * 1000)))

    def _show_random_self_talk(self) -> bool:
        """随机一条自言自语（文本或配图），落点 = 这只 sprite 自己的气泡。

        抽样/出图概率复用 ``window_alerts`` 的纯逻辑函数；配图只走壳的预热缓存
        （GUI 线程绝不付磁盘/解码税——冷缓存回退文本，与主宠同口径）。
        """
        if self._bubble_blocked():
            return False
        if self._sticky_bubble_active or self._alert_current is not None:
            return False
        now = time.monotonic()
        if now - self._self_talk_images_checked_at > 60.0:
            self._self_talk_images_checked_at = now
            live = [path for path in self._self_talk_images if path.is_file()]
            if len(live) != len(self._self_talk_images):
                self._self_talk_images = live
        picked = window_alerts.pick_self_talk_choice(
            self._self_talk_texts, self._self_talk_images,
            window_alerts.self_talk_image_chance(self))
        if picked is None:
            return False
        kind, value = picked
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        if kind == "image":
            self._last_self_talk_text = None
            follower = self._shell._bubble_for(self._sprite)
            if follower is None:
                return False
            cache = getattr(self._shell, "_self_talk_image_cache", {})
            image = cache.get(str(value))
            if image is None or image.isNull():
                self._shell._warm_shared_self_talk_images([str(value)])
                if not self._self_talk_texts:
                    return False
                self._last_self_talk_text = self._self_talk_texts[0]
                return self._show_self_talk_text(self._last_self_talk_text)
            return follower.show_image(
                value, duration_ms, image_scale=self._self_talk_image_scale,
                pixmap=QPixmap.fromImage(image))
        self._last_self_talk_text = value
        return self._show_self_talk_text(value)


def is_overlay_topology() -> bool:
    """唯一拓扑判定入口（输入：显式 PET_RENDER_TOPOLOGY env + 主配置捕获开关）。

    实现在零 Qt 的 ``overlay_settings_command``：设置进程（``--settings``，
    ``pet/__main__.py`` 明确禁止导入 pet.app/overlay_shell）也要按拓扑决定
    D12 指令通道走不走，故拓扑读取的实现必须落在两侧都能 import 的模块；
    本函数保留为对外唯一入口名（app.py 照旧转发）。T4 起判定还读主配置的
    ``stream_capture_mode``（勾选 = 重启级切到 legacy 单窗），细节见该模块。
    """
    return overlay_settings_command.is_overlay_topology()


class _SettingsIdentityConfig:
    """身份载体的 config 面（``open_settings_process`` 只读 ``instance_id``）。"""

    __slots__ = ("instance_id",)

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id


class _SettingsIdentity:
    """D13：喂给 ``AppShell.open_settings_process`` 的身份载体（鸭子类型）。

    该入口只读 ``instance.config.instance_id``：主宠的身份是 ``slot-0``、子 sprite
    是 ``slot-N``，两者都非空 → 设置进程显式带 ``--instance slot-N`` 打开**这一只
    自己的档案**（不再靠进程级 ``DSH_PET_INSTANCE`` 猜，主进程已把它固定成空串）。
    这里不建 Config、不碰磁盘。
    """

    __slots__ = ("config",)

    def __init__(self, instance_id: str) -> None:
        self.config = _SettingsIdentityConfig(instance_id)


def _screen_name(screen) -> str:
    try:
        return str(screen.name())
    except Exception:
        return "?"


def mixed_dpr_notice(ratios, *, multi_window: bool) -> str:
    """混合缩放的一句话说明（一次启动只打一次）。

    「一张合成窗只能有一个 DPR」是 #307 的真因，但处理方式有两种，日志必须说实话：
    走多窗时每块屏各一张、跨屏照常拖；只有没开「允许桌宠跨屏」时才退化成启动屏
    那一张。v4.3.3 的旧文案两种情况都喊「跨屏本次不可用」——多窗已经生效时那是
    假警报（实机：125% 副屏下进程确实开了两张窗，日志却说跨屏不可用）。
    """
    shown = sorted({round(float(ratio or 1.0), 3) for ratio in ratios})
    if multi_window:
        return ("多屏缩放不一致（DPR {}）：每块屏一张合成窗，"
                "跨屏照常直接拖过去".format(shown))
    return ("多屏缩放不一致（DPR {}）：没开「允许桌宠跨屏」，"
            "只开一张窗并退化为启动屏".format(shown))


def _sprite_current_pixmap(sprite):
    """sprite 已渲染帧 → 当前 clip 的 ``currentImage()`` → ``None``（帧未就绪）。

    取图链与 ``sprite_menu_facade.icon_pixmap`` 同口径：先读 sprite 上已渲染的
    ``_pixmap``，没有才回退当前 clip 的当前帧；两处都空 = 真的没有帧。
    """
    pm = getattr(sprite, "_pixmap", None)
    if pm is not None and not pm.isNull():
        return QPixmap(pm)
    clip = getattr(sprite, "_clip", None)
    current = getattr(clip, "currentImage", None)
    image = current() if callable(current) else None
    if image is None or image.isNull():
        return None
    return QPixmap.fromImage(image)


def _crop_icon_pixmap(pm: QPixmap, size: int) -> QPixmap:
    """裁掉帧的透明留白后等比缩放到 ``size``（旧 ``PetWindow._crop_icon_pixmap`` 同口径）。

    动画帧是整张视频画布，直接缩放会把角色缩成几个像素（``context_menus/icons``
    记录了同一个坑）；托盘与灵动岛头像共用这一份裁剪语义，不另造第二套。
    """
    image = pm.toImage()
    bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
    if bounds.isValid() and not bounds.isEmpty():
        pm = QPixmap.fromImage(image.copy(bounds))
    return pm.scaled(size, size,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


#: 屏拓扑重排一次调用最多跑几轮（事务期间到达的信号会触发重做；有界防抖动）
_TOPOLOGY_MAX_PASSES = 4


class ShellOverlayWindow(OverlayWindow):
    """4.1a 产品 overlay：挂进程级驱动器 + 单击/拖拽区分。

    仿真段不再经本类（M-2：驱动器独立持有控制器，tick 顺序协议在
    tick_driver.TickDriver.tick_sim 固化）；overlay 只把 advance+paint 段
    与鼠标路由做完。behavior 属性由壳层挂载，contextMenuEvent（基类）查表
    读它。点击 vs 拖拽的阈值判定口径同 run_overlay_demo.py：按下位移小于
    DRAG_THRESHOLD*scale 视为单击。
    """

    def __init__(self, screen, *, driver: TickDriver | None = None) -> None:
        super().__init__(screen=screen, driver=driver)
        self._press_pos = None
        self.behavior = None  # 宿主挂载（ScreenHost）；contextMenuEvent 查表读它
        self.edge_probe = None  # 宿主挂载；拖拽/点击事件接线
        self.host = None  # 所在宿主（ScreenHost 构造时回填）
        # 真拖拽升级（过 DRAG_THRESHOLD）才通知探头取消会话（旧机语义：
        # 按下只是点击候选，探头会话的点击拉直因此才有机会生效——按下即
        # 取消会让 on_sprite_clicked 永远遇到 mode==OFF）。
        self._drag_committed_cb = self._on_real_drag_started
        self.setAcceptDrops(True)  # 4.1c 投喂（命中 sprite 才 accept）

    def _behavior_of(self, sprite):
        """该 sprite 的行为控制器（单窗：本窗口那一份）。"""
        return self.behavior

    def _probe_of(self, sprite):
        """该 sprite 的边缘探头世界（单窗：本窗口那一个）。"""
        return self.edge_probe

    def _sprite_space_pos(self, local_pos):
        """本窗局部坐标 → 被拖 sprite **当前宿主窗**的局部坐标。

        多窗（混合缩放）下拖拽跨屏时，这只已经交给另一块屏的窗，而鼠标仍被本窗
        捕获——事件坐标必须换算到它的新坐标系，否则位置会跳回本窗局部。
        """
        resolver = getattr(self, "_host_for_sprite", None)
        grab = self._mouse_grab
        if not callable(resolver) or grab is None:
            return QPointF(local_pos)
        host = resolver(grab)
        if host is None or host.overlay is self:
            return QPointF(local_pos)
        delta = self.geometry().topLeft() - host.overlay.geometry().topLeft()
        return QPointF(local_pos) + QPointF(delta)

    def _on_real_drag_started(self, sprite) -> None:
        probe = self._probe_of(sprite)
        if probe is not None:
            probe.on_sprite_drag_started(sprite)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self.slingshot.aiming:
            # 弹弓瞄准中的左键松手 = 发射（基类已消费），不进单击/拖拽判别
            self._press_pos = None
            super().mouseReleaseEvent(event)
            return
        grab = self._mouse_grab
        committed = self._drag_committed  # super() 的 _finish_grab 会清旗标，先快照
        click_only = self._press_click_only  # 锁定位/SHIFT 门（同样先快照）
        press, self._press_pos = self._press_pos, None
        super().mouseReleaseEvent(event)
        behavior = self._behavior_of(grab)
        probe = self._probe_of(grab)
        if grab is None or press is None or behavior is None:
            return
        threshold = catalog.DRAG_THRESHOLD * getattr(grab, "scale", 1.0)
        # click_only：锁定位/未按 SHIFT 的按下（M5a/M5b）——位移再大也只算点击，
        # 与旧机两处闸门「取消拖拽但保留点击」语义一致
        if click_only or (not committed
                          and (event.position() - press).manhattanLength() < threshold):
            # 边缘探头消费点击（PEEKING 拉直/STRAIGHTENED 重置倒计时）时，
            # 抑制点击反应与点击音效——拉直本身就是反馈
            if probe is not None and probe.on_sprite_clicked(grab):
                return
            # M5e：点击触发黄金回旋（window.py:3356-3357 的同位置路由）。
            # 返回 True = 本次点击被效果层消费（直连模式），不再播点击反应/音效
            route_spin = getattr(self, "_click_route_spin", None)
            if callable(route_spin) and route_spin(grab):
                return
            clicked = behavior.on_sprite_clicked(grab)
            squash = getattr(grab, "squash", None)
            if callable(squash):
                squash()  # 4.1c 点击 Q 弹（window.py:3473 语义）
            cb = getattr(self, "click_feedback", None)
            if callable(cb):
                cb()  # 4.1c 点击音效（有无 click 素材都发声，同旧架构）
            # 点击气泡族（余额/自言自语）：回调由壳注入（与 click_feedback 同位置）。
            # click_name = 本次点击实际绑定的动画名——只有 on_sprite_clicked 真改绑
            # 了 click clip（返回 True）anim_of 才是点击动画名；无 click 素材时它
            # 读到的是按下前/拖拽名的残留，按契约传 ""（调用方回退全局随机台词）。
            click_cb = getattr(self, "_on_sprite_click", None)
            if callable(click_cb):
                anim_of = getattr(behavior, "anim_of", None)
                click_name = anim_of(grab) if (clicked and callable(anim_of)) else None
                click_cb(grab, str(click_name or ""))
        elif probe is not None:
            # 真拖拽释放（非单击）：通知探头按 tick 静止判定重新评估进入
            probe.on_sprite_drag_released(grab)
            # F1：真拖拽松手切回待机池（旧 window.py:3260-3264）。点击候选
            # 分支不走这里——它由 on_sprite_clicked 改绑 click clip；无 click
            # 素材时控制器的接管态自愈会在下一 tick 收回待机（不卡悬空动画）。
            behavior.on_drag_released(grab)
        else:
            # 无探头世界：真拖拽松手的待机收尾同样要走到（F1）
            behavior.on_drag_released(grab)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        feeding = getattr(self, "_feeding", None)
        if feeding is not None:
            feeding.handle_drag_enter(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        feeding = getattr(self, "_feeding", None)
        if feeding is not None:
            feeding.handle_drop(event)
        else:
            event.ignore()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """4.1c：有全量菜单建造器（产品壳）走 facade 菜单，否则基类最小集。

        D13：把**被点中的 sprite** 透传给建造器——菜单里的设置入口要按它自己的
        config 身份打开（否则右击子肥鱼的「桌宠设置」会静默打开主宠配置）。

        close_on_trigger 动作在菜单可见时会被 ``connect_action`` 挂起
        （``shared.defer_menu_callback`` → 根菜单 ``_deferred_callbacks``），
        window.py 在 ``menu.exec()`` 返回后统一派发；overlay 此前没人派发 →
        所有 close_on_trigger 条目（AI 对话/桌宠设置/隐藏桌宠/退出…）点下去
        静默无反应。这里补上同一条收口。
        """
        builder = getattr(self, "_full_menu_builder", None)
        if builder is None:
            super().contextMenuEvent(event)
            return
        target = self.sprite_at(event.pos())
        if target is None:
            event.ignore()
            return
        menu = builder(target)
        menu.exec(event.globalPos())
        self._dispatch_deferred_menu_callbacks(menu)
        release_menu_tree(menu)
        event.accept()

    def _dispatch_deferred_menu_callbacks(self, menu) -> None:
        """菜单关闭后派发挂起命令（window.py ``_show_context_menu`` 尾部等价物）。

        定时器 context 绑**本窗**（长寿命）而不是菜单：菜单在事件返回后即无
        Python 引用，绑定菜单会让 0ms 定时器随对象一起消失、命令永远不执行。
        """
        from .context_menus.shared import take_deferred_menu_callbacks

        callbacks = take_deferred_menu_callbacks(menu)
        if not callbacks:
            return

        def dispatch() -> None:
            for callback in callbacks:
                callback()

        QTimer.singleShot(0, self, dispatch)

    def reopen_context_menu(self, menu) -> None:
        """按原位置重开右键菜单（菜单树重建后立即生效；window.py 同名方法等价物）。

        QMenu 会把请求点挪到不出屏的位置，沿用用户实际看到的坐标（同
        window.py ``reopen_context_menu`` 的 ``_context_menu_anchor`` 口径）。
        """
        if menu is None:
            return
        global_pos = QPoint(menu.pos())
        menu.close()
        QTimer.singleShot(
            10, self, lambda: self._exec_full_menu_at(global_pos))

    def _exec_full_menu_at(self, global_pos: QPoint) -> None:
        builder = getattr(self, "_full_menu_builder", None)
        if builder is None:
            return
        target = self.sprite_at(self.mapFromGlobal(global_pos))
        if target is None:
            return
        menu = builder(target)
        menu.exec(global_pos)
        self._dispatch_deferred_menu_callbacks(menu)
        release_menu_tree(menu)


#: 活跃 OverlayShell 登记册（测试收口用，MovieLibrary._LIVE_MOVIE_LIBRARIES
#: 同范式）：壳持 QTimer/监视器/加载线程/素材库，测试把它们留在共享
#: QApplication 上漂到进程退出，就是全量套件/macOS CI 漂移段错误的累积源。
_LIVE_OVERLAY_SHELLS: "weakref.WeakSet" = weakref.WeakSet()

#: 图片解码串行锁（插件首用竞态防线）：Qt 的图片格式插件（dyld 装载 +
#: QFactoryLoader 缓存）在多线程并发首用下会原生崩溃（mac CI 实锤）；后台
#: 预热路径统一过它。GUI 线程的同步解码（首帧/跳帧）是既有路径不套它——
#: 否则预热线程持锁时 GUI 同步解码会被反锁（吞吐倒挂）。
_IMAGE_DECODE_LOCK = threading.Lock()

#: 自言自语配图加载队列（单 worker 串行；见 ``_start_self_talk_image_load``）。
#: 条目 = (cache, pending, edge, gen, gen_now)；gen_now 回读壳的换代戳。
_SELF_TALK_LOAD_Q: "queue.Queue" = queue.Queue()

#: 配图解码结果进程级共享（按绝对路径）：同一图片池只解码一次——overlay
#: 多宠/多壳同池时不再每壳重解一遍（3 宠 = 3 倍解码的浪费就此消除），也
#: 让测试期"每壳一批次"的队列积压从根上消失。目标长边变化（DPR/配图大小
#: 热改）时整表清一次重建（与原来每壳各自重建的口径一致，只是范围变全局）。
_SELF_TALK_IMAGE_CACHE_SHARED: dict = {}
_SELF_TALK_IMAGE_CACHE_EDGE: "int | None" = None
_self_talk_loader_started = False
_self_talk_loader_lock = threading.Lock()


def _self_talk_loader_loop() -> None:
    """配图加载 worker：逐批次逐图串行解码（进程内唯一配图解码线程）。"""
    while True:
        item = _SELF_TALK_LOAD_Q.get()
        if item is None:
            return  # 收口哨兵（进程退出）
        cache, pending, edge, gen, gen_now = item
        for path in pending:
            if gen != gen_now():
                break  # 已换代：本批剩余作废
            with _IMAGE_DECODE_LOCK:
                if gen != gen_now():
                    break  # 等锁期间被换代
                img = QImage(path)
                if img.isNull():
                    continue
                if img.width() > edge or img.height() > edge:
                    img = img.scaled(
                        edge, edge,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                cache[path] = img


def _start_self_talk_loader() -> None:
    """懒起配图加载 worker（幂等；进程级唯一，随进程退出消亡）。"""
    global _self_talk_loader_started
    with _self_talk_loader_lock:
        if _self_talk_loader_started:
            return
        _self_talk_loader_started = True
    threading.Thread(target=_self_talk_loader_loop, daemon=True,
                     name="self-talk-img-loader").start()


class OverlayShell(QObject):
    """overlay 拓扑产品壳：主屏 overlay + 主 sprite + 进程级三控制器。

    依赖最小化：QApplication + 主 PetInstance（config 与 per-pet MovieLibrary
    身份源）。屏事件/会话事件/退出收口全部在本类内自接，AppShell 只在
    拓扑分支处构造并 start()。

    4.3 后半：本类同时是进程级共享子系统（agent_link / proactive）的呈现
    扇出目标——``agent_link_manager`` / ``proactive_watcher`` 由 AppShell 在
    拓扑分支处注入（等价 PetWindow 的构造参数）。注入失败=None 时全部呈现面
    静默空转，绝不阻断启动。

    4.3 收口（菜单 parity 配套）：本壳同时承担 PetWindow 的两处**服务宿主**——
    音乐（歌词）控制器（``install_music_lyric`` 四件套）与「看看屏幕」worker
    （``look_at_screen`` + ``look_done`` 信号），以及 sprite 版「黄金回旋」。
    overlay 拓扑下 PetWindow 不再存在，这三项功能没有别的落点。
    """

    # 「看看屏幕」worker → GUI 线程（wire 口径同 PetWindow.look_done）
    look_done = Signal(str, str, bool)

    def __init__(self, app, instance, *, screen=None, sprite_factory=None,
                 agent_link_manager=None, proactive_watcher=None) -> None:
        super().__init__()
        _LIVE_OVERLAY_SHELLS.add(self)  # 测试收口登记（构造即登记，幂等弱引用）
        self.app = app
        self._instance = instance
        self._config = getattr(instance.config, "_main", instance.config)
        # 多屏宿主表（首位 = 主宿主 = slot0 的窗口；见 screen_host.ScreenHost）。
        # ``_initial_screen`` 只是构造期入参，建出主宿主后一切屏信息都从宿主读。
        self._hosts: list = []
        self._initial_screen = screen if screen is not None else app.primaryScreen()
        # 屏集合（屏热插拔的真相源）：建壳时按 Qt 枚举取一次，之后由
        # screenAdded/screenRemoved 维护。家那块屏不在枚举里时（Qt 已摘除的旧
        # wrapper、或建壳期直接传入的屏对象）以它为唯一屏：活动区塌成空集比
        # "少认一块屏"更糟。
        self._screen_list = self._seed_screens(self._initial_screen)
        # 允许桌宠跨屏（主配置全局键、**重启级**）：进程内快照一次，运行期不重读
        # ——设置页改的是磁盘，勾一下就让正在跑的进程多出/收掉一块活动区不是这个
        # 开关的语义（与 stream_capture_mode 同口径）。关闭时：窗仍铺满整个虚拟
        # 桌面，但活动区只剩启动时选定的那块屏（见 ``_activity_rects`` /
        # ``_restore_screen`` / ``move_sprite_to_screen``）。
        # 跨屏模块未激活（功能页）与配置关闭同义：活动区只剩家屏。
        from . import feature_activation as _fa

        self._cross_screen_allowed = (
            bool(self._config.get("allow_cross_screen", False))
            and _fa.is_active("cross_screen")
        )
        self._sprite_factory = sprite_factory or (
            lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
        self._started = False
        self._session_end_done = False
        # 音效预热一次性 latch：只在首次 start、overlay 可见前预热一次
        #（_warm_sound_effects_once）；重 show / stop→start 不重复。
        self._sound_warmed = False
        self.driver: TickDriver | None = None
        self.sprite = None
        self.lib = None
        self.tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_actions: list = []
        # 托盘逐只显隐勾选动作（M14：[sprite, QAction]，菜单重建时替换）
        self._pet_visible_actions: list = []
        self._session_watcher = None
        # D12 指令通道（overlay 拓扑：独立设置进程 → 主进程）；未装时为惰性空转
        self._command_watcher = None
        self._command_timer = QTimer(self)
        # 4.2b 子肥鱼登记：sprite 顺序 = spawn 顺序 = 活跃宠清单顺序
        self._spawned: list = []
        self._spawned_libs: dict = {}
        self._spawned_slots: dict = {}
        # B7b per-slot 取源：逐 sprite 的配置视图（配置同步边界重建）与子 sprite
        # 的各自主气泡跟随器/自言自语宿主/ pet 形宿主。
        self._drag_relaxed_sprite = None  # 拖拽期间被放宽域的那只（收尾时恢复）
        self._mixed_dpr_logged = False  # 混合缩放退化只提示一次
        #: 每块屏**上一次真正应用**的矩形（宿主窗局部坐标）。缩放变化时
        #: `screen.availableGeometry()` 已经变成**新**尺寸，而精灵坐标还是旧形态；
        #: 比例定标必须用这份旧值，否则会把 0.5 量成 1.0（见 _capture_screen_ratios）。
        self._applied_screen_rects: dict[str, tuple[object, QRect]] = {}
        #: 拓扑重排事务的重入闸：`_sync_host_geometry` 与 `_reconcile_hosts_for_dpi`
        #: 互相调用，靠它保证"一次事务只跑一层"，不会在一半状态上再开一层。
        self._topology_txn = False
        #: 事务打断的拖拽：收口后补存（见 `_flush_deferred_geometry_save`）
        self._geometry_save_after_txn = None
        #: **已应用**的宿主形态（True = 每屏一窗）。由建壳与拓扑事务维护，是几何 /
        #: 坐标 / 屏归属 / 落盘的唯一口径；`_multi_window()` 只是按平台、屏幕与
        #: 跨屏开关判定目标形态。两者不一致 = 需要重排（或重排失败后的降级态）。
        self._multi_mode = False
        #: 事务进行中被挡下的重排请求（收口时补做一次）
        self._topology_recheck = False
        self._sprite_cfg_cache: dict = {}
        self._bubble_followers: dict = {}
        self._sprite_hosts: dict = {}
        self._self_talk_hosts: dict = {}
        # 子宠 slot 配置的 (mtime_ns, size) 签名缓存：目录事件/既有 3s 兜底轮询时
        # 用它筛出"配置真的变了"（只 stat；内容读只在命中变化时发生）。
        self._slot_signature_cache = None
        # 音乐（歌词）宿主：懒建（window_optional_services 同款生命周期）
        self._music_lyric = None
        # 「看看屏幕」限流/忙碌状态（window.py 同名口径）
        self._look_busy = False
        self._last_look_ts = 0.0
        # 识屏发起者（右键命中的那一只）：构造期给确定值，`_look_bubble` 按它
        # 分发；`look_at_screen` 每次调用都会重记。
        self._look_sprite = None
        self.look_done.connect(self._on_look_done)
        # 黄金回旋：直接复用 golden_spin.GoldenSpinController（纯状态机：圈数
        # 累计/逐圈加速/缓动），宿主适配器把角度写进 sprite 的整帧旋转通道。
        # 懒建（默认关时不建对象、不 import golden_spin）。
        self._golden_spin = None
        # 弹弓会话（壳内唯一一份）：所有宿主窗共用，见 _wire_host_overlay
        self._slingshot = None
        self._golden_spin_host = _GoldenSpinSpriteHost(self)
        self._golden_spin_target = None
        # 4.3 后半：共享子系统注入（等价 PetWindow 的构造参数；None = 惰性空转）
        self.agent_link_manager = agent_link_manager
        self.proactive_watcher = proactive_watcher
        # 呈现/提醒状态（PetWindow 同名私有面的 sprite 等价物；window_alerts 的
        # host 形函数与 agent_link/proactive 的聚合读取都直接读这些属性）
        self._alert_queue: deque = deque()
        self._alert_current: dict | None = None
        # 实际呈现当前提醒的气泡（G2-R6）：提醒目标会随主位交接换宠，
        # 关闭提醒时要收起真正展示它的那只气泡，而不是新目标的空气泡。
        self._alert_bubble = None
        # 承载宠（G2）：一条提醒在屏期间只跟这一只——它退出/逐只隐藏/屏迁移时，
        # 整条提醒交接给新的可见目标（同 ID、同剩余 TTL、恰好一份副本）；
        # 主位回归只影响此后新产生的事件（不抢回）。见 _sync_alert_copy。
        self._alert_presenter = None
        # 首次呈现时刻/总时长（限时提醒的剩余 TTL 基准；迁移不重置，不永生）。
        self._alert_presented_key = None
        self._alert_presented_at = 0.0
        self._alert_presented_ms = 0
        # 承载气泡的生命周期收口期（宠退出/逐只隐藏/屏迁移重建）：期内的 hidden
        # 信号不得被当成"用户收起了提醒"再挂一次（重放/递归的唯一闸门）。
        self._alert_copy_teardown = False
        # 提醒计时缝（单调时钟；测试注入可控时钟，不用墙钟等待赌时序）
        self._alert_clock = time.monotonic
        # 队列满时的处置计数（可诊断；见 window_alerts._admit_to_queue）
        self._alert_overflow_count = 0
        self._sticky_bubble_active = False
        self._sticky_text = ""
        self._sticky_subtitle = ""
        self._sticky_buttons: list | None = None
        self._bubble_busy_until = 0.0
        self._bubble_suppressed = False
        self._last_sticky_restore = 0.0
        # 飞行中的 sprite 集合（B1）：被撞飞的那只禁气泡，其余宠不受影响。
        # 由行为控制器的飞行边沿回调维护（见 _on_sprite_flight_changed），
        # **不**复用 _bubble_suppressed——那个位同时是 proactive/联动节流信号
        # （multi_window_shared / agent_link / todo_reminder 读它），飞行期置真
        # 会把联动与主动搭话一起掐掉。
        self._flying_sprites: set = set()
        # 联动动作链（agent_link 的 request_link_anim/idle 落点）
        self._pending_link_anim: str | None = None
        self._link_anim_current: str | None = None
        self._link_next_provider = None
        self._link_chain: _LinkAnimChain | None = None
        # 快速对话气泡（懒建；无聊天变体 = None 静默降级）
        self._quick_chat = None
        self._quick_chat_resolved = False
        self._quick_chat_cls = None
        self._build()
        self._wire_screen_signals()
        self._install_session_watcher()
        self.app.aboutToQuit.connect(self._on_about_to_quit)

    # ---------------------------------------------------------------- 屏宿主（单窗）
    @property
    def _main_host(self):
        """唯一宿主。``self.overlay`` / ``_bounds`` / 三控制器都是它的面。

        全进程只有这一张合成窗（``_hosts`` 长度恒为 1，永不建第二块屏的窗口）：
        它铺的是整个虚拟桌面，跨屏只是同一张窗内的自由移动。
        """
        return self._hosts[0] if self._hosts else None

    @property
    def _screen(self):
        """家那块屏（关闭跨屏时唯一活动区、屏拔出时的兜底判据）。"""
        host = self._main_host
        return host.screen if host is not None else None

    @property
    def _bounds(self) -> QRect:
        """窗口局部坐标里的并集包围矩形（= 整张合成窗）。"""
        host = self._main_host
        return QRect(host.bounds) if host is not None else QRect()

    @property
    def overlay(self):
        host = self._main_host
        return host.overlay if host is not None else None

    @property
    def behavior(self):
        host = self._main_host
        return host.behavior if host is not None else None

    @property
    def collision(self):
        host = self._main_host
        return host.collision if host is not None else None

    @property
    def physics(self):
        host = self._main_host
        return host.physics if host is not None else None

    @property
    def _probe(self):
        host = self._main_host
        return host.probe if host is not None else None

    @property
    def _throw_egg(self):
        host = self._main_host
        return host.throw_egg if host is not None else None

    def hosts(self) -> list:
        """当前全部宿主（只读副本；单窗下恒为一个元素）。"""
        return list(self._hosts)

    def host_for_sprite(self, sprite):
        """持有该 sprite 的宿主（单窗下恒为唯一宿主）。

        保留这一面是因为它同时是"这只宠画在哪"的公开回答：尚未落到窗上的
        sprite（spawn 建库期/已退出的对象）同样回落到唯一宿主，不会拿到 None。
        """
        if sprite is not None:
            for host in self._hosts:
                if sprite in host.overlay.sprites:
                    return host
        return self._main_host

    def host_for_screen(self, screen):
        """该屏的宿主：合成窗铺满整个虚拟桌面，任何**已连接**屏都在同一张窗里；
        不在本次枚举里的屏（已拔出/未知）返回 None。"""
        if screen is None:
            return None
        if screen not in self._screens():
            return None
        if self._active_multi():
            for host in self._hosts:
                if host.screen is screen:
                    return host
            return None
        return self._main_host

    def _host_of(self, sprite):
        """持有该 sprite 的宿主；不在窗上返回 None（不回落）。"""
        for host in self._hosts:
            if sprite in host.overlay.sprites:
                return host
        return None

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        # M-2 统一 tick 驱动器（T2）：进程级**一个**时钟驱动唯一宿主；仿真段由
        # 宿主持有（见 tick_driver.tick_sim），overlay 只做 advance+paint。
        self.driver = TickDriver(self)
        # 全部 overlay advance 之后跑一次：把每只宠的屏归属与 DPR 对齐到它
        # **这一拍的实际身体位置**（弹簧档的位移发生在 advance 里，事件时刻读到的
        # 是上一拍的位置）。跨屏本身不需要任何路由——同一张窗内自由移动。
        self.driver.set_post_advance(self._advance_screen_sync)
        self._multi_mode = bool(self._multi_window())
        if self._multi_mode:
            # 每块屏一张窗（一窗只能一个 DPR）。家屏先建，保证
            # ``_main_host`` 仍是"启动那块屏"。
            order = [self._initial_screen]
            order += [s for s in self._screens() if s is not self._initial_screen]
            host = self._create_host(order[0])
            for extra in order[1:]:
                self._create_host(extra)
        else:
            host = self._create_host(self._initial_screen)
        # 4.1c 音效：点击 + 碰撞（click_sound 默认包，静默降级）。门/音量/音源都按
        # **触发 sprite** 的身份配置取源（B7b）：碰撞事件带成员 id，由壳解析成
        # sprite；点击由壳在 _on_sprite_click(sprite) 里带身份调用。播放器进程级
        # 一个（`_wire_host` 已按宿主注册监听）。
        self._sound = SpriteSoundPlayer(
            self._config, host.collision,
            config_for=self._sprite_config,
            sprite_for_event=self._sound_sprite_for_event)
        # 灵动岛与它的宿主归属（AppShell 建岛后 attach_island 接线）
        self._island = None
        self._island_host = None
        # 4.1b 窗口能力状态（on_top/穿透复合/全屏与光标监视/runtime 避让标记）。
        # 必须在**复活宠物之前**就位：主宠记着别的屏时，`_spawn_slot` 复活它的
        # 那一步就会走宿主换屏（`_migrate_to_screen`），而换屏里要重下发窗口能力。
        self._auto_hidden = False
        self._user_mouse_through = bool(self._config.get("mouse_through", False))
        self._auto_cursor_hidden = False
        self._cursor_restore_pending = False
        # 收敛请求的待办：被 grab 闸挡住的收敛不许丢弃，由 grab 收尾补做
        # （见 _converge_auto_cursor_hidden / _on_grab_finished）。
        self._converge_pending = False
        from . import pet_profiles

        config_dir = self._spawn_config_dir()
        if config_dir:
            migration = pet_profiles.migrate_slot0_profile(config_dir)
            # 与 AppShell 同一口径（ops D2）：``target-corrupt`` 只牵连 slot0 这一份档案
            # → 该槽不复活，别的照常；其余非 ok 状态（源配置坏了/备份失败/锁超时）
            # 涉及全局面，照旧硬失败。两处判据必须同源，否则两个入口对同一次事故
            # 给出不同处置（一个降级、一个炸）。
            slot0_unusable = False
            if not migration.ok:
                if not migration.slot_only_problem:
                    raise RuntimeError(str(migration))
                slot0_unusable = True
            pet_profiles.migrate_active_set(config_dir)
            remembered = list(pet_profiles.load_active_set(config_dir).slots)
            if slot0_unusable:
                remembered = [n for n in remembered if n != 0]
            self._remembered_active = remembered
        else:
            self._remembered_active = [0]
            self._slot_memory = {0: {key: copy.deepcopy(self._config.get(key))
                                    for key in pet_profiles.PER_PET_KEYS}}
        if 0 in self._remembered_active:
            self._spawn_slot(0)
        # 4.2b：按活跃宠清单复活子肥鱼（D5：依据是运行时清单，不是 slot 配置存在）
        self._restore_spawned_pets()
        # 主宿主的投喂控制器在 ``_create_host`` 时就挂上了（那时还没有主宠，落点
        # 兜底对象是 None）；宠复活/生成之后再挂一次，把"落点退回主宠"这一路补上。
        self._bind_feeding(self._main_host)
        self._watcher = FullscreenCursorWatcher(self)
        self._watcher.fullscreen_changed.connect(self._on_fullscreen_changed)
        self._watcher.cursor_visibility_changed.connect(
            self._on_cursor_visibility_changed)
        if self.sprite is not None:
            self._bind_position_listener(self.sprite)
        self._last_marker_write = 0.0
        self._apply_window_capabilities()
        # 4.1c 气泡跟随（真实 PetSpeechBubble；静默降级）
        self._bind_bubble()
        # 自言自语族（周期气泡 / 点击台词 / 配图 / 朗读）：旧架构唯一宿主是
        # PetWindow（window.py:458-480 + :740），overlay 拓扑下必须由本壳装配
        self._init_self_talk()
        # 音乐自动唱歌（M5f）：轮询定时器 + 唱歌续播链（extras 尾段观测唱歌结束）
        self._init_music_sing()
        self._music_sing_chain = _MusicSingChain(self)
        self.driver.add_extra_controller(self._music_sing_chain)
        # 4.3 后半：联动动作链接续控制器（extras 尾段观测一次性动作结束边沿）
        self._link_chain = _LinkAnimChain(self)
        self.driver.add_extra_controller(self._link_chain)
        self._install_shared_link()
        self._build_tray()
        self._install_settings_command_watch()
        self._slot_signature_cache = self._slot_config_signatures()
        self._announce_no_pets_if_empty()

    def _announce_no_pets_if_empty(self) -> None:
        """一只宠物都没有时**不许静默**（issue #286）。

        零活跃宠物是一个合法状态（用户点过托盘「退出这只」、``overlay-active-pets.json``
        损坏或版本不认识、slot0 档案降级被剥出活跃集），但旧口径下 overlay 拓扑照常
        ``show()`` 一个全屏置顶空窗、legacy 拓扑只留一个托盘图标，**既不报错也不打
        日志**：用户看到的是「进程在、托盘在、桌面上什么都没有」，而 CPU 停在 idle
        （0 sprite 空窗停表，见 M12c），观感等同于"启动失败或被安全软件拦了"。

        这里把事实说清楚：日志给排查，托盘 tooltip / 恢复窗给用户（无托盘时恢复窗是
        唯一可见入口）。不造宠物、不碰档案——只陈述与指路。
        """
        if self._all_sprites():
            return
        reason = self._no_pets_reason()
        active = list(getattr(self, "_remembered_active", ()) or ())
        config_dir = self._spawn_config_dir() or getattr(self._config, "dir", None)
        logging.warning(
            "overlay: 启动后没有任何宠物（活跃集合=%s，配置目录=%s）：%s",
            active, config_dir, reason)
        text = (f"没有可显示的桌宠：{reason}\n"
                "托盘（或本窗口）的「生成 / 恢复 slot0」可以重建。")
        # 恢复窗开着且还是空白时补一句话（无托盘时它是唯一的可见入口）。
        # **绝不写 ``_slot0_profile_diagnosis``**：那是"slot0 档案降级"的专用诊断源，
        # ``_spawn_failure_text`` 会优先引用它、因而带上坏档案路径；塞一句笼统的
        # "没有宠物"进去会把那条线索顶掉（本片首版就是这么踩的，
        # `test_slot0_lifecycle` 的恢复窗用例当场红）。零宠物是另一回事，只走
        # 日志、托盘 tooltip 与恢复窗的空白补齐。
        label = getattr(self, "_recovery_diagnosis_label", None)
        if label is not None:
            try:
                if not str(label.text() or ""):
                    label.setText(text)
                    label.setVisible(True)
            except RuntimeError:  # 窗口已随进程销毁
                pass
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                tray.setToolTip(f"dsh-pet：没有可显示的桌宠（{reason}）")
            except RuntimeError:
                pass

    def _no_pets_reason(self) -> str:
        """零宠物的一句话原因：按三条已知入口如实陈述，不猜。"""
        diagnosis = self.slot0_profile_diagnosis()
        if diagnosis:
            return diagnosis
        active = list(getattr(self, "_remembered_active", ()) or ())
        if not active:
            return "活跃宠物清单为空（overlay-active-pets.json 里没有有效身份）"
        return f"活跃清单 {active} 里的宠物都没能建起来（原因见日志）"

    def _create_host(self, screen):
        """建唯一宿主（合成窗 + 一份控制器组 + 活动区），并接线到壳。

        窗口矩形取各屏**可用工作区**的并集包围矩形（见 ``_desktop_bounds``）；
        ``screen`` 只是"家那块屏"的记录（关闭跨屏时它决定唯一活动区）。
        """
        if self._mixed_dpr() and not self._mixed_dpr_logged:
            self._mixed_dpr_logged = True
            multi_window = self._multi_window()
            notice = mixed_dpr_notice(
                [s.devicePixelRatio() for s in self._screens()],
                multi_window=multi_window)
            # 多窗是正解（正常路径），只作说明；退化才是需要用户注意的情况。
            (logging.info if multi_window else logging.warning)(notice)
        desktop = self._host_window_rect(screen)
        bounds = QRect(0, 0, desktop.width(), desktop.height())
        overlay = ShellOverlayWindow(screen, driver=self.driver)
        # 窗口的几何来源必须是**它自己那块屏**：注入壳的并集时，任何屏信号
        # （改分辨率/任务栏/刷新率）都会让这张窗 `_desktop_rect()` 重算出整张
        # 桌面，把"每屏一窗"悄悄扩回横跨混合 DPI 的单窗（实机探针：
        # (-1920,0,1536,816) → (-1920,0,4480,1392)，#307 的显示错误原样复活）。
        overlay._desktop_bounds = (  # noqa: SLF001 - 同模块协作面
            lambda screen=screen: self._host_window_rect(screen))
        overlay._screen_of_sprite = self._screen_of_sprite  # noqa: SLF001
        overlay.setGeometry(desktop)
        host = ScreenHost(screen, overlay, bounds)
        self._hosts.append(host)
        # 活动区要在宿主进表之后算：家那块屏（关闭跨屏时唯一活动区）从宿主读
        host.set_activity(self._host_activity(host))
        self._remember_applied_screen_rect(screen, host)
        self._wire_host(host)
        return host

    def _wire_host(self, host) -> None:
        """宿主接线：控制器回调、屏信号、overlay 上的壳层钩子、投喂。

        这里是"哪些壳层面挂在窗口上"的唯一清单——漏挂哪个，那一路就静默丢失。

        三条碰撞监听是**壳自己的绑定方法**（与单世界时代同一对象、同一签名），
        属唯一世界的一份。
        """
        host.behavior.on_flight_changed = self._on_sprite_flight_changed
        # 唱歌续播判据（window.py:2595-2601）：行为层不查音频，只问这一面。缺它
        # 则唱歌 clip 播完走掷骰链——约 60% 概率被切到随机动作，唱歌循环碎裂。
        host.behavior.sing_continue_provider = lambda: self._music_sing_active
        host.collision.add_collision_listener(self._on_collision_squash)
        host.collision.add_collision_listener(self._on_collision_probe)
        host.collision.add_collision_listener(self._on_collision_sound)
        # 边缘探头（edge_probe 移植）：tick 尾段（姿态最终写入口）；config 键
        # edge_probe_enabled（默认 False）世界内每次进入判定前热读，设置页即切即生效
        from .sprite_edge_probe import create_edge_probe_world
        host.probe = create_edge_probe_world(self._config, self._probe_bounds(host))
        # throw_egg 彩蛋：探头被撞飞头部跟随速度（extras 尾段，读物理结算后的
        # 速度——落地兜底依赖 physics 已切回 normal，顺序天然满足）
        from .sprite_throw_egg import create_throw_egg_world
        host.throw_egg = create_throw_egg_world(
            QRect(host.bounds), probe=host.probe)
        host.extras = [host.probe, host.throw_egg]
        self._wire_host_overlay(host, host.overlay)
        self._connect_host_screen(host)
        self._bind_feeding(host)

    def _wire_host_overlay(self, host, overlay) -> None:
        """把壳层的窗口回调挂到该宿主的 overlay 上（新建与换屏共用一份清单）。"""
        overlay.edge_probe = host.probe
        overlay.click_feedback = self._click_feedback
        # 点击气泡族（余额/点击自言自语）+ 点击音效：与 click_feedback 同位置注入
        overlay._on_sprite_click = self._on_sprite_click
        # M5e：点击触发黄金回旋路由（与 _on_sprite_click 同位置注入）
        overlay._click_route_spin = self._route_click_golden_spin
        # 4.1c 弹弓：controller 壳内一份，这里只接 config
        # （slingshot_enabled 热读，设置页即改即生效）。单窗下会话与绘制天然
        # 同源（同一张窗、同一套局部坐标）。
        if self._slingshot is None:
            from .sprite_slingshot import SlingshotController

            self._slingshot = SlingshotController()
        overlay.slingshot = self._slingshot
        overlay.slingshot.config = self._config
        # 4.1c 全量右键菜单（facade 适配旧 context_menus 建造器）
        from .sprite_menu_facade import build_sprite_full_menu
        overlay._full_menu_builder = (
            lambda target: build_sprite_full_menu(self, target))
        overlay._through_changed = self._on_user_through_changed
        overlay._grab_finished_cb = self._on_grab_finished
        # 多窗（混合缩放）跨屏：拖到另一块屏就把这只交给那块屏的窗
        overlay._handover_cb = self._hand_over_drag
        overlay._host_for_sprite = self.host_for_sprite
        # 真拖拽升级（过 DRAG_THRESHOLD）：窗口先取消探头会话，再把这只的钳制域
        # 放宽到整张窗——两件事都在壳层做，因为放宽/恢复要碰宿主的活动区。
        overlay._drag_committed_cb = self._on_drag_committed
        # 穿透态收敛的两个时机（见 _converge_auto_cursor_hidden）：显示/恢复
        # 与真到达窗口的鼠标输入，同一入口、同一语义
        overlay._shown_cb = self._converge_auto_cursor_hidden
        overlay._user_input_cb = self._converge_auto_cursor_hidden
        # sprite-removed 三条注销监听（退出任意一只宠都要注销行为/探头/彩蛋状态）
        self._wire_sprite_removed_hooks(overlay, host)

    def _click_feedback(self) -> None:
        """点击音效通道（overlay 的 ``click_feedback``，无参）。

        绑壳方法而不是 ``self._sound.on_click``：宿主在 ``_build`` 里先于播放器
        建出来（播放器要拿主宿主的碰撞世界当缺省世界），绑定对象必须等得及。
        逐宠身份的那一份由 ``_on_sprite_click`` 另行播放（同一处判定通道）。
        """
        self._sound.on_click()

    def _wire_sprite_removed_hooks(self, overlay, host=None) -> None:
        """挂 sprite-removed 三条注销监听（``_build`` 与宿主换屏共用）。

        ``overlay.remove_sprite`` 按这三条注销外部簿记：行为状态表
        （``behavior.forget``）、边缘探头 armed/预进入状态（``probe.forget``）、
        抛掷彩蛋角度（``throw_egg.forget``）。迁移重建 overlay 时漏挂，则
        屏热插拔/主屏切换之后再退出任意一只宠都不再注销——每退一只泄漏一份
        强引用（含 pixmap/命中图）与探头状态。
        """
        host = self._main_host if host is None else host
        overlay.add_sprite_removed_listener(host.behavior.forget)
        overlay.add_sprite_removed_listener(host.probe.forget)
        overlay.add_sprite_removed_listener(host.throw_egg.forget)

    def _create_main_library(self):
        """per-pet MovieLibrary（T3）；角色素材缺失回退默认角色（口径同
        AppShell._create_ui_with_character_fallback）。"""
        return self._create_library_for(
            str(self._sprite_config(self.sprite).get('character', catalog.DEFAULT_CHARACTER)),
            fallback_writer=self._slot_writer(0))

    def _create_library_for(self, character_id, *, fallback_writer=None):
        """按角色 id 建 per-pet 库；素材缺失回退默认角色（写回发起方那份配置）。

        ``fallback_writer`` = 回退时把角色写回哪份配置（缺省 = 主配置；子 sprite
        切角色时传它自己的 slot 写入器——回退绝不许污染主宠的角色）。
        """
        cid = str(character_id or catalog.DEFAULT_CHARACTER)
        try:
            return self._instance._create_library(cid)
        except FileNotFoundError:
            if cid == catalog.DEFAULT_CHARACTER:
                raise
            logging.warning('角色 %s 素材缺失，回退默认角色 %s',
                            cid, catalog.DEFAULT_CHARACTER)
            if fallback_writer is not None:
                if not fallback_writer('character', catalog.DEFAULT_CHARACTER):
                    raise RuntimeError("默认角色回写失败，未应用角色回退")
            else:
                self._config.set('character', catalog.DEFAULT_CHARACTER)
            self._library_fallback_used = True
            return self._instance._create_library(catalog.DEFAULT_CHARACTER)

    # ---------------------------------------------------------------- 屏与几何
    @staticmethod
    def _seed_screens(home) -> list:
        """建壳期的屏集合：Qt 枚举为准；枚举里没有家那块屏时就只认它。"""
        screens = list(QGuiApplication.screens())
        if home is None:
            return screens
        if not any(candidate is home for candidate in screens):
            return [home]
        return screens

    def _screens(self) -> list:
        """当前屏集合（窗口矩形、活动区与屏归属的取数源）。"""
        return list(self._screen_list)

    def _mixed_dpr(self) -> bool:
        """各屏设备像素比是否不一致（混合缩放）。

        一张合成窗只有一个 DPR：混合缩放下按并集铺窗，绘制会按**单一** DPR 缩放，
        与实际屏位置错位——实机表现是桌宠完全不可见、点不到（issue #307：主屏
        1920×1080@100% + 副屏 3200×2000@200%，把副屏调成 100% 就正常）。
        """
        ratios = {round(float(screen.devicePixelRatio() or 1.0), 3)
                  for screen in self._screens()}
        return len(ratios) > 1

    def _scaled_multi_screen(self) -> bool:
        """多屏且**任意一块**屏的 DPR ≠ 1（单窗坐标系在这种机器上不可靠）。

        Qt 6.12 在 Windows 上对**负原点屏**报的是混合坐标系：原点是原生像素、宽高
        是逻辑像素（本机实测，左侧副屏 1920×1080@125%：Win32 原生
        ``(-1920,0,1920,1080)``，Qt 报 ``(-1920,0,1536,864)``——x 差 384）。把这些
        矩形放进同一张窗的坐标空间时，屏与屏之间会凭空多出一段"无显示器空洞"、
        屏自己的外侧边缘反而没有边界：宠被钳在半块屏里、或走到看不见的地方
        （issue #310：双 4K@150%，A 在左）。
        """
        screens = self._screens()
        if len(screens) < 2:
            return False
        for screen in screens:
            if abs(round(float(screen.devicePixelRatio() or 1.0), 3) - 1.0) > 1e-6:
                return True
        return False

    def _active_multi(self) -> bool:
        """**此刻已经应用**的是每屏一窗形态吗（``_multi_mode`` 模型标志）。

        与 :meth:`_multi_window` 的分工必须分清：那个是"**想要**什么形态"（按当前
        平台、屏幕与跨屏开关算出的目标），这个是"现在实际有几张窗"。拓扑重排的**决定**用前者，
        **坐标 / 几何 / 屏归属 / 落盘**一律用后者。

        混用的后果不是理论问题：补窗失败时（想要多窗、实际一张）窗口自己按单窗铺整张
        桌面，活动区却按多窗给 `(0,0,w,h)`——宠被算到别的屏，落盘还会把 1.5 这种
        错误比例写进档案。
        """
        return bool(self._multi_mode)

    def _multi_window(self) -> bool:
        """判断目标拓扑是否采用每屏一窗（须开启允许跨屏）。

        多屏时，混合 DPR 或任一屏 DPR 非 1 都使用每屏一窗，避免单窗的绘制倍率
        或坐标映射错位；各屏缩放相同但非 1 也属于此条件（见两个缩放判据）。
        macOS 多屏即使全部 DPR 为 1 也采用此拓扑，避免跨显示器的大窗口在副屏
        缓冲区有内容、桌面上却不可见；其它平台的全 1 倍缩放多屏仍使用单窗。
        """
        return self._cross_screen_allowed and (
            (_IS_MACOS and len(self._screens()) > 1)
            or self._mixed_dpr() or self._scaled_multi_screen())

    @staticmethod
    def _window_physical_origin(overlay) -> QPoint:
        """窗口的**原生（物理）**左上角；取不到时退回逻辑原点。

        混合 DPI 下逻辑原点不能当物理用：Qt 官方文档的 islands-of-screens ——
        屏的位置不随缩放变，逻辑桌面不是连续平面。跨窗换算必须走物理坐标。
        """
        fallback = overlay.geometry().topLeft()
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(int(overlay.winId()), ctypes.byref(rect))
        except Exception:  # noqa: BLE001 - 非 Windows / 无原生窗时退回逻辑原点
            return fallback
        if rect.right <= rect.left and rect.bottom <= rect.top:
            return fallback
        return QPoint(rect.left, rect.top)

    def _sprite_pos_in(self, sprite, source_overlay, target_overlay) -> QPointF:
        """这只的左上角换算到 ``target_overlay`` 的局部逻辑坐标（跨 DPI 正确）。

        物理 = 源窗物理原点 + d_src × 源局部；目标局部 = (物理 − 目标窗物理原点)/d_dst。
        少了这里的两处 DPR 比例，换窗本身就会位移几百像素——实机的"瞬移"。
        """
        p_src = self._window_physical_origin(source_overlay)
        p_dst = self._window_physical_origin(target_overlay)
        d_src = float(source_overlay.devicePixelRatioF()) or 1.0
        d_dst = float(target_overlay.devicePixelRatioF()) or 1.0
        phys = QPointF(p_src.x() + sprite.pos.x() * d_src,
                       p_src.y() + sprite.pos.y() * d_src)
        return QPointF((phys.x() - p_dst.x()) / d_dst,
                       (phys.y() - p_dst.y()) / d_dst)

    def _virtual_work_area(self) -> QRect:
        """所有屏可用工作区的并集（全局逻辑坐标）——多窗拖拽的宽松域用。"""
        rect = None
        for screen in self._screens():
            avail = QRect(screen.availableGeometry())
            rect = avail if rect is None else rect.united(avail)
        return rect if rect is not None else QRect()

    def _host_window_rect(self, screen) -> QRect:
        """该宿主窗口的全局矩形（多窗模式 = 该屏可用区）。"""
        if self._active_multi() and screen is not None:
            return QRect(screen.availableGeometry())
        return self._desktop_bounds(screen)

    def _probe_bounds(self, host) -> QRect:
        """边缘探头世界的边界。

        单窗：就是本窗（覆盖整桌面）。**多窗（混合缩放）：必须是整张虚拟桌面
        换算到本窗局部**——否则窗的左缘会被当成"屏幕边缘"，而那里其实是与相邻屏
        相接的缝：探头的进入判定会在缝上触发，把宠物推到窗外、切成静止站姿
        （实机："那只子宠不动了、没有动画"）。
        """
        if not self._active_multi():
            return QRect(host.bounds)
        desktop = self._virtual_work_area()
        if desktop.isEmpty():
            return QRect(host.bounds)
        origin = host.overlay.geometry().topLeft()
        return QRect(desktop.translated(-origin.x(), -origin.y()))

    def _host_activity(self, host) -> list:
        """该宿主自己的活动区（多窗模式只有它那块屏，局部原点即本窗原点）。"""
        if self._active_multi():
            bounds = host.bounds
            return [(host.screen, QRect(0, 0, bounds.width(), bounds.height()))]
        return self._activity_rects()

    def _desktop_bounds(self, home=None) -> QRect:
        """窗口全局矩形 = 各屏**可用工作区**的并集包围矩形。

        用 availableGeometry 而不是 virtualGeometry：后者含每屏任务栏，窗口铺满
        它就是以"全屏矩形"存在，Wallpaper Engine / TrafficMonitor 这类按窗口矩形
        自判全屏的软件会把桌宠当成全屏应用（WE 暂停壁纸、TM 隐藏悬浮窗，#302）。

        **混合 DPR 时退化为家屏可用区**：宁可失去跨屏，也不能看不见（#307）。
        """
        # ``home`` 参数只为建壳期（宿主还没建、``self._screen`` 还是 None）
        if home is None:
            home = self._screen
        # 多屏且缩放不是 1:1、又没开跨屏：退化到启动那块屏。单窗在这种机器上
        # 铺整张虚拟桌面会把 Qt 那个混合坐标系（负原点屏的原生 x + 逻辑宽高，
        # 见 _scaled_multi_screen）当成真实桌面，宠被画到屏外（issue #310）。
        if home is not None and self._scaled_multi_screen() and not self._multi_window():
            return QRect(home.availableGeometry())
        rect = None
        for screen in self._screens():
            avail = QRect(screen.availableGeometry())
            rect = avail if rect is None else rect.united(avail)
        return rect if rect is not None else QRect()

    def _screen_rect(self, screen, *, multi=None) -> QRect:
        """某屏可用工作区在窗口局部坐标里的矩形（活动区的一块；比例/落位同源）。

        ``multi``：**几何事务**里要按"实际有几张窗"传，不能让它自己读判定——
        拓扑对齐失败时（某块屏的宿主建不起来）判定说多窗、实际只有一张窗，两套
        坐标系混用会把家屏的活动区写成窗口原点，(位置直接算坏)。
        """
        avail = QRect(screen.availableGeometry())
        if self._active_multi() if multi is None else multi:
            return QRect(0, 0, avail.width(), avail.height())
        desktop = self._desktop_bounds()
        return QRect(avail.translated(-desktop.x(), -desktop.y()))

    def _activity_rects(self, *, multi=None) -> list:
        """活动区（精灵可站立域）：``((screen, 局部矩形) ...)``。

        各屏可用工作区平移到窗口局部坐标后是**一组**矩形而不是并集：错位拼接
        的屏间空洞没有显示器，单矩形会让宠走进看不见的地方。关闭跨屏时只留家那
        块屏——窗仍铺满全桌面，但宠出不去；混合 DPR 时同样只留家那块（窗口本身
        已退化为家屏，见 ``_desktop_bounds``）。
        """
        screens = self._screens()
        home = self._screen
        mixed = self._mixed_dpr()
        rects = []
        for screen in screens:
            if screen is not home and (not self._cross_screen_allowed or mixed):
                continue
            rects.append((screen, self._screen_rect(screen, multi=multi)))
        return rects

    def _screen_at_global(self, point):
        """全局坐标落在哪块屏的可用区上（不在任何屏上返回最近的那块）。"""
        x, y = int(point.x()), int(point.y())
        screens = self._screens()
        for screen in screens:
            if screen.availableGeometry().contains(x, y):
                return screen
        if not screens:
            return None
        return min(screens, key=lambda s: self._center_distance(s, x, y))

    @staticmethod
    def _center_distance(screen, x: int, y: int) -> float:
        """点到某屏可用区中心距离的平方（不在任何屏上时的兜底取屏）。"""
        avail = screen.availableGeometry()
        cx = avail.x() + avail.width() / 2.0
        cy = avail.y() + avail.height() / 2.0
        return (cx - x) ** 2 + (cy - y) ** 2

    def _screen_of_sprite(self, sprite):
        """这只身体中心所在的屏（屏归属/逐只 DPR 的唯一判据）。"""
        host = self._host_of(sprite) or self._main_host
        if host is None or sprite is None:
            return self._initial_screen
        if self._active_multi():
            return host.screen
        origin = host.overlay.geometry().topLeft()
        body = sprite.body_rect()
        return self._screen_at_global(
            QPointF(origin.x() + body.x() + body.width() / 2.0,
                    origin.y() + body.y() + body.height() / 2.0))

    def _advance_screen_sync(self, _dt=None) -> None:
        """每 tick 一次（advance 之后）：多屏时才逐只对齐屏归属与 DPR。

        单屏没有信息量（屏归属恒定、DPR 由屏信号重喂），故整段跳过——110Hz 下
        这是绝大多数用户的形态。
        """
        if len(self._screen_list) < 2:
            return
        self._sync_sprite_screens()
        # 拖拽中：弹簧每拍都在动，投影必须跟着刷新，否则缝上的另一半会定格
        if self._drag_relaxed_sprite is not None:
            self._sync_drag_projection()
        if self._active_multi():
            for host in list(self._hosts):
                for sprite in list(host.overlay.sprites):
                    if not getattr(sprite, "_dragging", False):
                        self._place_sprite_in_physical_screen(sprite, host)

    def _sync_sprite_screens(self) -> None:
        """每 tick 一次：把每只宠的屏归属与渲染 DPR 对齐到它的实际位置。

        跨屏不产生任何"移交"（同一张窗内自由移动），但两件事必须跟着走：
        ``sprite.home_screen``（档案 ``screen_name`` 的来源）与逐只 DPR（一张窗
        只有一个 DPR，每只的 pixmap/命中图按自己那块屏重建）。
        """
        for host in self._hosts:
            for sprite in list(host.overlay.sprites):
                screen = self._screen_of_sprite(sprite)
                if screen is None:
                    continue
                sprite.home_screen = screen
            host.overlay._feed_dpr()  # noqa: SLF001 - 同模块协作面

    @staticmethod
    def _default_corner_pos(bounds: QRect, rect: QRect) -> QPointF:
        """默认右下角（对齐 window_placement._default_corner_pos 的旧算式：
        右缘留 CORNER_MARGIN、底贴可用区底）。"""
        return QPointF(bounds.x() + bounds.width() - 1 - rect.width() - catalog.CORNER_MARGIN,
                       bounds.y() + bounds.height() - 1 - rect.height())

    # ---------------------------------------------------------------- 主 sprite 接线
    def _bind_feeding(self, host=None) -> None:
        """投喂控制器逐宿主挂，落点按 ``sprite_at`` 逐只判定（B02）：拖到哪只就
        吃哪只、气泡也落那只。

        overlay/宿主窗口更换后必须重挂，否则投喂命中判定仍打在旧窗口的几何上
        （4.2b 主实例提升同理）；每块屏的窗口各挂一份，副屏上的宠才吃得到拖进去
        的文件（拖放事件只送到光标下那张窗）。

        ``host=None`` = 重建主宿主的控制器（旧的 ``self._feeding`` 语义）。
        """
        from .sprite_feeding import SpriteFeedingController
        host = self._main_host if host is None else host
        if host is None:
            return
        primary = self.sprite if host is self._main_host else None
        feeding = SpriteFeedingController(
            host.overlay, primary, self._config, host.behavior)
        feeding._bubble_cb = self._say_feeding_bubble
        # 解读询问接缝由壳持有：重建控制器时回填，否则主宠提升/屏迁移之后
        # 拖文件解读静默失效。
        feeding.interpret_offer = getattr(self, "_file_interpret_offer", None)
        host.feeding = feeding
        host.overlay._feeding = feeding
        if host is self._main_host:
            self._feeding = feeding

    def set_file_interpret_offer(self, offer) -> None:
        """注入拖文件解读询问回调（app 侧 ``FileInterpretController.offer``）。

        overlay 拓扑下 PetWindow 不构造，``file_eater`` 的 ``interpret_offer``
        接缝没有别的宿主：AppShell 持有解读控制器，经本方法把 ``offer(paths)``
        挂到当前投喂控制器上（``sprite_feeding`` 吃完后调用，口径同旧
        ``file_eater.eat_paths:183-186``）。传 None = 解除。
        """
        self._file_interpret_offer = offer
        for host in self._hosts:
            feeding = host.feeding
            if feeding is not None:
                feeding.interpret_offer = offer

    def _bind_bubble(self) -> None:
        """主 sprite 的气泡跟随器；重建前先关旧跟随器（防旧顶层气泡残留）。

        4.3 后半：跟随器接上点击回调（快速对话入口）+ ``hidden_signal``
        （提醒队列推进/粘滞气泡恢复，``window_alerts.on_speech_bubble_hidden``），
        并按聊天可用性切换气泡的可点状态。

        自言自语族：气泡风格（``self_talk_bubble_style``）与「气泡文字大小」
        （``bubble_text_scale``）此前在 overlay 拓扑下被静默降级成默认值——
        旧架构由 PetWindow 构造期注入（window.py:454-478），这里补齐同一注入。

        B7b：主 sprite 之外，每只子 sprite 也各有**自己那只**气泡（旧架构一窗
        一气泡，"点小肥鱼 → 气泡出现在小肥鱼头顶"）；已在册的子宠跟随器幂等补齐。
        """
        follower = getattr(self, "_bubble_follower", None)
        if follower is not None:
            follower.close()
        self._bubble_follower = (self._make_bubble_follower(self.sprite)
                                 if self.sprite is not None else None)
        self._bind_main_bubble_signals()
        for sprite in list(self._spawned):
            self._ensure_sprite_bubble(sprite)

    def _bind_main_bubble_signals(self) -> None:
        """主气泡的 hidden 信号/可点态接线（构造后与"提升者跟随器接管"共用）。"""
        bubble = self._speech_bubble
        if bubble is not None:
            self._apply_bubble_text_scale(bubble, self._sprite_config(self.alert_target_sprite()))
            self._connect_alert_hidden(bubble)
        if self._quick_chat_resolved:
            # 快速对话可用性已解析过（不重付 import 成本）：新气泡直接对齐
            # 可点状态；否则等首次冒泡时由 show_bubble 的
            # _apply_bubble_interactive 惰性解析（与 legacy 触点一致）。
            self._apply_bubble_interactive()

    def _connect_alert_hidden(self, bubble) -> None:
        """把一只气泡的 hidden 信号接进提醒推进链（**同一只只接一次**）。

        承载气泡可能不是主气泡：slot0 缺席时 ``_speech_bubble`` 就是某只子宠的
        气泡，而 ``_bind_bubble`` 会随 slot0 生灭反复调用——同一只子宠气泡被接
        两次的话，一次隐藏会触发两次推进（第一条 resolved 后第二次会顺手清掉刚
        弹上来的下一条，等于静默跳过提醒）。标记打在气泡对象上，正好"一只一次"。
        """
        if bubble is None or getattr(bubble, "_alert_hidden_wired", False):
            return
        try:
            bubble.hidden_signal.connect(self._on_speech_bubble_hidden)
        except (AttributeError, RuntimeError, TypeError):
            logging.debug("overlay: 气泡 hidden 信号接线失败", exc_info=True)
            return
        bubble._alert_hidden_wired = True

    def _make_bubble_follower(self, sprite):
        """按 sprite 的**自己那份**配置建一只气泡跟随器（风格/字号 per-slot）。

        跟随器把宿主窗口记进自身（原点来源 + 位置监听注册表），故必须挂在这只宠
        **当前所在屏**的窗口上——多屏下拿主宿主的窗口建跟随器，副屏那只的气泡会
        被算到主屏坐标上（锚点整体偏一整块屏）。
        """
        cfg = self._sprite_config(sprite)
        host = self.host_for_sprite(sprite)
        follower = SpriteBubbleFollower(
            host.overlay, sprite, on_clicked=self._on_bubble_clicked,
            style_id=str(cfg.get(
                "self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE)
                or DEFAULT_SELF_TALK_BUBBLE_STYLE))
        self._apply_bubble_text_scale(getattr(follower, "bubble", None), cfg)
        return follower

    def _ensure_sprite_bubble(self, sprite) -> None:
        """子 sprite 的气泡跟随器（幂等：已建即返回）。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            return
        if self._bubble_followers.get(sprite) is not None:
            return
        try:
            self._bubble_followers[sprite] = self._make_bubble_follower(sprite)
        except Exception:
            logging.debug("overlay: 子 sprite 气泡建失败", exc_info=True)

    def _close_sprite_bubble(self, sprite) -> None:
        """退出一只子 sprite：收掉它那只气泡窗（否则留孤儿顶层气泡）。"""
        follower = self._bubble_followers.pop(sprite, None)
        if follower is None:
            return
        try:
            follower.close()
        except Exception:
            logging.debug("overlay: 子 sprite 气泡收尾失败", exc_info=True)

    def _bubble_for(self, sprite):
        """该 sprite 的气泡跟随器（主 = 壳的主跟随器；子 = 各自主跟随器）。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            return getattr(self, "_bubble_follower", None)
        return self._bubble_followers.get(sprite)

    def _rebind_bubble_follower(self, sprite, host) -> None:
        """精灵换窗后把它的气泡跟随器也换到新窗（跨屏移交的唯一收口）。

        ``remove_sprite`` 会摘掉该 sprite 的全部位置监听，跟随器又缓存着旧窗原点：
        不重绑的话气泡停在旧位置、之后也不再跟宠走——实机表现就是"气泡漂在另一块
        屏"。主宠的跟随器不在 ``_bubble_followers`` 里（那是子宠表），必须经
        :meth:`_bubble_for` 取，否则主宠跨屏时这一步会被静默跳过。
        """
        follower = self._bubble_for(sprite)
        if follower is None or host is None:
            return
        overlay = host.overlay
        rebind = getattr(follower, "rebind", None)
        try:
            if callable(rebind):
                rebind(overlay, overlay.geometry().topLeft())
            else:  # 兼容只有 set_origin 的替身
                setter = getattr(follower, "set_origin", None)
                if callable(setter):
                    setter(overlay.geometry().topLeft())
        except Exception:
            logging.debug("overlay: 气泡跟随器换窗失败", exc_info=True)

    def _show_bubble_text_for(self, sprite, text: str, duration_ms: int,
                              **kwargs) -> bool:
        """把一条气泡发给**指定 sprite** 的那只气泡（主 sprite 走壳的原路径）。

        该 sprite 没有跟随器（未建/建失败/已退出）时回退主气泡——气泡是提醒/
        答复的呈现面，宁可降级到主宠头顶也不静默吞掉。
        """
        if sprite is None or sprite not in self._all_sprites():
            return False
        follower = self._bubble_for(sprite)
        if follower is None:
            return False
        # 子宠自己冒泡也要对齐可点态（G2-R1）：不能依赖主宠冒泡路径的
        # _apply_bubble_interactive 副作用，槽重建后的新跟随器同理。
        follower.set_interactive(self.quick_chat_available())
        return bool(follower.show(str(text), int(duration_ms), **kwargs))

    def _hide_bubble_for_sprite(self, sprite) -> None:
        """收起指定 sprite 的气泡（不影响其它 sprite 的气泡）。"""
        follower = self._bubble_for(sprite)
        if follower is not None:
            follower.hide()

    def _sprite_speech_visible(self, sprite) -> bool:
        """该 sprite 的"可冒泡"态：整窗可见（含全屏自动隐藏）且这只自己没被隐藏。"""
        if sprite is None or sprite not in self._all_sprites() or not self.isVisible():
            return False
        return self._sprite_visible(sprite)

    def _sprite_host(self, sprite):
        """sprite 的 pet 形宿主（余额/识屏等 host 形消费方用；主 = 壳本身）。"""
        if sprite is None:
            return self
        host = self._sprite_hosts.get(sprite)
        if host is None:
            host = _SpritePetHost(self, sprite)
            self._sprite_hosts[sprite] = host
        return host

    def sprite_presenter(self, sprite=None):
        """发起 sprite 的**代次呈现器**（手动服务结果：余额 / 天气的落点）。

        与 :meth:`_sprite_host` 的差别：它带 ``presentation_available`` 判据，让"请求
        在飞时把发起宠关掉"变成"只取消呈现"，而不是让迟到结果落到别宠/重建出来的同槽
        新住客身上。每次调用返回新的轻量对象（不缓存、不进 ``_sprite_hosts``）——它的
        代次判据来自壳的实时 sprite 清单，不需要身份共享，也就不需要新的清理点。
        自动定时余额不走这里（``QTimer.timeout → show_balance()`` 的 parent 为 None，
        仍按全局动态落点），两者语义分开（2026-10-10 终审 A10）。

        主 sprite 缺席（无宠）时返回 ``None``，调用方回退壳本身。
        """
        target = self.sprite if sprite is None else sprite
        if target is None:
            return None
        return _SpriteServicePresenter(self, target)

    def on_sprite_scale_changed(self, sprite=None) -> None:
        """sprite 缩放变更后的气泡刷新（旧机 ``window.py:1000-1004`` 等价物）。

        气泡画布尺寸/内容内边距按 ``pet_scale`` 排（思考泡泡尤甚），而缩放只发
        脏区、不发位置通知——跟随器无从自行感知，必须由改大小的那一侧显式调
        （旧机在 ``change_scale`` 后直接 ``bubble.reflow(新锚点, pet_scale=新scale)``）。
        B7b：每只 sprite 有自己的跟随器，按被改的那一只刷新（子宠不再静默跳过）。
        """
        follower = self._bubble_for(sprite)
        if follower is None:
            return
        reflow = getattr(follower, "reflow", None)
        if not callable(reflow):
            return
        try:
            reflow()
        except Exception:
            logging.debug("overlay: 气泡按新缩放重排失败", exc_info=True)

    def _refresh_sprite_bubble_style(self, sprite) -> None:
        """按该 sprite 自己那份配置重设气泡风格/文字大小 + 刷新锚点（配置热改）。"""
        follower = self._bubble_for(sprite)
        bubble = getattr(follower, "bubble", None)
        if bubble is not None:
            cfg = self._sprite_config(sprite)
            set_style = getattr(bubble, "set_style", None)
            if callable(set_style):
                set_style(str(cfg.get(
                    "self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE)
                    or DEFAULT_SELF_TALK_BUBBLE_STYLE))
            self._apply_bubble_text_scale(bubble, cfg)
        refresh_anchor = getattr(follower, "refresh_anchor", None)
        if callable(refresh_anchor):
            try:
                refresh_anchor()
            except Exception:
                logging.debug("overlay: 气泡锚点刷新失败", exc_info=True)

    def _apply_bubble_text_scale(self, bubble, cfg=None) -> None:
        """把「气泡文字大小」注入气泡控件（window.py:472-478 的 sprite 等价物）。

        getattr 守卫保留：测试替身气泡（``_BubbleStub`` 等）不实现
        ``set_text_scale``，守卫缺失会把无关用例打红。``cfg`` = 该 sprite 的
        配置来源（缺省主配置）。
        """
        setter = getattr(bubble, "set_text_scale", None)
        if not callable(setter):
            return
        source = self._config if cfg is None else cfg
        try:
            scale = float(source.get("bubble_text_scale", 100) or 100) / 100.0
            setter(max(0.5, min(3.0, scale)))
        except Exception:
            logging.debug("overlay: 气泡文字缩放注入失败", exc_info=True)

    # ---------------------------------------------------------------- B7b：per-slot 取源
    #
    # 旧架构每只桌宠是独立进程，各读自己的 config-slot-N.json；单进程壳把这条
    # 身份→配置的对应关系收敛成下面几个点：读（``_sprite_config``）、写
    # （``persist_sprite_setting``）、同步（``_apply_sprite_settings_to``）、以及
    # 子 sprite 各自的气泡/自言自语宿主。
    def _sprite_config(self, sprite):
        """一只 sprite 的配置来源（主 sprite = 主配置对象本身，子 = slot 视图）。

        子 sprite 的视图带 per-slot 快照，只在配置同步边界重建
        （``_invalidate_sprite_configs``）——点击/碰撞事件链上逐次读文件是磁盘税。
        """
        view = self._sprite_cfg_cache.get(sprite)
        if view is None:
            view = _SpriteConfig(self, sprite)
            self._sprite_cfg_cache[sprite] = view
        return view

    def _invalidate_sprite_configs(self) -> None:
        """配置同步边界：丢弃 per-slot 快照（下一次取源重新读文件）。"""
        self._sprite_cfg_cache = {}

    def _sound_sprite_for_event(self, event):
        """碰撞事件 → 参与的宠物 sprite（音效按触发 sprite 的身份配置取门/音量）。

        事件只带成员 id（``SpriteCollisionWorld._member_id``）。旧架构每只宠各自
        按自己的 cfg 播自己那一声，单进程壳里没有"每只都播一遍"的落点，取
        **参与碰撞的第一只宠物**（先 ``event.a`` 后 ``event.b``；pair 含岛等静态
        成员时自然落到宠那一只）——旧版每进程播自己那只，这里取其一。多屏下逐屏
        查全部成员（同一 id 只属于一块屏）。
        """
        ids = {getattr(event, "a", None), getattr(event, "b", None)}
        ids.discard(None)
        if not ids:
            return None
        from .sprite_collision import SpriteCollisionWorld
        for sprite in self._all_sprites():
            try:
                if SpriteCollisionWorld._member_id(sprite) in ids:
                    return sprite
            except Exception:
                continue
        return None

    def persist_sprite_setting(self, sprite, key: str, value) -> bool:
        """一只 sprite 的逐只设置落盘（旧"写本窗自己那份 config"的等价物）。

        主 sprite → 本进程主配置（``Config.set`` + ``save``，口径不变）；子 sprite
        → 它自己的 ``config-slot-N.json``（原子写单键，**绝不碰主配置**——那是另
        一只宠的身份，写脏就是审计 C1/C2 的"改子宠却改了主宠"）。
        """
        target = self.sprite if sprite is None else sprite
        name = str(key or "")
        if not name or target is None:
            return False
        slot = 0 if target is self.sprite else self._spawned_slots.get(target)
        if slot is None:
            return False
        ok = self._slot_writer(slot)(name, value)
        if ok:
            self._sprite_cfg_cache.pop(target, None)
            self._slot_signature_cache = None
        return ok

    def _stored_screen_name(self, config_dir, slot) -> str | None:
        """档案里记着的**屏归属**（没有记忆/读不到 = None；只读，绝不创建）。"""
        from . import pet_profiles

        profile = pet_profiles.read_slot_profile(config_dir, slot)
        if not profile.exists:
            return None
        name = str(profile.data.get("screen_name") or "")
        return name or None

    def _assigned_screen(self, sprite):
        """这只记着的屏归属（``screen_name`` → QScreen；没有记录/屏不在 = None）。

        先看 sprite 身上那份**待写**归属（``_remember_sprite_screen`` 记的，拖完还没
        落盘就是这么存的），再看档案。只看档案的话，刚拖过去、还没触发几何自存的那只
        会被当成"没归属"，缩放翻转重排时按当前宿主落位——等于把用户刚做的换屏吃掉。
        """
        host_screen = self._screen_under_sprite(sprite)
        pending = str(getattr(sprite, "_pending_screen_assignment", "") or "")
        if pending:
            found = self._screen_by_name(pending, prefer=host_screen)
            if found is not None:
                return found
            # 待写归属指向的屏不在线：**返回 None**，不要退回档案里的旧屏名。
            # 旧值是被这次换屏替换掉的过期意图，拿它当"归属"会让后续机械重排
            # 把宠搬到用户早就离开的那块屏，还会把 pending 一起改写掉。
            return None
        slot = self._spawned_slots.get(sprite)
        if slot is None and sprite is getattr(self, "sprite", None):
            slot = 0
        if slot is None:
            return None
        name = self._stored_screen_name(self._spawn_config_dir(), slot)
        if not name:
            return None
        return self._screen_by_name(name, prefer=host_screen)

    def _save_child_geometry(self, sprite, *, slot=None, geometry=None) -> bool:
        """这只宠的位置自存：写 ``rx``/``ry``/``facing``/``scale``（+ 待写的屏归属）。

        **比例与屏归属都属于"这只宠本该在的那块屏"**，按承载屏与归属是否一致分两路：

        - **一致**（常态：单屏用户、有意换屏之后）→ 照旧写比例；有"待写的屏归属"
          （有意换屏，见 :meth:`_remember_sprite_screen`）时与比例**同一次原子写、
          同一把目录锁**落盘；
        - **不一致 = 临时承载**（关闭跨屏时被摆在启动那块屏、或归属那块屏临时不在线
          ／被拔掉）→ **一个几何键都不写**：临时那块屏上的比例若落盘，用户重新开启
          跨屏后就回不到原来那块屏上的原位置——只保住屏名不等于保住位置。

        于是单屏普通拖动持久化不受影响（不是"关闭时一律禁写"），而"关闭／缺屏 →
        再开回来"能严格回到原来的比例。
        """
        from . import pet_profiles

        slot = self._spawned_slots.get(sprite) if slot is None else slot
        config_dir = self._spawn_config_dir()
        if slot is None or not config_dir:
            return False
        token = getattr(sprite, "_profile_generation", None)
        if token is None:
            logging.warning("overlay: slot-%s 没有已应用快照的 token，拒绝几何自存", slot)
            return False
        if self._topology_txn or self._topology_needs_reconcile():
            # **形态没达成 / 事务进行中**：两种时刻都不能落盘。
            # - 降级态（补窗失败）：窗口按单窗铺、屏按目标算，比例用哪套都可能是错
            #   的（实测会写 1.5 / 0.08）；
            # - **事务进行中**：宿主已补齐、几何还没应用那一段，`_active_multi()` 已经
            #   是多窗、精灵坐标还是旧单窗的，量出来同样越界。
            # 落盘一个越界比例会在补窗成功或重启时把宠推到屏边，比"这次没存"糟得多。
            logging.debug("overlay: slot-%s 屏拓扑尚未对齐（事务中=%s），本次几何自存跳过",
                          slot, self._topology_txn)
            return False
        pet_screen = self._screen_of_sprite(sprite)
        host_name = _screen_name(pet_screen)
        pending = getattr(sprite, "_pending_screen_assignment", None)
        assignment = pending or self._stored_screen_name(config_dir, slot)
        if assignment is not None and assignment != host_name:
            logging.debug("overlay: slot-%s 临时承载在 %s（归属 %s），本次几何自存跳过",
                          slot, host_name, assignment)
            return False
        geometry = (self._sprite_geometry(sprite, self._screen_rect(pet_screen))
                    if geometry is None else geometry)
        writes_assignment = pending is not None and bool(geometry)
        if writes_assignment:
            geometry = dict(geometry)
            geometry["screen_name"] = pending
        result = pet_profiles.write_profile_geometry(
            config_dir, slot, geometry, generation=token)
        if result.status != "ok":
            logging.warning("overlay: slot-%s 几何未保存：%s (%s)",
                            slot, result.status, result.reason)
            return False
        if writes_assignment:
            sprite._pending_screen_assignment = None
        return True

    def _apply_child_profile(self, slot) -> bool:
        sprite = self._sprite_for_slot(slot)
        if sprite is None:
            if slot == 0:
                self._sprite_cfg_cache.pop(None, None)
                self._sync_chat_surfaces(str(self._sprite_config(None).get("character")))
            return True
        self._sprite_cfg_cache.pop(sprite, None)
        cfg = self._sprite_config(sprite)
        if cfg.status not in ("ok", "memory"):
            logging.warning("overlay: slot-%s 快照未应用：%s", slot, cfg.status)
            return False
        center = QPointF(sprite.body_rect().center())
        screen = self._screen_of_sprite(sprite)
        character = str(cfg.get("character") or catalog.DEFAULT_CHARACTER)
        if character != getattr(sprite, "_profile_character", character):
            if not self._switch_child_character(sprite, character, persist=False):
                return False
            character = getattr(sprite, "_profile_character", character)
            cfg._values["character"] = character
        sprite = self._sprite_for_slot(slot)
        if sprite is None:
            return False
        self._apply_sprite_settings_to(sprite, cfg)
        sprite.facing = cfg.get("facing", "left")
        body = sprite.body_rect()
        sprite.set_pos(QPointF(sprite.pos) + center - QPointF(body.center()))
        sprite.home_screen = screen
        sprite.collision_enabled = bool(cfg.get("collision_enabled"))
        self._refresh_sprite_bubble_style(sprite)
        self._reload_self_talk_hosts([sprite])
        if slot == 0 and hasattr(self, "_self_talk_timer"):
            self._load_self_talk_settings()
            self._schedule_self_talk()
            self._sync_chat_surfaces(character)
        sprite._profile_character = character
        sprite._profile_generation = cfg.generation
        return True

    # ---- 子 sprite 的自言自语宿主（各自计时；主 sprite 仍走壳自身的机）----
    def _ensure_self_talk_host(self, sprite):
        """子 sprite 的自言自语宿主（幂等）；主 sprite/未知身份返回 None。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            return None
        host = self._self_talk_hosts.get(sprite)
        if host is None:
            host = _SpriteSelfTalkHost(self, sprite)
            self._self_talk_hosts[sprite] = host
        return host

    def _self_talk_host(self, sprite):
        """该 sprite 的自言自语宿主（主 sprite = 壳自身，兼容 host 形调用）。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            return self
        return self._self_talk_hosts.get(sprite)

    def _close_self_talk_host(self, sprite) -> None:
        host = self._self_talk_hosts.pop(sprite, None)
        if host is not None:
            host.close()

    def _schedule_self_talk_for(self, sprite, *, after_display: bool = False) -> None:
        """按 sprite 排下一次自言自语（主 = 壳的表，子 = 它自己的单发表）。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            self._schedule_self_talk(after_display=after_display)
            return
        host = self._self_talk_hosts.get(sprite)
        if host is not None:
            host.reload()
            host.schedule(after_display=after_display)

    def _stop_all_self_talk_hosts(self) -> None:
        for host in list(self._self_talk_hosts.values()):
            host.stop()

    def _resume_all_self_talk_hosts(self) -> None:
        for host in list(self._self_talk_hosts.values()):
            host.schedule()

    def _reload_self_talk_hosts(self, sprites) -> None:
        """指定子宠的 self_talk 配置热改（配置同步边界）：重读 + 重排下一次。"""
        for sprite in list(sprites):
            host = self._self_talk_hosts.get(sprite)
            if host is None:
                continue
            host.reload()
            host.schedule()

    def _init_self_talk(self) -> None:
        """自言自语状态装配 + 周期定时器（``window.py:458-480`` / ``:740`` 对齐）。

        overlay 拓扑下 PetWindow 不构造，这一族（周期气泡、点击台词、配图、
        朗读）没有别的宿主，缺这一步就整族静默失效。文本池走
        ``window_alerts.read_self_talk_texts``（host 形共享实现）；DEFAULT
        常量取 ``pet/config.py``（不在壳里依赖 ``pet.window`` 的再导出面）。
        """
        self._load_self_talk_settings()
        self._self_talk_timer = QTimer(self)
        self._self_talk_timer.setSingleShot(True)
        self._self_talk_timer.timeout.connect(self._on_self_talk_timeout)
        self._schedule_self_talk()  # 首次排程（window.py:740）

    def _load_self_talk_settings(self) -> None:
        """（重）读 self_talk 族配置字段。构造期由 _init_self_talk 调一次；
        运行期配置变更由 refresh_settings 再调——否则改开关/间隔/点击行为
        /配图目录要重启才生效（「改了没反应」会被当成 bug 报回来）。"""
        config = self._sprite_config(self.sprite)
        self._self_talk_enabled = self.sprite is not None and bool(config.get("self_talk_enabled", False))
        self._self_talk_texts = window_alerts.read_self_talk_texts(
            config.get("self_talk_texts"))
        self._self_talk_duration_seconds = max(
            1.0, min(300.0, float(config.get(
                "self_talk_duration_seconds", DEFAULT_SELF_TALK_DURATION_SECONDS))))
        self._self_talk_image_dir = str(config.get("self_talk_image_dir", "") or "")
        self._self_talk_images = list_self_talk_images(
            _resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_image_scale = max(
            0.5, min(3.0, float(config.get("self_talk_image_scale", 100)) / 100.0))
        self._self_talk_min_interval = max(
            5.0, float(config.get(
                "self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(
            self._self_talk_min_interval,
            float(config.get(
                "self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)))
        # 点击路径共享：实际显示的文本（图片气泡显式记 None，朗读据此静默）
        self._last_self_talk_text: str | None = None
        # 表达风格 picker：window_alerts.expression_style_text 惰性建，先占位
        self._expression_picker = None
        self.click_show_balance = bool(config.get("click_show_balance", False))
        self.click_show_self_talk = bool(config.get("click_show_self_talk", False))
        # 配图缓存按**签名**重建（清单 mtime/size + 目标显示尺寸），后台解码预热
        # ——GUI 只在出泡时取缓存（同步读图是 100-256ms 慢帧源，py-spy 实测）。
        # 签名没变就什么都不做：绝大多数 refresh 改的是气泡风格/间隔这类与配图
        # 无关的键，旧实现每次都清空重解全部配图（24 张 36.8MB 的 CPU+IO）。
        signature = self._self_talk_image_cache_signature()
        if (getattr(self, "_self_talk_image_cache_sig", None) == signature
                and getattr(self, "_self_talk_image_cache", None)):
            self._self_talk_images_checked_at = time.monotonic()
            return
        self._self_talk_image_cache = {}
        self._self_talk_image_cache_sig = signature
        self._self_talk_images_checked_at = time.monotonic()
        self._warm_self_talk_images()

    def _install_shared_link(self) -> None:
        """把本壳接进共享联动链（``AppShell._wire_shared_subsystems`` 的等价物）。

        共享 manager 的 ``win`` 是 ``MultiWindowProxy``，其 ``__init__`` 期
        注入 provider 时 overlay 壳还不存在（AppShell.start() 才构造），
        扇出集合为空 → provider 永远送不到。这里由壳自接一次；legacy 路径
        仍由 app.py 在每窗创建后调用，两边不重叠。
        """
        shared = getattr(getattr(self._instance, "shell", None), "_shared", None)
        if shared is None:
            return
        link = getattr(shared, "agent_link", None)
        if link is None:
            return  # agent_link 模块未激活：容器里没有管理器，无联动链可接
        try:
            shared.proxy.set_link_next_provider(link._next_busy_anim)
        except Exception:
            logging.debug("overlay: 接入共享联动链失败", exc_info=True)

    # ---------------------------------------------------------------- 4.3 后半：共享子系统呈现面
    #
    # 本段是 PetWindow 呈现面的 sprite 等价物，消费方是进程级共享子系统：
    #   - ``MultiWindowProxy``（agent_link / proactive 的 win）：读聚合状态属性
    #     （isVisible/_dragging/_physics_mode/_click_effect_phase/mouse_through/
    #     _bubble_busy_until/_bubble_suppressed/_sticky_bubble_active/
    #     _alert_current/_alert_queue/agent_link_manager）并调用呈现方法；
    #   - ``window_alerts`` 的 host 形函数：show_alert/resolve_alert/
    #     pump_alerts/clear_alerts/hide_bubble/on_speech_bubble_hidden/
    #     set_bubble_suppressed——直接复用同一份提醒队列实现（不重复造）。
    # 缺失任一方法只会让对应功能静默降级，绝不抛到调用方。
    @property
    def _speech_bubble(self):
        """**当前目标**的气泡控件（PetWindow._speech_bubble 的等价物；无气泡=None）。

        提醒当前实际承载在哪只气泡上是另一件事（见 ``_alert_bubble`` /
        ``alert_bubble``）：提醒迁移到子宠后，当前目标可能已经不是它了。
        """
        target = self.alert_target_sprite()
        return getattr(self._bubble_for(target), "bubble", None) if target is not None else None

    def _sprite_for_bubble(self, bubble):
        """该气泡属于哪只 sprite（无主 → None）。"""
        if bubble is None:
            return None
        follower = getattr(self, "_bubble_follower", None)
        if follower is not None and getattr(follower, "bubble", None) is bubble:
            return self.sprite
        for sprite, item in self._bubble_followers.items():
            if item is not None and getattr(item, "bubble", None) is bubble:
                return sprite
        return None

    def _sprite_origin(self, sprite=None) -> QPoint:
        """该 sprite 所在宿主窗口的全局原点（气泡/锚点/标记的换算源）。

        没有 sprite（无宠期）时取主宿主原点——调用方据此仍能给出稳定坐标。
        """
        host = self.host_for_sprite(sprite) if sprite is not None else self._main_host
        if host is None:
            return QPoint()
        return host.overlay.geometry().topLeft()

    def bubble_anchor_rect_for(self, bubble=None) -> QRect:
        """某只气泡的锚点矩形（全局）：按**它自己那只宠**算。

        ``window_alerts`` 的 host 形函数经这一面取锚点：提醒可以迁移到子宠
        （G2），锚点若按"当前目标"算，子宠气泡会被放到主位头顶上。给不出对象
        （气泡已随宠销毁）时回退当前目标。原点取**该宠所在屏**的宿主原点：多屏下
        拿主屏原点会把副屏那只的气泡锚到别的显示器上。
        """
        sprite = self._sprite_for_bubble(bubble)
        if sprite is None:
            sprite = self.alert_target_sprite()
        if sprite is None:
            return QRect()
        return sprite_anchor_rect_global(sprite, self._sprite_origin(sprite))

    @property
    def cfg(self):
        """PetWindow 同名的配置面：**主宠那份每宠视图**。

        host 形消费者（``MusicLyricController`` / ``window_alerts`` 的各种分支）
        直接读 ``win.cfg``。旧架构一窗一宠，``win.cfg`` 天然就是那只宠的视图；本壳
        是单例，直接回主配置会让每宠键（``self_talk_texts`` / ``click_show_self_talk``
        / ``self_talk_speak_enabled`` / ``character`` …）一律读成主配置里的缺省值
        ——设置页写进 slot 档案、运行期却看不到。实机表现：开着「点击台词朗读」，
        点击出气泡但**不朗读、也没有任何日志**（v4.3.3 用户实测 #307）。
        """
        sprite = getattr(self, "sprite", None)
        if sprite is not None:
            return self._sprite_config(sprite)
        return self._config

    @property
    def scale(self) -> float:
        """主 sprite 缩放（气泡字号/锚点的 pet_scale 来源）。"""
        return float(getattr(self.alert_target_sprite(), "scale", 1.0) or 1.0)

    def isVisible(self) -> bool:  # noqa: N802 (Qt/PetWindow 命名)
        """聚合可见性 = overlay 是否可见（隐藏/全屏自动隐藏都算不可见）。"""
        return self.alert_target_sprite() is not None

    def visible_content_rect(self) -> QRect:
        """主 sprite 身体框的全局矩形（气泡锚点；window_placement 口径的 sprite 版）。"""
        target = self.alert_target_sprite()
        if target is None:
            return QRect()
        return sprite_anchor_rect_global(target, self._sprite_origin(target))

    @property
    def _dragging(self) -> bool:
        """拖拽中（proactive G1 守卫的 interacting 判定之一）：任一屏的窗口持有 grab。"""
        return any(getattr(host.overlay, "_mouse_grab", None) is not None
                   for host in self._hosts)

    @property
    def _physics_mode(self):
        """'drag'/'throw'/None——**必须保持哨兵语义**（None = 不在物理模式）。

        消费方读 ``getattr(win, "_physics_mode", None) is not None``：返回
        ``False`` 会让 G1 恒真拦截（multi_window_shared 的同款注释记录了
        legacy 侧踩过的坑）。
        """
        state = getattr(self.sprite, "interaction_state", None)
        if state == INTERACTION_DRAG:
            return "drag"
        if state == INTERACTION_THROWN:
            return "throw"
        return None

    @property
    def _click_effect_phase(self) -> int:
        """sprite 世界没有点击效果相位（旧路径的 squash 相位计数）→ 恒 0。"""
        return 0

    @property
    def mouse_through(self) -> bool:
        """鼠标穿透态（窗口级设置逐宿主下发；任一窗口穿透即报真）。"""
        return any(bool(getattr(host.overlay, "mouse_through", False))
                   for host in self._hosts)

    @property
    def cats(self) -> dict:
        """素材分类（联动动作链按 acts 名筛选用）。失败回空字典，不抛。"""
        return self._sprite_categories(self.alert_target_sprite())

    def _sprite_categories(self, sprite) -> dict:
        """一只 sprite 的素材分类（None 或取用失败 → 空字典，不抛）。

        手动互动（点击路由）必须按**发起的那只**取素材，不能按自动提示落点取：
        点子宠时落点是可见的 slot0，用它的素材判断"有没有点击动画"会判错。
        """
        if sprite is None:
            return {}
        try:
            return dict(self.host_for_sprite(sprite).behavior._categories(sprite.library))
        except Exception:
            logging.debug("overlay: 取素材分类失败", exc_info=True)
            return {}

    @property
    def idles(self) -> list:
        return list(self.cats.get("idles", []) or [])

    @property
    def on_look_synced(self):
        """主动识屏答复同步进 AI 会话（``PetInstance.sync_look_to_chat`` 注入面）。

        无聊天变体 / 未启用聊天 → None（proactive 侧 callable 判定后静默跳过）；
        这正是 app.py ``_wire_window`` 里 ``win.on_look_synced`` 的等价注入。
        """
        if not self._chat_enabled():
            return None
        return getattr(self._instance, "sync_look_to_chat", None)

    @property
    def hidden_bubble_redirect(self):
        """桌宠隐藏时的气泡改道面（灵动岛反馈气泡；``window_alerts`` 读它）。

        可见时返回 None：改道只在隐藏期有意义，且 AppShell 的
        ``_island_feedback_bubble`` 用 ``_aggregate_pet_visible()``（读
        ``instances[].win``）判定——overlay 拓扑下恒为 False，故这里先按
        本壳自己的可见性过滤，语义与 legacy 逐条对齐。

        agent_link 侧经共享 ``MultiWindowProxy`` 读到本属性（proxy 仅在
        overlay 拓扑转发它，见 multi_window_shared）。
        """
        if self.isVisible():
            return None
        shell = getattr(self._instance, "shell", None)
        redirect = getattr(shell, "_island_feedback_bubble", None)
        return redirect if callable(redirect) else None

    # ---- 飞行期气泡门禁（B1：鱼飞行过程中不弹气泡）----
    def _sprite_in_flight(self, sprite=None) -> bool:
        """该 sprite 是否正在飞行（``sprite=None`` = 主 sprite）。"""
        target = self.sprite if sprite is None else sprite
        return target is not None and target in self._flying_sprites

    def _bubble_blocked(self, sprite=None) -> bool:
        """统一气泡门禁：设置页抑制期 **或** 该 sprite 正在飞行。

        这是 ``window_alerts.bubble_blocked(host, sprite)`` 读的那一面（壳上
        只有一个真值来源）；``_bubble_suppressed`` 仍是设置页/联动的聚合位，
        两者**不可**互相赋值——飞行集合按 sprite 判定，飞的那只禁泡、其他宠
        照常冒泡（见 ``_flying_sprites``）。
        """
        if self._bubble_suppressed:
            return True
        return self._sprite_in_flight(sprite)

    def _on_sprite_flight_changed(self, sprite, flying: bool) -> None:
        """飞行边沿（行为控制器回调）：进飞行收气泡，落地恢复提醒队列。

        进飞行：把该 sprite 记进飞行集合（此后它的一切气泡入口都读
        ``_bubble_blocked``），并**立即**收掉它当前那只气泡（复用
        ``window_alerts.set_bubble_suppressed`` True 分支的动作口径：就地
        hide）——被撞飞瞬间嘴边还挂着气泡是最显眼的穿帮。

        出飞行：先从集合摘除，主 sprite 落地时再走 ``window_alerts`` 的恢复
        分支（粘滞提醒重挂 / 限时提醒按原时长续上 → 超时后正常推进队列；该
        分支自带门禁复判，设置页仍开着时不破门）。子 sprite 落地只摘集合：
        提醒队列的呈现面是主 sprite 的气泡，子宠的飞行不该推动主宠的队列。
        """
        if flying:
            self._flying_sprites.add(sprite)
            self._hide_bubble_for_sprite(sprite)
            return
        self._flying_sprites.discard(sprite)
        if sprite is not self.sprite or self._speech_bubble is None:
            return
        window_alerts.restore_after_suppression(self)

    # ---- 气泡呈现（PetWindow.show_bubble / hold_bubble 等价）----
    def show_bubble(self, text: str, duration_ms: int = 3200,
                    subtitle: str | None = None, *, sticky: bool = False,
                    buttons: list | None = None, title_first: bool = False,
                    width_locked: bool = False, reserve: bool = True, **_ignored) -> bool:
        """向主 sprite 头顶冒泡（window.py:3850-3886 的 sprite 等价语义）。

        返回是否真上了屏：不可见/飞行期/正挂提醒让路时为 False。免打扰结束的
        汇报走 ``window_alerts.report_quiet_summary`` 的回退气泡面，它按这个
        返回值确认交付——丢弃却报成功会把暂存排空（用户什么都没看到）。
        """
        if not self.isVisible() or self._bubble_blocked():
            return False
        if self._speech_bubble is None:
            return False
        if not sticky and not buttons and self._alert_current is not None:
            return False  # 有提醒在展示：普通气泡让路，绝不覆盖审批弹窗
        if sticky or buttons:
            self._sticky_bubble_active = True
            self._sticky_text = str(text)
            self._sticky_subtitle = str(subtitle or "")
            self._sticky_buttons = list(buttons) if buttons else None
            return self._show_bubble_text(self._sticky_text, 0,
                                          subtitle=self._sticky_subtitle,
                                          sticky=True, buttons=self._sticky_buttons)
        self._apply_bubble_interactive()
        # Passive lyrics may be preempted by Agent status. Existing chat/alert
        # reservations are never cleared or shortened by a lyric refresh.
        if reserve:
            self.hold_bubble(duration_ms / 1000.0 + 2.0)
        return self._show_bubble_text(str(text), duration_ms,
                                      subtitle=str(subtitle or ""),
                                      title_first=title_first,
                                      width_locked=width_locked)

    def alert_target_sprite(self):
        if not self._any_host_visible():
            return None
        visible = [s for s in self._all_sprites() if self._sprite_visible(s)]
        return min(visible, key=lambda s: self._spawned_slots.get(s, 0), default=None)

    def _show_bubble_text(self, text: str, duration_ms: int, **kwargs) -> bool:
        target = self.alert_target_sprite()
        follower = self._bubble_for(target) if target is not None else None
        if follower is None:
            return False
        return follower.show(text, duration_ms, **kwargs)

    def hold_bubble(self, seconds: float) -> None:
        """声明重要气泡占用时长（联动/识屏气泡在此期间让路）。"""
        self._bubble_busy_until = max(
            self._bubble_busy_until, time.monotonic() + max(0.0, float(seconds)))

    def hide_bubble(self) -> None:
        """主动关闭当前气泡并推进提醒队列（window_alerts 同源实现）。

        收的是**提醒实际承载**的那只气泡（可能已随承载宠迁移）；无可见宠 /
        全隐藏时也不早退——``_alert_current`` 必须能立即清掉（不碰已销毁对象）。
        """
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.hide_bubble(self)
        self._settle_alert_bubble(stale, prev)

    def clear_alerts(self) -> None:
        """清空提醒队列并关闭当前提醒（DSH 离线/重启收口）。"""
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.clear_alerts(self)
        self._settle_alert_bubble(stale, prev)

    def show_alert(self, text: str, *, subtitle: str = "", duration_ms: int = 0,
                   buttons: list | None = None, sticky: bool = True,
                   alert_id: str = "", priority: int = 3,
                   alert_type: str = "watchdog", metadata: dict | None = None) -> None:
        """提醒入队（审批/问题/硬失败/卡住提醒；一次只展示一个）。"""
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.show_alert(
            self, text, subtitle=subtitle, duration_ms=duration_ms,
            buttons=buttons, sticky=sticky, alert_id=alert_id,
            priority=priority, alert_type=alert_type, metadata=metadata)
        self._settle_alert_bubble(stale, prev)

    def resolve_alert(self, alert_id: str) -> None:
        """按 alert_id 精确收起某条提醒（并发审批各自定位，不误关他人）。"""
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.resolve_alert(self, alert_id)
        self._settle_alert_bubble(stale, prev)

    def set_bubble_suppressed(self, suppressed: bool) -> None:
        """设置页打开期间暂停气泡（PetWindow.set_bubble_suppressed 等价）。"""
        window_alerts.set_bubble_suppressed(self, suppressed)

    def _pump_alerts(self) -> None:
        """弹出队首提醒（window_alerts 的 host 回调面）。"""
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.pump_alerts(self)
        self._settle_alert_bubble(stale, prev)

    def _settle_alert_bubble(self, stale, prev) -> None:
        """提醒收起/推进/交接收口后对齐呈现气泡（G2）。

        ``stale``/``prev`` 是操作前的呈现气泡与当前提醒：current 换成新条目
        （新事件或同 ID 升级）时按"新事件才优先主位"重记承载者，并把旧承载气泡
        上残留的那一份收掉（孤儿气泡）；current 清空时整体复位承载状态。
        最后统一走 :meth:`_sync_alert_copy` 保证"当前提醒恰好一份可见副本"。
        """
        cur = self._alert_current
        if cur is None:
            self._alert_presenter = None
            self._alert_presented_key = None
            self._alert_bubble = None
            self._dismiss_stale_bubble(stale)
            return
        if cur is not prev:
            self._note_alert_presented(cur)
            prev_id = "" if prev is None else str(prev.get("id", "") or "")
            if not (prev_id and prev_id == str(cur.get("id", "") or "")):
                # 新条目：window_alerts 刚把它呈在**当前目标**上（新事件优先主位）
                self._alert_presenter = self.alert_target_sprite()
                self._alert_bubble = self._speech_bubble
            self._dismiss_stale_bubble(stale)
        self._sync_alert_copy()

    # ---- 提醒承载宠（G2）：一条提醒一份可见副本，随承载宠迁移 ----
    def _sync_alert_copy(self, *, force: bool = False) -> None:
        """保证当前提醒**恰好一份可见副本**落在它的承载宠上（提醒生命周期唯一漏斗）。

        语义（逐条来自 G2 裁决）：
        - 承载宠仍是有效可见宠 → 副本就地保留：主位回归、别的宠显隐都**不抢回**；
          副本当前不可见（整窗隐藏/飞行期/设置页抑制）时只记"待恢复"，绝不在
          不可见期冒泡（那会漏出不该出现的顶层气泡）；``force=True`` 用于恢复
          路径（抑制解除/落地/整窗显示）：副本还在但不可见时补挂回来；
        - 承载宠退出 / 逐只隐藏 / 屏迁移（跟随器重建）→ 有其它可见宠时整条**交接**：
          同 event ID、同剩余 TTL、不重新入队、不出声，旧副本一并收掉；
        - 没有可见宠 → 不触碰任何已销毁的 QObject；current 留着待恢复，
          ``resolve`` 时立即清掉且不复活（见 ``window_alerts.resolve_alert``）。
        """
        cur = self._alert_current
        if cur is None:
            self._alert_presenter = None
            self._alert_bubble = None
            return
        presenter = self._alert_presenter
        if presenter is not None and not self._alert_presenter_usable(presenter):
            # 承载宠退出/被隐藏：旧气泡一并作废（对象可能已随它销毁，只清引用）
            presenter = None
            self._alert_presenter = None
            self._alert_bubble = None
        if presenter is None:
            presenter = self.alert_target_sprite()
        if presenter is None or not self._alert_window_visible():
            # 无可见宠 / 整窗隐藏：副本无处可挂（也不碰已销毁对象），current 留着
            self._alert_presenter = presenter
            self._alert_bubble = None
            return
        intact = self._alert_copy_intact(cur, presenter)
        if intact and not (force and not self._alert_copy_visible()):
            self._alert_presenter = presenter
            # 承载气泡的 hidden 信号必须接上：子宠气泡默认不接线，不接就没人
            # 驱动"限时提醒超时 → 清 current → 推进队列"（队列会卡死）。
            self._wire_alert_hidden(self._bubble_for(presenter))
            return
        self._present_alert_on(presenter, cur)

    def _resume_alert_copy(self) -> None:
        """抑制/飞行/整窗隐藏结束后的提醒恢复：强制按承载宠重挂那一份副本。"""
        self._sync_alert_copy(force=True)

    def _alert_presenter_usable(self, sprite) -> bool:
        """承载宠是否仍可用：还在册（没退出）且逐只可见。"""
        if sprite is None or sprite not in self._all_sprites():
            return False
        return self._sprite_visible(sprite)

    def _alert_window_visible(self) -> bool:
        """任一宿主窗口真的在屏上（提醒副本的上屏前提）。"""
        return any(host.overlay.isVisible() for host in self._hosts)

    def _alert_copy_intact(self, item, sprite) -> bool:
        """承载宠的气泡上是否仍挂着这一条（**不看可见性**：隐藏 ≠ 换承载）。

        只看"这只气泡还是不是承载宠的那只、正文还是不是这条"：气泡被换成新的
        （屏迁移/槽重建/宠退出/显隐切换）→ False（需要重挂）；仅仅被收起（隐藏）
        → True，由 hidden/300ms 防抖/恢复链路决定何时补挂。本漏斗抢着补的话，
        防抖会被绕过，"隐藏→立刻重挂"变成无界循环。
        """
        bubble = self._alert_bubble
        follower = self._bubble_for(sprite)
        if bubble is None or follower is None or follower.bubble is not bubble:
            return False
        try:
            text = str(getattr(bubble, "_raw_text", "") or "")
        except RuntimeError:  # 气泡已随它那只宠销毁
            return False
        return text == str(item.get("text", ""))

    def _alert_copy_visible(self) -> bool:
        """当前副本是否真的在屏上（对象已销毁 → False，不抛）。"""
        bubble = self._alert_bubble
        try:
            return bool(bubble is not None and bubble.isVisible())
        except RuntimeError:  # 气泡已随它那只宠销毁
            return False

    def _alert_detach_copy(self) -> None:
        """摘掉副本引用（那只气泡即将被关闭时先摘）。

        关闭动作会发 ``hidden_signal``——引用还挂着的话，那条"气泡被收起"会被
        当成用户收起提醒，立刻重挂一次（重放）。重新挂由调用方在收口后走
        :meth:`_sync_alert_copy` 决定（承载宠若还活着就用它的新气泡）。
        """
        self._alert_bubble = None

    def _note_alert_presented(self, item) -> None:
        """记下这一条**首次**呈现的时刻与总时长（限时提醒的剩余 TTL 基准）。

        键取 event 身份（有 id 用 id，无 id 用条目对象身份）：同 ID 的升级不该把
        限时提醒的 TTL 重置回整段——"迁移不重置、不永生"的判据就在这里。
        """
        key = item.get("id") or id(item)
        if self._alert_presented_key == key:
            return
        self._alert_presented_key = key
        self._alert_presented_at = float(self._alert_clock())
        self._alert_presented_ms = int(
            item.get("duration_ms") or window_alerts.DEFAULT_ALERT_DURATION_MS)

    def _alert_remaining_ms(self, item) -> int:
        """限时提醒的剩余展示时长（单调时钟；迁移不重置，走完即收口）。"""
        total = self._alert_presented_ms if self._alert_presented_key == (
            item.get("id") or id(item)) else int(
            item.get("duration_ms") or window_alerts.DEFAULT_ALERT_DURATION_MS)
        elapsed = max(0.0, float(self._alert_clock()) - float(self._alert_presented_at))
        return max(0, int(total) - int(elapsed * 1000.0))

    def _present_alert_on(self, sprite, item) -> bool:
        """把 current 挂到指定宠的气泡上（交接/恢复的唯一写入口；不出声、不入队）。"""
        follower = self._bubble_for(sprite)
        if follower is None or follower.bubble is None:
            return False
        if self._bubble_blocked(sprite):
            # 这一只正在飞行 / 设置页抑制期：不上屏，留给恢复分支
            return False
        self._note_alert_presented(item)
        sticky = bool(item.get("sticky"))
        duration_ms = 0
        if not sticky:
            duration_ms = self._alert_remaining_ms(item)
            if duration_ms <= 0:
                # 剩余 TTL 已走完（迁移路上到点）：按正常超时收口，不复活
                self._retire_alert(item)
                return False
        old = self._alert_bubble
        if old is not None and old is not follower.bubble:
            try:
                old.dismiss()          # 旧副本收掉 → 始终恰好一份
            except RuntimeError:       # 旧住客的气泡已随它的宠销毁
                pass
        try:
            follower.show(
                str(item.get("text", "")), duration_ms,
                subtitle=str(item.get("subtitle", "") or ""),
                sticky=sticky, buttons=item.get("buttons"))
        except RuntimeError:
            return False
        self._wire_alert_hidden(follower)
        self._alert_presenter = sprite
        self._alert_bubble = follower.bubble
        return True

    def _retire_alert(self, item) -> None:
        """限时提醒到期收口：清 current 并推进队列（到点即收，不靠迁移长生）。"""
        if self._alert_current is not item:
            return
        self._alert_current = None
        self._alert_presenter = None
        self._alert_presented_key = None
        self._alert_bubble = None
        self._clear_sticky_state()
        self._pump_alerts()

    def _wire_alert_hidden(self, follower) -> None:
        """把承载气泡的 hidden 信号接进提醒推进链（幂等；承载宠可能是子宠）。

        子宠气泡默认不接线（主气泡在 ``_bind_main_bubble_signals`` 接）：只有它
        真的承载过提醒时才接，并且 ``_on_speech_bubble_hidden`` 还有发送者身份
        过滤兜底——别的宠自己的气泡收起时绝不误推进/误清当前提醒。
        """
        self._connect_alert_hidden(getattr(follower, "bubble", None))

    def _dismiss_stale_bubble(self, stale) -> None:
        """收起旧承载气泡上残留的那一份（它已不是当前副本的落点）。"""
        if stale is None or stale is self._alert_bubble:
            return
        try:
            if stale.isVisible():
                stale.dismiss()
        except RuntimeError:  # 旧住客的气泡已被 Qt 销毁
            pass

    def _on_speech_bubble_hidden(self, *_args, **_kwargs) -> None:
        """气泡隐藏后的恢复/队列推进（PetWindow 同款委托）。

        两道闸门（缺一个就会重放/递归）：
        - **收口期**：承载气泡正因为宠退出/逐只隐藏/屏迁移被关掉，期间发出的
          hidden 信号不是"用户收起了提醒"，直接忽略，重挂由 ``_sync_alert_copy`` 决定；
        - **发送者身份**：只有承载气泡的隐藏才驱动队列；子宠自己的自言自语/普通
          气泡收起与提醒无关，必须早退（否则误推进、误清当前提醒）。
        """
        if self._alert_copy_teardown:
            return
        cur = self._alert_current
        if cur is not None and self._alert_bubble is not None:
            sender = self.sender()
            if sender is not None and sender is not self._alert_bubble:
                return
        stale, prev = self._alert_bubble, self._alert_current
        window_alerts.on_speech_bubble_hidden(self)
        self._settle_alert_bubble(stale, prev)

    def _clear_sticky_state(self) -> None:
        self._sticky_bubble_active = False
        self._sticky_text = ""
        self._sticky_subtitle = ""
        self._sticky_buttons = None

    # ---- 自言自语族（window.py:3686-3708 的 sprite 等价 host 面）----
    def _schedule_self_talk(self, *, after_display: bool = False) -> None:
        """排下一次自言自语（``window_alerts.schedule_self_talk`` host 形转发）。"""
        window_alerts.schedule_self_talk(self, after_display=after_display)

    def _on_self_talk_timeout(self) -> None:
        """周期定时器到点（``window_alerts.on_self_talk_timeout`` host 形转发）。"""
        window_alerts.on_self_talk_timeout(self)

    def _show_self_talk_text(self, text: str) -> bool:
        """自言自语文本落地（sprite 版，**不可**直接复用 window_alerts 同名函数）。

        差异点：``window_alerts.show_self_talk_text`` 会
        ``from .window import _set_speech_bubble_interactive``，而那个实现判的是
        ``host.on_open_quick_chat``——旧 PetWindow 的快速对话回调名。sprite 壳上
        叫 ``open_quick_chat``（见 ``_apply_bubble_interactive``），直接复用会把
        气泡每次置为「不可点」，快速对话入口静默失效。这里改用壳自己的可点态
        切换，文本交给跟随器，锚点由 follower 自算（气泡锚 sprite 身体框）。
        """
        if self._bubble_blocked():
            return False
        self._apply_bubble_interactive()
        return self._show_bubble_text(
            str(text), int(self._self_talk_duration_seconds * 1000))

    def _show_random_self_talk(self) -> bool:
        """随机一条自言自语（文本或配图）。

        抽样与出图概率复用 ``window_alerts`` 的纯逻辑函数
        （``pick_self_talk_choice`` / ``self_talk_image_chance``，无 host 依赖）；
        落地走 sprite 版气泡面（配图 = 刀 1 的 ``SpriteBubbleFollower.show_image``），
        不触碰 window 私面。
        """
        if self._bubble_blocked():
            return False
        # 审批等一直挂着的气泡优先，自言自语不覆盖（window_alerts 同款守卫）
        if self._sticky_bubble_active or self._alert_current is not None:
            return False
        # 惰性剔除运行期间被删除的图片——但 stat 是磁盘税（py-spy 实测
        # 每次自言自语 timeout 全量 is_file 一遍 = 48-56ms 慢帧），
        # 60s 才复查一次（配置热改路径会主动重置该时刻）。
        now = time.monotonic()
        if now - getattr(self, "_self_talk_images_checked_at", 0.0) > 60.0:
            self._self_talk_images_checked_at = now
            live_images = [path for path in self._self_talk_images
                           if path.is_file()]
            if len(live_images) != len(self._self_talk_images):
                self._self_talk_images = live_images
                # 清单变了：图片缓存按新清单裁剪（decode 在 worker，
                # 见 _warm_self_talk_images）
                cache = getattr(self, "_self_talk_image_cache", None)
                if isinstance(cache, dict):
                    live_keys = {str(p) for p in live_images}
                    for key in [k for k in cache if k not in live_keys]:
                        cache.pop(key, None)
        picked = window_alerts.pick_self_talk_choice(
            self._self_talk_texts, self._self_talk_images,
            window_alerts.self_talk_image_chance(self))
        if picked is None:
            return False
        kind, value = picked
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        self._apply_bubble_interactive()
        if kind == "image":
            # 图片气泡没有可朗读的文本：显式记 None，点击路径据此保持安静
            self._last_self_talk_text = None
            follower = getattr(self, "_bubble_follower", None)
            if follower is None:
                return False
            # 只走 worker 预热好的缓存（GUI 同步读图+解码是 100-256ms 慢帧
            # 源，py-spy 实测）；缓存未命中（decode 在飞/读取失败）这次先
            # 回退文本，下次命中再出图——绝不在 GUI 线程付磁盘/解码税。
            cache = getattr(self, "_self_talk_image_cache", {})
            image = cache.get(str(value))
            if image is None or image.isNull():
                self._warm_self_talk_images()
                if not self._self_talk_texts:
                    return False
                self._last_self_talk_text = self._self_talk_texts[0]
                return self._show_self_talk_text(self._last_self_talk_text)
            return follower.show_image(
                value, duration_ms, image_scale=self._self_talk_image_scale,
                pixmap=QPixmap.fromImage(image))
        self._last_self_talk_text = value
        return self._show_self_talk_text(value)

    def _self_talk_gen_now(self) -> int:
        """当前配图预热换代戳（加载 worker 的作废判据回读口）。"""
        return getattr(self, "_self_talk_image_warm_gen", 0)

    def _warm_self_talk_images(self, paths=None) -> None:
        """后台线程解码自言自语配图到 ``_self_talk_image_cache``（QImage，
        线程安全）；GUI 侧只取缓存。重复调用靠单个守护线程 + 代次去重。

        ``paths`` = 要预热的图片清单（缺省 = 主 sprite 的清单）。缓存按绝对路径
        共享：不同 sprite 的图片目录不同时天然不串号，相同图只解码一次。
        """
        cache = self._ensure_self_talk_image_cache()
        gen = self._self_talk_image_warm_gen = \
            getattr(self, "_self_talk_image_warm_gen", 0) + 1
        source = self._self_talk_images if paths is None else list(paths)
        self._start_self_talk_image_load(cache, source, gen)

    def _warm_shared_self_talk_images(self, paths) -> None:
        """预热一组图片到共享缓存，但**不动**代次计数器。

        代次机制只服务"主清单热改 → 作废在飞批次"；子宠 spawn/复活时各自的配图
        清单预热不该取消主宠在飞的解码（那会让主宠的配图自言自语多等一轮）。
        失效判定照旧按当前代次：之后主清单换代时这批照样作废。
        """
        cache = self._ensure_self_talk_image_cache()
        self._start_self_talk_image_load(
            cache, paths, getattr(self, "_self_talk_image_warm_gen", 0))

    def _ensure_self_talk_image_cache(self) -> dict:
        """配图缓存入口：默认进程级共享表（同池只解一次）；调用方显式设置的
        独立缓存（测试按需隔离）优先。"""
        global _SELF_TALK_IMAGE_CACHE_EDGE
        cache = getattr(self, "_self_talk_image_cache", None)
        if cache is not None and cache is not _SELF_TALK_IMAGE_CACHE_SHARED:
            return cache  # 显式独立缓存（测试隔离）：不动共享表
        edge = self._self_talk_image_cache_edge()
        if _SELF_TALK_IMAGE_CACHE_EDGE is not None and _SELF_TALK_IMAGE_CACHE_EDGE != edge:
            _SELF_TALK_IMAGE_CACHE_SHARED.clear()  # 目标长边变了：整表重建
        _SELF_TALK_IMAGE_CACHE_EDGE = edge
        self._self_talk_image_cache = _SELF_TALK_IMAGE_CACHE_SHARED
        return _SELF_TALK_IMAGE_CACHE_SHARED

    def _self_talk_screen_dpr(self) -> float:
        """当前屏 DPR（配图缓存按物理像素定尺寸；读不到按 1.0）。

        与 ``sprite.set_dpr`` 同源（主宿主的屏），不从 overlay 窗口取——
        QScreen 只能在 GUI 线程读，而目标尺寸在起解码线程**之前**算好。
        """
        screen = self._screen
        try:
            return float(screen.devicePixelRatio())
        except Exception:
            return 1.0

    def _self_talk_image_cache_edge(self) -> int:
        """本壳当前配置下的配图缓存目标长边（像素）。"""
        return self_talk_image_cache_edge(
            getattr(self, "_self_talk_image_scale", 1.0), self._self_talk_screen_dpr())

    def _self_talk_image_cache_signature(self) -> tuple:
        """配图缓存签名：(（路径, mtime_ns, size) 清单, 目标长边)。

        清单变了（换目录/加删图/原地替换素材）或显示尺寸变了（配图大小热改、
        换到不同 DPR 的屏）都必须重建；两者都没变时缓存直接复用——旧实现每次
        外部配置变更都清空并重新解码全部配图（24 张 36.8MB 的 CPU+IO），
        而绝大多数 refresh 改的是气泡风格/间隔这类与配图无关的键。
        """
        entries = []
        for path in self._self_talk_images:
            try:
                st = path.stat()
                entries.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                entries.append((str(path), 0, 0))  # 读不到：按占位签名参与比对
        return (tuple(entries), self._self_talk_image_cache_edge())

    def _refresh_self_talk_image_cache_for_dpr(self) -> None:
        """屏 DPR 变了（换屏/改缩放，不经过配置刷新的那条路）：签名不匹配才重建。

        目标尺寸吃 DPR，签名变了就得按新物理像素重解；DPR 没变时这里只做一次
        ``devicePixelRatio()`` 比较、连图片清单都不 stat（几何事件可能来得频繁，
        不能白刷 24 张图）。
        """
        sig = getattr(self, "_self_talk_image_cache_sig", None)
        if sig and sig[1] == self._self_talk_image_cache_edge():
            return
        if sig == self._self_talk_image_cache_signature():
            return
        _SELF_TALK_IMAGE_CACHE_SHARED.clear()  # 共享表原位清（其他壳仍持同引用）
        self._self_talk_image_cache = _SELF_TALK_IMAGE_CACHE_SHARED
        self._self_talk_image_cache_sig = self._self_talk_image_cache_signature()
        self._self_talk_images_checked_at = time.monotonic()
        self._warm_self_talk_images()

    def _cancel_self_talk_image_loads(self) -> None:
        """作废在飞的配图加载批次（stop/退出收口）：换代戳推一格，加载线程
        在下一张图前自查退出。线程是 ``threading.Thread`` 守护线程，不归 Qt
        对象树管——壳停了它们还活着，进程退出时原生解码会撞上 Qt 拆除。"""
        self._self_talk_image_warm_gen = \
            getattr(self, "_self_talk_image_warm_gen", 0) + 1

    def _start_self_talk_image_load(self, cache, source, gen) -> None:
        """起一个守护线程把这批图片解码进缓存（空批次不起线程）。"""
        pending = [str(p) for p in source if str(p) not in cache]
        if not pending:
            return
        # 目标尺寸在 GUI 线程算好（读 QScreen DPR 不能在 worker 里做），
        # 整批共用——中途换屏/改配图大小由调用方的签名重建接管。
        edge = self._self_talk_image_cache_edge()

        # 单 worker 加载队列（取代"每批一条守护线程"）：Qt 图片插件并发首用
        # 会原生崩溃（mac CI 实锤：每个崩点 dump 都有复数加载线程在场），线程
        # 攒多了又把全局锁挤爆、把活批次的预热饿死（CI 超时实锤）。单线程串行
        # = 无并发 + 无雪崩；换代戳逐图复查，作废批次零成本跳过。
        _start_self_talk_loader()
        _SELF_TALK_LOAD_Q.put((cache, pending, edge, gen, self._self_talk_gen_now))

    def _show_click_self_talk(self, click_name: str = "", sprite=None) -> bool:
        """点击自言自语（``window_alerts.show_click_self_talk`` host 形转发）。

        可安全转发：该实现只调用 ``host._show_self_talk_text`` /
        ``host._show_random_self_talk``（本类覆写的 sprite 版 / 子宠宿主版）与
        ``host.on_self_talk_speak``（朗读通道），自己不做 window 私面 import。

        ``sprite`` = 被点的那一只（B7b）：子宠走它自己的宿主（自己那份台词池/
        间隔/气泡），主宠仍走壳。
        """
        if sprite is None or sprite is getattr(self, "sprite", None):
            return bool(window_alerts.show_click_self_talk(self, click_name))
        host = self._self_talk_hosts.get(sprite)
        if host is None:
            return False
        return bool(window_alerts.show_click_self_talk(host, click_name))

    def _on_sprite_click(self, sprite, click_name: str) -> None:
        """单击 sprite 后的点击分支（``window.py:3370-3377`` 的 sprite 等价物）。

        由 ``ShellOverlayWindow.mouseReleaseEvent`` 注入调用（``click_name`` =
        本次点击实际绑定的动画名，取不到为 ""）。旧架构一窗一宠：点击音效、余额
        气泡、点击台词全部落在**被点的那一只**身上（B7b：逐 sprite 取源/落点）。
        余额优先于自言自语，二者都不开就只发声；自言自语显示成功后按
        ``after_display`` 重排**它的**周期定时器（避免与刚弹出的点击气泡叠一起）。
        """
        target = self.sprite if sprite is None else sprite
        # 每日一签：当天还没看签时冒一行「今日的签还没看」（服务内部保证一天只提示
        # 一次、开关关掉就不提示）。挂在这里而不是 click_feedback：后者不带 sprite
        # 身份、对无 sprite 的调用按契约静音（见下），而且**本方法是壳自己的方法**，
        # 首次构建与屏迁移两处接线都指向它，不会像 overlay 上的属性那样重挂漏掉。
        hint = getattr(getattr(self._instance, "shell", None),
                       "divination_hint_on_interaction", None)
        if callable(hint):
            try:
                hint()
            except Exception:
                logging.exception("抽签每日提示失败")
        # 点击音效：overlay 的 click_feedback 通道不带 sprite 身份（播放器对
        # "无 sprite"的调用已按契约静音，见 SpriteSoundPlayer.on_click），真正的
        # 播放落在这一句——门/音量/音源都取被点的那一只自己的配置。通道被置 None
        # （测试静音 / 无音效后端降级）时整条点击音效关闭。通道取**被点那只所在
        # 屏**的窗口（点击事件就是从那扇窗来的）。
        clicked_overlay = self.host_for_sprite(target).overlay
        if callable(getattr(clicked_overlay, "click_feedback", None)):
            self._sound.on_click(target)
        cfg = self._sprite_config(target)
        if bool(cfg.get("click_show_balance", False)):
            handler = getattr(getattr(self._instance, "shell", None),
                              "show_balance", None)
            if callable(handler):
                # 余额气泡锚在**被点那一只**的宿主上（主宠 = 壳本身，行为不变）
                handler(self._sprite_host(target))
        elif bool(cfg.get("click_show_self_talk", False)):
            if self._show_click_self_talk(click_name, target):
                self._schedule_self_talk_for(target, after_display=True)

    # ---- 音乐自动唱歌（M5f：window_alerts host 形函数复用 + 定时器 + 热改）----
    def _init_music_sing(self) -> None:
        """音乐检测轮询装配（``window.py:484-490`` 的 sprite 等价物）。

        overlay 拓扑下 PetWindow 不构造，``music_sing_enabled`` 此前零消费。
        检测/唱歌态/静音宽限期整体复用 ``window_alerts`` 的 host 形函数
        （``start_music_sing_polling`` / ``check_music_sing``），本壳只补
        host 面（定时器 + ``_switch`` / ``_is_one_shot_playing`` / 可见性）。
        构造期与旧机一致：开关开着就先起表（不可见时判定自身早退）。
        """
        self._music_sing_enabled = bool(self._config.get("music_sing_enabled", False))
        # 音乐模块未激活：唱歌轮询整条不建（下面就不起表；行为开关的值原样留着）
        if not feature_activation.is_active("music"):
            self._music_sing_enabled = False
        self._music_sing_active = False
        # 唱歌续播判据（window.py:2595-2601）：行为层不查音频，只问这一面；缺它
        # 则唱歌 clip 播完走掷骰链——约 60% 概率被切到随机动作，唱歌循环碎裂。
        # 接线点逐宿主一份，在 ``_wire_host``（host.behavior.sing_continue_provider）。
        self._music_sing_silent_since = None
        self._instrumental_playing = False
        timer = QTimer(self)
        # 1s 轮询：兼顾 COM 开销与「识别到音频后尽快触发」的体验（旧机同值）
        timer.setInterval(1000)
        timer.timeout.connect(self._check_music_sing)
        self._music_sing_timer = timer
        if self._music_sing_enabled:
            self._music_sing_timer.start()

    def _check_music_sing(self) -> None:
        """轮询槽（host 形转发 ``window_alerts.check_music_sing``）。"""
        window_alerts.check_music_sing(self)

    def _switch(self, name: str) -> None:
        """``PetWindow._switch`` 的 sprite 等价物（唱歌入口 ``_switch(SING_ANIM)``）。"""
        self.switch_clip(name)

    def _is_one_shot_playing(self) -> bool:
        """一次性动作/点击/移动是否在播（window.py ``_is_one_shot_playing`` 等价）。

        检测到音乐时若正在播一次性动作，先不抢绑唱歌（旧机同款守卫）。
        """
        return self._link_anim_busy()

    def set_instrumental_playing(self, on: bool) -> None:
        """纯音乐标志（``window_optional_services.py:130-139`` 的 sprite 等价物）。

        纯音乐没有可唱的句子，``window_alerts.check_music_sing`` 见标志即不唱；
        置位时同步退出唱歌态，避免"一边关一边开"打架。
        """
        self._instrumental_playing = bool(on)
        if on:
            self._music_sing_active = False
            self._music_sing_silent_since = None

    def _stop_music_sing_polling(self) -> None:
        """隐藏/退出：停轮询（``_pause_activity`` / ``aboutToQuit`` 同语义）。"""
        timer = getattr(self, "_music_sing_timer", None)
        if timer is not None:
            timer.stop()

    def sync_music_sing(self) -> None:
        """按配置（+可见性）启停轮询（window.py:3868-3875 / :1241-1242 口径）。

        - 开关关：停表并清唱歌态；
        - 开关开且可见：``start_music_sing_polling``（含立即检查一次的 singleShot）；
        - 开关开但隐藏：保持停止，恢复显示时由 ``set_pet_visible(True)`` 重启。
        """
        self._music_sing_enabled = bool(
            self._config.get("music_sing_enabled", False))
        # 音乐模块未激活：与开关关同路（停表 + 清唱歌态），不读配置以外的行为
        if not feature_activation.is_active("music"):
            self._music_sing_enabled = False
        timer = getattr(self, "_music_sing_timer", None)
        if timer is None:
            return
        if not self._music_sing_enabled:
            timer.stop()
            self._music_sing_active = False
            self._music_sing_silent_since = None
            return
        if self.isVisible():
            window_alerts.start_music_sing_polling(self)

    # ---- 音乐（歌词）宿主（window_optional_services 四件套的 sprite 等价物）----
    def install_music_lyric(self):
        """安装歌词控制器（幂等）。

        overlay 拓扑下 PetWindow 不存在，而右键菜单「音乐」子菜单与设置页
        ``music_lyric_enabled`` 都需要一个控制器宿主。控制器只消费
        ``cfg / isVisible / show_bubble / hold_bubble`` 四面，本壳全部具备，
        故逐行对齐 ``window_optional_services.install_music_lyric``。

        音乐模块未激活：返回 None，一个控制器都不构造（1s 轮询、取词线程、
        常驻 SMTC 采样线程都随之不存在）。调用方全部按 None 降级。
        """
        if not feature_activation.is_active("music"):
            return None
        if self._music_lyric is None:
            from .music.lyric_controller import MusicLyricController

            self._music_lyric = MusicLyricController(self)
        return self._music_lyric

    def sync_music_lyric(self) -> None:
        """按配置启停歌词显示（不再启用且从未装过 → 不白养定时器）。"""
        if not feature_activation.is_active("music"):
            # 音乐模块未激活：已装的控制器走既有 shutdown 收口并置 None
            self.shutdown_music_lyric()
            self._music_lyric = None
            return
        enabled = bool(self._config.get("music_lyric_enabled", False))
        if not enabled and self._music_lyric is None:
            return
        controller = self.install_music_lyric()
        controller.apply_lead()
        controller.sync_enabled(enabled)

    def pause_music_lyric(self) -> None:
        controller = getattr(self, "_music_lyric", None)
        if controller is not None:
            controller.pause()

    def shutdown_music_lyric(self) -> None:
        controller = getattr(self, "_music_lyric", None)
        if controller is not None:
            controller.shutdown()

    # ---- 看看屏幕（window.py look_at_screen 的 sprite 等价物）----
    def look_at_screen(self, sprite=None) -> None:
        """截屏 → 视觉问答 → 气泡答复（限流口径逐点对齐 window.py）。

        ``sprite`` = 本次识屏的发起者（右键命中的那一只；``None`` = 主 sprite）。
        旧版一窗一宠，答复气泡天经地义落在被点那只头顶；overlay 下先记住发起者
        （``_look_sprite``），气泡由 ``_look_bubble`` 分发到**发起者自己的**跟随器
        （B7b 接上逐 sprite 气泡后，子宠发起的识屏不再让主宠代答）。

        手动视觉模块停用：早退（不冒泡、不起 ``overlay-look-screen`` 线程）。
        """
        if not feature_activation.is_active("vision"):
            return
        target = self.sprite if sprite is None else sprite
        if target is None or target not in self._all_sprites():
            return
        if self._look_busy:
            self._show_bubble_text_for(target, "上一张还没看完呢…", 3200)
            return
        self._look_sprite = target
        now = time.monotonic()
        if now - self._last_look_ts < 4.0:
            self._look_bubble("喘口气嘛，刚看过啦…")
            return
        self._last_look_ts = now
        self._look_busy = True
        self._look_bubble("让我看看…", 6000)
        # 主线程解析快照，后台 worker 只做网络/识图（同 window.py 的线程纪律）
        settings = self._config.chat_settings()
        provider = copy.copy(settings.active_config)
        provider.api_key = self._config.resolve_api_key(provider)
        system_prompt = settings.default_system_prompt
        # 角色展示名按**发起者**的身份取（子宠可以有自己的角色）
        pet_name = self._config.character_display_name(str(
            self._sprite_config(self._look_sprite).get(
                "character", catalog.DEFAULT_CHARACTER)))
        threading.Thread(
            target=self._look_worker,
            args=(provider, system_prompt, pet_name),
            daemon=True,
            name="overlay-look-screen",
        ).start()

    def _look_worker(self, provider, system_prompt, pet_name: str = "") -> None:
        from . import vision as vision_mod

        try:
            shot = vision_mod.capture_screen_bytes()
            app_info = vision_mod.foreground_app_info()
            reply = vision_mod.ask_about_screen(
                shot, app_info, system_prompt, provider, pet_name=pet_name)
            if shiboken6.isValid(self) is False:
                return  # 壳已销毁：不再触碰信号
            user_text = f"[看看屏幕] 前台窗口：{app_info}" if app_info else "[看看屏幕]"
            self.look_done.emit(reply, user_text, False)
        except Exception as exc:
            logging.exception("看看屏幕失败")
            if shiboken6.isValid(self) is False:
                return
            self.look_done.emit(str(exc), "", True)

    def _look_bubble(self, text: str, duration_ms: int = 3200) -> None:
        """识屏气泡：落**发起者**（``_look_sprite``，右键命中的那一只）头顶。

        旧版一窗一宠，答复气泡天经地义落在被点那只头顶；B7b 接上逐 sprite 气泡
        后，这里按发起者分发（``_show_bubble_text_for``：主 = 壳，子 = 它自己那只）。
        飞行期丢弃：这条直写跟随器（不经 ``show_bubble``），不读门禁就会在
        起飞中照样冒泡——鱼飞行过程中不弹气泡是全局要求。
        """
        if self._bubble_blocked(self._look_sprite):
            return
        self._show_bubble_text_for(self._look_sprite, text, duration_ms)

    def _on_look_done(self, text: str, user_text: str, is_error: bool) -> None:
        self._look_busy = False
        if is_error:
            self._look_bubble(f"看不清啊…{text[:60]}", 5000)
            return
        self._look_bubble(text, max(4000, min(12000, len(text) * 150)))
        sync = self.on_look_synced
        if callable(sync):
            sync(user_text, text)

    # ---- 截图（快速识屏 P0 切片 3：三个产品入口的 sprite 侧接线）----
    def _screen_capture(self):
        """三个截图动作的编排件（**本壳长期持有**，菜单对象每次右键重建）。

        等价性：与 ``PetWindow`` 的三个同名入口指向**同一个**编排件实现
        （``screen_capture.actions``），差别只在宿主面——本壳的 ``_config`` /
        ``show_bubble`` / ``proactive_watcher`` / ``_instance.shell.island``。
        采集闭环自己负责隐藏/还原窗口，所以这里不需要逐 sprite 的宿主路由。
        """
        from .screen_capture.actions import actions_for

        return actions_for(self)

    @property
    def on_screen_capture(self):
        """「截图 → 框选识屏」：采集 → 覆盖层动作条选动作（提取/翻译/问答/复制）。

        手动视觉模块停用：空操作（不采集、不建编排件、不开覆盖层）——
        ``_callback_available`` 只看"可调"，闸必须在落点。
        """
        def _run() -> None:
            if not feature_activation.is_active("vision"):
                return
            self._screen_capture().run()

        return _run

    # ---- 黄金回旋（复用 golden_spin.GoldenSpinController + sprite 宿主）----
    def _golden_spin_capable(self, sprite) -> bool:
        """sprite 是否支持整帧旋转通道（假 sprite/替身没有则整族静默降级）。"""
        return callable(getattr(sprite, "set_throw_rotation", None))

    def _ensure_golden_spin(self, sprite):
        """懒建控制器（幂等）+ 选定角度目标；**换目标即结清旧的**。

        模型：控制器**全壳共享**（一条角度状态机服务所有宠，与旧架构"一窗一宠"
        下每窗一台等价），目标**独占**（同一时刻只有一只宠在转）。角度存在各
        sprite 自己身上，每拍只写给当前目标，故换目标不结清旧的，那只就会永远
        停在切换那一刻的倾斜角上（归零只发生在控制器对**当前**目标走完最后一圈）。
        菜单直连与点击直连都经本方法换目标，结清只有这一处。
        """
        if not self._golden_spin_capable(sprite):
            return None
        spin = self._golden_spin
        if spin is None:
            from .golden_spin import GoldenSpinController

            spin = GoldenSpinController(self._golden_spin_host)
            self._golden_spin = spin
        if self._golden_spin_target is not sprite:
            self._settle_golden_spin_target(spin)
        self._golden_spin_target = sprite
        return spin

    def _settle_golden_spin_target(self, spin) -> None:
        """结清当前角度目标：回正旧目标 + 让共享会话回到空闲（换目标前调用）。

        「回正」与控制器走完最后一圈的归零**同一口径**——都经 sprite 的
        ``set_throw_rotation`` 写 0（0 = 自然角），不走别的通道。顺序固定：先摘
        目标（此后宿主 ``update`` 找不到目标、不再写角），再 ``cancel`` 收掉共享
        会话（停表 + 控制器归零 + 清 pending），最后直接回正旧 sprite——"谁写 0"
        只有一个落点，不依赖 update 的时机。会话结清后 ``start()``/``spin_direct()``
        对新目标从自然角重新起转（active 拒绝只对**同一目标**的重复触发生效）。
        """
        old = self._golden_spin_target
        self._golden_spin_target = None
        if old is None:
            return
        if spin is not None:
            spin.cancel()
        reset = getattr(old, "set_throw_rotation", None)
        if callable(reset):
            reset(0.0)

    def _settle_golden_spin_if_target(self, sprite) -> None:
        """这只 sprite 就是当前回旋目标时先结清——它即将离开窗面（WP11c 相邻缺口）。

        **目标悬空**：被移除的那一只若正是当前目标，它身上的角度就再没人结清了。
        控制器（``_golden_spin``）是全壳共享的、还活着，宿主每拍仍按
        ``_golden_spin_target`` 往一只**已经不在窗里**的 sprite 写角——用户看不见，
        但它会一直写到下一次换目标（或整个会话自然结束）为止，状态挂在一个死对象上。
        结清口径与换目标那处是**同一份**（:meth:`_settle_golden_spin_target`：
        置 None → ``cancel`` → 归零；"谁写 0"只有一个落点），故这里只判条件、
        不重复实现。

        判别放在"移除前"这一侧：结清要写 0 给这只 sprite，写在它还在窗里的时候最
        直白（此后宿主已经没有目标可写）。
        """
        if self._golden_spin_target is sprite:
            self._settle_golden_spin_target(self._golden_spin)

    def _spin_direct(self, sprite) -> bool:
        """立即开转（空闲开一圈；旋转中累计一圈并让当前圈快速收尾）。

        语义 = ``GoldenSpinController.spin_direct``（golden_spin.py:75-82）：
        累计圈数与逐圈加速都在控制器内部，本壳只负责选定目标 sprite。
        """
        spin = self._ensure_golden_spin(sprite)
        if spin is None:
            return False
        spin.spin_direct()
        return True

    def trigger_golden_spin(self, sprite=None) -> None:
        """右键菜单入口：让**被点的那一只**原地逆时针转一圈（探头/彩蛋在跑时让路）。

        ``sprite=None`` = 主 sprite（兼容既有调用面）。旧 PetWindow「边缘探头激活时
        不叠加」同纪律；角度状态机/缓动直接复用 ``golden_spin`` /
        ``window_effects``，杜绝第二套数值。
        """
        target = self.sprite if sprite is None else sprite
        if target is None or not self._golden_spin_capable(target):
            return
        host = self.host_for_sprite(target)
        if getattr(host.probe, "active", False) or host.throw_egg.active:
            return
        spin = self._ensure_golden_spin(target)
        if spin is None:
            return
        spin.cancel_pending()
        spin.start()

    def _route_click_golden_spin(self, sprite) -> bool:
        """点击触发黄金回旋路由（window_optional_services.py:242-265 的 sprite 版）。

        - 直连模式（``golden_spin_direct``）或角色无点击素材：立即开转并返回
          True，调用方不再播点击反应/音效（点击已被效果层消费）；
        - armed 模式（有点击素材且未开直连）：记 pending 并返回 False，点击
          动画播完后由 ``_on_click_anim_finished`` 接续（旧机同款两段式）。
        - 开关关 / 探头激活 / sprite 不支持旋转：False（调用方走普通点击链路）。
        """
        if getattr(self.host_for_sprite(sprite).probe, "active", False):
            return False
        if not bool(self._config.get("golden_spin_on_click", False)):
            return False
        if not self._golden_spin_capable(sprite):
            return False
        spin = self._ensure_golden_spin(sprite)
        if spin is None:
            return False
        direct = bool(self._config.get("golden_spin_direct", False))
        has_clips = bool(self._sprite_categories(sprite).get("clicks"))
        if not direct and has_clips:
            spin.arm_after_click()
            return False
        spin.cancel_pending()
        spin.spin_direct()
        return True

    def _on_click_anim_finished(self) -> None:
        """点击动画自然结束（armed 回旋接续点，window.py:2533-2537 等价物）。"""
        spin = getattr(self, "_golden_spin", None)
        if spin is not None:
            spin.consume_click_finished()

    def _advance_golden_spin(self) -> None:
        """兼容面（**deprecation**）：同步驱动控制器走一帧。

        M5e 后角度状态机归 ``GoldenSpinController``（16ms 自带 QTimer）；本方法
        保留给旧调用点/测试手动推进（不等真实时钟）。
        """
        spin = getattr(self, "_golden_spin", None)
        if spin is not None:
            spin._update(time.monotonic())

    @property
    def _golden_spin_timer(self):
        """兼容面（**deprecation**）：控制器内部计时器（无控制器 → None）。"""
        spin = getattr(self, "_golden_spin", None)
        return getattr(spin, "_timer", None)

    @property
    def _golden_spin_started(self) -> float:
        """兼容面（**deprecation**）：当前圈的起算时刻（控制器私有字段映射）。"""
        spin = getattr(self, "_golden_spin", None)
        return float(getattr(spin, "_rev_started_at", 0.0))

    @_golden_spin_started.setter
    def _golden_spin_started(self, value: float) -> None:
        spin = getattr(self, "_golden_spin", None)
        if spin is not None:
            spin._rev_started_at = float(value)

    # ---- 联动动作（agent_link 的 request_link_* 落点）----
    def _link_anim_busy(self) -> bool:
        """一次性动作（动作池/点击/移动）是否在播——联动请求不打断它。"""
        try:
            target = self.alert_target_sprite()
            if target is None:
                return False
            return (self.host_for_sprite(target).behavior.state_of(target)
                    in _LINK_ONESHOT_STATES)
        except Exception:
            logging.debug("overlay: 读行为状态失败", exc_info=True)
            return False

    def switch_clip(self, name: str, link_request: bool = False) -> bool:
        """播放指定动画（window.switch_clip 语义：一次性，播完回掷骰链）。"""
        target = self.alert_target_sprite()
        if target is None or not str(name or ""):
            return False
        try:
            return bool(self.host_for_sprite(target).behavior.play_once(
                target, str(name)))
        except Exception:
            logging.debug("overlay: 切换动画失败 %s", name, exc_info=True)
            return False

    def request_link_anim(self, name: str) -> None:
        """Agent 联动动作请求：一次性动作播放中不打断，存为待播（最新覆盖旧的）。"""
        self.mark_activity()
        name = str(name or "")
        if not name:
            return
        self._pending_link_anim = name
        if not self._link_anim_busy():
            self._play_pending_link_anim()

    def _play_pending_link_anim(self) -> None:
        name, self._pending_link_anim = self._pending_link_anim, None
        if not name:
            return
        self._link_anim_current = name
        self.switch_clip(name)

    def request_link_idle(self) -> None:
        """Agent 回到空闲：取消待播联动；一次性动作让它播完自然回待机。"""
        self._pending_link_anim = None
        self._link_anim_current = None
        if self._link_anim_busy():
            return
        idles = self.idles
        if idles:
            self.switch_clip(self._pick_idle(idles))

    def _pick_idle(self, idles: list) -> str:
        rng = getattr(getattr(self, "behavior", None), "rng", None)
        chooser = getattr(rng, "choice", None)
        if callable(chooser):
            try:
                return str(chooser(list(idles)))
            except Exception:
                logging.debug("overlay: 随机待机取素材失败", exc_info=True)
        return str(idles[0])

    def set_link_next_provider(self, provider) -> None:
        """注入联动动作链「下一个动作」提供者（``_LinkAnimChain`` 消费）。"""
        self._link_next_provider = provider

    def clear_pending_link_anim(self) -> None:
        self._pending_link_anim = None

    def mark_activity(self) -> None:
        """用户/联动活跃锚点。

        overlay 路径尚无闲置降帧门（D9 未落地项），本方法保留调用面以承接
        agent_link 的事件语义，等降帧刀落地后在此接真。
        """

    # ---- 快速对话（气泡可点 → 锚定 sprite 的输入气泡）----
    def _chat_enabled(self) -> bool:
        """进程级聊天开关（E2 单源 = AppShell.enable_chat，经 PetInstance 转发）。"""
        return bool(getattr(self._instance, "enable_chat", True))

    def quick_chat_available(self) -> bool:
        """快速对话是否可用（未启用聊天 / 无聊天打包变体 → False）。"""
        return self._quick_chat_class() is not None

    def _quick_chat_class(self):
        """解析 ``QuickChatBubble``（结果缓存；不可用时 None，**绝不抛**）。

        呈现面纪律：入口探测失败一律降级——气泡/联动链路不能因为一个可选
        外围模块而中断；真故障仍由 ``_import_quick_chat`` 的 log 暴露。
        """
        if not self._chat_enabled():
            return None
        if not self._quick_chat_resolved:
            self._quick_chat_resolved = True
            try:
                self._quick_chat_cls = _import_quick_chat()
            except Exception:
                logging.exception("overlay: 快速对话模块导入失败，入口静默降级")
                self._quick_chat_cls = None
            else:
                if self._quick_chat_cls is None:
                    logging.info("overlay: 无聊天变体，快速对话入口静默降级")
        return self._quick_chat_cls

    def _apply_bubble_interactive(self) -> None:
        """按快速对话可用性切换气泡可点（旧路径 ``_set_speech_bubble_interactive``）。"""
        followers = [getattr(self, "_bubble_follower", None), *self._bubble_followers.values()]
        for follower in followers:
            if follower is not None:
                follower.set_interactive(self.quick_chat_available())

    def _on_bubble_clicked(self, sprite) -> None:
        """气泡主体点击 → 快速对话（``window.py:3419`` 语义：提醒/交互气泡 no-op）。"""
        if self._sticky_bubble_active or self._alert_current is not None:
            return
        self.open_quick_chat(sprite)

    def open_quick_chat(self, sprite=None) -> bool:
        """打开快速对话输入气泡，锚定被点 sprite；回车发送走既有 chat 链路。

        返回是否真的打开（无聊天变体/构造失败 → False，静默降级）。
        """
        cls = self._quick_chat_class()
        if cls is None:
            return False
        target = self.sprite if sprite is None else sprite
        if target is None or target not in self._all_sprites():
            return False
        anchor = _SpriteChatAnchor(self, target)
        try:
            bubble = self._quick_chat
            if bubble is None:
                bubble = cls(self._sprite_config(self.sprite), pet_window=anchor)
                bubble.open_chat_callback = self.open_full_chat
                self._quick_chat = bubble
            else:
                bubble.settings = self._config.chat_settings()
                bubble.refresh_session()
            bubble.show_for_pet(anchor)
            return True
        except Exception:
            logging.exception("overlay: 打开快速对话失败")
            return False

    def select_slot(self, slot) -> bool:
        target = self._sprite_for_slot(slot)
        self._selected_slot = slot
        self._selected_sprite = target
        if target is None:
            return False
        self.set_sprite_visible(target, True)
        self.set_pet_visible(True)
        # 抬到前面的必须是**这只所在屏**的窗口（多屏下抬主宿主窗口等于抬错屏）
        overlay = self.host_for_sprite(target).overlay
        overlay.raise_()
        overlay.activateWindow()
        return True

    def open_full_chat(self) -> None:
        """打开完整聊天窗（快速对话气泡的「全文见聊天窗」入口）。"""
        opener = getattr(self._instance, "open_chat", None)
        if not callable(opener):
            return
        try:
            opener()
        except Exception:
            logging.exception("overlay: 打开完整聊天窗失败")

    def _close_quick_chat(self) -> None:
        """收起并释放快速对话气泡（stop/aboutToQuit 收口）。"""
        bubble, self._quick_chat = self._quick_chat, None
        if bubble is None:
            return
        try:
            bubble.close()
        except Exception:
            logging.debug("overlay: 收起快速对话失败", exc_info=True)

    # ---- 显隐：共享子系统 pause/resume（window.py:1214-1250 语义）----
    def _pause_shared_subsystems(self) -> None:
        """隐藏 → proactive.pause + agent_link.pause（岛反馈面可用时不停联动）。

        共享实例（overlay 拓扑恒为 SharedSubsystems）的 ``pause`` 是刻意的
        no-op——单窗显隐不该停进程级监视器，G1 守卫逐 tick 读 ``isVisible()``
        拦下截图（限流器状态因此不丢）。本方法保留调用面与 legacy 逐条对齐。
        """
        proactive = getattr(self, "proactive_watcher", None)
        if proactive is not None:
            try:
                proactive.pause()
            except Exception:
                logging.debug("overlay: 暂停主动识屏失败", exc_info=True)
        manager = getattr(self, "agent_link_manager", None)
        if manager is None:
            return
        if self._island_feedback_available():
            return  # 隐藏期间岛是交互面，联动事件仍需驱动岛反馈气泡
        try:
            manager.pause()
        except Exception:
            logging.debug("overlay: 暂停联动监视器失败", exc_info=True)

    def _resume_shared_subsystems(self) -> None:
        """恢复显示 → proactive.resume + agent_link.resume（按最新配置重评估）。"""
        proactive = getattr(self, "proactive_watcher", None)
        if proactive is not None:
            try:
                proactive.resume()
            except Exception:
                logging.debug("overlay: 恢复主动识屏失败", exc_info=True)
        manager = getattr(self, "agent_link_manager", None)
        if manager is not None:
            try:
                manager.resume()
            except Exception:
                logging.debug("overlay: 恢复联动监视器失败", exc_info=True)

    def _island_feedback_available(self) -> bool:
        """岛反馈面是否可用（隐藏期气泡改道 + 联动是否暂停的判定探针）。"""
        shell = getattr(self._instance, "shell", None)
        probe = getattr(shell, "_island_feedback_available", None)
        if not callable(probe):
            return False
        try:
            return bool(probe())
        except Exception:
            logging.debug("overlay: 岛反馈面探测失败", exc_info=True)
            return False

    # ---------------------------------------------------------------- 灵动岛碰撞桥（4.3）
    def attach_island(self, island) -> None:
        """岛创建/重建/启停后接线（AppShell._sync_dynamic_island 调用）。

        幂等可重复调；island=None 即摘桥。墙挂**岛所在屏**那个宿主的碰撞世界与
        窗口（同 DPI 单窗下就是唯一宿主；混合缩放一屏一窗，岛跨屏时由
        ``_rebind_island_host`` 重绑）。

        ``kinetic`` 接 ``self.driver.note_kinetic``（M-1）：桌宠静止时 tick 已降到
        T2/T3（不跑仿真），而岛拖拽是几何回调驱动的用户输入、不经过 overlay 的
        鼠标事件（岛是独立顶层窗）——没有这条通道，快速甩岛过鱼可能整个手势
        期间一次碰撞结算都没发生。driver 未起（测试宿主/启动早期）时不强接。
        ``on_sync`` 接 ``_rebind_island_host``：岛换屏时把墙换到新宿主的世界上。
        """
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            bridge.close()
            self.island_bridge = None
        self._island = island
        if island is None:
            self._island_host = None
            return
        host = self._island_screen_host(island)
        driver = getattr(self, "driver", None)
        kinetic = getattr(driver, "note_kinetic", None)
        from .island.bridge import IslandWindowBridge
        self.island_bridge = IslandWindowBridge(
            island, host.collision, host.overlay, sound=self._sound,
            kinetic=kinetic if callable(kinetic) else None,
            on_sync=self._rebind_island_host)
        self._island_host = host

    def _island_screen_host(self, island):
        """岛所在屏的宿主：岛是独立顶层窗，有**自己**的屏。

        混合缩放下一块屏一张合成窗，每张窗一份碰撞世界与局部坐标原点（见
        ``_multi_window``）。墙绑错宿主 = 岛在副屏而墙在主屏的世界里：副屏那只
        宠在另一份世界/坐标系里，直接穿过岛（拖岛拍鱼也不产生碰撞事件）。

        岛左上角跨在两块屏之间的缝上时只绑**左上角所在那屏**（保守口径：墙只在
        那一侧生效，不做跨缝的双世界映射——岛与宠不是同一生命周期，宁可少一堵
        也不凭空多一堵）。

        岛自己的选屏口径（``DynamicIsland._current_screen``，与夹取/停靠同一处
        判据）优先，其次 Qt 顶层窗的 ``screen()``；两者都没有的岛替身回落到主宿主。
        """
        screen = None
        probe = getattr(island, "_current_screen", None)
        if callable(probe):
            screen = probe()
        if screen is None:
            probe = getattr(island, "screen", None)
            if callable(probe):
                screen = probe()
        host = self.host_for_screen(screen) if screen is not None else None
        return host if host is not None else self._main_host

    def _rebind_island_host(self) -> None:
        """岛换屏 → 墙换到新屏的宿主（碰撞世界与局部原点一起换）。

        岛桥每次几何/显隐同步前回调本方法（``IslandWindowBridge`` 的 ``on_sync``
        口）：岛移动、跨屏拖拽、被夹回屏内、换主屏都会发几何回调，故"屏变化"没有
        第二条通知面。宠的跨窗移交**不**走这里——岛是独立顶层窗，它的屏与原点只
        认它自己，不随宠的生命周期换。
        """
        island = getattr(self, "_island", None)
        bridge = getattr(self, "island_bridge", None)
        if island is None or bridge is None:
            return
        host = self._island_screen_host(island)
        if host is None or host is getattr(self, "_island_host", None):
            return
        # **不按 ``_multi_window()`` 早退**：拔掉混合缩放的副屏后只剩一块屏，
        # 多窗塌回单窗，但桥此刻可能仍绑在被拆宿主的世界上（拔屏到岛下次移动
        # 之间，宠撞不到岛）。是否重绑只看"目标宿主是不是当前绑的那个"。
        self._island_host = host  # rebind 内部会再回调一次本方法：先记账才不递归
        bridge.rebind(host.collision, host.overlay)

    # ---------------------------------------------------------------- 生命周期
    def _warm_sound_effects_once(self) -> None:
        """首次 start 时预热音效后端（口径 = legacy ``app.py:535-539``）。

        同一预热函数、同一组开关（``click_sound_enabled`` OR
        ``collision_sound_enabled``）与同一 pack/data_dir：overlay 拓扑没有
        PetWindow 走 ``_build_window``，缺这段接线时本进程第一次发声要现付
        winmm ``waveOutOpen`` 冷启动（实测 159.6ms，落在 GUI 事件循环里：
        ``.scratch/arch-ab/CTL-COLL-SOUND-NEW-10-REPORT.md``）。预热池是进程级
        的，故只做一次：重 show / stop→start 都不重复；开关都关时不预热，不
        无谓拉起池。异常纪律同 legacy：预热函数自身已对音频失败静默降级，这里
        不额外 catch，未知错误照旧抛出（不吞真故障）。
        """
        if self._sound_warmed:
            return
        self._sound_warmed = True
        if not (self._config.get("click_sound_enabled", True)
                or self._config.get("collision_sound_enabled", True)):
            return
        click_sound.warm_click_sound_effects(
            self._config.get("click_sound_pack"),
            data_dir=getattr(self._config, "dir", None),
        )

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopped = False
        self._warm_sound_effects_once()  # 首次可见前预热（避免首次发声卡事件循环）
        self._show_all_hosts()
        # 构造期复活/生成的 sprite 在窗口可见前被压成暂停（见 _spawn_slot 的
        # 有效可播判定）：显示后必须补放行，否则子宠永久冻在首帧。逐只被藏起
        # 的仍在恢复侧被跳过（与整窗显隐同一条判据）。
        self._set_all_clips_paused(False)
        self._sync_runtime_marker()  # D7：设置进程避让
        self.app.installEventFilter(self)  # Esc 全局兜底（弹弓取消）
        if self.tray is not None:
            self.tray.show()
            self._arm_tray_icon_refresh()
        # 音乐（歌词）：配置开着才装/启（默认关 → 一行不跑，与 legacy 同纪律）
        self.sync_music_lyric()
        # M5f：音乐自动唱歌轮询（可见即按开关恢复；隐藏期间保持停止）
        self.sync_music_sing()

    def _show_all_hosts(self) -> None:
        """全部宿主窗口显示 + 起播（整组显隐的唯一入口；进程级驱动器共享）。"""
        for host in self._hosts:
            host.overlay.show()
            host.overlay.start()

    def _hide_all_hosts(self) -> None:
        """全部宿主窗口隐藏（整窗显隐/全屏避让；与 ``_show_all_hosts`` 成对）。"""
        for host in self._hosts:
            host.overlay.hide()

    def _any_host_visible(self) -> bool:
        """是否至少有一张宿主窗口在屏上（整组显隐的判据）。"""
        return any(host.overlay.isVisible() for host in self._hosts)

    def _stop_all_hosts(self) -> None:
        """停表（驱动器是进程级的一个表，停任一成员等于停整组——按契约的收口
        路径只有这里与 ``stop()``）。"""
        driver = self.driver
        if driver is not None:
            driver.stop()

    def _close_all_hosts(self) -> None:
        """关闭全部宿主窗口（退出收口；逐个 close，单个失败不阻断其余）。"""
        for host in list(self._hosts):
            try:
                host.overlay.close()
            except RuntimeError:
                continue
            except Exception:
                logging.exception("overlay: 关闭宿主窗口失败")

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt 命名)
        """应用级 Esc 兜底：overlay 是防抢焦点窗（WS_EX_NOACTIVATE），
        keyPressEvent 只在确有焦点时收到 Esc；弹弓瞄准中按 Esc 全局取消。

        F-PERF 快路径：本过滤器注册在 QApplication 上（``start()`` 的
        ``installEventFilter``），110Hz 重绘 + 定时器 + 输入让它每秒被调用
        数千次（实机 py-spy：GUI 线程 9.4%），而其中只有「KeyPress + Esc +
        正在瞄准」这条路要动手。故：

        - 非 KeyPress 第一行直接 ``return False``——不构造 ``super()`` 的
          C++ 往返（``QObject.eventFilter`` 基类实现恒返回 False，Qt 文档
          明载 "The default implementation always returns false"，故语义
          逐位等价，见 tests/test_overlay_shell_event_filter.py）；
        - 不消费的 KeyPress 同样就地返回 False；
        - 消费分支（瞄准中 Esc）逐位保留原行为：取消会话 + 返回 True 吞掉
          事件。**目标是会话的 owner 窗**（多窗时输入可能接在副屏那张窗上）：
          取消只改会话状态，源窗的 grab/按下状态必须由它自己收，否则 Esc 之后
          那只窗仍扣着 grab，随后的松手会被当成一次普通点击。
        """
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.key() != Qt.Key.Key_Escape:
            return False
        overlay = getattr(self, "overlay", None)
        if overlay is None:
            return False
        slingshot = overlay.slingshot
        if not slingshot.aiming:
            return False
        (slingshot.owner or overlay)._cancel_slingshot(resume_drag=False)
        return True

    def _run_exit_step(self, label: str, step) -> None:
        """退出收口的一步一隔离（缺陷 13）：单步异常只记日志，不中断后续步骤。

        ``stop()`` / ``_on_about_to_quit()`` 原是一条裸调用链——靠前的可抛步骤
        （``shutdown_music_lyric`` 内部是无守卫的 ``timer.stop()`` / ``bridge.close()``
        / ``overlay.stop()``）一旦抛错，位置持久化与在飞供给取消就被整体跳过：
        配置不落盘、在飞帧序列线程无人取消（库随壳析构活线程 = Qt fatal）。
        """
        try:
            step()
        except Exception:
            logging.exception("overlay: 退出收口步骤失败（%s）", label)

    def _shutdown_library(self, lib, label) -> None:
        """显式关闭一个素材库（缺陷 14：主库与子宠库同一收口）。

        ``MovieLibrary.shutdown`` 自带幂等与内部收口（``pause_warm`` + 预热责任
        交接 + 在飞供给取消 + 逐个 clip 的 ``close``/``stop``）；库没有
        ``shutdown`` 面时退回只停预热（旧行为）。单库失败不阻断其余收口。
        """
        if lib is None:
            return
        shutdown = getattr(lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
                return
            except Exception:
                logging.exception("overlay: 退出时素材库收尾失败 (label=%r)", label)
        pause = getattr(lib, "pause_warm", None)
        if callable(pause):
            try:
                pause()
            except Exception:
                logging.exception("overlay: 退出时暂停预热失败 (label=%r)", label)

    def _shutdown_libraries(self) -> None:
        """关闭主库与全部子宠库（两处退出路径同一口径，缺陷 14）。"""
        self._shutdown_library(getattr(self, "lib", None), "主肥鱼")
        for sprite, lib in list(getattr(self, "_spawned_libs", {}).items()):
            self._shutdown_library(lib, sprite)

    def _close_bubble_followers(self) -> None:
        """收掉气泡跟随器（主气泡 + 逐 sprite 的子气泡）。

        单只跟随器收尾失败不阻断其余（逐对象容错口径同
        ``_cancel_frameseq_provisions``）。
        """
        follower = getattr(self, "_bubble_follower", None)
        if follower is not None:
            follower.close()
        for follower in list(self._bubble_followers.values()):
            try:
                follower.close()
            except Exception:
                logging.debug("overlay: 停机时子宠气泡收尾失败", exc_info=True)
        self._bubble_followers = {}

    # ---------------------------------------------------------------- 测试收口
    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """逐测试收口所有活跃壳（conftest 防线；MovieLibrary 同范式）。

        壳把 tick 驱动器、自言自语计时、监视器、配图加载线程与素材库带进共享
        QApplication；测试不收口就漂到后续用例与进程退出（全量套件/macOS CI
        的漂移段错误累积源）。逐壳 ``stop()``（幂等、逐步隔离），未 start 的
        壳靠外层顶层窗口清扫回收。
        """
        for shell in list(_LIVE_OVERLAY_SHELLS):
            try:
                shell.stop()
            except Exception:
                logging.getLogger(__name__).debug(
                    "测试收口 OverlayShell 失败", exc_info=True)
            # stop() 对未 start 的壳早退——但它们的配图预热批次照样在加载队列里
            # 排队，不戳换代戳就会整批解完（队列积压把活批次的预热饿死，CI 实测
            # 超时）。收口时无条件作废在飞批次。
            try:
                shell._cancel_self_talk_image_loads()
            except Exception:
                pass

    def stop(self) -> None:
        """幂等：重复 stop 是 no-op。

        退出收口逐步隔离（缺陷 13）：每一步独立 try/except，单步抛错只记日志。
        """
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._started = False
        recovery = getattr(self, "_recovery_window", None)
        if recovery is not None:
            recovery.close()
        self._run_exit_step('移除应用级事件过滤器',
                            lambda: self.app.removeEventFilter(self))
        timer = getattr(self, "_self_talk_timer", None)
        if timer is not None:
            # M9：stop 后不再自我重排（引用环也让壳可被回收）
            self._run_exit_step('停自我重排定时器', timer.stop)
        # B7b：子宠各自的计时同样停表
        self._run_exit_step('停止子宠自言自语计时', self._stop_all_self_talk_hosts)
        self._run_exit_step('摘设置命令监听', self._teardown_settings_command_watch)
        # 配图加载守护线程不归 Qt 管（threading.Thread）：换代戳推一格，在飞
        # 批次下一张图前自查作废——否则壳已停、线程还在往壳的缓存里写，进程
        # 退出窗口里就是原生解码撞上对象拆除（macOS CI 三连崩的实锤形态之一）。
        self._run_exit_step('作废在飞配图加载', self._cancel_self_talk_image_loads)
        self._run_exit_step('关闭歌词控制器', self.shutdown_music_lyric)
        self._run_exit_step('停音乐唱歌轮询', self._stop_music_sing_polling)
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            self._run_exit_step('关闭灵动岛桥', bridge.close)
            self.island_bridge = None
        self._run_exit_step('停止可见性监视器', self._watcher.stop)
        self._run_exit_step('删除运行标记', self._delete_runtime_marker)
        self._run_exit_step('关闭快速对话', self._close_quick_chat)
        self._run_exit_step('收掉气泡跟随器', self._close_bubble_followers)
        self._run_exit_step('停 overlay 驱动', self._stop_all_hosts)
        self._run_exit_step('关闭 overlay 窗口', self._close_all_hosts)
        if self.tray is not None:
            self._run_exit_step('隐藏托盘图标', self.tray.hide)
        # F3 + 缺陷 14：在飞帧序列供给线程与素材库（主库 + 子宠库）的收口（口径
        # 同 _on_about_to_quit / _on_session_end）。缺这两步时库不 shutdown，而
        # 库里活着的供给 QThread 是库的子对象——库随壳销毁会把活线程一起析构
        # （Qt fatal，见 _on_about_to_quit 的说明）。
        self._run_exit_step('取消在飞帧序列供给', self._cancel_frameseq_provisions)
        self._shutdown_libraries()

    def _on_about_to_quit(self) -> None:
        """退出收口：停 tick + 暂停预热 + 监视器与避让标记清理（4.1b）。

        逐步隔离（缺陷 13）：每一步独立 try/except，前置步骤抛错不得让**必做项**
        （窗口位置落盘 / 子宠位置落盘 / 在飞供给取消）被整体跳过。
        """
        self._run_exit_step('停止可见性监视器', self._watcher.stop)
        watcher = getattr(self, "_session_watcher", None)
        unregister = getattr(watcher, "unregister_session_notifications", None)
        if callable(unregister):
            # 锁屏通知注册与窗口句柄同生共死（O4）
            self._run_exit_step('反注册锁屏通知', unregister)
        # Qt 的过滤器表只存裸指针：退出时必须摘掉，绝不给它留一个即将析构的对象
        uninstall = getattr(watcher, "uninstall", None)
        if callable(uninstall):
            self._run_exit_step('摘除会话结束原生过滤器', uninstall)
        self._run_exit_step('摘设置命令监听', self._teardown_settings_command_watch)
        self._run_exit_step('删除运行标记', self._delete_runtime_marker)
        self._run_exit_step('关闭快速对话', self._close_quick_chat)
        self._run_exit_step('关闭歌词控制器', self.shutdown_music_lyric)
        self._run_exit_step('停音乐唱歌轮询', self._stop_music_sing_polling)
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            self._run_exit_step('关闭灵动岛桥', bridge.close)
            self.island_bridge = None
        # 4.2a：退出持久化（rx/ry/facing/scale）+ 4.2b：子肥鱼逐只按 slot 身份持久化
        self._run_exit_step('持久化窗口位置', self.save_position)
        self._run_exit_step('持久化子宠位置', self.save_spawned_positions)
        self._run_exit_step('停 overlay 驱动', self._stop_all_hosts)
        # 主库显式 shutdown（缺陷 14）：shutdown 内部已含 pause_warm 与在飞供给
        # 取消；无 shutdown 面的库退回只停预热（旧行为）。
        self._shutdown_library(getattr(self, "lib", None), "主肥鱼")
        # 首跑供给线程不是预热线程：``pause_warm`` 管不到它，而它自己派生转换
        # ffmpeg（issue #111 的关机/退出窗口里最不该有的派生），库随本壳销毁时
        # 活线程还会被一起析构（Qt fatal）。逐库显式取消（口径同 _on_session_end
        # 的逐库 stop_all_clips）。
        self._run_exit_step('取消在飞帧序列供给', self._cancel_frameseq_provisions)

    def _cancel_frameseq_provisions(self) -> None:
        """取消本路径各库的在飞帧序列供给线程（退出/会话结束收口，issue #111）。

        每只子肥鱼各持独立库（M8），故逐个来；单库取消失败（半销毁/已收尾）不得
        阻断其余库，与 ``_on_session_end`` 的逐库收口同口径。库侧自带失效语义
        （幂等 + 有界等待 + 超时孤儿兜底，见
        ``MovieLibrary.cancel_frameseq_provision``），这里只负责遍历。
        """
        libs = [self.lib] + [lib for lib in getattr(self, "_spawned_libs", {}).values()]
        for lib in libs:
            cancel = getattr(lib, "cancel_frameseq_provision", None)
            if not callable(cancel):
                continue
            try:
                cancel()
            except Exception:
                logging.exception("overlay: 取消帧序列供给线程失败")

    # ---------------------------------------------------------------- 托盘（多宠聚合）
    def _build_tray(self) -> None:
        """建单托盘 + 双击显隐；菜单本体交给 ``_refresh_tray_menu`` 聚合重建。

        对齐 legacy（app.py:3171-3309）：进程级**单**托盘，多宠时逐只子菜单；
        图标/双击接线只做一次，菜单随生灭刷新（托盘菜单是快照，不重建会把已
        退出的身份留在菜单里）。

        ``try`` 只包托盘构造本身（疑似缺口 C）：建菜单阶段任何异常都只记日志，
        已建好的托盘照常 ``show()``——菜单失败不该让用户「托盘里完全没有条目」。
        """
        try:
            tray = QSystemTrayIcon(self._tray_icon(), self)
        except Exception:
            logging.exception("overlay: 创建托盘失败")
            self.tray = None
            self._ensure_recovery_entry(force=True)
            return
        tray.activated.connect(
            lambda reason: self._toggle_pet_visible()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None)
        tray.setToolTip("dsh-pet (overlay)")
        # 菜单本体强引用保活（app.py F5 教训：PySide6 wrapper 回收后
        # contextMenu 会命中失效 wrapper）
        self.tray = tray
        try:
            self._refresh_tray_menu()
        except Exception:
            logging.exception("overlay: 重建托盘菜单失败（托盘保留）")
        self._ensure_recovery_entry()

    def announce_slot0_profile_problem(self, diagnosis: str) -> bool:
        """把 slot0 档案降级的诊断说给用户（含路径与显式恢复入口）。

        返回是否真的上了屏。**只陈述与指路**：不代用户生成、不碰坏档案（原文件保留）。
        存放一份供恢复窗/托盘提示复用——无托盘的恢复窗是此时唯一的可见入口。
        """
        self._slot0_profile_diagnosis = str(diagnosis)
        label = getattr(self, "_recovery_diagnosis_label", None)
        if label is not None:
            try:
                label.setText(str(diagnosis))
                # 标签恒建（见 ``_ensure_recovery_entry``）：先建窗后出事时它是隐藏的，
                # 补文案必须一起放出来，否则恢复窗开着却一句解释都没有。
                label.setVisible(True)
            except RuntimeError:
                pass
        text = f"{diagnosis}\n可用托盘/恢复窗的「生成 / 恢复 slot0」再试。"
        if self.show_bubble(text, duration_ms=9000):
            return True
        return False

    def slot0_profile_diagnosis(self) -> str:
        """slot0 降级诊断（空串 = 正常）；供托盘 tooltip / 恢复窗展示。"""
        own = str(getattr(self, "_slot0_profile_diagnosis", "") or "")
        if own:
            return own
        shell = getattr(self._instance, "shell", None)
        reader = getattr(shell, "slot0_profile_diagnosis", None)
        if callable(reader):
            try:
                return str(reader() or "")
            except Exception:
                logging.debug("overlay: 读取 slot0 降级诊断失败", exc_info=True)
        return ""

    def _ensure_recovery_entry(self, force=False):
        if not force and QSystemTrayIcon.isSystemTrayAvailable():
            return
        from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout, QPushButton

        recovery = getattr(self, "_recovery_window", None)
        if recovery is None:
            recovery = QWidget()
            recovery.setWindowTitle("桌宠恢复")
            layout = QVBoxLayout(recovery)
            # 诊断标签**恒建**（空文本时隐藏）：无托盘时这里是唯一可见入口，坏档案的
            # 事实与路径必须在这里说清楚（否则用户只看到"主桌宠不见了"）；运行期再
            # 出的事故（生成失败等）也复用同一个标签，见 ``_show_recovery_diagnosis``。
            note = QLabel(self.slot0_profile_diagnosis())
            note.setWordWrap(True)
            note.setObjectName("recoveryDiagnosis")
            note.setVisible(bool(note.text()))
            layout.addWidget(note)
            self._recovery_diagnosis_label = note
            for label, callback in (("生成 / 恢复桌宠", self.spawn_pet),
                                    ("打开配置目录", self.open_config_dir),
                                    ("slot0 设置", self.open_settings_for),
                                    ("退出应用", self.app.quit)):
                button = QPushButton(label)
                button.clicked.connect(callback)
                layout.addWidget(button)
            self._recovery_window = recovery
        recovery.show()

    def open_config_dir(self, *_ignored) -> bool:
        """打开 config 目录（恢复窗的"去修档案"入口，2026-10-10 ops D2-新）。

        本批次不做自动修复，也不代用户备份/重建坏档案（规范："不覆盖、不自动修复"），
        所以能给的补偿是**把路指清楚**：说明里点名文件路径，这里把它所在的目录打开，
        用户自己用编辑器改回去。返回是否真的交出去了（取不到目录/系统不认 = False）。
        """
        config_dir = self._spawn_config_dir() or getattr(self._config, "dir", None)
        if config_dir is None:
            logging.warning("overlay: 打开配置目录失败（取不到 config 目录）")
            return False
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        opened = bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir))))
        if not opened:
            logging.warning("overlay: 打开配置目录失败：%s", config_dir)
        return opened

    def _refresh_tray_menu(self) -> None:
        """重建聚合托盘菜单：进程级动作 + 逐只子菜单（legacy 单托盘多窗语义）。

        逐条对照 legacy（app.py:3203-3301，见 PR 报告）：
        - 顶层「显示 / 隐藏」「回到右下角」= legacy 主窗两项（overlay 单窗，
          显隐天然覆盖全部 sprite）；
        - 「生小肥鱼 / 退出子肥鱼」= overlay 多宠生命周期入口（legacy 在右键
          菜单/设置页，不重复造第二个实现，直接复用壳的两个公开方法）；
        - **只要 ≥1 只**就有逐只子菜单「主肥鱼 / 小肥鱼 [slot-N]」= legacy 每窗
          子菜单，含「回到右下角」「退出这只」（见 :meth:`_exit_pet_from_tray`）；
          「显示这只」（per-sprite visible，M14）只在 ≥2 只时挂——逐只显隐回答的
          是"这几只里藏哪只"，只剩一只时顶层「显示 / 隐藏」已经按整窗语义给过；
          顶层「显示 / 隐藏」仍是整窗语义（overlay 单窗，全显全隐）。
          **单宠也必须挂**：关闭宠物 ≠ 退出应用（规范 §一/§四），只在 ≥2 只时
          才建逐只菜单，等于让单宠用户的"关闭"只剩顶层「退出」（= 退出整个应用）
          一条路——实测发现时就是这样（见 .scratch/product-close-approved-gui-
          20261011/REPORT.md §5）。0 只时不建（没有"这只"可指，也就不会有
          指向 None 的条目）；恢复入口是下面那条「生成 / 恢复 slot0」；
        - 「桌宠设置」按主身份路由（D13）、「退出」= app.quit；
        - 「鼠标穿透」「开机自启」= legacy 托盘后段两项（穿透只有托盘/设置页能取消；
          自启勾选态按系统登录项实况，弹出前同步）。

        **派发契约**（与 legacy 托盘 `3356d67` 同口径）：会动窗面/冒泡/系统登录项
        的条目一律走 ``add_tray_action`` / ``add_tray_toggle``——菜单先关，回调延到
        菜单真的不在之后再执行。macOS 的托盘菜单是 AppKit 原生 NSMenu 跟踪会话，
        会话中新建/激活窗口会被抑制（issue #4 家族：点了没反应）；勾选项的副作用
        挂 ``triggered`` 而不是 ``toggled``，弹出前那次程序化 ``setChecked`` 就不会
        顺手重写配置/登录项。本壳是 overlay 拓扑下用户**实际看到**的托盘。
        """
        if self.tray is None:
            return

        def add_tray_toggle(target: QMenu, label: str, checked, apply):
            """可勾选托盘条目：勾选态即时翻转，副作用延到菜单关闭后执行。

            与 ``app.py::_build_tray`` 的同型助手逐位一致（那里是嵌套函数，导不出来）。
            ``add_tray_action`` 的回调在菜单关闭后执行，此时 ``isChecked()`` 已是用户
            点出来的最终值——故 apply 收勾选态，而不是在连接时把它绑死。
            """
            action = add_tray_action(
                target, label, lambda: apply(bool(action.isChecked())))
            action.setCheckable(True)
            action.setChecked(bool(checked))
            return action

        menu = QMenu()
        # 气泡是置顶 Tool 窗口（层级高于菜单）：弹出前先隐藏（legacy 同款）
        menu.aboutToShow.connect(self._hide_bubble_for_menu)
        # 弹出前同步逐只勾选态（外部改过 sprite.visible 也能反映）
        menu.aboutToShow.connect(self._sync_tray_pet_visibility)
        # 本段无一条是纯记账：显隐要动合成窗、归位要动 sprite 位置、生/退桌宠要动
        # sprite 与各自库（还可能冒泡），全按上面的派发契约走。
        add_tray_action(menu, "显示 / 隐藏", self._toggle_pet_visible)
        add_tray_action(menu, "回到右下角",
                        lambda: self._go_default_corner(self.sprite))
        menu.addSeparator()
        add_tray_action(menu, "生成 / 恢复 slot0（档案损坏，见日志）"
                        if self.slot0_profile_diagnosis() and self.sprite is None
                        else ("生成 / 恢复 slot0" if self.sprite is None else "生小肥鱼"),
                        self.spawn_pet)
        add_tray_action(menu, "退出子肥鱼", self.clear_spawned_pets)
        # tooltip 默认不显示（QMenu 的默认值），而这条关闭入口的 tooltip 正是
        # "档案还在、怎么弄回来"的说明，必须能看见。
        menu.setToolTipsVisible(True)
        visible_actions: list = []
        entries = self._pet_entries()
        if entries:
            menu.addSeparator()
            # 逐只显隐只在"≥2 只"时有意义：它回答的是"这几只里藏哪只"。只剩一只时
            # 藏它 = 整窗没有内容可画，而那条路顶层「显示 / 隐藏」已经按整窗语义
            # 给过了（拆成"藏这一只"只会多出一个等价但更弱的状态，且主宠即宿主
            # 合成窗——SpriteMenuFacade.hide() 记的正是这个口径）。
            # 关闭入口不受此限：单宠缺的从来不是显隐，而是"关掉它但别退出应用"。
            per_pet_visibility = len(entries) > 1
            for sprite, label in entries:
                # 子菜单也是同一个原生跟踪会话的一部分，会话中做这些同样会被抑制：
                # 子项与顶层同族（同一批窗面/显隐状态），一律延迟派发。
                # 关闭观测目标由 ``add_tray_action`` 统一取整棵树（``shared._menu_tree``）：
                # 子菜单关掉 ≠ 会话结束，根菜单还开着就还在同一次原生跟踪里。
                sub = menu.addMenu(label)
                add_tray_action(sub, "回到右下角",
                                lambda s=sprite: self._go_default_corner(s))
                if per_pet_visibility:
                    toggle = add_tray_toggle(
                        sub, "显示这只", self._sprite_visible(sprite),
                        lambda want, s=sprite: self.set_sprite_visible(s, want))
                    visible_actions.append((sprite, toggle))
                close = add_tray_action(sub, "退出这只",
                                        lambda s=sprite: self._exit_pet_from_tray(s))
                close.setToolTip(
                    "关闭这只桌宠：档案与位置保留，可用托盘「生成 / 恢复 slot0」再恢复；"
                    "不退出应用。"
                    if sprite is self.sprite else
                    "关闭这只桌宠：它自己的档案保留，可用托盘「生小肥鱼」再生成；"
                    "不影响其它桌宠。")
        self._pet_visible_actions = visible_actions
        menu.addSeparator()
        # 与 legacy 托盘同口径：菜单先关、回调延到菜单关闭后执行（原生菜单跟踪
        # 会话里开窗/冒泡会被 AppKit 抑制，见 shared.run_after_menu_closed）。
        add_tray_action(menu, "桌宠设置",
                        lambda: self.open_settings_for(self.sprite))
        # legacy 托盘两项（app.py:3333-3343）：鼠标穿透的取消入口只有托盘/设置页，
        # 开着时桌宠点不动；开机自启此前只有右键菜单有勾选项。两项都写配置、写系统
        # 登录项，按派发契约走勾选版助手。
        mouse_through = add_tray_toggle(
            menu, "鼠标穿透", bool(self._config.get("mouse_through", False)),
            self._on_user_through_changed)
        menu.addSeparator()
        autostart = add_tray_toggle(
            menu, "开机自启", self._autostart_enabled(), self._set_autostart_from_tray)
        # 弹出前同步勾选态（设置页/右键菜单改过的不刷新会过期，legacy 同款）。
        # 只改看相、不触发副作用：副作用挂 triggered，程序化 setChecked 不发它。
        menu.aboutToShow.connect(
            lambda: self._sync_tray_toggle_checks(mouse_through, autostart))
        menu.addSeparator()
        # 「退出」刻意不延迟：它不是窗面动作，而是结束本进程（会话中被抑制的对象
        # 在退出路径上不存在），legacy 托盘同样是裸 addAction——两边同判断。
        menu.addAction("退出", self.app.quit)
        old = self._tray_menu
        self.tray.setContextMenu(menu)  # 新菜单先接管，旧菜单才允许释放（F5）
        self._tray_menu = menu
        # QAction wrapper 一并保活：子菜单的 menuAction 挂在父菜单 actions 里，
        # wrapper 被回收会让整棵菜单被 PySide6 判为已删除（app.py:3319 同因）
        snapshot = list(menu.actions())
        for act in list(snapshot):
            sub = act.menu()
            if sub is not None:
                snapshot.extend(sub.actions())
        self._tray_actions = snapshot
        if old is not None and old is not menu:
            old.deleteLater()

    def _autostart_enabled(self) -> bool:
        """系统当前是否已注册开机自启（legacy ``autostart_mod.is_enabled()`` 口径）。"""
        try:
            return bool(autostart_mod.is_enabled())
        except Exception:
            logging.debug("overlay: 读取开机自启状态失败", exc_info=True)
            return False

    def _set_autostart_from_tray(self, enabled: bool) -> None:
        """托盘「开机自启」→ 实例的 ``_set_autostart(enabled, 壳)``。

        写系统登录项的实现在 AppShell（``app.py:_set_autostart``，覆盖三平台），
        壳只做转发：失败时它用 ``win.show_bubble`` 提示，overlay 下 ``win`` 就是
        本壳（具 ``show_bubble`` 宿主面）。取不到该方法（测试替身/精简装配）时
        静默降级，绝不因托盘一项拖垮菜单。
        """
        setter = getattr(self._instance, "_set_autostart", None)
        if not callable(setter):
            logging.debug("overlay: 实例无 _set_autostart，托盘项跳过")
            return
        try:
            setter(bool(enabled), self)
        except Exception:
            logging.exception("overlay: 托盘写入开机自启失败")

    def _sync_tray_toggle_checks(self, mouse_through, autostart) -> None:
        """托盘弹出前同步两个勾选项（legacy ``sync_tray_checks`` 口径）。

        设置页/右键菜单改过的开关若不刷新，托盘里的勾选态会过期（用户会以为
        「点了没反应」）。
        """
        pairs = ((mouse_through, bool(self._config.get("mouse_through", False))),
                 (autostart, self._autostart_enabled()))
        for action, value in pairs:
            try:
                action.setChecked(bool(value))
            except RuntimeError:
                continue  # 菜单已销毁（wrapper 失效）：跳过

    def _pet_entries(self) -> list:
        """托盘逐只条目：(sprite, 标签)。主宠在前 = 活跃清单列表头口径。"""
        entries = [(self.sprite, "主肥鱼 [slot-0]")] if self.sprite is not None else []
        for sprite in self._spawned:
            slot = self._spawned_slots.get(sprite)
            label = f"小肥鱼 [slot-{slot}]" if slot is not None else "小肥鱼"
            entries.append((sprite, label))
        return entries

    def _exit_pet_from_tray(self, sprite) -> None:
        """菜单关闭后按捕获sprite走同一可逆关闭契约；过期目标为空操作。"""
        from .sprite_menu_facade import SpriteMenuFacade

        SpriteMenuFacade(self, sprite).on_exit_pet()

    def _go_default_corner(self, sprite) -> None:
        """把指定 sprite 送回右下角（window.go_default_corner 等价）。

        落位前先取消该 sprite 的探头会话（legacy ``window._go_default_corner`` 同
        语义）。会话残留时：角色保持 ±45° 探头姿态停在右下角，且下一次点击触发的
        拉直过渡会把 x 写回**旧屏缘**（``_apply_pose`` 的 ``probe_target_x``）——
        表现为「点一下瞬移回原来贴的那条边」。托盘主宠/子宠与设置重置三个入口都走
        本方法，故一处收口即同惠。
        """
        host = self.host_for_sprite(sprite)
        if getattr(host.probe, "active", False):
            host.probe.cancel(sprite, "return_corner")
        sprite.set_pos(self._default_corner_pos(
            self._screen_rect(self._screen_of_sprite(sprite)), sprite.rect()))

    def _hide_bubble_for_menu(self) -> None:
        """托盘菜单弹出前隐藏气泡（legacy menu.aboutToShow → hide_speech_bubble）。

        气泡是置顶 Tool 窗口，**每只 sprite 各一只**（B7b），故逐只收起。
        """
        followers = [getattr(self, "_bubble_follower", None)]
        followers.extend(list(self._bubble_followers.values()))
        for follower in followers:
            if follower is not None:
                follower.hide()

    def _tray_placeholder_icon(self):
        """首帧未就绪时的托盘占位图标 = legacy 同款宠物爪印（app.py:3127）。

        不用系统 ``SP_ComputerIcon``：首帧未就绪期间（首跑转码/慢启动）用户在
        托盘里看到的会是「电脑」图标而不是桌宠图标，观感等同「托盘栏里没有
        桌宠图标」。刻意不传本壳当主题色宿主——托盘观感与窗口 QSS 无关
        （legacy ``_tray_placeholder_icon`` 同款口径）。
        """
        from .context_menus.icons import vector_menu_icon
        return vector_menu_icon(QWidget(), "pet", 64)

    def _tray_icon(self):
        """托盘图标取鱼本体当前帧（idle 首帧兜底）；取不到回退爪印占位图标。"""
        pm = self.icon_pixmap(64)
        if pm is not None:
            return QIcon(pm)
        return self._tray_placeholder_icon()

    def _arm_tray_icon_refresh(self) -> None:
        """首帧就绪后把托盘图标从占位换成角色头像。

        legacy 同款修复（app.py:3132-3148：占位图标 + frame_ready 换头像）的
        sprite 壳等价物——sprite 没有 frame_ready 信号，用 500ms 短轮询。
        必须有这一步：托盘图标只在建造时取一次，而 ``_build_tray`` 跑在
        ``overlay.show()`` 之前，此刻 sprite 首帧尚未上屏、clip 也未起播，
        ``icon_pixmap`` 只能回退系统占位图标，不刷新就永远显示不出鱼。
        """
        if self.tray is None:
            return
        if self._refresh_tray_icon_once():
            return
        self._tray_icon_retries = 0
        timer = QTimer(self)
        timer.setInterval(_TRAY_ICON_POLL_MS)
        timer.timeout.connect(self._poll_tray_icon)
        self._tray_icon_timer = timer
        timer.start()

    def _poll_tray_icon(self) -> None:
        """轮询槽：拿到帧即换头像并停表；超预算只降频（5s）继续等，绝不放弃。

        legacy 用无时限的 ``frame_ready``（app.py:3185-3192 + window.py:347）
        换图；sprite 侧没有该信号，若照旧在 20 拍（10s）后永久停表，首帧晚于
        10s（首跑帧序列转码、慢启动、内存压力）就会让托盘**整个进程生命期**
        停在占位图标——正是用户报的「托盘栏里没有桌宠图标」。降频而非保频是
        为了不常驻 500ms 高频表。
        """
        self._tray_icon_retries += 1
        if self._refresh_tray_icon_once():
            self._stop_tray_icon_poll()
            return
        if (self._tray_icon_retries >= _TRAY_ICON_POLL_BUDGET
                and self._tray_icon_timer.interval() != _TRAY_ICON_SLOW_POLL_MS):
            self._tray_icon_timer.setInterval(_TRAY_ICON_SLOW_POLL_MS)

    def _stop_tray_icon_poll(self) -> None:
        timer = getattr(self, "_tray_icon_timer", None)
        if timer is not None:
            timer.stop()

    def _refresh_tray_icon_once(self) -> bool:
        """能拿到角色帧就把托盘图标换成它；返回是否已换（拿到帧）。"""
        pm = self.icon_pixmap(64)
        if pm is None or self.tray is None:
            return False
        self.tray.setIcon(QIcon(pm))
        return True

    # ---------------------------------------------------------------- 图标面
    def _idle_first_frame_pixmap(self):
        """idle 首帧位图（``_tray_icon`` 原有取图链）；取不到返回 ``None``。

        ``lib.movie()`` 惰性建 clip：未起播的 clip ``currentPixmap()`` 为空 →
        ``None``，由调用方决定兜底（托盘回退爪印占位图标 / 岛稍后重试）。
        """
        try:
            cats = catalog.build_categories(
                self.lib.names(), self.lib.manifest,
                self.lib.folder_map, self.lib.folder_files)
            idle = cats["idles"][0] if cats["idles"] else None
            pm = self.lib.movie(idle).currentPixmap() if idle else None
        except Exception:
            return None
        if pm is None or pm.isNull():
            return None
        return QPixmap(pm)

    def icon_pixmap(self, size: int = 64):
        """主 sprite 当前帧图标（裁透明留白 + 缩放）；帧未就绪返回 ``None``。

        窗口版 ``PetWindow.icon_pixmap`` 的 sprite 等价物，两个消费方共用：

        - 灵动岛头像 provider（app.py ``_island_icon_pixmap``）：``None`` = 帧未
          就绪，岛侧按 ``dynamic_island._icon_pixmap`` 的契约稍后重试（不缓存 None）；
        - 托盘图标 ``_tray_icon``：拿不到才回退爪印占位图标。

        取图顺序：sprite 当前帧 → idle 首帧（``_tray_icon`` 旧逻辑原样保留）。
        """
        pm = _sprite_current_pixmap(self.sprite)
        if pm is None:
            pm = self._idle_first_frame_pixmap()
        if pm is None:
            return None
        return _crop_icon_pixmap(pm, size)

    # ---------------------------------------------------------------- 4.1b 窗口能力
    def refresh_settings(self) -> None:
        """外部配置变更应用点（AppShell._apply_external_config_change 扇出）。"""
        self._apply_window_capabilities()
        # self_talk 族热改：重读字段并按新口径重排程（schedule_self_talk
        # 先停表再按 enabled 早退，开/关/改间隔都收敛到这一条路径）。
        self._load_self_talk_settings()
        self._schedule_self_talk()
        # The visible presenter may be any slot; style and text size belong to
        # each pet, never to the process-wide configuration.
        for sprite in self._all_sprites():
            self._refresh_sprite_bubble_style(sprite)
        self.sync_music_lyric()
        # M5f：音乐自动唱歌开关热改（开→启轮询，关→停表并退出唱歌态）
        self.sync_music_sing()

    def _apply_window_capabilities(self) -> None:
        """按当前配置应用窗口能力（4.1b parity）：on_top / 穿透复合 /
        全屏与光标监视器门 + 逐 sprite 设置。

        窗口级设置逐宿主下发（``_apply_host_window_caps``）：**每一块屏的窗口**都要
        带上同一组配置，否则副屏那张 overlay 会保持默认置顶/不穿透/不锁位（用户改
        设置只在主屏生效）。
        """
        self._user_mouse_through = bool(self._config.get("mouse_through", False))
        self._apply_effective_mouse_through()
        self.set_on_top(bool(self._config.get("on_top", True)), persist=False)
        for host in self._hosts:
            self._apply_host_window_caps(host)
        self._sync_sprite_settings()
        self._watcher.set_fullscreen_enabled(
            bool(self._config.get("auto_hide_fullscreen", True)))
        self._watcher.set_cursor_enabled(
            bool(self._config.get("cursor_hidden_passthrough", True)))
        # 预测预热提前量（config predict_prewarm_lead_ms，范围 200-600，0=关）
        try:
            lead_ms = int(self._config.get("predict_prewarm_lead_ms", 350) or 350)
        except (TypeError, ValueError):
            lead_ms = 350
        lead_ms = max(0, min(600, lead_ms))
        for host in self._hosts:
            host.behavior._predict_lead_s = lead_ms / 1000.0
            host.behavior.predict_enabled = lead_ms > 0

    def _apply_host_window_caps(self, host) -> None:
        """一张宿主窗口的窗口级配置：置顶/穿透/锁位/不透明度。

        整组刷新（``_apply_window_capabilities``）与**运行期新建的屏**（第一次被占用）
        都走这一处——漏下发哪一项，那张窗就停在"出厂默认"（副屏的宠盖住桌面、
        锁定位在该窗不起作用）。置顶经 ``set_on_top`` 下发（它是"逐宿主写同一值"的
        入口，同值早退，循环调用是幂等的）。
        """
        overlay = host.overlay
        self.set_on_top(bool(self._config.get("on_top", True)), persist=False)
        overlay.mouse_through = bool(
            self._user_mouse_through or self._auto_cursor_hidden)
        # M5a/M5b：拖拽闸门（锁定位 / SHIFT 门）推到 overlay——命中按下不进
        # 拖拽 grab，点击语义保留（legacy window.py:3126-3133 / :3172-3181）
        overlay.lock_position = bool(self._config.get("lock_position", False))
        overlay.shift_drag_required = bool(self._config.get("shift_drag", False))
        # M5c：整窗不透明度（overlay 单窗 = legacy 单宠窗口语义）
        overlay.apply_opacity(self._pet_opacity_percent())

    def _pet_opacity_percent(self) -> int:
        """``pet_opacity`` 配置读数（10-100 钳制，口径同 window.py:3853-3857）。"""
        try:
            value = int(float(self._config.get("pet_opacity", 100)))
        except (TypeError, ValueError):
            value = 100
        return max(10, min(100, value))

    def set_on_top(self, on: bool, *, persist: bool = True) -> None:
        """窗口置顶（window.py set_on_top 等价）：逐宿主窗口一起改。

        同一组配置对每一块屏的窗口都生效；同值早退避免 ``setWindowFlag`` 重建
        原生窗口带来的可见闪烁。
        """
        on = bool(on)
        changed = False
        for host in self._hosts:
            overlay = host.overlay
            current = bool(overlay.windowFlags()
                           & Qt.WindowType.WindowStaysOnTopHint)
            if on == current:
                continue
            changed = True
            was_visible = overlay.isVisible()
            overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
            if was_visible:
                overlay.show()  # setWindowFlag 重建原生窗口会先隐藏
            # 原生窗口重建后旧 HWND 的锁屏通知注册随之失效，按新句柄补注册
            self._register_session_notifications(host)
        if not changed:
            # 同值早退（防御性）：setWindowFlag 会重建原生窗口并先隐藏，
            # 重复调用（refresh_settings 每次都走这里）会造成可见闪烁。
            return
        if persist:
            self._config.set("on_top", on)
            save = getattr(self._config, "save", None)
            if callable(save):
                save()

    def _sync_sprite_settings(self) -> None:
        """config → sprite/behavior 同步点（legacy ``window.py``
        ``refresh_pet_settings`` 的等价物）：启动、``refresh_settings`` 与
        spawn 后各走一次。

        缺了它（DS 全量审查 M6）：``drag_physics``/``throw_strength``/
        ``no_move``/``playback_speed`` 只在右键菜单里能改——启动不读 config
        （上次保存的重启即丢）、设置页改了没反应；且 sprite 侧默认值
        （``drag_physics=True``、``throw_speed_cap=MAX_THROW_SPEED=6000``）与
        config 默认（``False``、standard=4800）**相反**，开箱行为就不一致。

        B7b：逐 sprite **各取自己那份配置**（主 = 主配置，子 = 各自的
        ``config-slot-N.json``，缺键取同源默认）——旧架构每窗读自己的 cfg，
        本方法此前一律推主设置（审计 C1 的核心缺口）。
        """
        cfg = self._config
        # 碰撞物理 4 键（设置页「碰撞参数（高级）」）：旧架构每只宠各读自己
        # 的 cfg 组策略并在逐次求解时使用（collision_client.py:140-145 /
        # :558-563）；世界侧 solve_multi_body_collision 每次 tick 现读这组
        # 属性（sprite_collision.py:291-293 / :429 / :455），故运行期赋值即生效。
        # 世界只有一份（一次只有一个合成窗），故这 4 键按主配置**全局**生效。
        self._sync_collision_physics(cfg)
        self._invalidate_sprite_configs()  # 配置同步边界：per-slot 快照重读
        for sprite in list(self._all_sprites()):
            slot = self._spawned_slots.get(sprite)
            if slot is None:
                self._apply_sprite_settings_to(sprite)
            else:
                self._apply_child_profile(slot)
        self._sync_sprite_collision_flags()  # G4：逐 sprite 资格位（见该方法）
        # 窗口级行为设置（不移动 / 动画间隔）逐宿主下发：每块屏的宠共用同一条
        # 掷骰链策略
        for host in self._hosts:
            host.behavior.no_move = bool(cfg.get("no_move", False))
            # M5d：动画间隔（动作/移动播完后的强制氛围步）同步到控制器；
            # 改 0 由 setter 立即取消在跑的 gap
            try:
                gap = max(0.0, min(
                    3600.0, float(cfg.get("animation_gap_seconds", 0.0) or 0.0)))
            except (TypeError, ValueError):
                gap = 0.0
            host.behavior.animation_gap_seconds = gap
        # 刚刚按 slot 配置重算过资格位 → 同步刷新 stat 签名缓存，下一次目录事件
        # 才能准确判断"slot 配置到底有没有变"（只 stat，不读内容）。
        self._slot_signature_cache = self._slot_config_signatures()

    def _apply_sprite_settings_to(self, sprite, cfg=None) -> None:
        """把**一只 sprite 自己那份**设置下发到它的运行态。

        ``_sync_sprite_settings`` 的全量循环与"某个 slot 文件变了"的窄路径共用
        这一处：per-slot 键（见 :data:`PER_SLOT_SETTING_KEYS`）逐 sprite 取源，
        缺键取同源默认。窗口级键（no_move / animation_gap / on_top / 透明…）不在
        此列——它们只有一份窗，仍由壳全局下发。
        """
        if sprite is None:
            return
        cfg = self._sprite_config(sprite) if cfg is None else cfg
        sprite.throw_speed_cap = throw_speed_cap(cfg.get("throw_strength"))
        sprite.drag_physics = bool(cfg.get("drag_physics", False))
        # 静止放手下落（#219）：与 drag_physics 平级，逐 sprite 取自己那份配置
        sprite.drop_gravity = bool(cfg.get("drop_gravity", False))
        # 桌宠大小（scale）：旧机 refresh_pet_settings 即时 change_scale
        # （window.py:3937-3938），改完即改气泡画布/锚点。
        scale = self._config_scale(cfg)
        if abs(float(getattr(sprite, "scale", scale)) - scale) > 1e-9:
            try:
                sprite.scale = scale
            except (TypeError, ValueError):
                logging.debug("overlay: scale 应用失败", exc_info=True)
            else:
                self.on_sprite_scale_changed(sprite)
        # ffmpeg 圈边界回收阈值（config 缺省 10；0=关闭）：与 playback_speed 同点
        # 同步。归一/夹取在 webm_clip.set_recycle_minutes，这里只透传配置值。
        recycle_minutes = cfg.get("ffmpeg_recycle_minutes", 10)
        sprite.ffmpeg_recycle_minutes = recycle_minutes
        clip = getattr(sprite, "_clip", None)
        push = getattr(clip, "set_recycle_minutes", None)
        if callable(push):
            push(recycle_minutes)
        try:
            playback = max(0.25, min(3.0, float(cfg.get("playback_speed", 1.0) or 1.0)))
        except (TypeError, ValueError):
            playback = 1.0
        # 已绑定的 clip 热改即生效：只推参数，不重绑/不重启播放（旧 refresh
        # 的 `:3984-3985` 同款），缺该方法的 clip 跳过。
        if float(getattr(sprite, "playback_speed", 1.0)) != playback:
            sprite.playback_speed = playback
            setter = getattr(clip, "set_playback_speed", None)
            if callable(setter):
                setter(playback)

    def _config_scale(self, cfg=None) -> float:
        """config ``scale``（桌宠大小）→ sprite 缩放；缺键/坏值走默认。

        夹取范围同配置落库口径（config.py 对 ``spawn_scale`` 的 0.1~4.0），
        避免手工写坏的配置文件把 sprite 尺寸设成 0 或天文数字（sprite.scale
        setter 只拒非正数）。
        """
        cfg = self._config if cfg is None else cfg
        try:
            value = float(cfg.get("scale") or catalog.DEFAULT_SCALE)
        except (TypeError, ValueError):
            value = catalog.DEFAULT_SCALE
        return max(0.1, min(4.0, value))

    def _sync_collision_physics(self, cfg=None) -> None:
        """config 的碰撞物理 4 键 → ``SpriteCollisionWorld`` 求解参数。

        旧架构每个桌宠进程逐窗读自己 cfg 的组策略并在逐次求解时使用
        （``collision_client.py:140-145`` policy 四键 / ``:558-563`` 传入
        ``solve_collision_impulse``）。overlay 世界在 __init__ 只用形参默认值
        建了一次，此后无任何写入点——设置页四个滑块（含重启）完全无效。

        世界侧每次 tick 现读这组属性（``sprite_collision.py:291-293`` 传
        restitution/friction/impulse_cap、``:429``/``:455`` 传 mass_scale），
        故这里赋值即运行期生效。夹取范围同 config 归一（config.py:668-671）。
        世界逐屏一份，故逐宿主下发同一组值（全局设置对所有屏一致）。
        """
        cfg = self._config if cfg is None else cfg
        # 缺键/坏值 → 保留世界当前值（构造时的形参默认 = config 缺省）。
        for host in self._hosts:
            world = host.collision
            world.restitution = self._clamped_config_float(
                cfg, "collision_restitution", world.restitution, 0.0, 1.0)
            world.friction = self._clamped_config_float(
                cfg, "collision_friction", world.friction, 0.0, 0.30)
            world.mass_scale = self._clamped_config_float(
                cfg, "collision_mass_scale", world.mass_scale, 0.5, 2.0)
            world.impulse_cap = self._clamped_config_float(
                cfg, "collision_impulse_cap", world.impulse_cap, 1000.0, 12000.0)

    @staticmethod
    def _clamped_config_float(cfg, key: str, default: float,
                              low: float, high: float) -> float:
        """读一个带上下限的浮点键；缺键/坏值回退 ``default``（再夹取）。"""
        raw = cfg.get(key, None)
        if raw is None:
            raw = default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(default)
        return max(low, min(high, value))

    def _sync_sprite_collision_flags(self) -> None:
        """只重算逐 sprite 的**宠物间碰撞资格位**（不动其它任何设置）。

        G4：资格位 = per-sprite 口径（主 sprite 用本进程 config，子 sprite 用它
        自己的 config-slot-N.json，见 ``_sprite_collision_enabled``）。世界按
        pair 过滤见 ``sprite_collision._ignored_pet_pairs``；岛-宠接触不受它影响，
        岛墙由 ``dynamic_island.collision_enabled`` 单独控制。

        独立成窄入口的原因：子宠 slot 配置变化（目录事件 / 既有 3s 兜底轮询）只
        该重算资格位，**不得**顺手重刷 playback/drag_physics/no_move 等主配置项
        ——那些归 ``_sync_sprite_settings``（refresh_settings 的全量语义）。
        """
        for sprite in list(self._all_sprites()):
            sprite.collision_enabled = self._sprite_collision_enabled(sprite)

    def _sprite_collision_enabled(self, sprite) -> bool:
        """该 sprite 的宠物间总碰撞资格（per-sprite 口径 = 各自的 config）。

        旧契约（base-2786c15）：每个桌宠是独立进程、各读自己的 config
        （``window.py:3929``），子宠的 ``config-slot-N.json`` 由设置进程按其身份
        写就。本方法把这条身份→配置的对应关系在当前单进程壳里复原：

        - 主 sprite（``self.sprite``）= 本进程运行期 config（主配置）；
        - 子 sprite = 它自己的清洗档案快照，缺文件/缺键按同源默认 true；
        - 陌生 sprite（不在身份表）退回主配置值，绝不瞎猜 slot。

        只在配置刷新 / spawn·复活 / 提升等边界调用，不参与逐帧 tick。
        """
        if sprite is None or sprite not in self._spawned_slots:
            return True
        return bool(self._sprite_config(sprite).get("collision_enabled", True))

    def _apply_effective_mouse_through(self) -> None:
        """有效穿透 = 用户手动穿透 OR 光标自动穿透（window.py:4204 同式）。

        逐宿主下发：穿透/避让都是"整组桌宠"的状态，只给主屏窗口设会让副屏那张
        overlay 一直吞点击（用户看到的是"副屏的宠盖住了我的桌面"）。
        """
        through = bool(self._user_mouse_through or self._auto_cursor_hidden)
        for host in self._hosts:
            host.overlay.mouse_through = through

    def _on_user_through_changed(self, on: bool) -> None:
        """菜单「鼠标穿透」直写收编：记用户意愿 + 持久化 + 复合重算。"""
        self._user_mouse_through = bool(on)
        self._config.set("mouse_through", self._user_mouse_through)
        save = getattr(self._config, "save", None)
        if callable(save):
            save()
        self._apply_effective_mouse_through()

    def _on_fullscreen_changed(self, hit: bool) -> None:
        """全屏出现 → 隐藏；退出 → 恢复（window_screen.on_fullscreen_changed 等价）。

        自动隐藏与手动隐藏同语义（4.3 后半）：隐藏期间 proactive/agent_link
        一并 pause，恢复时 resume——legacy 的 ``host.hide()`` 走自定义 hide →
        ``_pause_activity`` 正是这条链。
        """
        # 诊断留痕：探测翻转时记录判定原因（2026-09-23 实机抓到 1Hz 翻转，
        # 无原因无法归因——probe 的 why 串本来就有，只差落日志）
        why = ""
        try:
            from . import platform_win
            why = platform_win._fg_fullscreen_probe()[1]
        except Exception:
            pass
        logging.info("overlay: 全屏状态变化 hit=%s auto_hidden=%s why=%s",
                     hit, self._auto_hidden, why)
        # 免打扰（issue #208）：全屏 = 等效免打扰的唯一来源（与 legacy 的
        # AppShell._on_shared_fullscreen 同一处判定，不另起探测）。
        # 入场先立闸（隐藏动作自身带出的呈现不漏门控）；退场等 overlay 真正
        # 显示回来之后再结算——结束汇报只投向可见目标，早一拍就是汇报给空气。
        from . import quiet_mode

        if hit:
            quiet_mode.note_fullscreen(True)
            if not self._auto_hidden and self._any_host_visible():
                self._auto_hidden = True
                self._hide_bubble_for_visibility()
                self._set_all_clips_paused(True)
                self._hide_all_hosts()
                self._pause_shared_subsystems()
                self._pause_warm_all()
                self._note_sprite_visibility_changed()
            return
        if self._auto_hidden:
            self._auto_hidden = False
            self._show_all_hosts()
            self._restore_sticky_bubble()
            self._resume_shared_subsystems()
            self._set_all_clips_paused(False)
            self._resume_warm_all()
            self._note_sprite_visibility_changed()
        # 退场结算：**无条件**落在最后（手动隐藏时上面那一支不走，但免打扰的
        # 等效状态必须落下，否则全屏标志永远停在 True）。此刻 overlay 若已恢复
        # 显示，结束汇报才交付出得去。
        quiet_mode.note_fullscreen(False)

    def _cursor_transition_blocked(self) -> bool:
        """有 grab 在途（含 click-only：锁定位 / 未按 SHIFT 的按住也持有
        ``_mouse_grab``）——此刻不许改穿透态（window._cursor_transition_blocked
        只看拖拽态，本壳的判据是任意 grab）。任一屏的窗口持有 grab 都算。"""
        return any(getattr(host.overlay, "_mouse_grab", None) is not None
                   for host in self._hosts)

    def _converge_auto_cursor_hidden(self) -> None:
        """自动穿透态收敛（幂等）：无 grab 在途即解除光标隐藏态。

        触发面（壳层在 ``OverlayShell.__init__`` 挂到 overlay 上，两处都直接
        挂本函数）：``_shown_cb``（每次 overlay 显示/恢复）与
        ``_user_input_cb``（``OverlayWindow._note_user_input``：真到达窗口的
        鼠标输入）。

        语义 = 「无 grab 阻挡即清，真实 HIDDEN 由 watcher 下一拍重建」。收敛
        时机本身就是证据，不查"最后一次探测说了什么"（曾有过一道 HIDDEN 闸，
        2026-10-15 撤销——见下）。

        为什么不能只靠 SHOWING：解除这件事原本挂在"收到 SHOWING 且无 grab"
        上，而滞留状态只有 ``_on_grab_finished`` 会冲刷——grab 一旦卡死
        （丢 release）就再没有收尾事件，窗口会永久停在"看得见点不到"。

        两道闸（按序）：

        1. ``_auto_cursor_hidden`` 为假 = 没有待解除的自动穿透（幂等）；
        2. grab 在途（``_cursor_transition_blocked()``）= 此刻不许动穿透态，
           但**不能丢弃这次收敛请求**：记 ``_converge_pending``，由
           ``_on_grab_finished`` 在 grab 收尾后补做。丢掉它就会出现"隐藏后
           重新显示、探针报 UNKNOWN 不产生回调"这条链上的永久卡死（show 的
           收敛被 grab 挡掉、看门狗随后清掉 grab，而 grab 收尾只处理
           ``_cursor_restore_pending``，再没人认这笔账）。

        为什么撤掉 HIDDEN 闸（它曾用来挡屏迁移后 ≤50ms 的误拦截）：屏迁移先
        显示新 overlay 再关旧的，可见性计数 1→2→1 不翻转 → 闸门不补探测，
        没有闸就会在这一刻清掉真实穿透、下一拍 50ms 轮询前错误拦截点击。但
        那道闸制造的新风险是永久卡死——生产上 ``_auto_cursor_hidden`` 必由
        HIDDEN 回调置位（最后见过的必是 HIDDEN），探针转 UNKNOWN 后再无回调、
        待办补做被闸门永远拦住，窗口永久"看得见点不到"。两害相权：误拦截是
        ≤1 拍的自愈窗口（探针恢复报 HIDDEN 即重建穿透），卡死不会自愈。故
        去掉闸门，把这一拍的误拦截当成**刻意接受的代价**。

        定位：显示/恢复与输入两道**都不是通用自愈**。正常穿透开着时点击到不了
        窗口，输入这条只在窗口重建 / 屏迁移把原生样式与标记弄脱同步时接得上；
        真正解开"grab 卡死"的是看门狗（``OverlayWindow._check_stale_press``）。
        """
        if not self._auto_cursor_hidden:
            return
        if self._cursor_transition_blocked():
            self._converge_pending = True
            return
        self._cursor_restore_pending = False
        self._converge_pending = False
        self._auto_cursor_hidden = False
        self._apply_effective_mouse_through()

    def _on_cursor_visibility_changed(self, visibility: str) -> None:
        """光标隐藏（watcher 已做 0.2s 去抖）→ 自动穿透；恢复 → 解除。

        watcher 每拍（20Hz）都会重报当前状态，故这里也是"真实 HIDDEN 重建
        穿透"的来源（收敛清掉后 ≤1 拍内被置回）。
        """
        if visibility == "HIDDEN":
            if not self._cursor_transition_blocked():
                self._auto_cursor_hidden = True
                self._apply_effective_mouse_through()
        elif visibility == "SHOWING":
            if self._cursor_transition_blocked():
                self._cursor_restore_pending = True
            else:
                self._cursor_restore_pending = False
                self._auto_cursor_hidden = False
                self._apply_effective_mouse_through()

    def _on_drag_committed(self, sprite) -> None:
        """真拖拽升级（``overlay._drag_committed_cb``）：取消探头 + 放宽钳制域。

        窗口自带的 ``_on_real_drag_started`` 只做探头取消；放宽域要碰宿主的活动区，
        所以整条升级钩子接到壳层来（``_wire_host_overlay`` 里覆盖）。
        """
        probe = self._probe_of_sprite(sprite)
        if probe is not None:
            probe.on_sprite_drag_started(sprite)
        self._relax_drag_domain(sprite)

    def _probe_of_sprite(self, sprite):
        host = self._host_of(sprite) or self._main_host
        return None if host is None else host.probe

    def _relax_drag_domain(self, sprite) -> None:
        """跨屏拖拽期间把这只的钳制域放宽到**整张窗**。

        多矩形域是"可站立域"：身体框跨在缝上、或经过错位屏间的空洞时会被逐点
        判定钳回最近那块屏——实测表现是先像撞墙、再突然瞬移过去，而不是顺着
        光标穿过去。拖动中放宽到整窗（含空洞），松手由 ``_on_grab_finished``
        恢复多矩形域并补一次钳制，落脚点仍在真正有显示器的屏上。
        """
        host = self._host_of(sprite) or self._main_host
        if host is None:
            return
        # 关闭跨屏时**不放宽**：活动区本来就只剩家那块屏，整窗宽松域会把宠放出
        # 它本该待着的这块屏（关闭语义是"拖不过去"）。
        if not self._cross_screen_allowed:
            return
        if self._active_multi():
            # 多窗：拖拽中必须允许身体**画到窗外的相邻屏**，否则一半身体被本窗
            # 裁掉（用户看到"被窗口挡住"）。宽松域 = 全部屏的**物理**外接矩形换算
            # 到本窗局部，相邻窗用投影补另一半（见 _sync_drag_projection）。
            domain = self._drag_domain_local(host)
            if domain is None:
                return
            sprite.set_clamp_rects((domain,))
            self._drag_relaxed_sprite = sprite
            return
        bounds = host.bounds
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        sprite.set_clamp_rects((QRectF(bounds),))
        self._drag_relaxed_sprite = sprite

    def _drag_domain_local(self, host) -> QRectF | None:
        """拖拽放宽域（源窗局部）：全部屏的**物理**外接矩形换算到源窗局部。

        逻辑坐标不能直接用：混合缩放下 Qt 的逻辑桌面是 islands-of-screens，一块
        1920 物理 @200% 的屏只占 960 逻辑宽；而源窗局部的单位是它自己的逻辑像素
        （DPR 1.0 时就是物理像素）。拿各屏**逻辑**矩形求并集会把高 DPI 屏的物理
        范围算小，实机表现是宠拖到那块屏中段就跟不上鼠标（鼠标还在动，宠被域钳住）。
        物理空洞包含在内是有意的——拖拽期间允许身体经过无显示器的缝，松手才按
        真实屏钳回来。
        """
        if not self._hosts or host is None:
            return None
        p_src = self._window_physical_origin(host.overlay)
        d_src = float(host.overlay.devicePixelRatioF()) or 1.0
        left = top = right = bottom = None
        for other in self._hosts:
            origin = self._window_physical_origin(other.overlay)
            d_dst = float(other.overlay.devicePixelRatioF()) or 1.0
            geometry = other.overlay.geometry()
            width_px = geometry.width() * d_dst
            height_px = geometry.height() * d_dst
            x0 = (origin.x() - p_src.x()) / d_src
            y0 = (origin.y() - p_src.y()) / d_src
            x1 = (origin.x() + width_px - p_src.x()) / d_src
            y1 = (origin.y() + height_px - p_src.y()) / d_src
            left = x0 if left is None else min(left, x0)
            top = y0 if top is None else min(top, y0)
            right = x1 if right is None else max(right, x1)
            bottom = y1 if bottom is None else max(bottom, y1)
        if left is None or right is None or top is None or bottom is None:
            return None
        if right <= left or bottom <= top:
            return None
        return QRectF(left, top, right - left, bottom - top)

    def _sync_drag_projection(self, sprite=None) -> None:
        """拖拽期间让**相邻窗**把这只的另一半也画出来（跨窗拼接）。

        每屏一窗后，被拖的这只只能画在自己宿主那张窗里；身体跨在缝上时另一半
        会被窗口裁掉——实机观感就是"被一个窗口挡住"。这里给相邻窗登记只读投影：
        它只按映射平移重画当前帧，本窗裁剪天然只留落在自己屏里的那半。
        """
        target = sprite if sprite is not None else self._drag_relaxed_sprite
        own = self._host_of(target) if target is not None else None
        for host in self._hosts:
            overlay = host.overlay
            if target is None or own is None or host is own:
                if target is not None:
                    overlay.clear_projection(target)
                continue
            if own.overlay is overlay:
                continue
            # 投影按**逻辑尺寸**画（scale=1）＝松手换窗后的观感，不是按物理像素对齐。
            # 按 d_src/d_dst 画设备采样比更接近 1:1，但会让这只在目标屏上先大 1.5 倍、
            # 松手瞬间缩回原生尺寸——4K@150% → 1080p@100% 的用户直接报成「拖过去缩放
            # 不对/很怪」。宠的逻辑尺寸跨屏不变，投影就该照逻辑尺寸画。
            scale = 1.0
            target_local = self._sprite_pos_in(target, own.overlay, overlay)
            # ``sprite.paint()`` 自己会用 ``sprite.pos`` 画，所以这里传**平移量**而不是
            # 最终位置：平移 = 目标位置 − pos（否则 pos 被加两次，实机就是"投影甩到
            # 鼠标左边很远"）。
            offset = QPointF(target_local.x() - target.pos.x() * scale,
                             target_local.y() - target.pos.y() * scale)
            overlay.set_projection(target, offset, scale)

    def _place_sprite_in_physical_screen(self, sprite, host=None, *,
                                         persist: bool = False) -> bool:
        """按**物理**身体中心把这只交到它现在所在那块屏的窗（多窗的归属落地）。

        拖拽期间不调用（跨缝靠投影）；松手后的收尾与每拍兜底都走这里，因此不依赖
        某个窗口的 grab 收尾钩子有没有被触发。
        """
        own = host or self._host_of(sprite)
        if own is None or sprite is None:
            return False
        p_src = self._window_physical_origin(own.overlay)
        d_src = float(own.overlay.devicePixelRatioF()) or 1.0
        # body_rect() 已经是**局部绝对**矩形（已含 pos），不要再加一次 pos
        body = sprite.body_rect()
        cx = p_src.x() + (body.x() + body.width() / 2.0) * d_src
        cy = p_src.y() + (body.y() + body.height() / 2.0) * d_src
        for other in self._hosts:
            if other is own:
                continue
            origin = self._window_physical_origin(other.overlay)
            d_dst = float(other.overlay.devicePixelRatioF()) or 1.0
            area = QRect(origin.x(), origin.y(),
                         int(other.overlay.geometry().width() * d_dst),
                         int(other.overlay.geometry().height() * d_dst))
            if not area.contains(int(cx), int(cy)):
                continue
            local = self._sprite_pos_in(sprite, own.overlay, other.overlay)
            own.overlay.remove_sprite(sprite, release_clip=False)
            other.overlay.add_sprite(sprite)
            sprite.set_pos(local)
            other.apply_activity(sprite)
            sprite.home_screen = other.screen
            # ``remove_sprite`` 会删掉该 sprite 的**全部**位置监听，跟随器又缓存着
            # 旧窗原点：不补这两步，气泡会停在旧位置、以后也不再跟宠走（实机表现
            # 就是"气泡漂到另一块屏"）。
            self._bind_position_listener(sprite)
            self._rebind_bubble_follower(sprite, other)
            if persist:
                # 归属只在**用户动作**里改写（拖拽收尾/显式移屏）。每拍兜底跑到
                # 这里说明是机械变化（拔屏、几何变化），改写会把用户记住的屏吃掉。
                self._remember_sprite_screen(sprite, other.screen)
            return True
        return False

    def _hand_over_at_release(self, sprite) -> bool:
        """松手后的归属落地（拖拽期间不换窗，见 ``_hand_over_drag``）。"""
        if not self._active_multi():
            return False
        return self._place_sprite_in_physical_screen(sprite, persist=True)

    def _clear_drag_projection(self) -> None:
        """拖拽收尾：把所有窗上的投影摘干净（否则会留下幽灵残影）。"""
        sprite = self._drag_relaxed_sprite
        if sprite is None:
            return
        for host in self._hosts:
            host.overlay.clear_projection(sprite)

    def _restore_drag_domain(self) -> None:
        """拖拽收尾：恢复多矩形活动区，并把落脚点钳回真正有显示器的屏。"""
        self._clear_drag_projection()
        sprite = self._drag_relaxed_sprite
        if self._active_multi() and sprite is not None:
            # 松手才换窗（拖拽期间不换，见 _hand_over_drag）
            self._hand_over_at_release(sprite)
        self._drag_relaxed_sprite = None
        if sprite is None:
            return
        host = self._host_of(sprite) or self._main_host
        if host is None:
            return
        host.apply_activity(sprite)
        # set_clamp_rects 按契约不补钳制，而松手点可能正落在屏间空洞里——
        # 不补这一次，宠会停在没有显示器的地方（看起来凭空消失）。
        sprite.set_pos(QPointF(sprite.pos))
        slot = self._spawned_slots.get(sprite)
        if slot is None and sprite is getattr(self, "sprite", None):
            slot = 0
        target = self._screen_of_sprite(sprite)
        stored = (None if slot is None
                  else self._stored_screen_name(self._spawn_config_dir(), slot))
        online = {_screen_name(screen) for screen in self._screens()}
        # 只有「原归属那块屏还活着」时，这次拖拽才算**有意换屏**。拔屏后的临时承载
        # （以及关闭跨屏）下拖动都是机械位置变化——改写归属会让那块屏插回来后回不到
        # 原位置，所以那条路径只记位置、不动归属。
        if stored is None or stored in online:
            self._remember_sprite_screen(sprite, target)
        if slot is not None:
            # 顺手落一次盘：一次拖拽只是用户动作级的一次原子写（实测中位 3.4 ms），
            # 换来「拖完直接重启/被杀」也不丢位置——不能只依赖关闭主宠那条路径。
            self._save_child_geometry(sprite, slot=slot)

    def _hand_over_drag(self, sprite, global_pos) -> bool:
        """拖拽中把这只交给光标所在那块屏的窗（仅多窗模式）。

        单窗模式恒 False——同一张窗内自由移动，没有"交给谁"这回事。
        """
        if not self._active_multi() or sprite is None:
            return False
        # 拖拽期间**不换窗**：这只始终在源窗积分（弹簧目标/轨迹/偏移都是那一套
        # 坐标系），别的窗只挂带缩放的只读投影。换窗会改写一整组弹簧状态，且
        # 换完立刻恢复活动区又会把它硬钳到窗边——实机"停在副屏边缘不动"。
        self._sync_drag_projection(sprite)
        return False

    def _hand_over_sprite(self, sprite, target_host, global_pos=None) -> bool:
        """把这只**连同当前身体位置**从源窗交给目标窗（多窗的唯一跨屏入口）。

        局部坐标经全局中转：源窗局部 → 全局 → 目标窗局部。拖拽偏移
        （``sprite._drag_offset``）是指针相对精灵的差、与窗无关，移交时不动它。
        ``remove_sprite(release_clip=False)`` 保住解码 clip——移交不是"放生"。
        """
        source = self._host_of(sprite)
        if source is None or target_host is None:
            return False
        origin_src = source.overlay.geometry().topLeft()
        origin_dst = target_host.overlay.geometry().topLeft()
        if global_pos is not None:
            grab = getattr(sprite, "_drag_offset", None)
            local = QPointF(global_pos) - QPointF(origin_dst)
            if grab is not None:
                local -= QPointF(grab)
        else:
            body = sprite.body_rect()
            local = QPointF(origin_src.x() + body.x() - origin_dst.x(),
                            origin_src.y() + body.y() - origin_dst.y())
        if source is not target_host:
            source.overlay.remove_sprite(sprite, release_clip=False)
            target_host.overlay.add_sprite(sprite)
        sprite.set_pos(local)
        target_host.apply_activity(sprite)
        sprite.home_screen = target_host.screen
        if source is not target_host:
            # 换窗必然摘掉了位置监听（见 ``_place_sprite_in_physical_screen`` 同款
            # 说明）：runtime 标记与气泡跟随器都要改挂到目标窗。
            self._bind_position_listener(sprite)
            self._rebind_bubble_follower(sprite, target_host)
        return True

    def _on_grab_finished(self) -> None:
        """拖拽收尾（overlay._finish_grab 钩子）：收放宽域 + 冲刷光标恢复滞留。

        第一段把跨屏拖拽期间放宽到整窗的钳制域收回多矩形活动区，并把落脚点钳
        回真正有显示器的屏（松手点可能正落在屏间空洞）。

        尾段补做被 grab 闸住的收敛请求（``_converge_pending``）：此刻 grab 已
        清，补做直接生效（收敛函数只看这两道闸）。
        跨屏拖拽把一块屏上的最后一只宠带走时，那块屏的窗口在**这次 grab 结束
        之后**才拆（拖拽期间拆掉正在接受输入的窗口，会话就再也收不了尾）。
        """
        self._restore_drag_domain()
        if self._cursor_restore_pending:
            self._cursor_restore_pending = False
            self._auto_cursor_hidden = False
            self._apply_effective_mouse_through()
        if self._converge_pending:
            self._converge_pending = False
            self._converge_auto_cursor_hidden()

    # ---------------------------------------------------------------- 4.2a 位置持久化
    def _pos_from_ratios(self, rx: float, ry: float, sprite=None,
                         bounds=None) -> QPointF:
        """rx/ry（身体中心相对可用区比例）反解 sprite 左上角坐标。

        口径同 window_placement.restore_position：以**身体框**中心为锚，扣除
        身体框在 sprite 矩形内的偏移（body_box 未声明的角色退化为全画布）。
        可用区取**这只所在那块屏**的矩形（``bounds`` 显式给出时优先）——比例是
        相对那块屏记的，用整桌面或别的屏反解会把它放到错误位置。
        """
        sprite = self.sprite if sprite is None else sprite
        if bounds is None:
            bounds = self._screen_rect(self._screen_of_sprite(sprite))
        body = (sprite.body_rect() if hasattr(sprite, "body_rect")
                else sprite.rect())
        bw, bh = body.width(), body.height()
        cx = bounds.left() + rx * bounds.width()
        cy = bounds.top() + ry * bounds.height()
        off_x = body.x() - sprite.rect().x()
        off_y = body.y() - sprite.rect().y()
        return QPointF(cx - bw / 2 - off_x, cy - bh / 2 - off_y)

    def _sprite_geometry(self, sprite, bounds: QRect) -> dict | None:
        """sprite 当前几何 → rx/ry/facing/scale（save_position 的共用算式）。

        bounds 非法（未初始化/退化为 0 宽高）返回 None：宁可不落盘，也不写
        NaN/Inf 比例污染下一次恢复。
        """
        if bounds.width() <= 0 or bounds.height() <= 0:
            return None
        body = (sprite.body_rect() if hasattr(sprite, "body_rect")
                else sprite.rect())
        cx = body.x() + body.width() / 2.0
        cy = body.y() + body.height() / 2.0
        geometry = {
            "rx": (cx - bounds.left()) / bounds.width(),
            "ry": (cy - bounds.top()) / bounds.height(),
            "scale": float(getattr(sprite, "scale", 1.0) or 1.0),
        }
        facing = getattr(sprite, "facing", None)
        if facing in ("left", "right"):
            geometry["facing"] = facing
        return geometry

    def _restore_position(self) -> None:
        """按"身体中心相对可用区比例"恢复位置（window_placement.restore_position
        语义；贴边钳制由 set_pos 的 body_box 钳制收口）。

        实际复活走 ``_spawn_slot`` 的 slot 档案路径（含 ``screen_name``），本方法
        只在没有档案状态的调用点兜底。
        """
        bounds = self._screen_rect(self._screen)
        rx, ry = self._config.get("rx"), self._config.get("ry")
        if rx is None or ry is None:
            self.sprite.set_pos(self._default_corner_pos(
                bounds, self.sprite.rect()))
        else:
            self.sprite.set_pos(self._pos_from_ratios(
                float(rx), float(ry), bounds=bounds))
        facing = str(self._config.get("facing", "") or "")
        if facing in ("left", "right"):
            self.sprite.facing = facing

    def save_position(self) -> None:
        """身体中心相对可用区比例持久化（window_placement.save_position 语义）。"""
        if self.sprite is not None:
            self._save_child_geometry(self.sprite, slot=0)

    # ---------------------------------------------------------------- 4.2b 多 sprite 生灭
    def _spawn_config_dir(self):
        """子肥鱼身份/几何的落盘目录（= 主配置目录）；假配置对象无 dir → None。"""
        return getattr(self._config, "dir", None)

    def _active_slots(self) -> list[int]:
        """进程内活跃身份（spawn 顺序）= 当前活跃宠清单的内容。"""
        return sorted(self._remembered_active)

    def _all_sprites(self):
        return ([self.sprite] if self.sprite is not None else []) + list(self._spawned)

    def _sprite_for_slot(self, slot):
        if slot == 0:
            return self.sprite
        return next((s for s in self._spawned if self._spawned_slots.get(s) == slot), None)

    def _persist_active_slots(self) -> bool:
        """活跃宠清单落盘（spawn/退出/复活各调一次，原子替换）。返回是否真的写成。

        这条路径上的调用者**全是用户显式动作**（生成 / 恢复 / 关闭），所以坏文件走
        ``on_broken="backup-overwrite"``：先备份再覆盖。默认 ``refuse`` 只适用于
        "没有用户在场"的写入；在这里继续 refuse 的后果是「生成的宠重启就没了」
        「关掉的宠重启还在」永远修不好，而 UI 里没有任何解释（2026-10-10 ops D1）。

        失败**必须可见**：调用方的"成功"叙事（菜单关了、宠上屏了）不能盖过这一层——
        日志 + 气泡点名路径，绝不静默。启动期的自动复活**不写盘**（不替用户改盘、
        不自动修复坏文件）。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return False
        from . import pet_profiles

        result = pet_profiles.save_active_set(
            config_dir, self._active_slots(), on_broken="backup-overwrite")
        if result.status != "ok":
            logging.error("overlay: 活跃宠清单落盘失败（%s）：%s → %s",
                          result.status, result.reason, result.path)
            self.show_bubble(
                f"桌宠状态没能记住：活跃状态文件写入失败，重启后这次改动会丢失。\n"
                f"文件：{result.path}", duration_ms=9000)
            return False
        if result.backup_path is not None:
            logging.warning("overlay: 原活跃宠清单不可信，已备份到 %s 后重建",
                            result.backup_path)
        return True

    def _spawn_failure_text(self, slot: int, exc: Exception) -> str:
        """生成失败的说明文案：点名槽位与**该槽档案路径**，slot0 损坏时用降级诊断。

        slot0 降级诊断（``slot0_profile_diagnosis``）本来就是"含路径 + 修好即可恢复"
        的整句，此时优先用它——比复述异常字符串有用得多。
        """
        from . import pet_profiles

        if slot == 0 and self.slot0_profile_diagnosis():
            return f"生成桌宠失败：{self.slot0_profile_diagnosis()}"
        text = f"生成桌宠失败（slot-{slot}）：{exc}"
        config_dir = self._spawn_config_dir()
        if config_dir:
            try:
                text += f"\n档案：{pet_profiles.slot_profile_path(config_dir, slot)}"
            except Exception:
                logging.debug("overlay: 取该槽档案路径失败", exc_info=True)
        return text

    def _notify_spawn_failed(self, slot: int, exc: Exception) -> None:
        """生成失败的**可见**反馈（2026-10-10 终审 ops D2-新）。

        旧实现只有 ``logging.exception``：坏 slot0 期间点「生成 / 恢复」没有任何反应，
        而分配器每次都按最小目标回到 0（用户明确取舍：不静默跳号），于是每次点都失败
        且无声——正是 B01 要消灭的「点了没反应」。

        通道按"谁能真上屏"降级：有宠在屏 → 冒一条带路径的气泡；**一只宠都没有**
        （消息无落点，刚关掉最后一只/全隐藏）→ 把同一份说明落进可见恢复窗，那是此时
        唯一的可见入口。有宠但整窗被隐藏等情况下气泡上不了屏时，只如实记一条警告，
        不硬造第二个入口。
        """
        text = self._spawn_failure_text(slot, exc)
        if self.show_bubble(text, duration_ms=12000):
            return
        if self._all_sprites():
            logging.warning("overlay: 生成失败提示未能上屏（无可见气泡面）：%s",
                            text.replace("\n", " | "))
            return
        self._show_recovery_diagnosis(text)

    def _show_recovery_diagnosis(self, text: str) -> None:
        """把一条上不了屏的说明落进**可见恢复窗**（无宠时唯一的可见入口）。"""
        self._ensure_recovery_entry(force=True)
        label = getattr(self, "_recovery_diagnosis_label", None)
        if label is None:
            return
        try:
            label.setText(str(text))
            label.setVisible(True)
        except RuntimeError:                      # 窗口已随进程销毁
            logging.debug("overlay: 恢复窗诊断标签已失效", exc_info=True)

    def spawn_pet(self) -> None:
        """生小肥鱼（app.py spawn_pet 进程内路径语义 + D6 无锁身份分配）。

        分配 slot 身份 → 建 per-pet 库 + 新 sprite（自主宠向右逐级错开，重叠
        规避由 body 钳制兜底）→ 进活跃清单并落盘。无可用身份时冒泡告知（B01，
        分配器兜底后几乎不可达），**建库/读档案失败也一律可见告知**（带该槽档案
        路径，见 :meth:`_notify_spawn_failed`）；两条失败都不留半只 sprite。

        目标槽仍是**最小非活跃槽**（含 0）：slot0 档案损坏期间每次点都落到 0 并失败，
        这是用户明确的取舍（最小补位，不静默跳号）；能做的不是绕开它，而是把失败说清楚
        （2026-10-10 ops D2-新）。
        """
        config_dir = self._spawn_config_dir()
        try:
            slot = overlay_spawn_state.allocate_slot(
                config_dir, active_slots=self._active_slots())
        except Exception:
            logging.exception("overlay: 生小肥鱼失败（无可用 slot 身份）")
            # B01：分配兜底后这条路径只剩「128 个身份全活跃」一种可能，但绝不
            # 能再只写日志——静默失败在用户眼里就是「点了没反应」。
            self.show_bubble(
                f"生小肥鱼失败：位置身份已耗尽（1-{overlay_spawn_state.MAX_SCAN_SLOTS}"
                " 全部占用）", duration_ms=6000)
            return
        try:
            self._spawn_slot(slot)
        except Exception as exc:
            logging.exception("overlay: 生小肥鱼失败 (slot=%s)", slot)
            self._notify_spawn_failed(slot, exc)
            return
        self._remembered_active.append(slot)
        self._slot_signature_cache = self._slot_config_signatures()
        if slot == 0:
            self._rebind_main_sprite()
            self._load_self_talk_settings()
            self._schedule_self_talk()
        self._persist_active_slots()
        self._refresh_tray_menu()  # 托盘逐只条目随生灭重建（4.2c）
        logging.info("overlay: 已生成子肥鱼 (slot=%s)", slot)
        # 无宠期留着的待恢复提醒：有新宠上屏时补回（仍是同一条，不重新入队）
        self._sync_alert_copy()

    def _spawn_slot(self, slot: int) -> None:
        before = set(self._all_sprites())
        try:
            self._build_slot_sprite(slot)
        except Exception:
            for sprite in set(self._all_sprites()) - before:
                host = self._host_of(sprite)
                self._close_self_talk_host(sprite)
                self._close_sprite_bubble(sprite)
                self._sprite_cfg_cache.pop(sprite, None)
                self._sprite_hosts.pop(sprite, None)
                self._spawned_slots.pop(sprite, None)
                lib = self._spawned_libs.pop(sprite, None) or getattr(sprite, "library", None)
                if sprite in self._spawned:
                    self._spawned.remove(sprite)
                if sprite is self.sprite:
                    self.sprite = self.lib = None
                if host is not None:
                    host.overlay.remove_sprite(sprite)
                self._shutdown_library(lib, "failed-spawn")
            raise

    def _build_slot_sprite(self, slot: int) -> None:
        """按 slot 身份建一只子肥鱼（spawn 与重启复活共用路径，不落清单）。

        身份几何（rx/ry/facing/scale）与**角色**从 ``config-slot-N.json`` 读；
        有记录 = 重启复活，按比例恢复上次位置、按它自己存的角色建库；无记录 =
        首次生成，自主宠向右错开（角色继承主配置）。spawn 后按该 slot 的配置
        下发设置，并建它自己的气泡/自言自语宿主（旧架构一窗一气泡一计时）。
        """
        from . import config_write_lock, pet_profiles

        config_dir = self._spawn_config_dir()
        snapshot = None
        if config_dir:
            with config_write_lock.config_write_lock(config_dir):
                snapshot = pet_profiles.read_slot_snapshot(config_dir, slot)
                if snapshot.status != "ok":
                    raise RuntimeError(f"slot-{slot}: {snapshot}")
                if not snapshot.profile.exists:
                    from .config import normalize_pet_profile_snapshot

                    source = normalize_pet_profile_snapshot(
                        pet_profiles.read_slot_profile(config_dir, 0).data)
                    seed = {key: copy.deepcopy(source[key])
                            for key in pet_profiles.PER_PET_SETTING_KEYS}
                    if not self._config.get("spawn_inherit_size", True):
                        seed["scale"] = self._config.get("spawn_scale", catalog.DEFAULT_SCALE)
                    result = pet_profiles.write_profile_settings(config_dir, slot, seed)
                    if not result.ok:
                        raise RuntimeError(f"slot-{slot}: {result}")
                result = pet_profiles.begin_slot_generation(config_dir, slot)
                if not result.ok:
                    raise RuntimeError(f"slot-{slot}: {result}")
                snapshot = pet_profiles.read_slot_snapshot(config_dir, slot)
                if snapshot.status != "ok":
                    raise RuntimeError(f"slot-{slot}: {snapshot}")
            slot_data = dict(snapshot.profile.data)
        else:
            memory = getattr(self, "_slot_memory", None)
            if memory is None:
                self._slot_memory = memory = {}
            slot_data = memory.setdefault(slot, {
                key: copy.deepcopy(self._config.get(key, pet_profiles.same_source_default(key)))
                for key in pet_profiles.PER_PET_SETTING_KEYS})
        reader = _SpriteConfig(self, None, snapshot=snapshot, data=slot_data)
        state = overlay_spawn_state._clean_geometry(slot_data)
        lib = self._create_sprite_library(reader._values, slot)
        scale = self._config_scale(reader)
        pet_screen = self._restore_screen(slot, state)
        # 多窗（混合缩放）下这只可能属于**别的屏**的窗：窗口边界、活动区、成员表
        # 都按目标宿主取，不能一律塞进主宿主。
        target_host = self.host_for_screen(pet_screen) or self._main_host
        try:
            sprite = self._sprite_factory(lib, QPointF(0, 0), scale)
            sprite.home_screen = pet_screen
            sprite.set_bounds(QRect(target_host.bounds))
            target_host.apply_activity(sprite)
            sprite.set_pos(self._spawn_sprite_pos(sprite, state, pet_screen))
        except Exception:
            self._shutdown_library(lib, "failed-spawn-before-registration")
            raise
        facing = state.get("facing")
        if facing in ("left", "right"):
            sprite.facing = facing
        target_host.overlay.add_sprite(sprite)
        target_host.apply_activity(sprite)
        # O3：新 sprite 的播放节拍按**当前有效可播性**初始化——PetSprite 默认
        # 未暂停，隐藏/挂起期生成的子宠若不在这里压住，行为链一起播就按帧率
        # 解码，而窗口根本没有像素要上屏。``visible`` 不动（它逻辑上仍是可见
        # 宠物），窗口真正显示时由整窗显示路径（含 ``start()``）补放行。
        if (slot != 0 or hasattr(self, "_watcher")) and not self._clips_playable():
            self._set_sprite_clip_paused(sprite, True)
        if slot == 0:
            self.sprite, self.lib = sprite, lib
        else:
            self._spawned.append(sprite)
            self._spawned_libs[sprite] = lib
        self._spawned_slots[sprite] = slot
        reader._sprite = sprite
        self._sprite_cfg_cache[sprite] = reader
        self._apply_sprite_settings_to(sprite, reader)
        sprite.collision_enabled = bool(reader.get("collision_enabled"))
        sprite._profile_character = str(reader.get("character"))
        sprite._profile_generation = reader.generation
        if self._cross_screen_allowed and not state.get("screen_name"):
            # 首次落位即把屏归属钉死在这块屏上：没被换过屏的宠（尤其新生成的子宠）
            # 若不留归属，重启时只能跟着**主宠**走——主宠单独拖到副屏后，子宠也会
            # 跟着过去（旧默认）。只记一笔待写（见 ``_remember_sprite_screen``），
            # 由这一次落位的几何自存落盘；关闭跨屏时**不记**（那时的落位是临时
            # 承载，写了就把用户多屏下摆好的归属覆盖掉）。
            self._remember_sprite_screen(sprite, pet_screen)
        # 这只子肥鱼自己的气泡与自己计时的自言自语（旧架构每窗各一套）
        self._ensure_sprite_bubble(sprite)
        host = self._ensure_self_talk_host(sprite)
        if host is not None:
            host.schedule()
        if config_dir:
            # 生成/复活即落一次几何：未及优雅退出（崩溃/断电）也能按清单复活
            geometry = self._sprite_geometry(
                sprite, self._screen_rect(self._screen_of_sprite(sprite)))
            if geometry and (state.get("rx") is None or state.get("ry") is None):
                self._save_child_geometry(sprite, slot=slot, geometry=geometry)
            # 刚写过该 slot 文件 → 签名缓存同步刷新：否则下一次目录事件会把这次
            # 自写当成"外部变更"，把**别的**子宠的运行态一起重下发（窄契约破了）。
            self._slot_signature_cache = self._slot_config_signatures()

    def _slot_writer(self, slot: int):
        """该 slot 的设置写入器（键/值 → ``config-slot-N.json``，原子写）。

        用于"回退默认角色"这类需要把值写回**发起方那份配置**的路径：子肥鱼切角色
        失败回退时绝不能写到主配置（那是另一只宠的身份）。
        """
        config_dir = self._spawn_config_dir()

        def _write(key, value) -> bool:
            from . import pet_profiles

            if key not in pet_profiles.PER_PET_KEYS:
                return False
            if not config_dir:
                memory = getattr(self, "_slot_memory", None)
                if memory is None:
                    self._slot_memory = memory = {}
                memory.setdefault(slot, {})[key] = copy.deepcopy(value)
                return True
            result = pet_profiles.write_profile_settings(
                config_dir, int(slot), {key: value}, user_customized=True)
            if not result.ok:
                logging.warning("overlay: slot-%s 设置未保存：%s (%s)",
                                slot, result.status, result.reason)
            return result.ok

        return _write

    def _create_sprite_library(self, slot_data: dict, slot: int):
        """按**这只子肥鱼自己那份**配置的角色建库（B7b）。

        缺 ``character`` 键或与主宠同角色 → 走既有的 per-pet 库工厂入口
        ``_create_main_library``（同一件事：按主配置的角色建一只新库，T3 的
        per-pet 语义不变）；只有该 slot 存了**不同的**角色时才单独建库，且回退
        默认角色时把值写回它自己的 slot 文件（绝不污染主配置）。
        """
        self._library_fallback_used = False
        main_character = str(self._sprite_config(self.sprite).get(
            'character', catalog.DEFAULT_CHARACTER))
        character = str(slot_data.get("character") or main_character)
        if character == main_character:
            lib = self._create_main_library()
            # 主库工厂自带的回退写的是发起方那份配置（slot 0），``slot_data`` 只是
            # 它的副本：不同步的话 ``_profile_character`` 会记着已被回退掉的旧角色，
            # 紧接着的 profile 重下发会误判成"要换角色"再白建一次库。
            if getattr(self, "_library_fallback_used", False):
                slot_data["character"] = catalog.DEFAULT_CHARACTER
            return lib
        writer = self._slot_writer(slot)

        def fallback(key, value):
            ok = writer(key, value)
            if ok:
                slot_data[key] = value
            return ok

        return self._create_library_for(character, fallback_writer=fallback)

    def _screen_under_sprite(self, sprite):
        """这只**此刻所在**的那块屏（同名兜底用的优先项）。

        不能直接用 ``host.screen``：单窗下宿主屏是"家那块屏"，而这只可能正站在
        同名的另一块屏上。按宿主当前活动区里包住身体中心的那一块取。
        """
        host = self._host_of(sprite)
        if host is None:
            return None
        body = sprite.body_rect()
        origin = host.overlay.geometry().topLeft()
        point = QPointF(origin.x() + body.x() + body.width() / 2.0,
                        origin.y() + body.y() + body.height() / 2.0)
        # 与 `_screen_at_global` 同一套判据（可用区包含 → 最近屏兜底）：两套包含关系
        # 各判一次会在接缝/空洞上给出不同答案。
        return self._screen_at_global(point) or getattr(host, "screen", None)

    def _screen_by_name(self, name, *, prefer=None):
        """已连接显示器里按名字取（与 ``window_placement.screen_available`` 同口径）。

        ``prefer``：**同名屏**时优先返回这一块（调用方通常传"这只当前所在那屏"）。
        驱动重装 / 虚拟显示器可能让两块屏报同一个名字，那时"取第一块匹配"可能拿到
        另一块，后续机械重排会把宠搬到它其实不在的那块屏上。
        """
        if not name:
            return None
        first = None
        for screen in QGuiApplication.screens():
            try:
                if screen.name() != name:
                    continue
            except RuntimeError:
                continue
            if prefer is not None and screen is prefer:
                return screen
            if first is None:
                first = screen
        return first

    def _restore_screen(self, slot, state):
        """这只宠该落在哪块屏（复活/生成的唯一判据）。

        - **关闭跨屏**（主配置 ``allow_cross_screen`` 为假、本进程快照）→ 都在家
          那块屏：记忆不采纳（见 ``_save_child_geometry`` 的临时承载分支，记忆
          本身一个字节不动），所以重新开启跨屏并重启，各宠仍回到各自那块屏；
        - **记忆命中的屏在线**（``screen_name`` 是已连接显示器）→ 就是那块屏，
          比例也在那块屏的可用区里反解；槽0 的归属即"家"（``_migrate_to_screen``）；
        - **记忆不在线 / 没有记忆** → 留在已有归属上，不搬：一段不在线的记忆不该
          把已经在屏上的宠挪走（legacy ``window_placement.restore_position`` 同款
          取舍）。子宠跟随**主宠**那块屏（新宠的旧行为：自主宠向右错开）。
        """
        home = self._screen
        if not self._cross_screen_allowed:
            return home
        screen = self._screen_by_name(state.get("screen_name"))
        if screen is not None:
            if slot == 0 and not self._active_multi():
                # 单窗：主宠的记忆即"家"——窗口就那一张，活动区跟着家走。
                # 多窗（混合缩放）：每块屏都有自己的窗，家是启动那块屏，不改宿主；
                # 这只落在哪个宿主由它加到谁的 overlay 决定。
                self._main_host.screen = screen
            return screen
        if slot == 0 or self.sprite is None:
            return home
        return self._screen_of_sprite(self.sprite)

    def _spawn_sprite_pos(self, sprite, state: dict, screen=None) -> QPointF:
        """这只宠的落位：有持久化比例按 rx/ry 恢复（比例相对**它那块屏**），
        否则自主宠向右逐级错开。"""
        screen = self._screen if screen is None else screen
        bounds = self._screen_rect(screen)
        rx, ry = state.get("rx"), state.get("ry")
        if rx is not None and ry is not None:
            return self._pos_from_ratios(
                float(rx), float(ry), sprite=sprite, bounds=bounds)
        if self.sprite is None or self._screen_of_sprite(self.sprite) is not screen:
            # 这块屏上还没有主宠作参照（该屏第一只）→ 默认右下角（旧语义）
            return self._default_corner_pos(bounds, sprite.rect())
        index = len(self._spawned) + 1
        main_body = self.sprite.body_rect()
        pos = QPointF(main_body.x() + main_body.width() + 24 * index,
                      main_body.y())
        body = sprite.body_rect()
        offset = QPointF(body.x() - sprite.rect().x(),
                         body.y() - sprite.rect().y())
        proposed = QRectF(pos.x() + offset.x(), pos.y() + offset.y(),
                          body.width(), body.height())
        if QRectF(bounds).contains(proposed):
            return pos
        # 错开之后越过这块屏的右缘：落回这块屏的右下角（活动区是整桌面，越界
        # 会把它生到隔壁屏上去——"新宠自主宠向右错开"只在同一块屏内成立）
        return self._default_corner_pos(bounds, sprite.rect())

    def _restore_spawned_pets(self) -> None:
        """D5：启动恢复依据 = 活跃宠清单（**不是** slot 配置存在）。

        清单缺失 = 首次运行语义；损坏/缺首字段由 overlay_spawn_state 防御性
        回退成空清单（只有主宠）。单条复活失败（素材缺失/身份配置被删）只
        记录并摘除该条，不拖垮启动，也不让坏条目永久留在清单里。
        """
        for slot in self._remembered_active:
            if slot == 0:
                continue
            try:
                self._spawn_slot(slot)
            except Exception:
                logging.exception("overlay: 恢复失败，保留活跃身份 slot=%s", slot)

    def _remove_spawned_sprite(self, sprite) -> None:
        """退出一只子肥鱼：几何先落盘（配置保留）→ remove_sprite（clip 释放
        V-8 + 行为注销 V-9 挂点）→ 库 shutdown 收尾。

        B7b：随该 sprite 一起收掉它自己的气泡窗、自言自语定时器与缓存视图
        （旧架构进程退出即整套资源释放；留着就是孤儿顶层气泡 + 空转定时器）。
        """
        config_dir = self._spawn_config_dir()
        slot = self._spawned_slots.pop(sprite, None)
        self._remembered_active = [n for n in self._remembered_active if n != slot]
        host = self._host_of(sprite)
        geometry = (self._sprite_geometry(
            sprite, self._screen_rect(self._screen_of_sprite(sprite)))
            if host is not None else None)
        if config_dir and slot and geometry:
            self._save_child_geometry(sprite, slot=slot, geometry=geometry)
        lib = self._spawned_libs.pop(sprite, None)
        # 菜单缩略图缓存挂壳上、按库身份分键：这只宠退出即定向摘掉它自己的条目
        # （只摘它的，不动同壳其他宠的图；死条目留着只占 128 条上限）。
        self._drop_menu_icon_cache_for(lib)
        if sprite in self._spawned:
            self._spawned.remove(sprite)
        # 目标悬空：这只若正被共享控制器写角，先结清（它马上就不在窗里了）
        self._settle_golden_spin_if_target(sprite)
        if host is not None:
            host.overlay.remove_sprite(sprite)
        self._close_self_talk_host(sprite)
        # 承载气泡收口期：同 _exit_main_pet（先摘引用 + 关闸，再关气泡）；
        # 走的正是承载宠时才摘引用（不是载体就不动副本）
        if sprite is self._alert_presenter:
            self._alert_detach_copy()
        self._alert_copy_teardown = True
        try:
            self._close_sprite_bubble(sprite)
        finally:
            self._alert_copy_teardown = False
        self._sprite_hosts.pop(sprite, None)
        self._sprite_cfg_cache.pop(sprite, None)
        shutdown = getattr(lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logging.exception("overlay: 子肥鱼素材库收尾失败")
        # 这只若是当前提醒的承载宠：整条交接到仍可见的宠（或在无宠时留待恢复）
        self._sync_alert_copy()

    def clear_spawned_pets(self) -> None:
        """退出全部子肥鱼（app.py clear_spawned_pets 语义）：逐只出活跃清单
        （slot 配置保留），最后清单落盘为空——退出即不再复活（D5）。"""
        for sprite in list(self._spawned):
            self._remove_spawned_sprite(sprite)
        self._spawned = []
        self._persist_active_slots()
        self._refresh_tray_menu()

    def save_spawned_positions(self) -> None:
        """退出收口：逐只按各自 slot 身份持久化几何（重启复活的位置来源）。"""
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        for sprite in list(self._spawned):
            slot = self._spawned_slots.get(sprite)
            host = self._host_of(sprite)
            geometry = (self._sprite_geometry(
                sprite, self._screen_rect(self._screen_of_sprite(sprite)))
                if host is not None else None)
            if slot and geometry:
                self._save_child_geometry(sprite, slot=slot, geometry=geometry)

    def exit_pet(self, sprite=None) -> bool:
        """退出单只（菜单「退出这只」的 overlay 落点）。

        只移除目标活跃身份，档案保留；slot0与子宠同样可逆，不提升、不退出应用。
        """
        target = self.sprite if sprite is None else sprite
        if target is self.sprite:
            return self._exit_main_pet()
        if target in self._spawned:
            self._remove_spawned_sprite(target)
            self._persist_active_slots()
            self._refresh_tray_menu()
            return True
        return False

    def _exit_main_pet(self) -> bool:
        """关闭slot0的呈现资源，保留其档案、共享服务与所有子身份。"""
        if self.sprite is None:
            return False
        self.save_position()
        old_main, old_lib = self.sprite, self.lib
        host = self._host_of(old_main)
        self._settle_golden_spin_if_target(old_main)
        if host is not None:
            host.overlay.remove_sprite(old_main)
        self._close_self_talk_host(old_main)
        # 承载气泡收口期：先摘副本引用 + 关闸，免得关闭动作的 hidden 信号把
        # "这只宠走了"当成"提醒被收起"，在濒死的气泡上再挂一次（重放）。
        # 只有走的正是承载宠时才摘引用：不是载体就不该动副本（免得白重挂一次）。
        if old_main is self._alert_presenter:
            self._alert_detach_copy()
        self._alert_copy_teardown = True
        try:
            self._close_sprite_bubble(old_main)
            follower = getattr(self, "_bubble_follower", None)
            if follower is not None:
                follower.close()
        finally:
            self._alert_copy_teardown = False
        self._bubble_follower = None
        self._sprite_cfg_cache.pop(old_main, None)
        self._spawned_slots.pop(old_main, None)
        self._sprite_hosts.pop(old_main, None)
        self.sprite = self.lib = None
        timer = getattr(self, "_self_talk_timer", None)
        if timer is not None:
            timer.stop()
        self._self_talk_enabled = False
        self._remembered_active = [n for n in self._remembered_active if n != 0]
        self._delete_runtime_marker()
        self._shutdown_library(old_lib, "slot0")
        self._persist_active_slots()
        self._refresh_tray_menu()
        # 承载宠退出：当前提醒整体交接到仍可见的宠上（同 ID、同剩余 TTL、一份副本）
        self._sync_alert_copy()
        return True

    def _rebind_main_sprite(self) -> None:
        """恢复slot0后重挂其位置监听、投喂、气泡与标记。"""
        host = self.host_for_sprite(self.sprite)
        if self.sprite is not None:
            self._bind_position_listener(self.sprite)
        self._bind_feeding(host)
        self._sprite_hosts.pop(self.sprite, None)
        self._bind_bubble()
        # 点击气泡族：主身份提升后回调仍指本壳（overlay 未变，重挂=幂等对齐）
        host.overlay._on_sprite_click = self._on_sprite_click
        self._sync_runtime_marker()

    # ---------------------------------------------------------------- D7 设置进程避让标记
    def _sync_runtime_marker(self) -> None:
        """写 runtime 标记（设置进程避让读它）：主 sprite 身体框的全局几何。

        原点取**主宠所在屏**的窗口（多屏下主宠可能不在主宿主上），否则设置进程
        会去避让一块屏上的错误坐标。
        """
        try:
            body = self.sprite.body_rect()
            origin = self._sprite_origin(self.sprite)
            slot_manager.write_runtime_marker(
                self._config.dir, self._config.instance_id,
                origin.x() + body.x(), origin.y() + body.y(),
                body.width(), body.height(), versioned=True)
        except Exception:
            logging.debug("overlay: 写 runtime 标记失败", exc_info=True)

    def _on_main_sprite_moved(self, _sprite) -> None:
        now = time.monotonic()
        if now - self._last_marker_write < 1.0:  # 1Hz 节流
            return
        self._last_marker_write = now
        self._sync_runtime_marker()

    def _delete_runtime_marker(self) -> None:
        try:
            slot_manager.delete_runtime_marker(
                self._config.dir, self._config.instance_id)
        except Exception:
            logging.debug("overlay: 删 runtime 标记失败", exc_info=True)

    # ---------------------------------------------------------------- D12 指令通道
    def _install_settings_command_watch(self) -> None:
        """装 D12 指令消费：config 目录 watcher + 3s 轮询兜底（不新起线程）。

        「独立设置进程写指令文件 → 主进程消费」的消费侧，机制与 app.py
        ``_install_config_watcher`` 同款：QFileSystemWatcher 盯目录（原子
        ``os.replace`` 会在目录里产生事件）+ QTimer 轮询兜底（网络盘/换 inode
        等场景可能漏事件，轮询是保底；与 ``_settings_watch_timer`` 同 3s 节奏）。
        无 config 目录（假配置对象 / 目录不可建）时整条链不装——与
        ``_spawn_config_dir()`` 的既有防御一致，绝不在测试替身上抛。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        from PySide6.QtCore import QFileSystemWatcher

        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logging.warning("overlay: 配置目录不可创建，跳过指令通道: %s", config_dir)
            return
        watcher = QFileSystemWatcher(self)
        watcher.directoryChanged.connect(self._on_settings_command_dir_changed)
        if not watcher.addPath(str(config_dir)):
            logging.warning("overlay: 无法监视配置目录，指令通道退化为轮询: %s",
                            config_dir)
        self._command_watcher = watcher
        self._command_timer.setInterval(3000)
        self._command_timer.timeout.connect(self._on_settings_command_poll)
        self._command_timer.start()
        # 启动即消费：设置进程可能在主进程起来之前就写下了指令（保鲜窗口内有效）
        self._consume_settings_command()

    def _on_settings_command_dir_changed(self, _path: str) -> None:
        self._consume_settings_command()
        self._refresh_slot_settings_if_changed()

    def _slot_config_signatures(self) -> dict:
        """活跃子宠 slot 配置的 ``(mtime_ns, size)`` 签名（只 stat、不读内容）。

        **已知局限**：同一文件两次改动若 mtime 与 size 都相同（粗粒度文件系统
        时间戳、同长度改写），签名不变 → 这次改动会被漏掉，直到下一次有可观测
        差异的写入或重启。这里只做"便宜的筛噪"，不引入内容哈希/版本号框架。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return {}
        signatures: dict = {}
        for slot in {0, *self._active_slots()}:
            try:
                stat = overlay_spawn_state.slot_config_path(
                    config_dir, slot).stat()
            except OSError:
                signatures[int(slot)] = None
                continue
            signatures[int(slot)] = (stat.st_mtime_ns, stat.st_size)
        return signatures

    def _refresh_slot_settings_if_changed(self) -> None:
        """配置目录有变动时：活跃 slot 的配置**真变了**才重读（内容读只在事件边界）。

        目录事件不带文件名（runtime 标记、settings.lock 等也会触发），所以先用
        stat 签名筛掉噪声，命中变化的 slot 才重读内容并把它那份设置下发到**对应
        的那只**子肥鱼（旧契约：设置保存即覆盖该宠运行态；见
        ``window.py:3928-4033`` 的逐窗 refresh）。

        窄契约（不出圈）：只有**签名变了的那几个 slot** 会被重读/重下发，其余
        子宠的运行态一个键都不碰——目录事件不该顺手把别的宠刷成主配置值。
        B7b 之前这里只重算碰撞资格位；现在同一入口按各自 slot 文件下发全套
        per-slot 键（``_apply_sprite_settings_to``）+ 重算资格位。

        没有子宠 / 没有 config 目录 → 立即返回，零 I/O；签名只 stat 不读文件，
        且只在目录事件与既有 3s 兜底轮询上跑（不逐帧、不新起线程、不动 tick）。
        """
        signatures = self._slot_config_signatures()
        previous = getattr(self, "_slot_signature_cache", None) or {}
        self._slot_signature_cache = signatures
        if previous == signatures:
            return
        changed = {slot for slot, sig in signatures.items()
                   if previous.get(slot) != sig}
        # 首次（无缓存）或签名真的变了 → 读 slot 配置内容（内容读只在这里发生）
        logging.info("overlay: 子肥鱼 slot 配置有变更 → 按各自配置重下发设置 %s",
                     sorted(changed))
        for slot in changed:
            if not self._apply_child_profile(slot):
                self._slot_signature_cache.pop(slot, None)

    def _on_settings_command_poll(self) -> None:
        """既有 3s 兜底轮询：指令消费 + 子宠 slot 设置变更检查（同为事件兜底）。"""
        self._consume_settings_command()
        self._refresh_slot_settings_if_changed()

    def _consume_settings_command(self) -> None:
        """消费一条设置进程指令（幂等；无指令 = 空转）。

        指令语义：``exit_spawned_pets`` 的 ``target`` 为空 = 退出全部子肥鱼（设置页
        「一键退出子肥鱼」），否则只退那个 slot 身份（身份已不在活跃清单 = 空操作，
        只留日志）；``apply_settings_reset`` = 按刚写下的默认值对齐这一只的运行态
        （见 :meth:`apply_settings_reset`）。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        command = overlay_settings_command.claim_command(config_dir)
        if command is None:
            return
        retry = False
        try:
            name, target = command["command"], command["target"]
            if name == overlay_settings_command.CMD_PROFILE_RESET:
                if target is None or not 0 <= target <= overlay_spawn_state.MAX_SCAN_SLOTS:
                    logging.warning("overlay: profile_reset target=%s 永久无效，失败结束", target)
                elif not self._apply_child_profile(target):
                    retry = True
                    logging.warning("overlay: profile_reset slot-%s 未应用，保留有界重试", target)
                else:
                    logging.info("overlay: profile_reset slot-%s 已同步（位置保留/关闭目标不复活）", target)
            elif name == overlay_settings_command.CMD_APPLY_SETTINGS_RESET:
                self.apply_settings_reset(target)
            elif target is None:
                self.clear_spawned_pets()
            else:
                sprite = next((item for item in self._spawned
                               if self._spawned_slots.get(item) == target), None)
                if sprite is not None:
                    self.exit_pet(sprite)
                else:
                    logging.info("overlay: 指令目标 slot-%s 已关闭，空操作结束", target)
        except Exception:
            retry = True
            logging.exception("overlay: 指令应用失败，保留有界重试")
        finally:
            overlay_settings_command.finish_command(config_dir, command, retry=retry)

    def apply_settings_reset(self, slot=None) -> None:
        """全局reset只重读全局能力与world参数，不动任何宠档案或位置。"""
        self._config.reload()
        self._apply_window_capabilities()
        self._sync_collision_physics(self._config)

    def _teardown_settings_command_watch(self) -> None:
        """释放指令 watcher 与轮询定时器（stop / aboutToQuit / 测试收口共用）。"""
        watcher = getattr(self, "_command_watcher", None)
        if watcher is not None:
            try:
                watcher.directoryChanged.disconnect(
                    self._on_settings_command_dir_changed)
            except (RuntimeError, TypeError):
                pass
            watcher.setParent(None)
            watcher.deleteLater()
            self._command_watcher = None
        timer = getattr(self, "_command_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- D13 逐 sprite 设置路由
    def sprite_instance_id(self, sprite) -> str:
        """sprite 的 config 身份：主 sprite = ``slot-0``，子 sprite = 各自 slot 身份。

        D13：独立设置进程按 ``--instance`` 打开对应 ``config-slot-N.json``；主宠的
        档案身份就是 ``slot-0``（身份空间含 0），故主/子走同一条显式身份通路。
        陌生 sprite（不在登记表）回退 ``slot-0``，绝不瞎猜 slot。
        """
        main_id = "slot-0"
        if sprite is None or sprite is self.sprite:
            return main_id
        slot = self._spawned_slots.get(sprite)
        if slot is None:
            return main_id
        return slot_manager.slot_to_instance_id(int(slot))

    def open_settings_for(self, sprite=None) -> bool:
        """按被点 sprite 的 config 身份打开设置页（D13 落点）。

        返回 True = 已交给独立设置进程（与 ``AppShell.open_settings_process``
        同义）。只认 AppShell 的 opener：拿不到（测试替身）或开关关闭 → False；
        overlay 拓扑没有 PetWindow 可挂 parent，故不做进程内回退。

        交不出去时必须给可见反馈（``_notify_settings_unavailable``）：菜单项看着
        可用、点下去毫无反应是最难自查的一类"坏了"。
        """
        opener = getattr(getattr(self._instance, "shell", None),
                         "open_settings_process", None)
        if not callable(opener):
            return False
        if bool(opener(_SettingsIdentity(self.sprite_instance_id(sprite)))):
            return True
        self._notify_settings_unavailable()
        return False

    def _notify_settings_unavailable(self) -> None:
        """设置进程交不出去时的可见反馈（气泡说明 + 日志）。

        **为什么是气泡而不是"菜单项置灰 + tooltip"**：``settings_process_isolation``
        在设置界面没有任何入口，置灰只说明"不可用"、说不出原因，用户也没有补救
        路径；tooltip 在 macOS 原生菜单上根本不显示，而 mac 正是这条缺陷家族的
        主战场（legacy 拓扑有进程内设置窗兜底，只有 overlay 会走到这里）。反馈收
        在 ``open_settings_for``（唯一路由点）→ 托盘、右键 facade 的
        ``on_open_modern_settings`` / ``on_open_settings`` 三条入口自动都有；置灰
        得在每个菜单构建处各写一遍，且构建期冻结的状态在用户改完 config.json 后
        就是错的（该键改动无需重启）。

        文案按原因分两种，并点名 config 键（用户能在 config.json 里改回来）。
        """
        if not bool(self._config.get("settings_process_isolation", True)):
            reason = ("config.json 里的 settings_process_isolation 被关掉了，"
                      "改回 true 再点一次")
        else:
            reason = "独立设置进程没拉起来，稍后重试或看日志"
        logging.warning("overlay: 桌宠设置未打开（%s）", reason)
        self.show_bubble(f"桌宠设置没打开：{reason}", duration_ms=6000)

    def _sprite_visible(self, sprite) -> bool:
        """sprite 当前可见性（M14）；无该字段的鸭式 sprite 视为可见。"""
        return bool(getattr(sprite, "visible", True))

    def set_sprite_visible(self, sprite, visible: bool) -> None:
        """逐只显隐（M14）：只改该 sprite 的 visible，不动整窗显隐语义。

        整窗显隐仍归 ``set_pet_visible``（overlay.show/hide + 岛状态同步）；
        逐只是"这一只退出合成与交互面"，两者互不联动。隐藏走 sprite 既有脏
        矩形通道，overlay 立即擦除残留。

        隐藏时同时收**这一只自己的**气泡（旧机 ``window.py:1160-1237``：
        逐宠 ``hide()`` → ``_pause_activity`` → ``bubble.hide()``）。不收的话
        气泡会继续浮在桌面上直到自身停留计时结束（最长时长 + 2s），且因该只
        已不可见，位置扇出早退——气泡停在原地不动。B7b 起子宠也有自己的气泡，
        故逐只收气泡对所有 sprite 生效（隐藏子宠不影响主宠的气泡）。

        A5：用户主动隐藏**主**宠（= 整窗？不，逐只隐藏主宠）时给托盘提示——
        旧机 ``on_hidden`` 由 app 注入（``app.py:545``），overlay 下托盘挂在壳
        上、``AppShell.tray`` 恒为 None，故由壳自己走那条通知（getattr 探测，
        不改 app.py）。

        O3：显隐同时切换该 sprite 的播放节拍（隐藏 → ``pause_clip``，显示 →
        ``resume_clip``），并把"可见 sprite 数变化"通知驱动器——藏光最后一只
        可见 sprite 时目标档位是 T3（没有像素要上屏），恢复可见则同步回全速。

        恢复侧先过**有效可播**闸门（``_clips_playable``）：暂停有两个独立所有者
        （本壳的整窗显隐 + 驱动器的锁屏挂起），"这次是谁解除的"不足以判定该放行
        ——整窗隐藏中托盘勾「显示这只」、锁屏挂起中全屏避让解除，都会让 clip 在
        不可见/挂起期重新按帧率解码。sprite 的 ``visible`` 照改（它逻辑上仍是
        可见宠物，只是窗口/屏幕不显示），被压住的播放节拍等窗口真正可见时由
        整窗显示路径补放行。
        """
        if sprite is None:
            return
        setter = getattr(sprite, "set_visible", None)
        if callable(setter):
            setter(bool(visible))
        else:
            sprite.visible = bool(visible)
        # O3：隐藏的那一只停播放节拍（clip 定时器），显示则从暂停处续播——
        # 隐藏 sprite 既不绘制也不进 fanout，继续按帧率解码纯属白烧 CPU；
        # 恢复**不**重启动画（不回第 0 帧、无首帧冷启动）。
        # 恢复放行 = 这一只被标为可见 **且** 窗口真的能上屏（有效可播闸门）；
        # 闸门关着时按暂停方向走（幂等，不打断既有的整窗暂停）。
        self._set_sprite_clip_paused(
            sprite, not (visible and self._clips_playable()))
        if not visible:
            # 逐只隐藏的收口期：承载宠被藏时先摘副本引用 + 关闸，隐藏该只气泡的
            # hidden 信号不得把"这只被藏了"当成"提醒被收起"而在它自己头上重挂一次
            # （不是载体就不动副本，免得白重挂一次）。
            if sprite is self._alert_presenter:
                self._alert_detach_copy()
            self._alert_copy_teardown = True
            try:
                self._hide_bubble_for_sprite(sprite)
            finally:
                self._alert_copy_teardown = False
            if sprite is getattr(self, "sprite", None):
                self._notify_pet_hidden()
        self._sync_tray_pet_visibility()
        # 可见 sprite 数变化 → 档位机立即重评（零可见 sprite 的目标档位是 T3）
        self._note_sprite_visibility_changed()
        # 承载宠被藏（或在无宠期重新可见）：提醒交接 / 恢复走同一漏斗
        self._sync_alert_copy()

    @staticmethod
    def _set_sprite_clip_paused(sprite, paused: bool) -> None:
        """逐只播放节拍暂停/恢复（clip 无该接口的鸭子 sprite 静默跳过）。"""
        name = "pause_clip" if paused else "resume_clip"
        call = getattr(sprite, name, None)
        if callable(call):
            call()

    def _clips_playable(self) -> bool:
        """有效可播（O3 恢复路径的统一闸门）：有窗口可见 **且** 驱动器未挂起。

        暂停有两个独立所有者——本壳（整窗显隐/全屏避让）与驱动器（锁屏挂起）。
        恢复路径只看"这次是谁解除的"就会替另一个所有者擅自放行：整窗隐藏中
        托盘勾「显示这只」、锁屏中全屏避让解除，都会让 clip 在不可见/挂起期
        重新按帧率解码（O3 契约破坏）。多屏下是"任一窗口可见"：一整组窗口全隐藏
        才没有像素要上屏。
        """
        try:
            if not self._any_host_visible():
                return False
        except RuntimeError:
            return False  # 原生窗口已销毁：没东西可上屏
        driver = getattr(self, "driver", None)
        return not bool(getattr(driver, "suspended", False))

    def _set_all_clips_paused(self, paused: bool) -> None:
        """整窗隐藏/显示：逐 sprite 停/续播放节拍（O3）。

        恢复侧跳过**逐只被隐藏**的 sprite（``visible`` 为假）：整窗显示不等于
        这一只该醒——否则"托盘藏起子肥鱼 → 隐藏整窗 → 显示整窗"会让那只又开始
        按帧率解码（隐藏 sprite 的播放节拍暂停被窗口显隐悄悄解除）。逐只恢复
        仍归 ``set_sprite_visible(True)``。

        恢复侧还要过有效可播闸门：本方法的调用点里，全屏避让解除发生在锁屏
        挂起期间（``overlay.show()`` 已走完、可见性判据不再拦得住），挂起未解除
        时整批不许放行。
        """
        if not paused and not self._clips_playable():
            return
        # 遍历**全部宿主**的成员：``self.overlay`` 只是主窗的别名，多窗（混合缩放）
        # 下副屏窗里的精灵走不到这里就等于永远等不到 resume——实机表现是"那只
        # 子宠完全静止、连帧都不换"（它 spawn/移交时被判不可播先压住，之后没人放行）。
        # 取**每张窗的成员表**而不是壳的记账列表：生产上覆盖全部宿主，测试/裸装配
        # 里直接挂到窗上的成员也照得到。
        for sprite in [sp for host in self._hosts
                       for sp in list(host.overlay.sprites)]:
            if not paused and not self._sprite_visible(sprite):
                continue
            self._set_sprite_clip_paused(sprite, paused)

    def _note_sprite_visibility_changed(self) -> None:
        """把"可见 sprite 数变化"通知驱动器（同 ``note_sprite_count_changed`` 面）。"""
        note = getattr(self.driver, "note_sprite_visibility_changed", None)
        if callable(note):
            note()

    def toggle_sprite_visible(self, sprite=None) -> None:
        """逐只显隐切换（托盘逐只菜单入口）；sprite=None = 主宠。"""
        target = self.sprite if sprite is None else sprite
        if target is None:
            return
        self.set_sprite_visible(target, not self._sprite_visible(target))

    def _sync_tray_pet_visibility(self) -> None:
        """托盘弹出前/显隐变更后同步逐只勾选态（外部也可能改过 sprite.visible）。"""
        for sprite, action in list(getattr(self, "_pet_visible_actions", [])):
            try:
                action.setChecked(self._sprite_visible(sprite))
            except RuntimeError:
                continue  # 菜单已销毁（wrapper 失效）：跳过

    def set_pet_visible(self, visible: bool) -> None:
        """显隐切换（app.py toggle_visible 等价）+ 岛状态同步 + 共享子系统 pause/resume。

        4.3 后半：显隐钩子上接主动识屏/联动监视器的 pause/resume
        （``window.py:1214-1250`` 语义）。隐藏时也收起气泡；恢复时把仍挂着的
        粘滞提醒重新挂上（legacy ``_resume_activity`` 同款）。

        自言自语定时器与显隐对称（``window.py:1183`` / ``:1240``）：隐藏期间
        停表（不可见壳不冒泡），恢复显示时重排下一次。

        音乐自动唱歌轮询同样对称（window.py:1145-1147 / :1241-1242）：隐藏
        停表、恢复显示按开关重启。

        O3：整窗隐藏/显示同时逐 sprite 停/续播放节拍，并把主库与**全部**子宠库
        的动画预热 ``pause_warm`` / ``resume_warm`` 成对切换（旧机 ``hide()`` →
        ``_pause_activity`` → ``lib.pause_warm()`` 的等价链，``window.py:1214-1248``）；
        隐藏期预热没有可见收益，却仍在派生 ffmpeg。恢复侧在 ``overlay.show()``
        之后同一分支内补回。
        """
        if visible:
            self._show_all_hosts()
            for host in self._hosts:
                if host.probe is not None:
                    host.probe.resume()
            if self._alert_current is not None:
                # 队列提醒（含已迁移到子宠的）整窗恢复也走承载宠漏斗：legacy
                # `_sticky_*` 重挂会把同一条提醒在"当前目标"上再挂一份
                self._sync_alert_copy()
            else:
                self._restore_sticky_bubble()
                if self._alert_queue:
                    # 隐藏期排队等着的提醒（隐藏期间 resolved 掉 current、或
                    # current 到点收口）：显示恢复时接着推进，不靠"下一次新提醒"
                    # 才把它捞出来（pump 自带可见性/门禁复判，被挡就原地等着）
                    self._pump_alerts()
            self._resume_shared_subsystems()
            if getattr(self, "_self_talk_timer", None) is not None:
                self._schedule_self_talk()
            self._resume_all_self_talk_hosts()   # 子宠各自的计时同样恢复
            self.sync_music_sing()
            self._set_all_clips_paused(False)
            self._resume_warm_all()
            self._note_sprite_visibility_changed()
        else:
            timer = getattr(self, "_self_talk_timer", None)
            if timer is not None:
                timer.stop()
            self._stop_all_self_talk_hosts()
            self._stop_music_sing_polling()
            self._hide_bubble_for_visibility()
            self._hide_all_hosts()
            for host in self._hosts:
                if host.probe is not None:
                    host.probe.pause()
            self._pause_shared_subsystems()
            self._set_all_clips_paused(True)
            self._pause_warm_all()
            # A5：用户主动隐藏 → 托盘提示怎么恢复（旧机 app 注入 on_hidden 的等价物）
            self._notify_pet_hidden()
            self._note_sprite_visibility_changed()
        island = getattr(getattr(self._instance, "shell", None), "island", None)
        if island is not None:
            try:
                island.set_pet_visible(bool(visible))
            except Exception:
                pass

    # ---------------------------------------------------------------- 预热成对（O3）
    def _all_libraries(self) -> list:
        """主库 + 全部子宠库（每只子肥鱼各持独立库，M8；None 已摘除的跳过）。"""
        libs = []
        main = getattr(self, "lib", None)
        if main is not None:
            libs.append(main)
        libs.extend(lib for lib in getattr(self, "_spawned_libs", {}).values()
                    if lib is not None)
        return libs

    def _pause_warm_all(self) -> None:
        """隐藏/挂起 → 逐库停预热（在飞首帧预热线程一并收口，见 library.pause_warm）。"""
        for lib in self._all_libraries():
            pause = getattr(lib, "pause_warm", None)
            if not callable(pause):
                continue
            try:
                pause()
            except Exception:
                logging.debug("overlay: 暂停动画预热失败", exc_info=True)

    def _resume_warm_all(self) -> None:
        """显示/唤醒 → 逐库补预热（与 ``_pause_warm_all`` 严格成对）。"""
        for lib in self._all_libraries():
            resume = getattr(lib, "resume_warm", None)
            if not callable(resume):
                continue
            try:
                resume()
            except Exception:
                logging.debug("overlay: 恢复动画预热失败", exc_info=True)

    def _notify_pet_hidden(self) -> None:
        """用户主动隐藏桌宠 → 托盘提示（说明怎么恢复）。

        旧机由 app 注入 ``win.on_hidden``（``app.py:545`` → ``_notify_pet_hidden``，
        实现读 ``shell.tray``）；overlay 拓扑下托盘挂在**壳**上（``AppShell.tray``
        恒为 None）、注入链也不存在，故由壳直接调实例上的同一个方法（getattr 探测：
        测试替身/精简装配静默跳过，绝不因提示失败拖垮隐藏动作）。
        """
        notify = getattr(self._instance, "_notify_pet_hidden", None)
        if not callable(notify):
            return
        try:
            notify()
        except Exception:
            logging.debug("overlay: 隐藏提示失败", exc_info=True)

    def _hide_bubble_for_visibility(self) -> None:
        """整窗隐藏期收起**全部**气泡（气泡是置顶 Tool 窗，不随 overlay 隐藏）。"""
        followers = [getattr(self, "_bubble_follower", None)]
        followers.extend(list(self._bubble_followers.values()))
        for follower in followers:
            if follower is not None:
                follower.hide()

    def _restore_sticky_bubble(self) -> None:
        """恢复显示：仍挂着的粘滞提醒重新挂上（legacy ``_resume_activity`` 同款）。"""
        if not self._sticky_bubble_active or not self._sticky_text:
            return
        if self._speech_bubble is None:
            return
        self._show_bubble_text(self._sticky_text, 0,
                              subtitle=self._sticky_subtitle, sticky=True,
                              buttons=self._sticky_buttons)

    def _toggle_pet_visible(self) -> None:
        self.set_pet_visible(not self._any_host_visible())

    def _host_for_collision(self, event):
        """撞出这一事件的成员在哪块屏的宿主上（找不到 → 主宿主）。

        事件只带成员 id（``SpriteCollisionWorld._member_id``），多屏下同一个 id
        只可能出现在一块屏的成员表里；找不到（纯静态成员对，实际不会发生）时
        退回主宿主，绝不猜屏。
        """
        from .sprite_collision import SpriteCollisionWorld
        ids = (getattr(event, "a", None), getattr(event, "b", None))
        for host in self._hosts:
            for sprite in host.overlay.sprites:
                if SpriteCollisionWorld._member_id(sprite) in ids:
                    return host
        return self._main_host

    def _on_collision_sound(self, event) -> None:
        """碰撞音效（进程级共享播放器）：按事件所在屏的世界过岛击闸门。

        播放器只持一个缺省世界（主宿主），故这里把"撞出这一事件的那块屏的世界"
        显式传进去——副屏的岛击才不会被主屏的静态成员表漏判成普通对阈值。
        """
        host = self._host_for_collision(event)
        world = host.collision if host is not None else None
        self._sound.on_collision(event, world=world)

    def _on_collision_squash(self, event) -> None:
        """碰撞 Q 弹（旧权威冲量路径的 squash 语义）：真撞击量级时
        双方 sprite 各压一次。runtime_id 反查 sprite（成员少，线性即可）。

        只在**撞出这一事件的那块屏**的成员里反查：多屏下主/副屏各有成员表，
        拿主宿主的表查副屏事件会认错对象（撞的是同名 id 的另一只）。

        不再用 hit_min_dv 二次过滤：CollisionEvent 只在真撞击时 fire，
        且阈值已按 pair 类型分级（普通 300 / 撞静态成员 60 / thrown 任意）——
        壳层再按 300 卡一刀会把 60-300 的岛撞（合法真撞击）整段截掉
        （旧机低速撞岛也会挤压）。
        """
        from .sprite_collision import SpriteCollisionWorld
        host = self._host_for_collision(event)
        for sprite in host.overlay.sprites:
            if SpriteCollisionWorld._member_id(sprite) in (event.a, event.b):
                squash = getattr(sprite, "squash", None)
                if callable(squash):
                    squash()

    def _on_collision_probe(self, event) -> None:
        """碰撞真撞击 → 边缘探头取消会话（旧机 collision_client 取消链语义）
        + throw_egg arm（探头被撞飞头部跟随速度）。

        arm 只认「本次真取消了活跃探头会话」这个显式事实（探头入口的返回
        值）——探头侧的 5 秒重进倒计时会残留，不能反过来当证据。

        探头/彩蛋走**撞出这一事件的那块屏**自己的那份（逐屏一份），成员也只看
        那块屏的。

        同 _on_collision_squash：不按 hit_min_dv 二次过滤（事件的阈值已按
        pair 分级，岛撞 60-300 也是合法真撞击，旧机同样取消探头会话）。
        """
        from .sprite_collision import SpriteCollisionWorld
        host = self._host_for_collision(event)
        probe = host.probe
        egg = host.throw_egg
        if probe is None and egg is None:
            return
        for sprite in host.overlay.sprites:
            if SpriteCollisionWorld._member_id(sprite) in (event.a, event.b):
                cancelled = None
                if probe is not None:
                    cancelled = probe.on_sprite_collision_hit(sprite)
                if egg is not None:
                    egg.on_probe_collision_throw(
                        sprite, probe_cancelled=cancelled)

    def _say_feeding_bubble(self, result: dict, sprite=None) -> None:
        """投喂气泡（file_eater 同文案格式；B02：落命中那只自己的气泡通道）。

        壳上子宠气泡既有分发靠 ``_bubble_for(sprite)``（每只一只跟随器，见
        ``_ensure_sprite_bubble``），这里不再新造通道：落点是子宠就发子宠那只。
        """
        from .file_interpret.eater import FEEDING_BUBBLE_SUBTITLE, feeding_bubble_text
        follower = self._bubble_for(sprite)
        if follower is None:
            return
        follower.say(feeding_bubble_text(result),
                     subtitle=FEEDING_BUBBLE_SUBTITLE)

    def switch_character(self, character_id: str, sprite=None) -> None:
        """切换角色（4.1c）：换 per-pet 库 + 行为状态重置 + 配置持久化。

        per-pet 库是 T3 定论：新建库给目标 sprite，旧库 shutdown 收尾 clip；
        行为状态机 forget 后下个 tick 自动接管 bind（防旧 clip 跨库残留）。

        ``sprite`` = 被点的那一只（B7b）：旧架构一窗一宠，菜单的「切换角色」只换
        本窗、只写本窗的 cfg。主宠照旧（换 ``self.lib`` + 写主配置）；子宠换**它
        自己的**库、写**它自己的** ``config-slot-N.json['character']``——此前一律
        打在主宠身上（审计 F1），子宠各自存的角色也无人读。

        切完主宠还要收口两处跨角色的派生状态：菜单动画缩略图缓存（B08，同名动画
        跨角色串图）与各聊天面的角色上下文（B07，切完再开口仍是旧角色）。
        """
        target = self.sprite if sprite is None else sprite
        if target is None:
            if sprite is None and character_id and self._slot_writer(0)("character", character_id):
                self._sprite_cfg_cache.pop(None, None)
                self._sync_chat_surfaces(character_id)
            return
        if target is not self.sprite:
            self._switch_child_character(target, character_id)
            return
        current = str(self._sprite_config(target).get("character", catalog.DEFAULT_CHARACTER))
        if not character_id or character_id == current:
            return
        egg = self.host_for_sprite(target).throw_egg
        if egg is not None:
            egg.cancel_all("character_switch")  # 换角色即换素材，飞行会话兜底回正
        try:
            new_lib = self._instance._create_library(character_id)
        except Exception:
            logging.exception("overlay: 切换角色建库失败 %s", character_id)
            return
        if not self.persist_sprite_setting(target, "character", character_id):
            self._shutdown_library(new_lib, "character-save-failed")
            return
        target._profile_character = character_id
        old_lib = self.lib
        self.lib = new_lib
        self.sprite.library = new_lib
        self.host_for_sprite(self.sprite).behavior.forget(self.sprite)
        self._clear_menu_icon_cache()
        self._sync_chat_surfaces(character_id)
        # 换库 = 换 body_box：锚点取材于 ``catalog.character_body_box``（体框变了
        # 但 rect 可能没变，位置扇出只在 old != new 时通知），必须显式刷新一次，
        # 否则同一位置下气泡挂在旧角色的锚点上直到下一次真实位移。
        follower = getattr(self, "_bubble_follower", None)
        refresh_anchor = getattr(follower, "refresh_anchor", None)
        if callable(refresh_anchor):
            try:
                refresh_anchor()
            except Exception:
                logging.debug("overlay: 换角色后气泡锚点刷新失败", exc_info=True)
        shutdown = getattr(old_lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logging.exception("overlay: 旧素材库收尾失败")

    def _drop_menu_icon_cache_for(self, library) -> None:
        """定向摘掉某个素材库的菜单缩略图条目（退出子肥鱼用）。

        缓存挂壳上、键 =（库身份, 动画名）：一份缓存服务同壳所有宠，所以这里按库身份
        只摘这只宠的条目——整份清掉（``_clear_menu_icon_cache`` 的换角色语义）会把别的
        宠的图一起丢掉，下次开菜单全部重解码。没开过菜单（缓存尚未创建）时不动作。
        """
        cache = getattr(self, "_menu_icon_cache", None)
        if isinstance(cache, dict):
            animation_icon_cache_drop_library(cache, library)

    def _clear_menu_icon_cache(self) -> None:
        """换角色后清菜单动画缩略图缓存（B08；复审#5 后是**内存卫生**）。

        缓存挂壳上、键 =（库身份, 动画名）（``shared.animation_icon_cache_key``）：
        同名动画与迟到写入都已由库身份隔开，整份清掉的余下价值是不让旧库的死项占着
        128 条上限、也不给 ``id()`` 复用留机会。下次取图按新库重新解码；没开过菜单
        （缓存尚未创建）时不动作。
        """
        cache = getattr(self, "_menu_icon_cache", None)
        if isinstance(cache, dict):
            cache.clear()

    def _sync_chat_surfaces(self, character_id: str) -> None:
        """把新角色同步到各聊天面（B07）：快速对话气泡 + 完整聊天窗/岛对话气泡。

        聊天面的角色（会话、头像、系统提示）是各自的构造期快照，换素材库不会
        连带更新。持久气泡与聊天窗由持有点按各自 ``switch_character`` 重建角色
        上下文；未创建的聊天面无需同步（新建时按新配置构造）。同步失败只记录，
        绝不能让已经完成的素材切换白做。
        """
        bubble = self._quick_chat
        sync = getattr(bubble, "switch_character", None)
        if callable(sync):
            try:
                sync(character_id)
            except Exception:
                logging.exception("overlay: 快速对话气泡角色同步失败")
        surface_sync = getattr(self._instance, "sync_chat_character", None)
        if callable(surface_sync):
            try:
                surface_sync(character_id)
            except Exception:
                logging.exception("overlay: 完整聊天窗/岛对话气泡角色同步失败")

    def _switch_child_character(self, sprite, character_id: str, *, persist=True) -> bool:
        """子宠换角色：换它自己的库 + 写它自己的 slot 配置（主宠一字不动）。

        聊天面不在此同步（B07 调查结论）：快速对话气泡、完整聊天窗、岛对话气泡
        的角色身份一律取**主配置**（``QuickChatBubble(self._config)``、会话目录按
        主身份），子宠没有自己的聊天身份通道可言——把主宠的聊天面改成子宠的角色
        只会错得更远。菜单缩略图缓存是壳级的（同名动画跨角色串图，B08），换库后
        照清。
        """
        slot = self._spawned_slots.get(sprite)
        current = getattr(sprite, "_profile_character", None)
        if slot is None or not character_id:
            return False
        if character_id == current:
            return True
        center = QPointF(sprite.body_rect().center())
        selected = character_id
        try:
            new_lib = self._instance._create_library(selected)
        except FileNotFoundError:
            selected = catalog.DEFAULT_CHARACTER
            try:
                new_lib = self._instance._create_library(selected)
            except Exception:
                logging.exception("overlay: 子宠默认角色建库失败")
                return False
        except Exception:
            logging.exception("overlay: 子宠切换角色建库失败 %s", selected)
            return False
        if (persist or selected != character_id) and not self._slot_writer(slot)("character", selected):
            self._shutdown_library(new_lib, "未应用角色")
            return False
        self._settle_golden_spin_if_target(sprite)
        sprite_host = self.host_for_sprite(sprite)
        egg = sprite_host.throw_egg
        if egg is not None:
            egg.cancel_all("character_switch")
        old_lib = self.lib if slot == 0 else self._spawned_libs.get(sprite)
        if slot == 0:
            self.lib = new_lib
            self._sync_chat_surfaces(selected)
        else:
            self._spawned_libs[sprite] = new_lib
        sprite.library = new_lib
        sprite._profile_character = selected
        sprite_host.behavior.forget(sprite)
        body = sprite.body_rect()
        sprite.set_pos(QPointF(sprite.pos) + center - QPointF(body.center()))
        if persist:
            self._sprite_cfg_cache.pop(sprite, None)
        self._clear_menu_icon_cache()
        self._refresh_sprite_bubble_style(sprite)
        self._shutdown_library(old_lib, "子宠旧角色")
        return True

    # ---------------------------------------------------------------- 跨屏：同一张窗内移动
    def move_sprite_to_screen(self, sprite, screen, global_pos=None) -> bool:
        """把**这一只**宠放到指定屏（显式调用/拖拽落定的共用面）。

        单窗下没有"换宿主、换窗口"这回事：同一张合成窗覆盖整个虚拟桌面，跨屏就是
        #303 报告的那条路——宠可以直接走到/拖到副屏上，不再被钳在 x=0。

        - ``global_pos`` 给出 = 按该全局左上角落位；
        - 不给 = 保持这只在**当前那块屏可用区里的相对位置**，落到目标屏的同一相对
          位置（与拔屏迁移同一口径）；
        - 已经是这块屏的宠、或关闭跨屏时目标不是家那块屏 → 只刷新归属，不动位置。

        这是**有意**改屏归属的唯一入口（写 ``screen_name``），返回是否真的动了位置。
        """
        if sprite is None or screen is None or screen not in self._screens():
            return False
        if self._active_multi():
            # 混合缩放：每块屏有独立的窗，跨屏 = 把这只交给目标窗
            target = self.host_for_screen(screen)
            if target is None:
                return False
            # 不给全局位置 = "保持这只在屏内的相对位置"（与单窗分支同一句契约）。
            # 直接交给 `_hand_over_sprite` 是按**全局位置**落位的：目标窗在右边时
            # 本地坐标会被算成负值（实机表现：宠落到目标窗外、下一拍再被钳到边上）。
            rx = ry = None
            if global_pos is None:
                rx, ry = self._body_ratio(
                    sprite, self._screen_rect(self._screen_of_sprite(sprite) or screen))
            moved = self._hand_over_sprite(sprite, target, global_pos)
            if moved and rx is not None:
                self._place_body_ratio(sprite, rx, ry, self._screen_rect(screen))
            if moved:
                self._remember_sprite_screen(sprite, screen)
            return moved
        host = self._main_host
        # 单窗：能去哪儿只看**活动区**（关闭跨屏时只有家那块屏）
        if host is None or screen not in {s for s, _rect in host.activity}:
            return False
        if global_pos is not None:
            moved = self._place_sprite_at(sprite, QPointF(global_pos))
        else:
            moved = self._move_sprite_onto_screen(sprite, screen)
        self._sync_sprite_screens()   # 屏归属 + 逐只 DPR 立即跟上（不等下一拍）
        self._remember_sprite_screen(sprite, screen)
        return moved

    def _place_sprite_at(self, sprite, global_pos) -> bool:
        """按全局左上角落位（局部坐标 = 全局 - 窗口原点）；位置没变返回 False。"""
        before = QPointF(sprite.pos)
        sprite.set_pos(QPointF(global_pos) - QPointF(
            self.overlay.geometry().topLeft()))
        return QPointF(sprite.pos) != before

    def _move_sprite_onto_screen(self, sprite, screen) -> bool:
        """按"身体中心在源屏可用区里的比例"把这只放到目标屏同一比例处。"""
        source = self._screen_of_sprite(sprite)
        target = self._screen_rect(screen)
        return self._remap_sprite_ratio(
            sprite, self._screen_rect(source), target)

    def _remap_sprite_ratio(self, sprite, old_rect: QRect, new_rect: QRect) -> bool:
        """把这只按身体中心比例从 ``old_rect`` 搬到 ``new_rect``（同语义迁移的唯一算式）。

        比例必须在换域**之前**取（换域会就地钳一次，旧比例当场消失），落点必须在
        换域**之后**写（``set_pos`` 受当前域钳制，先写会被旧域截断）。本方法自己
        不换域，故两步可以就地连着做；换域的那条路（``_sync_host_geometry``）必须
        先在 ``_geometry_plan`` 里把比例取出来。
        """
        if old_rect.width() <= 0 or old_rect.height() <= 0:
            return False
        if new_rect.width() <= 0 or new_rect.height() <= 0:
            return False
        rx, ry = self._body_ratio(sprite, old_rect)
        return self._place_body_ratio(sprite, rx, ry, new_rect)

    @staticmethod
    def _body_ratio(sprite, rect: QRect) -> tuple:
        """身体框中心相对 ``rect`` 的比例（= 档案里 rx/ry 的口径）。"""
        body = sprite.body_rect()
        cx = body.x() + body.width() / 2.0
        cy = body.y() + body.height() / 2.0
        return ((cx - rect.x()) / rect.width(), (cy - rect.y()) / rect.height())

    @staticmethod
    def _place_body_ratio(sprite, rx: float, ry: float, rect: QRect) -> bool:
        """按比例落位（身体框中心落在 ``rect`` 的同比例点上）；位置是否真的变了。"""
        before = QPointF(sprite.pos)
        body = sprite.body_rect()
        offset_x = body.x() - sprite.rect().x()
        offset_y = body.y() - sprite.rect().y()
        sprite.set_pos(QPointF(
            rect.x() + rx * rect.width() - body.width() / 2.0 - offset_x,
            rect.y() + ry * rect.height() - body.height() / 2.0 - offset_y))
        return QPointF(sprite.pos) != before

    def _remember_sprite_screen(self, sprite, screen) -> None:
        """记下这只宠的**屏幕归属**（有意换屏时唯一改 ``screen_name`` 的地方）。

        归属只在用户把这只**有意**拖到另一块屏时更新；被临时摆在别的屏上的宠
        （关闭跨屏、或原来那块屏临时不在线）比例照旧更新、归属不被机械动作改写，
        于是"关掉跨屏/临时拔屏 → 再开回来"仍回到各自那块屏。

        **不在这里写盘**：本方法跑在拖拽的 mouseMove 上，而单键几何写实测中位
        3.4 ms（p95 4.2 ms；目录锁被别人占着时最长等 ``DEFAULT_TIMEOUT_S`` = 5 s）
        ——GUI 线程不该为一次落盘停这么久。改把待写的屏名记在**这只自己身上**，
        由下一次几何自存与比例（同一文件、同一把锁、同一次原子写）一起落盘；
        记在 sprite 上（同 ``_profile_generation`` / ``home_screen`` 的存法）随
        这只的生命周期一起消失，退出/关闭路径不需要额外清理。
        """
        if sprite is None:
            return
        sprite._pending_screen_assignment = _screen_name(screen)

    def _bind_position_listener(self, sprite) -> None:
        """主 sprite 的 runtime 标记跟随监听（挂在它**当前所在**那张窗上）。

        sprite 不是窗口、没有 moveEvent，runtime 标记靠窗口的位置监听扇出更新。
        必须按 ``_host_of`` 取窗：跨窗移交后仍写死主窗的话，标记会跟着一张已经不
        承载这只的窗走（主宠拖到副屏后 runtime 标记不再跟目标窗）。
        """
        if sprite is not self.sprite:
            return
        host = self._host_of(sprite) or self._main_host
        if host is None:
            return
        host.overlay.add_position_listener(sprite, self._on_main_sprite_moved)

    # ---------------------------------------------------------------- 屏事件
    def _wire_screen_signals(self) -> None:
        self.app.screenAdded.connect(self.handle_screen_added)
        self.app.screenRemoved.connect(self.handle_screen_removed)
        self.app.primaryScreenChanged.connect(self.handle_primary_screen_changed)

    @staticmethod
    def _connect_screen_signal(screen, name: str, slot) -> None:
        """按名连接屏信号；假屏缺该信号/已销毁时静默跳过。"""
        signal = getattr(screen, name, None)
        if signal is None:
            return
        try:
            signal.connect(slot)
        except (TypeError, RuntimeError):
            pass

    @staticmethod
    def _disconnect_screen_signal(screen, name: str, slot) -> None:
        signal = getattr(screen, name, None)
        if signal is None:
            return
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass

    def _connect_host_screen(self, host) -> None:
        """把**全部**屏的几何/DPI/刷新率变化接到每一张合成窗上。

        注意两件事，别按单窗形态脑补：
        1. **订阅范围**：每个宿主都订阅**所有**屏，不只是自己承载的那一块——缩放/
           几何变化会让判定翻转（单窗 ↔ 每屏一窗），任何一块屏的变化都可能需要整组
           重排，回调驱动的是全局同步，不是"只动自己"；
        2. **承载范围**：单窗形态下这张窗确实铺满整张虚拟桌面（一片窗含多块屏），
           每屏一窗形态下每张窗只取**自己那块屏的工作区**（见 `_host_window_rect`）
           ——本方法对两种形态都成立。下面几行说的是单窗形态的几何口径，多窗下
           窗口不铺整个虚拟桌面。

        窗覆盖整个虚拟桌面，所以任何一块屏的几何、任务栏、缩放变化都要重算窗口
        矩形与活动区——只挂家那块屏的话，别的屏在窗里留下的那块矩形会停在旧值。

        刷新率同理：tick 间隔取**全部**屏的最高档（``TickDriver._refresh_rate``），
        失效通知就必须覆盖同一范围。整片窗只归属一块屏（Qt 按窗口左上角判），
        只挂那块屏的 ``refreshRateChanged`` 会让副屏升/降刷静默失效——升刷时
        宠物卡在旧频率，降刷时继续按高频空转唤醒。
        """
        if host is None:
            return
        self._disconnect_host_screen(host)
        geometry = host._geometry_slot = (
            lambda *args: self._sync_host_geometry())
        dpr = host._dpr_slot = lambda *args: self._feed_screen_dpr()
        refresh = host._refresh_slot = (
            lambda *args: self._refresh_tick_interval())
        screens = host._signal_screens = list(self._screens())
        for screen in screens:
            for name in ("geometryChanged", "availableGeometryChanged"):
                self._connect_screen_signal(screen, name, geometry)
            for name in ("logicalDotsPerInchChanged",
                         "physicalDotsPerInchChanged"):
                self._connect_screen_signal(screen, name, dpr)
            self._connect_screen_signal(screen, "refreshRateChanged", refresh)

    def _disconnect_host_screen(self, host) -> None:
        geometry = getattr(host, "_geometry_slot", None)
        dpr = getattr(host, "_dpr_slot", None)
        refresh = getattr(host, "_refresh_slot", None)
        screens = getattr(host, "_signal_screens", ())
        host._geometry_slot = None
        host._dpr_slot = None
        host._refresh_slot = None
        host._signal_screens = ()
        for screen in screens:
            for name in ("geometryChanged", "availableGeometryChanged"):
                self._disconnect_screen_signal(screen, name, geometry)
            for name in ("logicalDotsPerInchChanged",
                         "physicalDotsPerInchChanged"):
                self._disconnect_screen_signal(screen, name, dpr)
            self._disconnect_screen_signal(screen, "refreshRateChanged", refresh)

    def _feed_screen_dpr(self) -> None:
        """某块屏的显示缩放变了：逐只按自己那块屏重喂 DPR + 配图缓存换尺寸。

        逐**宿主**喂：多窗（混合缩放）下每张窗的 DPR 各自不同，只喂主宿主时副屏
        那张窗里的精灵不会立刻换 DPI（几何变化的路径已经逐 host 喂过了，这里补齐
        缩放信号这条）。
        """
        if not self._hosts:
            return
        for each in list(self._hosts):
            each.overlay._feed_dpr()  # noqa: SLF001 - 同模块协作面
        self._refresh_self_talk_image_cache_for_dpr()
        self._reconcile_hosts_for_dpi()

    def _adopt_new_host(self, screen, *, visible: bool) -> None:
        """运行期补一张宿主窗：建窗 + 下发窗口级配置 + **继承**当前显隐。

        两条入口共用（缩放翻转补窗、热插屏补窗）。漏了 caps 那张窗停在出厂默认
        （关了置顶它照样盖在最上面、锁定位在副屏不生效）；漏了显隐继承会在全屏
        避让隐藏中把宠放出来——而 `_on_fullscreen_changed` 见 `_auto_hidden` 已置位
        就不会再 hide，那一屏的宠会一直露在游戏上面。
        """
        try:
            host = self._create_host(screen)
            self._apply_host_window_caps(host)
            if visible:
                host.overlay.show()
                host.overlay.start()
        except Exception:
            # **半成品宿主必须撤干净**：`_create_host` 先把宿主登记进 `_hosts`，
            # 之后的接线/能力下发/显示任一失败都会留下"登记了但没初始化好"的窗。
            # 完整性判据只认"该屏有没有宿主"，不清掉就会被当成补齐成功——不再重试，
            # 而且回位时会把宠搬进一张没 show 过的窗。
            partial = host if "host" in locals() else next(
                (candidate for candidate in self._hosts if candidate.screen is screen), None)
            if partial is not None and partial in self._hosts:
                self._discard_partial_host(partial)
            raise

    def _discard_partial_host(self, host) -> None:
        """撤掉一张建到一半的宿主：摘表、断信号、**把成员交回主宿主**、关窗。

        顺序讲究：先把成员与岛桥交回存活宿主，再关窗——否则精灵会留在一张已关闭的
        窗里（``_host_of`` 之后就找不到它），气泡跟随器也还挂着旧 overlay。
        关窗失败要留日志继续收尾，不能因为 ``hide()`` 抛了就跳过 ``close()``。
        """
        if host is None:
            return
        survivor = next((other for other in self._hosts if other is not host), None)
        if survivor is not None:
            for sprite in list(host.overlay.sprites):
                try:
                    rx, ry = self._body_ratio(sprite, host.bounds)
                    host.overlay.remove_sprite(sprite, release_clip=False)
                    survivor.overlay.add_sprite(sprite)
                    survivor.apply_activity(sprite)
                    sprite.home_screen = survivor.screen
                    self._place_body_ratio(sprite, rx, ry, survivor.bounds)
                    self._bind_position_listener(sprite)
                    self._rebind_bubble_follower(sprite, survivor)
                except Exception:
                    logging.exception("overlay: 撤宿主时交回成员失败")
        try:
            self._disconnect_host_screen(host)
        except Exception:
            logging.debug("overlay: 摘半成品宿主的屏信号失败", exc_info=True)
        if self.driver is not None:
            try:
                self.driver.detach(host.overlay)
            except Exception:
                logging.debug("overlay: 从驱动器摘半成品宿主失败", exc_info=True)
        try:
            host.overlay.hide()
        except Exception:
            logging.debug("overlay: 隐藏半成品宿主窗失败", exc_info=True)
        try:
            host.overlay.close()
        except Exception:
            logging.debug("overlay: 关闭半成品宿主窗失败", exc_info=True)
        if host in self._hosts:
            self._hosts.remove(host)
        self._applied_screen_rects.pop(_screen_name(host.screen), None)
        # 岛墙若绑在这张窗上，撤掉后必须改绑，否则桥引用的是已关闭宿主的世界。
        if getattr(self, "_island_host", None) is host:
            self._island_host = None
            self._rebind_island_host()
        # 会话通知是"按 HWND 注册、新的会顶掉旧的"（见 session_watcher 的注册口径）：
        # 这张窗在能力下发时可能已经把注册抢过去，撤掉后必须把存活宿主**显式登记
        # 回来**。不能只调 `_apply_host_window_caps`——置顶标志没变化时它会早退，
        # 早退路径不碰通知注册（用户设了 on_top=False 时就是这条）。
        if survivor is not None:
            try:
                self._apply_host_window_caps(survivor)
            except Exception:
                logging.debug("overlay: 撤宿主后恢复存活宿主的窗口能力失败", exc_info=True)
            try:
                self._register_session_notifications(survivor)
            except Exception:
                logging.debug("overlay: 撤宿主后重新登记会话通知失败", exc_info=True)

    def _capture_screen_ratios(self) -> list[tuple]:
        """记下每只的（屏归属，该屏内比例）；拓扑变化前调用，变化后按它回位。

        比例按**归属屏**的矩形算，不按宿主 ``bounds`` 算：单窗下 ``bounds`` 是整个
        虚拟桌面，多窗下才是单块屏——同一只宠在拓扑翻转前后用这两种口径量出来的
        比例不是一回事，直接拿宿主 bounds 会在拆窗时把它挪到主屏中间（实测
        1920 屏 + 1920 屏、身体中心 x=960，翻转后落到 x≈480）。
        """
        plan: list[tuple] = []
        for sprite in self._all_sprites():
            host = self._host_of(sprite)
            if host is None:
                continue
            assigned = self._assigned_screen(sprite) or host.screen
            if assigned is None or assigned not in self._screens():
                continue
            # 比例必须在**这只所在宿主的坐标系**里量。两种形态不能混用：
            # - 每屏一窗时，那张窗的原点就是该屏原点 → 归属屏在当地是 (0,0,w,h)；
            # - 单窗时所有屏共用一张窗，归属屏在当地是"可用区相对窗原点的平移"。
            # 而且这里不能读 `_multi_window()`：缩放信号到达时判定已经翻转，而
            # 精灵的坐标还是**旧**形态的（实测：拆窗时按新判定量出 0.25 而不是 0.5，
            # 宠从主屏正中掉到 25% 处）。
            rect = self._screen_rect_in_host(assigned, host)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            rx, ry = self._body_ratio(sprite, rect)
            plan.append((sprite, assigned, rx, ry))
        return plan

    def _remember_applied_screen_rect(self, screen, host=None) -> None:
        """登记某块屏**此刻真正被应用**的宿主窗局部矩形（比例定标的唯一旧值来源）。"""
        if screen is None:
            return
        name = _screen_name(screen)
        if host is None:
            host = self._host_of_screen(screen)
        if host is None:
            return
        self._applied_screen_rects[name] = (host, self._compute_screen_rect_in_host(screen, host))

    def _host_of_screen(self, screen):
        for host in self._hosts:
            if host.screen is screen:
                return host
        return self._main_host

    @staticmethod
    def _compute_screen_rect_in_host(screen, host) -> QRect:
        """现算：某块屏在指定宿主窗坐标系里的矩形（只看那张窗的原点，不看拓扑判定）。"""
        if screen is None or host is None:
            return QRect()
        origin = host.overlay.geometry().topLeft()
        return QRect(screen.availableGeometry()).translated(-origin.x(), -origin.y())

    def _screen_rect_in_host(self, screen, host) -> QRect:
        """某块屏在**指定宿主窗**坐标系里的矩形。

        取**上次已应用**的那份（``_applied_screen_rects``）：缩放信号到达时
        ``availableGeometry()`` 已经是新尺寸，而精灵坐标还是旧形态，现算会把旧坐标
        除以新宽高（实测 0.5 → 1.0，宠被扔到屏边）。缓存没有时才现算。
        """
        if screen is None or host is None:
            return QRect()
        # 首选**宿主当前活动区**里那块屏的矩形：它就是"上一次真正应用"的局部矩形
        # （物理/碰撞都在用它），缩放信号到达时它还是旧值，而 availableGeometry()
        # 已经变新了 —— 这正是比例定标需要的那份旧状态。
        for candidate, rect in getattr(host, "activity", ()) or ():
            if candidate is screen:
                return QRect(rect)
        cached = self._applied_screen_rects.get(_screen_name(screen))
        # 缓存要连**宿主身份**一起对：同名屏、或宿主被拆掉重建后，旧条目属于另一个
        # 坐标系原点，直接拿来量比例会错位。
        if cached is not None and cached[0] is host and not cached[1].isEmpty():
            return QRect(cached[1])
        return self._compute_screen_rect_in_host(screen, host)

    def _restore_screen_ratios(self, plan: list[tuple]) -> None:
        """按 :meth:`_capture_screen_ratios` 的结果把每只放回自己那块屏的同一位置。"""
        for sprite, assigned, _rx, _ry in plan:
            host = self.host_for_screen(assigned)
            if host is None or host is self._host_of(sprite):
                continue
            # 先交出宿主（这一步只保证"在哪块屏"，精确位置下一步写）
            self.move_sprite_to_screen(sprite, assigned)
        for sprite, assigned, rx, ry in plan:
            host = self.host_for_screen(assigned)
            if host is None:
                continue
            self._place_body_ratio(
                sprite, rx, ry, self._screen_rect_in_host(assigned, host))

    def _topology_needs_reconcile(self) -> bool:
        """宿主集合是否与当前拓扑判定不一致（**看集合，不只数数量**）。

        只判 ``len(hosts) > 1`` 会漏掉"补齐到一半"的状态：三屏补窗时第二张宿主
        创建失败，hosts 是 2、判定要 3，数量判据却认为"已经是多窗"而永久不再补。
        """
        want_multi = self._multi_window()
        if want_multi != self._active_multi():
            return True
        if not want_multi:
            return False
        return any(screen is not None
                   and not any(host.screen is screen for host in self._hosts)
                   for screen in self._screens())

    def _reconcile_hosts_for_dpi(self) -> None:
        """缩放变化会翻转「单窗 ↔ 每屏一窗」：把宿主集合与新拓扑对齐。

        ``_multi_window()`` 按平台、当前屏幕与跨屏开关判定目标形态。
        运行中改显示缩放（控制面板把副屏从 100% 拖到 125%）会让判定翻转，只喂
        DPR 会留下错位：单窗却按多窗算几何（副屏没有窗，那边的宠被按主宿主重新
        落位）、多窗却按单窗算（旧副屏宿主仍挂着成员，主窗又按整桌面铺开）。

        两个方向共用一套"先记归属屏 + 屏内比例，改完再回位"的事务：只回位被搬动
        的那几只是不够的——留在主宿主的宠会被 `_sync_host_geometry` 用**旧**主宿主
        的整桌面 bounds 量比例，再落进新主屏的单屏 bounds，位置直接跑偏。

        **有界循环，不是递归**：事务期间到达的信号会置 ``_topology_recheck``，
        这里循环重做；每轮都重新用**集合判据**判断，并且"这一轮有没有真推进"
        作为退出条件——递归会在每层重新拿到预算，遇到持续翻转能一路递归下去。
        循环结束仍有缺口时留一次 pending 并留日志（不静默吞掉）。
        """
        if self._main_host is None:
            return
        if self._topology_txn:
            # 同一事务已经在上层跑：不在一半状态（宿主建了/拆了但几何还没同步）
            # 上再开一层，否则 capture 会量到中间态的矩形。记一笔，上层收口时补做。
            self._topology_recheck = True
            return
        try:
            for _attempt in range(_TOPOLOGY_MAX_PASSES):
                if not self._topology_needs_reconcile() and not self._topology_recheck:
                    return
                self._topology_recheck = False
                if not self._run_topology_pass():
                    break  # 没推进就别空转
        finally:
            # 无论是"对齐完就 return"还是"跑满预算"，都要把事务里被拒写的那次拖拽
            # 位置补存掉（`finally` 保证早退路径不漏）。
            self._flush_deferred_geometry_save()
        if self._topology_needs_reconcile():
            # 收敛不了（例如某块屏的宿主建不起来）：留 pending 等下一次信号重试，
            # 并且**留一条日志**——静默失败会让"副屏没有窗"变成查不出原因的现象。
            self._topology_recheck = True
            logging.warning("overlay: 屏拓扑未能对齐（宿主 %d 张 / 需要 %s），下次屏信号再试",
                            len(self._hosts),
                            "每屏一张" if self._multi_window() else "一张")

    def _run_topology_pass(self) -> bool:
        """跑一轮拓扑事务；返回**这一轮有没有真的推进**（宿主集合变了）。"""
        want_multi = self._multi_window()
        before = [(host, host.screen) for host in self._hosts]
        # 先按**目标**形态置标志：建窗期 `_host_window_rect` 就要按目标算几何。
        # 这一轮若没做到（补窗失败），finally 里回落到实际张数——降级态必须让
        # 窗口、活动区、归属、落盘四者口径一致。
        self._multi_mode = bool(want_multi)
        self._topology_txn = True
        try:
            self._reconcile_hosts_locked(want_multi)
        finally:
            self._topology_txn = False
            applied = len(self._hosts) > 1
            changed = applied != self._multi_mode
            self._multi_mode = applied
            if changed:
                # 这一轮没达成目标（例如补窗失败）：按**实际**形态把几何重算一遍，
                # 否则会留下"窗口按多窗、活动区按单窗"的混合口径。直接调
                # `_apply_geometry` 而不是 `_sync_host_geometry`——后者会再次进入
                # 拓扑对齐，和本循环互相递归。
                self._apply_geometry()
        after = [(host, host.screen) for host in self._hosts]
        return before != after

    def _reconcile_hosts_locked(self, want_multi: bool) -> None:
        """拓扑重排事务本体（调用方已置 ``_topology_txn`` 闸）。"""
        # 先记下"正被拖的那只"：下面的几何事务会调 `_end_drag`，它会把
        # `_drag_relaxed_sprite` 清掉，之后再记就永远是 None。
        dragged = self._drag_relaxed_sprite
        plan = self._capture_screen_ratios()
        if want_multi:
            was_visible = any(host.overlay.isVisible() for host in self._hosts)
            missing = [screen for screen in self._screens()
                       if screen is not None
                       and not any(host.screen is screen for host in self._hosts)]
            for screen in missing:
                # 单块屏建窗失败不许把整次重排（以及调用它的屏信号）打崩：记下来、
                # 跳过，剩下的交给调用方的有界循环与"未能对齐"日志。
                try:
                    self._adopt_new_host(screen, visible=was_visible)
                except Exception:
                    logging.exception("overlay: 为 %s 建宿主窗失败",
                                      _screen_name(screen))
        else:
            home = self._main_host
            for host in list(self._hosts):
                if host is home:
                    continue
                self._dismantle_host(host, home.screen)
        self._sync_host_geometry()
        self._restore_screen_ratios(plan)
        # 宿主集合变了 ⇒ 岛墙绑的那张窗可能已经不在了（岛自己没有几何事件就不会
        # 自己重绑）。所有重排事务收口都补一次。
        self._rebind_island_host()
        # 事务里的 `_end_drag` 走过一次拖拽收尾，但那时拒写（防错误比例）。记下这只，
        # 事务收口后补存——"这次不写"是为了不写错，不是永远不写。
        if dragged is not None and self._geometry_save_after_txn is None:
            # 已有待补存记录时不覆盖：多轮事务里第一轮就记下了，而 `_drag_relaxed_sprite`
            # 在几何事务里已被清掉——这一轮的 `dragged` 是 None，覆盖会把它丢掉。
            self._geometry_save_after_txn = dragged

    def _flush_deferred_geometry_save(self) -> None:
        """拓扑事务收口后，把被拒写的拖拽位置补存一次（拓扑已对齐时才有意义）。

        **记录只在"真的存上"或"对象已经不存在"时才消费**：未对齐就原样留着等下一次
        信号——先清记录再判条件，会把"这次没对齐"变成"这次没存过"，补窗成功后也没人
        再补。
        """
        sprite = getattr(self, "_geometry_save_after_txn", None)
        if sprite is None or self._topology_txn:
            return
        if sprite not in self._all_sprites():
            self._geometry_save_after_txn = None   # 这只已经被关掉：记录作废
            return
        if self._topology_needs_reconcile():
            return                                 # 仍未对齐：留着，不消费
        try:
            saved = self._save_child_geometry(sprite)
        except Exception:
            logging.debug("overlay: 拓扑事务后补存拖拽位置失败", exc_info=True)
            return
        if saved:
            self._geometry_save_after_txn = None
        else:
            logging.debug("overlay: 拖拽位置补存未落盘（会被下次事务或关闭时再试）")

    def _refresh_tick_interval(self) -> None:
        """某块屏的刷新率变了（或屏集合变了）：重读刷新率重算 tick 间隔。

        QTimer 只是改间隔，不新增定时器、不轮询：通知到即重算，与驱动器
        ``refresh_tick_interval`` 的 M-1 语义（只改满速档，不打回降档）一致。
        """
        driver = self.driver
        if driver is not None:
            driver.refresh_tick_interval()

    def handle_geometry_changed(self, *args) -> None:
        """屏几何变化入口（真信号与测试直调共用）：几何同步 + DPR 重喂。"""
        self._sync_host_geometry()

    def _dismantle_host(self, dead, target_screen=None) -> None:
        """拆掉一张宿主窗：成员按比例并到存活宿主，再关窗、摘表、断信号。

        拔屏/模式翻转都走这里。少了它，被拔那块屏上的宠会留在**已失效的宿主**里
        ——窗口还在但屏没了，宠要么看不见，要么停在 Qt 自动搬去的错位置。
        """
        if dead is None or dead not in self._hosts or len(self._hosts) <= 1:
            return
        target = None
        if target_screen is not None:
            target = next((h for h in self._hosts if h.screen is target_screen
                           and h is not dead), None)
        if target is None:
            target = next((h for h in self._hosts if h is not dead), None)
        if target is None:
            return
        for sprite in list(dead.overlay.sprites):
            rx, ry = self._body_ratio(sprite, dead.bounds)
            dead.overlay.remove_sprite(sprite, release_clip=False)
            target.overlay.add_sprite(sprite)
            target.apply_activity(sprite)
            sprite.home_screen = target.screen
            self._place_body_ratio(sprite, rx, ry, target.bounds)
            # 换窗摘掉了位置监听（移除即 pop）：跟随面全部改挂到并进去的宿主。
            self._bind_position_listener(sprite)
            self._rebind_bubble_follower(sprite, target)
        self._disconnect_host_screen(dead)
        dead.overlay.hide()
        dead.overlay.close()
        if self.driver is not None:
            self.driver.detach(dead.overlay)
        self._hosts.remove(dead)

    def handle_screen_added(self, screen) -> None:
        """屏插入：窗口矩形与活动区重算（不迁宠——位置记忆归各宠的 ``screen_name``）。"""
        logging.info("overlay: screen added (%s)", _screen_name(screen))
        if not any(candidate is screen for candidate in self._screen_list):
            self._screen_list.append(screen)
        if (self._multi_window() and screen is not None
                and not any(h.screen is screen for h in self._hosts)):
            # 多窗：新插进来的屏也要有自己的窗，否则拖不过去。caps 与显隐两条
            # 继承口径与缩放翻转补窗完全一致（见 `_adopt_new_host`）。
            was_visible = any(host.overlay.isVisible() for host in self._hosts)
            self._adopt_new_host(screen, visible=was_visible)
        for each in list(self._hosts):
            self._connect_host_screen(each)
        self._sync_host_geometry()
        self._refresh_tick_interval()  # 屏集合变了，刷新率取数范围跟着变

    def handle_screen_removed(self, screen) -> None:
        """屏拔出：收尾在途拖拽 → 家那块屏没了就换家 → 重算窗口与活动区。

        拔掉的那块屏上的宠由几何同步按比例搬进仍在用的屏；连兜底屏都没有时
        活动区不变（它们本来就该跟着那块屏一起消失）。
        """
        logging.info("overlay: screen removed (%s)", _screen_name(screen))
        host = self._main_host
        if host is None:
            return
        self._end_drag()  # 拖拽中拔屏处置（v1.1 §10）
        self._screen_list = [s for s in self._screen_list if s is not screen]
        dead = next((h for h in self._hosts if h.screen is screen), None)
        if dead is not None:
            self._dismantle_host(dead, self._fallback_screen(screen))
        if host.screen is screen or host.screen not in self._screen_list:
            self._migrate_to_screen(self._fallback_screen(screen))
        for each in list(self._hosts):
            self._connect_host_screen(each)
        self._sync_host_geometry()
        self._refresh_tick_interval()  # 拔掉的那块屏不再参与刷新率取数
        # 岛的墙可能挂在刚被拆掉的那块屏的宿主世界里：拔屏后主动重绑一次，不等岛
        # 自己下一次几何事件（否则拔屏到岛下一次移动之间，宠撞不到岛）。
        self._rebind_island_host()

    def handle_primary_screen_changed(self, screen) -> None:
        """主屏变更：**不迁宠**（用户把哪块屏设成主屏是系统级操作，与桌宠落位无关；
        宠的位置由各自的 ``screen_name`` 记忆与拖拽决定）。只记日志。"""
        logging.info("overlay: primary screen changed -> %s", _screen_name(screen))

    def _fallback_screen(self, removed):
        """屏被拔除后的兜底屏：当前主屏；它也被拔了就取任一存活屏。"""
        screens = [s for s in self._screens() if s is not removed]
        primary = QGuiApplication.primaryScreen()
        if primary is not None and primary in screens:
            return primary
        return screens[0] if screens else None

    def _migrate_to_screen(self, new_screen) -> None:
        """把"家"挪到另一块屏（主宠的记忆指向别的屏、或家那块屏被拔掉）。

        单窗铺满整个虚拟桌面，换家**不重建窗口**：HWND、正在显示的气泡、岛墙与
        在途会话都留在原地，只换家的记录（关闭跨屏时它决定唯一活动区）再同步一次
        几何。
        """
        host = self._main_host
        if host is None or new_screen is None or new_screen is host.screen:
            return
        host.screen = new_screen
        self._sync_host_geometry()

    def _sync_host_geometry(self) -> None:
        """屏几何/可用区/拓扑变化：窗口矩形、活动区、成员位置与 DPR 一起换。

        顺序不能变（每一步都是一个已修过的实机缺陷）：

        1. **先按旧活动区取每只的比例**——换域会就地钳一次，旧比例当场消失；
        2. 再换窗口矩形与活动区（控制器与成员一起换域）；
        3. 最后按新矩形写落点——``set_pos`` 受当前域钳制，先写会被旧域截断
           （实测宽 1000→1600：比例落点 x≈1230 被旧边界钳成 744）；
        4. 原点真变时同步气泡跟随器与岛墙原点——重建跟随器会丢正在显示的气泡，
           不同步则气泡/墙停在旧全局坐标上。

        拔掉的那块屏上的宠落到家那块屏（同比例）；家也没了才落到整桌面。
        """
        host = self._main_host
        if host is None:
            return
        # 拓扑与判定不一致（缩放变化时几何信号可能先到、DPI 信号后到）：先把宿主
        # 集合补齐/拆掉，再由那次调用按新拓扑算几何。不先对齐就按**判定**选分支，
        # 单窗的整桌面 bounds 会被当成单屏比例分母——主屏正中的宠直接掉到 25%
        # 处，而且这次错误结果还会把活动区一起覆盖掉，后面的重排再也救不回原比例。
        if not self._topology_txn and self._topology_needs_reconcile():
            self._reconcile_hosts_for_dpi()
            return
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        """按**已应用形态**重算几何（``_sync_host_geometry`` 的对齐前置之后）。

        分支取 `_active_multi()`（模型标志），不取 `_multi_window()`（目标）：拓扑
        对齐失败时两者会不一致，按目标选分支会拿整桌面 bounds 当单屏分母，把位置
        算坏。
        """
        host = self._main_host
        if host is None:
            return
        multi = self._active_multi()
        if multi:
            # 多窗：每张窗跟**自己那块屏**的可用区走；探头边界同样用虚拟桌面
            # 拖拽中的会话先收尾：拖拽域（放宽到虚拟桌面）、投影、弹簧目标全是
            # 按旧几何算出来的，换域之后它们指向窗外，松手会把这只丢在无显示器的
            # 空洞里。
            for each in list(self._hosts):
                self._end_drag(each)
            for each in list(self._hosts):
                rect = QRect(each.screen.availableGeometry())
                if rect.isEmpty():
                    continue
                # 比例必须在换域**之前**取：``set_bounds`` 会就地按新域钳一次，
                # 那时旧比例已经没了（实测宽 1920→1600：比例被钳成绝对位置的比例）
                old_bounds = QRect(each.bounds)
                plan = [(sprite, *self._body_ratio(sprite, old_bounds))
                        for sprite in list(each.overlay.sprites)]
                old_origin = each.overlay.geometry().topLeft()
                each.overlay.setGeometry(rect)
                each.set_bounds(QRect(0, 0, rect.width(), rect.height()))
                self._remember_applied_screen_rect(each.screen, each)
                each.set_activity(self._host_activity(each))
                if each.probe is not None:
                    each.probe.set_bounds(self._probe_bounds(each))
                # 落点必须在换域**之后**写（``set_pos`` 受当前域钳制）
                for sprite, rx, ry in plan:
                    self._place_body_ratio(sprite, rx, ry, QRect(each.bounds))
                # 气泡跟随器/岛墙把窗原点缓存在自己身上：原点变了不同步就会留在
                # 旧全局坐标上（屏平移后气泡停在原地）
                self._sync_bubble_origin(each, old_origin)
                each.overlay._feed_dpr()  # noqa: SLF001 - 同模块协作面
            self._sync_sprite_screens()
            self._refresh_self_talk_image_cache_for_dpr()
            return
        desktop = self._desktop_bounds()
        if desktop.isEmpty():
            # 一块屏都不剩（屏全拔了/会话收尾中）：没有几何可算，保持现状等屏回来
            # ——按空矩形同步会把每只宠钳到 (0,0)。
            return
        old_origin = host.overlay.geometry().topLeft()
        plan = self._geometry_plan(host, multi=multi)
        host.overlay.setGeometry(desktop)
        host.set_bounds(QRect(0, 0, desktop.width(), desktop.height()))
        host.set_activity(self._activity_rects(multi=multi))
        # 单窗下每块屏在窗里的位置也是"已应用状态"，同样要刷新给下一次比例定标用。
        for screen in self._screens():
            self._remember_applied_screen_rect(screen, host)
        for sprite, rx, ry, target_rect in plan:
            self._place_body_ratio(sprite, rx, ry, target_rect)
        self._sync_bubble_origin(host, old_origin)
        host.overlay._feed_dpr()  # noqa: SLF001 - 同模块协作面（几何变化常连带 DPR）
        self._sync_sprite_screens()
        self._refresh_self_talk_image_cache_for_dpr()

    def _geometry_plan(self, host, *, multi=None) -> list:
        """(sprite, rx, ry, 新矩形)：几何变化前的**一次性取数**，必须在换域之前。

        比例取自换域之前的身体位置（换域会就地钳一次，那时旧比例已经没了）；新
        矩形按**同一块屏**认——旧活动区里含身体中心的那一块认定屏，那块屏没了
        （拔屏）就落到家那块屏，家也没了就落到整张窗。
        """
        desktop = self._desktop_bounds()
        new_rects = {screen: rect for screen, rect in self._activity_rects(multi=multi)}
        fallback = new_rects.get(self._screen, QRect(0, 0, desktop.width(),
                                                     desktop.height()))
        plan = []
        for sprite in list(host.overlay.sprites):
            body = sprite.body_rect()
            cx = body.x() + body.width() / 2.0
            cy = body.y() + body.height() / 2.0
            source = None
            target = fallback
            for screen, rect in host.activity:
                if QRectF(rect).contains(cx, cy):
                    source = QRect(rect)
                    target = new_rects.get(screen, fallback)
                    break
            if source is None:
                source = QRect(host.bounds)
            rx, ry = self._body_ratio(sprite, source)
            plan.append((sprite, rx, ry, target))
        return plan

    def _sync_bubble_origin(self, host, old_origin: QPoint) -> None:
        """某宿主 overlay 全局原点变化后同步"按全局原点取坐标"的消费方。

        原点 = ``overlay.geometry().topLeft()``，两个消费方都把它缓存在别处：

        - 该屏宠物的气泡跟随器（``SpriteBubbleFollower.__init__`` 缓存；
          ``set_origin`` 会在原点真变时把在显气泡重定位一次——同尺寸平移屏下
          sprite 局部位置不变、没有位移通知，不同步则气泡停在旧全局位置）；
        - 岛墙桥（``IslandWindowBridge.set_origin``：更新原点 + 作废岛速基线 +
          立刻按新原点重算并重登记局部几何，否则墙留在旧原点、碰撞位置整体偏移）
          ——只在岛挂在本宿主上时。

        仅原点真变时调用：任务栏等"只有 available 变化"的场景原点不变，两个消费
        方都不产生任何多余动作；无跟随器/无桥（岛碰撞关、无岛）时整段跳过，
        不凭空建桥、不重建气泡。
        """
        origin = host.overlay.geometry().topLeft()
        if origin == old_origin:
            return
        for follower in self._host_bubble_followers(host):
            try:
                follower.set_origin(origin)
            except Exception:
                logging.debug("overlay: 气泡跟随原点同步失败", exc_info=True)
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None and getattr(self, "_island_host", None) is host:
            try:
                bridge.set_origin(origin)
            except Exception:
                logging.debug("overlay: 岛墙原点同步失败", exc_info=True)

    def _host_bubble_followers(self, host) -> list:
        """挂在该宿主上的气泡跟随器（主 + 逐只子宠）。"""
        followers = []
        for sprite, follower in self._all_bubble_followers():
            if follower is None:
                continue
            if self._host_of(sprite) is host:
                followers.append(follower)
        return followers

    def _all_bubble_followers(self) -> list:
        """(sprite, follower) 全表（主宠 + 逐只子宠）。"""
        items = [(self.sprite, getattr(self, "_bubble_follower", None))]
        items.extend(self._bubble_followers.items())
        return [(sprite, follower) for sprite, follower in items if sprite is not None]

    def _end_drag(self, host=None) -> None:
        """拖拽中拔屏/迁移：按当前位置松手收尾，清掉窗口的 grab 状态。

        收尾一律走窗口既有的 ``_finish_grab`` 协议，本方法只提供"按当前位置
        松手"的位置这一个输入。自己清字段（旧实现）漏掉三件事：click-only /
        拖拽提交旗标留在窗上、穿透轮询的拖拽降频（``set_drag_active``）没复位、
        壳层的收尾通知收不到事件——最后一笔最致命，壳层被 grab 闸住的穿透收敛
        （``_converge_pending``）就此永久滞留。

        ``host`` 只为既有调用点保留（单窗下恒为唯一宿主）。
        """
        overlay = host.overlay if host is not None else self.overlay
        grab = getattr(overlay, "_mouse_grab", None)
        if grab is None:
            return
        try:
            overlay._finish_grab(QPointF(grab.pos))
        except Exception:
            logging.exception("overlay: 屏事件收尾拖拽失败")

    # ---------------------------------------------------------------- 会话/电源（O4）
    def _install_session_watcher(self) -> None:
        try:
            self._session_watcher = install_session_watcher(
                app=self.app, on_session_end=self._on_session_end,
                on_suspend_change=self._on_suspend_changed)
        except Exception:
            logging.exception("overlay: 安装会话结束探测器失败")
            self._session_watcher = None
        self._register_session_notifications()
        driver = getattr(self, "driver", None)
        if driver is not None:
            # 用户输入自愈挂起时走完整解除路径（含预热续跑），不只降档复位
            driver.on_user_resume = lambda: self._on_suspend_changed(False, "user-input")

    def _register_session_notifications(self, host=None) -> None:
        """对宿主窗口句柄注册锁屏通知（Windows；失败降级为不支持并记日志）。

        锁屏消息（WM_WTSSESSION_CHANGE）只发给注册过的窗口，注册必须落在真实
        HWND 上（``winId()`` 会按需创建原生窗口）。失败不影响挂起/恢复探测
        （WM_POWERBROADCAST 是广播）与关机探测（原生事件过滤器照旧）。

        ``host=None`` = 主宿主（旧入口）。副屏窗口不重复注册：会话通知是进程级
        的，主宿主那一份够用（``set_on_top`` 重建任一原生窗口后按该宿主补注册）。
        """
        watcher = getattr(self, "_session_watcher", None)
        register = getattr(watcher, "register_session_notifications", None)
        if not callable(register):
            return
        owner = host if host is not None else self._main_host
        window = getattr(owner, "overlay", None)
        if window is None:
            return
        try:
            register(int(window.winId()))
        except Exception:
            logging.debug("overlay: 注册锁屏通知失败", exc_info=True)

    def _on_suspend_changed(self, active: bool, reason: str = "") -> None:
        """锁屏/挂起状态变化（会话/电源事件接线，O4）。

        只降档：``driver.set_suspended`` 强制/解除 T3（True 时逐 sprite
        ``pause_clip``，False 时续播 + 同帧回全速），**不**隐藏 overlay、**不**动
        全屏/光标 watcher——唤醒后可见性与交互面必须原样。预热同拍成对停/续
        （锁屏期间预热的 ffmpeg 没有可见收益；解锁必须补回，否则切动画退化到
        冷首帧）。异常一律吞掉：事件过滤器在原生消息链上，绝不因桌宠内部错误
        打断 Qt 事件循环。
        """
        logging.info("overlay: 锁屏/挂起状态变化 active=%s reason=%s", active, reason)
        try:
            driver = getattr(self, "driver", None)
            if driver is not None:
                driver.set_suspended(bool(active))
        except Exception:
            logging.exception("overlay: 锁屏/挂起降档失败")
        try:
            if active:
                self._pause_warm_all()
            else:
                self._resume_warm_all()
        except Exception:
            logging.debug("overlay: 锁屏/挂起预热成对切换失败", exc_info=True)

    def _on_session_end(self) -> None:
        """会话结束（关机/注销）：停止本路径全部 ffmpeg reader。

        spawn 闸门由 SessionWatcher 自身置位（先闸门后回调，顺序不可颠倒）；
        这里只做 watcher 不管的部分：停全部 clip + 停 tick。幂等。"""
        if self._session_end_done:
            return
        self._session_end_done = True
        libs = [self.lib] + [lib for lib in getattr(self, "_spawned_libs", {}).values()]
        for lib in libs:  # 每只子肥鱼各持独立库（M8）：关机窗口一并停 reader
            stop_all = getattr(lib, "stop_all_clips", None)
            if callable(stop_all):
                try:
                    stop_all()
                except Exception:
                    logging.exception("overlay: 会话结束停止素材库 clip 失败")
        self._stop_all_hosts()
