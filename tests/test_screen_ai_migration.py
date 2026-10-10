"""AI-owned DeepSeek credentials may be explicitly migrated to screen ownership."""

from __future__ import annotations

from features.screen_understanding.host.config import VisionConfigService
from pet.config import Config
from pet.credentials import CredentialVaultPort
from pet.feature_host_bindings import bind_screen_configuration
from pet.screen_understanding.migration import VisionMigration
from pet.screen_understanding.models import VisionProfile, VisionSettings


class MemorySecrets:
    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[service, username] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


class TestLayout:
    def credential_namespace(self, feature, instance):
        return f"{feature}:{instance}"


def _bound_services(tmp_path):
    layout = TestLayout()
    cfg = Config(tmp_path)
    cfg.dir = tmp_path
    cfg.path = tmp_path / "config.json"
    cfg.runtime_layout = layout
    cfg.instance_id = "primary"
    cfg.data["chat"] = {}
    cfg.data.setdefault("plugins", {})["official.ai-chat"] = {"chat": {}}
    assert cfg.save()

    backend = MemorySecrets()
    ai_vault = CredentialVaultPort("official.ai-chat", layout.credential_namespace("official.ai-chat", "primary"), backend=backend)
    ai_ref = ai_vault.save("openai-main", "https://api.deepseek.com", "GENERATED-AI-KEY")
    cfg.data["plugins"]["official.ai-chat"] = {
        "chat": {
            "active_provider": "openai-main",
            "providers": {
                "openai-main": {
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "chat_path": "/v1/chat/completions",
                    "model": "deepseek-v4-flash",
                    "api_key_ref": ai_ref,
                    "vision_same_as_chat": True,
                }
            },
        }
    }
    from pet.feature_config import bind_feature_configuration

    ai_port = bind_feature_configuration(cfg, "official.ai-chat", journal_path=cfg.path.with_suffix(".ai.json"))
    ai_port.commit_namespace(cfg.data["plugins"]["official.ai-chat"], expected_revision=ai_port.revision())

    screen_vault = CredentialVaultPort(
        "official.screen-understanding", layout.credential_namespace("official.screen-understanding", "primary"), backend=backend
    )
    bound, _, reader = bind_screen_configuration(cfg, vault=screen_vault)
    service = VisionConfigService(bound, vault=screen_vault, legacy_secret_reader=reader)
    return cfg, service, backend


def test_ai_namespace_is_explicitly_available_to_screen_migration(tmp_path):
    _, service, backend = _bound_services(tmp_path)
    migration = VisionMigration(service)

    preview = migration.preview()
    assert preview.automatic_credential == "available"
    assert preview.manual_credential == "available"
    migration.confirm(preview)

    automatic = service.resolve("automatic")
    manual = service.resolve("manual")
    assert automatic.ready and manual.ready
    assert automatic.request.api_key == "GENERATED-AI-KEY"
    assert manual.request.api_key == "GENERATED-AI-KEY"
    assert "GENERATED-AI-KEY" not in service.config.read_namespace().__repr__()
    assert len(backend.items) == 2


def test_existing_empty_screen_profile_can_be_repaired_from_ai_migration(tmp_path):
    _, service, backend = _bound_services(tmp_path)
    shared = VisionProfile("shared", "https://api.deepseek.com", "deepseek-v4-flash-vision-exp")
    settings = VisionSettings({"shared": shared}, {"automatic": "shared", "manual": "shared"}, "confirmed")
    service.config.commit_namespace({"schema_version": 1, "settings": settings.to_dict()}, expected_revision=service.revision())

    migration = VisionMigration(service)
    assert migration.repair_missing_credentials() is True
    assert service.resolve("automatic").request.api_key == "GENERATED-AI-KEY"
    assert service.resolve("manual").request.api_key == "GENERATED-AI-KEY"
    assert len(backend.items) == 2
    assert migration.repair_missing_credentials() is False
    assert len(backend.items) == 2
