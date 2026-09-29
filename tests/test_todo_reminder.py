# -*- coding: utf-8 -*-
"""待办提醒：偏好键、纯函数决策逻辑（advance_todo_state）与条目存储测试。

纯函数全部显式注入 now，不依赖墙钟；TodoStore 走 tmp_path 真实文件 IO。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from pet.config import Config
from pet.todo_reminder import (
    DEFAULT_GRACE_MINUTES,
    TODO_ITEMS_LIMIT,
    TODO_ITEMS_VERSION,
    TodoReminderService,
    TodoStore,
    advance_todo_state,
    clean_todo_items,
    new_todo_item,
    summarize_next,
    todo_items_path,
)

NOW = datetime(2026, 9, 4, 9, 0, 0)


def _prefs(lead: int = 5, enabled: bool = True) -> dict:
    return {"enabled": enabled, "lead_minutes": lead}


def _daily(time: str = "10:00", **kw) -> dict:
    item = new_todo_item("站会", "daily", time)
    item.update(kw)
    return item


def _once(date_text: str = "2026-09-04", time: str = "10:00", **kw) -> dict:
    item = new_todo_item("发版", "once", time, date_text)
    item.update(kw)
    return item


# ------------------------------------------------------------ new/clean

def test_new_todo_item_shape():
    item = new_todo_item(" 买奶 ", "daily", "9:05")
    assert item["title"] == "买奶"
    assert item["kind"] == "daily"
    assert item["time"] == "09:05"
    assert item["date"] == ""
    assert item["enabled"] is True
    assert item["fired_lead_slot"] is None
    assert item["fired_due_slot"] is None
    assert item["id"]


def test_new_once_item_keeps_date():
    item = new_todo_item("发版", "once", "10:00", "2026-09-05")
    assert item["kind"] == "once"
    assert item["date"] == "2026-09-05"


def test_clean_drops_legacy_per_item_reminder_lead():
    items = clean_todo_items([
        {"title": "项目会议", "kind": "once", "time": "10:00",
         "date": "2026-09-05", "reminder_lead_minutes": 30},
        {"title": "无效提前量", "kind": "once", "time": "10:00",
         "date": "2026-09-05", "reminder_lead_minutes": 999},
    ])

    assert "reminder_lead_minutes" not in items[0]
    assert "reminder_lead_minutes" not in items[1]


def test_store_roundtrip_drops_legacy_per_item_reminder_lead(tmp_path):
    store = TodoStore(todo_items_path(tmp_path))
    legacy_item = {
        "title": "项目会议", "kind": "once", "time": "10:00",
        "date": "2026-09-05", "reminder_lead_minutes": 30,
    }

    assert store.save([legacy_item]) is True
    assert "reminder_lead_minutes" not in store.load()[0]


def test_clean_drops_garbage_and_clamps():
    raw = [
        "junk",
        42,
        {"title": "", "kind": "daily", "time": "10:00"},
        {"title": "x" * 200, "kind": "weird", "time": "9:5", "date": "2026-13-40"},
        {"title": "正常", "kind": "daily", "time": "09:30"},
    ]
    items = clean_todo_items(raw)
    assert len(items) == 2
    clamped = items[0]
    assert len(clamped["title"]) == 80
    assert clamped["kind"] == "once"        # 非法 kind 归 once
    assert clamped["time"] == "09:00"       # 非法时间回退默认
    assert clamped["date"] == date.today().isoformat()  # 非法日期回退今天
    assert items[1]["kind"] == "daily"
    assert items[1]["time"] == "09:30"
    assert items[1]["date"] == ""


def test_clean_dedupes_ids_and_caps_limit():
    raw = [
        {"title": f"t{i}", "kind": "daily", "time": "10:00", "id": "same"}
        for i in range(TODO_ITEMS_LIMIT + 5)
    ]
    items = clean_todo_items(raw)
    assert len(items) == TODO_ITEMS_LIMIT
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids)


def test_clean_preserves_slots_and_enabled():
    items = clean_todo_items([
        {"title": "A", "kind": "daily", "time": "10:00",
         "enabled": False, "fired_due_slot": "2026-09-04T10:00#due"},
    ])
    assert items[0]["enabled"] is False
    assert items[0]["fired_due_slot"] == "2026-09-04T10:00#due"
    # 非布尔的 enabled 一律回到默认 True（不猜字符串语义）
    items2 = clean_todo_items([{"title": "B", "kind": "daily", "time": "10:00", "enabled": "false"}])
    assert items2[0]["enabled"] is True


def test_clean_preserves_valid_daily_snooze():
    item = clean_todo_items([{
        "title": "站会", "kind": "daily", "time": "10:00",
        "snooze_date": "2026-09-05", "snooze_time": "11:00",
        "snooze_fired_lead_slot": "2026-09-05T11:00#lead",
    }])[0]

    assert item["snooze_date"] == "2026-09-05"
    assert item["snooze_time"] == "11:00"
    assert item["snooze_fired_lead_slot"] == "2026-09-05T11:00#lead"


# ------------------------------------------------------------ advance

def test_no_fire_before_lead():
    fires, new_items = advance_todo_state(
        [_daily()], _prefs(lead=5), datetime(2026, 9, 4, 9, 54)
    )
    assert fires == []
    assert new_items[0]["fired_lead_slot"] is None


def test_lead_fire_then_due_fire():
    fires, stamped = advance_todo_state(
        [_daily()], _prefs(lead=5), datetime(2026, 9, 4, 9, 55)
    )
    assert [f["phase"] for f in fires] == ["lead"]
    assert stamped[0]["fired_lead_slot"] == "2026-09-04T10:00#lead"
    fires2, _ = advance_todo_state(stamped, _prefs(lead=5), datetime(2026, 9, 4, 10, 0))
    assert [f["phase"] for f in fires2] == ["due"]


def test_fire_payload_fields():
    items = [_daily()]
    fires, _ = advance_todo_state(items, _prefs(lead=5), datetime(2026, 9, 4, 9, 55))
    fire = fires[0]
    assert fire["id"] == items[0]["id"]
    assert fire["title"] == "站会"
    assert fire["time"] == "10:00"
    assert fire["phase"] == "lead"


def test_no_lead_fire_when_lead_zero():
    fires, _ = advance_todo_state([_daily()], _prefs(lead=0), datetime(2026, 9, 4, 9, 55))
    assert fires == []


def test_legacy_item_lead_does_not_override_global_reminder_preference():
    legacy_meeting = _once(time="10:00", reminder_lead_minutes=30)

    fires_before, _ = advance_todo_state(
        [legacy_meeting], _prefs(lead=5), datetime(2026, 9, 4, 9, 54)
    )
    fires_at_global_lead, _ = advance_todo_state(
        [legacy_meeting], _prefs(lead=5), datetime(2026, 9, 4, 9, 55)
    )

    assert fires_before == []
    assert [fire["phase"] for fire in fires_at_global_lead] == ["lead"]
    assert fires_at_global_lead[0]["lead_minutes"] == 5


def test_missed_beyond_grace_stamps_silently():
    late = datetime(2026, 9, 4, 10, DEFAULT_GRACE_MINUTES + 1)
    fires, stamped = advance_todo_state([_daily()], _prefs(lead=5), late)
    assert fires == []
    assert stamped[0]["fired_lead_slot"] == "2026-09-04T10:00#lead"
    assert stamped[0]["fired_due_slot"] == "2026-09-04T10:00#due"
    assert stamped[0]["enabled"] is True  # daily 不归档


def test_restart_no_double_fire():
    _, stamped = advance_todo_state([_daily()], _prefs(lead=5), datetime(2026, 9, 4, 10, 0))
    fires, _ = advance_todo_state(stamped, _prefs(lead=5), datetime(2026, 9, 4, 10, 1))
    assert fires == []


def test_daily_rolls_over_next_day():
    _, stamped = advance_todo_state([_daily()], _prefs(lead=0), datetime(2026, 9, 4, 10, 0))
    fires, stamped2 = advance_todo_state(stamped, _prefs(lead=0), datetime(2026, 9, 5, 10, 0))
    assert [f["phase"] for f in fires] == ["due"]
    assert stamped2[0]["fired_due_slot"] == "2026-09-05T10:00#due"


def test_daily_snooze_is_one_shot_and_keeps_regular_schedule():
    item = _daily(
        snooze_date="2026-09-05",
        snooze_time="11:00",
        snooze_fired_lead_slot=None,
        snooze_fired_due_slot=None,
    )
    item["fired_lead_slot"] = "2026-09-05T10:00#lead"
    item["fired_due_slot"] = "2026-09-04T10:00#due"

    fires, stamped = advance_todo_state(
        [item], _prefs(lead=5), datetime(2026, 9, 5, 10, 0)
    )
    assert [(fire["phase"], fire["deferred"], fire["time"]) for fire in fires] == [
        ("due", False, "10:00"),
    ]

    fires, stamped = advance_todo_state(
        stamped, _prefs(lead=5), datetime(2026, 9, 5, 10, 55)
    )
    assert [(fire["phase"], fire["deferred"], fire["time"]) for fire in fires] == [
        ("lead", True, "11:00"),
    ]
    assert stamped[0]["snooze_fired_lead_slot"] == "2026-09-05T11:00#lead"

    _, finished = advance_todo_state(
        stamped, _prefs(lead=5), datetime(2026, 9, 5, 11, 11)
    )
    assert finished[0]["snooze_date"] is None
    assert finished[0]["snooze_time"] is None
    assert finished[0]["fired_due_slot"] == "2026-09-05T10:00#due"


def test_once_archives_after_due_plus_grace():
    fires, stamped = advance_todo_state(
        [_once()], _prefs(lead=0), datetime(2026, 9, 4, 10, DEFAULT_GRACE_MINUTES + 1)
    )
    assert fires == []
    assert stamped[0]["enabled"] is False
    # daily 同时刻不归档
    _, daily_items = advance_todo_state(
        [_daily()], _prefs(lead=0), datetime(2026, 9, 5, 10, DEFAULT_GRACE_MINUTES + 1)
    )
    assert daily_items[0]["enabled"] is True


def test_once_fires_inside_window_without_archiving():
    fires, stamped = advance_todo_state([_once()], _prefs(lead=0), datetime(2026, 9, 4, 10, 0))
    assert [f["phase"] for f in fires] == ["due"]
    assert stamped[0]["enabled"] is True


def test_disabled_item_skipped():
    fires, _ = advance_todo_state(
        [_daily(enabled=False)], _prefs(), datetime(2026, 9, 4, 10, 0)
    )
    assert fires == []


def test_master_disabled_is_noop():
    items = [_daily()]
    fires, new_items = advance_todo_state(items, _prefs(enabled=False), datetime(2026, 9, 4, 10, 0))
    assert fires == []
    assert new_items == items


# ------------------------------------------------------------ summarize

def test_summarize_next_daily_today():
    text = summarize_next([_daily(time="23:00")], NOW)
    assert "23:00" in text
    assert "站会" in text


def test_summarize_next_daily_already_fired_shows_tomorrow():
    item = _daily(time="09:00")
    item["fired_due_slot"] = "2026-09-04T09:00#due"
    text = summarize_next([item], datetime(2026, 9, 4, 9, 30))
    assert "明天" in text


def test_summarize_next_once_and_empty():
    text = summarize_next([_once(date_text="2026-09-06")], NOW)
    assert "9月6日" in text
    assert summarize_next([], NOW) == ""


# ------------------------------------------------------------ TodoStore

def test_store_path_naming():
    base = Path("X:/whatever")
    assert todo_items_path(base).name == "todo_items.json"
    assert todo_items_path(base, "abc123").name == "todo_items-abc123.json"


def test_store_roundtrip(tmp_path):
    store = TodoStore(todo_items_path(tmp_path))
    items = clean_todo_items([_daily(), _once()])
    store.save(items)
    assert store.load() == items


def test_store_missing_and_corrupt(tmp_path):
    store = TodoStore(todo_items_path(tmp_path))
    assert store.load() == []
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == []


def test_store_payload_version(tmp_path):
    store = TodoStore(tmp_path / "todo_items.json")
    store.save([])
    payload = json.loads((tmp_path / "todo_items.json").read_text(encoding="utf-8"))
    assert payload["version"] == TODO_ITEMS_VERSION


# ------------------------------------------------------------ Config 偏好键

def test_todo_prefs_defaults(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get("todo_reminder_enabled") is True
    assert cfg.get("todo_reminder_lead_minutes") == 5
    assert cfg.get("todo_reminder_require_ack") is False


def test_todo_prefs_roundtrip_and_clamp(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("todo_reminder_enabled", "false")  # 字符串布尔防误开
    cfg.set("todo_reminder_lead_minutes", 999)
    cfg.set("todo_reminder_require_ack", True)
    cfg.save()
    cfg2 = Config(base=tmp_path)
    assert cfg2.get("todo_reminder_enabled") is False
    assert cfg2.get("todo_reminder_lead_minutes") == 60
    assert cfg2.get("todo_reminder_require_ack") is True
    cfg2.set("todo_reminder_lead_minutes", -3)
    assert cfg2.get("todo_reminder_lead_minutes") == 0


# ------------------------------------------------------------ 服务分发（GUI 线程）

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWin:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        self.bubbles: list[str] = []
        self.alerts: list[dict] = []
        self.resolved_alerts: list[str] = []

    def isVisible(self) -> bool:
        return self.visible

    def show_bubble(self, text, duration_ms=3200, subtitle=None):
        self.bubbles.append(text)

    def show_alert(self, text, **kwargs):
        self.alerts.append({"text": text, **kwargs})

    def resolve_alert(self, alert_id):
        self.resolved_alerts.append(alert_id)


class _FakeApp:
    def __init__(self, tmp_path, visible: bool = True) -> None:
        self.config = Config(base=tmp_path)
        self.win = _FakeWin(visible)
        self.notifies: list[tuple] = []
        self.modern_settings_dialog = None
        self.chat_settings_dialog = None
        self.panel_opens = 0

    def system_notify(self, title, message, *, on_click=None, duration_ms=5000):
        self.notifies.append((title, message, on_click))

    def open_todo_panel(self):
        self.panel_opens += 1


def _due_item_in_store(config_dir, time_text="10:00", kind="daily") -> dict:
    store = TodoStore(todo_items_path(config_dir))
    item = new_todo_item("站会", kind, time_text)
    store.save([item])
    return item


def test_service_tick_bubbles_when_visible(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    _due_item_in_store(app.config.dir)
    service = TodoReminderService(app)
    service.apply_config()
    # 10:06 已出 lead 窗口（lead=5 → 9:55~10:05），只应触发 due 档
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert len(app.win.bubbles) == 1
    assert "站会" in app.win.bubbles[0]
    assert app.notifies == []
    # 盖戳落盘后不重复提醒
    service._on_tick(now=datetime(2026, 9, 4, 10, 6, 30))
    assert len(app.win.bubbles) == 1


def test_acknowledgement_alert_snoozes_once_todo_to_next_free_slot(tmp_path, monkeypatch):
    _qapp()
    app = _FakeApp(tmp_path)
    app.config.set("todo_reminder_require_ack", True)
    item = new_todo_item("发版", "once", "10:00", "2026-09-04")
    TodoStore(todo_items_path(app.config.dir)).save([item])
    panel_refreshes = []
    app.todo_panel = type("Panel", (), {"reload_items": lambda _self: panel_refreshes.append(True)})()
    service = TodoReminderService(app)
    service.apply_config()
    original_snooze = service.snooze_fire
    monkeypatch.setattr(
        service, "snooze_fire",
        lambda fire: original_snooze(fire, now=datetime(2026, 9, 4, 10, 0)),
    )

    service._on_tick(now=datetime(2026, 9, 4, 9, 55))

    assert len(app.win.alerts) == 1
    alert = app.win.alerts[0]
    buttons = dict(alert["buttons"])
    assert set(buttons) == {"确定", "推迟"}
    buttons["推迟"]()

    stored = TodoStore(todo_items_path(app.config.dir)).load()[0]
    assert (stored["date"], stored["time"]) == ("2026-09-04", "11:00")
    assert stored["fired_lead_slot"] is None
    assert stored["fired_due_slot"] is None
    assert app.win.resolved_alerts == [alert["alert_id"]]
    assert panel_refreshes == [True]


def test_repeated_phases_share_alert_id_and_old_action_cannot_move_edited_todo(
    tmp_path, monkeypatch
):
    _qapp()
    app = _FakeApp(tmp_path)
    app.config.set("todo_reminder_require_ack", True)
    item = new_todo_item("发版", "once", "10:00", "2026-09-04")
    TodoStore(todo_items_path(app.config.dir)).save([item])
    service = TodoReminderService(app)
    service.apply_config()
    original_snooze = service.snooze_fire
    monkeypatch.setattr(
        service, "snooze_fire",
        lambda fire: original_snooze(fire, now=datetime(2026, 9, 4, 10, 0)),
    )

    service._on_tick(now=datetime(2026, 9, 4, 9, 55))
    first = app.win.alerts[0]
    service._on_tick(now=datetime(2026, 9, 4, 10, 0))
    second = app.win.alerts[1]
    assert first["alert_id"] == second["alert_id"]

    edited = service.items()[0]
    edited["time"] = "12:00"
    service.set_items([edited])
    dict(first["buttons"])["推迟"]()

    stored = TodoStore(todo_items_path(app.config.dir)).load()[0]
    assert stored["time"] == "12:00"
    assert app.win.resolved_alerts == []


def test_acknowledgement_alert_snoozes_daily_item_without_changing_recurrence(
    tmp_path, monkeypatch
):
    _qapp()
    app = _FakeApp(tmp_path)
    app.config.set("todo_reminder_require_ack", True)
    item = new_todo_item("站会", "daily", "10:00")
    TodoStore(todo_items_path(app.config.dir)).save([item])
    service = TodoReminderService(app)
    service.apply_config()
    original_snooze = service.snooze_fire
    monkeypatch.setattr(
        service, "snooze_fire",
        lambda fire: original_snooze(fire, now=datetime(2026, 9, 4, 10, 0)),
    )

    service._on_tick(now=datetime(2026, 9, 4, 9, 55))
    dict(app.win.alerts[0]["buttons"])["推迟"]()

    stored = TodoStore(todo_items_path(app.config.dir)).load()[0]
    assert (stored["kind"], stored["time"]) == ("daily", "10:00")
    assert (stored["snooze_date"], stored["snooze_time"]) == ("2026-09-04", "11:00")
    assert stored["fired_due_slot"] == "2026-09-04T10:00#due"
    assert len(app.win.resolved_alerts) == 1


def test_service_tick_notifies_when_hidden(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path, visible=False)
    _due_item_in_store(app.config.dir)
    service = TodoReminderService(app)
    service.apply_config()
    # 10:06 已出 lead 窗口，只应触发 due 档
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert app.win.bubbles == []
    assert len(app.notifies) == 1
    _title, message, on_click = app.notifies[0]
    assert "站会" in message
    assert on_click == app.open_todo_panel


def test_hidden_todo_notification_shows_global_lead(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path, visible=False)
    app.config.set("todo_reminder_lead_minutes", 30)
    meeting = new_todo_item(
        "项目会议", "once", "10:00", "2026-09-04"
    )
    TodoStore(todo_items_path(app.config.dir)).save([meeting])
    service = TodoReminderService(app)
    service.apply_config()

    service._on_tick(now=datetime(2026, 9, 4, 9, 30))

    assert len(app.notifies) == 1
    assert app.notifies[0][1] == "项目会议（10:00，提前30分钟提醒）"


def test_service_tick_notifies_when_settings_open(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    _due_item_in_store(app.config.dir)
    app.modern_settings_dialog = object()
    service = TodoReminderService(app)
    service.apply_config()
    # 10:06 已出 lead 窗口，只应触发 due 档
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert app.win.bubbles == []
    assert len(app.notifies) == 1


def test_service_notify_gated_by_system_notifications(tmp_path):
    """桌宠隐藏 + 系统通知总开关关闭 → 桌面通知分支静默（与 chat 调用方同规）。"""
    _qapp()
    app = _FakeApp(tmp_path, visible=False)
    _due_item_in_store(app.config.dir)
    app.config.set("system_notifications_enabled", False)
    service = TodoReminderService(app)
    service.apply_config()
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert app.win.bubbles == []
    assert app.notifies == []


def test_service_master_off_is_silent(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path)
    _due_item_in_store(app.config.dir)
    app.config.set("todo_reminder_enabled", False)
    service = TodoReminderService(app)
    service.apply_config()
    service._on_tick(now=datetime(2026, 9, 4, 10, 0))
    assert app.win.bubbles == []
    assert app.notifies == []


def test_service_lead_pref_reaches_engine(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path)
    _due_item_in_store(app.config.dir)
    app.config.set("todo_reminder_lead_minutes", 15)
    service = TodoReminderService(app)
    service.apply_config()
    service._on_tick(now=datetime(2026, 9, 4, 9, 45))
    assert len(app.win.bubbles) == 1


def test_service_once_archive_persists(tmp_path):
    _qapp()
    app = _FakeApp(tmp_path)
    item = _due_item_in_store(app.config.dir, kind="once")
    item["date"] = "2026-09-04"
    TodoStore(todo_items_path(app.config.dir)).save([item])
    service = TodoReminderService(app)
    service.apply_config()
    service._on_tick(now=datetime(2026, 9, 4, 10, DEFAULT_GRACE_MINUTES + 1))
    assert app.win.bubbles == []
    assert TodoStore(todo_items_path(app.config.dir)).load()[0]["enabled"] is False


# ------------------------------------------------------------ PetApp 接线

def test_petapp_creates_service_and_wires_callback(tmp_path):
    from pet.app import AppShell

    qapp = _qapp()
    shell = AppShell(qapp, Config(tmp_path))
    assert shell.todo_service is not None

    class _Win:
        pass

    win = _Win()
    shell.instance._wire_window(win)
    # 回调经 _slot_wrap 包了一层（日志槽位），断言已接线且可调用即可
    assert callable(win.on_open_todo_panel)


def test_petapp_settings_finish_applies_todo_prefs(tmp_path, monkeypatch):
    import pet.app as app_mod
    from pet.app import AppShell, PetInstance

    qapp = _qapp()
    monkeypatch.setattr(app_mod, "_mac_set_dock_icon_visible", lambda *a, **k: None)
    owner = PetInstance.__new__(PetInstance)
    owner.shell = AppShell.__new__(AppShell)
    owner.modern_settings_dialog = None
    owner.chat_settings_dialog = None
    owner.win = None
    owner.config = Config(tmp_path)
    owner.shell.config = owner.config
    owner.shell._apply_balance_timer = lambda: None
    owner.shell._sync_dynamic_island = lambda: None
    owner._refresh_chat_windows = lambda: None
    # 本分支 todo_service 是进程级单例，挂在 AppShell 上
    owner.shell.todo_service = TodoReminderService(owner.shell)
    owner.shell.todo_panel = None
    owner.config.set("todo_reminder_enabled", False)
    PetInstance._modern_settings_finished(owner, 0)
    # Phase 1 门控：待办总开关关闭且无面板打开时，服务对象应被释放
    assert owner.shell.todo_service is None


# ------------------------------------------------------------ 管理面板

def _make_panel(tmp_path):
    from pet.todo_panel import TodoPanelDialog

    _qapp()
    app = _FakeApp(tmp_path)
    service = TodoReminderService(app)
    app.todo_service = service
    service.apply_config()
    return app, TodoPanelDialog(app)


def test_panel_add_persists_item(tmp_path):
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QTimeEdit

    _app, panel = _make_panel(tmp_path)
    title_edit = panel.findChild(QLineEdit, "todoTitleEdit")
    save = panel.findChild(QPushButton, "todoSaveButton")
    assert title_edit is not None and save is not None
    assert not save.isEnabled()  # 空标题时保存不可用

    title_edit.setText("买奶")
    kind = panel.findChild(QComboBox, "todoKindSelect")
    kind.setCurrentIndex(kind.findText("每天"))
    time_edit = panel.findChild(QTimeEdit, "todoTimeEdit")
    time_edit.setTime(QTime(8, 30))
    save.click()

    items = _app.todo_service.items()
    assert len(items) == 1
    assert items[0]["title"] == "买奶"
    assert items[0]["kind"] == "daily"
    assert items[0]["time"] == "08:30"
    assert TodoStore(todo_items_path(_app.config.dir)).load()[0]["title"] == "买奶"


def test_panel_toggle_and_delete(tmp_path):
    from PySide6.QtWidgets import QCheckBox, QPushButton

    app, panel = _make_panel(tmp_path)
    item = new_todo_item("站会", "daily", "10:00")
    app.todo_service.set_items([item])
    panel.reload_items()

    toggle = panel.findChild(QCheckBox, f"todoEnable_{item['id']}")
    assert toggle is not None and toggle.isChecked()
    toggle.click()
    assert app.todo_service.items()[0]["enabled"] is False

    panel.findChild(QPushButton, f"todoDelete_{item['id']}").click()
    assert app.todo_service.items() == []


def test_panel_edit_updates_item_and_clears_slots(tmp_path):
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QPushButton, QTimeEdit

    app, panel = _make_panel(tmp_path)
    item = new_todo_item("站会", "daily", "10:00")
    item["fired_due_slot"] = "2026-09-04T10:00#due"
    app.todo_service.set_items([item])
    panel.reload_items()

    panel.begin_edit(item["id"])
    time_edit = panel.findChild(QTimeEdit, "todoTimeEdit")
    time_edit.setTime(QTime(11, 0))
    panel.findChild(QPushButton, "todoSaveButton").click()

    items = app.todo_service.items()
    assert len(items) == 1
    assert items[0]["id"] == item["id"]
    assert items[0]["time"] == "11:00"
    assert items[0]["fired_due_slot"] is None  # 改时间后重新武装
    assert TodoStore(todo_items_path(app.config.dir)).load()[0]["time"] == "11:00"


def test_panel_badge_shows_one_time_daily_snooze(tmp_path):
    app, panel = _make_panel(tmp_path)
    item = _daily(
        snooze_date="2026-09-05",
        snooze_time="11:00",
    )

    assert panel._badge_text(item) == "推迟至 9月5日 11:00"


# ------------------------------------------------------------ 设置页偏好

def test_settings_roundtrip_todo_prefs(tmp_path):
    """设置对话框读写 todo_reminder 两键，且两行归入「待办提醒」section。"""
    from PySide6.QtWidgets import QLabel

    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow, SettingsSection

    _qapp()
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        assert dialog.todo_reminder_check.isChecked() is True
        assert dialog.todo_reminder_lead_spin.value() == 5
        assert dialog.todo_reminder_require_ack_check.isChecked() is False

        dialog.todo_reminder_check.setChecked(False)
        dialog.todo_reminder_lead_spin.setValue(88)  # 超 60 → spin 钳制
        dialog.todo_reminder_require_ack_check.setChecked(True)
        assert dialog._write_config() is True

        def section_title(row):
            parent = row.parentWidget()
            while parent is not None:
                if isinstance(parent, SettingsSection):
                    for label in parent.findChildren(QLabel):
                        if label.objectName() == "sectionTitle":
                            return label.text()
                parent = parent.parentWidget()
            return ""

        row = dialog.findChild(SettingRow, "settingRow_todo_reminder_enabled")
        assert row is not None
        assert section_title(row) == "待办提醒"  # 显式 claim，不落「待分类」
        ack_row = dialog.findChild(SettingRow, "settingRow_todo_reminder_require_ack")
        assert ack_row is not None
        assert section_title(ack_row) == "待办提醒"
    finally:
        dialog.deleteLater()

    reloaded = Config(cfg_root)
    assert reloaded.get("todo_reminder_enabled") is False
    assert reloaded.get("todo_reminder_lead_minutes") == 60
    assert reloaded.get("todo_reminder_require_ack") is True
