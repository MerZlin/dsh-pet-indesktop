"""Standalone frozen entry: lease BEFORE execution, probe has no task surface."""

import io
import sys
import types

from pet.workers.protocol import build_message, decode_message, encode_message


def test_unisolated_probe_refuses_without_reading_or_loading_runtime(monkeypatch):
    from pet.workers.screen_entry import run_screen_worker_entry

    monkeypatch.setattr("pet.workers.screen_entry.sandbox_enforced", lambda: False)

    class Unreadable:
        def readline(self, *_):
            raise AssertionError("unisolated probe must not read commands")

    def run():
        raise AssertionError("probe must not import runtime")

    output = io.BytesIO()
    assert run_screen_worker_entry(["--feature-package-probe"], source=Unreadable(), output=output, run_runtime=run) == 77
    assert output.getvalue() == b""


def test_probe_only_accepts_shutdown_and_graceful_exit(monkeypatch):
    from pet.workers.screen_entry import run_screen_worker_entry

    monkeypatch.setattr("pet.workers.screen_entry.sandbox_enforced", lambda: True)

    def run():
        raise AssertionError("probe must not start runtime")

    output = io.BytesIO()
    source = io.BytesIO(encode_message(build_message("proactive-screen", "shutdown")))
    assert run_screen_worker_entry(["--feature-package-probe"], source=source, output=output, run_runtime=run) == 0
    hello = decode_message(output.getvalue())
    assert hello.worker_id == "proactive-screen" and hello.type == "hello"
    assert hello.payload == {"probe": True, "capabilities": []}
    for kind in ("config_push", "request"):
        message = build_message("proactive-screen", kind, {"operation": "capture"}, request_id="malicious")
        assert run_screen_worker_entry(["--feature-package-probe"], source=io.BytesIO(encode_message(message)), output=io.BytesIO(), run_runtime=run) == 78


def test_normal_standalone_worker_requires_real_lease_before_runtime(monkeypatch):
    from pet.workers.screen_entry import run_screen_worker_entry

    order = []
    module = types.ModuleType("pet.workers.lease_bootstrap")

    def claim():
        order.append("claim")
        return False

    module.claim_worker_lease_from_environment = claim
    monkeypatch.setitem(sys.modules, "pet.workers.lease_bootstrap", module)

    def run():
        order.append("runtime")
        return 0

    assert run_screen_worker_entry([], run_runtime=run) == 77
    assert order == ["claim"]
    module.claim_worker_lease_from_environment = lambda: order.append("claim") or True
    assert run_screen_worker_entry([], run_runtime=run) == 0
    assert order == ["claim", "claim", "runtime"]


def test_probe_rejects_extra_arguments_and_oversized_frame(monkeypatch):
    from pet.workers.screen_entry import run_screen_worker_entry

    monkeypatch.setattr("pet.workers.screen_entry.sandbox_enforced", lambda: True)
    assert run_screen_worker_entry(["--feature-package-probe", "--execute"], source=io.BytesIO(), output=io.BytesIO()) == 78
    assert run_screen_worker_entry(["--feature-package-probe"], source=io.BytesIO(b"x" * (64 * 1024 + 1)), output=io.BytesIO()) == 78
