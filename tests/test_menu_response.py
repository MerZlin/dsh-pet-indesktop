"""菜单启动策略与真实 Qt 悬浮事件回归（每例独立进程，隔离全局样式）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _probe(entry: str) -> None:
    from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu, QProxyStyle, QPushButton, QStyle

    class SlowMenuStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, return_data=None):  # noqa: N802
            overrides = {
                QStyle.StyleHint.SH_Menu_SubMenuPopupDelay: 100,
                QStyle.StyleHint.SH_Menu_SubMenuSloppyCloseTimeout: 1000,
                QStyle.StyleHint.SH_Menu_SubMenuSloppySelectOtherActions: 0,
                QStyle.StyleHint.SH_Menu_SubMenuUniDirection: 0,
            }
            return overrides.get(hint, super().styleHint(hint, option, widget, return_data))

    class HoldSyntheticPointer(QObject):
        def eventFilter(self, watched, event):  # noqa: N802
            # offscreen 无真实指针；固定已发送的 Enter，避免窗口创建生成的事件改写它。
            return event.type() in (QEvent.Type.Enter, QEvent.Type.Leave)

    app = QApplication([])
    app.setStyle(SlowMenuStyle("Fusion"))
    button = QPushButton()
    from pet.context_menus.menu_styles.common import ResponsiveMenuStyle, install_responsive_menu_style

    baseline_hints = {
        hint: app.style().styleHint(hint, None, button) for hint in ResponsiveMenuStyle._MENU_HINTS
    }
    with tempfile.TemporaryDirectory() as directory:
        if entry == "settings":
            from pet import __main__ as settings_entry
            from pet.config import Config

            with patch("pet.platform_mac._mac_set_dock_icon_visible"), patch.object(
                settings_entry, "_exec_settings", return_value=0,
            ):
                assert settings_entry._run_settings(Config(base=Path(directory))) == 0
            assert "pet.app" not in sys.modules, "设置进程不能因菜单样式导入主应用"
        else:
            from pet import app as app_entry

            gate = SimpleNamespace(acquired=False, path=Path(directory), holder_pid=lambda: 1)
            with patch.object(app_entry, "QApplication", return_value=app), patch.object(
                app_entry, "_default_base", return_value=Path(directory),
            ), patch.object(app_entry.overlay_gate_mod, "acquire_overlay_instance_gate", return_value=gate):
                assert app_entry.main(["pet"]) == 0

    for hint, value in baseline_hints.items():
        assert app.style().styleHint(hint, None, button) == value, "非菜单控件必须保留平台样式"
    style = app.style()
    install_responsive_menu_style(app)
    assert app.style() is style, "重复初始化不能叠加代理样式"

    def pump_until(predicate):
        deadline = time.perf_counter() + 5
        while not predicate() and time.perf_counter() < deadline:
            app.processEvents()
        assert predicate(), "Qt 菜单未在预算内展开"

    def move(menu, action):
        pos = menu.actionGeometry(action).center()
        for _ in range(3):
            QApplication.sendEvent(menu, QMouseEvent(
                QEvent.Type.MouseMove, pos, menu.mapToGlobal(pos),
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            ))

    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    timings = []
    for _ in range(3):
        root = QMenu()
        tools = root.addMenu("工具与帮助")
        tools.addAction("检查更新")
        agents = root.addMenu("Agent 联动")
        claude = agents.addAction("Claude Code")
        cursor = agents.addAction("Cursor")
        for menu in (root, tools, agents):
            apply_modern_menu_style(menu)
        root.popup(QPoint(20, 20))
        app.processEvents()
        pos = root.rect().center()
        QApplication.sendEvent(root, QEnterEvent(QPointF(pos), QPointF(pos), QPointF(root.mapToGlobal(pos))))
        guard = HoldSyntheticPointer(root)
        root.installEventFilter(guard)
        try:
            move(root, tools.menuAction())
            pump_until(tools.isVisible)
            move(root, tools.menuAction())
            start = time.perf_counter()
            move(root, agents.menuAction())
            assert root.activeAction() == agents.menuAction(), "已有子菜单展开时，同级高亮必须立即跟随鼠标"
            timings.append((time.perf_counter() - start) * 1000)
            assert not agents.isVisible(), "高亮切换不得取消子菜单的展开等待"
            pump_until(agents.isVisible)
            move(agents, claude)
            move(agents, cursor)
            assert agents.activeAction() == cursor, "叶子选项高亮必须立即跟随鼠标"
            root.setFocus()
            from PySide6.QtTest import QTest
            QTest.keyClick(root, Qt.Key.Key_Up)
            assert root.activeAction() == tools.menuAction(), "键盘导航必须仍可用"
        finally:
            root.close()
            root.deleteLater()
            app.processEvents()
    print(json.dumps({"entry": entry, "highlight_ms": timings}))


@pytest.mark.parametrize("entry", ["main", "settings"])
def test_startup_menu_highlight_follows_pointer_with_an_open_submenu(entry):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), entry], cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    measured = json.loads(result.stdout.splitlines()[-1])
    assert len(measured["highlight_ms"]) == 3
    print(measured)


if __name__ == "__main__":
    _probe(sys.argv[1])
