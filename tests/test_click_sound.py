# -*- coding: utf-8 -*-
"""点击音效播放、缓存与包解析测试。"""
from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

from pet import click_sound
from pet import window as window_mod


class FakeQtAudio:
    def __init__(self) -> None:
        self.volume = 1.0

    def setVolume(self, v: float) -> None:
        self.volume = v


class FakeQtPlayer:
    def __init__(self) -> None:
        self.stopped = False
        self.source = None
        self.played = False
        self.audio_output = None

    def stop(self) -> None:
        self.stopped = True

    def setSource(self, qurl) -> None:
        self.source = qurl

    def play(self) -> None:
        self.played = True

    def setAudioOutput(self, audio) -> None:
        self.audio_output = audio


class FakeSignal:
    def connect(self, callback):
        self.callback = callback


class FakeQtEffect:
    instances = []

    def __init__(self):
        self.source = None
        self.volumes = []
        self.play_count = 0
        self.__class__.instances.append(self)

    def setSource(self, source):
        self.source = source

    def setVolume(self, volume):
        self.volumes.append(volume)

    def play(self):
        self.play_count += 1


class FakeQtDecoder:
    def __init__(self):
        self.bufferReady = FakeSignal()
        self.finished = FakeSignal()
        self.error = FakeSignal()

    def setSource(self, source):
        self.source = source

    def start(self):
        self.error.callback("decode failed")


def _fake_classes():
    return (FakeQtDecoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect)


def _make_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"not-a-real-audio-file")
    return path


def test_wav_restarts_qsound_effect_on_each_click(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    effect = FakeQtEffect()
    monkeypatch.setattr(click_sound, "_qt_effects", {})
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", _fake_classes)

    path_wav = _make_file(tmp_path, "click.wav")
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert len(FakeQtEffect.instances) >= 1
    assert FakeQtEffect.instances[-1].play_count == 2
    assert FakeQtEffect.instances[-1].volumes == [0.5, 0.5]


def test_mp3_decode_failure_falls_back_to_player_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound, "_qt_effects", {})
    monkeypatch.setattr(click_sound, "_qt_decoders", {})
    monkeypatch.setattr(click_sound, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: tmp_path / "cache")

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is True
    assert len(click_sound._qt_player_pool) == 4
    assert click_sound._qt_player_pool[0][0].played is True


def test_nonwav_qt_unavailable_on_windows_skips_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", lambda: None)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is False


def test_mp3_second_click_uses_decoded_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound, "_qt_effects", {})
    monkeypatch.setattr(click_sound, "_qt_decoders", {})
    monkeypatch.setattr(click_sound, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", _fake_classes)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    path = _make_file(tmp_path, "click.mp3")

    class Decoder(FakeQtDecoder):
        def start(self):
            class Format:
                def sampleFormat(self): return 2
                def channelCount(self): return 1
                def sampleRate(self): return 8000
            class Buffer:
                def format(self): return Format()
                def data(self): return b"pcm"
            self.bufferAvailable = lambda: bool(getattr(self, "pending", True))
            self.read = lambda: (setattr(self, "pending", False) or Buffer())
            self.bufferReady.callback()
            self.finished.callback()

    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", lambda: (Decoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect))
    assert click_sound.play_sound(path) is True
    assert list(cache_dir.glob("*.wav"))
    assert click_sound.play_sound(path) is True
    assert FakeQtEffect.instances[-1].play_count == 1


def test_warm_player_pool_precreates_qt_players(monkeypatch):
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound, "_qt_player", None)
    monkeypatch.setattr(click_sound, "_qt_audio", None)

    click_sound._warm_player_pool()

    assert len(click_sound._qt_player_pool) == 4


def test_warm_click_sound_effects_precreates_wav_effect_and_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "_qt_available", lambda: True)
    monkeypatch.setattr(click_sound, "_qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_qt_effects", {})
    monkeypatch.setattr(click_sound, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound, "_qt_player", None)
    monkeypatch.setattr(click_sound, "_qt_audio", None)

    wav = _make_file(tmp_path, "click.wav")
    pack = {"kind": "file", "id": "custom", "path": str(wav)}
    click_sound.warm_click_sound_effects(pack, data_dir=tmp_path)

    assert str(wav.resolve()) in click_sound._qt_effects
    assert len(click_sound._qt_player_pool) == 4


def test_resolve_click_sound_candidates_and_choose(tmp_path):
    # 1. file mode
    f = _make_file(tmp_path, "test.mp3")
    pack_file = {"kind": "file", "id": "custom", "path": str(f)}
    candidates = click_sound.resolve_click_sound_candidates(pack_file)
    assert candidates == [f]
    assert click_sound.choose_sound(candidates) == f

    # 2. folder mode
    folder = tmp_path / "sounds_folder"
    folder.mkdir()
    f1 = _make_file(folder, "1.wav")
    f2 = _make_file(folder, "2.mp3")
    _make_file(folder, "ignored.txt")
    pack_folder = {"kind": "folder", "id": "custom", "path": str(folder)}
    candidates_folder = click_sound.resolve_click_sound_candidates(pack_folder)
    assert candidates_folder == [f1, f2]
    # deterministic choose via seeded rng
    rng = random.Random(42)
    chosen = click_sound.choose_sound(candidates_folder, rng=rng)
    assert chosen in {f1, f2}

    # 3. empty list
    assert click_sound.choose_sound([]) is None

    # 4. builtin duck pack
    pack_duck = {"kind": "builtin", "id": "duck", "path": ""}
    duck_candidates = click_sound.resolve_click_sound_candidates(pack_duck)
    assert len(duck_candidates) >= 2
    assert any(c.name == "Ya1.mp3" for c in duck_candidates)
    assert any(c.name == "Ya2.mp3" for c in duck_candidates)


def test_window_play_click_sound_uses_pack(monkeypatch, tmp_path):
    custom = _make_file(tmp_path, "custom.wav")
    cfg_dir = tmp_path / "data"
    cfg_dir.mkdir()

    class Cfg:
        dir = cfg_dir

        def get(self, key, default=None):
            if key == "click_sound_pack":
                return {"kind": "file", "id": "custom", "path": str(custom)}
            if key == "click_sound_volume":
                return 0.8
            return default

    class FakePet:
        click_sound_enabled = True
        cfg = Cfg()

    sent = []

    def capture(path, volume=1.0):
        sent.append((path, volume))
        return True

    monkeypatch.setattr(window_mod, "play_sound", capture)
    window_mod.PetWindow._play_click_sound(FakePet())
    assert sent == [(custom, 0.8)]

