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
import functools
import hashlib
import json
import os
import re
import tempfile
import threading
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
_QUESTION_WORDS = (
    "吗",
    "呢",
    "几",
    "什么",
    "谁",
    "哪",
    "怎么",
    "为什么",
    "多少",
    "多久",
    "是不是",
    "有没有",
)


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
    updated_at: float = 0.0
    decay_steps: int = 0
    recall_count: int = 0
    first: bool = False
    superseded_by: str = ""
    origin: str = ""

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
            "updated_at": float(self.updated_at or 0.0),
            "decay_steps": int(self.decay_steps or 0),
            "recall_count": int(self.recall_count or 0),
            "first": bool(self.first),
            "superseded_by": str(self.superseded_by or ""),
            "origin": str(self.origin or ""),
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
            updated_at=float(raw.get("updated_at") or 0.0),
            decay_steps=int(raw.get("decay_steps") or 0),
            recall_count=int(raw.get("recall_count") or 0),
            first=bool(raw.get("first")),
            superseded_by=str(raw.get("superseded_by") or "")[:64],
            origin=str(raw.get("origin") or "")[:200],
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


def _replace_with_retry(source: str, target: Path, *, attempts: int = 5) -> None:
    """Windows 上目标文件正被读取时 ``os.replace`` 会短暂拒绝访问。

    ``os.replace`` 在 POSIX 是原子的；Windows 也不允许覆盖已打开的文件。
    记忆页/状态查询会在同步 worker 落盘时读同一份 ``external.json``，一次
    瞬时 ``PermissionError`` 不该让整轮同步失败。只在 Windows 上短暂退避后
    重试（最长约 0.15s），最后仍失败才抛给调用方（真实权限/磁盘错误不吞）。
    POSIX 上的 PermissionError 不重试，避免无意义地拖住 GUI。
    """
    if os.name != "nt":
        os.replace(source, target)
        return
    for attempt in range(max(1, attempts)):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.01 * (attempt + 1))


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


def _locked(method):
    """把读写卡的方法串行化：GUI 召回、ingest、后台外部同步共用一把 RLock。"""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


_NORMALIZE_DROP = (
    " \t\r\n\u3000，。！？；：、,.!?;:'\"“”‘’（）()【】[]{}<>…—-_/\\|+*="
)
_NORMALIZE_TABLE = str.maketrans("", "", _NORMALIZE_DROP)


def normalize_text(text: object) -> str:
    """去空白/标点/大小写，用于重复与更正判定（不是写盘格式）。

    用 ``str.translate`` 而不是正则：这条路径在每轮 ingest 里会对整张卡
    跑一遍，C 级查表比 500 条 × ``re.sub`` 快一个量级。
    """
    return str(text or "").lower().translate(_NORMALIZE_TABLE)


_CORRECTION_RE = re.compile(
    r"(改(?:成|为|到)|换(?:成|为)|更正|记错|说错|不再|取消|以后不|现在起|更新一下)"
)


def _is_correction(text: object) -> bool:
    return bool(_CORRECTION_RE.search(str(text or "")))


def _longest_common_substring(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for char in left:
        current = [0] * (len(right) + 1)
        for index, other in enumerate(right, 1):
            if char == other:
                length = previous[index - 1] + 1
                current[index] = length
                if length > best:
                    best = length
        previous = current
    return best


def _correction_matches(old_text: object, new_text: object) -> bool:
    """更正只覆盖「明显是同一件事」的旧条目。

    只用 Dice 相似度会把模板相同的不同事实误合并（实测 500 条合成卡会误标 449 条）；
    这里要求新旧文本的最长公共子串 ≥ 4 且覆盖旧文本的 60%：
    「我星期三上课」→「我星期三不上课，改为星期四上课了」命中；「第2条…」→「第3条…」不命中。
    只比较前 80 个规范化字符，避免长文本的 O(n×m) 退化成每轮几百万次比较。
    """
    old_norm = normalize_text(old_text)[:80]
    new_norm = normalize_text(new_text)[:80]
    if len(old_norm) < 4 or not new_norm:
        return False
    if old_norm in new_norm:
        return True
    common = _longest_common_substring(old_norm, new_norm)
    return common >= 4 and common / len(old_norm) >= 0.6


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
        self.external_path = self.directory / "external.json"
        self.max_entries = max(50, int(max_entries))
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- 卡

    def load_card(self) -> MemoryCard:
        try:
            raw = json.loads(self.card_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return MemoryCard(character=self.character)
        return MemoryCard.from_dict(raw, character=self.character)

    @_locked
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
            _replace_with_retry(temp_name, self.card_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    # ---------------------------------------------------------------- inbox

    @_locked
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
            "pinned": bool(record.get("pinned")),
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
            _replace_with_retry(temp_name, self.seen_path)
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
            _replace_with_retry(temp, self.offset_path)
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

    @_locked
    def ingest(self, *, now: float | None = None) -> int:
        """把 inbox 的新行并入卡；返回新增条数。"""
        card = self.load_card()
        if not self.inbox_path.is_file():
            return 0
        seen = self._load_seen()
        added = 0
        changed = 0
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
            created_at = float(record.get("at") or time.time())
            normalized = normalize_text(text)
            duplicate = next(
                (
                    item
                    for item in card.entries
                    if not item.superseded_by
                    and item.kind != "reply"
                    and not item.origin
                    and normalize_text(item.text) == normalized
                ),
                None,
            )
            if duplicate is not None:
                duplicate.last_seen_at = float(now or time.time())
                duplicate.recall_count = int(duplicate.recall_count) + 1
                duplicate.importance = max(int(duplicate.importance), score)
                duplicate.updated_at = float(now or time.time())
                if bool(record.get("pinned")):
                    duplicate.pinned = True
                if str(record.get("scope") or "") == "shared":
                    duplicate.scope = "shared"
                if len(text) > len(duplicate.text):
                    duplicate.text = text
                    duplicate.title = text[:MAX_TITLE]
                changed += 1
                continue
            is_reply = str(record.get("kind") or "") == "reply"
            if (
                not is_reply
                and bool(record.get("is_owner"))
                and not _looks_like_question(text)
                and _is_correction(text)
            ):
                for old in card.entries:
                    if (
                        old.id == key
                        or old.superseded_by
                        or old.kind == "reply"
                        or old.origin
                        or not old.text
                    ):
                        continue
                    if _correction_matches(old.text, text):
                        old.superseded_by = key
                        old.updated_at = float(now or time.time())
                        changed += 1
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
                pinned=first or bool(record.get("pinned")),
                clarity="vivid",
                created_at=created_at,
                last_seen_at=float(now or time.time()),
                updated_at=created_at,
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
        if added or changed:
            self._compact(card)
            self.save_card(card)
        if lines:
            self._save_seen(seen)
        self._save_offset(next_offset)
        return added

    # ---------------------------------------------------------------- 维护

    def _compact(self, card: MemoryCard) -> None:
        if len(card.entries) <= self.max_entries:
            card.peak_level = 0
            return
        card.peak_level = max(card.peak_level, 2)
        def score(item):
            return (item.importance, item.last_seen_at)

        pinned = sorted(
            (item for item in card.entries if item.pinned), key=score, reverse=True
        )[: self.max_entries]
        keep = list(pinned)
        remaining = self.max_entries - len(keep)
        if remaining > 0:
            local = sorted(
                (
                    item
                    for item in card.entries
                    if not item.pinned and not item.origin
                ),
                key=score,
                reverse=True,
            )
            external = sorted(
                (item for item in card.entries if not item.pinned and item.origin),
                key=score,
                reverse=True,
            )
            # 外部镜像不能把用户自己的记忆整批挤出容量：非置顶名额至少
            # 60% 先留给本地条目，剩余再按重要度/最近使用填外部镜像。
            reserve_local = min(len(local), max(0, int(remaining * 0.6)))
            keep.extend(local[:reserve_local])
            remaining = self.max_entries - len(keep)
            if remaining > 0:
                rest = local[reserve_local:] + external
                rest.sort(key=score, reverse=True)
                keep.extend(rest[:remaining])
        keep_ids = {item.id for item in keep}
        removed = [item for item in card.entries if item.id not in keep_ids]
        if removed:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.archive_path.open("a", encoding="utf-8", newline="\n") as stream:
                for item in removed:
                    stream.write(json.dumps(item.as_dict(), ensure_ascii=False) + "\n")
        card.entries = keep

    @_locked
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

    @_locked
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

    @_locked
    def recall(
        self,
        query: str = "",
        *,
        scope: str = "pet",
        limit: int = 3,
        share_channels: bool = True,
    ) -> list[MemoryEntry]:
        card = self.load_card()
        allowed = _visible_scopes(scope, share_channels=share_channels)
        entries = [
            item
            for item in card.entries
            if item.scope in allowed
            and not item.superseded_by
            and item.kind != "reply"
        ]
        terms = _terms(query)
        now = time.time()

        def rank(item: MemoryEntry):
            return _entry_score(item, terms, query, now)

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
        share_channels: bool = True,
    ) -> str:
        card = self.load_card()
        allowed = _visible_scopes(scope, share_channels=share_channels)
        now = time.time()
        terms = _terms(query)
        candidates = [
            item
            for item in card.entries
            if item.scope in allowed
            and not item.superseded_by
            and item.kind != "reply"
        ]
        scored = [(item, _entry_score(item, terms, query, now)) for item in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        scores = {item.id: score for item, score in scored}
        selected = _select_for_render(
            _dedupe_by_text([item for item, _ in scored]), max_entries
        )
        text = _compose_render(selected, scores, max_chars)
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
            "active": sum(1 for item in card.entries if not item.superseded_by),
            "pinned": sum(1 for item in card.entries if item.pinned),
            "superseded": sum(1 for item in card.entries if item.superseded_by),
            "replies": sum(1 for item in card.entries if item.kind == "reply"),
            "external": sum(1 for item in card.entries if item.origin),
            "first_met": bool(card.first_met),
            "clarity": counts,
            "path": str(self.directory),
        }

    @_locked
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

    # ---------------------------------------------------------------- 用户编辑

    def _archive_entry(self, entry: MemoryEntry, reason: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = entry.as_dict()
        payload["reason"] = str(reason)
        payload["archived_at"] = time.time()
        try:
            with self.archive_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def entries(
        self,
        *,
        scope: str = "",
        limit: int = 0,
        include_superseded: bool = False,
        include_replies: bool = False,
    ) -> list[MemoryEntry]:
        card = self.load_card()
        items = list(card.entries)
        if scope:
            items = [item for item in items if item.scope == scope]
        if not include_superseded:
            items = [item for item in items if not item.superseded_by]
        if not include_replies:
            items = [item for item in items if item.kind != "reply"]
        items.sort(
            key=lambda item: (
                bool(item.pinned),
                int(item.importance),
                float(item.updated_at or item.created_at or 0.0),
            ),
            reverse=True,
        )
        if limit:
            items = items[: max(1, int(limit))]
        return items

    def find_entry(self, entry_id: str) -> MemoryEntry | None:
        wanted = str(entry_id or "")
        if not wanted:
            return None
        return next(
            (item for item in self.load_card().entries if item.id == wanted), None
        )

    @_locked
    def update_entry(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        importance: int | None = None,
        pinned: bool | None = None,
        scope: str | None = None,
    ) -> bool:
        card = self.load_card()
        entry = next((item for item in card.entries if item.id == str(entry_id)), None)
        if entry is None or entry.origin:
            return False
        changed = False
        if text is not None:
            value = redact(text)
            if not value:
                return False
            if value != entry.text:
                entry.text = value
                entry.title = value[:MAX_TITLE]
                changed = True
        if importance is not None:
            value = _clamp(int(importance), 1, 100)
            if value != entry.importance:
                entry.importance = value
                changed = True
        if pinned is not None and bool(pinned) != entry.pinned:
            entry.pinned = bool(pinned)
            changed = True
        if scope is not None and scope in SCOPES and scope != entry.scope:
            entry.scope = scope
            changed = True
        if not changed:
            return True
        entry.updated_at = time.time()
        self.save_card(card)
        return True

    @_locked
    def delete_entry(self, entry_id: str) -> bool:
        card = self.load_card()
        entry = next((item for item in card.entries if item.id == str(entry_id)), None)
        if entry is None or entry.origin:
            return False
        if entry.superseded_by == "deleted":
            return True
        self._archive_entry(entry, "deleted")
        entry.superseded_by = "deleted"
        entry.updated_at = time.time()
        self.save_card(card)
        return True

    @_locked
    def restore_entry(self, entry_id: str) -> bool:
        card = self.load_card()
        entry = next((item for item in card.entries if item.id == str(entry_id)), None)
        if entry is None or entry.origin:
            return False
        if not entry.superseded_by and entry.kind != "reply":
            return True
        entry.superseded_by = ""
        if entry.kind == "reply":
            entry.kind = "event"
        entry.updated_at = time.time()
        self.save_card(card)
        return True

    @_locked
    def mark_replies(self, normalized_texts: set[str]) -> int:
        if not normalized_texts:
            return 0
        card = self.load_card()
        changed = 0
        now = time.time()
        for entry in card.entries:
            if entry.kind == "reply" or entry.origin:
                continue
            if normalize_text(entry.text) in normalized_texts:
                entry.kind = "reply"
                entry.updated_at = now
                changed += 1
        if changed:
            self.save_card(card)
        return changed

    @_locked
    def tidy(self) -> dict:
        """保守整理：合并重复、用更正覆盖旧事实；只标记不删除，可恢复。"""
        card = self.load_card()
        active: list[tuple[MemoryEntry, str]] = []
        for item in card.entries:
            if item.superseded_by or item.kind == "reply" or item.origin:
                continue
            normalized = normalize_text(item.text)
            if normalized:
                active.append((item, normalized))
        active.sort(
            key=lambda pair: (float(pair[0].created_at), float(pair[0].updated_at))
        )
        merged = 0
        superseded = 0
        for index, (new, new_norm) in enumerate(active):
            if new.superseded_by:
                continue
            for old, old_norm in active[:index]:
                if old.superseded_by or old.id == new.id:
                    continue
                if old_norm == new_norm:
                    old.superseded_by = new.id
                    merged += 1
                    continue
                if len(old_norm) >= 10 and old_norm in new_norm:
                    old.superseded_by = new.id
                    merged += 1
                    continue
                if _is_correction(new.text) and _correction_matches(
                    old.text, new.text
                ):
                    old.superseded_by = new.id
                    superseded += 1
        if merged or superseded:
            card.updated_at = time.time()
            self.save_card(card)
        return {
            "merged": merged,
            "superseded": superseded,
            "entries": len(card.entries),
        }

    # ---------------------------------------------------------------- 外部镜像

    def _load_external(self) -> dict:
        try:
            raw = json.loads(self.external_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        sources = raw.get("sources")
        muted = raw.get("muted")
        return {
            "sources": sources if isinstance(sources, dict) else {},
            "muted": (
                [str(item) for item in muted if str(item)]
                if isinstance(muted, list)
                else []
            ),
        }

    def _save_external(self, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        handle, temp_name = tempfile.mkstemp(
            prefix="external.", suffix=".tmp", dir=str(self.directory)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            _replace_with_retry(temp_name, self.external_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def external_status(self) -> dict:
        state = self._load_external()
        card = self.load_card()
        sources: list[dict] = []
        for key, info in state["sources"].items():
            if not isinstance(info, dict):
                continue
            prefix = f"{key}:"
            count = sum(
                1 for item in card.entries if item.origin.startswith(prefix)
            )
            sources.append(
                {
                    "key": str(key),
                    "label": str(info.get("label") or key),
                    "path": str(info.get("path") or ""),
                    "status": str(info.get("status") or "unknown"),
                    "reason": str(info.get("reason") or ""),
                    "count": count,
                    "last_sync": float(info.get("last_sync") or 0.0),
                }
            )
        sources.sort(key=lambda item: item["label"])
        return {"sources": sources, "muted": len(state["muted"])}

    @_locked
    def apply_external(
        self,
        source_key: str,
        records: list[dict],
        *,
        status: str = "ok",
        label: str = "",
        path: str = "",
        reason: str = "",
    ) -> dict:
        state = self._load_external()
        muted = set(state["muted"])
        card = self.load_card()
        existing = {
            item.origin: item for item in card.entries if item.origin
        }
        local_texts = {
            normalize_text(item.text)
            for item in card.entries
            if not item.origin and item.kind != "reply"
        }
        seen: set[str] = set()
        added = updated = removed = 0
        now = time.time()
        for record in records:
            if not isinstance(record, dict):
                continue
            origin = str(record.get("origin") or "")
            text = redact(record.get("text") or "")
            if not origin or not text:
                continue
            seen.add(origin)
            if origin in muted:
                continue
            entry = existing.get(origin)
            if entry is not None:
                changed = False
                if entry.text != text:
                    entry.text = text
                    entry.title = text[:MAX_TITLE]
                    changed = True
                new_importance = _clamp(
                    int(record.get("importance") or entry.importance), 1, 100
                )
                if entry.importance != new_importance:
                    entry.importance = new_importance
                    changed = True
                new_created = float(record.get("created_at") or entry.created_at)
                if new_created and new_created != entry.created_at:
                    entry.created_at = new_created
                    changed = True
                if changed:
                    entry.updated_at = now
                    updated += 1
                continue
            normalized = normalize_text(text)
            if normalized in local_texts:
                continue
            entry = MemoryEntry(
                id=f"ext-{hashlib.sha1(origin.encode('utf-8')).hexdigest()[:16]}",
                source=str(record.get("source") or source_key)[:32],
                platform=str(record.get("platform") or "dsh")[:16],
                scope=str(record.get("scope") or "dsh")[:16],
                kind=str(record.get("kind") or "external")[:24],
                title=str(record.get("title") or text)[:MAX_TITLE],
                text=text,
                importance=_clamp(int(record.get("importance") or 70), 1, 100),
                clarity="vivid",
                created_at=float(record.get("created_at") or now),
                last_seen_at=now,
                updated_at=now,
                origin=origin,
            )
            card.entries.append(entry)
            existing[origin] = entry
            added += 1
        if status == "ok":
            prefix = f"{source_key}:"
            keep: list[MemoryEntry] = []
            for entry in card.entries:
                if entry.origin.startswith(prefix) and entry.origin not in seen:
                    self._archive_entry(entry, "external-removed")
                    removed += 1
                    continue
                keep.append(entry)
            card.entries = keep
        info = state["sources"].get(source_key)
        if not isinstance(info, dict):
            info = {}
        info.update(
            {
                "label": label or info.get("label") or source_key,
                "path": path or info.get("path") or "",
                "status": status,
                "reason": reason,
                "last_sync": now,
            }
        )
        state["sources"][source_key] = info
        if added or updated or removed:
            self._compact(card)
            self.save_card(card)
        self._save_external(state)
        return {"added": added, "updated": updated, "removed": removed, "count": len(seen)}

    @_locked
    def mute_external(self, entry_id: str) -> bool:
        card = self.load_card()
        entry = next((item for item in card.entries if item.id == str(entry_id)), None)
        if entry is None or not entry.origin:
            return False
        state = self._load_external()
        muted = set(state["muted"])
        muted.add(entry.origin)
        state["muted"] = sorted(muted)
        self._archive_entry(entry, "muted")
        card.entries = [item for item in card.entries if item.id != entry.id]
        self.save_card(card)
        self._save_external(state)
        return True

    # ---------------------------------------------------------------- 迁移

    @_locked
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
    """分区顺序 = 排版顺序；条目顺序由调用方按相关度排好，这里不再重排。"""
    def pick(predicate):
        return [item for item in entries if predicate(item)]
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


def _visible_scopes(scope: str, *, share_channels: bool = True) -> set[str]:
    """通道可见性矩阵。

    - ``shared`` 永远可见；
    - 主人私聊通道（AI / DSH）默认互见，避免「在 DSH 记得、在 AI 对话不记得」；
    - ``qq`` 仍只与 ``shared`` 互通，不把私聊事实带进群。
    """
    base = scope if scope in SCOPES else "pet"
    if base == "shared":
        return {"shared"}
    if not share_channels:
        return {"shared", base}
    if base in {"pet", "dsh"}:
        return {"shared", "pet", "dsh"}
    return {"shared", "qq"}


def _looks_like_question(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if value.endswith(("？", "?")):
        return True
    return len(value) <= 40 and any(word in value for word in _QUESTION_WORDS)


def _entry_score(
    entry: MemoryEntry,
    terms: list[str],
    query: str,
    now: float,
) -> float:
    """相关度优先的混合打分：字面 + 新近 + 重要度 + 置顶/首次 + 召回次数。

    旧实现按固定分区选前 N 条，普通用户事实（重要度 58，落在「桌宠」区）
    永远输给置顶/高重要度旧条目；这里让查询相关度真正参与选择。
    """
    text = entry.text
    relevance = 0.0
    if terms:
        hits = sum(1 for term in terms if term and term in text)
        relevance = hits / max(1, len(terms))
        if query and query in text:
            relevance = min(1.0, relevance + 0.25)
    # 问句不是事实：它和当前问题字面最像，却会把「星期四上课」挤掉。
    if _looks_like_question(text):
        relevance *= 0.35
    age_days = max(0.0, (now - float(entry.created_at or now)) / 86400.0)
    recency = 0.5 ** (age_days / 30.0)
    importance = max(0, min(100, int(entry.importance))) / 100.0
    return (
        2.2 * relevance
        + 0.9 * recency
        + 0.7 * importance
        + (0.25 if entry.pinned else 0.0)
        + (0.35 if (entry.first or entry.kind == "first") else 0.0)
        + min(5, int(entry.recall_count)) * 0.03
    )


def _dedupe_by_text(ordered: list[MemoryEntry]) -> list[MemoryEntry]:
    """同一规范化文本只保留一条；分数最高者优先，但保留 first/pinned 语义。

    多个外部源（dsh.memory / meow-memory / 身份接力 / feiyu）可能镜像到同一条
    事实；``apply_external`` 只和本地条目去重，这里补上跨源去重，避免提示词
    里同一句话出现两次。若低分的同文条目带 ``first``/``pinned``，换它进列表，
    否则 ``_select_for_render`` 的「最初/锚点」保底会被去重悄悄吃掉。
    记忆页仍能看到各源镜像，便于用户判断来源。
    """
    seen: dict[str, int] = {}
    deduped: list[MemoryEntry] = []
    for item in ordered:
        key = normalize_text(item.text)
        index = seen.get(key)
        if index is None:
            seen[key] = len(deduped)
            deduped.append(item)
            continue
        current = deduped[index]
        if _wants_section_slot(item) and not _wants_section_slot(current):
            deduped[index] = item
    return deduped


def _wants_section_slot(item: MemoryEntry) -> bool:
    return bool(item.pinned or item.first or item.kind == "first")


def _select_for_render(ordered: list[MemoryEntry], max_entries: int) -> list[MemoryEntry]:
    """按分数取 top-K；``first`` 在 K≥2 时保底一条，其余全部让位给相关度。"""
    limit = int(max_entries or 0)
    if limit <= 0:
        return list(ordered)
    chosen: list[MemoryEntry] = []
    chosen_ids: set[str] = set()
    first = next((item for item in ordered if item.first or item.kind == "first"), None)
    if first is not None and limit >= 2:
        chosen.append(first)
        chosen_ids.add(first.id)
    for item in ordered:
        if len(chosen) >= limit:
            break
        if item.id in chosen_ids:
            continue
        chosen.append(item)
        chosen_ids.add(item.id)
    return chosen


_MEMORY_HEADER = (
    "长期记忆（供参考；不要逐条复述，除非与问题直接相关；"
    "若与当前对话冲突，以当前说法为准）："
)


def _compose_render(
    selected: list[MemoryEntry],
    scores: dict[str, float],
    max_chars: int,
) -> str:
    """按分区排版；超预算时先丢最低分的已输出条目，再重排。"""
    budget = max(400, int(max_chars))
    items = list(selected)
    while items:
        text, emitted = _render_sections(items, budget)
        if len(text) <= budget:
            return text
        if not emitted:
            return ""
        drop = min(emitted, key=lambda item: scores.get(item.id, 0.0))
        items = [item for item in items if item.id != drop.id]
    return ""


def _render_sections(
    items: list[MemoryEntry], budget: int
) -> tuple[str, list[MemoryEntry]]:
    used = len(_MEMORY_HEADER) + 1
    lines: list[str] = []
    used_ids: set[str] = set()
    emitted: list[MemoryEntry] = []
    for title, group, per_section in _sections(items):
        chosen_items: list[MemoryEntry] = []
        chosen_lines: list[str] = []
        for item in group:
            if item.id in used_ids:
                continue
            line = f"- {item.text}"
            if used + len(line) + 1 > budget:
                break
            chosen_items.append(item)
            chosen_lines.append(line)
            if len(chosen_items) >= per_section:
                break
        while chosen_lines:
            block = f"\n【{title}】\n" + "\n".join(chosen_lines)
            if used + len(block) <= budget:
                lines.append(block)
                used += len(block)
                for item in chosen_items:
                    used_ids.add(item.id)
                    emitted.append(item)
                break
            chosen_lines.pop()
            chosen_items.pop()
    text = _MEMORY_HEADER + "".join(lines) if lines else ""
    return text, emitted
