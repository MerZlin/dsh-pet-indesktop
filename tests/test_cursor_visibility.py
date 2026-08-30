# -*- coding: utf-8 -*-
"""Cursor visibility API adapter and automatic passthrough state tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pet import vision
from pet.window import PetWindow


class FakeUser32:
    def __init__(self, flags=0, result=1, error=None):
        self.flags = flags
        self.result = result
        self.error = error
        self.seen_size = None
        self.GetCursorInfo = _FakeFunction(self._get_cursor_info)

    def _get_cursor_info(self, pointer):
        if self.error:
            raise self.error
        self.seen_size = pointer._obj.cbSize
        pointer._obj.flags = self.flags
        return self.result


class _FakeFunction:
    def __init__(self, function):
        self.function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.function(*args)


@pytest.mark.parametrize(
    ('flags', 'expected'),
    [
        (vision.CURSOR_SHOWING, 'SHOWING'),
        (0, 'HIDDEN'),
        (vision.CURSOR_SUPPRESSED, 'SUPPRESSED'),
        (vision.CURSOR_SHOWING | vision.CURSOR_SUPPRESSED, 'SHOWING'),
    ],
)
def test_get_cursor_visibility_maps_flags(monkeypatch, flags, expected):
    monkeypatch.setattr(vision.sys, 'platform', 'win32')
    user32 = FakeUser32(flags=flags)

    assert vision.get_cursor_visibility(user32) == expected
    assert user32.seen_size == vision.ctypes.sizeof(vision._CursorInfo)


@pytest.mark.parametrize('user32', [FakeUser32(result=0), FakeUser32(error=OSError())])
def test_get_cursor_visibility_failures_are_unknown(monkeypatch, user32):
    monkeypatch.setattr(vision.sys, 'platform', 'win32')
    assert vision.get_cursor_visibility(user32) == 'UNKNOWN'


def test_get_cursor_visibility_non_windows_does_not_access_user32(monkeypatch):
    monkeypatch.setattr(vision.sys, 'platform', 'linux')
    assert vision.get_cursor_visibility(SimpleNamespace()) == 'UNKNOWN'


def _state_window(monkeypatch):
    win = PetWindow.__new__(PetWindow)
    win.cfg = {'cursor_hidden_passthrough': True}
    win._cursor_hidden_passthrough = True
    win._user_mouse_through = False
    win._auto_cursor_hidden = False
    win.mouse_through = False
    win._cursor_visibility = 'UNKNOWN'
    win._cursor_hidden_since = None
    win._cursor_restore_pending = False
    win._press_global = None
    win._dragging = False
    win._interaction_state = 'IDLE'
    win._applied = []
    monkeypatch.setattr(PetWindow, '_apply_effective_mouse_through',
                        lambda self: self._applied.append(self._user_mouse_through or self._auto_cursor_hidden))
    return win


def test_cursor_hidden_requires_200ms_and_showing_restores(monkeypatch):
    win = _state_window(monkeypatch)
    times = iter((10.0, 10.1, 10.2, 10.21, 10.22))
    monkeypatch.setattr('pet.window.time.monotonic', lambda: next(times))

    win._on_cursor_visibility_changed('HIDDEN')
    win._on_cursor_visibility_changed('HIDDEN')
    win._on_cursor_visibility_changed('HIDDEN')
    win._on_cursor_visibility_changed('HIDDEN')
    assert win._auto_cursor_hidden is True
    assert win._applied == [True]

    win._on_cursor_visibility_changed('SHOWING')
    assert win._auto_cursor_hidden is False
    assert win._applied[-1] is False


def test_unknown_is_conservative_and_manual_layer_survives(monkeypatch):
    win = _state_window(monkeypatch)
    win._user_mouse_through = True
    win._auto_cursor_hidden = True
    win._on_cursor_visibility_changed('UNKNOWN')
    assert win._auto_cursor_hidden is True

    win._on_cursor_visibility_changed('SHOWING')
    assert win._auto_cursor_hidden is False
    assert win._applied[-1] is True


def test_cursor_transition_is_deferred_until_release(monkeypatch):
    win = _state_window(monkeypatch)
    win._press_global = object()
    win._on_cursor_visibility_changed('SHOWING')
    assert win._cursor_restore_pending is True
    assert win._applied == []

    win._press_global = None
    win._dragging = False
    win._interaction_state = 'IDLE'
    win._auto_cursor_hidden = False
    win._apply_effective_mouse_through()
    assert win._applied == [False]
