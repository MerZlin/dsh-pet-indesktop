"""每日一句的确定性选择（零 Qt，纯函数）。

同一天、同一 slot 永远得到同一句；换天才换句。种子用 ``date.toordinal()``，
不依赖 ``hash()``（``PYTHONHASHSEED`` 每进程随机）。
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


def mark_said(state: dict, slot: int, day: _dt.date | None = None) -> dict:
    current = day or _dt.date.today()
    state[str(int(slot))] = {"date": current.isoformat()}
    return state


def said_today(state: dict, slot: int, day: _dt.date | None = None) -> bool:
    current = day or _dt.date.today()
    entry = state.get(str(int(slot)))
    return isinstance(entry, dict) and str(entry.get("date") or "") == current.isoformat()


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
