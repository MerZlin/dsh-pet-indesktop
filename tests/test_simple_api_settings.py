"""Simple API public settings and request seams; generated secrets only."""

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QCheckBox

from pet.api_config import ApiService, CoreApiConfiguration
from pet.config import Config
from tests.screen_fakes import MemoryVault


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(base=tmp_path)
    assert cfg.save()
    return app, cfg, backend


def simple(setup):
    from pet.simple_api import SimpleApiConfiguration, SimpleApiProfile

    app, cfg, backend = setup
    store = SimpleApiConfiguration(cfg, backend=backend)
    profile = SimpleApiProfile(ApiService("generated-main", "主 API", "https://main.example", model="multimodal-model"))
    store.save([profile], profile.service.service_id, expected_revision=store.revision(), keys={profile.service.service_id: "GENERATED-MAIN"})
    return store, profile


def wait(app, predicate):
    timer = QTimer()
    timer.setInterval(10)
    loop = QEventLoop()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    QTimer.singleShot(15000, loop.quit)
    timer.start()
    if not predicate():
        loop.exec()
    timer.stop()
    assert predicate()


def test_default_ui_has_no_ids_or_purpose_grants(setup):
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    w = ApiSettingsWidget(cfg, backend=backend)
    try:
        w.show()
        app.processEvents()
        assert not hasattr(w, "id_edit")
        assert not hasattr(w, "grants")
        assert w.vision_secret_edit.isVisible()
        assert not w.vision_url_edit.isVisible()
        assert w.test_button.text() == "测试连接"
    finally:
        w.close()


def test_main_key_works_for_chat_files_balance_without_grants(setup):
    store, profile = simple(setup)
    for owner, purpose in [("example.ai", "chat.send"), ("example.ai", "files.interpret"), ("core.balance", "balance.query")]:
        req = store.api.bind(owner, authorized=lambda: True).resolve(purpose)
        assert req.api_key == "GENERATED-MAIN"
        assert req.base_url == "https://main.example"
    from unittest.mock import patch

    from pet.balance_config import resolve_balance_request

    with patch("pet.feature_distribution.BUILTIN_AI", False):
        assert resolve_balance_request(setup[1]).api_key == "GENERATED-MAIN"
    raw = (
        setup[1].path.read_text(encoding="utf-8") + store.api.port.journal_path.read_text(encoding="utf-8")
        if hasattr(store.api.port, "journal_path")
        else setup[1].path.read_text(encoding="utf-8")
    )
    assert "GENERATED-MAIN" not in raw


def test_blank_vision_key_uses_main_endpoint_not_foreign_advanced_url(setup):
    store, profile = simple(setup)
    profile = replace(profile, vision_url="https://other.example", vision_model="foreign-vision")
    store.save([profile], profile.service.service_id, expected_revision=store.revision())
    request = store.api.bind("example.screen", authorized=lambda: True).resolve("manual_look")
    assert request.base_url == "https://main.example"
    assert request.api_key == "GENERATED-MAIN"
    assert request.metadata.credential_source == "main"
    assert request.metadata.fallback_model == "multimodal-model"


def test_dedicated_visual_key_and_clear_return_to_main(setup):
    store, profile = simple(setup)
    pid = profile.service.service_id
    profile = replace(profile, vision_url="https://other.example", vision_model="vision-model")
    store.save([profile], pid, expected_revision=store.revision(), vision_keys={pid: "GENERATED-VISUAL"})
    port = store.api.bind("example.screen", authorized=lambda: True)
    assert port.resolve("manual_look").api_key == "GENERATED-VISUAL"
    assert port.resolve("analyze_frame").base_url == "https://other.example"
    assert port.resolve("manual_look").metadata.credential_source == "vision"
    assert port.resolve("manual_look").model == "vision-model"
    assert store.api.bind("example.ai", authorized=lambda: True).resolve("chat.send").api_key == "GENERATED-MAIN"
    store.save(store.read().profiles, pid, expected_revision=store.revision(), clear_vision={pid})
    assert port.resolve("manual_look").api_key == "GENERATED-MAIN"
    assert port.resolve("manual_look").base_url == "https://main.example"
    assert "GENERATED" not in setup[1].path.read_text(encoding="utf-8")


def test_changed_endpoint_needs_reentered_key_and_atomic_save(setup):
    store, profile = simple(setup)
    before = store.api.document()
    modified = replace(profile, service=replace(profile.service, base_url="https://new.example"))
    from pet.credentials import CredentialError

    with pytest.raises(CredentialError, match="credential_missing"):
        store.save([modified], profile.service.service_id, expected_revision=store.revision())
    assert store.api.document() == before


def test_store_failure_conflict_and_existing_credentials_are_preserved(setup):
    store, profile = simple(setup)
    before = store.api.document()
    with pytest.raises(ValueError, match="configuration_changed"):
        store.save([profile], profile.service.service_id, expected_revision="stale", keys={profile.service.service_id: "REPLACEMENT"})
    setup[2].fail = True
    from pet.credentials import CredentialError

    with pytest.raises(CredentialError):
        store.save([profile], profile.service.service_id, expected_revision=store.revision(), keys={profile.service.service_id: "REPLACEMENT"})
    setup[2].fail = False
    assert store.api.document() == before
    assert store.api.bind("example.ai", authorized=lambda: True).resolve("chat.send").api_key == "GENERATED-MAIN"


def test_legacy_selection_prefers_current_chat_and_manual_without_mutation(setup):
    from pet.simple_api import SimpleApiConfiguration

    api = CoreApiConfiguration(setup[1])
    for pid, uses in [
        ("main", [("official.ai-chat", "chat.send", "current-model")]),
        ("visual", [("official.screen-understanding", "manual_look", "current-vision")]),
        ("unused", []),
    ]:
        api.save_service(
            ApiService(pid, pid, "https://" + pid + ".example", model="base-model"), secret="GENERATED-" + pid, grants=uses, expected_revision=api.revision()
        )
    before = api.document()
    state = SimpleApiConfiguration(setup[1]).read()
    assert state.active_id == "main"
    active = next(p for p in state.profiles if p.service.service_id == state.active_id)
    assert active.service.model == "current-model"
    assert active.vision_model == "current-vision"
    assert active.vision.service_id == "visual"
    assert api.document() == before


def test_ui_save_auto_selects_and_does_not_publish_probe(setup, monkeypatch):
    from pet.api_probe import ProbeResult
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    w = ApiSettingsWidget(cfg, backend=backend)
    w.secret_edit.setText("GENERATED-UI")
    observed = []
    monkeypatch.setattr("pet.api_probe.probe_text", lambda cfg, service, secret, **kw: observed.append(secret) or ProbeResult("probe_text_ok", 200))
    try:
        w.test_button.click()
        wait(app, lambda: not w.busy)
        assert observed == ["GENERATED-UI"]
        assert not CoreApiConfiguration(cfg).services()
        w.save_button.click()
        wait(app, lambda: not w.busy)
        request = CoreApiConfiguration(cfg).bind("example.ai", authorized=lambda: True).resolve("chat.send")
        assert request.api_key == "GENERATED-UI"
        assert "已保存" in w.status.text()
    finally:
        w.close()


@pytest.mark.parametrize("dedicated", [False, True])
def test_vision_error_hint_uses_actual_key_source(dedicated):
    from features.screen_understanding.common.models import vision_failure_hint

    text = vision_failure_hint("vision_protocol_unsupported", "vision" if dedicated else "main", "fallback")
    assert "视觉" in text and "Key" in text
    assert ("主" in text) != dedicated
    assert vision_failure_hint("vision_network_failed", "main", "网络故障") == "网络故障"


@pytest.mark.parametrize("dedicated", [False, True])
def test_real_vision_resolution_infers_model_without_probing(setup, dedicated, monkeypatch):
    from features.screen_understanding.common.models import VisionRequestConfig
    from features.screen_understanding.host.config import VisionConfigService
    from pet.simple_api import SimpleApiConfiguration, SimpleApiProfile

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("No configuration-time probe"))
    store = SimpleApiConfiguration(setup[1])
    profile = SimpleApiProfile(ApiService("main", "主 API", "https://main.example", model="deepseek-v4-flash"), vision_url="https://visual.example")
    store.save(
        [profile], "main", expected_revision=store.revision(), keys={"main": "GENERATED-MAIN"}, vision_keys={"main": "GENERATED-VISUAL"} if dedicated else {}
    )
    from pet.feature_host_bindings import bind_screen_configuration

    config, vault, _ = bind_screen_configuration(setup[1])
    service = VisionConfigService(config, vault=vault, api=store.api.bind("example.screen", authorized=lambda: True))
    for mode in ("manual", "automatic"):
        resolution = service.resolve(mode)
        assert resolution.ready, resolution.reason
        request = resolution.request
        assert request.model == "deepseek-v4-flash-vision-exp"
        assert request.base_url == ("https://visual.example" if dedicated else "https://main.example")
        assert request.api_key == ("GENERATED-VISUAL" if dedicated else "GENERATED-MAIN")
        assert VisionRequestConfig.from_dict(request.to_dict(include_secret=True)) == request
        assert "api_key" not in request.to_dict()


@pytest.mark.parametrize("action", ["_save", "close", "reject"])
def test_finish_saves_api_before_closing_without_dlc(setup, monkeypatch, action):
    from pet.modern_settings_dialog import ModernSettingsDialog

    monkeypatch.setattr("pet.feature_distribution.BUILTIN_AI", False)
    monkeypatch.setattr("pet.feature_distribution.BUILTIN_SCREEN", False)
    app, cfg, _ = setup
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    from PySide6.QtCore import Qt

    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    monkeypatch.setattr(dialog, "_write_config", lambda: True)
    monkeypatch.setattr(dialog, "_apply_autostart", lambda: None)
    try:
        widget = dialog.api_settings_widget
        widget.secret_edit.setText("GENERATED-FINISH")
        dialog.show()
        app.processEvents()
        getattr(dialog, action)()
        wait(app, lambda: not widget.busy)
        assert CoreApiConfiguration(cfg).bind("example.ai", authorized=lambda: True).resolve("files.interpret").api_key == "GENERATED-FINISH"
        assert not dialog.isVisible()
        if action == "_save":
            assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        for manager in dialog.feature_managers.values():
            manager.close()
        dialog.deleteLater()
        app.processEvents()


def test_switching_profile_keeps_drafts_and_only_save_publishes(setup):
    from pet.settings_api import ApiSettingsWidget

    app, cfg, backend = setup
    widget = ApiSettingsWidget(cfg, backend=backend)
    try:
        original = widget.services_select.currentData()
        widget.secret_edit.setText("GENERATED-FIRST")
        widget.name_edit.setText("草稿一")
        widget.add_button.click()
        second = widget.services_select.currentData()
        assert second != original
        widget.secret_edit.setText("GENERATED-SECOND")
        widget.services_select.setCurrentData(original)
        assert widget.name_edit.text() == "草稿一"
        assert widget.secret_edit.text() == "GENERATED-FIRST"
        assert not CoreApiConfiguration(cfg).services()
        widget.save_button.click()
        wait(app, lambda: not widget.busy)
        assert CoreApiConfiguration(cfg).bind("example.ai", authorized=lambda: True).resolve("chat.send").api_key == "GENERATED-FIRST"
        assert widget.secret_edit.text() == ""
        assert "已配置" in widget.secret_edit.placeholderText()
    finally:
        widget.close()


def test_simple_visual_save_preserves_legacy_visual_transport(setup):
    from pet.simple_api import SimpleApiConfiguration

    api = CoreApiConfiguration(setup[1])
    api.save_service(
        ApiService("main", "主", "https://main.example", model="text"),
        secret="GENERATED-MAIN",
        grants=[("official.ai-chat", "chat.send", "")],
        expected_revision=api.revision(),
    )
    api.save_service(
        ApiService("visual", "视觉", "https://visual.example", chat_path="/custom/chat", model="image", timeout=99),
        secret="GENERATED-VISUAL",
        grants=[("official.screen-understanding", "manual_look", "image")],
        expected_revision=api.revision(),
    )
    store = SimpleApiConfiguration(setup[1])
    state = store.read()
    store.save(state.profiles, state.active_id, expected_revision=store.revision())
    request = api.bind("example.screen", authorized=lambda: True).resolve("manual_look")
    assert request.metadata.chat_path == "/custom/chat"
    assert request.metadata.timeout == 99


@pytest.mark.parametrize("base", ["https://api.example/v1", "https://api.example/v1/chat/completions"])
def test_probe_uses_same_legacy_endpoint_normalization_as_chat(setup, monkeypatch, base):
    from unittest.mock import MagicMock

    from pet.api_probe import ProbeResult, probe_text

    calls = []

    def post(request, **kw):
        calls.append(request.full_url)
        response = MagicMock()
        response.__enter__.return_value.status = 200
        return response

    monkeypatch.setattr("urllib.request.urlopen", post)
    assert probe_text(setup[1], ApiService("probe", "测试", base, model="text"), "GENERATED") == ProbeResult("probe_text_ok", 200)
    assert calls == ["https://api.example/v1/chat/completions"]


@pytest.mark.parametrize("owners", ["ai", "screen", "both"])
def test_dlc_business_settings_survive_navigation_reparenting(setup, monkeypatch, owners):
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from shiboken6 import isValid

    from features.ai_chat.host.factory import create_host as ai_host
    from features.screen_understanding.host.factory import create_host as screen_host
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.plugins.feature_host import FeatureHost

    monkeypatch.setattr("pet.feature_distribution.BUILTIN_AI", False)
    monkeypatch.setattr("pet.feature_distribution.BUILTIN_SCREEN", False)
    app, cfg, _ = setup
    host = FeatureHost()
    if owners in {"ai", "both"}:
        host.provide(ai_host(), enabled=True)
    if owners in {"screen", "both"}:
        host.provide(screen_host(), enabled=True)
    dialog = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    monkeypatch.setattr(dialog, "_apply_autostart", lambda: None)
    try:
        components = [dialog._ai_component] if owners == "ai" else [dialog._screen_component]
        if owners == "both":
            components = [dialog._ai_component, dialog._screen_component]
        assert all(c is not None for c in components)
        rows = [(row, row.objectName()) for c in components for row in c.rows]
        dialog.show()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not [name for row, name in rows if not isValid(row)]
        for component in components:
            component.dirty()
            assert component.confirm_save()
        dialog._search_settings("API")
        dialog._search_settings("")
        assert dialog.select_page("AI 与对话")
        assert dialog.select_page("自动化与联动")
        assert dialog.api_settings_widget is not None
    finally:
        monkeypatch.setattr(dialog, "_write_config", lambda: True)
        dialog.close()
        for manager in dialog.feature_managers.values():
            manager.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
