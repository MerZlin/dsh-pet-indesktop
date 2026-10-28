from __future__ import annotations

import sys
import threading
from contextlib import ExitStack
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from ...engine.geometry import DEFAULT_SCALE, SCALE_MAX, SCALE_MIN, SCALE_STEPS
from ...platform import autostart, hotkey_spec, screens
from ..external_links import CHARACTER_PACKS_URL, QUARK_URL
from ..rename_dialog import alias_bubble_text, prompt_alias
from .base import (
    SettingsPage,
    build_embedded_page,
    embedded_page_specs,
    make_button,
    make_checkbox,
    make_slider,
    make_spin,
)


def _clamped_scale(value: float) -> float:
    return max(SCALE_MIN, min(SCALE_MAX, float(value)))


def _percent(scale: float) -> int:
    return int(round(_clamped_scale(scale) * 100))


class GeneralPage(SettingsPage):
    title = "常规"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._autostart_supported = sys.platform == "win32"
        self._autostart_initial = False
        if self._autostart_supported:
            try:
                self._autostart_initial = bool(autostart.is_enabled())
            except Exception:
                self._autostart_initial = False
        self._draft_character: str | None = None
        self._draft_autostart = self._autostart_initial

        card = self.section("桌宠")
        self.character_combo = QComboBox(self)
        self.character_combo.setMinimumWidth(180)
        self.rename_character_button = make_button("重命名…", self)
        self.rename_character_button.clicked.connect(self._rename_character)
        character_row = QWidget(self)
        character_layout = QHBoxLayout(character_row)
        character_layout.setContentsMargins(0, 0, 0, 0)
        character_layout.setSpacing(8)
        character_layout.addWidget(self.character_combo)
        character_layout.addWidget(self.rename_character_button)
        self._fill_character_combo()
        self._select_character(str(self.config().get("character") or ""))
        self._initial_character = self._current_character()
        self._initial_aliases = self._aliases()
        self.character_combo.currentIndexChanged.connect(self._on_character_changed)
        card.add_row(
            "角色",
            character_row,
            "选择桌宠当前使用的角色；「重命名…」只改显示名，留空恢复默认。",
        )

        self.autostart_check = make_checkbox(self)
        self.autostart_check.setChecked(self._autostart_initial)
        if not self._autostart_supported:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅在 Windows 上可用。")
        hint = "登录 Windows 后自动运行 petcore。" if self._autostart_supported else "仅在 Windows 上可用。"
        card.add_row("开机自动启动", self.autostart_check, hint)

        # 全屏自动隐藏：全局键（一个共享 watcher 作用所有宠），勾选即时写配置并
        # 立刻作用到宿主；取消/关闭时由 discard_changes() 回滚到打开时的快照。
        self._fullscreen_hide_supported = sys.platform == "win32"
        self._initial_auto_hide_fullscreen = bool(
            self.config().get("auto_hide_fullscreen", True)
        )
        self.auto_hide_fullscreen_check = make_checkbox(self)
        self.auto_hide_fullscreen_check.setChecked(self._initial_auto_hide_fullscreen)
        if not self._fullscreen_hide_supported:
            self.auto_hide_fullscreen_check.setEnabled(False)
            self.auto_hide_fullscreen_check.setToolTip("仅在 Windows 上可用。")
        self.auto_hide_fullscreen_check.toggled.connect(self._on_auto_hide_changed)
        card.add_row(
            "全屏时自动隐藏",
            self.auto_hide_fullscreen_check,
            "全屏游戏或视频期间自动隐藏桌宠。",
        )

        data_card = self.section("数据")
        open_button = make_button("打开数据目录", self)
        open_button.clicked.connect(self._open_data_dir)
        data_card.add_row("数据目录", open_button, "打开保存配置、日志和扩展的目录。")

        import_button = make_button("导入角色目录", self)
        import_button.clicked.connect(self._import_character)
        import_zip_button = make_button("导入角色 ZIP", self)
        import_zip_button.clicked.connect(self._import_character_zip)
        import_row = QWidget(self)
        import_layout = QHBoxLayout(import_row)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(8)
        import_layout.addWidget(import_button)
        import_layout.addWidget(import_zip_button)
        self.import_status = QLabel("支持包含 videos/ 和 manifest.json 的角色目录或 ZIP。", self)
        self.import_status.setObjectName("settingHint")
        self.import_status.setWordWrap(True)
        data_card.add_row(
            "角色资源包",
            import_row,
            "选择角色目录或 ZIP 后生成并安装 content mod；不会覆盖已有角色。",
        )
        data_card.add_widget(self.import_status)
        data_card.add_row(
            "完整角色素材包",
            self._pack_links_row(
                (("GitHub 下载", CHARACTER_PACKS_URL), ("夸克网盘", QUARK_URL))
            ),
            "在浏览器打开素材包下载页；下载后用上面的「导入角色目录 / 导入角色 ZIP」安装。",
        )
        data_card.add_row(
            "高清完整角色素材包",
            self._pack_links_row(
                (("GitHub 下载", CHARACTER_PACKS_URL), ("夸克网盘", QUARK_URL))
            ),
            "高清素材体积更大、首次加载更慢；安装方式同上。",
        )

        app_card = self.section("应用")
        quit_button = make_button("退出桌宠", self)
        quit_button.clicked.connect(self._quit_app)
        app_card.add_row("退出", quit_button, "关闭桌宠、托盘图标与所有扩展。")

    def config(self):
        return self.host.config

    def _pack_links_row(self, pairs) -> QWidget:
        """素材包的横排外链条：每项一个按钮，点了只交给系统浏览器。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for text, url in pairs:
            button = make_button(text, self)
            button.clicked.connect(
                lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target))
            )
            layout.addWidget(button)
        return row

    def _select_character(self, character_id: str) -> None:
        if self.character_combo.count() == 0:
            return
        index = self.character_combo.findData(character_id)
        self.character_combo.setCurrentIndex(index if index >= 0 else 0)

    def _fill_character_combo(self, selected: str | None = None) -> None:
        """按当前别名重建下拉项（文本是「别名 or 目录名」，data 永远是角色 id）。"""
        if selected is None:
            selected = self._current_character()
        self.character_combo.blockSignals(True)
        try:
            self.character_combo.clear()
            for spec in self.host.characters():
                self.character_combo.addItem(str(spec.name), str(spec.id))
            self._select_character(selected or str(self.config().get("character") or ""))
        finally:
            self.character_combo.blockSignals(False)

    def _aliases(self) -> dict[str, str]:
        raw = self.config().get("character_aliases")
        return dict(raw) if isinstance(raw, dict) else {}

    def refresh_characters(self) -> None:
        """别名被别处（右键菜单）改过：刷下拉文本，并把基线推到「已保存的名字」。

        右键重命名是立即落盘的用户动作，保持 dirty() 为假才对得上磁盘状态。
        """
        self._fill_character_combo()
        self._initial_aliases = self._aliases()

    def _character_default_name(self, character_id: str) -> str:
        lookup = getattr(self.host, "character_default_name", None)
        if callable(lookup):
            return str(lookup(character_id) or character_id)
        for spec in self.host.characters():
            if str(spec.id) == character_id:
                return str(spec.name)
        return character_id

    def _rename_character(self) -> None:
        """「重命名…」：与右键同一个输入框，写别名后立即反馈（下拉文本 + 气泡）。

        写法与「开机自启」「全屏自动隐藏」一致：勾/点即写配置，落盘统一等
        confirm_save()，取消/Esc 由 discard_changes() 回到打开时的快照。
        """
        character_id = self._current_character()
        if not character_id:
            return
        default_name = self._character_default_name(character_id)
        result = prompt_alias(
            self,
            default_name=default_name,
            current=self.config().character_alias(character_id) or default_name,
        )
        if not result.accepted:
            return
        self.config().set_character_alias(character_id, result.name)
        self._fill_character_combo(character_id)
        self._say_alias(result, default_name)
        self.changed.emit()

    def _say_alias(self, result, default_name: str) -> None:
        window = getattr(self.host, "window", None)
        say = getattr(window, "say", None)
        if not callable(say):
            return
        try:
            say(alias_bubble_text(result, default_name))
        except Exception:  # noqa: BLE001 - 气泡坏了不许把设置页弄崩
            pass

    def _on_character_changed(self, _index: int) -> None:
        character = self._current_character()
        if character:
            self.config().set("character", character)
        self.changed.emit()

    def _open_data_dir(self) -> None:
        opener = getattr(self.host, "open_data_dir", None)
        if callable(opener):
            opener()

    def _import_character(self) -> None:
        source = QFileDialog.getExistingDirectory(
            self, "选择角色资源目录", str(getattr(self.host, "root", ""))
        )
        if source:
            self._import_character_source(Path(source))

    def _import_character_zip(self) -> None:
        source, _filter = QFileDialog.getOpenFileName(
            self,
            "选择角色 ZIP",
            str(getattr(self.host, "root", "")),
            "角色包 (*.zip)",
        )
        if source:
            self._import_character_source(Path(source))

    def _import_character_source(self, source: Path) -> None:
        try:
            from ...content.importer import install_character_source

            ok, message, character_id = install_character_source(source, self.host.root)
        except Exception as exc:
            ok, message, character_id = False, f"导入失败：{type(exc).__name__}: {exc}", None
        if not ok or not character_id:
            self.import_status.setText(str(message))
            QMessageBox.warning(self, "导入角色", str(message))
            return
        reloader = getattr(self.host, "reload_mods", None)
        if callable(reloader):
            reloader()
        index = self.character_combo.findData(character_id)
        if index < 0:
            for spec in self.host.characters():
                self.character_combo.addItem(str(spec.name), str(spec.id))
            index = self.character_combo.findData(character_id)
        if index >= 0:
            self.character_combo.setCurrentIndex(index)
        self.import_status.setText(f"已导入：{character_id}")

    def _quit_app(self) -> None:
        answer = QMessageBox.question(
            self,
            "退出桌宠",
            "确定要退出桌宠吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        quitter = getattr(self.host, "quit_app", None)
        if callable(quitter):
            quitter()

    def _current_character(self) -> str | None:
        if self.character_combo.count() == 0:
            return None
        data = self.character_combo.currentData()
        return str(data) if data is not None else None

    def _on_auto_hide_changed(self, checked: bool) -> None:
        """勾选即时写配置并作用到宿主（与「直播捕获兼容」同一套实时语义）。"""
        self.config().set("auto_hide_fullscreen", bool(checked))
        self._apply_auto_hide(bool(checked))
        self.changed.emit()

    def _apply_auto_hide(self, enabled: bool) -> None:
        apply = getattr(self.host, "apply_fullscreen_hide", None)
        if not callable(apply):
            return
        try:
            apply(bool(enabled))
        except Exception:  # noqa: BLE001 - 开关坏了不许把设置页弄崩
            pass

    def dirty(self) -> bool:
        if self._current_character() != self._initial_character:
            return True
        if self._aliases() != self._initial_aliases:
            return True
        if (
            self._fullscreen_hide_supported
            and self.auto_hide_fullscreen_check.isChecked()
            != self._initial_auto_hide_fullscreen
        ):
            return True
        return self._autostart_supported and self.autostart_check.isChecked() != self._autostart_initial

    def draft(self) -> None:
        self._draft_character = self._current_character()
        self._draft_autostart = self.autostart_check.isChecked()

    def confirm_save(self) -> None:
        if self._draft_character is not None:
            self.config().set("character", self._draft_character)
        # 别名已经写进配置（点即生效）；这里只把基线推到落盘值，别让同一个名字
        # 在保存之后还挂着 dirty。
        self._initial_aliases = self._aliases()
        if (
            self._fullscreen_hide_supported
            and self.auto_hide_fullscreen_check.isChecked()
            != self._initial_auto_hide_fullscreen
        ):
            self.config().set(
                "auto_hide_fullscreen", self.auto_hide_fullscreen_check.isChecked()
            )
        if not self._autostart_supported or self._draft_autostart == self._autostart_initial:
            return
        try:
            changed = autostart.enable() if self._draft_autostart else autostart.disable()
        except Exception:
            changed = False
        if changed:
            self._autostart_initial = self._draft_autostart
            return
        self.autostart_check.setChecked(self._autostart_initial)
        QMessageBox.warning(self, "开机自动启动", "无法写入开机自启设置，请检查系统权限。")

    def discard_changes(self) -> None:
        # 别名与下拉文本一起回到打开设置时的样子（别名是深拷贝的独立快照）。
        self.config().set("character_aliases", dict(self._initial_aliases))
        self._fill_character_combo(self._initial_character or "")
        self.autostart_check.setChecked(self._autostart_initial)
        # 勾选是实时生效的，取消必须把配置与宿主状态一起回滚（同 stream_capture）。
        self.config().set("auto_hide_fullscreen", self._initial_auto_hide_fullscreen)
        self.auto_hide_fullscreen_check.blockSignals(True)
        self.auto_hide_fullscreen_check.setChecked(self._initial_auto_hide_fullscreen)
        self.auto_hide_fullscreen_check.blockSignals(False)
        self._apply_auto_hide(self._initial_auto_hide_fullscreen)

    def keywords(self) -> list[str]:
        return [
            "常规",
            "角色",
            "重命名",
            "改名",
            "别名",
            "开机",
            "启动",
            "自启",
            "全屏",
            "自动隐藏",
            "游戏",
            "视频",
            "数据目录",
            "角色资源包",
            "素材包",
            "下载",
            "高清",
            "网盘",
            "github",
            "ZIP",
            "退出",
            "general",
            "character",
            "autostart",
            "startup",
            "fullscreen",
            "quit",
        ]


class AppearancePage(SettingsPage):
    title = "外观与交互"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._window = getattr(host, "window", None)
        self._initial_scale = _percent(float(self.config().get("scale") or DEFAULT_SCALE))
        self._initial_always_on_top = bool(self.config().get("always_on_top"))
        self._initial_click_through = bool(self.config().get("click_through"))
        self._initial_multi_screen = bool(self.config().get("multi_screen"))
        self._initial_edge_probe = bool(self.config().get("edge_probe_enabled"))
        self._initial_hotkey = str(self.config().get("hotkey_toggle_pet") or "")
        self._last_hotkey = self._initial_hotkey
        self._initial_ui_font_size = int(self.config().get("ui_font_size") or 13)
        self._initial_bubble_text_scale = int(self.config().get("bubble_text_scale") or 100)
        self._initial_stream_capture = bool(self.config().get("stream_capture_mode"))
        self._initial_visible = self._window_visible()

        # 这一页的内容已经长到四屏：按主分支的设置页内分页切成四块。
        # 用 ExitStack 而不是缩进整个构造体——分页边界一眼可见，也不用重排
        # 200 行控件代码。搜索跨分页仍可命中（`base.reveal_text` 会自动切页）。
        tabs = ExitStack()
        tabs.enter_context(self.tab("size", "大小与外观"))
        scale_card = self.section("大小")
        scale_row = QWidget(self)
        scale_layout = QHBoxLayout(scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(12)
        self.scale_slider = make_slider(scale_row)
        self.scale_slider.setRange(_percent(SCALE_MIN), _percent(SCALE_MAX))
        self.scale_spin = make_spin(scale_row)
        self.scale_spin.setRange(_percent(SCALE_MIN), _percent(SCALE_MAX))
        self.scale_spin.setSuffix(" %")
        self.scale_slider.setValue(self._initial_scale)
        self.scale_spin.setValue(self._initial_scale)
        self.scale_slider.valueChanged.connect(self._on_scale_percent)
        self.scale_spin.valueChanged.connect(self._on_scale_percent)
        scale_layout.addWidget(self.scale_slider, 1)
        scale_layout.addWidget(self.scale_spin)
        scale_card.add_row(
            "缩放",
            scale_row,
            f"拖动滑条或直接输入百分比，范围 {_percent(SCALE_MIN)}%–{_percent(SCALE_MAX)}%，实时生效。",
            expand=True,
        )
        preset_buttons = []
        for step in SCALE_STEPS:
            button = make_button(f"{_percent(step)}%", self)
            button.clicked.connect(lambda _checked=False, value=step: self._apply_scale(value))
            preset_buttons.append(button)
        reset_scale = make_button("恢复默认", self)
        reset_scale.clicked.connect(lambda: self._apply_scale(DEFAULT_SCALE))
        preset_buttons.append(reset_scale)
        scale_card.add_buttons(preset_buttons)

        card = self.section("外观")
        self.always_on_top_check = make_checkbox(self)
        self.always_on_top_check.setChecked(self._initial_always_on_top)
        self.always_on_top_check.toggled.connect(self._on_flags_changed)
        card.add_row("窗口置顶", self.always_on_top_check, "让桌宠始终显示在其他窗口之上。")

        self.click_through_check = make_checkbox(self)
        self.click_through_check.setChecked(self._initial_click_through)
        self.click_through_check.toggled.connect(self._on_flags_changed)
        card.add_row(
            "点击穿透",
            self.click_through_check,
            "影响所有桌宠：鼠标点击穿过桌宠，不再响应拖动与互动；开启后右键菜单点不到，可从托盘菜单关闭。",
        )

        self.stream_capture_check = make_checkbox(self)
        self.stream_capture_check.setChecked(self._initial_stream_capture)
        self.stream_capture_check.toggled.connect(self._on_stream_capture_changed)
        card.add_row(
            "直播捕获兼容",
            self.stream_capture_check,
            "影响所有桌宠，让 OBS / 直播姬能在窗口列表里枚举到桌宠。捕获方式请选 "
            "「Windows 10 (1903 及以上)」，BitBlt 会黑屏；多只桌宠的窗口都可单独捕获。",
        )

        self.visible_check = make_checkbox(self)
        self.visible_check.setChecked(self._initial_visible)
        self.visible_check.setEnabled(self._window is not None)
        self.visible_check.toggled.connect(self._on_visibility_changed)
        card.add_row("显示桌宠", self.visible_check, "取消勾选后桌宠隐藏；可从托盘或本设置重新显示。")

        self.edge_probe_check = make_checkbox(self)
        self.edge_probe_check.setChecked(self._initial_edge_probe)
        self.edge_probe_check.setEnabled(self._window is not None)
        self.edge_probe_check.toggled.connect(self._on_edge_probe_changed)
        card.add_row(
            "边缘探头",
            self.edge_probe_check,
            "贴到屏幕左/右缘后自动 45° 探头；点击拉直，5 秒后回到探头。",
        )

        self.hotkey_edit = QLineEdit(self)
        self.hotkey_edit.setPlaceholderText("例如 Ctrl+Alt+P（留空 = 不绑定）")
        self.hotkey_edit.setText(self._initial_hotkey)
        self.hotkey_edit.setMinimumWidth(220)
        self.hotkey_edit.editingFinished.connect(self._on_hotkey_finished)
        card.add_row(
            "显示/隐藏快捷键",
            self.hotkey_edit,
            "全局快捷键，至少包含一个修饰键；留空表示不绑定。",
        )

        tabs.close()
        tabs.enter_context(self.tab("island", "灵动岛"))
        island_card = self.section("灵动岛")
        self.island_enabled_check = make_checkbox(self)
        self.island_enabled_check.setChecked(bool(self.config().get("island_enabled")))
        self.island_enabled_check.toggled.connect(self._on_island_changed)
        island_card.add_row(
            "启用灵动岛",
            self.island_enabled_check,
            "顶部一枚胶囊，显示时间与 CPU / 内存 / 网络读数；点开有详细卡片。默认关闭。",
        )

        self.island_edge_dock_check = make_checkbox(self)
        self.island_edge_dock_check.setChecked(
            bool(self.config().get("island_edge_dock", True))
        )
        self.island_edge_dock_check.toggled.connect(self._on_island_changed)
        island_card.add_row(
            "贴边停靠",
            self.island_edge_dock_check,
            "拖到屏幕边缘约 40px 内松手会收成细条，悬停滑出预览；关闭后按拖动位置停留。",
        )

        self.island_collision_check = make_checkbox(self)
        self.island_collision_check.setChecked(
            bool(self.config().get("island_collision", True))
        )
        self.island_collision_check.toggled.connect(self._on_island_changed)
        island_card.add_row(
            "与桌宠碰撞",
            self.island_collision_check,
            "桌宠撞到胶囊会被弹开，拖着胶囊扫过桌宠会把它顶飞；关闭后胶囊不再阻挡桌宠"
            "（岛也不再自动让位）。默认开启，改完立即生效。",
        )

        toggle_row = QWidget(self)
        toggle_layout = QHBoxLayout(toggle_row)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(14)
        self.island_name_check = make_checkbox(toggle_row)
        self.island_name_check.setChecked(bool(self.config().get("island_show_name")))
        self.island_name_check.toggled.connect(self._on_island_changed)
        self.island_status_check = make_checkbox(toggle_row)
        self.island_status_check.setChecked(bool(self.config().get("island_show_status")))
        self.island_status_check.toggled.connect(self._on_island_changed)
        for label, box in (
            ("显示角色名", self.island_name_check),
            ("状态点", self.island_status_check),
        ):
            holder = QWidget(toggle_row)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(6)
            holder_layout.addWidget(box)
            holder_layout.addWidget(QLabel(label, holder))
            toggle_layout.addWidget(holder)
        toggle_layout.addStretch(1)
        island_card.add_row(
            "显示元素",
            toggle_row,
            "状态点按最高告警级变色（正常时用强调色）；角色名过长时右省略。",
            expand=True,
        )

        self.island_messages_check = make_checkbox(self)
        self.island_messages_check.setChecked(
            bool(self.config().get("island_show_messages", False))
        )
        self.island_messages_check.toggled.connect(self._on_island_changed)
        island_card.add_row(
            "消息显示",
            self.island_messages_check,
            "开启后展开卡片列出最近 8 条气泡消息，并可一键清除；"
            "关闭时不记录也不显示历史，消息引发的胶囊果冻也一并停掉；读数不受影响。默认关闭。",
        )

        info_row = QWidget(self)
        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(10)
        self.island_info_combo = QComboBox(info_row)
        self.island_info_combo.addItem("时间", "time")
        self.island_info_combo.addItem("自定义文字", "custom")
        mode = str(self.config().get("island_info_mode") or "time")
        self.island_info_combo.setCurrentIndex(1 if mode == "custom" else 0)
        self.island_info_combo.currentIndexChanged.connect(self._on_island_changed)
        self.island_info_text = QLineEdit(info_row)
        self.island_info_text.setPlaceholderText("显示在胶囊左侧的文字（≤80 字）")
        self.island_info_text.setText(str(self.config().get("island_custom_text") or ""))
        self.island_info_text.setMinimumWidth(200)
        self.island_info_text.editingFinished.connect(self._on_island_changed)
        info_layout.addWidget(self.island_info_combo)
        info_layout.addWidget(self.island_info_text, 1)
        island_card.add_row(
            "信息位",
            info_row,
            "时间档是 HH:mm 定宽形态（读数不改宽度）；自定义档超出时右省略。",
            expand=True,
        )

        metrics_row = QWidget(self)
        metrics_layout = QHBoxLayout(metrics_row)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(14)
        current_metrics = list(self.config().get("island_metrics") or [])
        self.island_metric_boxes = {}
        for metric_id, label in (
            ("network", "网络速率"),
            ("cpu", "CPU"),
            ("mem", "内存"),
            ("disk", "C 盘剩余"),
            ("gpu", "GPU"),
            ("fps", "帧率"),
        ):
            box = make_checkbox(metrics_row)
            box.setChecked(metric_id in current_metrics)
            box.toggled.connect(self._on_island_changed)
            holder = QWidget(metrics_row)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(6)
            holder_layout.addWidget(box)
            holder_layout.addWidget(QLabel(label, holder))
            metrics_layout.addWidget(holder)
            self.island_metric_boxes[metric_id] = box
        metrics_layout.addStretch(1)
        island_card.add_row(
            "指标段",
            metrics_row,
            "勾选项按此顺序排在信息位之后；屏幕放不下时先折进「+N」，折叠即停采样。"
            "C 盘剩余 5 秒采一次（只读容量，不扫盘）；GPU 走 PDH（无需权限）；"
            "帧率要读 ETW，需要一次授权。",
            expand=True,
        )

        fps_perm_row = QWidget(self)
        fps_perm_layout = QHBoxLayout(fps_perm_row)
        fps_perm_layout.setContentsMargins(0, 0, 0, 0)
        fps_perm_layout.setSpacing(10)
        self.fps_permission_label = QLabel("", fps_perm_row)
        self.fps_permission_label.setObjectName("settingHint")
        self.fps_permission_label.setWordWrap(True)
        self.fps_permission_button = make_button("授予帧率权限", fps_perm_row)
        self.fps_permission_button.clicked.connect(self._request_fps_permission)
        fps_perm_layout.addWidget(self.fps_permission_label, 1)
        fps_perm_layout.addWidget(self.fps_permission_button)
        island_card.add_row(
            "游戏帧率权限",
            fps_perm_row,
            "把当前账户加入「Performance Log Users」只在本机生效，需要重新登录后才读数；"
            "没授权时帧率段显示原因、不显示 0。",
            expand=True,
        )
        self._refresh_fps_permission()

        style_row = QWidget(self)
        style_layout = QHBoxLayout(style_row)
        style_layout.setContentsMargins(0, 0, 0, 0)
        style_layout.setSpacing(10)
        self.island_style_combo = QComboBox(style_row)
        for label, value in (("深色", "dark"), ("浅色", "light"), ("玻璃", "glass")):
            self.island_style_combo.addItem(label, value)
        styles = ["dark", "light", "glass"]
        current_style = str(self.config().get("island_style") or "dark")
        self.island_style_combo.setCurrentIndex(
            styles.index(current_style) if current_style in styles else 0
        )
        self.island_style_combo.currentIndexChanged.connect(self._on_island_changed)
        self.island_accent_combo = QComboBox(style_row)
        for label, value in (
            ("蓝", "blue"),
            ("绿", "green"),
            ("紫", "purple"),
            ("粉", "pink"),
            ("橙", "orange"),
        ):
            self.island_accent_combo.addItem(label, value)
        accents = ["blue", "green", "purple", "pink", "orange"]
        current_accent = str(self.config().get("island_accent") or "blue")
        self.island_accent_combo.setCurrentIndex(
            accents.index(current_accent) if current_accent in accents else 0
        )
        self.island_accent_combo.currentIndexChanged.connect(self._on_island_changed)
        style_layout.addWidget(self.island_style_combo)
        style_layout.addWidget(self.island_accent_combo)
        style_layout.addStretch(1)
        island_card.add_row(
            "配色与强调色",
            style_row,
            "胶囊自己决定深浅色，不跟随系统主题；强调色用于状态点与卡片强调。",
            expand=True,
        )

        self.island_opacity_slider = make_slider(self)
        self.island_opacity_slider.setRange(40, 100)
        try:
            opacity = int(round(float(self.config().get("island_opacity") or 1.0) * 100))
        except (TypeError, ValueError):
            opacity = 100
        self.island_opacity_slider.setValue(max(40, min(100, opacity)))
        self.island_opacity_slider.valueChanged.connect(self._on_island_changed)
        island_card.add_row(
            "不透明度",
            self.island_opacity_slider,
            "40%–100%；只影响胶囊底板，文字始终不透明。",
            expand=True,
        )

        self.island_click_combo = QComboBox(self)
        self.island_click_combo.addItem("展开卡片", "expand")
        self.island_click_combo.addItem("显示/隐藏桌宠", "toggle_pet")
        actions = ["expand", "toggle_pet"]
        current_action = str(self.config().get("island_click_action") or "expand")
        self.island_click_combo.setCurrentIndex(
            actions.index(current_action) if current_action in actions else 0
        )
        self.island_click_combo.currentIndexChanged.connect(self._on_island_changed)
        island_card.add_row(
            "单击胶囊",
            self.island_click_combo,
            "默认展开详细卡片；选「显示/隐藏桌宠」时点胶囊等同于切换桌宠可见。",
        )

        tabs.close()
        tabs.enter_context(self.tab("text", "文字与快捷键"))
        ai_card = self.section("AI 快捷键")
        self.hotkey_open_chat = self._ai_hotkey_row(
            ai_card, "AI 对话快捷键", "hotkey_open_chat", "open_chat"
        )
        self.hotkey_look_screen = self._ai_hotkey_row(
            ai_card, "看看屏幕快捷键", "hotkey_look_screen", "look_screen"
        )
        self.hotkey_screen_capture = self._ai_hotkey_row(
            ai_card, "截图提问快捷键", "hotkey_screen_capture", "screen_capture"
        )
        privacy = QLabel("截图只读取当前屏幕并直接发给模型，不写入磁盘。", ai_card)
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color:#8b93a1;")
        ai_card.add_row("截图隐私", privacy, "需要 AI 扩展声明截屏能力，未安装时快捷键会明确提示。")

        text_card = self.section("文字")
        self.ui_font_size_spin = make_spin(self)
        self.ui_font_size_spin.setAccessibleName("界面字号")
        self.ui_font_size_spin.setRange(10, 18)
        self.ui_font_size_spin.setValue(self._initial_ui_font_size)
        self.ui_font_size_spin.valueChanged.connect(self._on_text_sizes_changed)
        text_card.add_row(
            "界面字号",
            self.ui_font_size_spin,
            "全局设置：同步调整设置页与所有桌宠的右键菜单字号。",
        )

        self.bubble_text_scale_spin = make_spin(self)
        self.bubble_text_scale_spin.setAccessibleName("对话气泡字号")
        self.bubble_text_scale_spin.setRange(50, 300)
        self.bubble_text_scale_spin.setSuffix(" %")
        self.bubble_text_scale_spin.setValue(self._initial_bubble_text_scale)
        self.bubble_text_scale_spin.valueChanged.connect(self._on_text_sizes_changed)
        text_card.add_row(
            "对话气泡字号",
            self.bubble_text_scale_spin,
            "气泡与文字一起等比放大。",
        )

        tabs.close()
        tabs.enter_context(self.tab("screen", "屏幕与位置"))
        screen_card = self.section("屏幕与位置")
        self.multi_screen_check = make_checkbox(self)
        self.multi_screen_check.setChecked(self._initial_multi_screen)
        self.multi_screen_check.toggled.connect(self._on_flags_changed)
        screen_card.add_row("允许跨屏", self.multi_screen_check, "允许把桌宠拖到其他显示器；关闭时限制在当前屏幕。")
        self.screen_label = QLabel(self._screen_text(), self)
        self.screen_label.setObjectName("screenInfo")
        self.screen_label.setWordWrap(True)
        screen_card.add_widget(self.screen_label)

        primary_button = make_button("重置到主屏", self)
        primary_button.clicked.connect(self._move_to_primary)
        corner_button = make_button("回到右下角", self)
        corner_button.clicked.connect(self._return_corner)
        reset_button = make_button("重置位置", self)
        reset_button.clicked.connect(self._reset_position)
        screen_card.add_buttons([primary_button, corner_button, reset_button])
        tabs.close()

    def config(self):
        return self.host.config

    def _window_visible(self) -> bool:
        window = self._window
        checker = getattr(window, "isVisible", None)
        return bool(checker()) if callable(checker) else True

    def _sync_scale_widgets(self, percent: int) -> None:
        if self.scale_slider.value() != percent:
            self.scale_slider.blockSignals(True)
            self.scale_slider.setValue(percent)
            self.scale_slider.blockSignals(False)
        if self.scale_spin.value() != percent:
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(percent)
            self.scale_spin.blockSignals(False)

    def _apply_scale(self, scale: float) -> None:
        percent = _percent(scale)
        self._sync_scale_widgets(percent)
        self.config().set("scale", percent / 100.0)
        self.changed.emit()

    def _on_scale_percent(self, percent: int) -> None:
        self._apply_scale(percent / 100.0)

    def _refresh_fps_permission(self) -> None:
        label = getattr(self, "fps_permission_label", None)
        button = getattr(self, "fps_permission_button", None)
        if label is None or button is None:
            return
        from ...platform import fps_permission

        try:
            state = fps_permission.check()
        except Exception:  # noqa: BLE001 - 探测失败按「不支持」呈现
            state = "unsupported"
        labels = {
            "active": "已授权：帧率读数可用。",
            "pending_relogon": "已加入「Performance Log Users」，重新登录后生效。",
            "missing": "尚未授权：帧率段只会显示原因。",
            "unsupported": "当前平台不支持帧率采集。",
        }
        label.setText(labels.get(str(state), str(state)))
        button.setEnabled(str(state) not in ("active", "pending_relogon", "unsupported"))

    def _request_fps_permission(self) -> None:
        """一次性 UAC 授权（后台线程等，不能让 UAC 阻塞设置窗）。"""
        from ...platform import fps_permission

        label = self.fps_permission_label
        button = self.fps_permission_button
        button.setEnabled(False)
        label.setText("已弹出系统授权窗口，请在 UAC 里确认；确认后需要重新登录才生效。")
        outcomes = {
            "ok": "已加入「Performance Log Users」，重新登录后帧率可用。",
            "cancelled": "已取消授权。",
            "unsupported": "当前平台不支持帧率采集。",
            "unknown": "等待授权超时，请稍后重新查看状态。",
        }

        def worker() -> None:
            try:
                result = fps_permission.request_access()
                text = outcomes.get(str(getattr(result, "outcome", "")), "") or str(
                    getattr(result, "detail", "") or "授权结果未知"
                )
            except Exception:  # noqa: BLE001 - UAC 拉不起来必须可见但不崩设置
                text = "授权失败：请手动把当前账户加入「Performance Log Users」。"

            def apply() -> None:
                label.setText(text)
                self._refresh_fps_permission()
                if label.text().startswith(("已加入", "已授权")):
                    label.setText(text)

            scheduler = getattr(self.host, "scheduler", None)
            if scheduler is not None:
                scheduler.call_later(0, apply)
            else:
                apply()

        threading.Thread(target=worker, name="petcore-fps-grant", daemon=True).start()

    def _island_metrics(self) -> list[str]:
        order = ["network", "cpu", "mem", "disk", "gpu", "fps"]
        return [
            metric_id
            for metric_id in order
            if metric_id in self.island_metric_boxes
            and self.island_metric_boxes[metric_id].isChecked()
        ]

    def _on_island_changed(self, _value=None) -> None:
        config = self.config()
        config.set("island_enabled", self.island_enabled_check.isChecked())
        config.set("island_show_name", self.island_name_check.isChecked())
        config.set("island_show_status", self.island_status_check.isChecked())
        config.set("island_show_messages", self.island_messages_check.isChecked())
        config.set("island_info_mode", self.island_info_combo.currentData() or "time")
        config.set("island_custom_text", self.island_info_text.text().strip())
        config.set("island_metrics", self._island_metrics())
        config.set("island_edge_dock", self.island_edge_dock_check.isChecked())
        config.set("island_collision", self.island_collision_check.isChecked())
        config.set("island_style", self.island_style_combo.currentData() or "dark")
        config.set("island_accent", self.island_accent_combo.currentData() or "blue")
        config.set("island_opacity", round(self.island_opacity_slider.value() / 100.0, 2))
        config.set(
            "island_click_action", self.island_click_combo.currentData() or "expand"
        )
        self.changed.emit()

    def _on_flags_changed(self, _checked: bool = False) -> None:
        config = self.config()
        config.set("always_on_top", self.always_on_top_check.isChecked())
        config.set("click_through", self.click_through_check.isChecked())
        config.set("multi_screen", self.multi_screen_check.isChecked())
        self.changed.emit()

    def _on_visibility_changed(self, visible: bool) -> None:
        window = self._window
        if window is None:
            return
        setter = getattr(window, "set_visible", None)
        if callable(setter):
            setter(bool(visible))
            return
        if bool(window.isVisible()) != bool(visible):
            window.toggle_hidden()

    def _on_edge_probe_changed(self, enabled: bool) -> None:
        window = self._window
        self.config().set("edge_probe_enabled", bool(enabled))
        if window is not None:
            setter = getattr(window, "set_edge_probe_enabled", None)
            if callable(setter):
                setter(bool(enabled), persist=False)
        self.changed.emit()

    def _on_stream_capture_changed(self, enabled: bool) -> None:
        self.config().set("stream_capture_mode", bool(enabled))
        self._apply_stream_capture(bool(enabled))
        self.changed.emit()

    def _apply_stream_capture(self, enabled: bool) -> None:
        pets = getattr(self.host, "pets", None)
        windows = pets.each() if pets is not None else [self._window]
        for window in windows:
            if window is None:
                continue
            setter = getattr(window, "set_stream_capture_mode", None)
            if callable(setter):
                setter(bool(enabled), persist=False)

    def _ai_hotkey_row(self, card, label: str, key: str, kind: str):
        edit = QLineEdit(self)
        edit.setPlaceholderText("例如 Ctrl+Alt+K（留空 = 不绑定）")
        edit.setText(str(self.config().get(key) or ""))
        edit.setMinimumWidth(220)
        edit.editingFinished.connect(
            lambda e=edit, k=key, n=kind: self._on_ai_hotkey_finished(e, k, n)
        )
        card.add_row(
            label,
            edit,
            "全局快捷键，至少包含一个修饰键；留空表示不绑定。",
        )
        return edit

    def _on_ai_hotkey_finished(self, edit: QLineEdit, key: str, kind: str) -> None:
        raw = edit.text().strip()
        normalized = hotkey_spec.normalize(raw)
        if raw and not normalized:
            edit.setText(str(self.config().get(key) or ""))
            QMessageBox.warning(
                self,
                "快捷键",
                "无法识别这个组合键；请使用类似 Ctrl+Alt+K 的写法，至少包含一个修饰键。",
            )
            return
        edit.setText(normalized)
        self.config().set(key, normalized)
        applier = getattr(self.host, "set_hotkey", None)
        if callable(applier):
            try:
                applier(normalized, kind=kind)
            except TypeError:
                pass
        self.changed.emit()

    def _on_hotkey_finished(self) -> None:
        raw = self.hotkey_edit.text().strip()
        normalized = hotkey_spec.normalize(raw)
        if raw and not normalized:
            self.hotkey_edit.setText(self._last_hotkey)
            QMessageBox.warning(
                self,
                "快捷键",
                "无法识别这个组合键；请使用类似 Ctrl+Alt+P 的写法，至少包含一个修饰键。",
            )
            return
        self.hotkey_edit.setText(normalized)
        self._last_hotkey = normalized
        self.config().set("hotkey_toggle_pet", normalized)
        applier = getattr(self.host, "set_hotkey", None)
        if callable(applier):
            applier(normalized)
        self.changed.emit()

    def _on_text_sizes_changed(self, _value: int = 0) -> None:
        config = self.config()
        font_size = int(self.ui_font_size_spin.value())
        bubble_scale = int(self.bubble_text_scale_spin.value())
        config.set("ui_font_size", font_size)
        config.set("bubble_text_scale", bubble_scale)
        self._sync_bubble_scale(bubble_scale)
        self.changed.emit()

    def _sync_bubble_scale(self, value: int) -> None:
        """气泡已经显示时立即重排；没有这个接缝就只写配置。"""
        setter = getattr(self._window, "set_text_scale", None)
        if callable(setter):
            setter(int(value), persist=False)

    def _screen_text(self) -> str:
        try:
            layout = screens.snapshot_from_qt()
        except Exception:
            return "当前屏幕：不可用"
        screen = layout.by_name(layout.primary) or (layout.screens[0] if layout.screens else None)
        if screen is None:
            return "当前屏幕：不可用"
        return f"当前屏幕：{screen.name} · {screen.width} × {screen.height} · DPR {screen.dpr:g}"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.screen_label.setText(self._screen_text())
        self.visible_check.blockSignals(True)
        self.visible_check.setChecked(self._window_visible())
        self.visible_check.blockSignals(False)

    def _move_to_primary(self) -> None:
        window = self._window
        if window is not None:
            window.move_to_primary()

    def _return_corner(self) -> None:
        window = self._window
        if window is not None:
            window.return_corner()

    def _reset_position(self) -> None:
        window = self._window
        if window is not None:
            window.reset_position()

    def _current_percent(self) -> int:
        return int(self.scale_spin.value())

    def dirty(self) -> bool:
        if self._current_percent() != self._initial_scale:
            return True
        if int(self.ui_font_size_spin.value()) != self._initial_ui_font_size:
            return True
        if int(self.bubble_text_scale_spin.value()) != self._initial_bubble_text_scale:
            return True
        return (
            self.always_on_top_check.isChecked() != self._initial_always_on_top
            or self.click_through_check.isChecked() != self._initial_click_through
            or self.stream_capture_check.isChecked() != self._initial_stream_capture
            or self.multi_screen_check.isChecked() != self._initial_multi_screen
            or self.edge_probe_check.isChecked() != self._initial_edge_probe
            or self.hotkey_edit.text().strip() != self._initial_hotkey
        )

    def draft(self) -> None:
        return None

    def confirm_save(self) -> None:
        config = self.config()
        config.set("scale", self._current_percent() / 100.0)
        config.set("always_on_top", self.always_on_top_check.isChecked())
        config.set("click_through", self.click_through_check.isChecked())
        config.set("multi_screen", self.multi_screen_check.isChecked())
        config.set("stream_capture_mode", self.stream_capture_check.isChecked())
        config.set("edge_probe_enabled", self.edge_probe_check.isChecked())
        config.set("hotkey_toggle_pet", self.hotkey_edit.text().strip())
        config.set("ui_font_size", int(self.ui_font_size_spin.value()))
        config.set("bubble_text_scale", int(self.bubble_text_scale_spin.value()))

    def discard_changes(self) -> None:
        for widget in (self.scale_slider, self.scale_spin):
            widget.blockSignals(True)
        self.scale_slider.setValue(self._initial_scale)
        self.scale_spin.setValue(self._initial_scale)
        for widget in (self.scale_slider, self.scale_spin):
            widget.blockSignals(False)
        for spin, value in (
            (self.ui_font_size_spin, self._initial_ui_font_size),
            (self.bubble_text_scale_spin, self._initial_bubble_text_scale),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.config().set("ui_font_size", self._initial_ui_font_size)
        self.config().set("bubble_text_scale", self._initial_bubble_text_scale)
        self.config().set("stream_capture_mode", self._initial_stream_capture)
        self._sync_bubble_scale(self._initial_bubble_text_scale)
        for checkbox, value in (
            (self.always_on_top_check, self._initial_always_on_top),
            (self.click_through_check, self._initial_click_through),
            (self.stream_capture_check, self._initial_stream_capture),
            (self.multi_screen_check, self._initial_multi_screen),
            (self.edge_probe_check, self._initial_edge_probe),
            (self.visible_check, self._initial_visible),
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(value)
            checkbox.blockSignals(False)
        self._apply_stream_capture(self._initial_stream_capture)
        self.hotkey_edit.setText(self._initial_hotkey)
        self._last_hotkey = self._initial_hotkey
        applier = getattr(self.host, "set_hotkey", None)
        if callable(applier):
            applier(self._initial_hotkey, persist=False)
        if self._window is not None:
            setter = getattr(self._window, "set_edge_probe_enabled", None)
            if callable(setter):
                setter(self._initial_edge_probe, persist=False)

    def keywords(self) -> list[str]:
        return [
            "外观",
            "交互",
            "缩放",
            "大小",
            "滑条",
            "百分比",
            "300",
            "置顶",
            "穿透",
            "显示",
            "隐藏",
            "跨屏",
            "屏幕",
            "位置",
            "右下角",
            "边缘",
            "探头",
            "字号",
            "字体",
            "文字",
            "气泡",
            "直播",
            "OBS",
            "捕获",
            "scale",
            "appearance",
            "screen",
            "position",
            "font",
            "bubble",
        ]


class MenuPage(SettingsPage):
    title = "菜单"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.model = getattr(host, "menu_model", None)
        self.editor = None
        self._initial_animation_icons = self._animation_icons_enabled()
        embedded = []
        for spec in embedded_page_specs(host, "menu"):
            page = build_embedded_page(spec, host)
            if page is not None:
                embedded.append((spec, page))
        if embedded:
            with self.tab("layout", "菜单编排"):
                self._build_animation_section()
                self._build_layout_section()
            for spec, page in embedded:
                self.embed_page(page, str(spec.id), str(spec.title))
        else:
            self._build_animation_section()
            self._build_layout_section()

    def _animation_icons_enabled(self) -> bool:
        config = getattr(self.host, "config", None)
        if config is None:
            return True
        value = config.get("menu_animation_icons", True)
        return True if value is None else bool(value)

    def _build_animation_section(self) -> None:
        card = self.section("播放动画")
        self.animation_icons_check = make_checkbox(self)
        self.animation_icons_check.setChecked(self._initial_animation_icons)
        self.animation_icons_check.toggled.connect(self._on_animation_icons_toggled)
        card.add_row(
            "播放动画显示预览",
            self.animation_icons_check,
            "在「播放动画」子菜单里为每个动作显示缩略图；动画很多时首次展开会稍慢。",
        )

    def _on_animation_icons_toggled(self, checked: bool) -> None:
        config = getattr(self.host, "config", None)
        if config is not None:
            config.set("menu_animation_icons", bool(checked))
        self.changed.emit()

    def _build_layout_section(self) -> None:
        if self.model is None:
            self.add_note("菜单模型不可用，无法编辑右键菜单。")
            return
        self.add_note(
            "拖动调整顺序或移入子菜单；勾选控制显示；可插入分割线、新建子菜单、改别名与图标。"
        )
        card = self.section("菜单编排")
        from .menu_editor import MenuEditor

        self.editor = MenuEditor(self.host, self.model, self)
        self.editor.setAccessibleName("菜单编排")
        card.add_widget(self.editor)

    def dirty(self) -> bool:
        if self.animation_icons_check.isChecked() != self._initial_animation_icons:
            return True
        if self.editor is not None and self.editor.is_dirty():
            return True
        return self._embedded_dirty()

    def draft(self) -> None:
        return None

    def confirm_save(self) -> None:
        config = getattr(self.host, "config", None)
        if config is not None:
            config.set("menu_animation_icons", bool(self.animation_icons_check.isChecked()))
        if self.editor is not None and self.editor.is_dirty():
            self.editor.confirm_save()
        self._embedded_confirm_save()

    def discard_changes(self) -> None:
        self.animation_icons_check.blockSignals(True)
        self.animation_icons_check.setChecked(self._initial_animation_icons)
        self.animation_icons_check.blockSignals(False)
        config = getattr(self.host, "config", None)
        if config is not None:
            config.set("menu_animation_icons", self._initial_animation_icons)
        if self.editor is not None:
            self.editor.discard()
        self._embedded_discard()

    def keywords(self) -> list[str]:
        return [
            "菜单",
            "编排",
            "显示",
            "隐藏",
            "排序",
            "子菜单",
            "分割线",
            "图标",
            "右键",
            "快捷启动",
            "启动",
            "应用",
            "播放动画",
            "预览",
            "缩略图",
            "menu",
            "layout",
            "order",
            "animation",
            "thumbnail",
            *self._embedded_keywords(),
        ]
