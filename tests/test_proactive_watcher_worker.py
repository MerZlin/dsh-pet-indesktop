# -*- coding: utf-8 -*-
"""Core-side boundary tests for the proactive-screen Worker adapter."""

from __future__ import annotations

import time

import pytest

from pet.chat.models import ProviderConfig
from pet.config import Config
from pet.proactive import ProactiveScreenWatcher


class DummyWindow:
    on_open_chat = True
    mouse_through = False
    _dragging = False
    _physics_mode = None
    _click_effect_phase = 0

    def isVisible(self) -> bool:
        return True


class FakeAdapter:
    """Small deterministic adapter double; no screenshot or network is used."""

    def __init__(self, *, ready: bool = True, active: bool = True, start_result: bool = True) -> None:
        self.ready = ready
        self.active = active
        self.state = "ready" if ready else "stopped"
        self.start_result = start_result
        self.calls: list[tuple[str, object]] = []
        self._next_id = 0

    def _request_id(self, operation: str) -> str:
        self._next_id += 1
        return f"{operation}-{self._next_id}"

    def start(self, config: dict[str, object]) -> bool:
        self.calls.append(("start", dict(config)))
        if self.start_result:
            self.active = True
            self.ready = True
            self.state = "ready"
        return self.start_result

    def stop(self) -> None:
        self.calls.append(("stop", None))
        self.active = False
        self.ready = False
        self.state = "stopped"

    def observe_foreground(self, generation: int) -> str:
        request_id = self._request_id("observe")
        self.calls.append(("observe_foreground", (request_id, generation)))
        return request_id

    def capture_foreground(self, window: dict[str, object], generation: int) -> str:
        request_id = self._request_id("capture")
        self.calls.append(("capture_foreground", (request_id, dict(window), generation)))
        return request_id

    def analyze_frame(
        self,
        frame_id: str,
        provider: ProviderConfig,
        system_prompt: str,
        *,
        generation: int,
        memory_context: str = "",
        pet_name: str = "",
    ) -> str:
        request_id = self._request_id("analyze")
        self.calls.append(
            (
                "analyze_frame",
                {
                    "request_id": request_id,
                    "frame_id": frame_id,
                    "provider": provider,
                    "system_prompt": system_prompt,
                    "generation": generation,
                    "memory_context": memory_context,
                    "pet_name": pet_name,
                },
            )
        )
        return request_id

    def manual_look(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        *,
        generation: int,
        pet_name: str = "",
    ) -> str:
        request_id = self._request_id("manual")
        self.calls.append(
            (
                "manual_look",
                {
                    "request_id": request_id,
                    "provider": provider,
                    "system_prompt": system_prompt,
                    "generation": generation,
                    "pet_name": pet_name,
                },
            )
        )
        return request_id

    def release_frame(self, frame_id: str, *, generation: int | None = None) -> str:
        self.calls.append(("release_frame", (frame_id, generation)))
        return self._request_id("release")

    def cancel(self, request_id: str, *, generation: int | None = None) -> str:
        self.calls.append(("cancel", (request_id, generation)))
        return self._request_id("cancel")


def _provider() -> ProviderConfig:
    return ProviderConfig.from_dict(
        "vision-test",
        {
            "name": "Test Vision",
            "model": "vision-model",
            "api_key": "one-shot-secret",
        },
    )


def _watcher(tmp_path) -> ProactiveScreenWatcher:
    cfg = Config(base=tmp_path)
    cfg.set(
        "proactive_screen",
        {
            "enabled": True,
            "whitelist": ["code.exe"],
            "dwell_seconds": 0,
            "require_idle": False,
            "dry_run": False,
            "pre_cue": False,
            "preset": "custom",
        },
    )
    watcher = ProactiveScreenWatcher(DummyWindow(), cfg, worker_mode="in_process")
    watcher._timer.stop()
    watcher._resolve_vision_provider = lambda _eff: (_provider(), "look at the screen")
    watcher.limiter.allow = lambda: (True, "ok")
    watcher.limiter.try_acquire = lambda: (True, "ok")
    return watcher


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_worker_observation_keeps_policy_in_core_and_dispatches_capture(tmp_path, qt_app) -> None:
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._worker_fallback = False
    watcher._current_hwnd = 42
    # The effective custom preset clamps dwell to at least 15 seconds.
    watcher._entered_ts = time.time() - 20

    watcher._active_worker_request_id = "observe-1"
    watcher._on_worker_observation(
        {
            "request_id": "observe-1",
            "generation": watcher._generation,
            "result": {
                "window": {
                    "hwnd": 42,
                    "pid": 100,
                    "process": "code.exe",
                    "title": "main.py",
                    "rect": [0, 0, 800, 600],
                }
            },
        }
    )

    captures = [payload for name, payload in adapter.calls if name == "capture_foreground"]
    assert len(captures) == 1
    assert captures[0][1]["process"] == "code.exe"
    assert captures[0][2] == watcher._generation
    assert watcher._worker_busy is True
    watcher.pause()


def test_worker_frame_dispatches_analysis_and_stale_frame_is_released(tmp_path, qt_app) -> None:
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._worker_fallback = False
    watcher._worker_busy = True

    watcher._on_frame_ready(
        None,
        "code.exe | main.py",
        42,
        0x1234,
        {
            "_gen": watcher._generation,
            "hwnd": 42,
            "process": "code.exe",
            "title": "main.py",
        },
        worker_frame_id="frame-1",
    )

    analysis = [payload for name, payload in adapter.calls if name == "analyze_frame"]
    assert len(analysis) == 1
    assert analysis[0]["frame_id"] == "frame-1"
    assert analysis[0]["generation"] == watcher._generation
    assert analysis[0]["provider"].api_key == "one-shot-secret"
    assert watcher._active_worker_frame_id == "frame-1"
    assert watcher._request_in_flight is True
    assert watcher._worker_busy is True

    stale_frame = "frame-stale"
    watcher._on_frame_ready(
        None,
        "code.exe | stale",
        42,
        0x1235,
        {"_gen": watcher._generation + 1, "process": "code.exe"},
        worker_frame_id=stale_frame,
    )

    assert any(name == "release_frame" and payload[0] == stale_frame for name, payload in adapter.calls)
    assert watcher._worker_busy is True
    assert watcher._active_worker_frame_id == "frame-1"
    watcher.pause()


def test_manual_worker_result_is_routed_and_stale_generation_is_dropped(tmp_path, qt_app) -> None:
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._worker_fallback = False
    callbacks: list[tuple[str, str, bool]] = []

    assert watcher.request_manual_look(
        _provider(),
        "look at the screen",
        "深深",
        lambda reply, user_text, failed: callbacks.append((reply, user_text, failed)),
    )
    request_id = watcher._manual_request_id
    assert request_id is not None
    assert any(name == "manual_look" for name, _payload in adapter.calls)

    watcher._on_worker_manual(
        {
            "request_id": request_id,
            "generation": watcher._generation + 1,
            "result": {"reply": "stale", "app_info": "code.exe | old"},
        }
    )
    assert callbacks == []

    # A fresh request must still be possible after the stale response was dropped.
    assert watcher.request_manual_look(
        _provider(),
        "look at the screen",
        "深深",
        lambda reply, user_text, failed: callbacks.append((reply, user_text, failed)),
    )
    fresh_id = watcher._manual_request_id
    assert fresh_id is not None
    watcher._on_worker_manual(
        {
            "request_id": fresh_id,
            "generation": watcher._generation,
            "result": {"reply": "fresh reply", "app_info": "code.exe | main.py"},
        }
    )
    assert callbacks == [("fresh reply", "[看看屏幕] 前台窗口：code.exe | main.py", False)]
    watcher.pause()


def test_worker_start_failure_switches_to_in_process_fallback(tmp_path, qt_app) -> None:
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter(ready=False, active=False, start_result=False)
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._worker_fallback = False

    accepted = watcher.request_manual_look(
        _provider(),
        "look at the screen",
        "深深",
        lambda *_args: None,
    )

    assert accepted is False
    assert watcher._worker_fallback is True
    assert watcher.worker_mode == "auto"
    assert any(name == "start" for name, _payload in adapter.calls)
    watcher.pause()


@pytest.mark.parametrize("stage", ["observation", "capture"])
def test_stale_worker_result_cannot_start_new_capture_or_analysis(tmp_path, qt_app, stage):
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._current_hwnd = 42
    watcher._entered_ts = time.time() - 20
    result = {"window": {"hwnd": 42, "pid": 100, "process": "code.exe", "title": "main.py"}, "frame_id": "old-frame", "dhash": 123}
    generation = watcher._generation
    watcher.pause()
    adapter.ready = True
    adapter.active = True
    watcher._worker_busy = True
    watcher._active_worker_request_id = "new-request"
    getattr(watcher, "_on_worker_" + stage)({"request_id": "old-request", "generation": generation, "result": result})
    assert not any(op in {"capture_foreground", "analyze_frame"} for op, _ in adapter.calls)
    assert watcher._active_worker_request_id == "new-request"
    assert watcher._worker_busy
    watcher.pause()


def test_manual_completion_does_not_release_automatic_slot(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "auto"
    watcher._worker_adapter = FakeAdapter()
    assert watcher.request_manual_look(_provider(), "prompt", "pet", lambda *_: None)
    watcher._request_in_flight = watcher._worker_busy = True
    watcher._active_worker_request_id = "auto-request"
    watcher._on_worker_manual({"request_id": watcher._manual_request_id, "generation": watcher._generation, "result": {"reply": "hello"}})
    assert watcher._request_in_flight and watcher._worker_busy
    assert watcher._active_worker_request_id == "auto-request"
    watcher.pause()


def test_disabled_manual_is_handled_without_legacy_fallback(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "disabled"
    calls = []
    assert watcher.request_manual_look(_provider(), "prompt", "pet", lambda *args: calls.append(args))
    assert calls and calls[0][-1] is True
    watcher.pause()


def test_pause_clears_worker_pipeline_and_stops_process(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "auto"
    adapter = watcher._worker_adapter = FakeAdapter()
    watcher._active_worker_request_id = "auto-analysis"
    watcher._request_in_flight = watcher._worker_busy = True
    watcher.pause()
    assert not watcher._request_in_flight
    assert ("stop", None) in adapter.calls


def test_shared_busy_manual_does_not_fall_back_to_parallel_legacy_thread(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "auto"
    watcher._worker_adapter = FakeAdapter()
    assert watcher.request_manual_look(_provider(), "prompt", "one", lambda *_: None)
    calls = []
    assert watcher.request_manual_look(_provider(), "prompt", "two", lambda *args: calls.append(args))
    assert calls and calls[0][-1] is True
    watcher.pause()


def test_manual_transport_backpressure_does_not_start_legacy_in_parallel(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "auto"
    adapter = watcher._worker_adapter = FakeAdapter()
    adapter.manual_look = lambda *args, **kwargs: None
    calls = []
    assert watcher.request_manual_look(_provider(), "prompt", "pet", lambda *args: calls.append(args))
    assert calls and calls[0][-1] is True
    watcher.pause()


def test_manual_completion_with_auto_disabled_releases_idle_worker(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.cfg.data["proactive_screen"]["enabled"] = False
    watcher.worker_mode = "auto"
    adapter = watcher._worker_adapter = FakeAdapter()
    assert watcher.request_manual_look(_provider(), "prompt", "pet", lambda *_: None)
    watcher._on_worker_manual({"request_id": watcher._manual_request_id, "generation": watcher._generation, "result": {"reply": "hello"}})
    assert ("stop", None) in adapter.calls
    watcher.pause()


def test_automatic_policy_rejection_does_not_cancel_manual_request(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    replies = []
    watcher.request_manual_look(_provider(), "look", "pet", lambda *args: replies.append(args))
    manual_id = watcher._manual_request_id
    watcher._active_worker_request_id = "observation"
    watcher._worker_busy = True
    watcher.win._dragging = True
    watcher._on_worker_observation({"request_id": "observation", "generation": watcher._generation, "result": {}})
    assert watcher._manual_request_id == manual_id
    watcher._on_worker_manual({"request_id": manual_id, "generation": watcher._generation, "result": {"reply": "manual result"}})
    assert replies == [("manual result", "[看看屏幕]", False)]
    watcher.pause()


def test_capture_rechecks_whitelist_and_releases_rejected_frame(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._active_worker_request_id = "capture"
    watcher._worker_busy = True
    watcher._on_worker_capture(
        {
            "request_id": "capture",
            "generation": watcher._generation,
            "result": {"frame_id": "private-frame", "dhash": 1, "window": {"hwnd": 42, "process": "password-manager.exe", "title": "private"}},
        }
    )
    assert not any(name == "analyze_frame" for name, _ in adapter.calls)
    assert any(name == "release_frame" and value[0] == "private-frame" for name, value in adapter.calls)
    watcher.pause()


def test_cancel_manual_only_detaches_matching_window(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    watcher.worker_mode = "auto"
    watcher._worker_adapter = FakeAdapter()
    first, second = [], []

    def first_callback(*args):
        first.append(args)

    def second_callback(*args):
        second.append(args)

    watcher.request_manual_look(_provider(), "look", "pet", first_callback)
    request_id = watcher._manual_request_id
    watcher.cancel_manual_look(second_callback)
    assert watcher._manual_request_id == request_id
    watcher.cancel_manual_look(first_callback)
    watcher._on_worker_manual({"request_id": request_id, "generation": watcher._generation, "result": {"reply": "late"}})
    assert not first and not second
    assert not watcher._manual_callbacks
    watcher.pause()


def test_new_observation_does_not_reuse_previous_window_policy(tmp_path, qt_app):
    watcher = _watcher(tmp_path)
    adapter = FakeAdapter()
    watcher.worker_mode = "auto"
    watcher._worker_adapter = adapter
    watcher._active_worker_window = {"process": "old-excluded.exe", "title": "old"}
    watcher._on_tick()
    request = watcher._active_worker_request_id
    watcher._on_worker_observation(
        {"request_id": request, "generation": watcher._generation, "result": {"window": {"hwnd": 42, "process": "code.exe", "title": "new"}}}
    )
    assert watcher._current_hwnd == 42
    watcher.pause()
