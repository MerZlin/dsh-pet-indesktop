"""Phase 4B-1.5 资源 DLC 硬门回归测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from pet.content import CharacterRegistry, ContentError, ContentManager
from pet.content.hashing import content_sha256

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "content" / "characters" / "shenshen"


def _write_package(
    root: Path,
    *,
    version: str = "1.0.0",
    character_id: str = "demo",
    video: bytes = b"test-video",
) -> Path:
    package = root / "package"
    (package / "videos").mkdir(parents=True)
    (package / "videos" / "idle.webm").write_bytes(video)
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "id": f"test.character.{character_id}",
                "name": "Demo",
                "version": version,
                "kind": "content",
                "api_version": "1",
                "core_requires": ">=4.2.1,<5.0.0",
                "platforms": ["windows", "macos", "linux"],
                "dependencies": [],
                "capabilities": ["character", "animation"],
                "entrypoint": None,
                "content": {"characters": [character_id]},
                "integrity": {"sha256": None, "signature": None},
            }
        ),
        encoding="utf-8",
    )
    return package


def _registry(data_root: Path, *, bundled_root: Path | None = None) -> CharacterRegistry:
    return CharacterRegistry(
        bundled_root=bundled_root or data_root / "empty-bundled",
        installed_root=data_root / "characters",
        legacy_roots=[],
        core_version="4.2.1",
        platform_name="windows",
        allow_unsigned=True,
    )


def test_installed_version_registry_resolves_direct_package_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")

    manager.install(source, allow_unsigned=True)

    installed_root = data_root / "characters" / "demo" / "versions" / "1.0.0"
    package = _registry(data_root).get("demo")

    assert package is not None
    assert package.root == installed_root
    assert package.video_dir == installed_root / "videos"
    assert (package.video_dir / "idle.webm").read_bytes() == b"test-video"


def test_directory_and_zip_install_both_resolve_to_playable_video(tmp_path: Path) -> None:
    source = _write_package(tmp_path / "source")
    archive = tmp_path / "package.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for path in source.rglob("*"):
            if path.is_file():
                zip_file.write(path, path.relative_to(source).as_posix())

    for name, install_source in (("directory", source), ("zip", archive)):
        data_root = tmp_path / name / "data"
        manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
        manager.install(install_source, allow_unsigned=True)
        package = _registry(data_root).get("demo")

        assert package is not None
        assert package.video_dir.is_dir()
        assert (package.video_dir / "idle.webm").is_file()

        # 这里验证真实 MovieLibrary 输入路径，而不是只检查 active.json。
        from pet.library import MovieLibrary

        library = MovieLibrary(character_id="demo", asset_dir=package.video_dir, prewarm_enabled=False)
        library._load_all()
        assert library.clip_path("idle") == package.video_dir / "idle.webm"
        library.deleteLater()


def test_restart_registry_still_resolves_active_version(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)

    restarted_registry = _registry(data_root)
    package = restarted_registry.get("demo")

    assert package is not None
    assert package.video_dir.joinpath("idle.webm").is_file()


def test_conflicting_install_preserves_existing_version_and_pointers(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    existing = _write_package(tmp_path / "existing", video=b"old-content")
    conflicting = _write_package(tmp_path / "conflicting", video=b"new-content")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(existing, allow_unsigned=True)

    installed_root = data_root / "characters" / "demo" / "versions" / "1.0.0"
    active_path = data_root / "characters" / "demo" / "active.json"
    previous_path = data_root / "characters" / "demo" / "previous.json"
    before_root = {path.relative_to(installed_root): path.read_bytes() for path in installed_root.rglob("*") if path.is_file()}
    before_active = active_path.read_bytes()
    before_previous = previous_path.read_bytes() if previous_path.exists() else None

    with pytest.raises(ContentError, match="different content"):
        manager.install(conflicting, allow_unsigned=True)

    after_root = {path.relative_to(installed_root): path.read_bytes() for path in installed_root.rglob("*") if path.is_file()}
    assert after_root == before_root
    assert active_path.read_bytes() == before_active
    assert (previous_path.read_bytes() if previous_path.exists() else None) == before_previous
    assert manager.active_version("test.character.demo") == "1.0.0"


def test_install_failure_after_activation_restores_previous_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    source_v1 = _write_package(tmp_path / "v1", version="1.0.0", video=b"v1")
    source_v2 = _write_package(tmp_path / "v2", version="2.0.0", video=b"v2")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source_v1, allow_unsigned=True)

    def fail_self_check(character_id: str, *, allow_unsigned: bool) -> None:
        if character_id == "demo" and manager._active_for_character(character_id).get("version") == "2.0.0":
            raise ContentError("injected self-check failure")
        raise AssertionError("unexpected self-check call")

    monkeypatch.setattr(manager, "_self_check_active", fail_self_check)
    with pytest.raises(ContentError, match="injected self-check failure"):
        manager.install(source_v2, allow_unsigned=True)

    assert manager.active_version("test.character.demo") == "1.0.0"
    assert (data_root / "characters" / "demo" / "versions" / "1.0.0" / "videos" / "idle.webm").read_bytes() == b"v1"
    assert not (data_root / "characters" / "demo" / "versions" / "2.0.0").exists()


def test_cache_outside_version_root_does_not_change_content_identity(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    installed_root = data_root / "characters" / "demo" / "versions" / "1.0.0"

    first = manager.validate(installed_root, allow_unsigned=True)
    cache_file = data_root / "cache" / "derived" / "frame.bin"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"derived-cache")
    second = manager.validate(installed_root, allow_unsigned=True)

    assert first.valid and second.valid
    assert first.content_sha256 == second.content_sha256


def test_derived_cache_inside_version_root_is_rejected(tmp_path: Path) -> None:
    source = _write_package(tmp_path / "source")
    (source / "frameseq").mkdir()
    (source / "frameseq" / "idle.json").write_text("derived", encoding="utf-8")

    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    result = manager.validate(source, allow_unsigned=True)

    assert not result.valid
    assert any("derived cache must be outside package root" in error for error in result.errors)


def test_upgrade_and_rollback_keep_registry_playable(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    registry = _registry(data_root)
    manager = ContentManager(
        data_root=data_root,
        core_version="4.2.1",
        platform_name="windows",
        registry=registry,
    )

    manager.install(_write_package(tmp_path / "v1", version="1.0.0", video=b"v1"), allow_unsigned=True)
    package = registry.get("demo")
    assert package is not None
    assert (package.video_dir / "idle.webm").read_bytes() == b"v1"

    manager.install(_write_package(tmp_path / "v2", version="2.0.0", video=b"v2"), allow_unsigned=True)
    package = registry.get("demo")
    assert package is not None
    assert (package.video_dir / "idle.webm").read_bytes() == b"v2"

    manager.rollback("test.character.demo")
    package = registry.get("demo")
    assert package is not None
    assert (package.video_dir / "idle.webm").read_bytes() == b"v1"


def test_starter_root_is_untouched_by_user_install_and_uninstall(tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundled"
    starter_source = _write_package(tmp_path / "starter-source", character_id="starter", video=b"starter")
    shutil.copytree(starter_source, bundled_root / "starter")
    starter_root = bundled_root / "starter"
    starter_digest_before = content_sha256(starter_root)

    data_root = tmp_path / "data"
    registry = _registry(data_root, bundled_root=bundled_root)
    manager = ContentManager(
        data_root=data_root,
        core_version="4.2.1",
        platform_name="windows",
        registry=registry,
    )
    user_source = _write_package(tmp_path / "user-source", character_id="starter", video=b"user")

    manager.install(user_source, allow_unsigned=True)
    package = registry.get("starter")
    assert package is not None
    assert package.source == "installed"
    assert (package.video_dir / "idle.webm").read_bytes() == b"user"

    manager.uninstall("test.character.starter")
    package = registry.get("starter")
    assert package is not None
    assert package.source == "bundled"
    assert (package.video_dir / "idle.webm").read_bytes() == b"starter"
    assert content_sha256(starter_root) == starter_digest_before
    assert not (data_root / "characters" / "starter").exists()


def test_user_uninstall_does_not_remove_starter_fallback(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source", character_id="shenshen", video=b"user-shenshen")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    manager.uninstall("test.character.shenshen")

    package = _registry(data_root, bundled_root=ROOT / "content" / "characters").get("shenshen")
    assert package is not None
    assert package.source == "bundled"
    assert package.video_dir == STARTER / "videos"
    assert package.video_dir.is_dir()


def test_no_user_version_is_recreated_after_uninstall(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    manager.uninstall("test.character.demo")

    assert not (data_root / "characters" / "demo" / "versions").exists()
    assert not (data_root / "characters" / "demo" / "active.json").exists()
    assert _registry(data_root).get("demo") is None


@pytest.fixture(autouse=True)
def _clean_movie_libraries() -> None:
    yield
    try:
        from pet.library import MovieLibrary

        MovieLibrary._shutdown_live_for_tests()
    except Exception:
        pass


def test_helper_does_not_leave_source_tree_in_installed_data(tmp_path: Path) -> None:
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=tmp_path / "data", core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    assert not (tmp_path / "data" / "staging" / "install-").exists()
    # Keep an explicit assertion that the test package is still the source of truth.
    assert source.joinpath("manifest.json").is_file()
    shutil.rmtree(tmp_path / "data" / "staging", ignore_errors=True)


def test_uninstall_interrupted_before_removal_recovers_without_changing_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    target = data_root / "characters" / "demo" / "versions" / "1.0.0"
    active_path = data_root / "characters" / "demo" / "active.json"
    active_before = active_path.read_bytes()
    original_rmtree = shutil.rmtree

    def fail_target_removal(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path) == target:
            raise OSError("injected removal interruption")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("pet.content.manager.shutil.rmtree", fail_target_removal)
    with pytest.raises(OSError, match="injected removal interruption"):
        manager.uninstall("test.character.demo")

    journals = list((data_root / "staging" / "transactions").glob("*.json"))
    assert len(journals) == 1
    assert target.is_dir()
    assert active_path.read_bytes() == active_before

    recovered = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    recovered.recover_pending_operations()
    assert recovered.recovery_diagnostics == []
    assert not list((data_root / "staging" / "transactions").glob("*.json"))
    assert target.is_dir()
    assert active_path.read_bytes() == active_before


def test_uninstall_interrupted_after_removal_replays_pointer_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    source_v1 = _write_package(tmp_path / "v1", version="1.0.0", video=b"v1")
    source_v2 = _write_package(tmp_path / "v2", version="2.0.0", video=b"v2")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source_v1, allow_unsigned=True)
    manager.install(source_v2, allow_unsigned=True)
    target = data_root / "characters" / "demo" / "versions" / "2.0.0"
    original_apply = manager._apply_pointer_state
    calls = 0

    def fail_first_pointer_write(character_id: str, name: str, value: dict[str, object] | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected pointer interruption")
        original_apply(character_id, name, value)

    monkeypatch.setattr(manager, "_apply_pointer_state", fail_first_pointer_write)
    with pytest.raises(OSError, match="injected pointer interruption"):
        manager.uninstall("test.character.demo", "2.0.0")

    assert not target.exists()
    assert list((data_root / "staging" / "transactions").glob("*.json"))

    recovered = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    recovered.recover_pending_operations()
    assert recovered.recovery_diagnostics == []
    assert recovered.active_version("test.character.demo") == "1.0.0"
    assert not (data_root / "characters" / "demo" / "previous.json").exists()
    assert not list((data_root / "staging" / "transactions").glob("*.json"))


def test_uninstall_recovery_stops_on_unprovable_missing_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    source = _write_package(tmp_path / "source")
    manager = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    manager.install(source, allow_unsigned=True)
    target = data_root / "characters" / "demo" / "versions" / "1.0.0"
    original_write = manager._write_transaction

    def fail_version_removed(record: dict[str, object]) -> None:
        if record.get("phase") == "version_removed":
            raise OSError("injected journal interruption")
        original_write(record)

    monkeypatch.setattr(manager, "_write_transaction", fail_version_removed)
    with pytest.raises(OSError, match="injected journal interruption"):
        manager.uninstall("test.character.demo")

    assert not target.exists()
    recovered = ContentManager(data_root=data_root, core_version="4.2.1", platform_name="windows")
    recovered.recover_pending_operations()
    assert recovered.recovery_diagnostics
    with pytest.raises(ContentError, match="pending resource operation requires recovery"):
        recovered.install(_write_package(tmp_path / "replacement"), allow_unsigned=True)
