from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import paths
from ..contracts.content import AnimationSpec, CharacterSpec, SheetSpec
from ..engine.enums import AnimatType, GraphType, ModeType
from ..engine.geometry import HEAD_FALLBACK_RATIO

logger = logging.getLogger("petcore.content.legacy")

MEDIA_SUFFIXES = (".webm", ".gif", ".webp")

_FOLDER_MAP: dict[str, tuple[GraphType, AnimatType, str, bool]] = {
    "idle": (GraphType.IDEL, AnimatType.B_LOOP, "ground", True),
    "idle_turn": (GraphType.IDEL, AnimatType.B_LOOP, "ground", True),
    "turn": (GraphType.MOVE, AnimatType.B_LOOP, "in_place", False),
    "move": (GraphType.MOVE, AnimatType.B_LOOP, "ground", False),
    "click": (GraphType.TOUCH_BODY, AnimatType.SINGLE, "ground", False),
    "drag": (GraphType.RAISED_STATIC, AnimatType.SINGLE, "ground", False),
    "raised_static": (GraphType.RAISED_STATIC, AnimatType.SINGLE, "ground", False),
    "raised_dynamic": (GraphType.RAISED_DYNAMIC, AnimatType.B_LOOP, "ground", False),
    "touch_head": (GraphType.TOUCH_HEAD, AnimatType.SINGLE, "ground", False),
    "say": (GraphType.SAY, AnimatType.SINGLE, "ground", False),
    "sleep": (GraphType.SLEEP, AnimatType.B_LOOP, "ground", True),
    "startup": (GraphType.STARTUP, AnimatType.SINGLE, "ground", False),
    "start_up": (GraphType.STARTUP, AnimatType.SINGLE, "ground", False),
    "work": (GraphType.WORK, AnimatType.B_LOOP, "ground", True),
    # events/<group>/<name>.webm：语义事件动画（如 events/balance/余额-*.webm），
    # 不进随机动作池，只能由 play_event() 显式触发。
    "events": (GraphType.EVENT, AnimatType.SINGLE, "ground", False),
}


def _media_files(videos_dir: Path) -> list[Path]:
    files = [
        path
        for path in videos_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    ]
    return sorted(files, key=lambda item: (item.parent.as_posix(), item.name))


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _canvas_from_manifest(manifest: dict) -> tuple[int, int]:
    raw = manifest.get("canvas")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            width, height = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            width, height = 640, 360
    else:
        try:
            width = int(manifest.get("canvas_width") or 640)
            height = int(manifest.get("canvas_height") or 360)
        except (TypeError, ValueError):
            width, height = 640, 360
    return (width if width > 0 else 640, height if height > 0 else 360)


def _valid_box(value, canvas: tuple[int, int] = (640, 360)) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(item) for item in value)
    except (TypeError, ValueError):
        return None
    width, height = canvas
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        return None
    return x1, y1, x2, y2


def _first_int(raw: dict, *keys: str) -> int:
    for key in keys:
        if key not in raw:
            continue
        try:
            value = int(raw[key])
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _sheet_spec(path: Path, raw: dict) -> SheetSpec | None:
    if not isinstance(raw, dict):
        return None
    frame_w = _first_int(raw, "frame_w", "frame_width", "FrameWidth")
    frame_h = _first_int(raw, "frame_h", "frame_height", "FrameHeight")
    columns = _first_int(raw, "columns", "Columns")
    rows = _first_int(raw, "rows", "Rows")
    if not (frame_w and frame_h and columns and rows):
        return None
    count = _first_int(raw, "frame_count", "frameCount", "FrameCount")
    if not count:
        count = columns * rows
    fps = 0.0
    for key in ("fps", "sample_fps", "SampleFps"):
        if key in raw:
            try:
                fps = float(raw[key])
            except (TypeError, ValueError):
                fps = 0.0
            break
    if fps <= 0.0:
        fps = 24.0
    return SheetSpec(
        path=path,
        frame_width=frame_w,
        frame_height=frame_h,
        columns=columns,
        rows=rows,
        frame_count=min(count, columns * rows),
        fps=fps,
    )


def _read_manifest(character_dir: Path) -> dict:
    for candidate in (character_dir / "videos" / "manifest.json", character_dir / "manifest.json"):
        if candidate.exists():
            return _read_json(candidate)
    return {}


def _move_data(videos_dir: Path) -> tuple[dict[str, int], dict[str, tuple[float, ...]]]:
    raw = _read_json(videos_dir / "move_strides.json")
    strides: dict[str, int] = {}
    curves: dict[str, tuple[float, ...]] = {}
    for name, value in raw.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            stride = value
            curve = None
        elif isinstance(value, dict):
            stride = value.get("stride")
            curve = value.get("curve")
        else:
            continue
        if not isinstance(stride, bool) and isinstance(stride, int) and stride > 0:
            strides[str(name)] = stride
        cleaned = _clean_curve(curve)
        if cleaned is not None:
            curves[str(name)] = cleaned
    return strides, curves


def _clean_curve(value) -> tuple[float, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    cleaned: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if number != number:
            return None
        cleaned.append(max(0.0, min(1.0, number)))
    if cleaned[-1] < 1.0 - 1e-6:
        logger.debug("move curve does not end at 1.0; keeping raw values")
    return tuple(cleaned)


def _read_move_strides(videos_dir: Path) -> dict[str, int]:
    return _move_data(videos_dir)[0]


def _read_move_curves(videos_dir: Path) -> dict[str, tuple[float, ...]]:
    return _move_data(videos_dir)[1]


_SEGMENT_KEYS = ("start", "loop_start", "loop_end", "end")


def _clean_segments(value) -> dict | None:
    """只收 `start/loop_start/loop_end/end` 的非负整数；非法项丢弃。"""
    if not isinstance(value, dict):
        return None
    cleaned: dict[str, int] = {}
    for key in _SEGMENT_KEYS:
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            continue
        cleaned[key] = item
    return cleaned or None


def _read_move_segments(videos_dir: Path) -> dict[str, dict]:
    """`move_strides.json` 里每段的显式分段（`segments` 子对象）。

    素材把「绊倒/趴地」烤在移动 clip 里时，靠它声明真正的循环区间；
    缺省不写，运行时仍按曲线自动推导。
    """
    raw = _read_json(videos_dir / "move_strides.json")
    segments: dict[str, dict] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        cleaned = _clean_segments(value.get("segments"))
        if cleaned is not None:
            segments[str(name)] = cleaned
    return segments


def _read_text_clips(videos_dir: Path) -> frozenset[str]:
    raw = _read_json(videos_dir / "text_clips.json")
    names = raw.get("no_mirror")
    if not isinstance(names, list):
        return frozenset()
    return frozenset(str(name) for name in names if isinstance(name, str))


def _frameseq_for(
    webm: Path, videos_dir: Path, frameseq_root: Path, mode: str
) -> Path | None:
    if mode == "off" or not frameseq_root.is_dir():
        return None
    try:
        from pet import frameseq_provision
    except Exception:
        return None
    try:
        generation = frameseq_provision.current_generation(webm, videos_dir, frameseq_root)
    except Exception:
        logger.debug("frameseq adoption failed for %s", webm, exc_info=True)
        return None
    return Path(generation) if generation is not None else None


def list_characters(root: Path | None = None) -> list[str]:
    base = Path(root) if root is not None else paths.legacy_characters_root()
    if not base.is_dir():
        return []
    names = []
    for child in base.iterdir():
        videos = child / "videos"
        if not child.is_dir() or not videos.is_dir():
            continue
        if any(path.suffix.lower() in MEDIA_SUFFIXES for path in videos.rglob("*")):
            names.append(child.name)
    return sorted(names)


def _sheet_animation(
    videos_dir: Path,
    path: Path,
    raw: dict,
    *,
    folder: str,
    name: str,
    event_group: str = "",
) -> AnimationSpec | None:
    spec = _sheet_spec(path, raw)
    if spec is None:
        return None
    graph, animat, locate, loop = _FOLDER_MAP.get(
        folder, (GraphType.DEFAULT, AnimatType.SINGLE, "ground", False)
    )
    if graph is GraphType.EVENT and not event_group:
        try:
            relative = path.relative_to(videos_dir)
        except ValueError:
            relative = path.parts
        if len(relative) > 2:
            event_group = str(relative[1])
    return AnimationSpec(
        name=name,
        graph=graph,
        animat=animat,
        mode=ModeType.NOMAL,
        path=None,
        sheet=spec,
        locate=locate,
        loop=loop,
        event_group=event_group,
    )


def build_animations(
    videos_dir: Path, frameseq_root: Path, frameseq_mode: str = "auto"
) -> tuple[AnimationSpec, ...]:
    files = _media_files(videos_dir)
    animations: list[AnimationSpec] = []
    used_names: set[str] = set()
    sheet_paths: set[Path] = set()
    manifest = _read_json(videos_dir / "manifest.json")
    clips = manifest.get("clips") if isinstance(manifest.get("clips"), dict) else {}
    for raw_name, entry in clips.items():
        if not isinstance(entry, dict):
            continue
        relative = entry.get("sheet") or entry.get("path")
        if not isinstance(relative, str) or not relative.strip():
            continue
        path = (videos_dir / relative).resolve()
        try:
            path.relative_to(videos_dir.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        folder = str(entry.get("folder") or path.parent.name or "")
        name = str(raw_name or path.stem)
        if name in used_names:
            name = f"{folder}/{name}" if folder else f"{path.parent.name}/{name}"
        animation = _sheet_animation(
            videos_dir,
            path,
            entry,
            folder=folder,
            name=name,
            event_group=str(entry.get("event_group") or ""),
        )
        if animation is None:
            continue
        animations.append(animation)
        used_names.add(name)
        sheet_paths.add(path)
    for path in files:
        if path in sheet_paths:
            continue
        if path.suffix.lower() == ".webp":
            sidecar = _read_json(path.with_suffix(".json"))
            relative = path.relative_to(videos_dir)
            folder = relative.parts[0] if len(relative.parts) > 1 else ""
            name = path.stem
            if name in used_names:
                name = f"{folder}/{path.stem}" if folder else f"{path.parent.name}/{path.stem}"
            animation = _sheet_animation(
                videos_dir, path, sidecar, folder=folder, name=name
            )
            if animation is not None:
                animations.append(animation)
                used_names.add(name)
                sheet_paths.add(path)
                continue
        relative = path.relative_to(videos_dir)
        folder = relative.parts[0] if len(relative.parts) > 1 else ""
        graph, animat, locate, loop = _FOLDER_MAP.get(
            folder, (GraphType.DEFAULT, AnimatType.SINGLE, "ground", False)
        )
        event_group = ""
        if graph is GraphType.EVENT and len(relative.parts) > 2:
            event_group = str(relative.parts[1])
        name = path.stem
        if name in used_names:
            name = f"{folder}/{path.stem}" if folder else f"{path.parent.name}/{path.stem}"
        used_names.add(name)
        frameseq_dir = (
            _frameseq_for(path, videos_dir, frameseq_root, frameseq_mode)
            if path.suffix.lower() == ".webm"
            else None
        )
        animations.append(
            AnimationSpec(
                name=name,
                graph=graph,
                animat=animat,
                mode=ModeType.NOMAL,
                path=path,
                frameseq_dir=frameseq_dir,
                locate=locate,
                loop=loop,
                event_group=event_group,
            )
        )
    return tuple(animations)


def load_character(
    character_id: str,
    *,
    root: Path | None = None,
    frameseq_mode: str = "auto",
) -> CharacterSpec | None:
    base = Path(root) if root is not None else paths.legacy_characters_root()
    character_dir = base / character_id
    videos_dir = character_dir / "videos"
    if not videos_dir.is_dir():
        return None
    files = _media_files(videos_dir)
    if not files:
        return None

    manifest = _read_manifest(character_dir)
    canvas = _canvas_from_manifest(manifest)
    body_box = _valid_box(manifest.get("body_box"), canvas)
    head_box = _valid_box(manifest.get("head_box"), canvas)
    if head_box is None and body_box is not None:
        x1, y1, x2, y2 = body_box
        head_box = (x1, y1, x2, y1 + round((y2 - y1) * HEAD_FALLBACK_RATIO))

    frameseq_root = character_dir / "frameseq"
    animations = build_animations(videos_dir, frameseq_root, frameseq_mode)
    kinds: set[str] = set()
    for animation in animations:
        if animation.sheet is not None:
            kinds.add("sheet")
        elif animation.frameseq_dir is not None:
            kinds.add("frameseq")
        elif animation.path is not None:
            kinds.add(animation.path.suffix.lower().lstrip(".") or "unknown")
    media_kind = "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "none")
    display_name = str(manifest.get("name") or character_id)
    feet_y = manifest.get("feet_y")
    try:
        feet_y = int(feet_y) if feet_y is not None else max(0, canvas[1] - 30)
    except (TypeError, ValueError):
        feet_y = max(0, canvas[1] - 30)
    return CharacterSpec(
        id=character_id,
        name=display_name,
        root=character_dir,
        media_kind=media_kind,
        animations=animations,
        canvas=canvas,
        feet_y=feet_y,
        body_box=body_box,
        head_box=head_box,
        move_strides=_read_move_strides(videos_dir),
        move_curves=_read_move_curves(videos_dir),
        move_segments=_read_move_segments(videos_dir),
        text_clips=_read_text_clips(videos_dir),
        phrases_path=(videos_dir / "phrases.json") if (videos_dir / "phrases.json").exists() else None,
        owner="legacy",
    )


def load_all(
    *, root: Path | None = None, frameseq_mode: str = "auto"
) -> list[CharacterSpec]:
    specs = []
    for character_id in list_characters(root):
        spec = load_character(character_id, root=root, frameseq_mode=frameseq_mode)
        if spec is not None:
            specs.append(spec)
    return specs
