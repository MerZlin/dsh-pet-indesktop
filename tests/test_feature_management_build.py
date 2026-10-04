"""Closed, validation-only production management build inputs; never a release."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.build_screen_delivery import prepare_core
from tests.test_feature_probe_windows import _bundle

ROOT = Path(__file__).resolve().parents[1]


def test_management_build_requires_verified_core_owned_helper_before_output(tmp_path):
    with pytest.raises(ValueError, match="probe"):
        prepare_core(ROOT, tmp_path / "out", chat=False, public_key="aa" * 32, entrypoint=ROOT / "packaging/phase4b_validation_entry.py")
    assert not (tmp_path / "out").exists()


def test_management_snapshot_embeds_only_test_public_policy_and_production_entry(tmp_path):
    bundle, checksum = _bundle(tmp_path)
    output = tmp_path / "out"
    manifest = prepare_core(
        ROOT,
        output,
        chat=False,
        public_key="aa" * 32,
        entrypoint=ROOT / "packaging/phase4b_validation_entry.py",
        probe_bundle=bundle,
        probe_manifest_sha256=checksum,
    )
    policy = (output / "source/pet/feature_build_policy.py").read_text(encoding="utf-8")
    assert "'aa" in policy and checksum in policy and "VALIDATION_BUILD = True" in policy
    assert "TEST_PRIVATE_KEY" not in policy and "private_bytes" not in policy
    entry = (output / "source/validation_entry.py").read_bytes()
    assert entry == (ROOT / "packaging/phase4b_validation_entry.py").read_bytes()
    assert manifest["sources"]["validation_entry.py"] == hashlib.sha256(entry).hexdigest()
    assert manifest["production_management"] is True
    assert not any(name.startswith("features/") for name in manifest["sources"])
    assert "pet/chat/service.py" not in manifest["sources"]


def test_tampered_helper_cannot_create_a_management_snapshot(tmp_path):
    bundle, checksum = _bundle(tmp_path)
    (bundle / "probe.exe").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="bundle_integrity"):
        prepare_core(
            ROOT,
            tmp_path / "out",
            chat=True,
            public_key="aa" * 32,
            entrypoint=ROOT / "packaging/phase4b_validation_entry.py",
            probe_bundle=bundle,
            probe_manifest_sha256=checksum,
        )
    assert not (tmp_path / "out").exists()


def test_validation_cleanup_accepts_already_deleted_settings_widget(tmp_path):
    # A preceding process test may own a QCoreApplication. It cannot be
    # promoted to QApplication; exercise native QWidget destruction in its
    # own process instead of draining the shared suite's queued events.
    import os
    import subprocess
    import sys

    script = """import importlib.util
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
spec = importlib.util.spec_from_file_location('owned_validation_entry', Path(__import__('sys').argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
widget = QWidget()
widget.deleteLater()
QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
module.close_valid_widget(widget)
print('OWN_WIDGET_CLEANUP_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "packaging/phase4b_validation_entry.py")],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWN_WIDGET_CLEANUP_OK" in result.stdout
