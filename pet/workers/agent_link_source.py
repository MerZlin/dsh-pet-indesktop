# -*- coding: utf-8 -*-
"""Qt-free Agent Link event sources used by the worker process.

This module intentionally does not import ``pet.agent_link``.  The tailers mirror
its bounded byte-offset behavior, while parsing and semantic normalization reuse
the stable ``agent-event/v1`` modules.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..agent_event_normalizer import normalize_event
from ..agent_event_protocol import bounded_data, parse_agent_event

log = logging.getLogger("dsh-pet-worker")


class DirGlobTailer:
    """目录下按 glob 匹配多个 jsonl 文件的增量 tail（新文件发现 + 淘汰）。

    用于多 DSH 实例各自写入 dsh-{pid}.jsonl 的场景（P0-2 多实例分区写入）。
    每个实例一个文件，桌宠侧读全部 dsh*.jsonl（含旧版单实例的 dsh.jsonl），
    避免 Windows 上多进程并行写同一文件的行交织。

    兼容旧版单文件语义：保留 ``_initial_backfill_done`` 属性（读写代理到所有
    子 tailer），供测试强制关闭 backfill 直接读取已有内容。
    """

    def __init__(self, directory: Path | str, pattern: str = "dsh*.jsonl", scan_interval: float = 5.0, max_files: int = 64) -> None:
        self.directory = Path(directory)
        self.pattern = pattern
        self.scan_interval = scan_interval
        self.max_files = max_files
        self._last_scan = 0.0
        self._last_directory_mtime_ns: int | None = None
        self._tailers: dict[str, ByteOffsetTailer] = {}
        self._initial_backfill_done: bool = False

    @property
    def _initial_backfill_done(self) -> bool:
        """兼容旧测试：返回子 tailer 的 backfill 状态（任一子 tailer 未完成即 False）。"""
        for t in self._tailers.values():
            if not t._initial_backfill_done:
                return False
        return True

    @_initial_backfill_done.setter
    def _initial_backfill_done(self, value: bool) -> None:
        # 新创建的 tailer 也继承此值
        self._cached_backfill_value = value
        for t in self._tailers.values():
            t._initial_backfill_done = value

    def reset(self) -> None:
        self._last_scan = 0.0
        self._last_directory_mtime_ns = None
        for t in self._tailers.values():
            t.reset()
        self._tailers.clear()

    def _scan(self, now: float) -> None:
        # 始终 glob 目录以可靠发现新文件。st_mtime_ns 在部分文件系统（尤其 CI）
        # 上分辨率粗或有缓存，不能作为「文件增删」的唯一检测信号——用它做早退会
        # 导致 create 后立即 read 漏掉新文件（test_discovers_new_files_during_scan
        # _throttle_and_keeps_offsets 的稳定失败）。glob 便宜（dsh*.jsonl，
        # max_files 封顶），无需节流。
        self._last_scan = now
        try:
            if not self.directory.is_dir():
                return
            files = sorted(self.directory.glob(self.pattern))
            files = files[: self.max_files]
            candidates = {str(f) for f in files}
            for stale in [k for k in self._tailers if k not in candidates]:
                del self._tailers[stale]
            for fkey in candidates:
                if fkey not in self._tailers:
                    t = ByteOffsetTailer(fkey)
                    t._initial_backfill_done = getattr(self, "_cached_backfill_value", False)
                    self._tailers[fkey] = t
        except Exception:
            log.debug("桥目录扫描异常", exc_info=True)

    def read_new_lines(self, max_lines: int | None = None) -> list[str]:
        if max_lines is not None and max_lines <= 0:
            return []
        now = time.time()
        self._scan(now)
        lines: list[str] = []
        remaining = max_lines
        for key in sorted(self._tailers):
            tailer = self._tailers[key]
            batch = tailer.read_new_lines(max_lines=remaining)
            lines.extend(batch)
            if remaining is not None:
                remaining -= len(batch)
                if remaining <= 0:
                    break
        return lines


class ByteOffsetTailer:
    """有界 Byte-Offset 文件增量行读取器。

    特性：
    - 记录上次读取的 byte offset；
    - 启动时若 offset 为 0 且文件已有内容，执行 backfill 防护（移动到末尾），防止重放历史事件；
    - 文件截断/轮转（当前大小 < offset）时安全重置到头部；
    - 单次读取最大字节数有界（如 64KB），防止大文件卡顿；
    - 零外部依赖，毫秒级读取。
    """

    def __init__(self, file_path: Path | str, max_chunk_bytes: int = 65536) -> None:
        self.file_path = Path(file_path)
        self.offset: int = 0
        self.max_chunk_bytes = max_chunk_bytes
        self._initial_backfill_done = False
        self._partial: bytes = b""  # 跨读取边界的未完成行缓冲（防止半行被丢弃）
        self._pending_lines: list[str] = []
        self._discard_until_newline = False  # 超长行丢弃模式：跳到下一个换行再恢复
        self._file_id: tuple[int, ...] | None = None  # 文件身份（Win: ino+ctime_ns / POSIX: dev+ino），识别同路径轮转新文件

    def reset(self) -> None:
        self.offset = 0
        self._initial_backfill_done = False
        self._partial = b""
        self._pending_lines.clear()
        self._discard_until_newline = False
        self._file_id = None

    def read_new_lines(self, max_lines: int | None = None) -> list[str]:
        """读取新增完整行，并可用 *max_lines* 限制单次返回量。

        半行处理：若读取末尾不是换行符（行被 chunk 截断或写入方尚未写完），
        未完成部分存入 _partial，下次读取时拼回——绝不把半行当整行解析。"""
        if max_lines is not None and max_lines <= 0:
            return []
        if self._pending_lines:
            if max_lines is None or len(self._pending_lines) <= max_lines:
                lines = self._pending_lines
                self._pending_lines = []
                return lines
            lines = self._pending_lines[:max_lines]
            del self._pending_lines[:max_lines]
            return lines
        if not self.file_path.is_file():
            return []

        try:
            st = self.file_path.stat()
            size = st.st_size
            # 文件身份识别（应对 bridge rename 轮转出同路径新文件）：
            # Windows 用 (ino, ctime_ns)——ctime 是创建时间，追加不变、轮转变化；
            # POSIX 的 ctime 是 inode 变更时间（每次追加都变），只能用 (dev, ino)。
            if os.name == "nt":
                file_id = (st.st_ino, st.st_ctime_ns)
            else:
                file_id = (st.st_dev, st.st_ino)
        except (OSError, AttributeError):
            return []

        # 启动时的首次初始化：若未指定 offset 则跳至当前末尾（backfill 防护）
        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = size
            self._file_id = file_id
            self._partial = b""
            return []

        # 文件被截断，或被轮换成同路径的新文件（bridge rename 后新文件可能
        # 在下次轮询前就长到不小于旧 offset，只看 size 会永久跳过新文件前部）
        if size < self.offset or (self._file_id is not None and file_id != self._file_id):
            self.offset = 0
            self._partial = b""
            self._discard_until_newline = False  # 旧文件的超长行丢弃状态不得泄漏进新文件
        self._file_id = file_id

        if size == self.offset:
            return []

        bytes_to_read = min(size - self.offset, self.max_chunk_bytes)
        try:
            with open(self.file_path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(bytes_to_read)
                self.offset = f.tell()
        except OSError as exc:
            log.warning("读取 tail 文件失败 %s: %s", self.file_path, exc)
            return []

        chunk = self._partial + chunk

        # 超长行丢弃模式：上个 chunk 已确认某行超过上限，跳到下一个换行再恢复
        if self._discard_until_newline:
            idx = chunk.find(b"\n")
            if idx == -1:
                return []
            chunk = chunk[idx + 1 :]
            self._discard_until_newline = False

        if chunk and not chunk.endswith(b"\n"):
            # 末尾是不完整的半行：留到下次拼接
            idx = chunk.rfind(b"\n")
            if idx == -1:
                self._partial = chunk
                chunk = b""
            else:
                self._partial = chunk[idx + 1 :]
                chunk = chunk[: idx + 1]
            # 防呆：单行超过上限时进入丢弃模式（跳过该超长行剩余部分，
            # 避免把它的"后半截"误当成一条新事件解析）
            if len(self._partial) > self.max_chunk_bytes:
                log.warning("tail 行超过 %d 字节上限，丢弃该超长行: %s", self.max_chunk_bytes, self.file_path)
                self._partial = b""
                self._discard_until_newline = True
        else:
            self._partial = b""

        # utf-8-sig：兼容 PowerShell Add-Content -Encoding UTF8 在文件首行写入的 BOM
        text = chunk.decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if max_lines is not None and len(lines) > max_lines:
            self._pending_lines = lines[max_lines:]
            return lines[:max_lines]
        return lines


@dataclass(frozen=True)
class AgentSourceSpec:
    """A read-only JSONL source assigned to one Agent key."""

    agent_key: str
    path: str
    kind: str = "file"
    pattern: str = ""
    agent_name: str = ""
    scan_interval: float = 5.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AgentSourceSpec":
        agent_key = str(data.get("agent_key") or data.get("agent") or "").strip()
        path = str(data.get("path") or "").strip()
        kind = str(data.get("kind") or "file").strip().lower()
        if not agent_key or not path or kind not in {"file", "glob"}:
            raise ValueError("source requires agent_key, path and kind=file|glob")
        return cls(
            agent_key=agent_key,
            path=path,
            kind=kind,
            pattern=str(data.get("pattern") or "") if kind == "glob" else "",
            agent_name=str(data.get("agent_name") or agent_key),
            scan_interval=max(0.05, float(data.get("scan_interval", 5.0))),
        )


class AgentLinkEventSource:
    """Poll and normalize configured Agent JSONL sources without Qt or UI."""

    DEFAULT_MAX_EVENTS_PER_POLL = 256

    def __init__(
        self,
        sources: list[Mapping[str, Any]] | None = None,
        *,
        generation: int = 0,
        max_files: int = 64,
        max_events_per_poll: int = DEFAULT_MAX_EVENTS_PER_POLL,
    ) -> None:
        self.generation = int(generation)
        self.max_files = max(1, int(max_files))
        self.max_events_per_poll = max(1, int(max_events_per_poll))
        self._poll_cap_hits = 0
        self.paused = False
        self._specs: list[AgentSourceSpec] = []
        self._tailers: dict[str, ByteOffsetTailer | DirGlobTailer] = {}
        self.diagnostics: list[dict[str, Any]] = []
        self.configure(sources or [], generation=generation)

    @property
    def specs(self) -> tuple[AgentSourceSpec, ...]:
        return tuple(self._specs)

    def configure(self, sources: list[Mapping[str, Any]], *, generation: int | None = None) -> None:
        if generation is not None:
            self.generation = int(generation)
        specs = [AgentSourceSpec.from_mapping(item) for item in sources]
        self._specs = specs
        keep: dict[str, ByteOffsetTailer | DirGlobTailer] = {}
        for spec in specs:
            key = self._tailer_key(spec)
            old = self._tailers.get(key)
            if old is not None:
                keep[key] = old
            elif spec.kind == "glob":
                keep[key] = DirGlobTailer(spec.path, pattern=spec.pattern or "dsh*.jsonl", scan_interval=spec.scan_interval, max_files=self.max_files)
            else:
                keep[key] = ByteOffsetTailer(spec.path)
        self._tailers = keep
        self.paused = False

    def prime(self) -> None:
        """Initialize current tailers without emitting existing backlog.

        The worker sends ``ready`` only after this step.  That removes the
        startup race where records appended between ``config_push`` and the
        first polling cycle could be mistaken for pre-existing backlog and
        silently skipped by the initial-backfill guard.
        """
        for spec in self._specs:
            tailer = self._tailers.get(self._tailer_key(spec))
            if tailer is None:
                continue
            try:
                tailer.read_new_lines()
            except Exception as exc:
                self._diagnostic("tail", str(exc), agent_key=spec.agent_key, path=spec.path)

    @staticmethod
    def _tailer_key(spec: AgentSourceSpec) -> str:
        return f"{spec.kind}:{spec.agent_key}:{spec.path}:{spec.pattern}"

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def reset(self) -> None:
        for tailer in self._tailers.values():
            tailer.reset()

    def _diagnostic(self, stage: str, reason: str, *, agent_key: str = "", path: str = "") -> None:
        item = {"stage": stage, "reason": reason}
        if agent_key:
            item["agent_key"] = agent_key
        if path:
            item["path"] = path
        self.diagnostics.append(item)
        del self.diagnostics[:-100]

    @staticmethod
    def _flatten_record(data: dict[str, Any]) -> dict[str, Any]:
        nested = data.get("data")
        if isinstance(nested, dict):
            flattened = dict(data)
            flattened.update(nested)
            return flattened
        return data

    def poll(self, max_events: int | None = None) -> list[dict[str, Any]]:
        if self.paused:
            return []
        limit = self.max_events_per_poll if max_events is None else max(1, int(max_events))
        events: list[dict[str, Any]] = []
        for spec in self._specs:
            if len(events) >= limit:
                break
            tailer = self._tailers.get(self._tailer_key(spec))
            if tailer is None:
                continue
            try:
                lines = tailer.read_new_lines(max_lines=limit - len(events))
            except Exception as exc:
                self._diagnostic("tail", str(exc), agent_key=spec.agent_key, path=spec.path)
                continue
            for line in lines:
                if len(events) >= limit:
                    break
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        self._diagnostic("parse", "record root is not an object", agent_key=spec.agent_key, path=spec.path)
                        continue
                    record = self._flatten_record(raw)
                    # Keep the worker output bounded even when an upstream log line is not.
                    record = bounded_data(record)
                    event = parse_agent_event(record, source_hint=spec.agent_key, agent_name_hint=spec.agent_name or spec.agent_key)
                    semantic = normalize_event(event)
                    item: dict[str, Any] = {
                        "agent": spec.agent_key,
                        "generation": self.generation,
                        "record": record,
                        "event": str(record.get("event") or record.get("type") or ""),
                        "state": str(record.get("state") or ""),
                        "tool": str(record.get("tool") or "").strip(),
                    }
                    if semantic is not None:
                        item["semantic"] = asdict(semantic)
                    events.append(item)
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._diagnostic("parse", str(exc), agent_key=spec.agent_key, path=spec.path)
                except Exception as exc:
                    self._diagnostic("normalize", str(exc), agent_key=spec.agent_key, path=spec.path)
            if len(events) >= limit:
                self._poll_cap_hits += 1
                if self._poll_cap_hits == 1 or self._poll_cap_hits % 256 == 0:
                    self._diagnostic(
                        "backpressure",
                        f"event batch capped at {limit}; hit={self._poll_cap_hits}",
                        agent_key=spec.agent_key,
                        path=spec.path,
                    )
                break
        return events

    def drain_diagnostics(self) -> list[dict[str, Any]]:
        items = list(self.diagnostics)
        self.diagnostics.clear()
        return items
