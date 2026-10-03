# -*- coding: utf-8 -*-
"""Qt-free proactive screen worker.

The worker owns only the expensive and permission-sensitive execution side of
proactive screen analysis.  Policy, user state, quotas, presentation and
secrets remain authoritative in the Core process and are exchanged through the
``pet-worker/v1`` JSONL protocol.
"""

from __future__ import annotations

import io
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, BinaryIO, TextIO

from pet.workers.protocol import MAX_MESSAGE_BYTES, WorkerMessage, WorkerProtocolError, build_message, decode_message, encode_message

WORKER_ID = "proactive-screen"
HEARTBEAT_INTERVAL = 5.0
DEFAULT_FRAME_TTL = 20.0
DEFAULT_MAX_EDGE = 1280
DEFAULT_JPEG_QUALITY = 82
MAX_COMMAND_QUEUE = 64
MAX_ACTIVE_TASKS = 2

_CAPABILITIES = (
    "foreground.read",
    "screenshot.capture",
    "vision.request",
    "logging.write",
)


@dataclass
class _Frame:
    frame_id: str
    generation: int
    jpeg: bytes
    dhash: int | None
    window: dict[str, Any]
    app_info: str
    created_at: float


@dataclass
class _Task:
    request_id: str
    operation: str
    kind: str
    generation: int
    cancel: threading.Event


@dataclass
class _BudgetWait:
    event: threading.Event
    allowed: bool = False
    generation: int = 0


class ProactiveScreenWorker:
    """Run the proactive-screen worker without importing Qt or ``pet.app``."""

    def __init__(
        self,
        stdin: BinaryIO | TextIO | None = None,
        stdout: BinaryIO | TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.stdin = sys.stdin.buffer if stdin is None and hasattr(sys.stdin, "buffer") else stdin or sys.stdin
        self.stdout = sys.stdout.buffer if stdout is None and hasattr(sys.stdout, "buffer") else stdout or sys.stdout
        self.stderr = stderr or sys.stderr

        self._commands: queue.Queue[WorkerMessage | None] = queue.Queue(maxsize=MAX_COMMAND_QUEUE)
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._task_lock = threading.RLock()
        self._tasks: dict[str, _Task] = {}
        self._budget_lock = threading.RLock()
        self._budget_waits: dict[str, _BudgetWait] = {}
        self._frame_lock = threading.RLock()
        self._frame: _Frame | None = None
        self._configured = False
        self._generation = 0
        self._config: dict[str, Any] = {
            "max_edge": DEFAULT_MAX_EDGE,
            "jpeg_quality": DEFAULT_JPEG_QUALITY,
            "frame_ttl": DEFAULT_FRAME_TTL,
        }

    # ------------------------------------------------------------------ wire
    def _write(self, message_type: str, payload: dict[str, Any] | None = None, *, request_id: str | None = None) -> bool:
        try:
            message = build_message(WORKER_ID, message_type, payload or {}, request_id=request_id)
            raw = encode_message(message)
            with self._write_lock:
                target = self.stdout
                if hasattr(target, "buffer"):
                    target = target.buffer
                target.write(raw)
                flush = getattr(target, "flush", None)
                if callable(flush):
                    flush()
            return True
        except (BrokenPipeError, OSError, RuntimeError, WorkerProtocolError):
            self._stop.set()
            return False

    def _write_error(
        self,
        error_code: str,
        message: str,
        *,
        request_id: str | None = None,
        operation: str | None = None,
        generation: int | None = None,
        retryable: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "status": "error",
            "error_code": str(error_code),
            "message": str(message)[:300],
            "retryable": bool(retryable),
        }
        if operation:
            payload["operation"] = operation
        if generation is not None:
            payload["generation"] = int(generation)
        if request_id:
            self._write("response", payload, request_id=request_id)
        else:
            self._write("error", payload)

    def _write_response(
        self,
        request_id: str,
        operation: str,
        *,
        generation: int | None = None,
        result: dict[str, Any] | None = None,
        status: str = "ok",
        error_code: str | None = None,
        message: str | None = None,
        retryable: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "operation": operation,
            "status": status,
        }
        if generation is not None:
            payload["generation"] = int(generation)
        if result is not None:
            payload["result"] = result
        if error_code:
            payload["error_code"] = error_code
        if message:
            payload["message"] = message[:300]
        if status == "error":
            payload["retryable"] = bool(retryable)
        self._write("response", payload, request_id=request_id)

    # --------------------------------------------------------------- commands
    def _read_commands(self) -> None:
        try:
            while not self._stop.is_set():
                raw = self.stdin.readline(MAX_MESSAGE_BYTES + 1)
                if not raw:
                    self._stop.set()
                    return
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                if len(raw) > MAX_MESSAGE_BYTES:
                    self._write_error("message_too_large", "Worker 消息超出大小限制")
                    self._stop.set()
                    return
                try:
                    message = decode_message(bytes(raw))
                except WorkerProtocolError:
                    self._write_error("protocol_error", "收到无效的 Worker 消息")
                    self._diagnostic("protocol", "invalid JSONL message")
                    continue
                if message.worker_id not in (WORKER_ID, "*"):
                    self._diagnostic("routing", "收到其他 Worker 的消息")
                    continue
                try:
                    self._commands.put_nowait(message)
                except queue.Full:
                    self._write_error("queue_full", "Worker 控制队列已满", retryable=True)
                    self._stop.set()
                    return
                if message.type == "shutdown":
                    return
        except (BrokenPipeError, OSError, ValueError):
            self._stop.set()

    def _diagnostic(self, stage: str, reason: str) -> None:
        # Diagnostic text deliberately never contains request payloads or
        # provider values, which prevents accidental secret/screenshot leaks.
        self._write(
            "error",
            {
                "stage": str(stage),
                "reason": str(reason)[:300],
                "generation": self._generation,
            },
        )

    def _apply_config(self, message: WorkerMessage) -> None:
        raw = message.payload
        try:
            generation = int(raw.get("generation", self._generation + 1))
        except (TypeError, ValueError) as exc:
            raise WorkerProtocolError("config generation must be an integer") from exc
        if generation != self._generation:
            self._cancel_all_tasks()
            with self._frame_lock:
                self._frame = None
        config: dict[str, Any] = {}
        for key, default in self._config.items():
            value = raw.get(key, default)
            if key == "max_edge":
                try:
                    value = max(320, min(4096, int(value)))
                except (TypeError, ValueError):
                    value = default
            elif key == "jpeg_quality":
                try:
                    value = max(40, min(95, int(value)))
                except (TypeError, ValueError):
                    value = default
            elif key == "frame_ttl":
                try:
                    value = max(1.0, min(120.0, float(value)))
                except (TypeError, ValueError):
                    value = default
            config[key] = value
        self._generation = generation
        self._config = config
        self._configured = True
        self._write(
            "ready",
            {
                "generation": generation,
                "capabilities": list(_CAPABILITIES),
                "configured": True,
            },
        )

    def _handle_command(self, message: WorkerMessage | None) -> None:
        if message is None or message.type == "shutdown":
            self._stop.set()
            self._cancel_all_tasks()
            return
        if message.type == "config_push":
            try:
                self._apply_config(message)
            except (TypeError, ValueError, WorkerProtocolError):
                self._write_error("config_invalid", "Worker 配置无效")
            return
        if message.type == "heartbeat":
            self._write("heartbeat", {"generation": self._generation, "configured": self._configured})
            return
        if message.type == "response":
            self._handle_response(message)
            return
        if message.type != "request":
            self._write_error("unsupported_message", "Worker 不支持该消息类型")
            return
        self._handle_request(message)

    def _handle_response(self, message: WorkerMessage) -> None:
        operation = str(message.payload.get("operation", ""))
        if operation != "budget_check":
            self._diagnostic("response", "忽略未知的反向响应")
            return
        with self._budget_lock:
            pending = self._budget_waits.get(message.request_id or "")
            if pending is None or message.payload.get("generation") != pending.generation:
                return
            payload = message.payload
            result = payload.get("result")
            allowed = payload.get("allowed")
            if isinstance(result, dict):
                allowed = result.get("allowed", allowed)
            pending.allowed = payload.get("status") == "ok" and bool(allowed)
            pending.event.set()

    def _handle_request(self, message: WorkerMessage) -> None:
        request_id = message.request_id or ""
        payload = message.payload
        operation = str(payload.get("operation", "")).strip()
        generation = self._payload_generation(payload)
        arguments = payload.get("arguments", {})
        if not operation:
            self._write_response(request_id, "", status="error", error_code="missing_operation", message="缺少操作类型")
            return
        if not isinstance(arguments, dict):
            self._write_response(
                request_id,
                operation,
                generation=generation,
                status="error",
                error_code="invalid_arguments",
                message="请求参数无效",
            )
            return
        if operation == "release_frame":
            frame_id = str(arguments.get("frame_id", ""))
            with self._frame_lock:
                if self._frame is not None and (not frame_id or self._frame.frame_id == frame_id):
                    self._frame = None
            self._write_response(request_id, operation, generation=generation, result={"released": True})
            return
        if operation == "cancel":
            target = str(arguments.get("request_id", ""))
            cancelled = self._cancel_task(target)
            self._write_response(request_id, operation, generation=generation, result={"cancelled": cancelled})
            return
        if operation not in {"observe_foreground", "capture_foreground", "analyze_frame", "manual_look"}:
            self._write_response(
                request_id,
                operation,
                generation=generation,
                status="error",
                error_code="unknown_operation",
                message="不支持的识屏操作",
            )
            return

        if not self._configured or generation is None or generation < 0:
            self._write_error("not_ready", "Worker 尚未配置或请求代数无效", request_id=request_id, operation=operation, generation=generation)
            return
        # The caller cannot disguise automatic analysis as manual to bypass quota.
        kind = "manual" if operation == "manual_look" else "automatic"
        task_generation = self._generation if generation is None else generation
        with self._task_lock:
            if len(self._tasks) >= MAX_ACTIVE_TASKS or any(task.kind == kind for task in self._tasks.values()):
                self._write_response(
                    request_id,
                    operation,
                    generation=task_generation,
                    status="error",
                    error_code="busy",
                    message="同类识屏请求正在处理",
                    retryable=True,
                )
                return
            task = _Task(request_id, operation, kind, task_generation, threading.Event())
            self._tasks[request_id] = task
        thread = threading.Thread(
            target=self._run_task,
            args=(task, arguments),
            daemon=True,
            name=f"proactive-screen-{kind}",
        )
        thread.start()

    @staticmethod
    def _payload_generation(payload: dict[str, Any]) -> int | None:
        value = payload.get("generation")
        if value is None:
            arguments = payload.get("arguments")
            if isinstance(arguments, dict):
                value = arguments.get("generation")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    # -------------------------------------------------------------- task life
    def _run_task(self, task: _Task, arguments: dict[str, Any]) -> None:
        response: dict[str, Any]
        try:
            self._check_cancel(task)
            if task.operation == "observe_foreground":
                result = self._observe_foreground(task)
            elif task.operation == "capture_foreground":
                result = self._capture_foreground(task, arguments)
            elif task.operation == "analyze_frame":
                result = self._analyze_frame(task, arguments)
            else:
                result = self._manual_look(task, arguments)
            self._check_cancel(task)
            # Never return a credential even if a remote error/service echoes it.
            if "reply" in result:
                reply = str(result["reply"])
                provider = arguments.get("provider", {})
                for key in ("api_key", "vision_api_key"):
                    secret = provider.get(key) if isinstance(provider, dict) else None
                    if secret:
                        reply = reply.replace(str(secret), "[redacted]")
                result["reply"] = reply[:8000]
            response = {"result": result}
        except _Cancelled:
            response = {"status": "error", "error_code": "cancelled", "message": "请求已取消"}
        except _WorkerOperationError as exc:
            response = {"status": "error", "error_code": exc.code, "message": exc.message, "retryable": exc.retryable}
        except Exception:
            # Provider exceptions can include secrets and full request bodies.
            response = {"status": "error", "error_code": "worker_operation_failed", "message": "识屏操作失败", "retryable": True}
        finally:
            arguments.clear()
            with self._task_lock:
                self._tasks.pop(task.request_id, None)
        # Release the execution slot BEFORE acknowledging; Core may immediately
        # send the next stage when it sees this response.
        self._write_response(task.request_id, task.operation, generation=task.generation, **response)

    def _check_cancel(self, task: _Task) -> None:
        if task.cancel.is_set() or self._stop.is_set():
            raise _Cancelled

    def _observe_foreground(self, task: _Task) -> dict[str, Any]:
        self._check_cancel(task)
        from . import vision

        info = vision.foreground_window_info()
        return {"window": self._safe_window(info), "app_info": self._app_info(info)}

    def _capture_foreground(self, task: _Task, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_cancel(task)
        from . import vision

        expected = arguments.get("window")
        expected_hwnd = self._coerce_int(expected.get("hwnd")) if isinstance(expected, dict) else None
        info = vision.foreground_window_info()
        expected_pid = self._coerce_int(expected.get("pid")) if isinstance(expected, dict) else None
        if (
            not info
            or expected_hwnd is None
            or self._coerce_int(info.get("hwnd")) != expected_hwnd
            or expected_pid is None
            or self._coerce_int(info.get("pid")) != expected_pid
        ):
            raise _WorkerOperationError("foreground_changed", "前台窗口已变化", retryable=True)
        rect = self._rect(info.get("rect"))
        if rect is None:
            raise _WorkerOperationError("capture_failed", "窗口矩形无效", retryable=True)
        image = vision.capture_window_rect(rect)
        self._check_cancel(task)
        if image is None:
            raise _WorkerOperationError("capture_failed", "截图失败", retryable=True)
        latest = vision.foreground_window_info()
        if not latest or self._coerce_int(latest.get("hwnd")) != expected_hwnd or self._coerce_int(latest.get("pid")) != expected_pid:
            raise _WorkerOperationError("foreground_changed", "截图期间前台窗口已变化", retryable=True)
        jpeg, dhash = self._encode_image(image)
        frame_id = uuid.uuid4().hex
        frame = _Frame(frame_id, task.generation, jpeg, dhash, self._safe_window(info), self._app_info(info), time.monotonic())
        with self._frame_lock:
            self._check_cancel(task)
            self._frame = frame
        return {
            "frame_id": frame_id,
            "dhash": dhash,
            "window": frame.window,
            "app_info": frame.app_info,
            "size": len(jpeg),
        }

    def _analyze_frame(self, task: _Task, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_cancel(task)
        frame_id = str(arguments.get("frame_id", ""))
        with self._frame_lock:
            frame = self._frame
            if frame is None or frame.frame_id != frame_id or frame.generation != task.generation:
                raise _WorkerOperationError("frame_unavailable", "截图已过期", retryable=True)
            if time.monotonic() - frame.created_at > float(self._config.get("frame_ttl", DEFAULT_FRAME_TTL)):
                self._frame = None
                raise _WorkerOperationError("frame_expired", "截图已过期", retryable=True)
            self._frame = None  # Consume once, including failure/cancellation paths.
            jpeg = frame.jpeg
            app_info = frame.app_info
            window = dict(frame.window)
        provider = self._provider_from_arguments(arguments.get("provider"))
        from . import vision

        self._check_cancel(task)
        budget = None
        if task.kind == "automatic":

            def budget() -> bool:
                return self._request_budget(task)

        reply = vision._post_vision_request(
            jpeg,
            app_info,
            str(arguments.get("system_prompt", "")),
            provider,
            memory_context=str(arguments.get("memory_context", "")),
            consume_budget=budget,
            pet_name=str(arguments.get("pet_name", "")),
        )
        self._check_cancel(task)
        with self._frame_lock:
            if self._frame is not None and self._frame.frame_id == frame_id:
                self._frame = None
        return {"reply": reply, "window": window, "app_info": app_info, "request_metadata": {"kind": task.kind}}

    def _manual_look(self, task: _Task, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_cancel(task)
        from . import vision

        try:
            jpeg = vision.capture_screen_bytes()
        except Exception as exc:
            raise _WorkerOperationError("capture_failed", "截图失败", retryable=True) from exc
        self._check_cancel(task)
        info = vision.foreground_window_info()
        app_info = self._app_info(info)
        provider = self._provider_from_arguments(arguments.get("provider"))
        reply = vision._post_vision_request(
            jpeg,
            app_info,
            str(arguments.get("system_prompt", "")),
            provider,
            memory_context=str(arguments.get("memory_context", "")),
            consume_budget=lambda: self._manual_attempt_allowed(task),
            pet_name=str(arguments.get("pet_name", "")),
        )
        self._check_cancel(task)
        return {"reply": reply, "window": self._safe_window(info), "app_info": app_info, "request_metadata": {"kind": "manual"}}

    def _manual_attempt_allowed(self, task: _Task) -> bool:
        self._check_cancel(task)
        return True  # Manual requests never charge the automatic quota.

    def _request_budget(self, task: _Task) -> bool:
        self._check_cancel(task)
        request_id = uuid.uuid4().hex
        pending = _BudgetWait(threading.Event(), generation=task.generation)
        with self._budget_lock:
            self._budget_waits[request_id] = pending
        self._write(
            "request",
            {
                "operation": "budget_check",
                "generation": task.generation,
                "arguments": {"kind": task.kind, "parent_request_id": task.request_id},
            },
            request_id=request_id,
        )
        try:
            deadline = time.monotonic() + 5.0
            while not pending.event.is_set() and not task.cancel.is_set() and not self._stop.is_set():
                pending.event.wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    break
            if task.cancel.is_set() or self._stop.is_set():
                raise _Cancelled
            return pending.event.is_set() and pending.allowed
        finally:
            with self._budget_lock:
                self._budget_waits.pop(request_id, None)

    # ------------------------------------------------------------ safe helpers
    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _rect(cls, value: Any) -> tuple[int, int, int, int] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        vals = [cls._coerce_int(item) for item in value]
        if any(item is None for item in vals):
            return None
        x, y, w, h = (item for item in vals if item is not None)
        return x, y, w, h

    @classmethod
    def _safe_window(cls, info: Any) -> dict[str, Any]:
        if not isinstance(info, dict):
            return {}
        result: dict[str, Any] = {}
        for key in ("hwnd", "pid"):
            value = cls._coerce_int(info.get(key))
            if value is not None:
                result[key] = value
        process = info.get("process")
        title = info.get("title")
        if process:
            result["process"] = str(process)[:260]
        if title:
            result["title"] = str(title)[:500]
        rect = cls._rect(info.get("rect"))
        if rect is not None:
            result["rect"] = list(rect)
        return result

    @staticmethod
    def _app_info(info: Any) -> str:
        if not isinstance(info, dict):
            return ""
        return " | ".join(str(value) for value in (info.get("process"), info.get("title")) if value)[:800]

    def _encode_image(self, image: Any) -> tuple[bytes, int]:
        from PIL import Image

        from ..common.hashing import image_dhash

        max_edge = int(self._config.get("max_edge", DEFAULT_MAX_EDGE))
        quality = int(self._config.get("jpeg_quality", DEFAULT_JPEG_QUALITY))
        dhash = image_dhash(image)  # Match legacy thresholds: hash before downsampling.
        rgb = image.convert("RGB")
        width, height = rgb.size
        scale = max_edge / max(width, height, 1)
        if scale < 1.0:
            rgb = rgb.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue(), dhash

    @staticmethod
    def _provider_from_arguments(raw: Any) -> Any:
        if not isinstance(raw, dict):
            raise _WorkerOperationError("provider_missing", "视觉服务配置缺失", retryable=False)
        try:
            from ..common.models import VisionRequestConfig

            provider = VisionRequestConfig.from_dict(raw)
            if not provider.api_key:
                raise _WorkerOperationError("provider_key_missing", "视觉服务未配置 API Key", retryable=False)
            return provider
        except _WorkerOperationError:
            raise
        except Exception as exc:
            raise _WorkerOperationError("provider_invalid", "视觉服务配置无效", retryable=False) from exc

    def _cancel_task(self, request_id: str) -> bool:
        with self._task_lock:
            task = self._tasks.get(request_id)
            if task is None:
                return False
            task.cancel.set()
            return True

    def _cancel_all_tasks(self) -> None:
        with self._task_lock:
            for task in self._tasks.values():
                task.cancel.set()
        with self._budget_lock:
            for pending in self._budget_waits.values():
                pending.allowed = False
                pending.event.set()

    # ------------------------------------------------------------------- main
    def run(self) -> int:
        self._write("hello", {"pid": os.getpid(), "capabilities": list(_CAPABILITIES), "lease_claimed": os.environ.get("DSH_PET_FEATURE_LEASE_CLAIMED") == "1"})
        self._reader = threading.Thread(target=self._read_commands, name="proactive-screen-stdin", daemon=True)
        self._reader.start()
        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
        try:
            while not self._stop.is_set():
                try:
                    command = self._commands.get(timeout=0.25)
                    self._handle_command(command)
                except queue.Empty:
                    pass
                now = time.monotonic()
                with self._frame_lock:
                    if self._frame is not None and now - self._frame.created_at > self._config["frame_ttl"]:
                        self._frame = None
                if not self._stop.is_set() and now >= next_heartbeat:
                    self._write(
                        "heartbeat",
                        {
                            "generation": self._generation,
                            "configured": self._configured,
                            "active_tasks": len(self._tasks),
                        },
                    )
                    next_heartbeat = now + HEARTBEAT_INTERVAL
        except (BrokenPipeError, OSError):
            return 0
        finally:
            self._stop.set()
            self._cancel_all_tasks()
            with self._frame_lock:
                self._frame = None
            if self._reader is not None and self._reader is not threading.current_thread():
                self._reader.join(timeout=1.0)
        return 0


class _Cancelled(Exception):
    pass


class _WorkerOperationError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def run_proactive_screen_worker() -> int:
    return ProactiveScreenWorker().run()


if __name__ == "__main__":
    raise SystemExit(run_proactive_screen_worker())
