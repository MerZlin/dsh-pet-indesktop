"""Explicit Windows standalone Worker evidence (not a GUI or screenshot test).

Verify embedded PYZ, real loaded DLL paths and bounded JSONL lifecycle, from a
new empty working directory without Python/Qt/Core search injection. No keys,
screenshots, model requests, foreground titles or network access are used.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.workers.launch import isolated_worker_environment  # noqa: E402 -- developer script bootstrap only; never passed to child
from pet.workers.protocol import MAX_MESSAGE_BYTES, WorkerMessage, build_message, decode_message, encode_message  # noqa: E402
from scripts.build_screen_worker import inspect_archive  # noqa: E402

WORKER_ID = "proactive-screen"
CAPABILITIES = {"foreground.read", "screenshot.capture", "vision.request", "logging.write"}


def check_loaded_modules(paths: Iterable[Path], *, bundle: Path, system_roots: Sequence[Path], forbidden_roots: Sequence[Path]) -> list[str]:
    """Classify actual native modules, not PATH or speculative DLL resolution."""
    bundle = bundle.resolve()
    allowed = (bundle, *(p.resolve() for p in system_roots))
    forbidden = tuple(p.resolve() for p in forbidden_roots)
    result = sorted({str(p.resolve()) for p in paths})
    own_python = False
    for raw in result:
        path = Path(raw)
        if any(path.is_relative_to(root) for root in forbidden):
            raise ValueError(f"forbidden Core/source module: {path}")
        if not any(path.is_relative_to(root) for root in allowed):
            raise ValueError(f"module outside Worker/system roots: {path}")
        if path.name.lower().startswith(("qt5", "qt6", "shiboken", "pyside")) or any(part.lower() in {"pyside6", "pyqt6"} for part in path.parts):
            raise ValueError(f"Qt module loaded by independent Worker: {path}")
        if path.is_relative_to(bundle) and re.fullmatch(r"python3[0-9]*[dt]?\.dll", path.name, re.IGNORECASE):
            own_python = True
    if not own_python:
        raise ValueError("Worker did not load its own Python runtime")
    return result


def prepare_runtime_directory(path: Path, *, bundle: Path, forbidden_roots: Sequence[Path]) -> Path:
    path = path.resolve()
    if any(path.is_relative_to(root.resolve()) for root in (bundle, *forbidden_roots)):
        raise ValueError("runtime directory must be outside the bundle and Core/source roots")
    path.mkdir(parents=True, exist_ok=False)
    return path


def check_response(message: WorkerMessage, operation: str, request_id: str) -> None:
    payload = message.payload
    if (
        message.worker_id != WORKER_ID
        or message.type != "response"
        or message.request_id != request_id
        or payload.get("operation") != operation
        or payload.get("generation") != 1
        or payload.get("status") != "ok"
        or not isinstance(payload.get("result"), dict)
    ):
        raise ValueError("response correlation, status or generation mismatch")


def verify_artifact(executable: Path, runtime_directory: Path, *, core_roots: Sequence[Path] = (), timeout: float = 15.0) -> dict:
    import psutil

    if sys.platform != "win32":
        raise ValueError("native DLL evidence is currently Windows-only")
    if not 1 <= timeout <= 30:
        raise ValueError("timeout must be bounded to 1..30 seconds per protocol stage")
    executable = executable.resolve(strict=True)
    bundle = executable.parent
    modules = inspect_archive(executable)
    # The bundle may reside under a repo build directory, but source search and
    # the actual source trees must never be used by the child. Do not ban the
    # whole repository as a module root if that would also ban this bundle.
    forbidden = tuple(Path(p).resolve() for p in core_roots)
    runtime = prepare_runtime_directory(runtime_directory, bundle=bundle, forbidden_roots=(*forbidden, ROOT / "pet", ROOT / "features"))
    environment = isolated_worker_environment(os.environ, core_roots=(*forbidden, ROOT))
    started = time.perf_counter()
    process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=runtime, env=environment)
    lines: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
    overflow = threading.Event()

    def read_output() -> None:
        try:
            while True:
                line = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if len(line) > MAX_MESSAGE_BYTES:
                    overflow.set()
                    return
                lines.put_nowait(line or None)
                if not line:
                    return
        except (OSError, ValueError, queue.Full):
            overflow.set()

    reader = threading.Thread(target=read_output, daemon=True, name="standalone-worker-probe")
    reader.start()

    def send(kind, payload, request_id=None):
        process.stdin.write(encode_message(build_message(WORKER_ID, kind, payload, request_id=request_id)))
        process.stdin.flush()

    def expect(kind: str) -> WorkerMessage:
        deadline = time.perf_counter() + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or overflow.is_set():
                raise RuntimeError(f"Worker timeout/output bound waiting for {kind}")
            try:
                raw = lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError(f"Worker timeout waiting for {kind}") from exc
            if raw is None:
                raise RuntimeError(f"Worker exited before {kind}")
            message = decode_message(raw)
            if message.worker_id != WORKER_ID:
                raise ValueError("wrong worker ID")
            if message.type == "heartbeat" and kind != "heartbeat":
                continue
            if message.type != kind:
                raise ValueError(f"unexpected Worker message waiting for {kind}")
            return message

    try:
        hello = expect("hello")
        if hello.payload.get("pid") != process.pid or not CAPABILITIES.issubset(hello.payload.get("capabilities", [])):
            raise ValueError("hello PID or capabilities mismatch")
        send("config_push", {"generation": 1, "max_edge": 1024, "frame_ttl": 20})
        ready = expect("ready")
        if ready.payload.get("generation") != 1 or not ready.payload.get("configured"):
            raise ValueError("ready not configured")
        metrics = {"startup_ms": round((time.perf_counter() - started) * 1000, 2)}
        for operation, arguments in (("release_frame", {}), ("cancel", {"request_id": "no-active-task"})):
            request_id = f"probe-{operation}"
            send("request", {"operation": operation, "generation": 1, "arguments": arguments}, request_id)
            check_response(expect("response"), operation, request_id)
        sampled = psutil.Process(process.pid)
        cpu_before = sampled.cpu_times()
        sample_started = time.perf_counter()
        heartbeat = expect("heartbeat")  # Wait for real periodic heartbeat, not fixed sleeps.
        sample_seconds = time.perf_counter() - sample_started
        if heartbeat.payload.get("generation") != 1 or not heartbeat.payload.get("configured"):
            raise ValueError("heartbeat state mismatch")
        cpu_after = sampled.cpu_times()
        native_paths = [Path(item.path) for item in sampled.memory_maps() if Path(item.path).suffix.lower() in {".dll", ".pyd", ".exe"}]
        loaded = check_loaded_modules(
            native_paths,
            bundle=bundle,
            system_roots=(Path(os.environ["SystemRoot"]),),
            forbidden_roots=(*forbidden, ROOT / "pet", ROOT / "features"),
        )
        metrics.update(
            rss_bytes=sampled.memory_info().rss,
            idle_sample_seconds=round(sample_seconds, 3),
            idle_cpu_seconds=round(cpu_after.user + cpu_after.system - cpu_before.user - cpu_before.system, 6),
            worker_threads=sampled.num_threads(),
        )
        if sampled.children():
            raise ValueError("standalone Worker unexpectedly spawned children")
        shutdown_started = time.perf_counter()
        send("shutdown", {"reason": "validation complete"})
        process.stdin.close()
        returncode = process.wait(timeout=timeout)
        metrics["shutdown_ms"] = round((time.perf_counter() - shutdown_started) * 1000, 2)
        if returncode != 0:
            raise RuntimeError(f"Worker returned {returncode}")
        writes = sorted(str(p.relative_to(runtime)) for p in runtime.rglob("*"))
        if writes:
            raise ValueError(f"Worker wrote files into runtime directory: {writes}")
        return {
            "scope": "Windows frozen lifecycle/native-module check, no capture or model request",
            "executable": str(executable),
            "cwd": str(runtime),
            "returncode": returncode,
            "stages": ["hello", "ready", "release_frame", "cancel", "heartbeat", "shutdown"],
            "metrics": metrics,
            "loaded_native_modules": loaded,
            "pyz_modules": modules,
            "runtime_writes": writes,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        reader.join(timeout=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--runtime-directory", type=Path, required=True, help="new empty directory outside the bundle/Core")
    parser.add_argument("--core-root", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True, help="new evidence JSON, will not overwrite")
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("report already exists; use a new evidence path")
    try:
        report = verify_artifact(args.executable, args.runtime_directory, core_roots=args.core_root)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print("STANDALONE_SCREEN_WORKER_OK returncode=0")
        print(json.dumps(report["metrics"], sort_keys=True))
        return 0
    except Exception as exc:
        print(f"STANDALONE_SCREEN_WORKER_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
