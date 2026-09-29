"""Compatibility constructor; authoritative UI receives only bounded ports."""

from features.screen_understanding.host.contribution_settings import ScreenContributionSettings as _ScreenContributionSettings

from .host_binding import bind_settings


class ScreenContributionSettings(_ScreenContributionSettings):
    def __init__(self, config, parent=None):
        super().__init__(bind_settings(config), parent)
