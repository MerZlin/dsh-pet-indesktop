from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.config import Config

from tests import test_petcore_ai_mod as ai_mod


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _runtime(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    return ai_mod._create_host(context).runtime_factory(context), context


def test_persona_preset_files_are_complete() -> None:
    folder = ai_mod.MOD_DIR / "persona_presets"
    files = sorted(folder.glob("*.json"))
    assert len(files) >= 6
    ids: set[str] = set()
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict)
        persona_id = str(doc.get("id") or "").strip()
        prompt = str(doc.get("prompt") or "").strip()
        assert persona_id and persona_id not in ids, path
        assert prompt and "{name}" in prompt, path
        ids.add(persona_id)
    assert {"whale_maid", "gentle", "tsundere", "genki", "calm", "cat"} <= ids


def test_runtime_default_persona_uses_the_preset(tmp_path: Path) -> None:
    runtime, _context = _runtime(tmp_path)
    prompt = runtime._prompt()
    assert "鲸鱼娘" in prompt
    assert "回答长度" in prompt and "六句话" in prompt


def test_runtime_resolves_preset_custom_and_unknown(tmp_path: Path) -> None:
    runtime, context = _runtime(tmp_path)
    context.host.config.set("ai_persona", "gentle")
    assert "温柔" in runtime._prompt()

    context.host.config.set("ai_persona", "custom")
    context.host.config.set("ai_system_prompt", "你是{name}的测试人格。")
    assert runtime._prompt() == "你是鲸鱼娘的测试人格。"

    context.host.config.set("ai_persona", "missing-preset")
    assert "鲸鱼娘" in runtime._prompt()

    context.host.config.set("ai_persona", "")
    context.host.config.set("ai_system_prompt", "")
    assert "鲸鱼娘" in runtime._prompt()


def test_settings_page_switches_persona_and_fonts(qt_app, tmp_path: Path) -> None:
    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        assert page.prompt_edit.isEnabled() is True
        assert "鲸鱼娘" in page.prompt_edit.toPlainText()
        assert page.default_font_check.isChecked() is True

        page.persona_combo.setCurrentIndex(page.persona_combo.findData("custom"))
        assert page.prompt_edit.isEnabled() is True
        assert host.config.get("ai_persona") == "custom"

        page.persona_combo.setCurrentIndex(page.persona_combo.findData("gentle"))
        assert page.prompt_edit.isEnabled() is True
        assert "温柔" in page.prompt_edit.toPlainText()
        assert host.config.get("ai_persona") == "gentle"

        page.default_font_check.setChecked(False)
        page.font_combo.setCurrentFont(page.font_combo.currentFont())
        page.chat_font_size.setValue(18)
        assert host.config.get("chat_font_size") == 18
        assert host.config.get("chat_font_family") != ""
    finally:
        page.close()


def test_persona_store_saves_overrides_and_restores(tmp_path: Path) -> None:
    import sys

    ai_mod._create_host(ai_mod._context(tmp_path))
    personas = sys.modules["petcore_feature_ai_chat.persona_presets"]
    store = personas.PersonaStore(tmp_path / "personas")

    saved = store.save(
        personas.PersonaPreset("user-test", "自定义卡", "说明", "你是{name}，测试卡。")
    )
    assert saved.id == "user-test"
    assert store.is_overridden("user-test")
    assert "自定义卡" in {item.name for item in store.load_all()}

    store.save(
        personas.PersonaPreset("gentle", "改名的温柔", "覆盖说明", "覆盖后的{name}提示。")
    )
    assert store.is_overridden("gentle")
    overridden = next(item for item in store.load_all() if item.id == "gentle")
    assert overridden.name == "改名的温柔"

    assert store.reset_builtin("gentle") is True
    restored = next(item for item in store.load_all() if item.id == "gentle")
    assert restored.name == "温柔陪伴"
    assert store.delete("user-test") is True
    assert "user-test" not in {item.id for item in store.load_all()}

    import pytest

    with pytest.raises(ValueError):
        store.save(personas.PersonaPreset("Bad Id", "x", "", "prompt"))


def test_runtime_reads_a_user_persona_card(tmp_path: Path) -> None:
    import sys

    runtime, context = _runtime(tmp_path)
    context.ports.storage = SimpleNamespace(
        data_dir=lambda owner: tmp_path / "feature-data" / owner
    )
    personas = sys.modules["petcore_feature_ai_chat.persona_presets"]
    store = personas.PersonaStore(tmp_path / "feature-data" / "ai.chat" / "personas")
    store.save(
        personas.PersonaPreset(
            "user-runtime", "运行卡", "说明", "你是{name}，这是用户人格卡的回答。"
        )
    )
    context.host.config.set("ai_persona", "user-runtime")
    assert "用户人格卡" in runtime._prompt()


def test_settings_page_creates_renames_and_deletes_a_persona_card(
    qt_app, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", lambda *a, **k: ("测试人格卡", True)
        )
        page.prompt_edit.setPlainText("你是{name}，这是测试卡。")
        page._new_persona()
        persona_id = host.config.get("ai_persona")
        assert str(persona_id).startswith("user-")
        assert page.persona_combo.currentText() == "测试人格卡"

        page.prompt_edit.setPlainText("你是{name}，改过之后的提示词。")
        page._save_persona()
        saved = next(
            item for item in page._persona_store.load_all() if item.id == persona_id
        )
        assert "改过之后" in saved.prompt

        monkeypatch.setattr(
            QInputDialog, "getText", lambda *a, **k: ("改名人格卡", True)
        )
        page._rename_persona()
        saved = next(
            item for item in page._persona_store.load_all() if item.id == persona_id
        )
        assert saved.name == "改名人格卡"

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        page._delete_persona()
        assert all(
            item.id != persona_id for item in page._persona_store.load_all()
        )
        assert host.config.get("ai_persona") == "whale_maid"
    finally:
        page.close()


def test_settings_search_reaches_memory_and_chat_font_rows(qt_app, tmp_path: Path) -> None:
    from petcore.ui.settings.base import page_search_texts, reveal_text

    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        texts = page_search_texts(page)
        assert "长期记忆" in texts
        assert "对话窗字体" in texts
        assert reveal_text(page, "长期记忆") is True
        assert reveal_text(page, "对话窗字体") is True
    finally:
        page.close()
