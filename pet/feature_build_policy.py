"""Build-owned policy. Validation builds replace this module in their own tree.

No public trust key is inferred from packages, JSON state or environment. Empty
anchors/probe digest deliberately prevent production installation until release
engineering supplies the official values. No private key belongs in Core.
"""

OFFICIAL_FEATURE_TRUST_ANCHORS: tuple[tuple[str, str], ...] = ()
FEATURE_API_VERSION = "1"
FEATURE_CAPABILITIES = frozenset({"screen.capture", "network.http", "settings.contribute", "menu.contribute"})
PROBE_BUNDLE_DIRECTORY = "feature-probe"
PROBE_BUNDLE_MANIFEST_SHA256: str | None = None
VALIDATION_BUILD = False
