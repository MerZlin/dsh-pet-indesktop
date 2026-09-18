# -*- coding: utf-8 -*-
"""歌词显示控制器：把"当前在放什么歌 + 唱到哪句"同步到桌宠气泡。

职责边界（沿用本项目既有的控制器模式，如 ``edge_probe.EdgeProbeController``）：

- 本模块持有轮询定时器与歌词状态，``PetWindow`` 只负责装配与薄委托。
- **不**直接操作窗口私有实现细节，只走窗口的公开 seam。

两个关键设计点：

**进度有两级来源**（实机差异，详见 ``now_playing``）：
- 播放器上报进度（QQ 音乐）→ 直接用真实 position，暂停/拖进度条都能跟随。
- 播放器不上报（网易云、酷狗）→ 以"观察到切歌的时刻"为基准本地累加。

**优先级最低**：歌词气泡必须给告警/审批/交互气泡让路，且不能把自言自语顶掉
又立刻被顶回来。做法是暂停更新 + ``hold_bubble`` 占位（与自言自语同一个让路机制）。

网络取词在后台线程执行，结果经 Qt 信号回主线程，绝不阻塞 UI。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from . import music_lyric, now_playing

log = logging.getLogger(__name__)

# 轮询间隔：与现有 music_sing 一致，兼顾开销与切歌响应速度。
POLL_MS = 1000

# 歌词换句时允许气泡重新选位的概率。气泡位置由"当前尺寸"算得，而每句歌词
# 长短不同，若每句都重算就会一路乱跳；只在换句时以小概率允许移动，
# 其余时候钉在原位（用户要求"不要经常性改变位置"）。
LYRIC_REPOSITION_CHANCE = 0.25

# 别的气泡（点击台词、被动弹窗等）占用时，歌词让路多久（秒）。
# 歌词每拍都会重发，若不拦一道就会把刚弹出的提示瞬间盖掉；用户要求这类
# 弹窗至少完整显示 5 秒。占位期间不断续期，弹窗消失后再等满这个时长才恢复。
LYRIC_YIELD_SECONDS = 5.0

# 标题与歌词的合成格式：第一行常驻歌名，第二行起是当前歌词。
# 气泡最多 3 行、不触发分页，所以标题不会被拆到另一页去。
NOW_SINGING_TEMPLATE = "我在唱《{title}》"
# 纯音乐（配乐/OST/演奏曲）没有可唱的句子，标题也换成"在听"。
NOW_LISTENING_TEMPLATE = "正在听《{title}》"

# 纯音乐时显示的随机提示。风格对齐 pet/persona_presets/whale_maid.json：
# 第一人称「人家」、称用户「主人」、语气软萌带「～」、偶带一点小傲娇。
INSTRUMENTAL_HINTS = (
    "这首歌好好听～主人也一起听听嘛。",
    "没有词的曲子，最适合陪主人发呆啦。",
    "哼，人家听得很认真哦，不是在偷懒。",
    "这段旋律软软的，人家尾巴都跟着晃起来了～",
    "纯音乐呀，主人别走神，人家在陪您听。",
    "只有旋律也好舒服，像趴在海面上晒太阳。",
    "主人～这首没有歌词，人家就安静陪着您听啦。",
    "唔，这首越听越想眯一会儿了呢。",
)


def pick_instrumental_hint(rng=None) -> str:
    """随机取一条纯音乐提示。"""
    import random

    return (rng or random).choice(INSTRUMENTAL_HINTS)


def compose_bubble_text(title_line: str, lyric: str = "") -> str:
    """把常驻标题与当前歌词拼成气泡文本。

    歌名固定在第一行长期显示，歌词在下面跟着走；没有歌词时只显示标题。
    """
    title_line = str(title_line or "").strip()
    lyric = str(lyric or "").strip()
    if not title_line:
        return lyric
    if not lyric:
        return title_line
    return f"{title_line}\n{lyric}"


def split_bubble_text(title_line: str, lyric: str = "") -> tuple[str, str]:
    """拆成 ``(subtitle, text)`` 两个参数喂给气泡。

    有歌词时：标题走 ``subtitle``（独立的一行标签，天然固定在第一行），
    歌词走正文。

    没有歌词时（取词中 / 无词歌）：标题**改走正文**。不能让 subtitle 单独
    出现——气泡在正文为空时会直接不渲染，那样这几秒会是一片空白。
    """
    title = str(title_line or "").strip()
    lyric = str(lyric or "").strip()
    if not lyric:
        return "", title
    return title, lyric


# 歌词提前量（秒）的默认值与可调范围。实机实测歌词比音频晚不到 1 秒；
# 用户明确表示宁可歌词早、不能接受歌词晚，故默认提前 1 秒。
# 允许负值：如果某些播放器反而偏早，用户可往回调。
LYRIC_LEAD_SECONDS = 1.0
LEAD_MIN_SECONDS = -2.0
LEAD_MAX_SECONDS = 3.0


class LyricTracker:
    """把播放位置换算成"当前该显示第几句"。

    纯逻辑、无 Qt 依赖，便于单测。两种模式：

    - ``update_position``：播放器给了真实进度，直接采用。
    - ``update_clock``  ：播放器不给进度，用本地时钟从切歌时刻起累加。
    """

    def __init__(self) -> None:
        self._lines: list[music_lyric.LyricLine] = []
        self._index: int = -1
        # 歌词提前量（秒）。实机实测歌词会比音频晚不到 1 秒；提前显示比滞后
        # 更可接受（滞后会被明显察觉），故统一往前偏一点。
        self.lead: float = LYRIC_LEAD_SECONDS
        # 本地累加模式的状态
        self._anchor_at: float | None = None   # 建立基准时的本地时刻
        self._anchor_pos: float = 0.0          # 基准处的播放位置
        self._paused: bool = False
        self._paused_pos: float | None = None   # 暂停时冻结的位置

    # ------------------------------------------------------------ 状态

    @property
    def has_lyrics(self) -> bool:
        return bool(self._lines)

    @property
    def index(self) -> int:
        return self._index

    def reset(self) -> None:
        """清空全部状态（切歌或停止显示时调用）。"""
        self._lines = []
        self._index = -1
        self._anchor_at = None
        self._anchor_pos = 0.0
        self._paused = False
        self._paused_pos = None

    def load(self, lines: list[music_lyric.LyricLine], *, now: float,
             position: float | None) -> None:
        """装载一首歌的歌词并建立位置基准。"""
        self._lines = list(lines)
        self._index = -1
        self._paused = False
        self._paused_pos = None
        if position is None:
            # 无真实进度：从现在开始本地累加（此刻视为 0）。
            self._anchor_at = now
            self._anchor_pos = 0.0
        else:
            self._anchor_at = now
            self._anchor_pos = position

    # ------------------------------------------------------------ 位置

    def position(self, now: float, *, reported: float | None = None) -> float | None:
        """当前位置（秒）。``reported`` 为播放器上报值，有则以它为准。"""
        if reported is not None:
            # 真实进度可用：顺带校正本地基准，避免两套数据打架。
            self._anchor_at = now
            self._anchor_pos = reported
            self._paused_pos = reported if self._paused else None
            return reported
        if self._paused:
            return self._paused_pos if self._paused_pos is not None else self._anchor_pos
        if self._anchor_at is None:
            return None
        return self._anchor_pos + (now - self._anchor_at)

    def set_paused(self, paused: bool, *, now: float) -> None:
        """播放器暂停/恢复时调用，冻结或顺延本地基准。"""
        if paused == self._paused:
            return
        if paused:
            self._paused_pos = self.position(now)
            self._paused = True
        else:
            if self._paused_pos is not None:
                self._anchor_at = now
                self._anchor_pos = self._paused_pos
            self._paused = False
            self._paused_pos = None

    def line_at(self, position: float | None) -> int:
        """返回该位置对应的歌词下标；无歌词或位置未知时返回 -1。

        采用"最后一个时间戳不晚于当前位置"的行——即唱到哪句显示哪句。
        比较时叠加 ``self.lead``：提前显示好过滞后显示（人耳对"歌词晚了"
        远比"歌词早了"敏感），因此宁可让歌词抢在音频前面一点。
        """
        if position is None or not self._lines:
            return -1
        probe = position + self.lead
        low, high = 0, len(self._lines) - 1
        found = -1
        while low <= high:
            mid = (low + high) // 2
            if self._lines[mid].at <= probe:
                found = mid
                low = mid + 1
            else:
                high = mid - 1
        return found

    def advance(self, now: float, *, reported: float | None = None) -> int:
        """更新并返回当前应显示的行号；与上次相同则返回 -1（无需重绘）。"""
        pos = self.position(now, reported=reported)
        index = self.line_at(pos)
        if index < 0:
            return -1
        if index == self._index:
            return -1
        self._index = index
        return index

    def text_at(self, index: int) -> str:
        if 0 <= index < len(self._lines):
            return self._lines[index].text
        return ""


class MusicLyricController(QObject):
    """驱动"读曲目 → 取词 → 按进度更新气泡"的完整链路。"""

    # 后台取词完成：(曲目标识, 歌词行列表或 None)
    _lyrics_ready = Signal(object, object)

    # 后台 SMTC 采样完成：Playback 或 None。
    # 采样必须离开主线程——get_now_playing() 内部走 asyncio.run() 调 WinRT，
    # 一旦该调用不返回就会**永久阻塞 Qt 主线程**（窗口未响应）。
    _playback_ready = Signal(object)

    # 类级默认：单测直接驱动 _on_lyrics_ready 时未走过 _start_track，
    # 没有这个默认会 AttributeError（同文件既有约定：回调路径的属性要有默认）。
    _pending_playback: Any = None

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.win = window
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._tracker = LyricTracker()

        # 采样在途标志：后台线程还在跑就不再派发新的，避免堆积。
        self._sampling: bool = False
        # 常驻采样线程（懒启动）：见 _sample_loop 说明为何不每拍新建。
        self._sample_thread: threading.Thread | None = None
        self._sample_wake = threading.Event()
        self._sample_stop = threading.Event()
        self._current_key: tuple[str, str] | None = None
        # 本次播放已尝试过取词但失败的曲目——避免反复请求同一首无词的歌。
        self._no_lyric_keys: set[tuple[str, str]] = set()
        self._loading: set[tuple[str, str]] = set()
        # 是否已经历过一次切歌边界。用于跳过"开启功能时正在播、且播放器不报
        # 进度"的那首歌——那种情况无从推断进度，显示只会错位。
        self._primed: bool = False
        # 检测到切歌的时刻。对不上报进度的播放器（网易云），这是唯一可信的
        # 「这首歌从第 0 秒开始」的锚点，必须用它而不是取词完成时刻。
        self._detected_at: float | None = None
        # 常驻标题行（如「我在唱《歌名》」），固定在气泡第一行长期显示。
        self._title_line: str | None = None
        # 当前曲名（纯音乐时要据此换「正在听《歌名》」标题）。
        self._track_title: str = ""
        # 纯音乐模式：不显示歌词、只显示随机提示，且不唱歌。
        self._instrumental: bool = False
        self._hint: str = ""
        # 上一句歌词。同一句持续期间用它重发，避免气泡闪烁。
        self._last_lyric: str = ""
        # 气泡上一次的落点，用于抵消"尺寸变化导致的位置漂移"。
        self._bubble_anchor: Any = None
        self._bubble_pos: Any = None
        # 让路截止时刻：别的弹窗占用期间歌词停发（见 LYRIC_YIELD_SECONDS）。
        self._lyric_yield_until: float = 0.0
        # 右键菜单「退出音乐模式」的临时开关（仅本次运行，不写配置）。
        self._user_mode_off: bool = False
        # 同一首歌内锁定气泡宽度，避免逐句改宽把气泡推来推去。
        self._width_locked: bool = False
        self._last_shown: tuple[str, str] | None = None
        self._last_playing: bool | None = None
        # 取词对应的那次采样。取词是异步的，结果回来时要靠它换算进度，
        # 所以必须留在实例上（也要有类级默认，便于单测直接驱动回调）。
        self._pending_playback: Any = None
        self._lyrics_ready.connect(self._on_lyrics_ready)
        self._playback_ready.connect(self._on_playback_ready)

    # ------------------------------------------------------------ 生命周期

    def sync_enabled(self, on: bool) -> None:
        """设置开关联动：开启则立即采样并开始轮询，关闭则停止并复位。

        菜单里临时"退出音乐模式"期间（``_user_mode_off``）即便配置是开的也
        不启动，直到用户从菜单重新进入。
        """
        self.apply_lead()
        if on and getattr(self, "_user_mode_off", False):
            return
        if on:
            self._start_sample_thread()
            self._timer.start()
            self._on_tick()
        else:
            self._timer.stop()
            self._stop_sample_thread()
            self._reset()

    def _stop_sample_thread(self) -> None:
        """停掉采样线程（幂等）。卡在 WinRT 调用时不强杀——daemon 线程
        随进程退出即可，主线程不受它影响。

        **仍存活的线程保留引用**，不置 None：它可能正卡在 WinRT 里，丢掉引用
        就变成孤儿，之后醒来仍会 emit，与新线程抢 ``_sampling``（同类教训见
        pet/music_detect.py）。已退出的线程才清引用，供下次重建。
        """
        self._sample_stop.set()
        self._sample_wake.set()
        thread = self._sample_thread
        if thread is not None and not thread.is_alive():
            self._sample_thread = None

    def apply_lead(self) -> None:
        """从配置读歌词提前量（秒）。正直=歌词抢先于音频。

        配置缺失或非法时回退到默认值，绝不让坏配置把对轴搞乱。
        """
        try:
            value = float(self.win.cfg.get("music_lyric_lead_seconds", LYRIC_LEAD_SECONDS))
        except (TypeError, ValueError):
            value = LYRIC_LEAD_SECONDS
        # 与设置页的滑块范围保持一致，防止手改配置写出离谱的值。
        value = max(LEAD_MIN_SECONDS, min(LEAD_MAX_SECONDS, value))
        self._tracker.lead = value

    def shutdown(self) -> None:
        self._timer.stop()
        self._stop_sample_thread()
        self._reset()

    # ------------------------------------------------------------ 右键菜单入口

    def set_music_mode_enabled(self, on: bool) -> None:
        """右键菜单「退出/进入音乐模式」：仅本次运行有效，不写配置。

        改的是内存里的开关（再叠加一层自己的启停），所以重启桌宠后仍按设置页
        的配置生效——"退出模式"是临时动作，不该悄悄改掉用户的设置。
        """
        self._user_mode_off = not bool(on)
        if self._user_mode_off:
            self._timer.stop()
            self._reset()
            self._set_instrumental_flag(False)
        else:
            self.sync_enabled(True)

    def music_mode_active(self) -> bool:
        """当前音乐模式是否在跑（供菜单勾选态显示）。"""
        if getattr(self, "_user_mode_off", False):
            return False
        return bool(self.win.cfg.get("music_lyric_enabled", False))

    def skip_track(self, direction: str = "next") -> bool:
        """菜单「切歌」：切到下一首/上一首。

        切歌后 SMTC 的 title 会变，下几拍的轮询自然检测到"换了首歌"并重建基准，
        歌词因此自动跟上，不需要在这里额外处理。
        """
        return now_playing.skip_track(direction)


    def _reset(self) -> None:
        self._tracker.reset()
        self._current_key = None
        self._no_lyric_keys.clear()
        self._loading.clear()
        self._primed = False
        self._detected_at = None
        self._title_line = None
        self._last_lyric = ""
        self._last_shown = None
        self._last_playing = None
        self._lyric_yield_until = 0.0
        self._instrumental = False
        self._hint = ""
        # 必须清掉窗口上的纯音乐标志：否则关掉歌词功能后标志仍是 True，
        # check_music_sing 每拍都会直接 return，桌宠**再也不会唱歌**。
        self._set_instrumental_flag(False)

    # ------------------------------------------------------------ 气泡

    def _bubble_blocked(self) -> bool:
        """告警/审批/交互气泡占用期间，歌词让路（优先级最低）。"""
        if getattr(self.win, "_alert_current", None) is not None:
            return True
        if getattr(self.win, "_sticky_bubble_active", False):
            return True
        bubble = getattr(self.win, "_speech_bubble", None)
        if bubble is not None and getattr(bubble, "_interactive_active", False):
            return True
        return False

    # 哪些气泡是我们自己写进去的（用于识别"被别的弹窗抢占了"）。
    _LYRIC_SUBTITLE_OBJECT = "pet-speech-subtitle"

    def _bubble_taken_by_other(self) -> bool:
        """气泡里现在显示的是不是**别人**的内容（点击台词 / 被动弹窗）。

        歌词每秒都会重发，若不识别这种情况就会把刚弹出的提示瞬间盖掉。
        判据：气泡可见，且它的副标题不是我们写的歌名——我们的标题一定非空，
        所以"副标题为空却可见"或"副标题不等于当前标题"都说明被抢占了。
        """
        bubble = getattr(self.win, "_speech_bubble", None)
        if bubble is None:
            return False
        try:
            if not bubble.isVisible():
                return False
            label = getattr(bubble, "_subtitle_label", None)
            current = label.text() if label is not None else ""
            body = getattr(bubble, "_raw_text", "")
        except Exception:
            return False
        # 无歌词时标题走**正文**、subtitle 为空（见 split_bubble_text），
        # 所以不能只比 subtitle——那会把我们自己写的气泡误判成"被别人抢占"，
        # 于是每拍续期 5 秒，歌词/标题被自己吞掉。
        if body and body == (self._title_line or ""):
            return False
        if body and body == getattr(self, "_last_lyric", ""):
            return False
        return current != (self._title_line or "")

    def _yield_active(self) -> bool:
        """别的气泡正在占用：让路并续期，返回 True 表示本次不要显示歌词。

        续期而非一次性跳过——否则等满 5 秒的瞬间弹窗还在，歌词就又盖上去了。
        """
        now = time.monotonic()
        if self._bubble_taken_by_other():
            self._lyric_yield_until = now + LYRIC_YIELD_SECONDS
            return True
        return now < self._lyric_yield_until

    def _show(self, lyric: str, *, title: str = "", force: bool = False) -> None:
        """把「常驻标题 + 当前歌词」送进气泡。

        标题走气泡的 ``subtitle``（放在最上方、与正文同字号），歌词走正文。

        位置策略（用户要求「不要经常性改变位置」）：气泡的 `_place()` 是
        按"当前尺寸"确定性算的，而歌词每句长短不同 → 尺寸变 → 位置跟着跳。
        所以这里做粘滞处理：**只在歌词真的换句时**、且只在
        ``LYRIC_REPOSITION_CHANCE`` 的概率下才允许重新定位，其余时候沿用
        上一次的位置，避免气泡每句都乱蹦。

        - ``hold_bubble`` 占位，避免刚显示就被自言自语顶掉。
        - 每拍都要重送一次以续期，否则气泡会先于句子超时消失、闪一下再来。
        """
        # 点击台词 / 被动弹窗正在显示：让路，别把它瞬间盖掉。
        if self._yield_active():
            return
        subtitle, text = split_bubble_text(title, lyric)
        if not text and not subtitle:
            return
        if not force and (text, subtitle) == self._last_shown:
            return
        shower = getattr(self.win, "show_bubble", None)
        if not callable(shower):
            return

        # 歌词是否换句：换句才考虑挪位置。
        changed = lyric != self._last_lyric
        allow_move = bool(changed and random.random() < LYRIC_REPOSITION_CHANCE)
        self._last_lyric = lyric

        try:
            hold = getattr(self.win, "hold_bubble", None)
            if callable(hold):
                hold(POLL_MS / 1000.0 * 3)
            # 时长给足一拍有余：真正的续期由每拍重新调用完成。
            shower(text, duration_ms=POLL_MS * 2, subtitle=subtitle or None,
                   title_first=True, width_locked=self._width_locked)
            self._last_shown = (text, subtitle)
            # 首句显示完就把宽度定下来，后续同首歌不再改宽。
            self._width_locked = True
            if allow_move:
                self._remember_bubble_position()
            else:
                self._pin_bubble_position()
        except Exception:
            log.debug("歌词气泡显示失败", exc_info=True)

    def _pin_bubble_position(self) -> None:
        """把气泡钉回上一次的位置，抵消"尺寸变化"引起的漂移。

        **只适用于桌宠没移动的情况**：钉的是绝对屏幕坐标，一旦桌宠动了就
        会把它按在旧位置（实机 bug：开着歌词拖桌宠，气泡停在原地）。
        所以先比对桌宠矩形，变了就交给 reposition() 正常跟随。
        """
        bubble = getattr(self.win, "_speech_bubble", None)
        if bubble is None:
            return
        pos = getattr(self, "_bubble_pos", None)
        if pos is None:
            return
        anchor_fn = getattr(self.win, "visible_content_rect", None)
        anchor_now = anchor_fn() if callable(anchor_fn) else None
        if anchor_now is not None and anchor_now != getattr(self, "_bubble_anchor", None):
            return  # 桌宠移动过：让气泡跟随，不要钉回旧位置
        try:
            bubble.move(pos)
        except Exception:
            log.debug("钉住气泡位置失败", exc_info=True)

    def _remember_bubble_position(self) -> None:
        """记下气泡当前落点**与当时的桌宠矩形**，供下一次粘滞回位。

        必须一起记桌宠矩形：只记气泡坐标的话，桌宠移动后再钉回去就会把气泡
        留在旧位置（实机 bug：开着歌词拖桌宠，气泡停在原地不动）。
        """
        bubble = getattr(self.win, "_speech_bubble", None)
        if bubble is None:
            return
        try:
            if bubble.isVisible():
                self._bubble_pos = bubble.pos()
                # 一并记下当时的桌宠矩形：_pin_bubble_position 靠它判断
                # 桌宠有没有移动过（移动过就不能钉回旧坐标）。
                anchor_fn = getattr(self.win, "visible_content_rect", None)
                if callable(anchor_fn):
                    self._bubble_anchor = anchor_fn()
        except Exception:
            log.debug("记录气泡位置失败", exc_info=True)

    # ------------------------------------------------------------ 主循环

    def _on_tick(self) -> None:
        """定时器回调（主线程）：只负责**派发采样**，绝不自己查询。

        WinRT 的 SMTC 查询走 asyncio.run()，实测会在主线程永久阻塞
        （窗口未响应）。所以这里起后台线程采样，结果经 _playback_ready
        回主线程处理。
        """
        if not getattr(self.win, "isVisible", lambda: False)():
            return
        if self._sampling or self._sample_thread is None:
            return  # 上一拍还没回来 / 线程未起：跳过，避免堆积
        self._sampling = True
        self._sample_wake.set()

    def _sample_loop(self) -> None:
        """**常驻**采样线程：等信号 → 采一次 → 回报，循环直到关闭。

        刻意不每拍新建线程：winrt 会按线程初始化 COM apartment，每秒新建一个
        线程等于每秒多一个 apartment，正是 pet/music_detect.py 记录过的
        「句柄累积可能导致崩溃」（那里为此只初始化一次 COM 对象）。
        常驻线程只初始化一次，且即使 WinRT 调用卡住也只影响本线程。
        """
        while not self._sample_stop.is_set():
            self._sample_wake.wait()
            self._sample_wake.clear()
            if self._sample_stop.is_set():
                break
            try:
                playback = now_playing.get_now_playing()
            except Exception:
                playback = None
            try:
                self._playback_ready.emit(playback)
            except RuntimeError:
                break  # 对象已销毁

    def _start_sample_thread(self) -> None:
        """确保有一条在跑的采样线程（幂等）。

        **必须复位 ``_sample_stop``**：它一旦被 ``_stop_sample_thread`` 置位就没有
        别的复位路径，而 ``_sample_loop`` 的 ``while not _sample_stop.is_set()``
        会在进入时立刻退出——不复位的话「关闭歌词再打开」之后采样永久停摆
        （歌词再也不随播放更新，直到重启桌宠）。
        """
        thread = self._sample_thread
        if thread is not None and thread.is_alive():
            current = threading.current_thread()
            if thread is not current:
                self._sample_stop.clear()
                return  # 复用仍存活的线程，不新建（避免 COM apartment 堆积）
        # 线程已退出（或从未起过）：复位标志后重建，否则新线程一进循环就退出。
        # （clear 是幂等的，上面的复用分支已清过也不要紧。）
        self._sample_stop.clear()
        thread = threading.Thread(
            target=self._sample_loop, name="music-lyric-sample", daemon=True,
        )
        self._sample_thread = thread
        thread.start()

    def _on_playback_ready(self, playback) -> None:
        """采样回到主线程：在这里做原有的状态推进。"""
        self._sampling = False
        if not getattr(self.win, "isVisible", lambda: False)():
            return
        if playback is None:
            # 播放器没了（退出/会话消失）：整体复位，下次从头来过。
            if self._last_playing:
                self._reset()
            self._last_playing = False
            return

        track = playback.track
        key = track.key()
        now = time.monotonic()

        if not track.playing:
            # 暂停：只冻结进度，**不要** reset——否则恢复播放时同一首歌会被
            # 当成"新歌"重新走一遍跳过逻辑，后半首就再也接不上了。
            self._last_playing = False
            if key == self._current_key and self._tracker.has_lyrics:
                self._tracker.set_paused(True, now=now)
            return
        self._last_playing = True

        if key != self._current_key:
            self._start_track(key, track.title, track.artist, playback, now)
            return

        # 纯音乐：只挂「正在听《歌名》」+ 随机提示，不跟进度。
        # 唱歌已由 _instrumental_playing 标志拦下，这里不必反复去停。
        if self._instrumental:
            if not self._bubble_blocked():
                self._show(self._hint, title=self._title_line, force=True)
            return

        # 同一首歌：跟着进度走。标题常驻，歌词换行。
        if not self._tracker.has_lyrics:
            # 还在取词：只显示常驻标题，别留空白。
            if self._title_line and not self._bubble_blocked():
                self._show("", title=self._title_line, force=True)
            return
        self._tracker.set_paused(False, now=now)
        if self._bubble_blocked():
            # 让路期间不推进显示，但位置照常累计，告警结束后能接上。
            self._tracker.position(now, reported=playback.position)
            return
        index = self._tracker.advance(now, reported=playback.position)
        lyric = self._tracker.text_at(index) if index >= 0 else self._last_lyric
        self._last_lyric = lyric
        # 每拍都重送：一是续期（防气泡先于句子超时消失），二是标题必须一直在。
        self._show(lyric, title=self._title_line, force=True)

    def _start_track(self, key, title, artist, playback, now: float) -> None:
        """切歌：先判断能否定位，再决定是否后台取词。"""
        self._current_key = key
        self._tracker.reset()
        self._last_shown = None
        # 记下发现切歌的时刻——这是"这首歌从第 0 秒开始"的锚点。
        # 每首歌都重新记，避免用上一首的旧值把基准带到新歌上。
        self._detected_at = now

        # 功能刚开启时，歌可能已经唱了一半。此时：
        # - 播放器报真实进度（如 QQ 音乐）→ 直接对齐，不受影响；
        # - 播放器不报进度（如网易云）  → 无从推断已唱到第几秒，猜一个起点
        #   只会让歌词一路错位。按约定跳过这首，等下一次可信的切歌边界。
        first_key = not self._primed
        self._primed = True
        if playback.position is None and first_key:
            return

        # 先亮出歌名：一是给用户即时反馈，二是填上取词那几秒的空窗——
        # 否则切歌后会有 3~5 秒什么都不显示。
        self._announce(title, artist)

        if key in self._no_lyric_keys or key in self._loading:
            return

        # 取词走后台线程：轮询里绝不发网络请求。
        self._loading.add(key)
        self._pending_playback = playback
        threading.Thread(
            target=self._fetch_worker,
            args=(key, title, artist),
            name="music-lyric-fetch",
            daemon=True,
        ).start()

    def _announce(self, title: str, artist: str) -> None:
        """建立常驻标题（歌名固定显示在气泡第一行）。

        同时充当取词期间的内容——否则切歌后会有几秒什么都不显示。
        """
        title = str(title or "").strip()
        if not title:
            return
        self._track_title = title
        self._title_line = NOW_SINGING_TEMPLATE.format(title=title)
        self._instrumental = False
        self._hint = ""
        # 新歌重新量宽：上一首的宽度不一定合适。
        self._width_locked = False
        self._set_instrumental_flag(False)
        if self._bubble_blocked():
            return
        self._show("", title=self._title_line, force=True)

    def _enter_instrumental_mode(self, lyrics) -> None:
        """纯音乐：换标题为「正在听」、显示随机提示，并**不唱**。

        曲目没有歌词可唱，所以清掉唱歌状态（由 music_sing 的开关决定是否
        真的在唱），避免桌宠对着纯音乐做唱歌动画。
        """
        self._instrumental = True
        self._hint = pick_instrumental_hint()
        self._tracker.reset()
        self._last_lyric = ""
        title = getattr(self, "_track_title", "") or ""
        if title:
            self._title_line = NOW_LISTENING_TEMPLATE.format(title=title)
        self._set_instrumental_flag(True)
        if self._bubble_blocked():
            return
        self._show(self._hint, title=self._title_line, force=True)

    def _set_instrumental_flag(self, on: bool) -> None:
        """告诉窗口"当前是不是纯音乐"，由它决定要不要唱歌。

        不在这里直接停唱歌：唱歌检测每秒都会跑，两边一开一关会持续打架。
        置一个标志、让唱歌逻辑自己让开，才是干净的切面。
        """
        setter = getattr(self.win, "set_instrumental_playing", None)
        if callable(setter):
            try:
                setter(on)
            except Exception:
                log.debug("设置纯音乐标志失败", exc_info=True)

    def _fetch_worker(self, key, title: str, artist: str) -> None:
        started = time.monotonic()
        try:
            lyrics = music_lyric.fetch_lyrics(title, artist)
        except Exception:
            log.debug("歌词取词线程异常", exc_info=True)
            lyrics = None
        elapsed = time.monotonic() - started
        log.info(
            "歌词取词完成: %s - %s -> %s行%s, 耗时 %.2fs",
            artist, title, len(lyrics.lines) if lyrics else 0,
            "（纯音乐）" if (lyrics and lyrics.instrumental) else "",
            elapsed,
        )
        self._lyrics_ready.emit(key, lyrics)

    def _on_lyrics_ready(self, key, lyrics) -> None:
        """后台取词回到主线程：装载歌词 / 进入纯音乐模式 / 记住"查不到"。"""
        self._loading.discard(key)
        if key != self._current_key:
            return  # 期间已经切歌，丢弃过期结果
        if lyrics is None or (not lyrics.lines and not lyrics.instrumental):
            # 查不到曲目：不再重试，但**标题继续常驻**——
            # 用户要的是"歌名长期显示"，查不到也不该让气泡空掉或消失。
            self._no_lyric_keys.add(key)
            if self._title_line and not self._bubble_blocked():
                self._show("", title=self._title_line, force=True)
            return
        if lyrics.instrumental:
            self._enter_instrumental_mode(lyrics)
            return

        now = time.monotonic()
        playback = self._pending_playback
        reported = getattr(playback, "position", None) if playback is not None else None
        if reported is not None:
            # 有真实进度（如 QQ 音乐）：从"读到该值的时刻"回推到此刻，
            # 抵消取词耗时造成的滞后，因此不存在延迟。
            position = max(0.0, reported + (now - playback.updated_at))
        else:
            # 无真实进度（如网易云）：基准必须是**检测到切歌的时刻**，而不是
            # 取词完成的时刻——否则取词花掉的几秒会整体变成歌词滞后。
            detected = self._detected_at if self._detected_at is not None else now
            position = max(0.0, now - detected)
            log.info("歌词按检测时刻对齐: 已过去 %.2fs", position)
        self._tracker.load(list(lyrics.lines), now=now, position=position)
        if self._bubble_blocked():
            return
        # 立即用「标题 + 当前歌词」刷新，不必等下一拍。
        index = self._tracker.advance(now)
        self._last_lyric = self._tracker.text_at(index) if index >= 0 else ""
        self._show(self._last_lyric, title=self._title_line, force=True)
