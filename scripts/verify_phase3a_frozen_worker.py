"""Verify the JSONL handshake and graceful shutdown of a frozen Worker.

Usage:
    python scripts/verify_phase3a_frozen_worker.py path/to/dsh-pet-standalone.exe

The script does not build the application and does not modify PyInstaller
configuration.  It only exercises the existing ``--worker`` entrypoint.
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet.workers.protocol import (  # noqa: E402  # project root is inserted above for direct script execution
    PROTOCOL,
    WorkerMessage,
    build_message,
    decode_message,
    encode_message,
)


def _reader(stream, output: queue.Queue[bytes | None]) -> None:
    try:
        for line in iter(stream.readline, b""):
            output.put(line)
    finally:
        output.put(None)


def _read_message(output: queue.Queue[bytes | None], timeout: float) -> WorkerMessage:
    try:
        line = output.get(timeout=timeout)
    except queue.Empty as exc:
        raise RuntimeError(f"timed out waiting for Worker message ({timeout:.1f}s)") from exc
    if line is None:
        raise RuntimeError("Worker stdout closed before the expected message")
    return decode_message(line)


def _send(process: subprocess.Popen[bytes], message_type: str, payload: dict | None = None) -> None:
    if process.stdin is None:
        raise RuntimeError("Worker stdin is unavailable")
    data = encode_message(build_message("agent-link-events", message_type, payload or {}))
    process.stdin.write(data)
    process.stdin.flush()


def _expect(message: WorkerMessage, message_type: str) -> None:
    if message.protocol != PROTOCOL or message.worker_id != "agent-link-events" or message.type != message_type:
        raise RuntimeError(
            f"unexpected Worker message: protocol={message.protocol!r}, worker_id={message.worker_id!r}, type={message.type!r}; expected {message_type!r}"
        )


def verify(executable: Path, *, timeout: float = 10.0) -> int:
    process = subprocess.Popen(
        [str(executable), "--worker", "agent-link-events"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output: queue.Queue[bytes | None] = queue.Queue()
    reader = threading.Thread(target=_reader, args=(process.stdout, output), daemon=True)
    reader.start()
    try:
        hello = _read_message(output, timeout)
        _expect(hello, "hello")
        print(f"hello worker_id={hello.worker_id} payload={hello.payload}")

        _send(
            process,
            "config_push",
            {"generation": 1, "sources": [], "poll_interval": 0.25, "paused": False},
        )
        ready = _read_message(output, timeout)
        _expect(ready, "ready")
        if int(ready.payload.get("generation", -1)) != 1:
            raise RuntimeError(f"Worker ready generation mismatch: {ready.payload}")
        print(f"ready generation={ready.payload.get('generation')} source_count={ready.payload.get('source_count')}")

        _send(process, "shutdown", {"generation": 1})
        print("shutdown_sent")
        if process.stdin is not None:
            process.stdin.close()
            process.stdin = None
        return_code = process.wait(timeout=timeout)
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr is not None else ""
        if return_code != 0:
            raise RuntimeError(f"Worker returned {return_code}; stderr={stderr[-4000:]}")
        print(f"FROZEN_WORKER_SMOKE_OK returncode={return_code}")
        return 0
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        raise
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, help="frozen desktop-pet executable")
    args = parser.parse_args(argv)
    executable = args.executable.expanduser().resolve()
    if not executable.is_file():
        parser.error(f"frozen executable does not exist: {executable}")
    try:
        return verify(executable)
    except Exception as exc:
        print(f"FROZEN_WORKER_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
