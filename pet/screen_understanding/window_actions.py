"""Legacy window entry binding; manual policy lives in the feature host."""


def start_manual_look(window) -> None:
    from ..feature_bindings import screen_allowed
    from .host_binding import manual_for

    if screen_allowed(window) and not getattr(window, "_closing", False):
        manual_for(window).start()
