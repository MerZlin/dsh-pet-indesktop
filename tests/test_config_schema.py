# -*- coding: utf-8 -*-
"""Config 键白名单收口测试（批3 子项3）。

pet/config.py 里 __init__ 的默认值 dict（约 498-566 行）与 reload() 的白名单
元组（约 656-691 行）是两份独立维护的键列表。本测试把现状文档化并加护栏：

实测两集合**不一致**（现状文档化，不修产品代码）：
- 默认值 dict 共 76 键；reload 白名单共 72 键。
- 差异 = 默认值多出 4 键：{version, proactive_screen, agent_link, chat}。
  这 4 键在 reload() 里走专门路径（version 末尾强制回写 4；
  proactive_screen / agent_link / chat 分别经 _merge_*_data 合并），
  不属于普通白名单键，故不并入白名单元组。

护栏语义（新增键漏登记立即红）：
- 默认值键 - 特例键 ⊆ 真实白名单（默认值加键而白名单漏登记 → 失败）；
- 真实白名单 ⊆ 默认值键（白名单出现孤儿键 → 失败）；
- 两集合与显式快照一致（现状文档化，改键必须同步改快照）。
"""

from __future__ import annotations

import inspect
import re

from pet import config as config_mod
from pet.config import Config

# reload() 白名单键集合现状快照（72 键，与 pet/config.py reload() 的
# "for key in (...)" 元组一致；任何增删必须同步更新本快照）。
RELOAD_WHITELIST_SNAPSHOT = frozenset({
    "animation_gap_seconds", "auto_hide_fullscreen", "autostart_wanted",
    "balance_refresh_minutes", "balance_tier_color_enabled", "balance_tier_label_idle",
    "balance_tier_label_peak", "balance_tier_labels_mode",
    "character", "character_aliases", "character_profiles", "chat_always_on_top",
    "chat_background", "chat_background_fill", "chat_background_opacity", "chat_bg_crops",
    "chat_follow_pet", "chat_ui_style", "click_show_balance", "click_show_self_talk",
    "click_sound_enabled", "click_sound_pack", "click_sound_path", "click_sound_volume",
    "collision_enabled", "collision_friction", "collision_impulse_cap",
    "collision_mass_scale", "collision_restitution", "collision_sound_enabled",
    "collision_sound_volume", "context_menu_appearance", "context_menu_layout", "context_menu_template",
    "cursor_hidden_passthrough", "drag_physics",
    "dynamic_island", "facing",
    "idle_low_fps_enabled", "idle_low_fps_threshold", "lock_position",
    "menu_easter_egg", "media_prewarm", "first_frame_cache_max_mb", "predict_prewarm_lead_ms", "ffmpeg_recycle_minutes", "modern_chat_background", "modern_chat_background_fill",
    "modern_chat_background_opacity", "modern_chat_card_opacity", "mouse_through",
    "music_sing_enabled", "no_move", "on_top", "pet_opacity", "playback_speed",
    "quick_launch_apps", "rx", "ry", "scale", "screen_name",
    "self_talk_bubble_style", "self_talk_duration_seconds", "self_talk_enabled",
    "self_talk_image_dir", "self_talk_image_scale", "self_talk_max_interval", "self_talk_min_interval",
    "self_talk_texts", "shift_drag", "show_dock_icon", "slingshot_enabled",
    "stream_capture_mode", "system_notifications_enabled", "throw_max_speed", "throw_strength",
    "experimental_single_process_spawn", "experimental_shared_decode",
})

# 默认值 dict 里不走普通白名单、由 reload() 专门路径处理的键（现状文档化）。
SPECIAL_CASED_KEYS = frozenset({"version", "proactive_screen", "agent_link", "chat"})

# 默认值 dict 键集合现状快照（77 键）= 白名单 ∪ 特例键。
DEFAULTS_SNAPSHOT = RELOAD_WHITELIST_SNAPSHOT | SPECIAL_CASED_KEYS


def _actual_reload_whitelist() -> frozenset:
    """测试探针：从 config.py 源码提取 reload() 白名单元组的真实键集合。

    白名单是 reload() 方法内的字面量，运行期无法经实例访问，故用
    inspect.getsource + 正则提取。格式变更导致提取失败时以明确信息
    失败（提示同步更新本探针），而不是静默放行。
    """
    reload_src = inspect.getsource(config_mod.Config.reload)
    m = re.search(r"for key in \((.*?)\):\n", reload_src, re.S)
    assert m, "从 reload() 源码找不到白名单元组，请检查 config.py 的白名单格式"
    return frozenset(re.findall(r'"([a-zA-Z0-9_]+)"', m.group(1)))


def _actual_defaults_keys(tmp_path) -> frozenset:
    """运行期默认值键集合：无配置文件时 Config.data 即默认值 dict 的键。"""
    cfg = Config(base=tmp_path)
    return frozenset(cfg.data)


def test_defaults_snapshot_matches_current(tmp_path):
    """默认值 dict 键集合 == 显式快照（现状文档化；新增键不改快照立即红）。"""
    assert _actual_defaults_keys(tmp_path) == DEFAULTS_SNAPSHOT


def test_reload_whitelist_snapshot_matches_current():
    """reload 白名单真实字面量 == 显式快照（现状文档化）。"""
    assert _actual_reload_whitelist() == RELOAD_WHITELIST_SNAPSHOT


def test_every_defaults_key_is_whitelisted_or_special_cased(tmp_path):
    """核心护栏：默认值 dict 新增键必须登记白名单（或列入特例集），否则立即红。"""
    defaults = _actual_defaults_keys(tmp_path)
    whitelist = _actual_reload_whitelist()
    assert defaults - SPECIAL_CASED_KEYS <= whitelist


def test_whitelist_has_no_orphan_keys(tmp_path):
    """白名单键都必须存在于默认值 dict（无孤儿白名单键）。"""
    defaults = _actual_defaults_keys(tmp_path)
    assert _actual_reload_whitelist() <= defaults


def test_special_cased_keys_are_the_only_difference(tmp_path):
    """默认值与白名单的差集恰好是文档化的特例键（特例集不得悄悄扩大/缩小）。"""
    defaults = _actual_defaults_keys(tmp_path)
    whitelist = _actual_reload_whitelist()
    assert defaults - whitelist == SPECIAL_CASED_KEYS
    assert whitelist - defaults == frozenset()
