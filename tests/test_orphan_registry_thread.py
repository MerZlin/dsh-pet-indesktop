# -*- coding: utf-8 -*-
"""终审 P1-4：孤儿注册表 sweep timer 的线程归属回归测试。

_OrphanClipRegistry.register 可被任意线程调用（首帧进程收尾路径
_track_unconfirmed_proc 在解码线程触发）；sweep QTimer 必须在 GUI 线程
创建/启动——在无线程事件循环的 worker 线程创建 QTimer 会让 sweep 永不
触发，退役 reader / 未确认首帧进程无人回收。修复：非 GUI 线程经
_ArmInvoker（QObject）信号排队到 GUI 线程执行创建/启动。

测试口径说明（诚实声明）：本测试锁定「worker 线程不就地建 timer、必须经
编排信号请求」与「GUI 线程就地建并启动」两条不变量；Qt 跨线程排队投递
本身不在套件内做真实投递断言——该事件处理窗口在压满残留状态的套件里
会撞上既有 webm 族 flake 崩溃（登记册 #2），而同一投递模式在生产环境
已被长期使用（PetWindow.fullscreen_changed 等 watcher 线程 → GUI 的
queued 信号，同属 QObject receiver 路径）。
"""
from __future__ import annotations

import threading

import pytest
from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip_mod


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _RecordingSignal:
    def __init__(self):
        self.emit_count = 0

    def emit(self):
        self.emit_count += 1


class _RecordingInvoker:
    def __init__(self):
        self.arm_requested = _RecordingSignal()


def test_worker_thread_register_defers_timer_to_gui(app):
    reg = webm_clip_mod._OrphanClipRegistry()
    reg._arm_invoker = _RecordingInvoker()  # 拦下编排信号（见模块 docstring）

    worker = threading.Thread(target=reg.register, args=(object(),))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()

    # worker 线程：绝不就地创建 QTimer，必须经编排信号请求 GUI 执行
    assert reg._arm_invoker.arm_requested.emit_count == 1, \
        "worker 线程 register 必须经编排信号请求 GUI 建 timer"
    assert reg._timer is None, "worker 线程绝不就地创建 QTimer"

    # GUI 线程调用：就地创建并启动（生产主路径不变）
    reg._arm_sweep_timer()
    try:
        assert reg._timer is not None
        assert reg._timer.thread() is app.thread(), "timer 必须在 GUI 线程"
        assert reg._timer.isActive(), "timer 必须在 GUI 线程启动"
    finally:
        if reg._timer is not None:
            reg._timer.stop()
