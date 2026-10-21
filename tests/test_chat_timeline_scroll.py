"""聊天窗口时间线：打开即落在最新一条回复，下方不留大片空白（#191）。

修复前：对话越多，最新消息下方空白越大——气泡纵向策略 Minimum 把 sizeHint（按非实际
宽度折行估出的偏高值）当成最小高度，QScrollArea 用整条时间线的最小高度撑大内容；
窗口关闭只是隐藏，重新打开停在上次位置。
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

# 布局边距（message_host_layout 上下各 24~30px）+ 余量；修复前实测为数千像素
_MAX_BOTTOM_GAP = 80


def _app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _window_with_long_history(tmp_path: Path):
    from pet.chat.models import ChatMessage
    from pet.chat.session_store import SessionStore
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    cfg = Config(tmp_path)
    store = SessionStore(cfg.dir)
    session = store.create("shenshen", "openai-main", "sys")
    rng = random.Random(1)
    for i in range(20):
        session.messages.append(ChatMessage("user", f"问题 {i} " + "这是一个问题。" * rng.randint(1, 30)))
        body = ("这是一段比较长的回答内容，用来撑开气泡高度。" * rng.randint(2, 60) + "\n") * rng.randint(1, 4)
        session.messages.append(ChatMessage("assistant", f"回答 {i}\n" + body))
    store.save(session)
    if hasattr(store, "flush"):
        store.flush()
    return ChatWindow(cfg, "shenshen")


def _last_bubble_geometry(win):
    from pet.chat.widgets import MessageBubble

    viewport = win.scroll.viewport()
    last = [b for b in win.findChildren(MessageBubble) if b.isVisible()][-1]
    top = last.mapTo(viewport, QPoint(0, 0)).y()
    return top, top + last.height(), viewport.height()


def test_opening_long_conversation_shows_latest_reply_without_blank_space(tmp_path):
    app = _app()
    win = _window_with_long_history(tmp_path)
    win.resize(900, 700)
    win.show()
    # 布局落定是异步的：宽预算轮询直到最新回复贴底，而不是固定 sleep 猜时机
    deadline = time.time() + 5
    while time.time() < deadline:
        _pump(app, 0.1)
        _top, bottom, height = _last_bubble_geometry(win)
        if 0 <= height - bottom <= _MAX_BOTTOM_GAP:
            break
    _top, bottom, height = _last_bubble_geometry(win)
    assert bottom <= height, "最新回复底边落在视口外"
    assert height - bottom <= _MAX_BOTTOM_GAP, f"最新回复下方空白 {height - bottom}px"
    win.close()


def test_reopening_window_returns_to_latest_reply(tmp_path):
    app = _app()
    win = _window_with_long_history(tmp_path)
    win.resize(900, 700)
    win.show()
    _pump(app, 0.5)
    win.scroll.verticalScrollBar().setValue(0)  # 用户上翻阅读后关闭
    _pump(app, 0.2)
    win.close()  # 4.4.0 起关闭即销毁
    _pump(app, 0.2)
    # 重开＝重新构造窗口（不再是「同一个窗口再 show 一次」）
    reopened = _window_with_long_history(tmp_path)
    reopened.resize(900, 700)
    reopened.show()
    bar = reopened.scroll.verticalScrollBar()
    deadline = time.time() + 5
    while time.time() < deadline and bar.value() < bar.maximum():
        _pump(app, 0.05)
    assert bar.value() == bar.maximum() > 0
    _top, bottom, height = _last_bubble_geometry(reopened)
    assert 0 <= height - bottom <= _MAX_BOTTOM_GAP
    reopened.close()
