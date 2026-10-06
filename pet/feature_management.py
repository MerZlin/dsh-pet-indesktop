"""Application-owned bootstrap + asynchronous management, never GUI file writes."""

from __future__ import annotations

import sys
import threading
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from . import __version__, feature_build_policy, feature_distribution
from .feature_package_transactions import LOCK_BUSY_REASONS, FeaturePackageTransactionService, OperationResult
from .official_features import SCREEN_FEATURE_ID, official_feature
from .plugins.package_trust import FeaturePackageVerifier, SigningKeyPolicy


class FeatureManagementRuntime(QObject):
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    state_changed = Signal(object)
    probe_cleanup_changed = Signal(object)
    _probe_finished = Signal(object)
    _finished = Signal(object)

    def __init__(self, config, host, *, feature_id=SCREEN_FEATURE_ID, role="core", management_only=False):
        super().__init__()
        self.host, self.config, self.role = host, config, role
        self.feature_id = official_feature(feature_id).id
        self.builtin = feature_distribution.BUILTIN_SCREEN if feature_id == SCREEN_FEATURE_ID else feature_distribution.BUILTIN_AI
        self.service = self.startup = self.endpoint = self.server = self.monitor = None
        self.last_result = self._result("idempotent", reason="builtin_core" if self.builtin else "not_installed")
        # Inspection is not an operation outcome. Retain a confirmed retry plan
        # in this process only; never persist its confirmation token in a journal.
        self.last_operation = None
        self._closed = threading.Event()
        self.busy = False
        self._probe_sandbox = None
        self.probe_cleanup = ()
        self._management_only = management_only
        self._bootstrap_attempts = 0
        self._bootstrap_timer = QTimer(self)
        self._bootstrap_timer.setSingleShot(True)
        self._bootstrap_timer.timeout.connect(self._retry_bootstrap)
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._probe_finished.connect(self._deliver_probe_cleanup, Qt.ConnectionType.QueuedConnection)
        if self.builtin:
            return
        policy = feature_build_policy
        verifier = FeaturePackageVerifier(
            feature_id=feature_id,
            anchor_policy={key: SigningKeyPolicy(**scope) for key, scope in policy.OFFICIAL_FEATURE_KEY_POLICIES},
            core_version=__version__,
            api_version=policy.FEATURE_API_VERSION,
            allowed_capabilities=policy.FEATURE_CAPABILITIES,
            trust_anchors={name: bytes.fromhex(key) for name, key in policy.OFFICIAL_FEATURE_TRUST_ANCHORS},
        )
        self.service = FeaturePackageTransactionService(config.dir, verifier)
        # The frozen, Core-owned digest is mandatory; neither source nor a user
        # environment override can choose the helper or trust policy.
        if sys.platform == "win32" and getattr(sys, "frozen", False) and policy.PROBE_BUNDLE_MANIFEST_SHA256:
            from .feature_package_probe import SubprocessFeatureSelfChecker
            from .feature_probe_adapter import WindowsFeatureProbeSandbox
            from .feature_probe_windows import TrustedProbeBundle

            root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / policy.PROBE_BUNDLE_DIRECTORY
            bundle = TrustedProbeBundle(root, policy.PROBE_BUNDLE_MANIFEST_SHA256)
            try:
                bundle.verify()
                sandbox = WindowsFeatureProbeSandbox(verifier, bundle, config.dir / "feature-probe-runs" / feature_id)
                self.service.self_checker = SubprocessFeatureSelfChecker(verifier, sandbox=sandbox, timeout=30)
                self._probe_sandbox = sandbox
                QTimer.singleShot(0, self._start_probe_recovery)
            except (OSError, ValueError, RuntimeError):
                # A missing/tampered helper forbids candidate execution. It must
                # not crash Core or change the currently installed state.
                self.last_result = self._result("rejected", reason="trusted_probe_unavailable")
        from .feature_lifecycle import FeatureLifecycleEndpoint, QueuedFeatureLifecycle
        from .feature_lifecycle_ipc import CrossProcessFeatureLifecycle, FeatureLifecycleServer

        self.endpoint = FeatureLifecycleEndpoint(self.service.store, host, self)
        self.server = FeatureLifecycleServer(self.endpoint, self)
        self.service.runtime = CrossProcessFeatureLifecycle(self.service, QueuedFeatureLifecycle(self.endpoint))
        if not management_only:
            from .feature_host_bindings import bind_ai_context, bind_screen_context
            from .feature_package_startup import ProductionFeatureStartup

            # Recover before candidate import. Recovery is metadata-only here;
            # GUI preparation remains queued and is retried after event-loop start.
            runtime = self.service.runtime
            self.service.runtime = None
            try:
                recovered = self.service.recover_pending()
            finally:
                self.service.runtime = runtime
            context_factory = bind_screen_context if feature_id == SCREEN_FEATURE_ID else bind_ai_context
            runtime_directory = config.dir / "feature-runtime"
            if feature_id != SCREEN_FEATURE_ID:
                runtime_directory /= feature_id
            self.startup = ProductionFeatureStartup(
                self.service, host, runtime_directory=runtime_directory, role=role, context_factory=lambda: context_factory(config)
            )
            self.last_result = self.startup.load_current() if recovered.status in ("completed", "idempotent", "awaiting_startup_confirmation") else recovered
        if self.last_result.status not in ("completed", "idempotent"):
            self.last_operation = self.last_result
        self._schedule_bootstrap_retry()
        from .feature_state_monitor import FeatureStateMonitor

        self.monitor = FeatureStateMonitor(self.service.store, self)
        self.monitor.state_changed.connect(self._state_update)
        # Do not race the initial state read against the synchronous settings
        # factory's resolution. Startup/ports/settings finish on this GUI turn;
        # the monitor starts on the next turn and honors an intervening close.
        QTimer.singleShot(0, self._start_monitor)

    def _result(self, *args, **kwargs):
        if "feature_id" in kwargs:
            raise TypeError("result identity is owned by the service")
        kwargs["feature_id"] = self.feature_id
        return OperationResult(*args, **kwargs)

    def _collect_probe_cleanup(self, sandbox):
        from .feature_probe_materials import ProbeMaterialCleanup

        try:
            outcomes = sandbox.collect_garbage()
        except Exception:
            outcomes = (ProbeMaterialCleanup("recovery_required", reason="probe_materials_io_error"),)
        if not self._closed.is_set():
            try:
                self._probe_finished.emit(outcomes)
            except RuntimeError:
                pass
        return outcomes

    @Slot()
    def _start_probe_recovery(self):
        if self._closed.is_set() or self._probe_sandbox is None:
            return
        # This is a separate bounded maintenance lease, not the management
        # transaction lock. No GUI object or application state enters cleanup.
        sandbox = self._probe_sandbox
        threading.Thread(target=lambda: self._collect_probe_cleanup(sandbox), name="feature-probe-maintenance", daemon=False).start()

    @Slot(object)
    def _deliver_probe_cleanup(self, outcomes):
        if not self._closed.is_set():
            self.probe_cleanup = tuple(outcomes)
            self.probe_cleanup_changed.emit(self.probe_cleanup)

    @Slot()
    def _start_monitor(self):
        if not self._closed.is_set() and self.monitor is not None:
            self.monitor.start()

    def _schedule_bootstrap_retry(self):
        # A bounded backoff is for kernel contention only. Invalid evidence,
        # import failures and occupied versions are not silently retried.
        delays = (100, 250, 500, 1000, 2000, 4000, 5000)
        if (
            self._closed.is_set()
            or self._management_only
            or self.startup is None
            or self.last_result.reason not in LOCK_BUSY_REASONS
            or self._bootstrap_timer.isActive()
            or self._bootstrap_attempts >= len(delays)
        ):
            return
        self._bootstrap_timer.start(delays[self._bootstrap_attempts])
        self._bootstrap_attempts += 1

    @Slot()
    def _retry_bootstrap(self):
        if self._closed.is_set() or self._management_only or self.startup is None or self.service is None:
            return
        if self.startup.binding is None:
            runtime = self.service.runtime
            self.service.runtime = None
            try:
                recovered = self.service.recover_pending()
            finally:
                self.service.runtime = runtime
            result = self.startup.load_current() if recovered.status in ("completed", "idempotent", "awaiting_startup_confirmation") else recovered
        else:
            # Reuse a real sealed receipt; never import a replacement host.
            result = self.startup.load_current()
        self.last_result = result
        if result.status not in ("completed", "idempotent"):
            self.last_operation = result
        self.result_ready.emit(result)
        self._schedule_bootstrap_retry()

    @Slot(object)
    def _state_update(self, state_result):
        if self._closed.is_set():
            return
        if self.startup is not None and self.startup.binding is not None:
            self.startup.refresh_authorization()
        self.state_changed.emit(state_result)

    def submit(self, command: str, value=None, *, confirmation_token=None, expected_revision=None):
        self.host.registry.check_thread()
        if self._closed.is_set() or self.busy:
            return False
        if self.service is None:
            self.result_ready.emit(self._result("rejected", reason="builtin_core_not_physically_removable"))
            return False
        service = self.service
        actions = {
            "inspect": service.inspect,
            "recover": service.recover_pending,
            "gc": service.collect_garbage,
            "rollback": service.preflight_rollback,
            "uninstall": service.preflight_uninstall,
            "install": lambda: service.preflight_install(value),
            "upgrade": lambda: service.preflight_upgrade(value),
            "apply": lambda: service.apply(value, confirmation_token=confirmation_token),
            "cancel": lambda: service.cancel_preflight(value),
            "enable": lambda: service.set_enabled(value, expected_revision=expected_revision),
        }
        action = actions.get(command)
        if action is None:
            self.result_ready.emit(self._result("rejected", reason="management_command_invalid"))
            return False
        self.busy = True
        self.busy_changed.emit(True)

        def run():
            try:
                result = action()
                if command in ("recover", "gc") and self._probe_sandbox is not None:
                    outcomes = self._collect_probe_cleanup(self._probe_sandbox)
                    if isinstance(result, OperationResult):
                        result = replace(result, details={**dict(result.details), "probe_cleanup": tuple(asdict(row) for row in outcomes)})
            except Exception:
                result = self._result("failed", reason="management_background_failed")
            if not self._closed.is_set():
                try:
                    self._finished.emit(result)
                except RuntimeError:
                    pass

        # Closing UI never forcibly stops an accepted transaction. This owner
        # remains alive through the bounded job; no Qt/GUI object enters run().
        threading.Thread(target=run, name="feature-management", daemon=False).start()
        return True

    @Slot(object)
    def _deliver(self, result):
        if self._closed.is_set():
            return
        self.busy = False
        self.last_result = result
        if isinstance(result, OperationResult):
            self.last_operation = result
        self.busy_changed.emit(False)
        self.result_ready.emit(result)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._bootstrap_timer.stop()
        if self.monitor is not None:
            self.monitor.stop()
        if self.server is not None:
            self.server.close()
        if self.endpoint is not None:
            self.endpoint.close()
        # Host process pins are deliberately NOT released at GUI close.


def attach_feature_management(config, host, *, feature_id=SCREEN_FEATURE_ID, role="core", management_only=False):
    official_feature(feature_id)
    managers = getattr(host, "management_runtimes", None)
    if managers is None:
        managers = host.management_runtimes = {}
    manager = managers.get(feature_id)
    if manager is None:
        manager = FeatureManagementRuntime(config, host, feature_id=feature_id, role=role, management_only=management_only)
        managers[feature_id] = manager
        if feature_id == SCREEN_FEATURE_ID:
            host.management_runtime = manager
    return manager


def attach_official_management(config, host, *, role="core", management_only=False):
    """Core-owned closed list; state directories never discover executable owners."""
    from .official_features import AI_FEATURE_ID

    return {
        owner: attach_feature_management(config, host, feature_id=owner, role=role, management_only=management_only)
        for owner in (SCREEN_FEATURE_ID, AI_FEATURE_ID)
    }


def close_official_management(host):
    """Stop every observer/control endpoint, retaining imported native pins."""
    for manager in tuple(getattr(host, "management_runtimes", {}).values()):
        manager.close()


def is_management_page(page):
    """Stable aliases; management-only startup deliberately does not import a host."""
    return str(page or "").strip().casefold() in {"extensions", "plugin-management", "扩展管理", "插件管理"}
