"""AI 人设预设：data-over-code，坏文件/缺文件都回退到默认人设。

预设文件放在 ``examples/petcore-mods/ai.chat/persona_presets/*.json``；安装包
按目录整体拷贝，新增预设不需要改 Python。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .prompts import DEFAULT_PROMPT

DEFAULT_PERSONA = "whale_maid"
CUSTOM_PERSONA = "custom"
PRESET_DIR = Path(__file__).resolve().parent.parent / "persona_presets"


@dataclass(frozen=True)
class PersonaPreset:
    id: str
    name: str
    description: str
    prompt: str


def load_presets(directory: Path | str | None = None) -> tuple[PersonaPreset, ...]:
    base = Path(directory) if directory is not None else PRESET_DIR
    presets: list[PersonaPreset] = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            persona_id = str(doc.get("id") or path.stem).strip()
            prompt = str(doc.get("prompt") or "").strip()
            if not persona_id or not prompt:
                continue
            presets.append(
                PersonaPreset(
                    id=persona_id,
                    name=str(doc.get("name") or persona_id).strip(),
                    description=str(doc.get("description") or "").strip(),
                    prompt=prompt,
                )
            )
    if not presets:
        presets.append(
            PersonaPreset(
                DEFAULT_PERSONA, "鲸鱼娘女仆", "默认人设", DEFAULT_PROMPT
            )
        )
    return tuple(presets)


def preset_for(
    persona_id: str, presets: tuple[PersonaPreset, ...] | None = None
) -> PersonaPreset | None:
    pool = presets if presets is not None else load_presets()
    return next((item for item in pool if item.id == str(persona_id)), None)


def resolve_prompt(
    persona_id: object,
    custom_prompt: object,
    character_name: str,
    presets: tuple[PersonaPreset, ...] | None = None,
) -> str:
    """按「显式选择 → 自定义提示词 → 默认预设」解析最终 system prompt。"""
    pool = presets if presets is not None else load_presets()
    chosen = str(persona_id or "").strip()
    custom = str(custom_prompt or "").strip()
    if not chosen:
        chosen = CUSTOM_PERSONA if custom else DEFAULT_PERSONA
    if chosen == CUSTOM_PERSONA:
        if not custom:
            return DEFAULT_PROMPT.format(name=character_name)
        return _format_prompt(custom, character_name)
    preset = preset_for(chosen, pool) or (pool[0] if pool else None)
    if preset is None:
        return DEFAULT_PROMPT.format(name=character_name)
    return _format_prompt(preset.prompt, character_name)


def _format_prompt(text: str, character_name: str) -> str:
    try:
        return text.format(name=character_name)
    except (KeyError, IndexError, ValueError):
        # 自定义提示词里可能有 JSON 花括号；原样保留比抛错更安全。
        return text
