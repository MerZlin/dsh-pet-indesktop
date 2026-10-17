# -*- coding: utf-8 -*-
"""待办提醒：纯逻辑决策 + 条目存储 + GUI 线程调度服务。

模块顶部为纯函数与纯文件 IO（零 Qt），可在无 Qt 环境导入测试；
TodoReminderService 在 __init__ 内惰性导入 Qt（同 pet/proactive.py 约定），
QTimer 全程运行在 GUI 线程，无跨线程对象。

条目持久化在独立文件 todo_items[-<instance_id>].json（config.dir 下），
偏好 todo_reminder_enabled / todo_reminder_lead_minutes / todo_reminder_speak
在 config.json。

提醒语义：每条启用条目有 lead / due 两个触发档；触发窗口
[触发时刻, +grace] 内产生提醒并盖戳（持久化，防重启/唤醒重复），
出窗静默盖戳跳过（防休眠唤醒轰炸）；once 条目过 due+grace 自动归档
（enabled=False，面板置灰可见）。

语音播报（todo_reminder_speak，默认关）经 AppShell.ensure_audio_channel 走
语音报时服务那条唯一音频通道，文案与气泡共用 fire_text；免打扰拦下的那一档
不出声也不补播（与通道的"排队即丢弃"一致）。
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
    """返回 (lead_dt|None, due_dt|None)。

    daily 取"与 now 相关的那一次 occurrence"：当天那次还没过就用当天；当天已过
    且次日那次的**提前档**已经到点（每日 00:02 + 提前 5 分钟，23:57 看的就是次日
    的提前档）则取次日。缺这一步时跨午夜的提前提醒会被当成"当天已过"的出窗档
    静默盖戳，用户要等过了午夜才收到贴着正点的提醒（迟到的提前量）。

    取次日只在到期档到来前发生（now < 次日 due），所以不会当天与次日双发：
    提前档的盖戳键按**触发时刻**的日期，23:57 与 00:00 算出来是同一个键。

    once 用条目自己的日期（缺失/非法时返回 (None, None)，视为不可触发），
    过期即出窗静默盖戳——语义不受上面这步影响。
    """
    time_text = _normalize_hhmm(item.get("time"))
    if not time_text:
        return None, None
    hour, minute = (int(part) for part in time_text.split(":"))
    if item.get("kind") == "daily":
        due = datetime(now.year, now.month, now.day, hour, minute)
        if due <= now:
            next_due = due + timedelta(days=1)
            next_trigger = (
                next_due - timedelta(minutes=lead_minutes) if lead_minutes > 0 else next_due
            )
            if now >= next_trigger:
                due = next_due
    else:
        day_text = _normalize_iso_date(item.get("date"))
        if not day_text:
            return None, None
        day = date.fromisoformat(day_text)
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


def fire_text(fire: dict) -> str:
    """一次到点提醒的展示文案（气泡与语音共用这一个 builder，两处不会漂移）。"""
    return f"⏰ 待办提醒：{fire['title']}（{fire['time']}）"


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
            due = datetime(day.year, day.month, day.day, hour, minute)
            if due <= now:
                # 当天那次已到点（含已触发/已出窗盖戳）：下一条是**次日**那次。
                # 只判 fired_due_slot 会在"今天那次的 due 刚过、戳还没落盘"以及
                # 跨午夜（23:57 看每日 00:02，今天那次凌晨就过了）时漏掉次日那次，
                # 「下一条」整段空着——与 _fire_datetimes 同一族的天选择。
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
    """待办提醒调度服务（AppShell 持有，GUI 线程）。

    同 ProactiveScreenWatcher：本类不继承 QObject（Qt 在 __init__ 内惰性
    导入，模块顶层保持无 Qt），持有无主 QTimer，由 AppShell 持有引用保证
    生命周期。tick → 纯函数 advance_todo_state → 桌宠可见且设置未抑制时
    冒气泡，否则走 app.system_notify 桌面通知（受
    system_notifications_enabled 全局门控，关闭时该分支静默）；盖戳/归档
    仅在条目变化时落盘。todo_reminder_speak 打开时，气泡/通知之后还会把同一句
    话交给音频通道念一遍（见 _speak_fire）；免打扰那条早退分支不出声也不补播。
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
            "speak": bool(config.get("todo_reminder_speak", False)) if config else False,
        }
        # 桌面通知分支与既有调用方（chat 等）同规：受全局通知开关门控
        self._notify_enabled = (
            bool(config.get("system_notifications_enabled", True)) if config else True
        )
        self._items = self._store.load()

    def items(self) -> list[dict]:
        """当前条目（面板打开时以服务内副本为准，保存路径仍走 store）。"""
        return list(self._items)

    def set_items(self, items: list[dict], *, save: bool = True) -> bool:
        """面板保存入口：整体替换条目并立即落盘。返回是否成功落盘。

        **落盘成功才提交内存态**（失败返回 False，内存保持原样）：``_items`` 是
        提醒调度的真值源，留一份"没落盘却被当成已保存"的副本会出两层问题——提醒
        会为下次重读就消失的条目按时冒泡，且面板仍停在新建态（编辑器不关），用户
        再点一次保存就往内存里追加第二条。调用方（面板）据返回值保住草稿并提示。
        """
        cleaned = clean_todo_items(items)
        if save and not self._store.save(cleaned):
            return False
        self._items = cleaned
        return True

    def store_path(self):
        """条目文件路径（保存失败提示要告诉用户改哪里）。"""
        return self._store.path

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
        text = fire_text(fire)
        win = getattr(app, "win", None)
        if win is not None:
            from . import window_alerts

            if window_alerts.quiet_blocked(win):
                # 免打扰（含全屏等效）：桌宠气泡**与系统通知**一并压住——只压气泡
                # 的话，DND 期间照样弹一条 Windows 通知，等于没免打扰。提醒本身
                # 进暂存队列，结束（到时/提前/退出全屏）后统一汇报。
                window_alerts.defer_quiet(text)
                return
        if win is not None and win.isVisible() and not self._bubble_suppressed():
            win.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS)
        elif self._notify_enabled:
            notify = getattr(app, "system_notify", None)
            if callable(notify):
                notify("待办提醒", f"{fire['title']}（{fire['time']}）",
                       on_click=getattr(app, "open_todo_panel", None))
        self._speak_fire(fire)

    def _speak_fire(self, fire: dict) -> None:
        """经语音报时服务的音频通道把这次提醒念出来（同 festival_service._speak）。

        刻意**不自建播放器**：报时服务是进程内唯一的音频通道，共用它才能在结构上
        保证不叠音。通道不存在或播报失败都只降级为"有气泡没声音"，不影响提醒本身。
        """
        if not self._prefs.get("speak"):
            return
        getter = getattr(self._app, "ensure_audio_channel", None)
        if not callable(getter):
            logger.warning("音频通道不可用，待办提醒仅出气泡")
            return
        try:
            channel = getter()
        except Exception:
            logger.exception("获取音频通道失败，待办提醒仅出气泡")
            return
        if channel is None:
            logger.warning("音频通道为空，待办提醒仅出气泡")
            return
        try:
            channel.speak(fire_text(fire), log_tag="待办提醒")
        except Exception:
            logger.exception("待办提醒语音播报失败")

    def _bubble_suppressed(self) -> bool:
        """设置窗口打开期间暂停气泡（与 PetInstance._update_bubble_suppression_for_settings
        同一判定来源：抑制状态本就是 app 层根据对话框存在性设置的）。"""
        app = self._app
        return (
            getattr(app, "modern_settings_dialog", None) is not None
            or getattr(app, "chat_settings_dialog", None) is not None
        )
