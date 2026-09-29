"""Bounded independent-vision measurements; synthetic credentials, no desktop/network I/O."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pet.config_transaction import SCREEN_NAMESPACE, atomic_document, save_core_document  # noqa: E402
from pet.credentials import CredentialVaultPort  # noqa: E402
from pet.screen_understanding.config import VisionConfigService  # noqa: E402
from pet.screen_understanding.migration import VisionMigration  # noqa: E402
from pet.screen_understanding.models import VisionProfile  # noqa: E402


class MemoryVault:
    """Deliberately not a native keyring backend; never stores real credentials."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[service, name] = value

    def delete_password(self, service, name):
        self.items.pop((service, name), None)


def make_service(root, name, *, legacy=False):
    path = root / name / "config.json"
    path.parent.mkdir(parents=True)
    data = {"plugins": {}}
    if legacy:
        data = {
            "chat": {
                "enabled": True,
                "active_provider": "test",
                "providers": {
                    "test": {
                        "base_url": "https://visual.invalid",
                        "model": "vision-test",
                        "api_key_ref": "provider/test",
                        "vision_same_as_chat": True,
                    }
                },
            }
        }
    atomic_document(path, data)
    cfg = SimpleNamespace(path=path, dir=path.parent, data=data)
    vault = CredentialVaultPort(SCREEN_NAMESPACE, str(path.resolve()), backend=MemoryVault())
    return VisionConfigService(cfg, vault=vault)


def measure(function, count):
    samples = []
    for i in range(count):
        start = time.perf_counter_ns()
        function(i)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    samples.sort()
    return {
        "samples": count,
        "mean_ms": round(statistics.mean(samples), 4),
        "p50_ms": round(statistics.median(samples), 4),
        "p95_ms": round(samples[int((count - 1) * 0.95)], 4),
    }


def main():
    scratch = ROOT / ".scratch" / "phase4a-vision-config"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=scratch) as temp:
        root = Path(temp)
        ready = make_service(root, "ready")
        ready.save_profile(
            VisionProfile("manual", "https://visual.invalid", "vision-test"),
            modes=["automatic", "manual"],
            secret="BENCHMARK-FAKE-KEY",
            expected_revision=ready.revision(),
        )
        pending = make_service(root, "pending")

        def reader(ref):
            return "BENCHMARK-FAKE-KEY" if ref == "provider/test" else ""

        migration = VisionMigration(make_service(root, "preview", legacy=True), legacy_secret_reader=reader)
        ready.resolve("manual")
        migration.preview()
        threads = threading.active_count()
        out = {"backend": "MemoryVault, no native keyring/capture/network", "python": sys.version.split()[0]}
        out["pending_resolution"] = measure(lambda i: pending.resolve("automatic"), 500)
        out["ready_resolution"] = measure(lambda i: ready.resolve("manual"), 500)
        out["migration_preview"] = measure(lambda i: migration.preview(), 500)

        # Prepare independent fixtures first: the timed section is confirmation, not fixture construction.
        confirmations = []
        for i in range(25):
            service = make_service(root, f"confirm-{i}", legacy=True)
            move = VisionMigration(service, legacy_secret_reader=reader)
            confirmations.append((service, move, move.preview()))
        out["migration_confirm"] = measure(lambda i: confirmations[i][1].confirm(confirmations[i][2]), 25)
        for service, _, _ in confirmations:
            for mode in ("automatic", "manual"):
                result = service.resolve(mode)
                assert result.ready and result.request is not None
                assert result.request.base_url == "https://visual.invalid"
                assert result.request.model == "vision-test"
                assert result.request.api_key == "BENCHMARK-FAKE-KEY"
        out["stale_core_save"] = measure(lambda i: save_core_document(ready.path, {"core_test": i}), 25)
        tracemalloc.start()
        for _ in range(500):
            assert ready.resolve("manual").ready
        retained, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        out["500_resolutions_tracemalloc_bytes"] = {"retained": retained, "peak": peak}
        out["thread_delta"] = threading.active_count() - threads
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
