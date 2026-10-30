"""「API 与账户」设置页：服务规格落到 ``api.json``，密钥只走系统钥匙串。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import paths
from ...account.service import AccountError
from ...api.credentials import KeyringCredentials
from ...api.models import ModelListError, list_models
from ...api.presets import PRESETS, preset_for
from ...api.probe import ProbeResult, probe_text
from ...api.spec import ApiService, endpoint_host
from ...api.store import ApiStore
from ...net.errors import describe
from ..external_links import DEEPSEEK_API_URL
from .base import SettingsPage, make_button, make_checkbox, value_field

logger = logging.getLogger("petcore.ui.settings.api")

OWNER = "core.api"
#: 出厂预填的 DeepSeek 官方配置：与主分支 ``ProviderConfig`` 的默认值同一套，
#: 让小白只需要填 API Key。
DEFAULT_NAME = "DeepSeek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CHAT_PATH = "/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_VISION_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_BALANCE = "deepseek"
_BALANCE_CHOICES = (("none", "无"), ("deepseek", "DeepSeek"))
_ERROR_TEXT = {
    "api_endpoint_empty": "Base URL 不能为空",
    "api_endpoint_insecure": "远程地址只允许 https；http 只用于 localhost 或勾选允许的局域网私网",
    "api_endpoint_userinfo": "Base URL 里不能带用户名或密码",
    "api_endpoint_query": "Base URL 里不能带 ? 或 #",
    "api_endpoint_port": "端口号无效",
    "api_endpoint_scheme": "Base URL 必须以 https:// 开头",
    "api_name_invalid": "名称不能为空，且不超过 128 个字符",
    "api_chat_path_invalid": "Chat Path 必须以 / 开头",
    "api_timeout_invalid": "超时必须在 1–300 秒之间",
    "api_balance_protocol_invalid": "余额协议无效",
    "api_models_path_invalid": "模型列表路径必须以 / 开头",
    "api_header_invalid": "自定义请求头格式不对（每行 name: value，值不能为空）",
    "api_header_secret": "请求头里不能放 Authorization/Cookie：密钥只进钥匙串",
    "api_header_too_many": "自定义请求头最多 16 条",
    "unconfigured": "还没有填写地址",
    "auth": "服务需要鉴权",
    "network": "连不上服务",
    "empty": "服务没有返回模型",
}


class _ProbeSignals(QObject):
    finished = Signal(object)


class _BalanceSignals(QObject):
    finished = Signal(object)


class _BalanceWorker(QRunnable):
    def __init__(self, account, service_id: str, signals: _BalanceSignals):
        super().__init__()
        self._account = account
        self._service_id = service_id
        self.signals = signals

    def run(self) -> None:
        try:
            result = self._account.fetch_balance(self._service_id, force=True)
            payload = (True, result)
        except AccountError as exc:
            payload = (False, exc)
        except Exception as exc:  # noqa: BLE001 - UI must receive a stable result
            logger.warning("余额查询异常：%s", type(exc).__name__)
            payload = (False, AccountError("network"))
        try:
            self.signals.finished.emit(payload)
        except RuntimeError:
            pass


class _ProbeWorker(QRunnable):
    """在池线程里跑一次探测；结果（含失败）都收口成一次 ``finished``。"""

    def __init__(self, service: ApiService, api_key: str, signals: _ProbeSignals):
        super().__init__()
        self._service = service
        self._api_key = api_key
        self.signals = signals

    def run(self) -> None:
        try:
            result = probe_text(self._service, self._api_key)
        except Exception as exc:  # noqa: BLE001 - 异常不许穿出 QRunnable
            logger.warning("API 探测异常：%s", type(exc).__name__)
            result = ProbeResult("protocol")
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass


class _ModelSignals(QObject):
    finished = Signal(object)


class _ModelWorker(QRunnable):
    def __init__(self, service: ApiService, api_key: str, signals: _ModelSignals):
        super().__init__()
        self._service = service
        self._api_key = api_key
        self.signals = signals

    def run(self) -> None:
        try:
            models = list_models(self._service, self._api_key)
            payload = (True, models)
        except ModelListError as exc:
            payload = (False, exc.reason)
        except Exception:  # noqa: BLE001 - 异常不许穿出 QRunnable
            payload = (False, "protocol")
        try:
            self.signals.finished.emit(payload)
        except RuntimeError:
            pass


class ApiSettingsPage(SettingsPage):
    title = "API 与账户"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.root = Path(str(getattr(host, "root", None) or paths.base_dir()))
        self.store = getattr(host, "api_store", None) or ApiStore(self.root)
        self.credentials = getattr(host, "api_credentials", None) or KeyringCredentials()
        self._unsubscribe = self.store.subscribe(self._on_store_changed)
        self._loaded: ApiService | None = None
        self._draft_id: str | None = None
        self._baseline: ApiService | None = None
        self._probe_running = False
        self._probe_signals: _ProbeSignals | None = None
        self._balance_running = False
        self._balance_signals: _BalanceSignals | None = None
        self._disposed = False
        self._refreshing = False

        self.add_note(
            "API Key 只写入系统钥匙串，不写 config.json，也不写 api.json；"
            "页面只记住「已保存 / 未保存」，不会回显密钥。"
        )
        self._keyring_note = self.add_note("")

        card = self.section("服务")
        panel = QWidget(self)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(8)
        self.services_list = QListWidget(panel)
        self.services_list.setObjectName("apiServiceList")
        self.services_list.setAccessibleName("API 服务")
        self.services_list.setMinimumWidth(190)
        self.services_list.setMinimumHeight(300)
        self.services_list.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self.services_list, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.add_button = make_button("新增", panel)
        self.add_button.clicked.connect(self.add_service)
        self.remove_button = make_button("删除", panel)
        self.remove_button.clicked.connect(self.remove_service)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        left.addLayout(buttons)
        layout.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)

        self.preset_combo = QComboBox(panel)
        for preset in PRESETS:
            self.preset_combo.addItem(preset.name, preset.id)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self.name_edit = self._line("例如 DeepSeek")
        self.base_url_edit = self._line("https://api.example.com")
        self.chat_path_edit = self._line("/v1/chat/completions")
        self.models_path_edit = self._line("/v1/models")
        self.model_edit = self._line("例如 deepseek-chat")
        self.vision_edit = self._line("可留空；视觉模型名")
        self.model_combo = QComboBox(panel)
        self.model_combo.setMinimumWidth(240)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        self.models_button = make_button("拉取模型", panel)
        self.models_button.clicked.connect(self._fetch_models)
        self.allow_lan_check = make_checkbox(panel)
        self.headers_edit = QPlainTextEdit(panel)
        self.headers_edit.setPlaceholderText("每行一个 name: value；通常留空")
        self.headers_edit.setMaximumHeight(72)
        self.headers_edit.setMinimumWidth(300)
        self.key_edit = self._line("留空表示不修改", password=True)
        self.verify_ssl_check = make_checkbox(panel)
        self.timeout_spin = QSpinBox(panel)
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setMinimumWidth(110)
        self.balance_combo = QComboBox(panel)
        for value, label in _BALANCE_CHOICES:
            self.balance_combo.addItem(label, value)
        self.balance_service_combo = QComboBox(panel)
        self.balance_service_combo.setMinimumWidth(240)
        self.balance_service_combo.currentIndexChanged.connect(self._on_balance_service_changed)
        self.balance_service_hint = QLabel("", panel)
        self.balance_service_hint.setObjectName("settingHint")
        self.balance_service_hint.setWordWrap(True)

        self._form_row(
            form,
            "余额服务",
            self.balance_service_combo,
            "左键点击、右键「显示余额」和灵动岛都用这个服务；选「自动」时取第一个支持余额协议的服务。",
        )
        self._form_row(
            form,
            "点击显示余额",
            QLabel("在「互动与物理 → 点击与音效 → 余额提示」里设置", panel),
            "按宠物保存；峰谷提示的文案与颜色也在那一页。",
        )
        self._form_row(form, "内置预设", self.preset_combo, "选一个填入地址、路径与模型；不会覆盖名称和已保存的 Key。")
        self._form_row(form, "名称", self.name_edit)
        self._form_row(form, "Base URL", self.base_url_edit, "远程地址只允许 https；本机可用 http://127.0.0.1；局域网需勾选下方允许。")
        self._form_row(form, "Chat Path", self.chat_path_edit, "拼在 Base URL 后面，形如 /v1/chat/completions。")
        self._form_row(form, "模型列表路径", self.models_path_edit, "用于「拉取模型」；通常 /v1/models。")
        self._form_row(
            form,
            "模型",
            self.model_edit,
            "聊天与探测使用的模型名；默认 deepseek-v4-flash，如账号不支持可改成 deepseek-chat。",
        )
        model_row = QWidget(panel)
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(8)
        model_layout.addWidget(self.model_combo, 1)
        model_layout.addWidget(self.models_button)
        self._form_row(form, "模型列表", model_row, "从 /v1/models 拉取；选中后填入上面的模型名。")
        self._form_row(form, "视觉模型", self.vision_edit, "识屏等能力使用；留空表示该服务不支持。")
        self._form_row(
            form,
            "允许局域网 http",
            self.allow_lan_check,
            "只对 10./172.16-31./192.168. 私网地址生效；明文传输，确认对端可信。",
        )
        self._form_row(
            form,
            "自定义请求头",
            self.headers_edit,
            "每行 name: value；用于 opencode 等需要额外头的服务。Authorization/Cookie 不允许写在这里。",
        )
        self._form_row(form, "校验 SSL", self.verify_ssl_check, "关闭前请确认对端可信；只影响该服务。")
        self._form_row(form, "超时", self.timeout_spin, "单次请求的上限，1–300 秒。")
        self._form_row(form, "余额协议", self.balance_combo, "决定余额查询走哪套接口；未知服务选「无」。")
        self._form_row(form, "API Key", self.key_edit, "保存后再次打开不会回显；留空表示沿用已保存的密钥。")

        self.api_url_field = value_field(DEEPSEEK_API_URL, panel)
        self.api_url_field.setAccessibleName("DeepSeek 开放平台地址")
        self.open_api_page_button = make_button("打开 DeepSeek 开放平台", panel)
        self.open_api_page_button.clicked.connect(self.open_api_platform)
        api_row = QWidget(panel)
        api_row_layout = QHBoxLayout(api_row)
        api_row_layout.setContentsMargins(0, 0, 0, 0)
        api_row_layout.setSpacing(8)
        api_row_layout.addWidget(self.api_url_field, 1)
        api_row_layout.addWidget(self.open_api_page_button)
        self._form_row(
            form,
            "API 申请",
            api_row,
            "在浏览器打开申请页；本应用不会替你注册、也不会把 Key 发给我们。",
        )
        right.addLayout(form)
        right.addWidget(self.balance_service_hint)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("", panel)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.test_button = make_button("测试连接", panel)
        self.test_button.clicked.connect(self.test_connection)
        self.balance_button = make_button("查询余额", panel)
        self.balance_button.clicked.connect(self.query_balance)
        self.save_button = make_button("保存服务", panel, primary=True)
        self.save_button.clicked.connect(self.save_service)
        status_row.addWidget(self.test_button)
        status_row.addWidget(self.balance_button)
        status_row.addWidget(self.save_button)
        right.addLayout(status_row)
        right.addStretch(1)
        layout.addLayout(right, 1)
        card.add_widget(panel)

        self._refresh_list()
        self._refresh_keyring_note()

    def _line(self, placeholder: str, *, password: bool = False) -> QLineEdit:
        field = QLineEdit(self)
        field.setObjectName("apiField")
        field.setPlaceholderText(placeholder)
        field.setMinimumWidth(300)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setAccessibleName("API Key")
        return field

    def _form_row(self, form: QFormLayout, label: str, control: QWidget, hint: str = "") -> None:
        title = QLabel(label, self)
        title.setObjectName("settingLabel")
        title.setBuddy(control)
        control.setAccessibleName(label)
        if not hint:
            form.addRow(title, control)
            return
        control.setAccessibleDescription(hint)
        wrapper = QWidget(self)
        column = QVBoxLayout(wrapper)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(control)
        note = QLabel(hint, wrapper)
        note.setObjectName("settingHint")
        note.setWordWrap(True)
        column.addWidget(note)
        form.addRow(title, wrapper)

    def _refresh_keyring_note(self) -> None:
        if self.credentials.available():
            self._keyring_note.setText("系统钥匙串可用：保存后密钥由操作系统保管。")
        else:
            self._keyring_note.setText(
                "系统钥匙串不可用：密钥只保存在本进程内存，重启即丢。"
            )

    def _refresh_list(self, select: str | None = None) -> None:
        services = self.store.services()
        target = select or self._current_id() or None
        self.services_list.blockSignals(True)
        self.services_list.clear()
        for service in services:
            item = QListWidgetItem(service.name or service.service_id, self.services_list)
            item.setData(Qt.ItemDataRole.UserRole, service.service_id)
            item.setToolTip(f"{service.service_id} · {service.base_url}")
        row = -1
        if services:
            row = 0
            if target:
                for index, service in enumerate(services):
                    if service.service_id == target:
                        row = index
                        break
            self.services_list.setCurrentRow(row)
        self.services_list.blockSignals(False)
        if row >= 0:
            self._show(services[row])
        else:
            self._start_draft()
        self._sync_buttons()
        self._refresh_balance_services()

    def _refresh_balance_services(self) -> None:
        """余额服务下拉：只列真的带余额协议的服务，外加「自动」。"""
        combo = getattr(self, "balance_service_combo", None)
        if combo is None:
            return
        current = str(self.host.config.get("api_service_id") or "")
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("自动（第一个支持余额的服务）", "")
        for service in self.store.services():
            if str(getattr(service, "balance_protocol", "none") or "none") == "none":
                continue
            combo.addItem(service.name or service.service_id, service.service_id)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        self._refresh_balance_service_hint()

    def _refresh_balance_service_hint(self) -> None:
        label = getattr(self, "balance_service_hint", None)
        if label is None:
            return
        combo = self.balance_service_combo
        chosen = str(combo.currentData() or "")
        if chosen:
            label.setText(f"当前宠物使用「{combo.currentText()}」查询余额。")
            return
        fallback = next(
            (
                service
                for service in self.store.services()
                if str(getattr(service, "balance_protocol", "none") or "none") != "none"
            ),
            None,
        )
        if fallback is None:
            label.setText("还没有服务声明余额协议：先在上面选一个服务，把「余额协议」改成对应口径。")
        else:
            label.setText(f"自动：将使用「{fallback.name or fallback.service_id}」。")

    def _on_balance_service_changed(self, _index: int) -> None:
        if self._refreshing:
            return
        value = str(self.balance_service_combo.currentData() or "")
        self.host.config.set("api_service_id", value)
        self._refresh_balance_service_hint()

    def _current_id(self) -> str:
        if self._draft_id:
            return self._draft_id
        return self._loaded.service_id if self._loaded is not None else ""

    def _selected_id(self) -> str | None:
        item = self.services_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return str(data) if data is not None else None

    def _on_selection_changed(self, row: int) -> None:
        if row < 0:
            return
        service_id = self._selected_id()
        if service_id is None:
            return
        service = self.store.get(service_id)
        if service is None:
            return
        self._show(service)
        self.status_label.clear()
        self._sync_buttons()

    def _show(self, service: ApiService) -> None:
        self._loaded = service
        self._draft_id = None
        self._populate(service)

    def _populate(self, service: ApiService) -> None:
        self.name_edit.setText(service.name)
        self.base_url_edit.setText(service.base_url)
        self.chat_path_edit.setText(service.chat_path)
        self.models_path_edit.setText(service.models_path)
        self.model_edit.setText(service.model)
        self.vision_edit.setText(service.vision_model)
        self.allow_lan_check.setChecked(bool(service.allow_lan_http))
        self.headers_edit.setPlainText(
            "\n".join(f"{key}: {value}" for key, value in service.extra_headers.items())
        )
        preset_index = self.preset_combo.findData(service.preset)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(preset_index if preset_index >= 0 else -1)
        self.preset_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("（先拉取模型列表）", "")
        self.model_combo.blockSignals(False)
        self.verify_ssl_check.setChecked(bool(service.verify_ssl))
        self.timeout_spin.setValue(int(service.timeout))
        self.balance_combo.setCurrentIndex(
            max(0, self.balance_combo.findData(service.balance_protocol))
        )
        self.key_edit.clear()
        self._baseline = self._form_service()

    def _start_draft(self) -> None:
        draft = ApiService(
            service_id=self._next_service_id(),
            name=DEFAULT_NAME,
            base_url=DEFAULT_BASE_URL,
            chat_path=DEFAULT_CHAT_PATH,
            model=DEFAULT_MODEL,
            vision_model=DEFAULT_VISION_MODEL,
            balance_protocol=DEFAULT_BALANCE,
        )
        self._loaded = None
        self._draft_id = draft.service_id
        self._populate(draft)
        self.services_list.blockSignals(True)
        self.services_list.setCurrentRow(-1)
        self.services_list.blockSignals(False)
        self.status_label.setText("已预填 DeepSeek 官方配置：只需填写 API Key 后点「保存服务」。")
        self._sync_buttons()

    def _next_service_id(self) -> str:
        taken = {service.service_id for service in self.store.services()}
        index = 1
        while f"api{index}" in taken:
            index += 1
        return f"api{index}"

    def _parse_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in self.headers_edit.toPlainText().splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if ":" not in text:
                raise ValueError("api_header_invalid")
            name, value = text.split(":", 1)
            name = name.strip()
            value = value.strip()
            if not name or not value:
                raise ValueError("api_header_invalid")
            headers[name] = value
        return headers

    def _form_service(self) -> ApiService:
        return ApiService(
            service_id=self._current_id(),
            name=self.name_edit.text(),
            base_url=self.base_url_edit.text(),
            chat_path=self.chat_path_edit.text() or DEFAULT_CHAT_PATH,
            models_path=self.models_path_edit.text() or "/v1/models",
            model=self.model_edit.text(),
            vision_model=self.vision_edit.text(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            timeout=int(self.timeout_spin.value()),
            balance_protocol=str(self.balance_combo.currentData() or "none"),
            extra_headers=self._parse_headers(),
            allow_lan_http=self.allow_lan_check.isChecked(),
            preset=str(self.preset_combo.currentData() or ""),
        )

    def _on_preset_changed(self, _index: int) -> None:
        preset = preset_for(self.preset_combo.currentData())
        self.base_url_edit.setText(preset.base_url)
        self.chat_path_edit.setText(preset.chat_path)
        self.models_path_edit.setText(preset.models_path)
        self.model_edit.setText(preset.model)
        self.vision_edit.setText(preset.vision_model)
        self.allow_lan_check.setChecked(preset.allow_lan_http)
        self.balance_combo.setCurrentIndex(
            max(0, self.balance_combo.findData(preset.balance_protocol))
        )
        self.status_label.setText(preset.note)

    def _model_key(self, service: ApiService) -> str:
        key = self.key_edit.text().strip()
        if key:
            return key
        try:
            return str(self.credentials.get(OWNER, service.service_id) or "")
        except Exception:  # noqa: BLE001
            return ""

    def _fetch_models(self, *_args) -> None:
        try:
            service = self._form_service().validate()
        except ValueError as exc:
            self.status_label.setText(f"无法拉取：{_error_text(exc)}")
            return
        self.models_button.setEnabled(False)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("正在拉取…", "")
        self.model_combo.blockSignals(False)
        signals = _ModelSignals()
        signals.finished.connect(self._on_models_done)
        self._model_signals = signals
        self.status_label.setText("正在拉取模型列表…")
        QThreadPool.globalInstance().start(
            _ModelWorker(service, self._model_key(service), signals)
        )

    def _on_models_done(self, payload) -> None:
        self.models_button.setEnabled(True)
        ok, value = payload
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not ok:
            self.model_combo.addItem("未检测到模型", "")
            self.model_combo.blockSignals(False)
            self.status_label.setText(f"拉取模型失败：{_error_text(value)}")
            return
        for name in value:
            self.model_combo.addItem(name, name)
        current = self.model_edit.text().strip()
        index = self.model_combo.findData(current) if current else 0
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)
        self.status_label.setText(f"已拉取 {len(value)} 个模型；选中后会填入模型名。")

    def _on_model_selected(self, _index: int) -> None:
        name = str(self.model_combo.currentData() or "")
        if name:
            self.model_edit.setText(name)

    @staticmethod
    def _requires_key(service: ApiService) -> bool:
        host = endpoint_host(service.base_url)
        if host in {"localhost", "127.0.0.1", "::1"}:
            return False
        if host == "opencode.ai" or host.endswith(".opencode.ai"):
            return False
        return True

    def _sync_buttons(self) -> None:
        self.remove_button.setEnabled(self._selected_id() is not None)
        busy = self._probe_running or self._balance_running
        self.test_button.setEnabled(not busy)
        service = self.store.get(self._selected_id() or "")
        self.balance_button.setEnabled(
            not busy
            and service is not None
            and service.balance_protocol != "none"
        )

    def _on_store_changed(self) -> None:
        if self._disposed or self._refreshing:
            return
        self._refreshing = True
        try:
            self._refresh_list()
        finally:
            self._refreshing = False

    def add_service(self, *_args) -> None:
        self._start_draft()

    def open_api_platform(self, *_args) -> None:
        """用系统默认浏览器打开发放平台申请页；本进程不发任何网络请求。"""
        QDesktopServices.openUrl(QUrl(DEEPSEEK_API_URL))

    def remove_service(self, *_args) -> None:
        service_id = self._selected_id()
        if service_id is None:
            self.status_label.setText("先在左侧选择要删除的服务。")
            return
        service = self.store.get(service_id)
        name = service.name if service is not None else service_id
        answer = QMessageBox.question(
            self,
            "删除服务",
            f"确定删除「{name}」吗？对应的 API Key 也会从钥匙串删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.store.remove(service_id)
        except OSError as exc:
            self.status_label.setText(f"删除失败：{type(exc).__name__}")
            return
        if not removed:
            self.status_label.setText("服务已不存在。")
            return
        self.credentials.delete(OWNER, service_id)
        self._loaded = None
        self._draft_id = None
        self._refresh_list()
        self.status_label.setText(f"已删除「{name}」")

    def save_service(self, *_args) -> bool:
        try:
            normalized = self._form_service().validate()
        except ValueError as exc:
            self.status_label.setText(f"无法保存：{_error_text(exc)}")
            return False
        key = self.key_edit.text().strip()
        try:
            saved = self.store.upsert(normalized)
        except OSError as exc:
            self.status_label.setText(f"无法写入 api.json：{type(exc).__name__}")
            return False
        if key:
            self.credentials.set(OWNER, saved.service_id, key)
        self._loaded = saved
        self._draft_id = None
        self._refresh_list(saved.service_id)
        self._refresh_keyring_note()
        message = f"已保存「{saved.name}」"
        if key and not self.credentials.available():
            message += "；密钥只在本进程内存，重启即丢"
        self.status_label.setText(message)
        return True

    def test_connection(self, *_args) -> None:
        if self._probe_running:
            return
        try:
            service = self._form_service().validate()
        except ValueError as exc:
            self.status_label.setText(f"无法测试：{_error_text(exc)}")
            return
        if not service.model:
            self.status_label.setText("无法测试：请先填写模型名。")
            return
        key = self._model_key(service)
        if not key and self._requires_key(service):
            self.status_label.setText("无法测试：请先填写 API Key。")
            return
        self._probe_running = True
        self._sync_buttons()
        self.status_label.setText("正在测试连接…")
        signals = _ProbeSignals()
        signals.finished.connect(self._on_probe_done)
        self._probe_signals = signals
        QThreadPool.globalInstance().start(_ProbeWorker(service, key, signals))

    def query_balance(self, *_args) -> None:
        if self._probe_running or self._balance_running:
            return
        service_id = self._selected_id()
        account = getattr(self.host, "account", None)
        if not service_id or account is None:
            self.status_label.setText("无法查询余额：请先选择已保存的服务。")
            return
        service = self.store.get(service_id)
        if service is None or service.balance_protocol == "none":
            self.status_label.setText("无法查询余额：当前服务未配置余额协议。")
            return
        self._balance_running = True
        self._sync_buttons()
        self.status_label.setText("正在查询余额…")
        signals = _BalanceSignals()
        signals.finished.connect(self._on_balance_done)
        self._balance_signals = signals
        QThreadPool.globalInstance().start(_BalanceWorker(account, service_id, signals))

    def _on_balance_done(self, payload) -> None:
        if self._disposed:
            return
        self._balance_running = False
        self._balance_signals = None
        self._sync_buttons()
        ok, value = payload if isinstance(payload, tuple) and len(payload) == 2 else (False, None)
        if ok:
            self.status_label.setText(value.format_text())
            return
        reason = getattr(value, "reason", "network")
        self.status_label.setText(f"余额查询失败：{describe(reason)}")

    def _on_probe_done(self, result) -> None:
        if self._disposed:
            return
        self._probe_running = False
        self._probe_signals = None
        self._sync_buttons()
        if not isinstance(result, ProbeResult):
            self.status_label.setText("连接失败：内部错误")
            return
        if result.ok:
            text = "连接成功"
        else:
            text = f"连接失败：{describe(result.reason)}"
            host = endpoint_host(self.base_url_edit.text())
            if result.reason in ("rate_limit", "auth") and (
                host == "opencode.ai" or host.endswith(".opencode.ai")
            ):
                text += "；该免费模型仅限 OpenCode 客户端，请订阅 Go 或切换 DeepSeek/本地模型"
        if result.http_status:
            text += f"（HTTP {result.http_status}）"
        if result.retry_after:
            text += f"，{int(result.retry_after)} 秒后再试"
        self.status_label.setText(text)

    def dirty(self) -> bool:
        if bool(self.key_edit.text().strip()):
            return True
        try:
            return self._baseline is None or self._form_service() != self._baseline
        except ValueError:
            return True

    def confirm_save(self) -> bool:
        if not self.dirty():
            return True
        return bool(self.save_service())

    def discard_changes(self) -> None:
        self._loaded = None
        self._draft_id = None
        self._refresh_list()
        self.status_label.clear()

    def dispose(self) -> None:
        self._disposed = True
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

    def keywords(self) -> list[str]:
        return [
            "API",
            "密钥",
            "钥匙串",
            "模型",
            "视觉模型",
            "Base URL",
            "Chat Path",
            "连接测试",
            "余额",
            "点击显示余额",
            "显示余额",
            "左键余额",
            "DeepSeek",
            "开放平台",
            "API 申请",
            "api",
            "key",
            "keyring",
            "token",
            "model",
            "endpoint",
        ]


def _error_text(exc: ValueError) -> str:
    return _ERROR_TEXT.get(str(exc), "请检查填写内容")
