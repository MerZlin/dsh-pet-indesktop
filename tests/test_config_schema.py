# -*- coding: utf-8 -*-
"""Config 键白名单收口测试（批3 子项3）。

pet/config.py 里 __init__ 的默认值 dict（约 498-566 行）与 reload() 的白名单
元组（约 656-691 行）是两份独立维护的键列表。本测试把现状文档化并加护栏：

实测两集合**不一致**（现状文档化，不修产品代码）：
- 默认值 dict 共 80 键；reload 白名单共 75 键。
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
import json
import re

import pytest

from pet import config as config_mod
from pet.config import Config
from pet.island.segments import CPU, NETWORK

# reload() 白名单键集合现状快照（与 pet/config.py reload() 的
# "for key in (...)" 元组一致；任何增删必须同步更新本快照）。
RELOAD_WHITELIST_SNAPSHOT = frozenset(
    {
        "agent_cost_enabled",
        "allow_cross_screen",
        "anim_menu_stay_open",
        "animation_gap_seconds",
        "auto_hide_fullscreen",
        "autostart_wanted",
        "balance_refresh_minutes",
        "balance_tier_color_enabled",
        "balance_tier_label_idle",
        "balance_tier_label_peak",
        "balance_tier_labels_mode",
        "character",
        "character_aliases",
        "character_profiles",
        "chat_always_on_top",
        "chat_background",
        "chat_background_fill",
        "chat_background_opacity",
        "chat_bg_crops",
        "chat_follow_pet",
        "chat_memory_auto_extract",
        "chat_memory_extract_interval",
        "chat_memory_enabled",
        "chat_reply_voice_enabled",
        "chat_ui_style",
        "click_show_balance",
        "click_show_self_talk",
        "click_sound_enabled",
        "click_sound_pack",
        "click_sound_volume",
        "collision_enabled",
        "collision_friction",
        "collision_impulse_cap",
        "collision_mass_scale",
        "collision_restitution",
        "collision_sound_enabled",
        "collision_sound_volume",
        "context_menu_appearance",
        "context_menu_layout",
        "cursor_hidden_passthrough",
        "dialogue_last_scope",
        "dialogue_mode",
        "dialogue_phrases",
        "divination_enabled",
        "divination_hint",
        "divination_speak",
        "divination_state",
        "drag_physics",
        "drop_gravity",
        "dynamic_island",
        "edge_probe_enabled",
        "experimental_shared_decode",
        "facing",
        "feature_activation",
        "ffmpeg_recycle_minutes",
        "first_frame_cache_max_mb",
        "game_fps_process",
        "game_fps_target_mode",
        "golden_spin_direct",
        "golden_spin_on_click",
        # 全局快捷键（进程级；空串 = 不绑）
        "hotkey_open_chat",
        "hotkey_look_screen",
        "hotkey_screen_capture",
        "harness_autostart",
        "harness_launch_target",
        "idle_low_fps_enabled",
        "idle_low_fps_threshold",
        "island_metrics",
        "island_network_enabled",
        "lock_position",
        "media_prewarm",
        "mem_min_size_mb",
        "mem_skip_foreground",
        "menu_easter_egg",
        "modern_chat_background",
        "modern_chat_background_fill",
        "modern_chat_background_opacity",
        "modern_chat_card_opacity",
        "mouse_through",
        "music_browser_media_enabled",
        "foobar_lyrics_enabled",
        "foobar_title_format",
        "lxmusic_enabled",
        "music_lyric_cache_limit",
        "music_lyric_enabled",
        "music_lyric_lead_seconds",
        "music_player_paths",
        "music_sing_enabled",
        "music_sing_grace_seconds",
        "network_info_menu_enabled",
        "network_probe_interval_seconds",
        "no_move",
        "on_top",
        "pet_opacity",
        "playback_speed",
        "pnpm_bin",
        "predict_prewarm_lead_ms",
        "quick_launch_apps",
        "quiet_also_on_fullscreen",
        "quiet_minutes_default",
        "bubble_text_scale",
        # 2026-10-22 场景扮演 3 键（每宠键；默认关 / 场景与人物留空）
        "roleplay_cast",
        "roleplay_enabled",
        "roleplay_scene",
        "rx",
        "ry",
        "scale",
        "screen_name",
        "self_talk_bubble_style",
        "self_talk_duration_seconds",
        "self_talk_enabled",
        "self_talk_image_chance",
        "self_talk_image_dir",
        "self_talk_image_scale",
        "self_talk_max_interval",
        "self_talk_min_interval",
        "self_talk_speak_enabled",
        "self_talk_texts",
        "self_talk_voice_precache_enabled",
        "settings_process_isolation",
        "shift_drag",
        "show_dock_icon",
        "slingshot_enabled",
        "spawn_inherit_dynamic_island",
        "spawn_inherit_size",
        "spawn_scale",
        "stream_capture_mode",
        "system_notifications_enabled",
        "throw_strength",
        "todo_reminder_enabled",
        "todo_reminder_lead_minutes",
        "todo_reminder_speak",
        "user_customized",
        "voice_chime_custom_quotes_en",
        "voice_chime_custom_quotes_zh",
        "voice_chime_custom_times",
        "voice_chime_doubao_pitch",
        "voice_chime_doubao_speaker",
        "voice_chime_enabled",
        "voice_chime_mimo_model",
        "voice_chime_mimo_style",
        "voice_chime_mimo_voice",
        "voice_chime_pitch",
        "voice_chime_rate",
        "voice_chime_sapi_rate",
        "voice_chime_sapi_voice",
        "voice_chime_schedule",
        "voice_chime_show_bubble",
        "voice_chime_show_quote",
        "voice_chime_tts_backend",
        "voice_chime_tts_fallback",
        "festival_birthday",
        "festival_custom_quotes_cn",
        "festival_custom_quotes_west",
        "festival_reminder_animation",
        "festival_reminder_cn",
        "festival_reminder_count",
        "festival_reminder_enabled",
        "festival_reminder_mode",
        "festival_reminder_show_quote",
        "festival_reminder_solar_terms",
        "festival_reminder_speak",
        "festival_reminder_times",
        "festival_reminder_west",
        "voice_chime_custom_quotes_en",
        "voice_chime_custom_quotes_zh",
        "voice_chime_voice",
        "voice_chime_volume",
        "weather_city",
        "weather_city_codes",
        "weather_city_list",
        "weather_enabled",
    }
)

# 默认值 dict 里不走普通白名单、由 reload() 专门路径处理的键（现状文档化）。
# 2026-09-19 加入 file_interpret（拖文件解读，嵌套 dict 走 _merge_ 专门路径）。
SPECIAL_CASED_KEYS = frozenset({"version", "proactive_screen", "agent_link", "chat", "file_interpret"})

# 默认值 dict 键集合现状快照 = 白名单 ∪ 特例键。
# 2026-09-17 加入 music_player_paths（交付前审查 P1-3 登记）。
# 2026-09-22 加入点击台词朗读 / 台词本地语音预缓存 / 自言自语配图概率 3 键
# （self_talk_speak_enabled、self_talk_voice_precache_enabled、self_talk_image_chance）。
# 2026-10-07 加入音乐播放器来源 2 键（foobar_lyrics_enabled / foobar_title_format）。
# 2026-10-07 深夜批次再加：天气 3（weather_enabled/weather_city/weather_city_list）、
# 网络 3（island_network_enabled/network_probe_interval_seconds/network_info_menu_enabled）、
# 免打扰 2（quiet_minutes_default/quiet_also_on_fullscreen）、回收内存 2
# （mem_skip_foreground/mem_min_size_mb）、动画菜单 1（anim_menu_stay_open）。
# 后实测（2026-10-07 夜间整合后复测）：白名单字面量 139 + 特例 5 = 144。
# 2026-10-07 P1 Slice C 加灵动岛指标 1（island_metrics，列表序=优先级），白名单 140。
# 2026-10-11 加游戏帧率 2（game_fps_target_mode 枚举 / game_fps_process 纯 exe 名，
# 都是**全局**键：岛与采集链路读主配置），白名单 142。
# 2026-10-13 加语音报时合成后端 9 键（tts_backend / tts_fallback / mimo_model /
# mimo_voice / mimo_style / sapi_voice / sapi_rate / doubao_speaker / doubao_pitch，
# 随 provider 注册表一起进来），白名单 151。
# 2026-10-14 补登记节日提醒 2 键（festival_reminder_animation / festival_birthday）：
# 它们随「节日动画 + 我的生日」一起进来时**漏登在 config.py 的默认 dict 与白名单里**
# （设置页有行、config 没键），于是本轮把它们渲染出来后被
# `test_settings_default_marks` 的「冻结集」抓到——补齐后白名单 153。
# 2026-10-18 加待办语音播报 1 键（todo_reminder_speak，默认 False：复用语音报时的
# 音频通道，不新增语速/音色/音量键），白名单 154。
# 2026-10-20 加助手回复朗读 1 键（chat_reply_voice_enabled，默认 False：同样复用
# 语音报时的音频通道，只念对话不念歌词），白名单 155。
# 2026-10-21 加功能模块激活 1 键（feature_activation，默认 None=未判定，由
# pet/feature_activation 一次性迁移写回 {version, enabled}；NULL 接受键），白名单 156。
# 2026-10-22 加对话全局记忆 1 键（chat_memory_enabled，默认 True：档 A 手动钉记忆的
# 注入总开关；非 NULL 接受键，盘上显式 null 回退默认），白名单 157。
# 2026-10-22 加记忆自动提炼 1 键（chat_memory_auto_extract，默认 True：档 B 回复后
# 静默提炼，关 = 只保留手动钉；非 NULL 接受键），白名单 158。
# 2026-10-21 加提炼频率 1 键（chat_memory_extract_interval，默认 5=每 5 轮提炼
# 一次；共创者定稿的 token 成本闸），白名单 159。
# 2026-10-22 加场景扮演 3 键（roleplay_enabled / roleplay_scene / roleplay_cast；
# 每宠键，默认关/空），白名单 162。
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


# ---------------------------------------------------------------- #129 脏值归一化
# 音乐关联 / 消费统计这 5 个键此前只在默认值 dict 与 reload 白名单里登记，
# 没进 _normalize_pet_settings：数值键的脏值会让设置页构造直接抛
# ValueError（float('abc') 打死整个设置页），字符串布尔被 bool() 误开
# （bool('false') is True，歌词功能自己打开）。下面固定这两条修复。

def _dirty_music_cost_config(tmp_path):
    """把 5 个键写成脏值落盘，再走真实加载路径（reload + _normalize_pet_settings）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 4,
                "music_lyric_enabled": "false",
                "music_lyric_lead_seconds": "abc",
                "music_lyric_cache_limit": -5,
                "music_sing_grace_seconds": "oops",
                "agent_cost_enabled": "yes",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return Config(base=tmp_path)


def test_dirty_music_and_cost_keys_are_normalized(tmp_path):
    """脏 config.json 加载后：布尔判真、数值有类型且落在可用区间内。"""
    from pet.music.lyric_controller import LEAD_MAX_SECONDS, LEAD_MIN_SECONDS

    cfg = _dirty_music_cost_config(tmp_path)

    assert cfg.data["music_lyric_enabled"] is False, "'false' 不得被 bool() 误开"
    assert cfg.data["agent_cost_enabled"] is True
    assert isinstance(cfg.data["music_lyric_lead_seconds"], float)
    assert LEAD_MIN_SECONDS <= cfg.data["music_lyric_lead_seconds"] <= LEAD_MAX_SECONDS
    assert isinstance(cfg.data["music_sing_grace_seconds"], float)
    assert 1.0 <= cfg.data["music_sing_grace_seconds"] <= 3600.0
    assert isinstance(cfg.data["music_lyric_cache_limit"], int)
    assert cfg.data["music_lyric_cache_limit"] >= 1


def test_set_normalizes_music_and_cost_keys(tmp_path):
    """set() 的归一化名单同样要覆盖这 5 键（设置页写回的值不得绕过钳制）。"""
    cfg = Config(base=tmp_path)

    cfg.set("music_lyric_lead_seconds", "abc")
    assert isinstance(cfg.data["music_lyric_lead_seconds"], float)
    cfg.set("music_lyric_cache_limit", -5)
    assert cfg.data["music_lyric_cache_limit"] >= 1
    cfg.set("music_sing_grace_seconds", "oops")
    assert isinstance(cfg.data["music_sing_grace_seconds"], float)
    cfg.set("music_lyric_enabled", "false")
    assert cfg.data["music_lyric_enabled"] is False
    cfg.set("agent_cost_enabled", "yes")
    assert cfg.data["agent_cost_enabled"] is True


# ------------------------------------------------- 「回收内存」(#210) 两个键
def _memory_trim_config(tmp_path, raw: dict) -> Config:
    """把 mem_* 键写成脏值落盘，再走真实加载路径（reload + _normalize_pet_settings）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, **raw}, ensure_ascii=False), encoding="utf-8",
    )
    return Config(base=tmp_path)


# ------------------------------------------------- 动画菜单连播（#221）
def _anim_menu_config(tmp_path, raw) -> Config:
    """把 ``anim_menu_stay_open`` 写成脏值落盘，再走真实加载路径。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, "anim_menu_stay_open": raw}, ensure_ascii=False),
        encoding="utf-8",
    )
    return Config(base=tmp_path)


def test_anim_menu_stay_open_defaults_to_closing_after_action(tmp_path):
    """默认 False = 点一个动作就收起菜单（#221 的产品决策）。

    该键是**菜单行为类**偏好：不进设置页（与 mem_* 同口径），用户手改
    config.json 才切到连播。
    """
    assert Config(base=tmp_path).data["anim_menu_stay_open"] is False


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("no", False), ("off", False),
    ("true", True), ("yes", True), ("on", True),
    # 非字符串布尔不参与解析（同族布尔键同规）：回落默认 False
    (1, False), (0, False), (None, False), ("随便", False),
])
def test_dirty_anim_menu_stay_open_is_not_truthy_by_string(tmp_path, raw, expected):
    """bool("false") is True——字符串布尔必须按字面语义归一。"""
    cfg = _anim_menu_config(tmp_path / f"raw-{raw}", raw)
    assert cfg.data["anim_menu_stay_open"] is expected


def test_set_and_reload_keep_anim_menu_stay_open(tmp_path):
    """set() 路径归一，且落盘 → 重启加载不被默认值冲掉。"""
    cfg = Config(base=tmp_path)
    cfg.set("anim_menu_stay_open", "on")
    assert cfg.data["anim_menu_stay_open"] is True
    assert cfg.save() is True
    assert Config(base=tmp_path).data["anim_menu_stay_open"] is True


# ------------------------------------------------- 天气四键（issue #209）
def _dirty_weather_config(tmp_path):
    """把天气键写成脏值落盘，再走真实加载路径（reload + _normalize_pet_settings）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 4,
                "weather_enabled": "false",
                "weather_city": "  鹿城区  ",
                "weather_city_list": ["北京", "北京", " 鹿城区 ", "", 42, "上海"],
                "weather_city_codes": {"鹿城区": " 101210710 ", "北京": "", "": "x"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return Config(base=tmp_path)


def test_memory_trim_keys_have_documented_defaults(tmp_path):
    """默认值：跳过前台（保护用户正在用的窗口）、阈值 50MB。

    两个键都是"菜单动作类功能"的配置面，**不进设置页**——只能手改 config.json
    或由菜单路径读取，默认值即推荐值。
    """
    cfg = Config(base=tmp_path)

    assert cfg.data["mem_skip_foreground"] is True
    assert cfg.data["mem_min_size_mb"] == 50.0


def test_dirty_memory_trim_keys_are_normalized(tmp_path):
    """脏值不得打死 worker 线程：字符串布尔不许被 bool() 误开，阈值必须落区间。

    阈值下限 0 有语义（0 = 连 0 字节进程都纳入），上限与
    ``pet/memory_trim.py::MIN_SIZE_MB_MAX`` 同值（漂移由 test_memory_trim 钉住）。
    """
    from pet import memory_trim

    assert _memory_trim_config(
        tmp_path / "str-bool", {"mem_skip_foreground": "false"},
    ).data["mem_skip_foreground"] is False
    assert _memory_trim_config(
        tmp_path / "bad-num", {"mem_min_size_mb": "abc"},
    ).data["mem_min_size_mb"] == memory_trim.DEFAULT_MIN_SIZE_MB
    assert _memory_trim_config(
        tmp_path / "negative", {"mem_min_size_mb": -5},
    ).data["mem_min_size_mb"] == 0.0
    assert _memory_trim_config(
        tmp_path / "huge", {"mem_min_size_mb": 10 ** 9},
    ).data["mem_min_size_mb"] == memory_trim.MIN_SIZE_MB_MAX


def test_set_normalizes_memory_trim_keys(tmp_path):
    cfg = Config(base=tmp_path)

    cfg.set("mem_min_size_mb", "abc")
    assert cfg.data["mem_min_size_mb"] == 50.0
    cfg.set("mem_skip_foreground", "false")
    assert cfg.data["mem_skip_foreground"] is False


def test_dirty_weather_keys_are_normalized(tmp_path):
    """脏 config.json 加载后：布尔判真、城市名归一化（**不剥后缀**）、列表去空去重。"""
    cfg = _dirty_weather_config(tmp_path)

    assert cfg.data["weather_enabled"] is False, "'false' 不得被 bool() 误开"
    assert cfg.data["weather_city"] == "鹿城区"
    assert cfg.data["weather_city_list"] == ["北京", "鹿城区", "上海"]
    assert cfg.data["weather_city_codes"] == {"鹿城区": "101210710"}


def test_set_normalizes_weather_keys(tmp_path):
    """set() 的归一化名单同样覆盖天气键（设置页/菜单写回不得绕过钳制）。"""
    from pet.weather import MAX_CITIES, normalize_city_list

    cfg = Config(base=tmp_path)
    cfg.set("weather_enabled", "yes")
    assert cfg.data["weather_enabled"] is True
    cfg.set("weather_city", "乌鲁木齐县")
    assert cfg.data["weather_city"] == "乌鲁木齐县"
    cfg.set("weather_city_list", [f"城市{i}" for i in range(MAX_CITIES + 5)])
    assert cfg.data["weather_city_list"] == normalize_city_list(
        [f"城市{i}" for i in range(MAX_CITIES + 5)]
    )
    cfg.set("weather_city_list", "北京")
    assert cfg.data["weather_city_list"] == []
# ------------------------------------------- 网络信息 / 灵动岛指标（#172 + Slice C）
def _dirty_network_config(tmp_path, raw: dict) -> Config:
    """把网络信息诸键写成脏值落盘，再走真实加载路径（reload + 归一化）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": 4}
    payload.update(raw)
    (cfg_dir / "config.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return Config(base=tmp_path)


def test_network_keys_defaults_are_off_probe_and_on_menu(tmp_path):
    """默认值即产品契约：延迟探测默认关（要发包）、菜单入口默认开。

    指标列表（``island_metrics``，P1 Slice C）默认空：附加指标一个都不加。
    """
    cfg = Config(base=tmp_path)
    assert cfg.data["island_metrics"] == []
    assert cfg.data["island_network_enabled"] is False
    assert cfg.data["network_probe_interval_seconds"] == 1.0
    assert cfg.data["network_info_menu_enabled"] is True


@pytest.mark.parametrize("raw,expected_network,expected_menu", [
    ("false", False, False),
    ("no", False, False),
    ("true", True, True),
    ("on", True, True),
    # 整数不参与布尔解析（同族布尔键同规）：回落到各自默认值
    (0, False, True),
    (1, False, True),
])
def test_dirty_network_boolean_keys_are_not_truthy_by_string(
        tmp_path, raw, expected_network, expected_menu):
    """bool("false") is True —— 字符串布尔必须按字面语义归一（同族键同规）。

    旧键 ``island_network_enabled`` 自 P1 Slice C 起只做**单向迁移**，所以这里
    断言迁移后的真值落点：判真 → ``island_metrics`` 含 network；判假 → 列表不动；
    无论哪种，旧键归一化后恒为 False（不再是第二写入来源）。
    """
    cfg = _dirty_network_config(tmp_path / f"island-{raw}", {"island_network_enabled": raw})
    assert cfg.data["island_network_enabled"] is False
    assert (NETWORK in cfg.data["island_metrics"]) is expected_network
    cfg2 = _dirty_network_config(tmp_path / f"menu-{raw}", {"network_info_menu_enabled": raw})
    assert cfg2.data["network_info_menu_enabled"] is expected_menu


@pytest.mark.parametrize("raw,expected", [
    ("abc", 1.0),
    (None, 1.0),
    (0.01, 0.5),
    (0.5, 0.5),
    (2.5, 2.5),
    (9999, 60.0),
])
def test_dirty_probe_interval_is_clamped(tmp_path, raw, expected):
    """探测间隔夹到 [0.5, 60]：脏值不得让采样线程以 0.01 秒的节奏发包。"""
    cfg = _dirty_network_config(tmp_path / str(raw), {"network_probe_interval_seconds": raw})
    assert cfg.data["network_probe_interval_seconds"] == pytest.approx(expected)


def test_set_normalizes_network_keys(tmp_path):
    """set() 路径同样归一（设置页写回的值不得绕过钳制）。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_network_enabled", "false")
    assert cfg.data["island_network_enabled"] is False
    assert cfg.data["island_metrics"] == []
    cfg.set("network_probe_interval_seconds", 0.01)
    assert cfg.data["network_probe_interval_seconds"] == pytest.approx(0.5)
    cfg.set("network_info_menu_enabled", "off")
    assert cfg.data["network_info_menu_enabled"] is False


def test_network_keys_survive_reload_round_trip(tmp_path):
    """落盘 → 重启加载：指标列表与另两键都是普通白名单键，不得被默认值冲掉。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [NETWORK, CPU])
    cfg.set("network_probe_interval_seconds", 3.0)
    cfg.set("network_info_menu_enabled", False)
    assert cfg.save() is True
    restored = Config(base=tmp_path)
    assert restored.data["island_metrics"] == [NETWORK, CPU]
    assert restored.data["network_probe_interval_seconds"] == pytest.approx(3.0)
    assert restored.data["network_info_menu_enabled"] is False
    cfg.set("weather_city_codes", {"景德镇市": "101240801", "坏": 5})
    assert cfg.data["weather_city_codes"] == {"景德镇市": "101240801"}


# --------------------------------------- M1：城市名归一化幂等、不被别的设置改坏
def test_repeated_normalization_never_erodes_the_city_name(tmp_path):
    """保存任何设置都会跑整表归一化：城市名必须一次定形，之后一直不变。

    回归的正是终审 M1 的实测：``景德镇市`` → ``景德镇`` → ``景德``（每次保存
    别的设置都继续剥一层），以及「新乡」「景德镇」被当后缀裁掉。
    """
    cfg = Config(base=tmp_path)
    cfg.set("weather_city", "景德镇市")
    cfg.set("weather_city_list", ["新乡", "景德镇", "鹿城区"])
    assert cfg.data["weather_city"] == "景德镇市"
    assert cfg.data["weather_city_list"] == ["新乡", "景德镇", "鹿城区"]

    for _ in range(5):
        cfg._normalize_pet_settings()
        assert cfg.data["weather_city"] == "景德镇市"
        assert cfg.data["weather_city_list"] == ["新乡", "景德镇", "鹿城区"]


def test_setting_an_unrelated_key_leaves_the_weather_city_alone(tmp_path):
    """改一个跟天气无关的设置（会触发整表归一化）不得动城市名。"""
    cfg = Config(base=tmp_path)
    cfg.set("weather_city", "景德镇市")
    cfg.set("weather_city_codes", {"景德镇市": "101240801"})

    cfg.set("pet_opacity", 90)
    cfg.set("bubble_text_scale", 120)

    assert cfg.data["weather_city"] == "景德镇市"
    assert cfg.data["weather_city_codes"] == {"景德镇市": "101240801"}


def test_city_name_normalization_is_a_fixed_point_on_load_and_reload(tmp_path):
    """落盘 → 重载 → 再落盘 不得改变城市名（跨重启也不侵蚀）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {"version": 4, "weather_city": "景德镇市", "weather_city_list": ["新乡"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    first = Config(base=tmp_path)
    first.save()
    second = Config(base=tmp_path)
    assert second.data["weather_city"] == "景德镇市"
    assert second.data["weather_city_list"] == ["新乡"]

# ------------------------------------------------- 自启目标（harness_launch_target）
def _launch_target_config(tmp_path, raw: str) -> Config:
    """把 ``harness_launch_target`` 写成指定原始值落盘，再走真实加载路径。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, "harness_launch_target": raw}, ensure_ascii=False),
        encoding="utf-8",
    )
    return Config(base=tmp_path)


def test_harness_launch_target_accepts_service_and_falls_back_to_auto(tmp_path):
    """归一化认 ``service``（新增的自启目标），非法值仍回落 ``auto``。

    语义收紧（2026-10）：auto 不再解析成 desktop，而是只拉起 dsh 服务；设置页
    新增「仅 dsh 服务」项后，该值必须能落盘、重载后不被归一化抹掉。
    """
    assert _launch_target_config(tmp_path / "svc", "service").get("harness_launch_target") == "service"
    assert _launch_target_config(tmp_path / "auto", "auto").get("harness_launch_target") == "auto"
    for i, bad in enumerate(("desktop-gui", "SERVICE2", "", "0")):
        cfg = _launch_target_config(tmp_path / f"bad{i}", bad)
        assert cfg.get("harness_launch_target") == "auto", f"非法值 {bad!r} 必须回退 auto"


def test_set_harness_launch_target_keeps_service(tmp_path):
    """set() 路径同样不吞 service（设置页保存走 set → save）。"""
    cfg = Config(base=tmp_path)
    cfg.set("harness_launch_target", "service")
    assert cfg.data["harness_launch_target"] == "service"


def test_dirty_music_and_cost_config_does_not_break_settings_page(tmp_path):
    """设置页（settings_pet_controls）面对脏配置必须能构造出来。"""
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.music.lyric_controller import LEAD_MAX_SECONDS, LEAD_MIN_SECONDS

    QApplication.instance() or QApplication([])
    cfg = _dirty_music_cost_config(tmp_path)

    dialog = ModernSettingsDialog(cfg, include_ai=False)

    lead = float(dialog.music_lyric_lead_spin.value())
    assert LEAD_MIN_SECONDS <= lead <= LEAD_MAX_SECONDS
    assert dialog.music_lyric_check.isChecked() is False


# ---------------------------------------------------------------- 交付前审查 P1-3 / P2-a / P2-b


def _write_config(tmp_path, payload: dict) -> None:
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_music_player_paths_is_cleaned_on_load(tmp_path):
    """手改 config.json 的 music_player_paths 要活下来，并被清洗成干净形态（P1-3）。"""
    _write_config(
        tmp_path,
        {
            "version": 4,
            "music_player_paths": {
                "netease": "  D:/Custom/cloudmusic.exe  ",  # 两端空白要 strip
                "qqmusic": "D:/QQMusic/QQMusic.exe",
                "unknown": "D:/evil.exe",                    # 未知播放器键丢弃
                "netease_backup": 42,                        # 非字符串值丢弃
            },
        },
    )
    cfg = Config(base=tmp_path)

    assert cfg.data["music_player_paths"] == {
        "netease": "D:/Custom/cloudmusic.exe",
        "qqmusic": "D:/QQMusic/QQMusic.exe",
    }


def test_music_player_paths_survives_reload(tmp_path):
    """reload() 的白名单覆盖路径必须带上该键（P1-3）。

    此前键不在白名单：reload() 只覆盖白名单键，手填路径被静默丢弃，
    ``cfg.get("music_player_paths", {})`` 永远返回 {}。
    """
    _write_config(
        tmp_path,
        {"version": 4, "music_player_paths": {"netease": "D:/Custom/cloudmusic.exe"}},
    )
    cfg = Config(base=tmp_path)
    cfg.reload()
    assert cfg.data["music_player_paths"] == {"netease": "D:/Custom/cloudmusic.exe"}


def test_music_player_paths_dirty_values_are_normalized(tmp_path):
    """脏值（非 dict / 非字符串 / 空串 / 超长）不得让 Config() 抛，且不落非法值。"""
    for dirty in ("not-a-dict", ["netease"], 42, None):
        _write_config(tmp_path, {"version": 4, "music_player_paths": dirty})
        cfg = Config(base=tmp_path)
        assert cfg.data["music_player_paths"] == {}, dirty

    _write_config(
        tmp_path,
        {"version": 4, "music_player_paths": {"netease": "", "qqmusic": "   ", "x": []}},
    )
    cfg = Config(base=tmp_path)
    assert cfg.data["music_player_paths"] == {}

    _write_config(tmp_path, {"version": 4, "music_player_paths": {"netease": "x" * 900}})
    cfg = Config(base=tmp_path)
    assert len(cfg.data["music_player_paths"]["netease"]) == 500


def test_set_cleans_music_player_paths(tmp_path):
    """set() 路径同样走清洗（名单覆盖该键）。"""
    cfg = Config(base=tmp_path)
    cfg.set("music_player_paths", {"netease": "D:/a.exe", "bogus": "D:/b.exe"})
    assert cfg.data["music_player_paths"] == {"netease": "D:/a.exe"}
    cfg.set("music_player_paths", "not-a-dict")
    assert cfg.data["music_player_paths"] == {}


def test_music_player_paths_accepts_all_registered_players(tmp_path):
    """四播放器的路径都要能存下来（清洗键集与 music_players.PLAYERS 同源）。

    回归面：清洗名单只认 netease/qqmusic 时，酷狗/汽水手填的路径在 Config()
    构造与 reload() 里被静默丢掉，用户填了等于没填。
    """
    payload = {
        "netease": "D:/CloudMusic/cloudmusic.exe",
        "qqmusic": "D:/QQMusic/QQMusic.exe",
        "kugou": "D:/KuGou/KGMusic/KuGou.exe",
        "soda": "C:/Users/me/AppData/Local/SodaMusic/SodaMusic.exe",
    }
    _write_config(tmp_path, {"version": 4, "music_player_paths": payload})
    cfg = Config(base=tmp_path)
    assert cfg.data["music_player_paths"] == payload

    cfg.reload()
    assert cfg.data["music_player_paths"] == payload, "reload() 把新播放器的路径丢了"


def test_music_player_paths_keys_track_registry(tmp_path):
    """清洗键集 = 注册表键集（不许在 config 里另抄一份固定键表）。"""
    from pet.music import players as music_players

    assert set(music_players.player_keys()) == {"netease", "qqmusic", "kugou", "soda"}
    payload = {key: f"D:/{key}/player.exe" for key in music_players.player_keys()}
    _write_config(tmp_path, {"version": 4, "music_player_paths": payload})
    cfg = Config(base=tmp_path)
    assert cfg.data["music_player_paths"] == payload


def test_music_player_paths_drops_unknown_keys_but_keeps_old_two(tmp_path):
    """旧两键升级后不丢；表外键（含键名大小写不符、拼错的播放器键）照旧丢弃。"""
    _write_config(
        tmp_path,
        {
            "version": 4,
            "music_player_paths": {
                "netease": "D:/CloudMusic/cloudmusic.exe",
                "qqmusic": "D:/QQMusic/QQMusic.exe",
                "kugou": "D:/KuGou/KGMusic/KuGou.exe",
                "kuGou": "D:/KuGou/KuGou.exe",       # 键名大小写不符 = 表外键
                "soda_music": "D:/SodaMusic.exe",
                "unknown": "D:/evil.exe",
            },
        },
    )
    cfg = Config(base=tmp_path)
    assert cfg.data["music_player_paths"] == {
        "netease": "D:/CloudMusic/cloudmusic.exe",
        "qqmusic": "D:/QQMusic/QQMusic.exe",
        "kugou": "D:/KuGou/KGMusic/KuGou.exe",
    }


def test_four_player_paths_persist_across_restart(tmp_path):
    """四键写盘 → 重启（新建 Config）后仍是四键，且空串/未知键不落盘。"""
    cfg = Config(base=tmp_path)
    cfg.set("music_player_paths", {
        "kugou": "D:/KuGou/KGMusic/KuGou.exe",
        "soda": "D:/SodaMusic/SodaMusic.exe",
        "netease": "D:/CloudMusic/cloudmusic.exe",
        "qqmusic": "",
        "bogus": "D:/bogus.exe",
    })
    cfg.save()

    fresh = Config(base=tmp_path)
    assert fresh.data["music_player_paths"] == {
        "kugou": "D:/KuGou/KGMusic/KuGou.exe",
        "soda": "D:/SodaMusic/SodaMusic.exe",
        "netease": "D:/CloudMusic/cloudmusic.exe",
    }
    fresh.reload()
    assert fresh.data["music_player_paths"] == {
        "kugou": "D:/KuGou/KGMusic/KuGou.exe",
        "soda": "D:/SodaMusic/SodaMusic.exe",
        "netease": "D:/CloudMusic/cloudmusic.exe",
    }


def test_huge_integer_literal_falls_back_to_default(tmp_path):
    """超长整数字面量（json 产出 Python int）不得让 Config() 抛 OverflowError。

    回归：``_float_or_default`` 只捕 TypeError/ValueError，``float(10**400)``
    抛 OverflowError → Config.__init__ 失败 → pet/app.py 的 Config() 不在 try
    里 → 启动直接崩（无窗口）。
    """
    huge = int("9" * 400)
    _write_config(
        tmp_path,
        {
            "version": 4,
            "music_sing_grace_seconds": huge,
            "music_lyric_lead_seconds": huge,
            "music_lyric_cache_limit": huge,
            "click_sound_volume": huge,
        },
    )
    cfg = Config(base=tmp_path)

    assert cfg.data["music_sing_grace_seconds"] == 6.0
    assert cfg.data["click_sound_volume"] == 0.70
    assert cfg.data["music_lyric_cache_limit"] == 2000


def test_set_with_huge_integer_does_not_raise(tmp_path):
    """set() 路径同样不能因 OverflowError 抛（设置页写回同一助手）。"""
    cfg = Config(base=tmp_path)
    cfg.set("music_sing_grace_seconds", int("9" * 400))
    assert cfg.data["music_sing_grace_seconds"] == 6.0


def test_music_sing_enabled_string_false_is_not_truthy(tmp_path):
    """字符串 "false" 不得被 bool() 误开（P2-b：同族漏网的第六个键）。"""
    _write_config(tmp_path, {"version": 4, "music_sing_enabled": "false"})
    cfg = Config(base=tmp_path)
    assert cfg.data["music_sing_enabled"] is False

    cfg = Config(base=tmp_path)
    cfg.set("music_sing_enabled", "false")
    assert cfg.data["music_sing_enabled"] is False

    _write_config(tmp_path, {"version": 4, "music_sing_enabled": "yes"})
    assert Config(base=tmp_path).data["music_sing_enabled"] is True


# ------------------------------------------- 退役键的静默迁移（2026-10-08）
def test_retired_menu_template_key_is_discarded_silently(tmp_path):
    """旧版菜单退役后，配置里的菜单模板键必须静默丢弃：读入即弃 + 不再写回。

    旧存档（含用户手改的 `"legacy"`）只能表现为"什么都没发生"：不抛异常、
    不回退旧布局、不影响启动；下一次落盘时键从文件里消失。
    """
    cfg = _load_with(tmp_path, context_menu_template="legacy")
    assert "context_menu_template" not in cfg.data
    assert cfg.get("context_menu_template") is None
    assert cfg.get("context_menu_template", "modern") == "modern"


def test_retired_menu_template_key_is_not_written_back(tmp_path):
    """丢弃必须落到盘上：save() 之后 config.json 里不再有这个键。"""
    import json

    cfg = _load_with(tmp_path, context_menu_template="modern")
    assert cfg.save() is True
    payload = json.loads((cfg.dir / "config.json").read_text(encoding="utf-8"))
    assert "context_menu_template" not in payload


def test_retired_menu_template_key_is_dropped_in_slot_config(tmp_path):
    """每个 slot 配置同样丢弃（子肥鱼各有自己那份 config-slot-N.json）。"""
    import json

    slot_dir = tmp_path / "slot"
    (slot_dir / config_mod.APP_DIR_NAME).mkdir(parents=True, exist_ok=True)
    _write_config(slot_dir, {"version": 4, "context_menu_template": "legacy"})
    slot_path = slot_dir / config_mod.APP_DIR_NAME / "config.json"
    slot_path.rename(slot_path.with_name("config-slot-3.json"))
    cfg = Config(base=slot_dir, instance_id="slot-3")
    assert "context_menu_template" not in cfg.data
    assert cfg.get("context_menu_template") is None


# ------------------------------------------------- 脏值家族：合法 JSON 不得阻断启动
#
# 全产品审查 C3（2026-10-08）：三族脏值都能让 ``Config()`` 构造直接抛异常，
# 而启动路径（``pet/app.py`` 的 Config()）与自救入口（``pet/__main__.py`` 的
# 独立设置页同样先构造 Config()）都没有 try —— 一个手改出来的 config.json
# 就能让程序连设置恢复都进不去：
#
# - list / dict 撞 ``x not in {"..."}`` 成员判断 → TypeError: unhashable；
# - ``10**400``（json 合法数字字面量 → Python 大整数）撞 ``float()`` /
#   ``math.isfinite()`` → OverflowError；
# - ``Infinity`` / ``NaN``（Python json.loads 默认接受）撞 ``int()`` → OverflowError。
#
# 下面把这一族**逐键**钉住：构造成功 + 回默认值。探针口径见本文件末尾的
# ``test_dirty_value_scan_covers_every_config_key``（穷举全键 × 全脏值）。

_HUGE = int("9" * 400)


def _load_with(tmp_path, **payload):
    """按真实加载路径读一份脏配置（reload + _normalize_pet_settings）。"""
    payload.setdefault("version", 4)
    _write_config(tmp_path, payload)
    return Config(base=tmp_path)


def _island_coordinate_config(tmp_path, axis: str, value):
    return _load_with(tmp_path, dynamic_island={"enabled": True, axis: value})


def test_unhashable_enum_keys_fall_back_to_default(tmp_path):
    """枚举键撞 list/dict：成员判断不得抛 TypeError: unhashable type。"""
    for i, (key, expected) in enumerate((("chat_ui_style", "modern"),)):
        for j, dirty in enumerate(([1, 2], {"a": 1})):
            cfg = _load_with(tmp_path / f"{i}-{j}", **{key: dirty})
            assert cfg.data[key] == expected, f"{key}={dirty!r} 必须回落 {expected}"


def test_unhashable_enum_keys_keep_legal_values(tmp_path):
    """非回归：合法枚举值原样保留（保护不能顺手吃掉合法值）。"""
    assert _load_with(
        tmp_path / "classic", chat_ui_style="classic").get("chat_ui_style") == "classic"


def test_huge_island_coordinates_fall_back_to_auto_position(tmp_path):
    """灵动岛坐标超大整数：回 None（自动定位），不得 OverflowError。"""
    for axis in ("x", "y"):
        for i, dirty in enumerate((_HUGE, -_HUGE)):
            cfg = _island_coordinate_config(tmp_path / f"{axis}{i}", axis, dirty)
            assert cfg.data["dynamic_island"][axis] is None


def test_nonfinite_and_huge_island_opacity_fall_back(tmp_path):
    """灵动岛不透明度：超大整数/NaN/Infinity 落在 [0.4, 1.0]，不得抛异常。

    超范围数值按区间夹取（与全文件数值键同规），非数值/溢出/NaN 回默认 1.0。
    """
    cases = (
        (_HUGE, 1.0),            # float() 溢出 → 默认
        (float("nan"), 1.0),     # 不可比较 → 默认
        (float("inf"), 1.0),     # 超上限 → 夹到 1.0
        (float("-inf"), 0.4),    # 超下限 → 夹到 0.4
    )
    for i, (dirty, expected) in enumerate(cases):
        cfg = _load_with(
            tmp_path / f"opacity{i}", dynamic_island={"enabled": True, "opacity": dirty}
        )
        assert cfg.data["dynamic_island"]["opacity"] == expected, f"opacity={dirty!r}"


def test_huge_quiet_minutes_falls_back_to_default(tmp_path):
    """免打扰默认时长：超大整数回默认档（float() 溢出不得冒泡）。"""
    cfg = _load_with(tmp_path, quiet_minutes_default=_HUGE)
    assert cfg.data["quiet_minutes_default"] == config_mod.DEFAULT_QUIET_MINUTES


def test_nonfinite_int_keys_fall_back_to_default(tmp_path):
    """int() 型数值键遇 Infinity/NaN：不得 OverflowError，落在可用区间内。"""
    cases = ((float("inf"), 100, 100), (float("-inf"), 10, 10), (float("nan"), 100, 84))
    for i, (dirty, background, card) in enumerate(cases):
        cfg = _load_with(
            tmp_path / f"opacity{i}",
            chat_background_opacity=dirty,
            modern_chat_background_opacity=dirty,
            modern_chat_card_opacity=dirty,
        )
        assert cfg.data["chat_background_opacity"] == background, f"{dirty!r}"
        assert cfg.data["modern_chat_background_opacity"] == background, f"{dirty!r}"
        assert cfg.data["modern_chat_card_opacity"] == card, f"{dirty!r}"


def test_nonfinite_menu_appearance_int_keys_fall_back(tmp_path):
    """菜单外观的圆角/字号：Infinity/NaN 回可用值（不抛），合法值照常夹取。"""
    cases = (
        (float("inf"), 18, 18),
        (float("-inf"), 6, 10),
        (float("nan"), 12, 13),      # NaN → 默认
        (999, 18, 18),               # 越界数值 → 夹取
    )
    for i, (dirty, radius, font_size) in enumerate(cases):
        cfg = _load_with(
            tmp_path / f"appearance{i}",
            context_menu_appearance={"corner_radius": dirty, "ui_font_size": dirty},
        )
        assert cfg.data["context_menu_appearance"]["corner_radius"] == radius, f"{dirty!r}"
        assert cfg.data["context_menu_appearance"]["ui_font_size"] == font_size, f"{dirty!r}"


def test_nonfinite_version_does_not_break_reload(tmp_path):
    """顶层 version 非有限：reload 的迁移判定不得抛（回 1 = 老配置路径）。"""
    for i, dirty in enumerate((float("inf"), float("-inf"), float("nan"))):
        cfg = _load_with(tmp_path / f"version{i}", version=dirty)
        assert cfg.data["version"] == 4, "归一化后 version 仍必须回写当前版本"


def test_dirty_value_scan_covers_every_config_key(tmp_path):
    """穷举探针：每个配置键 × 每种脏值都必须能构造出 Config（家族护栏）。

    这是"同族残留"的机器判据——新增键只要又走裸 ``x in {...}`` / ``int()`` /
    ``float()`` / ``math.isfinite()``，这里立刻红，并指出是哪个键哪种脏值。
    """
    dirty_values = {
        "list": [1, 2],
        "dict": {"a": 1},
        "huge": _HUGE,
        "nan": float("nan"),
        "inf": float("inf"),
        "neginf": float("-inf"),
        "str": "abc",
        "none": None,
    }
    keys = sorted(Config(base=tmp_path / "seed").data)
    failures: list[str] = []
    for key in keys:
        for label, dirty in dirty_values.items():
            case = tmp_path / f"{key[:40]}-{label}"
            try:
                _load_with(case, **{key: dirty})
            except Exception as exc:  # noqa: BLE001 - 探针要把任何异常都记下来
                failures.append(f"{key}={label}: {type(exc).__name__}: {exc}")
    assert failures == [], "脏值不得阻断 Config() 构造：\n" + "\n".join(failures)
