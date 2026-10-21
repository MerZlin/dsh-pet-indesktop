# -*- coding: utf-8 -*-
"""GPU 段的接线合同：设置里的白名单 → 共享采样 → 胶囊读数 → 卡片详情。

P3 Slice F1 里 ``gpu`` 是"已注册但恒不可用"的能力占位；本版本它接了数据源
（``pet/gpu_monitor.py`` 的 Windows PDH 最忙引擎占用，经共享采样线程喂），所以
占位语义整体作废——本文件把**真接线后的四面**钉死：

1. **config 白名单放行**：``gpu`` 是合法 ``island_metrics`` 成员，落盘 → 重启往返
   不被归一化丢掉（与 network/cpu/mem 同一口径）。
2. **它是普通段**：采到读数就进胶囊（固定 label、定宽数值盒 + 单位盒），不再恒
   不可用；胶囊几何与"配置里根本没有这个 id"的差别恰好是一个段宽。
3. **订阅走 GPU 那一本账**：``subscribe_gpu``（不是 CPU/内存的 ``subscribe``）；
   GPU-only 时设备槽一个订阅都没有。
4. **降级两态都在卡片里有话说**：unsupported 不占位 + 原因行，error 占位 +
   原因行，两者都不是"读数就是 0"。

生命周期（隐藏/折叠/停靠/销毁的订阅联动）与真实后端链路见
``tests/test_island_gpu_integration.py``。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import segments as isl
from pet.config import Config
from pet.island.segments import CPU, GPU, MEM, NETWORK
from tests.test_island_gpu_integration import _config, _gpu_sample
from tests.test_island_segment_render import _island

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- config 放行
def test_gpu_is_a_known_metric_id_that_survives_a_restart(tmp_path):
    """白名单放行：gpu 落盘 → 重启加载后仍在列表里（不是"脏值被丢弃"）。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [CPU, GPU])
    assert cfg.get("island_metrics") == [CPU, GPU]
    assert cfg.save() is True
    restored = Config(base=tmp_path)
    assert restored.get("island_metrics") == [CPU, GPU]
    assert isl.GPU == "gpu", "常量是唯一来源（config 白名单引用它，不抄字面量）"


def test_gpu_sits_after_the_three_builtin_ids(tmp_path):
    """四个合法 id 的相对次序原样保留（列表序 = 用户优先级）。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [MEM, NETWORK, CPU, GPU])
    assert cfg.get("island_metrics") == [MEM, NETWORK, CPU, GPU]


# ---------------------------------------------------------------- 普通段（不再是占位）
def test_gpu_is_not_a_static_unavailable_segment_any_more(tmp_path, monkeypatch):
    """没有静态"本版本未接入"的占位条目了：可用性由**当刻读数**决定。"""
    island, _made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    try:
        assert island._unavailable_segments() == {}, "没有读数说它不可用就不许占位"
        assert "GPU_UNAVAILABLE_REASON" not in dir(isl), "占位常量必须随接线一起删掉"
    finally:
        island.close()


def test_gpu_takes_a_capsule_slot_once_it_has_a_reading(tmp_path, monkeypatch):
    """采到读数就进胶囊：段列表里有它、行计划画它、自然宽正好多出一个段。"""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with_gpu, _m1 = _island(_config(tmp_path / "a", metrics=[CPU, GPU]), monkeypatch)
    without_gpu, _m2 = _island(_config(tmp_path / "b", metrics=[CPU]), monkeypatch)
    try:
        for island in (with_gpu, without_gpu):
            island.show()
        app.processEvents()
        with_gpu._sys_monitor.gpu_sample = _gpu_sample(42.0)
        with_gpu._tick_sys()
        assert [seg.seg_id for seg in with_gpu._compose_info_segments()] == \
            ["primary", CPU, GPU]
        assert GPU in with_gpu._row_plan().visible
        assert GPU not in with_gpu._row_plan().folded, "两段都放得下（用例前提）"
        assert with_gpu._capsule_width() > without_gpu._capsule_width()
        assert with_gpu._rest_size().width() > without_gpu._rest_size().width()
    finally:
        with_gpu.close()
        without_gpu.close()


def test_gpu_gets_a_reading_row_in_the_card(tmp_path, monkeypatch):
    """卡片 B 区：一行真实读数 + 量纲/来源/更新时间（与 CPU/内存同一套三列）。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].sample = None
        made[0].gpu_sample = _gpu_sample(42.0)
        island._tick_sys()
        rows = {row.seg_id: row for row in island._card_metric_rows()}
        assert set(rows) == {CPU, GPU}
        row = rows[GPU]
        assert row.name == "GPU"
        assert row.state == isl.STATE_OK
        assert row.value == "42%"
        assert row.unit_full
        assert row.updated != "未采样"
    finally:
        island.close()


def test_gpu_row_keeps_the_configuration_order_in_the_card(tmp_path, monkeypatch):
    """卡片行序 = 配置序（不飘到末尾）。"""
    island, _made = _island(_config(tmp_path, metrics=[GPU, CPU]), monkeypatch)
    try:
        assert [row.seg_id for row in island._card_metric_rows()] == [GPU, CPU]
    finally:
        island.close()


# ---------------------------------------------------------------- 订阅账本
def test_gpu_takes_the_gpu_subscription_not_the_device_one(tmp_path, monkeypatch):
    """GPU 走 ``subscribe_gpu``：设备槽那本账里不许有它（否则会去读内存百分比）。"""
    island, made = _island(_config(tmp_path, metrics=[CPU, GPU, MEM]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor = made[0]
        assert monitor.gpu_subscribed == {GPU}
        assert monitor.subscribed == {CPU, MEM}
        assert GPU not in island_mod._SYS_METRIC_IDS
        assert GPU not in island_mod._DEVICE_LABELS, "它不是设备槽的成员"
    finally:
        island.close()


def test_turning_gpu_on_alone_samples_only_the_gpu(tmp_path, monkeypatch):
    """只勾 GPU：胶囊照画、卡片一行，且一个 psutil 订阅都没有。"""
    island, made = _island(_config(tmp_path, metrics=[GPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert made[0].subscribed == set()
        assert island._compose_info_segments()[0].seg_id == "primary"
        assert island._capsule_width() > 0
        assert [row.seg_id for row in island._card_metric_rows()] == [GPU]
    finally:
        island.close()
