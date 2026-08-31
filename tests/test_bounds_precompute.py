# -*- coding: utf-8 -*-
"""B14：动画 bounds 预计算 —— 每动画 union 可见 bounds / 每帧 bounds / 脚底锚点。

锁定的行为契约：
1. 预计算函数 bounds_precompute.frame_window_bounds 与运行时
   _rebuild_frame → _sync_mask（Windows 分支画布扫描）逐帧完全一致：
   合成帧（含空帧、alpha 127/128 阈值、镜像）+ 真实 webm 素材，多 scale × DPR；
2. PetWindow._sync_mask Windows 分支命中预计算缓存时免去 O(像素) 扫描，
   且 _mask_bounds 与扫描路径完全一致；未命中/无缓存/squash 几何变化时
   回落到现有逐帧扫描（行为不变）；
3. _collision_local_bounds 语义保持：稳定 union bounds、切换动画/缩放重置、
   不同动画的缓存互不串味；
4. WebMClip.warm_bounds 复用首帧解码的 锁 + 代次 + 进程登记 生命周期：
   原子认领（并发只一个执行者）、取消换代并 terminate 在飞解码进程、
   cleanup 取消、结果整包原子提交（绝不提交半成品 union）；
5. 内存可控：每动画每模式存储 ~2KB（241 帧 × 4×int16）。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication

from pet import window as window_mod
from pet import catalog
from pet import bounds_precompute as bp
from pet.webm_clip import WebMClip
import pet.webm_clip as webm_clip_mod

pytest.importorskip("imageio_ffmpeg", reason="imageio-ffmpeg 不可用，无法解码真实 webm")


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


# ================================================================ 合成素材
def _make_rgba_frame(width: int, height: int, opaque_rects) -> QImage:
    """RGBA8888 源帧（解码产物的等价形态）：指定区域纯白不透明，其余透明。"""
    img = QImage(width, height, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for (x0, y0, x1, y1) in opaque_rects:
        p.fillRect(x0, y0, x1 - x0, y1 - y0, QColor(255, 255, 255, 255))
    p.end()
    return img


def _make_threshold_frame(width: int = 320, height: int = 180) -> QImage:
    """alpha 阈值帧：x=60 列为 127（不进 mask）、x=61 列为 128（进 mask）。"""
    img = QImage(width, height, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(60, 0, 1, height, QColor(255, 255, 255, 127))
    p.fillRect(61, 0, 1, height, QColor(255, 255, 255, 128))
    p.fillRect(0, 0, 60, height, QColor(255, 255, 255, 255))
    p.end()
    return img


# ================================================================ 运行时扫描基准
class _FrameClip:
    """向 _rebuild_frame 提供帧的假播放器（帧号可控）。"""

    def __init__(self, frame: QImage, frame_number: int = 0) -> None:
        self._frame = frame
        self._frame_number = frame_number

    def currentPixmap(self) -> QPixmap:
        return QPixmap.fromImage(self._frame)

    def currentFrameNumber(self) -> int:
        return self._frame_number

    def jumpToFrame(self, n: int) -> bool:
        self._frame_number = max(0, n)
        return n <= 0

    def stop(self) -> None:
        pass

    def start(self) -> None:
        pass

    def set_playback_speed(self, speed) -> None:
        pass

    def frameCount(self) -> int:
        return 1

    def duration(self) -> float:
        return 1.0

    def currentTimeSeconds(self) -> float:
        return 0.0


class _ScanPet:
    """只挂载真实 _rebuild_frame / _sync_mask / _frame_draw_rect 的假窗口
    （无 bounds 缓存 → 走现有扫描路径，作为预计算函数的运行时基准）。"""

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _sync_mask = window_mod.PetWindow._sync_mask
    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect

    def __init__(self, clip, *, scale: float, dpr: float, facing: str) -> None:
        self.movie = clip
        self.lib = SimpleNamespace(no_mirror=frozenset())
        self.scale = scale
        self.facing = facing
        self.anim = "idle"
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._squash_active = False
        self._squash_progress = 1.0
        self._frame_pixmap = None
        self._hit_alpha_image = None
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._frame_key = None
        self._screen_dpr = dpr

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def mask(self):
        return QRegion(0, 0, 10, 10)

    def clearMask(self) -> None:
        pass


def _scan_bounds(frame: QImage, *, scale: float, dpr: float, facing: str) -> QRect:
    """跑真实产品链（_rebuild_frame → _sync_mask Windows 分支），返回扫描 bounds。"""
    pet = _ScanPet(_FrameClip(frame), scale=scale, dpr=dpr, facing=facing)
    window_mod.PetWindow._rebuild_frame(pet)
    return QRect(pet._mask_bounds)


# ================================================================ 1. 预计算函数逐帧一致
def test_frame_window_bounds_matches_runtime_scan_synthetic(monkeypatch):
    """合成帧：多 scale × DPR × 镜像 × 空帧 × alpha 阈值，预计算与运行时扫描逐帧一致。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    frames = [
        _make_rgba_frame(640, 360, [(60, 30, 260, 150)]),
        _make_rgba_frame(640, 360, []),                              # 空帧
        _make_rgba_frame(640, 360, [(0, 0, 640, 360)]),              # 整幅
        _make_rgba_frame(640, 360, [(300, 100, 640, 360), (10, 200, 100, 300)]),
        _make_threshold_frame(320, 180),                             # 127/128 阈值
        _make_rgba_frame(640, 360, [(319, 179, 320, 180)]),          # 右下单像素
    ]
    checked = 0
    for scale in (0.5, 0.72, 1.0):
        for dpr in (1.0, 2.0):
            for facing in ("left", "right"):
                mirrored = facing == "right"
                for fi, frame in enumerate(frames):
                    expected = _scan_bounds(frame, scale=scale, dpr=dpr, facing=facing)
                    got = bp.frame_window_bounds(
                        frame, mirrored=mirrored, scale=scale, dpr=dpr
                    )
                    checked += 1
                    assert got == expected, (
                        f"scale={scale} dpr={dpr} facing={facing} frame#{fi}: "
                        f"预计算={got} 运行时扫描={expected}"
                    )
    assert checked >= 40, "必须真正覆盖多组合（当前覆盖过少）"


def test_alpha_bounds_threshold_matches_create_alpha_mask():
    """预计算的 alpha>=128 直扫与 createAlphaMask 的 1bpp 掩码阈值一致。"""
    _qapp()
    for rects in ([(0, 0, 60, 30)], [(61, 0, 62, 1)], [], [(0, 0, 640, 360)]):
        img = _make_rgba_frame(640, 360, rects)
        assert bp.alpha_bounds(img) == window_mod._mono_mask_bounds(img), f"rects={rects}"


# ================================================================ 2. _sync_mask 命中缓存免扫描
class _CachedMovie:
    """带预计算 bounds 的假播放器：bounds_rect/bounds_union 直接返回给定矩形。"""

    def __init__(self, rect: QRect, union: QRect | None = None) -> None:
        self._rect = QRect(rect)
        self._union = QRect(union) if union is not None else QRect(rect)
        self.lookups = 0

    def bounds_rect(self, mirrored, scale, dpr, frame_n):
        self.lookups += 1
        return QRect(self._rect)

    def bounds_union(self, mirrored, scale, dpr):
        return QRect(self._union)

    def currentFrameNumber(self) -> int:
        return 3


class _CachedPet:
    """命中缓存路径的假窗口（movie 提供 bounds_rect/bounds_union）。"""

    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect

    def __init__(self, movie) -> None:
        self.movie = movie
        self.lib = SimpleNamespace(no_mirror=frozenset())
        self.scale = 0.72
        self.anim = "idle"
        self.facing = "left"
        self._w = max(1, int(round(catalog.CANVAS_W * 0.72)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * 0.72)))
        self._squash_active = False
        self._squash_progress = 1.0
        self._frame_pixmap = QPixmap(10, 10)
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._screen_dpr = 1.0
        self._cleared = 0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def mask(self):
        return QRegion(0, 0, 10, 10)

    def clearMask(self) -> None:
        self._cleared += 1


def test_sync_mask_uses_cached_bounds_without_scan(monkeypatch):
    """Windows 命中缓存：不触发画布扫描（_mono_mask_bounds 被禁止调用），
    _mask_bounds 取缓存值，_collision_local_bounds 取预计算 union。"""
    _qapp()
    rect = QRect(20, 30, 40, 50)
    union = QRect(10, 20, 60, 70)
    pet = _CachedPet(_CachedMovie(rect, union))
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    def _forbid(*_a, **_k):
        raise AssertionError("命中预计算缓存时不得再走逐帧扫描")

    monkeypatch.setattr(window_mod, "_mono_mask_bounds", _forbid)
    window_mod.PetWindow._sync_mask(pet)

    assert pet._mask_bounds == rect
    assert pet._collision_local_bounds == union, "首帧即用预计算 union（稳定碰撞体）"
    assert pet.movie.lookups == 1
    assert pet._cleared == 1, "Windows 上旧 mask 仍需清理"


def test_sync_mask_cached_union_stays_stable_across_frames(monkeypatch):
    """命中缓存时 _collision_local_bounds 保持稳定 union：帧 bounds 变化不改变 union。"""
    _qapp()
    movie = _CachedMovie(QRect(20, 30, 40, 50), QRect(10, 20, 60, 70))
    pet = _CachedPet(movie)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得扫描")))

    window_mod.PetWindow._sync_mask(pet)
    first_union = QRect(pet._collision_local_bounds)
    assert first_union == QRect(10, 20, 60, 70)

    # 下一帧 bounds 变化（仍在 union 内）→ union 不变
    movie._rect = QRect(15, 25, 30, 40)
    window_mod.PetWindow._sync_mask(pet)
    assert pet._collision_local_bounds == first_union
    assert pet._collision_local_bounds.contains(pet._mask_bounds)


def test_sync_mask_falls_back_to_scan_when_no_cache(monkeypatch):
    """无预计算（movie 没有 bounds_rect）→ 回落到现有逐帧扫描，bounds 与基准一致。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    frame = _make_rgba_frame(640, 360, [(60, 30, 260, 150)])
    pet = _ScanPet(_FrameClip(frame), scale=0.72, dpr=1.0, facing="left")
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._mask_bounds is not None and not pet._mask_bounds.isEmpty()
    expected = _scan_bounds(frame, scale=0.72, dpr=1.0, facing="left")
    assert pet._mask_bounds == expected
    assert pet._collision_local_bounds == expected


def test_sync_mask_falls_back_when_cache_miss(monkeypatch):
    """bounds_rect 返回 None（该帧未预计算）→ 回落到现有逐帧扫描。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    class _MissMovie(_CachedMovie):
        def bounds_rect(self, mirrored, scale, dpr, frame_n):
            self.lookups += 1
            return None

    pet = _CachedPet(_MissMovie(QRect(20, 30, 40, 50)))
    scan_calls = []
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda img: (scan_calls.append(img), None)[1])
    window_mod.PetWindow._sync_mask(pet)
    assert scan_calls, "缓存未命中必须回到扫描"
    assert pet._mask_bounds is not None


def test_sync_mask_falls_back_when_squash_active(monkeypatch):
    """squash 变形改变绘制矩形 → 预计算 bounds 不适用，强制回落扫描。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    pet = _CachedPet(_CachedMovie(QRect(20, 30, 40, 50)))
    pet._squash_active = True
    scan_calls = []
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda img: (scan_calls.append(img), None)[1])
    window_mod.PetWindow._sync_mask(pet)
    assert scan_calls, "squash 期间必须扫描（几何与缓存不符）"
    assert pet.movie.lookups == 0, "squash 期间不得查询缓存"


# ================================================================ 3. 碰撞 union 语义
def test_collision_local_bounds_switch_anim_no_cross_contamination(monkeypatch):
    """切换动画后 _collision_local_bounds 从新动画的缓存重新取 union，旧动画
    的 union 不串味（缓存按 clip 归属）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得扫描")))

    pet = _CachedPet(_CachedMovie(QRect(20, 30, 40, 50), QRect(10, 20, 60, 70)))
    window_mod.PetWindow._sync_mask(pet)
    anim_a_union = QRect(pet._collision_local_bounds)

    # 切换到另一个动画（不同 clip 对象、不同 union）
    pet.anim = "other"
    pet.movie = _CachedMovie(QRect(100, 100, 30, 30), QRect(90, 90, 50, 50))
    pet._collision_local_bounds = None  # _switch 的重置语义
    window_mod.PetWindow._sync_mask(pet)
    assert pet._collision_local_bounds == QRect(90, 90, 50, 50)
    assert pet._collision_local_bounds != anim_a_union, "旧动画 union 不得残留"

    # 切回旧动画 → 旧 union 仍在（缓存未丢）
    pet.anim = "idle"
    pet.movie = _CachedMovie(QRect(20, 30, 40, 50), QRect(10, 20, 60, 70))
    pet._collision_local_bounds = None
    window_mod.PetWindow._sync_mask(pet)
    assert pet._collision_local_bounds == anim_a_union


def test_clip_bounds_cache_isolated_between_clips():
    """不同 clip（不同角色/动画）的 bounds 缓存互不影响。"""
    _qapp()
    clip_a = WebMClip("dummy-a.webm")
    clip_b = WebMClip("dummy-b.webm")
    clip_a._bounds_cache[(False, 0.72, 1.0)] = bp.AnimBounds(
        1, bp.empty_flat(1), QRect(1, 2, 3, 4), QPoint(2, 5), False
    )
    assert clip_a.bounds_union(False, 0.72, 1.0) == QRect(1, 2, 3, 4)
    assert clip_b.bounds_union(False, 0.72, 1.0) is None, "不同 clip 缓存必须隔离"
    assert clip_a.bounds_union(True, 0.72, 1.0) is None, "不同模式键必须隔离"
    clip_a.cleanup()
    clip_b.cleanup()


# ================================================================ 4. warm_bounds 生命周期
class _FakeDecodeProc:
    """模拟 bounds 解码拉起的 ffmpeg 进程句柄：terminate 不退出、kill 才退出。"""

    def __init__(self):
        self._dead = False
        self.terminated = False
        self.killed = False
        self.pid = id(self)

    def poll(self):
        return None if not self._dead else 1

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._dead = True

    def wait(self, timeout=None):
        if self._dead:
            return 1
        import subprocess
        raise subprocess.TimeoutExpired(self, timeout)


class _FakePopenCapture:
    """替换 _PopenCapture：单例，捕获 on_process 登记回调，测试可精确控制
    「进程已创建、登记尚未完成」等窗口（复用 N4 测试的假 capture）。"""

    _instance = None
    current = None

    def __new__(cls, on_process=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.entered = threading.Event()
            cls._instance.proceed = threading.Event()
        return cls._instance

    def __init__(self, on_process=None):
        self.on_process = on_process
        _FakePopenCapture.current = self
        self.entered.clear()
        self.proceed.clear()

    def __enter__(self):
        self.entered.set()
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _install_fake_decode(monkeypatch, clip, frames, proc=None,
                         block: bool = False, entered=None):
    """安装假 capture/read_frames；frames 为 RGBA 帧 bytes 列表。

    block=True 时 read_frames 进入后先登记进程、置位 entered，然后阻塞在
    proceed 上（模拟在飞解码，供取消/并发认领测试控制时序）；放行后才
    yield 帧。
    """
    meta = {"size": (clip._w, clip._h), "fps": 24.0, "duration": 1.0}
    cap = _FakePopenCapture()
    proc = proc or _FakeDecodeProc()

    def _read_frames(*args, **kwargs):
        if cap.on_process is not None:
            cap.on_process(proc, ["ffmpeg", "-i", "dummy.webm"])
        if entered is not None:
            entered.set()
        if block:
            cap.proceed.wait(5.0)
        yield meta
        for chunk in frames:
            yield chunk

    monkeypatch.setattr(webm_clip_mod, "_PopenCapture", _FakePopenCapture)
    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", _read_frames)
    return cap, proc


def _frame_bytes(rects) -> bytes:
    img = _make_rgba_frame(640, 360, rects)
    return bytes(img.constBits())


def test_warm_bounds_atomic_claim_single_executor(monkeypatch):
    """同一时间只有一个 bounds 解码执行者：并发 warm_bounds 认领失败立即放弃。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    entered = threading.Event()
    _install_fake_decode(
        monkeypatch, clip,
        [_frame_bytes([(60, 30, 260, 150)])] * 3,
        block=True, entered=entered,
    )

    t = threading.Thread(target=lambda: clip.warm_bounds(False, 0.72, 1.0), daemon=True)
    t.start()
    assert entered.wait(5.0), "第一个执行者必须已进入解码"

    clip.warm_bounds(False, 0.72, 1.0)  # 第二个并发 warm：认领失败 → 不进入解码
    assert clip.bounds_union(False, 0.72, 1.0) is None, "解码未完成不得有结果"

    _FakePopenCapture.current.proceed.set()
    t.join(5.0)
    data = clip.bounds_data(False, 0.72, 1.0)
    assert data is not None, "后台解码完成应整包提交"
    assert data.frame_count == 3
    assert data.frame_rect(0) is not None and not data.frame_rect(0).isEmpty()
    assert data.frame_rect(1) is not None
    assert data.frame_rect(2) is not None
    assert data.frame_rect(3) is None, "越界帧返回 None（调用方回落扫描）"
    assert data.union.contains(data.frame_rect(0))
    assert data.union.contains(data.frame_rect(1))
    clip.cleanup()


def test_warm_bounds_empty_frame_and_union(monkeypatch):
    """空帧存空矩形（与扫描一致）、union 为全部非空帧的并集、脚底锚点正确。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    frames = [
        _frame_bytes([(60, 30, 260, 150)]),
        _frame_bytes([]),                       # 空帧
        _frame_bytes([(10, 200, 100, 300)]),
    ]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)

    data = clip.bounds_data(False, 0.72, 1.0)
    assert data is not None
    assert data.frame_rect(1).isEmpty(), "空帧必须返回空矩形（与扫描一致）"
    u = data.union
    assert u.contains(data.frame_rect(0))
    assert u.contains(data.frame_rect(2))
    assert u.top() == min(data.frame_rect(0).top(), data.frame_rect(2).top())
    assert u.bottom() == max(data.frame_rect(0).bottom(), data.frame_rect(2).bottom())
    assert data.feet.x() == u.center().x()
    assert data.feet.y() == u.bottom()
    clip.cleanup()


def test_cancel_bounds_warm_bumps_generation_and_terminates_procs(monkeypatch):
    """cancel_bounds_warm 必须换代（在飞结果作废）并 terminate 登记的进程。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    proc = _FakeDecodeProc()
    entered = threading.Event()
    _install_fake_decode(
        monkeypatch, clip, [_frame_bytes([(60, 30, 260, 150)])],
        proc=proc, block=True, entered=entered,
    )

    t = threading.Thread(target=lambda: clip.warm_bounds(False, 0.72, 1.0), daemon=True)
    t.start()
    assert entered.wait(5.0), "解码必须已进入"

    gen_before = clip._bounds_gen
    clip.cancel_bounds_warm()
    assert clip._bounds_gen == gen_before + 1
    assert proc.terminated or proc.killed, "取消必须 terminate 在飞解码进程"
    assert clip._bounds_procs == set(), "取消后登记集合必须清空"

    _FakePopenCapture.current.proceed.set()
    t.join(5.0)
    assert clip.bounds_union(False, 0.72, 1.0) is None, "被取消的结果不得提交"
    clip.cleanup()


def test_cleanup_cancels_bounds_warm_and_discards_result(monkeypatch):
    """cleanup 取消在飞 bounds 解码；被取消的结果不得写入缓存。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    entered = threading.Event()
    _install_fake_decode(
        monkeypatch, clip, [_frame_bytes([(60, 30, 260, 150)])],
        block=True, entered=entered,
    )

    t = threading.Thread(target=lambda: clip.warm_bounds(False, 0.72, 1.0), daemon=True)
    t.start()
    assert entered.wait(5.0), "解码必须已进入"

    clip.cleanup()  # 取消在飞解码（换代 + terminate）
    _FakePopenCapture.current.proceed.set()
    t.join(5.0)

    assert clip.bounds_union(False, 0.72, 1.0) is None, "cleanup 后结果不得提交"
    assert clip._cleaned is True
    assert clip.bounds_rect(False, 0.72, 1.0, 0) is None


def test_warm_bounds_skips_already_computed_and_cleaned(monkeypatch):
    """已预计算的键直接跳过（不重复解码）；cleanup 后不再预热。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)])]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    assert clip.bounds_union(False, 0.72, 1.0) is not None

    # 再次 warm 同键：应立即返回（无新解码）
    clip.warm_bounds(False, 0.72, 1.0)
    clip.cleanup()
    # cleanup 后 warm 是 no-op（不拉起解码）
    clip.warm_bounds(True, 0.72, 1.0)
    assert clip.bounds_union(True, 0.72, 1.0) is None


def test_warm_bounds_stores_has_text_flag(monkeypatch):
    """has_text 标记随键存储。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    _install_fake_decode(monkeypatch, clip, [_frame_bytes([(60, 30, 260, 150)])])
    clip.warm_bounds(False, 0.72, 1.0, has_text=True)
    assert clip.bounds_data(False, 0.72, 1.0).has_text is True
    clip.cleanup()


# ================================================================ 5. 真实 webm 集成 + 内存
VIDEOS_ROOT = (
    Path(__file__).resolve().parent.parent
    / "assets" / "characters" / "shenshen" / "videos"
)


def _decode_frames(path: Path) -> list[QImage]:
    import imageio_ffmpeg

    gen = imageio_ffmpeg.read_frames(
        str(path),
        pix_fmt="rgba",
        bits_per_pixel=32,
        input_params=["-c:v", "libvpx-vp9"],
    )
    meta = next(gen)
    w, h = meta.get("size") or (catalog.CANVAS_W, catalog.CANVAS_H)
    out: list[QImage] = []
    for data in gen:
        img = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        if not img.isNull():
            out.append(img.copy())
    return out


def _sample_indices(n: int, min_samples: int = 12) -> list[int]:
    if n <= min_samples:
        return list(range(n))
    idxs = {0, n - 1} | {round((n - 1) * k / (min_samples - 1)) for k in range(1, min_samples - 1)}
    return sorted(i for i in idxs if 0 <= i < n)


def test_warm_bounds_real_webm_matches_runtime_scan(monkeypatch):
    """真实 webm：warm_bounds 全帧预计算后，采样帧的缓存 bounds 与运行时
    扫描逐帧完全一致（多 scale × DPR × 镜像）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    files = sorted(VIDEOS_ROOT.rglob("*.webm"))
    if not files:
        pytest.skip("缺少真实 webm 素材目录")
    path = files[0]
    clip = WebMClip(path)
    try:
        combos = [(0.72, 1.0), (0.5, 2.0)]
        for mirrored in (False, True):
            for scale, dpr in combos:
                clip.warm_bounds(mirrored, scale, dpr)
        frames = _decode_frames(path)
        assert len(frames) > 0
        checked = 0
        for fi in _sample_indices(len(frames)):
            for mirrored in (False, True):
                for scale, dpr in combos:
                    cached = clip.bounds_rect(mirrored, scale, dpr, fi)
                    assert cached is not None, f"frame#{fi} 未预计算"
                    expected = bp.frame_window_bounds(
                        frames[fi], mirrored=mirrored, scale=scale, dpr=dpr
                    )
                    checked += 1
                    assert cached == expected, (
                        f"frame#{fi} mirrored={mirrored} scale={scale} dpr={dpr}: "
                        f"缓存={cached} 运行时={expected}"
                    )
        assert checked >= 40, "真实素材覆盖帧数不足"
    finally:
        clip.cleanup()


def test_bounds_memory_bounded(monkeypatch):
    """内存可控：每动画每模式存储 = 帧数×4×int16（241 帧 ≈ 1.9KB），
    不得出现每帧大对象（QRect 列表等）。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)])] * 60
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    data = clip.bounds_data(False, 0.72, 1.0)
    assert data is not None
    assert len(data.flat) == 60 * 4, "每帧恰 4 个 int16"
    assert data.memory_bytes() <= 60 * 4 * 2, "每帧 4×int16（8 字节）"
    # 真实动画 241 帧 ≈ 1.9KB/动画/模式；97 动画 × 2 模式 ≈ 0.4MB，可控
    assert 241 * 4 * 2 == 1928
    clip.cleanup()
