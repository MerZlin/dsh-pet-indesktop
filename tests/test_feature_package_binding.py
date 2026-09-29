"""Verified versions bind atomically to the existing host; no installation state."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from pet.plugins.feature_host import FeatureHost
from pet.plugins.feature_packages import FeaturePackageLoader, PackageVerificationError
from tests.test_feature_packages import package as package


@pytest.fixture
def ready(package, tmp_path):
    package.add(
        "host/factory.py",
        b"""from pet.plugins.feature_host import FeatureDefinition
from PySide6.QtWidgets import QWidget
def create_host():
    return FeatureDefinition("official.screen-understanding", (), lambda *a, **kw: QWidget())
""",
    )
    package.seal()
    verifier = package.verifier()
    loader = FeaturePackageLoader(verifier)
    descriptor = verifier.verify(package.root)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    app = QApplication.instance() or QApplication([])
    yield app, loader, descriptor, runtime, FeatureHost()


def test_bind_verified_package_forces_external_mode_and_holds_leases(ready):
    from pet.plugins.package_binding import bind_verified_feature

    app, loader, descriptor, runtime, host = ready
    binding = bind_verified_feature(host, loader, descriptor, runtime_directory=runtime)
    assert host.enabled(descriptor.id)
    assert loader.lease_counts(descriptor).host == 1
    definition = host._definitions[descriptor.id]
    assert not definition.allow_in_process
    launch = definition.worker_launch_factory()
    assert launch.program == str(descriptor.worker_path)
    assert loader.lease_counts(descriptor).worker == 1
    settings = host.settings(descriptor.id, "settings-instance").create()
    assert loader.lease_counts(descriptor).settings == 1
    settings.deleteLater()
    QCoreApplication.sendPostedEvents(settings, QEvent.Type.DeferredDelete)
    assert loader.lease_counts(descriptor).settings == 0
    assert binding.close()
    assert binding.close()  # token is idempotent
    assert not host.enabled(descriptor.id)
    with pytest.raises(PackageVerificationError, match="unavailable"):
        definition.worker_launch_factory()
    assert loader.lease_counts(descriptor).host == 0
    assert loader.lease_counts(descriptor).worker == 1  # native exit owns this release
    launch.close()
    assert loader.lease_counts(descriptor).worker == 0
    assert loader.lease_counts(descriptor).imported  # no Python hot unload promise


def test_draft_veto_retains_host_and_version(ready):
    from pet.plugins.package_binding import bind_verified_feature

    _, loader, descriptor, runtime, host = ready
    binding = bind_verified_feature(host, loader, descriptor, runtime_directory=runtime)
    veto = host.before_remove(descriptor.id, lambda: False)
    assert not binding.close()
    assert host.enabled(descriptor.id)
    assert loader.lease_counts(descriptor).host == 1
    veto()
    assert binding.close()


def test_invalid_definition_cannot_register_owner(ready, package):
    from pet.plugins.package_binding import bind_verified_feature

    _, loader, _, runtime, host = ready
    package.add(
        "host/factory.py",
        b"""from pet.plugins.feature_host import FeatureDefinition
def create_host():
    return FeatureDefinition("other.owner", (), lambda: None)
""",
    )
    package.seal()
    descriptor = loader.verifier.verify(package.root)
    with pytest.raises(PackageVerificationError, match="definition"):
        bind_verified_feature(host, loader, descriptor, runtime_directory=runtime)
    assert host.owners() == ()
    assert loader.lease_counts(descriptor).host == 0


def test_failed_settings_factory_releases_only_its_lease(ready):
    from pet.plugins.package_binding import bind_verified_feature

    _, loader, descriptor, runtime, host = ready
    binding = bind_verified_feature(host, loader, descriptor, runtime_directory=runtime)
    # Tamper is refused *before* invoking the saved factory.
    (descriptor.root / "host/factory.py").write_text("raise AssertionError('executed')", encoding="utf-8")
    with pytest.raises(PackageVerificationError):
        host.settings(descriptor.id, "settings").create()
    assert loader.lease_counts(descriptor).settings == 0
    assert binding.close()


def test_app_shell_accepts_explicit_empty_host_before_shared_services(tmp_path):
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell
    from pet.config import Config
    from pet.plugins.feature_host import FeatureHost

    app = QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path, instance_id="validation-41")
    cfg.set("experimental_single_process_spawn", True)
    host = FeatureHost()
    shell = AppShell(app, cfg, enable_chat=False, feature_host=host)
    try:
        assert shell.feature_host is host
        assert not host.enabled("official.screen-understanding")
        assert shell._shared.proactive is None
    finally:
        shell._on_about_to_quit()
