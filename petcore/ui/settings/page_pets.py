from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QWidget

from ...config import MAX_PETS
from ...engine.geometry import SCALE_MAX, SCALE_MIN
from .base import SettingsPage, make_button, make_checkbox, make_slider, make_spin


def _percent(scale: float) -> int:
    return int(round(max(SCALE_MIN, min(SCALE_MAX, float(scale))) * 100))


class PetsPage(SettingsPage):
    title = "多宠"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._initial = self._snapshot()

        manage_card = self.section("生成与收回")
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("petsList")
        self.list_widget.setAccessibleName("当前桌宠")
        self.list_widget.setMinimumHeight(150)
        manage_card.add_widget(self.list_widget)

        spawn_button = make_button("生成子宠", self)
        spawn_button.clicked.connect(self._spawn)
        remove_button = make_button("收回选中", self)
        remove_button.clicked.connect(self._remove_selected)
        clear_button = make_button("收回所有子宠", self)
        clear_button.clicked.connect(self._clear_secondary)
        manage_card.add_buttons([spawn_button, remove_button, clear_button])

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        manage_card.add_widget(self.status_label)

        size_card = self.section("子宠大小")
        self.inherit_size = make_checkbox(self)
        self.inherit_size.setChecked(bool(self.config().get("spawn_inherit_size", True)))
        self.inherit_size.toggled.connect(self._on_size_changed)
        size_card.add_row(
            "继承主宠大小",
            self.inherit_size,
            "关闭后，新生成的子宠使用下面的独立大小。",
        )
        scale_row = QWidget(self)
        scale_layout = QHBoxLayout(scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(12)
        self.scale_slider = make_slider(scale_row)
        self.scale_spin = make_spin(scale_row)
        self.scale_slider.setRange(_percent(SCALE_MIN), _percent(SCALE_MAX))
        self.scale_spin.setRange(_percent(SCALE_MIN), _percent(SCALE_MAX))
        self.scale_spin.setSuffix(" %")
        value = _percent(float(self.config().get("spawn_scale") or 0.72))
        self.scale_slider.setValue(value)
        self.scale_spin.setValue(value)
        self.scale_slider.valueChanged.connect(self._on_size_changed)
        self.scale_spin.valueChanged.connect(self._on_size_changed)
        scale_layout.addWidget(self.scale_slider, 1)
        scale_layout.addWidget(self.scale_spin)
        size_card.add_row("子宠大小", scale_row, "10%–300%；仅在不继承主宠大小时使用。", expand=True)

        note = self.add_note(
            "子宠的设置从主宠播种；右键菜单只作用于被点击的那只桌宠。"
        )
        note.setObjectName("settingHint")
        self._refresh()

    def config(self):
        return self.host.config

    def _snapshot(self) -> dict:
        return {
            "inherit": bool(self.config().get("spawn_inherit_size", True)),
            "scale": _percent(float(self.config().get("spawn_scale") or 0.72)),
        }

    def _on_size_changed(self, *_args) -> None:
        value = int(self.scale_spin.value())
        if self.scale_slider.value() != value:
            self.scale_slider.blockSignals(True)
            self.scale_slider.setValue(value)
            self.scale_slider.blockSignals(False)
        if self.scale_spin.value() != value:
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(value)
            self.scale_spin.blockSignals(False)
        self.config().set("spawn_inherit_size", self.inherit_size.isChecked())
        self.config().set("spawn_scale", value / 100.0)
        self.changed.emit()

    def _refresh(self) -> None:
        manager = getattr(self.host, "pets", None)
        self.list_widget.clear()
        if manager is None:
            self.status_label.setText("多宠管理器不可用。")
            return
        for window in manager.each():
            text = f"slot {window.slot}"
            if window.is_primary:
                text += " · 主宠"
            else:
                text += " · 子宠"
            text += f" · {window.character_id or '未加载'} · {_percent(window.state.scale)}%"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, window.slot)
            self.list_widget.addItem(item)
        count = len(manager.each())
        self.status_label.setText(
            f"当前 {count} 只（最多 {MAX_PETS} 只）。生小肥鱼会沿用主宠设置生成新的子宠。"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()

    def _spawn(self) -> None:
        manager = getattr(self.host, "pets", None)
        if manager is None:
            return
        window = manager.spawn()
        self.status_label.setText("已生成子宠。" if window is not None else "无法生成子宠（已达上限或没有空闲身份）。")
        self._refresh()

    def _selected_slot(self) -> int | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _remove_selected(self) -> None:
        manager = getattr(self.host, "pets", None)
        slot = self._selected_slot()
        if manager is None or slot is None:
            return
        manager.close_slot(slot)
        self._refresh()

    def _clear_secondary(self) -> None:
        manager = getattr(self.host, "pets", None)
        if manager is None:
            return
        manager.close_secondary()
        self._refresh()

    def dirty(self) -> bool:
        return self._snapshot() != {
            "inherit": self.inherit_size.isChecked(),
            "scale": int(self.scale_spin.value()),
        }

    def confirm_save(self) -> None:
        self._on_size_changed()
        self._initial = self._snapshot()

    def discard_changes(self) -> None:
        for widget in (self.scale_slider, self.scale_spin):
            widget.blockSignals(True)
        self.scale_slider.setValue(self._initial["scale"])
        self.scale_spin.setValue(self._initial["scale"])
        for widget in (self.scale_slider, self.scale_spin):
            widget.blockSignals(False)
        self.inherit_size.blockSignals(True)
        self.inherit_size.setChecked(self._initial["inherit"])
        self.inherit_size.blockSignals(False)
        self.config().set("spawn_inherit_size", self._initial["inherit"])
        self.config().set("spawn_scale", self._initial["scale"] / 100.0)
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "多宠",
            "子宠",
            "生小肥鱼",
            "生成",
            "收回",
            "大小",
            "继承",
            "spawn",
            "pets",
            "multi",
        ]
