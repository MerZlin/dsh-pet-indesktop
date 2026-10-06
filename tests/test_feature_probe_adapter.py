"""Contract tests mock only the native OS launch boundary, not package trust."""

import hashlib
import json
from dataclasses import replace

import pytest

from pet.feature_package_probe import ProbeRequest
from pet.feature_probe_windows import LaunchEvidence, NativeProbeResult, TrustedProbeBundle
from tests.test_feature_package_transactions import _package


def _fixture(tmp_path, monkeypatch, *, limits=None):
    from pet.feature_probe_adapter import WindowsFeatureProbeSandbox, probe_policy

    source, verifier, _ = _package(tmp_path / "source")
    if limits is not None:
        verifier = replace(verifier, limits=limits)
    helper = tmp_path / "core-helper"
    helper.mkdir()
    (helper / "probe.exe").write_bytes(b"trusted test fixture")
    manifest = {"schema": 1, "entry": "probe.exe", "files": {"probe.exe": hashlib.sha256((helper / "probe.exe").read_bytes()).hexdigest()}}
    (helper / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = TrustedProbeBundle(helper, hashlib.sha256((helper / "bundle.json").read_bytes()).hexdigest())
    calls = []
    evidence = LaunchEvidence(True, True, True, True, True)
    behavior = {}

    class Launcher:
        def __init__(self, bundle, *, limits):
            self.bundle, self.limits = bundle, limits

        def run(self, root, **kwargs):
            self.bundle.verify()
            calls.append((root, kwargs))
            if "candidate_worker" not in kwargs:
                doc = json.loads(kwargs["input_data"])
                assert doc["policy"] == probe_policy(verifier)
                descriptor = verifier.verify(root / "candidate")
                assert doc["snapshot_ancestors"] == [list(item) for item in descriptor.ancestors]
                output = (
                    json.dumps(
                        dict(schema=1, kind="host_valid", version=descriptor.version, manifest_digest=hashlib.sha256(descriptor.raw_manifest).hexdigest())
                    ).encode()
                    + b"\n"
                )
                return NativeProbeResult(behavior.get("host_code", 0), behavior.get("host_output", output), b"", behavior.get("evidence", evidence))
            target = kwargs["candidate_worker"]
            assert target.verify(root).is_file()
            assert kwargs["arguments"] == ("--feature-package-probe",)
            assert "input_data" not in kwargs
            from pet.workers.protocol import build_message, decode_message, encode_message

            hello = encode_message(build_message("proactive-screen", "hello", {"probe": True, "capabilities": []}))
            reply = kwargs["on_stdout_line"](behavior.get("hello", hello).rstrip(b"\n"))
            assert decode_message(reply).type == "shutdown"
            assert decode_message(reply).payload == {}
            if behavior.get("tamper"):
                target.descriptor.worker_path.write_bytes(b"changed after execution")
            return NativeProbeResult(0, hello, b"", evidence)

    monkeypatch.setattr("pet.feature_probe_adapter.WindowsProbeLauncher", Launcher)
    sandbox = WindowsFeatureProbeSandbox(verifier, bundle, tmp_path / "probe-runs")
    return sandbox, ProbeRequest(source, probe_policy(verifier), 30), calls, behavior, bundle


def test_adapter_uses_separate_verified_owned_snapshots_and_minimum_protocol(tmp_path, monkeypatch):
    sandbox, request, calls, behavior, bundle = _fixture(tmp_path, monkeypatch)
    result = sandbox.run(request)
    assert result.host_valid and result.worker_hello and result.worker_graceful_exit and result.isolation_enforced
    assert len(calls) == 2 and calls[0][0] != calls[1][0]
    assert all(root.is_relative_to(tmp_path / "probe-runs") for root, _ in calls)
    assert request.package_root.exists()


@pytest.mark.parametrize("changed", ["deny_network", "deny_desktop", "deny_user_data"])
def test_adapter_cannot_weaken_isolation_request(tmp_path, monkeypatch, changed):
    sandbox, request, calls, behavior, bundle = _fixture(tmp_path, monkeypatch)
    result = sandbox.run(replace(request, **{changed: False}))
    assert result.reason == "probe_policy_mismatch" and not calls


def test_adapter_rejects_foreign_trust_policy_without_execution(tmp_path, monkeypatch):
    sandbox, request, calls, behavior, bundle = _fixture(tmp_path, monkeypatch)
    result = sandbox.run(replace(request, policy={**request.policy, "allow_developer_unsigned": True}))
    assert result.reason == "probe_policy_mismatch" and not calls


@pytest.mark.parametrize("target", ["manifest.sig", "worker/screen-worker.exe"])
def test_adapter_rejects_invalid_candidate_before_helper(tmp_path, monkeypatch, target):
    sandbox, request, calls, behavior, bundle = _fixture(tmp_path, monkeypatch)
    (request.package_root / target).write_bytes(b"invalid")
    assert not sandbox.run(request).host_valid and not calls


def test_adapter_rejects_changed_trusted_bundle_before_execution(tmp_path, monkeypatch):
    sandbox, request, calls, behavior, bundle = _fixture(tmp_path, monkeypatch)
    (bundle.root / "probe.exe").write_bytes(b"replaced")
    assert sandbox.run(request).reason == "bundle_integrity" and not calls


@pytest.mark.parametrize(
    "behavior",
    [
        {"host_code": 1},
        {"host_output": b'{"kind":"host_valid","schema":1,"version":"9","manifest_digest":"forged"}\n'},
        {"evidence": LaunchEvidence(True, True, True, False, True)},
    ],
)
def test_host_failure_or_forged_receipt_never_starts_worker(tmp_path, monkeypatch, behavior):
    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch)
    options.update(behavior)
    result = sandbox.run(request)
    assert not result.host_valid and len(calls) == 1


def test_post_launch_snapshot_mutation_never_reports_success(tmp_path, monkeypatch):
    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch)
    options["tamper"] = True
    result = sandbox.run(request)
    assert not result.worker_graceful_exit and result.reason == "probe_verification_failed"


def test_non_probe_worker_hello_is_rejected(tmp_path, monkeypatch):
    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch)
    from pet.workers.protocol import build_message, encode_message

    options["hello"] = encode_message(build_message("proactive-screen", "hello", {"capabilities": ["screen.capture"]}))
    result = sandbox.run(request)
    assert not result.worker_hello and result.reason == "worker_probe_protocol"
