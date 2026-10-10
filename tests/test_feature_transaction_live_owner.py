"""A live installer and another process must not drive the same journal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from pet.feature_package_transactions import RuntimePreparation
from tests.test_feature_package_transactions import _confirm, _package, _service


def test_recovery_does_not_overtake_live_accepted_install(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    entered, release = threading.Event(), threading.Event()

    class LifecycleBoundary:
        def prepare(self, request):
            if service.store.read().state.pending_transaction:
                entered.set()
                assert release.wait(60), "the test must release its lifecycle endpoint"
            return RuntimePreparation()

    service.runtime = LifecycleBoundary()
    plan = service.preflight_install(source)
    child = r'''
import json, sys
from pathlib import Path
from pet.feature_package_transactions import FeaturePackageTransactionService
from pet.plugins.package_trust import FeaturePackageVerifier
from tests.test_feature_package_transactions import StubChecker
v = FeaturePackageVerifier(core_version="5.0.0", api_version="1", platform=sys.platform,
    allowed_capabilities={"screen.capture"}, trust_anchors={k: bytes.fromhex(v) for k, v in json.loads(sys.argv[2]).items()})
s = FeaturePackageTransactionService(Path(sys.argv[1]), v, self_checker=StubChecker())
r = s.recover_pending()
print(json.dumps({"status": r.status, "reason": r.reason}))
'''
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_confirm, service, plan)
        try:
            assert entered.wait(60), "accepted installer did not reach lifecycle wait"
            before = service._path(plan.operation_id).read_bytes()
            result = subprocess.run(
                [sys.executable, "-c", child, str(service.leases.data_root),
                 json.dumps({k: v.hex() for k, v in verifier.trust_anchors.items()})],
                capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            assert result.returncode == 0, result.stderr
            outcome = json.loads(result.stdout)
            assert outcome == {"status": "awaiting_release", "reason": "management_lock_busy"}
            assert service._path(plan.operation_id).read_bytes() == before
        finally:
            release.set()
        assert future.result(timeout=60).status == "awaiting_startup_confirmation"
    state = service.store.read().state
    assert state.document() == service._load(plan.operation_id)["expected_state"]
    # The kernel operation owner is released; a later recovery is still valid.
    assert service.recover_pending().status == "awaiting_startup_confirmation"
