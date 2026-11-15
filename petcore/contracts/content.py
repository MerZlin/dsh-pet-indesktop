from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .enums import AnimatType, GraphType, ModeType


@dataclass(frozen=True, slots=True)
class SheetSpec:
    """一张精灵表：按网格切帧，不生成逐帧文件。"""

    path: Path
    frame_width: int
    frame_height: int
    columns: int
    rows: int
    frame_count: int
    fps: float = 24.0


@dataclass(frozen=True, slots=True)
class AnimationSpec:
    name: str
    graph: GraphType
    animat: AnimatType
    mode: ModeType = ModeType.NOMAL
    path: Path | None = None
    frameseq_dir: Path | None = None
    sheet: SheetSpec | None = None
    locate: str = "ground"
    loop: bool = False
    event_group: str = ""


@dataclass(frozen=True, slots=True)
class CharacterSpec:
    id: str
    name: str
    root: Path
    media_kind: str
    animations: tuple[AnimationSpec, ...]
    canvas: tuple[int, int] = (640, 360)
    feet_y: int = 330
    body_box: tuple[int, int, int, int] | None = None
    head_box: tuple[int, int, int, int] | None = None
    move_strides: dict[str, int] | None = None
    move_curves: dict[str, tuple[float, ...]] | None = None
    #: 每个移动 clip 可选的显式分段（start/loop_start/loop_end/end）。
    #: 缺省时 `engine.keyboard_drive.move_segments` 仍按曲线自动推导。
    move_segments: dict[str, dict] | None = None
    text_clips: frozenset[str] = frozenset()
    phrases_path: Path | None = None
    owner: str = "core"


class Clip(Protocol):
    name: str
    fps: float
    frame_count: int
    duration_seconds: float

    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def jump_to_frame(self, index: int) -> None: ...
    def current_frame_number(self) -> int: ...
    def current_image(self) -> Any: ...
    def on_frame(self, callback) -> None: ...
    def on_finished(self, callback) -> None: ...
    def on_error(self, callback) -> None: ...


class ContentProvider(Protocol):
    def characters(self) -> Sequence[CharacterSpec]: ...
