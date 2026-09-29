"""Legacy path constructor for feature-owned metadata memory."""

import time
from pathlib import Path
from typing import Callable

from features.screen_understanding.host.memory import ProactiveMemory as _Memory

from .feature_data import bind_feature_document


class ProactiveMemory(_Memory):
    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time, max_entries: int = 20) -> None:
        self.path = Path(path)
        super().__init__(bind_feature_document(self.path), clock=clock, max_entries=max_entries)
