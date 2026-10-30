"""语音设置页：后端链、音色/语速/音量、本地部署模型、密钥与缓存。

后端声明仍以 :mod:`petcore.net.tts` 的 ``PROVIDER_SPECS`` 为唯一真相源；
本页只做「读配置 → 控件 → 差异写回」。密钥只进系统钥匙串（owner
``speech.<provider>``），页面从不回显已保存的值。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ...contracts.speech import SpeechRequest
from ...net.errors import describe as describe_net_error
from ...net.tts import PROVIDER_SPECS
from .base import (
    SettingsPage,
    make_button,
    make_checkbox,
    make_slider,
    make_spin,
    value_field,
)

logger = logging.getLogger("petcore.ui.settings.voice")

#: 后端 id → 音色配置键（同一个控件写不同后端的键）。
VOICE_KEYS = {
    "edge": "speech_voice",
    "sapi": "speech_sapi_voice",
    "doubao": "speech_doubao_voice",
    "mimo": "speech_mimo_voice",
    "local": "speech_local_voice",
}

#: 页面状态键 → 配置键（用于差异写回与回滚）。
CONFIG_KEYS = {
    "backend": "speech_backend",
    "fallback": "speech_fallback",
    "rate": "speech_rate",
    "volume": "speech_volume",
    "cache_limit": "speech_cache_limit",
    "local_endpoint": "speech_local_endpoint",
    "local_protocol": "speech_local_protocol",
    "local_path": "speech_local_path",
    "local_model": "speech_local_model",
    "local_format": "speech_local_format",
    "local_timeout": "speech_local_timeout",
    "asr_enabled": "asr_enabled",
    "asr_engine": "asr_engine",
    "asr_endpoint": "asr_endpoint",
    "asr_model": "asr_model",
    "asr_language": "asr_language",
    "asr_auto_send": "asr_auto_send",
    "asr_max_seconds": "asr_max_seconds",
    "asr_timeout": "asr_timeout",
    "voice_edge": "speech_voice",
    "voice_sapi": "speech_sapi_voice",
    "voice_doubao": "speech_doubao_voice",
    "voice_mimo": "speech_mimo_voice",
    "voice_local": "speech_local_voice",
}

REASON_TEXT = {
    "config": "未配置",
    "auth": "需密钥",
    "unavailable": "不可用",
    "network": "网络不可达",
    "timeout": "连接超时",
    "rate_limit": "请求过于频繁",
    "protocol": "服务端响应异常",
    "cache": "缓存不可用",
}

PREVIEW_TEXT = "你好，我是桌面上的小家伙。语音合成已经准备好了。"
PREVIEW_OWNER = "core.settings.voice"


def _rate_text(value: float) -> str:
    return f"{float(value):.2f}×"


def _volume_text(value: float) -> str:
    return f"{round(float(value) * 100)}%"


class VoicePage(SettingsPage):
    title = "语音"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._providers = self._provider_infos()
        self._building = True
        self._build_backend()
        self._build_local()
        self._build_asr()
        self._build_secrets()
        self._build_cache()
        self.add_note(
            "所有会说话的功能（报时、抽签、节日、待办等扩展）默认沿用这里的后端与音色；"
            "各功能自己的播报开关仍在其设置页里。"
        )
        self._initial = self._snapshot()
        self._written = dict(self._initial)
        self._building = False
        self._sync_voice_options()
        self._refresh_secret_statuses()

    # ------------------------------------------------------------ 构造
    def config(self):
        return self.host.config

    def _service(self):
        return getattr(self.host, "speech", None)

    def _port(self):
        ports = getattr(self.host, "ports", None)
        return getattr(ports, "speech", None) if ports is not None else None

    def _provider_infos(self) -> list[tuple[str, str, bool, str]]:
        """``(id, label, available, reason)``；服务不可用时退回静态声明。"""
        service = self._service()
        if service is not None:
            try:
                return [
                    (info.id, info.label, bool(info.available), str(info.reason or ""))
                    for info in service.providers()
                ]
            except Exception:  # noqa: BLE001 - 设置页不能因为探测失败打不开
                logger.warning("speech providers probe failed", exc_info=True)
        return [
            (spec.id, spec.label, True, "") for spec in PROVIDER_SPECS.values()
        ]

    def _provider_label(self, provider_id: str, label: str, available: bool, reason: str) -> str:
        if available:
            return label
        suffix = REASON_TEXT.get(reason, "不可用")
        return f"{label} · {suffix}"

    def _build_backend(self) -> None:
        card = self.section("语音后端")
        self.backend_combo = QComboBox(self)
        for provider_id, label, available, reason in self._providers:
            self.backend_combo.addItem(
                self._provider_label(provider_id, label, available, reason), provider_id
            )
        self._select(self.backend_combo, str(self.config().get("speech_backend") or "edge"))
        card.add_row(
            "主后端",
            self.backend_combo,
            "合成失败会按「回退后端」再试一次；本机离线时 Windows 本地语音最快。",
        )

        self.fallback_combo = QComboBox(self)
        self.fallback_combo.addItem("不回退", "")
        for provider_id, label, available, reason in self._providers:
            self.fallback_combo.addItem(
                self._provider_label(provider_id, label, available, reason), provider_id
            )
        self._select(self.fallback_combo, str(self.config().get("speech_fallback") or ""))
        card.add_row(
            "回退后端",
            self.fallback_combo,
            "主后端未安装、未配置或缺少凭据时使用；已选后端合成失败只会在它内部重试，不会自动换云端。",
        )

        self.voice_combo = QComboBox(self)
        self.voice_combo.setEditable(True)
        self.voice_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.voice_combo.lineEdit().setPlaceholderText("留空 = 默认音色")
        voice_row = QWidget(self)
        voice_layout = QHBoxLayout(voice_row)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(8)
        voice_layout.addWidget(self.voice_combo, 1)
        self.voice_value_label = QLabel("", voice_row)
        self.voice_value_label.setObjectName("settingHint")
        voice_layout.addWidget(self.voice_value_label)
        card.add_row(
            "音色",
            voice_row,
            "在线后端可从列表选，也可以直接填自定义音色名；本地模型的音色名在本地服务里定义。",
            expand=True,
        )

        self.rate_slider, self.rate_label = self._slider_row(
            card,
            "语速",
            50,
            200,
            lambda: round(float(self.config().get("speech_rate") or 1.0) * 100),
            _rate_text,
            "0.50×–2.00×。",
        )
        self.volume_slider, self.volume_label = self._slider_row(
            card,
            "音量",
            0,
            100,
            lambda: round(float(self.config().get("speech_volume") or 1.0) * 100),
            _volume_text,
            "只影响语音播报，不影响点击音效与音乐。",
        )

        self.cache_limit_spin = make_spin(self)
        self.cache_limit_spin.setRange(10, 2000)
        self.cache_limit_spin.setSuffix(" 个")
        self.cache_limit_spin.setValue(int(self.config().get("speech_cache_limit") or 200))
        card.add_row(
            "缓存上限",
            self.cache_limit_spin,
            "按最近使用保留合成好的音频；超出后删除最旧的文件。",
        )

        status_row = QWidget(self)
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self.status_label = QLabel("尚未试听", status_row)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label, 1)
        self.preview_button = make_button("试听", status_row)
        self.preview_button.clicked.connect(self._preview)
        status_layout.addWidget(self.preview_button)
        card.add_row("试听", status_row, expand=True)

        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        self.fallback_combo.currentIndexChanged.connect(self._on_changed)
        self.voice_combo.currentTextChanged.connect(self._on_voice_changed)
        self.rate_slider.valueChanged.connect(self._on_rate_changed)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.cache_limit_spin.valueChanged.connect(self._on_changed)

    def _slider_row(self, card, label, low, high, initial, formatter, hint):
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        slider = make_slider(row)
        slider.setRange(low, high)
        slider.setValue(int(initial()))
        value = QLabel(formatter(slider.value() / 100.0), row)
        value.setObjectName("settingHint")
        value.setMinimumWidth(52)
        layout.addWidget(slider, 1)
        layout.addWidget(value)
        card.add_row(label, row, hint, expand=True)
        return slider, value

    def _build_local(self) -> None:
        card = self.section("自定义 / 本地部署 TTS（HTTP）")
        self.local_endpoint = QLineEdit(self)
        self.local_endpoint.setMinimumWidth(300)
        self.local_endpoint.setPlaceholderText("http://127.0.0.1:9880")
        self.local_endpoint.setText(str(self.config().get("speech_local_endpoint") or ""))
        card.add_row(
            "服务地址",
            self.local_endpoint,
            "例如 Kokoro / openedai-speech / LocalAI 的本地地址；只填 http(s)，不要带查询串或 #。",
            expand=True,
        )

        self.local_protocol = QComboBox(self)
        self.local_protocol.addItem("OpenAI 兼容（/v1/audio/speech）", "openai")
        self.local_protocol.addItem("通用 JSON（{text, voice, speed}）", "simple")
        self._select(self.local_protocol, str(self.config().get("speech_local_protocol") or "openai"))
        card.add_row(
            "协议",
            self.local_protocol,
            "通用 JSON 适合 GPT-SoVITS / CosyVoice 等自带简单 HTTP 接口的本地服务。",
        )

        self.local_path = QLineEdit(self)
        self.local_path.setPlaceholderText("留空 = 按协议默认")
        self.local_path.setText(str(self.config().get("speech_local_path") or ""))
        card.add_row("请求路径", self.local_path, "例如 /tts；留空时 OpenAI 兼容用 /v1/audio/speech。", expand=True)

        self.local_model = QLineEdit(self)
        self.local_model.setPlaceholderText("tts-1 / kokoro / 你的模型名")
        self.local_model.setText(str(self.config().get("speech_local_model") or ""))
        card.add_row("模型名", self.local_model, "OpenAI 兼容协议会作为 model 字段发送。", expand=True)

        self.local_format = QComboBox(self)
        self.local_format.addItem("mp3", "mp3")
        self.local_format.addItem("wav", "wav")
        self._select(self.local_format, str(self.config().get("speech_local_format") or "mp3"))
        card.add_row("音频格式", self.local_format, "按你的本地服务实际返回的格式选。")

        self.local_timeout = QSpinBox(self)
        self.local_timeout.setRange(5, 120)
        self.local_timeout.setSuffix(" 秒")
        self.local_timeout.setValue(int(float(self.config().get("speech_local_timeout") or 30)))
        card.add_row("超时", self.local_timeout, "本地服务首次加载模型可能较慢，按需放宽。")

        self.local_key = self._secret_row(card, "本地 API Key", "local", "api_key", "本地服务通常不需要；需要时也存系统钥匙串。")
        self.local_test_button = make_button("测试本地模型", self)
        self.local_test_button.clicked.connect(self._test_local)
        card.add_buttons([self.local_test_button])

        self.local_endpoint.textChanged.connect(self._on_changed)
        self.local_protocol.currentIndexChanged.connect(self._on_changed)
        self.local_path.textChanged.connect(self._on_changed)
        self.local_model.textChanged.connect(self._on_changed)
        self.local_format.currentIndexChanged.connect(self._on_changed)
        self.local_timeout.valueChanged.connect(self._on_changed)

    def _build_asr(self) -> None:
        card = self.section("语音输入")
        self.asr_enabled = make_checkbox(self)
        self.asr_enabled.setChecked(bool(self.config().get("asr_enabled", True)))
        self.asr_enabled.toggled.connect(self._on_changed)
        card.add_row(
            "启用语音输入",
            self.asr_enabled,
            "对话窗/快速对话的话筒按钮；关闭后不录音、不识别。",
        )

        self.asr_engine = QComboBox(self)
        self.asr_engine.addItem("系统识别（Windows，免配置）", "sapi")
        self.asr_engine.addItem("本地 ASR 服务（OpenAI 兼容）", "http")
        self._select(self.asr_engine, str(self.config().get("asr_engine") or "sapi"))
        self.asr_engine.currentIndexChanged.connect(self._on_asr_engine_changed)
        card.add_row(
            "识别引擎",
            self.asr_engine,
            "系统识别跟随 Windows 中文语言包；本地服务质量更好，需要自己起服务。",
        )

        self.asr_endpoint = QLineEdit(self)
        self.asr_endpoint.setMinimumWidth(300)
        self.asr_endpoint.setPlaceholderText(
            "http://127.0.0.1:8000/v1/audio/transcriptions"
        )
        self.asr_endpoint.setText(str(self.config().get("asr_endpoint") or ""))
        self.asr_endpoint.textChanged.connect(self._on_changed)
        card.add_row(
            "本地服务地址",
            self.asr_endpoint,
            "OpenAI 兼容 /v1/audio/transcriptions；只直连，不走系统代理。",
            expand=True,
        )

        self.asr_model = QLineEdit(self)
        self.asr_model.setPlaceholderText("whisper-1 / FunASR 模型名（可留空）")
        self.asr_model.setText(str(self.config().get("asr_model") or ""))
        self.asr_model.textChanged.connect(self._on_changed)
        card.add_row("模型名", self.asr_model, "作为 multipart 的 model 字段发送；留空由服务端默认。")

        self.asr_language = QLineEdit(self)
        self.asr_language.setPlaceholderText("zh")
        self.asr_language.setText(str(self.config().get("asr_language") or "zh"))
        self.asr_language.setMaximumWidth(100)
        self.asr_language.textChanged.connect(self._on_changed)
        card.add_row("语言", self.asr_language, "通常保持 zh。")

        self.asr_auto_send = make_checkbox(self)
        self.asr_auto_send.setChecked(bool(self.config().get("asr_auto_send", False)))
        self.asr_auto_send.toggled.connect(self._on_changed)
        card.add_row(
            "识别后自动发送",
            self.asr_auto_send,
            "关闭时只把文字填进输入框，由你确认后发送。",
        )

        self.asr_max_seconds = QSpinBox(self)
        self.asr_max_seconds.setRange(3, 60)
        self.asr_max_seconds.setSuffix(" 秒")
        self.asr_max_seconds.setValue(int(self.config().get("asr_max_seconds") or 30))
        self.asr_max_seconds.valueChanged.connect(self._on_changed)
        card.add_row("最长录音", self.asr_max_seconds, "到点自动停止并开始识别。")

        self.asr_timeout = QSpinBox(self)
        self.asr_timeout.setRange(5, 120)
        self.asr_timeout.setSuffix(" 秒")
        self.asr_timeout.setValue(int(float(self.config().get("asr_timeout") or 30)))
        self.asr_timeout.valueChanged.connect(self._on_changed)
        card.add_row("识别超时", self.asr_timeout, "本地服务首次加载模型可能较慢。")

        self._sync_asr_rows()

    def _on_asr_engine_changed(self, *_args) -> None:
        self._sync_asr_rows()
        self._on_changed()

    def _sync_asr_rows(self, *_args) -> None:
        http = str(self.asr_engine.currentData() or "sapi") == "http"
        for widget in (self.asr_endpoint, self.asr_model, self.asr_language, self.asr_timeout):
            widget.setEnabled(http)

    def _build_secrets(self) -> None:
        card = self.section("在线后端凭据")
        self.doubao_key = self._secret_row(card, "豆包 API Key", "doubao", "api_key", "只存系统钥匙串；换 Key 时直接填新的保存。")
        self.mimo_key = self._secret_row(card, "MiMo API Key", "mimo", "api_key", "只存系统钥匙串；换 Key 时直接填新的保存。")
        credentials = getattr(self._service(), "credentials", None)
        available = bool(getattr(credentials, "available", lambda: False)())
        self.add_note(
            "系统钥匙串可用：密钥由操作系统保管。"
            if available
            else "系统钥匙串不可用：密钥只保存在本进程内存，重启即丢。"
        )

    def _secret_row(self, card, label, provider, field, hint) -> QLineEdit:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        edit = QLineEdit(row)
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setMinimumWidth(240)
        edit.setPlaceholderText("粘贴后点保存")
        edit.setAccessibleName(label)
        status = QLabel("", row)
        status.setObjectName("settingHint")
        status.setProperty("secretProvider", provider)
        status.setProperty("secretField", field)
        save = make_button("保存", row)
        save.clicked.connect(
            lambda _checked=False, p=provider, f=field, e=edit, s=status: self._save_secret(p, f, e, s)
        )
        clear = make_button("清除", row)
        clear.clicked.connect(
            lambda _checked=False, p=provider, f=field, e=edit, s=status: self._clear_secret(p, f, e, s)
        )
        layout.addWidget(edit, 1)
        layout.addWidget(status)
        layout.addWidget(save)
        layout.addWidget(clear)
        card.add_row(label, row, hint, expand=True)
        setattr(self, f"_{provider}_secret_status", (status, provider, field, edit))
        return edit

    def _build_cache(self) -> None:
        card = self.section("缓存")
        service = self._service()
        try:
            directory = str(service.cache_dir()) if service is not None else ""
        except Exception:  # noqa: BLE001 - 缓存目录探测失败不该打不开设置
            directory = ""
        self.cache_dir_field = value_field(directory, self)
        card.add_row("缓存目录", self.cache_dir_field, "合成好的音频按文本与音色缓存，命中直接播放。", expand=True)
        open_button = make_button("打开目录", self)
        open_button.clicked.connect(self._open_cache_dir)
        clear_button = make_button("清空缓存", self)
        clear_button.clicked.connect(self._clear_cache)
        self.cache_status = QLabel("", self)
        self.cache_status.setObjectName("settingHint")
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.cache_status, 1)
        layout.addWidget(open_button)
        layout.addWidget(clear_button)
        card.add_row("管理", row, expand=True)

    # ------------------------------------------------------------ 状态同步
    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _backend(self) -> str:
        return str(self.backend_combo.currentData() or "edge")

    def _voice_key(self, provider: str | None = None) -> str:
        return VOICE_KEYS.get(provider or self._backend(), "speech_voice")

    def _voice_text(self) -> str:
        index = self.voice_combo.currentIndex()
        text = self.voice_combo.currentText().strip()
        if index >= 0 and text == self.voice_combo.itemText(index):
            data = self.voice_combo.itemData(index)
            return str(data or "")
        return text

    def _sync_voice_options(self, *, write: bool = False) -> None:
        provider = self._backend()
        spec = PROVIDER_SPECS.get(provider)
        options = tuple(getattr(spec, "voice_options", ()) or ())
        self.voice_combo.blockSignals(True)
        try:
            self.voice_combo.clear()
            for value, label in options:
                self.voice_combo.addItem(label, value)
            stored = str(self.config().get(self._voice_key(provider)) or "")
            index = self.voice_combo.findData(stored)
            if index >= 0:
                self.voice_combo.setCurrentIndex(index)
            else:
                self.voice_combo.setCurrentIndex(-1)
                self.voice_combo.setEditText(stored)
            self.voice_value_label.setText(
                "默认" if not stored else ("可编辑" if not options else "")
            )
        finally:
            self.voice_combo.blockSignals(False)
        if write:
            self._on_changed()

    def _on_backend_changed(self, _index: int) -> None:
        self._sync_voice_options(write=True)

    def _on_voice_changed(self, _text: str) -> None:
        if self._building:
            return
        self._on_changed()

    def _on_rate_changed(self, value: int) -> None:
        self.rate_label.setText(_rate_text(value / 100.0))
        self._on_changed()

    def _on_volume_changed(self, value: int) -> None:
        self.volume_label.setText(_volume_text(value / 100.0))
        self._on_changed()

    # ------------------------------------------------------------ 快照/写回
    def _snapshot(self) -> dict:
        config = self.config()
        return {
            "backend": str(config.get("speech_backend") or "edge"),
            "fallback": str(config.get("speech_fallback") or ""),
            "rate": round(float(config.get("speech_rate") or 1.0), 3),
            "volume": round(float(config.get("speech_volume") or 1.0), 3),
            "cache_limit": int(config.get("speech_cache_limit") or 200),
            "local_endpoint": str(config.get("speech_local_endpoint") or ""),
            "local_protocol": str(config.get("speech_local_protocol") or "openai"),
            "local_path": str(config.get("speech_local_path") or ""),
            "local_model": str(config.get("speech_local_model") or ""),
            "local_format": str(config.get("speech_local_format") or "mp3"),
            "local_timeout": int(float(config.get("speech_local_timeout") or 30)),
            "asr_enabled": bool(config.get("asr_enabled", True)),
            "asr_engine": str(config.get("asr_engine") or "sapi"),
            "asr_endpoint": str(config.get("asr_endpoint") or ""),
            "asr_model": str(config.get("asr_model") or ""),
            "asr_language": str(config.get("asr_language") or "zh"),
            "asr_auto_send": bool(config.get("asr_auto_send", False)),
            "asr_max_seconds": int(config.get("asr_max_seconds") or 30),
            "asr_timeout": int(float(config.get("asr_timeout") or 30)),
            "voice_edge": str(config.get("speech_voice") or ""),
            "voice_sapi": str(config.get("speech_sapi_voice") or ""),
            "voice_doubao": str(config.get("speech_doubao_voice") or ""),
            "voice_mimo": str(config.get("speech_mimo_voice") or ""),
            "voice_local": str(config.get("speech_local_voice") or ""),
        }

    def _current(self) -> dict:
        return {
            "backend": self._backend(),
            "fallback": str(self.fallback_combo.currentData() or ""),
            "rate": round(self.rate_slider.value() / 100.0, 3),
            "volume": round(self.volume_slider.value() / 100.0, 3),
            "cache_limit": int(self.cache_limit_spin.value()),
            "local_endpoint": self.local_endpoint.text().strip(),
            "local_protocol": str(self.local_protocol.currentData() or "openai"),
            "local_path": self.local_path.text().strip(),
            "local_model": self.local_model.text().strip(),
            "local_format": str(self.local_format.currentData() or "mp3"),
            "local_timeout": int(self.local_timeout.value()),
            "asr_enabled": bool(self.asr_enabled.isChecked()),
            "asr_engine": str(self.asr_engine.currentData() or "sapi"),
            "asr_endpoint": self.asr_endpoint.text().strip(),
            "asr_model": self.asr_model.text().strip(),
            "asr_language": self.asr_language.text().strip() or "zh",
            "asr_auto_send": bool(self.asr_auto_send.isChecked()),
            "asr_max_seconds": int(self.asr_max_seconds.value()),
            "asr_timeout": int(self.asr_timeout.value()),
            "voice_edge": str(self.config().get("speech_voice") or ""),
            "voice_sapi": str(self.config().get("speech_sapi_voice") or ""),
            "voice_doubao": str(self.config().get("speech_doubao_voice") or ""),
            "voice_mimo": str(self.config().get("speech_mimo_voice") or ""),
            "voice_local": str(self.config().get("speech_local_voice") or ""),
        }

    def _current_for_write(self) -> dict:
        values = self._current()
        values[self._voice_state_key()] = self._voice_text()
        return values

    def _voice_state_key(self) -> str:
        return {
            "sapi": "voice_sapi",
            "doubao": "voice_doubao",
            "mimo": "voice_mimo",
            "local": "voice_local",
        }.get(self._backend(), "voice_edge")

    def _write(self, key: str, value) -> None:
        if key in {
            "speech_rate",
            "speech_volume",
            "speech_local_timeout",
            "asr_timeout",
        }:
            value = float(value)
        config = self.config()
        try:
            current = config.get(key)
        except Exception:  # noqa: BLE001 - 读不到就走写入
            current = None
        if current == value:
            return
        config.set(key, value)
        self._written[key] = value

    def _on_changed(self, *_args) -> None:
        if self._building:
            return
        values = self._current_for_write()
        for state_key, config_key in CONFIG_KEYS.items():
            self._write(config_key, values[state_key])
        self.changed.emit()

    def dirty(self) -> bool:
        if self._building:
            return False
        return self._current_for_write() != self._initial

    def confirm_save(self) -> None:
        raw_endpoint = self.local_endpoint.text().strip()
        self._on_changed()
        self._initial = self._snapshot()
        self._written = dict(self._initial)
        # 配置会把 endpoint 补成 http://、去掉首尾空白；输入框同步成落盘值，
        # 否则「保存后仍是已修改」会被 dirty() 报成脏。音色也可能被截断到 64 字。
        self._sync_text_fields(self._initial)
        self._sync_voice_options()
        if raw_endpoint and not self._initial["local_endpoint"]:
            self._set_status(
                "本地服务地址无效：只支持 http(s)，且不能带查询串或 #。"
            )

    def _sync_text_fields(self, values: dict) -> None:
        for field, key in (
            (self.local_endpoint, "local_endpoint"),
            (self.local_path, "local_path"),
            (self.local_model, "local_model"),
            (self.asr_endpoint, "asr_endpoint"),
            (self.asr_model, "asr_model"),
            (self.asr_language, "asr_language"),
        ):
            field.blockSignals(True)
            field.setText(str(values.get(key) or ""))
            field.blockSignals(False)

    def discard_changes(self) -> None:
        self._building = True
        try:
            config = self.config()
            for config_key, value in self._initial_config_items().items():
                config.set(config_key, value)
            self._select(self.backend_combo, self._initial["backend"])
            self._select(self.fallback_combo, self._initial["fallback"])
            self._select(self.local_protocol, self._initial["local_protocol"])
            self._select(self.local_format, self._initial["local_format"])
            self.rate_slider.setValue(int(round(self._initial["rate"] * 100)))
            self.volume_slider.setValue(int(round(self._initial["volume"] * 100)))
            self.cache_limit_spin.setValue(int(self._initial["cache_limit"]))
            self.local_endpoint.setText(self._initial["local_endpoint"])
            self.local_path.setText(self._initial["local_path"])
            self.local_model.setText(self._initial["local_model"])
            self.local_timeout.setValue(int(self._initial["local_timeout"]))
            self.asr_enabled.setChecked(bool(self._initial["asr_enabled"]))
            self._select(self.asr_engine, self._initial["asr_engine"])
            self.asr_endpoint.setText(self._initial["asr_endpoint"])
            self.asr_model.setText(self._initial["asr_model"])
            self.asr_language.setText(self._initial["asr_language"])
            self.asr_auto_send.setChecked(bool(self._initial["asr_auto_send"]))
            self.asr_max_seconds.setValue(int(self._initial["asr_max_seconds"]))
            self.asr_timeout.setValue(int(self._initial["asr_timeout"]))
            self._sync_asr_rows()
            self._sync_voice_options()
        finally:
            self._building = False
        self._written = dict(self._initial)
        self.changed.emit()

    def _initial_config_items(self) -> dict:
        return {
            config_key: self._initial[state]
            for state, config_key in CONFIG_KEYS.items()
        }

    # ------------------------------------------------------------ 密钥
    def _secret_status(self, provider: str, field: str) -> QLabel | None:
        entry = getattr(self, f"_{provider}_secret_status", None)
        return entry[0] if entry else None

    def _refresh_secret_statuses(self) -> None:
        service = self._service()
        for provider in ("local", "doubao", "mimo"):
            entry = getattr(self, f"_{provider}_secret_status", None)
            if entry is None:
                continue
            status, _provider, field, _edit = entry
            try:
                configured = bool((service.secret_status(provider) if service else {}).get(field))
            except Exception:  # noqa: BLE001 - 读不到按未配置
                configured = False
            status.setText("已配置" if configured else "未配置")

    def _save_secret(self, provider: str, field: str, edit: QLineEdit, status: QLabel) -> None:
        service = self._service()
        if service is None:
            status.setText("语音服务未就绪")
            return
        value = edit.text().strip()
        if not value:
            status.setText("请先填写密钥")
            return
        try:
            service.set_secret(provider, field, value)
        except Exception:  # noqa: BLE001 - 钥匙串不可用要可见
            logger.warning("speech secret save failed: %s/%s", provider, field)
            status.setText("保存失败（钥匙串不可用）")
            return
        edit.clear()
        status.setText("已保存")

    def _clear_secret(self, provider: str, field: str, edit: QLineEdit, status: QLabel) -> None:
        service = self._service()
        if service is None:
            status.setText("语音服务未就绪")
            return
        try:
            service.set_secret(provider, field, "")
        except Exception:  # noqa: BLE001
            status.setText("清除失败")
            return
        edit.clear()
        status.setText("未配置")

    # ------------------------------------------------------------ 试听
    def _speak(self, *, provider: str, text: str, voice: str = "") -> None:
        port = self._port()
        if port is None:
            self._set_status("语音通道未就绪（应用启动后可用）")
            return
        request = SpeechRequest(
            text=text,
            provider=provider,
            voice=voice,
            rate=self.rate_slider.value() / 100.0,
            volume=self.volume_slider.value() / 100.0,
        )
        self._set_status("正在合成…")

        def done(result) -> None:
            name = getattr(result, "voice", "") or "默认音色"
            self._set_status(f"正在播放：{getattr(result, 'provider', provider)} / {name}")

        def failed(error) -> None:
            reason = getattr(error, "reason", "") or ""
            self._set_status(f"合成失败：{REASON_TEXT.get(reason, describe_net_error(reason))}")

        try:
            port.speak(request, owner=PREVIEW_OWNER, on_done=done, on_error=failed)
        except Exception as exc:  # noqa: BLE001 - 端口异常要变成可见状态
            self._set_status(f"语音通道异常：{type(exc).__name__}")

    def _preview(self) -> None:
        provider = self._backend()
        self._speak(provider=provider, text=PREVIEW_TEXT, voice=self._voice_text())

    def _test_local(self) -> None:
        service = self._service()
        if service is not None:
            try:
                info = next(
                    (item for item in service.providers() if item.id == "local"), None
                )
            except Exception:  # noqa: BLE001
                info = None
            if info is not None and not info.available:
                reason = str(info.reason or "config")
                self._set_status(
                    f"本地模型不可用：{REASON_TEXT.get(reason, reason)}"
                )
                return
        self._speak(
            provider="local",
            text=PREVIEW_TEXT,
            voice=str(self.config().get("speech_local_voice") or ""),
        )

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ------------------------------------------------------------ 缓存
    def _open_cache_dir(self) -> None:
        service = self._service()
        if service is None:
            return
        try:
            directory = service.cache_dir()
        except Exception:  # noqa: BLE001
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _clear_cache(self) -> None:
        service = self._service()
        if service is None:
            self.cache_status.setText("语音服务未就绪")
            return
        try:
            cache = service.cache
            removed = 0
            for path in cache.entries():
                cache.discard(path)
                removed += 1
        except Exception:  # noqa: BLE001
            self.cache_status.setText("清空失败")
            return
        self.cache_status.setText(f"已清空 {removed} 个文件")
        self.cache_dir_field.setText(str(service.cache_dir()))

    # ------------------------------------------------------------ 其它
    def dispose(self) -> None:
        try:
            port = self._port()
            cancel = getattr(port, "cancel_owner", None)
            if callable(cancel):
                cancel(PREVIEW_OWNER)
        except Exception:  # noqa: BLE001
            logger.debug("voice preview cancel failed", exc_info=True)
        super().dispose()

    def keywords(self) -> list[str]:
        return [
            "语音",
            "朗读",
            "念",
            "音色",
            "语速",
            "音量",
            "合成",
            "tts",
            "speech",
            "edge",
            "sapi",
            "豆包",
            "doubao",
            "mimo",
            "本地模型",
            "本地部署",
            "缓存",
            "语音输入",
            "麦克风",
            "录音",
            "识别",
            "asr",
            "whisper",
        ]
