"""Independent runtime authorization; fake secure storage, no desktop capture."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.proactive import ProactiveScreenWatcher
from pet.screen_understanding.config import VisionConfigService
from pet.screen_understanding.models import VisionProfile
from tests.screen_fakes import MemoryVault


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(tmp_path)
    cfg.data["chat"]["enabled"] = False
    cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"]})
    assert cfg.save()
    service = VisionConfigService(cfg)
    yield cfg, service, app


def test_unconfirmed_automatic_never_starts_monitor(runtime):
    cfg, service, app = runtime
    watcher = ProactiveScreenWatcher(None, cfg)
    try:
        assert not watcher.is_running()
        with pytest.raises(ValueError, match="not_configured"):
            watcher._resolve_vision_provider({})
    finally:
        watcher.dispose()
        QCoreApplication.sendPostedEvents(watcher._bridge, QEvent.Type.DeferredDelete)


def test_automatic_resolution_is_independent_of_chat(runtime):
    cfg, service, app = runtime
    service.save_profile(
        VisionProfile("auto", "https://visual.example", "visual-model"), modes=["automatic"], secret="TEST-KEY", expected_revision=service.revision()
    )
    cfg.chat_settings = lambda: pytest.fail("chat resolver must not run")
    watcher = ProactiveScreenWatcher(None, cfg)
    try:
        request, prompt = watcher._resolve_vision_provider({"prefer_free_provider": False})
        assert request.model == "visual-model"
        assert request.api_key == "TEST-KEY"
        assert service.resolve("manual").reason == "not_configured"
    finally:
        watcher.dispose()
        QCoreApplication.sendPostedEvents(watcher._bridge, QEvent.Type.DeferredDelete)


def test_revision_changes_invalidate_inflight_generation(runtime):
    cfg, service, app = runtime
    service.save_profile(VisionProfile("auto", "https://visual.example", "v1"), modes=["automatic"], secret="TEST-KEY", expected_revision=service.revision())
    watcher = ProactiveScreenWatcher(None, cfg)
    try:
        old = watcher._generation
        service.save_profile(VisionProfile("auto", "https://visual.example", "v2"), modes=["automatic"], expected_revision=service.revision())
        watcher.apply_config()
        assert watcher._generation > old
    finally:
        watcher.dispose()
        QCoreApplication.sendPostedEvents(watcher._bridge, QEvent.Type.DeferredDelete)


def test_fresh_process_settings_manual_fallback_without_chat(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "tests.screen_runtime_probe", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=40,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"no_chat": true' in result.stdout
    assert '"stale_dropped": true' in result.stdout
