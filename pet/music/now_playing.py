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

**SMTC 之外还有一个补充源**：foobar2000 默认不注册 SMTC，永远不出现在会话
列表里，所以它额外走"进程 + 主窗口标题"（``pet/foobar_playing.py``）。那一路只在
**SMTC 没有在播会话**时被询问（见 :func:`_read_foobar_playback`），返回的
``app_id`` 是 :data:`foobar_playing.FOOBAR_APP_ID`，调用方据此不拿它覆盖 SMTC 的
跟踪位。

本模块只读系统信息，不发起任何网络请求，也不触碰音频流。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

from . import foobar as foobar_playing
from . import lxmusic as lxmusic_playing

# 判定"不上报进度"的阈值：末位时长小于该值视为无效时间轴。
_MIN_VALID_DURATION = 0.01

# Windows.Media.MediaPlaybackType
MEDIA_TYPE_UNKNOWN = 0
MEDIA_TYPE_MUSIC = 1
MEDIA_TYPE_VIDEO = 2
MEDIA_TYPE_IMAGE = 3

# 选会话时的类型优先级（越小越优先）：音乐 > 未上报 > 视频。
# 视频排最后是因为它不该进唱歌链路；未上报(Unknown)排在中间——LX Music 这类
# 第三方播放器不上报类型，一刀切过滤会静默误伤（仓库明确反对白名单式误伤）。
_MEDIA_TYPE_RANK = {
    MEDIA_TYPE_MUSIC: 0,
    MEDIA_TYPE_UNKNOWN: 1,
    MEDIA_TYPE_VIDEO: 2,
    MEDIA_TYPE_IMAGE: 3,
}

# 已知浏览器的 SMTC app_id（小写、去掉 .exe 后缀后比较）。
#
# 为什么需要它：浏览器把网页里的**视频**也上报成 MUSIC(1)——本机实测
# （2026-10）Edge 与 Chromium 播放 B 站视频时 playback_type 恒为 1，
# 页面还声明了空元数据（artist/album 皆空），SMTC 里没有任何字段能把
# "网页视频"与"网页音乐"分开。所以网页视频只能按会话来源（是不是浏览器）
# 否决，而不是按类型。
#
# 这是**否决表不是白名单**：表外的应用一律不受影响（退回旧行为），只有明确
# 认识的浏览器才走浏览器分支；不会因为某个播放器没被登记就被挡掉。
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


def normalized_app_id(app_id: str | None) -> str:
    """归一化 SMTC app_id：小写、去空白、去 ``.exe`` 后缀。

    实测同一台机器上 app_id 形状不统一（``Chrome`` / ``MSEdge`` /
    ``cloudmusic.exe``），比较前必须归一，否则 ``Chrome`` 与 ``chrome.exe``
    会被当成两个应用。
    """
    value = str(app_id or "").strip().lower()
    if value.endswith(".exe"):
        value = value[:-4]
    return value


@lru_cache(maxsize=1)
def _firefox_app_ids() -> frozenset[str]:
    """Read Firefox's registered install identities once per process (Windows).

    Firefox uses an install-path hash as its SMTC AppUserModelID, not its exe
    name. TaskBarIDs records that mapping for both per-user and system installs.
    Inspect both registry views so 32-bit installs work in a 64-bit pet too.
    Never infer browser identity merely from a hex-shaped ID: unknown players
    must remain accepted. A new install is discovered after restarting the pet.
    """
    if sys.platform != "win32":
        return frozenset()
    try:
        import winreg
    except ImportError:
        return frozenset()
    identities: set[str] = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    hive, r"Software\Mozilla\Firefox\TaskBarIDs", 0,
                    winreg.KEY_READ | view,
                ) as key:
                    index = 0
                    while True:
                        try:
                            _path, value, kind = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        index += 1
                        if kind == winreg.REG_SZ and isinstance(value, str):
                            identity = normalized_app_id(value)
                            if identity:
                                identities.add(identity)
            except OSError:
                # Missing/inaccessible keys must not disable media detection.
                continue
    return frozenset(identities)


def is_browser_session(app_id: str | None) -> bool:
    """该会话是否来自已知浏览器（见 :data:`_BROWSER_APP_IDS` 的表外放行说明）。

    表外应用一律返回 False——只否决明确认识的浏览器，不做白名单式拦截。
    """
    identity = normalized_app_id(app_id)
    return identity in _BROWSER_APP_IDS or bool(identity and identity in _firefox_app_ids())


def is_foobar_source(app_id: str | None) -> bool:
    """这条采样是否来自 foobar2000 窗口标题源（非 SMTC 会话）。

    调用方据此**不要**把它写进 SMTC 的跟踪位（``tracked_app_id``）：那个位是给
    会话选取用的，塞一个 SMTC 里不存在的 id 会白白丢掉"粘住当前播放器"的能力。
    """
    return str(app_id or "").strip().lower() == foobar_playing.FOOBAR_APP_ID


def _read_media_type(holder) -> int:
    """读 ``playback_type``（读不到一律按 Unknown）。

    属性同时挂在 ``PlaybackInfo`` 与 ``MediaProperties`` 上（两处同值，实测）。
    调用方应把同一个 ``PlaybackInfo`` 对象复用给 :func:`_read_playback_status`
    （见 :func:`_session_snapshot`）——每个会话每次采样只读一次它。

    必须兜住异常：只装 ``winrt-Windows.Media.Control`` 时，访问该属性会因缺少
    定义枚举的 ``winrt-Windows.Media`` 而抛 AttributeError；属性为 None 同样
    按 Unknown。**绝不能让它冒到 `_read_async` 的字段兜底**——那会把整次采样
    变成 None，"读不到类型"就退化成"播放器没了"。
    """
    try:
        value = getattr(holder, "playback_type", None)
        return MEDIA_TYPE_UNKNOWN if value is None else int(value)
    except Exception:
        return MEDIA_TYPE_UNKNOWN


def _read_playback_status(holder) -> int | None:
    """读 ``playback_status``（读不到返回 None，不抛）。"""
    try:
        return int(holder.playback_status)
    except Exception:
        return None


def _source_allows_singing(media_type: int, browser: bool, allow_browser: bool) -> bool:
    """否决规则本体（唯一一份实现，选择器与动画门共用）：

    - 明确 ``VIDEO(2)`` → 否决；
    - ``allow_browser=False``（浏览器开关关）且会话来自已知浏览器 → 否决；
    - 其余（音乐、Unknown、表外应用）→ 放行。
    """
    if media_type == MEDIA_TYPE_VIDEO:
        return False
    if not allow_browser and browser:
        return False
    return True


@dataclass(frozen=True)
class _SessionSnapshot:
    """一个会话的采样快照。

    存在的理由有两条，都是被实测逼出来的：

    1. **每个会话每次采样只读一次** ``PlaybackInfo``：类型、播放状态都从同一份
       读出复用。此前"选会话时读一遍、算类型排名时再读一遍、否决时又读一遍"，
       单会话一次采样读 5 次（HEAD 基线 2 次）——同一份数据反复取是白开销。
    2. 选择阶段就要能问"这个会话能不能进唱歌链路"（见
       :func:`_pick_singing_session`），而那个判断需要类型与来源同时在场。
    """

    session: object
    app_id: str
    media_type: int
    status: int | None
    browser: bool

    @property
    def playing(self) -> bool:
        return self.status == 4  # PLAYING

    def allows_singing(self, allow_browser: bool) -> bool:
        """该会话是否算"歌"（**不看是否在播**：暂停的视频/浏览器会话同样不该被
        当成歌曲显示出来）。"""
        return _source_allows_singing(self.media_type, self.browser, allow_browser)

    @property
    def type_rank(self) -> int:
        return _MEDIA_TYPE_RANK.get(
            self.media_type, _MEDIA_TYPE_RANK[MEDIA_TYPE_UNKNOWN]
        )


def _session_snapshot(session) -> _SessionSnapshot:
    """读一次 ``PlaybackInfo``，把该会话的类型/状态/来源固化下来。"""
    app_id = _session_app_id(session)
    try:
        info = session.get_playback_info()
    except Exception:
        info = None
    return _SessionSnapshot(
        session=session,
        app_id=app_id,
        media_type=_read_media_type(info),
        status=_read_playback_status(info),
        browser=is_browser_session(app_id),
    )


def _session_snapshots(manager) -> list[_SessionSnapshot]:
    return [_session_snapshot(session) for session in manager.get_sessions()]


async def _pick_playing_session(manager, tracked_app_id: str | None = None):
    """选出应跟踪的会话（**传输控制**用：切歌 / 暂停恢复 / 启动播放器）。

    实机常见同时存在多个会话（如网易云在播 + 浏览器标签页待机），必须跟随
    真正在播的那个；但**不能**在用户只是暂停一下的时候跳到别的会话上去。

    规则（按优先级）：
    1. 当前跟踪的会话仍在播 → 保持它（多会话同播时不被抢走）。
    2. 否则取正在播的会话（多个时按会话枚举顺序取第一个）。
    3. 全都没在播 → **留在当前跟踪的会话上**。旧实现直接退回
       ``sessions[0]``，实测那条正是 Chrome（浏览器标签页），于是用户暂停
       网易云的瞬间，桌宠就跳到浏览器里那首歌上去了。
    4. 没有跟踪对象也无人在播 → 退回第一个（保持旧兼容行为）。

    **这是传输控制的选择器，语义与唱歌链路刻意隔离**：用户在看视频时点
    「暂停」就该暂停那个视频，所以这里既不做媒体类型分层、也不做唱歌链路的
    否决（哪怕那句判决是"这不是歌"）。歌词/唱歌用
    :func:`_pick_singing_session`——只有它会在**可接受候选中**重选。
    """
    sessions = list(manager.get_sessions())
    if not sessions:
        return None
    wanted = normalized_app_id(tracked_app_id)
    tracked_session = None
    tracked_status: int | None = None
    playing: list = []
    for session in sessions:
        status = _session_status(session)
        if wanted and normalized_app_id(_session_app_id(session)) == wanted:
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


def _pick_singing_session(
    snapshots: list[_SessionSnapshot],
    tracked_app_id: str | None,
    allow_browser: bool,
) -> _SessionSnapshot | None:
    """在**可接受候选**里为歌词/唱歌链路选会话。

    与传输控制的关键区别：被否决的会话（明确 VIDEO、或开关关时的已知浏览器）
    **在选择阶段就被剔除**，而不是先选出赢家再否决。只否决赢家会漏掉两种情况：

    - 浏览器报 MUSIC(1)，同播的表外音乐播放器（LX Music 报 Unknown）优先级更低
      → 赢家是浏览器 → 否决后整条链路空掉，音乐明明在播却"没有歌"；
    - 两个 MUSIC 会话（浏览器 + 原生播放器）时，赢家随枚举顺序摇摆。

    规则（候选集合内，按优先级）：
    1. 有候选在播 → 先按媒体类型分层（音乐 > 未上报），层内优先当前跟踪的
       候选，否则取该层里枚举顺序最靠前的那个；
    2. 没有候选在播 → 留在被跟踪的候选上（暂停时不跳走）；
    3. 否则取第一个候选（保持旧兼容行为）；
    4. 一个候选都没有（只有视频/被否决的浏览器会话）→ ``None``（与"没有会话"
       同一口径：不显示、不取词、不唱歌）。
    """
    accepted = [s for s in snapshots if s.allows_singing(allow_browser)]
    if not accepted:
        return None
    wanted = normalized_app_id(tracked_app_id)
    tracked = None
    if wanted:
        tracked = next(
            (s for s in accepted if normalized_app_id(s.app_id) == wanted), None
        )
    playing = [s for s in accepted if s.playing]
    if playing:
        best = min(s.type_rank for s in playing)
        tier = [s for s in playing if s.type_rank == best]
        if tracked is not None and tracked.playing and tracked.type_rank == best:
            return tracked
        return tier[0]
    if tracked is not None:
        return tracked
    return accepted[0]


def _read_foobar_playback(allow_foobar: bool, title_format: str) -> Playback | None:
    """foobar2000 补充源：读它的主窗口标题，包成一次采样结果。

    三条刻意的口径：

    - **不抢 SMTC 的位**：只有调用方在"SMTC 没有在播会话"时才走到这里
      （见 :func:`_read_async`）；返回的 ``app_id`` 是
      :data:`foobar_playing.FOOBAR_APP_ID`，调用方据此不覆盖 SMTC 的跟踪位。
    - **进度恒为 ``None``**：窗口标题里没有时间轴，交给调用方按本地时钟推算
      （与网易云同一个口径，手动对齐入口照常可用）。
    - **只当"在播"**：窗口标题分辨不出播放状态，能读到的曲目按在播处理；用户
      开着 foobar 但没在播时可能读到上一首——这是本能力的已知局限，所以开关
      默认关、文案里如实写明。
    """
    if not allow_foobar:
        return None
    parsed = foobar_playing.read_track(str(title_format or ""))
    if parsed is None:
        return None
    title, artist = parsed
    return Playback(
        track=Track(title=title, artist=artist, playing=True),
        position=None,
        updated_at=time.monotonic(),
        app_id=foobar_playing.FOOBAR_APP_ID,
    )


def _read_lx_playback(allow_lxmusic: bool = True) -> Playback | None:
    """洛雪音乐（LX Music）优先源：走它自己的开放 API 拿真实进度。

    与 foobar 的补充源不同，这里**优先于 SMTC**：LX 在系统媒体控制里报
    Unknown、且经常不给时间轴，歌词只能按"检测到切歌的时刻"估算；而它的
    ``/status`` 直接给 ``progress`` 秒数，与播放器内部状态同源。

    ``allow_lxmusic=False``（设置页「洛雪音乐歌词」关闭）时整条源不参与，行为
    与引入本能力之前逐位一致。

    没在播（暂停/停止）、开放 API 没开、端口不通时一律返回 ``None``——链路
    照旧走 SMTC，不改变原有任何行为。异常绝不外抛（歌词是锦上添花）。
    """
    if not allow_lxmusic:
        return None
    try:
        return lxmusic_playing.read_playback()
    except Exception:  # noqa: BLE001 - 第三方接口异常不得影响采样
        return None


async def _read_async(
    tracked_app_id: str | None = None,
    *,
    allow_browser: bool = True,
    allow_foobar: bool = False,
    foobar_title_format: str = "",
    allow_lxmusic: bool = True,
) -> Playback | None:
    # 洛雪优先：拿到真实进度就不必再问 SMTC（见 _read_lx_playback）。
    # 放在 WinRT 之前，没装 winrt 的机器上这项能力也照常可用。
    lx_playback = _read_lx_playback(allow_lxmusic)
    if lx_playback is not None:
        return lx_playback
    manager_cls = _import_winrt()
    if manager_cls is None:
        # 没有 WinRT（非 Windows / 未装可选依赖）：SMTC 无从查起。foobar 的窗口
        # 标题源不依赖 WinRT，仍可作补充——两者都不行时才返回 None。
        return _read_foobar_playback(allow_foobar, foobar_title_format)
    manager = await manager_cls.request_async()
    snapshot = _pick_singing_session(
        _session_snapshots(manager), tracked_app_id, allow_browser
    )
    # foobar 是**补充源**：SMTC 有在播会话时绝不问它（见 _read_foobar_playback）。
    if snapshot is None or not snapshot.playing:
        fallback = _read_foobar_playback(allow_foobar, foobar_title_format)
        if fallback is not None:
            return fallback
    if snapshot is None:
        # 没有可接受的候选：既可能是"没有会话"，也可能是"只有在播的视频/被否决
        # 的浏览器会话"。两种都与"无标题"同一口径——不显示、不取词、不唱歌。
        return None
    session = snapshot.session

    # 读字段要各自兜住：session 选取与读取之间可能失效，或某个播放器给的
    # 时间轴字段异常（end_time 为 None 等）。让异常冒到最外层会被当成
    # "播放器没了"，控制器于是清空歌词、下一拍重新取词（三次网络请求）。
    try:
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        end_time = float(timeline.end_time.total_seconds())
        raw_position = float(timeline.position.total_seconds())
    except Exception:
        return None

    # 播放状态来自选择时那个快照（本会话本次采样只读一次 PlaybackInfo）。
    # 读不到状态时按 HEAD 的字段兜底口径处理：整次采样作废，而不是当成"暂停"。
    if snapshot.status is None:
        return None

    title = str(getattr(props, "title", "") or "").strip()
    artist = str(getattr(props, "artist", "") or "").strip()
    if not title and not artist:
        return None

    # 播放器不上报时间轴时 end_time 与 position 恒为 0，此时标记为"无进度"。
    has_timeline = end_time > _MIN_VALID_DURATION
    is_playing = snapshot.playing

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
        app_id=snapshot.app_id,
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


_LOOP_LOCAL = threading.local()


def _run_sync(coro):
    """在调用线程的**常驻**事件循环里跑协程。

    每秒一拍的歌词/会话采样如果各走 ``asyncio.run()``，每拍都要新建并销毁一个
    事件循环（新建循环的开销实测是这条链的大头——py-spy 火焰图上
    ``new_event_loop``/线程引导占了采样线程的近一半）。循环按线程常驻
    （thread-local），WinRT 调用始终落在同一个循环上；线程退出时由解释器回收。
    主线程依旧不许调（SMTC 查询实测会在主线程永久阻塞，见 ``music_lyric_controller``
    的采样线程说明）——本函数不改变那条纪律，只改变循环的寿命。
    """
    loop = getattr(_LOOP_LOCAL, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _LOOP_LOCAL.loop = loop
    return loop.run_until_complete(coro)


def skip_track(direction: str = "next") -> bool:
    """切换到下一首/上一首（``direction`` 为 ``"next"`` 或 ``"previous"``）。

    返回是否成功；不支持切歌的播放器、或没有会话时返回 ``False``。
    与 :func:`get_now_playing` 一样绝不抛异常。
    """
    if sys.platform != "win32":
        return False
    try:
        return _run_sync(_skip_async(direction == "previous"))
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
        return _run_sync(_play_pause_async())
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
        return _run_sync(_play_session_async(exe_name))
    except Exception:
        return False


def get_now_playing(
    tracked_app_id: str | None = None,
    *,
    allow_browser: bool = True,
    allow_foobar: bool = False,
    foobar_title_format: str = "",
    allow_lxmusic: bool = True,
) -> Playback | None:
    """返回当前播放信息；无播放器/无会话/任何异常时返回 ``None``。

    ``tracked_app_id`` 是上一次采样得到的 ``Playback.app_id``，用于让会话选取
    粘住当前跟踪的播放器（见 :func:`_pick_playing_session`）。

    ``allow_browser=False``（设置页「浏览器媒体会话参与歌词」关闭）时，来自
    已知浏览器的会话按"不是歌"处理并返回 ``None``——实测浏览器把网页视频也
    上报成音乐类型，类型过滤挡不住它，只能按会话来源否决。默认 ``True`` 保持
    旧行为（采样层不替调用方做策略决定，由持有配置的调用方显式传入）。

    ``allow_foobar=True``（设置页「foobar2000 歌词」开启）时，SMTC **没有在播
    会话**的情况下读 foobar2000 主窗口标题作为补充源（它默认不注册 SMTC，见
    ``pet/foobar_playing.py``）。``foobar_title_format`` 是用户在 foobar 里配置的
    窗口标题格式串，空串 = 自动（不猜歌名/歌手顺序）。

    ``allow_lxmusic=False``（设置页「洛雪音乐歌词」关闭）时，不读洛雪音乐的本地
    开放 API，直接走系统媒体控制那条路（见 ``pet/music/lxmusic.py``）。

    本函数绝不抛异常——歌词只是锦上添花，不能因为第三方接口异常影响桌宠本体。
    """
    if sys.platform != "win32":
        return None
    try:
        return _run_sync(
            _read_async(
                tracked_app_id,
                allow_browser=allow_browser,
                allow_foobar=allow_foobar,
                foobar_title_format=foobar_title_format,
                allow_lxmusic=allow_lxmusic,
            )
        )
    except Exception:
        return None


# --------------------------------------------------------- 唱歌链路会话探针
#
# ``music_detect.is_music_playing`` 跑在 Qt 主线程（音乐唱歌定时器槽），而查
# SMTC 走 ``_run_sync()``（线程常驻事件循环）——主线程同步查实测会永久阻塞窗口（同类教训见
# ``music_lyric_controller`` 的采样线程说明）。所以"当前会话是什么"由下面这条
# 常驻后台线程产出，主线程只读缓存。
#
# 缓存的是**会话事实**（有没有会话 / 类型 / 是不是浏览器）而不是最终判定：
# 判定还取决于设置里的浏览器开关，缓事实能让开关一改下一拍就生效（不必等
# 探针重采），规则也只有一份实现（:func:`_allows_singing_from_facts`）。

# 采样间隔：比唱歌轮询（1s）宽松，兼顾时效与开销。
SESSION_PROBE_TTL_SECONDS = 2.0
# 单次采样超时：正常采样实测 ~5ms，给三个数量级的宽余。没有它，一次永不返回
# 的 WinRT 请求会把探针线程永久钉在采样里（实测复现：停止询问后线程也不退出，
# 只能等进程结束），"空闲自行退出"的设计就作废了。
SESSION_PROBE_TIMEOUT_SECONDS = 3.0
# 缓存超过该年龄即视为过期（按"无数据"处理 → 放行）。
SESSION_VERDICT_STALE_SECONDS = SESSION_PROBE_TTL_SECONDS * 2
# 无人询问超过该时长，探针线程自行退出并清缓存：功能关掉后不留常驻轮询。
SESSION_PROBE_IDLE_EXIT_SECONDS = 8.0

# 会话事实表：每个会话一项 ``(媒体类型, 是否已知浏览器, 是否在播)``。
# 空元组 = 采样到了、但一个会话都没有；``None`` = 还没有采样结果（两回事）。
_NO_SESSION_FACTS: tuple = ()

_probe_lock = threading.Lock()
_probe_thread: threading.Thread | None = None
_probe_wake: threading.Event | None = None
_probe_stop: threading.Event | None = None
_session_facts: tuple | None = None
_session_facts_at = 0.0
_last_asked = 0.0


def _allows_singing_from_facts(facts: tuple, allow_browser: bool) -> bool:
    """动画门判定：当前**在播**的会话里至少有一个被唱歌链路接受。

    - 没有任何会话、或没有在播会话 → 放行（旧行为）：此时出声的可能是游戏、
      或不上报 SMTC 的播放器，按会话否决会误伤；
    - 有在播会话但**全部**被否决（视频 / 开关关时的浏览器）→ 否决；
    - 有在播且可接受的（音乐、Unknown、表外应用）→ 放行。

    这里逐会话判定而不是"先挑一个赢家再判"：浏览器报 MUSIC、同播的表外音乐
    播放器报 Unknown 时，只判赢家会把"有歌在播"错判成"没有歌"。
    """
    playing = [
        (media_type, browser)
        for media_type, browser, is_playing in facts
        if is_playing
    ]
    if not playing:
        return True
    return any(
        _source_allows_singing(media_type, browser, allow_browser)
        for media_type, browser in playing
    )


async def _facts_async() -> tuple:
    """采一次全部会话，产出 :data:`_NO_SESSION_FACTS` 形状的事实表。"""
    manager_cls = _import_winrt()
    if manager_cls is None:
        return _NO_SESSION_FACTS  # 无 WinRT（非 Windows / 未装依赖）：无从否决
    manager = await manager_cls.request_async()
    # 每个会话只读一次 PlaybackInfo（见 _SessionSnapshot 的说明）。
    return tuple(
        (s.media_type, s.browser, s.playing) for s in _session_snapshots(manager)
    )


async def _facts_async_bounded() -> tuple:
    """带超时的事实采样（见 :data:`SESSION_PROBE_TIMEOUT_SECONDS`）。"""
    return await asyncio.wait_for(_facts_async(), SESSION_PROBE_TIMEOUT_SECONDS)


def refresh_session_probe_once() -> tuple:
    """立即采一次并刷新缓存；返回逐会话事实表。

    探针线程每拍调用它；测试可同步调用以确定性地填充缓存（不必等线程）。
    任何异常（含采样超时）都按"没有会话"处理——门故障绝不误伤（无会话=放行），
    且**必须**让控制权回到探针循环，否则挂起的采样会把线程钉死。
    """
    global _session_facts, _session_facts_at
    try:
        facts = tuple(_run_sync(_facts_async_bounded()))
    except Exception:
        facts = _NO_SESSION_FACTS
    with _probe_lock:
        _session_facts = facts
        _session_facts_at = time.monotonic()
    return facts


def _probe_loop(wake: threading.Event, stop: threading.Event) -> None:
    """探针线程主体：等一拍 → 看还有人问吗 → 采样。空闲即自行退出。"""
    global _probe_thread, _probe_wake, _probe_stop, _session_facts, _session_facts_at
    try:
        while not stop.is_set():
            if wake.wait(SESSION_PROBE_TTL_SECONDS):
                wake.clear()
            if stop.is_set():
                break
            with _probe_lock:
                idle_seconds = time.monotonic() - _last_asked
            if idle_seconds > SESSION_PROBE_IDLE_EXIT_SECONDS:
                break
            refresh_session_probe_once()
    finally:
        with _probe_lock:
            # 只清自己的引用/缓存：别把后来重启的线程的现场抹掉。
            if _probe_thread is threading.current_thread():
                _probe_thread = None
                _probe_wake = None
                _probe_stop = None
                _session_facts = None
                _session_facts_at = 0.0


def _ensure_session_probe() -> None:
    """确保有一条在跑的探针线程（幂等）。

    探针只负责采"当前会话是什么"，不关心浏览器开关——开关在读取判定时才参与，
    所以这里没有配置参数，开关改动也就不需要重启线程。
    """
    global _probe_thread, _probe_wake, _probe_stop
    with _probe_lock:
        if _probe_thread is not None and _probe_thread.is_alive():
            return
        wake = threading.Event()
        stop = threading.Event()
        thread = threading.Thread(
            target=_probe_loop,
            args=(wake, stop),
            name="smtc-session-probe",
            daemon=True,
        )
        _probe_wake = wake
        _probe_stop = stop
        _probe_thread = thread
    thread.start()


def song_session_accepted(allow_browser: bool = True) -> bool | None:
    """当前 SMTC 会话是否被唱歌链路接受（**主线程安全**，只读缓存）。

    返回 ``None`` 表示还没有会话事实可用——调用方按**放行**处理（保持旧行为，
    不因门故障误伤）；``False`` 才是明确否决（视频 / 开关关时的浏览器）。

    首次调用会拉起后台探针线程，因此第一拍拿到的通常是 ``None``。
    """
    global _last_asked
    allow_browser = bool(allow_browser)
    now = time.monotonic()
    with _probe_lock:
        _last_asked = now
        facts = _session_facts
        fresh = (
            facts is not None
            and (now - _session_facts_at) <= SESSION_VERDICT_STALE_SECONDS
        )
    if fresh:
        return _allows_singing_from_facts(facts, allow_browser)
    # 缓存过期/尚未产出：先拉起或复用探针，本拍按"无数据"放行。
    _ensure_session_probe()
    return None


def reset_session_probe() -> None:
    """停掉探针线程并清空缓存（测试用；产品路径不需要）。"""
    global _probe_thread, _probe_wake, _probe_stop, _session_facts, _session_facts_at
    with _probe_lock:
        wake, stop, thread = _probe_wake, _probe_stop, _probe_thread
    if stop is not None:
        stop.set()
    if wake is not None:
        wake.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=1.0)
    with _probe_lock:
        _probe_thread = None
        _probe_wake = None
        _probe_stop = None
        _session_facts = None
        _session_facts_at = 0.0
