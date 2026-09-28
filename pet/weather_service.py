# -*- coding: utf-8 -*-
"""天气服务壳：按需抓取（后台线程）→ 主线程冒泡；城市切换与菜单入口。

**为什么还要一层服务壳**：取数是网络 + 阻塞的（`urllib`，6 秒超时），绝不能放
GUI 线程；而气泡必须在 GUI 线程弹。所以走「后台线程写队列 → 主线程 tick 消费」
这条本仓库既有的老路（同 `voice_chime_service` 的合成线程、`quiet_service` 的节拍）。

判定与解析全在 `pet/weather_source.py`（纯逻辑、可离线单测），本模块只做：
缓存与 TTL、并发去重（同一次查询不重复发）、城市列表读写、菜单用的小流程。
"""
from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QInputDialog, QLineEdit

from . import weather_source as source

logger = logging.getLogger("dsh-pet-standalone")

TICK_INTERVAL_MS = 300
CACHE_TTL_SECONDS = 600.0        # 同一个城市 10 分钟内不重复联网

_STATE_ATTR = "_weather_state"
_TIMER_ATTR = "_weather_timer"

# 取数出口（测试可替换；生产就是 weather_source 的实现）
_fetch = source.fetch_weather
_lookup = source.lookup_city


# ------------------------------------------------------------------ 状态与配置
def state(host) -> dict:
    """宿主上的小状态机：``queue``（后台结果）/ ``busy``（去重）/ ``cache``（TTL）。"""
    current = getattr(host, _STATE_ATTR, None)
    if not isinstance(current, dict):
        current = {"queue": [], "busy": False, "cache": {}}
        setattr(host, _STATE_ATTR, current)
    return current


def _cfg_value(host, key, default=None):
    cfg = getattr(host, "cfg", None)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:
        return default


def enabled(host) -> bool:
    return bool(_cfg_value(host, "weather_enabled", True))


def current_city(host) -> str:
    return source.clean_city(_cfg_value(host, "weather_city", ""))


def cities(host) -> list[str]:
    """菜单/设置页展示用的城市列表：当前城市永远排第一。"""
    return source.clean_city_list(_cfg_value(host, "weather_city_list", []), current_city(host))


def set_city(host, city: str) -> str:
    """切换城市并落盘（菜单点一下就该记住，不等退出）。"""
    city = source.clean_city(city)
    cfg = getattr(host, "cfg", None)
    if cfg is None or not city:
        return ""
    cfg.set("weather_city", city)
    cfg.set("weather_city_list", source.clean_city_list(cities(host), city))
    saver = getattr(cfg, "save", None)
    if callable(saver):
        saver()
    return city


def remove_city(host, city: str) -> None:
    """从列表里删掉一个城市；删的是当前城市就顶最前面那个上来（至少留一个）。"""
    left = [name for name in cities(host) if name != source.clean_city(city)]
    cfg = getattr(host, "cfg", None)
    if cfg is None:
        return
    cfg.set("weather_city_list", left)
    if current_city(host) == source.clean_city(city):
        cfg.set("weather_city", left[0] if left else "")
    saver = getattr(cfg, "save", None)
    if callable(saver):
        saver()


# ------------------------------------------------------------------ 抓取
def request(host, city: str | None = None) -> bool:
    """查天气并冒泡。返回 True 表示已发起或已展示；False 表示还没配城市/功能关着。"""
    if not enabled(host):
        return False
    city = source.clean_city(city) or current_city(host)
    if not city:
        return False
    cached = state(host)["cache"].get(city)
    if cached and time.monotonic() - float(cached[2]) < CACHE_TTL_SECONDS:
        _present(host, source.format_weather_line(city, cached[0], cached[1]))
        return True
    if state(host)["busy"]:
        _present(host, "还在查呢，主人等人家一下下嘛")
        return True
    state(host)["busy"] = True
    ensure_started(host)

    def worker() -> None:
        try:
            got = _fetch(city)
        except Exception:      # 网络层任何意外都按"没查到"处理，绝不炸 GUI
            logger.debug("天气查询失败", exc_info=True)
            got = None
        state(host)["queue"].append(("weather", city, got))

    threading.Thread(target=worker, daemon=True, name="weather-fetch").start()
    return True


def search_and_add(host, query: str) -> bool:
    """联网搜候选城市（后台），结果回到主线程让主人挑一个。"""
    query = source.clean_city(query)
    if not query:
        return False
    ensure_started(host)

    def worker() -> None:
        try:
            candidates = _lookup(query)
        except Exception:
            candidates = []
        state(host)["queue"].append(("cities", query, candidates))

    threading.Thread(target=worker, daemon=True, name="weather-lookup").start()
    return True


# ------------------------------------------------------------------ 节拍与展示
def ensure_started(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is None:
        # 无主 QTimer：宿主可能是测试替身（见 quiet_service 同款说明）
        timer = QTimer()
        timer.setInterval(TICK_INTERVAL_MS)
        timer.timeout.connect(lambda: tick(host))
        setattr(host, _TIMER_ATTR, timer)
    if not timer.isActive():
        timer.start()


def stop_timer(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is not None:
        timer.stop()


def tick(host) -> None:
    """主线程消费后台结果；队列空时把节拍停掉（不常驻空转）。"""
    queue = state(host)["queue"]
    while queue:
        kind, key, payload = queue.pop(0)
        if kind == "weather":
            state(host)["busy"] = False
            if payload:
                state(host)["cache"][key] = (payload[0], payload[1], time.monotonic())
                _present(host, source.format_weather_line(key, payload[0], payload[1]))
            else:
                _present(host, f"呜～{key}的天气没查到，等会儿再试试好嘛")
        elif kind == "cities":
            _pick_candidate(host, key, payload)
    if not queue and not state(host)["busy"]:
        stop_timer(host)


def _pick_candidate(host, query: str, candidates: list[tuple[str, str]]) -> None:
    """候选城市选择框（主线程）。只有一个候选就直接用，省一次点击。"""
    if not candidates:
        _present(host, f"呜～没搜到「{query}」这个地方，换个叫法试试？")
        return
    if len(candidates) == 1:
        set_city(host, candidates[0][1])
        request(host, candidates[0][1])
        return
    labels = [label for label, _city in candidates]
    try:
        picked, ok = QInputDialog.getItem(
            host, "添加城市", f"「{query}」搜到几个地方，选一个：", labels, 0, False,
            Qt.WindowType.WindowStaysOnTopHint,
        )
    except Exception:
        return
    if not ok or not picked:
        return
    for label, city in candidates:
        if label == picked:
            set_city(host, city)
            request(host, city)
            return


def _present(host, text: str, *, duration_ms: int = 5200) -> None:
    """冒泡（走提醒队列，一次只出一个；免打扰期间会被压住并攒着）。"""
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type="weather", priority=3,
        )
    except Exception:
        logger.exception("天气气泡展示失败")


def remember_city(host, city: str) -> None:
    """由设置页调用：把输入框里的城市落到配置（不清空列表）。"""
    set_city(host, city)


def forget_cache(host) -> None:
    state(host)["cache"].clear()


# ------------------------------------------------------------------ 菜单入口
def show_weather(host) -> None:
    """菜单「查看天气」：没配城市就先问一句，配好了直接查。"""
    if not enabled(host):
        _present(host, "天气功能关着呢，主人去设置里打开嘛")
        return
    if not current_city(host):
        prompt_manual_city(host)
        return
    request(host)


def prompt_manual_city(host, *, initial: str = "") -> None:
    """手动输入城市名（不联网，直接把名字写进配置）。"""
    try:
        city, ok = QInputDialog.getText(
            host, "设置城市", "输入城市名（中文英文都行，例如：汕头 / Shantou）：",
            QLineEdit.EchoMode.Normal, initial or current_city(host),
            Qt.WindowType.WindowStaysOnTopHint,
        )
    except Exception:
        return
    if ok and city.strip():
        set_city(host, city.strip())
        request(host, city.strip())


def prompt_search_city(host) -> None:
    """联网搜索城市（后台），搜到候选让主人挑。"""
    try:
        query, ok = QInputDialog.getText(
            host, "添加城市", "输入城市名（联网搜索）：", QLineEdit.EchoMode.Normal, "",
            Qt.WindowType.WindowStaysOnTopHint,
        )
    except Exception:
        return
    if ok and query.strip():
        search_and_add(host, query.strip())


def prompt_remove_city(host) -> None:
    """从列表里删掉一个城市（最后一个删不掉，总得留一个用）。"""
    known = cities(host)
    if len(known) <= 1:
        _present(host, "就剩这一个城市啦，删下去可要空咯")
        return
    try:
        picked, ok = QInputDialog.getItem(
            host, "删除城市", "删掉哪个城市？", known, 0, False,
            Qt.WindowType.WindowStaysOnTopHint,
        )
    except Exception:
        return
    if ok and picked:
        remove_city(host, picked)
