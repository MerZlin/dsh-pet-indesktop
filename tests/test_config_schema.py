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

from pet import config as config_mod
from pet.config import Config

# reload() 白名单键集合现状快照（与 pet/config.py reload() 的
# "for key in (...)" 元组一致；任何增删必须同步更新本快照）。
RELOAD_WHITELIST_SNAPSHOT = frozenset(
    {
        "agent_cost_enabled",
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
        "context_menu_template",
        "cursor_hidden_passthrough",
        "dialogue_last_scope",
        "dialogue_mode",
        "dialogue_phrases",
        "drag_physics",
        "dynamic_island",
        "edge_probe_enabled",
        "experimental_shared_decode",
        "experimental_single_process_spawn",
        "facing",
        "ffmpeg_recycle_minutes",
        "first_frame_cache_max_mb",
        "golden_spin_direct",
        "golden_spin_on_click",
        "harness_autostart",
        "idle_low_fps_enabled",
        "idle_low_fps_threshold",
        "lock_position",
        "look_screen_qr_enabled",  # 本 fork 新增（config.py reload 白名单同步登记）
        "media_prewarm",
        "menu_easter_egg",
        "modern_chat_background",
        "modern_chat_background_fill",
        "modern_chat_background_opacity",
        "modern_chat_card_opacity",
        "mouse_through",
        "music_lyric_cache_limit",
        "music_lyric_enabled",
        "music_lyric_lead_seconds",
        "music_player_paths",
        "music_sing_enabled",
        "music_sing_grace_seconds",
        "no_move",
        "on_top",
        "pet_opacity",
        "playback_speed",
        "pnpm_bin",
        "predict_prewarm_lead_ms",
        "quick_launch_apps",
        "bubble_text_scale",
        "rx",
        "ry",
        "scale",
        "screen_name",
        "self_talk_bubble_style",
        "self_talk_duration_seconds",
        "self_talk_enabled",
        "self_talk_image_dir",
        "self_talk_image_scale",
        "self_talk_max_interval",
        "self_talk_min_interval",
        "self_talk_texts",
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
        "user_customized",
        "voice_chime_custom_quotes_en",
        "voice_chime_custom_quotes_zh",
        "voice_chime_custom_times",
        "voice_chime_enabled",
        "voice_chime_pitch",
        "voice_chime_rate",
        "voice_chime_schedule",
        "voice_chime_show_bubble",
        "voice_chime_show_quote",
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
        "voice_chime_custom_quotes_en",
        "voice_chime_custom_quotes_zh",
        "voice_chime_voice",
        "voice_chime_volume",
    }
)

# 默认值 dict 里不走普通白名单、由 reload() 专门路径处理的键（现状文档化）。
# 2026-09-19 加入 file_interpret（拖文件解读，嵌套 dict 走 _merge_ 专门路径）。
SPECIAL_CASED_KEYS = frozenset({"version", "proactive_screen", "agent_link", "chat", "file_interpret"})

# 默认值 dict 键集合现状快照 = 白名单 ∪ 特例键。
# 2026-09-17 加入 music_player_paths（交付前审查 P1-3 登记）后实测：
# 白名单 118 + 特例 4 = 122。
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
    from pet.music_lyric_controller import LEAD_MAX_SECONDS, LEAD_MIN_SECONDS

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


def test_dirty_music_and_cost_config_does_not_break_settings_page(tmp_path):
    """设置页（settings_pet_controls）面对脏配置必须能构造出来。"""
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.music_lyric_controller import LEAD_MAX_SECONDS, LEAD_MIN_SECONDS

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
