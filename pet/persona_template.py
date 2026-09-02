# -*- coding: utf-8 -*-
"""Portable persona phrase template generation."""
from __future__ import annotations

import copy
from typing import Any

from .persona_phrases import phrase_keys

TEMPLATE_VERSION = "persona-phrases/v1"
VARIABLES = {
    "name": "Agent 展示名称", "command": "待执行命令", "label": "工具或操作名称",
    "body": "问题内容", "count": "连续次数", "reasons": "Watchdog 风险原因",
    "detail": "错误详情", "text": "显示文本",
}
PARAMETERS = {
    "start": ("name",), "thinking": ("name",), "activity.read": ("name",),
    "activity.search": ("name",), "activity.edit": ("name",), "activity.run": ("name",),
    "activity.default": ("name",), "agent.attention": ("name",), "agent.error": ("name",),
    "agent.missing": ("name",), "bridge.install.pending": ("name",),
    "bridge.install.success": ("name",), "bridge.install.failed": ("name", "detail"),
    "bridge.uninstall.failed": ("name",), "approval.command": ("name", "command"),
    "approval.tool": ("name", "label"), "approval.generic": ("name",),
    "question.one": ("name", "body"), "question.many": ("name", "count"),
    "watchdog.warning": ("name", "reasons"), "watchdog.intervention": ("name", "reasons"),
    "rate_limit.many": ("count",), "done.success": ("name",), "done.attention": ("name",),
    "failure.retry": ("name",), "failure.tool": ("name",), "failure.generic": ("name",),
    "control.replan.pending": ("name",), "control.replan.success": ("name",),
    "control.interrupt.pending": ("name",), "control.interrupt.success": ("name",),
    "control.failed": ("name",), "pattern.warning": ("name",),
    "pattern.control": ("name", "reasons"), "balance.result": ("text",),
}


def build_persona_template(config: dict[str, Any] | None) -> dict[str, Any]:
    """Build a complete portable document without leaking runtime settings."""
    config = config if isinstance(config, dict) else {}
    raw = config.get("dialogue_phrases", config)
    raw = raw if isinstance(raw, dict) else {}
    phrases = {}
    entries = []
    for key in phrase_keys():
        value = raw.get(key, [])
        if isinstance(value, str):
            value = [value] if value.strip() else []
        elif isinstance(value, list):
            value = [item.strip() for item in value if isinstance(item, str) and item.strip()][:8]
        else:
            value = []
        phrases[key] = copy.deepcopy(value)
        entries.append({"key": key, "description": key, "parameters": list(PARAMETERS.get(key, ())), "phrases": copy.deepcopy(value)})
    mode = str(config.get("dialogue_mode", "custom") or "custom")
    return {"template": TEMPLATE_VERSION, "mode": mode if mode in {"legacy", "whale_maid", "custom"} else "custom", "name": str(config.get("persona_template_name", "我的角色台词") or "我的角色台词"), "description": "Pet 角色台词自定义模板", "variables": copy.deepcopy(VARIABLES), "phrases": phrases, "entries": entries}


def template_json(config: dict[str, Any] | None) -> str:
    import json
    return json.dumps(build_persona_template(config), ensure_ascii=False, indent=2) + "\n"
