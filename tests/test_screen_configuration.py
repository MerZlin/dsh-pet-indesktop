"""Independent vision configuration: public seams, fake secure backend only."""

import json
from dataclasses import replace

import pytest

from pet.credentials import CredentialError, CredentialVaultPort
from pet.screen_understanding.config import VisionConfigService
from pet.screen_understanding.migration import VisionMigration
from pet.screen_understanding.models import VisionProfile, VisionSettings
from tests.screen_fakes import MemoryVault


@pytest.fixture
def config(tmp_path):
    from pet.config import Config

    cfg = Config(tmp_path)
    cfg.dir = tmp_path
    cfg.path = tmp_path / "config.json"
    cfg.data["chat"] = {
        "enabled": False,
        "active_provider": "a",
        "default_system_prompt": "old prompt",
        "providers": {
            "a": {
                "base_url": "https://chat.example/v1",
                "model": "deepseek-v4-flash",
                "api_key_ref": "old-chat",
                "vision_same_as_chat": False,
                "vision_base_url": "https://vision.example/v4",
                "vision_model": "v-model",
                "vision_api_key_ref": "old-vision",
            }
        },
    }
    cfg.data["proactive_screen"]["prefer_free_provider"] = False
    assert cfg.save()
    return cfg


@pytest.fixture
def services(config):
    backend = MemoryVault()
    vault = CredentialVaultPort("official.screen-understanding", str(config.path.resolve()), backend=backend)
    service = VisionConfigService(config, vault=vault)
    return service, backend


def test_no_confirmation_means_no_credentials_or_runtime_request(services):
    service, backend = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    preview = migration.preview()
    assert preview.automatic.model.endswith("vision-exp")
    assert preview.manual.model == "v-model"
    assert service.resolve("manual").reason == "not_configured"
    assert not backend.items
    assert "TEST-SECRET" not in repr(preview)


def test_confirmation_preserves_effective_modes_and_ignores_later_chat_changes(services):
    service, backend = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: {"old-chat": "CHAT-SECRET", "old-vision": "VISION-SECRET"}.get(ref, ""))
    migration.confirm(migration.preview())
    automatic = service.resolve("automatic")
    manual = service.resolve("manual")
    assert automatic.ready and manual.ready
    assert automatic.request.api_key == "CHAT-SECRET"
    assert manual.request.api_key == "VISION-SECRET"
    assert manual.request.base_url == "https://vision.example/v4"
    assert manual.request.system_prompt == "old prompt"
    service.cfg.data["chat"] = {}
    assert service.cfg.save()
    assert service.resolve("manual").request == manual.request
    assert "SECRET" not in service.cfg.path.read_text(encoding="utf-8")
    for path in service.cfg.dir.rglob("*.json"):
        assert "SECRET" not in path.read_text(encoding="utf-8")


def test_missing_visual_key_never_borrows_chat_key(services):
    service, _ = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "CHAT-SECRET" if ref == "old-chat" else "")
    migration.confirm(migration.preview())
    assert service.resolve("automatic").ready
    assert service.resolve("manual").reason == "credential_missing"


def test_backend_failure_is_pending_and_sanitized(services):
    service, backend = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    preview = migration.preview()
    backend.fail = True
    with pytest.raises(CredentialError) as error:
        migration.confirm(preview)
    assert "TEST-SECRET" not in str(error.value)
    assert not service.resolve("automatic").ready


def test_source_change_requires_new_preview(services):
    service, backend = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    preview = migration.preview()
    service.cfg.data["chat"]["providers"]["a"]["model"] = "changed"
    assert service.cfg.save()
    with pytest.raises(ValueError, match="source_changed"):
        migration.confirm(preview)
    assert not backend.items


def test_core_stale_save_preserves_settings_process_namespace(services):
    service, _ = services
    from pet.config import Config

    stale = Config(service.cfg.dir.parent)
    stale.dir, stale.path = service.cfg.dir, service.cfg.path
    stale.data = json.loads(stale.path.read_text(encoding="utf-8"))
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    migration.confirm(migration.preview())
    stale.data["scale"] = 1.23
    assert stale.save()
    stored = json.loads(stale.path.read_text(encoding="utf-8"))
    assert stored["plugins"]["official.screen-understanding"]["settings"]["bindings"]
    assert stored["scale"] == 1.23


def test_idempotent_confirm_cannot_overwrite_edit(services):
    service, _ = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    preview = migration.preview()
    migration.confirm(preview)
    settings = service.settings()
    profile = settings.profiles[settings.bindings["manual"]]
    service.save_profile(replace(profile, model="edited"), modes=["manual"], expected_revision=service.revision())
    with pytest.raises(ValueError, match="already_configured"):
        migration.confirm(preview)
    assert service.resolve("manual").request.model == "edited"


def test_scoped_credentials_reject_other_instance_profile_endpoint_or_operation():
    backend = MemoryVault()
    vault = CredentialVaultPort("official.screen-understanding", "instance-A", backend=backend)
    ref = vault.save("profile-A", "https://example.test/v1/chat/completions", "TEST-SECRET")
    assert vault.acquire(ref, "profile-A", "https://example.test/v1/chat/completions", "manual_look") == "TEST-SECRET"
    checks = [
        (vault, "profile-B", "https://example.test/v1/chat/completions", "manual_look"),
        (vault, "profile-A", "https://evil.test/v1/chat/completions", "manual_look"),
        (vault, "profile-A", "https://example.test/v1/chat/completions", "unknown"),
        (
            CredentialVaultPort("official.screen-understanding", "instance-B", backend=backend),
            "profile-A",
            "https://example.test/v1/chat/completions",
            "manual_look",
        ),
    ]
    for port, profile, endpoint, operation in checks:
        with pytest.raises(CredentialError):
            port.acquire(ref, profile, endpoint, operation)


def test_manual_configuration_does_not_require_chat_or_migration(services):
    service, _ = services
    service.cfg.data.pop("chat", None)
    profile = VisionProfile("manual", "https://vision.example/v1", "v-model", system_prompt="independent")
    service.save_profile(profile, modes=["manual"], secret="TEST-SECRET", expected_revision=service.revision())
    assert service.resolve("manual").ready
    assert not service.resolve("automatic").ready
    assert "TEST-SECRET" not in repr(service.resolve("manual"))


def test_models_reject_plaintext_secrets_and_bad_bindings():
    with pytest.raises(ValueError):
        VisionSettings.from_dict({"profiles": {"a": {"api_key": "TEST-SECRET"}}})
    with pytest.raises(ValueError):
        VisionSettings.from_dict({"bindings": {"manual": "missing"}})


def test_interrupted_migration_cleans_only_unreferenced_credentials(services):
    from pet.config_transaction import atomic_document

    service, backend = services
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    migration.confirm(migration.preview())
    active = set(backend.items)
    orphan = service.vault.save("orphan", "https://unused.example/v1/chat/completions", "ORPHAN")
    atomic_document(service.journal_path, {"phase": "prepared", "created_refs": [orphan]})
    migration.recover()
    assert set(backend.items) == active
    assert service.resolve("manual").ready
    migration.recover()
    assert set(backend.items) == active


def test_atomic_commit_failure_preserves_old_document_and_removes_new_credentials(services, monkeypatch):
    import pet.feature_config as module

    service, backend = services
    before = service.cfg.path.read_bytes()
    original = module.atomic_document

    def fail_commit(path, data):
        if path == service.cfg.path:
            raise OSError("test disk failure")
        original(path, data)

    monkeypatch.setattr(module, "atomic_document", fail_commit)
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    with pytest.raises(OSError):
        migration.confirm(migration.preview())
    assert service.cfg.path.read_bytes() == before
    assert not backend.items


def test_other_fields_are_reloaded_during_namespace_commit(services):
    service, backend = services
    original = backend.set_password

    def write_and_edit(service_name, ref, value):
        original(service_name, ref, value)
        doc = json.loads(service.cfg.path.read_text(encoding="utf-8"))
        doc["user_parallel_edit"] = "preserved"
        service.cfg.path.write_text(json.dumps(doc), encoding="utf-8")

    backend.set_password = write_and_edit
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    migration.confirm(migration.preview())
    assert json.loads(service.cfg.path.read_text(encoding="utf-8"))["user_parallel_edit"] == "preserved"


def test_worker_request_parser_uses_independent_model_not_chat():
    import inspect

    from pet.workers.proactive_screen_worker import ProactiveScreenWorker

    _provider_from_arguments = ProactiveScreenWorker._provider_from_arguments
    from pet.screen_understanding.models import VisionRequestConfig

    request = VisionRequestConfig("https://example.org", "exact-model", "TEST-SECRET")
    resolved = _provider_from_arguments(request.to_dict(include_secret=True))
    assert isinstance(resolved, VisionRequestConfig)
    assert resolved.model == "exact-model"
    assert "chat.models" not in inspect.getsource(_provider_from_arguments)


def test_adapter_never_resolves_keyring_references():
    from pet.screen_understanding.models import VisionRequestConfig
    from pet.workers.proactive_screen_adapter import ProactiveScreenWorkerAdapter

    request = VisionRequestConfig("https://example.org", "exact-model", "TEST-SECRET")
    payload = ProactiveScreenWorkerAdapter._provider_payload(request)
    assert payload["api_key"] == "TEST-SECRET"
    assert "api_key_ref" not in payload


def test_request_model_does_not_infer_chat_model():
    from pet.screen_understanding.models import VisionRequestConfig
    from pet.vision import resolve_vision_model

    assert resolve_vision_model(VisionRequestConfig("https://example.org", "deepseek-explicit")) == "deepseek-explicit"


@pytest.mark.parametrize(
    "provider, prompt",
    [
        ({"model": "DeepSeek-v4-flash"}, ""),
        ({"timeout": "bad", "temperature": None, "max_tokens": "1024.9"}, "legacy"),
        ({}, "legacy"),
    ],
)
def test_legacy_preview_matches_existing_resolver_defaults(services, provider, prompt):
    from pet.chat.models import ChatSettings
    from pet.vision import resolve_vision_model

    service, _ = services
    service.cfg.data["chat"] = {"providers": {"p": provider}, "active_provider": "p", "default_system_prompt": prompt}
    assert service.cfg.save()
    old = ChatSettings.from_dict(service.cfg.data["chat"])
    seen = []
    preview = VisionMigration(service, legacy_secret_reader=lambda ref: seen.append(ref) or "TEST-SECRET").preview()
    profile = preview.manual
    original = old.active_config
    assert profile.model == resolve_vision_model(original)
    assert profile.system_prompt == old.default_system_prompt
    assert (profile.timeout, profile.temperature, profile.max_tokens) == (original.timeout, original.temperature, original.max_tokens)
    assert original.api_key_ref in seen


def test_corrupt_disk_revision_invalidates_without_crashing_runtime(services):
    service, _ = services
    before = service.revision()
    service.cfg.path.write_text("{broken", encoding="utf-8")
    assert service.revision() != before
    assert service.resolve("manual").reason == "configuration_invalid"


def test_replacing_key_cleans_only_superseded_owned_reference(services):
    service, backend = services
    profile = VisionProfile("p", "https://vision.example", "model")
    service.save_profile(profile, modes=["manual"], secret="FIRST-SECRET", expected_revision=service.revision())
    old = service.settings().profiles["p"].credential_ref
    backend.items[("chat-service", "unrelated")] = "CHAT-SECRET"
    service.save_profile(profile, modes=["manual"], secret="SECOND-SECRET", expected_revision=service.revision())
    assert (service.vault.service, old) not in backend.items
    assert backend.items[("chat-service", "unrelated")] == "CHAT-SECRET"
    assert service.resolve("manual").request.api_key == "SECOND-SECRET"


@pytest.mark.parametrize("malformed", [[], None, "bad"])
def test_corrupt_namespace_is_pending_and_editor_does_not_overwrite(services, malformed):
    service, _ = services
    doc = json.loads(service.cfg.path.read_text(encoding="utf-8"))
    doc.setdefault("plugins", {})["official.screen-understanding"] = malformed
    service.cfg.path.write_text(json.dumps(doc), encoding="utf-8")
    assert service.resolve("manual").reason == "configuration_invalid"
    with pytest.raises(ValueError, match="configuration_invalid"):
        service.settings()


def test_real_core_process_stale_save_preserves_visual_namespace(services):
    import os
    import queue
    import subprocess
    import sys
    import threading

    service, _ = services
    # Child reads the old document before the parent settings process commits.
    code = """
import json, sys
from pathlib import Path
from pet.config import Config
cfg = Config(Path(sys.argv[1]))
cfg.path = Path(sys.argv[2])
cfg.data = json.loads(cfg.path.read_text(encoding='utf-8'))
print('loaded', flush=True)
sys.stdin.readline()
cfg.data['scale'] = 1.23
assert cfg.save()
print('saved', flush=True)
"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(service.cfg.dir), str(service.cfg.path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    startup = queue.Queue()
    reader = threading.Thread(target=lambda: startup.put(proc.stdout.readline()), daemon=True)
    reader.start()
    try:
        assert startup.get(timeout=15).strip() == "loaded"
        reader.join(timeout=1)
        migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
        migration.confirm(migration.preview())
        expected = service.settings()
        output, error = proc.communicate("continue\n", timeout=15)
        assert proc.returncode == 0, error
        assert "saved" in output
        assert service.settings() == expected
        assert json.loads(service.cfg.path.read_text(encoding="utf-8"))["scale"] == 1.23
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


def test_other_process_lock_is_nonblocking_and_preserves_document(services):
    import os
    import subprocess
    import sys

    from pet.config_transaction import file_transaction

    service, _ = services
    original = service.cfg.path.read_bytes()
    code = """
import sys
from pathlib import Path
from pet.config_transaction import atomic_document, file_transaction
try:
    with file_transaction(Path(sys.argv[1])):
        raise AssertionError('must not acquire another process lock')
except OSError as error:
    assert str(error) == 'configuration_busy'
    print('busy')
"""
    with file_transaction(service.cfg.path.with_suffix(".json.write.lock")):
        result = subprocess.run(
            [sys.executable, "-c", code, str(service.cfg.path.with_suffix(".json.write.lock"))],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "busy"
    assert service.cfg.path.read_bytes() == original


def test_vault_read_failure_and_unavailable_backend_are_distinct(services, monkeypatch):
    from pet import credentials

    service, backend = services
    service.save_profile(VisionProfile("p", "https://vision.example", "model"), modes=["manual"], secret="TEST-SECRET", expected_revision=service.revision())
    backend.fail = True
    assert service.resolve("manual").reason == "credential_read_failed"

    def unavailable():
        raise CredentialError("backend_unavailable")

    monkeypatch.setattr(credentials, "secure_backend", unavailable)
    assert VisionConfigService(service.cfg).resolve("manual").reason == "backend_unavailable"


def test_insecure_keyring_backend_is_rejected_without_access(monkeypatch):
    import keyring

    from pet.credentials import secure_backend

    class Plaintext:
        priority = 10

        def get_password(self, *args):
            raise AssertionError("must not read insecure store")

    monkeypatch.setattr(keyring, "get_keyring", lambda: Plaintext())
    with pytest.raises(CredentialError, match="backend_unavailable"):
        secure_backend()


def test_source_conflict_after_key_copy_cleans_only_new_keys(services):
    service, backend = services
    old = backend.set_password

    def save_and_change_source(*args):
        old(*args)
        doc = json.loads(service.cfg.path.read_text(encoding="utf-8"))
        doc["chat"]["default_system_prompt"] = "changed while saving"
        service.cfg.path.write_text(json.dumps(doc), encoding="utf-8")

    backend.set_password = save_and_change_source
    migration = VisionMigration(service, legacy_secret_reader=lambda ref: "TEST-SECRET")
    with pytest.raises(ValueError, match="source_changed"):
        migration.confirm(migration.preview())
    assert not backend.items
    assert not service.settings().profiles
