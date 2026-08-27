# -*- coding: utf-8 -*-
"""真实 Windows 下诊断 screen/availableGeometry 与窗口定位。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402
from pet.window import PetWindow  # noqa: E402

app = QApplication([])
screens = QGuiApplication.screens()
for i, s in enumerate(screens):
    print(f'screen[{i}]: geometry={s.geometry()} available={s.availableGeometry()} '
          f'name={s.name()}')

lib = MovieLibrary()
cfg = Config(base=os.path.join(ROOT, 'tests', '_tmp_pos_fresh'))
win = PetWindow(lib, cfg)

print('窗口 size:', win.width(), 'x', win.height())
print('win.screen() availableGeometry:', win.screen().availableGeometry())
print('move 前 pos:', win.x(), win.y())

avail = win.screen().availableGeometry()
x = avail.right() - win.width() - catalog.CORNER_MARGIN
y = avail.bottom() - win.height()
print(f'期望右下角: x={x} y={y}')
win.show()
app.processEvents()
print('show 后 pos:', win.x(), win.y())
