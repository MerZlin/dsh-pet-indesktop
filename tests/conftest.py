# -*- coding: utf-8 -*-
"""pytest 全局夹具。

1) 全局测试静音：跑测试时不许真实发声。
   play_sound 的取证日志照常记录（测试仍可断言"调用了播放"），但
   QSoundEffect/QMediaPlayer 的 play 被替换为空操作——测试套件在任何
   机器上跑都不应该让喇叭出声。

2) 无人值守环境（CI/自动化）下，模态 QMessageBox 弹窗会永久阻塞或直接崩溃
   （Fatal: Aborted）。设置对话框（如 modern_settings_dialog）保存开机自启失败时会弹模态
   QMessageBox.warning，所有调用 _save() 的测试在 CI 上都会因此卡死
   （定位手段：pytest -o timeout_method=thread --timeout=90 可 dump 出
   卡住的线程堆栈）。这里用 autouse fixture 全局把 QMessageBox 的静态弹窗
   方法替换为 no-op——任何测试都不会因模态弹窗卡死。需要断言弹窗行为的
   测试可自行 monkeypatch.setattr 覆盖。
"""

import pytest


@pytest.fixture(autouse=True)
def _mute_qt_audio(monkeypatch):
    try:
        from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
    except Exception:
        return
    monkeypatch.setattr(QSoundEffect, "play", lambda self: None)
    monkeypatch.setattr(QMediaPlayer, "play", lambda self: None)


@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    for method in ("warning", "information", "critical", "question", "about"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: None))


@pytest.fixture(autouse=True)
def _close_session_writers():
    """会话异步写盘（B8）：每个测试结束后关闭所有后台 writer，
    避免守护线程在 tmp_path 已清理后继续写盘（WinError 145 之类的 teardown 竞态）。"""
    yield
    try:
        from pet.chat import session_store
        session_store.close_all_writers()
    except Exception:
        pass
