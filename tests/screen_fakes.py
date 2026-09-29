"""Explicit independent vision provisioning; never reads a real credential backend."""

from pet.screen_understanding.config import VisionConfigService
from pet.screen_understanding.models import VisionProfile


class MemoryVault:
    priority = 1

    def __init__(self):
        self.items = {}
        self.fail = False

    def set_password(self, service, ref, value):
        if self.fail:
            raise RuntimeError("TEST-SECRET backend write failed")
        self.items[service, ref] = value

    def get_password(self, service, ref):
        if self.fail:
            raise RuntimeError("TEST-SECRET backend read failed")
        return self.items.get((service, ref))

    def delete_password(self, service, ref):
        self.items.pop((service, ref), None)


def configure_vision(cfg, monkeypatch=None, *, modes=("automatic", "manual"), key="one-shot-secret", model="vision-model", base_url="https://visual.invalid"):
    if monkeypatch is not None:
        backend = MemoryVault()
        monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    # Caller without monkeypatch must already install a fake secure backend.
    assert cfg.save()
    service = VisionConfigService(cfg)
    service.save_profile(
        VisionProfile("test", base_url, model, system_prompt="look at the screen"), modes=list(modes), secret=key, expected_revision=service.revision()
    )
    return service
