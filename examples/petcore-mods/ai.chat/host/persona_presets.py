"""AI 人设预设：包内只读 + 用户人格卡（data-over-code）。

包内预设放在 ``examples/petcore-mods/ai.chat/persona_presets/*.json``，安装后
只读；用户保存的人格卡落在 ``<data_root>/feature-data/ai.chat/personas/``。
同 id 的用户文件覆盖包内预设（可重命名/改提示词），新 id 追加。坏文件跳过，
不影响其它卡；旧调用 ``load_presets()`` 仍只读包内目录。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .prompts import DEFAULT_PROMPT

DEFAULT_PERSONA = "whale_maid"
CUSTOM_PERSONA = "custom"
PRESET_DIR = Path(__file__).resolve().parent.parent / "persona_presets"

MAX_PRESETS = 64
MAX_NAME = 32
MAX_DESCRIPTION = 160
MAX_PROMPT = 8000
_USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class PersonaPreset:
    id: str
    name: str
    description: str
    prompt: str
    builtin: bool = False


def _read_directory(directory: Path, *, builtin: bool) -> list[PersonaPreset]:
    presets: list[PersonaPreset] = []
    if not directory.is_dir():
        return presets
    for path in sorted(directory.glob("*.json")):
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
                name=str(doc.get("name") or persona_id).strip()[:MAX_NAME],
                description=str(doc.get("description") or "").strip()[:MAX_DESCRIPTION],
                prompt=prompt[:MAX_PROMPT],
                builtin=builtin,
            )
        )
    return presets


def load_presets(
    directory: Path | str | None = None,
    user_dir: Path | str | None = None,
) -> tuple[PersonaPreset, ...]:
    if user_dir is not None:
        return PersonaStore(user_dir).load_all()
    base = Path(directory) if directory is not None else PRESET_DIR
    presets = _read_directory(base, builtin=True)
    if not presets:
        presets.append(
            PersonaPreset(
                DEFAULT_PERSONA, "鲸鱼娘女仆", "默认人设", DEFAULT_PROMPT, True
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


def new_user_id() -> str:
    return f"user-{uuid.uuid4().hex[:8]}"


def validate_preset(
    persona_id: str,
    name: str,
    description: str,
    prompt: str,
) -> tuple[str, str, str, str]:
    """规范化并校验人格卡；非法时抛 ``ValueError``（文案可直接给 UI）。"""
    clean_id = str(persona_id or "").strip()
    if not _USER_ID.fullmatch(clean_id):
        raise ValueError("人格卡 id 只能是小写字母、数字、- 和 _，长度 2–64")
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("人格名称不能为空")
    if len(clean_name) > MAX_NAME:
        raise ValueError(f"人格名称最多 {MAX_NAME} 个字")
    clean_description = str(description or "").strip()
    if len(clean_description) > MAX_DESCRIPTION:
        raise ValueError(f"人格说明最多 {MAX_DESCRIPTION} 个字")
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("系统提示词不能为空")
    if len(clean_prompt) > MAX_PROMPT:
        raise ValueError(f"系统提示词最多 {MAX_PROMPT} 个字")
    return clean_id, clean_name, clean_description, clean_prompt


class PersonaStore:
    """用户人格卡目录；包内预设通过 ``load_all`` 合并进来。"""

    def __init__(self, user_dir: Path | str) -> None:
        self.user_dir = Path(user_dir)

    @staticmethod
    def for_data_root(data_root: Path | str) -> "PersonaStore":
        return PersonaStore(Path(data_root) / "feature-data" / "ai.chat" / "personas")

    def bundled(self) -> tuple[PersonaPreset, ...]:
        presets = _read_directory(PRESET_DIR, builtin=True)
        if not presets:
            presets.append(
                PersonaPreset(DEFAULT_PERSONA, "鲸鱼娘女仆", "默认人设", DEFAULT_PROMPT, True)
            )
        return tuple(presets)

    def user_presets(self) -> tuple[PersonaPreset, ...]:
        return tuple(_read_directory(self.user_dir, builtin=False))

    def load_all(self) -> tuple[PersonaPreset, ...]:
        bundled = self.bundled()
        user = self.user_presets()
        by_id = {item.id: item for item in bundled}
        for item in user:
            by_id[item.id] = item
        ordered = [by_id[item.id] for item in bundled]
        seen = {item.id for item in bundled}
        for item in user:
            if item.id in seen:
                continue
            seen.add(item.id)
            ordered.append(item)
        return tuple(ordered)

    def is_overridden(self, persona_id: str) -> bool:
        return self._path(str(persona_id)).is_file()

    def save(self, preset: PersonaPreset) -> PersonaPreset:
        clean_id, name, description, prompt = validate_preset(
            preset.id, preset.name, preset.description, preset.prompt
        )
        if len(self.user_presets()) >= MAX_PRESETS and not self.is_overridden(clean_id):
            raise ValueError(f"最多保存 {MAX_PRESETS} 张人格卡")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "id": clean_id,
            "name": name,
            "description": description,
            "prompt": prompt,
        }
        raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        handle, temp_name = tempfile.mkstemp(
            prefix="persona.", suffix=".tmp", dir=str(self.user_dir)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(raw)
            os.replace(temp_name, self._path(clean_id))
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return PersonaPreset(clean_id, name, description, prompt, False)

    def delete(self, persona_id: str) -> bool:
        path = self._path(str(persona_id))
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def reset_builtin(self, persona_id: str) -> bool:
        return self.delete(persona_id)

    def _path(self, persona_id: str) -> Path:
        clean = str(persona_id or "").strip()
        if not _USER_ID.fullmatch(clean):
            raise ValueError("invalid persona id")
        return self.user_dir / f"{clean}.json"
