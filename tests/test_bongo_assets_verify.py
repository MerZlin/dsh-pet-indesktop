# -*- coding: utf-8 -*-
"""键鼠跟随素材校验器（scripts/verify_bongo_assets.py）单元测试。

用合成模型目录覆盖：合格素材通过、缺 model3.json、引用缺件、贴图尺寸与目录名
不符、按键覆盖图尺寸不一致、strict 把缺件警告升级为失败，以及运行时目录扫描。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_bongo_assets.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_bongo_assets", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses 的注解解析要求模块已登记在 sys.modules 里
    # （dataclasses._is_type 会按 cls.__module__ 反查模块字典）。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _png(path: Path, size=(32, 32), *, mode="RGBA") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0)).save(path)


def _write_model3(model_dir: Path, *, texture="demomodel.1024/texture_00.png") -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Version": 3,
        "FileReferences": {
            "Moc": "demomodel.moc3",
            "Textures": [texture],
            "Expressions": [{"Name": "e0", "File": "live2d_expression0.exp3.json"}],
            "Motions": {
                "CAT_motion": [
                    {"File": "live2d_motion1.motion3.json", "Sound": "live2d_motion1.flac"}
                ]
            },
        },
    }
    (model_dir / "cat.model3.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (model_dir / "demomodel.moc3").write_bytes(b"moc3")
    (model_dir / "live2d_expression0.exp3.json").write_text("{}", encoding="utf-8")
    (model_dir / "live2d_motion1.motion3.json").write_text("{}", encoding="utf-8")
    (model_dir / "live2d_motion1.flac").write_bytes(b"flac")


def _write_keys(model_dir: Path, *, size=(32, 32), keys=("KeyA", "Space")) -> None:
    for key in keys:
        _png(model_dir / "resources" / "left-keys" / f"{key}.png", size)
    for key in ("UpArrow",):
        _png(model_dir / "resources" / "right-keys" / f"{key}.png", size)


def test_healthy_model_dir_has_no_errors(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 1024))
    _write_keys(model_dir)
    _png(model_dir / "resources" / "background.png", (256, 256))
    _png(model_dir / "resources" / "cover.png", (256, 256))

    report, dirs = verifier.verify(tmp_path)

    assert [item.name for item in dirs] == ["standard"]
    assert report.errors == []
    assert verifier.main(["--dir", str(model_dir)]) == 0


def test_missing_model3_json_is_an_error(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    model_dir.mkdir()

    report, _ = verifier.verify(model_dir)

    assert [item.code for item in report.errors] == ["missing-model3-json"]
    assert verifier.main(["--dir", str(model_dir)]) == 1


def test_missing_reference_and_size_mismatch_are_errors(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir, texture="demomodel.1024/missing.png")
    (model_dir / "demomodel.moc3").unlink()
    _png(model_dir / "demomodel.1024" / "texture_00.png", (512, 512))

    report, _ = verifier.verify(tmp_path)
    codes = {item.code for item in report.errors}

    assert "missing-reference" in codes
    assert verifier.main(["--dir", str(model_dir)]) == 1


def test_texture_size_must_match_directory_convention(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (512, 512))

    report, _ = verifier.verify(tmp_path)

    assert "texture-size-mismatch" in {item.code for item in report.errors}


def test_inconsistent_key_overlay_sizes_are_errors(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 1024))
    _write_keys(model_dir, keys=("KeyA",))
    _png(model_dir / "resources" / "left-keys" / "Space.png", (64, 16))

    report, _ = verifier.verify(tmp_path)

    assert "key-overlay-size-inconsistent" in {item.code for item in report.errors}


def test_missing_key_overlays_warn_and_strict_fails(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 1024))
    _write_keys(model_dir, keys=("KeyA",))

    report, _ = verifier.verify(tmp_path)

    assert "missing-key-overlay" in {item.code for item in report.warnings}
    assert verifier.main(["--dir", str(model_dir)]) == 0
    assert verifier.main(["--dir", str(model_dir), "--strict"]) == 1


def test_runtime_scan_covers_all_bundled_models(tmp_path):
    verifier = _load_verifier()
    models_root = tmp_path / "assets" / "models"
    for name in ("standard", "keyboard"):
        model_dir = models_root / name
        _write_model3(model_dir)
        _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 1024))

    report, dirs = verifier.verify(models_root)

    assert sorted(item.name for item in dirs) == ["keyboard", "standard"]
    assert report.errors == []


def test_json_report_shape(tmp_path, capsys):
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    model_dir.mkdir()

    assert verifier.main(["--dir", str(model_dir), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["errors"] >= 1
    assert "standard" in payload["models"]


# ---------------------------------------------- 上游预置素材的真实约定（实测）


def test_upstream_texture_is_1024x512_for_1024_directory(tmp_path):
    """上游三套预置模型的贴图实际是 1024×512：目录名后缀 = 宽度，不是正方形边长。"""
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 512))

    report, _ = verifier.verify(tmp_path)

    assert "texture-size-mismatch" not in {item.code for item in report.findings()}
    assert report.errors == []


def test_standard_model_without_right_keys_is_only_a_warning(tmp_path):
    """standard 预置模型本来就没有 resources/right-keys：缺目录只提示，不是错误。"""
    verifier = _load_verifier()
    model_dir = tmp_path / "standard"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 512))
    _write_keys(model_dir, keys=("KeyA", "Space"))
    (model_dir / "resources" / "right-keys" / "UpArrow.png").unlink()
    (model_dir / "resources" / "right-keys").rmdir()

    report, _ = verifier.verify(tmp_path)

    assert report.errors == []
    assert "missing-key-group" in {item.code for item in report.warnings}
    assert verifier.main(["--dir", str(model_dir)]) == 0


def test_gamepad_model_is_not_checked_against_keyboard_key_names(tmp_path):
    verifier = _load_verifier()
    model_dir = tmp_path / "gamepad"
    _write_model3(model_dir)
    _png(model_dir / "demomodel.1024" / "texture_00.png", (1024, 512))
    _png(model_dir / "resources" / "left-keys" / "DPadUp.png", (32, 32))
    _png(model_dir / "resources" / "left-keys" / "LeftTrigger.png", (32, 32))
    _png(model_dir / "resources" / "right-keys" / "South.png", (32, 32))

    report, _ = verifier.verify(tmp_path)
    messages = " ".join(item.message for item in report.warnings)

    assert report.errors == []
    assert "KeyA" not in messages  # 不拿键盘键名去套手柄模型
