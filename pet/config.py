# -*- coding: utf-8 -*-
"""配置读取与持久化；兼容旧版平铺 chat_* 字段的迁移。"""
from __future__ import annotations

import json
import os
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
    }


def _merge_proactive_screen_data(raw: Any) -> dict:
    result = _default_proactive_screen_data()
    if isinstance(raw, dict):
        result.update(raw)
    return result


def _merge_agent_link_data(raw: Any) -> dict:
    result = _default_agent_link_data()
    if isinstance(raw, dict):
        result.update(raw)
    return result


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
                # 避免沿用 openai-main 的钥匙串条目（密钥串用/查错 key）
                if not str(base.get("api_key_ref") or "").strip():
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


def _clean_self_talk_texts(value):
    if not isinstance(value, list):
        return list(DEFAULT_SELF_TALK_TEXTS)
    texts = []
    for item in value:
        text = str(item).strip()
        if text and text not in texts:
            texts.append(text[:120])
    return texts or list(DEFAULT_SELF_TALK_TEXTS)


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
        self.data = {
            "version": 4,
            "rx": None,
            "ry": None,
            "screen_name": None,
            "facing": "left",
            "scale": catalog.DEFAULT_SCALE,
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
            "self_talk_texts": list(DEFAULT_SELF_TALK_TEXTS),
            "self_talk_image_dir": "assets/big_blue_fat_fish",
            "self_talk_bubble_style": DEFAULT_SELF_TALK_BUBBLE_STYLE,
            "mouse_through": False,
            "drag_physics": False,
            "context_menu_template": "modern",
            "context_menu_appearance": dict(DEFAULT_CONTEXT_MENU_APPEARANCE),
            "menu_easter_egg": dict(DEFAULT_MENU_EASTER_EGG),
            "quick_launch_apps": [dict(item) for item in DEFAULT_QUICK_LAUNCH_APPS],
            "auto_hide_fullscreen": True,  # 全屏应用自动隐藏（Windows）
            "click_sound_enabled": True,   # 点击 Q 弹音效
            "click_sound_path": "",        # 自定义点击音效文件绝对路径（空=内置默认）
            "click_show_balance": False,   # 点击显示 DeepSeek 余额
            "click_show_self_talk": False, # 点击随机显示自定义自言自语
            "balance_refresh_minutes": 0,  # DeepSeek 余额自动刷新间隔（分钟，0=关闭）
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
            "proactive_screen": _default_proactive_screen_data(),
            "agent_link": _default_agent_link_data(),
            "chat_ui_style": "modern",  # modern / classic（仅聊天窗口保留双实现）
            "chat": _default_chat_data(),
        }
        self._load()
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

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
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
        self.data["chat"] = _merge_chat_data(merged)
        for key in (
            "rx", "ry", "screen_name", "facing", "scale", "on_top", "show_dock_icon", "no_move", "character",
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_bubble_style",
            "mouse_through", "drag_physics", "context_menu_template",
            "context_menu_appearance", "quick_launch_apps",
            "menu_easter_egg", "auto_hide_fullscreen",
            "click_sound_enabled", "click_sound_path",
            "click_show_balance", "click_show_self_talk",
            "balance_refresh_minutes", "autostart_wanted", "stream_capture_mode",
            "chat_background", "modern_chat_background",
            "chat_background_opacity", "chat_background_fill",
            "modern_chat_background_opacity", "modern_chat_background_fill",
            "modern_chat_card_opacity",
            "chat_bg_crops",
            "chat_ui_style",
            "character_aliases",
        ):
            if key in raw and raw[key] is not None:
                self.data[key] = raw[key]
        if "proactive_screen" in raw:
            self.data["proactive_screen"] = _merge_proactive_screen_data(raw["proactive_screen"])
        if "agent_link" in raw:
            self.data["agent_link"] = _merge_agent_link_data(raw["agent_link"])
        self.data["version"] = 4

    def _normalize_pet_settings(self):
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
        self.data["self_talk_enabled"] = bool(self.data.get("self_talk_enabled", False))
        self.data["show_dock_icon"] = bool(self.data.get("show_dock_icon", True))
        self.data["self_talk_texts"] = _clean_self_talk_texts(self.data.get("self_talk_texts"))
        bubble_style = str(self.data.get("self_talk_bubble_style") or "")
        self.data["self_talk_bubble_style"] = (
            bubble_style if bubble_style in SELF_TALK_BUBBLE_STYLES
            else DEFAULT_SELF_TALK_BUBBLE_STYLE
        )
        if self.data.get("context_menu_template") not in {"legacy", "modern"}:
            self.data["context_menu_template"] = "modern"
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

    def set(self, key, value):
        self.data[key] = value
        if key in {
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_bubble_style",
            "context_menu_appearance", "quick_launch_apps",
            "menu_easter_egg",
        }:
            self._normalize_pet_settings()

    def chat_settings(self):
        from .chat.models import ChatSettings
        return ChatSettings.from_dict(self.data.get("chat", {}))

    def set_chat_settings(self, settings):
        self.data["chat"] = settings.to_dict(include_secrets=True)

    def resolve_api_key(self, provider):
        from .chat.models import SecretStore
        return SecretStore().get(provider.api_key_ref) or provider.api_key

    def save(self):
        try:
            self._normalize_pet_settings()
            self.dir.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass
