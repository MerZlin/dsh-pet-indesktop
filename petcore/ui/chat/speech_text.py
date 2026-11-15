"""把聊天回复清成可直接朗读的纯文本（与 ai.chat 扩展的 reply_voice 同口径）。"""

from __future__ import annotations

import re

MAX_SPEECH_CHARS = 4000

_CODE_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
_BULLET = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d{1,2}[.)])[ \t]+", re.M)
_TABLE_RULE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)+\|?$", re.M
)
_EMPHASIS = re.compile(r"\*\*|__|~~|(?<!\*)\*(?!\*)|(?<!_)_(?!_)")
_ANGLE_TAG = re.compile(r"<[^>\n]{1,120}>")
_SPACES = re.compile(r"[ \t]{2,}")


def clean_for_speech(text: str, *, limit: int = MAX_SPEECH_CHARS) -> str:
    """去掉 Markdown 标记，压平空白，并截到 ``limit`` 字（超长明确截断）。"""
    value = str(text or "")
    value = _CODE_BLOCK.sub(" 代码略 ", value)
    value = _IMAGE.sub(" ", value)
    value = _LINK.sub(r"\1", value)
    value = _INLINE_CODE.sub(r"\1", value)
    value = _TABLE_RULE.sub("", value)
    value = _HEADING.sub("", value)
    value = _QUOTE.sub("", value)
    value = _BULLET.sub("", value)
    value = _EMPHASIS.sub("", value)
    value = _ANGLE_TAG.sub("", value)
    value = _SPACES.sub(" ", value)
    value = "\n".join(line.strip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if limit > 0 and len(value) > int(limit):
        value = value[: int(limit)].rstrip() + "……"
    return value


__all__ = ["MAX_SPEECH_CHARS", "clean_for_speech"]
