# -*- coding: utf-8 -*-
"""主动识屏短期陪伴记忆 — ProactiveMemory。

批6-1 从 proactive.py 整体迁出（纯搬移，逻辑/默认值/时序零改动）：
- 存储文件：<config.dir>/proactive_screen_memory.json；
- 仅记录元数据（时间戳、进程名、活动分类），绝不保存截图；
- 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
- 采用 .tmp + 原子替换持久化；损坏回退空列表。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable


class ProactiveMemory:
    """主动识屏短期陪伴记忆管理器。

    存储文件：<config.dir>/proactive_screen_memory.json
    - 仅记录元数据（时间戳、进程名、标题、活动分类），绝不保存截图；
    - 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
    - 采用 .tmp + 原子替换持久化；损坏回退空列表。
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        max_entries: int = 20,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self.max_entries = max(1, max_entries)

    def load(self) -> list[dict[str, Any]]:
        """读取记忆列表（按时间倒序，最新在最前）。"""
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                return raw["entries"]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def latest(self) -> dict[str, Any] | None:
        """获取最近一条记忆项。"""
        entries = self.load()
        return entries[0] if entries else None

    def record(self, process: str, title: str, activity: str) -> None:
        """记录一条新的陪伴活动记忆。

        注意：title 参数仅用于保持调用签名兼容，**不会落盘**——窗口标题可能含
        文档名/网页标题等敏感信息，记忆只保留进程名与活动分类。"""
        entries = self.load()
        new_item = {
            "ts": self._clock(),
            "process": str(process or "").strip(),
            "activity": str(activity or "").strip(),
        }
        entries.insert(0, new_item)
        entries = entries[: self.max_entries]

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        """清空陪伴记忆。"""
        try:
            if self.path.is_file():
                self.path.unlink()
        except OSError:
            pass
