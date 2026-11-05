from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from petcore.mods.install import install, remove, remove_at, validate


def write_pack(base: Path, payload: dict, files: dict[str, str] | None = None) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "mod.json").write_text(json.dumps(payload), encoding="utf-8")
    for name, content in (files or {}).items():
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return base


def content_payload(**overrides) -> dict:
    payload = {
        "schema": 1,
        "id": "character.demo",
        "kind": "content",
        "name": "Demo",
        "version": "1.0.0",
        "api_version": "1",
        "character": {"id": "demo", "media_dir": "media"},
    }
    payload.update(overrides)
    return payload


def make_zip(archive: Path, pack: Path) -> Path:
    with zipfile.ZipFile(archive, "w") as handle:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(pack).as_posix())
    return archive


def test_install_directory(tmp_path):
    root = tmp_path / "data"
    pack = write_pack(
        tmp_path / "src" / "demo",
        content_payload(),
        {"media/idle.png": "image-bytes"},
    )

    ok, message = install(pack, root)

    assert ok, message
    target = root / "mods" / "character.demo"
    assert (target / "mod.json").is_file()
    assert (target / "media" / "idle.png").read_text(encoding="utf-8") == "image-bytes"
    metadata = json.loads((target / "install.json").read_text(encoding="utf-8"))
    assert metadata["schema"] == 1
    assert metadata["id"] == "character.demo"
    assert metadata["version"] == "1.0.0"
    assert "installed_at" in metadata
    expected = hashlib.sha256((target / "mod.json").read_bytes()).hexdigest()
    assert metadata["files"]["mod.json"] == expected
    assert metadata["files"]["media/idle.png"] == hashlib.sha256(b"image-bytes").hexdigest()
    assert not list((root / "mods").glob(".staging-*"))


def test_install_zip(tmp_path):
    root = tmp_path / "data"
    pack = write_pack(tmp_path / "src" / "demo", content_payload(), {"media/idle.png": "x"})
    archive = make_zip(tmp_path / "demo.zip", pack)

    ok, message = install(archive, root)

    assert ok, message
    target = root / "mods" / "character.demo"
    assert (target / "mod.json").is_file()
    assert (target / "media" / "idle.png").is_file()
    assert not list((root / "mods").glob(".unpack-*"))


def test_zip_slip_is_rejected(tmp_path):
    root = tmp_path / "data"
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("mod.json", json.dumps(content_payload()))
        handle.writestr("../evil.txt", "escaped")

    ok, message = install(archive, root)

    assert not ok
    assert "unsafe zip entry" in message
    assert not (tmp_path / "evil.txt").exists()
    assert not (root / "mods" / "evil.txt").exists()
    assert not list((root / "mods").glob(".unpack-*"))


def test_force_replace(tmp_path):
    root = tmp_path / "data"
    first = write_pack(
        tmp_path / "first" / "demo",
        content_payload(version="1.0.0"),
        {"old.txt": "old"},
    )
    second = write_pack(
        tmp_path / "second" / "demo",
        content_payload(version="2.0.0"),
        {"new.txt": "new"},
    )

    assert install(first, root)[0]
    ok, message = install(second, root)
    assert not ok
    assert "already installed" in message
    target = root / "mods" / "character.demo"
    assert json.loads((target / "mod.json").read_text(encoding="utf-8"))["version"] == "1.0.0"

    ok, message = install(second, root, force=True)
    assert ok, message
    assert json.loads((target / "mod.json").read_text(encoding="utf-8"))["version"] == "2.0.0"
    assert (target / "new.txt").is_file()
    assert not (target / "old.txt").exists()
    assert not list((root / "mods").glob(".trash-*"))


def test_invalid_pack_is_not_installed(tmp_path):
    root = tmp_path / "data"
    pack = write_pack(tmp_path / "src", content_payload(id="Bad.Id"))

    ok, message = install(pack, root)

    assert not ok
    assert "invalid id" in message
    assert not (root / "mods" / "Bad.Id").exists()


def test_remove_only_under_user_mods(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    pack = write_pack(tmp_path / "src" / "demo", content_payload())
    assert install(pack, root_a)[0]
    write_pack(root_b / "mods" / "character.demo", content_payload())

    ok, message = remove(root_a, "character.demo")
    assert ok, message
    assert not (root_a / "mods" / "character.demo").exists()

    ok, message = remove(root_a, "character.demo")
    assert not ok

    ok, message = remove(root_a, "../character.demo")
    assert not ok
    assert "invalid mod id" in message

    ok, message = remove(root_a, "character.demo")
    assert not ok
    assert (root_b / "mods" / "character.demo" / "mod.json").is_file()


def test_remove_refuses_pack_outside_mods_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside" / "character.demo"
    write_pack(outside, content_payload())
    (root / "mods").mkdir(parents=True)

    ok, message = remove(root, "character.demo")

    assert not ok
    assert (outside / "mod.json").is_file()


def test_validate(tmp_path):
    good = write_pack(tmp_path / "good", content_payload())
    bad = write_pack(tmp_path / "bad", content_payload(version="1.0"))
    archive = make_zip(tmp_path / "good.zip", good)

    assert validate(good) == []
    assert validate(archive) == []
    problems = validate(bad)
    assert any("version must be X.Y.Z" in item for item in problems)


def test_remove_uses_manifest_and_keeps_unknown_files(tmp_path):
    root = tmp_path / "data"
    pack = write_pack(tmp_path / "src", content_payload())
    assert install(pack, root)[0]
    target = root / "mods" / "character.demo"
    (target / "notes.txt").write_text("user data", encoding="utf-8")

    ok, message = remove_at(target, root=root)

    assert ok, message
    assert (target / "notes.txt").is_file()
    assert not (target / "mod.json").exists()
    assert "kept" in message


def test_remove_trusted_pack_without_install_manifest(tmp_path):
    root = tmp_path / "data"
    target = root / "mods" / "character.demo"
    write_pack(target, content_payload())
    (target / ".trusted").write_text("", encoding="utf-8")

    ok, message = remove_at(target, root=root)

    assert ok, message
    assert not target.exists()


def test_remove_clears_disabled_state(tmp_path):
    from petcore.mods import state

    root = tmp_path / "data"
    pack = write_pack(tmp_path / "src", content_payload())
    assert install(pack, root)[0]
    state.set_enabled(root, "character.demo", False)

    assert remove_at(root / "mods" / "character.demo", root=root)[0]

    assert state.is_enabled(root, "character.demo") is True


def test_remove_readonly_trusted_pack(tmp_path):
    import stat

    target = write_pack(tmp_path / "mods" / "character.demo", content_payload(),
                        {"media/idle.png": "image"})
    readonly = target / "media/idle.png"
    readonly.chmod(stat.S_IREAD)
    try:
        ok, message = remove_at(target)
        assert ok, message
        assert not target.exists()
    finally:
        if readonly.exists():
            readonly.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_remove_reports_permission_failure_without_clearing_state(tmp_path, monkeypatch):
    import importlib
    import sys

    module = importlib.import_module("petcore.mods.install")
    target = write_pack(tmp_path / "mods" / "character.demo", content_payload())
    failed = target / "mod.json"

    def denied(_path):
        raise PermissionError("still in use")

    def rmtree(path, *, onerror=None, onexc=None):
        try:
            denied(failed)
        except PermissionError as exc:
            if onerror:
                onerror(denied, str(failed), sys.exc_info())
            else:
                onexc(denied, str(failed), exc)

    monkeypatch.setattr(module.shutil, "rmtree", rmtree)
    ok, message = remove_at(target)
    assert not ok
    assert "still in use" in message
    assert failed.exists()


def test_install_failure_keeps_original_when_staging_cleanup_also_fails(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("petcore.mods.install")
    root = tmp_path / "data"
    old = write_pack(tmp_path / "old", content_payload(), {"old.txt": "keep"})
    new = write_pack(tmp_path / "new", content_payload(version="2.0.0"))
    assert install(old, root)[0]
    original_copy = module.shutil.copy2

    def deny_copy(source, destination, *args, **kwargs):
        if Path(source).name == "mod.json":
            raise PermissionError("copy denied")
        return original_copy(source, destination, *args, **kwargs)

    def deny_cleanup(path, *args, **kwargs):
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(module.shutil, "copy2", deny_copy)
    monkeypatch.setattr(module.shutil, "rmtree", deny_cleanup)
    ok, message = install(new, root, force=True)
    assert not ok
    assert "copy denied" in message and "cleanup denied" in message
    assert (root / "mods/character.demo/old.txt").read_text() == "keep"


def test_successful_replacement_reports_old_pack_cleanup_warning(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("petcore.mods.install")
    root = tmp_path / "data"
    old = write_pack(tmp_path / "old", content_payload(), {"old.txt": "keep"})
    new = write_pack(tmp_path / "new", content_payload(version="2.0.0"))
    assert install(old, root)[0]
    original = module.shutil.rmtree

    def deny_trash(path, *args, **kwargs):
        if Path(path).name.startswith(".trash-"):
            raise PermissionError("old pack still in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", deny_trash)
    ok, message = install(new, root, force=True)
    assert ok, message  # The new pack has committed; callers must not retry as failure.
    assert "cleanup failed" in message and "old pack still in use" in message
    assert json.loads((root / "mods/character.demo/mod.json").read_text())["version"] == "2.0.0"
    assert list((root / "mods").glob(".trash-*/old.txt"))


def test_replace_with_retry_recovers_from_a_transient_permission_error(
    tmp_path, monkeypatch
) -> None:
    import importlib

    module = importlib.import_module("petcore.mods.install")
    source = tmp_path / "a.txt"
    target = tmp_path / "b.txt"
    source.write_text("x", encoding="utf-8")
    real = os.replace
    calls: list[str] = []

    def flaky(src, dst):
        calls.append(str(dst))
        if len(calls) < 3:
            raise PermissionError(13, "busy", str(dst))
        return real(src, dst)

    monkeypatch.setattr(module.os, "replace", flaky)
    module._replace_with_retry(source, target, delay=0.0)
    assert target.read_text(encoding="utf-8") == "x"
    assert len(calls) == 3


def test_replace_with_retry_reraises_the_last_permission_error(tmp_path, monkeypatch) -> None:
    import importlib

    module = importlib.import_module("petcore.mods.install")
    source = tmp_path / "a.txt"
    target = tmp_path / "b.txt"
    source.write_text("x", encoding="utf-8")
    calls: list[str] = []

    def deny(src, dst):
        calls.append(str(dst))
        raise PermissionError(13, "busy", str(dst))

    monkeypatch.setattr(module.os, "replace", deny)
    with pytest.raises(PermissionError):
        module._replace_with_retry(source, target, attempts=2, delay=0.0)
    assert len(calls) == 2
