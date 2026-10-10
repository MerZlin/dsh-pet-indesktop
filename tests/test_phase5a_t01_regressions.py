"""T01 Phase5A regressions: start from the neutral local-package contract."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet import __version__
from pet.official_features import AI_FEATURE_ID
from pet.plugins.feature_host import FeatureHost
from pet.plugins.feature_packages import FeaturePackageLoader
from pet.plugins.package_binding import bind_verified_feature
from pet.plugins.package_trust import FeaturePackageVerifier
from scripts.build_screen_delivery import ROOT


def _write_local_v2_package(root: Path, *, owner: str = "third-party.example") -> Path:
    root.mkdir(parents=True)
    files: dict[str, dict[str, object]] = {}

    def add(relative: str, data: bytes) -> None:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    add("host/__init__.py", b"")
    add(
        "host/factory.py",
        (f"from pet.plugins.feature_host import FeatureDefinition\ndef create_host():\n    return FeatureDefinition({owner!r}, (), lambda: None)\n").encode(),
    )
    manifest = {
        "format_version": 2,
        "execution_kind": "host-only",
        "key_id": "local-user",
        "id": owner,
        "version": "1.0.0",
        "api_version": "1",
        "core_requires": ">=4.2.1,<6.0.0",
        "platforms": [sys.platform],
        "capabilities": [],
        "factory": "third-party/v1",
        "worker": None,
        "files": files,
    }
    (root / "manifest.json").write_bytes((json.dumps(manifest, sort_keys=True) + "\n").encode())
    return root


def test_local_v2_unknown_owner_can_verify_install_and_start(tmp_path):
    owner = "third-party.example"
    package = _write_local_v2_package(tmp_path / "package", owner=owner)
    verifier = FeaturePackageVerifier(
        core_version=__version__,
        api_version="1",
        platform=sys.platform,
        allowed_capabilities=set(),
        allow_local_packages=True,
        feature_id=owner,
    )

    descriptor = verifier.verify(package)
    assert descriptor.id == owner
    assert descriptor.execution_kind == "host-only"
    assert descriptor.trust_status == "local_user"

    host = FeatureHost()
    binding = bind_verified_feature(
        host,
        FeaturePackageLoader(verifier),
        descriptor,
        runtime_directory=tmp_path / "runtime",
    )
    try:
        assert host.enabled(owner)
        assert host.owners() == (owner,)
    finally:
        assert binding.close()


def test_screen_entry_routes_ai_zip_by_manifest_registration(tmp_path):
    from pet.local_package_intents import route_local_package
    from scripts.build_screen_delivery import assemble_ai_package

    package = assemble_ai_package(ROOT, tmp_path / "ai-package")
    archive = tmp_path / "ai-package.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in package.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(package).as_posix())

    directory_route = route_local_package(package)
    zip_route = route_local_package(archive)
    assert directory_route.feature_id == AI_FEATURE_ID
    assert zip_route.feature_id == AI_FEATURE_ID
    assert directory_route.manifest_digest == zip_route.manifest_digest


def test_setup_embeds_official_packages_without_external_source_packages():
    script = (ROOT / "packaging/core_webm.iss").read_text(encoding="utf-8")
    assert "official.ai-chat.zip" in script
    assert "official.screen-understanding.zip" in script
    assert "{src}\\packages" not in script
    assert "--install-local-packages" not in script


def test_core_spec_is_windowless(tmp_path, monkeypatch):
    import scripts.build_screen_delivery as delivery

    monkeypatch.setattr(delivery.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(delivery, "verify_core_bundle", lambda *args, **kwargs: tmp_path / "core" / "dist" / "core.exe")
    output = tmp_path / "core"
    delivery.build_core(ROOT, output, chat=False)
    spec = (output / "validation.spec").read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "console=True" not in spec


@pytest.mark.parametrize("as_zip", [False, True])
def test_local_v2_unknown_owner_transaction_and_production_startup(tmp_path, as_zip):
    from pet.config import Config
    from pet.feature_host_bindings import bind_local_context
    from pet.feature_package_startup import ProductionFeatureStartup
    from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation

    owner = "third-party.example"
    package = _write_local_v2_package(tmp_path / "package", owner=owner)
    verifier = FeaturePackageVerifier(
        core_version=__version__,
        api_version="1",
        platform=sys.platform,
        allowed_capabilities=set(),
        allow_local_packages=True,
        feature_id=owner,
    )

    class ReadyChecker:
        def check(self, descriptor):
            return RuntimePreparation()

    service = FeaturePackageTransactionService(tmp_path / "install-data", verifier, self_checker=ReadyChecker())
    source = package
    if as_zip:
        source = tmp_path / "package.zip"
        with zipfile.ZipFile(source, "w") as output:
            for path in package.rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(package).as_posix())
    preflight = service.preflight_install(source)
    assert preflight.status == "awaiting_confirmation"
    applied = service.apply(preflight.plan, confirmation_token=preflight.plan.confirmation_token)
    assert applied.status == "awaiting_startup_confirmation"
    assert service.store.read().state.active == "1.0.0"

    cfg = Config(base=tmp_path / "config-root")
    host = FeatureHost()
    startup = ProductionFeatureStartup(
        service,
        host,
        runtime_directory=service.leases.data_root / "feature-runtime",
        context_factory=lambda: bind_local_context(cfg, owner),
    )
    try:
        result = startup.load_current()
        assert result.status == "completed"
        assert host.enabled(owner)
        assert startup.context is not None
        assert startup.context.owner == owner
        assert startup.context.desktop is None
    finally:
        if startup.binding is not None:
            assert startup.binding.close()


def test_unknown_local_install_state_is_discovered_without_official_allowlist(tmp_path):
    from pet.config import Config
    from pet.feature_install_state import FeatureInstallStateStore, StateChange
    from pet.feature_management import discover_local_feature_ids

    config = Config(base=tmp_path / "config")
    owner = "third-party.example"
    store = FeatureInstallStateStore(config.dir, feature_id=owner)
    store.commit(
        StateChange({"1.0.0": "a" * 64}, active="1.0.0", enabled=False),
        expected_revision=0,
        operation_id="discover-local",
    )
    (config.dir / "plugins" / AI_FEATURE_ID).mkdir(parents=True, exist_ok=True)
    (config.dir / "plugins" / "not a feature").mkdir(parents=True, exist_ok=True)

    assert discover_local_feature_ids(config) == (owner,)


def test_screen_manager_routes_unknown_owner_to_its_generic_manager(tmp_path, monkeypatch):
    import time

    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from pet import feature_distribution
    from pet.config import Config
    from pet.feature_management import attach_feature_management
    from pet.feature_package_transactions import RuntimePreparation

    owner = "third-party.example"
    package = _write_local_v2_package(tmp_path / "package", owner=owner)
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "config")
    host = FeatureHost()
    source_manager = attach_feature_management(config, host, feature_id="official.screen-understanding", role="settings", management_only=True)
    target_manager = attach_feature_management(config, host, feature_id=owner, role="settings", management_only=True)

    class ReadyChecker:
        def check(self, descriptor):
            return RuntimePreparation()

    target_manager.service.self_checker = ReadyChecker()
    routed = []
    source_manager.package_routed.connect(routed.append)
    try:
        assert source_manager.submit_local_source(package, auto_apply=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and (target_manager.last_result is None or target_manager.last_result.status != "awaiting_startup_confirmation"):
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert routed and routed[0].feature_id == owner
        assert target_manager.last_result is not None
        assert target_manager.last_result.status == "awaiting_startup_confirmation"
        state = target_manager.service.store.read().state
        assert state is not None and state.feature_id == owner and state.active == "1.0.0"
    finally:
        target_manager.close()
        source_manager.close()
        app.processEvents()


def test_setup_maintenance_installs_embedded_official_zip_without_qt(tmp_path):
    from pet.config import Config
    from pet.core_maintenance import run_install_packages
    from pet.feature_install_state import FeatureInstallStateStore
    from pet.feature_package_transactions import FeaturePackageTransactionService, RuntimePreparation
    from scripts.build_screen_delivery import assemble_ai_package

    package_dir = tmp_path / "embedded"
    package = assemble_ai_package(ROOT, tmp_path / "ai-package")
    archive = package_dir / "official.ai-chat.zip"
    package_dir.mkdir()
    with zipfile.ZipFile(archive, "w") as output:
        for path in package.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(package).as_posix())
    config = Config(base=tmp_path / "config")

    class ReadyChecker:
        def check(self, descriptor):
            return RuntimePreparation()

    def service_factory(owner, verifier, cfg):
        assert owner == AI_FEATURE_ID
        return FeaturePackageTransactionService(cfg.dir, verifier, self_checker=ReadyChecker())

    assert run_install_packages(archive.parent, (AI_FEATURE_ID,), config=config, service_factory=service_factory) == 0
    log_records = [json.loads(line) for line in (config.dir / "core-maintenance.log").read_text(encoding="utf-8").splitlines()]
    assert log_records[-1]["event"] == "install-packages"
    assert log_records[-1]["code"] == 0
    assert log_records[-1]["status"] == "completed"
    assert log_records[-1]["owners"] == [AI_FEATURE_ID]
    state = FeatureInstallStateStore(config.dir, feature_id=AI_FEATURE_ID).read().state
    assert state is not None and state.active == json.loads((package / "manifest.json").read_text(encoding="utf-8"))["version"] and state.enabled
    # Setup retries must preserve the same accepted startup frontier, not fail
    # because the first normal Core launch has not happened yet.
    assert run_install_packages(archive.parent, (AI_FEATURE_ID,), config=config, service_factory=service_factory) == 0
    retried = FeatureInstallStateStore(config.dir, feature_id=AI_FEATURE_ID).read().state
    assert retried is not None and retried.document() == state.document()


@pytest.mark.parametrize("legacy_signature", [False, True])
def test_unknown_local_owner_ignores_optional_legacy_signature(tmp_path, legacy_signature):
    owner = "third-party.example"
    package = _write_local_v2_package(tmp_path / "package", owner=owner)
    if legacy_signature:
        (package / "manifest.sig").write_bytes(b"not a publisher signature".ljust(64, b"!"))
    verifier = FeaturePackageVerifier(core_version=__version__, api_version="1", allow_local_packages=True, feature_id=owner)
    assert verifier.verify(package).trust_status == "local_user"


def test_local_verifier_pins_explicit_screen_route_instead_of_default_owner_exception(tmp_path):
    from pet.official_features import SCREEN_FEATURE_ID
    from pet.plugins.package_trust import PackageVerificationError

    package = _write_local_v2_package(tmp_path / "package")
    verifier = FeaturePackageVerifier(core_version=__version__, api_version="1", allow_local_packages=True, feature_id=SCREEN_FEATURE_ID)
    with pytest.raises(PackageVerificationError, match="owner"):
        verifier.verify(package)


def test_signed_only_policy_still_rejects_unknown_owner():
    from pet.plugins.package_trust import PackageVerificationError

    with pytest.raises(PackageVerificationError, match="Core feature policy"):
        FeaturePackageVerifier(core_version=__version__, api_version="1", feature_id="third-party.example")


def test_settings_dialog_keeps_routed_local_manager_in_its_lifecycle_map(tmp_path):
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.feature_management import attach_feature_management, close_official_management
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "config")
    dialog = ModernSettingsDialog(config, include_ai=False, standalone=True, initial_page="extensions")
    owner = "third-party.example"
    manager = attach_feature_management(config, dialog.feature_host, feature_id=owner, role="settings", management_only=True)
    try:
        widget = dialog._mount_local_feature_manager(None, manager)
        assert dialog.feature_managers[owner] is manager
        assert widget is dialog.mod_center
        assert dialog._mount_local_feature_manager(None, manager) is widget
        assert owner in dialog.mod_controller._connected
    finally:
        close_official_management(dialog.feature_host)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_unified_import_buttons_remain_available_with_builtin_official_features(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.settings_widgets import SettingRow

    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path / "config"), include_ai=False, standalone=True, initial_page="extensions")
    chosen = []
    try:
        import_row = dialog.findChild(SettingRow, "settingRow_local_package_import")
        assert import_row is not None
        assert dialog.pages.widget(0).isAncestorOf(import_row)
        local_row = dialog.findChild(SettingRow, "settingRow_local_feature_packages")
        assert local_row is None  # unified list replaces the second card group
        assert dialog.local_package_zip_button.isEnabled()
        assert dialog.local_package_directory_button.isEnabled()
        source_widget = dialog.mod_center
        monkeypatch.setattr(source_widget, "choose_source", lambda archive: chosen.append(archive))
        dialog.local_package_zip_button.click()
        dialog.local_package_directory_button.click()
        assert chosen == [True, False]
        dialog.show()
        app.processEvents()
        assert dialog.select_page("extensions")
        app.processEvents()
        assert dialog.focusWidget() is dialog.local_package_zip_button
    finally:
        from pet.feature_management import close_official_management

        close_official_management(dialog.feature_host)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
