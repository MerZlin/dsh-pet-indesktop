"""音乐设置页：音乐信息、唱歌动画、歌词与四个播放器的路径（Qt 允许）。

改动实时写进 config（全局键），保存只负责落盘；取消/关闭时 ``discard_changes()``
把配置与控件一起回滚到打开设置时的快照（与其它扩展设置页同一口径）。

运行时只读配置，所以「歌词对齐」调出来的偏移是**本次运行**的会话状态；本页只显示
配置里的初值 ``music_lyric_offset_seconds``。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from petcore.ui.settings.base import SettingsPage, make_checkbox

from . import audio_sessions, diagnostics, now_playing, players, recognizer, sampling
from .runtime import DEFAULTS


class MusicPage(SettingsPage):
    title = "音乐"
    detect_done = Signal(str)
    diagnose_done = Signal(str)
    transport_done = Signal(str)

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        config = self.config()
        self.group_heading("音乐")

        # -------------------------------------------------------- 音乐信息
        card = self.section("播放与唱歌", level=3)
        self.enabled = make_checkbox(self)
        self.enabled.setChecked(bool(config.get("music_enabled", DEFAULTS["music_enabled"])))
        self.enabled.toggled.connect(lambda _checked=False: self._write("music_enabled", self.enabled.isChecked()))
        card.add_row(
            "启用音乐",
            self.enabled,
            "读取系统当前播放的曲目（SMTC）；关闭后不轮询、不发任何请求。",
        )

        self.sing = make_checkbox(self)
        self.sing.setChecked(bool(config.get("music_sing_enabled", DEFAULTS["music_sing_enabled"])))
        self.sing.toggled.connect(
            lambda _checked=False: self._write("music_sing_enabled", self.sing.isChecked())
        )
        card.add_row(
            "唱歌动画",
            self.sing,
            "播放音乐时循环播放「悠闲哼歌」；纯音乐不唱。",
        )

        self.grace = QSpinBox(self)
        self.grace.setRange(0, 120)
        self.grace.setSuffix(" 秒")
        self.grace.setValue(int(float(config.get("music_sing_grace_seconds", DEFAULTS["music_sing_grace_seconds"]))))
        self.grace.valueChanged.connect(
            lambda value: self._write("music_sing_grace_seconds", int(value))
        )
        card.add_row(
            "停止后保持",
            self.grace,
            "没声音（切歌抖动、播放器被关掉）超过这个时间才停止唱歌动画；暂停会立即停唱，歌与歌之间的空隙不会打断。",
        )

        # -------------------------------------------------------- 识别诊断
        diagnose_card = self.section("识别诊断", level=3)
        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.detect_button = QPushButton("检测当前播放", buttons)
        self.detect_button.clicked.connect(self._detect)
        self.copy_button = QPushButton("复制诊断", buttons)
        self.copy_button.clicked.connect(self._copy_diagnostics)
        self.copy_button.setEnabled(False)
        button_layout.addWidget(self.detect_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addStretch(1)
        diagnose_card.add_row(
            "识别诊断",
            buttons,
            "只在你点它时采一次：系统媒体会话、音频会话、播放器窗口标题各看到了什么，"
            "以及最终判定为什么这么下。不联网、不录音。",
            expand=True,
        )

        self.detect_status = QLabel("", self)
        self.detect_status.setObjectName("settingHint")
        self.detect_status.setWordWrap(True)
        diagnose_card.add_row("检测结果", self.detect_status, "", expand=True)

        self.diagnose_view = QPlainTextEdit(self)
        self.diagnose_view.setReadOnly(True)
        self.diagnose_view.setObjectName("musicDiagnostics")
        self.diagnose_view.setMinimumHeight(150)
        self.diagnose_view.setPlaceholderText("点「检测当前播放」后，这里会列出三个来源的采样与判定。")
        diagnose_card.add_row("详细诊断", self.diagnose_view, "", expand=True)

        # -------------------------------------------------------- 歌词
        lyric_card = self.section("歌词显示", level=3)
        self.lyric = make_checkbox(self)
        self.lyric.setChecked(bool(config.get("music_lyric_enabled", DEFAULTS["music_lyric_enabled"])))
        self.lyric.toggled.connect(
            lambda _checked=False: self._write("music_lyric_enabled", self.lyric.isChecked())
        )
        lyric_card.add_row(
            "显示歌词",
            self.lyric,
            "每首歌在后台取一次歌词，之后按播放位置更新气泡；失败不重试。",
        )

        self.lead = QDoubleSpinBox(self)
        self.lead.setRange(-2.0, 3.0)
        self.lead.setDecimals(1)
        self.lead.setSingleStep(0.1)
        self.lead.setSuffix(" 秒")
        self.lead.setValue(float(config.get("music_lyric_lead_seconds", DEFAULTS["music_lyric_lead_seconds"])))
        self.lead.valueChanged.connect(
            lambda value: self._write("music_lyric_lead_seconds", round(float(value), 1))
        )
        lyric_card.add_row(
            "歌词提前量",
            self.lead,
            "正数让歌词提前出现（人耳对「歌词晚了」更敏感）。",
        )

        self.cache = QSpinBox(self)
        self.cache.setRange(10, 2000)
        self.cache.setSingleStep(10)
        self.cache.setSuffix(" 首")
        self.cache.setValue(int(float(config.get("music_lyric_cache_limit", DEFAULTS["music_lyric_cache_limit"]))))
        self.cache.valueChanged.connect(
            lambda value: self._write("music_lyric_cache_limit", int(value))
        )
        lyric_card.add_row(
            "歌词缓存上限",
            self.cache,
            "超出后按写入时间淘汰最旧的；缓存里只有歌词文本。",
        )

        self.offset_value = float(
            config.get("music_lyric_offset_seconds", DEFAULTS["music_lyric_offset_seconds"]) or 0.0
        )
        self.offset_label = QLabel(f"{self.offset_value:+.1f} 秒", self)
        self.offset_label.setObjectName("settingHint")
        lyric_card.add_row(
            "歌词偏移",
            self.offset_label,
            "右键菜单「音乐 → 歌词对齐」每次 +0.5 秒（本次运行有效）；这里是配置里的初值。",
        )

        self.browser = make_checkbox(self)
        self.browser.setChecked(
            bool(config.get("music_browser_media_enabled", DEFAULTS["music_browser_media_enabled"]))
        )
        self.browser.toggled.connect(
            lambda _checked=False: self._write("music_browser_media_enabled", self.browser.isChecked())
        )
        lyric_card.add_row(
            "浏览器媒体会话",
            self.browser,
            "浏览器会把网页视频也报成音乐；默认不参与歌词与唱歌。",
        )

        # -------------------------------------------------------- 识别范围
        filter_card = self.section("识别范围", level=3)
        self.filter_enabled = make_checkbox(self)
        self.filter_enabled.setChecked(
            bool(config.get("music_filter_enabled", DEFAULTS["music_filter_enabled"]))
        )
        self.filter_enabled.toggled.connect(
            lambda _checked=False: self._write(
                "music_filter_enabled", self.filter_enabled.isChecked()
            )
        )
        filter_card.add_row(
            "只识别指定播放器",
            self.filter_enabled,
            "开启后只认下面填了路径的播放器（以及下面的额外识别名）；"
            "其它播放器即使正在播也不会唱歌/出歌词。",
        )

        self.filter_extra = QLineEdit(
            str(config.get("music_filter_extra_ids", DEFAULTS["music_filter_extra_ids"]) or ""),
            self,
        )
        self.filter_extra.setPlaceholderText("多个用逗号分隔，例如：汽水音乐")
        self.filter_extra.editingFinished.connect(
            lambda: self._write(
                "music_filter_extra_ids", self.filter_extra.text().strip()
            )
        )
        filter_card.add_row(
            "额外识别名",
            self.filter_extra,
            "播放器上报的系统识别名不一定等于 exe 名；「检测当前播放」会显示识别名。",
            expand=True,
        )

        self.title_format = QLineEdit(
            str(config.get("music_title_format", DEFAULTS["music_title_format"]) or ""),
            self,
        )
        self.title_format.setPlaceholderText("留空 = 自动（不猜谁在前面）")
        self.title_format.setMaxLength(120)
        self.title_format.editingFinished.connect(
            lambda: self._write("music_title_format", self.title_format.text().strip())
        )
        filter_card.add_row(
            "播放器标题格式（高级）",
            self.title_format,
            "只有「不注册系统媒体、只能读窗口标题」的播放器（如 foobar2000）用得到。"
            "通常留空；对不上时把播放器「窗口标题」设置里那串格式表达式原样贴进来，"
            "例如 %artist% - %title%。",
            expand=True,
        )

        # -------------------------------------------------------- 切歌
        transport_card = self.section("切歌与播放控制", level=3)
        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        for label, action in (
            ("播放 / 暂停", "pause"),
            ("上一首", "prev"),
            ("下一首", "next"),
            ("停止", "stop"),
        ):
            button = QPushButton(label, buttons)
            button.clicked.connect(
                lambda _checked=False, command=action: self._transport(command)
            )
            button_layout.addWidget(button)
        button_layout.addStretch(1)
        transport_card.add_row(
            "播放控制",
            buttons,
            "与右键菜单的「音乐」命令是同一套实现；结果会以气泡提示。",
            expand=True,
        )

        # -------------------------------------------------------- 播放器
        player_card = self.section("播放器", level=3)
        self.player = QComboBox(self)
        self.player.setMinimumWidth(180)
        self.player.addItem("（不指定）", "")
        for key in players.player_keys():
            self.player.addItem(players.player_label(key), key)
        index = self.player.findData(str(config.get("music_player", DEFAULTS["music_player"]) or ""))
        self.player.setCurrentIndex(index if index >= 0 else 0)
        self.player.currentIndexChanged.connect(
            lambda _index=0: self._write("music_player", str(self.player.currentData() or ""))
        )
        player_card.add_row(
            "默认播放器",
            self.player,
            "多个会话同时播放时优先跟随它；留空则跟随正在播放的那个。",
        )

        self.path_edits: dict[str, QLineEdit] = {}
        configured = config.get("music_player_paths", DEFAULTS["music_player_paths"])
        configured = configured if isinstance(configured, dict) else {}
        for key in players.player_keys():
            player_card.add_row(
                f"{players.player_label(key)}路径",
                self._path_row(key, str(configured.get(key) or "")),
                "留空 = 不启动；菜单「打开 …」只用这里配置的路径，不扫盘。",
                expand=True,
            )

        self.add_note(
            "识别方式：系统媒体会话为主源；再用系统音频会话判断「这个进程是否真的在出声」；"
            "不在系统里登记播放信息的播放器（如 foobar2000 / AIMP / Winamp）读窗口标题兜底，"
            "且只有该进程正在出声才采信。全程不录音、不做听声识曲。"
            "隐私与网络：歌词只从公开接口取文本（网易云 / 酷狗 / lrclib，单次预算 8 秒、"
            "响应上限 2 MB），不下载音频、不记录收听历史；同一首要失败冷却后才会重试。"
        )

        self.detect_done.connect(self._on_detect_done)
        self.diagnose_done.connect(self._on_diagnose_done)
        self.transport_done.connect(self._on_transport_done)
        self._initial = self._snapshot()

    # ---------------------------------------------------------------- 配置

    def config(self):
        return self.host.config

    def _write(self, key: str, value) -> None:
        self.config().set(key, value)
        self.changed.emit()

    def _paths(self) -> dict[str, str]:
        raw = self.config().get("music_player_paths", {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _snapshot(self) -> dict:
        """当前控件状态（取消时按它整组回滚）。

        用字典而不是位置元组：元组一旦插字段，``confirm_save`` / ``discard_changes``
        里的下标就会静默错位（这一页已经被插过好几轮键）。
        """
        return {
            "music_enabled": bool(self.enabled.isChecked()),
            "music_sing_enabled": bool(self.sing.isChecked()),
            "music_sing_grace_seconds": int(self.grace.value()),
            "music_lyric_enabled": bool(self.lyric.isChecked()),
            "music_lyric_lead_seconds": round(float(self.lead.value()), 1),
            "music_lyric_cache_limit": int(self.cache.value()),
            "music_browser_media_enabled": bool(self.browser.isChecked()),
            "music_player": str(self.player.currentData() or ""),
            "music_player_paths": {
                key: edit.text().strip()
                for key, edit in self.path_edits.items()
                if edit.text().strip()
            },
            "music_filter_enabled": bool(self.filter_enabled.isChecked()),
            "music_filter_extra_ids": str(self.filter_extra.text().strip()),
            "music_title_format": str(self.title_format.text().strip()),
        }

    def dirty(self) -> bool:
        return self._snapshot() != self._initial

    def confirm_save(self) -> None:
        config = self.config()
        for key, value in self._snapshot().items():
            config.set(key, value)
        self._initial = self._snapshot()

    def discard_changes(self) -> None:
        values = self._initial
        config = self.config()
        for key, value in values.items():
            config.set(key, value)

        for widget, key in (
            (self.enabled, "music_enabled"),
            (self.sing, "music_sing_enabled"),
            (self.lyric, "music_lyric_enabled"),
            (self.browser, "music_browser_media_enabled"),
            (self.filter_enabled, "music_filter_enabled"),
        ):
            widget.blockSignals(True)
            widget.setChecked(bool(values.get(key)))
            widget.blockSignals(False)
        for widget, key in (
            (self.grace, "music_sing_grace_seconds"),
            (self.cache, "music_lyric_cache_limit"),
        ):
            widget.blockSignals(True)
            widget.setValue(int(values.get(key) or 0))
            widget.blockSignals(False)
        self.lead.blockSignals(True)
        self.lead.setValue(float(values.get("music_lyric_lead_seconds") or 0.0))
        self.lead.blockSignals(False)
        self.player.blockSignals(True)
        index = self.player.findData(str(values.get("music_player") or ""))
        self.player.setCurrentIndex(index if index >= 0 else 0)
        self.player.blockSignals(False)
        paths = values.get("music_player_paths") or {}
        for key, edit in self.path_edits.items():
            edit.blockSignals(True)
            edit.setText(str(paths.get(key) or ""))
            edit.blockSignals(False)
        for edit, key in (
            (self.filter_extra, "music_filter_extra_ids"),
            (self.title_format, "music_title_format"),
        ):
            edit.blockSignals(True)
            edit.setText(str(values.get(key) or ""))
            edit.blockSignals(False)
        self.detect_status.setText("")
        self.diagnose_view.clear()
        self.copy_button.setEnabled(False)
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "音乐",
            "播放",
            "暂停",
            "上一首",
            "下一首",
            "歌词",
            "唱歌",
            "识别",
            "切歌",
            "播放控制",
            "网易云",
            "QQ音乐",
            "酷狗",
            "汽水",
            "诊断",
            "识别诊断",
            "窗口标题",
            "foobar",
            "aimp",
            "音频会话",
            "music",
            "lyric",
        ]

    # ---------------------------------------------------------------- 控件

    def _path_row(self, key: str, value: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        edit = QLineEdit(value, row)
        edit.setPlaceholderText(f"{players.player_exe(key)} 的完整路径")
        edit.setMinimumWidth(260)
        edit.editingFinished.connect(lambda key=key: self._on_path_changed(key))
        browse = QPushButton("浏览…", row)
        browse.clicked.connect(lambda _checked=False, key=key: self._browse(key))
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        self.path_edits[key] = edit
        return row

    def _on_path_changed(self, key: str) -> None:
        edit = self.path_edits.get(key)
        if edit is None:
            return
        text = edit.text().strip()
        paths = self._paths()
        if text:
            paths[key] = text
        else:
            paths.pop(key, None)
        self._write("music_player_paths", paths)

    def _browse(self, key: str) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            f"选择{players.player_label(key)}的程序",
            str(Path.home()),
            "程序 (*.exe);;所有文件 (*)",
        )
        if not path:
            return
        edit = self.path_edits.get(key)
        if edit is not None:
            edit.setText(path)
        self._on_path_changed(key)

    def _allowed_ids_from_ui(self) -> frozenset[str]:
        """按当前控件状态算识别范围；开关关着就是空集合（不过滤）。"""
        if not self.filter_enabled.isChecked():
            return frozenset()
        paths = {
            key: edit.text().strip()
            for key, edit in self.path_edits.items()
            if edit.text().strip()
        }
        return now_playing.allowed_app_ids(
            paths, extras=self.filter_extra.text().strip()
        )

    def _detect(self) -> None:
        """采一次三源并渲染诊断（后台线程；结果经信号回 GUI 线程）。"""
        self.detect_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.detect_status.setText("正在采样：系统媒体会话 / 音频会话 / 播放器窗口标题…")
        browser_media = bool(self.browser.isChecked())
        allowed = self._allowed_ids_from_ui()
        targets = players.recognition_targets(self._paths(), self.filter_extra.text().strip())
        title_format = self.title_format.text().strip()

        def work() -> None:
            try:
                sample = sampling.collect(
                    browser_media_enabled=browser_media,
                    allowed_ids=allowed or None,
                    targets=targets,
                )
                decision = recognizer.decide(
                    sample,
                    allowed_ids=allowed,
                    title_format=title_format,
                )
                text = diagnostics.render(sample, decision)
            except Exception:
                text = "采样失败：读取系统媒体信息时出了意外，请稍后重试。"
            finally:
                # 这条线程是一次性的：常驻事件循环与 COM apartment 都要收尾，
                # 否则每次点击都漏一个 loop，还会把音频会话缓存绑到将死的线程上。
                now_playing.close_thread_loop()
                audio_sessions.close()
            try:
                self.diagnose_done.emit(text)
            except RuntimeError:
                # 设置窗口在采样期间被销毁：信号对象已失效，丢弃结果即可。
                pass

        threading.Thread(target=work, name="music-detect", daemon=True).start()

    def _on_diagnose_done(self, text: str) -> None:
        self.diagnose_view.setPlainText(text)
        self.copy_button.setEnabled(bool(text.strip()))
        self.detect_button.setEnabled(True)
        self._on_detect_done(self._summary_line(text))

    def _summary_line(self, text: str) -> str:
        for line in str(text or "").splitlines():
            if line.startswith("判定："):
                return line[len("判定：") :]
        for line in str(text or "").splitlines():
            if line.strip():
                return line.strip()
        return "没有采样结果。"

    def _copy_diagnostics(self) -> None:
        text = self.diagnose_view.toPlainText().strip()
        if not text:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.detect_status.setText("诊断已复制到剪贴板。")

    def _on_detect_done(self, text: str) -> None:
        self.detect_status.setText(text)
        self.detect_button.setEnabled(True)

    def _transport(self, action: str) -> None:
        allowed = self._allowed_ids_from_ui()

        def work() -> None:
            try:
                ok, reason = now_playing.transport(
                    action, allowed_ids=allowed or None
                )
            except Exception:
                ok, reason = False, "系统媒体控制调用失败，请稍后重试。"
            finally:
                now_playing.close_thread_loop()
            if ok:
                text = f"已{now_playing.ACTION_LABELS.get(action, action)}"
            else:
                text = reason or "系统媒体控制不可用"
            try:
                self.transport_done.emit(text)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="music-transport", daemon=True).start()

    def _on_transport_done(self, text: str) -> None:
        self.detect_status.setText(text)
        port_factory = getattr(self.host, "presentation_port", None)
        if not callable(port_factory):
            return
        try:
            port_factory().say(text, force=True)
        except Exception:
            return
