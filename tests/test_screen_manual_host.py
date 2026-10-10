"""Manual screen policy lives in the feature, not in a Qt window."""

from dataclasses import replace
from threading import Event

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import Config
from tests.screen_fakes import configure_vision


@pytest.fixture
def manual(tmp_path, monkeypatch):
    from features.screen_understanding.host.manual import ManualScreenHost
    from pet.screen_understanding.host_binding import bind_runtime

    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    configure_vision(cfg, monkeypatch)
    cfg.character_display_name = lambda _cid: "小鲸鱼"
    win = QWidget()
    bubbles, turns, requests, canceled = [], [], [], []
    win.show_bubble = lambda text, **kw: bubbles.append(text)
    win.on_look_synced = lambda *args: turns.append(args)
    context = bind_runtime(win, cfg)
    now = [10.0]

    def request(*args):
        requests.append(args)
        return True

    host = ManualScreenHost(context, request, canceled.append, clock=lambda: now[0])
    yield app, cfg, win, context, host, now, bubbles, turns, requests, canceled
    host.dispose()
    if shiboken6.isValid(win):
        win.deleteLater()
        QCoreApplication.sendPostedEvents(win, QEvent.Type.DeferredDelete)


def test_manual_host_owns_busy_cooldown_snapshot_and_optional_sync(manual):
    _, _, _, _, host, now, bubbles, turns, requests, _ = manual
    assert not any(hasattr(host, name) for name in ("cfg", "win", "window"))
    host.start()
    assert host.busy
    assert bubbles == ["让我看看…"]
    provider, prompt, pet_name, callback = requests[0]
    assert provider.api_key == "one-shot-secret"
    assert prompt == "look at the screen"
    assert pet_name == "小鲸鱼"
    host.start()
    assert bubbles[-1] == "上一张还没看完呢…"
    callback("analysis", "[看看屏幕]", False)
    callback("duplicate", "[看看屏幕]", False)
    assert turns == [("[看看屏幕]", "analysis")]
    assert not host.busy
    host.start()
    assert bubbles[-1] == "喘口气嘛，刚看过啦…"
    now[0] += 4.0
    host.start()
    assert len(requests) == 2


def test_revocation_cancels_only_owned_request_and_rejects_late_result(manual):
    from pet.feature_bindings import host_for
    from pet.official_features import SCREEN_OWNER

    _, _, win, _, host, _, bubbles, turns, requests, canceled = manual
    host.start()
    callback = requests[0][-1]
    host_for(win).disable(SCREEN_OWNER)
    assert canceled == [callback]
    assert not host.busy
    host_for(win).enable(SCREEN_OWNER)
    callback("stale", "question", False)
    assert turns == [] and bubbles == ["让我看看…"]


def test_profile_change_discards_result_and_sync_failure_keeps_bubble(manual):
    from features.screen_understanding.common.models import VisionProfile

    _, _, win, context, host, now, bubbles, _, requests, _ = manual
    host.start()
    context.vision.save_profile(
        VisionProfile("test", "https://visual.invalid", "changed"),
        modes=["manual"],
        expected_revision=context.vision.revision(),
    )
    requests[0][-1]("stale-config", "q", False)
    assert not host.busy
    assert "stale-config" not in bubbles
    now[0] += 4.0
    host.start()
    win.on_look_synced = lambda *a: (_ for _ in ()).throw(RuntimeError("receiver failed"))
    requests[1][-1]("visible", "q", False)
    assert bubbles[-1] == "visible"


def test_external_host_never_starts_in_process_fallback(manual, monkeypatch):
    from features.screen_understanding.host.manual import ManualScreenHost

    _, _, _, context, _, _, bubbles, _, _, _ = manual
    monkeypatch.setattr("threading.Thread", lambda **kw: pytest.fail("external fallback thread"))
    external = ManualScreenHost(replace(context, allow_in_process=False), lambda *a: False, lambda cb: None, clock=lambda: 10.0)
    try:
        external.start()
        assert not external.busy
        assert bubbles[-1].startswith("看不清啊…")
    finally:
        external.dispose()


def test_source_fallback_uses_real_thread_and_queued_gui_delivery(manual, monkeypatch):
    import time

    from features.screen_understanding.host.manual import ManualScreenHost
    from features.screen_understanding.worker import vision

    app, _, _, context, _, _, bubbles, turns, _, _ = manual
    called = Event()
    monkeypatch.setattr(vision, "capture_screen_bytes", lambda: b"deterministic-image")
    monkeypatch.setattr(vision, "foreground_app_info", lambda: "editor")

    def ask(image, app_info, prompt, provider, **kwargs):
        assert image == b"deterministic-image" and provider.api_key == "one-shot-secret"
        called.set()
        return "source-result"

    monkeypatch.setattr(vision, "ask_about_screen", ask)
    source = ManualScreenHost(context, lambda *a: False, lambda cb: None, clock=lambda: 10.0)
    try:
        source.start()
        deadline = time.monotonic() + 10.0
        while source.busy and time.monotonic() < deadline:
            app.processEvents()
            called.wait(0.01)
        assert called.is_set() and not source.busy
        assert bubbles[-1] == "source-result"
        assert turns[-1] == ("[看看屏幕] 前台窗口：editor", "source-result")
    finally:
        source.dispose()


def test_revocation_during_dispatch_does_not_enter_fallback(manual, monkeypatch):
    from features.screen_understanding.host.manual import ManualScreenHost
    from pet.feature_bindings import host_for
    from pet.official_features import SCREEN_OWNER

    _, _, win, context, _, _, _, _, _, _ = manual

    def request(*args):
        host_for(win).disable(SCREEN_OWNER)
        return False

    monkeypatch.setattr("threading.Thread", lambda **kw: pytest.fail("revoked fallback thread"))
    source = ManualScreenHost(context, request, lambda cb: None, clock=lambda: 10.0)
    try:
        source.start()
        assert not source.busy
    finally:
        source.dispose()


def test_credential_missing_message_explains_existing_config_needs_key(manual, monkeypatch):
    from types import SimpleNamespace

    _, _, _, context, host, _, bubbles, _, _, _ = manual
    monkeypatch.setattr(
        context.vision,
        "resolve",
        lambda mode: SimpleNamespace(ready=False, request=None, reason="credential_missing"),
    )
    host.start()
    assert bubbles[-1] == "请先在设置 → 自动化与联动 → 屏幕理解中填写并保存视觉 API Key；屏幕理解使用独立配置，不会读取 AI 对话 Key"
