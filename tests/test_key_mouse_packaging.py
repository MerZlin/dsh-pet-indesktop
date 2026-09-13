# -*- coding: utf-8 -*-
"""键鼠跟随模式的打包/CI 接线护栏。

与仓库既有「构建脚本必须打包某资源」的回归测试同源（见
tests/test_requested_regressions.py::test_build_scripts_bundle_menu_templates_and_chat_styles）：
新模式若漏接线，包会因为找不到运行时而在用户侧静默退化，故用文本断言钉住。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_gitignore_excludes_external_runtime():
    text = _read(".gitignore")

    assert "external/" in text


def test_onedir_build_injects_key_mouse_runtime():
    text = _read("scripts/build_onedir.ps1")

    assert "KeyMouseRuntimeDir" in text
    assert "RequireKeyMouseRuntime" in text
    assert "external\\bongocat" in text
    assert "BongoCat.exe" in text
    assert "assets\\models\\standard\\cat.model3.json" in text
    assert "verify_bongo_assets.py --runtime" in text


def test_runtime_build_script_builds_fork_and_verifies():
    text = _read("scripts/build_bongo_runtime.ps1")

    assert "pnpm tauri build --no-bundle" in text
    assert "tauri.conf.json" in text
    assert "verify_bongo_assets.py --runtime" in text


def test_windows_workflow_builds_runtime_before_packaging():
    text = _read(".github/workflows/build-windows.yml")

    assert "MerZlin/BongoCat" in text
    assert "ref: dsh-pet" in text
    assert "build_bongo_runtime.ps1" in text
    assert "-RequireKeyMouseRuntime" in text
    # 运行时构建 + 冒烟必须排在 onedir 打包之前
    assert text.index("build_bongo_runtime.ps1") < text.index("-RequireKeyMouseRuntime")
