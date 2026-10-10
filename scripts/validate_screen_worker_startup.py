"""Normal lease/HELLO/READY/natural-exit gate. No capture or Provider requests.

Runs the exact executable in an isolated generated data root. This gate is not
an LPAC package probe and never imports a factory or sends screenshot requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from features.screen_understanding.host.worker_diagnostics import safe_diagnostic
from pet.feature_install_state import FEATURE_ID, FeatureInstallStateStore, StateChange
from pet.feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection
from pet.workers.launch import WorkerLaunch, isolated_worker_environment
from pet.workers.supervisor import WorkerSupervisor


def _wait(predicate, timeout_ms):
    if predicate():
        return True
    loop, timer, deadline = QEventLoop(), QTimer(), QTimer()
    timer.setInterval(10)
    deadline.setSingleShot(True)
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout_ms)
    loop.exec()
    timer.stop()
    deadline.stop()
    return bool(predicate())


class _MeasuredSupervisor(WorkerSupervisor):
    def _handle_message(self, message):
        if message.type == "hello" and message.worker_id == self.worker_id:
            self.receipt["hello"] = True
            self.receipt["hello_ms"] = round((time.perf_counter() - self.started_at) * 1000, 3)
        super()._handle_message(message)


def validate_worker_startup(executable: Path, evidence: Path, *, arguments=(), source_root=None, timeout_ms=20000):
    executable = Path(executable).resolve(strict=True)
    app = QApplication.instance() or QApplication([])
    evidence = Path(evidence).absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    data_root = evidence / "data"
    version = "1.0.1"
    raw_manifest = json.dumps({"startup_gate_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}, sort_keys=True).encode()
    digest = hashlib.sha256(raw_manifest).hexdigest()
    store = FeatureInstallStateStore(data_root)
    store.commit(StateChange({version: digest}, active=version, enabled=True), expected_revision=0, operation_id="normal-startup-gate")
    installed = store.root / "versions" / version
    installed.mkdir(parents=True)
    descriptor = SimpleNamespace(
        id=FEATURE_ID, version=version, root=installed, raw_manifest=raw_manifest, trust_status="trusted_official", execution_kind="host-worker"
    )
    coordinator = FeatureVersionLeaseCoordinator(store)
    reservation = coordinator.reserve_worker(FeatureVersionSelection(FEATURE_ID, version, 1, digest, descriptor))
    environment = isolated_worker_environment(os.environ)
    if source_root is not None:
        environment["PYTHONPATH"] = str(Path(source_root).resolve(strict=True))
    environment["DSH_PET_FEATURE_LEASE_ROOT"] = str(data_root)
    environment["DSH_PET_FEATURE_LEASE_HANDOFF_TOKEN"] = reservation.handoff_token

    def abort():
        if not reservation.closed:
            reservation.abort_after_confirmed_failure()

    def confirm(pid):
        if reservation.claim_child(child_pid=pid):
            reservation.close()
            return True
        return False

    launch = WorkerLaunch(
        str(executable),
        tuple(arguments),
        str(executable.parent),
        environment,
        abort,
        on_handoff_confirmed=confirm,
        on_handoff_abort=abort,
        handoff_token=reservation.handoff_token,
    )
    supervisor = _MeasuredSupervisor(
        "proactive-screen", launch_factory=lambda: launch, handshake_timeout_ms=timeout_ms, shutdown_timeout_ms=5000, max_restarts=0
    )
    receipt = {
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "source_run": source_root is not None,
        "hello": False,
        "ready": False,
        "lease_claimed": False,
        "natural_exit": False,
        "exit_code": None,
        "screen_requests": 0,
        "network_requests": 0,
        "diagnostics": [],
    }
    supervisor.receipt, supervisor.started_at = receipt, time.perf_counter()
    forced = []

    def diagnostic(kind, detail):
        receipt["diagnostics"].append({"stage": kind, **safe_diagnostic(detail)})
        if kind in {"shutdown_timeout", "kill_timeout", "failure"}:
            forced.append(kind)

    supervisor.diagnostic.connect(diagnostic)
    try:
        if not supervisor.start():
            raise RuntimeError("worker_startup_failed")
        process = supervisor.process
        if process is None:
            raise RuntimeError("worker_startup_failed")
        process.finished.connect(lambda code, status: receipt.update(exit_code=code))
        if (
            not _wait(lambda: supervisor.state in {supervisor.READY, supervisor.FAULT, supervisor.CRASHED, supervisor.STOPPED}, timeout_ms + 3000)
            or supervisor.state != supervisor.READY
        ):
            raise RuntimeError("worker_startup_failed")
        receipt["ready"] = True
        receipt["ready_ms"] = round((time.perf_counter() - supervisor.started_at) * 1000, 3)
        receipt["lease_claimed"] = launch.handoff_confirmed
        occupancy = coordinator.inspect_occupancy(version, 1)
        if not receipt["hello"] or not launch.handoff_confirmed or occupancy.status != "occupied" or {item.kind for item in occupancy.leases} != {"worker"}:
            raise RuntimeError("worker_lease_handoff_failed")
        supervisor.stop()
        if not _wait(lambda: supervisor.state == supervisor.STOPPED, 15000):
            raise RuntimeError("worker_exit_failed")
        receipt["natural_exit"] = not forced and receipt["exit_code"] == 0
        if not receipt["natural_exit"]:
            raise RuntimeError("worker_exit_failed")
    finally:
        supervisor.stop()
        _wait(lambda: supervisor.state in {supervisor.STOPPED, supervisor.DISABLED}, 15000)
        abort()
        receipt["occupancy_after_exit"] = coordinator.inspect_occupancy(version, 1).status
        receipt["total_ms"] = round((time.perf_counter() - supervisor.started_at) * 1000, 3)
        (evidence / "startup.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        supervisor.deleteLater()
        app.processEvents()
    if receipt["occupancy_after_exit"] != "free":
        raise RuntimeError("worker_lease_release_failed")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = validate_worker_startup(args.executable, args.evidence)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
