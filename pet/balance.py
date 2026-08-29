# -*- coding: utf-8 -*-
"""DeepSeek 开放平台余额查询（GET /user/balance）。

余额气泡/小部件显示思路参考 MeteorNOX/DeepSeek-Balance-Whale-Widget
（见 README「参考项目」致谢），本实现为桌宠内置的轻量版本：
菜单「DeepSeek 余额」→ 后台查询 → 桌宠气泡显示；可在桌宠设置中
开启自动刷新（分钟级）。

v4.1 扩展（同步上游 dsh-pet 余额动画）：
- 余额按 ¥20 满额折算为“已用百分比”，分 6 档触发不同余额动画；
- DeepSeek 峰谷计价提示（北京时间：工作日 9-12/14-18 高峰，其余空闲；
  周六/周日全天空闲，下一高峰为下周一 9 点）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

BALANCE_PATH = '/user/balance'

# DeepSeek 满额基准（¥）：余额 ≥ 该值视为 100%（未消耗），余额按比例折算为已用百分比
DEEPSEEK_FULL_BALANCE_CNY = 20.0

# 余额事件动画档位顺序（与上游 assets/config.jsonc 一致）：
# index = p === 100 ? 5 : Math.floor(p / 20)
BALANCE_EVENT_NAMES = (
    '余额-钱袋满溢',  # 0 ≤ p < 20（几乎未消耗）
    '余额-金袋叮当',  # 20 ≤ p < 40
    '余额-钱袋如常',  # 40 ≤ p < 60
    '余额-数金皱眉',  # 60 ≤ p < 80
    '余额-袋空如洗',  # 80 ≤ p < 100（告急）
    '余额-分文不剩',  # p === 100（全部用完，格外档）
)

_BEIJING_TZ = ZoneInfo('Asia/Shanghai')


def _ssl_context(verify: bool):
    """延迟导入：无 Chat 变体排除 pet.chat 模块，顶层 import 会直接 ImportError。"""
    from .chat.providers import _make_ssl_context
    return _make_ssl_context(verify)


class BalanceError(RuntimeError):
    pass


def fetch_balance(base_url: str, api_key: str, timeout: float = 10.0,
                  verify_ssl: bool = True) -> dict:
    """查询余额。

    返回 {'is_available': bool, 'total': str, 'granted': str, 'topped_up': str}；
    未配置 Key / 端点不支持 / 网络失败抛 BalanceError。
    """
    endpoint = str(base_url or '').strip().rstrip('/') + BALANCE_PATH
    if not api_key:
        raise BalanceError('未配置 API Key')
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context(verify_ssl)) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise BalanceError(f'HTTP {exc.code}（该端点可能不支持余额查询）') from exc
    except urllib.error.URLError as exc:
        raise BalanceError(f'网络连接失败：{exc.reason}') from exc
    except (OSError, ValueError) as exc:
        raise BalanceError(str(exc)) from exc
    infos = data.get('balance_infos') if isinstance(data, dict) else None
    if not infos:
        raise BalanceError('响应中没有余额信息')
    info = infos[0] if isinstance(infos, list) else infos
    return {
        'is_available': bool(data.get('is_available', True)),
        'total': str(info.get('total_balance', '')),
        'granted': str(info.get('granted_balance', '')),
        'topped_up': str(info.get('topped_up_balance', '')),
    }


def format_balance(info: dict) -> str:
    """'余额 ¥12.34（充值 10.00 / 赠送 2.34）'；单一余额时简化。"""
    total = str(info.get('total', '') or '')
    granted = str(info.get('granted', '') or '')
    topped = str(info.get('topped_up', '') or '')
    if not total:
        return '余额信息为空'
    if granted and topped:
        return f'余额 ¥{total}（充值 ¥{topped} / 赠送 ¥{granted}）'
    return f'余额 ¥{total}'


def balance_percent(total: str | float | None) -> float | None:
    """把 DeepSeek 余额折算为“已用百分比”（0 = 未消耗，100 = 耗尽）。

    余额 20 元 → 0%，10 元 → 50%，0 元 → 100%；负数按 0 处理（透支视为已用完）。
    金额非法返回 None，上层不应触发档位动画。
    """
    raw = str(total or '').strip()
    if raw == '':
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    remaining = max(0.0, value) / DEEPSEEK_FULL_BALANCE_CNY * 100.0
    return max(0.0, min(100.0, 100.0 - remaining))


def balance_event_index(p: float) -> int:
    """余额事件档位索引：p === 100 → 5，否则 Math.floor(p / 20) 夹到 0..4。"""
    if p >= 100.0:
        return 5
    idx = int(p // 20)
    return idx if idx < 5 else 4


def _beijing_now(now: datetime | None = None) -> datetime:
    """把入参统一转到北京时间；None 取当前北京时间。"""
    if now is None:
        return datetime.now(_BEIJING_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=_BEIJING_TZ)
    return now.astimezone(_BEIJING_TZ)


def deepseek_pricing_tier(now: datetime | None = None) -> str:
    """DeepSeek 峰谷计价档位（北京时间）。

    高峰：工作日 9:00–12:00、14:00–18:00；其余为空闲（低谷）。
    周六/周日全天按低谷价计费。
    """
    bj = _beijing_now(now)
    if bj.weekday() >= 5:
        return 'idle'
    hour = bj.hour
    return 'peak' if (9 <= hour < 12) or (14 <= hour < 18) else 'idle'


def _next_pricing_switch(now: datetime | None = None) -> tuple[str, datetime]:
    """返回 (下一档位, 下一档位开始时间)，按北京时间计算。"""
    bj = _beijing_now(now)
    tz = bj.tzinfo or _BEIJING_TZ
    day = bj.date()
    weekday = bj.weekday()

    if weekday >= 5:
        # 周末全天低谷：下一高峰为下周一 9:00
        days_until_monday = 7 - weekday
        return 'peak', datetime.combine(day + timedelta(days=days_until_monday), time(9, 0), tzinfo=tz)

    hour = bj.hour
    if hour < 9:
        return 'peak', datetime.combine(day, time(9, 0), tzinfo=tz)
    if hour < 12:
        return 'idle', datetime.combine(day, time(12, 0), tzinfo=tz)
    if hour < 14:
        return 'peak', datetime.combine(day, time(14, 0), tzinfo=tz)
    if hour < 18:
        return 'idle', datetime.combine(day, time(18, 0), tzinfo=tz)
    # 18:00 后：下一高峰通常为次日 9:00，但若次日是周六/周日，
    # 周末全天空闲，下一高峰应跳到下周一 9:00。
    next_day = day + timedelta(days=1)
    if next_day.weekday() >= 5:
        days_until_monday = 7 - next_day.weekday()
        return 'peak', datetime.combine(
            next_day + timedelta(days=days_until_monday), time(9, 0), tzinfo=tz
        )
    return 'peak', datetime.combine(next_day, time(9, 0), tzinfo=tz)


def resolve_tier_labels(
    mode: str = "default",
    custom_peak: str = "",
    custom_idle: str = "",
) -> tuple[str, str]:
    """根据设置返回 (高峰文本, 空闲文本)。

    - default：高峰 / 空闲
    - liangwen：梁文峰 / 梁文谷
    - custom：使用用户自定义文本，留空回退默认
    """
    mode = str(mode or "default").strip().lower()
    if mode == "liangwen":
        return "梁文峰", "梁文谷"
    if mode == "custom":
        peak = str(custom_peak or "").strip() or "高峰"
        idle = str(custom_idle or "").strip() or "空闲"
        return peak, idle
    return "高峰", "空闲"


def deepseek_pricing_hint(
    now: datetime | None = None,
    peak_label: str | None = None,
    idle_label: str | None = None,
) -> str:
    """生成余额气泡下方的 DeepSeek 峰谷提示文案。

    如「DeepSeek 当前高峰 · 下一空闲 12:00」「DeepSeek 当前空闲 · 下一高峰 09:00」。
    peak_label/idle_label 可由设置项自定义（如梁文峰/梁文谷）。
    """
    bj = _beijing_now(now)
    tier = deepseek_pricing_tier(bj)
    next_tier, next_time = _next_pricing_switch(bj)
    peak_text = str(peak_label or "高峰")
    idle_text = str(idle_label or "空闲")
    label = peak_text if tier == 'peak' else idle_text
    next_label = idle_text if next_tier == 'idle' else peak_text
    return f'DeepSeek 当前{label} · 下一{next_label} {next_time:%H:%M}'


def deepseek_pricing_hint_html(
    now: datetime | None = None,
    peak_label: str | None = None,
    idle_label: str | None = None,
    peak_color: str = "#e5484d",
    idle_color: str = "#30a46c",
) -> str:
    """生成带颜色的 DeepSeek 峰谷提示 HTML（QLabel 可直接渲染）。

    默认高峰红、低谷绿；用户自定义文案会做 HTML 转义，防止破坏富文本。
    """
    bj = _beijing_now(now)
    tier = deepseek_pricing_tier(bj)
    next_tier, next_time = _next_pricing_switch(bj)
    peak_text = escape(str(peak_label or "高峰"))
    idle_text = escape(str(idle_label or "空闲"))

    def span(text: str, color: str) -> str:
        return f'<span style="color:{color}">{text}</span>'

    label = span(peak_text, peak_color) if tier == 'peak' else span(idle_text, idle_color)
    next_label = span(idle_text, idle_color) if next_tier == 'idle' else span(peak_text, peak_color)
    return f'DeepSeek 当前{label} · 下一{next_label} {next_time:%H:%M}'
