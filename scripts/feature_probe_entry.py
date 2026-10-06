"""Core-owned frozen headless probe entry; never runs a real user context."""

from __future__ import annotations

import hashlib
import json
import sys

from pet.frozen_runtime_paths import activate_frozen_dependency_path

activate_frozen_dependency_path()


def sandbox_enforced() -> bool:
    try:
        import _dsh_probe_native

        return _dsh_probe_native.sandbox_enforced() is True
    except (ImportError, OSError, AttributeError):
        return False


def host_probe(source, output) -> int:
    # Defense in depth; the trusted parent verifies the actual token/Job BEFORE
    # resume. This check also prohibits standalone/same-user helper execution.
    if not sandbox_enforced():
        return 77
    stage = "policy"
    try:
        raw = source.readline(64 * 1024 + 1)
        if len(raw.encode("utf-8")) > 64 * 1024 or not raw.endswith("\n"):
            raise ValueError("policy bound")
        doc = json.loads(raw)
        if set(doc) != {"schema", "package_root", "manifest_digest", "policy", "snapshot_ancestors"} or doc["schema"] != 1:
            raise ValueError("policy schema")
        policy = doc["policy"]
        required = {
            "core_version",
            "api_version",
            "platform",
            "allowed_capabilities",
            "trust_anchors",
            "allow_developer_unsigned",
            "limits",
            "feature_id",
            "anchor_policy",
        }
        if set(policy) == required - {"feature_id", "anchor_policy"}:
            # Compatibility for explicitly trusted v1 screen probe callers.
            # The candidate cannot supply this parent-owned, read-only policy.
            policy = {**policy, "feature_id": "official.screen-understanding", "anchor_policy": {}}
        if set(policy) != required or policy["allow_developer_unsigned"] is not False:
            raise ValueError("official trust required")
        stage = "verifier_import"
        from pathlib import Path

        from pet.feature_package_probe import verified_host_probe
        from pet.feature_probe_crypto import HeadlessFeaturePackageVerifier
        from pet.plugins.package_trust import VerificationLimits

        verifier = HeadlessFeaturePackageVerifier(
            feature_id=policy["feature_id"],
            anchor_policy=policy["anchor_policy"],
            core_version=policy["core_version"],
            api_version=policy["api_version"],
            platform=policy["platform"],
            allowed_capabilities=policy["allowed_capabilities"],
            trust_anchors={name: bytes.fromhex(key) for name, key in policy["trust_anchors"].items()},
            limits=VerificationLimits(**policy["limits"]),
            allow_developer_unsigned=False,
            snapshot_ancestors=doc["snapshot_ancestors"],
        )
        package = Path(doc["package_root"])
        stage = "verification"
        descriptor = verifier.verify(package)
        manifest_digest = hashlib.sha256(descriptor.raw_manifest).hexdigest()
        if manifest_digest != doc["manifest_digest"]:
            raise ValueError("candidate digest changed")
        stage = "factory"
        verified_host_probe(package, verifier)
        result = {"schema": 1, "kind": "host_valid", "manifest_digest": manifest_digest, "version": descriptor.version}
        output.write(json.dumps(result, sort_keys=True) + "\n")
        output.flush()
        return 0
    except Exception as exc:
        cause_filename = str(getattr(exc.__cause__, "filename", ""))
        seam = None
        trace = getattr(exc.__cause__, "__traceback__", None)
        while trace is not None:
            code = trace.tb_frame.f_code
            if code.co_filename.endswith("package_trust.py") and code.co_name in {"_checked_stat", "_inventory", "_read_file"}:
                seam = code.co_name
            trace = trace.tb_next
        # No exception text: signed code may put user material in its exception.
        output.write(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "host_rejected",
                    "reason": type(exc).__name__,
                    "stage": stage,
                    "module": exc.name
                    if isinstance(exc, ImportError) and exc.name in {"nacl._sodium", "_cffi_backend", "nacl.signing", "nacl.bindings"}
                    else None,
                    "boundary": (
                        "ancestor" if cause_filename and cause_filename.casefold() in {str(parent).casefold() for parent in package.parents} else "candidate"
                    )
                    if stage == "verification"
                    else None,
                    "winerror": getattr(exc.__cause__, "winerror", None),
                    "filesystem_seam": seam,
                    "failed_path_length": len(cause_filename),
                    "failed_relative_path": cause_filename.removeprefix(str(package) + "\\")
                    if stage == "verification" and cause_filename.startswith(str(package) + "\\")
                    else None,
                    "verification_code": (
                        "signature"
                        if str(exc).startswith("signature has no explicitly")
                        else "filesystem"
                        if str(exc).startswith("invalid/inaccessible feature package")
                        else "other"
                    )
                    if stage == "verification"
                    else None,
                }
            )
            + "\n"
        )
        output.flush()
        return 78


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--host-probe"]:
        return host_probe(sys.stdin, sys.stdout)
    # Trusted permission fixture mode, NOT candidate execution or source fallback.
    from scripts.feature_probe_canary import main as canary_main

    return canary_main()


if __name__ == "__main__":
    raise SystemExit(main())
