"""Trusted generated-fixture process for real production startup tests.

Normal Python here is the PRODUCTION startup, not a security self-check fallback.
No private key, personal profile, desktop capture, Worker, or network is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pet.config import Config
from pet.feature_host_bindings import bind_screen_context
from pet.feature_package_startup import ProductionFeatureStartup
from pet.feature_package_transactions import FeaturePackageTransactionService, OperationResult
from pet.plugins.feature_host import FeatureHost
from pet.plugins.package_trust import FeaturePackageVerifier, VerificationLimits


def main():
    document = json.loads(sys.stdin.buffer.read(65537))
    data_root = Path(document["data_root"])
    policy = document["policy"]
    policy["trust_anchors"] = {key: bytes.fromhex(value) for key, value in policy["trust_anchors"].items()}
    policy["limits"] = VerificationLimits(**policy["limits"])
    verifier = FeaturePackageVerifier(**policy)
    service = FeaturePackageTransactionService(data_root, verifier)
    host = FeatureHost()
    owner = verifier.feature_id
    runtime = data_root / "fixture-runtime"
    if not document.get("launch"):
        runtime.mkdir(exist_ok=True)
    cfg = Config(base=data_root / "fixture-profile")

    confirmation_lock = None

    def context():
        nonlocal confirmation_lock
        if document.get("confirmation_lock"):
            from pet.feature_state_io import open_kernel_lock

            confirmation_lock = open_kernel_lock(service.management_lock_path)
        if not document["success"]:
            raise ValueError("generated post-import port-binding failure")
        if owner == "official.screen-understanding":
            return bind_screen_context(cfg)
        # Fixture ports use generated configuration and no OS desktop/secret access.
        from pet.credentials import CredentialError, CredentialVaultPort
        from pet.feature_config import bind_feature_configuration, bind_feature_preferences
        from pet.feature_ports import FeatureHostContext

        def deny_secret(ref):
            raise CredentialError("fixture_has_no_secrets")

        return FeatureHostContext(
            owner,
            bind_feature_configuration(cfg, owner, journal_path=cfg.path.with_suffix(".ai-migration.json")),
            CredentialVaultPort(owner, "generated-fixture"),
            deny_secret,
            bind_feature_preferences(cfg, key="fixture_ai", fields=frozenset()),
            {},
            {},
            None,
        )

    if document.get("post_confirm_state_lock"):
        original_confirm = service.confirm_startup

        def confirm_then_contend(*args, **kwargs):
            nonlocal confirmation_lock
            result = original_confirm(*args, **kwargs)
            if result.status == "completed":
                from pet.feature_state_io import open_kernel_lock

                confirmation_lock = open_kernel_lock(service.store.lock_path)
            return result

        service.confirm_startup = confirm_then_contend

    if document.get("crash_step"):
        original = service.store.commit
        fired = False

        def crash_after(change, *, expected_revision, operation_id):
            nonlocal fired
            value = original(change, expected_revision=expected_revision, operation_id=operation_id)
            if operation_id.endswith("." + document["crash_step"]) and not fired:
                fired = True
                raise OSError("generated commit acknowledgment failure")
            return value

        service.store.commit = crash_after
    try:
        startup = ProductionFeatureStartup(service, host, runtime_directory=runtime, context_factory=context, role=document.get("role", "core"))
        result = startup.load_current() if document.get("mode") == "current" else startup.load_pending(document["operation_id"])
        retry_evidence = None
        if confirmation_lock is not None:
            first = result
            receipt = startup._pending_receipt
            lease_id = startup.binding.handle.process_pin.lease_id
            first_host_state = host.state(owner)
            confirmation_lock.close()
            pending = service.store.read().state.pending_transaction
            confirmation_lock = None
            result = startup.load_current()
            retry_evidence = {
                "first_status": first.status,
                "first_reason": first.reason,
                "first_host_state": first_host_state,
                "pending_before_retry": pending,
                "same_pin": startup.binding.handle.process_pin.lease_id == lease_id,
                "had_sealed_receipt": receipt is not None,
            }
        launch_evidence = None
        if document.get("launch"):
            launch = host._definitions[owner].worker_launch_factory()
            launch_evidence = {"runtime_exists": runtime.is_dir(), "program": launch.program, "cwd": launch.working_directory}
            launch.close()
        if document.get("mutate_authority"):
            from pet.feature_install_state import StateChange

            state = service.store.read().state
            service.store.commit(
                StateChange(dict(state.versions), state.active, state.previous, False),
                expected_revision=state.revision,
                operation_id="fixture-disable-authority",
            )
            # No monitor/event callback has run: stale execution must already fail.
            stale_enabled = host.enabled(owner)
            startup.refresh_authorization()
    except Exception:
        result = OperationResult("recovery_required", reason="startup_bootstrap_failed")
    print(
        json.dumps(
            {
                "status": result.status,
                "feature_id": result.feature_id,
                "desktop_granted": startup.context is not None and startup.context.desktop is not None,
                "operation_id": result.operation_id,
                "phase": result.phase,
                "revision": result.revision,
                "reason": result.reason,
                "host_state": host.state(owner),
                "bound": startup.binding is not None and startup.context is not None,
                "process_pin_live": startup.binding is not None
                and startup.binding.handle.process_pin is not None
                and not startup.binding.handle.process_pin.closed,
                "stale_enabled": locals().get("stale_enabled"),
                "launch": locals().get("launch_evidence"),
                "retry": locals().get("retry_evidence"),
                "crash_fired": locals().get("fired", False),
                "worker_leases": sum(
                    info.kind in ("worker", "worker_reservation") for info in service.leases.inspect_occupancy(service.store.read().state.active).leases
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
