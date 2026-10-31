"""音乐识别的输出设备：默认跟随、手选、端点缓存键（不碰真实 COM）。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_audio_device_test_host"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
audio_sessions = importlib.import_module(f"{HOST_PKG}.audio_sessions")
runtime_mod = importlib.import_module(f"{HOST_PKG}.runtime")


class _Manager:
    def __init__(self, name: str) -> None:
        self.name = name


class _RawDevice:
    def __init__(self, name: str) -> None:
        self.AudioSessionManager = _Manager(name)


class _Enumerator:
    def __init__(self, calls: list[str], available: set[str]) -> None:
        self.calls = calls
        self.available = available

    def GetDevice(self, device_id: str):
        self.calls.append(device_id)
        return _RawDevice(device_id) if device_id in self.available else None


def test_session_manager_uses_the_selected_endpoint(monkeypatch) -> None:
    from pycaw.utils import AudioUtilities

    calls: list[str] = []
    available = {"dev-a", "dev-b"}
    enumerator = _Enumerator(calls, available)
    monkeypatch.setattr(AudioUtilities, "GetDeviceEnumerator", staticmethod(lambda: enumerator))
    monkeypatch.setattr(
        AudioUtilities,
        "CreateDevice",
        staticmethod(lambda raw: raw),
    )
    monkeypatch.setattr(
        AudioUtilities,
        "GetSpeakers",
        staticmethod(lambda: _RawDevice("default")),
    )
    audio_sessions.reset()
    try:
        assert audio_sessions._session_manager("dev-a").name == "dev-a"
        assert audio_sessions._session_manager("dev-a").name == "dev-a"  # 同设备复用缓存
        assert calls == ["dev-a"]
        assert audio_sessions._session_manager("dev-b").name == "dev-b"
        assert calls == ["dev-a", "dev-b"]
        assert audio_sessions._session_manager("dev-a").name == "dev-a"  # 换回要重建
        assert calls[-1] == "dev-a"
        assert audio_sessions._session_manager("").name == "default"
    finally:
        audio_sessions.reset()


def test_manual_device_missing_falls_back_to_default(monkeypatch) -> None:
    configured = {"value": "dev-gone"}
    monkeypatch.setattr(
        audio_sessions, "endpoint_exists", lambda device: device != "dev-gone"
    )
    monkeypatch.setattr(audio_sessions, "default_endpoint_id", lambda: "default-1")
    monkeypatch.setattr(audio_sessions, "reset", lambda *args: None)
    runtime = SimpleNamespace(
        _config_value=lambda key, default: configured["value"]
        if key == "music_audio_device"
        else default,
        _last_default_audio="",
        _next_default_audio_check=0.0,
        _next_manual_audio_check=0.0,
        _audio_device_fallback_logged=False,
    )
    assert runtime_mod.MusicRuntime._audio_device_id(runtime) == ""
    assert runtime._audio_device_fallback_logged is True
    # 回退决定要撑到下一次 30s 检查，不能只在检查那一拍生效。
    assert runtime_mod.MusicRuntime._audio_device_id(runtime) == ""

    configured["value"] = "dev-ok"
    runtime._next_manual_audio_check = 0.0
    assert runtime_mod.MusicRuntime._audio_device_id(runtime) == "dev-ok"
    assert runtime._audio_device_fallback_logged is False


def test_following_default_resets_manager_when_endpoint_changes(monkeypatch) -> None:
    current = {"id": "default-a"}
    resets: list[bool] = []
    monkeypatch.setattr(audio_sessions, "default_endpoint_id", lambda: current["id"])
    monkeypatch.setattr(audio_sessions, "reset", lambda *args: resets.append(True))
    runtime = SimpleNamespace(
        _config_value=lambda key, default: default,
        _last_default_audio="",
        _next_default_audio_check=0.0,
        _next_manual_audio_check=0.0,
        _audio_device_fallback_logged=False,
    )
    assert runtime_mod.MusicRuntime._audio_device_id(runtime) == ""
    assert runtime._last_default_audio == "default-a"
    assert resets == []

    runtime._next_default_audio_check = 0.0
    current["id"] = "default-b"
    assert runtime_mod.MusicRuntime._audio_device_id(runtime) == ""
    assert resets == [True]
    assert runtime._last_default_audio == "default-b"


def test_snapshot_receives_the_selected_device(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        audio_sessions, "snapshot", lambda device="": seen.append(device) or ()
    )
    runtime = SimpleNamespace(_audio_device_id=lambda: "dev-x")
    runtime_mod.MusicRuntime._sample_audio(runtime)
    assert seen == ["dev-x"]
