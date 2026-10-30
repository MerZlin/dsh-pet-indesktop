"""动画分类与播放策略（纯逻辑）。

分类只由 ``AnimationSpec`` 的语义字段决定，不再做「排除法」：

* ``GraphType`` 决定大类；
* ``MOVE`` 再按 ``locate`` 拆成「原地转向」与「移动」；
* ``EVENT`` 单独一类，永不进随机动作池。

策略表达用户的两类偏好：

* ``auto_off``：不参与自动播放（待机圈末掷骰、点击/拖拽/摸头等自动入口），
  但仍可从右键菜单手动播放 —— 即「仅手动」；
* ``blocked``：完全不允许播放（自动与菜单都拦）。

条目既可以是分类 id（``idle``/``random``/...），也可以是动画名。名字在切换
角色后仍然保留，不存在的名字自然不命中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..contracts.content import AnimationSpec
from ..contracts.enums import GraphType

CATEGORY_IDLE = "idle"
CATEGORY_TURN = "turn"
CATEGORY_MOVE = "move"
CATEGORY_RANDOM = "random"
CATEGORY_CLICK = "click"
CATEGORY_DRAG = "drag"
CATEGORY_TOUCH_HEAD = "touch_head"
CATEGORY_SAY = "say"
CATEGORY_SLEEP = "sleep"
CATEGORY_STARTUP = "startup"
CATEGORY_WORK = "work"
CATEGORY_EVENT = "event"
CATEGORY_OTHER = "other"

#: 设置页/菜单的固定展示顺序与中文名。
CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    (CATEGORY_IDLE, "待机"),
    (CATEGORY_TURN, "原地转向"),
    (CATEGORY_MOVE, "移动"),
    (CATEGORY_RANDOM, "随机动作"),
    (CATEGORY_CLICK, "点击回应"),
    (CATEGORY_DRAG, "拖拽/悬起"),
    (CATEGORY_TOUCH_HEAD, "摸头"),
    (CATEGORY_SAY, "说话"),
    (CATEGORY_SLEEP, "睡觉"),
    (CATEGORY_STARTUP, "启动"),
    (CATEGORY_WORK, "工作"),
    (CATEGORY_EVENT, "事件"),
    (CATEGORY_OTHER, "其他"),
)

CATEGORY_LABELS: dict[str, str] = dict(CATEGORY_ORDER)
CATEGORY_IDS: frozenset[str] = frozenset(CATEGORY_LABELS)


def category_of(spec: AnimationSpec) -> str:
    """把一个动画归到唯一的展示分类。"""
    graph = spec.graph
    if graph is GraphType.IDEL:
        return CATEGORY_IDLE
    if graph is GraphType.MOVE:
        return CATEGORY_TURN if spec.locate == "in_place" else CATEGORY_MOVE
    if graph is GraphType.DEFAULT:
        return CATEGORY_RANDOM
    if graph is GraphType.TOUCH_BODY:
        return CATEGORY_CLICK
    if graph in (GraphType.RAISED_STATIC, GraphType.RAISED_DYNAMIC):
        return CATEGORY_DRAG
    if graph is GraphType.TOUCH_HEAD:
        return CATEGORY_TOUCH_HEAD
    if graph is GraphType.EVENT:
        return CATEGORY_EVENT
    if graph is GraphType.SAY:
        return CATEGORY_SAY
    if graph is GraphType.SLEEP:
        return CATEGORY_SLEEP
    if graph is GraphType.STARTUP:
        return CATEGORY_STARTUP
    if graph is GraphType.WORK:
        return CATEGORY_WORK
    return CATEGORY_OTHER


def _clean_tokens(value: Iterable[object]) -> frozenset[str]:
    tokens: list[str] = []
    for item in value or ():
        text = str(item or "").strip()
        if text and text not in tokens:
            tokens.append(text)
    return frozenset(tokens)


@dataclass(frozen=True, slots=True)
class AnimationPolicy:
    """一次播放决策的只读快照；调用方每次掷骰/展开菜单时现取。"""

    auto_off: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()
    random_auto: bool = True

    def _hit(self, spec: AnimationSpec, tokens: frozenset[str]) -> bool:
        return bool(tokens) and (spec.name in tokens or category_of(spec) in tokens)

    def auto_allowed(self, spec: AnimationSpec) -> bool:
        if self._hit(spec, self.blocked):
            return False
        if self._hit(spec, self.auto_off):
            return False
        if category_of(spec) is CATEGORY_RANDOM and not self.random_auto:
            return False
        return True

    def manual_allowed(self, spec: AnimationSpec) -> bool:
        return not self._hit(spec, self.blocked)

    def filter_auto(self, specs: Iterable[AnimationSpec]) -> tuple[AnimationSpec, ...]:
        return tuple(spec for spec in specs if self.auto_allowed(spec))

    def filter_manual(self, specs: Iterable[AnimationSpec]) -> tuple[AnimationSpec, ...]:
        return tuple(spec for spec in specs if self.manual_allowed(spec))


def policy_from_values(
    auto_off: Iterable[object] = (),
    blocked: Iterable[object] = (),
    random_auto: object = True,
) -> AnimationPolicy:
    return AnimationPolicy(
        auto_off=_clean_tokens(auto_off),
        blocked=_clean_tokens(blocked),
        random_auto=bool(random_auto),
    )
