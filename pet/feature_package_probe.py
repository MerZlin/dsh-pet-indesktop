"""Verified probe contract. No same-user subprocess fallback is permitted.

A Core-owned OS sandbox runner is deliberately required. Cleaning environment
variables, a temporary HOME, or Python audit hooks is not a security boundary
for arbitrary native Worker code. This module fails closed until that runner is
provided; it never quietly downgrades an isolation requirement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol

from .plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, VerifiedFeatureDescriptor

if TYPE_CHECKING:
    from .feature_package_transactions import RuntimePreparation


@dataclass(frozen=True)
class ProbeRequest:
    package_root: Path
    policy: Mapping[str, object]
    timeout: float
    deny_user_data: bool = True
    deny_network: bool = True
    deny_desktop: bool = True


@dataclass(frozen=True)
class ProbeOutcome:
    host_valid: bool
    worker_hello: bool
    worker_graceful_exit: bool
    isolation_enforced: bool
    reason: str | None = None
    worker_status: str = "verified"


class ProbeSandbox(Protocol):
    """Trusted Core launcher; both host and Worker must share OS isolation."""

    def run(self, request: ProbeRequest) -> ProbeOutcome: ...


class SubprocessFeatureSelfChecker:
    def __init__(self, verifier: FeaturePackageVerifier, *, sandbox: ProbeSandbox | None = None, timeout: float = 30.0):
        self.verifier = verifier
        self.sandbox = sandbox
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self.sandbox is not None

    def check(self, descriptor: VerifiedFeatureDescriptor) -> RuntimePreparation:
        from .feature_package_transactions import RuntimePreparation

        try:
            self.verifier.reverify(descriptor)
        except (PackageVerificationError, OSError):
            return RuntimePreparation("failed", "self_check_verification_failed")
        if self.sandbox is None:
            return RuntimePreparation("failed", "self_check_sandbox_unavailable")
        from .feature_probe_adapter import probe_policy

        policy = probe_policy(self.verifier)
        try:
            result = self.sandbox.run(ProbeRequest(descriptor.root, policy, self.timeout))
        except (OSError, TimeoutError):
            return RuntimePreparation("failed", "self_check_sandbox_failed")
        if not result.isolation_enforced:
            return RuntimePreparation("failed", "self_check_isolation_not_enforced")
        worker_valid = (
            result.worker_status == "not_applicable" and not result.worker_hello and not result.worker_graceful_exit
            if descriptor.execution_kind == "host-only"
            else result.worker_status == "verified" and result.worker_hello and result.worker_graceful_exit
        )
        if not (result.host_valid and worker_valid):
            return RuntimePreparation("failed", result.reason or "self_check_failed")
        return RuntimePreparation()


def verified_host_probe(package_root: Path, verifier: FeaturePackageVerifier) -> None:
    """Called only inside a sandbox; never uses package sys.path or real Core."""
    from .plugins.feature_host import FeatureDefinition
    from .plugins.feature_packages import FeaturePackageLoader

    descriptor = verifier.verify(package_root)
    if not verifier.accepts_descriptor(descriptor):
        raise PackageVerificationError("package activation policy rejected")
    handle = FeaturePackageLoader(verifier).load_host(descriptor)
    try:
        definition = handle.factory()
        if not isinstance(definition, FeatureDefinition) or definition.owner != descriptor.id or not callable(definition.settings_factory):
            raise PackageVerificationError("invalid official FeatureDefinition")
        if descriptor.execution_kind == "host-only" and (definition.worker_launch_factory is not None or definition.allow_in_process is not True):
            raise PackageVerificationError("host-only definition cannot request a Worker")
        # No runtime/settings factory, context, permissions or Core registration.
    finally:
        handle.close()


def validate_worker_transcript(transcript: bytes, *, graceful_returncode: int) -> bool:
    """Bounded minimum hello/shutdown protocol proof, with no task/config push.

    The sandbox launcher sends only shutdown after seeing hello, closes stdin,
    and owns the deadline/process-tree cleanup. A successful native exit alone
    is not a handshake. The launcher must not send screenshot/model requests.
    """
    from .workers.protocol import MAX_MESSAGE_BYTES, decode_message

    if len(transcript) > MAX_MESSAGE_BYTES * 4 or graceful_returncode != 0:
        return False
    hello = False
    try:
        for line in transcript.splitlines():
            if len(line) > MAX_MESSAGE_BYTES:
                return False
            message = decode_message(line)
            if message.worker_id != "proactive-screen" or message.type not in ("hello", "heartbeat"):
                return False
            if message.type == "hello":
                if hello:
                    return False
                hello = True
    except (ValueError, json.JSONDecodeError):
        return False
    return hello
