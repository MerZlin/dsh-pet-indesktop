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
    assert "三句话" in prompt


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
        assert page.prompt_edit.isEnabled() is False
        assert page.default_font_check.isChecked() is True

        page.persona_combo.setCurrentIndex(page.persona_combo.findData("custom"))
        assert page.prompt_edit.isEnabled() is True
        assert host.config.get("ai_persona") == "custom"

        page.persona_combo.setCurrentIndex(page.persona_combo.findData("gentle"))
        assert page.prompt_edit.isEnabled() is False
        assert host.config.get("ai_persona") == "gentle"

        page.default_font_check.setChecked(False)
        page.font_combo.setCurrentFont(page.font_combo.currentFont())
        page.chat_font_size.setValue(18)
        assert host.config.get("chat_font_size") == 18
        assert host.config.get("chat_font_family") != ""
    finally:
        page.close()
