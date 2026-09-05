from pet.menu_layout import (
    load_default_menu_layout,
    materialize_implicit_separators,
    resolve_menu_layout,
)


def test_modern_default_v1_has_compact_root_and_safety_actions():
    layout = load_default_menu_layout("modern-default-v1")

    assert layout["schema_version"] == 1
    assert layout["layout_id"] == "modern-default-v1"
    assert [node["id"] for node in layout["nodes"]] == [
        "ojingjing",
        "default.separator-profile",
        "chat",
        "look_screen",
        "default.separator-interaction",
        "animations_hub",
        "character",
        "playback_speed",
        "size",
        "default.separator-playback",
        "pet_controls",
        "quick_launch",
        "default.separator-pet",
        "tools_help",
        "agent_link",
        "proactive_screen",
        "todo_panel",
        "default.separator-tools",
        "modern_settings",
        "quit",
    ]
    assert layout["nodes"][-2:] == [
        {"type": "action", "id": "modern_settings", "visible": True, "section": "system"},
        {"type": "action", "id": "quit", "visible": True, "section": "system"},
    ]


def test_legacy_section_boundaries_become_editable_separator_nodes():
    upgraded = materialize_implicit_separators({
        "schema_version": 1,
        "layout_id": "user",
        "nodes": [
            {"type": "action", "id": "chat", "visible": True, "section": "one"},
            {"type": "action", "id": "look_screen", "visible": True, "section": "two"},
        ],
    })

    assert [node["type"] for node in upgraded["nodes"]] == [
        "action", "separator", "action"
    ]
    assert upgraded["nodes"][1]["id"] == "user.separator-1"


def test_invalid_layout_falls_back_to_minimum_safe_menu():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {"type": "action", "id": "quit", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        registered_actions={"modern_settings", "quit"},
        available_actions={"modern_settings", "quit"},
    )

    assert result.source == "fallback"
    assert result.diagnostics == ("duplicate-action:quit",)
    assert result.nodes == (
        {"type": "action", "id": "modern_settings", "visible": True},
        {"type": "action", "id": "quit", "visible": True},
    )


def test_user_layout_filters_hidden_unavailable_and_empty_submenus():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {"type": "action", "id": "chat", "visible": False},
                {"type": "action", "id": "look_screen", "visible": True},
                {
                    "type": "submenu",
                    "id": "pet_controls",
                    "label": "桌宠控制",
                    "visible": True,
                    "children": [
                        {"type": "action", "id": "no_move", "visible": True},
                        {"type": "action", "id": "mouse_through", "visible": True},
                    ],
                },
                {
                    "type": "submenu",
                    "id": "empty_tools",
                    "label": "空工具",
                    "visible": True,
                    "children": [
                        {"type": "action", "id": "balance", "visible": False}
                    ],
                },
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        registered_actions={
            "chat",
            "look_screen",
            "no_move",
            "mouse_through",
            "balance",
            "modern_settings",
            "quit",
        },
        available_actions={"no_move", "modern_settings", "quit"},
    )

    assert result.source == "user"
    assert result.diagnostics == ()
    assert result.nodes == (
        {
            "type": "submenu",
            "id": "pet_controls",
            "label": "桌宠控制",
            "visible": True,
            "children": (
                {"type": "action", "id": "no_move", "visible": True},
            ),
        },
        {"type": "action", "id": "modern_settings", "visible": True},
        {"type": "action", "id": "quit", "visible": True},
    )


def test_layout_preserves_alias_icon_and_explicit_separator_nodes():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {
                    "type": "action",
                    "id": "chat",
                    "visible": True,
                    "alias": "和鲸鱼聊聊",
                    "icon": "screen",
                },
                {"type": "separator", "id": "user.separator-1", "visible": True},
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        registered_actions={"chat", "modern_settings", "quit"},
        available_actions={"chat", "modern_settings", "quit"},
    )

    assert result.nodes[0] == {
        "type": "action",
        "id": "chat",
        "visible": True,
        "alias": "和鲸鱼聊聊",
        "icon": "screen",
    }
    assert result.nodes[1] == {
        "type": "separator",
        "id": "user.separator-1",
        "visible": True,
    }


def test_runtime_feature_state_does_not_filter_menu_layout_actions():
    from pet.context_menus.registry import MENU_ACTIONS

    class Config:
        def get(self, key, default=None):
            if key == "menu_easter_egg":
                return {"enabled": False}
            if key == "quick_launch_apps":
                return []
            return default

    class Pet:
        cfg = Config()
        on_open_chat = lambda self: None
        on_look_screen = lambda self: None
        on_show_balance = lambda self, parent=None: None
        on_check_update = lambda self, parent=None: None
        on_open_modern_settings = lambda self: None
        on_spawn_pet = lambda self: None

    pet = Pet()
    assert {"ojingjing", "quick_launch"} <= MENU_ACTIONS.available_ids(pet)
    assert {"ojingjing", "quick_launch"}.isdisjoint(MENU_ACTIONS.enabled_ids(pet))


def test_runtime_menu_renders_alias_icon_separator_and_disabled_item():
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.registry import MENU_ACTIONS

    class Config:
        def get(self, key, default=None):
            return [] if key == "quick_launch_apps" else default

    class Pet:
        cfg = Config()
        on_open_chat = lambda self: None
        on_open_modern_settings = lambda self: None
        request_quit = lambda self: None

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    nodes = (
        {"type": "action", "id": "chat", "alias": "和鲸鱼聊聊", "icon": "screen"},
        {"type": "separator", "id": "user.separator-1"},
        {"type": "action", "id": "quick_launch"},
    )
    MENU_ACTIONS.populate(menu, Pet(), nodes)

    actions = menu.actions()
    assert actions[0].text() == "和鲸鱼聊聊"
    assert not actions[0].icon().isNull()
    assert actions[1].isSeparator()
    assert actions[2].text() == "快捷启动"
    assert not actions[2].isEnabled()
    assert actions[2].toolTip() == "尚未配置快捷启动应用"
    assert actions[2].menu().actions()[0].text() == "尚未配置快捷项"
    menu.close()
    app.processEvents()


def test_menu_editor_customizes_alias_icon_separator_and_shows_runtime_state():
    from PySide6.QtWidgets import QApplication

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(
        None,
        available_actions=MENU_ACTIONS.ids,
        enabled_actions=MENU_ACTIONS.ids - {"quick_launch"},
    )

    assert [editor.tree.headerItem().text(index) for index in range(3)] == [
        "菜单项", "状态", "位置",
    ]
    quick_launch = editor.item_for_action("quick_launch")
    assert quick_launch is not None
    assert quick_launch.text(1) == "已停用"
    assert quick_launch.flags() & __import__("PySide6").QtCore.Qt.ItemFlag.ItemIsEnabled

    editor.set_item_alias("chat", "和鲸鱼聊聊")
    editor.set_item_icon("chat", "screen")
    editor.insert_separator(after_action_id="chat")

    nodes = editor.value()["nodes"]
    chat_index = next(index for index, node in enumerate(nodes) if node.get("id") == "chat")
    assert nodes[chat_index]["alias"] == "和鲸鱼聊聊"
    assert nodes[chat_index]["icon"] == "screen"
    assert nodes[chat_index + 1]["type"] == "separator"
    editor.close()
    app.processEvents()


def test_layout_restores_required_settings_and_exit_actions():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {"type": "action", "id": "chat", "visible": True},
                {"type": "action", "id": "modern_settings", "visible": False},
            ],
        },
        registered_actions={"chat", "modern_settings", "quit"},
        available_actions={"chat", "modern_settings", "quit"},
    )

    assert result.source == "normalized"
    assert result.diagnostics == (
        "required-action-restored:modern_settings",
        "required-action-restored:quit",
    )
    assert [node["id"] for node in result.nodes if node["type"] != "separator"] == [
        "chat",
        "modern_settings",
        "quit",
    ]


def test_missing_user_layout_resolves_versioned_default():
    registered = {
        "ojingjing",
        "chat",
        "look_screen",
        "animations_hub",
        "character",
        "playback_speed",
        "size",
        "drag_physics",
        "no_move",
        "mouse_through",
        "on_top",
        "autostart",
        "return_corner",
        "hide_pet",
        "spawn_pet",
        "quick_launch",
        "balance",
        "harness",
        "deepseek_web",
        "check_update",
        "github_project",
        "quark_download",
        "agent_link",
        "proactive_screen",
        "todo_panel",
        "modern_settings",
        "quit",
    }

    result = resolve_menu_layout(
        None,
        registered_actions=registered,
        available_actions=registered,
    )

    assert result.source == "default"
    assert result.diagnostics == ()
    assert [node["id"] for node in result.nodes if node["type"] != "separator"] == [
        "ojingjing",
        "chat",
        "look_screen",
        "animations_hub",
        "character",
        "playback_speed",
        "size",
        "pet_controls",
        "quick_launch",
        "tools_help",
        "agent_link",
        "proactive_screen",
        "todo_panel",
        "modern_settings",
        "quit",
    ]


def test_config_persists_menu_layout_override_without_copying_default(tmp_path):
    from pet.config import Config

    config = Config(tmp_path)
    assert config.get("context_menu_layout") is None

    override = {
        "schema_version": 1,
        "layout_id": "user",
        "nodes": [
            {"type": "action", "id": "modern_settings", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    config.set("context_menu_layout", override)
    config.save()

    restored = Config(tmp_path)
    assert restored.get("context_menu_layout") == override


def test_unknown_schema_uses_safe_fallback_with_migration_diagnostic():
    result = resolve_menu_layout(
        {
            "schema_version": 99,
            "layout_id": "future",
            "nodes": [{"type": "action", "id": "chat", "visible": True}],
        },
        registered_actions={"chat", "modern_settings", "quit"},
        available_actions={"chat", "modern_settings", "quit"},
    )

    assert result.source == "fallback"
    assert result.diagnostics == ("unsupported-schema:99",)
    assert [node["id"] for node in result.nodes] == ["modern_settings", "quit"]


def test_default_layout_populates_real_qmenu_hierarchy(monkeypatch):
    import sys
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    from pet import catalog
    from pet.context_menu import populate_context_menu
    from pet.context_menus import shared

    class FakeConfig:
        values = {
            "context_menu_template": "modern",
            "context_menu_layout": None,
            "context_menu_appearance": {"theme": "light"},
            "character": "shenshen",
            "on_top": True,
            "agent_link": {},
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

        def save(self):
            return None

    class FakePet:
        cfg = FakeConfig()
        on_open_chat = lambda self: None
        on_look_screen = lambda self: None
        on_show_balance = lambda self, parent=None: None
        on_check_update = lambda self, parent=None: None
        on_open_modern_settings = lambda self: None
        on_spawn_pet = lambda self: None
        idles = ["待机"]
        turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = mouse_through = False

        def icon_pixmap(self, size=64):
            pixmap = QPixmap(size, size)
            pixmap.fill()
            return pixmap

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(shared.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(catalog, "list_available_characters", lambda: ["shenshen"])
    menu = QMenu()

    populate_context_menu(menu, FakePet())

    root = [action.text() for action in menu.actions() if not action.isSeparator()]
    expected_root = [
        "厉害了我的鲸",
        "AI 对话",
        "看看屏幕",
        "播放动画",
        "切换角色",
        "播放速率",
        "大小",
        "桌宠控制",
        "快捷启动",
        "工具与帮助",
        "Agent 联动",
        "桌宠设置",
        "退出",
    ]
    if sys.platform == "win32":
        expected_root.insert(-2, "主动识屏")
        expected_root.insert(-2, "待办提醒")
    else:
        expected_root.insert(-2, "待办提醒")
    assert root == expected_root
    rendered = ["|" if action.isSeparator() else action.text() for action in menu.actions()]
    expected_rendered = [
        "厉害了我的鲸", "|",
        "AI 对话", "看看屏幕", "|",
        "播放动画", "切换角色", "播放速率", "大小", "|",
        "桌宠控制", "快捷启动", "|",
        "工具与帮助", "Agent 联动", "|",
        "桌宠设置", "退出",
    ]
    if sys.platform == "win32":
        expected_rendered.insert(-3, "主动识屏")
    # tools 段顺序：… Agent 联动 [主动识屏] 待办提醒 | 桌宠设置 退出
    expected_rendered.insert(-3, "待办提醒")
    assert rendered == expected_rendered
    pet_controls = next(action.menu() for action in menu.actions() if action.text() == "桌宠控制")
    assert [action.text() for action in pet_controls.actions() if not action.isSeparator()] == [
        "拖动物理",
        "不移动",
        "鼠标穿透",
        "窗口置顶",
        "开机自启",
        "回到右下角",
        "隐藏桌宠",
        "生小肥鱼",
    ]
    tools = next(action.menu() for action in menu.actions() if action.text() == "工具与帮助")
    expected_tools = [
        "DeepSeek 余额",
        "启动 DeepSeek Harness",
        "打开网页版 DeepSeek",
        "检查更新",
        "GitHub 项目页",
    ]
    if sys.platform == "win32":
        expected_tools.append("夸克网盘下载")
    assert [action.text() for action in tools.actions() if not action.isSeparator()] == expected_tools
    menu.close()
    app.processEvents()


def test_settings_menu_editor_commits_visibility_draft(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = ModernSettingsDialog(config, include_ai=False)

    sidebar_labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
    assert "菜单" in sidebar_labels
    chat_item = dialog.menu_layout_editor.item_for_action("chat")
    assert chat_item is not None
    chat_item.setCheckState(0, Qt.CheckState.Unchecked)
    assert Config(tmp_path).get("context_menu_layout") is None
    dialog.save_exit_button.click()

    restored = Config(tmp_path)
    result = resolve_menu_layout(
        restored.get("context_menu_layout"),
        registered_actions=MENU_ACTIONS.ids,
        available_actions=MENU_ACTIONS.ids,
    )
    assert "chat" not in [node["id"] for node in result.nodes]
    app.processEvents()


def test_settings_menu_editor_moves_action_into_submenu_without_dragging(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    editor = dialog.menu_layout_editor
    editor.tree.setCurrentItem(editor.item_for_action("playback_speed"))
    editor.move_menu.aboutToShow.emit()
    next(action for action in editor.move_menu.actions() if action.data() == "pet_controls").trigger()
    dialog.save_exit_button.click()

    nodes = Config(tmp_path).get("context_menu_layout")["nodes"]
    pet_controls = next(node for node in nodes if node["id"] == "pet_controls")
    assert [child["id"] for child in pet_controls["children"]][-1] == "playback_speed"
    app.processEvents()


def test_menu_editor_reorders_and_promotes_actions_with_button_controls():
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    root_ids = [node["id"] for node in editor.value()["nodes"]]
    editor.tree.setCurrentItem(editor.item_for_action("chat"))
    editor.move_down_action.trigger()
    reordered_ids = [node["id"] for node in editor.value()["nodes"]]
    chat_index = root_ids.index("chat")
    assert reordered_ids[chat_index : chat_index + 2] == ["look_screen", "chat"]

    promoted = editor.item_for_action("drag_physics")
    editor.tree.setCurrentItem(promoted)
    editor.move_menu.aboutToShow.emit()
    next(action for action in editor.move_menu.actions() if action.data() == "__root__").trigger()

    assert promoted.parent() is None
    assert editor.value()["nodes"][-1]["id"] == "drag_physics"
    assert editor.preview.topLevelItem(editor.preview.topLevelItemCount() - 1).text(0) == "拖动物理"
    editor.close()
    app.processEvents()


def test_menu_editor_reset_restores_the_versioned_default():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.item_for_action("chat").setCheckState(0, Qt.CheckState.Unchecked)
    editor.tree.setCurrentItem(editor.item_for_action("drag_physics"))
    editor.move_menu.aboutToShow.emit()
    next(action for action in editor.move_menu.actions() if action.data() == "__root__").trigger()

    editor.reset_action.trigger()

    assert editor.value()["nodes"] == load_default_menu_layout()["nodes"]
    editor.close()
    app.processEvents()


def test_settings_menu_editor_creates_named_submenu(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("常用操作", True))
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)

    dialog.menu_layout_editor.new_submenu_action.trigger()

    user_submenus = [
        node for node in dialog.menu_layout_editor.value()["nodes"]
        if node["type"] == "submenu" and node["id"].startswith("user.")
    ]
    assert [(node["label"], node["children"]) for node in user_submenus] == [
        ("常用操作", [])
    ]
    dialog.reject()
    app.processEvents()


def test_menu_editor_confirms_before_deleting_submenu_and_preserves_children(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from pet.menu_layout import load_default_menu_layout
    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    layout = load_default_menu_layout()
    chat = next(node for node in layout["nodes"] if node["id"] == "chat")
    layout["nodes"].remove(chat)
    layout["nodes"].insert(1, {
        "type": "submenu", "id": "user.work", "label": "工作",
        "visible": True, "children": [chat],
    })
    editor = MenuLayoutEditor(layout)
    submenu = editor.tree.topLevelItem(1)
    editor.tree.setCurrentItem(submenu)

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    editor.delete_submenu_action.trigger()
    assert any(node["id"] == "user.work" for node in editor.value()["nodes"])

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    editor.delete_submenu_action.trigger()
    nodes = editor.value()["nodes"]
    assert all(node["id"] != "user.work" for node in nodes)
    assert nodes[1]["id"] == "chat"
    editor.close()
    app.processEvents()


def test_menu_editor_removes_submenu_after_its_last_item_moves_out():
    from PySide6.QtWidgets import QApplication

    from pet.menu_layout import load_default_menu_layout
    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    layout = load_default_menu_layout()
    chat = next(node for node in layout["nodes"] if node["id"] == "chat")
    layout["nodes"].remove(chat)
    layout["nodes"].insert(1, {
        "type": "submenu", "id": "user.work", "label": "工作",
        "visible": True, "children": [chat],
    })
    editor = MenuLayoutEditor(layout)
    editor.tree.setCurrentItem(editor.item_for_action("chat"))
    editor.move_menu.aboutToShow.emit()
    root_action = next(
        action for action in editor.move_menu.actions()
        if action.text() == "根菜单"
    )
    root_action.trigger()

    nodes = editor.value()["nodes"]
    assert all(node["id"] != "user.work" for node in nodes)
    assert any(node["id"] == "chat" for node in nodes)
    editor.close()
    app.processEvents()


def test_menu_editor_drag_model_cleanup_removes_the_emptied_source_submenu():
    from PySide6.QtWidgets import QApplication

    from pet.menu_layout import load_default_menu_layout
    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    layout = load_default_menu_layout()
    chat = next(node for node in layout["nodes"] if node["id"] == "chat")
    layout["nodes"].remove(chat)
    layout["nodes"].insert(1, {
        "type": "submenu", "id": "user.drag", "label": "拖拽来源",
        "visible": True, "children": [chat],
    })
    editor = MenuLayoutEditor(layout)
    submenu = editor.tree.topLevelItem(1)
    moved = submenu.takeChild(0)
    editor.tree.addTopLevelItem(moved)
    app.processEvents()

    assert all(node["id"] != "user.drag" for node in editor.value()["nodes"])
    assert any(node["id"] == "chat" for node in editor.value()["nodes"])
    editor.close()
    app.processEvents()


def test_user_layout_rejects_nested_submenus_beyond_one_level():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [{
                "type": "submenu",
                "id": "user.outer",
                "label": "外层",
                "visible": True,
                "children": [{
                    "type": "submenu",
                    "id": "user.inner",
                    "label": "内层",
                    "visible": True,
                    "children": [
                        {"type": "action", "id": "chat", "visible": True}
                    ],
                }],
            }],
        },
        registered_actions={"chat", "modern_settings", "quit"},
        available_actions={"chat", "modern_settings", "quit"},
    )

    assert result.source == "fallback"
    assert result.diagnostics == ("submenu-depth-exceeded:user.inner",)
    assert [node["id"] for node in result.nodes] == ["modern_settings", "quit"]


def test_settings_sidebar_uses_stable_domains_and_owns_representative_rows(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    expected = ["常规", "桌宠", "互动", "菜单", "桌面组件", "AI 与对话", "自动化与联动"]
    assert [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())] == expected

    def owner(setting_id):
        row = dialog.findChild(SettingRow, f"settingRow_{setting_id}")
        return next(expected[index] for index in range(dialog.pages.count()) if dialog.pages.widget(index).isAncestorOf(row))

    assert owner("mouse_through") == "互动"
    assert owner("menu_theme") == "菜单"
    assert owner("quick_launch_apps") == "菜单"
    assert owner("dynamic_island_enabled") == "桌面组件"
    assert owner("api_url") == "AI 与对话"
    assert owner("agent_thinking_dsh") == "自动化与联动"
    assert "待分类（开发期）" not in [
        label.text() for label in dialog.findChildren(settings_mod.QLabel)
    ]
    dialog.reject()
    app.processEvents()


def test_menu_domain_uses_in_page_task_tabs_without_changing_sidebar(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow, SettingsTabContainer

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)

    expected_sidebar = ["常规", "桌宠", "互动", "菜单", "桌面组件", "AI 与对话", "自动化与联动"]
    assert [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())] == expected_sidebar
    tabs = dialog.pages.widget(3).findChild(SettingsTabContainer, "settingsTaskTabs")
    assert tabs is not None
    assert tabs.keys() == ("layout", "launcher", "appearance")
    assert tabs.labels() == ("菜单编排", "快捷启动", "外观")

    layout_row = dialog.findChild(SettingRow, "settingRow_context_menu_layout")
    launcher_row = dialog.findChild(SettingRow, "settingRow_quick_launch_apps")
    appearance_row = dialog.findChild(SettingRow, "settingRow_menu_theme")
    assert tabs.key_for_descendant(layout_row) == "layout"
    assert tabs.key_for_descendant(launcher_row) == "launcher"
    assert tabs.key_for_descendant(appearance_row) == "appearance"

    dialog.search_edit.setText("应用快捷启动")
    app.processEvents()
    assert dialog.sidebar.currentItem().text() == "菜单"
    assert tabs.currentKey() == "launcher"
    dialog.reject()
    app.processEvents()


def test_runtime_toggles_refresh_menu_status_without_rewriting_tree(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    before = dialog.menu_layout_editor.value()["nodes"]

    dialog.egg_enabled_check.setChecked(False)
    dialog.quick_launch_editor.list.clear()
    dialog.quick_launch_editor._sync_content_height()
    app.processEvents()

    assert dialog.menu_layout_editor.item_for_action("ojingjing").text(1) == "已停用"
    assert dialog.menu_layout_editor.item_for_action("quick_launch").text(1) == "已停用"
    assert dialog.menu_layout_editor.value()["nodes"] == before
    dialog.reject()
    app.processEvents()


def test_advanced_setting_groups_use_single_collapsed_disclosure_layer(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QToolButton

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import (
        ModernSettingsDialog,
        SettingsDisclosureHeader,
        SettingsSection,
    )

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)

    color_toggle = next(
        button for button in dialog.findChildren(SettingsDisclosureHeader)
        if button.text() == "高级配色"
    )
    assert not dialog.findChildren(QToolButton, "advancedSectionToggle")
    color_section = color_toggle.parentWidget()
    assert isinstance(color_section, SettingsSection)
    assert color_section.card.isHidden()
    assert not color_toggle.isChecked()

    color_toggle.click()

    assert not color_section.card.isHidden()
    assert color_toggle.isChecked()
    dialog.reject()
    app.processEvents()


def test_ai_settings_content_expands_to_the_shared_page_width(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.resize(1100, 760)
    dialog.sidebar.setCurrentRow(5)
    dialog.show()
    app.processEvents()

    ai_domain = dialog.findChild(settings_mod.QWidget, "settingsDomain_ai")
    assert ai_domain is not None
    assert ai_domain.width() >= ai_domain.parentWidget().width() - 2
    assert not dialog.ai_page.isVisible()
    dialog.reject()
    app.processEvents()


def test_settings_visual_hierarchy_uses_shared_product_tokens(tmp_path, monkeypatch):
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.show()
    app.processEvents()

    assert dialog.findChild(settings_mod.QFrame, "sidebarPane").width() == 200
    assert dialog.sidebar.iconSize() == QSize(18, 18)
    page_title = dialog.pages.currentWidget().findChild(settings_mod.QLabel, "pageTitle")
    section_title = dialog.pages.currentWidget().findChild(settings_mod.QLabel, "sectionTitle")
    row = dialog.findChild(SettingRow, "settingRow_autostart")
    assert page_title.font().pixelSize() == 22
    assert section_title.font().pixelSize() == 13
    assert row.label.font().pixelSize() == 13
    assert row.label.font().weight() == 500
    assert row.hint_label.font().pixelSize() == 12
    dialog.reject()
    app.processEvents()


def test_wide_settings_title_tracks_the_centered_page_content(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QScrollArea

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.resize(1600, 900)
    dialog.show()
    app.processEvents()

    page = dialog.pages.currentWidget()
    title = page.findChild(settings_mod.QLabel, "pageTitle")
    scroll = page.findChild(QScrollArea, "settingsScroll")
    content = scroll.widget()
    assert title.mapTo(page, QPoint(0, 0)).x() == content.mapTo(page, QPoint(0, 0)).x()

    dialog.resize(720, 700)
    app.processEvents()
    assert title.mapTo(page, QPoint(0, 0)).x() == content.mapTo(page, QPoint(0, 0)).x()
    dialog.reject()
    app.processEvents()


def test_setting_rows_name_and_describe_their_controls_for_accessibility(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    row = dialog.findChild(SettingRow, "settingRow_autostart")

    assert row.label.buddy() is row.control
    assert row.label.wordWrap()
    assert row.control.accessibleName() == row.label.text()
    assert row.control.accessibleDescription() == row.hint_label.text()
    assert "QPushButton:focus" in dialog.styleSheet()

    dialog.reject()
    app.processEvents()


def test_setting_row_stacks_a_wide_control_before_copy_becomes_unreadable():
    from PySide6.QtWidgets import QApplication, QPushButton

    from pet.modern_settings_dialog import SettingRow

    app = QApplication.instance() or QApplication([])
    control = QPushButton("宽控件")
    control.setFixedWidth(340)
    row = SettingRow(
        "responsive",
        "跨平台同步与自动恢复策略的超长本地化标题示例",
        "说明文字必须保持可读。",
        control,
    )
    row.resize(500, 180)
    row.show()
    app.processEvents()

    assert control.y() > row.label.y() + row.label.height()
    assert row.property("responsiveStacked") is True

    row.resize(900, 180)
    app.processEvents()

    assert control.x() > row.label.x()
    assert row.property("responsiveStacked") is False
    row.close()
    app.processEvents()


def test_compact_ai_provider_controls_stay_inside_their_setting_row(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QScrollArea

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.resize(720, 760)
    dialog.sidebar.setCurrentRow(5)
    for control in (
        dialog.ai_page.provider_combo,
        dialog.ai_page.add_provider_btn,
        dialog.ai_page.delete_provider_btn,
    ):
        font = control.font()
        font.setPixelSize(17)
        control.setFont(font)
    dialog.ai_page.add_provider_btn.setText("添加新的 Provider")
    dialog.ai_page.delete_provider_btn.setText("删除当前 Provider")
    dialog.show()
    dialog.resize(721, 760)
    app.processEvents()
    dialog.resize(720, 760)
    app.processEvents()

    row = dialog.findChild(SettingRow, "settingRow_provider_list")
    provider_controls = dialog.ai_page.provider_combo.parentWidget()
    left = provider_controls.mapTo(row, QPoint(0, 0)).x()
    assert left >= 16
    assert left + provider_controls.width() <= row.width() - 16
    scroll = row.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert scroll is not None
    row_left = row.mapTo(scroll.viewport(), QPoint(0, 0)).x()
    assert row_left >= 0
    assert row_left + row.width() <= scroll.viewport().width()
    dialog.reject()
    app.processEvents()


def test_responsive_action_row_can_stack_wide_actions_vertically():
    from PySide6.QtWidgets import QApplication, QPushButton

    from pet.modern_settings_dialog import ModernSelect, ResponsiveActionRow

    app = QApplication.instance() or QApplication([])
    primary = ModernSelect(width=230)
    first = QPushButton("第一个很长的本地化操作")
    second = QPushButton("第二个很长的本地化操作")
    first.setMinimumWidth(260)
    second.setMinimumWidth(260)
    row = ResponsiveActionRow(primary, [first, second])
    row.setFixedWidth(454)
    row.resize(454, 180)
    row.show()
    app.processEvents()

    assert row.minimumSizeHint().width() <= 260
    assert row.property("responsiveMode") == "compact"
    assert first.y() < second.y()
    assert first.geometry().right() <= row.rect().right()
    assert second.geometry().right() <= row.rect().right()
    row.close()
    app.processEvents()


def test_responsive_action_row_inline_mode_does_not_reserve_compact_height():
    from PySide6.QtWidgets import QApplication, QPushButton

    from pet.modern_settings_dialog import ModernSelect, ResponsiveActionRow

    app = QApplication.instance() or QApplication([])
    primary = ModernSelect(width=230)
    first = QPushButton("新增")
    second = QPushButton("删除")
    row = ResponsiveActionRow(primary, [first, second])
    row.setFixedWidth(800)
    row.show()
    app.processEvents()

    expected_height = max(
        widget.minimumHeight() or widget.minimumSizeHint().height()
        for widget in (primary, first, second)
    )
    assert row.property("responsiveMode") == "inline"
    assert row.minimumSizeHint().height() == expected_height
    row.close()
    app.processEvents()


def test_compact_agent_sound_controls_reflow_inside_their_setting_row(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QScrollArea

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    dialog.agent_sound_check.setChecked(True)
    dialog.resize(720, 760)
    automation_index = next(
        index for index in range(dialog.sidebar.count())
        if dialog.sidebar.item(index).text() == "自动化与联动"
    )
    dialog.sidebar.setCurrentRow(automation_index)
    for control in (
        dialog.agent_sound_start_picker,
        dialog.agent_sound_start_preview,
    ):
        font = control.font()
        font.setPixelSize(17)
        control.setFont(font)
    dialog.show()
    dialog.resize(721, 760)
    app.processEvents()
    dialog.resize(720, 760)
    app.processEvents()

    row = dialog.findChild(SettingRow, "settingRow_agent_sound_start")
    for control in (
        dialog.agent_sound_start_check,
        dialog.agent_sound_start_picker,
        dialog.agent_sound_start_preview,
    ):
        left = control.mapTo(row, QPoint(0, 0)).x()
        assert left >= 16
        assert left + control.width() <= row.width() - 16
    assert dialog.agent_sound_start_widget.property("responsiveStacked") is True
    scroll = row.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert scroll is not None
    row_left = row.mapTo(scroll.viewport(), QPoint(0, 0)).x()
    assert row_left >= 0
    assert row_left + row.width() <= scroll.viewport().width()
    dialog.reject()
    app.processEvents()


def test_settings_domains_use_semantic_sidebar_icons():
    from pet.modern_settings_dialog import SETTINGS_DOMAIN_NAV

    assert SETTINGS_DOMAIN_NAV == (
        ("常规", "settings"),
        ("桌宠", "pet"),
        ("互动", "interaction"),
        ("菜单", "application"),
        ("桌面组件", "island"),
        ("AI 与对话", "chat"),
        ("自动化与联动", "automation"),
    )


def test_settings_menu_page_persists_legacy_compatibility_mode(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)

    dialog.menu_template_select.setCurrentData("legacy")
    dialog.save_exit_button.click()

    assert Config(tmp_path).get("context_menu_template") == "legacy"
    app.processEvents()


def test_saving_unchanged_default_menu_keeps_layout_override_empty(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)

    dialog.save_exit_button.click()

    assert Config(tmp_path).get("context_menu_layout") is None
    app.processEvents()


def test_missing_default_resource_returns_minimum_safe_menu(monkeypatch):
    import pet.menu_layout as layout_mod

    monkeypatch.setattr(
        layout_mod,
        "load_default_menu_layout",
        lambda: (_ for _ in ()).throw(OSError("missing resource")),
    )

    result = layout_mod.resolve_menu_layout(
        None,
        registered_actions={"modern_settings", "quit"},
        available_actions={"modern_settings", "quit"},
    )

    assert result.source == "fallback"
    assert result.diagnostics == ("default-layout-unavailable",)
    assert [node["id"] for node in result.nodes] == ["modern_settings", "quit"]


def test_menu_preview_uses_resolver_and_omits_empty_submenu(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    editor = dialog.menu_layout_editor
    controls = next(
        editor.tree.topLevelItem(i)
        for i in range(editor.tree.topLevelItemCount())
        if (editor.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) or {}).get("id") == "pet_controls"
    )
    for index in range(controls.childCount()):
        controls.child(index).setCheckState(0, Qt.CheckState.Unchecked)

    preview_labels = [
        editor.preview.topLevelItem(i).text(0)
        for i in range(editor.preview.topLevelItemCount())
    ]
    assert "桌宠控制" not in preview_labels
    dialog.reject()
    app.processEvents()


def test_menu_preview_refreshes_after_tree_shape_changes():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    controls = next(
        editor.tree.topLevelItem(index)
        for index in range(editor.tree.topLevelItemCount())
        if (
            editor.tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) or {}
        ).get("id")
        == "pet_controls"
    )
    action = controls.child(0)
    action_label = action.text(0)

    controls.removeChild(action)
    editor.tree.addTopLevelItem(action)
    app.processEvents()

    preview_roots = [
        editor.preview.topLevelItem(index)
        for index in range(editor.preview.topLevelItemCount())
    ]
    preview_controls = next(item for item in preview_roots if item.text(0) == "桌宠控制")
    assert action_label in [item.text(0) for item in preview_roots]
    assert action_label not in [
        preview_controls.child(index).text(0)
        for index in range(preview_controls.childCount())
    ]
    editor.close()
    app.processEvents()


def test_menu_preview_and_runtime_qmenu_share_the_same_layout_structure():
    from copy import deepcopy

    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.modern import build_modern_menu
    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import MenuLayoutEditor

    layout = deepcopy(load_default_menu_layout())
    layout["layout_id"] = "user"
    visible_root_ids = {"chat", "pet_controls", "modern_settings", "quit"}
    for node in layout["nodes"]:
        node["visible"] = node["id"] in visible_root_ids
        if node["id"] == "pet_controls":
            node["label"] = "常用桌宠操作"
            for child in node["children"]:
                child["visible"] = child["id"] == "drag_physics"

    class FakeConfig:
        def get(self, key, default=None):
            values = {
                "context_menu_layout": layout,
                "menu_easter_egg": {"enabled": False},
                "quick_launch_apps": [],
            }
            return values.get(key, default)

    class FakePet:
        cfg = FakeConfig()
        drag_physics = False
        on_open_chat = lambda self: None
        on_open_modern_settings = lambda self: None
        set_drag_physics = lambda self, _enabled: None
        request_quit = lambda self: None

    def preview_shape(parent):
        return [
            (
                parent.child(index).text(0),
                preview_shape(parent.child(index)),
            )
            for index in range(parent.childCount())
            if parent.child(index).text(0) != "────────"
        ]

    def runtime_shape(menu, nodes):
        actions = [action for action in menu.actions() if not action.isSeparator()]
        assert len(actions) == len(nodes)
        return [
            (
                action.text(),
                runtime_shape(action.menu(), node["children"])
                if node.get("type") == "submenu"
                else [],
            )
            for action, node in zip(actions, nodes)
        ]

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(layout, available_actions=MENU_ACTIONS.ids)
    runtime_menu = QMenu()
    build_modern_menu(runtime_menu, FakePet(), {})
    resolved = resolve_menu_layout(
        layout,
        registered_actions=MENU_ACTIONS.ids,
        available_actions=MENU_ACTIONS.available_ids(FakePet()),
    )

    assert preview_shape(editor.preview.invisibleRootItem()) == runtime_shape(
        runtime_menu, resolved.nodes
    )
    runtime_menu.close()
    editor.close()
    app.processEvents()


def test_menu_editor_switches_between_stacked_and_split_layouts():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.resize(560, 420)
    editor.show()
    app.processEvents()
    assert editor.findChild(type(editor.split), "menuEditorSplit").orientation() == Qt.Orientation.Vertical
    assert editor.tree.isColumnHidden(2)

    editor.resize(900, 420)
    app.processEvents()
    assert editor.split.orientation() == Qt.Orientation.Horizontal
    assert not editor.tree.isColumnHidden(2)
    editor.close()
    app.processEvents()


def test_menu_editor_uses_settings_cards_instead_of_native_table_chrome():
    from PySide6.QtWidgets import QApplication, QHeaderView

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.resize(900, 520)
    editor.show()
    app.processEvents()

    assert editor.tree.objectName() == "menuLayoutTree"
    assert editor.tree.uniformRowHeights()
    assert editor.tree.indentation() == 18
    assert all(
        editor.tree.header().sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
        for column in range(3)
    )
    assert not editor.tree.header().stretchLastSection()
    assert editor.tree.columnWidth(2) == 92
    assert editor.preview.header().isHidden()
    assert editor.preview.uniformRowHeights()
    assert editor.preview.indentation() == 18
    assert editor.findChild(
        type(editor.preview_label), "menuLayoutPreviewLabel"
    ).text() == "实时菜单预览"

    editor.close()
    app.processEvents()


def test_wide_menu_editor_expands_and_groups_commands_into_dropdowns(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    menu_index = next(
        index for index in range(dialog.sidebar.count())
        if dialog.sidebar.item(index).text() == "菜单"
    )
    dialog.sidebar.setCurrentRow(menu_index)
    dialog.resize(1600, 1000)
    dialog.show()
    app.processEvents()

    editor = dialog.menu_layout_editor
    assert editor.width() >= 1100
    assert editor.tree.height() >= 320
    assert editor.editor_label.text() == "菜单结构"
    assert editor.preview_label.text() == "实时菜单预览"
    grouped = (
        (editor.order_button, "排序"),
        (editor.move_button, "移动到"),
        (editor.submenu_button, "插入"),
        (editor.customize_button, "自定义"),
        (editor.more_button, "更多"),
    )
    assert all(button.text() == label for button, label in grouped)
    assert all(button.popupMenu() is not None for button, _label in grouped)

    dialog.resize(720, 700)
    app.processEvents()
    assert editor.split.orientation() == settings_mod.Qt.Orientation.Vertical
    dialog.reject()
    app.processEvents()


def test_menu_editor_compact_action_bar_keeps_every_button_reachable():
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.resize(420, 620)
    editor.show()
    app.processEvents()
    assert editor.width() <= 420

    buttons = (
        editor.order_button,
        editor.move_button,
        editor.submenu_button,
        editor.customize_button,
        editor.more_button,
    )
    assert all(button.isVisible() for button in buttons)
    assert all(
        button.mapTo(editor, QPoint(0, 0)).x() + button.width() <= editor.width()
        for button in buttons
    )
    assert len({button.mapTo(editor, QPoint(0, 0)).y() for button in buttons}) == 3
    editor.close()
    app.processEvents()


def test_non_windows_settings_does_not_create_orphan_windows_control(tmp_path, monkeypatch):
    import sys

    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    if sys.platform == "win32":
        return
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)

    assert dialog.cursor_hidden_passthrough_check is None
    dialog.reject()
    app.processEvents()


def test_compact_settings_menu_action_bar_fits_scroll_viewport(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QScrollArea

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.sidebar.setCurrentRow(3)
    dialog.resize(1100, 700)
    dialog.show()
    app.processEvents()
    dialog.resize(720, 700)
    app.processEvents()
    editor = dialog.menu_layout_editor
    scroll = editor.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert scroll is not None

    for button in (
        editor.order_button,
        editor.move_button,
        editor.submenu_button,
        editor.customize_button,
        editor.more_button,
    ):
        left = button.mapTo(scroll.viewport(), QPoint(0, 0)).x()
        assert left >= 0
        assert left + button.width() <= scroll.viewport().width()
        dialog_left = button.mapTo(dialog, QPoint(0, 0)).x()
        assert dialog_left + button.width() <= dialog.width()
    dialog.reject()
    app.processEvents()


def test_menu_editor_retains_but_marks_platform_unavailable_action(tmp_path, monkeypatch):
    import sys

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    if sys.platform == "win32":
        return
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    item = dialog.menu_layout_editor.item_for_action("proactive_screen")

    assert item is not None
    assert item.text(1) == "此平台不可用"
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "主动识屏" not in [
        dialog.menu_layout_editor.preview.topLevelItem(i).text(0)
        for i in range(dialog.menu_layout_editor.preview.topLevelItemCount())
    ]
    dialog.reject()
    app.processEvents()


def test_menu_editor_keeps_recovery_actions_visible_but_movable(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    for action_id in ("modern_settings", "quit"):
        item = dialog.menu_layout_editor.item_for_action(action_id)
        assert item.checkState(0) == Qt.CheckState.Checked
        assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert item.flags() & Qt.ItemFlag.ItemIsDragEnabled
    dialog.reject()
    app.processEvents()


def test_menu_editor_actions_cannot_accept_dropped_children():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)

    action = editor.item_for_action("chat")
    assert action.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert not action.flags() & Qt.ItemFlag.ItemIsDropEnabled

    editor.close()
    app.processEvents()


def test_menu_editor_refuses_to_nest_one_submenu_inside_another():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    root = editor.tree.invisibleRootItem()
    source = next(
        root.child(index)
        for index in range(root.childCount())
        if (root.child(index).data(0, Qt.ItemDataRole.UserRole) or {}).get("id") == "pet_controls"
    )
    editor.tree.setCurrentItem(source)
    editor.move_menu.aboutToShow.emit()
    next(action for action in editor.move_menu.actions() if action.data() == "tools_help").trigger()

    assert source.parent() is None
    editor.close()
    app.processEvents()


def test_removed_action_is_ignored_with_migration_diagnostic():
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {"type": "action", "id": "retired_action", "visible": True},
                {"type": "action", "id": "chat", "visible": True},
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        registered_actions={"chat", "modern_settings", "quit"},
        available_actions={"chat", "modern_settings", "quit"},
    )

    assert result.source == "normalized"
    assert result.diagnostics == ("unknown-action:retired_action",)
    assert [node["id"] for node in result.nodes] == ["chat", "modern_settings", "quit"]


def test_user_layout_gains_future_default_action_without_losing_custom_order(
    monkeypatch,
):
    import pet.menu_layout as layout_mod

    monkeypatch.setattr(
        layout_mod,
        "load_default_menu_layout",
        lambda: {
            "schema_version": 1,
            "layout_id": "modern-default-v1",
            "nodes": [
                {
                    "type": "submenu",
                    "id": "pet_controls",
                    "label": "桌宠控制",
                    "visible": True,
                    "children": [
                        {"type": "action", "id": "drag_physics", "visible": True},
                        {"type": "action", "id": "future_action", "visible": True},
                        {"type": "action", "id": "no_move", "visible": True},
                    ],
                },
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
    )
    result = resolve_menu_layout(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {
                    "type": "submenu",
                    "id": "pet_controls",
                    "label": "我的桌宠操作",
                    "visible": True,
                    "children": [
                        {"type": "action", "id": "no_move", "visible": True},
                        {"type": "action", "id": "drag_physics", "visible": False},
                    ],
                },
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        registered_actions={
            "drag_physics",
            "future_action",
            "no_move",
            "modern_settings",
            "quit",
        },
        available_actions={
            "drag_physics",
            "future_action",
            "no_move",
            "modern_settings",
            "quit",
        },
    )

    assert result.source == "normalized"
    assert result.diagnostics == ("default-action-added:future_action",)
    controls = result.nodes[0]
    assert controls["label"] == "我的桌宠操作"
    assert [child["id"] for child in controls["children"]] == [
        "no_move",
        "future_action",
    ]


def test_menu_editor_exposes_future_default_action_for_customization(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.menu_layout as layout_mod
    from pet.modern_settings_dialog import MenuLayoutEditor

    monkeypatch.setattr(
        layout_mod,
        "load_default_menu_layout",
        lambda: {
            "schema_version": 1,
            "layout_id": "modern-default-v1",
            "nodes": [
                {"type": "action", "id": "chat", "visible": True},
                {"type": "action", "id": "look_screen", "visible": True},
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
    )
    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(
        {
            "schema_version": 1,
            "layout_id": "user",
            "nodes": [
                {"type": "action", "id": "chat", "visible": True},
                {"type": "action", "id": "modern_settings", "visible": True},
                {"type": "action", "id": "quit", "visible": True},
            ],
        },
        available_actions={"chat", "look_screen", "modern_settings", "quit"},
    )

    assert editor.item_for_action("look_screen") is not None
    assert [node["id"] for node in editor.value()["nodes"]] == [
        "chat",
        "look_screen",
        "modern_settings",
        "quit",
    ]
    editor.close()
    app.processEvents()


def test_settings_rejects_invalid_nested_menu_draft_before_writing(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox, QTreeWidgetItem

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    outer = next(
        dialog.menu_layout_editor.tree.topLevelItem(i)
        for i in range(dialog.menu_layout_editor.tree.topLevelItemCount())
        if (dialog.menu_layout_editor.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) or {}).get("id") == "pet_controls"
    )
    nested = QTreeWidgetItem(["非法内层", ""])
    nested.setData(0, Qt.ItemDataRole.UserRole, {"type": "submenu", "id": "user.invalid", "section": None})
    nested.setCheckState(0, Qt.CheckState.Checked)
    outer.addChild(nested)

    dialog._save()

    assert dialog.result() == 0
    assert Config(tmp_path).get("context_menu_layout") is None
    assert warnings and "菜单布局" in warnings[0]
    dialog.reject()
    app.processEvents()
# --- Settings menu customization regressions (2026-09-03) ---


def test_settings_action_popups_share_the_modern_select_visual_tokens():
    from PySide6.QtWidgets import QApplication

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import MenuLayoutEditor, QuickLaunchEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None, available_actions=MENU_ACTIONS.ids)
    launcher = QuickLaunchEditor([])
    menus = [
        editor.order_menu, editor.move_menu, editor.submenu_menu,
        editor.customize_menu, editor.more_menu, launcher.add_menu,
    ]
    assert all(menu.objectName() == "SettingsPopup" for menu in menus)
    assert all("border-radius: 10px" in menu.styleSheet() for menu in menus)
    assert all("padding: 6px" in menu.styleSheet() for menu in menus)
    assert all("min-height: 22px" in menu.styleSheet() for menu in menus)
    editor.close()
    launcher.close()
    app.processEvents()


def test_action_popups_use_the_exact_same_popup_surface_as_menu_mode():
    from PySide6.QtWidgets import QApplication

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import MenuLayoutEditor, ModernSelect, QuickLaunchEditor

    app = QApplication.instance() or QApplication([])
    select = ModernSelect(width=156)
    select.addItem("新版菜单", "modern")
    select.addItem("旧版兼容菜单", "legacy")
    select.show()
    select.showPopup()
    app.processEvents()
    editor = MenuLayoutEditor(None, available_actions=MENU_ACTIONS.ids)
    launcher = QuickLaunchEditor([])

    reference = select._popup
    assert reference is not None
    for popup in (editor.order_menu, editor.customize_menu, launcher.add_menu):
        assert popup.objectName() == reference.objectName()
        assert popup.styleSheet() == reference.styleSheet()
        assert popup.property("settingsPopup") is True

    reference.close()
    select.close()
    editor.close()
    launcher.close()
    app.processEvents()


def test_menu_editor_header_columns_are_user_resizable():
    from PySide6.QtWidgets import QApplication, QHeaderView

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.resize(1000, 560)
    editor.show()
    app.processEvents()

    header = editor.tree.header()
    assert all(
        header.sectionResizeMode(index) == QHeaderView.ResizeMode.Interactive
        for index in range(3)
    )
    assert not header.sectionsMovable()
    original = editor.tree.columnWidth(0)
    header.resizeSection(0, original + 48)
    app.processEvents()
    assert editor.tree.columnWidth(0) == original + 48
    assert "拖动表头" in editor.editor_hint.text()
    editor.close()
    app.processEvents()


def test_settings_tabs_are_compact_left_aligned_plugin_style_navigation():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

    from pet.modern_settings_dialog import SettingsTabContainer

    app = QApplication.instance() or QApplication([])
    tabs = SettingsTabContainer()
    for key, label in (("layout", "菜单编排"), ("launcher", "快捷启动"), ("appearance", "外观")):
        tabs.addTab(key, label, QWidget())
    tabs.resize(900, 420)
    tabs.show()
    app.processEvents()

    assert tabs.tab_bar.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Maximum
    assert tabs.tab_layout.alignment() & Qt.AlignmentFlag.AlignLeft
    assert tabs.tab_bar.width() < tabs.width() * 0.7
    assert all(button.property("navigationStyle") == "plugin" for button in tabs._buttons)
    tabs.close()
    app.processEvents()


def test_action_menu_button_matches_selector_popup_geometry_and_anchor():
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QWidgetAction

    from pet.modern_settings_dialog import MenuLayoutEditor, ModernSelect, SettingsMenuButton

    app = QApplication.instance() or QApplication([])
    select = ModernSelect(width=240)
    select.addItem("新版菜单", "modern")
    select.addItem("旧版兼容菜单", "legacy")
    select.move(40, 40)
    select.show()
    select.showPopup()
    app.processEvents()
    selector_popup = select._popup
    assert selector_popup is not None

    editor = MenuLayoutEditor(None)
    editor.resize(1000, 560)
    editor.show()
    app.processEvents()
    button = editor.order_button
    assert isinstance(button, SettingsMenuButton)
    assert button.menu() is None  # no native indicator/anchoring path
    button.showPopup()
    app.processEvents()
    popup = button.popupMenu()

    assert popup.width() >= button.width()
    assert popup.pos() == button.mapToGlobal(QPoint(0, button.height() + 4))
    assert popup.property("settingsPopup") == selector_popup.property("settingsPopup")
    assert not any(isinstance(action, QWidgetAction) for action in selector_popup.actions())
    assert popup.actionGeometry(popup.actions()[0]).height() == selector_popup.actionGeometry(
        selector_popup.actions()[0]
    ).height()
    popup.close()
    selector_popup.close()
    editor.close()
    select.close()
    app.processEvents()


def test_menu_editor_uses_three_responsive_toolbar_and_panel_modes():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import MenuLayoutEditor

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None)
    editor.show()

    editor.resize(1000, 600)
    app.processEvents()
    assert editor.property("layoutMode") == "wide"
    assert editor.split.orientation() == Qt.Orientation.Horizontal
    assert max(button.width() for button in editor.toolbar_buttons) <= 132
    assert editor.toolbar_layout.itemAtPosition(0, 4) is not None

    editor.resize(680, 700)
    app.processEvents()
    assert editor.property("layoutMode") == "medium"
    assert editor.split.orientation() == Qt.Orientation.Vertical
    assert editor.toolbar_layout.itemAtPosition(1, 1) is not None
    assert editor.toolbar_layout.itemAtPosition(1, 2) is None

    editor.resize(430, 760)
    app.processEvents()
    assert editor.property("layoutMode") == "compact"
    assert editor.tree.isColumnHidden(2)
    assert editor.toolbar_layout.itemAtPosition(2, 0) is not None
    assert editor.editor_hint.isHidden()
    editor.close()
    app.processEvents()


def test_alias_keeps_original_name_in_editor_but_runtime_uses_alias_only():
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.modern_settings_dialog import MenuLayoutEditor

    class Pet:
        on_open_chat = lambda self: None

    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None, available_actions=MENU_ACTIONS.ids)
    editor.resize(900, 520)
    editor.show()
    app.processEvents()
    editor.set_item_alias("chat", "和鲸鱼聊聊")
    item = editor.item_for_action("chat")
    assert item.text(0) == "和鲸鱼聊聊（AI 对话）"
    assert editor.tree.header().sectionSize(0) > editor.tree.header().sectionSize(2)
    preview = [
        editor.preview.topLevelItem(index).text(0)
        for index in range(editor.preview.topLevelItemCount())
    ]
    assert "和鲸鱼聊聊" in preview
    assert item.text(0) not in preview
    menu = QMenu()
    MENU_ACTIONS.populate(menu, Pet(), ({
        "type": "action", "id": "chat", "alias": "和鲸鱼聊聊",
    },), enabled_actions={"chat"})
    assert [action.text() for action in menu.actions()] == ["和鲸鱼聊聊"]
    editor.close()
    menu.close()
    app.processEvents()


def test_custom_file_icon_is_validated_persisted_and_supports_fit_modes(tmp_path):
    from PIL import Image
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.icons import custom_file_menu_icon
    from pet.context_menus.registry import MENU_ACTIONS
    from pet.menu_layout import resolve_menu_layout
    from pet.modern_settings_dialog import MenuLayoutEditor, custom_icon_file_error

    icon_path = tmp_path / "wide icon.png"
    Image.new("RGBA", (80, 32), (20, 120, 220, 255)).save(icon_path)
    invalid_path = tmp_path / "not-image.txt"
    invalid_path.write_text("not an image", encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    editor = MenuLayoutEditor(None, available_actions=MENU_ACTIONS.ids)
    assert custom_icon_file_error(icon_path) == ""
    assert custom_icon_file_error(invalid_path)
    assert editor.set_item_file_icon("chat", icon_path, "contain") is True
    chat = next(node for node in editor.value()["nodes"] if node.get("id") == "chat")
    assert chat["icon"] == {
        "kind": "file", "path": str(icon_path.resolve()), "display": "contain",
    }
    assert not editor.item_for_action("chat").icon(0).isNull()
    resolved = resolve_menu_layout(
        editor.value(),
        registered_actions=MENU_ACTIONS.ids,
        available_actions=MENU_ACTIONS.ids,
    )
    resolved_chat = next(node for node in resolved.nodes if node.get("id") == "chat")
    assert resolved_chat["icon"] == chat["icon"]
    menu = QMenu()
    MENU_ACTIONS.populate(menu, type("Pet", (), {"on_open_chat": lambda self: None})(),
                          (resolved_chat,), enabled_actions={"chat"})
    assert not menu.actions()[0].icon().isNull()
    contain = custom_file_menu_icon(menu, chat["icon"], 18).pixmap(18, 18).toImage()
    assert contain.pixelColor(9, 0).alpha() == 0
    assert editor.set_item_file_icon("chat", icon_path, "cover") is True
    chat = next(node for node in editor.value()["nodes"] if node.get("id") == "chat")
    assert chat["icon"]["display"] == "cover"
    cover = custom_file_menu_icon(menu, chat["icon"], 18).pixmap(18, 18).toImage()
    assert cover.pixelColor(9, 0).alpha() > 0
    assert {action.text() for action in editor.icon_display_menu.actions()} == {
        "完整显示", "裁切填满",
    }
    editor.close()
    menu.close()
    app.processEvents()
