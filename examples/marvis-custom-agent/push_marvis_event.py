#!/usr/bin/env python3
"""Append a Marvis agent event to the pet's agent-events channel.

Unified protocol (see docs/AGENT_LINK_PROTOCOL.md §2):
  - one JSON line per event, UTF-8, append-only
  - event file: <config.dir>/agent-events/<agent>.jsonl
  - write only metadata (state/event/tool), never payload content
"""
import argparse
import json
import os
import sys
import time


def default_path() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(
        appdata,
        "dsh-pet-standalone-webm-chat",
        "agent-events",
        "marvis.jsonl",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append a Marvis agent event (agent-link unified protocol)."
    )
    parser.add_argument(
        "--state",
        choices=["idle", "thinking", "working", "attention", "error", "sleeping"],
        help="explicit six-state word (highest priority)",
    )
    parser.add_argument("--event", help="event name (UserPromptSubmit/PreToolUse/PostToolUse/Stop/...)")
    parser.add_argument("--tool", help="optional tool name for process bubbles")
    parser.add_argument("--agent", default="marvis")
    parser.add_argument(
        "--path",
        default=None,
        help="event file path (default: %APPDATA%\\dsh-pet-standalone-webm-chat\\agent-events\\marvis.jsonl)",
    )
    args = parser.parse_args()

    if not args.state and not args.event:
        parser.error("need --state or --event")

    record = {"ts": time.time(), "agent": args.agent}
    if args.state:
        record["state"] = args.state
    if args.event:
        record["event"] = args.event
    if args.tool:
        record["tool"] = args.tool

    path = args.path or default_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"ok -> {path}")


if __name__ == "__main__":
    sys.exit(main())
