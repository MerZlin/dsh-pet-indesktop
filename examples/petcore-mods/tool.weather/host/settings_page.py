"""天气设置页：启用、城市列表、当前城市与「立即查看」。

改动实时写进 config（全局键），保存只负责落盘；取消/关闭时 `discard_changes()`
把配置与控件一起回滚到打开设置时的快照。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QWidget,
)

from petcore.ui.settings.base import SettingsPage, make_checkbox

from .client import WeatherClient
from .logic import (
    MAX_CITIES,
    add_city,
    current_city,
    normalize_city_list,
    remove_city,
)


class WeatherPage(SettingsPage):
    title = "天气"
    test_done = Signal(str)

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.group_heading("天气")
        config = self.config()
        self._initial_enabled = bool(config.get("weather_enabled", True))
        self._initial_cities = normalize_city_list(config.get("weather_city_list", []))
        self._initial_current = current_city(config)
        self._cities = list(self._initial_cities)
        self._current = self._initial_current

        card = self.section("天气", level=3)
        self.enabled = make_checkbox(self)
        self.enabled.setChecked(self._initial_enabled)
        self.enabled.toggled.connect(self._on_enabled)
        card.add_row(
            "启用天气",
            self.enabled,
            "右键菜单「天气」显示当前城市的实况；关闭后不发任何请求。",
        )

        self.current = QComboBox(self)
        self.current.setMinimumWidth(180)
        self.current.currentTextChanged.connect(self._on_current_changed)
        card.add_row("当前城市", self.current, "菜单查询的城市。")

        editor = QWidget(self)
        editor_layout = QHBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        self.name_edit = QLineEdit(editor)
        self.name_edit.setPlaceholderText("城市名，如 鹿城 / 朝阳 · 辽宁")
        self.name_edit.setMinimumWidth(200)
        self.name_edit.returnPressed.connect(self._add_city)
        self.add_button = QPushButton("添加", editor)
        self.add_button.clicked.connect(self._add_city)
        editor_layout.addWidget(self.name_edit, 1)
        editor_layout.addWidget(self.add_button)
        card.add_row(
            "添加城市",
            editor,
            f"最多 {MAX_CITIES} 个；保存时不会剥掉「市/区」等后缀，查询时才做后缀容错。",
            expand=True,
        )

        self.city_list = QListWidget(self)
        self.city_list.setMinimumHeight(112)
        self.city_list.setMaximumHeight(150)
        card.add_row("城市列表", self.city_list, "选中即可设为当前城市。", expand=True)

        tools = QWidget(self)
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(8)
        self.remove_button = QPushButton("删除选中", tools)
        self.remove_button.clicked.connect(self._remove_selected)
        self.test_button = QPushButton("立即查看", tools)
        self.test_button.clicked.connect(self._test)
        tools_layout.addWidget(self.remove_button)
        tools_layout.addWidget(self.test_button)
        tools_layout.addStretch(1)
        card.add_row("城市管理", tools, "", expand=True)

        self.status = QLabel("", self)
        self.status.setObjectName("settingHint")
        self.status.setWordWrap(True)
        card.add_row("状态", self.status, "只有手动点「立即查看」才会联网。", expand=True)

        self.add_note(
            "取数口径：中国天气网走直连，open-meteo 跟随系统代理；单请求 6 秒、"
            "整次 15 秒预算。两源都失败时气泡会点名每个源的原因，不会静默。"
        )
        self.test_done.connect(self._on_test_done)
        self._refresh()

    # ---------------------------------------------------------------- 配置

    def config(self):
        return self.host.config

    def _initial(self) -> tuple:
        return (self._initial_enabled, tuple(self._initial_cities), self._initial_current)

    def _current_state(self) -> tuple:
        return (self.enabled.isChecked(), tuple(self._cities), self._current)

    def dirty(self) -> bool:
        return self._current_state() != self._initial()

    def confirm_save(self) -> None:
        config = self.config()
        config.set("weather_enabled", self.enabled.isChecked())
        config.set("weather_city_list", list(self._cities))
        config.set("weather_city", self._current)
        self._initial_enabled, self._initial_cities, self._initial_current = self._current_state()

    def discard_changes(self) -> None:
        config = self.config()
        self._cities = list(self._initial_cities)
        self._current = self._initial_current
        config.set("weather_enabled", self._initial_enabled)
        config.set("weather_city_list", list(self._initial_cities))
        config.set("weather_city", self._initial_current)
        self.enabled.blockSignals(True)
        self.enabled.setChecked(self._initial_enabled)
        self.enabled.blockSignals(False)
        self._refresh()
        self.status.setText("")
        self.changed.emit()

    def keywords(self) -> list[str]:
        return ["天气", "城市", "气温", "weather", "风向", "湿度"]

    # ---------------------------------------------------------------- 控件

    def _refresh(self) -> None:
        self.city_list.blockSignals(True)
        self.city_list.clear()
        self.city_list.addItems(self._cities)
        self.city_list.blockSignals(False)
        self.current.blockSignals(True)
        self.current.clear()
        self.current.addItems(self._cities)
        target = self._current or (self._cities[0] if self._cities else "")
        if target:
            index = self.current.findText(target)
            if index >= 0:
                self.current.setCurrentIndex(index)
        self.current.blockSignals(False)

    def _on_enabled(self, _checked: bool = False) -> None:
        self.config().set("weather_enabled", self.enabled.isChecked())
        self.changed.emit()

    def _on_current_changed(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        self._current = text
        self.config().set("weather_city", text)
        self.changed.emit()

    def _add_city(self) -> None:
        cities, error = add_city(self._cities, self.name_edit.text())
        if error:
            self.status.setText(error)
            return
        self._cities = cities
        self._current = cities[-1]
        self.name_edit.setText("")
        self._refresh()
        self.config().set("weather_city_list", list(self._cities))
        self.config().set("weather_city", self._current)
        self.status.setText("")
        self.changed.emit()

    def _remove_selected(self) -> None:
        item = self.city_list.currentItem()
        if item is None:
            self.status.setText("先在列表里选中一个城市")
            return
        answer = QMessageBox.question(
            self,
            "移除城市",
            f"确定从列表移除「{item.text()}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cities, self._current = remove_city(self._cities, item.text(), self._current)
        self._refresh()
        self.config().set("weather_city_list", list(self._cities))
        self.config().set("weather_city", self._current)
        self.changed.emit()

    def _test(self) -> None:
        city = self._current
        if not city:
            self.status.setText("还没有选城市")
            return
        ports = getattr(self.host, "ports", None)
        http = getattr(ports, "http", None)
        if http is None:
            self.status.setText("取数端口不可用：请更新 core")
            return
        self.test_button.setEnabled(False)
        self.status.setText(f"正在查询 {city}…")

        def work() -> None:
            try:
                reading = WeatherClient(http).fetch(city)
                text = reading.bubble_text()
            except Exception as exc:  # noqa: BLE001 - 结果行必须说清楚
                text = str(exc) or "天气获取失败"
            try:
                self.test_done.emit(text)
            except RuntimeError:
                # 设置窗口在查询期间被销毁：信号对象已失效，丢弃结果即可。
                pass

        threading.Thread(target=work, name="weather-test", daemon=True).start()

    def _on_test_done(self, text: str) -> None:
        self.status.setText(text)
        self.test_button.setEnabled(True)
