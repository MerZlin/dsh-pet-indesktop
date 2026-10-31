"""音乐运行时：轮询三源采样 → 判定 → 唱歌动画 / 歌词气泡 / 菜单命令（无 Qt）。

一秒一拍（``POLL_MS``）。**所有系统调用只跑在常驻后台线程**（``start()`` 起
``music-smtc``）：主分支实测在主线程查 SMTC 会永久阻塞窗口，所以 GUI 线程这一拍
只做「唤醒采样线程」；上一拍没回来就跳过（不排队、不堆积），结果经
``scheduler.call_later(0)`` 回到 GUI 线程再动动画与气泡。采样线程没了就记一条日志
并在下一拍重拉，**绝不在 GUI 线程上补采一次**（那就是把实测会卡死窗口的调用搬回
主线程）。

## 三件事各有一条状态机

1. **识别**（``recognizer.decide``）：SMTC 为主源、音频会话判"在不在出声"、
   已登记播放器的窗口标题兜底。**读失败与"没在放"是两回事**：失败只记诊断，
   不动歌词、不动动画；"没在放"分两种——播放器**明确暂停**就下一拍停唱（冻结本地
   时钟），"什么都看不到"（``absent``）才等 ``music_sing_grace_seconds`` 的宽限。
2. **歌词**：每首曲子取一次词；失败按 ``LYRIC_RETRY_SECONDS`` 冷却重试，
   上限 ``LYRIC_MAX_ATTEMPTS`` 次并在第一次失败时说清原因。**同一行只冒一次**
   （``petcore/AGENTS.md`` 的硬规则），两句之间至少 ``MIN_BUBBLE_SECONDS``；
   ``say()`` 失败（隐藏 / 设置窗打开 / 让路）**不推进**行号，下一拍还试这一句。
   失败只在**播放中**冒泡：暂停里（或失败结果在暂停之后才回来）静默，并把那一次
   配额退回——暂停不该消费"每曲 3 次"。
3. **命令**：传输控制（``pause/next/prev/stop``）与打开播放器；SMTC 不可用时
   每条命令都**说清原因**（没有会话 / 播放器不支持 / 缺组件），不静默失败。

## 几条刻意的取舍

- **取词只有一条常驻线程**（``lyric-fetch``，单槽队列）：旧实现每次取词都
  ``Thread(...).start()``，一首歌三拨、每拨三源六跳，切歌快的时候十几条线程同时在
  跑，`stop()` 还停不掉。现在同一时刻最多一个请求在飞，新任务替换排队中的那个并
  取消在飞的那个（``fetch_lyrics(cancelled=...)`` 在每跳之间收手）；``stop()``
  置停止位并**有界等待**（``FETCH_JOIN_SECONDS``）线程退出。
- **失败记进内存负缓存**：同一首（同标题）刚失败过，60 秒内不再联网——哪怕曲目
  身份在抖动（艺人字段晚一拍到、SMTC 掉线换窗口源），也不会反复重取。
  每曲 3 次的上限照旧；负缓存只挡"同标题的重复轮次"。
- **身份抖动不算换歌**：暂停里与播放中一视同仁——**歌名相同**的新身份记成当前
  曲目的别名（``_track_aliases``），保留曲目/歌词/取词配额；只有歌名真的变了才
  ``_begin_track``。
- **不因一次缺席清空歌词**：旧实现一次采样失败就 ``_forget_track()``，于是同一
  首歌被当成新歌重新联网取词、重放第一句、重启动画。现在只有"换了一首歌"才替换
  曲目状态，缺席只冻结时钟。
- **唱歌失败不永久放弃**：按 2^n 拍退避重试（上限 ``SING_BACKOFF_MAX_SECONDS``），
  切歌/开关变化立即重置。角色包没有「悠闲哼歌」时，补上素材后下一拍就能唱。
- **"我在唱"与"循环还在"解耦**：核心的 ``_play_spec()`` 是循环的唯一清除点——点击、
  探头、击飞、拖拽、改播别的素材都会把 ``loop`` 标志清掉，而我们这边的租约还留着，
  于是旧实现"以为还在唱"、再也不会重新请求。现在每拍用
  ``PresentationPort.animation_state()`` 复核：循环丢了就重新 ``play_animation``
  （2^n 拍退避，``SING_REARM_MAX_SECONDS`` 封顶），探头/击飞/拖拽接管期间不抢。
- **同一首曲目再来一遍要重新出词**：单曲循环时 ``Playback.key`` 不变，旧实现只看
  行号，第二遍就停在末句。现在进度大幅回退（尾部→头部）= 新一轮：重置行号，用
  **已缓存**的歌词重新逐行显示，不重新联网。不上报进度的播放器（网易云/酷狗恒为 0）
  用估算曲长回绕 + "尾部暂停→播放"边沿判定，局限写在 ``_restart_after_pause``。
- **歌词占着气泡时别人的自动气泡让路**：歌词显示/唱歌期间持有
  ``PresentationPort.begin_scene("media.music")``（每拍 ``refresh_scene`` 续期），
  自言自语这类自动气泡被 Core 拒绝（返回 False）。命令反馈与点击台词走
  ``force=True``，不受影响；定时提醒拿不到气泡时会退回系统通知（Core 既有兜底）。
- **多宠逐只 lease**：``ports.pets`` 存在时按 slot 分别 ``play_animation`` /
  ``release_loop``，切宠不会再出现"A 的循环留在 A 身上、释放打到 B"。
- **每拍一个令牌**：迟到的采样结果直接丢弃，不覆盖更新的判定。

配置一律经 ``HostPorts.config`` **只读**读取（运行时不许持有/改写配置对象）；
歌词偏移是本次运行的会话状态，初始值来自 ``music_lyric_offset_seconds``。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import audio_sessions, now_playing, players, recognizer, sampling, window_source
from . import lyrics as lyrics_mod

log = logging.getLogger("media.music")

#: 播放时让桌宠哼歌的动画名（角色包里 ``random/悠闲哼歌``）。
SING_ANIM = "悠闲哼歌"
#: 展示会话的 owner：歌词/唱歌期间占着气泡，别人的自动气泡被 Core 拒绝。
SCENE_OWNER = "media.music"
#: 轮询间隔：与歌词/唱歌链路的既有口径一致，兼顾开销与切歌响应。
POLL_MS = 1000
#: 一拍采样的在飞上限（秒）：超过它就作废那一拍（SMTC 卡死时靠这条保住后续每一拍）。
#: 正常采样实测 <20ms；给到 3 秒只为容忍冷启动与一次降级读取。
INFLIGHT_LIMIT_SECONDS = 3.0
#: 「同播放器暂停、但这一拍没声音」的确认拍数（峰值门限的迟滞）。PAUSED 的缺席宽限
#: 是 0，单拍静音（淡出/间奏）若不迟滞就会 1Hz 释放—重播（气泡忽隐忽现）。
SOFT_PAUSE_CONFIRM_TICKS = 2
#: 唱歌失败后的退避：第 n 次失败等 ``2^n`` 拍，封顶该值（秒）。**不永久放弃**。
SING_BACKOFF_MAX_SECONDS = 30.0
#: 循环被核心清掉（点击/探头/击飞/拖拽）后重新武装的退避上限（秒）。首次当拍就补。
SING_REARM_MAX_SECONDS = 30.0
#: 曲目开始后先观察几拍再唱：纯音乐的歌词（占位文案 / lrclib 的 instrumental 标记）
#: 通常第一拍就到了，先等一拍能少唱一次"对纯音乐唱歌"。
INSTRUMENTAL_PROBE_TICKS = 1
#: 取词**在飞**时最多等几拍再唱。只在该曲真的有一次取词在途时生效：缓存命中/没网
#: 立刻失败（`_fetch_key` 被清）时不等，所以普通曲目的首句延迟不会变成"每次都等 3 秒"。
LYRIC_PROBE_MAX_TICKS = 3
#: 两句歌词气泡之间的最小间隔（秒）。
MIN_BUBBLE_SECONDS = 2.0
#: 取词失败后的冷却（秒），以及每曲最多尝试次数。
LYRIC_RETRY_SECONDS = 60.0
LYRIC_MAX_ATTEMPTS = 3
#: 取词失败的**内存负缓存**：同一首歌（归一后的标题）在这么多秒内不再联网。
#: 冷却/配额是"每曲"的，而负缓存挡的是**身份抖动出来的新曲目**（同标题、新 key）。
LYRIC_NEGATIVE_TTL = 60.0
#: 负缓存的条目上限（一次运行最多记这么多首，按到期时间淘汰最早的）。
LYRIC_NEGATIVE_LIMIT = 64
#: 取词线程的退出等待上限（秒）：一轮取词最多 ``lyrics.DEFAULT_TIMEOUT``，不能把
#: ``stop()``（GUI 线程）卡满 8 秒。超时就记一条日志，线程靠取消位自行退出。
FETCH_JOIN_SECONDS = 1.0
#: 常驻取词线程名（诊断与测试都按这个名字找它）。
FETCH_THREAD_NAME = "lyric-fetch"
#: 「歌词对齐」每次的步长与循环区间（秒）。
OFFSET_STEP = 0.5
OFFSET_MIN = -3.0
OFFSET_MAX = 3.0
#: 「没在放」的确认拍数：缺席的宽限至少这么久，单拍抖动不许让唱歌循环闪断。
ABSENT_CONFIRM_TICKS = 2
#: 判定"同一首曲目又从头播了"（单曲循环/手动重播）：进度至少要回退这么多秒……
ROUND_REWIND_SECONDS = 5.0
#: ……并且回到开头这么多秒以内（"从尾部回到头部"，拖动进度条不算）。
ROUND_HEAD_SECONDS = 15.0
#: 上一拍必须已经播过曲长的这个比例，否则回退只是用户拖动/换段。
ROUND_END_FRACTION = 0.75
#: 播放器不上报时长时，末句之后再给这么多秒当"尾奏"来估算一轮长度。
ROUND_TAIL_SECONDS = 6.0
#: 不上报进度时把"尾部暂停→又播放"当新一圈的容差（秒）。
ROUND_PAUSE_SLACK = 5.0

DEFAULTS = {
    "music_enabled": True,
    "music_sing_enabled": True,
    "music_sing_grace_seconds": 5.0,
    "music_lyric_enabled": True,
    "music_lyric_lead_seconds": 0.3,
    "music_lyric_cache_limit": 80,
    "music_lyric_offset_seconds": 0.0,
    "music_browser_media_enabled": False,
    "music_player_paths": {},
    "music_player": "",
    "music_filter_enabled": False,
    "music_filter_extra_ids": "",
    "music_title_format": "",
}


def next_offset(current: float) -> float:
    """「歌词对齐」的下一档：+0.5 秒，超过上界回到下界。"""
    value = round(float(current) + OFFSET_STEP, 1)
    if value > OFFSET_MAX + 1e-6:
        return OFFSET_MIN
    return value


def _spawn_thread(work) -> None:
    """一次性后台任务（传输命令这类用户手点的冷路径；取词**不走这里**）。"""
    threading.Thread(target=work, name="music-runtime", daemon=True).start()


def _never() -> bool:
    """``cancelled`` 的默认值：这一轮不取消（同步派发路径没有取消位）。"""
    return False


class _LyricJob:
    """一次取词任务：``work(cancel)`` 在取词线程上跑。

    ``cancel`` 是一个 :class:`threading.Event`：被新一轮顶掉或运行时停止时置位，
    取词循环在**每次发请求之前**看它一眼（``fetch_lyrics(cancelled=...)``）。
    """

    __slots__ = ("work", "cancel")

    def __init__(self, work) -> None:
        self.work = work
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            self.work(self.cancel)
        except Exception:  # 取词任务自己兜住所有异常，这里只防"线程被打断"
            log.exception("lyric fetch job crashed")


class LyricFetchWorker:
    """常驻取词线程：**单槽**请求队列，同一时刻最多一个请求在飞。

    - ``submit(work)`` 把任务放进唯一的槽位：排队中的旧任务被丢弃并置取消位，
      **正在跑的那个也会被置取消位**（它的下一跳之前就会收手）。
    - 线程只建一次（**第一次真正取词时**才建：从不取词的运行时不占线程）；``start()``
      幂等。``stop()`` 置停止位并**有界** ``join``，绝不把 GUI 线程押在一次 8 秒的
      HTTP 上。
    - 取词本身的异常/结果交回 GUI 线程由 ``work`` 自己负责（它持有运行时那半）。
    """

    __slots__ = (
        "_name", "_lock", "_wake", "_closing", "_stopped", "_pending", "_running", "_thread",
    )

    def __init__(self, name: str = FETCH_THREAD_NAME) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._stopped = False
        self._pending: _LyricJob | None = None
        self._running: _LyricJob | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 查询

    @property
    def name(self) -> str:
        return self._name

    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def busy(self) -> bool:
        """有任务在飞或排队（诊断与测试用）。"""
        with self._lock:
            return self._running is not None or self._pending is not None

    def pending(self) -> int:
        with self._lock:
            return 1 if self._pending is not None else 0

    # ---------------------------------------------------------------- 生命周期

    def arm(self) -> None:
        """允许再次 ``submit``（运行时 ``start()`` 时调用）；**不建线程**。

        线程在第一次真正取词时才建：一声不吭的运行时不占一条常驻线程。
        """
        with self._lock:
            self._stopped = False

    def start(self) -> None:
        with self._lock:
            self._stopped = False
            thread = self._thread
            if thread is not None and thread.is_alive():
                self._closing.clear()
                return
            self._closing = threading.Event()
            self._wake = threading.Event()
            self._thread = threading.Thread(
                target=self._loop,
                args=(self._closing, self._wake),
                name=self._name,
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = FETCH_JOIN_SECONDS) -> bool:
        """置停止位 + 取消在飞/排队的任务；返回线程是否已经退出。"""
        with self._lock:
            self._stopped = True
            pending, self._pending = self._pending, None
            running = self._running
        for job in (pending, running):
            if job is not None:
                job.cancel.set()
        self._closing.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return not self.alive()

    def submit(self, work) -> bool:
        """排队一次取词；已 ``stop()`` 时返回 False（调用方放弃这一次）。"""
        with self._lock:
            if self._stopped:
                return False
        self.start()
        if not self.alive():
            # 极小概率的竞态：``start()`` 复用了"已经通过退出判断、正要收尾"的线程。
            # ``stop()``/``submit()`` 都在 GUI 线程上，所以这里重拉不会把已停的线程
            # 复活（那只可能是 stop() 还没跑完，而它会把 _stopped 再置回去）。
            self.start()
        job = _LyricJob(work)
        with self._lock:
            superseded = self._pending
            running = self._running
            self._pending = job
        for older in (superseded, running):
            if older is not None:
                older.cancel.set()
        self._wake.set()
        return True

    # ---------------------------------------------------------------- 线程体

    def _loop(self, closing: threading.Event, wake: threading.Event) -> None:
        try:
            while not closing.is_set():
                if not wake.wait(0.5):
                    continue
                wake.clear()
                while not closing.is_set():
                    with self._lock:
                        job = self._pending
                        self._pending = None
                        self._running = job
                    if job is None:
                        break
                    if closing.is_set():
                        job.cancel.set()
                        break
                    try:
                        job.run()
                    finally:
                        with self._lock:
                            if self._running is job:
                                self._running = None
        finally:
            with self._lock:
                self._running = None
                self._pending = None


@dataclass(frozen=True, slots=True)
class _Request:
    """一拍采样的输入（后台线程读它，GUI 线程写它）。"""

    browser_media_enabled: bool
    tracked_app_id: str
    allowed_ids: frozenset[str]
    title_format: str
    targets: frozenset[str]
    seq: int


class MusicRuntime:
    """轮询器 + 命令集合；所有外部依赖都留了可替换的接缝（单测用替身）。"""

    def __init__(self, context):
        self.context = context
        self.commands = {
            "pause": lambda: self.transport("pause"),
            "next": lambda: self.transport("next"),
            "prev": lambda: self.transport("prev"),
            "stop": lambda: self.transport("stop"),
            "lyric_align": self.lyric_align,
        }
        for key in players.player_keys():
            self.commands[f"open_{key}"] = (lambda player_key=key: self.open_player(player_key))

        # ---- 接缝（测试替换这几项，生产走模块函数） ----
        self.sample_smtc = now_playing.read_sample
        self.sample_audio = self._sample_audio
        self.sample_windows = window_source.snapshot
        self._last_default_audio = ""
        self._next_default_audio_check = 0.0
        self._next_manual_audio_check = 0.0
        self._audio_device_fallback_logged = False
        self._manual_audio_fallback = False
        self.decide = recognizer.decide
        #: SMTC 的有界读取线程（``start()`` 建）：卡死的系统媒体服务不许占住采样线程。
        #: 测试/诊断直接调 ``poll()`` 的走法没有它，仍用上面那个同步接缝。
        self._smtc_reader: now_playing.SmtcReader | None = None
        # 传输控制：**必须是实例属性**。写成类属性会让 `self.control_playback(...)`
        # 变成绑定方法、把 self 塞进 action 位（四个播放命令会 100% 抛 TypeError，
        # 再被 except 吞成"系统媒体控制调用失败"）。
        self.control_playback = now_playing.transport
        self.fetch_lyrics = lyrics_mod.fetch_lyrics
        self.launch_player = players.launch
        #: 取词派发接缝：默认交给常驻线程的单槽队列（**不**每次开线程）。
        #: 测试把它换成同步执行（``lambda work: work()``），结果就在 ``poll()`` 里就位。
        self._fetch_worker = LyricFetchWorker()
        self.background = self._fetch_worker.submit
        #: 一次性后台任务（传输命令这类用户手点的冷路径）。
        self.spawn_task = _spawn_thread
        self.clock = time.monotonic

        self._timer: str | None = None
        self._worker: threading.Thread | None = None
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._generation = 0
        self._started = False
        self._stopped = False
        self._pending: _Request | None = None
        self._inflight = False
        #: 当前在飞那一拍的起点（``clock()``）：超过 ``INFLIGHT_LIMIT_SECONDS`` 就作废。
        self._inflight_since: float | None = None
        self._inflight_warned = False
        #: 采样线程意外退出时只记一条日志（每拍刷屏没有意义）。
        self._worker_lost = False
        #: 降级日志：SMTC 读取超时的开始/恢复各记一条（不逐拍刷屏）。
        self._smtc_degraded_logged = False
        #: 「同播放器暂停、但这一拍没声音」的连拍计数（峰值门限的迟滞，见
        #: ``SOFT_PAUSE_CONFIRM_TICKS``）：单拍静音不许把在播立刻收掉。
        self._soft_pause_beats = 0
        #: 暂停期间窗口源的枚举缓存（2 秒一次，见 ``sampling.WINDOW_PAUSED_INTERVAL``）。
        self._window_cache = sampling.WindowCache()
        #: 每拍递增的采样令牌；落地时丢弃比已落地更旧的包（迟到/乱序）。
        self._seq = 0
        self._delivered_seq = 0
        self._app_id = ""
        self._track: recognizer.Track | None = None
        self._track_key: tuple[str, str] | None = None
        #: 「暂停里见过」与「播放中见过」的降级身份（SMTC 掉线 → 窗口标题源兜底：
        #: 艺人/专辑/时长都没了，``Track.key`` 因此变了）。这些 key 视为**当前曲目**
        #: 的别名：不换歌、不清歌词、不重置取词配额。换歌（``_begin_track``）时清空。
        self._track_aliases: set[tuple[str, str]] = set()
        #: 上一次判定的"是否真的在播"：取词结果回来时据此决定失败要不要冒泡。
        self._playing_now = False
        self._title = ""
        self._lines: tuple[lyrics_mod.LyricLine, ...] = ()
        self._instrumental = False
        self._lyric_index = -1
        self._fetch_key: tuple[str, str] | None = None
        self._fetch_attempts = 0
        self._lyric_retry_at: float | None = None
        self._lyric_reported = False
        #: 取词失败的负缓存：``{归一标题: 到期时刻}``。挡的是"身份抖动出来的新曲目"
        #: （同标题、新 key）——那不是换歌，不该重新联网。
        self._lyric_negative: dict[str, float] = {}
        self._lyric_source = ""
        self._lyric_score = 0.0
        self._anchor_at: float = 0.0
        self._anchor_position = 0.0
        self._paused_at: float | None = None
        self._last_bubble_at: float | None = None
        self._probe_ticks = 0
        self._lyric_wait_ticks = 0
        #: 已经报过"认不出这首歌"的可执行名（同一台播放器只提示一次）。
        self._unknown_reported: str = ""
        #: ``{slot: presentation}``；``None`` 表示"单宠"（端口不支持 for_slot）。
        self._leases: dict[int | None, object] = {}
        self._sing_failures = 0
        self._next_sing_attempt = 0.0
        #: 循环被核心清掉后的重新武装：``_rearm_pending`` 表示"欠一次补播"，
        #: 连续丢失按 2^n 拍放慢（``_next_rearm``），一拍观察活下来就清零。
        self._rearm_pending = False
        self._rearm_attempts = 0
        self._next_rearm = 0.0
        #: 展示会话（歌词占着气泡）：``_scene_wanted`` 是这一拍该不该占，
        #: ``_scene_held`` 是 Core 真的给了我们，``_sing_ok`` 是"这首曲目唱起来过"
        #: （补播的空档里别把气泡会话放掉，否则自言自语会在两拍之间插进来）。
        self._scene_wanted = False
        self._scene_held = False
        self._sing_ok = False
        self._silent_since: float | None = None
        self._offset: float | None = None
        self.last_decision: object = None
        self.last_sample: sampling.Sample | None = None

    # ---------------------------------------------------------------- 配置

    @property
    def instrumental(self) -> bool:
        """当前曲目是否已被判定为纯音乐（纯音乐不唱、不显示歌词）。"""
        return self._instrumental

    @property
    def singing(self) -> bool:
        """唱歌动画当前是否由本运行时持着。

        租约只在 ``_release*`` 里显式放掉；核心把循环清掉（点击/探头/击飞/拖拽）时
        ``_reap_lost_loops`` 会先把这里的租约退掉再补播，因此它表示"我请求过、并且
        没有主动放弃"，动画是否真的在循环以 :meth:`_animation_state` 为准。
        """
        return bool(self._leases)

    def _config_value(self, key: str, default):
        port = getattr(self.context.ports, "config", None)
        if port is None:
            return default
        try:
            value = port.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _audio_device_id(self) -> str:
        """当前要采样的输出端点：空=跟随系统默认，非空=手选 id。

        跟随默认时每 5 秒比较一次默认端点 id；变了就丢掉缓存的 WASAPI
        manager（否则会一直读旧端点）。手选设备每 30 秒确认一次还在不在，
        拔掉时回退默认并只记一条 warning（不写配置、不打扰用户）。
        """
        configured = str(self._config_value("music_audio_device", "") or "").strip()
        now = time.monotonic()
        if not configured:
            if now >= self._next_default_audio_check:
                self._next_default_audio_check = now + 5.0
                current = audio_sessions.default_endpoint_id()
                if (
                    self._last_default_audio
                    and current
                    and current != self._last_default_audio
                ):
                    audio_sessions.reset()
                if current:
                    self._last_default_audio = current
            return ""
        if now >= self._next_manual_audio_check:
            self._next_manual_audio_check = now + 30.0
            if not audio_sessions.endpoint_exists(configured):
                if not self._audio_device_fallback_logged:
                    self._audio_device_fallback_logged = True
                    log.warning(
                        "configured audio output device is unavailable; using system default"
                    )
                self._manual_audio_fallback = True
            else:
                self._audio_device_fallback_logged = False
                self._manual_audio_fallback = False
        # 回退决定要保留到下次存在性检查（30s），不能只在检查那一拍生效；
        # 否则 29/30 拍仍拿着已拔掉的端点，音频会话证据整段消失。
        if self._manual_audio_fallback:
            return ""
        return configured

    def _sample_audio(self):
        return audio_sessions.snapshot(self._audio_device_id())

    def _flag(self, key: str, default: bool) -> bool:
        return bool(self._config_value(key, default))

    def _number(self, key: str, default: float, *, low: float, high: float) -> float:
        try:
            value = float(self._config_value(key, default))
        except (TypeError, ValueError):
            value = float(default)
        return max(low, min(high, value))

    def _grace_seconds(self) -> float:
        return self._number("music_sing_grace_seconds", DEFAULTS["music_sing_grace_seconds"], low=0.0, high=120.0)

    def _silence_limit(self, status: str) -> float:
        """多久的"没声音"才解除唱歌循环。

        ``paused``（播放器明确说了"暂停"）**立刻**停唱：用户按下暂停就是不想听了，
        再演几秒唱歌动画是实机报过的现象（旧实现一律走宽限）。宽限只留给
        ``absent``（什么都看不到：切歌瞬间、SMTC 抖动、播放器被关掉）——那里**至少**
        观察 ``ABSENT_CONFIRM_TICKS`` 拍，否则用户把宽限设成 0 时一次采样抖动就会让
        循环闪断重来。
        """
        if status == recognizer.STATUS_PAUSED:
            return 0.0
        grace = self._grace_seconds()
        if status == recognizer.STATUS_ABSENT:
            return max(grace, ABSENT_CONFIRM_TICKS * POLL_MS / 1000.0)
        return grace

    def _lead_seconds(self) -> float:
        return self._number("music_lyric_lead_seconds", DEFAULTS["music_lyric_lead_seconds"], low=-2.0, high=3.0)

    def _title_format(self) -> str:
        return str(self._config_value("music_title_format", "") or "").strip()

    def lyric_state_text(self) -> str:
        """给诊断用的一句话：歌词现在处在哪一步（命中源 / 分数 / 第几行 / 失败次数）。"""
        if self._instrumental:
            return "纯音乐（不出歌词、不唱）"
        if self._lines:
            where = (
                f"第 {self._lyric_index + 1} 行" if self._lyric_index >= 0 else "还没到第一句"
            )
            score = f" score={self._lyric_score:.2f}" if self._lyric_score else ""
            return f"{self._lyric_source or '未知源'}命中{score} · {where} / 共 {len(self._lines)} 行"
        if self._track is None:
            return "还没有识别到曲目"
        if self._fetch_attempts:
            return (
                f"取词失败 {self._fetch_attempts} 次"
                f"（上限 {LYRIC_MAX_ATTEMPTS} 次，冷却 {LYRIC_RETRY_SECONDS:.0f} 秒）"
            )
        return "还没开始取词"

    def lyric_offset(self) -> float:
        """当前歌词偏移（秒）：本次运行的会话状态，初值来自配置。"""
        if self._offset is None:
            self._offset = self._number(
                "music_lyric_offset_seconds", DEFAULTS["music_lyric_offset_seconds"], low=OFFSET_MIN, high=OFFSET_MAX
            )
        return self._offset

    def _allowed_ids(self) -> frozenset[str]:
        """「只识别已配置播放器」的识别名集合；空集合表示不过滤。"""
        if not self._flag("music_filter_enabled", False):
            return frozenset()
        paths = self._config_value("music_player_paths", {})
        extras = str(self._config_value("music_filter_extra_ids", "") or "")
        return now_playing.allowed_app_ids(paths if isinstance(paths, dict) else {}, extras=extras)

    def _preferred_app_id(self) -> str:
        """配置里的默认播放器：多会话同播时优先跟随它。"""
        key = str(self._config_value("music_player", "") or "").strip()
        return players.player_exe(key) if key else ""

    def _targets(self) -> frozenset[str]:
        """窗口标题源允许观察的可执行名（已知播放器 ∪ 用户配置 ∪ 额外识别名）。"""
        paths = self._config_value("music_player_paths", {})
        extras = str(self._config_value("music_filter_extra_ids", "") or "")
        return players.recognition_targets(paths if isinstance(paths, dict) else {}, extras)

    def _cache(self) -> lyrics_mod.LyricsCache | None:
        """磁盘缓存句柄。

        **只在取词线程上调用**：``StoragePort.data_dir`` 会 ``mkdir``，那是文件系统
        操作，不该挂在每秒一拍的 GUI 线程上。构造本身不做 I/O（``mkdir`` 在
        :meth:`LyricsCache.put` 里，也在取词线程上）。
        """
        storage = getattr(self.context.ports, "storage", None)
        if storage is None:
            return None
        try:
            base = storage.data_dir("media.music") / "lyrics"
        except Exception:
            return None
        limit = int(self._number("music_lyric_cache_limit", DEFAULTS["music_lyric_cache_limit"], low=1, high=2000))
        return lyrics_mod.LyricsCache(base, limit=limit)

    # ---------------------------------------------------------------- 轮询

    def poll(self) -> None:
        """一拍：把三源采样交给后台线程，回来后再动动画/歌词。

        上一拍还没回来就跳过这一拍（不排队、不堆积）。**采样线程不在时不补采**：
        采样线程死了就记一条日志并在下一拍重拉——在 GUI 线程上跑 SMTC 正是主分支
        实测会永久卡死窗口的那件事。``music_enabled`` 关着时连唤醒都不做。

        唯一同步采样的路径是"运行时还没 ``start()``"（测试与诊断直接调 ``poll()``
        的走法）：那时没有常驻线程可等，也无所谓时序。
        """
        if self._stopped:
            return
        if not self._flag("music_enabled", True):
            self._release()
            self._forget_track()
            return
        # 令牌必须在两条路径（后台线程 / 同步）上都有效：先自增再建请求，
        # 否则同步路径的 seq 恒为 0，迟到判断形同虚设。
        self._seq += 1
        request = _Request(
            browser_media_enabled=self._flag("music_browser_media_enabled", False),
            tracked_app_id=self._app_id or self._preferred_app_id(),
            allowed_ids=self._allowed_ids(),
            title_format=self._title_format(),
            targets=self._targets(),
            seq=self._seq,
        )
        if self._started:
            self._start_worker()
            if self._closing.is_set():
                return
        worker = self._worker
        if worker is None or not worker.is_alive():
            if not self._started:
                # 运行时还没 start()（测试/诊断直接调 poll()）：没有常驻线程可等。
                self._consume(self._collect_safely(request), request)
                return
            # 线程意外退出：上面已经尝试重拉；还是不在就记一条日志、**跳过这一拍**。
            # 绝不在 GUI 线程补采——SMTC 实测能把主线程永久钉住。
            if not self._worker_lost:
                self._worker_lost = True
                log.warning("music sampling worker is not running; skipping polls until it restarts")
            return
        if self._inflight:
            started = self._inflight_since
            if started is not None and (self.clock() - started) > INFLIGHT_LIMIT_SECONDS:
                # 上一拍卡在采集里（SMTC 无响应之类）：作废它。否则此后每一拍都在这里
                # 被挡回去，识别永久停摆。真正的卡死读取由 ``SmtcReader`` 的硬超时兜住，
                # 这条是最后一道保险（也覆盖音频/窗口源侧的长时间阻塞）。
                if not self._inflight_warned:
                    self._inflight_warned = True
                    log.warning(
                        "music sampling beat exceeded %.1fs; dropping it and continuing",
                        INFLIGHT_LIMIT_SECONDS,
                    )
                # 光放开标志不够：worker 真卡死时再也没有 sample 落地，状态机会永远
                # 停在最后一拍（继续唱歌、歌词不动）。这里直接把"这一拍读不到"交给
                # 判定层（按退化语义收掉循环）；卡死线程迟到的结果 seq 更旧，会被丢弃。
                self._consume(
                    sampling.Sample(
                        smtc_degraded=True,
                        smtc_reason="采样线程无响应，本次识别已跳过。",
                    ),
                    request,
                )
                if self._stopped:
                    return
                # ``_consume`` 会复位告警标志；这里补回来，让"同一次卡死"只告警一次
                # （真恢复后由正常 sample 的 ``_consume`` 复位，下一次卡死会重新告警）。
                self._inflight_warned = True
            else:
                return
        self._inflight = True
        self._inflight_since = self.clock()
        self._pending = request
        self._wake.set()

    def _collect(self, request: _Request) -> sampling.Sample:
        """三源采样（编排在 ``sampling.collect``，这里只注入运行时自己的探针接缝）。"""
        return sampling.collect(
            browser_media_enabled=request.browser_media_enabled,
            tracked_app_id=request.tracked_app_id,
            allowed_ids=request.allowed_ids,
            targets=request.targets,
            smtc=self._smtc_callable(),
            audio=self.sample_audio,
            windows=self.sample_windows,
            window_cache=self._window_cache,
            clock=self.clock,
        )

    def _smtc_callable(self):
        """SMTC 采样入口：有 reader 走有界读取，否则用同步接缝（测试/诊断）。

        在**采集时**解析而不是在 ``start()`` 时替换 ``sample_smtc``：``stop()`` 期间
        采样线程可能还差最后一拍，替换成裸 ``read_sample`` 会让它重新执行无界 COM
        调用（正是这次要修的那条链）。已收口时一律给"已收口"样例——那一拍的结果
        本来就会被 generation 丢掉。
        """
        if self._stopped:
            return now_playing.closed_sample
        reader = self._smtc_reader
        return reader.read if reader is not None else self.sample_smtc

    def _collect_safely(self, request: _Request) -> sampling.Sample:
        """采集一次，异常折成"这一拍读失败"（不许杀掉采样线程/冒到定时器回调）。"""
        try:
            return self._collect(request)
        except Exception:
            log.warning("music sampling failed; reporting a read failure", exc_info=True)
            return sampling.Sample(
                smtc_error=True,
                smtc_reason="采样失败（音乐来源读取异常）。",
            )

    def _decide(self, sample: sampling.Sample, request: _Request):
        try:
            return self.decide(
                sample,
                allowed_ids=request.allowed_ids,
                title_format=request.title_format,
            )
        except Exception:
            return recognizer.Decision(
                status=recognizer.STATUS_ERROR, reasons=("识别判定失败。",)
            )

    def _worker_loop(self, closing, wake, generation: int) -> None:
        try:
            while not closing.is_set():
                if not wake.wait(1.0):
                    continue
                wake.clear()
                if closing.is_set():
                    break
                request = self._pending
                if request is None:
                    continue
                sample = self._collect_safely(request)
                self._post(
                    lambda sample=sample, request=request: self._consume(sample, request),
                    generation,
                )
        finally:
            # 线程退出时关掉本线程的常驻事件循环，别把未关闭的 loop 留给 GC。
            now_playing.close_thread_loop()
            audio_sessions.close()

    # ---------------------------------------------------------------- 判定落地

    def _consume(self, sample: sampling.Sample, request: _Request) -> None:
        """在 GUI 线程落地一次采样（迟到/过期的包直接丢弃）。"""
        self._inflight = False
        self._inflight_since = None
        self._inflight_warned = False
        if self._stopped or request.seq < self._delivered_seq:
            return
        self._delivered_seq = request.seq
        self.last_sample = sample
        decision = self._decide(sample, request)
        self._on_decision(decision, sample)
        self._sync_scene()

    def _on_decision(self, decision, sample: sampling.Sample) -> None:
        self.last_decision = decision
        degraded = bool(getattr(sample, "smtc_degraded", False))
        if degraded and not self._smtc_degraded_logged:
            # 开始/恢复各记一条（不逐拍刷屏）；理由里没有曲名/URL，可以进日志。
            self._smtc_degraded_logged = True
            log.warning(
                "SMTC read timed out; falling back to window-title recognition: %s",
                str(getattr(sample, "smtc_reason", "") or "").strip(),
            )
        elif not degraded and self._smtc_degraded_logged:
            self._smtc_degraded_logged = False
            log.info("SMTC read is back; window-title fallback is no longer needed")
        now = self.clock()
        status = str(getattr(decision, "status", ""))
        if status == recognizer.STATUS_ERROR:
            # 读失败：不动歌词、不动动画；宽限期照常推进（长时间读不到就自然停下）。
            self._on_silence(now, status)
            return
        was_playing = self._playing_now
        soft_paused = bool(getattr(decision, "soft_paused", False))
        if soft_paused and was_playing:
            self._soft_pause_beats += 1
            if self._soft_pause_beats < SOFT_PAUSE_CONFIRM_TICKS:
                # 峰值门限的单拍抖动（淡出/间奏）：保留上一拍，不释放循环、不藏气泡。
                # 否则「PAUSED 零宽限」会把一次静音采样放大成 1Hz 释放—重播。
                return
        else:
            self._soft_pause_beats = 0
        self._playing_now = bool(getattr(decision, "playing", False))
        app_id = str(getattr(decision, "app_id", "") or "")
        if app_id:
            self._app_id = app_id
        track = getattr(decision, "track", None)
        if not bool(getattr(decision, "playing", False)):
            self._scene_wanted = False
            if status == recognizer.STATUS_PAUSED and was_playing:
                # 「播放 → 暂停」的边沿：气泡里的最后一句不该再挂 2–6 秒（看起来像
                # 还在唱）。端口只在「本 owner 仍持有展示会话」时才收，不会误收别人的。
                self._hide_sung_bubble()
            if not track and status == recognizer.STATUS_ABSENT:
                # 没有曲目也没有在播：保留上一首的歌词，只走宽限。
                self._on_silence(now, status)
                return
            if track is not None:
                self._adopt_track(track, now, playing=False)
            self._on_silence(now, status)
            return

        self._scene_wanted = True
        self._silent_since = None
        resumed = self._paused_at is not None
        if resumed:
            # 本地时钟模式下暂停过：把基准顺延，恢复后歌词不跳句。
            self._anchor_at += max(0.0, now - self._paused_at)
            self._paused_at = None
        if track is None:
            # 只唱不取词：认得出"在放歌"但认不出是哪首（窗口标题解析不了）。
            self._say_unknown_once(str(getattr(decision, "exe", "") or ""))
            self._tick_singing(now, playing=True)
            return
        self._adopt_track(track, now, playing=True)
        if resumed:
            # 不上报进度的播放器：单曲循环只有"暂停→播放"这个边沿可看。
            self._restart_after_pause(track, now)
        self._maybe_fetch_lyrics(now)
        self._tick_singing(now, playing=True)
        self._advance_round(track, now)
        self._sync_lyrics(track, now)

    def _say_unknown_once(self, exe: str) -> None:
        """认不出曲名时说一次（同一台播放器不重复）：用户要能分清"没歌"与"认不出"。"""
        if exe and exe == self._unknown_reported:
            return
        self._unknown_reported = exe
        label = exe or "这台播放器"
        self._say_sung(f"这首歌我认不出来（{label}），先陪你哼着", subtitle="")

    def _adopt_track(self, track, now: float, *, playing: bool) -> None:
        """把判定里的曲目落到运行时状态：换歌才重建，同一首只更新位置。

        同一首曲目的进度**大幅回退**（尾部→头部）是单曲循环/手动重播的确定信号：
        行号要归零、用已缓存的歌词重新逐行显示（不重新联网取词）。

        **同名身份不算换歌，暂停与播放一视同仁**：SMTC 掉线时只剩窗口标题源兜底
        （没有专辑/时长，艺人也可能解析不出来），元数据也可能晚一拍才到——曲目身份
        （``Track.key``）因此变了。旧实现按 key 判"换了首歌"，于是把已取到的歌词与
        取词配额一起清掉、重新联网取词，取不到就冒「歌词获取失败」。现在这种**同名
        降级身份**只记成当前曲目的别名（``_track_aliases``），保留曲目元信息、歌词与
        取词配额；只有歌名真的变了才 ``_begin_track``。
        """
        key = track.key()
        if not playing:
            if self._track is not None and key != self._track_key:
                if self._same_song(track, self._track):
                    self._track_aliases.add(key)
                # 歌名都变了：暂停里同样先不动，等真的恢复播放再接管（`_begin_track`）。
            elif self._track is not None:
                self._title = str(getattr(track, "title", "") or self._title)
            if self._paused_at is None:
                self._paused_at = now
            return
        if key != self._track_key:
            if key in self._track_aliases:
                # 已知的降级身份：同一首歌，保留曲目（含时长）/歌词/取词配额，
                # 只把时间轴交回播放器（它上报的进度更准）。
                position = getattr(track, "position", None)
                if position is not None:
                    self._anchor_at = now
                    self._anchor_position = float(position)
                return
            if self._track is not None and self._same_song(track, self._track):
                # 播放中才出现的降级身份（艺人晚一拍到 / SMTC 掉线换窗口源）：
                # 记成别名就是同一首歌，**不清歌词、不重置取词配额**。
                # 与暂停分支同一条规则，只是这里还要把时间轴交回播放器。
                self._track_aliases.add(key)
                self._title = str(getattr(track, "title", "") or self._title)
                position = getattr(track, "position", None)
                if position is not None:
                    self._anchor_at = now
                    self._anchor_position = float(position)
                return
            self._begin_track(track, now)
            return
        self._title = str(getattr(track, "title", "") or self._title)
        position = getattr(track, "position", None)
        if position is not None:
            if self._is_rewind(self._local_position(now), float(position)):
                self._restart_round()
            self._anchor_at = now
            self._anchor_position = float(position)

    @staticmethod
    def _same_song(candidate, track) -> bool:
        """歌名相同就是同一首歌（降级身份只丢了艺人/专辑/时长）。"""
        left = str(getattr(candidate, "title", "") or "").strip().lower()
        right = str(getattr(track, "title", "") or "").strip().lower()
        return bool(left) and left == right

    def _local_position(self, now: float) -> float:
        """本地推算的播放位置（不上报进度的播放器上它就是唯一的时间轴）。"""
        return self._anchor_position + max(0.0, now - self._anchor_at)

    def _round_length(self) -> float:
        """一轮播放的估算长度（秒）：播放器报告的时长，否则末句 + 尾奏估算。"""
        duration = float(getattr(self._track, "duration", 0.0) or 0.0)
        if duration > 0.0:
            return duration
        if not self._lines:
            return 0.0
        last = len(self._lines) - 1
        tail = max(
            lyrics_mod.line_duration_ms(self._lines, last) / 1000.0, ROUND_TAIL_SECONDS
        )
        return float(self._lines[last].at) + tail

    def _is_rewind(self, previous: float, position: float) -> bool:
        """同一首曲目「从尾部回到头部」= 新一轮播放（单曲循环 / 手动重播）。

        三个条件一起看，避免把"用户往前拖几秒"当成新一圈：回退量够大、新位置回到
        开头附近、且上一拍确实已经播过曲长的 ``ROUND_END_FRACTION``。
        """
        if previous - position < ROUND_REWIND_SECONDS:
            return False
        if position > ROUND_HEAD_SECONDS:
            return False
        length = self._round_length()
        if length <= 0.0:
            return False
        return previous >= max(ROUND_REWIND_SECONDS, ROUND_END_FRACTION * length)

    def _restart_round(self) -> None:
        """同一首曲目又从头播了：行号归零 + 重新武装唱歌（歌词不重新取）。"""
        self._lyric_index = -1
        self._lyric_wait_ticks = 0
        self._last_bubble_at = None
        self._sing_failures = 0
        self._next_sing_attempt = 0.0
        self._rearm_attempts = 0
        self._next_rearm = 0.0

    def _advance_round(self, track, now: float) -> None:
        """不上报进度的播放器：本地时钟越过估算曲长就整轮回绕（保留相位）。"""
        if getattr(track, "position", None) is not None:
            return
        length = self._round_length()
        if length <= 0.0:
            return
        elapsed = self._local_position(now)
        if elapsed < length:
            return
        rounds = int(elapsed // length)
        self._anchor_at += rounds * length
        self._restart_round()

    def _restart_after_pause(self, track, now: float) -> None:
        """不上报进度的播放器：把「尾部暂停 → 又播放」当作新一轮。

        网易云/酷狗的时间轴恒为 0，看不到进度回退，只能看状态边沿：已经播过估算
        曲长（``_round_length``）之后暂停再播放，就是单曲循环的接缝。
        **局限**：用户自己在尾奏里暂停再继续，也会被当成新一轮（那一句会提前）。
        """
        if getattr(track, "position", None) is not None:
            return
        length = self._round_length()
        if length <= 0.0:
            return
        if self._local_position(now) < length - ROUND_PAUSE_SLACK:
            return
        self._anchor_at = now
        self._anchor_position = 0.0
        self._restart_round()

    def _begin_track(self, track, now: float) -> None:
        self._track = track
        self._track_key = track.key()
        self._track_aliases.clear()
        self._title = str(track.title or "")
        self._lines = ()
        self._instrumental = False
        self._lyric_index = -1
        self._fetch_key = None
        self._fetch_attempts = 0
        self._lyric_retry_at = None
        self._lyric_reported = False
        self._anchor_at = now
        self._anchor_position = float(getattr(track, "position", None) or 0.0)
        self._paused_at = None
        self._last_bubble_at = None
        self._sing_ok = False
        self._sing_failures = 0
        self._next_sing_attempt = 0.0
        self._rearm_pending = False
        self._rearm_attempts = 0
        self._next_rearm = 0.0
        # 纯音乐先别唱：歌词（占位文案 / instrumental 标记）通常第一拍就到。
        self._probe_ticks = (
            INSTRUMENTAL_PROBE_TICKS if self._flag("music_lyric_enabled", True) else 0
        )
        self._lyric_wait_ticks = 0

    def _on_silence(self, now: float, status: str) -> None:
        if self._silent_since is None:
            self._silent_since = now
        if self._leases and (now - self._silent_since) >= self._silence_limit(status):
            self._release()

    def _forget_track(self) -> None:
        self._track = None
        self._track_key = None
        self._track_aliases.clear()
        self._lines = ()
        self._instrumental = False
        self._lyric_index = -1
        self._fetch_key = None
        self._fetch_attempts = 0
        self._lyric_retry_at = None
        self._lyric_source = ""
        self._lyric_score = 0.0
        self._anchor_at = 0.0
        self._anchor_position = 0.0
        self._paused_at = None
        self._probe_ticks = 0
        self._scene_wanted = False
        self._sync_scene()

    # ---------------------------------------------------------------- 唱歌

    def _presentation(self):
        return getattr(self.context.ports, "presentation", None)

    def _targets_for_pets(self) -> list[tuple[int | None, object]]:
        """每只宠一个呈现端口；端口不支持按 slot 路由时退回「当前活动宠」。"""
        base = self._presentation()
        if base is None:
            return []
        for_slot = getattr(base, "for_slot", None)
        if not callable(for_slot):
            return [(None, base)]
        registry = getattr(self.context.ports, "pets", None)
        handles = ()
        if registry is not None:
            try:
                handles = tuple(registry.pets() or ())
            except Exception:
                handles = ()
        slots = [int(getattr(handle, "slot", 0)) for handle in handles]
        if not slots:
            return [(None, base)]
        targets: list[tuple[int | None, object]] = []
        for slot in slots:
            try:
                targets.append((slot, for_slot(slot)))
            except Exception:
                continue
        return targets or [(None, base)]

    def _awaiting_lyrics(self) -> bool:
        """当前曲目是否有一次取词真的在飞（决定要不要为"别对纯音乐唱"多等几拍）。"""
        if not self._flag("music_lyric_enabled", True):
            return False
        if self._lines or self._instrumental or self._track_key is None:
            return False
        return self._fetch_key == self._track_key and self._fetch_attempts > 0

    def _tick_singing(self, now: float, *, playing: bool) -> None:
        if not playing:
            self._on_silence(now, recognizer.STATUS_PAUSED)
            return
        self._silent_since = None
        if not self._flag("music_sing_enabled", True) or self._instrumental:
            self._release()
            return
        targets = self._targets_for_pets()
        current = {slot for slot, _port in targets}
        # 宠关掉了/切走了：按 slot 释放，别让 A 的循环留在 A 身上。
        for slot in [slot for slot in self._leases if slot not in current]:
            self._release_slot(slot)
        if self._probe_ticks > 0:
            self._probe_ticks -= 1
            return
        if self._awaiting_lyrics() and self._lyric_wait_ticks < LYRIC_PROBE_MAX_TICKS:
            # 取词还没回来：等一下再看是不是纯音乐（纯音乐不该唱）。有上限，
            # 而且只在真的有取词在飞时生效，不会让普通曲目永远等不到。
            self._lyric_wait_ticks += 1
            return
        # 核心的 _play_spec() 是循环的唯一清除点（点击/探头/击飞/拖拽/改播别的素材），
        # 所以"我以为在唱"必须每拍向核心复核一次，否则循环没了就永远补不回来。
        if self._reap_lost_loops(targets):
            self._rearm_pending = True
        if targets and current and current <= set(self._leases):
            self._rearm_pending = False
            self._rearm_attempts = 0
            self._next_rearm = 0.0
            return
        if now < self._next_sing_attempt or now < self._next_rearm:
            return
        if self._rearm_pending:
            # 补播也按 2^n 拍退避：别人一直抢占时不许每拍都发一次请求。
            self._rearm_attempts += 1
            delay = (
                0.0
                if self._rearm_attempts <= 1
                else min(
                    SING_REARM_MAX_SECONDS,
                    float(2 ** min(self._rearm_attempts - 1, 5)),
                )
            )
            self._next_rearm = now + delay
        self._lease(targets, now)

    @staticmethod
    def _animation_state(port):
        """向 Core 问当前动画状态；端口不支持就返回 ``None``（= 未知，别据此重试）。"""
        query = getattr(port, "animation_state", None)
        if not callable(query):
            return None
        try:
            return query()
        except Exception:
            return None

    def _reap_lost_loops(self, targets) -> bool:
        """核心把我们的循环清掉了吗？是就退掉本地租约（返回 True）。

        只退租**不**补发 ``release_loop()``：那个循环标志已经不在我们手里，此时再释放
        会误伤别人（报时之类）正持着的循环。探头/击飞/拖拽接管期间什么都不做——此刻
        ``play_animation`` 会被核心降级成待机，抢了反而把待机锁成死循环。
        """
        lost = False
        for slot, port in targets:
            if slot not in self._leases:
                continue
            state = self._animation_state(port)
            if state is None:
                continue  # 端口查不到状态：按"还在唱"处理，绝不因此每拍重发
            if bool(getattr(state, "locked", False)):
                continue
            if bool(getattr(state, "loop", False)):
                continue  # 循环还在（我们的，或别人的）：不抢
            self._leases.pop(slot, None)
            lost = True
        return lost

    def _lease(self, targets, now: float) -> None:
        started: dict[int | None, object] = {}
        failed = 0
        for slot, port in targets:
            if slot in self._leases:
                continue  # 已经在唱的宠别重启动画（多宠时新开一只会把别人的循环打断重来）
            try:
                ok = bool(port.play_animation(SING_ANIM, loop=True))
            except Exception:
                ok = False
            if ok:
                started[slot] = port
            else:
                failed += 1
        if started:
            self._leases.update(started)
            self._sing_ok = True
            self._sing_failures = 0
            self._next_sing_attempt = 0.0
        if not failed:
            return
        # 2^n 拍退避，封顶 SING_BACKOFF_MAX_SECONDS。**永不永久放弃**：角色包补上
        # 「悠闲哼歌」后，最迟半分钟内就能唱起来（旧实现是 4 拍后彻底静默）。
        # 多宠里只有某一只失败时也走这条：别让那一只每拍都被重试一次。
        self._sing_failures += 1
        delay = min(SING_BACKOFF_MAX_SECONDS, float(2 ** min(self._sing_failures, 5)))
        self._next_sing_attempt = now + delay

    def _release_slot(self, slot) -> None:
        port = self._leases.pop(slot, None)
        if port is None:
            return
        try:
            port.release_loop()
        except Exception:
            return

    def _release(self) -> None:
        self._silent_since = None
        self._sing_ok = False
        self._sing_failures = 0
        self._next_sing_attempt = 0.0
        self._rearm_pending = False
        self._rearm_attempts = 0
        self._next_rearm = 0.0
        for slot in list(self._leases):
            self._release_slot(slot)
        self._leases.clear()

    # ---------------------------------------------------------------- 展示会话

    def _hide_sung_bubble(self) -> None:
        """收起歌词气泡（只在 ``media.music`` 仍持有展示会话时生效）。"""
        port = self._presentation()
        hider = getattr(port, "hide", None) if port is not None else None
        if not callable(hider):
            return
        try:
            hider(SCENE_OWNER)
        except Exception:  # noqa: BLE001 - 收气泡失败不影响暂停状态机
            pass

    def _sync_scene(self) -> None:
        """歌词/唱歌期间占住气泡：别人的自动气泡被 Core 拒绝（闸门只认 owner）。

        持有期间每拍 ``refresh_scene`` 续期（Core 侧会因此知道我们还活着）；不想要
        的时候一定 ``end_scene``，别把自言自语永久锁死。异常/退出路径由 ``stop()``
        与 ``_forget_track()`` 兜底，进程结束时状态随进程消失。
        """
        want = bool(self._scene_wanted) and not self._instrumental and (
            bool(self._leases)
            or (self._flag("music_lyric_enabled", True) and bool(self._lines))
            or (self._flag("music_sing_enabled", True) and self._sing_ok)
        )
        port = self._presentation()
        if port is None:
            return
        if not want:
            if not self._scene_held:
                return
            self._scene_held = False
            end = getattr(port, "end_scene", None)
            if callable(end):
                try:
                    end(SCENE_OWNER)
                except Exception:
                    pass
            return
        if self._scene_held:
            refresh = getattr(port, "refresh_scene", None)
            if callable(refresh):
                try:
                    self._scene_held = bool(refresh(SCENE_OWNER))
                except Exception:
                    self._scene_held = False
            return
        begin = getattr(port, "begin_scene", None)
        if not callable(begin):
            return
        try:
            self._scene_held = bool(begin(SCENE_OWNER))
        except Exception:
            self._scene_held = False

    # ---------------------------------------------------------------- 歌词

    def _maybe_fetch_lyrics(self, now: float) -> None:
        if not self._flag("music_lyric_enabled", True):
            return
        if self._lines or self._instrumental or self._track_key is None:
            return
        if self._fetch_key == self._track_key:
            return  # 已有一轮在飞/已完成
        if self._fetch_attempts >= LYRIC_MAX_ATTEMPTS:
            return
        if self._lyric_retry_at is not None and now < self._lyric_retry_at:
            return
        if self._lyric_blocked(self._title, now):
            # 这首（同标题）刚失败过：曲目身份在抖（艺人晚一拍到 → 新 key → 新曲目）
            # 也不许在负缓存到期前重新联网。
            return
        self._start_fetch(self._track)

    # ---------------------------------------------------------------- 取词负缓存

    @staticmethod
    def _title_key(title: str) -> str:
        """负缓存键：**只按标题**（大小写/空白归一）。

        按标题而不是 ``Track.key()`` 是刻意的：身份抖动的两种形态都是"标题不变、
        艺人变了"（SMTC 掉线 → 窗口标题源兜底；元数据晚一拍到）。带上艺人就等于
        两边都漏。
        """
        return str(title or "").strip().casefold()

    def _lyric_blocked(self, title: str, now: float) -> bool:
        key = self._title_key(title)
        if not key:
            return False
        expires = self._lyric_negative.get(key)
        if expires is None:
            return False
        if now >= expires:
            self._lyric_negative.pop(key, None)
            return False
        return True

    def _remember_lyric_failure(self, title: str, now: float) -> None:
        key = self._title_key(title)
        if not key:
            return
        # 顺手清过期的，再按上限淘汰最早到期的（一次运行最多记 64 首）。
        for stale in [name for name, at in self._lyric_negative.items() if now >= at]:
            self._lyric_negative.pop(stale, None)
        if len(self._lyric_negative) >= LYRIC_NEGATIVE_LIMIT:
            oldest = min(self._lyric_negative, key=self._lyric_negative.__getitem__)
            self._lyric_negative.pop(oldest, None)
        self._lyric_negative[key] = now + LYRIC_NEGATIVE_TTL

    def _start_fetch(self, track) -> None:
        if track is None:
            return
        key = track.key()
        if key is None or self._fetch_key == key:
            return
        http = getattr(self.context.ports, "http", None)
        if http is None:
            return
        title, artist = track.title, track.artist
        album, duration = track.album, track.duration
        generation = self._generation
        self._fetch_key = key
        self._fetch_attempts += 1

        def work(cancel: threading.Event | None = None) -> None:
            is_cancelled = cancel.is_set if cancel is not None else _never
            try:
                # 专辑与时长只用于候选打分（区分原唱/翻唱/现场版），不参与请求。
                # 磁盘缓存句柄也在**取词线程**上构造（`data_dir` 会建目录）。
                result = self.fetch_lyrics(
                    http,
                    title,
                    artist,
                    album=album,
                    duration=duration,
                    timeout=lyrics_mod.DEFAULT_TIMEOUT,
                    cache=self._cache(),
                    cancelled=is_cancelled,
                )
            except lyrics_mod.LyricsCancelled:
                return  # 被新一轮顶掉/已停止：不是失败，不冒泡、不记负缓存
            except lyrics_mod.LyricsError as exc:
                result = exc
            except Exception:
                result = lyrics_mod.LyricsError("歌词获取失败：未知原因")
            if is_cancelled():
                return
            self._post_lyrics(lambda: self._apply_lyrics(key, result), generation)

        try:
            accepted = self.background(work)
        except Exception:
            self._fetch_key = None
            return
        if accepted is False:
            # 取词线程已停止（运行时正在退出）：别把 _fetch_key 卡在这一轮上。
            self._fetch_key = None

    def _post(self, callback, generation: int) -> None:
        def deliver():
            if self._stopped or generation != self._generation:
                self._inflight = False
                return
            if self._flag("music_enabled", True):
                callback()
            else:
                self._inflight = False

        scheduler = getattr(self.context.ports, "scheduler", None)
        if scheduler is None:
            deliver()
            return
        try:
            scheduler.call_later(0, deliver)
        except Exception:
            # 调度器已关闭时不在 worker 上触碰 GUI，但必须放开在飞标志，
            # 否则音乐链路会永久停摆（旧实现只在成功路径复位）。
            self._inflight = False
            return

    def _post_lyrics(self, callback, generation: int) -> None:
        """把取词结果交回 GUI 线程（**不动采样的在飞标志**）。

        取词结果与采样结果走两套线程：旧实现共用一个 ``_post``，一次取词回来会把
        ``_inflight``（采样在飞）清掉，下一拍于是又发一次采样。
        """
        def deliver():
            if self._stopped or generation != self._generation:
                return
            if self._flag("music_enabled", True):
                callback()

        scheduler = getattr(self.context.ports, "scheduler", None)
        if scheduler is None:
            deliver()
            return
        try:
            scheduler.call_later(0, deliver)
        except Exception:
            return

    def _apply_lyrics(self, key, result) -> None:
        if key != self._track_key:
            return
        if isinstance(result, lyrics_mod.LyricsError):
            self._fetch_key = None
            if not self._playing_now:
                # 暂停（或"暂停边沿"：失败结果在暂停之后才回来）静默，并把这一拍的
                # 配额退回去——用户只是按了暂停，不是这首曲子取不到词；恢复播放后
                # 照常重试，失败仍按"冷却 + 每曲 3 次 + 首次说清原因"处理。
                self._fetch_attempts = max(0, self._fetch_attempts - 1)
                return
            now = self.clock()
            # 失败不拉黑整首：冷却后重试，第一次失败说清原因（不再静默）。
            # 同时记进内存负缓存：同一标题在 60 秒内不再重取，哪怕身份抖动把它
            # 变成"新曲目"（新 key / 新配额）。
            self._lyric_retry_at = now + LYRIC_RETRY_SECONDS
            self._remember_lyric_failure(self._title, now)
            if not self._lyric_reported:
                self._lyric_reported = True
                self._say_sung(str(result), subtitle=self._subtitle(), duration_ms=5000, force=False)
            return
        self._lines = tuple(result.lines)
        self._instrumental = bool(result.instrumental)
        self._lyric_source = str(getattr(result, "source", "") or "")
        self._lyric_score = float(getattr(result, "score", 0.0) or 0.0)
        self._lyric_index = -1
        self._lyric_wait_ticks = 0
        self._fetch_attempts = 0
        if self._instrumental:
            self._release()
            if self._title:
                self._say_sung(f"正在听《{self._title}》", subtitle="")

    def _subtitle(self) -> str:
        """气泡副标题（主分支口径）：有曲名就常驻「我在唱《…》」。"""
        return f"我在唱《{self._title}》" if self._title else ""

    def _say_sung(self, text: str, *, subtitle: str, duration_ms: int = 4000, force: bool = False) -> bool:
        """带副标题的气泡；旧版 presentation 端口不认 ``subtitle``/``owner`` 时逐级退回。

        ``owner`` 是展示会话的身份：歌词/唱歌期间我们占着气泡，只有带自己 owner 的
        气泡才不会被 Core 的闸门当成"别人的自动气泡"挡掉。
        """
        presentation = self._presentation()
        if presentation is None:
            return False
        for kwargs in (
            {"subtitle": subtitle, "owner": SCENE_OWNER},
            {"subtitle": subtitle},
            {},
        ):
            try:
                shown = presentation.say(
                    text, duration_ms=duration_ms, force=force, **kwargs
                )
            except TypeError:
                continue
            except Exception:
                return False
            return shown is not False
        return False

    def _position(self, track, now: float) -> float:
        """播放位置：播放器上报就用真值，否则从观察到切曲的时刻本地累加。"""
        position = getattr(track, "position", None)
        if position is not None:
            self._anchor_at = now
            self._anchor_position = float(position)
            return self._anchor_position
        return self._anchor_position + max(0.0, now - self._anchor_at)

    def _sync_lyrics(self, track, now: float) -> None:
        if not self._flag("music_lyric_enabled", True) or not self._lines:
            return
        presentation = self._presentation()
        if presentation is None:
            return
        index = lyrics_mod.line_at(
            self._lines,
            self._position(track, now),
            lead=self._lead_seconds(),
            offset=self.lyric_offset(),
        )
        if index < 0 or index == self._lyric_index:
            return
        text = self._lines[index].text.strip()
        if not text:
            return
        if self._last_bubble_at is not None and (now - self._last_bubble_at) < MIN_BUBBLE_SECONDS:
            return
        if not self._say_sung(
            text,
            subtitle=self._subtitle(),
            duration_ms=lyrics_mod.line_duration_ms(self._lines, index),
            force=False,
        ):
            # 气泡没显示出去（隐藏/设置窗打开/让路）：不推进行号，下一拍再试。
            return
        self._lyric_index = index
        self._last_bubble_at = now

    # ---------------------------------------------------------------- 命令

    def lyric_align(self) -> None:
        """把歌词偏移 +0.5 秒并在气泡里报出来（循环于 -3.0 ~ +3.0）。"""
        self._offset = next_offset(self.lyric_offset())
        self._lyric_index = -1
        self._say_forced(f"歌词偏移 {self._offset:+.1f} 秒（正数=歌词提前；本次运行有效）")

    def transport(self, action: str) -> None:
        """播放控制命令：**后台线程**调用，结果回 GUI 线程冒泡。

        菜单命令是在 GUI 线程被点的，而 SMTC 调用实测可能把主线程永久阻塞（见模块
        头）。旧实现直接在这里同步调，等于把一次点击押在 WinRT 上。
        """
        if not self._flag("music_enabled", True):
            self._say_forced("音乐控制已关闭：设置 → 扩展管理 → 音乐")
            return
        allowed = self._allowed_ids()
        kwargs = {"tracked_app_id": self._app_id}
        if allowed:
            kwargs["allowed_ids"] = allowed
        generation = self._generation

        def work() -> None:
            try:
                ok, reason = self.control_playback(action, **kwargs)
            except Exception:
                ok, reason = False, "系统媒体控制调用失败，请稍后重试。"
            finally:
                # 一次性线程：SMTC 调用会在本线程建一个常驻事件循环，退出前收掉。
                now_playing.close_thread_loop()
            self._post(
                lambda: self._report_transport(action, bool(ok), str(reason or "")),
                generation,
            )

        try:
            self.spawn_task(work)
        except Exception:
            self._report_transport(action, False, "系统媒体控制不可用")

    def _report_transport(self, action: str, ok: bool, reason: str) -> None:
        if ok:
            self._say_forced(f"已{now_playing.ACTION_LABELS.get(action, action)}")
        else:
            self._say_forced(reason or "系统媒体控制不可用")

    def open_player(self, player_key: str) -> None:
        if not self._flag("music_enabled", True):
            self._say_forced("音乐控制已关闭：设置 → 扩展管理 → 音乐")
            return
        try:
            _ok, message = self.launch_player(self._config_value("music_player_paths", {}), player_key)
        except Exception:
            message = f"启动{players.player_label(player_key)}失败"
        self._say_forced(message)

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        self._started = True
        self._stopped = False
        self._smtc_degraded_logged = False
        self._inflight_warned = False
        self._inflight_since = None
        self._soft_pause_beats = 0
        self._start_worker()
        # 取词线程按需建（第一次取词时），这里只允许它再次被派发：一首歌最多跑三轮
        # 取词，每轮都现场开线程既没法取消也没法在 stop() 里收尾（旧实现）。
        self._fetch_worker.arm()
        # SMTC 读取挪到自己的线程：Windows 媒体服务卡死时（RequestAsync 永不返回）
        # 采样线程不许被一条同步 COM 调用占住。测试把 ``sample_smtc`` 换成替身时
        # 保持替身（它本来就是同步的，卡死风险由替身自己决定）。
        if self._smtc_reader is None and self.sample_smtc is now_playing.read_sample:
            self._smtc_reader = now_playing.SmtcReader()
        scheduler = getattr(self.context.ports, "scheduler", None)
        if scheduler is None or self._timer is not None:
            return
        try:
            self._timer = scheduler.call_every(POLL_MS, self.poll)
        except Exception:
            self._timer = None

    def _start_worker(self) -> None:
        """常驻采样线程：所有系统调用只在这条线程上跑（GUI 线程一次都不碰）。"""
        worker = self._worker
        if worker is not None and worker.is_alive():
            return
        self._closing = threading.Event()
        self._wake = threading.Event()
        self._inflight = False
        self._worker_lost = False
        worker = threading.Thread(
            target=self._worker_loop,
            args=(self._closing, self._wake, self._generation),
            name="music-smtc", daemon=True,
        )
        self._worker = worker
        try:
            worker.start()
        except Exception:
            # 线程起不来（资源耗尽之类）：别让异常冒到 GUI 的定时器回调里；
            # 下一拍会再试一次，``poll()`` 也会记一条"采样线程不在"的日志。
            self._worker = None
            log.warning("could not start the music sampling worker", exc_info=True)

    def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        timer, self._timer = self._timer, None
        self._release()
        self._forget_track()
        scheduler = getattr(self.context.ports, "scheduler", None)
        if scheduler is not None and timer:
            try:
                scheduler.cancel(timer)
            except Exception:
                pass
        # 先让采样线程收工，再收 reader：``_collect()`` 在采集时解析接缝
        # （``_smtc_callable``），线程若在我们收掉 reader 之后还跑最后一拍，会退到
        # 同步接缝 ``sample_smtc``——生产上就是无界 COM 调用（正是这次要修的那条链）。
        self._closing.set()
        self._wake.set()
        self._inflight = False
        self._inflight_since = None
        self._inflight_warned = False
        self._soft_pause_beats = 0
        self._smtc_degraded_logged = False
        reader, self._smtc_reader = self._smtc_reader, None
        if reader is not None and not reader.stop(0.2):
            # 有界等待（进程退出/卸载扩展都在 GUI 线程上）：卡死的读取线程只能留给
            # 进程退出；正常线程在 25ms 内就退出了。
            log.warning("SMTC reader thread is still stuck after stop()")
        if not self._fetch_worker.stop(FETCH_JOIN_SECONDS):
            # 有界等待：一轮取词最多 8 秒，绝不能让 GUI 线程陪着等。取消位已置，
            # 线程会在下一跳之前收手（结果也会被 generation 丢掉）。
            log.warning("lyric fetch worker still running after %.1fs", FETCH_JOIN_SECONDS)

    def close(self) -> None:
        self.stop()

    # ---------------------------------------------------------------- 输出

    def _say_forced(self, text: str) -> None:
        presentation = self._presentation()
        if presentation is None:
            return
        try:
            presentation.say(str(text), duration_ms=4000, force=True)
        except Exception:
            return
