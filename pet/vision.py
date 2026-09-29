"""Compatibility module alias. Implementation ships with the screen feature."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("features.screen_understanding.worker.vision")
