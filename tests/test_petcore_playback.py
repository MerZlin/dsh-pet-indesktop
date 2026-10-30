from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.engine.behavior import Action, KIND_ACT
from petcore.engine.enums import AnimatType, GraphType, ModeType
from petcore.ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeClip:
    def __init__(self, name: str):
        self.name = name
        self.frame_count = 24
        self.duration_seconds = 2.0
        self.fps = 12.0
        self.starts = 0
        self.stops = 0
        self.callbacks: dict[str, object] = {}

    def on_frame(self, callback) -> None:
        self.callbacks["frame"] = callback

    def on_finished(self, callback) -> None:
        self.callbacks["finished"] = callback

    def on_error(self, callback) -> None:
        self.callbacks["error"] = callback

    def clear_callbacks(self) -> None:
        self.callbacks.clear()

    def start(self) -> bool:
        self.starts += 1
        return True

    def stop(self) -> None:
        self.stops += 1

    def current_image(self):
        return None

    def current_frame_number(self) -> int:
        return 0

    def set_playback_speed(self, speed: float) -> None:
        self.speed = speed

    def jump_to_frame(self, index: int) -> bool:
        return False


class FakeLibrary:
    def __init__(self) -> None:
        self.clips: dict[str, FakeClip] = {}

    def clip_for(self, spec: AnimationSpec) -> FakeClip:
        return self.clips.setdefault(spec.name, FakeClip(spec.name))

    def fallback(self) -> FakeClip:
        return FakeClip("fallback")

    def close_all(self) -> None:
        return None


class QuietHost:
    def __init__(self, root: Path):
        self.root = root
        self.config = Config(root)

    def presentation_port(self):
        return None


def _character() -> CharacterSpec:
    return CharacterSpec(
        id="test",
        name="Test",
        root=Path("."),
        media_kind="none",
        animations=(
            AnimationSpec(
                "idle_a", GraphType.IDEL, AnimatType.B_LOOP, loop=True
            ),
            AnimationSpec(
                "idle_b", GraphType.IDEL, AnimatType.B_LOOP, mode=ModeType.HAPPY, loop=True
            ),
            AnimationSpec("turn", GraphType.MOVE, AnimatType.B_LOOP, locate="in_place"),
            AnimationSpec("walk", GraphType.MOVE, AnimatType.B_LOOP),
            AnimationSpec("wave", GraphType.DEFAULT, AnimatType.SINGLE),
            AnimationSpec(
                "余额-钱袋满溢",
                GraphType.EVENT,
                AnimatType.SINGLE,
                event_group="balance",
            ),
        ),
    )


def _window(app, tmp_path: Path, **overrides) -> PetWindow:
    data = {"character": "test", "scale": 0.5, "frameseq": "off"}
    data.update(overrides)
    window = PetWindow(Config(tmp_path, data), tmp_path, QuietHost(tmp_path))
    window._activate(_character())
    window.library = FakeLibrary()
    window.show()
    app.processEvents()
    return window


def _act(window: PetWindow) -> AnimationSpec:
    spec = window.catalog.find("wave", AnimatType.SINGLE)
    assert spec is not None
    return spec


# ---------------------------------------------------------------- 掷骰节拍


def test_idle_finish_rolls_the_next_action(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        window._play_idle()
        assert window._action_kind == "idle"
        window.director.choose = lambda: Action(KIND_ACT, _act(window))
        window._on_finished()
        assert window._action_kind == "act"
        assert window.state.current is not None
        assert window.state.current.name == "wave"
    finally:
        window.shutdown()


def test_idle_finish_never_replays_itself_through_advance(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        window._play_idle()
        calls: list[str] = []
        original = window.director.choose

        def choose():
            calls.append("roll")
            return original()

        window.director.choose = choose
        for _ in range(3):
            window._play_idle()
            window._on_finished()
            app.processEvents()
        assert calls.count("roll") == 3
    finally:
        window.shutdown()


def test_gap_keeps_idle_running_until_it_expires(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path, animation_gap_seconds=30)
    try:
        window._action_kind = "act"
        window.state.current = _act(window)
        window._clip = window.library.clip_for(window.state.current)
        calls: list[str] = []
        window.director.choose = lambda: calls.append("roll") or Action(
            KIND_ACT, _act(window)
        )

        window._on_finished()
        assert window._action_kind == "idle"
        assert window._gap_active() is True
        assert calls == []

        window._on_finished()
        assert window._action_kind == "idle"
        assert calls == []

        window._gap_until = 0.0
        window._on_finished()
        assert calls == ["roll"]
        assert window._action_kind == "act"
    finally:
        window.shutdown()


def test_gap_is_cancelled_by_clicks(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path, animation_gap_seconds=30)
    try:
        window._gap_until = time.monotonic() + 30
        window._cancel_animation_gap()
        assert window._gap_active() is False
    finally:
        window.shutdown()


def test_same_clip_replay_rearms_without_stopping(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        spec = window.catalog.find("idle_a", AnimatType.B_LOOP)
        assert spec is not None
        window._play_spec(spec, "idle")
        clip = window._clip
        assert clip is not None
        stops = clip.stops
        starts = clip.starts
        window._play_spec(spec, "idle")
        assert window._clip is clip
        assert clip.stops == stops
        assert clip.starts == starts + 1
    finally:
        window.shutdown()


def test_configured_weights_reach_the_director(app, tmp_path: Path) -> None:
    window = _window(
        app,
        tmp_path,
        anim_weight_idle=0,
        anim_weight_turn=0,
        anim_weight_act=0,
        anim_weight_move=100,
    )
    try:
        assert window._behavior_weights() == (0.0, 0.0, 0.0, 1.0)
        assert window.director.weights == (0.0, 0.0, 0.0, 1.0)
        window.director.choose = window.director.choose
        action = window.director.choose()
        assert action is not None
        assert action.kind == "move"
    finally:
        window.shutdown()


# ---------------------------------------------------------------- 事件动画


def test_play_event_selects_by_name_and_index(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        assert window.play_event("balance", "余额-钱袋满溢") is True
        assert window._action_kind == "act"
        assert window.state.current is not None
        assert window.state.current.event_group == "balance"

        assert window.play_event("balance", 0) is True
        assert window.state.current is not None
        assert window.state.current.event_group == "balance"
    finally:
        window.shutdown()


def test_play_event_rejects_unknown_groups(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        assert window.play_event("missing") is False
    finally:
        window.shutdown()


def test_play_animation_maps_graph_to_state_kind(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path, no_move=True)
    try:
        assert window.play_animation("walk") is True
        assert window._action_kind == "idle"
        assert window.play_animation("wave") is True
        assert window._action_kind == "act"
        assert window.play_animation("missing") is False
    finally:
        window.shutdown()


# ---------------------------------------------------------------- 设置页


def test_interaction_page_exposes_and_writes_weights(app, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_petcore_settings as fixtures

    from petcore.ui.settings.page_interaction import InteractionPage

    host = fixtures.FakeHost(tmp_path)
    page = InteractionPage(host)
    try:
        assert page._weights["anim_weight_idle"].value() == 30
        assert page._weights["anim_weight_turn"].value() == 10
        assert page._weights["anim_weight_act"].value() == 40
        assert page._weights["anim_weight_move"].value() == 20
        assert page.dirty() is False

        page._weights["anim_weight_move"].setValue(55)
        assert host.config.get("anim_weight_move") == 55
        assert page.dirty() is True

        page.discard_changes()
        assert host.config.get("anim_weight_move") == 20
        assert page._weights["anim_weight_move"].value() == 20
        assert page.dirty() is False
    finally:
        page.deleteLater()
        app.processEvents()


# ---------------------------------------------------------------- 探头 / 击飞


def _probe(window: PetWindow) -> None:
    window._edge_probe._mode = "PEEKING"
    window._edge_probe._side = "left"


def _unprobe(window: PetWindow) -> None:
    window._edge_probe._mode = "OFF"
    window._edge_probe._side = None


def test_probe_session_only_plays_idle_or_turn(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    _probe(window)
    try:
        act = window.catalog.find("wave", AnimatType.SINGLE)
        assert act is not None
        window._play_spec(act, "act")
        assert window._action_kind == "idle"
        assert window.state.current is not None
        assert window.state.current.graph is GraphType.IDEL

        turn = window.catalog.find("turn", AnimatType.B_LOOP)
        assert turn is not None
        window._play_spec(turn, "turn")
        assert window._action_kind == "turn"
        assert window._pending_flip is False
    finally:
        _unprobe(window)
        window.shutdown()


def test_throw_egg_active_also_restricts_animations(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    window.effects.arm_throw_egg()
    try:
        assert window.effects.throw_egg_active is True
        act = window.catalog.find("wave", AnimatType.SINGLE)
        assert act is not None
        window._play_spec(act, "act")
        assert window._action_kind == "idle"
    finally:
        window.effects.end_throw_egg()
        window.shutdown()


def test_thrown_idle_finish_does_not_roll(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        window._play_idle()
        clip = window._clip
        assert clip is not None
        starts = clip.starts
        rolls: list[str] = []
        window.director.choose = lambda: rolls.append("roll") or Action(
            KIND_ACT, _act(window)
        )
        window.motion.mode = "thrown"
        window._on_finished()
        assert rolls == []
        assert window.state.current is not None
        assert window.state.current.graph is GraphType.IDEL
        assert clip.starts == starts + 1
    finally:
        window.motion.mode = "normal"
        window.shutdown()


def test_click_is_ignored_while_flying(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    try:
        window._play_idle()
        before = window.state.current
        window.motion.mode = "thrown"
        window._on_click()
        assert window.state.current is before
        assert window.effects.squash_active is False
    finally:
        window.motion.mode = "normal"
        window.shutdown()


def test_collision_arms_the_throw_egg_for_a_probing_pet(app, tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_petcore_p5 as p5

    from petcore.ui.pet_manager import PetManager

    host = p5._QuietHost(tmp_path)
    config = Config(tmp_path, {"character": "test", "scale": 0.5, "frameseq": "off"})
    character = _character()
    host.characters = lambda: [character]
    host.character_for = lambda _id: character
    host.build_menu = lambda parent=None: None
    host.pets = None
    manager = PetManager(host, tmp_path, config)
    host.pets = manager
    try:
        primary = manager.create_primary(character)
        second = manager.spawn()
        assert second is not None
        _probe(primary)
        second.move(primary.x() + 4, primary.y())
        second.motion.set_velocity(-420.0, 0.0)
        app.processEvents()
        manager._tick_collision()
        app.processEvents()
        assert primary.motion.thrown is True
        assert primary.effects.throw_egg_active is True
        primary.effects.update_throw_egg(600.0, 0.0, False)
        assert abs(primary.effects.effect_angle() - 90.0) < 1e-6
        primary.motion.stop()
        assert primary.effects.throw_egg_active is False
        assert primary.effects.effect_angle() == pytest.approx(0.0)
    finally:
        manager.shutdown()


def test_probe_drag_started_reports_the_previous_session(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path)
    _probe(window)
    try:
        assert window._edge_probe.on_drag_started() is True
        assert window.probe_active is False
        assert window._edge_probe.on_drag_started() is False
    finally:
        window.shutdown()


# ---------------------------------------------------------------- 动画策略


def test_random_action_switch_stops_auto_but_manual_still_plays(
    app, tmp_path: Path
) -> None:
    window = _window(
        app,
        tmp_path,
        anim_random_auto=False,
        anim_weight_idle=100,
        anim_weight_turn=0,
        anim_weight_act=100,
        anim_weight_move=0,
    )
    try:
        for _ in range(10):
            window._roll_next()
            assert window._action_kind != "act"
        assert window.play_animation("wave") is True
        assert window._action_kind == "act"
    finally:
        window.shutdown()


def test_manual_only_animation_never_rolls_but_still_plays(
    app, tmp_path: Path
) -> None:
    window = _window(
        app,
        tmp_path,
        anim_weight_idle=0,
        anim_weight_turn=0,
        anim_weight_act=100,
        anim_weight_move=0,
        anim_auto_off=["wave"],
    )
    try:
        for _ in range(20):
            window._roll_next()
            assert window.state.current is None or window.state.current.name != "wave"
        assert window.play_animation("wave") is True
    finally:
        window.shutdown()


def test_blocked_animation_cannot_be_played_manually(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path, anim_blocked=["wave"])
    try:
        assert window.play_animation("wave") is False
        assert window._action_kind != "act"
    finally:
        window.shutdown()


def test_blocked_event_animation_is_refused(app, tmp_path: Path) -> None:
    window = _window(app, tmp_path, anim_blocked=["余额-钱袋满溢"])
    try:
        assert window.play_event("balance") is False
    finally:
        window.shutdown()
