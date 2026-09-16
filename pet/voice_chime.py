# -*- coding: utf-8 -*-
"""语音报时：纯逻辑决策层（零 Qt、零 edge_tts）。

模块顶部为纯函数与纯数据，可在无 GUI 环境直接导入测试；语音合成与播放
由 pet/voice_chime_service.py 承担（后台线程 + QtMultimedia）。

职责：
- 配置默认值与逐项清洗（与 config.py 平铺键对应）；
- 报时调度判定（整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义
  时间点）与“距下一报时点秒数”计算；
- 报时文本组装（“现在是上午九点整” + 随机台词/歌词）；
- edge-tts 参数格式化（rate / pitch）。
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

from .voice_chime_quotes import CHINESE_QUOTES, ENGLISH_QUOTES

# 调度模式键（设置页 ModernSelect 的 data 与此对应）。
SCHEDULE_KEYS = (
    "hourly",  # 整点
    "every_30",  # 每 30 分钟
    "every_15",  # 每 15 分钟
    "every_5",  # 每 5 分钟
    "every_minute",  # 每分钟
    "custom",  # 自定义时间点
)
SCHEDULE_LABELS = {
    "hourly": "整点报时",
    "every_30": "每 30 分钟",
    "every_15": "每 15 分钟",
    "every_5": "每 5 分钟",
    "every_minute": "每分钟",
    "custom": "自定义时间点",
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = 0  # 语速偏移（%），edge-tts 范围约 -100 ~ +100
DEFAULT_PITCH = 0  # 音调偏移（Hz），edge-tts 范围约 -50 ~ +50
DEFAULT_VOLUME = 80  # 播放音量（0-100）

# 常用中文音色提示（设置页占位符用）。
COMMON_VOICES = (
    "zh-CN-XiaoxiaoNeural  晓晓（女，自然）",
    "zh-CN-XiaoyiNeural    晓伊（女，活泼）",
    "zh-CN-YunxiNeural     云希（男，阳光）",
    "zh-CN-YunjianNeural   云健（男，浑厚）",
    "zh-CN-YunyangNeural   云扬（男，新闻）",
    "zh-CN-XiaochenNeural  晓辰（女，电台）",
    "zh-CN-XiaohanNeural   晓涵（女，温柔）",
    "zh-CN-XiaomoNeural    晓墨（女，知性）",
    "zh-CN-XiaoxuanNeural  晓萱（女，甜妹）",
    "zh-CN-XiaoruiNeural   晓睿（女，方言）",
    "zh-CN-XiaoyouNeural   晓悠（女，童声）",
    "en-US-AriaNeural      Aria（英文女声）",
    "en-US-GuyNeural       Guy（英文男声）",
)

_CUSTOM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_RATE_RE = re.compile(r"^[+-]?\d+$")
_PITCH_RE = re.compile(r"^[+-]?\d+$")

# 中文时刻文本（12 小时制）。
_HOUR_CN = (
    "十二",
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
)


def default_chime_config() -> dict:
    """语音报时配置默认值（config.py 顶层平铺键的镜像）。"""
    return {
        "voice_chime_enabled": True,
        "voice_chime_schedule": "hourly",
        "voice_chime_custom_times": "",
        "voice_chime_voice": DEFAULT_VOICE,
        "voice_chime_rate": DEFAULT_RATE,
        "voice_chime_pitch": DEFAULT_PITCH,
        "voice_chime_volume": DEFAULT_VOLUME,
    }


def clean_schedule(value) -> str:
    """清洗调度模式；非法回落整点。"""
    text = str(value or "").strip()
    return text if text in SCHEDULE_KEYS else "hourly"


def clean_custom_times(value) -> frozenset[str]:
    """清洗自定义时间点：逗号/空格/分号分隔的 HH:MM，非法项丢弃。"""
    text = str(value or "").strip()
    parts = re.split(r"[,，;；\s]+", text)
    times: set[str] = set()
    for part in parts:
        part = part.strip()
        match = _CUSTOM_RE.match(part)
        if match:
            times.add(f"{int(match.group(1)):02d}:{match.group(2)}")
    return frozenset(times)


def clean_voice(value) -> str:
    """清洗音色名：仅保留可见字符，超长截断。"""
    text = str(value or "").strip()
    return text[:64] if text else DEFAULT_VOICE


def clean_rate(value) -> int:
    """清洗语速偏移（%）：钳制到 [-100, 100]。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RATE
    return max(-100, min(100, number))


def clean_pitch(value) -> int:
    """清洗音调偏移（Hz）：钳制到 [-50, 50]。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PITCH
    return max(-50, min(50, number))


def clean_volume(value) -> int:
    """清洗音量（0-100）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_VOLUME
    return max(0, min(100, number))


def normalize_chime_config(config) -> dict:
    """从 config 对象读取并清洗语音报时全部键（config 缺失字段用默认值）。"""
    if config is None:
        config = {}
    return {
        "enabled": bool(config.get("voice_chime_enabled", True)),
        "schedule": clean_schedule(config.get("voice_chime_schedule", "hourly")),
        "custom_times": clean_custom_times(config.get("voice_chime_custom_times", "")),
        "voice": clean_voice(config.get("voice_chime_voice", DEFAULT_VOICE)),
        "rate": clean_rate(config.get("voice_chime_rate", DEFAULT_RATE)),
        "pitch": clean_pitch(config.get("voice_chime_pitch", DEFAULT_PITCH)),
        "volume": clean_volume(config.get("voice_chime_volume", DEFAULT_VOLUME)),
    }


def is_chime_minute(now: datetime, cfg: dict) -> bool:
    """当前分钟是否命中报时点（cfg 为 normalize_chime_config 的输出）。"""
    schedule = cfg.get("schedule", "hourly")
    minute = now.minute
    if schedule == "hourly":
        return minute == 0
    if schedule == "every_30":
        return minute % 30 == 0
    if schedule == "every_15":
        return minute % 15 == 0
    if schedule == "every_5":
        return minute % 5 == 0
    if schedule == "every_minute":
        return True
    if schedule == "custom":
        hhmm = f"{now.hour:02d}:{now.minute:02d}"
        return hhmm in cfg.get("custom_times", frozenset())
    return False


def next_chime_in_seconds(now: datetime, cfg: dict) -> int:
    """距下一报时点的秒数（不含当前分钟已过部分，1..3600 或自定义 1..86400）。"""
    if cfg.get("schedule") == "custom":
        times = sorted(cfg.get("custom_times", frozenset()))
        if not times:
            return 3600 * 24
        for hhmm in times:
            hour, minute = (int(part) for part in hhmm.split(":"))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return max(1, int((candidate - now).total_seconds()))
        first = times[0]
        hour, minute = (int(part) for part in first.split(":"))
        tomorrow = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return max(1, int((tomorrow - now).total_seconds()))
    minute = now.minute
    if cfg.get("schedule") == "every_minute":
        return 60 - now.second if now.second else 60
    if cfg.get("schedule") == "hourly":
        step = 60
    elif cfg.get("schedule") == "every_30":
        step = 30
    elif cfg.get("schedule") == "every_15":
        step = 15
    else:  # every_5
        step = 5
    next_minute = minute - (minute % step) + step
    if next_minute >= 60:
        base = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        base = now.replace(minute=next_minute, second=0, microsecond=0)
    return max(1, int((base - now).total_seconds()))


def chime_slot(now: datetime, cfg: dict) -> str:
    """当前分钟命中报时点时返回去重槽位（YYYY-MM-DDTHH:MM#模式），否则空串。

    服务用该槽位做幂等盖戳：同一分钟只报一次（防 tick 重复触发）。
    """
    if not is_chime_minute(now, cfg):
        return ""
    return f"{now.strftime('%Y-%m-%dT%H:%M')}#{cfg.get('schedule', 'hourly')}"


def _period_cn(hour: int) -> str:
    """时段前缀；入参是 24 小时制的 hour（0-23），不是 12 小时制。"""
    return "凌晨" if hour < 5 else "早上" if hour < 9 else "上午" if hour < 12 else ("中午" if hour == 12 else "下午" if hour < 18 else "晚上")


def build_chime_text(now: datetime, cfg: dict) -> str:
    """组装报时文本：中文口播“现在是上午九点整 / 现在上午九点05分”。

    分钟用两位数字（TTS 读作「零五分」）；小时走 12 小时制中文
    （0 点与 12 点都是「十二点」）。rate/pitch 为 TTS 参数、音量在播放侧，
    都不进入正文。
    """
    hour_12 = now.hour % 12 or 12
    hour_cn = _HOUR_CN[hour_12 % 12]
    period = _period_cn(now.hour)
    if now.minute == 0:
        return f"现在是{period}{hour_cn}点整"
    return f"现在{period}{hour_cn}点{now.minute:02d}分"


def pick_quote(cfg: dict) -> str:
    """随机选取一句台词/歌词。

    音色以 zh 开头时以中文库为主（偶插英文），否则以英文库为主，
    避免音色与文本语言完全错配。
    """
    voice = str(cfg.get("voice", DEFAULT_VOICE))
    chinese = voice.lower().startswith("zh")
    pool = CHINESE_QUOTES if chinese else ENGLISH_QUOTES
    if random.random() < 0.85:
        return random.choice(pool)
    return random.choice(ENGLISH_QUOTES if chinese else CHINESE_QUOTES)


def build_chime_sentence(now: datetime, cfg: dict) -> str:
    """完整报时语句：报时文本 + 随机台词/歌词（空格分隔，便于 TTS 停顿）。"""
    return f"{build_chime_text(now, cfg)}。{pick_quote(cfg)}"


def edge_rate_arg(rate) -> str:
    """edge-tts rate 参数：如 +10% / -20% / +0%。"""
    value = clean_rate(rate)
    sign = "+" if value >= 0 else ""
    return f"{sign}{value}%"


def edge_pitch_arg(pitch) -> str:
    """edge-tts pitch 参数：如 +5Hz / -10Hz / +0Hz。"""
    value = clean_pitch(pitch)
    sign = "+" if value >= 0 else ""
    return f"{sign}{value}Hz"


def cache_key(text: str, cfg: dict) -> str:
    """音频缓存文件名键：内容 + 音色 + 语速 + 音调的短哈希。"""
    import hashlib

    raw = f"{text}|{cfg.get('voice')}|{edge_rate_arg(cfg.get('rate'))}|{edge_pitch_arg(cfg.get('pitch'))}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
