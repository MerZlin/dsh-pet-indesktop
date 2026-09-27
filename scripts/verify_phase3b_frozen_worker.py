"""Bounded source/frozen proactive-screen smoke; no screenshot or remote request.

python scripts/verify_phase3b_frozen_worker.py <frozen-exe> [--idle-seconds 1]
python scripts/verify_phase3b_frozen_worker.py --source

Only foreground metadata is queried. Window titles and credentials are never
printed. The optional bounded idle sample uses the existing psutil dependency.
This is not a soak test and does not validate a paid vision provider.
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.workers.protocol import (  # noqa: E402 -- standalone script needs project root
    MAX_MESSAGE_BYTES,
    WorkerMessage,
    build_message,
    decode_message,
    encode_message,
)

WORKER_ID = "proactive-screen"
CAPABILITIES = {"foreground.read", "screenshot.capture", "vision.request", "logging.write"}


def verify_command(command: list[str], *, timeout: float = 15.0, idle_seconds: float = 1.0) -> dict:
    if not 0 <= idle_seconds <= 5:
        raise ValueError("idle sample must be between 0 and 5 seconds")
    started = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT)
    output: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
    overflow = threading.Event()

    def read_output():
        try:
            while True:
                line = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if len(line) > MAX_MESSAGE_BYTES:
                    overflow.set()
                    return
                try:
                    output.put_nowait(line or None)
                except queue.Full:
                    overflow.set()
                    return
                if not line:
                    return
        except (OSError, ValueError):
            overflow.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def send(kind, payload, request_id=None):
        process.stdin.write(encode_message(build_message(WORKER_ID, kind, payload, request_id=request_id)))
        process.stdin.flush()

    def expect(kind: str, request_id: str | None = None) -> WorkerMessage:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if overflow.is_set():
                raise RuntimeError("Worker stdout exceeded smoke bounds")
            try:
                raw = output.get(timeout=max(0.001, deadline - time.perf_counter()))
            except queue.Empty as exc:
                raise RuntimeError(f"timeout waiting for {kind}") from exc
            if raw is None:
                raise RuntimeError(f"Worker exited before {kind}")
            message = decode_message(raw)
            if message.worker_id != WORKER_ID:
                raise RuntimeError("unexpected worker ID")
            if message.type == "heartbeat":
                continue
            if message.type != kind or (request_id and message.request_id != request_id):
                raise RuntimeError(f"unexpected message type/id while waiting for {kind}")
            return message
        raise RuntimeError(f"timeout waiting for {kind}")

    try:
        hello = expect("hello")
        if not CAPABILITIES.issubset(set(hello.payload.get("capabilities", []))):
            raise RuntimeError("missing proactive capabilities")
        print("hello capabilities=foreground.read,screenshot.capture,vision.request,logging.write")
        send("config_push", {"generation": 1, "max_edge": 1024, "jpeg_quality": 80, "frame_ttl": 20})
        ready = expect("ready")
        if ready.payload.get("generation") != 1:
            raise RuntimeError("ready generation mismatch")
        print("ready generation=1")
        metrics = {"startup_ms": round((time.perf_counter() - started) * 1000, 2)}
        observed = time.perf_counter()
        send("request", {"operation": "observe_foreground", "generation": 1, "arguments": {}}, "smoke-observe")
        response = expect("response", "smoke-observe")
        payload = response.payload
        if payload.get("operation") != "observe_foreground" or payload.get("generation") != 1 or payload.get("status") != "ok":
            raise RuntimeError("foreground observation failed or response mismatched")
        if not isinstance(payload.get("result"), dict) or "window" not in payload["result"]:
            raise RuntimeError("foreground result has no structured window field")
        metrics["observe_ms"] = round((time.perf_counter() - observed) * 1000, 2)
        print("observe_foreground status=ok (metadata withheld)")
        if idle_seconds:
            import psutil

            sampled = psutil.Process(process.pid)
            metrics["worker_rss_bytes"] = sampled.memory_info().rss
            before = sampled.cpu_times()
            idle_started = time.perf_counter()
            # This is a fixed-duration performance SAMPLE, not test synchronization.
            # EOF within the window fails immediately instead of sleeping through it.
            try:
                raw = output.get(timeout=idle_seconds)
                if raw is None or decode_message(raw).type != "heartbeat":
                    raise RuntimeError("Worker stopped or emitted an unexpected idle message")
            except queue.Empty:
                pass
            after = sampled.cpu_times()
            metrics["idle_sample_seconds"] = round(time.perf_counter() - idle_started, 3)
            metrics["idle_cpu_seconds"] = round(after.user + after.system - before.user - before.system, 6)
            metrics["worker_threads"] = sampled.num_threads()
        stopping = time.perf_counter()
        send("shutdown", {"generation": 1})
        print("shutdown_sent")
        process.stdin.close()
        code = process.wait(timeout=timeout)
        if code:
            raise RuntimeError(f"Worker returned {code}")
        metrics["shutdown_ms"] = round((time.perf_counter() - stopping) * 1000, 2)
        print("metrics " + json.dumps(metrics, sort_keys=True))
        print("PROACTIVE_WORKER_SMOKE_OK returncode=0")
        return metrics
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        reader.join(timeout=2)
        if process.stdout:
            process.stdout.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, nargs="?")
    parser.add_argument("--source", action="store_true", help="test the production python -m pet entry instead of a frozen build")
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.source == bool(args.executable):
        parser.error("select either a frozen executable or --source")
    if args.source:
        command = [sys.executable, "-m", "pet", "--worker", WORKER_ID]
    else:
        executable = args.executable.expanduser().resolve()
        if not executable.is_file():
            parser.error(f"frozen executable does not exist: {executable}")
        command = [str(executable), "--worker", WORKER_ID]
    try:
        verify_command(command, idle_seconds=args.idle_seconds)
        return 0
    except Exception as exc:
        print(f"PROACTIVE_WORKER_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
