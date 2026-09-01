# -*- coding: utf-8 -*-
"""预缩放帧缓存（_plan/WIN_PERF_RESEARCH_SOL.md §3.1 方案 A）。

覆盖：
- 最终 QPixmap 有界 LRU：命中跳过整条 toImage→镜像→预乘→Smooth 缩放→
  ARGB32→fromImage 转换链（动画循环播放不再重算）；
- key 含（素材路径+mtime、帧号、朝向、scale、DPR、动画名）；
- scale/DPR/朝向/动画名变化、素材文件 mtime 变化正确失效；
- squash/Q 弹只改绘制矩形，不误伤缓存（不重建、不逐出）；
- _hit_alpha_image 与 _frame_pixmap 出自同一缓存条目，命中测试不串帧；
- 命中/逐出/缺失/插入计数暴露，供测试断言。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet import window as window_mod
from pet.frame_cache import FRAME_CACHE_DEFAULT_MAX_BYTES, FramePixmapCache


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _frame_image(variant: int) -> QImage:
    """640x360 帧：variant=0 左红块，variant=1 右绿块（非对称，可检验镜像/串帧）。"""
    img = QImage(640, 360, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    if variant == 0:
        p.fillRect(0, 0, 100, 100, QColor(255, 0, 0, 255))
    else:
        p.fillRect(300, 0, 100, 100, QColor(0, 255, 0, 255))
    p.end()
    return img


class _FramesClip:
    """每帧内容可不同；记录 currentPixmap 调用次数（转换链是否被跳过）。"""

    def __init__(self, images, frame_number: int = 0):
        self._images = list(images)
        self._frame_number = frame_number
        self.pixmap_requests = 0

    def currentPixmap(self):
        self.pixmap_requests += 1
        return QPixmap.fromImage(self._images[self._frame_number])

    def currentFrameNumber(self):
        return self._frame_number

    def jumpToFrame(self, n):
        self._frame_number = max(0, min(n, len(self._images) - 1))
        return self._frame_number <= 0

    def stop(self):
        pass

    def start(self):
        pass

    def set_playback_speed(self, speed):
        pass

    def frameCount(self):
        return len(self._images)

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class _CacheLibrary:
    def __init__(self, clips, clip_paths=None, no_mirror=frozenset()):
        self._clips = dict(clips)
        self._paths = dict(clip_paths or {})
        self.no_mirror = set(no_mirror)
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name].frameCount()

    def duration(self, name):
        return 1.0

    def clip_path(self, name):
        return self._paths.get(name)


class _CachePet:
    """只挂载 _rebuild_frame / _is_transparent_at / _frame_draw_rect 的假窗口。"""

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _is_transparent_at = window_mod.PetWindow._is_transparent_at
    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect
    _frame_cache_key = window_mod.PetWindow._frame_cache_key

    def __init__(self, movie, lib, facing="left", scale=0.5, anim="idle", dpr=1.0,
                 cache_max_bytes=None):
        self.movie = movie
        self.lib = lib
        self.facing = facing
        self.scale = scale
        self.anim = anim
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._frame_pixmap = None
        self._hit_alpha_image = None
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._frame_key = None
        self._screen_dpr = dpr
        self._squash_active = False
        self._squash_progress = 1.0
        self._sync_mask_calls = 0
        if cache_max_bytes is not None:
            self._frame_cache_max_bytes = cache_max_bytes

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def _sync_mask(self):
        self._sync_mask_calls += 1


def _make_pet(clip_images, **kwargs):
    clip = _FramesClip(clip_images)
    lib = _CacheLibrary({"idle": clip})
    return clip, _CachePet(clip, lib, **kwargs)


# ================================================================ 缓存本体：LRU / 预算 / 计数

def test_frame_cache_lru_eviction_and_counters():
    """字节预算 LRU：超限逐出最久未用；命中/逐出/缺失/插入计数正确。"""
    _qapp()
    cache = FramePixmapCache(max_bytes=3 * 1024)  # 每条 16x16x4 = 1024B
    imgs = [QImage(16, 16, QImage.Format.Format_ARGB32) for _ in range(4)]
    for name, img in zip("abc", imgs[:3]):
        cache.put((name,), None, img)
    assert len(cache) == 3
    assert cache.stats()["evictions"] == 0
    assert cache.stats()["inserts"] == 3
    assert cache.stats()["bytes"] == 3 * 1024

    # 命中 a → LRU 序变为 b, c, a
    assert cache.get(("a",)) is not None
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 0

    # 再放 d → 逐出最久未用的 b
    cache.put(("d",), None, imgs[3])
    assert cache.get(("b",)) is None  # 已被逐出
    assert cache.stats()["misses"] == 1
    assert cache.stats()["evictions"] == 1
    assert cache.stats()["bytes"] == 3 * 1024
    for name in "acd":
        assert cache.get((name,)) is not None


def test_frame_cache_single_oversized_entry_stays():
    """单帧超过预算：保留该条目不自我逐出；再放新条目时逐出旧大帧。"""
    _qapp()
    cache = FramePixmapCache(max_bytes=1024)
    big = QImage(64, 64, QImage.Format.Format_ARGB32)  # 16384B > 预算
    cache.put(("big",), None, big)
    assert len(cache) == 1
    assert cache.get(("big",)) is not None
    small = QImage(8, 8, QImage.Format.Format_ARGB32)  # 256B
    cache.put(("small",), None, small)
    assert cache.get(("big",)) is None
    assert cache.get(("small",)) is not None


def test_frame_cache_put_replaces_and_reaccounts_bytes():
    """同 key 重复 put：替换条目且字节重新记账（不重复累计）。"""
    _qapp()
    cache = FramePixmapCache(max_bytes=4096)
    cache.put(("k",), None, QImage(8, 8, QImage.Format.Format_ARGB32))    # 256B
    assert cache.total_bytes() == 256
    cache.put(("k",), None, QImage(16, 16, QImage.Format.Format_ARGB32))  # 1024B
    assert len(cache) == 1
    assert cache.total_bytes() == 1024
    assert cache.stats()["inserts"] == 2
    assert cache.stats()["evictions"] == 0


def test_frame_cache_clear_empties_entries_and_bytes():
    _qapp()
    cache = FramePixmapCache(max_bytes=1024)
    cache.put(("a",), None, QImage(8, 8, QImage.Format.Format_ARGB32))
    cache.clear()
    assert len(cache) == 0
    assert cache.total_bytes() == 0
    assert cache.stats()["entries"] == 0


def test_frame_cache_default_budget_constant():
    assert FRAME_CACHE_DEFAULT_MAX_BYTES == 256 * 1024 * 1024


# ================================================================ _rebuild_frame 集成

def test_rebuild_frame_reuses_cached_pixmap_across_loop_iterations():
    """动画循环回到已构建帧：命中缓存，整条转换链（currentPixmap→…→fromImage）跳过。"""
    _qapp()
    clip, pet = _make_pet([_frame_image(0), _frame_image(1)], anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    first_pm = pet._frame_pixmap
    first_alpha = pet._hit_alpha_image
    assert pet._frame_cache.stats()["misses"] == 1
    assert pet._frame_cache.stats()["hits"] == 0

    clip.jumpToFrame(1)
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_pixmap is not first_pm
    assert pet._frame_cache.stats()["misses"] == 2

    # 回到帧 0：命中，不再调用 currentPixmap（帧 0/1 各只解码一次）
    clip.jumpToFrame(0)
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 2
    assert pet._frame_cache.stats()["hits"] == 1
    assert pet._frame_cache.stats()["misses"] == 2
    assert pet._frame_pixmap is first_pm       # 命中返回同一 QPixmap
    assert pet._hit_alpha_image is first_alpha
    assert pet._sync_mask_calls == 3           # 命中仍刷新 mask（显示帧已变化）


def test_rebuild_frame_cache_separates_scale_dpr_facing_anim():
    """scale/DPR/朝向/动画名各自独立成条目；回退旧参数命中旧条目。"""
    _qapp()
    clip, pet = _make_pet([_frame_image(0)], anim="idle", scale=0.5)
    lib = pet.lib
    lib._clips["walk"] = clip
    window_mod.PetWindow._rebuild_frame(pet)
    pm_base = pet._frame_pixmap

    # scale 变化 → miss；回退旧 scale → 命中
    pet.scale = 0.8
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_pixmap is not pm_base
    pet.scale = 0.5
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_pixmap is pm_base
    assert pet._frame_cache.stats()["hits"] == 1
    assert pet._frame_cache.stats()["misses"] == 2

    # DPR 变化 → miss（物理像素尺寸不同）
    pet._screen_dpr = 2.0
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
    assert pet._frame_cache.stats()["misses"] == 3

    # 朝向变化（普通动画朝右 → 镜像）→ miss，且画面确实镜像
    pet._screen_dpr = 1.0
    pm_left = pet._frame_pixmap
    pet.facing = "right"
    window_mod.PetWindow._rebuild_frame(pet)
    out = pet._frame_pixmap.toImage()
    assert out.pixelColor(300, 20).red() > 200  # 红块镜像到右侧
    assert pet._frame_pixmap is not pm_left
    assert pet._frame_cache.stats()["misses"] == 4

    # 动画名变化（同一 clip）→ miss；回退 → 命中
    pet.facing = "left"
    pet.anim = "walk"
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.stats()["misses"] == 5
    pet.anim = "idle"
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.stats()["hits"] == 2


def test_rebuild_frame_no_mirror_animation_never_mirrored_with_cache():
    """no_mirror 动画（文字动画）朝右：缓存路径下仍不镜像。"""
    _qapp()
    clip = _FramesClip([_frame_image(0)])
    lib = _CacheLibrary({"talk": clip}, no_mirror={"talk"})
    pet = _CachePet(clip, lib, facing="right", anim="talk", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    out = pet._frame_pixmap.toImage()
    assert out.pixelColor(20, 20).red() > 200    # 红块仍在左侧（未镜像）


def test_rebuild_frame_invalidates_on_material_file_change(tmp_path):
    """素材文件 mtime 变化：同一路径同一帧号也必须重新构建，不得复用旧缓存。"""
    _qapp()
    path = tmp_path / "idle.webm"
    path.write_bytes(b"v0")
    os.utime(path, (1000, 1000))

    clip0 = _FramesClip([_frame_image(0)])
    pet = _CachePet(clip0, _CacheLibrary({"idle": clip0}, clip_paths={"idle": path}),
                    anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    pm_v0 = pet._frame_pixmap
    assert pet._frame_cache.stats()["misses"] == 1

    # 素材被替换（mtime 变化）+ 新 clip 实例（同一路径同一帧号）
    path.write_bytes(b"v1")
    os.utime(path, (2000, 2000))
    clip1 = _FramesClip([_frame_image(0)])
    pet.movie = clip1
    pet.lib = _CacheLibrary({"idle": clip1}, clip_paths={"idle": path})
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.stats()["misses"] == 2  # mtime 不同 → 不命中旧条目
    assert pet._frame_pixmap is not pm_v0
    assert len(pet._frame_cache) == 2               # 新旧条目并存（旧条目等 LRU 逐出）

    # 同一路径 + 同一 mtime + 同一帧：即使 movie 实例不同也应命中（结果确定）
    clip2 = _FramesClip([_frame_image(0)])
    pet.movie = clip2
    pet.lib = _CacheLibrary({"idle": clip2}, clip_paths={"idle": path})
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.stats()["hits"] == 1
    assert pet._frame_pixmap is not pm_v0
    new_img = pet._frame_pixmap.toImage()
    old_img = pm_v0.toImage()
    assert new_img.size() == old_img.size()
    assert new_img.pixelColor(10, 10) == old_img.pixelColor(10, 10)
    assert new_img.pixelColor(100, 100) == old_img.pixelColor(100, 100)


def test_character_switch_gets_fresh_cache_per_window():
    """角色切换（新窗口新库）：缓存互相独立，同名动画不同角色互不串用。"""
    _qapp()
    clip_a = _FramesClip([_frame_image(0)])
    pet_a = _CachePet(clip_a, _CacheLibrary({"idle": clip_a},
                                            clip_paths={"idle": Path("charA.webm")}),
                      anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet_a)
    assert pet_a._frame_cache.stats()["misses"] == 1

    clip_b = _FramesClip([_frame_image(0)])
    pet_b = _CachePet(clip_b, _CacheLibrary({"idle": clip_b},
                                            clip_paths={"idle": Path("charB.webm")}),
                      anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet_b)
    assert pet_b._frame_cache.stats()["misses"] == 1
    assert pet_b._frame_cache is not pet_a._frame_cache

    ka = pet_a._frame_cache_key(0, 1.0)
    kb = pet_b._frame_cache_key(0, 1.0)
    assert ka[0] != kb[0]   # 素材路径不同
    assert ka != kb


def test_squash_geometry_does_not_touch_frame_cache():
    """squash/Q 弹只改绘制矩形：pixmap 不变，缓存不重建、不逐出、不误伤。"""
    _qapp()
    clip, pet = _make_pet([_frame_image(0)], anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    pm = pet._frame_pixmap
    stats_before = pet._frame_cache.stats()

    pet._squash_active = True
    pet._squash_progress = 0.3
    rect = window_mod.PetWindow._frame_draw_rect(pet)
    base = QRect(0, int(round(catalog.PAD * 0.5)),
                 int(round(catalog.CANVAS_W * 0.5)),
                 int(round(catalog.CANVAS_H * 0.5)))
    assert rect != base, "Q 弹必须改变绘制矩形（验证几何确实在变）"

    # 模拟 _on_squash_tick：只走 _sync_mask / 绘制，不重建帧
    pet._sync_mask()
    assert pet._frame_pixmap is pm
    assert pet._frame_cache.stats() == stats_before
    assert len(pet._frame_cache) == 1
    assert pet._frame_cache.total_bytes() > 0

    # squash 结束同样不触发重建/逐出
    pet._squash_active = False
    pet._squash_progress = 1.0
    assert pet._frame_cache.stats() == stats_before


def test_hit_alpha_image_consistent_after_cache_hit():
    """缓存命中后 _hit_alpha_image 必须是当前帧的 alpha 图：命中测试不串帧。"""
    _qapp()
    clip, pet = _make_pet([_frame_image(0), _frame_image(1)], anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    # 帧 0：左红块（scale 0.5 → 物理像素 x 0..50）
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(10, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(170, 20)) is True

    # 帧 1：右绿块（物理像素 x 150..200）
    clip.jumpToFrame(1)
    window_mod.PetWindow._rebuild_frame(pet)
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(170, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(10, 20)) is True

    # 回到帧 0：缓存命中，alpha 图随之回到帧 0（不得残留帧 1 的图）
    clip.jumpToFrame(0)
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.stats()["hits"] == 1
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(10, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(170, 20)) is True


def test_frame_cache_default_budget_is_256mb_on_lazy_init():
    """无显式配置时（_frame_cache_max_bytes 缺省）默认 256MB 预算。"""
    _qapp()
    clip, pet = _make_pet([_frame_image(0)], anim="idle")
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._frame_cache.max_bytes() == FRAME_CACHE_DEFAULT_MAX_BYTES


def test_pet_window_init_creates_bounded_cache_with_default_budget():
    """真实 PetWindow：__init__ 即建缓存，默认 256MB。"""
    _qapp()
    import tempfile

    from pet.config import Config
    from pet.library import MovieLibrary
    lib = MovieLibrary(character_id="shenshen")
    win = window_mod.PetWindow(lib, Config(base=Path(tempfile.mkdtemp())))
    try:
        assert win._frame_cache.max_bytes() == FRAME_CACHE_DEFAULT_MAX_BYTES
    finally:
        win.close()


def test_pet_window_cache_budget_from_config():
    """真实 PetWindow：frame_cache_max_bytes 配置可覆盖默认预算。"""
    _qapp()
    import tempfile

    from pet.config import Config
    from pet.library import MovieLibrary
    cfg = Config(base=Path(tempfile.mkdtemp()))
    cfg.set("frame_cache_max_bytes", 2 * 1024 * 1024)
    lib = MovieLibrary(character_id="shenshen")
    win = window_mod.PetWindow(lib, cfg)
    try:
        assert win._frame_cache.max_bytes() == 2 * 1024 * 1024
    finally:
        win.close()
