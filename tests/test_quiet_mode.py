# -*- coding: utf-8 -*-
"""免打扰（issue #208）聚焦回归：纯状态机 + 抑制真值表 + 菜单 + 设置页。

分成三段，对应三种风险等级：

1. **纯逻辑**（无 Qt）：倒计时/档位/暂存队列/全屏等效——注入假钟，不 sleep、
   不赌真实时序。
2. **抑制真值表**（真实 PetWindow + 真实提醒队列）：免打扰 × 事件类型 × 全屏，
   含"穿透类一律不抑制"与"自言自语/联动气泡不受影响"。
3. **接线**（真实菜单树 / 设置对话框 / 进程级广播 / 真实事件循环）：菜单子菜单
   行为、设置页两行的落位与保存 round trip、``AppShell`` 全屏广播把等效免打扰
   打开、到期后轮询表汇报暂存提醒。
"""

from __future__ import annotations

import time

import pytest

from pet import config as config_mod
from pet import quiet_mode
from pet import window_alerts

pytest.importorskip("PySide6")


class FakeClock:
    """可推进的单调钟（不 sleep：CI 慢 runner 上也不会翻车）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@pytest.fixture(autouse=True)
def _clean_quiet_state():
    """进程级免打扰状态在用例之间必须干净（它是单例）。"""
    quiet_mode.reset_shared_quiet_state()
    yield
    quiet_mode.reset_shared_quiet_state()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 1. 纯逻辑


@pytest.mark.parametrize(
    "manual,fullscreen,also_on_fullscreen,expected",
    [
        (False, False, True, False),    # 什么都没开
        (False, False, False, False),
        (True, False, True, True),      # 手动免打扰
        (True, False, False, True),     # 手动免打扰与全屏开关无关
        (False, True, True, True),      # 全屏等效（默认开）
        (False, True, False, False),    # 用户关掉全屏联动 → 全屏不抑制
        (True, True, False, True),      # 手动仍在，全屏开关管不着
        (True, True, True, True),
    ],
)
def test_suppression_truth_table(manual, fullscreen, also_on_fullscreen, expected):
    """免打扰 × 全屏 × 配置开关的抑制真值表（唯一判据 suppression_active）。"""
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    if manual:
        state.start(15)
    if fullscreen:
        state.set_fullscreen(True)

    assert state.suppression_active(also_on_fullscreen=also_on_fullscreen) is expected


def test_duration_choices_and_remaining_label():
    assert config_mod.QUIET_MINUTE_CHOICES == (15, 30, 60, 120)
    assert config_mod.normalize_quiet_minutes(30) == 30
    assert config_mod.normalize_quiet_minutes(90) == 60      # 非档位取最近档
    assert config_mod.normalize_quiet_minutes("abc") == config_mod.DEFAULT_QUIET_MINUTES
    assert config_mod.normalize_quiet_minutes(-5) == config_mod.DEFAULT_QUIET_MINUTES
    assert quiet_mode.quiet_duration_label(15) == "15 分钟"
    assert quiet_mode.quiet_duration_label(60) == "1 小时"
    assert quiet_mode.format_remaining(754) == "12:34"
    assert quiet_mode.format_remaining(3725) == "1:02:05"
    assert quiet_mode.format_remaining(-3) == "00:00"


def test_expiry_ends_quiet_and_keeps_queue_until_delivered():
    """到点自动结束：快照交出去，**排空要等交付确认**（先排空再交付 = 丢提醒）。"""
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    state.start(15)
    state.remember("一条普通提醒")

    assert state.take_report() is None, "仍在免打扰中：没有待汇报"
    assert state.manual_active() is True
    assert state.remaining_label() != "00:00"

    clock.advance(15 * 60)
    assert state.manual_active() is False, "到点即自动结束"

    assert state.take_report() == ["一条普通提醒"]
    assert state.pending_count() == 1, "未确认交付前不得排空"
    assert state.report_pending() is True
    assert state.take_report() == ["一条普通提醒"], "确认前反复取到同一份快照（重试语义）"

    state.ack_report()
    assert state.pending_count() == 0
    assert state.report_pending() is False
    assert state.take_report() is None, "同一轮不得汇报两遍"


def test_early_end_only_stops_the_timer_and_leaves_the_queue_intact():
    """提前结束只停计时：暂存与交付状态不动（汇报走与到期同一条链）。"""
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    state.start(60)
    state.remember("甲")
    state.remember("乙")

    assert state.end_now() is True
    assert state.manual_active() is False
    assert state.pending_count() == 2, "提前结束不得顺手排空（排空只发生在交付确认后）"

    assert state.take_report() == ["甲", "乙"]
    state.ack_report()
    assert state.pending_count() == 0
    assert state.end_now() is False, "已结束后再点提前结束 = 无操作"


def test_reselecting_duration_restarts_instead_of_stacking():
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    state.start(15)
    clock.advance(10 * 60)
    state.start(60)

    assert state.started_minutes() == 60
    assert state.remaining_seconds() == pytest.approx(60 * 60)
    clock.advance(55 * 60)
    assert state.manual_active() is True, "重新计时不得叠成 75 分钟"


def test_queue_limit_drops_oldest():
    state = quiet_mode.QuietState(queue_limit=3)
    for index in range(5):
        state.remember(f"提醒{index}")

    assert state.pending_count() == 3
    assert state.drain() == ["提醒2", "提醒3", "提醒4"], "溢出丢最旧"
    assert state.pending_count() == 0


def test_queue_ignores_blank_text():
    state = quiet_mode.QuietState()
    state.remember("   ")
    state.remember("")
    assert state.pending_count() == 0


def test_fullscreen_exit_ends_quiet_and_reports():
    state = quiet_mode.QuietState(clock=FakeClock())
    state.set_fullscreen(True)
    state.remember("全屏期间的提醒")

    state.set_fullscreen(False)
    assert state.take_report() == ["全屏期间的提醒"]
    state.ack_report()
    assert state.take_report() is None


def test_fullscreen_exit_before_the_first_tick_still_reports():
    """m5：入场即锁边沿——首个 1Hz tick 之前就退出全屏，那一轮也必须汇报得出去。

    边沿若只由轮询表第一拍去补，这里会留下「暂存 1 条、reports=0」的死状态。
    """
    state = quiet_mode.QuietState(clock=FakeClock())
    state.set_fullscreen(True)
    state.remember("一条普通提醒")

    state.set_fullscreen(False)   # 一拍都没跑过
    assert state.take_report() == ["一条普通提醒"], "入场边沿必须已经锁好"
    state.ack_report()


def test_manual_quiet_ended_before_the_first_tick_still_reports():
    """同一纪律的手动路径：开启后立刻提前结束也要报得出这一轮。"""
    state = quiet_mode.QuietState(clock=FakeClock())
    state.start(15)
    state.remember("一条普通提醒")
    state.end_now()

    assert state.take_report() == ["一条普通提醒"]
    state.ack_report()


def test_expiry_before_the_first_tick_still_reports():
    """钟直接跨过到期点（轮询表一拍都没跑，例如事件循环停摆）时同样要报得出去。

    这条钉的是 ``start()`` 自己的入场锁：靠"结算时再补边沿"的话，这里会留下
    暂存 1 条、永远不汇报的死状态。
    """
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    state.start(15)
    state.remember("一条普通提醒")
    clock.advance(15 * 60)

    assert state.take_report() == ["一条普通提醒"]
    state.ack_report()


def test_fullscreen_does_not_queue_when_flag_disabled():
    """``quiet_also_on_fullscreen`` 关掉时全屏不抑制，也就没有暂存与汇报。"""
    state = quiet_mode.QuietState()
    state.set_fullscreen(True)
    assert state.suppression_active(also_on_fullscreen=False) is False
    # 入场边沿与配置键无关（状态机不读配置）：开关关掉时结算拿到的是**空快照**，
    # 调用方据此确认"无事可报"，不会冒泡。这样配置读取仍然只发生在判据一处。
    assert state.take_report(also_on_fullscreen=False) == []
    assert state.pending_count() == 0


# ---------------------------------------------------------------- 2. 抑制真值表


class RecordingBubble:
    """记录 show_text / dismiss 的假气泡（真实提醒队列需要它）。"""

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Bubble(QObject):
            hidden_signal = Signal()

            def __init__(inner) -> None:
                super().__init__()
                inner.shown: list[dict] = []
                inner.dismiss_calls = 0

            def show_text(inner, text, anchor, duration_ms, *, pet_scale=None,
                          subtitle="", sticky=False, buttons=None,
                          title_first=False, width_locked=False):
                inner.shown.append({
                    "text": str(text), "sticky": bool(sticky),
                    "duration_ms": int(duration_ms), "buttons": buttons,
                    "subtitle": str(subtitle or ""),
                })

            def show_image(inner, *_args, **_kwargs):
                return True

            def reposition(inner, _anchor):
                pass

            def hide(inner):
                pass

            def isVisible(inner):
                return True

            def dismiss(inner):
                inner.dismiss_calls += 1

        self._bubble = _Bubble()

    def __getattr__(self, name):
        return getattr(self._bubble, name)


@pytest.fixture
def win(qapp, tmp_path):
    from tests.pet_window_fakes import make_pet_window

    window = make_pet_window(tmp_path)
    window._speech_bubble = RecordingBubble()
    yield window
    window.close()
    qapp.processEvents()


def _queue(window):
    return list(getattr(window, "_alert_queue", []) or [])


def _shown_texts(window) -> list[str]:
    return [item["text"] for item in window._speech_bubble.shown]


@pytest.mark.parametrize(
    "alert_type,shown_during_quiet",
    [
        ("watchdog", False),        # 默认分类 = 普通提醒（非穿透类）
        ("approval", True),         # 交互应答：压了会出事故
        ("question", True),
        ("interaction", True),
        ("lifecycle", True),
        ("task_complete", True),
        ("execution/failed", True),
        ("agent/request-error", True),
        ("balance", True),
    ],
)
def test_alert_truth_table_by_event_type(win, alert_type, shown_during_quiet):
    quiet_mode.shared_quiet_state().start(15)

    win.show_alert("一条提醒", duration_ms=6000, sticky=False, alert_type=alert_type)

    state = quiet_mode.shared_quiet_state()
    if shown_during_quiet:
        assert win._alert_current is not None, f"{alert_type} 必须穿透"
        assert state.pending_count() == 0
    else:
        assert win._alert_current is None, f"{alert_type} 应被免打扰拦下"
        assert state.pending_count() == 1


def test_interactive_alert_passes_through_with_buttons(win):
    """带按钮的审批（默认 sticky/优先级 0）必须连按钮一起穿透。"""
    quiet_mode.shared_quiet_state().start(15)

    win.show_alert("需要批准", buttons=[("同意", lambda: None), ("拒绝", lambda: None)],
                   sticky=True, alert_type="approval", alert_id="a1", priority=0)

    assert win._alert_current is not None
    assert win._speech_bubble.shown[-1]["buttons"] is not None


def test_agent_link_hard_failure_alert_passes_through_quiet(win):
    """硬失败提醒必须穿透免打扰：真实 manager → 真实 ``window_alerts.show_alert``。

    ``_on_execution_failed`` 此前没带 ``alert_type``，落进 ``show_alert`` 的默认值
    ``watchdog`` / priority=3 → 被免打扰门禁（``alert_survives_suppression``）当
    普通提醒压进暂存队列，用户在最需要看到报错的时刻什么都看不到，与「审批、报错
    照常提醒」的规格相矛盾。
    """
    from pet.agent_link import AgentLinkManager

    quiet_mode.shared_quiet_state().start(15)
    mgr = AgentLinkManager(win, win.cfg, min_interval=0.0)
    try:
        mgr._on_execution_failed(
            "dsh", {"failureType": "tool_failed", "errorCode": "E_BASH",
                    "errorMessage": "exit status 1"})
    finally:
        mgr.shutdown()

    assert win._alert_current is not None, "硬失败提醒必须穿透免打扰"
    assert quiet_mode.shared_quiet_state().pending_count() == 0, "不得进免打扰暂存队列"
    assert win._alert_current["alertType"] == "execution/failed"
    assert "失败" in _shown_texts(win)[-1], _shown_texts(win)


def test_fullscreen_equivalent_queues_ordinary_alert(win):
    """全屏等效免打扰（``quiet_also_on_fullscreen`` 默认开）同样暂存普通提醒。"""
    quiet_mode.shared_quiet_state().set_fullscreen(True)

    win.show_alert("硬失败", duration_ms=6000, sticky=False, alert_type="watchdog")

    assert win._alert_current is None
    assert quiet_mode.shared_quiet_state().pending_count() == 1


def test_fullscreen_equivalent_respects_disabled_flag(win):
    win.cfg.set("quiet_also_on_fullscreen", False)
    quiet_mode.shared_quiet_state().set_fullscreen(True)

    win.show_alert("卡住了", duration_ms=6000, sticky=False, alert_type="watchdog")

    assert win._alert_current is not None, "开关关掉后全屏不该抑制"
    assert quiet_mode.shared_quiet_state().pending_count() == 0


def test_quiet_drains_prequeued_ordinary_alerts(win):
    """免打扰开始**前**已入队的普通提醒：推进队列时转存，不在免打扰期间冒出来。"""
    win.show_alert("第一条", duration_ms=6000, sticky=False, alert_type="watchdog")
    win.show_alert("第二条", duration_ms=6000, sticky=False, alert_type="watchdog")
    assert len(_queue(win)) == 1

    quiet_mode.shared_quiet_state().start(15)
    win.hide_bubble()

    assert win._alert_current is None, "免打扰期间不得再冒出排队的普通提醒"
    state = quiet_mode.shared_quiet_state()
    assert state.pending_count() == 1, "转存进暂存队列的是排队那条"
    state.end_now()
    assert state.take_report() == ["第二条"]
    state.ack_report()


def test_quiet_does_not_touch_self_talk_or_link_bubbles(win):
    """自言自语/点击台词与联动状态气泡穿透：它们走 bubble_blocked/show_bubble。"""
    quiet_mode.shared_quiet_state().start(15)

    assert window_alerts.quiet_blocked(win) is True
    assert window_alerts.bubble_blocked(win) is False, "免打扰不得写进 bubble_blocked"

    assert win._show_self_talk_text("自言自语一句") is True
    assert _shown_texts(win)[-1] == "自言自语一句"

    # 联动状态气泡走 show_bubble（不经 bubble_blocked / quiet_blocked）
    win.show_bubble("联动：正在思考", duration_ms=3000)
    assert "联动：正在思考" in _shown_texts(win)


def test_report_quiet_summary_uses_one_bubble(win):
    assert window_alerts.report_quiet_summary(win, []) is False, "空暂存不打扰用户"
    assert window_alerts.report_quiet_summary(win, ["甲", "乙"]) is True

    text = _shown_texts(win)[-1]
    assert "2 条提醒" in text and "乙" in text


def test_end_quiet_mode_reports_and_clears(win):
    from pet.context_menus.shared import end_quiet_mode

    quiet_mode.shared_quiet_state().start(15)
    win.show_alert("卡住了", duration_ms=6000, sticky=False, alert_type="watchdog")
    win._alert_current = None  # 清掉展示态，让汇报气泡立刻上屏

    assert end_quiet_mode(win) == ["卡住了"]
    assert quiet_mode.shared_quiet_state().pending_count() == 0
    assert "1 条提醒" in _shown_texts(win)[-1]


def test_end_quiet_mode_keeps_queue_when_report_cannot_be_delivered(win, monkeypatch):
    """宿主交不出去时不得排空：暂存留着，轮询表稍后重试（先排空再丢弃禁止）。"""
    from pet.context_menus.shared import end_quiet_mode

    quiet_mode.shared_quiet_state().start(15)
    quiet_mode.shared_quiet_state().remember("卡住了")
    monkeypatch.setattr(window_alerts, "report_quiet_summary", lambda host, items: False)

    assert end_quiet_mode(win) == ["卡住了"]
    assert quiet_mode.shared_quiet_state().pending_count() == 1
    assert quiet_mode.shared_quiet_state().report_pending() is True


def test_overlay_host_dropped_summary_is_not_acked(tmp_path):
    """W2：overlay 形宿主（有 ``show_bubble`` 无 ``show_alert``）汇报被丢弃时不排空暂存。

    改前：``report_quiet_summary`` 的回退分支调用完气泡就 ``return True``，而壳的
    ``show_bubble`` 在不可见/飞行期/正挂提醒时直接丢弃且没有返回值——菜单拿到
    这个假的"成功"去 ``ack_report``，暂存被排空而用户什么都没看到（违反本批
    自己立的交付确认制）。
    """
    from pet.context_menus.shared import end_quiet_mode
    from pet.sprite_menu_facade import SpriteMenuFacade
    from tests.test_sprite_menu_facade import _make_shell

    shell, _lib = _make_shell(tmp_path)
    facade = SpriteMenuFacade(shell, shell.sprite)
    try:
        assert not hasattr(facade, "show_alert"), "前提：这一形宿主只有气泡面"
        assert shell.isVisible() is False, "前提：壳不可见（隐藏 / 全屏自动避让）"

        quiet_mode.shared_quiet_state().start(15)
        quiet_mode.shared_quiet_state().remember("攒了一条")

        assert end_quiet_mode(facade) == ["攒了一条"]
        state = quiet_mode.shared_quiet_state()
        assert state.pending_count() == 1, "没上屏就不许排空暂存"
        assert state.report_pending() is True, "轮询表据此保持重试"
        # 交付面本身的证据：这条宿主此刻确实吐不出气泡，回退分支必须读这个返回值
        assert facade.show_bubble("测试") is False, "这条气泡此刻上不了屏"
    finally:
        shell._delete_runtime_marker()


def test_overlay_host_delivered_summary_is_acked(tmp_path):
    """反面：气泡真的上屏时照常确认交付（不许因这次修复变成永远重试）。"""
    from pet.context_menus.shared import end_quiet_mode
    from pet.sprite_menu_facade import SpriteMenuFacade
    from tests.test_sprite_bubble import FakeBubble
    from tests.test_sprite_menu_facade import _make_shell

    shell, _lib = _make_shell(tmp_path)
    shell.sprite.bind_clip("idle1")
    shell.sprite._rebuild_pixmap()
    shell._bubble_follower.bubble = FakeBubble()
    shell.overlay.show()
    facade = SpriteMenuFacade(shell, shell.sprite)
    try:
        assert shell.isVisible() is True, "前提：壳可见"
        quiet_mode.shared_quiet_state().start(15)
        quiet_mode.shared_quiet_state().remember("攒了一条")

        assert end_quiet_mode(facade) == ["攒了一条"]
        state = quiet_mode.shared_quiet_state()
        assert state.pending_count() == 0, "确实上屏了才排空"
        assert state.report_pending() is False
        assert "1 条提醒" in shell._bubble_follower.bubble.texts[-1][0]
    finally:
        shell.overlay.hide()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 3. 接线


def _fake_pet(cfg):
    return type("Pet", (), {"cfg": cfg})()


def test_quiet_menu_lists_durations_and_shows_remaining(qapp, tmp_path):
    from PySide6.QtWidgets import QMenu

    from pet.config import Config
    from pet.context_menus.shared import add_quiet_mode_menu

    pet = _fake_pet(Config(base=tmp_path))
    root_menu = QMenu()
    submenu = add_quiet_mode_menu(root_menu, pet)
    labels = [action.text() for action in submenu.actions()]
    assert [label for label in labels if label] == [
        "15 分钟", "30 分钟（默认）", "1 小时", "2 小时", "提前结束",
    ]
    assert [a.isEnabled() for a in submenu.actions() if a.text() == "提前结束"] == [False]

    # 点击一档：立刻开始，时长取自该档
    submenu.actions()[2].trigger()
    state = quiet_mode.shared_quiet_state()
    assert state.manual_active() is True
    assert state.started_minutes() == 60

    # 开启期间菜单项显示剩余时间（子菜单标题 / 进行中 / 提前结束三处）
    active_sub = add_quiet_mode_menu(QMenu(root_menu), pet)
    assert active_sub.title().startswith("免打扰 · 剩余 ")
    active_labels = [action.text() for action in active_sub.actions()]
    assert "1 小时 · 进行中" in active_labels
    end_label = [label for label in active_labels if label.startswith("提前结束（剩余 ")][0]
    assert [a.isEnabled() for a in active_sub.actions() if a.text() == end_label] == [True]


def test_quiet_menu_end_action_reports(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    from pet.config import Config
    from pet.context_menus.shared import add_quiet_mode_menu

    pet = _fake_pet(Config(base=tmp_path))
    quiet_mode.shared_quiet_state().start(15)
    quiet_mode.shared_quiet_state().remember("攒了一条")
    reported: list = []
    monkeypatch.setattr(
        window_alerts, "report_quiet_summary", lambda host, items: reported.append(items)
    )

    root_menu = QMenu()
    submenu = add_quiet_mode_menu(root_menu, pet)
    end_action = [a for a in submenu.actions() if a.text().startswith("提前结束")][0]
    end_action.trigger()

    assert reported == [["攒了一条"]]
    assert quiet_mode.shared_quiet_state().manual_active() is False


def test_default_duration_setting_moves_the_menu_marker(qapp, tmp_path):
    from PySide6.QtWidgets import QMenu

    from pet.config import Config
    from pet.context_menus.shared import add_quiet_mode_menu

    cfg = Config(base=tmp_path)
    cfg.set("quiet_minutes_default", 120)

    root_menu = QMenu()
    labels = [a.text() for a in add_quiet_mode_menu(root_menu, _fake_pet(cfg)).actions()]
    assert "2 小时（默认）" in labels
    assert "30 分钟（默认）" not in labels


def test_window_menu_path_exposes_quiet_entry(qapp, tmp_path):
    """窗口侧菜单要有免打扰入口。

    overlay（facade）路径由 ``test_sprite_menu_parity.py`` 锁死——facade 直接复用
    context_menus 的同一套建造器，两条路径共用这一处菜单结构。
    旧版扁平布局退役后只剩这一条窗口侧路径（2026-10-08）。
    """
    from PySide6.QtWidgets import QMenu

    from pet.context_menu import populate_context_menu
    from tests.pet_window_fakes import make_pet_window

    window = make_pet_window(tmp_path, runtime_id="slot-quiet")
    menu = QMenu()
    populate_context_menu(menu, window)

    titles = [action.text() for action in menu.actions()]
    titles += [a.menu().title() for a in menu.actions() if a.menu() is not None]
    assert any(title.startswith("免打扰") for title in titles), "菜单缺少免打扰入口"
    window.close()
    qapp.processEvents()


def test_overlay_facade_host_can_build_quiet_menu(qapp, tmp_path):
    """overlay 的 ``SpriteMenuFacade`` 宿主也能建这个入口。

    facade 直接复用 context_menus 的同一套建造器（overlay 全量菜单树与窗口侧
    逐点一致由 ``test_sprite_menu_parity.py`` 锁死）；这里补一条**宿主形状**的
    钉子：建造器只读 ``pet.cfg``，不依赖任何 PetWindow 专有属性。
    """
    from PySide6.QtWidgets import QMenu

    from pet.config import Config
    from pet.context_menus.shared import add_quiet_mode_menu
    from pet.sprite_menu_facade import SpriteMenuFacade

    shell = type("Shell", (), {"_config": Config(base=tmp_path), "sprite": object()})()
    root_menu = QMenu()
    submenu = add_quiet_mode_menu(root_menu, SpriteMenuFacade(shell))

    assert [a.text() for a in submenu.actions() if a.text()][:4] == [
        "15 分钟", "30 分钟（默认）", "1 小时", "2 小时",
    ]


def test_menu_entry_is_a_submenu_with_durations(qapp, tmp_path):
    from PySide6.QtWidgets import QMenu

    from pet.context_menu import populate_context_menu
    from tests.pet_window_fakes import make_pet_window

    window = make_pet_window(tmp_path, runtime_id="slot-quiet-sub")
    menu = QMenu()
    populate_context_menu(menu, window)

    submenus = [a.menu() for a in menu.actions() if a.menu() is not None]
    quiet = [s for s in submenus if s.title().startswith("免打扰")]
    assert quiet, "菜单缺少免打扰子菜单"
    assert [a.text() for a in quiet[0].actions() if a.text()][:4] == [
        "15 分钟", "30 分钟（默认）", "1 小时", "2 小时",
    ]
    window.close()
    qapp.processEvents()


def test_appshell_fullscreen_broadcast_flips_quiet(qapp):
    """既有全屏避让广播（AppShell._on_shared_fullscreen）是等效免打扰的唯一来源。"""
    from pet.app import AppShell

    shell = type("Shell", (), {"_shared": object(), "_instances": []})()
    assert quiet_mode.shared_quiet_state().fullscreen() is False

    AppShell._on_shared_fullscreen(shell, True)
    assert quiet_mode.shared_quiet_state().fullscreen() is True
    assert quiet_mode.shared_quiet_state().suppression_active() is True

    AppShell._on_shared_fullscreen(shell, False)
    assert quiet_mode.shared_quiet_state().suppression_active() is False


# ------------------------------------------------- M2/M3：全屏往返 + 隐藏宿主
# 组合链此前是盲区：纯状态机与"可见的 PetWindow"各自测过，但「全屏进入 → 隐藏
# → 暂存 → 全屏退出 → 恢复显示 → 收到汇报」这条真实链路没人跑。它恰好踩在
# 「结算早于恢复可见性 / 代理由可见性筛目标」的交界上。


class _ProxyShell:
    """``MultiWindowProxy`` 的最小宿主：一只真实 PetWindow。"""

    def __init__(self, config, win) -> None:
        self.config = config
        self.instances = [type("Instance", (), {"win": win})()]


class _AppShellStub:
    """``AppShell._on_shared_fullscreen`` 的最小实例（只用到 _shared/_instances）。"""

    def __init__(self, win) -> None:
        self._shared = object()
        self._instances = [type("Instance", (), {"win": win})()]


class _HiddenHost:
    """隐藏窗的最小宿主形（节日/报时服务只读 ``cfg`` 与 ``isVisible``）。"""

    def __init__(self, cfg, *, visible: bool) -> None:
        self.cfg = cfg
        self._visible = bool(visible)

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt API
        return self._visible


def _make_proxy(win):
    from pet.multi_window_shared import MultiWindowProxy

    return MultiWindowProxy(_ProxyShell(win.cfg, win))


def _wire_quiet_pipeline(tmp_path, *, qapp):
    """真实 proxy + 真实 QuietTicker + 真实 SharedSubsystems 结束处理器。

    只装配，不替产品写逻辑：汇报链完全走 ``SharedSubsystems._on_quiet_ended``
    与 ``MultiWindowProxy``。
    """
    from pet.multi_window_shared import SharedSubsystems
    from tests.pet_window_fakes import make_pet_window

    win = make_pet_window(tmp_path, runtime_id="slot-quiet-chain")
    win._speech_bubble = RecordingBubble()
    proxy = _make_proxy(win)
    ticker = quiet_mode.QuietTicker(also_on_fullscreen=lambda: True, interval_ms=10)
    stub = type("Subs", (), {"proxy": proxy})()
    ticker.ended.connect(lambda items: SharedSubsystems._on_quiet_ended(stub, items))
    return win, proxy, ticker


def test_fullscreen_round_trip_delivers_report_after_window_restored(qapp, tmp_path):
    """全屏进 → 隐藏+暂存 → 全屏出 → 恢复显示 → 收到汇报（真实扇出顺序）。"""
    from pet.app import AppShell

    win, proxy, ticker = _wire_quiet_pipeline(tmp_path, qapp=qapp)
    shell = _AppShellStub(win)
    try:
        assert win.isVisible() is True

        # 进入全屏：真实扇出隐藏窗口（全屏避让），免打扰等效同时立闸
        AppShell._on_shared_fullscreen(shell, True)
        assert win.isVisible() is False
        assert quiet_mode.shared_quiet_state().suppression_active() is True

        # 全屏期间的普通提醒走共享代理入口：此刻窗口不可见，代理筛不到目标，
        # 提醒必须仍然进暂存队列（分流在可见性筛选之前）
        proxy.show_alert("卡住了", duration_ms=6000, sticky=False, alert_type="watchdog")
        assert win._alert_current is None
        assert quiet_mode.shared_quiet_state().pending_count() == 1

        # 退出全屏：窗口先恢复显示，随后才结算并汇报
        AppShell._on_shared_fullscreen(shell, False)
        assert win.isVisible() is True
        assert quiet_mode.shared_quiet_state().pending_count() == 0, "交付确认后才排空"
        assert "1 条提醒" in _shown_texts(win)[-1]
    finally:
        ticker.stop()
        win.close()
        qapp.processEvents()


def test_report_waits_for_a_presentation_target_instead_of_being_dropped(qapp, tmp_path):
    """免打扰结束但**没有任何可见目标**时：不得排空，目标一出现就补报。"""
    win, _proxy, ticker = _wire_quiet_pipeline(tmp_path, qapp=qapp)
    try:
        quiet_mode.shared_quiet_state().set_fullscreen(True)
        quiet_mode.shared_quiet_state().remember("卡住了")
        win.hide()
        qapp.processEvents()

        quiet_mode.shared_quiet_state().set_fullscreen(False)
        _pump(qapp, 0.2)
        assert quiet_mode.shared_quiet_state().pending_count() == 1, "无交付目标时不得排空"
        assert win._alert_current is None

        win.show()
        qapp.processEvents()
        _spin(qapp, lambda: quiet_mode.shared_quiet_state().pending_count() == 0)
        assert "1 条提醒" in _shown_texts(win)[-1]
    finally:
        ticker.stop()
        win.close()
        qapp.processEvents()


def test_hidden_proxy_queues_ordinary_alert_during_quiet(qapp, tmp_path):
    """M3：共享代理先筛可见窗——免打扰分流必须在筛选**之前**（隐藏窗也要进队列）。"""
    from tests.pet_window_fakes import make_pet_window

    win = make_pet_window(tmp_path, runtime_id="slot-hidden-proxy")
    win._speech_bubble = RecordingBubble()
    win.hide()
    qapp.processEvents()
    try:
        quiet_mode.shared_quiet_state().start(15)

        delivered = _make_proxy(win).show_alert(
            "卡住了", duration_ms=6000, sticky=False, alert_type="watchdog")

        assert delivered is True, "进了暂存队列也算接手"
        assert win._alert_current is None
        assert quiet_mode.shared_quiet_state().pending_count() == 1
    finally:
        win.close()
        qapp.processEvents()


def test_proxy_reports_no_delivery_when_no_visible_window(qapp, tmp_path):
    """没有可见目标时 ``show_alert`` 明说没交付（汇报据此保留重试）。"""
    from pet.config import Config

    proxy = _make_proxy(_HiddenHost(Config(base=tmp_path), visible=False))
    assert proxy.has_presentation_target() is False
    assert proxy.show_alert("一条", sticky=False, alert_type="watchdog") is False


def test_festival_reminder_on_hidden_host_is_queued_not_notified(qapp, tmp_path):
    """M3：隐藏宿主 + 免打扰 → 节日提醒进暂存队列，不再落到系统通知。"""
    from pet.config import Config
    from pet.festival_service import FestivalReminderService

    notified: list[tuple] = []
    win = _HiddenHost(Config(base=tmp_path), visible=False)
    app = type("App", (), {
        "win": win,
        "system_notify": lambda *args, **kwargs: notified.append(args),
    })()
    stub = type("Svc", (), {"_app": app, "BUBBLE_DURATION_MS": 12000})()

    quiet_mode.shared_quiet_state().start(15)
    FestivalReminderService._bubble(stub, "今天是某某节")

    assert notified == [], "免打扰期间连系统通知也要压住"
    state = quiet_mode.shared_quiet_state()
    assert state.pending_count() == 1
    state.end_now()
    assert state.take_report() == ["今天是某某节"]


def test_voice_chime_bubble_on_hidden_host_is_queued_not_notified(qapp, tmp_path):
    """M3：报时气泡同规（隐藏宿主 + 免打扰 → 暂存、不发通知；语音通道不受影响）。"""
    from pet.config import Config
    from pet.voice_chime_service import VoiceChimeService

    notified: list[tuple] = []
    win = _HiddenHost(Config(base=tmp_path), visible=False)
    app = type("App", (), {
        "win": win,
        "system_notify": lambda *args, **kwargs: notified.append(args),
    })()
    stub = type("Svc", (), {
        "_app": app, "_cfg": {"show_bubble": True}, "BUBBLE_DURATION_MS": 8000,
    })()

    quiet_mode.shared_quiet_state().start(15)
    VoiceChimeService._bubble(stub, "现在是下午三点")

    assert notified == []
    state = quiet_mode.shared_quiet_state()
    assert state.pending_count() == 1
    state.end_now()
    assert state.take_report() == ["现在是下午三点"]

def test_overlay_fullscreen_exit_settles_after_overlay_is_shown(qapp):
    """overlay 拓扑同序：退场结算必须晚于 ``overlay.show()``。

    用监听器在「免打扰状态翻转那一刻」读 overlay 可见性，直接钉住次序（只看
    最终状态的话，先后颠倒的实现也能蒙混过关）。
    """
    from PySide6.QtWidgets import QWidget

    from pet.overlay_shell import OverlayShell

    overlay = QWidget()
    overlay.resize(80, 80)
    overlay.show()
    shell = type("Shell", (), {
        "_auto_hidden": False,
        "overlay": overlay,
        "_hide_bubble_for_visibility": lambda self: None,
        "_set_all_clips_paused": lambda self, on: None,
        "_pause_shared_subsystems": lambda self: None,
        "_pause_warm_all": lambda self: None,
        "_note_sprite_visibility_changed": lambda self: None,
        "_restore_sticky_bubble": lambda self: None,
        "_resume_shared_subsystems": lambda self: None,
        "_resume_warm_all": lambda self: None,
    })()
    state = quiet_mode.shared_quiet_state()
    visible_at_settle: list[bool] = []

    def _record():
        visible_at_settle.append(overlay.isVisible())

    state.add_listener(_record)
    try:
        OverlayShell._on_fullscreen_changed(shell, True)
        assert state.fullscreen() is True
        assert overlay.isVisible() is False, "全屏避让照旧隐藏 overlay"

        visible_at_settle.clear()
        OverlayShell._on_fullscreen_changed(shell, False)
        assert overlay.isVisible() is True
        assert state.fullscreen() is False
        assert visible_at_settle == [True], "退场结算那一刻 overlay 必须已经恢复显示"
    finally:
        state.remove_listener(_record)
        overlay.close()
        qapp.processEvents()


def _acking_subscriber(state, reported: list, *, delivered=None):
    """把 ended 收成「汇报 → 确认交付」两段（生产里由 SharedSubsystems 做）。"""
    def _on_ended(items):
        reported.append(list(items))
        if delivered is None or delivered(items):
            state.ack_report()
    return _on_ended


def test_ticker_reports_queued_alerts_when_quiet_expires(qapp):
    """真实事件循环：到期后轮询表结算一次、汇报暂存提醒并自行停表。

    刻意**不**在测试里手工替产品推进结算：到期边沿由产品自己（入场锁定 +
    1Hz 轮询）建立，测试只观察 ``ended``。
    """
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    ticker = quiet_mode.QuietTicker(state=state, interval_ms=10)
    reported: list[list[str]] = []
    ticker.ended.connect(_acking_subscriber(state, reported))
    try:
        state.start(15)
        state.remember("攒了一条")
        assert ticker.active() is True, "免打扰一开始，轮询表就该跑起来"
        _pump(qapp, 0.1)                      # 让轮询表真的跑几拍
        assert reported == [], "免打扰期间不汇报"

        clock.advance(15 * 60)
        _spin(qapp, lambda: bool(reported))
        assert reported == [["攒了一条"]]
        assert state.pending_count() == 0, "确认交付后才排空"
        _spin(qapp, lambda: not ticker.active())
    finally:
        ticker.stop()


def test_ticker_keeps_retrying_until_delivery_is_confirmed(qapp):
    """交付未确认（没有可交付目标）时不清空、不停表，稍后仍然报得出去。

    这是「不得先 drain 后丢弃」的运行期保证：即便某一拍交付失败，提醒只是晚到。
    """
    clock = FakeClock()
    state = quiet_mode.QuietState(clock=clock)
    ticker = quiet_mode.QuietTicker(state=state, interval_ms=10)
    reported: list[list[str]] = []
    accept = {"value": False}
    ticker.ended.connect(_acking_subscriber(state, reported, delivered=lambda _items: accept["value"]))
    try:
        state.start(15)
        state.remember("攒了一条")
        clock.advance(15 * 60)
        _spin(qapp, lambda: bool(reported))
        _pump(qapp, 0.15)                     # 又跑若干拍：始终交付不成功
        assert state.pending_count() == 1, "未确认交付不得排空"
        assert ticker.active() is True, "未确认交付不得提前停表"

        accept["value"] = True                # 目标可用（例如桌宠恢复显示）
        _spin(qapp, lambda: state.pending_count() == 0)
        _spin(qapp, lambda: not ticker.active())
        assert set(reported[-1]) == {"攒了一条"}, "重试只重发同一份快照"
    finally:
        ticker.stop()


def test_ticker_does_not_run_when_quiet_never_started(qapp):
    ticker = quiet_mode.QuietTicker(state=quiet_mode.QuietState(clock=FakeClock()),
                                    interval_ms=10)
    try:
        assert ticker.active() is False
        _pump(qapp, 0.15)
        assert ticker.active() is False
    finally:
        ticker.stop()


def _pump(qapp, seconds: float) -> None:
    """空转事件循环若干秒（用于断言"什么都不该发生"）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def _spin(qapp, done, *, timeout: float = 5.0) -> None:
    """宽预算事件循环（CI 慢 runner 是本地数倍慢；宁可多等，不赌时序）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if done():
            return
        time.sleep(0.005)
    assert done(), "等待超时：条件在预算内未满足"


def test_settings_rows_live_in_automation_domain_and_round_trip(qapp, tmp_path):
    from pet import settings_quiet
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
        page = dialog.pages.widget(labels.index("自动化与联动"))
        for key in settings_quiet.ROW_KEYS:
            row = dialog.findChild(SettingRow, f"settingRow_{key}")
            assert row is not None, f"缺少设置行 {key}"
            assert page.isAncestorOf(row), f"{key} 不在「自动化与联动」域里"

        dialog.quiet_minutes_select.setCurrentIndex(
            dialog.quiet_minutes_select.findData(120)
        )
        dialog.quiet_also_on_fullscreen_check.setChecked(False)
        settings_quiet.save_quiet_settings(dialog)

        assert dialog.config.get("quiet_minutes_default") == 120
        assert dialog.config.get("quiet_also_on_fullscreen") is False
        dialog.config.save()
        assert Config(base=tmp_path).get("quiet_minutes_default") == 120
    finally:
        dialog.close()
        qapp.processEvents()


def test_config_normalizes_dirty_quiet_values(tmp_path):
    """手改 config.json：档位归一到合法档、字符串布尔不得误开（#129 同规）。"""
    from pet.config import Config

    cfg = Config(base=tmp_path)
    cfg.set("quiet_minutes_default", "abc")
    assert cfg.data["quiet_minutes_default"] == config_mod.DEFAULT_QUIET_MINUTES
    cfg.set("quiet_minutes_default", 45)
    assert cfg.data["quiet_minutes_default"] in config_mod.QUIET_MINUTE_CHOICES
    cfg.set("quiet_also_on_fullscreen", "false")
    assert cfg.data["quiet_also_on_fullscreen"] is False


def test_config_defaults_match_quiet_mode_constants(tmp_path):
    """默认值 dict 的免打扰两键与 feature 常量必须一致（防止两处漂移）。"""
    from pet.config import Config

    cfg = Config(base=tmp_path)
    assert cfg.get("quiet_minutes_default") == config_mod.DEFAULT_QUIET_MINUTES
    assert cfg.get("quiet_also_on_fullscreen") is config_mod.DEFAULT_QUIET_ALSO_ON_FULLSCREEN


# ---------------------------------------------------------------- 子宠页作用域
def test_quiet_controls_are_editable_on_the_child_page(qapp, tmp_path):
    """子宠页：免打扰两键可编辑，保存落**主配置**（消费方读的就是它）。

    这两个键不在 ``PER_SLOT_SETTING_KEYS`` 里（子宠页曾经置灰：写了 slot 文件、
    运行期读主配置 = 死开关）。独立设置进程现在按 key 分流到主配置，故恢复可用。
    """
    import json

    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        for control in (master.quiet_minutes_select,
                        master.quiet_also_on_fullscreen_check,
                        child.quiet_minutes_select,
                        child.quiet_also_on_fullscreen_check):
            assert control.isEnabled() is True
            assert control.toolTip() != "仅主桌宠可设置"

        child.quiet_minutes_select.setCurrentIndex(
            child.quiet_minutes_select.findData(120))
        child.quiet_also_on_fullscreen_check.setChecked(True)
        assert child._write_config() is True
        # #271：空身份会话的配置面指向 config-slot-0.json（SlotRoutedConfig 的 path），
        # 免打扰两键是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["quiet_minutes_default"] == 120, "子宠页改的键必须真正生效"
        assert on_disk["quiet_also_on_fullscreen"] is True
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        qapp.processEvents()
