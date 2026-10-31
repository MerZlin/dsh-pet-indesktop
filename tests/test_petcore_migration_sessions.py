"""旧壳会话迁移的回归测试。

口径：ISO → epoch float、消息裁剪与角色归一、目标不覆盖、幂等、dry-run 不写盘。
所有用例只在 ``tmp_path`` 里造数据，不碰真实用户目录。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from petcore import migration
from petcore.chat.session_store import ChatSession
from petcore.migration import sessions


@pytest.fixture(autouse=True)
def _only_sessions_domain(monkeypatch):
    """只跑 sessions 域：其余域在并行开发，缺模块与本文件无关。"""
    monkeypatch.setattr(migration, "_domain_functions", lambda: (("sessions", sessions.migrate),))


def _legacy_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": "s1",
        "character_id": "cat",
        "provider_id": "deepseek",
        "system_prompt": "你是猫",
        "created_at": "2026-01-02T03:04:05+00:00",
        "updated_at": "2026-01-02T03:04:05+00:00",
        "title": "旧会话",
        "pinned": False,
        "messages": [
            {"role": "user", "content": "喵", "message_id": "m1"},
            {"role": "assistant", "content": "喵喵", "message_id": "m2"},
        ],
    }
    payload.update(overrides)
    return payload


def _write_legacy(
    root: Path,
    *,
    base: str = "sessions",
    character: str = "cat",
    session_id: str = "s1",
    **overrides: Any,
) -> Path:
    path = root / base / character / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_legacy_payload(session_id=session_id, **overrides), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _run(root: Path, legacy: Path, **kwargs: Any):
    return migration.run(root, legacy_root=legacy, **kwargs)


def test_migrates_iso_timestamp_and_roundtrips(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy)
    root = tmp_path / "new"
    root.mkdir()

    report = _run(root, legacy)
    entries = report.by_kind("sessions")
    assert [entry.status for entry in entries] == ["migrated"]
    assert entries[0].count == 2

    target = root / "sessions" / "cat" / "s1.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["schema"] == 2
    assert raw["channel"] == "ai"
    assert raw["session_id"] == "s1"
    assert isinstance(raw["updated_at"], float)
    assert raw["updated_at"] == pytest.approx(
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
    )
    loaded = ChatSession.from_dict(raw, "s1")
    assert loaded.title == "旧会话"
    assert [message["content"] for message in loaded.messages] == ["喵", "喵喵"]

    # 丢弃项必须计数，不许静默。
    assert "dropped:provider_id,system_prompt,pinned" in entries[0].detail

    marker = json.loads((root / "migration.json").read_text(encoding="utf-8"))
    assert "sessions" in marker["completed"]
    # 目标原本不存在：回滚映射的 backup 是空串（框架把 Path("") 折成 ""）。
    assert marker["restore"] == [{"kind": "sessions", "backup": "", "target": str(target)}]


def test_naive_iso_is_treated_as_local_time(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy, updated_at="2026-01-02T03:04:05")
    root = tmp_path / "new"
    root.mkdir()

    _run(root, legacy)

    raw = json.loads((root / "sessions" / "cat" / "s1.json").read_text(encoding="utf-8"))
    assert raw["updated_at"] == pytest.approx(datetime(2026, 1, 2, 3, 4, 5).astimezone().timestamp())


def test_unparsable_timestamp_falls_back_to_mtime(tmp_path):
    legacy = tmp_path / "legacy"
    source = _write_legacy(legacy, updated_at="not-a-time")
    root = tmp_path / "new"
    root.mkdir()

    entries = _run(root, legacy).by_kind("sessions")

    assert "mtime-fallback" in entries[0].detail
    raw = json.loads((root / "sessions" / "cat" / "s1.json").read_text(encoding="utf-8"))
    assert raw["updated_at"] == pytest.approx(source.stat().st_mtime)


def test_slot_variant_keeps_its_own_root(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy, base="sessions-slot-2", character="dog", session_id="s9")
    root = tmp_path / "new"
    root.mkdir()

    entries = _run(root, legacy).by_kind("sessions")

    assert [entry.status for entry in entries] == ["migrated"]
    assert (root / "sessions-slot-2" / "dog" / "s9.json").is_file()
    assert not (root / "sessions").exists()


def test_second_run_is_idempotent(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy)
    root = tmp_path / "new"
    root.mkdir()

    first = _run(root, legacy, force=True)
    assert [entry.status for entry in first.by_kind("sessions")] == ["migrated"]

    target = root / "sessions" / "cat" / "s1.json"
    before = target.read_bytes()

    second = _run(root, legacy, force=True)
    assert [entry.status for entry in second.by_kind("sessions")] == ["already_migrated"]
    assert target.read_bytes() == before


def test_existing_target_is_not_overwritten(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy)
    root = tmp_path / "new"
    root.mkdir()
    target = root / "sessions" / "cat" / "s1.json"
    target.parent.mkdir(parents=True)
    original = {
        "schema": 1,
        "session_id": "s1",
        "title": "新壳自己写的",
        "messages": [],
        "updated_at": 1.0,
    }
    target.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    entries = _run(root, legacy).by_kind("sessions")

    assert [entry.status for entry in entries] == ["already_migrated"]
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_bad_json_is_skipped_and_source_kept(tmp_path):
    legacy = tmp_path / "legacy"
    broken = legacy / "sessions" / "cat" / "broken.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{oops", encoding="utf-8")
    root = tmp_path / "new"
    root.mkdir()

    entries = _run(root, legacy).by_kind("sessions")

    assert [entry.status for entry in entries] == ["skipped_invalid"]
    assert broken.read_text(encoding="utf-8") == "{oops"
    assert not (root / "sessions" / "cat" / "broken.json").exists()


def test_messages_are_trimmed_and_roles_normalised(tmp_path):
    legacy = tmp_path / "legacy"
    messages = [
        {"role": "wizard" if index % 2 else "user", "content": f"m{index}"}
        for index in range(250)
    ]
    messages[248]["content"] = "x" * 30_000
    messages[249] = {
        "role": "assistant",
        "content": "尾部",
        "reasoning": "r" * 30_000,
        "activity": [f"a{index}" for index in range(60)],
    }
    _write_legacy(legacy, messages=messages, title="T" * 200)
    root = tmp_path / "new"
    root.mkdir()

    entries = _run(root, legacy).by_kind("sessions")
    raw = json.loads((root / "sessions" / "cat" / "s1.json").read_text(encoding="utf-8"))

    assert entries[0].count == 200
    assert "role-normalized:" in entries[0].detail
    assert "trimmed-messages:50" in entries[0].detail
    assert len(raw["title"]) == 120
    assert len(raw["messages"]) == 200
    assert {message["role"] for message in raw["messages"]} <= {"user", "assistant"}
    assert raw["messages"][-1]["content"] == "尾部"
    assert len(raw["messages"][-1]["reasoning"]) == 24_000
    assert len(raw["messages"][-1]["activity"]) == 40
    assert raw["messages"][-1]["activity"][-1] == "a59"
    assert all(len(message["content"]) <= 24_000 for message in raw["messages"])
    assert any(message["content"] == "x" * 24_000 for message in raw["messages"])
    assert ChatSession.from_dict(raw, "s1").messages[-1]["content"] == "尾部"


def test_dry_run_writes_nothing(tmp_path):
    legacy = tmp_path / "legacy"
    _write_legacy(legacy)
    root = tmp_path / "new"
    root.mkdir()

    report = _run(root, legacy, dry_run=True)
    entries = report.by_kind("sessions")

    assert [entry.status for entry in entries] == ["migrated"]
    assert list(root.iterdir()) == []
    assert not (root / "migration.json").exists()


def test_missing_legacy_sessions_report_not_found(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    root = tmp_path / "new"
    root.mkdir()

    entries = _run(root, legacy).by_kind("sessions")

    assert [entry.status for entry in entries] == ["not_found"]
    assert entries[0].detail == "no-legacy-sessions"
