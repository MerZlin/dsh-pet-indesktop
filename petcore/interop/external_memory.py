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
import re
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

#: 身份接力的来源可信度 → 本机重要度（0–100）。
#: 刻意低于本地主人事实（score_memory 的主人私聊 100 / remember 80），
#: 否则一次外部库导入会按重要度把用户自己的记忆挤出容量。
IDENTITY_IMPORTANCE = {
    "owner": 75,
    "self": 70,
    "evidence": 60,
    "tool": 50,
    "unknown": 45,
    "inferred": 45,
}
FEIYU_KINDS = ("fact", "event")
#: 单个只读源最多导入多少条；外部库可能几千条，不能让它一次填满整张卡。
MAX_EXTERNAL_RECORDS = 500
_FEIYU_SAFE_NAME = re.compile(r"[^\w.@-]+")


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


def _feiyu_safe_name(value: object) -> str:
    text = _FEIYU_SAFE_NAME.sub("_", str(value or "").strip())[:80]
    if text in {"", ".", ".."}:
        return "__default__"
    return text


def read_identity_facts(identity_dir: Path) -> SourceResult:
    """只读身份接力的 ``facts.jsonl``（与 dsh.memory 的 facts 不是同一种格式）。

    只接受 ``status=active`` 且没有 ``supersededBy`` / ``archivedBy`` 的记录；
    ``content`` 先过本机 ``redact``（脱敏 + 500 字），避免把密钥/长文带进提示词。
    解析出坏行、文件为空或读取过程中大小/mtime 变化时返回 ``partial``：
    ``apply_external`` 只在 ``ok`` 时删除镜像，因此一次坏行/半截写不会把用户
    已有的记忆镜像整段抹掉。
    """
    key = "identity"
    label = "身份接力（只读）"
    root = Path(identity_dir)
    path = root / "facts.jsonl"
    if not path.is_file():
        return SourceResult(key, label, str(path), "not_found", (), "未找到记忆文件")
    try:
        before = path.stat()
        text = path.read_text(encoding="utf-8")
        after = path.stat()
    except (OSError, UnicodeError) as exc:
        return SourceResult(key, label, str(path), "error", (), type(exc).__name__)

    unstable = (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    )
    lines = text.splitlines()

    ordered: list[dict] = []
    seen_origins: set[str] = set()
    seen_text: set[str] = set()
    subject_index: dict[str, int] = {}
    text_by_index: dict[int, str] = {}
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(raw, dict):
            bad += 1
            continue
        if str(raw.get("status") or "active").strip().lower() != "active":
            continue
        if raw.get("supersededBy") or raw.get("archivedBy"):
            continue
        content = redact(raw.get("content") or "")
        if not content:
            continue
        source = str(raw.get("source") or "unknown").strip().lower() or "unknown"
        origin_id = str(
            raw.get("id") or hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        )
        origin = f"{key}:{origin_id}"
        if origin in seen_origins:
            continue
        text_key = normalize_text(content)
        if text_key in seen_text:
            continue
        subject = str(raw.get("subject") or "").strip()[:80]
        created_at = _timestamp(raw.get("time"))
        record = {
            "origin": origin,
            "text": content,
            "title": subject or content[:80],
            "importance": IDENTITY_IMPORTANCE.get(source, 50),
            "created_at": created_at,
            "updated_at": created_at,
            "source": f"identity:{source}"[:32],
            "platform": "dsh",
            "scope": "dsh",
            "kind": "external",
        }
        if subject and subject in subject_index:
            index = subject_index[subject]
            old = ordered[index]
            old_created = float(old.get("created_at") or 0.0)
            # 有时间就按时间；缺时间时按文件顺序（后写覆盖前写）。
            if created_at and old_created and created_at < old_created:
                continue
            seen_text.discard(text_by_index.get(index, ""))
            ordered[index] = record
            text_by_index[index] = text_key
            seen_origins.add(origin)
            seen_text.add(text_key)
            continue
        if subject:
            subject_index[subject] = len(ordered)
        text_by_index[len(ordered)] = text_key
        ordered.append(record)
        seen_origins.add(origin)
        seen_text.add(text_key)

    ordered.sort(key=lambda record: record["created_at"], reverse=True)
    ordered = ordered[:MAX_EXTERNAL_RECORDS]
    reasons: list[str] = []
    if unstable:
        reasons.append("文件在读取过程中变化")
    if not text.strip():
        reasons.append("文件为空或正在重写")
    elif not text.endswith("\n"):
        reasons.append("最后一行可能未写完")
    if bad:
        reasons.append(f"跳过 {bad} 行无法解析")
    status = "partial" if reasons else "ok"
    return SourceResult(key, label, str(path), status, tuple(ordered), "；".join(reasons))


def read_feiyu_logs(
    feiyu_root: Path, session: str = "__default__"
) -> SourceResult:
    """只读 dsh-feiyu-memory-plus-max 的追加日志 ``sessions/<sid>/log.jsonl``。

    日志是 ``add/amend/del/restore/supersede/purge`` 追加 op；这里重放成当前
    条目，只收 ``fact``/``event``（doc/skill 正文不进提示词）。JSON 坏行返回
    ``partial``，不删除已有镜像；未知 op / 指向已不存在 id 的 op 只记 reason，
    不作为坏行，避免一条未来 op 永久冻结该会话的删除。文件不存在返回
    ``not_found``。
    """
    safe = _feiyu_safe_name(session)
    key = f"feiyu:{safe}"
    label = f"feiyu 记忆 · {safe}"
    path = Path(feiyu_root) / "sessions" / safe / "log.jsonl"
    if not path.is_file():
        return SourceResult(key, label, str(path), "not_found", (), "未找到记忆日志")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return SourceResult(key, label, str(path), "error", (), type(exc).__name__)

    entries: dict[str, dict] = {}
    bad = 0
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(raw, dict):
            bad += 1
            continue
        op = str(raw.get("op") or "")
        if op == "add":
            entry = raw.get("e")
            if isinstance(entry, dict) and entry.get("id"):
                entries[str(entry["id"])] = dict(entry)
            else:
                bad += 1
        elif op == "amend":
            oid = str(raw.get("id") or "")
            patch = raw.get("patch")
            if oid in entries and isinstance(patch, dict):
                entries[oid].update(patch)
            else:
                skipped += 1
        elif op in {"del", "restore", "supersede", "purge"}:
            oid = str(raw.get("id") or "")
            if oid in entries:
                entry = entries[oid]
                if op == "restore":
                    entry["dead"] = False
                    entry["supersededBy"] = None
                elif op == "supersede":
                    entry["supersededBy"] = raw.get("by") or None
                    entry["dead"] = True
                elif op == "purge":
                    entry["dead"] = True
                    entry["purged"] = True
                else:
                    entry["dead"] = True
            else:
                skipped += 1
        else:
            # 未来 op：只读镜像不认识它，但不能因此永久冻结删除。
            skipped += 1

    records: list[dict] = []
    for entry_id, entry in entries.items():
        if entry.get("dead") or entry.get("supersededBy"):
            continue
        if str(entry.get("kind") or "event") not in FEIYU_KINDS:
            continue
        content = redact(entry.get("gist") or entry.get("text") or "")
        if not content:
            continue
        try:
            raw_importance = entry.get("importance")
            importance = round(
                float(0.5 if raw_importance is None else raw_importance) * 100
            )
        except (TypeError, ValueError):
            importance = 50
        importance = max(1, min(100, importance))
        if entry.get("milestone"):
            importance = min(100, importance + 10)
        created_at = _timestamp(entry.get("t"))
        source = str(entry.get("src") or "feiyu").strip() or "feiyu"
        records.append(
            {
                "origin": f"{key}:{entry_id}",
                "text": content,
                "title": content[:80],
                "importance": importance,
                "created_at": created_at,
                "updated_at": created_at,
                "source": f"feiyu:{source}"[:32],
                "platform": "dsh",
                "scope": "dsh",
                "kind": "external",
            }
        )

    records.sort(key=lambda record: record["created_at"], reverse=True)
    records = records[:MAX_EXTERNAL_RECORDS]
    reasons: list[str] = []
    if bad:
        reasons.append(f"跳过 {bad} 行无法解析")
    if skipped:
        reasons.append(f"忽略 {skipped} 个未知/过期 op")
    status = "partial" if bad else "ok"
    return SourceResult(key, label, str(path), status, tuple(records), "；".join(reasons))


def read_external_sources(
    data_root: Path,
    home: Path | None = None,
    *,
    identity_dir: Path | None = None,
    feiyu_root: Path | None = None,
    feiyu_session: str = "__default__",
) -> list[SourceResult]:
    results = [read_dsh_facts(data_root)]
    for path in discover_meow_dbs(home):
        results.append(read_meow_db(path))
    if identity_dir is not None:
        results.append(read_identity_facts(Path(identity_dir)))
    if feiyu_root is not None:
        results.append(read_feiyu_logs(Path(feiyu_root), feiyu_session))
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
