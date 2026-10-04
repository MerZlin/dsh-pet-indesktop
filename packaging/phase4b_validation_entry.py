"""Frozen Phase4B validation entry: real Core/bootstrap/settings/management UI.

This entry is embedded ONLY in explicitly marked validation builds. It never
binds a descriptor or writes the installation ledger. The test public anchor is
compiled into the owned build snapshot; no private key or environment trust.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path


def close_valid_widget(widget):
    """AppShell may have already deleted its child on aboutToQuit."""
    import shiboken6

    if widget is not None and shiboken6.isValid(widget):
        widget.close()


def reply_validation_budget(supervisor, message, pending):
    """Only our generated automatic analysis; not a release quota bypass."""
    payload = message.payload
    arguments = payload.get("arguments")
    if (
        message.worker_id != "proactive-screen"
        or message.type != "request"
        or not message.request_id
        or payload.get("operation") != "budget_check"
        or type(payload.get("generation")) is not int
        or payload["generation"] != 1
        or not isinstance(arguments, dict)
        or arguments.get("kind") != "automatic"
        or pending.get(arguments.get("parent_request_id")) != "analyze_frame"
    ):
        raise ValueError("invalid validation budget request")
    return supervisor.send_response(message.request_id, "budget_check", {"status": "ok", "result": {"allowed": True}}, generation=1)


def retryable_validation_result(result, attempts):
    """Exercise the actual safe-retry button, never fabricate acceptance."""
    return result.status == "failed" and result.reason == "management_lock_busy" and result.plan is not None and attempts < 3


class ValidationPerformance:
    """Observation only, in explicit validation builds; original calls unchanged."""

    def __init__(self):
        import psutil

        self.process = psutil.Process()
        self.phases = []

    def snapshot(self):
        process = self.process
        return {
            "clock": time.perf_counter(),
            "cpu": sum(process.cpu_times()[:2]),
            "rss": process.memory_info().rss,
            "threads": process.num_threads(),
            "io": process.io_counters()._asdict(),
        }

    def call(self, name, function, *args, **kwargs):
        before = self.snapshot()
        error = None
        try:
            return function(*args, **kwargs)
        except BaseException as exception:
            error = type(exception).__name__
            raise
        finally:
            after = self.snapshot()
            self.phases.append(
                {
                    "name": name,
                    "duration_ms": (after["clock"] - before["clock"]) * 1000,
                    "cpu_ms": (after["cpu"] - before["cpu"]) * 1000,
                    "rss_before": before["rss"],
                    "rss_after": after["rss"],
                    "threads_before": before["threads"],
                    "threads_after": after["threads"],
                    "io": {key: after["io"][key] - value for key, value in before["io"].items()},
                    "error": error,
                }
            )

    def observe_method(self, cls, method):
        original = getattr(cls, method)

        def observed(*args, **kwargs):
            return self.call(cls.__name__ + "." + method, original, *args, **kwargs)

        setattr(cls, method, observed)


def main(argv=None):
    from validation_config import ENABLE_CHAT, VALIDATION_ONLY

    from pet.feature_build_policy import VALIDATION_BUILD

    if VALIDATION_ONLY is not True or VALIDATION_BUILD is not True or not getattr(sys, "frozen", False):
        raise RuntimeError("explicit frozen validation build required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--mode", choices=("core", "settings", "manager"), default="core")
    parser.add_argument(
        "--action",
        choices=("inspect", "install", "uninstall", "rollback", "enable", "disable", "recover", "request", "request-disable", "verify-disabled", "configure"),
        default="inspect",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--width", type=int, choices=(720, 900, 1100), default=900)
    args = parser.parse_args(argv)
    if args.action == "install" and args.source is None:
        parser.error("install requires source")
    import psutil
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell
    from pet.config import Config
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.feature_package_transactions import Inspection, OperationResult
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.plugins.feature_host import FeatureHost

    performance = ValidationPerformance()
    from pet.feature_package_startup import ProductionFeatureStartup
    from pet.feature_package_transactions import FeaturePackageTransactionService
    from pet.feature_probe_adapter import WindowsFeatureProbeSandbox

    for method in ("preflight_install", "preflight_upgrade", "preflight_uninstall", "preflight_rollback", "apply", "recover_pending", "collect_garbage"):
        performance.observe_method(FeaturePackageTransactionService, method)
    for method in ("load_current", "load_pending"):
        performance.observe_method(ProductionFeatureStartup, method)
    performance.observe_method(WindowsFeatureProbeSandbox, "run")
    started = time.perf_counter()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    host = FeatureHost()
    cfg = Config(base=args.data_dir / "config", instance_id="phase4b-validation")
    for key in (
        "proactive_screen_enabled",
        "agent_link_enabled",
        "self_talk_enabled",
        "voice_chime_enabled",
        "festival_reminder_enabled",
        "todo_reminder_enabled",
        "dynamic_island_enabled",
        "harness_autostart",
        "autostart_wanted",
        "balance_auto_refresh",
    ):
        cfg.set(key, False)
    shell = dialog = supervisor = http_context = None
    evidence = {
        "validation_only": True,
        "production_bootstrap": True,
        "chat": ENABLE_CHAT,
        "pid": os.getpid(),
        "mode": args.mode,
        "action": args.action,
        "core_root": str(Path(sys.executable).parent),
        "events": [],
    }
    # Observation only: do not replace resolution, verification or state writes.
    from pet.feature_install_state import FeatureInstallStateStore

    original_resolve = FeatureInstallStateStore.resolve_verified

    def observed_resolve(store, verifier, **kwargs):
        result = original_resolve(store, verifier, **kwargs)
        evidence.setdefault("resolutions", []).append({"purpose": kwargs.get("purpose", "execution"), "status": result.status, "reason": result.reason})
        if result.status == "verification_failed":
            try:
                state = store.read().state
                verifier.verify(store.root / "versions" / state.active)
            except Exception as error:
                evidence["verification_exception_type"] = type(error).__name__
                # Trusted validator diagnostics only; no candidate exceptions.
                from pet.plugins.package_trust import PackageVerificationError

                if isinstance(error, PackageVerificationError):
                    evidence["verification_diagnostic"] = str(error)
        return result

    setattr(FeatureInstallStateStore, "resolve_verified", observed_resolve)
    ending = False
    acted = False
    disable_result = None
    worker_stopped = False
    execution_release = None
    code = 0

    class Control(QObject):
        quit_requested = Signal()

    control = Control()
    control.quit_requested.connect(app.quit)

    def output(result=None, *, error=None):
        nonlocal code, ending
        if ending:
            return
        ending = True
        if result is not None:
            evidence["result"] = {name: getattr(result, name, None) for name in ("status", "reason", "revision", "phase")}
        state = manager.service.store.read().state if manager.service is not None else None
        evidence["state"] = None if state is None else state.document()
        evidence["owners"] = list(manager.host.owners())
        evidence["loaded_feature_modules"] = sorted(
            n
            for n in sys.modules
            if n.startswith(("features.", "_dsh_feature_", "_pet_official_screen_", "pet.vision", "pet.proactive", "pet.screen_understanding"))
        )
        evidence["performance_phases"] = list(performance.phases)
        evidence["elapsed_ms"] = (time.perf_counter() - started) * 1000
        process = psutil.Process()
        evidence["rss"] = process.memory_info().rss
        evidence["threads"] = process.num_threads()
        evidence["leases"] = (
            [] if state is None else [asdict(item) for version in state.versions for item in manager.service.leases.inspect_occupancy(version).leases]
        )
        component = dialog._screen_component if dialog is not None else None
        if component is not None:
            settings = component.context.vision.settings()
            evidence["profile_bindings"] = dict(settings.bindings)
            evidence["profile_digest"] = __import__("hashlib").sha256(json.dumps(settings.to_dict(), sort_keys=True).encode()).hexdigest()
            if settings.bindings.get("manual"):
                evidence["profile_credential_ready"] = component.context.vision.resolve("manual").ready
        evidence["error"] = error
        if error:
            code = 1
        if args.screenshot:
            assert dialog is not None, "own widget capture requires a settings dialog"
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(args.screenshot)):
                code = 1
                evidence["error"] = "own_widget_capture_failed"
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"event": "ready", "evidence": str(args.evidence), "error": evidence["error"]}), flush=True)
        if not args.hold or code:
            QTimer.singleShot(0, app.quit)
        else:

            def commands():
                for line in sys.stdin:
                    if line.strip() == "quit":
                        control.quit_requested.emit()
                        return
                control.quit_requested.emit()

            threading.Thread(target=commands, name="validation-owned-control", daemon=True).start()

    def fail(reason):
        output(error=reason)

    try:
        if args.mode == "core":
            shell = AppShell(app, cfg, enable_chat=ENABLE_CHAT, feature_host=host)
            shell.start()
            if shell.win is None:
                raise RuntimeError("actual pet window missing")
            dialog = ModernSettingsDialog(cfg, include_ai=ENABLE_CHAT, feature_host=host, initial_page="extensions")
        else:
            dialog = ModernSettingsDialog(cfg, include_ai=ENABLE_CHAT, standalone=True, initial_page="extensions" if args.mode == "manager" else None)
        dialog.resize(args.width, 780)
        dialog.show()
        manager = dialog.feature_management
        widget = dialog.findChild(FeatureManagementWidget)
        if widget is None or manager.builtin:
            raise RuntimeError("actual removable management UI missing")
        evidence["startup_result"] = (
            None if manager.last_result is None else {name: getattr(manager.last_result, name, None) for name in ("status", "reason", "revision")}
        )
        evidence["pet_window"] = bool(shell is not None and shell.win is not None)
        evidence["settings_component"] = dialog._screen_component is not None

        retry_attempts = 0
        retry_pending = False

        def result_received(result):
            nonlocal acted, disable_result, retry_attempts, retry_pending
            try:
                if isinstance(result, Inspection):
                    if retry_pending:
                        retry_pending = False
                        if not widget.retry_button.isEnabled() or widget.retry_plan is None:
                            raise RuntimeError("confirmed retry plan unavailable")
                        widget.retry_button.click()
                        return
                    if acted:
                        return
                    acted = True
                    if args.action == "inspect":
                        if args.mode == "manager" and (manager.startup is not None or dialog._screen_component is not None):
                            raise RuntimeError("management-only entry imported feature")
                        output(result)
                    elif args.action == "install":
                        if not widget.begin_source(args.source):
                            fail("UI rejected source operation")
                    elif args.action in ("uninstall", "rollback", "recover"):
                        button = {"uninstall": widget.uninstall_button, "rollback": widget.rollback_button, "recover": widget.retry_button}[args.action]
                        if not button.isEnabled():
                            fail("UI action unavailable:" + args.action)
                        else:
                            button.click()
                    elif args.action in ("enable", "disable"):
                        target = args.action == "enable"
                        if result.enabled == target:
                            output(result)
                        elif not widget.enable_button.isEnabled():
                            fail("UI enable action unavailable")
                        else:
                            widget.enable_button.click()
                    elif args.action == "verify-disabled":
                        from pet.feature_host_bindings import manual_for, runtime_for

                        if manager.host.enabled("official.screen-understanding"):
                            raise RuntimeError("disabled execution authorized")
                        if shell is None or manual_for(shell.win) is not None or runtime_for(shell.win, cfg) is not None:
                            raise RuntimeError("disabled manual/automatic path accepted")
                        evidence["disabled_manual_automatic_rejected"] = True
                        output(result)
                    elif args.action == "configure":
                        component = dialog._screen_component
                        if component is None:
                            raise RuntimeError("actual feature settings missing")
                        page = component.vision
                        page.profile_id.setText("phase4b-generated-" + __import__("uuid").uuid4().hex[:12])
                        page.url.setText("http://127.0.0.1:9")
                        page.model.setText("generated-preservation-profile")
                        page.key_edit.setText(os.urandom(32).hex())
                        page.saved.connect(lambda: output())
                        page.save_button.click()
                    elif args.action in ("request", "request-disable"):
                        run_request()
                elif isinstance(result, OperationResult):
                    evidence["events"].append({name: getattr(result, name, None) for name in ("status", "reason", "phase")})
                    if retryable_validation_result(result, retry_attempts):
                        retry_attempts += 1
                        retry_pending = True
                        return  # Actual widget schedules an asynchronous inspect.
                    if result.phase == "awaiting_confirmation":
                        if not widget.confirm_button.isEnabled():
                            fail("immutable confirmation unavailable")
                        else:
                            widget.confirm_button.click()
                    elif args.action == "request-disable":
                        disable_result = result
                        finish_disabled()
                    else:
                        output(result)
            except Exception as error:
                fail(type(error).__name__ + ":" + str(error))

        def finish_disabled():
            if disable_result is not None and worker_stopped:
                assert shell is not None, "disable verification requires actual Core shell"
                from pet.feature_host_bindings import manual_for, runtime_for

                if manager.host.enabled("official.screen-understanding") or manual_for(shell.win) is not None or runtime_for(shell.win, cfg) is not None:
                    fail("disabled production execution accepted")
                else:
                    evidence["disabled_manual_automatic_rejected"] = True
                    evidence["worker_stopped_by_package_disable"] = True
                    output(disable_result)

        def run_request():
            nonlocal supervisor, http_context, execution_release
            from validation_boundaries import synthetic_http_service

            from pet.workers.supervisor import WorkerSupervisor

            startup = manager.startup
            if startup is None or startup.binding is None:
                raise RuntimeError("production feature is not loaded")
            descriptor = startup.binding.handle.descriptor
            if "resources/VALIDATION-SYNTHETIC.txt" not in descriptor.files:
                raise RuntimeError("signed synthetic marker required before request")
            http_context = synthetic_http_service()
            provider, stats = http_context.__enter__()
            factory = manager.host._definitions[descriptor.id].worker_launch_factory
            supervisor = WorkerSupervisor("proactive-screen", launch_factory=factory, max_restarts=0, handshake_timeout_ms=20000)
            execution_release = manager.host.bind_execution(descriptor.id, supervisor.stop, lambda: None)
            pending = {}
            replies = set()

            def send(operation, arguments):
                rid = supervisor.send_request(operation, arguments, generation=1)
                if not rid:
                    fail("real Worker request rejected")
                else:
                    pending[rid] = operation

            def ready():
                pid = int(supervisor.process.processId())
                mappings = sorted({m.path for m in psutil.Process(pid).memory_maps()})
                core = Path(sys.executable).parent.resolve()
                if any(Path(name).is_relative_to(core) for name in mappings):
                    fail("Worker borrowed Core DLL")
                    return
                evidence["worker"] = {"pid": pid, "mapped_files": mappings}
                send("observe_foreground", {})

            def response(message):
                try:
                    operation = pending.pop(message.request_id)
                    if message.payload.get("status") != "ok" or message.payload.get("operation") != operation:
                        raise RuntimeError("invalid real Worker response")
                    value = message.payload["result"]
                    if operation == "observe_foreground":
                        send("capture_foreground", {"window": value["window"]})
                    elif operation == "capture_foreground":
                        send("analyze_frame", {"frame_id": value["frame_id"], "provider": provider})
                    elif operation == "analyze_frame":
                        replies.add(operation)
                        send("manual_look", {"provider": provider})
                    elif operation == "manual_look":
                        replies.add(operation)
                        if replies != {"analyze_frame", "manual_look"} or stats["requests"] != 2 or stats["errors"]:
                            raise RuntimeError("deterministic Worker flow incomplete")
                        evidence["synthetic_requests"] = stats["requests"]
                        if args.action == "request-disable":
                            if not widget.enable_button.isEnabled():
                                raise RuntimeError("real disable UI unavailable")
                            widget.enable_button.click()
                        else:
                            supervisor.stop()
                except Exception as error:
                    fail("worker_response:" + type(error).__name__)

            def reverse(message):
                try:
                    if not reply_validation_budget(supervisor, message, pending):
                        fail("budget reply failed")
                except ValueError:
                    fail("unknown or stale Worker reverse request")

            def state_changed(state):
                nonlocal worker_stopped
                if str(getattr(state, "value", state)).lower() == "stopped" and replies == {"analyze_frame", "manual_look"}:
                    worker_stopped = True
                    if args.action == "request-disable":
                        finish_disabled()
                    else:
                        output()

            supervisor.ready.connect(ready)
            supervisor.response_received.connect(response)
            supervisor.request_received.connect(reverse)
            supervisor.state_changed.connect(state_changed)
            supervisor.failed.connect(lambda reason: fail("worker_fault:" + reason))
            if not supervisor.start({"generation": 1}):
                fail("worker_start_rejected")

        manager.result_ready.connect(result_received)
        # Widget's zero-delay inspect executes only after this connection exists.
        QTimer.singleShot(180000, lambda: fail("bounded_validation_timeout"))
        app.exec()
    except Exception as error:
        code = 1
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps({**evidence, "error": type(error).__name__ + ":" + str(error)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("VALIDATION_FAILURE " + type(error).__name__, flush=True)
    finally:
        if execution_release is not None:
            execution_release()
        if supervisor is not None:
            supervisor.stop()
        close_valid_widget(dialog)
        if shell is not None:
            shell._on_about_to_quit()
        if http_context is not None:
            http_context.__exit__(None, None, None)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
