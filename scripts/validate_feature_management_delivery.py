"""Owned native Windows, dual frozen Core seven-row production/UI acceptance.

No ledger writes or descriptor binding. All mutations enter actual settings UI;
only generated fixture processes receive the natural stdin quit command.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import subprocess
import threading
from pathlib import Path


def validate_evidence(value, *, active, enabled, pending, settings=False):
    assert value.get("validation_only") is True and value.get("production_bootstrap") is True, "production validation entry required"
    assert value.get("error") is None, value.get("error")
    assert value.get("pid", 0) > 0 and value.get("elapsed_ms", 0) > 0 and value.get("rss", 0) > 0
    state = value["state"]
    assert state["active"] == active, state
    assert state["enabled"] is enabled, state
    assert bool(state["pending_transaction"]) is pending, "pending transaction mismatch: " + repr(state)
    if active is not None:
        assert active in state["versions"], "active version is not authoritative"
    else:
        assert not state["versions"], "uninstalled ledger retained versions"
    if settings:
        assert value.get("settings_component") is True, "actual settings component missing"
        assert "official.screen-understanding" in value.get("owners", ()), "settings owner missing"
        assert any(item["kind"] == "settings" for item in value.get("leases", ())), "actual settings lease missing"


class OwnedProcess:
    def __init__(self, command, *, cwd, output):
        environment = {key: val for key, val in os.environ.items() if not key.upper().startswith(("PYTHON", "_PYI", "_MEIPASS"))}
        environment.pop("QT_QPA_PLATFORM", None)
        self.process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.lines: queue.Queue[dict] = queue.Queue()
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        assert self.process.stdout is not None
        with self.output.open("wb") as log:
            total = 0
            while line := self.process.stdout.readline(65537):
                total += len(line)
                if total > 256 * 1024:
                    self.lines.put({"event": "output_limit"})
                    return
                log.write(line)
                log.flush()
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict):
                    self.lines.put(message)
            self.lines.put({"event": "eof"})

    def ready(self):
        while True:
            event = self.lines.get(timeout=210)
            if event.get("event") == "ready":
                assert event.get("error") is None, event
                return
            assert event.get("event") not in ("eof", "output_limit"), self.output

    def finish(self, *, natural_quit=False):
        assert self.process.stdin is not None and self.process.stdout is not None
        if natural_quit and self.process.poll() is None:
            self.process.stdin.write(b"quit\n")
            self.process.stdin.flush()
        result = self.process.wait(timeout=45 if natural_quit else 210)
        self.reader.join(timeout=10)
        assert not self.reader.is_alive(), "owned output reader did not finish"
        assert result == 0, f"owned frozen process rc={result}: {self.output}"
        self.process.stdin.close()
        self.process.stdout.close()


def run_variant(variant, executable, artifacts, output):
    output.mkdir(parents=True, exist_ok=False)
    cwd = output / "empty-runtime"
    cwd.mkdir()
    data = output / "data"
    steps = []
    holders = []

    def step(name, action="inspect", mode="manager", *, source=None, hold=False, active=None, enabled=False, pending=False, settings=False, width=900):
        evidence_path = output / (name + ".json")
        command = [executable, "--mode", mode, "--action", action, "--data-dir", str(data), "--evidence", str(evidence_path), "--width", str(width)]
        if source is not None:
            command.extend(["--source", source])
        if hold:
            command.append("--hold")
        if action == "inspect" and not hold:
            command.extend(["--screenshot", str(output / (name + ".png"))])
        child = OwnedProcess(command, cwd=cwd, output=output / (name + ".log"))
        if hold:
            holders.append(child)
        try:
            child.ready()
            value = json.loads(evidence_path.read_text(encoding="utf-8"))
            validate_evidence(value, active=active, enabled=enabled, pending=pending, settings=settings)
            if not hold:
                child.finish()
            steps.append(
                {
                    "name": name,
                    "command": command,
                    "evidence": str(evidence_path),
                    "elapsed_ms": value["elapsed_ms"],
                    "rss": value["rss"],
                    "threads": value["threads"],
                }
            )
            print(json.dumps({"event": "step_passed", "variant": variant, "step": name, "ms": value["elapsed_ms"]}), flush=True)
            return child if hold else value
        except Exception:
            # Only our generated child. EOF requests its normal event-loop quit;
            # no terminate/kill against Core, settings, or external users.
            if child.process.poll() is None:
                assert child.process.stdin is not None
                child.process.stdin.close()
                child.process.wait(timeout=45)
            raise

    def release(child):
        child.finish(natural_quit=True)
        holders.remove(child)

    rows = []
    try:
        empty = step("01-empty-core", mode="core")
        assert empty["pet_window"] and not empty["owners"] and not empty["loaded_feature_modules"]
        rows.append({"row": 1, "passed": True})
        package = artifacts["packages"]
        step("02-directory-install", "install", source=package["v1"], active="1.0.0", enabled=True, pending=True)
        step("02-directory-load", mode="core", active="1.0.0", enabled=True, settings=True)
        step("02-independent-settings", mode="settings", active="1.0.0", enabled=True, settings=True)
        profile = step("02-configure-generated-profile", "configure", "settings", active="1.0.0", enabled=True, settings=True)
        assert profile["profile_credential_ready"]
        step("02-idempotent-zip", "install", source=artifacts["zip"], active="1.0.0", enabled=True)
        rows.append({"row": 2, "passed": True})
        request = step("03-real-worker-generated-image-http", "request", "core", active="1.0.0", enabled=True, settings=True)
        assert request["synthetic_requests"] == 2 and request["worker"]["mapped_files"]
        rows.append({"row": 3, "passed": True})
        disabled = step("04-disable-running-worker", "request-disable", "core", active="1.0.0", enabled=False, settings=True)
        assert disabled["worker_stopped_by_package_disable"] and disabled["disabled_manual_automatic_rejected"]
        step("04-disabled-production-seams", "verify-disabled", "core", active="1.0.0", enabled=False, settings=True)
        step("04-enable", "enable", active="1.0.0", enabled=True)
        rows.append({"row": 4, "passed": True})
        core = step("05-core-holder", mode="core", hold=True, active="1.0.0", enabled=True, settings=True)
        settings_owner = step("05-settings-holder", mode="settings", hold=True, active="1.0.0", enabled=True, settings=True)
        waiting = step("05-uninstall-waits-for-owners", "uninstall", active="1.0.0", enabled=False, pending=True)
        assert waiting["result"]["status"] == "awaiting_release" and len({item["owner_pid"] for item in waiting["leases"]}) >= 2
        release(settings_owner)
        release(core)
        uninstalled = step("05-resume-uninstall", "recover")
        ledger = next(data.rglob("state.json"))
        assert not any((ledger.parent / "versions").iterdir())
        assert not uninstalled["owners"]
        rows.append({"row": 5, "passed": True})
        step("06-zip-reinstall", "install", source=artifacts["zip"], active="1.0.0", enabled=True, pending=True)
        step("06-reinstall-load", mode="core", active="1.0.0", enabled=True, settings=True)
        restored = step("06-preserved-profile-vault", mode="settings", active="1.0.0", enabled=True, settings=True)
        assert restored["profile_digest"] == profile["profile_digest"] and restored["profile_bindings"] == profile["profile_bindings"]
        assert restored["profile_credential_ready"]
        rows.append({"row": 6, "passed": True})
        core = step("07-upgrade-core-holder", mode="core", hold=True, active="1.0.0", enabled=True, settings=True)
        step("07-upgrade-waits-natural-exit", "install", source=package["v2"], active="1.0.0", enabled=True, pending=True)
        release(core)
        step("07-upgrade-activate", "recover", active="1.0.1", enabled=True, pending=True)
        upgraded = step("07-upgrade-load", mode="core", active="1.0.1", enabled=True, settings=True)
        assert upgraded["state"]["previous"] == "1.0.0"
        step("07-explicit-retained-rollback", "rollback", active="1.0.0", enabled=True, pending=True)
        step("07-rollback-load", mode="core", active="1.0.0", enabled=True, settings=True)
        failed = step("07-selfcheck-fails-before-disable", "install", source=package["bad-probe"], active="1.0.0", enabled=True)
        assert failed["result"]["status"] == "rejected"
        step("07-candidate-staged-for-real-load-failure", "install", source=package["bad-startup"], active="1.0.4", enabled=True, pending=True)
        failed = step("07-real-startup-fails-after-import", mode="core", active="1.0.4", enabled=False, pending=True)
        assert failed["startup_result"]["status"] in ("awaiting_release", "recovery_required")
        restored = step("07-restart-real-previous-receipt", mode="core", active="1.0.0", enabled=True, settings=True)
        assert restored["startup_result"]["reason"] == "rolled_back"
        rows.append({"row": 7, "passed": True})
        return {"variant": variant, "rows": rows, "steps": steps, "passed": True}
    finally:
        for child in list(holders):
            child.finish(natural_quit=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=("no-chat", "chat", "both"), default="both")
    args = parser.parse_args(argv)
    assert os.name == "nt", "native Windows acceptance required"
    artifacts = json.loads(args.artifacts.read_text(encoding="utf-8"))
    assert artifacts["validation_only"] is True
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    variants: list[dict] = []
    report = {"environment": {"platform": platform.platform(), "python_controller": platform.python_version()}, "variants": variants, "passed": False}
    try:
        names = ("no-chat", "chat") if args.variant == "both" else (args.variant,)
        for name in names:
            variants.append(run_variant(name, artifacts["cores"][name], artifacts, output / name))
        report["passed"] = all(item["passed"] and len(item["rows"]) == 7 for item in variants)
    except Exception as error:
        report["error"] = type(error).__name__ + ":" + str(error)
        raise
    finally:
        (output / "matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
