"""Exercise hover transitions on the actual Codex island, without music RPC."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_probe(app, config, output):
    from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, QTimer
    from PySide6.QtGui import QEnterEvent
    from pet import island_music
    from pet.dynamic_island import DynamicIsland
    from pet.ui_preview import install_runtime

    config.set("codex_work_status_enabled", False)
    config.set("dynamic_island", dict(config.get("dynamic_island"), x=50, y=60, edge_dock=False))
    island = DynamicIsland(config)
    output.mkdir(parents=True, exist_ok=True)
    failures, samples = [], []
    cursor = SimpleNamespace(pos=lambda: island.mapToGlobal(QPoint(20, 20)))

    def enter():
        local = QPointF(20, 20)
        app.sendEvent(island, QEnterEvent(local, local, QPointF(cursor.pos())))

    def wait_settled():
        loop, timeout, check = QEventLoop(), QTimer(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        check.setInterval(10)
        check.timeout.connect(lambda: loop.quit() if island._geo_to is None or failures else None)
        timeout.start(30000)
        check.start()
        if island._geo_to is not None:
            loop.exec()
        check.stop()
        assert not failures, failures[0] if failures else ""
        assert island._geo_to is None, "Hover animation did not finish"

    def observe_tick():
        if island._geo_to is None:
            return
        before, progress = QRect(island.geometry()), island._geo_t
        enter()  # Native child transitions can re-deliver Enter mid-animation.
        if island._geo_t < progress:
            failures.append("Repeated Enter restarted the hover animation")
        island._refresh()  # Work/title updates race with the same real animation.
        if island.geometry() != before:
            failures.append("Status refresh jumped hover geometry")
        samples.append(island.width())

    started = time.perf_counter()
    try:
        with patch.object(island_music.MusicBar, "refresh", lambda self: None), patch.object(island_music, "QCursor", cursor):
            island.show()
            app.processEvents()
            runtime = install_runtime(config)
            config.save()
            runtime.tick()
            base = QRect(island.geometry())
            island._anim_timer.timeout.connect(observe_tick)
            enter()
            wait_settled()
            island._anim_timer.timeout.disconnect(observe_tick)
            assert not failures, failures[0] if failures else ""
            assert all(a <= b for a, b in zip(samples, samples[1:])), "Opening width reversed"
            assert island.width() == base.width() + island_music.EXTRA_WIDTH
            bar = island._music_bar
            # A persisted pet position is not a visual-setting change. The
            # preview poll must not reset the island underneath the pointer.
            config.set("rx", 0.25)
            config.save()
            runtime.tick()
            assert bar.isVisible() and island._music_requested, "Unrelated config save collapsed hovered music controls"
            assert island.width() == base.width() + island_music.EXTRA_WIDTH
            for _ in range(3):
                runtime.tick()
                assert bar.isVisible() and island._music_requested
            island.expand_card()
            wait_settled()
            assert bar.isVisible() and bar.poll.isActive(), "Opening the work card hid the music controls"
            assert island.rect().contains(bar.geometry()), "Expanded music controls were clipped"
            assert bar.geometry().bottom() < 44, "Music controls overlapped the work-card content"
            for theme in ("dark", "light", "glass"):
                config.set("dynamic_island", dict(config.get("dynamic_island"), style=theme))
                island.refresh_from_config()
                app.processEvents()
                assert bar.isVisible(), "Theme refresh hid expanded music controls"
                island.grab().save(str(output / ("expanded-music-" + theme + ".png")))
            island.collapse_card()
            wait_settled()
            assert bar.isVisible(), "Collapsing the card lost hovered music controls"
            for theme in ("dark", "light", "glass"):
                island._cfg["style"] = theme
                island.update()
                app.processEvents()
                island.grab().save(str(output / ("hover-" + theme + ".png")))
            # Moving across child buttons must also cancel the native dock timer.
            island._mode, island._hover_peek = "docked", True
            cursor.pos = lambda: bar.buttons["next"].mapToGlobal(bar.buttons["next"].rect().center())
            app.sendEvent(island, QEvent(QEvent.Type.Leave))
            button = bar.buttons["next"]
            app.sendEvent(button, QEnterEvent(QPointF(16, 16), QPointF(16, 16), QPointF(cursor.pos())))
            assert not island._dock_back_timer.isActive(), "Native docking timer remained armed over a music button"
            island._mode, island._hover_peek = "normal", False
            cursor.pos = lambda: QPoint(-500, -500)
            app.sendEvent(button, QEvent(QEvent.Type.Leave))
            assert island._music_close_timer.isActive(), "Leaving the last child did not schedule collapse"
            loop, check, deadline = QEventLoop(), QTimer(), QTimer()
            check.setInterval(10)
            check.timeout.connect(lambda: loop.quit() if not island._music_requested else None)
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            check.start()
            deadline.start(30000)
            loop.exec()
            check.stop()
            assert not island._music_requested, "Controls stayed open after the mouse left"
            wait_settled()
            assert island.geometry() == base and not bar.isVisible() and not bar.poll.isActive()
            result = {"repeatedEnterDoesNotRestart": True, "statusRefreshDoesNotJump": True,
                      "openingWidthsMonotonic": True, "childHoverCancelsDocking": True,
                      "collapseRestoresPosition": True, "threeThemes": True,
                      "unrelatedSaveKeepsMusicControls": True,
                      "expandedCardKeepsMusicControls": True,
                      "lastChildLeaveCollapsesControls": True,
                      "animationFrames": len(samples), "seconds": round(time.perf_counter() - started, 3),
                      "modelTurns": 0, "directDesktopInspection": False}
            (output / "music-hover.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
    finally:
        island.hide()
        if hasattr(island, "_codex_work_controller"):
            island._codex_work_controller.shutdown()
        island.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from pet.codex_companion import install
    from pet.config import Config

    install()
    app = QApplication.instance() or QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output) as profile:
        print(json.dumps(run_probe(app, Config(base=profile), args.output)))


if __name__ == "__main__":
    main()
