"""Phase 5A public trust/state seams; private signing keys exist only in RAM."""

from __future__ import annotations

import builtins
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pet.feature_install_state import FeatureInstallStateStore, StateChange
from pet.plugins.package_trust import FeaturePackageVerifier, PackageVerificationError

AI = "official.ai-chat"
SCREEN = "official.screen-understanding"


def signed_package(root: Path, feature_id=AI, *, version="1.0.0", key_id="release-2026", key=None):
    key = key or Ed25519PrivateKey.generate()
    root.mkdir(parents=True)
    payload = {
        "host/__init__.py": b"",
        "host/factory.py": ("import builtins\nbuiltins._phase5a_candidate_executed = True\ndef create_host():\n    return None\n").encode(),
    }
    if feature_id == SCREEN:
        payload["worker/screen.exe"] = b"never-executed"
    manifest = dict(
        format_version=2,
        key_id=key_id,
        execution_kind="host-only" if feature_id == AI else "host-worker",
        id=feature_id,
        version=version,
        api_version="1",
        core_requires=">=5.0.0,<6.0.0",
        platforms=[sys.platform],
        capabilities=["network.http" if feature_id == AI else "screen.capture"],
        factory="ai-chat/v1" if feature_id == AI else "screen-understanding/v1",
        worker=None if feature_id == AI else dict(path="worker/screen.exe", args=[]),
        files={},
    )
    for name, data in payload.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest["files"][name] = dict(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    seal(root, manifest, key)
    return manifest, key


def seal(root, manifest, key):
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sig").write_bytes(key.sign(raw))


def verifier(key, feature_id=AI, **kwargs):
    return FeaturePackageVerifier(
        core_version="5.0.0",
        api_version="1",
        platform=sys.platform,
        feature_id=feature_id,
        allowed_capabilities={"network.http", "screen.capture"},
        trust_anchors={"release-2026": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
        **kwargs,
    )


def test_v2_host_only_is_verified_without_candidate_import(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "_phase5a_candidate_executed", False, raising=False)
    manifest, key = signed_package(tmp_path / "source")
    descriptor = verifier(key).verify(tmp_path / "source")
    assert descriptor.id == AI and descriptor.execution_kind == "host-only"
    assert descriptor.worker_path is None and descriptor.worker_args == ()
    assert descriptor.trust_anchor == manifest["key_id"]
    assert not builtins._phase5a_candidate_executed


def test_v2_screen_still_requires_real_worker(tmp_path):
    _, key = signed_package(tmp_path / "source", SCREEN)
    descriptor = verifier(key, SCREEN).verify(tmp_path / "source")
    assert descriptor.execution_kind == "host-worker"
    assert descriptor.worker_path == tmp_path / "source/worker/screen.exe"


@pytest.mark.parametrize(
    "change",
    [
        {"key_id": "unknown"},
        {"key_id": False},
        {"format_version": True},
        {"format_version": 3},
        {"execution_kind": "host-worker"},
        {"worker": {"path": "host/factory.py", "args": []}},
        {"id": SCREEN},
        {"factory": "screen-understanding/v1"},
        {"extra": True},
        {"capabilities": ["screen.capture"]},
    ],
)
def test_v2_invalid_identity_and_execution_contract_is_rejected(tmp_path, change, monkeypatch):
    monkeypatch.setattr(builtins, "_phase5a_candidate_executed", False, raising=False)
    manifest, key = signed_package(tmp_path / "source")
    manifest.update(change)
    seal(tmp_path / "source", manifest, key)
    with pytest.raises(PackageVerificationError):
        verifier(key).verify(tmp_path / "source")
    assert not builtins._phase5a_candidate_executed


def test_v2_declared_key_cannot_borrow_other_trusted_signer(tmp_path):
    manifest, key = signed_package(tmp_path / "source")
    other = Ed25519PrivateKey.generate()
    policy = verifier(key)
    policy = replace(policy, trust_anchors={**policy.trust_anchors, "other": other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)})
    manifest["key_id"] = "other"
    seal(tmp_path / "source", manifest, key)
    with pytest.raises(PackageVerificationError):
        policy.verify(tmp_path / "source")


def test_revoked_signer_and_scope_are_enforced(tmp_path):
    from pet.plugins.package_trust import SigningKeyPolicy

    _, key = signed_package(tmp_path / "source")
    for policy in (
        SigningKeyPolicy(frozenset({AI}), frozenset({"network.http"}), revoked=True),
        SigningKeyPolicy(frozenset({SCREEN}), frozenset({"network.http"})),
        SigningKeyPolicy(frozenset({AI}), frozenset()),
    ):
        with pytest.raises(PackageVerificationError):
            verifier(key, anchor_policy={"release-2026": policy}).verify(tmp_path / "source")


def test_official_registry_refuses_arbitrary_feature_ids(tmp_path):
    with pytest.raises(ValueError):
        FeatureInstallStateStore(tmp_path, feature_id="../escaped")
    with pytest.raises(ValueError):
        FeatureInstallStateStore(tmp_path, feature_id="third-party.example")


def test_two_ledgers_have_independent_identity_and_cas(tmp_path):
    ai = FeatureInstallStateStore(tmp_path, feature_id=AI)
    screen = FeatureInstallStateStore(tmp_path)
    ai.commit(StateChange({"1.0.0": "a" * 64}, active="1.0.0", enabled=True), expected_revision=0, operation_id="install-ai")
    screen.commit(StateChange({"2.0.0": "b" * 64}, active="2.0.0", enabled=False), expected_revision=0, operation_id="install-screen")
    assert ai.read().state.feature_id == AI
    assert ai.read().state.revision == screen.read().state.revision == 1
    assert json.loads(ai.state_path.read_bytes())["feature_id"] == AI
    assert ai.root != screen.root
    assert ai.recover().state.active == "1.0.0"
    assert screen.recover().state.active == "2.0.0"


def test_cross_package_state_and_receipts_cannot_be_adopted(tmp_path):
    ai = FeatureInstallStateStore(tmp_path, feature_id=AI)
    screen = FeatureInstallStateStore(tmp_path)
    ai.commit(StateChange({"1.0.0": "a" * 64}, active="1.0.0", enabled=True), expected_revision=0, operation_id="install-ai")
    screen.root.mkdir(parents=True)
    screen.state_path.write_bytes(ai.state_path.read_bytes())
    assert screen.read().state is None
    assert screen.recover().state is None


def test_ai_transaction_preflight_is_scoped_to_ai_ledger(tmp_path):
    from pet.feature_package_transactions import FeaturePackageTransactionService

    _, key = signed_package(tmp_path / "source")
    service = FeaturePackageTransactionService(tmp_path / "data", verifier(key))
    result = service.preflight_install(tmp_path / "source")
    assert result.status == "awaiting_confirmation"
    assert service.store.feature_id == AI
    assert service.store.root.name == AI
    assert not (tmp_path / "data/plugins" / SCREEN).exists()


def test_existing_official_registry_keeps_public_screen_api():
    from pet.official_features import SCREEN_OWNER, default_feature_host

    assert SCREEN_OWNER == SCREEN
    assert callable(default_feature_host)


def test_ai_acceptance_and_recovery_keep_owner_in_every_state_image(tmp_path, monkeypatch):
    from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation

    class Checker:
        def check(self, descriptor):
            return RuntimePreparation()

    _, key = signed_package(tmp_path / "source")
    service = FeaturePackageTransactionService(tmp_path / "data", verifier(key), self_checker=Checker())
    preflight = service.preflight_install(tmp_path / "source")
    assert preflight.feature_id == preflight.plan.feature_id == AI
    original = service.store.commit
    crashed = False

    def interrupted(change, **kwargs):
        nonlocal crashed
        result = original(change, **kwargs)
        if kwargs["operation_id"].endswith(".pending") and not crashed:
            crashed = True
            raise OSError("generated acknowledgment failure")
        return result

    monkeypatch.setattr(service.store, "commit", interrupted)
    service.apply(preflight.plan, confirmation_token=preflight.plan.confirmation_token)
    assert crashed
    result = service.recover_pending()
    assert result.status == "awaiting_startup_confirmation"
    assert result.feature_id == AI
    state = service.store.read().state
    assert state.feature_id == AI and state.pending_transaction == preflight.plan.operation_id
    assert service.startup_permit(preflight.plan.operation_id, role="core").version_owner == AI
    assert not (tmp_path / "data/plugins" / SCREEN).exists()


def test_operation_plan_cannot_cross_official_owner(tmp_path):
    from pet.feature_package_transactions import FeaturePackageTransactionService

    _, key = signed_package(tmp_path / "source")
    ai = FeaturePackageTransactionService(tmp_path / "data", verifier(key))
    screen = FeaturePackageTransactionService(tmp_path / "data", verifier(key, SCREEN))
    result = ai.preflight_install(tmp_path / "source")
    rejected = screen.apply(result.plan, confirmation_token=result.plan.confirmation_token)
    assert rejected.status == "rejected" and rejected.reason == "feature_identity_conflict"
    assert rejected.feature_id == SCREEN


def test_lifecycle_authorization_cannot_cross_package(tmp_path):
    from pet.feature_lifecycle_contract import LifecyclePrepareRequest, authorize_request
    from pet.feature_state_io import StateError

    screen = FeatureInstallStateStore(tmp_path)
    request = LifecyclePrepareRequest("lc-" + "a" * 32, 0, "disable", (), feature_id=AI)
    with pytest.raises(StateError, match="feature_identity_conflict"):
        authorize_request(screen, request)


@pytest.mark.parametrize("isolation", [True, False])
def test_ai_selfcheck_records_worker_not_applicable_without_fake_handshake(tmp_path, isolation):
    from pet.feature_package_probe import ProbeOutcome, SubprocessFeatureSelfChecker

    _, key = signed_package(tmp_path / "source")
    policy = verifier(key)

    class Sandbox:
        def run(self, request):
            assert request.policy["feature_id"] == AI
            assert request.policy["anchor_policy"]["release-2026"]["feature_ids"] == [AI]
            return ProbeOutcome(True, False, False, isolation, worker_status="not_applicable")

    result = SubprocessFeatureSelfChecker(policy, sandbox=Sandbox()).check(policy.verify(tmp_path / "source"))
    assert result.status == ("ready" if isolation else "failed")


def test_host_only_package_cannot_create_worker_lease_or_command(tmp_path):
    from pet.plugins.feature_packages import FeaturePackageLoader

    _, key = signed_package(tmp_path / "source")
    policy = verifier(key)
    loader = FeaturePackageLoader(policy)
    descriptor = policy.verify(tmp_path / "source")
    with pytest.raises(PackageVerificationError, match="host-only"):
        loader.acquire_worker(descriptor)
    assert loader.lease_counts(descriptor).worker == 0


def _valid_ai_factory(root, manifest, key, *, worker=False):
    body = (
        "from pet.plugins.feature_host import FeatureDefinition\n"
        "def create_host():\n"
        "    return FeatureDefinition('official.ai-chat', (), lambda *a: None" + (", worker_launch_factory=lambda: None" if worker else "") + ")\n"
    ).encode()
    (root / "host/factory.py").write_bytes(body)
    manifest["files"]["host/factory.py"] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    seal(root, manifest, key)


def test_ai_pending_is_confirmed_by_real_core_startup_without_worker(tmp_path):
    from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation
    from tests.test_feature_package_transactions import _startup

    manifest, key = signed_package(tmp_path / "source")
    _valid_ai_factory(tmp_path / "source", manifest, key)

    class FixtureChecker:
        def check(self, descriptor):
            return RuntimePreparation()

    service = FeaturePackageTransactionService(tmp_path / "data", verifier(key), self_checker=FixtureChecker())
    preflight = service.preflight_install(tmp_path / "source")
    result = service.apply(preflight.plan, confirmation_token=preflight.plan.confirmation_token)
    assert result.status == "awaiting_startup_confirmation"
    evidence = {}
    result = _startup(service, result.operation_id, evidence=evidence)
    assert result.status == "completed", evidence
    assert evidence["feature_id"] == AI and evidence["host_state"] == "enabled"
    assert evidence["bound"] and evidence["process_pin_live"]
    assert evidence["worker_leases"] == 0 and evidence["desktop_granted"] is False
    assert service.store.read().state.pending_transaction is None
    assert service.store.read().state.enabled


def test_ai_bootstrap_failure_is_scoped_to_ai_and_never_screen(tmp_path):
    from pet.feature_package_startup import ProductionFeatureStartup
    from pet.feature_package_transactions import FeaturePackageTransactionService
    from pet.plugins.feature_host import FeatureHost

    _, key = signed_package(tmp_path / "source")
    policy = verifier(key)
    store = FeatureInstallStateStore(tmp_path / "data", feature_id=AI)
    store.commit(StateChange({}, None, None, False), expected_revision=0, operation_id="fixture-init")
    target = store.root / "versions/1.0.0"
    import shutil

    shutil.copytree(tmp_path / "source", target)
    descriptor = policy.verify(target)
    digest = hashlib.sha256(descriptor.raw_manifest).hexdigest()
    store.commit(StateChange({"1.0.0": digest}, "1.0.0", None, True), expected_revision=1, operation_id="fixture-ai")
    host = FeatureHost()
    startup = ProductionFeatureStartup(
        FeaturePackageTransactionService(tmp_path / "data", policy), host, runtime_directory=tmp_path / "data/runtime", context_factory=lambda: None
    )
    result = startup.load_current()
    assert result.feature_id == AI
    assert host.state(SCREEN) == "absent"
    assert result.status == "recovery_required"


def test_host_only_factory_cannot_smuggle_worker_into_probe_or_binding(tmp_path):
    from pet.feature_package_probe import verified_host_probe
    from pet.plugins.feature_host import FeatureHost
    from pet.plugins.feature_packages import FeaturePackageLoader
    from pet.plugins.package_binding import bind_verified_feature

    manifest, key = signed_package(tmp_path / "source")
    _valid_ai_factory(tmp_path / "source", manifest, key, worker=True)
    policy = verifier(key)
    with pytest.raises(PackageVerificationError, match="host-only"):
        verified_host_probe(tmp_path / "source", policy)
    with pytest.raises(PackageVerificationError, match="host-only"):
        bind_verified_feature(FeatureHost(), FeaturePackageLoader(policy), policy.verify(tmp_path / "source"), runtime_directory=tmp_path / "runtime")


def test_inspection_is_explicitly_routed_to_its_owner(tmp_path):
    from pet.feature_package_transactions import FeaturePackageTransactionService

    _, key = signed_package(tmp_path / "source")
    service = FeaturePackageTransactionService(tmp_path / "data", verifier(key))
    assert service.inspect().feature_id == AI


@pytest.mark.parametrize("lock_phase", ["confirmation_lock", "post_confirm_state_lock"])
def test_real_receipt_survives_confirmation_lock_contention_without_reimport(tmp_path, lock_phase):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation
    from pet.feature_probe_adapter import probe_policy

    manifest, key = signed_package(tmp_path / "source")
    _valid_ai_factory(tmp_path / "source", manifest, key)

    class FixtureChecker:
        def check(self, descriptor):
            return RuntimePreparation()

    service = FeaturePackageTransactionService(tmp_path / "data", verifier(key), self_checker=FixtureChecker())
    plan = service.preflight_install(tmp_path / "source").plan
    assert service.apply(plan, confirmation_token=plan.confirmation_token).status == "awaiting_startup_confirmation"
    policy = probe_policy(service.verifier)
    policy["trust_anchors"] = {k: v.hex() for k, v in service.verifier.trust_anchors.items()}
    policy.pop("schema_version", None)
    payload = {"data_root": str(service.leases.data_root), "operation_id": plan.operation_id, "policy": policy, "success": True, lock_phase: True}
    child = subprocess.run(
        [sys.executable, "-m", "tests._feature_startup_child"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=45,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert child.returncode == 0, child.stderr
    evidence = json.loads(child.stdout)
    expected_reason = "management_lock_busy" if lock_phase == "confirmation_lock" else "state_lock_busy"
    assert evidence["retry"]["first_reason"] == expected_reason, evidence
    assert evidence["retry"]["first_host_state"] == "disabled"
    expected_pending = plan.operation_id if lock_phase == "confirmation_lock" else None
    assert evidence["retry"]["pending_before_retry"] == expected_pending
    assert evidence["retry"]["same_pin"] and evidence["retry"]["had_sealed_receipt"]
    assert evidence["status"] in ("completed", "idempotent") and evidence["host_state"] == "enabled", evidence
    assert service.store.read().state.pending_transaction is None


def test_balance_core_path_never_imports_ai_provider(monkeypatch):
    """Other optional domains cannot borrow AI implementation from small Core."""
    import io
    import urllib.request

    from pet.balance import fetch_balance

    original = builtins.__import__

    def deny_ai(name, globals=None, locals=None, fromlist=(), level=0):
        package = (globals or {}).get("__package__", "")
        if name.startswith("pet.chat") or (package == "pet" and name.startswith("chat")):
            raise AssertionError("Core balance attempted to import AI implementation")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", deny_ai)
    payload = {"balance_infos": [{"currency": "CNY", "total_balance": "2", "granted_balance": "1", "topped_up_balance": "1"}]}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    assert fetch_balance("https://generated.invalid", "generated-fixture-key")["total"] == "2"
