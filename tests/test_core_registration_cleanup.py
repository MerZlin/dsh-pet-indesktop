"""OS registry boundary is a generated fake; no real startup registration is touched."""

from pathlib import Path

import pytest


class Registry:
    HKEY_CURRENT_USER = 1
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, value=None, *, denied=False):
        self.value = value
        self.denied = denied
        self.deleted = []
        self.queried = []

    def OpenKey(self, *args):
        if self.denied:
            raise PermissionError("generated registry permission denial")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def QueryValueEx(self, key, name):
        self.queried.append(name)
        if self.value is None:
            raise FileNotFoundError(name)
        return self.value, self.REG_SZ

    def DeleteValue(self, key, name):
        self.deleted.append(name)
        self.value = None


def executable(tmp_path):
    root = tmp_path / "generated Core 中文"
    root.mkdir()
    exe = root / "dsh-pet-core-webm.exe"
    exe.write_bytes(b"MZ generated never executed")
    return exe


def command(exe):
    return f'cmd /c start "" /D "{exe.parent}" "{exe}" --slot 0'


def test_removal_deletes_only_matching_new_product_registration(tmp_path):
    from pet.core_registration_cleanup import remove_owned_autostart

    exe = executable(tmp_path)
    registry = Registry(command(exe))
    result = remove_owned_autostart(exe, registry=registry)
    assert result.status == "completed"
    assert registry.deleted == ["dsh-pet-core-webm"]
    assert set(registry.queried) == {"dsh-pet-core-webm"}
    assert remove_owned_autostart(exe, registry=registry).status == "idempotent"


def test_another_new_core_copy_registration_is_preserved(tmp_path):
    from pet.core_registration_cleanup import remove_owned_autostart

    exe = executable(tmp_path)
    other = tmp_path / "other/dsh-pet-core-webm.exe"
    registry = Registry(command(other))
    result = remove_owned_autostart(exe, registry=registry)
    assert result.status == "idempotent" and result.reason == "other_core_registration_preserved"
    assert registry.value == command(other) and not registry.deleted


@pytest.mark.parametrize("value", ["unrecognized command", 'cmd /c start "" /D "E:/different" "E:/same/dsh-pet-core-webm.exe" --slot 0'])
def test_ambiguous_registration_blocks_completion_without_deleting(tmp_path, value):
    from pet.core_registration_cleanup import remove_owned_autostart

    exe = executable(tmp_path)
    registry = Registry(value)
    result = remove_owned_autostart(exe, registry=registry)
    assert result.status == "recovery_required" and not registry.deleted


def test_registry_permission_failure_is_not_a_completed_uninstall(tmp_path):
    from pet.core_registration_cleanup import remove_owned_autostart

    result = remove_owned_autostart(executable(tmp_path), registry=Registry(denied=True))
    assert result.status == "recovery_required" and result.reason == "autostart_registry_unavailable"


def test_core_maintenance_finalization_requires_package_evidence_before_registry_access(tmp_path):
    from pet.core_maintenance import finish_core_removal

    registry = Registry("must never be inspected")
    assert finish_core_removal(None, executable=executable(tmp_path), registry=registry) == 3
    assert not registry.queried and not registry.deleted


def test_core_maintenance_finalization_propagates_cleanup_failure(tmp_path):
    from pet.core_maintenance import finish_core_removal
    from pet.core_uninstall import CoreRemovalEvidence
    from pet.official_features import OFFICIAL_FEATURES

    evidence = CoreRemovalEvidence("generated-identity", tuple((owner, 1) for owner in sorted(OFFICIAL_FEATURES)))
    exe = executable(tmp_path)
    assert finish_core_removal(evidence, executable=exe, registry=Registry(denied=True)) == 2
    registry = Registry(command(exe))
    assert finish_core_removal(evidence, executable=exe, registry=registry) == 0
    assert registry.deleted == ["dsh-pet-core-webm"]
