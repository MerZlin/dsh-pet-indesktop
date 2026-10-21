# -*- coding: utf-8 -*-
"""待办提醒：偏好键、纯函数决策逻辑（advance_todo_state）与条目存储测试。

纯函数全部显式注入 now，不依赖墙钟；TodoStore 走 tmp_path 真实文件 IO。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from pet.config import Config
from pet.todo.reminder import (
    DEFAULT_GRACE_MINUTES,
    TODO_ITEMS_LIMIT,
    TODO_ITEMS_VERSION,
    TodoReminderService,
    TodoStore,
    advance_todo_state,
    clean_todo_items,
    fire_text,
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


def test_daily_lead_window_crosses_midnight():
    """daily 00:02 + lead 5：当天 23:57 就该发提前提醒，不等到 00:00 补发。

    旧行为：daily 无条件用 ``now.date()`` 组 due，23:57 算到的是**当天已过**的
    00:02（提前档 23:57 也在昨天），两档都走"出窗静默盖戳" → 真正该提醒的次日
    00:02 提前档要等过了午夜才补发，迟了整整 3 分钟（用户看到的提醒已经贴着正点）。
    """
    item = _daily("00:02")
    fires, stamped = advance_todo_state(
        [item], _prefs(lead=5), datetime(2026, 9, 4, 23, 57)
    )
    assert [f["phase"] for f in fires] == ["lead"], "23:57 必须发次日 00:02 的提前提醒"
    # 提前档的盖戳键按触发时刻（23:57）落在当天，午夜后重算仍是同一个键
    assert stamped[0]["fired_lead_slot"] == "2026-09-04T00:02#lead"
    assert stamped[0]["fired_due_slot"] is None, "正点档还早，不得提前盖戳"

    # 00:00 不重复发（同一个提前档只盖一次戳）
    midnight_fires, _ = advance_todo_state(stamped, _prefs(lead=5), datetime(2026, 9, 5, 0, 0))
    assert midnight_fires == []
    # due 正点照常
    due_fires, due_stamped = advance_todo_state(
        stamped, _prefs(lead=5), datetime(2026, 9, 5, 0, 2)
    )
    assert [f["phase"] for f in due_fires] == ["due"]
    assert due_stamped[0]["fired_due_slot"] == "2026-09-05T00:02#due"


def test_daily_grace_window_survives_midnight():
    """每日 23:59 + 10 分钟宽限：跨过午夜后**昨天那次**的正点档仍在窗内。

    旧行为：daily 只按 ``now.date()`` 组当天（必要时次日）那一次 occurrence，昨天
    那次压根不在候选里——电脑 23:59 睡着、00:00 醒来时只晚一分钟，这一条被算成
    "今天 23:59 还没到"，用户收不到任何提醒也没有补发；00:10 之后（出窗）才该静默。
    """
    cases = {
        datetime(2026, 9, 5, 0, 0): True,    # 刚过午夜，正点档仍在窗内
        datetime(2026, 9, 5, 0, 4): True,    # 宽限内（23:59 + 10 分钟 = 00:09）
        datetime(2026, 9, 5, 0, 10): False,  # 出窗
    }
    for now, in_window in cases.items():
        fires, stamped = advance_todo_state([_daily("23:59")], _prefs(lead=0), now)
        assert [f["phase"] for f in fires] == (["due"] if in_window else []), now
        assert stamped[0]["fired_due_slot"] == (
            "2026-09-04T23:59#due" if in_window else None), now

    # 窗内发过之后不重复（跨午夜重启同一档只提醒一次）
    _, stamped = advance_todo_state([_daily("23:59")], _prefs(lead=0), datetime(2026, 9, 5, 0, 0))
    again, _ = advance_todo_state(stamped, _prefs(lead=0), datetime(2026, 9, 5, 0, 5))
    assert again == []
    # 当天晚上那一次照旧（昨天的宽限不影响今天）
    tonight, tonight_items = advance_todo_state(
        stamped, _prefs(lead=0), datetime(2026, 9, 5, 23, 59)
    )
    assert [f["phase"] for f in tonight] == ["due"]
    assert tonight_items[0]["fired_due_slot"] == "2026-09-05T23:59#due"


def test_daily_lead_zero_does_not_use_next_day():
    """lead=0 的 daily 不受跨午夜取次日影响：当天正点档过期仍按原语义静默盖戳。"""
    fires, stamped = advance_todo_state([_daily("00:02")], _prefs(lead=0), datetime(2026, 9, 4, 23, 57))
    assert fires == []
    assert stamped[0]["fired_due_slot"] == "2026-09-04T00:02#due"


def test_once_cross_midnight_behavior_unchanged():
    """once 不受跨午夜改动影响：用条目自己的日期，过期即静默盖戳。"""
    fires, stamped = advance_todo_state(
        [_once("2026-09-04", "00:02")], _prefs(lead=5), datetime(2026, 9, 4, 23, 57)
    )
    assert fires == []
    assert stamped[0]["fired_lead_slot"] == "2026-09-03T00:02#lead"
    assert stamped[0]["fired_due_slot"] == "2026-09-04T00:02#due"


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


def test_summarize_next_daily_cross_midnight_shows_tomorrow():
    """跨午夜的每日待办：「下一条」必须指向次日那次，不能整段空着。

    23:57 看每日 00:02：当天那次凌晨就过了（戳可能已落盘、也可能还没），旧实现
    只认 fired_due_slot，两种情形下都 continue 掉，面板"下一条"空白——而这正是
    次日那次提前提醒刚发出的时刻，用户最需要看到它。
    """
    assert "明天" in summarize_next([_daily(time="00:02")], datetime(2026, 9, 4, 23, 57))
    # 当天那次的 due 戳已落盘：同样指向次日
    stamped = _daily(time="00:02")
    stamped["fired_due_slot"] = "2026-09-04T00:02#due"
    assert "明天" in summarize_next([stamped], datetime(2026, 9, 4, 23, 57))
    # 当天那次还没到点：照常显示今天（"每天"前缀）
    assert summarize_next([_daily(time="23:00")], datetime(2026, 9, 4, 22, 0)).startswith("每天")


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


def test_todo_prefs_roundtrip_and_clamp(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("todo_reminder_enabled", "false")  # 字符串布尔防误开
    cfg.set("todo_reminder_lead_minutes", 999)
    cfg.save()
    cfg2 = Config(base=tmp_path)
    assert cfg2.get("todo_reminder_enabled") is False
    assert cfg2.get("todo_reminder_lead_minutes") == 60
    cfg2.set("todo_reminder_lead_minutes", -3)
    assert cfg2.get("todo_reminder_lead_minutes") == 0


# ------------------------------------------------------------ 服务分发（GUI 线程）

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWin:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        # 气泡位被审批提醒占着：普通气泡让路（真宿主此时返回 False）
        self.occupied = False
        self.bubbles: list[str] = []

    def isVisible(self) -> bool:
        return self.visible

    def show_bubble(self, text, duration_ms=3200, subtitle=None) -> bool:
        if self.occupied:
            return False
        self.bubbles.append(text)
        return True


class _FakeApp:
    def __init__(self, tmp_path, visible: bool = True) -> None:
        self.config = Config(base=tmp_path)
        self.win = _FakeWin(visible)
        self.notifies: list[tuple] = []
        self.modern_settings_dialog = None
        self.chat_settings_dialog = None
        self.panel_opens = 0
        self.channel = _Channel()

    def ensure_audio_channel(self):
        return self.channel

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


def test_service_daily_cross_midnight_lead_fires_before_midnight(tmp_path):
    """服务级（真调度服务 + 真条目存储 + 真气泡路径）：23:57 就该冒提前提醒。

    纯函数用例钉的是判定，这条钉的是它落到用户眼前的那一步：23:57 的 tick 冒泡、
    盖戳落盘，过午夜后重启也不重复（提前档只发一次），正点档照常。
    """
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    store = TodoStore(todo_items_path(app.config.dir))
    store.save([new_todo_item("吃药", "daily", "00:02")])
    app.config.set("todo_reminder_lead_minutes", 5)
    service = TodoReminderService(app)
    service.apply_config()

    service._on_tick(now=datetime(2026, 9, 4, 23, 57))
    assert len(app.win.bubbles) == 1, "当天 23:57 必须冒提前提醒（不是等到 00:00）"
    assert "吃药" in app.win.bubbles[0]
    assert store.load()[0]["fired_lead_slot"] == "2026-09-04T00:02#lead", "盖戳需落盘"

    # 过午夜重启：同一个提前档不得补发第二次
    service2 = TodoReminderService(app)
    service2.apply_config()
    service2._on_tick(now=datetime(2026, 9, 5, 0, 0))
    assert len(app.win.bubbles) == 1
    # 正点照常提醒
    service2._on_tick(now=datetime(2026, 9, 5, 0, 2))
    assert len(app.win.bubbles) == 2


def test_service_daily_grace_across_midnight_still_bubbles(tmp_path):
    """服务级：每日 23:59 的待办在次日 00:00 醒来时照旧冒泡（不是静默丢）。

    纯函数用例钉的是判定，这条钉的是它落到用户眼前那一步：昨天那次的正点档仍在
    宽限窗内，tick 必须冒泡并把昨天的槽位盖戳落盘（重启后不再重复）。
    """
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    store = TodoStore(todo_items_path(app.config.dir))
    store.save([new_todo_item("关灯", "daily", "23:59")])
    app.config.set("todo_reminder_lead_minutes", 0)
    service = TodoReminderService(app)
    service.apply_config()

    service._on_tick(now=datetime(2026, 9, 5, 0, 0))
    assert len(app.win.bubbles) == 1, "跨午夜宽限内的正点档必须冒泡"
    assert "关灯" in app.win.bubbles[0]
    assert store.load()[0]["fired_due_slot"] == "2026-09-04T23:59#due", "盖戳需落盘"

    # 重启后同一档不补发第二次；出窗（00:10 之后）什么都不冒
    service2 = TodoReminderService(app)
    service2.apply_config()
    service2._on_tick(now=datetime(2026, 9, 5, 0, 5))
    service2._on_tick(now=datetime(2026, 9, 5, 0, 10))
    assert len(app.win.bubbles) == 1


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


def test_service_notifies_when_alert_occupies_bubble(tmp_path):
    """审批提醒占着气泡位时，到点待办必须改道系统通知，不能静默吞掉。

    旧行为：``show_bubble`` 在有提醒展示时让路并返回 False，而调度侧不看返回值
    → 戳已盖、气泡没出、通知也没发，这条提醒永久消失（语音关掉时用户完全无感）。
    """
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    app.win.occupied = True
    _due_item_in_store(app.config.dir)
    service = TodoReminderService(app)
    service.apply_config()

    service._on_tick(now=datetime(2026, 9, 4, 10, 6))

    assert app.win.bubbles == [], "宿主让路，气泡确实没上屏（前置）"
    assert len(app.notifies) == 1, "气泡没上屏就必须改道桌面通知"
    assert "站会" in app.notifies[0][1]
    assert app.notifies[0][2] == app.open_todo_panel, "通知点击仍落到待办面板"
    # 盖戳语义不变：同一档仍只提醒一次，不搞重复轰炸
    service._on_tick(now=datetime(2026, 9, 4, 10, 6, 30))
    assert len(app.notifies) == 1


def test_service_warns_when_bubble_blocked_and_notify_off(tmp_path, caplog):
    """气泡被占且桌面通知总开关关闭：至少留一条 warning，不许静默无痕。"""
    _qapp()
    app = _FakeApp(tmp_path, visible=True)
    app.win.occupied = True
    _due_item_in_store(app.config.dir)
    app.config.set("system_notifications_enabled", False)
    service = TodoReminderService(app)
    service.apply_config()

    with caplog.at_level(logging.WARNING, logger="pet.todo.reminder"):
        service._on_tick(now=datetime(2026, 9, 4, 10, 6))

    assert app.win.bubbles == [] and app.notifies == []
    assert any("站会" in record.getMessage() for record in caplog.records), "未交付也不能静默"


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
    from pet.todo.panel import TodoPanelDialog

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


def _block_item_writes(config_dir: Path) -> Path:
    """让条目落盘真实失败：把 todo_items.json 做成**目录**（原子替换必报错）。

    不打桩 ``TodoStore.save``——失败必须来自真实文件系统（Windows 上
    ``os.replace`` 撞目录是 WinError 5，POSIX 是 IsADirectoryError，两者都是
    产品代码捕获的那类 OSError）。
    """
    path = todo_items_path(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    """捕获 QMessageBox.warning（conftest 已全局置 no-op，这里要断言内容）。"""
    from PySide6.QtWidgets import QMessageBox

    calls: list[tuple[str, str]] = []

    def _warning(parent, title, text, *args, **kwargs):
        calls.append((str(title), str(text)))
        return None

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_warning))
    return calls


def test_panel_save_failure_keeps_editor_and_draft(tmp_path, monkeypatch):
    """条目落盘失败：编辑器不关、草稿还在、明确提示未保存。

    旧行为：``set_items`` 不检查 ``TodoStore.save`` 的 False，面板照常
    ``close_editor()`` + ``reload_items()``，用户看到的是"保存成功"的样子；
    下一次 ``apply_config()``（设置保存/重启）用磁盘覆盖内存，条目凭空消失。
    契约对齐设置保存失败（modern_settings_dialog：不关窗 + 明确告知未落盘）。
    """
    calls = _capture_warnings(monkeypatch)
    app, panel = _make_panel(tmp_path)
    blocked = _block_item_writes(app.config.dir)

    panel.begin_add()
    panel._title_edit.setText("买奶")
    panel._save_btn.click()

    assert not panel._editor_card.isHidden(), "保存失败时编辑器必须留在原地（草稿不丢）"
    assert panel._title_edit.text() == "买奶", "草稿不得被清掉"
    assert calls and calls[-1][0] == "保存失败", f"必须给出可见失败提示，实际 {calls}"
    assert str(blocked) in calls[-1][1], "提示要告诉用户条目文件在哪"
    # 内存态与磁盘同真值：不留"看着已保存、下次重读就消失"的幽灵条目
    assert app.todo_service.items() == []
    app.todo_service.apply_config()
    assert app.todo_service.items() == []


def test_panel_save_retry_after_failure_writes_once(tmp_path, monkeypatch):
    """障碍解除后重试：草稿一键落盘，且只落一条（失败未提交不留残留）。"""
    calls = _capture_warnings(monkeypatch)
    app, panel = _make_panel(tmp_path)
    blocked = _block_item_writes(app.config.dir)

    panel.begin_add()
    panel._title_edit.setText("买奶")
    panel._save_btn.click()
    assert app.todo_service.items() == []
    failed_calls = len(calls)

    blocked.rmdir()  # 修好磁盘侧（换成可写文件路径）
    panel._save_btn.click()

    assert len(calls) == failed_calls, "落盘成功不该再提示失败"
    assert panel._editor_card.isHidden(), "保存成功后编辑器照常收起"
    assert [i["title"] for i in app.todo_service.items()] == ["买奶"]
    assert [i["title"] for i in TodoStore(todo_items_path(app.config.dir)).load()] == ["买奶"]


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

        dialog.todo_reminder_check.setChecked(False)
        dialog.todo_reminder_lead_spin.setValue(88)  # 超 60 → spin 钳制
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
    finally:
        dialog.deleteLater()

    reloaded = Config(cfg_root)
    assert reloaded.get("todo_reminder_enabled") is False
    assert reloaded.get("todo_reminder_lead_minutes") == 60


# ------------------------------------------------------------ 到点语音播报


class _Channel:
    """假音频通道（即语音报时服务）：只记录被要求播报的文本。"""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str, log_tag: str = "") -> bool:
        self.spoken.append(text)
        return True


def _speaking_service(tmp_path, *, speak: bool, visible: bool = True):
    app = _FakeApp(tmp_path, visible=visible)
    app.config.set("todo_reminder_speak", speak)
    _due_item_in_store(app.config.dir)
    service = TodoReminderService(app)
    service.apply_config()
    return app, service


def test_fire_text_is_the_single_source_for_bubble_and_speech():
    """气泡与语音共用同一个 builder：两处文案不可能漂移。"""
    fire = {"id": "x", "title": "站会", "time": "10:00", "phase": "due"}
    assert fire_text(fire) == "⏰ 待办提醒：站会（10:00）"


def test_tick_stays_silent_when_speak_is_off(tmp_path):
    """默认关（主动打扰型能力）：气泡照旧，一声都不出。"""
    _qapp()
    app, service = _speaking_service(tmp_path, speak=False)
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert len(app.win.bubbles) == 1
    assert app.channel.spoken == []


def test_tick_speaks_exactly_the_bubble_text(tmp_path):
    _qapp()
    app, service = _speaking_service(tmp_path, speak=True)
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert app.win.bubbles == ["⏰ 待办提醒：站会（10:00）"]
    assert app.channel.spoken == app.win.bubbles, "语音与气泡必须逐字同源"


def test_quiet_mode_neither_speaks_nor_catches_up(tmp_path):
    """免打扰拦下的提醒不出声、也不在结束后补播（过期提醒不该突然出声）。"""
    from pet import quiet_mode

    _qapp()
    app, service = _speaking_service(tmp_path, speak=True)
    state = quiet_mode.shared_quiet_state()
    state.start(15)
    service._on_tick(now=datetime(2026, 9, 4, 10, 6))
    assert app.win.bubbles == []
    assert app.channel.spoken == []
    assert state.pending_count() == 1

    state.end_now()
    service._on_tick(now=datetime(2026, 9, 4, 10, 6, 30))
    assert app.channel.spoken == [], "免打扰期间那条不补播"


def test_todo_speak_pref_default_off_and_string_false_stays_off(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get("todo_reminder_speak") is False
    cfg.set("todo_reminder_speak", "false")  # 字符串布尔防误开
    assert cfg.get("todo_reminder_speak") is False
    cfg.set("todo_reminder_speak", True)
    cfg.save()
    assert Config(base=tmp_path).get("todo_reminder_speak") is True


def test_settings_save_keeps_the_audio_channel_wanted(tmp_path):
    """设置页保存写回 → 壳仍需要音频通道；漏接 ``_chime_wanted`` 就是死开关。"""
    from pet.app import AppShell
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    qapp = _qapp()
    cfg_root = tmp_path / "appdata"
    dialog = ModernSettingsDialog(Config(cfg_root), include_ai=False)
    try:
        row = dialog.findChild(SettingRow, "settingRow_todo_reminder_speak")
        assert row is not None, "「待办提醒」分区必须有语音播报一行"
        assert row.control.isChecked() is False, "出厂默认必须是关"
        assert "免打扰" in row.control.accessibleDescription(), "说明必须写明免打扰期间不出声"
        row.control.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    shell = AppShell(qapp, Config(cfg_root), enable_chat=False)
    assert shell.config.get("todo_reminder_speak") is True
    assert shell._chime_wanted() is True, "开了待办语音就必须留住音频通道"
    shell.config.set("todo_reminder_speak", False)
    assert shell._chime_wanted() is False, "关掉后不再无谓常驻一条通道"
