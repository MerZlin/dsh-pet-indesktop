"""Final frozen evidence is strict; a pending ledger is never a completed row."""

import pytest

from scripts.validate_feature_management_delivery import validate_evidence


def evidence(**state):
    return {
        "validation_only": True,
        "production_bootstrap": True,
        "error": None,
        "pid": 123,
        "elapsed_ms": 100,
        "rss": 10000,
        "threads": 2,
        "state": {"active": None, "previous": None, "enabled": False, "pending_transaction": None, "versions": {}, **state},
        "owners": [],
        "leases": [],
        "loaded_feature_modules": [],
    }


def test_pending_state_cannot_be_accepted_as_completed_installation():
    value = evidence(active="1.0.0", enabled=True, pending_transaction="tx-generated", versions={"1.0.0": "a" * 64})
    with pytest.raises(AssertionError, match="pending"):
        validate_evidence(value, active="1.0.0", enabled=True, pending=False)
    validate_evidence(value, active="1.0.0", enabled=True, pending=True)


def test_standalone_settings_needs_actual_component_and_settings_lease():
    value = evidence(active="1.0.0", enabled=True, versions={"1.0.0": "a" * 64})
    with pytest.raises(AssertionError, match="settings"):
        validate_evidence(value, active="1.0.0", enabled=True, pending=False, settings=True)
    value.update(settings_component=True, owners=["official.screen-understanding"], leases=[{"kind": "settings"}])
    validate_evidence(value, active="1.0.0", enabled=True, pending=False, settings=True)


def test_nonproduction_or_error_evidence_is_never_a_pass():
    value = evidence()
    value["production_bootstrap"] = False
    with pytest.raises(AssertionError, match="production"):
        validate_evidence(value, active=None, enabled=False, pending=False)


def test_validation_performance_observes_actual_call_without_changing_result(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("owned_validation_entry", Path(__file__).resolve().parents[1] / "packaging/phase4b_validation_entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ValidationPerformance = module.ValidationPerformance

    observer = ValidationPerformance()
    calls = []
    value = object()

    def operation():
        calls.append(True)
        (tmp_path / "owned.txt").write_bytes(b"generated data")
        return value

    assert observer.call("test", operation) is value
    assert calls == [True] and len(observer.phases) == 1
    phase = observer.phases[0]
    assert phase["name"] == "test" and phase["duration_ms"] > 0
    assert phase["rss_after"] > 0 and phase["threads_after"] > 0
    assert phase["error"] is None and phase["io"]["write_count"] >= 0


def test_validation_performance_does_not_hide_an_operation_failure():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("owned_validation_entry", Path(__file__).resolve().parents[1] / "packaging/phase4b_validation_entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ValidationPerformance = module.ValidationPerformance

    observer = ValidationPerformance()

    def operation():
        raise ValueError("generated failure")

    import pytest

    with pytest.raises(ValueError, match="generated failure"):
        observer.call("test", operation)
    assert observer.phases[0]["error"] == "ValueError"


@pytest.fixture
def validation_entry():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("owned_validation_entry", Path(__file__).resolve().parents[1] / "packaging/phase4b_validation_entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_reverse_budget_uses_actual_supervisor_routing_contract(validation_entry):
    from pet.workers.protocol import build_message

    calls = []

    class Supervisor:
        def send_response(self, request_id, operation, payload=None, *, generation=None):
            calls.append((request_id, operation, payload, generation))
            return True

    message = build_message(
        "proactive-screen",
        "request",
        {"operation": "budget_check", "generation": 1, "arguments": {"kind": "automatic", "parent_request_id": "own-analysis"}},
        request_id="own-budget",
    )
    assert validation_entry.reply_validation_budget(Supervisor(), message, {"own-analysis": "analyze_frame"})
    assert calls == [("own-budget", "budget_check", {"status": "ok", "result": {"allowed": True}}, 1)]


@pytest.mark.parametrize("change", ["unknown-operation", "old-generation", "wrong-parent", "manual", "wrong-worker"])
def test_validation_reverse_budget_rejects_unknown_or_stale_requests(validation_entry, change):
    from pet.workers.protocol import build_message

    payload = {"operation": "budget_check", "generation": 1, "arguments": {"kind": "automatic", "parent_request_id": "own-analysis"}}
    worker = "proactive-screen"
    if change == "unknown-operation":
        payload["operation"] = "check_manual_budget"
    elif change == "old-generation":
        payload["generation"] = 0
    elif change == "wrong-parent":
        payload["arguments"]["parent_request_id"] = "not-owned"
    elif change == "manual":
        payload["arguments"]["kind"] = "manual"
    else:
        worker = "not-owned"
    message = build_message(worker, "request", payload, request_id="own-budget")

    class Supervisor:
        def send_response(self, *args, **kwargs):
            raise AssertionError("invalid request must never receive approval")

    with pytest.raises(ValueError, match="validation budget"):
        validation_entry.reply_validation_budget(Supervisor(), message, {"own-analysis": "analyze_frame"})


@pytest.mark.parametrize(
    "status,reason,plan,attempts,expected",
    [
        ("failed", "management_lock_busy", object(), 0, True),
        ("failed", "management_lock_busy", object(), 3, False),
        ("failed", "permission_denied", object(), 0, False),
        ("failed", "management_lock_busy", None, 0, False),
        ("recovery_required", "management_lock_busy", object(), 0, False),
    ],
)
def test_validation_retry_is_bounded_to_explicit_retryable_confirmed_plan(validation_entry, status, reason, plan, attempts, expected):
    from types import SimpleNamespace

    result = SimpleNamespace(status=status, reason=reason, plan=plan)
    assert validation_entry.retryable_validation_result(result, attempts) is expected


def test_waiting_upgrade_keeps_enabled_intent_without_claiming_completed_load():
    value = evidence(active="1.0.0", enabled=True, pending_transaction="accepted-upgrade", versions={"1.0.0": "a" * 64})
    validate_evidence(value, active="1.0.0", enabled=True, pending=True)
    with pytest.raises(AssertionError, match="pending"):
        validate_evidence(value, active="1.0.0", enabled=True, pending=False)
