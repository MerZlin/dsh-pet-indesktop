"""Claude Code hooks 的显式安装/卸载。

只有设置页用户点击并确认后才会调用；默认不写任何 Agent 配置。
写入前备份 ``settings.json``，只增删带 ``x-dsh-pet`` 标记（或本脚本文件名）的条目，
解析失败时中止且不覆盖原文件。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionStart",
    "UserPromptSubmit",
)
HOOK_MARKER = "x-dsh-pet"
SCRIPT_STEM = "claude_event_hook"


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _ensure_script(events_file: Path, platform: str) -> tuple[Path, str]:
    events_file.parent.mkdir(parents=True, exist_ok=True)
    if platform == "win32":
        script = events_file.parent / f"{SCRIPT_STEM}.ps1"
        script.write_text(
            "param([string]$EventName = 'unknown')\n"
            "$tool = ''\n"
            "if ([Console]::IsInputRedirected) {\n"
            "  try {\n"
            "    $raw = [Console]::In.ReadToEnd()\n"
            "    if ($raw) { $j = $raw | ConvertFrom-Json -ErrorAction Stop; if ($j.tool_name) { $tool = [string]$j.tool_name } }\n"
            "  } catch {}\n"
            "}\n"
            f"$file = Join-Path $PSScriptRoot '{events_file.name}'\n"
            "$rec = [ordered]@{ ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0; agent = 'claude'; event = $EventName }\n"
            "if ($tool) { $rec['tool'] = $tool }\n"
            "Add-Content -Path $file -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8\n",
            encoding="utf-8",
        )
        return script, 'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" {event}'
    script = events_file.parent / f"{SCRIPT_STEM}.py"
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "event = sys.argv[1] if len(sys.argv) > 1 else 'unknown'\n"
        "tool = ''\n"
        "try:\n"
        "    if not sys.stdin.isatty():\n"
        "        raw = sys.stdin.read()\n"
        "        if raw.strip():\n"
        "            tool = str(json.loads(raw).get('tool_name') or '')\n"
        "except Exception:\n"
        "    pass\n"
        f"out = Path(__file__).with_name('{events_file.name}')\n"
        "rec = {'ts': time.time(), 'agent': 'claude', 'event': event}\n"
        "if tool:\n"
        "    rec['tool'] = tool\n"
        "with out.open('a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(rec, ensure_ascii=False) + '\\n')\n",
        encoding="utf-8",
    )
    exe = sys.executable if not getattr(sys, "frozen", False) else "python3"
    return script, f'"{exe}" "{{script}}" {{event}}'


def _build_command(template: str, script: Path, event: str) -> str:
    return template.replace("{script}", str(script)).replace("{event}", event)


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get(HOOK_MARKER) is True:
        return True
    for hook in entry.get("hooks") or []:
        command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
        if f"{SCRIPT_STEM}.ps1" in command or f"{SCRIPT_STEM}.py" in command:
            return True
    return False


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("settings.json 根节点不是对象")
    return data


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    backup = path.with_name(path.name + ".dsh-pet-backup")
    backup.write_bytes(path.read_bytes())


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def installed(*, path: Path | None = None) -> bool:
    path = Path(path) if path is not None else settings_path()
    try:
        data = _read(path)
    except ValueError:
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(
        _is_ours(entry)
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
    )


def install(
    events_file: Path | str,
    *,
    path: Path | None = None,
    platform: str | None = None,
) -> tuple[bool, str]:
    settings = Path(path) if path is not None else settings_path()
    events = Path(events_file)
    try:
        data = _read(settings)
    except ValueError as exc:
        return False, f"settings.json 解析失败，未改动原文件：{exc}"
    try:
        script, template = _ensure_script(events, platform or sys.platform)
    except OSError as exc:
        return False, f"写入 hook 脚本失败：{exc}"
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    for event in HOOK_EVENTS:
        existing = hooks.get(event)
        kept = [entry for entry in existing if not _is_ours(entry)] if isinstance(existing, list) else []
        kept.append(
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": _build_command(template, script, event),
                    }
                ],
                HOOK_MARKER: True,
            }
        )
        hooks[event] = kept
    try:
        _write(settings, data)
    except OSError as exc:
        return False, f"写入 settings.json 失败：{exc}"
    return True, f"已写入 {settings}"


def uninstall(*, path: Path | None = None) -> tuple[bool, str]:
    settings = Path(path) if path is not None else settings_path()
    try:
        data = _read(settings)
    except ValueError as exc:
        return False, f"settings.json 解析失败，未改动原文件：{exc}"
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return True, "没有可移除的 hooks"
    changed = False
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [entry for entry in entries if not _is_ours(entry)]
        if len(kept) == len(entries):
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not changed:
        return True, "没有本程序注入的 hooks"
    try:
        _write(settings, data)
    except OSError as exc:
        return False, f"写入 settings.json 失败：{exc}"
    return True, f"已从 {settings} 移除本程序的 hooks"
