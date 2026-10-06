"""AI-owned legacy preference policy. Core small builds never import this module."""

from __future__ import annotations

from typing import Any, cast


def _default_file_interpret_data() -> dict:
    """拖文件解读（file_interpret）默认值；消费方 pet/file_interpret.py。"""
    return {
        # 拖文件后提供「解读」确认气泡；关闭则拖放只有吃动画，不询问
        "enabled": True,
        # 进度汇报间隔（秒），产品区间 [5,120]；PR3 增加 progress_mode（heartbeat/chunked）
        "progress_interval_seconds": 15.0,
    }


def _merge_file_interpret_data(raw: Any) -> dict:
    result = _default_file_interpret_data()
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


def reload_legacy_chat(config, raw):
    raw_chat = raw.get("chat")
    chat: dict[str, Any] = cast(dict[str, Any], raw_chat) if isinstance(raw_chat, dict) else {}
    legacy: dict[str, Any] = {}
    if "chat_enabled" in raw:
        legacy["enabled"] = raw["chat_enabled"]
    if "chat_system_prompt" in raw:
        legacy["default_system_prompt"] = raw["chat_system_prompt"]
    legacy_provider: dict[str, Any] = {}
    if raw.get("chat_api_url"):
        legacy_provider["base_url"] = raw["chat_api_url"]
    if raw.get("chat_model"):
        legacy_provider["model"] = raw["chat_model"]
    if raw.get("chat_api_key"):
        legacy_provider["api_key"] = raw["chat_api_key"]
    if legacy_provider:
        legacy["providers"] = {"openai-main": legacy_provider}
    merged: dict[str, Any] = dict(legacy)
    merged.update(chat)
    # secret 只进不出：磁盘重载不得冲掉内存中的 key。
    # _redacted_data() 写盘时会剔除 chat.providers 下的明文 api_key /
    # vision_api_key（keyring 不可用时 key 只存内存 config.data），因此磁盘文件
    # 里没有这两项。这里若某 provider 在磁盘数据里缺 api_key/vision_api_key
    # 但合入前的内存里有，则保留内存值，避免设置对话框重开（自 config.reload()
    # 从磁盘重载）把用户未重启就丢掉的 key 覆盖成空。新旧两套设置对话框都走
    # 这条 reload() 路径，一处修复全覆盖。
    previous_chat = config.data.get("chat")
    previous_providers = previous_chat.get("providers") if isinstance(previous_chat, dict) else None
    merged_chat = _merge_chat_data(merged)
    config.data["chat"] = merged_chat
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


def normalize_file_preferences(raw):
    from pet.config import _bool_or_default, _float_or_default

    if isinstance(raw, dict):
        raw["enabled"] = _bool_or_default(raw.get("enabled", True), True)
        raw["progress_interval_seconds"] = _float_or_default(raw.get("progress_interval_seconds"), 15.0, 5.0, 120.0)
