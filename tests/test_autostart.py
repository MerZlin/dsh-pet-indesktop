# -*- coding: utf-8 -*-
"""开机自启模块测试（Linux XDG autostart 分支）。

Windows 上通过 monkeypatch 平台标志 + XDG_CONFIG_HOME 模拟 Linux 行为。
"""

from pathlib import Path

from pet import autostart as autostart_mod


def _force_linux(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(autostart_mod, "_IS_WIN", False)
    monkeypatch.setattr(autostart_mod, "_IS_MAC", False)
    monkeypatch.setattr(autostart_mod, "_IS_LINUX", True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_linux_enable_writes_desktop_file(monkeypatch, tmp_path: Path):
    _force_linux(monkeypatch, tmp_path)
    desktop = tmp_path / "autostart" / f"{autostart_mod.PLIST_LABEL}.desktop"
    assert not desktop.exists()
    assert autostart_mod.is_enabled() is False

    assert autostart_mod.enable() is True
    assert desktop.exists()
    assert autostart_mod.is_enabled() is True
    content = desktop.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Type=Application" in content
    assert content.startswith("Exec=") or "\nExec=" in content
    assert "Terminal=false" in content


def test_linux_disable_removes_desktop_file(monkeypatch, tmp_path: Path):
    _force_linux(monkeypatch, tmp_path)
    assert autostart_mod.enable() is True
    assert autostart_mod.disable() is True
    assert autostart_mod.is_enabled() is False
    desktop = tmp_path / "autostart" / f"{autostart_mod.PLIST_LABEL}.desktop"
    assert not desktop.exists()
