# -*- coding: utf-8 -*-
"""点击音效播放、缓存与包解析测试。"""
from __future__ import annotations

import random
import wave
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QTimer

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
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)

    path_wav = _make_file(tmp_path, "click.wav")
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert len(FakeQtEffect.instances) >= 1
    assert FakeQtEffect.instances[-1].play_count == 2
    assert FakeQtEffect.instances[-1].volumes == [0.5, 0.5]


def test_mp3_decode_failure_falls_back_to_player_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: tmp_path / "cache")

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is True
    assert len(click_sound._pool._qt_player_pool) == 4
    assert click_sound._pool._qt_player_pool[0][0].played is True


def test_nonwav_qt_unavailable_on_windows_skips_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: None)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is False


def test_mp3_second_click_uses_decoded_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
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

    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: (Decoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect))
    assert click_sound.play_sound(path) is True
    assert list(cache_dir.glob("*.wav"))
    assert click_sound.play_sound(path) is True
    assert FakeQtEffect.instances[-1].play_count == 1


def test_warm_player_pool_precreates_qt_players(monkeypatch):
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    click_sound._warm_player_pool()

    assert len(click_sound._pool._qt_player_pool) == 4


def test_warm_click_sound_effects_precreates_wav_effect_and_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound._pool, "qt_available", lambda: True)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    wav = _make_file(tmp_path, "click.wav")
    pack = {"kind": "file", "id": "custom", "path": str(wav)}
    click_sound.warm_click_sound_effects(pack, data_dir=tmp_path)

    assert str(wav.resolve()) in click_sound._pool._qt_effects
    assert len(click_sound._pool._qt_player_pool) == 4


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


def test_resolve_click_sound_pair_duck_and_non_duck(monkeypatch, tmp_path):
    press = _make_file(tmp_path, "Ya1.mp3")
    release = _make_file(tmp_path, "Ya2.mp3")
    monkeypatch.setattr(click_sound, "resolve_click_sound_candidates", lambda pack, data_dir=None: [press, release])
    monkeypatch.setattr(click_sound, "_cache_path", lambda path: path.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (press, release)
    press.with_suffix(".wav").write_bytes(b"")
    release.with_suffix(".wav").write_bytes(b"")
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (
        press.with_suffix(".wav"), release.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "file", "id": "custom"}) is None


def test_press_sound_stops_release_and_restarts_press(monkeypatch, tmp_path):
    pair = (_make_file(tmp_path, "press.wav"), _make_file(tmp_path, "release.wav"))
    release_effect = SimpleNamespace(
        stopped=False, stop=lambda: setattr(release_effect, "stopped", True),
        setLoopCount=lambda count: None, setVolume=lambda volume: None,
    )
    played = []
    monkeypatch.setattr(click_sound._pool, "effect_for", lambda path: release_effect)
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: played.append((path, volume)) or True)
    assert click_sound.play_press_sound(pair, 0.6) is True
    assert release_effect.stopped is True
    assert played == [(pair[0], 0.6)]


def test_release_sound_schedules_press_tail(monkeypatch, tmp_path):
    press = tmp_path / "press.wav"
    release = tmp_path / "release.wav"
    for path in (press, release):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(1000)
            out.writeframes(b"\0\0" * 1000)
    calls = []
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: calls.append((path, volume)) or True)
    monkeypatch.setattr(click_sound.time, "monotonic", lambda: 0.2)
    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    click_sound.play_release_sound((press, release), 0.5, press_started_at=0.0)
    assert scheduled and scheduled[0][0] == 700
    scheduled[0][1]()
    assert calls == [(release, 0.5)]


def test_second_pool_instance_state_is_isolated_from_singleton(tmp_path):
    """批4：第二个 ClickSoundPool 实例与模块单例 _pool 的状态互相隔离。

    类方法一律走 self.<method>()，实例的可变状态（音效/解码器/播放器池/
    时长缓存/配对状态）必须各自独立。不造 Qt 对象，仅轻量状态断言。
    """
    other = click_sound.ClickSoundPool()
    singleton = click_sound._pool

    # 1) 可变状态容器不是同一对象
    for attr in ("_qt_effects", "_qt_decoders", "_qt_player_pool",
                 "_wav_duration_cache", "_click_pair_state"):
        assert getattr(other, attr) is not getattr(singleton, attr), attr

    # 2) 直接写互不串
    other._wav_duration_cache["a.wav"] = 1.5
    assert "a.wav" not in singleton._wav_duration_cache
    other._qt_effects["k"] = object()
    assert "k" not in singleton._qt_effects
    index_before = singleton._qt_player_index
    other._qt_player_index = 7
    assert singleton._qt_player_index == index_before

    # 3) 经实例方法写入只落在第二个实例：批4 前 play_with_effect 会经实例
    #    方法 effect_for 把音效写进单例 _pool._qt_effects（实例间串写）。
    wav = tmp_path / "tick.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(1000)
        out.writeframes(b"\0\0" * 100)

    assert other.wav_duration(wav) == 0.1
    assert str(wav.resolve()) in other._wav_duration_cache
    assert str(wav.resolve()) not in singleton._wav_duration_cache

    class _FakeEffect:
        def __init__(self):
            self.source = None

        def setSource(self, source):
            self.source = source

        def setVolume(self, volume):
            pass

        def play(self):
            pass

    other.qt_multimedia_classes = lambda: (None, None, None, None, _FakeEffect)
    assert other.play_with_effect(wav, 0.5) is True
    assert str(wav.resolve()) in other._qt_effects
    assert str(wav.resolve()) not in singleton._qt_effects


def test_click_sound_immediate_toggle_in_dialog_affects_pet_window(tmp_path, monkeypatch):
    """回归测试：设置对话框中即时关闭点击音效，桌宠窗口点击立即不播放。"""
    from pet.config import Config
    from pet.window import PetWindow
    from pet.modern_settings_dialog import ModernSettingsDialog
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    config.set("click_sound_enabled", True)

    win = PetWindow.__new__(PetWindow)
    win.cfg = config

    # 初始状态下属性读取 cfg 为 True
    assert win.click_sound_enabled is True

    # 模拟设置对话框即时关闭
    monkeypatch.setattr("pet.modern_settings_dialog.autostart_mod.is_enabled", lambda: False)
    dialog = ModernSettingsDialog(config, include_ai=False)
    assert dialog.click_sound_check.isChecked() is True

    played = []
    monkeypatch.setattr("pet.window.play_sound", lambda *a, **k: played.append(a))
    monkeypatch.setattr("pet.window.play_press_sound", lambda *a, **k: played.append(a))

    # 关闭点击音效
    dialog.click_sound_check.setChecked(False)

    # 验证即时写回 config 且 PetWindow 读到 False
    assert config.get("click_sound_enabled") is False
    assert win.click_sound_enabled is False

    # 触发播放点击音效
    win._play_click_sound()
    assert played == []

    dialog.close()
    app.processEvents()


