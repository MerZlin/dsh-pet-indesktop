# -*- coding: utf-8 -*-
"""聊天窗口历史分页 + 批量加载性能守卫测试。

背景（本机 PySide6 6.11.2 offscreen 实测）：870 条消息的会话让
``ChatWindow._load()`` 一次性建约 8700 个 QWidget（同步 ~3s + 事件排空
5-9s），``_add`` 每条都 ``_update_bubble_widths()`` 全量循环（O(n²)，76 万次
setMin/MaxWidth），且每条消息排 4 个零延时 singleShot 回调 → 打开聊天窗 /
删除会话 / 新建会话整体卡死 6-12 秒，实测一次 AppHang 被系统终止。

本文件钉住修复契约：
- 分页：首屏只渲染最新 50 条，顶部「加载更早消息」行逐页 prepend，
  全部加载后该行消失，prepend 不拽视口回底部；
- 批量守卫：``_load()`` 期间 ``_update_bubble_widths`` 只跑一次（O(1)），
  不排逐条 singleShot；
- 流式路径：``_started`` / ``_typewriter_tick`` 新消息始终追加在底部，
  不重置已加载的更早消息；
- 泄漏：``_refresh_sessions`` 旧行控件随刷新销毁，viewport 子控件不单调增长；
- 悬挂引用：``_remove_bubble`` 后 ``self._bubble`` 置 None。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

_PAGE = 50
_TOTAL = 200


def _app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _exec_pump(app, ms: int = 150):
    """真正进事件循环的泵：本机 PySide6 6.11.2 实测 DeferredDelete（deleteLater）
    只在 exec() 循环里投递，processEvents 不投递——销毁类断言必须用它。"""
    from PySide6.QtCore import QTimer

    QTimer.singleShot(ms, app.quit)
    app.exec()


def _make_session(tmp_path: Path, count: int = _TOTAL):
    from pet.chat.models import ChatMessage
    from pet.chat.session_store import SessionStore
    from pet.config import Config

    cfg = Config(tmp_path)
    store = SessionStore(cfg.dir)
    session = store.create("shenshen", "openai-main", "sys")
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        reasoning = f"思考过程 {i}" if role == "assistant" and i % 4 == 1 else ""
        activity = [f"联网检索 {i}"] if role == "assistant" and i % 4 == 3 else []
        session.messages.append(
            ChatMessage(role, f"消息 {i:04d}", reasoning=reasoning, activity=activity)
        )
    store.save(session)
    if hasattr(store, "flush"):
        store.flush()
    return cfg, session


def _modern_window(tmp_path: Path, count: int = _TOTAL):
    from pet.chat.widgets import ChatWindow

    cfg, session = _make_session(tmp_path, count)
    return ChatWindow(cfg, "shenshen"), session


def _legacy_window(tmp_path: Path, count: int = _TOTAL):
    from pet.chat.legacy_widgets import ChatWindow

    cfg, session = _make_session(tmp_path, count)
    return ChatWindow(cfg, "shenshen"), session


# ---------- 分页（modern） ----------


def test_load_renders_only_latest_page(tmp_path):
    _app()
    win, session = _modern_window(tmp_path)
    try:
        assert len(win._bubbles) == _PAGE
        assert win._hidden_count == _TOTAL - _PAGE
        assert win._load_earlier_row is not None
        assert str(_TOTAL - _PAGE) in win._load_earlier_row.text()
        # 首屏是会话的最新一页，不是从头渲染
        assert win._bubbles[0].content == session.messages[_TOTAL - _PAGE].content
        assert win._bubbles[-1].content == session.messages[-1].content
        # restore_thinking 对分页内消息照常工作（奇数 assistant 条带思考/联网记录）
        thinking_map = {
            m.content: bool(m.reasoning or m.activity) for m in session.messages[-_PAGE:]
        }
        for bubble in win._bubbles:
            if bubble.content in thinking_map:
                assert bubble.has_thinking == thinking_map[bubble.content]
    finally:
        win.close()


def test_load_earlier_prepends_page_and_disappears_when_done(tmp_path):
    _app()
    win, session = _modern_window(tmp_path)
    try:
        win._load_earlier()
        assert len(win._bubbles) == 2 * _PAGE
        assert win._hidden_count == _TOTAL - 2 * _PAGE
        assert win._load_earlier_row is not None
        assert str(_TOTAL - 2 * _PAGE) in win._load_earlier_row.text()
        # prepend：更早的一页插到已有气泡之上，原有气泡不动
        assert win._bubbles[0].content == session.messages[_TOTAL - 2 * _PAGE].content
        assert win._bubbles[-1].content == session.messages[-1].content

        win._load_earlier()
        win._load_earlier()
        assert len(win._bubbles) == _TOTAL
        assert win._hidden_count == 0
        assert win._load_earlier_row is None, "全部加载完后加载行必须移除"
        assert [b.content for b in win._bubbles] == [m.content for m in session.messages]
        # prepend 进分页的思考/联网记录也照常恢复
        thinking_map = {
            m.content: bool(m.reasoning or m.activity) for m in session.messages
        }
        for bubble in win._bubbles:
            assert bubble.has_thinking == thinking_map[bubble.content]
    finally:
        win.close()


def test_load_earlier_keeps_viewport_on_anchor(tmp_path):
    """prepend 后视口不跳：原第一条气泡仍落在视口内（不许滚回底部）。"""
    app = _app()
    win, _session = _modern_window(tmp_path)
    try:
        win.resize(900, 700)
        win.show()
        _pump(app, 0.3)
        viewport = win.scroll.viewport()
        anchor = win._bubbles[0]
        win.scroll.verticalScrollBar().setValue(0)  # 用户翻到顶部点加载行
        _pump(app, 0.1)
        win._load_earlier()
        deadline = time.time() + 5
        while time.time() < deadline:
            _pump(app, 0.1)
            y = anchor.mapTo(viewport, anchor.rect().topLeft()).y()
            if -anchor.height() <= y <= viewport.height():
                break
        y = anchor.mapTo(viewport, anchor.rect().topLeft()).y()
        assert -anchor.height() <= y <= viewport.height(), (
            f"prepend 后锚点气泡被甩出视口（y={y}，视口高 {viewport.height()}）"
        )
        bar = win.scroll.verticalScrollBar()
        assert bar.value() < bar.maximum(), "加载更早消息后不允许滚回底部"
    finally:
        win.close()


def test_streaming_appends_at_bottom_regardless_of_paging(tmp_path):
    """流式新消息始终追加底部，且不重置已加载的更早消息/隐藏计数。"""
    _app()
    win, _session = _modern_window(tmp_path)
    try:
        win._load_earlier()  # 已加载 100 条，还剩 100 条隐藏
        assert len(win._bubbles) == 2 * _PAGE
        hidden_before = win._hidden_count

        win._bubble = win._add("assistant", "")
        win._active_request_id = "req-1"
        win._text = ""
        win._pending_output = ""
        win._started("req-1")
        win._pending_output = "流式新回复"
        while win._pending_output:
            win._typewriter_tick()  # 打字机一拍最多吐 10 字，排空为止
        assert win._bubbles[-1] is win._bubble, "流式气泡必须追加在时间线底部"
        assert win._bubble.content == "流式新回复"
        assert win._hidden_count == hidden_before, "新消息到来不重置分页状态"
        assert len(win._bubbles) == 2 * _PAGE + 1
    finally:
        win.close()


def test_new_session_resets_paging_state(tmp_path):
    _app()
    win, _session = _modern_window(tmp_path)
    try:
        assert win._hidden_count > 0
        win.new_session()
        assert win._hidden_count == 0
        assert win._load_earlier_row is None
        assert win._bubbles == []
    finally:
        win.close()


# ---------- 分页（legacy 镜像） ----------


def test_legacy_load_renders_only_latest_page(tmp_path):
    _app()
    win, session = _legacy_window(tmp_path)
    try:
        assert len(win._bubbles) == _PAGE
        assert win._hidden_count == _TOTAL - _PAGE
        assert win._load_earlier_row is not None
        # legacy 气泡无 content 属性；首屏第一条是 user（偶数序号），纯文本可直接比对
        first = session.messages[_TOTAL - _PAGE]
        assert first.role == "user"
        assert win._bubbles[0].body.text() == first.content
        win._load_earlier()
        assert len(win._bubbles) == 2 * _PAGE
        assert win._hidden_count == _TOTAL - 2 * _PAGE
        win._load_earlier()
        win._load_earlier()
        assert len(win._bubbles) == _TOTAL
        assert win._load_earlier_row is None
    finally:
        win.close()


# ---------- O(n²) 守卫 ----------


def test_load_calls_update_bubble_widths_constant_times(tmp_path, monkeypatch):
    """_load() 期间 _update_bubble_widths 全量循环必须 O(1)，不随条数线性。"""
    _app()
    from pet.chat import widgets as modern

    win, _session = _modern_window(tmp_path)
    try:
        calls = 0
        original = modern.ChatWindow._update_bubble_widths

        def spy(self):
            nonlocal calls
            calls += 1
            return original(self)

        monkeypatch.setattr(modern.ChatWindow, "_update_bubble_widths", spy)
        win._load()
        assert calls <= 2, f"_load() 期间 _update_bubble_widths 被调 {calls} 次（200 条会话）"
    finally:
        win.close()


def test_legacy_load_calls_update_bubble_widths_constant_times(tmp_path, monkeypatch):
    _app()
    from pet.chat import legacy_widgets as legacy

    win, _session = _legacy_window(tmp_path)
    try:
        calls = 0
        original = legacy.ChatWindow._update_bubble_widths

        def spy(self):
            nonlocal calls
            calls += 1
            return original(self)

        monkeypatch.setattr(legacy.ChatWindow, "_update_bubble_widths", spy)
        win._load()
        assert calls <= 2, f"legacy _load() 期间 _update_bubble_widths 被调 {calls} 次"
    finally:
        win.close()


def test_load_earlier_calls_update_bubble_widths_constant_times(tmp_path, monkeypatch):
    _app()
    from pet.chat import widgets as modern

    win, _session = _modern_window(tmp_path)
    try:
        calls = 0
        original = modern.ChatWindow._update_bubble_widths

        def spy(self):
            nonlocal calls
            calls += 1
            return original(self)

        monkeypatch.setattr(modern.ChatWindow, "_update_bubble_widths", spy)
        win._load_earlier()
        assert calls <= 2, f"prepend 一页期间 _update_bubble_widths 被调 {calls} 次"
    finally:
        win.close()


# ---------- 控件泄漏 ----------


def test_refresh_sessions_does_not_accumulate_row_widgets(tmp_path):
    """QListWidget.clear() 不销毁 index widget：旧行必须显式 removeItemWidget +
    deleteLater，连续刷新后 viewport 子控件数不增长。"""
    app = _app()
    from pet.chat.widgets import ChatWindow, SessionListRow
    from pet.chat.session_store import SessionStore

    win, _session = _modern_window(tmp_path)
    try:
        store = SessionStore(win.config.dir)
        extra = store.create("shenshen", "openai-main", "sys")
        store.save(extra)
        if hasattr(store, "flush"):
            store.flush()
        win._refresh_sessions()
        _exec_pump(app)
        baseline = len(win.session_list.viewport().findChildren(SessionListRow))
        assert baseline >= 2
        win._refresh_sessions()
        win._refresh_sessions()
        _exec_pump(app)
        after = len(win.session_list.viewport().findChildren(SessionListRow))
        assert after == baseline, (
            f"_refresh_sessions 后 viewport 行控件 {baseline} → {after}（旧行泄漏）"
        )
    finally:
        win.close()


def test_delete_session_dialog_is_disposed(tmp_path, monkeypatch):
    """DeleteConversationDialog exec() 结束后必须 deleteLater（实测每次删除
    泄漏一个隐藏顶层窗）。"""
    _app()
    from pet.chat import widgets as modern

    win, _session = _modern_window(tmp_path)
    try:
        created = []
        real_dialog = modern.DeleteConversationDialog

        class SpyDialog(real_dialog):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

            def exec(self):
                return modern.QDialog.DialogCode.Rejected  # 用户取消：不真删会话

        monkeypatch.setattr(modern, "DeleteConversationDialog", SpyDialog)
        assert win._delete_sessions((win.session.session_id,)) is False
        assert created, "对话框未被构造"
        # 取消路径也必须 dispose（exec 之后 deleteLater）；DeferredDelete 只在
        # exec() 循环里投递，不能用 processEvents 泵
        _exec_pump(_app())
        import shiboken6

        assert not shiboken6.isValid(created[0]), "取消删除后对话框仍未销毁"
    finally:
        win.close()


def test_session_row_menu_deletes_on_close(tmp_path):
    """SessionListRow 的 QMenu 必须 WA_DeleteOnClose（每次右键泄漏一个菜单）。"""
    _app()
    from pet.chat.widgets import SessionListRow
    from PySide6.QtCore import Qt

    win, _session = _modern_window(tmp_path)
    try:
        row = SessionListRow(win.session, win.session_list)
        menus = []

        from pet.chat import widgets as modern

        real_menu = modern.QMenu

        class SpyMenu(real_menu):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                menus.append(self)

            def popup(self, *_args, **_kwargs):  # 不真弹窗，只验证属性
                pass

        modern.QMenu = SpyMenu
        try:
            row._show_menu()
        finally:
            modern.QMenu = real_menu
        assert menus, "菜单未被构造"
        assert menus[0].testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menus[0].deleteLater()
        row.deleteLater()
    finally:
        win.close()


# ---------- 悬挂引用 ----------


def test_remove_bubble_clears_current_bubble_reference(tmp_path):
    """_stopped → _remove_bubble(self._bubble) 后，下一轮 _started/_activity
    不得再碰到已删对象：deleteLater 后 self._bubble 必须置 None。"""
    _app()
    win, _session = _modern_window(tmp_path, count=2)
    try:
        win._bubble = win._add("assistant", "半截回复")
        assert win._bubble is not None
        win._remove_bubble(win._bubble)
        assert win._bubble is None
    finally:
        win.close()
