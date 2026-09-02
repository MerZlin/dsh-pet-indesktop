# -*- coding: utf-8 -*-
"""Config 域 facade 测试（批5）。

facade 是纯新增（本批不迁移任何调用点）。护栏：
1) from_dict→to_dict 语义往返一致（to_dict 可再入 from_dict，normalize 是定点）；
2) 非法类型/缺字段输入 normalize 的产出 == Config 现有加载路径（reload +
   _normalize_pet_settings）对同一输入的产出；
3) 未知扩展键保留策略：agent_link 的 thinking_texts/自定义 agent key 等不能丢。
"""
from __future__ import annotations

import json

import pytest

from pet.config import Config
from pet.config_domains import (
    AgentLinkConfig,
    ChatConfig,
    CollisionConfig,
    MenuConfig,
    ProactiveConfig,
)

COLLISION_KEYS = (
    "collision_enabled", "collision_restitution", "collision_friction",
    "collision_mass_scale", "collision_impulse_cap",
    "collision_sound_enabled", "collision_sound_volume",
)
MENU_KEYS = ("context_menu_appearance", "menu_easter_egg", "quick_launch_apps")

# 各域脏输入：非法类型/越界/缺字段混在一起，normalize 产出必须与 Config 加载路径一致。
CHAT_DIRTY = {
    "enabled": "not_bool",
    "active_provider": "ghost",          # 不在 providers → 回退首个 provider
    "history_message_limit": "many",
    "providers": {
        "openai-main": {
            "model": 123, "timeout": -5.0, "temperature": 99.0, "max_tokens": -1,
            "api_key": "sk-in-memory",   # 纯数据保留（写盘 redact 由 Config 负责，facade 不碰）
        },
        "custom": {"base_url": "https://x", "model": "m"},  # 无 api_key_ref → 按自身归位
    },
}
AGENT_LINK_DIRTY = {
    "dsh": "yes",                        # bool("yes") → True
    "notify_done": 0,                    # bool(0) → False
    "sound_volume": 5.0,                 # clamp → 1.0
    "sound_cooldown_seconds": -1,        # clamp → 0.0
    "custom_agents": "bad",              # → []
    "thinking_texts": {"dsh": "大脑飞速运转"},
    "future_ext": {"keep": 1},           # 未知扩展键保留
}
PROACTIVE_DIRTY = {
    "enabled": "not_bool",               # _merge_proactive_screen_data 不清类型（与 Config 同）
    "dwell_seconds": -5,
    "whitelist": "bad",
    "future_ext": {"a": 1},              # 未知扩展键保留
}
COLLISION_DIRTY = {
    "collision_enabled": "not_bool",
    "collision_restitution": 99.0,       # max 1.0
    "collision_friction": -5.0,          # min 0.0
    "collision_mass_scale": 10.0,        # max 2.0
    "collision_impulse_cap": 500.0,      # min 1000.0
    "collision_sound_enabled": 0,
    "collision_sound_volume": "loud",
    "future_collision_key": 1,           # 未知顶层键按白名单丢弃
}
MENU_DIRTY = {
    "context_menu_appearance": {
        "theme": "neon", "corner_radius": 999, "opacity": 5.0, "ui_font_size": "big",
        "light_background": "not-a-color", "future_subkey": 1,
    },
    "menu_easter_egg": None,             # None → 默认彩蛋（reload 白名单跳过 None）
    "quick_launch_apps": [
        42,                              # 非 dict 条目丢弃
        {"kind": "default_browser", "name": ""},
        {"kind": "application", "name": "App", "path": "C:/x.exe"},
    ],
}

# 往返稳定性用例：(facade, 输入列表)，输入覆盖合法/脏/None。
ROUNDTRIP_CASES = [
    (ChatConfig, [
        {"enabled": False, "active_provider": "ghost"},
        {"enabled": "yes", "providers": "bad"},
        {"providers": {"custom": {"base_url": "https://x", "model": "m"}}},
        None,
    ]),
    (AgentLinkConfig, [
        {"claude": True},
        {"custom_agents": [{"key": "gemini", "name": "G", "path": "~/x.jsonl"}],
         "thinking_texts": {"gemini": "想"}},
        {"sound_volume": 5.0},
        None,
    ]),
    (ProactiveConfig, [
        {"enabled": True, "dwell_seconds": 12},
        {"whitelist": "bad"},
        None,
    ]),
    (CollisionConfig, [
        {"collision_enabled": False, "collision_restitution": 0.5},
        {"collision_friction": -1.0, "future_key": 1},
        None,
    ]),
    (MenuConfig, [
        {"context_menu_appearance": {"theme": "light"}},
        {"menu_easter_egg": {"title": "x"}},
        {"quick_launch_apps": [{"kind": "application", "name": "A", "path": "C:/a.exe"}]},
        None,
    ]),
]

# 与 Config 加载路径一致性用例：
# (facade, 原始输入, 写盘 payload, 从 cfg.data 提取该域产出的函数)
LOAD_PATH_CASES = [
    (ChatConfig, CHAT_DIRTY, {"chat": CHAT_DIRTY}, lambda cfg: cfg.data["chat"]),
    (AgentLinkConfig, AGENT_LINK_DIRTY, {"agent_link": AGENT_LINK_DIRTY}, lambda cfg: cfg.data["agent_link"]),
    (ProactiveConfig, PROACTIVE_DIRTY, {"proactive_screen": PROACTIVE_DIRTY}, lambda cfg: cfg.data["proactive_screen"]),
    (CollisionConfig, COLLISION_DIRTY, dict(COLLISION_DIRTY), lambda cfg: {k: cfg.data[k] for k in COLLISION_KEYS}),
    (MenuConfig, MENU_DIRTY, dict(MENU_DIRTY), lambda cfg: {k: cfg.data[k] for k in MENU_KEYS}),
]


def _write_config(tmp_path, payload: dict) -> Config:
    """写一份 version=4 的配置文件并加载出 Config（走 reload 加载路径）。"""
    cfg_dir = tmp_path / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, **payload}), encoding="utf-8"
    )
    return Config(tmp_path)


# ============================================================================
# 1. from_dict→to_dict 语义往返
# ============================================================================
@pytest.mark.parametrize(
    "facade,inputs",
    ROUNDTRIP_CASES,
    ids=["chat", "agent_link", "proactive", "collision", "menu"],
)
def test_from_dict_to_dict_roundtrip_is_stable(facade, inputs):
    for raw in inputs:
        once = facade.from_dict(raw).to_dict()
        twice = facade.from_dict(once).to_dict()
        assert once == twice                       # to_dict 可再入 from_dict（语义往返一致）
        assert facade.normalize(once) == once      # normalize 是定点（幂等）


@pytest.mark.parametrize(
    "facade,inputs",
    ROUNDTRIP_CASES,
    ids=["chat", "agent_link", "proactive", "collision", "menu"],
)
def test_normalize_equals_from_dict_data(facade, inputs):
    for raw in inputs:
        assert facade.from_dict(raw).data == facade.normalize(raw)
        assert facade.from_dict(raw).to_dict() == facade.normalize(raw)


# ============================================================================
# 2. 非法类型/缺字段输入 → 与 Config 现有加载路径产出一致
# ============================================================================
@pytest.mark.parametrize(
    "facade,raw,payload,extract",
    LOAD_PATH_CASES,
    ids=["chat", "agent_link", "proactive", "collision", "menu"],
)
def test_normalize_matches_config_load_path(tmp_path, facade, raw, payload, extract):
    cfg = _write_config(tmp_path, payload)
    assert facade.normalize(raw) == extract(cfg)


@pytest.mark.parametrize(
    "facade,extract",
    [
        (ChatConfig, lambda cfg: cfg.data["chat"]),
        (AgentLinkConfig, lambda cfg: cfg.data["agent_link"]),
        (ProactiveConfig, lambda cfg: cfg.data["proactive_screen"]),
        (CollisionConfig, lambda cfg: {k: cfg.data[k] for k in COLLISION_KEYS}),
        (MenuConfig, lambda cfg: {k: cfg.data[k] for k in MENU_KEYS}),
    ],
    ids=["chat", "agent_link", "proactive", "collision", "menu"],
)
def test_invalid_type_inputs_fall_back_to_domain_defaults(tmp_path, facade, extract):
    """非 dict 输入 → 域默认值，与全新 Config（无配置文件）的现状一致。"""
    fresh = Config(base=tmp_path)
    assert facade.normalize(None) == extract(fresh)
    assert facade.normalize("junk") == extract(fresh)
    assert facade.normalize(42) == extract(fresh)


def test_collision_none_values_do_not_override_defaults_like_whitelist(tmp_path):
    """collision 键为 None 时 reload 白名单跳过 → 保留默认值；facade 同规则。"""
    raw = {"collision_sound_enabled": None, "collision_enabled": None, "collision_restitution": None}
    assert CollisionConfig.normalize(raw) == CollisionConfig.normalize({})
    cfg = _write_config(tmp_path, raw)
    assert cfg.data["collision_sound_enabled"] is True
    assert cfg.data["collision_enabled"] is True
    assert cfg.data["collision_restitution"] == pytest.approx(0.82)


# ============================================================================
# 3. 未知扩展键保留策略
# ============================================================================
def test_agent_link_extension_keys_and_custom_agent_key_preserved():
    normalized = AgentLinkConfig.normalize({
        "custom_agents": [{"key": "gemini", "name": "Gemini", "path": "~/ev.jsonl"}],
        "gemini": True,                              # 自定义 agent 的开关布尔
        "thinking_texts": {"gemini": "大脑飞速运转", "dsh": "思考中"},
        "thinking_text": "旧版全局文案",
        "future_ext": {"keep": 1},
    })
    assert normalized["custom_agents"] == [{"key": "gemini", "name": "Gemini", "path": "~/ev.jsonl"}]
    assert normalized["gemini"] is True
    assert normalized["thinking_texts"] == {"gemini": "大脑飞速运转", "dsh": "思考中"}
    assert normalized["thinking_text"] == "旧版全局文案"
    assert normalized["future_ext"] == {"keep": 1}


def test_chat_extension_keys_preserved():
    normalized = ChatConfig.normalize({
        "future_feature": {"enabled": True},
        "providers": {"openai-main": {"model": "m", "extra_provider_field": "x"}},
    })
    assert normalized["future_feature"] == {"enabled": True}
    assert normalized["providers"]["openai-main"]["extra_provider_field"] == "x"


def test_proactive_extension_keys_preserved():
    normalized = ProactiveConfig.normalize({"future_key": {"a": 1}, "enabled": True})
    assert normalized["future_key"] == {"a": 1}
    assert normalized["enabled"] is True


def test_collision_unknown_keys_dropped_like_whitelist():
    normalized = CollisionConfig.normalize({"future_key": 1, "collision_enabled": False})
    assert "future_key" not in normalized
    assert normalized["collision_enabled"] is False


def test_menu_unknown_subkeys_dropped_by_clean_functions():
    normalized = MenuConfig.normalize({
        "context_menu_appearance": {"theme": "light", "future": 1},
        "menu_easter_egg": {"future": 2},
    })
    assert "future" not in normalized["context_menu_appearance"]
    assert "future" not in normalized["menu_easter_egg"]
    assert normalized["context_menu_appearance"]["theme"] == "light"


def test_menu_easter_egg_image_scale_cleaned_and_clamped():
    """彩蛋弹窗尺寸百分比：默认 100，钳到 40~200，非数值回默认。"""
    cases = [
        ({}, 100),                          # 缺省回默认
        ({"image_scale": 150}, 150),
        ({"image_scale": 40}, 40),          # 下界可取
        ({"image_scale": 200}, 200),        # 上界可取
        ({"image_scale": 10}, 40),          # 超界钳位
        ({"image_scale": 999}, 200),
        ({"image_scale": "abc"}, 100),      # 非数值回默认
        ({"image_scale": None}, 100),
    ]
    for raw, expected in cases:
        normalized = MenuConfig.normalize({"menu_easter_egg": raw})
        assert normalized["menu_easter_egg"]["image_scale"] == expected, raw



# ============================================================================
# 4. Config 便捷入口（只读视图，不改 Config 状态）
# ============================================================================
def test_accessors_return_facade_instances(tmp_path):
    cfg = Config(base=tmp_path)
    assert isinstance(cfg.chat_config(), ChatConfig)
    assert isinstance(cfg.agent_link_config(), AgentLinkConfig)
    assert isinstance(cfg.proactive_config(), ProactiveConfig)
    assert isinstance(cfg.collision_config(), CollisionConfig)
    assert isinstance(cfg.menu_config(), MenuConfig)


def test_accessors_expose_current_domain_data(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.chat_config().to_dict() == cfg.data["chat"]
    assert cfg.agent_link_config().to_dict() == cfg.data["agent_link"]
    assert cfg.proactive_config().to_dict() == cfg.data["proactive_screen"]
    assert cfg.collision_config().to_dict() == {k: cfg.data[k] for k in COLLISION_KEYS}
    assert cfg.menu_config().to_dict() == {k: cfg.data[k] for k in MENU_KEYS}


def test_accessors_reflect_recently_set_values(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("agent_link", {"claude": True, "thinking_texts": {"dsh": "想"}})
    assert cfg.agent_link_config().get("claude") is True
    assert cfg.agent_link_config().get("thinking_texts") == {"dsh": "想"}

    cfg.set("collision_restitution", 0.5)
    assert cfg.collision_config().get("collision_restitution") == pytest.approx(0.5)

    cfg.set("context_menu_appearance", {"theme": "dark", "corner_radius": 999})
    assert cfg.menu_config().get("context_menu_appearance")["corner_radius"] == 18
    assert cfg.menu_config().get("missing_key", "fallback") == "fallback"


def test_accessors_reflect_reload_from_disk(tmp_path):
    cfg = _write_config(tmp_path, {"agent_link": {"claude": True, "thinking_texts": {"dsh": "想"}}})
    assert cfg.agent_link_config().get("claude") is True
    assert cfg.agent_link_config().get("thinking_texts") == {"dsh": "想"}
