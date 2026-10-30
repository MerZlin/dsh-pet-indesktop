from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.config import Config

from tests import test_petcore_ai_mod as ai_mod

memory_mod = None


def setup_module():
    global memory_mod
    memory_mod = _load_memory()


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _load_memory():
    import importlib.util
    import sys

    path = ai_mod.MOD_DIR / "host" / "memory.py"
    spec = importlib.util.spec_from_file_location("ai_chat_memory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_memory_from_user_text() -> None:
    assert memory_mod.extract_memory("记住我喜欢红茶") == "我喜欢红茶"
    assert memory_mod.extract_memory("以后别叫我小明") == "别叫我小明"
    assert memory_mod.extract_memory("今天天气不错") == ""
    assert memory_mod.extract_memory("记住") == ""


def test_memory_store_add_search_global_and_clear(tmp_path: Path) -> None:
    store = memory_mod.MemoryStore(tmp_path / "memory", "鲸鱼娘")
    store.add("我喜欢红茶", scope="character")
    store.add("主人住在杭州", scope="global")
    assert store.count("character") == 1
    assert store.search("红茶", scope="character")[0].content == "我喜欢红茶"
    assert store.search("杭州", scope="global")[0].content == "主人住在杭州"
    found = store.search_all("红茶 杭州", limit=5)
    assert {entry.content for entry in found} == {"我喜欢红茶", "主人住在杭州"}

    store.add("我喜欢红茶", scope="character")
    assert store.count("character") == 1
    assert store.search("红茶", scope="character")[0].hits == 1

    assert store.clear("character") == 1
    assert store.count("character") == 0


def test_memory_store_skips_bad_lines_and_prunes(tmp_path: Path) -> None:
    store = memory_mod.MemoryStore(tmp_path / "memory", "test", max_entries=10)
    store.add("条目一")
    path = store._path("character")
    path.write_text(path.read_text(encoding="utf-8") + "{bad json}\n", encoding="utf-8")
    assert store.count("character") == 1

    for index in range(20):
        store.add(f"条目{index}")
    assert store.count("character") <= 10


def _runtime(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    data = tmp_path / "data"
    context.ports.storage = SimpleNamespace(data_dir=lambda owner: data / str(owner))
    runtime = ai_mod._create_host(context).runtime_factory(context)
    return runtime, context


def test_runtime_remembers_and_injects_memory(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    runtime._remember_text("记住我喜欢红茶")
    assert "红茶" in runtime._memory_context("我喜欢喝什么")

    context.ports.chat.history_messages = [
        {"role": "user", "content": "记住我喜欢红茶"}
    ]
    runtime._on_submit("记住我喜欢红茶", 2)
    assert context.ports.chat.done_event.wait(5)
    messages = context.ports.api.requests[0]["messages"]
    assert any(
        item.get("role") == "system" and "红茶" in str(item.get("content"))
        for item in messages
    )


def test_runtime_global_memory_prefix(tmp_path: Path) -> None:
    runtime, _context = _runtime(tmp_path)
    runtime._remember_text("全局记住主人住在杭州")
    store = runtime._memory_store()
    assert store is not None
    assert store.count("global") == 1
    assert store.count("character") == 0


def test_settings_page_memory_switches(qt_app, tmp_path: Path) -> None:
    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        assert page.memory_check.isChecked() is True
        page.memory_check.setChecked(False)
        page.memory_inject.setValue(5)
        page.memory_max.setValue(800)
        assert host.config.get("ai_memory_enabled") is False
        assert host.config.get("ai_memory_inject") == 5
        assert host.config.get("ai_memory_max_entries") == 800
    finally:
        page.close()
