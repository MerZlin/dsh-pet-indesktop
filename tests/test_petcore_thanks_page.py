"""侧栏「致谢」独立页：标题/关键词、搜索文案、随包名单与释放。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from petcore.community import store as community_store
from petcore.ui.settings.base import page_search_texts
from petcore.ui.settings.page_thanks import ThanksPage
from tests.test_petcore_about_page import FakeHost

NEW_THANKS = (
    "@浩瀚星空2005",
    "@邦多利素世",
    "@炎圣人但丁",
    "@玄秋沐雨",
    "@咕噜千鹤子",
    "@月匠",
    "深度求索 DeepSeek",
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _page(tmp_path: Path) -> ThanksPage:
    return ThanksPage(FakeHost(tmp_path))


def test_page_identity_and_keywords(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        assert page.title == "致谢"
        keywords = page.keywords()
        assert "鸣谢" in keywords
        assert "粉丝" in keywords
        assert page.dirty() is False
    finally:
        page.dispose()
        page.deleteLater()


def test_settings_search_finds_the_panel_rows(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    try:
        texts = page_search_texts(page)
        for needle in ("特别鸣谢", "粉丝", "签名"):
            assert any(needle in text for text in texts), f"搜索口径缺「{needle}」"
    finally:
        page.dispose()
        page.deleteLater()


def test_bundled_credits_carry_the_new_thanks(app) -> None:
    credits = community_store.load_bundled_credits()
    names = [item.name for item in credits.special_thanks]
    for name in NEW_THANKS:
        assert name in names, f"随包特别鸣谢缺 {name}"
    handles = [item.handle for item in credits.author_accounts]
    assert "@炎圣人但丁" in handles


def test_dispose_is_safe(app, tmp_path: Path) -> None:
    page = _page(tmp_path)
    page.dispose()
    page.deleteLater()
