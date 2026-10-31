"""每日一句的日期/游标状态（零 Qt，纯函数）。

状态文件按 slot 记一条 ``{"date": "YYYY-MM-DD", "index": n}``：``index`` 是
当天已经说过几句（下一次说当天台词组的第 ``index`` 句）。旧版只有 ``{"date":
...}``，读到时按「今天还没说过」处理，下一次写入会自动升级成新结构。

``daily_line`` 是旧版「一天一句」的确定性取句，保留给旧调用方；新运行时用
``daily_pool.daily_set`` 取当天的整组台词。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path

_MOD = 0xFFFFFFFFFFFFFFFF
_MAX_SEGMENTS = 5


def _splitmix64(value: int) -> int:
    z = (int(value) + 0x9E3779B97F4A7C15) & _MOD
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MOD
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MOD
    return (z ^ (z >> 31)) & _MOD


def daily_line(
    texts, day: _dt.date | None = None, *, salt: int = 0
) -> str:
    """从候选池里按日期确定性地取一句；空池返回空串。"""
    pool = [str(item).strip() for item in (texts or ()) if str(item).strip()]
    if not pool:
        return ""
    current = day or _dt.date.today()
    seed = _splitmix64(current.toordinal() * 0x2545F4914F6CDD1D + int(salt))
    return pool[seed % len(pool)]


def split_sequence(text: str) -> tuple[str, ...]:
    """把一句拆成连说片段：``|``/换行/句末标点，最多 5 段。"""
    value = str(text or "").strip()
    if not value:
        return ()
    parts = [
        part.strip()
        for part in re.split(r"[|\n。！？!?]+", value)
        if part.strip()
    ]
    if not parts:
        return ()
    return tuple(parts[:_MAX_SEGMENTS])


def load_state(path: Path | str) -> dict:
    """读每日状态；坏文件/缺文件一律当空，绝不抛。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def slot_entry(state: dict, slot: int) -> dict:
    """某只宠的状态条目；缺失或结构不对返回空字典。"""
    entry = (state or {}).get(str(int(slot)))
    return entry if isinstance(entry, dict) else {}


def daily_index(state: dict, slot: int, day: _dt.date | None = None) -> int:
    """当天已经说过几句（下一次取当天台词组的第几句）。

    日期不是今天（含旧版只有 ``{"date": ...}`` 的条目）一律 0：换天自然从头
    开始，旧结构也不会让当天的第一批台词被跳过。
    """
    current = day or _dt.date.today()
    entry = slot_entry(state, slot)
    if str(entry.get("date") or "") != current.isoformat():
        return 0
    try:
        return max(0, int(entry.get("index") or 0))
    except (TypeError, ValueError):
        return 0


def mark_said(
    state: dict, slot: int, day: _dt.date | None = None, index: int = 1
) -> dict:
    """记录「说到第几句」：只有气泡真的显示过才由调用方推进 ``index``。

    ``index`` 缺省按 1（旧调用方的「说过一句」语义）；显式 0 = 当天从头开始。
    """
    current = day or _dt.date.today()
    try:
        value = max(0, int(index))
    except (TypeError, ValueError):
        value = 0
    state[str(int(slot))] = {"date": current.isoformat(), "index": value}
    return state


def said_today(state: dict, slot: int, day: _dt.date | None = None) -> bool:
    """今天是否至少说过一句（旧语义；运行时改用 ``daily_index`` 判断是否说完）。"""
    current = day or _dt.date.today()
    entry = slot_entry(state, slot)
    return str(entry.get("date") or "") == current.isoformat()


def save_state(path: Path | str, state: dict) -> None:
    """原子写；失败只抛 OSError，由调用方决定是否吞掉。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        prefix="daily.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
