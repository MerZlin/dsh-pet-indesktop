# -*- coding: utf-8 -*-
"""灵动岛重生：四边停靠/滑出、展开卡片、事件动效、配置清洗。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.dynamic_island import (
    DynamicIsland,
    _STRIP_THICKNESS,
    dock_edge_for,
    spring_step,
    strip_rect_for,
)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def _avail() -> QRect:
    screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1040)


def _mouse(kind: QEvent.Type, widget, global_pos: QPoint,
           button=Qt.MouseButton.LeftButton,
           buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    local = widget.mapFromGlobal(global_pos)
    return QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                       button, buttons, Qt.KeyboardModifier.NoModifier)


def _drag_to(widget, target_global: QPoint) -> None:
    """完整拖一次：按下 → 移动（超过拖拽阈值）→ 在 target 松手。"""
    start = widget.geometry().center()
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, start))
    mid = QPoint((start.x() + target_global.x()) // 2,
                 (start.y() + target_global.y()) // 2)
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, mid))
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, widget, target_global))
    widget.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, widget, target_global,
               buttons=Qt.MouseButton.NoButton))


def _click(widget) -> None:
    center = widget.geometry().center()
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, widget, center))
    widget.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, widget, center,
               buttons=Qt.MouseButton.NoButton))


# ------------------------------------------------------------ 纯函数
def test_spring_step_converges_and_overshoots():
    value, velocity = 1.0, 6.0  # 事件冲量
    peak = value
    for _ in range(600):
        value, velocity = spring_step(value, velocity, 1.0, 1 / 60)
        peak = max(peak, value)
    assert peak > 1.05  # 有过冲才有果冻感
    assert abs(value - 1.0) < 0.01
    assert abs(velocity) < 0.05


def test_dock_edge_for_four_edges_and_none():
    available = QRect(0, 0, 1920, 1040)
    capsule = QRect(900, 300, 200, 44)
    assert dock_edge_for(capsule, available) == "none"
    assert dock_edge_for(QRect(900, 2, 200, 44), available) == "top"
    assert dock_edge_for(QRect(900, 1035 - 44, 200, 44), available) == "bottom"
    assert dock_edge_for(QRect(3, 500, 200, 44), available) == "left"
    assert dock_edge_for(QRect(1917 - 200, 500, 200, 44), available) == "right"


def test_strip_rect_shapes():
    available = QRect(0, 0, 1920, 1040)
    capsule = QRect(900, 2, 200, 44)
    top = strip_rect_for(capsule, "top", available)
    assert top.y() == 0 and top.height() == _STRIP_THICKNESS and top.width() == 200
    bottom = strip_rect_for(capsule, "bottom", available)
    assert bottom.bottom() == available.bottom() and bottom.height() == _STRIP_THICKNESS
    left = strip_rect_for(capsule, "left", available)
    assert left.x() == 0 and left.width() == _STRIP_THICKNESS
    right = strip_rect_for(capsule, "right", available)
    assert right.right() == available.right() and right.width() == _STRIP_THICKNESS
    assert strip_rect_for(capsule, "none", available) == capsule


# ------------------------------------------------------------ 停靠与滑出
def test_drag_to_edge_docks_and_hover_peeks(tmp_path):
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        available = _avail()
        # 拖到顶边 → 停靠成细条
        _drag_to(island, QPoint(island.x(), available.top()))
        island._finish_animations()
        assert island._debug_state()["mode"] == "docked"
        assert island._debug_state()["dock_edge"] == "top"
        assert island.height() == _STRIP_THICKNESS
        assert island.y() == available.top()
        # 配置已持久化
        assert island.config.get("dynamic_island")["dock_edge"] == "top"

        # 鼠标靠近 → 滑出完整胶囊
        island.enterEvent(None)
        island._finish_animations()
        assert island.height() == 44
        # 鼠标离开 → 延迟后收回细条
        island.leaveEvent(None)
        island._dock_back()  # 直接驱动定时器回调
        island._finish_animations()
        assert island.height() == _STRIP_THICKNESS

        # 拖离顶边（屏幕可用区正中心，不赌分辨率）→ 回到正常模式
        _drag_to(island, available.center())
        island._finish_animations()
        assert island._debug_state()["mode"] == "normal"
        assert island._debug_state()["dock_edge"] == "none"
        assert island.height() == 44
    finally:
        island.hide()
        island.deleteLater()


def test_edge_dock_disabled_never_docks(tmp_path):
    _qapp()
    island = _island(tmp_path, edge_dock=False)
    try:
        island.show()
        available = _avail()
        _drag_to(island, QPoint(island.x(), available.top()))
        island._finish_animations()
        assert island._debug_state()["mode"] == "normal"
        assert island._debug_state()["dock_edge"] == "none"
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------------ 展开卡片
def test_click_expands_card_and_buttons_emit(tmp_path):
    app = _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.set_balance_info("高峰 → 23:00", "余额 ¥12.34")
        island.set_last_message("我吃了三碗饭")
        got = {"toggle": 0, "chat": 0, "settings": 0}
        island.toggle_pet_requested.connect(lambda: got.__setitem__("toggle", 1))
        island.open_chat_requested.connect(lambda: got.__setitem__("chat", 1))
        island.open_settings_requested.connect(lambda: got.__setitem__("settings", 1))

        _click(island)
        island._finish_animations()
        app.processEvents()
        state = island._debug_state()
        assert state["mode"] == "expanded"
        assert state["card_visible"] is True
        assert island._card_balance_label.text() == "余额 ¥12.34"
        assert island._card_message_label.text() == "我吃了三碗饭"

        island._card_toggle_btn.click()
        island._card_chat_btn.click()
        island._card_settings_btn.click()
        assert got == {"toggle": 1, "chat": 1, "settings": 1}
        # 任一按钮动作后卡片收起
        assert island._debug_state()["mode"] == "normal"

        # 再点一次胶囊 → 展开；再点 → 收起
        _click(island)
        island._finish_animations()
        assert island._debug_state()["mode"] == "expanded"
        _click(island)
        island._finish_animations()
        assert island._debug_state()["mode"] == "normal"
    finally:
        island.hide()
        island.deleteLater()
        app.processEvents()


def test_click_toggle_pet_legacy_action(tmp_path):
    _qapp()
    island = _island(tmp_path, click_action="toggle_pet")
    try:
        island.show()
        clicks = []
        island.clicked.connect(lambda: clicks.append(1))
        _click(island)
        assert clicks == [1]
        assert island._debug_state()["mode"] == "normal"  # 不展开卡片
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------------ 事件动效
def test_notify_event_starts_animation_only_when_enabled(tmp_path):
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        assert island._debug_state()["animating"] is False
        island.notify_event("reply")
        assert island._debug_state()["animating"] is True
        island._finish_animations()
        assert island._debug_state()["animating"] is False

        island._cfg = dict(island._cfg, event_effects=False)
        island.notify_event("reply")
        assert island._debug_state()["animating"] is False
    finally:
        island.hide()
        island.deleteLater()


def test_bump_and_agent_active(tmp_path):
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.bump(2.0, 1.0, 0.0)  # 果冻墙受击：表面压扁回弹 + 定向踢动
        assert island._debug_state()["animating"] is True
        assert island._tilt_v != 0.0
        assert island._kick_vx > 0.0  # 踢动方向跟随撞击方向
        assert island._squish_v < 0.0  # 表面被压扁（弹簧回摆出果冻抖动）
        island._finish_animations()
        assert island._tilt == 0.0
        assert island._kick_x == 0.0 and island._kick_y == 0.0
        assert island._squish == 1.0

        island.set_agent_active(True)  # dsh 开工：蓝灯 + 弹一下
        assert island._agent_active is True
        assert island._status_dot_color().blue() > 200
        island.set_agent_active(False)
        assert island._status_dot_color().green() > 200  # 回到可见绿灯
    finally:
        island.hide()
        island.deleteLater()


def test_event_bounce_is_positional_with_squish(tmp_path):
    """事件动效 = 整数位移下压回弹 + 表面轻压 + 微缩放（不再有大幅挤压变形）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.notify_event("reply")
        assert island._kick_y > 0.0  # 下压
        assert island._squish_v < 0.0  # 表面轻压（史莱姆形变）
        assert abs(island._scale_v) < 0.5  # 缩放只做辅助（旧版 5.0 会糊文字）
        island._finish_animations()
        assert island._debug_state()["animating"] is False
    finally:
        island.hide()
        island.deleteLater()


def test_expand_card_emits_signal(tmp_path):
    """卡片展开发出 card_expanded（AppShell 借此静默刷新余额）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        hits = []
        island.card_expanded.connect(lambda: hits.append(1))
        island.expand_card()
        island._finish_animations()
        assert hits == [1]
        island.collapse_card()
        assert hits == [1]  # 收起不发
    finally:
        island.hide()
        island.deleteLater()


def test_appearance_opacity_and_accent(tmp_path):
    """不透明度与主题色进入调色板；非法值回退默认。"""
    _qapp()
    island = _island(tmp_path, opacity=0.5, accent="pink")
    try:
        bg, _primary, _secondary = island._style_palette()
        assert bg.alpha() == round(235 * 0.5)
        assert island._accent_color().name() == "#ff7ab8"
        island._cfg = dict(island._cfg, opacity="bogus", accent="not-a-color")
        assert island._opacity() == 1.0
        assert island._accent_color().name() == "#40b8ff"
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------------ 配置清洗
def test_config_cleans_new_island_keys(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {
        "click_action": "bogus", "event_effects": 0, "edge_dock": 1,
        "dock_edge": "celling", "enabled": True,
    })
    cleaned = cfg.get("dynamic_island")
    assert cleaned["click_action"] == "expand"  # 非法值回默认
    assert cleaned["event_effects"] is False
    assert cleaned["edge_dock"] is True
    assert cleaned["dock_edge"] == "none"  # 非法边回 none

    cfg.set("dynamic_island", {
        "click_action": "toggle_pet", "dock_edge": "left", "enabled": True,
    })
    cleaned = cfg.get("dynamic_island")
    assert cleaned["click_action"] == "toggle_pet"
    assert cleaned["dock_edge"] == "left"

    # 外观新键：opacity 钳位、accent 白名单（acrylic 已下线，非法风格回默认）
    cfg.set("dynamic_island", {
        "style": "glass", "opacity": 0.55, "accent": "purple", "enabled": True,
    })
    cleaned = cfg.get("dynamic_island")
    assert cleaned["style"] == "glass"
    assert cleaned["opacity"] == 0.55
    assert cleaned["accent"] == "purple"
    cfg.set("dynamic_island", {
        "style": "acrylic", "opacity": 9.9, "accent": "gold", "enabled": True,
    })
    cleaned = cfg.get("dynamic_island")
    assert cleaned["style"] == "dark"     # 已下线的风格回默认
    assert cleaned["opacity"] == 1.0      # 超界钳到 1.0
    assert cleaned["accent"] == "blue"    # 非法色回默认


def test_expanded_mode_disables_window_transform(tmp_path):
    """展开卡片态禁用整窗变换（截边回归）：悬停不放大、单击展开后目标归零、
    kick 平移参与不到 paint（展开态 paintEvent 冒烟不炸）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        # 悬停（收起态）会抬放大目标
        island.enterEvent(None)
        assert island._hover_scale_target == 1.02
        # 单击展开：目标必须拉回 1.0（否则展开后仍按 1.02 缩放，贴边底板被截）
        island.expand_card()
        assert island._mode == "expanded"
        assert island._hover_scale_target == 1.0
        # 展开态再次悬停：不再抬目标
        island.enterEvent(None)
        assert island._hover_scale_target == 1.0
        # 展开态 + 待播放的 kick：paintEvent 跳过整窗平移（冒烟不炸）
        island._bounce(4.0)
        island.repaint()
        island.update()
        QApplication.processEvents()
        # 收起后悬停放大恢复
        island.collapse_card()
        island.enterEvent(None)
        assert island._hover_scale_target == 1.02
    finally:
        island.hide()
        island.deleteLater()


def test_expanded_mode_freezes_squish(tmp_path):
    """展开卡片态冻结史莱姆形变：squish 打到下限也不鼓出窗口（截边回归）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.expand_card()
        island._squish = 0.85  # 形变下限（bump 最大冲击）
        rect, _radius = island._squished_capsule_rect()
        assert rect.width() <= island.width() - 2 * 6 + 0.01  # 不超内缩边界
        assert rect.width() <= island.width()
        # 收起态形变恢复（对照）
        island.collapse_card()
        island._squish = 0.85
        rect2, _ = island._squished_capsule_rect()
        assert rect2.width() > rect.width()
    finally:
        island.hide()
        island.deleteLater()


def test_edge_dock_off_resets_docked_island(tmp_path):
    """关闭「靠边半隐藏」时已停靠的岛复位回正常形态（不再卡 16px 细条）。"""
    _qapp()
    island = _island(tmp_path, edge_dock=True, dock_edge="left")
    try:
        island.show()
        island._apply_position()
        assert island._mode == "docked"
        assert island.dock_edge == "left"
        # 设置里关掉开关（dock_edge 键仍保留），刷新配置
        data = dict(island._cfg)
        data["edge_dock"] = False
        island.config.set("dynamic_island", data)
        island.refresh_from_config()
        assert island._mode == "normal"
        assert island.dock_edge == "none"  # 读取处被总开关拦截
        assert not island._hover_peek
        # 开关再开：存储的边恢复
        data["edge_dock"] = True
        island.config.set("dynamic_island", data)
        island.refresh_from_config()
        assert island.dock_edge == "left"
    finally:
        island.hide()
        island.deleteLater()


def test_drag_start_clears_geometry_animation(tmp_path):
    """拖拽起手清掉进行中的几何动画目标（滑出动画不再和拖拽抢窗口）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island._animate_to(QRect(100, 100, 260, 44))  # 模拟进行中的几何动画
        assert island._geo_to is not None
        # 模拟按下并拖动超过阈值
        press = QPoint(island.x() + 10, island.y() + 10)
        island.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(10, 10),
            QPointF(press), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        island.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(60, 10),
            QPointF(press + QPoint(50, 0)), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert island._dragging
        assert island._geo_to is None
        assert island._geo_from is None
    finally:
        island.hide()
        island.deleteLater()


def test_apply_position_does_not_hijack_expanded_card(tmp_path):
    """展开卡片态不被停靠劫持（设置保存触发 _apply_position 时 mode 保持
    expanded、卡片不收起）；收起后按 dock_edge 正常停靠。"""
    _qapp()
    island = _island(tmp_path, edge_dock=True, dock_edge="left")
    try:
        island.show()
        island.expand_card()
        assert island._mode == "expanded"
        island._apply_position()  # 保存设置的触发路径
        assert island._mode == "expanded"  # 不被劫持成 docked
        assert island._card_box.isVisible()
        # 收起后按 dock_edge 正常停靠（collapse_card 的分支）
        island.collapse_card()
        assert island._mode == "docked"
    finally:
        island.hide()
        island.deleteLater()
