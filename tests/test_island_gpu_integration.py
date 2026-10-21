# -*- coding: utf-8 -*-
"""GPU 段端到端接线（设置勾选 → 共享采样线程 → 胶囊读数 → 展开卡片详情）。

本文件管**接线**，不管数据源：PDH 的解析/聚合/降级是 ``tests/test_gpu_monitor.py``
的事，共享线程那两套订阅的活体行为是 ``tests/test_sys_monitor_gpu.py`` 的事，
段宽度的纯几何是 ``tests/test_island_unit_box.py`` 的事。这里回答四个问题：

1. **谁在采**：GPU 走 ``subscribe_gpu`` 那本账——CPU-only 一个 GPU 调用都不许发生
   （没有段就没有读数器，也就没有 PDH），GPU-only 不许订阅 cpu/mem。
2. **没有读数画什么**：首拍 / 恢复 / 不可读一律同宽 ``--``，0% 是**真读数**；
   盒宽恒为声明样本，0 → 9 → 100 一个像素都不动。
3. **恢复屏障**：暂停前那一代的快照不许在恢复后冒充实时值（hide→show、折叠→展开），
   必须等**新序列的第一份新样本**。
4. **降级与详情**：``unsupported`` 不占位（段增删，可以改宽）+ 卡片保原因；显卡名/
   引擎/量纲只进卡片，且放不下时仍能在 tooltip / 无障碍描述里读到。

时序纪律：替身用例全部同步直调（不赌定时器节奏）；唯一的真实后端用例
（``test_real_reader_reaches_the_island_through_the_qt_loop``）用宽预算轮询 Qt 事件
循环，只断言"接线通了"，不断言读数值。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet import gpu_monitor as gm
from pet.island import segments as isl
from pet import sys_monitor as sm
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.segments import CPU, GPU, MEM
from tests.test_island_segment_render import _config as _island_config
from tests.test_island_segment_render import _island

app = QApplication.instance() or QApplication([])

_LUID = "0x00000001_0x00000002"
_LONG_NAME = "NVIDIA GeForce RTX 4090 Ti SUPER EXTREME 24GB GDDR6X"


def _config(tmp_path, *, metrics=(), **island_overrides) -> Config:
    """配置 = 岛那一坨 + 顶层 ``island_metrics``（段开在哪个键上是产品的账）。"""
    cfg = _island_config(tmp_path, **island_overrides)
    cfg.set("island_metrics", list(metrics))
    return cfg


# ---------------------------------------------------------------- 样本素材
def _adapter(percent: float = 42.0, *, name: str = _LONG_NAME,
             engine: str = "3D") -> gm.AdapterReading:
    key = gm.EngineKey(luid=_LUID, phys=0, eng=0)
    return gm.AdapterReading(
        luid=_LUID, percent=percent,
        busiest=gm.EngineReading(key=key, percent=percent, engtype=engine),
        name=name)


def _gpu_sample(percent: float | None = 42.0, *, state: str = isl.STATE_OK,
                reason: str = "", age: float = 2.0, name: str = _LONG_NAME,
                engine: str = "3D", names_error: str = "") -> gm.GpuSample:
    """一条与真读数器同形的样本（字段口径逐条对齐 ``gpu_monitor._detail``）。"""
    adapter = _adapter(percent or 0.0, name=name, engine=engine)
    detail = {"unit_full": gm.UNIT_FULL, "source": gm.SOURCE_TEXT,
              "detail": gm.adapter_label(adapter),
              "adapter_luid": adapter.luid, "adapter_name": adapter.name,
              "engine": adapter.busiest.engtype if adapter.busiest else ""}
    if names_error:
        detail["names_error"] = names_error
    if reason:
        detail["error_reason"] = reason
    ok = state == isl.STATE_OK
    return gm.GpuSample(
        percent=percent if ok else None,
        sampled_at=time.monotonic() - age, state=state, error_reason=reason,
        adapter=adapter if ok else None, adapters=(adapter,) if ok else (),
        detail=detail)


def _sys_sample(cpu: float = 24.0, mem: float = 61.0, *, age: float = 1.0):
    return sm.SysSample(cpu_percent=cpu, mem_percent=mem,
                        sampled_at=time.monotonic() - age)


def _gpu_seg(island: DynamicIsland):
    """段管线里的 GPU 段（未收缩；折叠只发生在行计划里）。"""
    for segment in island._compose_info_segments():
        if segment.seg_id == GPU:
            return segment
    raise AssertionError(
        f"段列表里没有 GPU：{[s.seg_id for s in island._compose_info_segments()]}")


def _gpu_missing(island: DynamicIsland) -> bool:
    return GPU not in [s.seg_id for s in island._compose_info_segments()]


def _row(island: DynamicIsland, seg_id: str):
    for row in island._card_metric_rows():
        if row.seg_id == seg_id:
            return row
    raise AssertionError(f"卡片里没有 {seg_id!r}")


def _expanded(island: DynamicIsland) -> None:
    island.show()
    island.expand_card()
    island._finish_animations()
    app.processEvents()
    island._card_box.layout().activate()
    app.processEvents()


# ---------------------------------------------------------------- 谁在采
def test_gpu_only_subscribes_the_gpu_slot_and_nothing_else(tmp_path, monkeypatch):
    """GPU-only：只订 GPU 那本账，一条线程喂，且**不采** CPU/内存。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert len(made) == 1, "GPU 与设备槽共用同一条采样线程（不另起常驻线程）"
        monitor = made[0]
        assert monitor.gpu_subscribed == {GPU}
        assert monitor.subscribed == set(), "GPU-only 不该去读 psutil"
        assert monitor.started is True, "岛可见才开始采"
        assert island._sys_timer.isActive() is True
        assert island._sys_timer.interval() == 1000, "1Hz 上限不可配"
    finally:
        island.close()


def test_cpu_only_never_touches_the_gpu_slot(tmp_path, monkeypatch):
    """CPU-only：一个 GPU 调用都不许发生——没有段就没有读数器，也就没有 PDH。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, MEM]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        island._tick_sys()
        app.processEvents()
        assert made[0].gpu_calls == 0
        assert made[0].gpu_subscribed == set()
        assert island._gpu_sample is None
    finally:
        island.close()


def test_close_releases_both_subscriptions(tmp_path, monkeypatch):
    """窗口销毁：GPU 与设备槽的订阅一起归零（收口不停留在底座上）。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    island.show()
    app.processEvents()
    monitor = made[0]
    assert monitor.gpu_subscribed == {GPU}
    island.close()
    assert monitor.gpu_subscribed == set()
    assert monitor.subscribed == set()
    assert monitor.closed is True


# ---------------------------------------------------------------- 读数呈现
def test_first_sample_renders_the_reading_in_the_fixed_box(tmp_path, monkeypatch):
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert _gpu_seg(island).state == isl.STATE_SAMPLING, "还没采到第一拍"
        made[0].gpu_sample = _gpu_sample(42.0)
        island._tick_sys()
        segment = _gpu_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_OK, "42")
        assert segment.label == "GPU" and segment.unit == "%"
        assert segment.box_text == island_mod._GPU_VALUE_SAMPLE
    finally:
        island.close()


def test_before_the_first_sample_the_segment_shows_a_dash_not_a_zero(
        tmp_path, monkeypatch):
    """``--`` 与 0 是两件事：前者是"还没读到"，后者是"GPU 真的闲着"。"""
    island, _made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        segment = _gpu_seg(island)
        assert segment.text == isl.UNKNOWN_TEXT
        assert segment.state == isl.STATE_SAMPLING
    finally:
        island.close()


def test_a_real_zero_is_a_reading(tmp_path, monkeypatch):
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].gpu_sample = _gpu_sample(0.0)
        island._tick_sys()
        segment = _gpu_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_OK, "0")
    finally:
        island.close()


def test_the_value_box_never_changes_width_with_the_reading(tmp_path, monkeypatch):
    """0 → 9 → 100 只在盒内左扩：段样本、胶囊自然宽、窗口宽都不许动。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        geometry = []
        for value in (0.0, 9.0, 100.0):
            made[0].gpu_sample = _gpu_sample(value)
            island._tick_sys()
            assert _gpu_seg(island).text == f"{value:.0f}"
            geometry.append((island._capsule_width(), island.width(),
                             island._rest_size()))
        assert len(set(geometry)) == 1, f"读数改了岛宽：{geometry}"
    finally:
        island.close()


def test_sampling_states_keep_the_same_box(tmp_path, monkeypatch):
    """sampling / unknown / error 一律同宽 ``--``，岛宽与 ok 态逐像素相同。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].gpu_sample = _gpu_sample(42.0)
        island._tick_sys()
        ok_size = (island._capsule_width(), island._rest_size())
        for state, reason in ((isl.STATE_SAMPLING, ""),
                              (isl.STATE_UNKNOWN, "GPU 计数器当前没有数据"),
                              (isl.STATE_ERROR, "PDH 返回 0x800007D5")):
            made[0].gpu_sample = _gpu_sample(None, state=state, reason=reason)
            island._tick_sys()
            segment = _gpu_seg(island)
            assert (segment.state, segment.text) == (state, isl.UNKNOWN_TEXT)
            assert (island._capsule_width(), island._rest_size()) == ok_size
    finally:
        island.close()


def test_a_long_adapter_name_never_touches_the_capsule(tmp_path, monkeypatch):
    """长显卡名只进卡片：胶囊上只有一个 "GPU" 标签，几何与名字无关。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].gpu_sample = _gpu_sample(42.0, name="短名")
        island._tick_sys()
        short = (island._capsule_width(), _gpu_seg(island).label,
                 island.width())
        made[0].gpu_sample = _gpu_sample(42.0, name=_LONG_NAME)
        island._tick_sys()
        assert (island._capsule_width(), _gpu_seg(island).label,
                island.width()) == short
        assert short[1] == "GPU"
        assert _LONG_NAME in _row(island, GPU).hint, "名字必须能在卡片里读到"
    finally:
        island.close()


# ---------------------------------------------------------------- 恢复屏障
def test_hide_and_show_wait_for_a_fresh_sample(tmp_path, monkeypatch):
    """隐藏→显示：暂停前那一代的快照不许在恢复后冒充实时值。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        stale = _gpu_sample(42.0)
        made[0].gpu_sample = stale
        island._tick_sys()
        assert _gpu_seg(island).text == "42"

        island.hide()
        island.show()
        app.processEvents()
        assert _gpu_seg(island).state == isl.STATE_SAMPLING, "恢复后先报采样中"
        island._tick_sys()          # 底座还在吐暂停前那一份
        assert made[0].gpu_snapshot() is stale
        segment = _gpu_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_SAMPLING, isl.UNKNOWN_TEXT), \
            "旧快照被当成了实时值"

        made[0].gpu_sample = _gpu_sample(7.0)
        island._tick_sys()
        assert _gpu_seg(island).text == "7"
    finally:
        island.close()


def test_unfold_waits_for_a_fresh_sample_and_resubscribes(tmp_path, monkeypatch):
    """折叠→展开：重订阅 GPU，且等新序列的第一份样本（不端出折叠前的读数）。"""
    from tests.test_island_shrink_pipeline import FakeScreen, _narrow_to

    screen = FakeScreen(1400)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor = made[0]
        monitor.gpu_sample = _gpu_sample(42.0)
        island._tick_sys()
        assert _gpu_seg(island).text == "42"
        assert island._row_plan().folded == (), "先给一块够宽的屏"

        # 窄到把尾部（GPU）折进 +N
        _width, plan = _narrow_to(island, screen, lambda p: GPU in p.folded)
        assert GPU in plan.folded
        assert monitor.gpu_unsubscribed[-1] == GPU, "折掉的段必须退订（看不见就不采）"
        assert CPU in monitor.subscribed, "没被折的兄弟段不许被牵连"

        screen._rect = QRect(0, 0, 1400, 768)      # 搬回宽屏 → 恢复
        island.refresh_from_config()
        assert island._row_plan().folded == ()
        assert monitor.gpu_subscribed == {GPU}, "恢复必须重新订阅"
        restored = _gpu_seg(island)
        assert (restored.state, restored.text) == (isl.STATE_SAMPLING, isl.UNKNOWN_TEXT)

        monitor.gpu_sample = _gpu_sample(8.0)
        island._tick_sys()
        assert _gpu_seg(island).text == "8"
    finally:
        island.close()


def test_strip_dock_stops_the_gpu_slot_too(tmp_path, monkeypatch):
    """停靠细条：内容一个像素都不画 = GPU 也不采（与折叠同一套退订口径）。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor = made[0]
        island._mode = "docked"
        island._hover_peek = False
        island._sync_content_sampling()
        assert island._strip_hidden() is True, "用例要先真的进细条态"
        assert monitor.gpu_subscribed == set(), "细条里 GPU 也不许占着采样"
        assert monitor.closed is True, "0 订阅即收口（GPU 读数器不占着 PDH 句柄）"

        island._mode = "normal"
        island._sync_content_sampling()
        restored = island._sys_monitor
        assert restored is not None and restored is not monitor, "出细条 = 新底座"
        assert restored.gpu_subscribed == {GPU}, "出细条按同一条路径恢复订阅"
    finally:
        island.close()


# ---------------------------------------------------------------- 降级：不占位
def test_unsupported_gpu_takes_no_slot_and_the_card_keeps_the_reason(
        tmp_path, monkeypatch):
    """平台不支持：胶囊上连 ``--`` 都不占（段增删），卡片仍写清原因。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert _gpu_missing(island) is False, "首拍之前它是占位的（还没采到）"
        reason = "GPU 采集只在 Windows 上可用（PDH）"
        made[0].gpu_sample = _gpu_sample(None, state=isl.STATE_UNSUPPORTED, reason=reason)
        island._tick_sys()
        assert _gpu_missing(island) is True
        assert GPU not in island._row_plan().visible
        assert GPU not in island._row_plan().folded, "不占位的段连 +N 也折不到"
        assert island._unavailable_segments() == {GPU: reason}

        row = _row(island, GPU)
        assert row.state == isl.STATE_UNSUPPORTED
        assert row.value == isl.UNKNOWN_TEXT
        assert row.note == f"该指标不可用：{reason}"
        assert reason in row.hint
    finally:
        island.close()


def test_the_unsupported_transition_resizes_once_and_values_never_do(
        tmp_path, monkeypatch):
    """段增删可以改宽（unsupported↔支持各一次），读数变化一次都不许调 ``_update_size``。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        calls: list[int] = []
        original = island_mod.DynamicIsland._update_size

        def spy(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(island_mod.DynamicIsland, "_update_size", spy)
        made[0].gpu_sample = _gpu_sample(42.0)
        island._tick_sys()
        wide = island._capsule_width()
        assert calls == [], "普通读数（sampling→ok）绝不重算几何"

        made[0].gpu_sample = _gpu_sample(None, state=isl.STATE_UNSUPPORTED,
                                         reason="只在 Windows 上可用")
        island._tick_sys()
        assert len(calls) == 1, "段被移除 = 结构变化，几何重算一次"
        narrow = island._capsule_width()
        assert narrow < wide, "少了 GPU 段，胶囊该变窄"

        made[0].gpu_sample = _gpu_sample(None, state=isl.STATE_UNSUPPORTED,
                                         reason="只在 Windows 上可用")
        island._tick_sys()
        assert len(calls) == 1, "还是不可用：不再重复重算"

        made[0].gpu_sample = _gpu_sample(3.0)
        island._tick_sys()
        assert len(calls) == 2, "段回来 = 再重算一次"
        assert island._capsule_width() == wide
    finally:
        island.close()


# ---------------------------------------------------------------- 卡片详情
def test_card_uses_the_gpu_sample_time_not_the_cpu_one(tmp_path, monkeypatch):
    """卡片的时间取**这一段的**样本时刻：GPU 40 秒前的读数不许写 CPU 的"刚刚"。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sys_sample(age=0.2)
        made[0].gpu_sample = _gpu_sample(42.0, age=40.0)
        island._tick_sys()
        assert _row(island, CPU).updated == "刚刚"
        assert _row(island, GPU).updated == "40 秒前"
    finally:
        island.close()


def test_card_row_carries_the_measure_and_the_adapter(tmp_path, monkeypatch):
    """值 = 胶囊同一个串；量纲/来源人话；显卡名与引擎只在卡片（含 tooltip）。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].gpu_sample = _gpu_sample(42.0, engine="Video Codec 0")
        island._tick_sys()
        row = _row(island, GPU)
        assert row.name == "GPU"
        assert row.value == "42%", "卡片与胶囊是同一个显示值"
        assert row.unit_full == gm.UNIT_FULL, "量纲：最忙引擎，不是总算力"
        assert row.source == island_mod._CARD_SOURCE_SYS
        assert "Video Codec 0" in row.hint and _LONG_NAME in row.hint
        assert gm.SOURCE_TEXT in row.hint, "PDH 来源口径必须可读（tooltip 里）"
        assert row.value_sample == island_mod._GPU_VALUE_SAMPLE + "%"
    finally:
        island.close()


def test_card_tooltip_and_description_expose_the_full_context(tmp_path, monkeypatch):
    """详情列会被右省略，但完整上下文必须仍能从控件上读到（不许"省略即消失"）。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        _expanded(island)
        made[0].gpu_sample = _gpu_sample(42.0, age=0.5)
        island._tick_sys()
        island._sync_card_labels()
        app.processEvents()
        row = _row(island, GPU)
        index = [r.seg_id for r in island._card_metric_rows()].index(GPU)
        _name, value_label, detail_label = island._card_row_labels[index]
        for label in (value_label, detail_label):
            assert label.toolTip() == row.hint
            assert label.accessibleDescription() == row.hint
        assert gm.UNIT_FULL in detail_label.toolTip()
        assert _LONG_NAME in detail_label.toolTip()
    finally:
        island.close()


def test_error_sample_keeps_its_cause_in_the_card(tmp_path, monkeypatch):
    """error 态：胶囊安静地 ``--``，卡片写明原因（不伪造 0、不谎报可用）。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        reason = "本机没有 GPU Engine 计数器对象（没有可用的 GPU 计数来源）"
        made[0].gpu_sample = _gpu_sample(None, state=isl.STATE_ERROR, reason=reason)
        island._tick_sys()
        segment = _gpu_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_ERROR, isl.UNKNOWN_TEXT)
        row = _row(island, GPU)
        assert row.state == isl.STATE_ERROR
        assert reason in row.note
    finally:
        island.close()


def test_gpu_never_borrows_the_cpu_thresholds(tmp_path, monkeypatch):
    """最忙引擎 100% 不是故障：GPU 没有阈值，100 也不染色（不暗套 CPU 的线）。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert isl.alert_thresholds(GPU) is None
        made[0].gpu_sample = _gpu_sample(100.0)
        island._tick_sys()
        assert _gpu_seg(island).severity == ""
        assert _row(island, GPU).severity == ""
    finally:
        island.close()


# ---------------------------------------------------------------- 真实后端
def _drive_qt_loop(predicate, *, timeout: float = 25.0) -> bool:
    """在真实 Qt 事件循环里等 predicate 成立（宽预算轮询，不赌定时器节奏）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


@pytest.mark.skipif(sys.platform != "win32", reason="真实 PDH 采样只在 Windows 上存在")
def test_real_reader_reaches_the_island_through_the_qt_loop(tmp_path):
    """真实默认读数器（PDH）→ 真实 SysMonitor → island 的 Qt 循环：接线通了。

    只断言**链路**：GPU 那本账订阅为 1、设备槽为 0（GPU-only 不采 psutil）、快照
    经 Qt 定时器交到岛手上、段/卡片按四态如实呈现、窗口销毁后订阅归零且线程停。
    不断言读数值：机器上有没有活跃引擎不是契约（闲置就是 ``unknown``/``sampling``，
    那不是故障）。
    """
    cfg = _config(tmp_path, metrics=[GPU])
    island = DynamicIsland(cfg)          # 不替换工厂：走真实 GpuReader
    monitor = None
    try:
        island.show()
        assert _drive_qt_loop(lambda: island._gpu_sample is not None), \
            "真实采样线程没有把第一份快照交到岛手上"
        monitor = island._sys_monitor
        assert monitor is not None
        assert monitor.gpu_subscriber_count == 1
        assert monitor.subscriber_count == 0, "GPU-only 不许去读 psutil"
        sample = island._gpu_sample
        assert isinstance(sample, gm.GpuSample)
        assert sample.state in isl.STATES
        assert sample.sampled_at > 0.0
        if sample.state == isl.STATE_UNSUPPORTED:
            assert island._unavailable_segments().get(GPU), "不占位时必须给出原因"
            assert _gpu_missing(island) is True
        else:
            segment = _gpu_seg(island)
            assert segment.box_text == island_mod._GPU_VALUE_SAMPLE
            if sample.state == isl.STATE_OK:
                assert 0.0 <= float(sample.percent) <= 100.0
                assert segment.text == f"{float(sample.percent):.0f}"
            else:
                assert segment.text == isl.UNKNOWN_TEXT
        assert [r.seg_id for r in island._card_metric_rows()] == [GPU]
    finally:
        island.close()
    assert monitor is not None and monitor.gpu_subscriber_count == 0
    assert monitor.subscriber_count == 0
    deadline = time.monotonic() + 10.0
    while monitor.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert monitor.running is False, "收口后采样线程必须退出（不留下孤线程）"
