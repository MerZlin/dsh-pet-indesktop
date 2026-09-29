"""Core binding of the existing screen configuration/data grants.

This module contains compatibility schema permissions, not screen execution or UI.
"""

from pathlib import Path
from types import MappingProxyType

from . import credentials
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
    chat = document.get("chat", {})
    chat = chat if isinstance(chat, dict) else {}
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

    def read_legacy(ref: str) -> str:
        chat = config.migration_source()["chat"]
        provider = chat["providers"].get(chat["active_provider"], {})
        allowed = {provider.get("api_key_ref", "provider/" + chat["active_provider"]), provider.get("vision_api_key_ref", "")}
        if ref not in allowed:
            raise CredentialError("scope_denied")
        try:
            return credentials.secure_backend().get_password("dsh-pet-standalone", ref) or ""
        except Exception:
            raise CredentialError("legacy_credential_unavailable") from None

    return config, vault or CredentialVaultPort(SCREEN_NAMESPACE, str(path.resolve()) if path else "unbound"), read_legacy


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
    from .official_features import SCREEN_OWNER

    config, vault, legacy_reader = bind_screen_configuration(cfg)
    context = FeatureHostContext(
        owner=SCREEN_OWNER,
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
