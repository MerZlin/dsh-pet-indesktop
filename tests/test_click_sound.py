# -*- coding: utf-8 -*-
"""点击音效播放测试：WAV 走 winsound，非 WAV 走 QtMultimedia，绝不误触系统提示音。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from pet import click_sound
from pet import window as window_mod


class FakeQtPlayer:
    def __init__(self) -> None:
        self.stopped = False
        self.source = None
        self.played = False

    def stop(self) -> None:
        self.stopped = True

    def setSource(self, qurl) -> None:
        self.source = qurl

    def play(self) -> None:
        self.played = True


def _make_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"not-a-real-audio-file")
    return path


def test_wav_on_windows_uses_winsound(monkeypatch, tmp_path):
    # 替换 click_sound 模块的 os 引用而非全局 os.name：
    # 全局 os.name 被改成 "nt" 会让 pathlib.Path 在非 Windows 上创建
    # WindowsPath 而崩溃（Linux/macOS CI 实测 INTERNALERROR）。
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    calls = []

    fake = SimpleNamespace(
        PlaySound=lambda *args: calls.append(args) or True,
        SND_FILENAME=0x00020000,
        SND_ASYNC=0x0001,
    )
    monkeypatch.setitem(sys.modules, "winsound", fake)

    path = _make_file(tmp_path, "click.wav")
    assert click_sound.play_click_sound(path) is True
    assert calls, "WAV 应走 winsound.PlaySound"
    assert Path(calls[0][0]) == path


def test_mp3_on_windows_uses_qt_player_not_winsound(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    player = FakeQtPlayer()
    monkeypatch.setattr(click_sound, "_ensure_qt_player", lambda: player)

    winsound_calls = []
    fake = SimpleNamespace(
        PlaySound=lambda *args: winsound_calls.append(args) or True,
        SND_FILENAME=0x00020000,
        SND_ASYNC=0x0001,
    )
    monkeypatch.setitem(sys.modules, "winsound", fake)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is True
    assert winsound_calls == [], "MP3 绝不能走 winsound（会触发系统提示音）"
    assert player.stopped is True
    assert player.source is not None
    assert Path(player.source.toLocalFile()) == path
    assert player.played is True


def test_nonwav_qt_unavailable_on_windows_skips_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound, "_ensure_qt_player", lambda: None)

    winsound_calls = []
    fake = SimpleNamespace(PlaySound=lambda *args: winsound_calls.append(args) or True)
    monkeypatch.setitem(sys.modules, "winsound", fake)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is False
    assert winsound_calls == [], "Qt 不可用时 Windows 上也应静默失败，而不是 winsound 系统音"


def test_window_play_click_sound_prefers_custom_path(monkeypatch, tmp_path):
    monkeypatch.setattr(window_mod, "play_click_sound", lambda path: None)

    custom = _make_file(tmp_path, "custom.wav")
    cfg_dir = tmp_path / "data"
    cfg_dir.mkdir()
    default = cfg_dir / "sounds" / "click.wav"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_bytes(b"default")

    class Cfg:
        dir = cfg_dir

    class FakePet:
        click_sound_enabled = True
        click_sound_path = str(custom)
        cfg = Cfg()

    sent = []

    def capture(path):
        sent.append(path)

    monkeypatch.setattr(window_mod, "play_click_sound", capture)
    window_mod.PetWindow._play_click_sound(FakePet())
    assert sent == [custom], "自定义路径应优先于内置/数据目录音效"
