"""Measure native X11 input shapes with a deterministic in-memory sprite."""

import statistics, time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPointF
from tests.test_sprite_menu_facade import app, RichLibrary
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import PetSprite

window = OverlayWindow()
sprite = PetSprite(RichLibrary(), pos=QPointF(80, 100), scale=0.5)
sprite.bind_clip("idle1")
window.add_sprite(sprite)
window.show()
app.processEvents()
controller = window._linux_input
for mode in ("unchanged", "moving", "new_mask"):
    values = []
    for i in range(500):
        if mode == "moving":
            sprite.set_pos(QPointF(80 + i, 100))
        if mode == "new_mask":
            controller.cache.clear()
        t = time.perf_counter_ns()
        controller.refresh()
        values.append((time.perf_counter_ns() - t) / 1000)
    print(mode, "n=500, us median/p95", round(statistics.median(values), 2), round(sorted(values)[474], 2))
window.close()
assert controller.native.display is None
