"""每日一句的内置池与按日确定性取样（零 Qt，纯函数）。

``daily_lines.json`` 是与 ``host/`` 同级的随包素材（1000+ 句）；本模块回答
三件事：池子里有哪些句子、某一天某只宠该拿哪一组、这一组说完之后播放池怎么
循环。

当天 + slot 先洗成一个固定排列，再取前 ``count`` 句：同一天同一只宠永远拿到
同一组，换一天整组都换。洗牌参数刻意自带（不 import ``random``）：同一日期在
任何解释器版本、任何进程上都洗出同一个排列，也不受 ``PYTHONHASHSEED`` 影响。
做法与 ``examples/petcore-mods/voice.chime`` 的 ``quote_day_order`` 同源，但按
「mod 之间不互相 import」的约定复制在这里。

播放池 = 当天这一组 + 候选台词，按 ``(day, slot, cycle)`` 逐轮重洗；游标仍只
存一个 ``index``（见 ``daily.py``），``index < len(daily)`` 是每日句阶段，之后
用 ``playback_slot`` 换算成第几轮的第几句。
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

#: 随包台词池文件名（与 ``host/`` 同属 ``fun.self-talk`` 包根）。
POOL_FILENAME = "daily_lines.json"
POOL_SCHEMA = 1
#: 每天默认句数；与 ``petcore.config.DEFAULTS["self_talk_daily_count"]`` 一致。
DEFAULT_COUNT = 12
MIN_COUNT = 1
MAX_COUNT = 24

#: 洗牌用的 64 位线性同余参数（Knuth/MMIX）。高标准位取模（``>> 33``）避免
#: LCG 低位周期短；同一天在同一台机器上永远得到同一个排列。
_SHUFFLE_MASK = (1 << 64) - 1
_SHUFFLE_MUL = 6364136223846793005
_SHUFFLE_ADD = 1442695040888963407
_SHUFFLE_SALT = 0x9E3779B97F4A7C15

#: 按 (路径) 缓存已解析的池：文件 mtime 没变就不重复读盘。
_CACHE: dict[str, tuple[int, tuple[str, ...]]] = {}


def pool_path(base: Path | str | None = None) -> Path:
    """池文件路径；``base`` 给 mod 包根目录或 ``host`` 目录都行。"""
    if base is None:
        return Path(__file__).resolve().parent.parent / POOL_FILENAME
    target = Path(base)
    if target.is_dir():
        for candidate in (target / POOL_FILENAME, target.parent / POOL_FILENAME):
            if candidate.is_file():
                return candidate
        return target / POOL_FILENAME
    return target


def clean_pool(lines) -> tuple[str, ...]:
    """把任意输入洗成台词列表：逐条 strip、丢掉空值与重复项（保持顺序）。"""
    values: list[str] = []
    seen: set[str] = set()
    for item in lines or ():
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return tuple(values)


def load_pool(path: Path | str | None = None) -> tuple[str, ...]:
    """读随包池；文件缺失/损坏返回空元组，绝不抛（运行时不能因此断掉自语）。"""
    target = pool_path(path)
    try:
        stat = target.stat()
    except OSError:
        return ()
    key = str(target)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == int(stat.st_mtime_ns):
        return cached[1]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    raw = payload.get("lines") if isinstance(payload, dict) else payload
    lines = clean_pool(raw if isinstance(raw, (list, tuple)) else ())
    _CACHE[key] = (int(stat.st_mtime_ns), lines)
    return lines


def clamp_count(value, default: int = DEFAULT_COUNT) -> int:
    """句数钳到 1–24；非数字回默认值。"""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return int(default)
    return max(MIN_COUNT, min(MAX_COUNT, number))


def day_order(pool, day_ordinal: int, *, salt: int = 0) -> tuple[str, ...]:
    """把台词池按「日期 + slot」洗成当天的固定排列（Fisher–Yates，确定性）。"""
    items = list(clean_pool(pool))
    state = (
        int(day_ordinal) * _SHUFFLE_MUL + _SHUFFLE_SALT + int(salt)
    ) & _SHUFFLE_MASK
    for index in range(len(items) - 1, 0, -1):
        state = (state * _SHUFFLE_MUL + _SHUFFLE_ADD) & _SHUFFLE_MASK
        swap = (state >> 33) % (index + 1)
        items[index], items[swap] = items[swap], items[index]
    return tuple(items)


def daily_set(
    pool,
    day: _dt.date | None = None,
    *,
    count: int = DEFAULT_COUNT,
    salt: int = 0,
) -> tuple[str, ...]:
    """当天该说的那一组台词：池子不足 ``count`` 句时整池都用。"""
    current = day or _dt.date.today()
    lines = day_order(pool, current.toordinal(), salt=salt)
    return lines[: clamp_count(count)]


#: 轮次 salt 的步长：同一 (day, slot) 的第 n 轮必须洗出不同排列，又不与别的
#: slot 撞车。日轮次远小于这个数（自言自语 20–60 秒一次，一天最多几百轮）。
_PLAYBACK_SALT_STRIDE = 1 << 20


def merge_pool(*pools) -> tuple[str, ...]:
    """把多段台词合并成一个池：逐条 strip、丢空值、按首次出现去重。"""
    values: list[str] = []
    for pool in pools:
        values.extend(pool or ())
    return clean_pool(values)


def playback_slot(
    index: int, daily_count: int, pool_size: int
) -> tuple[int, int] | None:
    """把「已说几句」换算成播放池的 ``(第几轮, 本轮第几句)``。

    每日句阶段（``index < daily_count``）返回 ``None``；池为空也返回 ``None``。
    """
    try:
        spoken = max(0, int(index))
        daily = max(0, int(daily_count))
        size = int(pool_size)
    except (TypeError, ValueError, OverflowError):
        return None
    if size <= 0 or spoken < daily:
        return None
    return divmod(spoken - daily, size)


def playback_order(
    daily,
    extras,
    day_ordinal: int,
    *,
    cycle: int = 0,
    salt: int = 0,
    leading: str = "",
) -> tuple[str, ...]:
    """播放池第 ``cycle`` 轮的排列；``leading`` 是上一句，用来避免接缝重复。

    每一轮用 ``(day, slot, cycle)`` 独立洗牌；若本轮第一个词与上一句相同，与
    第二个词对调——同一轮内本来就不会有重复词，所以对调整轮仍然保持「每句
    只出现一次」。
    """
    pool = merge_pool(daily, extras)
    if not pool:
        return ()
    before = str(leading or "").strip()
    order: tuple[str, ...] = ()
    try:
        last = max(0, int(cycle))
    except (TypeError, ValueError, OverflowError):
        last = 0
    for current in range(last + 1):
        items = list(
            day_order(
                pool,
                int(day_ordinal),
                salt=(int(salt) + 1) * _PLAYBACK_SALT_STRIDE + current,
            )
        )
        if before and len(items) > 1 and items[0] == before:
            items[0], items[1] = items[1], items[0]
        order = tuple(items)
        before = order[-1]
    return order


def playback_line(
    daily,
    extras,
    day_ordinal: int,
    index: int,
    *,
    salt: int = 0,
) -> str:
    """播放池里「已说 ``index`` 句」时的下一句；每日阶段或池空返回空串。"""
    daily_lines = clean_pool(daily)
    slot = playback_slot(index, len(daily_lines), len(merge_pool(daily_lines, extras)))
    if slot is None:
        return ""
    cycle, offset = slot
    order = playback_order(
        daily_lines,
        extras,
        day_ordinal,
        cycle=cycle,
        salt=salt,
        leading=daily_lines[-1] if daily_lines else "",
    )
    if offset >= len(order):
        return ""
    return order[offset]
