"""外部记忆来源的只读读取器（零 Qt、零网络）。

两个来源：

- ``dsh.memory`` 扩展的 ``<data_root>/memory/facts.jsonl``；
- DSH 工作区的 ``<workspace>/.dsh-meow/memory.db``（meow-memory 基座，
  ``dsh-memory-semantic`` 的上游）。

所有读取都只读：不建表、不写行、不改 schema；失败只返回状态与异常类型，
不抛给调用方，也不记录正文。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .memory import normalize_text, redact

MEOW_TABLES = ("soul", "user", "project", "fact", "lesson", "topic", "rules")
MEOW_COLUMNS = (
    "id",
    "content",
    "title",
    "keywords",
    "project",
    "subcategory",
    "status",
    "updated_at",
)
MAX_ROWS = 5000


@dataclass(frozen=True)
class SourceResult:
    key: str
    label: str
    path: str
    status: str
    records: tuple[dict, ...] = ()
    reason: str = ""


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _timestamp(value: object) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number > 1e12:
        number /= 1000.0
    if number > 0:
        return number
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def read_dsh_facts(data_root: Path) -> SourceResult:
    key = "dsh-facts"
    label = "本机 dsh.memory"
    path = Path(data_root) / "memory" / "facts.jsonl"
    if not path.is_file():
        return SourceResult(key, label, str(path), "not_found", (), "未找到记忆文件")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return SourceResult(key, label, str(path), "error", (), type(exc).__name__)
    records: list[dict] = []
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict) or raw.get("superseded_by"):
            continue
        text = redact(raw.get("text") or "")
        if not text:
            continue
        origin_id = str(
            raw.get("id") or hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        )
        try:
            importance = _clamp(int(raw.get("importance") or 3) * 20, 20, 100)
        except (TypeError, ValueError):
            importance = 60
        created_at = _timestamp(raw.get("created_at"))
        records.append(
            {
                "origin": f"{key}:{origin_id}",
                "text": text,
                "importance": importance,
                "created_at": created_at,
                "updated_at": _timestamp(raw.get("last_used_at")) or created_at,
                "source": "dsh-facts",
                "platform": "dsh",
                "scope": "dsh",
                "kind": "external",
            }
        )
    return SourceResult(key, label, str(path), "ok", tuple(records), "")


def discover_meow_dbs(home: Path | None = None) -> list[Path]:
    base_home = Path(home or Path.home())
    candidates: list[Path] = []
    workspace_file = base_home / ".dsh" / "storages" / "workspace.json"
    try:
        raw = json.loads(workspace_file.read_text(encoding="utf-8"))
        tables = raw.get("tables") if isinstance(raw, dict) else None
        workspaces = tables.get("workspaces") if isinstance(tables, dict) else None
        if isinstance(workspaces, dict):
            for item in workspaces.values():
                if isinstance(item, dict) and item.get("path"):
                    candidates.append(Path(str(item["path"])))
    except (OSError, ValueError, TypeError):
        pass
    for parent in (
        base_home / "Documents" / "deepseek-harness",
        base_home / ".dsh" / "profiles",
    ):
        try:
            candidates.extend(item for item in parent.iterdir() if item.is_dir())
        except OSError:
            pass
    candidates.append(base_home)
    candidates.append(base_home / ".dsh-meow")

    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        possible = [candidate / ".dsh-meow" / "memory.db"]
        if candidate.name == ".dsh-meow":
            possible.append(candidate / "memory.db")
        for db in possible:
            try:
                resolved = db.resolve()
            except OSError:
                resolved = db
            if resolved in seen:
                continue
            seen.add(resolved)
            if db.is_file():
                found.append(db)
    return found


def _uri_path(path: Path) -> str:
    """把本地路径变成 SQLite read-only URI 的 path 部分（不引入 urllib）。"""
    value = path.as_posix()
    for char, escaped in (("%", "%25"), ("?", "%3F"), ("#", "%23"), (" ", "%20")):
        value = value.replace(char, escaped)
    return value


def read_meow_db(path: Path) -> SourceResult:
    path = Path(path)
    try:
        digest = hashlib.sha1(
            str(path.resolve()).lower().encode("utf-8")
        ).hexdigest()[:8]
    except OSError:
        digest = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:8]
    key = f"meow:{digest}"
    label = f"meow-memory · {path.parent.parent.name or 'workspace'}"
    if not path.is_file():
        return SourceResult(key, label, str(path), "not_found", (), "未找到记忆库")
    try:
        uri = "file:" + _uri_path(path) + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        return SourceResult(key, label, str(path), "error", (), type(exc).__name__)
    records: list[dict] = []
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in MEOW_TABLES:
            if table not in tables:
                continue
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            picked = [column for column in MEOW_COLUMNS if column in columns]
            if not picked:
                continue
            where = " WHERE status != 'archived'" if "status" in columns else ""
            query = f"SELECT {', '.join(picked)} FROM {table}{where} LIMIT {MAX_ROWS}"
            for row in connection.execute(query):
                item = dict(zip(picked, row))
                text = redact(item.get("content") or item.get("title") or "")
                if not text:
                    continue
                origin_id = str(
                    item.get("id")
                    or hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
                )
                updated_at = _timestamp(item.get("updated_at"))
                records.append(
                    {
                        "origin": f"{key}:{table}:{origin_id}",
                        "text": text,
                        "importance": 70,
                        "created_at": updated_at,
                        "updated_at": updated_at,
                        "source": f"meow:{table}",
                        "platform": "dsh",
                        "scope": "dsh",
                        "kind": "external",
                    }
                )
    except sqlite3.Error as exc:
        return SourceResult(key, label, str(path), "error", (), type(exc).__name__)
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    return SourceResult(key, label, str(path), "ok", tuple(records), "")


def read_external_sources(
    data_root: Path, home: Path | None = None
) -> list[SourceResult]:
    results = [read_dsh_facts(data_root)]
    for path in discover_meow_dbs(home):
        results.append(read_meow_db(path))
    return results


def assistant_texts(root: Path, character: str) -> set[str]:
    """读取本角色所有会话里的助手原文，供「旧回复标为自述」一次性整理。"""
    value = normalize_text(character)
    if not value:
        return set()
    texts: set[str] = set()
    base_root = Path(root)
    for base in base_root.glob("sessions*"):
        directory = base / character
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            messages = raw.get("messages") if isinstance(raw, dict) else None
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if str(message.get("role") or "") != "assistant":
                    continue
                content = str(message.get("content") or "")
                if not content:
                    continue
                texts.add(normalize_text(content[:500]))
    texts.discard("")
    return texts
