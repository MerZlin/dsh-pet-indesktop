"""Build-owned policy. Validation builds replace this module in their own tree.

Phase5A uses explicit local activation: the user selects a ZIP/folder in
Setup or the extension-management page, and Core validates its bounded local
package structure/inventory without publisher keys. Formal release signing is
out of scope for this path and must not block local activation. No private key
belongs in Core.
"""

OFFICIAL_FEATURE_TRUST_ANCHORS: tuple[tuple[str, str], ...] = ()
OFFICIAL_FEATURE_KEY_POLICIES: tuple[tuple[str, dict], ...] = ()
ALLOW_LOCAL_PACKAGE_ACTIVATION = True
FEATURE_API_VERSION = "1"
FEATURE_CAPABILITIES = frozenset({"screen.capture", "network.http", "settings.contribute", "menu.contribute", "chat.contribute", "files.user-selected.read"})
PROBE_BUNDLE_DIRECTORY = "feature-probe"
PROBE_BUNDLE_MANIFEST_SHA256: str | None = None
VALIDATION_BUILD = False
