# -*- coding: utf-8 -*-
"""Thread-safe representative-frame decoding for animation menu thumbnails."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from . import catalog

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional dependency in GIF-only installs
    imageio_ffmpeg = None


REPRESENTATIVE_FRACTION = 0.62


def representative_frame_index(frame_count: int, fraction: float = REPRESENTATIVE_FRACTION) -> int:
    """Choose a recognisable later-middle frame instead of near-identical intros."""
    count = max(1, int(frame_count or 1))
    return max(0, min(count - 1, int((count - 1) * float(fraction))))


def _decode_gif(path: Path) -> QImage:
    reader = QImageReader(str(path))
    count = max(1, reader.imageCount())
    target = representative_frame_index(count)
    if target and not reader.jumpToImage(target):
        reader = QImageReader(str(path))
        image = QImage()
        for _index in range(target + 1):
            image = reader.read()
            if image.isNull():
                break
        return image
    return reader.read()


def _decode_webm(path: Path) -> QImage:
    if imageio_ffmpeg is None:
        return QImage()
    generator = None
    try:
        generator = imageio_ffmpeg.read_frames(
            str(path),
            pix_fmt="rgba",
            bits_per_pixel=32,
            input_params=["-c:v", "libvpx-vp9"],
        )
        meta = next(generator)
        fps = float(meta.get("fps") or 24.0)
        duration = float(meta.get("duration") or 0.0)
        count = max(1, int(round(fps * duration)))
        target = representative_frame_index(count)
        size = meta.get("size") or meta.get("source_size") or (catalog.CANVAS_W, catalog.CANVAS_H)
        width, height = int(size[0]), int(size[1])
        expected = width * height * 4
        for index, frame in enumerate(generator):
            if index < target:
                continue
            if len(frame) != expected:
                return QImage()
            return QImage(
                frame, width, height, width * 4, QImage.Format.Format_RGBA8888,
            ).copy()
    except Exception:
        return QImage()
    finally:
        if generator is not None:
            try:
                generator.close()
            except Exception:
                pass
    return QImage()


def decode_representative_frame(path: str | Path) -> QImage:
    path = Path(path)
    if not path.is_file():
        return QImage()
    if path.suffix.lower() == ".gif":
        return _decode_gif(path)
    return _decode_webm(path)
