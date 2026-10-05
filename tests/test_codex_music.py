"""Transport remains bound to the selected music session across pause/resume."""

import asyncio
from types import SimpleNamespace


def test_paused_music_is_retained_and_missing_target_does_not_start_replay(monkeypatch):
    from pet import ytmusic, now_playing

    music = SimpleNamespace(source_app_user_model_id="YouTubeMusic", get_playback_info=lambda: SimpleNamespace(playback_status=5))
    replay = SimpleNamespace(source_app_user_model_id="Microsoft.XboxGameOverlay", get_playback_info=lambda: SimpleNamespace(playback_status=4))
    sessions = [replay, music]

    class Manager:
        @staticmethod
        async def request_async():
            return SimpleNamespace(get_sessions=lambda: list(sessions))

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: Manager)
    monkeypatch.setattr(ytmusic, "auto_connect_enabled", lambda: True)
    monkeypatch.setattr(ytmusic, "_controlled_app_id", "")
    assert asyncio.run(ytmusic.pick_control_session()) is music
    assert ytmusic._controlled_app_id == "YouTubeMusic"
    sessions.remove(music)
    assert asyncio.run(ytmusic.pick_control_session()) is None


def test_disabling_auto_connect_filters_browser_sessions(monkeypatch):
    from pet import ytmusic

    browser = SimpleNamespace(source_app_user_model_id="chrome.exe")
    player = SimpleNamespace(source_app_user_model_id="MusicPlayer.exe")
    manager = SimpleNamespace(get_sessions=lambda: [browser, player])
    assert ytmusic.music_sessions(manager, True) == [browser, player]
    assert ytmusic.music_sessions(manager, False) == [player]
