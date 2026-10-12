import statistics, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QImage, QRegion
from pet.overlay_window import OverlayWindow

app = QApplication([])
w = OverlayWindow()
w.resize(1920, 1080)
w.mouse_through = True
image = QImage(w.size(), QImage.Format.Format_ARGB32_Premultiplied)
region = QRegion(QRect(100, 100, 320, 180))
samples = []
for i in range(1100):
    t = time.perf_counter_ns()
    w.render(image, QPoint(), region, QWidget.RenderFlag.DrawWindowBackground)
    if i >= 100:
        samples.append((time.perf_counter_ns() - t) / 1000)
print("dirty paint 320x180 n=1000 median/p95 us", round(statistics.median(samples), 2), round(sorted(samples)[949], 2))
w.close()
