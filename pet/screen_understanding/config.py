"""Legacy Core constructor; the feature service only receives bound host ports."""

from pathlib import Path

from features.screen_understanding.host.config import Resolution as Resolution
from features.screen_understanding.host.config import VisionConfigService as _VisionConfigService
from features.screen_understanding.host.config import digest as digest

from ..credentials import CredentialVaultPort
from ..feature_host_bindings import bind_screen_configuration


def bind_vision_config(cfg, *, vault: CredentialVaultPort | None = None) -> _VisionConfigService:
    config, scoped_vault, legacy_reader = bind_screen_configuration(cfg, vault=vault)
    return _VisionConfigService(config, vault=scoped_vault, legacy_secret_reader=legacy_reader)


class VisionConfigService(_VisionConfigService):
    def __init__(self, cfg, *, vault: CredentialVaultPort | None = None):
        self.cfg = cfg  # Legacy constructor only; never passed to new feature host.
        self.path = Path(cfg.path) if hasattr(cfg, "path") else None
        bound = bind_vision_config(cfg, vault=vault)
        super().__init__(bound.config, vault=bound.vault, legacy_secret_reader=bound.read_legacy_secret)

    @property
    def journal_path(self) -> Path:
        if self.path is None:
            raise ValueError("configuration_unbound")
        return self.path.with_suffix(self.path.suffix + ".vision-migration.json")
