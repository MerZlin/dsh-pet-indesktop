from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from petcore.interop.external_memory import (
    assistant_texts,
    read_dsh_facts,
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
