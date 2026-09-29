"""Core UI adapters for owner leases. No screen UI is imported by these adapters."""

from __future__ import annotations

from .official_features import SCREEN_OWNER, default_feature_host


def host_for(target):
    host = getattr(target, "feature_host", None)
    from .plugins.feature_host import FeatureHost

    if not isinstance(host, FeatureHost):
        host = default_feature_host()
        target.feature_host = host
    return host


def screen_allowed(target):
    return host_for(target).enabled(SCREEN_OWNER)


def bind_screen_window(target):
    host = host_for(target)
    scope = f"window:{id(target)}"
    if getattr(target, "_screen_contribution_bound", False) is True:
        return host, scope

    def look():
        if screen_allowed(target):
            callback = getattr(target, "on_look_screen", None)
            if callback:
                return callback()
        return None

    def automatic(key, value):
        if not screen_allowed(target):
            return
        if key == "settings":
            callback = getattr(target, "on_open_modern_settings", None) or getattr(target, "on_open_legacy_settings", None)
            if callback:
                callback()
        elif key == "enabled":
            callback = getattr(target, "toggle_proactive_enabled", None)
            if callback:
                callback(value)
        else:
            callback = getattr(target, "set_proactive_option", None)
            if callback:
                callback(key, value)

    def stop():
        target._screen_execution_epoch = getattr(target, "_screen_execution_epoch", 0) + 1
        session = getattr(target, "_screen_manual_host", None)
        if session is not None:
            session.cancel()

    host.attach(SCREEN_OWNER, scope, {"look": look, "automatic": automatic}, stop)
    target._screen_contribution_bound = True
    # Parent destruction may bypass closeEvent. Do not retain the window in this slot.
    from PySide6.QtCore import QObject

    if isinstance(target, QObject):
        target.destroyed.connect(lambda: host.detach(SCREEN_OWNER, scope))
    return host, scope


def screen_menu_available(target, contribution_id):
    host, scope = bind_screen_window(target)
    if contribution_id == "look_screen" and not callable(getattr(target, "on_look_screen", None)):
        return False
    if contribution_id == "proactive_screen" and not all(callable(getattr(target, n, None)) for n in ("toggle_proactive_enabled", "set_proactive_option")):
        return False
    return host.menu(SCREEN_OWNER, scope, contribution_id) is not None


def build_screen_menu(menu, target, contribution_id, *, icons=True):
    host, scope = bind_screen_window(target)
    if not screen_menu_available(target, contribution_id):
        return None
    handle = host.menu(SCREEN_OWNER, scope, contribution_id)
    try:
        result = handle.create(menu, handle, target.cfg.get("proactive_screen", {}), icons=icons)
    except Exception:
        host.fault(SCREEN_OWNER, "menu_factory_failed")
        return None

    # Open Qt menus are closed on revocation; old QAction leases remain inert.
    def changed(owner, _state):
        if owner == SCREEN_OWNER and not handle.active:
            menu.close()
            if result is not None:
                action = result.menuAction() if hasattr(result, "menuAction") else result
                action.setEnabled(False)
                action.setVisible(False)

    unsubscribe = host.subscribe(changed)
    menu.destroyed.connect(lambda: unsubscribe())
    return result
