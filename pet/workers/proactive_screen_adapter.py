"""Compatibility alias; the screen feature owns the transport adapter."""

import sys

from features.screen_understanding.host import worker_adapter as _implementation

sys.modules[__name__] = _implementation
