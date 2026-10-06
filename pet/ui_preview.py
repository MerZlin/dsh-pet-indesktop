"""Reversible visual previews shared between settings and the pet process."""

from __future__ import annotations
import copy, json, logging, os, time, uuid
from pathlib import Path
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMenu

VISUAL = frozenset(
    (
        "settings_ui_style",
        "menu_ui_style",
        "bubble_ui_style",
        "quota_ui_style",
        "quick_chat_ui_style",
        "chat_window_ui_style",
        "self_talk_bubble_style",
        "bubble_text_scale",
        "scale",
        "pet_opacity",
        "playback_speed",
        "ui_language",
        "dynamic_island",
        "context_menu_appearance",
    )
)
_committing = False
_runtime = None


def preview_path(config):
    return Path(config.path).parent / "ui-preview.json"


def active_values(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if 0 <= time.time() - float(value["updatedAt"]) < 3.5:
            return {k: v for k, v in value["values"].items() if k in VISUAL}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def merge(config, values):
    for key, value in values.items():
        if key in ("dynamic_island", "context_menu_appearance"):
            existing = copy.deepcopy(config.data.get(key, {}))
            existing.update(copy.deepcopy(value))
            config.data[key] = existing
        else:
            config.data[key] = copy.deepcopy(value)


def overlay_config(config):
    if not _committing:
        merge(config, active_values(preview_path(config)))


def guarded_save(config, original):
    if not _committing and active_values(preview_path(config)):
        return True
    return original(config)


class SettingsPreview(QObject):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.path = preview_path(dialog.config)
        self.owner = uuid.uuid4().hex
        self.dirty = False
        self.saved = False
        self.closed = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(90)
        self.debounce.timeout.connect(self.publish)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(1000)
        self.heartbeat.timeout.connect(self.publish)
        controls = (
            "settings_style_select",
            "menu_theme_select",
            "bubble_theme_select",
            "quota_theme_select",
            "quick_chat_theme_select",
            "chat_window_theme_select",
            "bubble_style_select",
            "bubble_text_scale_spin",
            "scale_combo",
            "pet_opacity_spin",
            "speed_select",
            "ui_language_select",
            "island_style_select",
            "island_opacity_spin",
            "island_accent_select",
            "island_icon_select",
            "island_info_mode_select",
            "menu_font_size_select",
            "menu_font_select",
            "menu_density_select",
            "menu_radius_select",
            "menu_opacity_spin",
        )
        for name in controls:
            widget = getattr(dialog, name, None)
            if widget is None:
                continue
            for signal in ("currentIndexChanged", "valueChanged"):
                candidate = getattr(widget, signal, None)
                if candidate is not None:
                    candidate.connect(self.changed)
                    break
        dialog.finished.connect(lambda _result: self.finish())
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.finish)
        dialog.save_exit_button.setToolTip("保存並退出才會正式保存；直接關閉會取消預覽。")

    def changed(self, *_args):
        if self.closed:
            return
        self.dirty = True
        self.debounce.start()
        self.heartbeat.start()

    def values(self):
        d = self.dialog
        values = {}
        for name, key in (
            ("settings_style_select", "settings_ui_style"),
            ("menu_theme_select", "menu_ui_style"),
            ("bubble_theme_select", "bubble_ui_style"),
            ("quota_theme_select", "quota_ui_style"),
            ("quick_chat_theme_select", "quick_chat_ui_style"),
            ("chat_window_theme_select", "chat_window_ui_style"),
            ("bubble_style_select", "self_talk_bubble_style"),
            ("scale_combo", "scale"),
            ("speed_select", "playback_speed"),
            ("ui_language_select", "ui_language"),
        ):
            widget = getattr(d, name, None)
            if widget is not None:
                values[key] = widget.currentData()
        for name, key in (("bubble_text_scale_spin", "bubble_text_scale"), ("pet_opacity_spin", "pet_opacity")):
            widget = getattr(d, name, None)
            if widget is not None:
                values[key] = widget.value()
        island = {}
        for name, key in (
            ("island_style_select", "style"),
            ("island_accent_select", "accent"),
            ("island_icon_select", "icon"),
            ("island_info_mode_select", "info_mode"),
        ):
            widget = getattr(d, name, None)
            if widget is not None:
                island[key] = getattr(d, "_avatar_last", "auto") if key == "icon" and widget.currentData() == "__import_avatar__" else widget.currentData()
        island["opacity"] = d.island_opacity_spin.value()
        values["dynamic_island"] = island
        appearance = {}
        for name, key in (
            ("menu_font_size_select", "ui_font_size"),
            ("menu_font_select", "ui_font_family"),
            ("menu_density_select", "density"),
            ("menu_radius_select", "radius"),
        ):
            widget = getattr(d, name, None)
            if widget is not None:
                appearance[key] = widget.currentData()
        widget = getattr(d, "menu_opacity_spin", None)
        if widget is not None:
            appearance["opacity"] = widget.value()
        values["context_menu_appearance"] = appearance
        return values

    def publish(self):
        if self.closed or not self.dirty:
            return
        value = {"owner": self.owner, "pid": os.getpid(), "updatedAt": time.time(), "values": self.values()}
        try:
            temp = self.path.with_suffix("." + self.owner + ".tmp")
            temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
            self.dialog.config._dsh_preview_owner = self.owner
            if _runtime is not None:
                _runtime.tick()
        except OSError:
            pass

    def finish(self):
        if self.closed:
            return
        self.closed = True
        self.debounce.stop()
        self.heartbeat.stop()
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("owner") == self.owner:
                self.path.unlink()
        except (OSError, ValueError):
            pass
        self.dialog.config.reload()
        from .custom_avatar import cleanup

        cleanup(self.dialog)
        from .language_ui import _manager

        if _manager is not None:
            _manager._preview_owner = None
            _manager.preview(self.dialog.config.get("ui_language", "zh_TW"))
            _manager._stamp = None
        if _runtime is not None:
            _runtime.tick()


def attach(dialog):
    if not hasattr(dialog, "_dsh_preview_writer"):
        dialog._dsh_preview_writer = SettingsPreview(dialog)
    install_runtime(dialog.config)


def cancel(dialog):
    writer = getattr(dialog, "_dsh_preview_writer", None)
    if writer is not None:
        writer.finish()


def commit(dialog, original):
    global _committing
    previous = _committing
    _committing = True
    try:
        from .custom_avatar import prepare_save

        prepare_save(dialog)
        result = original(dialog)
    finally:
        _committing = previous
    if result:
        writer = getattr(dialog, "_dsh_preview_writer", None)
        if writer is not None:
            writer.saved = True
            writer.finish()
    return result


class PreviewRuntime(QObject):
    def __init__(self, app, config):
        super().__init__(app)
        self.config = config
        self.key = None
        self.visual_key = None
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        logging.info("DSH reversible visual previews ready")

    def tick(self):
        try:
            path = Path(self.config.path)
            values = active_values(preview_path(self.config))
            key = (path.stat().st_mtime_ns, json.dumps(values, sort_keys=True))
            if key == self.key:
                return
            previous = self.key
            self.key = key
            self.config.reload()
            widgets = QApplication.topLevelWidgets()
            configs = {id(self.config): self.config}
            for widget in widgets:
                cfg = getattr(widget, "cfg", None) or getattr(widget, "config", None)
                if cfg is not None and hasattr(cfg, "data") and Path(cfg.path) == path:
                    configs[id(cfg)] = cfg
            for cfg in configs.values():
                cfg.reload()
            # Older packaged Config.reload implementations can write normalized
            # defaults. Consume that timestamp rather than repeatedly redrawing.
            self.key = (path.stat().st_mtime_ns, key[1])
            visual_key = (bool(values), json.dumps({name: self.config.get(name) for name in VISUAL}, sort_keys=True))
            if visual_key == self.visual_key:
                return
            self.visual_key = visual_key
            from .language_ui import _manager

            if _manager is not None and not any(hasattr(w, "_dsh_preview_writer") and w.isVisible() for w in widgets):
                _manager._preview_owner = self if values else None
                _manager.preview(self.config.get("ui_language", "zh_TW"), self if values else None)
            pets = [w for w in widgets if hasattr(w, "_speech_bubble") and hasattr(w, "cfg")]
            for pet in pets:
                cfg = pet.cfg
                if hasattr(pet, "change_scale") and abs(float(cfg.get("scale", getattr(pet, "scale", 1))) - getattr(pet, "scale", 1)) > 0.001:
                    pet.change_scale(float(cfg.get("scale", 1)))
                if hasattr(pet, "set_pet_opacity"):
                    pet.set_pet_opacity(int(cfg.get("pet_opacity", 100)))
                speed = float(cfg.get("playback_speed", getattr(pet, "playback_speed", 1)))
                if hasattr(pet, "set_playback_speed") and abs(speed - getattr(pet, "playback_speed", 1)) > 0.001:
                    pet.set_playback_speed(speed)
                bubble = pet._speech_bubble
                self.refresh_bubble(bubble, cfg, pet, values, previous)
            for widget in widgets:
                if widget.objectName() == "dynamic-island":
                    widget.refresh_from_config()
                elif widget.objectName() == "codexUsageTrendWindow":
                    widget.refresh()
                elif widget.objectName() == "codexQuotaBubble":
                    widget.update()
                elif widget.objectName() == "chat-window":
                    widget._style()
                elif widget.objectName() == "quick-chat-bubble":
                    from .ui_polish import quick_style

                    quick_style(widget)
                    widget.update()
                elif widget.objectName() == "pet-speech-bubble" and not any(getattr(p, "_speech_bubble", None) is widget for p in pets):
                    self.refresh_bubble(widget, self.config, None, values, previous)
                elif isinstance(widget, QMenu) and widget.property("dshPolishedMenu") and pets:
                    from .independent_ui import menu_polish

                    menu_polish(widget, pets[0])
            state = {
                "active": bool(values),
                "fields": sorted(values),
                "bubbleTheme": self.config.get("bubble_ui_style", "light"),
                "quotaTheme": self.config.get("quota_ui_style", "dark"),
                "quickChatTheme": self.config.get("quick_chat_ui_style", "dark"),
                "chatWindowTheme": self.config.get("chat_window_ui_style", "dark"),
                "islandStyle": self.config.get("dynamic_island", {}).get("style", "light"),
                "bubbleTextScale": self.config.get("bubble_text_scale", 100),
                "updatedAt": time.time(),
                "pid": os.getpid(),
            }
            state_path = path.parent / "ui-preview-runtime.json"
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, state_path)
        except (OSError, ValueError, RuntimeError, AttributeError, TypeError):
            pass

    def refresh_bubble(self, bubble, cfg, pet, values, previous):
        if bubble is None or not hasattr(bubble, "_hide_timer"):
            return
        was_visible = bubble.isVisible()
        raw = bubble._raw_text
        subtitle = bubble._subtitle_label.text()
        kind = bubble._content_kind
        title_first = bubble._title_first
        bubble._dsh_theme_config = cfg
        bubble.set_style(cfg.get("self_talk_bubble_style", "classic_top"))
        scale = max(0.5, min(3.0, float(cfg.get("bubble_text_scale", 100)) / 100))
        bubble.set_text_scale(scale)
        if pet is not None:
            pet._bubble_text_scale = scale
        if not values and getattr(bubble, "_dsh_demo_preview", False):
            bubble._dsh_demo_preview = False
            bubble.hide()
            return
        anchor = bubble._anchor_rect
        if anchor.isEmpty() and pet is not None:
            anchor = pet.geometry()
        if bubble._interactive_active or anchor.isEmpty():
            return
        if was_visible and kind == "text" and raw:
            from .ytmusic import redraw_bubble

            redraw_bubble(bubble, raw, anchor, max(500, bubble._hide_timer.remainingTime()), subtitle=subtitle, title_first=title_first, width_locked=False)
        elif values and pet is not None and pet.isVisible():
            bubble._dsh_demo_preview = True
            bubble.show_text("氣泡預覽：大肥魚陪你工作", anchor, 3000)


def install_runtime(config=None):
    global _runtime
    app = QApplication.instance()
    if app is not None and _runtime is None:
        if config is None:
            from .config import Config

            config = Config()
        _runtime = PreviewRuntime(app, config)
    return _runtime
