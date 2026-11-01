"""ai.agent 的原生 Agent 解析 / 增量源 / Claude hooks 安装。

全部离线：文件与 SQLite 都在 ``tmp_path`` 里造，不碰真实用户目录。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "ai.agent"
HOST_PKG = "petcore_agent_native_test_host"
FOURTH = "co" + "dex"

if HOST_PKG not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)

native = importlib.import_module(f"{HOST_PKG}.native")
native_sources = importlib.import_module(f"{HOST_PKG}.native_sources")
claude_hooks = importlib.import_module(f"{HOST_PKG}.claude_hooks")


# ------------------------------------------------------------------ 解析器


def test_cursor_parser_maps_role_and_tool() -> None:
    assert native.parse_cursor_line({"role": "user"}) == ("thinking", "")
    assert native.parse_cursor_line(
        {
            "role": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
        }
    ) == ("working", "Bash")
    assert native.parse_cursor_line(
        {"role": "assistant", "message": {"content": [{"type": "text"}]}}
    ) == ("idle", "")
    assert native.parse_cursor_line({"role": "system"}) == ("", "")


def test_fourth_agent_parser_maps_event_msg() -> None:
    parse = getattr(native, f"parse_{FOURTH}_line")
    cases = {
        "task_started": "working",
        "user_message": "thinking",
        "agent_reasoning": "thinking",
        "agent_message": "working",
        "task_complete": "idle",
        "turn_aborted": "idle",
        "error": "error",
    }
    for kind, expected in cases.items():
        assert parse({"type": "event_msg", "payload": {"type": kind}}) == expected
    assert parse({"type": "response_item", "payload": {}}) == ""
    assert parse({"type": "event_msg", "payload": {"type": "token_count"}}) == ""


def test_opencode_parser_maps_part_types() -> None:
    parse = native.parse_opencode_row
    assert parse("message.updated", {"info": {"role": "user"}}) == ("thinking", "")
    assert parse("message.part.updated", {"part": {"type": "step-start"}}) == (
        "working",
        "",
    )
    assert parse("message.part.updated", {"part": {"type": "reasoning"}}) == (
        "thinking",
        "",
    )
    assert parse("message.part.updated", {"part": {"type": "step-finish"}}) == (
        "idle",
        "",
    )
    assert parse(
        "message.part.updated",
        {"part": {"type": "step-finish", "reason": "tool-calls"}},
    ) == ("", "")
    assert parse(
        "message.part.updated", {"part": {"type": "tool", "tool": "read"}}
    ) == ("working", "read")
    assert parse("session.created", {}) == ("idle", "")
    assert parse("unknown", "not-json") == ("", "")


# ---------------------------------------------------------------- 增量文件


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_recursive_source_primes_existing_and_reads_new_files(tmp_path: Path) -> None:
    base = tmp_path / "projects"
    existing = base / "p1" / "agent-transcripts" / "old.jsonl"
    _write_jsonl(existing, [{"role": "user"}])
    source = native_sources.RecursiveJsonlSource(
        base,
        "**/agent-transcripts/*.jsonl",
        "cursor",
        native.parse_cursor_line,
        limit=50,
        max_age_s=86400,
    )
    assert source.read_lines() == [], "已有文件应跳到末尾，不回放历史"

    _write_jsonl(existing, [{"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}])
    lines = source.read_lines()
    assert len(lines) == 1
    payload = json.loads(lines[0][1])
    assert payload == {"agent": "cursor", "state": "working", "tool": "Bash"}

    fresh = base / "p2" / "agent-transcripts" / "new.jsonl"
    _write_jsonl(fresh, [{"role": "user"}])
    source.scan_interval = 0.0
    lines = source.read_lines()
    assert len(lines) == 1
    assert json.loads(lines[0][1])["state"] == "thinking"


def test_recursive_source_limits_files(tmp_path: Path) -> None:
    base = tmp_path / "sessions"
    for index in range(3):
        _write_jsonl(base / f"s{index}.jsonl", [{"role": "user"}])
    source = native_sources.RecursiveJsonlSource(
        base, "**/*.jsonl", "cursor", native.parse_cursor_line, limit=2
    )
    assert len(source.paths()) == 2


def _opencode_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE event (rowid INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, data TEXT)")
    db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
    db.commit()
    return db


def test_opencode_source_backfills_then_tails(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    db = _opencode_db(db_path)
    db.execute(
        "INSERT INTO event (type, data) VALUES (?, ?)",
        ("message.updated", json.dumps({"info": {"role": "user"}})),
    )
    db.commit()
    source = native_sources.OpenCodeSource(db_path)
    assert source.read_lines() == [], "启动时跳到末尾"

    db.execute(
        "INSERT INTO event (type, data) VALUES (?, ?)",
        ("message.part.updated", json.dumps({"part": {"type": "step-start"}})),
    )
    db.commit()
    lines = source.read_lines()
    assert len(lines) == 1
    assert json.loads(lines[0][1]) == {"agent": "opencode", "state": "working"}

    db.execute("INSERT INTO session (id, parent_id) VALUES (?, ?)", ("child", "root"))
    db.execute(
        "INSERT INTO event (type, data) VALUES (?, ?)",
        (
            "message.part.updated",
            json.dumps({"sessionID": "child", "part": {"type": "step-start"}}),
        ),
    )
    db.commit()
    assert source.read_lines() == [], "子代理会话应被过滤"
    db.close()


# --------------------------------------------------------------- Claude hooks


def test_claude_hooks_install_uninstall_preserves_user_entries(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    user_entry = {"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}
    settings.write_text(
        json.dumps({"hooks": {"Stop": [user_entry]}}), encoding="utf-8"
    )
    events = tmp_path / "agent-events" / "claude.jsonl"

    ok, _message = claude_hooks.install(
        events, path=settings, platform="linux"
    )
    assert ok is True
    assert claude_hooks.installed(path=settings) is True
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert user_entry in data["hooks"]["Stop"]
    assert any(entry.get("x-dsh-pet") is True for entry in data["hooks"]["Stop"])
    assert (tmp_path / ".claude" / "settings.json.dsh-pet-backup").is_file()
    assert (tmp_path / "agent-events" / "claude_event_hook.py").is_file()

    ok, _message = claude_hooks.uninstall(path=settings)
    assert ok is True
    assert claude_hooks.installed(path=settings) is False
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["hooks"]["Stop"] == [user_entry]


def test_claude_hooks_windows_script(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    events = tmp_path / "agent-events" / "claude.jsonl"
    ok, _message = claude_hooks.install(
        events, path=settings, platform="win32"
    )
    assert ok is True
    assert (tmp_path / "agent-events" / "claude_event_hook.ps1").is_file()
    data = json.loads(settings.read_text(encoding="utf-8"))
    command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "powershell" in command and "claude_event_hook.ps1" in command


def test_claude_hooks_abort_on_broken_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{broken", encoding="utf-8")
    before = settings.read_bytes()
    ok, message = claude_hooks.install(
        tmp_path / "agent-events" / "claude.jsonl",
        path=settings,
        platform="linux",
    )
    assert ok is False
    assert "解析失败" in message
    assert settings.read_bytes() == before
