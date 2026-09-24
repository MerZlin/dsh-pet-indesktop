# -*- coding: utf-8 -*-
"""worker 线程向对话框发射信号的安全闸（_safe_emit）回归。

连接测试 worker（最长 10s）在飞时对话框可能已被关闭销毁（WA_DeleteOnClose），
对已删 C++ 对象访问信号/emit 都是 RuntimeError（崩工作线程）。
"""

from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication

from pet.chat.utils import _safe_emit


class _Obj(QObject):
    sig = Signal(bool, str)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_safe_emit_delivers_on_live_object(app):
    obj = _Obj()
    received: list = []
    obj.sig.connect(lambda *a: received.append(a))
    _safe_emit(obj, "sig", True, "ok")
    assert received == [(True, "ok")]


def test_safe_emit_on_deleted_object_is_silent(app):
    obj = _Obj()
    received: list = []
    obj.sig.connect(lambda *a: received.append(a))
    obj.deleteLater()
    QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(obj)
    _safe_emit(obj, "sig", True, "ok")  # 不得抛 RuntimeError
    assert received == []
