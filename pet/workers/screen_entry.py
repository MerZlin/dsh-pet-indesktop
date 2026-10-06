"""Standalone screen entry: sandbox-only probe or lease-bound normal execution.

The in-Core Worker entry keeps its existing dispatch. This entry is specifically
for independent installed executable packages: missing handoff is fail-closed.
"""

from __future__ import annotations

import sys


def sandbox_enforced() -> bool:
    try:
        import _dsh_probe_native

        return _dsh_probe_native.sandbox_enforced() is True
    except (ImportError, OSError, AttributeError):
        return False


def _probe(source, output) -> int:
    if not sandbox_enforced():
        return 77
    from .protocol import MAX_MESSAGE_BYTES, WorkerProtocolError, build_message, decode_message, encode_message

    output.write(encode_message(build_message("proactive-screen", "hello", {"probe": True, "capabilities": []})))
    output.flush()
    try:
        raw = source.readline(MAX_MESSAGE_BYTES + 1)
        if not raw or not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES:
            return 78
        message = decode_message(raw)
        if message.worker_id != "proactive-screen" or message.type != "shutdown" or message.payload:
            return 78
        return 0
    except (WorkerProtocolError, OSError, ValueError):
        return 78


def run_screen_worker_entry(argv=None, *, source=None, output=None, run_runtime=None) -> int:
    from pet.frozen_runtime_paths import activate_frozen_dependency_path

    activate_frozen_dependency_path()
    args = sys.argv[1:] if argv is None else argv
    if args:
        if args != ["--feature-package-probe"]:
            return 78
        return _probe(source or sys.stdin.buffer, output or sys.stdout.buffer)
    # This import (including OS lease machinery) is forbidden in sandbox mode.
    from .lease_bootstrap import claim_worker_lease_from_environment

    try:
        if not claim_worker_lease_from_environment():
            return 77
    except Exception:
        return 77
    if run_runtime is not None:
        return run_runtime()
    from features.screen_understanding.worker.runtime import run_proactive_screen_worker

    return run_proactive_screen_worker()
