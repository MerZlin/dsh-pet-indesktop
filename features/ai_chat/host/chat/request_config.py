"""Resolve once before mutating user input/session; legacy hosts remain supported."""

from pet.credentials import CredentialError


def prepare_request(config, operation="chat.send"):
    settings = config.chat_settings()
    provider = settings.active_config
    resolver = getattr(config, "request_config", None)
    if resolver is not None:
        provider = resolver(provider, operation=operation)
    else:
        from dataclasses import replace

        provider = replace(provider, api_key=config.resolve_api_key(provider))
    if getattr(getattr(config, "context", None), "api", None) is not None and not provider.model.strip():
        raise ValueError("api_model_required")
    if not provider.api_key:
        raise CredentialError("credential_missing")
    return settings, provider


def configuration_hint(error):
    reasons = {
        "api_use_not_authorized": "请打开 API 设置，填写主 Key 并保存。",
        "execution_not_authorized": "AI 功能已停用，请启用后重试。",
        "credential_missing": "尚未保存 API Key，请打开 API 设置。",
        "backend_unavailable": "系统安全存储不可用；不会回退明文 Key。",
        "credential_read_failed": "无法读取系统安全存储，请检查后重试。",
        "api_model_required": "尚未设置文字模型，请打开 API 设置。",
        "api_configuration_changed": "API 配置刚发生变化，请重新发送。",
    }
    return reasons.get(str(error), "API 配置不可用，请打开 API 设置检查。")
