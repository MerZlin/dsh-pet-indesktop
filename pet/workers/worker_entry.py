# -*- coding: utf-8 -*-
"""Explicit allowlist for built-in worker entry points."""

from __future__ import annotations

import sys
from collections.abc import Sequence

_WORKERS = {"agent-link-events"}


def main(worker_id: str | None = None, argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    selected = worker_id or (args[0] if args else "")
    if selected not in _WORKERS:
        sys.stderr.write(f"unknown worker: {selected or '<missing>'}\n")
        return 2
    if selected == "agent-link-events":
        from .agent_link_worker import run_agent_link_worker

        return run_agent_link_worker()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
