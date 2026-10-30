from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.config import Config

from tests import test_petcore_ai_mod as ai_mod


def _load_semantics():
    import importlib.util
    import sys

    path = ai_mod.MOD_DIR / "host" / "animation_semantics.py"
    spec = importlib.util.spec_from_file_location("ai_chat_animation_semantics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


animation_semantics = _load_semantics()


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


AVAILABLE = ("思考", "开心转圈", "写代码", "招手", "难过", "吃Token")


def test_choose_animation_matches_reply_semantics() -> None:
    assert animation_semantics.choose_animation("太好了，我好开心！", "", AVAILABLE) == "开心转圈"
    assert animation_semantics.choose_animation("让我想想…", "", AVAILABLE) == "思考"
    assert animation_semantics.choose_animation("这段代码有个 bug", "", AVAILABLE) == "写代码"
    assert animation_semantics.choose_animation("你好呀", "", AVAILABLE) == "招手"
    assert animation_semantics.choose_animation("有点难过", "", AVAILABLE) == "难过"


def test_choose_animation_uses_user_text_as_context() -> None:
    assert (
        animation_semantics.choose_animation("嗯嗯", "我今天好开心", AVAILABLE)
        == "开心转圈"
    )


def test_choose_animation_returns_none_without_material() -> None:
    assert animation_semantics.choose_animation("开心", "", ("跳舞",)) is None
    assert animation_semantics.choose_animation("", "", AVAILABLE) is None
    assert animation_semantics.choose_animation("开心", "", ()) is None


def test_choose_animation_avoids_repeating_previous() -> None:
    names = ("开心A", "开心B")
    picked = animation_semantics.choose_animation(
        "开心", "", names, previous="开心A"
    )
    assert picked == "开心B"


def test_choose_thinking_only_matches_thinking_material() -> None:
    assert animation_semantics.choose_thinking(AVAILABLE) == "思考"
    assert animation_semantics.choose_thinking(("写代码",)) is None


class AnimHandle:
    slot = 2

    def __init__(self, names=AVAILABLE) -> None:
        self._names = tuple(names)

    def animations(self):
        return self._names


class AnimPets:
    def __init__(self, names=AVAILABLE) -> None:
        self.handle = AnimHandle(names)

    def active(self):
        return self.handle

    def primary(self):
        return self.handle


class AnimPresentation:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.state = None
        self.visible_now = True

    def visible(self) -> bool:
        return self.visible_now

    def animation_state(self):
        return self.state

    def play_animation(self, name: str) -> bool:
        self.played.append(str(name))
        return True


def _runtime(tmp_path: Path, names=AVAILABLE):
    context = ai_mod._context(tmp_path)
    context.ports.pets = AnimPets(names)
    context.ports.presentation = AnimPresentation()
    runtime = ai_mod._create_host(context).runtime_factory(context)
    return runtime, context


def test_runtime_plays_semantic_animation_on_final_reply(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    runtime._play_chat_animation("好开心呀", "你好", force=True)
    assert context.ports.presentation.played == ["开心转圈"]


def test_runtime_plays_thinking_animation(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    runtime._play_chat_animation("", "", thinking=True)
    assert context.ports.presentation.played == ["思考"]


def test_runtime_respects_hidden_and_loop_gates(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    context.ports.presentation.visible_now = False
    runtime._play_chat_animation("好开心", force=True)
    assert context.ports.presentation.played == []

    context.ports.presentation.visible_now = True
    context.ports.presentation.state = SimpleNamespace(loop=True, locked=False)
    runtime._play_chat_animation("好开心", force=True)
    assert context.ports.presentation.played == []

    context.ports.presentation.state = SimpleNamespace(loop=False, locked=True)
    runtime._play_chat_animation("好开心", force=True)
    assert context.ports.presentation.played == []


def test_runtime_respects_animation_switches(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    context.host.config.set("ai_animation_enabled", False)
    runtime._play_chat_animation("好开心", force=True)
    assert context.ports.presentation.played == []

    context.host.config.set("ai_animation_enabled", True)
    context.host.config.set("ai_animation_thinking", False)
    runtime._play_chat_animation("", "", thinking=True)
    assert context.ports.presentation.played == []


def test_settings_page_saves_chat_animation_switches(qt_app, tmp_path: Path) -> None:
    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        assert page.anim_check.isChecked() is True
        page.anim_check.setChecked(False)
        page.anim_thinking_check.setChecked(False)
        page.anim_min_spin.setValue(5.0)
        assert host.config.get("ai_animation_enabled") is False
        assert host.config.get("ai_animation_thinking") is False
        assert host.config.get("ai_animation_min_seconds") == 5.0
    finally:
        page.close()
