"""Window placement helpers for runtime markers and multi-instance avoidance.

The functions in this module keep the runtime-marker policy independent from the
Qt window implementation.  PetWindow retains thin compatibility methods that
pass itself as the host, so existing callers and test patches continue to work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import logging

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication

from . import catalog

from . import slot_manager as slot_manager_mod


def rects_overlap(x: int, y: int, w: int, h: int, other) -> bool:
    """Return whether two window rectangles overlap."""
    ox, oy, ow, oh = other
    return x < ox + ow and ox < x + w and y < oy + oh and oy < y + h


def pid_alive(pid: int) -> bool:
    """Check whether a process is alive using the platform-aware slot manager."""
    return slot_manager_mod.pid_alive(pid)


def runtime_marker_versioned(host: Any) -> bool:
    """Return whether the host uses the versioned runtime-marker name."""
    return bool(getattr(host, '_single_process_spawn', False))


def live_instance_rects(
    host: Any,
    *,
    pid_alive_fn: Callable[[int], bool] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Return rectangles from other live runtime-marker instances.

    Dead processes and malformed marker files are cleaned by
    ``slot_manager.read_live_instances``.  In a same-process multi-window setup,
    only this host's marker is excluded; other windows with the same pid remain
    candidates for avoidance.
    """
    versioned = runtime_marker_versioned(host)
    own_marker = slot_manager_mod.runtime_marker_path(
        host.cfg.dir,
        host.cfg.instance_id,
        versioned=versioned,
    )
    rects: list[tuple[int, int, int, int]] = []
    if pid_alive_fn is None:
        pid_alive_fn = pid_alive
    for _pid, x, y, w, h in slot_manager_mod.read_live_instances(
        host.cfg.dir,
        exclude_markers=[own_marker],
        pid_alive_fn=pid_alive_fn,
    ):
        if w > 0 and h > 0:
            rects.append((x, y, w, h))
    return rects


def write_runtime_marker(host: Any) -> None:
    """Register the host's current position for later instances to avoid."""
    slot_manager_mod.write_runtime_marker(
        host.cfg.dir,
        host.cfg.instance_id,
        host.x(),
        host.y(),
        host._w,
        host._h,
        versioned=runtime_marker_versioned(host),
    )


def remove_runtime_marker(host: Any) -> None:
    """Remove the host's runtime marker during explicit window shutdown."""
    slot_manager_mod.delete_runtime_marker(
        host.cfg.dir,
        host.cfg.instance_id,
        versioned=runtime_marker_versioned(host),
    )


def arm_screen_restore_retry(host) -> None:
    """目标副屏暂未就绪：启动 5s 轮询 + screenAdded 监听，等它上线。"""
    from .window import time as _window_time  # 兼容 seam：与 HEAD 同读 pet.window.time
    app = QGuiApplication.instance()
    if app is None:
        return
    host._screen_retry_deadline = _window_time.monotonic() + 120.0
    if not host._screen_restore_armed:
        app.screenAdded.connect(host._screen_retry_tick)
        host._screen_restore_armed = True
        logging.debug('已监听屏幕变化，等待 %s 上线', host._awaiting_saved_screen)
    host._screen_retry_timer.start()  # start() 即重启，超时窗口随之刷新


def disarm_screen_restore_retry(host) -> None:
    host._awaiting_saved_screen = None
    if hasattr(host, '_screen_retry_timer'):
        host._screen_retry_timer.stop()
    if not host._screen_restore_armed:
        return
    host._screen_restore_armed = False
    app = QGuiApplication.instance()
    if app is not None:
        try:
            app.screenAdded.disconnect(host._screen_retry_tick)
        except (RuntimeError, TypeError):
            pass


def screen_retry_tick(host, *_args) -> None:
    """轮询/screenAdded 共用入口：目标屏一旦进入枚举立即恢复位置。"""
    from .window import time as _window_time  # 兼容 seam：与 HEAD 同读 pet.window.time
    target = host._awaiting_saved_screen
    if not target:
        host._disarm_screen_restore_retry()
        return
    if _window_time.monotonic() > host._screen_retry_deadline:
        # 超时也不能把窗口留在不可见的幻影屏上（启动时 Qt 可能枚举到
        # 空名字/假几何的占位屏，show() 到上面真实桌面不可见）：强制
        # 落到当前主屏并确保可见。宁可位置不理想，不可窗口消失。
        logging.info('等待屏幕 %s 超时（120s），强制落到当前主屏', target)
        host._disarm_screen_restore_retry()
        host._force_show_on_primary()
        return
    # _screen_available 找不到目标屏时回退当前屏（名字不匹配），找到才算上线
    scr = host._screen_available(target)
    if scr is not None and scr.name() == target:
        host._disarm_screen_restore_retry()
        host._restore_position()
        host._ensure_visible_after_restore()
        logging.info('目标屏幕 %s 上线，已恢复到保存位置', target)


def force_show_on_primary(host) -> None:
    """幻影屏兜底：把窗口强制恢复到当前主屏并确保可见。

    启动竞态下 QScreen 枚举可能给出空名字/假几何的占位屏，窗口 show()
    到那个坐标系后真实桌面不可见（MainWindowHandle=0）。此路径保证
    窗口最终一定落在真实可见的屏幕上。
    """
    host._restore_position()
    host._awaiting_saved_screen = None
    host._ensure_visible_after_restore()


def ensure_visible_after_restore(host) -> None:
    """恢复重试只 move() 不改可见性：若窗口曾被自动隐藏/未显示，
    恢复后必须补一次 show()，否则窗口永远停在隐藏态（桌面无窗体）。"""
    if not host.isVisible():
        host.show()


def on_screen_added_restore(host, screen) -> None:
    """兼容入口：新屏幕上线 → 立即触发一次检查。"""
    host._screen_retry_tick()

# ================================================================ 尺寸


def screen_available(host, screen_name: str | None = None):
    """返回指定或窗口所在屏幕；macOS 上 host.screen() 失效时兜底主屏。"""
    if screen_name:
        for screen in QGuiApplication.screens():
            if screen.name() == screen_name:
                return screen
    scr = host.screen()
    if scr is None:
        scr = QGuiApplication.primaryScreen()
    return scr


def visible_content_rect(host) -> QRect:
    """Return the current visible character bounds in global coordinates.

    The pet window includes a transparent canvas and landing padding. The
    alpha mask is the source of truth for the actual visible character, so
    other windows can be placed beside the character instead of beside the
    transparent canvas.
    """
    frame_rect = host.frameGeometry()
    local_rect = host.character_local_region()
    if not local_rect.isEmpty():
        return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
    mask = host.mask()
    if not mask.isEmpty():
        local_rect = mask.boundingRect()
        if not local_rect.isEmpty():
            return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
    return frame_rect


def restore_position(host) -> None:
    """恢复上次位置（按屏幕比例），无记录则落右下角。
    保存位置时所在的屏幕此刻不在线（如开机自启时副屏未就绪）→
    落当前屏并记下目标屏，由 screenAdded 监听在它上线后重新恢复。"""
    saved_screen = host.cfg.get('screen_name')
    scr = host._screen_available(saved_screen)
    if saved_screen and scr.name() != saved_screen:
        host._awaiting_saved_screen = saved_screen
        logging.info('目标屏幕 %s 暂不在线，先落在 %s，等它上线后自动恢复',
                     saved_screen, scr.name())
    else:
        host._awaiting_saved_screen = None
    avail = scr.availableGeometry()
    rx, ry = host.cfg.get('rx'), host.cfg.get('ry')
    if rx is None or ry is None:
        x = avail.right() - host._w - catalog.CORNER_MARGIN
        y = avail.bottom() - host._h
    else:
        x = int(round(avail.left() + rx * avail.width())) - host._w // 2
        y = int(round(avail.top() + ry * avail.height())) - host._h // 2
        x = min(max(x, avail.left()), avail.right() - host._w)
        y = min(max(y, avail.top()), avail.bottom() - host._h)
    # 多开避让：与其他存活实例重叠时逐级向左错开（含双击重复启动
    # 同一实例的场景——它和有名字的 --instance 一样会撞位置）
    _rects_fn = getattr(host, '_live_instance_rects', None)
    others = _rects_fn() if callable(_rects_fn) else []
    if others:
        step = host._w + 48
        for _ in range(12):
            if not any(host._rects_overlap(x, y, host._w, host._h, o) for o in others):
                break
            nx = max(avail.left(), x - step)
            if nx == x:
                break  # 已经顶到屏幕左缘，无法再让
            x = nx
    logging.info('恢复位置 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                 scr.name(), avail.left(), avail.top(), avail.right(),
                 avail.bottom(), scr.devicePixelRatio(), x, y)
    host.move(x, y)
    _marker_fn = getattr(host, '_write_runtime_marker', None)
    if callable(_marker_fn):
        _marker_fn()


def save_position(host) -> None:
    """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。
    等待目标副屏上线期间（_awaiting_saved_screen 非空）不写位置/屏名：
    当前只是临时落脚主屏，写回会把保存的副屏坐标永久覆盖。"""
    scr = host._screen_available()
    avail = scr.availableGeometry()
    if avail.width() <= 0 or avail.height() <= 0:
        return
    if not getattr(host, '_awaiting_saved_screen', None):
        cx = host.x() + host._w / 2
        cy = host.y() + (host._h + getattr(host, "_capture_headroom", 0)) / 2
        host.cfg.set('rx', (cx - avail.left()) / avail.width())
        host.cfg.set('ry', (cy - avail.top()) / avail.height())
        host.cfg.set('screen_name', scr.name())
    host.cfg.set('facing', host.facing)
    host.cfg.set('scale', host.scale)
    host.cfg.save()
    _marker_fn = getattr(host, '_write_runtime_marker', None)
    if callable(_marker_fn):
        _marker_fn()


def go_default_corner(host) -> None:
    # 用户明确要求回右下角 = 手动位置决策，撤销"等副屏上线自动恢复"
    _disarm = getattr(host, '_disarm_screen_restore_retry', None)
    if callable(_disarm):
        _disarm()
    # Position can still be written by the animation interpolation timer or
    # drag-physics timer after a direct move. Stop both first, otherwise the
    # pet briefly reaches the corner and is immediately snapped back.
    host._cancel_move()
    host._stop_physics()
    host._drag_target = None
    scr = host._screen_available()
    avail = scr.availableGeometry()
    x = avail.right() - host._w - catalog.CORNER_MARGIN
    y = avail.bottom() - host._h
    logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                 scr.name(), avail.left(), avail.top(), avail.right(),
                 avail.bottom(), scr.devicePixelRatio(), x, y)
    host.move(x, y)
    host._save_position()
