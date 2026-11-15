"""从链接安装：URL 校验、zip 识别、aboutme 角色包与功能扩展授权。"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore.mods import url_install


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as handle:
        for name, data in files.items():
            handle.writestr(name, data)
    return path


def _mod_zip(path: Path, *, kind: str = "content", mod_id: str = "test.url") -> Path:
    payload = {
        "schema": 1,
        "id": mod_id,
        "kind": kind,
        "name": "测试扩展",
        "version": "1.0.0",
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
    }
    files: dict[str, bytes] = {
        "mod.json": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    }
    if kind == "feature":
        payload["entrypoint"] = "host/__init__.py:create"
        files["mod.json"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        files["host/__init__.py"] = b"def create(context):\n    return None\n"
    return _zip(path, files)


def _sprite_zip(path: Path) -> Path:
    manifest = {
        "name": "测试角色",
        "id": "testrole",
        "canvasWidth": 64,
        "canvasHeight": 64,
        "idleAnimationId": "a1",
        "animations": [
            {
                "id": "a1",
                "name": "待机",
                "sources": {"webp": "idle/idle.webp"},
                "spritesheet": {
                    "frameWidth": 32,
                    "frameHeight": 32,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            }
        ],
    }
    return _zip(
        path,
        {
            "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "actions.json": b"{}",
            "idle/idle.webp": b"RIFFxxxxWEBP",
        },
    )


def test_normalize_url_rejects_non_http(tmp_path: Path) -> None:
    with pytest.raises(url_install.UrlInstallError) as exc:
        url_install.normalize_url("file:///c:/pack.zip")
    assert exc.value.reason == "invalid_url"
    assert url_install.normalize_url("https://example.test/pack.zip").startswith("https")


def test_fetch_zip_rejects_non_zip(tmp_path: Path, monkeypatch) -> None:
    import petcore.mods.url_install as module

    monkeypatch.setattr(
        module, "fetch_url", lambda *a, **k: (200, (), bytearray(b"not a zip"))
    )
    with pytest.raises(url_install.UrlInstallError) as exc:
        url_install.fetch_zip("https://example.test/x.zip", root=tmp_path)
    assert exc.value.reason == "not_zip"


def test_preview_and_install_content_mod(tmp_path: Path) -> None:
    archive = _mod_zip(tmp_path / "mod.zip")
    preview = url_install.preview_zip(archive)
    assert preview.kind == "mod"
    assert preview.package_id == "test.url"
    assert preview.mod_kind == "content"

    ok, message = url_install.install_zip(
        archive, root=tmp_path, preview=preview, allow_feature=False
    )
    assert ok, message
    assert (tmp_path / "mods" / "test.url" / "mod.json").is_file()


def test_feature_mod_requires_the_allow_flag(tmp_path: Path) -> None:
    archive = _mod_zip(tmp_path / "feature.zip", kind="feature", mod_id="test.feature")
    preview = url_install.preview_zip(archive)
    assert preview.mod_kind == "feature"
    ok, message = url_install.install_zip(
        archive, root=tmp_path, preview=preview, allow_feature=False
    )
    assert ok is False
    assert "允许功能扩展" in message
    assert not (tmp_path / "mods" / "test.feature").exists()


def test_preview_rejects_zip_slip(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "slip.zip",
        {"../evil.txt": b"x", "mod.json": b"{}"},
    )
    with pytest.raises(url_install.UrlInstallError):
        url_install.preview_zip(archive)


def test_preview_and_install_aboutme_sprite_pack(tmp_path: Path) -> None:
    archive = _sprite_zip(tmp_path / "sprite.zip")
    preview = url_install.preview_zip(archive)
    assert preview.kind == "sprite"
    assert preview.package_id == "character.testrole"
    ok, message = url_install.install_zip(
        archive, root=tmp_path, preview=preview, allow_feature=False
    )
    assert ok, message
    target = tmp_path / "mods" / "character.testrole"
    assert (target / "mod.json").is_file()
    assert (target / "videos" / "manifest.json").is_file()


def test_preview_rejects_unknown_zip(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "plain.zip", {"readme.txt": b"hi"})
    with pytest.raises(url_install.UrlInstallError) as exc:
        url_install.preview_zip(archive)
    assert exc.value.reason == "unsupported"


def test_failed_sprite_install_leaves_no_staging_directory(tmp_path: Path) -> None:
    manifest = {
        "name": "坏角色",
        "id": "brokenrole",
        "canvasWidth": 64,
        "canvasHeight": 64,
        "animations": [
            {
                "id": "a1",
                "name": "待机",
                "sources": {"webp": "missing/nope.webp"},
                "spritesheet": {
                    "frameWidth": 32,
                    "frameHeight": 32,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            }
        ],
    }
    archive = _zip(
        tmp_path / "broken.zip",
        {
            "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "actions.json": b"{}",
        },
    )
    preview = url_install.preview_zip(archive)
    assert preview.kind == "sprite"
    ok, _message = url_install.install_zip(archive, root=tmp_path, preview=preview)
    assert ok is False
    mods = tmp_path / "mods"
    assert not (mods / "character.brokenrole").exists()
    assert not list(mods.glob(".staging-url-*"))
