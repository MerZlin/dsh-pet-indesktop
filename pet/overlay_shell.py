# -*- coding: utf-8 -*-
"""Phase 4.1a 产品壳：overlay 拓扑（单合成窗）的 AppShell 挂载层。

设计稿：.scratch/single-overlay-window/PHASE4_DESIGN.md（T1/T4/T5 + §5 4.1a 行
+ v1.1 §10 屏热插拔/会话事件升级项）。本模块只做骨架与生命周期：

- 主屏 OverlayWindow + 主 sprite（MovieLibrary 经主 PetInstance._create_library
  创建——per-pet 库是 T3 定论，禁止跨 sprite 共享 clip 对象）；
- 进程级一组三控制器（BehaviorController/SpriteCollisionWorld/
  ThrowPhysicsController），挂在**统一 tick 驱动器**（tick_driver.TickDriver，
  M-2/T2）上：驱动器持有控制器与 tick 时钟，调
  ``tick_sim(全部 overlays 的 sprites, dt)``，tick 顺序协议语义不变
  （行为 → 碰撞 → 抛掷物理）；overlay 只负责 advance+paint/脏矩形。
  接线口径同 .scratch/single-overlay-window/run_overlay_demo.py；
- 屏事件：screenAdded/screenRemoved/primaryScreenChanged/geometryChanged →
  overlay 重建或几何同步，sprite 位置按 rx/ry（中心相对可用区比例）语义
  迁移；主屏 DPR 变化重喂 sprite.set_dpr；拖拽中拔屏先收尾拖拽再迁移；
- 会话结束（Windows 关机/注销）：复用 session_watcher 的闸门与信号链，
  收口本路径 MovieLibrary 的全部 clip（issue #111 等价链，D10 落点）。

T5 切换策略：PET_RENDER_TOPOLOGY=overlay 环境变量是开发期一次性分流 flag，
读取收口在本模块 is_overlay_topology()，不进 Config/设置页/schema。
4.1a 明确不做：多 sprite（4.2）、位置持久化（4.2a，本刀恒走默认右下角）、
岛/气泡/聊天/菜单全量 parity（4.1b/4.1c）、捕获模式切换（T4 后续）。

4.2b（本刀后段）：多 sprite 生命周期按 D5/D6 落地——spawn 分配 slot 身份并写
活跃宠清单（``overlay-active-pets.json``，运行时状态文件）；退出即出清单
（slot 配置保留）；重启严格按清单复活（含各自 rx/ry/facing/scale）；主宠退出
按 app.py P1-3 语义提升列表首只子宠为主（接管主身份/持久化身份）。清单、无锁
身份分配、每身份几何读写的纯逻辑在 ``overlay_spawn_state``（零 Qt，便于单测）。

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
import threading
import time
import weakref
from collections import deque
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QBitmap, QIcon, QImage, QPixmap, QRegion
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from . import autostart as autostart_mod
from . import catalog
from . import click_sound
from . import overlay_settings_command
from . import overlay_spawn_state
from . import slot_manager
from . import window_alerts
from .context_menus.shared import release_menu_tree
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
    BehaviorController,
)
from .sprite_bubble import SpriteBubbleFollower, sprite_anchor_rect_global
from .sprite_collision import SpriteCollisionWorld
from .sprite_physics import ThrowPhysicsController
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

    def visible_content_rect(self) -> QRect:
        """身体框全局矩形（气泡锚点，口径同 SpriteBubbleFollower.anchor）。"""
        overlay = getattr(self._shell, "overlay", None)
        if overlay is None:
            return QRect()
        return sprite_anchor_rect_global(self._sprite, overlay.geometry().topLeft())

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
# - 子 sprite → 它自己的 slot 文件，**缺文件/缺键回退主配置**（旧启动语义是
#   "缺文件按主配置落种"）；
# - 窗口级键不在此列（``on_top`` / ``mouse_through`` / ``pet_opacity`` /
#   ``lock_position`` / ``shift_drag`` / ``cursor_hidden_passthrough`` /
#   ``auto_hide_fullscreen`` / ``no_move`` / ``animation_gap_seconds`` /
#   ``stream_capture_mode``）：一次只有一个合成窗，这些键物理上无法 per-pet
#   （审计 C4），继续按主配置**全局**生效。
PER_SLOT_SETTING_KEYS = frozenset({
    # 几何 / 运动
    "scale", "playback_speed", "drag_physics", "throw_strength",
    "ffmpeg_recycle_minutes",
    # 音效（点击与碰撞各一对门/音量；音源包 click_sound_pack 两者共用）
    "click_sound_enabled", "click_sound_volume", "click_sound_pack",
    "collision_sound_enabled", "collision_sound_volume",
    # 气泡 / 自言自语族
    "self_talk_enabled", "self_talk_texts", "self_talk_duration_seconds",
    "self_talk_image_dir", "self_talk_image_scale", "self_talk_image_chance",
    "self_talk_min_interval", "self_talk_max_interval",
    "self_talk_speak_enabled", "self_talk_bubble_style", "bubble_text_scale",
    "click_show_balance", "click_show_self_talk",
    # 角色（per-pet 素材库）
    "character",
})


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
    """一只 sprite 的配置视图（旧"每宠各读自己 config"的单进程等价物）。

    ``get`` 对 :data:`PER_SLOT_SETTING_KEYS` 里的键逐 sprite 取源
    （子 sprite 缺键回退主配置）；其余键（窗口级/进程级/角色包级）原样转发主
    配置——旧架构里每窗读到的也是同一份文件里的同一批值。其余属性面
    （``character_alias`` / ``character_display_name`` / ``click_talk_texts_for``
    / ``dir`` / ``instance_id`` / ``save`` …）经 ``__getattr__`` 转发主配置。
    """

    def __init__(self, shell, sprite) -> None:
        self._shell = shell
        self._sprite = sprite
        # 构造期读一次（配置同步边界才重建视图，见 _invalidate_sprite_configs）：
        # 主配置可能有几十 KB，逐键现读文件会把点击/碰撞事件链拖进磁盘税。
        self._slot_data = shell._read_slot_data(sprite)

    def get(self, key, default=None):
        data = self._slot_data
        if data and key in PER_SLOT_SETTING_KEYS:
            value = data.get(key)
            if value is not None:
                return value
        if key == "collision_enabled":
            # 宠物间总碰撞资格位有专属读点（缺键按缺省 true，与"缺键回退主配置"
            # 的其余键不同口径），单一来源避免两套真值。
            return self._shell._sprite_collision_enabled(self._sprite)
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
        return sprite_anchor_rect_global(self._sprite, overlay.geometry().topLeft())

    def show_bubble(self, text: str, duration_ms: int = 3200, **_kwargs) -> bool:
        if self._bubble_blocked():
            return False  # 飞行期不弹（飞的那只禁泡，见 OverlayShell._bubble_blocked）
        return self._shell._show_bubble_text_for(
            self._sprite, str(text), int(duration_ms), **_kwargs)

    def hold_bubble(self, seconds: float) -> None:
        self._shell.hold_bubble(seconds)

    def hide_bubble(self) -> None:
        self._shell._hide_bubble_for_sprite(self._sprite)


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
    """唯一拓扑判定入口（T5：dev flag，不进 Config/设置页/schema）。

    实现在零 Qt 的 ``overlay_settings_command``：设置进程（``--settings``，
    ``pet/__main__.py`` 明确禁止导入 pet.app/overlay_shell）也要按拓扑决定
    D12 指令通道走不走，故 env 读取的实现必须落在两侧都能 import 的模块；
    本函数保留为对外唯一入口名（app.py/overlay_instance_gate 照旧转发）。
    """
    return overlay_settings_command.is_overlay_topology()


class _SettingsIdentityConfig:
    """身份载体的 config 面（``open_settings_process`` 只读 ``instance_id``）。"""

    __slots__ = ("instance_id",)

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id


class _SettingsIdentity:
    """D13：喂给 ``AppShell.open_settings_process`` 的身份载体（鸭子类型）。

    该入口只读 ``instance.config.instance_id``（app.py:1518-1523）：主身份与
    进程级 ``DSH_PET_INSTANCE`` 同值 → 命令逐字不变；子 sprite 的 ``slot-N``
    不等于 env → 追加 ``--instance slot-N``，独立设置进程因此打开该子肥鱼的
    ``config-slot-N.json`` 而不是静默打开主宠配置。这里不建 Config、不碰磁盘。
    """

    __slots__ = ("config",)

    def __init__(self, instance_id: str) -> None:
        self.config = _SettingsIdentityConfig(instance_id)


def _screen_name(screen) -> str:
    try:
        return str(screen.name())
    except Exception:
        return "?"


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
        self.behavior = None  # OverlayShell 挂载；contextMenuEvent 查表读它
        self.edge_probe = None  # OverlayShell 挂载；拖拽/点击事件接线
        # 真拖拽升级（过 DRAG_THRESHOLD）才通知探头取消会话（旧机语义：
        # 按下只是点击候选，探头会话的点击拉直因此才有机会生效——按下即
        # 取消会让 on_sprite_clicked 永远遇到 mode==OFF）。
        self._drag_committed_cb = self._on_real_drag_started
        self.setAcceptDrops(True)  # 4.1c 投喂（命中 sprite 才 accept）

    def _on_real_drag_started(self, sprite) -> None:
        if self.edge_probe is not None:
            self.edge_probe.on_sprite_drag_started(sprite)

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
        if grab is None or press is None or self.behavior is None:
            return
        threshold = catalog.DRAG_THRESHOLD * getattr(grab, "scale", 1.0)
        # click_only：锁定位/未按 SHIFT 的按下（M5a/M5b）——位移再大也只算点击，
        # 与旧机两处闸门「取消拖拽但保留点击」语义一致
        if click_only or (not committed
                          and (event.position() - press).manhattanLength() < threshold):
            # 边缘探头消费点击（PEEKING 拉直/STRAIGHTENED 重置倒计时）时，
            # 抑制点击反应与点击音效——拉直本身就是反馈
            if self.edge_probe is not None and self.edge_probe.on_sprite_clicked(grab):
                return
            # M5e：点击触发黄金回旋（window.py:3356-3357 的同位置路由）。
            # 返回 True = 本次点击被效果层消费（直连模式），不再播点击反应/音效
            route_spin = getattr(self, "_click_route_spin", None)
            if callable(route_spin) and route_spin(grab):
                return
            clicked = self.behavior.on_sprite_clicked(grab)
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
                anim_of = getattr(self.behavior, "anim_of", None)
                click_name = anim_of(grab) if (clicked and callable(anim_of)) else None
                click_cb(grab, str(click_name or ""))
        elif self.edge_probe is not None:
            # 真拖拽释放（非单击）：通知探头按 tick 静止判定重新评估进入
            self.edge_probe.on_sprite_drag_released(grab)
            # F1：真拖拽松手切回待机池（旧 window.py:3260-3264）。点击候选
            # 分支不走这里——它由 on_sprite_clicked 改绑 click clip；无 click
            # 素材时控制器的接管态自愈会在下一 tick 收回待机（不卡悬空动画）。
            self.behavior.on_drag_released(grab)
        else:
            # 无探头世界：真拖拽松手的待机收尾同样要走到（F1）
            self.behavior.on_drag_released(grab)

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
        """按原位置重开右键菜单（模板切换立即生效；window.py 同名方法等价物）。

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
        self._config = instance.config
        self._screen = screen if screen is not None else app.primaryScreen()
        self._sprite_factory = sprite_factory or (
            lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
        self._started = False
        self._session_end_done = False
        # 音效预热一次性 latch：只在首次 start、overlay 可见前预热一次
        #（_warm_sound_effects_once）；重 show / stop→start 不重复。
        self._sound_warmed = False
        self.behavior: BehaviorController | None = None
        self.collision: SpriteCollisionWorld | None = None
        self.physics: ThrowPhysicsController | None = None
        self.driver: TickDriver | None = None
        self.overlay: ShellOverlayWindow | None = None
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
        self._bounds = QRect()
        # 4.2b 子肥鱼登记：sprite 顺序 = spawn 顺序 = 活跃宠清单顺序
        self._spawned: list = []
        self._spawned_libs: dict = {}
        self._spawned_slots: dict = {}
        # B7b per-slot 取源：逐 sprite 的配置视图（配置同步边界重建）与子 sprite
        # 的各自主气泡跟随器/自言自语宿主/ pet 形宿主。
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
        self._golden_spin_host = _GoldenSpinSpriteHost(self)
        self._golden_spin_target = None
        # 4.3 后半：共享子系统注入（等价 PetWindow 的构造参数；None = 惰性空转）
        self.agent_link_manager = agent_link_manager
        self.proactive_watcher = proactive_watcher
        # 呈现/提醒状态（PetWindow 同名私有面的 sprite 等价物；window_alerts 的
        # host 形函数与 agent_link/proactive 的聚合读取都直接读这些属性）
        self._alert_queue: deque = deque()
        self._alert_current: dict | None = None
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

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        self._bounds = self._local_bounds(self._screen)
        self.behavior = BehaviorController(self._bounds)
        # 飞行边沿（B1）：行为层只报"进/出飞行"，气泡门禁归壳（飞行集合 +
        # _bubble_blocked）。挂在这里与其它 behavior 接线同点；屏迁移只重建
        # overlay，控制器不重建，故这一处就够。
        self.behavior.on_flight_changed = self._on_sprite_flight_changed
        self.collision = SpriteCollisionWorld()
        self.physics = ThrowPhysicsController(self._bounds)
        # 4.1c 音效：点击 + 碰撞（click_sound 默认包，静默降级）。门/音量/音源都按
        # **触发 sprite** 的身份配置取源（B7b）：碰撞事件带成员 id，由壳解析成
        # sprite；点击由壳在 _on_sprite_click(sprite) 里带身份调用。
        self._sound = SpriteSoundPlayer(
            self._config, self.collision,
            config_for=self._sprite_config,
            sprite_for_event=self._sound_sprite_for_event)
        self.collision.add_collision_listener(self._sound.on_collision)
        # 4.1c 碰撞 Q 弹：真撞击量级 → 双方 sprite 挤压
        self.collision.add_collision_listener(self._on_collision_squash)
        # M-2 统一 tick 驱动器（T2）：进程级一组控制器挂在驱动器上，overlay
        # 只做 advance+paint；屏迁移重建 overlay 时复用同一驱动器（成员替换）。
        self.driver = TickDriver(self)
        self.driver.set_controllers(self.behavior, self.collision, self.physics)
        # 边缘探头（edge_probe 移植）：第四控制器挂 tick 尾段（姿态最终写
        # 入口）；config 键 edge_probe_enabled（默认 False）世界内每次进入
        # 判定前热读，设置页开关即切即生效
        from .sprite_edge_probe import create_edge_probe_world
        self._probe = create_edge_probe_world(self._config, QRect(self._bounds))
        self.driver.add_extra_controller(self._probe)
        self.collision.add_collision_listener(self._on_collision_probe)
        # throw_egg 彩蛋：探头被撞飞头部跟随速度（extras 尾段，读物理结算后
        # 的速度——落地兜底依赖 physics 已切回 normal，顺序天然满足）
        from .sprite_throw_egg import create_throw_egg_world
        self._throw_egg = create_throw_egg_world(
            QRect(self._bounds), probe=self._probe)
        self.driver.add_extra_controller(self._throw_egg)
        self.overlay = ShellOverlayWindow(self._screen, driver=self.driver)
        self.overlay.behavior = self.behavior
        self.overlay.edge_probe = self._probe
        self.overlay.click_feedback = self._sound.on_click
        # 点击气泡族（余额/点击自言自语）+ 点击音效：与 click_feedback 同位置注入
        self.overlay._on_sprite_click = self._on_sprite_click
        # M5e：点击触发黄金回旋路由（与 _on_sprite_click 同位置注入）
        self.overlay._click_route_spin = self._route_click_golden_spin
        # 4.1c 弹弓：controller 由 OverlayWindow 自持，这里只接 config
        # （slingshot_enabled 热读，设置页即改即生效）
        self.overlay.slingshot.config = self._config
        # 4.1c 全量右键菜单（facade 适配旧 context_menus 建造器）
        from .sprite_menu_facade import build_sprite_full_menu
        self.overlay._full_menu_builder = (
            lambda target: build_sprite_full_menu(self, target))
        self.lib = self._create_main_library()
        # 首跑帧序列自动供给（B 档）：口径同 app._create_library，库内幂等
        getattr(self.lib, 'maybe_provision_frameseq', lambda: None)()
        scale = float(self._config.get("scale") or catalog.DEFAULT_SCALE)
        self.sprite = self._sprite_factory(self.lib, QPointF(0, 0), scale)
        self.sprite.home_screen = self._screen
        # DPR 由 overlay.add_sprite 按所在屏统一喂（V-11 收口，不再双喂）
        self.sprite.set_bounds(QRect(self._bounds))
        self.overlay.add_sprite(self.sprite)
        # 4.2b：sprite 移除时注销行为状态（spawn/退出子肥鱼的清理链）
        self._wire_sprite_removed_hooks(self.overlay)
        # 4.2a：按 rx/ry 比例恢复上次位置（无记录 → 默认右下角）
        self._restore_position()
        # 4.2b：按活跃宠清单复活子肥鱼（D5：依据是运行时清单，不是 slot 配置存在）
        self._restore_spawned_pets()
        # 4.1c 投喂（拖文件喂 sprite，命中判定与穿透同口径）
        self._bind_feeding()
        # 4.1b 窗口能力：on_top/穿透复合/全屏与光标监视/runtime 避让标记
        self._auto_hidden = False
        self._user_mouse_through = bool(self._config.get("mouse_through", False))
        self._auto_cursor_hidden = False
        self._cursor_restore_pending = False
        self._watcher = FullscreenCursorWatcher(self)
        self._watcher.fullscreen_changed.connect(self._on_fullscreen_changed)
        self._watcher.cursor_visibility_changed.connect(
            self._on_cursor_visibility_changed)
        self.overlay._through_changed = self._on_user_through_changed
        self.overlay._grab_finished_cb = self._on_grab_finished
        self.overlay.add_position_listener(self.sprite, self._on_main_sprite_moved)
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

    def _wire_sprite_removed_hooks(self, overlay) -> None:
        """挂 sprite-removed 三条注销监听（``_build`` 与屏迁移共用）。

        ``overlay.remove_sprite`` 按这三条注销外部簿记：行为状态表
        （``behavior.forget``）、边缘探头 armed/预进入状态（``probe.forget``）、
        抛掷彩蛋角度（``throw_egg.forget``）。迁移重建 overlay 时漏挂，则
        屏热插拔/主屏切换之后再退出任意一只宠都不再注销——每退一只泄漏一份
        强引用（含 pixmap/命中图）与探头状态。
        """
        overlay.add_sprite_removed_listener(self.behavior.forget)
        overlay.add_sprite_removed_listener(self._probe.forget)
        overlay.add_sprite_removed_listener(self._throw_egg.forget)

    def _create_main_library(self):
        """per-pet MovieLibrary（T3）；角色素材缺失回退默认角色（口径同
        AppShell._create_ui_with_character_fallback）。"""
        return self._create_library_for(
            str(self._config.get('character', catalog.DEFAULT_CHARACTER)))

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
            (fallback_writer or self._config.set)('character',
                                                  catalog.DEFAULT_CHARACTER)
            return self._instance._create_library(catalog.DEFAULT_CHARACTER)

    @staticmethod
    def _local_bounds(screen) -> QRect:
        """屏幕可用工作区换算成 overlay 局部坐标（overlay 铺满屏幕几何）。"""
        geo = screen.geometry()
        avail = screen.availableGeometry()
        return QRect(avail.x() - geo.x(), avail.y() - geo.y(),
                     avail.width(), avail.height())

    @staticmethod
    def _default_corner_pos(bounds: QRect, rect: QRect) -> QPointF:
        """默认右下角（对齐 window_placement._default_corner_pos 的旧算式：
        右缘留 CORNER_MARGIN、底贴可用区底）。"""
        return QPointF(bounds.x() + bounds.width() - 1 - rect.width() - catalog.CORNER_MARGIN,
                       bounds.y() + bounds.height() - 1 - rect.height())

    # ---------------------------------------------------------------- 主 sprite 接线
    def _bind_feeding(self) -> None:
        """投喂控制器只认主 sprite；overlay/主 sprite 更换后必须重挂，
        否则投喂命中判定仍打在旧 sprite 的几何上（4.2b 主实例提升同理）。"""
        from .sprite_feeding import SpriteFeedingController
        self._feeding = SpriteFeedingController(
            self.overlay, self.sprite, self._config, self.behavior)
        self._feeding._bubble_cb = self._say_feeding_bubble
        # 解读询问接缝由壳持有：重建控制器时回填，否则主宠提升/屏迁移之后
        # 拖文件解读静默失效。
        self._feeding.interpret_offer = getattr(self, "_file_interpret_offer", None)
        self.overlay._feeding = self._feeding

    def set_file_interpret_offer(self, offer) -> None:
        """注入拖文件解读询问回调（app 侧 ``FileInterpretController.offer``）。

        overlay 拓扑下 PetWindow 不构造，``file_eater`` 的 ``interpret_offer``
        接缝没有别的宿主：AppShell 持有解读控制器，经本方法把 ``offer(paths)``
        挂到当前投喂控制器上（``sprite_feeding`` 吃完后调用，口径同旧
        ``file_eater.eat_paths:183-186``）。传 None = 解除。
        """
        self._file_interpret_offer = offer
        feeding = getattr(self, "_feeding", None)
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
        self._bubble_follower = self._make_bubble_follower(self.sprite)
        self._bind_main_bubble_signals()
        for sprite in list(self._spawned):
            self._ensure_sprite_bubble(sprite)

    def _bind_main_bubble_signals(self) -> None:
        """主气泡的 hidden 信号/可点态接线（构造后与"提升者跟随器接管"共用）。"""
        bubble = self._speech_bubble
        if bubble is not None:
            self._apply_bubble_text_scale(bubble)
            try:
                bubble.hidden_signal.connect(self._on_speech_bubble_hidden)
            except (AttributeError, RuntimeError, TypeError):
                logging.debug("overlay: 气泡 hidden 信号接线失败", exc_info=True)
        if self._quick_chat_resolved:
            # 快速对话可用性已解析过（不重付 import 成本）：新气泡直接对齐
            # 可点状态；否则等首次冒泡时由 show_bubble 的
            # _apply_bubble_interactive 惰性解析（与 legacy 触点一致）。
            self._apply_bubble_interactive()

    def _make_bubble_follower(self, sprite):
        """按 sprite 的**自己那份**配置建一只气泡跟随器（风格/字号 per-slot）。"""
        cfg = self._sprite_config(sprite)
        follower = SpriteBubbleFollower(
            self.overlay, sprite, on_clicked=self._on_bubble_clicked,
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

    def _show_bubble_text_for(self, sprite, text: str, duration_ms: int,
                              **kwargs) -> bool:
        """把一条气泡发给**指定 sprite** 的那只气泡（主 sprite 走壳的原路径）。

        该 sprite 没有跟随器（未建/建失败/已退出）时回退主气泡——气泡是提醒/
        答复的呈现面，宁可降级到主宠头顶也不静默吞掉。
        """
        if sprite is not None and sprite is not getattr(self, "sprite", None):
            follower = self._bubble_followers.get(sprite)
            if follower is not None:
                return follower.show(str(text), int(duration_ms), **kwargs)
            logging.debug("overlay: 目标 sprite 无气泡跟随器，回退主气泡")
        return self._show_bubble_text(text, duration_ms, **kwargs)

    def _hide_bubble_for_sprite(self, sprite) -> None:
        """收起指定 sprite 的气泡（不影响其它 sprite 的气泡）。"""
        follower = self._bubble_for(sprite)
        if follower is not None:
            follower.hide()

    def _sprite_speech_visible(self, sprite) -> bool:
        """该 sprite 的"可冒泡"态：整窗可见（含全屏自动隐藏）且这只自己没被隐藏。"""
        if not self.isVisible():
            return False
        return self._sprite_visible(sprite)

    def _sprite_host(self, sprite):
        """sprite 的 pet 形宿主（余额/识屏等 host 形消费方用；主 = 壳本身）。"""
        if sprite is None or sprite is getattr(self, "sprite", None):
            return self
        host = self._sprite_hosts.get(sprite)
        if host is None:
            host = _SpritePetHost(self, sprite)
            self._sprite_hosts[sprite] = host
        return host

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
    def _read_slot_data(self, sprite) -> dict | None:
        """该 sprite 的 slot 配置原始 dict（``None`` = 无 slot 身份/缺文件 → 继承主配置）。"""
        slot = self._spawned_slots.get(sprite)
        config_dir = self._spawn_config_dir()
        if slot is None or not config_dir:
            return None
        return overlay_spawn_state.read_slot_config(config_dir, int(slot))

    def _sprite_config(self, sprite):
        """一只 sprite 的配置来源（主 sprite = 主配置对象本身，子 = slot 视图）。

        子 sprite 的视图带 per-slot 快照，只在配置同步边界重建
        （``_invalidate_sprite_configs``）——点击/碰撞事件链上逐次读文件是磁盘税。
        """
        if sprite is None or sprite is getattr(self, "sprite", None):
            return self._config
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
        成员时自然落到宠那一只）——旧版每进程播自己那只，这里取其一。
        """
        overlay = getattr(self, "overlay", None)
        if overlay is None:
            return None
        ids = {getattr(event, "a", None), getattr(event, "b", None)}
        ids.discard(None)
        if not ids:
            return None
        from .sprite_collision import SpriteCollisionWorld
        for sprite in list(getattr(overlay, "sprites", []) or []):
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
        if target is self.sprite:
            self._config.set(name, value)
            save = getattr(self._config, "save", None)
            if callable(save):
                save()
            return True
        slot = self._spawned_slots.get(target)
        config_dir = self._spawn_config_dir()
        if slot is None or not config_dir:
            return False
        ok = overlay_spawn_state.write_slot_setting(
            config_dir, int(slot), name, value, user_customized=True)
        if ok:
            # 立刻以文件为准：丢快照 + 同步签名（本次写入不该被随后的目录事件
            # 当成"外部变更"再刷一遍）。
            self._invalidate_sprite_configs()
            self._slot_signature_cache = self._slot_config_signatures()
        return ok

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
        config = self._config
        self._self_talk_enabled = bool(config.get("self_talk_enabled", False))
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
        try:
            shared.proxy.set_link_next_provider(shared.agent_link._next_busy_anim)
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
        """主 sprite 的气泡控件（PetWindow._speech_bubble 的等价物；无气泡=None）。"""
        return getattr(getattr(self, "_bubble_follower", None), "bubble", None)

    @property
    def cfg(self):
        """PetWindow 同名的配置面。

        host 形消费者（``MusicLyricController`` / ``window_alerts`` 的若干分支）
        直接读 ``win.cfg``；本壳内部用 ``_config``，这里补一个只读别名，避免
        为了别名去改那些共享实现。
        """
        return self._config

    @property
    def scale(self) -> float:
        """主 sprite 缩放（气泡字号/锚点的 pet_scale 来源）。"""
        return float(getattr(self.sprite, "scale", 1.0) or 1.0)

    def isVisible(self) -> bool:  # noqa: N802 (Qt/PetWindow 命名)
        """聚合可见性 = overlay 是否可见（隐藏/全屏自动隐藏都算不可见）。"""
        overlay = getattr(self, "overlay", None)
        return bool(overlay is not None and overlay.isVisible())

    def visible_content_rect(self) -> QRect:
        """主 sprite 身体框的全局矩形（气泡锚点；window_placement 口径的 sprite 版）。"""
        if self.sprite is None:
            return QRect()
        return sprite_anchor_rect_global(self.sprite, self.overlay.geometry().topLeft())

    @property
    def _dragging(self) -> bool:
        """拖拽中（proactive G1 守卫的 interacting 判定之一）。"""
        return getattr(self.overlay, "_mouse_grab", None) is not None

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
        return bool(getattr(self.overlay, "mouse_through", False))

    @property
    def cats(self) -> dict:
        """素材分类（联动动作链按 acts 名筛选用）。失败回空字典，不抛。"""
        try:
            return dict(self.behavior._categories(self.sprite.library))
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
                    width_locked: bool = False, **_ignored) -> None:
        """向主 sprite 头顶冒泡（window.py:3850-3886 的 sprite 等价语义）。"""
        if not self.isVisible() or self._bubble_blocked():
            return
        if self._speech_bubble is None:
            return
        if not sticky and not buttons and self._alert_current is not None:
            return  # 有提醒在展示：普通气泡让路，绝不覆盖审批弹窗
        if sticky or buttons:
            self._sticky_bubble_active = True
            self._sticky_text = str(text)
            self._sticky_subtitle = str(subtitle or "")
            self._sticky_buttons = list(buttons) if buttons else None
            self._show_bubble_text(self._sticky_text, 0,
                                   subtitle=self._sticky_subtitle,
                                   sticky=True, buttons=self._sticky_buttons)
            return
        self._apply_bubble_interactive()
        self.hold_bubble(duration_ms / 1000.0 + 2.0)
        self._show_bubble_text(str(text), duration_ms,
                               subtitle=str(subtitle or ""),
                               title_first=title_first,
                               width_locked=width_locked)

    def _show_bubble_text(self, text: str, duration_ms: int, **kwargs) -> bool:
        follower = getattr(self, "_bubble_follower", None)
        if follower is None:
            return False
        return follower.show(text, duration_ms, **kwargs)

    def hold_bubble(self, seconds: float) -> None:
        """声明重要气泡占用时长（联动/识屏气泡在此期间让路）。"""
        self._bubble_busy_until = max(
            self._bubble_busy_until, time.monotonic() + max(0.0, float(seconds)))

    def hide_bubble(self) -> None:
        """主动关闭当前气泡并推进提醒队列（window_alerts 同源实现）。"""
        if self._speech_bubble is None:
            self._clear_sticky_state()
            return
        window_alerts.hide_bubble(self)

    def clear_alerts(self) -> None:
        """清空提醒队列并关闭当前提醒（DSH 离线/重启收口）。"""
        if self._speech_bubble is None:
            self._alert_queue.clear()
            self._clear_sticky_state()
            return
        window_alerts.clear_alerts(self)

    def show_alert(self, text: str, *, subtitle: str = "", duration_ms: int = 0,
                   buttons: list | None = None, sticky: bool = True,
                   alert_id: str = "", priority: int = 3,
                   alert_type: str = "watchdog", metadata: dict | None = None) -> None:
        """提醒入队（审批/问题/硬失败/卡住提醒；一次只展示一个）。"""
        if self._speech_bubble is None:
            return
        window_alerts.show_alert(
            self, text, subtitle=subtitle, duration_ms=duration_ms,
            buttons=buttons, sticky=sticky, alert_id=alert_id,
            priority=priority, alert_type=alert_type, metadata=metadata)

    def resolve_alert(self, alert_id: str) -> None:
        """按 alert_id 精确收起某条提醒（并发审批各自定位，不误关他人）。"""
        if self._speech_bubble is None:
            return
        window_alerts.resolve_alert(self, alert_id)

    def set_bubble_suppressed(self, suppressed: bool) -> None:
        """设置页打开期间暂停气泡（PetWindow.set_bubble_suppressed 等价）。"""
        if self._speech_bubble is None:
            self._bubble_suppressed = bool(suppressed)
            return
        window_alerts.set_bubble_suppressed(self, suppressed)

    def _pump_alerts(self) -> None:
        """弹出队首提醒（window_alerts 的 host 回调面）。"""
        if self._speech_bubble is None:
            return
        window_alerts.pump_alerts(self)

    def _on_speech_bubble_hidden(self, *_args, **_kwargs) -> None:
        """气泡隐藏后的恢复/队列推进（PetWindow 同款委托）。"""
        if self._speech_bubble is None:
            return
        window_alerts.on_speech_bubble_hidden(self)

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

        与 ``sprite.set_dpr`` 同源（``self._screen``），不从 overlay 窗口取——
        QScreen 只能在 GUI 线程读，而目标尺寸在起解码线程**之前**算好。
        """
        try:
            return float(self._screen.devicePixelRatio())
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
        # 点击音效：overlay 的 click_feedback 通道不带 sprite 身份（播放器对
        # "无 sprite"的调用已按契约静音，见 SpriteSoundPlayer.on_click），真正的
        # 播放落在这一句——门/音量/音源都取被点的那一只自己的配置。通道被置 None
        # （测试静音 / 无音效后端降级）时整条点击音效关闭。
        if callable(getattr(self.overlay, "click_feedback", None)):
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
        self._music_sing_active = False
        # 唱歌续播判据（window.py:2595-2601）：行为层不查音频，只问这一面。
        # 缺这行则唱歌 clip 播完走掷骰链——约 60% 概率被切到随机动作，唱歌循环
        # 碎裂（旧机是"音乐还在就直接 _switch(SING_ANIM)"）。
        self.behavior.sing_continue_provider = lambda: self._music_sing_active
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
        """
        if self._music_lyric is None:
            from .music_lyric_controller import MusicLyricController

            self._music_lyric = MusicLyricController(self)
        return self._music_lyric

    def sync_music_lyric(self) -> None:
        """按配置启停歌词显示（不再启用且从未装过 → 不白养定时器）。"""
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
        """
        self._look_sprite = self.sprite if sprite is None else sprite
        if self._look_busy:
            self._look_bubble("上一张还没看完呢…")
            return
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

    # ---- 黄金回旋（复用 golden_spin.GoldenSpinController + sprite 宿主）----
    def _golden_spin_capable(self, sprite) -> bool:
        """sprite 是否支持整帧旋转通道（假 sprite/替身没有则整族静默降级）。"""
        return callable(getattr(sprite, "set_throw_rotation", None))

    def _ensure_golden_spin(self, sprite):
        """懒建控制器（幂等）：角度写入目标切到本次的 sprite。"""
        if not self._golden_spin_capable(sprite):
            return None
        spin = self._golden_spin
        if spin is None:
            from .golden_spin import GoldenSpinController

            spin = GoldenSpinController(self._golden_spin_host)
            self._golden_spin = spin
        self._golden_spin_target = sprite
        return spin

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
        if getattr(self._probe, "active", False) or self._throw_egg.active:
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
        if getattr(self._probe, "active", False):
            return False
        if not bool(self._config.get("golden_spin_on_click", False)):
            return False
        if not self._golden_spin_capable(sprite):
            return False
        spin = self._ensure_golden_spin(sprite)
        if spin is None:
            return False
        direct = bool(self._config.get("golden_spin_direct", False))
        has_clips = bool(self.cats.get("clicks"))
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
        behavior = getattr(self, "behavior", None)
        if behavior is None:
            return False
        try:
            return behavior.state_of(self.sprite) in _LINK_ONESHOT_STATES
        except Exception:
            logging.debug("overlay: 读行为状态失败", exc_info=True)
            return False

    def switch_clip(self, name: str, link_request: bool = False) -> bool:
        """播放指定动画（window.switch_clip 语义：一次性，播完回掷骰链）。"""
        if self.behavior is None or not str(name or ""):
            return False
        try:
            return bool(self.behavior.play_once(self.sprite, str(name)))
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
        follower = getattr(self, "_bubble_follower", None)
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
        anchor = _SpriteChatAnchor(self, target)
        try:
            bubble = self._quick_chat
            if bubble is None:
                bubble = cls(self._config, pet_window=anchor)
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

        幂等可重复调；island=None 即摘桥。world 必须是进程级 self.collision、
        overlay 必须是当前 overlay（原点来源）——否则墙挂到另一个世界/错位。

        ``kinetic`` 接 ``self.driver.note_kinetic``（M-1）：桌宠静止时 tick 已降到
        T2/T3（不跑仿真），而岛拖拽是几何回调驱动的用户输入、不经过 overlay 的
        鼠标事件（岛是独立顶层窗）——没有这条通道，快速甩岛过鱼可能整个手势
        期间一次碰撞结算都没发生。driver 未起（测试宿主/启动早期）时不强接。
        """
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            bridge.close()
            self.island_bridge = None
        if island is None:
            return
        driver = getattr(self, "driver", None)
        kinetic = getattr(driver, "note_kinetic", None)
        from .island_bridge import IslandWindowBridge
        self.island_bridge = IslandWindowBridge(
            island, self.collision, self.overlay, sound=self._sound,
            kinetic=kinetic if callable(kinetic) else None)

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
        self._warm_sound_effects_once()  # 首次可见前预热（避免首次发声卡事件循环）
        self.overlay.show()
        self.overlay.start()
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
          事件。
        """
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.key() != Qt.Key.Key_Escape:
            return False
        overlay = getattr(self, "overlay", None)
        if overlay is not None and overlay.slingshot.aiming:
            overlay._cancel_slingshot(resume_drag=False)
            return True
        return False

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
        if not self._started:
            return
        self._started = False
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
        self._run_exit_step('停 overlay 驱动', self.overlay.stop)
        self._run_exit_step('关闭 overlay 窗口', self.overlay.close)
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
        if self.overlay is not None:
            self._run_exit_step('停 overlay 驱动', self.overlay.stop)
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

    def _refresh_tray_menu(self) -> None:
        """重建聚合托盘菜单：进程级动作 + 逐只子菜单（legacy 单托盘多窗语义）。

        逐条对照 legacy（app.py:3203-3301，见 PR 报告）：
        - 顶层「显示 / 隐藏」「回到右下角」= legacy 主窗两项（overlay 单窗，
          显隐天然覆盖全部 sprite）；
        - 「生小肥鱼 / 退出子肥鱼」= overlay 多宠生命周期入口（legacy 在右键
          菜单/设置页，不重复造第二个实现，直接复用壳的两个公开方法）；
        - 多宠时逐只子菜单「主肥鱼 / 小肥鱼 [slot-N]」= legacy 每窗子菜单，
          含「回到右下角」「显示这只」（per-sprite visible，M14）「退出这只」；
          顶层「显示 / 隐藏」仍是整窗语义（overlay 单窗，全显全隐）；
        - 「桌宠设置」按主身份路由（D13）、「退出」= app.quit；
        - 「鼠标穿透」「开机自启」= legacy 托盘后段两项（穿透只有托盘/设置页能取消；
          自启勾选态按系统登录项实况，弹出前同步）。
        """
        if self.tray is None:
            return
        menu = QMenu()
        # 气泡是置顶 Tool 窗口（层级高于菜单）：弹出前先隐藏（legacy 同款）
        menu.aboutToShow.connect(self._hide_bubble_for_menu)
        # 弹出前同步逐只勾选态（外部改过 sprite.visible 也能反映）
        menu.aboutToShow.connect(self._sync_tray_pet_visibility)
        menu.addAction("显示 / 隐藏", self._toggle_pet_visible)
        menu.addAction("回到右下角", lambda: self._go_default_corner(self.sprite))
        menu.addSeparator()
        menu.addAction("生小肥鱼", self.spawn_pet)
        menu.addAction("退出子肥鱼", self.clear_spawned_pets)
        visible_actions: list = []
        if self._spawned:
            menu.addSeparator()
            for sprite, label in self._pet_entries():
                sub = menu.addMenu(label)
                sub.addAction("回到右下角",
                              lambda s=sprite: self._go_default_corner(s))
                toggle = sub.addAction(
                    "显示这只", lambda s=sprite: self.toggle_sprite_visible(s))
                toggle.setCheckable(True)
                toggle.setChecked(self._sprite_visible(sprite))
                visible_actions.append((sprite, toggle))
                sub.addAction("退出这只",
                              lambda s=sprite: self.exit_pet(s))
        self._pet_visible_actions = visible_actions
        menu.addSeparator()
        menu.addAction("桌宠设置", lambda: self.open_settings_for(self.sprite))
        # legacy 托盘两项（app.py:3333-3343）：鼠标穿透的取消入口只有托盘/设置页，
        # 开着时桌宠点不动；开机自启此前只有右键菜单有勾选项。
        mouse_through = menu.addAction("鼠标穿透")
        mouse_through.setCheckable(True)
        mouse_through.setChecked(bool(self._config.get("mouse_through", False)))
        mouse_through.toggled.connect(self._on_user_through_changed)
        menu.addSeparator()
        autostart = menu.addAction("开机自启")
        autostart.setCheckable(True)
        autostart.setChecked(self._autostart_enabled())
        autostart.toggled.connect(self._set_autostart_from_tray)
        # 弹出前同步勾选态（设置页/右键菜单改过的不刷新会过期，legacy 同款）
        menu.aboutToShow.connect(
            lambda: self._sync_tray_toggle_checks(mouse_through, autostart))
        menu.addSeparator()
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
        entries = [(self.sprite, "主肥鱼")]
        for sprite in self._spawned:
            slot = self._spawned_slots.get(sprite)
            label = f"小肥鱼 [slot-{slot}]" if slot is not None else "小肥鱼"
            entries.append((sprite, label))
        return entries

    def _go_default_corner(self, sprite) -> None:
        """把指定 sprite 送回右下角（window.go_default_corner 等价）。"""
        sprite.set_pos(self._default_corner_pos(self._bounds, sprite.rect()))

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
        # 气泡风格/字号热改（M10，legacy window.py:3879-3895 在
        # refresh_pet_settings 里热生效，不走重建）：直接对活气泡下手。
        bubble = self._speech_bubble
        if bubble is not None:
            set_style = getattr(bubble, "set_style", None)
            if callable(set_style):
                set_style(str(self._config.get(
                    "self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE)
                    or DEFAULT_SELF_TALK_BUBBLE_STYLE))
            self._apply_bubble_text_scale(bubble)
        # 子宠的气泡族不跟主配置走（它们各读自己的 slot 文件，热改由 slot 目录
        # 事件那条窄路径负责，见 _refresh_slot_settings_if_changed）——这里一个键
        # 都不碰，避免"改主宠设置把子宠也刷了"。
        self.sync_music_lyric()
        # M5f：音乐自动唱歌开关热改（开→启轮询，关→停表并退出唱歌态）
        self.sync_music_sing()

    def _apply_window_capabilities(self) -> None:
        """按当前配置应用窗口能力（4.1b parity）：on_top / 穿透复合 /
        全屏与光标监视器门。"""
        self.set_on_top(bool(self._config.get("on_top", True)), persist=False)
        self._user_mouse_through = bool(self._config.get("mouse_through", False))
        self._apply_effective_mouse_through()
        # M5a/M5b：拖拽闸门（锁定位 / SHIFT 门）推到 overlay——命中按下不进
        # 拖拽 grab，点击语义保留（legacy window.py:3126-3133 / :3172-3181）
        self.overlay.lock_position = bool(self._config.get("lock_position", False))
        self.overlay.shift_drag_required = bool(self._config.get("shift_drag", False))
        # M5c：整窗不透明度（overlay 单窗 = legacy 单宠窗口语义）
        self.overlay.apply_opacity(self._pet_opacity_percent())
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
        self.behavior._predict_lead_s = lead_ms / 1000.0
        self.behavior.predict_enabled = lead_ms > 0

    def _pet_opacity_percent(self) -> int:
        """``pet_opacity`` 配置读数（10-100 钳制，口径同 window.py:3853-3857）。"""
        try:
            value = int(float(self._config.get("pet_opacity", 100)))
        except (TypeError, ValueError):
            value = 100
        return max(10, min(100, value))

    def set_on_top(self, on: bool, *, persist: bool = True) -> None:
        """窗口置顶（window.py set_on_top 等价）。"""
        on = bool(on)
        current = bool(self.overlay.windowFlags()
                       & Qt.WindowType.WindowStaysOnTopHint)
        if on == current:
            # 同值早退（防御性）：setWindowFlag 会重建原生窗口并先隐藏，
            # 重复调用（refresh_settings 每次都走这里）会造成可见闪烁。
            return
        was_visible = self.overlay.isVisible()
        self.overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.overlay.show()  # setWindowFlag 重建原生窗口会先隐藏
        # 原生窗口重建后旧 HWND 的锁屏通知注册随之失效，按新句柄补注册
        self._register_session_notifications()
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
        ``config-slot-N.json``，缺键回退主配置）——旧架构每窗读自己的 cfg，
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
        for sprite in list(getattr(self.overlay, "sprites", []) or []):
            self._apply_sprite_settings_to(sprite)
        self._sync_sprite_collision_flags()  # G4：逐 sprite 资格位（见该方法）
        behavior = getattr(self, "behavior", None)
        if behavior is not None:
            behavior.no_move = bool(cfg.get("no_move", False))
            # M5d：动画间隔（动作/移动播完后的强制氛围步）同步到控制器；
            # 改 0 由 setter 立即取消在跑的 gap
            try:
                gap = max(0.0, min(
                    3600.0, float(cfg.get("animation_gap_seconds", 0.0) or 0.0)))
            except (TypeError, ValueError):
                gap = 0.0
            behavior.animation_gap_seconds = gap
        # 刚刚按 slot 配置重算过资格位 → 同步刷新 stat 签名缓存，下一次目录事件
        # 才能准确判断"slot 配置到底有没有变"（只 stat，不读内容）。
        self._slot_signature_cache = self._slot_config_signatures()

    def _apply_sprite_settings_to(self, sprite, cfg=None) -> None:
        """把**一只 sprite 自己那份**设置下发到它的运行态。

        ``_sync_sprite_settings`` 的全量循环与"某个 slot 文件变了"的窄路径共用
        这一处：per-slot 键（见 :data:`PER_SLOT_SETTING_KEYS`）逐 sprite 取源，
        缺键回退主配置。窗口级键（no_move / animation_gap / on_top / 透明…）不在
        此列——它们只有一份窗，仍由壳全局下发。
        """
        if sprite is None:
            return
        cfg = self._sprite_config(sprite) if cfg is None else cfg
        sprite.throw_speed_cap = throw_speed_cap(cfg.get("throw_strength"))
        sprite.drag_physics = bool(cfg.get("drag_physics", False))
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
        """
        cfg = self._config if cfg is None else cfg
        world = getattr(self, "collision", None)
        if world is None:
            return
        # 缺键/坏值 → 保留世界当前值（构造时的形参默认 = config 缺省）。
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
        for sprite in list(getattr(self.overlay, "sprites", []) or []):
            sprite.collision_enabled = self._sprite_collision_enabled(sprite)

    def _sprite_collision_enabled(self, sprite) -> bool:
        """该 sprite 的宠物间总碰撞资格（per-sprite 口径 = 各自的 config）。

        旧契约（base-2786c15）：每个桌宠是独立进程、各读自己的 config
        （``window.py:3929``），子宠的 ``config-slot-N.json`` 由设置进程按其身份
        写就。本方法把这条身份→配置的对应关系在当前单进程壳里复原：

        - 主 sprite（``self.sprite``）= 本进程运行期 config（主配置）；
        - 子 sprite = 它自己的 slot 配置，读点语义表见
          ``overlay_spawn_state.read_slot_collision_enabled``（缺文件继承主配置、
          缺键/损坏按缺省 true，且只读、不碰别的配置）；
        - 陌生 sprite（不在身份表）退回主配置值，绝不瞎猜 slot。

        只在配置刷新 / spawn·复活 / 提升等边界调用，不参与逐帧 tick。
        """
        main_value = bool(self._config.get("collision_enabled", True))
        if sprite is None or sprite is getattr(self, "sprite", None):
            return main_value
        slot = self._spawned_slots.get(sprite)
        config_dir = self._spawn_config_dir()
        if slot is None or not config_dir:
            return main_value
        return overlay_spawn_state.read_slot_collision_enabled(
            config_dir, int(slot), default=True, fallback=main_value)

    def _apply_effective_mouse_through(self) -> None:
        """有效穿透 = 用户手动穿透 OR 光标自动穿透（window.py:4204 同式）。"""
        self.overlay.mouse_through = bool(
            self._user_mouse_through or self._auto_cursor_hidden)

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
        if hit:
            if not self._auto_hidden and self.overlay.isVisible():
                self._auto_hidden = True
                self._hide_bubble_for_visibility()
                self._set_all_clips_paused(True)
                self.overlay.hide()
                self._pause_shared_subsystems()
                self._pause_warm_all()
                self._note_sprite_visibility_changed()
        elif self._auto_hidden:
            self._auto_hidden = False
            self.overlay.show()
            self._restore_sticky_bubble()
            self._resume_shared_subsystems()
            self._set_all_clips_paused(False)
            self._resume_warm_all()
            self._note_sprite_visibility_changed()

    def _cursor_transition_blocked(self) -> bool:
        """拖拽进行中（window._cursor_transition_blocked 等价）。"""
        return self.overlay._mouse_grab is not None

    def _on_cursor_visibility_changed(self, visibility: str) -> None:
        """光标隐藏（watcher 已做 0.2s 去抖）→ 自动穿透；恢复 → 解除。"""
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

    def _on_grab_finished(self) -> None:
        """拖拽收尾（overlay._finish_grab 钩子）：冲刷光标恢复滞留。"""
        if self._cursor_restore_pending:
            self._cursor_restore_pending = False
            self._auto_cursor_hidden = False
            self._apply_effective_mouse_through()

    # ---------------------------------------------------------------- 4.2a 位置持久化
    def _pos_from_ratios(self, rx: float, ry: float, sprite=None) -> QPointF:
        """rx/ry（身体中心相对可用区比例）反解 sprite 左上角坐标。

        口径同 window_placement.restore_position：以**身体框**中心为锚，扣除
        身体框在 sprite 矩形内的偏移（body_box 未声明的角色退化为全画布）。
        """
        sprite = self.sprite if sprite is None else sprite
        body = (sprite.body_rect() if hasattr(sprite, "body_rect")
                else sprite.rect())
        bw, bh = body.width(), body.height()
        cx = self._bounds.left() + rx * self._bounds.width()
        cy = self._bounds.top() + ry * self._bounds.height()
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
        语义；贴边钳制由 set_pos 的 body_box 钳制收口）。"""
        rx, ry = self._config.get("rx"), self._config.get("ry")
        if rx is None or ry is None:
            self.sprite.set_pos(self._default_corner_pos(
                self._bounds, self.sprite.rect()))
        else:
            self.sprite.set_pos(self._pos_from_ratios(float(rx), float(ry)))
        facing = str(self._config.get("facing", "") or "")
        if facing in ("left", "right"):
            self.sprite.facing = facing

    def save_position(self) -> None:
        """身体中心相对可用区比例持久化（window_placement.save_position 语义）。"""
        geometry = self._sprite_geometry(self.sprite, self._bounds)
        if not geometry:
            return
        for key, value in geometry.items():
            self._config.set(key, value)
        save = getattr(self._config, "save", None)
        if callable(save):
            save()

    # ---------------------------------------------------------------- 4.2b 多 sprite 生灭
    def _spawn_config_dir(self):
        """子肥鱼身份/几何的落盘目录（= 主配置目录）；假配置对象无 dir → None。"""
        return getattr(self._config, "dir", None)

    def _active_slots(self) -> list[int]:
        """进程内活跃身份（spawn 顺序）= 当前活跃宠清单的内容。"""
        return [self._spawned_slots[sprite] for sprite in self._spawned
                if sprite in self._spawned_slots]

    def _persist_active_slots(self) -> None:
        """活跃宠清单落盘（spawn/退出/复活各调一次，原子替换）。"""
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        overlay_spawn_state.save_active_slots(config_dir, self._active_slots())

    def spawn_pet(self) -> None:
        """生小肥鱼（app.py spawn_pet 进程内路径语义 + D6 无锁身份分配）。

        分配 slot 身份 → 建 per-pet 库 + 新 sprite（自主宠向右逐级错开，重叠
        规避由 body 钳制兜底）→ 进活跃清单并落盘。分配/建库失败只记录，
        不留半只 sprite 在清单里。
        """
        config_dir = self._spawn_config_dir()
        try:
            slot = overlay_spawn_state.allocate_slot(
                config_dir, active_slots=self._active_slots())
        except Exception:
            logging.exception("overlay: 生小肥鱼失败（无可用 slot 身份）")
            return
        try:
            self._spawn_slot(slot)
        except Exception:
            logging.exception("overlay: 生小肥鱼失败 (slot=%s)", slot)
            return
        self._persist_active_slots()
        self._refresh_tray_menu()  # 托盘逐只条目随生灭重建（4.2c）
        logging.info("overlay: 已生成子肥鱼 (slot=%s)", slot)

    def _spawn_slot(self, slot: int) -> None:
        """按 slot 身份建一只子肥鱼（spawn 与重启复活共用路径，不落清单）。

        身份几何（rx/ry/facing/scale）与**角色**从 ``config-slot-N.json`` 读；
        有记录 = 重启复活，按比例恢复上次位置、按它自己存的角色建库；无记录 =
        首次生成，自主宠向右错开（角色继承主配置）。spawn 后按该 slot 的配置
        下发设置，并建它自己的气泡/自言自语宿主（旧架构一窗一气泡一计时）。
        """
        config_dir = self._spawn_config_dir()
        if config_dir and not overlay_spawn_state.slot_config_exists(config_dir, slot):
            # 只在全新身份上落种（命名/剔除位置键复用 slot_manager 既有惯例，
            # 对齐 Config.__init__ 的"已有存档的 slot 一律不动"）；已有存档的
            # 身份（重启复活）一个键都不碰——否则每次重启都会用主设置刷新掉
            # 该子肥鱼自存的尺寸/位置/设置。
            slot_manager.seed_slot_config_from_main(config_dir, slot)
        state = (overlay_spawn_state.read_slot_geometry(config_dir, slot)
                 if config_dir else {})
        slot_data = (overlay_spawn_state.read_slot_config(config_dir, slot)
                     if config_dir else None) or {}
        lib = self._create_sprite_library(slot_data, slot)
        scale = float(state.get("scale") or slot_data.get("scale")
                      or self._config.get("scale")
                      or catalog.DEFAULT_SCALE)
        sprite = self._sprite_factory(lib, QPointF(0, 0), scale)
        sprite.home_screen = self._screen
        sprite.set_bounds(QRect(self._bounds))
        sprite.set_pos(self._spawn_sprite_pos(sprite, state))
        facing = state.get("facing")
        if facing in ("left", "right"):
            sprite.facing = facing
        self.overlay.add_sprite(sprite)
        # O3：新 sprite 的播放节拍按**当前有效可播性**初始化——PetSprite 默认
        # 未暂停，隐藏/挂起期生成的子宠若不在这里压住，行为链一起播就按帧率
        # 解码，而窗口根本没有像素要上屏。``visible`` 不动（它逻辑上仍是可见
        # 宠物），窗口真正显示时由整窗显示路径（含 ``start()``）补放行。
        if not self._clips_playable():
            self._set_sprite_clip_paused(sprite, True)
        self._spawned.append(sprite)
        self._spawned_libs[sprite] = lib
        self._spawned_slots[sprite] = slot
        self._sync_sprite_settings()  # 新 sprite 按**它自己那份** config（M6 + B7b）
        # 这只子肥鱼自己的气泡与自己计时的自言自语（旧架构每窗各一套）
        self._ensure_sprite_bubble(sprite)
        host = self._ensure_self_talk_host(sprite)
        if host is not None:
            host.schedule()
        if config_dir:
            # 生成/复活即落一次几何：未及优雅退出（崩溃/断电）也能按清单复活
            geometry = self._sprite_geometry(sprite, self._bounds)
            if geometry:
                overlay_spawn_state.write_slot_geometry(config_dir, slot, geometry)
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
            if not config_dir:
                return False
            return overlay_spawn_state.write_slot_setting(
                config_dir, int(slot), key, value, user_customized=True)

        return _write

    def _create_sprite_library(self, slot_data: dict, slot: int):
        """按**这只子肥鱼自己那份**配置的角色建库（B7b）。

        缺 ``character`` 键或与主宠同角色 → 走既有的 per-pet 库工厂入口
        ``_create_main_library``（同一件事：按主配置的角色建一只新库，T3 的
        per-pet 语义不变）；只有该 slot 存了**不同的**角色时才单独建库，且回退
        默认角色时把值写回它自己的 slot 文件（绝不污染主配置）。
        """
        main_character = str(self._config.get(
            'character', catalog.DEFAULT_CHARACTER))
        character = str(slot_data.get("character") or main_character)
        if character == main_character:
            return self._create_main_library()
        return self._create_library_for(
            character, fallback_writer=self._slot_writer(slot))

    def _spawn_sprite_pos(self, sprite, state: dict) -> QPointF:
        """子肥鱼落位：有持久化比例按 rx/ry 恢复，否则自主宠向右逐级错开。"""
        rx, ry = state.get("rx"), state.get("ry")
        if rx is not None and ry is not None:
            return self._pos_from_ratios(float(rx), float(ry), sprite=sprite)
        index = len(self._spawned) + 1
        main_body = self.sprite.body_rect()
        return QPointF(main_body.x() + main_body.width() + 24 * index,
                       main_body.y())

    def _restore_spawned_pets(self) -> None:
        """D5：启动恢复依据 = 活跃宠清单（**不是** slot 配置存在）。

        清单缺失 = 首次运行语义；损坏/缺首字段由 overlay_spawn_state 防御性
        回退成空清单（只有主宠）。单条复活失败（素材缺失/身份配置被删）只
        记录并摘除该条，不拖垮启动，也不让坏条目永久留在清单里。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        slots = overlay_spawn_state.load_active_slots(config_dir)
        if not slots:
            return
        restored: list[int] = []
        for slot in slots:
            if not overlay_spawn_state.slot_config_exists(config_dir, slot):
                logging.warning("overlay: 活跃宠 slot-%s 身份配置缺失，跳过复活", slot)
                continue
            try:
                self._spawn_slot(slot)
            except Exception:
                logging.exception("overlay: 复活子肥鱼失败 (slot=%s)", slot)
                continue
            restored.append(slot)
        if restored != slots:
            self._persist_active_slots()  # 摘掉复活失败的条目
        logging.info("overlay: 按活跃清单复活 %d 只子肥鱼", len(restored))

    def _remove_spawned_sprite(self, sprite) -> None:
        """退出一只子肥鱼：几何先落盘（配置保留）→ remove_sprite（clip 释放
        V-8 + 行为注销 V-9 挂点）→ 库 shutdown 收尾。

        B7b：随该 sprite 一起收掉它自己的气泡窗、自言自语定时器与缓存视图
        （旧架构进程退出即整套资源释放；留着就是孤儿顶层气泡 + 空转定时器）。
        """
        config_dir = self._spawn_config_dir()
        slot = self._spawned_slots.pop(sprite, None)
        geometry = self._sprite_geometry(sprite, self._bounds)
        if config_dir and slot and geometry:
            overlay_spawn_state.write_slot_geometry(config_dir, slot, geometry)
        lib = self._spawned_libs.pop(sprite, None)
        if sprite in self._spawned:
            self._spawned.remove(sprite)
        self.overlay.remove_sprite(sprite)
        self._close_self_talk_host(sprite)
        self._close_sprite_bubble(sprite)
        self._sprite_hosts.pop(sprite, None)
        self._sprite_cfg_cache.pop(sprite, None)
        shutdown = getattr(lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logging.exception("overlay: 子肥鱼素材库收尾失败")

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
            geometry = self._sprite_geometry(sprite, self._bounds)
            if slot and geometry:
                overlay_spawn_state.write_slot_geometry(config_dir, slot, geometry)

    # ---------------------------------------------------------------- 4.2b 主实例提升
    def exit_pet(self, sprite=None) -> bool:
        """退出单只（菜单「退出这只」的 overlay 落点）。

        主宠退出 → 提升一只子宠为主（app.py P1-3 等价语义）；子宠退出 →
        仅出活跃清单（配置保留）。返回是否真的退掉了目标。
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
        """主宠退出（app.py:2944-2956 等价）：无子宠 → 全部退出语义；
        有子宠 → 活跃清单列表头提升为主（接管主身份/持久化身份）。"""
        self.save_position()
        if not self._spawned:
            self._persist_active_slots()
            self._refresh_tray_menu()
            quit_fn = getattr(self.app, "quit", None)
            if callable(quit_fn):
                quit_fn()  # 最后一窗关闭 → 走全部退出语义
            return True
        promoted = self._spawned[0]
        promoted_lib = self._spawned_libs.pop(promoted, None)
        promoted_slot = self._spawned_slots.pop(promoted, None)
        old_main, old_lib = self.sprite, self.lib
        config_dir = self._spawn_config_dir()
        geometry = self._sprite_geometry(promoted, self._bounds)
        if config_dir and promoted_slot and geometry:
            overlay_spawn_state.write_slot_geometry(
                config_dir, promoted_slot, geometry)
        # 接管主身份：提升者的几何写进主配置（主配置 = 重启蒙主宠的持久身份）；
        # 宠物间总碰撞开关一并跟**提升者自己的** slot 配置走（不是旧主的 bool）——
        # 旧架构里该键就是每只宠各自 config 的键，提升者接管主身份时随之接管。
        # B7b：per-slot 设置同样随之接管（提升者成为主宠后按主配置取源，不把
        # 它自己的速度/音效/气泡族/角色在这一刻丢掉——旧架构它本来就读自己那份）。
        identity_taken = False
        if geometry:
            for key, value in geometry.items():
                self._config.set(key, value)
            identity_taken = True
        if config_dir and promoted_slot:
            slot_data = overlay_spawn_state.read_slot_config(
                config_dir, int(promoted_slot)) or {}
            for key in PER_SLOT_SETTING_KEYS:
                if key == "scale":
                    continue  # 几何已按 sprite 实测值写（含钳制），不重复写
                value = slot_data.get(key)
                if value is not None:
                    self._config.set(key, value)
                    identity_taken = True
        if config_dir and promoted_slot:
            main_value = bool(self._config.get("collision_enabled", True))
            self._config.set(
                "collision_enabled",
                overlay_spawn_state.read_slot_collision_enabled(
                    config_dir, int(promoted_slot),
                    default=True, fallback=main_value))
            identity_taken = True
        if identity_taken:
            save = getattr(self._config, "save", None)
            if callable(save):
                save()
        self._spawned.remove(promoted)
        self.sprite = promoted
        self.lib = promoted_lib
        self.overlay.remove_sprite(old_main)
        self._sprite_hosts.pop(old_main, None)   # 旧主的 pet 形宿主随之作废
        self._persist_active_slots()
        self._refresh_tray_menu()
        self._invalidate_sprite_configs()  # 身份换位：子视角快照全部作废
        self._rebind_main_sprite()
        self._sync_sprite_settings()  # 新主 + 其余子宠的资格位按最新身份重算
        # 提升者的自言自语计时改由壳自身的机接管（它已不是子宠）
        self._close_self_talk_host(promoted)
        self._load_self_talk_settings()
        self._schedule_self_talk()
        shutdown = getattr(old_lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logging.exception("overlay: 旧主肥鱼素材库收尾失败")
        logging.info("overlay: 主肥鱼退出，已提升子肥鱼 (slot=%s) 为主",
                     promoted_slot)
        return True

    def _rebind_main_sprite(self) -> None:
        """主 sprite 更换后重挂"只认主 sprite"的接线（位置监听/投喂/气泡/标记）。

        B7b：提升者本来就有自己的气泡跟随器（作为子宠时建的），把它**接管**成主
        气泡而不是另建一只——否则提升瞬间会多出一只孤儿顶层气泡，且用户正看的
        那条气泡（提醒/自言自语）会凭空消失。壳自身那只旧主跟随器照旧关掉。
        """
        self.overlay.add_position_listener(self.sprite, self._on_main_sprite_moved)
        self._bind_feeding()
        self._sprite_hosts.pop(self.sprite, None)   # 提升者的子宠宿主作废（主=壳）
        promoted_follower = self._bubble_followers.pop(self.sprite, None)
        if promoted_follower is None:
            self._bind_bubble()
        else:
            old_follower = getattr(self, "_bubble_follower", None)
            if old_follower is not None:
                old_follower.close()
            # 身份换位：风格/字号按"新主"（= 提升者接管的配置）重设
            bubble = getattr(promoted_follower, "bubble", None)
            if bubble is not None:
                set_style = getattr(bubble, "set_style", None)
                if callable(set_style):
                    set_style(str(self._config.get(
                        "self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE)
                        or DEFAULT_SELF_TALK_BUBBLE_STYLE))
            self._bubble_follower = promoted_follower
            self._bind_main_bubble_signals()
        # 点击气泡族：主身份提升后回调仍指本壳（overlay 未变，重挂=幂等对齐）
        self.overlay._on_sprite_click = self._on_sprite_click
        self._sync_runtime_marker()

    # ---------------------------------------------------------------- D7 设置进程避让标记
    def _sync_runtime_marker(self) -> None:
        """写 runtime 标记（设置进程避让读它）：主 sprite 身体框的全局几何。"""
        try:
            body = self.sprite.body_rect()
            origin = self.overlay.geometry().topLeft()
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
        for sprite in list(self._spawned):
            slot = self._spawned_slots.get(sprite)
            if slot is None:
                continue
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
        if not self._spawned:
            return
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
        self._invalidate_sprite_configs()   # per-slot 快照重读（内容读只在边界）
        touched: list = []
        for sprite in list(self._spawned):
            slot = self._spawned_slots.get(sprite)
            if slot is None or int(slot) not in changed:
                continue
            self._apply_sprite_settings_to(sprite)
            self._refresh_sprite_bubble_style(sprite)
            touched.append(sprite)
        self._sync_sprite_collision_flags()
        self._reload_self_talk_hosts(touched)

    def _on_settings_command_poll(self) -> None:
        """既有 3s 兜底轮询：指令消费 + 子宠 slot 设置变更检查（同为事件兜底）。"""
        self._consume_settings_command()
        self._refresh_slot_settings_if_changed()

    def _consume_settings_command(self) -> None:
        """消费一条设置进程指令（幂等；无指令 = 空转）。

        指令语义：``target`` 为空 = 退出全部子肥鱼（设置页「一键退出子肥鱼」）；
        否则只退那个 slot 身份（身份已不在活跃清单 = 空操作，只留日志）。
        """
        config_dir = self._spawn_config_dir()
        if not config_dir:
            return
        command = overlay_settings_command.consume_command(config_dir)
        if command is None:
            return
        if command.get("command") != overlay_settings_command.CMD_EXIT_SPAWNED_PETS:
            return  # consume 已按白名单过滤，这里只是防御
        target = command.get("target")
        if target is None:
            logging.info("overlay: 收到设置进程指令 → 退出全部子肥鱼")
            self.clear_spawned_pets()
            return
        sprite = next((item for item in self._spawned
                       if self._spawned_slots.get(item) == target), None)
        if sprite is None:
            logging.info("overlay: 指令目标 slot-%s 已不在活跃清单，跳过", target)
            return
        logging.info("overlay: 收到设置进程指令 → 退出子肥鱼 slot-%s", target)
        self.exit_pet(sprite)

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
        """sprite 的 config 身份：主 sprite = 主身份，子 sprite = 各自 slot 身份。

        D13：独立设置进程按 ``--instance`` 打开对应 ``config-slot-N.json``
        （app.py:1518-1523 的透传模式）；主身份与进程级 DSH_PET_INSTANCE 同值
        → 命令逐字不变。陌生 sprite（不在登记表）回退主身份，绝不瞎猜 slot。
        """
        main_id = str(getattr(self._config, "instance_id", "") or "")
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
        """
        opener = getattr(getattr(self._instance, "shell", None),
                         "open_settings_process", None)
        if not callable(opener):
            return False
        return bool(opener(_SettingsIdentity(self.sprite_instance_id(sprite))))

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
            self._hide_bubble_for_sprite(sprite)
            if sprite is getattr(self, "sprite", None):
                self._notify_pet_hidden()
        self._sync_tray_pet_visibility()
        # 可见 sprite 数变化 → 档位机立即重评（零可见 sprite 的目标档位是 T3）
        self._note_sprite_visibility_changed()

    @staticmethod
    def _set_sprite_clip_paused(sprite, paused: bool) -> None:
        """逐只播放节拍暂停/恢复（clip 无该接口的鸭子 sprite 静默跳过）。"""
        name = "pause_clip" if paused else "resume_clip"
        call = getattr(sprite, name, None)
        if callable(call):
            call()

    def _clips_playable(self) -> bool:
        """有效可播（O3 恢复路径的统一闸门）：窗口真的可见 **且** 驱动器未挂起。

        暂停有两个独立所有者——本壳（整窗显隐/全屏避让）与驱动器（锁屏挂起）。
        恢复路径只看"这次是谁解除的"就会替另一个所有者擅自放行：整窗隐藏中
        托盘勾「显示这只」、锁屏中全屏避让解除，都会让 clip 在不可见/挂起期
        重新按帧率解码（O3 契约破坏）。
        """
        overlay = getattr(self, "overlay", None)
        if overlay is None:
            return False
        try:
            if not overlay.isVisible():
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
        for sprite in list(getattr(self.overlay, "sprites", ())):
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
            self.overlay.show()
            probe = getattr(self, "_probe", None)
            if probe is not None:
                probe.resume()
            self._restore_sticky_bubble()
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
            self.overlay.hide()
            probe = getattr(self, "_probe", None)
            if probe is not None:
                probe.pause()
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
        self.set_pet_visible(not self.overlay.isVisible())

    def _on_collision_squash(self, event) -> None:
        """碰撞 Q 弹（旧权威冲量路径的 squash 语义）：真撞击量级时
        双方 sprite 各压一次。runtime_id 反查 sprite（成员少，线性即可）。

        不再用 hit_min_dv 二次过滤：CollisionEvent 只在真撞击时 fire，
        且阈值已按 pair 类型分级（普通 300 / 撞静态成员 60 / thrown 任意）——
        壳层再按 300 卡一刀会把 60-300 的岛撞（合法真撞击）整段截掉
        （旧机低速撞岛也会挤压）。
        """
        from .sprite_collision import SpriteCollisionWorld
        for sprite in self.overlay.sprites:
            if SpriteCollisionWorld._member_id(sprite) in (event.a, event.b):
                squash = getattr(sprite, "squash", None)
                if callable(squash):
                    squash()

    def _on_collision_probe(self, event) -> None:
        """碰撞真撞击 → 边缘探头取消会话（旧机 collision_client 取消链语义）
        + throw_egg arm（探头被撞飞头部跟随速度）。

        arm 只认「本次真取消了活跃探头会话」这个显式事实（探头入口的返回
        值）——探头侧的 5 秒重进倒计时会残留，不能反过来当证据。

        同 _on_collision_squash：不按 hit_min_dv 二次过滤（事件的阈值已按
        pair 分级，岛撞 60-300 也是合法真撞击，旧机同样取消探头会话）。
        """
        probe = getattr(self, "_probe", None)
        egg = getattr(self, "_throw_egg", None)
        if probe is None and egg is None:
            return
        from .sprite_collision import SpriteCollisionWorld
        for sprite in self.overlay.sprites:
            if SpriteCollisionWorld._member_id(sprite) in (event.a, event.b):
                cancelled = None
                if probe is not None:
                    cancelled = probe.on_sprite_collision_hit(sprite)
                if egg is not None:
                    egg.on_probe_collision_throw(
                        sprite, probe_cancelled=cancelled)

    def _say_feeding_bubble(self, files: int, folders: int,
                            total_bytes: int, stats: dict) -> None:
        """投喂气泡（file_eater._show_feedback 同文案格式）。"""
        from .file_eater import format_bytes
        if files and folders:
            batch = f"{files} 个文件、{folders} 个文件夹"
        elif files:
            batch = f"{files} 个文件"
        elif folders:
            batch = f"{folders} 个文件夹"
        else:
            batch = "空气"
        text = (
            f"啊呜～吃掉 {batch}（{format_bytes(total_bytes)}），"
            f"累计吃掉 {stats.get('file_count', 0)} 个文件、"
            f"{stats.get('folder_count', 0)} 个文件夹，"
            f"共 {format_bytes(stats.get('total_bytes', 0))}！"
        )
        self._bubble_follower.say(text, subtitle="放心，只是做个样子，文件没有删除或移动哦")

    def switch_character(self, character_id: str, sprite=None) -> None:
        """切换角色（4.1c）：换 per-pet 库 + 行为状态重置 + 配置持久化。

        per-pet 库是 T3 定论：新建库给目标 sprite，旧库 shutdown 收尾 clip；
        行为状态机 forget 后下个 tick 自动接管 bind（防旧 clip 跨库残留）。

        ``sprite`` = 被点的那一只（B7b）：旧架构一窗一宠，菜单的「切换角色」只换
        本窗、只写本窗的 cfg。主宠照旧（换 ``self.lib`` + 写主配置）；子宠换**它
        自己的**库、写**它自己的** ``config-slot-N.json['character']``——此前一律
        打在主宠身上（审计 F1），子宠各自存的角色也无人读。
        """
        target = self.sprite if sprite is None else sprite
        if target is None:
            return
        if target is not self.sprite:
            self._switch_child_character(target, character_id)
            return
        current = str(self._config.get("character", catalog.DEFAULT_CHARACTER))
        if not character_id or character_id == current:
            return
        egg = getattr(self, "_throw_egg", None)
        if egg is not None:
            egg.cancel_all("character_switch")  # 换角色即换素材，飞行会话兜底回正
        try:
            new_lib = self._instance._create_library(character_id)
        except Exception:
            logging.exception("overlay: 切换角色建库失败 %s", character_id)
            return
        self._config.set("character", character_id)
        save = getattr(self._config, "save", None)
        if callable(save):
            save()
        old_lib = self.lib
        self.lib = new_lib
        self.sprite.library = new_lib
        self.behavior.forget(self.sprite)
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

    def _switch_child_character(self, sprite, character_id: str) -> None:
        """子宠换角色：换它自己的库 + 写它自己的 slot 配置（主宠一字不动）。"""
        slot = self._spawned_slots.get(sprite)
        current = str(self._sprite_config(sprite).get(
            "character", catalog.DEFAULT_CHARACTER))
        if not character_id or character_id == current:
            return
        egg = getattr(self, "_throw_egg", None)
        if egg is not None:
            egg.cancel_all("character_switch")
        config_dir = self._spawn_config_dir()
        writer = self._slot_writer(int(slot)) if slot is not None else None
        try:
            new_lib = self._create_library_for(
                character_id, fallback_writer=writer)
        except Exception:
            logging.exception("overlay: 子宠切换角色建库失败 %s", character_id)
            return
        if slot is not None and config_dir:
            # 用户主动给这只选了角色：写进口它的 slot 配置（原子写单键；同时置位
            # user_customized——旧契约里"已自定义的 slot 下次生成不被主设置刷新"）。
            # 素材缺失时上面的 fallback_writer 已把回退后的默认角色写进去。
            overlay_spawn_state.write_slot_setting(
                config_dir, int(slot), "character", str(character_id),
                user_customized=True)
        old_lib = self._spawned_libs.get(sprite)
        self._spawned_libs[sprite] = new_lib
        sprite.library = new_lib
        self.behavior.forget(sprite)
        self._invalidate_sprite_configs()
        self._slot_signature_cache = self._slot_config_signatures()
        self._refresh_sprite_bubble_style(sprite)  # 换库换 body_box → 重锚一次
        shutdown = getattr(old_lib, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logging.exception("overlay: 子宠旧素材库收尾失败")

    # ---------------------------------------------------------------- 屏事件
    def _wire_screen_signals(self) -> None:
        self.app.screenAdded.connect(self.handle_screen_added)
        self.app.screenRemoved.connect(self.handle_screen_removed)
        self.app.primaryScreenChanged.connect(self.handle_primary_screen_changed)
        self._connect_screen(self._screen)

    def _connect_screen(self, screen) -> None:
        try:
            screen.geometryChanged.connect(self.handle_geometry_changed)
            screen.availableGeometryChanged.connect(self.handle_geometry_changed)
        except (AttributeError, TypeError):
            pass  # 测试假屏无 Qt 信号：几何同步由测试直调 handler

    def _disconnect_screen(self, screen) -> None:
        try:
            screen.geometryChanged.disconnect(self.handle_geometry_changed)
            screen.availableGeometryChanged.disconnect(self.handle_geometry_changed)
        except (AttributeError, TypeError, RuntimeError):
            pass

    def handle_geometry_changed(self, *args) -> None:
        """geometryChanged/availableGeometryChanged → 几何同步 + DPR 重喂。"""
        self._sync_geometry()
        self.sprite.set_dpr(float(self._screen.devicePixelRatio()))
        self._refresh_self_talk_image_cache_for_dpr()

    def handle_screen_added(self, screen) -> None:
        """屏插入：主屏切换由 primaryScreenChanged 负责；4.2a 位置持久化后
        才有"回保存屏"语义，本刀只观测记录。"""
        logging.info("overlay: screen added (%s)", _screen_name(screen))

    def handle_screen_removed(self, screen) -> None:
        """屏拔出：被拔的是当前屏 → 先收尾拖拽再按比例迁移到新主屏重建；
        非当前屏不动作（主屏切换由 primaryScreenChanged 负责）。"""
        logging.info("overlay: screen removed (%s)", _screen_name(screen))
        if screen is self._screen:
            self._end_drag()  # 拖拽中拔屏处置（v1.1 §10）
            self._migrate_to_screen(self.app.primaryScreen())

    def handle_primary_screen_changed(self, screen) -> None:
        logging.info("overlay: primary screen changed -> %s", _screen_name(screen))
        self._migrate_to_screen(screen)

    def _sync_geometry(self) -> None:
        """当前屏几何/可用区变化：overlay 几何 + 控制器边界 + 全部 sprite 按比例迁移。

        主 sprite 与全部子肥鱼用**同一份** old/new bounds 迁移（G2：只迁主宠会让
        子肥鱼留在旧绝对坐标上，随后被新边界钳到同一条边，相对布局丢失）；
        ``_apply_bounds`` 只能整体排在循环**之后**——它一次把**全部** sprite 的
        钳制域换成新边界，还没轮到的那几只会被就地钳一次、旧位置比例当场抹掉。
        每只自己的换域与落点顺序在 ``_migrate_sprite_position`` 内（比例取旧边界，
        落点钳新边界）。
        overlay 全局原点变化（屏位置变）另同步气泡跟随器原点（不重建跟随器
        与气泡：重建会丢正在显示的气泡）。
        """
        old_bounds = QRect(self._bounds)
        old_origin = self.overlay.geometry().topLeft()
        self.overlay.setGeometry(self._screen.geometry())
        new_bounds = self._local_bounds(self._screen)
        for sprite in [self.sprite, *self._spawned]:
            self._migrate_sprite_position(old_bounds, new_bounds, sprite)
        self._apply_bounds(new_bounds)
        self._sync_bubble_origin(old_origin)

    def _sync_bubble_origin(self, old_origin: QPoint) -> None:
        """overlay 全局原点变化后同步"按全局原点取坐标"的消费方（同屏几何路径）。

        原点 = ``overlay.geometry().topLeft()``，两个消费方都把它缓存在别处：
        - 气泡跟随器（``SpriteBubbleFollower.__init__`` 缓存；``set_origin`` 会在
          原点真变时把在显气泡重定位一次——同尺寸平移屏下 sprite 局部位置不变、
          没有位移通知，不同步则气泡停在旧全局位置）；
        - 岛墙桥（``IslandWindowBridge.set_origin``：更新原点 + 作废岛速基线 +
          立刻按新原点重算并重登记局部几何，否则墙留在旧原点、碰撞位置整体偏移）。

        仅原点真变时调用：任务栏等"只有 available 变化"的场景原点不变，两个消费
        方都不产生任何多余动作；无跟随器/无桥（岛碰撞关、无岛）时整段跳过，
        不凭空建桥、不重建气泡。
        """
        origin = self.overlay.geometry().topLeft()
        if origin == old_origin:
            return
        follower = getattr(self, "_bubble_follower", None)
        if follower is not None:
            try:
                follower.set_origin(origin)
            except Exception:
                logging.debug("overlay: 气泡跟随原点同步失败", exc_info=True)
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            try:
                bridge.set_origin(origin)
            except Exception:
                logging.debug("overlay: 岛墙原点同步失败", exc_info=True)

    def _apply_bounds(self, new_bounds: QRect) -> None:
        self._bounds = QRect(new_bounds)
        self.behavior.bounds = QRect(new_bounds)
        self.physics.set_bounds(new_bounds)
        # 主 sprite 与全部子肥鱼共用同一钳制域（4.2b：漏掉子肥鱼会让它们
        # 在几何变化后仍被旧屏边界钳制）
        for sprite in [self.sprite, *self._spawned]:
            sprite.set_bounds(QRect(new_bounds))
        probe = getattr(self, "_probe", None)
        if probe is not None:
            probe.set_bounds(QRect(new_bounds))
        egg = getattr(self, "_throw_egg", None)
        if egg is not None:
            egg.set_bounds(QRect(new_bounds))

    def _migrate_sprite_position(self, old_bounds: QRect, new_bounds: QRect,
                                 sprite=None) -> None:
        """rx/ry 语义迁移：sprite 中心相对可用区的比例在几何变化前后不变
        （口径同 window_placement.save_position 的持久化比例）。

        两步各按自己那一侧的边界口径算，顺序不能合并：

        - **取比例必须在换边界之前**——``PetSprite.set_bounds`` 会就地钳一次，
          先换边界就把旧位置的比例钳没了（G2 已锁死的语义）；
        - **写落点必须在换边界之后**——``set_pos`` 受当前边界钳制，落点在旧边界
          下先被截断、随后 ``_apply_bounds`` 扩边界也不回位：小屏/低分迁到大屏/
          高分时靠右/靠下的宠物整只卡在旧边缘（实测宽 1000→1600：比例落点
          x≈1230 被旧边界钳成 744）。

        只换本只的钳制域（本只以外的 sprite 各自的边界在轮到自己时才换，
        比例取自它们各自的旧位置），``_apply_bounds`` 随后统一写一遍是同值幂等。
        """
        sprite = self.sprite if sprite is None else sprite
        if sprite is None:
            return
        if old_bounds.width() <= 0 or old_bounds.height() <= 0:
            return
        rect = sprite.rect()
        rx = (rect.x() + rect.width() / 2.0 - old_bounds.x()) / old_bounds.width()
        ry = (rect.y() + rect.height() / 2.0 - old_bounds.y()) / old_bounds.height()
        ncx = new_bounds.x() + rx * new_bounds.width()
        ncy = new_bounds.y() + ry * new_bounds.height()
        sprite.set_bounds(QRect(new_bounds))
        sprite.set_pos(QPointF(ncx - rect.width() / 2.0, ncy - rect.height() / 2.0))

    def _migrate_to_screen(self, new_screen) -> None:
        """overlay 重建到新屏（屏热插拔/主屏切换）：sprite 按比例迁移坐标。

        重建出的新 overlay 是**未显示**的新窗口：是否 show 回桌面、是否补放行
        播放节拍、是否重挂气泡，全部按迁移前的有效可见性快照决定（见下
        ``was_visible``）——隐藏中的窗口不因一次屏事件自己冒出来。
        """
        if new_screen is None or new_screen is self._screen:
            return
        old_bounds = QRect(self._bounds)
        old_overlay = self.overlay
        # 迁移前快照"有效可见性"（= 整窗可见 **且** 不在全屏避让隐藏态）。迁移
        # 后是否 show / 补放行播放节拍只认它：隐藏中的窗口（托盘
        # ``set_pet_visible(False)`` / 全屏避让 ``_auto_hidden``）不得因为一次屏
        # 事件被重建路径无条件 show 回桌面。全屏避让按 watcher 口径——迁移本身
        # 不重评全屏探测，故沿用当下的 ``_auto_hidden``（切走前它已把窗口 hide，
        # 只是钉住"别顺手显示"这一方向；真解除仍由 watcher 的翻转回调走显示路径）。
        was_visible = self.overlay.isVisible() and not self._auto_hidden
        # 迁移前抓一份"正在显示的非粘滞气泡"文案：下面 ``_bind_bubble`` 会
        # ``follower.close()`` 关掉旧气泡窗（旧版气泡是独立 Tool 窗，跨屏只跟随
        # 不销毁），不抓就整条气泡在拔屏瞬间消失。
        transient_bubble = self._snapshot_transient_bubble()
        self._end_drag()
        self._disconnect_screen(self._screen)
        self._screen = new_screen
        new_bounds = self._local_bounds(new_screen)
        self.overlay = ShellOverlayWindow(new_screen, driver=self.driver)
        self.overlay.behavior = self.behavior
        self.overlay.edge_probe = getattr(self, "_probe", None)
        # 迁移后接线恢复（原 _build 挂在 overlay 上的能力一并重挂，否则
        # 屏迁移后点击音效/全量菜单/投喂/穿透回调/拖拽收尾全部静默丢失）
        self.overlay.click_feedback = self._sound.on_click
        # 4.1c 弹弓：controller 由 OverlayWindow 自持，这里只接 config
        # （slingshot_enabled 热读，设置页即改即生效）
        self.overlay.slingshot.config = self._config
        from .sprite_menu_facade import build_sprite_full_menu
        # 点击气泡族（余额/点击自言自语）：新 overlay 上必须重挂，否则屏迁移后
        # 点击静默丢失（与 click_feedback 同位置）
        self.overlay._on_sprite_click = self._on_sprite_click
        self.overlay._click_route_spin = self._route_click_golden_spin
        self.overlay._full_menu_builder = (
            lambda target: build_sprite_full_menu(self, target))
        self.overlay._through_changed = self._on_user_through_changed
        self.overlay._grab_finished_cb = self._on_grab_finished
        self.overlay.add_position_listener(self.sprite, self._on_main_sprite_moved)
        # 4.2b：子肥鱼随主 sprite 一起迁到新 overlay（否则拔屏/主屏切换后
        # 它们留在已关闭的旧 overlay 上，等于静默消失）
        for sprite in [self.sprite, *self._spawned]:
            old_overlay.remove_sprite(sprite, release_clip=False)  # 迁移保留 clip
            self.overlay.add_sprite(sprite)
            sprite.home_screen = new_screen
            sprite.set_dpr(float(new_screen.devicePixelRatio()))
            self._migrate_sprite_position(old_bounds, new_bounds, sprite)
        # sprite-removed 三条注销监听：只在 _build 挂过，重建 overlay 必须重挂，
        # 否则迁移后退出任意一只宠不再注销行为/探头/彩蛋状态（F2）。
        self._wire_sprite_removed_hooks(self.overlay)
        self._apply_bounds(new_bounds)
        self._apply_window_capabilities()  # on_top/穿透复合/监视器门重挂
        self._connect_screen(new_screen)
        was_started = self._started
        # M-2：驱动器为进程级共享（新 overlay 已在构造时挂上）——不再停旧表
        # 再起新表（那会在多 overlay 下停掉整组 tick）；旧 overlay 关闭即摘除。
        if getattr(self, "_feeding", None) is not None:
            self._bind_feeding()
        if getattr(self, "_bubble_follower", None) is not None:
            # 跟随器把旧 overlay 记进了自身（构造参数 + 位置监听者），迁移必须
            # **全部**重建（B7b：主 + 每只子宠各一只）。
            self._rebuild_all_bubbles()
        bridge = getattr(self, "island_bridge", None)
        if bridge is not None:
            # 屏迁移：岛墙局部坐标按新 overlay 原点重算（气泡跟随器同款）
            bridge.set_origin(self.overlay.geometry().topLeft())
        if was_started:
            # 新 overlay 是新 HWND：锁屏通知注册改挂到它上面（旧句柄随关闭失效）。
            # 注册按 HWND 走、与可见性无关，故不并进下面的可见分支：隐藏中迁移
            # 不回这里重挂的话，锁屏消息仍发往已关闭的旧句柄（休眠/唤醒收不到）。
            self._register_session_notifications()
        if was_started and was_visible:
            self.overlay.show()
            self.overlay.start()
            # 迁移前可见 = 该显示就显示：迁移重建不该把播放节拍留在暂停
            # （隐藏期压住的暂停归真正的显示路径解除，可见态下必须补放行，
            # 否则窗口可见而宠冻在首帧——与挂机冻结同貌）。
            self._set_all_clips_paused(False)
            # 迁移不丢气泡：新建的跟随器自带一只**空**气泡窗（未 show），
            # 把原内容按同一口径重新挂上——粘滞提醒（含队列来源，pump_alerts
            # 与 legacy sticky 都写 ``_sticky_*``）优先；非粘滞的限时气泡用
            # 迁移前抓下的文案补一次（不可见壳不冒泡，故本分支只在迁移前
            # 可见时进入，与 set_pet_visible(False) 同纪律）。
            self._restore_sticky_bubble()
            if transient_bubble is not None:
                text, subtitle, duration_ms = transient_bubble
                self._show_bubble_text(text, duration_ms, subtitle=subtitle)
        old_overlay.close()

    def _rebuild_all_bubbles(self) -> None:
        """屏迁移后重建**全部**气泡跟随器（主 + 每只子宠各一只）。

        跟随器把旧 overlay 记进了自身（``_origin`` 与位置监听注册表），迁移重建
        overlay 后必须整套换新；子宠各自的气泡窗同样不能留在已关闭的旧 overlay 上。
        """
        for follower in list(self._bubble_followers.values()):
            try:
                follower.close()
            except Exception:
                logging.debug("overlay: 迁移时子宠气泡收尾失败", exc_info=True)
        self._bubble_followers = {}
        self._bind_bubble()

    def _snapshot_transient_bubble(self):
        """抓一份"正在显示的非粘滞气泡"（跨屏重建跟随器前调用）。

        旧版气泡是独立 Tool 窗、跨屏只跟随不销毁；新版 ``_bind_bubble`` 会
        ``follower.close()`` 关掉旧窗，故先取文案再重建后补显。取源优先提醒队列
        的当前条目（带原停留时长）；其余（自言自语/联动/识屏）从气泡控件取，
        控件不暴露停留时长，补显用壳的默认时长。拿不到（无气泡/隐态/粘滞气泡）
        返回 None——粘滞的走 ``_restore_sticky_bubble``，不重复挂。
        """
        if self._sticky_bubble_active:
            return None
        cur = self._alert_current
        if cur is not None and not cur.get("sticky"):
            return (str(cur.get("text", "")), str(cur.get("subtitle", "")),
                    cur.get("duration_ms") or 3200)
        bubble = self._speech_bubble
        if bubble is None:
            return None
        try:
            if not bubble.isVisible():
                return None
        except Exception:
            return None
        text = str(getattr(bubble, "_raw_text", "") or "")
        if not text:
            return None
        subtitle = ""
        label = getattr(bubble, "_subtitle_label", None)
        if label is not None:
            try:
                subtitle = str(label.text() or "")
            except Exception:
                subtitle = ""
        return text, subtitle, 3200

    def _end_drag(self) -> None:
        """拖拽中拔屏/迁移：按当前位置松手收尾，清掉 overlay 的 grab 状态。"""
        grab = getattr(self.overlay, "_mouse_grab", None)
        if grab is None:
            return
        try:
            grab.on_release(QPointF(grab.pos))
        except Exception:
            logging.exception("overlay: 屏事件收尾拖拽失败")
        self.overlay._mouse_grab = None
        self.overlay._press_global = None

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

    def _register_session_notifications(self) -> None:
        """对 overlay 句柄注册锁屏通知（Windows；失败降级为不支持并记日志）。

        锁屏消息（WM_WTSSESSION_CHANGE）只发给注册过的窗口，注册必须落在真实
        HWND 上（``winId()`` 会按需创建原生窗口）。失败不影响挂起/恢复探测
        （WM_POWERBROADCAST 是广播）与关机探测（原生事件过滤器照旧）。
        """
        watcher = getattr(self, "_session_watcher", None)
        register = getattr(watcher, "register_session_notifications", None)
        if not callable(register):
            return
        overlay = getattr(self, "overlay", None)
        if overlay is None:
            return
        try:
            register(int(overlay.winId()))
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
        if self.overlay is not None:
            self.overlay.stop()
