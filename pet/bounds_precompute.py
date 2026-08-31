# -*- coding: utf-8 -*-
"""
动画 bounds 预计算（性能计划 B14）。

目标：把「运行时每帧 O(像素) 扫描画布求可见 bounds」降为「预热阶段后台
每动画算一次、运行时 O(1) 查表」。本模块只含纯函数与不可变数据容器，
不持有任何 Qt 对象生命周期，可被后台线程安全调用。

- alpha_bounds：ARGB32 图 alpha>=128 包围盒的直扫。与运行时
  window._mono_mask_bounds（createAlphaMask 的 1bpp 掩码扫描）阈值逐位
  一致（有差分测试锁定），但不构造 QBitmap/QPixmap——createAlphaMask
  返回 QBitmap，属 GUI 线程对象，后台预热线程禁用。
- frame_window_bounds：单帧的窗口局部可见 bounds。管线与运行时
  _rebuild_frame → _sync_mask 完全同源：镜像 → 预乘 → Smooth 缩放 →
  ARGB32 → 以运行时同款绘制矩形画进窗口尺寸画布 → alpha 扫描。画布内容
  与运行时 drawPixmap(带 DPR 的 QPixmap) 逐位相同（drawImage 与 drawPixmap
  在相同目标矩形上等价，已实测），因此结果与运行时扫描完全一致
  （合成帧 + 真实 webm 差分测试锁定）。
- AnimBounds：一个 (mirrored, scale, dpr) 键下整个动画的 bounds 数据。
  每帧 4 个 int16（x0,y0,x1,y1，含边界；空帧 -1）的紧凑存储：241 帧
  仅 ~1.9KB；union 为全部帧并集（窗口局部坐标）；feet 为 union 底边中点
  （脚底锚点）；has_text 为文字动画标记。
"""

from __future__ import annotations

from array import array

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter

from . import catalog

# alpha>=128 → 255、其余 → 0 的翻译表：把 8-bit alpha 行映射为 0/255，
# 从而用 C 速度的 bytes.find/rfind 定位可见像素首尾（整缓冲只翻译一次）。
_ALPHA_THRESHOLD_TABLE = bytes(128) + bytes([255]) * 128


def alpha_bounds(image: QImage) -> tuple[int, int, int, int] | None:
    """ARGB32 图中 alpha>=128 像素的包围盒；全空返回 None。

    返回 (x0, y0, x1, y1)，含边界。逐位等价于 window._mono_mask_bounds
    （createAlphaMask 的 1bpp 掩码扫描，阈值同为 alpha>=128），但纯字节
    扫描、不构造 QBitmap，可在后台线程执行。

    实现：整体转 Alpha8（C++）→ 一次性 translate 成 0/255 → 逐行
    find/rfind 首尾可见像素（C 速度，无每行拷贝）。
    """
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    width = alpha.width()
    height = alpha.height()
    stride = alpha.bytesPerLine()
    bits = alpha.constBits()
    buf = bytes(bits)
    tr = buf.translate(_ALPHA_THRESHOLD_TABLE)
    first = tr.find(b'\xff')
    if first < 0:
        return None
    x0 = y0 = None
    x1 = y1 = -1
    for y in range(height):
        start = y * stride
        fx = tr.find(b'\xff', start, start + width)
        if fx < 0:
            continue
        lx = tr.rfind(b'\xff', start, start + width)
        fx -= start
        lx -= start
        if y0 is None:
            y0, x0 = y, fx
        else:
            if fx < x0:
                x0 = fx
        y1 = y
        if lx > x1:
            x1 = lx
    if y0 is None:
        return None
    return x0, y0, x1, y1


def frame_window_bounds(
    frame: QImage,
    *,
    mirrored: bool,
    scale: float,
    dpr: float,
) -> QRect:
    """单帧的窗口局部可见 bounds，与运行时 _rebuild_frame+_sync_mask 逐位一致。

    参数与运行时完全同源（scale/dpr 与窗口一致、绘制矩形即 _frame_draw_rect
    的非 squash 形态、窗口尺寸即 _apply_scale 的结果）。squash 变形期间
    绘制矩形不同，运行时不会查本结果（_sync_mask 会回落扫描）。
    """
    img = frame
    if mirrored:
        img = img.mirrored(True, False)
    w_c = max(1, int(round(catalog.CANVAS_W * scale * dpr)))
    h_c = max(1, int(round(catalog.CANVAS_H * scale * dpr)))
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    img = img.scaled(
        w_c, h_c,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w = max(1, int(round(catalog.CANVAS_W * scale)))
    h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
    canvas = QImage(w, h, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    rect = QRect(
        0,
        int(round(catalog.PAD * scale)),
        int(round(catalog.CANVAS_W * scale)),
        int(round(catalog.CANVAS_H * scale)),
    )
    p.drawImage(rect, img)
    p.end()
    bb = alpha_bounds(canvas)
    if bb is None:
        return QRect()
    x0, y0, x1, y1 = bb
    return QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def empty_flat(frame_count: int) -> "array":
    """全空帧的 flat 存储（每帧 4 个 -1）。"""
    return array('h', [-1] * (frame_count * 4))


class AnimBounds:
    """一个 (mirrored, scale, dpr) 键下整个动画的 bounds 数据（不可变）。

    存储：flat 为 array('h')，每帧 4 个 int16（x0,y0,x1,y1，含边界；
    空帧为 -1）。窗口局部坐标上界 = round(CANVAS_W*scale*dpr) 等，任意
    合理 scale/DPR 组合都远小于 int16 上限 32767（scale≤8、DPR≤4 时
    ≤20480）。
    """

    __slots__ = ("frame_count", "flat", "union", "feet", "has_text")

    def __init__(
        self,
        frame_count: int,
        flat: "array",
        union: QRect,
        feet: QPoint,
        has_text: bool,
    ) -> None:
        self.frame_count = frame_count
        self.flat = flat
        self.union = union
        self.feet = feet
        self.has_text = has_text

    def frame_rect(self, n: int) -> QRect | None:
        """第 n 帧的窗口局部可见 bounds。

        n 为「显示帧索引」（0 基，== 预计算表索引 == WebMClip.currentFrameNumber
        的语义，B14 复审 P0 统一契约）：n=0 即首帧。空帧返回空 QRect（与画布
        扫描一致）；越界返回 None（调用方回落扫描）。
        """
        if n < 0 or n >= self.frame_count:
            return None
        i = n * 4
        x0 = self.flat[i]
        if x0 < 0:
            return QRect()
        x1 = self.flat[i + 2]
        return QRect(x0, self.flat[i + 1], x1 - x0 + 1, self.flat[i + 3] - self.flat[i + 1] + 1)

    def memory_bytes(self) -> int:
        """flat 存储的字节数（每帧 4×int16 = 8 字节）。"""
        return len(self.flat) * self.flat.itemsize
