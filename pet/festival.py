# -*- coding: utf-8 -*-
"""节日提醒：纯逻辑层（零 Qt / 零 GUI，可脱离界面直接单测）。

职责：配置清洗、今日节日判定、文案挑选、提醒时间计算、提醒槽位幂等。
不负责：定时器、线程、播放、气泡落地（那些在 festival_service.py）。

配置键（config.py 顶层平铺，共 11 个）：
    festival_reminder_enabled / festival_reminder_cn /
    festival_reminder_solar_terms / festival_reminder_west /
    festival_reminder_mode / festival_reminder_count /
    festival_reminder_times / festival_reminder_show_quote /
    festival_reminder_speak / festival_custom_quotes_cn /
    festival_custom_quotes_west

语音播报（``festival_reminder_speak``）**不自带音频管线**：它复用语音报时服务的
``speak()`` 通道（音色/语速/音调/音量同报时），从而在结构上不可能与报时叠音；
同一分钟两者都到点时，报时经 ``should_speak_at()`` 让位（见 festival_service）。

**总开关默认关闭**：节日提醒是"主动打扰"型功能，升级后不应突然冒出来，
由用户显式开启。

今日判定采用「日期 -> 节日」方向（见 ``festivals_on``），而不是先算
「节日 -> 日期」：后者要先解决"农历腊月初八落在哪个公历年"这类跨年归属
问题，容易出错；前者对给定的一天逐条比对规则，没有归属歧义。
"""

from __future__ import annotations

import datetime as _dt

from .festival_calendar import (
    easter,
    new_year_eve,
    nth_weekday,
    solar_term_on,
    solar_to_lunar,
)
from .festival_data import (
    CATEGORY_CN,
    CATEGORY_ORDER,
    CATEGORY_SOLAR_TERM,
    CATEGORY_WEST,
    FESTIVALS,
    Festival,
    KIND_EASTER,
    KIND_LUNAR,
    KIND_LUNAR_LAST,
    KIND_NTH_WEEKDAY,
    KIND_SOLAR,
    KIND_SOLAR_TERM,
)
from .festival_quotes_cn import QUOTES_CN
from .festival_quotes_west import QUOTES_WEST

# 受版权保护的流行文化台词库（电影/游戏）。刻意与公有领域库
# （festival_quotes_west.py）**分文件物理隔离**：需要"纯公有领域"发行版时，
# 删除对应模块文件即可，无需逐句挑选。
#
# 这里用**真实 import 语句**包在 try/except 里，而不是 importlib 动态导入：
# PyInstaller 只对字节码里的 import 语句做静态分析，importlib 动态导入的模块
# 不会被收集进包，打包后必然 ImportError。try/except 形式两者兼得——静态可收集，
# 且文件被删除时降级为空表而不是启动崩溃。
try:  # pragma: no cover - 剥离合规版本时该模块不存在
    from .festival_quotes_west_movie import QUOTES_WEST_MOVIE
except ImportError:  # pragma: no cover
    QUOTES_WEST_MOVIE = {}

try:  # pragma: no cover - 剥离合规版本时该模块不存在
    from .festival_quotes_west_game import QUOTES_WEST_GAME
except ImportError:  # pragma: no cover
    QUOTES_WEST_GAME = {}

try:  # pragma: no cover - 剥离合规版本时该模块不存在
    from .festival_quotes_west_song import QUOTES_WEST_SONG
except ImportError:  # pragma: no cover
    QUOTES_WEST_SONG = {}

# 复用语音报时的清洗实现，避免在同一包内写第二份同语义清洗逻辑：
# clean_flag（布尔真值解析）、clean_custom_times（HH:MM 集合）、
# clean_custom_quotes（多行文本去控制字符/去重保序/单条截断）。
from .voice_chime import clean_custom_quotes, clean_custom_times, clean_flag

# —— 提醒方式（二选一）——
MODE_TIMES = "times"
MODE_CUSTOM = "custom"
MODE_KEYS: tuple[str, ...] = (MODE_TIMES, MODE_CUSTOM)
MODE_LABELS: dict[str, str] = {
    MODE_TIMES: "按提醒次数",
    MODE_CUSTOM: "自定义提醒时间",
}
DEFAULT_MODE = MODE_TIMES

# —— 提醒次数（MODE_TIMES）——
DEFAULT_COUNT = 2
MIN_COUNT = 1
MAX_COUNT = 6

# —— 自定义时间点（MODE_CUSTOM）——
DEFAULT_TIMES = "09:00"

# 次数模式的分布窗口：把 count 次提醒均匀铺在 09:00–21:00。
# count=1 -> 09:00；2 -> 09:00/21:00；3 -> 09:00/15:00/21:00。
_WINDOW_START_MINUTE = 9 * 60
_WINDOW_END_MINUTE = 21 * 60

DEFAULT_SHOW_QUOTE = True

#: 手动「今日节日」入口在当天无任何命中时的提示文案（自动提醒不会用它：
#: 自动路径下无命中直接不打扰，只有用户主动点击才需要给出明确回应）。
NO_FESTIVAL_TEXT = "今天没有特别的节日或节气。"


def _fmt_minutes(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clean_mode(value) -> str:
    """清洗提醒方式；非法回落「按提醒次数」。"""
    text = str(value or "").strip()
    return text if text in MODE_KEYS else DEFAULT_MODE


def clean_count(value) -> int:
    """清洗提醒次数：非整数或非法值回落默认，结果钳制在 [MIN_COUNT, MAX_COUNT]。"""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(MIN_COUNT, min(MAX_COUNT, number))


def normalize_festival_config(config) -> dict:
    """把任意来源的配置清洗成内部统一结构（非法值一律回落默认，绝不抛异常）。

    接受 ``Config`` 对象或普通 dict；两者都只做读取。
    """
    get = getattr(config, "get", None)
    if get is None:
        source: dict = config or {}
        get = source.get
    return {
        "enabled": clean_flag(get("festival_reminder_enabled", False), False),
        "cn": clean_flag(get("festival_reminder_cn", True), True),
        "solar_terms": clean_flag(get("festival_reminder_solar_terms", True), True),
        "west": clean_flag(get("festival_reminder_west", True), True),
        "mode": clean_mode(get("festival_reminder_mode", DEFAULT_MODE)),
        "count": clean_count(get("festival_reminder_count", DEFAULT_COUNT)),
        "times": clean_custom_times(get("festival_reminder_times", DEFAULT_TIMES)),
        "show_quote": clean_flag(
            get("festival_reminder_show_quote", DEFAULT_SHOW_QUOTE), DEFAULT_SHOW_QUOTE
        ),
        "speak": clean_flag(get("festival_reminder_speak", False), False),
        "custom_quotes_cn": clean_custom_quotes(get("festival_custom_quotes_cn", "")),
        "custom_quotes_west": clean_custom_quotes(get("festival_custom_quotes_west", "")),
    }


def enabled_categories(cfg: dict) -> frozenset[str]:
    """按三个分类开关返回启用的类别集合。"""
    result = set()
    if cfg.get("cn"):
        result.add(CATEGORY_CN)
    if cfg.get("solar_terms"):
        result.add(CATEGORY_SOLAR_TERM)
    if cfg.get("west"):
        result.add(CATEGORY_WEST)
    return frozenset(result)


def is_festival_enabled(festival: Festival, cfg: dict) -> bool:
    """节日是否启用：其任一类别被开启即启用。

    清明节同时带 ``solar_term`` 与 ``cn`` 两个类别，因此「中国节日」或
    「24 节气」任一开关打开都会命中它，符合直觉。
    """
    return bool(set(festival.categories) & enabled_categories(cfg))


def _matches(festival: Festival, day: _dt.date) -> bool:
    """判断某节日规则是否命中给定公历日。"""
    kind = festival.kind
    if kind == KIND_SOLAR:
        return (day.month, day.day) == (festival.month, festival.day)
    if kind == KIND_LUNAR:
        lunar = solar_to_lunar(day)
        # 闰月必须排除：闰六月初六不是七夕（七夕是七月初七），但同号闰月
        # 会与正月的同号日混淆，故一律要求非闰月。
        if lunar.is_leap:
            return False
        return (lunar.month, lunar.day) == (festival.lunar_month, festival.lunar_day)
    if kind == KIND_LUNAR_LAST:
        return day == new_year_eve(day.year)
    if kind == KIND_SOLAR_TERM:
        return solar_term_on(day) == festival.term
    if kind == KIND_EASTER:
        return day == easter(day.year)
    if kind == KIND_NTH_WEEKDAY:
        return day == nth_weekday(day.year, festival.month, festival.weekday, festival.nth)
    return False


def festivals_on(day: _dt.date, cfg: dict) -> tuple[Festival, ...]:
    """返回该公历日命中的全部节日/节气，按「中国节日 -> 节气 -> 西方节日」排序。

    同一天可能命中多个（例如腊八节与小寒同日、除夕与立春同日）。
    """
    hits = [f for f in FESTIVALS if is_festival_enabled(f, cfg) and _matches(f, day)]
    order = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    hits.sort(key=lambda f: (min(order.get(c, 99) for c in f.categories), f.name))
    return tuple(hits)


def reminder_times(cfg: dict) -> tuple[str, ...]:
    """返回当天的提醒时间点（HH:MM，升序，去重）。

    - ``custom`` 模式：取用户自定义时间点；若为空则回落 ``DEFAULT_TIMES``
      （避免"选了自定义却没填"导致永不提醒这种静默失效）。
    - ``times`` 模式：把 ``count`` 次提醒均匀铺在 09:00–21:00 窗口内。
    """
    if cfg.get("mode") == MODE_CUSTOM:
        times = tuple(sorted(cfg.get("times") or ()))
        return times or (DEFAULT_TIMES,)
    count = int(cfg.get("count", DEFAULT_COUNT))
    count = max(MIN_COUNT, min(MAX_COUNT, count))
    if count == 1:
        return (_fmt_minutes(_WINDOW_START_MINUTE),)
    span = _WINDOW_END_MINUTE - _WINDOW_START_MINUTE
    step = span / (count - 1)
    return tuple(
        _fmt_minutes(round(_WINDOW_START_MINUTE + index * step)) for index in range(count)
    )


def quote_pool(festival: Festival, cfg: dict) -> tuple[str, ...]:
    """返回该节日生效的文案池：内置库 + 用户自定义追加。

    西方节日的顺序为：公有领域引文 -> 电影台词 -> 游戏台词 -> 用户自定义。
    自定义文案是**追加**而非整体替换：内置库已按节日氛围逐条选材，用户
    追加自己的句子时不应被迫丢失这些内容。中国节日与节气用中文库。
    """
    categories = set(festival.categories)
    if CATEGORY_WEST in categories and not (categories & {CATEGORY_CN, CATEGORY_SOLAR_TERM}):
        builtin = (
            tuple(QUOTES_WEST.get(festival.id, ()))
            + tuple(QUOTES_WEST_MOVIE.get(festival.id, ()))
            + tuple(QUOTES_WEST_GAME.get(festival.id, ()))
            + tuple(QUOTES_WEST_SONG.get(festival.id, ()))
        )
        custom = cfg.get("custom_quotes_west") or ()
    else:
        builtin = tuple(QUOTES_CN.get(festival.id, ()))
        custom = cfg.get("custom_quotes_cn") or ()
    return builtin + tuple(custom)


def pick_quote(festival: Festival, day: _dt.date, index: int, cfg: dict) -> str:
    """确定性地挑一条文案。

    以「日期序号 + 当天第几次提醒」取模，保证：同一天同一时刻结果稳定
    （可测试、可复现），而当天多次提醒会轮到不同句子。
    """
    pool = quote_pool(festival, cfg)
    if not pool:
        return ""
    return pool[(day.toordinal() + int(index)) % len(pool)]


def build_festival_text(day: _dt.date, cfg: dict, index: int = 0) -> str:
    """组装提醒文案：先点明今天是什么节日/节气，再附一句氛围匹配的文案。

    无命中或分类全关时返回空串（调用方据此跳过本次提醒）。
    """
    hits = festivals_on(day, cfg)
    if not hits:
        return ""
    names = "、".join(festival.name for festival in hits)
    text = f"今天是{names}。"
    if cfg.get("show_quote", DEFAULT_SHOW_QUOTE):
        quote = pick_quote(hits[0], day, index, cfg)
        if quote:
            text += quote
    return text


def festival_label(day: _dt.date, cfg: dict) -> str:
    """当日节目的简短标签（供菜单项显示）；无命中时返回空串。"""
    hits = festivals_on(day, cfg)
    if not hits:
        return ""
    return "、".join(festival.name for festival in hits)


def reminder_slot(now: _dt.datetime, cfg: dict) -> str:
    """到点提醒的幂等槽位；不该提醒时返回空串。

    槽位形如 ``"2026-02-17T09:00"``。服务用它盖戳，保证同一天同一提醒
    时间只播报一次——免疫 tick 抖动与服务重启造成的重复触发。
    """
    day = now.date()
    hhmm = now.strftime("%H:%M")
    if hhmm not in reminder_times(cfg):
        return ""
    if not festivals_on(day, cfg):
        return ""
    return f"{day.isoformat()}T{hhmm}"


def startup_slot(now: _dt.datetime, cfg: dict) -> str:
    """服务启动时的补提醒槽位。

    桌宠不保证常驻：用户可能中午才开机，此时当天的 09:00 提醒点已经错过。
    若当天确有节日且当前已过首个提醒时间，返回一个独立槽位让服务立即补
    报一次（与正常到点槽位互不压制，晚间那次仍会照常提醒）。
    """
    day = now.date()
    if not festivals_on(day, cfg):
        return ""
    times = reminder_times(cfg)
    if not times:
        return ""
    if now.strftime("%H:%M") < times[0]:
        return ""
    return f"{day.isoformat()}#startup"
