# -*- coding: utf-8 -*-
"""低优先级预热交互让路（性能计划 B6 部分）的回归测试。

锁定四点：
1. begin/end_interaction 可重入闸门：交互期间低优先级随机动作池预热等待，
   交互结束立即继续；高优先级预热不受闸门影响；
2. 低优先级预热排期时交互进行中：不创建 clip（主线程开销）、不启动预热线程，
   交互结束后补上；
3. 在飞的低优先级批次在交互中阻塞在每段 ffmpeg 预热之间，交互结束放行；
   阻塞必须是真正的 Condition 等待（不是 Event.wait 忙循环空转 CPU）；
4. pause_warm（隐藏/切角色）代次作废旧批次：旧角色/旧库的预热不会复活；
   迟到 end_interaction（旧代次 token）不会误释放换代后新交互的持有；
5. 低优先级批次去重：同一时间最多一个在飞批次，timer/50ms 重试/resume 重排
   的并发触发不会重复起批、不会重复启动 ffmpeg。

已知测试局限（保留，不修）：
- 右键菜单测试用 monkeypatch 替换了 QMenu.exec()（立即返回），只验证
  _context_menu_open 同步块内的 begin/end 配对；真实 exec() 的 nested event
  loop、子菜单、菜单期间切角色/隐藏、action 退出等场景不在单测覆盖内。
- 窗口测试未走真实 PetApp.switch_character()；旧窗口迟到事件用直接调用
  _on_frame/_on_clip_finished 模拟（生产路径是 hide() 后 Qt 队列中的残留信号）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMenu

from pet import catalog
import pet.library as library_mod
from pet.config import Config
from pet.window import PetWindow
from tests.test_window_pause import FakeLibrary


class FakeClip:
    """极简假 WebMClip：记录预热调用（含次数），不碰 ffmpeg/Qt。"""

    def __init__(self, path, parent=None):
        self.path = Path(path)
        self.warmed_meta = False
        self.warmed_frame = False
        self.meta_calls = 0
        self.frame_calls = 0

    def warm_meta(self):
        self.meta_calls += 1
        self.warmed_meta = True

    def warm_first_frame(self):
        self.frame_calls += 1
        self.warmed_frame = True


class BlockableClip(FakeClip):
    """warm_meta 可阻塞/可放行的假 clip，用于观测让路与放弃时机。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.meta_entered = threading.Event()
        self.meta_release = threading.Event()

    def warm_meta(self):
        self.meta_calls += 1
        self.warmed_meta = True
        self.meta_entered.set()
        self.meta_release.wait(5.0)


class _GateObjectsLib(library_mod.MovieLibrary):
    """在 _warm_objects 入口加屏障，复现"线程启动后、进入预热前"的窗口，
    用于验证快速 pause/resume 不会让旧批次把新代次误认成自己的批次。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objects_entered = threading.Event()
        self.objects_release = threading.Event()

    def _warm_objects(self, clips, workers, *, yield_to_interaction=False, generation=None, include_frames=True):
        self.objects_entered.set()
        self.objects_release.wait(5.0)
        return super()._warm_objects(
            clips, workers,
            yield_to_interaction=yield_to_interaction,
            generation=generation,
            include_frames=include_frames,
        )


def _make_lib(tmp_path, monkeypatch, clip_cls=FakeClip, lib_cls=None):
    lib_cls = lib_cls or library_mod.MovieLibrary
    monkeypatch.setattr(library_mod, "WebMClip", clip_cls)
    videos = tmp_path / "videos"
    folders = {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm", "吃白饭.webm"],
    }
    for folder, files in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    # 本文件锁定的是让路/代次机制（含首帧阶段），用 full 档保持旧预热语义；
    # 档位对首帧的门控由 test_library_priority_warm.py 专项覆盖。
    return lib_cls(asset_dir=videos, prewarm_policy="full")


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"等待超时: {predicate!r}")


def _wait_never(predicate, timeout=0.4, app=None):
    """有界负向等待：窗口内若 predicate 成立立即失败（用于"不应发生"断言）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            raise AssertionError(f"不应满足: {predicate!r}")
        time.sleep(0.01)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 库侧闸门
def test_begin_end_interaction_gate_reentrant(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    assert lib._interaction_active.is_set() is False

    t1 = lib.begin_interaction()
    assert lib._interaction_active.is_set() is True
    t2 = lib.begin_interaction()  # 拖拽中再点击等叠加持有
    lib.end_interaction(t1)
    assert lib._interaction_active.is_set() is True, "仍有一个持有者，闸门必须保持"
    lib.end_interaction(t2)
    assert lib._interaction_active.is_set() is False

    lib.end_interaction(t1)  # 无持有者时释放是 no-op
    assert lib._interaction_active.is_set() is False


def test_stale_end_after_pause_keeps_new_interaction(tmp_path, monkeypatch):
    """P1：pause_warm 换代清零后，旧代次 token 的迟到 end 不得误释放新交互。"""
    lib = _make_lib(tmp_path, monkeypatch)
    t0 = lib.begin_interaction()   # 交互 A
    lib.pause_warm()               # 隐藏/切角色：换代清零
    lib.resume_warm()
    t1 = lib.begin_interaction()   # 交互 B
    lib.end_interaction(t0)        # A 的迟到 release（旧 token）：必须 no-op
    assert lib._interaction_holders == 1
    assert lib._interaction_active.is_set() is True, "B 仍在交互，闸门必须保持"
    lib.end_interaction(t1)
    assert lib._interaction_active.is_set() is False
    lib.end_interaction(t0)        # 旧 token 再次迟到：no-op，计数不为负
    assert lib._interaction_holders == 0


def test_low_warm_defers_while_interaction_active(tmp_path, monkeypatch, app):
    lib = _make_lib(tmp_path, monkeypatch)
    lib.begin_interaction()

    lib._warm_low_priority_background()  # 模拟 2s 定时器在交互期间到点
    assert "写代码" not in lib._movies, "交互中不得在主线程创建低优先级 clip"
    assert "吃白饭" not in lib._movies

    lib.end_interaction()
    lib._warm_low_priority_background()  # 交互结束后的重排期：立即开始
    _wait_until(lambda: lib._low_first_frames_done)
    assert "写代码" in lib._movies
    assert lib._movies["写代码"].warmed_meta is True
    assert lib._movies["写代码"].warmed_frame is True
    app.processEvents()  # 排干让路期遗留的重试 timer


def test_no_busy_loop_while_waiting_gate(tmp_path, monkeypatch):
    """P1：交互期间等待必须是真正的阻塞，不能对已 set 的 Event 高频 wait 空转。

    用实例属性覆盖 _interaction_active.wait 计数：正确实现用 Condition 阻塞
    （观测窗口内 0 次调用），忙循环实现会高频调用（数千次）。
    """
    lib = _make_lib(tmp_path, monkeypatch, BlockableClip)
    lib._warm_low_priority_background()  # 非交互状态启动批次
    _wait_until(lambda: lib._movies["写代码"].meta_entered.is_set())

    lib.begin_interaction()
    real_wait = lib._interaction_active.wait
    wait_calls: list[int] = []

    def counting_wait(*args, **kwargs):
        wait_calls.append(1)
        return real_wait(*args, **kwargs)

    lib._interaction_active.wait = counting_wait  # 记录旧实现的忙循环调用

    lib._movies["写代码"].meta_release.set()  # 放行第一个 clip → worker 进入第二个 clip 闸门
    # 固定观测窗口（负向断言：等待线程应睡眠，而非空转；不是阶段猜测）
    time.sleep(0.25)
    assert len(wait_calls) == 0, "交互期间低优先级预热必须阻塞等待，不得忙循环"

    lib._movies["吃白饭"].meta_release.set()
    lib.end_interaction()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["吃白饭"].warmed_meta is True
    assert lib._movies["吃白饭"].warmed_frame is True
    assert lib._movies["写代码"].warmed_frame is True


def test_low_warm_waits_while_interaction_active_then_resumes(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch, BlockableClip)
    lib._warm_low_priority_background()  # 非交互状态启动批次
    _wait_until(lambda: lib._movies["写代码"].meta_entered.is_set())

    lib.begin_interaction()
    # 观测第二个 clip 前的让路闸门：worker 放行第一个 clip 后应阻塞在此
    gate_entered = threading.Event()
    orig_gate = lib._await_interaction_clear

    def gated(generation):
        gate_entered.set()
        return orig_gate(generation)

    lib._await_interaction_clear = gated
    lib._movies["写代码"].meta_release.set()  # 放行第一个 clip
    _wait_until(gate_entered.is_set)  # 事件同步确定 worker 已进入闸门（不猜时序）
    assert lib._movies["吃白饭"].warmed_meta is False, "交互中低优先级预热必须让路"

    # 放行第二个 clip 的阻塞再结束交互：worker 立即完成，不留在飞线程
    lib._movies["吃白饭"].meta_release.set()
    lib.end_interaction()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["吃白饭"].warmed_meta is True
    assert lib._movies["吃白饭"].warmed_frame is True
    assert lib._movies["写代码"].warmed_frame is True


def test_low_warm_batch_dedup_in_flight(tmp_path, monkeypatch):
    """P1：批次去重——timer 到点/重试/resume 重排的并发触发不得重复起批。"""
    lib = _make_lib(tmp_path, monkeypatch, BlockableClip)
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._movies["写代码"].meta_entered.is_set())
    assert lib._low_warm_in_flight is True

    lib._warm_low_priority_background()  # 模拟 timer 重入：不得再起一批
    assert lib._low_warm_in_flight is True, "已有批次在飞时必须去重"

    lib._movies["写代码"].meta_release.set()
    lib._movies["吃白饭"].meta_release.set()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._low_warm_in_flight is False
    # 同一批 clip 只被预热一次：没有重复批次重复启动 ffmpeg
    assert lib._movies["写代码"].meta_calls == 1
    assert lib._movies["吃白饭"].meta_calls == 1
    assert lib._movies["写代码"].frame_calls == 1
    assert lib._movies["吃白饭"].frame_calls == 1


def test_pause_cancels_queued_interaction_retry(tmp_path, monkeypatch, app):
    """P1：pause_warm 必须取消交互期间排队的 50ms 重排期，不能遗留触发。"""
    lib = _make_lib(tmp_path, monkeypatch)
    lib.begin_interaction()
    lib._warm_low_priority_background()  # 交互中：安排 50ms 重排期
    assert "写代码" not in lib._movies
    lib.pause_warm()  # 隐藏：取消排队的重排期
    lib.end_interaction()

    _wait_never(lambda: "写代码" in lib._movies, timeout=0.4, app=app)
    assert lib._low_warm_in_flight is False

    # 恢复后重新排期可正常工作
    lib.resume_warm()
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["写代码"].warmed_meta is True


def test_completed_flag_stable_after_completion(tmp_path, monkeypatch):
    """P2：完成标志在批次收尾后保持稳定——遗留 timer/重复排期不再起批覆盖。"""
    lib = _make_lib(tmp_path, monkeypatch)
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._low_warm_in_flight is False

    lib._warm_low_priority_background()  # 遗留 timer 触发：已完整跑完 → 跳过
    assert lib._low_warm_in_flight is False
    assert lib._low_first_frames_done is True
    assert lib._movies["写代码"].meta_calls == 1
    assert lib._movies["吃白饭"].meta_calls == 1


def test_pause_warm_aborts_stale_batch_no_revival(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch, BlockableClip)
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._movies["写代码"].meta_entered.is_set())

    lib.begin_interaction()  # 拖拽中
    lib._movies["写代码"].meta_release.set()
    lib.pause_warm()  # 隐藏/切角色：代次作废旧批次
    lib.end_interaction()  # 旧窗口迟到的松手事件：不得复活旧预热

    _wait_until(lambda: not lib._low_warm_in_flight)  # 旧 worker 真正收尾（不猜时序）
    assert lib._movies["吃白饭"].warmed_meta is False, "旧代次批次必须放弃，不得复活"
    assert lib._low_first_frames_done is False

    # 恢复显示后重新排期：新代次批次完整跑完
    lib.resume_warm()
    lib._movies["吃白饭"].meta_release.set()  # 新批次遇到阻塞 clip 时直接放行
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["吃白饭"].warmed_meta is True
    assert lib._movies["吃白饭"].warmed_frame is True


def test_fast_pause_resume_aborts_batch_via_captured_generation(tmp_path, monkeypatch):
    """P2：代次只在批次启动时捕获一次——线程启动后、进入预热前的快速
    pause/resume 必须作废旧批次，不能把新代次误认成自己的批次继续预热。"""
    lib = _make_lib(tmp_path, monkeypatch, FakeClip, lib_cls=_GateObjectsLib)
    lib._warm_low_priority_background()
    _wait_until(lib.objects_entered.is_set)  # worker 已进入 _warm_objects 前屏障

    lib.pause_warm()
    lib.resume_warm()
    lib.objects_release.set()  # 放行：旧批次拿的是 pause 前的代次，应整体放弃

    _wait_until(lambda: not lib._low_warm_in_flight)
    assert lib._movies["写代码"].warmed_meta is False, "旧代次批次不得在新代次下继续预热"
    assert lib._movies["吃白饭"].warmed_meta is False
    assert lib._low_first_frames_done is False

    # 恢复后的新代次批次可完整跑完
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["写代码"].warmed_meta is True
    assert lib._movies["写代码"].warmed_frame is True


def test_fast_pause_resume_after_claim_before_worker_start_aborts(tmp_path, monkeypatch):
    """N2 回归：代次必须在 GUI 线程认领批次那一刻捕获并随批次传入 worker——
    认领后、worker 真正开始预热前的快速 pause/resume 不得让旧批次把新代次
    误认成自己的代次而复活。用 worker run() 屏障同步（只拦 worker、不拦
    主线程），不用 sleep 猜时序。"""
    lib = _make_lib(tmp_path, monkeypatch)

    worker_entered = threading.Event()
    worker_release = threading.Event()
    real_run = threading.Thread.run

    def gated_run(self, *args, **kwargs):
        worker_entered.set()
        worker_release.wait(5.0)
        return real_run(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "run", gated_run)
    lib._warm_low_priority_background()  # GUI 线程：认领批次（此刻捕获代次）并启动 worker
    assert worker_entered.wait(5.0), "worker 必须在执行预热前被屏障拦截（认领已完成）"

    lib.pause_warm()  # 隐藏：换代
    lib.resume_warm()  # 恢复显示：新代次
    worker_release.set()  # 放行 worker：它必须用认领时捕获的旧代次 → 整体放弃

    _wait_until(lambda: not lib._low_warm_in_flight)
    assert lib._movies["写代码"].warmed_meta is False, "认领后换代的旧批次不得复活"
    assert lib._movies["吃白饭"].warmed_meta is False
    assert lib._low_first_frames_done is False

    # 恢复后的新代次批次可完整跑完（屏障已放行，不再拦截）
    lib._warm_low_priority_background()
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["写代码"].warmed_meta is True
    assert lib._movies["写代码"].warmed_frame is True


def test_thread_start_failure_clears_in_flight_flag(tmp_path, monkeypatch):
    """N3 回归：认领批次后 threading.Thread.start() 抛异常（如资源耗尽）必须
    回滚 _low_warm_in_flight，否则后续排期被永久去重、低优先级预热彻底停摆。"""
    lib = _make_lib(tmp_path, monkeypatch)

    real_start = threading.Thread.start
    calls = {"n": 0}

    def failing_start(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("can't start new thread")
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", failing_start)
    lib._warm_low_priority_background()  # 首次启动失败
    assert lib._low_warm_in_flight is False, "线程启动失败必须回滚认领标志"

    lib._warm_low_priority_background()  # 第二次排期可正常启动并跑完
    _wait_until(lambda: lib._low_first_frames_done)
    assert lib._movies["写代码"].warmed_meta is True
    assert lib._movies["吃白饭"].warmed_meta is True


def test_high_priority_warm_unaffected_by_interaction(tmp_path, monkeypatch):
    lib = _make_lib(tmp_path, monkeypatch)
    lib.begin_interaction()

    lib._warm_all_meta_background()  # 高优先级：与调度线程相同的同步路径
    assert lib._movies[catalog.CLICKS[0]].warmed_meta is True
    assert lib._movies[catalog.CLICKS[0]].warmed_frame is True
    assert lib._movies[catalog.IDLE].warmed_meta is True
    assert lib._movies[catalog.DRAG].warmed_meta is True


def test_high_priority_warm_skipped_while_paused(tmp_path, monkeypatch):
    """P2：高优先级预热在暂停（隐藏/切角色）时整批放弃——sleep 前门控。"""
    lib = _make_lib(tmp_path, monkeypatch)
    lib.pause_warm()

    lib._warm_all_meta_background()
    assert lib._movies[catalog.CLICKS[0]].warmed_meta is False
    assert lib._movies[catalog.CLICKS[0]].warmed_frame is False
    assert lib._movies[catalog.IDLE].warmed_meta is False
    assert lib._movies[catalog.DRAG].warmed_meta is False


def test_high_priority_warm_aborts_when_paused_during_sleep(tmp_path, monkeypatch):
    """P2：高优先级预热的随机错峰 sleep 期间被 pause_warm（隐藏/切角色）——
    sleep 后必须检查 _warm_paused/代次并作废本批，不得拉起 ffmpeg 继续预热。

    用 fake sleep 在 sleep 窗口内同步触发 pause_warm（不猜时序），验证门控。
    """
    lib = _make_lib(tmp_path, monkeypatch)
    real_sleep = library_mod.time.sleep
    sleep_calls = {"n": 0}

    def pausing_sleep(seconds):
        sleep_calls["n"] += 1
        lib.pause_warm()  # 模拟 sleep 期间隐藏/切角色：换代

    monkeypatch.setattr(library_mod.time, "sleep", pausing_sleep)
    lib._warm_all_meta_background()
    assert sleep_calls["n"] == 1, "高优先级预热必须经历随机错峰 sleep"
    assert lib._movies[catalog.CLICKS[0]].warmed_meta is False, "sleep 期间暂停，本批必须作废"
    assert lib._movies[catalog.CLICKS[0]].warmed_frame is False
    assert lib._movies[catalog.IDLE].warmed_meta is False
    assert lib._movies[catalog.DRAG].warmed_meta is False

    # 恢复后新代次批次可正常预热（门控不放错、不误伤）
    monkeypatch.setattr(library_mod.time, "sleep", real_sleep)
    lib.resume_warm()
    lib._warm_all_meta_background()
    assert lib._movies[catalog.CLICKS[0]].warmed_meta is True
    assert lib._movies[catalog.CLICKS[0]].warmed_frame is True
    assert lib._movies[catalog.IDLE].warmed_meta is True


def test_high_priority_warm_aborts_mid_batch_when_paused(tmp_path, monkeypatch):
    """P2：高优先级预热中途（metadata 解码期间）被 pause_warm——尚未开始的
    clip 不得再拉起 ffmpeg（非阻塞中途作废），首帧阶段整段跳过。"""
    lib = _make_lib(tmp_path, monkeypatch, clip_cls=BlockableClip)
    results: dict = {}
    t = threading.Thread(
        target=lambda: (lib._warm_all_meta_background(), results.setdefault("done", True)),
        name="test-high-warm",
    )
    t.start()
    # 第一批（并发 3）已进入 warm_meta 阻塞：事件同步，不猜时序
    _wait_until(lambda: lib._movies[catalog.CLICKS[0]].meta_entered.is_set())
    lib.pause_warm()  # 隐藏/切角色：排队中的 clip 必须作废
    for clip in list(lib._movies.values()):
        if clip.meta_entered.is_set():
            clip.meta_release.set()  # 放行已在跑的 metadata 探测
    t.join(timeout=10.0)
    assert not t.is_alive()
    assert results.get("done") is True
    # 首帧阶段整段跳过（暂停中，不得再拉起 ffmpeg）
    assert all(c.frame_calls == 0 for c in lib._movies.values())
    # 已进入 meta 的 clip 完成探测；暂停后排队未开始的 clip 被中途作废
    entered = [c for c in lib._movies.values() if c.meta_entered.is_set()]
    skipped = [c for c in lib._movies.values() if not c.meta_entered.is_set()]
    assert entered, "第一批高优先级 clip 必须已进入 warm_meta"
    assert all(c.meta_calls == 1 for c in entered)
    assert all(c.meta_calls == 0 for c in skipped), "暂停后排队中的 clip 不得再拉起 ffmpeg"

    # 恢复后新代次批次可完整跑完（门控不误伤）
    lib.resume_warm()
    lib._warm_all_meta_background()
    assert lib._movies[catalog.CLICKS[0]].warmed_frame is True
    assert lib._movies[catalog.DRAG].warmed_frame is True


# ---------------------------------------------------------------- 窗口侧钩子
class RecordingLibrary(FakeLibrary):
    """记录 begin/end_interaction 调用的假素材库（token 兼容签名）。

    不实现 pause_warm()：窗口隐藏/关闭路径必须用对称的 end_interaction()
    释放持有（而不是依赖 pause_warm 隐式清零），否则此桩能测出库侧泄漏。
    """

    def __init__(self):
        super().__init__()
        self.begins = 0
        self.ends = 0

    def begin_interaction(self):
        self.begins += 1
        return 0  # 简化 token：窗口原样传回 end 时被忽略

    def end_interaction(self, token=None):
        self.ends += 1


def _press(pos=QPointF(10, 10), global_pos=QPointF(100, 100)) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _move(pos=QPointF(60, 60), global_pos=QPointF(400, 300)) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseMove, pos, global_pos,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _release(pos=QPointF(60, 60), global_pos=QPointF(400, 300)) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease, pos, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_drag_press_release_toggles_interaction_hold(app, tmp_path):
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press())
    assert lib.begins == 1 and lib.ends == 0
    win.mouseMoveEvent(_move())
    assert win._interaction_state == "DRAGGING"
    assert lib.begins == 1, "拖拽中不得重复持有"
    win.mouseReleaseEvent(_release())
    assert lib.ends == 1

    win.close()
    app.processEvents()


def test_click_hold_lasts_until_click_anim_ends(app, tmp_path):
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    assert lib.begins == 1
    # 原地松手 = 点击：点击动画播放中，闸门必须保持持有
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(100, 100)))
    assert lib.ends == 0, "点击动画播放中，闸门必须保持持有"
    assert win.anim in win.clicks

    win._on_anim_ended(win.anim)  # 点击动画播完 → 回待机 → 释放
    assert lib.ends == 1

    win.close()
    app.processEvents()


def test_click_without_idle_releases_hold(app, tmp_path):
    """P2：角色包有点击但无 idle 时，点击动画播完必须释放闸门（否则永久让路）。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.idles = []  # 手工形态：无待机（build_categories 有兜底，正常构造不可达）
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(100, 100)))
    assert win.anim in win.clicks
    assert lib.ends == 0, "点击动画播放中闸门保持"

    win._on_anim_ended(win.anim)  # 点击动画播完、无 idle → 必须释放
    assert lib.ends == 1, "点击动画结束且无 idle 时必须释放闸门"
    assert win._interaction_hold_active is False

    win.close()
    app.processEvents()


def test_lock_position_press_holds_gate(app, tmp_path):
    """P1：锁定位置只禁拖拽，不禁交互——左键按住期间同样持有让路闸门。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.lock_position = True
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press())
    assert lib.begins == 1 and lib.ends == 0, "锁定位左键按住期间也要让低优先级预热让路"
    assert win._lock_press_active is True

    win.mouseMoveEvent(_move())
    assert win._interaction_state != "DRAGGING", "锁定位禁止拖拽"
    assert lib.begins == 1, "锁定位按住不重复持有"

    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(100, 100)))
    assert win._lock_press_active is False
    assert win.anim in win.clicks, "松手仍按点击处理"
    assert lib.ends == 0, "点击动画播放中闸门保持"
    win._on_anim_ended(win.anim)  # 点击动画播完 → 释放
    assert lib.ends == 1

    win.close()
    app.processEvents()


def test_context_menu_open_holds_gate_until_closed(app, tmp_path, monkeypatch):
    import pet.window as window_mod

    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    monkeypatch.setattr(window_mod, "_populate_context_menu", lambda menu, pet: None)
    # 局限（见文件头注释）：真实 QMenu.exec() nested loop 不在单测覆盖内
    monkeypatch.setattr(QMenu, "exec", lambda self, *args, **kwargs: None)

    win._show_context_menu(QPoint(100, 100))
    assert lib.begins == 1, "右键菜单打开期间应持有让路闸门"
    assert lib.ends == 1, "菜单关闭后应释放让路闸门"

    win.close()
    app.processEvents()


def test_context_menu_flag_updates_hold_directly(app, tmp_path):
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))

    win._context_menu_open = True
    win._update_interaction_hold()
    assert lib.begins == 1 and lib.ends == 0
    win._context_menu_open = False
    win._update_interaction_hold()
    assert lib.ends == 1

    win.close()
    app.processEvents()


def test_hide_releases_interaction_hold_symmetrically(app, tmp_path):
    """P1：隐藏必须对称释放库侧持有（桩库无 pause_warm，靠 end_interaction 配对）。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press())
    assert lib.begins == 1
    win.hide()
    assert lib.ends == 1, "隐藏必须对称释放库侧持有"
    assert win._interaction_hold_active is False
    assert win._context_menu_open is False
    assert win._press_global is None

    win.close()
    app.processEvents()


def test_native_hide_event_releases_interaction_hold(app, tmp_path):
    """P1：不经自定义 hide() 的直进 hideEvent 路径也必须释放库侧持有。"""
    from PySide6.QtGui import QHideEvent

    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press())
    assert lib.begins == 1
    assert win._press_global is not None
    win.hideEvent(QHideEvent())
    assert lib.ends == 1, "直进 hideEvent 必须对称释放库侧持有"
    assert win._interaction_hold_active is False
    assert win._press_global is None, "直进 hideEvent 必须清掉残留按住状态"

    win.close()
    app.processEvents()


def test_native_set_visible_cycle_clears_press_hold_state(app, tmp_path):
    """N4 回归：原生 setVisible(False)→setVisible(True) 周期必须把按住状态
    （_press_global/_dragging 等）一并复位——旧实现 hideEvent 只清了点击/菜单
    标志，残留的 _press_global 在重新显示时被 _resume_activity → _switch →
    _update_interaction_hold 误判成活跃交互、对库侧重新 begin_interaction()，
    而松手事件不再到来 → 库侧 hold 泄漏。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True
    win.show()
    app.processEvents()

    # 制造按住状态：按下并拖过阈值 → _press_global/_dragging 均置位、库侧持有
    win.mousePressEvent(_press())
    win.mouseMoveEvent(_move())
    assert win._interaction_state == "DRAGGING"
    assert win._dragging is True and win._press_global is not None
    assert lib.begins == 1 and lib.ends == 0

    # 原生隐藏（不经自定义 hide()/_pause_activity）：直进 hideEvent
    win.setVisible(False)
    app.processEvents()
    assert win._press_global is None, "原生隐藏必须清掉残留按住状态"
    assert win._dragging is False
    assert lib.ends == 1, "原生隐藏必须对称释放库侧持有"
    assert win._interaction_hold_active is False

    # 原生重新显示：不得把残留旧按住误判成活跃交互、不得重新 begin_interaction
    win.setVisible(True)
    app.processEvents()
    assert win._press_global is None
    assert lib.begins == 1, "重新显示不得对库侧重新 begin_interaction（hold 泄漏）"
    assert lib.begins == lib.ends == 1, "库侧 hold 计数必须为零"
    assert win._interaction_hold_active is False

    win.close()
    app.processEvents()


def test_close_releases_interaction_hold(app, tmp_path):
    """P1：直接 close() 路径必须释放库侧持有，不能只在正常路径兜底。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    win.mousePressEvent(_press())
    assert lib.begins == 1 and lib.ends == 0
    win.close()
    app.processEvents()
    assert lib.ends == 1, "closeEvent 必须对称释放库侧持有"
    assert win._interaction_hold_active is False


def test_hidden_window_late_anim_events_do_not_rehold(app, tmp_path):
    """P1：旧窗口（角色切换后隐藏）迟到的动画事件不得推进动画链、
    不得对旧库重新建立交互让路 hold（生命周期守卫）。"""
    lib = RecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win._is_in_interactive_area = lambda pos: True

    # 点击 → 点击动画播放中（闸门持有）
    win.mousePressEvent(_press(QPointF(10, 10), QPointF(100, 100)))
    win.mouseReleaseEvent(_release(QPointF(10, 10), QPointF(100, 100)))
    assert win.anim in win.clicks
    assert lib.begins == 1 and lib.ends == 0
    click_anim = win.anim

    win.hide()  # 角色切换等价路径：旧窗口隐藏并释放
    assert lib.ends == 1 and win._interaction_hold_active is False

    # 隐藏前已入队的迟到动画事件：必须被生命周期守卫丢弃
    win._on_frame(click_anim, 0)
    win._on_clip_finished(click_anim)
    assert win.anim == click_anim, "隐藏后迟到事件不得推进动画链"
    assert lib.begins == 1 and lib.ends == 1, "隐藏后迟到事件不得重新建立 hold"
    assert win._interaction_hold_active is False

    win.close()
    app.processEvents()
