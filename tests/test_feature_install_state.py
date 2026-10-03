"""Install state behavior, with OS/file/verification boundaries only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet.feature_install_state import FEATURE_ID, FeatureInstallStateStore, StateChange, StateError

DIGEST = "a" * 64


def document(**overrides):
    result = dict(
        schema_version=1, feature_id=FEATURE_ID, revision=1, versions={"1.0.0": DIGEST}, active="1.0.0", previous=None, enabled=True, pending_transaction=None
    )
    result.update(overrides)
    return result


def write_state(store, value):
    store.root.mkdir(parents=True, exist_ok=True)
    store.state_path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    return FeatureInstallStateStore(tmp_path)


def test_first_read_is_uninstalled_without_creating_any_files(store, tmp_path):
    result = store.read()
    assert result is not None, "read must return a classified immutable snapshot"
    assert result.status == "uninstalled" and result.state.revision == 0
    assert list(tmp_path.iterdir()) == []


def test_tombstone_remains_uninstalled_despite_old_version_directories(store):
    write_state(store, document(revision=8, versions={}, active=None, enabled=False))
    (store.root / "versions/1.0.0").mkdir(parents=True)
    result = store.read()
    assert result is not None, "an explicit uninstall must not be replaced by directory discovery"
    assert result.status == "uninstalled" and result.state.revision == 8


def test_read_snapshot_is_immutable_and_detached(store):
    write_state(store, document())
    result = store.read()
    assert result is not None
    assert result.status == "enabled"
    with pytest.raises(TypeError):
        result.state.versions["2.0.0"] = DIGEST
    with pytest.raises(AttributeError):
        result.state.revision = 99


@pytest.mark.parametrize(
    "overrides",
    [
        {"revision": True},
        {"revision": -1},
        {"revision": 1.0},
        {"enabled": 1},
        {"active": "2.0.0"},
        {"previous": "2.0.0"},
        {"active": None},
        {"versions": {"../escape": DIGEST}},
        {"versions": {"01.0.0": DIGEST}},
        {"versions": {"1.0.0": "bad"}},
        {"versions": []},
        {"feature_id": "other"},
        {"pending_transaction": "../escape"},
        {"extra": "not allowed"},
    ],
)
def test_invalid_state_is_not_empty_installation(store, overrides):
    write_state(store, document(**overrides))
    result = store.read()
    assert result is not None
    assert result.status == "corrupt" and result.state is None


def test_duplicate_json_field_is_rejected(store):
    write_state(store, document())
    store.state_path.write_text(store.state_path.read_text().replace('"revision": 1', '"revision": 1, "revision": 2'), encoding="utf-8")
    result = store.read()
    assert result is not None
    assert result.status == "corrupt"


@pytest.mark.parametrize("trace", ["state.json.bak", "transactions/old.json", "versions/1.0.0/manifest.json", "staging/partial/file"])
def test_missing_state_with_management_traces_needs_recovery(store, trace):
    path = store.root / trace
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old")
    result = store.read()
    assert result is not None
    assert result.status == "recovery_required" and result.state is None


def test_future_schema_is_not_recovered_using_old_backup(store):
    write_state(store, document(schema_version=2))
    before = store.state_path.read_bytes()
    result = store.recover()
    assert result is not None
    assert result.status == "unsupported_schema"
    assert store.state_path.read_bytes() == before


def test_commit_increments_revision_and_persists_tombstone(store):
    receipt = store.commit(StateChange({"1.0.0": DIGEST}, active="1.0.0", enabled=True), expected_revision=0, operation_id="install-1")
    assert receipt is not None, "commit must return a durable receipt"
    assert receipt.revision == 1
    assert store.read().state.enabled
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall-1")
    assert store.state_path.is_file()
    assert store.read().status == "uninstalled" and store.read().state.revision == 2


def installed_change():
    return StateChange({"1.0.0": DIGEST}, active="1.0.0", enabled=True)


def test_operation_retry_is_idempotent_even_after_later_uninstall(store):
    change = installed_change()
    first = store.commit(change, expected_revision=0, operation_id="install")
    assert first is not None
    assert store.commit(change, expected_revision=0, operation_id="install") == first
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    assert store.commit(change, expected_revision=0, operation_id="install") == first
    assert store.read().status == "uninstalled" and store.read().state.revision == 2


def test_operation_id_different_request_rejected_before_revision_check(store):
    store.commit(installed_change(), expected_revision=0, operation_id="same")
    with pytest.raises(StateError, match="operation_conflict"):
        store.commit(StateChange({}), expected_revision=0, operation_id="same")


def test_stale_revision_cannot_overwrite(store):
    store.commit(installed_change(), expected_revision=0, operation_id="one")
    with pytest.raises(StateError, match="revision_conflict"):
        store.commit(StateChange({}), expected_revision=0, operation_id="two")
    assert store.read().state.revision == 1


@pytest.mark.parametrize("revision,operation", [(True, "x"), (-1, "x"), (0, "../bad"), (0, ""), (0, "X"), (0, "a" * 80)])
def test_invalid_commit_identity_creates_no_state(store, revision, operation):
    with pytest.raises(StateError, match="invalid_request"):
        store.commit(StateChange({}), expected_revision=revision, operation_id=operation)
    assert not store.state_path.exists()


def test_unknown_state_fields_cannot_smuggle_credentials(store):
    write_state(store, document(api_key="TEST-SECRET"))
    result = store.read()
    assert result is not None
    assert result.status == "corrupt"
    assert "TEST-SECRET" not in repr(result)


def _competing_writer(data_root, ready, go, output, operation):
    import time

    service = FeatureInstallStateStore(Path(data_root))
    ready.set()
    assert go.wait(30)
    deadline = time.monotonic() + 30
    while True:
        try:
            receipt = service.commit(installed_change(), expected_revision=0, operation_id=operation)
            output.put(("ok", receipt.revision))
            return
        except StateError as exc:
            if exc.code != "lock_busy" or time.monotonic() >= deadline:
                output.put((exc.code, None))
                return
            # Bounded kernel-contention polling, not a guessed completion sleep.
            import threading

            threading.Event().wait(0.01)


def test_two_real_processes_have_exactly_one_revision_winner(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    go, ready1, ready2, output = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_competing_writer, args=(str(tmp_path), ready, go, output, op)) for ready, op in [(ready1, "a"), (ready2, "b")]]
    try:
        for process in processes:
            process.start()
        assert ready1.wait(30) and ready2.wait(30)
        go.set()
        results = [output.get(timeout=40), output.get(timeout=40)]
        for process in processes:
            process.join(30)
            assert process.exitcode == 0
        assert sorted(code for code, _ in results) == ["ok", "revision_conflict"]
        assert FeatureInstallStateStore(tmp_path).read().state.revision == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)
        output.close()


def test_corrupt_state_without_proof_stays_closed(store):
    store.root.mkdir(parents=True)
    store.state_path.write_bytes(b'{"revision":')
    before = store.state_path.read_bytes()
    result = store.recover()
    assert result is not None
    assert result.status == "corrupt" and result.state is None
    assert store.state_path.read_bytes() == before


def test_committed_snapshot_recovers_exact_latest_tombstone(store):
    store.commit(installed_change(), expected_revision=0, operation_id="install")
    assert store.state_path.exists()
    enabled_backup = store.state_path.read_bytes()
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    (store.root / "state.json.bak").write_bytes(enabled_backup)
    store.state_path.write_bytes(b"broken")
    result = store.recover()
    assert result.status == "uninstalled" and result.state.revision == 2
    assert any(p.read_bytes() == b"broken" for p in (store.root / "transactions").glob("corrupt-*.bin"))
    assert store.recover() == result


def test_only_old_backup_is_not_recovery_proof(store):
    store.root.mkdir(parents=True)
    (store.root / "state.json.bak").write_text(json.dumps(document()))
    result = store.recover()
    assert result is not None
    assert result.status == "recovery_required" and result.state is None
    assert not store.state_path.exists()


def test_operations_preserve_unrelated_user_data(store, tmp_path):
    paths = [tmp_path / p for p in ["config.json", "memory.json", "quota.json", "chat/history.json"]]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"user-owned-data")
    store.commit(installed_change(), expected_revision=0, operation_id="a")
    assert store.read() is not None
    store.commit(StateChange({}), expected_revision=1, operation_id="b")
    store.recover()
    assert all(p.read_bytes() == b"user-owned-data" for p in paths)


@pytest.mark.parametrize("field", ["active", "previous", "pending_transaction"])
def test_nonstandard_json_constants_are_rejected(store, field):
    write_state(store, document(enabled=False, active=None))
    raw = store.state_path.read_text()
    raw = raw.replace(f'"{field}": null', f'"{field}": NaN')
    store.state_path.write_text(raw)
    assert store.read().status == "corrupt"


def _hold_state_lock(data_root, ready, release):
    from pet.feature_state_io import state_lock

    service = FeatureInstallStateStore(Path(data_root))
    with state_lock(service.lock_path):
        ready.set()
        assert release.wait(60)


def test_kernel_lock_busy_and_holder_crash_are_distinct_from_io_error(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_state_lock, args=(str(tmp_path), ready, release))
    service = FeatureInstallStateStore(tmp_path)
    child.start()
    try:
        assert ready.wait(30)
        with pytest.raises(StateError, match="lock_busy"):
            service.commit(installed_change(), expected_revision=0, operation_id="locked")
        assert service.read().status == "lock_busy"
        # Intentionally crash our test child, not a user's process.
        child.terminate()
        child.join(30)
        assert not child.is_alive()
        assert service.commit(installed_change(), expected_revision=0, operation_id="after-crash").revision == 1
    finally:
        # A terminated process may poison an Event condition. Never notify or
        # reuse that event after the deliberate crash; join the owned child.
        if child.is_alive():
            child.terminate()
            child.join(10)


def test_permission_error_is_not_lock_busy(store, monkeypatch):
    from pet import feature_state_io as io

    def denied(*args, **kwargs):
        raise PermissionError("private user path")

    monkeypatch.setattr(io.os, "open", denied)
    with pytest.raises(StateError) as caught:
        store.commit(installed_change(), expected_revision=0, operation_id="permission")
    assert caught.value.code == "io_error"
    assert "private user path" not in str(caught.value)


@pytest.mark.parametrize("phase", ["prepare", "state", "receipt"])
def test_atomic_replace_error_does_not_claim_success(store, monkeypatch, phase):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="old")
    replace = io.os.replace

    def fail(source, target):
        stage = "state" if Path(target).name == "state.json" else json.loads(Path(source).read_bytes()).get("phase", "frontier")
        if stage == {"prepare": "prepared", "state": "state", "receipt": "committed"}[phase]:
            raise OSError("injected disk error")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(io.os, "replace", fail)
        with pytest.raises(StateError, match="io_error"):
            store.commit(StateChange({}), expected_revision=1, operation_id="next")
    recovered = store.recover()
    assert recovered.state.revision == (2 if phase == "receipt" else 1)
    assert recovered.status == ("uninstalled" if phase == "receipt" else "enabled")
    assert store.recover() == recovered


def _crashing_committer(data_root, phase):
    import os

    from pet import feature_state_io as io

    service = FeatureInstallStateStore(Path(data_root))
    atomic, replace, fsync = io.atomic_write, io.os.replace, io.os.fsync

    def crash_write(path, data):
        atomic(path, data)
        stage = json.loads(data).get("phase", "frontier") if path.name != "state.json" else "state"
        if phase == "frontier" and stage == "frontier":
            os._exit(73)
        if (phase == "prepared" and stage == "prepared") or (phase == "replaced" and stage == "state") or (phase == "committed" and stage == "committed"):
            os._exit(73)

    def crash_replace(source, target):
        if phase == "frontier_temp" and Path(target).name == "commit-frontier.json":
            os._exit(73)
        if phase == "state_temp" and Path(target).name == "state.json":
            os._exit(73)
        if phase == "receipt_temp" and Path(target).name == "op-next.json" and json.loads(Path(source).read_bytes()).get("phase") == "committed":
            os._exit(73)
        return replace(source, target)

    def crash_fsync(fd):
        fsync(fd)
        if phase == "prepare_temp":
            os._exit(73)

    io.atomic_write, io.os.replace, io.os.fsync = crash_write, crash_replace, crash_fsync
    service.commit(StateChange({}), expected_revision=1, operation_id="next")
    os._exit(74)


@pytest.mark.parametrize(
    "phase,new_committed",
    [
        ("prepare_temp", False),
        ("prepared", False),
        ("frontier_temp", False),
        ("frontier", False),
        ("state_temp", False),
        ("replaced", True),
        ("receipt_temp", True),
        ("committed", True),
    ],
)
def test_real_process_interruption_at_each_commit_boundary(tmp_path, phase, new_committed):
    import multiprocessing

    service = FeatureInstallStateStore(tmp_path)
    service.commit(installed_change(), expected_revision=0, operation_id="old")
    child = multiprocessing.get_context("spawn").Process(target=_crashing_committer, args=(str(tmp_path), phase))
    child.start()
    try:
        child.join(30)
        assert child.exitcode == 73
        result = service.recover()
        assert result.state.revision == (2 if new_committed else 1)
        assert result.status == ("uninstalled" if new_committed else "enabled")
        assert service.recover() == result
        if new_committed:
            assert service.commit(StateChange({}), expected_revision=1, operation_id="next").revision == 2
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)


def test_first_commit_pre_replace_failure_can_recover_proven_initial_state(store, monkeypatch):
    from pet import feature_state_io as io

    atomic = io.atomic_write

    def fail_state(path, data):
        if path == store.state_path:
            raise OSError("no state replacement")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail_state)
        with pytest.raises(StateError, match="io_error"):
            store.commit(installed_change(), expected_revision=0, operation_id="failed-first")
    assert store.read().status == "recovery_required"
    recovered = store.recover()
    assert recovered.status == "uninstalled" and recovered.state.revision == 0
    assert store.commit(installed_change(), expected_revision=0, operation_id="retry-new").revision == 1


def test_fsync_failure_is_reported_without_changing_active(store, monkeypatch):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="old")

    def fail(fd):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(io.os, "fsync", fail)
        with pytest.raises(StateError, match="io_error"):
            store.commit(StateChange({}), expected_revision=1, operation_id="failed")
    assert store.recover().state.revision == 1


def test_ambiguous_later_prepare_prevents_restoring_old_enabled_backup(store, monkeypatch):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="old")
    atomic = io.atomic_write

    def fail(path, data):
        if path == store.state_path:
            raise OSError("interruption")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail)
        with pytest.raises(StateError):
            store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    store.state_path.write_bytes(b"truncated")
    result = store.recover()
    assert result.status == "recovery_required" and result.state is None
    assert store.state_path.read_bytes() == b"truncated"


@pytest.mark.parametrize("damage", ["checksum", "truncate", "future", "unknown-record"])
def test_damaged_or_unknown_proof_cannot_recover(store, damage):
    store.commit(installed_change(), expected_revision=0, operation_id="old")
    store.commit(StateChange({}), expected_revision=1, operation_id="new")
    record = store.root / "transactions/op-new.json"
    if damage == "truncate":
        record.write_bytes(b"{")
    elif damage == "unknown-record":
        (store.root / "transactions/op-unknown.json").write_bytes(b"{")
    else:
        data = json.loads(record.read_bytes())
        data["after_digest" if damage == "checksum" else "schema_version"] = "f" * 64 if damage == "checksum" else 2
        record.write_text(json.dumps(data))
    store.state_path.write_bytes(b"broken")
    result = store.recover()
    assert result.state is None
    assert result.status in ("corrupt", "unsupported_schema")
    assert store.state_path.read_bytes() == b"broken"


def test_missing_committed_state_restores_exact_latest(store):
    store.commit(installed_change(), expected_revision=0, operation_id="a")
    store.commit(StateChange({}), expected_revision=1, operation_id="b")
    store.state_path.unlink()
    assert store.read().status == "recovery_required"
    assert store.recover().status == "uninstalled"
    assert store.read().state.revision == 2


def test_newer_valid_tombstone_is_not_replaced_by_older_proof(store):
    store.commit(installed_change(), expected_revision=0, operation_id="a")
    write_state(store, document(revision=20, versions={}, active=None, enabled=False))
    assert store.recover().state.revision == 20
    assert store.read().status == "uninstalled"


def test_inconsistent_state_at_same_revision_requires_diagnosis(store):
    store.commit(installed_change(), expected_revision=0, operation_id="a")
    write_state(store, document(enabled=False))
    assert store.read().status == "corrupt"
    assert store.recover().status == "corrupt"
    assert json.loads(store.state_path.read_bytes())["enabled"] is False


def test_state_hardlink_is_rejected_without_touching_external_file(store, tmp_path):
    import os

    external = tmp_path / "external.json"
    external.write_text(json.dumps(document()))
    store.root.mkdir(parents=True)
    os.link(external, store.state_path)
    assert store.read().status == "unsafe_path"
    assert store.recover().status == "unsafe_path"
    assert external.read_text() == json.dumps(document())


@pytest.fixture
def signed_package(store):
    import hashlib
    import sys

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from pet.plugins.package_trust import FeaturePackageVerifier

    root = store.root / "versions/1.0.0"
    root.mkdir(parents=True)
    manifest = dict(
        id=FEATURE_ID,
        version="1.0.0",
        api_version="1",
        core_requires=">=4.0.0,<5.0.0",
        platforms=[sys.platform],
        capabilities=["screen.capture"],
        factory="screen-understanding/v1",
        worker={"path": "worker/worker.exe", "args": []},
        files={},
    )
    for name in ["host/__init__.py", "host/factory.py", "worker/worker.exe"]:
        content = b"raise AssertionError('feature code must not execute')\n"
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest["files"][name] = dict(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
    raw = json.dumps(manifest).encode()
    key = Ed25519PrivateKey.generate()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sig").write_bytes(key.sign(raw))
    # Explicit test state, not directory discovery or an installer invocation.
    write_state(store, document(versions={"1.0.0": hashlib.sha256(raw).hexdigest()}))
    verifier = FeaturePackageVerifier(
        core_version="4.2.1",
        api_version="1",
        trust_anchors={"test-only": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
        allowed_capabilities={"screen.capture"},
    )
    return root, verifier


def test_resolve_verifies_signature_files_and_manifest_identity_without_execution(store, signed_package):
    root, verifier = signed_package
    result = store.resolve_verified(verifier)
    assert result.status == "resolved" and result.revision == 1
    assert result.descriptor.root == root
    assert result.descriptor.trust_status == "trusted_official"


@pytest.mark.parametrize("damage", ["signature", "file", "identity", "compatibility"])
def test_resolve_rejects_untrusted_or_mismatched_package(store, signed_package, damage):
    root, verifier = signed_package
    if damage == "signature":
        (root / "manifest.sig").write_bytes(b"bad")
    elif damage == "file":
        (root / "host/factory.py").write_bytes(b"changed")
    elif damage == "identity":
        write_state(store, document())
    else:
        from dataclasses import replace

        verifier = replace(verifier, core_version="100.0.0")
    result = store.resolve_verified(verifier)
    assert result.status == "verification_failed" and result.descriptor is None


def test_verification_is_outside_lock_and_rechecks_revision(store, signed_package):
    _, verifier = signed_package

    class ConcurrentChange:
        def verify(self, root):
            descriptor = verifier.verify(root)
            store.commit(StateChange({}), expected_revision=1, operation_id="concurrent-uninstall")
            return descriptor

    result = store.resolve_verified(ConcurrentChange())
    assert result.status == "revision_conflict" and result.descriptor is None
    assert store.read().status == "uninstalled"


def test_disabled_pending_and_uninstalled_never_call_verifier(store):
    class Forbidden:
        def verify(self, root):
            raise AssertionError("verifier must not be called")

    assert store.resolve_verified(Forbidden()).status == "uninstalled"
    write_state(store, document(enabled=False))
    assert store.resolve_verified(Forbidden()).status == "disabled"
    write_state(store, document(pending_transaction="install-pending"))
    assert store.resolve_verified(Forbidden()).status == "recovery_required"


def test_module_import_does_not_load_qt_feature_host_or_verifier():
    import subprocess
    import sys

    code = "import pet.feature_install_state,sys; assert not any(n.startswith(('PySide6','pet.plugins','features.screen_understanding')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_aborted_later_operation_prevents_restoring_an_older_enabled_commit(store, monkeypatch):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="install")
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    atomic = io.atomic_write

    def fail_state(path, data):
        if path == store.state_path:
            raise OSError("interrupted reinstall")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail_state)
        with pytest.raises(StateError):
            store.commit(installed_change(), expected_revision=2, operation_id="aborted-reinstall")
    assert store.recover().status == "uninstalled"
    # A missing newer receipt must not resurrect the older installed snapshot:
    # the aborted attempt proves a later revision had existed.
    (store.transactions / "op-uninstall.json").unlink()
    store.state_path.write_bytes(b"broken")
    result = store.recover()
    assert result.status == "recovery_required"
    assert store.state_path.read_bytes() == b"broken"


def test_read_rejects_state_older_than_a_later_operation_before_snapshot(store, monkeypatch):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="install")
    old = store.state_path.read_bytes()
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    atomic = io.atomic_write

    def fail_state(path, data):
        if path == store.state_path:
            raise OSError("interrupted reinstall")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail_state)
        with pytest.raises(StateError):
            store.commit(installed_change(), expected_revision=2, operation_id="aborted-reinstall")
    store.recover()
    (store.transactions / "op-uninstall.json").unlink()
    store.state_path.write_bytes(old)
    assert store.read().status == "recovery_required"
    assert store.recover().status == "recovery_required"
    assert store.state_path.read_bytes() == old


def test_commit_does_not_create_broken_evidence_chain_after_unknown_newer_state(store):
    store.commit(installed_change(), expected_revision=0, operation_id="known")
    write_state(store, document(revision=20, versions={}, active=None, enabled=False))
    before = store.state_path.read_bytes()
    with pytest.raises(StateError, match="recovery_required"):
        store.commit(installed_change(), expected_revision=20, operation_id="must-not-bridge-gap")
    assert store.state_path.read_bytes() == before
    assert not (store.transactions / "op-must-not-bridge-gap.json").exists()


def test_aborted_target_can_be_replaced_by_different_operation(store, monkeypatch):
    from pet import feature_state_io as io

    atomic = io.atomic_write

    def fail_state(path, data):
        if path == store.state_path:
            raise OSError("interrupted")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail_state)
        with pytest.raises(StateError):
            store.commit(installed_change(), expected_revision=0, operation_id="first")
    assert store.recover().status == "uninstalled"
    with pytest.raises(StateError, match="operation_aborted"):
        store.commit(installed_change(), expected_revision=0, operation_id="first")
    store.commit(StateChange({}), expected_revision=0, operation_id="different")
    assert store.read().status == "uninstalled" and store.read().state.revision == 1
    assert store.recover().state.revision == 1


def test_future_schema_with_real_commit_evidence_is_never_overwritten(store):
    store.commit(installed_change(), expected_revision=0, operation_id="old")
    write_state(store, document(schema_version=2, revision=9))
    before = store.state_path.read_bytes()
    assert store.recover().status == "unsupported_schema"
    assert store.state_path.read_bytes() == before


def test_metadata_and_record_limits_are_explicit_not_empty_installations(store, monkeypatch):
    from pet import feature_install_state as state

    monkeypatch.setattr(state, "MAX_RECORDS", 1)
    first = store.commit(installed_change(), expected_revision=0, operation_id="one")
    with pytest.raises(StateError, match="metadata_limit"):
        store.commit(StateChange({}), expected_revision=1, operation_id="two")
    assert store.commit(installed_change(), expected_revision=0, operation_id="one") == first
    assert store.read().state.revision == 1
    monkeypatch.setattr(state, "MAX_DOCUMENT_BYTES", 10)
    assert store.read().status == "metadata_limit"
    assert store.recover().state is None


@pytest.mark.parametrize("marker", ["symlink", "reparse"])
def test_linked_version_path_is_rejected_before_verifier(store, monkeypatch, marker):
    import stat
    from types import SimpleNamespace

    write_state(store, document())
    root = store.root / "versions/1.0.0"
    root.mkdir(parents=True)
    lstat = Path.lstat
    called = []

    def linked(path):
        if path == root:
            return SimpleNamespace(st_mode=stat.S_IFLNK if marker == "symlink" else stat.S_IFDIR, st_file_attributes=0x400 if marker == "reparse" else 0)
        return lstat(path)

    class Forbidden:
        def verify(self, path):
            called.append(path)
            raise AssertionError("unsafe path cannot reach verifier")

    monkeypatch.setattr(Path, "lstat", linked)
    result = store.resolve_verified(Forbidden())
    assert result.status == "verification_failed" and result.descriptor is None
    assert called == []


@pytest.mark.parametrize("missing", ["last-receipt", "frontier"])
def test_missing_commit_tail_never_revives_uninstalled_feature(store, missing):
    store.commit(installed_change(), expected_revision=0, operation_id="install")
    store.commit(StateChange({}), expected_revision=1, operation_id="uninstall")
    target = store.transactions / ("op-uninstall.json" if missing == "last-receipt" else "commit-frontier.json")
    target.unlink(missing_ok=True)
    store.state_path.write_bytes(b"truncated tombstone")
    result = store.recover()
    assert result.status == "recovery_required" and result.state is None
    assert store.state_path.read_bytes() == b"truncated tombstone"


def test_frontier_failure_before_state_replacement_preserves_old_active(store, monkeypatch):
    from pet import feature_state_io as io

    store.commit(installed_change(), expected_revision=0, operation_id="old")
    atomic = io.atomic_write

    def fail_frontier(path, data):
        if path.name == "commit-frontier.json":
            raise OSError("interrupted frontier")
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(io, "atomic_write", fail_frontier)
        with pytest.raises(StateError, match="io_error"):
            store.commit(StateChange({}), expected_revision=1, operation_id="aborted")
    assert store.recover().state.revision == 1
    assert store.commit(StateChange({}), expected_revision=1, operation_id="retry").revision == 2


def test_windows_lock_reuses_native_types_instead_of_growing_pointer_cache(monkeypatch):
    import ctypes
    import sys
    from types import SimpleNamespace

    from pet import feature_state_io as io

    types, libraries = [], []

    def acquire(handle, flags, reserved, low, high, overlapped):
        assert (handle, flags, reserved, low, high) == (123, 3, 0, 1, 0)
        types.append(type(overlapped._obj))
        return True

    def library(name, **kwargs):
        libraries.append(name)
        return SimpleNamespace(LockFileEx=acquire)

    # Real ctypes structures; only the DLL/OS handle boundary is substituted.
    cached = getattr(io, "_windows_lock", None)
    if cached is not None:
        cached.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "platform", "win32")
            patch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
            patch.setattr(ctypes, "WinDLL", library, raising=False)
            for _ in range(8):
                io._lock(123)
        assert len(set(types)) == 1
        assert libraries == ["kernel32"]
    finally:
        if cached is not None:
            cached.cache_clear()
