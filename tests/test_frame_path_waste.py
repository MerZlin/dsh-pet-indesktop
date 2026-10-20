# -*- coding: utf-8 -*-
"""O1 帧路径「每帧浪费」回归（帧序列 B 档，offscreen）。

三件事必须在真播放里逐帧成立（真 webp 素材 + 真预取线程 + 真 PetSprite）：
1. 上屏/交付零 QPixmap 构建——overlay 渲染链只读 ``currentImage()``，而 QPixmap
   只在托盘/灵动岛图标那类消费者请求时惰性构建（按帧缓存）；
2. 每帧恰好解码一次——预热解出的帧 0 交给 ``start()`` 复用，不再让预取 worker
   重解（改前预热起播每圈多解一帧：~1.2ms + 一次线程往返）；
3. 每新帧 sprite 恰好重建一次（渲染仍按新帧产出非空 pixmap）。

计数探针绑在 ``pet.frameseq_clip`` 自己的 ``QPixmap`` / ``QImage`` 名字上
（真实模块 seam），产品对象一律是真的。纪律同 tests/test_frameseq_clip.py：
同步直调 ``_advance`` + 事件泵，不赌固定 sleep。

H2（2026 全量套件实锤）：解码计数只认**本用例 tmp_path 下**的帧文件——探针
是模块级 seam，别的用例泄漏的 clip 在后台解自己目录的帧走的也是这条路，
不设范围会把外来的解码算进本用例的「每帧恰一次」。范围收窄≠放宽断言：
本目录的帧仍必须逐帧恰好计一次（见
``test_foreign_clip_decode_outside_tmp_path_is_not_counted``）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])

FRAMES = 6


# ---------------------------------------------------------------- 夹具与探针
def _make_frames(dir_path: Path, count: int = FRAMES) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        img = QImage(64, 48, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        for y in range(8):
            for x in range(8):
                img.setPixel(x, y, (0xFF << 24) | ((i * 40) << 16) | 0x2244)
        assert img.save(str(dir_path / f"f_{i + 1:04d}.webp"), "webp", 100)
    (dir_path / "meta.json").write_text(
        json.dumps({"fps": 24.0, "frames": count}), encoding="utf-8")


class _Library:
    """PetSprite 取 clip 的最小库替身（movie/no_mirror）。"""

    def __init__(self, clip):
        self._clip = clip
        self.no_mirror: frozenset[str] = frozenset()

    def movie(self, _name, **_kw):
        return self._clip


class _Probe:
    """按模块绑定的帧路径探针：记录 fromImage 与"解一帧"。

    解码计数只认 ``root``（本用例 tmp_path）下的帧文件：探针绑的是
    ``pet.frameseq_clip`` 的模块级 ``QImage`` 名字，而全量套件里别的用例
    泄漏的 clip 仍在自己的目录里解码，走的正是同一个名字——不设范围就会把
    外来的后台解码算进本用例的「每帧恰一次」。收窄的只有计数**范围**：
    本目录的帧仍必须逐帧恰好计一次（断言强度不变）。
    """

    def __init__(self, module, root: Path):
        self.module = module
        self.root = Path(root)
        self.pixmaps: list[int] = []
        self.decodes: list[str] = []

    def install(self):
        probe = self

        class _PixmapProbe:
            @staticmethod
            def fromImage(image):
                probe.pixmaps.append(1)
                return QPixmap.fromImage(image)

        def _image_probe(path):
            target = Path(str(path))
            if target.is_relative_to(probe.root):
                probe.decodes.append(target.name)
            return QImage(path)

        self.module.QPixmap = _PixmapProbe
        self.module.QImage = _image_probe


@pytest.fixture
def probe(monkeypatch, tmp_path):
    from pet import frameseq_clip
    p = _Probe(frameseq_clip, tmp_path)
    p.install()
    return p


def _pump_until(cond, timeout_s=10.0):
    t0 = time.monotonic()
    while not cond():
        QApplication.processEvents()
        if time.monotonic() - t0 > timeout_s:
            raise AssertionError("事件泵超时：异步交付未到达")
        time.sleep(0.002)


def _render(sprite):
    """真绘制路径：overlay paintEvent → sprite.paint → _rebuild_pixmap（惰性）。"""
    image = QImage(sprite.rect().size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    sprite.paint(painter)
    painter.end()
    return image


def _play_to_end(clip, sprite, frames=FRAMES):
    """逐帧推进到末帧（不依赖真实走时）；每帧都过一遍真绘制路径。"""
    clip._timer.stop()
    clip.frameChanged.connect(lambda _f: _render(sprite))
    _render(sprite)                                  # 起播前先画一帧（同 overlay tick）
    while clip.currentFrameNumber() < frames - 1:
        target = clip.currentFrameNumber() + 1
        clip._advance()
        _pump_until(lambda: clip.currentFrameNumber() >= target)


def _settle(clip):
    """用例收尾前的在途交付落定（本文件自己的测试卫生）。

    预取 worker 的 deleteLater 在共享线程上执行，其析构会断开 `loaded` →
    `_on_loaded` 连接；此刻若 GUI 队列里还压着一次跨线程交付、或 clip 的
    C++ 侧正被 Python 引用计数回收，销毁顺序就落进 teardown 与共享线程的
    竞态（本仓已知的 0xC0000005 漂移崩溃类）。这里把两件事都在用例内做完：
    先把在途交付派发干净，再等 worker 真正析构。
    """
    deadline = time.monotonic() + 3.0
    while getattr(clip, "_wanted", -1) != -1 and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    clip.close()
    try:
        import shiboken6
    except Exception:
        return
    worker = getattr(clip, "_worker", None)
    while worker is not None and shiboken6.isValid(worker) \
            and time.monotonic() < deadline:
        time.sleep(0.002)


def _sprite_for(clip):
    from pet import frameseq_clip
    assert isinstance(clip, frameseq_clip.FrameSeqClip)
    return PetSprite(_Library(clip), pos=QPointF(0, 0), scale=0.5)


# ---------------------------------------------------------------- 用例
def test_playback_decodes_each_frame_once_and_builds_no_pixmap(tmp_path, probe):
    """每帧恰解码一次 + 上屏零 QPixmap（预热起播：帧 0 不重复解码）。"""
    d = tmp_path / "clip"
    _make_frames(d)
    from pet.frameseq_clip import FrameSeqClip
    clip = FrameSeqClip(d)
    sprite = _sprite_for(clip)
    try:
        clip.warm_first_frame()                 # 生产预热入口（后台线程同路径）
        assert probe.decodes == ["f_0001.webp"]
        sprite.bind_clip("idle")                # → start()（复用预热帧 0）
        assert clip.currentFrameNumber() == 0
        assert clip.currentImage() is not None

        _play_to_end(clip, sprite)

        # 每帧一次、无重复（改前预热起播会多解一次帧 0）
        assert probe.decodes == [f"f_{i + 1:04d}.webp" for i in range(FRAMES)], \
            f"帧解码序列不干净：{probe.decodes}"
        assert probe.pixmaps == [], "上屏/交付不得构建 QPixmap"
        # 渲染链真的按每帧重建过（不是没画）
        assert sprite._pixmap is not None and not sprite._pixmap.isNull()
        assert clip.currentFrameNumber() == FRAMES - 1
    finally:
        _settle(clip)


def test_cold_bind_clip_decodes_frame_zero_once(tmp_path, probe):
    """冷起播（bind_clip 的 jumpToFrame(0) 同步首帧）：帧 0 也不重复解码。"""
    d = tmp_path / "clip"
    _make_frames(d)
    from pet.frameseq_clip import FrameSeqClip
    clip = FrameSeqClip(d)
    sprite = _sprite_for(clip)
    try:
        sprite.bind_clip("idle")
        assert clip.currentFrameNumber() == 0 and clip.currentImage() is not None
        _play_to_end(clip, sprite)
        assert probe.decodes == [f"f_{i + 1:04d}.webp" for i in range(FRAMES)]
        assert probe.pixmaps == []
    finally:
        _settle(clip)


def test_sprite_rebuilds_once_per_frame_from_current_image(tmp_path, probe):
    """每新帧 sprite 恰重建一次，且重建后命中图与帧缓冲不别名。"""
    d = tmp_path / "clip"
    _make_frames(d)
    from pet.frameseq_clip import FrameSeqClip

    class _CountingSprite(PetSprite):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.rebuilds = 0

        def _rebuild_pixmap(self):
            rebuilt = super()._rebuild_pixmap()
            self.rebuilds += bool(rebuilt)
            return rebuilt

    clip = FrameSeqClip(d)
    sprite = _CountingSprite(_Library(clip), pos=QPointF(0, 0), scale=0.5)
    try:
        sprite.bind_clip("idle")
        _play_to_end(clip, sprite)
        assert sprite.rebuilds == FRAMES, f"重建次数 {sprite.rebuilds} != {FRAMES}"
        assert probe.pixmaps == []
        assert sprite._hit_image is not None
        # V-15 别名面：命中图是独立缓冲（绝不为省一次 memcpy 而共享活帧）
        assert sprite._hit_image.cacheKey() != clip.currentImage().cacheKey()
    finally:
        _settle(clip)


def test_icon_consumer_builds_lazily_but_still_gets_same_frame(tmp_path, probe):
    """托盘/灵动岛消费者语义不变：按需构建、同帧缓存、换帧失效。"""
    d = tmp_path / "clip"
    _make_frames(d)
    from pet.frameseq_clip import FrameSeqClip
    clip = FrameSeqClip(d)
    try:
        clip.start()
        clip._timer.stop()                          # 时间轴本用例自驱（不赌走时）
        _pump_until(lambda: clip.currentFrameNumber() == 0
                    and clip.currentImage() is not None)
        assert probe.pixmaps == []                  # 还无人请求：零构建

        img = clip.currentImage()
        pm = clip.currentPixmap()
        assert pm is not None and not pm.isNull()
        assert (pm.width(), pm.height()) == (img.width(), img.height())
        assert pm.toImage().pixel(1, 1) == img.pixel(1, 1)
        assert probe.pixmaps == [1]
        assert clip.currentPixmap() is pm           # 同帧缓存
        assert probe.pixmaps == [1]

        clip._advance()
        _pump_until(lambda: clip.currentFrameNumber() == 1)
        assert clip.currentPixmap() is not pm       # 新帧 → 缓存失效重建
        assert probe.pixmaps == [1, 1]
    finally:
        _settle(clip)


def test_foreign_clip_decode_outside_tmp_path_is_not_counted(tmp_path, probe):
    """H2 探针口径：只有本用例 tmp_path 下的帧文件进解码计数。

    复现全量套件里真实发生过的干扰——别的用例泄漏的 clip 仍在自己目录里解码，
    且走的正是探针绑定的那个模块级 ``QImage`` 名字。收窄前后本目录的帧计数
    完全相同（断言强度不变），变的只是外来解码不再冒充本用例的帧。
    """
    d = tmp_path / "clip"
    _make_frames(d)
    from pet.frameseq_clip import FrameSeqClip

    with tempfile.TemporaryDirectory(prefix="frame_probe_foreign_") as foreign_dir:
        foreign = Path(foreign_dir)
        _make_frames(foreign)
        outside = FrameSeqClip(foreign)
        try:
            assert outside.jumpToFrame(0) is True   # 真解一帧（外来目录）
            assert probe.decodes == [], (
                f"外来目录的帧解码不该进计数：{probe.decodes}")
            local = FrameSeqClip(d)
            try:
                assert local.jumpToFrame(0) is True  # 本用例目录：照旧计入
                assert probe.decodes == ["f_0001.webp"]
            finally:
                _settle(local)
        finally:
            _settle(outside)
