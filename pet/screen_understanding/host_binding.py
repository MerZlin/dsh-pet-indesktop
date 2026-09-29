"""Legacy constructors; the authoritative host is supplied only bound Core ports."""

from ..feature_host_bindings import bind_screen_context, manual_for

__all__ = ["bind_runtime", "bind_settings", "manual_for"]


def bind_settings(cfg):
    from features.screen_understanding.host.factory import settings_context

    return settings_context(bind_screen_context(cfg))


def bind_runtime(window, cfg):
    from features.screen_understanding.host.factory import runtime_context

    context = bind_screen_context(cfg, window=window)
    if window is None:
        # Legacy callers can inspect policy/config without a display target.
        # Supply inert presentation ports, never a fake UI or a global Config.
        from dataclasses import replace

        from ..feature_host_bindings import _bind_window

        context = replace(context, window=_bind_window(None, cfg))
    return runtime_context(context)
