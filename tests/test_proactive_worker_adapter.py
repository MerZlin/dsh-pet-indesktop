"""Transport policy tests; OS process behavior is exercised by lifecycle tests."""

import time
from unittest.mock import Mock

import pytest

from pet.workers.proactive_screen_adapter import ProactiveScreenWorkerAdapter
from pet.workers.protocol import build_message


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def adapter(qt_app):
    item = ProactiveScreenWorkerAdapter()
    item.supervisor = Mock(state="ready", generation=1)
    item.supervisor.send_request.side_effect = [f"request-{i}" for i in range(100)]
    yield item
    item.stop()


def test_provider_payload_contains_only_selected_vision_credentials():
    data = ProactiveScreenWorkerAdapter._provider_payload(
        {
            "base_url": "https://chat.invalid",
            "api_key": "CHAT-SECRET",
            "api_key_ref": "chat-ref",
            "vision_same_as_chat": False,
            "vision_base_url": "https://vision.invalid",
            "vision_api_key": "VISION-SECRET",
            "vision_api_key_ref": "vision-ref",
            "vision_model": "vision-model",
            "sessions": ["private-session"],
            "token": "unrelated-token",
        }
    )
    assert "CHAT-SECRET" not in str(data) and "private-session" not in str(data)
    assert "ref" not in str(data) and "unrelated-token" not in str(data)
    assert data["vision_api_key"] == "VISION-SECRET"


def test_orphan_budget_check_does_not_charge_core_quota(adapter):
    adapter._budget_checker = Mock(return_value=True)
    adapter._on_request(
        build_message(
            "proactive-screen",
            "request",
            {"operation": "budget_check", "generation": 7, "arguments": {"kind": "automatic", "parent_request_id": "missing"}},
            request_id="budget",
        )
    )
    adapter._budget_checker.assert_not_called()
    assert adapter.supervisor.send_response.call_args.args[2]["result"]["allowed"] is False


def test_response_operation_mismatch_cannot_route_as_manual(adapter):
    request_id = adapter.observe_foreground(7)
    got, errors = [], []
    adapter.manual_ready.connect(got.append)
    adapter.request_failed.connect(lambda *args: errors.append(args))
    adapter._on_response(
        build_message(
            "proactive-screen", "response", {"operation": "manual_look", "generation": 7, "status": "ok", "result": {"reply": "wrong"}}, request_id=request_id
        )
    )
    assert not got
    assert errors and errors[0][0] == "observe_foreground"


def test_crash_finishes_outstanding_requests_instead_of_leaving_core_busy(adapter):
    adapter.observe_foreground(7)
    errors = []
    adapter.request_failed.connect(lambda *args: errors.append(args))
    adapter._on_state_changed("crashed")
    assert not adapter._pending and errors


def test_request_deadline_is_bounded_and_cancelled(adapter, monkeypatch):
    adapter.observe_foreground(7)
    errors = []
    adapter.request_failed.connect(lambda *args: errors.append(args))
    future = time.monotonic() + 1000
    monkeypatch.setattr("pet.workers.proactive_screen_adapter.time.monotonic", lambda: future)
    adapter._expire_requests()
    assert not adapter._pending
    assert errors[0][1]["error_code"] == "request_timeout"


def test_independent_endpoint_preserves_derived_vision_model():
    from types import SimpleNamespace

    from pet.vision import resolve_vision_model

    provider = {"base_url": "https://chat.invalid", "model": "deepseek-v4-flash", "vision_same_as_chat": False, "vision_model": "", "vision_api_key": "vision"}
    payload = ProactiveScreenWorkerAdapter._provider_payload(provider)
    assert payload["vision_model"] == resolve_vision_model(SimpleNamespace(**provider))
