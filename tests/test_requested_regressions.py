import os
import types

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
        settings_mod.SettingRow, "settingRow_click_sound_pack"
    )
    assert row is not None
    # 默认开启 → 音效包行可见
    assert dialog.click_sound_check.isChecked()
    assert not row.isHidden()
    dialog.click_sound_check.setChecked(False)
    assert row.isHidden()
    dialog.click_sound_check.setChecked(True)
    assert not row.isHidden()
    dialog.click_sound_picker.set_pack({"kind": "file", "id": "custom", "path": "/tmp/my-click.wav"})
    dialog._save()

    assert config.get("click_sound_pack") == {"kind": "file", "id": "custom", "path": "/tmp/my-click.wav"}
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_hidden_initially_when_toggle_disabled(tmp_path, monkeypatch):
    """点击音效未启用时，音效包行初始就应隐藏（此前初始同步在 UI 构建前，
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
        settings_mod.SettingRow, "settingRow_click_sound_pack"
    )
    assert row is not None
    assert not dialog.click_sound_check.isChecked()
    assert row.isHidden()
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_sits_directly_below_toggle(tmp_path, monkeypatch):
    """音效包行必须紧贴点击音效行下方（此前 click_balance 插入 index 1 把
    音效包行挤到第三位）。"""
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
    assert names[1] == "settingRow_click_sound_pack"
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
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)

    def hover(x: float, y: float) -> None:
        win.event(QHoverEvent(QEvent.Type.HoverMove, QPointF(x, y), QPointF(x, y)))

    # 悬停在窗口右边缘时应显示水平缩放光标
    hover(win.width() - 2, 350)
    assert win.cursor().shape() == Qt.CursorShape.SizeHorCursor, (
        "悬停在窗口右边缘时应显示水平缩放光标"
    )
    # 移入窗口内部应立即恢复箭头（回归：从窗口外进入后光标卡在缩放双箭头）
    hover(win.width() // 2, 350)
    assert win.cursor().shape() == Qt.CursorShape.ArrowCursor, (
        "离开边缘进入窗口内部应恢复箭头光标"
    )
    # 离开窗口恢复箭头
    win.event(QHoverEvent(QEvent.Type.HoverLeave, QPointF(1, 1), QPointF(1, 1)))
    assert win.cursor().shape() == Qt.CursorShape.ArrowCursor
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
    """macOS 隐藏桌宠时应临时打开 Dock 图标（运行期策略，不写回配置）。

    回归背景：旧实现把 show_dock_icon 直接改成 True（内存态污染，且会经
    其他路径的 cfg.save() 落盘覆盖用户偏好）；现在改为 _dock_icon_forced
    运行期标志，恢复显示时按偏好还原。
    """
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
    PetWindow._ensure_dock_icon_on_hide(fake)
    assert config.get("show_dock_icon") is False, "隐藏不得覆盖用户偏好配置"
    assert getattr(fake, "_dock_icon_forced", False) is True
    pet.app._mac_set_dock_icon_visible.assert_called_once_with(True)

    # 恢复显示：按偏好还原 Dock 策略（pref=False → Accessory）
    pet.app._mac_set_dock_icon_visible.reset_mock()
    PetWindow._restore_dock_icon_preference(fake)
    assert getattr(fake, "_dock_icon_forced", True) is False
    pet.app._mac_set_dock_icon_visible.assert_called_once_with(False)

    # hide() 组合：先临时打开再真正隐藏（QWidget.hide 被 mock 拦截）
    with mock.patch.object(QWidget, "hide") as mock_hide:
        win = PetWindow.__new__(PetWindow)
        win.cfg = Config(tmp_path)
        win.cfg.set("show_dock_icon", False)
        PetWindow.hide(win)
        mock_hide.assert_called_once()
        assert win.cfg.get("show_dock_icon") is False
        assert getattr(win, "_dock_icon_forced", False) is True


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


# Linux 回归：Windows 专属开关不得在非 Windows 平台创建后游离到设置窗左上角。
def test_linux_settings_has_no_orphan_windows_cursor_passthrough_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "linux")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)

    assert dialog.cursor_hidden_passthrough_check is None
    assert not any(
        isinstance(child, settings_mod.ToggleSwitch)
        for child in dialog.children()
    ), "所有开关都必须被设置行接管，不能游离在窗口左上角"

    dialog.close()
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


def test_animation_icon_applier_updates_action_and_cleans_worker():
    """图标解码完成回调须经 GUI 线程槽更新 QAction，并清理 worker 记录。

    回归背景：ready 信号此前直连普通闭包，setIcon/update 在 QThreadPool
    工作线程执行（Qt 未定义行为）；现经 _AnimationIconApplier（挂在
    submenu 下、随菜单生命周期）队列投递回 GUI 线程。
    """
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import _AnimationIconApplier

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    submenu = QMenu(menu)
    submenu._animation_icon_workers = []
    action = submenu.addAction("测试动画")
    pump_calls = []
    worker = object()

    # 无图（解码失败/空帧）：不 setIcon，但必须移除 worker 并继续泵任务
    applier = _AnimationIconApplier(
        submenu, action, worker, lambda: pump_calls.append(1), parent=submenu
    )
    applier.on_ready(None)
    assert submenu._animation_icon_workers == []
    assert pump_calls == [1]

    # 有效图：菜单不可见（offscreen）时更新图标
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(0xFF3366FF)
    submenu._animation_icon_workers.append(worker)
    applier2 = _AnimationIconApplier(
        submenu, action, worker, lambda: pump_calls.append(2), parent=submenu
    )
    applier2.on_ready(image)
    assert submenu._animation_icon_workers == []
    assert pump_calls == [1, 2]
    assert not action.icon().isNull()

    # 菜单已销毁：槽必须静默返回，不访问已删 C++ 对象
    menu.deleteLater()
    app.processEvents()


def test_build_scripts_bundle_menu_templates_and_chat_styles():
    """三平台打包脚本必须包含菜单模板与聊天样式资源（防漏打包回归）。

    回归背景：Linux workflow 曾漏掉 pet/menu_templates（右键菜单在冻结版
    打不开）与 legacy/modern_styles.qss（聊天窗无样式）。

    构建定义统一在本地脚本（scripts/build_*.sh / build_onedir.ps1），
    CI workflow 只调用脚本不再内联打包命令，故资源断言指向脚本而非 workflow。
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    linux = (repo / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
    macos = (repo / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
    windows = (repo / "scripts" / "build_onedir.ps1").read_text(encoding="utf-8")

    for name, text in (("build_linux.sh", linux), ("build_macos.sh", macos), ("build_onedir.ps1", windows)):
        assert "menu_templates" in text, f"{name} 必须打包 pet/menu_templates"
        assert "legacy_styles.qss" in text, f"{name} 必须打包 legacy_styles.qss"
        assert "modern_styles.qss" in text, f"{name} 必须打包 modern_styles.qss"
    # 兜底：模板 JSON 缺失时 load_menu_template 必须回退内置模板而非抛异常
    import pet.context_menu as context_menu_mod
    assert context_menu_mod.load_menu_template("modern")["id"] == "modern"
    assert context_menu_mod.load_menu_template("legacy")["id"] == "legacy"


def test_store_fun_asset_keeps_bundled_paths_relative(tmp_path):
    """内置 assets 内的路径必须持久化为相对值（portable），外部文件保留绝对路径。

    回归背景：设置对话框把默认相对路径固化成安装目录绝对路径，目录移动/
    自更新后彩蛋弹窗失效。
    """
    from pet.fun_image_popup import bundled_assets_root, store_fun_asset

    default = bundled_assets_root() / "big_blue_fat_fish" / "ojingjing.jpg"
    # 绝对路径指向内置 assets → 归一化为相对值
    stored = store_fun_asset(str(default), default)
    assert stored == "assets/big_blue_fat_fish/ojingjing.jpg"
    # 相对值原样保留
    assert store_fun_asset("assets/big_blue_fat_fish", default) == "assets/big_blue_fat_fish"
    # 空值回退默认
    assert store_fun_asset("", default) == str(default)
    # 外部文件 → 绝对路径保留
    external = tmp_path / "custom" / "egg.jpg"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"x")
    assert store_fun_asset(str(external), default) == str(external)


def test_popup_image_paths_survives_missing_directory(tmp_path):
    """彩蛋图片目录不存在时回退默认彩蛋池（而非抛异常或空列表）。"""
    from pet.fun_image_popup import popup_image_paths

    paths = popup_image_paths(tmp_path / "does-not-exist")
    assert paths, "缺失目录应回退默认彩蛋图片池"
    assert all(path.is_file() for path in paths)


def test_config_normalizes_polluted_absolute_easter_egg_paths(tmp_path):
    """旧配置里已固化的内置资产绝对路径在加载时归一化回相对值。"""
    from pet.fun_image_popup import bundled_assets_root
    from pet.config import Config

    bundled = bundled_assets_root()
    (tmp_path / "config.json").write_text(
        '{"version": 4, "menu_easter_egg": {"enabled": true, "avatar": "'
        + str(bundled / "big_blue_fat_fish" / "ojingjing.jpg").replace("\\", "/")
        + '", "image_dir": "'
        + str(bundled / "big_blue_fat_fish").replace("\\", "/")
        + '"}}',
        encoding="utf-8",
    )
    cfg = Config(tmp_path)
    egg = cfg.get("menu_easter_egg")
    assert egg["avatar"] == "assets/big_blue_fat_fish/ojingjing.jpg"
    assert egg["image_dir"] == "assets/big_blue_fat_fish"


def test_easter_egg_activate_defers_until_menu_closes():
    """彩蛋点击在菜单可见时排入 deferred callbacks，菜单关闭后才开窗。

    回归背景：macOS 原生 NSMenu 跟踪循环中直接开窗被 AppKit 抑制（首点无效）。
    """
    import time

    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry_mod
    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu, {"title": "厉害了我的鲸"})
    opened = []
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(fun_entry_mod, "open_ojingjing_window", lambda config: opened.append(config))
    try:
        # 菜单不可见：走 singleShot 延迟
        entry._activate()
        deadline = time.time() + 1
        while not opened and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert opened, "不可见菜单点击应立即排队开窗"
        opened.clear()

        # 菜单可见：排入 deferred callbacks 并关闭菜单
        menu.show()
        app.processEvents()
        entry._activate()
        callbacks = list(getattr(menu, "_deferred_callbacks", ()))
        assert callbacks, "可见菜单点击必须排入 deferred callbacks"
        assert not menu.isVisible(), "点击后菜单应被关闭"
        callbacks[0]()
        assert opened, "回调应打开彩蛋窗口"
    finally:
        monkeypatch.undo()
        menu.close()
        app.processEvents()


def test_modern_settings_save_writes_autostart_wanted(tmp_path, monkeypatch):
    """新版设置保存必须记录 autostart_wanted（启动自检提醒依赖它）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    # 不碰真实系统自启：CI runner 上写入失败会触发 QMessageBox.warning，
    # offscreen 下无人交互（实测 access violation / 挂起）。
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda enabled: True)
    config = Config(tmp_path)
    assert config.get("autostart_wanted") is False
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.autostart_check.setChecked(True)
    dialog._save()
    assert config.get("autostart_wanted") is True
    dialog.close()
    app.processEvents()


def test_modern_provisional_config_falls_back_to_keyring(tmp_path, monkeypatch):
    """测试连接未填 Key 时必须回退系统钥匙串，否则默认场景误报 401。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    class FakeStore:
        def get(self, _ref):
            return "keyring-secret"

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    monkeypatch.setattr(dialog.ai_page, "_secret_store_type", FakeStore)
    provisional = dialog.ai_page.provisional_config()
    assert provisional.api_key == "keyring-secret"
    dialog.close()
    app.processEvents()


def test_spawned_children_are_reaped_after_exit():
    """孵化的子进程退出后必须从登记表回收（防 POSIX 僵尸 / 句柄泄漏）。"""
    import sys

    import pet.instance_launcher as launcher

    before = list(launcher._SPAWNED_CHILDREN)
    try:
        proc = launcher.launch_new_pet(offset_index=99)
        assert proc in launcher._SPAWNED_CHILDREN
        # 触发回收：活着的子进程必须保留
        launcher._reap_children()
        assert proc in launcher._SPAWNED_CHILDREN
        # 退出后必须被回收
        proc.terminate()
        import time
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        launcher._reap_children()
        assert proc not in launcher._SPAWNED_CHILDREN
    finally:
        for proc in list(launcher._SPAWNED_CHILDREN):
            if proc not in before and proc.poll() is None:
                proc.terminate()
        launcher._SPAWNED_CHILDREN[:] = before


def test_modern_settings_close_autosaves(tmp_path, monkeypatch):
    """直接关闭（X）新版设置也必须落盘，不能只靠「保存并退出」。

    回归背景：用户改完字体颜色/气泡方案后直接关窗，修改全部丢失。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.bubble_style_select.setCurrentData("breath_bubble")
    dialog.light_background_picker.edit.setText("#123456")
    dialog.close()  # 不点「保存并退出」
    app.processEvents()
    assert config.get("self_talk_bubble_style") == "breath_bubble"
    assert config.get("context_menu_appearance", {}).get("light_background") == "#123456"
    # 重载磁盘验证
    reloaded = Config(tmp_path)
    assert reloaded.get("self_talk_bubble_style") == "breath_bubble"


def test_settings_stylesheet_has_dark_overrides(monkeypatch):
    """深色系统下新版设置必须追加深色覆盖段（白底白字不可读问题）。"""
    from pet.modern_settings_dialog import _settings_stylesheet

    monkeypatch.setattr("pet.modern_settings_dialog._system_dark", lambda: True)
    qss = _settings_stylesheet()
    assert "background: #202024" in qss
    assert "color: #e4e4e9" in qss
    monkeypatch.setattr("pet.modern_settings_dialog._system_dark", lambda: False)
    qss_light = _settings_stylesheet()
    assert "background: #202024" not in qss_light
    # 浅色也必须显式给按钮补文字色（防深色 palette 白字）
    assert "QPushButton { color: #202020; }" in qss_light


def test_settings_window_uses_the_explicit_dark_appearance_on_a_light_system(
    tmp_path, monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod, "_system_dark", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("context_menu_appearance", {"theme": "dark"})

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    assert "QDialog { background: #202024" in dialog.styleSheet()

    dialog.close()
    app.processEvents()


def test_settings_window_rethemes_immediately_with_the_appearance_selector(
    tmp_path, monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod, "_system_dark", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert "QDialog { background: #202024" not in dialog.styleSheet()

    dialog.menu_theme_select.setCurrentData("dark")
    app.processEvents()
    assert "QDialog { background: #202024" in dialog.styleSheet()

    dialog.menu_theme_select.setCurrentData("light")
    app.processEvents()
    assert "QDialog { background: #202024" not in dialog.styleSheet()

    dialog.close()
    app.processEvents()


def test_modern_settings_finished_refreshes_even_on_rejected(tmp_path, monkeypatch):
    """新版设置直接关闭（Rejected）也必须把改动应用到桌宠。

    回归背景：只有 Accepted（保存并退出）才刷新；X 关闭时 closeEvent 已
    自动保存，但桌宠 scale/拖动物理等不生效。
    """
    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp.__new__(PetApp)
    owner.modern_settings_dialog = object()
    refreshed = []

    class FakeWin:
        def refresh_pet_settings(self):
            refreshed.append(1)

        def set_bubble_suppressed(self, _suppressed):
            pass

    owner.win = FakeWin()
    owner.config = Config(tmp_path)
    owner._apply_balance_timer = lambda: None
    owner._refresh_chat_windows = lambda: None
    owner.todo_service = types.SimpleNamespace(apply_config=lambda: None)
    monkeypatch.setattr(app_mod, "_mac_set_dock_icon_visible", lambda *a, **k: None)
    PetApp._modern_settings_finished(owner, 0)  # QDialog.Rejected（X 关闭）
    assert refreshed == [1], "Rejected 关闭也必须刷新桌宠"


def test_ai_page_warns_when_keyring_unavailable(tmp_path, monkeypatch):
    """keyring 不可用时保存必须提示，且 key 仅保留内存（不落盘明文）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    class FakeStore:
        def get(self, _ref):
            return ""

        def set(self, _ref, _value):
            return False

    warnings = []
    monkeypatch.setattr(settings_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    monkeypatch.setattr(dialog.ai_page, "_secret_store_type", FakeStore)
    dialog.ai_page.key.setText("sk-new")
    dialog.ai_page.save()
    assert len(warnings) == 1
    assert "系统安全存储" in str(warnings[0][2])
    assert dialog.ai_page.settings.active_config.api_key == "sk-new"
    dialog.close()
    app.processEvents()


def test_modern_settings_save_warns_on_failure(tmp_path, monkeypatch):
    """保存失败（此处置目标为目录迫使 os.replace 失败）时提示用户+配置路径。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    warnings = []
    monkeypatch.setattr(settings_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    config = Config(tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)  # 目标为目录，os.replace 必失败
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    dialog._save()
    assert any("保存失败" in str(x[1]) for x in warnings)
    assert any(str(config.path) in str(x[2]) for x in warnings)
    dialog.close()
    app.processEvents()


def test_modern_settings_close_applies_autostart(tmp_path, monkeypatch):
    """直接关闭（X）新版设置也必须应用开机自启开关（与「保存并退出」一致）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    applied = []
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(
        settings_mod.autostart_mod,
        "set_enabled",
        lambda enabled: applied.append(enabled) or True,
    )
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.autostart_check.setChecked(True)
    dialog.close()  # X 关闭，不点「保存并退出」
    app.processEvents()
    assert applied == [True]
    assert config.get("autostart_wanted") is True
    dialog.close()
    app.processEvents()


def test_modern_autostart_write_failure_warns(tmp_path, monkeypatch):
    """开机自启写入失败时必须弹窗提示（此前无任何反馈，用户不知自启没生效）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda enabled: False)
    warnings = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a)
    )
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    # 与初始状态不同（初始 = is_enabled() = False）→ 触发写入
    dialog.autostart_check.setChecked(True)
    dialog._apply_autostart()
    assert len(warnings) == 1
    assert "开机自启设置失败" in str(warnings[0][1])
    assert "失败" in str(warnings[0][2])
    dialog.close()
    app.processEvents()
# --- macOS Dock recovery menu regression (2026-09-03) ---


def test_macos_dock_menu_keeps_settings_reachable_when_pet_is_mouse_through(monkeypatch):
    from unittest import mock

    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod

    app = QApplication.instance() or QApplication([])
    controller = app_mod.PetApp.__new__(app_mod.PetApp)
    controller.app = app
    controller.win = mock.Mock()
    controller.open_modern_settings = mock.Mock()
    controller.open_chat = mock.Mock()
    controller.enable_chat = True
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")

    menu = controller._install_macos_dock_menu()

    assert menu is controller.dock_menu
    assert menu.property("dockMenuInstalled") is callable(getattr(menu, "setAsDockMenu", None))
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels[:3] == ["显示桌宠", "桌宠设置", "AI 对话"]
    next(action for action in menu.actions() if action.text() == "桌宠设置").trigger()
    controller.open_modern_settings.assert_called_once_with()
    menu.close()
    app.processEvents()
