# -*- coding: utf-8 -*-
"""语音报时 / 节日提醒共用的纯清洗函数（零 Qt、零网络、零第三方依赖）。

从 ``pet/voice_chime.py`` 抽出来的一层：那份模块为了合成与调度必须导入 TTS
提供者链（edge-tts / mimo / sapi），而节日提醒只需要这里的三个纯函数。
节日页设置项与 ``config.py`` 的归一化都只用到清洗，不需要 TTS——独立成轻模块
后，设置页构造节日域不再拖起整条 TTS 导入链（本机实测启动开销约 -0.3s）。

语义与原实现逐条一致（同正则、同上限、同去重保序规则），
``pet/voice_chime.py`` 仍原样 re-export 这三个名字，调用方无需改。
"""
from __future__ import annotations

import re

# 自定义时间点：HH:MM（小时允许 0/00~23，分钟 00~59）
_CUSTOM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# 自定义单条台词长度上限（超长截断，防误粘长文）
MAX_CUSTOM_QUOTE_LEN = 120


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


def clean_custom_quotes(value) -> tuple[str, ...]:
    """清洗自定义台词/歌词：按行拆分（一行一条），去空行/去重/保序。

    兼容设置页传来的多行字符串（``\\n`` / ``\\r\\n`` / ``\\r``）与已是
    序列的配置值；去除控制字符并按 :data:`MAX_CUSTOM_QUOTE_LEN` 截断单条，
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
        text = text[:MAX_CUSTOM_QUOTE_LEN].strip()
        if text and text not in quotes:
            quotes.append(text)
    return tuple(quotes)