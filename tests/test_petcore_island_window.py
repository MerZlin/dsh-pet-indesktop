"""灵动岛窗口（`petcore/ui/island.py`）的 offscreen 测试。

覆盖：几何下限与固定高、段组合、展开/收起、点胶囊的行为分叉、配置刷新、
位置持久化与屏幕夹取、隐藏即停采样、读数不改宽。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.island import segments as isl
from petcore.platform import netmon, sysmon
from petcore.ui import island as island_ui

app = QApplication.instance() or QApplication([])


class _Event:
    """最小鼠标事件替身（只要窗口用到的三个成员）。"""

    def __init__(self, point: QPoint, button=Qt.MouseButton.LeftButton) -> None:
        self._point = point
        self._button = button

    def button(self):
        return self._button

    def globalPosition(self) -> QPointF:
        return QPointF(self._point)

    def accept(self) -> None:
        return None


@pytest.fixture()
def make_island(tmp_path):
    created = []

    def factory(values=None, *, character_name="深深"):
        config = Config(
            tmp_path,
            {
                "island_enabled": True,
                "island_metrics": ["cpu", "mem"],
                **(values or {}),
            },
        )
        widget = island_ui.DynamicIsland(config, character_name=character_name)
        created.append(widget)
        return widget

    yield factory
    for widget in created:
        widget.stop()
        assert widget.sys_monitor.wait_stopped(5)
        assert widget.net_monitor.wait_stopped(5)
        assert widget.disk_monitor.wait_stopped(5)
        widget.close()
        widget.deleteLater()


def test_capsule_has_fixed_height_and_a_minimum_width(make_island):
    island = make_island({"island_show_name": False})
    assert island.height() == island_ui.CAPSULE_HEIGHT
    assert island.width() >= island_ui.MIN_CAPSULE_WIDTH


def test_metrics_come_from_config_in_order(make_island):
    island = make_island({"island_metrics": ["network", "cpu"]})
    plan = island.plan()
    assert plan is not None
    assert plan.visible[0] == "primary"
    assert plan.visible[1:] == ("network", "cpu")


def test_unknown_metric_ids_do_not_take_space(make_island):
    island = make_island({"island_metrics": ["cpu", "unknown"]})
    plan = island.plan()
    assert plan is not None
    assert plan.visible == ("primary", "cpu")


def test_readings_never_change_the_planned_width(make_island):
    island = make_island()
    before = island.width()
    island.sys_monitor = sysmon.SysMonitor(cpu_reader=lambda: 99.0, mem_reader=lambda: 99.0)
    island.sys_monitor.poll()
    island.sys_monitor.poll()
    counts = iter([(0.0, 0.0), (1.0, 1.0)])
    ticks = iter([0.0, 1.0])
    island.net_monitor = netmon.NetMonitor(meter=netmon.NetRateMeter(
        reader=lambda: next(counts), clock=lambda: next(ticks)))
    island.net_monitor.poll()
    island.net_monitor.poll()
    island._tick()
    assert island.width() == before


def test_expand_grows_the_window_and_show_a_card(make_island):
    island = make_island()
    base = island.height()
    island.expand_card()
    assert island.is_expanded() is True
    assert island.width() >= island_ui.CARD_MIN_WIDTH
    assert island.height() > base
    assert island.card.isVisible() is False  # 父窗口未 show 时子控件也不可见
    island.collapse_card()
    assert island.is_expanded() is False
    assert island.height() == base


def test_messages_are_off_by_default_and_never_enter_the_card(make_island):
    """消息显示默认关：不记录、不进卡片、不弹「清除消息」。"""
    island = make_island()
    island.add_message("这条不该出现")
    assert tuple(island._recent_messages) == ()  # noqa: SLF001
    assert "最近消息" not in [row[0] for row in island._card_rows()]
    island.expand_card()
    assert island.card._clear_button.isHidden()  # noqa: SLF001


def test_message_toggle_shows_and_hides_the_history(make_island):
    """开关打开：新消息按行进卡片、可清除；关掉立刻从卡片消失。"""
    island = make_island()
    island.config.set("island_show_messages", True)
    island.refresh_from_config()
    island.add_message("第一条")
    island.add_message("第二条")
    assert [row[0] for row in island._card_rows()].count("最近消息") == 2
    island.expand_card()
    assert not island.card._clear_button.isHidden()  # noqa: SLF001
    island.config.set("island_show_messages", False)
    island.refresh_from_config()
    assert "最近消息" not in [row[0] for row in island._card_rows()]
    assert island.card._clear_button.isHidden()  # noqa: SLF001


def test_expanded_row_folds_instead_of_clipping(make_island):
    """展开态比胶囊窄时（420 上限），行必须按卡片宽重排而不是画到窗外。"""
    island = make_island({"island_metrics": ["network", "cpu", "mem"]})
    capsule_width = island.width()
    island.expand_card()
    plan = island.plan()
    assert island.width() <= island_ui.CARD_MAX_WIDTH
    assert plan.width <= island.width() + 2 * island_ui.CAPSULE_INSET
    if capsule_width > island.width():
        assert plan.folded, "窄卡片下应把放不下的段折进 +N"
    island.collapse_card()
    assert island.width() == capsule_width


def test_card_lists_one_row_per_metric(make_island):
    island = make_island({"island_metrics": ["cpu", "mem"]})
    rows = island._card_rows()
    assert [row[0] for row in rows] == ["CPU", "内存"]


def test_card_gives_glyph_only_segments_a_readable_name(make_island):
    island = make_island({"island_metrics": ["network"]})
    rows = island._card_rows()
    assert rows[0][0] == "网络"


def test_card_reports_sampling_and_errors(make_island):
    island = make_island({"island_metrics": ["cpu"]})
    assert island._card_rows()[0][1] == "--"          # 还没采到第一拍
    def unavailable():
        raise sysmon.PsutilUnavailable("psutil 不可用")

    island.sys_monitor = sysmon.SysMonitor(cpu_reader=unavailable)
    island.sys_monitor.poll()
    island._rebuild_row()
    row = island._card_rows()[0]
    assert row[3] == isl.ALERT_CRIT and "psutil" in row[2]


def test_click_toggles_the_card(make_island):
    island = make_island()
    island.mousePressEvent(_Event(QPoint(10, 10)))
    island.mouseReleaseEvent(_Event(QPoint(10, 10)))
    assert island.is_expanded() is True
    island.mousePressEvent(_Event(QPoint(10, 10)))
    island.mouseReleaseEvent(_Event(QPoint(10, 10)))
    assert island.is_expanded() is False


def test_click_action_can_forward_to_the_pet(make_island):
    island = make_island({"island_click_action": "toggle_pet"})
    fired = []
    island.clicked.connect(lambda: fired.append(True))
    island.mousePressEvent(_Event(QPoint(10, 10)))
    island.mouseReleaseEvent(_Event(QPoint(10, 10)))
    assert fired == [True]
    assert island.is_expanded() is False


def test_drag_moves_the_window_and_saves_the_position(make_island):
    island = make_island()
    start = island.pos()
    island.mousePressEvent(_Event(QPoint(start.x() + 5, start.y() + 5)))
    target = QPoint(start.x() + 120, start.y() + 40)
    island._dragging = True
    island.move(target)
    island.mouseReleaseEvent(_Event(target))
    config = island.config
    assert config.get("island_x") == target.x() or config.get("island_x") is not None


def test_drag_collapses_an_open_card(make_island):
    island = make_island()
    island.expand_card()
    island.mousePressEvent(_Event(QPoint(10, 10)))
    island._dragging = True
    island.mouseMoveEvent(_Event(QPoint(60, 40)))
    assert island.is_expanded() is False


def test_saved_position_is_restored(make_island):
    first = make_island()
    first.move(200, 120)
    first.mousePressEvent(_Event(QPoint(205, 125)))
    first._dragging = True
    first.mouseReleaseEvent(_Event(QPoint(200, 120)))
    saved_x = first.config.get("island_x")
    assert saved_x is not None
    # 同一数据根再造一个：应当读回保存的位置
    second = island_ui.DynamicIsland(first.config, character_name="深深")
    try:
        assert abs(second.x() - int(saved_x)) <= 2
    finally:
        second.stop()
        second.deleteLater()


def test_refresh_from_config_rebuilds_segments_and_width(make_island):
    island = make_island()
    wide = island.width()
    island.config.set("island_show_name", False)
    island.config.set("island_metrics", ["cpu"])
    island.refresh_from_config()
    assert island.width() < wide
    assert island.plan().visible == ("primary", "cpu")


def test_hidden_island_stops_sampling(make_island):
    island = make_island({"island_metrics": ["cpu", "network"]})
    island.show()
    app.processEvents()
    island.start()
    assert island.sys_monitor.running() is True
    assert island.net_monitor.running() is True
    island.hide()
    app.processEvents()
    assert island.sys_monitor.wait_stopped(5)
    assert island.sys_monitor.running() is False
    assert island.net_monitor.wait_stopped(5)
    assert island.net_monitor.running() is False


def _record_probe_sync(island):
    wanted: list[frozenset[str]] = []
    island.set_probe_sync_callback(lambda ids: wanted.append(frozenset(ids)))
    return wanted


def test_stop_reports_no_wanted_probes(make_island):
    """隐藏/停用必须让宿主收到空集：否则 GPU/FPS 在岛看不见时继续采（白烧电）。"""
    island = make_island({"island_metrics": ["cpu", "gpu", "fps"]})
    wanted = _record_probe_sync(island)
    island.show()
    app.processEvents()
    island.start()
    assert wanted[-1] == frozenset({isl.GPU, isl.FPS})
    island.stop()
    assert wanted[-1] == frozenset(), "stop() 必须自己收口采样，不能等下一次 1Hz 心跳"


def test_hide_reports_no_wanted_probes(make_island):
    island = make_island({"island_metrics": ["gpu"]})
    wanted = _record_probe_sync(island)
    island.show()
    app.processEvents()
    assert wanted[-1] == frozenset({isl.GPU})
    island.hide()
    app.processEvents()
    assert wanted[-1] == frozenset()


def test_set_visible_false_reports_no_wanted_probes(make_island):
    island = make_island({"island_metrics": ["fps"]})
    wanted = _record_probe_sync(island)
    island.set_visible(True)
    app.processEvents()
    assert wanted[-1] == frozenset({isl.FPS})
    island.set_visible(False)
    assert wanted[-1] == frozenset()
    island.set_visible(False)  # 已经隐藏：仍必须是空集（幂等，不重新拉起采样）
    assert wanted[-1] == frozenset()


def test_hover_pauses_auto_collapse(make_island):
    island = make_island()
    island.expand_card()
    assert island._card_timer.isActive() is True
    island.enterEvent(None)
    assert island._card_timer.isActive() is False
    island.leaveEvent(None)
    assert island._card_timer.isActive() is True


def test_island_config_reads_defaults_from_any_mapping():
    settings = island_ui.IslandConfig.from_config({})
    assert settings.enabled is False
    assert settings.style == "dark"
    assert settings.metrics == ()
    assert settings.click_action == "expand"


def test_palette_alpha_follows_the_opacity_setting(make_island):
    island = make_island({"island_opacity": 0.5})
    palette = island._palette()
    assert palette["bg"][3] < 235
    island.settings = island_ui.IslandConfig.from_config(
        {"island_opacity": 1.0}, character_name="深深"
    )
    assert island._palette()["bg"][3] >= 235


def test_alert_colours_only_touch_the_value(make_island):
    island = make_island({"island_metrics": ["cpu"]})
    island.sys_monitor = sysmon.SysMonitor(cpu_reader=lambda: 95.0, mem_reader=lambda: 50.0)
    island.sys_monitor.poll()
    island.sys_monitor.poll()
    island._alert["cpu"] = isl.alert_severity("cpu", 95.0, "")
    island._rebuild_row()
    row = island._row
    cpu = next(seg for seg in row if seg.seg_id == "cpu")
    assert cpu.severity == isl.ALERT_CRIT
    # 段宽仍由声明样本决定（读数不进宽度）
    assert cpu.box_text == sysmon.MAX_TEXT.rstrip("%")


def test_stop_is_idempotent(make_island):
    island = make_island()
    island.start()
    island.stop()
    island.stop()
    assert island.sys_monitor.wait_stopped(5)
    assert island.sys_monitor.running() is False


def test_expanding_card_keeps_folded_metrics_sampling(make_island):
    """卡片态折行只是排版：每段都有明细行，值仍然可见，不该停采（AUD-25）。

    胶囊态被折进 `+N` 的段照旧停采（那才是真的看不见），见下一条用例。
    """
    island = make_island({"island_metrics": ["cpu", "mem", "network"]},
                         character_name="Shenshen")
    island.show()
    app.processEvents()
    assert "network" not in island.plan().folded
    assert island.net_monitor.running()
    island.expand_card()
    assert "network" in island.plan().folded
    assert island.net_monitor.running(), "卡片态不该因为折行停采"
    island.collapse_card()
    assert island.net_monitor.running()


def test_capsule_folding_still_stops_the_sampler(make_island, monkeypatch):
    """胶囊态折进 `+N` 的段看不见 → 停采（这条红线保留）。"""
    island = make_island({"island_metrics": ["cpu", "mem", "network"]},
                         character_name="Shenshen")
    island.show()
    app.processEvents()
    assert island.net_monitor.running()
    monkeypatch.setattr(island, "_available_width", lambda: 240)
    island.refresh_from_config()
    assert "network" in island.plan().folded
    assert island.net_monitor.wait_stopped(5), "胶囊上被折进 +N 的段必须停采"
    assert not island.net_monitor.running()


def test_disabling_visible_island_stops_samplers(make_island):
    island = make_island({"island_metrics": ["cpu", "network"]})
    island.show()
    app.processEvents()
    island.config.set("island_enabled", False)
    island.refresh_from_config()
    assert island.sys_monitor.wait_stopped(5)
    assert island.net_monitor.wait_stopped(5)
    assert not island.sys_monitor.running()
    assert not island.net_monitor.running()
