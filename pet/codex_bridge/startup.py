"""Offline startup metadata and executable discovery; never infer at startup."""

import os
from pathlib import Path
import re
import shutil


def resolve_executable(configured=None):
    if configured and Path(configured).is_file():
        return str(configured)
    command = shutil.which("codex")
    if command and Path(command).is_file():
        return command
    # Desktop updates replace the hashed bin directory. GUI/autostart PATH can
    # still point at the removed version, unlike a newly opened terminal.
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        candidates = list((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        candidates = [path for path in candidates if path.is_file()]
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return str(configured or "")


def saved_models(config):
    """Saved metadata names a model; it never asserts account entitlement."""
    raw = config.get("modelCatalog")
    models = [dict(item) for item in raw if isinstance(item, dict) and item.get("model")] if isinstance(raw, list) else []
    selected = config.get("model")
    if selected and not any(item["model"] == selected for item in models):
        models.append({"model": selected, "isDefault": True, "supportedReasoningEfforts": [{"reasoningEffort": "low"}], "unverified": True})
    return models


class UsageLimitError(RuntimeError):
    """An inference failure explicitly tagged by the Codex service."""


def chat_error(error):
    if isinstance(error, UsageLimitError) or re.search(r"UsageLimitExceeded|usage[_ ]limit|out of usage|quota.{0,30}(?:exceed|exhaust)|(?:exceed|exhaust).{0,30}quota", str(error), re.I):
        return 429, {"error": {"message": "Codex 聊天額度已用完，請等額度重置；桌寵與音樂仍可使用。", "type": "usage_limit_exceeded"}}
    if isinstance(error, FileNotFoundError):
        return 503, {"error": {"message": "暫時找不到 Codex，請開啟或更新 Codex；桌寵仍可使用。", "type": "codex_unavailable"}}
    return (400 if isinstance(error, ValueError) else 502), {"error": {"message": str(error), "type": "codex_bridge_error"}}
