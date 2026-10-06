"""Exercise concurrent status rows on the real Qt island without generation."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_probe(app, config, output):
    from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer
    from pet.dynamic_island import DynamicIsland
    from pet.codex_work_status import capsule_info

    config.set("codex_work_status_enabled", True)
    config.set("ui_language", "zh_TW")
    island = DynamicIsland(config)
    island.show()
    controller = island._codex_work_controller
    controller.poll.stop()
    controller.request.disconnect()
    # This probe acknowledges specific rows explicitly below; wall-clock
    # auto-read/collapse must not compete with the scripted visual inspection.
    controller.seen_timer.timeout.disconnect()
    island._card_collapse_timer.timeout.disconnect()
    clock = [100.0]
    controller._clock = lambda: clock[0]

    def row(thread, state):
        return {"threadId": thread, "turnId": "turn-" + thread, "title": thread, "state": state, "signature": "turn-" + thread + ":" + state, "timestamp": 1000}

    def accept(rows):
        controller.accept({"ok": True, "updatedAt": 1000, "rows": rows})
        app.processEvents()

    def wait_for_geometry():
        # Wait for the real expand/collapse animation, not an arbitrary delay.
        loop = QEventLoop()
        timer = QTimer()
        elapsed = QElapsedTimer()
        elapsed.start()

        def check():
            if island.size() == island._rest_size() or elapsed.elapsed() > 10000:
                loop.quit()

        timer.setInterval(10)
        timer.timeout.connect(check)
        timer.start()
        check()
        if island.size() != island._rest_size():
            loop.exec()
        timer.stop()
        assert island.size() == island._rest_size(), "Island animation did not reach its final card size"
        app.processEvents()

    try:
        assert island._mode != "expanded" and not controller.box.isVisible()
        accept([row("桌寵功能", "thinking"), row("檢查環境", "thinking")])
        assert {r["threadId"] for r in controller.visible} == {"桌寵功能", "檢查環境"}, "Collapsed island discarded concurrent status updates"
        assert "2" in capsule_info(island, "12:00")
        clock[0] = 161
        controller.refresh()
        assert all(controller.state_caption(r) == "雷霆大思考" for r in controller.visible)
        accept([row("桌寵功能", "thinking"), row("檢查環境", "coding")])
        captions = {r["threadId"]: controller.state_caption(r) for r in controller.visible}
        assert captions == {"桌寵功能": "雷霆大思考", "檢查環境": "大肥魚敲代碼"}
        island.expand_card()
        wait_for_geometry()
        rows = [
            row("桌寵功能", "thinking"),
            row("檢查環境", "coding"),
            row("需要確認", "attention"),
            *(row("完成項目" + str(index), "completed") for index in range(4)),
        ]
        accept(rows)
        assert len(controller._record_frames) == 7
        assert controller.scroll.verticalScrollBar().maximum() > 0
        assert "2" in controller.heading.text()
        for style in ("dark", "light", "glass"):
            island._cfg["style"] = style
            controller.refresh()
            wait_for_geometry()
            assert len(controller._record_frames) == 7
            viewport = controller.scroll.viewport()
            assert all(viewport.rect().contains(frame.mapTo(viewport, frame.rect().center())) for frame, _row in controller._record_frames[:2])
            button = island._card_chat_btn
            assert island.rect().contains(button.mapTo(island, button.rect().bottomRight()))
            assert island.grab().save(str(output / ("multi-work-" + style + ".png")))
        controller._ack_rows = [rows[2], rows[3]]
        controller.acknowledge()
        assert {r["threadId"] for r in controller.visible} == {r["threadId"] for r in rows if r not in rows[2:4]}
        island.collapse_card()
        accept([row("桌寵功能", "coding"), row("檢查環境", "completed")])
        island.expand_card()
        wait_for_geometry()
        assert {r["threadId"]: r["state"] for r in controller.visible} == {"桌寵功能": "coding", "檢查環境": "completed"}
        result = {
            "collapsedBackgroundUpdates": True,
            "independentConcurrentPhases": True,
            "allSevenRowsScrollable": True,
            "perThreadAcknowledgement": True,
            "threeThemes": True,
            "modelTurns": 0,
            "directDesktopInspection": False,
        }
        (output / "multi-work-qt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        controller.shutdown()
        island.close()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from pet.codex_companion import install

    install()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    from pet.config import Config
    from pet.language_ui import install as language_manager

    app = QApplication([])
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "msjh.ttc", "msjhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
        app.setFont(QFont("Segoe UI", 9))
    with tempfile.TemporaryDirectory(dir=args.output) as profile:
        config = Config(base=profile)
        config.set("ui_language", "zh_TW")
        manager = language_manager(config)
        try:
            print(json.dumps(run_probe(app, config, args.output)))
        finally:
            manager.timer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
