from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.platform import win as win_mod
from petcore.ui.app import CoreHost
from petcore.ui.pet_window import PetWindow

MASK = Qt.WindowType.WindowType_Mask


class FakeHost:
    def characters(self):
        return []

    def character_for(self, character_id):
        return None

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

    def apply_live_settings(self):
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path, stream: bool) -> PetWindow:
    config = Config(tmp_path)
    config.set("stream_capture_mode", stream)
    return PetWindow(config, tmp_path, FakeHost())


def test_stream_mode_uses_a_regular_top_level_window(app, tmp_path: Path) -> None:
    window = _window(tmp_path, True)
    try:
        assert window.windowFlags() & MASK == Qt.WindowType.Window
        assert window.windowFlags() & MASK != Qt.WindowType.Tool
        assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert window.windowTitle() == "dsh-pet core #0"
        assert window.bubble.windowFlags() & MASK == Qt.WindowType.Window
        assert window.bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    finally:
        window.shutdown()


def test_tool_mode_is_the_default(app, tmp_path: Path) -> None:
    window = _window(tmp_path, False)
    try:
        assert window.windowFlags() & MASK == Qt.WindowType.Tool
        assert window.bubble.windowFlags() & MASK == Qt.WindowType.Tool
        assert window.windowTitle() == ""
    finally:
        window.shutdown()


def test_set_stream_capture_mode_round_trips_window_and_bubble(app, tmp_path: Path) -> None:
    window = _window(tmp_path, False)
    try:
        window.set_stream_capture_mode(True)
        assert window.stream_capture_mode is True
        assert window.config.get("stream_capture_mode") is True
        assert Config.load(tmp_path).get("stream_capture_mode") is True
        assert window.windowFlags() & MASK == Qt.WindowType.Window
        assert window.bubble.windowFlags() & MASK == Qt.WindowType.Window

        window.set_stream_capture_mode(False, persist=False)
        assert window.windowFlags() & MASK == Qt.WindowType.Tool
        assert window.bubble.windowFlags() & MASK == Qt.WindowType.Tool
        assert Config.load(tmp_path).get("stream_capture_mode") is True
    finally:
        window.shutdown()


def test_live_apply_flips_stream_mode_without_persisting(tmp_path: Path) -> None:
    calls = []

    class RecordingWindow:
        def __init__(self) -> None:
            self.config = Config(tmp_path)
            self.character_id = "shenshen"
            self.state = SimpleNamespace(scale=0.72)
            self.always_on_top = True
            self.click_through = False
            self.stream_capture_mode = False
            self.stream_capture_keycolor = False
            self.stream_key_color = SimpleNamespace(name=lambda: "#00ff00")

        def set_stream_capture_mode(
            self, value, *, keycolor=None, persist=True
        ):
            self.stream_capture_mode = bool(value)
            if keycolor is not None:
                self.stream_capture_keycolor = bool(keycolor)
            calls.append((bool(value), persist))

    host = object.__new__(CoreHost)
    host.config = Config(tmp_path)
    host.window = RecordingWindow()
    host.pets = None
    host.config.set("stream_capture_mode", True)

    CoreHost.apply_live_settings(host)

    assert calls == [(True, False)]


def test_apply_stream_window_is_a_no_op_off_windows(monkeypatch) -> None:
    def _fail():
        raise AssertionError("user32 must not be touched off Windows")

    monkeypatch.setattr(win_mod.os, "name", "posix")
    monkeypatch.setattr(win_mod, "_user32", _fail)

    assert win_mod.apply_stream_window(0) is None


def test_keycolor_mode_uses_an_opaque_window_and_paints_the_key(app, tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("stream_capture_mode", True)
    config.set("stream_capture_keycolor", True)
    config.set("stream_key_color", "#00FF00")
    window = PetWindow(config, tmp_path, FakeHost())
    try:
        assert window.stream_capture_keycolor is True
        assert (
            window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False
        )
        window.resize(80, 80)
        image = window.grab().toImage()
        assert image.pixelColor(0, 0).name().upper() == "#00FF00"
        assert window.bubble.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        ) is False
    finally:
        window.shutdown()


def test_keycolor_toggle_persists_both_keys(app, tmp_path: Path) -> None:
    window = _window(tmp_path, False)
    try:
        window.set_stream_capture_mode(True, keycolor=True)
        assert window.stream_capture_mode is True
        assert window.stream_capture_keycolor is True
        assert Config.load(tmp_path).get("stream_capture_keycolor") is True
        window.set_stream_capture_mode(False, keycolor=False, persist=False)
        assert window.stream_capture_mode is False
        assert window.stream_capture_keycolor is False
    finally:
        window.shutdown()
