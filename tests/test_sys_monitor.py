# -*- coding: utf-8 -*-
"""设备槽共享采样器（``pet/sys_monitor.py``）的单测（规格 §Slice B）。

采样核心零 Qt、依赖全注入：psutil、时钟、读数函数都是替身，**不真睡 >2s**。
时序类断言一律事件同步 + 宽预算（``_wait_until`` / ``Event``），禁止用固定
sleep 猜时序——冻结的 runner 上事件派发时刻会漂。

覆盖：差分首样本为 None（psutil 首调恒 0，绝不伪造 0）、格式化、error 态、
启停幂等、无订阅不采样、间隔 1Hz 下限与运行中即时生效、线程收口有界、
旧代晚到的样本不得覆盖新代快照。GPU 接线的向后兼容面（本文件末尾）：
无 GPU 订阅时不建读数器/不踩 PDH、``sample_once`` 不碰 GPU 路径。
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from pet.island import segments as isl
from pet import sys_monitor as sm


#: 采样线程的固定名字（收口断言用）
THREAD_NAME = "pet-sys-monitor"


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


class FakePsutil:
    """psutil 模块替身：只实现采样器真正用到的两个 API。"""

    def __init__(self, *, cpu: float = 37.5, mem: float = 61.0,
                 cpu_error: Exception | None = None, mem_error: Exception | None = None):
        self.cpu = cpu
        self.mem = mem
        self.cpu_error = cpu_error
        self.mem_error = mem_error
        self.cpu_calls: list[object] = []
        self.mem_calls = 0

    def cpu_percent(self, interval=None):
        self.cpu_calls.append(interval)
        if self.cpu_error is not None:
            raise self.cpu_error
        return self.cpu

    def virtual_memory(self):
        self.mem_calls += 1
        if self.mem_error is not None:
            raise self.mem_error
        return types.SimpleNamespace(percent=self.mem)


@pytest.fixture
def fake_psutil(monkeypatch):
    """把替身 psutil 塞进 sys.modules（比改真实模块更干净，缺 psutil 也能跑）。"""
    fake = FakePsutil()
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return fake


class Reader:
    """可脚本化的读数替身：按序列出值，可挂起（``gate``）与计数。"""

    def __init__(self, values=None, *, default: float = 42.0, gate: threading.Event | None = None,
                 entered: threading.Event | None = None):
        self._values = list(values or [])
        self._default = default
        self._gate = gate
        self._entered = entered
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self._entered is not None:
            self._entered.set()
        if self._gate is not None:
            self._gate.wait(5.0)
        return self._values.pop(0) if self._values else self._default


# ---------------------------------------------------------------- 区间口径
def test_clamp_interval_keeps_the_one_hz_floor():
    """1Hz 是硬上限（不可配）：CPU/内存是秒级趋势量，更快的刷新只是烧 CPU。"""
    assert sm.DEFAULT_INTERVAL_SECONDS == 1.0
    assert sm.clamp_interval(0.1) == 1.0
    assert sm.clamp_interval(0.0) == 1.0
    assert sm.clamp_interval(-5) == 1.0
    assert sm.clamp_interval("0.2") == 1.0
    assert sm.clamp_interval(10) == 10.0
    assert sm.clamp_interval(600) == sm.MAX_INTERVAL_SECONDS


def test_clamp_interval_falls_back_on_bad_values():
    assert sm.clamp_interval("abc") == sm.DEFAULT_INTERVAL_SECONDS
    assert sm.clamp_interval(None) == sm.DEFAULT_INTERVAL_SECONDS
    assert sm.clamp_interval(float("nan")) == sm.DEFAULT_INTERVAL_SECONDS
    assert sm.clamp_interval(10 ** 400) == sm.DEFAULT_INTERVAL_SECONDS


# ---------------------------------------------------------------- 文案
def test_format_percent_rounds_and_dashes_out_missing_values():
    assert sm.format_cpu(24.4) == "24%"
    assert sm.format_cpu(24.6) == "25%"
    assert sm.format_cpu(0) == "0%"
    assert sm.format_cpu(100) == "100%"
    assert sm.format_cpu(None) == "--"
    assert sm.format_cpu("bad") == "--"
    assert sm.format_cpu(float("nan")) == "--"


def test_format_mem_follows_the_same_field_contract():
    assert sm.format_mem(61.0) == "61%"
    assert sm.format_mem(60.5) == "60%"  # 银行家舍入口径随 Python 走，取整即可
    assert sm.format_mem(None) == "--"


def test_max_text_sample_covers_worst_case_width():
    """盒宽由最坏样本声明（"888%" = 3 位数字 + 百分号），段不得超宽。"""
    assert sm.MAX_TEXT == "888%"
    assert len(sm.MAX_TEXT) == 4
    assert sm.format_cpu(1234.0) == "1234%"  # 超出声明盒宽由字段盒截断护栏兜底


def test_state_vocabulary_matches_the_segment_contract():
    assert sm.STATE_OK == isl.STATE_OK
    assert sm.STATE_ERROR == isl.STATE_ERROR


# ---------------------------------------------------------------- 差分语义
def test_cpu_meter_first_sample_is_none_then_reads_through():
    """psutil.cpu_percent(interval=None) 首次调用没有参照区间（恒 0），
    报 None 而不是伪造 0。"""
    reader = Reader([0.0, 37.5, 12.5])
    meter = sm.CpuMeter(reader=reader)
    assert meter.sample() is None
    assert meter.sample() == 37.5
    assert meter.sample() == 12.5
    assert reader.calls == 3


def test_cpu_meter_consumes_the_baseline_even_when_the_first_read_fails():
    """首轮读数抛错时也不能把 psutil 的首值（恒 0）当真实占用报出去。"""
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return 0.0

    meter = sm.CpuMeter(reader=reader)
    with pytest.raises(RuntimeError):
        meter.sample()
    assert meter.sample() is None  # psutil 的基线刚被消费，本轮仍无有效区间
    assert meter.sample() == 0.0


def test_cpu_meter_reset_reprimes_the_baseline():
    """换代/重建后重新建基线：新代第一轮同样报 None（宁可少报一轮，不报错值）。"""
    reader = Reader([1.0, 2.0, 3.0])
    meter = sm.CpuMeter(reader=reader)
    assert meter.sample() is None  # 消费掉 1.0 作基线
    assert meter.primed is True
    meter.reset()
    assert meter.primed is False
    assert meter.sample() is None  # 消费掉 2.0 作新基线
    assert meter.sample() == 3.0


# ---------------------------------------------------------------- 真实读数函数
def test_read_cpu_percent_asks_psutil_for_a_diffing_reading(fake_psutil):
    assert sm.read_cpu_percent() == 37.5
    assert fake_psutil.cpu_calls == [None], "必须走 interval=None 的差分口径"


def test_read_mem_percent_reads_virtual_memory_percent(fake_psutil):
    assert sm.read_mem_percent() == 61.0
    assert fake_psutil.mem_calls == 1


def test_readers_raise_explicitly_when_psutil_is_missing(monkeypatch):
    """psutil 不可用时是"读数不可用"，不是 0——抛显式异常，由采样线程收成 error 态。"""
    monkeypatch.setitem(sys.modules, "psutil", None)
    with pytest.raises(sm.PsutilUnavailable):
        sm.read_cpu_percent()
    with pytest.raises(sm.PsutilUnavailable):
        sm.read_mem_percent()


def test_readers_propagate_psutil_call_failures(monkeypatch):
    """psutil 在、但平台缺计数器：异常直传（不吞、不编 0），由采样线程收成 error 态。"""

    class Exploding:
        def cpu_percent(self, interval=None):
            raise OSError("no such counter")

        def virtual_memory(self):
            raise OSError("no such counter")

    monkeypatch.setitem(sys.modules, "psutil", Exploding())
    with pytest.raises(OSError):
        sm.read_cpu_percent()
    with pytest.raises(OSError):
        sm.read_mem_percent()


def test_real_readers_are_psutil_backed_or_honestly_unavailable():
    """本机真实集成（不联网）：psutil 在 requirements.txt 里，装了就给 0~100 的读数，
    没装就抛 PsutilUnavailable——绝不返回一个假的 0。"""
    try:
        cpu_reader = sm.read_cpu_percent
        cpu_reader()  # 首调只建立 psutil 自己的基线
        assert 0.0 <= sm.read_cpu_percent() <= 100.0
        assert 0.0 <= sm.read_mem_percent() <= 100.0
    except sm.PsutilUnavailable:
        pytest.skip("本机未安装 psutil：读数函数如实报不可用")


# ---------------------------------------------------------------- 快照
def test_sample_once_reports_ok_state_with_injected_readers():
    monitor = sm.SysMonitor(
        cpu_reader=Reader([0.0, 37.5]), mem_reader=lambda: 61.0, clock=lambda: 1000.0,
    )
    first = monitor.sample_once()
    assert (first.state, first.cpu_percent, first.mem_percent) == (sm.STATE_OK, None, 61.0)
    assert first.sampled_at == 1000.0
    assert first.error_reason == ""
    assert monitor.snapshot() is first
    second = monitor.sample_once(now=1001.0)
    assert (second.cpu_percent, second.mem_percent, second.sampled_at) == (37.5, 61.0, 1001.0)
    monitor.close()


def test_sampling_failure_becomes_an_error_sample_not_an_exception():
    def boom():
        raise OSError("psutil 炸了")

    monitor = sm.SysMonitor(cpu_reader=Reader(), mem_reader=boom)
    sample = monitor.sample_once(now=5.0)
    assert sample.state == sm.STATE_ERROR
    assert sample.cpu_percent is None and sample.mem_percent is None
    assert "psutil 炸了" in sample.error_reason
    assert monitor.snapshot() is sample
    monitor.close()


def test_non_numeric_reading_becomes_an_error_sample():
    """替身/平台返回非数字（None、字符串）时不许当读数用下去。"""
    monitor = sm.SysMonitor(cpu_reader=Reader(), mem_reader=lambda: None)
    assert monitor.sample_once().state == sm.STATE_ERROR
    monitor.close()


def test_missing_psutil_becomes_an_error_sample(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    monitor = sm.SysMonitor()  # 默认读数函数 → psutil 不可用
    sample = monitor.sample_once(now=7.0)
    assert sample.state == sm.STATE_ERROR
    assert "psutil" in sample.error_reason.lower()
    monitor.close()


# ---------------------------------------------------------------- 订阅计数
def test_subscriptions_dedupe_by_key_and_never_go_negative():
    monitor = sm.SysMonitor()
    assert monitor.subscriber_count == 0
    assert monitor.subscribe(isl.CPU) == 1
    assert monitor.subscribe(isl.CPU) == 1  # 同 key 不重复计数
    assert monitor.subscribe(isl.MEM) == 2
    assert monitor.unsubscribe(isl.CPU) == 1
    assert monitor.unsubscribe("gpu") == 1  # 未订阅的 key 是 no-op
    assert monitor.unsubscribe(isl.MEM) == 0
    assert monitor.subscriber_count == 0
    monitor.close()


def test_no_subscribers_means_no_reading_at_all():
    """岛不可见 / 两个段都被禁用时不采样：后台线程不得白烧 CPU 与 syscall。"""
    reader = Reader(entered=None)
    sampled = threading.Event()
    monitor = sm.SysMonitor(
        cpu_reader=reader, mem_reader=lambda: 61.0,
        on_sample=lambda _sample: sampled.set(),
    )
    monitor.start()
    try:
        assert _wait_until(lambda: monitor.running, timeout=5.0)
        time.sleep(0.25)  # 让线程至少空转一轮（首轮不睡满间隔）
        assert reader.calls == 0, "无订阅时仍然读了 CPU"
        assert monitor.snapshot() is None

        monitor.subscribe(isl.CPU)
        assert sampled.wait(5.0), "订阅后未在宽预算内出样本"
        assert monitor.snapshot() is not None
    finally:
        monitor.close()


def test_last_unsubscribe_stops_sampling():
    samples: list[sm.SysSample] = []
    monitor = sm.SysMonitor(
        cpu_reader=Reader(), mem_reader=lambda: 61.0, on_sample=samples.append)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert _wait_until(lambda: bool(samples), timeout=5.0), "订阅后未出样本"
        monitor.unsubscribe(isl.CPU)
        time.sleep(0.05)
        count = len(samples)
        time.sleep(1.3)  # > 一个采样周期（1Hz）：退订后不得再采样
        assert len(samples) == count, "退订后仍在采样"
    finally:
        monitor.close()


def test_stop_and_start_are_idempotent():
    monitor = sm.SysMonitor(cpu_reader=Reader(), mem_reader=lambda: 61.0)
    monitor.subscribe(isl.CPU)
    assert monitor.start() is True
    assert monitor.start() is False, "已在跑：不重复起线程"
    monitor.stop(wait=True)
    assert monitor.running is False
    monitor.stop(wait=True)  # 重复 stop 是 no-op，不抛
    assert monitor.start() is True
    monitor.close()
    assert monitor.running is False
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0), \
        f"关闭后仍有采样线程在跑：{_live_sys_threads()}"


def test_set_interval_returns_clamped_value_while_running():
    monitor = sm.SysMonitor(interval=1.0, cpu_reader=Reader(), mem_reader=lambda: 61.0)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert monitor.set_interval(0.1) == 1.0, "1Hz 上限不可配：更快的请求被夹回 1s"
        assert monitor.interval == 1.0
        assert monitor.set_interval(30) == 30.0
        assert monitor.interval == 30.0
        assert monitor.running is True
    finally:
        monitor.close()


def test_set_interval_takes_effect_immediately_while_running():
    """运行中改间隔即时生效（伸缩策略改完不重建线程）。

    计时账：改间隔前线程可能已经算好了本次 1s 的等待，在途那一次不算违约——
    所以先允许它出来，再看窗口里还有没有新样本。旧节奏（1Hz）会在 2s 附近再
    来一个，新节奏（30s）不会。
    """
    samples: list[sm.SysSample] = []
    monitor = sm.SysMonitor(
        interval=1.0, cpu_reader=Reader(), mem_reader=lambda: 61.0,
        on_sample=samples.append)
    monitor.subscribe(isl.CPU)  # 订阅先于 start：首轮不睡即采样
    monitor.start()
    try:
        assert _wait_until(lambda: bool(samples), timeout=5.0), "首样本未出"
        monitor.set_interval(30.0)
        _wait_until(lambda: len(samples) >= 2, timeout=2.0)  # 在途那一次（可能没有）
        count = len(samples)
        time.sleep(1.4)  # 旧节奏会在这段窗口里再出一个，新节奏不会
        assert len(samples) == count, "改间隔后仍按旧节奏（1Hz）采样"
    finally:
        monitor.close()


# ---------------------------------------------------------------- 线程纪律
def test_thread_survives_sampling_error_and_keeps_publishing():
    """采样异常不得杀线程（否则槽永久停在最后一帧，看不出坏了）。"""
    samples: list[sm.SysSample] = []
    monitor = sm.SysMonitor(
        cpu_reader=Reader(), mem_reader=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        on_sample=samples.append)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(samples) >= 2, timeout=5.0), \
            "采样异常后线程未继续出样本"
        assert monitor.running is True
        assert all(sample.state == sm.STATE_ERROR for sample in samples)
    finally:
        monitor.close()


def test_close_joins_the_thread_with_a_bounded_wait():
    """线程收口有界：读数卡住时 close() 不许把 GUI 线程吊死。"""
    gate = threading.Event()
    entered = threading.Event()
    monitor = sm.SysMonitor(
        cpu_reader=Reader(gate=gate, entered=entered), mem_reader=lambda: 61.0)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert entered.wait(5.0), "采样线程未进入读数"
        started = time.monotonic()
        monitor.close()
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"close() 阻塞了 {elapsed:.2f}s（应 ≤ 有界 join 预算）"
        assert monitor.running is False
    finally:
        gate.set()
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0), \
        f"放行后线程未退出：{_live_sys_threads()}"


def test_stop_then_start_does_not_leave_two_live_threads():
    """stop→start 紧邻（岛隐藏后马上显示）：旧线程只认自己的停止事件，不得复活。"""
    monitor = sm.SysMonitor(cpu_reader=Reader(), mem_reader=lambda: 61.0)
    monitor.subscribe(isl.CPU)
    monitor.start()
    monitor.stop(wait=False)
    monitor.start()
    monitor.stop(wait=True)
    assert _wait_until(lambda: not _live_sys_threads(), timeout=5.0), \
        f"停止后仍有采样线程在跑：{_live_sys_threads()}"
    monitor.close()


def test_stale_generation_result_never_overwrites_new_snapshot():
    """旧代在途读数晚到，不得覆盖新代快照（stop→start 只有停止事件隔离不够）。

    复现路径：第一代卡在读数 → stop→start（第二代立刻出结果）→ 放行第一代。
    第一代的样本时间戳更晚，若共享快照/无代核验就会盖掉新代结果。
    "第一代已收口"用它的线程对象 join 确认，不用 sleep 猜时序。
    """
    gate = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}
    stale_threads: list[threading.Thread] = []

    def cpu_reader():
        calls["n"] += 1
        if calls["n"] == 1:
            stale_threads.append(threading.current_thread())
            entered.set()
            gate.wait(5.0)
        return 12.0

    samples: list[sm.SysSample] = []
    monitor = sm.SysMonitor(
        cpu_reader=cpu_reader, mem_reader=lambda: 61.0, on_sample=samples.append)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert entered.wait(5.0), "旧代未进入读数"
        stale_thread = stale_threads[0]
        monitor.stop(wait=False)
        monitor.start()
        assert _wait_until(lambda: bool(samples), timeout=5.0), "新代未出样本"
        fresh = monitor.snapshot()
        assert fresh is samples[-1]

        monitor.stop(wait=True)  # 先稳住新代：下面只观察旧代的迟到结果
        gate.set()
        stale_thread.join(5.0)
        assert not stale_thread.is_alive(), "旧代未收口"
        assert monitor.snapshot() is fresh, "旧代晚到的样本覆盖了新代快照"
        assert samples[-1] is fresh, "旧代晚到的样本仍然触发了回调"
    finally:
        gate.set()
        monitor.stop(wait=True)
        monitor.close()


# ---------------------------------------------------------------- GPU 接线的向后兼容面
def test_cpu_only_run_never_constructs_a_gpu_reader_or_touches_pdh(monkeypatch):
    """CPU-only（默认工厂路径）：一个 GPU 读数器都不建，PDH 一次都不踩。

    共享采样器现在也能喂 GPU 段，但"没订阅就不采"对 GPU 同样成立：工厂没被调用 =
    没有 ``GpuReader`` = 没加载 ``pdh.dll``、没建 query。这是把"无订阅零开销"钉在
    默认工厂路径上的守卫（而不是只钉在注入替身的用例上）。
    """
    from pet import gpu_monitor as gpu_mod

    def boom(*args, **kwargs):  # pragma: no cover - 只有违约时才会走到
        raise AssertionError("CPU-only 仍然构造了 GPU 读数器 / 踩了 PDH")

    monkeypatch.setattr(gpu_mod, "GpuReader", boom)
    monkeypatch.setattr(gpu_mod, "WindowsPdh", boom)
    samples: list[sm.SysSample] = []
    monitor = sm.SysMonitor(
        cpu_reader=Reader(), mem_reader=lambda: 61.0, on_sample=samples.append)
    monitor.subscribe(isl.CPU)
    monitor.start()
    try:
        assert _wait_until(lambda: len(samples) >= 2, timeout=5.0), "CPU 样本未出"
        assert monitor.gpu_subscriber_count == 0
        assert monitor.gpu_snapshot() is None
    finally:
        monitor.close()


def test_sample_once_does_not_start_gpu_sampling():
    """``sample_once`` 只管设备槽：GUI 线程调它不该在 GUI 线程里建 PDH query
    （读数器绑定创建线程；进程内第一次 read 还要付约 0.54s 的 provider 装载）。"""
    created: list[object] = []

    def factory():
        created.append(threading.get_ident())
        raise AssertionError("sample_once 触发了 GPU 读数器创建")

    monitor = sm.SysMonitor(
        cpu_reader=Reader(), mem_reader=lambda: 61.0, gpu_reader_factory=factory)
    monitor.subscribe_gpu(isl.GPU)
    sample = monitor.sample_once(now=3.0)
    assert sample.state == sm.STATE_OK
    assert created == []
    assert monitor.gpu_snapshot() is None
    monitor.close()
