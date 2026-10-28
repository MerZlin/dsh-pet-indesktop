from tests.codex_helpers import bridge
import json
from pet.agent_snapshot import SnapshotReader


def test_session_metadata_and_commentary(tmp_path):
    root = tmp_path / "dsh-pet-bridge"
    log = tmp_path / "rollout.jsonl"
    log.touch()
    bridge.record(dict(hook_event_name="SessionStart", session_id="s", transcript_path=str(log), cwd="/project"), root)
    bridge.record(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t"), root)
    (tmp_path / "session_index.jsonl").write_text(json.dumps(dict(id="s", thread_name="修复桌宠")) + "\n")
    reader = SnapshotReader(root / "state.json")
    first = reader.read()
    assert first["detail"]["title"] == "修复桌宠"
    with log.open("a") as f:
        f.write(
            json.dumps(
                dict(
                    type="response_item",
                    payload=dict(type="message", role="assistant", phase="commentary", content=[dict(type="output_text", text="正在检查动画")]),
                )
            )
            + "\n"
        )
    assert reader.read()["detail"]["text"] == "正在检查动画"
    bridge.record(dict(hook_event_name="Stop", session_id="s", turn_id="t"), root)
    result = reader.read()
    assert result["ended"] == ["stopped"]
    assert not result.get("detail")


def test_partial_and_old_history(tmp_path):
    root = tmp_path / "dsh-pet-bridge"
    log = tmp_path / "rollout.jsonl"
    message = (
        json.dumps(dict(type="response_item", payload=dict(type="message", role="assistant", phase="commentary", content=[dict(text="新的进展")]))).encode()
        + b"\n"
    )
    log.write_bytes(message)
    bridge.record(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t", transcript_path=str(log)), root)
    reader = SnapshotReader(root / "state.json")
    assert not reader.read()["detail"]["text"]
    with log.open("ab") as f:
        f.write(message[:30])
    assert reader.read() is None
    with log.open("ab") as f:
        f.write(message[30:])
    assert reader.read()["detail"]["text"] == "新的进展"


def test_waiting_survives_unrelated_tool_completion(tmp_path):
    payload = dict(session_id="s", turn_id="t")
    for event, call in [("UserPromptSubmit", ""), ("PreToolUse", "a"), ("PreToolUse", "b"), ("PermissionRequest", "a"), ("PostToolUse", "b")]:
        bridge.record(dict(payload, hook_event_name=event, tool_use_id=call), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    assert reader.read()["waiting"] == 1
    bridge.record(dict(payload, hook_event_name="PostToolUse", tool_use_id="a"), tmp_path)
    assert reader.read()["thinking"] == 1


def test_bounded_parser_replacement_and_unknown_records(tmp_path):
    from pet.codex_transcript import JsonlReader, MAX_LINE

    p = tmp_path / "r.jsonl"
    p.write_bytes(b"x" * (MAX_LINE + 1))
    reader = JsonlReader(p)
    for _ in range(6):
        assert reader.read() == []
    with p.open("ab") as f:
        f.write(b'\n{"ok":1}\n')
    assert reader.read() == [{"ok": 1}]
    new = tmp_path / "new"
    new.write_text('{"old":1}\n')
    new.replace(p)
    assert reader.read() == []
    with p.open("a") as f:
        f.write('{"new":1}\n')
    assert reader.read() == [{"new": 1}]


def test_sqlite_fallback_and_renamed_title(tmp_path):
    import sqlite3

    root = tmp_path / "dsh-pet-bridge"
    log = tmp_path / "r.jsonl"
    log.touch()
    with sqlite3.connect(tmp_path / "state_999.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, title TEXT)")
        db.execute("INSERT INTO threads VALUES (?,?,?)", ("s", str(log), "数据库会话名"))
    bridge.record(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t"), root)
    reader = SnapshotReader(root / "state.json")
    assert reader.read()["detail"]["title"] == "数据库会话名"
    (tmp_path / "session_index.jsonl").write_text(json.dumps(dict(id="s", thread_name="新名字")) + "\n")
    assert reader.read()["detail"]["title"] == "新名字"


def test_multi_session_waiting_priority_and_late_events(tmp_path):
    for sid in ["a", "b"]:
        bridge.record(dict(hook_event_name="UserPromptSubmit", session_id=sid, turn_id="t"), tmp_path)
    bridge.record(dict(hook_event_name="PermissionRequest", session_id="a", turn_id="t", tool_use_id="x"), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    summary = reader.read()
    assert summary["busy"] == 1 and summary["waiting"] == 1
    assert summary["detail"]["state"] == "waiting"
    bridge.record(dict(hook_event_name="Stop", session_id="a", turn_id="t"), tmp_path)
    summary = reader.read()
    assert summary["detail"]["state"] == "thinking"
    bridge.record(dict(hook_event_name="PostToolUse", session_id="a", turn_id="t", tool_use_id="x"), tmp_path)
    assert reader.read() is None


def test_foreign_turn_and_reasoning_are_not_shown(tmp_path):
    root = tmp_path / "bridge"
    log = tmp_path / "r.jsonl"
    log.touch()
    bridge.record(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t", transcript_path=str(log)), root)
    reader = SnapshotReader(root / "state.json")
    reader.read()
    with log.open("a") as f:
        for record in [
            dict(type="response_item", payload=dict(type="reasoning", encrypted_content="private", summary=[dict(text="private")])),
            dict(type="turn_context", payload=dict(turn_id="other")),
            dict(type="response_item", payload=dict(type="message", role="assistant", phase="commentary", content=[dict(text="foreign")])),
        ]:
            f.write(json.dumps(record) + "\n")
    assert reader.read() is None


def test_session_start_without_turn_and_compact_does_not_reannounce(tmp_path):
    reader = SnapshotReader(tmp_path / "state.json")
    reader.read()
    bridge.record(dict(hook_event_name="SessionStart", session_id="s", source="startup", cwd="/project/demo"), tmp_path)
    assert reader.read()["opened"] == ["demo"]
    assert reader.read() is None
    bridge.record(dict(hook_event_name="SessionStart", session_id="s", source="compact", cwd="/project/demo"), tmp_path)
    assert reader.read() is None


def test_two_permissions_need_two_matching_results(tmp_path):
    payload = dict(session_id="s", turn_id="t")
    for event, call in [("UserPromptSubmit", ""), ("PermissionRequest", "a"), ("PermissionRequest", "b"), ("PostToolUse", "b")]:
        bridge.record(dict(payload, hook_event_name=event, tool_use_id=call), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    assert reader.read()["waiting"] == 1
    bridge.record(dict(payload, hook_event_name="PostToolUse", tool_use_id="a"), tmp_path)
    assert reader.read()["waiting"] == 0


def test_index_replacement_and_malformed_items(tmp_path):
    from pet.codex_transcript import JsonlReader, TranscriptDetails

    p = tmp_path / "index"
    p.write_text('{"id":"a"}\n')
    reader = JsonlReader(p, backfill=True)
    assert reader.read() == [{"id": "a"}]
    new = tmp_path / "new"
    new.write_text('{"id":"b"}\n')
    new.replace(p)
    assert reader.read() == [{"id": "b"}]
    cache = dict(context="", kind="", at=0, text="")
    TranscriptDetails._consume(dict(type="response_item", payload=dict(type=[])), {}, cache, 0)
    assert cache["text"] == ""


def test_resume_title_available_before_any_prompt(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "state_888.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, title TEXT)")
        db.execute("INSERT INTO threads VALUES (?,?,?)", ("s", "", "恢复的旧会话"))
    root = tmp_path / "dsh-pet-bridge"
    reader = SnapshotReader(root / "state.json")
    reader.read()
    bridge.record(dict(hook_event_name="SessionStart", session_id="s", source="resume"), root)
    assert reader.read()["opened"] == ["恢复的旧会话"]


def test_final_after_stop_once_and_no_history(tmp_path):
    log = tmp_path / "r.jsonl"
    log.touch()
    payload = dict(session_id="s", turn_id="t", transcript_path=str(log))
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    reader.read()
    bridge.record(dict(payload, hook_event_name="Stop"), tmp_path)
    assert reader.read()["ended"] == ["stopped"]
    with log.open("a") as f:
        f.write(
            json.dumps(dict(type="response_item", payload=dict(type="message", role="assistant", phase="final_answer", content=[dict(text="已修复并验证。")])))
            + "\n"
        )
    result = reader.read()
    assert result["final"]["text"] == "已修复并验证。"
    assert not result["detail"]
    assert not (reader.read() or {}).get("final")
    assert not SnapshotReader(tmp_path / "state.json").read().get("final")


def test_final_cached_before_stop_and_interrupt_ignored(tmp_path):
    log = tmp_path / "r.jsonl"
    log.touch()
    payload = dict(session_id="s", turn_id="t", transcript_path=str(log))
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    reader.read()
    with log.open("a") as f:
        f.write(
            json.dumps(dict(type="response_item", payload=dict(type="message", role="assistant", phase="final_answer", content=[dict(text="最终回复")]))) + "\n"
        )
    assert reader.read() is None
    bridge.record(dict(payload, hook_event_name="Stop"), tmp_path)
    assert reader.read()["final"]["text"] == "最终回复"
    payload["turn_id"] = "next"
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), tmp_path)
    reader.read()
    bridge.record(dict(payload, hook_event_name="Interrupt"), tmp_path)
    assert not reader.read().get("final")


def test_late_reply_expires(tmp_path):
    import time

    now = [time.time()]
    log = tmp_path / "r.jsonl"
    log.touch()
    payload = dict(session_id="s", turn_id="t", transcript_path=str(log))
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json", clock=lambda: now[0])
    reader.read()
    bridge.record(dict(payload, hook_event_name="Stop"), tmp_path)
    reader.read()
    now[0] += 11
    with log.open("a") as f:
        f.write(
            json.dumps(dict(type="response_item", payload=dict(type="message", role="assistant", phase="final_answer", content=[dict(text="过期回复")]))) + "\n"
        )
    assert not (reader.read() or {}).get("final")


def test_current_activity_and_no_completion_bubbles(tmp_path):
    log = tmp_path / "rollout.jsonl"
    log.touch()
    payload = dict(session_id="s", turn_id="t", transcript_path=str(log))
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    assert reader.read()["detail"]["activity"] == "思考中"
    bridge.record(dict(payload, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="a"), tmp_path)
    detail = reader.read()["detail"]
    assert detail["activity"] == "运行命令"
    with log.open("a") as f:
        f.write(json.dumps(dict(type="event_msg", payload=dict(type="item_completed", item=dict(type="CommandExecution", exit_code=0)))) + "\n")
    assert reader.read() is None
    bridge.record(dict(payload, hook_event_name="PostToolUse", tool_name="Bash", tool_use_id="a"), tmp_path)
    assert reader.read()["detail"]["activity"] == "思考中"
    bridge.record(dict(payload, hook_event_name="PreToolUse", tool_name="unknown_tool", tool_use_id="b"), tmp_path)
    assert reader.read()["detail"]["activity"] == "调用工具"


def test_commentary_survives_completed_items(tmp_path):
    from pet.codex_transcript import TranscriptDetails

    cached = dict(context="", kind="", at=0, text="")
    entry = dict(turn_id="t")
    TranscriptDetails._consume(
        dict(type="response_item", payload=dict(type="message", role="assistant", phase="commentary", content=[dict(text="检查联动状态")])), entry, cached, 0
    )
    for kind in ["CommandExecution", "FileChange", "WebSearch"]:
        TranscriptDetails._consume(dict(type="event_msg", payload=dict(type="item_completed", item=dict(type=kind))), entry, cached, 20)
    assert cached["text"] == "检查联动状态"
