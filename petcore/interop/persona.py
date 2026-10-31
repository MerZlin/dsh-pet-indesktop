"""人格层组合：核心 / 场景 / 日常，超预算先裁日常与场景。

只做字符串预算，不读文件、不认具体角色；调用方把三层文本传进来。
"""

from __future__ import annotations

MAX_LAYER_CHARS = 6000


def _clean(text: str) -> str:
    return str(text or "").strip()


def trim_layers(
    core: str,
    daily: str = "",
    scenes: dict[str, str] | None = None,
    *,
    max_chars: int = MAX_LAYER_CHARS,
) -> tuple[str, str, dict[str, str]]:
    """按「核心 → 场景 → 日常」保留，超预算从低优先级开始裁。"""
    budget = max(400, int(max_chars))
    core_text = _clean(core)
    scene_map = {
        str(key): _clean(value)
        for key, value in dict(scenes or {}).items()
        if _clean(value)
    }
    daily_text = _clean(daily)
    # 核心永远保留，但极端情况下截断到预算的 70%。
    if len(core_text) > int(budget * 0.7):
        core_text = core_text[: int(budget * 0.7)].rstrip()
    used = len(core_text)
    kept_scenes: dict[str, str] = {}
    for key, value in scene_map.items():
        if used + len(value) + 2 <= budget:
            kept_scenes[key] = value
            used += len(value) + 2
    if used + len(daily_text) <= budget:
        kept_daily = daily_text
    else:
        kept_daily = ""
    return core_text, kept_daily, kept_scenes


def compose_layers(
    core: str,
    daily: str = "",
    scenes: dict[str, str] | None = None,
    *,
    max_chars: int = MAX_LAYER_CHARS,
) -> str:
    core_text, daily_text, kept_scenes = trim_layers(
        core, daily, scenes, max_chars=max_chars
    )
    parts: list[str] = []
    if core_text:
        parts.append(core_text)
    for key, value in kept_scenes.items():
        parts.append(f"【{key}】\n{value}")
    if daily_text:
        parts.append(f"【今天】\n{daily_text}")
    return "\n\n".join(parts)
