# -*- coding: utf-8 -*-
"""今日汇总纯逻辑层：把手上已有的几段数据拼成一句人话。

数据来源按"有没有"逐段拼，缺哪段就少哪段（没配 API Key 就不提余额，
这是参考版的既有口径，也避免把查询失败说成"今天没花钱"）。

本模块不 import Qt、不读配置、不发网络请求——调用方（``pet/quiet_service.py``）
负责把数据准备好再传进来，因此可以在纯逻辑单测里逐段验证。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

# 应用时长进汇总的门槛与条数（参考版口径：至少 20 分钟才点名，最多两条）
DEFAULT_SUMMARY_MIN_SECONDS = 20 * 60
DEFAULT_SUMMARY_MAX_APPS = 2

EMPTY_SUMMARY = "今天还没有可汇总的内容哦"


def format_duration(seconds) -> str:
    """秒 → ``2 小时 10 分`` / ``48 分`` / ``30 秒``。"""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        total = 0
    if total >= 3600:
        hours, rest = divmod(total, 3600)
        minutes = rest // 60
        return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
    if total >= 60:
        return f"{total // 60} 分"
    return f"{total} 秒"


def format_money(used, currency: str = "") -> str:
    """金额格式化：默认人民币符号，非 CNY 用货币代码前缀。"""
    try:
        value = float(used)
    except (TypeError, ValueError):
        return ""
    code = str(currency or "CNY").strip().upper()
    symbol = {"CNY": "¥", "USD": "$", "EUR": "€"}.get(code, f"{code} ")
    return f"{symbol}{value:.2f}"


def read_today_usage(path) -> float:
    """读本地账本里的"今天用了多少"；没这本 / 跨天 / 脏数据都返回 0。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            book = json.load(handle) or {}
    except (OSError, ValueError):
        return 0.0
    if not isinstance(book, dict):
        return 0.0
    if book.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return 0.0
    try:
        return float(book.get("todayUsage") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def top_app_usage(app_times, *, min_seconds: int = DEFAULT_SUMMARY_MIN_SECONDS,
                  max_apps: int = DEFAULT_SUMMARY_MAX_APPS) -> list[tuple[str, int]]:
    """``{app: seconds}`` → 用时最长的前几条；低于门槛的不点名。"""
    items: list[tuple[str, int]] = []
    for name, seconds in (app_times or {}).items():
        try:
            value = int(round(float(seconds)))
        except (TypeError, ValueError):
            continue
        if value >= max(0, int(min_seconds)):
            items.append((str(name), value))
    items.sort(key=lambda item: item[1], reverse=True)
    return items[: max(1, int(max_apps))]


def build_daily_summary(
    *,
    balance_used=None,
    currency: str = "CNY",
    balance_text: str = "",
    app_times=None,
    agent_cost_text: str = "",
    min_seconds: int = DEFAULT_SUMMARY_MIN_SECONDS,
    max_apps: int = DEFAULT_SUMMARY_MAX_APPS,
) -> str:
    """拼今天的汇总句；一段数据都没有时返回 ``EMPTY_SUMMARY``。

    ``balance_text`` 优先于 ``balance_used``：调用方已经有一句现成的余额描述
    （例如余额气泡渲染过的文案）时直接复用，避免同一件事出现两种说法。
    """
    parts: list[str] = []
    money = format_money(balance_used, currency) if balance_used else ""
    if money:
        parts.append(f"余额用了 {money}")
    text = str(balance_text or "").strip()
    if text:
        parts.append(text)
    top = top_app_usage(app_times, min_seconds=min_seconds, max_apps=max_apps)
    if top:
        parts.append("、".join(f"{name} {format_duration(seconds)}" for name, seconds in top))
    cost = str(agent_cost_text or "").strip()
    if cost:
        parts.append(cost)
    if not parts:
        return EMPTY_SUMMARY
    return "今天：" + " · ".join(parts)


def usage_book_path(config_dir) -> str:
    """余额账本路径（与余额域同目录；文件不存在时按 0 处理）。"""
    return os.path.join(str(config_dir or "."), "usage.json")
