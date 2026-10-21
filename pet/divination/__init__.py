# -*- coding: utf-8 -*-
"""每日一签的抽签引擎（纯逻辑：零 Qt、零 IO、零随机源）。

**核心不变量：同一天必得同一支。**

取签靠 `_splitmix64` 混一个由**日期序号**派生的种子，**不 `import random`**。
好处是：① 不用存"今天抽了什么"就能得到稳定结果（重启、重装、换机都一样）；
② 完全可回归——用例给一个日期就能断言一支签，不存在"今天验过明天又变"。

**一轮不重复**：调用方把"本轮已抽过的签号"传进来（`drawn_ids`），取签只在剩余签里
进行——先在剩余签中按档位权重定档（某档抽空则回落到还有剩余的档），再在该档剩余里
等概率取一支。一轮 48 天正好走完 48 支、一轮之内绝不重复；走满后由调用方清空重开。

**为什么不是"只排除同档"**：中平有 24 支却是 56% 权重，48 天里必然先抽空；若只做
同档排除，就会出现"同一支重复出现、而别的档还有没见过的签"，与"走完 48 支再轮回"
直接矛盾（这条是写用例时发现的，不是拍脑袋改的）。

**签号 = 在 `RAW_QUOTES` 里的下标 + 1**，只许追加（见 `divination_quotes` 的说明）。

**渲染有三档**（2026-10-16 定稿）：

* `bubble_lines()` —— 气泡：签头 + 解签（四句签诗放不进气泡，会占掉半屏）；
* `card_lines()` —— 详版：签头 / 四句签诗 / 解签；
* `speech_text()` —— 播报：``抽签中... 今日X签，签题。签文：……。解签：……``
  （她给的句式就是「抽签中。今日上上签，签文如何如何，解签如何如何」，
  省略号按她的原话写成三个点）。

**黄历/穿色已于 2026-10-16 整块删除**（她的原话：「不需要看黄历写宜忌了，我不是
说了忘掉之前的吗」）。本模块现在不 import 任何历法代码。
"""
from __future__ import annotations

import datetime as _dt
from typing import Iterable, NamedTuple, Sequence

from .quotes import RAW_QUOTES

__all__ = [
    "DivinationQuote",
    "QUOTES",
    "TIERS",
    "TIER_WEIGHTS",
    "WAITING_TEXT",
    "draw_for_day",
    "quote_by_id",
    "tier_probabilities",
    "bubble_lines",
    "card_lines",
    "speech_text",
    "normalize_divination_config",
    "clean_divination_state",
]

#: 档位由吉到平（显示顺序，也是权重表的键序）
TIERS: tuple[str, ...] = ("上上", "上", "中平", "下")

#: 出签概率权重（总和 100）。**与各档签数无关**——上签签数少但出手概率不低，
#: 这是当初定的口径（中平最多＝大多数日子本来就平常）。
TIER_WEIGHTS: dict[str, int] = {"上上": 4, "上": 18, "中平": 56, "下": 22}


class DivinationQuote(NamedTuple):
    """一支签（带签号）：四句七言签诗 + 白话解签。"""

    id: int
    tier: str
    title: str
    verses: tuple[str, str, str, str]
    reading: str


#: 48 支签（签号按顺序生成，只增不改）
QUOTES: tuple[DivinationQuote, ...] = tuple(
    DivinationQuote(i + 1, raw.tier, raw.title, raw.verses, raw.reading)
    for i, raw in enumerate(RAW_QUOTES)
)

_BY_ID: dict[int, DivinationQuote] = {q.id: q for q in QUOTES}


def _splitmix64(x: int) -> int:
    """splitmix64 混合函数（Steele 等）：雪崩性好、无外部依赖、跨平台一致。"""
    x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


def _seed_for(day: _dt.date) -> int:
    """日期 → 种子（用 toordinal，与本地时区/夏令时无关）。"""
    return _splitmix64(day.toordinal() * 0x2545F4914F6CDD1D)


def tier_probabilities() -> dict[str, float]:
    """各档位的出签概率（0–1），供设置页文案与用例使用。"""
    total = sum(TIER_WEIGHTS.values())
    return {tier: TIER_WEIGHTS[tier] / total for tier in TIERS}


def _tier_for(seed: int, available: frozenset[str] | None = None) -> str:
    """按权重定档位：把 [0, total) 的整数空间按权重切开。

    ``available`` 给定时（本轮还没抽空的档位集合），只在其中有签的档位里切分——
    否则中平（56% 权重、24 支）会在一轮的后段抽空却仍被反复选中，导致同一支重复，
    与"走完 48 支再轮回"矛盾。
    """
    tiers = [t for t in TIERS if available is None or t in available]
    if not tiers:
        tiers = list(TIERS)
    total = sum(TIER_WEIGHTS[t] for t in tiers)
    point = (seed >> 11) % total          # 高位常数偏置少，取中段更均匀
    acc = 0
    for tier in tiers:
        acc += TIER_WEIGHTS[tier]
        if point < acc:
            return tier
    return tiers[-1]


def _pick_in_tier(tier: str, seed: int, pool: Sequence[DivinationQuote]) -> DivinationQuote:
    """在给定池子里等概率取一支。

    这里**不能用 `hash(tier)`**——Python 的字符串 hash 每进程随机（PYTHONHASHSEED），
    会让"同一天同一支"跨进程失效。用档位在 `TIERS` 里的固定序号代替。
    """
    candidates = [q for q in pool if q.tier == tier] or list(pool)
    return candidates[_splitmix64(seed ^ TIERS.index(tier)) % len(candidates)]


def draw_for_day(day: _dt.date, drawn_ids: Iterable[int] = ()) -> DivinationQuote:
    """取 ``day`` 这一天的签（确定性；同一天永远同一支）。

    ``drawn_ids``＝**本轮已抽过的签号**。取签分两步：

    1. 只在"本轮还没抽过"的签里按档位权重定档（该档抽空则回落到还有剩余的档）；
    2. 在该档剩余签里等概率取一支。

    这样一轮（48 天）恰好走完 48 支、且**一轮之内绝不重复**；走满后调用方把
    ``drawn_ids`` 清空，开始下一轮。
    """
    drawn = {int(x) for x in drawn_ids}
    remaining = [q for q in QUOTES if q.id not in drawn] or list(QUOTES)
    seed = _seed_for(day)
    tiers = frozenset(q.tier for q in remaining)
    tier = _tier_for(seed, tiers)
    return _pick_in_tier(tier, seed, remaining)


def quote_by_id(quote_id: int) -> DivinationQuote | None:
    """按签号取签（当天锁定与配置清洗要用：状态里存的是号，不是整支）。"""
    return _BY_ID.get(int(quote_id))


#: 摇签时先冒/先念的那一句（她的原文，三个点）
WAITING_TEXT = "抽签中..."


def bubble_lines(quote: DivinationQuote) -> tuple[str, str]:
    """气泡两行：签头（档位＋签题）+ 解签。四句签诗放不进气泡。"""
    return (f"〔{quote.tier}〕{quote.title}", quote.reading)


def card_lines(quote: DivinationQuote) -> list[str]:
    """详版：签头 / 四句签诗 / 解签。"""
    return [f"〔{quote.tier}〕{quote.title}", *quote.verses, f"解签：{quote.reading}"]


def speech_text(quote: DivinationQuote) -> str:
    """**签文那一句**的播报文本（**不含**「抽签中...」）。

    「抽签中...」是独立的起手（`WAITING_TEXT`，由服务层先说、并在它念完之后静一段），
    所以这里不能再带——带上就会把「抽签中」念两遍。

    签诗按「上句，下句。上句，下句。」连读——七言连着念没有停顿，TTS 会糊成一片；
    句号处天然有换气。
    """
    upper = "，".join(quote.verses[:2])
    lower = "，".join(quote.verses[2:])
    return (f"今日{quote.tier}签，{quote.title}。"
            f"签文：{upper}。{lower}。解签：{quote.reading}")


# --------------------------------------------------------------- 配置清洗
def _clean_flag(value: object, default: bool) -> bool:
    """任意来源的布尔 → bool（非法值回落默认，绝不抛异常）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
    return default


def normalize_divination_config(config: object) -> dict:
    """把任意来源的配置清洗成本功能的内部结构（与节日提醒同口径）。

    键是**扁平顶层键**（`divination_*`），与 `pet/config.py` 的登记一致；
    接受 ``Config`` 对象或普通 dict，两者都只读。
    """
    get = getattr(config, "get", None)
    if get is None:
        source = config or {}
        get = source.get
    return {
        "enabled": _clean_flag(get("divination_enabled", False), False),
        "speak": _clean_flag(get("divination_speak", False), False),
        "hint": _clean_flag(get("divination_hint", True), True),
    }


def clean_divination_state(value: object) -> dict:
    """抽签状态清洗：非法结构一律当空，绝不让脏数据把功能卡死。

    形状：``{"date": "YYYY-MM-DD", "quote_id": int, "drawn": [int...]}``。
    **没有签册**（2026-10-16 她选 B 删掉）：历史只写不读，那就别写。
    """
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    date_text = value.get("date")
    if isinstance(date_text, str):
        try:
            _dt.date.fromisoformat(date_text)
            out["date"] = date_text
        except ValueError:
            pass
    try:
        quote_id = int(value.get("quote_id") or 0)
    except (TypeError, ValueError):
        quote_id = 0
    if quote_id > 0:
        out["quote_id"] = quote_id
    hint_text = value.get("hint_date")
    if isinstance(hint_text, str):
        try:
            _dt.date.fromisoformat(hint_text)
            out["hint_date"] = hint_text
        except ValueError:
            pass
    drawn: list[int] = []
    for item in value.get("drawn") or []:
        try:
            qid = int(item)
        except (TypeError, ValueError):
            continue
        if qid > 0:
            drawn.append(qid)
    if drawn:
        out["drawn"] = drawn
    return out
