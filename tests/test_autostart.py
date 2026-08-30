# -*- coding: utf-8 -*-
"""开机自启模块测试（Linux XDG autostart 分支 + Windows 变体隔离）。"""

from pathlib import Path

from pet import autostart as autostart_mod


class FakeWinreg:
    """极简 winreg 替身：用 dict 模拟 HKCU Run 值，验证变体互不影响。"""

    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, subkey, reserved=0, access=0):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def _force_win(monkeypatch):
    monkeypatch.setattr(autostart_mod, "_IS_WIN", True)
    monkeypatch.setattr(autostart_mod, "_IS_MAC", False)
    monkeypatch.setattr(autostart_mod, "_IS_LINUX", False)
    fake = FakeWinreg()
    # Linux/macOS 模块不会自动 import winreg，用 raising=False 补挂测试替身。
    monkeypatch.setattr(autostart_mod, "winreg", fake, raising=False)
    return fake


def test_windows_autostart_variants_do_not_affect_each_other(monkeypatch):
    fake = _force_win(monkeypatch)
    monkeypatch.setattr(autostart_mod, "VALUE_NAME", "dsh-pet-standalone-webm")

    assert autostart_mod.enable() is True
    assert "dsh-pet-standalone-webm" in fake.values
    assert autostart_mod.is_enabled() is True

    # 切到 Chat 变体再开启：不应清掉无 Chat 版的自启
    monkeypatch.setattr(autostart_mod, "VALUE_NAME", "dsh-pet-standalone-webm-chat")
    assert autostart_mod.enable() is True
    assert "dsh-pet-standalone-webm" in fake.values
    assert "dsh-pet-standalone-webm-chat" in fake.values

    # 关闭 Chat 版：无 Chat 版必须保留
    assert autostart_mod.disable() is True
    assert "dsh-pet-standalone-webm-chat" not in fake.values
    assert "dsh-pet-standalone-webm" in fake.values

    # is_enabled 只认当前变体自己的值
    assert autostart_mod.is_enabled() is False
    monkeypatch.setattr(autostart_mod, "VALUE_NAME", "dsh-pet-standalone-webm")
    assert autostart_mod.is_enabled() is True


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
