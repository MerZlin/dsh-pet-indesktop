"""Host routes text; chat owns sessions, persistence and duplicate receipts."""

from __future__ import annotations

import pytest

from pet.plugins.services import ServiceRegistry, ServiceResult, ServiceRoute

SERVICE = "chat.external-turn/v1"
SCREEN = "official.screen-understanding"
CHAT = "official.ai-chat"


def packet(result_id="result-1"):
    return {"result_id": result_id, "kind": "manual", "user_text": "看看屏幕", "reply": "完整分析文字"}


def test_bound_service_checks_owner_route_and_revocation():
    router = ServiceRegistry()
    route = ServiceRoute("window-a", "slot-1", "shenshen")
    seen = []
    handle = router.register(CHAT, SERVICE, route, lambda request: seen.append(request) or ServiceResult("accepted"))
    client = router.bind(SCREEN, route, allowed={SERVICE}, authorized=lambda: True)
    assert client.call(SERVICE, packet()).status == "accepted"
    assert seen[0].owner == SCREEN and seen[0].route == route
    assert client.call("unknown", packet()).status == "denied"
    other = router.bind(SCREEN, ServiceRoute("window-b", "slot-2", "shenshen"), allowed={SERVICE}, authorized=lambda: True)
    assert other.call(SERVICE, packet()).status == "unavailable"
    handle.dispose()
    replacement = router.register(CHAT, SERVICE, route, lambda request: ServiceResult("busy"))
    handle.dispose()  # Old handle must not revoke a new registration.
    assert client.call(SERVICE, packet()).status == "busy"
    replacement.dispose()
    assert client.call(SERVICE, packet()).status == "unavailable"
    client.dispose()
    assert client.call(SERVICE, packet()).status == "denied"


def test_service_failures_and_invalid_payload_are_isolated():
    router = ServiceRegistry()
    route = ServiceRoute("window", "", "shenshen")
    enabled = [True]
    client = router.bind(SCREEN, route, allowed={SERVICE}, authorized=lambda: enabled[0])

    def fail(request):
        raise RuntimeError("private payload must not leak")

    router.register(CHAT, SERVICE, route, fail)
    assert client.call(SERVICE, packet()).status == "fault"
    assert client.call(SERVICE, {"bad": object()}).status == "invalid"
    assert client.call(SERVICE, {"bad": float("nan")}).status == "invalid"
    enabled[0] = False
    assert client.call(SERVICE, packet()).status == "denied"


@pytest.fixture
def chat_port(tmp_path):
    from pet.chat.external_turns import ExternalTurnReceiver
    from pet.chat.session_store import SessionStore, close_writer_for_root
    from pet.config import Config

    cfg = Config(tmp_path)
    cfg.set("chat", {**cfg.get("chat", {}), "enabled": True})
    route = ServiceRoute("window", str(cfg.instance_id or "primary"), "shenshen")
    router = ServiceRegistry()
    receiver = ExternalTurnReceiver(cfg, route, lambda: None)
    router.register(CHAT, SERVICE, route, receiver.receive)
    client = router.bind(SCREEN, route, allowed={SERVICE}, authorized=lambda: True)
    store = SessionStore(cfg.dir, cfg.instance_id)
    yield cfg, router, receiver, client, store
    assert store.flush()
    assert close_writer_for_root(store.root)


def test_closed_chat_persists_full_text_and_deduplicates_across_receivers(chat_port):
    from pet.chat.external_turns import ExternalTurnReceiver

    cfg, _, receiver, client, store = chat_port
    text = packet()
    text["reply"] = "完整文字" * 200
    assert client.call(SERVICE, text).status == "accepted"
    sessions = store.list("shenshen")
    assert len(sessions) == 1
    assert [m.content for m in sessions[0].messages] == [text["user_text"], text["reply"]]
    assert store.flush()
    # Recreate receiver/store: the receipt belongs to persisted chat messages,
    # not a Core cache or an in-memory screen request map.
    from pet.plugins.services import ServiceRequest

    fresh = ExternalTurnReceiver(cfg, receiver.route, lambda: None)
    assert fresh.receive(ServiceRequest(SCREEN, receiver.route, text)).status == "duplicate"
    assert len(store.list("shenshen")[0].messages) == 2
    assert client.call(SERVICE, packet("result-2")).status == "accepted"
    assert len(store.list("shenshen")[0].messages) == 4


def test_chat_disabled_invalid_or_stale_route_does_not_persist(chat_port):
    cfg, _, receiver, client, store = chat_port
    assert client.call(SERVICE, {**packet(), "api_key": "not-allowed"}).status == "invalid"
    cfg.set("chat", {"enabled": False})
    assert client.call(SERVICE, packet()).status == "unavailable"
    cfg.set("chat", {"enabled": True})
    cfg.set("character", "changed")
    assert client.call(SERVICE, packet()).status == "stale"
    assert store.list("shenshen") == []


def test_chat_window_busy_does_not_queue_and_visible_result_uses_chat_ui(chat_port):
    from PySide6.QtWidgets import QApplication

    from pet.chat.external_turns import ExternalTurnReceiver
    from pet.chat.widgets import ChatWindow
    from pet.plugins.services import ServiceRequest

    cfg, _, receiver, _, store = chat_port
    app = QApplication.instance() or QApplication([])
    window = ChatWindow(cfg, "shenshen")
    receive = ExternalTurnReceiver(cfg, receiver.route, lambda: window).receive
    request = ServiceRequest(SCREEN, receiver.route, packet())
    try:
        # Change only busy observation; use real chat/store/widgets.
        from unittest.mock import PropertyMock, patch

        with patch.object(type(window.service), "busy", new_callable=PropertyMock, return_value=True):
            assert receive(request).status == "busy"
        assert len(window.session.messages) == 0
        assert receive(request).status == "accepted"
        assert len(window.session.messages) == 2
        assert receive(request).status == "duplicate"
        assert len(window.session.messages) == 2
        assert store.load(window.session.session_id, "shenshen").messages[-1].content == packet()["reply"]
    finally:
        window.close()
        app.processEvents()


def test_core_binding_routes_to_chat_and_rejects_closed_source(tmp_path):
    from types import SimpleNamespace

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from pet.app import PetInstance
    from pet.chat.session_store import SessionStore, close_writer_for_root
    from pet.config import Config
    from pet.official_features import default_feature_host

    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    cfg.set("chat", {**cfg.get("chat", {}), "enabled": True})
    instance = PetInstance.__new__(PetInstance)
    instance.config = cfg
    instance.chat_window = None
    instance.shell = SimpleNamespace(enable_chat=True, feature_host=default_feature_host())
    win = QObject()
    instance.win = win
    instance._bind_optional_services(win)
    assert win.on_external_text("id-1", "manual", "question", "full answer").status == "accepted"
    assert win.on_external_text("id-1", "manual", "question", "full answer").status == "duplicate"
    win._closing = True
    assert win.on_external_text("id-2", "manual", "question", "late").status == "denied"
    store = SessionStore(cfg.dir, cfg.instance_id)
    assert [m.content for m in store.list("shenshen")[0].messages] == ["question", "full answer"]
    assert store.flush()
    assert close_writer_for_root(store.root)
    win.deleteLater()
    app.processEvents()


def test_core_binding_with_no_chat_does_not_import_chat(tmp_path):
    import os
    import subprocess
    import sys

    code = r"""
import importlib.abc, sys
class NoChat(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pet.chat' or fullname.startswith('pet.chat.'):
            raise AssertionError('optional chat imported: ' + fullname)
sys.meta_path.insert(0, NoChat())
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from types import SimpleNamespace
from pet.app import PetInstance
from pet.config import Config
app = QApplication([])
i = PetInstance.__new__(PetInstance)
i.config = Config(__import__('pathlib').Path(sys.argv[1]))
i.shell = SimpleNamespace(enable_chat=False)
i.win = QObject()
i._bind_optional_services(i.win)
assert i.win.on_external_text is None
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True, text=True, timeout=40
    )
    assert result.returncode == 0, result.stdout + result.stderr
