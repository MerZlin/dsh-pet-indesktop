from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from petcore.config import Config
from petcore.contracts.content import AnimationSpec
from petcore.engine.animation_policy import (
    CATEGORY_CLICK,
    CATEGORY_DRAG,
    CATEGORY_EVENT,
    CATEGORY_IDLE,
    CATEGORY_MOVE,
    CATEGORY_RANDOM,
    CATEGORY_TOUCH_HEAD,
    CATEGORY_TURN,
    AnimationPolicy,
    category_of,
    policy_from_values,
)
from petcore.engine.behavior import KIND_ACT, BehaviorDirector
from petcore.engine.catalog import GraphCatalog
from petcore.engine.enums import AnimatType, GraphType


def _spec(
    name: str,
    graph: GraphType,
    animat: AnimatType,
    *,
    locate: str = "ground",
    event_group: str = "",
) -> AnimationSpec:
    return AnimationSpec(
        name=name,
        graph=graph,
        animat=animat,
        locate=locate,
        event_group=event_group,
    )


def _specs() -> list[AnimationSpec]:
    return [
        _spec("idle_a", GraphType.IDEL, AnimatType.B_LOOP),
        _spec("turn_left", GraphType.MOVE, AnimatType.B_LOOP, locate="in_place"),
        _spec("walk", GraphType.MOVE, AnimatType.B_LOOP),
        _spec("wave", GraphType.DEFAULT, AnimatType.SINGLE),
        _spec("dance", GraphType.DEFAULT, AnimatType.SINGLE),
        _spec("poke", GraphType.TOUCH_BODY, AnimatType.SINGLE),
        _spec("lift", GraphType.RAISED_STATIC, AnimatType.SINGLE),
        _spec("pat", GraphType.TOUCH_HEAD, AnimatType.SINGLE),
        _spec(
            "余额-钱袋满溢",
            GraphType.EVENT,
            AnimatType.SINGLE,
            event_group="balance",
        ),
    ]


def _director(policy: AnimationPolicy | None = None, seed: int = 0) -> BehaviorDirector:
    catalog = GraphCatalog(_specs(), rng=random.Random(seed))
    return BehaviorDirector(
        catalog,
        rng=random.Random(seed),
        policy_provider=(lambda: policy) if policy is not None else None,
    )


# ---------------------------------------------------------------- 分类


def test_categories_follow_graph_and_locate() -> None:
    specs = {spec.name: spec for spec in _specs()}
    assert category_of(specs["idle_a"]) == CATEGORY_IDLE
    assert category_of(specs["turn_left"]) == CATEGORY_TURN
    assert category_of(specs["walk"]) == CATEGORY_MOVE
    assert category_of(specs["wave"]) == CATEGORY_RANDOM
    assert category_of(specs["poke"]) == CATEGORY_CLICK
    assert category_of(specs["lift"]) == CATEGORY_DRAG
    assert category_of(specs["pat"]) == CATEGORY_TOUCH_HEAD
    assert category_of(specs["余额-钱袋满溢"]) == CATEGORY_EVENT


# ---------------------------------------------------------------- 策略


def test_policy_category_and_name_tokens() -> None:
    specs = {spec.name: spec for spec in _specs()}
    policy = policy_from_values(auto_off=["random", "walk"], blocked=["wave"])

    assert policy.auto_allowed(specs["dance"]) is False  # 分类命中
    assert policy.auto_allowed(specs["walk"]) is False  # 名字命中
    assert policy.auto_allowed(specs["idle_a"]) is True
    assert policy.manual_allowed(specs["wave"]) is False
    assert policy.manual_allowed(specs["dance"]) is True


def test_policy_random_switch_is_separate_from_manual() -> None:
    specs = {spec.name: spec for spec in _specs()}
    policy = policy_from_values(random_auto=False)
    assert policy.auto_allowed(specs["wave"]) is False
    assert policy.manual_allowed(specs["wave"]) is True


def test_policy_empty_tokens_are_ignored() -> None:
    policy = policy_from_values(auto_off=["", "  "], blocked=None, random_auto=True)
    assert policy.auto_off == frozenset()
    assert policy.blocked == frozenset()


# ---------------------------------------------------------------- BehaviorDirector


def test_director_act_pool_excludes_auto_off_names() -> None:
    policy = policy_from_values(auto_off=["wave"])
    director = _director(policy)
    names = {director.act().spec.name for _ in range(50)}
    assert names == {"dance"}


def test_director_act_returns_none_when_every_act_is_off() -> None:
    director = _director(policy_from_values(auto_off=["random"]))
    assert director.act() is None


def test_director_idle_respects_blocked_without_falling_back() -> None:
    director = _director(policy_from_values(blocked=["idle"]))
    assert director.idle() is None


def test_director_random_switch_never_picks_act() -> None:
    director = _director(policy_from_values(random_auto=False), seed=3)
    kinds = set()
    for _ in range(500):
        action = director.choose()
        assert action is not None
        kinds.add(action.kind)
    assert KIND_ACT not in kinds


# ---------------------------------------------------------------- 配置


def test_config_normalizes_animation_policy_keys(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        {
            "anim_random_auto": False,
            "anim_auto_off": ["random", "wave", "wave", "", 3],
            "anim_blocked": "not-a-list",
        },
    )
    assert config.get("anim_random_auto") is False
    assert config.get("anim_auto_off") == ["random", "wave"]
    assert config.get("anim_blocked") == []


# ---------------------------------------------------------------- 菜单 / 设置页


def test_menu_visible_names_hides_blocked_animations() -> None:
    from petcore.ui.menu_actions import _visible_names

    config = Config(
        Path("."),
        {"anim_auto_off": ["random"], "anim_blocked": ["dance"]},
    )
    window = SimpleNamespace(config=config, catalog=GraphCatalog(_specs()))
    visible = _visible_names(window, ["wave", "dance", "unknown"])
    assert visible == ["wave", "unknown"]


def test_interaction_page_animation_tree_writes_and_discards(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.page_interaction import InteractionPage

    app = QApplication.instance() or QApplication([])

    class Host:
        def __init__(self, root: Path):
            self.config = Config(root)
            self.window = SimpleNamespace(catalog=GraphCatalog(_specs()))

    host = Host(tmp_path)
    page = InteractionPage(host)
    try:
        assert page.dirty() is False
        assert page.random_auto.isChecked() is True
        assert page._weights["anim_weight_act"].isEnabled() is True
        categories = {
            page.anim_tree.topLevelItem(index).text(0)
            for index in range(page.anim_tree.topLevelItemCount())
        }
        assert "随机动作" in categories

        page.random_auto.setChecked(False)
        assert host.config.get("anim_random_auto") is False
        assert page._weights["anim_weight_act"].isEnabled() is False
        assert page.dirty() is True

        page._set_all_animation_checks(False, True)
        assert "wave" in (host.config.get("anim_auto_off") or [])
        assert host.config.get("anim_blocked") == []
        assert page.dirty() is True

        page.discard_changes()
        assert host.config.get("anim_random_auto") is True
        assert host.config.get("anim_auto_off") == []
        assert page.random_auto.isChecked() is True
        assert page.dirty() is False
        assert page._weights["anim_weight_act"].isEnabled() is True
    finally:
        page.deleteLater()
        app.processEvents()


def test_interaction_page_preserves_category_tokens_on_unrelated_changes(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.page_interaction import InteractionPage

    app = QApplication.instance() or QApplication([])

    class Host:
        def __init__(self, root: Path):
            self.config = Config(root, {"anim_auto_off": ["random"]})
            self.window = SimpleNamespace(catalog=GraphCatalog(_specs()))

    page = InteractionPage(Host(tmp_path))
    try:
        assert page.dirty() is False
        page.playback_speed.setValue(1.5)
        assert page.config().get("anim_auto_off") == ["random"]

        page._set_all_animation_checks(False, True)
        assert "wave" in page.config().get("anim_auto_off")
        page.discard_changes()
        assert page.config().get("anim_auto_off") == ["random"]
        assert page.dirty() is False
    finally:
        page.deleteLater()
        app.processEvents()


def test_settings_search_finds_animation_tree_items(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from petcore.ui.settings.base import page_search_texts, reveal_text
    from petcore.ui.settings.page_interaction import InteractionPage

    app = QApplication.instance() or QApplication([])

    class Host:
        def __init__(self, root: Path):
            self.config = Config(root)
            self.window = SimpleNamespace(catalog=GraphCatalog(_specs()))

    page = InteractionPage(Host(tmp_path))
    try:
        texts = page_search_texts(page)
        assert "随机动作" in texts
        assert "wave" in texts
        assert reveal_text(page, "wave") is True
        app.processEvents()
        assert page.anim_tree.currentItem() is not None
    finally:
        page.deleteLater()
        app.processEvents()
