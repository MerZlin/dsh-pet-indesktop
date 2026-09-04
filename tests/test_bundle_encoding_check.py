# -*- coding: utf-8 -*-
"""打包产物中文编码自检脚本（scripts/check_bundle_encoding.py）单元测试。

用合成目录模拟 PyInstaller 产物（pyz 压缩字节码 + 散落 pyc + 文本资源 +
中文文件名），验证自检能：
- 从字节码里找回中文字面量（marshal 路径与字节级兜底路径）；
- 识别被乱码编译的字节码（字面量缺失 → FAIL）；
- 识别被二次转码的文本资源（无法按 UTF-8 严格解码 → FAIL）；
- 识别缺失的中文素材文件名。
"""
from __future__ import annotations

import importlib.util
import marshal
import zipfile
import zlib
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_bundle_encoding.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_bundle_encoding", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CHECKER = _load_checker()


@pytest.fixture(scope="module")
def checker():
    return _CHECKER


def _make_pyc_bytes(code) -> bytes:
    """模拟 PyInstaller pyz 内的成员：16 字节 pyc 头 + marshal 后的 code 对象。"""
    return b"\x00" * 16 + marshal.dumps(code)


def _make_module_code(literal: str):
    """构造含中文字面量的模块级 code 对象（co_consts 里带该字符串）。"""
    return compile(
        f"VALUE = {literal!r}\ndef speak():\n    return VALUE\n",
        "<synthetic>",
        "exec",
    )


def _make_catalog_code(literals, garble: bool = False):
    """构造含全部期望字面量的模块 code；garble 时把第一个字面量换成乱码。

    字面量嵌在较长字符串常量里，模拟真实源码中「深色玻璃 · 右上方」这类
    片段不是独立常量、但必须按子串命中的场景。
    """
    lines = "\n".join(
        f"    L{i} = '前缀' + {lit!r} + '后缀'" for i, lit in enumerate(literals)
    )
    return_items = ", ".join(f"L{i}" for i in range(len(literals)))
    source = f"def catalog():\n{lines}\n    return [{return_items}]\n"
    if garble:
        source = source.replace(literals[0], "\u9d5a\ufffd" + literals[0][2:])
    return compile(source, "<synthetic>", "exec")


def _write_pyz(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, zlib.compress(data))  # 模拟 PYZ 的 zlib 压缩


class TestCodeConstantScan:
    def test_finds_literals_in_marshalled_code(self, checker):
        code = _make_module_code("吃Token")
        found = checker.scan_code_constants(_make_pyc_bytes(code))
        assert "吃Token" in found

    def test_byte_scan_fallback_finds_utf8_literal(self, checker):
        raw = "写代码".encode("utf-8") + b"\x00" * 64
        assert checker._byte_scan_literals(raw, ["写代码"]) == {"写代码"}
        assert checker._byte_scan_literals(raw, ["吃Token"]) == set()

    def test_zip_scan_handles_compressed_members(self, checker, tmp_path):
        pyz = tmp_path / "app.pyz"
        _write_pyz(pyz, {"pet/catalog.pyc": _make_pyc_bytes(_make_module_code("原地敲击桌面互动"))})
        found = checker.scan_zip_code_constants(pyz, ["原地敲击桌面互动", "写代码"])
        assert "原地敲击桌面互动" in found
        assert "写代码" not in found  # 不存在的字面量不会被误报为存在


class TestBundleChecks:
    def _build_bundle(self, tmp_path: Path, *, garble_code=False, garble_text=False) -> Path:
        root = tmp_path / "app"
        internal = root / "_internal"
        internal.mkdir(parents=True)
        literals = _CHECKER.EXPECTED_CODE_LITERALS
        _write_pyz(
            internal / "app.pyz",
            {"pet/catalog.pyc": _make_pyc_bytes(_make_catalog_code(literals, garble_code))},
        )
        # 文本资源：正常 UTF-8 或 GBK 转码后的坏字节
        menu = internal / "pet" / "menu_templates"
        menu.mkdir(parents=True)
        if garble_text:
            (menu / "modern.json").write_bytes("新版菜单".encode("gbk"))
        else:
            (menu / "modern.json").write_text("新版菜单", encoding="utf-8")
        # 中文素材文件名
        videos = root / "assets" / "characters" / "shenshen" / "videos" / "random"
        videos.mkdir(parents=True)
        (videos / "吃Token.webm").write_bytes(b"fake")
        return root

    def test_clean_bundle_passes(self, checker, tmp_path):
        root = self._build_bundle(tmp_path)
        assert checker.check_bundle(root) == []

    def test_garbled_bytecode_fails(self, checker, tmp_path):
        root = self._build_bundle(tmp_path, garble_code=True)
        errors = checker.check_bundle(root)
        assert errors, "乱码字节码必须被检出"
        assert "吃Token" in errors[0]

    def test_garbled_text_resource_fails(self, checker, tmp_path):
        root = self._build_bundle(tmp_path, garble_text=True)
        errors = checker.check_bundle(root)
        assert any("modern.json" in error for error in errors)

    def test_missing_chinese_asset_fails(self, checker, tmp_path):
        root = self._build_bundle(tmp_path)
        (root / "assets" / "characters" / "shenshen" / "videos" / "random" / "吃Token.webm").unlink()
        errors = checker.check_bundle(root)
        assert any("吃Token" in error for error in errors)

    def test_directory_named_with_needle_does_not_count_as_asset(self, checker, tmp_path):
        root = self._build_bundle(tmp_path)
        asset = root / "assets" / "characters" / "shenshen" / "videos" / "random" / "吃Token.webm"
        asset.unlink()
        # 只有同名目录、没有实际文件时，仍必须判定为缺失（防止目录名误报存在）
        asset.parent.joinpath("吃Token").mkdir()
        errors = checker.check_bundle(root)
        assert any("吃Token" in error for error in errors)


# Linux 回归：Fcitx 会话必须保留 fcitx 输入法上下文，供随包的匹配 Qt 插件加载。
def test_linux_fcitx_session_preserves_fcitx_input_context(monkeypatch):
    from pet import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.setenv("QT_IM_MODULE", "fcitx")

    app_mod._configure_linux_fcitx_input_method()

    assert app_mod.os.environ["QT_IM_MODULE"] == "fcitx"
