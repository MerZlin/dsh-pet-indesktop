"""Compatibility module alias. Implementation ships with the screen feature."""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Static tooling sees the same public entry point as the runtime module alias.
    from features.screen_understanding.worker.runtime import run_proactive_screen_worker as run_proactive_screen_worker

_implementation = import_module("features.screen_understanding.worker.runtime")
if __name__ == "__main__":
    raise SystemExit(_implementation.run_proactive_screen_worker())
sys.modules[__name__] = _implementation
