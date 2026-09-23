"""Window placement helpers for runtime markers and multi-instance avoidance.

The functions in this module keep the runtime-marker policy independent from the
Qt window implementation.  PetWindow retains thin compatibility methods that
pass itself as the host, so existing callers and test patches continue to work.

多显示器活动区域也在这里：``desktop_area()`` 取一次拖拽/抛掷的桌面快照
（``DesktopArea``），``band_bounds()`` 按宠物当前所在的屏幕带收窄它，
``move_window_towards`` / ``throw_bounds`` 在有快照时用它、没有快照时逐位退回
本屏语义（单屏用户与漫游/落位/边缘探头不受影响）。见
``docs/PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import logging

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

from . import catalog

from . import slot_manager as slot_manager_mod


def clamp_span(value: int, lo: int, hi: int, span: int) -> int:
    """把 value 钳进 [lo, hi - span + 1]（hi 为含端点右/下界）。

    可用区比窗口还窄/矮（小屏）时上界 < 下界，min/max 互相打架会把窗口
    推出屏幕外；此时钳到下界（与 app._apply_spawn_offset 同一模式）。
    """
    upper = hi - span + 1
    if upper < lo:
        return lo
    return min(max(value, lo), upper)


def stable_body_local_rect(host) -> QRect:
    """稳定身体框（窗口局部坐标，绘制偏移为零时）。

    定位基准必须取稳定量而非当前帧（待机帧可见框几乎不抖，特效动画逐帧
    可摆上百像素）。来源：角色 manifest 的 body_box（源像素、已镜像对称
    化）；未声明的角色包回退为整个窗口——语义等同"窗口即身体"，贴边补偿
    自动退化为纯窗口钳位（与改造前行为一致）。
    """
    box = catalog.character_body_box(str(host.cfg.get('character', '') or ''))
    if box is None:
        return QRect(0, 0, host._w, host._h)
    x1, y1, x2, y2 = box
    s = host.scale
    return QRect(
        int(round(x1 * s)),
        getattr(host, "_capture_headroom", 0) + int(round(catalog.PAD * s)) + int(round(y1 * s)),
        max(1, int(round((x2 - x1) * s))),
        max(1, int(round((y2 - y1) * s))),
    )


@dataclass(frozen=True)
class DesktopArea:
    """一次拖拽/抛掷交互的多屏桌面快照。

    screens = 各屏可用区（沿用 availableGeometry，继续避开任务栏等系统保留区）；
    bounds = 它们的包围矩形，作为交互的粗活动范围。多屏下"能不能越屏"由
    ``band_bounds`` 按宠物当前所在的屏幕带再收窄，避免落进错位拼接的空洞。
    """

    bounds: QRect
    screens: tuple[QRect, ...]


def _valid_rect(rect: QRect | None) -> bool:
    return rect is not None and rect.width() > 0 and rect.height() > 0


def desktop_area() -> DesktopArea | None:
    """取当前多屏桌面区域；有效屏不足 2 块或任一屏几何异常时返回 None。

    None 表示"按单屏语义走"——单屏用户、屏几何拿不到时与改造前逐位一致。
    只在一次拖拽/抛掷开始时调用（window 侧生命周期），不在物理 tick 里重复枚举。
    """
    try:
        screens = list(QGuiApplication.screens() or ())
    except Exception:  # 极端情况下 Qt 查询本身失败：退回本屏语义
        return None
    if len(screens) < 2:
        return None
    rects: list[QRect] = []
    for screen in screens:
        try:
            area = QRect(screen.availableGeometry())
        except Exception:
            return None
        if not _valid_rect(area):
            return None
        rects.append(area)
    bounds = QRect(rects[0])
    for rect in rects[1:]:
        bounds = bounds.united(rect)
    return DesktopArea(bounds=bounds, screens=tuple(rects))


def band_bounds(area: DesktopArea, body: QRect) -> QRect:
    """把桌面包围矩形收窄到"宠物当前所在的屏幕带"。

    与身体框 x 带相交的屏决定上下边界，与 y 带相交的屏决定左右边界：错位拼接
    （异分辨率/异缩放、上下错开）时包围矩形里有不属于任何显示器的空洞，直接用
    包围矩形会让宠物停进空洞里看不见。收窄后身体框恒与某块屏的可用区相交
    （身体框整个落在某条带之外时用另一条带的屏兜底，例如往主屏正下方的空洞里拖）。

    拼接整齐的布局（同尺寸并列/上下堆叠）收窄结果恒等于包围矩形，常见双屏行为
    不受影响；单屏路径根本不进这里（desktop_area 返回 None）。
    """
    vertical_band = [r for r in area.screens
                     if r.right() >= body.left() and r.left() <= body.right()]
    horizontal_band = [r for r in area.screens
                       if r.bottom() >= body.top() and r.top() <= body.bottom()]
    if not vertical_band and not horizontal_band:
        return QRect(area.bounds)  # 请求整体在桌面之外：交给包围矩形钳回边缘
    vertical_band = vertical_band or horizontal_band
    horizontal_band = horizontal_band or vertical_band
    left = min(r.left() for r in horizontal_band)
    right = max(r.right() for r in horizontal_band)
    top = min(r.top() for r in vertical_band)
    bottom = max(r.bottom() for r in vertical_band)
    return QRect(left, top, right - left + 1, bottom - top + 1)


def virtual_pos(host) -> QPoint:
    """虚拟窗口位置 = 实际位置 + 绘制偏移（角色无约束时窗口该在的位置）。

    各移动路径（拖拽/抛掷/漫游/落位）的请求语义都是它；身体框屏幕位置
    = virtual_pos + 身体框局部左上，与实际窗口位置/绘制偏移无关。
    """
    delta = getattr(host, "_draw_delta", None)
    if delta is None:
        delta = QPoint(0, 0)
    return host.pos() + delta


def move_window_towards(host, x: float, y: float,
                        body_bounds: QRect | None = None,
                        *, interaction_area: DesktopArea | None = None) -> None:
    """统一位置出口：按虚拟窗口位置 (x, y) 落窗。

    GNOME/mutter 会把移出工作区的窗口整体钳回（raw X11 XMoveWindow 同样
    被钳，已实测），且钳位是异步的——请求非法位置会让窗口位置不可控。
    这里主动把窗口钳进工作区（位置确定性），角色身体则通过绘制偏移在
    窗口内继续平移、贴到工作区边缘：

    1. 身体框钳进工作区（或 body_bounds 指定的放宽区间，如边缘探头要把
       身体藏一半出屏）——角色不会被拖出屏幕丢失；
    2. 窗口钳进工作区——永不向 WM 请求非法位置；
    3. 绘制偏移 = 虚拟 - 实际，变化时 mask 与碰撞局部并集同步失效重算
       （画面/mask/碰撞体逐像素一致），并调度气泡/碰撞状态同步。

    屏幕中央请求时偏移恒为 (0,0)，Windows/macOS 行为与改造前逐像素一致。

    ``interaction_area`` 是多屏拖拽/抛掷的活动区域快照：给定时活动范围换成该
    快照（按宠物所在屏幕带收窄），角色得以跨屏；缺省时读 host 上缓存的快照
    （window 侧一次交互写一次、结束即清），没有快照就走本屏语义——单屏用户与
    漫游/落位/边缘探头逐位不变（显式 body_bounds 优先级最高，恒本屏）。
    """
    scr = host._screen_available()
    if scr is None:
        host.move(int(round(x)), int(round(y)))
        return
    if interaction_area is None and body_bounds is None:
        interaction_area = getattr(host, "_interaction_area", None)
    sbr = stable_body_local_rect(host)
    xi, yi = int(round(x)), int(round(y))
    if interaction_area is not None:
        bounds = band_bounds(interaction_area, QRect(
            xi + sbr.x(), yi + sbr.y(), sbr.width(), sbr.height()))
        avail = bounds
    else:
        avail = scr.availableGeometry()
        bounds = avail if body_bounds is None else body_bounds
    # 1. 身体框（= 虚拟位置 + 局部偏移）钳进工作区
    xi = clamp_span(xi + sbr.x(), bounds.left(), bounds.right(), sbr.width()) - sbr.x()
    yi = clamp_span(yi + sbr.y(), bounds.top(), bounds.bottom(), sbr.height()) - sbr.y()
    # 1b. 灵动岛同步硬墙（可选）：身体框不得进入岛碰撞区（像屏幕边界一样
    #     同步钳制，杜绝 30Hz 采样下"钻进区→被弹→再钻回"的抽搐）。hook 由
    #     岛碰撞体注册（IslandCollisionBody.start），无岛时是 no-op。
    island_clamp = getattr(host, "_island_clamp_body", None)
    if callable(island_clamp):
        xi, yi = island_clamp(host, xi, yi, sbr)
    # 2. 窗口钳进工作区
    wx = clamp_span(xi, avail.left(), avail.right(), host._w)
    wy = clamp_span(yi, avail.top(), avail.bottom(), host._h)
    # 3. 绘制偏移；变化时同步派生量
    delta = QPoint(xi - wx, yi - wy)
    old_delta = getattr(host, "_draw_delta", None)
    if old_delta is None:
        old_delta = QPoint(0, 0)
    if delta != old_delta:
        host._draw_delta = delta
        # 碰撞局部并集是窗口局部坐标：帧随偏移整体平移，旧积累平移后继续
        # 有效——置空重积累会让碰撞体在贴边期间突然收紧成单帧包围盒。
        bounds = getattr(host, "_collision_local_bounds", None)
        if bounds is not None and not bounds.isEmpty():
            host._collision_local_bounds = bounds.translated(delta - old_delta)
        sync_mask = getattr(host, "_sync_mask", None)
        if callable(sync_mask):
            sync_mask()  # 画面/mask 随帧平移，逐像素一致
        update = getattr(host, "update", None)
        if callable(update):
            update()
        sync = getattr(host, "_schedule_position_sync", None)
        if callable(sync):
            sync()  # 气泡按 visible_content_rect 全局坐标定位，偏移变化要重排
        submit = getattr(host, "_submit_collision_state", None)
        if callable(submit):
            submit()  # 碰撞体屏幕 rect 随偏移变化（20Hz 节流兜底）
    host.move(wx, wy)


def throw_bounds(host, *,
                 interaction_area: DesktopArea | None = None) -> tuple[float, float, float, float]:
    """抛掷/碰撞的虚拟窗口边界 (left, top, right, bottom)。

    语义 = 角色身体框贴到工作区四边（兑现原 `_w/3` 经验值注释里"让角色
    形象真正碰到边缘才反弹"的意图——该注释的前提"窗口可悬出屏幕"在
    GNOME 上不成立，改由绘制偏移兑现，边界算式随之改用身体框）。

    多屏抛掷：``interaction_area`` 快照（缺省读 host 上缓存的那份）把边界换成
    "宠物当前所在屏幕带"的范围，于是能从一块屏飞进相邻屏；单屏或没有快照时
    仍用本屏 availableGeometry，逐位不变。无显示器（屏幕对象拿不到）时退化为
    当前位置的零尺寸盒——冻结而不抛异常。
    """
    if interaction_area is None:
        interaction_area = getattr(host, "_interaction_area", None)
    sbr = stable_body_local_rect(host)
    if interaction_area is not None:
        pos = getattr(host, "_phys_pos", None) or (0.0, 0.0)
        avail = band_bounds(interaction_area, QRect(
            int(round(pos[0])) + sbr.x(), int(round(pos[1])) + sbr.y(),
            sbr.width(), sbr.height()))
    else:
        scr = host._screen_available()
        if scr is None:
            pos = getattr(host, "_phys_pos", None) or (0.0, 0.0)
            return (float(pos[0]), float(pos[1]), float(pos[0]), float(pos[1]))
        avail = scr.availableGeometry()
    return (
        float(avail.left() - sbr.x()),
        float(avail.top() - sbr.y()),
        float(avail.right() + 1 - sbr.x() - sbr.width()),
        float(avail.bottom() + 1 - sbr.y() - sbr.height()),
    )


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


def bubble_anchor_rect(host) -> QRect:
    """气泡定位锚点：碰撞稳定边界（当前动画各帧轮廓的并集）换算到全局坐标。

    不能用当前帧轮廓（visible_content_rect 的 _mask_bounds 分支）做气泡
    锚点——待机动画每帧的 alpha 包围盒都在小幅晃动，歌词气泡每秒重放
    一次就会跟着每秒跳一次（实机探针实测：鱼静止时气泡每秒 ±10px 抖动、
    偶发 40px 候选位跳变）。并集边界在同一段动画内恒定，气泡只在鱼真正
    移动/换动画/缩放时才挪。轮廓还没算出来时回退当前帧轮廓。
    """
    local = getattr(host, "_collision_local_bounds", None)
    if local is None or local.isEmpty():
        return host.visible_content_rect()
    frame_rect = host.frameGeometry()
    return QRect(frame_rect.topLeft() + local.topLeft(), local.size())


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
        x, y = _default_corner_pos(host, avail)
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
    _move_towards(host, x, y)
    _marker_fn = getattr(host, '_write_runtime_marker', None)
    if callable(_marker_fn):
        _marker_fn()


def _move_towards(host, x: float, y: float) -> None:
    """经统一出口落窗（虚拟窗口坐标）；轻量桩没有该出口时回退直接 move。"""
    mover = getattr(host, '_move_window_towards', None)
    if callable(mover):
        mover(x, y)
    else:
        host.move(int(round(x)), int(round(y)))


def _default_corner_pos(host, avail) -> tuple[int, int]:
    """默认右下角落位（虚拟窗口坐标）。

    有稳定身体框时按角色语义：身体右缘距可用区 CORNER_MARGIN、脚底贴
    可用区底。旧算式量的是画布窗口，实际残留 = CORNER_MARGIN + 素材透明
    边距（实测右侧 178px，而 CORNER_MARGIN 意图只有 24px）。无身体框
    （轻量桩/未声明 body_box 的角色包回退全窗口）时保持旧算式逐像素一致。
    """
    sbr_fn = getattr(host, '_stable_body_local_rect', None)
    sbr = sbr_fn() if callable(sbr_fn) else None
    if sbr is not None and (sbr.width(), sbr.height()) != (host._w, host._h):
        x = avail.right() - catalog.CORNER_MARGIN + 1 - sbr.x() - sbr.width()
        y = avail.bottom() + 1 - sbr.y() - sbr.height()
        return int(x), int(y)
    return (avail.right() - host._w - catalog.CORNER_MARGIN,
            avail.bottom() - host._h)


def save_position(host) -> None:
    """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。
    等待目标副屏上线期间（_awaiting_saved_screen 非空）不写位置/屏名：
    当前只是临时落脚主屏，写回会把保存的副屏坐标永久覆盖。"""
    scr = host._screen_available()
    avail = scr.availableGeometry()
    if avail.width() <= 0 or avail.height() <= 0:
        return
    if not getattr(host, '_awaiting_saved_screen', None):
        # 存"虚拟窗口中心"比例：贴边时窗口被钳在工作区内，实际位置 + 绘制
        # 偏移才是角色的自然位置；偏移为零时与旧算式（窗口中心）逐像素一致。
        vp_fn = getattr(host, '_virtual_pos', None)
        vp = vp_fn() if callable(vp_fn) else None
        cx = (vp.x() if vp is not None else host.x()) + host._w / 2
        cy = (vp.y() if vp is not None else host.y()) + (host._h + getattr(host, "_capture_headroom", 0)) / 2
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
    x, y = _default_corner_pos(host, avail)
    logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                 scr.name(), avail.left(), avail.top(), avail.right(),
                 avail.bottom(), scr.devicePixelRatio(), x, y)
    _move_towards(host, x, y)
    host._save_position()
