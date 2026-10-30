from __future__ import annotations

import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget

from .. import paths
from ..config import Config
from ..contracts.content import AnimationSpec, CharacterSpec
from ..engine.animation import AnimationPlayer
from ..engine.behavior import (
    BehaviorDirector,
    KIND_MOVE,
    KIND_TURN,
    normalize_weights,
)
from ..engine.catalog import GraphCatalog
from ..engine.animation_policy import AnimationPolicy, policy_from_values
from ..engine.enums import AnimatType, GraphType, ModeType
from ..engine.head_pat import (
    HEAD_PAT_DEFAULT_MODE,
    HeadPatTracker,
    choose_head_pat_spec,
    head_pat_specs,
)
from ..engine.geometry import (
    CORNER_MARGIN,
    DRAG_THRESHOLD,
    MOVE_MARGIN,
    MOVE_MAX_PX,
    MOVE_MIN_PX,
    SCALE_STEPS,
    body_local_rect,
    body_position_ratio,
    content_size,
)
from ..engine.motion import (
    choose_move_direction,
    clamp_to_screen,
    clamp_to_screens,
    move_position_at_frame,
    quantize_move,
    stride_for,
)
from ..engine.pet_state import PetState
from ..engine.pointer import (
    POINTER_DRAG_HOLD_MS,
    POINTER_MENU_HOLD_MS,
    PointerAction,
    PointerArbiter,
)
from ..media.click_sound import ClickSoundPair, choose_click_sound, resolve_click_sound_candidates
from ..media.images import screen_dpr
from ..media.library import ClipLibrary
from ..media.sound import SoundPlayer
from ..platform import screens as screen_mod
from ..platform.win import (
    PerPixelInputController,
    apply_click_through,
    apply_stream_window,
    apply_tool_window,
    forward_left_click,
)
from .bubble import SpeechBubble
from .easter_egg import DEFAULT_EASTER_EGG_TITLE, EasterEggController
from .edge_probe import EdgeProbeController
from .effects import EffectController
from .feeding import FeedingController
from .menu import menu_open, show_context_menu
from .motion import MotionController
from .renderer import frame_pixmap
from .rename_dialog import alias_bubble_text, prompt_alias
from .slingshot import SlingshotController

logger = logging.getLogger("petcore.ui.window")


@dataclass
class ScreenState:
    screen_name: str | None = None
    rx: float | None = None
    ry: float | None = None
    body_rx: float | None = None
    body_ry: float | None = None
    pos_x: float | None = None
    pos_y: float | None = None


class PetWindow(QWidget):
    #: 桌宠移动后发出（拖动、抛掷、漫游、碰撞分离都会走 ``moveEvent``）；
    #: 聊天窗用它做「跟随桌宠」的合并定位。
    pet_moved = Signal()
    #: 点击反馈被余额接管时，等余额气泡收起的兜底时限：结果一直不来、免打扰把气泡
    #: 拦掉、窗口被隐藏时，到点仍要把点击台词放出来（不许永久憋住）。
    CLICK_TALK_FALLBACK_MS = 9000

    def __init__(self, config: Config, root: Path, host, *, slot: int = 0):
        super().__init__(None)
        self.config = config
        self.root = Path(root)
        self.host = host
        self.slot = int(slot)
        self.is_primary = self.slot == 0
        self.click_through = bool(config.get("click_through"))
        self.always_on_top = bool(config.get("always_on_top"))
        self.stream_capture_mode = bool(config.get("stream_capture_mode"))
        self.dragging = False
        self._suppress_context_menu = False
        self.state = PetState(scale=float(config.get("scale") or 0.72))
        self.character: CharacterSpec | None = None
        self._body_local = body_local_rect(None, self.state.scale)
        self._head_local = self._body_local
        self._probe_angle = 0.0
        self._probe_exposure = 1.0
        self._edge_probe = EdgeProbeController(self)
        self.effects = EffectController(self)
        self.easter = EasterEggController(self)
        self.motion = MotionController(self)
        self.slingshot = SlingshotController(self)
        self.feeding = FeedingController(self)
        self.library = ClipLibrary(str(config.get("frameseq") or "auto"))
        self.sound = SoundPlayer(float(config.get("volume") or 0.8))
        self._click_sound_pair = ClickSoundPair(self.sound.play)
        self._click_sound_token: int | None = None
        # 从属窗口（owned window）：气泡的层级跟着桌宠走——桌宠置顶它才置顶。但
        # `show()/raise_()` 会把整条 owner 链一起抬到普通窗口层最上面，所以「非置顶时
        # 不抢前台」与「菜单会话期间不压菜单」都由 `SpeechBubble._present()` 收口。
        self.bubble = SpeechBubble(self)
        self.bubble.clicked.connect(self._on_bubble_clicked)
        self.bubble.set_text_scale(self._text_scale_factor())
        self.bubble.set_stream_mode(self.stream_capture_mode)
        self._clip = None
        self._image = None
        self._pixmap = None
        self._action_kind: str | None = None
        self._pending_flip = False
        self._loop_active = False
        self._probe_drag_throw = False
        self._move_plan: dict | None = None
        self._move_loop = 0
        self._move_last_frame = -1
        self._press_global: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._last_cursor_local: QPointF | None = None
        self._head_pat_tracker = HeadPatTracker()
        self._head_pat_last_name: str | None = None
        self._head_pat_no_spec_logged = False
        self._click_listeners: list = []
        self._drop_listeners: list = []
        # 点击反馈排队：开「点击查看余额」时余额气泡先冒，点击台词等它收起。
        self._click_talk_pending = False
        self._click_talk_waiting_close = False
        #: 余额结果「已经展示过」的序号：缓存命中时它是**同步**发生的，可能早于
        #: 排队建立（`request()` 内部先发缓存），靠序号判断这次点击已经有过余额气泡。
        self._click_balance_serial = 0
        self._click_talk_timer = QTimer(self)
        self._click_talk_timer.setSingleShot(True)
        self._click_talk_timer.timeout.connect(self._flush_click_talk)
        self.bubble.closed.connect(self._on_bubble_closed)
        self._bubble_follow_pending = False
        # 多气泡序列（自言自语彩蛋）：单次定时器推进，新的 say/show_image/隐藏都会取消。
        self._say_sequence_items: deque = deque()
        self._say_sequence_on_show = None
        self._say_sequence_timer = QTimer(self)
        self._say_sequence_timer.setSingleShot(True)
        self._say_sequence_timer.timeout.connect(self._advance_say_sequence)
        self._gap_until = 0.0
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(16)
        self._move_timer.timeout.connect(self._tick_move)
        self._input_controller: PerPixelInputController | None = None
        # 穿透模式（click_through=True）的鼠标状态机与两个长按计时。
        self._pointer = PointerArbiter()
        self._left_press_active = False
        self._left_drag_armed = False
        self._right_press_active = False
        self._right_menu_shown = False
        self._right_menu_armed = False
        self._through_hold_until = 0.0
        self._paused_for_session = False
        self._left_hold_timer = QTimer(self)
        self._left_hold_timer.setSingleShot(True)
        self._left_hold_timer.setInterval(POINTER_DRAG_HOLD_MS)
        self._left_hold_timer.timeout.connect(self._on_left_hold)
        self._right_hold_timer = QTimer(self)
        self._right_hold_timer.setSingleShot(True)
        self._right_hold_timer.setInterval(POINTER_MENU_HOLD_MS)
        self._right_hold_timer.timeout.connect(self._on_right_hold)
        self._layout = None
        self._apply_flags(initial=True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

    def available_characters(self) -> list[CharacterSpec]:
        return list(self.host.characters())

    @property
    def character_id(self) -> str:
        return self.character.id if self.character is not None else ""

    def _update_body_local(self) -> None:
        box = self.character.body_box if self.character is not None else None
        canvas = self.character.canvas if self.character is not None else None
        head = self.character.head_box if self.character is not None else None
        if canvas is None:
            self._body_local = body_local_rect(box, self.state.scale)
            self._head_local = (
                body_local_rect(head, self.state.scale) if head is not None else self._body_local
            )
        else:
            self._body_local = body_local_rect(box, self.state.scale, canvas)
            self._head_local = (
                body_local_rect(head, self.state.scale, canvas)
                if head is not None
                else self._body_local
            )

    def body_local_rect(self) -> tuple[int, int, int, int]:
        return self._body_local

    def body_rect(self) -> tuple[int, int, int, int]:
        bx, by, bw, bh = self._body_local
        return self.x() + bx, self.y() + by, bw, bh

    @property
    def probe_active(self) -> bool:
        return self._edge_probe.active

    def probe_angle(self) -> float:
        return self._probe_angle

    def probe_exposure(self) -> float:
        return self._probe_exposure

    def set_probe_pose(self, angle: float, exposure: float) -> None:
        self._probe_angle = float(angle)
        self._probe_exposure = max(0.0, min(1.0, float(exposure)))
        self.update()

    def move_probe_to(self, x: float) -> None:
        self.move(int(round(x)), self.y())

    def probe_bounds(self) -> tuple[int, int, int, int] | None:
        layout = self._layout or screen_mod.snapshot_from_qt()
        screen = screen_mod.screen_for_rect(
            self.body_rect(), layout
        )
        return screen.rect if screen is not None else None

    def stop_for_probe(self) -> None:
        self._cancel_move_plan()
        self.state.dragging = False
        if self._action_kind not in (None, "idle"):
            self._play_idle()

    def remember_probe_geometry(self) -> None:
        self._remember_geometry()

    def set_edge_probe_enabled(self, enabled: bool, *, persist: bool = True) -> None:
        self._edge_probe.set_enabled(bool(enabled))
        self.config.set("edge_probe_enabled", bool(enabled))
        if persist:
            self.config.save()

    def toggle_edge_probe(self) -> None:
        self.set_edge_probe_enabled(
            not bool(self.config.get("edge_probe_enabled"))
        )

    def start(self, character: CharacterSpec | None = None) -> None:
        character = character or (self.available_characters()[0] if self.available_characters() else None)
        if character is None:
            logger.error("no character available")
            return
        self._activate(character)
        self._restore_geometry()
        self.show()
        if self.stream_capture_mode:
            apply_stream_window(int(self.winId()))
        else:
            apply_tool_window(int(self.winId()))
        self._play_idle()
        click_spec = self.catalog.find_for(GraphType.TOUCH_BODY, AnimatType.SINGLE)
        if click_spec is not None:
            QTimer.singleShot(800, lambda spec=click_spec: self.library.warm_first_frame(spec))
        QTimer.singleShot(200, self._warm_interaction_assets)
        self._head_pat_tracker.reset()
        if os.name == "nt":
            self._input_controller = PerPixelInputController(self, self.is_transparent_at)
        if self.click_through:
            self._apply_click_through()
        if bool(self.config.get("edge_probe_enabled")):
            QTimer.singleShot(800, self._edge_probe.maybe_enter)

    def _warm_interaction_assets(self) -> None:
        """预热点击素材与音效：第一次点击不该卡在解码/音频后端初始化上。"""
        if self.character is None:
            return
        for spec in self.catalog.specs_for(GraphType.TOUCH_BODY):
            if spec.animat is AnimatType.SINGLE:
                try:
                    self.library.warm_first_frame(spec)
                except Exception:
                    logger.debug("warm click clip failed: %s", spec.name, exc_info=True)
        self.sound.prepare(paths.legacy_asset_root() / "sounds" / "click.wav")
        for candidate in self._click_sound_candidates():
            try:
                self.sound.prepare(candidate)
            except Exception:
                logger.debug("warm click sound failed: %s", candidate, exc_info=True)

    def _click_sound_candidates(self):
        try:
            return resolve_click_sound_candidates(
                self.config.get("click_sound_pack"),
                paths.legacy_asset_root(),
            )
        except Exception:
            return [paths.legacy_asset_root() / "sounds" / "click.wav"]

    def _activate(self, character: CharacterSpec) -> None:
        self._stop_clip()
        self.library.close_all()
        self.library = ClipLibrary(str(self.config.get("frameseq") or "auto"))
        self.character = character
        self.catalog = GraphCatalog(character.animations)
        self.player = AnimationPlayer(self.catalog)
        self.director = BehaviorDirector(
            self.catalog,
            weights_provider=self._behavior_weights,
            policy_provider=self._animation_policy,
        )
        self.state.current = None
        self.state.dragging = False
        self.dragging = False
        self._action_kind = None
        self._pending_flip = False
        self._move_timer.stop()
        self._move_plan = None
        self._move_loop = 0
        self._move_last_frame = -1
        self.motion.mode = "normal"
        self.motion._timer.stop()
        self._gap_until = 0.0
        self.effects.reset()
        self.slingshot.aiming = False
        width, height = content_size(self.state.scale)
        self.setFixedSize(width, height)
        self._update_body_local()
        self._head_pat_tracker.reset()
        self._head_pat_last_name = None
        self._head_pat_no_spec_logged = False
        self._cancel_pointer_holds()

    def set_character(self, character_id: str, *, persist: bool = True) -> None:
        self._edge_probe.cancel("character", restore=True)
        for character in self.available_characters():
            if character.id == character_id:
                self.config.set("character", character_id)
                if persist:
                    self.config.save()
                previous = self.pos()
                self._activate(character)
                self.move(previous)
                self._restore_geometry(persist=persist)
                self._play_idle()
                self.update()
                return

    def rename_character(self) -> None:
        """右键「切换角色 → 重命名当前角色…」：写别名（空 = 恢复默认）并冒泡确认。

        别名是**全局**偏好（同一角色 id 在所有宠上同名），所以优先经 host 写主
        配置并立即落盘；host 没提供这套接口时才退回自己的 config。取消输入
        （含 Esc/关窗）什么都不改。
        """
        character_id = self.character_id
        if not character_id:
            return
        host = getattr(self, "host", None)
        read = getattr(host, "character_alias", None)
        alias = (
            str(read(character_id) or "")
            if callable(read)
            else self.config.character_alias(character_id)
        )
        lookup = getattr(host, "character_default_name", None)
        default_name = (
            str(lookup(character_id) or character_id)
            if callable(lookup)
            else character_id
        )
        result = prompt_alias(
            self, default_name=default_name, current=alias or default_name
        )
        if not result.accepted:
            return
        write = getattr(host, "set_character_alias", None)
        if callable(write):
            write(character_id, result.name)
        else:
            self.config.set_character_alias(character_id, result.name)
            try:
                self.config.save()
            except OSError:
                logger.warning("failed to persist character alias", exc_info=True)
        refresh = getattr(host, "refresh_character_names", None)
        if callable(refresh):
            refresh()
        self.say(alias_bubble_text(result, default_name), 5000)

    def _apply_flags(self, *, initial: bool = False) -> None:
        if self.stream_capture_mode:
            # 直播捕获：普通顶层窗口（不进 Tool 分支），OBS/直播姬才能枚举到。
            flags = (
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
        else:
            flags = (
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        position = self.pos() if not initial else None
        self.setWindowFlags(flags)
        self.setWindowTitle("dsh-pet core" if self.stream_capture_mode else "")
        if position is not None:
            self.move(position)
        if was_visible and not initial:
            self.show()
        # 气泡与桌宠必须是同一图层：置顶跟着走，否则图片气泡会被桌宠盖住。
        self.bubble.set_host_on_top(self.always_on_top)

    def _play_idle(self) -> None:
        spec = self.player.play(GraphType.IDEL, animat=AnimatType.B_LOOP)
        if spec is not None and not self._animation_policy().auto_allowed(spec):
            spec = None
        if spec is None:
            action = self.director.idle() or self.director.choose()
            spec = action.spec if action is not None else None
        if spec is None:
            self._start_fallback()
            return
        self._play_spec(spec, "idle")

    def _play_spec(self, spec: AnimationSpec, kind: str) -> None:
        if self._paused_for_session and kind != "idle":
            # 锁屏期间外部事件（DSH/点击台词）不许把动画重新拉起；
            # 待机由 resume/set_visible 在解锁后重新收敛。
            return
        self._loop_active = False
        if self._effects_lock_active() and not self._spec_allowed_while_locked(spec):
            # 主分支 _effects_filter_switch：探头/彩蛋接管期间只允许待机与转向，
            # 其它素材（act/click/手动播放）一律降级待机。
            idle = self.catalog.find_for(GraphType.IDEL, AnimatType.B_LOOP)
            if idle is None:
                return
            spec, kind = idle, "idle"
        clip = self.library.clip_for(spec)
        if clip is None:
            logger.warning("no clip backend for %s", spec.name)
            self._start_fallback()
            return
        self._cancel_move_plan()
        if self._clip is clip and self.state.current is spec and kind == self._action_kind:
            # 同一 clip 续圈：WebMClip 圈末驻留等 re-arm，硬停会换代并丢末帧；
            # 帧序列在跑时则不要重开（会把 10s 的段拉回第 0 帧）。
            rearm = getattr(clip, "rearm_same", None)
            if callable(rearm):
                rearm()
            else:
                clip.start()
            return
        self._stop_clip()
        self._clip = clip
        clip.clear_callbacks()
        clip.on_frame(self._on_frame)
        clip.on_finished(self._on_finished)
        clip.on_error(self._on_error)
        self.player.current = spec
        self.state.current = spec
        self._action_kind = kind
        self._pending_flip = kind == KIND_TURN and not self._effects_lock_active()
        speed = float(self.config.get("playback_speed") or 1.0)
        if abs(speed - 1.0) > 1e-9:
            clip.set_playback_speed(speed)
        clip.start()
        if clip.current_image() is not None:
            self._on_frame(clip.current_frame_number())
        if kind == KIND_MOVE:
            self._begin_move(spec, clip.duration_seconds)

    def _start_fallback(self) -> None:
        clip = self.library.fallback()
        self._stop_clip()
        self._clip = clip
        clip.clear_callbacks()
        clip.on_frame(self._on_frame)
        clip.on_finished(self._on_finished)
        clip.on_error(self._on_error)
        clip.start()

    def _stop_clip(self) -> None:
        if self._clip is not None:
            self._clip.stop()
            self._clip.clear_callbacks()
            self._clip = None

    def _on_frame(self, _number: int) -> None:
        if self._clip is None:
            return
        image = self._clip.current_image()
        self._image = image
        if image is not None:
            # 只做 QImage→QPixmap；缩放/镜像在 paintEvent 按当前目标矩形做。
            self._pixmap = frame_pixmap(image)
        self._advance_move_from_frame()
        self.update()

    def _mirrored(self) -> bool:
        """``facing == "right"`` 时水平镜像（素材原生朝左，同主分支）。"""
        if self.character is None or self.state.facing != "right":
            return False
        current = self.state.current
        return current is None or current.name not in self.character.text_clips

    def _on_finished(self) -> None:
        if not self.isVisible():
            return
        if self._loop_active:
            # 循环接缝（唱歌/报时）：被探头、击飞、拖拽接管时立即解除，
            # 否则同一段素材一直重播，行为链永远不掷骰。
            if self._effects_lock_active() or self.dragging or self.motion.thrown:
                self._loop_active = False
            else:
                if self._clip is not None:
                    self._clip.start()
                return
        if self._move_plan is not None:
            loops = int(self._move_plan.get("loops") or 1)
            if self._clip is not None and self._move_loop + 1 < loops:
                self._move_loop += 1
                self._move_last_frame = -1
                self._clip.start()
                return
            self._finish_move()
        if self.dragging:
            if self._clip is not None:
                self._clip.start()
            return
        if self.motion.thrown:
            # 飞行/弹跳属于接管态：不掷骰、不切动作，等落地稳定后由
            # _on_motion_stopped() 接回行为链（主分支 THROWN 语义）。
            if self._clip is not None:
                self._clip.start()
            return
        if self._pending_flip:
            self.state.flip()
            self._pending_flip = False
        kind = self._action_kind
        if self._is_idle_dwell(kind):
            # 待机一圈结束就是掷骰时机（主分支 STATE_IDLE 到点 → _roll_next）。
            # 绝不能走 player.advance()：IDEL 是 B_LOOP + loop=True，advance 会
            # 原样返回同一个 idle spec，桌宠就永远停在待机。
            if self._gap_active():
                self._play_idle()
                return
            self._roll_next()
            return
        nxt = self.player.advance()
        if nxt is not None:
            self._play_spec(nxt, kind or "idle")
            return
        self.effects.consume_click_finished()
        self._finish_action(kind)

    def _is_idle_dwell(self, kind: str | None) -> bool:
        current = self.state.current
        return bool(
            kind == "idle"
            and current is not None
            and current.graph is GraphType.IDEL
            and current.animat is AnimatType.B_LOOP
            and current.loop
        )

    def _finish_action(self, kind: str | None) -> None:
        """动作播完：先播 gap 氛围步（待机），到点再掷骰。"""
        gap = float(self.config.get("animation_gap_seconds") or 0.0)
        if gap > 0.0 and kind not in ("click", "drag", "throw", "idle", "head_pat"):
            self._gap_until = time.monotonic() + gap
            self._play_idle()
            return
        self._roll_next()

    def _roll_next(self) -> None:
        self._gap_until = 0.0
        action = self.director.choose()
        if action is None:
            self._play_idle()
            return
        self._play_spec(action.spec, action.kind)

    def _effects_lock_active(self) -> bool:
        """探头会话或击飞彩蛋接管：位置/素材都受限制。"""
        return bool(self.probe_active or self.effects.throw_egg_active)

    @staticmethod
    def _spec_allowed_while_locked(spec: AnimationSpec) -> bool:
        if spec.graph is GraphType.IDEL:
            return True
        return spec.graph is GraphType.MOVE and spec.locate == "in_place"

    def _behavior_weights(self) -> tuple[float, float, float, float]:
        config = self.config
        return normalize_weights(
            config.get("anim_weight_idle"),
            config.get("anim_weight_turn"),
            config.get("anim_weight_act"),
            config.get("anim_weight_move"),
        )

    def _animation_policy(self) -> AnimationPolicy:
        config = self.config
        return policy_from_values(
            auto_off=config.get("anim_auto_off") or (),
            blocked=config.get("anim_blocked") or (),
            random_auto=config.get("anim_random_auto", True),
        )

    def _pick_manual(self, graph: GraphType, animat: AnimatType) -> AnimationSpec | None:
        """按「允许播放」策略挑一个手动入口素材（点击/拖拽/抛掷/摸头）。"""
        policy = self._animation_policy()
        specs = [
            spec
            for spec in self.catalog.specs_for(graph)
            if spec.animat is animat and policy.manual_allowed(spec)
        ]
        if not specs:
            return None
        normal = [spec for spec in specs if spec.mode is ModeType.NOMAL]
        return self.director.rng.choice(normal or specs)

    def _gap_active(self) -> bool:
        return self._gap_until > 0.0 and time.monotonic() < self._gap_until

    def _cancel_animation_gap(self) -> None:
        self._gap_until = 0.0

    def _on_error(self, message: str) -> None:
        logger.warning("clip error: %s", message)

    def _begin_move(self, spec: AnimationSpec, duration: float) -> None:
        self._cancel_move_plan()
        if bool(self.config.get("no_move")) or self.probe_active:
            self._play_idle()
            return
        bounds = self.probe_bounds()
        if bounds is None or self.character is None:
            self._play_idle()
            return
        body_x, _body_y, body_w, _body_h = self.body_rect()
        left, _top, width, _height = bounds
        center_x = body_x + body_w / 2.0
        left_bound = left + MOVE_MARGIN + body_w / 2.0
        right_bound = left + width - MOVE_MARGIN - body_w / 2.0
        direction = choose_move_direction(
            center_x, left_bound, right_bound, MOVE_MIN_PX, rng=self.director.rng
        )
        if direction is None:
            self._play_idle()
            return
        room = (center_x - left_bound) if direction < 0 else (right_bound - center_x)
        if room < MOVE_MIN_PX:
            self._play_idle()
            return
        distance = self.director.rng.randint(MOVE_MIN_PX, min(MOVE_MAX_PX, int(room)))
        stride_px = stride_for(spec, self.character.move_strides) * self.state.scale
        loops, distance_px, move_duration = quantize_move(
            distance, stride_px, room, max(0.001, float(duration or 0.0))
        )
        if loops <= 0 or distance_px <= 0.0 or move_duration <= 0.0:
            self._play_idle()
            return

        # 朝向先于首帧：否则第一帧仍按旧朝向镜像，看起来和位移相反。
        self.state.facing = "right" if direction > 0 else "left"
        self._pending_flip = False
        self._on_frame(0)

        clip = self._clip
        curve = (self.character.move_curves or {}).get(spec.name)
        frames_per_loop = len(curve) if curve else max(1, int(clip.frame_count if clip is not None else 1))
        start_x = float(self.x())
        start_y = float(self.y())
        target_x = start_x + direction * distance_px
        target_y = start_y
        target_x = float(
            self._clamp_position(int(round(target_x)), int(round(target_y)), settle=False)[0]
        )
        self._move_plan = {
            "spec": spec.name,
            "start_x": start_x,
            "start_y": start_y,
            "target_x": target_x,
            "target_y": target_y,
            "loops": loops,
            "frames_per_loop": frames_per_loop,
            "total_frames": frames_per_loop * loops,
            "curve": curve,
            "duration": float(move_duration),
            "started_at": time.monotonic(),
        }
        self._move_loop = 0
        self._move_last_frame = -1
        self._move_timer.start()

    def _tick_move(self) -> None:
        plan = self._move_plan
        if plan is None or self._clip is None or not self.isVisible():
            self._move_timer.stop()
            return
        duration = float(plan.get("duration") or 0.0)
        started = float(plan.get("started_at") or 0.0)
        if duration <= 0.0 or started <= 0.0:
            return
        elapsed = time.monotonic() - started
        if elapsed > duration * 2.0 + 1.0:
            # 帧回调断流（解码停摆/队列空转）：别让宠物永久停在走动姿态，
            # 也别让 `maybe_enter()` 再也不被调用（审计 AUD-33）。
            self._finish_move()
            return
        if int(plan.get("frames_per_loop") or 1) > 1:
            return
        # 冷 meta：clip 只有一帧，帧号推不动 → 按墙钟插值（否则首帧即到终点）。
        progress = min(1.0, max(0.0, elapsed / duration))
        start_x = float(plan.get("start_x") or 0.0)
        start_y = float(plan.get("start_y") or 0.0)
        target_x = float(plan.get("target_x") or start_x)
        target_y = float(plan.get("target_y") or start_y)
        x = start_x + (target_x - start_x) * progress
        y = start_y + (target_y - start_y) * progress
        x, y = self._clamp_position(int(round(x)), int(round(y)))
        self.move(x, y)

    def _cancel_move_plan(self) -> None:
        self._move_timer.stop()
        self._move_plan = None
        self._move_loop = 0
        self._move_last_frame = -1

    def shift_move_plan(self, dx: float, dy: float) -> None:
        """碰撞分离把宠物推开后，把移动计划整体平移，继续走而不是被拉回原位。"""
        plan = self._move_plan
        if plan is None:
            return
        if not dx and not dy:
            return
        plan["start_x"] = float(plan.get("start_x") or 0.0) + dx
        plan["start_y"] = float(plan.get("start_y") or 0.0) + dy
        plan["target_x"] = float(plan.get("target_x") or 0.0) + dx
        plan["target_y"] = float(plan.get("target_y") or 0.0) + dy

    def abort_move_plan(self) -> None:
        """撞上探头宠物等「不能再往前走」的场合：清计划并回待机。"""
        self._cancel_move_plan()
        if self._action_kind == KIND_MOVE:
            self._play_idle()

    def _advance_move_from_frame(self) -> None:
        plan = self._move_plan
        clip = self._clip
        if plan is None or clip is None:
            return
        frame = clip.current_frame_number()
        per_loop = max(1, int(plan.get("frames_per_loop") or 1))
        if frame < self._move_last_frame:
            self._move_loop += 1
        self._move_last_frame = frame
        if self._move_loop >= int(plan.get("loops") or 1):
            self._finish_move()
            return
        frames_elapsed = self._move_loop * per_loop + frame
        x, y = move_position_at_frame(plan, frames_elapsed)
        x, y = self._clamp_position(int(round(x)), int(round(y)))
        self.move(x, y)

    def _finish_move(self) -> None:
        plan = self._move_plan
        if plan is None:
            return
        x, y = self._clamp_position(
            int(round(plan.get("target_x") or self.x())),
            int(round(plan.get("target_y") or self.y())),
            settle=False,
        )
        self.move(x, y)
        self._cancel_move_plan()
        self._edge_probe.maybe_enter()

    def _clamp_position(self, x: int, y: int, *, settle: bool = True) -> tuple[int, int]:
        """位置出口：先屏幕钳制，再岛墙钳制。

        岛墙（``host.island_field``）挂在这里而不是 tick 里：拖拽/漫游/碰撞分离/
        缩放/换屏全都经过它，和屏幕边界同语义——没有采样间隙就没有「钻进→被弹→
        再钻回」的抽搐。宿主没装灵动岛时是一个 ``getattr`` 的距离，行为逐像素不变。

        ``settle=False`` 只算几何、不结算（不算接近速度、不写速度、不清移动计划）：
        发起漫游时先钳一次 60–240px 外的**目标点**，那不是「桌宠移动到这里」。
        """
        x, y = self._clamp_to_screen(x, y)
        field = getattr(self.host, "island_field", None)
        if field is not None:
            x, y = field.clamp(self, x, y, settle=settle)
        return x, y

    def _clamp_to_screen(self, x: int, y: int) -> tuple[int, int]:
        if self._layout is None:
            return x, y
        width, height = self.width(), self.height()
        if bool(self.config.get("multi_screen")):
            return clamp_to_screens(
                x, y, width, height, self._layout.rects(), self._body_local
            )
        name = self.config.get("screen_name")
        screen = self._layout.by_name(name) if name else None
        if screen is None:
            screen = self._layout.by_name(self._layout.primary)
        if screen is None and self._layout.screens:
            # 某些平台/离屏后端的屏幕名为空串，by_name 永远命中不到；退回第一块屏。
            screen = screen_mod.screen_for_rect(
                (x, y, width, height), self._layout
            ) or self._layout.screens[0]
        if screen is None:
            return x, y
        return clamp_to_screen(x, y, screen.rect, self._body_local)

    def _restore_geometry(self, *, persist: bool = True) -> None:
        self._layout = screen_mod.snapshot_from_qt()
        saved = (self.config.get("positions") or {}).get(self.character_id) or {}
        state = ScreenState(
            screen_name=saved.get("screen_name") or self.config.get("screen_name"),
            rx=saved.get("rx") if saved.get("rx") is not None else self.config.get("rx"),
            ry=saved.get("ry") if saved.get("ry") is not None else self.config.get("ry"),
            body_rx=saved.get("body_rx")
            if saved.get("body_rx") is not None
            else self.config.get("body_rx"),
            body_ry=saved.get("body_ry")
            if saved.get("body_ry") is not None
            else self.config.get("body_ry"),
            pos_x=saved.get("pos_x") if saved.get("pos_x") is not None else self.config.get("pos_x"),
            pos_y=saved.get("pos_y") if saved.get("pos_y") is not None else self.config.get("pos_y"),
        )
        x, y, name = screen_mod.restore_position(
            state,
            self._layout,
            self.width(),
            self.height(),
            body_local=self._body_local,
        )
        self.move(x, y)
        self._remember_geometry(name, persist=persist)

    def _remember_geometry(self, screen_name: str | None = None, *, persist: bool = True) -> None:
        if self._layout is None:
            return
        self._layout = screen_mod.snapshot_from_qt()
        name = screen_name or self.config.get("screen_name")
        screen = self._layout.by_name(name) if name else None
        if screen is None:
            screen = screen_mod.screen_for_rect(self.body_rect(), self._layout)
        if screen is None:
            return
        rx, ry = screen_mod.position_ratio(
            self.x(), self.y(), self.width(), self.height(), screen
        )
        body_rx, body_ry = body_position_ratio(
            self.x(),
            self.y(),
            self.width(),
            self.height(),
            screen.rect,
            self._body_local,
        )
        entry = {
            "screen_name": screen.name,
            "rx": rx,
            "ry": ry,
            "body_rx": body_rx,
            "body_ry": body_ry,
            "pos_x": self.x(),
            "pos_y": self.y(),
        }
        positions = dict(self.config.get("positions") or {})
        if self.character_id:
            positions[self.character_id] = dict(entry)
        self.config.data.update(entry)
        self.config.data["positions"] = positions
        if persist:
            self.config.save()

    def handle_screens_changed(self) -> None:
        self._edge_probe.cancel("screens", restore=True)
        if self._layout is None:
            return
        current = screen_mod.snapshot_from_qt()
        state = ScreenState(
            screen_name=self.config.get("screen_name"),
            rx=self.config.get("rx"),
            ry=self.config.get("ry"),
            body_rx=self.config.get("body_rx"),
            body_ry=self.config.get("body_ry"),
            pos_x=self.x(),
            pos_y=self.y(),
        )
        migrated = screen_mod.migrate_layout(
            self._layout,
            current,
            state,
            self.width(),
            self.height(),
            body_local=self._body_local,
        )
        self._layout = current
        if migrated is not None:
            x, y, name = migrated
            self.move(x, y)
            self._remember_geometry(name)
            self.say("显示器布局改变了，我把位置重新放到可见区域了。")
        else:
            x, y = self._clamp_position(self.x(), self.y())
            self.move(x, y)
            self._remember_geometry()

    def set_scale(self, scale: float, *, persist: bool = True) -> None:
        self._edge_probe.cancel("scale", restore=True)
        self._cancel_move_plan()
        self.slingshot.cancel()
        self.effects.reset()
        center = self.frameGeometry().center()
        self.state.set_scale(scale)
        self._update_body_local()
        width, height = content_size(self.state.scale)
        self.setFixedSize(width, height)
        self.move(center.x() - width // 2, center.y() - height // 2)
        x, y = self._clamp_position(self.x(), self.y())
        self.move(x, y)
        self.config.set("scale", self.state.scale)
        if persist:
            self.config.save()
        self._remember_geometry(persist=persist)
        self._on_frame(0)
        if self.bubble.isVisible():
            self.bubble.reposition(self.bubble_anchor_rect())

    def set_click_through(self, value: bool, *, persist: bool = True) -> None:
        self._head_pat_tracker.reset()
        self.click_through = bool(value)
        self.config.set("click_through", self.click_through)
        if persist:
            self.config.save()
        self._apply_click_through()

    def toggle_click_through(self) -> None:
        self.set_click_through(not self.click_through)

    def _apply_click_through(self) -> None:
        self._cancel_pointer_holds()
        if self.click_through and os.name != "nt":
            # 非 Windows 没有 SendInput 转发能力：保持旧的整窗穿透语义。
            if self._input_controller is not None:
                self._input_controller.stop()
                self._input_controller = None
            apply_click_through(self, True)
            return
        # Windows：穿透模式也不再整窗挂 WS_EX_TRANSPARENT，否则右键会被一起放走。
        # 逐像素轮询保留，只由「透明像素」决定是否穿透。
        apply_click_through(self, False)
        if os.name == "nt" and self._input_controller is None:
            self._input_controller = PerPixelInputController(self, self.is_transparent_at)

    def set_always_on_top(self, value: bool, *, persist: bool = True) -> None:
        self.always_on_top = bool(value)
        self.config.set("always_on_top", self.always_on_top)
        if persist:
            self.config.save()
        self._apply_flags()

    def toggle_always_on_top(self) -> None:
        self.set_always_on_top(not self.always_on_top)

    def set_stream_capture_mode(self, value: bool, *, persist: bool = True) -> None:
        """直播捕获兼容：切换成普通顶层窗口，气泡同步跟随。"""
        self.stream_capture_mode = bool(value)
        self.config.set("stream_capture_mode", self.stream_capture_mode)
        if persist:
            self.config.save()
        self._apply_flags()
        if self.stream_capture_mode:
            apply_stream_window(int(self.winId()))
        else:
            apply_tool_window(int(self.winId()))
        self.bubble.set_stream_mode(self.stream_capture_mode)
        if self.isVisible():
            self.show()

    def toggle_hidden(self) -> None:
        self.set_visible(not self.isVisible())

    def set_visible(self, visible: bool) -> None:
        if bool(visible) == self.isVisible():
            return
        if not visible:
            self._head_pat_tracker.reset()
            self._cancel_pointer_holds()
            if self._input_controller is not None:
                self._input_controller.stop()
            self._edge_probe.pause()
            self._stop_clip()
            self._cancel_say_sequence()
            self.bubble.hide()
            self.hide()
            self._sync_host_pet_visibility()
            return
        self.show()
        if self._input_controller is not None and not self._paused_for_session:
            self._input_controller.resume()
        if not self._paused_for_session:
            self._edge_probe.resume()
        # 右键菜单开着的时候不许把桌宠抬到菜单上面（菜单会话期间层级归菜单）。
        if not menu_open():
            self.raise_()
        if self._paused_for_session:
            return
        if self._action_kind is not None and self._clip is not None:
            self._clip.start()
        else:
            self._play_idle()
        self._sync_host_pet_visibility()

    def pause_animation(self) -> None:
        """锁屏/睡眠：停播放节拍与位移动画（保留当前帧），解锁后原地续播。"""
        self._paused_for_session = True
        self._cancel_pointer_holds()
        self._move_timer.stop()
        if self._input_controller is not None:
            self._input_controller.stop()
        self._edge_probe.pause()
        if self._clip is not None:
            self._clip.pause()

    def resume_animation(self) -> None:
        """锁屏/睡眠结束：续播当前帧，不回到第 0 帧。"""
        self._paused_for_session = False
        if not self.isVisible():
            return
        if self._clip is not None:
            self._clip.resume()
        if self._input_controller is not None:
            self._input_controller.resume()
        self._edge_probe.resume()
        if self._move_plan is not None and not self._move_timer.isActive():
            self._move_timer.start()

    def _sync_host_pet_visibility(self) -> None:
        """显隐是四条路（快捷键/托盘全部/托盘逐宠/右键隐藏）共用的收口点。

        灵动岛的「隐藏」态由宠物可见性派生，任何一个入口改了可见性都要通知它；
        host 没有这个钩子（测试替身）时静默。
        """
        sync = getattr(self.host, "_sync_island_pet_visible", None)
        if not callable(sync):
            return
        try:
            sync()
        except Exception:  # noqa: BLE001 - 状态点坏了不许挡住显隐
            logger.debug("island pet visibility failed", exc_info=True)

    def _unrotated_point(self, local: QPoint) -> QPoint:
        angle = self._probe_angle + self.effects.effect_angle()
        if abs(angle) < 1e-6:
            return local
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        radians = math.radians(-angle)
        dx = local.x() - cx
        dy = local.y() - cy
        return QPoint(
            int(round(cx + dx * math.cos(radians) - dy * math.sin(radians))),
            int(round(cy + dx * math.sin(radians) + dy * math.cos(radians))),
        )

    def _unsquash_point(self, local: QPoint) -> QPoint | None:
        if not self.effects.squash_active:
            return local
        x, y, width, height = self.effects.squash_rect()
        if width <= 0 or height <= 0:
            return None
        if not (x <= local.x() < x + width and y <= local.y() < y + height):
            return None
        return QPoint(
            int((local.x() - x) * self.width() / width),
            int((local.y() - y) * self.height() / height),
        )

    def is_transparent_at(self, local: QPoint) -> bool:
        image = self._image
        if image is None or image.isNull():
            return True
        if self.width() <= 0 or self.height() <= 0:
            return True
        # paintEvent 的合成是 R∘S（先旋转坐标系、再把整帧压进 Q 弹矩形），
        # 逆变换必须是 S⁻¹∘R⁻¹：先反旋转，再在**未旋转**的坐标系里反 Q 弹。
        # 顺序反了会让 Q 弹+旋转同时生效时点击偏移 5–20px（审计 AUD-34）。
        local = self._unrotated_point(local)
        unsquashed = self._unsquash_point(local)
        if unsquashed is None:
            return True
        local = unsquashed
        if not (0 <= local.x() < self.width() and 0 <= local.y() < self.height()):
            return True
        ix = int(local.x() * image.width() / self.width())
        iy = int(local.y() * image.height() / self.height())
        if not (0 <= ix < image.width() and 0 <= iy < image.height()):
            return True
        return image.pixelColor(ix, iy).alpha() < 8

    def _frame_target_rect(self) -> QRect:
        """角色帧的绘制矩形：Q 弹用 squash rect，否则按原比例适配并左上对齐。

        旧实现先 ``scaled(..., KeepAspectRatio)`` 再画到 (0,0)：宽高比与画布一致
        时就是整窗；不一致时保持比例、左上留白。这里用目标矩形复现同一语义，
        但不再每帧分配中间 pixmap。
        """
        if self.effects.squash_active:
            x, y, width, height = self.effects.squash_rect()
            return QRect(x, y, width, height)
        width, height = self.width(), self.height()
        pixmap = self._pixmap
        if pixmap is None or pixmap.width() <= 0 or pixmap.height() <= 0:
            return QRect(0, 0, width, height)
        scale = min(width / pixmap.width(), height / pixmap.height())
        target_width = max(1, int(round(pixmap.width() * scale)))
        target_height = max(1, int(round(pixmap.height() * scale)))
        return QRect(0, 0, target_width, target_height)

    def paintEvent(self, _event) -> None:
        if self._pixmap is None:
            return
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        angle = self._probe_angle + self.effects.effect_angle()
        if abs(angle) >= 1e-6:
            center = QPointF(self.width() / 2.0, self.height() / 2.0)
            painter.translate(center)
            painter.rotate(angle)
            painter.translate(-center)
        # 镜像只作用于角色帧（旧实现是在 pixmap 上翻，现在改成绘制变换）；
        # 弹弓轨迹等叠加层保持旧语义：随旋转、不随镜像。
        target = self._frame_target_rect()
        painter.save()
        if self._mirrored():
            painter.translate(2 * target.x() + target.width(), 0)
            painter.scale(-1, 1)
        painter.drawPixmap(target, self._pixmap)
        painter.restore()
        self.slingshot.paint(painter)
        painter.end()

    def _head_pat_is_busy(self) -> bool:
        return bool(
            not self.isVisible()
            or self.click_through
            or self.dragging
            or self.slingshot.aiming
            or self.motion.thrown
            or self._effects_lock_active()
            or self._action_kind not in (None, "idle")
        )

    def _head_pat_hit(self, local: QPoint) -> tuple[float, float, bool]:
        unsquashed = self._unsquash_point(local)
        if unsquashed is None:
            return float(local.x()), float(local.y()), False
        logical = self._unrotated_point(unsquashed)
        x, y, width, height = self._head_local or self._body_local
        inside = x <= logical.x() < x + width and y <= logical.y() < y + height
        if inside and self._image is not None and self.is_transparent_at(local):
            inside = False
        return float(logical.x()), float(logical.y()), inside

    def _head_pat_candidates(self):
        mode = str(self.config.get("head_pat_animation_mode") or HEAD_PAT_DEFAULT_MODE)
        names = self.config.get("head_pat_animation_names") or ()
        return head_pat_specs(
            getattr(self, "catalog", None), mode=mode, names=names
        )

    def _maybe_head_pat(self, position: QPointF) -> None:
        local = position.toPoint()
        x, y, inside = self._head_pat_hit(local)
        busy = self._head_pat_is_busy()
        policy = self._animation_policy()
        candidates = (
            ()
            if busy
            else tuple(
                spec
                for spec in self._head_pat_candidates()
                if policy.manual_allowed(spec)
            )
        )
        if not candidates and not busy:
            self._head_pat_tracker.reset()
            if not self._head_pat_no_spec_logged:
                logger.debug("head pat has no eligible animation for character %s", self.character_id)
                self._head_pat_no_spec_logged = True
            return
        self._head_pat_no_spec_logged = False
        triggered = self._head_pat_tracker.update(
            (x, y),
            inside_head=inside,
            now=time.monotonic(),
            eligible=bool(self.config.get("head_pat_enabled", True)) and not busy,
        )
        if not triggered:
            return
        chosen = choose_head_pat_spec(
            candidates,
            rng=self.director.rng,
            previous_name=self._head_pat_last_name,
        )
        if chosen is None:
            return
        self._head_pat_last_name = chosen.name
        self._cancel_animation_gap()
        self._cancel_move_plan()
        self._play_spec(chosen, "head_pat")

    def leaveEvent(self, event) -> None:
        self._head_pat_tracker.reset()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._head_pat_tracker.reset()
        self._last_cursor_local = event.position()
        if self.click_through:
            self._pass_through_press(event)
            return
        self._classic_press(event)

    def _classic_press(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._click_sound_pair.cancel(self._click_sound_token)
            self._click_sound_token = None
            if self.slingshot.maybe_enter(event.position(), event.position()):
                self._suppress_context_menu = True
                self._press_global = None
                self._press_pos = None
                event.accept()
                return
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # 左键（弹弓发射/普通点击）之后紧跟的右键不再是「进瞄准那一下」。
            self._suppress_context_menu = False
            self._press_global = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            self.dragging = False
            self._click_sound_pair.cancel(self._click_sound_token)
            self._click_sound_token = (
                self._click_sound_pair.begin()
                if bool(self.config.get("click_sound"))
                else None
            )

    def _pass_through_press(self, event) -> None:
        """穿透模式：左键等长按拖动，右键单击/长按菜单（只放走左键）。"""
        if event.button() == Qt.MouseButton.RightButton:
            self._click_sound_pair.cancel(self._click_sound_token)
            self._click_sound_token = None
            if self.slingshot.aiming:
                return
            action = self._pointer.right_press(dragging=self.dragging)
            if action is PointerAction.SLINGSHOT:
                if self.slingshot.maybe_enter(event.position(), event.position()):
                    self._suppress_context_menu = True
                    event.accept()
                return
            if action is PointerAction.PENDING:
                self._right_press_active = True
                self._right_menu_shown = False
                self._right_hold_timer.start()
                event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._suppress_context_menu = False
            self._press_global = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            self.dragging = False
            self._left_press_active = True
            self._left_drag_armed = False
            self._click_sound_pair.cancel(self._click_sound_token)
            self._click_sound_token = None
            self._pointer.left_press()
            self._left_hold_timer.start()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        self._last_cursor_local = event.position()
        if self.slingshot.aiming:
            self._head_pat_tracker.reset()
            self.slingshot.update(event.position())
            return
        self._maybe_head_pat(event.position())
        if self.click_through:
            if (
                self._left_press_active
                and self._left_drag_armed
                and self._press_global is not None
                and self._press_pos is not None
            ):
                delta = event.globalPosition().toPoint() - self._press_global
                x, y = self._clamp_position(
                    self._press_pos.x() + delta.x(), self._press_pos.y() + delta.y()
                )
                self.motion.drag_to(x, y)
            return
        if self._press_global is None or self._press_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self.dragging and delta.manhattanLength() < DRAG_THRESHOLD:
            return
        if not self.dragging:
            if bool(self.config.get("lock_position")):
                return
            if bool(self.config.get("shift_drag")) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                return
            self._head_pat_tracker.reset()
            self.dragging = True
            self.state.dragging = True
            self._probe_drag_throw = self._edge_probe.on_drag_started()
            self._cancel_animation_gap()
            self._cancel_move_plan()
            self.motion.begin_drag(self.x(), self.y())
            spec = self._pick_manual(GraphType.RAISED_STATIC, AnimatType.SINGLE)
            if spec is not None:
                self._play_spec(spec, "drag")
        x, y = self._clamp_position(
            self._press_pos.x() + delta.x(), self._press_pos.y() + delta.y()
        )
        self.motion.drag_to(x, y)

    def _on_left_hold(self) -> None:
        """穿透模式的左键长按：越过阈值才开始拖动。"""
        if not self.click_through or not self._left_press_active:
            return
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._cancel_left_press()
            return
        if bool(self.config.get("lock_position")):
            return
        if bool(self.config.get("shift_drag")) and not (
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            return
        if self._pointer.left_hold() is not PointerAction.DRAG:
            return
        self._left_drag_armed = True
        self._head_pat_tracker.reset()
        self.dragging = True
        self.state.dragging = True
        self._probe_drag_throw = self._edge_probe.on_drag_started()
        self._cancel_animation_gap()
        self._cancel_move_plan()
        self.motion.begin_drag(self.x(), self.y())
        spec = self._pick_manual(GraphType.RAISED_STATIC, AnimatType.SINGLE)
        if spec is not None:
            self._play_spec(spec, "drag")

    def _on_right_hold(self) -> None:
        """穿透模式的右键长按：弹出右键菜单，松手不再触发单击反馈。"""
        if not self.click_through or not self._right_press_active:
            return
        if not (QApplication.mouseButtons() & Qt.MouseButton.RightButton):
            self._cancel_right_press()
            return
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._cancel_right_press()
            return
        if self._pointer.right_hold() is not PointerAction.MENU:
            return
        # 菜单等松手再弹：右键仍按着时 exec() 会让菜单把这一次 release 当成
        # 选中/关闭动作（标准右键菜单也是松手才弹）。
        self._right_menu_armed = True
        self._right_hold_timer.stop()

    def _show_context_menu_at_cursor(self) -> None:
        menu = self.host.build_menu(self)
        if menu is None:
            self._right_menu_shown = False
            return
        self._suppress_context_menu = True
        try:
            show_context_menu(menu, QCursor.pos(), topmost=self.always_on_top)
        finally:
            self._right_menu_shown = False
            QTimer.singleShot(350, self, self._clear_suppress_context_menu)

    def _clear_suppress_context_menu(self) -> None:
        if not self.slingshot.suppresses_context_menu():
            self._suppress_context_menu = False

    def _right_click(self) -> None:
        if self.motion.thrown or self.effects.throw_egg_active:
            return
        if self._edge_probe.on_clicked():
            return
        self._on_click()

    def _cancel_left_press(self) -> None:
        self._left_hold_timer.stop()
        self._left_press_active = False
        self._left_drag_armed = False
        self._press_global = None
        self._press_pos = None
        self._pointer.left_release()

    def _cancel_right_press(self) -> None:
        self._right_hold_timer.stop()
        self._right_press_active = False
        self._right_menu_shown = False
        self._right_menu_armed = False
        self._pointer.right_cancel()

    def _cancel_pointer_holds(self) -> None:
        self._left_hold_timer.stop()
        self._right_hold_timer.stop()
        self._left_press_active = False
        self._left_drag_armed = False
        self._right_press_active = False
        self._right_menu_shown = False
        self._right_menu_armed = False
        self.dragging = False
        self.state.dragging = False
        self._press_global = None
        self._press_pos = None
        self._pointer.reset()

    def mouseReleaseEvent(self, event) -> None:
        self._head_pat_tracker.reset()
        if event.button() == Qt.MouseButton.RightButton:
            self._release_right_button()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.click_through:
            self._release_pass_through_left()
            return
        probe_throw = self._probe_drag_throw
        self._probe_drag_throw = False
        if self.slingshot.aiming:
            self._launch_slingshot(probe_throw=probe_throw)
            return
        dragged = self.dragging
        self.dragging = False
        self.state.dragging = False
        self._press_global = None
        self._press_pos = None
        if dragged:
            self._finish_drag_release(probe_throw=probe_throw)
        elif self._edge_probe.on_clicked():
            self._click_sound_pair.cancel(self._click_sound_token)
            self._click_sound_token = None
            return
        else:
            played = self._click_sound_pair.release(
                self._click_sound_token, self._click_sound_candidates()
            )
            self._click_sound_token = None
            self._on_click(sound_already_played=played)

    def _release_right_button(self) -> None:
        if self.slingshot.aiming:
            self.slingshot.cancel()
            self._pointer.right_cancel()
            return
        if not self.click_through:
            return
        self._right_hold_timer.stop()
        armed = self._right_menu_armed
        action = self._pointer.right_release()
        self._right_press_active = False
        self._right_menu_shown = False
        self._right_menu_armed = False
        if armed:
            if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
                self._show_context_menu_at_cursor()
            return
        if action is PointerAction.CLICK:
            self._right_click()

    def _release_pass_through_left(self) -> None:
        self._left_hold_timer.stop()
        armed = self._left_drag_armed
        action = self._pointer.left_release()
        self._left_press_active = False
        self._left_drag_armed = False
        probe_throw = self._probe_drag_throw
        self._probe_drag_throw = False
        if self.slingshot.aiming:
            self._launch_slingshot(probe_throw=probe_throw)
            return
        if armed or self.dragging:
            self._finish_drag_release(probe_throw=probe_throw)
            return
        self.dragging = False
        self.state.dragging = False
        self._press_global = None
        self._press_pos = None
        self._click_sound_pair.cancel(self._click_sound_token)
        self._click_sound_token = None
        if action is PointerAction.FORWARD_CLICK:
            forward_left_click(self)

    def _launch_slingshot(self, *, probe_throw: bool) -> None:
        launched = self.slingshot.launch()
        self.dragging = False
        self.state.dragging = False
        self._press_global = None
        self._press_pos = None
        self._remember_geometry()
        self._edge_probe.on_release(True)
        self._click_sound_pair.cancel(self._click_sound_token)
        self._click_sound_token = None
        if launched:
            if probe_throw:
                self.effects.arm_throw_egg()
            self._play_throw_animation()
        else:
            self._play_idle()

    def _finish_drag_release(self, *, probe_throw: bool) -> None:
        self.dragging = False
        self.state.dragging = False
        self._press_global = None
        self._press_pos = None
        self._click_sound_pair.cancel(self._click_sound_token)
        self._click_sound_token = None
        self.motion.release()
        if self.motion.thrown:
            if probe_throw:
                # 从探头姿态拖出去甩飞：同样开启头部跟随速度的旋转。
                self.effects.arm_throw_egg()
        else:
            self._play_idle()
        self._remember_geometry()
        self._edge_probe.on_release(True)

    def _play_throw_animation(self) -> None:
        spec = self._pick_manual(GraphType.RAISED_DYNAMIC, AnimatType.B_LOOP)
        if spec is None:
            spec = self._pick_manual(GraphType.RAISED_DYNAMIC, AnimatType.SINGLE)
        if spec is None:
            # 主分支回退到 drag 悬空素材；没有才回 idle。
            spec = self._pick_manual(GraphType.RAISED_STATIC, AnimatType.SINGLE)
        if spec is not None:
            self._play_spec(spec, "throw" if spec.graph is GraphType.RAISED_DYNAMIC else "drag")
        else:
            self._play_idle()

    def _on_thrown(self) -> None:
        """进入抛掷接管态：停 gap/移动计划，绑定飞行（或拖拽）动画。"""
        self._cancel_animation_gap()
        self._cancel_move_plan()
        self._play_throw_animation()

    def _on_click(self, *, sound_already_played: bool = False) -> None:
        if self.motion.thrown or self.effects.throw_egg_active:
            # 主分支 on_sprite_clicked：非 normal 状态不消费点击。飞行中换素材
            # 会与 16ms 位移叠加成「两个位置的桌宠在闪」。
            return
        self._cancel_animation_gap()
        self._cancel_move_plan()
        if bool(self.config.get("golden_spin_on_click")):
            if bool(self.config.get("golden_spin_direct")):
                self.effects.start_spin(direct=True)
                serial = self._click_balance_serial
                if self._maybe_request_click_balance():
                    self._begin_click_talk_wait(
                        already_presented=self._click_balance_serial != serial
                    )
                else:
                    self._notify_click()
                return
            self.effects.arm_spin_after_click()
        spec = self._pick_manual(GraphType.TOUCH_BODY, AnimatType.SINGLE)
        if spec is not None:
            self._play_spec(spec, "click")
        # 点击素材可能同步解码；Q 弹在切换之后再起表，保证 220ms 真的可见。
        self.effects.start_squash(int(self.config.get("click_squash_ms") or 220))
        if bool(self.config.get("click_sound")) and not sound_already_played:
            QTimer.singleShot(0, self._play_click_sound)
        serial = self._click_balance_serial
        if self._maybe_request_click_balance():
            # 余额气泡先冒；点击台词（点击监听者）等它收起后再通知。
            self._begin_click_talk_wait(
                already_presented=self._click_balance_serial != serial
            )
        else:
            self._notify_click()

    def _maybe_request_click_balance(self) -> bool:
        """按宠物配置异步请求余额；点击线程只提交请求，不执行网络 I/O。

        返回真表示「这次点击的反馈归余额气泡」——调用方据此把 ``_notify_click()``
        排到余额气泡收起之后（否则点击台词先闪一下、再被余额盖掉）。
        """
        if not bool(self.config.get("click_show_balance")):
            return False
        request = getattr(self.host, "request_balance", None)
        if not callable(request):
            # 宿主没有余额回调（子壳 / 测试替身）：点击台词照常立刻冒，与主分支
            # `callable(on_show_balance)` 的回落口径一致。
            return False
        try:
            request(self.slot, force=True)
        except Exception:  # noqa: BLE001 - 余额提示不能破坏点击动画
            logger.debug("click balance request failed", exc_info=True)
            return False
        return True

    def _begin_click_talk_wait(self, *, already_presented: bool = False) -> None:
        """排一次点击反馈：等余额气泡收起；连点只保留一条（跟着最后一次点击）。

        ``already_presented`` 用于缓存命中：余额气泡在 ``request()`` 里同步冒出来了
        （``presented`` 回调早于这里），此时直接进入「等它收起」。
        """
        self._click_talk_pending = True
        self._click_talk_waiting_close = bool(already_presented) and self.bubble.isVisible()
        self._click_talk_timer.start(int(self.CLICK_TALK_FALLBACK_MS))
        if already_presented and not self.bubble.isVisible():
            # 缓存路径但气泡被免打扰/隐藏拦掉了：没有可等的气泡，立刻放台词。
            self._flush_click_talk()

    def notify_click_balance_presented(self) -> None:
        """宿主把余额气泡（含失败原因那条）冒出来之后调用，接续点击台词。"""
        self._click_balance_serial += 1
        if not self._click_talk_pending:
            return
        if self.bubble.isVisible():
            # 等这一条收起：`SpeechBubble.closed` 由 hideEvent 发。
            self._click_talk_waiting_close = True
            return
        # 免打扰 / 窗口隐藏把气泡拦掉了：没有可等的气泡，立刻放台词。
        self._flush_click_talk()

    def _on_bubble_closed(self) -> None:
        if self._click_talk_pending and self._click_talk_waiting_close:
            self._flush_click_talk()

    def _flush_click_talk(self) -> None:
        if not self._click_talk_pending:
            return
        self._click_talk_pending = False
        self._click_talk_waiting_close = False
        self._click_talk_timer.stop()
        self._notify_click()

    def _play_click_sound(self) -> None:
        candidates = self._click_sound_candidates()
        chosen = choose_click_sound(candidates, rng=self.director.rng)
        if chosen is None:
            return
        self.sound.play(chosen)

    def show_easter_egg(self) -> bool:
        """彩蛋入口：默认用气泡显示图片，``easter_egg_display="window"`` 用弹窗。"""
        mode = str(self.config.get("easter_egg_display") or "bubble").strip().lower()
        if mode == "window":
            if self.easter.show():
                return True
            return self._show_easter_egg_in_bubble()
        if self._show_easter_egg_in_bubble():
            return True
        # 气泡失败（关掉了/目录为空/图片坏了）时交给弹窗路径报告原因。
        return bool(self.easter.show())

    def _show_easter_egg_in_bubble(self) -> bool:
        if not bool(self.config.get("easter_egg_enabled", True)):
            return False
        images = self.easter.images()
        if not images:
            return False
        image_path = self.easter.selected_image(images)
        if image_path is None:
            return False
        title = str(self.config.get("easter_egg_title") or DEFAULT_EASTER_EGG_TITLE)
        # 气泡已有点击关闭行为；hint 只用于独立彩蛋窗口，保持旧气泡文案契约。
        caption = title
        return self.show_image(
            image_path,
            caption,
            image_scale=self._image_scale_factor("easter_egg_image_scale"),
        )

    def _notify_click(self) -> None:
        for listener in tuple(self._click_listeners):
            try:
                listener(self)
            except Exception:
                logger.debug("click listener failed", exc_info=True)

    def add_click_listener(self, listener) -> None:
        if listener not in self._click_listeners:
            self._click_listeners.append(listener)

    def remove_click_listener(self, listener) -> None:
        if listener in self._click_listeners:
            self._click_listeners.remove(listener)

    def add_drop_listener(self, listener) -> None:
        if listener not in self._drop_listeners:
            self._drop_listeners.append(listener)

    def remove_drop_listener(self, listener) -> None:
        if listener in self._drop_listeners:
            self._drop_listeners.remove(listener)

    def _notify_drop(self, paths) -> None:
        for listener in tuple(self._drop_listeners):
            try:
                listener(paths)
            except Exception:
                logger.debug("drop listener failed", exc_info=True)

    def _on_motion_bounce(self) -> None:
        self.effects.start_squash(int(self.config.get("click_squash_ms") or 220))
        if bool(self.config.get("collision_sound")):
            self._play_click_sound()

    def island_hit_feedback(self) -> None:
        """撞岛反馈（挤压 + 碰撞音效）。

        节流由碰撞层统一持有（``IslandField.notify_hit``，0.25s）：非抛掷撞岛与
        抛掷撞墙两条链共用一个闸，各留一份就会「先响一声、追着又响一声」。
        """
        self.effects.start_squash(int(self.config.get("click_squash_ms") or 220))
        if bool(self.config.get("collision_sound")):
            self._play_click_sound()

    def _on_motion_stopped(self) -> None:
        if not self.dragging:
            self._play_idle()
            self._edge_probe.maybe_enter()

    def contextMenuEvent(self, event) -> None:
        if self.click_through:
            # 穿透模式的菜单只由右键长按计时弹出，原生右键事件一律吞掉。
            event.accept()
            return
        if (
            self.slingshot.suppresses_context_menu()
            or self._suppress_context_menu
        ):
            if self.slingshot.aiming:
                # 保留「瞄准时右键取消」的手感，但不再弹出菜单。
                self.slingshot.cancel()
            self._suppress_context_menu = False
            event.accept()
            return
        menu = self.host.build_menu(self)
        show_context_menu(menu, event.globalPos(), topmost=self.always_on_top)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.slingshot.aiming:
            self.slingshot.cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        self.feeding.handle_drag_enter(event)

    def dragMoveEvent(self, event) -> None:
        if self.feeding.accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if not self.feeding.accepts(event):
            event.ignore()
            return
        paths = self.feeding._paths(event.mimeData())
        self._notify_drop(paths)
        self.feeding.handle_drop(event)

    def wheelEvent(self, event) -> None:
        steps = list(SCALE_STEPS)
        current = min(steps, key=lambda item: abs(item - self.state.scale))
        index = steps.index(current)
        index += 1 if event.angleDelta().y() > 0 else -1
        index = max(0, min(len(steps) - 1, index))
        self.set_scale(steps[index])
        event.accept()

    def say(self, text: str, duration_ms: int = 4000, *, subtitle: str = "") -> bool:
        self._cancel_say_sequence()
        if not self.isVisible():
            # 隐藏的桌宠不该继续冒泡；隐藏时 set_visible(False) 也会收起气泡。
            return False
        # 设置页只写配置，这里在每次新气泡前同步一次字号（未变时是空操作）。
        self.bubble.set_text_scale(self._text_scale_factor())
        self.bubble.show_text(
            text, self.bubble_anchor_rect(), duration_ms, subtitle=subtitle
        )
        return self.bubble.isVisible()

    def hide_bubble(self) -> None:
        """立即收起气泡（展示会话结束用：音乐暂停不该让最后一句再挂几秒）。"""
        self._cancel_say_sequence()
        if self.bubble.isVisible():
            self.bubble.hide()

    def say_sequence(
        self,
        items,
        *,
        gap_ms: int = 200,
        on_show=None,
    ) -> bool:
        """依次显示多条气泡（``(text, subtitle, duration_ms)``）。

        单实例气泡一次只能显示一条，序列由本方法用单次定时器推进；任何新的
        ``say()`` / ``show_image()`` / 隐藏 / 关闭都会取消剩余条目。
        """
        self._cancel_say_sequence()
        if not self.isVisible():
            return False
        lines = []
        for item in items or ():
            if not item:
                continue
            text = str(item[0] or "").strip()
            if not text:
                continue
            subtitle = str(item[1] or "").strip() if len(item) > 1 else ""
            duration = int(item[2]) if len(item) > 2 else 4000
            lines.append((text, subtitle, max(800, duration)))
        if not lines:
            return False
        self._say_sequence_items = deque(lines)
        self._say_sequence_on_show = on_show
        self._say_sequence_gap_ms = max(0, int(gap_ms))
        self._advance_say_sequence()
        return True

    def _advance_say_sequence(self) -> None:
        if not self._say_sequence_items:
            self._say_sequence_on_show = None
            return
        text, subtitle, duration = self._say_sequence_items.popleft()
        self.bubble.set_text_scale(self._text_scale_factor())
        self.bubble.show_text(
            text, self.bubble_anchor_rect(), duration, subtitle=subtitle
        )
        if self._say_sequence_on_show is not None and self.bubble.isVisible():
            try:
                self._say_sequence_on_show(text)
            except Exception:  # noqa: BLE001 - 通知失败不影响后续气泡
                logger.debug("say_sequence on_show failed", exc_info=True)
        if self._say_sequence_items:
            gap = getattr(self, "_say_sequence_gap_ms", 200)
            self._say_sequence_timer.start(int(duration) + int(gap))

    def _cancel_say_sequence(self) -> None:
        self._say_sequence_timer.stop()
        self._say_sequence_items.clear()
        self._say_sequence_on_show = None

    def _on_bubble_clicked(self) -> None:
        """点气泡 = 打开快速对话（主分支同一条入口）。"""
        opener = getattr(self.host, "open_quick_chat", None)
        if callable(opener):
            opener(self.slot)

    def _text_scale_factor(self) -> float:
        try:
            percent = float(self.config.get("bubble_text_scale") or 100)
        except (TypeError, ValueError):
            percent = 100.0
        return percent / 100.0

    def set_text_scale(self, percent: int, *, persist: bool = True) -> None:
        """设置气泡文字缩放（百分比 50–300），已显示的气泡立即重排。"""
        try:
            value = int(round(float(percent)))
        except (TypeError, ValueError, OverflowError):
            value = 100
        value = max(50, min(300, value))
        self.config.set("bubble_text_scale", value)
        if persist:
            self.config.save()
        self.bubble.set_text_scale(value / 100.0)

    def _image_scale_factor(self, key: str) -> float:
        try:
            percent = float(self.config.get(key) or 100)
        except (TypeError, ValueError):
            percent = 100.0
        return percent / 100.0

    def show_image(
        self,
        path,
        caption: str = "",
        duration_ms: int = 4000,
        image_scale: float | None = None,
    ) -> bool:
        """在气泡里显示一张图；``image_scale`` 缺省用彩蛋图片大小设置。"""
        self._cancel_say_sequence()
        if not self.isVisible():
            return False
        if image_scale is None:
            image_scale = self._image_scale_factor("easter_egg_image_scale")
        self.bubble.set_text_scale(self._text_scale_factor())
        try:
            shown = self.bubble.show_image(
                path,
                self.bubble_anchor_rect(),
                caption,
                duration_ms,
                image_scale=image_scale,
                dpr=screen_dpr(self),
            )
        except (OSError, ValueError):
            logger.debug("bubble image failed", exc_info=True)
            return False
        if shown and not self.bubble.isVisible():
            logger.warning("bubble image shown but window is not visible: %s", path)
            return False
        return bool(shown)

    def bubble_anchor_rect(self) -> QRect:
        """气泡锚点：角色头部框（回退身体框/整窗），同主分支的可见轮廓锚点。"""
        x, y, width, height = self._head_local or self._body_local
        return QRect(self.x() + x, self.y() + y, width, height)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_bubble_follow()
        self.pet_moved.emit()

    def _schedule_bubble_follow(self) -> None:
        """同一帧内多次 move 只排一次重排（拖拽/抛掷每 16ms 都在动）。"""
        if self._bubble_follow_pending:
            return
        self._bubble_follow_pending = True
        QTimer.singleShot(0, self._follow_bubble)

    def _follow_bubble(self) -> None:
        self._bubble_follow_pending = False
        try:
            if not self.bubble.isVisible():
                return
            self.bubble.reposition(self.bubble_anchor_rect())
        except RuntimeError:
            # 窗口已销毁（关闭/退出）时 singleShot 仍可能落地。
            return

    def play_animation(self, name: str, *, loop: bool = False) -> bool:
        self._cancel_animation_gap()
        spec = (
            self.catalog.find(name, AnimatType.SINGLE)
            or self.catalog.find(name, AnimatType.B_LOOP)
            or self.catalog.find(name, AnimatType.A_START)
        )
        if spec is None:
            return False
        if not self._animation_policy().manual_allowed(spec):
            return False
        self._play_spec(spec, self._kind_for_spec(spec))
        self._loop_active = bool(loop)
        return True

    def release_loop(self) -> bool:
        """解除 ``play_animation(loop=True)``；当前这圈播完就回行为链。"""
        was = bool(getattr(self, "_loop_active", False))
        self._loop_active = False
        return was

    @staticmethod
    def _kind_for_spec(spec: AnimationSpec) -> str:
        if spec.graph is GraphType.IDEL:
            return "idle"
        if spec.graph is GraphType.MOVE:
            return "turn" if spec.locate == "in_place" else "move"
        if spec.graph is GraphType.TOUCH_BODY:
            return "click"
        return "act"

    def play_event(self, group: str, variant: str | int | None = None) -> bool:
        """播放 ``events/<group>/`` 下的语义事件动画（余额档位等）。

        事件动画不进随机动作池；``variant`` 支持名字（可带 ``folder/`` 前缀）
        或按名字排序的索引，留空随机。
        """
        specs = list(self.catalog.specs_for_event(group))
        if not specs:
            return False
        spec: AnimationSpec | None = None
        if isinstance(variant, str) and variant:
            spec = next((item for item in specs if item.name == variant), None)
            if spec is None:
                tail = variant.rsplit("/", 1)[-1]
                spec = next(
                    (
                        item
                        for item in specs
                        if item.name == tail or item.name.endswith(variant)
                    ),
                    None,
                )
        elif isinstance(variant, int) and not isinstance(variant, bool):
            ordered = sorted(specs, key=lambda item: item.name)
            if 0 <= variant < len(ordered):
                spec = ordered[variant]
        policy = self._animation_policy()
        if spec is None:
            allowed = [item for item in specs if policy.manual_allowed(item)]
            if not allowed:
                return False
            spec = self.director.rng.choice(allowed)
        if not policy.manual_allowed(spec):
            return False
        self._cancel_animation_gap()
        self._play_spec(spec, "act")
        return True

    def _corner_position(self, screen) -> tuple[int, int]:
        bx, by, bw, bh = self._body_local
        if (bw, bh) == (self.width(), self.height()):
            x = screen.x + screen.width - self.width() - CORNER_MARGIN
            y = screen.y + screen.height - self.height() - CORNER_MARGIN
        else:
            x = screen.x + screen.width - CORNER_MARGIN - bx - bw
            y = screen.y + screen.height - by - bh
        return clamp_to_screen(x, y, screen.rect, self._body_local)

    def move_to_primary(self) -> None:
        self._layout = screen_mod.snapshot_from_qt()
        primary = self._layout.by_name(self._layout.primary)
        if primary is None:
            return
        x, y = self._clamp_position(*self._corner_position(primary))
        self.move(x, y)
        self._remember_geometry(primary.name)

    def return_corner(self) -> None:
        self._layout = screen_mod.snapshot_from_qt()
        name = self.config.get("screen_name")
        screen = self._layout.by_name(name) if name else None
        if screen is None:
            screen = self._layout.by_name(self._layout.primary)
        if screen is None:
            return
        x, y = self._clamp_position(*self._corner_position(screen))
        self.move(x, y)
        self._remember_geometry(screen.name)

    def reset_position(self) -> None:
        self.move_to_primary()
        self.set_scale(float(self.config.get("scale") or 0.72))

    def set_config_flag(self, key: str, value, *, persist: bool = True) -> None:
        self.config.set(key, value)
        if persist:
            self.config.save()

    def toggle_config_flag(self, key: str) -> None:
        self.set_config_flag(key, not bool(self.config.get(key)))

    def set_playback_speed(self, speed: float, *, persist: bool = True) -> None:
        self.set_config_flag("playback_speed", max(0.5, min(2.0, float(speed))), persist=persist)
        if self._clip is not None:
            self._clip.set_playback_speed(float(self.config.get("playback_speed") or 1.0))

    def trigger_golden_spin(self) -> None:
        self.effects.start_spin(direct=True)

    def spawn_pet(self) -> None:
        if self.host.pets is not None:
            self.host.pets.spawn()

    def clear_spawned_pets(self) -> None:
        if self.host.pets is not None:
            self.host.pets.close_secondary()

    def open_settings(self, initial_page: str | None = None) -> None:
        self.host.open_settings(initial_page, window=self)

    def toggle_dynamic_island(self) -> None:
        toggler = getattr(self.host, "toggle_dynamic_island", None)
        if callable(toggler):
            toggler()

    def reload_mods(self) -> None:
        self.host.reload_mods()

    def open_data_dir(self) -> None:
        self.host.open_data_dir()

    def quit_app(self) -> None:
        self.host.quit_app()

    def run_ai_command(self, command_id: str) -> bool:
        """菜单/热键共用的 AI 入口；扩展缺失时由 core 负责提示。"""
        runner = getattr(self.host, "run_feature_command", None)
        if callable(runner):
            return bool(runner("ai.chat", command_id, slot=self.slot))
        self.say("AI 扩展未安装：可在「扩展管理 → 插件市场」安装")
        return False

    def quit_from_menu(self) -> None:
        """右键「退出」：主宠退整个应用，子宠只收回自己。"""
        if self.slot == 0:
            self.host.quit_app()
            return
        manager = getattr(self.host, "pets", None)
        if manager is None:
            logger.debug("no pet manager for slot %s; closing window only", self.slot)
            self.close()
            return
        if not manager.close_slot(self.slot):
            return
        primary = getattr(manager, "primary", None)
        if primary is not None and primary is not self:
            primary.say("已收回这只子宠。")
        tray = getattr(self.host, "tray", None)
        refresh = getattr(tray, "refresh", None)
        if callable(refresh):
            QTimer.singleShot(0, refresh)

    def closeEvent(self, event) -> None:
        self._head_pat_tracker.reset()
        self._cancel_pointer_holds()
        self._click_sound_pair.cancel_all()
        self._click_sound_token = None
        self._cancel_say_sequence()
        self.sound.stop_all()
        self._stop_clip()
        self.library.close_all()
        if self._input_controller is not None:
            self._input_controller.stop()
        super().closeEvent(event)

    def reload_characters(self, character: CharacterSpec) -> None:
        self._activate(character)
        self._restore_geometry()
        self.show()
        self._play_idle()

    def shutdown(self) -> None:
        self._head_pat_tracker.reset()
        self._cancel_pointer_holds()
        self._click_sound_pair.cancel_all()
        self._click_sound_token = None
        self._click_talk_timer.stop()
        self._click_talk_pending = False
        self._cancel_say_sequence()
        self.sound.stop_all()
        self._edge_probe.shutdown()
        self.motion.shutdown()
        self.effects.shutdown()
        self.easter.close_all()
        self._cancel_animation_gap()
        self._move_timer.stop()
        self._stop_clip()
        self.library.close_all()
        if self._input_controller is not None:
            self._input_controller.stop()
        try:
            self.bubble.closed.disconnect(self._on_bubble_closed)
        except (RuntimeError, TypeError):
            pass
        self.bubble.close()
