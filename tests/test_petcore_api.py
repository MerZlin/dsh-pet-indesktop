from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import ssl
import sys
import time
import urllib.error
from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLineEdit

import petcore.api.probe as probe_module
import petcore.ui.external_links as external_links
import petcore.ui.settings.page_api as page_api
from petcore.api.credentials import KeyringCredentials
from petcore.api.probe import ProbeResult, probe_text
from petcore.api.spec import ApiService, validate_endpoint
from petcore.api.store import ApiStore
from petcore.config import Config
from petcore.contracts.api import ApiMetadata, ApiRequest
from petcore.ui.settings.page_api import ApiSettingsPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeHost:
    def __init__(self, root: Path):
        self.root = root
        self.config = Config(root)


class _FakeBackend:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _BrokenBackend:
    def get_password(self, service: str, username: str):
        raise RuntimeError("no keyring")

    def set_password(self, service: str, username: str, value: str) -> None:
        raise RuntimeError("no keyring")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("no keyring")


def _service(**overrides) -> ApiService:
    values = {
        "service_id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.example.com",
        "model": "test-model",
    }
    values.update(overrides)
    return ApiService(**values)


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://api.example.com", code, "error", headers, None)


class _Response:
    def __init__(self, status: int = 200):
        self.status = status
        self.closed = False

    def read(self, *args):
        raise AssertionError("probe must not read the response body")

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------- endpoints


@pytest.mark.parametrize(
    "value",
    [
        "https://api.deepseek.com",
        "https://api.example.com/v1",
        "http://localhost",
        "http://localhost:8080",
        "http://127.0.0.1:11434",
        "http://[::1]:8000",
    ],
)
def test_validate_endpoint_accepts(value: str) -> None:
    assert validate_endpoint(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://api.example.com",
        "http://10.0.0.2:8000",
        "ftp://api.example.com",
        "api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1?debug=1",
        "https://api.example.com/#frag",
        "https://api.example.com:abc",
        "https://api.example.com:70000",
        "https://api.example.com:0",
        "https://api .example.com",
        "https://api.example.com\\v1",
        "https://" + "a" * 2100,
    ],
)
def test_validate_endpoint_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoint(value)


def test_service_validate_normalizes_and_validates() -> None:
    normalized = _service(
        service_id=" deepseek ",
        name=" DeepSeek ",
        base_url="https://api.deepseek.com/",
    ).validate()
    assert normalized.service_id == "deepseek"
    assert normalized.name == "DeepSeek"
    assert normalized.base_url == "https://api.deepseek.com"

    for bad in (
        _service(service_id="bad id"),
        _service(name=" "),
        _service(chat_path="v1/chat"),
        _service(timeout=0),
        _service(timeout=301),
        _service(balance_protocol="openai"),
    ):
        with pytest.raises(ValueError):
            bad.validate()


def test_service_metadata_has_no_credentials() -> None:
    metadata = _service(model="m", vision_model="v").metadata(version="7")
    assert isinstance(metadata, ApiMetadata)
    assert metadata.model == "m"
    assert metadata.version == "7"
    assert not hasattr(metadata, "api_key")
    assert "api_key" not in metadata.__slots__


def test_api_request_repr_hides_key() -> None:
    request = ApiRequest(metadata=_service().metadata(), api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(request)
    assert request.base_url == "https://api.example.com"
    assert request.model == "test-model"


# ------------------------------------------------------------------- store


def test_store_round_trip_and_file_has_no_key(tmp_path: Path) -> None:
    store = ApiStore(tmp_path)
    assert store.services() == ()
    assert store.version() == 0

    saved = store.upsert(_service(base_url="https://api.deepseek.com/"))
    assert saved.base_url == "https://api.deepseek.com"
    assert store.version() == 1

    text = (tmp_path / "api.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["schema_version"] == 1
    assert data["version"] == 1
    assert "api_key" not in text
    assert "sk-" not in text
    assert not list(tmp_path.glob("*.tmp"))

    reopened = ApiStore(tmp_path)
    assert reopened.services() == (saved,)
    assert reopened.get("deepseek") == saved
    assert reopened.version() == 1


def test_store_orders_by_id_and_notifies(tmp_path: Path) -> None:
    store = ApiStore(tmp_path)
    events: list[int] = []
    unsubscribe = store.subscribe(lambda: events.append(store.version()))
    store.upsert(_service(service_id="zeta"))
    store.upsert(_service(service_id="alpha"))
    assert [item.service_id for item in ApiStore(tmp_path).services()] == ["alpha", "zeta"]
    assert events == [1, 2]

    assert store.remove("alpha") is True
    assert store.remove("alpha") is False
    assert events == [1, 2, 3]
    unsubscribe()
    store.upsert(_service(service_id="beta"))
    assert len(events) == 3


def test_store_quarantines_corrupt_file_once(tmp_path: Path) -> None:
    path = tmp_path / "api.json"
    path.write_text("{not json", encoding="utf-8")
    store = ApiStore(tmp_path)
    assert store.services() == ()
    backup = tmp_path / "api.json.bak"
    assert backup.read_text(encoding="utf-8") == "{not json"

    store.upsert(_service())
    path.write_text("[]", encoding="utf-8")
    assert ApiStore(tmp_path).services() == ()
    assert backup.read_text(encoding="utf-8") == "{not json"


def test_store_drops_unknown_fields_such_as_keys(tmp_path: Path) -> None:
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "services": {
                    "deepseek": {
                        "name": "DeepSeek",
                        "base_url": "https://api.example.com",
                        "api_key": "sk-leaked",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = ApiStore(tmp_path)
    assert store.get("deepseek") is not None
    store.upsert(_service(service_id="other"))
    assert "sk-leaked" not in path.read_text(encoding="utf-8")


# ------------------------------------------------------------- credentials


def test_keyring_credentials_round_trip_with_backend() -> None:
    backend = _FakeBackend()
    credentials = KeyringCredentials(service="dsh-pet-core", backend=backend)
    assert credentials.available() is True

    credentials.set("core.api", "deepseek", "sk-1")
    assert credentials.get("core.api", "deepseek") == "sk-1"
    assert credentials.persisted("core.api", "deepseek") is True
    assert backend.values[("dsh-pet-core", "core.api:deepseek")] == "sk-1"

    credentials.delete("core.api", "deepseek")
    assert credentials.get("core.api", "deepseek") is None
    assert credentials.persisted("core.api", "deepseek") is False


def test_keyring_credentials_fall_back_to_memory_on_failure() -> None:
    credentials = KeyringCredentials(backend=_BrokenBackend())
    credentials.set("core.api", "deepseek", "sk-2")
    assert credentials.get("core.api", "deepseek") == "sk-2"
    assert credentials.available() is False
    assert credentials.persisted("core.api", "deepseek") is False
    credentials.delete("core.api", "deepseek")
    assert credentials.get("core.api", "deepseek") is None


def test_keyring_credentials_without_module_use_memory(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    credentials = KeyringCredentials()
    assert credentials.available() is False
    credentials.set("core.api", "svc", "sk-3")
    assert credentials.get("core.api", "svc") == "sk-3"
    assert credentials.persisted("core.api", "svc") is False


# ------------------------------------------------------------------- probe


def test_probe_success_posts_minimal_request(monkeypatch) -> None:
    seen: dict = {}

    def fake_urlopen(request, *, timeout, context=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["payload"] = json.loads(request.data)
        seen["timeout"] = timeout
        seen["context"] = context
        return _Response(200)

    monkeypatch.setattr(probe_module, "urlopen", fake_urlopen)
    result = probe_text(_service(), "sk-test", timeout=5)

    assert result == ProbeResult("ok", http_status=200)
    assert result.ok is True
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["payload"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1,
        "stream": False,
    }
    assert seen["timeout"] == 5
    assert seen["context"] is None

    probe_text(_service(), "sk-test")
    assert seen["timeout"] == 60


def test_probe_disables_ssl_verification_when_asked(monkeypatch) -> None:
    seen: dict = {}

    def fake_urlopen(request, *, timeout, context=None):
        seen["context"] = context
        return _Response(200)

    monkeypatch.setattr(probe_module, "urlopen", fake_urlopen)
    assert probe_text(_service(verify_ssl=False), "sk") == ProbeResult("ok", 200)
    context = seen["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_probe_maps_http_errors(monkeypatch) -> None:
    def unauthorized(request, *, timeout, context=None):
        raise _http_error(401)

    monkeypatch.setattr(probe_module, "urlopen", unauthorized)
    assert probe_text(_service(), "sk-bad") == ProbeResult("auth", http_status=401)

    def limited(request, *, timeout, context=None):
        raise _http_error(429, retry_after="2.5")

    monkeypatch.setattr(probe_module, "urlopen", limited)
    result = probe_text(_service(), "sk")
    assert result.reason == "rate_limit"
    assert result.http_status == 429
    assert result.retry_after == 2.5


def test_probe_maps_transport_errors(monkeypatch) -> None:
    def timed_out(request, *, timeout, context=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(probe_module, "urlopen", timed_out)
    assert probe_text(_service(), "sk") == ProbeResult("timeout")

    def unreachable(request, *, timeout, context=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(probe_module, "urlopen", unreachable)
    assert probe_text(_service(), "sk") == ProbeResult("network")

    def url_timeout(request, *, timeout, context=None):
        raise urllib.error.URLError(TimeoutError("slow"))

    monkeypatch.setattr(probe_module, "urlopen", url_timeout)
    assert probe_text(_service(), "sk") == ProbeResult("timeout")

    def bad_certificate(request, *, timeout, context=None):
        raise ssl.SSLError("bad certificate")

    monkeypatch.setattr(probe_module, "urlopen", bad_certificate)
    assert probe_text(_service(), "sk") == ProbeResult("network")


def test_probe_requires_model() -> None:
    with pytest.raises(ValueError, match="api_model_required"):
        probe_text(_service(model=""), "sk")


# -------------------------------------------------------------- settings page


def test_settings_page_constructs_without_keyring(app, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        assert page.title == "API 与账户"
        assert page.credentials.available() is False
        assert "内存" in page._keyring_note.text()
        assert page.key_edit.echoMode() == QLineEdit.EchoMode.Password
        assert page.key_edit.placeholderText() == "留空表示不修改"
        assert page.timeout_spin.minimum() == 1 and page.timeout_spin.maximum() == 300
        assert page._draft_id is not None
        assert page.dirty() is False
        assert page.keywords()
        assert any("密钥" in item for item in page.keywords())
    finally:
        page.dispose()
        page.deleteLater()


def test_settings_page_saves_form_without_storing_key_in_json(
    app, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.name_edit.setText("DeepSeek")
        page.base_url_edit.setText("https://api.deepseek.com/")
        page.model_edit.setText("deepseek-chat")
        page.vision_edit.setText("deepseek-vl")
        page.timeout_spin.setValue(90)
        page.balance_combo.setCurrentIndex(page.balance_combo.findData("deepseek"))
        page.key_edit.setText("sk-local")
        assert page.dirty() is True

        page.confirm_save()

        services = page.store.services()
        assert len(services) == 1
        saved = services[0]
        assert saved.name == "DeepSeek"
        assert saved.base_url == "https://api.deepseek.com"
        assert saved.model == "deepseek-chat"
        assert saved.vision_model == "deepseek-vl"
        assert saved.timeout == 90
        assert saved.balance_protocol == "deepseek"
        assert page.credentials.get("core.api", saved.service_id) == "sk-local"
        assert page.key_edit.text() == ""
        assert "已保存" in page.status_label.text()
        assert page.dirty() is False

        text = (tmp_path / "api.json").read_text(encoding="utf-8")
        assert "sk-local" not in text
        assert not (tmp_path / "config.json").exists()

        page.name_edit.setText("改名")
        assert page.dirty() is True
        page.discard_changes()
        assert page.name_edit.text() == "DeepSeek"
        assert page.dirty() is False
    finally:
        page.dispose()
        page.deleteLater()


def test_settings_page_reports_validation_errors_inline(
    app, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.name_edit.setText("Remote")
        page.base_url_edit.setText("http://api.example.com")
        assert page.save_service() is False
        assert page.store.services() == ()
        assert "https" in page.status_label.text()
    finally:
        page.dispose()
        page.deleteLater()


def test_settings_page_probe_runs_off_thread(app, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(
        page_api,
        "probe_text",
        lambda service, key, **kwargs: ProbeResult("rate_limit", 429, 3.0),
    )
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.name_edit.setText("DeepSeek")
        page.base_url_edit.setText("https://api.deepseek.com")
        page.model_edit.setText("deepseek-chat")
        page.key_edit.setText("sk-test")
        page.test_connection()
        deadline = time.monotonic() + 15
        while page._probe_running and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert page._probe_running is False
        text = page.status_label.text()
        assert "HTTP 429" in text
        assert "频繁" in text
        assert "3 秒" in text
    finally:
        page.dispose()
        page.deleteLater()


def test_invalid_api_form_keeps_the_settings_dialog_open(
    app, tmp_path: Path, monkeypatch
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_petcore_settings as fixtures
    from PySide6.QtWidgets import QMessageBox

    from petcore.ui.settings.dialog import SettingsDialog

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: warnings.append(str(args[2]))
    )
    host = fixtures.FakeHost(tmp_path)
    dialog = SettingsDialog(host, initial_page="api")
    dialog.show()
    app.processEvents()
    page = dialog.stack.currentWidget()
    page.add_service()
    page.name_edit.setText("Bad")
    page.base_url_edit.setText("http://example.com")
    app.processEvents()
    assert page.dirty() is True

    dialog.accept()
    app.processEvents()
    assert dialog.isVisible() is True
    assert warnings
    assert page.store.services() == ()
    dialog.close()


def test_settings_page_prefills_deepseek_defaults(
    app, tmp_path: Path, monkeypatch
) -> None:
    """出厂预填：空 store 打开页面就是 DeepSeek 官方配置，只需填 Key。"""
    monkeypatch.setitem(sys.modules, "keyring", None)
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        assert page.store.services() == ()
        assert page.name_edit.text() == "DeepSeek"
        assert page.base_url_edit.text() == "https://api.deepseek.com"
        assert page.chat_path_edit.text() == "/v1/chat/completions"
        assert page.model_edit.text() == "deepseek-v4-flash"
        assert page.vision_edit.text() == "deepseek-v4-flash-vision-exp"
        assert page.balance_combo.currentData() == "deepseek"
        assert page.dirty() is False
        assert "预填" in page.status_label.text()
        assert page.api_url_field.text() == external_links.DEEPSEEK_API_URL
        assert page.open_api_page_button.text() == "打开 DeepSeek 开放平台"
    finally:
        page.dispose()
        page.deleteLater()


def test_settings_page_saves_with_only_an_api_key(
    app, tmp_path: Path, monkeypatch
) -> None:
    """小白只填 Key 就能存：规格走默认值，Key 只进钥匙串。"""
    monkeypatch.setitem(sys.modules, "keyring", None)
    backend = _FakeBackend()
    host = FakeHost(tmp_path)
    host.api_credentials = KeyringCredentials(service="dsh-pet-core", backend=backend)
    page = ApiSettingsPage(host)
    try:
        assert page.dirty() is False
        page.key_edit.setText("sk-only-key")
        assert page.dirty() is True
        assert page.save_service() is True

        services = page.store.services()
        assert len(services) == 1
        saved = services[0]
        assert saved.name == "DeepSeek"
        assert saved.base_url == "https://api.deepseek.com"
        assert saved.chat_path == "/v1/chat/completions"
        assert saved.model == "deepseek-v4-flash"
        assert saved.balance_protocol == "deepseek"

        text = (tmp_path / "api.json").read_text(encoding="utf-8")
        assert "api_key" not in text
        assert "sk-only-key" not in text
        assert not (tmp_path / "config.json").exists()

        assert page.credentials.available() is True
        assert page.credentials.get("core.api", saved.service_id) == "sk-only-key"
        assert backend.values[("dsh-pet-core", f"core.api:{saved.service_id}")] == "sk-only-key"
        assert page.dirty() is False
    finally:
        page.dispose()
        page.deleteLater()


def test_open_api_platform_button_uses_the_default_browser(
    app, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True
    )
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.open_api_page_button.click()
        app.processEvents()
        assert opened == [external_links.DEEPSEEK_API_URL]
    finally:
        page.dispose()
        page.deleteLater()


def test_api_menu_item_opens_the_api_settings_page() -> None:
    from unittest.mock import MagicMock

    from petcore.ui.menu_actions import core_items

    window = MagicMock()
    item = core_items(window)["api_settings"]
    assert item.callback is not None
    item.callback()
    window.open_settings.assert_called_once_with("api")
