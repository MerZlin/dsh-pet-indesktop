"""Runtime host uses snapshots and scoped data, never the legacy window/config."""

from dataclasses import FrozenInstanceError, replace

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import Config
from tests.screen_fakes import MemoryVault


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from features.screen_understanding.host.runtime import ProactiveScreenWatcher
    from pet.screen_understanding.host_binding import bind_runtime

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: MemoryVault())
    cfg = Config(tmp_path / "config.json")
    cfg.save()
    window = QWidget()
    window.mouse_through = False
    window._dragging = False
    bubbles, turns = [], []
    window.show_bubble = lambda text, **kw: bubbles.append(text)
    window.on_look_synced = lambda text, reply: turns.append((text, reply))
    context = bind_runtime(window, cfg)
    watcher = ProactiveScreenWatcher(context, worker_mode="in_process")
    yield app, cfg, window, context, watcher, bubbles, turns
    watcher.dispose()
    if shiboken6.isValid(watcher._bridge):
        QCoreApplication.sendPostedEvents(watcher._bridge, QEvent.Type.DeferredDelete)
    if shiboken6.isValid(window):
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_runtime_receives_only_scoped_ports(runtime):
    app, cfg, window, context, watcher, bubbles, turns = runtime
    for obj in (context, watcher, context.vision, context.preferences, context.memory, context.state_document):
        assert not any(hasattr(obj, key) for key in ("cfg", "win", "data", "path", "dir"))
    snapshot = context.window_state()
    assert not snapshot.visible
    with pytest.raises(FrozenInstanceError):
        snapshot.visible = True
    window.show()
    app.processEvents()
    window._dragging = True
    assert context.window_state().visible
    assert context.window_state().interacting
    assert not snapshot.interacting
    watcher._bridge.bubble_requested.emit("bounded", 1000)
    watcher._on_reply_synced("screen", "answer")
    assert bubbles == ["bounded"]
    assert turns == [("screen", "answer")]


def test_runtime_state_and_dry_run_keep_original_files(runtime):
    _, cfg, _, context, watcher, *_ = runtime
    watcher.limiter._clock = lambda: 10000.0
    assert watcher.limiter.consume_budget()
    original = (cfg.dir / "proactive_screen_state.json").read_bytes()
    watcher.limiter.update_config({"daily_cap": 3}, dry_run=True)
    assert watcher.limiter.consume_budget()
    assert (cfg.dir / "proactive_screen_dryrun_state.json").is_file()
    assert (cfg.dir / "proactive_screen_state.json").read_bytes() == original
    watcher.memory.record("editor.exe", "PRIVATE-TITLE", "coding")
    assert "PRIVATE-TITLE" not in (cfg.dir / "proactive_screen_memory.json").read_text(encoding="utf-8")
    context.memory.clear()
    assert watcher.memory.latest() is None
    assert (cfg.dir / "proactive_screen_state.json").read_bytes() == original


def test_revocation_and_window_deletion_stop_scoped_runtime(runtime):
    app, _, window, _, watcher, bubbles, turns = runtime
    from pet.feature_bindings import host_for
    from pet.official_features import SCREEN_OWNER

    watcher._timer.start()
    gen = watcher._generation
    host_for(window).disable(SCREEN_OWNER)
    assert not watcher.is_running()
    assert watcher._generation > gen
    watcher._bridge.bubble_requested.emit("stale", 1000)
    watcher._on_reply_synced("screen", "stale")
    assert bubbles == turns == []
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    assert watcher._disposed


def test_external_runtime_fault_never_enters_core_fallback(runtime, monkeypatch):
    from features.screen_understanding.host.runtime import ProactiveScreenWatcher

    _, _, _, context, _, *_ = runtime
    external = ProactiveScreenWatcher(replace(context, allow_in_process=False), worker_mode="auto")
    monkeypatch.setattr(external, "_on_tick_in_process", lambda *a: pytest.fail("external core fallback"))
    try:
        external._switch_to_in_process("test fault")
        assert not external._worker_fallback
        assert external._execution_fault
        assert not external.is_running()
        external._on_tick()
        replies = []
        assert external.request_manual_look(None, "", "pet", lambda *args: replies.append(args))
        assert replies and replies[0][2] is True
        assert not external._worker_adapter.active
    finally:
        external.dispose()


def test_legacy_worker_adapter_aliases_feature_implementation():
    import importlib

    canonical = importlib.import_module("features.screen_understanding.host.worker_adapter")
    legacy = importlib.import_module("pet.workers.proactive_screen_adapter")
    assert legacy is canonical
    assert legacy.ProactiveScreenWorkerAdapter.__module__ == canonical.__name__
