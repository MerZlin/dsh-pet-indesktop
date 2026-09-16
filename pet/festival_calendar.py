# -*- coding: utf-8 -*-
"""节日提醒：农历 / 24 节气 / 浮动节日换算（纯逻辑层，零 Qt）。

本模块是 **唯一** 接触第三方历法库 `lunar-python` 的地方：上层
`festival.py` / `festival_data.py` 只依赖这里暴露的 `datetime.date` 接口。
这样将来若替换历法后端（例如换库或改成自研天文推算），改动被限制在本文件。

为什么是 `lunar-python`（许可证硬约束，勿换）：
    `lunardate` / `zhdate` 是流传最广的农历实现，但均为 GPL-3.0-or-later
    （同源于 1988 年 Fung F. Lee / Ricky Yeung 的 GPLv2 `lunar` 项目），
    本项目是 MIT，引入即 copyleft 传染。`lunar-python` (6tail) 为 MIT。

lunar-python 的两个易错语义（已实测确认，勿凭直觉改）：
    1. `Lunar.getMonth()` 对**闰月返回负数**（如闰六月 -> -6）。农历节日必须
       排除闰月，否则闰月里的同号日会被误判成节日。
    2. `Lunar.getJieQi()` 仅在节气**当天**返回节气名，其余日返回空串。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from lunar_python import Lunar, Solar

# 24 节气：按公历年内出现顺序（小寒起，冬至止）。名称必须与
# lunar-python 的 getJieQi() 返回值逐字一致，否则匹配不到。
SOLAR_TERMS: tuple[str, ...] = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分",
    "清明", "谷雨", "立夏", "小满", "芒种", "夏至",
    "小暑", "大暑", "立秋", "处暑", "白露", "秋分",
    "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)


@dataclass(frozen=True)
class LunarDate:
    """农历日期。``is_leap`` 为真表示闰月。"""

    year: int
    month: int
    day: int
    is_leap: bool = False


def solar_to_lunar(day: _dt.date) -> LunarDate:
    """公历 -> 农历。闰月由 ``is_leap`` 标记，``month`` 恒为正数。"""
    lunar = Solar.fromYmd(day.year, day.month, day.day).getLunar()
    raw_month = int(lunar.getMonth())
    return LunarDate(
        year=int(lunar.getYear()),
        month=abs(raw_month),
        day=int(lunar.getDay()),
        is_leap=raw_month < 0,
    )


def lunar_to_solar(year: int, month: int, day: int, *, is_leap: bool = False) -> _dt.date:
    """农历 -> 公历。闰月需显式传 ``is_leap=True``（内部用负月份表示）。

    农历月可能只有 29 天，传入不存在的日（如某月三十）会由历法库抛
    ``ValueError``；调用方应只在已知合法的日期上使用。
    """
    signed_month = -abs(month) if is_leap else abs(month)
    solar = Lunar.fromYmd(int(year), signed_month, int(day)).getSolar()
    return _dt.date(int(solar.getYear()), int(solar.getMonth()), int(solar.getDay()))


def solar_term_on(day: _dt.date) -> str:
    """返回该公历日对应的节气名；不是节气日则返回空串。"""
    return str(Solar.fromYmd(day.year, day.month, day.day).getLunar().getJieQi() or "")


def solar_terms_of_year(year: int) -> dict[str, _dt.date]:
    """返回某公历年全部 24 个节气 -> 公历日期（逐日扫描，一年 24 次命中）。"""
    found: dict[str, _dt.date] = {}
    cursor = _dt.date(int(year), 1, 1)
    end = _dt.date(int(year), 12, 31)
    while cursor <= end:
        name = solar_term_on(cursor)
        if name and name not in found:
            found[name] = cursor
        cursor += _dt.timedelta(days=1)
    return found


def spring_festival(lunar_year: int) -> _dt.date:
    """农历某年的正月初一（春节）对应的公历日期。

    春节公历日期恒定落在 1 月 21 日 ~ 2 月 21 日之间，因此
    ``spring_festival(G).year == G`` 恒成立——除夕的推算依赖这一点。
    """
    return lunar_to_solar(lunar_year, 1, 1)


def new_year_eve(gregorian_year: int) -> _dt.date:
    """公历年 ``G`` 内的除夕（腊月最后一天）。

    除夕 = 该公历年春节的前一天。农历腊月可能是 29 或 30 天，直接构造
    「腊月三十」会在小月年份抛错，故一律用「春节 - 1 天」推算。
    """
    return spring_festival(gregorian_year) - _dt.timedelta(days=1)


def easter(year: int) -> _dt.date:
    """西方复活节（Anonymous Gregorian 算法，1583 年起有效）。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> _dt.date:
    """某年某月的第 ``nth`` 个星期 ``weekday``（``weekday``: 周一=0 … 周日=6）。

    用于母亲节（5 月第 2 个周日）与父亲节（6 月第 3 个周日）。
    """
    first = _dt.date(int(year), int(month), 1)
    offset = (int(weekday) - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (int(nth) - 1))
