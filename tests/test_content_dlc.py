"""Phase 1 资源型 DLC 闭环测试。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from pet import catalog
from pet.content import CharacterRegistry, ContentError, ContentManager
from pet.content.hashing import content_sha256, validate_zip_members
from pet.content import paths as content_paths


ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "content" / "characters" / "shenshen"


def _write_package(root: Path, *, version: str = "1.0.0", core_requires: str = ">=4.2.1,<5.0.0") -> Path:
    package = root / "package"
    (package / "videos").mkdir(parents=True)
    (package / "videos" / "idle.webm").write_bytes(b"test-video")
    (package / "manifest.json").write_text(json.dumps({
        "id": "test.character.demo",
        "name": "Demo",
        "version": version,
        "kind": "content",
        "api_version": "1",
        "core_requires": core_requires,
        "platforms": ["windows", "macos", "linux"],
        "dependencies": [],
        "capabilities": ["character", "animation"],
        "entrypoint": None,
        "content": {"characters": ["demo"]},
        "integrity": {"sha256": None, "signature": None},
    }), encoding="utf-8")
    return package


def test_starter_registry_and_catalog_compatibility():
    registry = CharacterRegistry(bundled_root=ROOT / "content" / "characters", legacy_roots=[])
    package = registry.get("shenshen")
    assert package is not None
    assert package.source == "bundled"
    assert package.video_dir.is_dir()
    assert "shenshen" in catalog.list_available_characters()
    assert catalog.resolve_character_video_dir("shenshen") == package.video_dir


def test_manifest_rejects_executable_and_incompatible_package(tmp_path):
    package = _write_package(tmp_path)
    (package / "bad.py").write_text("print('no')", encoding="utf-8")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    result = manager.validate(package, allow_unsigned=True)
    assert not result.valid
    assert any("executable" in error for error in result.errors)

    incompatible = _write_package(tmp_path / "incompatible", core_requires=">=9.0.0,<10.0.0")
    result = manager.validate(incompatible, allow_unsigned=True)
    assert not result.valid
    assert any("does not satisfy" in error for error in result.errors)


def test_directory_and_zip_hash_match_and_zip_traversal_is_rejected(tmp_path):
    package = _write_package(tmp_path)
    archive = tmp_path / "package.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        for path in package.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(package).as_posix())
    assert content_sha256(package) == content_sha256(archive)

    malicious = tmp_path / "bad.zip"
    with ZipFile(malicious, "w") as zf:
        zf.writestr("../escape.txt", "bad")
    with ZipFile(malicious) as zf:
        with pytest.raises(ValueError, match="unsafe"):
            validate_zip_members(zf)


def test_install_upgrade_rollback_and_uninstall_preserve_active_version(tmp_path):
    source_v1 = _write_package(tmp_path / "v1", version="1.0.0")
    source_v2 = _write_package(tmp_path / "v2", version="2.0.0")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")

    manager.install(source_v1, allow_unsigned=True)
    manager.install(source_v2, allow_unsigned=True)
    assert manager.active_version("test.character.demo") == "2.0.0"
    manager.rollback("test.character.demo")
    assert manager.active_version("test.character.demo") == "1.0.0"

    manager.uninstall("test.character.demo", "2.0.0")
    assert not (tmp_path / "data" / "characters" / "demo" / "versions" / "2.0.0").exists()
    assert manager.active_version("test.character.demo") == "1.0.0"


def test_bad_install_does_not_replace_current_active(tmp_path):
    source = _write_package(tmp_path / "good", version="1.0.0")
    bad = _write_package(tmp_path / "bad", version="2.0.0")
    manifest = json.loads((bad / "manifest.json").read_text(encoding="utf-8"))
    manifest["integrity"]["sha256"] = "0" * 64
    (bad / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    with pytest.raises(ContentError):
        manager.install(bad, allow_unsigned=True)
    assert manager.active_version("test.character.demo") == "1.0.0"


def test_starter_package_passes_formal_hash_validation():
    manager = ContentManager(data_root=ROOT / ".test-content-data", core_version="4.2.1", platform_name="windows")
    result = manager.validate(STARTER)
    assert result.valid
    assert result.manifest is not None
    assert result.content_sha256 == result.manifest.integrity_sha256


def test_zip_install_uses_same_transaction_as_directory_install(tmp_path):
    source = _write_package(tmp_path / "zip-source")
    archive = tmp_path / "demo.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source).as_posix())
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    result = manager.install(archive, allow_unsigned=True)
    assert result.activated
    assert manager.active_version("test.character.demo") == "1.0.0"
    assert manager.discover()[0].source == "installed"


def test_uninstall_active_promotes_previous_and_clears_stale_pointer(tmp_path):
    source_v1 = _write_package(tmp_path / "v1", version="1.0.0")
    source_v2 = _write_package(tmp_path / "v2", version="2.0.0")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    manager.install(source_v1, allow_unsigned=True)
    manager.install(source_v2, allow_unsigned=True)
    manager.uninstall("test.character.demo", "2.0.0")
    assert manager.active_version("test.character.demo") == "1.0.0"
    assert not (tmp_path / "data" / "characters" / "demo" / "previous.json").exists()


def test_platform_data_roots_are_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(content_paths.sys, "platform", "win32")
    assert content_paths.app_data_root("demo") == tmp_path / "appdata" / "demo"

    monkeypatch.setattr(content_paths.sys, "platform", "darwin")
    assert content_paths.app_data_root("demo") == Path.home() / "Library" / "Application Support" / "demo"

    monkeypatch.setattr(content_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert content_paths.app_data_root("demo") == tmp_path / "xdg" / "demo"


def test_webm_builds_include_starter_content_but_gif_builds_do_not():
    powershell = (ROOT / "scripts" / "build_onedir.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
    macos = (ROOT / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
    assert "--add-data', 'content;content'" in powershell
    assert "if (-not $isGif)" in powershell
    for script in (linux, macos):
        assert 'content_data=(--add-data "content:content")' in script
        assert 'if [[ "$variant" == webm-* ]]' in script


def test_empty_video_file_is_rejected(tmp_path):
    package = _write_package(tmp_path)
    (package / "videos" / "idle.webm").write_bytes(b"")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    result = manager.validate(package, allow_unsigned=True)
    assert not result.valid
    assert any("empty file" in error for error in result.errors)


def test_invalid_installed_active_falls_back_to_starter(tmp_path):
    installed = tmp_path / "installed" / "characters" / "shenshen"
    broken = installed / "versions" / "1.0.0"
    shutil.copytree(STARTER, broken)
    first_video = next((broken / "videos").rglob("*.webm"))
    first_video.write_bytes(b"")
    installed.mkdir(exist_ok=True)
    (installed / "active.json").write_text(json.dumps({
        "plugin_id": "official.character.shenshen",
        "version": "1.0.0",
    }), encoding="utf-8")
    registry = CharacterRegistry(
        bundled_root=ROOT / "content" / "characters",
        installed_root=tmp_path / "installed" / "characters",
        legacy_roots=[],
        platform_name="windows",
    )
    package = registry.get("shenshen")
    assert package is not None
    assert package.source == "bundled"
    assert any(item["fallback_source"] == "installed" for item in registry.diagnostics)


def test_registry_rejects_unsigned_bundled_package_by_default(tmp_path):
    package = _write_package(tmp_path)
    bundled = tmp_path / "bundled" / "demo"
    shutil.copytree(package, bundled)
    registry = CharacterRegistry(
        bundled_root=tmp_path / "bundled",
        installed_root=tmp_path / "installed",
        legacy_roots=[],
        platform_name="windows",
    )
    assert registry.get("demo") is None
    assert any("unsigned content package" in item["reason"] for item in registry.diagnostics)

    development_registry = CharacterRegistry(
        bundled_root=tmp_path / "bundled",
        installed_root=tmp_path / "installed",
        legacy_roots=[],
        platform_name="windows",
        allow_unsigned=True,
    )
    assert development_registry.get("demo") is not None
