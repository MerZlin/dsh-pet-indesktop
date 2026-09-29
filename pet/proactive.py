"""Compatibility API for the feature-owned screen runtime.

Only default/source builds include this legacy binding. External screen hosts
receive ScreenRuntimeContext directly and cannot recover raw Core objects here.
"""

from features.screen_understanding.host.runtime import (
    ProactiveScreenWatcher as _Watcher,
)
from features.screen_understanding.host.runtime import (
    build_memory_context,
    build_sync_marker,
    classify_activity,
    dwell_satisfied,
    hamming_distance,
    idle_satisfied,
    image_dhash,
    match_process_whitelist,
    should_watch,
)

from .proactive_limiter import DEFAULT_PROACTIVE_CONFIG, PRESET_DEFAULTS, ProactiveLimiter, effective_proactive_config
from .proactive_memory import ProactiveMemory

__all__ = [
    "DEFAULT_PROACTIVE_CONFIG",
    "PRESET_DEFAULTS",
    "ProactiveLimiter",
    "ProactiveMemory",
    "ProactiveScreenWatcher",
    "build_memory_context",
    "build_sync_marker",
    "classify_activity",
    "dwell_satisfied",
    "hamming_distance",
    "idle_satisfied",
    "image_dhash",
    "match_process_whitelist",
    "should_watch",
    "effective_proactive_config",
]


class ProactiveScreenWatcher(_Watcher):
    def __init__(self, window, config, worker_mode="in_process"):
        from .screen_understanding.host_binding import bind_runtime

        # Retained only at the old constructor seam; canonical host has neither.
        self.win = window
        self.cfg = config
        super().__init__(bind_runtime(window, config), worker_mode)
