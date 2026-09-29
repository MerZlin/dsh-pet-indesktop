"""Pure screen policy defaults and normalization; no GUI or filesystem imports."""

from typing import Any

# 合法参数范围与默认值常量定义（依据实施手册 §2 与 §3）
PRESET_DEFAULTS: dict[str, dict[str, int]] = {
    "quiet": {"dwell_seconds": 90, "cooldown_minutes": 10, "daily_cap": 8},
    "balanced": {"dwell_seconds": 45, "cooldown_minutes": 5, "daily_cap": 15},
    "active": {"dwell_seconds": 20, "cooldown_minutes": 3, "daily_cap": 25},
}

DEFAULT_PROACTIVE_CONFIG: dict[str, Any] = {
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


def _clamp(val: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, num))


def _clamp_int(val: Any, default: int, minimum: int, maximum: int) -> int:
    return round(_clamp(val, default, minimum, maximum))


def effective_proactive_config(raw: dict | None) -> dict[str, Any]:
    """计算主动识屏的有效运行时配置。

    - 以 DEFAULT_PROACTIVE_CONFIG 为基础；
    - 根据 preset 填充 dwell_seconds、cooldown_minutes、daily_cap；
    - 合并用户 raw 字典中的自定义配置；
    - 所有数值 clamp 到手册 §2 合法范围；
    - require_idle 为 False 时，effective 配置中 min_idle_seconds 视为 0（保留原始键不变）；
    - 非法 preset 回退为 'balanced'。
    """
    result = dict(DEFAULT_PROACTIVE_CONFIG)
    raw = raw if isinstance(raw, dict) else {}

    preset = str(raw.get("preset", result["preset"])).strip().lower()
    if preset not in PRESET_DEFAULTS and preset != "custom":
        preset = "balanced"
    result["preset"] = preset

    # 预设覆盖三项（custom 不覆盖）
    if preset in PRESET_DEFAULTS:
        result.update(PRESET_DEFAULTS[preset])

    # 用户手动配置项覆盖
    for k, v in raw.items():
        if k in DEFAULT_PROACTIVE_CONFIG and v is not None:
            result[k] = v

    # 确保 preset 在非法情况下已被规范化
    result["preset"] = preset

    # 规范化与范围 clamp
    result["enabled"] = bool(result.get("enabled", False))
    result["dry_run"] = bool(result.get("dry_run", False))
    result["allow_when_mouse_through"] = bool(result.get("allow_when_mouse_through", True))
    result["require_idle"] = bool(result.get("require_idle", False))
    result["prefer_free_provider"] = bool(result.get("prefer_free_provider", True))
    result["pre_cue"] = bool(result.get("pre_cue", True))

    whitelist = result.get("whitelist")
    if isinstance(whitelist, list):
        result["whitelist"] = [str(item).strip() for item in whitelist if str(item).strip()]
    else:
        result["whitelist"] = []

    # clamp 数值范围（手册 §2）
    # dwell_seconds: 15 ~ 600 (默认 45)
    # min_idle_seconds: 0 ~ 3600 (默认 30)
    # cooldown_minutes: 1 ~ 120 (默认 5)
    # daily_cap: 1 ~ 9999 (默认 15；用户自定义不设硬顶，约等于不限)
    # min_request_interval_seconds: 30 ~ 3600 (默认 60)
    # change_threshold: 0 ~ 32 (默认 8)
    result["dwell_seconds"] = _clamp_int(result.get("dwell_seconds"), 45, 15, 600)
    # cooldown 允许 0.5 分钟粒度（用户反馈整分钟太粗）
    result["cooldown_minutes"] = _clamp(result.get("cooldown_minutes"), 5.0, 0.5, 120.0)
    result["daily_cap"] = _clamp_int(result.get("daily_cap"), 15, 1, 9999)
    result["min_request_interval_seconds"] = _clamp_int(result.get("min_request_interval_seconds"), 60, 30, 3600)
    result["change_threshold"] = _clamp_int(result.get("change_threshold"), 8, 0, 32)

    raw_min_idle = _clamp_int(result.get("min_idle_seconds"), 30, 0, 3600)
    result["min_idle_seconds"] = raw_min_idle if result["require_idle"] else 0

    return result
