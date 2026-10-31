"""一次识别的**采样编排**：三源按需采集，产出 ``Sample``（零 Qt，端口/探针可注入）。

为什么单独一个模块：运行时的每秒一拍与设置页的「识别诊断」按钮要采**同一份**
证据，否则诊断说的和实际判定的不是一回事。把"什么时候问哪个源"收在一处：

- **SMTC 恒定**：它是主源，元数据、时间轴、传输控制都来自它；
- **音频会话恒定**：它便宜（实测 2ms 级），而且是"在不在出声"的裁判；
- **窗口标题按需**：只在 SMTC 没有"在播"会话时才枚举——这一条把空闲时的
  系统调用省掉（窗口枚举实测 0.5ms，但它会碰到别的进程的标题，能不问就不问）。
  **暂停时再降到 2 秒一次**（:data:`WINDOW_PAUSED_INTERVAL`）：暂停期间没有"在播
  会话"要发现，而整桌枚举 + 逐 pid 查进程名是这条链路里最贵的一次系统调用，
  每秒重扫一遍纯属白烧（SMTC / WASAPI 仍保持每秒，暂停边沿漏不了）。

任何一源抛异常都折成"该源没有数据"，绝不冒泡到调用方：音乐是锦上添花。
audio / windows 两源另外套一层**有界读取**（:func:`_bounded`，每源一条常驻线程）：
同步 COM / Win32 调用卡住时按 ``source_timeout`` 返回，并把"哪个源超时了几秒"
写进样例——诊断要的正是"哪一层没读到"，而不是一句笼统的采样失败。固定线程而不是
每拍新建：``audio_sessions`` 的 COM 管理器按线程缓存，换线程就要重建端点。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from . import audio_sessions, now_playing, window_source

#: 暂停期间窗口源的刷新间隔（秒）。``absent``（什么都看不到）仍按每拍问：
#: 那时窗口源是唯一能发现"新开了播放器"的来源。
WINDOW_PAUSED_INTERVAL = 2.0

#: 音频/窗口两源的默认读取预算（秒）。SMTC 由 ``SmtcReader`` 自己收口，这两个源
#: 之前**没有任何超时**：音频会话里有 5 处同步 COM 调用，一旦卡住整次采样就再也不
#: 返回（现场：设置页「正在采样…」永久转圈、按钮永久 disabled）。
DEFAULT_SOURCE_TIMEOUT = 8.0

#: 一个源超时后先跳过多久（秒）。卡在同步 COM 调用里的读取没法打断，但也不该让
#: 之后每一拍都白等满预算（否则运行时每秒都在等 8 秒 = 识别实际停摆）；卡住的那
#: 次一返回就立刻解除降级。
SOURCE_DEGRADED_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class Sample:
    """三源的一次采样（``recognizer.decide`` 的输入，也是诊断文本的数据源）。"""

    playback: object = None
    sessions: tuple = ()
    smtc_reason: str = ""
    smtc_error: bool = False
    #: SMTC **持续**读不到（读取超时/媒体服务无响应）：判定层要退到窗口标题源，
    #: 不许整拍作废。与 ``smtc_error``（这一拍抖动、保持上一拍）语义不同。
    smtc_degraded: bool = False
    audio: tuple = ()
    audio_available: bool = True
    windows: tuple = ()
    window_available: bool = True
    #: 该源**读取超时**（不是"没有数据"，也不是异常）：诊断文本据此单独写一行。
    #: 字段加在末尾并带默认值，既有构造（含测试里的 ``SimpleNamespace``）不受影响。
    audio_error: str = ""
    window_error: str = ""


class WindowCache:
    """暂停期间的窗口源结果缓存（调用方持有；只在采样线程上读写）。

    键是识别目标集合（``targets``）：设置里换了播放器就立刻作废，不拿旧目标的结果
    冒充新目标的证据。``None`` 表示"这次没枚举，用缓存"。
    """

    __slots__ = ("rows", "targets", "at")

    def __init__(self) -> None:
        self.rows: tuple = ()
        self.targets: frozenset[str] = frozenset()
        self.at: float | None = None

    def get(
        self, targets: frozenset[str], now: float, interval: float = WINDOW_PAUSED_INTERVAL
    ) -> tuple | None:
        if self.at is None or self.targets != frozenset(targets):
            return None
        if (now - self.at) >= interval:
            return None
        return self.rows

    def put(self, targets: frozenset[str], rows, now: float) -> None:
        self.targets = frozenset(targets)
        self.rows = tuple(rows or ())
        self.at = float(now)

    def clear(self) -> None:
        self.rows = ()
        self.targets = frozenset()
        self.at = None


def collect(
    *,
    browser_media_enabled: bool = False,
    tracked_app_id: str = "",
    allowed_ids: frozenset[str] | None = None,
    targets: frozenset[str] | None = None,
    smtc=None,
    audio=None,
    windows=None,
    window_cache: WindowCache | None = None,
    clock=None,
    source_timeout: float = DEFAULT_SOURCE_TIMEOUT,
) -> Sample:
    """采一次三源。``smtc`` / ``audio`` / ``windows`` 是测试注入缝（默认模块函数）。

    ``window_cache`` 非空时，**暂停期间**的窗口枚举按 :data:`WINDOW_PAUSED_INTERVAL`
    节流（其余情况照旧每拍问一次）；``clock`` 是节流用的时钟（默认 ``monotonic``）。

    ``source_timeout`` 是 audio / windows 两源的读取预算（秒）：两者都可能卡在
    同步 COM / Win32 调用里，超时只让**该源**为空并把原因写进 ``Sample`` 的
    ``*_error``，其余源照常返回（SMTC 已由 ``SmtcReader`` 自己收口）。
    """
    read_smtc = smtc or now_playing.read_sample
    read_audio = audio or audio_sessions.snapshot
    read_windows = windows or window_source.snapshot
    now = float(clock()) if clock is not None else time.monotonic()

    kwargs: dict = {
        "browser_media_enabled": bool(browser_media_enabled),
        "tracked_app_id": str(tracked_app_id or ""),
    }
    if allowed_ids:
        kwargs["allowed_ids"] = frozenset(allowed_ids)
    try:
        smtc_sample = read_smtc(**kwargs)
    except Exception:
        smtc_sample = now_playing.SmtcSample(reason="读取系统媒体信息失败。", failed=True)

    audio_available = _available(audio_sessions.available)
    audio_rows: tuple = ()
    audio_error = ""
    if audio_available:
        try:
            rows = _bounded(read_audio, source_timeout, worker=_AUDIO_WORKER)
        except Exception:
            rows = ()
        else:
            if rows is None:
                audio_error = _timeout_reason("音频会话", source_timeout)
        audio_rows = tuple(rows or ())

    window_available = _available(window_source.available)
    playback = getattr(smtc_sample, "playback", None)
    playing = bool(getattr(playback, "playing", False))
    need_window = playback is None or not playing
    # 暂停 = 播放器明确报了曲目但说"没在放"；absent 没有 playback 对象，逐拍问。
    paused = playback is not None and not playing
    wanted_targets = frozenset(targets or ())
    window_rows: tuple = ()
    window_error = ""
    if window_available and need_window:
        cached = None
        if paused and window_cache is not None:
            cached = window_cache.get(wanted_targets, now)
        if cached is not None:
            window_rows = cached
        else:
            try:
                rows = _bounded(
                    lambda: read_windows(wanted_targets),
                    source_timeout,
                    worker=_WINDOW_WORKER,
                )
            except Exception:
                rows = ()
            else:
                if rows is None:
                    window_error = _timeout_reason("播放器窗口标题", source_timeout)
            window_rows = tuple(rows or ())
            # 超时的空结果不进缓存：那是"没读到"，不是"暂停期间没有窗口"。
            if paused and window_cache is not None and not window_error:
                window_cache.put(wanted_targets, window_rows, now)

    return Sample(
        playback=playback,
        sessions=tuple(getattr(smtc_sample, "sessions", ()) or ()),
        smtc_reason=str(getattr(smtc_sample, "reason", "") or ""),
        smtc_error=bool(getattr(smtc_sample, "failed", False)),
        smtc_degraded=bool(getattr(smtc_sample, "degraded", False)),
        audio=audio_rows,
        audio_available=audio_available,
        windows=window_rows,
        window_available=window_available,
        audio_error=audio_error,
        window_error=window_error,
    )


class _SourceWorker:
    """一条常驻 daemon 线程 + 单槽请求：把可能卡死的源读取移出采样线程。

    为什么不是"每次调用起一条线程"：``audio_sessions`` 的 COM 管理器按**线程**
    缓存（COM 接口线程亲和），每次换线程就要重建端点——本机实测同线程稳态 1.2ms、
    每拍换线程 20ms；接口指针还会留在已退出的线程上（释放跨 apartment）。
    所以每个源固定由一条线程读，:data:`_AUDIO_WORKER` / :data:`_WINDOW_WORKER`
    是 ``collect`` 用的那两条。

    - 调用方最多等 ``timeout``；超时返回 ``None``，**不 join**（卡住的那次调用
      留在常驻线程里，Python 没有安全的办法打断它）；
    - 卡住期间提交的请求只占单槽、不排队：不会因为积压让之后每一拍都等满预算。
    """

    __slots__ = ("_name", "_lock", "_wake", "_request", "_thread", "_degraded_until")

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._request = None
        self._thread: threading.Thread | None = None
        #: 超时后的降级截止时刻：卡住的那次调用没返回前不再排队——否则之后每一拍
        #: 都要白等满预算（运行时看门狗只是丢拍，线程照样被钉住）。
        self._degraded_until = 0.0

    def run(self, fn, timeout: float):
        now = time.monotonic()
        with self._lock:
            if now < self._degraded_until:
                return None
            box: list[tuple[bool, object]] = []
            done = threading.Event()
            self._request = (fn, box, done)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name=self._name, daemon=True
                )
                self._thread.start()
        self._wake.set()
        if not done.wait(max(0.0, float(timeout))):
            with self._lock:
                self._degraded_until = time.monotonic() + SOURCE_DEGRADED_SECONDS
            return None
        if not box:
            return None
        ok, value = box[0]
        if not ok:
            raise value
        return value

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                request, self._request = self._request, None
            if request is None:
                continue
            fn, box, done = request
            try:
                box.append((True, fn()))
            except Exception as exc:
                box.append((False, exc))
            finally:
                done.set()
                with self._lock:
                    # 这条线程又能干活了：解除降级（超时那次的调用方早就返回了）。
                    self._degraded_until = 0.0


#: 音频会话源：COM 句柄与线程绑定，必须固定一条线程（见 :class:`_SourceWorker`）。
_AUDIO_WORKER = _SourceWorker("music-sample-audio")
#: 窗口标题源：纯 Win32/psutil，没有线程亲和，但仍固定一条线程（省掉每拍建线程）。
_WINDOW_WORKER = _SourceWorker("music-sample-windows")


def _bounded(fn, timeout: float, *, worker: "_SourceWorker | None" = None):
    """在独立 daemon 线程里跑一次可能卡死的同步调用。

    正常返回把 ``fn()`` 的结果交回（异常原样重抛），**超时返回 ``None``**。
    不 join：Python 没有安全的办法打断卡在同步 COM 调用里的线程，join 只会把
    调用方一起钉死；线程是 daemon，进程退出时由解释器回收。

    ``worker`` 非空时用那条**常驻**线程（:func:`collect` 的两个源都这样：音视频
    COM 接口线程亲和，换线程就会每拍重建）；为空则起一条一次性线程。

    为什么不能只在采样线程上等：卡住的是 ``ISimpleAudioVolume`` 之类的同步 COM
    调用，事件循环/看门狗都轮不上——设置页的「正在采样…」就是这么永久转圈的。
    """
    if worker is not None:
        return worker.run(fn, timeout)
    outcome: list[tuple[bool, object]] = []
    done = threading.Event()

    def work() -> None:
        try:
            outcome.append((True, fn()))
        except Exception as exc:
            outcome.append((False, exc))
        finally:
            done.set()

    threading.Thread(target=work, name="music-sample-source", daemon=True).start()
    if not done.wait(max(0.0, float(timeout))):
        return None
    if not outcome:
        return None
    ok, value = outcome[0]
    if not ok:
        raise value
    return value


def _timeout_reason(source: str, timeout: float) -> str:
    """超时原因（写清是哪个源、等了几秒；不含路径/进程名）。"""
    return f"{source}：读取超时（{float(timeout):g} 秒）"


def _available(probe) -> bool:
    try:
        return bool(probe())
    except Exception:
        return False
