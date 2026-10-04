"""Application-owned bootstrap + asynchronous management, never GUI file writes."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from . import __version__, feature_build_policy, feature_distribution
from .feature_package_transactions import FeaturePackageTransactionService, OperationResult
from .plugins.package_trust import FeaturePackageVerifier


class FeatureManagementRuntime(QObject):
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    state_changed = Signal(object)
    _finished = Signal(object)

    def __init__(self, config, host, *, role="core", management_only=False):
        super().__init__()
        self.host, self.config, self.role = host, config, role
        self.builtin = feature_distribution.BUILTIN_SCREEN
        self.service = self.startup = self.endpoint = self.server = self.monitor = None
        self.last_result = OperationResult("idempotent", reason="builtin_core" if self.builtin else "not_installed")
        # Inspection is not an operation outcome. Retain a confirmed retry plan
        # in this process only; never persist its confirmation token in a journal.
        self.last_operation = None
        self._closed = threading.Event()
        self.busy = False
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        if self.builtin:
            return
        policy = feature_build_policy
        verifier = FeaturePackageVerifier(
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
                sandbox = WindowsFeatureProbeSandbox(verifier, bundle, config.dir / "feature-probe-runs")
                self.service.self_checker = SubprocessFeatureSelfChecker(verifier, sandbox=sandbox, timeout=30)
            except (OSError, ValueError, RuntimeError):
                # A missing/tampered helper forbids candidate execution. It must
                # not crash Core or change the currently installed state.
                self.last_result = OperationResult("rejected", reason="trusted_probe_unavailable")
        from .feature_lifecycle import FeatureLifecycleEndpoint, QueuedFeatureLifecycle
        from .feature_lifecycle_ipc import CrossProcessFeatureLifecycle, FeatureLifecycleServer

        self.endpoint = FeatureLifecycleEndpoint(self.service.store, host, self)
        self.server = FeatureLifecycleServer(self.endpoint, self)
        self.service.runtime = CrossProcessFeatureLifecycle(self.service, QueuedFeatureLifecycle(self.endpoint))
        if not management_only:
            from .feature_host_bindings import bind_screen_context
            from .feature_package_startup import ProductionFeatureStartup

            # Recover before candidate import. Recovery is metadata-only here;
            # GUI preparation remains queued and is retried after event-loop start.
            runtime = self.service.runtime
            self.service.runtime = None
            try:
                recovered = self.service.recover_pending()
            finally:
                self.service.runtime = runtime
            self.startup = ProductionFeatureStartup(
                self.service, host, runtime_directory=config.dir / "feature-runtime", role=role, context_factory=lambda: bind_screen_context(config)
            )
            self.last_result = self.startup.load_current() if recovered.status in ("completed", "idempotent", "awaiting_startup_confirmation") else recovered
        if self.last_result.status not in ("completed", "idempotent"):
            self.last_operation = self.last_result
        from .feature_state_monitor import FeatureStateMonitor

        self.monitor = FeatureStateMonitor(self.service.store, self)
        self.monitor.state_changed.connect(self._state_update)
        # Do not race the initial state read against the synchronous settings
        # factory's resolution. Startup/ports/settings finish on this GUI turn;
        # the monitor starts on the next turn and honors an intervening close.
        QTimer.singleShot(0, self._start_monitor)

    @Slot()
    def _start_monitor(self):
        if not self._closed.is_set() and self.monitor is not None:
            self.monitor.start()

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
            self.result_ready.emit(OperationResult("rejected", reason="builtin_core_not_physically_removable"))
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
            self.result_ready.emit(OperationResult("rejected", reason="management_command_invalid"))
            return False
        self.busy = True
        self.busy_changed.emit(True)

        def run():
            try:
                result = action()
            except Exception:
                result = OperationResult("failed", reason="management_background_failed")
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
        if self.monitor is not None:
            self.monitor.stop()
        if self.server is not None:
            self.server.close()
        if self.endpoint is not None:
            self.endpoint.close()
        # Host process pins are deliberately NOT released at GUI close.


def attach_feature_management(config, host, *, role="core", management_only=False):
    manager = getattr(host, "management_runtime", None)
    if manager is None:
        manager = FeatureManagementRuntime(config, host, role=role, management_only=management_only)
        host.management_runtime = manager
    return manager


def is_management_page(page):
    """Stable aliases; management-only startup deliberately does not import a host."""
    return str(page or "").strip().casefold() in {"extensions", "plugin-management", "扩展管理", "插件管理"}
