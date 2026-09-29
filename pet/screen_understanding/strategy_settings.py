"""Compatibility constructor for the feature-owned policy editor."""

from features.screen_understanding.host.strategy_settings import StrategySettings as _StrategySettings

from .host_binding import bind_settings


class StrategySettings(_StrategySettings):
    def __init__(self, config, parent=None):
        super().__init__(bind_settings(config), parent)
