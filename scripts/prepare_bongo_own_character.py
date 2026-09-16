# -*- coding: utf-8 -*-
"""把「键鼠跟随」运行时的模型素材复制成一份可编辑工作区，并生成预览图。

做自己的版本时不要在运行时目录里原地改（升级/重建副本会覆盖）。本脚本：

1. 从运行时（或你已装好的 BongoCat）的 `<模型目录>` 复制出完整一套素材到
   `<输出目录>/<名称>/`，相对路径与命名完全保持原样（替换时才不会踩路径坑）；
2. 打印素材清单（尺寸 / 色彩模式 / 透明占比），并标注哪些是"必须保留结构"的贴图；
3. 生成棋盘底预览图 `<输出目录>/preview/*.png`——直接把预览图作为"结构参考图"
   贴给 GPT，比贴原始透明图更容易让它理解"哪里不许动"。

用法::

    # Windows（已装好的官方 BongoCat）：
    python scripts\\prepare_bongo_own_character.py ^
        --model-dir "%LOCALAPPDATA%\\Programs\\BongoCat\\assets\\models\\standard" ^
        --out D:\\dsh-pet-mycat --name mycat

    # macOS：
    python scripts/prepare_bongo_own_character.py \
        --model-dir "/Applications/BongoCat.app/Contents/Resources/assets/models/standard" \
        --out ~/dsh-pet-mycat --name mycat

    # Linux（deb/rpm 安装；AppImage 先解包再取同名路径）：
    python scripts/prepare_bongo_own_character.py \
        --model-dir /usr/lib/BongoCat/assets/models/standard \
        --out ~/dsh-pet-mycat --name mycat

（旧方案里 `%APPDATA%\\dsh-pet-standalone\\bongocat\\runtime\\...` 的运行时副本已随
fork 方案一并废弃，见 docs/EXTERNAL-MODE-2026-09-16.md。）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow 是运行期依赖，缺失时只跳过预览
    Image = None


MODEL3_SUFFIX = ".model3.json"
TEXTURE_DIR_HINT = "texture_"
PREVIEW_NAME = "preview"
CHECKER_SIZE = 16


def _ensure_utf8_console() -> None:
    """Windows 控制台可能是 cp1252/GBK：中文输出会 UnicodeEncodeError。

    与打包脚本同一套编码隔离纪律（issue #26）：自己把标准输出改成 UTF-8，
    不要求用户先设 PYTHONUTF8。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


@dataclass
class AssetInfo:
    relative: str
    size_bytes: int
    image_size: tuple[int, int] | None
    mode: str | None
    alpha_ratio: float | None
    role: str


def _role_of(relative: str) -> str:
    text = relative.replace("\\", "/")
    if text.endswith(MODEL3_SUFFIX):
        return "模型清单（不要改名/改引用）"
    if text.endswith(".moc3"):
        return "Live2D 模型数据（绑定后才变）"
    if TEXTURE_DIR_HINT in text and text.endswith(".png"):
        return "模型贴图（结构必须逐像素保留，只改画风）"
    if text.endswith((".exp3.json", ".cdi3.json", ".motion3.json", ".flac")):
        return "表情/动作（一般不动）"
    if text.endswith("background.png"):
        return "窗口背景（可自由替换）"
    if text.endswith("cover.png"):
        return "偏好窗口封面（可自由替换）"
    if "/left-keys/" in text or "/right-keys/" in text:
        return "按键覆盖图（整窗透明层，可自由替换）"
    return "其他"


def _image_info(path: Path) -> tuple[tuple[int, int] | None, str | None, float | None]:
    if Image is None:
        return None, None, None
    try:
        with Image.open(path) as image:
            size = (image.size[0], image.size[1])
            mode = image.mode
            alpha_ratio = None
            if "A" in image.mode:
                alpha = image.getchannel("A")
                histogram = alpha.histogram()
                total = sum(histogram)
                if total:
                    alpha_ratio = round(sum(histogram[1:]) / total, 4)
            return size, mode, alpha_ratio
    except Exception:
        return None, None, None


def collect_assets(model_dir: Path) -> list[AssetInfo]:
    model_dir = Path(model_dir)
    infos: list[AssetInfo] = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(model_dir).as_posix()
        size, mode, alpha = _image_info(path)
        infos.append(
            AssetInfo(
                relative=relative,
                size_bytes=path.stat().st_size,
                image_size=size,
                mode=mode,
                alpha_ratio=alpha,
                role=_role_of(relative),
            )
        )
    return infos


def find_model_json(model_dir: Path) -> Path | None:
    candidates = sorted(Path(model_dir).glob(f"*{MODEL3_SUFFIX}"))
    return candidates[0] if candidates else None


def referenced_files(model_dir: Path) -> list[str]:
    model_json = find_model_json(model_dir)
    if model_json is None:
        return []
    data = json.loads(model_json.read_text(encoding="utf-8"))
    references = data.get("FileReferences") or {}
    out: list[str] = []
    if isinstance(references.get("Moc"), str):
        out.append(references["Moc"])
    for key in ("Textures",):
        value = references.get(key)
        if isinstance(value, list):
            out.extend(str(item) for item in value if isinstance(item, str))
    for expression in references.get("Expressions") or ():
        if isinstance(expression, dict) and isinstance(expression.get("File"), str):
            out.append(expression["File"])
    motions = references.get("Motions")
    if isinstance(motions, dict):
        for entries in motions.values():
            for entry in entries or ():
                if not isinstance(entry, dict):
                    continue
                for key in ("File", "Sound"):
                    if isinstance(entry.get(key), str):
                        out.append(entry[key])
    return out


def _checkerboard(size: tuple[int, int], *, light=200, dark=150):
    image = Image.new("RGB", size, (light, light, light))
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if ((x // CHECKER_SIZE) + (y // CHECKER_SIZE)) % 2:
                pixels[x, y] = (dark, dark, dark)
    return image


def write_previews(source_dir: Path, preview_dir: Path, infos: list[AssetInfo]) -> list[str]:
    if Image is None:
        return []
    preview_dir = Path(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for info in infos:
        if not info.relative.lower().endswith(".png"):
            continue
        source = Path(source_dir) / info.relative
        try:
            with Image.open(source) as image:
                rgba = image.convert("RGBA")
                canvas = _checkerboard(rgba.size)
                canvas.paste(rgba, (0, 0), rgba)
                target = preview_dir / (info.relative.replace("/", "__") + ".png")
                canvas.save(target)
        except Exception:
            continue
        written.append(info.relative)
    return written


def prepare(model_dir: Path, out_dir: Path, name: str, *, previews: bool = True) -> dict:
    model_dir = Path(model_dir)
    if find_model_json(model_dir) is None:
        raise FileNotFoundError(f"不是 BongoCat 模型目录（缺 *{MODEL3_SUFFIX}）：{model_dir}")
    workspace = Path(out_dir) / name
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(model_dir, workspace)
    infos = collect_assets(workspace)
    missing = [rel for rel in referenced_files(workspace) if not (workspace / rel).is_file()]
    written = write_previews(workspace, Path(out_dir) / PREVIEW_NAME, infos) if previews else []
    return {
        "workspace": str(workspace),
        "assets": infos,
        "missing": missing,
        "previews": written,
        "preview_dir": str(Path(out_dir) / PREVIEW_NAME),
    }


def format_report(result: dict) -> list[str]:
    lines = [f"工作区：{result['workspace']}", "", "素材清单："]
    for info in result["assets"]:
        size = f"{info.image_size[0]}×{info.image_size[1]}" if info.image_size else "-"
        alpha = f"{info.alpha_ratio * 100:.1f}%" if info.alpha_ratio is not None else "-"
        lines.append(
            f"  {info.relative:<52} {size:>10}  {info.mode or '-':<5} 不透明像素 {alpha:>6}  {info.role}"
        )
    if result["previews"]:
        lines += ["", f"预览图（贴给 GPT 当结构参考）：{result['preview_dir']}"]
    if result["missing"]:
        lines += ["", "警告：model3.json 引用了但工作区里缺失的文件：", *[f"  - {m}" for m in result["missing"]]]
    return lines


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    parser = argparse.ArgumentParser(description="准备键鼠跟随模式的素材工作区")
    parser.add_argument("--model-dir", required=True, help="运行时/已安装 BongoCat 的模型目录")
    parser.add_argument("--out", required=True, help="工作区输出目录")
    parser.add_argument("--name", default="my-character", help="工作区内的模型子目录名")
    parser.add_argument("--no-preview", action="store_true", help="不生成棋盘预览图")
    args = parser.parse_args(argv)
    try:
        result = prepare(
            Path(args.model_dir), Path(args.out), args.name, previews=not args.no_preview
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"准备失败：{exc}")
        return 1
    print("\n".join(format_report(result)))
    print()
    print("下一步：改完贴图后跑 python scripts/verify_bongo_assets.py --dir", result["workspace"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
