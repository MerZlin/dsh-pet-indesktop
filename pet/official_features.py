"""Feature registration models and the closed official registry.

The official registry remains the Core-owned source for built-in/default packages
and formal signed release policy.  Local user-trusted packages use the same
neutral :class:`FeatureRegistration` shape, but their registration is derived
from a bounded manifest rather than discovered from this closed mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

SCREEN_FEATURE_ID = "official.screen-understanding"
AI_FEATURE_ID = "official.ai-chat"

_FEATURE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_FACTORY_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}(?:/[A-Za-z0-9_.-]{1,128})?\Z")
_EXECUTION_KINDS = frozenset({"host-only", "host-worker"})


@dataclass(frozen=True)
class FeatureRegistration:
    """Neutral package registration used by official and local package paths."""

    id: str
    factory: str
    execution_kind: str
    capabilities: frozenset[str]

    def __post_init__(self):
        if not is_valid_feature_id(self.id) or not isinstance(self.factory, str) or not _FACTORY_ID.fullmatch(self.factory):
            raise ValueError("invalid feature registration")
        if self.execution_kind not in _EXECUTION_KINDS:
            raise ValueError("invalid feature execution kind")
        capabilities = frozenset(self.capabilities)
        if any(not isinstance(capability, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}\Z", capability) for capability in capabilities):
            raise ValueError("invalid feature capabilities")
        object.__setattr__(self, "capabilities", capabilities)


# Compatibility name retained for existing official-only callers.
OfficialFeature = FeatureRegistration


def is_valid_feature_id(feature_id: object) -> bool:
    return isinstance(feature_id, str) and bool(_FEATURE_ID.fullmatch(feature_id)) and ".." not in feature_id


def is_valid_factory_id(factory: object) -> bool:
    return isinstance(factory, str) and bool(_FACTORY_ID.fullmatch(factory))


OFFICIAL_FEATURES = MappingProxyType(
    {
        SCREEN_FEATURE_ID: FeatureRegistration(
            SCREEN_FEATURE_ID, "screen-understanding/v1", "host-worker", frozenset({"screen.capture", "network.http", "settings.contribute", "menu.contribute"})
        ),
        AI_FEATURE_ID: FeatureRegistration(
            AI_FEATURE_ID,
            "ai-chat/v1",
            "host-only",
            frozenset({"network.http", "settings.contribute", "menu.contribute", "chat.contribute", "files.user-selected.read"}),
        ),
    }
)


def official_feature(feature_id: str) -> FeatureRegistration:
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
