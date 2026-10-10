"""Trusted transaction adapter for the fail-closed native Windows probe.

Each attempt owns two immutable copies; the candidate cannot replace the helper,
choose trust anchors or receive Core/user handles. No Qt and no source fallback.
Run directories are reclaimed through parent-owned bounded recovery; this code
never sweeps unknown directories or deletes user data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from . import feature_package_files as files
from .feature_package_probe import ProbeOutcome, ProbeRequest, validate_worker_transcript
from .feature_probe_materials import ProbeMaterialStore
from .feature_probe_windows import ProbeLaunchError, ProbeLimits, TrustedProbeBundle, VerifiedProbeWorker, WindowsProbeLauncher
from .feature_state_io import StateError, safe_path
from .plugins.package_trust import FeaturePackageVerifier, PackageVerificationError
from .workers.protocol import build_message, decode_message, encode_message


def probe_policy(verifier: FeaturePackageVerifier) -> dict[str, object]:
    return dict(
        feature_id=verifier.feature_id,
        anchor_policy={
            name: dict(
                feature_ids=sorted(policy.feature_ids), capabilities=sorted(policy.capabilities), revoked=policy.revoked, allow_legacy_v1=policy.allow_legacy_v1
            )
            for name, policy in verifier.anchor_policy.items()
        },
        core_version=verifier.core_version,
        api_version=verifier.api_version,
        platform=verifier.platform,
        allowed_capabilities=sorted(verifier.allowed_capabilities),
        trust_anchors={name: key.hex() for name, key in verifier.trust_anchors.items()},
        allow_developer_unsigned=False,
        allow_local_packages=verifier.allow_local_packages,
        limits=asdict(verifier.limits),
    )


def _digest(descriptor) -> str:
    return hashlib.sha256(descriptor.raw_manifest).hexdigest()


class WindowsFeatureProbeSandbox:
    """Configured by trusted Core/build policy, never by management UI input."""

    def __init__(self, verifier: FeaturePackageVerifier, bundle: TrustedProbeBundle, run_parent: Path):
        self.verifier, self.bundle = verifier, bundle
        self.run_parent = run_parent.absolute()
        self.materials = ProbeMaterialStore(self.run_parent, limits=verifier.limits)

    def collect_garbage(self):
        return self.materials.collect_garbage()

    def _materials(self, source, attempt: Path, kind: str):
        root = attempt / kind
        root.mkdir(exist_ok=False)
        # Both source and copy are verified. Copytree only consumes a closed,
        # reparse/hardlink-free helper inventory, pinned BEFORE and AFTER copy.
        self.bundle.verify()
        shutil.copytree(self.bundle.root, root / "helper")
        helper = TrustedProbeBundle(root / "helper", self.bundle.manifest_digest)
        helper.verify()
        self.bundle.verify()
        files.stage(source.root, "directory", root / "candidate", self.verifier.limits)
        candidate = self.verifier.verify(root / "candidate")
        self.verifier.reverify(source)
        if not self.verifier.accepts_descriptor(candidate) or candidate.raw_manifest != source.raw_manifest:
            raise PackageVerificationError("snapshot changed")
        return root, helper, candidate

    def run(self, request: ProbeRequest) -> ProbeOutcome:
        try:
            if not all((request.deny_network, request.deny_desktop, request.deny_user_data)) or dict(request.policy) != probe_policy(self.verifier):
                return ProbeOutcome(False, False, False, False, "probe_policy_mismatch")
            self.bundle.verify()
            original = self.verifier.verify(request.package_root)
            if not self.verifier.accepts_descriptor(original):
                raise PackageVerificationError("package activation policy rejected")
            with self.materials.lock():
                self.materials.recover_locked()
                attempt = self.materials.begin(original, self.bundle.manifest_digest)
                try:
                    return self._run_attempt(request, attempt)
                finally:
                    # Cleanup failures are retained warnings, not false probe
                    # success or a reason to disable a valid installed version.
                    self.materials.recover_locked()
        except ProbeLaunchError as exc:
            return ProbeOutcome(False, False, False, False, exc.reason)
        except StateError as exc:
            reason = "probe_materials_in_use" if exc.code == "lock_busy" else exc.code
            return ProbeOutcome(False, False, False, False, reason)
        except (OSError, PackageVerificationError):
            return ProbeOutcome(False, False, False, False, "probe_verification_failed")

    def _run_attempt(self, request: ProbeRequest, owned) -> ProbeOutcome:
        host_valid = worker_hello = worker_exit = isolation = False
        try:
            if not all((request.deny_network, request.deny_desktop, request.deny_user_data)) or dict(request.policy) != probe_policy(self.verifier):
                return ProbeOutcome(False, False, False, False, "probe_policy_mismatch")
            limits = ProbeLimits(timeout=request.timeout)
            self.bundle.verify()
            original = self.verifier.verify(request.package_root)
            if not self.verifier.accepts_descriptor(original):
                raise PackageVerificationError("package activation policy rejected")
            safe_path(self.run_parent)
            self.run_parent.mkdir(parents=True, exist_ok=True)
            attempt = owned.root
            attempt.mkdir(exist_ok=False)
            self.materials.mark(owned, "host", "copying")
            root, helper, candidate = self._materials(original, attempt, "host")
            document = dict(
                schema=1,
                package_root=str(candidate.root),
                manifest_digest=_digest(candidate),
                policy=probe_policy(self.verifier),
                snapshot_ancestors=candidate.ancestors,
            )
            self.materials.mark(owned, "host", "launching")
            result = WindowsProbeLauncher(helper, limits=limits).run(
                root, arguments=("--host-probe",), input_data=(json.dumps(document, separators=(",", ":")) + "\n").encode()
            )
            self.materials.mark(owned, "host", "returned")
            isolation = result.evidence.isolation_enforced
            self.verifier.reverify(candidate)  # includes full ancestry, outside the sandbox
            self.verifier.reverify(original)
            helper.verify()
            if not isolation:
                return ProbeOutcome(False, False, False, False, "probe_isolation_not_enforced")
            if result.reason or result.returncode != 0 or len(result.stdout) > 64 * 1024 or len(result.stdout.splitlines()) != 1:
                return ProbeOutcome(False, False, False, isolation, result.reason or "host_probe_failed")
            receipt = json.loads(result.stdout)
            expected = dict(schema=1, kind="host_valid", version=candidate.version, manifest_digest=_digest(candidate))
            if receipt != expected:
                return ProbeOutcome(False, False, False, isolation, "host_probe_receipt")
            host_valid = True
            if original.execution_kind == "host-only":
                # A signed host-only contract has no Worker. Do not fake HELLO.
                return ProbeOutcome(True, False, False, isolation, worker_status="not_applicable")
            self.materials.mark(owned, "worker", "copying")
            root, helper, candidate = self._materials(original, attempt, "worker")
            seen = False
            worker_id = None

            def hello_shutdown(line: bytes) -> bytes:
                nonlocal seen, worker_id
                try:
                    message = decode_message(line)
                except ValueError as exc:
                    raise ProbeLaunchError("worker_probe_protocol") from exc
                if seen or message.worker_id not in (candidate.id, "proactive-screen") or message.type != "hello" or message.payload != {"probe": True, "capabilities": []}:
                    raise ProbeLaunchError("worker_probe_protocol")
                seen = True
                worker_id = message.worker_id
                # No task/configuration paths, screenshots or model permissions.
                return encode_message(build_message(worker_id, "shutdown"))

            self.materials.mark(owned, "worker", "launching")
            result = WindowsProbeLauncher(helper, limits=limits).run(
                root, arguments=("--feature-package-probe",), candidate_worker=VerifiedProbeWorker(candidate, self.verifier), on_stdout_line=hello_shutdown
            )
            self.materials.mark(owned, "worker", "returned")
            isolation = isolation and result.evidence.isolation_enforced
            self.verifier.reverify(candidate)
            self.verifier.reverify(original)
            helper.verify()
            worker_hello = seen and validate_worker_transcript(result.stdout, graceful_returncode=0, worker_id=worker_id)
            worker_exit = worker_hello and result.returncode == 0 and not result.reason
            return ProbeOutcome(
                host_valid, worker_hello, worker_exit, isolation, result.reason or (None if worker_exit and isolation else "worker_probe_failed")
            )
        except ProbeLaunchError as exc:
            return ProbeOutcome(host_valid, False, False, isolation, exc.reason)
        except (StateError, OSError, PackageVerificationError):
            return ProbeOutcome(host_valid, False, False, isolation, "probe_verification_failed")
        except (ValueError, TypeError, KeyError):
            return ProbeOutcome(host_valid, False, False, isolation, "probe_invalid_result")
