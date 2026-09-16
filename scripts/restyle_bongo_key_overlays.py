# -*- coding: utf-8 -*-
"""用一张「高亮样式图」批量把整套按键覆盖图换成你自己的风格。

为什么需要它：BongoCat 的按键覆盖图是**整窗大小的透明 PNG**，每个键一张（左右加起来
50 多张）。让 GPT 一张一张重绘既慢又容易跑位；而每张原图的**透明区域位置**本身就
精确记录了该键的高亮位置，所以正确做法是：

    新风格高亮图（GPT 生成 1 张） + 原覆盖图的 alpha 掩膜 = 位置正确的整套新覆盖图

脚本对每张原图：取其非透明区域的包围盒 → 把样式图按 `--fit` 缩放铺进该包围盒 →
用原图 alpha 做掩膜裁剪 → 按原尺寸输出。位置、尺寸、透明通道都与原版一致。

用法::

    # 先让 GPT 生成一张"按键高亮"样式图（透明底、正方形、居中、四周留白 10%）
    python scripts/restyle_bongo_key_overlays.py `
        --overlay-dir D:\\dsh-pet-mycat\\mycat\\resources\\left-keys `
        --style D:\\dsh-pet-mycat\\style-keycap.png `
        --out D:\\dsh-pet-mycat\\mycat\\resources\\left-keys

    # 也可以只处理几个键（原地覆盖前会用 .orig 备份）
    python scripts/restyle_bongo_key_overlays.py ... --keys Space,Return,KeyA --backup
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow 是运行期依赖
    Image = None


def _bbox_of_alpha(image: "Image.Image"):
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    return bbox


def _ensure_utf8_console() -> None:
    """Windows 控制台可能是 cp1252/GBK：中文输出会 UnicodeEncodeError（issue #26）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def restyle_overlay(overlay: "Image.Image", style: "Image.Image", *, fit: str = "contain"):
    """把样式图铺进原覆盖图的高亮包围盒，并套用原图 alpha。"""
    overlay = overlay.convert("RGBA")
    bbox = _bbox_of_alpha(overlay)
    if bbox is None:
        return overlay
    left, top, right, bottom = bbox
    box_w, box_h = max(1, right - left), max(1, bottom - top)
    tile = style.convert("RGBA")
    if fit == "stretch":
        tile = tile.resize((box_w, box_h), Image.LANCZOS)
    else:
        scale = min(box_w / tile.width, box_h / tile.height)
        new_size = (max(1, int(round(tile.width * scale))), max(1, int(round(tile.height * scale))))
        tile = tile.resize(new_size, Image.LANCZOS)
    canvas = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    offset = (left + (box_w - tile.width) // 2, top + (box_h - tile.height) // 2)
    canvas.paste(tile, offset, tile)
    # 原图 alpha 是唯一的形状权威：位置/尺寸永远与按键对得上
    canvas.putalpha(overlay.getchannel("A"))
    return canvas


def restyle_dir(
    overlay_dir: Path,
    style_path: Path,
    out_dir: Path,
    *,
    keys: list[str] | None = None,
    fit: str = "contain",
    backup: bool = False,
) -> list[str]:
    overlay_dir = Path(overlay_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(style_path) as image:
        style = image.convert("RGBA")
    wanted = {name.lower() for name in keys} if keys else None
    style_resolved = Path(style_path).resolve()
    written: list[str] = []
    for path in sorted(overlay_dir.glob("*.png")):
        if path.resolve() == style_resolved:
            continue  # 样式图自己也在同一目录时不要把它当成覆盖图处理
        if wanted is not None and path.stem.lower() not in wanted:
            continue
        target = out_dir / path.name
        try:
            with Image.open(path) as image:
                stylized = restyle_overlay(image, style, fit=fit)
        except OSError:
            continue
        if backup and target == path:
            # 备份放子目录：同目录多出 *.png.orig 会让素材校验器报
            # key-overlay-not-png（"只允许 PNG"是刻意的门禁）。
            backup_dir = path.parent / "_orig"
            backup_dir.mkdir(exist_ok=True)
            if not (backup_dir / path.name).exists():
                shutil.copy2(path, backup_dir / path.name)
        stylized.save(target)
        written.append(path.name)
    return written


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    if Image is None:
        print("需要 Pillow：pip install Pillow")
        return 1
    parser = argparse.ArgumentParser(description="批量把按键覆盖图换成自己的高亮风格")
    parser.add_argument("--overlay-dir", required=True, help="原覆盖图目录（left-keys / right-keys）")
    parser.add_argument("--style", required=True, help="GPT 生成的高亮样式图（透明底 PNG）")
    parser.add_argument("--out", default="", help="输出目录；默认与 --overlay-dir 相同（原地替换）")
    parser.add_argument("--keys", default="", help="只处理这些键，逗号分隔（如 Space,Return,KeyA）")
    parser.add_argument("--fit", choices=("contain", "stretch"), default="contain",
                        help="样式图铺满高亮包围盒的方式（默认 contain 不变形）")
    parser.add_argument("--backup", action="store_true", help="原地替换前把原图备份成 *.png.orig")
    args = parser.parse_args(argv)

    overlay_dir = Path(args.overlay_dir)
    if not overlay_dir.is_dir():
        print(f"覆盖图目录不存在：{overlay_dir}")
        return 1
    out_dir = Path(args.out) if args.out else overlay_dir
    keys = [item.strip() for item in args.keys.split(",") if item.strip()]
    written = restyle_dir(
        overlay_dir, Path(args.style), out_dir, keys=keys or None, fit=args.fit, backup=args.backup
    )
    if not written:
        print("没有处理任何覆盖图（检查 --overlay-dir / --keys）")
        return 1
    print(f"已生成 {len(written)} 张覆盖图 → {out_dir}")
    for name in written[:10]:
        print(f"  - {name}")
    if len(written) > 10:
        print(f"  … 其余 {len(written) - 10} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
