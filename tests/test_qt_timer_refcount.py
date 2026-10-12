"""Guard the Python 3.11 native abort seen with PySide6 6.12.0 on Linux."""

import json
import os
import subprocess
import sys


def test_timer_stop_does_not_consume_none_references():
    # A native abort must fail this test, not terminate the full pytest process.
    script = """
import gc, json, sys, warnings
from PySide6.QtCore import QCoreApplication, QTimer, QObject, Signal
warnings.simplefilter('ignore')
app = QCoreApplication([]); timer = QTimer()
class Emitter(QObject): event = Signal()
emitter = Emitter()
def callback(): pass
def disconnect():
    emitter.event.connect(callback)
    emitter.event.disconnect(callback)
gc.collect(); gc.disable(); deltas = []
for method, value in ((timer.stop, None), (lambda: timer.setInterval(100), None),
                      (lambda: timer.start(100), None), (emitter.event.emit, True),
                      (disconnect, True), (lambda: emitter.event.disconnect(callback), False)):
    before = sys.getrefcount(value)
    for _ in range(100): method()
    deltas.append(sys.getrefcount(value) - before)
print(json.dumps(deltas), flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    deltas = json.loads(result.stdout)
    assert min(deltas) >= -5, f"Qt calls consumed singleton references: {deltas}"
