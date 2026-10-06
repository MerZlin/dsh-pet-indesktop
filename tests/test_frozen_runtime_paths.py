"""Frozen native lookup must handle long local paths without adding source roots."""

import sys

import pytest


def test_long_frozen_dependency_path_is_appended_once(monkeypatch):
    from pet.frozen_runtime_paths import activate_frozen_dependency_path

    base = "E:\\generated\\" + "a" * 225
    runtime = base + "\\_internal"
    paths = [runtime, "frozen-pyz"]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", runtime, raising=False)
    monkeypatch.setattr(sys, "executable", base + "\\helper.exe")
    monkeypatch.setattr(sys, "path", paths)
    assert activate_frozen_dependency_path()
    assert paths == [runtime, "frozen-pyz", "\\\\?\\" + runtime]
    assert not activate_frozen_dependency_path()


@pytest.mark.parametrize("runtime", ["E:\\foreign\\_internal", "\\\\server\\share\\_internal", "E:relative", "E:\\owned\\..\\_internal"])
def test_invalid_frozen_layout_fails_closed(monkeypatch, runtime):
    from pet.frozen_runtime_paths import activate_frozen_dependency_path

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", runtime, raising=False)
    monkeypatch.setattr(sys, "executable", "E:\\owned\\helper.exe")
    monkeypatch.setattr(sys, "path", ["original"])
    with pytest.raises(ValueError, match="frozen_runtime_layout"):
        activate_frozen_dependency_path()
    assert sys.path == ["original"]


def test_unfrozen_runtime_never_adds_native_or_source_search_paths(monkeypatch):
    from pet.frozen_runtime_paths import activate_frozen_dependency_path

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "path", ["original"])
    assert not activate_frozen_dependency_path()
    assert sys.path == ["original"]


@pytest.mark.parametrize("extended_root,extended_exe", [(True, True), (True, False), (False, True)])
def test_extended_local_spelling_keeps_same_owned_dependency_root(monkeypatch, extended_root, extended_exe):
    from pet.frozen_runtime_paths import activate_frozen_dependency_path

    base = "E:\\generated\\" + "a" * 240
    runtime = base + "\\_internal"
    extended = "\\\\?\\" + runtime
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", extended if extended_root else runtime, raising=False)
    monkeypatch.setattr(sys, "executable", ("\\\\?\\" if extended_exe else "") + base + "\\helper.exe")
    monkeypatch.setattr(sys, "path", ["frozen-pyz"])
    assert activate_frozen_dependency_path()
    assert sys.path == ["frozen-pyz", extended]
    assert not activate_frozen_dependency_path()
