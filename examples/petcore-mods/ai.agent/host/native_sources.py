"""只读、增量、有界的原生 Agent 事件源。

- ``RecursiveJsonlSource``：递归发现 transcript/rollout 文件，按字节偏移 tail；
  首次见到的已有文件跳到末尾（不回放历史），监视开始后新出现的文件从头读。
- ``OpenCodeSource``：只读 OpenCode 本地 SQLite 的 ``event`` 表，rowid 增量；
  启动时 backfill 到末尾，并过滤子代理会话，避免 busy→idle 抖动。

两者都返回 ``[(path, 一行统一协议 JSON)]``，由 runtime 现有的 ``_Source`` 消费。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .tailer import ByteTailer


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _line(agent: str, state: str, tool: str = "") -> bytes:
    payload = {"agent": agent, "state": state}
    if tool:
        payload["tool"] = tool
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class RecursiveJsonlSource:
    """递归目录 + 字节偏移 tail；只读，不创建目录、不写文件。"""

    def __init__(
        self,
        base: Path | str,
        pattern: str,
        agent: str,
        parser,
        *,
        limit: int = 30,
        max_age_s: float | None = None,
        scan_interval: float = 30.0,
    ) -> None:
        self.base = Path(base)
        self.pattern = str(pattern)
        self.agent = str(agent)
        self.parser = parser
        self.limit = max(1, int(limit))
        self.max_age_s = max_age_s
        self.scan_interval = max(1.0, float(scan_interval))
        self._tailers: dict[Path, ByteTailer] = {}
        self._initial = True
        self._last_scan = 0.0

    def paths(self) -> list[Path]:
        try:
            files = [path for path in self.base.glob(self.pattern) if path.is_file()]
        except OSError:
            return []
        if self.max_age_s is not None:
            cutoff = time.time() - float(self.max_age_s)
            files = [path for path in files if _mtime(path) >= cutoff]
        files.sort(key=_mtime, reverse=True)
        return files[: self.limit]

    def _scan(self, *, initial: bool) -> None:
        current = self.paths()
        keep = set(current)
        for stale in [path for path in self._tailers if path not in keep]:
            self._tailers.pop(stale, None)
        for path in current:
            if path not in self._tailers:
                self._tailers[path] = ByteTailer(path, prime_to_end=initial)

    def read_lines(self) -> list[tuple[Path, bytes]]:
        now = time.time()
        if self._initial or now - self._last_scan >= self.scan_interval:
            self._scan(initial=self._initial)
            self._initial = False
            self._last_scan = now
        result: list[tuple[Path, bytes]] = []
        for path in sorted(self._tailers, key=_mtime):
            tailer = self._tailers[path]
            for raw in tailer.read_lines():
                try:
                    record = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                try:
                    state, tool = self.parser(record)
                except Exception:
                    continue
                if not state and not tool:
                    continue
                result.append((path, _line(self.agent, state, tool)))
        return result


class OpenCodeSource:
    """只读 OpenCode SQLite ``event`` 表；rowid 增量、启动 backfill。"""

    def __init__(self, db_path: Path | str, *, batch: int = 200) -> None:
        self.db_path = Path(db_path)
        self.batch = max(1, int(batch))
        self._last_rowid = 0
        self._ready = False
        self._file_id: tuple[int, ...] | None = None

    def paths(self) -> list[Path]:
        return [self.db_path]

    def _connect(self):
        import sqlite3

        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def read_lines(self) -> list[tuple[Path, bytes]]:
        if not self.db_path.is_file():
            self._ready = False
            self._file_id = None
            return []
        try:
            stat = self.db_path.stat()
            file_id = (int(stat.st_dev), int(stat.st_ino))
            if file_id != self._file_id:
                self._file_id = file_id
                self._ready = False
                self._last_rowid = 0
        except OSError:
            return []
        try:
            db = self._connect()
        except Exception:
            return []
        try:
            if not self._ready:
                try:
                    self._last_rowid = int(
                        db.execute(
                            "SELECT COALESCE(MAX(rowid), 0) FROM event"
                        ).fetchone()[0]
                    )
                except Exception:
                    return []
                self._ready = True
                return []
            rows = db.execute(
                "SELECT rowid, type, data FROM event WHERE rowid > ? ORDER BY rowid LIMIT ?",
                (self._last_rowid, self.batch),
            ).fetchall()
            parsed: list[tuple[int, str, dict]] = []
            session_ids: set[str] = set()
            for rowid, event_type, data_raw in rows:
                try:
                    data = json.loads(str(data_raw))
                except (TypeError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                session_id = str(data.get("sessionID") or "")
                if session_id:
                    session_ids.add(session_id)
                parsed.append((int(rowid), str(event_type), data))
            root_sessions: dict[str, bool] = {}
            if session_ids:
                try:
                    marks = ",".join("?" * len(session_ids))
                    for session_id, parent_id in db.execute(
                        f"SELECT id, parent_id FROM session WHERE id IN ({marks})",
                        tuple(session_ids),
                    ):
                        root_sessions[str(session_id)] = parent_id is None
                except Exception:
                    root_sessions = {}
        except Exception:
            return []
        finally:
            try:
                db.close()
            except Exception:
                pass

        from .native import parse_opencode_row

        result: list[tuple[Path, bytes]] = []
        for rowid, event_type, data in parsed:
            self._last_rowid = max(self._last_rowid, rowid)
            session_id = str(data.get("sessionID") or "")
            if session_id and root_sessions and root_sessions.get(session_id) is False:
                continue
            try:
                state, tool = parse_opencode_row(event_type, data)
            except Exception:
                continue
            if not state and not tool:
                continue
            result.append((self.db_path, _line("opencode", state, tool)))
        return result
