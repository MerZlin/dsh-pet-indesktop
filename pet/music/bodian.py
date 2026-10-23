# -*- coding: utf-8 -*-
"""波点音乐的曲目来源：读它自己的本地播放记录（进程 + 数据库 + 日志）。

**为什么需要它**：波点音乐（``bodian_pc.exe``，Flutter + media_kit/libmpv）**不注册
系统媒体会话**，也不在窗口标题里写歌名——两条现成通道都不通，歌词链路看不见它：

- 2026-10-21 实机探针：波点正在播放时
  ``GlobalSystemMediaTransportControlsSessionManager.GetSessions()`` 返回 **0 个会话**；
  它的 ``data/app.so`` 与全部插件 DLL 里都没有 ``SystemMediaTransportControls`` 字样
  （唯一命中是 ``vccorlib140d.dll`` 这个调试版 C++ 运行时的通用接口名，不是它的代码）。
- 它的主窗口标题恒为 ``波点音乐``（无属主可见顶层窗口实测），所以 foobar 那套
  「读窗口标题」的补充源对它同样无效。

**数据从哪来**：波点自己把播放历史写进本地库
``%LOCALAPPDATA%\\cn.wenyu.bodian\\bodian_pc\\database\\songDB.db`` 的 ``hist_song``
表——**每次开始播放一首就插一行**，``json`` 列里是完整的曲目对象
（``name`` / ``artist`` / ``album`` / ``duration`` / ``id``）。实机实测（2026-10-21）：
切歌后约 0.26 秒落库，``time`` 列是这首开始播放的本地时刻。

播放/暂停状态库里没有，只能读它的日志
``%LOCALAPPDATA%\\cn.wenyu.bodian\\bodian_pc\\bdlog\\log_0.log``：media_kit 的播放
事件被逐行写成 ``mpv playing event=true/false``（同一时刻另有
``_audioPlayerListener===true=`` 重复一行，本模块只认前者）。日志在切歌/暂停时
**即时落盘**（实测与数据库同一秒内），所以状态判定的滞后在 1 拍以内。

**它在链路里的地位与 :mod:`pet.music.foobar` 完全相同：补充源**。系统媒体会话有在播
会话时根本不问它（见 ``now_playing._read_supplementary_playback``），返回的 ``app_id``
是 :data:`BODIAN_APP_ID`，调用方据此不拿它覆盖系统会话的跟踪位。

**三条刻意的口径**：

1. **只读**：数据库用 ``mode=ro`` 只读 URI 打开、日志只读尾巴，从不写、从不建表、
   从不加锁抢占。数据库是 ``journal_mode=delete``，写事务期间读会短暂拿到
   ``database is locked``——此时**沿用上一次成功读到的曲目**（有上限），
   而不是把歌词清空：歌词闪一下比晚半秒更伤。
2. **不猜进度**：库里的 ``time`` 是"开始播放的时刻"，不是播放器上报的进度；
   把它当 ``position`` 会骗过 ``LyricTracker``（它会认为播放器会自己跟快进，
   从而关掉手动对齐）。所以进度恒为 ``None``，交给调用方按本地时钟推算——
   与网易云同一个口径。
3. **读不到就当没有**：路径不存在、表不存在、JSON 坏、进程没跑，一律返回 ``None``。
   歌词只是锦上添花，不能因为第三方程序影响桌宠本体。

**上限**（有界优先于猜中）：每次只取 ``ORDER BY ord DESC LIMIT 1`` 一行；
单行 ``json`` 超过 :data:`_MAX_JSON_CHARS` 按损坏处理；日志只读末尾
:data:`_LOG_TAIL_BYTES` 字节；扫描的 ``log_*.log`` 条数有上限。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: 作为 ``Playback.app_id`` 的取值——**刻意不是系统会话 id**：它让调用方能区分
#: "这条来自本地播放记录、别拿它去覆盖系统会话的跟踪位"
#: （``now_playing.is_bodian_source``）。
BODIAN_APP_ID = "bodian_pc.exe"

#: 数据目录（相对 ``%LOCALAPPDATA%``）：库与日志都在这里。这是**安装器固定的**
#: 位置（实测 2026-10-21），不是用户可配置项——读不到就是没有，不猜别的盘。
_DATA_SUBDIR = ("cn.wenyu.bodian", "bodian_pc")
_DB_RELATIVE = ("database", "songDB.db")
_LOG_DIR_RELATIVE = ("bdlog",)

#: 单行 ``json`` 的长度上限。实测一首歌的对象约 2.2~2.4KB（35 个字段），
#: 上限给到 64KB：再大的只可能是损坏或攻击性内容，不解析。
_MAX_JSON_CHARS = 64 * 1024

#: 日志尾巴读取上限。实测一行最长约 700 字节（超长 JSON 行会被跳过），
#: 64KB 足够覆盖最近几十次播放事件。
_LOG_TAIL_BYTES = 64 * 1024

#: 参与扫描的日志文件条数上限（``log_0.log`` / ``log_1.log`` 这类滚动名）。
#: **先按 mtime 排完全部，再截前 N 条**——先截断再排序会按目录枚举序挑样本，
#: 日志滚动到 8 个以上时可能把最新的那个排除在外（那样播放状态会一直读到旧值）。
_MAX_LOG_FILES = 8

#: 数据库读取的超时（秒）。**必须很短**：本函数跑在歌词采样线程上（1s 一拍），
#: 波点正在写库时读会撞 ``database is locked``，等下去只会拖慢这一拍。
_DB_TIMEOUT_SECONDS = 0.2

#: 上次成功读到的曲目在数据库短暂不可读时最多沿用多久（秒）。
#: 只覆盖"写事务撞锁"这种瞬时故障，不覆盖"波点已经关了"——进程不在了就直接没有
#: （进程判定见 :func:`process_running`，所以这里可以给得比一拍宽）。
_STALE_REUSE_SECONDS = 10.0

#: 进程扫描的缓存时长（秒）。扫描实测与 :mod:`pet.music.foobar` 同一量级（进程表
#: 遍历，热态 2~3ms），1s 一拍下缓存到 2 秒即可：波点退出后最多 2 秒被发现。
_PROCESS_SCAN_TTL_SECONDS = 2.0

_BODIAN_EXE = "bodian_pc.exe"

#: 播放状态（日志）的缓存时长。日志尾巴是 64KB 的顺序读，实测远低于一拍的预算；
#: 缓存只为把"同一拍里被问多次"合并掉。**只缓存确定结果**（见
#: :func:`playing_state`）——``None`` 是"不知道"，缓存它会让"日志还没写出第一行"
#: 的状态卡满一个 TTL。
_PLAYING_TTL_SECONDS = 1.0

_lock = threading.Lock()
#: ``(ord, id, time)`` → 解析好的曲目；同一行不重复解析 JSON。
_row_cache: tuple | None = None
_row_value: dict | None = None
#: 上次成功读到的曲目（含读出时刻），供 ``_STALE_REUSE_SECONDS`` 内复用。
_last_good: tuple[float, dict] | None = None
#: 播放状态缓存：``(读到的时刻, 是否在播)``——**只存 bool，不存 None**。
_playing_cache: tuple[float, bool] | None = None
#: 进程扫描缓存：``(扫到的时刻, 是否在跑)``。
_process_cache: tuple[float, bool] | None = None


@dataclass(frozen=True)
class BodianTrack:
    """一条从波点本地库读到的曲目。"""

    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    playing: bool = True


def _local_appdata() -> Path | None:
    """``%LOCALAPPDATA%``；非 Windows 或环境变量缺失时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base)


def data_dir() -> Path | None:
    """波点的数据目录；不在预期位置时返回 ``None``（**不扫描别的盘**）。"""
    base = _local_appdata()
    if base is None:
        return None
    return base.joinpath(*_DATA_SUBDIR)


def database_path() -> Path | None:
    """``songDB.db`` 的完整路径（不保证存在）。"""
    root = data_dir()
    if root is None:
        return None
    return root.joinpath(*_DB_RELATIVE)


def log_dir() -> Path | None:
    """``bdlog`` 目录（不保证存在）。"""
    root = data_dir()
    if root is None:
        return None
    return root.joinpath(*_LOG_DIR_RELATIVE)


def _sqlite_uri(path: Path) -> str:
    """把本地路径编成 SQLite 的 ``file:`` 只读 URI。

    **必须按路径段百分号编码**：``%LOCALAPPDATA%`` 在中文 Windows 上是
    ``C:\\Users\\<中文名>\\AppData\\Local``，而 ``file:`` URI 对非 ASCII 与保留字符
    （``?`` ``#`` ``%`` ``&``）都有语义。把整串直接拼进 URI 会让 SQLite 打开一个
    **不存在的路径**（表现为"读不到歌"），或把 ``#`` 之后当成 fragment 截断。

    编码只用 ``quote`` 的默认安全集（斜杠保留），并显式把 ``%`` 自己编掉——
    否则路径里本来带 ``%`` 时会被 SQLite 当成转义起点。
    """
    from urllib.parse import quote

    posix = path.as_posix()
    # 先把已有的 '%' 编码，避免二次解码；quote 默认不转 '%'。
    posix = posix.replace("%", "%25")
    encoded = quote(posix, safe="/:")
    return "file:%s?mode=ro" % encoded


def _read_newest_row() -> dict | None:
    """读 ``hist_song`` 里 ``ord`` 最大的一行，返回 ``{"ord","id","time","json"}``。

    只读打开（``mode=ro``）：不建库、不写、不加写锁。任何异常都返回 ``None``——
    包括波点正在写库时拿到的 ``database is locked``（由调用方决定是否沿用旧值）。
    """
    path = database_path()
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    conn = None
    try:
        conn = sqlite3.connect(
            _sqlite_uri(path), uri=True, timeout=_DB_TIMEOUT_SECONDS
        )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select ord, id, time, json from hist_song order by ord desc limit 1"
        ).fetchone()
    except Exception:
        log.debug("读取波点播放记录失败", exc_info=True)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if row is None:
        return None
    return {
        "ord": row["ord"],
        "id": row["id"],
        "time": str(row["time"] or ""),
        "json": row["json"],
    }


def _parse_row(row: dict) -> dict | None:
    """把一行 ``hist_song`` 解析成曲目字段；坏行返回 ``None``。

    ``json`` 是**第三方程序的私有格式**，按不可信输入处理：类型不对、超长、
    字段缺失都当损坏，绝不因为它的形状去猜歌名。
    """
    raw = row.get("json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    if len(raw) > _MAX_JSON_CHARS:
        log.debug("波点播放记录的 json 过大，按损坏处理（%d 字符）", len(raw))
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        log.debug("波点播放记录的 json 解析失败")
        return None
    if not isinstance(data, dict):
        return None
    title = _text(data.get("name"))
    artist = _text(data.get("artist"))
    if not title and not artist:
        return None
    return {
        "title": title,
        "artist": artist,
        "album": _text(data.get("album")),
        "duration": _seconds(data.get("duration")),
        "key": (row.get("ord"), row.get("id"), row.get("time")),
    }


def _text(value: object) -> str:
    """取字符串字段（去首尾空白）；**非字符串一律空串**。

    刻意不做 ``str(value)`` 兜底：曲目名/歌手名是文本，第三方库里出现数字或
    ``null`` 只可能是脏数据或格式变更。把 ``42`` 变成歌名 ``"42"`` 会拿它去搜歌词，
    搜到的是别人的歌——这正是仓库明文反对的"猜"（宁可搜不到，也不能换成错的）。
    """
    return value.strip() if isinstance(value, str) else ""


def _seconds(value: object) -> float:
    """曲目时长（秒）。**不接受** bool / 负数 / NaN / 无穷——坏值当 0（未知）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    seconds = float(value)
    if seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds <= 0:
        return 0.0
    return seconds


def _log_files() -> list[Path]:
    """``bdlog`` 下的 ``log_*.log``，按修改时间从新到旧（条数有上限）。

    **先收全、再排序、最后截断**。反过来（边枚举边 ``break``）拿到的是目录枚举序
    的前 N 个，日志滚动出 8 个以上时最新的那个可能进不了样本——那会让
    :func:`playing_state` 一直读到轮转前的旧状态（表现为"暂停了歌词还在走"）。
    """
    folder = log_dir()
    if folder is None:
        return []
    try:
        if not folder.is_dir():
            return []
        candidates: list[tuple[float, Path]] = []
        for child in folder.glob("log_*.log"):
            try:
                if child.is_file():
                    candidates.append((child.stat().st_mtime, child))
            except OSError:
                continue
    except OSError:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [child for _, child in candidates[:_MAX_LOG_FILES]]


def _tail_text(path: Path) -> str:
    """读文件末尾 :data:`_LOG_TAIL_BYTES` 字节并解码（坏字节替换，不抛）。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return ""
    return raw.decode("utf-8", "replace")


def _parse_playing(text: str) -> bool | None:
    """从日志尾巴里取**最后一条** ``mpv playing event=`` 的值。

    找不到（日志被轮转/截断、或波点版本换了日志文案）返回 ``None``——
    "不知道"与"没在播"是两回事：不知道时按在播处理（与 foobar 补充源同一口径，
    窗口/日志里分辨不出播放状态时宁可多显示一次，也不要让歌词整段消失）。
    """
    marker = "mpv playing event="
    at = text.rfind(marker)
    if at < 0:
        return None
    value = text[at + len(marker):].strip().lower()
    if value.startswith("true"):
        return True
    if value.startswith("false"):
        return False
    return None


def playing_state() -> bool | None:
    """当前是否在播放；判断不了返回 ``None``。

    带 :data:`_PLAYING_TTL_SECONDS` 缓存，但**只缓存确定结果**（``True``/``False``）。
    ``None`` 是"这一拍判断不了"（日志可能还没写出第一行，波点刚启动），下一拍必须
    重新看——把它缓存满一个 TTL 会让刚启动的波点白白晚一拍才有歌词。
    """
    global _playing_cache
    now = time.monotonic()
    with _lock:
        cached = _playing_cache
    if cached is not None and (now - cached[0]) <= _PLAYING_TTL_SECONDS:
        return cached[1]
    state: bool | None = None
    for path in _log_files():
        state = _parse_playing(_tail_text(path))
        if state is not None:
            break
    if state is not None:
        with _lock:
            _playing_cache = (time.monotonic(), state)
    return state


def reset_cache() -> None:
    """清空全部缓存（测试用；产品路径不需要）。"""
    global _row_cache, _row_value, _last_good, _playing_cache, _process_cache
    with _lock:
        _row_cache = None
        _row_value = None
        _last_good = None
        _playing_cache = None
        _process_cache = None


def _scan_process() -> bool:
    """扫一遍进程表看 ``bodian_pc.exe`` 在不在跑。

    与 ``now_playing.player_process_running`` 同一口径：没有 psutil 或扫描失败时
    返回 ``True``（不拦截）——宁可多试一次也不误伤。
    """
    try:
        import psutil
    except Exception:
        return True
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                if str(proc.info.get("name") or "").lower() == _BODIAN_EXE:
                    return True
            except Exception:
                continue
    except Exception:
        log.debug("扫描波点进程失败", exc_info=True)
        return True
    return False


def process_running() -> bool:
    """波点进程是否在跑（带 :data:`_PROCESS_SCAN_TTL_SECONDS` 缓存）。

    **它是"沿用旧曲目"的前提**：数据库短暂不可读时只有进程还在才允许沿用
    （波点退出后库文件还在、最后一行还是上一首，沿用就等于永远显示上一首）。
    """
    global _process_cache
    now = time.monotonic()
    with _lock:
        cached = _process_cache
    if cached is not None and (now - cached[0]) <= _PROCESS_SCAN_TTL_SECONDS:
        return cached[1]
    running = _scan_process()
    with _lock:
        _process_cache = (time.monotonic(), running)
    return running


def _current_row() -> dict | None:
    """最新一行曲目（解析后），带行级缓存与"短暂不可读时沿用旧值"。"""
    global _row_cache, _row_value, _last_good
    row = _read_newest_row()
    if row is None:
        # 读不到：可能是波点在写库（瞬时撞锁），也可能是它已经退出/删了库。
        # 只有"进程还在"时才沿用旧值——进程不在了就该老老实实没有歌。
        with _lock:
            last = _last_good
        if (
            last is not None
            and (time.monotonic() - last[0]) <= _STALE_REUSE_SECONDS
            and process_running()
        ):
            return last[1]
        return None
    key = (row.get("ord"), row.get("id"), row.get("time"))
    with _lock:
        if _row_cache == key and _row_value is not None:
            return _row_value
    parsed = _parse_row(row)
    if parsed is None:
        return None
    with _lock:
        _row_cache = key
        _row_value = parsed
        _last_good = (time.monotonic(), parsed)
    return parsed


def read_track() -> BodianTrack | None:
    """读波点正在播的曲目；读不到返回 ``None``。

    任何异常都按"读不到"处理——歌词只是锦上添花，不能因为第三方程序影响桌宠。

    **进程不在就没有歌**：这一条必须在读库**之前**判，与 :mod:`pet.music.foobar`
    的 ``if not pids: return None`` 同一先例。理由很具体——波点退出后库文件和
    最后一行都还在（实测 2026-10-23：进程已退出两天，``hist_song`` 里仍是上次那首
    「赐我茉莉」）。只在"库读不到"时才看进程是不够的：库读得到、进程却没了，
    就会把上一首当成正在播——歌词永远停在那一首上。

    :func:`_scan_process` 在"没装 psutil / 扫描失败"时返回 ``True``（fail-open），
    所以这里不会因为环境问题误伤。
    """
    if sys.platform != "win32":
        return None
    try:
        if not process_running():
            return None
    except Exception:
        pass  # 进程判定本身出错时不拦截，交给下面的读取去决定
    try:
        row = _current_row()
    except Exception:
        log.debug("读取波点曲目失败", exc_info=True)
        return None
    if row is None:
        return None
    try:
        state = playing_state()
    except Exception:
        state = None
    return BodianTrack(
        title=str(row.get("title") or ""),
        artist=str(row.get("artist") or ""),
        album=str(row.get("album") or ""),
        duration=float(row.get("duration") or 0.0),
        # 判断不了播放状态时按在播处理（见 _parse_playing 的口径）。
        playing=state is not False,
    )
