"""Both real DLC ledgers are independent; Core removal is a final all-clear gate."""

from __future__ import annotations

import builtins
import hashlib
import shutil
from dataclasses import replace

import pytest

from pet.feature_install_state import StateChange
from pet.feature_package_transactions import FeaturePackageTransactionService
from pet.feature_version_lease import FeatureVersionSelection
from pet.official_features import AI_FEATURE_ID, OFFICIAL_FEATURES, SCREEN_FEATURE_ID
from tests.test_official_feature_contracts import signed_package, verifier


def installed_services(tmp_path):
    data = tmp_path / "generated-user-data"
    data.mkdir(parents=True)
    (data / "config.json").write_text('{"generated_personal_setting":true}')
    services = {}
    for owner in OFFICIAL_FEATURES:
        source = tmp_path / "generated-sources" / owner
        manifest, key = signed_package(source, owner)
        service = FeaturePackageTransactionService(data, verifier(key, owner))
        digest = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
        service.store.commit(StateChange({"1.0.0": digest}, "1.0.0", enabled=True), expected_revision=0, operation_id="generated-seed")
        shutil.copytree(source, service.versions_root / "1.0.0")
        services[owner] = service
    return data, services


def confirm(coordinator, prepared):
    return coordinator.apply(prepared.plan, confirmation_token=prepared.plan.confirmation_token)


def test_core_removal_requires_explicit_immutable_two_owner_confirmation(tmp_path):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    coordinator = CoreUninstallCoordinator(services)
    prepared = coordinator.prepare()
    assert prepared.status == "awaiting_confirmation" and prepared.evidence is None
    assert {entry.feature_id for entry in prepared.plan.packages} == set(OFFICIAL_FEATURES)
    assert "个人数据" in prepared.plan.summary
    assert coordinator.apply(prepared.plan).status == "awaiting_confirmation"
    forged = replace(prepared.plan, summary="forged confirmation")
    rejected = coordinator.apply(forged, confirmation_token=forged.confirmation_token)
    assert rejected.status == "rejected" and rejected.evidence is None
    assert all(service.store.read().state.enabled for service in services.values())


def test_core_removal_cleans_both_real_packages_without_loading_candidates(tmp_path, monkeypatch):
    from pet.core_uninstall import CoreUninstallCoordinator

    monkeypatch.delattr(builtins, "_phase5a_candidate_executed", raising=False)
    data, services = installed_services(tmp_path)
    coordinator = CoreUninstallCoordinator(services)
    result = confirm(coordinator, coordinator.prepare())
    assert result.status == "completed" and result.evidence is not None
    assert set(dict(result.evidence.revisions)) == set(OFFICIAL_FEATURES)
    assert (data / "config.json").read_text() == '{"generated_personal_setting":true}'
    assert not hasattr(builtins, "_phase5a_candidate_executed")
    for service in services.values():
        state = service.store.read().state
        assert not state.versions and not state.enabled and not state.pending_transaction
        assert not (service.versions_root / "1.0.0").exists()


def test_partial_uninstall_is_not_cross_package_rollback_and_retry_is_safe(tmp_path):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    occupied = services[AI_FEATURE_ID]
    pin = occupied.leases.acquire_host(FeatureVersionSelection.from_resolution(occupied.store.resolve_verified(occupied.verifier)))
    coordinator = CoreUninstallCoordinator(services)
    try:
        result = confirm(coordinator, coordinator.prepare())
        assert result.status == "awaiting_release" and result.evidence is None
        assert services[SCREEN_FEATURE_ID].store.read().status == "uninstalled"
        assert not occupied.store.read().state.enabled
        assert occupied.store.read().state.pending_transaction
        coordinator.close()
        assert not occupied.store.read().state.enabled
    finally:
        pin.close()
    resumed = CoreUninstallCoordinator(services).recover()
    assert resumed.status == "completed" and resumed.evidence is not None
    assert not occupied.store.read().state.pending_transaction
    assert (data / "config.json").exists()


def test_revision_change_rejects_before_any_package_is_accepted(tmp_path):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    coordinator = CoreUninstallCoordinator(services)
    prepared = coordinator.prepare()
    ai = services[AI_FEATURE_ID]
    ai.set_enabled(False, expected_revision=1)
    result = confirm(coordinator, prepared)
    assert result.status == "rejected" and result.reason == "revision_conflict"
    assert result.evidence is None
    assert services[SCREEN_FEATURE_ID].store.read().state.enabled
    assert all(service.store.read().state.versions for service in services.values())


@pytest.mark.parametrize("folder", ["versions", "staging", "leases"])
def test_unknown_residual_code_or_lease_evidence_blocks_core_deletion(tmp_path, folder):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    coordinator = CoreUninstallCoordinator(services)
    accepted = confirm(coordinator, coordinator.prepare())
    assert accepted.evidence is not None
    extra = services[AI_FEATURE_ID].store.root / folder / "unowned-generated-evidence"
    extra.parent.mkdir(exist_ok=True)
    extra.write_text("generated must not be automatically removed")
    result = CoreUninstallCoordinator(services).prepare()
    assert result.status == "recovery_required" and result.evidence is None
    assert extra.exists()


def test_missing_owner_or_mismatched_data_root_never_yields_removal_evidence(tmp_path):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    with pytest.raises(ValueError, match="official_owner_set_required"):
        CoreUninstallCoordinator({AI_FEATURE_ID: services[AI_FEATURE_ID]})
    other_data, other = installed_services(tmp_path / "other")
    with pytest.raises(ValueError, match="shared_data_root_required"):
        CoreUninstallCoordinator({AI_FEATURE_ID: services[AI_FEATURE_ID], SCREEN_FEATURE_ID: other[SCREEN_FEATURE_ID]})


def test_closed_or_forged_batch_never_authorizes_removal(tmp_path):
    from pet.core_uninstall import CoreUninstallCoordinator

    data, services = installed_services(tmp_path)
    first = CoreUninstallCoordinator(services)
    prepared = first.prepare()
    second = CoreUninstallCoordinator(services)
    assert second.apply(prepared.plan, confirmation_token=prepared.plan.confirmation_token).status == "rejected"
    first.close()
    assert first.apply(prepared.plan, confirmation_token=prepared.plan.confirmation_token).status == "rejected"
    assert all(service.store.read().state.enabled for service in services.values())
