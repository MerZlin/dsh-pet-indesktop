# -*- coding: utf-8 -*-
"""语音报时：纯逻辑决策层（零 Qt、零 edge_tts）。

模块顶部为纯函数与纯数据，可在无 GUI 环境直接导入测试；语音合成与播放
由 pet/voice_chime_service.py 承担（后台线程 + QtMultimedia）。

职责：
- 配置默认值与逐项清洗（与 config.py 平铺键对应）；
- 报时调度判定（整点 / 每30分钟 / 每15分钟 / 每5分钟 / 每分钟 / 自定义
  时间点）与“距下一报时点秒数”计算；
- 报时文本组装：语音口播（中文数字，供 TTS 与缓存键）与气泡展示（阿拉伯数字）
  两套文本解耦生成；
- 台词/歌词轮换：库按 8 小时周期整体换批，同一周期内按序轮换取不同条目；
- 自定义台词/歌词解析（留空回退内置库）；
- edge-tts 参数格式化（rate / pitch）。
"""

from __future__ import annotations

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
DEFAULT_SHOW_BUBBLE = True  # 报时气泡开关
DEFAULT_SHOW_QUOTE = True  # 台词/歌词开关

# 台词/歌词轮换：库按本地时间每 8 小时整体换一批（周期 0-8 / 8-16 / 16-24）。
# 库按序均分为 _QUOTE_BATCHES_PER_DAY（=3）批，周期序号取模决定当前批次，跨周期
# 即切到新批次；同一周期内每次报时在批次内按顺序轮换取下一条（用尽回环），因此
# 同周期内多次报时听到的是不同句子，而非“一句固定 8 小时”。周期序号跨天连续
# 递增，故跨天也保持可预期的换批顺序。
QUOTE_ROTATION_HOURS = 8
_QUOTE_SLOTS_PER_DAY = 24 // QUOTE_ROTATION_HOURS
_QUOTE_BATCHES_PER_DAY = _QUOTE_SLOTS_PER_DAY  # 台词库均分批数（一天 3 个周期 = 3 批）
_MAX_CUSTOM_QUOTE_LEN = 120  # 自定义单条台词长度上限（超长截断，防误粘长文）

# edge-tts 中英文音色下拉列表（value=音色名，label=友好中文标签）。
# 2026-09-22 按微软在线音色表（``edge_tts.list_voices()``，322 款）重建：老清单里
# 有 10 款中文音色已被下架（晓涵/晓辰/晓梦/晓墨/晓秋/晓睿/晓双/晓萱/晓颜/晓悠），
# 留着它们只会让用户选到「合成没有声音」；这里同时补上了在线的新音色。
# 服务层另有「配置音色不在在线表里 → 自动改用默认音色并提示」的兜底（见
# voice_chime_service.resolve_voice），所以这份清单是快照而非唯一真相。
VOICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("zh-CN-XiaoxiaoNeural", "晓晓（女 · 自然）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女 · 活泼）"),
    ("zh-CN-YunjianNeural", "云健（男 · 浑厚）"),
    ("zh-CN-YunxiNeural", "云希（男 · 阳光）"),
    ("zh-CN-YunxiaNeural", "云夏（男 · 少年）"),
    ("zh-CN-YunyangNeural", "云扬（男 · 新闻播报）"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北（女 · 东北腔）"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮（女 · 陕西腔）"),
    ("zh-TW-HsiaoChenNeural", "曉臻（女 · 台灣腔）"),
    ("zh-TW-HsiaoYuNeural", "曉雨（女 · 台灣腔）"),
    ("zh-TW-YunJheNeural", "雲哲（男 · 台灣腔）"),
    ("zh-HK-HiuGaaiNeural", "曉佳（女 · 粵語）"),
    ("zh-HK-HiuMaanNeural", "曉文（女 · 粵語）"),
    ("zh-HK-WanLungNeural", "雲龍（男 · 粵語）"),
    ("en-US-AriaNeural", "Aria（英文女声 · 自然）"),
    ("en-US-JennyNeural", "Jenny（英文女声 · 甜美）"),
    ("en-US-MichelleNeural", "Michelle（英文女声 · 温暖）"),
    ("en-US-AnaNeural", "Ana（英文女声 · 童声）"),
    ("en-US-AvaNeural", "Ava（英文女声 · 新一代）"),
    ("en-US-EmmaNeural", "Emma（英文女声 · 新一代）"),
    ("en-US-GuyNeural", "Guy（英文男声 · 沉稳）"),
    ("en-US-ChristopherNeural", "Christopher（英文男声 · 沉稳）"),
    ("en-US-EricNeural", "Eric（英文男声 · 年轻）"),
    ("en-US-RogerNeural", "Roger（英文男声 · 成熟）"),
    ("en-US-AndrewNeural", "Andrew（英文男声 · 新一代）"),
    ("en-US-BrianNeural", "Brian（英文男声 · 新一代）"),
    ("en-US-SteffanNeural", "Steffan（英文男声 · 沉稳）"),
    ("en-US-AvaMultilingualNeural", "Ava（多语言女声）"),
    ("en-US-EmmaMultilingualNeural", "Emma（多语言女声）"),
    ("en-US-AndrewMultilingualNeural", "Andrew（多语言男声）"),
    ("en-US-BrianMultilingualNeural", "Brian（多语言男声）"),
    ("en-GB-SoniaNeural", "Sonia（英音女声）"),
    ("en-GB-RyanNeural", "Ryan（英音男声）"),
    ("en-GB-LibbyNeural", "Libby（英音女声）"),
    ("en-GB-MaisieNeural", "Maisie（英音女童）"),
    ("en-GB-ThomasNeural", "Thomas（英音男声）"),
)

#: 2026-09-22 实测已从微软在线音色表下线的音色（老清单里的 10 款）。只用于把
#: 「你配的那个音色已经没了」说成人话——用户看到的应当是「晓涵」而不是音色 id。
DEPRECATED_VOICE_LABELS = {
    "zh-CN-XiaochenNeural": "晓辰",
    "zh-CN-XiaohanNeural": "晓涵",
    "zh-CN-XiaomengNeural": "晓梦",
    "zh-CN-XiaomoNeural": "晓墨",
    "zh-CN-XiaoqiuNeural": "晓秋",
    "zh-CN-XiaoruiNeural": "晓睿",
    "zh-CN-XiaoshuangNeural": "晓双",
    "zh-CN-XiaoxuanNeural": "晓萱",
    "zh-CN-XiaoyanNeural": "晓颜",
    "zh-CN-XiaoyouNeural": "晓悠",
}


def voice_label(value) -> str:
    """音色的中文名：内置清单 → 已下线映射 → 原样返回 id。给用户看的提示用它。"""
    voice = str(value or "").strip()
    for name, label in VOICE_OPTIONS:
        if name == voice:
            return label.split("（")[0]
    return DEPRECATED_VOICE_LABELS.get(voice, voice)

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
        "voice_chime_enabled": False,
        "voice_chime_schedule": "hourly",
        "voice_chime_custom_times": "",
        "voice_chime_voice": DEFAULT_VOICE,
        "voice_chime_rate": DEFAULT_RATE,
        "voice_chime_pitch": DEFAULT_PITCH,
        "voice_chime_volume": DEFAULT_VOLUME,
        "voice_chime_show_bubble": DEFAULT_SHOW_BUBBLE,
        "voice_chime_show_quote": DEFAULT_SHOW_QUOTE,
        "voice_chime_custom_quotes_zh": "",  # 自定义中文台词/歌词（一行一条，留空用内置库）
        "voice_chime_custom_quotes_en": "",  # 自定义英文台词/歌词（一行一条，留空用内置库）
    }


def clean_flag(value, default: bool = True) -> bool:
    """清洗布尔开关：JSON bool 原样返回，字符串按常见真值解析，非法回落默认。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "开", "开启"):
        return True
    if text in ("0", "false", "no", "off", "关", "关闭"):
        return False
    return default


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


def clean_custom_quotes(value) -> tuple[str, ...]:
    """清洗自定义台词/歌词：按行拆分（一行一条），去空行/去重/保序。

    兼容设置页传来的多行字符串（``\\n`` / ``\\r\\n`` / ``\\r``）与已是
    序列的配置值；去除控制字符并按 ``_MAX_CUSTOM_QUOTE_LEN`` 截断单条，
    保证进入 TTS 的文本干净。返回空元组表示“未自定义”，调用方回退内置库。
    """
    if value is None:
        return ()
    if isinstance(value, (tuple, list, set)):
        raw_lines = [str(item) for item in value]
    else:
        raw_lines = re.split(r"\r\n|\r|\n", str(value))
    quotes: list[str] = []
    for line in raw_lines:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", line).strip()
        if not text:
            continue
        text = text[:_MAX_CUSTOM_QUOTE_LEN].strip()
        if text and text not in quotes:
            quotes.append(text)
    return tuple(quotes)


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
        "enabled": bool(config.get("voice_chime_enabled", False)),
        "schedule": clean_schedule(config.get("voice_chime_schedule", "hourly")),
        "custom_times": clean_custom_times(config.get("voice_chime_custom_times", "")),
        "voice": clean_voice(config.get("voice_chime_voice", DEFAULT_VOICE)),
        "rate": clean_rate(config.get("voice_chime_rate", DEFAULT_RATE)),
        "pitch": clean_pitch(config.get("voice_chime_pitch", DEFAULT_PITCH)),
        "volume": clean_volume(config.get("voice_chime_volume", DEFAULT_VOLUME)),
        "show_bubble": clean_flag(config.get("voice_chime_show_bubble", DEFAULT_SHOW_BUBBLE), DEFAULT_SHOW_BUBBLE),
        "show_quote": clean_flag(config.get("voice_chime_show_quote", DEFAULT_SHOW_QUOTE), DEFAULT_SHOW_QUOTE),
        "custom_quotes_zh": clean_custom_quotes(config.get("voice_chime_custom_quotes_zh", "")),
        "custom_quotes_en": clean_custom_quotes(config.get("voice_chime_custom_quotes_en", "")),
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


def quote_slot_serial(now: datetime) -> int:
    """台词/歌词轮换的全局 8 小时周期序号（本地时间，跨天连续递增）。

    每个自然日固定 ``_QUOTE_SLOTS_PER_DAY``（=3）个 8 小时周期，序号由
    “日期序数 × 周期数 + 当日第几个周期”得到，因此相邻周期序号恰好差 1
    （含跨天 16-24 → 次日 0-8），取模即可实现顺序换批、跨天不跳乱。
    """
    return now.date().toordinal() * _QUOTE_SLOTS_PER_DAY + now.hour // QUOTE_ROTATION_HOURS


def chime_index_in_period(now: datetime, cfg: dict) -> int:
    """当前 8 小时周期内的报时次序（0 起，含当前这一次报时）。

    从周期起点（0 点 / 8 点 / 16 点）逐分钟回溯统计命中的报时点个数；
    同一周期内每报一次该值 +1，批次内据此顺序轮换取不同条目。周期内尚无
    更早报时（或自定义时间点集中在其它周期）时返回 0，取批次首条。
    """
    start = now.replace(
        hour=now.hour // QUOTE_ROTATION_HOURS * QUOTE_ROTATION_HOURS,
        minute=0,
        second=0,
        microsecond=0,
    )
    index = -1
    cursor = start
    while cursor <= now:
        if is_chime_minute(cursor, cfg):
            index += 1
        cursor += timedelta(minutes=1)
    return max(index, 0)


def split_quote_batches(pool, batches: int = _QUOTE_BATCHES_PER_DAY) -> tuple[tuple[str, ...], ...]:
    """把台词库按序均分为若干批（前几批各多 1 条），空批自动过滤。

    用于“每 8 小时整体换一批”：周期序号取模决定当前批次。条目数少于批数
    时会出现空批，过滤后保证取批永远不会命中空批（自定义仅 1 条时只有 1 批）。
    """
    items = tuple(pool)
    if not items:
        return ()
    count = max(1, int(batches))
    size, extra = divmod(len(items), count)
    result: list[tuple[str, ...]] = []
    pos = 0
    for i in range(count):
        take = size + (1 if i < extra else 0)
        if take:
            result.append(items[pos : pos + take])
        pos += take
    return tuple(result)


def pick_quote(now: datetime, cfg: dict) -> str:
    """按“每 8 小时整体换一批 + 周期内按序轮换”选取一句台词/歌词。

    选取规则：
    1. 音色以 zh 开头用中文库，否则用英文库；对应语言自定义条目非空时整体
       替换内置库（留空回退内置库）；
    2. 库按序均分为 ``_QUOTE_BATCHES_PER_DAY`` 批，周期序号
       :func:`quote_slot_serial` 取模决定当前批次 —— 跨周期即换新批次；
    3. 周期内第 :func:`chime_index_in_period` 次报时取批次内第 N 条（顺序
       轮换、用尽回环），因此同一周期内多次报时能听到不同句子。
    """
    chinese = str(cfg.get("voice", DEFAULT_VOICE)).lower().startswith("zh")
    custom = clean_custom_quotes(cfg.get("custom_quotes_zh" if chinese else "custom_quotes_en"))
    pool = custom or (CHINESE_QUOTES if chinese else ENGLISH_QUOTES)
    if not pool:
        return ""
    batch_list = split_quote_batches(pool)
    if not batch_list:
        return ""
    batch = batch_list[quote_slot_serial(now) % len(batch_list)]
    return batch[chime_index_in_period(now, cfg) % len(batch)]


def build_chime_sentence(now: datetime, cfg: dict) -> str:
    """完整报时语句：报时文本 + 轮换台词/歌词（句号分隔，便于 TTS 停顿）。

    配置 ``show_quote`` 为 False 时仅返回报时文本（用户关闭台词/歌词）。
    """
    text = build_chime_text(now, cfg)
    if not clean_flag(cfg.get("show_quote", DEFAULT_SHOW_QUOTE), DEFAULT_SHOW_QUOTE):
        return text
    quote = pick_quote(now, cfg)
    return f"{text}。{quote}" if quote else text


def build_chime_bubble_text(now: datetime) -> str:
    """气泡展示用的报时文本：以阿拉伯数字为主（如“现在下午 15:45”）。

    与 :func:`build_chime_text`（中文数字口播，供 TTS 与缓存键）解耦：气泡
    只做视觉展示，用 24 小时制阿拉伯数字 + 中文时段词，避免“下午一点05分”
    这类阿拉伯数字与中文数字混排。
    """
    return f"现在{_period_cn(now.hour)} {now.hour:02d}:{now.minute:02d}"


def build_bubble_sentence(now: datetime, cfg: dict) -> str:
    """气泡完整文本：阿拉伯数字报时 + 台词/歌词（与语音口播文本解耦）。

    语音仍用 :func:`build_chime_sentence`（中文数字口播，作为合成输入与缓存键）；
    两者在同一时刻取到同一条台词，仅时间部分表示不同。``show_quote`` 关闭时
    只返回气泡报时文本。
    """
    text = build_chime_bubble_text(now)
    if not clean_flag(cfg.get("show_quote", DEFAULT_SHOW_QUOTE), DEFAULT_SHOW_QUOTE):
        return text
    quote = pick_quote(now, cfg)
    return f"{text}。{quote}" if quote else text


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
