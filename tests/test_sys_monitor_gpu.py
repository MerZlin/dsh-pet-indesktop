# -*- coding: utf-8 -*-
"""GPU 读数器接进共享采样线程（``pet/sys_monitor.py``，G3b 接线批）的行为规格。

被约束的是**接线**，不是 GPU 数据源本身（数据源与聚合是 ``pet/gpu_monitor.py``
的 52 条用例）：**一条**采样线程同时喂设备槽（CPU/内存）与 GPU 段，两套订阅集合
各自记账、互不牵连；GPU 读数器按 **worker 代**创建，并由创建它的线程（也就是
唯一使用它的采样线程）收口。

时序纪律：一律事件同步（``Event`` / 回调）＋宽预算（``_wait_until``），不用固定
``sleep`` 猜采样节拍。负向断言（"没有发生"）只在**确定的事件之后**取窗口，或者
用有界窗口证明"没有热循环"——这两处下面都写明了窗口为什么足够。

覆盖（逐条对应接线契约）：

1. 订阅面独立：``subscribe_gpu`` / ``subscribe`` 两套计数；同 key 幂等，不换代。
2. **无 GPU 订阅 → 一个读数器都不建**（工厂 0 次调用）；CPU-only 跑满一轮同样 0 次。
3. **GPU-only → CPU/内存读数函数 0 次调用**，也不产生设备槽快照。
4. 一拍里两条读数互不牵连：GPU 读异常 → 可诊断 error 且 CPU 照常出数；CPU 读异常
   → GPU 照常出数。
5. 工厂失败 / 读数器抛异常 / 读数器返回怪东西 → 都是**可诊断**的 error 样本。
6. 快照就是读数器的 :class:`gpu_monitor.GpuSample`（state/reason/detail/monotonic 时间原样）。
7. **无订阅即停采集**：订阅归零 → 同一拍里由 worker 收口读数器（不占着 PDH 句柄）。
8. **退订拒收在途结果**；**重新订阅 = 新读数器 + 重新建基线**（第一拍 ``sampling``）。
9. **代隔离**：每代 worker 自己的读数器；旧代晚到的结果不覆盖新代快照，旧代读数器
   由旧代自己的线程收口。
10. 读数器的创建/读取/关闭**都在 worker 线程**；``close()`` 即使 join 超时也**不从
    调用方强关**（跨线程关句柄比不关更危险）。
11. 回调**不在锁内**调用（回调里再读快照不自锁死）。
12. 真实 PDH 后端：后台真采 3 拍后正常 ``close()``，线程与句柄都释放（win32）。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

import pytest

from pet import gpu_monitor as gm
from pet.island import segments as isl
from pet import sys_monitor as sm

#: 采样线程的固定名字（收口/独占断言用）。
THREAD_NAME = "pet-sys-monitor"
#: 真实后端证据落盘目录（本批 scratch；.scratch 不入库）。
SCRATCH_DIR = (Path(__file__).resolve().parents[1]
               / ".scratch" / "product-gpu-sampling-20261009")


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """轮询直到 predicate 成立；返回是否在宽预算内成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _live_sys_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == THREAD_NAME]


def _thread_by_ident(ident: int | None) -> threading.Thread | None:
    """按线程号找回线程对象（要 join 那个具体线程时用，不用 sleep 猜时序）。"""
    for thread in threading.enumerate():
        if thread.ident == ident:
            return thread
    return None


class Counter:
    """读数函数替身：调用次数 + 线程记账 + 可挂起（闸门）/ 可注入异常。"""

    def __init__(self, value=37.5, *, error=None, gate=None, entered=None):
        self.value = value
        self.error = error
        self.gate = gate
        self.entered = entered
        self.calls = 0
        self.threads: list[int] = []

    def __call__(self):
        self.calls += 1
        self.threads.append(threading.get_ident())
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.error is not None:
            raise self.error
        return self.value


class FakeGpuReader:
    """``GpuReader`` 替身：复刻两条真语义（**首拍只建基线** / ``close`` 幂等）+ 线程记账。

    ``tag`` 写进 ``detail``：快照里能认出"这一拍是哪一代的读数器出的"。
    非 ``ok`` 的样本一律 ``percent=None``（与真读数器同一条"不伪造 0"的纪律）。
    ``close_ok`` 控制收口成败（``False`` = 复刻原生 ``PdhCloseQuery`` 失败：句柄状态
    未知、**不**返回成功），``release_error`` 是读数器自带的诊断串。
    """

    def __init__(self, *, tag="gpu", percent=42.0, state=None, reason="",
                 error=None, payload=None, gate=None, entered=None,
                 close_ok=True, release_error=""):
        self.tag = tag
        self.percent = percent
        self.state = state            # None = 按"首拍只建基线"的语义自动
        self.reason = reason
        self.error = error
        self.payload = payload        # 非 None 时原样返回（测"读数器返回了怪东西"）
        self.gate = gate
        self.entered = entered
        self.close_ok = close_ok      # bool 或 ``lambda 第几次: bool``
        self.release_error = release_error
        self.reads = 0
        self.closes = 0
        self.read_threads: list[int] = []
        self.close_threads: list[int] = []
        self.entered_thread: int | None = None

    def read(self):
        self.reads += 1
        self.entered_thread = threading.get_ident()
        self.read_threads.append(threading.get_ident())
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.payload is not None:
            return self.payload
        if self.error is not None:
            raise self.error
        primed = self.reads > 1
        state = self.state if self.state is not None else (
            isl.STATE_OK if primed else isl.STATE_SAMPLING)
        return gm.GpuSample(
            percent=self.percent if state == isl.STATE_OK else None,
            sampled_at=time.monotonic(),
            state=state,
            error_reason=self.reason,
            detail={"unit_full": gm.UNIT_FULL, "source": gm.SOURCE_TEXT,
                    "detail": self.tag, "tag": self.tag},
        )

    def close(self) -> bool:
        self.closes += 1
        self.close_threads.append(threading.get_ident())
        if callable(self.close_ok):
            return bool(self.close_ok(self.closes))
        return bool(self.close_ok)


class GpuFactory:
    """读数器工厂替身：记账"建了几个、建在哪个线程、建了几次"（失败也计数）。"""

    def __init__(self, make=None):
        self._make = make if make is not None else (lambda index: FakeGpuReader(tag=f"r{index}"))
        self.readers: list[FakeGpuReader] = []
        self.threads: list[int] = []
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.threads.append(threading.get_ident())
        reader = self._make(self.calls)
        self.readers.append(reader)
        return reader


def _monitor(cpu=None, mem=None, *, factory=None, gpu_samples=None, samples=None, **kwargs):
    """建一个采样器：读数器全注入，GPU 工厂默认是记账替身。"""
    return sm.SysMonitor(
        cpu_reader=cpu if cpu is not None else Counter(),
        mem_reader=mem if mem is not None else Counter(61.0),
        gpu_reader_factory=factory if factory is not None else GpuFactory(),
        on_sample=(samples.append if samples is not None else None),
        on_gpu_sample=(gpu_samples.append if gpu_samples is not None else None),
        **kwargs,
    )


# ---------------------------------------------------------------- 订阅面（独立记账）
def test_gpu_subscriptions_are_counted_separately_and_are_idempotent():
    """GPU 段与设备槽**两套**订阅计数：互不影响；同 key 重复订阅不重复计数。

    宿主（灵动岛）在每次刷新里都会重放订阅，若同 key 会二次计数，退订一次就归不了零，
    采样线程会永远停不下来——所以幂等是契约，不是优化。
    """
    factory = GpuFactory()
    monitor = _monitor(factory=factory)
    try:
        assert monitor.gpu_subscriber_count == 0
        assert monitor.subscribe(isl.CPU) == 1
        assert monitor.gpu_subscriber_count == 0, "设备槽订阅不该算进 GPU 计数"
        assert monitor.subscribe_gpu(isl.GPU) == 1
        assert monitor.subscriber_count == 1, "GPU 订阅不该算进设备槽计数"
        assert monitor.subscribe_gpu(isl.GPU) == 1, "同 key 重复订阅不重复计数"
        assert monitor.subscribe_gpu("gpu") == 1, "同一个 key 的不同写法归一成同一份"
        assert monitor.unsubscribe_gpu("nope") == 1, "未订阅的 key 是 no-op"
        assert monitor.unsubscribe_gpu(isl.GPU) == 0
        assert monitor.subscriber_count == 1, "退 GPU 订阅不该动设备槽订阅"
        assert factory.calls == 0, "订阅本身不建读数器"
    finally:
        monitor.close()


def test_repeated_subscribe_of_the_same_key_does_not_rebuild_the_reader():
    """重复订阅同一个 key（宿主每轮刷新都这么干）不得把读数器换掉。

    订阅集合没变 → 代次不变 → 在途那一拍照旧有效、读数器照旧复用；否则每次刷新都会
    关掉重建一个 PDH query（首拍还要重新建基线），读数永远停在 ``sampling``。
    """
    factory = GpuFactory()
    monitor = _monitor(factory=factory)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: monitor.gpu_snapshot() is not None, timeout=5.0), "未出样本"
        for _ in range(5):
            assert monitor.subscribe_gpu(isl.GPU) == 1
        assert _wait_until(lambda: monitor.gpu_snapshot().state == isl.STATE_OK,
                           timeout=5.0), "重复订阅后读数没有继续推进（读数器被换掉了？）"
        assert factory.calls == 1, "同 key 重复订阅不该重建读数器"
        assert factory.readers[0].closes == 0
    finally:
        monitor.close()


def test_subscriptions_taken_while_stopped_are_picked_up_on_start():
    """岛隐藏时保留订阅（灵动岛的既有口径）：停止态订阅不建读数器，start 后立刻生效。"""
    factory = GpuFactory()
    gpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    try:
        assert monitor.running is False
        assert factory.calls == 0, "停止态订阅不该建读数器（没有采样线程可用）"
        monitor.start()
        assert _wait_until(lambda: bool(gpu_samples), timeout=5.0), "start 后未采 GPU"
        assert factory.calls == 1
        assert gpu_samples[0].state == isl.STATE_SAMPLING, "新读数器的第一拍只建基线"
    finally:
        monitor.close()


# ---------------------------------------------------------------- 无订阅 = 零采集
def test_no_gpu_subscription_never_constructs_a_reader():
    """CPU-only（灵动岛没订阅 GPU 段）时工厂一次都不许被调用。

    这是"没有订阅就没有 PDH"的机器化守卫：工厂被调用就说明已经在往 GPU 采集上走。
    """
    factory = GpuFactory()
    cpu_samples: list = []
    monitor = _monitor(cpu=Counter(), factory=factory, samples=cpu_samples)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(cpu_samples) >= 2, timeout=5.0), "CPU 样本未出"
        assert factory.calls == 0, "CPU-only 也调了 GPU 读数器工厂"
        assert monitor.gpu_snapshot() is None
    finally:
        monitor.close()


def test_gpu_snapshot_and_sample_once_never_build_a_reader():
    """只问快照 / 只同步采一轮，都不是"开始采 GPU"的许可。

    ``sample_once`` 可能被 GUI 线程调用，在那里顺手建读数器就等于在 GUI 线程里建
    PDH query（构建线程归属 + 进程内第一次 read 要付约 0.54s 的 provider 装载），
    所以它只管设备槽。
    """
    factory = GpuFactory()
    monitor = _monitor(factory=factory)
    monitor.subscribe_gpu(isl.GPU)
    try:
        assert monitor.gpu_snapshot() is None
        sample = monitor.sample_once(now=1.0)
        assert sample.state == sm.STATE_OK
        assert monitor.gpu_snapshot() is None
        assert factory.calls == 0, "sample_once / gpu_snapshot 不许建 GPU 读数器"
    finally:
        monitor.close()


def test_gpu_only_sampling_never_touches_cpu_or_mem():
    """GPU-only：不采 CPU/内存（没有订阅的读数一次都不做），也不产生设备槽快照。"""
    cpu, mem = Counter(), Counter(61.0)
    gpu_samples: list = []
    monitor = _monitor(cpu=cpu, mem=mem, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "GPU 样本未出"
        assert cpu.calls == 0 and mem.calls == 0, "GPU-only 仍然采了 CPU/内存"
        assert monitor.snapshot() is None, "GPU-only 不该产生设备槽快照"
    finally:
        monitor.close()


def test_no_subscribers_at_all_means_no_reading_but_the_thread_stays_ready():
    """两套订阅都空：一拍读数都不做（线程只等），但订阅一来立刻恢复采样。

    负向断言只能靠有界窗口：``start()`` 之后的第一拍是**立刻**发生的（首轮不睡满间隔），
    0.3s 窗口覆盖它绰绰有余；窗口里一次读数都没发生，就说明空转那一拍真的零 syscall。
    "线程等待"不等于"整个进程零 CPU"——本用例只断言前者。
    """
    cpu, mem = Counter(), Counter(61.0)
    factory = GpuFactory()
    monitor = _monitor(cpu=cpu, mem=mem, factory=factory)
    monitor.start()
    try:
        assert _wait_until(lambda: monitor.running, timeout=5.0)
        time.sleep(0.3)  # 有界窗口：覆盖 start 后立即发生的那一拍
        assert cpu.calls == 0 and mem.calls == 0, "无订阅时仍在读 CPU/内存"
        assert factory.calls == 0, "无订阅时建了 GPU 读数器"
        assert monitor.snapshot() is None and monitor.gpu_snapshot() is None

        monitor.subscribe_gpu(isl.GPU)
        assert _wait_until(lambda: monitor.gpu_snapshot() is not None, timeout=5.0), \
            "订阅后没有恢复采样（线程是否已经死了？）"
        assert factory.calls == 1
        assert cpu.calls == 0 and mem.calls == 0
    finally:
        monitor.close()


# ---------------------------------------------------------------- 快照契约
def test_gpu_snapshot_keeps_the_reader_sample_verbatim():
    """快照就是读数器给的那个 ``GpuSample``：state / error_reason / detail / 时间原样带出。

    呈现层要的"为什么没有读数"必须能从快照本身读到，宿主不再自己攒第二份状态。
    """
    payload_sample = gm.GpuSample(
        percent=None, sampled_at=time.monotonic(), state=isl.STATE_UNKNOWN,
        error_reason="GPU 计数器当前没有数据（没有实例在活动）",
        detail={"unit_full": gm.UNIT_FULL, "source": gm.SOURCE_TEXT,
                "detail": "", "error_reason": "GPU 计数器当前没有数据（没有实例在活动）"},
    )
    reader = FakeGpuReader(payload=payload_sample)
    gpu_samples: list = []
    monitor = _monitor(factory=GpuFactory(make=lambda _n: reader), gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: bool(gpu_samples), timeout=5.0), "未出样本"
        assert monitor.gpu_snapshot() is payload_sample, "快照被复制/改写了"
        assert gpu_samples[0] is payload_sample, "回调拿到的不是原样本"
        assert payload_sample.state == isl.STATE_UNKNOWN and payload_sample.percent is None
    finally:
        monitor.close()


def test_first_gpu_sample_after_subscribe_is_sampling_not_a_fake_zero():
    """订阅后的第一拍只建基线（``sampling`` + ``--``），第二拍才是读数。

    这是 GPU 与 psutil 差分口径的共同纪律：首拍没有参照区间，报 0 就是把"还没开始"
    说成"GPU 空闲"。
    """
    gpu_samples: list = []
    monitor = _monitor(gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "两拍未出"
        first, second = gpu_samples[0], gpu_samples[1]
        assert (first.state, first.percent) == (isl.STATE_SAMPLING, None)
        assert (second.state, second.percent) == (isl.STATE_OK, 42.0)
        assert second.sampled_at >= first.sampled_at
        assert abs(second.sampled_at - time.monotonic()) < 5.0, "时间戳不是 monotonic 量级"
    finally:
        monitor.close()


# ---------------------------------------------------------------- 失败隔离
def test_gpu_read_failure_is_diagnosable_and_cpu_keeps_sampling():
    """GPU 读数抛异常 → 可诊断的 error 样本；CPU 侧照常出数（一拍里两条读数互不牵连）。"""
    reader = FakeGpuReader(error=RuntimeError("读数炸了"))
    gpu_samples: list = []
    cpu_samples: list = []
    monitor = _monitor(factory=GpuFactory(make=lambda _n: reader), gpu_samples=gpu_samples,
                       samples=cpu_samples)
    monitor.subscribe(isl.CPU)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(cpu_samples) >= 2, timeout=5.0), "CPU 未继续采样"
        assert _wait_until(lambda: bool(gpu_samples), timeout=5.0), "GPU 未出 error 样本"
        gpu = monitor.gpu_snapshot()
        assert gpu.state == isl.STATE_ERROR
        assert gpu.percent is None
        assert "读数炸了" in gpu.error_reason and "RuntimeError" in gpu.error_reason
        assert gpu.detail.get("error_reason") == gpu.error_reason, "卡片原因列没拿到原因"
        assert cpu_samples[-1].state == sm.STATE_OK, "GPU 坏了把 CPU 也带坏了"
        assert reader.closes == 0, "一次读失败不该把读数器换掉（下一拍自愈）"
    finally:
        monitor.close()


def test_cpu_read_failure_does_not_stop_gpu_sampling():
    """CPU/内存读数抛异常 → 设备槽 error；GPU 侧照常推进。"""
    cpu = Counter(error=OSError("psutil 炸了"))
    gpu_samples: list = []
    monitor = _monitor(cpu=cpu, gpu_samples=gpu_samples)
    monitor.subscribe(isl.CPU)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "GPU 未继续采样"
        device = monitor.snapshot()
        assert device.state == sm.STATE_ERROR and "psutil 炸了" in device.error_reason
        assert gpu_samples[-1].state == isl.STATE_OK, "CPU 坏了把 GPU 也带坏了"
    finally:
        monitor.close()


def test_factory_failure_is_a_diagnosable_error_and_is_retried():
    """读数器工厂抛异常 → error 样本（带原因）+ 下一拍重试；CPU 侧不受影响。

    重试而不是"永久放弃"：工厂失败是一次环境事实（比如平台/依赖暂不可用），
    下一拍再试一次是自愈路径，代价是每拍一次可控的失败调用（1Hz，不是热循环）。
    """
    def boom(_index):
        raise RuntimeError("工厂炸了")

    factory = GpuFactory(make=boom)
    gpu_samples: list = []
    cpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples, samples=cpu_samples)
    monitor.subscribe(isl.CPU)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 1, timeout=5.0), "未出 error 样本"
        first = gpu_samples[0]
        assert first.state == isl.STATE_ERROR and "工厂炸了" in first.error_reason
        assert first.percent is None
        assert monitor.gpu_snapshot() is first
        assert _wait_until(lambda: factory.calls >= 2, timeout=5.0), "工厂失败后不再重试"
        assert cpu_samples and cpu_samples[-1].state == sm.STATE_OK
    finally:
        monitor.close()


def test_reader_returning_something_that_is_not_a_sample_is_diagnosable():
    """读数器返回怪东西（非 ``GpuSample``）也要成为可诊断的 error，而不是静默乱用。"""
    def make(index):
        if index == 1:
            return None                       # 工厂返回的不是读数器
        return FakeGpuReader(payload="这不是样本")

    gpu_samples: list = []
    monitor = _monitor(factory=GpuFactory(make=make), gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 1, timeout=5.0), "未出 error 样本"
        first = gpu_samples[0]
        assert first.state == isl.STATE_ERROR and first.percent is None
        assert "read()" in first.error_reason, first.error_reason
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "未继续重试"
        second = gpu_samples[1]
        assert second.state == isl.STATE_ERROR
        assert "str" in second.error_reason and "GpuSample" in second.error_reason, \
            second.error_reason
    finally:
        monitor.close()


def test_wiring_error_sample_carries_the_same_detail_keys_as_the_reader():
    """接线层自己的 error 样本与 ``gpu_monitor`` 的退化样本**同一份 detail 契约**。

    detail 的键是卡片渲染面（B 区行素材）。这里拿真读数器的一条真退化样本对拍键集，
    漂移（改名/漏键）会当场红，而不是等到卡片上少一行字才发现。
    """
    reference = gm.GpuReader(api=object()).read()  # 真读数器的 error 退化样本
    assert reference.state == isl.STATE_ERROR

    def boom(_index):
        raise RuntimeError("工厂炸了")

    monitor = _monitor(factory=GpuFactory(make=boom))
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: monitor.gpu_snapshot() is not None, timeout=5.0)
        wired = monitor.gpu_snapshot()
        assert set(wired.detail) == set(reference.detail), (wired.detail, reference.detail)
        assert wired.detail["unit_full"] == reference.detail["unit_full"]
        assert wired.detail["source"] == reference.detail["source"] == gm.SOURCE_TEXT
        assert wired.detail["error_reason"] == wired.error_reason
    finally:
        monitor.close()


def test_unsupported_gpu_does_not_hot_loop():
    """非 Windows（``unsupported``）：按拍报降级，**不热循环**、不重建读数器。

    窗口是有界负向断言的正当用法：1.3s 只装得下 2 拍（首拍立刻发生 + 一拍间隔），
    这里卡"读数 ≤ 3 次"——热循环会是成百上千次，两者差三个数量级，不会误判。
    """
    reader = FakeGpuReader(state=isl.STATE_UNSUPPORTED,
                           reason="GPU 采集只在 Windows 上可用（PDH）")
    factory = GpuFactory(make=lambda _n: reader)
    gpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: bool(gpu_samples), timeout=5.0), "未出样本"
        assert gpu_samples[0].state == isl.STATE_UNSUPPORTED
        time.sleep(1.3)  # 有界窗口（见 docstring）
        assert reader.reads <= 3, f"unsupported 之下读数次数失控：{reader.reads}"
        assert factory.calls == 1, "unsupported 不该反复重建读数器"
        assert monitor.running is True
    finally:
        monitor.close()


# ---------------------------------------------------------------- 订阅生命周期 / 代隔离
def test_unsubscribe_rejects_the_in_flight_result():
    """退订即拒收**在途**那一拍：用户停掉的段不该在之后收到它的新读数。"""
    gate, entered = threading.Event(), threading.Event()
    reader = FakeGpuReader(gate=gate, entered=entered)
    gpu_samples: list = []
    monitor = _monitor(factory=GpuFactory(make=lambda _n: reader), gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert entered.wait(5.0), "采样线程未进入 GPU 读数"
        assert monitor.unsubscribe_gpu(isl.GPU) == 0
        gate.set()
        # 读数器的 close 只会在"这一拍处理完"之后发生：等到它发生，就说明在途结果已经
        # 走过了发布点——此时快照仍为空，拒收是真的（不是"还没跑到"）。
        assert _wait_until(lambda: reader.closes >= 1, timeout=5.0), \
            "退订后 worker 没有收口读数器"
        assert not gpu_samples, "退订后的在途结果仍然触发回调"
        assert monitor.gpu_snapshot() is None, "退订后的在途结果仍然被发布"
    finally:
        gate.set()
        monitor.close()


def test_resubscribe_after_unsubscribe_starts_from_a_fresh_baseline():
    """退订 → 重新订阅 = 新读数器 + 重新建基线：新序列的第一拍仍是 ``sampling``。

    旧读数器的基线属于上一段订阅，把两段之间的区间接起来算出来的数不是"此刻"。
    """
    factory = GpuFactory()
    gpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "首段未出两拍"
        assert gpu_samples[-1].state == isl.STATE_OK
        first_series = len(gpu_samples)

        assert monitor.unsubscribe_gpu(isl.GPU) == 0
        assert _wait_until(lambda: factory.readers[0].closes == 1, timeout=5.0), \
            "退订后旧读数器没有被收口"
        assert monitor.gpu_snapshot() is gpu_samples[-1], "已发布的样本被清空（与设备槽口径不一致）"

        assert monitor.subscribe_gpu(isl.GPU) == 1
        assert _wait_until(lambda: len(gpu_samples) > first_series, timeout=5.0), \
            "重新订阅后没出样本"
        assert factory.calls == 2, "重新订阅必须换一个新读数器"
        fresh = gpu_samples[first_series]
        assert (fresh.state, fresh.percent) == (isl.STATE_SAMPLING, None), \
            "重新订阅后第一拍不是 sampling（沿用了旧基线？）"
        assert fresh.detail["tag"] == "r2"
    finally:
        monitor.close()


def test_stale_generation_gpu_result_never_overwrites_the_new_snapshot():
    """stop→start 后旧代在途读数晚到：不许覆盖新代快照，也不许触发回调。

    旧线程的读数器由旧线程自己收口（跨线程关句柄是更坏的组合），所以这里一并断言
    "旧代晚到的样本没有进入发布面"与"旧代读数器由旧代线程收口"。
    """
    gate, entered = threading.Event(), threading.Event()
    reader1 = FakeGpuReader(tag="r1", gate=gate, entered=entered)
    factory = GpuFactory(make=lambda index: reader1 if index == 1
                         else FakeGpuReader(tag=f"r{index}"))
    gpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert entered.wait(5.0), "旧代未进入 GPU 读数"
        stale_thread = _thread_by_ident(reader1.entered_thread)
        assert stale_thread is not None, "拿不到旧代采样线程"

        monitor.stop(wait=False)
        monitor.start()
        assert _wait_until(lambda: any(sample.detail.get("tag") == "r2"
                                       for sample in gpu_samples), timeout=5.0), \
            "新代未出样本"
        fresh = monitor.gpu_snapshot()
        assert fresh.detail["tag"] == "r2"

        monitor.stop(wait=True)  # 稳住新代：下面只观察旧代的迟到结果
        gate.set()
        assert _wait_until(lambda: reader1.closes == 1, timeout=5.0), "旧代读数器未收口"
        stale_thread.join(5.0)
        assert not stale_thread.is_alive(), "旧代线程未收口"
        assert monitor.gpu_snapshot() is fresh, "旧代晚到的样本覆盖了新代快照"
        assert all(sample.detail.get("tag") == "r2" for sample in gpu_samples), \
            "旧代晚到的样本仍然触发了回调"
        assert reader1.close_threads == [reader1.entered_thread], \
            "旧代读数器不是由读它的那个线程收口的"
    finally:
        gate.set()
        monitor.stop(wait=True)
        monitor.close()


def test_reader_is_created_read_and_closed_on_the_worker_thread():
    """读数器只在采样线程里建/读/关：调用方线程（GUI）一次都不碰。

    PDH query 绑定创建线程：在 GUI 线程里建，代价是线程归属绑到界面上，
    外加进程内第一次 read 约 0.54s 的 provider 装载落在界面线程里。
    """
    factory = GpuFactory()
    gpu_samples: list = []
    monitor = _monitor(factory=factory, gpu_samples=gpu_samples)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(gpu_samples) >= 2, timeout=5.0), "未出两拍"
        worker = _live_sys_threads()
        assert len(worker) == 1, f"采样线程数不为 1：{worker}"
        worker_ident = worker[0].ident
        reader = factory.readers[0]
        caller = threading.get_ident()
        assert factory.threads == [worker_ident], "读数器不是在采样线程里建的"
        assert set(reader.read_threads) == {worker_ident}, "读数器不是在采样线程里读的"
        assert caller != worker_ident
    finally:
        monitor.close()
    reader = factory.readers[0]
    assert reader.closes == 1, "收口时读数器没有被关一次"
    assert reader.close_threads == [worker_ident], "读数器不是由采样线程关闭的"
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0), \
        f"关闭后仍有采样线程：{_live_sys_threads()}"
    assert len(factory.readers) == 1, "一个 worker 代只该有一个读数器"


def test_close_with_a_stuck_read_never_force_closes_the_reader_from_the_caller():
    """join 超时也不许从调用方强关读数器：句柄归采样线程，跨线程关比不关更危险。"""
    gate, entered = threading.Event(), threading.Event()
    reader = FakeGpuReader(gate=gate, entered=entered)
    monitor = _monitor(factory=GpuFactory(make=lambda _n: reader))
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert entered.wait(5.0), "采样线程未进入 GPU 读数"
        started = time.monotonic()
        monitor.close()
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"close() 阻塞了 {elapsed:.2f}s（应 ≤ 有界 join 预算）"
        assert monitor.running is False
        assert reader.closes == 0, "调用方在 join 超时后强关了读数器"
    finally:
        gate.set()
    assert _wait_until(lambda: reader.closes == 1, timeout=5.0), "卡住的线程没有自己收口"
    assert reader.close_threads[0] == reader.read_threads[0], "收口线程不是读它的线程"
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0)


# ---------------------------------------------------------------- 收口失败（诚实合同）
# 读数器的 ``close()`` 复刻原生 ``PdhCloseQuery``：返回 ``False`` 表示**句柄状态未知**
# （微软文档只把"关掉句柄、释放内存"承诺给成功路径）。接线层因此不许把"我试过了"
# 说成"已经关掉了"，也不许在循环里反复赌——这三条下面逐条钉住。
def test_session_close_keeps_the_reader_and_retries_exactly_once():
    """读数器报"没关掉"：引用不清、owner 恰好再试一次、仍失败如实返回 False。"""
    reader = FakeGpuReader(close_ok=False, release_error="PdhError: PDH_INVALID_HANDLE")
    session = sm._GpuSession(7, reader)
    assert session.close() is False
    assert session.reader is reader, "没确认释放就清引用 = 把句柄丢进没人管的地方"
    assert reader.closes == 2, "owner 恰好再试一次（不是重试循环）"
    assert "PDH_INVALID_HANDLE" in session.release_error
    assert session.epoch == 7


def test_session_close_clears_only_after_a_successful_retry():
    """第二次尝试成功：这时才清引用，且诊断串一并清掉。"""
    reader = FakeGpuReader(close_ok=lambda attempt: attempt >= 2,
                           release_error="PdhError: PDH_INVALID_HANDLE")
    session = sm._GpuSession(1, reader)
    assert session.close() is True
    assert reader.closes == 2
    assert session.reader is None
    assert session.release_error == ""
    assert session.close() is True and reader.closes == 2, "再调是 no-op"


def test_session_close_without_a_closer_keeps_the_reader_and_says_why():
    """读数器连 ``close`` 都没有：如实返回 False + 有界诊断，句柄同样不丢。"""
    reader = object()
    session = sm._GpuSession(1, reader)
    assert session.close() is False
    assert session.reader is reader
    assert "close" in session.release_error


def test_a_failed_release_is_reported_once_and_never_retried_by_the_worker(
        caplog):
    """worker 侧：收口失败留一条带原因的 warning，不重试第三次、不卡线程、不谎报。

    事件同步 + 有界窗口：先等"恰好两次原生尝试"这个确定事件，再给一整拍（1Hz）
    的窗口证明"订阅归零之后不再反复重试"——不是拿 sleep 猜时序。
    """
    reader = FakeGpuReader(close_ok=False, release_error="PdhError: PDH_INVALID_HANDLE")
    factory = GpuFactory(make=lambda _n: reader)
    monitor = _monitor(factory=factory)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: reader.reads >= 1, timeout=5.0)
        with caplog.at_level(logging.WARNING, logger="pet.sys_monitor"):
            monitor.unsubscribe_gpu(isl.GPU)
            assert _wait_until(lambda: reader.closes >= 2, timeout=5.0)
            time.sleep(1.2)                     # 至少一整拍：不会再有第三次
            assert reader.closes == 2, "worker 在退订后反复重试收口"
        assert reader.close_threads[0] == reader.read_threads[0], "收口线程不是读它的线程"
        assert any("收口" in record.getMessage() for record in caplog.records), \
            "收口失败没有留下可诊断的日志（假成功比失败更坏）"
    finally:
        monitor.close()
    assert reader.closes == 2, "会话早该退出，线程 finally 不该再试一次"


def test_no_gpu_subscription_at_stop_time_or_after():
    """收口后订阅集合清空、快照保留（与设备槽同口径），再订阅要重新建读数器。"""
    factory = GpuFactory()
    monitor = _monitor(factory=factory)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert _wait_until(lambda: monitor.gpu_snapshot() is not None, timeout=5.0)
        keeper = monitor.gpu_snapshot()
        monitor.stop(wait=True)
        assert monitor.gpu_subscriber_count == 1, "stop 不改变订阅（宿主保留订阅是既有口径）"
        assert monitor.gpu_snapshot() is keeper, "stop 不该丢掉最后一份快照"
        assert factory.readers[0].closes == 1, "stop 后读数器没有被采样线程收口"
    finally:
        monitor.close()
    # 重新 start（岛再次可见）：换新读数器，从新的基线开始
    monitor.start()
    try:
        assert _wait_until(lambda: factory.readers[-1].reads >= 1, timeout=5.0)
        assert factory.calls == 2
    finally:
        monitor.close()


# ---------------------------------------------------------------- 回调纪律
def test_callbacks_run_outside_the_lock():
    """回调不在锁内调用：回调里再读快照（要拿同一把非重入锁）不许自锁死。

    锁内回调会永久卡在回调里的 ``snapshot()`` 上——事件永不置位，本用例以"等待超时"
    失败，而不是以某个模糊的断言失败。
    """
    cpu_ok, gpu_ok = threading.Event(), threading.Event()
    seen: list[int] = []
    holder: dict = {}

    def on_sample(_sample):
        holder["monitor"].snapshot()          # 再进一次锁：锁内回调会死在这里
        seen.append(threading.get_ident())
        cpu_ok.set()

    def on_gpu_sample(_sample):
        holder["monitor"].gpu_snapshot()
        seen.append(threading.get_ident())
        gpu_ok.set()

    monitor = sm.SysMonitor(
        cpu_reader=Counter(), mem_reader=Counter(61.0), gpu_reader_factory=GpuFactory(),
        on_sample=on_sample, on_gpu_sample=on_gpu_sample)
    holder["monitor"] = monitor
    monitor.subscribe(isl.CPU)
    monitor.subscribe_gpu(isl.GPU)
    monitor.start()
    try:
        assert cpu_ok.wait(5.0), "CPU 回调未在锁外触发（疑似锁内调用）"
        assert gpu_ok.wait(5.0), "GPU 回调未在锁外触发（疑似锁内调用）"
        worker = _live_sys_threads()
        assert len(worker) == 1
        assert seen and all(ident == worker[0].ident for ident in seen), "回调不在采样线程上"
    finally:
        monitor.close()


# ---------------------------------------------------------------- 真实后端（win32）
@pytest.mark.skipif(sys.platform != "win32", reason="真实 PDH 后端只在 Windows 上可跑")
def test_real_backend_three_ticks_in_the_background_then_close():
    """真机后台采 3 拍后正常 ``close()``：线程与 PDH 句柄都释放。

    不 mock、不跳过成功：本机真实结果是什么就断言什么（没有 GPU 计数器时的
    ``unknown``/``error`` 也是合法结论，那时断言的是"可解释的降级"而不是假装通过）。
    证据落盘到 scratch，供人复核。
    """
    reader = gm.GpuReader()
    gpu_samples: list = []
    monitor = sm.SysMonitor(interval=1.0, gpu_reader_factory=lambda: reader,
                            on_gpu_sample=gpu_samples.append)
    monitor.subscribe_gpu(isl.GPU)
    record: dict = {"probe": "sys-monitor-gpu-background", "platform": sys.platform}
    try:
        started = time.monotonic()
        monitor.start()
        assert _wait_until(lambda: len(gpu_samples) >= 3, timeout=30.0), \
            f"30s 宽预算内没采到 3 拍：{len(gpu_samples)}"
        elapsed = time.monotonic() - started
        states = [sample.state for sample in gpu_samples]
        percents = [sample.percent for sample in gpu_samples]
        workers = sorted({thread.ident for thread in _live_sys_threads()})
        record.update({
            "ticks": len(gpu_samples), "states": states, "percents": percents,
            "elapsed_s": round(elapsed, 3),
            "sampled_at": [round(sample.sampled_at, 3) for sample in gpu_samples],
            "monotonic_skew": round(abs(gpu_samples[-1].sampled_at - time.monotonic()), 3),
            "reader_owner_thread": reader.owner_thread,
            "worker_idents": workers,
        })
        # query 的 owner 必须是那条采样线程（PDH 句柄绑定创建线程，不许落在调用方线程上）
        assert reader.owner_thread is not None and reader.owner_thread in workers, record
    finally:
        monitor.close()
        monitor.stop(wait=True)
    record["after_close"] = {
        "opened": reader.opened, "closed": reader.closed,
        "owner_thread_after_close": reader.owner_thread,
        "live_sys_threads": [thread.name for thread in _live_sys_threads()],
        "thread_names_after": sorted({thread.name for thread in threading.enumerate()
                                      if "sys-monitor" in thread.name}),
    }
    try:
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        (SCRATCH_DIR / "real_backend_sampling.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:  # 只读环境下落盘失败不该把探针判红
        pass
    print("共享采样线程真实后端探针：%s" % json.dumps(record, ensure_ascii=False))

    assert len(gpu_samples) >= 3, record
    assert all(sample.state in (isl.STATE_OK, isl.STATE_SAMPLING, isl.STATE_UNKNOWN,
                                isl.STATE_ERROR, isl.STATE_UNSUPPORTED)
               for sample in gpu_samples), record
    assert all(sample.percent is None or 0.0 <= sample.percent <= 100.0
               for sample in gpu_samples), record
    # 首拍只建基线（真后端）；后续拍要么给读数要么给可解释的降级
    assert states[0] == isl.STATE_SAMPLING, record
    for state, percent, sample in zip(states[1:], percents[1:], gpu_samples[1:]):
        if state == isl.STATE_OK:
            assert percent is not None, record
        else:
            assert percent is None and sample.error_reason, record
    assert record["monotonic_skew"] < 30.0, "采样时间戳与 monotonic 不同源"
    assert record["after_close"]["opened"] is False
    assert record["after_close"]["closed"] is True
    assert record["after_close"]["owner_thread_after_close"] is None, \
        "收口后 query 的 owner 不该还挂着（句柄已释放）"
    assert record["after_close"]["live_sys_threads"] == []
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0)
