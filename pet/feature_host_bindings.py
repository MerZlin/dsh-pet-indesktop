"""Core binding of the existing screen configuration/data grants.

This module contains compatibility schema permissions, not screen execution or UI.
"""

from pathlib import Path
from types import MappingProxyType

from . import credentials
from .api_config import CoreApiConfiguration
from .config_transaction import SCREEN_NAMESPACE
from .credentials import CredentialError, CredentialVaultPort
from .desktop_query import get_desktop_query
from .feature_config import bind_feature_configuration, bind_feature_preferences
from .feature_data import bind_feature_document, bind_feature_state_document
from .feature_ports import FeatureHostContext, FeatureWindowPorts, FeatureWindowState

# Explicit legacy-field grant. Defaults and normalization remain feature-owned.
_SCREEN_FIELDS = frozenset(
    {
        "enabled",
        "dry_run",
        "preset",
        "allow_when_mouse_through",
        "whitelist",
        "dwell_seconds",
        "require_idle",
        "min_idle_seconds",
        "cooldown_minutes",
        "daily_cap",
        "min_request_interval_seconds",
        "change_threshold",
        "prefer_free_provider",
        "pre_cue",
    }
)

# Only fields that selected legacy vision execution actually consumed are granted.
_PROVIDER_FIELDS = frozenset(
    {
        "base_url",
        "model",
        "api_key",
        "api_key_ref",
        "vision_same_as_chat",
        "vision_base_url",
        "vision_model",
        "vision_api_key",
        "vision_api_key_ref",
        "chat_path",
        "timeout",
        "temperature",
        "max_tokens",
        "verify_ssl",
    }
)


def _migration_source(document: dict) -> dict:
    """Return the explicit, redacted migration source for screen configuration.

    The AI package owns the current chat namespace after the Phase 5 split.  The
    old top-level ``chat`` namespace remains a compatibility source for legacy
    installs, but must not win over an installed AI namespace that has a usable
    provider.  Only the small provider field allow-list is exposed to the screen
    feature; credential values are never copied into this document.
    """
    plugins = document.get("plugins", {})
    plugins = plugins if isinstance(plugins, dict) else {}
    ai_namespace = plugins.get("official.ai-chat", {})
    ai_namespace = ai_namespace if isinstance(ai_namespace, dict) else {}
    ai_chat = ai_namespace.get("chat", {})
    ai_chat = ai_chat if isinstance(ai_chat, dict) else {}

    legacy_chat = document.get("chat", {})
    legacy_chat = legacy_chat if isinstance(legacy_chat, dict) else {}

    def has_providers(value: dict) -> bool:
        providers = value.get("providers")
        return isinstance(providers, dict) and any(isinstance(item, dict) for item in providers.values())

    # A split AI install is the authoritative compatibility source.  If it is
    # absent or empty, retain the pre-split top-level behavior.
    chat = ai_chat if has_providers(ai_chat) else legacy_chat
    raw = chat.get("providers", {})
    providers = {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    active = str(chat.get("active_provider", ""))
    selected = active if active in providers else next(iter(providers), "")
    granted_providers: dict[str, dict[str, object]] = {}
    granted: dict[str, object] = {"active_provider": selected, "providers": granted_providers}
    if "default_system_prompt" in chat:
        granted["default_system_prompt"] = chat["default_system_prompt"]
    if selected in providers:
        granted_providers[selected] = {k: v for k, v in providers[selected].items() if k in _PROVIDER_FIELDS}
    return {"chat": granted, "proactive_screen": {"prefer_free_provider": document.get("proactive_screen", {}).get("prefer_free_provider", True)}}


def bind_screen_configuration(cfg, *, vault: CredentialVaultPort | None = None):
    path = Path(cfg.path) if hasattr(cfg, "path") else None
    config = bind_feature_configuration(
        cfg,
        SCREEN_NAMESPACE,
        journal_path=path.with_suffix(path.suffix + ".vision-migration.json") if path else None,
        migration_source=_migration_source,
    )

    layout = getattr(cfg, "runtime_layout", None)
    instance = str(getattr(cfg, "instance_id", "") or "primary")
    scope = layout.credential_namespace(SCREEN_NAMESPACE, instance) if layout is not None else str(path.resolve()) if path else "unbound"
    bound_vault = vault or CredentialVaultPort(SCREEN_NAMESPACE, scope)

    # The AI vault is used only by the explicit migration reader below.  It is
    # never exposed to screen execution, and an existing screen credential is
    # never replaced by this bridge.  Reuse a test backend when supplied; in
    # production the vault resolves the OS-backed secure store itself.
    ai_vault = None
    if layout is not None:
        from .official_features import AI_OWNER

        try:
            ai_scope = layout.credential_namespace(AI_OWNER, instance)
            ai_vault = CredentialVaultPort(AI_OWNER, ai_scope, backend=getattr(bound_vault, "_backend", None))
        except (AttributeError, TypeError, ValueError):
            ai_vault = None

    def read_legacy(ref: str) -> str:
        source = config.migration_source()
        chat = source["chat"]
        provider = chat["providers"].get(chat["active_provider"], {})
        api_ref = provider.get("api_key_ref", "provider/" + chat["active_provider"])
        vision_ref = provider.get("vision_api_key_ref", "")
        allowed = {value for value in (api_ref, vision_ref) if value}
        if ref not in allowed:
            raise CredentialError("scope_denied")

        # New split installs store the chat key in the AI-owned scoped vault.
        # A scope-shaped reference can only be read through that exact owner;
        # it must not fall back to the legacy service with the same string.
        if ai_vault is not None and ref.startswith(ai_vault.scope + "/"):
            endpoint = provider.get("base_url", "")
            if ref == vision_ref and provider.get("vision_base_url"):
                endpoint = provider.get("vision_base_url")
            return ai_vault.acquire(ref, chat["active_provider"], endpoint, "chat.send")

        try:
            return credentials.secure_backend().get_password("dsh-pet-standalone", ref) or ""
        except Exception:
            raise CredentialError("legacy_credential_unavailable") from None

    return config, bound_vault, read_legacy


def _bind_window(window, cfg):
    import shiboken6
    from PySide6.QtCore import QObject

    from . import catalog
    from .feature_bindings import host_for
    from .official_features import SCREEN_OWNER, default_feature_host

    host = host_for(window) if window is not None else default_feature_host()
    window_id = f"window:{id(window)}"
    instance_id = str(getattr(cfg, "instance_id", "") or "primary")

    def alive():
        return not isinstance(window, QObject) or shiboken6.isValid(window)

    def window_state():
        character = str(cfg.get("character", catalog.DEFAULT_CHARACTER))
        return FeatureWindowState(
            window_id,
            instance_id,
            character,
            cfg.character_display_name(character),
            bool(alive() and getattr(window, "isVisible", lambda: True)()),
            bool(
                alive()
                and (getattr(window, "_dragging", False) or getattr(window, "_physics_mode", None) is not None or getattr(window, "_click_effect_phase", 0) > 0)
            ),
            bool(alive() and getattr(window, "mouse_through", False)),
        )

    def enabled():
        return alive() and not getattr(window, "_closing", False) and host.enabled(SCREEN_OWNER)

    def bubble(text, duration):
        callback = getattr(window, "show_bubble", None)
        if enabled() and callable(callback):
            callback(text, duration_ms=duration)

    def hold(seconds):
        callback = getattr(window, "hold_bubble", None)
        if enabled() and callable(callback):
            callback(seconds)

    def sync(text, reply):
        callback = getattr(window, "on_look_synced", None)
        if enabled() and callable(callback):
            callback(text, reply)

    def external_text(result_id, kind, text, reply):
        if not enabled():
            return
        callback = getattr(window, "on_external_text", None)
        if callable(callback):
            callback(result_id, kind, text, reply)
        else:
            sync(text, reply)  # Existing callback-only hosts remain compatible.

    def busy(process, title):
        manager = getattr(window, "agent_link_manager", None)
        return bool(alive() and manager is not None and manager.busy_agent_owns_process(process, title))

    def lifecycle(stop, resume, dispose):
        release_execution = host.bind_execution(SCREEN_OWNER, stop, resume)
        if isinstance(window, QObject):
            window.destroyed.connect(dispose)

        def release():
            release_execution()
            if isinstance(window, QObject) and shiboken6.isValid(window):
                try:
                    window.destroyed.disconnect(dispose)
                except RuntimeError:
                    pass

        return release

    return FeatureWindowPorts(window_state, busy, bubble, hold, sync, external_text, enabled, lifecycle)


def bind_screen_context(cfg, *, window=None, host=None) -> FeatureHostContext:
    from .feature_distribution import BUILTIN_SCREEN
    from .official_features import SCREEN_OWNER

    config, vault, legacy_reader = bind_screen_configuration(cfg)
    context = FeatureHostContext(
        owner=SCREEN_OWNER,
        api=None if BUILTIN_SCREEN else CoreApiConfiguration(cfg).bind(SCREEN_OWNER, authorized=lambda: True, open_settings=lambda: _open_api_settings(cfg)),
        configuration=config,
        credentials=vault,
        legacy_secret_reader=legacy_reader,
        preferences=bind_feature_preferences(cfg, key="proactive_screen", fields=_SCREEN_FIELDS),
        documents=MappingProxyType({"memory": bind_feature_document(cfg.dir / "proactive_screen_memory.json")}),
        state_documents=MappingProxyType(
            {
                "state": bind_feature_state_document(cfg.dir / "proactive_screen_state.json"),
                "dry_state": bind_feature_state_document(cfg.dir / "proactive_screen_dryrun_state.json"),
            }
        ),
        desktop=get_desktop_query(),
        window=_bind_window(window, cfg) if window is not None else None,
    )
    return host.bind_context(context) if host is not None else context


def bind_ai_context(cfg, *, host=None):
    """Core-owned AI grants: opaque preferences and scoped storage, no desktop.

    This binding does not import AI models, UI, Providers, or start request threads.
    Actual AI lifecycle/window/document ports are added by the signed host route.
    """
    from .feature_distribution import BUILTIN_AI
    from .feature_ports import FeatureUserDataPort
    from .feature_state_io import safe_path
    from .official_features import AI_OWNER

    user_root = Path(cfg.dir) / "feature-data" / AI_OWNER
    safe_path(user_root)

    path = Path(cfg.path)
    layout = getattr(cfg, "runtime_layout", None)
    scope = layout.credential_namespace(AI_OWNER, str(getattr(cfg, "instance_id", "") or "primary")) if layout else str(path.resolve())

    def deny_legacy(ref: str) -> str:
        raise CredentialError("legacy_import_authorization_required")

    context = FeatureHostContext(
        owner=AI_OWNER,
        api=None if BUILTIN_AI else CoreApiConfiguration(cfg).bind(AI_OWNER, authorized=lambda: True, open_settings=lambda: _open_api_settings(cfg)),
        configuration=bind_feature_configuration(cfg, AI_OWNER, journal_path=path.with_suffix(".json.ai-migration.json")),
        credentials=CredentialVaultPort(AI_OWNER, scope),
        legacy_secret_reader=deny_legacy,
        preferences=bind_feature_preferences(
            cfg,
            key="chat",
            fields=frozenset(
                {
                    "enabled",
                    "active_provider",
                    "default_system_prompt",
                    "history_message_limit",
                    "history_char_limit",
                    "providers",
                }
            ),
        ),
        documents=MappingProxyType({}),
        state_documents=MappingProxyType({}),
        desktop=None,
        user_data=FeatureUserDataPort(
            user_root,
            str(getattr(cfg, "instance_id", "") or ""),
            lambda: MappingProxyType({"character": cfg.get("character"), "self_talk_bubble_style": cfg.get("self_talk_bubble_style", "classic_top")}),
            lambda character: str(cfg.character_alias(character) or ""),
        ),
    )
    return host.bind_context(context) if host is not None else context


def bind_local_context(cfg, owner: str, *, host=None) -> FeatureHostContext:
    """Bind a user-selected local package to only generic, owner-scoped ports.

    Local packages are trusted executable Python, not sandboxed code.  This
    binding deliberately exposes no desktop/window ports and grants no
    preference fields by default; package-specific capabilities remain a
    manifest/verifier concern rather than an implicit Core object leak.
    """
    from .feature_state_io import safe_path
    from .official_features import is_valid_feature_id

    if not is_valid_feature_id(owner):
        raise ValueError("invalid_feature_owner")
    root = Path(cfg.dir) / "feature-data" / owner
    safe_path(root)
    path = Path(cfg.path)
    journal = path.parent / f"{path.name}.{owner}.migration.json"
    scope = (
        cfg.runtime_layout.credential_namespace(owner, str(getattr(cfg, "instance_id", "") or "primary"))
        if getattr(cfg, "runtime_layout", None) is not None
        else str(path.resolve())
    )

    def deny_legacy(ref: str) -> str:
        raise CredentialError("legacy_import_authorization_required")

    context = FeatureHostContext(
        owner=owner,
        api=CoreApiConfiguration(cfg).bind(owner, authorized=lambda: True, open_settings=lambda: _open_api_settings(cfg)),
        configuration=bind_feature_configuration(cfg, owner, journal_path=journal),
        credentials=CredentialVaultPort(owner, scope),
        legacy_secret_reader=deny_legacy,
        preferences=bind_feature_preferences(cfg, key=f"feature_preferences.{owner}", fields=frozenset()),
        documents=MappingProxyType({"memory": bind_feature_document(root / "memory.json")}),
        state_documents=MappingProxyType({"state": bind_feature_state_document(root / "state.json")}),
        desktop=None,
        window=None,
        user_data=None,
    )
    return host.bind_context(context) if host is not None else context


def runtime_for(window, cfg, *, worker_mode="auto"):
    from .feature_bindings import host_for
    from .official_features import SCREEN_OWNER

    host = host_for(window)
    if not host.enabled(SCREEN_OWNER):
        return None
    return host.runtime(bind_screen_context(cfg, window=window), worker_mode=worker_mode)


def manual_for(window):
    """Core routes to the current owner factory, without importing feature code."""
    from .feature_bindings import host_for
    from .official_features import SCREEN_OWNER

    host = host_for(window)
    if not host.enabled(SCREEN_OWNER) or getattr(window, "_closing", False):
        return None
    existing = getattr(window, "_screen_manual_host", None)
    if existing is not None:
        return existing

    def request(provider, prompt, pet_name, callback):
        watcher = window._ensure_proactive_watcher()
        return watcher.request_manual_look(provider, prompt, pet_name, callback) if watcher is not None else False

    def cancel(callback):
        watcher = getattr(window, "proactive_watcher", None)
        if watcher is not None:
            watcher.cancel_manual_look(callback)

    session = host.manual(bind_screen_context(window.cfg, window=window), request, cancel)
    window._screen_manual_host = session
    return session


def _open_api_settings(cfg):
    from .settings_api import open_api_settings

    open_api_settings(cfg)
