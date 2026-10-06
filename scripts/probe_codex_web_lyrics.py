"""Exercise timed web lyrics through real Qt preview and notification events."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_probe(app, config, output):
    from PySide6.QtCore import QEventLoop, QRect, QTimer
    from PySide6.QtWidgets import QWidget
    from pet import music_lyric, now_playing
    from pet.music_lyric_controller import MusicLyricController
    from pet.speech_bubble import PetSpeechBubble
    from pet.ui_preview import install_runtime

    original_http = music_lyric._http_get_json
    def provider_response(url, **_kwargs):
        if "/cloudsearch/" in url:
            return {"result": {"songs": [{"id": 123, "ar": [{"name": "Artist A"}, {"name": "周杰伦"}]}]}}
        return {"lrc": {"lyric": "[00:01.00]provider sample"}}
    music_lyric._http_get_json = provider_response
    try:
        matched = music_lyric._fetch_from_netease("Test song", "Artist A和周杰倫")
        assert matched is not None, "Web collaborative and traditional artist names did not match the provider"
        assert matched.lines[0].text == "provider sample"
        assert not music_lyric._name_matches("Unrelated artist", "Artist A和周杰倫")
    finally:
        music_lyric._http_get_json = original_http

    first = ("測試第一句，連續歌詞與文字換行 " + "sample text 🎵 " * 8).strip()
    second, third = "測試第二句，歌詞繼續前進", "測試第三句，提示結束後接續"
    lines = music_lyric.Lyrics(lines=tuple(music_lyric.LyricLine(at, text) for at, text in ((0, first), (10, second), (20, third))))
    position = [1.0]
    track = now_playing.Track("Web song", "Test artist", duration=120, playing=True)

    def playback(*_args):
        return now_playing.Playback(track, position[0], time.monotonic(), "MSEdge")

    class Pet(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config
            self._speech_bubble = PetSpeechBubble(self)
            self._alert_current = None
            self._sticky_bubble_active = False
            self.messages = []
            self.resize(160, 160)
            self.move(300, 350)

        def hold_bubble(self, _seconds):
            pass

        def set_instrumental_playing(self, _on):
            pass

        def show_bubble(self, message, **kwargs):
            self.messages.append(message)
            self._speech_bubble.show_text(message, QRect(300, 350, 160, 160), **kwargs)

    def until(predicate):
        if predicate():
            return
        loop = QEventLoop()
        check, deadline = QTimer(), QTimer()
        check.setInterval(10)
        check.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        check.start()
        deadline.start(15000)
        loop.exec()
        check.stop()
        deadline.stop()
        assert predicate(), "Qt event did not reach the expected lyric state"

    original_read, original_fetch = now_playing.get_now_playing, music_lyric.fetch_lyrics
    now_playing.get_now_playing = playback  # OS media-session boundary
    music_lyric.fetch_lyrics = lambda *_args, **_kwargs: lines  # network boundary
    pet = Pet()
    runtime = install_runtime(config)
    runtime.tick()
    controller = MusicLyricController(pet)
    try:
        config.set("music_lyric_enabled", True)
        pet.show()
        controller.sync_enabled(True)
        until(lambda: first in pet.messages)
        bubble = pet._speech_bubble
        assert controller._tracker.uses_reported_position

        def full_text(expected):
            assert "".join(bubble.label.text().split()) == "".join(expected.split())
            assert not bubble._subtitle_label.isVisible()
            assert not bubble._pages

        for theme, scale in (("dark", 120), ("light", 300), ("glass", 100)):
            config.set("bubble_ui_style", theme)
            config.set("bubble_text_scale", scale)
            remaining = bubble._hide_timer.remainingTime()
            runtime.refresh_bubble(bubble, config, pet, {"bubble_ui_style": theme}, None)
            assert bubble._ytmusic_owner == id(controller), "Preview redraw discarded the lyric owner"
            assert not controller._bubble_taken_by_other(), "Own lyrics were mistaken for a notification"
            assert 0 < bubble._hide_timer.remainingTime() <= remaining
            full_text(first)

        position[0] = 11.0
        controller._on_tick()
        until(lambda: second in pet.messages)
        full_text(second)
        # A new notification, even while previewing, must retain its priority.
        pet.show_bubble("請確認操作", duration_ms=1000, sticky=True, buttons=[("確認", lambda: None)])
        assert controller._bubble_blocked()
        assert bubble._ytmusic_owner is None
        runtime.refresh_bubble(bubble, config, pet, {"bubble_ui_style": "dark"}, None)
        position[0] = 21.0
        controller._on_tick()
        until(lambda: controller._tracker.position(time.monotonic()) >= 20)
        assert bubble._raw_text == "請確認操作" and bubble._interactive_active
        bubble.hide()
        until(lambda: not bubble._interactive_active)
        controller._on_tick()
        until(lambda: third in pet.messages)
        full_text(third)
        bubble.grab().save(str(output / "web-lyric-preview.png"))
        result = {"webPlaybackRecognized": True, "collaborativeAndTraditionalArtistsMatch": True,
                  "previewRetainsLyricOwnership": True,
                  "fullTextSurvivesPreview": True, "previewPreservesExpiry": True,
                  "consecutiveLyricsContinue": True, "interactiveNotificationKeepsPriority": True,
                  "lyricsResumeAfterDismiss": True, "modelGenerationRequests": 0}
        (output / "web-lyrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        controller.shutdown()
        runtime.timer.stop()
        pet._speech_bubble.hide()
        pet.hide()
        now_playing.get_now_playing, music_lyric.fetch_lyrics = original_read, original_fetch
        pet.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from pet.codex_companion import install
    from pet.config import Config

    install()
    app = QApplication.instance() or QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output) as profile:
        print(json.dumps(run_probe(app, Config(base=profile), args.output)))


if __name__ == "__main__":
    main()
