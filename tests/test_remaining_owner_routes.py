"""Remaining Core balance/owned theme routes cannot borrow AI implementation."""

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


def test_agent_cost_uses_core_balance_request_without_chat_facade(monkeypatch):
    from pet import balance_config, feature_distribution
    from pet.agent_link import AgentLinkManager

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    request = balance_config.BalanceRequest(api_key="generated-cost-only")
    monkeypatch.setattr(balance_config, "resolve_balance_request", lambda cfg: request)
    cfg = SimpleNamespace(chat_settings=lambda: pytest.fail("Core must not read AI policies"))
    manager = SimpleNamespace(cfg=cfg)
    assert AgentLinkManager._deepseek_provider(manager) is request


def test_small_ai_theme_loads_owned_resource_not_core_assets(tmp_path, monkeypatch):
    from features.ai_chat.host.chat import themes
    from pet import feature_distribution

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "signed-version"
    (root / "host/chat").mkdir(parents=True)
    (root / "resources/chat").mkdir(parents=True)
    image = QImage(4, 6, QImage.Format.Format_RGB32)
    image.fill(0xFFAA2255)
    assert image.save(str(root / "resources/chat/whale.jpg"))
    monkeypatch.setattr(themes, "__file__", str(root / "host/chat/themes.py"))
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    monkeypatch.setattr(themes, "characters_dir", lambda: pytest.fail("DLC must not borrow Core theme resources"))
    pixmap = themes.resolve_background_pixmap("builtin:whale")
    assert pixmap is not None and pixmap.size() == image.size()
    app.processEvents()
