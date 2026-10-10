"""Validation-only frozen Core. Never used by the default product entry point.

Only explicit package selection is supported. Phase5A validates a user-trusted
local package structure/inventory; no public/private key is injected or required.
No screenshots, credentials or remote services are used by this smoke.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path


def start_worker(supervisor, failed):
    """Run after entering the event loop, including synchronous launch failures."""
    if not supervisor.start({"generation": 1}):
        failed("worker_start_rejected")


def probe_supervisor(on_heartbeat, **kwargs):
    """Observe accepted wire heartbeats without changing the production signals."""
    from pet.workers.supervisor import WorkerSupervisor

    class ProbeSupervisor(WorkerSupervisor):
        def _handle_message(self, message):
            previous = self._last_heartbeat
            super()._handle_message(message)
            if message.type == "heartbeat" and self._last_heartbeat != previous:
                on_heartbeat()

    return ProbeSupervisor("proactive-screen", **kwargs)


def require_synthetic_package(descriptor):
    # Marker must be in the authenticated inventory, not merely on disk.
    if descriptor.trust_status not in {"trusted_official", "local_user"} or "resources/VALIDATION-SYNTHETIC.txt" not in descriptor.files:
        raise ValueError("synthetic request requires a marked synthetic validation package")


@contextmanager
def synthetic_http_service():
    """A bounded loopback fixture; never log image bytes or request credentials."""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    stats = {"requests": 0, "errors": []}
    key = "synthetic-test-secret"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer " + key:
                    raise ValueError("invalid fixture request route/authorization")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("fixture request size limit")
                body = json.loads(self.rfile.read(size))
                images = [
                    part["image_url"]["url"]
                    for message in body["messages"]
                    for part in message.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "image_url"
                ]
                if len(images) != 1 or not images[0].startswith("data:image/jpeg;base64,"):
                    raise ValueError("expected one synthetic JPEG")
                if not base64.b64decode(images[0].split(",", 1)[1], validate=True).startswith(b"\xff\xd8"):
                    raise ValueError("invalid synthetic JPEG")
                stats["requests"] += 1
                reply = json.dumps({"choices": [{"message": {"content": "synthetic analysis"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except Exception as error:
                stats["errors"].append(type(error).__name__)
                self.send_error(400, "fixture request rejected")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield {"base_url": f"http://127.0.0.1:{server.server_port}", "model": "validation", "api_key": key, "timeout": 10}, stats
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("local fixture did not stop")


def main(argv=None):
    from validation_config import ENABLE_CHAT, VALIDATION_ONLY

    if VALIDATION_ONLY is not True:
        raise RuntimeError("not a validation build")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--shared", action="store_true")
    parser.add_argument("--synthetic-request", action="store_true", help="marked synthetic validation package only; loopback HTTP")
    args = parser.parse_args(argv)
    if args.synthetic_request and not args.feature_dir:
        parser.error("synthetic request requires --feature-dir")
    data = args.data_dir.absolute()
    data.mkdir(parents=True, exist_ok=False)
    # Isolate every ordinary data root before importing application modules.
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        directory = data / name.lower()
        directory.mkdir()
        os.environ[name] = str(directory)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import psutil
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    from pet import __version__
    from pet.app import AppShell
    from pet.config import Config
    from pet.plugins.feature_host import FeatureHost
    from pet.plugins.feature_packages import FeaturePackageLoader, FeaturePackageVerifier
    from pet.plugins.package_binding import bind_verified_feature
    from pet.workers.supervisor import WorkerSupervisor

    faulthandler.enable()
    faulthandler.dump_traceback_later(40, repeat=True)
    started = time.perf_counter()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    host = FeatureHost()
    binding = loader = descriptor = supervisor = shell = settings = None
    evidence = {
        "validation_only": True,
        "chat": ENABLE_CHAT,
        "shared": args.shared,
        "feature": bool(args.feature_dir),
        "core_root": str(Path(sys.executable).parent),
        "stages": [],
        "diagnostics": [],
    }
    errors, requests = [], set()
    child_pid = None
    finished = False
    ready_at = None
    worker_started = None
    synthetic_service = None
    synthetic_provider = synthetic_stats = None
    request_started = {}
    replies = set()
    got_heartbeat = False
    owner = "official.screen-understanding"

    def stage(name):
        evidence["stages"].append(name)
        print("VALIDATION_STAGE " + name, flush=True)

    def fail(reason):
        print("VALIDATION_FAILURE " + reason, flush=True)
        errors.append(reason)
        shutdown()

    def shutdown():
        nonlocal finished
        if finished:
            return
        finished = True
        if supervisor:
            supervisor.stop()
        # aboutToQuit runs the real AppShell shutdown path; global supervisors
        # get their existing bounded graceful-stop handling there too.
        QTimer.singleShot(0, app.quit)

    def after_ready():
        nonlocal child_pid, ready_at
        ready_at = time.perf_counter()
        child_pid = int(supervisor.process.processId())
        process = psutil.Process(child_pid)
        evidence["worker"] = {
            "pid": child_pid,
            "rss": process.memory_info().rss,
            "threads": process.num_threads(),
            "startup_ms": (ready_at - worker_started) * 1000,
            "core_to_worker_ready_ms": (ready_at - started) * 1000,
            "mapped_files": sorted({m.path for m in process.memory_maps()}),
        }
        core = Path(sys.executable).resolve().parent
        package = args.feature_dir.resolve()
        paths = [Path(m) for m in evidence["worker"]["mapped_files"] if m]
        if any(p.is_relative_to(core) for p in paths):
            fail("Worker borrowed a DLL from Core")
            return
        if not any(p.is_relative_to(package) and "python" in p.name.lower() for p in paths):
            fail("Worker own Python DLL not found")
            return
        if args.synthetic_request:
            send_request("observe_foreground", {})
            stage("worker_ready")
            return
        for operation, arguments in (("release_frame", {"frame_id": "absent-validation-frame"}), ("cancel", {"request_id": "absent-validation-request"})):
            rid = supervisor.send_request(operation, arguments, generation=1)
            if not rid:
                fail("request not sent")
                return
            requests.add(rid)
        stage("worker_ready")

    def send_request(operation, arguments):
        rid = supervisor.send_request(operation, arguments, generation=1)
        if not rid:
            raise RuntimeError("request not sent")
        requests.add(rid)
        request_started[rid] = (operation, time.perf_counter())

    def response(message):
        try:
            if message.request_id not in requests or message.payload.get("status") != "ok":
                raise RuntimeError("invalid response: " + str(message.payload.get("error_code", "status")))
            requests.remove(message.request_id)
            operation = message.payload["operation"]
            stage("response:" + operation)
            if not args.synthetic_request:
                return
            expected, since = request_started.pop(message.request_id)
            if expected != operation or message.payload.get("generation") != 1:
                raise RuntimeError("response routing mismatch")
            evidence.setdefault("request_ms", {})[operation] = (time.perf_counter() - since) * 1000
            result = message.payload["result"]
            if operation == "observe_foreground":
                if result["window"]["hwnd"] != 42:
                    raise RuntimeError("synthetic window missing")
                send_request("capture_foreground", {"window": result["window"]})
            elif operation == "capture_foreground":
                send_request("analyze_frame", {"frame_id": result["frame_id"], "provider": synthetic_provider})
            elif operation in ("analyze_frame", "manual_look"):
                if result.get("reply") != "synthetic analysis":
                    raise RuntimeError("loopback result mismatch")
                replies.add(operation)
                if operation == "analyze_frame":
                    send_request("manual_look", {"provider": synthetic_provider})
        except Exception as error:
            fail("response_probe:" + str(error))

    def reverse_request(message):
        if not args.synthetic_request or message.payload.get("operation") != "budget_check" or message.payload.get("generation") != 1:
            fail("unexpected_reverse_request")
            return
        evidence["budget_checks"] = evidence.get("budget_checks", 0) + 1
        if not supervisor.send_response(message.request_id, "budget_check", {"status": "ok", "result": {"allowed": True}}, generation=1):
            fail("budget_response_not_sent")

    def received_heartbeat():
        nonlocal got_heartbeat
        got_heartbeat = True

    def tick():
        if ready_at is not None and not requests and got_heartbeat:
            if args.synthetic_request and (
                replies != {"analyze_frame", "manual_look"}
                or synthetic_stats["requests"] != 2
                or synthetic_stats["errors"]
                or evidence.get("budget_checks") != 1
            ):
                fail("synthetic_flow_incomplete")
                return
            evidence["stages"].append("heartbeat")
            shutdown()

    try:
        stage("application_created")
        if args.feature_dir:
            verifier = FeaturePackageVerifier(
                core_version=__version__,
                api_version="1",
                platform=sys.platform,
                allowed_capabilities={"screen.capture"},
                allow_local_packages=True,
            )
            loader = FeaturePackageLoader(verifier)
            verified_at = time.perf_counter()
            descriptor = verifier.verify(args.feature_dir.resolve())
            stage("package_verified")
            if args.synthetic_request:
                require_synthetic_package(descriptor)
                synthetic_service = synthetic_http_service()
                synthetic_provider, synthetic_stats = synthetic_service.__enter__()
            runtime_directory = data / "worker-runtime"
            runtime_directory.mkdir()
            binding = bind_verified_feature(host, loader, descriptor, runtime_directory=runtime_directory, core_roots=(Path(sys.executable).parent,))
            evidence["verify_load_ms"] = (time.perf_counter() - verified_at) * 1000
            stage("verified_host")
        cfg = Config(base=data / "config", instance_id="validation-core")
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
        cfg.set("experimental_single_process_spawn", args.shared)
        stage("before_core_constructor")
        shell = AppShell(app, cfg, enable_chat=ENABLE_CHAT, feature_host=host)
        stage("core_constructed")
        shell.start()
        if shell.win is None:
            raise RuntimeError("Core window missing")
        stage("core_window")
        from pet.modern_settings_dialog import ModernSettingsDialog

        settings_at = time.perf_counter()
        stage("before_settings")
        settings = ModernSettingsDialog(cfg, include_ai=ENABLE_CHAT, feature_host=host)
        evidence["settings_ms"] = (time.perf_counter() - settings_at) * 1000
        component = getattr(settings, "_screen_component", None)
        if bool(component) != bool(args.feature_dir):
            raise RuntimeError("settings contribution mismatch")
        if any(p.name().lower().startswith("proactive-screen-worker") for p in psutil.Process().children(recursive=True)):
            raise RuntimeError("opening settings launched a Worker")
        stage("settings_without_execution")
        settings.close()
        settings.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settings = None
        if descriptor:
            if loader.lease_counts(descriptor).settings:
                raise RuntimeError("settings lease leaked")
            factory = host._definitions[owner].worker_launch_factory
            supervisor = probe_supervisor(received_heartbeat, launch_factory=factory, max_restarts=0, handshake_timeout_ms=15000)
            supervisor.ready.connect(after_ready)
            supervisor.response_received.connect(response)
            supervisor.request_received.connect(reverse_request)
            supervisor.failed.connect(lambda reason: fail("worker_fault:" + reason))
            supervisor.diagnostic.connect(lambda stage, detail: evidence["diagnostics"].append({"stage": stage, "detail": detail}))

            def launch():
                nonlocal worker_started
                worker_started = time.perf_counter()
                start_worker(supervisor, fail)

            QTimer.singleShot(0, launch)
        else:
            if host.owners() or any(n.startswith(("features.", "pet.screen_understanding", "pet.vision", "pet.proactive")) for n in sys.modules):
                raise RuntimeError("screen imported without package")
            QTimer.singleShot(100, shutdown)
        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(50)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: fail("validation_timeout"))
        timeout.start(45000)
        app.exec()
        timer.stop()
        timeout.stop()
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        # Mirror pet.app.main: keep QObject owners alive through the bounded
        # asynchronous shutdown drain, including immediate quits after ready.
        for cleanup in (
            supervisor.stop if supervisor else None,
            shell._on_about_to_quit if shell else None,
            WorkerSupervisor.finish_app_shutdown,
            binding.close if binding else None,
        ):
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    errors.append("cleanup_failed: " + traceback.format_exc())
        if synthetic_service is not None:
            synthetic_service.__exit__(None, None, None)
            evidence["synthetic_http"] = synthetic_stats
        faulthandler.cancel_dump_traceback_later()
        if child_pid and psutil.pid_exists(child_pid):
            errors.append("Worker remained after Core cleanup")
        if not ENABLE_CHAT and any(n == "pet.chat" or n.startswith("pet.chat.") for n in sys.modules):
            errors.append("no-chat Core imported chat")
        evidence.update(
            errors=errors,
            duration_ms=(time.perf_counter() - started) * 1000,
            core_rss=psutil.Process().memory_info().rss,
            worker_stopped=not child_pid or not psutil.pid_exists(child_pid),
        )
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SCREEN_DELIVERY_SMOKE_OK" if not errors else "SCREEN_DELIVERY_SMOKE_FAILED", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
