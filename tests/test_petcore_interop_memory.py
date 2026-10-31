from __future__ import annotations

import json
import time
from pathlib import Path

from petcore.interop.memory import (
    MemoryStore,
    extract_emotion,
    redact,
    score_memory,
)
from petcore.interop.messages import Watermark


def test_score_prefers_owner_first_and_explicit_facts() -> None:
    owner, reasons, first = score_memory("第一次和主人一起看海", is_owner=True, direct=True)
    assert owner >= 100
    assert first is True
    assert "主人私聊" in reasons
    short, _reasons, _first = score_memory("嗯")
    assert short < 20


def test_redact_masks_secrets() -> None:
    text = redact("我的 key 是 sk-abcdefgh12345678，token=deadbeefdeadbeefdeadbeefdeadbeef")
    assert "sk-abcdefgh" not in text
    assert "deadbeef" not in text
    assert "********" in text
    assert extract_emotion("今天好开心呀") > 0


def test_ingest_dedupes_same_day_same_sender(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    record = {
        "at": time.time(),
        "platform": "pet",
        "sender": "主人",
        "sender_id": "owner",
        "text": "今天一起看海",
        "is_owner": True,
        "direct": True,
    }
    store.append_inbox(record)
    store.append_inbox(record)
    assert store.ingest() == 1
    assert store.ingest() == 0
    assert store.load_card().entries[0].text == "今天一起看海"


def test_render_respects_budget_and_writes_prompt(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    for index in range(40):
        store.append_inbox(
            {
                "at": time.time() - index,
                "platform": "pet",
                "sender": "主人",
                "sender_id": "owner",
                "text": f"第{index}条记忆：一起做了很多小事",
                "is_owner": True,
                "direct": True,
            }
        )
    store.ingest()
    text = store.render("pet", max_chars=600)
    assert len(text) <= 600
    assert text.startswith("长期记忆")
    assert (store.directory / "prompt-pet.md").is_file()
    # 同一段内容不在多个分区重复出现
    body = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(body) == len(set(body))


def test_decay_and_clear(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    store.append_inbox(
        {
            "at": time.time() - 30 * 86400,
            "platform": "pet",
            "sender": "主人",
            "sender_id": "owner",
            "text": "很久以前的一件普通小事",
            "is_owner": True,
            "direct": True,
        }
    )
    store.ingest()
    card = store.load_card()
    card.entries[0].last_seen_at = time.time() - 30 * 86400
    card.entries[0].created_at = time.time() - 30 * 86400
    store.save_card(card)
    assert store.apply_decay() > 0
    assert store.load_card().entries[0].clarity != "vivid"
    assert store.clear("pet") == 1
    assert store.load_card().entries == []


def test_legacy_migration_is_marked(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy" / "global.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"content": "主人喜欢鲸鱼", "scope": "global", "created_at": time.time()})
        + "\n",
        encoding="utf-8",
    )
    store = MemoryStore(tmp_path / "memory", "m")
    marker = tmp_path / "migration.json"
    assert store.migrate_legacy([legacy], marker=marker) == 1
    assert store.load_card().entries[0].scope == "shared"
    assert store.migrate_legacy([legacy], marker=marker) == 0


def test_watermark_snapshot_and_fold() -> None:
    mark = Watermark()
    for index in range(20):
        mark.append(f"m{index}", external_id=f"e{index}")
    snapshot = mark.snapshot(limit=5, recent_limit=2)
    assert snapshot["unread_count"] == 20
    assert snapshot["included_unread_count"] == 5
    assert snapshot["partial"] is True
    assert snapshot["folded"]["count"] == 15
    assert mark.acknowledge(10) == 10
    assert [item.seq for item in mark.unread()] == list(range(11, 21))
    assert mark.acknowledge(999) == 20
    assert mark.snapshot()["unread_count"] == 0


def test_inbox_offset_only_ingests_new_lines(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    store.append_inbox({"at": time.time(), "platform": "pet", "sender": "a", "text": "第一句"})
    assert store.ingest() == 1
    assert store.ingest() == 0
    store.append_inbox({"at": time.time(), "platform": "pet", "sender": "a", "text": "第二句"})
    assert store.ingest() == 1
    assert (store.directory / "inbox.offset").is_file()
    assert [item.text for item in store.load_card().entries] == ["第一句", "第二句"]


def test_shared_scope_is_visible_from_all_channels(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    store.append_inbox(
        {
            "at": time.time(),
            "platform": "pet",
            "scope": "shared",
            "sender": "主人",
            "text": "主人喜欢鲸鱼",
            "is_owner": True,
            "direct": True,
        }
    )
    store.ingest()
    assert "主人喜欢鲸鱼" in store.render("pet", max_chars=2000)
    assert "主人喜欢鲸鱼" in store.render("qq", max_chars=2000)
