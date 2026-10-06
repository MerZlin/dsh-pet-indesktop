"""Bounded parent-owned probe recovery; only native launch/process APIs are mocked."""

import ctypes
import json
from pathlib import Path

import pytest

from pet.feature_probe_windows import ProbeOwnership
from tests.test_feature_probe_adapter import _fixture


def _owned_fixture(tmp_path, monkeypatch, *, limits=None):
    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch, limits=limits)
    from pet import feature_probe_adapter

    launcher = feature_probe_adapter.WindowsProbeLauncher

    class OwnedLauncher(launcher):
        def run(self, root, **kwargs):
            # Native boundary fixture creates no process/profile. Record that
            # fact just as the trusted native parent does, never via stdout.
            owner = ProbeOwnership(root, self.bundle.manifest_digest)
            owner.update("cleaned", process_released=True)
            return super().run(root, **kwargs)

    monkeypatch.setattr(feature_probe_adapter, "WindowsProbeLauncher", OwnedLauncher)
    return sandbox, request, calls, options, bundle


def test_successful_probe_reclaims_only_its_owned_materials(tmp_path, monkeypatch):
    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    unknown = sandbox.run_parent / "unknown-not-owned"
    unknown.mkdir(parents=True)
    (unknown / "keep.txt").write_text("not an ownership receipt")
    result = sandbox.run(request)
    assert result.host_valid and result.worker_graceful_exit
    assert len(calls) == 2
    assert all(not root.exists() for root, _ in calls), "completed probe retains full helper/candidate copies"
    assert request.package_root.exists() and bundle.root.exists()
    assert (unknown / "keep.txt").read_text() == "not an ownership receipt"


def test_probe_gc_is_public_idempotent_and_does_not_adopt_unknown_directory(tmp_path, monkeypatch):
    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    unknown = sandbox.run_parent / ("probe-" + "f" * 32)
    unknown.mkdir(parents=True)
    assert callable(getattr(sandbox, "collect_garbage", None)), "no production recovery seam"
    assert all(result.status == "idempotent" for result in sandbox.collect_garbage())
    assert unknown.exists()


def test_cleaned_native_marker_cannot_bypass_live_pid_check(tmp_path, monkeypatch):
    from pet.feature_probe_windows import cleanup_owned_probe
    from tests.test_feature_probe_windows import _bundle

    helper, digest = _bundle(tmp_path)
    owner = ProbeOwnership(tmp_path, digest)
    owner.update("cleaned", pid=1234, created=9876, process_released=True)
    calls = []

    class API:
        def open_process(self, access, inherit, pid):
            assert not access & 1  # no PROCESS_TERMINATE
            return 42

        def process_created(self, handle):
            return 9876

        def wait(self, handle, timeout):
            return 258

        def close(self, handle):
            calls.append("closed")

        def delete_profile(self, profile):
            pytest.fail("live probe profile must not be removed")

    monkeypatch.setattr("pet.feature_probe_windows._Win32", API)
    assert cleanup_owned_probe(tmp_path, digest) == "awaiting_release"
    assert calls == ["closed"] and helper.exists()


def test_probe_gc_and_new_probe_wait_on_real_maintenance_kernel_lock(tmp_path, monkeypatch):
    from pet.feature_state_io import open_kernel_lock

    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    with open_kernel_lock(sandbox.run_parent / "materials.lock"):
        outcomes = sandbox.collect_garbage()
        assert len(outcomes) == 1 and outcomes[0].status == "awaiting_release"
        assert sandbox.run(request).reason == "probe_materials_in_use"
        assert not calls


def test_delete_failure_is_warning_then_new_instance_safely_recovers(tmp_path, monkeypatch):
    from pet import feature_probe_materials
    from pet.feature_probe_adapter import WindowsFeatureProbeSandbox

    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    original = feature_probe_materials.files.remove_owned_tree

    def deny(path, parent, limits):
        raise PermissionError("generated boundary injection")

    monkeypatch.setattr(feature_probe_materials.files, "remove_owned_tree", deny)
    result = sandbox.run(request)
    assert result.host_valid and result.worker_graceful_exit
    assert any(root.exists() for root, _ in calls)
    assert any(row.status == "recovery_required" for row in sandbox.collect_garbage())
    monkeypatch.setattr(feature_probe_materials.files, "remove_owned_tree", original)
    restarted = WindowsFeatureProbeSandbox(sandbox.verifier, bundle, sandbox.run_parent)
    assert any(row.status == "completed" for row in restarted.collect_garbage())
    assert all(not root.exists() for root, _ in calls)
    assert all(row.status == "idempotent" for row in restarted.collect_garbage())
    assert request.package_root.exists() and bundle.root.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "foreign_node", "journal_conflict"])
def test_probe_gc_does_not_delete_conflicted_or_escaping_materials(tmp_path, monkeypatch, kind):
    from pet import feature_probe_materials
    from pet.feature_probe_adapter import WindowsFeatureProbeSandbox

    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    original = feature_probe_materials.files.remove_owned_tree
    monkeypatch.setattr(feature_probe_materials.files, "remove_owned_tree", lambda *args: (_ for _ in ()).throw(PermissionError()))
    sandbox.run(request)
    monkeypatch.setattr(feature_probe_materials.files, "remove_owned_tree", original)
    attempt = calls[0][0].parent
    canary = tmp_path / "outside-canary"
    canary.write_text("keep outside root")
    if kind == "symlink":
        try:
            (attempt / "escape").symlink_to(canary)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    elif kind == "hardlink":
        (attempt / "escape").hardlink_to(canary)
    elif kind == "foreign_node":
        (attempt / "unknown-owner").mkdir()
    else:
        journal = sandbox.run_parent / "materials.json"
        doc = json.loads(journal.read_bytes())
        next(iter(doc["attempts"].values()))["root"] = str(tmp_path / "outside")
        journal.write_text(json.dumps(doc))
    restarted = WindowsFeatureProbeSandbox(sandbox.verifier, bundle, sandbox.run_parent)
    assert any(row.status == "recovery_required" for row in restarted.collect_garbage())
    assert attempt.exists() and canary.read_text() == "keep outside root"


def test_partial_delete_recovery_does_not_require_deleted_helper_to_be_reverified(tmp_path, monkeypatch):
    from pet import feature_probe_materials

    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    original = feature_probe_materials.files.remove_owned_tree
    fired = False

    def partial(path, parent, limits):
        nonlocal fired
        if not fired:
            fired = True
            target = path / "host/helper/probe.exe"
            target.unlink()
            raise PermissionError("after one owned file removed")
        original(path, parent, limits)

    monkeypatch.setattr(feature_probe_materials.files, "remove_owned_tree", partial)
    assert sandbox.run(request).host_valid
    assert any(row.status == "completed" for row in sandbox.collect_garbage())
    assert all(not root.exists() for root, _ in calls)


def test_uncertain_launch_without_pid_is_retained_not_guessed(tmp_path, monkeypatch):
    from pet import feature_probe_adapter

    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch)

    class UncertainLauncher:
        def __init__(self, bundle, *, limits):
            self.bundle = bundle

        def run(self, root, **kwargs):
            ProbeOwnership(root, self.bundle.manifest_digest).update("profile_created")
            calls.append((root, kwargs))
            raise OSError("crash window boundary fixture")

    monkeypatch.setattr(feature_probe_adapter, "WindowsProbeLauncher", UncertainLauncher)
    assert not sandbox.run(request).host_valid
    assert any(row.status == "recovery_required" for row in sandbox.collect_garbage())
    assert calls[0][0].exists()


def test_missing_attempt_before_mkdir_intent_recovers_and_records_are_bounded(tmp_path, monkeypatch):
    from pet.feature_probe_materials import ProbeMaterialStore

    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch)
    store = ProbeMaterialStore(sandbox.run_parent)
    with store.lock():
        row = store.begin(sandbox.verifier.verify(request.package_root), bundle.manifest_digest)
        assert not row.root.exists()
    assert any(value.status == "completed" for value in store.collect_garbage())
    for _ in range(12):
        assert sandbox.run(request).host_valid
    doc = json.loads((sandbox.run_parent / "materials.json").read_bytes())
    assert not doc["attempts"] and len(doc["recent"]) <= 8
    assert not list(sandbox.run_parent.glob("probe-*"))


def test_cleanup_checks_actual_generated_process_identity_before_release(tmp_path):
    import subprocess
    import sys

    from pet.feature_probe_windows import _Win32, cleanup_owned_probe
    from tests.test_feature_probe_windows import _bundle

    _helper, digest = _bundle(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE)
    api = _Win32()
    handle = api.open_process(0x100000 | 0x1000, False, process.pid)
    try:
        assert handle
        owner = ProbeOwnership(tmp_path, digest)
        owner.update("cleaned", pid=process.pid, created=api.process_created(handle), process_released=True)
        assert cleanup_owned_probe(tmp_path, digest) == "awaiting_release"
        process.stdin.write(b"x")
        process.stdin.flush()
        process.wait(timeout=30)
        assert cleanup_owned_probe(tmp_path, digest) == "idempotent"
    finally:
        api.close(handle)
        process.stdin.close()
        process.wait(timeout=30)


def test_gc_limits_cover_both_valid_snapshots_not_one_package(tmp_path, monkeypatch):
    from dataclasses import replace

    from pet.feature_probe_materials import ProbeMaterialStore

    # Configure once BEFORE source, policy and launch fixture are bound. The
    # signed source has nine nodes; two valid snapshots have more than nine.
    limits = replace(ProbeMaterialStore.LIMITS, max_entries=9)
    monkeypatch.setattr(ProbeMaterialStore, "LIMITS", limits)
    sandbox, request, calls, options, bundle = _owned_fixture(tmp_path, monkeypatch, limits=limits)
    assert sandbox.verifier.verify(request.package_root)
    assert sandbox.run(request).host_valid
    assert all(not root.exists() for root, _ in calls), "one-package limit blocks valid two-snapshot GC"
