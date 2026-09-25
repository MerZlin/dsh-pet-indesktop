# -*- coding: utf-8 -*-
"""Asynchronous QProcess supervisor for built-in JSONL workers."""

from __future__ import annotations

import logging
import sys
import time
import weakref
from collections import deque
from typing import Any, Mapping, Sequence

from PySide6.QtCore import QEventLoop, QObject, QProcess, QTimer, Signal

from .protocol import MAX_MESSAGE_BYTES, WorkerMessage, WorkerProtocolError, build_message, decode_message, encode_message

log = logging.getLogger("dsh-pet-standalone.worker-supervisor")
_LIVE_SUPERVISORS: weakref.WeakSet = weakref.WeakSet()


class WorkerSupervisor(QObject):
    """Own one worker process without blocking the GUI thread.

    The supervisor deliberately accepts a program and argument vector so tests
    can launch a small fake worker.  Production callers use the current Python
    interpreter or the frozen executable and the explicit ``--worker`` route.
    """

    state_changed = Signal(str)
    message_received = Signal(object)
    ready = Signal()
    failed = Signal(str)
    diagnostic = Signal(str, object)

    DISABLED = "disabled"
    STARTING = "starting"
    HANDSHAKING = "handshaking"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    FAULT = "fault"

    def __init__(
        self,
        worker_id: str,
        parent: QObject | None = None,
        *,
        program: str | None = None,
        arguments: Sequence[str] | None = None,
        handshake_timeout_ms: int = 5000,
        heartbeat_interval_ms: int = 5000,
        heartbeat_timeout_ms: int = 15000,
        shutdown_timeout_ms: int = 2000,
        max_restarts: int = 3,
        restart_window_s: float = 60.0,
        restart_backoff_s: Sequence[float] = (0.5, 1.0, 2.0),
    ) -> None:
        super().__init__(parent)
        self.worker_id = str(worker_id)
        self.program, self.arguments = self._resolve_command(self.worker_id, program, arguments)
        self.handshake_timeout_ms = max(100, int(handshake_timeout_ms))
        self.heartbeat_interval_ms = max(250, int(heartbeat_interval_ms))
        self.heartbeat_timeout_ms = max(self.heartbeat_interval_ms, int(heartbeat_timeout_ms))
        self.shutdown_timeout_ms = max(100, int(shutdown_timeout_ms))
        self.max_restarts = max(0, int(max_restarts))
        self.restart_window_s = max(1.0, float(restart_window_s))
        self.restart_backoff_s = tuple(max(0.0, float(item)) for item in restart_backoff_s) or (0.0,)

        self._state = self.DISABLED
        self._process: QProcess | None = None
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._config: dict[str, Any] = {}
        self._generation = 0
        self._last_heartbeat = 0.0
        self._restart_times: deque[float] = deque()
        self._restart_timer: QTimer | None = None
        self._shutdown_timer: QTimer | None = None
        self._terminate_timer: QTimer | None = None
        self._io_timer = QTimer(self)
        self._io_timer.setInterval(50)
        self._io_timer.timeout.connect(self._poll_process_io)
        self._handshake_timer = QTimer(self)
        self._handshake_timer.setSingleShot(True)
        self._handshake_timer.timeout.connect(self._on_handshake_timeout)
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._check_heartbeat)
        self._requested_stop = False
        self._accept_events = False
        _LIVE_SUPERVISORS.add(self)

    @staticmethod
    def _resolve_command(worker_id: str, program: str | None, arguments: Sequence[str] | None) -> tuple[str, list[str]]:
        if program:
            return str(program), list(arguments or ())
        if getattr(sys, "frozen", False):
            return sys.executable, ["--worker", worker_id]
        return sys.executable, ["-m", "pet", "--worker", worker_id]

    @property
    def state(self) -> str:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def process(self) -> QProcess | None:
        return self._process

    def _set_state(self, state: str) -> None:
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(state)

    def _emit_diagnostic(self, stage: str, detail: Any = None) -> None:
        payload = detail if isinstance(detail, dict) else {"detail": str(detail) if detail is not None else ""}
        payload = dict(payload)
        payload.setdefault("worker_id", self.worker_id)
        payload.setdefault("generation", self._generation)
        self.diagnostic.emit(stage, payload)

    def start(self, config: Mapping[str, Any] | None = None) -> bool:
        if self._state in {self.STARTING, self.HANDSHAKING, self.READY, self.STOPPING}:
            return False
        previous = self._process
        if previous is not None:
            if previous.state() != QProcess.ProcessState.NotRunning:
                return False
            self._release_process(previous)
        self._cancel_restart_timer()
        self._requested_stop = False
        self._accept_events = False
        self._config = dict(config or {})
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()
        self._generation += 1
        self._last_heartbeat = time.monotonic()
        process = QProcess(self)
        process.setProgram(self.program)
        process.setArguments(self.arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.started.connect(self._on_started)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_process_finished)
        self._process = process
        self._set_state(self.STARTING)
        process.start()
        return True

    def configure(self, config: Mapping[str, Any]) -> bool:
        self._config = dict(config)
        if self._state == self.READY:
            return self._send_config()
        return False

    def _on_started(self) -> None:
        if self._process is None:
            return
        self._set_state(self.HANDSHAKING)
        self._handshake_timer.start(self.handshake_timeout_ms)
        self._heartbeat_timer.start()
        # QProcess normally emits readyReadStandardOutput, but a polling
        # fallback makes the protocol robust when a long-running Qt test
        # leaves a native notification queued or missed.
        self._io_timer.start()

    def _on_handshake_timeout(self) -> None:
        if self._state in {self.STARTING, self.HANDSHAKING}:
            self._fail_current("handshake timeout")

    def _send(self, message_type: str, payload: Mapping[str, Any] | None = None) -> bool:
        process = self._process
        if process is None or process.state() != QProcess.ProcessState.Running:
            return False
        try:
            data = encode_message(build_message(self.worker_id, message_type, dict(payload or {})))
            written = process.write(data)
            if written < 0:
                self._emit_diagnostic("write", {"reason": "QProcess.write returned -1"})
                return False
            return True
        except (WorkerProtocolError, OSError, RuntimeError) as exc:
            self._emit_diagnostic("write", {"reason": str(exc)})
            return False

    def _send_config(self) -> bool:
        body = dict(self._config)
        body["generation"] = self._generation
        return self._send("config_push", body)

    def stop(self) -> None:
        self._requested_stop = True
        self._accept_events = False
        self._cancel_restart_timer()
        self._handshake_timer.stop()
        self._heartbeat_timer.stop()
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            if process is not None:
                self._release_process(process)
            self._set_state(self.STOPPED)
            return
        self._set_state(self.STOPPING)
        self._send("shutdown", {"generation": self._generation})
        # Closing the parent write channel delivers EOF after the shutdown
        # frame. The worker uses EOF to release its stdin reader on Windows,
        # where a blocking pipe read cannot be interrupted by an Event alone.
        process.closeWriteChannel()
        if self._shutdown_timer is None:
            self._shutdown_timer = QTimer(self)
            self._shutdown_timer.setSingleShot(True)
            self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        self._shutdown_timer.start(self.shutdown_timeout_ms)

    def _on_shutdown_timeout(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._emit_diagnostic("shutdown_timeout", {"reason": "worker did not exit after graceful shutdown"})
        process.terminate()
        if self._terminate_timer is None:
            self._terminate_timer = QTimer(self)
            self._terminate_timer.setSingleShot(True)
            self._terminate_timer.timeout.connect(self._on_terminate_timeout)
        self._terminate_timer.start(500)

    def _on_terminate_timeout(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self._emit_diagnostic("kill_timeout", {"reason": "worker did not exit after terminate"})
            process.kill()

    def _poll_process_io(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if process.bytesAvailable() > 0:
            self._read_stdout()
        if process.bytesAvailable() > 0:
            self._read_stderr()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None:
            return
        self._stdout_buffer.extend(bytes(process.readAllStandardOutput()))
        self._consume_lines(self._stdout_buffer, is_stderr=False)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        self._stderr_buffer.extend(bytes(process.readAllStandardError()))
        if len(self._stderr_buffer) > MAX_MESSAGE_BYTES:
            self._stderr_buffer = self._stderr_buffer[-MAX_MESSAGE_BYTES:]
        while b"\n" in self._stderr_buffer:
            line, _, rest = self._stderr_buffer.partition(b"\n")
            self._stderr_buffer = bytearray(rest)
            if line.strip():
                self._emit_diagnostic("stderr", {"line": line.decode("utf-8", errors="replace")[:2000]})

    def _consume_lines(self, buffer: bytearray, *, is_stderr: bool) -> None:
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer[:] = rest
            if len(line) > MAX_MESSAGE_BYTES:
                self._emit_diagnostic("protocol", {"reason": "worker line exceeds message limit"})
                continue
            if not line.strip():
                continue
            try:
                message = decode_message(bytes(line))
            except WorkerProtocolError as exc:
                self._emit_diagnostic("protocol", {"reason": str(exc)})
                continue
            self._handle_message(message)
        if len(buffer) > MAX_MESSAGE_BYTES:
            self._emit_diagnostic("protocol", {"reason": "worker output has an overlong unterminated line"})
            buffer.clear()

    def _handle_message(self, message: WorkerMessage) -> None:
        if message.worker_id != self.worker_id:
            self._emit_diagnostic("routing", {"reason": f"unexpected worker_id: {message.worker_id}"})
            return
        payload = message.payload
        generation = payload.get("generation")
        if generation is not None:
            try:
                generation = int(generation)
            except (TypeError, ValueError):
                self._emit_diagnostic("generation", {"reason": "generation is not an integer"})
                return
            if message.type in {"event", "heartbeat", "error", "ready"} and generation != self._generation:
                self._emit_diagnostic("stale_message", {"message_type": message.type, "message_generation": generation})
                return
        if message.type == "hello":
            if self._state not in {self.STARTING, self.HANDSHAKING}:
                self._emit_diagnostic("lifecycle", {"reason": "hello received outside handshake"})
                return
            self._set_state(self.HANDSHAKING)
            if not self._send_config():
                self._fail_current("failed to send config")
            return
        if message.type == "ready":
            self._handshake_timer.stop()
            self._last_heartbeat = time.monotonic()
            self._accept_events = True
            self._set_state(self.READY)
            self.ready.emit()
            return
        if message.type == "heartbeat":
            self._last_heartbeat = time.monotonic()
            return
        if message.type == "error":
            self._emit_diagnostic("worker_error", payload)
        if message.type in {"event", "error", "heartbeat"}:
            self.message_received.emit(message)
            return
        if message.type == "shutdown":
            return
        self._emit_diagnostic("protocol", {"reason": f"unexpected worker message: {message.type}"})

    def _check_heartbeat(self) -> None:
        if self._state not in {self.HANDSHAKING, self.READY}:
            return
        if time.monotonic() - self._last_heartbeat > self.heartbeat_timeout_ms / 1000.0:
            self._fail_current("heartbeat timeout")

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        self._emit_diagnostic("process_error", {"error": str(error), "error_string": self._process.errorString() if self._process else ""})
        if self._state in {self.STARTING, self.HANDSHAKING, self.READY}:
            self.failed.emit(self._process.errorString() if self._process else "worker process error")

    def _fail_current(self, reason: str) -> None:
        self._accept_events = False
        self._handshake_timer.stop()
        self._heartbeat_timer.stop()
        self._emit_diagnostic("failure", {"reason": reason})
        self.failed.emit(reason)
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if self._terminate_timer is None:
                self._terminate_timer = QTimer(self)
                self._terminate_timer.setSingleShot(True)
                self._terminate_timer.timeout.connect(self._on_terminate_timeout)
            self._terminate_timer.start(500)
        else:
            self._schedule_restart(reason)

    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process = self._process
        if self._shutdown_timer is not None:
            self._shutdown_timer.stop()
        if self._terminate_timer is not None:
            self._terminate_timer.stop()
        self._handshake_timer.stop()
        self._heartbeat_timer.stop()
        self._io_timer.stop()
        self._read_stdout()
        self._read_stderr()
        if process is not None:
            self._release_process(process)
        if self._requested_stop:
            self._set_state(self.STOPPED)
            return
        detail = {"exit_code": exit_code, "exit_status": str(exit_status)}
        self._set_state(self.CRASHED)
        self._emit_diagnostic("crashed", detail)
        self._schedule_restart("worker exited")

    def _schedule_restart(self, reason: str) -> None:
        if self._requested_stop:
            self._set_state(self.STOPPED)
            return
        now = time.monotonic()
        while self._restart_times and now - self._restart_times[0] > self.restart_window_s:
            self._restart_times.popleft()
        if len(self._restart_times) >= self.max_restarts:
            self._set_state(self.FAULT)
            self._emit_diagnostic("fault", {"reason": reason, "restart_count": len(self._restart_times)})
            self.failed.emit(reason)
            return
        index = len(self._restart_times)
        self._restart_times.append(now)
        delay = self.restart_backoff_s[min(index, len(self.restart_backoff_s) - 1)]
        self._set_state(self.CRASHED)
        self._emit_diagnostic("restart_scheduled", {"reason": reason, "delay_s": delay, "restart_count": len(self._restart_times)})
        if self._restart_timer is None:
            self._restart_timer = QTimer(self)
            self._restart_timer.setSingleShot(True)
            self._restart_timer.timeout.connect(self._restart_now)
        self._restart_timer.start(max(0, int(delay * 1000)))

    def _restart_now(self) -> None:
        if not self._requested_stop:
            self.start(self._config)

    def _cancel_restart_timer(self) -> None:
        if self._restart_timer is not None:
            self._restart_timer.stop()

    def _release_process(self, process: QProcess) -> None:
        """Release a stopped process exactly once.

        ``QProcess.finished`` can be queued after the native process has
        already reached ``NotRunning``.  Centralising cleanup prevents a
        stopped process from surviving into a later restart or test teardown
        while keeping the production lifecycle asynchronous.
        """
        if self._process is process:
            self._process = None
        try:
            process.deleteLater()
        except RuntimeError:
            # Qt may already have deleted the QObject through its parent.
            pass

    def wait_for_stopped_for_tests(self, timeout_ms: int = 2000) -> None:
        """Wait for this QProcess during test teardown only.

        Production shutdown remains asynchronous.  The test suite needs a small
        bounded drain point so a worker started by one test cannot survive into
        the next test and compete for Qt/process resources.
        """
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        process.finished.connect(loop.quit)
        timer.timeout.connect(loop.quit)
        timer.start(max(1, int(timeout_ms)))
        loop.exec()
        timer.stop()
        try:
            process.finished.disconnect(loop.quit)
        except (RuntimeError, TypeError):
            pass

    @classmethod
    def _shutdown_live_for_tests(cls, timeout_ms: int = 2000) -> None:
        """Stop and drain all live supervisors created by tests.

        This is intentionally a test-only hook.  It does not change the
        non-blocking production lifecycle and is called by pytest teardown.
        """
        supervisors = tuple(_LIVE_SUPERVISORS)
        for supervisor in supervisors:
            try:
                supervisor.stop()
            except (RuntimeError, OSError):
                log.debug("测试收口 Worker supervisor 失败", exc_info=True)
        per_supervisor_ms = max(100, int(timeout_ms))
        for supervisor in supervisors:
            try:
                supervisor.wait_for_stopped_for_tests(per_supervisor_ms)
            except (RuntimeError, OSError):
                log.debug("等待 Worker supervisor 退出失败", exc_info=True)

    def close(self) -> None:
        self.stop()


__all__ = ["WorkerSupervisor"]
