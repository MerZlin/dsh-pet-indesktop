# -*- coding: utf-8 -*-
"""灵动岛对话气泡：桌宠隐藏时岛作为对话代理（点击弹气泡 / 回复到达预览）。

覆盖：岛单击路由（hidden_chat × click_action × 桌宠可见性）、
IslandChatBubble 锚定定位与预览态、AppShell 接线（可用性判定、
点击开关切换、显示桌宠、回复到达自动弹出与在场守卫）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication

from pet.app import AppShell
from pet.chat.service import ChatService
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.chat import IslandChatBubble


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
    island = DynamicIsland(cfg)
    # 岛的常规形态是**可见**的交互面：隐藏的岛不弹气泡（AppShell 的可用性判定
    # 看宿主侧运行态，见 test_running_island_disable_retires_chat_availability）。
    island.show()
    return island


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
        _doc = QTextDocument()
        _doc.setHtml(bubble.output.text())   # 富文本信封 → 纯文本比对
        assert _doc.toPlainText().rstrip("\n") == "你好呀"
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
        # 4.4b：collision_ipc 会话已随退役层删除，无需再收口
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


def test_shell_click_when_no_chat_reshows_pets(tmp_path):
    """纯桌宠版（打包时排除 `pet.chat`）：桌宠隐藏时单击岛必须把桌宠叫回来。

    旧行为：岛按 `hidden_chat` 发 `chat_requested` → `_chat_from_island` →
    `_show_island_chat` 因 `_island_chat_available()` 为假**直接返回**，整次点击
    静默无响应（用户反馈「桌宠隐藏后点灵动岛没反应」，2026-09-23 修复）。
    岛是纯桌宠版隐藏后唯一的常驻交互面，此时单击的合理语义就是「显示桌宠」。
    """
    _qapp()
    shell = _make_shell(tmp_path)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)   # 桌宠已隐藏（隐藏时由 on_hidden 同步）
        shell.enable_chat = False             # 无 chat 的打包变体
        assert shell._island_chat_available() is False

        shell._chat_from_island()

        assert shell.island._pet_visible is True, "点击后桌宠仍未恢复可见"
        assert shell.island_chat is None, "无 chat 变体不该去建对话气泡"
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


def test_shell_show_pets_from_island_chat_restores_the_overlay_window(tmp_path):
    """overlay 拓扑：「显示桌宠」必须真的把桌宠显示回来，不能只改岛的状态。

    旧实现只对 ``instances[].win`` 调 ``win.show()``——overlay 下每只 ``win`` 恒为
    None（PetWindow 不构造），循环一只都命不中，宠物连窗带 sprite 都没恢复，却照样
    把岛标成可见并关掉气泡（岛状态与屏幕事实相反，用户再点一次也无处可恢复）。
    恢复面是唯一合成窗的 ``OverlayShell.set_pet_visible(True)``，本用例用真壳 +
    真合成窗断言它被走到。
    """
    from tests.test_overlay_spawn import _make_persistent_shell

    _qapp()
    shell = _make_shell(tmp_path)
    overlay, _made = _make_persistent_shell(tmp_path / "overlay")
    try:
        shell._sync_dynamic_island()
        shell._overlay_shell = overlay      # overlay 拓扑：恢复面在壳上
        overlay.set_pet_visible(False)      # 桌宠已隐藏（一个可见面都不剩）
        assert overlay.overlay.isVisible() is False
        shell.island.set_pet_visible(False)

        shell._show_island_chat(activate=True)
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()

        shell._show_pets_from_island_chat()
        QApplication.processEvents()

        assert overlay.overlay.isVisible() is True, \
            "overlay 下点「显示桌宠」必须把合成窗恢复可见（旧实现的 win.show() 循环恒空转）"
        assert shell.island._pet_visible is True, "岛状态与实际可见性一致"
        assert not bubble.isVisible()
    finally:
        overlay._delete_runtime_marker()
        _teardown_shell(shell)


def test_shell_show_pets_from_island_chat_restores_per_sprite_hidden_pets(tmp_path):
    """逐只藏光后点「显示桌宠」：每一只 sprite 都要恢复可见，岛状态与屏幕一致。

    ``set_pet_visible(True)`` 的语义是「整窗显隐保留逐只选择」——壳自己的
    ``_set_all_clips_paused`` 恢复侧显式跳过 ``visible`` 为假的 sprite，托盘「显示
    这只」的逐只隐藏不会被整窗显示悄悄解除（那条语义本身是对的）。所以逐只全藏过的
    桌宠在整窗恢复后仍然一只都看不见，岛却被同步成「可见」：屏幕事实与岛状态相反，
    用户再点岛也无处可恢复。恢复入口（岛气泡「显示桌宠」）必须自己把逐只标志补回来。
    """
    from tests.test_overlay_spawn import _make_persistent_shell

    _qapp()
    shell = _make_shell(tmp_path)
    overlay, _made = _make_persistent_shell(tmp_path / "overlay")
    try:
        shell._sync_dynamic_island()
        shell._overlay_shell = overlay
        overlay.spawn_pet()
        overlay.set_pet_visible(True)       # 前提：桌宠当前可见（岛同步为可见）
        sprites = list(overlay.overlay.sprites)
        assert len(sprites) == 2, "前提：主宠 + 一只子肥鱼"
        assert overlay.overlay.isVisible() is True, "前提：合成窗可见"

        for sprite in sprites:              # 逐只全藏（主宠与子宠同等计入）
            overlay.set_sprite_visible(sprite, False)
        assert overlay.overlay.isVisible() is True, "逐只隐藏不动整窗"
        assert shell._aggregate_pet_visible() is False, "一只都不剩：聚合态已是隐藏"
        overlay.set_pet_visible(False)      # 岛同步为隐藏 → 整窗随之收起（真实链路）
        shell.island.set_pet_visible(False)
        assert overlay.overlay.isVisible() is False

        shell._show_island_chat(activate=True)
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()

        shell._show_pets_from_island_chat()
        QApplication.processEvents()

        assert overlay.overlay.isVisible() is True, "整窗必须恢复可见"
        assert all(overlay._sprite_visible(sprite) for sprite in sprites), \
            "逐只全藏后点「显示桌宠」必须把每一只都恢复可见（只见窗不见宠＝屏幕上仍无桌宠）"
        assert shell._aggregate_pet_visible() is True, "聚合可见态与屏幕事实一致"
        assert shell.island._pet_visible is True, "岛状态与屏幕事实一致"
        assert not bubble.isVisible()
    finally:
        # 收口：本用例刻意以「整窗可见」收尾（那正是被测行为），但别把一面可见的全屏
        # 合成窗留给后面的用例（同文件既有用例也都是以隐藏态收口）。
        overlay.set_pet_visible(False)
        overlay._delete_runtime_marker()
        _teardown_shell(shell)


def test_shell_show_pets_from_island_chat_keeps_per_sprite_flags_when_window_hidden(tmp_path):
    """整窗隐藏（未逐只隐藏）后点「显示桌宠」：只恢复窗，一根逐只标志都不碰。

    钉住既有语义的另一半：恢复入口不是"无条件把所有 sprite 标可见"，而是**仅当
    一只可见的都没有**时才补。逐只选择是用户的显式决定（托盘「显示这只」），
    整窗显隐路径不得顺手改它。
    """
    from tests.test_overlay_spawn import _make_persistent_shell

    _qapp()
    shell = _make_shell(tmp_path)
    overlay, _made = _make_persistent_shell(tmp_path / "overlay")
    try:
        shell._sync_dynamic_island()
        shell._overlay_shell = overlay
        overlay.spawn_pet()
        overlay.set_pet_visible(True)       # 前提：桌宠当前可见（岛同步为可见）
        sprites = list(overlay.overlay.sprites)
        assert overlay.overlay.isVisible() is True, "前提：合成窗可见"
        assert all(overlay._sprite_visible(sprite) for sprite in sprites), \
            "前提：两只都可见，没有任何逐只隐藏"

        per_sprite_calls: list = []
        real_set_sprite_visible = overlay.set_sprite_visible

        def spy(sprite, visible):
            per_sprite_calls.append((sprite, visible))
            real_set_sprite_visible(sprite, visible)

        overlay.set_sprite_visible = spy

        overlay.set_pet_visible(False)      # 只关整窗（逐只标志原样保留）
        assert overlay.overlay.isVisible() is False
        shell.island.set_pet_visible(False)

        shell._show_pets_from_island_chat()
        QApplication.processEvents()

        assert overlay.overlay.isVisible() is True, "整窗必须恢复可见"
        assert per_sprite_calls == [], "没有任何一只被逐只藏过：不许去动逐只标志"
        assert all(overlay._sprite_visible(sprite) for sprite in sprites)
        assert shell.island._pet_visible is True
    finally:
        overlay.set_pet_visible(False)      # 同上：不留可见的全屏合成窗给后面的用例
        overlay._delete_runtime_marker()
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


# ------------------------------------------------------ 隐藏期 DSH 反馈改道


def test_island_chat_feedback_preview(tmp_path):
    """反馈气泡：预览式弹出（不抢焦点）、文案/字幕就位、按注入时长收回。"""
    _qapp()
    island = _island(tmp_path)
    bubble = IslandChatBubble(Config(base=tmp_path))
    try:
        bubble.show_feedback(island, "DSH 正在认真想办法……", subtitle="DSH", duration_ms=1234)

        assert bubble.isVisible()
        assert QApplication.activeWindow() is not bubble, "预览式弹出不抢焦点"
        assert bubble._auto_collapse.isActive()
        assert bubble._auto_collapse.interval() == 1234
        assert "认真想办法" in bubble.output.text()
        assert bubble.hint_label.text() == "DSH"
    finally:
        bubble.close()
        bubble.deleteLater()
        _teardown_island(island)


def test_island_feedback_bubble_redirect_gates(tmp_path):
    """AppShell 注入面：桌宠隐藏 + 岛可用 → 反馈弹到岛气泡；桌宠可见 → 拒绝。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        island = _island(tmp_path)
        shell.island = island
        shell.enable_chat = True

        assert shell._island_feedback_bubble(
            "DSH 开始干活啦～", subtitle="DSH", duration_ms=4500) is True
        bubble = shell.island_chat
        assert bubble is not None and bubble.isVisible()
        assert "开始干活啦" in bubble.output.text()

        # 桌宠可见时拒绝改道（返回 False，调用方走原路径）
        shell._aggregate_pet_visible = lambda: True
        assert shell._island_feedback_bubble("第二条") is False
    finally:
        _teardown_shell(shell)


def test_island_feedback_disabled_when_hidden_chat_off(tmp_path):
    """hidden_chat 关：注入面返回 False，维持原丢弃行为（用户显式关闭）。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=False)
    try:
        shell.island = _island(tmp_path, hidden_chat=False)
        shell.enable_chat = True

        assert shell._island_feedback_bubble("DSH 开始干活啦～") is False
        assert shell.island_chat is None
    finally:
        _teardown_shell(shell)


class _BlockingProvider:
    """可控网络替身（只替换网络边界）：请求进来卡住，测试放行后才产出一条回答。"""

    def __init__(self, reply: str = "在的呀，我一直在听") -> None:
        self.reply = reply
        self.started = threading.Event()
        self.release = threading.Event()

    def stream(self, messages, config, cancel):
        self.started.set()
        while not self.release.wait(timeout=0.02):
            if cancel.is_set():
                return
        if cancel.is_set():
            return
        yield self.reply


class _LateProvider:
    """可控网络替身：请求卡住，放行后**一定**产出一条回答（收到 cancel 也不提前收场）。

    与 ``_BlockingProvider`` 的分工：那个收到 cancel 就直接返回，迟到信号根本没被造
    出来；本替身让 worker 在切角色**之前**就 emit 出「增量 + 完成」，投递到 GUI 线程
    留到切换之后，正好验 ID 闸。
    """

    def __init__(self, reply: str = "旧角色的迟到回答") -> None:
        self.reply = reply
        self.started = threading.Event()
        self.release = threading.Event()

    def stream(self, messages, config, cancel):
        self.started.set()
        self.release.wait(timeout=10)
        yield self.reply


def _pump_until(predicate, *, timeout: float = 10.0) -> bool:
    """宽预算轮询 + 事件泵（worker→主线程的信号是队列投递，必须跑事件循环）。"""
    app = _qapp()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _disk_messages(bubble) -> list[dict]:
    """会话**磁盘文件**里的消息（落库真值，不走 pending 快照）。"""
    path = (Path(bubble.config.dir) / "sessions" / bubble.session.character_id
            / f"{bubble.session.session_id}.json")
    return json.loads(path.read_text(encoding="utf-8"))["messages"]


def _send_blocking_question(shell, tmp_path, question: str = "你在吗"):
    """真气泡 + 真 ChatService/QThread + 真会话存储：发一条问题并卡在请求中。"""
    shell._show_island_chat(activate=True)
    bubble = shell.island_chat
    provider = _BlockingProvider()
    bubble.service.provider = provider
    bubble.input.setText(question)
    bubble._send()
    assert provider.started.wait(timeout=10), "前提：请求已进入 provider"
    assert bubble._active_request_id is not None, "前提：请求在途"
    return bubble, provider


def test_island_feedback_yields_to_inflight_request(tmp_path):
    """联动反馈不得打断岛聊天在途请求：AI 回答照常上屏并落库。

    旧行为：``_island_feedback_bubble`` 复用现存气泡直接 ``show_feedback`` →
    ``show_reply`` 把 ``_active_request_id`` 清成 None → 真实 AI 完成回调
    request_id 对不上直接返回，回答既不显示也不落库（用户看到的是被反馈文案
    顶掉的气泡，聊到一半的回答凭空消失）。反馈面必须给在途请求让路：本轮
    反馈丢弃（反馈本身有时效性，排队到回答之后只会送出一条过期状态），
    调用方维持"改道失败即放弃"的原语义。
    """
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        shell.island = _island(tmp_path)
        shell.enable_chat = True
        bubble, provider = _send_blocking_question(shell, tmp_path)

        assert shell._island_feedback_bubble(
            "DSH 正在认真想办法……", subtitle="DSH", duration_ms=4500) is False, \
            "在途请求期间联动反馈必须让路（返回 False 让调用方维持原丢弃行为）"
        assert bubble._active_request_id is not None, "在途请求不得被反馈清掉"

        provider.release.set()
        assert _pump_until(lambda: bubble._active_request_id is None), "回答未回到气泡"

        _doc = QTextDocument()
        _doc.setHtml(bubble.output.text())   # 富文本信封 → 纯文本比对
        assert _doc.toPlainText().rstrip("\n") == provider.reply, "AI 回答必须显示（不是反馈文案）"
        bubble.store.flush()
        roles = [m["role"] for m in _disk_messages(bubble)]
        assert roles == ["user", "assistant"], f"AI 回答必须落库，实际 {roles}"
    finally:
        _teardown_shell(shell)


def test_island_switch_character_drops_inflight_answer(tmp_path):
    """岛气泡继承基类的切角色闸门：旧角色的回答不许落进新角色的会话。

    岛气泡与快速对话气泡共用一套会话链路（``IslandChatBubble`` 继承
    ``QuickChatBubble``），切角色走的就是基类那个 seam——主宠切角色后岛气泡跟在途
    请求之间同样必须作废（否则岛上的旧回答写进新角色会话）。
    """
    _qapp()
    bubble = IslandChatBubble(Config(base=tmp_path))
    try:
        provider = _BlockingProvider()
        bubble.service.provider = provider
        bubble.input.setText("你在吗")
        bubble._send()
        assert provider.started.wait(timeout=10), "前提：请求已进入 provider"

        bubble.switch_character("nova")
        assert bubble._active_request_id is None, "切角色即作废在途请求"

        provider.release.set()
        assert _pump_until(lambda: not bubble.service.busy), "旧 worker 未收尾"
        assert bubble.output.text() == "", "旧角色的回答不许显示在岛上"
        assert not [m for m in bubble.session.messages if m.role == "assistant"]
        assert bubble.session.character_id == "nova"
    finally:
        bubble.close()
        bubble.deleteLater()


def test_island_switch_character_drops_queued_late_delta_and_completion(tmp_path):
    """astra #1：岛气泡同一条——排好队的增量 + 完成信号在切角色后投递必须被丢弃。

    基类那条用例的替身收到 cancel 就直接返回（迟到信号根本没被造出来）；本用例放行后
    先产出一条增量再正常收尾，worker 线程直连打点确认两类信号**已 emit**（此时队列投递
    还挂在 GUI 线程事件队列里），随后才切角色：增量不许上岛、完成不许落进新角色会话。
    """
    _qapp()
    bubble = IslandChatBubble(Config(base=tmp_path))
    try:
        provider = _LateProvider()
        bubble.service.provider = provider
        phase = {"now": "in_flight"}
        forwarded: list[tuple[str, str]] = []
        for name in ("delta", "finished"):
            getattr(bubble.service, name).connect(
                lambda *args, _name=name: forwarded.append((_name, phase["now"])))
        bubble.input.setText("你在吗")
        bubble._send()
        assert provider.started.wait(timeout=10), "前提：请求已进入 provider"

        done = threading.Event()
        bubble.service._worker.finished.connect(lambda: done.set(), Qt.DirectConnection)
        provider.release.set()
        assert done.wait(timeout=10), "前提：worker 已把增量 + 完成 emit 进队列"

        phase["now"] = "after_switch"
        bubble.switch_character("nova")

        delivered = {name for name, _ in forwarded}
        assert _pump_until(lambda: {"delta", "finished"} <= {name for name, _ in forwarded}), \
            f"前提：两类信号必须真的被投递过（实际 {delivered}）"
        assert all(where == "after_switch" for _, where in forwarded), \
            f"前提：投递必须发生在切角色之后（实际 {forwarded}）"
        assert bubble.output.text() == "", "迟到的增量不许上岛"
        assert not [m for m in bubble.session.messages if m.role == "assistant"], \
            "迟到的完成信号不许落进新角色的会话"
        assert bubble.session.character_id == "nova"
    finally:
        bubble.close()
        bubble.deleteLater()


def test_island_feedback_previews_when_request_finished(tmp_path):
    """非回归：请求不在途时反馈照常预览（让路只针对在途请求）。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        shell.island = _island(tmp_path)
        shell.enable_chat = True
        bubble, provider = _send_blocking_question(shell, tmp_path)
        provider.release.set()
        assert _pump_until(lambda: bubble._active_request_id is None)

        assert shell._island_feedback_bubble(
            "DSH 开始干活啦～", subtitle="DSH", duration_ms=4500) is True
        assert "开始干活啦" in bubble.output.text()
        assert bubble.hint_label.text() == "DSH"
    finally:
        _teardown_shell(shell)


# ------------------------------------------------------ 隐藏期联动暂停决策


def test_pause_agent_link_for_hide_decision(tmp_path):
    """隐藏期联动暂停决策：反馈面可用 → 不暂停；不可用/未注入/探针炸 → 照旧暂停。"""
    _qapp()
    from PySide6.QtWidgets import QWidget

    from pet.window_optional_services import WindowFeatureGateMixin

    class _Win(QWidget, WindowFeatureGateMixin):
        pass

    win = _Win()
    pauses = []

    class _Mgr:
        def pause(self):
            pauses.append("pause")

    win.agent_link_manager = _Mgr()

    # 未注入探针（no-chat / 旧接线）：照旧暂停（原省电行为）
    win.pause_agent_link_for_hide()
    assert pauses == ["pause"]

    # 反馈面可用：跳过暂停——隐藏期间岛继续收 DSH 事件驱动反馈气泡
    win.island_feedback_available = lambda: True
    win.pause_agent_link_for_hide()
    assert pauses == ["pause"]

    # 反馈面不可用 / 探针异常：照旧暂停
    win.island_feedback_available = lambda: False
    win.pause_agent_link_for_hide()
    assert pauses == ["pause", "pause"]

    def _boom():
        raise RuntimeError("probe boom")

    win.island_feedback_available = _boom
    win.pause_agent_link_for_hide()
    assert pauses == ["pause", "pause", "pause"]


def test_appshell_island_feedback_available(tmp_path):
    """注入探针透传 _island_chat_available（enable_chat / 岛在 / hidden_chat 门）。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        assert shell._island_feedback_available() is False  # 岛未建
        shell.island = _island(tmp_path)
        shell.enable_chat = True
        assert shell._island_feedback_available() is True
        shell.enable_chat = False
        assert shell._island_feedback_available() is False
    finally:
        _teardown_shell(shell)

# ------------------------------------------------------ 关岛后的运行中行为（R07）
def _disable_island_at_runtime(shell) -> None:
    """运行中关岛：设置页/托盘落盘后由 ``_sync_dynamic_island`` 收口（只 hide）。"""
    cfg = dict(shell.config.get("dynamic_island"))
    cfg["enabled"] = False
    shell.config.set("dynamic_island", cfg)
    shell._sync_dynamic_island()


def test_running_island_disable_retires_chat_availability(tmp_path):
    """运行中关岛：岛对象还在（只 ``hide()``），但它不再是可用的交互面。

    ``_sync_dynamic_island`` 关岛走 ``hide()`` + 摘桥，**不销毁对象**；可用性判定
    若只看"对象有效 + hidden_chat 开"，隐藏桌宠时反馈改道与后台回复仍会锚着岛旧
    位置弹气泡——用户明明关了岛，屏幕上却冒出岛对话孤窗（2026-10-09 审查 R07）。
    """
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        shell._sync_dynamic_island()
        assert shell.island is not None and shell.island.isVisible() is True
        assert shell._island_chat_available() is True

        _disable_island_at_runtime(shell)

        assert shell.island is not None, "关岛只 hide()：对象保留 ≠ 交互面可用"
        assert shell.island.isVisible() is False
        assert shell._island_chat_available() is False
        assert shell._island_feedback_available() is False

        cfg = dict(shell.config.get("dynamic_island"))
        cfg["enabled"] = True
        shell.config.set("dynamic_island", cfg)
        shell._sync_dynamic_island()
        assert shell.island.isVisible() is True
        assert shell._island_chat_available() is True, "再开岛必须恢复可用"
    finally:
        _teardown_shell(shell)


def test_disabled_island_drops_hidden_feedback_and_reply_bubbles(tmp_path):
    """关岛 + 桌宠隐藏：反馈改道与后台回复都不再弹岛气泡（回退各原非岛路径）。"""
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        _disable_island_at_runtime(shell)

        assert shell._island_feedback_bubble("DSH 开始干活啦～") is False
        assert shell.island_chat is None, "关岛后不许再建岛对话气泡（旧位置孤窗）"

        shell._on_global_chat_finished("在的呀")
        assert shell.island_chat is None, "后台回复同样不许弹在隐藏的岛上"
    finally:
        _teardown_shell(shell)


def test_disabled_island_hidden_chat_click_falls_back_to_non_island_route(tmp_path):
    """关岛 + 桌宠隐藏：hidden_chat 的单击路由回退到既有非岛路径（叫回桌宠）。

    岛关着就不该有岛气泡这条路：``chat_requested`` 落到这里时按**既有的 else
    分支语义**处理（与无聊天能力的构建同一条回退链）。
    """
    _qapp()
    shell = _make_shell(tmp_path, hidden_chat=True)
    try:
        shell._sync_dynamic_island()
        shell.island.set_pet_visible(False)
        _disable_island_at_runtime(shell)

        shell._chat_from_island()

        assert shell.island_chat is None, "岛不可用时不弹气泡"
        assert shell.island._pet_visible is True, "回退到既有的「显示桌宠」分支"
    finally:
        _teardown_shell(shell)
