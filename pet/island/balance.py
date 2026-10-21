# -*- coding: utf-8 -*-
"""DeepSeek 开放平台余额查询（GET /user/balance）。

余额气泡/小部件显示思路参考 MeteorNOX/DeepSeek-Balance-Whale-Widget
（见 README「参考项目」致谢），本实现为桌宠内置的轻量版本：
菜单「DeepSeek 余额」→ 后台查询 → 桌宠气泡显示；可在桌宠设置中
开启自动刷新（分钟级）。

v4.1 扩展（同步上游 dsh-pet 余额动画）：
- 余额按 ¥20 满额折算为“已用百分比”，分 6 档触发不同余额动画；
- DeepSeek 峰谷计价提示（北京时间：工作日 9-12/14-18 高峰，其余空闲；
  周六/周日与**中国法定节假日**全天空闲——调休补班的周六/周日按官方口径
  仍是空闲；下一高峰由 09/12/14/18 点边界逐点扫描得出）。
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
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
    from ..chat.providers import _make_ssl_context
    return _make_ssl_context(verify)


class BalanceError(RuntimeError):
    pass


def _pick_balance_info(infos):
    """多币种 balance_infos 挑选：优先有余额的一条，同分优先 CNY，全为 0 退回首条。

    官方 GET /user/balance 可能返回多条记录（如 CNY + USD）且顺序不保证；
    盲目取 infos[0] 会在 USD 排前时把 0.00 当成余额（issue #106）。
    """
    if not isinstance(infos, list):
        return infos

    def _rank(item):
        if not isinstance(item, dict):
            return (False, False)
        try:
            positive = float(item.get('total_balance') or 0) > 0
        except (TypeError, ValueError):
            positive = False
        cny = str(item.get('currency', '')).upper() == 'CNY'
        return (positive, cny)

    return max(infos, key=_rank)


def fetch_balance(base_url: str, api_key: str, timeout: float = 10.0,
                  verify_ssl: bool = True) -> dict:
    """查询余额。

    返回 {'is_available': bool, 'total': str, 'granted': str, 'topped_up': str}；
    未配置 Key / 端点不支持 / 网络失败抛 BalanceError。
    """
    endpoint = str(base_url or '').strip().rstrip('/') + BALANCE_PATH
    if not api_key:
        raise BalanceError('未配置 API Key')
    from ..chat.providers import build_browser_headers  # 延迟导入：无 Chat 变体排除 pet.chat
    headers = build_browser_headers({'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'})
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context(verify_ssl)) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise BalanceError(f'HTTP {exc.code}') from exc
    except (socket.timeout, TimeoutError) as exc:
        raise BalanceError('请求超时') from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason or '')
        if 'timed out' in reason.lower() or 'timeout' in reason.lower():
            raise BalanceError('请求超时') from exc
        raise BalanceError(f'网络连接失败：{exc.reason}') from exc
    except json.JSONDecodeError as exc:
        raise BalanceError('返回数据无效') from exc
    except (OSError, ValueError) as exc:
        raise BalanceError(f'返回数据无效：{exc}') from exc
    infos = data.get('balance_infos') if isinstance(data, dict) else None
    if not infos:
        raise BalanceError('响应中没有余额信息')
    info = _pick_balance_info(infos)
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


# U+2060 WORD JOINER：零宽，且不属于 Unicode 空白，normalize 折空白时不会把它吃掉。
# 用它而不是 NBSP，是因为气泡规整会把不换行空格折回普通空格。
_WORD_JOINER = '\u2060'
_HSPACE = r'[^\S\n]*'
# 只连接「余额/充值/赠送 + 现有分隔 + 货币金额」。人设散文里单独出现的这两个字不动。
_BALANCE_PHRASE_RE = re.compile(
    rf'(?:余额|充值|赠送){_HSPACE}[:：]?{_HSPACE}'
    rf'(?:[¥￥]{_HSPACE}-?{_HSPACE}\d+(?:\.\d+)?|-{_HSPACE}[¥￥]{_HSPACE}\d+(?:\.\d+)?)'
)


def format_balance_display(text: str) -> str:
    """给已经渲染好的余额正文加上断行保护，只用于气泡显示。

    「余额 ¥12.34」「充值 ¥10.00」「赠送 ¥2.34」各成一段：标签、原有分隔和整个
    金额之间插入 WORD JOINER，看起来仍是原来的空格。没有这些金额短语的文字
    原样返回。再调用一次不会叠第二层连接符。

    不要把结果写进余额缓存或请求体——``format_balance`` 保持明文。
    """
    raw = text if isinstance(text, str) else str(text or '')
    if not raw:
        return ''

    def _join(match: re.Match) -> str:
        visible = match.group(0).replace(_WORD_JOINER, '')
        if len(visible) <= 1:
            return match.group(0)
        return _WORD_JOINER.join(visible)

    return _BALANCE_PHRASE_RE.sub(_join, raw)


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


def _statutory_holiday(day: date) -> str | None:
    """查询该日是否为中国法定放假日（放假日名 / None）。

    函数内 import `pet.festival.calendar`：它是唯一的 lunar-python 边界，
    而 `tests/test_app_lazy_imports.py` 要求 `import pet.app` 常驻时不得连带
    载入节日模块（实测 ~7.7MB），故不能在模块顶层引入。历法后端缺失时按
    「无节假日数据」降级（返回 None），不阻断余额提示。
    """
    try:
        from ..festival.calendar import statutory_holiday_name
    except ImportError:
        return None
    return statutory_holiday_name(day)


def deepseek_pricing_tier(now: datetime | None = None) -> str:
    """DeepSeek 峰谷计价档位（北京时间）。

    高峰：周一至周五（**不含中国法定节假日**）9:00–12:00、14:00–18:00；
    其余为空闲（低谷）。周六/周日与法定节假日全天空闲——调休补班的周六/周日
    按官方口径仍是空闲，不是工作日高峰。节假日安排表覆盖范围之外（2026-10-11
    起）降级为只按星期规则判断。
    """
    bj = _beijing_now(now)
    if bj.weekday() >= 5:
        return 'idle'
    if _statutory_holiday(bj.date()):
        return 'idle'
    hour = bj.hour
    return 'peak' if (9 <= hour < 12) or (14 <= hour < 18) else 'idle'


# 一天内的计价切换时刻：09:00 / 14:00 进入高峰，12:00 / 18:00 离开高峰。
_PRICING_BOUNDARIES = (time(9, 0), time(12, 0), time(14, 0), time(18, 0))

# 切换扫描窗口（天）：法定长假最长 8 天，叠加两端周末约 10 天，40 天留足余量。
_PRICING_SCAN_DAYS = 40


def _next_pricing_switch(now: datetime | None = None) -> tuple[str, datetime]:
    """返回 (下一档位, 下一档位开始时间)，按北京时间计算。

    逐个扫描未来 09:00/12:00/14:00/18:00 边界，取**最早**一个档位与当前不同的
    时刻。这样周末、法定节假日、连同调休补班后的空档都能被跨越，无需为每种
    日历情形单独写分支。
    """
    bj = _beijing_now(now)
    tz = bj.tzinfo or _BEIJING_TZ
    current = deepseek_pricing_tier(bj)
    day = bj.date()
    for offset in range(_PRICING_SCAN_DAYS + 1):
        candidate_day = day + timedelta(days=offset)
        for boundary in _PRICING_BOUNDARIES:
            candidate = datetime.combine(candidate_day, boundary, tzinfo=tz)
            if candidate <= bj:
                continue
            tier = deepseek_pricing_tier(candidate)
            if tier != current:
                return tier, candidate
    # 扫描窗口内没有翻转（历法数据耗尽的远期日期才会走到这里）：退回次日 09:00。
    fallback = datetime.combine(day + timedelta(days=1), time(9, 0), tzinfo=tz)
    return deepseek_pricing_tier(fallback), fallback


def next_pricing_switch(now: datetime | None = None) -> tuple[str, datetime]:
    """公开入口：返回 (下一档位, 下一档位开始时间)，按北京时间计算。"""
    return _next_pricing_switch(now)


def beijing_now(now: datetime | None = None) -> datetime:
    """公开入口：返回当前/入参的北京时间（供 UI 层自行定时刷新）。"""
    return _beijing_now(now)


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _format_switch_time(now: datetime, next_time: datetime) -> str:
    """把下一档位切换时间格式化为易读文案。

    当天切换只显示 HH:MM；跨天切换显示“明天 HH:MM”，更远的日期按 **ISO 周**
    决定措辞：同一周内用“周四 HH:MM”，跨周才用“下周四 HH:MM”。避免把周五晚上
    的“09:00”误解为次日早晨，也避免把同一周的周四说成“下周四”。
    """
    if next_time.date() == now.date():
        return f"{next_time:%H:%M}"
    days = (next_time.date() - now.date()).days
    if days == 1:
        return f"明天 {next_time:%H:%M}"
    same_week = next_time.date().isocalendar()[:2] == now.date().isocalendar()[:2]
    prefix = "" if same_week else "下"
    return f"{prefix}{_WEEKDAY_CN[next_time.weekday()]} {next_time:%H:%M}"


def format_switch_time(now: datetime, next_time: datetime) -> str:
    """公开入口：把下一档位切换时间格式化为易读文案。"""
    return _format_switch_time(now, next_time)


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
    time_text = _format_switch_time(bj, next_time)
    return f'DeepSeek 当前{label} · 下一{next_label} {time_text}'


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
    time_text = _format_switch_time(bj, next_time)
    return f'DeepSeek 当前{label} · 下一{next_label} {time_text}'
