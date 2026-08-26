# -*- coding: utf-8 -*-
"""配置读取与持久化；兼容旧版平铺 chat_* 字段的迁移。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from . import catalog


DEFAULT_ANIMATION_GAP_SECONDS = 0.0
DEFAULT_SELF_TALK_MIN_INTERVAL = 20.0
DEFAULT_SELF_TALK_MAX_INTERVAL = 60.0
DEFAULT_SELF_TALK_TEXTS = [
    "\u597d\u5973\u5b69\u2026\u2026",
    "\u597d\u6a21\u578b\u2026\u2026",
    "\u6b27\u9cb8\u9cb8\u2026\u2026",
    "\u4eca\u5929\u4e5f\u8981\u8ba4\u771f\u5de5\u4f5c\u5440\u3002",
    "\u518d\u966a\u4f60\u4e00\u4f1a\u513f\u3002",
]


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
    def __init__(self, base=None):
        base = Path(base) if isinstance(base, str) else (base or _default_base())
        self.dir = base / APP_DIR_NAME
        self.path = self.dir / "config.json"
        self._migrate_legacy_config(base)
        self.data = {
            "version": 3,
            "rx": None,
            "ry": None,
            "facing": "left",
            "scale": catalog.DEFAULT_SCALE,
            "on_top": True,
            "no_move": False,
            "character": catalog.DEFAULT_CHARACTER,
            "playback_speed": 1.0,
            "animation_gap_seconds": DEFAULT_ANIMATION_GAP_SECONDS,
            "self_talk_enabled": False,
            "self_talk_min_interval": DEFAULT_SELF_TALK_MIN_INTERVAL,
            "self_talk_max_interval": DEFAULT_SELF_TALK_MAX_INTERVAL,
            "self_talk_texts": list(DEFAULT_SELF_TALK_TEXTS),
            "mouse_through": False,
            "drag_physics": False,
            "auto_hide_fullscreen": True,  # 全屏应用自动隐藏（Windows）
            "click_sound_enabled": True,   # 点击 Q 弹音效
            "click_show_balance": False,   # 点击显示 DeepSeek 余额
            "click_show_self_talk": False, # 点击随机显示自定义自言自语
            "balance_refresh_minutes": 0,  # DeepSeek 余额自动刷新间隔（分钟，0=关闭）
            "autostart_wanted": False,     # 用户曾开启过开机自启（用于启动自检：被安全软件清理时提醒）
            "chat_background": "",  # 聊天背景图：空=纯色；builtin:whale=内置鲸鱼壁纸；否则为图片路径
            "chat_bg_crops": {},    # 每个背景的用户自定义取景框 {背景标识: [x,y,w,h] 归一化}
            "chat": _default_chat_data(),
        }
        self._load()
        self._normalize_pet_settings()

    def _migrate_legacy_config(self, base) -> None:
        """旧版各变体共用 %APPDATA%/dsh-pet-standalone；升级后首次运行时
        把该目录的 config.json 与 sessions/ 一次性复制到变体独立目录，
        避免用户设置与聊天会话“消失”。仅在新目录尚不存在时执行。"""
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
        old_version = int(raw.get("version", 1) or 1)
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
            "rx", "ry", "facing", "scale", "on_top", "no_move", "character",
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "mouse_through", "drag_physics", "auto_hide_fullscreen",
            "click_sound_enabled", "click_show_balance", "click_show_self_talk",
            "balance_refresh_minutes", "autostart_wanted",
            "chat_background", "chat_bg_crops",
        ):
            if key in raw and raw[key] is not None:
                self.data[key] = raw[key]
        self.data["version"] = 3

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
        self.data["self_talk_enabled"] = bool(self.data.get("self_talk_enabled", False))
        self.data["self_talk_texts"] = _clean_self_talk_texts(self.data.get("self_talk_texts"))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        if key in {
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
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
