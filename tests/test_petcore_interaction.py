from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.engine import physics
from petcore.engine.enums import AnimatType, GraphType
from petcore.engine.collision import CollisionBody, collide
from petcore.engine.feeding import ScanBudget, format_bytes, measure_paths, pick_eating_animation
from petcore.engine.spin import eased_progress, spin_angle
from petcore.engine.squash import squash_geometry
from petcore.ui.pet_window import PetWindow


class FakeHost:
    def __init__(self, characters):
        self._characters = characters

    def characters(self):
        return list(self._characters)

    def build_menu(self, parent=None):
        return None

    def open_settings(self, initial_page=None):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def quit_app(self):
        return None


def fallback() -> CharacterSpec:
    return CharacterSpec(id="fallback", name="Fallback", root=Path("."), media_kind="none", animations=())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    config = Config(tmp_path)
    instance = PetWindow(config, tmp_path, FakeHost([fallback()]))
    instance.start(fallback())
    yield instance
    instance.shutdown()


def test_squash_geometry_is_bottom_centered() -> None:
    x, y, width, height = squash_geometry(640, 360, 640, 360, 0.5)
    assert width > 640
    assert height < 360
    assert x + width // 2 == pytest.approx(320, abs=1)
    assert y + height == 360


def test_squash_geometry_bounces_add_pulses_without_crossing_top() -> None:
    single = [squash_geometry(640, 360, 640, 360, p / 20) for p in range(21)]
    triple = [
        squash_geometry(640, 360, 640, 360, p / 20, bounces=3)
        for p in range(21)
    ]
    assert max(w for _, _, w, _ in single) == pytest.approx(
        max(w for _, _, w, _ in triple), abs=8
    )
    assert all(y >= 0 and y + h == 360 for _, y, _, h in triple)
    # 三次弹跳在中段仍会再次压扁，不是一次到底。
    assert any(h < 360 for _, _, _, h in triple)


def test_squash_geometry_strength_scales_amplitude() -> None:
    _, _, weak_w, weak_h = squash_geometry(640, 360, 640, 360, 0.5, strength=0.5)
    _, _, base_w, base_h = squash_geometry(640, 360, 640, 360, 0.5)
    _, _, strong_w, strong_h = squash_geometry(640, 360, 640, 360, 0.5, strength=1.8)
    assert base_w > weak_w and base_h < weak_h
    assert strong_w > base_w and strong_h < base_h
    assert weak_h <= 360 and strong_h >= 1


def test_effect_controller_reads_adjustable_squash(window) -> None:
    window.config.set("click_squash_bounces", 3)
    window.config.set("click_squash_strength", 160)
    window.config.set("click_squash_ms", 600)
    window.effects.start_squash()
    assert window.effects.squash_active is True
    assert window.effects._squash_bounces == 3
    assert window.effects._squash_strength == pytest.approx(1.6)
    assert window.effects._squash_duration_ms == 600

    window.config.set("click_squash_enabled", False)
    window.effects.reset()
    window.effects.start_squash()
    assert window.effects.squash_active is False


def test_spring_and_release_velocity() -> None:
    velocity = physics.spring_velocity(0.0, 0.0, 100.0, 0.016)
    assert velocity > 0.0
    trail = [(0.0, 0.0, 0.0), (0.02, 10.0, 0.0), (0.04, 20.0, 0.0)]
    vx, vy = physics.estimate_release_velocity(trail, 0.04)
    assert vx > 0.0
    assert abs(vy) < 1e-6


def test_throw_bounces_and_rests() -> None:
    px, py, vx, vy, bounced = physics.throw_step(
        100.0, 350.0, 0.0, 0.0, 0.1, 0.0, 0.0, 640.0, 360.0
    )
    assert bounced is True
    assert py == 360.0
    assert not physics.is_at_rest(py, vx, vy, 360.0, bounced, math.hypot(vx, vy))
    assert physics.is_at_rest(360.0, 0.0, 0.0, 360.0, True, 0.0)


def test_slingshot_deformation_and_trajectory() -> None:
    sx, sy = physics.slingshot_deformation(100.0, 0.0, 0.5)
    assert sx > 1.0
    assert sy < 1.0
    points = physics.slingshot_trajectory(300.0, -100.0)
    assert len(points) == 12
    assert points[0] == (0.0, 0.0)


def test_spin_progress_is_monotonic() -> None:
    assert eased_progress(0.0, 700.0) == 0.0
    assert eased_progress(700.0, 700.0) == 1.0
    assert spin_angle(0.0) == 0.0
    assert spin_angle(1.0) == -360.0


def test_collision_separates_and_exchanges_velocity() -> None:
    a = CollisionBody(0, 0, 100, 100, vx=200.0, vy=0.0)
    b = CollisionBody(40, 0, 100, 100, vx=-200.0, vy=0.0)
    result = collide(a, b)
    assert result is not None
    assert result.depth > 0
    assert result.ax < result.bx
    assert result.impact_speed > 0


def test_dragging_body_is_infinite_mass() -> None:
    a = CollisionBody(0, 0, 100, 100, dragging=True)
    b = CollisionBody(40, 0, 100, 100)
    result = collide(a, b)
    assert result is not None
    assert result.ax == pytest.approx(a.x)
    assert result.bx > b.x


def test_feeding_budget_and_helpers(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x" * 10, encoding="utf-8")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "b.txt").write_text("y" * 20, encoding="utf-8")
    report = measure_paths([tmp_path])
    assert report.files == 2
    assert report.folders >= 1
    assert report.total_bytes >= 30
    assert format_bytes(2048) == "2.0 KB"
    assert pick_eating_animation(["吃Token", "idle"]) == "吃Token"
    budget = ScanBudget(file_limit=1)
    assert budget.account_file() is True
    assert budget.account_file() is False
    assert budget.truncated is True


def test_click_starts_squash_and_spin(window) -> None:
    window.config.set("golden_spin_on_click", True)
    window._on_click()
    assert window.effects.squash_active is True
    assert window.effects.spin_pending is True
    window.effects.start_spin(direct=True)
    assert window.effects.spin_active is True


def test_slingshot_enters_and_launches(window) -> None:
    window.dragging = True
    window.motion.begin_drag(window.x(), window.y())
    assert window.slingshot.maybe_enter(QPointF(100, 100), QPointF(100, 100)) is True
    window.slingshot.update(QPointF(20, 100))
    assert window.slingshot.distance() == pytest.approx(80.0)
    assert window.slingshot.launch() is True
    assert window.motion.thrown is True


def test_config_normalizes_interaction_keys(tmp_path: Path) -> None:
    config = Config(tmp_path, {"playback_speed": 9.0, "throw_strength": "nope", "active_pets": [9, 2, 2]})
    assert config.get("playback_speed") == 2.0
    assert config.get("throw_strength") == "standard"
    assert config.get("active_pets") == [0, 2, 9]


# ---------------------------------------------------------------- 动画循环接缝（P4-b）


class _LoopClip:
    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        return

    def clear_callbacks(self) -> None:
        return


def test_animation_loop_replays_until_released(window, monkeypatch) -> None:
    clip = _LoopClip()
    window._clip = clip
    window._loop_active = True
    monkeypatch.setattr(window, "isVisible", lambda: True)

    window._on_finished()

    assert clip.starts == 1
    assert window._loop_active is True
    assert window.release_loop() is True
    assert window.release_loop() is False


def test_animation_loop_is_cancelled_by_effects_lock(window, monkeypatch) -> None:
    clip = _LoopClip()
    window._clip = clip
    window._loop_active = True
    monkeypatch.setattr(window, "isVisible", lambda: True)
    monkeypatch.setattr(window, "_effects_lock_active", lambda: True)

    window._on_finished()

    assert window._loop_active is False
    assert clip.starts == 0


def test_play_spec_clears_the_loop(window) -> None:
    from petcore.contracts.content import AnimationSpec
    from petcore.engine.enums import AnimatType

    window._loop_active = True
    window._play_spec(AnimationSpec(name="missing", graph=None, animat=AnimatType.SINGLE), "act")

    assert window._loop_active is False


def test_head_pat_uses_head_box_and_starts_head_pat_action(app, tmp_path, monkeypatch):
    character = CharacterSpec(
        id="pat",
        name="Pat",
        root=tmp_path,
        media_kind="none",
        animations=(
            AnimationSpec("点击", GraphType.TOUCH_BODY, AnimatType.SINGLE),
            AnimationSpec("摸头", GraphType.TOUCH_HEAD, AnimatType.SINGLE),
        ),
        canvas=(100, 100),
        body_box=(0, 0, 100, 100),
        head_box=(0, 0, 100, 60),
    )
    config = Config(tmp_path / "pat-config")
    instance = PetWindow(config, tmp_path, FakeHost([character]))
    instance.start(character)
    instance._image = None
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        instance,
        "_play_spec",
        lambda spec, kind: calls.append((spec.name, kind)),
    )
    times = iter((0.0, 0.1, 0.2, 0.3))
    monkeypatch.setattr("petcore.ui.pet_window.time.monotonic", lambda: next(times))

    assert instance.hasMouseTracking() is True
    assert instance._head_pat_hit(QPointF(10, 10).toPoint())[2] is True
    assert instance._head_pat_hit(QPointF(10, 90).toPoint())[2] is False
    for point in (QPointF(10, 10), QPointF(20, 10), QPointF(10, 10), QPointF(20, 10)):
        instance._maybe_head_pat(point)

    assert calls == [("点击", "head_pat")]
    instance.shutdown()


def test_head_pat_is_suppressed_when_disabled_or_busy(window, monkeypatch):
    from petcore.contracts.content import AnimationSpec
    from petcore.engine.enums import AnimatType, GraphType

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_play_spec", lambda spec, kind: calls.append((spec.name, kind)))
    monkeypatch.setattr(
        window,
        "_head_pat_candidates",
        lambda: (AnimationSpec("点击", GraphType.TOUCH_BODY, AnimatType.SINGLE),),
    )
    window._head_local = (0, 0, 100, 100)
    window._body_local = (0, 0, 100, 100)
    clock = iter((0.0, 0.1, 0.2, 0.3, 0.4, 0.5))
    monkeypatch.setattr("petcore.ui.pet_window.time.monotonic", lambda: next(clock))

    window.config.set("head_pat_enabled", False)
    window._maybe_head_pat(QPointF(10, 10))
    assert calls == []

    window.config.set("head_pat_enabled", True)
    window._action_kind = "click"
    for point in (QPointF(10, 10), QPointF(20, 10), QPointF(10, 10), QPointF(20, 10)):
        window._maybe_head_pat(point)
    assert calls == []

    window._action_kind = None
    window.click_through = True
    window._maybe_head_pat(QPointF(10, 10))
    assert calls == []


def test_click_through_left_click_forwards_instead_of_petting(window, monkeypatch):
    window.click_through = True
    forwarded: list[object] = []
    clicks: list[object] = []
    monkeypatch.setattr(
        "petcore.ui.pet_window.forward_left_click",
        lambda target: forwarded.append(target) or True,
    )
    monkeypatch.setattr(window, "_on_click", lambda **kwargs: clicks.append(kwargs))

    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

    assert forwarded == [window]
    assert clicks == []


def test_click_through_right_click_replaces_the_old_left_click(window, monkeypatch):
    window.click_through = True
    clicks: list[object] = []
    monkeypatch.setattr(window, "_on_click", lambda **kwargs: clicks.append(kwargs))

    QTest.mousePress(window, Qt.MouseButton.RightButton, pos=QPoint(10, 10))
    QTest.mouseRelease(window, Qt.MouseButton.RightButton, pos=QPoint(10, 10))

    assert len(clicks) == 1


def test_click_through_right_hold_opens_menu_without_a_click(window, monkeypatch):
    window.click_through = True
    menu: list[bool] = []
    clicks: list[object] = []
    monkeypatch.setattr(
        QApplication, "mouseButtons", staticmethod(lambda: Qt.MouseButton.RightButton)
    )
    monkeypatch.setattr(window, "_show_context_menu_at_cursor", lambda: menu.append(True))
    monkeypatch.setattr(window, "_on_click", lambda **kwargs: clicks.append(kwargs))
    monkeypatch.setattr(window, "mapFromGlobal", lambda _pos: QPoint(10, 10))

    QTest.mousePress(window, Qt.MouseButton.RightButton, pos=QPoint(10, 10))
    window._on_right_hold()
    assert menu == [], "菜单等松手再弹（按住时 exec 会把 release 当成选中）"
    QTest.mouseRelease(window, Qt.MouseButton.RightButton, pos=QPoint(10, 10))

    assert menu == [True]
    assert clicks == []


def test_click_through_left_hold_arms_drag(window, monkeypatch):
    window.click_through = True
    monkeypatch.setattr(
        QApplication, "mouseButtons", staticmethod(lambda: Qt.MouseButton.LeftButton)
    )
    monkeypatch.setattr(window, "_play_spec", lambda spec, kind: None)

    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    window._on_left_hold()

    assert window._left_drag_armed is True
    assert window.dragging is True

    window._cancel_left_press()
    window.dragging = False
    window.state.dragging = False


def test_classic_left_click_still_pets(window, monkeypatch):
    window.click_through = False
    clicks: list[object] = []
    monkeypatch.setattr(window, "_on_click", lambda **kwargs: clicks.append(kwargs))
    monkeypatch.setattr(window._edge_probe, "on_clicked", lambda: False)

    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

    assert len(clicks) == 1
