"""QQ 互通设置页：OneBot HTTP API、webhook 监听、白名单与回复模式。"""

from __future__ import annotations

import secrets

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox

OWNER = "im.qq"


class QqSettingsPage(SettingsPage):
    title = "QQ 互通"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.group_heading("QQ 互通")
        config = self.config()

        self.enabled = make_checkbox(self)
        self.enabled.setChecked(bool(config.get("im_qq_enabled", False)))
        card = self.section("总开关", level=3)
        card.add_row(
            "启用 QQ 互通",
            self.enabled,
            "通过 OneBot v11 网关接 QQ：消息进「QQ 消息」会话，可通知桌宠或自动回复。默认关闭。",
            search_keys=("QQ", "OneBot", "互通", "消息通知"),
        )

        self.api_base = QLineEdit(self)
        self.api_base.setPlaceholderText("http://127.0.0.1:3000")
        self.api_base.setText(str(config.get("im_qq_api_base", "") or ""))
        card_api = self.section("OneBot HTTP API", level=3)
        card_api.add_row(
            "API 地址",
            self.api_base,
            "NapCat / Lagrange 等网关的 HTTP API 根地址；只在本机回环使用。",
            expand=True,
            search_keys=("OneBot", "API 地址", "napcat", "lagrange"),
        )

        self.webhook_port = QSpinBox(self)
        self.webhook_port.setRange(1024, 65535)
        self.webhook_port.setValue(int(config.get("im_qq_webhook_port", 8790) or 8790))
        self.webhook_path = QLineEdit(self)
        self.webhook_path.setText(str(config.get("im_qq_webhook_path", "/onebot") or "/onebot"))
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(self.webhook_port)
        row_layout.addWidget(self.webhook_path, 1)
        card_api.add_row(
            "事件监听",
            row,
            "让网关把 HTTP POST 事件发到这里；token 由桌宠自动生成，配置在网关的 webhook 密钥里。",
            expand=True,
            search_keys=("webhook", "监听", "端口", "事件"),
        )

        self.webhook_token = QLineEdit(self)
        self.webhook_token.setReadOnly(True)
        self.webhook_token.setPlaceholderText("启用后自动生成；点「生成」可换一个新 token")
        token_row = QWidget(self)
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.setSpacing(8)
        token_layout.addWidget(self.webhook_token, 1)
        copy_token = make_button("复制", self)
        copy_token.clicked.connect(self._copy_webhook_token)
        rotate_token = make_button("生成", self)
        rotate_token.clicked.connect(self._rotate_webhook_token)
        token_layout.addWidget(copy_token)
        token_layout.addWidget(rotate_token)
        card_api.add_row(
            "Webhook token",
            token_row,
            "网关 POST 事件时带的 x-webhook-token / Bearer；只存系统钥匙串。生成后需重启桌宠让监听换新 token。",
            expand=True,
            search_keys=("webhook token", "密钥", "复制"),
        )

        self.reply_mode = QComboBox(self)
        self.reply_mode.addItem("只通知，不回复", "notify")
        self.reply_mode.addItem("关闭（只记录）", "off")
        self.reply_mode.addItem("AI 自动回复", "ai")
        index = self.reply_mode.findData(str(config.get("im_qq_reply_mode", "notify") or "notify"))
        self.reply_mode.setCurrentIndex(index if index >= 0 else 0)
        self.notify_bubble = make_checkbox(self)
        self.notify_bubble.setChecked(bool(config.get("im_qq_notify_bubble", True)))
        card_reply = self.section("通知与回复", level=3)
        card_reply.add_row(
            "收到消息时",
            self.reply_mode,
            "AI 自动回复使用「AI 对话」里选的服务、人设与长期记忆；不会把群聊内容发给无关服务。",
            search_keys=("回复模式", "自动回复", "通知"),
        )
        card_reply.add_row(
            "气泡提醒",
            self.notify_bubble,
            "收到 QQ 消息时在桌宠旁冒一条简短气泡；群聊只显示前 120 字。",
        )

        self.allowed_groups = QLineEdit(self)
        self.allowed_groups.setPlaceholderText("群号，逗号分隔；留空 = 不接收群消息")
        self.allowed_groups.setText(self._join(config.get("im_qq_allowed_groups", [])))
        self.allowed_users = QLineEdit(self)
        self.allowed_users.setPlaceholderText("QQ 号，逗号分隔；留空 = 不接收私聊")
        self.allowed_users.setText(self._join(config.get("im_qq_allowed_users", [])))
        card_scope = self.section("白名单", level=3)
        card_scope.add_row(
            "允许的群",
            self.allowed_groups,
            "空 = 群消息全部忽略（避免把陌生群聊带进桌宠）。",
            expand=True,
            search_keys=("群白名单", "群号"),
        )
        card_scope.add_row(
            "允许的私聊",
            self.allowed_users,
            "空 = 私聊全部忽略。",
            expand=True,
            search_keys=("私聊白名单", "QQ 号"),
        )

        self.token = QLineEdit(self)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("留空 = 网关未启用鉴权；已保存的值不会回显")
        self.token_status = QLabel("", self)
        self.token_status.setObjectName("settingHint")
        save_token = make_button("保存访问令牌", self)
        save_token.clicked.connect(self._save_token)
        card_token = self.section("访问令牌", level=3)
        card_token.add_row(
            "OneBot access token",
            self.token,
            "只存系统钥匙串，不写 config.json；保存后即时生效。",
            expand=True,
            search_keys=("token", "令牌", "密钥"),
        )
        card_token.add_row("状态", self.token_status)
        card_token.add_buttons([save_token])

        self.status = QLabel("", self)
        self.status.setObjectName("settingHint")
        self.status.setWordWrap(True)
        card_status = self.section("状态", level=3)
        card_status.add_widget(self.status)

        self._initial = self._snapshot()
        self._on_changed()
        self._refresh_status()
        for widget in (
            self.enabled,
            self.notify_bubble,
        ):
            widget.toggled.connect(self._on_changed)
        self.reply_mode.currentIndexChanged.connect(self._on_changed)
        self.api_base.textChanged.connect(self._on_changed)
        self.webhook_path.textChanged.connect(self._on_changed)
        self.webhook_port.valueChanged.connect(self._on_changed)
        self.allowed_groups.textChanged.connect(self._on_changed)
        self.allowed_users.textChanged.connect(self._on_changed)

    # ---------------------------------------------------------------- 基础

    def config(self):
        return self.host.config

    @staticmethod
    def _join(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return ""

    @staticmethod
    def _split(value: str) -> list[str]:
        import re

        return [
            item.strip()
            for item in re.split(r"[,，\s]+", str(value or ""))
            if item.strip()
        ]

    def _snapshot(self) -> dict:
        return {
            "enabled": bool(self.enabled.isChecked()),
            "api_base": self.api_base.text().strip(),
            "webhook_port": int(self.webhook_port.value()),
            "webhook_path": self.webhook_path.text().strip() or "/onebot",
            "reply_mode": str(self.reply_mode.currentData() or "notify"),
            "notify_bubble": bool(self.notify_bubble.isChecked()),
            "allowed_groups": self._join(self._split(self.allowed_groups.text())),
            "allowed_users": self._join(self._split(self.allowed_users.text())),
        }

    def dirty(self) -> bool:
        return self._snapshot() != self._initial

    def confirm_save(self) -> None:
        self._on_changed()
        self._initial = self._snapshot()

    def discard_changes(self) -> None:
        values = dict(self._initial)
        widgets = (
            (self.enabled, values["enabled"]),
            (self.notify_bubble, values["notify_bubble"]),
        )
        for widget, value in widgets:
            widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(False)
        for widget, value in (
            (self.api_base, values["api_base"]),
            (self.webhook_path, values["webhook_path"]),
            (self.allowed_groups, values["allowed_groups"]),
            (self.allowed_users, values["allowed_users"]),
        ):
            widget.blockSignals(True)
            widget.setText(str(value))
            widget.blockSignals(False)
        self.webhook_port.blockSignals(True)
        self.webhook_port.setValue(int(values["webhook_port"]))
        self.webhook_port.blockSignals(False)
        index = self.reply_mode.findData(values["reply_mode"])
        self.reply_mode.blockSignals(True)
        self.reply_mode.setCurrentIndex(index if index >= 0 else 0)
        self.reply_mode.blockSignals(False)
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        values = self._snapshot()
        config = self.config()
        config.set("im_qq_enabled", values["enabled"])
        config.set("im_qq_api_base", values["api_base"])
        config.set("im_qq_webhook_port", values["webhook_port"])
        config.set("im_qq_webhook_path", values["webhook_path"])
        config.set("im_qq_reply_mode", values["reply_mode"])
        config.set("im_qq_notify_bubble", values["notify_bubble"])
        config.set("im_qq_allowed_groups", self._split(values["allowed_groups"]))
        config.set("im_qq_allowed_users", self._split(values["allowed_users"]))
        self.changed.emit()

    # ---------------------------------------------------------------- 令牌

    def _credentials(self):
        return getattr(self.host, "credentials", None)

    def _save_token(self) -> None:
        credentials = self._credentials()
        if credentials is None:
            self.token_status.setText("钥匙串不可用，无法保存令牌")
            return
        value = self.token.text()
        try:
            if value:
                credentials.set(OWNER, "access_token", value)
            else:
                credentials.delete(OWNER, "access_token")
        except Exception:
            self.token_status.setText("令牌保存失败")
            return
        self.token.clear()
        self._refresh_status()

    def _refresh_status(self) -> None:
        credentials = self._credentials()
        configured = False
        webhook_token = ""
        if credentials is not None:
            try:
                configured = bool(credentials.get(OWNER, "access_token"))
                webhook_token = str(credentials.get(OWNER, "webhook_token") or "")
            except Exception:
                configured = False
                webhook_token = ""
        self.token_status.setText("已配置" if configured else "未配置")
        if hasattr(self, "webhook_token"):
            self.webhook_token.setText(webhook_token)
        self.status.setText(
            "监听地址：http://127.0.0.1:"
            f"{self.webhook_port.value()}{self.webhook_path.text().strip() or '/onebot'}"
            + ("" if webhook_token else "（启用后自动生成 token）")
        )

    def _copy_webhook_token(self) -> None:
        token = self.webhook_token.text().strip() if hasattr(self, "webhook_token") else ""
        if token:
            QApplication.clipboard().setText(token)
            self.status.setText("webhook token 已复制到剪贴板。")

    def _rotate_webhook_token(self) -> None:
        credentials = self._credentials()
        if credentials is None:
            self.status.setText("钥匙串不可用，无法生成 webhook token")
            return
        value = secrets.token_hex(16)
        try:
            credentials.set(OWNER, "webhook_token", value)
        except Exception:
            self.status.setText("webhook token 保存失败")
            return
        self._refresh_status()
        self.status.setText("已生成新 webhook token；重启桌宠后监听生效。")

    def keywords(self) -> list[str]:
        return ["QQ", "OneBot", "互通", "群消息", "私聊", "webhook", "napcat", "lagrange"]
