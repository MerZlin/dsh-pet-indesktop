"""歌词：LRC 解析、两源取词、磁盘缓存（纯逻辑 + ``HttpPort``，无 Qt）。

口径沿用主分支 ``pet/music_lyric.py`` 的结论（本模块不 import ``pet.*``，只搬逻辑）：

- **两个公开源**，按匹配质量排序：网易云（中文曲库，搜索 + 歌词两跳）→ 酷狗
  （同样两跳）。命中即返回；**失败不重试**（一首歌最多一轮）。
- **首选直连**：``HttpRequest.prefer_direct=True``。2026-09-22 实机事故里系统代理把
  三个歌词源拖到 20~41 秒，全部超过超时，于是每首未缓存曲目都以「0 行」收场。
  路线切换（最多一次、只对传输失败或 500/502/504）由 ``HttpPort`` 实现，mod 只声明
  「国内源直连」；404/429/503 与带 ``Retry-After`` 的响应不会换路，也不在这里重发。
- **响应体 ≤ 2 MB**：``HttpRequest.max_bytes`` 由 core 强制执行。
- **纯音乐**：平台对没有词的曲子会给一句占位文案（``纯音乐，请欣赏`` /
  ``instrumental``），必须识别出来，否则会被当成唯一一句歌词、还会放起唱歌动画。
- **缓存**：``storage.data_dir("media.music")/lyrics`` 下按 ``sha1(title|artist)``
  一到一曲一个 JSON，条目数超上限按修改时间淘汰最旧的。
- **可取消**：``fetch_lyrics(cancelled=...)`` 在**每次发请求之前**问一次回调，
  切歌/停止时被顶掉的那一轮立刻收手（``LyricsCancelled``），不再把剩下的源和
  HTTP 跳数跑完。取消不是"这首没有歌词"：调用方不许把它记成失败。
- **不写日志**：歌词 URL、代理地址、响应体一律不进日志（调用方也没给日志出口）。

``parse_lrc`` / ``line_at`` / ``looks_instrumental`` 不依赖端口，可独立单测。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from petcore.contracts.http import HttpError, HttpRequest

#: 单次取词的默认预算（秒）；两个源共享同一份截止时刻，不会叠加成两倍。
DEFAULT_TIMEOUT = 8.0
#: 单个响应体的硬上限（字节）。
MAX_BYTES = 2_000_000
#: 磁盘缓存条目上限的默认值。
CACHE_LIMIT = 80
#: 剩余预算低于该值就不再发请求（注定来不及的那发只会白占连接）。
MIN_ATTEMPT_BUDGET = 0.25
#: 缓存格式版本：解析口径变更时可据此作废旧条目。
#: v2（2026-10-26）：带匹配分 ``score``（诊断要显示"这个源多少分命中"）。
CACHE_VERSION = 2

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 形如 [mm:ss.xx] / [mm:ss:xx] / [mm:ss]，可能一行多个。
_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 只有标签、没有时间戳的行，如 [ti:歌名] —— 按 LRC 规范跳过。
_TAG_RE = re.compile(r"^\[[a-zA-Z#]+:[^\]]*\]\s*$")
# [offset:±毫秒] 需要叠加到所有时间点上。
_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)

# 多歌手分隔符：酷狗报「、」、歌词源多写「/」，feat./ft. 见英文曲库。
_ARTIST_SEP_RE = re.compile(r"\s*(?:[、/／&×,，;；]|\bfeat\b\.?|\bft\b\.?)\s*", re.IGNORECASE)

# 各平台给"纯音乐"的占位文案。只匹配**整句占位语**，不匹配裸关键词：
# 真歌词里完全可能出现"纯音乐"三个字。
_INSTRUMENTAL_MARKERS = (
    "纯音乐，请欣赏",
    "纯音乐,请欣赏",
    "没有填词的纯音乐",
    "此歌曲为没有填词",
    "该歌曲为纯音乐",
    "暂无歌词",
    "无歌词",
    "instrumental",
    "no lyrics",
)


@dataclass(frozen=True, slots=True)
class LyricLine:
    """一行带时间戳的歌词。"""

    at: float
    text: str


@dataclass(frozen=True, slots=True)
class Lyrics:
    """一次取词的结果。

    ``instrumental=True`` 表示**纯音乐**：曲目本身没有歌词（配乐 / OST / 演奏曲）。
    调用方据此不显示歌词、也不播唱歌动画。``source`` 是命中的源名（诊断用）。
    """

    lines: tuple[LyricLine, ...] = ()
    instrumental: bool = False
    source: str = ""
    #: 命中时该候选的匹配分（0..1）；诊断面板会显示它，用户报「歌词不对」时能一眼看出。
    score: float = 0.0

    def __bool__(self) -> bool:
        # 纯音乐（无行、instrumental=True）也是有效结果，否则缓存永远读不中。
        return bool(self.lines) or self.instrumental


class LyricsError(RuntimeError):
    """两个源都没取到可用的歌词；``str()`` 是给用户看的原因（点名每个源）。"""


class LyricsCancelled(LyricsError):
    """取词被取消（被新一轮顶掉 / 运行时停止）。

    刻意继承 ``LyricsError``：老的调用方把它当"这次没拿到"也不会炸；但**它不
    等于"这首没有歌词"**，运行时不许据此记失败、冷却或负缓存。
    """


#: ``cancelled`` 回调的形态：返回 True 表示这一轮不要再发请求了。
Cancelled = Callable[[], bool]


def _abort_if_cancelled(cancelled: "Cancelled | None") -> None:
    """发请求之前问一次；被取消就抛 :class:`LyricsCancelled`（不吞、不记失败）。"""
    if cancelled is not None and cancelled():
        raise LyricsCancelled("取词已取消。")


# ---------------------------------------------------------------- LRC 解析


def _parse_stamp(minutes: str, seconds: str, frac: str | None) -> float:
    value = int(minutes) * 60 + int(seconds)
    if frac:
        # 两位是厘秒、三位是毫秒，一位按十分之一秒处理。
        value += int(frac) / (10 ** len(frac))
    return value


def parse_lrc(text: str) -> tuple[LyricLine, ...]:
    """把 LRC 文本解析成按时间排序的歌词行。

    - 跳过不含时间戳的标签行（``[ti:]`` / ``[ar:]`` / ``[by:]`` 等）与无法解析的行；
    - 一行多个时间戳（``[00:01][00:05]同一句``）展开成多条；
    - 应用 ``[offset:]``（毫秒）；
    - 保留空文本行（间奏），由调用方决定要不要显示。
    """
    offset_sec = 0.0
    match = _OFFSET_RE.search(text or "")
    if match:
        try:
            offset_sec = int(match.group(1)) / 1000.0
        except ValueError:
            offset_sec = 0.0

    lines: list[LyricLine] = []
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        stamps = list(_TIME_RE.finditer(stripped))
        if not stamps or _TAG_RE.match(stripped):
            continue
        body = stripped[stamps[-1].end():].strip()
        for stamp in stamps:
            at = _parse_stamp(stamp.group(1), stamp.group(2), stamp.group(3)) + offset_sec
            lines.append(LyricLine(at=max(0.0, at), text=body))
    lines.sort(key=lambda item: item.at)
    return tuple(lines)


def line_at(
    lines: tuple[LyricLine, ...] | list[LyricLine],
    position: float | None,
    *,
    lead: float = 0.0,
    offset: float = 0.0,
) -> int:
    """返回该位置对应（最后一个不晚于它）的歌词下标；无歌词/位置未知返回 -1。

    ``lead`` 是提前量（秒，正值让歌词早点出现），``offset`` 是用户用菜单
    「歌词对齐」调的手动偏移（同样是正值让歌词提前）。两者都只改比较基准，
    不改行本身的时间戳。
    """
    if position is None or not lines:
        return -1
    probe = float(position) + float(lead) + float(offset)
    low, high = 0, len(lines) - 1
    found = -1
    while low <= high:
        mid = (low + high) // 2
        if lines[mid].at <= probe:
            found = mid
            low = mid + 1
        else:
            high = mid - 1
    return found


def line_duration_ms(
    lines: tuple[LyricLine, ...] | list[LyricLine],
    index: int,
    *,
    default: int = 3000,
    minimum: int = 2000,
    maximum: int = 6000,
) -> int:
    """一句歌词的气泡时长：到下一句的间隔（夹在 2~6 秒，最后一句用默认值）。"""
    if not (0 <= index < len(lines)):
        return default
    if index + 1 >= len(lines):
        return default
    gap = float(lines[index + 1].at) - float(lines[index].at)
    if gap <= 0:
        return minimum
    return int(max(minimum, min(maximum, gap * 1000)))


def looks_instrumental(lines: tuple[LyricLine, ...] | list[LyricLine]) -> bool:
    """这些"歌词"是否只是纯音乐占位文案（整首只有一两句、且全部命中）。"""
    texts = [str(line.text or "").strip().lower() for line in lines]
    texts = [text for text in texts if text]
    if not texts or len(texts) > 3:
        return False
    return all(
        any(marker in text for marker in _INSTRUMENTAL_MARKERS) for text in texts
    )


def _lyrics_from_text(text: str, source: str, *, score: float = 0.0) -> Lyrics | None:
    lines = parse_lrc(text)
    if not lines:
        return None
    if looks_instrumental(lines):
        return Lyrics(instrumental=True, source=source, score=score)
    return Lyrics(lines=lines, source=source, score=score)


# ---------------------------------------------------------------- 缓存


def cache_key(title: str, artist: str) -> str:
    """``sha1(title|artist)``（归一小写）——缓存文件名。"""
    raw = f"{str(title or '').strip().lower()}|{str(artist or '').strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class LyricsCache:
    """磁盘缓存：一到一曲一个 JSON，条目数超上限按修改时间淘汰最旧的。

    ``directory=None`` 时什么也不做（测试或没有存储端口的环境）。
    """

    def __init__(self, directory: Path | str | None = None, limit: int = CACHE_LIMIT):
        self.directory = Path(directory) if directory is not None else None
        self.limit = max(1, int(limit))

    def path_for(self, title: str, artist: str) -> Path | None:
        if self.directory is None:
            return None
        return self.directory / f"{cache_key(title, artist)}.json"

    def get(self, title: str, artist: str) -> Lyrics | None:
        path = self.path_for(title, artist)
        if path is None:
            return None
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("v") != CACHE_VERSION:
                return None
            lines = tuple(
                LyricLine(at=float(item[0]), text=str(item[1]))
                for item in data.get("lines") or []
            )
            instrumental = bool(data.get("instrumental"))
            if not lines and not instrumental:
                return None
            return Lyrics(
                lines=lines,
                instrumental=instrumental,
                source=str(data.get("source") or ""),
                score=float(data.get("score") or 0.0),
            )
        except Exception:
            return None

    def put(self, title: str, artist: str, lyrics: Lyrics) -> None:
        path = self.path_for(title, artist)
        if path is None or not lyrics:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "v": CACHE_VERSION,
                        "title": title,
                        "artist": artist,
                        "instrumental": bool(lyrics.instrumental),
                        "source": lyrics.source,
                        "score": float(lyrics.score or 0.0),
                        "lines": [[line.at, line.text] for line in lyrics.lines],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            return
        self.prune()

    def prune(self) -> None:
        """条目超上限时删掉最旧的若干条（LRU 近似），失败不影响取词。"""
        if self.directory is None:
            return
        try:
            entries = [path for path in self.directory.glob("*.json") if path.is_file()]
            if len(entries) <= self.limit:
                return
            entries.sort(key=lambda item: item.stat().st_mtime)
            for stale in entries[: len(entries) - self.limit]:
                try:
                    stale.unlink()
                except OSError:
                    continue
        except Exception:
            return


# ---------------------------------------------------------------- 取数


def describe_error(exc: BaseException) -> str:
    """把 ``HttpError`` 的分类翻成用户可读的短句（不带 URL / 代理 / 响应体）。"""
    reason = str(getattr(exc, "reason", "") or "network")
    status = int(getattr(exc, "status", 0) or 0)
    if reason == "timeout":
        return "请求超时"
    if reason == "rate_limit":
        return "被限流" + (f"（HTTP {status}）" if status else "")
    if reason == "auth":
        return "需要登录" + (f"（HTTP {status}）" if status else "")
    if status:
        return f"HTTP {status}"
    if reason == "protocol":
        return "响应格式不对"
    return "网络连接失败"


def _name_matches(candidate: str, wanted: str) -> bool:
    """歌手名匹配：大小写/空白不敏感，允许子串；两侧都是多歌手时逐段相等比对。"""
    a = re.sub(r"\s+", "", str(candidate or "").lower())
    b = re.sub(r"\s+", "", str(wanted or "").lower())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    parts_a = [re.sub(r"\s+", "", part) for part in _ARTIST_SEP_RE.split(str(candidate or "").lower())]
    parts_b = [re.sub(r"\s+", "", part) for part in _ARTIST_SEP_RE.split(str(wanted or "").lower())]
    parts_a = [part for part in parts_a if part]
    parts_b = [part for part in parts_b if part]
    if len(parts_a) < 2 or len(parts_b) < 2:
        return False
    return any(pa == pb for pa in parts_a for pb in parts_b)


# ---------------------------------------------------------------- 候选打分
#
# 「认错歌」的根因：旧实现只校验歌手名，然后取**第一个**命中的候选。搜「晴天
# 周杰伦」时排在前面的往往是翻唱/同名曲，歌手字段里带"周杰伦"三个字就过了。
# 这里改成四维加权：歌名、歌手、专辑、时长；**缺失的字段按可用权重重新归一**，
# 所以"接口没返回歌名"不会被当成低分（那会让整条流程静默失效）。低于阈值就
# 不取——宁可没有歌词，也不贴一首错的（与仓库"不猜"的纪律一致）。

#: 候选接受阈值（0..1）。标定样本见
#: ``tests/test_petcore_music_lyric_quality.py``：翻唱同名曲 0.50、原唱同名 1.00、
#: 歌名带 ``(Live)`` 后缀 0.93。
MATCH_THRESHOLD = 0.55

#: 各字段权重（时长与歌名、歌手同权：7 分钟现场版和 4 分钟录音室版往往是两份词）。
_FIELD_WEIGHTS = {"title": 1.0, "artist": 1.0, "album": 0.4, "duration": 1.0}

# 归一化时去掉的字符：空白、标点、括号、分隔符（中文属 \w，会被保留）。
_NORM_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(text: str) -> str:
    """比较用归一：NFKC（全角→半角）→ casefold → 去空白与标点。"""
    import unicodedata

    return _NORM_STRIP_RE.sub("", unicodedata.normalize("NFKC", str(text or "")).casefold())


def _title_similarity(wanted: str, candidate: str) -> float:
    """歌名相似度：相等 1.0；互相包含 0.85（``晴天`` vs ``晴天 (Live)``）；否则 0。"""
    a, b = _norm(wanted), _norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return 0.85
    return 0.0


def _artist_similarity(wanted: str, candidate: str) -> float:
    return 1.0 if _name_matches(candidate, wanted) else 0.0


def _album_similarity(wanted: str, candidate: str) -> float:
    a, b = _norm(wanted), _norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return 0.7 if (a in b or b in a) else 0.0


def _duration_similarity(wanted: float, candidate: float) -> float:
    """时长接近度：同一录音通常差不到 1 秒，现场/剪辑版能差出十几秒。"""
    diff = abs(float(wanted) - float(candidate))
    if diff <= 3.0:
        return 1.0
    if diff <= 8.0:
        return 0.6
    if diff <= 15.0:
        return 0.3
    return 0.0


def candidate_score(
    *, title: str, artist: str, album: str = "", duration: float = 0.0, candidate
) -> float:
    """一个候选与"当前在放的这首"的综合匹配分（0..1，缺失字段重新归一）。

    ``candidate`` 是各源自己组出来的字典：``title`` / ``artists``（列表）/
    ``album`` / ``duration``（秒，可缺）。
    """
    scores: dict[str, float] = {}
    cand_title = str(candidate.get("title") or "")
    if cand_title:
        scores["title"] = _title_similarity(title, cand_title)
    artists = [str(item) for item in (candidate.get("artists") or []) if str(item)]
    if artists:
        scores["artist"] = _artist_similarity(artist, "/".join(artists))
    cand_album = str(candidate.get("album") or "")
    if cand_album and album:
        scores["album"] = _album_similarity(album, cand_album)
    cand_duration = candidate.get("duration")
    try:
        has_duration = (
            cand_duration is not None and float(cand_duration) > 0 and float(duration) > 0
        )
    except (TypeError, ValueError):
        has_duration = False
    if has_duration:
        scores["duration"] = _duration_similarity(float(duration), float(cand_duration))
    if not scores:
        return 0.0
    total_weight = sum(_FIELD_WEIGHTS[name] for name in scores)
    if total_weight <= 0:
        return 0.0
    weighted = sum(_FIELD_WEIGHTS[name] * value for name, value in scores.items())
    return weighted / total_weight


def pick_best(candidates, *, title: str, artist: str, album: str = "", duration: float = 0.0):
    """在候选里挑分最高且过阈值的一个；返回 ``(候选, 分数)`` 或 ``None``。

    **一个都不够分就不取**：同一首歌贴错词比没有词更糟（用户会以为桌宠在放另一首）。
    """
    best = None
    best_score = 0.0
    for candidate in candidates or ():
        if not isinstance(candidate, dict) or candidate.get("id") in (None, ""):
            continue
        score = candidate_score(
            title=title, artist=artist, album=album, duration=duration, candidate=candidate
        )
        if score > best_score:
            best, best_score = candidate, score
    if best is None or best_score < MATCH_THRESHOLD:
        return None
    return best, best_score


@dataclass(frozen=True, slots=True)
class _Wanted:
    """一次取词要找的那首歌（查询词已清洗）。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0


def _clean_query(title: str, artist: str) -> tuple[str, str]:
    """把原始标题洗成查询词（网页标题 → 歌名/歌手）。

    只用于**搜索**，不改显示：气泡里的「我在唱《…》」仍是播放器给的原始曲名。
    清洗失败（结果为空/过短）时原样返回——``title_clean`` 保证只删片段不改写。
    """
    raw_title = str(title or "").strip()
    raw_artist = str(artist or "").strip()
    try:
        from . import title_clean

        cleaned_title, cleaned_artist = title_clean.clean_web_title(raw_title, raw_artist)
    except Exception:
        return raw_title, raw_artist
    return (
        str(cleaned_title or raw_title).strip() or raw_title,
        str(cleaned_artist or raw_artist).strip() or raw_artist,
    )


def _get_json(
    http,
    url: str,
    params: tuple[tuple[str, str], ...],
    *,
    timeout: float,
    referer: str = "",
    prefer_direct: bool = True,
    cancelled: "Cancelled | None" = None,
):
    """GET 一个 JSON 接口；非 2xx / 太大 / 解析失败都抛 ``HttpError``。

    ``prefer_direct``：国内曲库（网易云 / 酷狗）直连优先；海外源（lrclib）交回
    系统路由——与 ``docs/NETWORK-PROXY-AND-VPN-2026-09-22.md`` 的分域口径一致。

    ``cancelled``：每发一跳之前问一次（一次取词最多三源、每源两跳，被顶掉的那轮
    不该再把剩下的跳数跑完）。
    """
    _abort_if_cancelled(cancelled)
    headers: tuple[tuple[str, str], ...] = (("User-Agent", _UA),)
    if referer:
        headers += (("Referer", referer),)
    response = http.request(
        HttpRequest(
            url=url,
            params=tuple(params),
            headers=headers,
            timeout=max(MIN_ATTEMPT_BUDGET, float(timeout)),
            prefer_direct=prefer_direct,
            max_bytes=MAX_BYTES,
        )
    )
    status = int(getattr(response, "status", 0) or 0)
    body = bytes(getattr(response, "body", b"") or b"")
    if len(body) > MAX_BYTES:
        raise HttpError("protocol", message="歌词响应超出大小上限")
    if status >= 400 or status == 0:
        raise HttpError("protocol", status=status)
    try:
        return json.loads(response.text)
    except ValueError:
        raise HttpError("protocol", message="歌词响应不是 JSON") from None


def _fetch_netease(http, wanted: _Wanted, *, timeout: float, cancelled=None) -> Lyrics | None:
    """网易云：搜索（``cloudsearch``）→ 按分挑候选 → 歌词（``song/lyric``）。"""
    query = " ".join(part for part in (wanted.title, wanted.artist) if part)
    search = _get_json(
        http,
        "https://music.163.com/api/cloudsearch/pc",
        (("s", query), ("type", "1"), ("offset", "0"), ("limit", "10")),
        timeout=timeout,
        referer="https://music.163.com/",
        cancelled=cancelled,
    )
    songs = ((search.get("result") or {}).get("songs") or []) if isinstance(search, dict) else []
    candidates = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        artists_field = song.get("ar") or song.get("artists") or []
        duration = song.get("dt")
        candidates.append(
            {
                "id": song.get("id"),
                "title": str(song.get("name") or ""),
                "artists": [
                    str(item.get("name") or "")
                    for item in artists_field
                    if isinstance(item, dict)
                ],
                "album": str((song.get("al") or {}).get("name") or ""),
                "duration": (float(duration) / 1000.0) if duration else None,
            }
        )
    best = pick_best(
        candidates,
        title=wanted.title,
        artist=wanted.artist,
        album=wanted.album,
        duration=wanted.duration,
    )
    if best is None:
        return None
    song_id, score = best[0].get("id"), best[1]
    payload = _get_json(
        http,
        "https://music.163.com/api/song/lyric",
        (("id", str(song_id)), ("lv", "-1"), ("kv", "-1"), ("tv", "-1")),
        timeout=timeout,
        referer="https://music.163.com/",
        cancelled=cancelled,
    )
    if not isinstance(payload, dict):
        return None
    return _lyrics_from_text(
        str((payload.get("lrc") or {}).get("lyric") or ""), "网易云", score=score
    )


def _fetch_kugou(http, wanted: _Wanted, *, timeout: float, cancelled=None) -> Lyrics | None:
    """酷狗：搜索（``krcs/search``）→ 按分挑候选 → 下载（正文 base64）。"""
    keyword = " - ".join(part for part in (wanted.title, wanted.artist) if part)
    search = _get_json(
        http,
        "https://krcs.kugou.com/search",
        (
            ("ver", "1"),
            ("man", "yes"),
            ("client", "mobi"),
            ("keyword", keyword),
        ),
        timeout=timeout,
        referer="https://www.kugou.com/",
        cancelled=cancelled,
    )
    raw = search.get("candidates") if isinstance(search, dict) else None
    candidates = []
    for item in raw or ():
        if not isinstance(item, dict) or item.get("id") is None or not item.get("accesskey"):
            continue
        singer = str(item.get("singer") or "")
        candidates.append(
            {
                "id": item.get("id"),
                "accesskey": item.get("accesskey"),
                "title": str(item.get("song") or ""),
                "artists": [singer] if singer else [],
                "album": str(item.get("album") or ""),
                "duration": item.get("duration"),
            }
        )
    best = pick_best(
        candidates,
        title=wanted.title,
        artist=wanted.artist,
        album=wanted.album,
        duration=wanted.duration,
    )
    if best is None:
        return None
    chosen, score = best
    payload = _get_json(
        http,
        "https://lyrics.kugou.com/download",
        (
            ("id", str(chosen.get("id"))),
            ("accesskey", str(chosen.get("accesskey"))),
            ("fmt", "lrc"),
            ("charset", "utf8"),
            ("client", "pc"),
            ("ver", "1"),
        ),
        timeout=timeout,
        referer="https://www.kugou.com/",
        cancelled=cancelled,
    )
    content = str((payload or {}).get("content") or "") if isinstance(payload, dict) else ""
    if not content:
        return None
    try:
        body = base64.b64decode(content).decode("utf-8", "replace")
    except Exception:
        return None
    return _lyrics_from_text(body, "酷狗", score=score)


def _fetch_lrclib(http, wanted: _Wanted, *, timeout: float, cancelled=None) -> Lyrics | None:
    """lrclib：按「歌手 + 歌名」精确查；海外曲目覆盖好，也是唯一给纯音乐标记的源。"""
    if not wanted.title:
        return None
    payload = _get_json(
        http,
        "https://lrclib.net/api/get",
        (
            ("artist_name", wanted.artist),
            ("track_name", wanted.title),
        ),
        timeout=timeout,
        prefer_direct=False,
        cancelled=cancelled,
    )
    if not isinstance(payload, dict):
        return None
    synced = str(payload.get("syncedLyrics") or "")
    if not synced:
        if bool(payload.get("instrumental")):
            return Lyrics(instrumental=True, source="lrclib", score=1.0)
        return None
    return _lyrics_from_text(synced, "lrclib", score=1.0)


#: 歌词源，按匹配质量排序（靠前优先）。每个源最多两跳，失败直接换下一个。
SOURCES: tuple[tuple[str, object], ...] = (
    ("网易云", _fetch_netease),
    ("酷狗", _fetch_kugou),
    ("lrclib", _fetch_lrclib),
)


def fetch_lyrics(
    http,
    title: str,
    artist: str,
    *,
    album: str = "",
    duration: float = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
    cache: LyricsCache | None = None,
    cancelled: "Cancelled | None" = None,
) -> Lyrics:
    """取一首歌的歌词：清洗查询词 → 缓存 → 网易云 → 酷狗 → lrclib。

    一首歌最多一轮（**失败不重试**：重试策略在运行时按冷却做，避免后台重复流量）。
    ``timeout`` 是整次取词的预算，各源共享同一份截止时刻：每个源只拿到剩余部分，
    因此源切换不会把总耗时翻倍。``duration``（秒）只用于候选打分，不参与请求。

    ``cancelled`` 是"这一轮还要不要"的回调（切歌/停止时由运行时置位）：每次发请求
    之前问一次，被取消就抛 :class:`LyricsCancelled`。取消**不算失败**——不写缓存、
    不冷却，调用方也不该弹"歌词获取失败"。
    """
    title, artist = _clean_query(title, artist)
    if not title and not artist:
        raise LyricsError("没有歌名信息，无法取歌词。")
    wanted = _Wanted(
        title=str(title or ""),
        artist=str(artist or ""),
        album=str(album or ""),
        duration=float(duration or 0.0),
    )

    if cache is not None:
        hit = cache.get(wanted.title, wanted.artist)
        if hit is not None:
            return hit

    deadline = time.monotonic() + max(1.0, float(timeout))
    reasons: list[str] = []
    for name, fetcher in SOURCES:
        _abort_if_cancelled(cancelled)
        remaining = deadline - time.monotonic()
        if remaining < MIN_ATTEMPT_BUDGET:
            reasons.append(f"{name}：时间预算不足")
            break
        try:
            found = fetcher(  # type: ignore[operator]
                http, wanted, timeout=remaining, cancelled=cancelled
            )
        except LyricsCancelled:
            raise
        except HttpError as exc:
            reasons.append(f"{name}：{describe_error(exc)}")
            continue
        except (ValueError, KeyError, TypeError):
            reasons.append(f"{name}：响应格式不对")
            continue
        except Exception as exc:
            reasons.append(f"{name}：{describe_error(exc)}")
            continue
        if found is not None:
            if cache is not None:
                cache.put(wanted.title, wanted.artist, found)
            return found
        reasons.append(f"{name}：没有找到匹配的歌词")
    raise LyricsError("歌词获取失败：" + "；".join(reasons))
