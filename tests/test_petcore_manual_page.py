# -*- coding: utf-8 -*-
"""使用手册页：3 个子页、可复制提示词、导出 schema/guide。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from petcore.config import Config
from petcore.ui.settings.base import page_search_texts
from petcore.ui.settings.page_manual import ManualPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _page(tmp_path: Path) -> ManualPage:
    host = SimpleNamespace(config=Config(tmp_path), root=tmp_path)
    return ManualPage(host)


def test_manual_page_has_three_tabs(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        tabs = page._tab_container
        assert tabs._keys == ["faq", "wiki", "ai"]
        assert tabs._labels == ["常见问题", "使用说明", "给 AI 的提示词"]
        assert page.dirty() is False
        assert "使用手册" in page.title
        assert "WebP" in page.keywords()
    finally:
        page.deleteLater()


def test_manual_page_faq_covers_api_errors(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        texts = [label.text() for label in page.findChildren(QLabel)]
        assert any("API 返回 401" in text for text in texts)
        assert any("429" in text for text in texts)
        assert any("WebP" in text for text in texts)
    finally:
        page.deleteLater()


def test_manual_page_is_searchable_by_row_text(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        texts = page_search_texts(page)
        assert any("API 返回 401" in text for text in texts)
        assert any("WebP" in text for text in texts)
    finally:
        page.deleteLater()


def test_manual_prompt_contains_real_paths_and_can_be_copied(
    app, tmp_path: Path
) -> None:
    page = _page(tmp_path)
    try:
        prompt = page.prompt_edit.toPlainText()
        assert str(tmp_path) in prompt
        assert "petcore settings list" in prompt
        assert "settings-schema.json" in prompt
        page._copy_prompt()
        assert "petcore settings list" in QApplication.clipboard().text()
    finally:
        page.deleteLater()


def test_manual_page_exports_schema_and_guide(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        page._export_guide()
        schema = json.loads((tmp_path / "settings-schema.json").read_text(encoding="utf-8"))
        assert schema["schema"] == 1
        assert schema["count"] > 200
        assert (tmp_path / "settings-guide.md").is_file()
        assert "设置 schema" in (tmp_path / "settings-guide.md").read_text(encoding="utf-8")
    finally:
        page.deleteLater()
