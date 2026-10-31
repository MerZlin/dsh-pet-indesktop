"""语音设置页：后端链、音色、本地模型、密钥、缓存与试听。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.speech import SpeechProviderInfo
from petcore.ui.settings.page_voice import VoicePage


class FakeCredentials:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get(self, owner, key):
        return self.data.get((str(owner), str(key)))

    def set(self, owner, key, value):
        if value:
            self.data[(str(owner), str(key))] = str(value)
        else:
            self.data.pop((str(owner), str(key)), None)

    def delete(self, owner, key):
        self.data.pop((str(owner), str(key)), None)

    def available(self) -> bool:
        return True


class FakeCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._files: list[Path] = []

    def add(self, path: Path) -> None:
        self._files.append(path)

    def entries(self):
        return list(self._files)

    def discard(self, path) -> None:
        self._files = [item for item in self._files if item != path]


class FakeSpeechService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.credentials = FakeCredentials()
        self.cache = FakeCache(config.root)
        self.cache_dir_calls = 0
        self.local_available = False

    def providers(self):
        return (
            SpeechProviderInfo("edge", "edge-tts（微软 · 免 Key）", True, ("voice",), True, ""),
            SpeechProviderInfo("sapi", "Windows 本地语音", False, ("voice",), True, ""),
            SpeechProviderInfo("doubao", "豆包", True, ("voice", "api_key"), False, "auth"),
            SpeechProviderInfo(
                "local",
                "本地部署模型（HTTP）",
                False,
                (),
                self.local_available,
                "" if self.local_available else "config",
            ),
        )

    def secret_status(self, provider: str):
        return {"api_key": bool(self.credentials.get(f"speech.{provider}", "api_key"))}

    def set_secret(self, provider: str, field: str, value: str) -> None:
        self.credentials.set(f"speech.{provider}", field, value)

    def cache_dir(self):
        self.cache_dir_calls += 1
        return self.cache.root / "cache" / "tts"


class FakePort:
    def __init__(self) -> None:
        self.requests: list[tuple[object, str]] = []
        self.cancelled: list[str] = []

    def speak(self, request, *, owner="", slot=None, guard=None, on_done=None, on_error=None, on_finished=None):
        self.requests.append((request, owner))
        if on_done is not None:
            on_done(SimpleNamespace(provider=request.provider, voice=request.voice))
        return "speech-1"

    def cancel_owner(self, owner: str) -> None:
        self.cancelled.append(owner)


class FakeHost:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.config = Config(self.root)
        self.speech = FakeSpeechService(self.config)
        self.ports = SimpleNamespace(speech=FakePort())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _page(tmp_path: Path) -> VoicePage:
    return VoicePage(FakeHost(tmp_path))


def test_voice_page_lists_backends_and_selects_config(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.config.set("speech_backend", "doubao")
    page = VoicePage(host)
    try:
        assert page.title == "语音"
        values = [page.backend_combo.itemData(i) for i in range(page.backend_combo.count())]
        assert values == ["edge", "sapi", "doubao", "local"]
        assert page.backend_combo.currentData() == "doubao"
        assert "需密钥" in page.backend_combo.itemText(2), "不可用后端要标出原因"
    finally:
        page.deleteLater()


def test_backend_change_writes_live_and_swaps_voice_options(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        index = page.backend_combo.findData("edge")
        page.backend_combo.setCurrentIndex(index)
        assert host.config.get("speech_backend") == "edge"
        assert page.voice_combo.count() > 0, "edge 必须带内置音色列表"

        doubao = page.backend_combo.findData("doubao")
        page.backend_combo.setCurrentIndex(doubao)
        assert host.config.get("speech_backend") == "doubao"
        assert page.voice_combo.itemData(0) == "zh_female_peiqi_uranus_bigtts"

        page.voice_combo.setCurrentIndex(1)
        assert host.config.get("speech_doubao_voice") == "zh_female_xiaoxue_uranus_bigtts"
    finally:
        page.deleteLater()


def test_sapi_voice_uses_its_own_config_key(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        sapi = page.backend_combo.findData("sapi")
        page.backend_combo.setCurrentIndex(sapi)
        page.voice_combo.setEditText("Microsoft Huihui Desktop")

        assert host.config.get("speech_sapi_voice") == "Microsoft Huihui Desktop"
        assert host.config.get("speech_voice") == "", "SAPI 音色不能覆盖 Edge 的音色"
    finally:
        page.deleteLater()


def test_rate_and_volume_are_written_as_fractions(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.rate_slider.setValue(140)
        page.volume_slider.setValue(60)
        assert host.config.get("speech_rate") == pytest.approx(1.4)
        assert host.config.get("speech_volume") == pytest.approx(0.6)
        assert "1.40" in page.rate_label.text()
        assert page.dirty() is True
    finally:
        page.deleteLater()


def test_local_settings_are_written_and_normalized(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.local_endpoint.setText("127.0.0.1:9880/")
        page.local_protocol.setCurrentIndex(page.local_protocol.findData("simple"))
        page.local_path.setText("tts")
        page.local_model.setText("cosyvoice")
        page.local_format.setCurrentIndex(page.local_format.findData("wav"))
        page.local_timeout.setValue(45)

        assert host.config.get("speech_local_endpoint") == "http://127.0.0.1:9880"
        assert host.config.get("speech_local_protocol") == "simple"
        assert host.config.get("speech_local_path") == "/tts"
        assert host.config.get("speech_local_model") == "cosyvoice"
        assert host.config.get("speech_local_format") == "wav"
        assert host.config.get("speech_local_timeout") == pytest.approx(45.0)
    finally:
        page.deleteLater()


def test_preview_uses_current_backend_and_settings(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.config.set("speech_backend", "edge")
    page = VoicePage(host)
    try:
        page.rate_slider.setValue(120)
        page.voice_combo.setEditText("zh-CN-YunxiNeural")
        page.preview_button.click()

        request, owner = host.ports.speech.requests[-1]
        assert owner == "core.settings.voice"
        assert request.provider == "edge"
        assert request.voice == "zh-CN-YunxiNeural"
        assert request.rate == pytest.approx(1.2)
        assert "正在播放" in page.status_label.text()
    finally:
        page.deleteLater()


def test_local_test_button_previews_local_provider(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.speech.local_available = True
    page = VoicePage(host)
    try:
        page.local_test_button.click()
        request, _owner = host.ports.speech.requests[-1]
        assert request.provider == "local"
    finally:
        page.deleteLater()


def test_local_test_button_explains_unavailable_provider(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.local_test_button.click()
        assert host.ports.speech.requests == []
        assert "不可用" in page.status_label.text() and "未配置" in page.status_label.text()
    finally:
        page.deleteLater()


def test_secret_save_and_clear_never_echo(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.doubao_key.setText("sk-secret")
        page._save_secret("doubao", "api_key", page.doubao_key, page._doubao_secret_status[0])
        assert host.speech.credentials.get("speech.doubao", "api_key") == "sk-secret"
        assert page.doubao_key.text() == ""
        assert page._doubao_secret_status[0].text() == "已保存"

        page._clear_secret("doubao", "api_key", page.doubao_key, page._doubao_secret_status[0])
        assert host.speech.credentials.get("speech.doubao", "api_key") in (None, "")
        assert page._doubao_secret_status[0].text() == "未配置"
    finally:
        page.deleteLater()


def test_clear_cache_reports_count(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        cache = host.speech.cache
        cache.add(tmp_path / "a.mp3")
        cache.add(tmp_path / "b.mp3")
        page._clear_cache()
        assert cache.entries() == []
        assert "已清空 2" in page.cache_status.text()
    finally:
        page.deleteLater()


def test_discard_restores_every_changed_key(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.rate_slider.setValue(180)
        page.local_endpoint.setText("http://127.0.0.1:9999")
        assert page.dirty() is True

        page.discard_changes()

        assert page.dirty() is False
        assert host.config.get("speech_rate") == pytest.approx(1.0)
        assert host.config.get("speech_local_endpoint") == ""
        assert page.rate_slider.value() == 100
    finally:
        page.deleteLater()


def test_confirm_save_normalizes_endpoint_and_clears_dirty(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.local_endpoint.setText("127.0.0.1:9880/")
        assert page.dirty() is True

        page.confirm_save()

        assert page.local_endpoint.text() == "http://127.0.0.1:9880"
        assert host.config.get("speech_local_endpoint") == "http://127.0.0.1:9880"
        assert page.dirty() is False
    finally:
        page.deleteLater()


def test_invalid_endpoint_save_explains_instead_of_silently_wiping(
    app, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.local_endpoint.setText("http://127.0.0.1:9880#tts")
        page.confirm_save()

        assert host.config.get("speech_local_endpoint") == ""
        assert page.local_endpoint.text() == ""
        assert "地址无效" in page.status_label.text()
    finally:
        page.deleteLater()


def test_confirm_save_syncs_a_truncated_voice_and_clears_dirty(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        page.voice_combo.setEditText("v" * 70)
        assert page.dirty() is True

        page.confirm_save()

        assert host.config.get("speech_voice") == "v" * 64
        assert page.voice_combo.currentText() == "v" * 64
        assert page.dirty() is False
    finally:
        page.deleteLater()


def test_dispose_cancels_preview_owner(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    page.dispose()
    assert "core.settings.voice" in host.ports.speech.cancelled
    page.deleteLater()


def test_asr_settings_write_and_discard(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    page = VoicePage(host)
    try:
        assert page.asr_engine.currentData() == "sapi"
        assert page.asr_endpoint.isEnabled() is False

        page.asr_engine.setCurrentIndex(page.asr_engine.findData("http"))
        assert page.asr_endpoint.isEnabled() is True
        page.asr_endpoint.setText("http://127.0.0.1:8000/v1/audio/transcriptions")
        page.asr_model.setText("whisper-1")
        page.asr_auto_send.setChecked(True)
        page.asr_max_seconds.setValue(20)
        assert page.asr_device.count() >= 1
        assert page.asr_stream.isChecked() is True
        page.asr_stream.setChecked(False)
        page.asr_partial_interval.setValue(1200)

        assert host.config.get("asr_stream_enabled") is False
        assert host.config.get("asr_partial_interval_ms") == 1200
        assert host.config.get("asr_engine") == "http"
        assert host.config.get("asr_endpoint").endswith("/v1/audio/transcriptions")
        assert host.config.get("asr_model") == "whisper-1"
        assert host.config.get("asr_auto_send") is True
        assert host.config.get("asr_max_seconds") == 20
        assert page.dirty() is True

        page.discard_changes()
        assert host.config.get("asr_engine") == "sapi"
        assert page.asr_engine.currentData() == "sapi"
        assert page.asr_endpoint.text() == ""
        assert page.asr_endpoint.isEnabled() is False
        assert page.asr_stream.isChecked() is True
        assert page.asr_partial_interval.value() == 800
        assert page.dirty() is False
    finally:
        page.deleteLater()
