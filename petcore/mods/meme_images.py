"""彩蛋图片目录 → AI 表情包候选（纯标准库，Qt-free）。

目录口径与 `petcore.ui.easter_egg.EasterEggController.image_dir()` 一致：
`easter_egg_image_dir` 非空就用用户改后的路径，空则用随包
``assets/big_blue_fat_fish``。可选 ``meme-manifest.json`` 给每张图补
caption/tags/keywords；没有 manifest 时文件名 stem 就是 caption。

取图永远只在**目录清单内**匹配：模型给的标记不会被当成文件路径。
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path

from .. import paths

DEFAULT_DIR = "big_blue_fat_fish"
MANIFEST_NAME = "meme-manifest.json"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})
DEFAULT_LIMIT = 60
MAX_CAPTION = 80
MAX_TAG = 24
MAX_KEYWORDS = 8
MAX_MARKER = 80

_MARKER = re.compile(r"[\[【]\s*表情\s*[:：]\s*([^\]】\n]{1,80})[\]】]")


@dataclass(frozen=True)
class MemeImage:
    path: Path
    name: str
    caption: str
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemeCatalog:
    directory: Path
    items: tuple[MemeImage, ...]
    total: int


def directory(raw) -> Path:
    """彩蛋图片目录；空值回退随包内置目录（与彩蛋菜单同口径）。"""
    text = str(raw or "").strip()
    if text:
        return Path(text).expanduser()
    return paths.legacy_asset_root() / DEFAULT_DIR


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _manifest_items(folder: Path) -> dict[str, dict]:
    try:
        data = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("items") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("file") or item.get("name"), 200)
        if not name:
            continue
        result[Path(name).name.casefold()] = item
    return result


def _tags(meta: dict) -> tuple[str, ...]:
    raw = meta.get("tags")
    if isinstance(raw, str):
        raw = re.split(r"[,，/\s]+", raw)
    if not isinstance(raw, list):
        return ()
    values: list[str] = []
    for tag in raw:
        text = _clean(tag, MAX_TAG)
        if text and text not in values:
            values.append(text)
    return tuple(values)


def _keywords(meta: dict) -> tuple[str, ...]:
    raw = meta.get("keywords")
    if isinstance(raw, str):
        raw = re.split(r"[,，/\s]+", raw)
    if not isinstance(raw, list):
        return ()
    values: list[str] = []
    for keyword in raw:
        text = _clean(keyword, MAX_TAG)
        if text and text not in values:
            values.append(text)
        if len(values) >= MAX_KEYWORDS:
            break
    return tuple(values)


def catalog(folder: Path | str, limit: int = DEFAULT_LIMIT) -> MemeCatalog:
    """扫描目录并按上限等距抽样；``total`` 是抽样前的真实张数。"""
    root = Path(folder).expanduser()
    files: list[Path] = []
    try:
        # `os.scandir` 在 Windows 上直接读目录项属性，避免每个文件再 stat 一次
        # （1000 张实测比 `Path.iterdir() + is_file()` 快约 30%）。
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if Path(entry.name).suffix.lower() in IMAGE_SUFFIXES:
                    files.append(Path(entry.path))
    except OSError:
        files = []
    files.sort()
    manifest = _manifest_items(root)
    items: list[MemeImage] = []
    for file in files:
        meta = manifest.get(file.name.casefold(), {})
        caption = _clean(meta.get("caption"), MAX_CAPTION) or file.stem
        items.append(
            MemeImage(
                path=file,
                name=file.name,
                caption=caption,
                tags=_tags(meta),
                keywords=_keywords(meta),
            )
        )
    total = len(items)
    capped = max(1, int(limit))
    if total > capped:
        if capped == 1:
            items = items[:1]
        else:
            indices = sorted(
                {
                    round(index * (total - 1) / (capped - 1))
                    for index in range(capped)
                }
            )
            items = [items[index] for index in indices]
    return MemeCatalog(directory=root, items=tuple(items), total=total)


def _haystack(item: MemeImage) -> str:
    return " ".join(
        (item.name, item.path.stem, item.caption, *item.tags, *item.keywords)
    ).casefold()


def _matches(marker: str, result: MemeCatalog) -> list[MemeImage]:
    text = _clean(marker, MAX_MARKER).strip().strip("[]【】").strip()
    if not text:
        return []
    folded = text.casefold()
    exact = [
        item
        for item in result.items
        if item.name.casefold() == folded or item.path.stem.casefold() == folded
    ]
    if exact:
        return exact
    tagged = [
        item
        for item in result.items
        if any(tag.casefold() == folded for tag in item.tags)
    ]
    if tagged:
        return tagged
    return [item for item in result.items if folded in _haystack(item)]


def resolve(marker: str, result: MemeCatalog) -> MemeImage | None:
    """按文件名/stem/tag/caption 在清单内解析；绝不接受目录外路径。"""
    matches = _matches(marker, result)
    return matches[0] if matches else None


def _fallback(text: str, result: MemeCatalog, picker) -> MemeImage:
    folded = str(text or "").casefold()
    scored: list[tuple[int, MemeImage]] = []
    for item in result.items:
        score = 0
        for token in (item.path.stem, item.caption, *item.tags, *item.keywords):
            value = str(token or "").strip().casefold()
            if len(value) >= 2 and value in folded:
                score += 1
        scored.append((score, item))
    best = max((score for score, _item in scored), default=0)
    if best > 0:
        return picker.choice([item for score, item in scored if score == best])
    return picker.choice(list(result.items))


def apply_meme(
    text: str,
    result: MemeCatalog,
    rng: random.Random | None = None,
) -> tuple[str, MemeImage | None]:
    """消费 ``[表情: …]`` 标记；标记缺失/非法时本地兜底挑一张。

    返回 ``(去掉标记的正文, 选中的图)``。目录为空时原样返回正文、不配图。
    """
    picker = rng if rng is not None else random
    source = str(text or "")
    marker = _MARKER.search(source)
    cleaned = _MARKER.sub("", source) if marker else source
    cleaned = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", cleaned).strip()
    if not result.items:
        return cleaned, None
    if marker is not None:
        matches = _matches(marker.group(1), result)
        if matches:
            return cleaned, picker.choice(matches)
    return cleaned, _fallback(cleaned, result, picker)


def prompt_listing(result: MemeCatalog) -> str:
    """给模型的紧凑清单（最多 ``len(items)`` 行，目录大时只列抽样）。"""
    lines = [
        f"可用表情包（共 {result.total} 张，下面列出 {len(result.items)} 张）："
    ]
    for item in result.items:
        extras: list[str] = []
        if item.caption and item.caption != item.path.stem:
            extras.append(item.caption)
        if item.tags:
            extras.append("/".join(item.tags))
        suffix = f"（{'；'.join(extras)}）" if extras else ""
        lines.append(f"- {item.name}{suffix}")
    return "\n".join(lines)
