"""Fresh-process real Qt gate; fake only desktop system calls, never the pet window."""

from __future__ import annotations

import base64
import ctypes
import importlib.abc
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


class BlockVision(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pet.vision" or fullname.startswith("pet.vision."):
            raise AssertionError("basic desktop path imported " + fullname)


def main():
    sys.meta_path.insert(0, BlockVision())
    # Import the real application entry module as well as executing real windows.
    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtWidgets import QApplication

    import pet.app  # noqa: F401
    from pet import platform_win, window_screen
    from pet.config import Config
    from pet.library import MovieLibrary
    from pet.multi_window_shared import SharedFullscreenWatcher
    from pet.window import PetWindow
    from pet.workers.supervisor import WorkerSupervisor
    from tests.desktop_query_fakes import WindowApi

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    root, shared = Path(sys.argv[1]), bool(int(sys.argv[2]))
    root.mkdir(parents=True, exist_ok=True)
    media = root / "media"
    media.mkdir(exist_ok=True)
    (media / "idle.gif").write_bytes(base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))
    worker_starts = []
    start = WorkerSupervisor.start

    def record_start(self, *args, **kwargs):
        worker_starts.append(True)
        return start(self, *args, **kwargs)

    WorkerSupervisor.start = record_start
    windows, libraries = [], []
    for i in range(2 if shared else 1):
        cfg = Config(base=root / f"data-{i}", instance_id=f"query-{i}")
        cfg.data.update(
            {
                "agent_link": {},
                "proactive_screen": {"enabled": False},
                "chat_enabled": False,
                "auto_hide_fullscreen": True,
                "cursor_hidden_passthrough": True,
                "click_sound_enabled": False,
                "music_lyric_enabled": False,
            }
        )
        lib = MovieLibrary(asset_dir=media, prewarm_enabled=False)
        libraries.append(lib)
        win = PetWindow(lib, cfg, single_process_spawn=shared)
        windows.append(win)
        win.show()
    app.processEvents()

    api = WindowApi().with_watch_queries()
    api.pid = os.getpid() + 1
    api.style = 0x00C00000
    old_dlls = getattr(ctypes, "windll", None)

    class DllBoundary:
        def __getattr__(self, name):
            return getattr(api.dlls, name) if hasattr(api.dlls, name) else getattr(old_dlls, name)

    ctypes.windll = DllBoundary()
    # Exercise Windows dispatch on all CI platforms; QApplication remains offscreen.
    # Only the watcher platform policy sees a desktop-capable environment.
    platform_win.os = SimpleNamespace(name="nt", getpid=os.getpid)
    window_screen.os = SimpleNamespace(name="nt", environ={**os.environ, "QT_QPA_PLATFORM": "minimal"})
    from pet import desktop_query

    desktop_query.sys = SimpleNamespace(platform="win32")
    cursor_signals = []
    if shared:
        watcher = SharedFullscreenWatcher(SimpleNamespace(instances=[SimpleNamespace(win=w) for w in windows]))
        for win in windows:
            watcher.fullscreen_changed.connect(win._on_fullscreen_changed)
            watcher.cursor_visibility_changed.connect(win._on_cursor_visibility_changed)
        watcher.cursor_visibility_changed.connect(cursor_signals.append)
        watcher.start()
    else:
        watcher = None
        windows[0].cursor_visibility_changed.connect(cursor_signals.append)
        windows[0]._start_fs_watch()
    ok = []

    def check():
        if api.user32.GetMonitorInfoW.calls and len(cursor_signals) >= 2:
            ok.append(True)
            app.quit()

    poll = QTimer()
    poll.timeout.connect(check)
    poll.start(20)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(app.quit)
    deadline.start(10000)
    try:
        app.exec()
    finally:
        poll.stop()
        deadline.stop()
        if watcher is not None:
            watcher.stop()
            watcher._thread.join(2)
            assert not watcher._thread.is_alive()
        for win in windows:
            win._stop_fs_watch()
            assert win._fs_thread is None or not win._fs_thread.is_alive()
        ctypes.windll = old_dlls
        for win in windows:
            win.close()
        for lib in libraries:
            lib.shutdown()
        app.processEvents()
    assert ok, "watcher did not query the OS and deliver signals within budget"
    assert set(cursor_signals) == {"SHOWING"}
    assert api.closed and len(api.closed) == len(api.kernel32.OpenProcess.calls)
    assert all(w.proactive_watcher is None for w in windows)
    assert not worker_starts
    assert not any(p.state() != QProcess.ProcessState.NotRunning for w in windows for p in w.findChildren(QProcess))
    assert "pet.vision" not in sys.modules
    print(
        json.dumps(
            {
                "query_probe": "ok",
                "windows": len(windows),
                "foreground_calls": len(api.user32.GetForegroundWindow.calls),
                "cursor_calls": len(api.user32.GetCursorInfo.calls),
                "worker_starts": len(worker_starts),
            }
        )
    )


if __name__ == "__main__":
    main()
