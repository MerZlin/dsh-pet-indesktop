"""Public launcher contract; native permissions are a separate opt-in gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _bundle(tmp_path):
    root = tmp_path / "helper"
    root.mkdir()
    (root / "probe.exe").write_bytes(b"fixture-not-an-executable")
    content = json.dumps(
        {"schema": 1, "entry": "probe.exe", "files": {"probe.exe": hashlib.sha256((root / "probe.exe").read_bytes()).hexdigest()}}, sort_keys=True
    ).encode()
    (root / "bundle.json").write_bytes(content)
    return root, hashlib.sha256(content).hexdigest()


def test_bundle_requires_pinned_manifest_and_every_file(tmp_path):
    from pet.feature_probe_windows import ProbeLaunchError, TrustedProbeBundle

    root, digest = _bundle(tmp_path)
    bundle = TrustedProbeBundle(root, digest)
    assert bundle.verify() == root / "probe.exe"
    (root / "probe.exe").write_bytes(b"changed")
    with pytest.raises(ProbeLaunchError, match="bundle_integrity"):
        bundle.verify()


def test_bundle_rejects_unregistered_files(tmp_path):
    from pet.feature_probe_windows import ProbeLaunchError, TrustedProbeBundle

    root, digest = _bundle(tmp_path)
    (root / "extra.dll").write_bytes(b"untrusted")
    with pytest.raises(ProbeLaunchError, match="bundle_inventory"):
        TrustedProbeBundle(root, digest).verify()


def test_bundle_rejects_parent_escape(tmp_path):
    from pet.feature_probe_windows import ProbeLaunchError, TrustedProbeBundle

    root, digest = _bundle(tmp_path)
    data = json.dumps({"schema": 1, "entry": "../probe.exe", "files": {"../probe.exe": "0" * 64}}).encode()
    (root / "bundle.json").write_bytes(data)
    with pytest.raises(ProbeLaunchError, match="bundle_path"):
        TrustedProbeBundle(root, hashlib.sha256(data).hexdigest()).verify()


def test_launcher_never_advertises_isolation_without_native_evidence():
    from pet.feature_probe_windows import LaunchEvidence

    evidence = LaunchEvidence(False, False, False, False, False)
    assert not evidence.isolation_enforced
    assert LaunchEvidence(True, True, True, True, True).isolation_enforced


def test_limits_are_bounded_and_no_ordinary_subprocess_fallback():
    from pet.feature_probe_windows import ProbeLimits

    limits = ProbeLimits()
    assert limits.timeout == 30.0
    assert limits.memory_bytes == 512 * 1024 * 1024
    assert limits.output_bytes <= 256 * 1024
    with pytest.raises(ValueError):
        ProbeLimits(timeout=0)
    with pytest.raises(ValueError):
        ProbeLimits(output_bytes=1024 * 1024 * 1024)


def test_native_lpac_permission_matrix(tmp_path, monkeypatch):
    import os

    bundle = os.environ.get("DSHPET_NATIVE_PROBE_BUNDLE")
    if os.name != "nt" or not bundle:
        pytest.skip("explicit native bundle required; not an isolation pass")
    from scripts.validate_feature_probe_windows import validate

    evidence = validate(Path(bundle), tmp_path / "native-owned")
    assert evidence["status"] == "passed"
    assert evidence["permissions"]["passed"]


def test_evidence_lpac_requires_kernel_access_check():
    from pet.feature_probe_windows import LaunchEvidence

    evidence = LaunchEvidence(appcontainer=True, zero_capabilities=True, lpac_access_denied=False, win32k_disabled=True, job_verified=True)
    assert not evidence.isolation_enforced


def test_protocol_output_rejects_split_oversize_lines_and_accepts_bounded_frames():
    from pet.feature_probe_windows import ProbeLaunchError, ProbeLineBuffer

    frames = ProbeLineBuffer()
    assert frames.feed(b'{"hello":') == []
    assert frames.feed(b"true}\n") == [b'{"hello":true}']
    with pytest.raises(ProbeLaunchError, match="probe_line_limit"):
        frames.feed(b"x" * (64 * 1024 + 1))


def test_protocol_input_includes_framing_in_size_limit():
    from pet.feature_probe_windows import ProbeLaunchError, ProbeLineBuffer

    frames = ProbeLineBuffer()
    with pytest.raises(ProbeLaunchError, match="probe_line_limit"):
        frames.feed(b"x" * (64 * 1024) + b"\n")


def test_input_writer_owns_blocking_io_and_rejects_oversize_input():
    import threading

    from pet.feature_probe_windows import ProbeInputWriter, ProbeLaunchError

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def write(data):
        entered.set()
        assert release.wait(5)

    writer = ProbeInputWriter(write, closed.set)
    try:
        writer.send(b"hello\n")
        assert entered.wait(5)
        with pytest.raises(ProbeLaunchError, match="probe_input_limit"):
            writer.send(b"x" * (64 * 1024 + 1))
        writer.finish()
        assert not writer.finished.is_set()
    finally:
        release.set()
        assert writer.finished.wait(5)
    assert closed.is_set()


def test_input_writer_reports_errors_and_finishes_without_main_thread_writes():
    from pet.feature_probe_windows import ProbeInputWriter

    def failed(data):
        raise OSError("owned pipe broken")

    writer = ProbeInputWriter(failed, lambda: None)
    writer.send(b"hello\n")
    assert writer.finished.wait(5)
    assert isinstance(writer.error, OSError)


def test_probe_ownership_is_durable_unique_and_binds_root_and_bundle(tmp_path):
    from pet.feature_probe_windows import ProbeOwnership

    owner = ProbeOwnership(tmp_path, "a" * 64)
    doc = json.loads((tmp_path / "probe-ownership.json").read_bytes())
    assert doc["phase"] == "profile_intent"
    assert doc["root"] == str(tmp_path.absolute())
    assert doc["bundle_digest"] == "a" * 64
    assert doc["profile"] == owner.profile
    owner.update("running", pid=321)
    assert json.loads(owner.path.read_bytes())["pid"] == 321
    with pytest.raises(FileExistsError):
        ProbeOwnership(tmp_path, "a" * 64)


def test_canary_gate_requires_all_named_negative_and_positive_controls():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows-native fixture module")
    from scripts.validate_feature_probe_windows import CANARY_CHECKS, check_canary_results

    positive = dict.fromkeys(CANARY_CHECKS, True)
    negative = {name: name in {"helper_read", "candidate_read", "scratch_rw"} for name in CANARY_CHECKS}
    check_canary_results(positive, negative)
    with pytest.raises(AssertionError):
        check_canary_results({key: value for key, value in positive.items() if key != "inherited_file_access"}, negative)
    with pytest.raises(AssertionError):
        check_canary_results(positive, dict(negative, credential_read=True))
    with pytest.raises(AssertionError):
        check_canary_results(dict(positive, ipv4="true"), negative)


def test_probe_recovery_refuses_unowned_profile_before_native_api(tmp_path):
    from pet.feature_probe_windows import ProbeLaunchError, cleanup_owned_probe

    (tmp_path / "probe-ownership.json").write_text(
        json.dumps({"schema": 1, "root": str(tmp_path), "bundle_digest": "a" * 64, "profile": "unrelated.profile", "phase": "running"})
    )
    with pytest.raises(ProbeLaunchError, match="probe_ownership"):
        cleanup_owned_probe(tmp_path, "a" * 64)


def test_candidate_worker_target_requires_official_snapshot_and_detects_replacement(tmp_path):
    from pet.feature_probe_windows import ProbeLaunchError, VerifiedProbeWorker
    from tests.test_feature_package_transactions import _package

    root, verifier, _ = _package(tmp_path / "owned/candidate")
    descriptor = verifier.verify(root)
    target = VerifiedProbeWorker(descriptor, verifier)
    assert target.verify(tmp_path / "owned") == descriptor.worker_path
    with pytest.raises(ProbeLaunchError, match="worker_boundary"):
        target.verify(tmp_path / "different")
    descriptor.worker_path.write_bytes(b"replacement")
    with pytest.raises(ProbeLaunchError, match="worker_verification"):
        target.verify(tmp_path / "owned")


def test_input_cleanup_failure_still_releases_attributes_sid_and_profile():
    import threading

    from pet.feature_probe_windows import ProbeLaunchError, _finish_probe_resources

    calls = []

    class API:
        def delete_attributes(self, value):
            calls.append("attributes")

        def free_sid(self, value):
            calls.append("sid")

        def delete_profile(self, value):
            calls.append("profile")
            return 0

    class Owner:
        def update(self, phase, **details):
            calls.append(phase)

    class Writer:
        finished = threading.Event()

        def abort(self):
            calls.append("abort")

    class NeverFinished:
        def wait(self, timeout):
            return False

    writer = Writer()
    writer.finished = NeverFinished()
    with pytest.raises(ProbeLaunchError, match="probe_input_cleanup"):
        _finish_probe_resources(API(), Owner(), object(), object(), "own-profile", writer)
    assert calls == ["abort", "attributes", "sid", "profile", "cleanup_required"]
