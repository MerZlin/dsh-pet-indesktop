"""Minimal public v1 child transport. No Qt, application or secret-store imports.

The handler receives (operation, arguments, cancel_event) on one worker thread.
It must cooperate with cancellation; stdout is reserved exclusively for JSONL.
"""
from __future__ import annotations

import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from pet.workers.protocol import MAX_MESSAGE_BYTES, build_message, decode_message, encode_message


def _probe(worker_id):
    # Probe never imports the lease/OS services and cannot execute author tasks.
    try:
        import _dsh_probe_native
        if _dsh_probe_native.sandbox_enforced() is not True:
            return 77
    except (ImportError, OSError, AttributeError):
        return 77
    output = sys.stdout.buffer
    output.write(encode_message(build_message(worker_id, "hello", {"probe": True, "capabilities": []})))
    output.flush()
    try:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES:
            return 78
        message = decode_message(raw)
        return 0 if message.worker_id == worker_id and message.type == "shutdown" and not message.payload else 78
    except (OSError, ValueError):
        return 78


def serve(worker_id, handler, *, capabilities=()):
    from pet.frozen_runtime_paths import activate_frozen_dependency_path
    activate_frozen_dependency_path()
    if sys.argv[1:]:
        return _probe(worker_id) if sys.argv[1:] == ["--feature-package-probe"] else 78
    from pet.workers.lease_bootstrap import claim_worker_lease_from_environment, worker_lease_claimed
    claimed = claim_worker_lease_from_environment()
    if getattr(sys, "frozen", False) and not claimed:
        return 77
    incoming = queue.Queue(maxsize=256)
    pending = {}
    generation = 0

    def write(kind, payload, rid=None):
        sys.stdout.buffer.write(encode_message(build_message(worker_id, kind, payload, request_id=rid)))
        sys.stdout.buffer.flush()

    def read():
        try:
            while True:
                raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 2)
                if not raw:
                    break
                incoming.put(("message", decode_message(raw)))
        finally:
            incoming.put(("eof", None))

    reader = threading.Thread(target=read, name="mod-worker-input", daemon=True)
    reader.start()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mod-worker-task")
    write("hello", {"capabilities": list(capabilities), "lease_claimed": worker_lease_claimed()})
    try:
        while True:
            try:
                kind, value = incoming.get(timeout=1)
            except queue.Empty:
                write("heartbeat", {"generation": generation})
                continue
            if kind == "eof":
                break
            if kind == "result":
                rid, op, payload = value
                cancel = pending.pop(rid, None)
                if cancel is not None and not cancel.is_set():
                    write("response", {**payload, "operation": op}, rid)
                continue
            message = value
            if message.worker_id != worker_id:
                continue
            if message.type == "shutdown":
                break
            if message.type == "config_push":
                generation = int(message.payload.get("generation", generation))
                write("ready", {"generation": generation})
            elif message.type == "request":
                op = message.payload.get("operation", "")
                args = message.payload.get("arguments", {})
                if op == "cancel":
                    cancel = pending.get(args.get("request_id"))
                    if cancel is not None:
                        cancel.set()
                    continue
                rid = message.request_id
                if len(pending) >= 64:
                    write("response", {"operation": op, "error": "worker_busy"}, rid)
                    continue
                event = pending[rid] = threading.Event()
                def execute(rid=rid, op=op, args=args, event=event):
                    try:
                        payload = dict(handler(op, args, event)) if not event.is_set() else {}
                    except Exception:
                        payload = {"error": "handler_failed"}  # no raw exceptions or credentials
                    incoming.put(("result", (rid, op, payload)))
                executor.submit(execute)
    finally:
        for event in pending.values():
            event.set()
        executor.shutdown(wait=True, cancel_futures=True)
        # The parent closes stdin after shutdown; do not leave a live reader
        # holding Python's buffered-input lock during interpreter finalization.
        reader.join(timeout=2)
    return 0
