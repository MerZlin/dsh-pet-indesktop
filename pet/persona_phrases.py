# -*- coding: utf-8 -*-
"""Existing-event dialogue variants.

The registry deliberately does not define or emit events.  ``legacy`` returns
the caller's current text verbatim, so changing the selected persona is safe
for existing installations and easy to roll back.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

DIALOGUE_MODES = {"legacy", "whale_maid", "custom"}

_PHRASES = {
    "start": ["收到啦，{name}开始工作了。", "{name}已经出发，主人稍等一下。"],
    "thinking": ["正在认真替主人想办法……", "让我再仔细想想，主人稍等一下。"],
    "activity.read": ["正在帮主人认真翻找资料呢……", "收到，正在仔细查看文件。"],
    "activity.search": ["正在替主人探索线索……", "我来帮主人继续找找相关线索。"],
    "activity.edit": ["正在把想法写进代码里……", "收到，正在推进代码修改。"],
    "activity.run": ["正在替主人执行并验证一下……", "我来跑一遍，看看结果是否符合预期。"],
    "activity.default": ["正在认真处理这一步……", "收到，正在继续推进。"],
    "approval.command": ["主人，这一步需要你确认：{command}", "请主人确认一下这条操作：{command}"],
    "approval.tool": ["主人，这里需要你确认一下：{label}", "请主人决定是否允许这一步：{label}"],
    "approval.generic": ["主人，这里需要你确认一下。", "这一步需要主人的决定，确认后我就继续。"],
    "question.empty": ["这里需要主人的决定，选好以后我就继续出发啦。"],
    "question.one": ["主人，这里有个问题需要你决定：{body}", "我需要主人的选择，确认后就继续。问题是：{body}"],
    "question.many": ["主人，这里有 {count} 个问题需要决定。", "还有 {count} 个问题等主人确认。"],
    "watchdog.warning": ["好像在同一片海域绕圈圈了……主人先留意一下。"],
    "watchdog.intervention": ["{name} 可能在重复排查。最近表现：{reasons}。要不要换一条路线？"],
    "watchdog.unknown": ["判断服务暂时不可用，这次先提醒主人留意。"],
    "rate_limit.one": ["呜，通信有点拥挤，暂时被限流了，请稍后再试。"],
    "rate_limit.many": ["呜，通信有点拥挤，已经连续限流 {count} 次了，请稍后再试。"],
    "done.success": ["主人，{name}这一轮完成啦，去看看成果吧。"],
    "done.attention": ["{name}这一轮停下来了，结果还请主人确认一下。"],
    "failure.retry": ["{name}本轮没有完成：多次重试后仍未成功，请检查后再运行。"],
    "failure.tool": ["{name}本轮没有完成：工具执行失败，请检查后再运行。"],
    "failure.generic": ["{name}本轮没有完成，请检查后再运行。"],
}

try:
    _json_phrases = json.loads(Path(__file__).with_name("persona_phrases.json").read_text(encoding="utf-8"))
    if isinstance(_json_phrases, dict):
        _PHRASES.update({str(key): value for key, value in _json_phrases.items() if isinstance(value, list)})
except (OSError, ValueError):
    pass


class PhrasePicker:
    """Small deterministic picker which avoids immediate repeats per phrase key."""

    def __init__(self) -> None:
        self._last: dict[str, int] = defaultdict(lambda: -1)

    def get(self, mode: str, key: str, fallback: str, **values) -> str:
        if str(mode or "legacy").lower() != "whale_maid":
            return fallback
        variants = _PHRASES.get(key)
        if not variants:
            return fallback
        if isinstance(variants, str):
            variants = [variants]
        last = self._last[key]
        index = (last + 1) % len(variants)
        self._last[key] = index
        try:
            return variants[index].format(**values)
        except (KeyError, ValueError):
            return fallback

    @staticmethod
    def custom(custom_phrases: dict, key: str, fallback: str, **values) -> str:
        """Return a user override, or the supplied existing-event fallback."""
        if not isinstance(custom_phrases, dict):
            return fallback
        template = custom_phrases.get(key)
        if isinstance(template, list):
            template = template[0] if template else ""
        template = str(template or "").strip()
        if not template:
            return fallback
        try:
            return template.format(**values)
        except (KeyError, ValueError):
            return fallback
def phrase_keys() -> tuple[str, ...]:
    return tuple(sorted(_PHRASES))
