# -*- coding: utf-8 -*-
"""Agent Link manager integration tests for the Phase 3 worker boundary."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from pet.agent_link import AgentLinkManager
from pet.config import Config


def _wait_until(predicate, *, timeout_ms: int = 8000) -> None:
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    deadline = QTimer()
    poll.setInterval(10)
    deadline.setSingleShot(True)

    def check() -> None:
        if predicate():
            loop.quit()

    poll.timeout.connect(check)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate(), f"condition not met within {timeout_ms}ms"


def _config_with_agent(tmp_path: Path, *, worker_mode: str) -> Config:
    config = Config(base=tmp_path)
    config.data["agent_link"].update({"dsh": True, "worker_mode": worker_mode})
    config.save()
    return config


class _DummyWindow:
    cats = {"acts": ["写代码"]}
    idles = ["待机"]
    _bubble_busy_until = 0.0

    def __init__(self) -> None:
        self.animations: list[str] = []
        self.bubbles: list[str] = []

    def isVisible(self) -> bool:
        return True

    def request_link_anim(self, name: str) -> None:
        self.animations.append(name)

    def request_link_idle(self) -> None:
        self.animations.append(self.idles[0])

    def _pick(self, values: list[str]) -> str:
        return values[0]

    def show_bubble(self, text: str, duration_ms: int = 3000) -> None:
        del duration_ms
        self.bubbles.append(text)

    def set_link_next_provider(self, provider) -> None:
        self.next_provider = provider


def _prepare_dsh_events(config: Config) -> Path:
    events_dir = config.dir.parent / "dsh-pet-bridge"
    events_dir.mkdir(parents=True, exist_ok=True)
    events_file = events_dir / "dsh.jsonl"
    events_file.touch()
    return events_file


def test_manager_uses_worker_for_dsh_and_forwards_semantic_event(tmp_path, qt_app=None) -> None:
    """真实 Worker 读取 DSH JSONL，Manager 仍收到原有 AgentEvent 信号。"""
    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    config = _config_with_agent(tmp_path, worker_mode="worker")
    events_file = _prepare_dsh_events(config)
    window = _DummyWindow()
    manager = AgentLinkManager(window, config, min_interval=0.0)
    states = []
    manager._worker_source.state_event.connect(states.append)

    try:
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.READY)
        assert manager._worker_managed_keys == {"dsh"}
        assert manager.monitors["dsh"]._worker is None
        with events_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": 1, "agent": "dsh", "event": "AgentStatus", "state": "working"}) + "\n")
        _wait_until(lambda: any(getattr(event, "state", "") == "working" for event in states))
        event = next(event for event in states if event.state == "working")
        assert event.agent == "dsh"
        assert event.gen == manager._worker_source._emit_gen
    finally:
        manager.shutdown()
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.STOPPED, timeout_ms=5000)


def test_manager_can_switch_to_legacy_fallback_after_worker_fault(tmp_path, qt_app=None) -> None:
    """Worker fault stops the isolated source and reactivates the legacy monitor."""
    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    config = _config_with_agent(tmp_path, worker_mode="auto")
    _prepare_dsh_events(config)
    window = _DummyWindow()
    manager = AgentLinkManager(window, config, min_interval=0.0)
    legacy = manager.monitors["dsh"]
    legacy._POLL_INTERVAL_S = 0.05

    try:
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.READY)
        manager._on_worker_fault("test fault")
        _wait_until(lambda: legacy._worker is not None and legacy._running)
        assert manager._worker_fallback_active is True
        assert manager._worker_managed_keys == set()
        assert manager._worker_source._running is False
    finally:
        manager.shutdown()
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.STOPPED, timeout_ms=5000)


def test_real_worker_crash_switches_to_legacy_and_keeps_events(tmp_path, qt_app=None) -> None:
    """A real Worker crash activates the legacy source without losing new events."""
    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    config = _config_with_agent(tmp_path, worker_mode="auto")
    events_file = _prepare_dsh_events(config)
    window = _DummyWindow()
    manager = AgentLinkManager(window, config, min_interval=0.0)
    legacy = manager.monitors["dsh"]
    legacy._POLL_INTERVAL_S = 0.05
    states = []
    legacy.state_event.connect(states.append)

    try:
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.READY)
        manager._worker_source.supervisor.max_restarts = 0
        process = manager._worker_source.supervisor.process
        assert process is not None
        process.kill()

        _wait_until(
            lambda: manager._worker_fallback_active and manager._worker_managed_keys == set() and legacy._worker is not None and legacy._running,
            timeout_ms=8000,
        )
        # 等 legacy tailer 完成启动时的历史回填跳过，再追加新记录，
        # 避免测试把新事件误判为启动前 backlog。
        _wait_until(
            lambda: str(events_file) in legacy._tailer._tailers and legacy._tailer._tailers[str(events_file)]._initial_backfill_done,
            timeout_ms=3000,
        )

        with events_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": 2, "agent": "dsh", "event": "AgentStatus", "state": "working"}) + "\n")
        _wait_until(lambda: any(getattr(event, "state", "") == "working" for event in states), timeout_ms=8000)
        event = next(event for event in states if event.state == "working")
        assert event.agent == "dsh"
        assert event.gen == legacy._emit_gen
    finally:
        manager.shutdown()
        _wait_until(lambda: manager._worker_source.state == manager._worker_source.supervisor.STOPPED, timeout_ms=5000)


def test_worker_adapter_paused_outbox_is_bounded_and_diagnosed(qt_app=None) -> None:
    """Paused presentation may coalesce activity, but never grows unbounded."""
    from pet.workers.agent_link_adapter import WorkerAgentEventSource

    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    source = WorkerAgentEventSource()
    diagnostics = []
    source.worker_diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))
    source._running = True
    source._paused = True

    for index in range(source._OUTBOX_CAP + 17):
        source._emit(source.activity, ("dsh", f"tool-{index}"))

    assert len(source._outbox) == source._OUTBOX_CAP
    assert source._dropped_outbox >= 17
    assert any(stage == "backpressure" for stage, _payload in diagnostics)
    assert diagnostics[0][1]["capacity"] == source._OUTBOX_CAP
    source.stop()


def test_worker_adapter_drops_critical_event_when_paused_queue_has_no_evictable_activity(qt_app=None) -> None:
    from pet.workers.agent_link_adapter import WorkerAgentEventSource

    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    source = WorkerAgentEventSource()
    diagnostics = []
    source.worker_diagnostic.connect(lambda stage, payload: diagnostics.append((stage, payload)))
    source._running = True
    source._paused = True

    for index in range(source._OUTBOX_CAP):
        source._emit(source.raw_record, ("dsh", {"index": index}))
    source._emit_pair(source.state_changed, ("dsh", "working"), source.state_event, (object(),), state="working")

    assert len(source._outbox) == source._OUTBOX_CAP
    assert any(payload.get("reason") == "dropped_event_pair" for stage, payload in diagnostics if stage == "backpressure")
    source.stop()


def test_manager_in_process_mode_does_not_start_worker(tmp_path, qt_app=None) -> None:
    """The explicit rollback mode preserves the original in-process monitor."""
    app = qt_app or QCoreApplication.instance() or QCoreApplication([])
    del app
    config = _config_with_agent(tmp_path, worker_mode="in_process")
    _prepare_dsh_events(config)
    manager = AgentLinkManager(_DummyWindow(), config, min_interval=0.0)

    try:
        assert manager._worker_managed_keys == set()
        assert manager._worker_source._running is False
        assert manager.monitors["dsh"]._worker is not None
        assert manager.monitors["dsh"]._running is True
    finally:
        manager.shutdown()
        _wait_until(lambda: manager.monitors["dsh"]._worker is None or not manager.monitors["dsh"]._worker.is_alive(), timeout_ms=5000)
