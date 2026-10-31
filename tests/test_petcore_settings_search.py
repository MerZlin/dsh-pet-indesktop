from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QListWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petcore.ui.settings.base import (
    SettingsPage,
    page_search_texts,
    register_search_terms,
    reveal_text,
)
from petcore.ui.settings.tabs import SettingsTabContainer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class SearchPage(SettingsPage):
    title = "搜索测试"

    def __init__(self, parent=None):
        super().__init__(None, parent)
        card = self.section("功能")
        self.combo = QComboBox(self)
        self.combo.addItems(["鲸鱼娘女仆", "冷静助手"])
        card.add_row("人设", self.combo)
        self.list = QListWidget(self)
        self.list.addItems(["长期记忆", "联网搜索"])
        card.add_row("列表", self.list)
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["动画", "自动", "允许"])
        root = QTreeWidgetItem(["随机动作"])
        child = QTreeWidgetItem(["可爱宅舞"])
        root.addChild(child)
        self.tree.addTopLevelItem(root)
        card.add_row("按分类开关", self.tree)
        self.row = card.add_row(
            "每日一句",
            QLabel("……", self),
            search_keys=("今日台词", "连说"),
        )


def test_search_texts_include_tree_combo_list_and_registered_keys(app) -> None:
    page = SearchPage()
    try:
        texts = page_search_texts(page)
        assert "可爱宅舞" in texts
        assert "随机动作" in texts
        assert "鲸鱼娘女仆" in texts
        assert "长期记忆" in texts
        assert "今日台词" in texts
        assert "连说" in texts
    finally:
        page.deleteLater()


def test_reveal_tree_item_expands_and_selects_without_changing_settings(app) -> None:
    page = SearchPage()
    try:
        assert reveal_text(page, "可爱宅舞") is True
        QApplication.processEvents()
        root = page.tree.topLevelItem(0)
        assert root.isExpanded() is True
        assert page.tree.currentItem() is root.child(0)
        # 搜索不是设置修改：下拉值保持不变。
        assert page.combo.currentIndex() == 0
    finally:
        page.deleteLater()


def test_reveal_combo_item_keeps_the_current_value(app) -> None:
    page = SearchPage()
    try:
        page.combo.setCurrentIndex(1)
        assert reveal_text(page, "鲸鱼娘女仆") is True
        QApplication.processEvents()
        assert page.combo.currentIndex() == 1
    finally:
        page.deleteLater()


def test_reveal_switches_the_inner_tab(app) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    container = SettingsTabContainer(host)
    layout.addWidget(container)
    first = QWidget()
    QVBoxLayout(first).addWidget(QLabel("第一页", first))
    second = QWidget()
    QVBoxLayout(second).addWidget(QLabel("目标功能", second))
    container.addTab("first", "第一页", first)
    container.addTab("second", "目标功能", second)
    try:
        assert reveal_text(host, "目标功能") is True
        QApplication.processEvents()
        assert container.currentKey() == "second"
    finally:
        host.deleteLater()


def test_register_search_terms_is_idempotent(app) -> None:
    widget = QWidget()
    register_search_terms(widget, "字体", "字号")
    register_search_terms(widget, "字号", "预设")
    assert widget.property("searchKeys") == ["字体", "字号", "预设"]
    widget.deleteLater()
