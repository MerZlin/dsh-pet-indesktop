"""Maintenance cannot route to a normal host/settings/Worker under code barrier."""

from __future__ import annotations

import sys
import types

import pytest


def configure(monkeypatch):
    import pet.__main__ as entry
    from pet import core_code_gate, runtime_layout

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "E:/generated-core/dsh-pet-core-webm.exe")
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))

    def forbidden():
        raise AssertionError("maintenance must not acquire normal runtime lock or create ordinary data")

    monkeypatch.setattr(core_code_gate, "hold_current_core_code", forbidden)
    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", forbidden)
    monkeypatch.setattr(entry, "_run_settings", forbidden)
    return entry


def test_closed_maintenance_route_calls_only_trusted_removal_entry(monkeypatch):
    entry = configure(monkeypatch)
    calls = []
    monkeypatch.setitem(sys.modules, "pet.core_maintenance", types.SimpleNamespace(run_uninstall=lambda: calls.append("remove") or 3))
    monkeypatch.setattr(sys, "argv", ["core", "--core-maintenance", "uninstall"])
    assert entry._main() == 3
    assert calls == ["remove"]


@pytest.mark.parametrize("args", [[], ["anything"], ["uninstall", "--settings"], ["uninstall", "--worker", "screen"], ["uninstall", "../outside"]])
def test_maintenance_rejects_extra_routes_before_data_access(monkeypatch, args):
    entry = configure(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["core", "--core-maintenance", *args])
    assert entry._main() == 64


def test_legacy_or_source_maintenance_is_rejected_without_layout(monkeypatch):
    entry = configure(monkeypatch)
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(sys, "argv", ["core", "--core-maintenance", "uninstall"])
    assert entry._main() == 64
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="webm-chat"))
    assert entry._main() == 64


def test_normal_core_barrier_failure_precedes_layout_and_settings(monkeypatch):
    import pet.__main__ as entry
    from pet import core_code_gate, runtime_layout

    calls = []

    def busy():
        calls.append("barrier")
        raise core_code_gate.CoreCodeGateError("core_replacement_in_progress")

    def unexpected():
        raise AssertionError("blocked Core must not initialize user data")

    monkeypatch.setattr(core_code_gate, "hold_current_core_code", busy)
    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", unexpected)
    monkeypatch.setattr(sys, "argv", ["core", "--settings"])
    assert entry._main() == 3
    assert calls == ["barrier"]
