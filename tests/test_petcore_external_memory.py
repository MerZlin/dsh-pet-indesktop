from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from petcore.interop.external_memory import (
    assistant_texts,
    read_dsh_facts,
    read_external_sources,
    read_feiyu_logs,
    read_identity_facts,
    read_meow_db,
)
from petcore.interop.memory import MemoryStore


def _write_facts(root: Path, rows: list[dict]) -> Path:
    path = root / "memory" / "facts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_read_dsh_facts_skips_superseded_and_maps_importance(tmp_path: Path) -> None:
    _write_facts(
        tmp_path,
        [
            {"id": "f1", "text": "用户叫小张", "importance": 5, "created_at": 100.0},
            {
                "id": "f2",
                "text": "已经改掉的旧事",
                "importance": 3,
                "created_at": 90.0,
                "superseded_by": "f1",
            },
        ],
    )
    result = read_dsh_facts(tmp_path)
    assert result.status == "ok"
    assert len(result.records) == 1
    record = result.records[0]
    assert record["origin"] == "dsh-facts:f1"
    assert record["importance"] == 100
    assert record["scope"] == "dsh"


def test_read_dsh_facts_missing_file_degrades(tmp_path: Path) -> None:
    result = read_dsh_facts(tmp_path)
    assert result.status == "not_found"
    assert result.records == ()


def _make_meow_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE fact (id TEXT, content TEXT, status TEXT, updated_at REAL)"
    )
    connection.execute(
        "CREATE TABLE user (id TEXT, content TEXT, status TEXT, updated_at REAL)"
    )
    now = time.time()
    connection.execute(
        "INSERT INTO fact VALUES ('m1', '主人喜欢鲸鱼', 'active', ?)", (now,)
    )
    connection.execute(
        "INSERT INTO fact VALUES ('m2', '已归档的事实', 'archived', ?)", (now,)
    )
    connection.execute(
        "INSERT INTO user VALUES ('u1', '用户叫小张', 'active', ?)", (now,)
    )
    connection.commit()
    connection.close()


def test_read_meow_db_is_read_only_and_skips_archived(tmp_path: Path) -> None:
    path = tmp_path / "ws" / ".dsh-meow" / "memory.db"
    _make_meow_db(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = read_meow_db(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.status == "ok"
    assert len(result.records) == 2
    assert {record["text"] for record in result.records} == {
        "主人喜欢鲸鱼",
        "用户叫小张",
    }
    assert all(record["origin"].startswith("meow:") for record in result.records)
    assert before == after


def test_apply_external_updates_removes_and_mutes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", "m")
    records = [
        {
            "origin": "dsh-facts:f1",
            "text": "用户叫小张",
            "importance": 100,
            "created_at": 100.0,
            "source": "dsh-facts",
        },
        {
            "origin": "dsh-facts:f2",
            "text": "主人喜欢鲸鱼",
            "importance": 80,
            "created_at": 101.0,
            "source": "dsh-facts",
        },
    ]
    result = store.apply_external("dsh-facts", records, status="ok", label="dsh")
    assert result["added"] == 2
    assert "小张" in store.render("pet", max_chars=2000)
    status = store.external_status()
    assert status["sources"][0]["count"] == 2

    # 源里改名：镜像更新而不是新增
    records[0]["text"] = "用户叫小张先生"
    result = store.apply_external("dsh-facts", records, status="ok", label="dsh")
    assert result["updated"] == 1
    assert "小张先生" in store.render("pet", max_chars=2000)

    # 源里删掉一条：镜像移除
    result = store.apply_external("dsh-facts", records[:1], status="ok", label="dsh")
    assert result["removed"] == 1
    assert "主人喜欢鲸鱼" not in store.render("pet", max_chars=2000)

    # 静音后不再回来
    entry = next(item for item in store.entries() if item.origin == "dsh-facts:f1")
    assert store.mute_external(entry.id) is True
    store.apply_external("dsh-facts", records, status="ok", label="dsh")
    assert not any(item.origin == "dsh-facts:f1" for item in store.entries())
    assert store.external_status()["muted"] == 1


def test_mark_replies_uses_session_texts(tmp_path: Path) -> None:
    root = tmp_path / "data"
    session_dir = root / "sessions" / "shen"
    session_dir.mkdir(parents=True)
    (session_dir / "s1.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "session_id": "s1",
                "messages": [
                    {"role": "assistant", "content": "我记住啦，主人喜欢红茶喵。"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    texts = assistant_texts(root, "shen")
    assert texts

    store = MemoryStore(tmp_path / "memory", "shen")
    store.append_inbox(
        {
            "at": time.time(),
            "platform": "pet",
            "sender": "桌宠",
            "text": "我记住啦，主人喜欢红茶喵。",
            "is_owner": False,
            "is_self": True,
        }
    )
    store.ingest()
    assert store.mark_replies(texts) == 1
    assert store.entries(include_replies=True)[0].kind == "reply"
    assert "我记住啦" not in store.render("pet", max_chars=2000)


def _write_identity(root: Path, rows: list[dict], *, bad_line: bool = False) -> Path:
    path = root / "facts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if bad_line:
        body += "\nnot-json"
    path.write_text(body + "\n", encoding="utf-8")
    return path


def test_read_identity_facts_maps_active_records_and_supersedes_subject(
    tmp_path: Path,
) -> None:
    _write_identity(
        tmp_path,
        [
            {
                "id": "i1",
                "content": "主人喜欢鲸鱼",
                "source": "owner",
                "status": "active",
                "time": "2026-01-01T00:00:00Z",
                "subject": "pet",
            },
            {
                "id": "i2",
                "content": "旧事实",
                "source": "self",
                "status": "superseded",
                "supersededBy": "i1",
                "time": "2026-01-01T00:00:00Z",
            },
            {
                "id": "i3",
                "content": "证据事实",
                "source": "evidence",
                "status": "active",
                "time": "2026-01-02T00:00:00Z",
            },
            {
                "id": "i4",
                "content": "旧偏好",
                "source": "owner",
                "status": "active",
                "subject": "pref",
                "time": "2026-01-01T00:00:00Z",
            },
            {
                "id": "i5",
                "content": "新偏好",
                "source": "owner",
                "status": "active",
                "subject": "pref",
                "time": "2026-01-03T00:00:00Z",
            },
            {
                "id": "i6",
                "content": "已归档",
                "source": "owner",
                "status": "active",
                "archivedBy": "x",
                "time": "2026-01-04T00:00:00Z",
            },
        ],
    )
    result = read_identity_facts(tmp_path)
    assert result.status == "ok"
    texts = {record["text"] for record in result.records}
    assert texts == {"主人喜欢鲸鱼", "证据事实", "新偏好"}
    by_text = {record["text"]: record for record in result.records}
    assert by_text["主人喜欢鲸鱼"]["importance"] == 75
    assert by_text["证据事实"]["importance"] == 60
    assert by_text["主人喜欢鲸鱼"]["origin"] == "identity:i1"
    assert by_text["主人喜欢鲸鱼"]["scope"] == "dsh"
    assert by_text["主人喜欢鲸鱼"]["source"] == "identity:owner"


def test_read_identity_facts_missing_and_partial(tmp_path: Path) -> None:
    missing = read_identity_facts(tmp_path / "nope")
    assert missing.status == "not_found"
    assert missing.records == ()

    _write_identity(
        tmp_path,
        [
            {
                "id": "i1",
                "content": "坏行旁边的事实",
                "source": "owner",
                "status": "active",
                "time": "2026-01-01T00:00:00Z",
            }
        ],
        bad_line=True,
    )
    result = read_identity_facts(tmp_path)
    assert result.status == "partial"
    assert [record["text"] for record in result.records] == ["坏行旁边的事实"]
    assert "1 行" in result.reason


def test_identity_reader_is_read_only(tmp_path: Path) -> None:
    path = _write_identity(
        tmp_path,
        [
            {
                "id": "i1",
                "content": "只读事实",
                "source": "owner",
                "status": "active",
                "time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    read_identity_facts(tmp_path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after


def test_apply_external_partial_does_not_remove_mirrors(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", "m")
    records = [
        {
            "origin": "identity:i1",
            "text": "只读事实",
            "importance": 90,
            "created_at": 100.0,
            "source": "identity:owner",
        }
    ]
    store.apply_external("identity", records, status="ok", label="身份接力")
    assert "只读事实" in store.render("pet", max_chars=2000)

    result = store.apply_external(
        "identity", [], status="partial", label="身份接力", reason="坏行"
    )
    assert result["removed"] == 0
    assert "只读事实" in store.render("pet", max_chars=2000)


def _write_feiyu_log(root: Path, records: list[dict], *, bad_line: bool = False) -> Path:
    path = root / "sessions" / "__default__" / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    if bad_line:
        body += "\nnot-json"
    path.write_text(body + "\n", encoding="utf-8")
    return path


def test_read_feiyu_logs_replays_ops_and_skips_non_memory_kinds(
    tmp_path: Path,
) -> None:
    _write_feiyu_log(
        tmp_path,
        [
            {
                "op": "add",
                "e": {
                    "id": "e1",
                    "kind": "event",
                    "t": 1700000000000,
                    "gist": "今天很开心",
                    "importance": 0.6,
                    "tags": ["日结"],
                    "src": "fy-mod-puls-max",
                },
            },
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000001000,
                    "gist": "主人喜欢鲸鱼",
                    "importance": 0.8,
                    "src": "remember",
                },
            },
            {
                "op": "add",
                "e": {
                    "id": "d1",
                    "kind": "doc",
                    "t": 1700000002000,
                    "gist": "文档正文不进提示词",
                },
            },
            {
                "op": "add",
                "e": {
                    "id": "s1",
                    "kind": "skill",
                    "t": 1700000003000,
                    "gist": "技能也不进",
                },
            },
            {
                "op": "amend",
                "id": "f1",
                "patch": {"gist": "主人喜欢鲸鱼和红茶", "importance": 0.9},
            },
            {"op": "del", "id": "e1"},
            {
                "op": "add",
                "e": {
                    "id": "e3",
                    "kind": "event",
                    "t": 1700000004000,
                    "gist": "恢复的事件",
                    "importance": 0.5,
                },
            },
            {"op": "del", "id": "e3"},
            {"op": "restore", "id": "e3"},
            {
                "op": "add",
                "e": {
                    "id": "e4",
                    "kind": "event",
                    "t": 1700000005000,
                    "gist": "里程碑事件",
                    "importance": 0.95,
                    "milestone": True,
                    "src": "fy-mod-puls-max",
                },
            },
            {
                "op": "add",
                "e": {
                    "id": "e5",
                    "kind": "event",
                    "t": 1700000006000,
                    "gist": "被取代的事件",
                    "importance": 0.5,
                },
            },
            {"op": "supersede", "id": "e5", "by": "e4"},
            {
                "op": "add",
                "e": {
                    "id": "e6",
                    "kind": "fact",
                    "t": 1700000007000,
                    "gist": "被清除的事实",
                    "importance": 0.5,
                },
            },
            {"op": "purge", "id": "e6"},
        ],
        bad_line=True,
    )
    result = read_feiyu_logs(tmp_path)
    assert result.status == "partial"
    texts = {record["text"] for record in result.records}
    assert texts == {"主人喜欢鲸鱼和红茶", "恢复的事件", "里程碑事件"}
    by_text = {record["text"]: record for record in result.records}
    assert by_text["主人喜欢鲸鱼和红茶"]["importance"] == 90
    assert by_text["里程碑事件"]["importance"] == 100
    assert by_text["里程碑事件"]["origin"].startswith("feiyu:__default__:")
    assert by_text["里程碑事件"]["scope"] == "dsh"


def test_read_feiyu_logs_missing_and_read_only(tmp_path: Path) -> None:
    missing = read_feiyu_logs(tmp_path)
    assert missing.status == "not_found"
    path = _write_feiyu_log(
        tmp_path,
        [
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000000000,
                    "gist": "只读事实",
                    "importance": 0.7,
                },
            }
        ],
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = read_feiyu_logs(tmp_path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.status == "ok"
    assert [record["text"] for record in result.records] == ["只读事实"]
    assert before == after


def test_read_external_sources_includes_identity_and_feiyu(tmp_path: Path) -> None:
    identity = tmp_path / "identity"
    _write_identity(
        identity,
        [
            {
                "id": "i1",
                "content": "身份事实",
                "source": "owner",
                "status": "active",
                "time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    feiyu = tmp_path / "feiyu"
    _write_feiyu_log(
        feiyu,
        [
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000000000,
                    "gist": "feiyu 事实",
                    "importance": 0.7,
                },
            }
        ],
    )
    sources = read_external_sources(
        tmp_path,
        tmp_path,
        identity_dir=identity,
        feiyu_root=feiyu,
        feiyu_session="__default__",
    )
    keys = {source.key for source in sources}
    assert "identity" in keys
    assert "feiyu:__default__" in keys
    assert {record["text"] for source in sources for record in source.records} >= {
        "身份事实",
        "feiyu 事实",
    }


def test_identity_empty_or_truncated_file_is_partial(tmp_path: Path) -> None:
    path = tmp_path / "facts.jsonl"
    path.write_text("", encoding="utf-8")
    result = read_identity_facts(tmp_path)
    assert result.status == "partial"
    assert result.records == ()
    assert "为空" in result.reason

    store = MemoryStore(tmp_path / "memory", "m")
    store.apply_external(
        "identity",
        [
            {
                "origin": "identity:i1",
                "text": "已经在卡里的身份事实",
                "importance": 75,
                "created_at": 100.0,
                "source": "identity:owner",
            }
        ],
        status="ok",
        label="身份接力",
    )
    store.apply_external("identity", [], status=result.status, label="身份接力")
    assert "已经在卡里的身份事实" in store.render("pet", max_chars=2000)


def test_identity_subject_without_time_prefers_later_line(tmp_path: Path) -> None:
    _write_identity(
        tmp_path,
        [
            {
                "id": "old",
                "content": "杭州",
                "source": "owner",
                "status": "active",
                "subject": "city",
                "time": "2026-01-01T00:00:00Z",
            },
            {
                "id": "new",
                "content": "上海",
                "source": "owner",
                "status": "active",
                "subject": "city",
            },
        ],
    )
    result = read_identity_facts(tmp_path)
    assert [record["text"] for record in result.records] == ["上海"]


def test_feiyu_unknown_op_does_not_freeze_removal(tmp_path: Path) -> None:
    _write_feiyu_log(
        tmp_path,
        [
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000000000,
                    "gist": "仍然有效",
                    "importance": 0.7,
                },
            },
            {"op": "link", "id": "f1", "to": "x"},
            {"op": "del", "id": "missing"},
        ],
    )
    result = read_feiyu_logs(tmp_path)
    assert result.status == "ok"
    assert [record["text"] for record in result.records] == ["仍然有效"]
    assert "忽略" in result.reason

    store = MemoryStore(tmp_path / "memory", "m")
    store.apply_external(
        "feiyu:__default__",
        [
            {
                "origin": "feiyu:__default__:gone",
                "text": "已经消失的旧镜像",
                "importance": 60,
                "created_at": 100.0,
                "source": "feiyu:remember",
            }
        ],
        status="ok",
        label="feiyu",
    )
    store.apply_external(
        "feiyu:__default__",
        list(result.records),
        status=result.status,
        label="feiyu",
    )
    assert "已经消失的旧镜像" not in store.render("pet", max_chars=2000)


def test_feiyu_session_name_cannot_escape_sessions_dir(tmp_path: Path) -> None:
    root = tmp_path / "feiyu"
    root.mkdir()
    (root / "log.jsonl").write_text(
        json.dumps(
            {
                "op": "add",
                "e": {
                    "id": "f1",
                    "kind": "fact",
                    "t": 1700000000000,
                    "gist": "根目录不应被读",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = read_feiyu_logs(root, "..")
    assert result.status == "not_found"
    assert result.records == ()
