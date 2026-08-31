# -*- coding: utf-8 -*-
"""B7：WebMClip reader 生命周期受控（性能计划 B7）回归测试。

锁定四点（全部事件/join/有界条件等待同步，不用 sleep 猜时序）：
1. 快速连续 start/stop（Q 弹连点）不产生线程/ffmpeg 子进程泄漏；
2. stop() 主动 terminate 底层 ffmpeg：stop 后进程句柄确实退出；
3. 退役 reader 池有硬上限：池满且无法回收时拒绝启动新 reader；
4. cleanup() 在 reader 仍存活时不丢失追踪（保持记录，等待后续回收）。
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip_mod
from pet.webm_clip import WebMClip

SAMPLE_WEBM = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _alive_threads():
    return {t for t in threading.enumerate() if t.is_alive()}


class _TrackingClip(WebMClip):
    """记录每次 stop() 时仍在运行的 ffmpeg 进程句柄，用于断言进程全部退出。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.seen_procs = []
        self._seen_lock = threading.Lock()

    def stop(self):
        with self._seen_lock:
            if self._reader_proc is not None:
                self.seen_procs.append(self._reader_proc)
        super().stop()


class _StuckReaderClip(WebMClip):
    """reader 线程永不退出（模拟解码/管道卡死），用事件控制放行。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.reader_entered = threading.Event()
        self.reader_release = threading.Event()
        self.spawn_count = 0
        self._spawn_lock = threading.Lock()

    def _reader(self, stop_evt, generation, ready_evt=None):
        with self._spawn_lock:
            self.spawn_count += 1
        self.reader_entered.set()
        self.reader_release.wait()  # 卡死：绝不退出，直到测试放行


def test_rapid_start_stop_leaks_no_threads_or_processes(app):
    """Q 弹连点：连续 start/stop 不产生线程/子进程泄漏，退役池不超上限。"""
    assert SAMPLE_WEBM.exists(), f"WebM test file not found: {SAMPLE_WEBM}"
    clip = _TrackingClip(SAMPLE_WEBM)
    baseline = _alive_threads()

    # 阶段 A：不等待就立刻 stop（覆盖 stop 先于 ffmpeg 进程拉起的竞态路径）
    for _ in range(10):
        clip.start()
        clip.stop()
    # 阶段 B：等 reader 拉起并登记 ffmpeg 进程后再 stop（覆盖主动 terminate 路径）
    for _ in range(5):
        clip.start()
        assert clip._reader_ready.wait(5.0), "reader 未在时限内拉起 ffmpeg"
        clip.stop()

    clip.cleanup()
    app.processEvents()

    # 退役池不超过硬上限
    assert len(clip._retired) <= webm_clip_mod._MAX_RETIRED_READERS
    # 退役 reader 线程全部退出、其进程句柄全部退出
    for r in clip._retired:
        assert not r.thread.is_alive()
        if r.proc is not None:
            assert r.proc.poll() is not None
    # 所有被 stop 记录过的 ffmpeg 进程都已退出（无子进程泄漏）
    assert clip.seen_procs, "至少应捕获到阶段 B 的 ffmpeg 进程"
    assert all(p.poll() is not None for p in clip.seen_procs), \
        "stop() 后存在未退出的 ffmpeg 进程"

    # 无残留 reader 线程（含竞态路径下自终止的 reader）
    deadline = time.monotonic() + 5.0
    while True:
        new_alive = [t for t in threading.enumerate()
                     if t.is_alive() and t not in baseline]
        if not new_alive or time.monotonic() >= deadline:
            break
        app.processEvents()
    assert len(new_alive) == 0, f"残留线程: {new_alive}"


def test_stop_terminates_ffmpeg_process(app):
    """stop() 必须主动 terminate 底层 ffmpeg：进程句柄确实退出、reader 线程退出。"""
    assert SAMPLE_WEBM.exists()
    clip = _TrackingClip(SAMPLE_WEBM)
    baseline = _alive_threads()

    clip.start()
    assert clip._reader_ready.wait(5.0), "reader 未在时限内拉起 ffmpeg"
    proc = clip._reader_proc
    assert proc is not None, "reader 应持有 ffmpeg 进程句柄"
    assert proc.poll() is None, "播放中 ffmpeg 进程应存活"

    clip.stop()

    # stop() 后进程句柄退出（有界等待真实 OS 条件，非猜时序）
    deadline = time.monotonic() + 5.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert proc.poll() is not None, "stop() 后 ffmpeg 进程必须已退出"

    # reader 线程随之退出（join 事件同步）
    assert clip._retired, "stop() 应把 active reader 移入退役池"
    for r in clip._retired:
        r.thread.join(5.0)
        assert not r.thread.is_alive(), "stop() 后 reader 线程必须退出"

    # 无残留线程
    deadline = time.monotonic() + 5.0
    while True:
        new_alive = [t for t in threading.enumerate()
                     if t.is_alive() and t not in baseline]
        if not new_alive or time.monotonic() >= deadline:
            break
        app.processEvents()
    assert len(new_alive) == 0, f"残留线程: {new_alive}"
    clip.cleanup()


def test_retired_pool_cap_blocks_new_reader_when_stuck(app):
    """退役池满且无法回收（卡死 reader）时，拒绝启动新 reader（硬上限生效）。"""
    clip = _StuckReaderClip(SAMPLE_WEBM)
    clip.start()
    assert clip.reader_entered.wait(5.0), "卡死 reader 必须已进入"

    clip.stop()  # 退役卡死 reader（线程不退出）
    assert len(clip._retired) == 1

    # 池满（1 个存活退役 reader）→ 拒绝启动新 reader，不产生累积
    assert clip.start() is False, "退役池未清空时 start() 必须返回失败状态（可观测）"
    assert clip._thread is None, "退役池未清空时不得启动新 reader"
    assert clip._running is False
    assert clip.spawn_count == 1, "卡死场景下不得再拉起新 reader"
    assert len(clip._retired) == webm_clip_mod._MAX_RETIRED_READERS

    # 放行后线程退出，池可回收
    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    assert not clip._retired[0].thread.is_alive()
    clip.cleanup()
    assert len(clip._retired) == 0
    app.processEvents()


def test_cleanup_keeps_tracking_alive_reader(app):
    """cleanup() 在 reader 仍存活时不得丢失追踪（保持记录，等待后续回收）。"""
    clip = _StuckReaderClip(SAMPLE_WEBM)
    clip.start()
    assert clip.reader_entered.wait(5.0), "卡死 reader 必须已进入"

    clip.cleanup()  # 卡死 reader 无法回收：必须保留追踪，不得静默丢弃
    assert len(clip._retired) == 1, "cleanup 不得丢失存活 reader 的追踪"
    assert clip._retired[0].thread.is_alive()

    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    assert not clip._retired[0].thread.is_alive()
    clip.cleanup()
    assert len(clip._retired) == 0
    app.processEvents()


class _FakeProc:
    """模拟 Popen：terminate 不退出、kill 才退出（病态 ffmpeg 的 P2 兜底路径）。"""

    def __init__(self, *, already_exited: bool = False, exit_on_terminate: bool = True):
        self._dead = already_exited
        self.exit_on_terminate = exit_on_terminate
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.pid = id(self)

    def poll(self):
        return None if not self._dead else 1

    def terminate(self):
        self.terminated = True
        if self.exit_on_terminate:
            self._dead = True

    def kill(self):
        self.killed = True
        self._dead = True

    def wait(self, timeout=None):
        self.waits += 1
        if self._dead:
            return 1
        raise subprocess.TimeoutExpired(self, timeout)


def test_terminate_proc_escalates_to_kill_when_terminate_times_out(app):
    """P2：terminate 超时（进程不退出）时必须 kill 兜底，且进程最终确认退出。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeProc(exit_on_terminate=False)  # terminate 后仍存活 → 必须强杀
    webm_clip_mod.WebMClip._terminate_proc(proc)
    assert proc.terminated is True, "必须先尝试 terminate"
    assert proc.killed is True, "terminate 超时后必须 kill 兜底"
    assert proc.poll() is not None, "terminate+kill 后进程必须确认退出"
    assert proc.waits >= 2, "terminate 后、kill 后各应有一次有界等待"
    clip.cleanup()
    app.processEvents()


def test_terminate_proc_is_noop_for_exited_and_none_proc(app):
    """P2：已退出进程与 None 进程不得触发 terminate/kill。"""
    clip = WebMClip("dummy.webm")
    exited = _FakeProc(already_exited=True)
    webm_clip_mod.WebMClip._terminate_proc(exited)
    assert exited.terminated is False and exited.killed is False
    webm_clip_mod.WebMClip._terminate_proc(None)  # 不得抛异常
    clip.cleanup()
    app.processEvents()


def test_cleanup_keeps_tracking_via_module_lifecycle_manager(app):
    """P2：cleanup 后 clip 自身不再调度 sweep；存活 reader 的追踪由模块级
    生命周期管理器持有（不随 clip GC 丢弃，也与 reader 收尾不竞态）。"""
    clip = _StuckReaderClip(SAMPLE_WEBM)
    clip.start()
    assert clip.reader_entered.wait(5.0), "卡死 reader 必须已进入"

    clip.stop()  # 退役卡死 reader
    assert len(clip._retired) == 1
    clip.cleanup()
    assert clip._cleaned is True
    # 追踪保留：模块级管理器持有该 clip（不随 cleanup 丢弃）
    assert clip in webm_clip_mod._ORPHANED_CLIPS, "cleanup 后存活 reader 的追踪必须由管理器持有"

    # 手动触发一次模块回收：卡死 reader 仍在 → 继续持有（不静默丢弃）
    webm_clip_mod._reap_orphaned_clips()
    assert clip in webm_clip_mod._ORPHANED_CLIPS
    assert clip._retired[0].thread.is_alive()

    # reader 退出后：回收清空退役池 → 管理器释放该 clip
    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    webm_clip_mod._reap_orphaned_clips()
    assert len(clip._retired) == 0
    assert clip not in webm_clip_mod._ORPHANED_CLIPS, "退役池清空后管理器必须释放追踪"
    app.processEvents()


def test_start_after_cleanup_is_rejected(app):
    """cleanup 后 clip 已终结：start() 必须返回失败，不得复活。"""
    clip = _StuckReaderClip(SAMPLE_WEBM)
    clip.start()
    assert clip.reader_entered.wait(5.0)
    clip.cleanup()
    assert clip.start() is False, "cleanup 后不得重新启动"
    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    clip.cleanup()
    assert clip not in webm_clip_mod._ORPHANED_CLIPS
    app.processEvents()
