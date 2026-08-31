# -*- coding: utf-8 -*-
"""B5 差分测试：Windows _mask_bounds 新旧算法在真实 webm 素材上 bounds 逐帧一致。

旧算法（HEAD 基准，本测试的 bounds 基准）：
    canvas = QImage(w, h, ARGB32)  →  drawPixmap(rect, frame_pixmap)
    mask   = QBitmap.fromImage(canvas.createAlphaMask())
    bounds = QRegion(mask).boundingRect()

新算法（产品实现 pet.window.PetWindow._sync_mask 的 Windows 分支）：
    同一张「实际绘制用」画布（与 _sync_mask 的 drawPixmap 同源、与 paintEvent
    同几何）createAlphaMask 后直接扫描 1bpp 掩码算包围盒——窗口边界裁剪、
    alpha>=128 阈值、DPR/采样映射全部与旧路径天然一致，不做源图 bbox 反推。

素材：assets/characters/shenshen/videos/ 下全部 webm（真实 alpha 轮廓；
squash 峰值时绘制矩形确实超出窗口左右边界，覆盖旧 canvas 的裁剪语义）。

本测试驱动的是真实产品代码链：_rebuild_frame（toImage→镜像→预乘→Smooth
缩放→ARGB32→QPixmap+DPR）→ _sync_mask（Windows 分支），再与旧算法逐帧
比较 bounds。

注意：本测试断言的是「bounds 逐帧一致」（旧/新算法的包围盒逐帧相等），
不是整幅图像的逐像素一致。
"""

# ---------------------------------------------------------------- 已知局限
# 本测试通过 monkeypatch 把 window_mod.os.name 桩成 "nt" 来驱动 _sync_mask 的
# Windows 分支；offscreen CI 环境没有真实 Windows 窗口系统，无法真实验证
# Windows 平台行为。该桩只保证 Windows 分支代码被真实执行并与旧算法逐帧比较
# bounds；平台级验证仍需在真实 Windows（手工或平台 CI）上完成。
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBitmap, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication

from pet import window as window_mod
from pet import catalog

pytest.importorskip("imageio_ffmpeg", reason="imageio-ffmpeg 不可用，无法解码真实 webm")

VIDEOS_ROOT = (
    Path(__file__).resolve().parent.parent
    / "assets" / "characters" / "shenshen" / "videos"
)


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 素材解码
def _video_files() -> list[Path]:
    """仓库内置 shenshen 角色全部 webm（含 idle/turn/move/click/drag/random）。"""
    return sorted(VIDEOS_ROOT.rglob("*.webm"))


def _decode_frames(path: Path) -> list[QImage]:
    """与 WebMClip._reader 相同的解码参数，逐帧产出 RGBA8888 QImage。"""
    import imageio_ffmpeg

    gen = imageio_ffmpeg.read_frames(
        str(path),
        pix_fmt="rgba",
        bits_per_pixel=32,
        input_params=["-c:v", "libvpx-vp9"],
    )
    meta = next(gen)
    w, h = meta.get("size") or (catalog.CANVAS_W, catalog.CANVAS_H)
    frames: list[QImage] = []
    for data in gen:
        img = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        if not img.isNull():
            frames.append(img.copy())
    return frames


SAMPLES_MIN = 20  # 长动画等距采样的帧数下限（短动画则全帧覆盖）


def _sample_indices(n: int, min_samples: int = SAMPLES_MIN) -> list[int]:
    """采样策略：短动画（帧数 <= min_samples）全帧；长动画首/尾 + 等距至少
    min_samples 帧（覆盖同一动画的不同轮廓帧）。"""
    if n <= min_samples:
        return list(range(n))
    idxs = {0, n - 1} | {
        round((n - 1) * k / (min_samples - 1)) for k in range(1, min_samples - 1)
    }
    return sorted(i for i in idxs if 0 <= i < n)


# ---------------------------------------------------------------- 旧算法基准
def _reference_bounds(pm: QPixmap, w: int, h: int, rect: QRect) -> QRect:
    """旧实现（canvas→createAlphaMask→QBitmap→QRegion）作为 bounds 基准。"""
    canvas = QImage(w, h, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.drawPixmap(rect, pm)
    p.end()
    mask = QBitmap.fromImage(canvas.createAlphaMask())
    return QRegion(mask).boundingRect()


# ---------------------------------------------------------------- 新算法（真实产品代码）
class _DecodeClip:
    """向 _rebuild_frame 提供真实解码帧的假播放器（帧号可控）。"""

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


class _DecodeLib:
    no_mirror = frozenset()


class _DifferentialPet:
    """只挂载真实 _rebuild_frame / _sync_mask / _frame_draw_rect 的假窗口。"""

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _sync_mask = window_mod.PetWindow._sync_mask
    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect

    def __init__(
        self,
        clip,
        *,
        scale: float,
        dpr: float,
        facing: str,
        squash_active: bool,
        squash_progress: float,
    ) -> None:
        self.movie = clip
        self.lib = _DecodeLib()
        self.scale = scale
        self.facing = facing
        self.anim = "idle"
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._squash_active = squash_active
        self._squash_progress = squash_progress
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


def _run_new_bounds(pet) -> QRect:
    """跑真实产品链（_rebuild_frame → _sync_mask Windows 分支），返回新 bounds。"""
    window_mod.PetWindow._rebuild_frame(pet)
    return QRect(pet._mask_bounds)


# ---------------------------------------------------------------- 测试
def test_windows_mask_bounds_differential_on_real_webm(monkeypatch):
    """真实素材逐帧差分：每个动画多个帧 × 多档几何（含 squash 越界）× DPR。

    旧算法与新产品 Windows 分支的 bounds 必须逐帧完全一致；同时证明测试
    真的覆盖了「绘制矩形超出窗口」的裁剪场景。
    """
    _qapp()
    files = _video_files()
    if not files:
        pytest.skip("缺少真实 webm 素材目录")
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    failures: list[str] = []
    checked_animations = 0
    checked_frames = 0
    checked_cases = 0
    nonempty_cases = 0
    overflow_hit = False
    min_covered = 10**9
    max_covered = 0

    for path in files:
        try:
            frames = _decode_frames(path)
        except Exception as exc:
            failures.append(f"{path.name}: 解码失败 {exc!r}")
            continue
        if not frames:
            failures.append(f"{path.name}: 无帧")
            continue
        checked_animations += 1
        frame_idxs = _sample_indices(len(frames))
        # 防静默少测：每个被测动画的实际覆盖帧数必须达到下限
        # （短动画全帧、长动画等距至少 SAMPLES_MIN 帧），采样策略退化即失败。
        covered_min = min(len(frames), SAMPLES_MIN)
        assert len(frame_idxs) >= covered_min, (
            f"{path.name}: 实际覆盖 {len(frame_idxs)} 帧，低于下限 {covered_min}"
        )
        min_covered = min(min_covered, len(frame_idxs))
        max_covered = max(max_covered, len(frame_idxs))
        for fi in frame_idxs:
            frame = frames[fi]
            facing = "right" if fi % 2 else "left"  # 一半帧走镜像路径
            for scale in (0.5, 1.0):
                for dpr in (1.0, 2.0):
                    geoms = [("normal", False, 1.0),
                             ("squash-0.25", True, 0.25),
                             ("squash-0.5", True, 0.5),
                             ("squash-0.75", True, 0.75)]
                    for label, squash_active, progress in geoms:
                        pet = _DifferentialPet(
                            _DecodeClip(frame, fi),
                            scale=scale, dpr=dpr, facing=facing,
                            squash_active=squash_active,
                            squash_progress=progress,
                        )
                        rect = window_mod.PetWindow._frame_draw_rect(pet)
                        if label.startswith("squash") and (
                            rect.left() < 0 or rect.right() >= pet._w
                        ):
                            overflow_hit = True
                        # 先跑新链（内部 _rebuild_frame 会生成 _frame_pixmap），
                        # 旧基准用同一张 pixmap 与同一几何比较。
                        new = _run_new_bounds(pet)
                        old = _reference_bounds(pet._frame_pixmap, pet._w, pet._h, rect)
                        checked_cases += 1
                        if not old.isEmpty():
                            nonempty_cases += 1
                        if old != new:
                            failures.append(
                                f"{path.name} frame#{fi} scale={scale} dpr={dpr} "
                                f"facing={facing} {label}: old={old} new={new} "
                                f"rect=({rect.x()},{rect.y()},{rect.width()}x{rect.height()}) "
                                f"w={pet._w} h={pet._h}"
                            )
            checked_frames += 1

    print(
        f"\n[差分] 动画 {checked_animations} 个 / 帧样本 {checked_frames} "
        f"（每动画覆盖 {min_covered}~{max_covered} 帧，下限 {SAMPLES_MIN}）/ "
        f"比较 {checked_cases} 例（非空 {nonempty_cases}）/ squash 越界 {overflow_hit}"
    )
    assert overflow_hit, "测试必须真正覆盖 squash 越界几何（当前配置下无越界）"
    assert nonempty_cases > 0, "真实素材应存在非空 alpha 轮廓（当前全部为空？）"
    assert not failures, (
        "旧 QRegion 路径与新 canvas 扫描路径出现 bounds 分歧：\n"
        + "\n".join(failures[:60])
    )
