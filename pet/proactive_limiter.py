"""Legacy path-based constructor; policy has one implementation in the feature."""

import time
from pathlib import Path
from typing import Callable

from features.screen_understanding.common.policy import (
    DEFAULT_PROACTIVE_CONFIG as DEFAULT_PROACTIVE_CONFIG,
)
from features.screen_understanding.common.policy import (
    PRESET_DEFAULTS as PRESET_DEFAULTS,
)
from features.screen_understanding.common.policy import (
    _clamp as _clamp,
)
from features.screen_understanding.common.policy import (
    _clamp_int as _clamp_int,
)
from features.screen_understanding.common.policy import (
    effective_proactive_config as effective_proactive_config,
)
from features.screen_understanding.host.limiter import ProactiveLimiter as _Limiter

from .feature_data import bind_feature_state_document


class ProactiveLimiter(_Limiter):
    def __init__(
        self, state_path: Path | str, cfg: dict | None, *, dry_run: bool = False, clock: Callable[[], float] = time.time, today: Callable[[], str] | None = None
    ) -> None:
        self.raw_state_path = Path(state_path)
        super().__init__(
            bind_feature_state_document(self.raw_state_path),
            bind_feature_state_document(self.raw_state_path.with_name("proactive_screen_dryrun_state.json")),
            cfg,
            dry_run=dry_run,
            clock=clock,
            today=today,
        )

    @property
    def state_path(self) -> Path:
        return self.raw_state_path.with_name("proactive_screen_dryrun_state.json") if self.dry_run else self.raw_state_path
