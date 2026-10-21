# -*- coding: utf-8 -*-
"""灵动岛网络段（``island_metrics`` 里的 network 成员）的接线契约。

覆盖：默认关（零线程零定时器）→ 开启后由后台采样器提供文本 → 隐藏即休眠、
显示恢复 → 关闭/改间隔即时生效 → 文案定宽。

「开启」的配置口径是 ``island_metrics`` 含 ``network``（P1 Slice C 起列表是唯一
真值；旧键 ``island_network_enabled`` 只在归一化阶段单向迁移一次，本文件不再
造它——迁移本身由 ``tests/test_island_metrics_config.py`` 覆盖）。

**段语义（P1 Slice D1 起）**：网络是普通指标段，文本进 ``_compose_info_segments()``
的 network 段（断言走本文件的 ``_net_seg`` 助手）；信息槽内容永由 ``info_mode``
决定，``_info_text()`` 只返回主信息段的文案。段管线的段序/文案/四态由
``tests/test_island_segment_render.py`` 覆盖，本文件只管采样生命周期接线。

采样器在测试里一律替换成替身（``DynamicIsland._make_net_monitor`` 替换点）：
**测试不发真实探测包、不碰 psutil**，活体行为由实机验证覆盖。时序断言不赌
真定时器节奏，全部同步直调。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet import net_monitor
from pet.island import segments as island_segments
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.segments import NETWORK

app = QApplication.instance() or QApplication([])


class StubMonitor:
    """采样器替身：只记录生命周期调用，样本由测试塞进去。"""

    def __init__(self, interval: float = net_monitor.DEFAULT_INTERVAL_SECONDS):
        self.interval = net_monitor.clamp_interval(interval)
        self.sample = None
        self.starts = 0
        self.stops = 0
        self.closed = False
        self.started = False

    # DynamicIsland 用到的面
    def start(self) -> bool:
        self.starts += 1
        self.started = True
        return True

    def stop(self, **_kwargs) -> None:
        self.stops += 1
        self.started = False

    def close(self) -> None:
        self.closed = True
        self.started = False

    def set_interval(self, interval: float) -> float:
        self.interval = net_monitor.clamp_interval(interval)
        return self.interval

    def snapshot(self):
        return self.sample


def _config(tmp_path, **network) -> Config:
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    })
    for key, value in network.items():
        cfg.set(key, value)
    return cfg


def _island(tmp_path, monkeypatch, **network) -> tuple[DynamicIsland, list[StubMonitor]]:
    made: list[StubMonitor] = []

    def factory(_self, interval):
        monitor = StubMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    return DynamicIsland(_config(tmp_path, **network)), made


def _net_seg(island: DynamicIsland):
    """当前网络段（P1 D1 起网络是普通指标段，不再是"接管信息槽"的文案）。"""
    for segment in island._compose_info_segments():
        if segment.seg_id == NETWORK:
            return segment
    raise AssertionError("段列表里没有 network 段（网络段开关失效了？）")


def _sample(down=2048.0, up=512.0, latency=23.0, lost=False):
    return net_monitor.NetSample(
        down_bps=down, up_bps=up, latency_ms=latency, latency_lost=lost, sampled_at=1.0,
    )


# ---------------------------------------------------------------- 默认关
def test_network_slot_off_by_default_uses_info_mode_and_starts_nothing(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert made == [], "默认关不得创建采样器（更别提起线程/发包）"
        assert island._net_timer.isActive() is False
        # 默认关 → 必须走 info_mode 分支（本用例配置 info_mode="time"）：文本是
        # HH:mm 形态，而不是网络槽的 "↓…↑…"。给 ±1 分钟容差，避免恰好跨分钟时假红。
        now = QTime.currentTime()
        expected = {now.addSecs(offset).toString("HH:mm") for offset in (-60, 0, 60)}
        text = island._info_text()
        assert text in expected, f"默认关时信息槽必须回到 info_mode 内容，实际 {text!r}"
        assert "↓" not in text
    finally:
        island.close()


# ---------------------------------------------------------------- 开启
def test_network_slot_on_starts_worker_and_shows_sample(tmp_path, monkeypatch):
    island, made = _island(
        tmp_path, monkeypatch, island_metrics=[NETWORK],
        network_probe_interval_seconds=2.0,
    )
    try:
        island.show()
        app.processEvents()
        assert len(made) == 1
        assert made[0].started is True
        assert island._net_timer.isActive() is True
        assert island._net_timer.interval() == 2000

        # 采样器还没出结果：段显示四态占位 "--"，不空着也不编造 0B
        # （信息槽内容仍由 info_mode 决定——网络段不再是"接管信息槽"的特殊形态）
        assert _net_seg(island).text == "--"
        assert _net_seg(island).state == island_segments.STATE_SAMPLING

        made[0].sample = _sample()
        island._tick_network()
        text = _net_seg(island).text
        assert _net_seg(island).state == island_segments.STATE_OK
        assert text.startswith("↓")
        assert "↓" in text and "↑" in text and "23ms" in text
        assert "2K" in text and "512B" in text
    finally:
        island.close()


def test_legacy_network_key_still_turns_the_segment_on(tmp_path, monkeypatch):
    """老用户升级：配置里只有旧键 True（P1 前的 config.json）→ 迁移后网络段照常跑。

    迁移是归一化阶段做的，所以本用例与"从磁盘加载带旧键的配置"走同一条路径；
    这条护栏防的是"迁移被谁顺手删了"——那会让老用户的网络段静默消失。
    """
    island, made = _island(tmp_path, monkeypatch, island_network_enabled=True)
    try:
        assert island.config.get("island_metrics") == [NETWORK]
        assert island.config.get("island_network_enabled") is False
        island.show()
        app.processEvents()
        assert len(made) == 1 and made[0].started is True
        assert island._net_timer.isActive() is True
    finally:
        island.close()


def test_network_slot_tick_keeps_text_width_stable(tmp_path, monkeypatch):
    """文案定宽：速率/延迟变化不得改文本宽度（否则胶囊每秒左右抖）。"""
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        widths = set()
        for sample in (
            _sample(0.0, 0.0, None, lost=True),
            _sample(12 * 1024 * 1024, 300 * 1024, 8.0),
            _sample(987654321.0, 1024.0, 123.4),
        ):
            made[0].sample = sample
            island._tick_network()
            widths.add(len(_net_seg(island).text))
        assert len(widths) == 1, widths
    finally:
        island.close()


def test_network_slot_lost_probe_shows_dashes(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sample(latency=None, lost=True)
        island._tick_network()
        assert _net_seg(island).text.endswith("--")
    finally:
        island.close()


def test_network_slot_ignores_tick_before_first_sample(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        island._tick_network()  # 快照还是 None：不抛、不改状态
        assert island._net_sample is None
    finally:
        island.close()


# ---------------------------------------------------------------- 休眠/恢复
def test_network_slot_sleeps_when_hidden_and_resumes_on_show(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        assert island._net_timer.isActive() is True

        island.hide()
        app.processEvents()
        assert island._net_timer.isActive() is False
        assert made[0].stops >= 1, "隐藏即休眠：采样线程必须停"

        island.show()
        app.processEvents()
        assert island._net_timer.isActive() is True
        assert made[0].started is True, "显示后应恢复采样"
    finally:
        island.close()


def test_network_slot_honours_show_info_switch(tmp_path, monkeypatch):
    """三态回归：岛可见 + 显示信息槽 + 网络槽开关，缺一都不采样。

    关掉「显示信息槽」时网络文案根本画不出来（_visible_parts 的第 3 位），
    继续跑采样线程只是白烧计数器读取与探测包。
    """
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    island_cfg = dict(island.config.get("dynamic_island"))
    try:
        island.show()
        app.processEvents()
        assert made[0].started is True
        assert island._net_timer.isActive() is True

        # 关掉「显示信息槽」：采样与定时器一起停
        island.config.set("dynamic_island", {**island_cfg, "show_info": False})
        island.refresh_from_config()
        assert island._net_timer.isActive() is False, "信息槽关了不该继续刷新"
        assert made[0].stops >= 1, "信息槽关了不该继续采样"
        assert island._net_sample is None

        # 再打开：恢复采样（信息槽内容回到 info_mode 之外由开关决定）
        island.config.set("dynamic_island", {**island_cfg, "show_info": True})
        island.refresh_from_config()
        island.show()
        app.processEvents()
        assert island._net_timer.isActive() is True
        assert made[-1].started is True
    finally:
        island.close()


def test_network_slot_honours_island_visibility(tmp_path, monkeypatch):
    """岛不可见（隐藏）时同样不采样：可见性是第三个条件。"""
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        assert island._net_timer.isActive() is True
        island.hide()
        app.processEvents()
        assert island._net_timer.isActive() is False
        assert made[0].stops >= 1
    finally:
        island.close()


# ---------------------------------------------------------------- 配置变更
def test_network_slot_disabled_by_config_change_closes_worker(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, island_metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        island._net_sample = _sample()
        island.config.set("island_metrics", [])
        island.refresh_from_config()
        assert made[0].closed is True
        assert island._net_monitor is None
        assert island._net_sample is None
        assert island._net_timer.isActive() is False
        # 关掉后段列表里不该再有 network 段（信息槽回到 info_mode 的内容）
        assert all(segment.seg_id != NETWORK
                   for segment in island._compose_info_segments())
    finally:
        island.close()


def test_network_slot_interval_change_applies_without_rebuild(tmp_path, monkeypatch):
    island, made = _island(
        tmp_path, monkeypatch, island_metrics=[NETWORK],
        network_probe_interval_seconds=1.0,
    )
    try:
        island.show()
        app.processEvents()
        island.config.set("network_probe_interval_seconds", 5.0)
        island.refresh_from_config()
        assert len(made) == 1, "改间隔不该重建采样器"
        assert made[0].interval == 5.0
        assert made[0].closed is False
        assert island._net_timer.interval() == 5000
    finally:
        island.close()


def test_network_slot_dirty_interval_is_clamped(tmp_path, monkeypatch):
    island, made = _island(
        tmp_path, monkeypatch, island_metrics=[NETWORK],
        network_probe_interval_seconds=0.01,
    )
    try:
        island.show()
        app.processEvents()
        assert made[0].interval == net_monitor.MIN_INTERVAL_SECONDS
        assert island._net_timer.interval() == 500
    finally:
        island.close()
