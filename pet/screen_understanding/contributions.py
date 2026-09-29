"""Legacy Core binding for feature-owned descriptors. UI stays lazy."""

from dataclasses import replace

from features.screen_understanding.host.contributions import (
    OWNER as OWNER,
)
from features.screen_understanding.host.contributions import (
    automatic_menu as automatic_menu,
)
from features.screen_understanding.host.contributions import (
    definition as _definition,
)
from features.screen_understanding.host.contributions import (
    manual_menu as manual_menu,
)


def settings_factory(config, parent=None):
    from features.screen_understanding.host.contributions import settings_factory as create

    from .host_binding import bind_settings

    return create(bind_settings(config), parent)


def definition():
    return replace(_definition(), settings_factory=settings_factory)
