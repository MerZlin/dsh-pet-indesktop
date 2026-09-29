"""Official allowlist. No dynamic entrypoints and no feature UI imports here."""

from .plugins.feature_host import FeatureHost

SCREEN_OWNER = "official.screen-understanding"


def default_feature_host(registry=None) -> FeatureHost:
    from . import feature_distribution

    host = FeatureHost(registry)
    if feature_distribution.BUILTIN_SCREEN:
        from features.screen_understanding.host.factory import create_host

        host.provide(create_host())
    return host
