# -*- coding: utf-8 -*-
"""identity-memory facts.jsonl 适配层（petcore 移植版：纯 Python、零 Qt、零网络）。

与 DSH 版 dsh-identity-relay 的 ``lib/store.mjs`` 逐字段对齐：

- 行格式：每行一个 JSON 对象，字段 id/time/content/source/status/subject/
  supersededBy/retractNote/related/archivedBy。
- 写语义：subject 给了就「同键新压旧」（同 subject 的 active 旧条盖章作废，
  不删行）；内容完全相同才去重（只拦 active）；密钥模式命中即拒写。
- 读语义：recall 按来源可信度优先、再按时间新→旧；硬上限 20 条；关联带出 ≤3 条。

已知边界（与 DSH 版 README Q8 一致）：写锁只在进程内生效。两个进程同时写
同一个库，后写覆盖先写。多宿主共用时错开写时间，或单侧写、另一侧只读。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

FACTS_FILE = "facts.jsonl"
SOURCE_RANK = {"owner": 5, "self": 4, "evidence": 3, "tool": 2, "unknown": 1, "inferred": 1}
RECALL_HARD_LIMIT = 20
RECALL_RELATED_MAX = 3
RELATED_MAX = 5
IDENTITY_TEXT_MAX_CHARS = 4000

SECRET_PATTERNS = [
    re.compile(r"sk-[a-z0-9_-]{10,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key|access[_-]?key|secret|client[_-]?secret)\s*[:=]\s*\S{6,}", re.IGNORECASE),
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S{6,}", re.IGNORECASE),
    re.compile(r"(bearer\s+)?(access[_-]?token|auth[_-]?token|api[_-]?token|token)\s*[:=]\s*[\w-]{16,}", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+(RSA|EC|OPENSSH|PGP|DSA)\s+PRIVATE\s+KEY-----", re.IGNORECASE),
]

_WRITE_LOCKS: dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _lock_for(dir_path: str) -> threading.Lock:
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(str(dir_path), threading.Lock())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def looks_like_secret(text: str) -> bool:
    return any(p.search(str(text or "")) for p in SECRET_PATTERNS)


def _read_lines(path: Path) -> list[str]:
    try:
        return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def parse_facts(dir_path) -> list[dict]:
    """读库：坏行跳过，老行补默认字段（与 store.mjs parseFacts 同口径）。"""
    facts: list[dict] = []
    for line in _read_lines(Path(dir_path) / FACTS_FILE):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        content = str(obj.get("content") or "").strip()
        if not content:
            continue
        obj["content"] = content
        obj["source"] = str(obj.get("source") or "unknown") or "unknown"
        obj["status"] = obj.get("status") if isinstance(obj.get("status"), str) and obj["status"] else "active"
        if not isinstance(obj.get("time"), str) or not obj["time"]:
            obj["time"] = ""
        if isinstance(obj.get("subject"), str) and obj["subject"]:
            obj["subject"] = obj["subject"]
        else:
            obj.pop("subject", None)
        if isinstance(obj.get("supersededBy"), str) and obj["supersededBy"]:
            obj["supersededBy"] = obj["supersededBy"]
        else:
            obj.pop("supersededBy", None)
        if isinstance(obj.get("archivedBy"), str) and obj["archivedBy"]:
            obj["archivedBy"] = obj["archivedBy"]
        else:
            obj.pop("archivedBy", None)
        if not isinstance(obj.get("related"), list):
            obj.pop("related", None)
        facts.append(obj)
    return facts


def _write_lines(dir_path, lines: list[str]) -> None:
    path = Path(dir_path) / FACTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def append_fact(
    dir_path,
    content,
    *,
    source: str = "inferred",
    subject: str | None = None,
    related=None,
    max_facts: int = 2000,
) -> dict:
    """追加一条记忆（对齐 store.mjs appendFact，返回 {appended, entry, error|duplicated|unresolved}）。"""
    entry = {
        "id": uuid.uuid4().hex,
        "time": _utcnow_iso(),
        "content": str(content or "").strip(),
        "source": str(source or "inferred") or "inferred",
        "status": "active",
    }
    if isinstance(subject, str) and subject.strip():
        entry["subject"] = subject.strip()
    if not entry["content"]:
        return {"appended": False, "entry": entry, "error": "内容为空，未写入。"}
    if looks_like_secret(entry["content"]):
        return {"appended": False, "entry": entry, "error": "疑似明文凭据，拒绝写入（宁拦错，不放行）。密钥/密码只进钥匙串，记忆只存指针。"}
    with _lock_for(str(dir_path)):
        lines = _read_lines(Path(dir_path) / FACTS_FILE)
        unresolved: list[str] = []
        if related:
            active = []
            for line in lines:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict) and (o.get("status") or "active") == "active" and isinstance(o.get("content"), str):
                    active.append(o)
            ids: list[str] = []
            for raw in list(related)[:8]:
                s = str(raw or "").strip()
                if not s:
                    continue
                by_id = next((o for o in active if o.get("id") == s), None)
                if by_id:
                    if by_id["id"] not in ids and by_id["id"] != entry["id"]:
                        ids.append(by_id["id"])
                    continue
                by_content = [o for o in active if s in o["content"]][-1:] or []
                if by_content and by_content[0]["id"] not in ids and by_content[0]["id"] != entry["id"]:
                    ids.append(by_content[0]["id"])
                else:
                    unresolved.append(s)
            if ids:
                entry["related"] = ids[:RELATED_MAX]
        # 同键新压旧
        if entry.get("subject"):
            for i, line in enumerate(lines):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("subject") == entry["subject"] and (obj.get("status") or "active") == "active":
                    obj["status"] = "superseded"
                    obj["supersededBy"] = entry["id"]
                    obj["retractNote"] = f"被同键新记忆取代（subject={entry['subject']}）"
                    lines[i] = json.dumps(obj, ensure_ascii=False)
        # 完全相同内容去重（只拦 active）
        for line in lines:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("content") == entry["content"] and (o.get("status") or "active") == "active":
                return {"appended": False, "entry": entry, "duplicated": True}
        lines.append(json.dumps(entry, ensure_ascii=False))
        cap = max(100, min(int(max_facts) if max_facts else 2000, 100000))
        kept = lines[-cap:] if len(lines) > cap else lines
        _write_lines(dir_path, kept)
        return {"appended": True, "entry": entry, "unresolved": unresolved}


def retract_fact(dir_path, query: str, *, note: str = "") -> dict:
    """按 content 片段从后往前匹配最近一条 active，标 superseded（不删行）。"""
    q = str(query or "").strip()
    if not q:
        return {"found": False, "error": "没有给出要作废的内容片段。"}
    with _lock_for(str(dir_path)):
        lines = _read_lines(Path(dir_path) / FACTS_FILE)
        for i in range(len(lines) - 1, -1, -1):
            try:
                obj = json.loads(lines[i])
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("content"), str):
                continue
            if (obj.get("status") or "active") == "active" and q in obj["content"]:
                obj["status"] = "superseded"
                obj["retractNote"] = note or "（未写原因）"
                obj["retractedAt"] = _utcnow_iso()
                lines[i] = json.dumps(obj, ensure_ascii=False)
                _write_lines(dir_path, lines)
                return {"found": True, "entry": obj}
        return {"found": False}


def read_facts(dir_path, limit: int = 20, all: bool = False) -> list[dict]:
    facts = parse_facts(dir_path)
    pool = facts if all else [f for f in facts if f["status"] == "active"]
    n = max(1, min(int(limit) if limit else 20, 1000))
    return pool[-n:][::-1]


def recall_facts(dir_path, query: str = "", limit: int = 10, all: bool = False) -> list[dict]:
    """对齐 store.mjs recallFacts：命中带关联（via='related'，≤3，不占检索上限）。"""
    facts = parse_facts(dir_path)
    pool = facts if all else [f for f in facts if f["status"] == "active"]
    n = max(1, min(int(limit) if limit else 10, RECALL_HARD_LIMIT))
    q = str(query or "").strip().lower()
    hit = lambda f: (q in f["content"].lower()) or (isinstance(f.get("subject"), str) and q in f["subject"].lower())
    matched = pool if q == "" else [f for f in pool if hit(f)]
    ranked = sorted(
        matched,
        key=lambda f: (SOURCE_RANK.get(f["source"], 1), f.get("time") or ""),
        reverse=True,
    )
    picked = ranked[:n]
    if q == "":
        return picked
    seen = {f.get("id") for f in picked if f.get("id")}
    extras: list[dict] = []
    for f in picked:
        for rid in f.get("related") or []:
            if len(extras) >= RECALL_RELATED_MAX:
                break
            if rid in seen:
                continue
            target = next((x for x in pool if x.get("id") == rid), None)
            if not target:
                continue
            seen.add(rid)
            extras.append({**target, "via": "related"})
        if len(extras) >= RECALL_RELATED_MAX:
            break
    return picked + extras


def store_stats(dir_path) -> dict:
    facts = parse_facts(dir_path)
    return {
        "factCount": len(facts),
        "activeCount": sum(1 for f in facts if f["status"] == "active"),
        "supersededCount": sum(1 for f in facts if f["status"] == "superseded"),
        "archivedCount": sum(1 for f in facts if f["status"] == "archived"),
    }


def read_identity_text(dir_path, name: str, *, max_chars: int = IDENTITY_TEXT_MAX_CHARS) -> str:
    """读人格卡/交接书（persona.md / handover.md）；缺文件或读不到返回空串。"""
    if not isinstance(name, str) or not name.strip():
        return ""
    try:
        text = (Path(dir_path) / name.strip()).read_text(encoding="utf-8")
    except OSError:
        return ""
    return text.strip()[:max_chars]
