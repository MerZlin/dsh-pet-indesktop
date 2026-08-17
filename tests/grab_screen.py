# -*- coding: utf-8 -*-
"""Qt QScreen.grabWindow 抓屏：验证真实屏幕上透明桌宠是否可见。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
screen = app.primaryScreen()
# 抓右下角区域（桌宠预期位置屏幕 x1050-1511, y534-815）
x, y, w, h = 900, 470, 636, 394
pix = screen.grabWindow(0, x, y, w, h)
out = os.path.join(ROOT, 'grab_qt.png')
pix.save(out)
print('saved', out, pix.width(), 'x', pix.height())

# 分析非桌面背景/肤色像素
img = pix.toImage()
skin = 0
colorful = 0
for yy in range(0, img.height(), 2):
    for xx in range(0, img.width(), 2):
        c = img.pixelColor(xx, yy)
        if c.alpha() == 0:
            continue
        r, g, b = c.red(), c.green(), c.blue()
        if r > 150 and g > 110 and b > 110 and r > g > b:
            skin += 1
        if max(r, g, b) - min(r, g, b) > 30:
            colorful += 1
print('肤色采样:', skin, '彩色采样:', colorful)
