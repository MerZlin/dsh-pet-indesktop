"""Portable data stays inside the project, but never inside executable trees."""

from __future__ import annotations

import hashlib
import os
import shutil

import pytest

from pet.feature_install_state import FeatureInstallStateStore, StateChange
from pet.feature_state_io import StateError
from pet.feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection
from pet.plugins.feature_packages import FeaturePackageLoader, PackageVerificationError
from pet.plugins.worker_launch import verified_worker_launch
from tests.test_feature_packages import package as package


@pytest.fixture
def installed(package, tmp_path):
    project = tmp_path / "project"
    data = project / "data"
    store = FeatureInstallStateStore(data)
    target = store.root / "versions" / package.manifest["version"]
    verifier = package.verifier()
    digest = hashlib.sha256((package.root / "manifest.json").read_bytes()).hexdigest()
    store.commit(StateChange({"1.2.3": digest}, active="1.2.3", enabled=True), expected_revision=0, operation_id="install")
    shutil.copytree(package.root, target)
    package.root = target
    selection = FeatureVersionSelection.from_resolution(store.resolve_verified(verifier, purpose="execution"))
    runtime = data / "feature-runtime"
    runtime.mkdir()
    (project / "_internal").mkdir()
    return project, runtime, FeaturePackageLoader(verifier), selection, FeatureVersionLeaseCoordinator(store)


def launch(installed, runtime=None, **kwargs):
    project, owned, loader, selection, coordinator = installed
    return verified_worker_launch(
        loader,
        selection.descriptor,
        runtime_directory=runtime or owned,
        core_roots=(project, project / "_internal"),
        selection=selection,
        lease_coordinator=coordinator,
        **kwargs,
    )


@pytest.mark.parametrize("child", ["", "official.screen-understanding/session"])
def test_portable_owned_runtime_launches_and_retains_isolation(installed, child):
    project, runtime, loader, selection, coordinator = installed
    runtime = runtime / child
    runtime.mkdir(parents=True, exist_ok=True)
    item = launch(installed, runtime, environment={"PYTHONPATH": "bad", "PATH": str(project / "_internal"), "HTTPS_PROXY": "https://proxy.invalid"})
    try:
        assert item.working_directory == str(runtime.resolve())
        assert item.requires_handoff and item.handoff_token
        assert "PYTHONPATH" not in item.environment and item.environment["PATH"] == ""
        assert item.environment["HTTPS_PROXY"] == "https://proxy.invalid"
        assert coordinator.inspect_occupancy(selection.version).status == "occupied"
    finally:
        item.close()
    assert coordinator.inspect_occupancy(selection.version).status == "free"
    assert loader.lease_counts(selection.descriptor).worker == 0


@pytest.mark.parametrize("relative", [".", "_internal", "data", "data/other", "data/plugins/official.screen-understanding/versions/1.2.3/worker"])
def test_portable_exception_does_not_allow_program_or_arbitrary_directories(installed, relative):
    project, _, _, selection, coordinator = installed
    runtime = project / relative
    runtime.mkdir(parents=True, exist_ok=True)
    with pytest.raises((ValueError, StateError)):
        launch(installed, runtime)
    assert coordinator.inspect_occupancy(selection.version).status == "free"


def test_portable_runtime_needs_installed_selection_and_coordinator(installed):
    project, runtime, loader, selection, _ = installed
    with pytest.raises((ValueError, StateError)):
        verified_worker_launch(loader, selection.descriptor, runtime_directory=runtime, core_roots=(project,))


def test_portable_launch_reverification_failure_releases_reservation(installed):
    _, _, loader, selection, coordinator = installed
    selection.descriptor.worker_path.write_bytes(b"changed after selection")
    with pytest.raises(PackageVerificationError):
        launch(installed)
    assert coordinator.inspect_occupancy(selection.version).status == "free"
    assert loader.lease_counts(selection.descriptor).worker == 0


def test_runtime_link_is_rejected_even_when_resolved_target_is_outside_core(installed, tmp_path):
    project, _, _, selection, coordinator = installed
    target = tmp_path / "outside"
    target.mkdir()
    link = project / "data/feature-runtime/redirect"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    try:
        with pytest.raises((ValueError, StateError)):
            launch(installed, link)
        assert coordinator.inspect_occupancy(selection.version).status == "free"
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()  # Unlink only this owned junction; never traverse its target.
