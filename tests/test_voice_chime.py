# -*- coding: utf-8 -*-
"""语音报时（voice_chime）纯逻辑层契约测试。

只覆盖 pet/voice_chime.py 的纯函数接缝（配置默认值 / 逐项清洗 /
六种调度判定 / 距下一报时点秒数 / 槽位幂等 / 文本组装 / edge-tts 参数
格式化 / 缓存键），零 Qt、零线程、零 edge_tts，全部同步断言，不依赖
GUI 事件循环与固定 sleep（对齐 AGENTS.md「CI 优先纪律」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pet.voice_chime import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_SHOW_BUBBLE,
    DEFAULT_SHOW_QUOTE,
    DEFAULT_VOICE,
    DEFAULT_VOLUME,
    SCHEDULE_KEYS,
    VOICE_OPTIONS,
    build_bubble_sentence,
    build_chime_bubble_text,
    build_chime_sentence,
    build_chime_text,
    cache_key,
    chime_index_in_day,
    chime_slot,
    clean_custom_quotes,
    clean_custom_times,
    clean_flag,
    clean_pitch,
    clean_rate,
    clean_schedule,
    clean_voice,
    clean_volume,
    default_chime_config,
    edge_pitch_arg,
    edge_rate_arg,
    is_chime_minute,
    next_chime_in_seconds,
    normalize_chime_config,
    pick_quote,
    quote_day_order,
)
from pet.voice_chime_quotes import CHINESE_QUOTES, ENGLISH_QUOTES


# ---------------------------------------------------------------- 配置默认值与清洗


def test_default_chime_config_has_all_flat_keys():
    """默认配置与 config.py 平铺键一一对应，且调度模式合法。"""
    cfg = default_chime_config()
    assert set(cfg) == {
        "voice_chime_enabled",
        "voice_chime_schedule",
        "voice_chime_custom_times",
        "voice_chime_voice",
        "voice_chime_rate",
        "voice_chime_pitch",
        "voice_chime_volume",
        "voice_chime_show_bubble",
        "voice_chime_show_quote",
        "voice_chime_custom_quotes_zh",
        "voice_chime_custom_quotes_en",
    }
    assert cfg["voice_chime_schedule"] in SCHEDULE_KEYS
    assert cfg["voice_chime_enabled"] is False  # 默认关闭：主动打扰型功能，用户显式开启
    assert cfg["voice_chime_voice"] == DEFAULT_VOICE
    assert cfg["voice_chime_rate"] == DEFAULT_RATE
    assert cfg["voice_chime_pitch"] == DEFAULT_PITCH
    assert cfg["voice_chime_volume"] == DEFAULT_VOLUME
    assert cfg["voice_chime_show_bubble"] is DEFAULT_SHOW_BUBBLE
    assert cfg["voice_chime_show_quote"] is DEFAULT_SHOW_QUOTE
    assert cfg["voice_chime_custom_quotes_zh"] == ""  # 默认留空 → 回退内置中文库
    assert cfg["voice_chime_custom_quotes_en"] == ""  # 默认留空 → 回退内置英文库


@pytest.mark.parametrize(
    "value,expected",
    [
        ("hourly", "hourly"),
        ("every_30", "every_30"),
        ("every_15", "every_15"),
        ("every_5", "every_5"),
        ("every_minute", "every_minute"),
        ("custom", "custom"),
        ("  hourly  ", "hourly"),
        ("bogus", "hourly"),
        ("", "hourly"),
        (None, "hourly"),
        (123, "hourly"),
    ],
)
def test_clean_schedule(value, expected):
    assert clean_schedule(value) == expected


def test_clean_custom_times_normalizes_and_drops_invalid():
    """HH:MM 列表清洗：归一化小时补零、多种分隔符、非法项丢弃。"""
    times = clean_custom_times("9:00, 10:30;11:00  12:15，13:45；14:00")
    assert times == frozenset(
        {
            "09:00",
            "10:30",
            "11:00",
            "12:15",
            "13:45",
            "14:00",
        }
    )


def test_clean_custom_times_rejects_bad_entries():
    assert clean_custom_times("25:00, 12:60, 0:99, abc, 9") == frozenset()
    assert clean_custom_times("") == frozenset()
    assert clean_custom_times(None) == frozenset()
    assert clean_custom_times("00:00, 23:59") == frozenset({"00:00", "23:59"})


def test_clean_voice_defaults_and_truncates():
    assert clean_voice("") == DEFAULT_VOICE
    assert clean_voice(None) == DEFAULT_VOICE
    assert clean_voice("  en-US-GuyNeural  ") == "en-US-GuyNeural"
    long_voice = "x" * 200
    assert clean_voice(long_voice) == "x" * 64


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (10, 10),
        (-20, -20),
        (150, 100),
        (-150, -100),
        ("25", 25),
        ("abc", DEFAULT_RATE),
        (None, DEFAULT_RATE),
        (3.7, 3),
    ],
)
def test_clean_rate_clamps(value, expected):
    assert clean_rate(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (5, 5),
        (-10, -10),
        (60, 50),
        (-60, -50),
        ("12", 12),
        ("abc", DEFAULT_PITCH),
        (None, DEFAULT_PITCH),
        (2.9, 2),
    ],
)
def test_clean_pitch_clamps(value, expected):
    assert clean_pitch(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (80, 80),
        (0, 0),
        (100, 100),
        (150, 100),
        (-5, 0),
        ("60", 60),
        ("abc", DEFAULT_VOLUME),
        (None, DEFAULT_VOLUME),
    ],
)
def test_clean_volume_clamps(value, expected):
    assert clean_volume(value) == expected


def test_normalize_chime_config_none_returns_defaults():
    cfg = normalize_chime_config(None)
    assert cfg["enabled"] is False  # 默认关闭
    assert cfg["schedule"] == "hourly"
    assert cfg["custom_times"] == frozenset()
    assert cfg["voice"] == DEFAULT_VOICE
    assert cfg["rate"] == DEFAULT_RATE
    assert cfg["pitch"] == DEFAULT_PITCH
    assert cfg["volume"] == DEFAULT_VOLUME
    assert cfg["show_bubble"] is DEFAULT_SHOW_BUBBLE
    assert cfg["show_quote"] is DEFAULT_SHOW_QUOTE
    assert cfg["custom_quotes_zh"] == ()
    assert cfg["custom_quotes_en"] == ()


def test_normalize_chime_config_cleans_partial_dict():
    cfg = normalize_chime_config(
        {
            "voice_chime_enabled": False,
            "voice_chime_schedule": "every_15",
            "voice_chime_custom_times": "9:00, 25:00",
            "voice_chime_rate": "999",
            "voice_chime_pitch": "abc",
            "voice_chime_show_bubble": "false",
            "voice_chime_show_quote": "on",
            "voice_chime_custom_quotes_zh": "早呀\n\n  多喝水  \n早呀",
            "voice_chime_custom_quotes_en": "Keep going\nKeep going\n\n",
        }
    )
    assert cfg["enabled"] is False
    assert cfg["schedule"] == "every_15"
    assert cfg["custom_times"] == frozenset({"09:00"})
    assert cfg["rate"] == 100
    assert cfg["pitch"] == DEFAULT_PITCH
    assert cfg["volume"] == DEFAULT_VOLUME
    assert cfg["show_bubble"] is False
    assert cfg["show_quote"] is True
    # 多行自定义台词：去空行/去首尾空白/去重保序
    assert cfg["custom_quotes_zh"] == ("早呀", "多喝水")
    assert cfg["custom_quotes_en"] == ("Keep going",)


def test_clean_flag_parses_common_bools():
    """布尔开关清洗：JSON bool 原样，字符串按常见真值解析，非法回落默认。"""
    assert clean_flag(True) is True
    assert clean_flag(False) is False
    assert clean_flag("true") is True
    assert clean_flag("off") is False
    assert clean_flag("0") is False
    assert clean_flag("1") is True
    assert clean_flag("开") is True
    assert clean_flag("关闭") is False
    assert clean_flag("whatever") is True  # 非法回落默认 True
    assert clean_flag(None, default=False) is False


# ---------------------------------------------------------------- 调度判定（六种模式）


@pytest.mark.parametrize(
    "schedule,minute,expected",
    [
        ("hourly", 0, True),
        ("hourly", 30, False),
        ("hourly", 59, False),
        ("every_30", 0, True),
        ("every_30", 30, True),
        ("every_30", 15, False),
        ("every_15", 0, True),
        ("every_15", 15, True),
        ("every_15", 30, True),
        ("every_15", 45, True),
        ("every_15", 10, False),
        ("every_5", 0, True),
        ("every_5", 5, True),
        ("every_5", 55, True),
        ("every_5", 3, False),
        ("every_minute", 0, True),
        ("every_minute", 42, True),
    ],
)
def test_is_chime_minute_schedule_math(schedule, minute, expected):
    now = datetime(2026, 9, 15, 9, minute, 30)
    cfg = {"schedule": schedule, "custom_times": frozenset()}
    assert is_chime_minute(now, cfg) is expected


def test_is_chime_minute_custom_matches_exact_hhmm():
    cfg = {"schedule": "custom", "custom_times": frozenset({"09:30", "23:59"})}
    assert is_chime_minute(datetime(2026, 9, 15, 9, 30), cfg) is True
    assert is_chime_minute(datetime(2026, 9, 15, 23, 59), cfg) is True
    assert is_chime_minute(datetime(2026, 9, 15, 9, 31), cfg) is False


def test_is_chime_minute_unknown_schedule_is_false():
    now = datetime(2026, 9, 15, 9, 0)
    assert is_chime_minute(now, {"schedule": "bogus"}) is False


# ---------------------------------------------------------------- 距下一报时点秒数


@pytest.mark.parametrize(
    "schedule,now_seconds,expected",
    [
        # hourly：下一个整点（含跨小时）
        ("hourly", (9, 0, 0), 3600),
        ("hourly", (9, 0, 30), 3570),
        ("hourly", (9, 30, 0), 1800),
        ("hourly", (9, 59, 59), 1),
        # every_30：0/30 分整点对齐
        ("every_30", (9, 0, 0), 1800),
        ("every_30", (9, 29, 0), 60),
        ("every_30", (9, 30, 0), 1800),
        ("every_30", (9, 45, 0), 900),
        # every_15
        ("every_15", (9, 0, 0), 900),
        ("every_15", (9, 14, 59), 1),
        ("every_15", (9, 15, 0), 900),
        ("every_15", (9, 50, 0), 600),
        # every_5
        ("every_5", (9, 0, 0), 300),
        ("every_5", (9, 3, 30), 90),
        ("every_5", (9, 55, 0), 300),
    ],
)
def test_next_chime_in_seconds_regular_schedules(schedule, now_seconds, expected):
    hour, minute, second = now_seconds
    now = datetime(2026, 9, 15, hour, minute, second)
    cfg = {"schedule": schedule, "custom_times": frozenset()}
    assert next_chime_in_seconds(now, cfg) == expected


def test_next_chime_in_seconds_every_minute():
    cfg = {"schedule": "every_minute", "custom_times": frozenset()}
    assert next_chime_in_seconds(datetime(2026, 9, 15, 9, 0, 0), cfg) == 60
    assert next_chime_in_seconds(datetime(2026, 9, 15, 9, 0, 1), cfg) == 59
    assert next_chime_in_seconds(datetime(2026, 9, 15, 9, 0, 59), cfg) == 1


def test_next_chime_in_seconds_custom_today_and_tomorrow():
    cfg = {"schedule": "custom", "custom_times": frozenset({"10:00", "14:30"})}
    now = datetime(2026, 9, 15, 9, 15, 30)
    assert next_chime_in_seconds(now, cfg) == 44 * 60 + 30  # 今天 10:00
    now = datetime(2026, 9, 15, 12, 0, 0)
    assert next_chime_in_seconds(now, cfg) == 2 * 3600 + 30 * 60  # 今天 14:30
    now = datetime(2026, 9, 15, 23, 0, 0)
    # 今天所有点已过 → 明天 10:00
    assert next_chime_in_seconds(now, cfg) == 11 * 3600


def test_next_chime_in_seconds_custom_empty_times_is_full_day():
    cfg = {"schedule": "custom", "custom_times": frozenset()}
    now = datetime(2026, 9, 15, 9, 0, 0)
    assert next_chime_in_seconds(now, cfg) == 24 * 3600


# ---------------------------------------------------------------- 槽位幂等


def test_chime_slot_returns_empty_when_not_due():
    cfg = {"schedule": "hourly", "custom_times": frozenset()}
    assert chime_slot(datetime(2026, 9, 15, 9, 30), cfg) == ""


def test_chime_slot_is_stable_and_mode_scoped():
    """同一命中分钟的槽位稳定；不同调度模式同分钟槽位不同（# 后缀）。"""
    now = datetime(2026, 9, 15, 9, 0, 30)
    hourly_cfg = {"schedule": "hourly", "custom_times": frozenset()}
    every_30_cfg = {"schedule": "every_30", "custom_times": frozenset()}
    slot = chime_slot(now, hourly_cfg)
    assert slot == "2026-09-15T09:00#hourly"
    assert chime_slot(now, hourly_cfg) == slot  # 幂等：重复调用结果一致
    assert chime_slot(now, every_30_cfg) == "2026-09-15T09:00#every_30"
    assert chime_slot(now, every_30_cfg) != slot


def test_chime_slot_custom_uses_mode_suffix():
    cfg = {"schedule": "custom", "custom_times": frozenset({"09:30"})}
    slot = chime_slot(datetime(2026, 9, 15, 9, 30, 10), cfg)
    assert slot == "2026-09-15T09:30#custom"


# ---------------------------------------------------------------- 报时文本与台词组装


@pytest.mark.parametrize(
    "dt,expected",
    [
        (datetime(2026, 9, 15, 0, 0), "现在是凌晨十二点整"),
        (datetime(2026, 9, 15, 4, 59), "现在凌晨四点59分"),
        (datetime(2026, 9, 15, 5, 0), "现在是早上五点整"),
        (datetime(2026, 9, 15, 8, 30), "现在早上八点30分"),
        (datetime(2026, 9, 15, 9, 0), "现在是上午九点整"),
        (datetime(2026, 9, 15, 11, 25), "现在上午十一点25分"),
        (datetime(2026, 9, 15, 12, 0), "现在是中午十二点整"),
        (datetime(2026, 9, 15, 13, 5), "现在下午一点05分"),
        (datetime(2026, 9, 15, 17, 59), "现在下午五点59分"),
        (datetime(2026, 9, 15, 18, 0), "现在是晚上六点整"),
        (datetime(2026, 9, 15, 23, 10), "现在晚上十一点10分"),
    ],
)
def test_build_chime_text_periods(dt, expected):
    assert build_chime_text(dt, {}) == expected


def test_build_chime_text_multiple_of_five_minutes():
    assert build_chime_text(datetime(2026, 9, 15, 9, 5), {}) == "现在上午九点05分"
    assert build_chime_text(datetime(2026, 9, 15, 9, 55), {}) == "现在上午九点55分"


# -------------------------------------------- 台词/歌词轮换（每天一套新排列 + 日内顺序取用）


def test_quote_day_order_is_deterministic_permutation():
    """当天排列：同一天恒等、是库的一个置换（不丢条不重复），跨天换新。"""
    pool = tuple(f"q{i}" for i in range(10))
    order = quote_day_order(pool, 739000)
    assert order == quote_day_order(pool, 739000)
    assert sorted(order) == sorted(pool)
    assert order != pool  # 洗过，不是原序
    assert order != quote_day_order(pool, 739001)
    assert quote_day_order((), 739000) == ()
    assert quote_day_order(("only",), 739000) == ("only",)


def test_quote_day_order_gives_a_fresh_order_every_day():
    """用户诉求的机器化守卫：连续 7 天，首句与整套排列都不得重复。

    旧实现里批次 =（日期序数 × 3 + 当日第几段）% 3 —— 乘 3 与取模 3 对消，
    日期被自己抵消，7 天洗出同一个排列、同一天同一时刻永远同一句。本用例
    就是盯着这个回归。
    """
    orders = [quote_day_order(CHINESE_QUOTES, 739000 + i) for i in range(7)]
    assert len(set(orders)) == 7
    assert len({order[0] for order in orders}) >= 6


def test_chime_index_in_day_counts_elapsed_chime_points():
    """日内次序 = 当天 [00:00, now] 命中（含当前）的报时点数量 - 1。"""
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 0), {"schedule": "hourly"}) == 0
    assert chime_index_in_day(datetime(2026, 9, 15, 9, 0), {"schedule": "hourly"}) == 9
    assert chime_index_in_day(datetime(2026, 9, 15, 23, 0), {"schedule": "hourly"}) == 23
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 30), {"schedule": "every_30"}) == 1
    assert chime_index_in_day(datetime(2026, 9, 15, 1, 0), {"schedule": "every_30"}) == 2
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 45), {"schedule": "every_15"}) == 3
    assert chime_index_in_day(datetime(2026, 9, 15, 23, 45), {"schedule": "every_15"}) == 95
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 20), {"schedule": "every_5"}) == 4
    assert chime_index_in_day(datetime(2026, 9, 15, 1, 1), {"schedule": "every_minute"}) == 61
    custom = {"schedule": "custom", "custom_times": frozenset({"00:10", "00:30"})}
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 10), custom) == 0
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 25), custom) == 0  # 未命中点沿用最近一次
    assert chime_index_in_day(datetime(2026, 9, 15, 0, 30), custom) == 1


def test_pick_quote_walks_today_order_and_is_idempotent():
    """日内按当天排列顺序取用；同一分钟内 tick 多次结果一致。"""
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "hourly"}
    order = quote_day_order(CHINESE_QUOTES, datetime(2026, 9, 15).date().toordinal())
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) == order[0]
    assert pick_quote(datetime(2026, 9, 15, 1, 0), cfg) == order[1]
    assert pick_quote(datetime(2026, 9, 15, 2, 0), cfg) == order[2]
    # 用户诉求：每次报时取到不同句子，而不是一句固定一整天
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) != pick_quote(datetime(2026, 9, 15, 1, 0), cfg)
    # 幂等：同一分钟内 tick 多次结果一致
    assert pick_quote(datetime(2026, 9, 15, 1, 0), cfg) == pick_quote(datetime(2026, 9, 15, 1, 40), cfg)


def test_pick_quote_differs_at_same_clock_time_across_days():
    """同一时刻跨天不再撞同一句（旧实现在这里每天完全相同）。"""
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "every_15"}
    quotes = [pick_quote(datetime(2026, 9, 15 + i, 9, 0), cfg) for i in range(7)]
    assert len(set(quotes)) >= 6


def test_pick_quote_covers_a_15min_day_without_repeats():
    """库长盖得住最高频的现实配置：每 15 分钟一天 96 次，一天之内不重句。"""
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "every_15"}
    day = [pick_quote(datetime(2026, 9, 15, 0, 0) + timedelta(minutes=15 * i), cfg) for i in range(96)]
    assert len(set(day)) == 96
    assert len(CHINESE_QUOTES) >= 96
    assert len(ENGLISH_QUOTES) >= 96


def test_quote_libraries_are_unique_and_tts_sized():
    """库里不留空条 / 首尾空白 / 重复项，单条长度适合语音播报。"""
    for pool in (CHINESE_QUOTES, ENGLISH_QUOTES):
        assert len(set(pool)) == len(pool)
        assert all(item == item.strip() and item for item in pool)
    assert max(len(item) for item in CHINESE_QUOTES) <= 40
    assert max(len(item) for item in ENGLISH_QUOTES) <= 100


def test_clean_custom_quotes_splits_trims_and_dedupes():
    """自定义台词清洗：按行拆分、去空行/首尾空白、去重保序、超长截断。"""
    assert clean_custom_quotes("") == ()
    assert clean_custom_quotes(None) == ()
    assert clean_custom_quotes("  \n \n") == ()
    assert clean_custom_quotes("早呀\n\n  多喝水  \n早呀\r\n好梦") == ("早呀", "多喝水", "好梦")
    assert clean_custom_quotes(["早呀", " 早呀 ", "多喝水"]) == ("早呀", "多喝水")
    assert clean_custom_quotes("x" * 200) == ("x" * 120,)  # 单条超长截断


def test_pick_quote_cycles_within_day_when_pool_is_short():
    """库短于当天报时次数时顺序用尽回环，且不跳出当天排列。"""
    cfg = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "schedule": "hourly",
        "custom_quotes_zh": ("第一条", "第二条", "第三条"),
    }
    pool = ("第一条", "第二条", "第三条")
    order = quote_day_order(pool, datetime(2026, 9, 15).date().toordinal())
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) == order[0]
    assert pick_quote(datetime(2026, 9, 15, 1, 0), cfg) == order[1]
    assert pick_quote(datetime(2026, 9, 15, 2, 0), cfg) == order[2]
    assert pick_quote(datetime(2026, 9, 15, 3, 0), cfg) == order[0]  # 用尽回环


def test_pick_quote_zh_and_en_pools_rotate_independently():
    """中英文库各自按当天排列轮换：zh 音色只用中文库，非 zh 音色只用英文库。"""
    zh_cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "hourly"}
    en_cfg = {"voice": "en-US-GuyNeural", "schedule": "hourly"}
    for hour in (0, 1, 8, 17):
        zh_quote = pick_quote(datetime(2026, 9, 15, hour, 0), zh_cfg)
        en_quote = pick_quote(datetime(2026, 9, 15, hour, 0), en_cfg)
        assert zh_quote in CHINESE_QUOTES
        assert en_quote in ENGLISH_QUOTES
        assert zh_quote not in ENGLISH_QUOTES
        assert en_quote not in CHINESE_QUOTES
    assert pick_quote(datetime(2026, 9, 15, 0, 0), zh_cfg) != pick_quote(datetime(2026, 9, 15, 1, 0), zh_cfg)
    assert pick_quote(datetime(2026, 9, 15, 0, 0), en_cfg) != pick_quote(datetime(2026, 9, 15, 1, 0), en_cfg)


def test_pick_quote_custom_overrides_builtin_and_rotates():
    """自定义台词非空时整体替换内置库，并走同一套「每天排列 + 日内顺序」。"""
    cfg = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "schedule": "hourly",
        "custom_quotes_zh": ("第一条", "第二条", "第三条", "第四条", "第五条", "第六条"),
    }
    order = quote_day_order(
        tuple(cfg["custom_quotes_zh"]), datetime(2026, 9, 15).date().toordinal()
    )
    assert set(order) == set(cfg["custom_quotes_zh"])
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) == order[0]
    assert pick_quote(datetime(2026, 9, 15, 1, 0), cfg) == order[1]
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) != pick_quote(datetime(2026, 9, 15, 1, 0), cfg)
    assert pick_quote(datetime(2026, 9, 15, 0, 0), cfg) not in CHINESE_QUOTES


def test_pick_quote_empty_custom_falls_back_to_builtin():
    """自定义留空（空串/空白行/空序列/缺失）时回退内置库。"""
    now = datetime(2026, 9, 15, 1, 0)
    for empty in ("", "   \n\n ", (), [], None):
        assert pick_quote(now, {"voice": "zh-CN-XiaoxiaoNeural", "custom_quotes_zh": empty}) in CHINESE_QUOTES
    assert pick_quote(now, {"voice": "zh-CN-XiaoxiaoNeural"}) in CHINESE_QUOTES
    assert pick_quote(now, {"voice": "en-US-GuyNeural", "custom_quotes_en": ""}) in ENGLISH_QUOTES


def test_pick_quote_custom_is_language_specific():
    """自定义中文只作用于中文音色；英文音色仍独立走英文库。"""
    cfg = {"voice": "en-US-GuyNeural", "custom_quotes_zh": ("仅中文自定义",)}
    assert pick_quote(datetime(2026, 9, 15, 1, 0), cfg) in ENGLISH_QUOTES


def test_build_chime_sentence_joins_text_and_rotated_quote():
    """报时语句 = 报时文本 + 当次台词；同一报时点重复组装结果一致。"""
    now = datetime(2026, 9, 15, 9, 0)
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "hourly"}
    assert build_chime_sentence(now, cfg) == f"现在是上午九点整。{pick_quote(now, cfg)}"
    assert build_chime_sentence(now.replace(minute=30), cfg) == f"现在上午九点30分。{pick_quote(now, cfg)}"
    # 当天排列里的下一次报时换到下一个位置
    assert build_chime_sentence(datetime(2026, 9, 15, 10, 0), cfg) != build_chime_sentence(now, cfg)


def test_build_chime_sentence_uses_custom_quote():
    """自定义台词贯通到报时语句（含非整点报时文本）。"""
    now = datetime(2026, 9, 15, 9, 0)
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "custom_quotes_zh": "该休息啦"}
    assert build_chime_sentence(now, cfg) == f"现在是上午九点整。{pick_quote(now, cfg)}"
    assert build_chime_sentence(now, cfg).endswith("该休息啦")


def test_build_chime_sentence_show_quote_off_returns_text_only():
    """关闭台词/歌词开关后，报时语句只含时间文本、不带台词。"""
    now = datetime(2026, 9, 15, 9, 0)
    sentence = build_chime_sentence(now, {"voice": "zh-CN-XiaoxiaoNeural", "show_quote": False})
    assert sentence == "现在是上午九点整"
    assert "。" not in sentence


@pytest.mark.parametrize(
    "dt,expected",
    [
        (datetime(2026, 9, 15, 0, 5), "现在凌晨 00:05"),
        (datetime(2026, 9, 15, 8, 0), "现在早上 08:00"),
        (datetime(2026, 9, 15, 9, 0), "现在上午 09:00"),
        (datetime(2026, 9, 15, 12, 0), "现在中午 12:00"),
        (datetime(2026, 9, 15, 13, 5), "现在下午 13:05"),
        (datetime(2026, 9, 15, 15, 45), "现在下午 15:45"),
        (datetime(2026, 9, 15, 18, 0), "现在晚上 18:00"),
        (datetime(2026, 9, 15, 23, 59), "现在晚上 23:59"),
    ],
)
def test_build_chime_bubble_text_is_arabic_numerals(dt, expected):
    """气泡报时文本：中文时段词 + 阿拉伯数字 24 小时制（不再中阿混排）。"""
    assert build_chime_bubble_text(dt) == expected


def test_build_bubble_sentence_decouples_from_speech_text():
    """同一时刻：气泡用阿拉伯数字、语音用中文数字，台词取到同一条。"""
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "schedule": "hourly"}
    now = datetime(2026, 9, 15, 15, 45)
    quote = pick_quote(now, cfg)
    assert build_bubble_sentence(now, cfg) == f"现在下午 15:45。{quote}"
    assert build_chime_sentence(now, cfg) == f"现在下午三点45分。{quote}"
    assert "15:45" in build_bubble_sentence(now, cfg)
    assert "15:45" not in build_chime_sentence(now, cfg)


def test_build_bubble_sentence_show_quote_off_returns_bubble_text_only():
    """关闭台词开关：气泡只含阿拉伯数字时间文本。"""
    bubble = build_bubble_sentence(
        datetime(2026, 9, 15, 9, 0),
        {"voice": "zh-CN-XiaoxiaoNeural", "show_quote": False},
    )
    assert bubble == "现在上午 09:00"
    assert "。" not in bubble


def test_voice_options_cover_20plus_bilingual():
    """音色下拉内置 20+ 项，中英文（zh / en）音色均有且标签非空。

    2026-09-22 按微软在线音色表重建清单后，中文音色从 21 降到 14（微软下架了
    晓涵/晓辰/晓梦等 10 款），所以下界按**现实**校准到 12/12，而不是硬留旧数字。
    真实可用性另有运行期兜底：配置音色不在在线表里时自动改用默认音色并提示
    （见 tests/test_voice_chime_edge_voice.py）。
    """
    assert len(VOICE_OPTIONS) >= 20
    zh_voices = [value for value, _label in VOICE_OPTIONS if value.startswith("zh")]
    en_voices = [value for value, _label in VOICE_OPTIONS if value.startswith("en")]
    assert len(zh_voices) >= 12
    assert len(en_voices) >= 12
    assert all(label.strip() for _value, label in VOICE_OPTIONS)
    # 任务点名的关键音色必须在列
    assert "zh-CN-XiaoxiaoNeural" in zh_voices
    assert "zh-CN-YunyangNeural" in zh_voices
    assert "zh-CN-liaoning-XiaobeiNeural" in zh_voices
    assert "zh-CN-shaanxi-XiaoniNeural" in zh_voices
    assert "zh-TW-HsiaoChenNeural" in zh_voices
    assert "zh-HK-HiuGaaiNeural" in zh_voices
    assert "en-US-AriaNeural" in en_voices
    assert "en-US-GuyNeural" in en_voices
    assert "en-US-MichelleNeural" in en_voices


# ---------------------------------------------------------------- 服务层降延迟/气泡（纯逻辑面）


def _make_svc(cfg: dict | None = None):
    """object.__new__ 构造服务实例，只注入预合成/气泡逻辑所需字段（零 Qt）。"""
    from pet.voice_chime_service import VoiceChimeService

    svc = object.__new__(VoiceChimeService)
    svc._cfg = {
        "show_bubble": True,
        "show_quote": True,
        "voice": DEFAULT_VOICE,
        "rate": 0,
        "pitch": 0,
        **(cfg or {}),
    }
    svc._precache_slot = None
    svc._precache_text = None
    svc._precache_bubble = None
    svc._precache_path = None
    svc._pending_bubble = None
    svc._last_slot = None
    svc._busy = False
    svc._busy_since = 0.0
    svc._last_prune_at = 0.0
    svc._synthesis_role = "play"
    svc._bridge = None
    svc._app = None
    svc._cache_dir = None
    svc._player = None
    svc._audio_out = None
    return svc


def test_release_stale_busy_keeps_fresh_lock():
    svc = _make_svc()
    svc._busy = True
    svc._busy_since = float("inf")  # 刚置位：距超时阈值仍有无限余量
    svc._release_stale_busy()
    assert svc._busy is True


def test_release_stale_busy_resets_timed_out_lock():
    svc = _make_svc()
    svc._busy = True
    svc._synthesis_role = "precache"
    svc._busy_since = -1e9  # 远早于超时阈值（模拟 worker 未回调）
    svc._release_stale_busy()
    assert svc._busy is False
    assert svc._synthesis_role == "play"


def test_on_tick_recovers_timed_out_synthesis_lock(monkeypatch):
    svc = _make_svc({**default_chime_config(), "enabled": True})
    monkeypatch.setattr(svc, "_maybe_precache", lambda now: None)
    monkeypatch.setattr(svc, "_fire", lambda sentence, now, bubble=None: None)
    svc._busy = True
    svc._busy_since = -1e9
    svc._on_tick(datetime(2026, 9, 15, 9, 30, 0))
    assert svc._busy is False


def test_prune_cache_throttles_repeated_scans(tmp_path):
    svc = _make_svc()
    svc._cache_dir = tmp_path
    for i in range(5):
        (tmp_path / f"cache-{i}.mp3").write_bytes(b"x")
    svc._prune_cache()  # 首次调用：记录时间戳，上限（200）内不删
    assert svc._last_prune_at > 0.0
    assert len(list(tmp_path.glob("*.mp3"))) == 5
    svc.MAX_CACHE_FILES = 2
    svc._prune_cache()  # 节流窗口内重复调用：直接返回，不再全量扫描
    assert len(list(tmp_path.glob("*.mp3"))) == 5
    svc._last_prune_at = -1e9  # 超出节流窗口 → 真正清理到上限
    svc._prune_cache()
    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_consume_precache_hit_returns_text_and_clears(tmp_path):
    svc = _make_svc()
    audio = tmp_path / "abc.mp3"
    audio.write_bytes(b"fake-mp3")
    svc._precache_slot = "2026-09-15T10:00#hourly"
    svc._precache_text = "现在是上午十点整。加油！"
    svc._precache_bubble = "现在上午 10:00。加油！"
    svc._precache_path = str(audio)
    text = svc._consume_precache("2026-09-15T10:00#hourly")
    assert text == "现在是上午十点整。加油！"
    assert svc._precache_slot is None
    assert svc._precache_text is None
    assert svc._precache_bubble is None
    assert svc._precache_path is None


def test_consume_precache_miss_or_missing_file_falls_back(tmp_path):
    svc = _make_svc()
    svc._precache_slot = "2026-09-15T10:00#hourly"
    svc._precache_text = "现在是上午十点整。"
    svc._precache_path = str(tmp_path / "not-exist.mp3")
    assert svc._consume_precache("2026-09-15T11:00#hourly") is None
    assert svc._precache_path is None
    svc._precache_slot = "2026-09-15T10:00#hourly"
    svc._precache_text = "现在是上午十点整。"
    svc._precache_path = str(tmp_path / "still-not-exist.mp3")
    assert svc._consume_precache("2026-09-15T10:00#hourly") is None
    assert svc._precache_slot is None


def test_maybe_precache_respects_window_busy_and_slot(monkeypatch, tmp_path):
    import pet.voice_chime_service as svc_mod

    started = []

    class DummyWorker:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            started.append(1)

    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(svc_mod, "_TTSWorker", DummyWorker)
    monkeypatch.setattr(
        svc_mod,
        "next_chime_in_seconds",
        lambda _now, _cfg: 30,
    )
    monkeypatch.setattr(
        svc_mod,
        "build_chime_sentence",
        lambda _now, _cfg: "现在是下午三点整。",
    )
    monkeypatch.setattr(svc_mod, "cache_key", lambda _text, _params: "fixed-key")
    monkeypatch.setattr(svc_mod, "chime_slot", lambda dt, _cfg: "2026-09-15T15:00#hourly")
    svc = _make_svc()
    svc._cache_dir = tmp_path
    svc._maybe_precache(datetime(2026, 9, 15, 14, 59, 40))
    assert svc._precache_slot == "2026-09-15T15:00#hourly"
    assert svc._precache_text == "现在是下午三点整。"
    assert len(started) == 1
    # 已占位同槽位不再重复合成
    svc._maybe_precache(datetime(2026, 9, 15, 14, 59, 55))
    assert len(started) == 1
    # busy 时不启动新合成也不占位（窗口 60s 内下个 tick 会再试）
    svc._busy = True
    svc._precache_slot = None
    svc._precache_path = None
    svc._maybe_precache(datetime(2026, 9, 15, 14, 59, 55))
    assert svc._precache_slot is None
    assert len(started) == 1


def test_maybe_precache_outside_window_does_nothing(monkeypatch, tmp_path):
    import pet.voice_chime_service as svc_mod

    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(svc_mod, "next_chime_in_seconds", lambda _now, _cfg: 120)
    monkeypatch.setattr(
        svc_mod,
        "build_chime_sentence",
        lambda _now, _cfg: "x",
    )
    monkeypatch.setattr(svc_mod, "cache_key", lambda _text, _params: "k")
    monkeypatch.setattr(svc_mod, "chime_slot", lambda dt, _cfg: "slot")
    svc = _make_svc()
    svc._cache_dir = tmp_path
    svc._maybe_precache(datetime(2026, 9, 15, 14, 59, 40))
    assert svc._precache_slot is None


def test_bubble_respects_show_bubble_switch():
    calls = []

    class FakeNotifyApp:
        system_notify = staticmethod(lambda *a: calls.append(a))

    svc = _make_svc({"show_bubble": False})
    svc._app = FakeNotifyApp()
    svc._bubble("现在是上午十点整。")
    assert calls == []

    svc2 = _make_svc({"show_bubble": True})
    svc2._app = FakeNotifyApp()
    svc2._bubble("现在是上午十点整。")
    assert calls == [("语音报时", "现在是上午十点整。")]


def test_fire_cache_hit_uses_bubble_sentence(monkeypatch, tmp_path):
    """缓存命中路径：语音文本进缓存键与合成，气泡用阿拉伯数字文本。"""
    import pet.voice_chime_service as svc_mod

    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(svc_mod, "cache_key", lambda _text, _params: "hit")
    seen = []
    svc = _make_svc({"volume": 100})
    svc._cache_dir = tmp_path
    (tmp_path / "hit.mp3").write_bytes(b"fake-mp3")
    monkeypatch.setattr(svc, "_play_and_bubble", lambda path, text, bubble=None: seen.append((text, bubble)))

    svc._fire("现在是下午三点45分。加油", datetime(2026, 9, 15, 15, 45))
    assert len(seen) == 1
    text, bubble = seen[0]
    assert text == "现在是下午三点45分。加油"  # 语音文本不变
    assert bubble is not None and "15:45" in bubble and "三点" not in bubble


def test_play_and_bubble_prefers_bubble_text(monkeypatch):
    """气泡优先用传进来的阿拉伯数字文本；未传时回退语音文本（兼容旧调用）。"""
    import sys
    import types

    stub = types.ModuleType("PySide6.QtCore")
    stub.QUrl = type("QUrl", (), {"fromLocalFile": staticmethod(lambda path: ("file", path))})
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", stub)

    seen = []

    class _Player:
        def setSource(self, url):
            seen.append(("source", url))

        def play(self):
            seen.append(("play", None))

    class _Out:
        def setVolume(self, value):
            seen.append(("volume", value))

    bubbles = []
    svc = _make_svc({"volume": 100})
    svc._player = _Player()
    svc._audio_out = _Out()
    monkeypatch.setattr(svc, "_bubble", lambda text: bubbles.append(text))

    svc._play_and_bubble("cache.mp3", "现在是下午三点45分。加油", "现在下午 15:45。加油")
    assert bubbles == ["现在下午 15:45。加油"]
    assert ("volume", 1.0) in seen  # 音量 100 → 1.0

    svc._play_and_bubble("cache.mp3", "现在是上午九点整。")
    assert bubbles[-1] == "现在是上午九点整。"


def test_on_tick_uses_precached_bubble_text(monkeypatch, tmp_path):
    """命中预合成时，气泡复用预合成阶段算好的阿拉伯数字文本。"""
    import pet.voice_chime_service as svc_mod

    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(svc_mod, "chime_slot", lambda dt, _cfg: "2026-09-15T15:00#hourly")
    seen = []
    svc = _make_svc({"enabled": True, "schedule": "hourly"})
    svc._cache_dir = tmp_path
    audio = tmp_path / "abc.mp3"
    audio.write_bytes(b"fake-mp3")
    svc._precache_slot = "2026-09-15T15:00#hourly"
    svc._precache_text = "现在是下午三点整。加油"
    svc._precache_bubble = "现在下午 15:00。加油"
    svc._precache_path = str(audio)
    monkeypatch.setattr(svc, "_fire", lambda sentence, now, bubble=None: seen.append((sentence, bubble)))

    svc._on_tick(datetime(2026, 9, 15, 15, 0, 5))
    assert seen == [("现在是下午三点整。加油", "现在下午 15:00。加油")]
    assert svc._precache_bubble is None


# ---------------------------------------------------------------- edge-tts 参数格式化


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "+0%"),
        (10, "+10%"),
        (-20, "-20%"),
        (150, "+100%"),  # 钳制上限
        (-150, "-100%"),  # 钳制下限
        ("25", "+25%"),
        ("abc", "+0%"),  # 非法回落默认
    ],
)
def test_edge_rate_arg(value, expected):
    assert edge_rate_arg(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "+0Hz"),
        (5, "+5Hz"),
        (-10, "-10Hz"),
        (60, "+50Hz"),  # 钳制上限
        (-60, "-50Hz"),  # 钳制下限
        ("12", "+12Hz"),
        ("abc", "+0Hz"),  # 非法回落默认
    ],
)
def test_edge_pitch_arg(value, expected):
    assert edge_pitch_arg(value) == expected


# ---------------------------------------------------------------- 缓存键


def test_cache_key_stable_and_content_sensitive():
    cfg = {"voice": "zh-CN-XiaoxiaoNeural", "rate": 0, "pitch": 0}
    key = cache_key("现在是上午九点整", cfg)
    assert isinstance(key, str) and len(key) == 16
    assert cache_key("现在是上午九点整", cfg) == key  # 同文本同配置幂等
    assert cache_key("现在是上午十点整", cfg) != key  # 文本不同键不同
    assert cache_key("现在是上午九点整", {**cfg, "rate": 10}) != key  # rate 影响键
    assert cache_key("现在是上午九点整", {**cfg, "pitch": 5}) != key  # pitch 影响键
    assert cache_key("现在是上午九点整", {**cfg, "voice": "en-US-GuyNeural"}) != key
