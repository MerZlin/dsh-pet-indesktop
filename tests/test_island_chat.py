# -*- coding: utf-8 -*-
"""灵动岛对话气泡：桌宠隐藏时岛作为对话代理（点击弹气泡 / 回复到达预览）。

覆盖：岛单击路由（hidden_chat × click_action × 桌宠可见性）、
IslandChatBubble 锚定定位与预览态、AppShell 接线（可用性判定、
点击开关切换、显示桌宠、回复到达自动弹出与在场守卫）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.app import AppShell
from pet.chat.service import ChatService
from pet.collision_ipc import _stop_live_sessions_for_tests
from pet.config import Config
from pet.dynamic_island import DynamicIsland
from pet.island_chat import IslandChatBubble


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------ 岛侧路由
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


def _click(widget) -> None:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    center = widget.geometry().center()
    local = widget.mapFromGlobal(center)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local), QPointF(center),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(center),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(press)
    widget.mouseReleaseEvent(release)


def _teardown_island(island: DynamicIsland) -> None:
    island.hide()
    island.deleteLater()


def test_click_when_pet_hidden_emits_chat_requested(tmp_path):
    """桌宠隐藏 + hidden_chat 开：单击胶囊发 chat_requested，不展开卡片。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.set_pet_visible(False)  # 聚合可见态：全部隐藏
        chats, cards = [], []
        island.chat_requested.connect(lambda: chats.append(1))
        island.card_expanded.connect(lambda: cards.append(1))
        _click(island)
        assert chats == [1]
        assert cards == []
        assert island._debug_state()["mode"] == "normal"  # 卡片未展开
    finally:
        _teardown_island(island)


def test_click_when_pet_visible_still_expands_card(tmp_path):
    """桌宠可见：单击行为不变（展开卡片），不发 chat_requested。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.set_pet_visible(True)
        chats, cards = [], []
        island.chat_requested.connect(lambda: chats.append(1))
        island.card_expanded.connect(lambda: cards.append(1))
        _click(island)
        island._finish_animations()
        assert chats == []
        assert cards == [1]
        assert island._debug_state()["mode"] == "expanded"
    finally:
        island.collapse_card()
        _teardown_island(island)


def test_click_toggle_pet_action_unaffected(tmp_path):
    """click_action=toggle_pet：桌宠隐藏时单击仍是旧行为（发 clicked）。"""
    _qapp()
    island = _island(tmp_path, click_action="toggle_pet")
    try:
        island.show()
        island.set_pet_visible(False)
        clicked, chats = [], []
        island.clicked.connect(lambda: clicked.append(1))
        island.chat_requested.connect(lambda: chats.append(1))
        _click(island)
        assert clicked == [1]
        assert chats == []
    finally:
        _teardown_island(island)


def test_click_hidden_chat_disabled_falls_back_to_card(tmp_path):
    """hidden_chat 关：桌宠隐藏时单击仍展开卡片（旧行为）。"""
    _qapp()
    island = _island(tmp_path, hidden_chat=False)
    try:
        island.show()
        island.set_pet_visible(False)
        chats, cards = [], []
        island.chat_requested.connect(lambda: chats.append(1))
        island.card_expanded.connect(lambda: cards.append(1))
        _click(island)
        island._finish_animations()
        assert chats == []
        assert cards == [1]
    finally:
        island.collapse_card()
        _teardown_island(island)


# ------------------------------------------------------------ 气泡本体
def test_island_chat_bubble_positions_below_and_flips_above(tmp_path):
    """锚定岛定位：默认岛下方（尾巴朝上），岛贴近屏幕底部时翻到上方。"""
    app = _qapp()
    island = _island(tmp_path)
    bubble = IslandChatBubble(Config(base=tmp_path))
    try:
        island.show()
        avail = app.primaryScreen().availableGeometry()
        # 岛在屏幕上部 → 气泡在岛下方，尾巴朝上
        island.move(avail.center().x(), avail.top() + 40)
        bubble._anchor = island
        bubble.position_near_pet()
        assert bubble.geometry().top() >= island.geometry().bottom()
        assert bubble._tail_up is True
        # 岛贴底 → 气泡翻到岛上方，尾巴朝下
        island.move(avail.center().x(), avail.bottom() - island.height() - 4)
        bubble.position_near_pet()
        assert bubble.geometry().bottom() <= island.geometry().top()
        assert bubble._tail_up is False
        # 气泡整体不出屏幕可用区
        assert bubble.geometry().right() <= avail.right()
        assert bubble.geometry().left() >= avail.left()
    finally:
        bubble.close()
        bubble.deleteLater()
        _teardown_island(island)


def test_island_chat_show_reply_display_only(tmp_path):
    """预览回复：只改展示文本，不产生新的在途请求、不落库。"""
    _qapp()
    cfg = Config(base=tmp_path)
    bubble = IslandChatBubble(cfg)
    try:
        before = list(bubble.session.messages)
        bubble.show_reply("你好呀")
        assert bubble._reply_full == "你好呀"
        assert bubble.output.text() == "你好呀"
        assert bubble._active_request_id is None
        assert list(bubble.session.messages) == before  # 不动会话历史
        assert not bubble.service.busy
    finally:
        bubble.close()
        bubble.deleteLater()


def test_island_chat_show_pet_button(tmp_path):
    """「显示桌宠」按钮发出 show_pet_requested。"""
    _qapp()
    bubble = IslandChatBubble(Config(base=tmp_path))
    try:
        hits = []
        bubble.show_pet_requested.connect(lambda: hits.append(1))
        bubble.show_pet_btn.click()
        assert hits == [1]
    finally:
        bubble.close()
        bubble.deleteLater()


# ------------------------------------------------------------ AppShell 接线
def _make_shell(tmp_path: Path, **island_cfg) -> AppShell:
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True, "x": 400, "y": 300,
        **island_cfg,
    })
    shell = AppShell.__new__(AppShell)
    shell.config = cfg
    shell.island = None
    shell.island_collision = None
    shell.island_chat = None
    shell.instance = None
    shell._instances = []
    return shell


def _teardown_shell(shell) -> None:
    try:
        body = getattr(shell, "island_collision", None)
        if body is not None:
            body.stop()
    finally:
        island = getattr(shell, "island", None)
        if island is not None:
            island.hide()
            island.deleteLater()
        bubble = getattr(shell, "island_chat", None)
        if bubble is not None:
            bubble.close()
            bubble.deleteLater()
        ChatService.unregister_global_finished(shell._on_global_chat_finished)
        _stop_live_sessions_for_tests()
        QApplication.processEvents()


def test_island_chat_availability_gates(tmp_path):
    """可用性判定：岛未建 / hidden_chat 关 → 不可用；岛建好 → 可用。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=False)
    try:
        assert shell._island_chat_available() is False  # 岛未创建
        shell._sync_dynamic_island()
        assert shell._island_chat_available() is False  # hidden_chat 关
        cfg = dict(shell.config.get("dynamic_island"))
        cfg["hidden_chat"] = True
        shell.config.set("dynamic_island", cfg)
        assert shell._island_chat_available() is True
        shell.enable_chat = False  # no-chat 打包变体（setter 缓存 _enable_chat）
        assert shell._island_chat_available() is False
    finally:
        _teardown_shell(shell)


def test_shell_click_toggles_island_chat_bubble(tmp_path):
    """桌宠隐藏时 _chat_from_island：首次弹出（激活、锚定岛），再次收起。"""
    app = _qapp()
    shell = _make_shell(tmp_path)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        shell._chat_from_island()
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()
        assert bubble._anchor is shell.island
        # 再点一次 → 收起（开关语义）
        shell._chat_from_island()
        QApplication.processEvents()
        assert not bubble.isVisible()
    finally:
        _teardown_shell(shell)


def test_shell_show_pets_from_island_chat(tmp_path):
    """气泡内「显示桌宠」：调 set_pet_visible(True) 并收起气泡（无实例时不出错）。"""
    _qapp()
    shell = _make_shell(tmp_path)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        shell._show_island_chat(activate=True)
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()
        shell._show_pets_from_island_chat()
        QApplication.processEvents()
        assert shell.island._pet_visible is True
        assert not bubble.isVisible()
    finally:
        _teardown_shell(shell)


def test_shell_auto_pop_on_global_chat_finished_when_hidden(tmp_path):
    """桌宠隐藏 + 回复到达：自动弹预览气泡（不激活）；回复文本进展示层。"""
    _qapp()
    shell = _make_shell(tmp_path)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        shell._on_global_chat_finished("半夜好呀")
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()
        assert QApplication.activeWindow() is not bubble  # 预览态不抢焦点
        assert bubble._reply_full == "半夜好呀"
        assert bubble._auto_collapse.isActive()  # 超时自动收回已排程
        # 岛气泡在场（多半自己刚回完话）：不重复弹、不重置锚定状态
        shell._on_global_chat_finished("第二条")
        assert bubble._reply_full == "半夜好呀"
    finally:
        _teardown_shell(shell)


def test_shell_no_auto_pop_when_pet_visible(tmp_path):
    """桌宠可见：回复到达只推动效，不弹岛气泡。"""
    _qapp()
    shell = _make_shell(tmp_path)

    class _Win:
        @staticmethod
        def isVisible():
            return True

    class _Inst:
        win = _Win()

    shell._instances = [_Inst()]
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(True)
        shell._on_global_chat_finished("在的")
        assert shell.island_chat is None
    finally:
        _teardown_shell(shell)


def test_shell_no_auto_pop_when_full_chat_window_open(tmp_path):
    """桌宠隐藏但完整聊天窗开着：回复已有去处，不弹岛预览。"""
    _qapp()
    shell = _make_shell(tmp_path)

    class _Win:
        @staticmethod
        def isVisible():
            return False  # 桌宠隐藏

    class _ChatWin:
        @staticmethod
        def isVisible():
            return True  # 聊天窗开着

    class _Inst:
        win = _Win()
        modern_chat_window = _ChatWin()
        legacy_chat_window = None

    shell._instances = [_Inst()]
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        shell._on_global_chat_finished("看聊天窗就好")
        assert shell.island_chat is None
    finally:
        _teardown_shell(shell)
