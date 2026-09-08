# -*- coding: utf-8 -*-
"""预设“一起做某事”列表与角色素材交集测试。"""
from __future__ import annotations

from pet import group_presets


def test_preset_table_has_unique_ids_and_clips():
    ids = [item["id"] for item in group_presets.GROUP_PRESETS]
    clips = [item["clip"] for item in group_presets.GROUP_PRESETS]
    assert len(ids) == len(set(ids))
    assert len(clips) == len(set(clips))
    assert any("早餐" in item["label"] for item in group_presets.GROUP_PRESETS)


def test_resolve_preset_returns_none_for_unknown():
    assert group_presets.resolve_group_preset("missing") is None
    assert group_presets.resolve_group_preset("breakfast")["clip"] == "吃早餐"


def test_available_presets_filters_by_names_without_disk_scan(monkeypatch):
    scanned = group_presets.character_animation_names("shenshen")
    # 不实际依赖磁盘：用 monkeypatch 直接替换扫描结果为构造集合
    monkeypatch.setattr(group_presets, "character_animation_names", lambda _cid: {"吃早餐", "吃午餐"})
    available = group_presets.available_presets_for_character("shenshen")
    assert {item["id"] for item in available} == {"breakfast", "lunch"}


def test_common_presets_returns_intersection():
    pets = [
        {"character": "shenshen", "names": {"吃早餐", "吃午餐", "吃晚餐"}},
        {"character": "custom", "names": {"吃早餐", "吃白饭"}},
    ]
    common = group_presets.common_presets(pets)
    assert {item["id"] for item in common} == {"breakfast"}
