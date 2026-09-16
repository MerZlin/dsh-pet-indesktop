# -*- coding: utf-8 -*-
"""语音报时（voice_chime）纯逻辑层契约测试。

只覆盖 pet/voice_chime.py 的纯函数接缝（配置默认值 / 逐项清洗 /
六种调度判定 / 距下一报时点秒数 / 槽位幂等 / 文本组装 / edge-tts 参数
格式化 / 缓存键），零 Qt、零线程、零 edge_tts，全部同步断言，不依赖
GUI 事件循环与固定 sleep（对齐 AGENTS.md「CI 优先纪律」）。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from pet.voice_chime import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    DEFAULT_VOLUME,
    SCHEDULE_KEYS,
    build_chime_sentence,
    build_chime_text,
    cache_key,
    chime_slot,
    clean_custom_times,
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
    }
    assert cfg["voice_chime_schedule"] in SCHEDULE_KEYS
    assert cfg["voice_chime_enabled"] is True
    assert cfg["voice_chime_voice"] == DEFAULT_VOICE
    assert cfg["voice_chime_rate"] == DEFAULT_RATE
    assert cfg["voice_chime_pitch"] == DEFAULT_PITCH
    assert cfg["voice_chime_volume"] == DEFAULT_VOLUME


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
    assert cfg["enabled"] is True
    assert cfg["schedule"] == "hourly"
    assert cfg["custom_times"] == frozenset()
    assert cfg["voice"] == DEFAULT_VOICE
    assert cfg["rate"] == DEFAULT_RATE
    assert cfg["pitch"] == DEFAULT_PITCH
    assert cfg["volume"] == DEFAULT_VOLUME


def test_normalize_chime_config_cleans_partial_dict():
    cfg = normalize_chime_config(
        {
            "voice_chime_enabled": False,
            "voice_chime_schedule": "every_15",
            "voice_chime_custom_times": "9:00, 25:00",
            "voice_chime_rate": "999",
            "voice_chime_pitch": "abc",
        }
    )
    assert cfg["enabled"] is False
    assert cfg["schedule"] == "every_15"
    assert cfg["custom_times"] == frozenset({"09:00"})
    assert cfg["rate"] == 100
    assert cfg["pitch"] == DEFAULT_PITCH
    assert cfg["volume"] == DEFAULT_VOLUME


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


def test_pick_quote_prefers_matching_language_pool(monkeypatch):
    """zh 音色主池为中文库、偶插英文；非 zh 反之（随机分支经 monkeypatch 固定）。"""
    monkeypatch.setattr("pet.voice_chime.random.random", lambda: 0.5)
    monkeypatch.setattr("pet.voice_chime.random.choice", lambda pool: pool[0])
    zh_cfg = {"voice": "zh-CN-XiaoxiaoNeural"}
    en_cfg = {"voice": "en-US-GuyNeural"}
    # 0.5 < 0.85：zh → 中文库主池；en → 英文库主池
    assert pick_quote(zh_cfg) in CHINESE_QUOTES
    assert pick_quote(en_cfg) in ENGLISH_QUOTES


def test_pick_quote_fallback_cross_pool(monkeypatch):
    """random >= 0.85 时跨池兜底，但兜底目标仍来自合法池。"""
    monkeypatch.setattr("pet.voice_chime.random.random", lambda: 0.9)
    monkeypatch.setattr("pet.voice_chime.random.choice", lambda pool: pool[0])
    zh_cfg = {"voice": "zh-CN-XiaoxiaoNeural"}
    en_cfg = {"voice": "en-US-GuyNeural"}
    assert pick_quote(zh_cfg) in ENGLISH_QUOTES
    assert pick_quote(en_cfg) in CHINESE_QUOTES


def test_build_chime_sentence_joins_text_and_quote(monkeypatch):
    monkeypatch.setattr("pet.voice_chime.random.random", lambda: 0.5)
    monkeypatch.setattr("pet.voice_chime.random.choice", lambda pool: pool[0])
    now = datetime(2026, 9, 15, 9, 0)
    sentence = build_chime_sentence(now, {"voice": "zh-CN-XiaoxiaoNeural"})
    assert sentence == f"现在是上午九点整。{CHINESE_QUOTES[0]}"


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
