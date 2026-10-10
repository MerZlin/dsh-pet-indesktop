"""Connection feedback belongs next to the command, with real response codes."""

import ssl
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtCore import QPoint, Qt

from pet.api_config import ApiService
from tests.test_simple_api_settings import setup as setup
from tests.test_simple_api_settings import wait


@pytest.mark.parametrize("code", [200, 201, 401, 403, 429, 503])
def test_probe_preserves_real_http_status_without_reflecting_provider_body(setup, code):
    from pet.api_probe import probe_text

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"GENERATED-SECRET-ECHO")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = ApiService("test", "test", f"http://127.0.0.1:{server.server_port}", model="fixture")
        result = probe_text(setup[1], service, "GENERATED-SECRET-ECHO")
        assert result.http_status == code
        assert result.reason == ("probe_text_ok" if code < 300 else "probe_unauthorized" if code in {401, 403} else "probe_http_error")
        assert "GENERATED-SECRET-ECHO" not in str(result)
        assert seen == ["/v1/chat/completions"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize(
    "error,reason",
    [
        (TimeoutError(), "probe_timeout"),
        (urllib.error.URLError(TimeoutError()), "probe_timeout"),
        (ssl.SSLError(), "probe_tls_error"),
        (urllib.error.URLError("GENERATED-SENSITIVE-URL"), "probe_network_error"),
    ],
)
def test_transport_failure_has_no_invented_http_status(setup, monkeypatch, error, reason):
    from pet.api_probe import probe_text

    def fail(*args, **kw):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    result = probe_text(setup[1], ApiService("test", "test", "https://fixture.invalid", model="fixture"), "GENERATED")
    assert result.http_status is None and result.reason == reason
    assert "GENERATED" not in str(result)


@pytest.mark.parametrize(
    "reason,code,label",
    [("probe_text_ok", 200, "连接成功"), ("probe_unauthorized", 401, "连接失败"), ("probe_http_error", 429, "连接失败"), ("probe_timeout", None, "连接失败")],
)
def test_result_stays_next_to_test_button_and_does_not_save_draft(setup, monkeypatch, reason, code, label):
    from pet.api_probe import ProbeResult
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    entered, release = threading.Event(), threading.Event()

    def probe(*args, **kwargs):
        entered.set()
        assert release.wait(20)
        return ProbeResult(reason, code)

    monkeypatch.setattr("pet.api_probe.probe_text", probe)
    widget = ApiSettingsWidget(cfg, backend=backend)
    before = cfg.path.read_bytes()
    try:
        widget.secret_edit.setText("GENERATED-DRAFT")
        widget.resize(650, 900)
        widget.show()
        widget.test_button.click()
        wait(app, entered.is_set)
        assert "测试中" in widget.test_result.text()
        assert not widget.test_button.isEnabled()
        release.set()
        wait(app, lambda: not widget.busy)
        assert label in widget.test_result.text()
        assert (f"HTTP {code}" if code else "TIMEOUT") in widget.test_result.text()
        assert widget.test_result.textFormat() == Qt.TextFormat.PlainText
        button = widget.test_button.mapTo(widget, QPoint(0, 0))
        result = widget.test_result.mapTo(widget, QPoint(0, 0))
        assert abs(button.y() - result.y()) < 50
        assert result.x() > button.x()
        assert "保存" in widget.test_note.text()
        assert cfg.path.read_bytes() == before
        assert widget.secret_edit.text() == "GENERATED-DRAFT"
        widget.model_edit.setText("changed-after-test")
        assert "重新测试" in widget.test_result.text()
    finally:
        release.set()
        wait(app, lambda: not widget.busy)
        widget.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_result_uses_settings_text_color_even_when_system_theme_differs(setup, theme):
    from PySide6.QtGui import QColor, QPalette

    from pet.settings_api import ApiSettingsWidget
    from pet.settings_theme_qss import _settings_stylesheet
    from pet.settings_widgets import SettingRow

    app, cfg, backend = setup
    w = ApiSettingsWidget(cfg, backend=backend)
    palette = w.palette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#ff00ff"))
    w.setPalette(palette)  # Simulate a different inherited system palette.
    w.setStyleSheet(_settings_stylesheet(theme))
    try:
        w.show()
        app.processEvents()
        expected = w.findChild(SettingRow, "settingRow_api_test").label.palette().color(QPalette.ColorRole.WindowText)
        assert w.test_result.palette().color(QPalette.ColorRole.WindowText) == expected
    finally:
        w.close()
