"""Opt-in offline AI frame export; never updates the player's source cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

AI_ENCODER = "libwebp lossless (AI 2x; realesr-animevideov3)"


@dataclass(frozen=True)
class ClipSpec:
    directory: Path
    frames: tuple[Path, ...]
    fps: float
    size: tuple[int, int]
    source_encoder: str
    input_digest: str


def _digest(frames) -> str:
    digest = hashlib.sha256()
    for path in frames:
        digest.update(path.name.encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def validate_paths(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("source and output must not overlap")


def read_clip(directory: Path) -> ClipSpec:
    directory = directory.resolve()
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8-sig"))
    if not isinstance(meta, dict):
        raise ValueError("meta.json must contain an object")
    count = meta.get("frames")
    if type(count) is not int or count <= 0:
        raise ValueError("frames must be a positive integer")
    try:
        fps = float(meta["fps"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("fps must be finite and positive") from error
    if isinstance(meta["fps"], bool) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    if meta.get("ai_upscale") or meta.get("ai_enhancement") or meta.get("encoder") == AI_ENCODER:
        raise ValueError("source is already AI enhanced")
    actual = set(directory.glob("f_*.webp"))
    if len(actual) != count:
        raise ValueError("frame sequence must be complete and contiguous")
    frames = tuple(directory / f"f_{index:04d}.webp" for index in range(1, count + 1))
    if actual != set(frames):
        raise ValueError("frame sequence must be complete and contiguous")
    if any(frame.is_symlink() or not frame.is_file() for frame in frames):
        raise ValueError("frame sequence must contain regular files")
    with Image.open(frames[0]) as image:
        size = image.size
    return ClipSpec(directory, frames, fps, size, str(meta.get("encoder") or ""), _digest(frames))


def _identity(spec, model_sha):
    return {"schema": 1, "scale": 2, "model_sha256": model_sha,
            "input_frames_sha256": spec.input_digest, "input_size": list(spec.size),
            "source_encoder": spec.source_encoder, "fps": spec.fps, "frames": len(spec.frames)}


def export_clip(spec: ClipSpec, output: Path, backend, model_sha: str, *, batch_size=4) -> dict:
    import numpy as np

    if not 1 <= batch_size <= 16:
        raise ValueError("batch size must be in 1..16")
    validate_paths(spec.directory, output)
    output = output.resolve()
    identity = _identity(spec, model_sha)
    width, height = spec.size
    target_size = (width * 2, height * 2)
    if output.exists():
        try:
            saved = json.loads((output / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("existing output is not a completed AI export") from error
        if saved.get("ai_upscale") != identity:
            raise ValueError("existing output has a different source/model identity")
        if (saved.get("fps") != spec.fps or saved.get("frames") != len(spec.frames)
                or saved.get("canvas") != list(target_size) or saved.get("encoder") != AI_ENCODER):
            raise ValueError("existing output metadata changed")
        expected = [output / frame.name for frame in spec.frames]
        if set(output.glob("f_*.webp")) != set(expected) or _digest(expected) != saved.get("output_frames_sha256"):
            raise ValueError("existing output changed or has an invalid digest")
        return {"frames": len(spec.frames), "skipped": True}

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ai2x-", dir=output.parent))
    started = time.perf_counter()
    for offset in range(0, len(spec.frames), batch_size):
        images = []
        for path in spec.frames[offset:offset + batch_size]:
            with Image.open(path) as frame:
                if frame.size != spec.size:
                    raise ValueError(f"inconsistent canvas: {path.name}")
                images.append(frame.convert("RGBA"))
        tensor = np.stack([np.asarray(image.convert("RGB")).transpose(2, 0, 1) for image in images]
                          + [np.asarray(images[-1].convert("RGB")).transpose(2, 0, 1)] * (batch_size - len(images)))
        enhanced = backend.infer(tensor)
        if enhanced.dtype != np.uint8 or enhanced.shape != (batch_size, 3, height * 2, width * 2):
            raise ValueError("model must return uint8 NCHW RGB at exactly 2x resolution")
        for index, original in enumerate(images):
            rgb = np.ascontiguousarray(enhanced[index].transpose(1, 2, 0))
            if original.getchannel("A").getextrema()[1] > 128 and tensor[index].max() > 40 and rgb.max() <= 5:
                raise ValueError("backend returned an unexpected black frame")
            result = Image.fromarray(rgb)
            result.putalpha(original.getchannel("A").resize(target_size, Image.Resampling.BICUBIC))
            result.save(stage / spec.frames[offset + index].name, lossless=True, exact=True, method=2)

    meta = {"fps": spec.fps, "frames": len(spec.frames), "canvas": list(target_size),
            "encoder": AI_ENCODER, "source_encoder": spec.source_encoder,
            "ai_upscale": identity,
            "output_frames_sha256": _digest([stage / frame.name for frame in spec.frames])}
    (stage / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # Publish a complete clip only. Failed staging directories remain for diagnosis.
    if output.exists():
        raise ValueError("output was created by another process; refusing to overwrite")
    os.rename(stage, output)
    return {"frames": len(spec.frames), "skipped": False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "bytes": sum((output / frame.name).stat().st_size for frame in spec.frames)}


class OnnxBackend:
    def __init__(self, model: Path, size, batch_size, *, provider="dml", device=0):
        import onnxruntime as ort

        if device < 0:
            raise ValueError("device must be nonnegative")
        selected = "DmlExecutionProvider" if provider == "dml" else "CPUExecutionProvider"
        if selected not in ort.get_available_providers():
            raise ValueError(f"requested provider is unavailable: {selected}")
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 4
        if provider == "dml":
            options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        for name, value in (("batch", batch_size), ("width", size[0]), ("height", size[1])):
            options.add_free_dimension_override_by_name(name, value)
        providers = [(selected, {"device_id": device})] if provider == "dml" else [selected]
        self.session = ort.InferenceSession(str(model), options, providers=providers)
        if self.session.get_providers()[0] != selected:
            raise ValueError("requested provider was not activated")
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1 or inputs[0].type != "tensor(uint8)" or outputs[0].type != "tensor(uint8)":
            raise ValueError("use the uint8 2x model produced by export_ai_upscale_model.py")
        self.input_name = inputs[0].name

    def infer(self, frames):
        return self.session.run(None, {self.input_name: frames})[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="one clip or a frame-sequence root")
    parser.add_argument("--output", type=Path, required=True, help="separate review-only output tree")
    parser.add_argument("--model", type=Path, help="uint8 2x ONNX model (not needed for --dry-run)")
    parser.add_argument("--provider", choices=("dml", "cpu"), default="dml")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_paths(args.source, args.output)
        if not 1 <= args.batch <= 16 or args.device < 0:
            raise ValueError("batch must be 1..16 and device must be nonnegative")
        root = args.source.resolve()
        directories = [root] if (root / "meta.json").is_file() else [path.parent for path in sorted(root.rglob("meta.json"))]
        if not directories:
            raise ValueError("source contains no frame-sequence clips")
        if any(not directory.resolve().is_relative_to(root) for directory in directories):
            raise ValueError("source contains linked clips outside its root")
        specs = [read_clip(directory) for directory in directories]
        if any(spec.size != specs[0].size for spec in specs):
            raise ValueError("all clips must use the same canvas")
        if args.dry_run:
            print(json.dumps({"clips": len(specs), "frames": sum(len(s.frames) for s in specs),
                              "source_size": specs[0].size, "output_size": [v * 2 for v in specs[0].size]}))
            return 0
        if args.model is None or not args.model.is_file():
            raise ValueError("--model must name an exported ONNX model")
        model_sha = hashlib.sha256(args.model.read_bytes()).hexdigest()
        backend = OnnxBackend(args.model, specs[0].size, args.batch, provider=args.provider, device=args.device)
        for spec in specs:
            relative = spec.directory.relative_to(root)
            target = args.output if relative == Path(".") else args.output / relative
            report = export_clip(spec, target, backend, model_sha, batch_size=args.batch)
            print(json.dumps({"clip": str(relative), **report}, ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        print(f"AI export failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
