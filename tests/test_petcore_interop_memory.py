from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import petcore.interop.memory as memmod
from petcore.interop.memory import (
    MemoryCard,
    MemoryEntry,
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


def _remember(
    store: MemoryStore,
    text: str,
    *,
    scope: str = "pet",
    platform: str = "pet",
    importance: int | None = None,
    kind: str | None = None,
    at: float | None = None,
) -> None:
    record = {
        "at": float(at if at is not None else time.time()),
        "platform": platform,
        "scope": scope,
        "sender": "主人",
        "sender_id": "owner",
        "text": text,
        "is_owner": True,
        "direct": True,
    }
    if importance is not None:
        record["importance"] = importance
    if kind is not None:
        record["kind"] = kind
    store.append_inbox(record)


def test_render_uses_query_relevance_within_top_k(tmp_path: Path) -> None:
    """真实卡形状：旧的高重要度事实不能把「星期四上课」挤出 top-3。"""
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    for index in range(8):
        _remember(
            store,
            f"主人喜欢第{index}种收藏品",
            importance=90,
            at=now - 60 * 86400,
        )
    _remember(store, "我喜欢鲸鱼", importance=85, at=now - 2 * 86400)
    _remember(store, "我星期三不上课，改为星期四上课了", importance=58, at=now)
    store.ingest()
    card = store.load_card()
    for entry in card.entries:
        if entry.text == "我喜欢鲸鱼":
            entry.pinned = True
    store.save_card(card)

    text = store.render("pet", query="我星期几上课啊", max_chars=2200, max_entries=3)
    assert "星期四上课" in text
    assert len([line for line in text.splitlines() if line.startswith("- ")]) <= 3


def test_render_shares_owner_channels_but_keeps_qq_isolated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    _remember(store, "AI 里说的事实", scope="pet", platform="pet", importance=80)
    _remember(store, "DSH 里说的事实", scope="dsh", platform="dsh", importance=80)
    _remember(store, "QQ 群里说的事实", scope="qq", platform="qq", importance=80)
    _remember(store, "全局事实", scope="shared", platform="pet", importance=80)
    store.ingest()

    pet = store.render("pet", max_chars=2000)
    assert "AI 里说的事实" in pet
    assert "DSH 里说的事实" in pet
    assert "全局事实" in pet
    assert "QQ 群里说的事实" not in pet

    dsh = store.render("dsh", max_chars=2000)
    assert "DSH 里说的事实" in dsh
    assert "AI 里说的事实" in dsh
    assert "QQ 群里说的事实" not in dsh

    qq = store.render("qq", max_chars=2000)
    assert "QQ 群里说的事实" in qq
    assert "全局事实" in qq
    assert "AI 里说的事实" not in qq
    assert "DSH 里说的事实" not in qq

    strict = store.render("pet", max_chars=2000, share_channels=False)
    assert "AI 里说的事实" in strict
    assert "DSH 里说的事实" not in strict


def test_render_skips_assistant_replies_and_superseded_entries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    _remember(store, "主人喜欢红茶", importance=80)
    _remember(store, "我记住啦，主人喜欢红茶喵。", kind="reply", importance=100)
    store.ingest()
    card = store.load_card()
    for entry in card.entries:
        if entry.text.startswith("我记住啦"):
            entry.kind = "reply"
        elif entry.text == "主人喜欢红茶":
            entry.superseded_by = "newer"
    store.save_card(card)

    text = store.render("pet", max_chars=2000)
    assert "主人喜欢红茶" not in text
    assert "我记住啦" not in text


def test_duplicate_fact_across_days_merges_instead_of_appending(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    day = 86400.0
    _remember(store, "我喜欢红茶", importance=70, at=time.time() - day)
    store.ingest()
    _remember(store, "我喜欢红茶", importance=80, at=time.time())
    added = store.ingest()
    assert added == 0
    entries = store.entries()
    assert len(entries) == 1
    assert entries[0].importance == 80
    assert entries[0].recall_count >= 1


def test_correction_supersedes_the_old_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    _remember(store, "我星期三上课", importance=58, at=now - 60)
    store.ingest()
    _remember(store, "我星期三不上课，改为星期四上课了", importance=58, at=now)
    store.ingest()

    active = store.entries()
    assert [item.text for item in active] == ["我星期三不上课，改为星期四上课了"]
    superseded = store.entries(include_superseded=True)
    old = next(item for item in superseded if item.text == "我星期三上课")
    assert old.superseded_by
    text = store.render("pet", query="我星期几上课", max_chars=2000, max_entries=3)
    assert "星期四上课" in text
    assert "\n- 我星期三上课\n" not in text


def test_edit_delete_restore_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    _remember(store, "我喜欢红茶", importance=60)
    store.ingest()
    entry = store.entries()[0]

    assert store.update_entry(
        entry.id, text="我喜欢乌龙茶", importance=90, pinned=True, scope="shared"
    )
    updated = store.find_entry(entry.id)
    assert updated is not None
    assert updated.text == "我喜欢乌龙茶"
    assert updated.importance == 90
    assert updated.pinned is True
    assert updated.scope == "shared"
    assert "乌龙茶" in store.render("pet", max_chars=2000)

    assert store.delete_entry(entry.id) is True
    assert store.entries() == []
    deleted = store.find_entry(entry.id)
    assert deleted is not None and deleted.superseded_by == "deleted"
    assert store.entries(include_superseded=True)

    assert store.restore_entry(entry.id) is True
    restored = store.find_entry(entry.id)
    assert restored is not None and restored.superseded_by == ""
    assert "乌龙茶" in store.render("pet", max_chars=2000)


def test_tidy_merges_duplicates_and_is_idempotent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    card = MemoryCard(
        entries=[
            MemoryEntry(
                id="old",
                text="我星期三上课",
                importance=58,
                created_at=now - 120,
            ),
            MemoryEntry(
                id="new",
                text="我星期三不上课，改为星期四上课了",
                importance=58,
                created_at=now - 60,
            ),
            MemoryEntry(
                id="dup-a",
                text="主人喜欢红茶",
                importance=70,
                created_at=now - 30,
            ),
            MemoryEntry(
                id="dup-b",
                text="主人喜欢红茶",
                importance=80,
                created_at=now - 10,
            ),
        ]
    )
    store.save_card(card)

    result = store.tidy()
    assert result["superseded"] == 1
    assert result["merged"] == 1
    assert store.find_entry("old").superseded_by == "new"
    assert store.find_entry("dup-a").superseded_by == "dup-b"
    assert store.tidy()["merged"] == 0
    assert store.tidy()["superseded"] == 0


def test_remember_can_add_a_pinned_entry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    store.append_inbox(
        {
            "at": time.time(),
            "platform": "pet",
            "sender": "主人",
            "text": "这是置顶事实",
            "is_owner": True,
            "pinned": True,
            "importance": 90,
        }
    )
    store.ingest()
    entry = store.entries()[0]
    assert entry.pinned is True
    assert "这是置顶事实" in store.render("pet", max_chars=2000, max_entries=1)


def test_tidy_does_not_merge_template_facts_with_correction_words(
    tmp_path: Path,
) -> None:
    """模板相同、只有编号不同的事实不能因为「改成」被误合并。"""
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    store.save_card(
        MemoryCard(
            entries=[
                MemoryEntry(
                    id="a",
                    text="第2条：主人喜欢第2种收藏品，改成星期四了",
                    created_at=now - 10,
                ),
                MemoryEntry(
                    id="b",
                    text="第3条：主人喜欢第3种收藏品，改成星期四了",
                    created_at=now,
                ),
            ]
        )
    )
    result = store.tidy()
    assert result["superseded"] == 0
    assert store.find_entry("a").superseded_by == ""
    assert store.find_entry("b").superseded_by == ""


def test_render_dedupes_same_text_across_sources(tmp_path: Path) -> None:
    """多个外部源镜像到同一条事实时，提示词只出现一次。"""
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    store.save_card(
        MemoryCard(
            entries=[
                MemoryEntry(
                    id="local",
                    text="主人喜欢鲸鱼",
                    source="ai",
                    platform="pet",
                    scope="pet",
                    importance=80,
                    created_at=now,
                ),
                MemoryEntry(
                    id="ext-dsh",
                    text="主人喜欢鲸鱼",
                    source="dsh-facts",
                    platform="dsh",
                    scope="dsh",
                    kind="external",
                    importance=100,
                    created_at=now - 10,
                    origin="dsh-facts:f1",
                ),
                MemoryEntry(
                    id="ext-identity",
                    text="主人喜欢鲸鱼",
                    source="identity:owner",
                    platform="dsh",
                    scope="dsh",
                    kind="external",
                    importance=90,
                    created_at=now - 20,
                    origin="identity:i1",
                ),
            ]
        )
    )
    text = store.render("pet", query="鲸鱼", max_chars=2000)
    assert text.count("主人喜欢鲸鱼") == 1
    # 分数最高的本地条目优先，外部镜像不会把它挤掉。
    assert "【和主人】" in text


def test_apply_external_updates_importance_on_amend(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    record = {
        "origin": "feiyu:__default__:f1",
        "text": "被 amend 的事实",
        "importance": 60,
        "created_at": 100.0,
        "source": "feiyu:remember",
    }
    store.apply_external("feiyu:__default__", [record], status="ok", label="feiyu")
    assert store.entries()[0].importance == 60
    record["importance"] = 90
    store.apply_external("feiyu:__default__", [record], status="ok", label="feiyu")
    assert store.entries()[0].importance == 90


def test_dedupe_keeps_first_section_entry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m")
    now = time.time()
    store.save_card(
        MemoryCard(
            entries=[
                MemoryEntry(
                    id="high",
                    text="同一句话",
                    importance=99,
                    created_at=now,
                ),
                MemoryEntry(
                    id="first",
                    text="同一句话",
                    importance=10,
                    created_at=now - 100,
                    first=True,
                    kind="first",
                ),
            ]
        )
    )
    text = store.render("pet", max_chars=2000, max_entries=2)
    assert text.count("同一句话") == 1
    assert "【最初】" in text, "去重不能吃掉 first 的分区保底"


def test_compact_reserves_local_slots_when_external_floods(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, "m", max_entries=50)
    now = time.time()
    store.save_card(
        MemoryCard(
            entries=[
                MemoryEntry(
                    id=f"local-{i}",
                    text=f"本地记忆 {i}",
                    importance=50,
                    created_at=now - i,
                )
                for i in range(50)
            ]
        )
    )
    records = [
        {
            "origin": f"identity:i{i}",
            "text": f"外部记忆 {i}",
            "importance": 90,
            "created_at": now,
            "source": "identity:owner",
        }
        for i in range(50)
    ]
    store.apply_external("identity", records, status="ok", label="identity")
    entries = store.entries()
    assert len(entries) == 50
    local = [entry for entry in entries if not entry.origin]
    assert len(local) >= 30, "外部镜像不能把本地记忆整批挤掉"


def test_replace_with_retry_retries_permission_error(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_replace(_source, _target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "busy")

    monkeypatch.setattr(memmod.os, "replace", fake_replace)
    if os.name == "nt":
        memmod._replace_with_retry("a", tmp_path / "b")
        assert len(calls) == 3
    else:
        with pytest.raises(PermissionError):
            memmod._replace_with_retry("a", tmp_path / "b")
        assert len(calls) == 1


def test_replace_with_retry_does_not_mask_other_errors(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_replace(_source, _target):
        calls.append(1)
        raise FileNotFoundError(2, "missing")

    monkeypatch.setattr(memmod.os, "replace", fake_replace)
    with pytest.raises(FileNotFoundError):
        memmod._replace_with_retry("a", tmp_path / "b")
    assert len(calls) == 1
