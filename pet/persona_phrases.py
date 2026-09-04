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
from string import Formatter
from typing import Any, Mapping
import re

class _TemplateObject(dict):
    """Mapping that supports both ``field.key`` and ``field[key]`` syntax."""
    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _wrap_template_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _TemplateObject({str(k): _wrap_template_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap_template_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_wrap_template_value(v) for v in value)
    return value


def _template_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Expose payload fields at top level and under ``payload``/``data``.

    Keeping the original mapping available means newly added upstream fields do
    not require another adapter change; explicit display aliases still win.
    """
    result = dict(values)
    payload = values.get("payload")
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            result.setdefault(str(key), value)
        result.setdefault("payload", payload)
        result.setdefault("data", payload)
    return {str(key): _wrap_template_value(value) for key, value in result.items()}


_FIELD_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.([A-Za-z_][A-Za-z0-9_-]*)|\[([^\]]+)\]))*$")


def _safe_get_field(field_name: str, values: Mapping[str, Any]) -> Any:
    """Resolve only mapping keys and list indexes; never arbitrary attributes."""
    match = _FIELD_PATH_RE.fullmatch(field_name)
    if not match:
        raise KeyError(field_name)
    root_end = field_name.find(".")
    bracket = field_name.find("[")
    ends = [pos for pos in (root_end, bracket) if pos >= 0]
    root = field_name[:min(ends)] if ends else field_name
    if root not in values:
        raise KeyError(root)
    current: Any = values[root]
    rest = field_name[len(root):]
    while rest:
        if rest.startswith("."):
            end = len(rest)
            for marker in (".", "["):
                pos = rest.find(marker, 1)
                if pos >= 0:
                    end = min(end, pos)
            key = rest[1:end]
            if not isinstance(current, Mapping) or key not in current:
                raise KeyError(key)
            current = current[key]
            rest = rest[end:]
        elif rest.startswith("["):
            end = rest.find("]", 1)
            token = rest[1:end]
            if token.isdigit():
                if not isinstance(current, (list, tuple)):
                    raise TypeError(token)
                current = current[int(token)]
            else:
                token = token.strip("'\"")
                if not isinstance(current, Mapping) or token not in current:
                    raise KeyError(token)
                current = current[token]
            rest = rest[end + 1:]
        else:
            raise KeyError(field_name)
    return current


def render_template(template: str, values: Mapping[str, Any] | None = None) -> str:
    """Format templates using safe mapping/list traversal.

    Unknown or malformed placeholders remain verbatim so future upstream fields
    and existing custom phrases remain usable without exposing object attributes.
    """
    values = _template_values(values or {})
    formatter = Formatter()
    output: list[str] = []
    try:
        for literal, field_name, format_spec, conversion in formatter.parse(str(template)):
            output.append(literal)
            if field_name is None:
                continue
            try:
                obj = _safe_get_field(field_name, values)
                if conversion:
                    obj = formatter.convert_field(obj, conversion)
                output.append(format(obj, format_spec))
            except (KeyError, IndexError, AttributeError, TypeError, ValueError):
                output.append("{" + field_name + ("!" + conversion if conversion else "") + (":" + format_spec if format_spec else "") + "}")
    except (ValueError, TypeError):
        return str(template)
    return "".join(output)



_PHRASES = {
    "start": ["收到啦，{name}开始工作了。", "{name}已经出发，主人稍等一下。"],
    "thinking": ["正在认真替主人想办法……", "让我再仔细想想，主人稍等一下。"],
    "activity.read": ["正在帮主人认真翻找资料呢……", "收到，正在仔细查看文件。"],
    "activity.search": ["正在替主人探索线索……", "我来帮主人继续找找相关线索。"],
    "activity.edit": ["正在把想法写进代码里……", "收到，正在推进代码修改。"],
    "activity.run": ["正在替主人执行并验证一下……", "我来跑一遍，看看结果是否符合预期。"],
    "activity.default": ["正在认真处理这一步……", "收到，正在继续推进。"],
    "agent.attention": ["主人，这一步需要你看一眼哦。", "这里需要主人的决定，我先停在这里等你。"],
    "agent.error": ["呜，{name}这边遇到一点问题了。", "{name}好像出错了，主人帮忙看一下吧。"],
    "agent.missing": ["主人，暂时没有找到{name}，所以我还感知不到它。", "{name}还没有在这台电脑上运行，主人检查一下吧。"],
    "bridge.install.pending": ["正在给 {name} 接上通信桥，主人稍等一下。", "人家正在安装 {name} 的联动桥接，马上就好～"],
    "bridge.install.success": ["通信桥接好啦，{name} 的状态现在可以被我感知了。", "{name} 的联动插件安装完成，收到啦～"],
    "bridge.install.failed": ["呜，{name} 的通信桥没有装好，主人检查一下吧：{detail}", "桥接安装遇到问题啦：{detail}，主人帮我看看～"],
    "bridge.uninstall.failed": ["{name} 的通信桥没有完全卸载，主人需要手动检查一下。", "桥接收尾没有完成，主人看一下 {name} 的配置吧。"],
    "dsh.writeback.failed": ["呜，消息没有送回 DSH，主人请到 DSH 界面处理。", "通信回写失败啦，主人去 DSH 看一下吧。"],
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
    "llm_error.api": ["AI 服务出错了，主人看看是怎么回事吧。", "AI 服务暂时没有回应，主人稍后再试一次吧。"],
    "done.success": ["主人，{name}这一轮完成啦，去看看成果吧。"],
    "done.attention": ["{name}这一轮停下来了，结果还请主人确认一下。"],
    "failure.retry": ["{name}本轮没有完成：多次重试后仍未成功，请检查后再运行。"],
    "failure.tool": ["{name}本轮没有完成：工具执行失败，请检查后再运行。"],
    "failure.generic": ["{name}本轮没有完成，请检查后再运行。"],
}

# Stable schema keys reserved for newer event integrations.  Empty built-ins
# remain compatible with the legacy JSON while making exports complete.
for _key in (
    "control.replan.pending", "control.replan.success", "control.interrupt.pending",
    "control.interrupt.success", "control.failed", "stuck.reminder", "pattern.warning",
    "pattern.control", "balance.query",
):
    _PHRASES.setdefault(_key, [])

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
            return render_template(variants[index], values)
        except (KeyError, ValueError):
            return fallback

    def custom(self, custom_phrases: dict, key: str, fallback: str, **values) -> str:
        """Render a custom phrase, rotating through all configured variants."""
        if not isinstance(custom_phrases, dict):
            return fallback
        raw = custom_phrases.get(key)
        if isinstance(raw, list):
            variants = [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
        else:
            variants = [str(raw).strip()] if isinstance(raw, str) and raw.strip() else []
        if not variants:
            return fallback
        last = self._last[key]
        index = (last + 1) % len(variants)
        self._last[key] = index
        return render_template(variants[index], values)
def phrase_keys() -> tuple[str, ...]:
    return tuple(sorted(_PHRASES))

def default_phrases() -> dict[str, str]:
    """Return the default phrase template for each key (first variant)."""
    return {key: variants[0] if variants else "" for key, variants in _PHRASES.items()}
