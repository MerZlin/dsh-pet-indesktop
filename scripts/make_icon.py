# -*- coding: utf-8 -*-
"""从待机动画提取封面帧，生成应用图标。

用法：
    python scripts/make_icon.py           # 生成 Windows ICO + 预览
    python scripts/make_icon.py --icns    # 额外生成 macOS .icns（需要 macOS 的 iconutil）

产物：
    assets/icon.ico          多尺寸（16/24/32/48/64/128/256）ICO
    assets/icon-preview.png  256px 预览图（便于人工确认）
    assets/icon.icns         macOS 图标（--icns 时生成，CI 构建 macOS .app 使用）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDLE_WEBM = ROOT / "assets" / "characters" / "shenshen" / "videos" / "idle" / "待机呼吸休闲.webm"
OUT_ICO = ROOT / "assets" / "icon.ico"
OUT_PREVIEW = ROOT / "assets" / "icon-preview.png"
OUT_ICONSET = ROOT / "assets" / "icon.iconset"
OUT_ICNS = ROOT / "assets" / "icon.icns"
# Windows 标准档位：16/20/24/32/40/48/64/96/128/256
# 40 是 125% DPI 缩放下桌面/任务栏快捷方式的常用尺寸，缺失会被放大而发虚。
# 注意顺序：PIL 按此顺序写入 ICO 文件，看图软件/资源管理器预览取“第一帧”，
# 大尺寸在前可避免预览显示成 16x16。
ICON_SIZES = (256, 128, 96, 64, 48, 40, 32, 24, 20, 16)
# macOS iconset 规范：<name>_<w>x<h>[(@2x)].png
_ICONSET_ENTRIES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

def extract_frame(path: Path) -> bytes:
    """读取 RGBA 首帧原始字节。"""
    import imageio_ffmpeg

    gen = None
    try:
        gen = imageio_ffmpeg.read_frames(
            str(path),
            pix_fmt="rgba",
            bits_per_pixel=32,
            input_params=["-c:v", "libvpx-vp9"],
        )
        next(gen)  # meta
        return next(gen)  # first frame
    finally:
        if gen is not None:
            try:
                gen.close()
            except Exception:
                pass


def prepare_icon_image(source_img, side: int = 256):
    """裁掉透明留白并让角色尽量填满正方形图标画布。"""
    from PIL import Image

    img = source_img.convert("RGBA")
    # VP9 透明边缘可能散落 alpha=1 的噪点，直接 getbbox 会误判为整张画布。
    mask = img.getchannel("A").point(lambda a: 255 if a > 8 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("帧全透明，无法生成图标")
    img = img.crop(bbox)

    char_w, char_h = img.size
    scale = min(0.977 * side / char_w, 0.977 * side / char_h)
    char = img.resize(
        (max(1, int(round(char_w * scale))), max(1, int(round(char_h * scale)))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(char, ((side - char.width) // 2, (side - char.height) // 2))
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="生成应用图标")
    parser.add_argument("--icns", action="store_true", help="额外生成 macOS .icns（需 iconutil）")
    args = parser.parse_args()

    if not IDLE_WEBM.is_file():
        print(f"未找到待机动画: {IDLE_WEBM}")
        return 1
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        print(f"缺少 Pillow: {exc}")
        return 1

    print(f"提取首帧: {IDLE_WEBM.name}")
    try:
        frame = extract_frame(IDLE_WEBM)
    except ImportError as exc:  # pragma: no cover
        print(f"缺少 imageio-ffmpeg: {exc}")
        return 1
    if len(frame) != 640 * 360 * 4:
        print(f"帧尺寸异常: {len(frame)} bytes")
        return 1

    try:
        img = prepare_icon_image(Image.frombytes("RGBA", (640, 360), frame))
    except ValueError as exc:
        print(str(exc))
        return 1

    # 生成多尺寸 ICO（手工构造以控制帧顺序：Pillow 会强制升序，首帧必为 16x16，
    # 导致看图软件/资源管理器预览显示成小图标；这里让 256 排第一）
    _save_ico_ordered(OUT_ICO, img, ICON_SIZES)
    print(f"已生成: {OUT_ICO}（尺寸 {ICON_SIZES}）")

    preview = img.resize((256, 256), Image.Resampling.LANCZOS)
    preview.save(OUT_PREVIEW)
    print(f"预览图: {OUT_PREVIEW}")

    if args.icns:
        rc = build_icns(img)
        if rc != 0:
            return rc
    return 0


def _save_ico_ordered(path: Path, source_img, sizes) -> None:
    """按指定顺序写出 ICO（首帧=最大尺寸）。

    32bpp BMP-in-ICO：BITMAPINFOHEADER + 自底向上 BGRA 像素 + 全零 AND 掩码
    （32bpp 带 alpha 时 Windows 忽略 AND 掩码）。
    """
    import struct

    from PIL import Image

    blobs: list[bytes] = []
    entries: list[tuple[int, int, int, int, int, int, int]] = []
    for size in sizes:
        frame = source_img.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
        w = h = int(size)
        header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
        pixels = bytearray()
        for y in range(h - 1, -1, -1):
            for x in range(w):
                r, g, b, a = frame.getpixel((x, y))
                pixels += bytes((b, g, r, a))
        row_and = ((w + 31) // 32) * 4
        blob = header + bytes(pixels) + bytes(row_and * h)
        blobs.append(blob)
        entries.append((w if w < 256 else 0, h if h < 256 else 0, 0, 0, 1, 32, len(blob)))

    count = len(entries)
    offset = 6 + 16 * count
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, count))
        for (w, h, cc, res, planes, bpp, size), blob in zip(entries, blobs):
            f.write(struct.pack("<BBBBHHII", w, h, cc, res, planes, bpp, size, offset))
            offset += size
        for blob in blobs:
            f.write(blob)


def build_icns(source_img) -> int:
    """由方形 RGBA 图生成 macOS iconset 并编译为 .icns（依赖 iconutil）。"""
    from PIL import Image

    OUT_ICONSET.mkdir(exist_ok=True)
    for name, size in _ICONSET_ENTRIES:
        resized = source_img.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(OUT_ICONSET / name)
    print(f"已生成 iconset: {OUT_ICONSET}（{len(_ICONSET_ENTRIES)} 个尺寸）")

    if sys.platform != "darwin":
        print("非 macOS 环境，跳过 iconutil（.icns 由 CI 的 macOS 步骤生成）")
        return 0
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(OUT_ICONSET), "-o", str(OUT_ICNS)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # macOS 26 的 iconutil 可能拒绝尺寸/命名均合法的 Pillow PNG iconset。
        # Pillow 自身支持写 ICNS，作为等价回退保证本机构建不中断。
        print(f"iconutil 失败，改用 Pillow ICNS 回退: {result.stderr.strip()}")
        try:
            source_img.save(OUT_ICNS, format="ICNS")
        except Exception as exc:
            print(f"Pillow ICNS 回退失败: {exc}")
            return 1
    print(f"已生成: {OUT_ICNS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
