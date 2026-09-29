"""The validation build has a closed source set and never overwrites defaults."""

import json
from pathlib import Path

import pytest

from scripts import build_screen_worker as build


def source_tree(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    for relative in build.WORKER_SOURCES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# deterministic source\n", encoding="utf-8")
    return root


def test_staging_is_an_explicit_source_snapshot_not_the_whole_repository(tmp_path):
    root = source_tree(tmp_path)
    unwanted = root / "pet/chat/providers.py"
    unwanted.parent.mkdir()
    unwanted.write_text("raise AssertionError('must never be staged')", encoding="utf-8")
    output = tmp_path / "worker-build"
    manifest = build.prepare_build(root, output)
    assert set(manifest["sources"]) == set(build.WORKER_SOURCES)
    for relative, digest in manifest["sources"].items():
        assert (output / "source" / relative).read_bytes() == (root / relative).read_bytes()
        assert len(digest) == 64
    assert not (output / "source/pet/chat").exists()
    assert not (output / "source/features/screen_understanding/host").exists()
    assert (output / "source/worker_entry.py").is_file()
    assert json.loads((output / "evidence/build-input.json").read_text(encoding="utf-8"))["sources"] == manifest["sources"]


def test_existing_output_is_never_reused_or_removed(tmp_path):
    root = source_tree(tmp_path)
    output = tmp_path / "already-there"
    output.mkdir()
    sentinel = output / "user-file"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        build.prepare_build(root, output)
    assert sentinel.read_bytes() == b"preserve"


def test_missing_source_fails_before_any_output_write(tmp_path):
    root = source_tree(tmp_path)
    (root / build.WORKER_SOURCES[-1]).unlink()
    output = tmp_path / "not-created"
    with pytest.raises(FileNotFoundError):
        build.prepare_build(root, output)
    assert not output.exists()


def test_compiler_command_points_only_at_staged_sources(tmp_path):
    output = tmp_path / "build"
    command = build.compiler_command(output)
    assert command[:3] == [build.sys.executable, "-m", "PyInstaller"]
    assert "--onedir" in command and "--console" in command
    assert str(output / "source/worker_entry.py") in command
    assert str(build.ROOT) not in command
    for module in ("PySide6", "pet.chat", "keyring", "features.screen_understanding.host"):
        i = command.index(module)
        assert command[i - 1] == "--exclude-module"


def test_build_environment_removes_interpreter_and_bundler_injection():
    raw = {
        "PYTHONPATH": "Core",
        "PYTHONHOME": "Core",
        "_PYI_APPLICATION_HOME_DIR": "Core",
        "_MEIPASS2": "Core",
        "PATH": "system",
        "HTTPS_PROXY": "proxy",
        "SSL_CERT_FILE": "cert",
    }
    environment = build.build_environment(raw)
    assert environment["HTTPS_PROXY"] == "proxy"
    assert environment["SSL_CERT_FILE"] == "cert"
    assert environment["PATH"] == "system"
    assert "PYTHONPATH" not in environment and "PYTHONHOME" not in environment
    assert not any(name.startswith(("_PYI", "_MEIPASS")) for name in environment)
    assert raw["PYTHONPATH"] == "Core"


@pytest.mark.parametrize("bad", ["PySide6.QtCore", "keyring", "pet.chat.models", "pet.app", "features.screen_understanding.host.runtime", "pet.proactive"])
def test_archive_rejects_gui_chat_host_and_legacy_execution(bad):
    with pytest.raises(ValueError, match="forbidden"):
        build.check_module_inventory([*build.REQUIRED_MODULES, bad])


def test_archive_requires_real_worker_and_its_execution_dependencies():
    assert build.check_module_inventory(build.REQUIRED_MODULES) == sorted(build.REQUIRED_MODULES)
    with pytest.raises(ValueError, match="missing"):
        build.check_module_inventory(["json", "queue"])


def test_synthetic_validation_worker_is_explicit_and_records_its_entry(tmp_path):
    root = source_tree(tmp_path)
    fixture = root / "packaging/phase4a_synthetic_worker.py"
    fixture.parent.mkdir()
    fixture.write_text("# fixed synthetic OS boundary, validation only\n", encoding="utf-8")
    ordinary = build.prepare_build(root, tmp_path / "ordinary")
    synthetic = build.prepare_build(root, tmp_path / "synthetic", synthetic=True)
    assert ordinary["synthetic_boundary"] is False
    assert synthetic["synthetic_boundary"] is True
    assert synthetic["entry_sha256"] != ordinary["entry_sha256"]
    assert (tmp_path / "synthetic/source/worker_entry.py").read_bytes() == fixture.read_bytes()
    assert synthetic["sources"]["packaging/phase4a_synthetic_worker.py"] == build._digest(fixture.read_bytes())
