# -*- coding: utf-8 -*-
"""配置读取与持久化；兼容旧版平铺 chat_* 字段的迁移。"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from . import catalog


DEFAULT_ANIMATION_GAP_SECONDS = 0.0
DEFAULT_SELF_TALK_MIN_INTERVAL = 20.0
DEFAULT_SELF_TALK_MAX_INTERVAL = 60.0
DEFAULT_SELF_TALK_DURATION_SECONDS = 3.2
DEFAULT_SELF_TALK_TEXTS = [
    "\u597d\u5973\u5b69\u2026\u2026",
    "\u597d\u6a21\u578b\u2026\u2026",
    "\u6b27\u9cb8\u9cb8\u2026\u2026",
    "\u4eca\u5929\u4e5f\u8981\u8ba4\u771f\u5de5\u4f5c\u5440\u3002",
    "\u518d\u966a\u4f60\u4e00\u4f1a\u513f\u3002",
]
DEFAULT_SELF_TALK_BUBBLE_STYLE = "classic_top"
DEFAULT_COLLISION_SETTINGS = {
    "collision_enabled": True,
    "collision_restitution": 0.82,
    "collision_friction": 0.08,
    "collision_mass_scale": 1.0,
    "collision_impulse_cap": 9000.0,
    "collision_sound_enabled": True,
    "collision_sound_volume": 0.70,
}
SELF_TALK_BUBBLE_STYLES = {
    "classic_top", "paper_left", "glass_right", "soft_blue_top", "breath_bubble",
}
DEFAULT_CONTEXT_MENU_APPEARANCE = {
    "theme": "system",
    "density": "standard",
    "corner_radius": 12,
    "ui_font": "system",
    "ui_font_size": 13,
    "translucent": True,
    "opacity": 0.94,
    "light_background": "#ffffff",
    "light_foreground": "#171717",
    "light_hover": "#eeeeee",
    "dark_background": "#252525",
    "dark_foreground": "#f3f3f3",
    "dark_hover": "#3a3a3a",
}
DEFAULT_MENU_EASTER_EGG = {
    "enabled": True,
    "title": "厉害了我的鲸",
    "hint": "请点击",
    "avatar": "assets/big_blue_fat_fish/ojingjing.jpg",
    "image_dir": "assets/big_blue_fat_fish",
}
DEFAULT_QUICK_LAUNCH_APPS = [
    {"name": "默认浏览器", "path": "", "kind": "default_browser"},
]


def _clean_menu_layout_override(value):
    return copy.deepcopy(value) if isinstance(value, dict) else None


def _clean_color(value, default):
    value = str(value or "").strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            pass
    return default


def _clean_menu_appearance(value):
    value = value if isinstance(value, dict) else {}
    defaults = DEFAULT_CONTEXT_MENU_APPEARANCE
    theme = str(value.get("theme", "system"))
    density = str(value.get("density", "standard"))
    try:
        radius = int(value.get("corner_radius", 12))
    except (TypeError, ValueError):
        radius = 12
    try:
        font_size = int(value.get("ui_font_size", 13))
    except (TypeError, ValueError):
        font_size = 13
    result = {
        "theme": theme if theme in {"system", "light", "dark"} else "system",
        "density": density if density in {"compact", "standard", "spacious"} else "standard",
        "corner_radius": max(6, min(18, radius)),
        "ui_font": str(value.get("ui_font") or "system")[:80],
        "ui_font_size": max(10, min(18, font_size)),
        "translucent": bool(value.get("translucent", True)),
        "opacity": _float_or_default(value.get("opacity"), 0.94, 0.72, 1.0),
    }
    for key in (
        "light_background", "light_foreground", "light_hover",
        "dark_background", "dark_foreground", "dark_hover",
    ):
        result[key] = _clean_color(value.get(key), defaults[key])
    return result


def _normalize_fun_asset_path(candidate: str, default: str) -> str:
    """绝对路径若指向应用内置 assets 目录，归一化为相对路径。

    旧版设置对话框会把默认相对路径固化成安装目录绝对路径；portable
    目录一移动/自更新即失效。此处在加载时统一还原为 assets/... 相对值。
    """
    candidate = str(candidate or "").strip()
    if not candidate:
        return default
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        return candidate
    assets_root = Path(__file__).resolve().parents[1] / "assets"
    try:
        rel = path.resolve().relative_to(assets_root.resolve())
        # 统一正斜杠：配置值与 legacy 迁移比较、跨平台一致
        return str(Path("assets") / rel).replace("\\", "/")
    except ValueError:
        return candidate


def _clean_menu_easter_egg(value):
    value = value if isinstance(value, dict) else {}
    defaults = DEFAULT_MENU_EASTER_EGG
    avatar = _normalize_fun_asset_path(
        str(value.get("avatar") or defaults["avatar"]).strip()[:500], defaults["avatar"]
    )
    image_dir = _normalize_fun_asset_path(
        str(value.get("image_dir") or defaults["image_dir"]).strip()[:500], defaults["image_dir"]
    )
    return {
        "enabled": bool(value.get("enabled", defaults["enabled"])),
        "title": str(value.get("title") or defaults["title"]).strip()[:40],
        "hint": str(value.get("hint") or defaults["hint"]).strip()[:20],
        "avatar": avatar,
        "image_dir": image_dir,
    }


def _clean_quick_launch_apps(value):
    if not isinstance(value, list):
        return [dict(item) for item in DEFAULT_QUICK_LAUNCH_APPS]
    cleaned = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "application")
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or "").strip()[:60]
        if kind == "default_browser":
            cleaned.append({"name": name or "默认浏览器", "path": "", "kind": "default_browser"})
        elif path and name:
            cleaned.append({"name": name, "path": path, "kind": "application"})
    return cleaned


def _default_proactive_screen_data() -> dict:
    return {
        "enabled": False,
        "dry_run": False,
        "preset": "balanced",
        "allow_when_mouse_through": True,
        "whitelist": [],
        "dwell_seconds": 45,
        "require_idle": False,
        "min_idle_seconds": 30,
        "cooldown_minutes": 5,
        "daily_cap": 15,
        "min_request_interval_seconds": 60,
        "change_threshold": 8,
        "prefer_free_provider": True,
        "pre_cue": True,
    }


def _default_agent_link_data() -> dict:
    return {
        "dsh": False,
        "claude": False,
        "cursor": False,
        "opencode": False,
        # 自定义联动 Agent（协议见 docs/AGENT_LINK_PROTOCOL.md §4）：只读监听
        # 用户指定的事件文件，不写外部配置、无需授权弹窗，默认空
        "custom_agents": [],
        # 联动气泡：开始干活提醒（可选，默认关）、任务完成通知（默认开）
        "notify_state": False,
        "notify_done": True,
        # 过程汇报（可选，默认关）：Agent 干活中报「正在读文件/跑命令/改代码…」
        "notify_activity": False,
        # 音效配置
        "sound_enabled": False,
        "sound_start_path": "builtin:agent-start",
        "sound_done_path": "builtin:agent-done",
        "sound_error_path": "builtin:agent-error",
        "sound_volume": 0.65,
        "sound_cooldown_seconds": 2.0,
        "sound_start_enabled": True,
        "sound_done_enabled": True,
        "sound_error_enabled": True,
    }


def _default_click_sound_pack() -> dict:
    return {"kind": "builtin", "id": "default", "path": ""}


def _clean_click_sound_pack(value: Any) -> dict:
    defaults = _default_click_sound_pack()
    if not isinstance(value, dict):
        return dict(defaults)
    kind = str(value.get("kind") or "builtin").strip().lower()
    if kind not in {"builtin", "file", "folder"}:
        return dict(defaults)
    pack_id = str(value.get("id") or ("default" if kind == "builtin" else "custom")).strip()
    if kind == "builtin" and pack_id not in {"default", "duck"}:
        pack_id = "default"
    path = str(value.get("path") or "").strip()[:500]
    return {
        "kind": kind,
        "id": pack_id,
        "path": path,
    }


# 内置联动 Agent 键：custom_agents 的 key 不得与之重复
_AGENT_LINK_BUILTIN_KEYS = ("dsh", "claude", "cursor", "opencode")
# 自定义联动 Agent 条目上限（防配置文件被塞爆）
_CUSTOM_AGENT_MAX = 8


def _clean_custom_agents(raw: Any) -> list[dict]:
    """清洗自定义联动 Agent 列表（agent_link.custom_agents）。

    条目 {key, name, path}：key 为小写标识（不得与内置键/其他条目重复），
    name 为显示名（缺省用 key），path 为事件文件路径（支持 ~，允许暂不存在）。
    非法条目直接丢弃，超出上限截断。"""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if len(result) >= _CUSTOM_AGENT_MAX:
            break
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", key):
            continue
        if key in _AGENT_LINK_BUILTIN_KEYS or key in seen:
            continue
        path = str(item.get("path") or "").strip()[:500]
        if not path:
            continue
        name = str(item.get("name") or "").strip()[:50] or key
        seen.add(key)
        result.append({"key": key, "name": name, "path": path})
    return result


def _clean_agent_link_data(raw: Any) -> dict:
    defaults = _default_agent_link_data()
    if not isinstance(raw, dict):
        return dict(defaults)
    result = dict(defaults)
    # 保留传入的额外合法键（例如 thinking_text, thinking_texts 等）
    result.update(raw)
    result["custom_agents"] = _clean_custom_agents(raw.get("custom_agents"))
    for key in (
        "dsh", "claude", "cursor", "opencode", "notify_state", "notify_done", "notify_activity",
        "sound_enabled", "sound_start_enabled", "sound_done_enabled", "sound_error_enabled",
    ):
        if key in raw:
            result[key] = bool(raw[key])
    for key in ("sound_start_path", "sound_done_path", "sound_error_path"):
        if key in raw:
            val = str(raw[key] or "").strip()[:500]
            result[key] = val or defaults[key]
    if "sound_volume" in raw:
        result["sound_volume"] = _float_or_default(raw.get("sound_volume"), defaults["sound_volume"], 0.0, 1.0)
    if "sound_cooldown_seconds" in raw:
        result["sound_cooldown_seconds"] = _float_or_default(
            raw.get("sound_cooldown_seconds"), defaults["sound_cooldown_seconds"], 0.0, 30.0
        )
    return result


def _merge_proactive_screen_data(raw: Any) -> dict:
    result = _default_proactive_screen_data()
    if isinstance(raw, dict):
        result.update(raw)
    return result


def _merge_agent_link_data(raw: Any) -> dict:
    return _clean_agent_link_data(raw)


def _default_chat_data():
    return {
        "enabled": True,
        "active_provider": "openai-main",
        "default_system_prompt": "\u4f60\u662f\u4e00\u53ea\u53ef\u7231\u7684\u684c\u9762\u5ba0\u7269\uff0c\u8bf7\u7528\u81ea\u7136\u3001\u53cb\u5584\u7684\u4e2d\u6587\u548c\u7528\u6237\u4ea4\u6d41\u3002",
        "history_message_limit": 40,
        "history_char_limit": 24000,
        "providers": {
            "openai-main": {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "chat_path": "/v1/chat/completions",
                "model": "deepseek-v4-flash",
                "api_key_ref": "provider/openai-main",
                "api_key": "",
                "timeout": 60.0,
                "temperature": 0.7,
                "max_tokens": 2048,
            }
        },
    }


def _merge_chat_data(raw):
    result = _default_chat_data()
    raw = raw if isinstance(raw, dict) else {}
    result.update({k: v for k, v in raw.items() if k != "providers"})
    incoming = raw.get("providers")
    if isinstance(incoming, dict) and incoming:
        providers = {}
        for provider_id, provider in incoming.items():
            if isinstance(provider, dict):
                base = dict(_default_chat_data()["providers"].get("openai-main", {}))
                base.update(provider)
                # 非 openai-main provider 未显式写 api_key_ref 时按自身归位，
                # 避免沿用 openai-main 的钥匙串条目（密钥串用/查错 key）。
                # 必须看用户原始输入：base 已被 openai-main 默认值预填，判 base 永远非空。
                if not str(provider.get("api_key_ref") or "").strip():
                    base["api_key_ref"] = f"provider/{provider_id}"
                # 历史 bug 迁移：旧版本曾把 openai-main 的钥匙串引用继承给自定义 provider，
                # UI 从不暴露该字段，非主 provider 挂着主引用一定是继承错的。
                if provider_id != "openai-main" and base.get("api_key_ref") == "provider/openai-main":
                    base["api_key_ref"] = f"provider/{provider_id}"
                providers[str(provider_id)] = base
    else:
        providers = dict(result["providers"])
    result["providers"] = providers or _default_chat_data()["providers"]
    active = str(result.get("active_provider") or "")
    result["active_provider"] = active if active in result["providers"] else next(iter(result["providers"]))
    return result


def _default_base():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".config"


def _app_dir_name() -> str:
    """打包变体的独立数据目录名；源码运行时回退到共享目录。

    构建脚本（scripts/build_onedir.ps1）会在打包前生成
    packaging/build_variant.py（VARIANT = "webm-chat" 等），
    使 Chat / 无 Chat 等变体各自使用独立的配置目录、会话与自启项。
    """
    try:
        from build_variant import VARIANT  # 仅打包产物中存在
        name = str(VARIANT).strip()
        if name:
            return f"dsh-pet-standalone-{name}"
    except Exception:
        pass
    return "dsh-pet-standalone"


APP_DIR_NAME = _app_dir_name()


def _float_or_default(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bool_or_default(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'off'}:
            return False
    return bool(default)


def _clean_self_talk_texts(value):
    if not isinstance(value, list):
        return list(DEFAULT_SELF_TALK_TEXTS)
    texts = []
    for item in value:
        text = str(item).strip()
        if text and text not in texts:
            texts.append(text[:120])
    return texts or list(DEFAULT_SELF_TALK_TEXTS)


def _default_dynamic_island_data() -> dict:
    """灵动岛默认配置：默认开启常驻，位置留空由首次显示时自动定位。"""
    return {
        "enabled": True,
        "show_icon": True,
        "show_name": True,
        "show_info": True,
        "info_mode": "time",       # time / balance_tier / balance / custom
        "custom_text": "",
        "show_status": True,
        "style": "dark",           # dark / light / glass
        "icon": "🐳",
        "x": None,
        "y": None,
    }


def _clean_dynamic_island_data(value) -> dict:
    defaults = _default_dynamic_island_data()
    if not isinstance(value, dict):
        return defaults
    result = dict(defaults)
    result.update({k: v for k, v in value.items() if k in defaults})
    result["enabled"] = bool(result["enabled"])
    result["show_icon"] = bool(result["show_icon"])
    result["show_name"] = bool(result["show_name"])
    result["show_info"] = bool(result["show_info"])
    result["show_status"] = bool(result["show_status"])
    mode = str(result.get("info_mode") or "time").strip()
    result["info_mode"] = mode if mode in {"time", "balance_tier", "balance", "custom"} else "time"
    result["custom_text"] = str(result.get("custom_text") or "")[:80]
    style = str(result.get("style") or "dark").strip()
    result["style"] = style if style in {"dark", "light", "glass"} else "dark"
    result["icon"] = str(result.get("icon") or "🐳").strip()[:8] or "🐳"
    # 至少保留一个组件：全部关闭时强制显示信息槽，避免空胶囊。
    if not (result["show_icon"] or result["show_name"] or result["show_info"] or result["show_status"]):
        result["show_info"] = True
    return result


def _clean_character_profiles(value) -> dict:
    """角色档案：当前先承载 click_talk_bindings，后续可扩展头像/人设字段。"""
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for character_id, profile in value.items():
        if not isinstance(profile, dict):
            continue
        bindings_raw = profile.get("click_talk_bindings")
        bindings = {}
        if isinstance(bindings_raw, dict):
            for action_id, texts in bindings_raw.items():
                if not isinstance(texts, list):
                    continue
                items = []
                for item in texts:
                    text = str(item).strip()
                    if text and text not in items:
                        items.append(text[:120])
                if items:
                    bindings[str(action_id)] = items
        entry = dict(profile)
        entry["click_talk_bindings"] = bindings
        cleaned[str(character_id)] = entry
    return cleaned


def _clean_collision_data(value: dict) -> dict:
    """归一化碰撞设置：collision_* 一组 7 键。

    从 Config._normalize_pet_settings 原样上提为模块级纯函数，供 Config 与
    config_domains 的 CollisionConfig 共用；清洗逻辑本体一行未改。
    """
    result = dict(value)
    result["collision_enabled"] = _bool_or_default(value.get("collision_enabled"), True)
    result["collision_restitution"] = _float_or_default(value.get("collision_restitution"), .82, 0.0, 1.0)
    result["collision_friction"] = _float_or_default(value.get("collision_friction"), .08, 0.0, .30)
    result["collision_mass_scale"] = _float_or_default(value.get("collision_mass_scale"), 1.0, .5, 2.0)
    result["collision_impulse_cap"] = _float_or_default(value.get("collision_impulse_cap"), 9000.0, 1000.0, 12000.0)
    result["collision_sound_enabled"] = bool(value.get("collision_sound_enabled", True))
    result["collision_sound_volume"] = _float_or_default(
        value.get("collision_sound_volume"), 0.70, 0.0, 1.0
    )
    return result


class Config:
    def __init__(self, base=None, instance_id: str | None = None):
        base = Path(base) if isinstance(base, str) else (base or _default_base())
        self.dir = base / APP_DIR_NAME
        # 多开隔离：--instance <id> 或 DSH_PET_INSTANCE 时使用独立配置文件，
        # 位置/大小/朝向等不再互相覆盖；不传时完全保持原行为。
        self.instance_id = (instance_id or os.environ.get("DSH_PET_INSTANCE", "") or "").strip()
        self.path = (
            self.dir / f"config-{self.instance_id}.json"
            if self.instance_id
            else self.dir / "config.json"
        )
        self._migrate_legacy_config(base)
        fresh_spawn = os.environ.get("DSH_PET_SPAWN_FRESH") == "1"
        if self.instance_id and (not self.path.exists() or fresh_spawn):
            self._seed_slot_config_from_main(force=fresh_spawn)
        self.data = {
            "version": 4,
            "rx": None,
            "ry": None,
            "screen_name": None,
            "facing": "left",
            "scale": catalog.DEFAULT_SCALE,
            "spawn_inherit_size": True,  # 生小肥鱼继承主肥鱼大小（False 用 spawn_scale）
            "spawn_scale": catalog.DEFAULT_SCALE,  # 关闭继承时生小肥鱼使用的尺寸
            "spawn_inherit_dynamic_island": False,  # 生小肥鱼继承主肥鱼灵动岛（默认关=不开灵动岛）
            "on_top": True,
            "show_dock_icon": True,
            "no_move": False,
            "character": catalog.DEFAULT_CHARACTER,
            "playback_speed": 1.0,
            "animation_gap_seconds": DEFAULT_ANIMATION_GAP_SECONDS,
            "self_talk_enabled": False,
            "self_talk_min_interval": DEFAULT_SELF_TALK_MIN_INTERVAL,
            "self_talk_max_interval": DEFAULT_SELF_TALK_MAX_INTERVAL,
            "self_talk_duration_seconds": DEFAULT_SELF_TALK_DURATION_SECONDS,
            "self_talk_image_scale": 100,  # 气泡配图显示尺寸百分比（50~300，100 = 默认）
            "self_talk_texts": list(DEFAULT_SELF_TALK_TEXTS),
            "self_talk_image_dir": "assets/big_blue_fat_fish",
            "self_talk_bubble_style": DEFAULT_SELF_TALK_BUBBLE_STYLE,
            "mouse_through": False,
            "cursor_hidden_passthrough": True,
            "drag_physics": False,
            "lock_position": False,  # 锁定位置：桌宠不可拖动（点击仍有效）
            "shift_drag": False,     # 按住 SHIFT+左键才能拖动
            "pet_opacity": 100,      # 桌宠窗口不透明度 10-100
            "context_menu_template": "modern",
            "context_menu_layout": None,
            "context_menu_appearance": dict(DEFAULT_CONTEXT_MENU_APPEARANCE),
            "menu_easter_egg": dict(DEFAULT_MENU_EASTER_EGG),
            "quick_launch_apps": [dict(item) for item in DEFAULT_QUICK_LAUNCH_APPS],
            "auto_hide_fullscreen": True,  # 全屏应用自动隐藏（Windows）
            "click_sound_enabled": True,   # 点击 Q 弹音效
            "click_sound_path": "",        # 自定义点击音效文件绝对路径（空=内置默认）
            "click_sound_pack": _default_click_sound_pack(),
            "click_sound_volume": 0.70,
            "slingshot_enabled": True,     # 弹弓弹射
            "throw_strength": "standard",  # gentle / standard / strong / crazy
            "throw_max_speed": 4800.0,     # 由 throw_strength 导出
            "idle_low_fps_enabled": False,  # 闲置降帧（灰度默认关）：长时间无交互时动画隔帧呈现
            "idle_low_fps_threshold": 30.0,  # 闲置阈值（秒）：超过该时长无交互且窗口可见才降帧
            "animation_prewarm_enabled": True,  # 动画预热（默认开）：预载高频/随机动作首帧以换流畅；关闭省内存
            "click_show_balance": False,   # 点击显示 DeepSeek 余额
            "click_show_self_talk": False, # 点击随机显示自定义自言自语
            "balance_refresh_minutes": 0,  # DeepSeek 余额自动刷新间隔（分钟，0=关闭）
            "balance_tier_labels_mode": "default",  # 峰谷提示文案：default / liangwen / custom
            "balance_tier_label_peak": "",  # 自定义“高峰”文本（custom 模式）
            "balance_tier_label_idle": "",  # 自定义“空闲”文本（custom 模式）
            "balance_tier_color_enabled": True,  # 峰谷提示颜色：高峰红/低谷绿
            "music_sing_enabled": False,   # 检测到后台播放音乐时自动播放唱歌动画
            "autostart_wanted": False,     # 用户曾开启过开机自启（用于启动自检：被安全软件清理时提醒）
            "stream_capture_mode": False,  # 直播捕获兼容模式（Windows：Tool 窗口直播姬/OBS 枚举不到）
            "chat_background": "",  # 肥鱼牌小手机背景：空=纯色；builtin:* = 内置主题；否则为图片路径
            "modern_chat_background": "",  # 肥鱼版 DeepSeek 背景：空=纯色；否则为自定义图片路径
            "chat_background_opacity": 100,
            "chat_background_fill": "cover",
            "modern_chat_background_opacity": 100,
            "modern_chat_background_fill": "cover",
            "modern_chat_card_opacity": 84,
            "chat_bg_crops": {},    # 每个背景的用户自定义取景框 {背景标识: [x,y,w,h] 归一化}
            "character_aliases": {},  # 角色显示名别名 {角色id: 自定义名}，空名=恢复默认
            "character_profiles": {},  # 角色档案：{角色id: {click_talk_bindings: {动画id: [台词]}}}
            "chat_always_on_top": False,  # 聊天窗置顶
            "dynamic_island": _default_dynamic_island_data(),
            "proactive_screen": _default_proactive_screen_data(),
            "agent_link": _default_agent_link_data(),
            "chat_ui_style": "modern",  # modern / classic（仅聊天窗口保留双实现）
            "chat_follow_pet": False,   # 聊天窗口是否跟随桌宠移动
            # P3 broker（灰度默认关，不进设置 UI）：多开同角色空闲素材共享解码开关。
            # 前置条件 = collision_enabled（broker 骑在碰撞 QLocal 通道上）。
            # ⚠ 平台限定（P3A R2 P0-1 / R3 收口）：本键只在 Windows x86/x64
            # （AMD64/x86_64，TSO）上生效——共享内存 seqlock 的 seq 提交词只经
            # ctypes 普通 8B load/store，无 acquire/release/跨进程 barrier，
            # 弱序平台（ARM macOS/Linux 及 **Windows ARM64**）上协议不作正确性
            # 声明。非支持平台即使本键为 True，BrokerFacade 的 enabled 判定
            # （decode_broker.broker_platform_supported()：OS + 架构双重检查）
            # 也强制为 False：本键只是「用户请求」，平台门禁在启用点收口。
            "decode_broker_enabled": False,
            "system_notifications_enabled": True,  # 对话完成/失败/需要授权时弹桌面系统通知
            "todo_reminder_enabled": True,   # 待办提醒总开关
            "todo_reminder_lead_minutes": 5,  # 待办提前提醒分钟数（0~60，0=不提前）
            **DEFAULT_COLLISION_SETTINGS,
            "chat": _default_chat_data(),
        }
        self.reload()
        self._normalize_pet_settings()

    def _migrate_legacy_config(self, base) -> None:
        """旧版各变体共用 %APPDATA%/dsh-pet-standalone；升级后首次运行时
        把该目录的 config.json 与 sessions/ 一次性复制到变体独立目录，
        避免用户设置与聊天会话“消失”。仅在新目录尚不存在时执行。"""
        if self.instance_id:
            return  # 多开实例不参与旧版迁移，避免把单开配置复制给每个实例
        if APP_DIR_NAME == "dsh-pet-standalone" or self.path.exists():
            return
        legacy = base / "dsh-pet-standalone"
        if not (legacy / "config.json").is_file():
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy / "config.json", self.path)
            src_sessions = legacy / "sessions"
            if src_sessions.is_dir():
                shutil.copytree(src_sessions, self.dir / "sessions", dirs_exist_ok=True)
        except OSError:
            pass

    def _seed_slot_config_from_main(self, *, force: bool = False) -> None:
        """新建副槽时继承主配置（issue #69-6），避免“生小肥鱼恢复默认设置”。

        只在该槽位还没有个体配置文件、或本次是通过“生小肥鱼”显式孵化的
        新进程（DSH_PET_SPAWN_FRESH=1）时执行；普通重启已有 slot-N 配置
        仍保持独立记忆。复制主 config.json 后做副槽化处理：位置回到自动
        摆放、开机自启仍只归主槽所有。写盘副本沿用主配置的脱敏策略，
        不把明文 API Key 复制进副槽。
        """
        main_path = self.dir / "config.json"
        if (not force and self.path.exists()) or not main_path.is_file():
            return
        try:
            raw = json.loads(main_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        seed = copy.deepcopy(raw)
        seed["version"] = 4
        # 副槽不继承主桌宠的位置/屏幕，避免新鱼叠在旧鱼身上；自启仍仅主槽。
        seed["rx"] = None
        seed["ry"] = None
        seed["screen_name"] = None
        seed["autostart_wanted"] = False
        # 生小肥鱼大小策略：开启继承 → 保留主配置 scale；
        # 关闭继承 → 用主配置里给“小肥鱼”单独选择的 spawn_scale。
        inherit_size = _bool_or_default(seed.get("spawn_inherit_size"), True)
        seed["spawn_inherit_size"] = inherit_size
        if not inherit_size:
            try:
                seed["scale"] = float(seed.get("spawn_scale", catalog.DEFAULT_SCALE))
            except (TypeError, ValueError):
                seed["scale"] = catalog.DEFAULT_SCALE
        # 生小肥鱼灵动岛策略：默认不继承 → 小肥鱼不开启自己的灵动岛；
        # 开启继承 → 保留主配置的 dynamic_island（含是否启用）。
        inherit_island = _bool_or_default(seed.get("spawn_inherit_dynamic_island"), False)
        seed["spawn_inherit_dynamic_island"] = inherit_island
        island = seed.get("dynamic_island")
        if isinstance(island, dict):
            island["enabled"] = bool(inherit_island)
        else:
            seed["dynamic_island"] = {"enabled": bool(inherit_island)}
        chat = seed.get("chat")
        if isinstance(chat, dict):
            providers = chat.get("providers")
            if isinstance(providers, dict):
                for provider in providers.values():
                    if isinstance(provider, dict):
                        provider.pop("api_key", None)
                        provider.pop("vision_api_key", None)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.seed.tmp")
            temp.write_text(
                json.dumps(seed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except OSError as exc:
            logging.warning("从主配置播种副槽配置失败: %s (%s)", self.path, exc)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def reload(self):
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            from . import slot_manager as slot_manager_mod
            slot_manager_mod.backup_corrupt_config(self.path)
            return
        if not isinstance(raw, dict):
            from . import slot_manager as slot_manager_mod
            slot_manager_mod.backup_corrupt_config(self.path)
            return
        try:
            old_version = int(raw.get("version", 1) or 1)
        except (TypeError, ValueError):
            old_version = 1  # 脏数据（手改/损坏）不得导致启动崩溃
        if old_version < 2:
            raw.pop("scale", None)
        chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
        legacy = {}
        if "chat_enabled" in raw:
            legacy["enabled"] = raw["chat_enabled"]
        if "chat_system_prompt" in raw:
            legacy["default_system_prompt"] = raw["chat_system_prompt"]
        legacy_provider = {}
        if raw.get("chat_api_url"):
            legacy_provider["base_url"] = raw["chat_api_url"]
        if raw.get("chat_model"):
            legacy_provider["model"] = raw["chat_model"]
        if raw.get("chat_api_key"):
            legacy_provider["api_key"] = raw["chat_api_key"]
        if legacy_provider:
            legacy["providers"] = {"openai-main": legacy_provider}
        merged = dict(legacy)
        merged.update(chat)
        # secret 只进不出：磁盘重载不得冲掉内存中的 key。
        # _redacted_data() 写盘时会剔除 chat.providers 下的明文 api_key /
        # vision_api_key（keyring 不可用时 key 只存内存 self.data），因此磁盘文件
        # 里没有这两项。这里若某 provider 在磁盘数据里缺 api_key/vision_api_key
        # 但合入前的内存里有，则保留内存值，避免设置对话框重开（自 config.reload()
        # 从磁盘重载）把用户未重启就丢掉的 key 覆盖成空。新旧两套设置对话框都走
        # 这条 reload() 路径，一处修复全覆盖。
        previous_chat = self.data.get("chat")
        previous_providers = (
            previous_chat.get("providers") if isinstance(previous_chat, dict) else None
        )
        merged_chat = _merge_chat_data(merged)
        self.data["chat"] = merged_chat
        if isinstance(previous_providers, dict):
            raw_providers = merged.get("providers")
            raw_providers = raw_providers if isinstance(raw_providers, dict) else {}
            merged_providers = merged_chat.get("providers")
            if isinstance(merged_providers, dict):
                for provider_id, merged_provider in merged_providers.items():
                    if not isinstance(merged_provider, dict):
                        continue
                    previous_provider = previous_providers.get(provider_id)
                    if not isinstance(previous_provider, dict):
                        continue
                    raw_provider = raw_providers.get(provider_id)
                    raw_provider = raw_provider if isinstance(raw_provider, dict) else {}
                    if "api_key" not in raw_provider and previous_provider.get("api_key"):
                        merged_provider["api_key"] = previous_provider["api_key"]
                    if "vision_api_key" not in raw_provider and previous_provider.get("vision_api_key"):
                        merged_provider["vision_api_key"] = previous_provider["vision_api_key"]
        for key in (
            "rx", "ry", "screen_name", "facing", "scale", "on_top", "show_dock_icon", "no_move", "character",
            "spawn_inherit_size", "spawn_scale", "spawn_inherit_dynamic_island",
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_image_scale",
            "self_talk_bubble_style",
             "mouse_through", "cursor_hidden_passthrough", "drag_physics", "context_menu_template",
            "context_menu_layout",
            "lock_position", "shift_drag", "pet_opacity",
            "context_menu_appearance", "quick_launch_apps",
            "menu_easter_egg", "auto_hide_fullscreen",
            "click_sound_enabled", "click_sound_path",
            "click_sound_pack", "click_sound_volume",
            "slingshot_enabled", "throw_strength", "throw_max_speed",
            "idle_low_fps_enabled", "idle_low_fps_threshold",
            "animation_prewarm_enabled",
            "click_show_balance", "click_show_self_talk",
            "balance_refresh_minutes", "autostart_wanted", "stream_capture_mode",
            "music_sing_enabled",
            "balance_tier_labels_mode", "balance_tier_label_peak",
            "balance_tier_label_idle", "balance_tier_color_enabled",
            "chat_background", "modern_chat_background",
            "chat_background_opacity", "chat_background_fill",
            "modern_chat_background_opacity", "modern_chat_background_fill",
            "modern_chat_card_opacity",
            "chat_bg_crops",
            "chat_ui_style",
            "chat_follow_pet",
            "system_notifications_enabled",
            "todo_reminder_enabled", "todo_reminder_lead_minutes",
            "character_aliases",
            "character_profiles",
            "chat_always_on_top",
            "dynamic_island",
            "collision_enabled", "collision_restitution", "collision_friction",
            "collision_mass_scale", "collision_impulse_cap",
            "collision_sound_enabled", "collision_sound_volume",
            "decode_broker_enabled",
        ):
            if key in raw and raw[key] is not None:
                self.data[key] = raw[key]
        if "proactive_screen" in raw:
            self.data["proactive_screen"] = _merge_proactive_screen_data(raw["proactive_screen"])
        if "agent_link" in raw:
            self.data["agent_link"] = _merge_agent_link_data(raw["agent_link"])
        self._migrate_click_sound_config(raw)
        self.data["version"] = 4
        self._migrate_plaintext_keys_to_keyring()

    def _migrate_plaintext_keys_to_keyring(self) -> None:
        """加载时把磁盘遗留的明文 API Key 迁移进 keyring。

        v4.0.4/4.0.5 起 _redacted_data() 写盘时剔除 chat.providers 下的明文
        api_key/vision_api_key，但 SecretStore.set 只在设置对话框保存时调用——
        老版本（≤v4.0.0）磁盘上的明文 key 从未进过 keyring，升级后首次写盘即被剔除，
        重启后 resolve_api_key 拿不到任何值，聊天/视觉 401 静默失效。
        此处补迁移：keyring 已有值不覆盖（与 resolve_api_key 的 keyring 优先序一致），
        仅丢弃明文；set 失败（keyring 不可用）保留内存明文，维持原兜底行为。
        幂等：迁移成功后内存/磁盘均无明文，重复 reload 无副作用；不主动 save()，
        写盘剔除交给下次正常保存。
        """
        chat = self.data.get("chat")
        providers = chat.get("providers") if isinstance(chat, dict) else None
        if not isinstance(providers, dict):
            return
        from .chat.models import SecretStore  # 惰性导入，且只实例化一次
        store = SecretStore()
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            for key_field, ref_field, default_ref in (
                ("api_key", "api_key_ref", f"provider/{provider_id}"),
                ("vision_api_key", "vision_api_key_ref", f"provider/{provider_id}/vision"),
            ):
                plaintext = str(provider.get(key_field) or "")
                if not plaintext.strip():
                    continue
                ref = str(provider.get(ref_field) or "").strip()
                if not ref:
                    ref = default_ref
                    provider[ref_field] = ref
                if store.get(ref) or store.set(ref, plaintext):
                    provider.pop(key_field, None)

    def _migrate_click_sound_config(self, raw: dict) -> None:
        """旧版 click_sound_path 迁移为 click_sound_pack。"""
        # 如果 raw 里面没有明确合法的 click_sound_pack，但有旧 click_sound_path
        has_explicit_pack = isinstance(raw.get("click_sound_pack"), dict) and bool(
            raw.get("click_sound_pack", {}).get("kind")
        )
        if not has_explicit_pack:
            old_path = str(raw.get("click_sound_path") or "").strip()
            if old_path:
                self.data["click_sound_pack"] = {
                    "kind": "file",
                    "id": "custom",
                    "path": old_path,
                }
            else:
                self.data["click_sound_pack"] = _default_click_sound_pack()

    def _normalize_pet_settings(self):
        from . import physics as physics_mod

        self.data["playback_speed"] = _float_or_default(self.data.get("playback_speed"), 1.0, 0.1, 8.0)
        self.data["animation_gap_seconds"] = _float_or_default(
            self.data.get("animation_gap_seconds"), DEFAULT_ANIMATION_GAP_SECONDS, 0.0, 3600.0
        )
        minimum = _float_or_default(
            self.data.get("self_talk_min_interval"), DEFAULT_SELF_TALK_MIN_INTERVAL, 5.0, 3600.0
        )
        maximum = _float_or_default(
            self.data.get("self_talk_max_interval"), DEFAULT_SELF_TALK_MAX_INTERVAL, 5.0, 3600.0
        )
        self.data["self_talk_min_interval"] = min(minimum, maximum)
        self.data["self_talk_max_interval"] = max(minimum, maximum)
        self.data["self_talk_duration_seconds"] = _float_or_default(
            self.data.get("self_talk_duration_seconds"),
            DEFAULT_SELF_TALK_DURATION_SECONDS,
            1.0,
            300.0,
        )
        self.data["self_talk_image_dir"] = str(
            self.data.get("self_talk_image_dir") or ""
        ).strip()[:500]
        self.data["self_talk_image_scale"] = int(_float_or_default(
            self.data.get("self_talk_image_scale"), 100.0, 50.0, 300.0
        ))
        self.data["self_talk_enabled"] = bool(self.data.get("self_talk_enabled", False))
        self.data["cursor_hidden_passthrough"] = _bool_or_default(
            self.data.get("cursor_hidden_passthrough"), True
        )
        self.data["spawn_inherit_size"] = _bool_or_default(
            self.data.get("spawn_inherit_size"), True
        )
        self.data["spawn_scale"] = _float_or_default(
            self.data.get("spawn_scale"), catalog.DEFAULT_SCALE, 0.1, 4.0
        )
        self.data["spawn_inherit_dynamic_island"] = _bool_or_default(
            self.data.get("spawn_inherit_dynamic_island"), False
        )
        self.data["show_dock_icon"] = bool(self.data.get("show_dock_icon", True))
        self.data["self_talk_texts"] = _clean_self_talk_texts(self.data.get("self_talk_texts"))
        bubble_style = str(self.data.get("self_talk_bubble_style") or "")
        self.data["self_talk_bubble_style"] = (
            bubble_style if bubble_style in SELF_TALK_BUBBLE_STYLES
            else DEFAULT_SELF_TALK_BUBBLE_STYLE
        )
        if self.data.get("context_menu_template") not in {"legacy", "modern"}:
            self.data["context_menu_template"] = "modern"
        self.data["context_menu_layout"] = _clean_menu_layout_override(
            self.data.get("context_menu_layout")
        )
        self.data["context_menu_appearance"] = _clean_menu_appearance(
            self.data.get("context_menu_appearance")
        )
        self.data["menu_easter_egg"] = _clean_menu_easter_egg(
            self.data.get("menu_easter_egg")
        )
        self.data["quick_launch_apps"] = _clean_quick_launch_apps(
            self.data.get("quick_launch_apps")
        )
        if self.data.get("chat_ui_style") not in {"modern", "classic"}:
            self.data["chat_ui_style"] = "modern"
        self.data["character_profiles"] = _clean_character_profiles(
            self.data.get("character_profiles")
        )
        self.data["chat_always_on_top"] = bool(self.data.get("chat_always_on_top", False))
        self.data["dynamic_island"] = _clean_dynamic_island_data(
            self.data.get("dynamic_island")
        )
        for prefix in ("chat_background", "modern_chat_background"):
            opacity_key = f"{prefix}_opacity"
            fill_key = f"{prefix}_fill"
            try:
                opacity = int(self.data.get(opacity_key, 100))
            except (TypeError, ValueError):
                opacity = 100
            self.data[opacity_key] = max(10, min(100, opacity))
            fill = str(self.data.get(fill_key, "cover") or "cover")
            self.data[fill_key] = fill if fill in {"cover", "contain", "stretch"} else "cover"
        try:
            card_opacity = int(self.data.get("modern_chat_card_opacity", 84))
        except (TypeError, ValueError):
            card_opacity = 84
        self.data["modern_chat_card_opacity"] = max(10, min(100, card_opacity))

        # 点击音效 & 弹弓 & 物理力度归一化
        self.data["click_sound_enabled"] = bool(self.data.get("click_sound_enabled", True))
        self.data["click_sound_pack"] = _clean_click_sound_pack(self.data.get("click_sound_pack"))
        self.data["click_sound_volume"] = _float_or_default(self.data.get("click_sound_volume"), 0.70, 0.0, 1.0)
        self.data["slingshot_enabled"] = bool(self.data.get("slingshot_enabled", True))
        strength = physics_mod.normalize_throw_strength(str(self.data.get("throw_strength") or "standard"))
        self.data["throw_strength"] = strength
        self.data["throw_max_speed"] = physics_mod.throw_speed_cap(strength)
        # 闲置降帧（性能调研 §4.3）：开关默认关（灰度）；阈值夹到 [1, 3600] 秒
        # 终审 P1-3：必须用 _bool_or_default——bool("false") is True，字符串
        # 布尔（外部手改配置/旧版导出）会被误开；与 decode_broker_enabled 同规。
        self.data["idle_low_fps_enabled"] = _bool_or_default(
            self.data.get("idle_low_fps_enabled"), False
        )
        self.data["idle_low_fps_threshold"] = _float_or_default(
            self.data.get("idle_low_fps_threshold"), 30.0, 1.0, 3600.0
        )
        # 动画预热（Phase 2，默认开）：关闭后不再后台预载大量动画首帧。
        self.data["animation_prewarm_enabled"] = _bool_or_default(
            self.data.get("animation_prewarm_enabled"), True
        )
        # P3 broker（灰度默认关）：多开同角色空闲素材共享解码开关。
        # ⚠ 平台限定（P3A R2 P0-1 / R3，与 defaults 声明一致）：本键只在
        # Windows x86/x64（AMD64/x86_64 TSO）上生效——非支持平台（非 Windows，
        # 或 Windows ARM64 弱序）即使归一后为 True，BrokerFacade 的 enabled
        # 判定也会与 broker_platform_supported()（Windows 且 AMD64/x86_64）
        # 做与而强制关闭；此处归一只保证「类型为 bool」，「是否启用」由启用点
        # 的平台门禁收口。
        self.data["decode_broker_enabled"] = _bool_or_default(
            self.data.get("decode_broker_enabled"), False
        )
        # 上游 #60 系统通知开关：同规防字符串布尔误开（bool("false") is True）。
        self.data["system_notifications_enabled"] = _bool_or_default(
            self.data.get("system_notifications_enabled"), True
        )
        # 待办提醒：开关同规防字符串布尔误开；提前量钳到 [0, 60] 分钟（0=不提前）。
        self.data["todo_reminder_enabled"] = _bool_or_default(
            self.data.get("todo_reminder_enabled"), True
        )
        self.data["todo_reminder_lead_minutes"] = int(_float_or_default(
            self.data.get("todo_reminder_lead_minutes"), 5.0, 0.0, 60.0
        ))
        self.data["agent_link"] = _clean_agent_link_data(self.data.get("agent_link"))
        self.data.update(_clean_collision_data(self.data))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def character_alias(self, character_id: str) -> str:
        """用户自定义的角色显示名；未设置返回空串。"""
        aliases = self.data.get("character_aliases")
        if isinstance(aliases, dict):
            return str(aliases.get(character_id, "") or "").strip()
        return ""

    def set_character_alias(self, character_id: str, name: str) -> None:
        """设置角色显示名别名（最长 24 字符）；空名表示恢复默认。"""
        aliases = self.data.setdefault("character_aliases", {})
        if not isinstance(aliases, dict):
            aliases = {}
            self.data["character_aliases"] = aliases
        name = (name or "").strip()[:24]
        if name:
            aliases[character_id] = name
        else:
            aliases.pop(character_id, None)
        self.save()

    def character_profile(self, character_id: str) -> dict:
        """返回角色档案；不存在时返回空档案。"""
        profiles = self.data.get("character_profiles")
        if isinstance(profiles, dict):
            profile = profiles.get(str(character_id))
            if isinstance(profile, dict):
                return profile
        return {}

    def click_talk_bindings(self, character_id: str) -> dict:
        """返回某角色的点击动画台词绑定：{动画id: [台词, ...]}。"""
        profile = self.character_profile(character_id)
        bindings = profile.get("click_talk_bindings")
        return bindings if isinstance(bindings, dict) else {}

    def click_talk_texts_for(self, character_id: str, action_id: str) -> list[str]:
        """返回某点击动画绑定的台词；未绑定返回空列表。"""
        bindings = self.click_talk_bindings(character_id)
        texts = bindings.get(str(action_id))
        return texts if isinstance(texts, list) else []

    def set_click_talk_bindings(self, character_id: str, bindings: dict) -> None:
        """保存某角色的点击动画台词绑定并立即落盘。"""
        profiles = self.data.setdefault("character_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["character_profiles"] = profiles
        profile = profiles.setdefault(str(character_id), {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[str(character_id)] = profile
        profile["click_talk_bindings"] = bindings
        self.data["character_profiles"] = _clean_character_profiles(profiles)
        self.save()

    def set(self, key, value):
        self.data[key] = value
        if key in {
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_image_scale",
            "self_talk_bubble_style",
            "context_menu_appearance", "context_menu_layout", "quick_launch_apps",
            "menu_easter_egg",
            "click_sound_enabled", "click_sound_pack", "click_sound_volume",
            "collision_sound_enabled", "collision_sound_volume",
            "slingshot_enabled", "throw_strength", "agent_link",
            "idle_low_fps_enabled", "idle_low_fps_threshold",
            "animation_prewarm_enabled",
            "spawn_inherit_size", "spawn_scale", "spawn_inherit_dynamic_island",
            "todo_reminder_enabled", "todo_reminder_lead_minutes",
            "character_profiles", "chat_always_on_top", "dynamic_island",
        }:
            self._normalize_pet_settings()

    def chat_settings(self):
        from .chat.models import ChatSettings
        return ChatSettings.from_dict(self.data.get("chat", {}))

    def set_chat_settings(self, settings):
        self.data["chat"] = settings.to_dict(include_secrets=True)

    # ---- 域 facade 便捷入口（批5：只建不用，调用点未迁移）----
    # 返回对应域的轻量视图（pet/config_domains.py）。normalize 复用本模块现有
    # _merge_*/_clean_* 函数；facade 只读，不写盘、不碰 secret 保留/version 迁移。
    def chat_config(self):
        from .config_domains import ChatConfig
        return ChatConfig.from_dict(self.data.get("chat", {}))

    def agent_link_config(self):
        from .config_domains import AgentLinkConfig
        return AgentLinkConfig.from_dict(self.data.get("agent_link", {}))

    def proactive_config(self):
        from .config_domains import ProactiveConfig
        return ProactiveConfig.from_dict(self.data.get("proactive_screen", {}))

    def collision_config(self):
        from .config_domains import CollisionConfig
        return CollisionConfig.from_dict(self.data)

    def menu_config(self):
        from .config_domains import MenuConfig
        return MenuConfig.from_dict(self.data)

    def resolve_api_key(self, provider):
        from .chat.models import SecretStore
        return SecretStore().get(provider.api_key_ref) or provider.api_key

    def _redacted_data(self) -> dict:
        """深拷贝待写盘数据，并剔除 chat.providers 下的明文 API Key。

        keyring 不可用时（SecretStore.set 返回 False）设置对话框会把 key 放进
        provider.api_key / vision_api_key 供本次运行使用；写盘时必须剔除，
        避免明文落盘——key 只保留在内存（self.data），重启需重输。
        """
        write_data = copy.deepcopy(self.data)
        chat = write_data.get("chat")
        if isinstance(chat, dict):
            providers = chat.get("providers")
            if isinstance(providers, dict):
                for provider in providers.values():
                    if isinstance(provider, dict):
                        provider.pop("api_key", None)
                        provider.pop("vision_api_key", None)
        return write_data

    def save(self) -> bool:
        """把配置写入磁盘；成功返回 True，失败返回 False（并记录 warning）。

        写盘使用 _redacted_data() 的副本，self.data 本身不动，保证运行期
        key 在内存可见而不会明文落盘。
        临时文件名加入 PID 后缀，避免错误并发写入撞名。
        """
        try:
            self._normalize_pet_settings()
            self.dir.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            temp.write_text(
                json.dumps(self._redacted_data(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except OSError as exc:
            logging.warning("保存配置失败: %s (%s)", self.path, exc)
            return False
        return True
