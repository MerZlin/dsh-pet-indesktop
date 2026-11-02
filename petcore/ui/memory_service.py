"""Core 记忆服务：把分层记忆接到 HostPorts 与聊天/DSH/QQ。

- 所有通道只 append inbox；本服务是唯一的卡维护者（启动 + 每 10 分钟）；
- 用户显式编辑/删除/整理也经本服务（单写者）；
- 外部记忆（dsh.memory facts / meow-memory DB）只读镜像到本机卡，绝不写回；
- 旧 ``feature-data/ai.chat/memory/*.jsonl`` 一次性导入，旧文件原样保留；
- 只做文件与纯逻辑，不 import Qt 之外的网络/平台实现。
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..interop.external_memory import assistant_texts, read_external_sources
from ..interop.memory import MemoryStore, redact

logger = logging.getLogger("petcore.interop.memory")

MAINTAIN_INTERVAL_MS = 10 * 60 * 1000
STARTUP_DELAY_MS = 2_000
SYNC_DELAY_MS = 3_000

_BUDGETS = {"pet": 2200, "dsh": 2600, "qq": 2000}


class MemoryService(QObject):
    #: 外部同步完成（角色, 结果）；从工作线程发出，GUI 线程消费。
    external_sync_finished = Signal(str, object)

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
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="memory-sync"
        )
        self._sync_inflight: set[str] = set()
        self.external_sync_finished.connect(self._apply_external_result)

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.store()
        self._startup.start(STARTUP_DELAY_MS)
        self._timer.start()
        QTimer.singleShot(SYNC_DELAY_MS, self, self.sync_external)

    def stop(self) -> None:
        self._timer.stop()
        self._startup.stop()
        self._started = False
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - 收口失败不影响退出
            logger.debug("memory sync shutdown failed", exc_info=True)

    def maintain(self) -> None:
        stores = list(self._stores.values())
        current = self._character()
        if current not in self._stores:
            stores.append(self.store())
        include_sources = bool(self._config_get("ai_memory_import_dsh", True))
        for store in stores:
            try:
                store.maintain()
            except Exception:
                logger.warning("memory maintain failed: %s", store.character, exc_info=True)
            self._sync_store(store, include_sources=include_sources)

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
                "kind": "reply" if role == "assistant" else "message",
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
        pinned: bool = False,
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
                "pinned": bool(pinned),
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
                query,
                scope=scope,
                limit=max(1, min(8, int(limit))),
                share_channels=bool(self._config_get("ai_memory_share_channels", True)),
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
                scope,
                query=query,
                max_chars=budget,
                max_entries=limit,
                share_channels=bool(self._config_get("ai_memory_share_channels", True)),
            )
        except Exception:
            logger.warning("memory render failed", exc_info=True)
            return ""

    def stats(self, *, pet_slot: int | None = None) -> dict:
        try:
            return self.store(pet_slot).stats()
        except Exception:
            return {
                "entries": 0,
                "pinned": 0,
                "first_met": False,
                "clarity": {},
            }

    def entries(
        self,
        *,
        scope: str = "",
        limit: int = 0,
        include_superseded: bool = False,
        include_replies: bool = False,
        pet_slot: int | None = None,
    ) -> list[dict]:
        try:
            items = self.store(pet_slot).entries(
                scope=scope,
                limit=limit,
                include_superseded=include_superseded,
                include_replies=include_replies,
            )
        except Exception:
            logger.warning("memory entries failed", exc_info=True)
            return []
        return [item.as_dict() for item in items]

    def update_entry(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        importance: int | None = None,
        pinned: bool | None = None,
        scope: str | None = None,
        pet_slot: int | None = None,
    ) -> bool:
        try:
            return self.store(pet_slot).update_entry(
                entry_id,
                text=text,
                importance=importance,
                pinned=pinned,
                scope=scope,
            )
        except Exception:
            logger.warning("memory update failed", exc_info=True)
            return False

    def delete_entry(self, entry_id: str, *, pet_slot: int | None = None) -> bool:
        try:
            store = self.store(pet_slot)
            entry = store.find_entry(entry_id)
            if entry is None:
                return False
            if entry.origin:
                return store.mute_external(entry_id)
            return store.delete_entry(entry_id)
        except Exception:
            logger.warning("memory delete failed", exc_info=True)
            return False

    def restore_entry(self, entry_id: str, *, pet_slot: int | None = None) -> bool:
        try:
            return self.store(pet_slot).restore_entry(entry_id)
        except Exception:
            logger.warning("memory restore failed", exc_info=True)
            return False

    def tidy(self, *, pet_slot: int | None = None) -> dict:
        try:
            return self.store(pet_slot).tidy()
        except Exception:
            logger.warning("memory tidy failed", exc_info=True)
            return {"merged": 0, "superseded": 0, "entries": 0}

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

    # ---------------------------------------------------------------- 外部记忆

    def external_status(self, *, pet_slot: int | None = None) -> dict:
        try:
            status = self.store(pet_slot).external_status()
        except Exception:
            status = {"sources": [], "muted": 0}
        status["enabled"] = bool(self._config_get("ai_memory_import_dsh", True))
        return status

    def sync_external(
        self, *, pet_slot: int | None = None, force: bool = False
    ) -> bool:
        """后台同步外部记忆；同一角色在上一轮结束前直接返回 False。"""
        character = self._character(pet_slot)
        store = self._stores.get(character)
        if store is None:
            store = self.store(pet_slot)
        return self._sync_store(
            store,
            include_sources=bool(self._config_get("ai_memory_import_dsh", True))
            or bool(force),
        )

    def _sync_store(self, store: MemoryStore, *, include_sources: bool) -> bool:
        character = store.character
        if character in self._sync_inflight:
            return False
        reply_marker = store.directory / "migration-replies.json"
        tidy_marker = store.directory / "tidy-v2.json"
        need_replies = not reply_marker.is_file()
        need_tidy = not tidy_marker.is_file()
        if not include_sources and not need_replies and not need_tidy:
            return False
        self._sync_inflight.add(character)
        try:
            self._executor.submit(
                self._sync_worker,
                store,
                include_sources,
                need_replies,
                need_tidy,
            )
        except RuntimeError:
            self._sync_inflight.discard(character)
            return False
        return True

    def _sync_worker(
        self,
        store: MemoryStore,
        include_sources: bool,
        need_replies: bool,
        need_tidy: bool,
    ) -> None:
        result: dict = {
            "sources": [],
            "reply_marked": None,
            "tidy": None,
            "error": "",
        }
        root = Path(getattr(self.host, "root", self.root))
        try:
            if include_sources:
                for source in read_external_sources(root, Path.home()):
                    try:
                        applied = store.apply_external(
                            source.key,
                            list(source.records),
                            status=source.status,
                            label=source.label,
                            path=source.path,
                            reason=source.reason,
                        )
                        result["sources"].append(
                            {
                                "key": source.key,
                                "status": source.status,
                                **applied,
                            }
                        )
                    except Exception:
                        logger.warning(
                            "memory external apply failed: %s",
                            source.key,
                            exc_info=True,
                        )
            if need_replies:
                result["reply_marked"] = store.mark_replies(
                    assistant_texts(root, store.character)
                )
            if need_tidy:
                result["tidy"] = store.tidy()
        except Exception as exc:  # noqa: BLE001 - 同步失败不能影响聊天
            logger.warning("memory external sync failed: %s", type(exc).__name__)
            result["error"] = type(exc).__name__
        self.external_sync_finished.emit(store.character, result)

    def _apply_external_result(self, character: str, result: object) -> None:
        """GUI 线程只写幂等标记与日志；卡写入已在工作线程完成。"""
        try:
            store = self._stores.get(str(character))
            if store is None or not isinstance(result, dict):
                return
            if result.get("reply_marked") is not None:
                self._write_marker(store.directory / "migration-replies.json")
            if isinstance(result.get("tidy"), dict):
                self._write_marker(store.directory / "tidy-v2.json")
            for source in result.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                logger.info(
                    "memory external sync %s: status=%s added=%s updated=%s removed=%s",
                    source.get("key"),
                    source.get("status"),
                    source.get("added"),
                    source.get("updated"),
                    source.get("removed"),
                )
        finally:
            self._sync_inflight.discard(str(character))

    @staticmethod
    def _write_marker(path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"at": time.time()}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
