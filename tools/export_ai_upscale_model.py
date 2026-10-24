"""Export an optional Real-ESRGAN anime-video 2x uint8 model for DirectML.

SRVGGNetCompact is adapted from xinntao/Real-ESRGAN v0.2.5.0 (BSD-3-Clause).
Copyright (c) 2021, Xintao Wang. See tools/licenses/Real-ESRGAN-BSD-3-Clause.txt.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path

WEIGHTS_SHA256 = "b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d"


def export_model(weights: Path, output: Path) -> None:
    if hashlib.sha256(weights.read_bytes()).hexdigest() != WEIGHTS_SHA256:
        raise ValueError("weights do not match the pinned official animevideov3 checkpoint")
    if output.exists() or output.is_symlink() or output.resolve() == weights.resolve():
        raise ValueError("output already exists; choose a new model path")
    import torch
    from torch import nn
    from torch.nn import functional as F

    class SRVGGNetCompact(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.ModuleList([nn.Conv2d(3, 64, 3, 1, 1), nn.PReLU(64)])
            for _ in range(16):
                self.body.extend([nn.Conv2d(64, 64, 3, 1, 1), nn.PReLU(64)])
            self.body.append(nn.Conv2d(64, 48, 3, 1, 1))
            self.upsampler = nn.PixelShuffle(4)

        def forward(self, x):
            out = x
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + F.interpolate(x, scale_factor=4, mode="nearest")

    class PackedTwoX(nn.Module):
        def __init__(self, network):
            super().__init__()
            self.network = network
            # Half-size cubic (a=-0.75), expressed using GPU-supported operators.
            k = torch.tensor([-3, 19, 19, -3], dtype=torch.float16) / 32
            self.register_buffer("kernel", torch.outer(k, k)[None, None].repeat(3, 1, 1, 1))

        def forward(self, pixels):
            out = self.network(pixels.to(torch.float16) / 255)
            out = F.conv2d(F.pad(out, (1, 1, 1, 1), mode="replicate"), self.kernel, stride=2, groups=3)
            return (out * 255 + 0.5).clamp(0, 255).to(torch.uint8)

    torch.set_num_threads(4)
    network = SRVGGNetCompact().eval()
    checkpoint = torch.load(weights, map_location="cpu", weights_only=True)
    network.load_state_dict(checkpoint.get("params_ema", checkpoint.get("params", checkpoint)), strict=True)
    model = PackedTwoX(network.half()).eval()
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with torch.no_grad():
        torch.onnx.export(model, torch.zeros(1, 3, 32, 32, dtype=torch.uint8), buffer,
                          dynamo=False, opset_version=17, external_data=False,
                          input_names=["input"], output_names=["output"],
                          dynamic_axes={"input": {0: "batch", 2: "height", 3: "width"},
                                        "output": {0: "batch", 2: "out_height", 3: "out_width"}})
    with output.open("xb") as stream:
        stream.write(buffer.getvalue())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    export_model(args.weights, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
