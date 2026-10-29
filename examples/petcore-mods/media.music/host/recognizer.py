"""把多来源的证据合并成一次"现在在放什么"的判定（**纯函数、零 I/O、零 Qt**）。

## 为什么要三源

旧实现只有一条源：系统媒体会话（SMTC，``now_playing.py``）。它有两个盲区，
两个都在实机上复现过：

1. **不注册 SMTC 的播放器完全看不到**（foobar2000 默认如此，AIMP / Winamp 同理），
   用户放着歌、桌宠却在待机；
2. **SMTC 说"在播"不等于真的在出声**：播放器被系统音量静音、或会话状态已经
   停下而播放器没来得及更新元数据时，旧实现照样唱歌。

补的两条证据：

- **音频会话**（``audio_sessions``）：该进程的 WASAPI 会话是 Active 还是 Inactive、
  有没有静音。它回答"**这个进程在不在出声**"，是判定"在播 / 暂停"的裁判。
- **播放器窗口标题**（``window_source`` + ``title_clean``）：只对
  :data:`players.KNOWN_MUSIC_EXES` 或用户配置过的可执行名生效，且在**SMTC 没有
  在播会话时**才去问。它回答"**在放哪一首**"。

## 三条忍住不猜的纪律

- **音频会话里没有它，就交回 SMTC 的播放状态**（蓝牙、独占模式、虚拟声卡都会
  让会话不可见）。此时按 SMTC 判，而不是判"暂停"——门故障不许误伤。
- **只有"任意进程在出声"不构成音乐**：游戏、语音、系统提示音都会出声。窗口源
  必须同时命中"已登记播放器"与"该进程音频 Active"才认，否则明确判不在播
  （这是对"看视频被当成歌"的收紧；旧实现只看 SMTC，本来也不给游戏唱）。
- **窗口标题解析不出歌名就不给曲目**：可以只唱歌、不取词（``track=None``），
  但绝不把窗口标题原样当歌名塞进歌词链路。

判定结果里的 ``reasons`` / ``evidence`` 是给用户的**诊断文本**（设置页的
「识别诊断」与 ``scripts/music_probe.py`` 都读它），所以每条否决都要说清原因。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 判定状态：在播 / 暂停 / 不在播（含"没有播放器"）/ 读失败。
STATUS_PLAYING = "playing"
STATUS_PAUSED = "paused"
STATUS_ABSENT = "absent"
STATUS_ERROR = "error"

SOURCE_SMTC = "smtc"
SOURCE_WINDOW = "window"
SOURCE_NONE = "none"

#: 判定置信度：``confirmed`` = 有音频证据印证；``assumed`` = 退回 SMTC 状态。
CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_ASSUMED = "assumed"

#: 「真在出声」的峰值下限（WASAPI ``GetPeakValue``，0..1）。播放器暂停后会话常
#: 仍挂 ``active``，此时峰值恒为 0；用它把"残留 Active"与"SMTC 状态落后但真在放"
#: 分开。定得足够低：极安静段落里只要有一点波形就算在放，宁可漏判一拍，下一拍自愈。
AUDIBLE_PEAK = 0.005


@dataclass(frozen=True, slots=True)
class Track:
    """一首曲目的元信息（与来源无关的统一形态）。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    position: float | None = None

    def key(self) -> tuple[str, str]:
        """判断"换没换歌"的标识（大小写/空白归一）。"""
        return (self.title.strip().lower(), self.artist.strip().lower())

    def label(self) -> str:
        title = self.title.strip()
        artist = self.artist.strip()
        if title and artist:
            return f"{artist} - {title}"
        return title or artist or "未知曲目"


@dataclass(frozen=True, slots=True)
class Decision:
    """一次采样的最终判定；``reasons`` / ``evidence`` 只用于展示。"""

    status: str = STATUS_ABSENT
    source: str = SOURCE_NONE
    track: Track | None = None
    app_id: str = ""
    exe: str = ""
    confidence: str = CONFIDENCE_ASSUMED
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    #: 这次「暂停」来自**峰值门限**（同播放器窗口源说在播、但这一拍没声音）。
    #: 调用方可以按迟滞保留上一拍：单拍静音不该立刻收掉播放（淡出/间奏）。
    soft_paused: bool = False

    @property
    def playing(self) -> bool:
        return self.status == STATUS_PLAYING

    @property
    def paused(self) -> bool:
        return self.status == STATUS_PAUSED

    @property
    def readable(self) -> bool:
        """这次采样是否给出了可用结论（``error`` 表示"这次没读到"，不是"没在放"）。"""
        return self.status != STATUS_ERROR


def _basename(value: str) -> str:
    """取路径/名字的可执行文件名部分（小写、去空白）。"""
    return str(value or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1].strip()


def normalized_exe(value: str | None) -> str:
    """可执行名归一：取 basename、小写、**去掉** ``.exe``（用于与 SMTC app_id 比对）。

    取 basename 是必须的：有些 Win32 播放器把 app_id 报成完整安装路径
    （`C:\\Program Files\\...\\cloudmusic.exe`），只做 ``endswith`` 会让
    "静音/会话状态"这条证据永远匹配不上，还会把安装路径带进给用户看的诊断文本。
    """
    text = _basename(value)
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def exe_name(app_id: str | None) -> str:
    """SMTC app_id / 路径 → 小写可执行名（含 ``.exe``，不含目录）。

    ``cloudmusic.exe``、``Chrome`` 与完整路径三种形状都实测/按文档可能出现；UWP 的
    AUMID（``SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify``）没有可执行名，此时返回
    空串——调用方据此走"音频表里没有它 → 按 SMTC 判"的保守分支。
    """
    text = str(app_id or "").strip().lower()
    if not text:
        return ""
    if "!" in text or "_" in text:
        return ""
    name = _basename(text)
    if not name:
        return ""
    return name if name.endswith(".exe") else f"{name}.exe"


def _audio_for(rows, exe: str):
    """在这批音频会话里找 ``exe`` 的那一条（同一 exe 多条时取"最活跃"的）。"""
    wanted = normalized_exe(exe)
    if not wanted:
        return None
    matched = [row for row in rows if normalized_exe(getattr(row, "exe", "")) == wanted]
    if not matched:
        return None
    for row in matched:
        if getattr(row, "state", "") == "active" and not bool(getattr(row, "muted", False)):
            return row
    return matched[0]


def _default_parse(title: str, exe: str, fmt: str):
    """窗口标题 → ``(歌名, 歌手)``：默认走 ``title_clean``（惰性 import，便于替换）。"""
    from . import title_clean

    return title_clean.parse_window_title(title, exe, fmt)


def _note(reasons: list[str], message: str) -> None:
    """去重地记一条理由：多台播放器被同一条规则挡下时别刷屏。"""
    if message not in reasons:
        reasons.append(message)


def _audible(rows, exe: str) -> bool:
    """该可执行名的音频会话是否**真在出声**（Active + 未静音 + peak ≥ 阈值）。

    这是"残留 Active"与"SMTC 状态落后但真在放"之间唯一的客观区别：暂停的播放器
    会话峰值恒为 0。读不到峰值时按"没在出声"处理（宁可少判一拍，也不凭一个认不出
    的证据让桌宠唱起来）。
    """
    audio = _audio_for(rows, exe)
    if audio is None:
        return False
    if str(getattr(audio, "state", "") or "") != "active":
        return False
    if bool(getattr(audio, "muted", False)):
        return False
    try:
        peak = float(getattr(audio, "peak", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    return peak >= AUDIBLE_PEAK


def _window_decision(
    sample,
    *,
    allowed_ids,
    title_format: str,
    parse,
    reasons: list[str],
    evidence: list[str],
) -> Decision | None:
    """窗口标题源：只用"已登记播放器 + 该进程真有音频会话"的窗口。"""
    best = None
    for window in sample.windows:
        exe = str(getattr(window, "exe", "") or "")
        if not exe:
            continue
        if allowed_ids and normalized_exe(exe) not in allowed_ids:
            _note(reasons, f"{exe} 不在识别范围（设置 → 扩展管理 → 音乐 → 识别范围）")
            continue
        audio = _audio_for(sample.audio, exe)
        playing = True
        if audio is not None:
            muted = bool(getattr(audio, "muted", False))
            state = str(getattr(audio, "state", "") or "")
            playing = (state == "active") and not muted
            evidence.append(
                f"{exe} 音频会话 state={state or '?'} mute={'yes' if muted else 'no'} "
                f"peak={float(getattr(audio, 'peak', 0.0) or 0.0):.2f}"
            )
        elif sample.audio_available:
            # 播放器没有音频会话：可能是独占模式，也可能是"开着没在放"。
            # 窗口标题看不出播放状态，所以这里**不认它在播**——这正是主分支
            # foobar 源「停止播放后仍显示上一首」的根因。
            _note(reasons, f"{exe} 没有音频会话（可能没在播放，或用了独占输出）")
            continue
        parsed = None
        if playing:
            try:
                parsed = parse(str(getattr(window, "title", "") or ""), exe, title_format)
            except Exception:
                parsed = None
        track = None
        if parsed:
            title, artist = (str(parsed[0] or ""), str(parsed[1] or ""))
            if artist.strip() and artist.strip() == title.strip():
                # 顺序不可知时解析器会把整条标题同时当作歌名与歌手（那是给**搜索**用的
                # 口径，两种顺序都能匹配）。显示只需要一次：气泡里贴两遍同一串很怪。
                artist = ""
            track = Track(title=title, artist=artist)
        candidate = Decision(
            status=STATUS_PLAYING if playing else STATUS_PAUSED,
            source=SOURCE_WINDOW,
            track=track,
            app_id=exe,
            exe=exe,
            confidence=CONFIDENCE_CONFIRMED if audio is not None else CONFIDENCE_ASSUMED,
            reasons=tuple(reasons),
            evidence=tuple(evidence),
        )
        if playing and track is not None:
            return candidate
        if best is None:
            best = candidate
    return best


def decide(
    sample,
    *,
    allowed_ids: frozenset[str] | None = None,
    title_format: str = "",
    parse_window=None,
) -> Decision:
    """一次采样 → 一次判定。

    ``sample`` 需要四个属性（都允许为空，缺省按"没有该来源"处理）：
    ``playback``（``now_playing.Playback | None``）、``smtc_reason``、
    ``smtc_error``、``audio``、``audio_available``、``windows``。

    ``allowed_ids`` 是「只识别指定播放器」的归一识别名集合（空集合 = 不过滤）；
    它对**三个来源统一生效**（契约见 ``docs/PETCORE-MOD-FORMAT.md`` §13.3）。
    """
    reasons: list[str] = []
    evidence: list[str] = []
    soft_pause = False
    allowed = frozenset(allowed_ids or ())
    parse = parse_window or _default_parse

    if bool(getattr(sample, "smtc_error", False)) and not bool(
        getattr(sample, "smtc_degraded", False)
    ):
        # 读失败 ≠ 没在放：调用方必须保留上一拍的状态（歌词/动画都不许动）。
        # **持续**读不到（``smtc_degraded``：读取超时/媒体服务卡死）不在此列——那种
        # 情况要退到窗口标题源，否则一次卡死就把识别永久钉在最后一首歌上。
        reason = str(getattr(sample, "smtc_reason", "") or "读取系统媒体信息失败。")
        return Decision(status=STATUS_ERROR, reasons=(reason,))

    playback = getattr(sample, "playback", None)
    window_available = bool(getattr(sample, "window_available", True))
    if playback is not None and not bool(getattr(playback, "playing", False)) and window_available:
        # SMTC 里挂着一个**暂停**的会话、同时另一台不注册 SMTC 的播放器在出声：
        # 这是"挂着的客户端 + foobar 在放"的常见姿势。「在播」的证据优先于「暂停」
        # 的证据，否则窗口源白采一遍、诊断里证据齐全却判暂停。
        windowed = _window_decision(
            sample,
            allowed_ids=allowed,
            title_format=title_format,
            parse=parse,
            reasons=reasons,
            evidence=evidence,
        )
        # **同一台播放器**的窗口源要额外一条证据才翻盘：暂停后 WASAPI 会话常仍挂
        # ``active``（音频会话只报事实、不判时间上下文），只看状态会把「暂停」翻成
        # 「在播」——本地时钟照走、歌词每两秒继续换一句。反过来，网易云的 SMTC 状态
        # 会落后于实际播放（停在 paused 而音频在响）：只认状态又会把「在播」永久判成
        # 「暂停」= 识别不到。两者唯一的客观区别是**有没有声音**（peak）。
        paused_exe = normalized_exe(exe_name(getattr(playback, "app_id", "")))
        window_exe = normalized_exe(str(getattr(windowed, "exe", "") or ""))
        same_player = bool(paused_exe) and window_exe == paused_exe
        if windowed is not None and windowed.status == STATUS_PLAYING:
            if not same_player:
                return Decision(
                    status=windowed.status,
                    source=windowed.source,
                    track=windowed.track,
                    app_id=windowed.app_id,
                    exe=windowed.exe,
                    confidence=windowed.confidence,
                    reasons=(
                        f"系统媒体会话处于暂停（{getattr(playback, 'app_id', '') or '未知播放器'}）；"
                        "以窗口标题源为准",
                        *windowed.reasons,
                    ),
                    evidence=windowed.evidence,
                )
            if _audible(getattr(sample, "audio", ()) or (), windowed.exe):
                return Decision(
                    status=windowed.status,
                    source=windowed.source,
                    track=windowed.track,
                    app_id=windowed.app_id,
                    exe=windowed.exe,
                    confidence=windowed.confidence,
                    reasons=(
                        f"{window_exe or '该播放器'} 的系统媒体会话停在暂停，但音频会话仍在出声"
                        "（peak 高于静音门限）；以窗口标题源为准",
                        *windowed.reasons,
                    ),
                    evidence=windowed.evidence,
                )
            _note(
                reasons,
                f"{window_exe or '该播放器'} 与暂停中的系统媒体会话是同一台播放器，"
                "且音频会话没有声音（peak 低于静音门限）：残留的 Active 会话不算在播",
            )
            # 这条「暂停」只值一拍：调用方按迟滞保留上一拍（见 SOFT_PAUSE_CONFIRM_TICKS）。
            soft_pause = True

    if playback is not None:
        exe = exe_name(getattr(playback, "app_id", ""))
        audio = _audio_for(getattr(sample, "audio", ()) or (), exe)
        muted = False
        audio_state = ""
        if audio is not None:
            muted = bool(getattr(audio, "muted", False))
            audio_state = str(getattr(audio, "state", "") or "")
            evidence.append(
                f"{exe} 音频会话 state={audio_state or '?'} mute={'yes' if muted else 'no'} "
                f"peak={float(getattr(audio, 'peak', 0.0) or 0.0):.2f}"
            )
        elif bool(getattr(sample, "audio_available", True)) and exe:
            evidence.append(f"{exe} 不在音频会话表（蓝牙/独占/虚拟声卡）：按系统媒体状态判断")
        else:
            evidence.append("没有可用的音频会话证据：按系统媒体状态判断")

        playing = bool(getattr(playback, "playing", False))
        if muted:
            reasons.append(f"{exe or '当前播放器'} 被静音：不算在播")
            playing = False
        elif playing and audio_state and audio_state != "active":
            reasons.append(f"{exe} 的音频会话是 {audio_state}：按暂停处理")
            playing = False

        track = Track(
            title=str(getattr(playback, "title", "") or ""),
            artist=str(getattr(playback, "artist", "") or ""),
            album=str(getattr(playback, "album", "") or ""),
            duration=float(getattr(playback, "duration", 0.0) or 0.0),
            position=getattr(playback, "position", None),
        )
        return Decision(
            status=STATUS_PLAYING if playing else STATUS_PAUSED,
            source=SOURCE_SMTC,
            track=track,
            app_id=str(getattr(playback, "app_id", "") or ""),
            exe=exe,
            confidence=CONFIDENCE_CONFIRMED if audio is not None else CONFIDENCE_ASSUMED,
            reasons=tuple(reasons),
            evidence=tuple(evidence),
            soft_paused=soft_pause and not playing,
        )

    smtc_reason = str(getattr(sample, "smtc_reason", "") or "")
    if smtc_reason:
        reasons.append(smtc_reason)
    if bool(getattr(sample, "window_available", True)):
        windowed = _window_decision(
            sample,
            allowed_ids=allowed,
            title_format=title_format,
            parse=parse,
            reasons=reasons,
            evidence=evidence,
        )
        if windowed is not None:
            return windowed
    else:
        reasons.append("播放器窗口标题源不可用（仅 Windows 可用）")

    if not reasons:
        reasons.append("没有正在播放的播放器。")
    return Decision(status=STATUS_ABSENT, reasons=tuple(reasons), evidence=tuple(evidence))
