import os
import time

from PySide6.QtGui import QImage


def _image():
    return QImage(4, 4, QImage.Format.Format_RGBA8888)


def _reset(thumbnail, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail, "_DISK_CACHE_DIR", tmp_path / "thumbs")
    thumbnail._image_cache.clear()
    thumbnail._inflight.clear()


def test_disk_cache_hit_and_mtime_invalidation(monkeypatch, tmp_path):
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    path = tmp_path / "clip.webm"
    path.write_bytes(b"clip")
    calls = []

    def decode(_path):
        calls.append(1)
        return _image()

    monkeypatch.setattr(thumbnail, "_decode_representative_frame", decode)
    assert not thumbnail.decode_representative_frame(path).isNull()
    thumbnail._image_cache.clear()
    assert not thumbnail.decode_representative_frame(path).isNull()
    assert len(calls) == 1

    old_ns = path.stat().st_mtime_ns
    os.utime(path, ns=(old_ns + 1_000_000_000, old_ns + 1_000_000_000))
    thumbnail._image_cache.clear()
    assert not thumbnail.decode_representative_frame(path).isNull()
    assert len(calls) == 2


def test_half_written_cache_file_is_ignored(monkeypatch, tmp_path):
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    path = tmp_path / "clip.webm"
    path.write_bytes(b"clip")
    key = (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
    cache_path = thumbnail._disk_cache_path(key)
    cache_path.parent.mkdir()
    cache_path.write_bytes(b"not a png")
    calls = []
    monkeypatch.setattr(
        thumbnail,
        "_decode_representative_frame",
        lambda _path: calls.append(1) or _image(),
    )

    assert not thumbnail.decode_representative_frame(path).isNull()
    assert calls == [1]


def test_disk_cache_eviction_keeps_limit(monkeypatch, tmp_path):
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    monkeypatch.setattr(thumbnail, "_DISK_CACHE_LIMIT", 2)
    for index in range(3):
        path = tmp_path / f"clip-{index}.webm"
        path.write_bytes(str(index).encode())
        monkeypatch.setattr(thumbnail, "_decode_representative_frame", lambda _path: _image())
        thumbnail.decode_representative_frame(path)
        time.sleep(0.002)

    assert len(list((tmp_path / "thumbs").glob("*.png"))) == 2


def test_decode_failure_falls_back_without_cache_entry(monkeypatch, tmp_path):
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    path = tmp_path / "clip.webm"
    path.write_bytes(b"clip")
    monkeypatch.setattr(thumbnail, "_decode_representative_frame", lambda _path: QImage())

    assert thumbnail.decode_representative_frame(path).isNull()
    assert list((tmp_path / "thumbs").glob("*.png")) == []




def test_decode_result_is_bounded_to_thumbnail_size(monkeypatch, tmp_path):
    """代表帧入缓存/落盘前必须收成 128px 缩略图。

    回归：全尺寸（640×390+ RGBA ≈1MB/张）整帧缓存上百段动画 → 常驻
    100MB+（实机：右键逛动画菜单 90→231MB 不回落）；菜单图标槽位只有
    ~18 逻辑像素，缩略图视觉无损。
    """
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    path = tmp_path / "big.webm"
    path.write_bytes(b"clip")
    monkeypatch.setattr(
        thumbnail, "_decode_representative_frame",
        lambda _path: QImage(640, 390, QImage.Format.Format_RGBA8888),
    )
    image = thumbnail.decode_representative_frame(path)
    assert not image.isNull()
    assert max(image.width(), image.height()) <= thumbnail._THUMBNAIL_MAX_SIDE
    # 内存缓存里的副本也是缩略图
    cached = next(iter(thumbnail._image_cache.values()))
    assert max(cached.width(), cached.height()) <= thumbnail._THUMBNAIL_MAX_SIDE


def test_legacy_fullres_disk_entry_scaled_on_read(monkeypatch, tmp_path):
    """旧版磁盘缓存里的全尺寸 PNG：读入内存时缩放，不把大图带进缓存。"""
    import pet.animation_thumbnail as thumbnail

    _reset(thumbnail, monkeypatch, tmp_path)
    path = tmp_path / "legacy.webm"
    path.write_bytes(b"clip")
    # 造一个全尺寸旧条目（绕过新写入路径）
    key = (str(path.resolve()), int(path.stat().st_mtime_ns), int(path.stat().st_size))
    big = QImage(800, 600, QImage.Format.Format_RGBA8888)
    big.fill(0xFF112233)
    cache_path = thumbnail._disk_cache_path(key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    assert big.save(str(cache_path), "PNG")

    image = thumbnail.decode_representative_frame(path)
    assert not image.isNull()
    assert max(image.width(), image.height()) <= thumbnail._THUMBNAIL_MAX_SIDE
