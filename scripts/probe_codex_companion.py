"""Source-edition Qt smoke/visual probe. Uses a temporary profile and no inference."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshots", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from pet.codex_companion import install

    install()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPixmap, QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.chat.widgets import ChatWindow
    from pet.quick_chat import QuickChatBubble
    from pet.dynamic_island import DynamicIsland
    from pet.island_chat import IslandChatBubble
    from pet.speech_bubble import PetSpeechBubble
    from pet.codex_usage import QuotaBubble
    from pet.codex_usage_chart import UsageChart
    from pet.language_ui import install as language_manager
    from pet.ui_preview import active_values, preview_path, _runtime
    from pet.independent_ui import settings_style

    app = QApplication([])
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # Windows offscreen does not enumerate the native system font database.
        # Load the host's real fonts for meaningful headless visual evidence.
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "seguisb.ttf", "msjh.ttc", "msjhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
        app.setFont(QFont("Segoe UI", 9))
    observations = []
    with tempfile.TemporaryDirectory(prefix="dsh-codex-probe-") as temporary:
        config = Config(base=temporary)
        config.save()
        manager = language_manager(config)
        for locale in ("zh_CN", "zh_TW", "en"):
            config.set("ui_language", locale)
            config.save()
            manager.preview(locale)
            for style in ("dark", "light", "glass"):
                for width in (720, 1100):
                    dialog = ModernSettingsDialog(config, standalone=True)
                    dialog.resize(width, 760)
                    dialog.show()
                    dialog.settings_style_select.setCurrentData("light" if style != "light" else "dark")
                    dialog.settings_style_select.setCurrentData(style)
                    dialog._dsh_preview_writer.publish()
                    app.processEvents()
                    assert active_values(preview_path(config))["settings_ui_style"] == style
                    assert settings_style(config) == style
                    assert dialog.width() == width
                    assert dialog.chat_window_theme_select.currentData() == "dark"
                    if args.screenshots:
                        dialog.grab().save(str(args.output / f"settings-{locale}-{style}-{width}.png"))
                    observations.append({"language": locale, "theme": style, "width": dialog.width(), "preview": True})
                    dialog.reject()
                    config.reload()
                    assert config.get("settings_ui_style") == "dark"
                    assert not active_values(preview_path(config))
        dialog = ModernSettingsDialog(config, standalone=True)
        dialog.chat_window_theme_select.setCurrentData("light")
        assert dialog._write_config()
        config.reload()
        assert config.get("chat_window_ui_style") == "light"
        assert config.get("quick_chat_ui_style") == "dark"
        dialog.reject()

        chat = ChatWindow(config, "shenshen")
        chat.show()
        quick = QuickChatBubble(config)
        island = DynamicIsland(config)
        island.show()
        island.expand_card()
        embedded = IslandChatBubble(config)
        embedded.show_for_island(island, activate=True)
        assert embedded.parentWidget() is not None
        assert island._dsh_chat_controller.bubble is embedded
        speech = PetSpeechBubble()
        quota = QuotaBubble()
        chart = UsageChart(None)
        for style in ("dark", "light", "glass"):
            config.set("chat_window_ui_style", style)
            chat._style()
            config.set("quick_chat_ui_style", style)
            from pet.ui_polish import quick_style

            quick_style(quick)
            assert chat.property("dshChatWindowTheme") == style
            assert quick.property("dshQuickChatTheme") == style
            if args.screenshots:
                chat.grab().save(str(args.output / f"chat-{style}.png"))
        pixmap = QPixmap(24, 24)
        pixmap.fill()
        avatar = args.output / "temporary-avatar.png"
        pixmap.save(str(avatar))
        dialog = ModernSettingsDialog(config, standalone=True)
        from pet.custom_avatar import choose_file

        choose_file(dialog, str(avatar))
        imported = list(dialog._avatar_preview_files)
        assert imported and all(Path(path).exists() for path in imported)
        dialog.reject()
        assert all(not Path(path).exists() for path in imported)
        avatar.unlink()
        island._codex_work_controller.shutdown()
        for widget in (embedded, island, chat, quick, speech, quota, chart):
            widget.close()
        if _runtime is not None:
            _runtime.timer.stop()
        manager.timer.stop()
        QTimer.singleShot(0, app.quit)
        app.exec()
    result = {
        "sourceQtSmoke": True,
        "modelTurns": 0,
        "settingsStates": observations,
        "saveAndCancel": True,
        "independentThemes": True,
        "embeddedChat": True,
        "avatarCancelCleanup": True,
        "platform": sys.platform,
        "qtPlatform": os.environ.get("QT_QPA_PLATFORM", "native"),
        "directDesktopInspection": False,
    }
    (args.output / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "settingsStates"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
