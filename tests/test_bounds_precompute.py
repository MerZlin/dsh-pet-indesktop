# -*- coding: utf-8 -*-
"""B14：动画 bounds 预计算 —— 每动画 union 可见 bounds / 每帧 bounds / 脚底锚点。

锁定的行为契约：
1. 预计算函数 bounds_precompute.frame_window_bounds 与运行时
   _rebuild_frame → _sync_mask（Windows 分支画布扫描）逐帧完全一致：
   合成帧（含空帧、alpha 127/128 阈值、镜像）+ 真实 webm 素材，多 scale × DPR；
2. PetWindow._sync_mask Windows 分支命中预计算缓存时免去 O(像素) 扫描，
   且 _mask_bounds 与扫描路径完全一致；未命中/无缓存/squash 几何变化时
   回落到现有逐帧扫描（行为不变）；
3. 帧号契约（B14 复审 P0）：显示帧索引（0 基，== 预计算表索引）与事件序号
   （frameChanged 信号值）分离——真实播放时序下逐帧查到当前帧 bounds，
   首帧缓存/手动 jumpToFrame(0) 路径一致，最后一帧不走回落；
4. 碰撞 union 时机/几何与旧扫描路径逐帧等价（B14 复审 P1）：缓存命中只
   替代「每帧 bounds 怎么算」，绝不改变「什么时候 union、union 哪些」——
   首帧从当前帧开始累积，之后逐帧 united 已显示帧；空帧不改变 union；
5. bounds 预热线程安全（B14 复审 P1）：GUI 线程采集纯数据快照（路径/尺寸/
   bpp/scale/dpr/代次），worker 只用纯数据 + 独立解码，不触碰任何 Qt 对象；
   结果由 GUI 线程按代次原子提交（代次确认 + 缓存写入同一把锁，取消后绝不
   提交）；隐藏/关闭经停止事件 + terminate 已登记进程回收；
6. 完成判定按目标键验证结果已提交（clip 已创建 ≠ 任务已执行 ≠ 结果已提交）；
   (scale, dpr) 键变化时重置依赖几何的碰撞状态；
7. WebMClip.warm_bounds 复用首帧解码的 锁 + 代次 + 进程登记 生命周期：
   原子认领（并发只一个执行者）、取消换代并 terminate 在飞解码进程、
   cleanup 取消、结果整包原子提交（绝不提交半成品 union）；
8. 内存可控：每动画每模式存储 ~2KB（241 帧 × 4×int16）。
"""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
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
    """带预计算 bounds 的假播放器：bounds_rect 直接返回给定矩形；bounds_union
    返回全动画 union——运行时必须不使用它初始化碰撞（B14 复审 P1）。"""

    def __init__(self, rect: QRect, union: QRect | None = None) -> None:
        self._rect = QRect(rect)
        self._union = QRect(union) if union is not None else QRect(rect)
        self.lookups = 0
        self.union_lookups = 0

    def bounds_rect(self, mirrored, scale, dpr, frame_n):
        self.lookups += 1
        return QRect(self._rect)

    def bounds_union(self, mirrored, scale, dpr):
        self.union_lookups += 1
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
    _mask_bounds 取缓存值；碰撞 union 从当前帧开始累积（与旧扫描路径逐帧
    等价，绝不提前使用全动画 union——B14 复审 P1）。"""
    _qapp()
    rect = QRect(20, 30, 40, 50)
    union = QRect(10, 20, 60, 70)  # 全动画 union（更大）：不得在首帧直接使用
    pet = _CachedPet(_CachedMovie(rect, union))
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    def _forbid(*_a, **_k):
        raise AssertionError("命中预计算缓存时不得再走逐帧扫描")

    monkeypatch.setattr(window_mod, "_mono_mask_bounds", _forbid)
    window_mod.PetWindow._sync_mask(pet)

    assert pet._mask_bounds == rect
    assert pet._collision_local_bounds == rect, "首帧从当前帧 bounds 开始累积（非全动画 union）"
    assert pet.movie.union_lookups == 0, "碰撞初始化不得查询全动画 union（时机与扫描等价）"
    assert pet.movie.lookups == 1
    assert pet._cleared == 1, "Windows 上旧 mask 仍需清理"


class _FrameSequenceMovie:
    """bounds_rect 按当前帧号返回逐帧矩形的假播放器（帧号可控，模拟真实
    WebMClip 时序：currentFrameNumber 即预计算表索引，0 基）；bounds_union
    返回全动画 union——运行时不得使用它初始化碰撞。"""

    def __init__(self, rects) -> None:
        self._rects = [QRect(r) for r in rects]
        self._frame_number = 0
        self.lookups = 0
        self.union_lookups = 0

    def currentFrameNumber(self) -> int:
        return self._frame_number

    def bounds_rect(self, mirrored, scale, dpr, frame_n):
        self.lookups += 1
        if frame_n is None or frame_n < 0 or frame_n >= len(self._rects):
            return None
        return QRect(self._rects[frame_n])

    def bounds_union(self, mirrored, scale, dpr):
        self.union_lookups += 1
        return QRect(0, 0, 200, 200)


def test_sync_mask_collision_accumulates_per_frame(monkeypatch):
    """命中缓存时碰撞 union 逐帧累积（首帧=当前帧，之后 united 已显示帧），
    空帧不改变 union——与旧扫描路径时机/几何逐帧等价（B14 复审 P1）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得扫描")))

    movie = _FrameSequenceMovie([
        QRect(20, 30, 40, 50),
        QRect(10, 40, 80, 30),
        QRect(),                      # 空帧
        QRect(60, 10, 20, 90),
    ])
    pet = _CachedPet(movie)
    seq = []
    for fi in range(len(movie._rects)):
        movie._frame_number = fi
        window_mod.PetWindow._sync_mask(pet)
        seq.append(QRect(pet._collision_local_bounds))
    assert seq[0] == QRect(20, 30, 40, 50), "首帧从当前帧 bounds 开始累积"
    assert seq[1] == QRect(10, 30, 80, 50), "第二帧 united 已显示帧（时机与扫描一致）"
    assert seq[2] == QRect(10, 30, 80, 50), "空帧不改变 union（与画布扫描一致）"
    assert seq[3] == QRect(10, 10, 80, 90), "后续帧继续累积"
    assert pet._mask_bounds == QRect(60, 10, 20, 90)
    assert movie.union_lookups == 0, "碰撞初始化不得查询全动画 union（B14 复审 P1）"


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
    """切换动画后 _collision_local_bounds 从新动画首帧重新累积，旧动画的
    union 不串味（缓存按 clip 归属、重置语义不变）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不得扫描")))

    pet = _CachedPet(_CachedMovie(QRect(20, 30, 40, 50), QRect(10, 20, 60, 70)))
    window_mod.PetWindow._sync_mask(pet)
    anim_a_bounds = QRect(pet._collision_local_bounds)
    assert anim_a_bounds == QRect(20, 30, 40, 50)
    assert pet.movie.union_lookups == 0, "切换/首帧碰撞初始化不得使用全动画 union"

    # 切换到另一个动画（不同 clip 对象）：重置后从新动画首帧重新累积
    pet.anim = "other"
    pet.movie = _CachedMovie(QRect(100, 100, 30, 30), QRect(90, 90, 50, 50))
    pet._collision_local_bounds = None  # _switch 的重置语义
    window_mod.PetWindow._sync_mask(pet)
    assert pet._collision_local_bounds == QRect(100, 100, 30, 30)
    assert pet._collision_local_bounds != anim_a_bounds, "旧动画 union 不得残留"

    # 切回旧动画 → 从旧动画首帧重新累积（缓存未丢）
    pet.anim = "idle"
    pet.movie = _CachedMovie(QRect(20, 30, 40, 50), QRect(10, 20, 60, 70))
    pet._collision_local_bounds = None
    window_mod.PetWindow._sync_mask(pet)
    assert pet._collision_local_bounds == anim_a_bounds


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


# ================================================================ B14 复审：帧号契约（P0）
def test_bounds_lookup_follows_real_playback_frames(monkeypatch):
    """真实播放时序（P0 回归）：解码三帧 A/B/C，逐帧断言查到的 bounds 属于
    当前显示帧（首帧=A、次帧=B、末帧=C），最后一帧不走回落——显示帧索引
    （0 基）与预计算表索引统一（旧实现第一帧查第二帧、末帧越界回落）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    clip = WebMClip("dummy.webm")
    rects = [(60, 30, 260, 150), (300, 100, 640, 360), (10, 200, 100, 300)]
    frames = [_frame_bytes([r]) for r in rects]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    data = clip.bounds_data(False, 0.72, 1.0)
    assert data is not None and data.frame_count == 3

    def _img(fb):
        return QImage(fb, 640, 360, 640 * 4, QImage.Format.Format_RGBA8888)

    expected = [
        bp.frame_window_bounds(_img(frames[0]), mirrored=False, scale=0.72, dpr=1.0),
        bp.frame_window_bounds(_img(frames[1]), mirrored=False, scale=0.72, dpr=1.0),
        bp.frame_window_bounds(_img(frames[2]), mirrored=False, scale=0.72, dpr=1.0),
    ]
    assert all(not r.isEmpty() for r in expected)

    scan_calls = []
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda img: (scan_calls.append(img), None)[1])
    pet = _CachedPet(clip)
    for fi in range(3):
        clip._process_frame(frames[fi])  # 真实播放推进：存帧 → 帧号推进 → 信号
        window_mod.PetWindow._sync_mask(pet)
        assert pet._mask_bounds == expected[fi], (
            f"第 {fi} 帧显示时查到的 bounds 必须属于当前帧（不得错位/越界回落）"
        )
    assert not scan_calls, "最后一帧不得走回落扫描（预计算必须命中）"
    clip.cleanup()


def test_bounds_lookup_first_frame_after_jump(monkeypatch):
    """手动 jumpToFrame(0) / 首帧缓存路径（P0 回归）：显示帧索引 0 基，查表
    命中首帧 bounds 而不是 -1 越界回落（B14 复审 P0：首帧缓存/手动跳转路径
    与播放路径共用同一帧号契约）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    clip = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)]), _frame_bytes([(300, 100, 640, 360)])]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    expected0 = bp.frame_window_bounds(
        QImage(frames[0], 640, 360, 640 * 4, QImage.Format.Format_RGBA8888),
        mirrored=False, scale=0.72, dpr=1.0,
    )
    scan_calls = []
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda img: (scan_calls.append(img), None)[1])
    pet = _CachedPet(clip)
    clip.jumpToFrame(0)
    assert clip.currentFrameNumber() == 0, "跳回首帧后显示帧索引必须为 0（0 基）"
    window_mod.PetWindow._sync_mask(pet)
    assert pet._mask_bounds == expected0, "首帧缓存/手动跳转路径必须命中首帧 bounds"
    assert not scan_calls, "首帧路径不得走回落扫描"
    clip.cleanup()


# ================================================================ B14 复审：碰撞 union 逐帧等价（P1）
class _NoBoundsClip:
    """去掉 bounds API 的 clip 包装：强制 _sync_mask 走逐帧扫描（旧路径基准）。"""

    def __init__(self, clip) -> None:
        self._clip = clip

    def currentPixmap(self):
        return self._clip.currentPixmap()

    def currentFrameNumber(self) -> int:
        return self._clip.currentFrameNumber()


def test_collision_accumulation_cached_equals_scan(monkeypatch):
    """碰撞 union 时机/几何与旧扫描路径逐帧等价（差分，B14 复审 P1）：同一帧
    序列分别走「缓存命中（真实 WebMClip._process_frame 时序）」与「逐帧扫描
    （无 bounds API）」，累积的 _collision_local_bounds 序列完全一致。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    frames = [
        _frame_bytes([(60, 30, 260, 150)]),
        _frame_bytes([(300, 100, 640, 360)]),
        _frame_bytes([]),                       # 空帧
        _frame_bytes([(10, 200, 100, 300)]),
    ]

    orig_mono = window_mod._mono_mask_bounds
    scan_calls = []

    def _rec_mono(img):
        scan_calls.append(1)
        return orig_mono(img)

    monkeypatch.setattr(window_mod, "_mono_mask_bounds", _rec_mono)

    # 扫描侧：独立 clip + 无 bounds API → 旧逐帧扫描路径（基准）
    scan_clip = WebMClip("dummy-scan.webm")
    scan_pet = _ScanPet(_NoBoundsClip(scan_clip), scale=0.72, dpr=1.0, facing="left")
    scan_seq = []
    for fi in range(4):
        scan_clip._process_frame(frames[fi])
        window_mod.PetWindow._rebuild_frame(scan_pet)
        scan_seq.append(QRect(scan_pet._collision_local_bounds))
    assert scan_calls, "扫描侧必须真的走了逐帧扫描"

    # 缓存侧：独立 clip（预计算命中）+ 真实 _process_frame 时序
    cached_clip = WebMClip("dummy-cached.webm")
    _install_fake_decode(monkeypatch, cached_clip, frames)
    cached_clip.warm_bounds(False, 0.72, 1.0)
    cached_pet = _CachedPet(cached_clip)
    cached_seq = []
    for fi in range(4):
        cached_clip._process_frame(frames[fi])
        window_mod.PetWindow._sync_mask(cached_pet)
        cached_seq.append(QRect(cached_pet._collision_local_bounds))

    assert cached_seq == scan_seq, (
        f"缓存命中与逐帧扫描的碰撞累积必须逐帧一致: 缓存={cached_seq} 扫描={scan_seq}"
    )
    scan_clip.cleanup()
    cached_clip.cleanup()


# ================================================================ B14 复审：线程安全（P1）
def test_bounds_warm_snapshot_is_pure_data(monkeypatch):
    """GUI 线程采集的 bounds 任务快照只含普通值（路径/尺寸/bpp/镜像/scale/
    dpr/代次），不持有 clip/QObject 引用；快照代次在取消后提交被拒。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    _install_fake_decode(monkeypatch, clip, [_frame_bytes([(60, 30, 260, 150)])])
    snap = clip.bounds_warm_snapshot(False, 0.72, 1.0, False)
    assert snap is not None
    assert snap["path"] == "dummy.webm"
    assert (snap["w"], snap["h"], snap["bpp"]) == (640, 360, 4)
    assert snap["key"] == (False, 0.72, 1.0)
    assert snap["mirrored"] is False
    assert snap["has_text"] is False
    assert snap["gen"] == clip._bounds_gen

    data = bp.AnimBounds(1, bp.empty_flat(1), QRect(1, 2, 3, 4), QPoint(2, 5), False)
    clip.cancel_bounds_warm()  # 换代：快照代次失效
    assert clip.bounds_warm_commit(snap["key"], data, snap["gen"]) is False, \
        "取消后旧代次不得提交"
    assert clip.bounds_data(False, 0.72, 1.0) is None

    # 已缓存键不再出快照（不重复解码）
    _install_fake_decode(monkeypatch, clip, [_frame_bytes([(60, 30, 260, 150)])])
    clip.warm_bounds(False, 0.72, 1.0)
    assert clip.bounds_union(False, 0.72, 1.0) is not None
    assert clip.bounds_warm_snapshot(False, 0.72, 1.0, False) is None
    clip.cleanup()


def test_bounds_warm_worker_pure_data_and_stop(monkeypatch):
    """bounds worker 只用纯数据快照 + 独立解码（不触碰任何 Qt 对象）：结果
    整包入队、stop 事件后不再产出、GUI 线程按代次提交（B14 复审 P1）。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)]), _frame_bytes([(300, 100, 640, 360)])]
    _install_fake_decode(monkeypatch, clip, frames)
    snap = clip.bounds_warm_snapshot(False, 0.72, 1.0, False)
    assert snap is not None
    snap = dict(snap, name="idle")
    stop_evt = threading.Event()
    results = queue.Queue()
    procs, procs_lock = set(), threading.Lock()

    window_mod._run_bounds_warm_tasks(
        {"scale": 0.72, "dpr": 1.0, "tasks": [snap]},
        stop_evt, results, procs, procs_lock,
    )
    assert results.qsize() == 1
    name, key, data, gen = results.get_nowait()
    assert (name, key) == ("idle", (False, 0.72, 1.0))
    assert data.frame_count == 2
    # GUI 线程提交（代次匹配 → 成功）
    assert clip.bounds_warm_commit(key, data, gen) is True
    assert clip.bounds_union(False, 0.72, 1.0) is not None

    # stop 事件置位后：worker 不再执行任务（无结果入队）
    snap2 = clip.bounds_warm_snapshot(True, 0.72, 1.0, False)
    assert snap2 is not None
    stop_evt.set()
    window_mod._run_bounds_warm_tasks(
        {"scale": 0.72, "dpr": 1.0, "tasks": [dict(snap2, name="idle")]},
        stop_evt, results, procs, procs_lock,
    )
    assert results.qsize() == 0, "stop 后不得再产出结果"
    clip.cleanup()


def test_bounds_warm_worker_aborts_mid_decode(monkeypatch):
    """解码中途 stop：worker 逐帧放弃，结果不得入队（隐藏/切角色零后台
    残留；B14 复审 P1：worker 有明确停止协议）。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    entered = threading.Event()
    _install_fake_decode(
        monkeypatch, clip,
        [_frame_bytes([(60, 30, 260, 150)])] * 3,
        block=True, entered=entered,
    )
    snap = clip.bounds_warm_snapshot(False, 0.72, 1.0, False)
    assert snap is not None
    stop_evt = threading.Event()
    results = queue.Queue()
    procs, procs_lock = set(), threading.Lock()
    t = threading.Thread(
        target=window_mod._run_bounds_warm_tasks,
        args=({"scale": 0.72, "dpr": 1.0, "tasks": [dict(snap, name="idle")]},
              stop_evt, results, procs, procs_lock),
        daemon=True,
    )
    t.start()
    assert entered.wait(5.0), "解码必须已进入"
    stop_evt.set()
    _FakePopenCapture.current.proceed.set()
    t.join(5.0)
    assert results.qsize() == 0, "中途 stop 的解码结果不得入队"
    assert clip.bounds_union(False, 0.72, 1.0) is None
    clip.cleanup()


def test_cancel_bounds_warm_blocks_stale_commit():
    """取消（换代）与缓存提交同一同步协议（B14 复审 P1）：携带旧代次的提交
    必须被拒，取消后绝不提交旧结果；代次匹配且未 cleanup 时提交成功。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    data = bp.AnimBounds(1, bp.empty_flat(1), QRect(1, 2, 3, 4), QPoint(2, 5), False)
    gen = clip._bounds_gen
    clip.cancel_bounds_warm()
    assert clip._bounds_gen == gen + 1
    assert clip.bounds_warm_commit((False, 0.72, 1.0), data, gen) is False
    assert clip.bounds_data(False, 0.72, 1.0) is None, "取消后旧代次结果不得提交"

    assert clip.bounds_warm_commit((False, 0.72, 1.0), data, clip._bounds_gen) is True
    assert clip.bounds_data(False, 0.72, 1.0) is not None

    clip.cleanup()
    assert clip.bounds_warm_commit((True, 0.72, 1.0), data, clip._bounds_gen) is False, \
        "cleanup 后不得提交"


def test_bounds_warm_stop_sets_event_and_terminates_procs():
    """hide/closeEvent 停止 worker：置停止事件 + terminate 已登记解码进程 +
    复位在飞标志（隐藏/切角色零后台 ffmpeg，恢复显示可立即重启；B14 复审
    P1：worker 有明确终止协议）。"""
    _qapp()
    proc = _FakeDecodeProc()
    pet = SimpleNamespace(
        _bounds_warm_stop_evt=threading.Event(),
        _bounds_warm_procs={proc},
        _bounds_warm_procs_lock=threading.Lock(),
        _bounds_warm_timer=QTimer(),
        _bounds_warm_running=True,
        _bounds_warm_thread=object(),
    )
    window_mod.PetWindow._bounds_warm_stop(pet)
    assert pet._bounds_warm_stop_evt.is_set(), "停止事件必须置位"
    assert proc.terminated or proc.killed, "已登记解码进程必须 terminate"
    assert pet._bounds_warm_procs == set(), "登记集合必须清空"
    assert not pet._bounds_warm_timer.isActive()
    assert pet._bounds_warm_running is False, "在飞标志必须复位（恢复显示可重启）"
    assert pet._bounds_warm_thread is None


class _WarmLib:
    def __init__(self, clips, no_mirror=frozenset()) -> None:
        self._clips = clips
        self.no_mirror = no_mirror

    def movies(self):
        return dict(self._clips)


class _WarmPet:
    """只挂载 _bounds_warm_order / _bounds_warm_complete 的假窗口（GUI 线程）。"""

    _bounds_warm_order = window_mod.PetWindow._bounds_warm_order
    _bounds_warm_complete = window_mod.PetWindow._bounds_warm_complete

    def __init__(self, lib, cats) -> None:
        self.lib = lib
        self.cats = cats
        self.scale = 0.72
        self._screen_dpr = 1.0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: 1.0)


class _WarmWindow:
    """绑定真实预热驱动方法的最小假窗口：start → 采集快照 → worker 独立解码
    → GUI 轮询提交 → 完成；stop → 重启（B14 复审 P1 全链路）。"""

    _maybe_start_bounds_warm = window_mod.PetWindow._maybe_start_bounds_warm
    _collect_bounds_snapshot = window_mod.PetWindow._collect_bounds_snapshot
    _on_bounds_warm_poll = window_mod.PetWindow._on_bounds_warm_poll
    _bounds_warm_stop = window_mod.PetWindow._bounds_warm_stop
    _bounds_warm_order = window_mod.PetWindow._bounds_warm_order
    _bounds_warm_complete = window_mod.PetWindow._bounds_warm_complete

    def __init__(self, lib, cats) -> None:
        self.lib = lib
        self.cats = cats
        self.scale = 0.72
        self._screen_dpr = 1.0
        self._closing = False
        self._hidden_paused = False
        self._bounds_warm_started = False
        self._bounds_warm_running = False
        self._bounds_warm_done = False
        self._bounds_warm_key = None
        self._bounds_warm_retries = 0
        self._bounds_warm_round_gen = 0
        self._bounds_warm_empty_ticks = 0
        self._bounds_warm_thread = None
        self._bounds_warm_stop_evt = threading.Event()
        self._bounds_warm_procs = set()
        self._bounds_warm_procs_lock = threading.Lock()
        self._bounds_warm_results = queue.Queue()
        self._bounds_warm_timer = QTimer()

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: 1.0)


def _warm_pet(lib, cats):
    return _WarmPet(lib, cats)


def test_window_warm_cycle_drives_to_done_and_restarts(monkeypatch):
    """窗口级预热链路（B14 复审 P1）：start 采集纯数据快照 → worker 独立解码
    → GUI 轮询提交 → 按目标键完成（GIF 等无 bounds API 不计入）；stop 后置
    停止事件并复位在飞标志，可立即重启。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    webm = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)]), _frame_bytes([(300, 100, 640, 360)])]
    _install_fake_decode(monkeypatch, webm, frames)
    gif = SimpleNamespace()  # 无 bounds API：不计入完成
    lib = _WarmLib({"idle": webm, "idle_gif": gif})
    win = _WarmWindow(lib, {"idles": ["idle", "idle_gif"]})
    win._bounds_warm_started = True

    win._maybe_start_bounds_warm()
    assert win._bounds_warm_running, "worker 必须已启动"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        win._on_bounds_warm_poll()
        if win._bounds_warm_done:
            break
        time.sleep(0.01)
    assert win._bounds_warm_done, "全目标键提交后必须判完成"
    assert webm.bounds_union(False, 0.72, 1.0) is not None, "非镜像键已提交"
    assert webm.bounds_union(True, 0.72, 1.0) is not None, "镜像键已提交"

    # stop（hide/close 语义）：停止事件置位 + 在飞标志复位 → 可重启
    win._bounds_warm_stop()
    assert win._bounds_warm_stop_evt.is_set()
    assert win._bounds_warm_running is False
    webm._bounds_cache.clear()  # 模拟 (scale, dpr) 键变化：旧键缓存失效需重新预热
    win._bounds_warm_done = False
    win._maybe_start_bounds_warm()
    assert win._bounds_warm_running, "stop 后必须可立即重启"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        win._on_bounds_warm_poll()
        if win._bounds_warm_done:
            break
        time.sleep(0.01)
    assert win._bounds_warm_done, "重启后必须再次完成"
    webm.cleanup()


def test_bounds_warm_complete_verifies_committed_data():
    """_bounds_warm_done 判定按目标键验证结果已提交（B14 复审 P1）：clip 已
    创建 ≠ 结果已提交；GIF 等无 bounds API 的 clip 不计入（运行时回落扫描）。"""
    _qapp()
    webm = WebMClip("dummy.webm")
    for mirrored in (False, True):
        webm._bounds_cache[(mirrored, 0.72, 1.0)] = bp.AnimBounds(
            1, bp.empty_flat(1), QRect(1, 2, 3, 4), QPoint(2, 5), False
        )
    gif = SimpleNamespace()  # 无 bounds_data：不计入完成
    lib = _WarmLib({"idle": webm, "idle_gif": gif})
    pet = _warm_pet(lib, {"idles": ["idle", "idle_gif"]})
    assert pet._bounds_warm_complete() is True, "目标键均已提交 → 完成"

    webm2 = WebMClip("dummy2.webm")  # clip 已创建但 bounds 未提交
    lib2 = _WarmLib({"idle": webm2})
    pet2 = _warm_pet(lib2, {"idles": ["idle"]})
    assert pet2._bounds_warm_complete() is False, "clip 已创建但结果未提交 → 未完成"

    lib3 = _WarmLib({})  # 目标 clip 尚未创建
    pet3 = _warm_pet(lib3, {"idles": ["idle"]})
    assert pet3._bounds_warm_complete() is False, "目标 clip 未创建 → 未完成"
    webm.cleanup()
    webm2.cleanup()


def test_rebuild_frame_resets_collision_on_scale_dpr_change(monkeypatch):
    """(scale, dpr) 键变化后旧碰撞 union 失效重置（B14 复审 P1）：DPR 变化时
    _collision_local_bounds 必须清空后按新键几何重新累积，不得与旧键并集混并。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    frame_a = _make_rgba_frame(640, 360, [(60, 30, 260, 150)])
    frame_b = _make_rgba_frame(640, 360, [(300, 100, 640, 360)])
    pet = _ScanPet(_FrameClip(frame_a), scale=0.72, dpr=1.0, facing="left")
    window_mod.PetWindow._rebuild_frame(pet)
    r1 = QRect(pet._collision_local_bounds)
    assert not r1.isEmpty()

    # 跨屏 DPR 变化 + 帧切换：旧 union 必须重置，从当前帧重新累积
    pet._screen_dpr = 2.0
    pet.movie._frame = frame_b
    window_mod.PetWindow._rebuild_frame(pet)
    r2 = QRect(pet._collision_local_bounds)
    assert r2 != r1, "帧/DPR 变化后几何应不同"
    assert pet._collision_local_bounds == pet._mask_bounds, \
        "重置后从当前帧重新累积（未与旧键帧并集混并）"


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


# ================================================================ B14 复审 R2：运行时播放契约（P0）
class _PlaybackLib:
    """真实播放链路假库：frames/movie/movies 与 PetWindow 期望一致。"""

    def __init__(self, clip, name="idle") -> None:
        self._clip = clip
        self._name = name
        self.no_mirror = frozenset()

    def frames(self, name):
        return self._clip.frameCount()

    def movie(self, name):
        return self._clip

    def movies(self):
        return {self._name: self._clip}


class _PlaybackPet:
    """挂载真实 _on_frame/_rebuild_frame/_sync_mask 的假窗口：movie 为真实
    WebMClip。只用于「start → reader 线程 → QTimer → frameChanged →
    _on_frame」端到端播放时序测试（B14 复审 R2 P0）。

    _sync_mask 用包装：事件循环泵动期间其他遗留窗口/定时器也可能触发模块级
    画布扫描（_mono_mask_bounds），不能算进本测试——用 _sync_scan_active
    只标记本 pet 的扫描，回落断言只针对本 pet 的播放链。
    """

    _on_frame = window_mod.PetWindow._on_frame
    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect
    _orig_sync_mask = window_mod.PetWindow._sync_mask
    _sync_scan_active = False

    def _sync_mask(self):
        _PlaybackPet._sync_scan_active = True
        try:
            return _PlaybackPet._orig_sync_mask(self)
        finally:
            _PlaybackPet._sync_scan_active = False

    def __init__(self, clip, lib, *, scale=0.72, dpr=1.0, facing="left") -> None:
        self.movie = clip
        self.lib = lib
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
        self._hidden_paused = False
        self._closing = False
        self._ended_fired = False
        # 每次 _on_frame 记录 (signal 值, 显示帧索引, _mask_bounds)
        self.frame_events = []
        # 播完事件：(name, 触发时显示帧索引, 总帧数)
        self.ended_events = []
        self.updates = 0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def update(self) -> None:
        self.updates += 1

    def mask(self):
        return QRegion(0, 0, 10, 10)

    def clearMask(self) -> None:
        pass

    def _on_anim_ended(self, name):
        self.ended_events.append(
            (name, self.movie.currentFrameNumber(), self.lib.frames(name))
        )


def _drive_event_loop(app, predicate, timeout=5.0):
    """驱动 Qt 事件循环直到 predicate() 为真（QTimer/信号链真实推进）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def test_on_frame_end_detection_uses_display_index():
    """播完判定回归（B14 复审 R2 P0）：_on_frame 必须用 0 基显示帧索引判定末帧，
    不能把 1 基 frameChanged 事件序号当帧号——三帧动画在信号值 2（第二帧）时
    不得提前结束，最后一帧（显示索引 2 / 信号值 3）才触发一次播完。"""
    _qapp()

    class _Movie:
        def __init__(self, frame_count):
            self._fc = frame_count
            self._display = 0
            self.stops = 0

        def currentFrameNumber(self):
            return self._display

        def frameCount(self):
            return self._fc

        def stop(self):
            self.stops += 1

    class _Lib:
        no_mirror = frozenset()

        def frames(self, name):
            return 3

    class _Pet:
        def __init__(self, movie):
            self.movie = movie
            self.lib = _Lib()
            self.anim = "idle"
            self._hidden_paused = False
            self._closing = False
            self._ended_fired = False
            self.rebuilt = 0
            self.ended = 0

        def _rebuild_frame(self):
            self.rebuilt += 1

        def update(self):
            pass

        def _on_anim_ended(self, name):
            self.ended += 1

    movie = _Movie(3)
    pet = _Pet(movie)
    # 逐帧推进显示索引，信号值模拟真实 WebMClip 时序（显示 0/1/2 → 信号 1/2/3）
    for display, signal in ((0, 1), (1, 2), (2, 3)):
        movie._display = display
        window_mod.PetWindow._on_frame(pet, "idle", signal)
    assert pet.rebuilt == 3, "每一帧都必须重建画面（不得在第二帧提前结束）"
    assert pet.ended == 1, "播完判定只允许在最后一帧触发一次"
    assert movie.stops == 1, "只在最后一帧 stop"


def test_real_playback_timing_frame_contract(monkeypatch):
    """真实播放时序端到端（B14 复审 R2 P0）：start → reader 线程 → QTimer →
    frameChanged → PetWindow._on_frame 全链路。三帧动画：
    - frameChanged 信号值为 1 基事件序号（1,2,3）；
    - currentFrameNumber()（0 基显示帧索引）为 0,1,2（== 预计算表索引）；
    - 每帧查预计算表命中当前帧 bounds（首帧=A、末帧=C，不越界回落）；
    - 播完判定必须发生在最后一帧（显示索引 2），绝不能在第二帧提前结束
      （旧实现把 1 基信号值当 0 基帧号用，三帧动画在信号值 2 时被误判结束，
      第三帧永远不显示、不触发窗口回调）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    clip = WebMClip("dummy.webm")
    rects = [(60, 30, 260, 150), (300, 100, 640, 360), (10, 200, 100, 300)]
    frames = [_frame_bytes([r]) for r in rects]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    data = clip.bounds_data(False, 0.72, 1.0)
    assert data is not None and data.frame_count == 3

    def _img(fb):
        return QImage(fb, 640, 360, 640 * 4, QImage.Format.Format_RGBA8888)

    expected = [
        bp.frame_window_bounds(_img(frames[0]), mirrored=False, scale=0.72, dpr=1.0),
        bp.frame_window_bounds(_img(frames[1]), mirrored=False, scale=0.72, dpr=1.0),
        bp.frame_window_bounds(_img(frames[2]), mirrored=False, scale=0.72, dpr=1.0),
    ]
    # 固定元数据（避免 reader 用假 meta 的 fps×duration 覆盖帧数）：
    # 帧数=3 是播完判定的分母；提高 fps 只是加快 QTimer 节拍，帧号契约与节拍无关。
    clip._frame_count = 3
    clip._duration = 1.0
    clip._fps = 240.0

    scan_calls = []

    def _rec_mono(img):
        # 只记录本 pet 的回落扫描（_sync_scan_active 标记）；事件循环泵动期间
        # 其他遗留窗口/定时器的扫描与本测试无关，不计入
        if _PlaybackPet._sync_scan_active:
            scan_calls.append(img)
        return None

    monkeypatch.setattr(window_mod, "_mono_mask_bounds", _rec_mono)

    lib = _PlaybackLib(clip)
    pet = _PlaybackPet(clip, lib)
    lookups = []
    lookup_errors = []
    lookup_misses = []
    orig_bounds_rect = clip.bounds_rect

    def _rec_bounds(mirrored, scale, dpr, frame_n):
        lookups.append(frame_n)
        try:
            r = orig_bounds_rect(mirrored, scale, dpr, frame_n)
        except Exception as exc:  # 记录异常以便定位回落根因
            lookup_errors.append((frame_n, repr(exc)))
            raise
        if r is None:
            lookup_misses.append(
                (frame_n, clip.bounds_data(mirrored, scale, dpr),
                 clip.currentFrameNumber(), clip._display_frame_index)
            )
        return r

    clip.bounds_rect = _rec_bounds

    def _on_frame_record(name, n):
        window_mod.PetWindow._on_frame(pet, name, n)
        pet.frame_events.append((n, clip.currentFrameNumber(),
                                 QRect(pet._mask_bounds) if pet._mask_bounds is not None else None))

    clip.frameChanged.connect(
        lambda n, name="idle": _on_frame_record(name, n)
    )
    assert clip.start() is True, "真实播放启动必须成功"

    app = _qapp()
    assert _drive_event_loop(app, lambda: bool(pet.ended_events)), \
        "动画必须真实播完并触发 _on_anim_ended"

    # 播完判定发生在最后一帧：显示索引 == 总帧数-1（绝不在第二帧提前结束）
    name, shown, total = pet.ended_events[0]
    assert total == 3
    assert shown == 2, f"播完判定必须发生在最后一帧（显示索引 2），实际在显示索引 {shown} 提前结束"
    assert len(pet.ended_events) == 1, "_on_anim_ended 只允许触发一次"
    assert clip.currentFrameNumber() == 2, "停表后显示帧索引必须停留在最后一帧"
    # 三帧全部显示过：每帧 _mask_bounds 命中当前帧预计算 bounds
    assert [e[0] for e in pet.frame_events] == [1, 2, 3], "frameChanged 信号序列必须为 1 基事件序号"
    assert [e[1] for e in pet.frame_events] == [0, 1, 2], "显示帧索引必须为 0 基序列"
    assert [e[2] for e in pet.frame_events] == expected, "每帧查表必须命中当前帧 bounds"
    assert lookups == [0, 1, 2], f"预计算查表帧号序列错误: {lookups}（不得错位/越界回落）"
    assert not lookup_errors, f"bounds 查表异常: {lookup_errors}"
    assert not lookup_misses, f"bounds 查表未命中: {lookup_misses}"
    assert not scan_calls, "真实播放链全程不得走回落扫描（每帧均命中预计算表）"
    assert pet.updates >= 3, "窗口必须为每一帧重建画面"
    clip.cleanup()


def test_first_frame_cache_jump_keeps_zero_based_contract(monkeypatch):
    """首帧缓存路径（B14 复审 R2 P0）：_first_image 已缓存时 jumpToFrame(0)
    直接应用首帧（零阻塞），显示帧索引保持 0（查表命中第 0 帧）、事件序号
    保持 0——与播放路径共用同一 0 基显示帧契约。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    clip = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)]), _frame_bytes([(300, 100, 640, 360)])]
    _install_fake_decode(monkeypatch, clip, frames)
    clip.warm_bounds(False, 0.72, 1.0)
    expected0 = bp.frame_window_bounds(
        QImage(frames[0], 640, 360, 640 * 4, QImage.Format.Format_RGBA8888),
        mirrored=False, scale=0.72, dpr=1.0,
    )
    clip._first_image = QImage(frames[0], 640, 360, 640 * 4,
                               QImage.Format.Format_RGBA8888)
    clip._first_frame_done.set()
    scan_calls = []
    monkeypatch.setattr(window_mod, "_mono_mask_bounds",
                        lambda img: (scan_calls.append(img), None)[1])
    pet = _CachedPet(clip)
    assert clip.jumpToFrame(0) is True
    assert clip.currentFrameNumber() == 0, "首帧缓存路径显示帧索引必须为 0"
    assert clip._display_frame_index == 0 and clip._frame_index == 0, \
        "首帧缓存/手动跳帧路径不得残留旧事件序号"
    assert clip._current_pixmap is not None, "首帧缓存必须直接应用（零阻塞）"
    window_mod.PetWindow._sync_mask(pet)
    assert pet._mask_bounds == expected0, "首帧缓存路径必须命中首帧 bounds"
    assert not scan_calls, "首帧路径不得走回落扫描"
    clip.cleanup()


# ================================================================ B14 复审 R2：缓存读写同步协议（P1）
def test_bounds_cache_lock_protocol_rejects_stale_and_never_partial():
    """缓存读写同步协议（B14 复审 R2 P1）：读（bounds_data）与写
    （bounds_warm_commit）共用同一把锁；代次匹配才提交、取消/换代后旧代次
    提交被拒且绝不进入缓存；读者只可能看到 None 或完整 AnimBounds。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    key = (False, 0.72, 1.0)
    data_a = bp.AnimBounds(2, bp.empty_flat(2), QRect(1, 2, 3, 4), QPoint(2, 5), False)
    data_b = bp.AnimBounds(2, bp.empty_flat(2), QRect(5, 6, 7, 8), QPoint(2, 5), False)
    gen0 = clip._bounds_gen
    assert clip.bounds_data(False, 0.72, 1.0) is None, "未预计算返回 None"
    # 代次匹配 → 提交成功；读者立即可见完整对象
    assert clip.bounds_warm_commit(key, data_a, gen0) is True
    assert clip.bounds_data(False, 0.72, 1.0) is data_a
    # 取消换代后：旧代次提交被拒，读者仍只见已提交值（旧代次结果绝不生效）
    clip.cancel_bounds_warm()
    assert clip.bounds_warm_commit(key, data_b, gen0) is False
    assert clip.bounds_data(False, 0.72, 1.0) is data_a, "取消后旧代次结果不得生效"
    # cleanup 后：任何提交被拒（读仍返回已提交的完整对象，不返回半成品）
    clip.cleanup()
    assert clip.bounds_warm_commit(key, data_a, clip._bounds_gen) is False
    assert clip.bounds_data(False, 0.72, 1.0) is data_a


def test_bounds_cache_concurrent_reads_never_partial():
    """缓存读写并发压力（B14 复审 R2 P1）：后台读线程与前台提交/取消交错时，
    读者只可能观察到 None 或完整 AnimBounds（整包原子提交，无半成品/无崩溃）。"""
    _qapp()
    clip = WebMClip("dummy.webm")
    key = (False, 0.72, 1.0)
    data = bp.AnimBounds(60, bp.empty_flat(60), QRect(1, 2, 3, 4), QPoint(2, 5), False)
    stop = threading.Event()
    bad: list = []

    def _reader():
        while not stop.is_set():
            v = clip.bounds_data(False, 0.72, 1.0)
            if v is not None and not isinstance(v, bp.AnimBounds):
                bad.append(v)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        for _ in range(200):
            gen0 = clip._bounds_gen
            clip.bounds_warm_commit(key, data, gen0)
            clip.cancel_bounds_warm()  # 换代：旧代次结果绝不写入
            assert clip.bounds_warm_commit(key, data, gen0) is False
            clip.bounds_warm_commit(key, data, clip._bounds_gen)
    finally:
        stop.set()
        t.join(5.0)
    assert not bad, "读者绝不见半成品/非 AnimBounds 数据"
    clip.cleanup()


# ================================================================ B14 复审 R2：窗口 worker 生命周期（P1）
def test_bounds_warm_stop_joins_and_drops_queued_results():
    """窗口级停止协议（B14 复审 R2 P1）：停止置事件 + terminate 已登记进程 +
    有界 join 旧 worker + 清空结果队列 + 窗口级换代（旧轮次迟到结果不得生效），
    复位后可立即重启。"""
    _qapp()
    proc = _FakeDecodeProc()

    class _FakeThread:
        def __init__(self):
            self.join_calls = []

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.join_calls.append(timeout)

    th = _FakeThread()
    pet = SimpleNamespace(
        _bounds_warm_stop_evt=threading.Event(),
        _bounds_warm_procs={proc},
        _bounds_warm_procs_lock=threading.Lock(),
        _bounds_warm_timer=QTimer(),
        _bounds_warm_running=True,
        _bounds_warm_thread=th,
        _bounds_warm_results=queue.Queue(),
        _bounds_warm_round_gen=3,
    )
    pet._bounds_warm_results.put(("idle", (False, 0.72, 1.0), object(), 1, 3))
    window_mod.PetWindow._bounds_warm_stop(pet)
    assert pet._bounds_warm_stop_evt.is_set(), "停止事件必须置位"
    assert proc.terminated or proc.killed, "已登记解码进程必须 terminate"
    assert pet._bounds_warm_procs == set(), "登记集合必须清空"
    assert th.join_calls, "停止必须 join 旧 worker（不残留后台线程）"
    assert pet._bounds_warm_results.empty(), "停止必须清空结果队列（迟到结果不得残留）"
    assert pet._bounds_warm_round_gen == 4, "停止必须窗口级换代（迟到结果不得生效）"
    assert not pet._bounds_warm_timer.isActive()
    assert pet._bounds_warm_running is False
    assert pet._bounds_warm_thread is None


def test_bounds_warm_poll_drops_stale_round_results(monkeypatch):
    """窗口级换代（B14 复审 R2 P1）：旧轮次 worker 的迟到结果（round_gen 不匹配）
    必须被 poll 丢弃，绝不提交进 clip 缓存；当前轮次结果正常提交。"""
    _qapp()
    webm = WebMClip("dummy.webm")
    data = bp.AnimBounds(1, bp.empty_flat(1), QRect(1, 2, 3, 4), QPoint(2, 5), False)
    # no_mirror：完成判定只要求非镜像键（本测试只提交该键）
    win = _WarmWindow(_WarmLib({"idle": webm}, no_mirror={"idle"}), {"idles": ["idle"]})
    win._bounds_warm_started = True
    win._bounds_warm_round_gen = 7
    # 旧轮次（round_gen=6）迟到结果 + 当前轮次结果混入队列
    win._bounds_warm_results.put(("idle", (False, 0.72, 1.0), data, webm._bounds_gen, 6))
    win._bounds_warm_results.put(("idle", (False, 0.72, 1.0), data, webm._bounds_gen, 7))
    win._on_bounds_warm_poll()
    assert webm.bounds_data(False, 0.72, 1.0) is data, "仅当前轮次结果提交"
    assert win._bounds_warm_done, "当前轮次结果提交后判完成"
    webm.cleanup()


# ================================================================ B14 复审 R2：_bounds_warm_done 状态机（P1）
def test_window_warm_no_stall_when_snapshot_busy(monkeypatch):
    """并发认领忙（bounds_warm_snapshot 全返回 None）时不得永久停滞（B14 复审
    R2 P1）：忙态下不启动 worker、不虚假判完成，但必须进入轮询重试；忙态结束
    后轮询自动补跑并完成。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    webm = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)]), _frame_bytes([(300, 100, 640, 360)])]
    _install_fake_decode(monkeypatch, webm, frames)
    lib = _WarmLib({"idle": webm})
    win = _WarmWindow(lib, {"idles": ["idle"]})
    win._bounds_warm_started = True

    webm._bounds_lock.acquire()  # 模拟并发 clip 级 warm 占用：快照采集全部认领忙
    try:
        win._maybe_start_bounds_warm()
        assert not win._bounds_warm_running, "忙态下不得启动 worker"
        assert not win._bounds_warm_done, "忙态下不得虚假判完成"
        assert win._bounds_warm_timer.isActive(), "忙态下必须进入轮询重试（不得永久停滞）"
    finally:
        webm._bounds_lock.release()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        win._on_bounds_warm_poll()
        if win._bounds_warm_done:
            break
        time.sleep(0.01)
    assert win._bounds_warm_done, "忙态结束后轮询重试必须自动补跑并完成"
    assert webm.bounds_union(False, 0.72, 1.0) is not None
    webm.cleanup()


def test_bounds_warm_retry_exhaustion_does_not_false_done(monkeypatch):
    """重试耗尽不得虚假标记 done（B14 复审 R2 P1）：目标键始终无法提交（如
    永久解码失败）时，_bounds_warm_done 必须保持 False（预计算确实未完成），
    定时器停表放弃本轮；后续触发（showEvent/(scale,dpr) 变化/批次补建）会
    重置重试。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    webm = WebMClip("dummy.webm")
    frames = [_frame_bytes([(60, 30, 260, 150)])]
    _install_fake_decode(monkeypatch, webm, frames)
    lib = _WarmLib({"idle": webm})
    win = _WarmWindow(lib, {"idles": ["idle"]})
    win._bounds_warm_started = True
    # 提交永远失败（模拟永久解码失败/键不匹配）：目标键永不就绪
    monkeypatch.setattr(webm, "bounds_warm_commit", lambda key, data, gen: False)

    win._maybe_start_bounds_warm()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        win._on_bounds_warm_poll()
        if not win._bounds_warm_timer.isActive() and not win._bounds_warm_running:
            break
        time.sleep(0.01)
    assert not win._bounds_warm_timer.isActive(), "重试耗尽必须停表"
    assert not win._bounds_warm_running
    assert win._bounds_warm_done is False, "失败路径不得虚假标记 done（预计算未完成）"
    assert webm.bounds_data(False, 0.72, 1.0) is None, "失败路径不得有缓存结果"
    webm.cleanup()


# ================================================================ B14 复审 R2：DPR 往返 / union 窗口接缝
def test_rebuild_frame_resets_collision_on_dpr_round_trip(monkeypatch):
    """DPR 完整往返 1.0→2.0→1.0（B14 复审 R2 P1）：每次 (scale, dpr) 键变化都
    重置 _collision_local_bounds，往返后按新键几何重新累积，不得残留旧键并集。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    frame = _make_rgba_frame(640, 360, [(60, 30, 260, 150)])
    pet = _ScanPet(_FrameClip(frame), scale=0.72, dpr=1.0, facing="left")
    window_mod.PetWindow._rebuild_frame(pet)
    r1 = QRect(pet._collision_local_bounds)
    assert not r1.isEmpty()

    pet._screen_dpr = 2.0
    window_mod.PetWindow._rebuild_frame(pet)
    r2 = QRect(pet._collision_local_bounds)
    assert r2 != r1, "DPR 1.0→2.0 几何应变化"
    assert pet._collision_local_bounds == pet._mask_bounds, "DPR 变化后必须从当前帧重新累积"

    pet._screen_dpr = 1.0
    window_mod.PetWindow._rebuild_frame(pet)
    r3 = QRect(pet._collision_local_bounds)
    assert r3 == r1, "DPR 2.0→1.0 往返后几何应回到 1.0 基准（不得残留 2.0 键并集）"
    assert pet._collision_local_bounds == pet._mask_bounds


def test_collision_content_rect_uses_accumulated_union():
    """碰撞窗口接缝（B14 复审 R2）：collision_content_rect 取逐帧累积 union——
    「视觉未接触但 union 接触」时碰撞体大于当前帧视觉区域（圆链不随帧跳动）。"""
    _qapp()

    class _UnionPet:
        _collision_local_bounds = QRect(20, 30, 120, 150)  # 累积 union（大于当前帧）
        _mask_bounds = QRect(60, 60, 40, 50)               # 当前帧视觉区域

        def frameGeometry(self):
            return QRect(100, 200, 300, 400)

        def visible_content_rect(self):
            return QRect(160, 260, 40, 50)

    rect = window_mod.PetWindow.collision_content_rect(_UnionPet())
    assert rect == QRect(120, 230, 120, 150), \
        f"collision_content_rect 必须返回累积 union（当前帧视觉区域仅 {_UnionPet._mask_bounds}）"


def test_warm_bounds_real_webm_all_categories_match_runtime_scan(monkeypatch):
    """真实 webm 全类别覆盖（B14 复审 R2）：不再只取第一个文件——按顶层类别
    目录（click/idle/turn/move/drag/act…）各取一个真实素材，warm_bounds 全帧
    预计算后采样帧缓存 bounds 与运行时扫描逐帧一致（1 个 scale/DPR 组合控制
    运行时长，类别覆盖保证样本代表性）。"""
    _qapp()
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    files = sorted(VIDEOS_ROOT.rglob("*.webm"))
    if not files:
        pytest.skip("缺少真实 webm 素材目录")
    by_cat: dict[str, Path] = {}
    for p in files:
        cat = p.parent.name
        by_cat.setdefault(cat, p)
    picks = [by_cat[k] for k in sorted(by_cat)]
    assert len(picks) >= 3, "素材类别覆盖过少"
    checked = 0
    for path in picks:
        clip = WebMClip(path)
        try:
            clip.warm_bounds(False, 0.72, 1.0)
            data = clip.bounds_data(False, 0.72, 1.0)
            assert data is not None, f"{path.name} 未预计算"
            frames = _decode_frames(path)
            assert len(frames) > 0
            for fi in _sample_indices(len(frames), min_samples=6):
                cached = clip.bounds_rect(False, 0.72, 1.0, fi)
                assert cached is not None, f"{path.name} frame#{fi} 未预计算"
                expected = bp.frame_window_bounds(
                    frames[fi], mirrored=False, scale=0.72, dpr=1.0
                )
                checked += 1
                assert cached == expected, (
                    f"{path.name} frame#{fi}: 缓存={cached} 运行时={expected}"
                )
        finally:
            clip.cleanup()
    assert checked >= 18, "真实素材类别覆盖帧数不足"
