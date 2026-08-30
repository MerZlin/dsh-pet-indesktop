# -*- coding: utf-8 -*-
"""Thread-safe representative-frame decoding for animation menu thumbnails."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from . import catalog

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional dependency in GIF-only installs
    imageio_ffmpeg = None


REPRESENTATIVE_FRACTION = 0.62
_CACHE_LIMIT = 128
_cache_lock = threading.Lock()
_image_cache: dict[tuple[str, int, int], QImage] = {}
_inflight: dict[tuple[str, int, int], threading.Event] = {}


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


def _decode_representative_frame(path: Path) -> QImage:
    if not path.is_file():
        return QImage()
    if path.suffix.lower() == ".gif":
        return _decode_gif(path)
    return _decode_webm(path)


def decode_representative_frame(path: str | Path) -> QImage:
    """Decode once per file version and share the result across pet windows."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return QImage()
    key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))

    with _cache_lock:
        cached = _image_cache.get(key)
        if cached is not None:
            return QImage(cached)
        event = _inflight.get(key)
        owner = event is None
        if owner:
            event = threading.Event()
            _inflight[key] = event

    if not owner:
        event.wait()
        with _cache_lock:
            return QImage(_image_cache.get(key, QImage()))

    try:
        image = _decode_representative_frame(path)
        if not image.isNull():
            with _cache_lock:
                if len(_image_cache) >= _CACHE_LIMIT:
                    _image_cache.pop(next(iter(_image_cache)))
                _image_cache[key] = QImage(image)
        return image
    finally:
        with _cache_lock:
            event = _inflight.pop(key, None)
            if event is not None:
                event.set()
