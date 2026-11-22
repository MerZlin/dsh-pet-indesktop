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


# ---------------------------------------------------------------- GitHub 角色目录

from petcore.net import aboutme_repo  # noqa: E402


def _sprite_docs():
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
                "sources": {"webp": "spritesheet/待机.webp"},
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
    actions = {
        "animations": [
            {
                "id": "a2",
                "name": "走路",
                "sources": {"webp": "spritesheet/走路.webp"},
                "spritesheet": {
                    "frameWidth": 32,
                    "frameHeight": 32,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            },
            {
                "id": "a1",
                "name": "待机",
                "sources": {"webp": "spritesheet/待机.webp"},
                "spritesheet": {
                    "frameWidth": 32,
                    "frameHeight": 32,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            },
        ]
    }
    return manifest, actions


def test_aboutme_tree_url_round_trip() -> None:
    url = aboutme_repo.tree_url("gpt")
    assert url == "https://github.com/hunmer/aboutme-pets/tree/main/pets/gpt"
    assert aboutme_repo.parse_tree_url(url) == (
        "hunmer",
        "aboutme-pets",
        "main",
        "pets/gpt",
    )
    assert url_install.is_tree_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/hunmer/aboutme-pets/tree/main/pets/gpt",
        "https://github.com/hunmer/aboutme-pets/blob/main/pets/gpt",
        "https://github.com/hunmer/aboutme-pets/tree/main/../pets/gpt",
        "https://github.com/hunmer/aboutme-pets/tree/main",
        "file:///c:/pets/gpt",
        "https://github.com/hunmer/aboutme-pets/tree/main/pets/%2e%2e/x",
    ],
)
def test_aboutme_tree_url_rejects_other_shapes(url: str) -> None:
    assert aboutme_repo.parse_tree_url(url) is None
    assert url_install.is_tree_url(url) is False


def test_fetch_tree_downloads_only_declared_spritesheets(tmp_path: Path, monkeypatch) -> None:
    manifest, actions = _sprite_docs()
    fetched: list[str] = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        if url.endswith("/manifest.json"):
            return 200, (), json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        if url.endswith("/actions.json"):
            return 200, (), json.dumps(actions, ensure_ascii=False).encode("utf-8")
        assert url.startswith("https://raw.githubusercontent.com/")
        return 200, (), b"RIFFxxxxWEBP"

    monkeypatch.setattr(url_install, "fetch_url", fake_fetch)
    path, preview = url_install.fetch_tree(
        "https://github.com/hunmer/aboutme-pets/tree/main/pets/testrole",
        root=tmp_path,
    )
    try:
        assert preview.kind == "sprite"
        assert preview.package_id == "character.testrole"
        assert preview.name == "测试角色"
        assert (path / "manifest.json").is_file()
        assert (path / "spritesheet" / "待机.webp").read_bytes() == b"RIFFxxxxWEBP"
        assert (path / "spritesheet" / "走路.webp").is_file()
        # 只取声明过的两个精灵表，不多下整仓
        assert sum(url.endswith(".webp") for url in fetched) == 2
        ok, message = url_install.install_tree(path, root=tmp_path, preview=preview)
        assert ok, message
        assert (tmp_path / "mods" / "character.testrole" / "mod.json").is_file()
        assert (tmp_path / "mods" / "character.testrole" / "videos" / "manifest.json").is_file()
    finally:
        url_install.discard(path)
    assert not path.exists()


def test_fetch_tree_reports_a_missing_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        url_install, "fetch_url", lambda *a, **k: (404, (), b"not found")
    )
    with pytest.raises(url_install.UrlInstallError) as exc:
        url_install.fetch_tree(
            "https://github.com/hunmer/aboutme-pets/tree/main/pets/nope",
            root=tmp_path,
        )
    assert exc.value.reason == "unsupported"
    assert "manifest.json" in str(exc.value)


def test_discard_leaves_unregistered_directories_alone(tmp_path: Path) -> None:
    keep = tmp_path / "user-dir"
    keep.mkdir()
    (keep / "note.txt").write_text("keep", encoding="utf-8")
    url_install.discard(keep)
    assert (keep / "note.txt").is_file()


def test_mods_page_fills_the_aboutme_directory_url(tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.page_mods import ModsPage
    from tests.test_petcore_settings import FakeHost

    app = QApplication.instance() or QApplication([])
    opened: list[str] = []
    monkeypatch.setattr(
        "petcore.ui.settings.page_mods.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    page = ModsPage(FakeHost(tmp_path))
    try:
        index = page.aboutme_combo.findData("kimi")
        assert index >= 0
        page.aboutme_combo.setCurrentIndex(index)
        expected = "https://github.com/hunmer/aboutme-pets/tree/main/pets/kimi"
        assert page.aboutme_url.text() == expected

        page.aboutme_fill_button.click()
        assert page.url_input.text() == expected
        assert "已填入" in page.url_status.text()
        assert page.aboutme_all.isChecked() is False, "默认只装常用动作，整包要显式勾选"

        page.aboutme_open_button.click()
        assert opened == [expected]
    finally:
        page.dispose()
        page.deleteLater()
        app.processEvents()


def test_essential_actions_keep_the_common_clips_first() -> None:
    actions = [
        {"id": f"a{i}", "name": f"动作{i}", "sources": {"webp": f"s/{i}.webp"}}
        for i in range(20)
    ]
    actions[3]["name"] = "待机呼吸"
    manifest = {
        "idleAnimationId": "a3",
        "movement": {"leftAnimationId": "a4", "dragAnimationId": "a5"},
        "click": {"animationIds": ["a6"]},
    }
    picked = aboutme_repo.essential_actions(manifest, actions)
    ids = [item["id"] for item in picked]
    assert len(ids) == aboutme_repo.ESSENTIAL_SHEETS
    assert ids[:4] == ["a3", "a4", "a6", "a5"]
    assert len(set(ids)) == len(ids)


def test_fetch_tree_all_actions_opt_in(tmp_path: Path, monkeypatch) -> None:
    actions = [
        {
            "id": f"a{i}",
            "name": f"动作{i}",
            "sources": {"webp": f"sheet/{i}.webp"},
            "spritesheet": {
                "frameWidth": 32,
                "frameHeight": 32,
                "columns": 2,
                "rows": 2,
                "frameCount": 4,
                "fps": 12,
            },
        }
        for i in range(12)
    ]
    manifest = {"id": "bigrole", "name": "大包", "animations": actions}
    fetched: list[str] = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        if url.endswith("/manifest.json"):
            return 200, (), json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        if url.endswith("/actions.json"):
            return 200, (), json.dumps({"animations": actions}, ensure_ascii=False).encode("utf-8")
        return 200, (), b"RIFFxxxxWEBP"

    monkeypatch.setattr(url_install, "fetch_url", fake_fetch)
    for all_actions, expected in ((False, aboutme_repo.ESSENTIAL_SHEETS), (True, 12)):
        fetched.clear()
        path, _preview = url_install.fetch_tree(
            "https://github.com/hunmer/aboutme-pets/tree/main/pets/bigrole",
            root=tmp_path,
            all_actions=all_actions,
        )
        try:
            assert sum(url.endswith(".webp") for url in fetched) == expected
            assert len(list(path.rglob("*.webp"))) == expected
        finally:
            url_install.discard(path)


def test_fetch_tree_uses_proxy_and_falls_back_on_html_or_bad_webp(
    tmp_path: Path, monkeypatch
) -> None:
    manifest, actions = _sprite_docs()
    fetched: list[str] = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        if url.startswith("https://gh-proxy.com/"):
            if url.endswith("/manifest.json"):
                return 200, (), b"<html>rate limit</html>"
            if url.endswith("/actions.json"):
                return 200, (), json.dumps(actions, ensure_ascii=False).encode("utf-8")
            return 200, (), b"<html>not-webp</html>"
        if url.endswith("/manifest.json"):
            return 200, (), json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        if url.endswith("/actions.json"):
            return 200, (), json.dumps(actions, ensure_ascii=False).encode("utf-8")
        return 200, (), b"RIFFxxxxWEBP"

    monkeypatch.setattr(url_install, "fetch_url", fake_fetch)
    path, preview = url_install.fetch_tree(
        "https://github.com/hunmer/aboutme-pets/tree/main/pets/testrole",
        root=tmp_path,
        mirror_policy="gh-proxy",
    )
    try:
        assert preview.package_id == "character.testrole"
        # manifest.json 与 webp 在代理返回 HTML 后自动回退直连；actions.json 由代理成功返回。
        assert any(u.startswith("https://gh-proxy.com/") and u.endswith("/actions.json") for u in fetched)
        assert any(u.startswith("https://raw.githubusercontent.com/") and u.endswith("/manifest.json") for u in fetched)
        assert any(u.startswith("https://raw.githubusercontent.com/") and u.endswith(".webp") for u in fetched)
        assert (path / "spritesheet" / "待机.webp").read_bytes() == b"RIFFxxxxWEBP"
    finally:
        url_install.discard(path)


def test_fetch_zip_never_rewrites_url(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_fetch(url, **kwargs):
        seen.append(url)
        return 200, (), b"PK\x03\x04dummy"

    monkeypatch.setattr(url_install, "fetch_url", fake_fetch)
    target = "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/v1.0.0/mod.zip"
    path = url_install.fetch_zip(target, root=tmp_path)
    try:
        assert seen == [target]
    finally:
        url_install.discard(path)
