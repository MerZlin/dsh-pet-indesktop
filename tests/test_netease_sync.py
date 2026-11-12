"""Playback samples, including loss of a native clock, reach the real tracker."""
from dataclasses import replace
import time

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from pet.music.lyric import LyricLine
from pet.music.lyric_controller import MusicLyricController
from pet.music.now_playing import Playback, Track


class Window(QObject):
    cfg = {}
    _alert_current = None
    _sticky_bubble_active = False
    _speech_bubble = None

    def __init__(self):
        super().__init__()
        self.shown = []

    def isVisible(self):
        return True

    def show_bubble(self, text, **kwargs):
        self.shown.append(text)


@pytest.fixture
def controller():
    app = QApplication.instance() or QApplication([])
    window = Window()
    ctrl = MusicLyricController(window)
    sample = Playback(Track('song', 'artist', playing=True), 299, time.monotonic(), 'cloudmusic.exe')
    ctrl._current_key = sample.track.key()
    ctrl._primed = True
    ctrl._title_line = 'TITLE'
    ctrl._tracker.lead = 0
    ctrl._tracker.load([LyricLine(2, 'FIRST'), LyricLine(10, 'MIDDLE'), LyricLine(290, 'LAST')],
                       now=time.monotonic(), position=299, reported=True)
    yield app, window, ctrl, sample
    ctrl.shutdown()


def test_lost_clock_reenables_manual_alignment_and_recovered_clock_takes_over(controller):
    _, _, ctrl, sample = controller
    ctrl._on_playback_ready(sample)
    assert not ctrl.align_available()
    ctrl._on_playback_ready(replace(sample, position=None))
    assert ctrl.align_available(), 'unavailable clock must release manual alignment'
    assert ctrl.resync_to_start()
    ctrl._on_playback_ready(replace(sample, position=15))
    assert not ctrl.align_available()
    assert ctrl._last_lyric == 'MIDDLE'


def test_seek_while_paused_updates_frozen_position(controller):
    _, _, ctrl, sample = controller
    paused = replace(sample, track=replace(sample.track, playing=False), position=5)
    ctrl._on_playback_ready(paused)
    assert ctrl._tracker.position(time.monotonic()) == 5
    ctrl._on_playback_ready(replace(paused, position=0))
    assert ctrl._tracker.position(time.monotonic()) == 0


def test_two_same_song_repeats_clear_the_previous_outro(controller):
    _, window, ctrl, sample = controller
    for position, expected in [(299, 'LAST'), (0, 'TITLE'), (2, 'FIRST'), (15, 'MIDDLE'),
                               (299, 'LAST'), (0, 'TITLE'), (2, 'FIRST')]:
        ctrl._on_playback_ready(replace(sample, position=position))
        assert window.shown[-1] == expected
