"""Bounded owner/UI lease measurements; temporary config, no credentials or execution.

Default: offscreen. ``--native`` uses Windows Qt without displaying user windows;
this is native object-lifecycle evidence, NOT manual visual acceptance.
"""

from __future__ import annotations

import argparse
import gc
import importlib.abc
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ExecutionBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in ("pet.vision", "pet.proactive", "pet.app", "pet.workers.supervisor", "keyring")):
            raise AssertionError("configuration-only benchmark imported execution: " + fullname)
        return None


def measure(function, count):
    samples = []
    for index in range(count):
        start = time.perf_counter_ns()
        function(index)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    samples.sort()
    return {"samples": count, "mean_ms": round(statistics.mean(samples), 4), "p95_ms": round(samples[int((count - 1) * 0.95)], 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    if args.native and sys.platform != "win32":
        parser.error("--native currently requires Windows")
    os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
    sys.meta_path.insert(0, ExecutionBlocker())

    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication, QMenu
    from shiboken6 import isValid

    from pet.config import Config
    from pet.context_menus.shared import add_look_screen, add_proactive_menu
    from pet.feature_bindings import bind_screen_window
    from pet.official_features import SCREEN_OWNER, default_feature_host
    from pet.plugins.capabilities import CapabilitySet
    from pet.plugins.contributions import Contribution, ContributionRegistry
    from pet.plugins.ports import CommandRegistry

    app = QApplication([])

    def flush():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    scratch = ROOT / ".scratch" / "phase4a-contributions"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=scratch) as temp:
        cfg = Config(Path(temp))
        host = default_feature_host()
        registry = ContributionRegistry(CommandRegistry())
        port = registry.bind("official.benchmark", "instance:0", CapabilitySet(["menu.contribute"]))

        def register(_):
            batch = port.register([Contribution("action", "menu", command="run")], commands={"run": lambda: None})
            batch.dispose()

        target = SimpleNamespace(
            feature_host=host,
            cfg=cfg,
            on_look_screen=lambda: None,
            toggle_proactive_enabled=lambda value: None,
            set_proactive_option=lambda key, value: None,
        )
        _, scope = bind_screen_window(target)

        def menu(_):
            widget = QMenu()
            add_look_screen(widget, target)
            add_proactive_menu(widget, target)
            widget.deleteLater()
            flush()

        refs = []
        timer_counts = []

        def settings(index):
            key = f"settings:benchmark:{index}"
            handle = host.settings(SCREEN_OWNER, key)
            component = handle.create(cfg)
            timer_counts.append(sum(t.isActive() for t in component.findChildren(QTimer)))
            refs.append(component)
            component.dispose()
            host.detach(SCREEN_OWNER, key)
            flush()
            assert not isValid(component)

        # Warm Qt font/style and lazy imports before the bounded measurements.
        menu(0)
        settings(-1)
        widget_baseline = len(app.allWidgets())
        threads = threading.active_count()
        out = {"python": sys.version.split()[0], "qt_platform": app.platformName(), "shown_windows": 0}
        out["register_revoke"] = measure(register, 500)
        out["screen_menus_create_destroy"] = measure(menu, 100)
        out["screen_settings_create_destroy"] = measure(settings, 30)
        gc.collect()
        tracemalloc.start()
        measure(register, 500)
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        out["register_revoke_python_memory_bytes"] = {"retained": retained, "peak": peak, "samples": 500}
        out["remaining_settings_qobjects"] = sum(isValid(x) for x in refs)
        out["remaining_widget_delta"] = len(app.allWidgets()) - widget_baseline
        out["settings_active_timers_min_max"] = [min(timer_counts), max(timer_counts)]
        out["remaining_menu_observers"] = len(host._listeners)
        out["thread_delta"] = threading.active_count() - threads
        host.detach(SCREEN_OWNER, scope)
        out["remaining_contributions"] = len(host.registry._entries)
        out["remaining_commands"] = len(host.registry.commands._commands)
        assert not any(
            out[k]
            for k in (
                "remaining_settings_qobjects",
                "remaining_widget_delta",
                "remaining_menu_observers",
                "thread_delta",
                "remaining_contributions",
                "remaining_commands",
            )
        )
        out["execution_import_guard"] = "passed: no vision/watcher/supervisor/app/keyring"
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
