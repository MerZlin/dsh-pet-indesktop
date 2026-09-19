# -*- coding: utf-8 -*-
"""灵动岛对话气泡。

桌宠隐藏后灵动岛是唯一常驻的交互面：点击岛（或 AI 回复到达时）在岛上
弹出对话气泡，发送/流式回复与快速对话气泡共用同一套会话链路
（SessionStore / ChatService / PromptBuilder），只是锚点从桌宠换成岛。

与 QuickChatBubble 的行为差异：

- 锚定岛胶囊几何：默认在岛下方弹出（尾巴朝上指向岛），下方放不下
  （岛停靠屏幕底边）翻转到岛上方；
- 支持两种弹出形态：交互式（激活窗口、聚焦输入框，用户主动点击）与
  预览式（不激活不抢焦点，AI 回复到达时自动弹出，超时自动收回）；
- 预览态若用户点进气泡（窗口激活）则视为开始交互，不再自动收回；
- 附带「显示桌宠」按钮：桌宠隐藏时点击岛弹的是气泡而不是卡片，
  恢复桌宠的入口要留在气泡里。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from .quick_chat import QuickChatBubble

# 预览式弹出的自动收回时长（与展开卡片 _CARD_AUTO_COLLAPSE_MS 同口径）
_AUTO_COLLAPSE_MS = 15_000


class IslandChatBubble(QuickChatBubble):
    """锚定灵动岛的快速对话气泡（桌宠隐藏时的对话代理）。"""

    show_pet_requested = Signal()

    def __init__(self, config):
        super().__init__(config, pet_window=None)
        self._anchor: QWidget | None = None
        # 预览式弹出不激活窗口：显式声明，避免 Windows 上 show() 抢前台焦点
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.title_label.setText("灵动岛对话")

        self.show_pet_btn = QPushButton("显示桌宠")
        self.show_pet_btn.setObjectName("quick-chat-send")
        self.show_pet_btn.clicked.connect(self.show_pet_requested.emit)
        extra_row = QHBoxLayout()
        extra_row.setContentsMargins(0, 0, 0, 0)
        extra_row.addWidget(self.show_pet_btn)
        extra_row.addStretch(1)
        self.layout().insertLayout(self.layout().count() - 1, extra_row)

        self._auto_collapse = QTimer(self)
        self._auto_collapse.setSingleShot(True)
        self._auto_collapse.setInterval(_AUTO_COLLAPSE_MS)
        self._auto_collapse.timeout.connect(self._on_auto_collapse)

    # ------------------------------------------------------------ 弹出
    def show_for_island(self, island: QWidget, *, activate: bool = True,
                        reply_text: str | None = None) -> None:
        """在岛旁弹出：activate=True 交互式（点击触发），False 预览式（回复到达）。"""
        self._anchor = island
        if reply_text is not None:
            self.show_reply(reply_text)
        if activate:
            self._auto_collapse.stop()
        else:
            self._auto_collapse.start()
        self.position_near_pet()
        self.show()
        if activate:
            self.raise_()
            self.activateWindow()
            self.input.setFocus()

    def show_reply(self, text: str) -> None:
        """只展示一条回复：不发送也不落库（落库由发送方负责）。"""
        self._active_request_id = None
        self.hint_label.setText("")
        self._set_reply_text(str(text or ""))
        self._render_reply()

    def _on_auto_collapse(self) -> None:
        # 用户已点进气泡（窗口激活）＝开始交互，不再自动收回
        if QApplication.activeWindow() is not self:
            self.close()

    # ------------------------------------------------------------ 定位
    def position_near_pet(self) -> None:  # noqa: D102
        """按岛几何定位：默认岛下方（尾巴朝上），下方放不下翻到上方。"""
        anchor = self._anchor
        if anchor is None:
            return
        geo = anchor.geometry()
        screen = QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()
        available = (screen.availableGeometry() if screen is not None
                     else QGuiApplication.primaryScreen().availableGeometry())
        self.adjustSize()
        w, h = self.width(), self.height()
        x = geo.center().x() - w // 2
        y = geo.bottom() + 8
        self._tail_up = True
        if y + h > available.bottom() + 1:
            y = geo.top() - h - 8
            self._tail_up = False
        x = max(available.left() + 4, min(x, available.right() - w - 4))
        y = max(available.top() + 4, min(y, available.bottom() - h - 4))
        self.move(QPoint(x, y))
        self.update()
