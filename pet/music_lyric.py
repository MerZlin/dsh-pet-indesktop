# -*- coding: utf-8 -*-
"""歌词获取、LRC 解析与磁盘缓存。

按"匹配质量"排序依次尝试三个歌词源，命中即返回：

1. **QQ音乐** —— 中文曲库匹配最好，实测搜索首条即原唱。
2. **lrclib** —— 国际/日文曲目覆盖好，字段规范。
3. **网易云** —— 最后兜底；实测搜索常返回翻唱，故靠后。

三个源都是明文 HTTP 接口，无需加密，也不下载任何音频（单曲响应约 10 KB）。

缓存放数据目录下的 ``lyrics_cache/``，只存歌词文本，不存音频、不存收听历史；
条目数超过上限时按文件修改时间淘汰最旧的。用户可在设置页一键清空。

本模块的纯函数（``parse_lrc`` / 曲目匹配）不依赖 Qt，可独立单测。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# 缓存条目上限：按实测量级约 10 KB/首，2000 首 ≈ 21 MB。
CACHE_LIMIT = 2000
# 单个 HTTP 请求超时（秒）。歌词是锦上添花，宁可失败也不要长时间挂住后台线程。
HTTP_TIMEOUT = 8.0
# 缓存格式版本：解析逻辑变更时可据此失效旧缓存。
# v2：新增 instrumental 标记。v1 缓存把纯音乐的占位文案（"纯音乐，请欣赏"）
# 当成正常歌词存了下来，必须作废重取，否则会被当作有词曲目放起唱歌动画。
_CACHE_VERSION = 2

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


@dataclass(frozen=True)
class LyricLine:
    """一行带时间戳的歌词。"""

    at: float
    text: str


@dataclass(frozen=True)
class Lyrics:
    """一次取词的结果。

    ``instrumental`` 表示**纯音乐**：曲目本身没有歌词（配乐 / OST / 演奏曲）。
    调用方据此不显示歌词、也不播唱歌动画——纯音乐没有可唱的句子。
    """

    lines: tuple[LyricLine, ...] = ()
    instrumental: bool = False

    def __bool__(self) -> bool:
        # 纯音乐（lines 为空、instrumental=True）也是**有效结果**：
        # 只认 lines 会让纯音乐缓存永远读不中，每次切回该曲都重打三个源。
        return bool(self.lines) or self.instrumental

    def __iter__(self):
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)


# ---------------------------------------------------------------- LRC 解析


def _parse_stamp(minutes: str, seconds: str, frac: str | None) -> float:
    """把 [mm:ss.xx] 的三段数字换算成秒。"""
    value = int(minutes) * 60 + int(seconds)
    if frac:
        # 两位是厘秒、三位是毫秒，一位按十分之一秒处理。
        value += int(frac) / (10 ** len(frac))
    return value


def parse_lrc(text: str) -> list[LyricLine]:
    """把 LRC 文本解析成按时间排序的歌词行。

    - 跳过不含时间戳的标签行（``[ti:]`` / ``[ar:]`` / ``[by:]`` 等）。
    - 一行有多个时间戳时（``[00:01][00:05]同一句``）展开成多条。
    - 应用 ``[offset:]`` 偏移。
    - 保留空文本行（间奏），调用方据此可让气泡保持上一句或清空。

    注意：**不**过滤"带时间戳但不是歌词"的元信息行（如 ``[00:05] 词：方文山``），
    这是产品上明确接受的行为。
    """
    offset_sec = 0.0
    match = _OFFSET_RE.search(text)
    if match:
        try:
            offset_sec = int(match.group(1)) / 1000.0
        except ValueError:
            offset_sec = 0.0

    lines: list[LyricLine] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        stamps = list(_TIME_RE.finditer(stripped))
        if not stamps:
            # 没有时间戳：可能是标签行，也可能是不规范的纯文本，两种都跳过。
            continue
        if _TAG_RE.match(stripped):
            continue
        # 时间戳之后的剩余内容即歌词文本。
        body = stripped[stamps[-1].end():].strip()
        for stamp in stamps:
            at = _parse_stamp(stamp.group(1), stamp.group(2), stamp.group(3)) + offset_sec
            lines.append(LyricLine(at=max(0.0, at), text=body))

    lines.sort(key=lambda item: item.at)
    return lines


# ---------------------------------------------------------------- 缓存


def cache_dir() -> Path:
    """歌词缓存目录（不存在时创建）。"""
    from .config import APP_DIR_NAME, _default_base

    path = Path(_default_base()) / APP_DIR_NAME / "lyrics_cache"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.debug("创建歌词缓存目录失败: %s", path, exc_info=True)
    return path


def _cache_path(title: str, artist: str) -> Path:
    digest = hashlib.sha1(
        f"{artist.strip().lower()}|{title.strip().lower()}".encode("utf-8")
    ).hexdigest()
    return cache_dir() / f"{digest}.json"


def _read_cache(title: str, artist: str) -> Lyrics | None:
    path = _cache_path(title, artist)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("v") != _CACHE_VERSION:
            return None
        lines = tuple(
            LyricLine(at=float(item[0]), text=str(item[1]))
            for item in data.get("lines", [])
        )
        instrumental = bool(data.get("instrumental"))
        if not lines and not instrumental:
            return None
        return Lyrics(lines=lines, instrumental=instrumental)
    except Exception:
        log.debug("读取歌词缓存失败: %s", path, exc_info=True)
        return None


def _write_cache(title: str, artist: str, lyrics: Lyrics) -> None:
    if not lyrics.lines and not lyrics.instrumental:
        return
    path = _cache_path(title, artist)
    try:
        path.write_text(
            json.dumps(
                {
                    "v": _CACHE_VERSION,
                    "title": title,
                    "artist": artist,
                    "fetched_at": time.time(),
                    "instrumental": bool(lyrics.instrumental),
                    "lines": [[line.at, line.text] for line in lyrics.lines],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        log.debug("写入歌词缓存失败: %s", path, exc_info=True)
        return
    _prune_cache()


def _prune_cache(limit: int = CACHE_LIMIT) -> None:
    """条目超上限时，按修改时间淘汰最旧的若干条（LRU 近似）。"""
    try:
        entries = [p for p in cache_dir().glob("*.json") if p.is_file()]
        if len(entries) <= limit:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        for stale in entries[: len(entries) - limit]:
            try:
                stale.unlink()
            except OSError:
                continue
    except Exception:
        log.debug("清理歌词缓存失败", exc_info=True)


def clear_cache() -> int:
    """清空歌词缓存，返回删除的条目数（供设置页"清空歌词缓存"按钮）。"""
    removed = 0
    try:
        for path in cache_dir().glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    except Exception:
        log.debug("清空歌词缓存失败", exc_info=True)
    return removed


# ---------------------------------------------------------------- 网络


def _http_get_json(url: str, *, referer: str | None = None) -> dict | list | None:
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
        # 部分接口返回 JSONP，剥掉外层包裹后再解析。
        text = raw.decode("utf-8", "replace").strip()
        if not text.startswith(("{", "[")):
            start = text.find("(")
            end = text.rfind(")")
            if start != -1 and end > start:
                text = text[start + 1:end]
        return json.loads(text)
    except Exception:
        log.debug("歌词请求失败: %s", url, exc_info=True)
        return None


def _name_matches(candidate: str, wanted: str) -> bool:
    """歌手名匹配：大小写不敏感，允许子串（"周杰伦" 命中 "周杰伦/Jay Chou"）。"""
    a = re.sub(r"\s+", "", str(candidate or "").lower())
    b = re.sub(r"\s+", "", str(wanted or "").lower())
    if not a or not b:
        return False
    return a in b or b in a


def _as_lyrics(lines: list[LyricLine]) -> Lyrics | None:
    """把解析出的行包成 :class:`Lyrics`；空结果视为"这个源没命中"。"""
    if not lines:
        return None
    if _looks_instrumental(lines):
        return Lyrics(instrumental=True)
    return Lyrics(lines=tuple(lines))


# 各平台对"纯音乐"会给一句**占位文案**当歌词（不是空），必须识别出来，
# 否则会被当成唯一的歌词行显示、还会放起唱歌动画。
#
# 只匹配**整句占位语**，不匹配裸关键词：真歌词里完全可能出现"纯音乐"三个字
# （如「这首纯音乐真好听」），用关键词会把它们误判成纯音乐。
# 实测样本：QQ音乐 '此歌曲为没有填词的纯音乐，请您欣赏' / '纯音乐，请欣赏'。
_INSTRUMENTAL_PATTERNS = (
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


def _looks_instrumental(lines: list[LyricLine]) -> bool:
    """判断这些"歌词"是否只是纯音乐占位文案。

    仅在**整首**都像占位时才判定：真实歌曲里的某一行偶然提到"纯音乐"
    不该被误判成纯音乐。
    """
    texts = [str(line.text or "").strip().lower() for line in lines]
    texts = [t for t in texts if t]
    if not texts:
        return False
    # 占位文案只有一两句；真歌词不会这么少还全部命中关键词。
    if len(texts) > 3:
        return False
    return all(
        any(pattern in text for pattern in _INSTRUMENTAL_PATTERNS) for text in texts
    )


def _fetch_from_qq(title: str, artist: str) -> Lyrics | None:
    """QQ音乐：中文曲库匹配质量最好。"""
    query = urllib.parse.quote(f"{title} {artist}".strip())
    search = _http_get_json(
        "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        f"?w={query}&format=json&n=10&p=1&cr=1&aggr=1",
        referer="https://y.qq.com/",
    )
    if not isinstance(search, dict):
        return None
    songs = ((search.get("data") or {}).get("song") or {}).get("list") or []
    songmid = None
    for song in songs:
        singers = "/".join(
            str(s.get("name") or "") for s in (song.get("singer") or [])
        )
        if _name_matches(singers, artist):
            songmid = song.get("songmid")
            break
    if not songmid:
        return None
    payload = _http_get_json(
        "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
        f"?songmid={songmid}&format=json&nobase64=1&g_tk=5381",
        referer="https://y.qq.com/",
    )
    if not isinstance(payload, dict):
        return None
    return _as_lyrics(parse_lrc(str(payload.get("lyric") or "")))


def _fetch_from_lrclib(title: str, artist: str) -> Lyrics | None:
    """lrclib：国际/日文曲目覆盖好，也是唯一会给「纯音乐」标记的源。"""
    params = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    payload = _http_get_json(f"https://lrclib.net/api/get?{params}")
    if not isinstance(payload, dict):
        return None
    synced = payload.get("syncedLyrics")
    if not synced:
        # 曲目存在但没词：lrclib 会显式给出 instrumental=True（配乐/OST）。
        if payload.get("instrumental"):
            return Lyrics(instrumental=True)
        return None
    return Lyrics(lines=tuple(parse_lrc(str(synced))))


def _fetch_from_netease(title: str, artist: str) -> Lyrics | None:
    """网易云：明文接口，作为最后兜底。"""
    query = urllib.parse.quote(f"{title} {artist}".strip())
    search = _http_get_json(
        "https://music.163.com/api/cloudsearch/pc"
        f"?s={query}&type=1&offset=0&limit=10",
        referer="https://music.163.com/",
    )
    if not isinstance(search, dict):
        return None
    songs = ((search.get("result") or {}).get("songs")) or []
    song_id = None
    for song in songs:
        singers = "/".join(
            str(a.get("name") or "") for a in (song.get("artists") or [])
        )
        if _name_matches(singers, artist):
            song_id = song.get("id")
            break
    if not song_id:
        return None
    payload = _http_get_json(
        f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&kv=-1&tv=-1",
        referer="https://music.163.com/",
    )
    if not isinstance(payload, dict):
        return None
    return _as_lyrics(parse_lrc(str((payload.get("lrc") or {}).get("lyric") or "")))


# 按匹配质量排序（靠前优先）。三源**并发**发起，避免串行等待把几次网络
# 往返叠加成十几秒——实测 QQ 音乐单次就要 4~6 秒，串行时"QQ没命中 + 换
# lrclib"会直接翻倍。
_SOURCES = (
    ("qq", _fetch_from_qq),
    ("lrclib", _fetch_from_lrclib),
    ("netease", _fetch_from_netease),
)

# 高优先级源的优势窗口（秒）：这段时间内若靠前的源返回就采用它，保证匹配
# 质量；过期后接受任何已成功的结果，不让慢源拖住整次取词。
_PRIORITY_GRACE = 1.2


def fetch_lyrics(title: str, artist: str, *, use_cache: bool = True) -> Lyrics | None:
    """取歌词：缓存 → 三源并发（QQ音乐 → lrclib → 网易云，按质量优先）。

    三个源同时发起请求，因此总耗时约等于**最慢的那个**而不是三者之和。
    靠前的源在优势窗口内返回即采用，保证匹配质量；窗口过期后接受任何已成功的
    结果，不让慢源拖住整次取词。

    返回 :class:`Lyrics`；``instrumental=True`` 表示纯音乐（曲目存在但无词），
    调用方据此不显示歌词、也不播唱歌动画。全部失败时返回 ``None``，调用方应
    据此显示「暂无歌词」**并停止对该曲重试**，避免反复请求。
    """
    title = str(title or "").strip()
    artist = str(artist or "").strip()
    if not title and not artist:
        return None

    if use_cache:
        cached = _read_cache(title, artist)
        if cached:
            return cached

    executor = ThreadPoolExecutor(
        max_workers=len(_SOURCES), thread_name_prefix="lyric-fetch"
    )
    try:
        # 把 future 与源名配成对，避免用 id() 反查这种脆弱做法。
        submitted = [
            (name, executor.submit(fetcher, title, artist))
            for name, fetcher in _SOURCES
        ]
        rank = {name: index for index, (name, _) in enumerate(_SOURCES)}
        found: dict[str, Lyrics] = {}
        deadline = time.monotonic() + HTTP_TIMEOUT + 1.0
        grace_until = time.monotonic() + _PRIORITY_GRACE

        while submitted:
            now = time.monotonic()
            if now >= deadline:
                break
            horizon = grace_until if now < grace_until else deadline
            _, not_done = wait(
                [future for _, future in submitted],
                timeout=max(0.05, horizon - now),
                return_when=FIRST_COMPLETED,
            )
            still: list[tuple[str, object]] = []
            for name, future in submitted:
                if future in not_done:
                    still.append((name, future))
                    continue
                try:
                    lines = future.result()
                except Exception:
                    log.debug("歌词源 %s 异常", name, exc_info=True)
                    continue
                if lines is not None:
                    # 注意：空歌词但带 instrumental 标记也是有效结果，
                    # 不能用真值判断（Lyrics.__bool__ 只看 lines）。
                    found[name] = lines
            submitted = still

            # 优势窗口内只要靠前的源成功就别再等；窗口过后任何结果都收。
            if found and time.monotonic() >= grace_until:
                break

        best = _best_found(found, rank)
        if best is not None:
            _write_cache(title, artist, best)
        return best
    finally:
        # 不等剩余请求收尾：已经拿到结果，慢源在后台自然结束即可。
        executor.shutdown(wait=False)


def _best_found(
    found: dict[str, Lyrics], rank: dict[str, int]
) -> Lyrics | None:
    """在已成功的源里挑优先级最高的那个（rank 越小越优先）。"""
    if not found:
        return None
    name = min(found, key=lambda key: rank.get(key, len(rank)))
    return found[name]
