"""Minimal trusted Core removal entry; no desktop, AI, screenshots or Worker.

The installer owns exclusive Core code and data-root barriers through this
process and subsequent Core file deletion. Uninstall only validates that
handoff and removes integrations belonging to this executable. It neither
loads DLC nor reads/changes their state, configuration or personal data.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MAINTENANCE_LOG_NAME = "core-maintenance.log"
MAINTENANCE_SUCCESS_STATUSES = frozenset({"completed", "idempotent", "awaiting_startup_confirmation"})


def _write_maintenance_log(config, *, code: int, status: str, owners: tuple[str, ...], reason: str = "") -> None:
    """Append a bounded, non-secret maintenance receipt for Setup diagnosis."""

    try:
        path = Path(config.dir) / MAINTENANCE_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": "install-packages",
            "code": int(code),
            "status": str(status),
            "owners": list(owners),
            "reason": str(reason)[:512],
            "pid": os.getpid(),
            "time": time.time(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, TypeError, ValueError):
        # Setup's exit code remains the authoritative result. A log failure must
        # not turn a completed transaction into a false negative.
        return


def _write_core_removal_log(data_root, *, code: int, status: str, reason: str) -> None:
    """Append a bounded, non-secret receipt for Core-only uninstall diagnosis."""

    try:
        path = Path(data_root) / MAINTENANCE_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": "uninstall",
            "code": int(code),
            "status": str(status),
            "reason": str(reason)[:128],
            "pid": os.getpid(),
            "time": time.time(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, TypeError, ValueError):
        # The uninstall exit code remains authoritative. Diagnostics must not
        # turn a completed cleanup into a failure or create a new dependency.
        return


def _record_core_removal(layout, *, code: int, status: str, reason: str) -> int:
    data_root = getattr(layout, "data_root", None)
    if data_root is not None:
        _write_core_removal_log(data_root, code=code, status=status, reason=reason)
    return code


@dataclass(frozen=True)
class CoreRemovalPermit:
    """Native barrier/installation identity handoff, not an empty-DLC receipt."""

    layout: object


def _uninstall_owned_bridge_with_retry(executable: Path) -> bool:
    """Remove only this Core's bridge registration, tolerating a short shutdown race.

    DSH profile writers may still be closing their per-profile lock immediately
    after the user exits the desktop. A bounded retry gives best-effort
    cleanup a chance to finish that normal handoff race. Exhaustion is ignored
    by Core removal and never becomes an uninstall warning. Foreign profiles/references remain outside
    the scoped cleanup path and are never modified.
    """
    from .agent_link import DshMonitor

    root = Path(executable).absolute().parent / "_internal" / "integrations" / "dsh-pet-bridge"
    for attempt in range(3):
        if DshMonitor.uninstall_bridge(scope_root=root):
            return True
        if attempt < 2:
            time.sleep(0.05 * (attempt + 1))
    return False


def finish_core_removal(evidence, *, executable: Path, registry=None) -> int:
    from .core_registration_cleanup import remove_owned_autostart
    from .runtime_layout import current_layout

    layout = current_layout()
    if not isinstance(evidence, CoreRemovalPermit) or layout is None or evidence.layout is not layout:
        return 3

    if Path(layout.executable).absolute() != Path(executable).absolute():
        return _record_core_removal(
            layout,
            code=2,
            status="blocked",
            reason="core_executable_identity_mismatch",
        )

    # Association/autostart cleanup is deliberately best-effort.  It is not
    # part of the project-directory removal contract and must never turn a
    # successful Core uninstall into a blocking ``core_maintenance_incomplete``
    # warning.  The cleanup routines still get a chance to remove stale
    # registrations, but their result is intentionally ignored.
    try:
        _uninstall_owned_bridge_with_retry(executable)
    except Exception:
        pass
    try:
        remove_owned_autostart(executable, registry=registry)
    except Exception:
        pass
    return _record_core_removal(
        layout,
        code=0,
        status="completed",
        reason="core_removal_completed",
    )


def _maintenance_verifier(owner: str):
    from . import __version__, feature_build_policy
    from .plugins.package_trust import FeaturePackageVerifier

    return FeaturePackageVerifier(
        feature_id=owner,
        core_version=__version__,
        api_version=feature_build_policy.FEATURE_API_VERSION,
        allowed_capabilities=feature_build_policy.FEATURE_CAPABILITIES,
        trust_anchors={name: bytes.fromhex(key) for name, key in feature_build_policy.OFFICIAL_FEATURE_TRUST_ANCHORS},
        allow_local_packages=True,
    )


def _maintenance_service(config, owner: str, verifier):
    from . import feature_build_policy as policy
    from .feature_package_transactions import FeaturePackageTransactionService

    service = FeaturePackageTransactionService(config.dir, verifier)
    if sys.platform == "win32" and getattr(sys, "frozen", False) and policy.PROBE_BUNDLE_MANIFEST_SHA256:
        from .feature_package_probe import SubprocessFeatureSelfChecker
        from .feature_probe_adapter import WindowsFeatureProbeSandbox
        from .feature_probe_windows import TrustedProbeBundle

        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / policy.PROBE_BUNDLE_DIRECTORY
        bundle = TrustedProbeBundle(root, policy.PROBE_BUNDLE_MANIFEST_SHA256)
        bundle.verify()
        sandbox = WindowsFeatureProbeSandbox(verifier, bundle, config.dir / "feature-probe-runs" / owner)
        service.self_checker = SubprocessFeatureSelfChecker(verifier, sandbox=sandbox, timeout=30)
    return service


def run_install_packages(
    packages_root: Path,
    owners: tuple[str, ...],
    *,
    config=None,
    service_factory=None,
) -> int:
    """Install Setup-selected official packages without creating a Qt app.

    The installer supplies only embedded official ZIPs and owner ids. The Core
    routes each bounded manifest, pins the official registration, runs the
    normal verifier/self-check, then accepts the exact transaction plan. The
    accepted transaction statuses are deliberately explicit and are recorded in
    the core-maintenance.log file for Setup diagnosis.
    """
    from .config import Config
    from .local_package_intents import route_local_package
    from .official_features import OFFICIAL_FEATURES, official_feature
    from .plugins.package_trust import PackageVerificationError

    root = Path(packages_root)
    active_config = config

    def finish(code: int, status: str, reason: str = "") -> int:
        if active_config is not None:
            _write_maintenance_log(active_config, code=code, status=status, owners=owners, reason=reason)
        return code

    if (
        not root.is_absolute()
        or not root.is_dir()
        or not isinstance(owners, tuple)
        or not 1 <= len(owners) <= len(OFFICIAL_FEATURES)
        or len(set(owners)) != len(owners)
        or any(owner not in OFFICIAL_FEATURES for owner in owners)
    ):
        return finish(64, "rejected", "invalid_arguments")
    try:
        from .runtime_layout import RuntimeLayoutError, initialize_for_current_build

        try:
            initialize_for_current_build()
        except RuntimeLayoutError as exc:
            return finish(2, "blocked", str(exc))
        if active_config is None:
            active_config = Config()
        for owner in owners:
            source = root / (owner + ".zip")
            route = route_local_package(source)
            registration = official_feature(owner)
            if (
                route.feature_id != registration.id
                or route.factory != registration.factory
                or route.execution_kind != registration.execution_kind
                or not set(route.capabilities) <= registration.capabilities
            ):
                return finish(3, "failed", f"official_registration_mismatch:{owner}")
            verifier = _maintenance_verifier(owner)
            service = service_factory(owner, verifier, active_config) if service_factory is not None else _maintenance_service(active_config, owner, verifier)
            inspection = service.inspect()
            if inspection.pending_transaction is not None or inspection.status == "recovery_required":
                recovered = service.recover_pending()
                if recovered.status not in MAINTENANCE_SUCCESS_STATUSES:
                    return finish(3, "failed", f"recovery:{owner}:{recovered.status}")
                inspection = service.inspect()
            if inspection.active == route.version:
                state = service.store.read().state
                if state is None or state.versions.get(route.version) != route.manifest_digest:
                    return finish(3, "failed", f"active_digest_mismatch:{owner}")
                if not state.enabled:
                    enabled = service.set_enabled(True, expected_revision=state.revision)
                    if enabled.status not in {"completed", "idempotent"}:
                        return finish(3, "failed", f"enable:{owner}:{enabled.status}")
                continue
            command = service.preflight_upgrade if inspection.active is not None else service.preflight_install
            result = command(source)
            if result.status != "awaiting_confirmation" or result.plan is None:
                return finish(3, "failed", f"preflight:{owner}:{result.status}")
            applied = service.apply(result.plan, confirmation_token=result.plan.confirmation_token)
            if applied.status not in MAINTENANCE_SUCCESS_STATUSES:
                return finish(3, "failed", f"apply:{owner}:{applied.status}")
        return finish(0, "completed", "all_selected_packages_applied")
    except RuntimeError as exc:
        return finish(2, "blocked", str(exc))
    except (OSError, TypeError, ValueError, PackageVerificationError) as exc:
        return finish(3, "failed", f"{type(exc).__name__}:{exc}")


def run_uninstall() -> int:
    from .runtime_layout import RuntimeLayoutError, initialize_for_current_build

    try:
        layout = initialize_for_current_build(for_core_removal=True)
        if layout is None:
            return 64
    except RuntimeLayoutError:
        return 2
    # Inno already owns confirmation, the code lock and the removal barrier.
    # Do not construct Config/QApplication/FeatureHost or run any DLC transaction.
    return finish_core_removal(CoreRemovalPermit(layout), executable=Path(sys.executable))
