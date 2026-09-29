"""Compatibility module; official feature owns migration semantics."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("features.screen_understanding.host.migration")
