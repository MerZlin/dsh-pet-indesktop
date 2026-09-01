# -*- coding: utf-8 -*-
"""Thread-safe representative-frame decoding for animation menu thumbnails."""
from __future__ import annotations

import itertools
import threading
import hashlib
import os
import tempfile
from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from . import catalog

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional dependency in GIF-only installs
    imageio_ffmpeg = None


REPRESENTATIVE_FRACTION = 0.62
_CACHE_LIMIT = 128
# 缩略图内存缓存字节预算（§4.4：缩略图内存缓存 32-64 MiB，取上界）。
# 与条数上限（_CACHE_LIMIT）双约束：任一超限即逐出最久未用的大块头。
_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DISK_CACHE_LIMIT = 256
_DISK_CACHE_DIR = Path(tempfile.gettempdir()) / "dsh-pet-thumbs"
_DECODE_SEMAPHORE = threading.BoundedSemaphore(2)
_cache_lock = threading.Lock()
_image_cache: dict[tuple[str, int, int], QImage] = {}
# LRU 最近使用序（单调计数器，确定性；命中/插入刷新，逐出取最久未用）。
_image_last_used: dict[tuple[str, int, int], int] = {}
_cache_clock = itertools.count()
# 观测计数（P3）：供 image_cache_stats() 断言与遥测。
_cache_hits = 0
_cache_misses = 0
_cache_evictions = 0
_cache_inserts = 0
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


def _disk_cache_path(key: tuple[str, int, int]) -> Path:
    digest = hashlib.sha1("|".join(map(str, key)).encode("utf-8")).hexdigest()
    return _DISK_CACHE_DIR / f"{digest}.png"


def _read_disk_cache(key: tuple[str, int, int]) -> QImage:
    cache_path = _disk_cache_path(key)
    try:
        image = QImage(str(cache_path))
        if image.isNull():
            return QImage()
        return image
    except Exception:
        return QImage()


def _trim_disk_cache() -> None:
    try:
        entries = [path for path in _DISK_CACHE_DIR.glob("*.png") if path.is_file()]
        if len(entries) <= _DISK_CACHE_LIMIT:
            return
        entries.sort(key=lambda path: path.stat().st_mtime_ns)
        for path in entries[:-_DISK_CACHE_LIMIT]:
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _write_disk_cache(key: tuple[str, int, int], image: QImage) -> None:
    cache_path = _disk_cache_path(key)
    tmp_path = cache_path.with_name(
        f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if image.save(str(tmp_path), "PNG"):
            os.replace(tmp_path, cache_path)
            _trim_disk_cache()
    except Exception:
        pass
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _decode_representative_frame(path: Path) -> QImage:
    if not path.is_file():
        return QImage()
    if path.suffix.lower() == ".gif":
        return _decode_gif(path)
    return _decode_webm(path)


def _image_byte_size(image: QImage) -> int:
    """单张缩略图记账字节：宽 × 高 × 每像素字节（保守上界）。"""
    return max(1, image.width() * image.height() * (image.depth() // 8))


def _image_cache_bytes() -> int:
    return sum(_image_byte_size(img) for img in _image_cache.values())


def _trim_image_cache_locked() -> None:
    """内存缓存硬预算（§4.4 步骤 3）：字节（_CACHE_MAX_BYTES）或条数
    （_CACHE_LIMIT）任一超限，逐出最久未用的大块头（LRU，平局先丢大）。
    只清内存缓存，磁盘缓存（_DISK_CACHE_DIR）保留。
    """
    global _cache_evictions
    while _image_cache and (
            _image_cache_bytes() > _CACHE_MAX_BYTES
            or len(_image_cache) > _CACHE_LIMIT):
        victim = min(
            _image_cache,
            key=lambda k: (_image_last_used.get(k, 0), -_image_byte_size(_image_cache[k])),
        )
        _image_cache.pop(victim)
        _image_last_used.pop(victim, None)
        _cache_evictions += 1


def _cache_insert_locked(key, image) -> None:
    """持锁插入内存缓存并刷新 LRU 序；插入后按预算逐出。"""
    global _cache_inserts
    _image_cache[key] = QImage(image)
    _image_last_used[key] = next(_cache_clock)
    _cache_inserts += 1
    _trim_image_cache_locked()


def image_cache_stats() -> dict:
    """缩略图内存缓存当前占用 + 命中/逐出计数（P3 观测，供测试断言）。"""
    with _cache_lock:
        return {
            "hits": _cache_hits,
            "misses": _cache_misses,
            "evictions": _cache_evictions,
            "inserts": _cache_inserts,
            "entries": len(_image_cache),
            "bytes": _image_cache_bytes(),
            "max_bytes": _CACHE_MAX_BYTES,
            "max_entries": _CACHE_LIMIT,
        }


def decode_representative_frame(path: str | Path) -> QImage:
    """Decode once per file version and share the result across pet windows."""
    global _cache_hits, _cache_misses
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return QImage()
    key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))

    with _cache_lock:
        cached = _image_cache.get(key)
        if cached is not None:
            _cache_hits += 1
            _image_last_used[key] = next(_cache_clock)
            return QImage(cached)
        _cache_misses += 1
        disk_cached = _read_disk_cache(key)
        if not disk_cached.isNull():
            _cache_insert_locked(key, disk_cached)
            return disk_cached
        event = _inflight.get(key)
        owner = event is None
        if owner:
            event = threading.Event()
            _inflight[key] = event

    if not owner:
        event.wait()
        with _cache_lock:
            cached = _image_cache.get(key)
            if cached is not None:
                _cache_hits += 1
                _image_last_used[key] = next(_cache_clock)
                return QImage(cached)
            _cache_misses += 1
            return QImage()

    try:
        with _DECODE_SEMAPHORE:
            image = _decode_representative_frame(path)
        if not image.isNull():
            with _cache_lock:
                _cache_insert_locked(key, image)
            _write_disk_cache(key, image)
        return image
    finally:
        with _cache_lock:
            event = _inflight.pop(key, None)
            if event is not None:
                event.set()
