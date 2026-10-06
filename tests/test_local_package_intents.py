"""Setup conveys intent only: actual manager preflights, user confirms later."""

from __future__ import annotations

import builtins
import sys
import time
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet import feature_distribution
from pet.config import Config
from pet.feature_management import attach_feature_management
from pet.feature_package_transactions import OperationResult
from pet.official_features import AI_FEATURE_ID, SCREEN_FEATURE_ID
from pet.plugins.feature_host import FeatureHost
from tests.test_official_feature_contracts import signed_package, verifier


def pump(app, predicate):
    deadline = time.monotonic() + 30
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert predicate()


def test_parse_intents_has_closed_owner_list_and_no_source_execution(tmp_path):
    from pet.local_package_intents import parse_intents

    root, owners = parse_intents([str(tmp_path), AI_FEATURE_ID, SCREEN_FEATURE_ID])
    assert root == tmp_path and set(owners) == {AI_FEATURE_ID, SCREEN_FEATURE_ID}
    for bad in (
        [str(tmp_path)],
        ["relative", AI_FEATURE_ID],
        [str(tmp_path), "third.party"],
        [str(tmp_path), AI_FEATURE_ID, AI_FEATURE_ID],
        [str(tmp_path), "--worker"],
    ):
        with pytest.raises(ValueError, match="local_package_intent_invalid"):
            parse_intents(bad)


@pytest.mark.parametrize("owner", [AI_FEATURE_ID, SCREEN_FEATURE_ID])
def test_setup_intent_only_preflights_real_signed_zip_without_enabling_or_execution(tmp_path, monkeypatch, owner):
    from pet.local_package_intents import LocalPackageIntent

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.delattr(builtins, "_phase5a_candidate_executed", raising=False)
    source = tmp_path / "generated-source"
    manifest, key = signed_package(source, owner)
    packages = tmp_path / "旁置 packages with spaces"
    packages.mkdir()
    with zipfile.ZipFile(packages / (owner + ".zip"), "w") as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    manager = attach_feature_management(Config(base=tmp_path / "generated-data"), FeatureHost(), feature_id=owner, management_only=True)
    manager.service.verifier = verifier(key, owner)
    intent = LocalPackageIntent(manager, packages)
    results = []
    manager.result_ready.connect(results.append)
    try:
        assert intent.begin()
        pump(app, lambda: any(isinstance(result, OperationResult) and result.plan is not None for result in results))
        plan = results[-1].plan
        assert results[-1].status == "awaiting_confirmation" and plan.feature_id == owner
        assert plan.target_version == manifest["version"]
        state = manager.service.store.read().state
        assert not state.enabled and not state.versions and not state.pending_transaction
        assert not hasattr(builtins, "_phase5a_candidate_executed")
    finally:
        intent.close()
        manager.close()


def test_missing_one_local_package_does_not_impact_other_owner(tmp_path, monkeypatch):
    from pet.local_package_intents import LocalPackageIntent

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    manager = attach_feature_management(Config(base=tmp_path / "generated-data"), FeatureHost(), feature_id=AI_FEATURE_ID, management_only=True)
    intent = LocalPackageIntent(manager, tmp_path / "missing-packages")
    results = []
    manager.result_ready.connect(results.append)
    try:
        assert intent.begin()
        pump(app, lambda: any(isinstance(result, OperationResult) for result in results))
        assert results[-1].status in {"rejected", "failed"} and results[-1].plan is None
        assert manager.service.store.read().status == "uninstalled"
        assert not (tmp_path / "generated-data/plugins" / SCREEN_FEATURE_ID).exists()
    finally:
        intent.close()
        manager.close()


@pytest.mark.parametrize("width", [720, 1100])
@pytest.mark.parametrize("font_scale", [1.0, 1.5])
def test_real_local_install_dialog_keeps_both_owners_actions_inside_viewport(tmp_path, monkeypatch, width, font_scale):
    from PySide6.QtWidgets import QLabel, QScrollArea

    from pet.feature_management import attach_official_management, close_official_management
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.local_package_intents import create_local_package_dialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    host = FeatureHost()
    managers = attach_official_management(Config(base=tmp_path / "generated-data"), host, role="local-install", management_only=True)
    dialog = create_local_package_dialog(managers, (AI_FEATURE_ID, SCREEN_FEATURE_ID))
    font = dialog.font()
    font.setPointSizeF(font.pointSizeF() * font_scale)
    dialog.setFont(font)
    dialog.resize(width, 650)
    widgets = dialog.findChildren(FeatureManagementWidget)
    try:
        assert len(widgets) == 2
        headings = {label.text() for label in dialog.findChildren(QLabel) if label.objectName() == "localPackageOwnerHeading"}
        assert headings == {widget.feature_title for widget in widgets}, "two confirmation cards must visibly identify their owners"
        for widget in widgets:
            widget.status_label.setText("操作被安全拒绝；原因：generated_long_reason" * 4)
            widget.summary_label.setText("正式签名来源 /generated/" + "a" * 160 + "；个人数据始终保留。" * 12)
        dialog.show()
        pump(app, lambda: all(not manager.busy for manager in managers.values()))
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        assert dialog.width() == width, "long copy must not enlarge a user's chosen window"
        assert scroll.horizontalScrollBar().maximum() == 0, "local install must have no horizontal page overflow"
        assert scroll.widget().width() <= scroll.viewport().width()
        for widget in widgets:
            for button in widget.buttons:
                assert button.mapTo(scroll.widget(), button.rect().topRight()).x() < scroll.viewport().width()
            for label in (widget.status_label, widget.summary_label):
                assert label.mapTo(scroll.widget(), label.rect().topRight()).x() < scroll.viewport().width()
    finally:
        dialog.close()
        dialog.deleteLater()
        close_official_management(host)
        app.processEvents()
