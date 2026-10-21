# -*- coding: utf-8 -*-
"""节日提醒设置页的折叠编排 + 域卡片构建：常用项平铺，少用/高级项折成一行。

机主原话（2026-10-13，同日先验收语音页整理）：「**节日提醒页面也修整一下**」——
照语音页那套口径做：常用项平铺，少用项折起来（可展开、默认收起），文案一句话。

**为什么折叠必须做在「行」上，而不是容器嵌套**（与 ``voice_chime_tts_panel`` 同一
条教训）：设置页的行会被 ``modern_settings_dialog._rebuild_domain_navigation`` 收进
「语音」域的**一张平卡**，那一步只搬 ``SettingRow``、不搬容器——任何 QWidget 嵌套
到那里都会散架（容器留下，行被搬走）。所以折叠只能是一个折叠头行 + ``setVisible``
管成员行。

**为什么域卡片由本模块构建**（``build_domain_page``）：本页在设置对话框里不作为一个
独立页面出现——它的行整体搬进「语音」域。若让对话框侧现造卡片（``page_content``），
本页的四段分组标题会跟着外面的容器一起散架，整块只剩一张无标题的长卡。这里
按 ``settings_interaction`` / ``settings_file_interpret`` 的先例，把**域卡片连同
分段标题一次性建好并交给对话框**：分组标题（何时提醒 / 提醒哪些日子 / 怎么提醒 /
生日 / 少用项）于是和行一起存活。对话框侧只剩一行调用。

行序：卡片行序即呈现顺序（域构建期现造），折叠编排只按既有位置把整块摆正一次。
``SettingsCard.refresh_separators`` 只认 ``rows`` 的先后，所以布局与 ``rows`` 必须
一起改，否则分隔线会挂到别的行上。

本模块只碰可见性、行序与分组标题：**配置键、读写、调度语义、动画/音效链路一行没动**
（都在 ``pet/festival_settings.py`` 与 ``pet/festival_service.py`` 里）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ..settings_widgets import SettingRow, SettingsCard, SettingsSection

#: 折叠头（少用项）的行 key 与文案
FOLD_KEY = "festival_fold_rare"
FOLD_TITLE = "自定义文案（折叠）"
FOLD_HINT = "点「展开」自定义祝福语；默认用内置库。"
#: 折叠头的动作文案（与语音页同一套字样）
EXPAND_TEXT = "展开"
COLLAPSE_TEXT = "收起"


def notice_row(key: str, title: str, hint: str, control: QWidget | None = None) -> SettingRow:
    """折叠头行：右侧放展开/收起按钮；不传控件时留零尺寸占位（纯文案行）。"""
    stub = control if control is not None else QWidget()
    if control is None:
        stub.setFixedSize(0, 0)
        stub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return SettingRow(key, title, hint, stub)


class FestivalFoldPanel:
    """把少用项收进一个折叠头行，并把整块行序摆成「常用在前」的顺序。

    设置页侧的接线只有：``set_header()`` / ``add_flat()`` / ``add_folded()``，然后
    ``build_domain_page()`` 建域卡片、``apply()`` 摆正行序与显隐。
    """

    def __init__(self) -> None:
        self._flat: list[SettingRow] = []
        self._folded: list[SettingRow] = []
        self._header: SettingRow | None = None
        self._button: QPushButton | None = None
        self.open = False

    # ------------------------------------------------------------ 构建
    def set_header(self, row: SettingRow, button: QPushButton) -> None:
        """登记折叠头行与它的展开/收起按钮（位置由 ``apply()`` 决定）。"""
        self._header = row
        self._button = button
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: self.toggle())

    def add_flat(self, row: SettingRow) -> None:
        """登记一个平铺行（常用项，永远可见）。"""
        self._flat.append(row)

    def add_folded(self, row: SettingRow) -> None:
        """登记一个折叠成员行；搜索命中它时经 ``_reveal_fold`` 自动展开。"""
        self._folded.append(row)
        # 搜索命中折叠里的行时要把这一行露出来（见 settings_widgets.reveal_settings_row）
        row._reveal_fold = self.expand

    # ------------------------------------------------------------ 编排
    def rows(self) -> list[SettingRow]:
        """整块行，顺序即呈现顺序：平铺 → 折叠头 → 折叠成员。"""
        rows = list(self._flat)
        if self._header is not None:
            rows.append(self._header)
        rows.extend(self._folded)
        return rows

    def row_map(self) -> dict[str, SettingRow]:
        """整块行按 key 索引（行在域构建前没有 parent，``findChildren`` 找不到它们）。"""
        return {
            row.objectName().removeprefix("settingRow_"): row for row in self.rows()
        }

    def build_domain_page(self, host: QWidget, groups) -> QWidget:
        """建「语音」域里的节日提醒页：一张平卡 + 分组标题，行序即本模块的顺序。

        ``groups`` 是 ``[(分组标题, [平铺行...]), ...]``；折叠成员行不进分组，
        由 ``apply()`` 摆到折叠头之后（它们默认收起，标题由折叠头自己承担）。
        """
        content = QWidget(host)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        for title, rows in groups:
            rows = [row for row in rows if row is not None]
            if not rows:
                continue
            layout.addWidget(SettingsSection(title, rows, content))
        # 折叠头与成员行跟在常用项之后（`apply()` 会按 rows() 把整块摆正）
        tail: list[SettingRow] = []
        if self._header is not None:
            tail.append(self._header)
        tail.extend(self._folded)
        if tail:
            layout.addWidget(SettingsSection("少用项", tail, content))
        layout.addStretch(1)
        return content

    @staticmethod
    def card_of(page: QWidget) -> SettingsCard:
        """域页里的折叠卡片（收着折叠头与少用项那一张；用例与宿主都按它取）。"""
        for card in page.findChildren(SettingsCard):
            if any(row.objectName() == f"settingRow_{FOLD_KEY}" for row in card.rows):
                return card
        raise AssertionError("域页里找不到节日提醒的折叠卡片")

    def apply(self, card: SettingsCard | None = None) -> None:
        """同步显隐；给了卡片就顺带把整块按既有位置摆正（域构建前后各调一次）。"""
        if card is None:
            card = self._resolve_card()
        if card is not None:
            self._reorder(card)
        self._sync_visibility()
        if card is not None:
            card.refresh_separators()

    def toggle(self) -> None:
        """展开/收起少用项。"""
        self.open = not self.open
        self.apply()

    def set_open(self, opened: bool) -> None:
        self.open = bool(opened)
        self.apply()

    def is_open(self) -> bool:
        return bool(self.open)

    def expand(self) -> None:
        """展开少用项（搜索命中折叠内的行时由 ``reveal_settings_row`` 调用）。"""
        if not self.open:
            self.set_open(True)

    # ------------------------------------------------------------ 内部
    def _resolve_card(self) -> SettingsCard | None:
        """行当前的宿主平卡：认**装得下整块行**的那张，域构建中途返回 None。"""
        block = set(self.rows())
        for row in self.rows():
            card = row.parentWidget()
            if isinstance(card, SettingsCard) and block <= set(getattr(card, "rows", ())):
                return card
        return None

    def _sync_visibility(self) -> None:
        if self._button is not None:
            self._button.setText(COLLAPSE_TEXT if self.open else EXPAND_TEXT)
        for row in self._flat:
            row.setVisible(True)
        for row in self._folded:
            row.setVisible(self.open)

    def _reorder(self, card: SettingsCard) -> None:
        """按呈现顺序把整块行填回它们在平卡里原本占的槽位（块内重排，不挪别的设置）。"""
        block = self.rows()
        existing = list(getattr(card, "rows", ()))
        positions = [index for index, row in enumerate(existing) if row in block]
        if len(positions) != len(block):
            # 平卡里还没搬齐这些行（构建中途）：原样不动，绝不按猜的索引搬家。
            return
        first = positions[0]
        if positions != list(range(first, first + len(positions))):
            # 行不连续：插空重排会打乱别的设置，宁可不动
            return
        # 行在布局里的下标是它在 rows 里的两倍（中间夹着分隔线）：把整块原有位置
        # 当作槽位，按呈现顺序把行填回槽位，间隔的分隔线各自归位。
        layout = card.layout()
        for row in block:
            layout.removeWidget(row)
        for slot, row in zip(positions, block):
            layout.insertWidget(2 * slot, row)
        reordered = list(existing)
        for slot, row in zip(positions, block):
            reordered[slot] = row
        card.rows = reordered
