"""Closed import entry never starts normal Core or obtains a self-blocking data pin."""

from __future__ import annotations

import sys
import types

import pytest

from tests.test_runtime_data_import import fixture_import


def setup_entry(monkeypatch, tmp_path):
    import pet.__main__ as entry
    from pet import core_code_gate, runtime_data_import_entry, runtime_layout

    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-appdata"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "dsh-pet-core-webm.exe"))
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    calls = []
    monkeypatch.setattr(core_code_gate, "hold_current_core_code", lambda: calls.append("code-barrier"))
    monkeypatch.setattr(runtime_data_import_entry, "run_data_import", lambda: calls.append("import-ui") or 3)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("import entry must not load normal Core, settings, Worker or acquire ordinary-data session")

    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", unexpected)
    monkeypatch.setattr(entry, "_run_settings", unexpected)
    monkeypatch.setattr(entry, "_run_worker", unexpected)
    monkeypatch.setitem(sys.modules, "pet.app", types.SimpleNamespace(main=unexpected))
    return entry, calls


def test_closed_entry_holds_code_gate_before_import_only_bootstrap(tmp_path, monkeypatch):
    entry, calls = setup_entry(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["core", "--import-local-data"])
    assert entry._main() == 3
    assert calls == ["code-barrier", "import-ui"]


@pytest.mark.parametrize(
    "args",
    [
        ["--settings", "--import-local-data"],
        ["--import-local-data", "E:/unconfirmed-profile"],
        ["--import-local-data", "--worker", "screen"],
        ["--import-local-data", "--install-local-packages"],
    ],
)
def test_extra_or_conflicting_args_rejected_before_user_data_io(tmp_path, monkeypatch, args):
    entry, calls = setup_entry(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["core", *args])
    assert entry._main() == 64
    assert calls == ["code-barrier"]


@pytest.mark.parametrize("variant", ["full-webm", "core-no-screen-webm"])
def test_legacy_build_cannot_enter_new_product_import(tmp_path, monkeypatch, variant):
    entry, calls = setup_entry(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT=variant))
    monkeypatch.setattr(sys, "argv", ["core", "--import-local-data"])
    assert entry._main() == 64
    assert calls == ["code-barrier"]


def test_source_invocation_cannot_bypass_current_build_layout(tmp_path, monkeypatch):
    entry, calls = setup_entry(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(sys, "argv", ["python", "--import-local-data"])
    assert entry._main() == 64
    assert calls == ["code-barrier"]


def test_import_gate_blocks_core_removal_but_not_its_own_confirmed_write(tmp_path, monkeypatch):
    from pet import feature_state_io as io

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    with layout.acquire_import_gate():
        with pytest.raises(io.StateError, match="lock_busy"):
            io.open_kernel_lock(layout.data_root / "core-removal.lock")
        preview = importer.preflight(source)
        assert importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token).status == "completed"
    with io.open_kernel_lock(layout.data_root / "core-removal.lock"):
        pass


def test_pending_import_can_be_recovered_without_normal_runtime_access(tmp_path, monkeypatch):
    from pet.runtime_layout import RuntimeLayoutError

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)

    def fail(stage):
        if stage == "accepted":
            raise OSError("generated interruption")

    monkeypatch.setattr(importer, "_checkpoint", fail)
    assert importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token).status == "recovery_required"
    with pytest.raises(RuntimeLayoutError, match="data_import_recovery_required"):
        layout.acquire_session()
    monkeypatch.setattr(importer, "_checkpoint", lambda _stage: None)
    with layout.acquire_import_gate():
        assert importer.recover_pending().status == "completed"


def test_launcher_never_falls_back_to_source_or_old_product(tmp_path, monkeypatch):
    from pet import runtime_data_import_entry

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert not runtime_data_import_entry.launch_data_import()


@pytest.mark.parametrize("alias", ["data-import", "旧数据", "迁移", "导入", "便携"])
def test_real_settings_import_action_is_discoverable_but_does_not_import(tmp_path, monkeypatch, alias):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as ui
    from pet import runtime_data_import_entry as route
    from pet import runtime_layout
    from pet.config import Config
    from pet.settings_widgets import SettingRow

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_layout, "_current_layout", layout)
    monkeypatch.setattr(ui.autostart_mod, "is_enabled", lambda: False)
    launches = []
    monkeypatch.setattr(route, "launch_data_import", lambda: launches.append("closed-core-import-command") or True)
    app = QApplication.instance() or QApplication([])
    config = Config(layout=layout)
    assert config.save()
    before = config.path.read_bytes()
    dialog = ui.ModernSettingsDialog(config, include_ai=False, standalone=True, initial_page="extensions")
    dialog.show()
    try:
        app.processEvents()
        row = dialog.findChild(SettingRow, "settingRow_legacy_data_import")
        assert row is not None and dialog.pages.widget(0).isAncestorOf(row)
        if alias == "data-import":
            assert dialog.select_page(alias)
            assert dialog.sidebar.currentItem().text() == "常规"
            assert dialog.data_import_button.hasFocus()
        else:
            dialog.search_edit.setText(alias)
            app.processEvents()
            assert row in dialog._search_matches
        assert dialog.data_import_button.accessibleName()
        assert not launches and not importer.pending.exists()
        assert not list(importer.root.glob("import-*"))
        dialog.data_import_button.click()
        assert launches == ["closed-core-import-command"]
        assert config.path.read_bytes() == before
        assert source.is_dir()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
