from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QWidget,
)

from ...config import MAX_PETS
from ...content.sprite_pack import build_sprite_pack, character_id_for
from ...engine.geometry import SCALE_MAX, SCALE_MIN
from .base import SettingsPage, make_button, make_checkbox, make_slider, make_spin


#: 下载线程不能挂在设置页上：用户关窗时线程可能还在跑，父对象销毁会崩。
_ACTIVE_DOWNLOADS: set = set()


def _percent(scale: float) -> int:
    return int(round(max(SCALE_MIN, min(SCALE_MAX, float(scale))) * 100))


class _AboutmeDownloadJob(QThread):
    progress = Signal(int, int, str)
    done = Signal(bool, str, str)

    def __init__(self, pack_id: str, dest: Path, parent=None) -> None:
        super().__init__(parent)
        self.pack_id = pack_id
        self.dest = Path(dest)

    def run(self) -> None:
        from ...net.aboutme_packs import download_pack

        ok, message, path = download_pack(
            self.pack_id,
            self.dest,
            progress=lambda done, total, label: self.progress.emit(
                int(done), int(total), str(label)
            ),
        )
        self.done.emit(bool(ok), str(message), str(path or ""))


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
        pack_card = self.section("角色素材包")
        self.pack_status = QLabel(
            "支持 aboutme-pets 的 WebP 精灵表；素材按非商业/署名条款分发，"
            "不随安装包分发。",
            self,
        )
        self.pack_status.setObjectName("settingHint")
        self.pack_status.setWordWrap(True)
        self.import_pack_button = make_button("导入 WebP 精灵表…", self)
        self.import_pack_button.clicked.connect(self._import_sprite_pack)
        self.download_pack_button = make_button("下载 aboutme 角色…", self)
        self.download_pack_button.clicked.connect(self._show_download_menu)
        self.source_button = make_button("素材来源", self)
        self.source_button.setToolTip("打开 aboutme-pets 素材仓库")
        self.source_button.clicked.connect(self._open_sprite_source)
        self.spawn_character_button = make_button("用新角色生成子宠", self)
        self.spawn_character_button.setEnabled(False)
        self.spawn_character_button.clicked.connect(self._spawn_last_character)
        pack_card.add_buttons(
            [
                self.import_pack_button,
                self.download_pack_button,
                self.source_button,
                self.spawn_character_button,
            ]
        )
        pack_card.add_widget(self.pack_status)
        self._last_character_id = ""
        self._download_job = None
        self._disposed = False
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

    # ------------------------------------------------------------ 角色素材包
    def _set_pack_status(self, text: str) -> None:
        self.pack_status.setText(str(text))

    def _import_sprite_pack(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择 aboutme-pets 单角色目录",
            str(Path.home()),
        )
        if folder:
            self._install_sprite_pack(Path(folder))

    def _install_sprite_pack(self, source: Path) -> None:
        source = Path(source)
        try:
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            raw_id = character_id_for(
                str(manifest.get("id") or source.name)
            )
        except (OSError, ValueError):
            QMessageBox.warning(
                self,
                "导入角色包",
                "这个目录不像 aboutme-pets 角色包：缺少可读的 manifest.json。",
            )
            return
        if not raw_id:
            QMessageBox.warning(self, "导入角色包", "manifest.json 里的角色 id 不合法。")
            return
        mods_root = Path(self.host.root) / "mods"
        out = mods_root / f"character.{raw_id}"
        ok, message = build_sprite_pack(source, out, character_id=raw_id)
        if not ok:
            QMessageBox.warning(self, "导入角色包", message)
            self._set_pack_status(message)
            return
        self._last_character_id = raw_id
        self.spawn_character_button.setEnabled(True)
        reload_mods = getattr(self.host, "reload_mods", None)
        if callable(reload_mods):
            reload_mods()
        self._set_pack_status(
            f"{message}。可在「常规 → 角色」或右键「切换角色」里选择；"
            "也可以点「用新角色生成子宠」。"
        )

    def _open_sprite_source(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/hunmer/aboutme-pets"))

    def _show_download_menu(self) -> None:
        try:
            from ...net.aboutme_packs import packs
        except Exception as exc:  # noqa: BLE001
            self._set_pack_status(f"下载索引不可用：{type(exc).__name__}")
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for pack in packs():
            name = str(pack.get("name") or pack.get("id") or "")
            action = menu.addAction(name)
            pet_dir = str(pack.get("pet_dir") or "").strip("/")
            if pet_dir:
                action.setToolTip(
                    f"https://github.com/hunmer/aboutme-pets/tree/main/{pet_dir}"
                )
            action.triggered.connect(
                lambda _checked=False, pid=str(pack.get("id") or ""): self._confirm_download(pid)
            )
        if not menu.actions():
            self._set_pack_status("下载索引为空。")
            return
        menu.exec(
            self.download_pack_button.mapToGlobal(
                self.download_pack_button.rect().bottomLeft()
            )
        )

    def _confirm_download(self, pack_id: str) -> None:
        from ...net.aboutme_packs import pack_for

        pack = pack_for(pack_id)
        if pack is None:
            return
        size = sum(int(item.get("size") or 0) for item in pack.get("files") or [])
        answer = QMessageBox.question(
            self,
            "下载角色包",
            f"下载「{pack.get('name') or pack_id}」？\n\n"
            "许可：CC BY-NC-SA 4.0 / 禁止商用；二创须署名（见包内 NOTICE）。\n"
            f"只下载必要精灵表，约 {size / 1024 / 1024:.1f} MB；不随安装包分发。\n"
            "来源：https://github.com/hunmer/aboutme-pets",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_download(pack_id)

    def _start_download(self, pack_id: str) -> None:
        if self._download_job is not None and self._download_job.isRunning():
            return
        dest = Path(self.host.root) / "downloads" / "aboutme" / pack_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        self.download_pack_button.setEnabled(False)
        self._set_pack_status("正在下载…")
        job = _AboutmeDownloadJob(pack_id, dest, None)
        _ACTIVE_DOWNLOADS.add(job)
        job.finished.connect(lambda: _ACTIVE_DOWNLOADS.discard(job))
        job.finished.connect(job.deleteLater)
        job.progress.connect(self._on_download_progress)
        job.done.connect(self._on_download_done)
        self._download_job = job
        job.start()

    def _on_download_progress(self, done: int, total: int, label: str) -> None:
        if self._disposed:
            return
        total = max(1, int(total or 1))
        self._set_pack_status(
            f"下载 {label} · {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB"
        )

    def _on_download_done(self, ok: bool, message: str, path: str) -> None:
        if self._disposed:
            # 设置在下载途中被关掉：线程仍由 _ACTIVE_DOWNLOADS 持有，回调不许碰
            # 已销毁的页面（QMessageBox 的嵌套事件循环会触发 deleteLater 崩溃）。
            if path:
                shutil.rmtree(Path(path), ignore_errors=True)
            return
        self.download_pack_button.setEnabled(True)
        job = self._download_job
        self._download_job = None
        if job is not None:
            _ACTIVE_DOWNLOADS.discard(job)
        if not ok:
            self._set_pack_status(message)
            QMessageBox.warning(self, "下载角色包", message)
            return
        self._install_sprite_pack(Path(path))
        if path:
            shutil.rmtree(Path(path), ignore_errors=True)

    def _spawn_last_character(self) -> None:
        manager = getattr(self.host, "pets", None)
        if manager is None or not self._last_character_id:
            return
        window = manager.spawn(character_id=self._last_character_id)
        if window is None:
            self._set_pack_status("生成子宠失败：可能已达上限。")
            return
        config = getattr(window, "config", None)
        if config is not None:
            try:
                config.set("character", self._last_character_id)
                config.save()
            except OSError:
                pass
        self._refresh()
        self._set_pack_status(
            f"已生成子宠：{self._last_character_id}；下次启动会保留这个角色。"
        )

    def dispose(self) -> None:
        # 线程由 _ACTIVE_DOWNLOADS 持有；页面先走，下载完成后的信号自然断开。
        self._disposed = True
        self._download_job = None
        super().dispose()

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
            "WebP",
            "精灵表",
            "aboutme",
            "角色包",
            "下载",
            "来源",
            "仓库",
        ]
