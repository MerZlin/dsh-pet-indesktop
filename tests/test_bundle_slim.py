# -*- coding: utf-8 -*-
"""onedir 瘦身脚本（scripts/slim_bundle.py）纯逻辑测试。

只覆盖「移除计划」与「依赖闭包 / 必需清单校验」的判定逻辑：用注入的假
import 读取器替代 pefile，不触碰真实 dist 产物，也不做任何真实删除（真实
bundle 由构建流程 scripts/build_onedir.ps1 调用脚本执行，并带自检）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def slim():
    spec = importlib.util.spec_from_file_location("slim_bundle", REPO / "scripts" / "slim_bundle.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["slim_bundle"] = module
    spec.loader.exec_module(module)
    return module


FULL_BUNDLE = [
    "dsh-pet-standalone-webm-chat.exe",
    "_internal/PySide6/Qt6Core.dll",
    "_internal/PySide6/Qt6Gui.dll",
    "_internal/PySide6/Qt6Widgets.dll",
    "_internal/PySide6/Qt6Network.dll",
    "_internal/PySide6/Qt6Multimedia.dll",
    "_internal/PySide6/Qt6Svg.dll",
    "_internal/PySide6/QtCore.pyd",
    "_internal/PySide6/QtGui.pyd",
    "_internal/PySide6/QtWidgets.pyd",
    "_internal/PySide6/QtMultimedia.pyd",
    "_internal/PySide6/pyside6.abi3.dll",
    "_internal/PySide6/avcodec-61.dll",
    "_internal/PySide6/plugins/platforms/qwindows.dll",
    "_internal/PySide6/plugins/multimedia/ffmpegmediaplugin.dll",
    "_internal/shiboken6/shiboken6.cp311-win_amd64.dll",
]

REDUNDANT = [
    "_internal/PySide6/Qt6Quick.dll",
    "_internal/PySide6/Qt6Qml.dll",
    "_internal/PySide6/Qt6OpenGL.dll",
    "_internal/PySide6/Qt6Pdf.dll",
    "_internal/PySide6/Qt6VirtualKeyboard.dll",
    "_internal/PySide6/opengl32sw.dll",
    "_internal/PySide6/plugins/imageformats/qpdf.dll",
    "_internal/PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "_internal/PySide6/translations/qt_de.qm",
    "_internal/PySide6/translations/qtbase_ja.qm",
    "_internal/PIL/_avif.cp311-win_amd64.pyd",
]

KEEP_ALWAYS = [
    "_internal/PySide6/Qt6Core.dll",
    "_internal/PySide6/plugins/imageformats/qjpeg.dll",
    "_internal/PySide6/plugins/imageformats/qwebp.dll",
    "_internal/PySide6/translations/qt_zh_CN.qm",
    "_internal/PySide6/translations/qtbase_zh_TW.qm",
    "_internal/PySide6/translations/qtmultimedia_en.qm",
    "_internal/PIL/_imaging.cp311-win_amd64.pyd",
]


def test_plan_removals_hits_whitelist_and_keeps_neighbours(slim):
    """命中白名单的冗余项被移除，同目录的必要/中文-英文资源必须保留。"""
    rels = FULL_BUNDLE + REDUNDANT + KEEP_ALWAYS
    removals, skipped = slim.plan_removals(rels)

    assert skipped == []
    assert set(removals) == set(REDUNDANT)
    for keep in FULL_BUNDLE + KEEP_ALWAYS:
        assert keep not in removals, keep


def test_plan_removals_keep_opengl_sw_switch(slim):
    """--keep-opengl-sw 时软件 OpenGL 后备 DLL 不进入移除集，只登记为跳过。"""
    rels = FULL_BUNDLE + REDUNDANT
    removals, skipped = slim.plan_removals(rels, keep_opengl_sw=True)

    assert "_internal/PySide6/opengl32sw.dll" in skipped
    assert "_internal/PySide6/opengl32sw.dll" not in removals
    # 其余白名单项不受开关影响
    assert "_internal/PySide6/Qt6Quick.dll" in removals


def test_plan_removals_handles_windows_separators(slim):
    """Windows 反斜杠路径同样命中（bundle 内路径可能带 \\ 分隔符）。"""
    removals, _ = slim.plan_removals([r"_internal\PySide6\Qt6Quick.dll"])
    assert removals == ["_internal/PySide6/Qt6Quick.dll"]


def test_verify_no_live_reference_allows_intra_removal_edges(slim):
    """待删项之间互相引用不算冲突（整条链路一起移除）。"""
    binaries = {
        "_internal/PySide6/Qt6Core.dll": Path("Qt6Core.dll"),
        "_internal/PySide6/Qt6Quick.dll": Path("Qt6Quick.dll"),
        "_internal/PySide6/Qt6VirtualKeyboard.dll": Path("Qt6VirtualKeyboard.dll"),
    }
    removals = ["_internal/PySide6/Qt6Quick.dll", "_internal/PySide6/Qt6VirtualKeyboard.dll"]
    deps = {
        "Qt6Core.dll": {"qt6core.dll", "kernel32.dll"},
        "Qt6Quick.dll": {"qt6qml.dll", "qt6core.dll"},
        "Qt6VirtualKeyboard.dll": {"qt6quick.dll"},
    }
    assert slim.verify_no_live_reference(binaries, removals, read_imports=lambda p: deps[p.name]) == []


def test_verify_no_live_reference_flags_surviving_importer(slim):
    """保留二进制仍静态引用待删文件 → 必须报告冲突（调用方据此中止）。"""
    binaries = {
        "_internal/PySide6/Qt6Core.dll": Path("Qt6Core.dll"),
        "_internal/PySide6/opengl32sw.dll": Path("opengl32sw.dll"),
    }
    deps = {"Qt6Core.dll": {"opengl32sw.dll"}, "opengl32sw.dll": set()}
    conflicts = slim.verify_no_live_reference(
        binaries, ["_internal/PySide6/opengl32sw.dll"], read_imports=lambda p: deps[p.name]
    )
    assert conflicts == [("_internal/PySide6/Qt6Core.dll", ["opengl32sw.dll"])]


def test_verify_required_reports_missing_core_files(slim):
    """必需清单齐全时通过；缺任一核心文件都要点名报出。"""
    assert slim.verify_required(FULL_BUNDLE + REDUNDANT) == []
    broken = [r for r in FULL_BUNDLE if not r.endswith("Qt6Widgets.dll")]
    missing = slim.verify_required(broken)
    assert "_internal/PySide6/Qt6Widgets.dll" in missing
    assert all("Qt6Core.dll" != m for m in missing)


def test_prune_empty_dirs_only_removes_empty_branches(slim, tmp_path):
    """只清理空目录分支，含文件的目录必须保留。"""
    (tmp_path / "keep" / "sub").mkdir(parents=True)
    (tmp_path / "keep" / "sub" / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "gone" / "inner").mkdir(parents=True)

    removed = slim.prune_empty_dirs(tmp_path)

    assert removed == 2
    assert not (tmp_path / "gone").exists()
    assert (tmp_path / "keep" / "sub" / "file.txt").is_file()


def _make_fake_bundle(root: Path) -> Path:
    app_dir = root / "app"
    for rel in FULL_BUNDLE + REDUNDANT:
        target = app_dir / Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub")
    (app_dir / "_internal" / "PySide6" / "translations" / "qt_zh_CN.qm").write_bytes(b"stub")
    return app_dir


def test_main_dry_run_keeps_files_and_reports_plan(slim, tmp_path, monkeypatch, capsys):
    """dry-run 只报告不落删（安全阀），且必需清单校验通过才返回 0。"""
    monkeypatch.setattr(slim, "_read_imports", lambda _path: set())
    app_dir = _make_fake_bundle(tmp_path)

    code = slim.main(["--app-dir", str(app_dir), "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert '"mode": "dry-run"' in out
    for rel in REDUNDANT:
        assert (app_dir / Path(rel)).is_file(), rel


def test_main_applies_removals_and_keeps_required_files(slim, tmp_path, monkeypatch, capsys):
    """真实执行：冗余项被删、必需清单与 zh 翻译留存，摘要包含释放体积。"""
    monkeypatch.setattr(slim, "_read_imports", lambda _path: set())
    app_dir = _make_fake_bundle(tmp_path)

    code = slim.main(["--app-dir", str(app_dir)])

    assert code == 0
    out = capsys.readouterr().out
    assert '"mode": "applied"' in out
    assert '"removed_count": %d' % len(REDUNDANT) in out
    for rel in REDUNDANT:
        assert not (app_dir / Path(rel)).exists(), rel
    assert (app_dir / "_internal" / "PySide6" / "Qt6Core.dll").is_file()
    assert (app_dir / "_internal" / "PySide6" / "translations" / "qt_zh_CN.qm").is_file()


def test_main_refuses_when_dependency_check_fails(slim, tmp_path, monkeypatch, capsys):
    """依赖闭包校验不可用时必须中止（返回非 0），且不删除任何文件。"""
    monkeypatch.setattr(slim, "_read_imports", lambda _path: (_ for _ in ()).throw(RuntimeError("no pefile")))
    app_dir = _make_fake_bundle(tmp_path)

    code = slim.main(["--app-dir", str(app_dir)])

    assert code == 4
    assert (app_dir / "_internal" / "PySide6" / "Qt6Quick.dll").is_file()
