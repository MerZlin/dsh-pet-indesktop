from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import paths
from .engine.geometry import SCALE_MAX, SCALE_MIN
from .platform import hotkey_spec

logger = logging.getLogger("petcore.config")

SCHEMA_VERSION = 1

#: 角色自定义显示名上限（按**字符**数，不是字节）：中文名字与界面下拉的宽度都按这个口径。
ALIAS_MAX_CHARS = 24

#: 桌宠身份上限（含主宠 slot 0）：身份是 0..MAX_PETS-1，配置落在 `config-pet-N.json`。
#: 「无上限」在这里没有意义——每个身份背后是一个真实窗口、一份独立配置与一对碰撞结算，
#: 99 是能保证「身份空间足够大」又让碰撞拍仍能守住 16ms 的实测上限（见 L126）。
MAX_PETS = 99

MIGRATION_STATUSES = frozenset({
    "not_needed",
    "migrated",
    "already_migrated",
    "skipped_invalid",
    "failed_backup",
    "failed_write",
    "failed_validation",
})


@dataclass(frozen=True)
class MigrationRecord:
    """诊断一次迁移尝试；不包含配置值，避免把凭据写入日志。"""

    status: str
    source: str | None = None
    target: str | None = None
    backup: str | None = None
    stage: str = ""
    error: str = ""
    retryable: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

# Settings which belong to the process rather than the pet selected in the
# settings window.  Unknown keys deliberately stay per-pet so new pet-local
# feature settings do not silently become global.
GLOBAL_CONFIG_KEYS = frozenset(
    {
        "active_pets",
        "allow_feature_mods",
        "market_index_url",
        "click_through",
        # 全屏自动隐藏是**进程级**事实（一个共享 watcher、一次探测隐藏所有宠），
        # 所以必须是全局键：子宠设置页勾掉它就该对所有宠生效。
        "auto_hide_fullscreen",
        # 角色别名是**用户级偏好**（同一角色 id 在所有宠上就该同名），子宠设置页
        # 写的必须是同一份，否则「主宠改了子宠还是旧名」。
        "character_aliases",
        # 余额峰谷提示：服务商计价是进程级事实（一只宠改了两只都该变），
        # 与「点击显示余额」（按宠保存）区分开。
        "balance_tier_labels_mode",
        "balance_tier_label_peak",
        "balance_tier_label_idle",
        "balance_tier_color_enabled",
        # 这三个由**共享**的 feature 运行时读取（每个 owner 只有一个 runtime，
        # 不按宠物实例化），所以必须是全局键，否则子宠设置页写入永远不生效。
        "network_info_enabled",
        "memory_trim_enabled",
        "quiet_minutes_default",
        "hotkey_toggle_pet",
        "hotkey_open_chat",
        "hotkey_look_screen",
        "hotkey_screen_capture",
        "ai_service",
        "ai_model",
        "ai_system_prompt",
        "ai_persona",
        "ai_vision_enabled",
        "ai_temperature",
        "ai_look_screen_auto_open",
        "ai_thinking",
        "ai_reasoning_effort",
        "ai_animation_enabled",
        "ai_animation_thinking",
        "ai_animation_min_seconds",
        "ai_memory_enabled",
        "ai_memory_inject",
        "ai_memory_max_entries",
        "ai_web_search",
        "ai_web_engine",
        "ai_web_results",
        "ai_web_fetch",
        "ai_proactive_enabled",
        "ai_proactive_whitelist",
        "ai_proactive_dwell_seconds",
        "ai_proactive_change_threshold",
        "ai_proactive_daily_cap",
        "ai_proactive_cooldown_seconds",
        "ai_proactive_min_interval_seconds",
        "ai_proactive_require_idle",
        "ai_proactive_min_idle_seconds",
        "ai_proactive_dry_run",
        "ai_proactive_pre_cue",
        "ai_proactive_sync_chat",
        "ai_agent_enabled",
        "ai_agent_dsh_enabled",
        "ai_agent_events_enabled",
        "ai_agent_custom_agents",
        "ai_agent_animation_enabled",
        "ai_agent_command_enabled",
        "ai_agent_activity_bubbles",
        "ai_agent_done_notice",
        "ai_agent_min_interval_seconds",
        "ai_agent_sound_enabled",
        "ai_agent_sound_volume",
        "ai_agent_sound_cooldown_seconds",
        "ai_agent_sound_start_path",
        "ai_agent_sound_done_path",
        "ai_agent_sound_error_path",
        "island_enabled",
        "island_show_name",
        "island_show_status",
        "island_show_messages",
        "island_info_mode",
        "island_custom_text",
        "island_style",
        "island_opacity",
        "island_accent",
        "island_click_action",
        "island_metrics",
        "island_x",
        "island_y",
        "island_edge_dock",
        "island_dock_edge",
        "island_collision",
        "chat_always_on_top",
        "chat_follow_pet",
        "chat_background",
        "chat_accent",
        "chat_font_size",
        "chat_font_family",
        "last_settings_page",
        "menu_hidden",
        "menu_layout",
        "menu_order",
        "quick_launch_apps",
        "spawn_inherit_size",
        "spawn_scale",
        "stream_capture_mode",
        "stream_capture_keycolor",
        "stream_key_color",
        "ui_font_size",
        "weather_enabled",
        "weather_city",
        "weather_city_list",
        "weather_city_codes",
        "file_reader_enabled",
        "file_reader_progress_seconds",
        "file_reader_record",
        "speech_backend",
        "speech_fallback",
        "speech_voice",
        "speech_rate",
        "speech_volume",
        "speech_cache_limit",
        "speech_doubao_voice",
        "speech_mimo_voice",
        "speech_local_endpoint",
        "speech_local_protocol",
        "speech_local_path",
        "speech_local_model",
        "speech_local_voice",
        "speech_local_format",
        "speech_local_timeout",
        "speech_sapi_voice",
        "asr_enabled",
        "asr_engine",
        "asr_endpoint",
        "asr_model",
        "asr_language",
        "asr_device",
        "asr_auto_send",
        "asr_max_seconds",
        "asr_timeout",
        "ai_voice_reply",
        "voice_chime_enabled",
        "voice_chime_schedule",
        "voice_chime_times",
        "voice_chime_show_bubble",
        "voice_chime_quotes",
        "voice_chime_anim",
        "voice_chime_provider",
        "voice_chime_voice",
        "voice_chime_rate",
        "voice_chime_volume",
        "music_enabled",
        "music_sing_enabled",
        "music_sing_grace_seconds",
        "music_lyric_enabled",
        "music_lyric_lead_seconds",
        "music_lyric_cache_limit",
        "music_lyric_offset_seconds",
        "music_browser_media_enabled",
        "music_player_paths",
        "music_player",
        "music_filter_enabled",
        "music_filter_extra_ids",
        "music_title_format",
        "music_audio_device",
        "music_audio_device_name",
    }
)

DEFAULTS: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "character": "shenshen",
    "scale": 0.72,
    "always_on_top": True,
    "click_through": False,
    "multi_screen": False,
    "screen_name": None,
    "rx": None,
    "ry": None,
    "body_rx": None,
    "body_ry": None,
    "pos_x": None,
    "pos_y": None,
    "click_sound": True,
    "click_sound_pack": {"kind": "builtin", "id": "default", "path": ""},
    "volume": 0.8,
    "ui_font_size": 13,
    "bubble_text_scale": 100,
    "menu_animation_icons": True,
    "stream_capture_mode": False,
    "stream_capture_keycolor": False,
    "stream_key_color": "#00FF00",
    # 全屏游戏/视频期间自动隐藏所有桌宠（仅 Windows 有探测；默认开，同主分支）。
    "auto_hide_fullscreen": True,
    # 角色自定义显示名 {角色id: 自定义名}，空名 = 恢复默认（目录显示名）。
    "character_aliases": {},
    "easter_egg_enabled": True,
    "easter_egg_title": "鲸鱼娘彩蛋",
    "easter_egg_hint": "点击任意处关闭",
    "easter_egg_avatar": "",
    "easter_egg_image_dir": "",
    "easter_egg_image_scale": 100,
    "easter_egg_display": "bubble",
    "frameseq": "auto",
    "allow_feature_mods": False,
    "market_index_url": "",
    "edge_probe_enabled": False,
    "hotkey_toggle_pet": "",
    "hotkey_open_chat": "",
    "hotkey_look_screen": "",
    "hotkey_screen_capture": "",
    "ai_service": "",
    "ai_model": "",
    "ai_system_prompt": "",
    "ai_persona": "",
    "ai_vision_enabled": True,
    "ai_temperature": 0.7,
    "ai_look_screen_auto_open": False,
    "ai_thinking": "auto",
    "ai_reasoning_effort": "high",
    "ai_animation_enabled": True,
    "ai_animation_thinking": True,
    "ai_animation_min_seconds": 2.0,
    "ai_memory_enabled": True,
    "ai_memory_inject": 3,
    "ai_memory_max_entries": 500,
    "ai_web_search": False,
    "ai_web_engine": "baidu",
    "ai_web_results": 5,
    "ai_web_fetch": True,
    "ai_proactive_enabled": False,
    "ai_proactive_whitelist": [],
    "ai_proactive_dwell_seconds": 45,
    "ai_proactive_change_threshold": 8,
    "ai_proactive_daily_cap": 15,
    "ai_proactive_cooldown_seconds": 300,
    "ai_proactive_min_interval_seconds": 60,
    "ai_proactive_require_idle": False,
    "ai_proactive_min_idle_seconds": 30,
    "ai_proactive_dry_run": False,
    "ai_proactive_pre_cue": True,
    "ai_proactive_sync_chat": True,
    "ai_agent_enabled": False,
    "ai_agent_dsh_enabled": True,
    "ai_agent_events_enabled": True,
    "ai_agent_custom_agents": [],
    "ai_agent_animation_enabled": True,
    "ai_agent_command_enabled": True,
    "ai_agent_activity_bubbles": True,
    "ai_agent_done_notice": True,
    "ai_agent_min_interval_seconds": 2.0,
    "ai_agent_sound_enabled": False,
    "ai_agent_sound_volume": 0.65,
    "ai_agent_sound_cooldown_seconds": 2.0,
    "ai_agent_sound_start_path": "builtin:agent-start",
    "ai_agent_sound_done_path": "builtin:agent-done",
    "ai_agent_sound_error_path": "builtin:agent-error",
    "island_enabled": False,
    "island_show_name": True,
    "island_show_status": True,
    "island_show_messages": False,
    "island_info_mode": "time",
    "island_custom_text": "",
    "island_style": "dark",
    "island_opacity": 1.0,
    "island_accent": "blue",
    "island_click_action": "expand",
    "island_metrics": ["cpu", "mem"],
    "island_x": None,
    "island_y": None,
    # 贴边停靠：拖到屏幕边缘 40px 内松手 → 收成 16px 细条，悬停滑出预览。
    "island_edge_dock": True,
    "island_dock_edge": "none",
    # 岛 × 桌宠碰撞：桌宠撞岛被弹、拖岛扫过桌宠把它顶飞。默认开（主分支同款）。
    "island_collision": True,
    "click_show_balance": False,
    # DeepSeek 峰谷提示：文案模式、自定义文本（custom 模式生效）、颜色开关（高峰红/空闲绿）。
    "balance_tier_labels_mode": "default",
    "balance_tier_label_peak": "",
    "balance_tier_label_idle": "",
    "balance_tier_color_enabled": True,
    "chat_always_on_top": False,
    "chat_follow_pet": False,
    "chat_background": "",
    "chat_accent": "",
    # 0 = 跟随 ui_font_size；字体族空 = 系统默认。两者只作用于对话窗。
    "chat_font_size": 0,
    "chat_font_family": "",
    "click_squash_enabled": True,
    "click_squash_ms": 220,
    "click_squash_bounces": 1,
    "click_squash_strength": 100,
    "drag_physics_enabled": False,
    "drop_gravity_enabled": False,
    "throw_strength": "standard",
    "slingshot_enabled": True,
    "collision_enabled": True,
    "collision_sound": True,
    "golden_spin_on_click": False,
    "golden_spin_direct": False,
    "throw_egg_enabled": True,
    "file_feeding_enabled": True,
    "no_move": False,
    "lock_position": False,
    "shift_drag": False,
    "playback_speed": 1.0,
    "animation_gap_seconds": 0.0,
    "head_pat_enabled": True,
    "head_pat_animation_mode": "default_click",
    "head_pat_animation_names": [],
    "anim_weight_idle": 30,
    "anim_weight_turn": 10,
    "anim_weight_act": 40,
    "anim_weight_move": 20,
    # 待机随机动作总开关；关闭后动作池不自播，右键手动播放不受影响。
    "anim_random_auto": True,
    # 动画策略名单：分类 id（idle/random/...）或动画名。
    # auto_off = 仅手动；blocked = 完全禁用（自动与菜单都拦）。
    "anim_auto_off": [],
    "anim_blocked": [],
    "active_pets": [0],
    "spawn_inherit_size": True,
    "spawn_scale": 0.72,
    "self_talk_enabled": False,
    "self_talk_texts": [
        "好女孩……",
        "好模型……",
        "欧鲸鲸……",
        "今天也要认真工作呀。",
        "再陪你一会儿。",
        "今天也要好好休息。",
        "忙完了就伸个懒腰吧。",
    ],
    "self_talk_daily_enabled": True,
    "self_talk_daily_texts": [
        "今天也一起慢慢来吧。",
        "窗外的光不错，适合做点小事。",
        "先喝口水，再继续也不迟。",
        "今天的第一件小事，想做什么？",
        "如果有点累，就靠一会儿再走。",
        "别把今天排得太满，留一点空。",
        "今天的你，也值得被好好对待。",
        "做不完也没关系，明天还有时间。",
        "记得起来走两步，肩颈会谢谢你。",
        "今天适合把一件小事做完。",
        "有我在旁边，慢慢来就好。",
        "今天也要给自己留一点喜欢的时间。",
    ],
    "self_talk_daily_sequence": False,
    "self_talk_min": 20,
    "self_talk_max": 60,
    "self_talk_duration": 5,
    "self_talk_image_dir": "",
    "self_talk_image_scale": 100,
    "self_talk_image_chance": 30,
    "self_talk_voice_enabled": True,
    "self_talk_champion_enabled": True,
    "self_talk_champion_chance": 8,
    "click_talk_enabled": False,
    "click_talk_texts": ["我在呢。"],
    "click_talk_voice_enabled": True,
    "quick_launch_apps": [],
    "quiet_minutes_default": 30,
    "network_info_enabled": True,
    "memory_trim_enabled": True,
    "divination_enabled": True,
    "divination_speak": False,
    "divination_hint": True,
    "festival_enabled": True,
    "festival_reminder_enabled": False,
    "festival_reminder_cn": True,
    "festival_reminder_solar_terms": True,
    "festival_reminder_west": True,
    "festival_reminder_mode": "times",
    "festival_reminder_count": 2,
    "festival_reminder_times": "09:00",
    "festival_reminder_show_quote": True,
    "festival_reminder_speak": False,
    "festival_custom_quotes_cn": "",
    "festival_custom_quotes_west": "",
    "festival_birthday": "",
    "weather_enabled": True,
    "weather_city": "",
    "weather_city_list": [],
    "weather_city_codes": {},
    "file_reader_enabled": True,
    "file_reader_progress_seconds": 15,
    "file_reader_record": True,
    "todo_items": [],
    "todo_reminder_enabled": True,
    "todo_reminder_lead_minutes": 5,
    "todo_reminder_speak": False,
    "voice_chime_enabled": False,
    "voice_chime_schedule": "hourly",
    "voice_chime_times": [],
    "voice_chime_show_bubble": True,
    "voice_chime_quotes": [],
    "voice_chime_anim": "悠闲哼歌",
    "voice_chime_provider": "",
    "voice_chime_voice": "",
    "voice_chime_rate": 1.0,
    "voice_chime_volume": 1.0,
    "music_enabled": True,
    "music_sing_enabled": True,
    "music_sing_grace_seconds": 5.0,
    "music_lyric_enabled": True,
    "music_lyric_lead_seconds": 0.3,
    "music_lyric_cache_limit": 80,
    "music_lyric_offset_seconds": 0.0,
    "music_browser_media_enabled": False,
    "music_player_paths": {},
    "music_player": "",
    "music_filter_enabled": False,
    "music_filter_extra_ids": "",
    "music_title_format": "",
    "music_audio_device": "",
    "music_audio_device_name": "",
    "speech_backend": "edge",
    "speech_fallback": "sapi",
    "speech_voice": "",
    "speech_rate": 1.0,
    "speech_volume": 1.0,
    "speech_cache_limit": 200,
    "speech_doubao_voice": "",
    "speech_mimo_voice": "",
    "speech_local_endpoint": "",
    "speech_local_protocol": "openai",
    "speech_local_path": "",
    "speech_local_model": "tts-1",
    "speech_local_voice": "",
    "speech_local_format": "mp3",
    "speech_local_timeout": 30.0,
    "speech_sapi_voice": "",
    "asr_enabled": True,
    "asr_engine": "sapi",
    "asr_endpoint": "",
    "asr_model": "",
    "asr_language": "zh",
    "asr_device": 0,
    "asr_device_name": "",
    "asr_stream_enabled": True,
    "asr_partial_interval_ms": 800,
    "asr_auto_send": False,
    "asr_max_seconds": 30,
    "asr_timeout": 30.0,
    "ai_voice_reply": False,
    "menu_hidden": [],
    "menu_order": [],
    "menu_layout": None,
    "positions": {},
    "last_settings_page": "general",
}

_LEGACY_IMPORT_KEYS = ("character", "scale", "rx", "ry", "screen_name")

_LEGACY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "character": ("character",),
    "scale": ("scale",),
    "rx": ("rx",),
    "ry": ("ry",),
    "screen_name": ("screen_name",),
    "body_rx": ("body_rx",),
    "body_ry": ("body_ry",),
    "pos_x": ("pos_x",),
    "pos_y": ("pos_y",),
    "always_on_top": ("always_on_top", "on_top"),
    "click_through": ("click_through", "mouse_through"),
    "playback_speed": ("playback_speed",),
    "animation_gap_seconds": ("animation_gap_seconds", "animation_gap"),
    "self_talk_enabled": ("self_talk_enabled",),
    "self_talk_min": ("self_talk_min", "self_talk_min_interval"),
    "self_talk_max": ("self_talk_max", "self_talk_max_interval"),
    "self_talk_duration": ("self_talk_duration", "self_talk_duration_seconds"),
    "self_talk_texts": ("self_talk_texts",),
    "self_talk_image_dir": ("self_talk_image_dir",),
    "self_talk_image_scale": ("self_talk_image_scale",),
    "self_talk_image_chance": ("self_talk_image_chance",),
    "easter_egg_title": ("easter_egg_title",),
    "easter_egg_hint": ("easter_egg_hint",),
    "easter_egg_avatar": ("easter_egg_avatar",),
    "bubble_text_scale": ("bubble_text_scale",),
    "drag_physics_enabled": ("drag_physics_enabled", "drag_physics"),
    "drop_gravity_enabled": ("drop_gravity_enabled", "drop_gravity"),
    "lock_position": ("lock_position",),
    "shift_drag": ("shift_drag",),
    "click_sound": ("click_sound", "click_sound_enabled"),
    "click_sound_pack": ("click_sound_pack",),
    "volume": ("volume", "click_sound_volume"),
    "collision_enabled": ("collision_enabled",),
    "collision_sound": ("collision_sound", "collision_sound_enabled"),
    "golden_spin_on_click": ("golden_spin_on_click",),
    "golden_spin_direct": ("golden_spin_direct",),
    "edge_probe_enabled": ("edge_probe_enabled",),
    "no_move": ("no_move",),
    "throw_strength": ("throw_strength",),
    "slingshot_enabled": ("slingshot_enabled",),
    "file_feeding_enabled": ("file_feeding_enabled",),
    "quiet_minutes_default": ("quiet_minutes_default",),
    "quick_launch_apps": ("quick_launch_apps",),
    "divination_enabled": ("divination_enabled",),
    "festival_enabled": ("festival_enabled",),
    "todo_items": ("todo_items",),
    "voice_chime_enabled": ("voice_chime_enabled",),
    "music_enabled": ("music_enabled",),
    # 旧壳的 foobar 窗口标题格式串：新壳把它扩展到所有"不注册系统媒体"的播放器，
    # 键名改成 music_title_format，迁移时按别名搬过来（否则老用户要重新填一遍）。
    "music_title_format": ("music_title_format", "foobar_title_format"),
    "ui_font_size": ("ui_font_size",),
    "stream_capture_mode": ("stream_capture_mode",),
    "allow_feature_mods": ("allow_feature_mods",),
    "active_pets": ("active_pets",),
    "spawn_inherit_size": ("spawn_inherit_size",),
    "spawn_scale": ("spawn_scale",),
    "menu_hidden": ("menu_hidden",),
    "menu_order": ("menu_order",),
    "menu_layout": ("menu_layout", "context_menu_layout"),
}


# 旧壳里与 petcore **同名**的键：白名单式映射会静默丢掉它们（审计 AUD-08）。
# 这些键在旧壳里真的被读取（待办/节日/报时/天气/音乐/快捷键），必须按同名直搬，
# 否则用户升级后这些设置回到默认值，而迁移报告全绿。
_LEGACY_SAME_NAME_KEYS = (
    "chat_always_on_top",
    "chat_background",
    "chat_follow_pet",
    "click_show_balance",
    "divination_hint",
    "divination_speak",
    "festival_birthday",
    "festival_custom_quotes_cn",
    "festival_custom_quotes_west",
    "festival_reminder_cn",
    "festival_reminder_count",
    "festival_reminder_enabled",
    "festival_reminder_mode",
    "festival_reminder_show_quote",
    "festival_reminder_solar_terms",
    "festival_reminder_speak",
    "festival_reminder_times",
    "festival_reminder_west",
    "hotkey_look_screen",
    "hotkey_open_chat",
    "hotkey_screen_capture",
    "island_metrics",
    "music_browser_media_enabled",
    "music_lyric_cache_limit",
    "music_lyric_enabled",
    "music_lyric_lead_seconds",
    "music_player_paths",
    "music_sing_enabled",
    "music_sing_grace_seconds",
    "todo_reminder_enabled",
    "todo_reminder_lead_minutes",
    "todo_reminder_speak",
    "voice_chime_rate",
    "voice_chime_schedule",
    "voice_chime_show_bubble",
    "voice_chime_voice",
    "voice_chime_volume",
    "weather_city",
    "weather_city_codes",
    "weather_city_list",
    "weather_enabled",
)
_LEGACY_KEY_ALIASES.update({key: (key,) for key in _LEGACY_SAME_NAME_KEYS})

# ---------------------------------------------------------------- 扩展偏好键
# `config.json` 顶层只允许 `DEFAULTS` 里的键——这是「配置里永不出现 API Key」的
# 唯一机制。第三方扩展的偏好键统一落在受管的 `extra` 区块，`get/set` 对调用方
# 透明，但顶层键集仍然可枚举、可审计。
_EXTRA_FIELD = "extra"
_EXTRA_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_EXTRA_VALUE_LIMIT = 4096
_EXTRA_TOTAL_LIMIT = 65536
_SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}|[A-Fa-f0-9]{32,})"
)


def _looks_like_secret(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_SECRET_VALUE_RE.search(value))
    if isinstance(value, dict):
        return any(_looks_like_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_looks_like_secret(item) for item in value)
    return False


def _sanitize_extra(raw: Any) -> dict[str, Any]:
    """校验 `extra` 区块：键名白名单、单值/总量上限、拒绝密钥样式。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    total = 0
    for key, value in raw.items():
        text = str(key)
        if not _EXTRA_KEY_RE.match(text):
            logger.warning("config extra key rejected: %s", text[:64])
            continue
        if _looks_like_secret(value):
            logger.warning("config extra value rejected as credential-like: %s", text[:64])
            continue
        try:
            size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            continue
        if size > _EXTRA_VALUE_LIMIT:
            logger.warning("config extra value too large: %s", text[:64])
            continue
        if total + size > _EXTRA_TOTAL_LIMIT:
            break
        cleaned[text] = value
        total += size
    return cleaned


def legacy_unmapped_keys(raw: dict[str, Any]) -> list[str]:
    """旧壳里有、但别名表覆盖不到的键（迁移报告要如实点名，不许静默丢）。"""
    known = {alias for aliases in _LEGACY_KEY_ALIASES.values() for alias in aliases}
    return sorted(str(key) for key in raw if str(key) not in known)


def _map_legacy_data(raw: dict[str, Any]) -> dict[str, Any]:
    """Map known v4/standalone keys without copying secrets or unknown data."""
    mapped: dict[str, Any] = {}
    for new_key, aliases in _LEGACY_KEY_ALIASES.items():
        for old_key in aliases:
            if old_key in raw:
                mapped[new_key] = raw[old_key]
                break
    return mapped


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if number != number:
        return default
    return max(low, min(high, number))


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _head_pat_names(value: Any) -> list[str]:
    """Normalize configured head-pat animation names without storing paths."""
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()[:160]
        if not name or name in result:
            continue
        result.append(name)
        if len(result) >= 64:
            break
    return result


def _animation_tokens(value: Any) -> list[str]:
    """动画策略名单：分类 id 或动画名，去重、限长、不存路径。

    名字是跨角色保留的（切角色后不丢设置），所以这里不按当前角色过滤。
    """
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        token = item.strip()[:160]
        if not token or token in result:
            continue
        result.append(token)
        if len(result) >= 256:
            break
    return result


#: 灵动岛指标段的合法 id；GPU/FPS 由可选平台探针提供，能力不可用时显示原因。
ISLAND_METRIC_IDS = ("network", "cpu", "mem", "disk", "gpu", "fps")
ISLAND_METRIC_LIMIT = 8
#: 灵动岛的强调色词表。
ISLAND_DOCK_EDGES = frozenset({"none", "top", "bottom", "left", "right"})
ISLAND_ACCENTS = ("blue", "green", "purple", "pink", "orange")
#: 余额峰谷提示的文案模式（default=高峰/空闲、liangwen=梁文峰/梁文谷、custom=自定义）。
BALANCE_TIER_MODES = frozenset({"default", "liangwen", "custom"})
#: 自定义峰谷文本的长度上限（气泡副标题只有一行，超过会被气泡截断）。
BALANCE_TIER_LABEL_MAX = 12


def _island_metrics(value: Any) -> list[str]:
    """清洗 ``island_metrics``：只留合法 id、去重、保序、限长（未知 id 直接丢）。"""
    from .island.segments import BUILTIN_SEGMENT_IDS

    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        metric_id = str(item or "").strip().lower()
        if metric_id in result:
            continue
        if metric_id not in BUILTIN_SEGMENT_IDS or metric_id not in ISLAND_METRIC_IDS:
            continue
        result.append(metric_id)
        if len(result) >= ISLAND_METRIC_LIMIT:
            break
    return result


#: 自定义联动 Agent 条目上限（防配置文件被塞爆）。
AGENT_LINK_MAX_CUSTOM = 8
#: 内置来源占用的 key，自定义条目不得重复。
AGENT_LINK_RESERVED_KEYS = frozenset({"dsh"})


def _agent_link_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not 1 <= len(key) <= 32 or not key[0].isalnum():
        return ""
    if not all(char.isalnum() or char in "-_" for char in key):
        return ""
    return key


def _agent_links(value: Any) -> list[dict]:
    """清洗 ``ai_agent_custom_agents``：``[{key,name,path}]``，非法条目直接丢弃。"""
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if len(result) >= AGENT_LINK_MAX_CUSTOM:
            break
        if not isinstance(item, dict):
            continue
        key = _agent_link_key(item.get("key"))
        if not key or key in seen or key in AGENT_LINK_RESERVED_KEYS:
            continue
        path = str(item.get("path") or "").strip()[:500]
        if not path:
            continue
        name = str(item.get("name") or "").strip()[:50] or key
        seen.add(key)
        result.append({"key": key, "name": name, "path": path})
    return result


_CLICK_SOUND_KINDS = frozenset({"builtin", "file", "folder"})
_CLICK_SOUND_BUILTINS = frozenset({"default", "duck"})
_CLICK_SOUND_PATH_MAX = 500

#: 语音合成的可选后端（与 ``petcore.net.tts.PROVIDER_SPECS`` 的 id 对应；这里不
#: import 那个模块，配置层不该把网络实现拖进启动路径）。
_SPEECH_BACKENDS = frozenset({"edge", "sapi", "doubao", "mimo", "local"})
_LOCAL_PROTOCOLS = frozenset({"openai", "simple"})
_LOCAL_FORMATS = frozenset({"mp3", "wav"})


def _speech_backend(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _SPEECH_BACKENDS else DEFAULTS["speech_backend"]


def _speech_fallback(value: Any) -> str:
    """空串表示「不要回退」；未知值回落默认后备（``sapi``）。"""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if text in _SPEECH_BACKENDS else DEFAULTS["speech_fallback"]


def _local_endpoint(value: Any) -> str:
    """本地模型地址：补 ``http://``、只留 http(s)、去尾部斜杠；空串表示未配置。

    这里**不用** ``urllib.parse``：`petcore/config.py` 不在联网白名单里
    （`tests/test_petcore_architecture.py` 会红），只做前缀与主机段校验即可。
    """
    text = str(value or "").strip()[:300]
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    lowered = text.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return ""
    if "?" in text or "#" in text:
        # 路径是拼在地址后面的；带查询串/# 会拼出 http://host?x/v1/audio/speech。
        return ""
    rest = text.split("://", 1)[1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not host or any(char.isspace() for char in host):
        return ""
    return text.rstrip("/")


_CHIME_SCHEDULES = ("hourly", "every_30", "every_15", "every_5", "every_minute", "custom")


def _chime_schedule(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _CHIME_SCHEDULES else DEFAULTS["voice_chime_schedule"]


def _clock_times(value: Any) -> list[str]:
    """``HH:MM`` 列表清洗：非法项丢弃、去重、按时间排序。"""
    items: list[str] = []
    for raw in value if isinstance(value, (list, tuple)) else ():
        text = str(raw or "").strip()
        parts = text.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            continue
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            items.append(f"{hour:02d}:{minute:02d}")
    return sorted(set(items))


def _alias_text(value: Any) -> str:
    """单个角色别名清洗：去首尾空白、拒绝换行/控制字符、截断到 24 个字符。

    返回空串表示「没有可用别名」（= 恢复默认）。这里按**字符**截断，不按字节：
    Qt 控件本来就是按字符排版，按字节切会把中文名字切成半个字。
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text[:ALIAS_MAX_CHARS]


def _character_aliases(value: Any) -> dict[str, str]:
    """清洗 ``{角色id: 自定义名}``（总是返回新 dict，不与 DEFAULTS 共享可变对象）。"""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, str] = {}
    for raw_id, raw_name in value.items():
        if not isinstance(raw_id, str):
            continue
        character_id = raw_id.strip()
        if not character_id:
            continue
        name = _alias_text(raw_name)
        if name:
            cleaned[character_id] = name
    return cleaned


def _click_sound_pack(value: Any) -> dict[str, str]:
    """清洗点击音效包（总是返回新 dict，不与 DEFAULTS 共享可变对象）。"""
    if not isinstance(value, dict):
        value = {}
    kind = str(value.get("kind") or "builtin").strip().lower()
    if kind not in _CLICK_SOUND_KINDS:
        kind = "builtin"
    pack_id = str(value.get("id") or "default").strip()
    if pack_id not in _CLICK_SOUND_BUILTINS:
        pack_id = "default"
    path = str(value.get("path") or "").strip()[:_CLICK_SOUND_PATH_MAX]
    return {"kind": kind, "id": pack_id, "path": path}


def _pets(value: Any) -> list[int]:
    if not isinstance(value, list):
        return [0]
    slots: list[int] = []
    for item in value:
        try:
            slot = int(item)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= slot < MAX_PETS and slot not in slots:
            slots.append(slot)
    if 0 not in slots:
        slots.insert(0, 0)
    return sorted(slots)


def _positions(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for character_id, entry in value.items():
        if not isinstance(entry, dict):
            continue
        record = {
            "screen_name": _optional_text(entry.get("screen_name")),
            "rx": _optional_number(entry.get("rx")),
            "ry": _optional_number(entry.get("ry")),
            "body_rx": _optional_number(entry.get("body_rx")),
            "body_ry": _optional_number(entry.get("body_ry")),
            "pos_x": _optional_number(entry.get("pos_x")),
            "pos_y": _optional_number(entry.get("pos_y")),
        }
        if any(item is not None for item in record.values()):
            cleaned[str(character_id)] = record
    return cleaned


class Config:
    def __init__(self, root: Path, data: dict[str, Any] | None = None, *, filename: str | None = None):
        self.root = Path(root)
        self.path = self.root / filename if filename else paths.config_path(self.root)
        self.migration_records: list[MigrationRecord] = []
        self._legacy_source: Path | None = None
        #: 损坏的 config.json 被改名留档后的路径（`--check` 会显示它）。
        self.corrupt_backup: str = ""
        self.data = self._normalize(data if data is not None else dict(DEFAULTS))

    def _record_migration(self, status: str, **kwargs: object) -> None:
        if status not in MIGRATION_STATUSES:
            raise ValueError(f"unknown migration status: {status}")
        self.migration_records.append(MigrationRecord(status=status, **kwargs))

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        portable: bool = False,
        filename: str | None = None,
        persist: bool = True,
    ) -> "Config":
        """读取配置。

        ``persist=False`` 是「只读诊断」模式（``petcore --check``）：会算出与正常
        启动一致的合并结果，但**不备份、不写文件、不迁 slot**。否则一次诊断命令
        就会改动用户数据，而且绕开 `migration.json` 标记。
        """
        root = Path(root)
        config = cls(root, filename=filename)
        if config.path.exists():
            try:
                raw = json.loads(config.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                config._record_migration(
                    "skipped_invalid",
                    source=str(config.path),
                    target=str(config.path),
                    stage="read-current",
                    error=type(exc).__name__,
                    retryable=True,
                )
                if persist:
                    config.corrupt_backup = config._quarantine_corrupt()
                raw = None
            if isinstance(raw, dict):
                config.data = config._normalize({**DEFAULTS, **raw})
                config._record_migration(
                    "not_needed", source=str(config.path), target=str(config.path), stage="load"
                )
                return config
        if filename is not None:
            config._record_migration("not_needed", target=str(config.path), stage="variant-load")
            return config
        legacy = config._read_legacy()
        if legacy:
            config.data.update(legacy)
            config.data = config._normalize(config.data)
            if not persist:
                config._record_migration(
                    "not_needed",
                    source=str(config._legacy_source) if config._legacy_source else None,
                    target=str(config.path),
                    stage="read-only",
                )
                return config
            # The legacy source is backed up before either global or slot data is
            # written.  A backup failure leaves the old source untouched and is
            # explicitly retryable on the next startup.
            if not config._ensure_legacy_backup():
                return config
            try:
                config.save()
            except OSError as exc:
                config._record_migration(
                    "failed_write",
                    source=str(config._legacy_source) if config._legacy_source else None,
                    target=str(config.path),
                    stage="write-global",
                    error=type(exc).__name__,
                    retryable=True,
                )
                return config
            config._record_migration(
                "migrated",
                source=str(config._legacy_source) if config._legacy_source else None,
                target=str(config.path),
                backup=str(config.root / "migration-backup"),
                stage="write-global",
            )
            config._migrate_legacy_slots()
        elif not config.migration_records:
            config._record_migration("not_needed", target=str(config.path), stage="load")
        return config

    def _quarantine_corrupt(self) -> str:
        """把读不动的配置文件改名留档：否则下一次 save 会把它整份覆盖掉。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.bad-{stamp}")
        try:
            os.replace(self.path, target)
        except OSError:
            logger.warning("cannot quarantine corrupt config %s", self.path, exc_info=True)
            return ""
        logger.warning("config.json was unreadable and has been kept as %s", target.name)
        return str(target)

    def _read_legacy(self) -> dict[str, Any]:
        for root in paths.legacy_data_roots():
            path = root / "config.json"
            if not path.is_file():
                continue
            self._legacy_source = root
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._record_migration(
                    "skipped_invalid", source=str(path), stage="read-legacy", error=type(exc).__name__, retryable=True
                )
                continue
            if not isinstance(raw, dict):
                self._record_migration(
                    "skipped_invalid", source=str(path), stage="validate-legacy", error="not-object", retryable=False
                )
                continue
            imported = _map_legacy_data(raw)
            if imported:
                return self._normalize({**DEFAULTS, **imported})
            self._record_migration(
                "failed_validation", source=str(path), stage="map-legacy", error="no-supported-keys", retryable=False
            )
        return {}

    def _ensure_legacy_backup(self) -> bool:
        source_root = self._legacy_source
        if source_root is None:
            return True
        backup_root = self.root / "migration-backup"
        backup = backup_root / f"{source_root.name}-config.json"
        try:
            backup_root.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(source_root / "config.json", backup)
        except OSError as exc:
            self._record_migration(
                "failed_backup",
                source=str(source_root / "config.json"),
                target=str(self.path),
                backup=str(backup),
                stage="backup-global",
                error=type(exc).__name__,
                retryable=True,
            )
            return False
        return True

    def _migrate_legacy_slots(self) -> list[str]:
        """Copy old slot profiles with backup-first, atomic and diagnosable writes."""
        migrated: list[str] = []
        source_root = self._legacy_source
        if source_root is None:
            for root in paths.legacy_data_roots():
                if (root / "config.json").is_file():
                    source_root = root
                    self._legacy_source = root
                    break
        if source_root is None:
            return migrated
        if not self._ensure_legacy_backup():
            return migrated
        backup_root = self.root / "migration-backup"
        for candidate in sorted(source_root.glob("config-slot-*.json")) + sorted(
            source_root.glob("config-pet-*.json")
        ):
            stem = candidate.stem
            suffix = stem.removeprefix("config-slot-").removeprefix("config-pet-")
            if not suffix.isdigit() or not 0 < int(suffix) < MAX_PETS:
                self._record_migration(
                    "skipped_invalid", source=str(candidate), stage="validate-slot-name", error="invalid-slot"
                )
                continue
            target = self.root / f"config-pet-{int(suffix)}.json"
            if target.exists():
                self._record_migration(
                    "already_migrated", source=str(candidate), target=str(target), stage="slot-exists"
                )
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._record_migration(
                    "skipped_invalid", source=str(candidate), target=str(target), stage="read-slot",
                    error=type(exc).__name__, retryable=True,
                )
                continue
            if not isinstance(raw, dict):
                self._record_migration(
                    "skipped_invalid", source=str(candidate), target=str(target), stage="validate-slot",
                    error="not-object",
                )
                continue
            mapped = _map_legacy_data(raw)
            if not mapped:
                self._record_migration(
                    "failed_validation", source=str(candidate), target=str(target), stage="map-slot",
                    error="no-supported-keys",
                )
                continue
            slot_backup = backup_root / f"{source_root.name}-{candidate.name}"
            try:
                if not slot_backup.exists():
                    shutil.copy2(candidate, slot_backup)
            except OSError as exc:
                self._record_migration(
                    "failed_backup", source=str(candidate), target=str(target), backup=str(slot_backup),
                    stage="backup-slot", error=type(exc).__name__, retryable=True,
                )
                continue
            try:
                Config(self.root, mapped, filename=target.name).save()
            except OSError as exc:
                self._record_migration(
                    "failed_write", source=str(candidate), target=str(target), backup=str(slot_backup),
                    stage="write-slot", error=type(exc).__name__, retryable=True,
                )
                continue
            migrated.append(target.name)
            self._record_migration(
                "migrated", source=str(candidate), target=str(target), backup=str(slot_backup), stage="write-slot"
            )
        return migrated

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(DEFAULTS)
        normalized.update({key: data[key] for key in DEFAULTS if key in data})
        normalized["schema"] = SCHEMA_VERSION
        normalized[_EXTRA_FIELD] = _sanitize_extra(data.get(_EXTRA_FIELD))
        normalized["character"] = str(normalized.get("character") or DEFAULTS["character"])
        normalized["scale"] = _number(
            normalized.get("scale"), DEFAULTS["scale"], SCALE_MIN, SCALE_MAX
        )
        normalized["always_on_top"] = _bool(normalized.get("always_on_top"), True)
        normalized["click_through"] = _bool(normalized.get("click_through"), False)
        normalized["multi_screen"] = _bool(normalized.get("multi_screen"), False)
        normalized["screen_name"] = _optional_text(normalized.get("screen_name"))
        normalized["rx"] = _optional_number(normalized.get("rx"))
        normalized["ry"] = _optional_number(normalized.get("ry"))
        normalized["body_rx"] = _optional_number(normalized.get("body_rx"))
        normalized["body_ry"] = _optional_number(normalized.get("body_ry"))
        normalized["pos_x"] = _optional_number(normalized.get("pos_x"))
        normalized["pos_y"] = _optional_number(normalized.get("pos_y"))
        normalized["click_sound"] = _bool(normalized.get("click_sound"), True)
        normalized["click_sound_pack"] = _click_sound_pack(normalized.get("click_sound_pack"))
        normalized["volume"] = _number(normalized.get("volume"), 0.8, 0.0, 1.0)
        normalized["ui_font_size"] = int(
            round(_number(normalized.get("ui_font_size"), 13.0, 10.0, 18.0))
        )
        normalized["bubble_text_scale"] = int(
            round(_number(normalized.get("bubble_text_scale"), 100.0, 50.0, 300.0))
        )
        normalized["menu_animation_icons"] = _bool(
            normalized.get("menu_animation_icons"), True
        )
        normalized["stream_capture_mode"] = _bool(
            normalized.get("stream_capture_mode"), False
        )
        normalized["stream_capture_keycolor"] = _bool(
            normalized.get("stream_capture_keycolor"), False
        )
        key_color = str(normalized.get("stream_key_color") or "").strip()
        normalized["stream_key_color"] = (
            key_color if re.fullmatch(r"#[0-9a-fA-F]{6}", key_color) else "#00FF00"
        )
        if normalized["stream_capture_keycolor"]:
            # 色键模式必须是普通顶层窗口：没有 stream 边界时 OBS 枚举不到。
            normalized["stream_capture_mode"] = True
        normalized["auto_hide_fullscreen"] = _bool(
            normalized.get("auto_hide_fullscreen"), True
        )
        normalized["character_aliases"] = _character_aliases(
            normalized.get("character_aliases")
        )
        normalized["easter_egg_enabled"] = _bool(normalized.get("easter_egg_enabled"), True)
        title = normalized.get("easter_egg_title")
        normalized["easter_egg_title"] = (
            DEFAULTS["easter_egg_title"] if title is None else str(title)
        ).strip()[:40]
        hint = normalized.get("easter_egg_hint")
        normalized["easter_egg_hint"] = (
            DEFAULTS["easter_egg_hint"] if hint is None else str(hint)
        ).strip()[:80]
        normalized["easter_egg_avatar"] = str(
            normalized.get("easter_egg_avatar") or ""
        ).strip()[:500]
        normalized["easter_egg_image_dir"] = str(
            normalized.get("easter_egg_image_dir") or ""
        ).strip()
        normalized["easter_egg_image_scale"] = int(
            round(_number(normalized.get("easter_egg_image_scale"), 100.0, 50.0, 300.0))
        )
        display = str(normalized.get("easter_egg_display") or "bubble").strip().lower()
        normalized["easter_egg_display"] = (
            display if display in {"bubble", "window"} else "bubble"
        )
        normalized["click_squash_enabled"] = _bool(normalized.get("click_squash_enabled"), True)
        normalized["click_squash_ms"] = _number(normalized.get("click_squash_ms"), 220.0, 80.0, 1000.0)
        normalized["click_squash_bounces"] = int(
            round(_number(normalized.get("click_squash_bounces"), 1.0, 1.0, 5.0))
        )
        normalized["click_squash_strength"] = int(
            round(_number(normalized.get("click_squash_strength"), 100.0, 30.0, 200.0))
        )
        normalized["drag_physics_enabled"] = _bool(normalized.get("drag_physics_enabled"), False)
        normalized["drop_gravity_enabled"] = _bool(normalized.get("drop_gravity_enabled"), False)
        strength = str(normalized.get("throw_strength") or "standard")
        normalized["throw_strength"] = strength if strength in {"gentle", "standard", "strong", "crazy"} else "standard"
        normalized["slingshot_enabled"] = _bool(normalized.get("slingshot_enabled"), True)
        normalized["collision_enabled"] = _bool(normalized.get("collision_enabled"), True)
        normalized["collision_sound"] = _bool(normalized.get("collision_sound"), True)
        normalized["golden_spin_on_click"] = _bool(normalized.get("golden_spin_on_click"), False)
        normalized["golden_spin_direct"] = _bool(normalized.get("golden_spin_direct"), False)
        normalized["throw_egg_enabled"] = _bool(normalized.get("throw_egg_enabled"), True)
        normalized["file_feeding_enabled"] = _bool(normalized.get("file_feeding_enabled"), True)
        normalized["no_move"] = _bool(normalized.get("no_move"), False)
        normalized["lock_position"] = _bool(normalized.get("lock_position"), False)
        normalized["shift_drag"] = _bool(normalized.get("shift_drag"), False)
        normalized["playback_speed"] = _number(normalized.get("playback_speed"), 1.0, 0.5, 2.0)
        normalized["animation_gap_seconds"] = _number(
            normalized.get("animation_gap_seconds"), 0.0, 0.0, 3600.0
        )
        normalized["head_pat_enabled"] = _bool(
            normalized.get("head_pat_enabled"), True
        )
        head_pat_mode = str(normalized.get("head_pat_animation_mode") or "default_click").strip().lower()
        normalized["head_pat_animation_mode"] = (
            head_pat_mode if head_pat_mode in {"default_click", "custom", "all_single"}
            else "default_click"
        )
        normalized["head_pat_animation_names"] = _head_pat_names(
            normalized.get("head_pat_animation_names")
        )
        for key in (
            "anim_weight_idle",
            "anim_weight_turn",
            "anim_weight_act",
            "anim_weight_move",
        ):
            normalized[key] = int(
                round(_number(normalized.get(key), float(DEFAULTS[key]), 0.0, 100.0))
            )
        normalized["anim_random_auto"] = _bool(
            normalized.get("anim_random_auto"), True
        )
        normalized["anim_auto_off"] = _animation_tokens(
            normalized.get("anim_auto_off")
        )
        normalized["anim_blocked"] = _animation_tokens(
            normalized.get("anim_blocked")
        )
        normalized["active_pets"] = _pets(normalized.get("active_pets"))
        normalized["spawn_inherit_size"] = _bool(normalized.get("spawn_inherit_size"), True)
        normalized["spawn_scale"] = _number(
            normalized.get("spawn_scale"), DEFAULTS["spawn_scale"], SCALE_MIN, SCALE_MAX
        )
        normalized["self_talk_enabled"] = _bool(normalized.get("self_talk_enabled"), False)
        normalized["self_talk_texts"] = _text_list(normalized.get("self_talk_texts"))
        normalized["self_talk_daily_enabled"] = _bool(
            normalized.get("self_talk_daily_enabled"), True
        )
        normalized["self_talk_daily_texts"] = _text_list(
            normalized.get("self_talk_daily_texts")
        )
        normalized["self_talk_daily_sequence"] = _bool(
            normalized.get("self_talk_daily_sequence"), False
        )
        normalized["self_talk_min"] = _number(normalized.get("self_talk_min"), 20.0, 1.0, 3600.0)
        normalized["self_talk_max"] = _number(normalized.get("self_talk_max"), 60.0, 1.0, 3600.0)
        normalized["self_talk_duration"] = _number(
            normalized.get("self_talk_duration"), 5.0, 1.0, 120.0
        )
        normalized["self_talk_image_dir"] = str(
            normalized.get("self_talk_image_dir") or ""
        ).strip()[:500]
        normalized["self_talk_image_scale"] = int(
            round(_number(normalized.get("self_talk_image_scale"), 100.0, 50.0, 300.0))
        )
        normalized["self_talk_image_chance"] = int(
            round(_number(normalized.get("self_talk_image_chance"), 30.0, 0.0, 100.0))
        )
        normalized["self_talk_voice_enabled"] = _bool(
            normalized.get("self_talk_voice_enabled"), True
        )
        normalized["self_talk_champion_enabled"] = _bool(
            normalized.get("self_talk_champion_enabled"), True
        )
        normalized["self_talk_champion_chance"] = int(
            round(_number(normalized.get("self_talk_champion_chance"), 8.0, 0.0, 100.0))
        )
        normalized["click_talk_enabled"] = _bool(normalized.get("click_talk_enabled"), False)
        normalized["click_talk_texts"] = _text_list(normalized.get("click_talk_texts"))
        normalized["click_talk_voice_enabled"] = _bool(
            normalized.get("click_talk_voice_enabled"), True
        )
        apps = normalized.get("quick_launch_apps")
        normalized["quick_launch_apps"] = apps if isinstance(apps, list) else []
        normalized["quiet_minutes_default"] = _number(
            normalized.get("quiet_minutes_default"), 30.0, 1.0, 1440.0
        )
        normalized["network_info_enabled"] = _bool(normalized.get("network_info_enabled"), True)
        normalized["memory_trim_enabled"] = _bool(normalized.get("memory_trim_enabled"), True)
        normalized["divination_speak"] = _bool(normalized.get("divination_speak"), False)
        normalized["divination_hint"] = _bool(normalized.get("divination_hint"), True)
        normalized["divination_enabled"] = _bool(normalized.get("divination_enabled"), True)
        normalized["festival_enabled"] = _bool(normalized.get("festival_enabled"), True)
        for key in ("festival_reminder_enabled", "festival_reminder_cn", "festival_reminder_solar_terms",
                    "festival_reminder_west", "festival_reminder_show_quote", "festival_reminder_speak"):
            normalized[key] = _bool(normalized.get(key), DEFAULTS[key])
        normalized["festival_reminder_count"] = int(_number(normalized.get("festival_reminder_count"), 2, 1, 6))
        if normalized.get("festival_reminder_mode") not in ("times", "custom"):
            normalized["festival_reminder_mode"] = "times"
        for key in ("festival_reminder_times", "festival_custom_quotes_cn", "festival_custom_quotes_west"):
            normalized[key] = str(normalized.get(key) or "")
        birthday = str(normalized.get("festival_birthday") or "").strip()
        try:
            from datetime import date
            parsed = date.fromisoformat("2000-" + birthday)
            normalized["festival_birthday"] = birthday if parsed.strftime("%m-%d") == birthday else ""
        except ValueError:
            normalized["festival_birthday"] = ""
        normalized["weather_enabled"] = _bool(normalized.get("weather_enabled"), True)
        normalized["weather_city"] = str(normalized.get("weather_city") or "").strip()[:24]
        normalized["weather_city_list"] = _text_list(normalized.get("weather_city_list"))[:12]
        codes = normalized.get("weather_city_codes")
        normalized["weather_city_codes"] = (
            {
                str(key)[:24]: str(value).strip()
                for key, value in codes.items()
                if isinstance(key, str) and isinstance(value, str) and str(value).strip()
            }
            if isinstance(codes, dict)
            else {}
        )
        normalized["file_reader_enabled"] = _bool(
            normalized.get("file_reader_enabled"), True
        )
        normalized["file_reader_progress_seconds"] = int(
            round(
                _number(
                    normalized.get("file_reader_progress_seconds"), 15.0, 5.0, 120.0
                )
            )
        )
        normalized["file_reader_record"] = _bool(
            normalized.get("file_reader_record"), True
        )
        normalized["todo_items"] = _text_list(normalized.get("todo_items"))
        normalized["todo_reminder_enabled"] = _bool(normalized.get("todo_reminder_enabled"), True)
        normalized["todo_reminder_speak"] = _bool(normalized.get("todo_reminder_speak"), False)
        normalized["todo_reminder_lead_minutes"] = int(_number(
            normalized.get("todo_reminder_lead_minutes"), 5, 0, 60))
        normalized["voice_chime_enabled"] = _bool(normalized.get("voice_chime_enabled"), False)
        normalized["music_enabled"] = _bool(normalized.get("music_enabled"), True)
        normalized["voice_chime_schedule"] = _chime_schedule(
            normalized.get("voice_chime_schedule")
        )
        normalized["voice_chime_times"] = _clock_times(
            normalized.get("voice_chime_times")
        )
        normalized["voice_chime_show_bubble"] = _bool(
            normalized.get("voice_chime_show_bubble"), True
        )
        normalized["voice_chime_quotes"] = _text_list(normalized.get("voice_chime_quotes"))
        normalized["voice_chime_anim"] = str(
            normalized.get("voice_chime_anim") or ""
        ).strip()[:64]
        normalized["voice_chime_provider"] = str(
            normalized.get("voice_chime_provider") or ""
        ).strip().lower()[:32]
        normalized["voice_chime_voice"] = str(
            normalized.get("voice_chime_voice") or ""
        ).strip()[:64]
        normalized["voice_chime_rate"] = _number(
            normalized.get("voice_chime_rate"), 1.0, 0.5, 2.0
        )
        normalized["voice_chime_volume"] = _number(
            normalized.get("voice_chime_volume"), 1.0, 0.0, 1.0
        )
        normalized["music_sing_enabled"] = _bool(
            normalized.get("music_sing_enabled"), True
        )
        normalized["music_sing_grace_seconds"] = _number(
            normalized.get("music_sing_grace_seconds"), 5.0, 0.0, 120.0
        )
        normalized["music_lyric_enabled"] = _bool(
            normalized.get("music_lyric_enabled"), True
        )
        normalized["music_lyric_lead_seconds"] = _number(
            normalized.get("music_lyric_lead_seconds"), 0.3, -2.0, 3.0
        )
        normalized["music_lyric_cache_limit"] = int(
            round(
                _number(normalized.get("music_lyric_cache_limit"), 80.0, 1.0, 2000.0)
            )
        )
        normalized["music_lyric_offset_seconds"] = _number(
            normalized.get("music_lyric_offset_seconds"), 0.0, -3.0, 3.0
        )
        normalized["music_browser_media_enabled"] = _bool(
            normalized.get("music_browser_media_enabled"), False
        )
        paths = normalized.get("music_player_paths")
        normalized["music_player_paths"] = (
            {
                str(key): str(value).strip()
                for key, value in paths.items()
                if isinstance(key, str)
                and isinstance(value, str)
                and str(value).strip()
            }
            if isinstance(paths, dict)
            else {}
        )
        normalized["music_player"] = str(
            normalized.get("music_player") or ""
        ).strip()[:32]
        normalized["music_filter_enabled"] = _bool(
            normalized.get("music_filter_enabled"), False
        )
        normalized["music_filter_extra_ids"] = str(
            normalized.get("music_filter_extra_ids") or ""
        ).strip()[:200]
        normalized["music_title_format"] = str(
            normalized.get("music_title_format") or ""
        ).strip()[:120]
        normalized["music_audio_device"] = str(
            normalized.get("music_audio_device") or ""
        ).strip()[:512]
        normalized["music_audio_device_name"] = str(
            normalized.get("music_audio_device_name") or ""
        ).strip()[:160]
        normalized["speech_backend"] = _speech_backend(normalized.get("speech_backend"))
        normalized["speech_fallback"] = _speech_fallback(normalized.get("speech_fallback"))
        normalized["speech_voice"] = (
            str(normalized.get("speech_voice") or "").strip()[:64]
        )
        normalized["speech_rate"] = _number(
            normalized.get("speech_rate"), 1.0, 0.5, 2.0
        )
        normalized["speech_volume"] = _number(
            normalized.get("speech_volume"), 1.0, 0.0, 1.0
        )
        normalized["speech_cache_limit"] = int(
            round(_number(normalized.get("speech_cache_limit"), 200.0, 10.0, 2000.0))
        )
        normalized["speech_doubao_voice"] = (
            str(normalized.get("speech_doubao_voice") or "").strip()[:64]
        )
        normalized["speech_mimo_voice"] = (
            str(normalized.get("speech_mimo_voice") or "").strip()[:64]
        )
        normalized["speech_local_endpoint"] = _local_endpoint(
            normalized.get("speech_local_endpoint")
        )
        protocol = str(normalized.get("speech_local_protocol") or "").strip().lower()
        normalized["speech_local_protocol"] = (
            protocol if protocol in _LOCAL_PROTOCOLS else "openai"
        )
        route = str(normalized.get("speech_local_path") or "").strip()[:120]
        if route and not route.startswith("/"):
            route = "/" + route
        normalized["speech_local_path"] = route
        normalized["speech_local_model"] = (
            str(normalized.get("speech_local_model") or "").strip()[:64]
        )
        normalized["speech_local_voice"] = (
            str(normalized.get("speech_local_voice") or "").strip()[:64]
        )
        audio_format = str(normalized.get("speech_local_format") or "").strip().lower()
        normalized["speech_local_format"] = (
            audio_format if audio_format in _LOCAL_FORMATS else "mp3"
        )
        normalized["speech_local_timeout"] = _number(
            normalized.get("speech_local_timeout"), 30.0, 5.0, 120.0
        )
        normalized["speech_sapi_voice"] = (
            str(normalized.get("speech_sapi_voice") or "").strip()[:64]
        )
        normalized["asr_enabled"] = _bool(normalized.get("asr_enabled"), True)
        asr_engine = str(normalized.get("asr_engine") or "sapi").strip().lower()
        normalized["asr_engine"] = (
            asr_engine if asr_engine in {"sapi", "http"} else "sapi"
        )
        normalized["asr_endpoint"] = (
            str(normalized.get("asr_endpoint") or "").strip()[:500]
        )
        normalized["asr_model"] = str(normalized.get("asr_model") or "").strip()[:128]
        normalized["asr_language"] = (
            str(normalized.get("asr_language") or "zh").strip()[:16] or "zh"
        )
        normalized["asr_device"] = int(
            round(_number(normalized.get("asr_device"), 0.0, 0.0, 32.0))
        )
        normalized["asr_device_name"] = (
            str(normalized.get("asr_device_name") or "").strip()[:120]
        )
        normalized["asr_stream_enabled"] = _bool(
            normalized.get("asr_stream_enabled"), True
        )
        normalized["asr_partial_interval_ms"] = int(
            round(
                _number(
                    normalized.get("asr_partial_interval_ms"), 800.0, 400.0, 3000.0
                )
            )
        )
        normalized["asr_auto_send"] = _bool(normalized.get("asr_auto_send"), False)
        normalized["asr_max_seconds"] = int(
            round(_number(normalized.get("asr_max_seconds"), 30.0, 3.0, 60.0))
        )
        normalized["asr_timeout"] = _number(
            normalized.get("asr_timeout"), 30.0, 5.0, 120.0
        )
        normalized["ai_voice_reply"] = _bool(normalized.get("ai_voice_reply"), False)
        frameseq = str(normalized.get("frameseq") or "auto")
        normalized["frameseq"] = frameseq if frameseq in {"auto", "off", "only"} else "auto"
        normalized["allow_feature_mods"] = _bool(normalized.get("allow_feature_mods"), False)
        normalized["edge_probe_enabled"] = _bool(normalized.get("edge_probe_enabled"), False)
        normalized["hotkey_toggle_pet"] = hotkey_spec.normalize(
            normalized.get("hotkey_toggle_pet")
        )
        for hotkey_key in (
            "hotkey_open_chat",
            "hotkey_look_screen",
            "hotkey_screen_capture",
        ):
            normalized[hotkey_key] = hotkey_spec.normalize(normalized.get(hotkey_key))
        normalized["ai_service"] = str(normalized.get("ai_service") or "").strip()
        normalized["ai_model"] = str(normalized.get("ai_model") or "").strip()
        normalized["ai_system_prompt"] = str(normalized.get("ai_system_prompt") or "")[:4000]
        normalized["ai_persona"] = str(normalized.get("ai_persona") or "").strip()[:64]
        normalized["ai_vision_enabled"] = _bool(
            normalized.get("ai_vision_enabled"), True
        )
        normalized["ai_temperature"] = _number(
            normalized.get("ai_temperature"), 0.7, 0.0, 2.0
        )
        normalized["ai_look_screen_auto_open"] = _bool(
            normalized.get("ai_look_screen_auto_open"), False
        )
        thinking = str(normalized.get("ai_thinking") or "auto")
        normalized["ai_thinking"] = (
            thinking if thinking in {"auto", "enabled", "disabled"} else "auto"
        )
        effort = str(normalized.get("ai_reasoning_effort") or "high")
        normalized["ai_reasoning_effort"] = (
            effort if effort in {"low", "high", "max"} else "high"
        )
        normalized["ai_animation_enabled"] = _bool(
            normalized.get("ai_animation_enabled"), True
        )
        normalized["ai_animation_thinking"] = _bool(
            normalized.get("ai_animation_thinking"), True
        )
        normalized["ai_animation_min_seconds"] = _number(
            normalized.get("ai_animation_min_seconds"), 2.0, 0.5, 30.0
        )
        normalized["ai_memory_enabled"] = _bool(
            normalized.get("ai_memory_enabled"), True
        )
        normalized["ai_memory_inject"] = int(
            round(_number(normalized.get("ai_memory_inject"), 3.0, 1.0, 8.0))
        )
        normalized["ai_memory_max_entries"] = int(
            round(_number(normalized.get("ai_memory_max_entries"), 500.0, 50.0, 2000.0))
        )
        normalized["ai_web_search"] = _bool(normalized.get("ai_web_search"), False)
        engine = str(normalized.get("ai_web_engine") or "baidu")
        normalized["ai_web_engine"] = (
            engine if engine in {"baidu", "duckduckgo", "bocha", "tavily"} else "baidu"
        )
        normalized["ai_web_results"] = int(
            _number(normalized.get("ai_web_results"), 5, 1, 10)
        )
        normalized["ai_web_fetch"] = _bool(normalized.get("ai_web_fetch"), True)
        normalized["ai_proactive_enabled"] = _bool(
            normalized.get("ai_proactive_enabled"), False
        )
        normalized["ai_proactive_whitelist"] = [
            rule.strip()[:120]
            for rule in _text_list(normalized.get("ai_proactive_whitelist"))
            if rule.strip()
        ][:50]
        normalized["ai_proactive_dwell_seconds"] = int(
            round(_number(normalized.get("ai_proactive_dwell_seconds"), 45, 15, 600))
        )
        normalized["ai_proactive_change_threshold"] = int(
            round(_number(normalized.get("ai_proactive_change_threshold"), 8, 0, 32))
        )
        normalized["ai_proactive_daily_cap"] = int(
            round(_number(normalized.get("ai_proactive_daily_cap"), 15, 1, 9999))
        )
        normalized["ai_proactive_cooldown_seconds"] = int(
            round(_number(normalized.get("ai_proactive_cooldown_seconds"), 300, 30, 7200))
        )
        normalized["ai_proactive_min_interval_seconds"] = int(
            round(
                _number(normalized.get("ai_proactive_min_interval_seconds"), 60, 30, 3600)
            )
        )
        normalized["ai_proactive_require_idle"] = _bool(
            normalized.get("ai_proactive_require_idle"), False
        )
        normalized["ai_proactive_min_idle_seconds"] = int(
            round(_number(normalized.get("ai_proactive_min_idle_seconds"), 30, 0, 3600))
        )
        normalized["ai_proactive_dry_run"] = _bool(
            normalized.get("ai_proactive_dry_run"), False
        )
        normalized["ai_proactive_pre_cue"] = _bool(
            normalized.get("ai_proactive_pre_cue"), True
        )
        normalized["ai_proactive_sync_chat"] = _bool(
            normalized.get("ai_proactive_sync_chat"), True
        )
        normalized["ai_agent_enabled"] = _bool(normalized.get("ai_agent_enabled"), False)
        normalized["ai_agent_dsh_enabled"] = _bool(
            normalized.get("ai_agent_dsh_enabled"), True
        )
        normalized["ai_agent_events_enabled"] = _bool(
            normalized.get("ai_agent_events_enabled"), True
        )
        normalized["ai_agent_custom_agents"] = _agent_links(
            normalized.get("ai_agent_custom_agents")
        )
        normalized["ai_agent_animation_enabled"] = _bool(
            normalized.get("ai_agent_animation_enabled"), True
        )
        normalized["ai_agent_command_enabled"] = _bool(
            normalized.get("ai_agent_command_enabled"), True
        )
        normalized["ai_agent_activity_bubbles"] = _bool(
            normalized.get("ai_agent_activity_bubbles"), True
        )
        normalized["ai_agent_done_notice"] = _bool(
            normalized.get("ai_agent_done_notice"), True
        )
        normalized["ai_agent_min_interval_seconds"] = round(
            _number(normalized.get("ai_agent_min_interval_seconds"), 2.0, 0.5, 10.0), 2
        )
        normalized["ai_agent_sound_enabled"] = _bool(
            normalized.get("ai_agent_sound_enabled"), False
        )
        normalized["ai_agent_sound_volume"] = round(
            _number(normalized.get("ai_agent_sound_volume"), 0.65, 0.0, 1.0), 2
        )
        normalized["ai_agent_sound_cooldown_seconds"] = round(
            _number(normalized.get("ai_agent_sound_cooldown_seconds"), 2.0, 0.0, 10.0), 2
        )
        for _sound_key, _fallback in (
            ("ai_agent_sound_start_path", "builtin:agent-start"),
            ("ai_agent_sound_done_path", "builtin:agent-done"),
            ("ai_agent_sound_error_path", "builtin:agent-error"),
        ):
            normalized[_sound_key] = (
                str(normalized.get(_sound_key) or "").strip()[:500] or _fallback
            )
        normalized["island_enabled"] = _bool(normalized.get("island_enabled"), False)
        for _island_flag in ("island_show_name", "island_show_status"):
            normalized[_island_flag] = _bool(normalized.get(_island_flag), True)
        # 消息显示默认关：历史消息默认不进岛卡片（老键 island_show_icon 已退役，读入即弃）。
        normalized["island_show_messages"] = _bool(
            normalized.get("island_show_messages"), False
        )
        mode = str(normalized.get("island_info_mode") or "time").strip().lower()
        normalized["island_info_mode"] = mode if mode in {"time", "custom"} else "time"
        normalized["island_custom_text"] = str(
            normalized.get("island_custom_text") or ""
        ).strip()[:80]
        style = str(normalized.get("island_style") or "dark").strip().lower()
        normalized["island_style"] = (
            style if style in {"dark", "light", "glass"} else "dark"
        )
        normalized["island_opacity"] = round(
            _number(normalized.get("island_opacity"), 1.0, 0.4, 1.0), 2
        )
        accent = str(normalized.get("island_accent") or "blue").strip().lower()
        normalized["island_accent"] = accent if accent in ISLAND_ACCENTS else "blue"
        action = str(normalized.get("island_click_action") or "expand").strip().lower()
        normalized["island_click_action"] = (
            action if action in {"expand", "toggle_pet"} else "expand"
        )
        normalized["island_metrics"] = _island_metrics(normalized.get("island_metrics"))
        normalized["click_show_balance"] = _bool(
            normalized.get("click_show_balance"), False
        )
        tier_mode = str(normalized.get("balance_tier_labels_mode") or "default").strip().lower()
        normalized["balance_tier_labels_mode"] = (
            tier_mode if tier_mode in BALANCE_TIER_MODES else "default"
        )
        for _label_key in ("balance_tier_label_peak", "balance_tier_label_idle"):
            normalized[_label_key] = str(normalized.get(_label_key) or "").strip()[
                :BALANCE_TIER_LABEL_MAX
            ]
        normalized["balance_tier_color_enabled"] = _bool(
            normalized.get("balance_tier_color_enabled"), True
        )
        normalized["island_edge_dock"] = _bool(
            normalized.get("island_edge_dock"), True
        )
        normalized["island_collision"] = _bool(
            normalized.get("island_collision"), True
        )
        dock_edge = str(normalized.get("island_dock_edge") or "none").strip().lower()
        normalized["island_dock_edge"] = (
            dock_edge if dock_edge in ISLAND_DOCK_EDGES else "none"
        )
        for _axis in ("island_x", "island_y"):
            coordinate = _optional_number(normalized.get(_axis))
            normalized[_axis] = (
                int(round(coordinate))
                if coordinate is not None and abs(coordinate) <= 100000
                else None
            )
        normalized["chat_always_on_top"] = _bool(
            normalized.get("chat_always_on_top"), False
        )
        normalized["chat_follow_pet"] = _bool(normalized.get("chat_follow_pet"), False)
        normalized["chat_background"] = str(
            normalized.get("chat_background") or ""
        ).strip()[:500]
        normalized["chat_accent"] = str(
            normalized.get("chat_accent") or ""
        ).strip()[:32]
        chat_font_size = int(
            round(_number(normalized.get("chat_font_size"), 0.0, 0.0, 24.0))
        )
        normalized["chat_font_size"] = (
            0 if chat_font_size <= 0 else max(10, chat_font_size)
        )
        normalized["chat_font_family"] = str(
            normalized.get("chat_font_family") or ""
        ).strip()[:64]
        normalized["menu_hidden"] = _text_list(normalized.get("menu_hidden"))
        normalized["menu_order"] = _text_list(normalized.get("menu_order"))
        layout = normalized.get("menu_layout")
        normalized["menu_layout"] = layout if isinstance(layout, dict) else None
        normalized["positions"] = _positions(normalized.get("positions"))
        normalized["last_settings_page"] = str(
            normalized.get("last_settings_page") or "general"
        )
        return normalized

    def get(self, key: str, default: Any = None) -> Any:
        text = str(key)
        if text == _EXTRA_FIELD:
            return dict(self.data.get(_EXTRA_FIELD) or {})
        if text in DEFAULTS:
            return self.data.get(text, default)
        extra = self.data.get(_EXTRA_FIELD) or {}
        if text in extra:
            return extra[text]
        return default

    def set(self, key: str, value: Any) -> None:
        text = str(key)
        if text == _EXTRA_FIELD:
            self.data[_EXTRA_FIELD] = _sanitize_extra(value)
        elif text in DEFAULTS:
            self.data[text] = value
        else:
            merged = dict(self.data.get(_EXTRA_FIELD) or {})
            merged[text] = value
            self.data[_EXTRA_FIELD] = _sanitize_extra(merged)
        self.data = self._normalize(self.data)

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.data = self._normalize(dict(snapshot))

    # ------------------------------------------------------------ 角色别名
    def character_alias(self, character_id: str) -> str:
        """角色自定义显示名；未设置返回空串（调用方回落到目录显示名）。"""
        aliases = self.data.get("character_aliases")
        if not isinstance(aliases, dict):
            return ""
        return str(aliases.get(str(character_id or "").strip()) or "")

    def set_character_alias(self, character_id: str, name: str) -> None:
        """写角色别名（strip + 截断到 24 个字符）；空名 = 恢复默认（删掉该键）。

        只改内存，落盘由调用方决定（菜单命令立即 ``save()``，设置页统一等
        确认保存）。坏输入不抛异常：清洗后为空就当「恢复默认」。
        """
        character_id = str(character_id or "").strip()
        if not character_id:
            return
        raw = self.data.get("character_aliases")
        aliases = dict(raw) if isinstance(raw, dict) else {}
        cleaned = _alias_text(name if isinstance(name, str) else str(name or ""))
        if cleaned:
            aliases[character_id] = cleaned
        else:
            aliases.pop(character_id, None)
        self.set("character_aliases", aliases)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        handle, tmp_name = tempfile.mkstemp(
            prefix="config.", suffix=".tmp", dir=str(self.root)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data))


class ScopedConfig:
    """Route settings reads/writes to a global config or the selected pet.

    The settings UI historically received ``CoreHost`` directly, so pages call
    ``host.config``.  Keeping this small Config-shaped facade lets existing
    pages and feature settings gain correct per-pet behavior without giving
    them a second configuration API.
    """

    def __init__(self, global_config: Config, pet_config: Config | None = None):
        self.global_config = global_config
        self.pet_config = pet_config or global_config

    def _config(self, key: str) -> Config:
        # 全局键 + 未登记键（扩展偏好）都归全局：feature 运行时是**共享**的，
        # 一份 per-pet 的扩展偏好会让「设置页写了、运行时看不到」。
        if (
            self.pet_config is self.global_config
            or key in GLOBAL_CONFIG_KEYS
            or key not in DEFAULTS
        ):
            return self.global_config
        return self.pet_config

    @property
    def root(self) -> Path:
        return self.pet_config.root

    @property
    def path(self) -> Path:
        return self.pet_config.path

    @property
    def data(self) -> dict[str, Any]:
        merged = self.global_config.snapshot()
        if self.pet_config is not self.global_config:
            merged.update(self.pet_config.snapshot())
            for key in GLOBAL_CONFIG_KEYS:
                merged[key] = self.global_config.get(key)
            # 扩展偏好是全局的，子宠快照不许盖掉它。
            merged[_EXTRA_FIELD] = self.global_config.get(_EXTRA_FIELD)
        return merged

    def get(self, key: str, default: Any = None) -> Any:
        return self._config(str(key)).get(str(key), default)

    def set(self, key: str, value: Any) -> None:
        self._config(str(key)).set(str(key), value)

    def character_alias(self, character_id: str) -> str:
        return self._config("character_aliases").character_alias(character_id)

    def set_character_alias(self, character_id: str, name: str) -> None:
        self._config("character_aliases").set_character_alias(character_id, name)

    def snapshot(self) -> dict[str, Any]:
        return self.data

    def restore(self, snapshot: dict[str, Any]) -> None:
        if self.pet_config is self.global_config:
            self.global_config.restore(snapshot)
            return
        for key, value in snapshot.items():
            self._config(str(key)).set(str(key), value)

    def save(self) -> None:
        self.global_config.save()
        if self.pet_config is not self.global_config:
            self.pet_config.save()
