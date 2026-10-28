"""「常规 → 数据」里的两个素材包外链模块。

锁住四件事：两行都在「数据」卡片里、每行两枚按钮指向已验证地址、按钮与提示
文案能被设置搜索命中、纯外链不产生草稿差异。外链只打开浏览器，不发请求。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton

from petcore.ui import external_links
from petcore.ui.settings.base import SettingsCard, page_search_texts
from tests.test_petcore_settings import _host

#: 两行按钮在 720×500 窄窗里必须还能横排（没做目视截图，这里只钉宽度上限）。
PACK_ROW_MAX_WIDTH = 280

PACK_ROWS = ("完整角色素材包", "高清完整角色素材包")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def _general_page(app, tmp_path: Path):
    from petcore.ui.settings.pages_core import GeneralPage

    return GeneralPage(_host(tmp_path))


def _data_card(page) -> SettingsCard:
    title = next(
        label
        for label in page.findChildren(QLabel)
        if label.objectName() == "sectionTitle" and label.text().strip() == "数据"
    )
    card = title.parentWidget().findChild(SettingsCard)
    assert card is not None
    return card


def _row_buttons(page, title: str) -> list[QPushButton]:
    """按行标题找到该行容器里的按钮（``add_row`` 的行 = 标题 → 文字块 → 行）。"""
    label = next(
        label
        for label in page.findChildren(QLabel)
        if label.objectName() == "settingLabel" and label.text().strip() == title
    )
    row = label.parentWidget().parentWidget()
    return list(row.findChildren(QPushButton))


def _button(buttons: list[QPushButton], needle: str) -> QPushButton:
    matches = [button for button in buttons if needle in button.text()]
    assert len(matches) == 1, [button.text() for button in buttons]
    return matches[0]


def test_both_pack_rows_sit_in_the_data_card(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        titles = [
            label.text().strip()
            for label in _data_card(page).findChildren(QLabel)
            if label.objectName() == "settingLabel"
        ]
        assert "角色资源包" in titles
        for title in PACK_ROWS:
            assert title in titles, titles
        # 素材包行必须排在「角色资源包」之后，并且高清包紧跟在完整包之后。
        assert titles.index("角色资源包") < titles.index(PACK_ROWS[0])
        assert titles.index(PACK_ROWS[0]) < titles.index(PACK_ROWS[1])
    finally:
        page.deleteLater()


def test_each_pack_row_has_github_and_quark_buttons(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        for title in PACK_ROWS:
            buttons = _row_buttons(page, title)
            texts = [button.text() for button in buttons]
            assert len(buttons) == 2, (title, texts)
            assert "GitHub" in _button(buttons, "GitHub").text(), (title, texts)
            assert "夸克" in _button(buttons, "夸克").text(), (title, texts)
            # 按钮不许靠 setFixedWidth 硬钉宽度：窄窗/大字号下要能自然撑开。
            assert min(button.minimumWidth() for button in buttons) < 60
            row = buttons[0].parentWidget()
            assert row.sizeHint().width() <= PACK_ROW_MAX_WIDTH, row.sizeHint().width()
    finally:
        page.deleteLater()


def test_pack_row_texts_are_searchable(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        haystack = " ".join(page_search_texts(page))
        for needle in ("素材包", "夸克网盘", "GitHub 下载", "高清"):
            assert needle in haystack, needle
        # 提示文案要说清「下载后怎么装」，不能只剩两枚裸按钮。
        assert "导入角色目录" in haystack
    finally:
        page.deleteLater()


def test_pack_buttons_open_the_verified_urls(app, tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    page = _general_page(app, tmp_path)
    try:
        for title in PACK_ROWS:
            buttons = _row_buttons(page, title)
            _button(buttons, "GitHub").click()
            _button(buttons, "夸克").click()
        assert opened == [
            external_links.CHARACTER_PACKS_URL,
            external_links.QUARK_URL,
            external_links.CHARACTER_PACKS_URL,
            external_links.QUARK_URL,
        ]
    finally:
        page.deleteLater()


def test_pack_buttons_leave_no_draft_behind(app, tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    page = _general_page(app, tmp_path)
    try:
        assert page.dirty() is False
        for title in PACK_ROWS:
            for button in _row_buttons(page, title):
                button.click()
        # 外链只开浏览器：不写配置、不进草稿，所以「保存」按钮不该因此亮起。
        assert len(opened) == 4
        assert page.dirty() is False
    finally:
        page.deleteLater()
