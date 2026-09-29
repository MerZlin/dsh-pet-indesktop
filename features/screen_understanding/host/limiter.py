"""Automatic screen policy/state; persistence and locks belong to the host."""

from __future__ import annotations

import datetime
import time
from typing import Any, Callable

from pet.feature_ports import FeatureStateDocumentPort

from ..common.policy import effective_proactive_config


class ProactiveLimiter:
    def __init__(
        self,
        state: FeatureStateDocumentPort,
        dry_run_state: FeatureStateDocumentPort,
        cfg: dict | None,
        *,
        dry_run: bool = False,
        clock: Callable[[], float] = time.time,
        today: Callable[[], str] | None = None,
    ) -> None:
        self._state = state
        self._dry_state = dry_run_state
        self.dry_run = dry_run
        self.cfg = effective_proactive_config(cfg)
        self._clock = clock
        self._today_fn = today or (lambda: datetime.date.today().isoformat())

    @property
    def _document(self) -> FeatureStateDocumentPort:
        return self._dry_state if self.dry_run else self._state

    def _locked(self):
        return self._document.locked()

    def update_config(self, cfg: dict | None, dry_run: bool | None = None) -> None:
        self.cfg = effective_proactive_config(cfg)
        if dry_run is not None:
            self.dry_run = dry_run

    def _default_state(self) -> dict[str, Any]:
        return {
            "date": self._today_fn(),
            "count": 0,
            "last_trigger": 0.0,
            "last_request": 0.0,
            "consecutive_failures": 0,
            "paused_until_date": "",
        }

    def _load_state(self) -> dict[str, Any]:
        """读取状态，跨天自动重置，损坏自动回退。"""
        current_today = self._today_fn()
        state = self._default_state()

        try:
            state.update(self._document.read())
        except (OSError, ValueError, TypeError):
            pass

        # 规则 1：跨天重置 count 与熔断状态
        if state.get("date") != current_today:
            state["date"] = current_today
            state["count"] = 0
            state["consecutive_failures"] = 0
            state["paused_until_date"] = ""

        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            self._document.write(state)
        except OSError:
            pass

    def allow(self) -> tuple[bool, str]:
        """判定当前是否允许发起主动识屏请求（跨进程加锁，判定期间状态不被并发改写）。

        规则判定顺序（手册 §4.3）：
        1. 跨天重置（由 _load_state 处理）；
        2. paused_until_date == today -> 拒绝（当日熔断）；
        3. count >= daily_cap -> 拒绝（达到每日上限）；
        4. now - last_request < min_request_interval_seconds -> 拒绝（请求间隔过短）；
        5. now - last_trigger < cooldown_minutes * 60 -> 拒绝（冷却中）；
        6. 否则放行。

        返回: (allowed: bool, reason: str)
        """
        with self._locked():
            return self._allow_unlocked()

    def _allow_unlocked(self) -> tuple[bool, str]:
        state = self._load_state()
        now = self._clock()
        current_today = self._today_fn()

        if state.get("paused_until_date") == current_today:
            return False, "paused_by_circuit_breaker"

        daily_cap = int(self.cfg.get("daily_cap", 15))
        if int(state.get("count", 0)) >= daily_cap:
            return False, "daily_cap_reached"

        min_req_interval = float(self.cfg.get("min_request_interval_seconds", 60))
        last_req = float(state.get("last_request", 0.0))
        if (now - last_req) < min_req_interval:
            return False, "min_request_interval_cooldown"

        cooldown_sec = float(self.cfg.get("cooldown_minutes", 5)) * 60.0
        last_trig = float(state.get("last_trigger", 0.0))
        if (now - last_trig) < cooldown_sec:
            return False, "cooldown_active"

        return True, "ok"

    def try_acquire(self) -> tuple[bool, str]:
        """原子版 allow + record_attempt：判定与盖章在同一把锁内完成，
        多开实例不会同时通过判定后再互相覆盖 last_request（lost update）。"""
        with self._locked():
            ok, reason = self._allow_unlocked()
            if ok:
                state = self._load_state()
                state["last_request"] = self._clock()
                self._save_state(state)
            return ok, reason

    def record_attempt(self) -> None:
        """记录一次请求尝试（更新 last_request 时戳）。"""
        with self._locked():
            state = self._load_state()
            state["last_request"] = self._clock()
            self._save_state(state)

    def consume_budget(self) -> bool:
        """每次真实 HTTP 请求前调用：消耗一次当日请求预算。

        预算（count）按真实请求次数计费——一次触发里的多次重试各自占用额度，
        不再只记一次。返回 False 表示当日预算已耗尽，调用方应停止重试。
        """
        with self._locked():
            state = self._load_state()
            now = self._clock()
            daily_cap = int(self.cfg.get("daily_cap", 15))
            if int(state.get("count", 0)) >= daily_cap:
                return False
            state["count"] = int(state.get("count", 0)) + 1
            state["last_request"] = now
            self._save_state(state)
            return True

    def record_success(self) -> None:
        """记录一次成功的主动关怀（更新 last_trigger, last_request，清空失败计数）。

        注意：预算（count）已由 consume_budget 在每次真实 HTTP 请求前消耗，
        此处不再累加，避免一次请求被重复计费。
        """
        with self._locked():
            state = self._load_state()
            now = self._clock()
            state["last_trigger"] = now
            state["last_request"] = now
            state["consecutive_failures"] = 0
            self._save_state(state)

    def record_failure(self) -> bool:
        """记录一次请求失败。

        若连续失败次数达到 3 次，触发当日熔断（paused_until_date=today）。
        返回: 是否触发了当日熔断。
        """
        with self._locked():
            state = self._load_state()
            now = self._clock()
            state["last_request"] = now
            fails = int(state.get("consecutive_failures", 0)) + 1
            state["consecutive_failures"] = fails

            tripped = False
            if fails >= 3:
                state["paused_until_date"] = self._today_fn()
                tripped = True

            self._save_state(state)
            return tripped
