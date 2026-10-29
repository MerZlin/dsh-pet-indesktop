"""读取 Windows 系统媒体会话（SMTC）：当前曲目、进度与传输控制（无 Qt）。

数据来源是 Windows 的 SMTC——播放器主动向系统上报的会话（网易云、QQ 音乐、
酷狗、汽水、浏览器……）。所以这里不需要为单个播放器做适配。

三条实测口径（沿用主分支 ``pet/now_playing.py`` 的结论）：

- **进度不一定有**：QQ 音乐会上报 position/end_time；网易云与酷狗恒为 0。
  因此 :attr:`Playback.position` 允许为 ``None``（"该播放器不提供进度"），
  调用方回退到本地计时。
- **不是所有会话都是歌**：浏览器把网页**视频**也上报成 ``MUSIC(1)``，SMTC 里没有
  字段能把两者分开，只能按会话来源否决（:func:`accepts_singing`）。浏览器会话
  还需要配置里显式打开 ``music_browser_media_enabled``；系统提示音/通知类应用
  在 :data:`_VETO_APP_IDS` 里直接否决。
- **winrt 只在这里惰性导入**：它在非 Windows 或未装可选依赖时不存在；导入发生在
  调用线程（运行时是后台轮询线程），模块导入本身不碰 COM。
- **管理器与会话表按线程缓存**：``request_async()`` 每次都走一次 WinRT 激活，实测
  是本链路最贵的一步；会话集合的变化远慢于 1Hz。所以管理器缓存在**调用线程**上，
  会话列表最多每 :data:`SESSION_REFRESH_SECONDS` 秒重列一次；被选中那一个的
  标题/进度仍然**每拍现读**（暂停边沿、切歌、进度跳变一秒都不能漏）。

**未移植 foobar2000 窗口标题源**：它需要读进程列表 + 枚举顶层窗口标题，属于另一
套 Win32 代码（主分支 ``pet/foobar_playing.py``）。本 mod 只做 SMTC；foobar 用户
可以在 foobar 里装 SMTC 插件，或继续用主分支版本。这条省略是刻意的。

本模块只读系统信息与发传输控制，不发起任何网络请求、不触碰音频流。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import dataclass

# Windows.Media.MediaPlaybackType
MEDIA_TYPE_UNKNOWN = 0
MEDIA_TYPE_MUSIC = 1
MEDIA_TYPE_VIDEO = 2
MEDIA_TYPE_IMAGE = 3

# Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus
PLAYBACK_STATUS_PLAYING = 4

#: 判定"不上报进度"的阈值：末位时长小于该值视为无效时间轴。
_MIN_VALID_DURATION = 0.01

#: 已知浏览器的 SMTC app_id（小写、去掉 ``.exe`` 后比较）。这是**否决表不是白名单**：
#: 表外应用一律不受影响，只有明确认识的浏览器才走浏览器开关分支。
_BROWSER_APP_IDS = frozenset(
    {
        "msedge",
        "microsoftedge",
        "chrome",
        "chromium",
        "firefox",
        "brave",
        "bravebrowser",
        "opera",
        "vivaldi",
        "arc",
    }
)

#: 系统提示音 / 通知类应用：它们会上报"在播"，但不是歌。
_VETO_APP_IDS = frozenset(
    {
        "systemsettings",
        "shellexperiencehost",
        "windows.ui.core.corewindow",
        "notificationlistenerservice",
        "lockapp",
    }
)

#: 选会话时的类型优先级（越小越优先）：音乐 > 未上报 > 图片（视频已否决）。
_MEDIA_TYPE_RANK = {
    MEDIA_TYPE_MUSIC: 0,
    MEDIA_TYPE_UNKNOWN: 1,
    MEDIA_TYPE_IMAGE: 2,
}

#: 传输命令 → 气泡里的成功文案。
ACTION_LABELS = {
    "pause": "播放/暂停",
    "next": "下一首",
    "prev": "上一首",
    "stop": "停止播放",
}


@dataclass(frozen=True, slots=True)
class Playback:
    """一次采样结果：曲目 + 进度。``position=None`` 表示播放器不上报进度。"""

    title: str = ""
    artist: str = ""
    app_id: str = ""
    position: float | None = None
    duration: float = 0.0
    playing: bool = False
    source: str = "smtc"

    def key(self) -> tuple[str, str]:
        """判断"是否换了首歌"的标识（大小写/空白归一）。"""
        return (self.title.strip().lower(), self.artist.strip().lower())

    def label(self) -> str:
        title = self.title.strip()
        artist = self.artist.strip()
        if title and artist:
            return f"{artist} - {title}"
        return title or artist or "未知曲目"


@dataclass(frozen=True, slots=True)
class Session:
    """一个 SMTC 会话的采样快照（选择器的输入，纯数据）。"""

    app_id: str = ""
    media_type: int = MEDIA_TYPE_UNKNOWN
    playing: bool = False
    browser: bool = False
    vetoed: bool = False
    index: int = 0

    @property
    def type_rank(self) -> int:
        return _MEDIA_TYPE_RANK.get(self.media_type, _MEDIA_TYPE_RANK[MEDIA_TYPE_UNKNOWN])


@dataclass(frozen=True, slots=True)
class SmtcSample:
    """一次 SMTC 采样：**全部会话的事实** + 被选中那一个的曲目信息。

    与旧接口 ``(Playback | None, reason)`` 的区别在于 ``failed``：读失败（超时、
    WinRT 异常、字段异常）和"系统里没有会话"是两回事——前者不许被当成"没在放"，
    否则一次抖动就会清空歌词并重新取词（旧实现踩过这个坑）。
    """

    sessions: tuple[Session, ...] = ()
    playback: "Playback | None" = None
    reason: str = ""
    failed: bool = False


def normalized_app_id(app_id: str | None) -> str:
    """归一化 SMTC app_id：小写、去空白、去 ``.exe`` 后缀。

    实测同一台机器上形状不统一（``Chrome`` / ``MSEdge`` / ``cloudmusic.exe``），
    比较前必须归一。
    """
    value = str(app_id or "").strip().lower()
    if value.endswith(".exe"):
        value = value[:-4]
    return value


def is_browser_session(app_id: str | None) -> bool:
    return normalized_app_id(app_id) in _BROWSER_APP_IDS


def is_vetoed_session(app_id: str | None) -> bool:
    return normalized_app_id(app_id) in _VETO_APP_IDS


def allowed_app_ids(player_paths, *, extras: str = "") -> frozenset[str]:
    """从已配置的播放器路径推导可识别的 SMTC 应用名（归一化）。

    空集合表示「不做过滤」；非空时只有命中集合的会话才进唱歌/歌词链路。
    额外识别名用于 AUMID 与 exe 名不一致的播放器（例如「汽水音乐」）。
    """
    values: set[str] = set()
    if isinstance(player_paths, dict):
        for path in player_paths.values():
            name = str(path or "").strip().strip('"').replace("\\", "/").rsplit("/", 1)[-1]
            normalized = normalized_app_id(name)
            if normalized:
                values.add(normalized)
    for chunk in str(extras or "").replace("；", ",").replace("，", ",").replace("、", ",").split(","):
        normalized = normalized_app_id(chunk)
        if normalized:
            values.add(normalized)
    return frozenset(values)


def accepts_singing(
    session: Session,
    *,
    browser_media_enabled: bool = False,
    allowed_ids: frozenset[str] | None = None,
) -> bool:
    """该会话能不能进歌词/唱歌链路（否决表 + 可选白名单）。"""
    if session.vetoed:
        return False
    if allowed_ids and normalized_app_id(session.app_id) not in allowed_ids:
        return False
    if session.media_type in (MEDIA_TYPE_VIDEO, MEDIA_TYPE_IMAGE):
        return False
    if session.browser and not browser_media_enabled:
        return False
    return True


def pick_singing_session(
    sessions: list[Session] | tuple[Session, ...],
    *,
    browser_media_enabled: bool = False,
    tracked_app_id: str = "",
    allowed_ids: frozenset[str] | None = None,
) -> Session | None:
    """在**可接受候选**里为歌词/唱歌链路选会话。

    被否决的会话（视频 / 图片 / 表内的系统应用 / 开关关时的浏览器）在选择阶段
    就被剔除，而不是先选出赢家再否决——否则浏览器报的音乐会挤掉同播的原生播放器。
    候选内：在播者按类型分层（音乐 > 未上报），层内优先"上次跟踪的那个"，否则取
    枚举顺序最靠前的；都没在播则留在跟踪对象上（暂停时不跳走）；否则取第一个。
    一个候选都没有时返回 ``None``。
    """
    accepted = [
        session
        for session in sessions
        if accepts_singing(
            session,
            browser_media_enabled=browser_media_enabled,
            allowed_ids=allowed_ids,
        )
    ]
    if not accepted:
        return None
    wanted = normalized_app_id(tracked_app_id)
    tracked = None
    if wanted:
        tracked = next(
            (session for session in accepted if normalized_app_id(session.app_id) == wanted),
            None,
        )
    playing = [session for session in accepted if session.playing]
    if playing:
        best = min(session.type_rank for session in playing)
        tier = [session for session in playing if session.type_rank == best]
        if tracked is not None and tracked.playing and tracked.type_rank == best:
            return tracked
        return tier[0]
    if tracked is not None:
        return tracked
    return accepted[0]


# ---------------------------------------------------------------- 位置外推


def _utc_now():
    """UTC 当前时间（独立函数便于测试注入）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _extrapolate(position: float, timeline, *, is_playing: bool, end: float) -> float:
    """把采样时刻的 position 外推到"现在"，消除轮询粒度造成的滞后。

    SMTC 的 ``position`` 是某个瞬间的采样值，``last_updated_time`` 记录它被采样的
    时刻；播放器推送间隔可能接近一秒，直接用会让歌词比声音慢最多一个间隔
    （主分支实测 QQ 音乐有 ≤1 秒的音画不同步）。按微软推荐做法修正：

        位置 ≈ position + (现在 - last_updated_time)

    只在正在播放时外推（暂停时位置本就该冻结），并夹到 ``[0, 总时长]``。
    时间戳无效（某些播放器给 1601-01-01 这类空值，网易云实测就是）时不外推。
    """
    if not is_playing:
        return position
    try:
        stamp = timeline.last_updated_time
        if stamp is None or getattr(stamp, "year", 0) < 2000:
            return position
        delta = _utc_now().timestamp() - stamp.timestamp()
        # 只接受合理的前向偏移：负值或过大值说明时钟/字段异常，宁可不修。
        if not (0.0 < delta < 5.0):
            return position
        corrected = position + delta
        if end > 0:
            corrected = min(corrected, end)
        return max(0.0, corrected)
    except Exception:
        return position


# ---------------------------------------------------------------- winrt 惰性导入


def _manager_class():
    """惰性导入 SMTC 管理器；非 Windows / 未装 winrt 时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as manager_class,
        )
    except Exception:
        return None
    return manager_class


def unavailable_reason() -> str:
    """为什么读不到 SMTC（给用户看的一句话）；可用时返回空串。"""
    if sys.platform != "win32":
        return "当前系统不支持读取系统媒体信息（仅 Windows）。"
    if _manager_class() is None:
        return "读取不到系统媒体会话：缺少 winrt 组件，请重装桌宠。"
    return ""


_LOOP_LOCAL = threading.local()

#: 单次采样/控制的超时（秒）：正常采样实测 ~5ms 级；没有它，一次永不返回的
#: WinRT 请求会把采样线程永久钉住（主分支的现场教训）。
CALL_TIMEOUT = 3.0

#: 会话列表的重列间隔（秒）。管理器（``request_async``）本来就只建一次；
#: 会话对象本身按引用缓存，每次采样现读它们的播放状态与元数据。
SESSION_REFRESH_SECONDS = 2.0


def _now() -> float:
    """节流用的单调时钟（独立函数便于测试注入）。"""
    return time.monotonic()


@dataclass
class _ThreadCache:
    """一条采样线程上的 SMTC 缓存（``threading.local`` 持有，天然线程隔离）。"""

    manager: object = None
    sessions: tuple = ()
    at: float | None = None


_CACHE_LOCAL = threading.local()


def _thread_cache() -> _ThreadCache:
    cache = getattr(_CACHE_LOCAL, "state", None)
    if cache is None:
        cache = _ThreadCache()
        _CACHE_LOCAL.state = cache
    return cache


def reset_thread_cache() -> None:
    """丢掉**本线程**的管理器与会话缓存（线程退出或会话表明显失效时调用）。"""
    _CACHE_LOCAL.state = _ThreadCache()


def _run_sync(coro):
    """在调用线程的**常驻**事件循环里跑协程（每拍新建循环是主分支实测的开销大头）。

    SMTC 查询在 GUI 主线程实测会永久阻塞窗口，所以调用方必须在后台线程里用它。
    """
    loop = getattr(_LOOP_LOCAL, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _LOOP_LOCAL.loop = loop
    return loop.run_until_complete(coro)


def close_thread_loop() -> None:
    """关闭**本线程**的常驻事件循环（采样线程退出时调用，避免未关闭循环告警）。"""
    loop = getattr(_LOOP_LOCAL, "loop", None)
    _LOOP_LOCAL.loop = None
    reset_thread_cache()
    if loop is None:
        return
    try:
        if not loop.is_closed():
            loop.close()
    except Exception:
        return


def _session_app_id(session) -> str:
    try:
        return str(getattr(session, "source_app_user_model_id", "") or "")
    except Exception:
        return ""


def _read_playback_info(session):
    try:
        return session.get_playback_info()
    except Exception:
        return None


def _read_media_type(holder) -> int:
    """读 ``playback_type``；读不到一律按 Unknown（绝不冒到外层当成"播放器没了"）。"""
    try:
        value = getattr(holder, "playback_type", None)
        return MEDIA_TYPE_UNKNOWN if value is None else int(value)
    except Exception:
        return MEDIA_TYPE_UNKNOWN


def _read_status(holder) -> int | None:
    try:
        return int(holder.playback_status)
    except Exception:
        return None


def _snapshot(session, index: int) -> Session:
    app_id = _session_app_id(session)
    info = _read_playback_info(session)
    status = _read_status(info)
    return Session(
        app_id=app_id,
        media_type=_read_media_type(info),
        playing=status == PLAYBACK_STATUS_PLAYING,
        browser=is_browser_session(app_id),
        vetoed=is_vetoed_session(app_id),
        index=index,
    )


async def _sample_async(
    *, browser_media_enabled: bool, tracked_app_id: str, allowed_ids: frozenset[str] | None = None
) -> SmtcSample:
    """采一次：全部会话的事实 + 被选中那一个的曲目。

    管理器与会话列表按**调用线程**缓存（:data:`SESSION_REFRESH_SECONDS`），被选中
    会话的播放状态/元数据/时间轴仍然每拍现读——缓存的是"有哪些会话"，不是
    "在放什么"。
    """
    manager_class = _manager_class()
    if manager_class is None:
        return SmtcSample(reason=unavailable_reason())
    cache = _thread_cache()
    manager = cache.manager
    if manager is None:
        try:
            manager = await manager_class.request_async()
        except Exception:
            return SmtcSample(reason="读取系统媒体信息失败。", failed=True)
        cache.manager = manager
        cache.sessions = ()
        cache.at = None
    now = _now()
    if cache.at is None or (now - cache.at) >= SESSION_REFRESH_SECONDS:
        try:
            cache.sessions = tuple(manager.get_sessions() or ())
        except Exception:
            # 管理器自己失效（播放器崩溃/系统重置）：丢掉缓存，下一次重建。
            cache.manager = None
            cache.sessions = ()
            cache.at = None
            return SmtcSample(reason="读取系统媒体信息失败。", failed=True)
        cache.at = now
    raw_sessions = list(cache.sessions)
    if not raw_sessions:
        return SmtcSample(reason="系统里没有正在播放的媒体会话。")
    snapshots = [_snapshot(session, index) for index, session in enumerate(raw_sessions)]
    facts = tuple(snapshots)
    chosen = pick_singing_session(
        snapshots,
        browser_media_enabled=browser_media_enabled,
        tracked_app_id=tracked_app_id,
        allowed_ids=allowed_ids,
    )
    if chosen is None:
        blocked = [
            session.app_id
            for session in snapshots
            if allowed_ids and normalized_app_id(session.app_id) not in allowed_ids
        ]
        if blocked:
            names = "、".join(dict.fromkeys(name or "未知应用" for name in blocked))
            return SmtcSample(
                sessions=facts,
                reason=f"当前播放器（{names}）不在识别范围：设置 → 扩展管理 → 音乐 → 识别范围。",
            )
        return SmtcSample(
            sessions=facts,
            reason="当前媒体会话不是音乐（视频或未开启的浏览器会话已被过滤）。",
        )
    session = raw_sessions[chosen.index]

    # 字段各自兜住：选取与读取之间会话可能失效，或时间轴字段异常。
    try:
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        duration = float(timeline.end_time.total_seconds())
        raw_position = float(timeline.position.total_seconds())
    except Exception:
        # 选中的会话已经被换掉/失效：下一拍重列会话表，别在同一个死句柄上再撞。
        _thread_cache().at = None
        return SmtcSample(sessions=facts, reason="读取系统媒体信息失败。", failed=True)
    title = str(getattr(props, "title", "") or "").strip()
    artist = str(getattr(props, "artist", "") or "").strip()
    if not title and not artist:
        return SmtcSample(
            sessions=facts,
            reason="当前会话没有曲目信息（可能还在加载）。",
        )
    has_timeline = duration > _MIN_VALID_DURATION
    position = raw_position if has_timeline else None
    if position is not None:
        position = _extrapolate(position, timeline, is_playing=chosen.playing, end=duration)
    return SmtcSample(
        sessions=facts,
        playback=Playback(
            title=title,
            artist=artist,
            app_id=chosen.app_id,
            position=position,
            duration=duration if has_timeline else 0.0,
            playing=chosen.playing,
            source="smtc",
        ),
    )


async def _read_async(
    *, browser_media_enabled: bool, tracked_app_id: str, allowed_ids: frozenset[str] | None = None
) -> tuple[Playback | None, str]:
    """旧接口的协程形态：``(Playback | None, reason)``（保留给已有调用方与测试）。"""
    sample = await _sample_async(
        browser_media_enabled=browser_media_enabled,
        tracked_app_id=tracked_app_id,
        allowed_ids=allowed_ids,
    )
    return sample.playback, sample.reason


def _bounded(coro_factory) -> SmtcSample:
    """跑一次采样并把所有失败折成 ``SmtcSample(failed=True)``（绝不抛）。"""
    reason = unavailable_reason()
    if reason:
        return SmtcSample(reason=reason)
    try:
        return _run_sync(asyncio.wait_for(coro_factory(), CALL_TIMEOUT))
    except (asyncio.TimeoutError, TimeoutError):
        return SmtcSample(reason="读取系统媒体信息超时。", failed=True)
    except Exception:
        return SmtcSample(reason="读取系统媒体信息失败。", failed=True)


def read_sample(
    *,
    browser_media_enabled: bool = False,
    tracked_app_id: str = "",
    allowed_ids: frozenset[str] | None = None,
) -> SmtcSample:
    """采样当前 SMTC：会话事实 + 曲目 + 原因 + ``failed``（不抛异常）。"""
    return _bounded(
        lambda: _sample_async(
            browser_media_enabled=bool(browser_media_enabled),
            tracked_app_id=str(tracked_app_id or ""),
            allowed_ids=allowed_ids,
        )
    )


def read_playback_with_reason(
    *,
    browser_media_enabled: bool = False,
    tracked_app_id: str = "",
    allowed_ids: frozenset[str] | None = None,
) -> tuple[Playback | None, str]:
    """采样当前曲目；读不到时第二项是给用户看的原因（不抛异常）。"""
    sample = read_sample(
        browser_media_enabled=browser_media_enabled,
        tracked_app_id=tracked_app_id,
        allowed_ids=allowed_ids,
    )
    return sample.playback, sample.reason


def read_playback(
    *,
    browser_media_enabled: bool = False,
    tracked_app_id: str = "",
    allowed_ids: frozenset[str] | None = None,
) -> Playback | None:
    """采样当前曲目；无播放器/无会话/任何异常都返回 ``None``。"""
    return read_playback_with_reason(
        browser_media_enabled=browser_media_enabled,
        tracked_app_id=tracked_app_id,
        allowed_ids=allowed_ids,
    )[0]


# ---------------------------------------------------------------- 传输控制


def _pick_transport_session(
    manager, tracked_app_id: str, allowed_ids: frozenset[str] | None = None
):
    """传输控制用选择器：跟随在播的会话，暂停时不跳到别的会话上。

    与唱歌链路刻意隔离：用户在看视频时点「暂停」就该暂停那个视频，所以这里既不做
    媒体类型分层、也不做唱歌链路的否决；``allowed_ids`` 非空时只控制识别范围内的
    播放器（与「只识别已配置播放器」的设置保持一致）。
    """
    sessions = [
        session
        for session in (manager.get_sessions() or ())
        if not allowed_ids or normalized_app_id(_session_app_id(session)) in allowed_ids
    ]
    if not sessions:
        return None
    wanted = normalized_app_id(tracked_app_id)
    tracked_session = None
    tracked_playing = False
    playing = []
    for session in sessions:
        info = _read_playback_info(session)
        is_playing = _read_status(info) == PLAYBACK_STATUS_PLAYING
        if wanted and normalized_app_id(_session_app_id(session)) == wanted:
            tracked_session, tracked_playing = session, is_playing
        if is_playing:
            playing.append(session)
    if tracked_session is not None and tracked_playing:
        return tracked_session
    if playing:
        return playing[0]
    if tracked_session is not None:
        return tracked_session
    return sessions[0]


async def _transport_async(
    action: str, tracked_app_id: str, allowed_ids: frozenset[str] | None = None
) -> tuple[bool, str]:
    manager_class = _manager_class()
    if manager_class is None:
        return False, unavailable_reason()
    manager = await manager_class.request_async()
    session = _pick_transport_session(manager, tracked_app_id, allowed_ids)
    if session is None:
        if allowed_ids:
            return False, "识别范围内没有可控制的播放器：设置 → 音乐 → 识别范围。"
        return False, "当前没有可控制的播放器（请先在播放器里播放一首歌）。"
    info = _read_playback_info(session)
    controls = getattr(info, "controls", None)
    if action == "pause":
        ok = bool(await session.try_toggle_play_pause_async())
        return ok, "" if ok else "这个播放器不支持播放/暂停。"
    if action == "stop":
        stop = getattr(session, "try_stop_async", None)
        enabled = True if controls is None else bool(getattr(controls, "is_stop_enabled", True))
        if stop is None or not enabled:
            return False, "这个播放器不支持停止。"
        ok = bool(await stop())
        return ok, "" if ok else "这个播放器不支持停止。"
    previous = action == "prev"
    flag = "is_previous_enabled" if previous else "is_next_enabled"
    if controls is not None and not bool(getattr(controls, flag, False)):
        return False, "这个播放器不支持切歌（网络电台之类）。"
    method = session.try_skip_previous_async if previous else session.try_skip_next_async
    ok = bool(await method())
    return ok, "" if ok else "这个播放器不支持切歌（网络电台之类）。"


def transport(
    action: str, *, tracked_app_id: str = "", allowed_ids: frozenset[str] | None = None
) -> tuple[bool, str]:
    """发一个传输命令；返回 ``(是否成功, 失败原因)``，绝不抛异常。"""
    if action not in ACTION_LABELS:
        return False, f"不认识的操作：{action}"
    reason = unavailable_reason()
    if reason:
        return False, reason
    try:
        return _run_sync(
            asyncio.wait_for(
                _transport_async(action, str(tracked_app_id or ""), allowed_ids),
                CALL_TIMEOUT,
            )
        )
    except (asyncio.TimeoutError, TimeoutError):
        return False, "系统媒体控制调用超时，请稍后重试。"
    except Exception:
        return False, "系统媒体控制调用失败，请稍后重试。"
