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


def test_real_qt_hover_does_not_restart_or_jump_during_status_updates(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/probe_codex_music_hover.py"
    result = subprocess.run([sys.executable, "-X", "utf8", str(script), "--output", str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=120,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1"),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "music-hover.json").read_text())["statusRefreshDoesNotJump"]
