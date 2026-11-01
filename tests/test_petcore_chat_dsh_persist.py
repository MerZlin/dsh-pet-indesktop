# -*- coding: utf-8 -*-
"""DSH 会话落盘合并与历史分批重放（2026-11-01 审批卡顿修复）。

两个现场问题：

- ``_persist_dsh_frame`` 每帧 ``store.save + _refresh_history``：一次工具密集的回合
  几十帧，每帧重写整份会话并重建侧栏，GUI 线程被拖住（桌宠同线程 → 卡）。
  现在改为内存 append + 400ms 合并落盘，切换/关闭前强制 flush。
- ``_on_history`` 一次性重建最多 200 条：审批到达时切到 DSH 视图会整段阻塞。
  现在每批 20 条、批间让出事件循环；重放期间的实时帧排队，完成后按序落地。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.chat import ChatController
from petcore.ui.chat.dsh_view import DshView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeHost:
    def __init__(self, root: Path) -> None:
        self.config = Config(root)
        self.pets = None


@pytest.fixture()
def controller(app, tmp_path: Path):
    host = _FakeHost(tmp_path)
    control = ChatController(host, tmp_path)
    yield control
    for state in control.states().values():
        if state.window is not None:
            state.window.deleteLater()


def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QThread.msleep(5)
    QApplication.processEvents()
    return bool(predicate())


def _frame(seq: int, text: str = "回复") -> dict:
    return {"kind": "assistant", "seq": seq, "text": text, "reasoning": "想一想"}


def test_dsh_frames_are_saved_once_per_window(controller) -> None:
    state = controller._ensure("ai.chat", 0, with_window=False)
    # 先把 DSH 会话建出来（它会落一份空会话），之后的计数只属于实时帧。
    controller._dsh_session(state, "pet")
    saved: list[str] = []
    original = state.store.save

    def counting(session):
        saved.append(session.session_id)
        original(session)

    state.store.save = counting
    try:
        for seq in range(3):
            controller._persist_dsh_frame(state, _frame(seq, f"第 {seq} 条"))
        assert saved == [], "帧到达时不该同步写盘"
        assert _wait_until(lambda: bool(saved))
        assert len(saved) == 1, f"400ms 合并窗口内应只写一次，实际 {saved}"
        dsh_session = controller._dsh_session(state, "pet")
        assert [item["content"] for item in dsh_session.messages] == [
            "第 0 条",
            "第 1 条",
            "第 2 条",
        ]
    finally:
        state.store.save = original


def test_close_flushes_pending_dsh_frames(controller) -> None:
    key = ("ai.chat", 0)
    state = controller._ensure(*key, with_window=False)
    controller._dsh_session(state, "pet")
    saved: list[str] = []
    original = state.store.save

    def counting(session):
        saved.append(session.session_id)
        original(session)

    state.store.save = counting
    try:
        controller._persist_dsh_frame(state, _frame(9, "最后一帧"))
        assert saved == []
        controller._on_closed(key)
        assert saved, "关闭窗口前必须把未落盘的 DSH 帧写下去"
        on_disk = state.store.load(saved[0])
        assert on_disk is not None
        assert on_disk.messages[-1]["content"] == "最后一帧"
    finally:
        state.store.save = original


def test_dsh_session_cache_returns_the_same_object(controller) -> None:
    state = controller._ensure("ai.chat", 0, with_window=False)
    first = controller._dsh_session(state, "pet")
    second = controller._dsh_session(state, "pet")
    assert first is second, "逐帧 ensure_conversation 会反复读整个会话目录"


def test_dsh_history_replays_in_batches_and_defers_live_frames(app) -> None:
    view = DshView(None)
    items = [
        {"role": "user" if index % 2 == 0 else "assistant", "text": f"历史 {index}"}
        for index in range(60)
    ]
    try:
        view._on_history(items)
        assert view._history_draining is True
        assert 0 < len(view._transcript) <= 20, "第一条历史必须当拍就开始铺"
        view._on_frame({"kind": "assistant", "text": "实时回复"})
        assert ("assistant", "实时回复") not in view._transcript, (
            "重放未完成时实时帧必须排队，不能插进历史中间"
        )
        assert _wait_until(lambda: len(view._transcript) == 61)
        assert view._history_draining is False
        assert view._transcript[-1] == ("assistant", "实时回复")
        assert view._transcript[0] == ("user", "历史 0")
    finally:
        view.deleteLater()
