# -*- coding: utf-8 -*-
"""点击动画台词绑定对话框的素材发现回归测试。

_discover_click_names 必须走 catalog.resolve_character_video_dir（外部 DLC
目录优先），并同时识别 webm/gif——不能只认内置目录（否则 DLC 角色在绑定
对话框里看到的是别的角色的动画名，绑定写到不存在的键上静默失效）。
"""
from __future__ import annotations

from pet import catalog
from pet.click_talk_dialog import _discover_click_names


def test_discovers_from_external_dlc_dir(tmp_path, monkeypatch):
    """外部 DLC 角色：从外部目录读点击动画名，webm/gif 都识别。"""
    click_dir = tmp_path / "mydlc" / "videos" / "click"
    click_dir.mkdir(parents=True)
    (click_dir / "点击-开心.webm").write_bytes(b"")
    (click_dir / "点击-生气.gif").write_bytes(b"")
    monkeypatch.setattr(catalog, "external_character_dirs", lambda: [tmp_path])
    names = _discover_click_names("mydlc")
    assert names == ["点击-开心", "点击-生气"]


def test_missing_dir_falls_back_to_catalog_constant(tmp_path, monkeypatch):
    """目录完全不存在时回退 catalog.CLICKS（保持旧兜底行为）。"""
    monkeypatch.setattr(catalog, "external_character_dirs", lambda: [tmp_path])
    names = _discover_click_names("no-such-character")
    assert names == list(catalog.CLICKS)


def test_builtin_character_discovers_real_names():
    """内置角色（shenshen）：读到真实素材名而非过期常量——
    守住「catalog.CLICKS 字面量与磁盘真实文件名已漂移」的回归。"""
    names = _discover_click_names(catalog.DEFAULT_CHARACTER)
    click_dir = catalog.resolve_character_video_dir(catalog.DEFAULT_CHARACTER) / "click"
    expected = sorted(
        {p.stem for p in click_dir.glob("*.webm")} | {p.stem for p in click_dir.glob("*.gif")}
    )
    assert names == expected
    assert names, "内置角色必须有点击动画"
