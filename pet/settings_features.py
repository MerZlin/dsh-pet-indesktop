# -*- coding: utf-8 -*-
"""设置页「功能」——功能模块的启用/停用卡片（**重启后生效**）。

**为什么单独成模块**：``modern_settings_dialog.py`` 有硬行数预算且明文规定
「再往上帝类里塞新页面时只许降不涨」。对话框里只留接线（构造 + 保存委托），
页面本体在这里（与 ``character_packs_page`` 同规）。

**staged 语义**：卡片上的开关只改内存里的 pending 集，不碰配置；对话框的
``_write_config`` 在统一 save 之前调 :meth:`FeaturesPage.write_to_config`，
由它把 pending 集与盘上「下次启动」值（``feature_activation.pending_enabled``）
比较——不同才 ``config.set``，复用统一保存链。取消/关窗不保存即丢弃，
不存在「即点即写」。本次运行的功能不受本页影响（激活快照在启动早期冻结），
页面上如实写「重启后生效」。

**卡片不是 SettingRow**：启用/停用不是逐键偏好而是整模块的激活选择（一个键装
14 个模块的集合），套 SettingRow 会被「每个设置行必须归属某个域」的契约当成
漏登记的设置项——与角色包卡片的 ``_InfoRow`` 同一理由。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from .feature_activation import CURRENT_VERSION, pending_enabled
from .features import (CATEGORY_ORDER, FEATURES, all_features,
                       normalize_enabled, resolve_dependencies)
from .settings_widgets import SettingsSection, ToggleSwitch

log = logging.getLogger(__name__)

#: 「推荐套件」：一键勾上的模块（纯 UI 捷径，改的就是 pending 集）。
RECOMMENDED_SUITE: tuple[str, ...] = (
    "weather", "todo", "divination", "festival", "voice", "chat",
)

#: 顶部说明条（如实写：本页改动重启才生效，本次运行不受影响）。
INFO_LINE = "勾选的功能重启桌宠后生效；没勾的功能不会出现菜单和设置，后台也不会运行。"
RESTART_NOTE = "重启后生效：本页的改动只写进配置，重启桌宠后才生效；当前正在运行的功能不受影响。"
CORE_NOTE = ("始终开启的核心能力（不可关闭）：动画播放与素材角色、拖拽/点击/碰撞/弹弓互动、"
             "自言自语（文字气泡）、多桌宠、全局快捷键、菜单自定义、设置与更新。"
             "直播捕获与跨屏是既有设置项，以卡片形式列在下方（可关闭）。")


def _badge_text(spec) -> str:
    badges = []
    if spec.needs_network:
        badges.append("联网")
    if spec.needs_paid:
        badges.append("可能要花钱/凭据")
    if spec.needs_external_process:
        badges.append("会拉起外部程序")
    return " · ".join(badges)


class FeatureCard(QFrame):
    """一个功能模块一张卡（复用 ``settingsCard`` 样式令牌）。

    布局：左 名称+徽标 / 一句话说明 / 依赖提示行，右侧开关。
    ``label`` / ``hint_label`` 与 SettingRow 同形暴露（搜索/可访问性的既有口径）。
    """

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setObjectName(f"featureCard_{spec.id}")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.label = QLabel(spec.name, self)
        self.label.setObjectName("settingLabel")
        head.addWidget(self.label, 0)
        badge = _badge_text(spec)
        if badge:
            badge_label = QLabel(badge, self)
            badge_label.setObjectName("settingHint")
            head.addWidget(badge_label, 0)
        head.addStretch(1)
        main.addLayout(head)
        self.hint_label = QLabel(spec.desc, self)
        self.hint_label.setObjectName("settingHint")
        self.hint_label.setWordWrap(True)
        main.addWidget(self.hint_label)
        if spec.entry_names:
            # 「对应：」行——关掉这张卡后具体哪些东西会消失（用户词汇，不列动作 id）
            entries = QLabel("对应：" + "、".join(spec.entry_names), self)
            entries.setObjectName("settingHint")
            entries.setWordWrap(True)
            main.addWidget(entries)
        self.dep_hint = QLabel("", self)
        self.dep_hint.setObjectName("settingHint")
        self.dep_hint.setWordWrap(True)
        self.dep_hint.setVisible(False)
        main.addWidget(self.dep_hint)
        outer.addLayout(main, 1)

        self.toggle = ToggleSwitch(self)
        self.toggle.setObjectName(f"featureToggle_{spec.id}")
        self.toggle.setAccessibleName(f"启用{spec.name}")
        outer.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)

    def set_dep_hint(self, text: str) -> None:
        self.dep_hint.setText(text)
        self.dep_hint.setVisible(bool(text))


class FeaturesPage(QWidget):
    """「功能」域内容：说明条 + 快捷按钮 + 按分组的功能卡片。"""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.setObjectName("settingsFeaturesPage")
        self._dialog = dialog
        self._pending: list[str] = list(pending_enabled(dialog.config))
        self._cards: dict[str, FeatureCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        info = QFrame(self)
        info.setObjectName("settingsCard")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(6)
        info_line = QLabel(INFO_LINE, info)
        info_line.setObjectName("settingLabel")
        info_line.setWordWrap(True)
        info_layout.addWidget(info_line)
        restart = QLabel(RESTART_NOTE, info)
        restart.setObjectName("settingHint")
        restart.setWordWrap(True)
        info_layout.addWidget(restart)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for text, slot, name in (
            ("推荐套件", self._apply_recommended, "featurePresetRecommended"),
            ("全部开启", self._enable_all, "featureEnableAll"),
            ("全部关闭", self._disable_all, "featureDisableAll"),
        ):
            button = QPushButton(text, info)
            button.setObjectName(name)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(slot)
            buttons.addWidget(button, 0)
        buttons.addStretch(1)
        info_layout.addLayout(buttons)
        layout.addWidget(info)

        by_category: dict[str, list] = {}
        for spec in all_features():
            card = FeatureCard(spec, self)
            card.toggle.setChecked(spec.id in self._pending)
            card.toggle.toggled.connect(
                lambda checked, fid=spec.id: self._on_card_toggled(fid, checked))
            self._cards[spec.id] = card
            by_category.setdefault(spec.category, []).append(card)
        # 组顺序跟随 all_features() 的分组序；未登记的新分组排到末尾，不静默丢弃
        # （与 features._sort_key 同口径）。
        ordered = [c for c in CATEGORY_ORDER if c in by_category]
        ordered += [c for c in by_category if c not in CATEGORY_ORDER]
        for category in ordered:
            layout.addWidget(SettingsSection(category, by_category[category], self))

        # 核心能力说明区：让用户知道「不拆的是哪些」（不可关闭，仅供说明）
        core = QLabel(CORE_NOTE, self)
        core.setObjectName("settingHint")
        core.setWordWrap(True)
        layout.addWidget(core)

        layout.addStretch(1)
        self._refresh_dep_hints()

    # ------------------------------------------------------------ pending 集
    def pending(self) -> list[str]:
        """当前内存里的「下次启动」启用集（未落盘）。"""
        return list(self._pending)

    def _on_card_toggled(self, feature_id: str, checked: bool) -> None:
        if checked and feature_id not in self._pending:
            self._pending.append(feature_id)
        elif not checked and feature_id in self._pending:
            self._pending.remove(feature_id)
        self._refresh_dep_hints()

    def _set_pending(self, enabled: list[str]) -> None:
        self._pending = [fid for fid in enabled if fid in FEATURES]
        for fid, card in self._cards.items():
            card.toggle.blockSignals(True)
            card.toggle.setChecked(fid in self._pending)
            card.toggle.blockSignals(False)
        self._refresh_dep_hints()

    def _apply_recommended(self) -> None:
        self._set_pending(list(RECOMMENDED_SUITE))

    def _enable_all(self) -> None:
        self._set_pending([spec.id for spec in all_features()])

    def _disable_all(self) -> None:
        self._set_pending([])

    # ------------------------------------------------------------ 依赖提示
    def _refresh_dep_hints(self) -> None:
        """如实显示硬依赖：被勾选的功能缺谁，就在**被依赖者**的卡下挂一行小字。

        保存不强制拦（blocked 模块运行时自然不可用），但 UI 不许装没看见。
        """
        blocked = resolve_dependencies(self._pending).blocked
        for fid, card in self._cards.items():
            dependents = [FEATURES[other].name
                          for other, missing in blocked.items() if fid in missing]
            text = "、".join(f"「{name}」" for name in dependents) + "需要它才能用" \
                if dependents else ""
            card.set_dep_hint(text)

    # ------------------------------------------------------------ 保存
    def write_to_config(self, config) -> None:
        """把 pending 集写进 ``config``（**不自己 save**，复用对话框的统一保存链）。

        有改动才 ``config.set``（未改动不碰——避免无谓写盘触发主进程 watcher）。
        盘上已判定的未知模块 ID（降级/再升级场景）原样保留在启用集末尾
        （SPEC §4「未知 ID 不执行但保留」）；形状损坏的值在用户**未改动**时一字不动，
        用户改了就按用户的明确选择整值提交。
        """
        if set(self._pending) == set(pending_enabled(config)):
            return
        unknown: list[str] = []
        raw = config.data.get("feature_activation")
        if isinstance(raw, dict) and isinstance(raw.get("enabled"), list):
            try:
                _known, unknown = normalize_enabled(raw["enabled"])
            except ValueError:
                # 元素级损坏：用户的明确选择覆盖损坏值（等价于 write_enabled 的口径）
                log.info("功能模块激活：启用列表元素损坏，按功能页的明确选择重写")
                unknown = []
        enabled = list(dict.fromkeys([*self._pending, *unknown]))
        config.set("feature_activation", {"version": CURRENT_VERSION, "enabled": enabled})


def build_content(dialog) -> QWidget:
    """构造「功能」页内容（由设置对话框调用，保存走对话框的统一写回）。"""
    return FeaturesPage(dialog)
