# -*- coding: utf-8 -*-
"""启动路径角色素材缺失回退的回归测试。

配置记住的角色素材目录被删/搬走（如 DLC 卸载）时，启动不得直接弹错退出，
应回退默认角色重试一次；默认角色也缺素材才报错。
"""
from __future__ import annotations

import pytest

from pet import catalog
from pet.app import AppShell
from pet.config import Config


class _StubInstance:
    """记录 _create_ui 调用序列，可按角色注入 FileNotFoundError。"""

    def __init__(self, failing: set[str]):
        self.failing = set(failing)
        self.created: list[str] = []

    def _create_ui(self, character_id: str) -> None:
        self.created.append(character_id)
        if character_id in self.failing:
            raise FileNotFoundError(f"角色素材目录不存在: {character_id}")


def _make_shell(tmp_path, character: str, failing: set[str]):
    cfg = Config(base=tmp_path)
    cfg.set("character", character)
    shell = AppShell.__new__(AppShell)  # 绕开重型 __init__，只挂本方法用到的字段
    shell.config = cfg
    shell.instance = _StubInstance(failing)
    return shell


def test_missing_character_falls_back_to_default(tmp_path):
    """DLC 角色素材缺失：回退默认角色并重试成功，配置同步纠正。"""
    shell = _make_shell(tmp_path, "deleted-dlc-char", failing={"deleted-dlc-char"})
    shell._create_ui_with_character_fallback("deleted-dlc-char")
    assert shell.instance.created == ["deleted-dlc-char", catalog.DEFAULT_CHARACTER]
    assert shell.config.get("character") == catalog.DEFAULT_CHARACTER


def test_missing_default_character_raises(tmp_path):
    """默认角色也缺素材：不再回退，原样抛出（由上层弹启动错误）。"""
    shell = _make_shell(tmp_path, catalog.DEFAULT_CHARACTER, failing={catalog.DEFAULT_CHARACTER})
    with pytest.raises(FileNotFoundError):
        shell._create_ui_with_character_fallback(catalog.DEFAULT_CHARACTER)
    assert shell.instance.created == [catalog.DEFAULT_CHARACTER]


def test_other_errors_do_not_fallback(tmp_path):
    """非素材缺失的异常不触发回退（不掩盖真实缺陷）。"""
    shell = _make_shell(tmp_path, "any-char", failing=set())

    def boom(character_id):
        raise RuntimeError("别的启动缺陷")

    shell.instance._create_ui = boom
    with pytest.raises(RuntimeError):
        shell._create_ui_with_character_fallback("any-char")
