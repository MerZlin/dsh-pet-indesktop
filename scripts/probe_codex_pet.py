"""Decode and display actual source-edition assets in a temporary Qt profile."""

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
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from pet.codex_companion import install

    install()
    from PySide6.QtCore import QTimer, QEventLoop, QElapsedTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.library import MovieLibrary
    from pet.window import PetWindow

    app = QApplication([])
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "msjh.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
        app.setFont(QFont("Segoe UI", 9))
    with tempfile.TemporaryDirectory(dir=args.output) as profile:
        cfg = Config(base=profile)
        cfg.set("ui_language", "zh_TW")
        cfg.save()
        library = MovieLibrary(character_id="shenshen", prewarm_enabled=False)
        pet = None
        try:
            assert library.names()
            pet = PetWindow(library, cfg)
            pet.show()
            loop, timer, elapsed = QEventLoop(), QTimer(), QElapsedTimer()
            elapsed.start()

            def check():
                if (pet._frame_pixmap is not None and not pet._frame_pixmap.isNull()) or elapsed.elapsed() > 30000:
                    loop.quit()

            timer.setInterval(10)
            timer.timeout.connect(check)
            timer.start()
            loop.exec()
            timer.stop()
            assert pet._frame_pixmap is not None and not pet._frame_pixmap.isNull(), "No real animation frame decoded"
            pet.show_bubble("同時進行多項工作，各自顯示狀態。", duration_ms=3200)
            app.processEvents()
            assert pet.isVisible() and pet._speech_bubble.isVisible()
            assert hasattr(pet, "_codex_quota_controller")
            pet.grab().save(str(args.output / "source-pet.png"))
            pet._speech_bubble.grab().save(str(args.output / "source-bubble.png"))
            result = {
                "realSourcePetWindow": True,
                "realAnimationLibrary": True,
                "realDecodedFrame": True,
                "animationAssets": len(library.names()),
                "speechBubbleVisible": True,
                "modelTurns": 0,
                "directDesktopInspection": False,
            }
            (args.output / "source-pet.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
        finally:
            if pet is not None:
                pet.close()
            library.shutdown()
            app.processEvents()
            QTimer.singleShot(0, app.quit)
            app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
