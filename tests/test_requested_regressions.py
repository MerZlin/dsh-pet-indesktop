import os

import pytest
from datetime import datetime, timezone


def test_modern_message_card_opacity_is_configurable_and_persisted(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.chat.themes import build_modern_custom_overlay_qss
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    assert config.get("modern_chat_card_opacity") == 84

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_modern_chat_card_opacity"
    )
    assert row is not None and not row.isHidden()
    dialog.ai_page.chat_ui_style.setCurrentData("classic")
    assert row.isHidden()
    dialog.ai_page.chat_ui_style.setCurrentData("modern")
    assert not row.isHidden()
    dialog.ai_page.message_card_opacity.setValue(65)
    dialog._save()

    assert config.get("modern_chat_card_opacity") == 65
    assert "rgba(255, 255, 255, 166)" in build_modern_custom_overlay_qss(
        "#3994ff", 65
    )
    dialog.close()
    app.processEvents()


def test_modern_chat_header_uses_current_pet_image(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    avatar = QPixmap(24, 24)
    avatar.fill(Qt.GlobalColor.red)

    class Pet:
        def icon_pixmap(self, size):
            assert size == 34
            return avatar

    window = ChatWindow(Config(tmp_path), "shenshen", pet_window=Pet())
    assert window.avatar_label.text() == ""
    assert not window.avatar_label.pixmap().isNull()
    window.close()
    app.processEvents()


def test_click_sound_path_is_linked_to_enable_toggle_and_persisted(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    assert config.get("click_sound_path") == ""

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_click_sound_path"
    )
    assert row is not None
    # 默认开启 → 音效文件行可见
    assert dialog.click_sound_check.isChecked()
    assert not row.isHidden()
    dialog.click_sound_check.setChecked(False)
    assert row.isHidden()
    dialog.click_sound_check.setChecked(True)
    assert not row.isHidden()
    dialog.click_sound_picker.setText("/tmp/my-click.wav")
    dialog._save()

    assert config.get("click_sound_path") == "/tmp/my-click.wav"
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_hidden_initially_when_toggle_disabled(tmp_path, monkeypatch):
    """点击音效未启用时，音效文件行初始就应隐藏（此前初始同步在 UI 构建前，
    findChild 找不到行导致初始状态错误显示）。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("click_sound_enabled", False)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_click_sound_path"
    )
    assert row is not None
    assert not dialog.click_sound_check.isChecked()
    assert row.isHidden()
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_sits_directly_below_toggle(tmp_path, monkeypatch):
    """音效文件行必须紧贴点击音效行下方（此前 click_balance 插入 index 1 把
    音效文件行挤到第三位）。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication, QLabel

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    section = next(
        s for s in dialog.findChildren(settings_mod.SettingsSection)
        if s.findChild(QLabel, "sectionTitle") is not None
        and s.findChild(QLabel, "sectionTitle").text() == "点击反馈"
    )
    card = section.findChild(settings_mod.SettingsCard)
    assert card is not None
    names = [row.objectName() for row in card.rows]
    assert names[0] == "settingRow_click_sound"
    assert names[1] == "settingRow_click_sound_path"
    dialog.close()
    app.processEvents()


def test_settings_dialog_position_avoids_pet_window(tmp_path, monkeypatch):
    """设置窗口激活时不应遮挡桌宠：打开时移动到不与桌宠相交的位置。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication, QWidget

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    pet = QWidget()
    pet.setGeometry(QRect(100, 100, 200, 200))
    pet.show()
    app.processEvents()
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, pet, include_ai=True)
    # 真实最小尺寸 720x500 在 offscreen 800x600 屏幕上无处可避，放宽以测试避让逻辑
    dialog.setMinimumSize(360, 260)
    dialog.resize(420, 320)
    dialog.show()
    app.processEvents()
    try:
        assert not dialog.geometry().intersects(pet.geometry())
    finally:
        dialog.close()
        pet.close()
        app.processEvents()


def test_menu_font_select_lists_system_fonts(tmp_path, monkeypatch):
    """UI 字体设置项应枚举系统可用字体，而不是硬编码少数几个。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    select = dialog.menu_font_select
    # 字体枚举延迟到事件循环空闲时填充（避免阻塞窗口打开）。
    # 测试直接同步触发填充，不等待 QTimer：QTest.qWait 的嵌套事件循环
    # 会触发 GC 析构残留测试对象，在 macOS/Windows CI 上均可致进程
    # abort（setParent_helper / QPA 平台层），同步调用则完全绕开。
    dialog._populate_menu_fonts()
    available = {select.itemData(i) for i in range(select.count())}
    system_families = set(QFontDatabase.families())
    assert "system" in available
    custom = available - {"system"}
    # 所有可选字体必须来自系统字体表
    assert custom <= system_families
    # 必须真正列出系统字体（而非只剩硬编码几项）；无系统字体的平台
    # （如 Windows offscreen CI）跳过数量断言
    if system_families:
        assert len(custom) >= 4
    dialog.close()
    app.processEvents()


def test_settings_first_paint_does_not_enumerate_system_fonts(tmp_path, monkeypatch):
    """系统字体枚举可能在 Windows 阻塞数秒，只能在用户展开字体选择器时执行。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(
        settings_mod,
        "_system_font_families",
        lambda: calls.append("enumerated") or ("Regression Test Font",),
    )

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    dialog.show()
    app.processEvents()
    assert calls == [], "首次显示设置窗口时不应枚举全部系统字体"

    dialog.menu_font_select.showPopup()
    assert calls == ["enumerated"]
    dialog.menu_font_select._popup.close()
    dialog.close()
    app.processEvents()


def test_settings_save_preserves_custom_font_before_selector_is_opened(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    appearance = dict(config.get("context_menu_appearance"))
    appearance["ui_font"] = "Regression Custom Font"
    config.set("context_menu_appearance", appearance)

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    assert dialog._menu_fonts_populated is False
    assert dialog.menu_font_select.currentData() == "Regression Custom Font"
    dialog._save()
    assert config.get("context_menu_appearance")["ui_font"] == "Regression Custom Font"
    app.processEvents()


def test_modern_select_reuses_one_popup_without_accumulating_children(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    select = dialog.menu_theme_select

    popup_ids = []
    for _ in range(3):
        select.showPopup()
        app.processEvents()
        popup_ids.append(id(select._popup))
        select._popup.close()
        app.processEvents()
        assert len(select.findChildren(QMenu)) == 1

    assert len(set(popup_ids)) == 1

    dialog.close()
    app.processEvents()


def test_dock_icon_row_platform_gated(tmp_path, monkeypatch):
    """「显示 Dock 图标」是 macOS 专属选项，其他平台不应显示。"""
    import sys

    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(sys, "platform", "win32")
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    assert dialog.findChild(settings_mod.SettingRow, "settingRow_dock_icon") is None
    dialog.close()
    monkeypatch.setattr(sys, "platform", "darwin")
    dialog2 = settings_mod.ModernSettingsDialog(config, include_ai=True)
    assert dialog2.findChild(settings_mod.SettingRow, "settingRow_dock_icon") is not None
    dialog2.close()
    app.processEvents()


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows 无 time.tzset()，无法在测试进程内切换时区（CI runner 为 UTC）",
)
def test_new_session_title_converts_utc_creation_time_to_local(monkeypatch):
    import os
    import time

    from pet.chat.models import ChatSession
    from pet.chat.widgets import _short_title

    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        created = datetime(2026, 8, 27, 1, 5, tzinfo=timezone.utc).isoformat()
        session = ChatSession("id", "shenshen", "provider", "", created_at=created)
        assert _short_title(session) == "新会话 · 09:05"
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        if hasattr(time, "tzset"):
            time.tzset()


def test_chat_window_left_edge_drag_resizes(tmp_path):
    """无边框聊天窗口应支持按住边缘拖拽缩放（此前无任何边缘 resize 处理）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)
    before_w = win.width()

    def mouse(kind, x, y, button):
        # 旧 5 参构造不写 globalPosition（遗留默认值），必须显式传全局坐标
        return QMouseEvent(
            kind, QPointF(x, y), QPointF(x, y), button, button,
            Qt.KeyboardModifier.NoModifier,
        )

    before_x = win.x()
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 2, 350, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 140, 350, Qt.MouseButton.LeftButton
    ))
    # 左边缘跟随鼠标右移：窗口变窄（右边缘锚定），x 坐标右移
    assert win.x() > before_x, "按住左边缘右拖时左边缘应跟随鼠标右移"
    assert win.width() < before_w, "按住左边缘右拖应使窗口变窄（右边缘锚定）"
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 140, 350, Qt.MouseButton.LeftButton
    ))
    win.close()
    app.processEvents()


def test_chat_window_edge_resize_clamps_position_with_size(tmp_path):
    """边缘拖拽触达最小尺寸时，位置应随尺寸回退（锚定对侧边缘），
    窗口不能被推出屏幕（此前 setGeometry 仅 clamp 尺寸不回退位置）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)
    min_w, min_h = win.minimumWidth(), win.minimumHeight()
    start_right = win.x() + win.width()
    start_bottom = win.y() + win.height()

    def mouse(kind, x, y, button):
        return QMouseEvent(
            kind, QPointF(x, y), QPointF(x, y), button, button,
            Qt.KeyboardModifier.NoModifier,
        )

    # 左边缘右拖 1000px（远超最小宽度）→ 右边缘锚定，x = 原右边缘 - 最小宽度
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 2, 350, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 1002, 350, Qt.MouseButton.LeftButton
    ))
    assert win.width() == min_w
    # offscreen 平台对窗口几何有 1px 微调，右边缘不得超出原位置（此前会偏出 600px）
    assert abs((win.x() + win.width()) - start_right) <= 1, (
        f"拖到最小宽度时右边缘应锚定在 {start_right}，实际 {win.x() + win.width()}"
    )
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 1002, 350, Qt.MouseButton.LeftButton
    ))

    # 顶部上拖 2000px → 高度触达最大尺寸上限，底边缘锚定（此前窗口整体移出屏幕顶部）
    max_h = win.maximumHeight()
    win.resize(960, 700)
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 480, 2, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 480, -1998, Qt.MouseButton.LeftButton
    ))
    assert win.height() == max_h
    assert abs((win.y() + win.height()) - start_bottom) <= 1, (
        f"拖到最大高度时底边缘应锚定在 {start_bottom}，实际 {win.y() + win.height()}"
    )
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 480, -1998, Qt.MouseButton.LeftButton
    ))
    win.close()
    app.processEvents()


def test_chat_window_edge_hover_shows_resize_cursor(tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)
    win.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(win.width() - 2, 350),
        QPointF(win.width() - 2, 350),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert win.cursor().shape() == Qt.CursorShape.SizeHorCursor, (
        "悬停在窗口右边缘时应显示水平缩放光标"
    )
    win.close()
    app.processEvents()


def test_ojingjing_entry_hover_survives_widget_children(monkeypatch):
    """彩蛋项 hover 不应依赖 enter 事件：菜单弹出时鼠标已在项上（无 enter）
    应合成高亮；鼠标移入子 widget 触发的 leave 不应丢高亮。"""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry
    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    # offscreen QPA 的 QCursor.setPos 不生效（光标位置由平台管理），
    # 固定模拟光标悬在菜单项内部，验证不依赖 enter 事件的合成高亮。
    monkeypatch.setattr(fun_entry.QCursor, "pos", staticmethod(lambda: QPoint(20, 20)))
    menu = QMenu()
    entry = OjingjingMenuEntry(menu, {"title": "厉害了我的鲸", "hint": "请点击"})
    menu.show()
    app.processEvents()
    try:
        # 1. 菜单弹出时鼠标已悬在项上：showEvent 按光标位置合成高亮
        assert entry._hovered, "鼠标已位于项上时应合成初始高亮（无需 enter 事件）"
        # 2. 鼠标移入子 widget 触发的 leave：光标仍在项内，高亮保持
        entry.leaveEvent(QEvent(QEvent.Type.Leave))
        assert entry._hovered, "光标仍在项内时 leave 不应清除高亮"
        # 3. 跨窗口移动丢失 enter 时，mouseMove 兜底恢复高亮
        entry._hovered = False
        entry.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(20, 20), QPointF(20, 20),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ))
        assert entry._hovered, "鼠标移动应恢复高亮"
    finally:
        entry.close()
        menu.close()
        app.processEvents()


def test_windows_ojingjing_children_do_not_intercept_hover():
    """Windows 按子窗口做命中测试；首项内容必须把鼠标事件透传给背景控件。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu)
    children = [
        entry.findChild(QWidget, "ojingjingAvatar"),
        entry.findChild(QWidget, "ojingjingTitle"),
        entry.findChild(QWidget, "ojingjingClickAccessory"),
    ]
    assert all(child is not None for child in children)
    assert all(
        child.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for child in children
    ), "头像、标题和提示不应截获 Windows hover 事件"
    entry.close()
    menu.close()
    app.processEvents()


def test_macos_hide_pet_enables_dock_icon(tmp_path, monkeypatch):
    """macOS 隐藏桌宠时应同步打开 Dock 图标，避免桌宠无法找回。"""
    import sys

    from unittest import mock

    from PySide6.QtWidgets import QWidget

    import pet.app
    from pet.config import Config
    from pet.window import PetWindow

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pet.app, "_mac_set_dock_icon_visible", mock.Mock())
    config = Config(tmp_path)
    config.set("show_dock_icon", False)

    class FakePet:
        cfg = config

    fake = FakePet()
    # hide() 的同步逻辑：Dock 图标关闭时打开并写回配置
    PetWindow._ensure_dock_icon_on_hide(fake)
    assert config.get("show_dock_icon") is True, "隐藏时若 Dock 图标关闭应自动打开"
    pet.app._mac_set_dock_icon_visible.assert_called_once_with(True)

    # hide() 组合：先同步再真正隐藏（QWidget.hide 被 mock 拦截）
    with mock.patch.object(QWidget, "hide") as mock_hide:
        win = PetWindow.__new__(PetWindow)
        win.cfg = Config(tmp_path)
        win.cfg.set("show_dock_icon", False)
        PetWindow.hide(win)
        mock_hide.assert_called_once()
        assert win.cfg.get("show_dock_icon") is True


def test_windows_settings_has_no_orphan_macos_dock_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "win32")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("show_dock_icon", False)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)

    assert dialog.dock_icon_check is None
    assert not any(
        isinstance(child, settings_mod.ToggleSwitch)
        for child in dialog.children()
    ), "所有开关都必须被设置行接管，不能游离在窗口左上角"
    assert dialog.findChild(
        settings_mod.SettingRow, "settingRow_auto_hide_fullscreen"
    ) is not None
    assert dialog.findChild(
        settings_mod.SettingRow, "settingRow_stream_capture"
    ) is not None

    dialog._save()
    assert config.get("show_dock_icon") is False
    app.processEvents()


def test_hide_pet_notifies_and_dock_click_restores(tmp_path, monkeypatch):
    """用户主动隐藏：弹托盘提示 + macOS 点击 Dock 图标恢复桌宠。"""
    import sys

    from unittest import mock

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    import pet.window as window_mod
    from pet.config import Config

    monkeypatch.setattr(sys, "platform", "darwin")
    app = QApplication.instance() or QApplication([])
    win = window_mod.PetWindow.__new__(window_mod.PetWindow)
    win.cfg = Config(tmp_path)
    notified = []
    win.on_hidden = lambda: notified.append(True)

    with mock.patch.object(QWidget, "hide"), mock.patch.object(
        window_mod.PetWindow, "show"
    ) as mock_show, mock.patch.object(app, "applicationStateChanged") as m_sig:
        window_mod.PetWindow.hide(win)
        assert notified, "用户主动隐藏应触发托盘提示回调"
        # 隐藏后 arm Dock 点击恢复监听
        m_sig.connect.assert_called_once()
        # 点击 Dock 图标 → 应用激活 → 自动恢复桌宠
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_called_once()
        # 一次性监听：再次激活不再恢复
        mock_show.reset_mock()
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_not_called()


def test_hide_pet_internal_replacement_skips_notify(tmp_path, monkeypatch):
    """角色切换等内部替换隐藏：不弹提示、不 arm Dock 恢复监听。"""
    import sys

    from unittest import mock

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    import pet.window as window_mod
    from pet.config import Config

    monkeypatch.setattr(sys, "platform", "darwin")
    app = QApplication.instance() or QApplication([])
    win = window_mod.PetWindow.__new__(window_mod.PetWindow)
    win.cfg = Config(tmp_path)
    win.on_hidden = lambda: pytest.fail("内部替换不应弹提示")

    with mock.patch.object(QWidget, "hide"), mock.patch.object(
        window_mod.PetWindow, "show"
    ) as mock_show, mock.patch.object(app, "applicationStateChanged") as m_sig:
        window_mod.PetWindow.hide(win, notify=False)
        m_sig.connect.assert_not_called()
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_not_called()
