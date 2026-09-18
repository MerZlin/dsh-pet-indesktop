# -*- coding: utf-8 -*-
"""读取系统当前正在播放的曲目与播放进度（Windows）。

数据来源是 Windows 的 SMTC（System Media Transport Controls）——播放器主动
向系统上报的会话信息。网易云、QQ 音乐、Spotify、酷狗等接入 SMTC 的播放器
都会出现在同一份会话列表里，因此本模块无需为单个播放器做适配。

两个关键差异（实机实测，2026-09）：

- **曲目信息**：接入 SMTC 的播放器基本都能提供 title/artist/album。
- **播放进度**：不一定有。QQ 音乐会上报（position 随播放前进），
  网易云不上报（position/end_time 恒为 0），酷狗按官方文档同样无时间轴。

因此 ``Playback.position`` 允许为 ``None``，表示"该播放器不提供进度"，
调用方需回退到本地计时推算。这与同类工具（如 Lyricify）的处理方式一致。

本模块只读系统信息，不发起任何网络请求，也不触碰音频流。

阻塞防护（事故 2026-09-17）：SMTC 请求在某些系统/播放器状态下**永不完成**，
且该 await 连 ``asyncio.wait_for`` 都取消不掉——事件循环会钉死在 proactor 的
``_poll``（定时器得不到执行）。因此任何 ``asyncio.run`` 都**不许在调用线程里等**：
采样放到后台线程（:func:`_sampler_loop`），取值只读快照，用户操作走有界执行
（:func:`_run_bounded`）。详见 ``.scratch/now-playing-gui-hang/spec.md``。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import media_window

log = logging.getLogger(__name__)

# 判定"不上报进度"的阈值：末位时长小于该值视为无效时间轴。
_MIN_VALID_DURATION = 0.01


@dataclass(frozen=True)
class Track:
    """一首曲目的元信息。"""

    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    playing: bool = False

    def key(self) -> tuple[str, str]:
        """用于判断"是否换了首歌"的标识（大小写归一）。"""
        return (self.title.strip().lower(), self.artist.strip().lower())


@dataclass(frozen=True)
class Playback:
    """一次采样结果：曲目 + 播放进度。

    ``position`` 为 ``None`` 表示当前播放器不上报进度，调用方应回退到
    本地计时推算；``updated_at`` 是读到该值的本地时刻，供调用方换算。
    """

    track: Track
    position: float | None
    updated_at: float


def _import_winrt():
    """惰性导入 winrt。缺失（非 Windows / 未装可选依赖）时返回 None。"""
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _Manager,
        )
    except Exception:
        return None
    return _Manager


def player_process_running(exe_name: str) -> bool:
    """指定可执行名的进程是否在跑。纯进程扫描，不碰 WinRT。

    按调用方传入的 exe 名精确匹配，**不维护播放器白名单**——白名单会把
    名单外的播放器（如 LX Music）静默挡掉。
    扫描失败时返回 True（不拦截），宁可多试一次也不误伤。
    """
    wanted = str(exe_name or "").strip().lower()
    if not wanted:
        return False
    try:
        import psutil
    except Exception:
        return True
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                name = str(proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name == wanted:
                return True
    except Exception:
        return True
    return False


def available() -> bool:
    """SMTC 是否可用（winrt 是否装得上）。供设置页决定是否禁用开关。"""
    if sys.platform != "win32":
        return False
    return _import_winrt() is not None


async def _pick_playing_session(manager):
    """选出应跟踪的会话：优先正在播放的，其次第一个。

    实机常见同时存在多个会话（如网易云在播 + QQ 音乐待机），
    此时必须跟随真正在播的那个。
    """
    sessions = list(manager.get_sessions())
    if not sessions:
        return None
    for session in sessions:
        try:
            # PlaybackStatus.PLAYING == 4
            if int(session.get_playback_info().playback_status) == 4:
                return session
        except Exception:
            continue
    return sessions[0]


async def _read_async() -> Playback | None:
    manager_cls = _import_winrt()
    if manager_cls is None:
        return None
    manager = await manager_cls.request_async()
    session = await _pick_playing_session(manager)
    if session is None:
        return None

    # 读字段要各自兜住：session 选取与读取之间可能失效，或某个播放器给的
    # 时间轴字段异常（end_time 为 None 等）。让异常冒到最外层会被当成
    # "播放器没了"，控制器于是清空歌词、下一拍重新取词（三次网络请求）。
    try:
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        timeline = session.get_timeline_properties()
        end_time = float(timeline.end_time.total_seconds())
        raw_position = float(timeline.position.total_seconds())
    except Exception:
        return None

    title = str(getattr(props, "title", "") or "").strip()
    artist = str(getattr(props, "artist", "") or "").strip()
    if not title and not artist:
        return None

    # 播放器不上报时间轴时 end_time 与 position 恒为 0，此时标记为"无进度"。
    has_timeline = end_time > _MIN_VALID_DURATION
    is_playing = int(info.playback_status) == 4

    position = raw_position if has_timeline else None
    if position is not None:
        position = _extrapolate(position, timeline, is_playing=is_playing, end=end_time)

    track = Track(
        title=title,
        artist=artist,
        album=str(getattr(props, "album_title", "") or "").strip(),
        duration=end_time if has_timeline else 0.0,
        playing=is_playing,
    )
    return Playback(track=track, position=position, updated_at=time.monotonic())


def _read_blocking() -> Playback | None:
    """单次同步采样（**只允许在采样/工作线程里调用**）。

    Windows 之外直接返回 None；任何异常都吞掉——歌词不能因为第三方接口异常
    影响桌宠本体。注意：本函数**可能永不返回**（SMTC 请求挂住），调用方
    （:func:`_sampler_loop`）因此必须允许自己被摘牌弃用。
    """
    if sys.platform != "win32":
        return None
    try:
        return asyncio.run(_read_async())
    except Exception:
        return None


def _extrapolate(
    position: float, timeline, *, is_playing: bool, end: float
) -> float:
    """把采样时刻的 position 外推到"现在"，消除轮询粒度造成的滞后。

    SMTC 的 ``position`` 不是连续快照，而是某个瞬间的采样值，
    ``last_updated_time`` 记录它被采样的时刻。播放器推送间隔可能接近一秒，
    直接用 position 会让歌词比声音慢最多一个间隔——实测 QQ 音乐存在
    不超过 1 秒的音画不同步，正是这个原因。按微软的推荐做法做外推修正：

        位置 ≈ position + (现在 - last_updated_time)

    只在正在播放时外推（暂停时位置本就该冻结），并夹到 [0, 总时长] 内。
    """
    if not is_playing:
        return position
    try:
        stamp = timeline.last_updated_time
        # 某些播放器给的是 epoch(1601-01-01) 之类的无效值，此时不外推。
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


def _utc_now():
    """UTC 当前时间（独立函数便于测试注入）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def _pick_playback_session():
    """取当前应操作的 SMTC 会话（优先正在播放的那个）。

    """
    manager_cls = _import_winrt()
    if manager_cls is None:
        return None
    manager = await manager_cls.request_async()
    return await _pick_playing_session(manager)


async def _skip_async(to_previous: bool) -> bool:
    session = await _pick_playback_session()
    if session is None:
        return False
    # 先看播放器是否真的声明支持切歌：方法存在不代表允许（比如网络电台）。
    controls = session.get_playback_info().controls
    flag = "is_previous_enabled" if to_previous else "is_next_enabled"
    if not bool(getattr(controls, flag, False)):
        return False
    method = (
        session.try_skip_previous_async if to_previous else session.try_skip_next_async
    )
    return bool(await method())


def skip_track(direction: str = "next") -> bool:
    """切换到下一首/上一首（``direction`` 为 ``"next"`` 或 ``"previous"``）。

    返回是否成功；不支持切歌的播放器、或没有会话时返回 ``False``。
    与 :func:`get_now_playing` 一样绝不抛异常。
    """
    if sys.platform != "win32":
        return False
    return bool(_run_bounded(lambda: asyncio.run(_skip_async(direction == "previous")), False))


async def _play_pause_async() -> bool:
    session = await _pick_playback_session()
    if session is None:
        return False
    return bool(await session.try_toggle_play_pause_async())


def toggle_play_pause() -> bool:
    """暂停/恢复当前播放器。返回是否成功（无会话或不支持时为 False）。"""
    if sys.platform != "win32":
        return False
    return bool(_run_bounded(lambda: asyncio.run(_play_pause_async()), False))


async def _play_session_async(exe_name: str) -> bool:
    manager_cls = _import_winrt()
    if manager_cls is None:
        return False
    # 该播放器进程不在就直接放弃：既省掉一次可能永久阻塞的调用，
    # 也让调用方可以据此判断"需要先启动它"。
    if not player_process_running(exe_name):
        return False
    manager = await manager_cls.request_async()
    wanted = str(exe_name or "").strip().lower()
    for session in manager.get_sessions():
        if wanted and wanted in str(session.source_app_user_model_id).lower():
            return bool(await session.try_play_async())
    return False


def play_session_for(exe_name: str) -> bool:
    """让指定播放器开始播放；找不到它的会话时返回 False。"""
    if sys.platform != "win32":
        return False
    return bool(
        _run_bounded(lambda: asyncio.run(_play_session_async(exe_name)), False)
    )


# --- 阻塞防护：采样与用户操作都不许在调用线程里等 SMTC（事故 2026-09-17）-------
# 实机结论（证据见 .scratch/now-playing-gui-hang/spec.md）：
#   * request_async() 0.016s 就返回 _IAsyncOperation（调用本身不阻塞）；
#   * 但 await asyncio.wait_for(op, 3.0) 的 3 秒定时器**不生效**——事件循环钉在
#     proactor _poll（windows_events.py:825），8 秒后主线程仍在等待。
# 所以「给 await 加超时」这条路不成立，唯一可靠的做法是让**别的线程**去等它：
# 采样线程可以被摘牌弃用，调用线程只读快照或做有界等待。
_SAMPLE_INTERVAL = 0.3     # 后台采样间隔（秒）
# 采样间隔的"空转档"：一个媒体会话都没有时（没开播放器/播放器不露面）放慢到
# 这一档。多宠物一起挂在桌面上时，N 个进程 × 每秒 3.3 次窗口枚举 + WASAPI
# 查询是白白烧 CPU/电；放慢后空闲开销约降到 1/4，而"开始放歌"最多晚
# _SAMPLE_EMPTY_GRACE 内的这一档被发现（1.2s，用户感知不到）。
_SAMPLE_IDLE_INTERVAL = 1.2
_SAMPLE_EMPTY_GRACE = 3.0   # 连续空会话多久后才降档（避免刚起播就被慢节拍拖住）
_SAMPLE_STALL_LIMIT = 5.0  # 采样线程停摆超过该时长 = 卡死，弃用并重开
_RESTART_COOLDOWN = 5.0    # 两次重开的最小间隔（卡死时不疯狂开线程）
_IDLE_STOP = 5.0           # 调用方连续多久不取值就收摊（歌词关/窗口隐藏）
_ACTION_TIMEOUT = 1.5      # 用户操作（播放/暂停/切歌）在调用线程最多等多久
# SMTC 被判定卡死后的退避窗口：期间不再调用 SMTC（否则新采样线程立刻又卡死，
# 兜底永远轮不到），只走窗口标题兜底；窗口到期后重试一次以探测 SMTC 恢复
# （2026-09-17 实机 SMTC 间歇性永不返回，spec 见 .scratch/media-window-fallback/）。
_SMTC_BACKOFF_S = 120.0
# SMTC 采样最小间隔（秒）：见 _sample_once 的说明——绝不能跟着 0.3s 的采样节拍打
# RequestAsync()，那等于每秒 3 次以上，实机怀疑就是把系统 SMTC 打挂的原因。
_SMTC_MIN_INTERVAL_S = 1.0

_sample_lock = threading.Lock()
_sample_token: object | None = None      # 当前采样线程的身份牌（摘牌即失效）
_sample_thread: threading.Thread | None = None
_sample_value: Playback | None = None    # 最近一次成功采样（快照）
_sample_ready = False                    # 快照是否可用（区分「无播放器」与「没采过」）
_sample_beat = 0.0                       # 采样线程最近一次开工/收工时刻
_sample_empty_since: float | None = None  # 连续"没有任何会话"的起点（空转降档用）
_sample_started_at = float("-inf")       # 当前采样线程启动时刻（冷却判定）
_sample_request_at = 0.0                 # 调用方最近一次取值时刻（空闲退出）
_stall_reported = False                  # 卡死告警只报一次（恢复后重置）
_smtc_wedged_until = 0.0                 # SMTC 退避截止时刻（monotonic）
_smtc_last_at = 0.0                      # 上一次真正调用 SMTC 的时刻（限速用）
_window_log_key: tuple[str, str, bool] | None = None  # 兜底来源最近上报的（曲目, 播放）
_window_sticky: tuple[Playback, float] | None = None   # 最近一次成功的兜底样本 + 时刻
_WINDOW_STICKY_S = 20.0                                # 偶发取不到时沿用的时长（秒）


def _read_window_media() -> Playback | None:
    """窗口标题兜底：SMTC 不可用/查不到会话时用它拿到「在放什么歌」。

    没有播放进度（``position=None``，与网易云在 SMTC 下的表现一致，控制器已支持）；
    ``playing`` 由 media_window 结合会话状态判定。

    **偶发取不到时不返回 None**：播放器切窗口/改标题会让这个探针单拍失败，而控制器
    一旦收到 None 就复位整条链路——重新取词、歌词时间轴从 0 秒重来。实机症状正是
    「歌词显示一半就只剩歌名，然后从头再来」，所以这里对最近的成功结果做短时粘滞
    （:data:`_WINDOW_STICKY_S`）。
    """
    global _window_log_key, _window_sticky
    try:
        found = media_window.read_window_media()
    except Exception:
        found = None
    if not found:
        if _window_sticky is not None:
            value, at = _window_sticky
            if (time.monotonic() - at) <= _WINDOW_STICKY_S:
                return value
        return None
    title, artist, playing = found
    key = (str(artist), str(title), bool(playing))
    if key != _window_log_key:
        # 换歌、或播放判定翻转才记一行：出问题时日志里能直接看到兜底来源与判定结果。
        _window_log_key = key
        log.info("窗口标题监听：%s - %s（playing=%s）", artist, title, playing)
    value = Playback(
        track=Track(title=str(title), artist=str(artist), playing=bool(playing)),
        position=None,
        updated_at=time.monotonic(),
    )
    _window_sticky = (value, value.updated_at)
    return value


def _now() -> float:
    """当前单调时刻（独立函数便于测试注入）。"""
    return time.monotonic()


def _sample_once() -> Playback | None:
    """一次采样：**SMTC 优先**（带播放进度），退避期内或查不到时退回窗口标题。

    SMTC 那条路被限速：``GlobalSystemMediaTransportControlsSessionManager.
    RequestAsync()`` 是重家伙，原先跟着采样节拍（0.3s）打，等于 3.3 次/秒——
    实机 2026-09-17 系统 SMTC 挂死（请求永不返回，重启外壳/播放器都不好，只能重启机器），
    时间点与高频采样吻合。窗口标题兜底很便宜、照常 0.3s 跑；SMTC 最多
    :data:`_SMTC_MIN_INTERVAL_S` 一次（与既有歌词 tick 同量级）。
    """
    global _smtc_last_at, _stall_reported
    now = _now()
    if now >= _smtc_wedged_until and (now - _smtc_last_at) >= _SMTC_MIN_INTERVAL_S:
        _smtc_last_at = now
        value = _read_blocking()  # 可能永不返回：由取值方监管弃用
        if value is not None:
            if _stall_reported:
                # SMTC 恢复：解除「只报一次」的抑制，并切回带进度的来源。
                _stall_reported = False
                log.info("SMTC 已恢复，切回带播放进度的采样来源")
            return value
    return _read_window_media()


def _next_sample_interval(value: Playback | None, now: float) -> float:
    """本次采样后该歇多久：一直没有媒体会话就降档（多宠物一起空转时省开销）。

    纯函数 + 模块级状态，便于单测：``value is None`` 表示"这一拍没有任何会话"。
    """
    global _sample_empty_since
    if value is not None:
        _sample_empty_since = None
        return _SAMPLE_INTERVAL
    if _sample_empty_since is None:
        _sample_empty_since = now
        return _SAMPLE_INTERVAL
    if (now - _sample_empty_since) >= _SAMPLE_EMPTY_GRACE:
        return _SAMPLE_IDLE_INTERVAL
    return _SAMPLE_INTERVAL


def _sampler_loop(token: object) -> None:
    """后台采样线程体：循环采样并发布快照；被摘牌或无人取值即退场。"""
    global _sample_beat, _sample_ready, _sample_thread, _sample_token
    global _sample_value
    while True:
        with _sample_lock:
            if _sample_token is not token:
                return  # 已被弃用/停止：不发布、直接退场
            if time.monotonic() - _sample_request_at > _IDLE_STOP:
                _sample_token = None
                _sample_thread = None
                return  # 没人再取值：收摊，不留常驻轮询
            _sample_beat = time.monotonic()
        value = _sample_once()  # 可能永不返回：由取值方监管弃用
        with _sample_lock:
            if _sample_token is not token:
                return  # 迟到的陈旧结果不得发布
            _sample_value = value
            _sample_ready = True
            _sample_beat = time.monotonic()
        time.sleep(_next_sample_interval(value, time.monotonic()))


def _start_sampler_locked(now: float) -> threading.Thread:
    """（持 ``_sample_lock``）摘掉旧身份并登记一个新采样线程。"""
    global _sample_beat, _sample_started_at, _sample_thread, _sample_token
    token = object()
    _sample_token = token
    _sample_started_at = now
    _sample_beat = now
    thread = threading.Thread(
        target=_sampler_loop, args=(token,), name="now-playing-sampler", daemon=True,
    )
    _sample_thread = thread
    return thread


def _ensure_sampler() -> None:
    """保证有一个采样线程在跑；停摆的线程被摘牌重开（带冷却，绝不阻塞）。"""
    global _sample_ready, _sample_request_at, _smtc_wedged_until, _stall_reported
    now = time.monotonic()
    thread = None
    with _sample_lock:
        _sample_request_at = now
        current = _sample_thread
        healthy = (
            current is not None
            and current.is_alive()
            and (now - _sample_beat) <= _SAMPLE_STALL_LIMIT
        )
        if healthy or (now - _sample_started_at) < _RESTART_COOLDOWN:
            return
        if current is not None and current.is_alive():
            # 卡死：摘牌弃用（它随后的返回值会被身份校验拦住），快照同时作废——
            # 调用方看到「未知」而不是无限期沿用陈旧曲目。同时进入 SMTC 退避：
            # 否则新采样线程会立刻再次卡在同一个请求上，窗口兜底永远轮不到。
            _sample_ready = False
            _smtc_wedged_until = now + _SMTC_BACKOFF_S
            if not _stall_reported:
                _stall_reported = True
                log.warning(
                    "SMTC 采样停摆超过 %.1fs：退避 %.0fs 并改用窗口标题兜底"
                    "（窗口不会因此冻结，歌词可能没有进度）",
                    _SAMPLE_STALL_LIMIT, _SMTC_BACKOFF_S,
                )
        thread = _start_sampler_locked(now)
    thread.start()


def _stop_sampler() -> None:
    """摘牌当前采样线程并清空快照（空闲退出、测试隔离、收尾共用）。

    卡在系统调用里的旧线程无法被强杀，但它已被摘牌：既不会发布结果，
    也会在迟到的返回之后自行退场。
    """
    global _sample_ready, _sample_started_at, _sample_thread, _sample_token
    global _sample_value, _smtc_last_at, _smtc_wedged_until, _sample_empty_since
    with _sample_lock:
        _sample_token = None
        _sample_thread = None
        _sample_value = None
        _sample_ready = False
        _sample_empty_since = None  # 空转降档状态也归零：重新开始时先按快节拍观察
        _sample_started_at = float("-inf")
        _smtc_wedged_until = 0.0  # 重新开始时清掉退避：给 SMTC 一次新机会
        _smtc_last_at = 0.0       # 限速计时也归零


def _run_bounded(factory: Callable[[], object], default):
    """在工作线程里跑 ``factory()``，调用线程最多等 ``_ACTION_TIMEOUT`` 秒。

    超时按 ``default`` 返回（调用方语义 = 「无会话/不支持」）：SMTC 卡死时
    用户点右键菜单也不会冻住窗口。卡死的工作线程是守护线程，不阻塞进程退出。
    """
    box: list = []

    def _work() -> None:
        try:
            box.append(factory())
        except Exception:
            log.debug("播放器操作异常（按不支持处理）", exc_info=True)

    thread = threading.Thread(target=_work, name="now-playing-action", daemon=True)
    thread.start()
    thread.join(_ACTION_TIMEOUT)
    return box[0] if box else default


def get_now_playing() -> Playback | None:
    """返回后台采样线程最近一次结果；无结果（尚未采到/采样卡死）时为 ``None``。

    本函数**只读快照，绝不阻塞调用方**（通常就是 GUI 线程），也绝不抛异常：
    底层 SMTC 请求可能永不完成（事故 2026-09-17），采样因此放在
    :func:`_sampler_loop` 线程里做。歌词只是锦上添花——卡死的第三方接口最多
    让歌词停止更新，绝不影响桌宠本体。
    """
    _ensure_sampler()
    with _sample_lock:
        return _sample_value if _sample_ready else None
