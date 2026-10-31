from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.media import audio_output
from petcore.media.audio_output import AudioOutputRouter, AudioRoute
from petcore.media.sound import SoundPlayer
from petcore.ui.settings.page_voice import VoicePage


class _Config:
    def __init__(self, **values) -> None:
        self.values = dict(values)

    def get(self, key: str, default=None):
        return self.values.get(key, default)


class _Device:
    def __init__(self, device_id: str, name: str) -> None:
        self._id = device_id
        self._name = name

    def id(self) -> bytes:
        return self._id.encode("utf-8")

    def description(self) -> str:
        return self._name

    def isNull(self) -> bool:
        return False


class _Signal:
    def __init__(self) -> None:
        self.slots = []

    def connect(self, callback) -> None:
        self.slots.append(callback)

    def disconnect(self, callback) -> None:
        if callback in self.slots:
            self.slots.remove(callback)

    def emit(self) -> None:
        for callback in list(self.slots):
            callback()


class _FakeMedia:
    devices: list = []
    default = None

    def __init__(self) -> None:
        self.audioOutputsChanged = _Signal()

    @classmethod
    def audioOutputs(cls):
        return list(cls.devices)

    @classmethod
    def defaultAudioOutput(cls):
        return cls.default


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def fake_media(monkeypatch):
    _FakeMedia.devices = [_Device("dev-a", "耳机 A"), _Device("dev-b", "扬声器 B")]
    _FakeMedia.default = _FakeMedia.devices[0]
    monkeypatch.setattr(audio_output, "_media_devices_class", lambda: _FakeMedia)
    return _FakeMedia


def test_route_follows_system_default(fake_media) -> None:
    router = AudioOutputRouter(_Config())
    try:
        route = router.route()
        assert route.mode == "follow"
        assert route.audible is True
        assert route.device is fake_media.devices[0]
        assert route.device_id == "dev-a"
        assert route.device_name == "耳机 A"
    finally:
        router.close()


def test_route_uses_manual_device(fake_media) -> None:
    router = AudioOutputRouter(_Config(audio_output_device="dev-b"))
    try:
        route = router.route()
        assert route.mode == "manual"
        assert route.device is fake_media.devices[1]
        assert route.device_id == "dev-b"
    finally:
        router.close()


def test_missing_manual_device_is_muted_not_fallback(fake_media) -> None:
    router = AudioOutputRouter(
        _Config(
            audio_output_device="dev-gone",
            audio_output_device_name="旧耳机",
        )
    )
    try:
        route = router.route()
        assert route.mode == "missing"
        assert route.audible is False
        assert route.device is None
        assert route.device_name == "旧耳机"
    finally:
        router.close()


def test_hot_plug_refreshes_subscribers(fake_media) -> None:
    router = AudioOutputRouter(_Config())
    seen = []
    unsubscribe = router.subscribe(seen.append)
    try:
        assert router.route().device_id == "dev-a"
        fake_media.default = fake_media.devices[1]
        fake_media.devices = list(fake_media.devices)
        router._media_devices.audioOutputsChanged.emit()
        assert seen
        assert seen[-1].device_id == "dev-b"
    finally:
        unsubscribe()
        router.close()


def test_unavailable_qt_keeps_old_behaviour(monkeypatch) -> None:
    monkeypatch.setattr(audio_output, "_media_devices_class", lambda: None)
    router = AudioOutputRouter(_Config(audio_output_device="anything"))
    try:
        route = router.route()
        assert route.mode == "unavailable"
        assert route.audible is True
        assert route.device is None
    finally:
        router.close()


def test_list_outputs_marks_default(fake_media) -> None:
    outputs = audio_output.list_outputs()
    assert outputs == [
        ("dev-a", "耳机 A", True),
        ("dev-b", "扬声器 B", False),
    ]


def test_sound_player_muted_route_never_plays(tmp_path: Path) -> None:
    player = SoundPlayer(route_provider=lambda: AudioRoute(None, False, "missing"))
    assert player.play(tmp_path / "click.wav") is False


def test_sound_player_apply_route_drops_caches() -> None:
    player = SoundPlayer(route_provider=lambda: AudioRoute(None, True, "follow"))
    player._cache["x"] = object()
    player._players["y"] = (object(), object())
    player.apply_route(AudioRoute(None, True, "follow"))
    assert player._cache == {}
    assert player._players == {}


class _Host:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.config = Config(self.root)
        self.ports = SimpleNamespace(speech=None)


def _page(tmp_path: Path) -> VoicePage:
    return VoicePage(_Host(tmp_path))


@pytest.fixture()
def fake_outputs(monkeypatch):
    monkeypatch.setattr(
        audio_output,
        "list_outputs",
        lambda: [("dev-a", "耳机 A", True), ("dev-b", "扬声器 B", False)],
    )


def test_voice_page_lists_output_devices_and_writes_selection(app, tmp_path, fake_outputs) -> None:
    host = _Host(tmp_path)
    page = VoicePage(host)
    try:
        assert page.output_device.itemData(0) == ""
        assert page.output_device.findData("dev-a") >= 0
        index = page.output_device.findData("dev-b")
        page.output_device.setCurrentIndex(index)
        assert host.config.get("audio_output_device") == "dev-b"
        assert host.config.get("audio_output_device_name") == "扬声器 B"
        assert "使用「扬声器 B」" in page.output_status.text()
    finally:
        page.deleteLater()


def test_voice_page_missing_saved_device_is_visible(app, tmp_path, fake_outputs) -> None:
    host = _Host(tmp_path)
    host.config.set("audio_output_device", "dev-gone")
    host.config.set("audio_output_device_name", "旧耳机")
    page = VoicePage(host)
    try:
        index = page.output_device.findData("dev-gone")
        assert index >= 0
        assert "不可用" in page.output_device.itemText(index)
        assert "不会从默认设备外放" in page.output_status.text()
    finally:
        page.deleteLater()


def test_voice_page_discard_restores_output_device(app, tmp_path, fake_outputs) -> None:
    host = _Host(tmp_path)
    host.config.set("audio_output_device", "dev-a")
    host.config.set("audio_output_device_name", "耳机 A")
    page = VoicePage(host)
    try:
        page.output_device.setCurrentIndex(page.output_device.findData("dev-b"))
        assert host.config.get("audio_output_device") == "dev-b"
        page.discard_changes()
        assert host.config.get("audio_output_device") == "dev-a"
        assert page.output_device.currentData() == "dev-a"
    finally:
        page.deleteLater()


def test_voice_page_search_covers_output_words(app, tmp_path, fake_outputs) -> None:
    page = _page(tmp_path)
    try:
        words = page.keywords()
        for word in ("音频输出", "耳机", "外放"):
            assert word in words
    finally:
        page.deleteLater()


def test_sound_player_provider_failure_stays_muted() -> None:
    def broken():
        raise RuntimeError("device enumerator failed")

    player = SoundPlayer(route_provider=broken)
    player._sync_route()
    assert player._audible() is False
