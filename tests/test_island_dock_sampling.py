# -*- coding: utf-8 -*-
"""灵动岛**停靠细条停采样**（P2 Slice E6，D2 越界发现转正）的契约测试。

施工单 §E6：停靠细条态 = **内容一个像素都不画**（细条只画头像 + 状态点，见
``paintEvent`` 走 ``_paint_strip`` 的那个分支），那些读数既然看不见，采样就该
**全停**——退订口径与 P1 Slice D2 的折叠态完全一致（网络段整条收口、设备槽按 id
退订，0 订阅零 syscall），出细条按既有 ``_sync_*_slot`` 路径恢复订阅，且**首轮按
四态报 ``--``**（不拿细条期间的陈旧读数冒充"它一直在采"）。

"细条态"判据与绘制层同源：``_mode == "docked" and not _hover_peek``——hover 滑出的
完整胶囊是"内容可见"，照常采样。所以本文件把**所有**会翻这两个标志的入口都走一遍：
启动即停靠 / 配置保存到停靠边 / 拖到边缘停靠 / 细条单击滑出 / hover 滑出与收回 /
拖出停靠 / 展开卡片与收起 / 关掉「靠边半隐藏」。

采样器一律替身（``_make_net_monitor`` / ``_make_sys_monitor`` 替换点）：本文件不发
探测包、不碰 psutil。缺陷：段宽按真实字体实测，段太多会在 800×800 的 offscreen
屏上被收缩流水线折掉（那是另一条闸门）——所以用例只用 1-2 个段并在建岛后断言
"没被折"，把停靠这条闸门单独隔出来验。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import segments as isl
from pet import net_monitor as nm
from pet import sys_monitor as sm
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])


class StubMonitor:
    """采样器替身：网络槽/设备槽两面都有，只记录生命周期调用。"""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
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
        """起始幂等（与真底座同契约：已在跑就返回 False）。"""
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
        "show_status": True, "style": "dark", "x": 200, "y": 300,
    }
    data.update(island_overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return cfg


def _island(tmp_path, monkeypatch, *, metrics=(), **island_overrides):
    """建岛：两条采样路径都换替身；断言段没被收缩流水线折掉（隔开另一条闸门）。"""
    made: list[StubMonitor] = []

    def factory(_self, interval):
        monitor = StubMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    island = DynamicIsland(_config(tmp_path, metrics=metrics, **island_overrides))
    assert island._row_plan().folded == (), \
        "本用例的段必须都放得下：折叠会额外退订，混淆停靠这条闸门"
    return island, made


def _segment(island: DynamicIsland, seg_id: str):
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}")


def _sys_sample(cpu=24.0, mem=61.0, *, sampled_at=1.0):
    return sm.SysSample(cpu_percent=cpu, mem_percent=mem, sampled_at=sampled_at)


def _net_sample(down=2048.0, up=512.0, latency=23.0):
    return nm.NetSample(down_bps=down, up_bps=up, latency_ms=latency,
                        latency_lost=False, sampled_at=1.0)


def _dock_by_config(island: DynamicIsland, edge: str = "top") -> None:
    """走"配置值 → 应用位置"那条路进停靠（构造与设置保存都走它）。"""
    data = dict(island.config.get("dynamic_island"))
    data["dock_edge"] = edge
    island.config.set("dynamic_island", data)
    island.refresh_from_config()


def _set_config(island: DynamicIsland, **overrides) -> None:
    data = dict(island.config.get("dynamic_island"))
    data.update(overrides)
    island.config.set("dynamic_island", data)
    island.refresh_from_config()


def _assert_sampling_off(island: DynamicIsland) -> None:
    """停靠细条态的采样收口口径（与折叠态同一套：对象丢弃 + 计时器停）。"""
    assert island._net_monitor is None, "细条态不得留着网络采样器"
    assert island._net_sample is None, "细条态不得留着网络快照"
    assert island._net_timer.isActive() is False
    assert island._sys_monitor is None, "细条态不得留着设备采样器"
    assert island._sys_sample is None, "细条态不得留着设备快照"
    assert island._sys_timer.isActive() is False
    assert island._sys_subscribed == set(), "退订计数不许留在底座上"


def _assert_sampling_on(island: DynamicIsland) -> None:
    assert island._net_timer.isActive() is True
    assert island._sys_timer.isActive() is True


# ---------------------------------------------------------------- 鼠标路径
def _avail():
    screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


class _FakeScreen:
    """固定 2560×1440 的假屏：拖拽停靠测试的落点必须离四边远超 40px 停靠阈值。

    为什么不用真实/offscreen 屏：offscreen 默认屏只有 800×800，而退化后端字体
    把岛撑到 ~730px 宽——拖到"屏中心"后左右缘距屏幕边只剩 ~36px，落进 40px
    停靠阈值被误判成靠左/右停靠（E7 加宽 13px 后从 43px 跌过线）。屏宽 2560
    时中心落点距四边 ≥700px，判定与环境字体无关。
    """

    def __init__(self, width=2560, height=1440):
        self._rect = QRect(0, 0, width, height)

    def availableGeometry(self):
        return self._rect


def _mouse(kind, widget, global_pos, button=Qt.MouseButton.LeftButton,
           buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    local = widget.mapFromGlobal(global_pos)
    return QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                       button, buttons, Qt.KeyboardModifier.NoModifier)


def _drag_to(widget, target_global) -> None:
    start = widget.geometry().center()
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, start))
    mid = type(start)((start.x() + target_global.x()) // 2,
                      (start.y() + target_global.y()) // 2)
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, mid))
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, target_global))
    widget.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, widget, target_global,
               buttons=Qt.MouseButton.NoButton))


def _click(widget) -> None:
    center = widget.geometry().center()
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, center))
    widget.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, widget, center,
               buttons=Qt.MouseButton.NoButton))


# ---------------------------------------------------------------- 进/出细条
def test_starting_docked_never_creates_a_monitor(tmp_path, monkeypatch):
    """启动即停靠（配置里存着停靠边）：一条采样线程都不许起。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM],
                           dock_edge="top")
    try:
        island.show()
        app.processEvents()
        assert island._debug_state()["mode"] == "docked"
        assert made == [], "细条态不得创建任何采样器"
        _assert_sampling_off(island)
        for seg_id in (NETWORK, CPU, MEM):
            assert _segment(island, seg_id).state == isl.STATE_SAMPLING
            assert _segment(island, seg_id).text == "--"
    finally:
        island.close()


def test_docking_closes_both_slots_and_stops_their_timers(tmp_path, monkeypatch):
    island, made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM])
    try:
        island.show()
        app.processEvents()
        _assert_sampling_on(island)
        net_stub, sys_stub = island._net_monitor, island._sys_monitor
        assert net_stub is not None and sys_stub is not None
        assert sys_stub.subscribed == {CPU, MEM}

        _dock_by_config(island)
        assert island._debug_state()["mode"] == "docked"
        assert net_stub.closed is True and net_stub.started is False
        assert CPU in sys_stub.unsubscribed and MEM in sys_stub.unsubscribed
        _assert_sampling_off(island)
    finally:
        island.close()


def test_undocking_resubscribes_with_a_dash_first_round(tmp_path, monkeypatch):
    """出细条恢复订阅，首轮按四态 ``--``（不冒充"一直在采"）。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM])
    try:
        island.show()
        app.processEvents()
        first_net = island._net_monitor
        _dock_by_config(island)
        _assert_sampling_off(island)

        _set_config(island, edge_dock=False)      # 关掉「靠边半隐藏」→ 复位胶囊
        assert island._debug_state()["mode"] == "normal"
        _assert_sampling_on(island)
        assert island._net_monitor is not None and island._net_monitor.started is True
        assert island._net_monitor is not first_net, "细条态收口过：出细条是重建采样器"
        assert island._sys_monitor is not None
        assert island._sys_monitor.subscribed == {CPU, MEM}
        for seg_id in (NETWORK, CPU, MEM):
            assert _segment(island, seg_id).state == isl.STATE_SAMPLING, seg_id
            assert _segment(island, seg_id).text == "--", seg_id

        # 新快照到手之后才报读数（E4 起数值与单位分双盒：37 + %）
        island._sys_monitor.sample = _sys_sample(cpu=37.0, mem=61.0)
        island._tick_sys()
        island._net_monitor.sample = _net_sample()
        island._tick_network()
        cpu = _segment(island, CPU)
        assert (cpu.text, cpu.unit, cpu.state) == ("37", "%", isl.STATE_OK)
        assert _segment(island, NETWORK).state == isl.STATE_OK
    finally:
        island.close()


def test_hover_peek_resumes_sampling_and_dock_back_stops_it(tmp_path, monkeypatch):
    """hover 滑出的完整胶囊 = 内容可见 → 采样恢复；收回细条 → 再停。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU],
                            dock_edge="top")
    try:
        island.show()
        app.processEvents()
        _assert_sampling_off(island)

        island.enterEvent(None)                   # 鼠标靠近 → 滑出
        island._finish_animations()
        assert island._hover_peek is True
        _assert_sampling_on(island)
        assert island._net_monitor is not None
        assert _segment(island, CPU).text == "--", "滑出首轮是采样中"

        island.leaveEvent(None)
        island._dock_back()                       # 直接驱动定时器回调
        island._finish_animations()
        assert island._hover_peek is False
        _assert_sampling_off(island)
    finally:
        island.close()


def test_clicking_the_strip_peek_resumes_sampling(tmp_path, monkeypatch):
    """细条单击 = 滑出预览（不是展开卡片）：那也是内容可见，采样要跟上。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK],
                            dock_edge="top")
    try:
        island.show()
        app.processEvents()
        _assert_sampling_off(island)

        _click(island)
        assert island._hover_peek is True
        assert island._net_monitor is not None and island._net_monitor.started is True
        assert island._net_timer.isActive() is True

        island._dock_back()
        _assert_sampling_off(island)
    finally:
        island.close()


def test_drag_to_edge_stops_sampling_and_dragging_away_resumes(tmp_path, monkeypatch):
    """真实拖拽路径：拖到上边缘停靠 → 全停；拖离 → 恢复订阅 + 首轮 ``--``。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM])
    fake_screen = _FakeScreen()
    # 两处选屏都要钉：心跳/夹取走 _current_screen，松手落点走 _screen_at(光标)
    monkeypatch.setattr(
        type(island), "_current_screen", lambda self: fake_screen)
    monkeypatch.setattr(
        type(island), "_screen_at", lambda self, _pos: fake_screen)
    island.refresh_from_config()
    available = fake_screen.availableGeometry()
    assert available is not None
    try:
        island.show()
        app.processEvents()
        _assert_sampling_on(island)

        _drag_to(island, type(island.geometry().topLeft())(
            island.x(), available.top()))
        assert island._debug_state()["mode"] == "docked"
        assert island._debug_state()["dock_edge"] == "top"
        _assert_sampling_off(island)
        assert _segment(island, CPU).text == "--"

        _drag_to(island, available.center())
        assert island._debug_state()["mode"] == "normal"
        _assert_sampling_on(island)
        assert island._sys_monitor.subscribed == {CPU, MEM}
        assert _segment(island, CPU).state == isl.STATE_SAMPLING
    finally:
        island.close()


def test_expanding_from_the_strip_resumes_and_collapsing_stops(tmp_path, monkeypatch):
    """展开卡片也是内容可见（卡片 + 胶囊内容都在画）；收起回细条再停。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU],
                            dock_edge="top")
    try:
        island.show()
        app.processEvents()
        _assert_sampling_off(island)

        island.expand_card()
        assert island._debug_state()["mode"] == "expanded"
        _assert_sampling_on(island)
        assert island._net_monitor is not None

        island.collapse_card(animate=False)
        assert island._debug_state()["mode"] == "docked", "有停靠边就收回细条"
        _assert_sampling_off(island)
    finally:
        island.close()


def test_docked_strip_stays_off_across_refresh_and_plan_recompute(tmp_path, monkeypatch):
    """细条态是稳定的关：设置刷新/行计划重算/几何重算都不许把它拉活。"""
    island, made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM])
    try:
        island.show()
        app.processEvents()
        _dock_by_config(island)
        _assert_sampling_off(island)
        created = len(made)

        for _ in range(3):
            island.refresh_from_config()
        island._row_plan()
        island._update_size()
        island._capsule_width()
        island._apply_fixed_size()

        _assert_sampling_off(island)
        assert len(made) == created, "细条态不许重建采样器"
    finally:
        island.close()


def test_docked_strip_does_not_change_the_width_or_fade_keys(tmp_path, monkeypatch):
    """停靠只是"画哪一种图元"：不进宽度白名单、不触发结构淡化（内容没变）。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[NETWORK, CPU, MEM])
    try:
        island.show()
        app.processEvents()
        plan = island._row_plan()
        before = (island._plan_inputs(), island._structure_key(plan),
                  island.width())

        _dock_by_config(island)
        plan = island._row_plan()
        assert (island._plan_inputs(), island._structure_key(plan),
                island.width()) == before, "停靠不是内容结构变化"
    finally:
        island.close()


def test_docked_strip_does_not_advance_alert_severity(tmp_path, monkeypatch):
    """停采样期间告警滞回**不推进**（E2 的推进只在 _tick_sys 里，别画蛇添足）。"""
    island, _made = _island(tmp_path, monkeypatch, metrics=[CPU, MEM])
    try:
        island.show()
        app.processEvents()
        island._sys_monitor.sample = _sys_sample(cpu=99.0, mem=50.0)
        island._tick_sys()
        assert island._alert_severity.get(CPU) == "crit"

        _dock_by_config(island)
        _assert_sampling_off(island)
        frozen = dict(island._alert_severity)
        island._tick_sys()                        # 采样器已收口：什么也不该发生
        assert island._alert_severity == frozen
        assert _segment(island, CPU).text == "--", "细条态不拿旧读数冒充当下"
    finally:
        island.close()
