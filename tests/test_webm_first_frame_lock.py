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

B7 复审（R2）遗留修复：
4. 首帧进程登记与取消的竞态窗口闭掉：登记回调在锁内复查代次/cleanup，
   迟到登记（取消后）的进程必须自终止且不得进 _first_frame_procs；
5. 同步逃生解码纳入代次取消语义：在飞逃生解码被取消后结果作废；
6. 逃生口缓存提交单胜者化：拿不到锁绝不无锁 check-then-store，只把
   图像直接应用到当前画面，缓存提交只可能由持锁方完成。
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


def test_sync_escape_commit_deferred_when_lock_unavailable(app):
    """P1-3/R2：逃生口拿不到锁时放弃写缓存（单胜者=持锁方），只把图像直接
    应用；后台（持锁方）失败时缓存保持空，前台画面仍可显示。"""
    wait_ms = webm_clip_mod._FIRST_FRAME_SYNC_WAIT_MS
    clip = _FailThenSuccessClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.first_entered.wait(5.0), "后台第一次解码必须已卡住（持有锁）"

    started = time.monotonic()
    clip._decode_first_frame_sync()  # 前台：超时 → 自行解码成功 → 锁不可用 → 只应用不写缓存
    elapsed = time.monotonic() - started
    assert elapsed < (wait_ms / 1000.0) + 0.5, "前台等待后台预热的时间必须有界"
    assert clip.decode_count == 2, "后台卡住时前台必须自行解码（逃生口）"
    assert clip._first_image is None, "锁不可用时逃生口不得写缓存（单胜者=持锁方）"
    assert clip._current_pixmap is not None, "前台逃生解码结果必须直接应用到当前画面"

    clip.first_release.set()  # 后台收尾：返回 None（失败）→ 缓存保持空
    t.join(5.0)
    assert clip._first_image is None, "后台失败时缓存必须保持空（无人提交）"
    clip.cleanup()
    app.processEvents()


def test_escape_commits_never_write_cache_without_lock(app):
    """P1-3/R2：多个逃生提交者在锁不可用时都不写缓存（无 check-then-store
    竞态，缓存胜者不再取决于调度顺序）；提交只由持锁方（后台）完成。"""
    clip = _WarmBlockingClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.decode_entered.wait(5.0), "后台预热必须已持锁卡住"

    done = threading.Event()

    def _sync():
        clip._decode_first_frame_sync()
        done.set()

    s1 = threading.Thread(target=_sync, daemon=True)
    s2 = threading.Thread(target=_sync, daemon=True)
    s1.start()
    s2.start()
    deadline = time.monotonic() + 5.0
    while clip.decode_count < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert clip.decode_count == 3, "后台 1 次 + 两个逃生口各 1 次解码"
    assert clip._first_image is None, "锁不可用期间不得有任何无锁写缓存"
    s1.join(5.0)
    s2.join(5.0)
    assert clip._current_pixmap is not None, "逃生口必须把解码结果应用到当前画面"

    clip.decode_release.set()  # 后台（持锁方）完成 → 提交缓存
    t.join(5.0)
    assert clip._first_image is not None, "持锁方完成后缓存必须被提交"
    assert clip._first_frame_done.is_set(), "提交必须与完成事件一致"
    clip.cleanup()
    app.processEvents()


# ============================================================================
# B7 复审（R2）遗留 3：首帧进程登记/取消竞态 + 同步逃生代次取消
# ============================================================================
class _FakePopenCapture:
    """替换 _PopenCapture：单例实例，捕获 on_process 登记回调，测试可精确
    控制「Popen 已创建但登记尚未完成」的竞态窗口。"""

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


def _fake_read_frames(proc, frame_bytes, meta, clip, observed=None):
    """模拟 imageio_ffmpeg.read_frames：经 capture 回调拉起「解码进程」登记，
    等待 proceed 放行（测试控制登记时机）；observed 记录登记发生时进程
    是否已在 _first_frame_procs 中。"""

    def _read_frames(*args, **kwargs):
        cap = _FakePopenCapture.current
        cap.proceed.wait(5.0)
        cap.on_process(proc, ["ffmpeg", "-i", "dummy.webm"])
        if observed is not None:
            observed.append(proc in clip._first_frame_procs)
        yield meta
        yield frame_bytes

    return _read_frames


def _install_fake_decode(monkeypatch, clip, proc, observed=None):
    """安装假 capture/read_frames，返回 capture 实例；frame 数据用真实尺寸。"""
    frame_bytes = bytes(clip._w * clip._h * clip._bpp)
    meta = {"fps": 24.0, "duration": 1.0}
    cap = _FakePopenCapture()
    fake = _fake_read_frames(proc, frame_bytes, meta, clip, observed)
    monkeypatch.setattr(webm_clip_mod, "_PopenCapture", _FakePopenCapture)
    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", fake)
    return cap


def test_first_frame_proc_registered_then_unregistered(app, monkeypatch):
    """P1-2/R2：首帧解码进程经 capture 登记进 _first_frame_procs（供取消
    主动 terminate）；解码结束（finally）即从集合移除。"""
    clip = WebMClip("dummy.webm")
    gen = clip._first_frame_gen
    proc = _FakeDecodeProc()
    observed = []
    cap = _install_fake_decode(monkeypatch, clip, proc, observed)
    result = {}

    def _decode():
        result["img"] = clip._decode_first_qimage(gen=gen)

    t = threading.Thread(target=_decode, daemon=True)
    t.start()
    assert cap.entered.wait(5.0), "capture 必须已进入"
    cap.proceed.set()  # 放行登记回调
    t.join(5.0)

    assert result["img"] is not None, "正常解码应成功"
    assert observed == [True], "解码期间进程必须已登记进 _first_frame_procs"
    assert proc not in clip._first_frame_procs, "解码结束后进程必须从集合移除"
    assert proc.terminated is False and proc.killed is False, "正常解码进程不得被终止"
    clip.cleanup()
    app.processEvents()


def test_first_frame_cancel_before_register_terminates_stale_proc(app, monkeypatch):
    """P1-2/R2：取消发生在「Popen 已创建、登记尚未完成」窗口内时，迟到的
    登记必须在锁内复查代次并自终止进程——已取消的进程绝不漏进集合。"""
    clip = WebMClip("dummy.webm")
    gen = clip._first_frame_gen
    proc = _FakeDecodeProc()
    cap = _install_fake_decode(monkeypatch, clip, proc)
    result = {}

    def _decode():
        result["img"] = clip._decode_first_qimage(gen=gen)

    t = threading.Thread(target=_decode, daemon=True)
    t.start()
    assert cap.entered.wait(5.0), "capture 必须已进入"
    # 竞态窗口：Popen 已创建、_register 尚未执行 → 此刻取消（换代 + 清集合）
    clip.cancel_first_frame_warm()
    cap.proceed.set()  # 放行 → 迟到登记执行
    t.join(5.0)

    assert proc.terminated or proc.killed, "迟到的登记必须自终止已取消进程"
    assert proc.poll() is not None, "自终止必须确认进程退出"
    assert proc not in clip._first_frame_procs, "已取消进程不得登记进集合"
    assert result["img"] is None, "取消后解码结果作废"
    clip.cleanup()
    app.processEvents()


def test_cancel_timeout_skip_registers_unconfirmed_and_sweep_kills(app, monkeypatch):
    """批 6-8b 收尾 P1：cancel_first_frame_warm 的 try-acquire 超时跳过不再是
    「无条件安全」——跳过时进程必须登记进 _unconfirmed_procs 重试机制，孤儿
    注册表 sweep 在 owner 释放 _ff_proc_lock 后确认/补杀（进程最终退出）。"""
    clip = WebMClip("dummy.webm")
    proc = _FakeDecodeProc()
    with clip._reader_lock:
        clip._first_frame_procs.add(proc)
    monkeypatch.setattr(webm_clip_mod, "_PROC_LOCK_ACQUIRE_TIMEOUT", 0.05)

    with clip._ff_proc_lock:  # 模拟解码线程正在 finally 的 g.close()（持锁）
        clip.cancel_first_frame_warm()  # 超时跳过 → 登记 unconfirmed

    assert proc.terminated is False, "持锁期间取消不得操作 Popen"
    assert clip._unconfirmed_procs, "超时跳过的进程必须登记进重试机制"
    assert proc in [e[0] for e in clip._unconfirmed_procs], "登记必须携带进程句柄"

    # owner 释放锁后：sweep 补杀确认
    webm_clip_mod._reap_orphaned_clips()
    assert proc.terminated or proc.killed, "sweep 必须补杀未确认退出的进程"
    assert proc.poll() is not None, "补杀必须确认进程退出"
    assert clip._unconfirmed_procs == [], "确认后登记列表必须清空"
    clip.cleanup()
    app.processEvents()


class _SyncEscapeCancelClip(WebMClip):
    """第一次解码阻塞（后台 warm 卡住持锁）；第二次解码阻塞（逃生口在飞），
    放行后返回有效 QImage——用于验证取消期间完成的逃生结果作废。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.bg_entered = threading.Event()
        self.bg_release = threading.Event()
        self.escape_entered = threading.Event()
        self.escape_release = threading.Event()
        self.decode_count = 0
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self, gen=None):
        with self._counter_lock:
            self.decode_count += 1
            n = self.decode_count
        if n == 1:
            self.bg_entered.set()
            self.bg_release.wait(5.0)
            return QImage(2, 2, QImage.Format.Format_RGBA8888)
        self.escape_entered.set()
        self.escape_release.wait(5.0)
        return QImage(3, 3, QImage.Format.Format_RGBA8888)


def test_sync_escape_result_voided_by_cancel(app):
    """P1-2/R2：同步逃生解码在飞期间发生取消（换代）→ 逃生结果作废，
    不得写入缓存（与后台 warm 同等的代次取消语义）。"""
    clip = _SyncEscapeCancelClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.bg_entered.wait(5.0), "后台预热必须已持锁卡住"

    sync_done = threading.Event()

    def _sync():
        clip._decode_first_frame_sync()
        sync_done.set()

    st = threading.Thread(target=_sync, daemon=True)
    st.start()
    assert clip.escape_entered.wait(5.0), "前台逃生解码必须已进入在飞"

    clip.cancel_first_frame_warm()  # 取消：换代
    clip.escape_release.set()  # 逃生解码完成 → 代次检查作废
    st.join(5.0)
    clip.bg_release.set()
    t.join(5.0)

    assert clip._first_image is None, "取消期间完成的逃生结果不得污染缓存"
    assert clip._first_frame_done.is_set() is False
    app.processEvents()


# ============================================================================
# 批 6-8b R3（R2 复审 P1 闭合）：_sweep_unconfirmed_procs 补杀失败保留追踪
# + 有界重试 + 达上限告警标注放弃（绝不静默丢句柄）
# ============================================================================
class _UnkillableDecodeProc:
    """模拟首帧解码进程：terminate/kill 全部执行但进程永不退出（kill 无效）。"""

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.pid = id(self)

    def poll(self):
        return None  # 永远存活

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits += 1
        raise subprocess.TimeoutExpired(self, timeout)


class _PollRaisingDecodeProc:
    """模拟 poll 抛异常的进程句柄（Windows 句柄失效等病态）。"""

    def __init__(self):
        self.pid = id(self)
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        raise OSError("handle invalid")

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 1


def test_sweep_unconfirmed_keeps_tracking_when_proc_unkillable(app, monkeypatch):
    """R2 复审 P1 闭合：拿到 _ff_proc_lock 后补杀失败（kill 后仍存活）不得
    一次即丢条目——保留追踪并累计 attempts，后续 sweep 可再次补杀。"""
    clip = WebMClip("dummy.webm")
    proc = _UnkillableDecodeProc()
    with clip._reader_lock:
        clip._unconfirmed_procs.append([proc, 0, False])
    webm_clip_mod._register_orphan(clip)
    monkeypatch.setattr(webm_clip_mod, "_PROC_LOCK_ACQUIRE_TIMEOUT", 0.05)

    webm_clip_mod._reap_orphaned_clips()

    entries = list(clip._unconfirmed_procs)
    assert len(entries) == 1, "补杀失败必须保留条目（不得移出追踪）"
    assert entries[0][0] is proc
    assert entries[0][1] == 1, "失败必须累计 attempts"
    assert entries[0][2] is False, "未达上限不得标注放弃"
    assert proc.terminated is True and proc.killed is True, "补杀必须已执行"
    assert proc.poll() is None, "进程仍存活（未被误判为已退出）"

    # 第二次 sweep 仍可再次补杀（追踪不丢）
    webm_clip_mod._reap_orphaned_clips()
    entries = list(clip._unconfirmed_procs)
    assert entries[0][1] == 2

    clip.cleanup()
    clip._unconfirmed_procs = []
    webm_clip_mod._ORPHAN_REGISTRY._clips.discard(clip)
    app.processEvents()


def test_sweep_unconfirmed_keeps_tracking_when_poll_raises(app, monkeypatch):
    """R2 复审 P1 闭合：poll 异常（无法确认退出）时条目必须保留追踪并累计
    attempts（绝不一次即丢）。"""
    clip = WebMClip("dummy.webm")
    proc = _PollRaisingDecodeProc()
    with clip._reader_lock:
        clip._unconfirmed_procs.append([proc, 0, False])
    webm_clip_mod._register_orphan(clip)
    monkeypatch.setattr(webm_clip_mod, "_PROC_LOCK_ACQUIRE_TIMEOUT", 0.05)

    webm_clip_mod._reap_orphaned_clips()

    entries = list(clip._unconfirmed_procs)
    assert len(entries) == 1, "poll 异常：必须保留条目"
    assert entries[0][1] == 1
    assert entries[0][2] is False
    assert proc.poll_calls >= 1

    clip.cleanup()
    clip._unconfirmed_procs = []
    webm_clip_mod._ORPHAN_REGISTRY._clips.discard(clip)
    app.processEvents()


def test_sweep_unconfirmed_abandons_after_retry_limit(app, monkeypatch):
    """R2 复审 P1 闭合：未确认退出达到重试上限时告警并标注 abandoned——
    条目保留在追踪中但不再重试（绝不静默丢弃句柄）。"""
    clip = WebMClip("dummy.webm")
    proc = _UnkillableDecodeProc()
    with clip._reader_lock:
        clip._unconfirmed_procs.append([proc, 0, False])
    webm_clip_mod._register_orphan(clip)
    monkeypatch.setattr(webm_clip_mod, "_PROC_LOCK_ACQUIRE_TIMEOUT", 0.01)

    limit = webm_clip_mod._UNCONFIRMED_KILL_MAX
    for _ in range(limit):
        webm_clip_mod._reap_orphaned_clips()

    entries = list(clip._unconfirmed_procs)
    assert len(entries) == 1, "标注放弃后条目仍保留在追踪中"
    assert entries[0][1] >= limit
    assert entries[0][2] is True, "达到上限必须标注 abandoned"
    assert proc.poll() is None, "病态进程仍未退出"

    # 标注放弃后：后续 sweep 不再重试（attempts 不再递增）
    webm_clip_mod._reap_orphaned_clips()
    entries = list(clip._unconfirmed_procs)
    assert entries[0][1] == limit, "标注放弃后不得再重试"

    clip.cleanup()
    clip._unconfirmed_procs = []
    webm_clip_mod._ORPHAN_REGISTRY._clips.discard(clip)
    app.processEvents()
