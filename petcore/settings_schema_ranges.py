# 由 scripts/gen_settings_meta.py 生成；只收录数值范围/类型/枚举，不收录默认值。
RANGES = {
  "volume": {
    "min": 0.0,
    "max": 1.0
  },
  "click_squash_ms": {
    "min": 80.0,
    "max": 1000.0
  },
  "playback_speed": {
    "min": 0.5,
    "max": 2.0
  },
  "animation_gap_seconds": {
    "min": 0.0,
    "max": 3600.0
  },
  "self_talk_min": {
    "min": 1.0,
    "max": 3600.0
  },
  "self_talk_max": {
    "min": 1.0,
    "max": 3600.0
  },
  "self_talk_duration": {
    "min": 1.0,
    "max": 120.0
  },
  "quiet_minutes_default": {
    "min": 1.0,
    "max": 1440.0
  },
  "voice_chime_rate": {
    "min": 0.5,
    "max": 2.0
  },
  "voice_chime_volume": {
    "min": 0.0,
    "max": 1.0
  },
  "music_sing_grace_seconds": {
    "min": 0.0,
    "max": 120.0
  },
  "music_lyric_lead_seconds": {
    "max": 3.0
  },
  "music_lyric_offset_seconds": {
    "max": 3.0
  },
  "speech_rate": {
    "min": 0.5,
    "max": 2.0
  },
  "speech_volume": {
    "min": 0.0,
    "max": 1.0
  },
  "speech_local_timeout": {
    "min": 5.0,
    "max": 120.0
  },
  "asr_timeout": {
    "min": 5.0,
    "max": 120.0
  },
  "ai_temperature": {
    "min": 0.0,
    "max": 2.0
  },
  "ai_animation_min_seconds": {
    "min": 0.5,
    "max": 30.0
  }
}

KINDS = {
  "always_on_top": "bool",
  "click_through": "bool",
  "multi_screen": "bool",
  "screen_name": "str",
  "click_sound": "bool",
  "menu_animation_icons": "bool",
  "stream_capture_mode": "bool",
  "stream_capture_keycolor": "bool",
  "auto_hide_fullscreen": "bool",
  "easter_egg_enabled": "bool",
  "click_squash_enabled": "bool",
  "drag_physics_enabled": "bool",
  "drag_lean_enabled": "bool",
  "drop_gravity_enabled": "bool",
  "slingshot_enabled": "bool",
  "collision_enabled": "bool",
  "collision_sound": "bool",
  "golden_spin_on_click": "bool",
  "golden_spin_direct": "bool",
  "throw_egg_enabled": "bool",
  "file_feeding_enabled": "bool",
  "no_move": "bool",
  "lock_position": "bool",
  "shift_drag": "bool",
  "idle_low_fps_enabled": "bool",
  "work_animations_only": "bool",
  "head_pat_enabled": "bool",
  "anim_random_auto": "bool",
  "spawn_inherit_size": "bool",
  "self_talk_enabled": "bool",
  "self_talk_daily_enabled": "bool",
  "self_talk_daily_sequence": "bool",
  "self_talk_voice_enabled": "bool",
  "self_talk_champion_enabled": "bool",
  "click_talk_enabled": "bool",
  "click_talk_texts": "list",
  "click_talk_voice_enabled": "bool",
  "network_info_enabled": "bool",
  "memory_trim_enabled": "bool",
  "divination_speak": "bool",
  "divination_hint": "bool",
  "divination_enabled": "bool",
  "festival_enabled": "bool",
  "weather_enabled": "bool",
  "file_reader_enabled": "bool",
  "file_reader_record": "bool",
  "todo_items": "list",
  "todo_reminder_enabled": "bool",
  "todo_reminder_speak": "bool",
  "voice_chime_enabled": "bool",
  "music_enabled": "bool",
  "voice_chime_show_bubble": "bool",
  "voice_chime_quotes": "list",
  "music_sing_enabled": "bool",
  "music_buddy_wine_mode": "bool",
  "music_lyric_enabled": "bool",
  "music_browser_media_enabled": "bool",
  "music_filter_enabled": "bool",
  "asr_enabled": "bool",
  "asr_stream_enabled": "bool",
  "asr_auto_send": "bool",
  "ai_voice_reply": "bool",
  "ai_meme_reply_enabled": "bool",
  "allow_feature_mods": "bool",
  "edge_probe_enabled": "bool",
  "ai_vision_enabled": "bool",
  "ai_look_screen_auto_open": "bool",
  "ai_animation_enabled": "bool",
  "ai_animation_thinking": "bool",
  "ai_memory_enabled": "bool",
  "ai_memory_share_channels": "bool",
  "ai_memory_import_dsh": "bool",
  "ai_web_search": "bool",
  "ai_web_fetch": "bool",
  "ai_proactive_enabled": "bool",
  "ai_proactive_require_idle": "bool",
  "ai_proactive_dry_run": "bool",
  "ai_proactive_pre_cue": "bool",
  "ai_proactive_sync_chat": "bool",
  "ai_agent_enabled": "bool",
  "ai_agent_dsh_enabled": "bool",
  "ai_agent_events_enabled": "bool",
  "ai_agent_animation_enabled": "bool",
  "ai_agent_command_enabled": "bool",
  "ai_agent_activity_bubbles": "bool",
  "ai_agent_done_notice": "bool",
  "ai_agent_sound_enabled": "bool",
  "dsh_memory_inject": "bool",
  "dsh_persona_inject": "bool",
  "island_enabled": "bool",
  "island_show_messages": "bool",
  "island_show_toggle_button": "bool",
  "island_hide_fullscreen": "bool",
  "click_show_balance": "bool",
  "balance_tier_color_enabled": "bool",
  "island_edge_dock": "bool",
  "island_collision": "bool",
  "chat_always_on_top": "bool",
  "chat_follow_pet": "bool",
  "chat_dsh_mode": "bool",
  "chat_dark_mode": "bool",
  "menu_hidden": "list",
  "menu_order": "list"
}

CHOICES = {}
