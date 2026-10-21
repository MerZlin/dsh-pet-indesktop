# -*- coding: utf-8 -*-
"""Qt 插件路径消毒（issue #323）回归。"""
from __future__ import annotations

import os
import subprocess
import sys


def test_sanitize_pops_vars_and_is_idempotent(monkeypatch):
    from pet.qt_plugin_sanitizer import sanitize_plugin_path

    monkeypatch.setenv("QT_PLUGIN_PATH", "D:/Qt-debug/plugins")
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "D:/Qt-debug/plugins/platforms")
    assert sanitize_plugin_path() == ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")
    assert "QT_PLUGIN_PATH" not in os.environ
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ
    assert sanitize_plugin_path() == ()  # 幂等


def test_sanitize_noop_when_clean(monkeypatch):
    from pet.qt_plugin_sanitizer import sanitize_plugin_path

    monkeypatch.delenv("QT_PLUGIN_PATH", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
    assert sanitize_plugin_path() == ()


def test_sanitizer_has_no_pyside_dependency():
    """消毒模块自身不许 import PySide6（它必须在任何 PySide6 导入之前可用）。"""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("pet/qt_plugin_sanitizer.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert not any("PySide" in n or "PySide" in module for n in names)


def test_main_entry_sanitizes_before_pyside(tmp_path):
    """端到端时序：污染环境变量下 import pet.__main__，变量必须在模块加载即被清除
    （pet.__main__ 的 sanitize 调用在模块顶部，先于任何 PySide6 导入）。"""
    env = dict(os.environ)
    env["QT_PLUGIN_PATH"] = str(tmp_path / "fake-qt-plugins")
    (tmp_path / "fake-qt-plugins").mkdir()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c",
         "import os\n"
         "import pet.__main__  # noqa: F401  # 模块顶部即消毒\n"
         "assert 'QT_PLUGIN_PATH' not in os.environ, os.environ.get('QT_PLUGIN_PATH')\n"
         "print('SANITIZED')"],
        capture_output=True, text=True, env=env, cwd=os.getcwd(), timeout=60)
    assert result.returncode == 0, result.stderr[-500:]
    assert "SANITIZED" in result.stdout
