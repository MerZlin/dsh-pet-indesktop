"""Contract and failure tests for Phase 4B-3 local package transactions."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pet import feature_state_io as io
from pet.feature_install_state import FeatureInstallStateStore, StateChange
from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation
from pet.plugins.feature_host import FeatureDefinition
from pet.plugins.package_trust import FeaturePackageVerifier

_KEY = Ed25519PrivateKey.generate()


@dataclass
class StubChecker:
    calls: int = 0

    def check(self, descriptor):
        self.calls += 1
        return RuntimePreparation()


def _package(root: Path, *, version: str = "1.2.3", worker: bytes = b"not-executed", key=None):
    key = key or _KEY
    root.mkdir(parents=True)
    files: dict[str, dict[str, object]] = {}

    def add(path: str, data: bytes) -> None:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[path] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    add("host/__init__.py", b"")
    add(
        "host/factory.py",
        b"from pet.plugins.feature_host import FeatureDefinition\ndef create_host():\n    return FeatureDefinition('official.screen-understanding', (), lambda: None)\n",
    )
    add("worker/screen-worker.exe", worker)
    add("resources/defaults.json", b"{}")
    manifest = {
        "id": "official.screen-understanding",
        "version": version,
        "api_version": "1",
        "core_requires": ">=5.0.0,<6.0.0",
        "platforms": [sys.platform],
        "capabilities": ["screen.capture"],
        "factory": "screen-understanding/v1",
        "worker": {"path": "worker/screen-worker.exe", "args": ["--stdio"]},
        "files": files,
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sig").write_bytes(key.sign(raw))
    verifier = FeaturePackageVerifier(
        core_version="5.0.0",
        api_version="1",
        platform=sys.platform,
        allowed_capabilities={"screen.capture"},
        trust_anchors={"test": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
    )
    return root, verifier, manifest


def _service(tmp_path: Path, package_root: Path, verifier: FeaturePackageVerifier, checker: StubChecker | None = None):
    return FeaturePackageTransactionService(tmp_path / "data", verifier, self_checker=checker or StubChecker())


def _confirm(service, result):
    assert result.plan is not None
    return service.apply(result.plan, confirmation_token=result.plan.confirmation_token)


def _startup(service, operation_id, *, success=True, crash_step=None, finish_rollback=True, evidence=None):
    import subprocess

    from pet.feature_package_transactions import OperationResult
    from pet.feature_probe_adapter import probe_policy

    journal = service._load(operation_id)
    if journal["phase"] == "completed":
        return service.recover_pending()
    policy = probe_policy(service.verifier)
    # Test child receives public policy, never the signing private key.
    policy["trust_anchors"] = {key: value.hex() for key, value in service.verifier.trust_anchors.items()}
    policy.pop("schema_version", None)
    payload = {"data_root": str(service.leases.data_root), "operation_id": operation_id, "policy": policy, "success": success, "crash_step": crash_step}
    child = subprocess.run(
        [sys.executable, "-m", "tests._feature_startup_child"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=45,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert child.returncode == 0, child.stderr
    value = json.loads(child.stdout)
    if crash_step is not None:
        assert value["crash_fired"], "requested failure injection was not reached"
    if evidence is not None:
        evidence.update(value)
    result = OperationResult(**{key: value[key] for key in ("status", "operation_id", "phase", "revision", "reason")})
    if finish_rollback and not success and result.status == "awaiting_release":
        result = service.recover_pending()
        if result.status == "awaiting_startup_confirmation":
            result = _startup(service, operation_id)
    return result


def test_preflight_stages_without_execution_and_requires_confirmation(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    checker = StubChecker()
    service = _service(tmp_path, source, verifier, checker)
    result = service.preflight_install(source)
    assert result.status == "awaiting_confirmation"
    assert result.phase == "awaiting_confirmation"
    assert result.plan is not None
    assert checker.calls == 0
    assert service.store.read().status == "uninstalled"
    assert result.plan.staged_root is not None and result.plan.staged_root.exists()
    assert service.apply(result.plan).status == "awaiting_confirmation"
    assert service.apply(result.plan, confirmation_token="wrong").status == "rejected"


def test_install_commit_is_pending_until_startup_confirmation(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    checker = StubChecker()
    service = _service(tmp_path, source, verifier, checker)
    preflight = service.preflight_install(source)
    applied = _confirm(service, preflight)
    assert applied.status == "awaiting_startup_confirmation"
    assert checker.calls == 1
    state = service.store.read().state
    assert state is not None and state.pending_transaction == applied.operation_id and state.active == "1.2.3" and state.enabled
    completed = _startup(service, applied.operation_id or "")
    assert completed.status == "completed"
    state = service.store.read().state
    assert state is not None and state.pending_transaction is None and state.active == "1.2.3" and state.enabled
    assert _startup(service, applied.operation_id or "").status == "idempotent"


def test_same_digest_is_idempotent_and_does_not_enable_disabled_state(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    first = _confirm(service, service.preflight_install(source))
    assert _startup(service, first.operation_id or "").status == "completed"
    state = service.store.read().state
    assert state is not None
    service.store.commit(
        StateChange(dict(state.versions), state.active, state.previous, False, None), expected_revision=state.revision, operation_id="disable-for-test"
    )
    repeat = service.preflight_install(source)
    assert repeat.status == "idempotent"
    assert service.store.read().state.enabled is False


def test_same_version_different_digest_is_rejected(tmp_path):
    source, verifier, _ = _package(tmp_path / "source", version="1.2.3")
    service = _service(tmp_path, source, verifier)
    first = _confirm(service, service.preflight_install(source))
    _startup(service, first.operation_id or "")
    changed, _, _ = _package(tmp_path / "changed", version="1.2.3", worker=b"different signed worker")
    result = service.preflight_install(changed)
    assert result.status == "rejected"
    assert result.reason == "same_version_different_digest"


def test_source_change_after_preflight_is_rejected(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    (source / "resources/defaults.json").write_bytes(b"changed")
    applied = _confirm(service, result)
    assert applied.status in {"failed", "rejected"}
    assert applied.reason == "source_changed"


def test_revision_change_cannot_be_overwritten(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    service.store.commit(StateChange({}, None, None, False, None), expected_revision=result.plan.revision, operation_id="other-operation")
    applied = _confirm(service, result)
    assert applied.status == "rejected"
    assert applied.reason == "revision_conflict"


def test_management_lock_competition_is_explicit(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    with io.open_kernel_lock(service.management_lock_path):
        applied = _confirm(service, result)
    assert applied.status == "failed"
    assert applied.reason == "management_lock_busy"


def test_zip_traversal_and_duplicate_entries_are_rejected(tmp_path):
    import zipfile

    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape.txt", b"x")
    verifier = FeaturePackageVerifier(core_version="5.0.0", api_version="1", platform=sys.platform, allowed_capabilities={"screen.capture"})
    service = FeaturePackageTransactionService(tmp_path / "data", verifier, self_checker=StubChecker())
    result = service.preflight_install(source)
    assert result.status == "rejected"
    assert result.reason == "unsafe_archive_path"


def test_uninstall_waits_for_real_version_lease(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    installed = _confirm(service, service.preflight_install(source))
    _startup(service, installed.operation_id or "")
    resolution = service.store.resolve_verified(verifier)
    from pet.feature_version_lease import FeatureVersionSelection

    selection = FeatureVersionSelection.from_resolution(resolution)
    lease = service.leases.acquire_host(selection)
    try:
        uninstall = service.preflight_uninstall()
        blocked = _confirm(service, uninstall)
        assert blocked.status == "awaiting_release"
        assert blocked.blocked_versions == ("1.2.3",)
    finally:
        lease.close()
    resumed = service.apply(uninstall.plan, confirmation_token=uninstall.plan.confirmation_token)
    assert resumed.status == "completed"
    assert service.store.read().status == "uninstalled"
    assert not (service.versions_root / "1.2.3").exists()


def test_fresh_preflight_creates_proven_empty_ledger_before_staging(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    assert result.status == "awaiting_confirmation"
    assert service.store.read().status == "uninstalled"
    assert service.store.state_path.is_file()
    assert service.store.read().state.pending_transaction is None


def test_confirmation_plan_cannot_be_mutated_or_substituted(tmp_path):
    from dataclasses import replace

    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    assert result.plan is not None
    with pytest.raises(TypeError):
        result.plan.versions["99.0.0"] = "0" * 64
    forged = replace(result.plan, target_version="99.0.0")
    assert service.apply(forged, confirmation_token=forged.confirmation_token).status == "rejected"


def test_unrelated_new_revision_never_resumes_accepted_transaction(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source)
    accepted = _confirm(service, result)
    assert accepted.status == "awaiting_startup_confirmation"
    state = service.store.read().state
    service.store.commit(
        StateChange(dict(state.versions), state.active, state.previous, False, state.pending_transaction),
        expected_revision=state.revision,
        operation_id="external-higher-revision",
    )
    assert _startup(service, result.operation_id).status == "recovery_required"
    assert service.store.read().state.enabled is False


def test_access_time_changes_are_not_source_content_changes(tmp_path, monkeypatch):
    import os

    from pet.feature_package_files import source_fingerprint

    source, verifier, _ = _package(tmp_path / "source")
    original = Path.open

    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path.is_file() and args and args[0] == "rb":
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns + 1_000_000_000, info.st_mtime_ns))
        return stream

    monkeypatch.setattr(Path, "open", opened)
    source_fingerprint(source, verifier.limits)


def _installed(tmp_path, *, version="1.2.3", enabled=True):
    source, verifier, _ = _package(tmp_path / ("source-" + version), version=version)
    service = _service(tmp_path, source, verifier)
    result = _confirm(service, service.preflight_install(source))
    assert _startup(service, result.operation_id).status == "completed"
    if not enabled:
        state = service.store.read().state
        service.store.commit(
            StateChange(dict(state.versions), state.active, state.previous, False), expected_revision=state.revision, operation_id="disabled-fixture"
        )
    return service


@pytest.mark.parametrize("enabled", [True, False])
def test_upgrade_retains_enabled_previous_and_defers_gc(tmp_path, enabled):
    service = _installed(tmp_path, enabled=enabled)
    for version in ("1.2.4", "1.2.5"):
        source, _, _ = _package(tmp_path / version, version=version)
        result = _confirm(service, service.preflight_upgrade(source))
        assert result.status == "awaiting_startup_confirmation"
        assert service.store.resolve_verified(service.verifier).descriptor is None
        assert _startup(service, result.operation_id).status == "completed"
    state = service.store.read().state
    assert state.active == "1.2.5" and state.previous == "1.2.4" and state.enabled is enabled
    assert set(state.versions) == {"1.2.4", "1.2.5"}
    assert (service.versions_root / "1.2.3").exists()
    assert service.collect_garbage().status == "completed"
    assert not (service.versions_root / "1.2.3").exists()


def test_upgrade_real_host_lease_blocks_switch_then_recovers(tmp_path):
    from pet.feature_version_lease import FeatureVersionSelection

    service = _installed(tmp_path)
    selection = FeatureVersionSelection.from_resolution(service.store.resolve_verified(service.verifier))
    lease = service.leases.acquire_host(selection)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    plan = service.preflight_upgrade(source)
    try:
        blocked = _confirm(service, plan)
        assert blocked.status == "awaiting_release"
        assert service.store.read().state.active == "1.2.3"
        assert service.store.resolve_verified(service.verifier).descriptor is None
    finally:
        lease.close()
    resumed = FeaturePackageTransactionService(service.store.root.parent.parent, service.verifier, self_checker=StubChecker()).recover_pending()
    assert resumed.status == "awaiting_startup_confirmation"


def test_startup_failure_rolls_back_old_active_not_old_previous(tmp_path):
    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    applied = _confirm(service, service.preflight_upgrade(source))
    result = _startup(service, applied.operation_id, success=False)
    assert result.status == "completed" and result.reason == "rolled_back"
    state = service.store.read().state
    assert state.active == "1.2.3" and state.enabled and state.pending_transaction is None
    assert "1.2.4" not in state.versions
    assert service.collect_garbage().status == "completed"


def test_missing_previous_never_reenables_failed_candidate(tmp_path):
    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    applied = _confirm(service, service.preflight_upgrade(source))
    (service.versions_root / "1.2.3" / "manifest.sig").unlink()
    result = _startup(service, applied.operation_id, success=False)
    assert result.status == "recovery_required"
    state = service.store.read().state
    assert not state.enabled and state.pending_transaction == applied.operation_id
    assert service.store.resolve_verified(service.verifier).descriptor is None


def test_uninstall_drafts_block_without_saving_or_discarding(tmp_path):
    class DraftRuntime:
        def prepare(self, request):
            return RuntimePreparation("draft_blocked", "unsaved_settings", details={"drafts": ["vision"]})

    service = _installed(tmp_path)
    service.runtime = DraftRuntime()
    state = service.store.read().state
    result = _confirm(service, service.preflight_uninstall())
    assert result.status == "awaiting_confirmation"
    assert service.store.read().state == state


def test_partial_uninstall_failure_is_disabled_and_restart_continues(tmp_path, monkeypatch):
    from pet import feature_package_files

    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    applied = _confirm(service, service.preflight_upgrade(source))
    _startup(service, applied.operation_id)
    marker = service.store.root.parent.parent / "profile-preserved.json"
    marker.write_text("user-secret-profile")
    original = feature_package_files.remove_owned_tree
    calls = 0

    def fail_second(path, parent, limits):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError(13, "test permission")
        return original(path, parent, limits)

    with monkeypatch.context() as patch:
        patch.setattr(feature_package_files, "remove_owned_tree", fail_second)
        result = _confirm(service, service.preflight_uninstall())
    assert result.status == "recovery_required" and result.reason == "permission_denied"
    assert not service.store.read().state.enabled
    assert service.store.read().state.pending_transaction
    assert service.recover_pending().status == "completed"
    assert service.store.read().status == "uninstalled"
    assert marker.read_text() == "user-secret-profile"
    assert service.recover_pending().status == "idempotent"


def test_uninstall_removes_previously_deferred_gc_versions(tmp_path):
    service = _installed(tmp_path)
    for version in ("1.2.4", "1.2.5"):
        source, _, _ = _package(tmp_path / version, version=version)
        applied = _confirm(service, service.preflight_upgrade(source))
        _startup(service, applied.operation_id)
    assert _confirm(service, service.preflight_uninstall()).status == "completed"
    assert not list(service.versions_root.iterdir())


def test_orphan_version_is_not_adopted_by_install(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    plan = service.preflight_install(source)
    import shutil

    orphan = service.versions_root / "1.2.3"
    orphan.parent.mkdir(parents=True)
    shutil.copytree(source, orphan)
    applied = _confirm(service, plan)
    assert applied.status in {"rejected", "recovery_required"}
    assert applied.reason == "version_directory_exists"
    assert service.store.read().state.active is None


@pytest.mark.parametrize(
    "members, reason",
    [
        (["/absolute", "safe"], "unsafe_archive_path"),
        (["C:/drive"], "unsafe_archive_path"),
        (["a/../escape"], "unsafe_archive_path"),
        (["same", "same"], "duplicate_archive_entry"),
        (["Host/a", "host/b"], "case_collision"),
        (["safe", "safe/child"], "case_collision"),
    ],
)
def test_zip_member_boundaries(tmp_path, members, reason):
    import zipfile

    source, verifier, _ = _package(tmp_path / "source")
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for name in members:
            stream.writestr(name, b"x")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(archive)
    assert result.status == "rejected" and result.reason == reason


def test_zip_install_and_source_fingerprint_change(tmp_path):
    import zipfile

    source, verifier, _ = _package(tmp_path / "source")
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.write(source / "host", "host/")
        for path in source.rglob("*"):
            if path.is_file():
                stream.write(path, path.relative_to(source).as_posix())
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(archive)
    assert result.status == "awaiting_confirmation"
    assert _confirm(service, result).status == "awaiting_startup_confirmation"


def test_no_startup_success_without_real_load_receipt(tmp_path):
    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    applied = _confirm(service, service.preflight_upgrade(source))
    assert service.confirm_startup(applied.operation_id).reason == "startup_load_receipt_required"


def test_default_selfcheck_fails_closed_without_os_sandbox(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = FeaturePackageTransactionService(tmp_path / "data", verifier)
    result = _confirm(service, service.preflight_install(source))
    assert result.status == "rejected" and result.reason == "self_check_sandbox_unavailable"
    state = service.store.read().state
    assert state.active is None and not state.enabled and state.pending_transaction is None


def test_selfcheck_does_not_accept_host_only_or_unisolated_worker(tmp_path):
    from pet.feature_package_probe import ProbeOutcome, SubprocessFeatureSelfChecker

    source, verifier, _ = _package(tmp_path / "source")
    descriptor = verifier.verify(source)

    class FakeSandbox:
        def run(self, request):
            assert request.deny_user_data and request.deny_network and request.deny_desktop
            return ProbeOutcome(True, False, True, True)

    assert SubprocessFeatureSelfChecker(verifier, sandbox=FakeSandbox()).check(descriptor).status == "failed"

    class Unisolated:
        def run(self, request):
            return ProbeOutcome(True, True, True, False)

    assert SubprocessFeatureSelfChecker(verifier, sandbox=Unisolated()).check(descriptor).reason == "self_check_isolation_not_enforced"


def test_worker_probe_requires_real_protocol_hello_and_clean_exit():
    from pet.feature_package_probe import validate_worker_transcript
    from pet.workers.protocol import build_message, encode_message

    hello = encode_message(build_message("proactive-screen", "hello", {"pid": 12}))
    assert validate_worker_transcript(hello, graceful_returncode=0)
    assert not validate_worker_transcript(b"", graceful_returncode=0)
    assert not validate_worker_transcript(hello, graceful_returncode=1)
    assert not validate_worker_transcript(hello + encode_message(build_message("proactive-screen", "request", {}, request_id="model")), graceful_returncode=0)


@pytest.mark.parametrize("fault", ["pending", "activate", "confirmed", "uninstalled"])
def test_state_commit_ack_crash_recovery_is_idempotent(tmp_path, monkeypatch, fault):
    service = _installed(tmp_path)
    original = service.store.commit
    fired = False

    def crash_after(change, *, expected_revision, operation_id):
        nonlocal fired
        result = original(change, expected_revision=expected_revision, operation_id=operation_id)
        if operation_id.endswith("." + fault) and not fired:
            fired = True
            raise OSError("injected after state commit")
        return result

    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    plan = service.preflight_uninstall() if fault == "uninstalled" else service.preflight_upgrade(source)
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "commit", crash_after)
        applied = _confirm(service, plan)
        if fault == "confirmed":
            assert applied.status == "awaiting_startup_confirmation"
            _startup(service, applied.operation_id, crash_step="confirmed")
            # The actual confirmation/commit runs in its own imported host
            # process, so that child's test-only fault injector is used here.
            fired = True
    assert fired
    recovered = service.recover_pending()
    assert recovered.status == ("completed" if fault in {"confirmed", "uninstalled"} else "awaiting_startup_confirmation")
    if fault in {"pending", "activate"}:
        assert _startup(service, plan.operation_id).status == "completed"
    assert service.recover_pending().status == "idempotent"
    assert service.store.read().state.pending_transaction is None


def test_rename_ack_crash_recovers_only_owned_verified_candidate(tmp_path, monkeypatch):
    import os

    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    original = os.rename
    fired = False

    def rename_then_crash(source, target):
        nonlocal fired
        original(source, target)
        fired = True
        raise OSError("injected after rename")

    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", rename_then_crash)
        applied = _confirm(service, service.preflight_upgrade(source))
    assert fired and applied.status == "recovery_required"
    assert service.recover_pending().status == "awaiting_startup_confirmation"
    assert _startup(service, applied.operation_id).status == "completed"


@pytest.mark.parametrize("fault", ["signature", "unsigned", "payload", "unlisted", "platform", "capability", "api", "core"])
def test_failed_verification_never_executes_checker(tmp_path, fault):
    source, verifier, manifest = _package(tmp_path / "source")
    checker = StubChecker()
    if fault == "signature":
        (source / "manifest.sig").write_bytes(b"0" * 64)
    elif fault == "unsigned":
        (source / "manifest.sig").unlink()
    elif fault == "payload":
        (source / "host/factory.py").write_bytes(b"untrusted factory")
    elif fault == "unlisted":
        (source / "surprise.py").write_bytes(b"untrusted")
    else:
        key, value = {
            "platform": ("platforms", ["darwin" if sys.platform != "darwin" else "win32"]),
            "capability": ("capabilities", ["credentials.read"]),
            "api": ("api_version", "2"),
            "core": ("core_requires", ">=99.0.0,<100.0.0"),
        }[fault]
        manifest[key] = value
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        (source / "manifest.json").write_bytes(raw)
        (source / "manifest.sig").write_bytes(_KEY.sign(raw))
    service = _service(tmp_path, source, verifier, checker)
    assert service.preflight_install(source).status == "rejected"
    assert checker.calls == 0
    assert service.store.read().state.active is None


def test_hardlinked_source_is_rejected(tmp_path):
    import os

    source, verifier, _ = _package(tmp_path / "source")
    os.link(source / "resources/defaults.json", tmp_path / "shared-file")
    assert _service(tmp_path, source, verifier).preflight_install(source).status == "rejected"


def test_zip_symbolic_member_rejected_before_extraction(tmp_path):
    import stat
    import zipfile

    source, verifier, _ = _package(tmp_path / "source")
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        member = zipfile.ZipInfo("link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(member, "../outside")
    assert _service(tmp_path, source, verifier).preflight_install(archive).reason == "unsafe_archive_member"


def test_gc_old_journal_never_removes_reinstalled_active(tmp_path):
    service = _installed(tmp_path)
    for version in ("1.2.4", "1.2.5"):
        source, _, _ = _package(tmp_path / version, version=version)
        applied = _confirm(service, service.preflight_upgrade(source))
        _startup(service, applied.operation_id)
    _confirm(service, service.preflight_uninstall())
    first_source = tmp_path / "source-1.2.3"
    installed = _confirm(service, service.preflight_install(first_source))
    _startup(service, installed.operation_id)
    assert service.collect_garbage().status == "completed"
    assert (service.versions_root / "1.2.3" / "manifest.json").is_file()
    assert service.store.read().state.active == "1.2.3"


def test_zip_entry_limit_checked_before_central_directory_parser(tmp_path, monkeypatch):
    import zipfile
    from dataclasses import replace

    from pet import feature_package_files

    source, verifier, _ = _package(tmp_path / "source")
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for i in range(3):
            stream.writestr(str(i), b"x")
    limits = replace(verifier.limits, max_entries=2)
    called = False
    original = zipfile.ZipFile

    def observed(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", observed)
    with pytest.raises(io.StateError, match="entry_limit"):
        feature_package_files.stage(archive, "zip", tmp_path / "stage", limits)
    assert not called


def test_journal_failure_never_escapes_as_false_success(tmp_path, monkeypatch):
    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "upgrade", version="1.2.4")
    plan = service.preflight_upgrade(source)
    original = service._save

    def failed_save(journal):
        if journal.get("accepted"):
            raise PermissionError(13, "journal unwritable")
        return original(journal)

    with monkeypatch.context() as patch:
        patch.setattr(service, "_save", failed_save)
        result = _confirm(service, plan)
    assert result.status == "recovery_required" and result.reason == "permission_denied"
    assert service.store.read().state.active == "1.2.3"


def test_failed_candidate_self_check_does_not_prepare_or_disable_working_runtime(tmp_path):
    service = _installed(tmp_path)
    before = service.store.read().state.document()
    calls = []

    class Runtime:
        def prepare(self, *args):
            calls.append("prepare")
            return RuntimePreparation()

    class BadChecker:
        def check(self, descriptor):
            calls.append("check")
            return RuntimePreparation("failed", "candidate_probe_failed")

    service.runtime, service.self_checker = Runtime(), BadChecker()
    source, _, _ = _package(tmp_path / "bad-upgrade", version="1.2.4")
    result = _confirm(service, service.preflight_upgrade(source))
    assert result.status == "rejected" and result.reason == "candidate_probe_failed"
    assert calls == ["check"] and service.store.read().state.document() == before
    assert not service._load(result.operation_id)["accepted"]


def test_candidate_probe_precedes_lifecycle_effects(tmp_path):
    service = _installed(tmp_path)
    calls = []

    class Runtime:
        def prepare(self, *args):
            calls.append("prepare")
            return RuntimePreparation()

    class Checker:
        def check(self, descriptor):
            calls.append("check")
            return RuntimePreparation()

    service.runtime, service.self_checker = Runtime(), Checker()
    source, _, _ = _package(tmp_path / "upgrade-order", version="1.2.4")
    assert _confirm(service, service.preflight_upgrade(source)).status == "awaiting_startup_confirmation"
    assert calls[0] == "check" and calls.count("check") == 1


@pytest.mark.parametrize("kind", ["upgrade", "uninstall"])
def test_accepted_journal_before_first_pending_is_recovered_without_reviving(tmp_path, monkeypatch, kind):
    service = _installed(tmp_path)
    source, _, _ = _package(tmp_path / "accept-gap", version="1.2.4")
    plan = service.preflight_uninstall() if kind == "uninstall" else service.preflight_upgrade(source)
    save = service._save
    fired = False

    def crash(journal):
        nonlocal fired
        save(journal)
        if journal["accepted"] and journal["intent"] is None and journal["expected_state"]["pending_transaction"] is None and not fired:
            fired = True
            raise OSError("owned failure after acceptance journal")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_save", crash)
        result = _confirm(service, plan)
    assert fired and result.status == "recovery_required"
    assert service.store.read().state.pending_transaction is None
    recovered = service.recover_pending()
    assert recovered.status == ("completed" if kind == "uninstall" else "awaiting_startup_confirmation")
    if kind == "uninstall":
        assert not service.store.read().state.enabled and not service.store.read().state.versions


def test_conflicting_accepted_intents_disable_execution_instead_of_choosing_newest(tmp_path):
    service = _installed(tmp_path)
    first = service.preflight_uninstall()
    second = service.preflight_uninstall()
    for operation in (first, second):
        journal = service._load(operation.operation_id)
        journal["accepted"] = True
        service._save(journal)
    result = service.recover_pending()
    assert result.status == "recovery_required" and result.reason == "accepted_intent_conflict"
    assert not service.store.read().state.enabled
    assert service.store.resolve_verified(service.verifier).descriptor is None


def test_acceptance_journal_is_written_under_management_lock(tmp_path, monkeypatch):
    from pet.feature_state_io import StateError, open_kernel_lock

    service = _installed(tmp_path)
    plan = service.preflight_uninstall()
    save = service._save
    observed = []

    def verify_lock(journal):
        if journal["accepted"] and journal["expected_state"]["pending_transaction"] is None:
            try:
                with open_kernel_lock(service.management_lock_path):
                    observed.append("unlocked")
            except StateError as exc:
                assert exc.code == "lock_busy"
                observed.append("locked")
        return save(journal)

    monkeypatch.setattr(service, "_save", verify_lock)
    assert _confirm(service, plan).status == "completed"
    assert observed and set(observed) == {"locked"}


def test_set_enabled_is_revision_bound_and_does_not_import_or_probe(tmp_path):
    service = _installed(tmp_path)
    revision = service.store.read().state.revision
    assert service.set_enabled(False, expected_revision=revision).status == "completed"
    state = service.store.read().state
    assert not state.enabled and state.revision == revision + 1
    assert service.set_enabled(True, expected_revision=revision).reason == "revision_conflict"
    assert service.set_enabled(False, expected_revision=state.revision).status == "idempotent"
    assert service.set_enabled(True, expected_revision=state.revision).status == "completed"


def test_set_enabled_cannot_revive_accepted_uninstall_gap(tmp_path):
    service = _installed(tmp_path)
    preflight = service.preflight_uninstall()
    journal = service._load(preflight.operation_id)
    journal["accepted"] = True
    service._save(journal)
    state = service.store.read().state
    assert service.set_enabled(True, expected_revision=state.revision).reason == "accepted_transaction_pending"


def test_cancel_only_cleans_own_unaccepted_staging_and_invalidates_plan(tmp_path):
    source, verifier, _ = _package(tmp_path / "cancel-source")
    service = _service(tmp_path, source, verifier)
    first = service.preflight_install(source)
    other = service.preflight_install(source)
    assert service.cancel_preflight(first.plan).status == "completed"
    assert not first.plan.staged_root.exists() and other.plan.staged_root.exists() and source.exists()
    assert _confirm(service, first).reason == "preflight_cancelled"


def test_accepted_uninstall_has_no_cancel_and_cannot_reenable(tmp_path):
    service = _installed(tmp_path)
    from pet.feature_version_lease import FeatureVersionSelection

    lease = service.leases.acquire_host(FeatureVersionSelection.from_resolution(service.store.resolve_verified(service.verifier)))
    plan = service.preflight_uninstall()
    try:
        assert _confirm(service, plan).status == "awaiting_release"
        assert service.cancel_preflight(plan.plan).reason == "accepted_transaction_cannot_cancel"
        assert service.set_enabled(True, expected_revision=service.store.read().state.revision).reason == "pending_transaction"
        assert not service.store.read().state.enabled
    finally:
        lease.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_rollback_only_uses_verified_retained_previous(tmp_path, enabled):
    service = _installed(tmp_path, enabled=enabled)
    source, _, _ = _package(tmp_path / "newer", version="1.2.4")
    result = _confirm(service, service.preflight_upgrade(source))
    assert _startup(service, result.operation_id).status == "completed"
    previous = service.versions_root / "1.2.3"
    identity = previous.stat().st_ino
    rollback = service.preflight_rollback()
    assert rollback.plan.kind == "rollback" and rollback.plan.target_version == "1.2.3"
    result = _confirm(service, rollback)
    assert result.status == "awaiting_startup_confirmation"
    assert _startup(service, result.operation_id).status == "completed"
    state = service.store.read().state
    assert state.active == "1.2.3" and state.previous == "1.2.4" and state.enabled is enabled
    assert previous.stat().st_ino == identity


def test_rollback_rejects_missing_previous_and_external_downgrade(tmp_path):
    service = _installed(tmp_path)
    assert service.preflight_rollback().reason == "previous_unavailable"
    source, _, _ = _package(tmp_path / "outside-old", version="1.2.2")
    assert service.preflight_upgrade(source).reason == "downgrade_not_proven_compatible"


def test_state_moves_use_distinct_child_ids_and_replay_same_unacknowledged_intent(tmp_path, monkeypatch):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    preflight = service.preflight_install(source)
    original = service.store.commit
    calls = []
    interrupted = False

    def interrupt_before(change, *, expected_revision, operation_id):
        nonlocal interrupted
        calls.append(operation_id)
        if operation_id.endswith(".pending") and not interrupted:
            interrupted = True
            raise OSError("generated crash before commit")
        return original(change, expected_revision=expected_revision, operation_id=operation_id)

    monkeypatch.setattr(service.store, "commit", interrupt_before)
    assert _confirm(service, preflight).status == "recovery_required"
    saved = service._load(preflight.operation_id)["intent"]
    assert saved["operation_id"] == calls[0]
    assert service.recover_pending().status == "awaiting_startup_confirmation"
    assert calls[0] == calls[1]
    assert calls[-1] != calls[0]
    assert calls[-1].startswith("txc-") and len(calls[-1]) <= 64


def test_lifecycle_request_is_revision_and_live_owner_bound_before_acceptance(tmp_path):
    from pet.feature_lifecycle_contract import LifecyclePrepareRequest, verify_request

    service = _installed(tmp_path)
    seen = []

    class Runtime:
        def prepare(self, request):
            assert isinstance(request, LifecyclePrepareRequest)
            verify_request(service.store, request)
            seen.append(request)
            return RuntimePreparation()

    service.runtime = Runtime()
    result = _confirm(service, service.preflight_uninstall())
    assert result.status == "completed" and len(seen) == 2
    assert seen[0].revision < seen[1].revision
    assert seen[0].operation_id == seen[1].operation_id == result.operation_id
    assert seen[0].nonce != seen[1].nonce
    assert not list((service.store.root / "locks" / "lifecycle-requests").glob("*.json"))


def test_lifecycle_prepares_real_live_owner_and_waits_for_kernel_pin(tmp_path):
    from pet.feature_version_lease import FeatureVersionSelection

    service = _installed(tmp_path)
    selection = FeatureVersionSelection.from_resolution(service.store.resolve_verified(service.verifier))
    seen = []

    class Runtime:
        def prepare(self, request):
            seen.append(request)
            return RuntimePreparation()

    service.runtime = Runtime()
    with service.leases.acquire_host(selection) as pin:
        result = _confirm(service, service.preflight_uninstall())
        assert result.status == "awaiting_release" and result.reason == "version_in_use"
        assert seen and seen[0].live_owners == (
            (__import__("os").getpid(), __import__("pet.feature_version_lease", fromlist=["process_owner_identity"]).process_owner_identity()),
        )
        assert not service.store.read().state.enabled
    assert service.recover_pending().status == "completed"


def test_full_verification_never_holds_management_lock(tmp_path, monkeypatch):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    verify = FeaturePackageVerifier.verify

    def outside_lock(owner, root):
        with io.open_kernel_lock(service.management_lock_path):
            pass
        return verify(owner, root)

    monkeypatch.setattr(FeaturePackageVerifier, "verify", outside_lock)
    result = _confirm(service, service.preflight_install(source))
    assert result.status == "awaiting_startup_confirmation", result


def test_uninstall_and_gc_delete_outside_management_but_freeze_lease_admission(tmp_path, monkeypatch):
    from pet import feature_package_files

    service = _installed(tmp_path)
    for version in ("1.2.4", "1.2.5"):
        source, _, _ = _package(tmp_path / version, version=version)
        applied = _confirm(service, service.preflight_upgrade(source))
        assert _startup(service, applied.operation_id).status == "completed"
    remove = feature_package_files.remove_owned_tree
    checked = []

    def outside_lock(path, parent, limits):
        if parent == service.versions_root:
            with io.open_kernel_lock(service.management_lock_path):
                pass
            with pytest.raises(io.StateError, match="lock_busy"):
                io.open_kernel_lock(service.leases.leases_lock_path)
            checked.append(path.name)
        return remove(path, parent, limits)

    monkeypatch.setattr(feature_package_files, "remove_owned_tree", outside_lock)
    assert service.collect_garbage().status == "completed"
    assert _confirm(service, service.preflight_uninstall()).status == "completed"
    assert set(checked) == {"1.2.3", "1.2.4", "1.2.5"}
    assert service.store.read().state.active is None


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows file sharing")
def test_native_windows_file_lock_keeps_uninstall_pending_until_owned_handle_release(tmp_path):
    import ctypes
    from ctypes import wintypes

    service = _installed(tmp_path)
    target = service.store.root / "versions/1.2.3/manifest.json"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result = _confirm(service, service.preflight_uninstall())
        assert result.status == "recovery_required" and result.reason == "file_in_use"
        state = service.store.read().state
        assert state.pending_transaction and not state.enabled and state.active == "1.2.3"
        assert target.exists()
    finally:
        assert kernel.CloseHandle(handle)
    assert service.recover_pending().status == "completed"
    assert service.store.read().status == "uninstalled"
    assert not (service.store.root / "versions/1.2.3").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows owned directory ACL")
def test_native_windows_owned_acl_delete_denial_is_not_package_corruption(tmp_path):
    from pet.feature_probe_windows import _Win32

    service = _installed(tmp_path)
    root = service.store.root / "versions/1.2.3"
    target = root / "manifest.json"
    native = _Win32()
    sid = native.user_sid()
    # Preserve access to every generated child when Windows recomputes
    # inherited ACLs after replacing the parent's DACL.
    owned_nodes = list(root.rglob("*"))
    for node in owned_nodes:
        native.set_acl(node, f"D:P(A;;FA;;;SY)(A;;FA;;;{sid})")
    # Only this test's generated version and file are modified. DELETE is
    # denied on the file and DELETE_CHILD on its parent; reads remain allowed.
    native.set_acl(root, f"D:P(D;;0x40;;;{sid})(A;;FA;;;SY)(A;;FA;;;{sid})")
    native.set_acl(target, f"D:P(D;;SD;;;{sid})(A;;FA;;;SY)(A;;FA;;;{sid})")
    try:
        result = _confirm(service, service.preflight_uninstall())
        assert result.status == "recovery_required" and result.reason == "permission_denied"
        state = service.store.read().state
        assert state.pending_transaction and not state.enabled
        assert target.exists()
    finally:
        if target.exists():
            native.set_acl(target, f"D:P(A;;FA;;;SY)(A;;FA;;;{sid})")
        if root.exists():
            native.set_acl(root, f"D:P(A;;FA;;;SY)(A;;FA;;;{sid})")
    recovered = service.recover_pending()
    assert recovered.status == "completed", recovered
    assert service.store.read().status == "uninstalled"


@pytest.mark.parametrize(
    "winerror,reason",
    [
        (5, "permission_denied"),
        (32, "file_in_use"),
        (33, "file_in_use"),
        (39, "disk_full"),
        (112, "disk_full"),
        (225, "antivirus_blocked"),
        (226, "antivirus_blocked"),
    ],
)
def test_preflight_classifies_injected_windows_io_without_executing_or_accepting(tmp_path, monkeypatch, winerror, reason):
    from pet import feature_package_files

    source, verifier, _ = _package(tmp_path / "source")
    checker = StubChecker()
    service = _service(tmp_path, source, verifier, checker)

    def denied(*args):
        error = OSError("generated boundary fault; not an actual disk fill or antivirus event")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(feature_package_files, "stage", denied)
    result = service.preflight_install(source)
    assert result.status == "rejected" and result.reason == reason
    assert checker.calls == 0
    state = service.store.read().state
    assert not state.pending_transaction and not state.enabled and not state.versions


@pytest.mark.parametrize("resource", ["management", "leases", "state"])
def test_lock_busy_after_runtime_preparation_names_real_resource_and_retries(tmp_path, resource):
    """A confirmed operation may revoke entrypoints before a short lock conflict."""
    from contextlib import ExitStack

    from pet.feature_package_transactions import RuntimePreparation

    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    installed = _confirm(service, service.preflight_install(source))
    _startup(service, installed.operation_id)
    preflight = service.preflight_uninstall()
    before = service.store.read().state.document()
    paths = {"management": service.management_lock_path, "leases": service.leases.leases_lock_path, "state": service.store.lock_path}
    prepared = []
    with ExitStack() as held:

        class Contention:
            def prepare(self, request):
                prepared.append(request.operation_id)
                held.enter_context(io.open_kernel_lock(paths[resource]))
                return RuntimePreparation()

        service.runtime = Contention()
        failed = _confirm(service, preflight)
    assert prepared == [preflight.operation_id]
    assert failed.status == "failed"
    assert failed.reason == resource + "_lock_busy"
    assert failed.details["lock_resource"] == resource
    assert failed.details["safe_retry"] is True
    assert service.store.read().state.document() == before
    assert service._load(preflight.operation_id)["accepted"] is False
    assert service.versions_root.joinpath("1.2.3").is_dir()
    service.runtime = None
    retried = service.apply(failed.plan, confirmation_token=failed.plan.confirmation_token)
    assert retried.status == "completed"
    assert service.store.read().status == "uninstalled"
    assert not service.versions_root.joinpath("1.2.3").exists()
