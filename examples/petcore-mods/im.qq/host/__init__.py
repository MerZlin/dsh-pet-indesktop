"""im.qq 入口：OneBot v11 适配（HTTP 事件 + HTTP API）。"""

from __future__ import annotations

from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .runtime import OWNER, QqRuntime
from .settings_page import QqSettingsPage

__all__ = ["QqRuntime", "QqSettingsPage", "create_host"]


def create_host(context):
    return FeatureDefinition(
        owner=OWNER,
        contributions=(
            Contribution(
                kind="settings_page",
                id="qq",
                data={"title": "QQ 互通", "order": 690, "icon": "chat"},
            ),
        ),
        runtime_factory=QqRuntime,
        settings_factory=QqSettingsPage,
    )
