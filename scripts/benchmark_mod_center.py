"""Measure local MOD list rebuild and idle catalog checks, using generated data."""
from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication

from pet.mod_catalog import ModCatalogMonitor
from pet.mod_center_ui import ModCenterWidget
from pet.mod_management import ModEntry


class Catalog(QObject):
    changed = Signal()
    busy_changed = Signal(bool)
    result = Signal(str, object)
    batch_finished = Signal(object)
    busy = False

    def __init__(self, root, count):
        super().__init__()
        self.rows = [ModEntry(f'demo.mod-{n}', '功能扩展', f'离线测试 MOD {n}',
                              '1.0.0', '用于测量列表渲染的纯文本说明。' * 4,
                              False, root / str(n)) for n in range(count)]

    def entries(self):
        return self.rows


def describe(values):
    return {'n': len(values), 'median_ms': statistics.median(values),
            'max_ms': max(values), 'mean_ms': statistics.mean(values)}


def measure(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'data'
    for directory in (root / 'plugins', root / 'content/characters'):
        directory.mkdir(parents=True)
    app = QApplication.instance() or QApplication([])
    monitor = ModCatalogMonitor(root)
    checks = []
    for _ in range(1000):
        start = time.perf_counter()
        monitor.check()
        checks.append((time.perf_counter() - start) * 1000)
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'idle_check': describe(checks), 'list_build': {}}
    try:
        import psutil
        process = psutil.Process()
    except ImportError:
        process = None
    for count in (2, 20, 100):
        values, memory = [], []
        backend = Catalog(root, count)
        for _ in range(10):
            start = time.perf_counter()
            widget = ModCenterWidget(backend)
            widget.resize(720, 700)
            widget.show()
            app.processEvents()
            values.append((time.perf_counter() - start) * 1000)
            widget.close()
            widget.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            gc.collect()
            if process:
                memory.append(process.memory_info().rss)
        report['list_build'][str(count)] = {**describe(values), 'rss_after_dispose': memory}
    monitor.close()
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new measurement directory')
    measure(parser.parse_args().output)
