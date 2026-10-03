"""Real QProcess coverage for Phase 4B-2 Worker lease handoff."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from pet.feature_install_state import FEATURE_ID, FeatureInstallStateStore, StateChange
from pet.feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection
from pet.workers.launch import WorkerLaunch
from pet.workers.supervisor import WorkerSupervisor

_RAW_MANIFEST = b"phase4b-2-qprocess-manifest"
_DIGEST = hashlib.sha256(_RAW_MANIFEST).hexdigest()
_VERSION = "1.0.0"


@pytest.fixture()
def qt_app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms: int = 15000) -> None:
    if predicate():
        return
    loop = QEventLoop()
    tick = QTimer()
    tick.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)
    tick.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    tick.start()
    deadline.start(timeout_ms)
    loop.exec()
    tick.stop()
    deadline.stop()
    assert predicate(), "QProcess handoff did not reach the expected state"


def _install(data_root: Path) -> FeatureInstallStateStore:
    store = FeatureInstallStateStore(data_root)
    store.commit(
        StateChange({_VERSION: _DIGEST}, active=_VERSION, enabled=True),
        expected_revision=0,
        operation_id="install",
    )
    (store.root / "versions" / _VERSION).mkdir(parents=True)
    return store


def _selection(data_root: Path) -> FeatureVersionSelection:
    root = Path(data_root) / "plugins" / FEATURE_ID / "versions" / _VERSION
    descriptor = SimpleNamespace(
        id=FEATURE_ID,
        version=_VERSION,
        trust_status="trusted_official",
        raw_manifest=_RAW_MANIFEST,
        root=root,
    )
    return FeatureVersionSelection(FEATURE_ID, _VERSION, 1, _DIGEST, descriptor)


def _write_worker(path: Path, *, claim: bool) -> None:
    path.write_text(
        f"""import json, os, sys\nfrom pathlib import Path\n\nfrom pet.feature_version_lease import FeatureVersionLeaseCoordinator, retain_process_lease\n\nif {claim!r}:\n    coordinator = FeatureVersionLeaseCoordinator(Path(os.environ.pop('DSH_PET_FEATURE_LEASE_ROOT')))\n    lease = coordinator.claim_worker(os.environ.pop('DSH_PET_FEATURE_LEASE_HANDOFF_TOKEN'))\n    retain_process_lease(lease)\n\ndef send(kind, payload):\n    data = json.dumps(dict(protocol='pet-worker/v1', worker_id='qprocess-handoff',\n        type=kind, timestamp='2026-10-03T00:00:00Z', payload=payload), separators=(',', ':'))\n    sys.stdout.buffer.write(data.encode('utf-8') + bytes([10]))\n    sys.stdout.buffer.flush()\n\nsend('hello', {{'lease_claimed': {claim!r}, 'capabilities': []}})\nfor line in sys.stdin:\n    msg = json.loads(line)\n    if msg['type'] == 'config_push':\n        send('ready', {{'generation': msg['payload']['generation']}})\n    elif msg['type'] == 'shutdown':\n        break\n""",
        encoding="utf-8",
    )


def _launch_with_reservation(
    data_root: Path,
    script: Path,
    reservation,
) -> WorkerLaunch:
    environment = dict(os.environ)
    repository = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (repository, environment.get("PYTHONPATH", ""))))
    environment["DSH_PET_FEATURE_LEASE_ROOT"] = str(data_root)
    environment["DSH_PET_FEATURE_LEASE_HANDOFF_TOKEN"] = reservation.handoff_token

    def confirm(child_pid: int | None) -> bool:
        confirmed = reservation.claim_child(child_pid=child_pid)
        if confirmed:
            reservation.close()
        return confirmed

    def abort() -> None:
        if not reservation.closed:
            reservation.abort_after_confirmed_failure()

    return WorkerLaunch(
        sys.executable,
        (str(script),),
        str(script.parent),
        environment,
        abort,
        on_handoff_confirmed=confirm,
        on_handoff_abort=abort,
        handoff_token=reservation.handoff_token,
    )


def test_real_qprocess_worker_reservation_is_taken_over_before_parent_release(tmp_path, qt_app):
    del qt_app
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    reservation = coordinator.reserve_worker(_selection(tmp_path))
    script = tmp_path / "worker_claims_lease.py"
    _write_worker(script, claim=True)
    launch = _launch_with_reservation(tmp_path, script, reservation)
    supervisor = WorkerSupervisor(
        "qprocess-handoff",
        launch_factory=lambda: launch,
        max_restarts=0,
        restart_backoff_s=(0,),
    )
    try:
        assert supervisor.start()
        wait_until(lambda: supervisor.state == supervisor.READY)
        assert launch.handoff_confirmed
        assert reservation.closed
        occupancy = coordinator.inspect_occupancy(_VERSION, 1)
        assert occupancy.status == "occupied"
        assert {item.kind for item in occupancy.leases} == {"worker"}
    finally:
        supervisor.stop()
        wait_until(lambda: supervisor.state == supervisor.STOPPED)
    assert coordinator.inspect_occupancy(_VERSION, 1).status == "free"


def test_real_qprocess_without_lease_confirmation_aborts_parent_reservation(tmp_path, qt_app):
    del qt_app
    store = _install(tmp_path)
    coordinator = FeatureVersionLeaseCoordinator(store)
    reservation = coordinator.reserve_worker(_selection(tmp_path))
    script = tmp_path / "worker_without_lease.py"
    _write_worker(script, claim=False)
    launch = _launch_with_reservation(tmp_path, script, reservation)
    supervisor = WorkerSupervisor(
        "qprocess-handoff",
        launch_factory=lambda: launch,
        max_restarts=0,
        restart_backoff_s=(0,),
    )
    try:
        assert supervisor.start()
        wait_until(lambda: supervisor.state == supervisor.FAULT)
        assert not launch.handoff_confirmed
        assert reservation.closed
        assert coordinator.inspect_occupancy(_VERSION, 1).status == "free"
    finally:
        supervisor.stop()
        wait_until(lambda: supervisor.state == supervisor.STOPPED)
