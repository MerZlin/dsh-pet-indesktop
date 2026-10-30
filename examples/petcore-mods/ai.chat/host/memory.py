"""AI 对话长期记忆：可读 JSONL、零依赖、按中文 bigram 检索。

两个域：``global``（所有角色共享）与 ``character``（当前角色）。写入只来自
用户明确说「记住…」的句子；读取按用户本轮问题做 bigram 重叠打分，取 top-K
注入 system。刻意不做向量库：条目少、可手改、断网可用。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

MAX_CONTENT = 500
MAX_ENTRIES = 500
MAX_QUERY = 400
_BAD_NAME = re.compile(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+")
_MEMORY_RE = re.compile(
    r"(?:请你?|帮我)?(?:记住|记一下|记下|别忘了|以后)[:：,，\s]*([^\n]{2,500})"
)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: str
    scope: str
    content: str
    keys: tuple[str, ...]
    created_at: float
    last_hit: float
    hits: int


def extract_memory(text: str) -> str:
    match = _MEMORY_RE.search(str(text or ""))
    if not match:
        return ""
    content = match.group(1).strip(" 。.,，！!？?；;")
    if not 2 <= len(content) <= MAX_CONTENT:
        return ""
    return content


def _safe_name(value: object) -> str:
    name = _BAD_NAME.sub("_", str(value or "").strip())[:64]
    return name or "default"


def _bigrams(text: str) -> set[str]:
    value = "".join(char for char in str(text or "").lower() if char.isalnum())
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _score(query: str, entry: MemoryEntry) -> float:
    query_grams = _bigrams(query)
    if not query_grams:
        return 0.0
    content_grams = _bigrams(entry.content)
    overlap = len(query_grams & content_grams)
    keys = sum(1 for key in entry.keys if key and key in query)
    return overlap / max(1, len(query_grams)) + keys * 0.5


class MemoryStore:
    def __init__(
        self,
        root: Path | str,
        character_id: object = "default",
        *,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self.root = Path(root)
        self.character_id = _safe_name(character_id)
        self.max_entries = max(10, min(2000, int(max_entries)))

    def _path(self, scope: str) -> Path:
        name = "global.jsonl" if scope == "global" else f"{self.character_id}.jsonl"
        return self.root / name

    def _read(self, scope: str) -> list[MemoryEntry]:
        path = self._path(scope)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[MemoryEntry] = []
        for line in lines:
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if not isinstance(doc, dict):
                continue
            content = str(doc.get("content") or "").strip()
            if not content:
                continue
            keys = doc.get("keys")
            entries.append(
                MemoryEntry(
                    id=str(doc.get("id") or uuid.uuid4().hex),
                    scope=scope,
                    content=content[:MAX_CONTENT],
                    keys=tuple(str(k) for k in keys) if isinstance(keys, list) else (),
                    created_at=float(doc.get("created_at") or 0.0),
                    last_hit=float(doc.get("last_hit") or 0.0),
                    hits=int(doc.get("hits") or 0),
                )
            )
        return entries

    def _write(self, scope: str, entries: list[MemoryEntry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(scope)
        temp = path.with_suffix(path.suffix + ".tmp")
        lines = []
        for entry in entries:
            lines.append(
                json.dumps(
                    {
                        "id": entry.id,
                        "content": entry.content,
                        "keys": list(entry.keys),
                        "created_at": entry.created_at,
                        "last_hit": entry.last_hit,
                        "hits": entry.hits,
                    },
                    ensure_ascii=False,
                )
            )
        temp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(temp, path)

    def load(self, scope: str = "character") -> list[MemoryEntry]:
        return self._read(scope)

    def add(self, content: str, *, scope: str = "character", keys=()) -> MemoryEntry | None:
        value = str(content or "").strip()[:MAX_CONTENT]
        if not value:
            return None
        entries = self._read(scope)
        now = time.time()
        for index, entry in enumerate(entries):
            if entry.content == value:
                updated = MemoryEntry(
                    entry.id,
                    entry.scope,
                    entry.content,
                    entry.keys,
                    entry.created_at,
                    now,
                    entry.hits + 1,
                )
                entries[index] = updated
                self._write(scope, entries)
                return updated
        entry = MemoryEntry(
            uuid.uuid4().hex,
            scope,
            value,
            tuple(str(key)[:40] for key in keys if str(key).strip()),
            now,
            now,
            0,
        )
        entries.append(entry)
        if len(entries) > self.max_entries:
            entries.sort(key=lambda item: (item.last_hit, item.created_at), reverse=True)
            entries = entries[: self.max_entries]
        self._write(scope, entries)
        return entry

    def search(self, text: str, *, scope: str = "character", limit: int = 3) -> list[MemoryEntry]:
        query = str(text or "").strip()[:MAX_QUERY]
        entries = self._read(scope)
        if not entries:
            return []
        if not query:
            return sorted(entries, key=lambda item: item.last_hit, reverse=True)[:limit]
        scored = [(score, entry) for entry in entries if (score := _score(query, entry)) > 0]
        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], item[1].last_hit), reverse=True)
        return [entry for _score_value, entry in scored[: max(1, int(limit))]]

    def search_all(
        self, text: str, *, limit: int = 3, include_global: bool = True
    ) -> list[MemoryEntry]:
        found: list[MemoryEntry] = []
        found.extend(self.search(text, scope="character", limit=limit))
        if include_global:
            found.extend(self.search(text, scope="global", limit=limit))
        seen: set[str] = set()
        unique: list[MemoryEntry] = []
        for entry in found:
            if entry.content in seen:
                continue
            seen.add(entry.content)
            unique.append(entry)
        return unique[: max(1, int(limit))]

    def clear(self, scope: str | None = None) -> int:
        removed = 0
        for name in ("global", "character") if scope is None else (scope,):
            removed += len(self._read(name))
            try:
                self._path(name).unlink(missing_ok=True)
            except OSError:
                pass
        return removed

    def count(self, scope: str = "character") -> int:
        return len(self._read(scope))


def format_context(entries: list[MemoryEntry]) -> str:
    lines = ["长期记忆（供参考；不要逐条复述，除非与问题直接相关）："]
    for entry in entries:
        lines.append(f"- {entry.content}")
    return "\n".join(lines)[:800]
