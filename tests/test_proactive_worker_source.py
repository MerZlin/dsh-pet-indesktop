# -*- coding: utf-8 -*-
"""Pure worker-side safety and media boundary tests."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from pet.workers.proactive_screen_worker import ProactiveScreenWorker


def test_safe_window_only_returns_bounded_display_fields() -> None:
    info = ProactiveScreenWorker._safe_window(
        {
            "hwnd": "42",
            "pid": 7,
            "process": "code.exe",
            "title": "x" * 900,
            "rect": [1, 2, 800, 600],
            "secret": "should-not-cross-boundary",
        }
    )

    assert info == {
        "hwnd": 42,
        "pid": 7,
        "process": "code.exe",
        "title": "x" * 500,
        "rect": [1, 2, 800, 600],
    }


def test_encode_image_is_deterministic_and_in_memory() -> None:
    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    worker._config.update({"max_edge": 320, "jpeg_quality": 80})
    image = Image.new("RGB", (640, 480), (20, 40, 80))

    first, first_hash = worker._encode_image(image)
    second, second_hash = worker._encode_image(image)

    assert first_hash == second_hash
    assert first == second
    assert first.startswith(b"\xff\xd8")
    assert len(first) < 100_000
    assert worker._frame is None


def test_provider_parser_drops_keyring_references() -> None:
    provider = ProactiveScreenWorker._provider_from_arguments(
        {
            "provider_type": "openai",
            "model": "vision-model",
            "api_key": "one-shot-key",
            "api_key_ref": "keyring-ref",
            "vision_api_key_ref": "vision-ref",
        }
    )

    assert getattr(provider, "api_key", None) == "one-shot-key"
    assert not hasattr(provider, "api_key_ref") or getattr(provider, "api_key_ref", None) in (None, "")


def _task(operation="capture_foreground"):
    import threading

    from pet.workers.proactive_screen_worker import _Task

    return _Task("req", operation, "automatic", 7, threading.Event())


def test_hash_is_calculated_before_lossy_resize(monkeypatch):
    import pet.proactive as proactive

    sizes = []
    monkeypatch.setattr(proactive, "image_dhash", lambda image: sizes.append(image.size) or 12)
    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    worker._config["max_edge"] = 320
    worker._encode_image(Image.new("RGB", (1000, 800)))
    assert sizes == [(1000, 800)]


def test_disappeared_foreground_after_capture_is_rejected(monkeypatch):
    import pytest

    from pet import vision
    from pet.workers.proactive_screen_worker import _WorkerOperationError

    info = {"hwnd": 42, "pid": 7, "rect": [0, 0, 20, 20]}
    items = iter([info, None])
    monkeypatch.setattr(vision, "foreground_window_info", lambda: next(items))
    monkeypatch.setattr(vision, "capture_window_rect", lambda _rect: Image.new("RGB", (20, 20)))
    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    with pytest.raises(_WorkerOperationError, match="前台窗口"):
        worker._capture_foreground(_task(), {"window": info})
    assert worker._frame is None


def test_failed_analysis_consumes_frame_and_does_not_retain_secret(monkeypatch):
    import time

    from pet import vision
    from pet.workers.proactive_screen_worker import _Frame

    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    worker._frame = _Frame("frame", 7, b"image", 0, {}, "", time.monotonic())

    def fail(*args, **kwargs):
        raise RuntimeError("SECRET")

    monkeypatch.setattr(vision, "_post_vision_request", fail)
    arguments = {"frame_id": "frame", "provider": {"api_key": "SECRET"}}
    worker._run_task(_task("analyze_frame"), arguments)
    assert worker._frame is None
    assert "SECRET" not in worker.stdout.getvalue().decode()
    assert not arguments


def test_response_is_published_after_execution_slot_released(monkeypatch):
    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    task = _task("observe_foreground")
    worker._tasks[task.request_id] = task
    monkeypatch.setattr(worker, "_observe_foreground", lambda _task: {"window": {}})
    states = []
    monkeypatch.setattr(worker, "_write_response", lambda *args, **kwargs: states.append(len(worker._tasks)))
    worker._run_task(task, {})
    assert states == [0]


def test_control_reader_bounds_each_input_line():
    from pet.workers.protocol import MAX_MESSAGE_BYTES

    class BoundedInput(BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= MAX_MESSAGE_BYTES + 1
            return super().readline(size)

    worker = ProactiveScreenWorker(stdin=BoundedInput(b"x" * (MAX_MESSAGE_BYTES + 2)), stdout=BytesIO())
    worker._read_commands()
    assert worker._stop.is_set()


def test_budget_response_requires_current_request_generation():
    import threading

    from pet.workers.proactive_screen_worker import _BudgetWait
    from pet.workers.protocol import build_message

    worker = ProactiveScreenWorker(stdin=BytesIO(), stdout=BytesIO())
    pending = _BudgetWait(threading.Event())
    pending.generation = 7
    worker._budget_waits["budget-1"] = pending
    worker._handle_response(
        build_message(
            "proactive-screen", "response", {"operation": "budget_check", "generation": 6, "status": "ok", "result": {"allowed": True}}, request_id="budget-1"
        )
    )
    assert not pending.event.is_set()
    worker._handle_response(
        build_message(
            "proactive-screen", "response", {"operation": "budget_check", "generation": 7, "status": "ok", "result": {"allowed": True}}, request_id="budget-1"
        )
    )
    assert pending.event.is_set() and pending.allowed
