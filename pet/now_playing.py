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
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass

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

    ``app_id`` 是该会话的 ``source_app_user_model_id``（如 ``cloudmusic.exe``）。
    调用方把它带回下一次采样，用于"粘住当前跟踪的播放器"——本机实测同时存在
    多个会话时（网易云在播 + 浏览器标签页待机），没有这个标识就有可能在
    两者之间乱跳。
    """

    track: Track
    position: float | None
    updated_at: float
    app_id: str = ""


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


def _session_status(session) -> int | None:
    """读会话的播放状态；读不到返回 ``None``（不抛，视作未知）。"""
    try:
        # PlaybackStatus.PLAYING == 4
        return int(session.get_playback_info().playback_status)
    except Exception:
        return None


def _session_app_id(session) -> str:
    try:
        return str(getattr(session, "source_app_user_model_id", "") or "")
    except Exception:
        return ""


async def _pick_playing_session(manager, tracked_app_id: str | None = None):
    """选出应跟踪的会话。

    实机常见同时存在多个会话（如网易云在播 + 浏览器标签页待机），必须跟随
    真正在播的那个；但**不能**在用户只是暂停一下的时候跳到别的会话上去。

    规则（按优先级）：
    1. 当前跟踪的会话仍在播 → 保持它（多会话同播时不被抢走）。
    2. 否则取正在播的会话（多个时按会话枚举顺序取第一个）。
    3. 全都没在播 → **留在当前跟踪的会话上**。旧实现直接退回
       ``sessions[0]``，实测那条正是 Chrome（浏览器标签页），于是用户暂停
       网易云的瞬间，桌宠就跳到浏览器里那首歌上去了。
    4. 没有跟踪对象也无人在播 → 退回第一个（保持旧兼容行为）。

    只读一次每个会话的状态，不额外增加 WinRT 往返。
    """
    sessions = list(manager.get_sessions())
    if not sessions:
        return None
    wanted = str(tracked_app_id or "").strip().lower()
    tracked_session = None
    tracked_status: int | None = None
    playing: list = []
    for session in sessions:
        status = _session_status(session)
        if wanted and _session_app_id(session).strip().lower() == wanted:
            tracked_session, tracked_status = session, status
        if status == 4:  # PLAYING
            playing.append(session)
    if tracked_session is not None and tracked_status == 4:
        return tracked_session
    if playing:
        return playing[0]
    if tracked_session is not None:
        return tracked_session
    return sessions[0]


async def _read_async(tracked_app_id: str | None = None) -> Playback | None:
    manager_cls = _import_winrt()
    if manager_cls is None:
        return None
    manager = await manager_cls.request_async()
    session = await _pick_playing_session(manager, tracked_app_id)
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
    return Playback(
        track=track,
        position=position,
        updated_at=time.monotonic(),
        app_id=_session_app_id(session),
    )


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


async def _pick_playback_session(tracked_app_id: str | None = None):
    """取当前应操作的 SMTC 会话（优先正在播放的那个）。

    """
    manager_cls = _import_winrt()
    if manager_cls is None:
        return None
    manager = await manager_cls.request_async()
    return await _pick_playing_session(manager, tracked_app_id)


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
    try:
        return asyncio.run(_skip_async(direction == "previous"))
    except Exception:
        return False


async def _play_pause_async() -> bool:
    session = await _pick_playback_session()
    if session is None:
        return False
    return bool(await session.try_toggle_play_pause_async())


def toggle_play_pause() -> bool:
    """暂停/恢复当前播放器。返回是否成功（无会话或不支持时为 False）。"""
    if sys.platform != "win32":
        return False
    try:
        return asyncio.run(_play_pause_async())
    except Exception:
        return False


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
    try:
        return asyncio.run(_play_session_async(exe_name))
    except Exception:
        return False


def get_now_playing(tracked_app_id: str | None = None) -> Playback | None:
    """返回当前播放信息；无播放器/无会话/任何异常时返回 ``None``。

    ``tracked_app_id`` 是上一次采样得到的 ``Playback.app_id``，用于让会话选取
    粘住当前跟踪的播放器（见 :func:`_pick_playing_session`）。

    本函数绝不抛异常——歌词只是锦上添花，不能因为第三方接口异常影响桌宠本体。
    """
    if sys.platform != "win32":
        return None
    try:
        return asyncio.run(_read_async(tracked_app_id))
    except Exception:
        return None
