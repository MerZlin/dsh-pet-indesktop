# -*- coding: utf-8 -*-
"""素材工具链测试：工作区准备（prepare）与按键覆盖图批量换风格（restyle）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prepare_module():
    return _load("prepare_bongo_own_character", "prepare_bongo_own_character.py")


def _restyle_module():
    return _load("restyle_bongo_key_overlays", "restyle_bongo_key_overlays.py")


def _make_model(root: Path) -> Path:
    model = root / "standard"
    (model / "demomodel.1024").mkdir(parents=True)
    (model / "resources" / "left-keys").mkdir(parents=True)
    (model / "cat.model3.json").write_text(
        json.dumps({
            "Version": 3,
            "FileReferences": {
                "Moc": "demomodel.moc3",
                "Textures": ["demomodel.1024/texture_00.png"],
                "Motions": {"CAT_motion": [{"File": "m.motion3.json"}]},
            },
        }),
        encoding="utf-8",
    )
    (model / "demomodel.moc3").write_bytes(b"moc3")
    (model / "m.motion3.json").write_text("{}", encoding="utf-8")
    Image.new("RGBA", (1024, 512), (10, 20, 30, 255)).save(
        model / "demomodel.1024" / "texture_00.png"
    )
    Image.new("RGBA", (64, 32), (0, 0, 0, 0)).save(model / "resources" / "left-keys" / "KeyA.png")
    return model


def test_prepare_copies_workspace_and_reports_inventory(tmp_path):
    prepare = _prepare_module()
    model = _make_model(tmp_path / "src")

    result = prepare.prepare(model, tmp_path / "work", "mycat")

    workspace = Path(result["workspace"])
    assert workspace == tmp_path / "work" / "mycat"
    assert (workspace / "demomodel.moc3").is_file()
    assert (workspace / "cat.model3.json").is_file()
    assert (workspace / "demomodel.1024" / "texture_00.png").is_file()
    rels = {info.relative for info in result["assets"]}
    assert "demomodel.1024/texture_00.png" in rels
    assert "resources/left-keys/KeyA.png" in rels
    texture = next(i for i in result["assets"] if i.relative.endswith("texture_00.png"))
    assert texture.image_size == (1024, 512)
    assert "结构必须逐像素保留" in texture.role
    assert result["missing"] == []


def test_prepare_generates_checkerboard_previews(tmp_path):
    prepare = _prepare_module()
    model = _make_model(tmp_path / "src")

    result = prepare.prepare(model, tmp_path / "work", "mycat")

    preview_dir = Path(result["preview_dir"])
    assert preview_dir.is_dir()
    assert "demomodel.1024/texture_00.png" in result["previews"]
    preview = preview_dir / "demomodel.1024__texture_00.png.png"
    assert preview.is_file()
    with Image.open(preview) as image:
        assert image.size == (1024, 512)  # 与原图同尺寸，方便对照


def test_prepare_reports_missing_references(tmp_path):
    prepare = _prepare_module()
    model = _make_model(tmp_path / "src")
    (model / "demomodel.moc3").unlink()

    result = prepare.prepare(model, tmp_path / "work", "mycat")

    assert "demomodel.moc3" in result["missing"]


def test_prepare_rejects_non_model_dir(tmp_path):
    prepare = _prepare_module()
    empty = tmp_path / "empty"
    empty.mkdir()

    try:
        prepare.prepare(empty, tmp_path / "work", "mycat")
    except FileNotFoundError as exc:
        assert "model3.json" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝非模型目录")


def test_restyle_keeps_size_position_and_alpha(tmp_path):
    restyle = _restyle_module()
    overlay_path = tmp_path / "Space.png"
    overlay = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    # 高亮区域固定在 (20,10)-(30,20)：换风格后位置必须原样
    for x in range(20, 30):
        for y in range(10, 20):
            overlay.putpixel((x, y), (255, 255, 255, 255))
    overlay.save(overlay_path)
    style = Image.new("RGBA", (20, 20), (255, 0, 0, 255))
    style_path = tmp_path / "style.png"
    style.save(style_path)

    written = restyle.restyle_dir(tmp_path, style_path, tmp_path, fit="stretch")

    assert written == ["Space.png"]
    with Image.open(overlay_path) as result:
        assert result.size == (64, 32)
        assert result.getchannel("A").getbbox() == (20, 10, 30, 20)
        assert result.getpixel((25, 15))[:3] == (255, 0, 0)
        assert result.getpixel((0, 0))[3] == 0  # 透明区仍然是透明


def test_restyle_can_limit_keys_and_backup(tmp_path):
    restyle = _restyle_module()
    for name in ("KeyA", "KeyB", "Space"):
        image = Image.new("RGBA", (32, 16), (0, 0, 0, 0))
        for x in range(8, 12):
            for y in range(4, 8):
                image.putpixel((x, y), (255, 255, 255, 255))
        image.save(tmp_path / f"{name}.png")
    style_path = tmp_path / "style.png"
    Image.new("RGBA", (8, 8), (0, 200, 0, 255)).save(style_path)

    written = restyle.restyle_dir(
        tmp_path, style_path, tmp_path, keys=["Space", "KeyA"], fit="stretch", backup=True
    )

    assert sorted(written) == ["KeyA.png", "Space.png"]
    # 备份进 _orig/ 子目录：同目录的 *.png.orig 会让素材校验器报"只允许 PNG"
    assert (tmp_path / "_orig" / "KeyA.png").is_file()
    assert not (tmp_path / "_orig" / "KeyB.png").exists()


def test_restyle_backup_dir_keeps_verifier_green(tmp_path):
    """换完风格后，素材校验器不应因为备份文件而报错。"""
    restyle = _restyle_module()
    verify = _load("verify_bongo_assets", "verify_bongo_assets.py")
    overlay = Image.new("RGBA", (32, 16), (0, 0, 0, 0))
    for x in range(8, 12):
        for y in range(4, 8):
            overlay.putpixel((x, y), (255, 255, 255, 255))
    overlay.save(tmp_path / "KeyA.png")
    style_dir = tmp_path / "styles"  # 样式图放别处，别混进按键目录（校验器只允许 PNG 覆盖图）
    style_dir.mkdir()
    style_path = style_dir / "style.png"
    Image.new("RGBA", (8, 8), (0, 200, 0, 255)).save(style_path)

    restyle.restyle_dir(tmp_path, style_path, tmp_path, fit="stretch", backup=True)
    report = verify.Report()
    verify._check_key_group(
        "standard", tmp_path, "left-keys", None, report
    )

    assert [item.code for item in report.findings("standard")] == []
