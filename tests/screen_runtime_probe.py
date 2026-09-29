"""Real Qt + loopback HTTP gate with all chat imports forbidden (no desktop capture)."""

from __future__ import annotations

import importlib.abc
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoChat(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pet.chat" or fullname.startswith("pet.chat."):
            raise AssertionError("screen path imported " + fullname)


def main():
    sys.meta_path.insert(0, NoChat())
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from pet import credentials, vision
    from pet.config import Config
    from pet.library import MovieLibrary
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.proactive import ProactiveScreenWatcher
    from pet.screen_understanding.config import VisionConfigService
    from pet.window import PetWindow
    from tests.screen_fakes import MemoryVault

    backend = MemoryVault()
    credentials.secure_backend = lambda: backend
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    cfg = Config(root / "data")
    cfg.data.update({"agent_link": {}, "proactive_screen": {"enabled": False}, "click_sound_enabled": False, "music_lyric_enabled": False})
    cfg.data["chat"]["enabled"] = False
    assert cfg.save()
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            assert self.headers.get("Authorization") == "Bearer TEST-VISION-SECRET"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"fixture vision reply"}}]}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    from features.screen_understanding.host.settings import ScreenSettingsPage

    page = dialog.findChild(ScreenSettingsPage)
    assert page is not None
    page.profile_id.setText("manual")
    page.url.setText(f"http://127.0.0.1:{server.server_port}")
    page.model.setText("fixture-vision")
    page.key_edit.setText("TEST-VISION-SECRET")
    page.save_button.click()
    assert VisionConfigService(cfg).resolve("manual").ready
    assert not VisionConfigService(cfg).resolve("automatic").ready
    assert not posts, "opening/saving settings must not request a model"
    dialog.close()

    media = root / "media"
    media.mkdir(exist_ok=True)
    from PIL import Image

    frames = [Image.new("RGBA", (32, 32), color) for color in ("red", "blue")]
    frames[0].save(media / "idle.gif", save_all=True, append_images=frames[1:], duration=500, loop=0)
    lib = MovieLibrary(asset_dir=media, prewarm_enabled=False)
    win = PetWindow(lib, cfg)
    # Explicit retained rollback path; actual watcher/window and network executor.
    win.proactive_watcher = ProactiveScreenWatcher(win, cfg, worker_mode="in_process")
    captures = []

    def capture():
        captures.append(True)
        return b"fixture JPEG boundary"

    vision.capture_screen_bytes = capture
    vision.foreground_app_info = lambda: "fixture application"
    synced, shown = [], []
    show = win.show_bubble

    def show_and_record(text, *args, **kwargs):
        shown.append(text)
        return show(text, *args, **kwargs)

    win.show_bubble = show_and_record
    win.on_look_synced = lambda user, reply: synced.append((user, reply))
    win.show()
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if synced else None)
    timer.start(10)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(15000)
    try:
        win.look_at_screen()
        loop.exec()
        assert len(posts) == len(captures) == len(synced) == 1
        assert synced[0][1] == "fixture vision reply"
        assert "fixture vision reply" in shown
        assert not win._look_busy

        # Receiver failure never prevents the already-rendered bubble.
        def failed_receiver(*args):
            raise RuntimeError("fixture receiver unavailable")

        win.on_look_synced = failed_receiver
        win._on_look_done("sync failure still visible", "fixture", False)
        assert shown[-1] == "sync failure still visible"
        before = len(shown)
        service = VisionConfigService(cfg)
        from dataclasses import replace

        service.save_profile(replace(service.settings().profiles["manual"], model="changed"), modes=["manual"], expected_revision=service.revision())
        win._on_look_done("stale result must not display", "fixture", False)
        assert len(shown) == before
        assert not any(n == "pet.chat" or n.startswith("pet.chat.") for n in sys.modules)
        print(
            json.dumps(
                {
                    "no_chat": True,
                    "settings_saved": True,
                    "http_requests": len(posts),
                    "manual_reply": True,
                    "sync_failure_isolated": True,
                    "stale_dropped": True,
                }
            )
        )
    finally:
        timer.stop()
        deadline.stop()
        win.proactive_watcher.pause()
        win.close()
        lib.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(5)
        app.processEvents()


if __name__ == "__main__":
    main()
