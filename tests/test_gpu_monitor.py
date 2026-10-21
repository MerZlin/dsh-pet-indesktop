# -*- coding: utf-8 -*-
"""``pet/gpu_monitor.py``（GPU 占用数据源，Windows PDH）单测。

本模块零 Qt、**零线程**：采样节拍（1Hz、无订阅不采样、有界收口）由下一批的
``SysMonitor`` 持有，所以这里不测时序，只测四件事：

1. **纯聚合**：引擎级（``(luid,phys,eng)`` 跨 pid 求和夹紧 100）→ 适配器级（引擎取
   max）→ 跨适配器取 max；乱序、多 pid 多引擎、NaN/inf、无有效项、软件适配器剔除、
   无名字适配器按真实 LUID 标识。
2. **读数生命周期**：首拍只建基线、长停恢复重新 priming、有界重试、句柄释放
   （含异常路径）、重复 close 幂等、**跨线程 close / 跨线程 read 明确失败**、
   非 Windows 明确 ``unsupported``。
   **释放失败有它自己的语义**：原生 ``PdhCloseQuery`` 返回错误码时句柄状态未知
   （微软文档只承诺成功路径收干净，失败路径没说），所以那种情况一律不报"已关闭"、
   不丢句柄、不把句柄转给非 owner 线程，只留诊断让 owner 明确再试一次。
3. **原生边界（fault injection）**：假 PDH / 假 DXGI 各按真实 ABI 造出来，
   专打"看不出来"的越界与 COM 误用——不是本机跑不出问题就当作没问题。
4. **真实后端短探针**（Windows only）：冷启动 / 稳态 / 资源关闭的实测记录，
   证据写进 ``.scratch/product-gpu-implementation-20261009/``。

PDH 的替身（:class:`FakePdh`）实现的是本模块真正会调的那五个方法，两条登记在案的
静默出 0 路径（通配计数器走标量、raw/formatted 按下标 zip）在这里没有可乘之机——
本模块只走数组 API 且按 ``szName`` 建键。
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from pet import gpu_monitor as gm
from pet.island import segments as isl

#: 本机探针实测过的三块适配器（名字来自 DXGI）+ 一块只有 PDH 认识的适配器。
LUID_AMD = "0x00000000_0x00017F06"
LUID_SW = "0x00000000_0x0001A546"
LUID_NV = "0x00000000_0x0001A59A"
LUID_GHOST = "0x00000000_0x0001B5F6"

#: ``IID_IDXGIFactory1``（本机 SDK ``shared/dxgi.h:2553``，非记忆）。
IID_IDXGIFACTORY1 = (0x770AAE78, 0xF26F, 0x4DBA,
                     (0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
DXGI_ERROR_NOT_FOUND = 0x887A0002
E_INVALIDARG = 0x80070057
E_OUTOFMEMORY = 0x8007000E

#: COM 虚表替身只能在能造 ``WINFUNCTYPE`` 回调的平台上搭。
requires_com = pytest.mark.skipif(not hasattr(ctypes, "WINFUNCTYPE"),
                                  reason="造 COM 虚表替身需要 WINFUNCTYPE")


def _name(pid, luid, eng, engtype, phys=0):
    """按探针实测的文法造一个实例名（``engtype`` 可以是含空格的名字）。"""
    high, low = luid.split("_")
    return f"pid_{pid}_luid_{high}_{low}_phys_{phys}_eng_{eng}_engtype_{engtype}"


class FakeClock:
    """单调时钟替身：长停/恢复的判定要能"跳"，不靠真睡。"""

    def __init__(self, now: float = 1000.0):
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakePdh:
    """PDH API 替身：只实现 :class:`gm.GpuReader` 真正会调的五件事。

    ``array_steps`` 逐条应答 ``counter_array``（用来造两段式读取里的尺寸增长 / 重试
    耗尽）；用尽后回落到语义化默认：问尺寸回 ``PDH_MORE_DATA``，带尺寸取数据回成功。
    """

    def __init__(self, *, items=(), array_steps=None, always_more=False,
                 open_error=None, add_error=None, collect_error=None, close_error=None):
        # 收 ``(名字, 读数)``（默认 CStatus 有效）或 ``(名字, CStatus, 读数)`` 三态全写。
        self.items = [tuple(item) if len(item) == 3
                      else (item[0], gm.PDH_CSTATUS_VALID_DATA, item[1])
                      for item in items]
        self.array_steps = list(array_steps or ())
        self.always_more = always_more
        self.open_error = open_error
        self.add_error = add_error
        self.collect_error = collect_error
        self.close_error = close_error
        self.calls: list = []
        self.opened = 0
        self.closed = 0
        self.collects = 0

    def open_query(self):
        self.calls.append("open")
        if self.open_error is not None:
            raise self.open_error
        self.opened += 1
        return object()

    def add_counter(self, query, path):
        self.calls.append(("add", path))
        if self.add_error is not None:
            raise self.add_error
        return object()

    def collect(self, query):
        self.calls.append("collect")
        self.collects += 1
        if self.collect_error is not None:
            raise self.collect_error

    def counter_array(self, counter, size=None):
        self.calls.append(("array", size))
        if self.array_steps:
            status, out_size, count = self.array_steps.pop(0)
            return status, out_size, count, (list(self.items) if status == 0 else [])
        if size is None or self.always_more:
            grow = max(len(self.items), 1) * 24 if size is None else int(size) * 2
            return gm.PDH_MORE_DATA, grow, max(len(self.items), 1), []
        return 0, int(size), len(self.items), list(self.items)

    def close_query(self, query):
        self.calls.append("close")
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def _reader(items=(), *, clock=None, provider=None, **api_kw):
    """造一个注入式 reader：PDH、时钟、适配器名字全部是替身，永不碰真系统。"""
    fake = FakePdh(items=items, **api_kw)
    clock = clock if clock is not None else FakeClock()
    gate = provider if provider is not None else (lambda: {})
    reader = gm.GpuReader(api=fake, adapter_provider=gate, clock=clock,
                          max_gap_seconds=5.0)
    return reader, fake, clock


def _boom():
    raise RuntimeError("DXGI 挂掉了")


# ---------------------------------------------------------------- 实例名解析
def test_parse_engine_instance_uses_recorded_grammar():
    parsed = gm.parse_engine_instance(
        "pid_10932_luid_0x00000000_0x00017F06_phys_0_eng_0_engtype_3D")
    assert parsed is not None
    key, engtype = parsed
    assert key == gm.EngineKey(luid=LUID_AMD, phys=0, eng=0)
    assert engtype == "3D"

    # engtype 含空格（探针实测 17 种里有 5 种带空格）→ 只能是行尾捕获组
    for raw, expected in (("Video Codec 0", "Video Codec 0"),
                          ("High Priority Compute", "High Priority Compute"),
                          ("Compute 1", "Compute 1")):
        parsed = gm.parse_engine_instance(_name(7, LUID_NV, 3, raw))
        assert parsed is not None and parsed[1] == expected

    # LUID 两半按 8 位十六进制规范化：短写与长写是同一个键
    short = gm.parse_engine_instance(_name(7, "0x1F06_0x1", 0, "3D"))
    assert short is not None and short[0].luid == "0x00001F06_0x00000001"


def test_parse_engine_instance_rejects_junk_without_guessing():
    for junk in ("", None, 42, "gpu", "pid_1_luid_0x0_0x1_phys_0",
                 "luid_0x00000000_0x00017F06_phys_0_eng_0_engtype_3D",
                 "pid_x_luid_0x0_0x1_phys_0_eng_0_engtype_3D",
                 "pid_1_luid_0x0_0x1_phys_0_eng_0"):
        assert gm.parse_engine_instance(junk) is None, junk


# ---------------------------------------------------------------- 纯聚合
def test_group_engines_sums_across_pids_and_clamps_at_100():
    items = [
        (_name(1, LUID_AMD, 0, "3D"), 30.0),
        (_name(2, LUID_AMD, 0, "3D"), 40.0),
        (_name(3, LUID_AMD, 0, "3D"), 50.0),  # 120 → 夹到 100
        (_name(1, LUID_AMD, 1, "Copy"), 5.0),
        (_name(9, LUID_NV, 4, "Video Decode"), 2.0),
    ]
    engines = gm.group_engines(items)
    assert [(e.key.luid, e.key.eng, e.percent) for e in engines] == [
        (LUID_AMD, 0, 100.0), (LUID_AMD, 1, 5.0), (LUID_NV, 4, 2.0),
    ]
    busiest_amd = engines[0]
    assert busiest_amd.samples == 3 and busiest_amd.clamped is True


def test_group_engines_keeps_engines_apart_even_with_same_engtype():
    # 探针实测：同一 (luid,phys) 下 17 个不同 eng_N 共用 engtype "3D"。
    # 按 engtype 聚合会把 17 个独立硬件引擎混成一条，所以键必须是引擎序数。
    items = [(_name(1, LUID_AMD, index, "3D"), 10.0) for index in range(17)]
    engines = gm.group_engines(items)
    assert len(engines) == 17
    assert {e.key.eng for e in engines} == set(range(17))


def test_group_engines_is_order_independent():
    items = [
        (_name(1, LUID_AMD, 0, "3D"), 30.0),
        (_name(2, LUID_AMD, 0, "Compute"), 40.0),  # 同键不同 engtype（探针实测 0 冲突，仍须确定性）
        (_name(3, LUID_NV, 1, "Copy"), 7.0),
        (_name(3, LUID_NV, 1, "Copy"), 1.0),
    ]
    forward = gm.group_engines(items)
    backward = gm.group_engines(list(reversed(items)))
    assert forward == backward
    # 同键冲突的 engtype 取字典序最小者（"3D" < "Compute"），与输入序无关
    assert [e.engtype for e in forward if e.key.luid == LUID_AMD] == ["3D"]


def test_group_engines_skips_non_finite_and_unparseable_items():
    first = _name(1, LUID_AMD, 0, "3D")
    bad_values = (float("nan"), float("inf"), float("-inf"), None, "不是数字")
    items = [(first, value) for value in bad_values]
    items += [
        (first, 0.0),  # 合法的 0 必须留下（0 与"没有读数"是两件事）
        ("它不是一个实例名", 99.0),
        "not-a-pair",
        ((_name(2, LUID_AMD, 0, "3D"), 12.5)),
    ]
    engines = gm.group_engines(items)
    assert [(e.key, e.percent, e.samples) for e in engines] == [
        (gm.EngineKey(LUID_AMD, 0, 0), 12.5, 2),
    ]


def test_adapter_readings_max_over_engines_then_max_across_adapters():
    engines = gm.group_engines([
        (_name(1, LUID_AMD, 0, "3D"), 27.5),
        (_name(2, LUID_AMD, 1, "Copy"), 0.3),
        (_name(1, LUID_AMD, 2, "Compute"), 1.0),
        (_name(3, LUID_NV, 0, "3D"), 0.0),
        (_name(4, LUID_GHOST, 0, "3D"), 0.0),
    ])
    # 适配器级是 max 不是求和：27.5 + 0.3 + 1.0 = 28.8 是错的口径
    adapters = gm.adapter_readings(engines)
    assert [(a.luid, a.percent) for a in adapters] == [
        (LUID_AMD, 27.5), (LUID_NV, 0.0), (LUID_GHOST, 0.0),
    ]
    assert adapters[0].busiest is not None and adapters[0].busiest.key.eng == 0
    # 跨适配器取 max：三块加起来 27.8 是错的
    assert gm.busiest_adapter(adapters).luid == LUID_AMD
    assert gm.busiest_adapter(adapters).percent == 27.5


def test_adapter_readings_is_order_independent():
    engines = gm.group_engines([
        (_name(1, LUID_AMD, 0, "3D"), 5.0),
        (_name(1, LUID_NV, 0, "3D"), 5.0),
        (_name(1, LUID_GHOST, 0, "3D"), 5.0),
    ])
    forward = gm.adapter_readings(engines)
    backward = gm.adapter_readings(tuple(reversed(engines)))
    assert forward == backward
    assert gm.busiest_adapter(forward).luid == LUID_AMD  # 同分按 luid 定序，不随机


def test_software_adapter_is_dropped_only_when_dxgi_says_so():
    engines = gm.group_engines([
        (_name(1, LUID_AMD, 0, "3D"), 12.0),
        (_name(1, LUID_SW, 0, "3D"), 0.0),
    ])
    known = {
        LUID_AMD: gm.AdapterInfo(luid=LUID_AMD, name="AMD Radeon(TM) Graphics", is_software=False),
        LUID_SW: gm.AdapterInfo(luid=LUID_SW, name="Microsoft Basic Render Driver", is_software=True),
    }
    filtered = gm.adapter_readings(engines, known=known)
    assert [a.luid for a in filtered] == [LUID_AMD]

    # DXGI 不可用（known=None）→ 一块都不剔、一个数都不改，且 is_software 保持"不知道"
    unfiltered = gm.adapter_readings(engines, known=None)
    assert {(a.luid, a.percent) for a in unfiltered} == {(LUID_AMD, 12.0), (LUID_SW, 0.0)}
    assert {a.is_software for a in unfiltered} == {None}
    assert {a.name for a in unfiltered} == {""}


def test_adapter_label_uses_real_luid_and_never_invents_a_name():
    engines = gm.group_engines([
        (_name(1, LUID_NV, 0, "3D"), 31.0),
        (_name(1, LUID_GHOST, 3, "Video Decode"), 4.0),
    ])
    known = {LUID_NV: gm.AdapterInfo(luid=LUID_NV,
                                     name="NVIDIA GeForce RTX 4060 Laptop GPU",
                                     is_software=False)}
    by_luid = {a.luid: a for a in gm.adapter_readings(engines, known=known)}
    assert gm.adapter_label(by_luid[LUID_NV]) == "NVIDIA GeForce RTX 4060 Laptop GPU · 3D"

    label = gm.adapter_label(by_luid[LUID_GHOST])
    assert LUID_GHOST in label and "Video Decode" in label
    for guessed in ("GPU 1", "GPU1", "图形适配器 1", "显卡 1"):
        assert guessed not in label


def test_adapter_label_survives_an_adapter_without_engines():
    adapter = gm.AdapterReading(luid=LUID_GHOST)
    assert gm.adapter_label(adapter) == f"{LUID_GHOST}(未命名适配器)"


def test_format_gpu_never_fakes_zero():
    assert gm.format_gpu(24.4) == "24%"
    assert gm.format_gpu(0.0) == "0%"
    for bad in (None, float("nan"), float("inf"), float("-inf"), "x"):
        assert gm.format_gpu(bad) == isl.UNKNOWN_TEXT == "--"


# ---------------------------------------------------------------- 惰性 / 生命周期
def test_reader_touches_no_pdh_and_creates_no_thread_before_read(monkeypatch):
    created = []

    class SpyWindowsPdh:
        def __init__(self, *args, **kwargs):
            created.append(1)
            raise AssertionError("未订阅就实例化了 PDH")

    monkeypatch.setattr(gm, "WindowsPdh", SpyWindowsPdh)
    before_threads = threading.active_count()

    reader = gm.GpuReader()
    assert created == []
    assert reader.opened is False
    assert reader.primed is False
    reader.close()  # 从没读过就 close：no-op，不抛
    assert created == []
    assert threading.active_count() == before_threads

    # 注入替身的路径同样"没读就没碰"
    reader2, fake, _ = _reader()
    assert fake.calls == []
    assert reader2.opened is False


def test_first_sample_only_primes_the_baseline():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 88.0)])
    first = reader.read()
    assert first.state == gm.STATE_SAMPLING
    assert first.percent is None and first.error_reason == ""
    assert fake.collects == 1
    assert [c for c in fake.calls if isinstance(c, tuple) and c[0] == "array"] == []
    assert reader.primed is True and reader.opened is True


def test_second_sample_reports_the_busiest_engine():
    items = [(_name(1, LUID_AMD, 0, "3D"), 21.0),
             (_name(2, LUID_AMD, 0, "3D"), 6.5),
             (_name(1, LUID_NV, 3, "Copy"), 1.25)]
    reader, fake, clock = _reader(items)
    reader.read()
    clock.advance(1.0)
    second = reader.read()
    assert second.state == gm.STATE_OK
    assert second.percent == 27.5
    assert second.adapter is not None and second.adapter.luid == LUID_AMD
    assert second.detail["engine"] == "3D"
    assert second.detail["adapter_luid"] == LUID_AMD
    assert second.detail["unit_full"] == gm.UNIT_FULL
    assert "error_reason" not in second.detail


def test_long_gap_rebuilds_the_baseline_instead_of_reporting_a_window_average():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 40.0)])
    reader.read()                     # 建基线
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_OK
    clock.advance(120.0)              # 长停（无人订阅时只是不 collect）
    resumed = reader.read()
    assert resumed.state == gm.STATE_SAMPLING and resumed.percent is None
    assert fake.collects == 3
    clock.advance(1.0)                # 恢复后的第二拍才有可信读数
    assert reader.read().percent == 40.0


def test_no_valid_items_is_unknown_not_zero():
    fake_items = [(_name(1, LUID_AMD, 0, "3D"), 0xC0000BBA, 55.0)]
    reader, fake, clock = _reader(fake_items)
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_UNKNOWN
    assert sample.percent is None
    assert "error_reason" in sample.detail


def test_empty_counter_array_is_unknown_not_zero():
    reader, fake, clock = _reader([], array_steps=[(gm.PDH_NO_DATA, 0, 0)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_UNKNOWN
    assert sample.percent is None
    assert sample.error_reason


def test_zero_item_array_is_unknown_not_zero():
    reader, fake, clock = _reader([], array_steps=[(gm.PDH_MORE_DATA, 0, 0)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_UNKNOWN
    assert sample.percent is None


def test_missing_counter_object_is_error_with_a_reason():
    reader, fake, clock = _reader(
        open_error=gm.PdhError(gm.PDH_CSTATUS_NO_OBJECT))
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR
    assert sample.percent is None
    assert "GPU" in sample.error_reason and sample.error_reason in sample.detail["error_reason"]
    assert reader.opened is False


def test_failed_add_counter_releases_the_half_open_query():
    reader, fake, clock = _reader(
        add_error=gm.PdhError(gm.PDH_CSTATUS_NO_COUNTER))
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR
    assert fake.opened == 1 and fake.closed == 1
    assert reader.opened is False


def test_collect_failure_is_error_and_forces_a_new_baseline():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)])
    reader.read()
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_OK
    fake.collect_error = gm.PdhError(gm.PDH_INVALID_HANDLE)
    clock.advance(1.0)
    failed = reader.read()
    assert failed.state == gm.STATE_ERROR and failed.percent is None
    fake.collect_error = None
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_SAMPLING  # 基线重建，不拿旧值充数
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_OK


def test_invalid_data_status_is_treated_as_still_sampling():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 10.0)],
                                  array_steps=[(gm.PDH_CSTATUS_INVALID_DATA, 0, 0)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_SAMPLING and sample.percent is None
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_OK


# ---------------------------------------------------------------- 有界性
def test_buffer_growth_is_retried_within_bounds_then_succeeds():
    items = [(_name(1, LUID_AMD, 0, "3D"), 18.0)]
    reader, fake, clock = _reader(
        items, array_steps=[(gm.PDH_MORE_DATA, 24, 1), (gm.PDH_MORE_DATA, 48, 2)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_OK and sample.percent == 18.0
    sizes = [call[1] for call in fake.calls if isinstance(call, tuple) and call[0] == "array"]
    assert sizes == [None, 24, 48]  # 尺寸变了就用新尺寸重取，不读半截缓冲


def test_buffer_growth_retries_are_bounded():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 18.0)], always_more=True)
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR and sample.percent is None
    assert "缓冲区" in sample.error_reason
    arrays = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "array"]
    assert len(arrays) == 1 + gm.MAX_ARRAY_RETRIES


def test_item_cap_refuses_to_allocate_for_an_unbounded_count():
    reader, fake, clock = _reader(
        [], array_steps=[(gm.PDH_MORE_DATA, 24, gm.MAX_ENGINE_ITEMS + 1)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR
    assert str(gm.MAX_ENGINE_ITEMS) in sample.error_reason
    arrays = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "array"]
    assert len(arrays) == 1  # 一条数据取数都没发出去


def test_item_cap_boundary_is_inclusive():
    reader, fake, clock = _reader(
        [(_name(1, LUID_AMD, 0, "3D"), 9.0)],
        array_steps=[(gm.PDH_MORE_DATA, 24576, gm.MAX_ENGINE_ITEMS)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_OK and sample.percent == 9.0


def test_oversized_buffer_is_refused_even_when_the_count_looks_small():
    reader, fake, clock = _reader(
        [], array_steps=[(gm.PDH_MORE_DATA, gm.MAX_ARRAY_BYTES + 1, 2)])
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR
    assert str(gm.MAX_ARRAY_BYTES) in sample.error_reason
    arrays = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "array"]
    assert len(arrays) == 1


def test_empty_and_wrong_input_to_the_pure_aggregation():
    assert gm.group_engines(None) == ()
    assert gm.group_engines([]) == ()
    assert gm.adapter_readings(()) == ()
    assert gm.busiest_adapter(()) is None
    assert gm.busiest_adapter(None) is None
    assert gm.adapter_label(None) == ""
    assert gm.parse_engine_instance(123) is None
    assert gm.parse_engine_instance(b"pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D") is None
    with pytest.raises(ValueError):
        gm.canonical_luid("不是数字", 1)


# ---------------------------------------------------------------- 句柄 / 线程
def test_close_is_idempotent_when_the_release_succeeds():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)])
    reader.read()
    assert reader.opened is True
    assert reader.close() is True
    assert reader.close() is True
    assert reader.close() is True
    assert reader.closed is True and reader.opened is False
    assert reader.release_error == ""
    assert fake.closed == 1  # 只真的关一次


def test_a_failed_release_is_never_reported_as_closed():
    """原生 close 失败 = 句柄状态未知：不许返回成功、不许把句柄从手里丢掉。

    微软文档只把"关掉句柄、释放内存"承诺给成功路径，失败路径只列错误码
    （``PDH_INVALID_HANDLE`` 的原文是"the query handle is not valid"），**没有**说失败时
    已经收干净。所以这里不猜：句柄留在手上、``opened`` 仍为真、诊断进 ``release_error``。
    """
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)],
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    reader.read()
    assert reader.close() is False          # 没关掉就报没关掉
    assert reader.closed is False and reader.opened is True
    assert "PDH_INVALID_HANDLE" in reader.release_error

    assert reader.close() is False          # 明确的第二次尝试：仍然失败
    assert fake.closed == 2                 # 每次显式 close 恰好一次原生调用，不内部循环重试
    assert reader.closed is False and reader.opened is True


def test_owner_retry_after_a_failed_release_succeeds_and_clears_the_diagnosis():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)],
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    reader.read()
    assert reader.close() is False
    fake.close_error = None                 # 下一次原生 close 能成功
    assert reader.close() is True
    assert fake.closed == 2
    assert reader.closed is True and reader.opened is False
    assert reader.release_error == ""
    clock.advance(1.0)
    sample = reader.read()                  # 释放成功后重新建 query：第一拍只建基线
    assert sample.state == gm.STATE_SAMPLING and fake.opened == 2


def test_read_after_a_failed_release_refuses_without_touching_pdh():
    """失败后不许把旧句柄"覆盖丢弃"：read 既不开新 query，也不悄悄换掉手里的句柄。"""
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)],
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    reader.read()
    assert reader.close() is False
    before = (fake.opened, fake.closed, fake.collects)
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR and sample.percent is None
    assert "释放" in sample.error_reason
    assert (fake.opened, fake.closed, fake.collects) == before   # 一次原生调用都没多
    assert reader.opened is True and reader.closed is False      # 旧句柄仍在手上


def test_a_retained_handle_is_never_handed_to_a_foreign_thread():
    """释放失败后的句柄仍可能有效：非 owner 既不能读它、也不能替 owner 丢它。"""
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)],
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    reader.read()
    assert reader.close() is False
    box = {}

    def worker():
        box["close"] = reader.close()
        box["read"] = reader.read()

    thread = threading.Thread(target=worker, name="gpu-foreign-retained")
    thread.start()
    thread.join(5.0)
    assert not thread.is_alive()
    assert box["close"] is False
    assert box["read"].state == gm.STATE_ERROR
    assert fake.closed == 1                 # 非 owner 不能替它重试
    assert reader.opened is True            # 句柄没被转手、也没被丢掉


def test_failed_add_counter_cleanup_keeps_the_half_open_query_visible():
    """``add_counter`` 失败后的收口如果也失败，半开 query 不能就这么消失。"""
    reader, fake, clock = _reader(add_error=gm.PdhError(gm.PDH_CSTATUS_NO_COUNTER),
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    sample = reader.read()
    assert sample.state == gm.STATE_ERROR           # 仍报 add_counter 的真实原因
    assert fake.opened == 1 and fake.closed == 1    # 收口试过一次
    assert reader.opened is True and reader.closed is False
    assert reader.release_error                     # 收口失败可见，不是静默泄露

    before = (fake.opened, fake.closed)
    assert reader.read().state == gm.STATE_ERROR    # 不再开第二只 query
    assert (fake.opened, fake.closed) == before

    fake.close_error = None
    assert reader.close() is True                   # owner 明确重试才收干净
    assert fake.closed == 2 and reader.opened is False and reader.closed is True
    assert reader.release_error == ""


def test_close_from_a_foreign_thread_must_not_close_the_owner_query():
    """query 归创建它的线程：别人来 close 只能失败，不能替你把句柄收了。"""
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)])
    reader.read()
    box = {}

    def worker():
        box["result"] = reader.close()

    thread = threading.Thread(target=worker, name="gpu-foreign-close")
    thread.start()
    thread.join(5.0)
    assert not thread.is_alive()
    assert box["result"] is False          # 明确失败，不吞成成功
    assert reader.opened is True           # 句柄还在
    assert fake.closed == 0                # 一次都没真的关

    assert reader.close() is True          # owner 自己关才生效
    assert fake.closed == 1


def test_close_without_a_query_is_a_no_op_success():
    reader, fake, clock = _reader()
    assert reader.close() is True
    assert reader.close() is True
    assert fake.calls == []
    assert reader.closed is True and reader.opened is False
    assert reader.release_error == ""


def test_a_worker_finally_does_not_claim_the_handle_was_released():
    """采样线程的 finally 调 close：释放失败时它拿到的必须是"没关掉"，不是成功。

    这条同时钉住"owner 线程走掉之后谁也收不掉它"这一事实：句柄仍记在读数器上，
    主线程（非 owner）再 close 只能被拒绝——不把可能还有效的句柄转手出去。
    """
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)],
                                  close_error=gm.PdhError(gm.PDH_INVALID_HANDLE))
    box = {}
    done = threading.Event()

    def worker():
        try:
            reader.read()
            clock.advance(1.0)
            reader.read()
        finally:
            box["released"] = reader.close()
            box["opened"] = reader.opened
            box["closed"] = reader.closed
            done.set()

    thread = threading.Thread(target=worker, name="gpu-worker-finally")
    thread.start()
    assert done.wait(5.0)                  # 事件同步：不靠 sleep 猜时序
    thread.join(5.0)
    assert not thread.is_alive()
    assert box == {"released": False, "opened": True, "closed": False}
    assert fake.closed == 1                # finally 只试了一次原生 close，没有循环重试
    assert reader.close() is False         # 主线程不是 owner：不能替它丢句柄
    assert fake.closed == 1
    assert reader.opened is True


def test_close_reopens_on_the_next_read_with_a_fresh_baseline():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)])
    reader.read()
    clock.advance(1.0)
    assert reader.read().state == gm.STATE_OK
    assert reader.close() is True
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_SAMPLING  # 新 query，第一拍永远只建基线
    assert fake.opened == 2


def test_cross_thread_use_is_refused_without_touching_pdh():
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 30.0)])
    reader.read()
    before = fake.collects
    box = {}

    def worker():
        box["sample"] = reader.read()

    thread = threading.Thread(target=worker, name="gpu-cross-thread")
    thread.start()
    thread.join(5.0)
    assert not thread.is_alive()
    assert box["sample"].state == gm.STATE_ERROR
    assert "线程" in box["sample"].error_reason
    assert fake.collects == before


def test_module_has_no_third_party_dependency():
    """零新依赖：只许标准库 + 项目内模块（``pet.*``）。"""
    allowed = {"__future__", "ctypes", "dataclasses", "logging", "math", "re",
               "sys", "threading", "time", "typing", "pet"}
    for line in Path(gm.__file__).read_text(encoding="utf-8").splitlines():
        if not line.startswith(("import ", "from ")):
            continue
        top = line.split()[1].split(".")[0]
        assert top in allowed, f"新增了第三方依赖：{line!r}"


def test_module_import_is_platform_safe():
    """跨平台 import 安全：Windows 专有 API 只许出现在函数体内（模块级零调用）。"""
    source = Path(gm.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        for marker in ("WinDLL(", "WINFUNCTYPE("):
            if marker in line:
                assert line.startswith((" ", "\t")), f"模块级调用了 Windows 专有 API：{line!r}"


def test_reads_never_spawn_threads():
    """本模块只做读数：采样节拍与后台线程由下一批的 SysMonitor 持有。"""
    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 12.0)])
    before = {thread.ident for thread in threading.enumerate()}
    for _ in range(3):
        reader.read()
        clock.advance(1.0)
    assert {thread.ident for thread in threading.enumerate()} == before


def test_non_windows_reports_unsupported(monkeypatch):
    monkeypatch.setattr(gm, "_platform_supported", lambda: False)
    reader = gm.GpuReader()
    sample = reader.read()
    assert sample.state == gm.STATE_UNSUPPORTED
    assert sample.percent is None and sample.adapter is None
    assert "Windows" in sample.error_reason
    assert reader.opened is False
    # unsupported 段在胶囊上不占位（段契约的既有口径）
    assert isl.render_compact(isl.SegmentSnapshot(id="gpu", label="GPU",
                                                  max_text=gm.MAX_TEXT,
                                                  state=sample.state)) is None


# ---------------------------------------------------------------- DXGI 名字退化
def test_dxgi_failure_keeps_values_and_says_names_are_unavailable():
    items = [(_name(1, LUID_AMD, 0, "3D"), 33.0),
             (_name(1, LUID_SW, 0, "3D"), 0.0)]
    reader, fake, clock = _reader(items, provider=_boom)
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_OK
    assert sample.percent == 33.0            # 不许因为拿不到名字就把读数按 0 / 正常处理
    assert sample.detail["names_error"]
    assert sample.detail["adapter_name"] == ""
    assert gm.adapter_label(sample.adapter) == f"{LUID_AMD}(未命名适配器) · 3D"
    assert {a.luid for a in sample.adapters} == {LUID_AMD, LUID_SW}


def test_software_only_machine_is_unknown_not_zero():
    items = [(_name(1, LUID_SW, 0, "3D"), 0.0)]
    known = {LUID_SW: gm.AdapterInfo(luid=LUID_SW, name="Microsoft Basic Render Driver",
                                     is_software=True)}
    reader, fake, clock = _reader(items, provider=lambda: known)
    reader.read()
    clock.advance(1.0)
    sample = reader.read()
    assert sample.state == gm.STATE_UNKNOWN
    assert sample.percent is None
    assert sample.adapters == ()


def test_adapter_names_are_resolved_once_and_can_be_refreshed():
    calls = []
    known = {LUID_AMD: gm.AdapterInfo(luid=LUID_AMD, name="AMD Radeon(TM) Graphics",
                                      is_software=False)}

    def provider():
        calls.append(1)
        return known

    reader, fake, clock = _reader([(_name(1, LUID_AMD, 0, "3D"), 12.0)], provider=provider)
    reader.read()
    clock.advance(1.0)
    reader.read()
    clock.advance(1.0)
    reader.read()
    assert calls == [1]
    reader.refresh_adapters()
    clock.advance(1.0)
    reader.read()
    assert calls == [1, 1]


# ================================================================ 原生边界（fault injection）
# 下面两组用例直接按真实 ABI 造替身：COM 虚表是**每对象一份**（虚表指针存在对象首字），
# PDH 数组是"条目数组 + 名字区"的同一块缓冲区。它们专打"本机跑不出问题就等于没问题"
# 的盲区——这类缺陷在真机上只会偶发崩溃或静默错值，必须用注入的证据来钉。


class _FakeFn:
    """可挂 ``argtypes``/``restype`` 的假 DLL 方法（生产代码会给它们赋值）。"""

    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._fn(*args)


def _fake_dxgi(*, names=("AMD Radeon(TM) Graphics", "NVIDIA GeForce RTX 4060 Laptop GPU"),
               luids=((0x00000000, 0x00017F06), (0x00000000, 0x0001A59A)),
               flags=(0, 0), enum_error=None, enum_error_at=None, desc_error_at=None):
    """假 dxgi.dll：factory 与每个 adapter **各带自己的虚表**。

    这是 COM ABI 的最小复刻。``Release`` 在**每个**接口上的槽位号都是 2，所以
    "拿 factory 的 Release 去放 adapter" 在真机上不会报错、只会静默错放对象——
    这里两个回调分得清谁是谁，一错就露。
    """
    log = {"factory_releases": [], "adapter_releases": [], "iids": [], "enum_calls": []}
    keep: list = []
    release_t = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    enum_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_void_p))
    desc_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                ctypes.POINTER(gm._DxgiAdapterDesc1))
    create_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(gm._Guid),
                                  ctypes.POINTER(ctypes.c_void_p))

    adapters, adapter_addrs = [], []

    def _make_release(index):
        def _release(this):
            log["adapter_releases"].append((index, int(this or 0)))
            return 1
        return release_t(_release)

    def _make_desc(index):
        def _get_desc1(this, out):
            if desc_error_at is not None and index == desc_error_at:
                return E_INVALIDARG
            desc = out.contents
            desc.Description = names[index]
            desc.AdapterLuid.HighPart = luids[index][0]
            desc.AdapterLuid.LowPart = luids[index][1]
            desc.Flags = flags[index]
            return 0
        return desc_t(_get_desc1)

    for index in range(len(names)):
        release_cb, desc_cb = _make_release(index), _make_desc(index)
        vtable = (ctypes.c_void_p * 16)()
        vtable[2] = ctypes.cast(release_cb, ctypes.c_void_p)   # IUnknown::Release
        vtable[10] = ctypes.cast(desc_cb, ctypes.c_void_p)     # IDXGIAdapter1::GetDesc1
        obj = (ctypes.c_void_p * 1)(ctypes.cast(vtable, ctypes.c_void_p))
        keep += [release_cb, desc_cb, vtable, obj]
        adapters.append(obj)
        adapter_addrs.append(ctypes.cast(obj, ctypes.c_void_p))

    box = {}

    def _factory_release(this):
        log["factory_releases"].append(int(this or 0))
        return 1

    def _factory_enum(this, index, out):
        log["enum_calls"].append(index)
        if enum_error is not None and enum_error_at is not None and index >= enum_error_at:
            out[0] = None
            return enum_error
        if index >= len(adapters):
            out[0] = None
            return DXGI_ERROR_NOT_FOUND
        out[0] = adapter_addrs[index]
        return 0

    def _factory_create(iid, out):
        guid = iid.contents
        log["iids"].append((guid.Data1, guid.Data2, guid.Data3, tuple(guid.Data4)))
        out[0] = box["addr"]
        return 0

    factory_release_cb = release_t(_factory_release)
    factory_enum_cb = enum_t(_factory_enum)
    factory_create_cb = create_t(_factory_create)
    factory_vtable = (ctypes.c_void_p * 16)()
    factory_vtable[2] = ctypes.cast(factory_release_cb, ctypes.c_void_p)
    factory_vtable[12] = ctypes.cast(factory_enum_cb, ctypes.c_void_p)  # EnumAdapters1
    factory_obj = (ctypes.c_void_p * 1)(ctypes.cast(factory_vtable, ctypes.c_void_p))
    keep += [factory_release_cb, factory_enum_cb, factory_create_cb, factory_vtable, factory_obj]
    box["addr"] = ctypes.cast(factory_obj, ctypes.c_void_p)

    class _FakeDxgi:
        CreateDXGIFactory1 = _FakeFn(factory_create_cb)
        _keep = keep

    log["factory_addr"] = int(box["addr"].value or 0)
    log["adapter_addrs"] = [int(addr.value or 0) for addr in adapter_addrs]
    return _FakeDxgi(), log


def _use_fake_dxgi(monkeypatch, fake):
    monkeypatch.setattr(gm.ctypes, "WinDLL", lambda *args, **kwargs: fake)


@requires_com
def test_dxgi_releases_every_adapter_through_its_own_vtable(monkeypatch):
    fake, log = _fake_dxgi()
    _use_fake_dxgi(monkeypatch, fake)
    table = gm.dxgi_adapters()
    assert sorted(table) == [LUID_AMD, LUID_NV]
    assert table[LUID_AMD].name == "AMD Radeon(TM) Graphics"
    assert table[LUID_NV].is_software is False

    # 每个 adapter 恰好被自己的 Release 放掉一次
    assert sorted(index for index, _ in log["adapter_releases"]) == [0, 1]
    assert {addr for _, addr in log["adapter_releases"]} == set(log["adapter_addrs"])
    # factory 的 Release 只许作用在 factory 自己身上
    assert log["factory_releases"] == [log["factory_addr"]]


@requires_com
def test_dxgi_requests_the_factory1_iid_it_actually_uses(monkeypatch):
    fake, log = _fake_dxgi()
    _use_fake_dxgi(monkeypatch, fake)
    gm.dxgi_adapters()
    # 调的是 EnumAdapters1（IDXGIFactory1 的方法）→ 必须按 IDXGIFactory1 取工厂
    assert log["iids"] == [IID_IDXGIFACTORY1]


@requires_com
def test_dxgi_enumeration_failure_is_not_cached_as_a_partial_table(monkeypatch):
    fake, log = _fake_dxgi(names=("A", "B", "C"),
                           luids=((0, 1), (0, 2), (0, 3)),
                           enum_error=E_OUTOFMEMORY, enum_error_at=1)
    _use_fake_dxgi(monkeypatch, fake)
    with pytest.raises(gm.DxgiUnavailable):
        gm.dxgi_adapters()


@requires_com
def test_dxgi_get_desc_failure_is_not_silently_dropped(monkeypatch):
    fake, log = _fake_dxgi(names=("A", "B"), luids=((0, 1), (0, 2)), desc_error_at=1)
    _use_fake_dxgi(monkeypatch, fake)
    with pytest.raises(gm.DxgiUnavailable):
        gm.dxgi_adapters()


@requires_com
def test_dxgi_running_out_of_adapters_is_a_normal_end(monkeypatch):
    fake, log = _fake_dxgi(names=())
    _use_fake_dxgi(monkeypatch, fake)
    assert gm.dxgi_adapters() == {}
    assert log["enum_calls"] == [0]
    assert log["factory_releases"] == [log["factory_addr"]]


@requires_com
def test_fake_dxgi_itself_behaves_like_com(monkeypatch):
    """替身自检：打错一个槽位就该被这个替身抓出来（防"替身和实现一起错"）。

    它和上面五条一样是**按 COM ABI 造替身**的用例（``_fake_dxgi`` 用 ``WINFUNCTYPE``
    造虚表回调），所以走同一道 ``requires_com`` 闸门；纯解析用例不受此门影响。
    """
    fake, log = _fake_dxgi(names=("A",), luids=((0, 1),))
    _use_fake_dxgi(monkeypatch, fake)
    table = gm.dxgi_adapters()
    assert list(table) == ["0x00000000_0x00000001"]
    assert len(log["adapter_releases"]) == 1 and len(log["factory_releases"]) == 1
    assert log["adapter_releases"][0][1] != log["factory_addr"]


# ---------------------------------------------------------------- 原生 PDH 数组读取
class _RawItem(ctypes.Structure):
    """``PDH_FMT_COUNTERVALUE_ITEM_W`` 的**裸指针**版：名字保持指针，不自动转字符串。

    与生产结构同布局（指针 + CStatus + 对齐 + double = 24 字节），只是字段类型不同——
    替身用它才写得出"名字指针指向哪里"这种故障。
    """

    _fields_ = [("szName", ctypes.c_void_p), ("FmtValue", gm._FmtCounterValue)]


class _FakePdhDll:
    """假 pdh.dll：只实现 :meth:`counter_array` 真正会调的那个函数。

    ``report_count`` 与 ``alloc_size`` 故意可以**不自洽**——那正是真实世界里
    "PDH 说还有 N 条，但缓冲区只够 M 条"的形态。
    """

    def __init__(self, *, alloc_size, report_count, names_offset=None,
                 external_name=None, status=0):
        self.alloc_size = alloc_size
        self.report_count = report_count
        self.names_offset = names_offset
        self.external_name = external_name
        self.status = status
        self.calls = []
        self.PdhGetFormattedCounterArrayW = _FakeFn(self._call)

    def _call(self, counter, fmt, size_p, count_p, buffer):
        # 生产代码按真 API 传的是 ctypes.byref(...)，替身这边要显式转回可写指针
        out_size = ctypes.cast(size_p, ctypes.POINTER(ctypes.c_uint32))
        out_count = ctypes.cast(count_p, ctypes.POINTER(ctypes.c_uint32))
        self.calls.append(None if buffer is None else int(out_size[0]))
        out_size[0] = self.alloc_size
        out_count[0] = self.report_count
        if buffer is None:
            return gm.PDH_MORE_DATA
        if self.status != 0:
            return self.status
        item_size = ctypes.sizeof(_RawItem)
        base = ctypes.addressof(buffer)
        for index in range(self.report_count):
            item = _RawItem()          # 先在本地的结构体上写，再整块搬进缓冲区
            item.FmtValue.CStatus = gm.PDH_CSTATUS_VALID_DATA
            item.FmtValue.doubleValue = float(index + 1)
            ctypes.memmove(base + index * item_size, ctypes.byref(item), item_size)
        if self.names_offset is not None:
            # 像真 PDH 一样：名字写在条目数组之后的同一块缓冲区里
            raw = _name(1, LUID_AMD, 0, "3D").encode("utf-16-le") + b"\x00\x00"
            address = base + self.names_offset
            ctypes.memmove(address, raw, len(raw))
            self._set_name(buffer, 0, address, item_size)
        if self.external_name is not None:
            address = ctypes.cast(self.external_name, ctypes.c_void_p).value
            self._set_name(buffer, self.report_count - 1, address, item_size)
        return 0

    @staticmethod
    def _set_name(buffer, index, address, item_size):
        pointer = ctypes.c_void_p(address)
        ctypes.memmove(ctypes.addressof(buffer) + index * item_size, ctypes.byref(pointer),
                       ctypes.sizeof(pointer))


def test_native_reader_never_dereferences_past_the_declared_buffer(monkeypatch):
    """申报条数 > 缓冲区能装的条目数时，只许读到装得下的那部分。

    为了让**修复前**的越界读也是内存安全的，这里把 ``create_string_buffer`` 换成
    一块零填充的大缓冲：越界读落在全零区（``szName`` = NULL、值为 0），
    于是"读了几条"变成可断言的数字，而不是一次偶发崩溃。
    """
    guard = ctypes.create_string_buffer(1 << 18)     # 256KB，全零
    monkeypatch.setattr(gm.ctypes, "create_string_buffer", lambda size: guard)
    declared, count = 24576, 1025                    # 24576/24 = 1024 条装得下
    dll = _FakePdhDll(alloc_size=declared, report_count=count)
    api = gm.WindowsPdh(dll=dll)

    status, size, reported, _ = api.counter_array(object(), None)
    assert (status, size, reported) == (gm.PDH_MORE_DATA, declared, count)
    status, size, reported, items = api.counter_array(object(), size)

    assert status == 0
    assert len(items) == declared // ctypes.sizeof(_RawItem) == 1024
    assert all(cstatus == gm.PDH_CSTATUS_VALID_DATA for _, cstatus, _ in items)


def test_native_reader_keeps_names_that_live_inside_the_buffer():
    """真 PDH 的名字就在同一块缓冲区里（已实测核实）→ 合法名字必须照常取回。"""
    declared = 4096
    dll = _FakePdhDll(alloc_size=declared, report_count=2, names_offset=64)
    api = gm.WindowsPdh(dll=dll)
    _, size, _, _ = api.counter_array(object(), None)
    _, _, _, items = api.counter_array(object(), size)
    assert items[0][0] == _name(1, LUID_AMD, 0, "3D")
    assert items[0][2] == 1.0


def test_native_reader_rejects_a_name_pointer_outside_the_buffer():
    """名字指针不在缓冲区内 → 不许解引用，也不许当成一个"名字"。"""
    outside = ctypes.create_unicode_buffer("pid_9_luid_0x1_0x2_phys_0_eng_0_engtype_3D")
    dll = _FakePdhDll(alloc_size=4096, report_count=2, external_name=outside)
    api = gm.WindowsPdh(dll=dll)
    _, size, _, _ = api.counter_array(object(), None)
    _, _, _, items = api.counter_array(object(), size)
    assert items[0][0] == ""
    assert items[1][0] == ""
    assert [value for _, _, value in items] == [1.0, 2.0]  # 值不受名字影响


def test_name_bounds_helper_rejects_out_of_range_and_unterminated():
    """名字安全性的纯函数面：范围外与界内未终止都必须判为不可信。

    这里用**自有的**小缓冲区构造，不需要真 PDH——越界与未终止这两条路径
    在原生替身里跑会真的越界读，所以它们由这个纯函数负责钉死。
    """
    buffer = ctypes.create_string_buffer(16)
    base = ctypes.addressof(buffer)
    end = base + 16
    assert gm._safe_name(None, base, end) is None          # NULL
    assert gm._safe_name(base - 2, base, end) is None      # 起点在界外
    assert gm._safe_name(end, base, end) is None           # 末尾之后
    assert gm._safe_name(end - 1, base, end) is None       # 只剩 1 字节，装不下宽字符

    ctypes.memmove(base, "AB\0".encode("utf-16-le"), 6)
    assert gm._safe_name(base, base, end) == "AB"

    ctypes.memset(base, 0x41, 16)                          # 16 字节全是 'A'，没有终止符
    assert gm._safe_name(base, base, end) is None

    # 超长名字：另开一块装得下的缓冲区（往 16 字节缓冲里写 1026 字节就是测试自己的越界写）
    long_buffer = ctypes.create_string_buffer((gm.MAX_INSTANCE_NAME_CHARS + 2) * 2)
    long_base = ctypes.addressof(long_buffer)
    long_end = long_base + len(long_buffer.raw)
    payload = ("X" * gm.MAX_INSTANCE_NAME_CHARS).encode("utf-16-le") + b"\x00\x00"
    ctypes.memmove(long_base, payload, len(payload))
    assert gm._safe_name(long_base, long_base, long_end) is None


# ---------------------------------------------------------------- 真实后端短探针
SCRATCH_DIR = (Path(__file__).resolve().parents[1]
               / ".scratch" / "product-gpu-implementation-20261009")


@pytest.mark.skipif(sys.platform != "win32", reason="真实 PDH 后端只在 Windows 上可跑")
def test_real_backend_short_probe_records_startup_steady_and_close():
    """真实 PDH 后端的一次短探针：冷启动 / 稳态 / 资源关闭，证据落盘。

    这是本模块唯一的真实系统调用测试——不 mock、不 skip 成功、不伪造读数：
    探针拿到的每一个状态都是这台机器上的真实结果（包括"本机没有 GPU 计数器"
    这种非 0 退化，那时断言的是可解释的降级而不是假装通过）。
    """
    reader = gm.GpuReader()
    try:
        started = time.perf_counter()
        cold = reader.read()
        startup_s = time.perf_counter() - started
        time.sleep(1.0)
        steady_started = time.perf_counter()
        steady = reader.read()
        steady_s = time.perf_counter() - steady_started
        # 之后几拍：DXGI 名字已缓存，量到的是真正的稳态每拍成本（取中位数）
        warm_costs, warm = [], None
        for _ in range(3):
            time.sleep(0.2)
            mark = time.perf_counter()
            warm = reader.read()
            warm_costs.append(time.perf_counter() - mark)
        warm_s = sorted(warm_costs)[len(warm_costs) // 2]
        record = {
            "probe": "gpu-monitor-real-backend",
            "platform": sys.platform,
            "cold": {"state": cold.state, "percent": cold.percent,
                     "error_reason": cold.error_reason, "wall_s": round(startup_s, 6)},
            "steady": {"state": steady.state, "percent": steady.percent,
                       "error_reason": steady.error_reason, "wall_s": round(steady_s, 6),
                       "adapter": (steady.adapter.luid if steady.adapter else ""),
                       "engine": steady.detail.get("engine", ""),
                       "label": gm.adapter_label(steady.adapter) if steady.adapter else ""},
            "warm": {"state": warm.state, "percent": warm.percent,
                     "wall_s_median": round(warm_s, 6),
                     "wall_s_all": [round(cost, 6) for cost in warm_costs]},
            "adapters": [{"luid": a.luid, "percent": a.percent, "name": a.name,
                          "is_software": a.is_software} for a in steady.adapters],
            "names_error": steady.detail.get("names_error", ""),
        }
    finally:
        reader.close()
    record["closed"] = {"opened": reader.opened, "closed": reader.closed}
    try:
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        (SCRATCH_DIR / "real_backend_probe.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:  # 只读环境下证据落盘失败不该把探针判红
        pass
    print("GPU 真实后端探针：%s" % json.dumps(record, ensure_ascii=False))

    assert cold.state == gm.STATE_SAMPLING, record
    assert cold.percent is None, record
    # 第二拍要么给出可解释的读数，要么给出可解释的降级——不许 silent 0
    if steady.state == gm.STATE_OK:
        assert steady.percent is not None and 0.0 <= steady.percent <= 100.0, record
    else:
        assert steady.state in (gm.STATE_UNKNOWN, gm.STATE_ERROR, gm.STATE_UNSUPPORTED), record
        assert steady.error_reason, record
    assert reader.closed is True and reader.opened is False
    assert startup_s < 30.0 and steady_s < 5.0 and warm_s < 1.0, record  # 有界，不吊死
