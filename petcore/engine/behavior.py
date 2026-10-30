from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Sequence

from ..contracts.content import AnimationSpec
from .animation_policy import AnimationPolicy
from .catalog import GraphCatalog
from .enums import AnimatType, GraphType

KIND_IDLE = "idle"
KIND_TURN = "turn"
KIND_ACT = "act"
KIND_MOVE = "move"

#: 待机 / 转向 / 随机动作 / 漫游 的默认权重（主分支 30/10/40/20 的累计阈值口径）。
DEFAULT_WEIGHTS: tuple[float, float, float, float] = (0.30, 0.10, 0.40, 0.20)

Weights = tuple[float, float, float, float]


def normalize_weights(idle, turn, act, move) -> Weights:
    """四项权重归一化：负数/NaN/非数字都当 0；全 0 回默认。"""
    values: list[float] = []
    for value in (idle, turn, act, move):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = 0.0
        if number != number or number < 0.0:
            number = 0.0
        values.append(number)
    total = sum(values)
    if total <= 0.0:
        return DEFAULT_WEIGHTS
    return tuple(value / total for value in values)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class Action:
    kind: str
    spec: AnimationSpec


class BehaviorDirector:
    def __init__(
        self,
        catalog: GraphCatalog,
        rng: random.Random | None = None,
        *,
        weights: Sequence[float] | None = None,
        weights_provider: Callable[[], Sequence[float]] | None = None,
        policy_provider: Callable[[], AnimationPolicy] | None = None,
    ):
        self.catalog = catalog
        self.rng = rng or catalog.rng
        self._weights: Weights = (
            normalize_weights(*weights) if weights is not None else DEFAULT_WEIGHTS
        )
        self._weights_provider = weights_provider
        self._policy = AnimationPolicy()
        self._policy_provider = policy_provider

    @property
    def weights(self) -> Weights:
        provider = self._weights_provider
        if provider is None:
            return self._weights
        try:
            values = tuple(provider())
        except Exception:
            return DEFAULT_WEIGHTS
        if len(values) != 4:
            return DEFAULT_WEIGHTS
        return normalize_weights(*values)

    @property
    def policy(self) -> AnimationPolicy:
        provider = self._policy_provider
        if provider is None:
            return self._policy
        try:
            policy = provider()
        except Exception:
            return AnimationPolicy()
        return policy if isinstance(policy, AnimationPolicy) else AnimationPolicy()

    def set_weights(self, weights: Sequence[float]) -> None:
        self._weights = normalize_weights(*tuple(weights)[:4])
        self._weights_provider = None

    def _auto_pool(self, specs):
        """按策略过滤自动候选；被禁用/仅手动的动画不允许回退播放。"""
        return [spec for spec in specs if self.policy.auto_allowed(spec)]

    def idle(self) -> Action | None:
        specs = [
            spec
            for spec in self.catalog.specs_for(GraphType.IDEL)
            if spec.animat is AnimatType.B_LOOP
        ]
        pool = self._auto_pool(specs)
        if not pool:
            return None
        return Action(KIND_IDLE, self.rng.choice(pool))

    def turn(self) -> Action | None:
        specs = [
            spec
            for spec in self.catalog.specs_for(GraphType.MOVE)
            if spec.locate == "in_place"
        ]
        pool = self._auto_pool(specs)
        if not pool:
            return None
        return Action(KIND_TURN, self.rng.choice(pool))

    def act(self) -> Action | None:
        specs = [
            spec
            for spec in self.catalog.specs_for(GraphType.DEFAULT)
            if spec.animat is AnimatType.SINGLE
        ]
        pool = self._auto_pool(specs)
        if not pool:
            return None
        return Action(KIND_ACT, self.rng.choice(pool))

    def move(self) -> Action | None:
        specs = [
            spec
            for spec in self.catalog.specs_for(GraphType.MOVE)
            if spec.locate != "in_place"
        ]
        pool = self._auto_pool(specs)
        if not pool:
            return None
        return Action(KIND_MOVE, self.rng.choice(pool))

    def choose(self) -> Action | None:
        idle, turn, act, move = self.weights
        if not self.policy.random_auto:
            act = 0.0
        values = [idle, turn, act, move]
        total = sum(values)
        if total <= 0.0:
            # 四项全 0：保留待机，别让桌宠冻住；随机动作仍按开关关掉。
            values = list(DEFAULT_WEIGHTS)
            if not self.policy.random_auto:
                values[2] = 0.0
            total = sum(values)
        idle, turn, act, move = (value / total for value in values)
        roll = self.rng.random()
        p_turn = idle + turn
        p_act = p_turn + act
        if roll < idle:
            order = (self.idle, self.turn, self.act, self.move)
        elif roll < p_turn:
            order = (self.turn, self.idle, self.act, self.move)
        elif roll < p_act:
            order = (self.act, self.idle, self.turn, self.move)
        else:
            order = (self.move, self.idle, self.turn, self.act)
        for factory in order:
            action = factory()
            if action is not None:
                return action
        return None
