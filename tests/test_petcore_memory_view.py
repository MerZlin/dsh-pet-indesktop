from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "petcore-mods"
    / "ai.chat"
    / "host"
    / "memory_view.py"
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def view_module():
    spec = importlib.util.spec_from_file_location("ai_chat_memory_view", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeMemory:
    def __init__(self) -> None:
        now = time.time()
        self.items = [
            {
                "id": "a",
                "text": "主人喜欢红茶",
                "scope": "pet",
                "platform": "pet",
                "kind": "event",
                "importance": 80,
                "pinned": True,
                "created_at": now,
                "updated_at": now,
                "superseded_by": "",
                "origin": "",
                "source": "ai",
            },
            {
                "id": "b",
                "text": "我星期三上课",
                "scope": "pet",
                "platform": "pet",
                "kind": "event",
                "importance": 58,
                "pinned": False,
                "created_at": now - 60,
                "updated_at": now - 60,
                "superseded_by": "c",
                "origin": "",
                "source": "ai",
            },
            {
                "id": "c",
                "text": "用户叫小张",
                "scope": "dsh",
                "platform": "dsh",
                "kind": "external",
                "importance": 100,
                "pinned": False,
                "created_at": now - 120,
                "updated_at": now - 120,
                "superseded_by": "",
                "origin": "dsh-facts:f1",
                "source": "dsh-facts",
            },
        ]
        self.calls: list[tuple] = []
        self.entries_kwargs: dict = {}

    def entries(self, **kwargs):
        self.entries_kwargs = dict(kwargs)
        return [dict(item) for item in self.items]

    def stats(self, **_kwargs):
        return {
            "character": "shen",
            "entries": 3,
            "active": 2,
            "pinned": 1,
            "superseded": 1,
            "replies": 0,
            "external": 1,
            "first_met": True,
            "clarity": {"vivid": 3},
            "path": "C:/tmp/memory/shen",
        }

    def remember(self, text, **kwargs):
        self.calls.append(("remember", text, kwargs))
        return True

    def update_entry(self, entry_id, **kwargs):
        self.calls.append(("update", entry_id, kwargs))
        return True

    def delete_entry(self, entry_id, **_kwargs):
        self.calls.append(("delete", entry_id))
        return True

    def restore_entry(self, entry_id, **_kwargs):
        self.calls.append(("restore", entry_id))
        return True

    def tidy(self, **_kwargs):
        self.calls.append(("tidy",))
        return {"merged": 1, "superseded": 2, "entries": 3}

    def sync_external(self, **kwargs):
        self.calls.append(("sync", kwargs))
        return True

    def external_status(self, **_kwargs):
        return {
            "enabled": True,
            "sources": [
                {
                    "key": "dsh-facts",
                    "label": "本机 dsh.memory",
                    "status": "ok",
                    "count": 1,
                    "last_sync": time.time(),
                }
            ],
            "muted": 0,
        }

    def render_prompt(self, scope, query="", **kwargs):
        self.calls.append(("render", scope, query, kwargs))
        return "长期记忆（供参考）：\n- 主人喜欢红茶"


def _make_view(view_module):
    memory = FakeMemory()
    context = SimpleNamespace(
        ports=SimpleNamespace(
            memory=memory,
            config=SimpleNamespace(get=lambda key, default=None: default),
        )
    )
    view = view_module.MemoryView(context)
    view.set_pet_slot(2)
    return view, memory


def _row_by_text(view, text: str):
    for index in range(view.tree.topLevelItemCount()):
        item = view.tree.topLevelItem(index)
        if text in item.text(0):
            return item
    return None


def test_list_search_and_filters(qt_app, view_module) -> None:
    view, memory = _make_view(view_module)
    try:
        assert memory.entries_kwargs["pet_slot"] == 2
        assert view.tree.topLevelItemCount() == 3
        assert "置顶 · AI 对话" in _row_by_text(view, "红茶").text(1)
        assert "已被更新" in _row_by_text(view, "我星期三上课").text(1)
        assert "DSH 记忆（只读）" in _row_by_text(view, "小张").text(1)

        view.search.setText("红茶")
        assert view.tree.topLevelItemCount() == 1
        view.search.setText("不存在的记忆")
        assert view.tree.topLevelItemCount() == 0
        assert view.empty.isVisibleTo(view)

        view.search.clear()
        index = view.scope_combo.findData("__superseded__")
        view.scope_combo.setCurrentIndex(index)
        assert view.tree.topLevelItemCount() == 1
        assert "我星期三上课" in view.tree.topLevelItem(0).text(0)
    finally:
        view.shutdown()


def test_actions_route_through_the_memory_port(qt_app, view_module, monkeypatch) -> None:
    view, memory = _make_view(view_module)
    monkeypatch.setattr(
        view_module.QMessageBox, "question", lambda *args, **kwargs: view_module.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(view_module.QMessageBox, "information", lambda *args, **kwargs: None)
    try:
        view.tree.setCurrentItem(_row_by_text(view, "红茶"))
        assert view.pin_button.text() == "取消置顶"
        view._toggle_pin()
        assert memory.calls[-1] == ("update", "a", {"pinned": False, "pet_slot": 2})

        view.tree.setCurrentItem(_row_by_text(view, "我星期三上课"))
        assert view.restore_button.isEnabled() is True
        view._restore()
        assert memory.calls[-1] == ("restore", "b")

        view.tree.setCurrentItem(_row_by_text(view, "小张"))
        assert view.edit_button.isEnabled() is False
        assert view.delete_button.isEnabled() is True
        view._delete()
        assert memory.calls[-1] == ("delete", "c")

        view.tree.setCurrentItem(_row_by_text(view, "红茶"))
        view._delete()
        assert memory.calls[-1] == ("delete", "a")

        view._tidy()
        assert ("tidy",) in memory.calls

        view._sync()
        assert any(call[0] == "sync" and call[1].get("force") is True for call in memory.calls)
        view._sync_timer.stop()
    finally:
        view.shutdown()


def test_preview_shows_what_the_model_would_see(qt_app, view_module) -> None:
    view, memory = _make_view(view_module)
    try:
        view.search.setText("红茶")
        view.preview_toggle.setChecked(True)
        assert "红茶" in view.preview.toPlainText()
        assert any(call[0] == "render" for call in memory.calls)
        render_call = next(call for call in memory.calls if call[0] == "render")
        assert render_call[2] == "红茶"
        assert render_call[3]["pet_slot"] == 2
    finally:
        view.shutdown()


def test_external_status_line(qt_app, view_module) -> None:
    view, _memory = _make_view(view_module)
    try:
        text = view.source_status.text()
        assert "本机 dsh.memory" in text
        assert "已同步 1 条" in text
    finally:
        view.shutdown()
