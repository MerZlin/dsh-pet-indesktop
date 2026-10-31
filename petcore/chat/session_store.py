from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MAX_MESSAGES = 200
MAX_MESSAGE_CHARS = 24_000
MAX_TITLE_CHARS = 120
MAX_META_CHARS = 2_000
SESSION_SCHEMA_VERSION = 2
LEGACY_SESSION_SCHEMA_VERSION = 1
CHANNELS = ("ai", "dsh", "qq")


@dataclass(frozen=True)
class SessionDiagnostic:
    status: str
    path: str
    error: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _clean_channel(value: object) -> str:
    text = str(value or "ai").strip().lower()
    return text if text in CHANNELS else "ai"


def _clean_meta(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:32]:
        name = str(key or "").strip()[:64]
        if not name:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[name] = item if not isinstance(item, str) else item[:500]
    if len(json.dumps(result, ensure_ascii=False)) > MAX_META_CHARS:
        return {}
    return result


@dataclass
class ChatSession:
    session_id: str
    title: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    #: 归档时间（0 = 未归档）。旧文件没有这个键，读出来就是 0。
    archived_at: float = 0.0
    #: 会话来源：ai / dsh / qq。旧文件没有这个键，按 ai 读。
    channel: str = "ai"
    #: 外部会话标识（DSH sessionId / QQ group:123 等）；空 = 本地会话。
    conversation_id: str = ""
    created_at: float = 0.0

    def append(
        self,
        role: str,
        content: str,
        *,
        message_id: str = "",
        ts: float | None = None,
        channel: str = "",
        sender: str = "",
        external_id: str = "",
        meta: dict | None = None,
    ) -> bool:
        role = str(role or "user").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        text = str(content or "")[:MAX_MESSAGE_CHARS]
        if not text:
            return False
        external = str(external_id or "").strip()[:200]
        if external and any(
            str(item.get("external_id") or "") == external for item in self.messages
        ):
            return False
        record: dict[str, Any] = {
            "role": role,
            "content": text,
            "id": str(message_id or uuid.uuid4().hex)[:64],
            "ts": float(ts if ts is not None else time.time()),
            "channel": _clean_channel(channel or self.channel),
        }
        sender_text = str(sender or "").strip()[:80]
        if sender_text:
            record["sender"] = sender_text
        if external:
            record["external_id"] = external
        cleaned_meta = _clean_meta(meta)
        if cleaned_meta:
            record["meta"] = cleaned_meta
        self.messages.append(record)
        if len(self.messages) > MAX_MESSAGES:
            del self.messages[: len(self.messages) - MAX_MESSAGES]
        if not self.title or self.title == "新会话":
            self.title = text.replace("\n", " ").strip()[:MAX_TITLE_CHARS] or "新会话"
        self.updated_at = time.time()
        if not self.created_at:
            self.created_at = self.updated_at
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "title": self.title[:MAX_TITLE_CHARS],
            "messages": list(self.messages[-MAX_MESSAGES:]),
            "updated_at": float(self.updated_at or 0.0),
            "archived_at": float(self.archived_at or 0.0),
            "channel": _clean_channel(self.channel),
            "conversation_id": str(self.conversation_id or "")[:200],
            "created_at": float(self.created_at or 0.0),
        }

    @classmethod
    def from_dict(cls, raw: Any, fallback_id: str = "") -> "ChatSession":
        if not isinstance(raw, dict):
            raise ValueError("session must be an object")
        schema = raw.get("schema", LEGACY_SESSION_SCHEMA_VERSION)
        if schema not in (LEGACY_SESSION_SCHEMA_VERSION, SESSION_SCHEMA_VERSION):
            raise ValueError(f"unsupported session schema: {schema!r}")
        session_id = str(raw.get("session_id") or fallback_id).strip()
        if not session_id:
            raise ValueError("session id is empty")
        messages: list[dict[str, Any]] = []
        for index, item in enumerate(
            raw.get("messages", []) if isinstance(raw.get("messages"), list) else []
        ):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().lower()
            content = str(item.get("content") or "")[:MAX_MESSAGE_CHARS]
            if role not in {"system", "user", "assistant", "tool"} or not content:
                continue
            record: dict[str, Any] = {
                "role": role,
                "content": content,
                "id": str(item.get("id") or f"m{index}")[:64],
                "ts": float(item.get("ts") or 0.0),
                "channel": _clean_channel(item.get("channel") or raw.get("channel")),
            }
            sender = str(item.get("sender") or "").strip()[:80]
            if sender:
                record["sender"] = sender
            external = str(item.get("external_id") or "").strip()[:200]
            if external:
                record["external_id"] = external
            meta = _clean_meta(item.get("meta"))
            if meta:
                record["meta"] = meta
            reasoning = str(item.get("reasoning") or "")[:MAX_MESSAGE_CHARS]
            if reasoning:
                record["reasoning"] = reasoning
            activity = item.get("activity")
            if isinstance(activity, list) and activity:
                record["activity"] = [str(line)[:500] for line in activity[-40:]]
            messages.append(record)
        try:
            archived_at = float(raw.get("archived_at") or 0.0)
        except (TypeError, ValueError):
            archived_at = 0.0
        try:
            created_at = float(raw.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        return cls(
            session_id=session_id,
            title=str(raw.get("title") or "新会话")[:MAX_TITLE_CHARS],
            messages=messages[-MAX_MESSAGES:],
            updated_at=float(raw.get("updated_at") or 0.0),
            archived_at=archived_at,
            channel=_clean_channel(raw.get("channel")),
            conversation_id=str(raw.get("conversation_id") or "")[:200],
            created_at=created_at,
        )


class SessionStore:
    """Per-pet chat history store; no credentials or provider state is written."""

    def __init__(self, root: Path, *, slot: int = 0, character_id: str = "default") -> None:
        self.root = Path(root)
        self.slot = max(0, int(slot))
        self.character_id = _safe_component(character_id) or "default"
        self.directory = self.root / (
            "sessions" if self.slot == 0 else f"sessions-slot-{self.slot}"
        ) / self.character_id
        self.diagnostics: list[SessionDiagnostic] = []
        self._legacy_checked: set[str] = set()

    def _diagnose(self, path: Path, error: BaseException | str) -> None:
        self.diagnostics.append(
            SessionDiagnostic("skipped_invalid", str(path), error if isinstance(error, str) else type(error).__name__)
        )

    def new_session(
        self,
        title: str = "新会话",
        session_id: str | None = None,
        *,
        channel: str = "ai",
        conversation_id: str = "",
    ) -> ChatSession:
        now = time.time()
        session = ChatSession(
            session_id=_safe_component(session_id or uuid.uuid4().hex) or uuid.uuid4().hex,
            title=str(title or "新会话")[:MAX_TITLE_CHARS],
            updated_at=now,
            channel=_clean_channel(channel),
            conversation_id=str(conversation_id or "")[:200],
            created_at=now,
        )
        self.save(session)
        return session

    def list_sessions(
        self, *, include_archived: bool = False, channel: str | None = None
    ) -> list[ChatSession]:
        if not self.directory.is_dir():
            return []
        sessions: list[ChatSession] = []
        wanted = None if channel is None else _clean_channel(channel)
        for path in sorted(self.directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                session = ChatSession.from_dict(raw, path.stem)
            except (OSError, ValueError, TypeError) as exc:
                self._diagnose(path, exc)
                continue
            if wanted is not None and session.channel != wanted:
                continue
            sessions.append(session)
        if not include_archived:
            sessions = [item for item in sessions if not item.archived_at]
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def find_by_conversation(
        self, channel: str, conversation_id: str
    ) -> ChatSession | None:
        wanted_channel = _clean_channel(channel)
        wanted_id = str(conversation_id or "").strip()
        if not wanted_id:
            return None
        for session in self.list_sessions(include_archived=True):
            if (
                session.channel == wanted_channel
                and session.conversation_id == wanted_id
            ):
                return session
        return None

    def ensure_conversation(
        self,
        channel: str,
        conversation_id: str,
        title: str = "新会话",
    ) -> ChatSession:
        found = self.find_by_conversation(channel, conversation_id)
        if found is not None:
            return found
        return self.new_session(
            title=title,
            channel=channel,
            conversation_id=conversation_id,
        )

    def archive(self, session_id: str, archived: bool = True) -> ChatSession | None:
        """归档/取消归档；返回更新后的会话，找不到返回 None。"""
        session = self.load(session_id)
        if session is None:
            return None
        session.archived_at = time.time() if archived else 0.0
        self.save(session)
        return session

    def load(self, session_id: str) -> ChatSession | None:
        path = self._path(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return ChatSession.from_dict(raw, path.stem)
        except (OSError, ValueError, TypeError) as exc:
            self._diagnose(path, exc)
            return None

    def _backup_legacy(self, path: Path) -> None:
        key = str(path)
        if key in self._legacy_checked or not path.is_file():
            self._legacy_checked.add(key)
            return
        self._legacy_checked.add(key)
        backup = path.with_name(f"{path.name}.pre-v2")
        if backup.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        if raw.get("schema", LEGACY_SESSION_SCHEMA_VERSION) != LEGACY_SESSION_SCHEMA_VERSION:
            return
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass

    def save(self, session: ChatSession) -> None:
        path = self._path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_legacy(path)
        payload = json.dumps(session.as_dict(), ensure_ascii=False, indent=2) + "\n"
        handle, temp_name = tempfile.mkstemp(prefix="session.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _path(self, session_id: str) -> Path:
        safe = _safe_component(session_id)
        if not safe:
            raise ValueError("invalid session id")
        return self.directory / f"{safe}.json"


def _safe_component(value: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."}:
        return ""
    if any(char in text for char in "/\\:\x00"):
        return ""
    return text[:128]
