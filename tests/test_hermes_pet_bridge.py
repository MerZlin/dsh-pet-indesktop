from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "integrations" / "hermes-pet-bridge" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("hermes_pet_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class HookRegistry:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def _read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _reset():
    bridge._LAST_EMITTED = None
    bridge._SESSION_STATES.clear()
    bridge._API_SERVER_SESSIONS.clear()
    getattr(bridge, "_ACTIVE_TOOLS", {}).clear()
    getattr(bridge, "_FOREGROUND_TURNS", set()).clear()
    getattr(bridge, "_TURN_SESSION_IDS", {}).clear()


def test_registers_activity_and_human_input_observer_hooks():
    ctx = HookRegistry()
    bridge.register(ctx)
    assert set(ctx.hooks) == {
        "pre_llm_call", "pre_tool_call", "post_tool_call", "on_session_end",
        "on_human_input_request", "on_human_input_resolved",
    }


def test_writes_minimal_state_events_and_omits_prompt_and_tool_args(tmp_path, monkeypatch):
    path = tmp_path / "pet" / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    ctx = HookRegistry()
    bridge.register(ctx)

    ctx.hooks["pre_llm_call"](session_id="s-1", user_message="PRIVATE PROMPT")
    ctx.hooks["pre_tool_call"](
        session_id="s-1", tool_name="terminal", args={"command": "PRIVATE COMMAND"}
    )
    ctx.hooks["post_tool_call"](session_id="s-1", tool_name="terminal", status="success", result="PRIVATE OUTPUT")
    ctx.hooks["on_session_end"](session_id="s-1", completed=True, failed=False, interrupted=False)

    records = _read(path)
    assert [row["state"] for row in records] == ["thinking", "working", "thinking", "idle"]
    assert records[1]["tool"] == "terminal"
    serialized = path.read_text(encoding="utf-8")
    assert "PRIVATE" not in serialized
    assert all(set(row) <= {"ts", "agent", "inst", "state", "tool"} for row in records)


def test_session_end_uses_documented_completion_fields(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_session_end(session_id="one", completed=False, interrupted=True)
    assert _read(path)[-1]["state"] == "attention"
    bridge._on_session_end(session_id="two", completed=True, interrupted=False)
    assert _read(path)[-1]["state"] == "idle"


def test_failed_session_emits_error_state(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="failed-session", platform="cli")
    bridge._on_session_end(
        session_id="failed-session", completed=False, failed=True,
        interrupted=False, platform="cli",
    )
    assert _read(path)[-1]["state"] == "error"


def test_api_server_chat_activity_is_suppressed(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    ctx = HookRegistry()
    bridge.register(ctx)

    ctx.hooks["pre_llm_call"](session_id="pet-chat", platform="api_server")
    # Tool hooks do not carry platform, so the pre-LLM session marker must suppress them too.
    ctx.hooks["pre_tool_call"](
        session_id="pet-chat", tool_name="terminal", args={"command": "PRIVATE"}
    )
    ctx.hooks["on_session_end"](
        session_id="pet-chat", platform="api_server", completed=True,
        failed=False, interrupted=False,
    )

    assert not path.exists()
    assert bridge._API_SERVER_SESSIONS == set()


def test_human_approval_events_use_correlated_ids_without_prompt_content(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    ctx = HookRegistry()
    bridge.register(ctx)

    ctx.hooks["on_human_input_request"](
        kind="approval", request_id="req-1", session_id="session-1",
        platform="desktop", prompt="PRIVATE COMMAND",
    )
    ctx.hooks["on_human_input_resolved"](
        kind="approval", request_id="req-1", session_id="session-1",
        platform="desktop", outcome="once",
    )
    # Other blocking human input is not an approval.
    ctx.hooks["on_human_input_request"](
        kind="clarify", request_id="req-2", session_id="session-1",
        platform="desktop", prompt="PRIVATE QUESTION",
    )

    records = _read(path)
    assert [(row["event"], row["requestId"], row["sessionId"]) for row in records] == [
        ("approval/request", "req-1", "session-1"),
        ("approval/resolved", "req-1", "session-1"),
    ]
    serialized = path.read_text(encoding="utf-8")
    assert "PRIVATE" not in serialized
    assert all(set(row) <= {"ts", "agent", "inst", "event", "requestId", "sessionId"} for row in records)


def test_event_write_failure_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(tmp_path / "not-a-dir" / "events.jsonl"))
    (tmp_path / "not-a-dir").write_text("occupied", encoding="utf-8")
    _reset()
    bridge._on_pre_llm_call(session_id="s")
    assert bridge._LAST_EMITTED is None


def test_failed_tool_does_not_leave_session_permanently_working(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_tool_call(session_id="recovering", tool_name="terminal")
    bridge._on_post_tool_call(session_id="recovering", status="error")
    assert _read(path)[-1]["state"] == "error"
    # Another session finishing must not resurrect the failed tool as still running.
    bridge._on_session_end(session_id="other", completed=True)
    assert _read(path)[-1]["state"] == "thinking"


def test_missing_tool_name_never_falls_back_to_private_arguments(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_tool_call(session_id="s", args={"tool": "PRIVATE ARGUMENT"})
    assert "tool" not in _read(path)[-1]
    assert "private" not in path.read_text(encoding="utf-8").lower()


def test_parallel_tools_stay_working_until_last_call_returns(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_tool_call(session_id="s", tool_call_id="a", tool_name="terminal")
    bridge._on_pre_tool_call(session_id="s", tool_call_id="b", tool_name="read_file")
    bridge._on_post_tool_call(session_id="s", tool_call_id="a", status="success")
    assert _read(path)[-1]["state"] == "working"
    bridge._on_post_tool_call(session_id="s", tool_call_id="b", status="success")
    assert _read(path)[-1]["state"] == "thinking"
    assert bridge._ACTIVE_TOOLS == {}


def test_api_server_descendants_are_suppressed_but_cli_children_are_not(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="api", platform="api_server")
    bridge._on_pre_llm_call(session_id="child", platform="subagent", parent_session_id="api")
    bridge._on_pre_llm_call(session_id="grandchild", platform="subagent", parent_session_id="child")
    bridge._on_pre_tool_call(session_id="grandchild", tool_name="terminal")
    bridge._on_post_tool_call(session_id="grandchild", status="error")
    for sid in ("grandchild", "child", "api"):
        bridge._on_session_end(session_id=sid, completed=True)
    assert not path.exists()
    assert bridge._API_SERVER_SESSIONS == set()
    bridge._on_pre_llm_call(session_id="normal", platform="subagent", parent_session_id="cli")
    assert _read(path)[-1]["state"] == "thinking"


def test_independent_writers_have_distinct_owners_including_approvals(tmp_path, monkeypatch):
    from pet.agent_link import record_owner
    from pet.agent_link.dsh_state import fold_writer_states

    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    other = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(other)
    bridge._on_pre_tool_call(session_id="a", tool_name="terminal")
    bridge._on_human_input_request(kind="approval", request_id="r", session_id="a")
    other._on_session_end(session_id="b", completed=True)
    rows = _read(path)
    assert record_owner(rows[0]) == record_owner(rows[1])
    assert record_owner(rows[0]) is not None
    assert record_owner(rows[0]) != record_owner(rows[2])
    states = {}
    for index, row in enumerate(rows):
        if "state" in row:
            states[record_owner(row)] = (row["state"], index)
    assert fold_writer_states(states.values()) == "working"


def test_state_snapshot_and_append_share_one_critical_section(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    append = bridge._append_event
    locked = []

    def checked_append(*args):
        def probe():
            acquired = bridge._LOCK.acquire(blocking=False)
            locked.append(not acquired)
            if acquired:
                bridge._LOCK.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
        append(*args)

    monkeypatch.setattr(bridge, "_append_event", checked_append)
    bridge._on_pre_llm_call(session_id="s")
    bridge._on_pre_tool_call(session_id="s", tool_call_id="a", tool_name="terminal")
    bridge._on_post_tool_call(session_id="s", tool_call_id="a", status="success")
    bridge._on_session_end(session_id="s", completed=True)
    assert locked == [True, True, True, True]


def test_background_fork_without_lifecycle_cannot_resurrect_finished_turn(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="shared", turn_id="foreground", platform="desktop")
    bridge._on_session_end(session_id="shared", turn_id="foreground", completed=True)
    before = path.read_bytes()
    # Real background-review forks share the session ID, but skip pre_llm/end.
    bridge._on_pre_tool_call(session_id="shared", turn_id="background", tool_call_id="m", tool_name="memory")
    bridge._on_post_tool_call(session_id="shared", turn_id="background", tool_call_id="m", status="success")
    assert path.read_bytes() == before
    assert bridge._SESSION_STATES == {}
    bridge._on_pre_llm_call(session_id="shared", turn_id="next", platform="desktop")
    bridge._on_session_end(session_id="shared", turn_id="next", completed=True)
    assert [r["state"] for r in _read(path)] == ["thinking", "idle", "thinking", "idle"]


def test_background_fork_is_ignored_even_while_parent_is_active(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="shared", turn_id="foreground")
    before = path.read_bytes()
    bridge._on_pre_tool_call(session_id="shared", turn_id="background", tool_call_id="b", tool_name="read_file")
    bridge._on_post_tool_call(session_id="shared", turn_id="background", tool_call_id="b", status="error")
    assert path.read_bytes() == before
    bridge._on_pre_tool_call(session_id="shared", turn_id="foreground", tool_call_id="a", tool_name="terminal")
    assert _read(path)[-1]["state"] == "working"
    bridge._on_post_tool_call(session_id="shared", turn_id="foreground", tool_call_id="a", status="success")
    bridge._on_session_end(session_id="shared", turn_id="foreground", completed=True)
    assert _read(path)[-1]["state"] == "idle"


def test_compression_session_rotation_preserves_turn_lifecycle(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="before-compression", turn_id="live")
    bridge._on_pre_tool_call(session_id="after-compression", turn_id="live", tool_call_id="a", tool_name="terminal")
    assert _read(path)[-1]["state"] == "working"
    bridge._on_post_tool_call(session_id="after-compression", turn_id="live", tool_call_id="a", status="success")
    bridge._on_session_end(session_id="after-compression", turn_id="live", completed=True)
    assert _read(path)[-1]["state"] == "idle"
    assert bridge._SESSION_STATES == {}
    assert bridge._FOREGROUND_TURNS == set()
    assert bridge._TURN_SESSION_IDS == {}


def test_session_shutdown_without_turn_id_retires_registered_turns(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("DSH_PET_HERMES_EVENTS", str(path))
    _reset()
    bridge._on_pre_llm_call(session_id="s", turn_id="live")
    bridge._on_session_end(session_id="s", interrupted=True)
    assert bridge._SESSION_STATES == {}
    assert bridge._FOREGROUND_TURNS == set()
    assert bridge._TURN_SESSION_IDS == {}
