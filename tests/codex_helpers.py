"""Shared fixtures for Codex hook/transcript integration regressions."""

import runpy
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
bridge = SimpleNamespace(**runpy.run_path(str(ROOT / "integrations/dsh-pet-codex/scripts/bridge.py")))


def poll_until(predicate, timeout=10000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(10)
    assert predicate()
