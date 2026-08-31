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

import subprocess
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


class _FakeDecodeProc:
    """模拟首帧解码拉起的 ffmpeg 进程句柄：terminate 不退出、kill 才退出。"""

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
        raise subprocess.TimeoutExpired(self, timeout)


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

    def _decode_first_qimage(self, gen=None):
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

    def _decode_first_qimage(self, gen=None):
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


class _WarmBlockingClip(WebMClip):
    """每次 _decode_first_qimage 都阻塞直到放行（模拟在飞预热解码）。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.decode_entered = threading.Event()
        self.decode_release = threading.Event()
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self, gen=None):
        with self._counter_lock:
            self.decode_count += 1
        self.decode_entered.set()
        self.decode_release.wait(5.0)
        return QImage(2, 2, QImage.Format.Format_RGBA8888)


class _FailThenSuccessClip(WebMClip):
    """第一次解码阻塞后失败（模拟后台预热卡住后失败），后续解码成功。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.first_entered = threading.Event()
        self.first_release = threading.Event()
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self, gen=None):
        with self._counter_lock:
            self.decode_count += 1
            n = self.decode_count
        if n == 1:
            self.first_entered.set()
            self.first_release.wait(5.0)
            return None  # 后台解码失败
        return QImage(2, 2, QImage.Format.Format_RGBA8888)


def test_cancel_first_frame_warm_terminates_procs_and_bumps_generation(app):
    """P1-2：cancel_first_frame_warm 必须 terminate 登记的首帧 ffmpeg 进程并换代。"""
    clip = WebMClip("dummy.webm")
    procs = [_FakeDecodeProc(), _FakeDecodeProc()]
    with clip._reader_lock:
        clip._first_frame_procs.update(procs)
    gen_before = clip._first_frame_gen

    clip.cancel_first_frame_warm()

    assert clip._first_frame_gen == gen_before + 1, "取消必须换代使在飞结果作废"
    assert all(p.terminated for p in procs), "取消必须 terminate 在飞首帧 ffmpeg 进程"
    assert all(p.poll() is not None for p in procs), "terminate+kill 后进程必须退出"
    assert clip._first_frame_procs == set(), "取消后登记集合必须清空"
    clip.cleanup()
    app.processEvents()


def test_cleanup_cancels_inflight_warm_and_discards_result(app):
    """P1-2：cleanup 取消在飞首帧预热；被取消的预热结果不得写入缓存。"""
    clip = _WarmBlockingClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.decode_entered.wait(5.0), "预热必须已进入解码（持有锁）"

    clip.cleanup()  # 取消在飞预热（换代 + terminate）
    clip.decode_release.set()  # 放行解码：结果须被代次检查丢弃
    t.join(5.0)

    assert clip._first_image is None, "被取消的预热结果不得提交缓存"
    assert clip._cleaned is True
    app.processEvents()


def test_sync_escape_commits_atomically_under_lock(app):
    """P1-3：逃生口（超时自行解码）的缓存提交必须回到锁内原子完成。"""
    clip = _WarmBlockingClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.decode_entered.wait(5.0), "后台预热必须已进入解码（持有锁）"

    sync_done = threading.Event()

    def _sync():
        clip._decode_first_frame_sync()
        sync_done.set()

    sync_thread = threading.Thread(target=_sync, daemon=True)
    sync_thread.start()
    # 前台等待超时后自行解码（逃生口）——双解码进行中
    deadline = time.monotonic() + 5.0
    while clip.decode_count < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert clip.decode_count == 2, "后台卡住时前台必须自行解码（逃生口）"

    clip.decode_release.set()  # 放行两个解码：两者都完成，缓存只提交一次
    sync_thread.join(5.0)
    t.join(5.0)

    assert clip._first_image is not None, "至少一个解码成功则缓存必须被提交"
    assert clip._first_frame_done.is_set(), "提交必须与完成事件一致"
    assert clip._current_pixmap is not None, "前台同步路径必须拿到可显示首帧"
    clip.cleanup()
    app.processEvents()


def test_sync_escape_commit_wins_when_background_fails(app):
    """P1-3：后台解码失败、前台逃生口解码成功时，最终缓存必须是前台的成果。"""
    wait_ms = webm_clip_mod._FIRST_FRAME_SYNC_WAIT_MS
    clip = _FailThenSuccessClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.first_entered.wait(5.0), "后台第一次解码必须已卡住（持有锁）"

    started = time.monotonic()
    clip._decode_first_frame_sync()  # 前台：超时 → 自行解码成功 → 锁内提交
    elapsed = time.monotonic() - started
    assert elapsed < (wait_ms / 1000.0) + 0.5, "前台等待后台预热的时间必须有界"
    assert clip.decode_count == 2, "后台卡住时前台必须自行解码（逃生口）"
    assert clip._first_image is not None, "前台成功解码必须提交缓存"
    assert clip._current_pixmap is not None, "前台必须应用首帧"

    clip.first_release.set()  # 后台收尾：返回 None，不得覆盖前台成功结果
    t.join(5.0)
    assert clip._first_image is not None, "后台失败不得覆盖前台成功结果"
    clip.cleanup()
    app.processEvents()
