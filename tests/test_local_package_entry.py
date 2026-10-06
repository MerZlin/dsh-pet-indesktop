"""Setup intent is a closed new-Core route, never a runtime-lock bypass."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_user_data(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-appdata"))


def configure(monkeypatch):
    import pet.__main__ as entry
    from pet import core_code_gate, local_package_intents, runtime_layout

    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "E:/generated-core/dsh-pet-core-webm.exe")
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setattr(core_code_gate, "hold_current_core_code", lambda: calls.append("barrier"))
    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", lambda: calls.append("layout") or object())
    monkeypatch.setattr(local_package_intents, "run_local_packages", lambda root, owners: calls.append((root, owners)) or 3)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("intent must not start normal settings, Core or Worker")

    monkeypatch.setitem(sys.modules, "pet.app", types.SimpleNamespace(main=unexpected))
    monkeypatch.setattr(entry, "_run_settings", unexpected)
    monkeypatch.setattr(entry, "_run_worker", unexpected)
    return entry, calls


def test_local_intent_holds_normal_barrier_then_layout_and_closed_gui(monkeypatch, tmp_path):
    entry, calls = configure(monkeypatch)
    root = tmp_path / "旁置 packages"
    monkeypatch.setattr(sys, "argv", ["core", "--install-local-packages", str(root), "official.ai-chat", "official.screen-understanding"])
    assert entry._main() == 3
    assert calls == ["barrier", "layout", (root, ("official.ai-chat", "official.screen-understanding"))]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["relative", "official.ai-chat"],
        ["E:/packages", "unknown"],
        ["E:/packages", "official.ai-chat", "official.ai-chat"],
        ["E:/packages", "official.ai-chat", "--settings"],
        ["E:/packages", "official.ai-chat", "--worker", "screen"],
    ],
)
def test_malformed_intent_stops_before_data_initialization(monkeypatch, args):
    entry, calls = configure(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["core", "--install-local-packages", *args])
    assert entry._main() == 64
    assert calls == ["barrier"]


@pytest.mark.parametrize(
    "variant,frozen,exe", [("core-webm", False, "dsh-pet-core-webm.exe"), ("webm-chat", True, "dsh-pet-core-webm.exe"), ("core-webm", True, "other.exe")]
)
def test_only_new_trusted_frozen_core_accepts_install_intent(monkeypatch, variant, frozen, exe):
    entry, calls = configure(monkeypatch)
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT=variant))
    monkeypatch.setattr(sys, "frozen", frozen)
    monkeypatch.setattr(sys, "executable", str(Path("E:/generated-core") / exe))
    monkeypatch.setattr(sys, "argv", ["core", "--install-local-packages", "E:/packages", "official.ai-chat"])
    assert entry._main() == 64
    assert calls == ["barrier"]


def test_mixed_entry_cannot_use_maintenance_bypass(monkeypatch):
    entry, calls = configure(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["core", "--core-maintenance", "uninstall", "--install-local-packages", "E:/packages", "official.ai-chat"])
    assert entry._main() == 64
    assert calls == []


def test_new_core_rejects_missing_layout_instead_of_legacy_fallback(monkeypatch):
    from pet import runtime_layout

    entry, calls = configure(monkeypatch)
    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", lambda: calls.append("layout") or None)
    monkeypatch.setattr(sys, "argv", ["core", "--install-local-packages", "E:/packages", "official.ai-chat"])
    assert entry._main() == 2
    assert calls == ["barrier", "layout"]
