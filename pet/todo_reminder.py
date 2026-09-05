# -*- coding: utf-8 -*-
"""待办提醒：纯逻辑决策 + 条目存储 + GUI 线程调度服务。

模块顶部为纯函数与纯文件 IO（零 Qt），可在无 Qt 环境导入测试；
TodoReminderService 在 __init__ 内惰性导入 Qt（同 pet/proactive.py 约定），
QTimer 全程运行在 GUI 线程，无跨线程对象。

条目持久化在独立文件 todo_items[-<instance_id>].json（config.dir 下），
偏好 todo_reminder_enabled / todo_reminder_lead_minutes 在 config.json。

提醒语义：每条启用条目有 lead / due 两个触发档；触发窗口
[触发时刻, +grace] 内产生提醒并盖戳（持久化，防重启/唤醒重复），
出窗静默盖戳跳过（防休眠唤醒轰炸）；once 条目过 due+grace 自动归档
（enabled=False，面板置灰可见）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

TODO_ITEMS_VERSION = 1
TODO_ITEMS_LIMIT = 100
TODO_TITLE_LIMIT = 80
TODO_KINDS = ("once", "daily")
DEFAULT_GRACE_MINUTES = 10
DEFAULT_TODO_TIME = "09:00"

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ------------------------------------------------------------ 纯函数

def _normalize_hhmm(value) -> str:
    """归一化 HH:MM；接受单位数小时（9:05 → 09:05），非法返回空串。"""
    match = _HHMM_RE.match(str(value or "").strip())
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _normalize_iso_date(value) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def new_todo_item(title, kind, time_text, date_text: str = "") -> dict:
    """构造一条新待办（面板新建入口用）；非法字段按默认值钳制。"""
    kind = str(kind or "").strip()
    item = {
        "id": uuid.uuid4().hex,
        "title": str(title or "").strip()[:TODO_TITLE_LIMIT],
        "kind": kind if kind in TODO_KINDS else "once",
        "time": _normalize_hhmm(time_text) or DEFAULT_TODO_TIME,
        "date": "",
        "enabled": True,
        "fired_lead_slot": None,
        "fired_due_slot": None,
    }
    if item["kind"] == "once":
        item["date"] = _normalize_iso_date(date_text) or date.today().isoformat()
    return item


def clean_todo_items(value) -> list[dict]:
    """清洗待办条目列表：非清单/非字典/空标题丢弃，字段逐项钳制，
    id 去重（冲突重生成），上限 TODO_ITEMS_LIMIT 条。"""
    if not isinstance(value, list):
        return []
    items: list[dict] = []
    seen_ids: set[str] = set()
    for raw in value[:TODO_ITEMS_LIMIT]:
        if not isinstance(raw, dict):
            continue
        item = new_todo_item(raw.get("title"), raw.get("kind"),
                             raw.get("time"), raw.get("date", ""))
        if not item["title"]:
            continue
        item["id"] = str(raw.get("id") or item["id"]).strip()[:64] or item["id"]
        if item["id"] in seen_ids:
            item["id"] = uuid.uuid4().hex
        seen_ids.add(item["id"])
        item["enabled"] = raw["enabled"] if isinstance(raw.get("enabled"), bool) else True
        for key in ("fired_lead_slot", "fired_due_slot"):
            slot = raw.get(key)
            item[key] = slot if isinstance(slot, str) and slot else None
        items.append(item)
    return items


def _fire_datetimes(item: dict, lead_minutes: int, now: datetime):
    """返回 (lead_dt|None, due_dt|None)。daily 以 now 当天组合；once 用条目
    日期（缺失/非法时返回 (None, None)，视为不可触发）。"""
    time_text = _normalize_hhmm(item.get("time"))
    if not time_text:
        return None, None
    if item.get("kind") == "daily":
        day = now.date()
    else:
        day_text = _normalize_iso_date(item.get("date"))
        if not day_text:
            return None, None
        day = date.fromisoformat(day_text)
    hour, minute = (int(part) for part in time_text.split(":"))
    due = datetime(day.year, day.month, day.day, hour, minute)
    lead = due - timedelta(minutes=lead_minutes) if lead_minutes > 0 else None
    return lead, due


def advance_todo_state(items, prefs, now: datetime, *,
                       grace_minutes: int = DEFAULT_GRACE_MINUTES):
    """推进待办触发状态，返回 (fires, new_items)。

    - prefs = {"enabled": bool, "lead_minutes": int}；总开关关闭时原样返回；
    - 触发窗口 [触发时刻, +grace] 内产生 fire 并盖戳；出窗静默盖戳；
    - once 条目过 due+grace 自动归档（enabled=False）。
    fires 元素：{"id", "title", "time", "phase"}，phase ∈ {"lead", "due"}。
    """
    if not bool(prefs.get("enabled", True)):
        return [], list(items)
    try:
        lead_minutes = max(0, int(prefs.get("lead_minutes", 0) or 0))
    except (TypeError, ValueError):
        lead_minutes = 0
    grace = timedelta(minutes=max(0, int(grace_minutes)))
    fires: list[dict] = []
    new_items: list[dict] = []
    for raw in items:
        item = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(item, dict) or not item.get("enabled"):
            new_items.append(item)
            continue
        lead_dt, due_dt = _fire_datetimes(item, lead_minutes, now)
        time_text = _normalize_hhmm(item.get("time")) or str(item.get("time") or "")
        for phase, fire_dt, slot_key in (
            ("lead", lead_dt, "fired_lead_slot"),
            ("due", due_dt, "fired_due_slot"),
        ):
            if fire_dt is None or now < fire_dt:
                continue
            slot = f"{fire_dt.date().isoformat()}T{time_text}#{phase}"
            if item.get(slot_key) == slot:
                continue
            item[slot_key] = slot
            if now <= fire_dt + grace:
                fires.append({
                    "id": item["id"],
                    "title": item["title"],
                    "time": item["time"],
                    "phase": phase,
                })
        if (item.get("kind") == "once" and due_dt is not None
                and now > due_dt + grace):
            item["enabled"] = False
        new_items.append(item)
    return fires, new_items


def summarize_next(items, now: datetime) -> str:
    """下一条未触发待办的人类可读摘要（气泡/面板用）；无则空串。"""
    best = None  # (due_dt, item)
    for raw in items:
        item = raw if isinstance(raw, dict) else {}
        if not item.get("enabled"):
            continue
        time_text = _normalize_hhmm(item.get("time"))
        if not time_text:
            continue
        hour, minute = (int(part) for part in time_text.split(":"))
        if item.get("kind") == "daily":
            day = now.date()
            today_slot = f"{day.isoformat()}T{time_text}#due"
            if item.get("fired_due_slot") == today_slot:
                day += timedelta(days=1)
            due = datetime(day.year, day.month, day.day, hour, minute)
        else:
            _, due = _fire_datetimes(item, 0, now)
        if due is None or due <= now:
            continue
        if best is None or due < best[0]:
            best = (due, item)
    if best is None:
        return ""
    due, item = best
    day = due.date()
    # daily 且下次就在今天：以“每天”开头；其余（含 daily 已触发顺延到明天）
    # 走通用的 今天/明天/M月D日 前缀。
    if item.get("kind") == "daily" and day == now.date():
        return f"每天 {item['time']} {item['title']}"
    if day == now.date():
        day_text = "今天"
    elif day == now.date() + timedelta(days=1):
        day_text = "明天"
    else:
        day_text = f"{day.month}月{day.day}日"
    return f"{day_text} {item['time']} {item['title']}"


# ------------------------------------------------------------ 条目存储

def todo_items_path(config_dir, instance_id: str = "") -> Path:
    """条目文件路径：多开实例跟随 config 文件的 -<instance_id> 命名惯例。"""
    name = f"todo_items-{instance_id}.json" if instance_id else "todo_items.json"
    return Path(config_dir) / name


class TodoStore:
    """todo_items.json 的读写：读侧清洗容错，写侧原子替换（同 Config.save）。"""

    def __init__(self, path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict):
            return []
        return clean_todo_items(raw.get("items"))

    def save(self, items) -> bool:
        """原子落盘；IO 失败记日志并清理临时文件（盖戳丢失仅可能在宽限窗
        内导致一次重复提醒，优于让异常从 QTimer slot 炸出）。"""
        payload = {"version": TODO_ITEMS_VERSION, "items": clean_todo_items(items)}
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)
            return True
        except OSError:
            logger.exception("待办条目写入失败：%s", self.path)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False


# ------------------------------------------------------------ 调度服务

class TodoReminderService:
    """待办提醒调度服务（PetApp 持有，GUI 线程）。

    同 ProactiveScreenWatcher：本类不继承 QObject（Qt 在 __init__ 内惰性
    导入，模块顶层保持无 Qt），持有无主 QTimer，由 PetApp 持有引用保证
    生命周期。tick → 纯函数 advance_todo_state → 桌宠可见且设置未抑制时
    冒气泡，否则走 app.system_notify 桌面通知（受
    system_notifications_enabled 全局门控，关闭时该分支静默）；盖戳/归档
    仅在条目变化时落盘。
    """

    TICK_INTERVAL_MS = 30_000
    BUBBLE_DURATION_MS = 8000

    def __init__(self, app) -> None:
        from PySide6.QtCore import QTimer

        self._app = app
        config = getattr(app, "config", None)
        self._store = TodoStore(todo_items_path(
            getattr(config, "dir", Path(".")),
            getattr(config, "instance_id", "") or "",
        ))
        self._items: list[dict] = []
        self._prefs: dict = {"enabled": True, "lead_minutes": 0}
        self._notify_enabled = True
        self._timer = QTimer()
        self._timer.setInterval(self.TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self.apply_config()
        # 启动即推进一次：登录自启等场景下宽限窗内的提醒不必等首个 30s tick
        self._on_tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def apply_config(self) -> None:
        """重读偏好与条目（设置保存、面板保存后调用）。"""
        config = getattr(self._app, "config", None)
        try:
            lead = int(config.get("todo_reminder_lead_minutes", 5) or 0)
        except (TypeError, ValueError, AttributeError):
            lead = 5
        self._prefs = {
            "enabled": bool(config.get("todo_reminder_enabled", True)) if config else True,
            "lead_minutes": max(0, min(60, lead)),
        }
        # 桌面通知分支与既有调用方（chat 等）同规：受全局通知开关门控
        self._notify_enabled = (
            bool(config.get("system_notifications_enabled", True)) if config else True
        )
        self._items = self._store.load()

    def items(self) -> list[dict]:
        """当前条目（面板打开时以服务内副本为准，保存路径仍走 store）。"""
        return list(self._items)

    def set_items(self, items: list[dict], *, save: bool = True) -> None:
        """面板保存入口：整体替换条目并立即落盘。"""
        self._items = clean_todo_items(items)
        if save:
            self._store.save(self._items)

    def _on_tick(self, now: datetime | None = None) -> None:
        if not self._prefs.get("enabled"):
            return
        fires, new_items = advance_todo_state(
            self._items, self._prefs, now or datetime.now()
        )
        if new_items != self._items:
            self._items = new_items
            self._store.save(new_items)
        for fire in fires:
            self._notify_fire(fire)

    def _notify_fire(self, fire: dict) -> None:
        app = self._app
        text = f"⏰ 待办提醒：{fire['title']}（{fire['time']}）"
        win = getattr(app, "win", None)
        if win is not None and win.isVisible() and not self._bubble_suppressed():
            win.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS)
            return
        if not self._notify_enabled:
            return
        notify = getattr(app, "system_notify", None)
        if callable(notify):
            notify("待办提醒", f"{fire['title']}（{fire['time']}）",
                   on_click=getattr(app, "open_todo_panel", None))

    def _bubble_suppressed(self) -> bool:
        """设置窗口打开期间暂停气泡（与 PetApp._update_bubble_suppression_for_settings
        同一判定来源：抑制状态本就是 app 层根据对话框存在性设置的）。"""
        app = self._app
        return (
            getattr(app, "modern_settings_dialog", None) is not None
            or getattr(app, "chat_settings_dialog", None) is not None
        )
