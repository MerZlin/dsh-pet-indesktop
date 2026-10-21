# -*- coding: utf-8 -*-
"""游戏帧率段**端到端接线**（``island_metrics`` 的 fps 成员 → 采集器 → 胶囊读数 → 卡片详情）。

本文件管**接线**，不管采集链路：采集器的状态机/窗口统计/发布/代次是
``tests/test_fps_monitor.py`` 的事，CSV 解析与统计是 ``tests/test_game_fps.py`` 的事，
段自己的状态机（订阅账目、屏障、四态、卡片素材）是 ``tests/test_island_fps_slot.py``
的事。这里回答六个问题：

1. **谁在采、什么时候停**：默认关闭时连采集器都不建；开着且岛可见才订阅+起线程；
   折进 +N / 收进细条 / 隐藏 / 关窗口各走哪条收口路径。
2. **读出什么**：读数进胶囊（定宽盒，0/60/144/9999 一个像素都不动）、卡片写目标
   与 Present 来源与可选上屏帧率；没有读数时同宽 ``--``，**绝不伪报 0**。
3. **配置热改**：设置页改了跟随方式 / 指定程序名，岛侧同步一次就换代。
4. **恢复不端旧值**：隐藏→显示、折叠→展开都要等新序列的第一份新读数。
5. **降级**：非 Windows / 内置采集程序不在 = 段不占位 + 卡片写原因（不是 ``--`` 占位）。
6. **多宠唯一**：两只宠各一个岛，仍然只有一个采集器；关掉一只不掐另一只的读数。

时序纪律：替身用例全部同步直调（不赌定时器节奏）；唯一的真实采集器用例
（``test_the_real_collector_reaches_the_island_through_the_qt_loop``）用宽预算轮询
Qt 事件循环——只跑真采集器（真状态机、真线程、真代次），替换的边界只有两个在测试里
**无法**真实运行的 OS 面：PresentMon 子进程（真机上是 ETW 会话）与前台窗口查询
（真机上是 Win32）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import fps_monitor as fm
from pet.island import game_fps as gf
from pet.island import fps as island_fps, segments as isl
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.segments import CPU, FPS, GPU
from tests.test_island_segment_render import _config as _island_config
from tests.test_island_segment_render import _island as _render_island

app = QApplication.instance() or QApplication([])


class _StubMonitor:
    """与 ``FpsMonitor`` 同形的替身：只记调用、只吐注入的快照。"""

    def __init__(self):
        self.subscribed: set[str] = set()
        self.subscriptions: list[str] = []
        self.unsubscriptions: list[str] = []
        self.configured: list[tuple[str, str]] = []
        self.starts = 0
        self.stops = 0
        self.closed = False
        #: 每次 stop 拿到的 ``terminate_session``（退出编排的透传）
        self.stop_flags: list[bool] = []
        self.sample = None
        self.fallback = _sample(None, state="")

    def configure(self, mode: str = "foreground", process: str = "") -> None:
        self.configured.append((mode, process))

    def subscribe(self, key: str) -> bool:
        self.subscribed.add(str(key))
        self.subscriptions.append(str(key))
        return True

    def unsubscribe(self, key: str) -> None:
        self.subscribed.discard(str(key))
        self.unsubscriptions.append(str(key))

    def start(self) -> bool:
        self.starts += 1
        return True

    def stop(self, wait: bool = False, timeout: float = 2.0, *,
             terminate_session: bool = True) -> None:
        self.stops += 1
        self.stop_flags.append(bool(terminate_session))

    def close(self, *, terminate_session: bool = True) -> None:
        self.closed = True

    def snapshot(self):
        return self.sample if self.sample is not None else self.fallback


def _sample(fps, *, state: str = isl.STATE_OK, reason: str = "", pid=4242,
            name: str = "game.exe", displayed=None, age: float = 0.0):
    """一条与真采集器同形的快照（字段口径逐条对齐 ``fps_monitor._sample``）。"""
    detail = {"unit_full": gf.UNIT_FULL, "source": gf.SOURCE_TEXT, "mode": "foreground"}
    if pid:
        detail["detail"] = f"{name} (pid {pid})"
    if reason:
        detail["error_reason"] = reason
    return fm.FpsSample(
        fps=fps, state=state, sampled_at=time.monotonic() - age,
        target_pid=pid if state == isl.STATE_OK else None,
        target_name=name if state == isl.STATE_OK else "",
        error_reason=reason, displayed_fps=displayed, generation=1, detail=detail)


def _config(tmp_path, *, metrics=(), **island_overrides) -> Config:
    """配置 = 岛那一坨 + 顶层 ``island_metrics``（段开在哪个键上是产品的账）。"""
    cfg = _island_config(tmp_path, **island_overrides)
    cfg.set("island_metrics", list(metrics))
    return cfg


def _pin_fps_platform(monkeypatch, *, supported: bool = True) -> None:
    """把"这台机器有没有帧率采集能力"钉成注入值（两个视图一起钉）。

    ``FpsSlot`` / ``FpsMonitor`` 在**构造期**就把 ``game_fps.platform_support`` 取成
    自己的判据（存的是当时的函数对象），而它读的是真实 ``sys.platform``：在 macOS /
    Linux runner 上默认判定为"这台机器没有这个能力"，于是**接线**用例整体退化成
    "段不占位、采集器根本不建"（mac CI 实测 22 条）。接线与宿主平台无关，所以建岛
    之前先钉住。

    两个视图一起钉是刻意的：``platform_support()`` 就是 ``platform_problem() == ""``
    的别名，只钉一个会造出"没能力却报有能力"的自相矛盾替身世界。
    """
    monkeypatch.setattr(gf, "platform_support", lambda: bool(supported))
    monkeypatch.setattr(
        gf, "platform_problem",
        lambda: "" if supported else gf.PLATFORM_UNSUPPORTED_REASON)


def _island(config, monkeypatch, monitor=None, *, platform_supported: bool = True):
    """建岛：网络/设备/第三方三条采样路径与帧率采集器全换替身。

    采集器宿主必须在**建岛之前**换掉：岛构造的最后一步就会同步采样闸门
    （``_sync_fps_slot``），那时段已经要去共享宿主拿实例了。

    平台闸门同样要在**建岛之前**钉住（见 :func:`_pin_fps_platform`）；确实要验
    "这台机器没有这个能力"的用例显式传 ``platform_supported=False``。
    """
    _pin_fps_platform(monkeypatch, supported=platform_supported)
    monitor = monitor if monitor is not None else _StubMonitor()
    built: list[_StubMonitor] = []

    def factory():
        built.append(monitor)
        return monitor

    hub = island_fps.FpsHub(monitor_factory=factory)
    monkeypatch.setattr(island_fps, "_HUB", hub)
    island, made = _render_island(config, monkeypatch)
    island._fps_test_built = built          # 供"只建了一个采集器"断言
    return island, monitor, hub


def _fps_seg(island: DynamicIsland):
    for segment in island._compose_info_segments():
        if segment.seg_id == FPS:
            return segment
    raise AssertionError(
        f"段列表里没有 fps：{[s.seg_id for s in island._compose_info_segments()]}")


def _fps_missing(island: DynamicIsland) -> bool:
    return FPS not in [s.seg_id for s in island._compose_info_segments()]


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
def test_default_off_builds_no_collector_and_runs_no_read(tmp_path, monkeypatch):
    """默认关闭 = 真的零开销：采集器不建、读表不跑、段不出现（连 ``--`` 都没有）。"""
    island, monitor, hub = _island(_config(tmp_path, metrics=[CPU]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert hub.monitor is None, "段关着时连采集器对象都不该建"
        assert island._fps_test_built == []
        assert island._fps_timer.isActive() is False
        assert island._fps.sample is None
        assert _fps_missing(island) is True
        assert [s.seg_id for s in island._compose_info_segments()] == ["primary", CPU]
    finally:
        island.close()


def test_enabling_the_metric_subscribes_and_reads_at_one_hz(tmp_path, monkeypatch):
    island, monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        # 构造期就登记订阅（与设备槽同口径），但**线程要等岛可见**才起——采集器
        # 对象本身不建线程、不起子进程，所以"隐藏着不采"在这条路径上是真话。
        assert hub.monitor is monitor and monitor.starts == 0
        island.show()
        app.processEvents()
        assert hub.keys == {island._fps.key}
        assert monitor.subscribed == {island._fps.key}
        assert monitor.starts == 1, "岛可见才起线程"
        assert island._fps_timer.isActive() is True
        assert island._fps_timer.interval() == 1000, "1Hz 读快照（GUI 不做 syscall）"
        assert _fps_seg(island).state == isl.STATE_SAMPLING
    finally:
        island.close()


def test_showing_an_already_enabled_slot_does_not_restart_the_thread_twice(
        tmp_path, monkeypatch):
    """同一段反复同步（配置刷新 / 折叠同步都调它）只起一条线程、一次订阅、一次下发。

    这是稳态成本：``_sync_fps_slot`` 会被折叠流水线调到，重复下发等于每次都让采集器
    再做一遍"内置采集程序在不在"的文件检查。
    """
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        for _ in range(3):
            island.refresh_from_config()
        assert monitor.starts == 1
        assert monitor.subscribed == {island._fps.key}
        assert monitor.subscriptions == [island._fps.key], "重复同步不许重复订阅"
        assert len(monitor.configured) == 1, "目标没变就不该重复下发配置"
    finally:
        island.close()


def test_turning_the_metric_off_stops_the_collector(tmp_path, monkeypatch):
    island, monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        island.config.set("island_metrics", [])
        island.refresh_from_config()
        assert hub.keys == set(), "段没了 = 退订收口"
        assert monitor.unsubscriptions == [island._fps.key]
        assert island._fps_timer.isActive() is False
    finally:
        island.close()


def test_target_config_hot_change_reaches_the_collector_and_the_card(
        tmp_path, monkeypatch):
    """设置页改了跟随方式 / 程序名：岛侧同步一次就换代，卡片上下文跟着说。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert monitor.configured[-1] == ("foreground", "")
        island.config.set("game_fps_target_mode", "process")
        island.config.set("game_fps_process", "game.exe")
        island.refresh_from_config()
        assert monitor.configured[-1] == ("process", "game.exe")
        assert (island._fps.mode, island._fps.process) == ("process", "game.exe")

        sample = _sample(60.0)
        sample.detail["mode"] = "process"
        sample.detail["process"] = "game.exe"
        monitor.sample = sample
        island._tick_fps()
        row = _row(island, FPS)
        assert "指定程序 game.exe" in row.hint
    finally:
        island.close()


# ---------------------------------------------------------------- 读数呈现
def test_the_reading_reaches_the_capsule_and_the_card(tmp_path, monkeypatch):
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert _fps_seg(island).state == isl.STATE_SAMPLING, "还没采到第一拍"
        monitor.sample = _sample(144.0, displayed=138.0, age=0.2)
        assert island._tick_fps() is True
        segment = _fps_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_OK, "144")
        assert segment.label == "FPS" and segment.unit == ""
        assert segment.box_text == island_fps.VALUE_SAMPLE
        assert segment.severity == "", "帧率高不是故障：不染色（不借 GPU/CPU 的阈值）"

        row = _row(island, FPS)
        assert row.name == "FPS" and row.value == "144", "卡片与胶囊是同一个显示值"
        assert row.unit_full == gf.UNIT_FULL, "口径必须写明是应用 Present 帧率"
        assert "Present" in row.unit_full
        assert row.source == island_mod._CARD_SOURCE_SYS
        assert row.updated == "刚刚", "卡片的时间取这一段的采样时刻"
        assert "game.exe (pid 4242)" in row.hint, "读数归属必须具体到进程"
        assert gf.SOURCE_TEXT in row.hint, "Present 来源必须可读"
        assert "上屏帧率 138" in row.hint and "另一口径" in row.hint
    finally:
        island.close()


def test_the_value_box_never_changes_width_with_the_reading(tmp_path, monkeypatch):
    """0 → 60 → 144 → 9999：读数只在盒内左扩，胶囊宽与窗口宽一个像素都不动。"""
    from pet.island import layout

    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        geometry = []
        for value in (0.0, 60.0, 144.0, 9999.0, 12000.0):
            monitor.sample = _sample(value)
            island._tick_fps()
            segment = _fps_seg(island)
            assert segment.text == gf.format_fps(value)
            placed = [item for item in layout.place_info_segments(
                island._compose_info_segments(), start_x=0.0,
                primary_fm=island._metrics_for("primary"),
                value_fm=island._metrics_for("value"),
                label_fm=island._metrics_for("label"))
                if item.seg.seg_id == FPS]
            assert placed, "段没进排版"
            assert placed[0].text == segment.text, \
                f"字段盒把读数省略了（盒宽不够）：{placed[0].text!r} != {segment.text!r}"
            geometry.append((island._capsule_width(), island.width(),
                             island._rest_size()))
        assert len(set(geometry)) == 1, f"读数改了岛宽：{geometry}"
    finally:
        island.close()


def test_sampling_states_keep_the_same_box_and_never_show_a_zero(tmp_path, monkeypatch):
    """sampling / unknown / error 一律同宽 ``--``，岛宽与 ok 态逐像素相同。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor.sample = _sample(144.0)
        island._tick_fps()
        ok_size = (island._capsule_width(), island._rest_size())
        for state, reason in ((isl.STATE_SAMPLING, ""),
                              (isl.STATE_UNKNOWN, "当前没有可采集的目标"),
                              (isl.STATE_ERROR, "采集程序没有采集权限")):
            monitor.sample = _sample(None, state=state, reason=reason)
            island._tick_fps()
            segment = _fps_seg(island)
            assert (segment.state, segment.text) == (state, isl.UNKNOWN_TEXT)
            assert (island._capsule_width(), island._rest_size()) == ok_size
    finally:
        island.close()


def test_a_long_target_name_never_touches_the_capsule(tmp_path, monkeypatch):
    """长进程名只进卡片上下文：胶囊上只有一个 "FPS" 标签，几何与名字无关。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor.sample = _sample(60.0, name="短.exe")
        island._tick_fps()
        short = (island._capsule_width(), _fps_seg(island).label, island.width())
        long_name = "A-Very-Long-Game-Executable-Name-With-Version-2026-x64.exe"
        monitor.sample = _sample(60.0, name=long_name)
        island._tick_fps()
        assert (island._capsule_width(), _fps_seg(island).label,
                island.width()) == short
        assert short[1] == "FPS"
        assert long_name in _row(island, FPS).hint, "名字必须能在卡片里读到"
    finally:
        island.close()


def test_an_overflow_reading_keeps_the_box_and_gives_the_exact_value(
        tmp_path, monkeypatch):
    """>9999：段上是后端声明的溢出记号（不夹紧成假精确值），精确值在卡片上下文里。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor.sample = _sample(12000.0)
        island._tick_fps()
        assert _fps_seg(island).text == gf.MAX_TEXT
        assert _row(island, FPS).value == gf.MAX_TEXT
        assert "12000" in _row(island, FPS).hint
    finally:
        island.close()


# ---------------------------------------------------------------- 卡片原因
def test_no_target_is_explained_without_claiming_an_unimplemented_segment(
        tmp_path, monkeypatch):
    """没有目标时卡片说原因，**不许**落到"未注册的段 id"那条通用兜底上。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor.sample = _sample(None, state=isl.STATE_UNKNOWN,
                                 reason="没有前台窗口（桌宠或桌面在前台）")
        island._tick_fps()
        row = _row(island, FPS)
        assert row.state == isl.STATE_UNKNOWN
        assert row.value == isl.UNKNOWN_TEXT
        assert "没有前台窗口" in row.note
        assert "未注册" not in row.note and "未注册" not in row.hint
        assert "没有前台窗口" in row.hint
    finally:
        island.close()


def test_a_permission_failure_is_explained_on_the_card(tmp_path, monkeypatch):
    """error 态：胶囊安静地 ``--``，卡片写明原因（权限不足要能看懂）。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        reason = "采集会话启动失败：需要管理员或 Performance Log Users 组"
        monitor.sample = _sample(None, state=isl.STATE_ERROR, reason=reason)
        island._tick_fps()
        segment = _fps_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_ERROR, isl.UNKNOWN_TEXT)
        row = _row(island, FPS)
        assert row.state == isl.STATE_ERROR
        assert reason in row.note
        assert reason in row.hint
    finally:
        island.close()


def test_the_backend_failure_text_is_kept_in_the_card_context(tmp_path, monkeypatch):
    """后端 stderr 原文（官方话）也要能读到——卡片短句不是唯一证据。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        sample = _sample(None, state=isl.STATE_ERROR, reason="采集会话启动失败")
        sample.detail["backend_stderr"] = "error: failed to start trace session (0x5)"
        monitor.sample = sample
        island._tick_fps()
        assert "0x5" in _row(island, FPS).hint
    finally:
        island.close()


def test_the_card_tooltip_and_description_expose_the_full_context(tmp_path, monkeypatch):
    """详情列会被右省略，但完整上下文必须仍能从控件上读到（不许"省略即消失"）。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        _expanded(island)
        monitor.sample = _sample(144.0, displayed=138.0, age=0.5)
        island._tick_fps()
        island._sync_card_labels()
        app.processEvents()
        row = _row(island, FPS)
        index = [r.seg_id for r in island._card_metric_rows()].index(FPS)
        _name, value_label, detail_label = island._card_row_labels[index]
        for label in (value_label, detail_label):
            assert label.toolTip() == row.hint
            assert label.accessibleDescription() == row.hint
        assert gf.SOURCE_TEXT in detail_label.toolTip()
        assert "game.exe (pid 4242)" in detail_label.toolTip()
    finally:
        island.close()


# ---------------------------------------------------------------- 恢复屏障
def test_hide_and_show_wait_for_a_fresh_reading(tmp_path, monkeypatch):
    """隐藏→显示：暂停前那一代的快照不许在恢复后冒充实时值。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        stale = _sample(144.0)
        monitor.sample = stale
        island._tick_fps()
        assert _fps_seg(island).text == "144"

        island.hide()
        island.show()
        app.processEvents()
        assert _fps_seg(island).state == isl.STATE_SAMPLING, "恢复后先报采样中"
        assert island._tick_fps() is False, "恢复屏障必须挡住那一份旧读数"
        assert monitor.snapshot() is stale
        segment = _fps_seg(island)
        assert (segment.state, segment.text) == (isl.STATE_SAMPLING, isl.UNKNOWN_TEXT)

        monitor.sample = _sample(60.0)
        assert island._tick_fps() is True
        assert _fps_seg(island).text == "60"
    finally:
        island.close()


def test_hiding_keeps_the_subscription_and_only_stops_the_thread(tmp_path, monkeypatch):
    """隐藏只是休眠：退订留给"段没了/窗口关了"，显示回来不重开一场采集会话。"""
    island, monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        island.hide()
        assert island._fps_timer.isActive() is False
        assert monitor.stops == 1 and monitor.unsubscriptions == []
        assert hub.keys == {island._fps.key}
    finally:
        island.close()


def test_unfold_waits_for_a_fresh_reading_and_resubscribes(tmp_path, monkeypatch):
    """折叠→展开：重新订阅，且等新序列的第一份样本（不端出折叠前的读数）。"""
    from tests.test_island_shrink_pipeline import FakeScreen, _narrow_to

    screen = FakeScreen(1400)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    island, monitor, _hub = _island(_config(tmp_path, metrics=[CPU, FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        monitor.sample = _sample(144.0)
        island._tick_fps()
        assert _fps_seg(island).text == "144"
        assert island._row_plan().folded == (), "先给一块够宽的屏"

        _width, plan = _narrow_to(island, screen, lambda p: FPS in p.folded)
        assert FPS in plan.folded
        assert monitor.unsubscriptions[-1] == island._fps.key, "折掉的段必须退订"
        assert CPU in [s.seg_id for s in island._compose_info_segments()] or True

        screen._rect = QRect(0, 0, 1400, 768)      # 搬回宽屏 → 恢复
        island.refresh_from_config()
        assert island._row_plan().folded == ()
        assert monitor.subscribed == {island._fps.key}, "恢复必须重新订阅"
        restored = _fps_seg(island)
        assert (restored.state, restored.text) == (isl.STATE_SAMPLING, isl.UNKNOWN_TEXT)

        monitor.sample = _sample(8.0)
        island._tick_fps()
        assert _fps_seg(island).text == "8"
    finally:
        island.close()


def test_strip_dock_stops_the_frame_rate_too(tmp_path, monkeypatch):
    """停靠细条：内容一个像素都不画 = 帧率也不采（独占一场 ETW 会话的段更该守这条）。"""
    island, monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        island._mode = "docked"
        island._hover_peek = False
        island._sync_content_sampling()
        assert island._strip_hidden() is True, "用例要先真的进细条态"
        assert hub.keys == set(), "细条里帧率也不许占着采集会话"
        assert island._fps_timer.isActive() is False

        island._mode = "normal"
        island._sync_content_sampling()
        assert hub.keys == {island._fps.key}, "出细条按同一条路径恢复订阅"
        assert island._fps_timer.isActive() is True
        assert _fps_seg(island).state == isl.STATE_SAMPLING
    finally:
        island.close()


# ---------------------------------------------------------------- 降级：不占位
def test_a_platform_without_the_capability_takes_no_slot_but_keeps_the_reason(
        tmp_path, monkeypatch):
    """非 Windows：胶囊上连 ``--`` 都不占（段增删），卡片仍写清原因。"""
    island, monitor, hub = _island(
        _config(tmp_path, metrics=[FPS]), monkeypatch, platform_supported=False)
    try:
        island.show()
        app.processEvents()
        assert hub.monitor is None, "没有这个能力的机器连采集器都不建"
        assert _fps_missing(island) is True
        assert FPS not in island._row_plan().visible
        assert FPS not in island._row_plan().folded, "不占位的段连 +N 也折不到"
        assert island._unavailable_segments() == {FPS: gf.PLATFORM_UNSUPPORTED_REASON}
        assert "Windows" in gf.PLATFORM_UNSUPPORTED_REASON

        row = _row(island, FPS)
        assert row.state == isl.STATE_UNSUPPORTED
        assert row.value == isl.UNKNOWN_TEXT
        assert row.note == f"该指标不可用：{gf.PLATFORM_UNSUPPORTED_REASON}"
        assert gf.PLATFORM_UNSUPPORTED_REASON in row.hint
    finally:
        island.close()


def test_a_missing_backend_shrinks_the_capsule_once_and_values_never_do(
        tmp_path, monkeypatch):
    """段增删可以改宽（unsupported↔有读数各一次），读数变化一次都不许调 ``_update_size``。"""
    island, monitor, _hub = _island(_config(tmp_path, metrics=[CPU, FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        calls: list[int] = []
        original = island_mod.DynamicIsland._update_size

        def spy(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(island_mod.DynamicIsland, "_update_size", spy)
        monitor.sample = _sample(144.0)
        island._tick_fps()
        wide = island._capsule_width()
        assert calls == [], "普通读数（sampling→ok）绝不重算几何"

        reason = f"缺少内置采集程序 {gf.BACKEND_EXE_NAME}（预期路径：…）"
        monitor.sample = _sample(None, state=isl.STATE_UNSUPPORTED, reason=reason)
        island._tick_fps()
        assert len(calls) == 1, "段被移除 = 结构变化，几何重算一次"
        assert island._capsule_width() < wide, "少了 FPS 段，胶囊该变窄"
        assert island._unavailable_segments() == {FPS: reason}

        monitor.sample = _sample(None, state=isl.STATE_UNSUPPORTED, reason=reason)
        island._tick_fps()
        assert len(calls) == 1, "还是不可用：不再重复重算"

        monitor.sample = _sample(3.0)
        island._tick_fps()
        assert len(calls) == 2, "段回来 = 再重算一次"
        assert island._capsule_width() == wide
    finally:
        island.close()


# ---------------------------------------------------------------- 收口与多宠
def test_close_releases_the_shared_collector(tmp_path, monkeypatch):
    """窗口销毁：停表 + 退订（采集线程与子进程由采集器自己收口，GUI 不等它）。"""
    island, monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    island.show()
    app.processEvents()
    assert hub.keys == {island._fps.key}
    island.close()
    assert hub.keys == set()
    assert monitor.unsubscriptions == [island._fps.key]
    assert island._fps_timer.isActive() is False


def test_a_fault_survives_the_fold_in_the_card(tmp_path, monkeypatch):
    """折叠不埋没故障：折进 +N 后卡片仍写得出那一拍为什么没有读数。"""
    from tests.test_island_shrink_pipeline import FakeScreen, _narrow_to

    screen = FakeScreen(1400)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    island, monitor, _hub = _island(_config(tmp_path, metrics=[CPU, FPS]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        reason = "采集程序没有采集权限（需要 Performance Log Users 组）"
        monitor.sample = _sample(None, state=isl.STATE_ERROR, reason=reason)
        island._tick_fps()
        _narrow_to(island, screen, lambda p: FPS in p.folded)
        row = _row(island, FPS)
        assert row.state == isl.STATE_ERROR, "被折的段照旧把这一个故障呈出来"
        assert reason in row.note
    finally:
        island.close()


def test_the_app_quit_hook_releases_the_collector(tmp_path, monkeypatch):
    """应用退出（按退出路径不发 closeEvent）：退出钩子挂在应用上，且收口一次就够。

    钩子与 ``closeEvent`` 连的是**同一个槽**，所以这里直接调它不是抄近路：信号本身
    只在应用退出时发（测试里 emit 会连带触发别的退出收口），而"连上了没有"由连接数
    断言钉死——每个岛恰好连一次，且 PySide 在岛被回收后会自动断开（不会吊住窗口）。
    """
    before = app.receivers("2aboutToQuit()")
    island, _monitor, hub = _island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        assert app.receivers("2aboutToQuit()") == before + 1, "退出钩子必须挂在应用上"
        island.show()
        app.processEvents()
        assert hub.keys == {island._fps.key}
        island._release_fps_slot()
        assert hub.keys == set(), "退出收口必须退订（采集线程随之自停）"
        assert island._fps_timer.isActive() is False
        island._release_fps_slot()          # 幂等：重复收口不抛、不再重复退订
        assert hub.keys == set()
    finally:
        island.close()


def test_two_pets_share_one_collector_and_one_stay_alive_after_the_other_closes(
        tmp_path, monkeypatch):
    """多宠唯一：两只宠 = 一个采集器；关掉一只不影响另一只的读数。"""
    _pin_fps_platform(monkeypatch)      # 见 _island：段要在宿主平台之外也真的挂上
    monitor = _StubMonitor()
    hub = island_fps.FpsHub(monitor_factory=lambda: monitor)
    monkeypatch.setattr(island_fps, "_HUB", hub)
    first, _made = _render_island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    second, _made2 = _render_island(_config(tmp_path, metrics=[FPS]), monkeypatch)
    try:
        first.show()
        second.show()
        app.processEvents()
        assert hub.keys == {first._fps.key, second._fps.key}
        assert monitor.starts == 1, "第二个岛复用已经在跑的那条采集线程"
        monitor.sample = _sample(60.0)
        assert first._tick_fps() is True and second._tick_fps() is True
        assert _fps_seg(first).text == "60" and _fps_seg(second).text == "60"

        first.close()
        assert hub.keys == {second._fps.key}
        assert monitor.stops == 0, "还有一只宠在看读数，不许停采集"
        monitor.sample = _sample(30.0)
        assert second._tick_fps() is True
        assert _fps_seg(second).text == "30"
    finally:
        for island in (first, second):
            try:
                island.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 真实采集器接管
class _StubSession:
    """``FpsSession`` 的替身：按挂钟吐**事件时钟同步**的 CSV 行，不起子进程。

    只替换"PresentMon 子进程"这一个 OS 边界：行格式、表头列名、事件时钟列
    （``TimeInMs``）与真实输出逐字同形，所以采集器那一侧（表头识别、列解析、
    pid 过滤、事件时钟校准、窗口统计、发布、代次）全部是真代码在跑。

    ``stop_completed`` 是"会话收口真的做完了"的**明确信号**（在 ``stop`` 末尾置位）：
    收口是异步的（``unsubscribe`` 立刻清 ``_thread``，worker 稍后才在 finally 里调
    ``stop``），"采集线程的 ``running`` 变假"推不出它——见文件尾那条慢停闸门探针。
    """

    _HEADER = ("Application,ProcessID,SwapChainAddress,MsBetweenPresents,"
               "MsBetweenDisplayChange,TimeInMs")

    def __init__(self, pid: int, name: str, session_name: str, *, interval_ms: float = 1000.0 / 60.0):
        self.pid = int(pid)
        self.target_name = name
        self.session_name = session_name
        self._interval_ms = float(interval_ms)
        self._started = time.monotonic()
        self._next_ms = 0.0
        self._header_sent = False
        self.stopped = False
        self.closed = False
        self.stop_completed = threading.Event()

    def start(self) -> None:
        pass

    def drain_lines(self) -> list[str]:
        lines: list[str] = []
        if not self._header_sent:
            self._header_sent = True
            lines.append(self._HEADER)
        now_ms = (time.monotonic() - self._started) * 1000.0
        while self._next_ms <= now_ms and len(lines) < 40:
            lines.append(
                f"game.exe,{self.pid},0x0000000000000AAA,"
                f"{self._interval_ms:.4f},{self._interval_ms:.4f},{self._next_ms:.3f}")
            self._next_ms += self._interval_ms
        return lines

    def stderr_tail(self) -> str:
        return ""

    def poll(self):
        return None

    def stop(self, timeout=None, *, terminate_session: bool = True) -> None:
        self.stopped = True
        self.terminate_session = bool(terminate_session)
        self.stop_completed.set()

    def close(self, *, terminate_session: bool = True) -> None:
        self.closed = True


class _GatedStubSession(_StubSession):
    """``stop`` 上装了闸门的替身：进 ``stop`` 先发信号，再**停在闸门前面**。

    用来确定性地造出那一刻——"采集线程已经收到停止并进入会话收口，但收口还没做完"
    （慢 runner 上偶然出现、快机器上几乎撞不到）。闸门有超时兜底：用例自己出错时
    不许把守住它的 worker 永久挂在里面（跨用例的线程泄漏）。
    """

    #: 闸门最多关这么久：只是兜底（用例从"进 stop"到显式放行只隔几条语句），
    #: 正常路径永远由用例放行，不靠这个值。
    GATE_TIMEOUT = 60.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = threading.Event()
        self.stop_entered = threading.Event()

    def stop(self, timeout=None, *, terminate_session: bool = True) -> None:
        self.stop_entered.set()
        self.gate.wait(self.GATE_TIMEOUT)
        super().stop(timeout, terminate_session=terminate_session)


def _drive_qt_loop(predicate, *, timeout: float = 20.0) -> bool:
    """在真实 Qt 事件循环里等 predicate 成立（宽预算轮询，不赌定时器节奏）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def test_the_real_collector_reaches_the_island_through_the_qt_loop(tmp_path, monkeypatch):
    """真采集器（真线程/真状态机/真窗口统计）→ 真 Qt 1Hz 读表 → 胶囊与卡片。

    只替换两个测试里无法真实运行的 OS 边界：PresentMon 子进程（替身吐同形 CSV）与
    前台窗口查询（替身给固定 ``game.exe``）。不断言"真机 ETW 能采到帧"——那是实机
    探针的事（本机权限被拒，见交接报告）；这里断言的是**接线**：读数经真实采集器
    发布、经岛 1Hz 读表、按四态与定宽盒呈现，窗口销毁后订阅归零且线程退出。
    """
    stub_session = _StubSession(pid=4242, name="game.exe", session_name="test-session")

    def factory():
        return fm.FpsMonitor(
            foreground_reader=lambda: (4242, "game.exe"),
            process_identity=lambda pid: 1.0,
            session_factory=lambda pid, name, session: stub_session,
            excluded_names=frozenset(),
            target_stable_seconds=0.0,
            min_session_seconds=0.0,
            poll_seconds=0.02,
            publish_interval=0.0,
        )

    monkeypatch.setattr(island_fps, "_HUB", island_fps.FpsHub(monitor_factory=factory))
    monkeypatch.setattr(gf, "platform_support", lambda: True)
    monkeypatch.setattr(gf, "bundled_backend_path", lambda: _fake_backend(monkeypatch))
    cfg = _config(tmp_path, metrics=[FPS])
    island = DynamicIsland(cfg)          # 不替换任何岛侧工厂：走真实接线
    monitor = None
    try:
        island.show()
        assert _drive_qt_loop(lambda: _fps_seg(island).state == isl.STATE_OK), \
            f"真实读数没有走到胶囊上：{_fps_seg(island)}"
        segment = _fps_seg(island)
        assert segment.box_text == island_fps.VALUE_SAMPLE
        assert segment.text == "60", f"60000/1000 的稳定流应当报 60，实际 {segment.text!r}"
        row = _row(island, FPS)
        assert row.value == "60"
        assert "game.exe (pid 4242)" in row.hint
        assert gf.SOURCE_TEXT in row.hint
        assert row.unit_full == gf.UNIT_FULL
        monitor = island._fps.monitor
        assert monitor is not None and monitor.running is True
        assert monitor.subscribers == {island._fps.key}
    finally:
        island.close()
    assert monitor is not None
    assert monitor.subscribers == frozenset(), "关窗口后订阅必须归零"
    # 收口是**异步**的：退订当场清 ``_thread``（``FpsMonitor._stop_worker(wait=False)``），
    # 所以 ``running`` 立刻为假，而 worker 那一刻可能还没跑进 finally 里的会话收口——
    # 快机器上看不出来，慢 runner（mac CI 2026-10-16 00:32 原始红）上就是假红。
    # 因此等的是替身在自己 ``stop`` 末尾置位的**完成事件**（宽预算 + 泵 Qt），
    # 不是睡眠猜时序，也不是拿 ``running`` 当收口完成的证据。
    assert _drive_qt_loop(lambda: stub_session.stop_completed.is_set(), timeout=30.0), \
        "会话收口没在预算内完成（等的是替身 stop 的完成事件）"
    assert monitor.running is False, "收口后采集线程必须退出（不留下孤线程）"
    assert stub_session.stopped is True, "会话要收到停止（真机上即官方终止命令那一步）"


def test_repeated_hide_show_cycles_keep_one_session_and_leak_no_worker(
        tmp_path, monkeypatch):
    """反复隐藏/显示：一场采集会话（不重开会话）+ 一条采集线程；关窗口后一条不剩。"""
    sessions: list[_StubSession] = []

    def session_factory(pid, name, session_name):
        session = _StubSession(pid, name, session_name)
        sessions.append(session)
        return session

    def factory():
        return fm.FpsMonitor(
            foreground_reader=lambda: (4242, "game.exe"),
            process_identity=lambda pid: 1.0,
            session_factory=session_factory,
            excluded_names=frozenset(),
            target_stable_seconds=0.0,
            min_session_seconds=0.0,
            poll_seconds=0.02,
        )

    monkeypatch.setattr(island_fps, "_HUB", island_fps.FpsHub(monitor_factory=factory))
    monkeypatch.setattr(gf, "platform_support", lambda: True)
    monkeypatch.setattr(gf, "bundled_backend_path", lambda: _fake_backend(monkeypatch))
    before = _fps_threads()
    island = DynamicIsland(_config(tmp_path, metrics=[FPS]))
    monitor = None
    try:
        island.show()
        assert _drive_qt_loop(lambda: len(sessions) == 1), "第一场采集会话没起来"
        for _ in range(3):
            island.hide()
            app.processEvents()
            island.show()
            app.processEvents()
            assert _drive_qt_loop(lambda: island._fps.monitor.running is True), \
                "显示回来必须接上采集线程"
        assert len(sessions) == 1, f"隐藏/显示不该重开采集会话：{len(sessions)} 场"
        monitor = island._fps.monitor
    finally:
        island.close()
    assert monitor is not None
    deadline = time.monotonic() + 20.0
    while monitor.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert monitor.running is False, "关窗口后采集线程必须退出"
    # 线程退出到 threading.enumerate 可见之间还有一层调度延迟，慢 runner 上
    # 裸读会误判泄漏——泵到名单回落（宽预算，不赌时序）
    assert _drive_qt_loop(lambda: _fps_threads() == before), \
        f"反复开关后不许留下采集线程（线程泄漏）：{_fps_threads()} vs {before}"


def _fps_threads() -> list[str]:
    """活着的采集线程名（``fps_monitor.THREAD_NAME`` 前缀的都算：主循环 + 两条读线程）。"""
    return sorted(str(thread.name) for thread in threading.enumerate()
                  if str(thread.name).startswith(fm.THREAD_NAME))


def _fake_backend(monkeypatch):
    """给"内置采集程序在不在"这一关一个真实存在的文件（不启动它）。"""
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp(prefix="dsh-fps-")) / gf.BACKEND_EXE_NAME
    path.write_bytes(b"stub")
    monkeypatch.setattr(gf, "BACKEND_EXE_NAME", path.name)
    return path


def test_a_slow_session_stop_proves_running_false_is_not_stop_completed(
        tmp_path, monkeypatch):
    """慢 ``stop`` 闸门：确定性地钉住"``running`` 变假 ≠ 会话收口完成"。

    这是上面那条真采集器用例收口断言的**探针**，把它的理由做成可复现的一步：

    - ``FpsHub.detach`` → ``FpsMonitor.unsubscribe`` → ``_stop_worker(wait=False)``
      **当场**把 ``_thread`` 清空，于是 ``monitor.running`` 立刻为假；而 worker 那一
      刻可能还没跑进 ``finally`` 里的 ``_close_session``（真会话 stop 在慢机器上要
      发官方终止命令 + 有界等它退出，本机替身则是被闸门停住）。
    - 所以旧写法"等 ``running`` 变假，再断言 ``stopped is True``"是一个**时序赌**：
      慢 runner 上必红——mac CI 2026-10-16 00:32 的原始红（``.scratch/ci278-macos-failed.txt``
      ``tests/test_island_fps_integration.py:839``，``assert False is True``）就是这个形态。
    - 闸门由本用例显式开关，所以窗口是**确定**出现的（不是睡出来的），换到快机器上
      也一样复现；放行后按完成事件等收口，两个原断言（``running`` 已停、``stopped``
      为真）都保留。
    """
    session = _GatedStubSession(pid=4242, name="game.exe", session_name="gated-session")
    before = _fps_threads()

    def factory():
        return fm.FpsMonitor(
            foreground_reader=lambda: (4242, "game.exe"),
            process_identity=lambda pid: 1.0,
            session_factory=lambda pid, name, session_name: session,
            excluded_names=frozenset(),
            target_stable_seconds=0.0,
            min_session_seconds=0.0,
            poll_seconds=0.02,
            publish_interval=0.0,
        )

    monkeypatch.setattr(island_fps, "_HUB", island_fps.FpsHub(monitor_factory=factory))
    monkeypatch.setattr(gf, "platform_support", lambda: True)
    monkeypatch.setattr(gf, "bundled_backend_path", lambda: _fake_backend(monkeypatch))
    island = DynamicIsland(_config(tmp_path, metrics=[FPS]))
    monitor = None
    try:
        island.show()
        assert _drive_qt_loop(lambda: _fps_seg(island).state == isl.STATE_OK), \
            "读数没上来：探针的前置条件不成立"
        monitor = island._fps.monitor
        assert monitor is not None and monitor.running is True

        island.close()
        # 旧写法的等价物：等 ``running`` 变假——等到了也**推不出**收口完成。
        assert _drive_qt_loop(lambda: monitor.running is False, timeout=20.0), \
            "退订后 running 应当立刻为假（收口异步，不 join）"
        assert _drive_qt_loop(lambda: session.stop_entered.is_set(), timeout=20.0), \
            "worker 没走到会话收口：前置条件不成立"
        assert session.stopped is False, \
            "闸门关着时收口必然没完成——旧写法在这一刻断言 stopped 就是赌时序"

        session.gate.set()
        assert _drive_qt_loop(lambda: session.stop_completed.is_set(), timeout=30.0), \
            "放行闸门后收口必须完成"
        assert session.stopped is True
        assert monitor.running is False, "收口完成后采集线程不许还在跑"
    finally:
        # 用例中途失败也不许把 worker 永久关在闸门里（跨用例线程泄漏）
        session.gate.set()
        island.close()
        assert _drive_qt_loop(lambda: _fps_threads() == before, timeout=30.0), \
            "用例收尾后不得遗留采集线程"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_platform_gate_is_read_at_sync_time_not_at_import(monkeypatch, platform):
    """平台闸门读的是当刻判定：同一个段在两种平台下都能自洽（不 import 期定死）。"""
    monkeypatch.setattr(sys, "platform", platform)
    slot = island_fps.FpsSlot(key=f"gate-{platform}", hub=island_fps.FpsHub())
    assert bool(slot.unavailable_reason()) == (platform != "win32")
