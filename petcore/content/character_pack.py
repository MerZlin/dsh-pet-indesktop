"""把外部角色素材目录打成一个可安装的 content mod。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..mods.manifest import is_valid_id
from .legacy import _canvas_from_manifest, _read_manifest, _valid_box

MEDIA_SUFFIXES = (".webm", ".gif", ".webp")


def _media_root(source: Path) -> Path:
    videos = source / "videos"
    return videos if videos.is_dir() else source


def _media_files(media_root: Path) -> list[Path]:
    return sorted(
        path
        for path in media_root.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    )


def validate_character_source(source: Path) -> list[str]:
    source = Path(source)
    if not source.is_dir():
        return [f"source is not a directory: {source}"]
    if (source / "mod.json").is_file():
        return ["source already contains mod.json; use petcore mods validate instead"]
    media_root = _media_root(source)
    if not _media_files(media_root):
        return [f"no webm/gif/webp media found under {media_root}"]
    manifest = _read_manifest(source)
    canvas = _canvas_from_manifest(manifest)
    for key in ("body_box", "head_box"):
        value = manifest.get(key)
        if value is not None and _valid_box(value, canvas) is None:
            return [f"invalid {key}: {value!r} (canvas {canvas[0]}x{canvas[1]})"]
    return []


def _link_or_copy(source: Path, target: Path, *, link: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if link:
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def build_character_pack(
    source: Path,
    out: Path,
    *,
    character_id: str | None = None,
    name: str | None = None,
    version: str = "1.0.0",
    link: bool = True,
) -> tuple[bool, str]:
    source = Path(source)
    problems = validate_character_source(source)
    if problems:
        return False, "; ".join(problems)
    if character_id is None:
        out_name = Path(out).name
        character_id = (
            out_name[len("character.") :]
            if out_name.startswith("character.")
            else source.name
        )
    character_id = str(character_id).strip()
    if not is_valid_id(f"character.{character_id}"):
        return False, f"invalid character id: {character_id!r}"

    out = Path(out)
    if out.exists() and any(out.iterdir()):
        return False, f"target is not empty: {out}"
    out.mkdir(parents=True, exist_ok=True)

    media_root = _media_root(source)
    manifest = _read_manifest(source)
    display_name = str(name or manifest.get("name") or character_id)
    canvas = _canvas_from_manifest(manifest)
    body_box = _valid_box(manifest.get("body_box"), canvas)
    head_box = _valid_box(manifest.get("head_box"), canvas)
    try:
        feet_y = int(manifest.get("feet_y") or max(0, canvas[1] - 30))
    except (TypeError, ValueError):
        feet_y = max(0, canvas[1] - 30)

    character = {
        "media": "videos",
        "name": display_name,
        "canvas": [canvas[0], canvas[1]],
        "feet_y": feet_y,
    }
    if body_box is not None:
        character["body_box"] = list(body_box)
    if head_box is not None:
        character["head_box"] = list(head_box)
    for filename, key in (
        ("move_strides.json", "move_strides"),
        ("text_clips.json", "text_clips"),
        ("phrases.json", "phrases"),
    ):
        if (media_root / filename).is_file():
            character[key] = f"videos/{filename}"

    mod = {
        "schema": 1,
        "id": f"character.{character_id}",
        "kind": "content",
        "name": display_name,
        "version": str(version),
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
        "author": str(manifest.get("author") or "petcore pack-character"),
        "character": character,
    }
    (out / "mod.json").write_text(
        json.dumps(mod, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for item in sorted(media_root.rglob("*")):
        if item.is_file():
            _link_or_copy(item, out / "videos" / item.relative_to(media_root), link=link)

    frameseq = source / "frameseq"
    if frameseq.is_dir():
        for item in sorted(frameseq.rglob("*")):
            if item.is_file():
                _link_or_copy(item, out / "frameseq" / item.relative_to(frameseq), link=link)
    return True, f"packed character {character_id} -> {out}"
