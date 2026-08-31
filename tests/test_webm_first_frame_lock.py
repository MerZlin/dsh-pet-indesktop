# -*- coding: utf-8 -*-
"""N4：WebMClip 首帧解码原子认领（每实例锁）的回归测试。

warm_first_frame()（后台预热）与 _decode_first_frame_sync()（前台首次播放）
旧实现是非原子 check-then-act：可能并发解码同一文件（两个 ffmpeg 进程）。

锁定三点：
1. 同一时间只有一个首帧解码执行者：并发 warm_first_frame 认领失败的立即放弃；
2. 前台同步解码在后台预热进行中时，等待后台完成并复用其缓存（不重复解码）；
3. 前台同步解码绝不被后台预热长时间卡住：后台卡死时前台在有限等待后
   放弃等待、直接自行解码（允许短暂双解码的逃生口，但不死锁）。

全部用事件/锁同步，不用 sleep 猜时序。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip_mod
from pet.webm_clip import WebMClip


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class BlockingDecodeClip(WebMClip):
    """每次 _decode_first_qimage 都阻塞直到放行，记录解码调用次数。

    返回有效 QImage：解码完成后可被前台同步路径复用为缓存。
    """

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.decode_entered = threading.Event()
        self.decode_release = threading.Event()
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self):
        with self._counter_lock:
            self.decode_count += 1
        self.decode_entered.set()
        self.decode_release.wait(5.0)
        return QImage(2, 2, QImage.Format.Format_RGBA8888)


class OneShotBlockingClip(WebMClip):
    """第一次 _decode_first_qimage 阻塞（模拟后台预热卡住），后续调用立即返回。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.first_entered = threading.Event()
        self.first_release = threading.Event()
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self):
        with self._counter_lock:
            self.decode_count += 1
            n = self.decode_count
        if n == 1:
            self.first_entered.set()
            self.first_release.wait(5.0)
        return None


def test_warm_first_frame_single_executor_atomic_claim(app):
    """同一时间只能有一个首帧解码执行者：第二个并发 warm 认领失败立即放弃。"""
    clip = BlockingDecodeClip("dummy.webm")
    t1 = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t1.start()
    assert clip.decode_entered.wait(5.0), "第一个执行者必须已认领并进入解码"

    clip.warm_first_frame()  # 第二个并发 warm：认领失败 → 不进入解码、不等待
    assert clip.decode_count == 1, "并发首帧解码必须原子认领，不得双执行"

    clip.decode_release.set()
    t1.join(5.0)
    assert clip.decode_count == 1
    assert clip._first_image is not None, "后台解码完成应写入首帧缓存"
    clip.cleanup()
    app.processEvents()


def test_sync_decode_waits_for_background_warm_then_uses_cache(app):
    """前台同步解码在后台预热进行中时：等待其完成并复用缓存，不重复解码。"""
    clip = BlockingDecodeClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.decode_entered.wait(5.0), "后台预热必须已认领并进入解码（持有锁）"

    clip.decode_release.set()  # 放行后台：它完成解码后 set 完成事件
    clip._decode_first_frame_sync()  # 前台认领失败 → 等后台完成 → 直接用其缓存
    t.join(5.0)

    assert clip.decode_count == 1, "前台必须复用后台缓存，不得重复解码"
    assert clip._first_image is not None
    assert clip._current_pixmap is not None, "前台同步路径必须拿到可显示首帧"
    clip.cleanup()
    app.processEvents()


def test_sync_decode_abandons_wait_when_background_stuck(app):
    """前台绝不被后台预热长时间卡住：后台卡死时前台有限等待后自行解码。"""
    wait_ms = webm_clip_mod._FIRST_FRAME_SYNC_WAIT_MS
    clip = OneShotBlockingClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.first_entered.wait(5.0), "后台第一次解码必须已卡住（持有锁）"

    started = time.monotonic()
    clip._decode_first_frame_sync()  # 前台：等待超时 → 放弃等待直接自行解码
    elapsed = time.monotonic() - started
    assert elapsed < (wait_ms / 1000.0) + 0.5, "前台等待后台预热的时间必须有界"
    assert clip.decode_count == 2, "后台卡死时前台自行解码（短暂双解码是允许的逃生口）"

    clip.first_release.set()  # 后台可正常收尾，互不阻塞
    t.join(5.0)
    clip.cleanup()
    app.processEvents()
