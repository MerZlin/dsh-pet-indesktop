"""分层人格记忆：append-only inbox → 单写者衰减卡 → 预算内渲染块。

设计取自 surfcore 的可复用部分（不搬 Node 实现）：

- 跨进程只追加 ``inbox.jsonl``，只有本模块的 ``ingest`` 改卡；
- 卡内条目有重要度、情绪浓度、清晰度、pin、召回次数；
- 渲染按分区优先级和字数预算裁剪，全量档案只追加、永不进提示词；
- 去重键 = 日期 + 发送者 + 文本（同一天同一句只入卡一次）。

所有函数都是纯逻辑/文件 IO，零 Qt、零网络。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

MEMORY_CARD_VERSION = 1
MAX_TEXT = 500
MAX_TITLE = 80
MAX_ENTRIES = 1000
CLARITY_LEVELS = ("vivid", "clear", "fuzzy", "trace")
PLATFORMS = ("pet", "dsh", "qq")
SCOPES = ("pet", "dsh", "qq", "shared")

_SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{8,}|bearer\s+[a-z0-9._-]{8,}|api[_-]?key\s*[=:]\s*\S+|"
    r"access[_-]?token\s*[=:]\s*\S+|[a-f0-9]{32,})"
)
_EMOTION_WORDS = (
    "喜欢",
    "开心",
    "高兴",
    "难过",
    "伤心",
    "生气",
    "害怕",
    "讨厌",
    "爱",
    "想念",
    "担心",
    "焦虑",
    "累",
    "哭",
    "笑",
    "委屈",
    "感动",
    "期待",
)
_FIRST_WORDS = ("第一次", "最初", "最早", "刚开始", "初次")
_PROMISE_WORDS = ("答应", "约定", "说好", "保证", "下次", "以后")
_CONFLICT_WORDS = ("对不起", "抱歉", "吵架", "冲突", "误会", "冷战")
_SELF_WORDS = ("你", "桌宠", "鲸鱼", "小家伙")


@dataclass
class MemoryEntry:
    id: str
    source: str = "pet"
    platform: str = "pet"
    scope: str = "pet"
    kind: str = "event"
    title: str = ""
    text: str = ""
    importance: int = 8
    emotional: int = 0
    pinned: bool = False
    clarity: str = "vivid"
    created_at: float = 0.0
    last_seen_at: float = 0.0
    decay_steps: int = 0
    recall_count: int = 0
    first: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "platform": self.platform,
            "scope": self.scope,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "importance": int(self.importance),
            "emotional": int(self.emotional),
            "pinned": bool(self.pinned),
            "clarity": self.clarity if self.clarity in CLARITY_LEVELS else "vivid",
            "created_at": float(self.created_at or 0.0),
            "last_seen_at": float(self.last_seen_at or 0.0),
            "decay_steps": int(self.decay_steps or 0),
            "recall_count": int(self.recall_count or 0),
            "first": bool(self.first),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "MemoryEntry | None":
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or raw.get("content") or "").strip()[:MAX_TEXT]
        if not text:
            return None
        clarity = str(raw.get("clarity") or "vivid")
        return cls(
            id=str(raw.get("id") or hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]),
            source=str(raw.get("source") or "pet")[:32],
            platform=str(raw.get("platform") or "pet")[:16],
            scope=str(raw.get("scope") or "pet")[:16],
            kind=str(raw.get("kind") or "event")[:24],
            title=str(raw.get("title") or "")[:MAX_TITLE],
            text=text,
            importance=_clamp(int(raw.get("importance") or 8), 1, 100),
            emotional=_clamp(int(raw.get("emotional") or 0), 0, 24),
            pinned=bool(raw.get("pinned")),
            clarity=clarity if clarity in CLARITY_LEVELS else "vivid",
            created_at=float(raw.get("created_at") or 0.0),
            last_seen_at=float(raw.get("last_seen_at") or 0.0),
            decay_steps=int(raw.get("decay_steps") or 0),
            recall_count=int(raw.get("recall_count") or 0),
            first=bool(raw.get("first")),
        )


@dataclass
class MemoryCard:
    version: int = MEMORY_CARD_VERSION
    character: str = ""
    updated_at: float = 0.0
    entries: list[MemoryEntry] = field(default_factory=list)
    first_met: dict | None = None
    peak_level: int = 0

    def as_dict(self) -> dict:
        return {
            "version": MEMORY_CARD_VERSION,
            "character": self.character,
            "updated_at": float(self.updated_at or 0.0),
            "entries": [item.as_dict() for item in self.entries],
            "first_met": self.first_met,
            "peak_level": int(self.peak_level or 0),
        }

    @classmethod
    def from_dict(cls, raw: object, *, character: str = "") -> "MemoryCard":
        if not isinstance(raw, dict):
            return cls(character=character)
        entries = []
        for item in raw.get("entries") if isinstance(raw.get("entries"), list) else []:
            entry = MemoryEntry.from_dict(item)
            if entry is not None:
                entries.append(entry)
        first_met = raw.get("first_met")
        return cls(
            character=str(raw.get("character") or character),
            updated_at=float(raw.get("updated_at") or 0.0),
            entries=entries,
            first_met=first_met if isinstance(first_met, dict) else None,
            peak_level=int(raw.get("peak_level") or 0),
        )


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _safe_name(value: object) -> str:
    text = str(value or "").strip()[:64]
    if not text or text in {".", ".."}:
        return "default"
    if any(char in text for char in "/\\:\x00"):
        return "default"
    return text


def redact(text: str) -> str:
    """写盘前脱敏；正文截断到 500 字。"""
    value = str(text or "").strip()
    value = _SECRET_RE.sub("********", value)
    return value[:MAX_TEXT]


def extract_emotion(text: str) -> int:
    value = str(text or "")
    hits = sum(1 for word in _EMOTION_WORDS if word in value)
    return min(24, hits * 4)


def _is_first(text: str) -> bool:
    return any(word in text for word in _FIRST_WORDS)


def _is_promise(text: str) -> bool:
    return any(word in text for word in _PROMISE_WORDS)


def _is_conflict(text: str) -> bool:
    return any(word in text for word in _CONFLICT_WORDS)


def _is_self_ref(text: str) -> bool:
    return any(word in text for word in _SELF_WORDS)


def score_memory(
    text: str,
    *,
    is_owner: bool = False,
    direct: bool = False,
    platform: str = "pet",
) -> tuple[int, list[str], bool]:
    """纯函数评分：返回 ``(importance, reasons, first)``。"""
    value = str(text or "").strip()
    emotional = extract_emotion(value)
    first = _is_first(value)
    score = 8
    reasons = ["基础"]
    if is_owner:
        score += 42 if direct else 32
        reasons.append("主人私聊" if direct else "主人在群里")
    if direct:
        score += 8
        reasons.append("直接对她说")
    if emotional:
        score += emotional
        reasons.append(f"情绪浓度 {emotional}")
    if first:
        score = max(score, 100)
        reasons.append("第一次/最早")
    if _is_promise(value):
        score += 16
        reasons.append("约定")
    if _is_conflict(value):
        score += 14
        reasons.append("矛盾/道歉")
    if _is_self_ref(value):
        score += 5
        reasons.append("关于自己")
    if len(value) < 6:
        score -= 12
        reasons.append("太短")
    return _clamp(score, 1, 100), reasons, first


def _entry_id(day: str, sender: str, text: str) -> str:
    payload = f"{day}|{sender}|{text}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def _day_of(timestamp: float) -> str:
    return _dt.datetime.fromtimestamp(float(timestamp or time.time())).date().isoformat()


class MemoryStore:
    """单个角色的记忆卡；所有写操作都经过本对象（单写者）。"""

    def __init__(self, root: Path, character: str = "default", *, max_entries: int = MAX_ENTRIES) -> None:
        self.root = Path(root)
        self.character = _safe_name(character)
        self.directory = self.root / self.character
        self.inbox_path = self.directory / "inbox.jsonl"
        self.offset_path = self.directory / "inbox.offset"
        self.card_path = self.directory / "card.json"
        self.archive_path = self.directory / "archive.jsonl"
        self.seen_path = self.directory / "seen.json"
        self.max_entries = max(50, int(max_entries))

    # ---------------------------------------------------------------- 卡

    def load_card(self) -> MemoryCard:
        try:
            raw = json.loads(self.card_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return MemoryCard(character=self.character)
        return MemoryCard.from_dict(raw, character=self.character)

    def save_card(self, card: MemoryCard) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        card.character = self.character
        card.updated_at = time.time()
        payload = json.dumps(card.as_dict(), ensure_ascii=False, indent=2) + "\n"
        handle, temp_name = tempfile.mkstemp(
            prefix="card.", suffix=".tmp", dir=str(self.directory)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            os.replace(temp_name, self.card_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    # ---------------------------------------------------------------- inbox

    def append_inbox(self, record: dict) -> None:
        """跨进程唯一写入口：只追加一行，不做读改写。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": float(record.get("at") or time.time()),
            "platform": str(record.get("platform") or "pet")[:16],
            "kind": str(record.get("kind") or "message")[:24],
            "sender": str(record.get("sender") or "")[:80],
            "sender_id": str(record.get("sender_id") or "")[:80],
            "conversation_key": str(record.get("conversation_key") or "")[:200],
            "text": redact(record.get("text") or ""),
            "is_owner": bool(record.get("is_owner")),
            "is_self": bool(record.get("is_self")),
            "direct": bool(record.get("direct", True)),
            "external_id": str(record.get("external_id") or "")[:200],
            "importance": record.get("importance"),
            "source": str(record.get("source") or "")[:32],
            "scope": str(record.get("scope") or "")[:16],
        }
        if not payload["text"]:
            return
        with self.inbox_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _load_seen(self) -> dict[str, float]:
        try:
            raw = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): float(value or 0.0) for key, value in raw.items()}

    def _save_seen(self, seen: dict[str, float]) -> None:
        if len(seen) > 5000:
            keep = sorted(seen.items(), key=lambda item: item[1], reverse=True)[:4000]
            seen = dict(keep)
        payload = json.dumps(seen, ensure_ascii=False, sort_keys=True) + "\n"
        handle, temp_name = tempfile.mkstemp(
            prefix="seen.", suffix=".tmp", dir=str(self.directory)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            os.replace(temp_name, self.seen_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _load_offset(self) -> int:
        try:
            return max(0, int(self.offset_path.read_text(encoding="utf-8").strip() or 0))
        except (OSError, ValueError, TypeError):
            return 0

    def _save_offset(self, value: int) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.offset_path.with_name(self.offset_path.name + ".tmp")
            temp.write_text(f"{max(0, int(value))}\n", encoding="utf-8")
            os.replace(temp, self.offset_path)
        except OSError:
            pass

    def _read_new_lines(self) -> tuple[list[str], int]:
        try:
            size = self.inbox_path.stat().st_size
        except OSError:
            return [], 0
        offset = self._load_offset()
        if offset < 0 or offset > size:
            offset = 0
        try:
            with self.inbox_path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read()
        except OSError:
            return [], offset
        if not data:
            return [], offset
        end = data.rfind(b"\n") + 1
        if end <= 0:
            return [], offset
        chunk = data[:end]
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        return lines, offset + len(chunk)

    def ingest(self, *, now: float | None = None) -> int:
        """把 inbox 的新行并入卡；返回新增条数。"""
        card = self.load_card()
        if not self.inbox_path.is_file():
            return 0
        seen = self._load_seen()
        added = 0
        try:
            lines, next_offset = self._read_new_lines()
        except OSError:
            return 0
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            text = redact(record.get("text") or "")
            if not text:
                continue
            day = _day_of(record.get("at") or time.time())
            sender = str(record.get("sender_id") or record.get("sender") or "")
            key = _entry_id(day, sender, text)
            if key in seen:
                continue
            seen[key] = time.time()
            score, _reasons, first = score_memory(
                text,
                is_owner=bool(record.get("is_owner")),
                direct=bool(record.get("direct", True)),
                platform=str(record.get("platform") or "pet"),
            )
            try:
                override = record.get("importance")
                if override is not None:
                    score = _clamp(int(override), 1, 100)
            except (TypeError, ValueError):
                pass
            platform = str(record.get("platform") or "pet")
            entry = MemoryEntry(
                id=key,
                source=str(record.get("source") or platform),
                platform=platform if platform in PLATFORMS else "pet",
                scope=(
                    str(record.get("scope"))
                    if str(record.get("scope") or "") in SCOPES
                    else (platform if platform in SCOPES else "pet")
                ),
                kind="first" if first else str(record.get("kind") or "event"),
                title=text[:MAX_TITLE],
                text=text,
                importance=score,
                emotional=extract_emotion(text),
                pinned=first,
                clarity="vivid",
                created_at=float(record.get("at") or time.time()),
                last_seen_at=float(now or time.time()),
                first=first,
            )
            card.entries.append(entry)
            if first and card.first_met is None:
                card.first_met = {
                    "text": text,
                    "at": entry.created_at,
                    "entry_id": entry.id,
                }
            added += 1
        if added:
            self._compact(card)
            self.save_card(card)
            self._save_seen(seen)
        self._save_offset(next_offset)
        return added

    # ---------------------------------------------------------------- 维护

    def _compact(self, card: MemoryCard) -> None:
        if len(card.entries) <= self.max_entries:
            card.peak_level = 0
            return
        card.peak_level = max(card.peak_level, 2)
        keep = sorted(
            card.entries,
            key=lambda item: (item.pinned, item.importance, item.last_seen_at),
            reverse=True,
        )[: self.max_entries]
        keep_ids = {item.id for item in keep}
        removed = [item for item in card.entries if item.id not in keep_ids]
        if removed:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.archive_path.open("a", encoding="utf-8", newline="\n") as stream:
                for item in removed:
                    stream.write(json.dumps(item.as_dict(), ensure_ascii=False) + "\n")
        card.entries = keep

    def apply_decay(self, *, days: int = 1, now: float | None = None) -> int:
        card = self.load_card()
        current = float(now or time.time())
        changed = 0
        for entry in card.entries:
            if entry.pinned:
                continue
            age_days = max(0.0, (current - float(entry.last_seen_at or entry.created_at)) / 86400.0)
            if age_days < 7:
                continue
            steps = int(age_days // 14) + max(0, days - 1)
            if steps <= entry.decay_steps:
                continue
            delta = steps - entry.decay_steps
            index = CLARITY_LEVELS.index(entry.clarity) if entry.clarity in CLARITY_LEVELS else 0
            index = min(len(CLARITY_LEVELS) - 1, index + delta)
            entry.decay_steps = steps
            entry.clarity = CLARITY_LEVELS[index]
            changed += 1
        if changed:
            self.save_card(card)
        return changed

    def maintain(self) -> dict:
        added = self.ingest()
        decayed = self.apply_decay()
        card = self.load_card()
        self._compact(card)
        self.save_card(card)
        for scope in SCOPES:
            self.render(scope, max_chars=_scope_budget(scope))
        return {"added": added, "decayed": decayed, "entries": len(card.entries)}

    # ---------------------------------------------------------------- 查询/渲染

    def recall(self, query: str = "", *, scope: str = "pet", limit: int = 3) -> list[MemoryEntry]:
        card = self.load_card()
        allowed = {"shared", scope if scope in SCOPES else "pet"}
        entries = [item for item in card.entries if item.scope in allowed]
        terms = _terms(query)
        def rank(item: MemoryEntry):
            overlap = sum(1 for term in terms if term and term in item.text)
            return (
                overlap * 20
                + (60 if item.first else 0)
                + int(item.importance)
                + min(12, int(item.recall_count) * 2)
            )
        entries.sort(key=rank, reverse=True)
        result = entries[: max(1, int(limit))]
        for item in result:
            item.recall_count += 1
            item.last_seen_at = time.time()
        if result:
            self.save_card(card)
        return result

    def render(
        self,
        scope: str = "pet",
        *,
        query: str = "",
        max_chars: int = 2200,
        max_entries: int = 0,
    ) -> str:
        card = self.load_card()
        allowed = {"shared", scope if scope in SCOPES else "pet"}
        entries = [item for item in card.entries if item.scope in allowed]
        terms = _terms(query)
        if terms:
            entries.sort(
                key=lambda item: sum(1 for term in terms if term in item.text),
                reverse=True,
            )
        sections = _sections(entries)
        lines: list[str] = []
        header = "长期记忆（供参考；不要逐条复述，除非与问题直接相关）："
        budget = max(400, int(max_chars))
        used = len(header)
        used_ids: set[str] = set()
        total_chosen = 0
        # 低优先级先裁：先按 section 顺序尝试，放不下就跳过。
        for title, items, per_section in sections:
            if max_entries and total_chosen >= int(max_entries):
                break
            items = [item for item in items if item.id not in used_ids]
            if not items:
                continue
            chosen: list[str] = []
            for item in items:
                if item.id in used_ids:
                    continue
                if max_entries and total_chosen >= int(max_entries):
                    break
                line = f"- {item.text}"
                if used + len(line) + 1 > budget:
                    break
                chosen.append(line)
                used_ids.add(item.id)
                used += len(line) + 1
                total_chosen += 1
                if len(chosen) >= per_section:
                    break
            if chosen:
                block = f"\n【{title}】\n" + "\n".join(chosen)
                if used + len(block) <= budget:
                    lines.append(block)
                    used += len(block)
        text = header + "".join(lines) if lines else ""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / f"prompt-{scope}.md").write_text(text + "\n", encoding="utf-8")
        except OSError:
            pass
        return text

    def stats(self) -> dict:
        card = self.load_card()
        counts = {level: 0 for level in CLARITY_LEVELS}
        for item in card.entries:
            counts[item.clarity if item.clarity in counts else "vivid"] += 1
        return {
            "character": self.character,
            "entries": len(card.entries),
            "pinned": sum(1 for item in card.entries if item.pinned),
            "first_met": bool(card.first_met),
            "clarity": counts,
            "path": str(self.directory),
        }

    def clear(self, scope: str = "") -> int:
        card = self.load_card()
        before = len(card.entries)
        if scope:
            card.entries = [item for item in card.entries if item.scope != scope]
        else:
            card.entries = []
            card.first_met = None
        self.save_card(card)
        return before - len(card.entries)

    def export(self) -> str:
        return json.dumps(self.load_card().as_dict(), ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- 迁移

    def migrate_legacy(self, paths: list[Path], *, marker: Path) -> int:
        """把旧 AI 记忆 JSONL 导入；marker 存在则不再重复导入。"""
        if marker.is_file():
            return 0
        card = self.load_card()
        existing = {item.text for item in card.entries}
        added = 0
        for path in paths:
            try:
                lines = Path(path).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                text = redact(record.get("content") or record.get("text") or "")
                if not text or text in existing:
                    continue
                existing.add(text)
                scope = "shared" if str(record.get("scope") or "") == "global" else "pet"
                entry = MemoryEntry(
                    id=_entry_id(_day_of(record.get("created_at") or time.time()), "legacy", text),
                    source="legacy",
                    platform="pet",
                    scope=scope,
                    kind="event",
                    title=text[:MAX_TITLE],
                    text=text,
                    importance=40,
                    created_at=float(record.get("created_at") or time.time()),
                    last_seen_at=float(record.get("last_hit") or time.time()),
                    recall_count=int(record.get("hits") or 0),
                )
                card.entries.append(entry)
                added += 1
        if added:
            self._compact(card)
            self.save_card(card)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"imported": added, "at": time.time()}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return added


def _scope_budget(scope: str) -> int:
    return {"pet": 2200, "dsh": 2600, "qq": 2000}.get(scope, 2200)


def _terms(query: str) -> list[str]:
    value = str(query or "").strip().lower()
    if not value:
        return []
    terms = [value]
    terms.extend(
        value[index : index + 2]
        for index in range(max(0, len(value) - 1))
        if value[index : index + 2].strip()
    )
    return list(dict.fromkeys(terms))[:48]


def _sections(entries: list[MemoryEntry]):
    """分区顺序 = 优先级从高到低；每区条数上限。"""
    def pick(predicate):
        items = [item for item in entries if predicate(item)]
        items.sort(key=lambda item: (item.pinned, item.importance, item.last_seen_at), reverse=True)
        return items
    return [
        ("最初", pick(lambda item: item.first or item.kind == "first"), 1),
        ("锚点", pick(lambda item: item.pinned and not (item.first or item.kind == "first")), 5),
        ("和主人", pick(lambda item: item.source in {"qq-owner", "pet-owner"} or item.importance >= 65), 8),
        ("桌宠", pick(lambda item: item.platform == "pet" and item.importance < 65), 4),
        ("DSH", pick(lambda item: item.platform == "dsh"), 4),
        ("QQ", pick(lambda item: item.platform == "qq"), 4),
        ("最近", pick(lambda item: not item.first and not item.pinned), 8),
        ("模糊", pick(lambda item: item.clarity in {"fuzzy", "trace"}), 4),
    ]
