"""Concurrent local Codex threads remain independent of recency and notices."""

import json
import sqlite3

import pytest

from pet.codex_work_reader import CodexWorkReader, NoticeLedger


@pytest.fixture
def work_databases(tmp_path):
    with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
        conn.execute("CREATE TABLE threads(id TEXT, name TEXT, title TEXT, rollout_path TEXT, updated_at INT, source TEXT, archived INT, agent_path TEXT)")
    with sqlite3.connect(tmp_path / "thread_history_1.sqlite") as conn:
        conn.execute("CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, status TEXT, started_at INT, completed_at INT)")
        conn.execute("CREATE TABLE thread_items(thread_id TEXT, turn_id TEXT, item_type TEXT, created_at_ms INT, item_json TEXT, rollout_ordinal INT)")

    def add(thread, status="inProgress", kind="reasoning", stamp=100000, source="vscode", agent_path=None, archived=0):
        rollout = tmp_path / (thread + ".jsonl")
        rollout.write_text("", encoding="utf-8")
        with sqlite3.connect(tmp_path / "state_5.sqlite") as conn:
            conn.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)", (thread, thread, "raw prompt", str(rollout), stamp, source, archived, agent_path))
        with sqlite3.connect(tmp_path / "thread_history_1.sqlite") as conn:
            conn.execute("INSERT INTO thread_turns VALUES(?,?,?,?,?)", (thread, "turn-" + thread, status, stamp, stamp if status == "completed" else None))
            conn.execute("INSERT INTO thread_items VALUES(?,?,?,?,?,?)", (thread, "turn-" + thread, kind, stamp * 1000, "{}", 1))
        return rollout

    return tmp_path, add


def test_concurrent_threads_survive_recent_notifications_and_long_turns(work_databases):
    root, add = work_databases
    add("thinking", stamp=1000)
    add("coding", kind="commandExecution", stamp=2000)
    add("root-path", agent_path="/root", stamp=99000)
    add("guardian", source='{"subagent":{"other":"guardian"}}')
    add("child", agent_path="/root/helper")
    add("archived", archived=1)
    for index in range(30):
        add(f"done-{index}", status="completed", stamp=100000 + index)
    result = CodexWorkReader(root).read(now=100100)
    assert result["ok"]
    active = {r["threadId"]: r["state"] for r in result["rows"] if r["state"] in ("thinking", "coding")}
    assert active == {"thinking": "thinking", "coding": "coding", "root-path": "thinking"}
    assert len(result["rows"]) == 33
    ledger = NoticeLedger(root / "seen.json")
    ledger.mark(result["rows"])
    assert {r["threadId"] for r in ledger.visible(result["rows"])} == set(active)


def test_attention_and_completion_of_one_thread_do_not_replace_others(work_databases):
    root, add = work_databases
    question = add("question")
    add("coding", kind="fileChange")
    add("thinking")
    question.write_text(
        json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "request_user_input_async", "call_id": "question-1"}}) + "\n",
        encoding="utf-8",
    )
    reader = CodexWorkReader(root)
    result = reader.read(now=100100)
    assert {r["threadId"]: r["state"] for r in result["rows"]} == {"question": "attention", "coding": "coding", "thinking": "thinking"}
    ledger = NoticeLedger(root / "seen.json")
    ledger.mark([result["rows"][0]])
    assert {r["threadId"] for r in ledger.visible(result["rows"])} == {"coding", "thinking"}
    with question.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user"}}) + "\n")
    with sqlite3.connect(root / "thread_history_1.sqlite") as conn:
        conn.execute("UPDATE thread_turns SET status='completed', completed_at=100101 WHERE thread_id='coding'")
    result = reader.read(now=100102)
    assert {r["threadId"]: r["state"] for r in ledger.visible(result["rows"])} == {"question": "thinking", "coding": "completed", "thinking": "thinking"}


def test_collapsed_island_and_concurrent_rows_in_real_qt_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "scripts/probe_codex_work_status.py"), "--output", str(tmp_path)],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads((tmp_path / "multi-work-qt.json").read_text(encoding="utf-8"))
    assert value["collapsedBackgroundUpdates"]
    assert value["allSevenRowsScrollable"]
    assert value["perThreadAcknowledgement"]
