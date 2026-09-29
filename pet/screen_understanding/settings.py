"""Compatibility constructor; screen UI is owned by the feature."""

from features.screen_understanding.host.settings import ScreenSettingsPage as _ScreenSettingsPage

from .config import bind_vision_config


class ScreenSettingsPage(_ScreenSettingsPage):
    def __init__(self, config, parent=None):
        super().__init__(bind_vision_config(config), parent)
