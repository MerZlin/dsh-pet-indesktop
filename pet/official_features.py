"""Core-owned closed official registry; neither directories nor manifests add owners."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

SCREEN_FEATURE_ID = "official.screen-understanding"
AI_FEATURE_ID = "official.ai-chat"


@dataclass(frozen=True)
class OfficialFeature:
    id: str
    factory: str
    execution_kind: str
    capabilities: frozenset[str]


OFFICIAL_FEATURES = MappingProxyType(
    {
        SCREEN_FEATURE_ID: OfficialFeature(
            SCREEN_FEATURE_ID, "screen-understanding/v1", "host-worker", frozenset({"screen.capture", "network.http", "settings.contribute", "menu.contribute"})
        ),
        AI_FEATURE_ID: OfficialFeature(
            AI_FEATURE_ID,
            "ai-chat/v1",
            "host-only",
            frozenset({"network.http", "settings.contribute", "menu.contribute", "chat.contribute", "files.user-selected.read"}),
        ),
    }
)


def official_feature(feature_id: str) -> OfficialFeature:
    if not isinstance(feature_id, str) or feature_id not in OFFICIAL_FEATURES:
        raise ValueError("unsupported official feature")
    return OFFICIAL_FEATURES[feature_id]


# Retained public API for existing built-in distributions and screen bindings.
SCREEN_OWNER = SCREEN_FEATURE_ID
AI_OWNER = AI_FEATURE_ID


def default_feature_host(registry=None):
    from . import feature_distribution
    from .plugins.feature_host import FeatureHost

    host = FeatureHost(registry)
    if feature_distribution.BUILTIN_SCREEN:
        from features.screen_understanding.host.factory import create_host

        host.provide(create_host())
    return host
