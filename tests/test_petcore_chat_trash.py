# -*- coding: utf-8 -*-
"""会话回收站：删除先暂存 30 天，可恢复；超期才永久清理。"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from petcore.chat.session_store import SessionStore
from petcore.config import Config
from petcore.ui.chat import controller as controller_mod
from petcore.ui.chat.controller import ChatController


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_trash_restore_and_lists(tmp_path) -> None:
    store = SessionStore(tmp_path)
    first = store.new_session("第一条")
    second = store.new_session("第二条")

    trashed = store.trash(first.session_id)
    assert trashed is not None and trashed.trashed_at > 0
    assert {item.session_id for item in store.list_sessions()} == {second.session_id}
    assert [item.session_id for item in store.list_trashed()] == [first.session_id]

    restored = store.restore(first.session_id)
    assert restored is not None and restored.trashed_at == 0.0
    assert {item.session_id for item in store.list_sessions()} == {
        first.session_id,
        second.session_id,
    }
    assert store.list_trashed() == []


def test_trash_is_hidden_even_when_archived_view_is_open(tmp_path) -> None:
    store = SessionStore(tmp_path)
    session = store.new_session("归档也在回收站")
    store.archive(session.session_id, True)
    store.trash(session.session_id)
    assert store.list_sessions(include_archived=True) == []
    assert [item.session_id for item in store.list_sessions(include_archived=True, include_trashed=True)] == [
        session.session_id
    ]


def test_purge_only_removes_sessions_past_retention(tmp_path) -> None:
    store = SessionStore(tmp_path)
    old = store.new_session("旧会话")
    fresh = store.new_session("新会话")
    store.trash(old.session_id)
    store.trash(fresh.session_id)
    # 把旧会话的删除时间拨到 31 天前；只改这个文件，不动 fresh。
    old_path = store._path(old.session_id)  # noqa: SLF001 - 测试要拨时钟
    import json

    raw = json.loads(old_path.read_text(encoding="utf-8"))
    raw["trashed_at"] = time.time() - 31 * 86400
    old_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    now = time.time()
    assert store.purge(30, now=now) == 1
    assert store.load(old.session_id) is None
    assert store.load(fresh.session_id) is not None


def test_zero_retention_days_means_no_automatic_purge(app, tmp_path) -> None:
    config = Config(tmp_path)
    store = SessionStore(tmp_path, character_id=str(config.get("character")))
    session = store.new_session("不要自动清理")
    store.trash(session.session_id)
    assert store.purge(0) == 0, "0 天不是立刻全删"
    host = SimpleNamespace(config=config)
    host.config.set("chat_trash_retention_days", 0)
    control = ChatController(host, tmp_path)
    control._ensure("ai.chat", 0, with_window=False)  # noqa: SLF001
    assert store.load(session.session_id) is not None, "0 天 = 不自动清理"


def test_delete_confirmation_moves_the_session_to_trash(
    app, tmp_path, monkeypatch
) -> None:
    host = SimpleNamespace(config=Config(tmp_path))
    control = ChatController(host, tmp_path)
    state = control._ensure("ai.chat", 0, with_window=False)  # noqa: SLF001
    session_id = state.session.session_id

    monkeypatch.setattr(
        controller_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    control._on_delete(state, session_id)  # noqa: SLF001
    assert [item.session_id for item in state.store.list_trashed()] == [session_id]
    assert state.session.session_id != session_id, "删掉当前会话要回退到新的会话"
