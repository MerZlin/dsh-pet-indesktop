from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from petcore.api.headers import extra_headers, is_opencode_host
from petcore.api.models import list_models
from petcore.api.presets import PRESET_BY_ID, seed_services
from petcore.api.spec import ApiService
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


def _service(**overrides) -> ApiService:
    values = {
        "service_id": "test",
        "name": "Test",
        "base_url": "https://api.example.com",
        "model": "m",
    }
    values.update(overrides)
    return ApiService(**values)


# ---------------------------------------------------------------- 请求头


def test_opencode_headers_are_added_only_for_opencode() -> None:
    assert is_opencode_host("https://opencode.ai/inference")
    headers = extra_headers(
        "https://opencode.ai/inference",
        version="0.2.0",
        headers={"X-Test": "1"},
    )
    assert headers["User-Agent"] == "dsh-pet-core/0.2.0"
    assert headers["x-opencode-session"]
    assert headers["X-Test"] == "1"
    assert extra_headers("https://api.deepseek.com") == {}


def test_api_service_header_validation() -> None:
    assert _service(extra_headers={"X-Test": "1"}).validate().extra_headers == {
        "X-Test": "1"
    }
    with pytest.raises(ValueError) as caught:
        _service(extra_headers={"Authorization": "Bearer secret"}).validate()
    assert str(caught.value) == "api_header_secret"
    with pytest.raises(ValueError) as caught:
        _service(extra_headers={"Bad Header": "x"}).validate()
    assert str(caught.value) == "api_header_invalid"


def test_api_service_models_path_and_lan_http() -> None:
    with pytest.raises(ValueError) as caught:
        _service(models_path="v1/models").validate()
    assert str(caught.value) == "api_models_path_invalid"

    with pytest.raises(ValueError) as caught:
        _service(base_url="http://192.168.1.5:8000").validate()
    assert str(caught.value) == "api_endpoint_insecure"

    allowed = _service(
        base_url="http://192.168.1.5:8000", allow_lan_http=True
    ).validate()
    assert allowed.allow_lan_http is True


def test_build_request_carries_extra_headers() -> None:
    from petcore.api.chat import _build_request

    request = ApiRequest(
        metadata=ApiMetadata(
            service_id="opencode",
            name="OpenCode",
            base_url="https://opencode.ai/inference",
            chat_path="/openai/v1/chat/completions",
            model="deepseek-v4.1-flash-free",
            version="0.2.0",
            extra_headers={"X-Test": "1"},
        ),
        api_key="sk-test",
    )
    built = _build_request(request, {"model": "m", "messages": []})
    assert built.get_header("X-test") == "1"
    assert built.get_header("X-opencode-session")
    assert built.get_header("User-agent") == "dsh-pet-core/0.2.0"
    assert built.get_header("Authorization") == "Bearer sk-test"


# ---------------------------------------------------------------- 预设


def test_seed_services_keeps_deepseek_first() -> None:
    services = seed_services()
    assert [item.service_id for item in services] == ["deepseek", "opencode-free"]
    opencode = services[1]
    assert opencode.base_url == "https://opencode.ai/inference"
    assert opencode.chat_path == "/openai/v1/chat/completions"
    assert opencode.models_path == "/v1/models"
    assert opencode.model == "deepseek-v4.1-flash-free"
    assert PRESET_BY_ID["ollama"].base_url == "http://127.0.0.1:11434"
    assert PRESET_BY_ID["llamacpp"].base_url == "http://127.0.0.1:9931"


# ---------------------------------------------------------------- 模型列表


class _ModelsHandler(BaseHTTPRequestHandler):
    payload = b'{"data":[{"id":"a"},{"id":"b"}]}'

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args):
        return


@pytest.fixture
def models_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_list_models_parses_openai_shape(models_server) -> None:
    service = _service(base_url=f"http://127.0.0.1:{models_server}")
    assert list_models(service) == ("a", "b")


def test_list_models_reports_empty(models_server) -> None:
    _ModelsHandler.payload = b'{"data":[]}'
    service = _service(base_url=f"http://127.0.0.1:{models_server}")
    try:
        with pytest.raises(Exception) as caught:
            list_models(service)
        assert getattr(caught.value, "reason", "") == "empty"
    finally:
        _ModelsHandler.payload = b'{"data":[{"id":"a"},{"id":"b"}]}'


# ---------------------------------------------------------------- 设置页


def test_page_preset_fills_fields_without_touching_name(app, tmp_path: Path) -> None:
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.name_edit.setText("我的服务")
        index = page.preset_combo.findData("ollama")
        page.preset_combo.setCurrentIndex(index)
        assert page.base_url_edit.text() == "http://127.0.0.1:11434"
        assert page.chat_path_edit.text() == "/v1/chat/completions"
        assert page.models_path_edit.text() == "/v1/models"
        assert page.model_edit.text() == ""
        assert page.name_edit.text() == "我的服务"
    finally:
        page.deleteLater()
        app.processEvents()


def test_page_shows_fetched_models_and_fills_model(app, tmp_path: Path) -> None:
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page._on_models_done((True, ("m1", "m2")))
        assert page.model_combo.count() == 2
        page.model_combo.setCurrentIndex(1)
        assert page.model_edit.text() == "m2"
        page._on_models_done((False, "network"))
        assert page.model_combo.itemText(0) == "未检测到模型"
    finally:
        page.deleteLater()
        app.processEvents()


def test_page_lan_http_requires_the_checkbox(app, tmp_path: Path) -> None:
    page = ApiSettingsPage(FakeHost(tmp_path))
    try:
        page.base_url_edit.setText("http://192.168.1.5:8000")
        with pytest.raises(ValueError):
            page._form_service().validate()
        page.allow_lan_check.setChecked(True)
        assert page._form_service().validate().base_url == "http://192.168.1.5:8000"
    finally:
        page.deleteLater()
        app.processEvents()
