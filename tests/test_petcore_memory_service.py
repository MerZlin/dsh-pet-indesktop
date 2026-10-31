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
