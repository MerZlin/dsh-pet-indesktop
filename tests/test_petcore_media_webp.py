# -*- coding: utf-8 -*-
"""WebP 单文件动画与 WebP 精灵表：解码、切帧、角色清单解析。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from petcore.content.legacy import build_animations, load_character
from petcore.contracts.content import AnimationSpec, SheetSpec
from petcore.engine.enums import AnimatType, GraphType
from petcore.media.sheet_clip import SheetClip
from petcore.media.webp_clip import WebPClip
from petcore.media.library import ClipLibrary


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _animated_webp(path: Path, frames: int = 4, size=(24, 24)) -> Path:
    images = []
    for index in range(frames):
        image = Image.new("RGBA", size, (index * 40, 60, 120, 255))
        images.append(image)
    images[0].save(
        path,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=40,
        loop=0,
    )
    return path


def _sheet_webp(path: Path, *, frame=16, columns=2, rows=2) -> Path:
    image = Image.new("RGBA", (frame * columns, frame * rows), (0, 0, 0, 0))
    colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]
    for index, color in enumerate(colors):
        column = index % columns
        row = index // columns
        cell = Image.new("RGBA", (frame, frame), color)
        image.paste(cell, (column * frame, row * frame))
    image.save(path, format="WEBP", lossless=True)
    return path


def test_webp_clip_reads_animated_frames(app, tmp_path: Path) -> None:
    path = _animated_webp(tmp_path / "anim.webp")
    clip = WebPClip(path)
    try:
        assert clip.frameCount() == 4
        assert clip.duration() > 0
        # Qt 的 WebP handler 不支持随机跳帧；键盘驱动会退回整段循环。
        assert clip.jumpToFrame(2) is False
        clip.jumpToFrame(0)
        image = clip.currentImage()
        assert not image.isNull()
        assert image.width() == 24
    finally:
        clip.close()
        clip.deleteLater()


def test_sheet_clip_divisor_skips_frames_without_stretching_duration(app, tmp_path: Path) -> None:
    path = _sheet_webp(tmp_path / "sheet-divisor.webp")
    spec = SheetSpec(
        path=path,
        frame_width=16,
        frame_height=16,
        columns=2,
        rows=2,
        frame_count=4,
        fps=12.0,
    )
    clip = SheetClip(spec)
    try:
        clip.set_frame_divisor(2)
        assert clip.duration() == pytest.approx(4 / 12.0), "隔帧不能把时长拉长"
        assert clip._interval_ms() == 167
        clip.start()
        assert clip.currentFrameNumber() == 0
        clip._tick()
        assert clip.currentFrameNumber() == 2
        clip._tick()
        assert clip.currentFrameNumber() == 3
        clip._tick()
        assert clip.currentFrameNumber() == 3
    finally:
        clip.close()
        clip.deleteLater()


def test_sheet_clip_slices_the_grid(app, tmp_path: Path) -> None:
    path = _sheet_webp(tmp_path / "sheet.webp")
    spec = SheetSpec(
        path=path,
        frame_width=16,
        frame_height=16,
        columns=2,
        rows=2,
        frame_count=4,
        fps=12.0,
    )
    clip = SheetClip(spec)
    try:
        assert clip.frameCount() == 4
        assert clip.duration() == pytest.approx(4 / 12.0)
        assert clip.estimated_bytes() > 0
        assert clip.jumpToFrame(3) is True
        assert clip.currentFrameNumber() == 3
        image = clip.currentImage()
        assert image.width() == 16 and image.height() == 16
        assert image.pixelColor(4, 4).red() > 200
        assert image.pixelColor(4, 4).green() > 200
    finally:
        clip.close()
        clip.deleteLater()


def test_library_returns_a_clip_for_a_sheet(app, tmp_path: Path) -> None:
    path = _sheet_webp(tmp_path / "sheet.webp")
    spec = SheetSpec(path, 16, 16, 2, 2, 4, 12.0)
    animation = AnimationSpec(
        name="walk",
        graph=GraphType.MOVE,
        animat=AnimatType.B_LOOP,
        sheet=spec,
    )
    library = ClipLibrary()
    try:
        clip = library.clip_for(animation)
        assert clip is not None
        assert clip.frame_count == 4
    finally:
        library.close_all()


def test_legacy_reads_a_sidecar_sheet(tmp_path: Path) -> None:
    character = tmp_path / "characters" / "sheet-pet"
    videos = character / "videos"
    move = videos / "move"
    move.mkdir(parents=True)
    _sheet_webp(move / "walk.webp")
    (move / "walk.json").write_text(
        json.dumps(
            {
                "FrameWidth": 16,
                "FrameHeight": 16,
                "Columns": 2,
                "Rows": 2,
                "FrameCount": 4,
                "SampleFps": 12,
            }
        ),
        encoding="utf-8",
    )
    (videos / "manifest.json").write_text(
        json.dumps({"name": "Sheet Pet", "body_box": [0, 0, 64, 64]}),
        encoding="utf-8",
    )
    spec = load_character("sheet-pet", root=tmp_path / "characters")
    assert spec is not None
    assert spec.media_kind == "sheet"
    walk = next(item for item in spec.animations if item.name == "walk")
    assert walk.sheet is not None
    assert walk.sheet.fps == 12
    assert walk.graph is GraphType.MOVE


def test_legacy_manifest_clips_can_declare_a_sheet(tmp_path: Path) -> None:
    character = tmp_path / "characters" / "manifest-pet"
    videos = character / "videos"
    videos.mkdir(parents=True)
    _sheet_webp(videos / "walk.webp")
    (videos / "manifest.json").write_text(
        json.dumps(
            {
                "name": "Manifest Pet",
                "clips": {
                    "run": {
                        "sheet": "walk.webp",
                        "frame_w": 16,
                        "frame_h": 16,
                        "columns": 2,
                        "rows": 2,
                        "frame_count": 4,
                        "fps": 24,
                        "folder": "move",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    animations = build_animations(videos, character / "frameseq")
    assert len(animations) == 1
    assert animations[0].name == "run"
    assert animations[0].sheet is not None
    assert animations[0].graph is GraphType.MOVE
