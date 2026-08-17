# -*- coding: utf-8 -*-
"""诊断：验证高清素材下窗口渲染的每个环节。"""

from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402
from pet.window import PetWindow  # noqa: E402

app = QApplication([])
lib = MovieLibrary()
tmp = os.path.join(ROOT, 'tests', '_tmp_diag_fresh')
cfg = Config(base=tmp)
win = PetWindow(lib, cfg)
win.show()
app.processEvents()

print('scale:', win.scale, '(DEFAULT_SCALE=', catalog.DEFAULT_SCALE, ')')
print('窗口 size:', win.width(), 'x', win.height())
print('窗口 isVisible:', win.isVisible())
print('窗口 pos:', win.x(), win.y())
region = win.mask()
br = region.boundingRect()
print('mask boundingRect:', br.x(), br.y(), br.width(), 'x', br.height())

# 当前帧
pm = win._frame_pixmap
print('当前帧 pixmap:', None if pm is None else f'{pm.width()}x{pm.height()}')

if pm is not None:
    img = pm.toImage()
    opaque = 0
    for y in range(0, img.height(), 5):
        for x in range(0, img.width(), 5):
            if img.pixelColor(x, y).alpha() > 0:
                opaque += 1
    print('当前帧非透明采样点:', opaque)

# mask 非空像素
mask_img = win.mask().toImage() if hasattr(win.mask(), 'toImage') else None
if mask_img is None:
    # QRegion 无 toImage，从窗口 grab 判断
    grab = win.grab()
    gimg = grab.toImage()
    mask_opaque = 0
    for y in range(0, gimg.height(), 4):
        for x in range(0, gimg.width(), 4):
            if gimg.pixelColor(x, y).alpha() > 0:
                mask_opaque += 1
    print('窗口 grab 非透明采样点:', mask_opaque, '(grab 尺寸', grab.width(), 'x', grab.height(), ')')
else:
    mask_opaque = 0
    for y in range(0, mask_img.height(), 4):
        for x in range(0, mask_img.width(), 4):
            if mask_img.pixelColor(x, y).alpha() > 0:
                mask_opaque += 1
    print('mask 非透明采样点:', mask_opaque)

# 当前 QMovie 状态
mv = win.movie
print('当前动画:', win.anim)
print('QMovie 帧数:', mv.frameCount() if mv else None)
print('QMovie 当前帧号:', mv.currentFrameNumber() if mv else None)
cp = mv.currentPixmap() if mv else None
print('QMovie currentPixmap:', None if cp is None or cp.isNull() else f'{cp.width()}x{cp.height()}')
