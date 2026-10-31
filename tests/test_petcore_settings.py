from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from petcore.config import Config, DEFAULTS
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.contracts.contributions import SettingsPageSpec
from petcore.contracts.host import HostPorts
from petcore.engine.catalog import GraphCatalog
from petcore.engine.enums import AnimatType, GraphType
from petcore.mods.feature import FeatureManager
from petcore.ui.menu_model import MenuItem, MenuModel
from petcore.ui.settings.base import SettingsPage
from petcore.ui.icons import MENU_ICON_SIZE
from petcore.ui.settings.dialog import SettingsDialog

CORE_TITLES = [
    "常规",
    "外观与交互",
    "互动与物理",
    "语音",
    "多宠",
    "菜单",
    "扩展管理",
    "API 与账户",
    "关于与诊断",
]


class FakeWindow:
    def __init__(self):
        self.calls = []
        self.click_through = False
        self.always_on_top = True
        self.scale = 0.72
        self.character = "shenshen"
        self.visible = True
        self.edge_probe = False

    def set_character(self, character_id, *, persist=True):
        self.character = character_id
        self.calls.append(("set_character", character_id, persist))

    def set_scale(self, scale, *, persist=True):
        self.scale = scale
        self.calls.append(("set_scale", scale, persist))

    def set_click_through(self, value, *, persist=True):
        self.click_through = bool(value)
        self.calls.append(("set_click_through", bool(value), persist))

    def set_always_on_top(self, value, *, persist=True):
        self.always_on_top = bool(value)
        self.calls.append(("set_always_on_top", bool(value), persist))

    def set_visible(self, visible):
        self.visible = bool(visible)
        self.calls.append(("set_visible", bool(visible)))

    def set_edge_probe_enabled(self, enabled, *, persist=True):
        self.edge_probe = bool(enabled)
        self.calls.append(("set_edge_probe_enabled", bool(enabled), persist))

    def isVisible(self):
        return self.visible

    def toggle_click_through(self):
        self.set_click_through(not self.click_through)

    def toggle_always_on_top(self):
        self.set_always_on_top(not self.always_on_top)

    def move_to_primary(self):
        self.calls.append(("move_to_primary",))

    def return_corner(self):
        self.calls.append(("return_corner",))

    def reset_position(self):
        self.calls.append(("reset_position",))

    def open_settings(self, initial_page=None):
        self.calls.append(("open_settings", initial_page))

    def say(self, text):
        self.calls.append(("say", text))


class FakeHost:
    def __init__(self, root: Path):
        self.config = Config(root)
        self.root = root
        self.window = FakeWindow()
        self.load_result = None
        self.diagnostics = []
        self.mod_dirs = ()
        self.settings_pages = None
        self.opened_data_dir = False
        self.reloaded = False
        # 与 CoreHost 同构：内置特性常驻，设置页据此内嵌 Core 特性页。
        self.features = FeatureManager(HostPorts(), host=self)
        self.diagnostics.extend(self.features.load_builtin_features())
        self._character = CharacterSpec(
            id="fallback",
            name="Fallback",
            root=root,
            media_kind="none",
            animations=(),
            owner="core",
        )

    def characters(self):
        return [self._character]

    def character_for(self, character_id):
        return self._character

    def build_menu(self, parent=None):
        return None

    def open_data_dir(self):
        self.opened_data_dir = True

    def reload_mods(self):
        self.reloaded = True

    def apply_live_settings(self):
        window = self.window
        character = str(self.config.get("character") or "")
        if character and character != window.character:
            window.set_character(character, persist=False)
        scale = float(self.config.get("scale") or 0.72)
        if abs(scale - window.scale) > 1e-9:
            window.set_scale(scale, persist=False)
        always_on_top = bool(self.config.get("always_on_top"))
        if always_on_top != window.always_on_top:
            window.set_always_on_top(always_on_top, persist=False)
        click_through = bool(self.config.get("click_through"))
        if click_through != window.click_through:
            window.set_click_through(click_through, persist=False)

    def set_hotkey(self, text, *, persist=True):
        self.hotkey = text
        return True, ""

    def quit_app(self):
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_questions(monkeypatch):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )


def _host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path)


def test_settings_dialog_binds_to_the_clicked_child_pet(app, tmp_path: Path) -> None:
    host = _host(tmp_path)
    child_config = Config(
        tmp_path,
        {"character": "fallback", "scale": 0.5},
        filename="config-pet-1.json",
    )
    host.window.slot = 1
    host.window.config = child_config

    dialog = SettingsDialog(host, target_window=host.window)
    assert dialog.windowTitle() == "设置 · 子宠 1"
    assert dialog.config.get("scale") == 0.5
    dialog.config.set("scale", 0.9)
    dialog.config.set("ui_font_size", 16)

    assert child_config.get("scale") == 0.9
    assert host.config.get("scale") == 0.72
    assert host.config.get("ui_font_size") == 16
    dialog.deleteLater()


def _page(dialog: SettingsDialog, title: str) -> QWidget:
    for index in range(dialog.stack.count()):
        page = dialog.stack.widget(index)
        if getattr(page, "title", "") == title:
            return page
    raise AssertionError(f"missing settings page: {title}")


def _embedded(page, title: str) -> SettingsPage:
    for child in page._embedded_pages:
        if getattr(child, "title", "") == title:
            return child
    raise AssertionError(f"missing embedded page: {title}")


def _titles(dialog: SettingsDialog) -> list[str]:
    return [dialog.nav.item(row).text() for row in range(dialog.nav.count())]


def test_core_pages_and_contract(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path))
    assert _titles(dialog) == CORE_TITLES
    assert dialog.stack.count() == len(CORE_TITLES)
    for index in range(dialog.stack.count()):
        page = dialog.stack.widget(index)
        for method in ("dirty", "draft", "confirm_save", "discard_changes", "dispose", "keywords"):
            assert callable(getattr(page, method)), (getattr(page, "title", index), method)
        assert isinstance(page.dirty(), bool)
        assert isinstance(page.keywords(), list)
    assert "petcore v" in dialog.version_label.text()
    dialog.close()


def test_edge_probe_and_hotkey_settings_are_live(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")

    appearance.edge_probe_check.setChecked(True)
    assert host.window.edge_probe is True
    assert host.config.get("edge_probe_enabled") is True

    appearance.hotkey_edit.setText("ctrl+alt+p")
    appearance.hotkey_edit.editingFinished.emit()
    assert appearance.hotkey_edit.text() == "Ctrl+Alt+P"
    assert host.hotkey == "Ctrl+Alt+P"
    assert host.config.get("hotkey_toggle_pet") == "Ctrl+Alt+P"

    dialog.cancel_button.click()
    assert host.window.edge_probe is False
    assert host.config.get("edge_probe_enabled") is False


def test_dialog_is_non_modal(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path))
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    dialog.close()


def test_scale_widgets_cover_10_to_300_percent(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    assert appearance.scale_slider.minimum() == 10
    assert appearance.scale_slider.maximum() == 300
    assert appearance.scale_spin.minimum() == 10
    assert appearance.scale_spin.maximum() == 300
    assert appearance.scale_spin.text().strip() == "72 %"

    appearance.scale_spin.setValue(237)
    dialog._apply_live()
    assert host.config.get("scale") == 2.37
    assert host.window.scale == 2.37
    assert ("set_scale", 2.37, False) in host.window.calls

    appearance.scale_spin.setValue(500)
    assert appearance.scale_spin.value() == 300
    assert host.config.get("scale") == 3.0
    dialog.cancel_button.click()


def test_live_preview_applies_without_save(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    appearance.scale_spin.setValue(150)
    for _ in range(20):
        app.processEvents()
        if host.window.scale == 1.5:
            break
        QTest.qWait(10)
    assert host.window.scale == 1.5
    assert host.config.get("scale") == 1.5
    assert not (tmp_path / "config.json").exists()
    dialog.cancel_button.click()


def test_save_applies_scale_and_click_through(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    appearance.scale_spin.setValue(200)
    appearance.click_through_check.setChecked(True)
    dialog._apply_live()

    assert host.window.scale == 2.0
    assert host.window.click_through is True
    assert ("set_scale", 2.0, False) in host.window.calls

    dialog.save_button.click()

    assert host.config.get("scale") == 2.0
    assert host.config.get("click_through") is True
    assert host.config.get("character") == "shenshen"
    assert ("set_click_through", True, False) in host.window.calls
    assert not any(call[0] == "set_character" for call in host.window.calls)
    assert Config.load(tmp_path).get("scale") == 2.0
    assert Config.load(tmp_path).get("click_through") is True

    reopened = SettingsDialog(host)
    assert _page(reopened, "外观与交互").scale_spin.value() == 200
    reopened.close()


def test_cancel_reverts_live_changes(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    appearance.scale_spin.setValue(150)
    appearance.click_through_check.setChecked(True)
    dialog._apply_live()
    assert host.window.scale == 1.5
    assert host.window.click_through is True
    assert appearance.dirty() is True

    dialog.cancel_button.click()

    assert host.config.get("scale") == 0.72
    assert host.config.get("click_through") is False
    assert host.window.scale == 0.72
    assert host.window.click_through is False
    assert not (tmp_path / "config.json").exists()
    assert appearance.click_through_check.isChecked() is False
    assert appearance.scale_spin.value() == 72


def test_close_button_saves_live_changes(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    dialog.show()
    appearance = _page(dialog, "外观与交互")
    appearance.scale_spin.setValue(150)
    dialog._apply_live()

    dialog.close()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert host.config.get("scale") == 1.5
    assert host.window.scale == 1.5
    assert Config.load(tmp_path).get("scale") == 1.5


def test_initial_page_and_select_page(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path), initial_page="appearance")
    assert dialog.stack.currentWidget().title == "外观与交互"
    dialog.select_page("about")
    assert dialog.stack.currentWidget().title == "关于与诊断"
    dialog.close()


def test_search_filters_by_title_and_keywords(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path))
    dialog.search.setText("外观")
    assert dialog.nav.item(0).isHidden() is True
    assert dialog.nav.item(1).isHidden() is False
    assert dialog.search_status.text() == "匹配 1 个页面"

    dialog.search.clear()
    assert all(not dialog.nav.item(row).isHidden() for row in range(dialog.nav.count()))

    dialog.search.setText("DIAGNOSTICS")
    assert dialog.nav.item(CORE_TITLES.index("关于与诊断")).isHidden() is False
    assert dialog.search_status.text() == "匹配 1 个页面"

    dialog.search.setText("API 密钥")
    assert dialog.nav.item(CORE_TITLES.index("API 与账户")).isHidden() is False

    dialog.search.setText("没有这个页面")
    assert all(dialog.nav.item(row).isHidden() for row in range(dialog.nav.count()))
    assert dialog.search_status.text() == "没有匹配的设置页"
    dialog.close()


def test_menu_page_edits_layout_and_saves(app, tmp_path):
    host = _host(tmp_path)
    model = MenuModel()
    for order, item_id in enumerate(("a", "b", "c")):
        model.register(MenuItem(item_id, item_id.upper(), order=order))
    host.menu_model = model
    host.config.set(
        "menu_layout",
        {
            "schema_version": 1,
            "layout_id": "petcore-default-v1",
            "nodes": [
                {"type": "action", "id": "a", "visible": True},
                {"type": "action", "id": "b", "visible": True},
                {"type": "action", "id": "c", "visible": True},
            ],
        },
    )
    dialog = SettingsDialog(host)
    page = _page(dialog, "菜单")
    editor = page.editor
    assert editor.tree.topLevelItemCount() == 3
    assert [editor.tree.topLevelItem(row).text(0) for row in range(3)] == ["A", "B", "C"]

    editor.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    editor.tree.setCurrentItem(editor.tree.topLevelItem(1))
    editor.move_selected(-1)

    dialog.save_button.click()

    saved = host.config.get("menu_layout")
    assert [node["id"] for node in saved["nodes"]] == ["b", "a", "c"]
    assert saved["nodes"][1]["visible"] is False


def test_contributed_pages_are_isolated_and_disposed(app, tmp_path):
    host = _host(tmp_path)
    created = []

    class ExtraPage(SettingsPage):
        title = "自定义页"

        def __init__(self, host):
            super().__init__(host)
            self.disposed = False
            created.append(self)

        def keywords(self):
            return ["自定义"]

        def dispose(self):
            self.disposed = True

    def boom(host):
        raise RuntimeError("bad contribution")

    host.settings_pages = lambda: [
        SettingsPageSpec("extra", "自定义页", ExtraPage, order=5),
        SettingsPageSpec("broken", "坏页", boom, order=6),
    ]
    dialog = SettingsDialog(host)
    assert _titles(dialog) == CORE_TITLES
    extensions = _page(dialog, "扩展管理")
    assert "自定义页" in extensions.tabs.labels()
    assert len(created) == 1

    extensions.select_tab("extension:extra")
    assert extensions.tabs.currentKey() == "extension:extra"

    dialog.close()
    assert created[0].disposed is True


def test_plain_widget_contribution_is_hosted_with_hint(app, tmp_path):
    from PySide6.QtWidgets import QLabel

    host = _host(tmp_path)
    host.settings_pages = lambda: [
        SettingsPageSpec("plain", "普通控件", lambda _host: QLabel("hello"), order=7),
    ]
    dialog = SettingsDialog(host)
    extensions = _page(dialog, "扩展管理")
    assert "普通控件" in extensions.tabs.labels()
    extensions.select_tab("extension:plain")
    assert extensions.tabs.currentKey() == "extension:plain"
    hosted = dict(extensions._pages)["extension:plain"]
    assert hosted.dirty() is False
    texts = [label.text() for label in hosted.scroll.widget().findChildren(QLabel)]
    assert any("没有实现 SettingsPage" in text for text in texts)
    dialog.close()


def test_golden_spin_sub_option_follows_its_parent(app, tmp_path):
    host = _host(tmp_path)
    host.config.set("golden_spin_on_click", False)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    row = interaction._spin_direct_row
    assert row.isVisibleTo(interaction) is False
    interaction._checks["golden_spin_on_click"].setChecked(True)
    assert row.isVisibleTo(interaction) is True
    interaction._checks["golden_spin_on_click"].setChecked(False)
    assert row.isVisibleTo(interaction) is False
    dialog.close()


def test_search_matches_setting_rows_and_jumps_to_the_page(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    dialog.search.setText("黄金回旋")
    app.processEvents()
    assert dialog.search_status.text().startswith("匹配")
    assert dialog.nav.currentItem().text() == "互动与物理"

    dialog.search.setText("开机自动启动")
    app.processEvents()
    assert dialog.nav.currentItem().text() == "常规"

    dialog.search.setText("完全不存在的东西")
    app.processEvents()
    assert dialog.search_status.text() == "没有匹配的设置页"
    dialog.close()


# ---------------------------------------------------------------- 页内分页
def test_interaction_page_splits_into_inner_tabs(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    tabs = interaction.tab_container
    assert tabs is not None
    assert tabs.labels() == (
        "点击与音效",
        "点击台词",
        "自言自语",
        "拖拽与投掷",
        "碰撞与投喂",
        "动画",
        "彩蛋与配图",
        "免打扰",
    )
    assert tabs.keys() == (
        "click",
        "click_talk",
        "self_talk",
        "drag",
        "collision",
        "animation",
        "easter",
        "quiet",
    )
    dialog.close()


def test_appearance_page_splits_into_inner_tabs(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    tabs = appearance.tab_container
    assert tabs is not None
    assert tabs.keys() == ("size", "island", "text", "screen")
    assert tabs.labels() == ("大小与外观", "灵动岛", "文字与快捷键", "屏幕与位置")
    dialog.close()


def test_island_page_swaps_the_header_dot_for_a_message_toggle(app, tmp_path):
    """「显示头标」退役，新增默认关闭的「消息显示」；开关直接写配置。"""
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    # 头标（左侧圆点）已彻底删除：控件与配置键都不再存在。
    assert not hasattr(appearance, "island_icon_check")
    assert "island_show_icon" not in Config(tmp_path).data
    # 消息显示默认关；勾上后立刻落到配置。
    assert appearance.island_messages_check.isChecked() is False
    appearance.island_messages_check.setChecked(True)
    app.processEvents()
    assert host.config.get("island_show_messages") is True
    appearance.island_messages_check.setChecked(False)
    app.processEvents()
    assert host.config.get("island_show_messages") is False
    dialog.close()


def test_island_page_toggles_collision_and_the_disk_segment(app, tmp_path):
    """「与桌宠碰撞」默认开、切换即写配置；「指标段」能勾上 C 盘剩余。"""
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    assert appearance.island_collision_check.isChecked() is True
    appearance.island_collision_check.setChecked(False)
    app.processEvents()
    assert host.config.get("island_collision") is False
    appearance.island_collision_check.setChecked(True)
    app.processEvents()
    assert host.config.get("island_collision") is True

    assert "disk" in appearance.island_metric_boxes
    appearance.island_metric_boxes["disk"].setChecked(True)
    app.processEvents()
    assert "disk" in host.config.get("island_metrics")
    appearance.island_metric_boxes["disk"].setChecked(False)
    app.processEvents()
    assert "disk" not in host.config.get("island_metrics")
    dialog.close()


def test_appearance_search_switches_to_the_owning_inner_tab(app, tmp_path):
    """搜索要能落到非默认分页里：搜「边缘探头」应切到「大小与外观」并滚到那一行。"""
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    appearance = _page(dialog, "外观与交互")
    dialog.select_page("appearance")
    assert appearance.tab_container.currentKey() == "size"
    dialog.search.setText("边缘探头")
    app.processEvents()
    assert appearance.tab_container.currentKey() == "size"
    dialog.search.setText("不透明度")
    app.processEvents()
    assert appearance.tab_container.currentKey() == "island"
    dialog.close()


def test_interaction_page_hosts_balance_rows(app, tmp_path):
    """「点击查看余额」与峰谷提示都在「点击与音效」分页里（主分支同页）。"""
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    assert "click_show_balance" in interaction._checks
    assert "balance_tier_color_enabled" in interaction._checks
    assert interaction.tab_container.key_for_label("点击与音效") == "click"
    assert interaction.balance_tier_mode.findData("liangwen") >= 0
    # 默认模式不是 custom：两行自定义文本隐藏（对话框未显示，用 isHidden 判）。
    assert interaction.balance_tier_mode.currentData() == "default"
    assert interaction._tier_peak_row.isHidden()
    assert interaction._tier_idle_row.isHidden()
    interaction.balance_tier_mode.setCurrentIndex(
        interaction.balance_tier_mode.findData("custom")
    )
    app.processEvents()
    assert not interaction._tier_peak_row.isHidden()
    dialog.close()


def test_embedded_page_changes_flow_through_save(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    click_talk = _embedded(interaction, "点击台词")

    click_talk.enabled.setChecked(True)
    assert host.config.get("click_talk_enabled") is True
    assert interaction.dirty() is True

    dialog.save_button.click()

    assert interaction.dirty() is False
    assert Config.load(tmp_path).get("click_talk_enabled") is True


def test_embedded_page_changes_flow_through_cancel(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    assert interaction.dirty() is False

    self_talk = _embedded(interaction, "自言自语")
    self_talk.enabled.setChecked(True)
    assert host.config.get("self_talk_enabled") is True
    assert interaction.dirty() is True

    dialog.cancel_button.click()

    assert host.config.get("self_talk_enabled") is False
    assert self_talk.enabled.isChecked() is False
    assert not (tmp_path / "config.json").exists()


def test_core_pages_host_their_feature_tabs(app, tmp_path):
    host = _host(tmp_path)
    host.menu_model = MenuModel()
    dialog = SettingsDialog(host)

    menu = _page(dialog, "菜单")
    assert menu.tab_container.labels() == ("菜单编排", "快捷启动")
    assert [page.title for page in menu._embedded_pages] == ["快捷启动"]

    launcher = menu._embedded_pages[0]
    launcher.editor.setPlainText("记事本|C:\\Windows\\notepad.exe|")
    assert menu.dirty() is True
    menu.confirm_save()
    assert host.config.get("quick_launch_apps") == [
        {"name": "记事本", "path": "C:\\Windows\\notepad.exe", "args": []}
    ]

    about = _page(dialog, "关于与诊断")
    assert about.tab_container.labels() == (
        "版本与诊断",
        "致谢",
        "链接与项目",
        "网络信息",
        "回收内存",
    )
    assert [page.title for page in about._embedded_pages] == ["链接与项目", "网络信息", "回收内存"]
    dialog.close()


def test_easter_display_mode_is_written_live(app, tmp_path):
    host = _host(tmp_path)
    writes: list[tuple[str, object]] = []
    original_set = host.config.set

    def recording_set(key, value):
        writes.append((str(key), value))
        original_set(key, value)

    host.config.set = recording_set
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    combo = interaction.easter_display
    assert combo.currentData() == "bubble"

    combo.setCurrentIndex(combo.findData("window"))

    assert combo.currentData() == "window"
    assert ("easter_egg_display", "window") in writes
    assert interaction.dirty() is True
    dialog.close()


def test_search_reveals_rows_inside_inner_tabs(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path))
    dialog.search.setText("点击时随机说话")
    app.processEvents()

    interaction = _page(dialog, "互动与物理")
    assert dialog.stack.currentWidget() is interaction
    assert interaction.tab_container.currentKey() == "click_talk"
    dialog.close()


def test_each_core_feature_page_is_built_exactly_once(app, tmp_path):
    host = _host(tmp_path)
    host.settings_pages = host.features.settings_pages
    dialog = SettingsDialog(host)

    extensions = _page(dialog, "扩展管理")
    for owner, feature in host.features.features.items():
        factory = feature.definition.settings_factory
        assert len(dialog.findChildren(factory)) == 1, owner
        assert extensions.findChildren(factory) == [], owner
    dialog.close()


def test_closing_dialog_disposes_embedded_pages(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")
    assert interaction._embedded_pages

    dialog.close()

    assert interaction._embedded_pages == []


def test_interaction_rows_remain_reachable_from_menu_coverage(app, tmp_path):
    from petcore.ui.settings.coverage import CORE_MENU_SETTINGS_COVERAGE

    dialog = SettingsDialog(_host(tmp_path))
    interaction = _page(dialog, "互动与物理")
    haystack = " ".join(interaction.keywords())
    for menu_id, (page_key, row) in CORE_MENU_SETTINGS_COVERAGE.items():
        if page_key != "interaction":
            continue
        assert row in haystack, (menu_id, row)
    dialog.close()


def test_wide_rows_stack_text_above_control(app) -> None:
    page = SettingsPage(None)
    try:
        card = page.section("测试")
        control = QWidget(page)
        row = card.add_row("路径", control, "很长的提示" * 6, expand=True)
        assert isinstance(row.layout(), QVBoxLayout)

        page.resize(620, 760)
        page.show()
        app.processEvents()
        assert control.width() > 400, control.width()

        plain = card.add_row("选项", QWidget(page), "短提示")
        assert isinstance(plain.layout(), QHBoxLayout)
        block = plain.findChild(QWidget, "settingTextBlock")
        assert block is not None
        assert block.minimumWidth() == 180
    finally:
        page.close()


def test_settings_dialog_has_minimize_and_maximize(app, tmp_path: Path) -> None:
    dialog = SettingsDialog(_host(tmp_path))
    try:
        flags = dialog.windowFlags()
        assert flags & Qt.WindowType.WindowMinMaxButtonsHint
        assert flags & Qt.WindowType.WindowCloseButtonHint
        assert dialog.isModal() is False

        dialog.show()
        app.processEvents()
        dialog.showMinimized()
        app.processEvents()
        assert dialog.isMinimized() is True

        dialog.showMaximized()
        app.processEvents()
        assert dialog.isMaximized() is True

        dialog.showNormal()
        app.processEvents()
        assert dialog.isMaximized() is False
        assert dialog.isMinimized() is False
    finally:
        dialog.close()


def test_head_pat_settings_are_scoped_live_and_cancelable(app, tmp_path):
    host = _host(tmp_path)
    host.window.character = CharacterSpec(
        id="fallback",
        name="可测试角色",
        root=tmp_path,
        media_kind="none",
        animations=(
            AnimationSpec("点击", GraphType.TOUCH_BODY, AnimatType.SINGLE),
            AnimationSpec("摸头", GraphType.TOUCH_HEAD, AnimatType.SINGLE),
        ),
    )
    host.window.catalog = GraphCatalog(host.window.character.animations)
    dialog = SettingsDialog(host)
    interaction = _page(dialog, "互动与物理")

    assert interaction.head_pat_enabled.isChecked() is True
    assert interaction.head_pat_mode.currentData() == "default_click"
    assert interaction.head_pat_names.count() == 2
    assert "摸头" in " ".join(interaction.keywords())

    interaction.head_pat_enabled.setChecked(False)
    interaction.head_pat_mode.setCurrentIndex(
        interaction.head_pat_mode.findData("custom")
    )
    interaction.head_pat_names.item(1).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    assert host.config.get("head_pat_enabled") is False
    assert host.config.get("head_pat_animation_mode") == "custom"
    assert host.config.get("head_pat_animation_names") == ["摸头"]

    interaction.discard_changes()
    assert host.config.get("head_pat_enabled") is True
    assert host.config.get("head_pat_animation_mode") == "default_click"
    assert host.config.get("head_pat_animation_names") == []
    assert interaction.head_pat_enabled.isChecked() is True
    assert interaction.head_pat_mode.currentData() == "default_click"
    dialog.close()


def test_p5_settings_icons_and_combos_keep_safe_geometry(app, tmp_path):
    dialog = SettingsDialog(_host(tmp_path))
    assert dialog.nav.iconSize() == QSize(MENU_ICON_SIZE, MENU_ICON_SIZE)
    # 侧栏行高 34px（主分支 `SIDEBAR_ROW_HEIGHT`）；旧的 42px 会让 18px 图标显得过大。
    assert all(dialog.nav.item(row).sizeHint().height() == 34 for row in range(dialog.nav.count()))
    combos = dialog.findChildren(QComboBox)
    assert combos
    for combo in combos:
        widest = max(
            (combo.fontMetrics().horizontalAdvance(combo.itemText(index)) for index in range(combo.count())),
            default=0,
        )
        assert combo.minimumWidth() >= widest + 58
    dialog.close()


def test_click_show_balance_is_per_slot_and_cancelable(app, tmp_path):
    host = _host(tmp_path)
    dialog = SettingsDialog(host, initial_page="interaction")
    page = _page(dialog, "互动与物理")
    box = page._checks["click_show_balance"]
    assert box.isChecked() is False
    box.setChecked(True)
    app.processEvents()
    assert host.config.get("click_show_balance") is True
    dialog.cancel_button.click()
    assert host.config.get("click_show_balance") is False
    assert box.isChecked() is False

    child = Config(tmp_path, filename="config-pet-1.json")
    child.set("click_show_balance", True)
    child.save()
    assert Config.load(tmp_path).get("click_show_balance") is False
    assert Config.load(tmp_path, filename="config-pet-1.json").get("click_show_balance") is True


def test_api_page_stays_reachable_at_the_minimum_window_size(app, tmp_path) -> None:
    """720×500 下「保存服务」必须可达：内容不裁或横向滚动条可用（AUD-26）。"""
    from PySide6.QtWidgets import QScrollArea

    dialog = SettingsDialog(_host(Path(tmp_path)))
    try:
        api = next(entry.page for entry in dialog._pages if entry.key == "api")  # noqa: SLF001
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()
        dialog._select_row([e.key for e in dialog._pages].index("api"))  # noqa: SLF001
        app.processEvents()
        scroll = api.findChild(QScrollArea)
        assert scroll is not None
        if api.content.width() > scroll.viewport().width():
            bar = scroll.horizontalScrollBar()
            assert bar.maximum() > 0
            assert bar.isVisible() or scroll.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        else:
            assert api.content.width() <= scroll.viewport().width()
    finally:
        dialog.close()


def test_menu_editor_is_clean_right_after_construction(app, tmp_path) -> None:
    """没碰过的菜单不算 dirty：否则每次保存都会重写 menu_layout（AUD-28）。"""
    from petcore.ui.menu_model import MenuModel
    from petcore.ui.settings.menu_editor import MenuEditor

    host = _host(Path(tmp_path))
    host.menu_model = MenuModel()
    editor = MenuEditor(host, host.menu_model)
    try:
        assert editor.is_dirty() is False
        editor.confirm_save()
        assert editor.is_dirty() is False, "保存之后仍应是干净的"
    finally:
        editor.deleteLater()


def test_interaction_page_does_not_revert_external_menu_changes(app, tmp_path) -> None:
    """菜单里刚改的勾选项，不许被互动页的整组回写翻回去（AUD-27）。"""
    host = _host(Path(tmp_path))
    dialog = SettingsDialog(host)
    try:
        page = next(entry.page for entry in dialog._pages if entry.key == "interaction")  # noqa: SLF001
        # 模拟用户从右键菜单勾上「黄金回旋」。
        host.config.set("golden_spin_on_click", True)
        assert host.config.get("golden_spin_on_click") is True
        # 然后在设置页里改**另一个**勾选项。
        page._checks["click_sound"].setChecked(not page._checks["click_sound"].isChecked())  # noqa: SLF001
        assert host.config.get("golden_spin_on_click") is True, "未触碰的键被旧控件值覆盖了"
    finally:
        dialog.close()


# ---------------------------------------------------------------- 重置（命令）


def test_reset_buttons_sit_in_the_sidebar(app, tmp_path) -> None:
    host = _host(Path(tmp_path))
    dialog = SettingsDialog(host)
    try:
        assert dialog.reset_pet_button.text() == "重置本宠设置…"
        assert dialog.reset_global_button.text() == "重置全局设置…"
        assert dialog.reset_pet_button.accessibleName() == "重置本宠设置"
        assert dialog.reset_global_button.accessibleName() == "重置全局设置"
        # 命令类控件不进页面列表/搜索索引（SETTINGS-CHANGE-GATES 2026-10-17 条）。
        assert "重置" not in [entry.title for entry in dialog._pages]  # noqa: SLF001
    finally:
        dialog.close()


def test_reset_cancel_changes_nothing(app, tmp_path) -> None:
    host = _host(Path(tmp_path))
    host.config.set("ui_font_size", 17)
    dialog = SettingsDialog(host)
    try:
        dialog._confirm_reset = lambda scope: False  # noqa: SLF001
        dialog.reset_global_button.click()
        assert host.config.get("ui_font_size") == 17
        assert dialog.result() != QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def test_reset_global_closes_without_committing_stale_widgets(app, tmp_path) -> None:
    host = _host(Path(tmp_path))
    host.config.set("ui_font_size", 17)
    dialog = SettingsDialog(host)
    commits: list[int] = []
    try:
        dialog._confirm_reset = lambda scope: True  # noqa: SLF001
        dialog._commit = lambda: commits.append(1) or True  # noqa: SLF001
        dialog.reset_global_button.click()
        assert commits == [], "重置后关闭不能再 _commit（旧控件值会把默认值写回去）"
        assert host.config.get("ui_font_size") == DEFAULTS["ui_font_size"]
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.isVisible() is False
    finally:
        dialog.deleteLater()


def test_reset_pet_keeps_identity_and_global_keys(app, tmp_path) -> None:
    host = _host(Path(tmp_path))
    host.config.set("character", "fallback")
    host.config.set("scale", 1.4)
    host.config.set("ui_font_size", 17)
    dialog = SettingsDialog(host)
    try:
        dialog._confirm_reset = lambda scope: True  # noqa: SLF001
        dialog.reset_pet_button.click()
        assert host.config.get("scale") == DEFAULTS["scale"]
        assert host.config.get("character") == "fallback"
        assert host.config.get("ui_font_size") == 17
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("font_size", [13, 18])
def test_reset_buttons_fit_the_sidebar_at_the_minimum_size(
    app, tmp_path, font_size
) -> None:
    """侧栏底部多了两枚按钮：最小窗口 + 最大字号下不许被挤出视口。"""
    host = _host(Path(tmp_path))
    host.config.set("ui_font_size", font_size)
    dialog = SettingsDialog(host)
    try:
        dialog.resize(720, 500)
        dialog.show()
        QApplication.processEvents()
        sidebar = dialog.reset_pet_button.parentWidget()
        for button in (dialog.reset_pet_button, dialog.reset_global_button):
            top_left = button.mapTo(sidebar, QPoint(0, 0))
            rect = QRect(top_left, button.size())
            assert button.isVisible(), "重置按钮在最小窗口下不可见"
            assert sidebar.rect().contains(rect), "重置按钮被挤出侧栏视口"
        # 导航仍在上面正常占位（按钮只吃底部那一块）。
        assert dialog.nav.height() > 0
    finally:
        dialog.deleteLater()


def test_interaction_page_exposes_squash_elasticity(app, tmp_path: Path) -> None:
    host = _host(tmp_path)
    dialog = SettingsDialog(host)
    page = _page(dialog, "互动与物理")
    assert page.squash_bounces.value() == 1
    assert page.squash_strength.value() == 100
    assert page.squash_ms.value() == 220

    page.squash_bounces.setValue(3)
    page.squash_strength.setValue(150)
    page.squash_ms.setValue(600)
    assert host.config.get("click_squash_bounces") == 3
    assert host.config.get("click_squash_strength") == 150
    assert host.config.get("click_squash_ms") == 600

    page.discard_changes()
    assert page.squash_bounces.value() == 1
    assert page.squash_strength.value() == 100
    assert page.squash_ms.value() == 220
    dialog.deleteLater()


def test_appearance_stream_mode_and_key_color_round_trip(app, tmp_path: Path) -> None:
    host = _host(tmp_path)
    dialog = SettingsDialog(host, initial_page="appearance")
    try:
        page = _page(dialog, "外观与交互")
        page.stream_mode.setCurrentIndex(page.stream_mode.findData("keycolor"))
        assert host.config.get("stream_capture_mode") is True
        assert host.config.get("stream_capture_keycolor") is True
        page.stream_key_color.setText("#FF00FF")
        page._on_stream_key_color()
        assert host.config.get("stream_key_color") == "#FF00FF"

        # 从色键切回关闭后点保存：keycolor 必须先关，否则 normalize 把 mode 翻回 True。
        page.stream_mode.setCurrentIndex(page.stream_mode.findData("off"))
        page.confirm_save()
        assert host.config.get("stream_capture_mode") is False
        assert host.config.get("stream_capture_keycolor") is False

        page.discard_changes()
        assert host.config.get("stream_capture_mode") is False
        assert host.config.get("stream_capture_keycolor") is False
        assert page.stream_mode.currentData() == "off"
        assert host.config.get("stream_key_color") == "#00FF00"
    finally:
        dialog.deleteLater()
