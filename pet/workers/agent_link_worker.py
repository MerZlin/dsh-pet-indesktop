# -*- coding: utf-8 -*-
"""Qt-free Agent Link event collector worker.

The worker owns only read-only log tailing and event normalization.  It never
imports ``pet.app`` or any Qt/UI module; the parent process owns presentation,
policy, bridge installation, and user interaction.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any

from .agent_link_source import AgentLinkEventSource
from .protocol import WorkerMessage, WorkerProtocolError, build_message, decode_message, encode_message

WORKER_ID = "agent-link-events"
HEARTBEAT_INTERVAL = 5.0
DEFAULT_POLL_INTERVAL = 0.25


class AgentLinkWorker:
    """Run the Agent Link source loop behind a small JSONL command queue."""

    def __init__(self, *, stdin=None, stdout=None, stderr=None) -> None:
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.stderr = stderr or sys.stderr
        self._commands: queue.Queue[WorkerMessage | None] = queue.Queue()
        self._stop = threading.Event()
        self._output_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._source: AgentLinkEventSource | None = None
        self._generation = 0
        self._poll_interval = DEFAULT_POLL_INTERVAL
        self._configured = False
        self._paused = False

    def _close_stdin(self) -> None:
        """Close the command pipe so the reader thread can leave cleanly.

        ``readline()`` on a Windows pipe cannot be interrupted by a Python
        ``Event`` alone. The reader therefore uses a best-effort close during
        shutdown so the blocked call releases before interpreter finalization.
        Test doubles may not implement ``close``.
        """
        close = getattr(self.stdin, "close", None)
        if close is None:
            return
        try:
            close()
        except (OSError, ValueError):
            pass

    def _write(self, message_type: str, payload: dict[str, Any] | None = None) -> None:
        message = build_message(WORKER_ID, message_type, payload or {})
        data = encode_message(message)
        with self._output_lock:
            self.stdout.write(data)
            flush = getattr(self.stdout, "flush", None)
            if flush is not None:
                flush()

    def _write_error(self, reason: str, *, stage: str = "protocol", fatal: bool = False) -> None:
        try:
            self._write("error", {"stage": stage, "reason": str(reason)[:1000], "fatal": bool(fatal), "generation": self._generation})
        except (BrokenPipeError, OSError):
            self._stop.set()

    def _read_commands(self) -> None:
        try:
            while not self._stop.is_set():
                raw = self.stdin.readline()
                if not raw:
                    self._commands.put(None)
                    return
                try:
                    message = decode_message(raw)
                except WorkerProtocolError as exc:
                    self._write_error(str(exc), stage="protocol")
                    continue
                if message.worker_id not in (WORKER_ID, "*"):
                    self._write_error(f"unexpected worker_id: {message.worker_id}", stage="routing")
                    continue
                self._commands.put(message)
                if message.type == "shutdown":
                    # The main loop will process the command and exit. Stop
                    # reading immediately so a parent that keeps its write
                    # channel open cannot strand this thread at readline().
                    return
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._write_error(str(exc), stage="stdin", fatal=True)
            self._commands.put(None)

    def _apply_config(self, message: WorkerMessage) -> None:
        payload = message.payload
        generation = int(payload.get("generation", self._generation + 1))
        sources = payload.get("sources", [])
        if not isinstance(sources, list):
            raise WorkerProtocolError("config_push.sources must be a list")
        poll_interval = float(payload.get("poll_interval", DEFAULT_POLL_INTERVAL))
        if poll_interval <= 0:
            raise WorkerProtocolError("config_push.poll_interval must be positive")
        self._generation = generation
        self._poll_interval = min(10.0, max(0.05, poll_interval))
        if self._source is None:
            self._source = AgentLinkEventSource(sources, generation=generation)
            self._source.prime()
        else:
            self._source.configure(sources, generation=generation)
        self._paused = bool(payload.get("paused", False))
        if self._paused:
            self._source.pause()
        self._configured = True
        self._write("ready", {"generation": generation, "source_count": len(sources)})

    def _handle_command(self, message: WorkerMessage | None) -> None:
        if message is None:
            self._stop.set()
            return
        if message.type == "config_push":
            try:
                self._apply_config(message)
            except (TypeError, ValueError, WorkerProtocolError) as exc:
                self._write_error(str(exc), stage="config")
            return
        if message.type == "shutdown":
            self._stop.set()
            return
        if message.type == "heartbeat":
            self._write("heartbeat", {"generation": self._generation, "echo": message.payload.get("generation")})
            return
        self._write_error(f"unsupported command from parent: {message.type}", stage="protocol")

    def run(self) -> int:
        self._write("hello", {"pid": __import__("os").getpid(), "capabilities": ["agent_events.read", "settings.read", "logging.write"]})
        self._reader = threading.Thread(target=self._read_commands, name="agent-link-worker-stdin", daemon=False)
        self._reader.start()
        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
        try:
            while not self._stop.is_set():
                try:
                    command = self._commands.get(timeout=min(self._poll_interval, 0.25))
                    self._handle_command(command)
                except queue.Empty:
                    pass
                if self._stop.is_set():
                    break
                if self._configured and self._source is not None and not self._paused:
                    for item in self._source.poll():
                        try:
                            self._write("event", item)
                        except (BrokenPipeError, OSError):
                            self._stop.set()
                            break
                    for diagnostic in self._source.drain_diagnostics():
                        self._write_error(diagnostic.get("reason", "source failure"), stage=str(diagnostic.get("stage", "source")))
                now = time.monotonic()
                if now >= next_heartbeat:
                    self._write("heartbeat", {"generation": self._generation, "configured": self._configured})
                    next_heartbeat = now + HEARTBEAT_INTERVAL
        except (BrokenPipeError, OSError):
            return 0
        except Exception as exc:
            self._write_error(str(exc), stage="worker", fatal=True)
            return 1
        finally:
            self._stop.set()
            self._close_stdin()
            if self._reader is not None and self._reader is not threading.current_thread():
                self._reader.join(timeout=1.0)
        return 0


def run_agent_link_worker() -> int:
    return AgentLinkWorker().run()


if __name__ == "__main__":
    raise SystemExit(run_agent_link_worker())
