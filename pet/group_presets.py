# -*- coding: utf-8 -*-
"""多桌宠「一起做某事」预设与参与者公共动作交集。

预设 clip 必须来自当前素材中真实存在的动画名；显示前再按每个参与者
实际拥有的素材名过滤，避免“菜单里有但某只播不了”。
"""
from __future__ import annotations

from typing import Any, Iterable

from . import catalog

# id 稳定作为 IPC 线格式标识；clip 是动画素材名；label 是菜单文案。
GROUP_PRESETS: tuple[dict[str, str], ...] = (
    {"id": "breakfast", "label": "一起吃早餐", "clip": "吃早餐"},
    {"id": "lunch", "label": "一起吃午餐", "clip": "吃午餐"},
    {"id": "dinner", "label": "一起吃晚餐", "clip": "吃晚餐"},
    {"id": "rice", "label": "一起吃白饭", "clip": "吃白饭"},
    {"id": "snack", "label": "一起吃零食", "clip": "大口吃零食"},
    {"id": "icecream", "label": "一起吃冰淇淋", "clip": "吃冰淇淋融化"},
    {"id": "mooncake", "label": "一起吃月饼", "clip": "中秋赏月吃月饼"},
    {"id": "hotpot", "label": "一起吃火锅", "clip": "涮火锅"},
    {"id": "token", "label": "一起吃Token", "clip": "吃Token"},
)

_PRESET_BY_ID = {item["id"]: item for item in GROUP_PRESETS}


def resolve_group_preset(preset_id: str) -> dict[str, str] | None:
    """按 id 取预设；未知返回 None。"""
    return _PRESET_BY_ID.get(str(preset_id or "").strip())


def character_animation_names(character_id: str) -> frozenset[str]:
    """只读扫描某角色目录的视频 stem 集合；不解码不预热。"""
    video_dir = catalog.resolve_character_video_dir(str(character_id or ""))
    if not video_dir.is_dir():
        return frozenset()
    names: set[str] = set()
    try:
        for path in video_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".webm", ".gif"}:
                names.add(path.stem)
    except OSError:
        return frozenset()
    return frozenset(names)


def available_presets_for_character(
    character_id: str, names: Iterable[str] | None = None
) -> list[dict[str, str]]:
    """返回该角色实际拥有的预设（clip 在 names 中才保留）。"""
    available_names = (
        frozenset(str(n) for n in names)
        if names is not None
        else character_animation_names(character_id)
    )
    result: list[dict[str, str]] = []
    for preset in GROUP_PRESETS:
        if str(preset["clip"]) in available_names:
            result.append(dict(preset))
    return result


def common_presets(
    pets: Iterable[dict[str, Any]], *, names_key: str = "names"
) -> list[dict[str, str]]:
    """返回所有参与者都有的预设。

    pets 元素至少含 ``character``；若含 ``names_key`` 指定的可迭代素材名则直接
    使用（测试/内存内避免扫盘），否则调用 character_animation_names 扫描。
    """
    participants = list(pets)
    if not participants:
        return []
    common = [
        item["id"]
        for item in available_presets_for_character(str(participants[0].get("character") or ""),
                                                   participants[0].get(names_key))
    ]
    for pet in participants[1:]:
        if not common:
            break
        own = set(
            item["id"]
            for item in available_presets_for_character(
                str(pet.get("character") or ""), pet.get(names_key)
            )
        )
        common = [preset_id for preset_id in common if preset_id in own]
    return [
        dict(_PRESET_BY_ID[preset_id])
        for preset_id in common
        if preset_id in _PRESET_BY_ID
    ]
