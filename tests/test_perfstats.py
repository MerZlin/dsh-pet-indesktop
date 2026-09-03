# -*- coding: utf-8 -*-
"""P0 性能观测（pet/perfstats.py + webm_clip/window 打点）回归测试。

覆盖：
- 模块开关语义：默认关闭（零记录）、enable/disable/reset、dump 落盘/日志；
- 关闭态零开销形态：打点路径不产生任何指标记录、行为与未打点前逐位一致；
- webm_clip 打点：_stamp_source_indices（解码帧耗时 / 队列等待 / 丢帧）、
  _process_frame（主线程消费转换耗时）、_poll（空转计数）；
- window 打点：_rebuild_frame 命中/缺失/快路径与缩放段、_sync_mask 掩码段、
  paintEvent 绘制段、frame_cache 命中率计数。
"""
from __future__ import annotations

import json
import os
import queue as queue_mod
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from pet import catalog
from pet import perfstats
from pet import window as window_mod
from pet.webm_clip import WebMClip


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _perfstats_reset():
    """每个测试前后把 perfstats 复位到默认关闭 + 清空，防跨测试串扰。"""
    perfstats.disable()
    perfstats.reset()
    yield
    perfstats.disable()
    perfstats.reset()


# ================================================================ 模块语义
class TestPerfStatsModule:
    def test_disabled_records_nothing(self):
        perfstats.note("x")
        perfstats.time("y", 1.0)
        assert perfstats.snapshot() == {}

    def test_enable_records_note_and_time(self):
        perfstats.enable()
        perfstats.note("hits")
        perfstats.note("hits")
        perfstats.note("drops", 3)
        perfstats.time("decode", 0.25)
        perfstats.time("decode", 0.5)
        snap = perfstats.snapshot()
        assert snap["hits"]["count"] == 2
        assert snap["drops"]["count"] == 3
        assert snap["decode"] == {"count": 2, "total": 0.75}

    def test_disable_stops_recording_reset_clears(self):
        perfstats.enable()
        perfstats.note("a")
        perfstats.disable()
        perfstats.note("a")  # 关闭后不再记录
        assert perfstats.snapshot()["a"]["count"] == 1
        perfstats.reset()
        assert perfstats.snapshot() == {}

    def test_dump_writes_json_file(self, tmp_path):
        perfstats.enable()
        perfstats.note("webm.decode")
        target = tmp_path / "perf.json"
        perfstats.set_output_file(str(target))
        try:
            snap = perfstats.dump()
            assert snap["webm.decode"]["count"] == 1
            raw = json.loads(target.read_text(encoding="utf-8"))
            assert raw["webm.decode"]["count"] == 1
        finally:
            perfstats.set_output_file(None)

    def test_dump_logs_when_no_file(self, caplog):
        perfstats.enable()
        perfstats.note("rebuild.calls")
        with caplog.at_level("INFO", logger="pet.perfstats"):
            perfstats.dump()
        assert any("rebuild.calls" in rec.message for rec in caplog.records)


# ================================================================ webm_clip 打点
class _FlakyQueue:
    """模拟 reader 有界队列：每隔 drop_every 次 put 抛 queue.Full（丢帧）。"""

    def __init__(self, drop_every: int):
        self._drop_every = drop_every
        self._attempts = 0
        self.items = []

    def put(self, item, timeout=0):
        self._attempts += 1
        if self._drop_every and self._attempts % self._drop_every == 0:
            raise queue_mod.Full
        self.items.append(item)


class TestWebmClipInstrumentation:
    def test_stamp_source_indices_disabled_unchanged(self):
        """关闭态：行为与未打点前逐位一致（丢帧语义 + 帧号连续），且零记录。"""
        q = _FlakyQueue(drop_every=2)
        WebMClip._stamp_source_indices(
            iter([b"f0", b"f1", b"f2", b"f3", b"f4", b"f5"]),
            q,
            lambda: False,
        )
        assert [item[0] for item in q.items] == [b"f0", b"f2", b"f4"]
        assert [item[1] for item in q.items] == [0, 2, 4]
        assert perfstats.snapshot() == {}

    def test_stamp_source_indices_records_decode_drop_queue_wait(self):
        perfstats.enable()
        q = _FlakyQueue(drop_every=2)
        WebMClip._stamp_source_indices(
            iter([b"f0", b"f1", b"f2", b"f3", b"f4", b"f5"]),
            q,
            lambda: False,
        )
        snap = perfstats.snapshot()
        # 每帧一次解码间隔 + 一次入队（含丢帧的等待）记录；三帧因队列满被丢
        assert snap["webm.decode"]["count"] == 6
        assert snap["webm.queue_wait"]["count"] == 6
        assert snap["webm.queue_drop"]["count"] == 3

    def test_throttled_reader_records_queue_wait_never_drops(self):
        perfstats.enable()
        attempts = {"n": 0}

        class _FullThenRoom:
            def __init__(self):
                self.items = []

            def put(self, item, timeout=0):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise queue_mod.Full
                self.items.append(item)

        q = _FullThenRoom()
        WebMClip._stamp_source_indices(
            iter([b"f0", b"f1"]), q, lambda: False, throttled=lambda: True,
        )
        snap = perfstats.snapshot()
        assert [item[0] for item in q.items] == [b"f0", b"f1"]
        assert snap["webm.decode"]["count"] == 2
        assert snap["webm.queue_wait"]["count"] == 2  # 节流路径：背压入队等待
        assert "webm.queue_drop" not in snap  # 节流路径绝不丢帧

    def test_process_frame_records_consume(self):
        _qapp()
        clip = WebMClip(str(Path("dummy.webm")))
        clip._w = 2
        clip._h = 2
        perfstats.enable()
        try:
            clip._process_frame((bytes(16), 5))
            clip._process_frame((bytes(16), 6))
        finally:
            perfstats.disable()
            clip.cleanup()
        snap = perfstats.snapshot()
        assert snap["webm.consume"]["count"] == 2

    def test_process_frame_disabled_no_records(self):
        _qapp()
        clip = WebMClip(str(Path("dummy.webm")))
        clip._w = 2
        clip._h = 2
        try:
            clip._process_frame((bytes(16), 5))
        finally:
            clip.cleanup()
        assert perfstats.snapshot() == {}

    def test_poll_empty_records_when_enabled(self):
        clip = WebMClip(str(Path("dummy.webm")))
        clip._queue = queue_mod.Queue(maxsize=8)  # 空队列
        perfstats.enable()
        try:
            clip._poll()
            clip._poll()
        finally:
            perfstats.disable()
            clip.cleanup()
        assert perfstats.snapshot()["webm.poll_empty"]["count"] == 2


# ================================================================ window 打点
class _FrameClip:
    """每帧内容可不同的极简 clip（含 frame 切换），记录 currentPixmap 次数。"""

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
    def __init__(self, clips):
        self._clips = dict(clips)
        self.no_mirror = set()

    def movie(self, name):
        return self._clips[name]

    def clip_path(self, name):
        return None

    def names(self):
        return list(self._clips)


class _CachePet:
    """只挂载 _rebuild_frame 的假窗口（与 test_frame_pixmap_cache 同构）。"""

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _frame_cache_key = window_mod.PetWindow._frame_cache_key
    _frame_content_fingerprint = window_mod.PetWindow._frame_content_fingerprint

    def __init__(self, movie, lib, scale=0.5, anim="idle", dpr=1.0):
        self.movie = movie
        self.lib = lib
        self.facing = "left"
        self.scale = scale
        self.anim = anim
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._frame_pixmap = None
        self._hit_alpha_image = None
        self._frame_key = None
        self._screen_dpr = dpr
        self._squash_active = False
        self._squash_progress = 1.0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def _sync_mask(self):
        pass  # mask 段单独用真实 _sync_mask 测（见 test_sync_mask_timing）


def _frame_image(variant: int) -> QImage:
    img = QImage(64, 36, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(variant * 30, 0, 10, 10, QColor(255, 0, 0, 255))
    p.end()
    return img


class TestWindowInstrumentation:
    def _pet(self, n_frames: int = 2):
        _qapp()
        clip = _FrameClip([_frame_image(i) for i in range(n_frames)])
        lib = _CacheLibrary({"idle": clip})
        return clip, _CachePet(clip, lib)

    def test_rebuild_frame_disabled_records_nothing(self):
        clip, pet = self._pet()
        window_mod.PetWindow._rebuild_frame(pet)
        window_mod.PetWindow._rebuild_frame(pet)
        assert pet._frame_cache is not None  # 缓存路径照常工作
        assert perfstats.snapshot() == {}

    def test_rebuild_frame_records_hit_miss_skip_and_scale(self):
        clip, pet = self._pet()
        perfstats.enable()
        try:
            window_mod.PetWindow._rebuild_frame(pet)  # 帧0：miss
            clip._frame_number = 1
            window_mod.PetWindow._rebuild_frame(pet)  # 帧1：miss
            clip._frame_number = 0
            window_mod.PetWindow._rebuild_frame(pet)  # 帧0：缓存命中
            window_mod.PetWindow._rebuild_frame(pet)  # 同帧：快路径跳过
        finally:
            perfstats.disable()
        snap = perfstats.snapshot()
        assert snap["rebuild.calls"]["count"] == 4
        assert snap["frame_cache.miss"]["count"] == 2
        assert snap["frame_cache.hit"]["count"] == 1
        assert snap["rebuild.skip"]["count"] == 1
        assert snap["rebuild.scale"]["count"] == 2  # 每次 miss 走整条转换链
        assert snap["rebuild.total"]["count"] == 4

    def test_sync_mask_timing_records_mask_segment(self, monkeypatch):
        """真实 _sync_mask（mask 段计时）挂在带最少属性的假窗口上运行。"""
        _qapp()
        fake = SimpleNamespace()
        fake.scale = 0.1
        fake._w = 64
        fake._h = 36
        fake._frame_pixmap = None
        fake._mask_bounds = None
        fake._collision_local_bounds = None
        fake.setMask = lambda mask: None
        fake.clearMask = lambda: None
        fake.mask = lambda: QRegion()
        monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="posix"))
        perfstats.enable()
        try:
            window_mod.PetWindow._sync_mask(fake)
            window_mod.PetWindow._sync_mask(fake)
        finally:
            perfstats.disable()
        snap = perfstats.snapshot()
        assert snap["rebuild.mask"]["count"] == 2
        assert snap["rebuild.mask"]["total"] >= 0

    def test_paint_event_records_draw(self):
        _qapp()

        class _PaintWidget(QWidget):
            paintEvent = window_mod.PetWindow.paintEvent  # noqa: N802

        w = _PaintWidget()
        w.scale = 0.5
        w._w = 64
        w._h = 36
        w._frame_pixmap = None
        perfstats.enable()
        try:
            w.grab()  # 同步触发 paintEvent
            w.grab()
        finally:
            perfstats.disable()
            w.close()
        snap = perfstats.snapshot()
        assert snap["paint.draw"]["count"] == 2

    def test_frame_cache_hit_rate_exposed_via_snapshot(self):
        """命中率 = frame_cache.hit / (hit + miss)，可从 snapshot 直接算出。"""
        clip, pet = self._pet()
        perfstats.enable()
        try:
            window_mod.PetWindow._rebuild_frame(pet)
            clip._frame_number = 1
            window_mod.PetWindow._rebuild_frame(pet)
            clip._frame_number = 0
            window_mod.PetWindow._rebuild_frame(pet)
        finally:
            perfstats.disable()
        snap = perfstats.snapshot()
        hits = snap["frame_cache.hit"]["count"]
        misses = snap["frame_cache.miss"]["count"]
        assert (hits, misses) == (1, 2)
        assert hits / (hits + misses) == pytest.approx(1 / 3)
