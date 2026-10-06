"""Explicit, idempotent hook setup preserving all existing user handlers."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .hook import STATES


def groups(events):
    args = [sys.executable, str(Path(__file__).with_name("hook.py")), "--events", str(Path(events).resolve())]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    return {event: [{"hooks": [{"type": "command", "command": command, "timeout": 2}]}] for event in STATES}


def merge(existing, additions):
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Existing hooks configuration is malformed.")
    for event, new_groups in additions.items():
        previous = hooks.setdefault(event, [])
        if not isinstance(previous, list):
            raise ValueError("Existing hook event is malformed.")
        commands = {handler.get("command") for group in previous for handler in group.get("hooks", [])}
        for group in new_groups:
            if group["hooks"][0]["command"] not in commands:
                previous.append(group)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="Codex edition profile directory / events")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--install", action="store_true", help="Merge hooks and create a backup; otherwise print the additions")
    args = parser.parse_args(argv)
    additions = groups(args.events)
    if not args.install:
        print(json.dumps({"hooks": additions}, ensure_ascii=False, indent=2))
        return 0
    path = args.codex_home / "hooks.json"
    existing = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    result = merge(existing, additions)
    if result == existing:
        print("Codex companion hooks already configured.")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(f"hooks-before-dsh-{time.time_ns()}.json"))
    temporary = path.with_suffix(".dsh.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    print("Hooks saved. Review and trust the added commands in Codex Hooks settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
