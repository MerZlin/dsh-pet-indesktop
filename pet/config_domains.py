# -*- coding: utf-8 -*-
"""Config 域 facade（批5）：把 pet/config.py 的清洗/合并函数包装成轻量域视图。

铁律：normalize 一律复用 pet/config.py 现有的 _merge_*/_clean_*/_default_* 函数，
本模块只做「取子键 / 聚合成域 dict」的编排，**不写第二份清洗逻辑**。清洗结果与
Config 现有加载路径（reload + _normalize_pet_settings）对同一输入完全一致。

本批**不迁移任何调用点**：设置对话框等继续走 Config 老路，facade 只建不用。
secret 保留逻辑、version 迁移、reload 白名单行为均由 Config 负责，facade 不触碰。
"""
from __future__ import annotations

from typing import Any

from .config import (
    DEFAULT_COLLISION_SETTINGS,
    _clean_agent_link_data,
    _clean_collision_data,
    _clean_menu_appearance,
    _clean_menu_easter_egg,
    _clean_quick_launch_apps,
    _merge_chat_data,
    _merge_proactive_screen_data,
)

__all__ = [
    "ChatConfig",
    "AgentLinkConfig",
    "ProactiveConfig",
    "CollisionConfig",
    "MenuConfig",
]


class _DomainFacade:
    """轻量域 facade 基类：data 为已归一化 dict，to_dict() 返回副本（语义往返一致）。"""

    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def from_dict(cls, raw: Any) -> "_DomainFacade":
        """归一化 raw 并包装；非法类型/缺字段回退到域默认值（与 Config 加载路径一致）。"""
        return cls(cls.normalize(raw))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self.data)


class ChatConfig(_DomainFacade):
    """chat 域：对话开关 / 活跃 provider / provider 列表。

    normalize 复用 config._merge_chat_data（含 legacy 平铺字段合并与
    active_provider 回退）。providers 下未知扩展字段随 _merge_chat_data 保留。
    """

    @classmethod
    def normalize(cls, raw: Any) -> dict:
        return _merge_chat_data(raw)


class AgentLinkConfig(_DomainFacade):
    """agent_link 域：联动开关 / 自定义 Agent / 通知与音效。

    normalize 复用 config._clean_agent_link_data；未知扩展键（thinking_text、
    thinking_texts、自定义 agent 的开关布尔等）按既有保留策略不丢失。
    """

    @classmethod
    def normalize(cls, raw: Any) -> dict:
        return _clean_agent_link_data(raw)


class ProactiveConfig(_DomainFacade):
    """proactive_screen 域：主动屏保参数。

    normalize 复用 config._merge_proactive_screen_data（默认值 + 浅合并，与
    Config 加载路径同一函数同一产出）。
    """

    @classmethod
    def normalize(cls, raw: Any) -> dict:
        return _merge_proactive_screen_data(raw)


class CollisionConfig(_DomainFacade):
    """collision 域：碰撞物理 / 音效，共 7 键。

    normalize 复用 config._clean_collision_data；键集合先按 Config reload 白名单
    行为过滤（未知键丢弃、None 不覆盖默认值），再清洗，产出与加载路径一致。
    """

    @classmethod
    def normalize(cls, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        known = {
            key: value
            for key, value in raw.items()
            if key in DEFAULT_COLLISION_SETTINGS and value is not None
        }
        return _clean_collision_data(known)


class MenuConfig(_DomainFacade):
    """menu 域：右键菜单外观 / 彩蛋 / 快捷启动（3 个顶层键聚合视图）。

    normalize 逐个复用 config._clean_menu_appearance / _clean_menu_easter_egg /
    _clean_quick_launch_apps；缺失键按加载路径回退到清洗后的默认值。
    """

    @classmethod
    def normalize(cls, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        return {
            "context_menu_appearance": _clean_menu_appearance(raw.get("context_menu_appearance")),
            "menu_easter_egg": _clean_menu_easter_egg(raw.get("menu_easter_egg")),
            "quick_launch_apps": _clean_quick_launch_apps(raw.get("quick_launch_apps")),
        }
