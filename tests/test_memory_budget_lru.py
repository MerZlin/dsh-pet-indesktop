# -*- coding: utf-8 -*-
"""P3：内存分层预算 + LRU 治理（_plan/WIN_PERF_RESEARCH_SOL.md §4.2/§4.4）。

覆盖：
- 首帧缓存（WebMClip._first_image 经 MovieLibrary 治理，L2）：
  * 预算 = 角色内动画数 × 单帧大小（character_first_frame_budget）；
  * 超限逐出最久未用的冷门动画首帧（evict_first_frame 清图 + 完成事件）；
  * 冷动画被逐出后用到再解码（jumpToFrame(0)/warm 重新解码，允许代价）；
  * 刚用过的动画（正在播放）不被逐出（LRU 刷新保护）；
  * 逐出绝不动当前播放帧 / 当前 alpha 图（只清首帧缓存）；
  * stats 暴露 hits/misses/evictions/inserts/entries/bytes/max_bytes。
- 缩略图缓存（animation_thumbnail._image_cache，L3）：
  * 字节预算硬上界 + 条数上限；超限逐出最久未用的大块头（LRU 平局先丢大）；
  * 命中刷新 LRU；逐出只清内存缓存，磁盘缓存保留；
  * stats 暴露 hits/misses/evictions/inserts/entries/bytes/max_bytes/max_entries。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

import pet.library as library_mod
from pet import catalog
from pet.first_frame_cache import FirstFrameCache, character_first_frame_budget
from pet.webm_clip import WebMClip


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class _StubDecodeClip(WebMClip):
    """真实 WebMClip + 桩解码：不碰 ffmpeg，解码即得小 QImage，计数解码次数。"""

    def __init__(self, path, parent=None, first_frame_cache=None):
        super().__init__(path, parent, first_frame_cache=first_frame_cache)
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self, gen=None):
        with self._counter_lock:
            self.decode_count += 1
        return QImage(4, 4, QImage.Format.Format_RGBA8888)


# ================================================================ 首帧缓存：FirstFrameCache 本体

class _FakeClip:
    """FirstFrameCache 协议的最小实现（与 WebMClip 首帧行为一致）。"""

    def __init__(self, name: str, frame_bytes: int):
        self.name = name
        self._first_frame_bytes = frame_bytes
        self._first_image = None
        self._first_frame_done = threading.Event()

    def store(self, img: QImage) -> None:
        if self._first_image is None:
            self._first_image = img
            self._first_frame_done.set()

    def evict_first_frame(self) -> None:
        self._first_image = None
        self._first_frame_done.clear()


def test_first_frame_budget_formula():
    """预算 = 角色内动画数 × 单帧 RGBA 大小（§4.2 计算方法）；至少一帧。"""
    assert character_first_frame_budget(91, 640, 360) == 91 * 640 * 360 * 4
    assert character_first_frame_budget(0, 640, 360) == 640 * 360 * 4


def test_first_frame_cache_lru_eviction_and_stats():
    """字节预算 LRU：超限逐出最久未用；命中刷新；计数与字节正确；单条目不逐空。"""
    _qapp()
    fb = 100
    cache = FirstFrameCache(max_bytes=2 * fb)  # 恰好两条
    a, b, c = (_FakeClip(n, fb) for n in "abc")
    for clip in (a, b):
        clip.store(QImage(4, 4, QImage.Format.Format_RGBA8888))
        cache.note_stored(clip)
    assert len(cache) == 2
    assert cache.stats()["inserts"] == 2
    assert cache.stats()["evictions"] == 0
    assert cache.stats()["bytes"] == 2 * fb

    # 第三条 → 逐出最久未用的 a（清图 + 清完成事件）
    c.store(QImage(4, 4, QImage.Format.Format_RGBA8888))
    cache.note_stored(c)
    assert len(cache) == 2
    assert a._first_image is None and not a._first_frame_done.is_set()
    assert b._first_image is not None and c._first_image is not None
    assert cache.stats()["evictions"] == 1
    assert cache.stats()["bytes"] == 2 * fb

    # 用过的（note_used 刷新 LRU）不被逐出；同 key 重复 note_stored 不重复计数
    cache.note_used(b)
    cache.note_stored(b)
    assert cache.stats()["inserts"] == 3
    d = _FakeClip("d", fb)
    d.store(QImage(4, 4, QImage.Format.Format_RGBA8888))
    cache.note_stored(d)
    assert b._first_image is not None   # b 刚用过 → 保留
    assert c._first_image is None       # c 最久未用 → 被逐出

    # 未命中统计
    e = _FakeClip("e", fb)
    cache.note_used(e)
    assert cache.stats()["misses"] >= 1

    # 单条目超预算不逐空（防抖动，与 FramePixmapCache 同策略）
    tiny = FirstFrameCache(max_bytes=10)
    f = _FakeClip("f", 100)
    f.store(QImage(4, 4, QImage.Format.Format_RGBA8888))
    tiny.note_stored(f)
    assert len(tiny) == 1
    assert tiny.stats()["bytes"] == 100


# ================================================================ 首帧缓存：MovieLibrary 接线

def _make_library(tmp_path, monkeypatch, *, max_bytes=None):
    monkeypatch.setattr(library_mod, "WebMClip", _StubDecodeClip)
    videos = tmp_path / "videos"
    folders = {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm", "吃白饭.webm"],
    }
    for folder, files in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    return library_mod.MovieLibrary(
        asset_dir=videos,
        first_frame_cache_max_bytes=max_bytes,
    )


def _cleanup(*clips):
    for clip in clips:
        try:
            clip.cleanup()
        except Exception:
            pass


def test_library_default_budget_is_anim_count_times_frame_bytes(tmp_path, monkeypatch):
    """无覆盖时：预算 = 角色内动画数 × 单帧大小（640×360×4）。"""
    _qapp()
    lib = _make_library(tmp_path, monkeypatch)
    anims = len(lib.names())
    assert lib.first_frame_cache_stats()["max_bytes"] == (
        anims * catalog.CANVAS_W * catalog.CANVAS_H * 4
    )


def test_library_first_frame_cache_evicts_cold_animation(tmp_path, monkeypatch):
    """超预算逐出冷门动画首帧；被逐出后 jumpToFrame(0) 用到再解码。"""
    _qapp()
    fb = catalog.CANVAS_W * catalog.CANVAS_H * 4
    lib = _make_library(tmp_path, monkeypatch, max_bytes=fb)  # 只够 1 条
    idle = lib.movie("待机呼吸休闲")
    turn = lib.movie("东张西望")
    idle.warm_first_frame()
    assert idle._first_image is not None
    turn.warm_first_frame()  # 超预算 → 逐出最久未用的 idle
    assert idle._first_image is None, "冷门动画首帧必须被逐出"
    assert not idle._first_frame_done.is_set(), "逐出必须清完成事件（用到再解码）"
    assert turn._first_image is not None

    # 用到再解码：jumpToFrame(0) 重新解码并应用到当前帧
    n_before = idle.decode_count
    idle.jumpToFrame(0)
    assert idle.decode_count == n_before + 1
    assert idle._first_image is not None
    assert idle._current_pixmap is not None
    # 重新缓存的 idle 成为最近使用 → turn 变为最久未用被逐出
    assert turn._first_image is None

    stats = lib.first_frame_cache_stats()
    assert stats["evictions"] == 2
    assert stats["entries"] == 1
    _cleanup(idle, turn)


def test_library_first_frame_cache_keeps_recently_used(tmp_path, monkeypatch):
    """刚用过的动画（正在播放）不被逐出：LRU 刷新保护。"""
    _qapp()
    fb = catalog.CANVAS_W * catalog.CANVAS_H * 4
    lib = _make_library(tmp_path, monkeypatch, max_bytes=2 * fb)  # 两条
    idle = lib.movie("待机呼吸休闲")
    turn = lib.movie("东张西望")
    idle.warm_first_frame()
    turn.warm_first_frame()
    idle.jumpToFrame(0)  # 使用 idle 首帧 → LRU 刷新到最近
    write_code = lib.movie("写代码")
    write_code.warm_first_frame()  # 第三条 → 逐出最久未用的 turn
    assert turn._first_image is None
    assert idle._first_image is not None, "刚用过的动画不得被逐出"
    assert write_code._first_image is not None
    assert idle._current_pixmap is not None, "当前播放帧不受影响"
    _cleanup(idle, turn, write_code)


def test_evict_first_frame_never_touches_current_frame_or_alpha(tmp_path, monkeypatch):
    """§4.4：逐出只清首帧缓存与完成事件，绝不动当前播放帧/当前 alpha 图。"""
    _qapp()
    fb = catalog.CANVAS_W * catalog.CANVAS_H * 4
    lib = _make_library(tmp_path, monkeypatch, max_bytes=fb)
    idle = lib.movie("待机呼吸休闲")
    turn = lib.movie("东张西望")
    idle.warm_first_frame()
    # 模拟窗口已把首帧应用到当前播放帧（窗口持有引用，不受缓存治理影响）
    idle._current_image = idle._first_image
    idle._current_pixmap = QPixmap.fromImage(idle._first_image)
    current_pm = idle._current_pixmap
    current_img = idle._current_image

    turn.warm_first_frame()  # 超预算 → 逐出 idle 首帧
    assert idle._first_image is None
    assert idle._current_pixmap is current_pm, "逐出绝不动当前播放帧 pixmap"
    assert idle._current_image is current_img, "逐出绝不动当前播放帧图像"
    _cleanup(idle, turn)


def test_library_first_frame_stats_readable(tmp_path, monkeypatch):
    """首帧缓存当前占用可从 stats 读取（供测试断言/遥测）。"""
    _qapp()
    fb = catalog.CANVAS_W * catalog.CANVAS_H * 4
    lib = _make_library(tmp_path, monkeypatch, max_bytes=2 * fb)
    idle = lib.movie("待机呼吸休闲")
    idle.warm_first_frame()
    stats = lib.first_frame_cache_stats()
    assert stats["entries"] == 1
    assert stats["bytes"] == fb
    assert stats["max_bytes"] == 2 * fb
    assert stats["inserts"] == 1
    _cleanup(idle)


# ================================================================ 缩略图缓存：字节预算 LRU + stats

def _thumb_image(w: int = 16) -> QImage:
    return QImage(w, w, QImage.Format.Format_RGBA8888)


def _reset_thumbs(thumbnail, monkeypatch, tmp_path, *, max_bytes=None, limit=None):
    monkeypatch.setattr(thumbnail, "_DISK_CACHE_DIR", tmp_path / "thumbs")
    if max_bytes is not None:
        monkeypatch.setattr(thumbnail, "_CACHE_MAX_BYTES", max_bytes)
    if limit is not None:
        monkeypatch.setattr(thumbnail, "_CACHE_LIMIT", limit)
    thumbnail._image_cache.clear()
    thumbnail._image_last_used.clear()
    thumbnail._inflight.clear()
    thumbnail._cache_hits = 0
    thumbnail._cache_misses = 0
    thumbnail._cache_evictions = 0
    thumbnail._cache_inserts = 0


def test_thumbnail_cache_byte_budget_lru_and_stats(monkeypatch, tmp_path):
    """字节预算：超限逐出最久未用；命中刷新 LRU；stats 暴露占用。"""
    import pet.animation_thumbnail as thumbnail

    entry_bytes = 16 * 16 * 4
    _reset_thumbs(thumbnail, monkeypatch, tmp_path, max_bytes=2 * entry_bytes)
    calls = []
    monkeypatch.setattr(
        thumbnail,
        "_decode_representative_frame",
        lambda _p: calls.append(1) or _thumb_image(16),
    )
    paths = [tmp_path / f"a{i}.webm" for i in range(3)]
    for p in paths:
        p.write_bytes(b"x")
        assert not thumbnail.decode_representative_frame(p).isNull()

    # 第三条插入 → 逐出最久未用的第一条
    stats = thumbnail.image_cache_stats()
    assert stats["entries"] == 2
    assert stats["bytes"] == 2 * entry_bytes
    assert stats["evictions"] == 1
    assert stats["inserts"] == 3
    assert stats["misses"] == 3
    key0 = (str(paths[0].resolve()), paths[0].stat().st_mtime_ns, paths[0].stat().st_size)
    key1 = (str(paths[1].resolve()), paths[1].stat().st_mtime_ns, paths[1].stat().st_size)
    assert key0 not in thumbnail._image_cache
    assert key1 in thumbnail._image_cache

    # 命中刷新 LRU：命中 a1 后再插第四条 → 逐出 a2（而非刚命中的 a1）
    assert not thumbnail.decode_representative_frame(paths[1]).isNull()
    stats = thumbnail.image_cache_stats()
    assert stats["hits"] == 1
    p3 = tmp_path / "a3.webm"
    p3.write_bytes(b"x")
    assert not thumbnail.decode_representative_frame(p3).isNull()
    key2 = (str(paths[2].resolve()), paths[2].stat().st_mtime_ns, paths[2].stat().st_size)
    assert key1 in thumbnail._image_cache, "刚命中的条目不得被逐出"
    assert key2 not in thumbnail._image_cache, "最久未用的条目先被逐出"
    assert thumbnail.image_cache_stats()["bytes"] <= 2 * entry_bytes


def test_thumbnail_cache_count_limit_still_enforced(monkeypatch, tmp_path):
    """条数上限仍生效（字节预算不触发时按条数逐出）。"""
    import pet.animation_thumbnail as thumbnail

    _reset_thumbs(thumbnail, monkeypatch, tmp_path, limit=2)
    monkeypatch.setattr(thumbnail, "_CACHE_MAX_BYTES", 10 ** 9)  # 字节预算不触发
    monkeypatch.setattr(thumbnail, "_decode_representative_frame", lambda _p: _thumb_image(16))
    for i in range(3):
        p = tmp_path / f"c{i}.webm"
        p.write_bytes(b"x")
        assert not thumbnail.decode_representative_frame(p).isNull()
    stats = thumbnail.image_cache_stats()
    assert stats["entries"] == 2
    assert stats["max_entries"] == 2
    assert stats["evictions"] == 1


def test_thumbnail_memory_eviction_keeps_disk_cache(monkeypatch, tmp_path):
    """§4.4 步骤 3：清理缩略图内存缓存，但保留磁盘缓存——内存逐出后
    再从磁盘命中，不重复解码。"""
    import pet.animation_thumbnail as thumbnail

    entry_bytes = 16 * 16 * 4
    _reset_thumbs(thumbnail, monkeypatch, tmp_path, max_bytes=2 * entry_bytes)
    calls = []
    monkeypatch.setattr(
        thumbnail,
        "_decode_representative_frame",
        lambda _p: calls.append(1) or _thumb_image(16),
    )
    paths = [tmp_path / f"d{i}.webm" for i in range(3)]
    for p in paths:
        p.write_bytes(b"x")
        assert not thumbnail.decode_representative_frame(p).isNull()
    assert len(calls) == 3

    # 第一条已被内存逐出；再次解码 → 磁盘缓存命中，不重新 ffmpeg 解码
    assert not thumbnail.decode_representative_frame(paths[0]).isNull()
    assert len(calls) == 3
    assert thumbnail.image_cache_stats()["inserts"] == 4  # 磁盘回填重新入内存


def test_thumbnail_stats_shape(monkeypatch, tmp_path):
    """缩略图 stats 形状与首帧/预缩放缓存统一（供测试断言）。"""
    import pet.animation_thumbnail as thumbnail

    _reset_thumbs(thumbnail, monkeypatch, tmp_path)
    stats = thumbnail.image_cache_stats()
    for key in ("hits", "misses", "evictions", "inserts", "entries", "bytes",
                "max_bytes", "max_entries"):
        assert key in stats
    assert stats["max_bytes"] > 0
    assert stats["max_entries"] > 0
