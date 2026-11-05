"""QQ 互通设置页：凭据必须取自宿主的 api_credentials。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "im.qq"


def _settings_module():
    path = MOD_DIR / "host" / "settings_page.py"
    spec = importlib.util.spec_from_file_location("im_qq_settings_page_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Credentials:
    def __init__(self, *, persisted: bool = True) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self._persisted = persisted

    def get(self, owner, key):
        return self.data.get((owner, key))

    def set(self, owner, key, value):
        self.data[(owner, key)] = str(value)

    def delete(self, owner, key):
        self.data.pop((owner, key), None)

    def persisted(self, owner, key) -> bool:
        return bool(self._persisted and self.data.get((owner, key)))

    def available(self) -> bool:
        return True


class _Host:
    """真实 CoreHost 只暴露 api_credentials，不暴露 credentials。"""

    def __init__(self, root: Path, *, persisted: bool = True) -> None:
        self.config = Config(root)
        self.api_credentials = _Credentials(persisted=persisted)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_page_reads_the_token_from_api_credentials(tmp_path, app) -> None:
    module = _settings_module()
    host = _Host(tmp_path)
    host.api_credentials.set("im.qq", "webhook_token", "abc123")
    page = module.QqSettingsPage(host)
    try:
        assert page.webhook_token.text() == "abc123"
        assert "已配置" not in page.token_status.text()
    finally:
        page.deleteLater()


def test_rotate_generates_and_stores_a_new_token(tmp_path, app) -> None:
    module = _settings_module()
    host = _Host(tmp_path)
    page = module.QqSettingsPage(host)
    try:
        page._rotate_webhook_token()
        token = page.webhook_token.text()
        assert len(token) == 32
        assert host.api_credentials.data[("im.qq", "webhook_token")] == token
        assert "重启" in page.status.text()
    finally:
        page.deleteLater()


def test_rotate_reports_a_non_persisted_token_instead_of_claiming_success(
    tmp_path, app
) -> None:
    module = _settings_module()
    host = _Host(tmp_path, persisted=False)
    page = module.QqSettingsPage(host)
    try:
        page._rotate_webhook_token()
        assert "未持久化" in page.status.text()
        assert "重启" in page.status.text()
    finally:
        page.deleteLater()


def test_save_access_token_uses_the_shared_keyring(tmp_path, app) -> None:
    module = _settings_module()
    host = _Host(tmp_path)
    page = module.QqSettingsPage(host)
    try:
        page.token.setText("qn-secret")
        page._save_token()
        assert host.api_credentials.data[("im.qq", "access_token")] == "qn-secret"
        assert page.token_status.text() == "已配置"
    finally:
        page.deleteLater()
