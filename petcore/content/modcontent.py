from __future__ import annotations

import json
from pathlib import Path

from ..contracts.content import CharacterSpec
from .legacy import (
    build_animations,
    _clean_curve,
    _clean_segments,
    _read_move_curves,
    _read_move_segments,
    _read_move_strides,
    _read_text_clips,
    _valid_box,
)


def _declared_path(pack: Path, value) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = (pack / value).resolve()
    try:
        candidate.relative_to(pack.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def build_character(
    character: dict,
    pack: Path,
    *,
    owner: str,
    character_id: str,
    display_name: str,
    frameseq_mode: str = "auto",
) -> CharacterSpec | None:
    pack = Path(pack)
    media_root = _declared_path(pack, character.get("media") or "videos")
    if media_root is None or not media_root.is_dir():
        return None
    frameseq_root = pack / "frameseq"
    animations = build_animations(media_root, frameseq_root, frameseq_mode)
    if not animations:
        return None

    strides_path = _declared_path(pack, character.get("move_strides"))
    strides = _read_move_strides(media_root)
    curves = _read_move_curves(media_root)
    segments = _read_move_segments(media_root)
    if strides_path is not None and strides_path.suffix.lower() == ".json":
        try:
            raw = json.loads(strides_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    strides[str(key)] = value
                elif isinstance(value, dict):
                    stride = value.get("stride")
                    if isinstance(stride, int) and not isinstance(stride, bool):
                        strides[str(key)] = int(stride)
                if isinstance(value, dict):
                    curve = _clean_curve(value.get("curve"))
                    if curve is not None:
                        curves[str(key)] = curve
                    cleaned = _clean_segments(value.get("segments"))
                    if cleaned is not None:
                        segments[str(key)] = cleaned

    text_clips = _read_text_clips(media_root)
    text_path = _declared_path(pack, character.get("text_clips"))
    if text_path is not None:
        try:
            raw = json.loads(text_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("no_mirror"), list):
                text_clips = frozenset(str(item) for item in raw["no_mirror"])
        except (OSError, ValueError):
            pass

    return CharacterSpec(
        id=character_id,
        name=display_name,
        root=pack,
        media_kind="mod",
        animations=animations,
        canvas=tuple(character.get("canvas") or (640, 360))[:2],
        feet_y=int(character.get("feet_y") or 330),
        body_box=_valid_box(character.get("body_box")),
        head_box=_valid_box(character.get("head_box")),
        move_strides=strides,
        move_curves=curves,
        move_segments=segments,
        text_clips=text_clips,
        phrases_path=_declared_path(pack, character.get("phrases")),
        owner=owner,
    )
