# -*- coding: utf-8 -*-
"""灵动岛设备槽段（``island_metrics`` 里的 cpu / mem 成员）的接线契约（P1 Slice D1）。

覆盖：默认关（零线程零定时器）→ 开启后 `SysMonitor.subscribe + start` → 读数进段
→ 隐藏即休眠、显示恢复 → 段撤掉即退订 + 收口 → 岛不可见/信息槽关闭都不采样。

采样器一律替身（``DynamicIsland._make_sys_monitor`` 替换点）：**测试不碰 psutil、
不起采样线程**；底座的活体行为由 ``tests/test_sys_monitor.py`` 覆盖，本文件只管
灵动岛侧的订阅口径。时序断言不赌真定时器节奏，全部同步直调。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import segments as isl
from pet import sys_monitor as sm
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])


class StubSysMonitor:
    """``SysMonitor`` 替身：只记录生命周期与订阅调用，样本由测试塞进去。"""

    def __init__(self, interval: float = sm.DEFAULT_INTERVAL_SECONDS):
        self.interval = sm.clamp_interval(interval)
        self.sample = None
        self.starts = 0
        self.stops = 0
        self.closed = False
        self.started = False
        self.subscribed: set[str] = set()
        self.unsubscribed: list[str] = []

    def subscribe(self, key: str) -> int:
        self.subscribed.add(str(key))
        return len(self.subscribed)

    def unsubscribe(self, key: str) -> int:
        self.unsubscribed.append(str(key))
        self.subscribed.discard(str(key))
        return len(self.subscribed)

    def start(self) -> bool:
        """起始幂等（与真 ``SysMonitor.start`` 同契约：已在跑就返回 False）。"""
        if self.started:
            return False
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
        self.interval = interval
        return self.interval

    def snapshot(self):
        return self.sample


def _config(tmp_path, *, metrics=(), **island_overrides) -> Config:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    }
    data.update(island_overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return cfg


def _island(tmp_path, monkeypatch, *, metrics=(), **island_overrides):
    """建岛：两条采样路径都换成替身（本文件只验设备槽，但不能让网络段起真线程）。"""
    made: list[StubSysMonitor] = []

    def factory(_self, interval):
        monitor = StubSysMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    return DynamicIsland(_config(tmp_path, metrics=metrics, **island_overrides)), made


def _sys_sample(cpu=24.0, mem=61.0, *, state=isl.STATE_OK, reason=""):
    return sm.SysSample(cpu_percent=cpu, mem_percent=mem, sampled_at=1.0,
                        state=state, error_reason=reason)


def _segment(island: DynamicIsland, seg_id: str):
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}")


# ---------------------------------------------------------------- 默认关
def test_device_slot_off_by_default_creates_nothing(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert made == [], "默认关不得创建采样器（更别提起线程/碰 psutil）"
        assert island._sys_timer.isActive() is False
        assert island._sys_sample is None
    finally:
        island.close()


def test_network_only_does_not_create_the_device_monitor(tmp_path, monkeypatch):
    """网络段开了也不该拉设备槽下水：两条采样路径各自独立。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK])
    try:
        island.show()
        app.processEvents()
        assert island._sys_monitor is None
        assert island._sys_timer.isActive() is False
    finally:
        island.close()


# ---------------------------------------------------------------- 订阅与启停
def test_cpu_segment_subscribes_and_starts_the_shared_monitor(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU])
    try:
        island.show()
        app.processEvents()
        assert len(made) == 1, "cpu 与 mem 共用同一条采样线程"
        assert made[0].subscribed == {CPU}
        assert made[0].started is True
        assert island._sys_timer.isActive() is True
        assert island._sys_timer.interval() == 1000, "1Hz 上限不可配"
    finally:
        island.close()


def test_both_device_segments_share_one_monitor(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        assert len(made) == 1, "两个段必须共用一份系统快照（否则多一份 syscall）"
        assert made[0].subscribed == {CPU, MEM}
        assert made[0].starts == 1
    finally:
        island.close()


def test_hidden_island_subscribes_but_does_not_start(tmp_path, monkeypatch):
    """岛不可见 = 不采样（可见性是订阅之外的第二道闸门）。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU])
    try:
        assert made[0].subscribed == {CPU}
        assert made[0].starts == 0, "构造完还没显示，不该起线程"
        assert island._sys_timer.isActive() is False

        island.show()
        app.processEvents()
        assert made[0].starts == 1
        assert island._sys_timer.isActive() is True
    finally:
        island.close()


def test_device_slot_sleeps_when_hidden_and_resumes_on_show(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        assert island._sys_timer.isActive() is True

        island.hide()
        app.processEvents()
        assert island._sys_timer.isActive() is False
        assert made[0].stops >= 1, "隐藏即休眠：采样线程必须停"

        island.show()
        app.processEvents()
        assert island._sys_timer.isActive() is True
        assert made[0].started is True, "显示后应恢复采样"
        assert made[0].subscribed == {CPU, MEM}, "恢复不该丢订阅"
    finally:
        island.close()


def test_resume_after_hiding_waits_for_a_fresh_sample(tmp_path, monkeypatch):
    """隐藏→显示：暂停前那一代的快照不许在恢复后冒充实时值（恢复屏障）。

    底座 ``stop()`` 不清最后一份快照，恢复时它可能还是暂停前那个对象——直接读它
    就等于把"上一代的读数"说成"这一代的实时值"。屏障的做法是记住**暂停时底座手里
    那一个对象**，只接纳它之后的新样本；订阅本身照旧保留（与 GPU 同一套口径）。
    """
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        stale = _sys_sample(cpu=24.0, mem=61.0)
        made[0].sample = stale
        island._tick_sys()
        assert _segment(island, CPU).text == "24"

        island.hide()
        island.show()
        app.processEvents()
        assert _segment(island, CPU).state == isl.STATE_SAMPLING, "恢复后先报采样中"
        island._tick_sys()               # 底座还在吐暂停前那一份
        assert made[0].snapshot() is stale
        assert _segment(island, CPU).text == "--", "旧快照被当成了实时值"

        made[0].sample = _sys_sample(cpu=7.0, mem=8.0)
        island._tick_sys()
        assert _segment(island, CPU).text == "7"
    finally:
        island.close()


def test_removing_a_segment_unsubscribes_it(tmp_path, monkeypatch):
    """段撤掉 = 退订那一段（0 订阅时底座零 syscall）。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        island.config.set("island_metrics", [MEM])
        island.refresh_from_config()
        app.processEvents()
        assert CPU in made[0].unsubscribed, "撤掉的段必须退订"
        assert made[0].subscribed == {MEM}
        assert made[0].closed is False, "还有一个段活着：采样器不许收口"
        assert _segment(island, MEM).label == "内存"
    finally:
        island.close()


def test_disabling_the_last_segment_closes_the_monitor(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU])
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sys_sample()
        island._tick_sys()
        assert island._sys_sample is not None

        island.config.set("island_metrics", [])
        island.refresh_from_config()
        assert made[0].closed is True
        assert made[0].subscribed == set(), "收口前必须退订（不然订阅计数留在替身里）"
        assert island._sys_monitor is None
        assert island._sys_sample is None
        assert island._sys_timer.isActive() is False
    finally:
        island.close()


def test_show_info_off_stops_device_sampling(tmp_path, monkeypatch):
    """三态回归：岛可见 + 显示信息槽 + 段在列表里，缺一都不采样。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU])
    island_cfg = dict(island.config.get("dynamic_island"))
    try:
        island.show()
        app.processEvents()
        assert made[0].started is True

        island.config.set("dynamic_island", {**island_cfg, "show_info": False})
        island.refresh_from_config()
        assert island._sys_timer.isActive() is False, "信息槽关了不该继续刷新"
        assert made[0].stops >= 1, "信息槽关了不该继续采样"
        assert island._sys_sample is None

        island.config.set("dynamic_island", {**island_cfg, "show_info": True})
        island.refresh_from_config()
        island.show()
        app.processEvents()
        assert island._sys_timer.isActive() is True
        assert made[-1].started is True
    finally:
        island.close()


def test_refresh_without_changes_is_idempotent(tmp_path, monkeypatch):
    """幂等：重复 refresh 不许重建采样器、不许重复起线程。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        for _ in range(3):
            island.refresh_from_config()
        assert len(made) == 1
        assert made[0].starts == 1
        assert made[0].closed is False
    finally:
        island.close()


# ---------------------------------------------------------------- 读数进段
def test_sample_flows_into_both_segments(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        assert _segment(island, CPU).text == "--"
        assert _segment(island, CPU).state == isl.STATE_SAMPLING

        made[0].sample = _sys_sample(cpu=37.5, mem=61.2)
        island._tick_sys()
        # Slice E4 双盒：数值与单位分居两盒，拼回去仍是格式化器的合并串
        assert _segment(island, CPU).text + _segment(island, CPU).unit == sm.format_cpu(37.5)
        assert _segment(island, MEM).text + _segment(island, MEM).unit == sm.format_mem(61.2)

        # 值没变（同一份快照对象）→ 什么都不做（静止时零重绘）
        island._sys_sample = made[0].sample
        before = island._sys_sample
        island._tick_sys()
        assert island._sys_sample is before
    finally:
        island.close()


def test_tick_without_monitor_is_a_noop(tmp_path, monkeypatch):
    island, _made = _island(tmp_path, monkeypatch)
    try:
        island._tick_sys()  # 没采样器：不抛、不改状态
        assert island._sys_sample is None
    finally:
        island.close()


def test_error_snapshot_shows_dashes_in_both_segments(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sys_sample(cpu=None, mem=None, state=isl.STATE_ERROR,
                                     reason="PsutilUnavailable: 没装")
        island._tick_sys()
        for metric_id in (CPU, MEM):
            assert _segment(island, metric_id).text == "--"
            assert _segment(island, metric_id).state == isl.STATE_ERROR
    finally:
        island.close()


def test_device_tick_does_not_resize_the_island(tmp_path, monkeypatch):
    """白名单：设备槽读数变化只换内容层位图，不动窗口几何。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        geometry = island.geometry()
        key = island._width_key()
        pixmap = island._content_cache()
        made[0].sample = _sys_sample(cpu=99.0, mem=4.0)
        island._tick_sys()
        assert island.geometry() == geometry
        assert island._width_key() == key
        assert island._content_cache() is not pixmap
    finally:
        island.close()
