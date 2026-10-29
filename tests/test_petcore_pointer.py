"""穿透模式的鼠标状态机（petcore.engine.pointer）纯逻辑测试。"""

from __future__ import annotations

from petcore.engine.pointer import (
    POINTER_DRAG_HOLD_MS,
    POINTER_MENU_HOLD_MS,
    PointerAction,
    PointerArbiter,
)


def test_threshold_constants_are_sane() -> None:
    assert 150 <= POINTER_DRAG_HOLD_MS <= 600
    assert 200 <= POINTER_MENU_HOLD_MS <= 900


def test_left_quick_click_is_forwarded_not_a_pet_click() -> None:
    arbiter = PointerArbiter()
    assert arbiter.left_press() is PointerAction.PENDING
    assert arbiter.left_release() is PointerAction.FORWARD_CLICK


def test_left_hold_starts_drag_and_release_is_drag_release() -> None:
    arbiter = PointerArbiter()
    assert arbiter.left_press() is PointerAction.PENDING
    assert arbiter.left_hold() is PointerAction.DRAG
    assert arbiter.left_dragging is True
    assert arbiter.left_release() is PointerAction.DRAG_RELEASE
    assert arbiter.left_down is False


def test_left_hold_without_press_is_ignored() -> None:
    arbiter = PointerArbiter()
    assert arbiter.left_hold() is PointerAction.NONE
    assert arbiter.left_release() is PointerAction.NONE


def test_right_quick_click_replaces_old_left_click() -> None:
    arbiter = PointerArbiter()
    assert arbiter.right_press(dragging=False) is PointerAction.PENDING
    assert arbiter.right_release() is PointerAction.CLICK


def test_right_hold_opens_menu_and_swallows_release() -> None:
    arbiter = PointerArbiter()
    assert arbiter.right_press(dragging=False) is PointerAction.PENDING
    assert arbiter.right_hold() is PointerAction.MENU
    assert arbiter.right_menu_open is True
    assert arbiter.right_release() is PointerAction.NONE
    assert arbiter.right_menu_open is False


def test_right_hold_is_idempotent() -> None:
    arbiter = PointerArbiter()
    arbiter.right_press(dragging=False)
    assert arbiter.right_hold() is PointerAction.MENU
    assert arbiter.right_hold() is PointerAction.NONE


def test_right_click_while_dragging_enters_slingshot() -> None:
    arbiter = PointerArbiter()
    assert arbiter.right_press(dragging=True) is PointerAction.SLINGSHOT
    # 瞄准期间不再记右键按下，松手也不会变成一次点击。
    assert arbiter.right_release() is PointerAction.NONE


def test_second_left_press_while_down_is_ignored() -> None:
    arbiter = PointerArbiter()
    arbiter.left_press()
    assert arbiter.left_press() is PointerAction.NONE
    assert arbiter.left_release() is PointerAction.FORWARD_CLICK


def test_reset_clears_both_buttons() -> None:
    arbiter = PointerArbiter()
    arbiter.left_press()
    arbiter.right_press(dragging=False)
    arbiter.reset()
    assert arbiter.left_down is False
    assert arbiter.right_down is False
    assert arbiter.left_release() is PointerAction.NONE
    assert arbiter.right_release() is PointerAction.NONE


def test_right_cancel_does_not_emit_a_click() -> None:
    arbiter = PointerArbiter()
    arbiter.right_press(dragging=False)
    arbiter.right_cancel()
    assert arbiter.right_release() is PointerAction.NONE
