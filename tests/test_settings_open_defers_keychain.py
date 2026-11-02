# -*- coding: utf-8 -*-
"""设置窗构造期不读系统钥匙串：凭据状态等窗口显示出来再补。

机主反馈「设置弹窗弹出太久」。构造期读「已保存 / 还没设置」这点状态要走
``tts_secrets.get`` → ``import keyring``（Windows 上连带 win32ctypes，本机实测
200~500ms），等于每次开窗都先把钱花在一行提示上。改成构造期只排布局、``show``
之后用零延迟定时器补读。

锁三件事：

1. 对话框构造完、还没 ``show`` 时，一次钥匙串读取都没发生；
2. ``show`` 后事件循环一转，状态就补齐（每个凭据字段都读到）；
3. 直接构造设置页（不经对话框）仍是老行为，构造完立刻读——测试与其它入口
   的语义不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import tts, tts_secrets  # noqa: E402


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _count_secret_reads(monkeypatch) -> list[str]:
    """把钥匙串读取换成计数器（返回读过的 ref 列表）。"""
    reads: list[str] = []
    monkeypatch.setattr(tts_secrets, "get", lambda ref: reads.append(str(ref)) or "")
    monkeypatch.setattr(tts_secrets, "persisted", lambda ref: False)
    return reads


def _secret_field_count() -> int:
    return sum(len(tts.secret_fields(provider)) for provider in tts.providers())


def test_dialog_construction_does_not_touch_the_keychain(tmp_path, monkeypatch):
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    qapp = _qapp()
    reads = _count_secret_reads(monkeypatch)
    assert _secret_field_count() > 0, "前提：至少有一个后端要凭据"

    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False)
    try:
        assert dialog.voice_chime_page is not None, "前提：语音模块处于激活态"
        assert reads == [], f"构造期不该读钥匙串，实际读了 {len(reads)} 次"
    finally:
        dialog.hide()

    dialog.show()
    try:
        qapp.processEvents()  # 触发 showEvent 里排的零延迟定时器
        assert len(reads) == _secret_field_count(), (
            f"show 之后应把 {_secret_field_count()} 个凭据字段都补读一遍，实际 {len(reads)} 次"
        )
    finally:
        dialog.close()


def test_page_constructed_alone_still_reads_immediately(tmp_path, monkeypatch):
    """不经对话框（测试/其它入口）：老行为不变，构造完行里就有状态。"""
    from pet.config import Config
    from pet.voice.chime_settings import VoiceChimeSettingsPage

    _qapp()
    reads = _count_secret_reads(monkeypatch)
    page = VoiceChimeSettingsPage(Config(base=tmp_path))
    try:
        assert reads, "单独构造设置页应当立即读凭据状态"
    finally:
        page.deleteLater()


def test_deferred_hints_still_render_a_hint_before_showing(tmp_path, monkeypatch):
    """推迟读取时行里不是空白：先给出字段自己的说明，别让用户看见空提示。"""
    import pet.voice.chime_settings as chime_settings

    from pet.config import Config

    _qapp()
    _count_secret_reads(monkeypatch)
    chime_settings.set_defer_secret_hints(True)
    try:
        page = chime_settings.VoiceChimeSettingsPage(Config(base=tmp_path))
    finally:
        chime_settings.set_defer_secret_hints(False)
    try:
        for provider in tts.providers():
            for spec in tts.secret_fields(provider):
                row = page._field_rows[(provider.id, spec.name)]
                assert spec.hint in row.hint_label.text()
                assert "还没设置" not in row.hint_label.text()
    finally:
        page.deleteLater()