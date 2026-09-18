# -*- coding: utf-8 -*-
"""fix_bridge_bundle.py 的冒烟路径回归（Build Linux/macOS CI 红）。

故障：CI 工作流以 `--dist dist`（相对路径）调 scripts/build_linux.sh /
build_macos.sh，透传到 fix_bridge_bundle.py --app-dir dist/<name>；
脚本随后 subprocess.run([node, smoke], cwd=dst_bridge) 把**相对** smoke
路径交给 node，node 相对自己的 cwd（= dst_bridge）再拼一次，路径双重嵌套：
  dist/<app>/_internal/integrations/dsh-pet-bridge/dist/<app>/_internal/...
  integrations/dsh-pet-bridge/verify_import.mjs
→ "Cannot find module"，Linux/macOS 构建红（Windows 走 build_onedir.ps1
的绝对路径实现，不受影响）。

修复：main() 入口把 --app-dir 归一为绝对路径（其下游 dst_bridge/smoke/
node_modules 清理路径全部随之绝对化）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fix_bridge_bundle", REPO / "scripts" / "fix_bridge_bundle.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_bundle(tmp_path: Path) -> Path:
    """构造最小合法 dist 产物结构（零依赖 bridge 副本）。"""
    bridge = (tmp_path / "dist" / "app" / "_internal"
              / "integrations" / "dsh-pet-bridge")
    bridge.mkdir(parents=True)
    (bridge / "package.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    (bridge / "verify_import.mjs").write_text("// smoke\n", encoding="utf-8")
    return tmp_path / "dist" / "app"


def _run_main(mod, monkeypatch, argv, captured):
    monkeypatch.setattr(sys, "argv", ["fix_bridge_bundle.py", *argv])
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/node")

    def fake_run(cmd, cwd=None, **kwargs):
        captured.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod.main()


def test_relative_app_dir_smoke_path_is_absolute(tmp_path, monkeypatch):
    """相对 --app-dir（CI 形态）：传给 node 的冒烟脚本必须是存在的绝对路径。"""
    mod = _load_module()
    app_dir = _make_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = []
    assert _run_main(mod, monkeypatch, ["--app-dir", os.path.relpath(app_dir)], captured) == 0
    assert captured, "冒烟应被调用（node 可用时）"
    smoke_argv, _cwd = captured[0]
    assert os.path.isabs(smoke_argv[1])
    assert os.path.exists(smoke_argv[1])


def test_absolute_app_dir_smoke_path_unchanged(tmp_path, monkeypatch):
    """绝对 --app-dir（本地默认形态）：行为不变。"""
    mod = _load_module()
    app_dir = _make_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = []
    assert _run_main(mod, monkeypatch, ["--app-dir", str(app_dir)], captured) == 0
    smoke_argv, _cwd = captured[0]
    assert os.path.isabs(smoke_argv[1])
    assert os.path.exists(smoke_argv[1])
