"""Production startup authorization and actual import/binding evidence."""

from __future__ import annotations

import pytest

from pet.feature_package_transactions import FeaturePackageTransactionService
from pet.feature_version_lease import FeatureVersionSelection, LeaseError
from tests.test_feature_package_transactions import _confirm, _installed, _package, _service


def test_pending_permit_allows_only_bound_nonexecuting_host_and_settings_leases(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    operation = _confirm(service, service.preflight_install(source))
    assert service.store.resolve_verified(verifier).descriptor is None
    assert service.store.resolve_verified(verifier, purpose="configuration").descriptor is None
    permit = service.startup_permit(operation.operation_id, role="core")
    resolution = service.store.resolve_verified(verifier, purpose="startup", permit=permit)
    assert resolution.status == "resolved"
    selection = FeatureVersionSelection.from_resolution(resolution)
    with service.leases.acquire_host(selection), service.leases.acquire_settings(selection):
        with pytest.raises(LeaseError, match="execution_purpose_required"):
            service.leases.reserve_worker(selection)
        with pytest.raises(LeaseError, match="execution_purpose_required"):
            service.leases.verify_current_selection(selection)
    assert service.store.read().state.pending_transaction == operation.operation_id


def test_forged_or_stale_startup_permit_never_resolves(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    operation = _confirm(service, service.preflight_install(source))
    assert service.store.resolve_verified(verifier, purpose="startup", permit={"success": True}).descriptor is None
    permit = service.startup_permit(operation.operation_id, role="settings")
    other = FeaturePackageTransactionService(service.store.root.parent.parent, verifier)
    assert other.confirm_startup(operation.operation_id, receipt={"permit": permit, "success": True}).reason == "startup_load_receipt_required"


def test_disabled_package_configuration_cannot_authorize_worker(tmp_path):
    service = _installed(tmp_path, enabled=False)
    assert service.store.resolve_verified(service.verifier).descriptor is None
    resolution = service.store.resolve_verified(service.verifier, purpose="configuration")
    assert resolution.status == "resolved"
    selection = FeatureVersionSelection.from_resolution(resolution)
    with service.leases.acquire_settings(selection):
        with pytest.raises(LeaseError, match="execution_purpose_required"):
            service.leases.reserve_worker(selection)


def test_actual_production_receipt_has_bound_ports_live_host_pin_and_no_worker(tmp_path):
    from tests.test_feature_package_transactions import _startup

    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    operation = _confirm(service, service.preflight_install(source))
    assert (
        service.confirm_startup(operation.operation_id, loaded_manifest_digest=operation.plan.manifest_digest, success=True).reason
        == "startup_load_receipt_required"
    )
    evidence = {}
    assert _startup(service, operation.operation_id, evidence=evidence).status == "completed"
    assert evidence["bound"] and evidence["process_pin_live"] and evidence["host_state"] == "enabled"
    assert evidence["worker_leases"] == 0


def test_rollback_waits_for_failed_import_then_requires_actual_previous_load(tmp_path):
    from tests.test_feature_package_transactions import _startup

    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    operation = _confirm(service, service.preflight_upgrade(source))
    failed = _startup(service, operation.operation_id, success=False, finish_rollback=False)
    assert failed.status == "awaiting_release" and failed.reason == "failed_host_requires_exit"
    assert service.store.resolve_verified(service.verifier).descriptor is None
    recovered = service.recover_pending()
    assert recovered.status == "awaiting_startup_confirmation"
    state = service.store.read().state
    assert state.active == "1.2.3" and not state.enabled and state.pending_transaction == operation.operation_id
    assert "1.2.4" not in state.versions
    assert _startup(service, operation.operation_id).reason == "rolled_back"
    assert service.store.resolve_verified(service.verifier).status == "resolved"


def test_rollback_load_failure_cannot_be_confirmed_or_resurrected(tmp_path):
    from tests.test_feature_package_transactions import _startup

    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    operation = _confirm(service, service.preflight_upgrade(source))
    assert _startup(service, operation.operation_id, success=False, finish_rollback=False).status == "awaiting_release"
    assert service.recover_pending().status == "awaiting_startup_confirmation"
    assert _startup(service, operation.operation_id, success=False, finish_rollback=False).status == "recovery_required"
    assert service.recover_pending().status == "recovery_required"
    state = service.store.read().state
    assert not state.enabled and state.pending_transaction == operation.operation_id
    assert service.store.resolve_verified(service.verifier).descriptor is None


@pytest.mark.parametrize("after_commit", [False, True])
def test_interrupted_rollback_replays_the_same_child_and_never_authorizes_execution(tmp_path, monkeypatch, after_commit):
    from tests.test_feature_package_transactions import _startup

    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    operation = _confirm(service, service.preflight_upgrade(source))
    assert _startup(service, operation.operation_id, success=False, finish_rollback=False).status == "awaiting_release"
    original = service.store.commit
    child_ids = []

    def interrupt(change, *, expected_revision, operation_id):
        if operation_id.endswith(".rollback"):
            child_ids.append(operation_id)
            if len(child_ids) == 1:
                if after_commit:
                    original(change, expected_revision=expected_revision, operation_id=operation_id)
                raise OSError("generated rollback interruption")
        return original(change, expected_revision=expected_revision, operation_id=operation_id)

    monkeypatch.setattr(service.store, "commit", interrupt)
    assert service.recover_pending().status == "recovery_required"
    assert service.store.resolve_verified(service.verifier).descriptor is None
    assert service.recover_pending().status == "awaiting_startup_confirmation"
    if not after_commit:
        assert child_ids[0] == child_ids[1]
    assert _startup(service, operation.operation_id).status == "completed"


def test_real_current_startup_and_stale_authority_fail_closed_without_reimport(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from pet.feature_probe_adapter import probe_policy

    service = _installed(tmp_path)
    policy = probe_policy(service.verifier)
    policy.pop("schema_version", None)
    policy["trust_anchors"] = {key: value.hex() for key, value in service.verifier.trust_anchors.items()}
    payload = {"data_root": str(service.leases.data_root), "policy": policy, "success": True, "mode": "current", "mutate_authority": True}
    child = subprocess.run(
        [sys.executable, "-m", "tests._feature_startup_child"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=45,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert child.returncode == 0, child.stderr
    evidence = json.loads(child.stdout)
    assert evidence["status"] == "completed"
    assert evidence["bound"] and evidence["process_pin_live"]
    assert evidence["stale_enabled"] is False
    assert evidence["host_state"] == "disabled" and evidence["worker_leases"] == 0


def test_production_startup_prepares_missing_owned_runtime_for_real_launch_factory(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from pet.feature_probe_adapter import probe_policy

    service = _installed(tmp_path)
    runtime = service.leases.data_root / "fixture-runtime"
    assert runtime.is_relative_to(tmp_path) and not list(runtime.iterdir())
    runtime.rmdir()
    policy = probe_policy(service.verifier)
    policy.pop("schema_version", None)
    policy["trust_anchors"] = {key: value.hex() for key, value in service.verifier.trust_anchors.items()}
    child = subprocess.run(
        [sys.executable, "-m", "tests._feature_startup_child"],
        input=json.dumps({"data_root": str(service.leases.data_root), "policy": policy, "success": True, "mode": "current", "launch": True}),
        text=True,
        capture_output=True,
        timeout=45,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert child.returncode == 0, child.stderr
    evidence = json.loads(child.stdout)
    assert evidence["status"] == "completed", evidence
    assert evidence["launch"]["runtime_exists"]
    assert evidence["launch"]["cwd"] == str(service.leases.data_root / "fixture-runtime")
    assert evidence["worker_leases"] == 0
