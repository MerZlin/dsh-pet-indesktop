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
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import audio_sessions, now_playing, window_source

#: 暂停期间窗口源的刷新间隔（秒）。``absent``（什么都看不到）仍按每拍问：
#: 那时窗口源是唯一能发现"新开了播放器"的来源。
WINDOW_PAUSED_INTERVAL = 2.0


@dataclass(frozen=True, slots=True)
class Sample:
    """三源的一次采样（``recognizer.decide`` 的输入，也是诊断文本的数据源）。"""

    playback: object = None
    sessions: tuple = ()
    smtc_reason: str = ""
    smtc_error: bool = False
    audio: tuple = ()
    audio_available: bool = True
    windows: tuple = ()
    window_available: bool = True


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
) -> Sample:
    """采一次三源。``smtc`` / ``audio`` / ``windows`` 是测试注入缝（默认模块函数）。

    ``window_cache`` 非空时，**暂停期间**的窗口枚举按 :data:`WINDOW_PAUSED_INTERVAL`
    节流（其余情况照旧每拍问一次）；``clock`` 是节流用的时钟（默认 ``monotonic``）。
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
    if audio_available:
        try:
            audio_rows = tuple(read_audio() or ())
        except Exception:
            audio_rows = ()

    window_available = _available(window_source.available)
    playback = getattr(smtc_sample, "playback", None)
    playing = bool(getattr(playback, "playing", False))
    need_window = playback is None or not playing
    # 暂停 = 播放器明确报了曲目但说"没在放"；absent 没有 playback 对象，逐拍问。
    paused = playback is not None and not playing
    wanted_targets = frozenset(targets or ())
    window_rows: tuple = ()
    if window_available and need_window:
        cached = None
        if paused and window_cache is not None:
            cached = window_cache.get(wanted_targets, now)
        if cached is not None:
            window_rows = cached
        else:
            try:
                window_rows = tuple(read_windows(wanted_targets) or ())
            except Exception:
                window_rows = ()
            if paused and window_cache is not None:
                window_cache.put(wanted_targets, window_rows, now)

    return Sample(
        playback=playback,
        sessions=tuple(getattr(smtc_sample, "sessions", ()) or ()),
        smtc_reason=str(getattr(smtc_sample, "reason", "") or ""),
        smtc_error=bool(getattr(smtc_sample, "failed", False)),
        audio=audio_rows,
        audio_available=audio_available,
        windows=window_rows,
        window_available=window_available,
    )


def _available(probe) -> bool:
    try:
        return bool(probe())
    except Exception:
        return False
