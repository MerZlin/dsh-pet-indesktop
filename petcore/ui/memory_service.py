"""Core 记忆服务：把分层记忆接到 HostPorts 与聊天/DSH/QQ。

- 所有通道只 append inbox；本服务是唯一的卡维护者（启动 + 每 10 分钟）；
- 旧 ``feature-data/ai.chat/memory/*.jsonl`` 一次性导入，旧文件原样保留；
- 只做文件与纯逻辑，不 import Qt 之外的网络/平台实现。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from ..interop.memory import MemoryStore, redact

logger = logging.getLogger("petcore.interop.memory")

MAINTAIN_INTERVAL_MS = 10 * 60 * 1000
STARTUP_DELAY_MS = 2_000

_BUDGETS = {"pet": 2200, "dsh": 2600, "qq": 2000}


class MemoryService(QObject):
    def __init__(self, host, root: Path) -> None:
        super().__init__(host if isinstance(host, QObject) else None)
        self.host = host
        self.root = Path(root) / "interop" / "memory"
        self._stores: dict[str, MemoryStore] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(MAINTAIN_INTERVAL_MS)
        self._timer.timeout.connect(self.maintain)
        self._startup = QTimer(self)
        self._startup.setSingleShot(True)
        self._startup.timeout.connect(self.maintain)
        self._started = False

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._startup.start(STARTUP_DELAY_MS)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._startup.stop()
        self._started = False

    def maintain(self) -> None:
        stores = list(self._stores.values())
        current = self._character()
        if current not in self._stores:
            stores.append(self.store())
        for store in stores:
            try:
                store.maintain()
            except Exception:
                logger.warning("memory maintain failed: %s", store.character, exc_info=True)

    # ---------------------------------------------------------------- 角色/存储

    def _config(self):
        return getattr(self.host, "config", None)

    def _config_get(self, key: str, default=None):
        config = self._config()
        getter = getattr(config, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
        except Exception:
            return default
        return default if value is None else value

    def character_for(self, pet_slot: int | None = None) -> str:
        pets = getattr(self.host, "pets", None)
        window = None
        if pets is not None and pet_slot is not None:
            try:
                window = pets.window_for_slot(int(pet_slot))
            except Exception:
                window = None
        if window is None and pet_slot in (None, 0):
            window = getattr(self.host, "window", None)
        config = getattr(window, "config", None)
        if config is not None:
            try:
                return str(config.get("character") or "default")
            except Exception:
                pass
        return str(self._config_get("character", "default") or "default")

    def _character(self, pet_slot: int | None = None) -> str:
        return str(self.character_for(pet_slot) or "default")

    def store(self, pet_slot: int | None = None) -> MemoryStore:
        character = self._character(pet_slot)
        store = self._stores.get(character)
        if store is None:
            store = MemoryStore(
                self.root,
                character,
                max_entries=int(self._config_get("ai_memory_max_entries", 500) or 500),
            )
            self._stores[character] = store
            self._migrate(store)
        return store

    def _migrate(self, store: MemoryStore) -> None:
        legacy = Path(getattr(self.host, "root", self.root)) / "feature-data" / "ai.chat" / "memory"
        paths = [legacy / "global.jsonl", legacy / f"{store.character}.jsonl"]
        marker = self.root / f"migration-ai-chat-{store.character}.json"
        try:
            added = store.migrate_legacy(paths, marker=marker)
            if added:
                logger.info("memory migrated legacy entries: %d", added)
        except Exception:
            logger.warning("memory legacy migration failed", exc_info=True)

    # ---------------------------------------------------------------- 写入

    def append_message(
        self,
        text: str,
        *,
        role: str = "user",
        channel: str = "pet",
        conversation_id: str = "",
        sender: str = "",
        sender_id: str = "",
        is_owner: bool | None = None,
        external_id: str = "",
        ts: float | None = None,
        pet_slot: int | None = None,
    ) -> bool:
        body = redact(text)
        if not body:
            return False
        platform = channel if channel in {"pet", "dsh", "qq"} else "pet"
        owner = bool(role == "user") if is_owner is None else bool(is_owner)
        store = self.store(pet_slot)
        store.append_inbox(
            {
                "at": float(ts or time.time()),
                "platform": platform,
                "kind": "message",
                "sender": sender or ("主人" if owner else "桌宠"),
                "sender_id": sender_id,
                "conversation_key": conversation_id,
                "text": body,
                "is_owner": owner,
                "is_self": role == "assistant",
                "direct": True,
                "external_id": external_id,
                "scope": platform,
            }
        )
        try:
            store.ingest()
        except OSError:
            logger.warning("memory message ingest failed")
            return False
        return True

    def remember(
        self,
        text: str,
        *,
        scope: str = "pet",
        source: str = "",
        kind: str = "",
        importance: int | None = None,
        pet_slot: int | None = None,
    ) -> bool:
        body = redact(text)
        if not body:
            return False
        store = self.store(pet_slot)
        store.append_inbox(
            {
                "at": time.time(),
                "platform": scope if scope in {"pet", "dsh", "qq"} else "pet",
                "kind": kind or "explicit",
                "sender": "主人",
                "sender_id": "owner",
                "text": body,
                "is_owner": True,
                "is_self": False,
                "direct": True,
                "source": source or scope,
                "importance": 80 if importance is None else int(importance),
                "scope": scope if scope in {"pet", "dsh", "qq", "shared"} else "pet",
            }
        )
        try:
            store.ingest()
        except OSError:
            logger.warning("memory remember write failed")
            return False
        return True

    # ---------------------------------------------------------------- 读取

    def recall(
        self,
        query: str = "",
        *,
        scope: str = "pet",
        limit: int = 3,
        pet_slot: int | None = None,
    ) -> list[dict]:
        try:
            entries = self.store(pet_slot).recall(
                query, scope=scope, limit=max(1, min(8, int(limit)))
            )
        except Exception:
            logger.warning("memory recall failed", exc_info=True)
            return []
        return [item.as_dict() for item in entries]

    def render_prompt(
        self,
        scope: str = "pet",
        query: str = "",
        *,
        max_chars: int = 0,
        limit: int = 0,
        pet_slot: int | None = None,
    ) -> str:
        budget = int(max_chars or _BUDGETS.get(scope, 2200))
        try:
            return self.store(pet_slot).render(
                scope, query=query, max_chars=budget, max_entries=limit
            )
        except Exception:
            logger.warning("memory render failed", exc_info=True)
            return ""

    def stats(self, *, pet_slot: int | None = None) -> dict:
        try:
            return self.store(pet_slot).stats()
        except Exception:
            return {"entries": 0, "pinned": 0, "first_met": False, "clarity": {}}

    def clear(self, scope: str = "", *, pet_slot: int | None = None) -> int:
        try:
            return self.store(pet_slot).clear(scope)
        except OSError:
            return 0

    def export(self, *, pet_slot: int | None = None) -> str:
        try:
            return self.store(pet_slot).export()
        except OSError:
            return ""
