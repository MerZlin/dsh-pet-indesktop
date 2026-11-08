"""框选：一次拖拽的几何、屏幕冻结与裁切（覆盖层本体在 ``overlay``）。

``select_region`` 是对外唯一入口（``ScreenPort.capture_region`` 的实现）：
冻结目标屏 → 弹覆盖层 → 等用户确认/取消 → 按设备像素比裁出选区 PNG。

覆盖层与抓屏都可注入：``overlay_factory`` 决定交互层（测试给一个立即确认的
替身即可在 offscreen 下跑完整条链路），``grab`` 决定底图来源。取消（Esc /
覆盖层动作条「取消」）与过小选区一律抛 :class:`ScreenSelectionCancelled`，
调用方按「用户没截」处理，不该弹错误框。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEventLoop, QRect
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QWidget

from ...contracts.screen import (
    ACTION_ASK,
    ACTION_COPY,
    ACTION_OCR,
    ACTION_TRANSLATE,
    RegionPick,
    ScreenError,
    ScreenSelectionCancelled,
)
from ...media.images import sanitize_dpr
from ...platform.screenshot import capture_from_image, grab_screen_image

__all__ = [
    "DEFAULT_CAPABILITIES",
    "MIN_SELECTION_SIDE",
    "SelectionRect",
    "normalize_drag",
    "select_region",
]

#: 缺省动作条能力（旧调用方不传时保持「问 + 提取文字 + 复制」可用）。
DEFAULT_CAPABILITIES = {
    ACTION_OCR: True,
    ACTION_TRANSLATE: False,
    ACTION_ASK: True,
    ACTION_COPY: True,
}

#: 选区的最小边长（逻辑像素）。8 的依据：小于 8 的拖拽只可能是误触——真截一块
#: 进去也读不出任何内容，于是「确认」后用户只会拿到一次白跑的会话。门槛卡在
#: 交互层：比这更小的拖拽等于**没框**。
MIN_SELECTION_SIDE = 8


@dataclass(frozen=True, slots=True)
class SelectionRect:
    """逻辑像素（DPR 前）下的选区；``x/y`` 为左上角，宽高非负才算有效。"""

    x: int
    y: int
    width: int
    height: int

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def device_rect(self, dpr: float) -> tuple[int, int, int, int]:
        """换算成设备像素 ``(left, top, width, height)``。

        左/上取 ``floor``、右/下取 ``ceil``：宁可多覆盖一像素，也不漏掉用户框进来
        的边缘（分数 DPR 下四舍五入会切掉半像素宽的一条）。
        """
        scale = sanitize_dpr(dpr)
        left = math.floor(self.x * scale)
        top = math.floor(self.y * scale)
        right = math.ceil((self.x + self.width) * scale)
        bottom = math.ceil((self.y + self.height) * scale)
        return left, top, right - left, bottom - top


def normalize_drag(
    x0: int, y0: int, x1: int, y1: int, bounds: tuple[int, int] | None = None
) -> SelectionRect:
    """把一次拖拽的起止点归一成选区（支持反向拖拽）。

    ``bounds`` 给定时按屏宽高钳制，越界部分直接裁掉（全在外面则得到空选区）。
    坐标为**边**语义：从 (10,10) 拖到 (20,20) 得到 10×10，原地点击得到空选区。
    """
    left, right = min(int(x0), int(x1)), max(int(x0), int(x1))
    top, bottom = min(int(y0), int(y1)), max(int(y0), int(y1))
    if bounds is not None:
        width, height = int(bounds[0]), int(bounds[1])
        left = max(0, left)
        top = max(0, top)
        right = min(width, right)
        bottom = min(height, bottom)
    return SelectionRect(left, top, max(0, right - left), max(0, bottom - top))


def usable(selection: SelectionRect | None) -> bool:
    """能不能进下游：非空 **且** 两个方向都不小于 ``MIN_SELECTION_SIDE``。"""
    if not isinstance(selection, SelectionRect) or selection.is_empty:
        return False
    return (
        selection.width >= MIN_SELECTION_SIDE
        and selection.height >= MIN_SELECTION_SIDE
    )


def _screen_geometry(screen) -> QRect | None:
    try:
        geometry = screen.geometry()
    except Exception:
        return None
    if geometry is None:
        return None
    rect = QRect(geometry)
    return None if rect.isEmpty() else rect


def _target_screen(window: QWidget | None, screen) -> object | None:
    """目标屏：显式 ``screen`` → 窗口所在屏 → 鼠标所在屏 → 主屏。"""
    if screen is not None:
        return screen
    if window is not None:
        try:
            own = window.screen()
        except Exception:
            own = None
        if own is not None:
            return own
    try:
        cursor = QGuiApplication.screenAt(QCursor.pos())
    except Exception:
        cursor = None
    if cursor is not None:
        return cursor
    try:
        return QGuiApplication.primaryScreen()
    except Exception:
        return None


def _split_grab(result, fallback_screen):
    """``grab`` 允许返回 ``(image, screen)``（默认抓屏）或直接 ``QImage``。"""
    if isinstance(result, tuple):
        image = result[0] if result else None
        screen = result[1] if len(result) > 1 and result[1] is not None else fallback_screen
        return image, screen
    return result, fallback_screen


def _image_dpr(image: QImage, screen) -> float:
    """冻结帧的逻辑→物理倍率：优先用图自身 DPR，其次屏 DPR（offscreen 恒 1）。"""
    try:
        dpr = sanitize_dpr(image.devicePixelRatio())
    except Exception:
        dpr = 1.0
    if dpr > 1.0:
        return dpr
    try:
        screen_dpr = sanitize_dpr(screen.devicePixelRatio())
    except Exception:
        screen_dpr = 1.0
    return screen_dpr if screen_dpr > 1.0 else 1.0


def _crop(image: QImage, selection: SelectionRect, dpr: float) -> QImage:
    left, top, width, height = selection.device_rect(dpr)
    left = max(0, min(left, max(0, image.width() - 1)))
    top = max(0, min(top, max(0, image.height() - 1)))
    width = min(width, image.width() - left)
    height = min(height, image.height() - top)
    if width <= 0 or height <= 0:
        raise ScreenSelectionCancelled("选区为空")
    cropped = image.copy(left, top, width, height)
    if cropped.isNull():
        raise ScreenError("capture_failed", "截图为空")
    return cropped


def _run_overlay(overlay) -> tuple[str, SelectionRect]:
    """显示覆盖层并等一次确认；取消/关闭都返回空结果。"""
    outcome: dict[str, object] = {}
    loop = QEventLoop()

    def on_accepted(*args) -> None:
        # 新签名 (action, selection)；旧测试替身可能只发 selection。
        if len(args) >= 2:
            action, selection = args[0], args[1]
        else:
            action, selection = "ask", (args[0] if args else None)
        outcome["action"] = str(action or "")
        outcome["selection"] = selection
        loop.quit()

    def on_cancelled(reason) -> None:
        outcome["cancelled"] = str(reason or "cancelled")
        loop.quit()

    overlay.accepted.connect(on_accepted)
    overlay.cancelled.connect(on_cancelled)
    try:
        overlay.show()
        # 注入的覆盖层可能在 show() 里就确认了；已出结果就别再进事件循环。
        if not outcome:
            loop.exec()
    finally:
        try:
            overlay.close()
        except Exception:
            pass
    if "cancelled" in outcome:
        raise ScreenSelectionCancelled()
    selection = outcome.get("selection")
    if not usable(selection):
        raise ScreenSelectionCancelled()
    return str(outcome.get("action") or "ask"), selection


def _copy_image_to_clipboard(image: QImage) -> bool:
    """把裁好的图放进系统剪贴板；平台剪贴板不可用时不抛（动作仍算完成）。"""
    try:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setImage(image)
        return True
    except Exception:  # noqa: BLE001 - 剪贴板是外部状态，失败不该拖垮截图
        return False


def select_region(
    window: QWidget | None = None,
    *,
    screen=None,
    grab: Callable | None = None,
    overlay_factory: Callable | None = None,
    capabilities: dict[str, bool] | None = None,
    reasons: dict[str, str] | None = None,
) -> RegionPick:
    """让用户框选一块屏幕区域、在动作条上选一个动作，返回裁好的画面 + 动作。

    ``grab`` 缺省用 :func:`grab_screen_image`（冻结整屏）；``overlay_factory``
    缺省是 :class:`~petcore.ui.screen.overlay.SelectionOverlay`，签名
    ``factory(frame: QImage, screen, capabilities=..., reasons=...)``。
    ``copy`` 动作在这里完成剪贴板写入（core UI 层，不经过任何 mod）。
    """
    target = _target_screen(window, screen)
    if target is None:
        raise ScreenError("no_screen", "没有可用的显示器")
    grabber = grab if grab is not None else grab_screen_image
    if grab is None:
        from ...platform.screenshot import ensure_capture_permission
        ensure_capture_permission(request=True)
    image, target = _split_grab(grabber(target), target)
    if image is None or image.isNull():
        raise ScreenError("capture_failed", "无法读取屏幕内容")

    caps = dict(DEFAULT_CAPABILITIES)
    caps.update({str(k): bool(v) for k, v in (capabilities or {}).items()})
    factory = overlay_factory
    if factory is None:
        from .overlay import SelectionOverlay

        factory = SelectionOverlay
    try:
        overlay = factory(
            image, target, capabilities=caps, reasons=dict(reasons or {})
        )
    except TypeError:  # 旧测试替身不支持 capabilities 关键字
        overlay = factory(image, target)
    action, selection = _run_overlay(overlay)

    cropped = _crop(image, selection, _image_dpr(image, target))
    if action == ACTION_COPY:
        _copy_image_to_clipboard(cropped)
    try:
        name = str(target.name() or "")
    except Exception:
        name = ""
    return RegionPick(
        capture=capture_from_image(cropped, screen_name=name),
        action=action,
    )
