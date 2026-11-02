from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.memory_service import MemoryService


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def no_meow(monkeypatch):
    monkeypatch.setattr(
        "petcore.interop.external_memory.discover_meow_dbs",
        lambda home=None: [],
    )


class _Host:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.config = Config(self.root)
        self.config.set("character", "whale")
        self.window = SimpleNamespace(config=self.config)
        self.pets = None


def test_remember_render_recall_and_clear(app, tmp_path: Path) -> None:
    service = MemoryService(_Host(tmp_path), tmp_path)
    assert service.remember("主人喜欢鲸鱼", scope="pet", source="ai") is True
    text = service.render_prompt("pet", "鲸鱼")
    assert "主人喜欢鲸鱼" in text
    recalled = service.recall("鲸鱼", scope="pet")
    assert recalled and recalled[0]["text"] == "主人喜欢鲸鱼"
    stats = service.stats()
    assert stats["entries"] >= 1
    assert service.clear("pet") >= 1
    assert service.stats()["entries"] == 0


def test_append_message_reaches_channel_scope(app, tmp_path: Path) -> None:
    service = MemoryService(_Host(tmp_path), tmp_path)
    service.append_message(
        "群里有人问晚饭吃什么",
        role="user",
        channel="qq",
        conversation_id="group:1",
        sender="小明",
    )
    text = service.render_prompt("qq", "晚饭")
    assert "晚饭" in text


def test_legacy_ai_chat_memory_is_imported_once(app, tmp_path: Path) -> None:
    legacy = tmp_path / "feature-data" / "ai.chat" / "memory"
    legacy.mkdir(parents=True)
    (legacy / "whale.jsonl").write_text(
        json.dumps(
            {"content": "旧记忆：主人喜欢海", "scope": "character", "created_at": time.time()}
        )
        + "\n",
        encoding="utf-8",
    )
    service = MemoryService(_Host(tmp_path), tmp_path)
    assert "旧记忆" in service.render_prompt("pet", "海")
    marker = tmp_path / "interop" / "memory" / "migration-ai-chat-whale.json"
    assert marker.is_file()
    # 第二次启动不应重复导入
    service2 = MemoryService(_Host(tmp_path), tmp_path)
    assert service2.stats()["entries"] == service.stats()["entries"]


def test_external_sync_includes_identity_and_feiyu(
    app, no_meow, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    identity = tmp_path / "identity"
    identity.mkdir()
    (identity / "facts.jsonl").write_text(
        json.dumps(
            {
                "id": "i1",
                "content": "身份事实：主人喜欢鲸鱼",
                "source": "owner",
                "status": "active",
                "time": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    feiyu = tmp_path / "feiyu"
    log = feiyu / "sessions" / "__default__" / "log.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000000000,
                    "gist": "feiyu 事实：主人喜欢红茶",
                    "importance": 0.8,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    host = _Host(root)
    host.config.set("ai_memory_identity_dir", str(identity))
    host.config.set("ai_memory_feiyu_root", str(feiyu))
    host.config.set("ai_memory_feiyu_session", "__default__")
    service = MemoryService(host, root)
    try:
        assert service.sync_external(force=True) is True
        deadline = time.monotonic() + 8.0
        keys: set[str] = set()
        while time.monotonic() < deadline:
            app.processEvents()
            status = service.external_status()
            keys = {str(item.get("key")) for item in status.get("sources", [])}
            if {"identity", "feiyu:__default__"} <= keys:
                break
            time.sleep(0.02)
        assert {"identity", "feiyu:__default__"} <= keys
        assert "身份事实" in service.render_prompt("pet", "身份")
        assert "feiyu 事实" in service.render_prompt("pet", "红茶")
    finally:
        service.stop()


def test_switching_feiyu_session_prunes_old_mirrors(
    app, no_meow, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    feiyu = tmp_path / "feiyu"
    for session, text in (("one", "会话一事实"), ("two", "会话二事实")):
        log = feiyu / "sessions" / session / "log.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            json.dumps(
                {
                    "op": "add",
                    "e": {
                        "id": f"f-{session}",
                        "kind": "fact",
                        "t": 1700000000000,
                        "gist": text,
                        "importance": 0.8,
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    host = _Host(root)
    host.config.set("ai_memory_feiyu_root", str(feiyu))
    host.config.set("ai_memory_feiyu_session", "one")
    service = MemoryService(host, root)

    def wait_for(predicate, budget: float = 8.0) -> bool:
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())

    try:
        assert service.sync_external(force=True) is True
        assert wait_for(
            lambda: any(
                entry.origin.startswith("feiyu:one:")
                for entry in service.store().entries()
            )
        )
        assert wait_for(lambda: not service._sync_inflight)
        host.config.set("ai_memory_feiyu_session", "two")
        assert service.sync_external(force=True) is True
        assert wait_for(
            lambda: any(
                entry.origin.startswith("feiyu:two:")
                for entry in service.store().entries()
            )
        )
        assert wait_for(lambda: not service._sync_inflight)
        origins = [entry.origin for entry in service.store().entries()]
        assert not any(origin.startswith("feiyu:one:") for origin in origins)
    finally:
        service.stop()
