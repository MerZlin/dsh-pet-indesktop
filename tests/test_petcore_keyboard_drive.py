# -*- coding: utf-8 -*-
"""键盘控制移动：前摇/循环/收尾切段与连续位移计划。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.engine.enums import AnimatType, GraphType, ModeType
from petcore.ui.pet_window import PetWindow
from petcore.engine.keyboard_drive import (
    MoveSegments,
    build_drive_plan,
    drive_position_at_frame,
    move_segments,
)


def test_move_segments_splits_windup_loop_and_recovery() -> None:
    curve = [0.0, 0.0, 0.2, 0.5, 0.8, 1.0, 1.0]
    segments = move_segments(curve, 7)
    assert segments == MoveSegments(0, 2, 4, 6)
    assert segments.loop_frames == 3
    assert segments.has_recovery is True


def test_move_segments_falls_back_to_the_whole_clip() -> None:
    assert move_segments(None, 10) == MoveSegments(0, 0, 9, 9)
    assert move_segments([1.0] * 10, 10) == MoveSegments(0, 0, 9, 9)
    assert move_segments([0.0] * 10, 10) == MoveSegments(0, 0, 9, 9)


def test_move_segments_accepts_explicit_override() -> None:
    segments = move_segments(
        [0.0] * 10,
        10,
        {"start": 0, "loop_start": 2, "loop_end": 7, "end": 9},
    )
    assert segments == MoveSegments(0, 2, 7, 9)
    # 越界/倒序回落自动推导，不抛异常。
    assert move_segments(None, 10, {"loop_start": 9, "loop_end": 2}) == MoveSegments(
        0, 0, 9, 9
    )


def test_drive_plan_advances_one_stride_per_loop() -> None:
    curve = [0.0, 0.0, 0.2, 0.5, 0.8, 1.0, 1.0]
    segments = move_segments(curve, 7)
    plan = build_drive_plan(
        segments=segments,
        curve=curve,
        direction=(1.0, 0.0),
        stride=120.0,
        start_x=100.0,
        start_y=50.0,
        loops=64,
    )
    assert plan["frames_per_loop"] == 3
    # stride 是整条 0..1 曲线的位移；这里循环段只覆盖 0.2..0.8，所以每圈 60%×120=72。
    assert drive_position_at_frame(plan, 0) == pytest.approx((100.0, 50.0))
    assert drive_position_at_frame(plan, 1)[0] == pytest.approx(136.0)
    assert drive_position_at_frame(plan, 2)[0] == pytest.approx(172.0)
    # 循环接缝那一帧位置不跳变（动画回到 loop_start）；下一帧再继续。
    assert drive_position_at_frame(plan, 3)[0] == pytest.approx(172.0)
    assert drive_position_at_frame(plan, 4)[0] == pytest.approx(208.0)
    assert drive_position_at_frame(plan, 4)[1] == pytest.approx(50.0)


def test_drive_plan_uses_full_stride_when_loop_covers_the_curve() -> None:
    curve = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    segments = MoveSegments(0, 0, len(curve) - 1, len(curve) - 1)
    plan = build_drive_plan(
        segments=segments,
        curve=curve,
        direction=(1.0, 0.0),
        stride=100.0,
        start_x=0.0,
        start_y=0.0,
        loops=4,
    )
    assert drive_position_at_frame(plan, 0)[0] == pytest.approx(0.0)
    assert drive_position_at_frame(plan, 5)[0] == pytest.approx(100.0)
    assert drive_position_at_frame(plan, 12)[0] == pytest.approx(200.0)


def test_drive_plan_flat_loop_segment_does_not_slide() -> None:
    curve = [0.0, 0.0, 0.5, 0.5, 1.0, 1.0]
    segments = MoveSegments(0, 2, 3, 5)
    plan = build_drive_plan(
        segments=segments,
        curve=curve,
        direction=(1.0, 0.0),
        stride=120.0,
        start_x=10.0,
        start_y=20.0,
        loops=8,
    )
    for frame in (0, 1, 2, 4, 8):
        assert drive_position_at_frame(plan, frame) == pytest.approx((10.0, 20.0))


def test_drive_plan_clamps_out_of_range_segment_indices() -> None:
    curve = [0.0, 0.5, 1.0]
    plan = build_drive_plan(
        segments=MoveSegments(0, 1, 99, 99),
        curve=curve,
        direction=(1.0, 0.0),
        stride=100.0,
        start_x=0.0,
        start_y=0.0,
        loops=4,
    )
    assert drive_position_at_frame(plan, 0)[0] == pytest.approx(0.0)


def test_drive_plan_normalizes_diagonal_direction() -> None:
    plan = build_drive_plan(
        segments=MoveSegments(0, 0, 3, 3),
        curve=None,
        direction=(1.0, 1.0),
        stride=100.0,
        start_x=0.0,
        start_y=0.0,
        loops=10,
    )
    x, y = drive_position_at_frame(plan, 10)
    assert x == pytest.approx(y)
    assert x > 0


def test_keyboard_hotkey_defaults_and_normalizes(tmp_path) -> None:
    config = Config(tmp_path)
    assert config.get("hotkey_keyboard_move") == "Ctrl+Alt+M"
    config.set("hotkey_keyboard_move", "ctrl+alt+k")
    assert config.get("hotkey_keyboard_move") == "Ctrl+Alt+K"
    config.set("hotkey_keyboard_move", "不是键")
    assert config.get("hotkey_keyboard_move") == ""


def test_pet_window_loops_the_middle_and_plays_recovery(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    character = CharacterSpec(
        id="drive-test",
        name="Drive Test",
        root=Path("."),
        media_kind="none",
        animations=(
            AnimationSpec(
                name="walk",
                graph=GraphType.MOVE,
                animat=AnimatType.B_LOOP,
                mode=ModeType.NOMAL,
                path=None,
                locate="ground",
                loop=False,
            ),
        ),
        move_strides={"walk": 120},
        move_curves={"walk": (0.0, 0.0, 0.2, 0.5, 0.8, 1.0, 1.0)},
    )
    config = Config(tmp_path)
    config.set("character", character.id)
    host = type(
        "Host",
        (),
        {
            "characters": lambda self: [character],
            "build_menu": lambda self, parent=None: None,
            "open_settings": lambda self: None,
            "reload_mods": lambda self: None,
            "open_data_dir": lambda self: None,
            "show_about": lambda self: None,
            "quit_app": lambda self: None,
        },
    )()
    window = PetWindow(config, tmp_path, host)
    window.start(character)
    window._idle_fps_timer.stop()
    window.show()
    window.move(100, 100)
    window.isVisible = lambda: True  # type: ignore[method-assign]
    window._clamp_position = lambda x, y, settle=True: (int(x), int(y))  # type: ignore[method-assign]

    class _Clip:
        frame_count = 7

        def __init__(self) -> None:
            self.frame = 0
            self.jumps: list[int] = []

        def jump_to_frame(self, index: int) -> bool:
            self.frame = int(index)
            self.jumps.append(int(index))
            return True

        def current_frame_number(self) -> int:
            return self.frame

        def stop(self) -> None:
            return None

        def clear_callbacks(self) -> None:
            return None

    clip = _Clip()

    def _fake_play(spec, kind):
        window._clip = clip
        window._action_kind = kind

    window._play_spec = _fake_play  # type: ignore[method-assign]
    try:
        start_x = window.x()
        window._last_activity -= 31.0
        window.drive_keyboard({"right"})
        assert window._drive_active is True
        assert time.monotonic() - window._last_activity < 1.0, "按键必须重置闲置计时"
        assert window.state.facing == "right"
        assert clip.jumps == [2], "按下必须从循环段起点开始，跳过前摇"

        stride = float(window._drive_plan["stride"])
        # stride 是整条 0..1 曲线的位移；循环段 0.2..0.8 只占 60%。
        per_loop = stride * 0.6
        clip.frame = 2
        window._advance_keyboard_drive()
        assert window.x() == start_x + round(per_loop * 0.5)
        clip.frame = 4
        jumps = len(clip.jumps)
        window._advance_keyboard_drive()
        assert window.x() == start_x + round(per_loop)
        assert len(clip.jumps) == jumps, "圈末帧必须先画出来，不能提前回绕"
        clip.frame = 5  # 下一帧越界：这一拍跳回 loop_start，位置不再前进
        window._advance_keyboard_drive()
        assert clip.jumps[-1] == 2, "越界帧必须回到 loop_start"
        assert window.x() == start_x + round(per_loop)

        window.drive_keyboard(set())
        assert window._drive_releasing is True
        assert window._idle_low_fps_allowed() is False, "刚松手不能立刻降帧"
        assert clip.jumps[-1] == 5, "松手必须跳到收尾段起点"
        window.drive_keyboard({"right"})
        assert window._drive_releasing is False
        assert window._drive_active is True
        assert clip.jumps[-1] == 2, "收尾段内再按必须回到循环段起跑"
        window._finish_keyboard_drive()
        assert window._drive_active is False

        # 贴边探头是静息态：按键必须能接管并取消探头，不能什么都不发生。
        window._edge_probe._mode = "PEEKING"  # noqa: SLF001
        window.drive_keyboard({"left"})
        assert window._drive_active is True
        assert window._edge_probe.mode != "PEEKING"
    finally:
        window.shutdown()


def test_keyboard_drive_controller_stops_and_releases() -> None:
    from petcore.ui.keyboard_drive import KeyboardDriveController

    class _Target:
        def __init__(self) -> None:
            self.states: list[set[str]] = []

        def drive_keyboard(self, keys) -> None:
            self.states.append(set(keys))

    target = _Target()
    controller = KeyboardDriveController(reader=lambda: {"right"})
    controller.set_target(target)
    try:
        controller._tick()
        assert target.states[-1] == {"right"}
        controller.stop()
        assert target.states[-1] == set()
        assert controller.target is None
    finally:
        controller.stop()


def test_keyboard_polling_drops_to_idle_rate_and_back() -> None:
    from petcore.ui.keyboard_drive import KeyboardDriveController

    class Target:
        def __init__(self) -> None:
            self.calls: list[set[str]] = []

        def isVisible(self) -> bool:  # noqa: N802 - Qt 命名
            return True

        def drive_keyboard(self, directions) -> None:
            self.calls.append(set(directions))

    QApplication.instance() or QApplication([])
    target = Target()
    keys: list[str] = []
    controller = KeyboardDriveController(reader=lambda: keys)
    controller._target = target
    controller._timer.start()
    try:
        for _ in range(controller.IDLE_AFTER_TICKS):
            controller._tick()
        assert controller._timer.interval() == controller.IDLE_INTERVAL_MS

        keys.append("left")
        controller._tick()
        assert controller._timer.interval() == controller.INTERVAL_MS
        assert target.calls[-1] == {"left"}
    finally:
        controller.stop()
