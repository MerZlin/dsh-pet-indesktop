"""Firefox's SMTC identity is its registered install ID, not firefox.exe.

Only the registry and SMTC OS boundaries are replaced; selection, browser
policy and the audio gate execute the real implementation.
"""
from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest

from pet import music_detect, now_playing
from tests.test_now_playing_media_type import MUSIC, UNKNOWN, _Session, _install_winrt

FIREFOX_ID = "308046B0AF4A39CB"
OTHER_INSTALL_ID = "E7CF176E110C211B"
REG_PATH = r"Software\Mozilla\Firefox\TaskBarIDs"


class Registry:
    HKEY_CURRENT_USER = 1
    HKEY_LOCAL_MACHINE = 2
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x100
    KEY_WOW64_32KEY = 0x200
    REG_SZ = 1

    def __init__(self, entries=None):
        self.entries = entries or {}
        self.opens = []

    @contextmanager
    def OpenKey(self, hive, path, reserved, access):
        assert path == REG_PATH
        assert reserved == 0
        assert access & self.KEY_READ == self.KEY_READ
        self.opens.append((hive, access))
        key = (hive, access & (self.KEY_WOW64_64KEY | self.KEY_WOW64_32KEY))
        if key not in self.entries:
            raise PermissionError("absent or inaccessible")
        yield self.entries[key]

    def EnumValue(self, values, index):
        if index >= len(values):
            raise OSError("no more values")
        return values[index]


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    loader = getattr(now_playing, "_firefox_app_ids", None)
    if loader:
        loader.cache_clear()
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)
    yield
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)
    if loader:
        loader.cache_clear()


def install_registry(monkeypatch, entries=None):
    registry = Registry(entries)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    return registry


@pytest.mark.parametrize("hive,view", [(1, 0x100), (1, 0x200), (2, 0x100), (2, 0x200)])
@pytest.mark.parametrize("app_id", [FIREFOX_ID, OTHER_INSTALL_ID])
def test_registered_install_ids_are_browsers(monkeypatch, hive, view, app_id):
    install_registry(monkeypatch, {(hive, view): [(r"D:\Firefox", app_id, 1)]})
    assert now_playing.is_browser_session("  " + app_id.lower() + "  ")


def test_unregistered_hex_and_non_browser_players_are_not_browsers(monkeypatch):
    install_registry(monkeypatch, {(1, 0x100): [(r"C:\Firefox", FIREFOX_ID, 1)]})
    for app_id in (OTHER_INSTALL_ID, "cloudmusic.exe", "lx-music-desktop.exe", "", None):
        assert not now_playing.is_browser_session(app_id)


def test_missing_registry_keeps_existing_browser_and_player_behavior(monkeypatch):
    install_registry(monkeypatch)
    assert now_playing.is_browser_session("Firefox.exe")
    assert now_playing.is_browser_session("MSEdge")
    assert not now_playing.is_browser_session(FIREFOX_ID)
    assert not now_playing.is_browser_session("player.exe")


def test_invalid_registry_values_are_ignored(monkeypatch):
    install_registry(monkeypatch, {(1, 0x100): [
        (r"C:\Firefox", 123, 4), (r"D:\Firefox", "", 1),
        (r"E:\Firefox", "   ", 1), (r"F:\Firefox", FIREFOX_ID, 1),
    ]})
    assert now_playing.is_browser_session(FIREFOX_ID)
    assert not now_playing.is_browser_session("123")
    assert not now_playing.is_browser_session("")


def test_registry_is_cached_not_read_every_poll(monkeypatch):
    registry = install_registry(monkeypatch, {(1, 0x100): [(r"C:\Firefox", FIREFOX_ID, 1)]})
    for _ in range(100):
        assert now_playing.is_browser_session(FIREFOX_ID)
    assert len(registry.opens) == 4


def test_non_windows_does_not_open_registry(monkeypatch):
    registry = install_registry(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    assert not now_playing.is_browser_session(FIREFOX_ID)
    assert now_playing.is_browser_session("chrome")
    assert registry.opens == []


def test_missing_winreg_degrades_without_rejecting_players(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert now_playing.is_browser_session("firefox.exe")
    assert not now_playing.is_browser_session(FIREFOX_ID)
    assert not now_playing.is_browser_session("player.exe")


@pytest.mark.parametrize("enabled", [False, True])
def test_firefox_install_id_obeys_lyrics_and_singing_switch(monkeypatch, enabled):
    install_registry(monkeypatch, {(1, 0x100): [(r"C:\Firefox", FIREFOX_ID, 1)]})
    _install_winrt(monkeypatch, [_Session(FIREFOX_ID, MUSIC, title="Browser video")])
    playback = now_playing.get_now_playing(allow_browser=enabled)
    assert (playback is not None) is enabled
    now_playing.refresh_session_probe_once()
    monkeypatch.setattr(music_detect, "_get_meter", lambda: SimpleNamespace(GetPeakValue=lambda: 0.5))
    music_detect.set_browser_media_enabled(enabled)
    assert music_detect.is_music_playing() is enabled


@pytest.mark.parametrize("browser_first", [False, True])
def test_rejected_firefox_does_not_hide_unknown_music_player(monkeypatch, browser_first):
    install_registry(monkeypatch, {(2, 0x200): [(r"D:\Firefox", OTHER_INSTALL_ID, 1)]})
    sessions = [_Session(OTHER_INSTALL_ID, MUSIC), _Session("lx-music-desktop.exe", UNKNOWN)]
    _install_winrt(monkeypatch, sessions if browser_first else sessions[::-1])
    playback = now_playing.get_now_playing(OTHER_INSTALL_ID, allow_browser=False)
    assert playback is not None
    assert playback.app_id == "lx-music-desktop.exe"
