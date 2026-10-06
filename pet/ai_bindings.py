"""Core-owned AI UI routes; never import AI implementation from the Core."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, QRect
from PySide6.QtGui import QPixmap
from shiboken6 import isValid

from .feature_host_bindings import bind_ai_context
from .official_features import AI_OWNER


class AiPetView:
    """Restricted presentation facade, not the desktop query or PetWindow.

    Geometry/avatar are application-produced values. Position subscriptions and
    status are the only mutations; no capture, global Config or arbitrary call.
    """

    def __init__(self, window):
        self._window_ref = weakref.ref(window)

    def _live(self):
        window = self._window_ref()
        return window if window is not None and (not isinstance(window, QObject) or isValid(window)) else None

    def visible_content_rect(self):
        window = self._live()
        method = getattr(window, "visible_content_rect", None)
        return QRect(method() if callable(method) else window.frameGeometry()) if window else QRect()

    def frameGeometry(self):  # noqa: N802 - existing presentation ABI
        window = self._live()
        return QRect(window.frameGeometry()) if window else QRect()

    def icon_pixmap(self, size):
        window = self._live()
        method = getattr(window, "icon_pixmap", None)
        return QPixmap(method(size)) if callable(method) else QPixmap()

    def set_chat_status(self, state, text=""):
        window = self._live()
        method = getattr(window, "set_chat_status", None)
        if callable(method):
            method(state, text)

    def add_position_listener(self, callback):
        window = self._live()
        method = getattr(window, "add_position_listener", None)
        if callable(method):
            method(callback)

    def remove_position_listener(self, callback):
        window = self._live()
        method = getattr(window, "remove_position_listener", None)
        if callable(method):
            method(callback)


def runtime_for_instance(instance):
    """Runtime factory is available only via an already verified owner host."""
    host = getattr(getattr(instance, "shell", None), "feature_host", None)
    if host is None or not host.enabled(AI_OWNER):
        return None
    runtime = getattr(instance, "_ai_runtime", None)
    if runtime is None:
        runtime = host.runtime(bind_ai_context(instance.config))
        instance._ai_runtime = runtime
        callback = getattr(instance.shell, "_on_global_chat_finished", None)
        if callable(callback):
            instance._ai_finished_cleanup = runtime.subscribe_finished(callback)
    return runtime


def view_for_instance(instance):
    view = getattr(instance, "_ai_pet_view", None)
    if view is None or view._live() is not instance.win:
        view = AiPetView(instance.win)
        instance._ai_pet_view = view
    return view


class AiFileView(AiPetView):
    """Core-owned file-drop presentation, with AI configuration only."""

    def __init__(self, window, config):
        super().__init__(window)
        self.cfg = config

    def show_alert(self, text, **options):
        window = self._live()
        if window is not None:
            window.show_alert(text, **options)

    def resolve_alert(self, identity):
        window = self._live()
        if window is not None:
            window.resolve_alert(identity)

    def show_bubble(self, text, *, duration_ms=3200):
        window = self._live()
        if window is not None:
            window.show_bubble(text, duration_ms=duration_ms)


def bind_ai_window(instance, window):
    """Refresh Core callbacks from owner state; no business code discovery."""
    host = instance.shell.feature_host
    scope = f"window:{id(window)}"
    if getattr(window, "_ai_binding_cleanup", None) is not None:
        return

    def invoke(callback):
        if host.enabled(AI_OWNER) and instance.win is window:
            return callback()
        return None

    commands = {"chat": lambda: invoke(instance.open_chat), "quick_chat": lambda: invoke(instance.open_quick_chat)}

    def file_factory():
        runtime = runtime_for_instance(instance)
        return runtime.create_file_interpreter(window) if runtime is not None and runtime.accepting else None

    window._file_interpreter_factory = file_factory

    def refresh(owner, _state=None):
        if owner != AI_OWNER or not isValid(window):
            return
        enabled = host.enabled(AI_OWNER)
        window.enable_chat = enabled
        window.on_open_chat = (lambda: invoke(instance.open_chat)) if enabled else None
        window.on_open_quick_chat = (lambda: invoke(instance.open_quick_chat)) if enabled else None
        window.on_open_chat_settings = (lambda: invoke(instance.open_chat_settings)) if enabled else None
        instance._bind_optional_services(window)
        install = getattr(window, "install_file_interpreter", None)
        if callable(install):
            install()

    host.attach(AI_OWNER, scope, commands, lambda: None)
    unsubscribe = host.subscribe(refresh)

    def cleanup(*_):
        window._ai_binding_cleanup = None
        unsubscribe()
        host.detach(AI_OWNER, scope)
        dispose = getattr(instance, "_optional_service_cleanup", None)
        if instance.win is window and callable(dispose):
            dispose()

    window._ai_binding_cleanup = cleanup
    window.destroyed.connect(cleanup)
    refresh(AI_OWNER)


def build_ai_menu(menu, window, contribution_id, *, icons=True):
    host = getattr(window, "feature_host", None)
    if host is None:
        return None
    handle = host.menu(AI_OWNER, f"window:{id(window)}", contribution_id)
    if handle is None:
        return None
    try:
        result = handle.create(menu, handle, {}, icons=icons)
    except Exception:
        host.fault(AI_OWNER, "menu_factory_failed")
        return None

    def changed(owner, _state):
        if owner == AI_OWNER and not handle.active and isValid(menu):
            menu.close()
            if result is not None and isValid(result):
                result.setEnabled(False)
                result.setVisible(False)

    unsubscribe = host.subscribe(changed)
    menu.destroyed.connect(unsubscribe)
    return result
