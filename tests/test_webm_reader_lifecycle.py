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


def test_start_never_blocks_gui_when_retired_reader_stuck(app):
    """退役池有存活卡死 reader 时 start() 也不得阻塞 GUI（零等待回收）。

    原设计：池未清空就拒绝启动新 reader（B7 一审）。实测回归：join 等待在
    GUI 线程造成连点/快速切换卡顿。现契约：start 永远零等待推进，卡死的
    退役 reader 由模块级管理器追踪回收，累积只记日志不阻塞。"""
    clip = _StuckReaderClip(SAMPLE_WEBM)
    clip.start()
    assert clip.reader_entered.wait(5.0), "卡死 reader 必须已进入"

    clip.stop()  # 退役卡死 reader（线程不退出）
    assert len(clip._retired) == 1

    import time as _t
    t0 = _t.monotonic()
    assert clip.start() is True, "退役池有存活 reader 也必须正常启动（不阻塞）"
    assert _t.monotonic() - t0 < 0.2, "start() 绝不做有界 join（零等待）"
    assert clip._running is True
    assert clip.spawn_count == 2, "新 reader 正常拉起"
    assert len(clip._retired) == 1, "卡死的旧 reader 保留追踪（不丢、不泄漏）"

    # 放行后线程退出，cleanup 回收
    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    clip.stop()
    clip.cleanup()
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


# ============================================================================
# 批 6-8b 收尾：try-acquire 超时跳过的最终保障（P1 盲审两项）
# ============================================================================
def test_reap_retired_confirms_and_kills_proc_after_reader_exit(app):
    """P1：reader finally 之外的兜底确认——退役线程已退出（finally 已完整
    执行）但进程句柄仍存活（_terminate_proc/gen.close 异常被吞的病态路径）
    时，_reap_retired 补杀并确认退出，绝不静默丢失句柄。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeProc()  # 存活：poll() is None
    done = threading.Event()

    def _target():
        done.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(5.0)
    assert not t.is_alive(), "测试前提：退役线程已退出"

    clip._retired.append(webm_clip_mod._Reader(thread=t, proc=proc))
    clip._reap_retired(join_timeout=0)

    assert proc.poll() is not None, "兜底确认必须补杀仍存活的进程"
    assert clip._retired == [], "已退出且已确认的退役记录应被丢弃"
    clip.cleanup()
    app.processEvents()


def test_unblock_proc_timeout_skip_still_killed_by_owner_finally(app):
    """P1：_unblock_proc 的 try-acquire 超时跳过是有界的，且锁持有者
    （reader finally）随后仍会执行终止——「超时跳过绝不漏杀」的最终保障链
    闭合（owner 杀进程为主保证，退役池 sweep 兜底确认）。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeProc()
    entered = threading.Event()
    release = threading.Event()

    def _owner_in_finally():
        with clip._proc_lock:  # 模拟 reader finally 持锁收尾
            entered.set()
            release.wait(5.0)
            webm_clip_mod.WebMClip._terminate_proc(proc)  # owner 的主保证

    t = threading.Thread(target=_owner_in_finally, daemon=True)
    t.start()
    assert entered.wait(5.0), "owner 必须已持锁"

    t0 = time.monotonic()
    clip._unblock_proc(proc)  # 锁被持有 → 有界超时跳过
    elapsed = time.monotonic() - t0
    assert proc.terminated is False, "GUI 不得在锁被持有时操作同一 Popen"
    assert elapsed < 1.0, "超时跳过必须有界（≈_PROC_LOCK_ACQUIRE_TIMEOUT）"
    assert elapsed >= 0.15, "确实走了超时跳过路径（等满 0.2s 锁等待）"

    release.set()
    t.join(5.0)
    assert proc.poll() is not None, "owner finally 必须杀进程（主保证）"
    clip.cleanup()
    app.processEvents()


class _FakeReaderGen:
    """模拟 imageio read_frames 生成器：记录 close() 时进程是否存活。

    close() 模仿 imageio-ffmpeg 0.6.0 的 finally：先 poll 判活（短路点），
    进程仍存活则 kill 兜底（对应关管道 + 1.5s 轮询 + kill 的清理块）。
    """

    def __init__(self, proc, meta):
        self._proc = proc
        self._meta = meta
        self._stage = 0
        self.closed = False
        self.saw_alive_at_close = None

    def __next__(self):
        if self._stage == 0:
            self._stage = 1
            return self._meta
        self._stage = 2
        raise StopIteration  # 模拟自然播完（无更多帧）

    def close(self):
        self.closed = True
        self.saw_alive_at_close = self._proc.poll() is None
        if self.saw_alive_at_close:
            self._proc.kill()  # 模拟 imageio finally 的存活进程清理


def test_reader_finally_short_circuits_gen_close_when_proc_captured(app, monkeypatch):
    """P1/Fix2-路径A：句柄已捕获时先 _terminate_proc 再 gen.close()——close
    内部 poll 判死（saw_alive_at_close=False），跳过 imageio 的存活进程清理
    （1.5s 轮询）路径（短路成立，锁持有上界 = terminate 时间）。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeProc()
    meta = {"fps": 24.0, "duration": 1.0}
    gen = _FakeReaderGen(proc, meta)

    def _fake_read_frames(*args, **kwargs):
        cap = webm_clip_mod._PopenCapture._local.capture
        cap._on_process(proc, ["ffmpeg", "-i", "dummy.webm"])  # 正常登记
        return gen

    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", _fake_read_frames)
    clip._generation = 1
    clip._reader(threading.Event(), generation=1)

    assert proc.poll() is not None, "reader finally 必须终止进程"
    assert gen.closed is True, "gen.close() 必须被调用"
    assert gen.saw_alive_at_close is False, \
        "先杀后 close：close 时必须看到进程已死（短路，无 1.5s 轮询）"
    clip.cleanup()
    app.processEvents()


def test_reader_finally_gen_close_kills_when_proc_capture_failed(app, monkeypatch):
    """P1/Fix2-路径B：proc 句柄捕获失败（capture 未看到进程）时收尾退化为
    只调 gen.close()——imageio close 是最后杀手（poll 判活 → kill 兜底），
    进程仍必被终止（该路径不短路，病态锁持有可达 ~1.5s，为代价上界）。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeProc()
    meta = {"fps": 24.0, "duration": 1.0}
    gen = _FakeReaderGen(proc, meta)

    def _fake_read_frames(*args, **kwargs):
        return gen  # 不触发 capture 登记 → 句柄捕获失败（proc 保持 None）

    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", _fake_read_frames)
    clip._generation = 1
    clip._reader(threading.Event(), generation=1)

    assert gen.closed is True, "句柄捕获失败也必须 close 生成器"
    assert gen.saw_alive_at_close is True, "路径 B：close 时进程仍存活（未短路）"
    assert proc.poll() is not None, "gen.close() 必须兜底杀进程"
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
    assert clip in webm_clip_mod._ORPHAN_REGISTRY.holders(), "cleanup 后存活 reader 的追踪必须由管理器持有"

    # 手动触发一次模块回收：卡死 reader 仍在 → 继续持有（不静默丢弃）
    webm_clip_mod._reap_orphaned_clips()
    assert clip in webm_clip_mod._ORPHAN_REGISTRY.holders()
    assert clip._retired[0].thread.is_alive()

    # reader 退出后：回收清空退役池 → 管理器释放该 clip
    clip.reader_release.set()
    clip._retired[0].thread.join(5.0)
    webm_clip_mod._reap_orphaned_clips()
    assert len(clip._retired) == 0
    assert clip not in webm_clip_mod._ORPHAN_REGISTRY.holders(), "退役池清空后管理器必须释放追踪"
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
    assert clip not in webm_clip_mod._ORPHAN_REGISTRY.holders()
    app.processEvents()
