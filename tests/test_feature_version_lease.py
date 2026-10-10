"""Real process tests for Phase 4B-2 cross-process version leases."""

from __future__ import annotations

import hashlib
import multiprocessing
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet.feature_install_state import FEATURE_ID, FeatureInstallStateStore, StateChange, StateError
from pet.feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection, LeaseError

_RAW_MANIFEST = b"phase4b-2-manifest"
_DIGEST = hashlib.sha256(_RAW_MANIFEST).hexdigest()
_VERSION = "1.0.0"

# Windows spawn and durable lease I/O compete with all-core load. Signals
# still complete immediately; this bounded budget is not a fixed delay.
_PROCESS_TIMEOUT = 90.0


def _selection(data_root: Path, revision: int = 1) -> FeatureVersionSelection:
    root = Path(data_root) / "plugins" / FEATURE_ID / "versions" / _VERSION
    descriptor = SimpleNamespace(
        id=FEATURE_ID,
        version=_VERSION,
        trust_status="trusted_official",
        raw_manifest=_RAW_MANIFEST,
        root=root,
        execution_kind="host-worker",
    )
    return FeatureVersionSelection(FEATURE_ID, _VERSION, revision, _DIGEST, descriptor)


def _install(data_root: Path) -> FeatureInstallStateStore:
    store = FeatureInstallStateStore(data_root)
    store.commit(StateChange({_VERSION: _DIGEST}, active=_VERSION, enabled=True), expected_revision=0, operation_id="install")
    (store.root / "versions" / _VERSION).mkdir(parents=True)
    return store


def _wait(event, timeout: float = _PROCESS_TIMEOUT) -> None:
    assert event.wait(timeout), "bounded process coordination timed out"


def _hold_host(data_root: str, ready, release) -> None:
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(Path(data_root)))
    lease = coordinator.acquire_host(_selection(Path(data_root)))
    ready.set()
    _wait(release, timeout=2 * _PROCESS_TIMEOUT)
    lease.close()


def _crash_after_host(data_root: str, ready) -> None:
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(Path(data_root)))
    coordinator.acquire_host(_selection(Path(data_root)))
    ready.set()
    # Process exit closes the OS handle; the diagnostic record is intentionally
    # left for the next inspector to prove stale and clean.


def _crash_after_reservation(data_root: str, ready) -> None:
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(Path(data_root)))
    coordinator.reserve_worker(_selection(Path(data_root)))
    ready.set()
    # The parent process disappears before the one-time token is claimed.  An
    # inspector must not guess that the child can no longer be starting.


def _claim_worker(data_root: str, token: str, ready, release) -> None:
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(Path(data_root)))
    lease = coordinator.claim_worker(token)
    ready.set()
    _wait(release, timeout=2 * _PROCESS_TIMEOUT)
    lease.close()


def _wait_until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    wake = threading.Event()
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("bounded wait timed out")
        wake.wait(0.02)


def test_host_lease_blocks_removal_until_explicit_close(tmp_path):
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    lease = coordinator.acquire_host(_selection(tmp_path))
    try:
        occupancy = coordinator.inspect_occupancy(_VERSION, 1)
        assert occupancy.status == "occupied"
        assert not coordinator.can_remove(_VERSION, 1)
    finally:
        assert lease.close()
    assert coordinator.inspect_occupancy(_VERSION, 1).status == "free"


def test_revision_and_digest_mismatch_are_rejected(tmp_path):
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    with pytest.raises(StateError, match="revision_conflict"):
        coordinator.acquire_host(_selection(tmp_path, revision=2))

    store.commit(StateChange({_VERSION: "b" * 64}, active=_VERSION, enabled=True), expected_revision=1, operation_id="replace")
    with pytest.raises(LeaseError, match="digest_conflict"):
        coordinator.acquire_host(_selection(tmp_path, revision=2))


def test_old_selection_is_rejected_after_revision_changes(tmp_path):
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    selection = _selection(tmp_path)
    assert coordinator.verify_current_selection(selection)
    store.commit(
        StateChange({_VERSION: _DIGEST}, active=None, enabled=False),
        expected_revision=1,
        operation_id="disable",
    )
    assert not coordinator.is_current_selection(selection)
    with pytest.raises(LeaseError, match="feature_not_enabled"):
        coordinator.verify_current_selection(selection)


def test_orphan_lock_is_conservative_pending_confirmation(tmp_path):
    store = _install(tmp_path)
    leases = store.root / "leases"
    leases.mkdir()
    (leases / ("a" * 32 + ".lock")).write_bytes(b"")
    occupancy = FeatureVersionLeaseCoordinator(store).inspect_occupancy(_VERSION, 1)
    assert occupancy.status == "pending_confirmation"
    assert not occupancy.can_remove


def test_two_real_processes_share_one_version_occupancy(tmp_path):
    _install(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready_a, release_a = context.Event(), context.Event()
    ready_b, release_b = context.Event(), context.Event()
    first = context.Process(target=_hold_host, args=(str(tmp_path), ready_a, release_a))
    second = context.Process(target=_hold_host, args=(str(tmp_path), ready_b, release_b))
    first.start()
    try:
        _wait(ready_a)
        second.start()
        _wait(ready_b)
        coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(tmp_path))
        assert coordinator.inspect_occupancy(_VERSION, 1).status == "occupied"
    finally:
        release_a.set()
        release_b.set()
        first.join(_PROCESS_TIMEOUT)
        second.join(_PROCESS_TIMEOUT)
        assert first.exitcode == 0
        assert second.exitcode == 0
    _wait_until(lambda: FeatureVersionLeaseCoordinator(FeatureInstallStateStore(tmp_path)).can_remove(_VERSION, 1))


def test_process_exit_releases_kernel_lease_and_allows_record_cleanup(tmp_path):
    _install(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    child = context.Process(target=_crash_after_host, args=(str(tmp_path), ready))
    child.start()
    try:
        _wait(ready)
    finally:
        child.join(_PROCESS_TIMEOUT)
    assert child.exitcode == 0
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(tmp_path))
    _wait_until(lambda: coordinator.inspect_occupancy(_VERSION, 1).status == "free")
    assert not list((coordinator.leases_dir).glob("*.json"))


def test_unconfirmed_reservation_after_parent_exit_is_pending(tmp_path):
    _install(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    child = context.Process(target=_crash_after_reservation, args=(str(tmp_path), ready))
    child.start()
    try:
        _wait(ready)
    finally:
        child.join(_PROCESS_TIMEOUT)
    assert child.exitcode == 0
    coordinator = FeatureVersionLeaseCoordinator(FeatureInstallStateStore(tmp_path))
    occupancy = coordinator.inspect_occupancy(_VERSION, 1)
    assert occupancy.status == "pending_confirmation"
    assert occupancy.reason == "unconfirmed_reservation"
    assert not occupancy.can_remove


def test_core_settings_and_worker_leases_can_overlap(tmp_path):
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    host = coordinator.acquire_host(_selection(tmp_path))
    settings = coordinator.acquire_settings(_selection(tmp_path))
    reservation = coordinator.reserve_worker(_selection(tmp_path))
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_claim_worker, args=(str(tmp_path), reservation.handoff_token, ready, release))
    child.start()
    try:
        _wait(ready)
        assert reservation.claim_child(child_pid=child.pid)
        assert reservation.close()
        occupancy = coordinator.inspect_occupancy(_VERSION, 1)
        assert occupancy.status == "occupied"
        assert {item.kind for item in occupancy.leases} == {"host", "settings", "worker"}
    finally:
        release.set()
        child.join(_PROCESS_TIMEOUT)
        host.close()
        settings.close()
        assert child.exitcode == 0
    _wait_until(lambda: coordinator.inspect_occupancy(_VERSION, 1).status == "free")


def test_worker_reservation_requires_child_takeover_before_parent_release(tmp_path):
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    reservation = coordinator.reserve_worker(_selection(tmp_path))
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_claim_worker, args=(str(tmp_path), reservation.handoff_token, ready, release))
    child.start()
    try:
        _wait(ready)
        assert reservation.claim_child(child_pid=child.pid)
        occupancy = coordinator.inspect_occupancy(_VERSION, 1)
        assert occupancy.status == "occupied"
        # Releasing the parent reservation must not release the child lease.
        assert reservation.close()
        assert coordinator.inspect_occupancy(_VERSION, 1).status == "occupied"
    finally:
        release.set()
        child.join(_PROCESS_TIMEOUT)
        assert child.exitcode == 0
    _wait_until(lambda: coordinator.inspect_occupancy(_VERSION, 1).status == "free")
