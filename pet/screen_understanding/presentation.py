"""Legacy configuration constructor for feature-owned presentation."""

from features.screen_understanding.host.presentation import present_manual_result as _present

from .config import bind_vision_config


def present_manual_result(config, revision, text, user_text, is_error, show_bubble, sync_text):
    return _present(bind_vision_config(config), revision, text, user_text, is_error, show_bubble, sync_text)
