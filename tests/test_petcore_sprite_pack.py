# -*- coding: utf-8 -*-
"""WebP 精灵表角色包：aboutme 目录 -> content mod，以及 curated 下载索引。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.content.legacy import build_animations
from petcore.content.sprite_pack import build_sprite_pack, character_id_for
from petcore.net import aboutme_packs
from petcore.ui.settings.page_pets import PetsPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _sheet(path: Path, *, columns=2, rows=2, frame=16) -> None:
    image = Image.new("RGBA", (frame * columns, frame * rows), (0, 0, 0, 0))
    for index in range(columns * rows):
        column = index % columns
        row = index // columns
        cell = Image.new("RGBA", (frame, frame), (index * 30, 80, 120, 255))
        image.paste(cell, (column * frame, row * frame))
    image.save(path, format="WEBP", lossless=True)


def _aboutme_dir(root: Path) -> Path:
    source = root / "dsh-demo"
    sheets = source / "spritesheet"
    sheets.mkdir(parents=True)
    _sheet(sheets / "待机.webp")
    _sheet(sheets / "走路.webp")
    manifest = {
        "schemaVersion": 1,
        "id": "dsh-demo-pet",
        "name": "演示角色",
        "canvasWidth": 640,
        "canvasHeight": 360,
        "idleAnimationId": "待机",
        "movement": {"leftAnimationId": "走路", "rightAnimationId": "走路"},
        "click": {"animationIds": []},
        "regions": [
            {"id": "body", "x": 0.4, "y": 0.5, "width": 0.2, "height": 0.3},
            {"id": "head", "x": 0.4, "y": 0.1, "width": 0.2, "height": 0.3},
        ],
    }
    (source / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    actions = {
        "schemaVersion": 1,
        "petId": "dsh-demo-pet",
        "animations": [
            {
                "id": "待机",
                "name": "待机",
                "category": "日常动作",
                "sources": {"webp": "spritesheet/待机.webp"},
                "spritesheet": {
                    "frameWidth": 16,
                    "frameHeight": 16,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            },
            {
                "id": "走路",
                "name": "走路",
                "category": "运动动作",
                "sources": {"webp": "spritesheet/走路.webp"},
                "spritesheet": {
                    "frameWidth": 16,
                    "frameHeight": 16,
                    "columns": 2,
                    "rows": 2,
                    "frameCount": 4,
                    "fps": 12,
                },
            },
        ],
    }
    (source / "actions.json").write_text(
        json.dumps(actions, ensure_ascii=False), encoding="utf-8"
    )
    (source / "NOTICE.txt").write_text("素材禁止商用；二创须署名。", encoding="utf-8")
    return source


def test_build_sprite_pack_creates_a_content_mod(tmp_path: Path) -> None:
    source = _aboutme_dir(tmp_path)
    out = tmp_path / "mods" / "character.dsh-demo"
    ok, message = build_sprite_pack(source, out)
    assert ok is True, message
    assert (out / "mod.json").is_file()
    assert (out / "NOTICE.txt").is_file()
    manifest = json.loads((out / "videos" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["canvas"] == [640, 360]
    assert "待机" in manifest["clips"]
    assert manifest["clips"]["待机"]["folder"] == "idle"
    assert manifest["clips"]["走路"]["folder"] == "move"
    assert manifest["body_box"] == [256, 180, 384, 288]

    animations = build_animations(out / "videos", out / "frameseq")
    assert {item.name for item in animations} == {"待机", "走路"}
    assert all(item.sheet is not None for item in animations)


def test_character_id_is_sanitized_before_path_use() -> None:
    assert character_id_for("../../evil") == "_.._evil"
    assert character_id_for("dsh1-pet") == "dsh1"
    assert character_id_for("") == ""


def test_build_sprite_pack_refuses_a_non_empty_target(tmp_path: Path) -> None:
    source = _aboutme_dir(tmp_path)
    out = tmp_path / "occupied"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    ok, message = build_sprite_pack(source, out)
    assert ok is False
    assert "非空" in message


def test_curated_index_has_two_licensed_packs() -> None:
    index = aboutme_packs.load_index()
    assert index.get("schema") == 1
    packs = aboutme_packs.packs()
    assert {pack["id"] for pack in packs} == {"dsh", "dsh1"}
    for pack in packs:
        assert pack["license"] if "license" in pack else True
        assert pack["files"]
        for item in pack["files"]:
            assert item["url"].startswith("https://raw.githubusercontent.com/")
            assert len(item["sha256"]) == 64
            assert item["size"] > 0
    assert aboutme_packs.pack_for("dsh1") is not None
    assert aboutme_packs.pack_for("no-such-pack") is None


def test_download_pack_rejects_an_unknown_id(tmp_path: Path) -> None:
    ok, message, path = aboutme_packs.download_pack("no-such-pack", tmp_path / "out")
    assert ok is False and path is None
    assert "未知角色包" in message


def test_pets_page_installs_a_sprite_pack(app, tmp_path: Path) -> None:
    source = _aboutme_dir(tmp_path)
    host = SimpleNamespace(
        config=Config(tmp_path),
        pets=None,
        root=tmp_path,
        reload_mods=lambda: None,
    )
    page = PetsPage(host)
    try:
        page._install_sprite_pack(source)
        assert (tmp_path / "mods" / "character.dsh-demo" / "mod.json").is_file()
        assert page.spawn_character_button.isEnabled() is True
    finally:
        page.deleteLater()


def test_pets_page_exposes_the_webp_pack_entry(app, tmp_path: Path) -> None:
    host = SimpleNamespace(config=Config(tmp_path), pets=None, root=tmp_path)
    page = PetsPage(host)
    try:
        assert page.import_pack_button.text().startswith("导入 WebP")
        assert page.download_pack_button.text().startswith("下载 aboutme")
        assert page.source_button.text() == "素材来源"
        assert page.spawn_character_button.isEnabled() is False
        assert "WebP" in page.keywords()
    finally:
        page.deleteLater()


def test_download_callback_after_page_dispose_does_not_touch_ui(app, tmp_path: Path) -> None:
    host = SimpleNamespace(config=Config(tmp_path), pets=None, root=tmp_path)
    page = PetsPage(host)
    temp = tmp_path / "download-temp"
    temp.mkdir()
    page.dispose()
    page.deleteLater()
    app.processEvents()
    page._on_download_done(False, "boom", str(temp))
    assert temp.exists() is False


def test_aboutme_download_pack_uses_proxy_and_falls_back_on_bad_digest(
    tmp_path: Path, monkeypatch
) -> None:
    import hashlib

    payload = b"RIFFxxxxWEBP"
    sha = hashlib.sha256(payload).hexdigest()
    raw_url = "https://raw.githubusercontent.com/hunmer/aboutme-pets/main/pets/demo/manifest.json"
    fake_pack = {
        "id": "demo",
        "name": "演示包",
        "pet_dir": "pets/demo",
        "files": [
            {
                "url": raw_url,
                "path": "pets/demo/manifest.json",
                "size": len(payload),
                "sha256": sha,
            }
        ],
    }
    monkeypatch.setattr(aboutme_packs, "pack_for", lambda pid: fake_pack if pid == "demo" else None)

    calls: list[str] = []

    def fake_fetch(url: str, **_kwargs):
        calls.append(url)
        if url.startswith("https://gh-proxy.com/"):
            return 200, {}, b"tamperedxxxx"
        return 200, {}, payload

    monkeypatch.setattr(aboutme_packs, "fetch_url", fake_fetch)
    ok, _msg, out = aboutme_packs.download_pack(
        "demo", tmp_path / "out1", mirror_policy="gh-proxy"
    )
    assert ok is True and out is not None
    assert (out / "manifest.json").read_bytes() == payload
    assert calls == [f"https://gh-proxy.com/{raw_url}", raw_url]

    calls.clear()

    def fake_custom_fetch(url: str, **_kwargs):
        calls.append(url)
        if url.startswith("https://my.mirror/prefix/"):
            return 200, {}, payload
        raise OSError("direct blocked")

    monkeypatch.setattr(aboutme_packs, "fetch_url", fake_custom_fetch)
    ok2, _msg2, out2 = aboutme_packs.download_pack(
        "demo",
        tmp_path / "out2",
        mirror_policy="custom",
        custom_mirror_prefix="https://my.mirror/prefix",
    )
    assert ok2 is True and out2 is not None
    assert (out2 / "manifest.json").read_bytes() == payload
    assert calls == [f"https://my.mirror/prefix/{raw_url}"]
