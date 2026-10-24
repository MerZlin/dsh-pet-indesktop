from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools import ai_upscale_frameseq as tool


MODEL_SHA = "a" * 64


class NearestBackend:
    def __init__(self):
        self.calls = 0

    def infer(self, frames):
        self.calls += 1
        return frames.repeat(2, axis=2).repeat(2, axis=3)


def make_clip(root, *, frames=3, mode="RGBA", fps=24.0):
    root.mkdir(parents=True)
    for index in range(1, frames + 1):
        image = Image.new(mode, (8, 6), (70 + index, 120, 200, 255) if mode == "RGBA" else (70 + index, 120, 200))
        if mode == "RGBA":
            image.putpixel((0, 0), (200, 30, 40, 0))
            image.putpixel((1, 1), (120, 90, 80, 80))
        image.save(root / f"f_{index:04d}.webp", lossless=True, exact=True)
    (root / "meta.json").write_text(json.dumps({
        "fps": fps, "frames": frames, "encoder": "original-source-encoder",
        "source_sha256": "b" * 64, "unblend": "white-v6",
    }), encoding="utf-8")
    return root


def test_export_preserves_timing_and_uses_an_honest_ai_identity(tmp_path):
    source = make_clip(tmp_path / "source")
    output = tmp_path / "output"
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    spec = tool.read_clip(source)
    result = tool.export_clip(spec, output, NearestBackend(), MODEL_SHA, batch_size=2)
    meta = json.loads((output / "meta.json").read_text())
    assert result["frames"] == 3 and not result["skipped"]
    assert meta["fps"] == 24.0 and meta["frames"] == 3
    assert meta["encoder"] == tool.AI_ENCODER
    assert meta["encoder"] != "original-source-encoder"
    assert meta["source_encoder"] == "original-source-encoder"
    assert meta["canvas"] == [16, 12]
    assert meta["ai_upscale"]["model_sha256"] == MODEL_SHA
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
    for index, path in enumerate(sorted(output.glob("f_*.webp")), start=1):
        with Image.open(path) as enhanced:
            assert enhanced.size == (16, 12)
            assert enhanced.convert("RGB").getpixel((4, 4)) == (70 + index, 120, 200)
            assert enhanced.convert("RGBA").getchannel("A").getextrema() == (0, 255)


def test_resume_skips_only_verified_complete_clips(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source"))
    output = tmp_path / "output"
    backend = NearestBackend()
    tool.export_clip(spec, output, backend, MODEL_SHA, batch_size=2)
    previous_calls = backend.calls
    assert tool.export_clip(spec, output, backend, MODEL_SHA, batch_size=2)["skipped"]
    assert backend.calls == previous_calls


def test_resume_rejects_modified_output(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source"))
    output = tmp_path / "output"
    tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)
    Image.new("RGBA", (16, 12), (200, 30, 30, 255)).save(output / "f_0001.webp", lossless=True)
    with pytest.raises(ValueError, match="changed|digest"):
        tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)


def test_resume_rejects_corrupt_timing_metadata(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source"))
    output = tmp_path / "output"
    tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)
    meta_path = output / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["fps"] = 12
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="metadata"):
        tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)


def test_resume_rejects_changed_source_or_model(tmp_path):
    source = make_clip(tmp_path / "source")
    output = tmp_path / "output"
    tool.export_clip(tool.read_clip(source), output, NearestBackend(), MODEL_SHA)
    with pytest.raises(ValueError, match="identity"):
        tool.export_clip(tool.read_clip(source), output, NearestBackend(), "c" * 64)
    Image.new("RGBA", (8, 6), (255, 200, 10, 255)).save(source / "f_0001.webp", lossless=True)
    with pytest.raises(ValueError, match="identity"):
        tool.export_clip(tool.read_clip(source), output, NearestBackend(), MODEL_SHA)


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "3"])
def test_bad_frame_count_is_rejected(tmp_path, value):
    source = make_clip(tmp_path / "source")
    meta = json.loads((source / "meta.json").read_text())
    meta["frames"] = value
    (source / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="frames"):
        tool.read_clip(source)


@pytest.mark.parametrize("value", [0, -1, True, "NaN", "Infinity"])
def test_bad_fps_is_rejected(tmp_path, value):
    source = make_clip(tmp_path / "source", fps=value)
    with pytest.raises(ValueError, match="fps"):
        tool.read_clip(source)


def test_missing_or_extra_frame_is_rejected(tmp_path):
    source = make_clip(tmp_path / "source")
    (source / "f_0002.webp").rename(source / "f_9999.webp")
    with pytest.raises(ValueError, match="sequence"):
        tool.read_clip(source)


def test_huge_declared_count_is_rejected_before_allocating_paths(tmp_path, monkeypatch):
    source = make_clip(tmp_path / "source")
    meta = json.loads((source / "meta.json").read_text())
    meta["frames"] = 10 ** 18
    (source / "meta.json").write_text(json.dumps(meta))
    original_range = range

    def guarded_range(start, stop):
        assert stop < 1000, "untrusted count reached path allocation"
        return original_range(start, stop)

    monkeypatch.setattr(tool, "range", guarded_range, raising=False)
    with pytest.raises(ValueError, match="sequence"):
        tool.read_clip(source)


def test_inconsistent_canvas_is_rejected(tmp_path):
    source = make_clip(tmp_path / "source")
    Image.new("RGBA", (9, 6)).save(source / "f_0002.webp", lossless=True)
    with pytest.raises(ValueError, match="canvas"):
        tool.export_clip(tool.read_clip(source), tmp_path / "output", NearestBackend(), MODEL_SHA)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("relation", ["same", "child", "parent"])
def test_overlapping_paths_are_rejected(tmp_path, relation):
    source = tmp_path / "source"
    source.mkdir()
    target = {"same": source, "child": source / "child", "parent": tmp_path}[relation]
    with pytest.raises(ValueError, match="overlap"):
        tool.validate_paths(source, target)


def test_foreign_existing_output_is_not_overwritten(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source"))
    output = tmp_path / "output"
    output.mkdir()
    keep = output / "important.txt"
    keep.write_text("keep")
    with pytest.raises(ValueError):
        tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)
    assert keep.read_text() == "keep"


@pytest.mark.parametrize("bad_kind", ["shape", "dtype", "black", "failure"])
def test_backend_failure_never_publishes_partial_clip(tmp_path, bad_kind):
    class BadBackend:
        def infer(self, frames):
            if bad_kind == "failure":
                raise RuntimeError("backend failed")
            if bad_kind == "shape":
                return frames
            shape = (len(frames), 3, 12, 16)
            return np.zeros(shape, dtype=np.float32 if bad_kind == "dtype" else np.uint8)

    spec = tool.read_clip(make_clip(tmp_path / "source"))
    output = tmp_path / "output"
    with pytest.raises((ValueError, RuntimeError)):
        tool.export_clip(spec, output, BadBackend(), MODEL_SHA)
    assert not output.exists()


def test_rgb_source_can_remain_opaque(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source", mode="RGB"))
    output = tmp_path / "output"
    tool.export_clip(spec, output, NearestBackend(), MODEL_SHA)
    with Image.open(output / "f_0001.webp") as frame:
        assert frame.convert("RGBA").getchannel("A").getextrema() == (255, 255)


def test_already_enhanced_source_is_rejected(tmp_path):
    source = make_clip(tmp_path / "source")
    meta = json.loads((source / "meta.json").read_text())
    meta["ai_enhancement"] = {"scale": 2}
    (source / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="enhanced"):
        tool.read_clip(source)


def test_dry_run_never_loads_ml_runtime_or_creates_output(tmp_path):
    source = make_clip(tmp_path / "source")
    output = tmp_path / "output"
    command = [sys.executable, str(Path(tool.__file__)), "--source", str(source), "--output", str(output), "--dry-run"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["clips"] == 1 and report["frames"] == 3
    assert not output.exists()


def test_model_export_rejects_untrusted_weights_before_importing_torch(tmp_path):
    from tools.export_ai_upscale_model import export_model

    weights = tmp_path / "untrusted.pth"
    weights.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError, match="checkpoint"):
        export_model(weights, tmp_path / "model.onnx")
    assert not (tmp_path / "model.onnx").exists()


def test_missing_requested_provider_fails_without_cpu_fallback(tmp_path, monkeypatch):
    import types

    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]))
    with pytest.raises(ValueError, match="unavailable"):
        tool.OnnxBackend(tmp_path / "model.onnx", (8, 6), 2, provider="dml")


def test_invalid_batch_does_not_create_output(tmp_path):
    spec = tool.read_clip(make_clip(tmp_path / "source"))
    with pytest.raises(ValueError, match="batch"):
        tool.export_clip(spec, tmp_path / "output", NearestBackend(), MODEL_SHA, batch_size=0)
    assert not (tmp_path / "output").exists()
