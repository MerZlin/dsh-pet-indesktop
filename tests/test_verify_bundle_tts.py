# -*- coding: utf-8 -*-
"""打包产物 TTS 自检（`scripts/verify_bundle_tts.py`）的「闭包该要求哪些模块」回归。

背景（2026-09-22 实测）：自检脚本对着**真实产物**跑出 FAIL——
``missing: cython_runtime, google, mpl_toolkits, pywin32_bootstrap``——而 edge-tts
明明在包里（PYZ 里有 ``edge_tts.communicate``）。根因是闭包按「``import edge_tts``
之后 ``sys.modules`` 里所有非标准库模块」算，于是三类与 edge-tts 无关的东西被当成
「必须有」，构建期被判红：

1. **启动期注入**：``.pth`` / ``sitecustomize`` 在解释器启动时就塞进 ``sys.modules``
   的模块（实测这台机器启动时已有 ``google`` / ``mpl_toolkits`` /
   ``pywin32_bootstrap``，来自 Dev-Cpp 自带的 site-packages）；
2. **运行时合成模块**：``cython_runtime`` 由 Cython 生成的扩展在 import 时现造，
   磁盘上根本没有文件，任何产物都不可能也不该为它准备文件；
3. **pkgutil 风格 namespace 空壳**：``backports`` 的 ``__init__.py`` 只有一行
   ``extend_path``，自身没有代码；aiohttp 的 ``from backports.zstd import ...``
   先把这个空壳建出来再失败（``backports.zstd`` 只是 aiohttp 的 speedups extra，
   未装，且包在 ``try/except ImportError`` 里）。

本文件钉住三条过滤，同时保证**真需要的模块仍然被要求**——否则把判定改成永不报错
也是一种"假绿"。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def verify_tts():
    spec = importlib.util.spec_from_file_location(
        "verify_bundle_tts", REPO / "scripts" / "verify_bundle_tts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_bundle_tts"] = module
    spec.loader.exec_module(module)
    return module


def _fake_module(name: str, file_path: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = str(file_path)
    return module


def test_startup_injected_module_is_not_part_of_closure(verify_tts, tmp_path):
    """``.pth`` 在启动期注入的模块不属于被检查库的闭包。

    探针模块刻意给一个真实存在的 ``.py``——这样唯一能排除它的理由就只剩
    「它在 ``import edge_tts`` 之前就已在 ``sys.modules`` 里」。
    """
    pytest.importorskip("edge_tts")
    source = tmp_path / "zz_startup_injected_probe.py"
    source.write_text("value = 1\n", encoding="utf-8")
    sys.modules["zz_startup_injected_probe"] = _fake_module(
        "zz_startup_injected_probe", source
    )
    try:
        closure = verify_tts.import_closure()
    finally:
        sys.modules.pop("zz_startup_injected_probe", None)

    assert closure is not None
    assert "zz_startup_injected_probe" not in closure


def test_synthesized_module_without_file_is_not_packable(verify_tts):
    """``cython_runtime`` 那类运行时合成模块没有文件，不该被要求进包。"""
    assert verify_tts._has_packable_source(types.ModuleType("cython_runtime")) is False


def test_pkgutil_namespace_shim_is_not_packable(verify_tts, tmp_path):
    """pkgutil 风格 namespace 空壳（实测 ``backports``）自身没有代码。"""
    package = tmp_path / "backports"
    package.mkdir()
    init = package / "__init__.py"
    init.write_text(
        "__path__ = __import__('pkgutil').extend_path(__path__, __name__)  # type: ignore\n",
        encoding="utf-8",
    )
    assert verify_tts._has_packable_source(_fake_module("backports", init)) is False


def test_regular_package_is_still_packable(verify_tts, tmp_path):
    """真包必须仍被判为「需要」，否则等于把判定改成永不报错。"""
    package = tmp_path / "yarl"
    package.mkdir()
    init = package / "__init__.py"
    init.write_text("from ._url import URL\n\n__all__ = ('URL',)\n", encoding="utf-8")
    assert verify_tts._has_packable_source(_fake_module("yarl", init)) is True


def test_module_missing_its_file_is_not_packable(verify_tts, tmp_path):
    """``__file__`` 指向不存在的路径（陈旧 ``sys.modules`` 残留）不算有源码。"""
    gone = _fake_module("zz_removed_probe", tmp_path / "zz_removed_probe.py")
    assert verify_tts._has_packable_source(gone) is False


def test_missing_modules_returns_sorted_difference(verify_tts):
    assert verify_tts.missing_modules({"a", "b"}, {"b", "c", "d"}) == ["c", "d"]


def test_minimum_modules_are_still_required(verify_tts):
    """兜底清单（含 PYZ-only 的 tabulate）不许在过滤里被削掉。"""
    assert "tabulate" in verify_tts.MINIMUM_MODULES
    assert "edge_tts" in verify_tts.MINIMUM_MODULES
