# -*- coding: utf-8 -*-
"""歌词显示控制器：把"当前在放什么歌 + 唱到哪句"同步到桌宠气泡。

职责边界（沿用本项目既有的控制器模式，如 ``edge_probe.EdgeProbeController``）：

- 本模块持有轮询定时器与歌词状态，``PetWindow`` 只负责装配与薄委托。
- **不**直接操作窗口私有实现细节，只走窗口的公开 seam。

两个关键设计点：

**进度有两级来源**（实机差异，详见 ``now_playing``）：
- 播放器上报进度（QQ 音乐）→ 直接用真实 position，暂停/拖进度条都能跟随。
- 播放器不上报（网易云、酷狗）→ 以"观察到切歌的时刻"为基准本地累加。

**不是所有会话都是歌**：实测浏览器把网页视频也上报成音乐类型，所以
``now_playing`` 按会话类型与来源做否决（明确的视频、以及关掉开关后的浏览器
会话），被否决的会话整条链路都不参与——不显示、不取词、不唱歌。

**标题不等于歌名**：网页标题（B 站是「【4K】周杰伦《晴天》官方MV_哔哩哔哩
_bilibili」）与 foobar 的窗口标题都不是结构化的歌名/歌手，所以取词前要先过
``music_title`` 清洗；**只有浏览器会话**走这条路（见 :meth:`_lyric_query`），
原生播放器给的是 SMTC 的结构化字段，清洗只会误伤。气泡标题仍显示原始标题。

**优先级最低**：歌词气泡必须给告警/审批/交互气泡让路，且不能把自言自语顶掉
又立刻被顶回来。做法是暂停更新 + ``hold_bubble`` 占位（与自言自语同一个让路机制）。

网络取词在后台线程执行，结果经 Qt 信号回主线程，绝不阻塞 UI。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from . import lyric as music_lyric, now_playing, title as music_title
from .. import window_alerts

log = logging.getLogger(__name__)

# 轮询间隔：与现有 music_sing 一致，兼顾开销与切歌响应速度。
POLL_MS = 1000

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
        # 本曲是否见过播放器上报的真实进度。见过就说明位置可信，手动对齐
        # 必须让路（见 reanchor / MusicLyricController.align_available）。
        # 注意"估算位置不算真值"：网易云给的估算值不算见过（见 load 的 reported）。
        self._reported_position: bool = False

    # ------------------------------------------------------------ 状态

    @property
    def has_lyrics(self) -> bool:
        return bool(self._lines)

    @property
    def index(self) -> int:
        return self._index

    @property
    def uses_reported_position(self) -> bool:
        """位置是否来自播放器上报的真值（True 时不该手动对齐）。"""
        return self._reported_position

    def reset(self) -> None:
        """清空全部状态（切歌或停止显示时调用）。"""
        self._lines = []
        self._index = -1
        self._anchor_at = None
        self._anchor_pos = 0.0
        self._paused = False
        self._paused_pos = None
        self._reported_position = False

    def load(self, lines: list[music_lyric.LyricLine], *, now: float,
             position: float | None, reported: bool | None = None) -> None:
        """装载一首歌的歌词并建立位置基准。

        ``reported`` 说明 ``position`` 的来源：``True`` = 播放器上报的真值；
        ``False`` = 调用方按本地时钟**估算**出来的（网易云这类不上报进度的
        播放器）。``None``（默认）沿用旧口径——``position`` 有值即当真值，
        仅供内部/既有单测的调用点。

        为什么必须区分：估算值不是真值。它若被当成真值，``align_available()``
        就判定"播放器会自己跟快进"从而关掉手动对齐，于是网易云用户拖完进度条
        再也没有纠正手段——而同一个控制器还在提示他去点那个被关掉的菜单
        （2026-09-22 用户反馈「合并后快进进度不行了」）。
        """
        self._lines = list(lines)
        self._index = -1
        self._paused = False
        self._paused_pos = None
        self._reported_position = (
            position is not None if reported is None else bool(reported)
        )
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
            self._reported_position = True
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

    # ------------------------------------------------- 手动对齐（快进 / 中途开始）

    def reanchor(self, position: float, *, now: float) -> None:
        """把本地时钟的基准挪到 ``position``，即"此刻唱到第 position 秒"。

        存在的理由（2026-09-21 实测）：网易云音乐通过 SMTC **完全不上报**播放
        进度（position/end_time 恒为 0，播放中、暂停、切歌、快进都一样），此时
        位置只能靠本地时钟累加，而本地时钟原理上感知不到快进——用户拖一次
        进度条，歌词就永久错位。有了这个入口，用户可以一键把歌词重新对到
        当前正在唱的那句上。

        只影响本地累加模式：``position(reported=...)`` 一旦拿到真实进度就以
        真值优先，手动基准会被覆盖（也正是我们想要的——绝不与真值打架）。
        """
        self._anchor_at = now
        self._anchor_pos = max(0.0, float(position))
        if self._paused:
            # 暂停中也要一起挪，否则恢复播放会弹回旧位置。
            self._paused_pos = self._anchor_pos

    def reanchor_to_line(self, delta: int, *, now: float) -> bool:
        """以"当前句再走 ``delta`` 句"那一行为准；越界返回 False。"""
        if not self._lines:
            return False
        # 还没唱到第一句时（_index == -1），"下一句"应当是第一句本身。
        base = self._index if self._index >= 0 else -1
        target = base + int(delta)
        if not (0 <= target < len(self._lines)):
            return False
        # 查行时会叠加 lead，这里先把它减掉，保证"按一次走一句"——否则行距比
        # 提前量短（快歌/说唱）时按一次会跳过一句。
        self.reanchor(max(0.0, self._lines[target].at - self.lead), now=now)
        self._index = target
        return True

    def line_now(self, now: float, *, reported: float | None = None) -> int:
        """返回当前位置对应的行号，**并把 ``_index`` 同步成该值（含 -1）**。

        与 :meth:`advance` 的区别：``advance`` 用 ``-1`` 同时表示"没有行可显示"
        和"与上一拍相同"（供调用方去重），两者混在一起时，调用方在"拖回第一句
        之前"这种情形下会分不清，只能沿用上一句，于是气泡继续显示错位的旧歌词。
        这里给需要"当前到底是哪一句"的调用方一个不含糊的读数。
        """
        index = self.line_at(self.position(now, reported=reported))
        self._index = index
        return index


class MusicLyricController(QObject):
    """驱动"读曲目 → 取词 → 按进度更新气泡"的完整链路。"""

    # 后台取词完成：(曲目标识, 歌词行列表或 None)
    _lyrics_ready = Signal(object, object)

    # 后台 SMTC 采样完成：(Playback 或 None, 本拍的请求令牌)。
    # 采样必须离开主线程——get_now_playing() 内部走 asyncio.run() 调 WinRT，
    # 一旦该调用不返回就会**永久阻塞 Qt 主线程**（窗口未响应）。
    _playback_ready = Signal(object, object)

    # 类级默认：单测直接驱动 _on_lyrics_ready 时未走过 _start_track，
    # 没有这个默认会 AttributeError（同文件既有约定：回调路径的属性要有默认）。
    _pending_by_key: dict = None

    # 每次取词派发那一拍的暂停累计快照（key → 秒，见 _request_lyrics）。同样是回调
    # 路径要用的字段，故有类级默认；读侧用 `or {}` 兜住 None（测试替身）。
    _paused_base_by_key: dict = None

    # 取词在飞期间的暂停记账（见 _note_paused）：同样的道理，回调路径要有默认值。
    _paused_total: float = 0.0
    _paused_since: float | None = None

    # 采样**请求令牌**：每次派发自增，令牌随结果信号一起回来。回调只认与当前
    # 在途令牌一致的结果；`None` = 没有在途派发（也供单测直接驱动
    # _on_playback_ready 用——不带令牌 = 没有派发过 = 两值相等 = 放行）。
    _inflight_token: int | None = None
    # worker 下一次采样要带的令牌（派发时与 _inflight_token 同时写入）。**只由
    # 主线程写、worker 读一次**，读侧不清零：清零是"读改写"，会与紧随其后的新
    # 派发抢，把新令牌抹掉——那一拍就再也没有结果回来，忙门永久卡死。
    _pending_token: int | None = None

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.win = window
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._tracker = LyricTracker()

        # 采样在途标志：后台线程还在跑就不再派发新的，避免堆积。
        self._sampling: bool = False
        # 令牌来源（只增不减，跨代次唯一）：见 _on_tick / _reset。
        self._request_seq: int = 0
        self._inflight_token: int | None = None
        self._pending_token: int | None = None
        # 常驻采样线程（懒启动）：见 _sample_loop 说明为何不每拍新建。
        self._sample_thread: threading.Thread | None = None
        self._sample_wake = threading.Event()
        self._sample_stop = threading.Event()
        self._current_key: tuple[str, str] | None = None
        # 取词失败曲目的重试冷却（key → 可再次尝试的时刻）。一次网络故障
        # 不该把这首歌在本次会话里永久拉黑（歌词族终审应修 3）——冷却到期后
        # **同曲轮询**（_on_playback_ready 的无歌词分支 → _request_lyrics）会再试
        # 一次，网络恢复即可自愈；每次失败都刷新冷却，所以最密也是每 5 分钟一次，
        # 冷却内不反复请求。
        self._no_lyric_until: dict[tuple[str, str], float] = {}
        self._loading: set[tuple[str, str]] = set()
        # 是否已经历过一次切歌边界。首首（开启功能时已在播）且播放器不报进度时，
        # 「第 0 秒」只能当作检测到这首歌的那一刻——位置是猜的，但**照常取词并
        # 亮出歌名**（跳过等于整段空白），只在那一刻提示一次可用菜单手动对齐。
        # 提示是一次性的，故本标志同时充当"本次会话只提示一次"的闸门。
        self._primed: bool = False
        # 检测到切歌的时刻。对不上报进度的播放器（网易云），这是唯一可信的
        # 「这首歌从第 0 秒开始」的锚点，必须用它而不是取词完成时刻。
        self._detected_at: float | None = None
        # 当前跟踪的播放器标识（Playback.app_id）。带上它去采样，会话选取才会
        # 留在同一个播放器上——本机实测同时有网易云与浏览器会话，丢了这个标识
        # 就会在"暂停一下"时跳到浏览器里那首歌上。
        self._tracked_app_id: str = ""
        # 「该播放器不上报播放进度」的提示每个会话只发一次。
        self._estimated_hint_shown: bool = False
        # 常驻标题行（如「我在唱《歌名》」），固定在气泡第一行长期显示。
        self._title_line: str | None = None
        # 当前曲名（纯音乐时要据此换「正在听《歌名》」标题）。
        self._track_title: str = ""
        # 纯音乐模式：不显示歌词、只显示随机提示，且不唱歌。
        self._instrumental: bool = False
        self._hint: str = ""
        # 上一句歌词。同一句持续期间用它重发，避免气泡闪烁。
        self._last_lyric: str = ""
        # 让路截止时刻：别的弹窗占用期间歌词停发（见 LYRIC_YIELD_SECONDS）。
        self._lyric_yield_until: float = 0.0
        # 右键菜单「退出音乐模式」的临时开关（仅本次运行，不写配置）。
        self._user_mode_off: bool = False
        # 同一首歌内锁定气泡宽度，避免逐句改宽把气泡推来推去。
        self._width_locked: bool = False
        self._last_shown: tuple[str, str] | None = None
        self._last_playing: bool | None = None
        # 取词在飞期间的暂停记账（累计秒 + 当前未完结段的起点）：无真实进度源时
        # 位置按"检测到切歌的时刻"往下估算，而取词那几秒里 tracker 还没有歌词、
        # set_paused 冻结不了任何东西——暂停时长必须在控制器这一层单独记，否则
        # 取词回来时会把它算成播放进度（见 _note_paused）。
        self._paused_total: float = 0.0
        self._paused_since: float | None = None
        # 取词对应的那次采样。取词是异步的，结果回来时要靠它换算进度，
        # 所以必须留在实例上（也要有类级默认，便于单测直接驱动回调）。
        self._pending_by_key: dict = {}
        # 每次派发那一拍的暂停累计快照（key → 秒）。与 _pending_by_key 同生命周期、
        # 同样按 key 绑定：有进度分支完成时只扣"派发之后新增"的暂停（见 _on_lyrics_ready）。
        self._paused_base_by_key: dict = {}
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
        """停掉采样线程（幂等），并结清那一拍"可能永远不回来"的派发（R02）。

        卡在 WinRT 调用时不强杀——daemon 线程随进程退出即可，主线程不受它影响。

        **仍存活的线程保留引用**，不置 None：它可能正卡在 WinRT 里，丢掉引用
        就变成孤儿，之后醒来仍会 emit，与新线程抢 ``_sampling``（同类教训见
        pet/music_detect.py）。已退出的线程才清引用，供下次重建。

        时序（R02）：``_on_tick`` 是**先记** ``_sampling=True`` 再唤醒 worker 的。
        worker 若在起跑前撞上关闭，就会消费掉唤醒口、在 ``_sample_loop`` 的停止检查
        处直接 break——**不发任何结果回调**，于是唯一的常规复位点
        ``_on_playback_ready`` 永不执行，``_sampling`` 卡在 True：重开后每一拍都在
        ``_on_tick`` 第一道闸门返回，唤醒口也再没人置位，采样线程永远停在
        ``wake.wait()`` 上（歌词再也不更新，直到重启应用）。所以关闭必须在这里把
        这拍未消费的派发按**结果回调的同一口径**（只复位 ``_sampling``）结清。

        在途那一拍若稍后真的回来了，不必在这里额外作废：紧随其后的 ``_reset()``
        会把在途令牌作废（``_inflight_token = None``），``_on_playback_ready``
        的令牌守卫会把它丢掉。
        """
        self._sample_stop.set()
        self._sample_wake.set()
        self._sampling = False
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

    def pause(self) -> None:
        """窗口隐藏：停掉 1s 轮询（显示时由窗口钩子按配置恢复）。

        只停表、**不复位状态**：``_on_tick`` 本来就在不可见时短路，隐藏期间
        进度基准同样不推进，所以"停表"与"继续空转"的观感完全一致，而复位会
        顺带清掉纯音乐标志（``_reset`` 清它是给"关掉功能"用的，见那里的说明），
        桌宠恢复显示后就可能对着纯音乐唱起来。
        """
        self._timer.stop()

    def shutdown(self) -> None:
        """窗口关闭 / 会话结束：停表并复位（不可恢复的收口）。"""
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
        # 作废在途那一拍放在**最前**：复位之后到达的采样必须被认成上一拍。
        # 所有关闭路径（sync_enabled(False) / set_music_mode_enabled(False) /
        # shutdown）都经这里，作废即等价于"当前不再启用"。
        #
        # 令牌作废本身不够，还得把忙门结清：`_on_playback_ready` 只认当前在途的
        # 令牌，被作废那一拍的结果回来时不会再清忙门（它不属于任何在途派发）。
        # 而"退出音乐模式"这条路径只停表、不停采样线程（`set_music_mode_enabled`
        # 不叫 `_stop_sample_thread`），忙门若卡在 True，用户再进音乐模式时
        # `_on_tick` 会在第一道闸门返回、唤醒口也再没人置位——歌词这条链路
        # 直到重启应用都不会再更新。
        #
        # 令牌随结果回来（派发时捕获），所以**关闭后立刻重开**时，关闭前那一拍
        # 回来带的仍是旧令牌，不会像"回调读可变单槽"那样读到新派发的令牌而被
        # 当成本代结果接受。
        self._inflight_token = None
        self._sampling = False
        self._current_key = None
        self._no_lyric_until.clear()
        self._loading.clear()
        self._primed = False
        self._detected_at = None
        self._tracked_app_id = ""
        self._title_line = None
        self._last_lyric = ""
        self._last_shown = None
        self._last_playing = None
        self._paused_total = 0.0
        self._paused_since = None
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
        按"当前尺寸 + 鱼的当前位置"确定性算的；尺寸侧由气泡的真锁宽兜住
        （同首歌沿用第一句的列宽，见 speech_bubble 的 `_locked_column`），
        于是每次放置结果天然稳定，**且始终跟着鱼走**。

        注意：不要改成"记住绝对坐标钉回去"——鱼会游走，钉绝对坐标会把
        气泡丢在原地单飞（2026-09-17 实机踩坑）。

        - 歌词是最低优先级的被动内容，不占用重要通知的气泡位。
          联动/聊天接管后，由 ``_yield_active`` 让路，到期自然恢复。
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

        self._last_lyric = lyric

        try:
            # 时长给足一拍有余：真正的续期由每拍重新调用完成。
            shower(text, duration_ms=POLL_MS * 2, subtitle=subtitle or None,
                   title_first=True, width_locked=self._width_locked, reserve=False)
            self._last_shown = (text, subtitle)
            # 首句显示完就把宽度定下来，后续同首歌不再改宽。
            self._width_locked = True
        except Exception:
            log.debug("歌词气泡显示失败", exc_info=True)

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
        # 本拍的**请求令牌**：派发时捕获、随结果一起回来（worker 在真正开始采样
        # 前读走），是两个不可变值，不是"回调再去读的可变槽"。回调侧据此判
        # "是不是关闭/复位之后才回来的"（见 _on_playback_ready）。
        self._request_seq += 1
        self._pending_token = self._request_seq
        self._inflight_token = self._request_seq
        self._sample_wake.set()

    def _browser_media_enabled(self) -> bool:
        """「浏览器媒体会话参与歌词」开关（配置缺失/脏值一律按关处理）。

        在采样线程上读 cfg：Config.get 只是字典取值，与 ``_cache_limit``
        在取词线程上读配置同一个先例。关（默认）时浏览器会话不进歌词链路。
        """
        try:
            return bool(self.win.cfg.get("music_browser_media_enabled", False))
        except Exception:
            return False

    def _foobar_lyrics_enabled(self) -> bool:
        """「foobar2000 歌词」开关（配置缺失/脏值一律按关处理）。

        与上面同规：读配置只在采样线程上做，不另存一份会过期的副本。
        """
        try:
            return bool(self.win.cfg.get("foobar_lyrics_enabled", False))
        except Exception:
            return False

    def _lxmusic_enabled(self) -> bool:
        """「洛雪音乐歌词」开关（配置缺失/脏值一律按开处理）。

        与 foobar 那个开关相反：洛雪的开放 API 给的是**真实进度**，读不到时
        整条源自己返回 None、自动回落 SMTC，所以默认开不带来副作用。
        """
        try:
            return bool(self.win.cfg.get("lxmusic_enabled", True))
        except Exception:
            return True

    def _foobar_title_format(self) -> str:
        """foobar 窗口标题格式串（配置缺失/非字符串一律按"自动"处理）。"""
        try:
            value = self.win.cfg.get("foobar_title_format", "")
        except Exception:
            return ""
        return value.strip() if isinstance(value, str) else ""

    def _lyric_query(self, title: str, artist: str, playback) -> tuple[str, str]:
        """取词用的 ``(歌名, 歌手)``——**只有浏览器媒体会话**才先清洗标题。

        原生播放器（网易云 / QQ 音乐 / Spotify 等）走 SMTC 的结构化字段，标题
        本来就是歌名，清洗只会误伤。浏览器给的是网页标题（B 站是
        「【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili」），直接拿去搜命中率极低
        ——这正是 issue #228 的 B 站侧。清洗规则与误伤防护见 ``music_title``。

        口径与"浏览器会话参与歌词"开关**同一个**：开关关着时浏览器会话本就整条
        不进链路，打开才可能走到这里。
        """
        try:
            if not self._browser_media_enabled():
                return title, artist
            if not now_playing.is_browser_session(getattr(playback, "app_id", "")):
                return title, artist
        except Exception:
            return title, artist
        return music_title.clean_web_title(title, artist)

    def _sample_loop(self) -> None:
        """**常驻**采样线程：等信号 → 采一次 → 回报，循环直到关闭。

        刻意不每拍新建线程：winrt 会按线程初始化 COM apartment，每秒新建一个
        线程等于每秒多一个 apartment，正是 pet/music_detect.py 记录过的
        「句柄累积可能导致崩溃」（那里为此只初始化一次 COM 对象）。
        常驻线程只初始化一次，且即使 WinRT 调用卡住也只影响本线程。

        **令牌在开始采样前读走**（不是回调时再读）：慢查询期间主线程可能已经
        复位并派发下一拍，读晚了就会把这一拍的结果贴上新令牌——那正是"旧结果
        被当本代"的根因。读一次不清零：清零是读改写，会与紧随其后的新派发抢。
        """
        while not self._sample_stop.is_set():
            self._sample_wake.wait()
            self._sample_wake.clear()
            if self._sample_stop.is_set():
                break
            token = self._pending_token
            try:
                playback = now_playing.get_now_playing(
                    self._tracked_app_id,
                    allow_browser=self._browser_media_enabled(),
                    allow_foobar=self._foobar_lyrics_enabled(),
                    foobar_title_format=self._foobar_title_format(),
                    allow_lxmusic=self._lxmusic_enabled(),
                )
            except Exception:
                playback = None
            try:
                self._playback_ready.emit(playback, token)
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

    def _on_playback_ready(self, playback, token=None) -> None:
        """采样回到主线程：只处理**当前在途那一拍**的结果，然后推进状态。

        ``token`` 是派发时捕获、随结果一起回来的请求令牌（不带 = 单测直接驱动，
        没有派发过，``_inflight_token`` 也是 ``None``，两值相等即放行）。

        令牌守卫：关闭歌词（``_reset()`` 作废在途令牌）之后才回来的采样不得再
        推进任何状态。此时 ``_current_key`` 已被清空，放行等于把"关掉歌词前那一
        拍"当成新歌——重新取词 + 歌词上屏，用户明明关了歌词。shutdown 同理
        （同一个 ``_reset``）。

        **不匹配时连忙门都不清**：忙门属于新的在途派发（关闭后立刻重开时，
        ``sync_enabled(True)`` 已同步派发下一拍），被上一代清掉就等于允许两拍
        结果并发推进状态。被作废那一拍的忙门由作废方（``_reset``）结清。
        """
        if token != self._inflight_token:
            return
        self._inflight_token = None
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
        # 记住这次跟的是哪个播放器，下一拍带上它去采样（会话选取据此粘住）。
        # **foobar 窗口标题源不占 SMTC 的跟踪位**：那个位是给会话选取用的，塞一个
        # SMTC 里不存在的 id 只会丢掉"粘住当前播放器"的能力（见
        # now_playing.is_foobar_source）。
        app_id = str(getattr(playback, "app_id", "") or "")
        if not now_playing.is_foobar_source(app_id):
            self._tracked_app_id = app_id

        if not track.playing:
            # 暂停：只冻结进度，**不要** reset——否则恢复播放时同一首歌会被
            # 当成"新歌"重新走一遍跳过逻辑，后半首就再也接不上了。
            self._last_playing = False
            if key == self._current_key:
                if self._tracker.has_lyrics:
                    self._tracker.set_paused(True, now=now)
                else:
                    # 取词在飞（tracker 里还没有歌词）：`set_paused` 冻结不了任何东西
                    # ——位置基准要等歌词到位由 load() 建立，所以这段暂停必须由控制器
                    # 自己记账。不记的话，取词回来按「now - 检测时刻」估算的播放位置
                    # 会把暂停的这几秒算进去，歌词当场跳到未来（B05）。
                    self._note_paused(now)
            return
        self._last_playing = True
        # 恢复播放这一拍给暂停段封口（与 tracker 自己的 set_paused(False) 同一拍）：
        # 取词在飞期间的暂停时长到此为止，之后不再增长。
        self._close_pause_segment(now)

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
            # 取词失败后的同曲重试：冷却到期的判断原先只在 _start_track（切歌路径）
            # 里，而同曲轮询的曲目 key 不变、永远走不到那条判断——一次网络故障就
            # 把这首歌在本次会话里永久拉黑，网络恢复也不会自愈。这里按同一个
            # `_no_lyric_until` 冷却补一次（在途去重与冷却判定都在 _request_lyrics）。
            self._request_lyrics(key, track.title, track.artist, playback, now)
            # 还在取词：只显示常驻标题，别留空白。
            if self._title_line and not self._bubble_blocked():
                self._show("", title=self._title_line, force=True)
            return
        self._tracker.set_paused(False, now=now)
        if self._bubble_blocked():
            # 让路期间不推进显示，但位置照常累计，告警结束后能接上。
            self._tracker.position(now, reported=playback.position)
            return
        index = self._tracker.line_now(now, reported=playback.position)
        # index < 0 = 当前位置早于第一句歌词（例如向后拖回开头）。此时必须让
        # 歌词位空出来、只留歌名——沿用 self._last_lyric 会把上一句留在屏幕上，
        # 这是"拖回去歌词还对不上"的根因。
        lyric = self._tracker.text_at(index) if index >= 0 else ""
        self._last_lyric = lyric
        # 每拍都重送：一是续期（防气泡先于句子超时消失），二是标题必须一直在。
        self._show(lyric, title=self._title_line, force=True)

    # ------------------------------------------------ 取词在飞期间的暂停记账

    def _note_paused(self, now: float) -> None:
        """记下一次"采样说此刻暂停"：开一段暂停区间（幂等）。

        只在**取词在飞**（tracker 还没有歌词）时调用——那时 ``LyricTracker.set_paused``
        冻结不了任何东西（位置基准要等歌词到位才由 ``load()`` 建立）。暂停时长因此
        由控制器自己记账，取词回来算估算位置时扣掉它。
        """
        if getattr(self, "_paused_since", None) is None:
            self._paused_since = now

    def _close_pause_segment(self, now: float) -> None:
        """给暂停区间封口（恢复播放那一拍调用）；没有开着的区间时什么都不做。"""
        since = getattr(self, "_paused_since", None)
        if since is None:
            return
        self._paused_total = getattr(self, "_paused_total", 0.0) + max(0.0, now - since)
        self._paused_since = None

    def _paused_seconds(self, now: float) -> float:
        """迄今累计的暂停时长，**含"此刻仍在暂停"的未完结段**。

        未完结段必须算进来：取词回来时用户还按着暂停，那一刻到检测时刻之间的全部
        时间都不该算进播放进度。
        """
        total = getattr(self, "_paused_total", 0.0)
        since = getattr(self, "_paused_since", None)
        if since is not None:
            total += max(0.0, now - since)
        return total

    def _start_track(self, key, title, artist, playback, now: float) -> None:
        """切歌：先亮歌名，再决定是否后台取词。"""
        self._current_key = key
        self._tracker.reset()
        self._last_shown = None
        # 暂停记账跟着歌走：上一首的暂停时长绝不能算到这一首的进度上。
        self._paused_total = 0.0
        self._paused_since = None
        # 记下发现切歌的时刻——这是"这首歌从第 0 秒开始"的锚点。
        # 每首歌都重新记，避免用上一首的旧值把基准带到新歌上。
        self._detected_at = now

        # 现象与取舍（用户反馈「识别不到我放到一半的歌」，2026-09-21 实测）：
        # - 播放器报真实进度（QQ 音乐 / Chrome）→ 直接用真值，不受影响；
        # - 播放器不报进度（网易云音乐，实测 position / end_time 恒为 0，播放
        #   中、暂停、切歌、快进四个状态采样完全一致）→ 功能开启时歌可能已经
        #   唱了一半，而无从推断已唱到第几秒。
        # 旧实现在这里直接 return，结果是**连歌名都不显示**、整段空白。现在改为：
        # 照常亮出歌名、把此刻当作第 0 秒往下走，并提示一次可手动对齐——位置
        # 可能偏，但用户一键就能纠正（右键菜单「音乐 → 歌词对齐」），总好过
        # 什么都不显示。
        first_boundary = not self._primed
        self._primed = True
        if playback.position is None and first_boundary:
            # 只有"开启功能时已在播"这一首的位置是猜出来的，才值得提示。
            self._maybe_hint_estimated_progress()

        # 先亮出歌名：一是给用户即时反馈，二是填上取词那几秒的空窗——
        # 否则切歌后会有 3~5 秒什么都不显示。
        # 注意这里用**原始** title/artist 显示；取词用的是清洗后的查询词
        # （见 _lyric_query），两者刻意分开：显示不该被清洗规则改写。
        self._announce(title, artist)
        self._request_lyrics(key, title, artist, playback, time.monotonic())

    def _request_lyrics(self, key, title, artist, playback, now: float) -> bool:
        """按需派发一次后台取词（在途去重 + 失败冷却）。返回是否真的派发了。

        两条调用点共用这一份判定：切歌（``_start_track``）与同曲轮询
        （``_on_playback_ready`` 的无歌词分支）。**同曲轮询这条点不可省**——只留
        切歌那一条的话，取词失败后曲目 key 不变就永远不再判定冷却，这首歌在本次
        会话里被永久拉黑（B5）。

        取词走后台线程：轮询里绝不发网络请求。
        """
        if key in self._loading or now < self._no_lyric_until.get(key, 0.0):
            return False
        query_title, query_artist = self._lyric_query(title, artist, playback)
        self._loading.add(key)
        # 采样上下文按**请求键**绑定，不写实例级单一槽（歌词族终审阻塞 1）：
        # A 取词中快切 B 再切回 A 时，实例槽已被 B 覆盖，A 的结果回来会拿 B 的
        # position/reported 装载——A 是估算进度、B 是真值时，A 的手动对齐会被
        # 误判置灰一整首。按 key 绑定后，A 的结果永远只读 A 自己的上下文。
        self._pending_by_key[key] = playback
        # 派发这一拍的暂停累计快照：有进度分支的位置窗口从**这次采样**起算，完成时
        # 只扣这之后新增的暂停。每次派发都刷新——同曲失败重试是一次新派发，窗口也
        # 跟着挪到新的采样上（旧值留着会把冷却期的暂停扣进新窗口，见 _on_lyrics_ready）。
        self._paused_base_by_key[key] = self._paused_seconds(now)
        threading.Thread(
            target=self._fetch_worker,
            args=(key, query_title, query_artist),
            name="music-lyric-fetch",
            daemon=True,
        ).start()
        return True

    # ------------------------------------------------ 手动对齐（快进 / 中途开始）

    def align_available(self) -> bool:
        """当前是否值得提供"手动对齐"。

        只有「有歌词 + 位置靠本地时钟估算」时才需要：播放器报了真实进度
        （Chrome / QQ 音乐）时位置本来就跟得上快进，手动对齐反而会跟真值打架。
        """
        if self._instrumental:
            return False
        if not self._tracker.has_lyrics:
            return False
        return not self._tracker.uses_reported_position

    def resync_to_start(self) -> bool:
        """「回到开头」：把此刻正在唱的那句当作歌曲开头。"""
        return self._resync(0.0)

    def resync_to_line(self, delta: int) -> bool:
        """「上一句 / 下一句」：按歌词行的时间戳重定位。

        比"±N 秒"更贴合用户心智——他听到的是"现在唱这句"，而不是"现在第几秒"，
        所以对准通常两三下就够。越界或不可对齐时返回 False（不动作）。
        """
        if not self.align_available():
            return False
        if not self._tracker.reanchor_to_line(delta, now=time.monotonic()):
            return False
        self._refresh_lyric_now()
        return True

    def nudge(self, seconds: float) -> bool:
        """「±5 秒」：在当前基准上平移，用于微调。"""
        if not self.align_available():
            return False
        now = time.monotonic()
        current = self._tracker.position(now)
        if current is None:
            return False
        return self._resync(current + float(seconds))

    def _resync(self, position: float) -> bool:
        """把本地时钟基准挪到 ``position`` 并立即刷新气泡。"""
        if not self.align_available():
            return False
        self._tracker.reanchor(max(0.0, float(position)), now=time.monotonic())
        self._refresh_lyric_now()
        return True

    def _refresh_lyric_now(self) -> None:
        """按当前基准立刻刷新一次气泡，不必等下一拍定时器。"""
        if self._instrumental or not self._tracker.has_lyrics:
            return
        index = self._tracker.line_now(time.monotonic())
        self._last_lyric = self._tracker.text_at(index) if index >= 0 else ""
        if self._bubble_blocked():
            return
        self._show(self._last_lyric, title=self._title_line, force=True)

    def _maybe_hint_estimated_progress(self) -> None:
        """不报进度的播放器：每个会话只提示一次「快进后用菜单对齐」。

        实测网易云音乐完全不上报播放进度，歌词只能按本地时钟估算——快进与
        中途开始播放都会错位，而且没有任何自动信号可依赖（SMTC 时间轴恒为 0、
        UIA 树里也没有进度控件）。与其让用户以为功能坏了，不如把可用的纠正
        入口告诉他一次。
        """
        if self._estimated_hint_shown:
            return
        self._estimated_hint_shown = True
        alert = getattr(self.win, "show_alert", None)
        if not callable(alert):
            return
        try:
            alert(
                "这个播放器不上报播放进度，歌词按开始时间估算。"
                "快进或从中途开始播放后，用右键菜单「音乐 → 歌词对齐」校正。",
                sticky=False,
                duration_ms=12000,
                alert_id="music-lyric-estimated",
                priority=1,
                alert_type="music_lyric",
            )
        except Exception:
            log.debug("进度估算提示显示失败", exc_info=True)

    def _announce(self, title: str, artist: str) -> None:
        """建立常驻标题（歌名固定显示在气泡第一行）。

        同时充当取词期间的内容——否则切歌后会有几秒什么都不显示。
        """
        title = str(title or "").strip()
        # 新歌重新量宽：上一首的宽度不一定合适。必须在空标题 return 之前
        # 复位——否则无标题曲目沿用上一首的列宽，锁高棘轮也整会话不复位
        # （实审 P2-3）。
        self._width_locked = False
        if not title:
            return
        self._track_title = title
        self._title_line = NOW_SINGING_TEMPLATE.format(title=title)
        self._instrumental = False
        self._hint = ""
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

    def _cache_limit(self) -> int:
        """磁盘歌词缓存条数上限：读配置（config 归一化已夹到 [1, 100000]）。

        在取词线程上执行，任何"取不到配置"（最小窗口替身 / 脏值）都回落模块
        默认：坏配置不能把整次取词炸掉。
        """
        try:
            raw = self.win.cfg.get("music_lyric_cache_limit", music_lyric.CACHE_LIMIT)
            return max(1, int(raw))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return music_lyric.CACHE_LIMIT

    def _fetch_worker(self, key, title: str, artist: str) -> None:
        started = time.monotonic()
        try:
            lyrics = music_lyric.fetch_lyrics(title, artist, cache_limit=self._cache_limit())
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
            # 查不到曲目：记 5 分钟冷却（`_no_lyric_until`），冷却到期后由**同曲
            # 轮询**自动重试一次（`_on_playback_ready` → `_request_lyrics`），
            # 冷却内不复请求——一次网络故障不会把这首歌在本次会话里永久拉黑。
            # 无论查不到与否，**标题继续常驻**：用户要的是"歌名长期显示"，查不到
            # 也不该让气泡空掉或消失。
            self._no_lyric_until[key] = time.monotonic() + 300.0
            if self._title_line and not self._bubble_blocked():
                self._show("", title=self._title_line, force=True)
            return
        if lyrics.instrumental:
            self._enter_instrumental_mode(lyrics)
            return

        now = time.monotonic()
        playback = self._pending_by_key.pop(key, None)
        # 本次派发那一拍快照的累计暂停（见 _request_lyrics）。`or {}` 兜住类级默认
        # None（测试替身直接驱动本回调），拿不到快照时按 0 处理。
        paused_base = (getattr(self, "_paused_base_by_key", None) or {}).pop(key, None)
        reported = getattr(playback, "position", None) if playback is not None else None
        if reported is not None:
            # 有真实进度（如 QQ 音乐）：从"读到该值的时刻"回推到此刻，
            # 抵消取词耗时造成的滞后，因此不存在延迟。再扣掉**本次窗口内**已经
            # 发生的暂停：窗口里面不全是播放时间，不扣的话暂停会被算成进度、歌词
            # 当场跳到未来（与无进度分支同一条口径：只扣锚点之后的暂停）。
            #
            # **必须减去派发基线的增量，而不是整首累计**：`_paused_seconds` 记的是
            # 整首歌的暂停（只在切歌时复位），而同曲失败重试换了新的采样上下文——
            # 窗口起点（这次采样）晚于首试与冷却期那些已封口的暂停段。按整首累计
            # 扣（哪怕按窗口长度封顶）会把这些窗口外的暂停也算进来：30s 冷却暂停撞
            # 上 5s 窗口会把位置算成 100 而不是 105（歌词退回一整句）。
            #
            # 不再需要"按窗口长度封顶"：窗口起点就是基线快照那一刻，之后的暂停段
            # 必然落在 `[updated_at, now]` 内（派发是播放拍，且派发时刻晚于采样时刻），
            # 所以 `paused <= elapsed` 本就恒成立；上限只留 `max(0, ...)` 兜脏值。
            elapsed = max(0.0, now - playback.updated_at)
            paused = max(0.0, self._paused_seconds(now) - (paused_base or 0.0))
            position = max(0.0, reported + elapsed - paused)
        else:
            # 无真实进度（如网易云）：基准必须是**检测到切歌的时刻**，而不是
            # 取词完成的时刻——否则取词花掉的几秒会整体变成歌词滞后。再扣掉这一个
            # 窗口内已经发生的暂停（含"此刻仍在暂停"的未完结段）：暂停不是播放
            # 时间，算进去歌词会当场跳到未来（B05）。
            #
            # 这里**不**减派发基线：本分支的锚点是检测到切歌的时刻（暂停记账在那里
            # 归零），窗口就是 `[检测, now]`；减去基线会把重试前（首试 + 冷却期）的
            # 暂停漏扣，位置反而偏大。两个分支共用的口径是"只扣锚点之后的暂停"，
            # 锚点本身按各自的位置来源取——这是刻意的不同，不是漏改。
            detected = self._detected_at if self._detected_at is not None else now
            paused = self._paused_seconds(now)
            position = max(0.0, now - detected - paused)
            log.info("歌词按检测时刻对齐: 已过去 %.2fs（其中暂停 %.2fs）",
                     now - detected, paused)
        # `reported` 必须一起带下去：这里的 position 在无进度时是**估算值**，
        # 不能让它冒充真值，否则「歌词对齐」会被误判为没必要而整组置灰。
        self._tracker.load(list(lyrics.lines), now=now, position=position,
                           reported=reported is not None)
        # 歌词到位时若「进度估算提示」还占着气泡（首首那 12s），歌词优先——提示是
        # 一次性引导，歌词是功能本体。用既有 resolve_alert 精确收起那一条，不误伤
        # 队列里的其它提醒。
        #
        # **必须排在 `_bubble_blocked()` 早退之前**：那条提示自己就占着
        # `_alert_current`，而 `_bubble_blocked()` 把"正挂着提醒"判为让路——早退先
        # 跑的话这段 resolve 永远不可达（死代码），用户第一首歌 12 秒零帧歌词。
        current = getattr(self.win, "_alert_current", None)
        if current is not None and current.get("id") == "music-lyric-estimated":
            window_alerts.resolve_alert(self.win, "music-lyric-estimated")
        if self._paused_since is not None:
            # 取词期间用户按了暂停、到这一拍还没恢复：以**暂停态**装载，且**不立即
            # 显示**——恢复播放那一拍由 `_on_playback_ready` 的既有 set_paused(False)
            # 把基准顺延接上，那时按正确位置显示（歌词停在暂停那一刻的那句）。
            # 冻结这一步不可省：`load()` 会清掉暂停态，不冻的话恢复后基准仍从 load
            # 的时刻起算，暂停的这几秒又会被算成播放时间。
            #
            # **必须排在 `_bubble_blocked()` 早退之前**：冻结时钟记的是"这一段暂停
            # 有多长"，与"这一刻能不能显示"是两件事——气泡被别的提醒占着时早退，
            # 冻不上的话暂停时长直接算进播放时间（且恢复那一拍的 set_paused(False)
            # 落在未暂停的 tracker 上是空操作，基准再也回不来）。
            self._tracker.set_paused(True, now=now)
            return
        if self._bubble_blocked():
            return
        # 立即用「标题 + 当前歌词」刷新，不必等下一拍。
        index = self._tracker.advance(now)
        self._last_lyric = self._tracker.text_at(index) if index >= 0 else ""
        self._show(self._last_lyric, title=self._title_line, force=True)
