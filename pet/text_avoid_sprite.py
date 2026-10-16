# -*- coding: utf-8 -*-
"""避字移动在 **overlay/sprite 拓扑**（v4.3.0 起的默认拓扑）下的接线。

为什么需要单独一层
------------------
默认拓扑的自行移动走 ``pet/sprite_behavior.py::_plan_move``，而该模块**不 import**
旧窗口路径；几何口径也换了一套：桌宠不再是"自己的小窗"，而是画在一张铺满屏的
合成窗里的 sprite。控制器要的宿主面（cfg / 位置 / 身体框 / dpr / 窗口句柄 /
移动空间 / 是否在路上）在 sprite 世界里都有等价物，本模块就负责把两者对上。

分工
----
- ``pet/text_avoid.py``：纯逻辑与采集原语（零 Qt）；
- ``pet/text_avoid_controller.py``：Qt 侧装配（后台刷文字图 + 决策入口）；
- **本模块**：宿主适配 + 接进 ``BehaviorController``（``text_avoid_plan`` 改道钩子、
  ``request_move_now`` 立刻走开的出口）。

为什么"每只 sprite 一个控制器"
------------------------------
与 legacy「每个 ``PetWindow`` 一个」同构：决策、冷却、白走退避这些状态天然按只
隔离，不必把控制器改成多实例共享。代价是文字图（屏幕级）在 N 只时会被各刷一遍
——单只时与 legacy 完全等价，多只的合并留给后续（见报告 §14.4）。

"看穿自己脚下"在新拓扑下**依然成立**：overlay 是本进程的窗口，被
``topmost_window_under(exclude_pid=...)`` 排除后，底下那个用户窗口照旧能被
PrintWindow 取到（实机探针：命中 DSH 窗口并拿到 1294×829 真实内容）。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from PySide6.QtCore import QObject, QPointF, QRect

from . import movement, platform_win, text_avoid
from .sprite_behavior import STATE_MOVE
from .text_avoid_controller import TextAvoidController

log = logging.getLogger('dsh-pet-standalone')

#: 未接线/降级时给行为层的返回值：原样不动 + 纵向不改 + 不加速。
_PASSTHROUGH = (None, 1.0)


class _SpriteHost(QObject):
    """把一个 sprite + 它的 overlay 适配成 ``TextAvoidController`` 要的宿主。

    控制器只用到下面这几面（与 legacy 的 ``WindowFeatureGateMixin`` 同款）：
    ``cfg`` / ``_virtual_pos`` / ``_stable_body_local_rect`` / ``devicePixelRatioF`` /
    ``winId`` / ``_move_space`` / ``_move_plan``。**坐标一律"全局逻辑像素"**：
    overlay 的全局原点 + overlay 局部的 sprite 坐标。

    继承 QObject 只为一件事：``TextAvoidController`` 是 QObject，构造时会把宿主当
    **父对象**（legacy 里宿主正是 ``PetWindow``）。不继承就会在构造处报
    "QObject.__init__ called with wrong argument types"。
    """

    def __init__(self, shell, sprite, bounds: QRect) -> None:
        super().__init__(None)
        self._shell = shell
        self._sprite = sprite
        self._bounds = QRect(bounds)

    # ------------------------------------------------------------------ 属性面
    @property
    def cfg(self):
        return self._shell._config

    @property
    def _move_plan(self):
        """控制器用它判断"此刻在路上吗"：sprite 世界等价于状态机停在 move 态。"""
        behavior = getattr(self._shell, 'behavior', None)
        if behavior is None:
            return None
        return {} if behavior.state_of(self._sprite) == STATE_MOVE else None

    # ------------------------------------------------------------------ 几何面
    def _overlay(self):
        return getattr(self._shell, 'overlay', None)

    def _origin(self) -> tuple[float, float]:
        """overlay 的全局逻辑原点（多屏/非零原点时 sprite 坐标要加上它）。"""
        overlay = self._overlay()
        if overlay is None:
            return 0.0, 0.0
        try:
            pos = overlay.pos()
        except Exception:
            return 0.0, 0.0
        return float(pos.x()), float(pos.y())

    def _virtual_pos(self) -> QPointF:
        """sprite 的全局逻辑左上角（CoordMap 的锚要用它）。"""
        ox, oy = self._origin()
        return QPointF(ox + float(self._sprite.pos.x()), oy + float(self._sprite.pos.y()))

    def _stable_body_local_rect(self) -> QRect:
        """稳定身体框，**相对 sprite 左上角**（对 legacy 的"相对窗口"）。"""
        r = self._sprite.rect()
        b = self._sprite.body_rect()
        return QRect(b.x() - r.x(), b.y() - r.y(), b.width(), b.height())

    def devicePixelRatioF(self) -> float:
        overlay = self._overlay()
        if overlay is not None:
            try:
                return float(overlay.devicePixelRatioF() or 1.0)
            except Exception:
                pass
        return 1.0

    def winId(self) -> int:
        overlay = self._overlay()
        if overlay is None:
            return 0
        try:
            return int(overlay.winId())
        except Exception:
            return 0

    def _coord_physical_rect(self):
        """与 :meth:`_virtual_pos` **配对**的物理矩形（``CoordMap`` 只用它的原点）。

        sprite 不是一个窗口，所以"它的物理矩形"必须自己算：以 overlay 的逻辑位置 ↔
        物理原点为参照，把 sprite 的全局逻辑位置换算成物理：

            px = overlay_px + (sprite_logical − overlay_logical) × dpr

        配错会整体偏一个 sprite 的距离——实测把 overlay 的物理矩形配上 sprite 的逻辑
        位置时，`CoordMap.anchor` 被算成 sprite 自己的逻辑位置，身体框与 OCR 条带一起
        偏移（条带跑到 x=−820，几乎全在屏幕外）。拿不到 overlay 的物理矩形时返回 None，
        由控制器回落到按 HWND 查（那时至少不自相矛盾）。
        """
        overlay = self._overlay()
        if overlay is None:
            return None
        try:
            rect = platform_win.window_physical_rect(int(overlay.winId()))
        except Exception:
            return None
        if rect is None:
            return None
        base = overlay.pos()
        vp = self._virtual_pos()
        scale = self.devicePixelRatioF()
        px = rect[0] + (vp.x() - float(base.x())) * scale
        py = rect[1] + (vp.y() - float(base.y())) * scale
        return (int(round(px)), int(round(py)), 1, 1)

    def _screen_available(self):
        """控制器算 OCR 条带要用屏幕**可用区**（与 legacy 同接口，返回 QScreen）。

        优先取 overlay 所在那块屏（多屏时条带不该跨到别的屏上算）；拿不到
        （未建原生窗口/无 windowHandle）再回退主屏。返回 None 时控制器会按
        "几何取不到"降级——功能不生效但绝不出错。
        """
        overlay = self._overlay()
        if overlay is not None:
            try:
                handle = overlay.windowHandle()
                screen = handle.screen() if handle is not None else None
            except Exception:
                screen = None
            if screen is not None:
                return screen
        try:
            from PySide6.QtGui import QGuiApplication
            return QGuiApplication.primaryScreen()
        except Exception:
            return None

    def body_geometry(self) -> tuple[float, float, float, float]:
        """(off_x, off_y, body_w, body_h) —— 与 ``BehaviorController._body_geometry`` 同口径。

        走 sprite 的公开几何（``body_rect`` / ``rect``），不碰行为层的私有方法。
        """
        body = self._stable_body_local_rect()
        return (float(body.x()), float(body.y()),
                float(body.width()), float(body.height()))

    def _margin(self) -> float:
        behavior = getattr(self._shell, 'behavior', None)
        return float(getattr(behavior, 'margin', 0.0) or 0.0)

    def _move_space(self):
        return self.move_space()

    def move_space(self):
        """控制器要的"移动空间"（``cx/left/right/avail``），全局逻辑像素。

        与 ``BehaviorController._plan_move`` 里 ``movement.body_reach`` 的算法同源，
        只是把结果平移到全局坐标——决策与实际规划必须用同一套边界口径，否则
        避字算出来的落点会与实际能走的范围差一个 overlay 原点。
        """
        off_x, _off_y, body_w, _body_h = self.body_geometry()
        cx, left, right = movement.body_reach(
            self._bounds.left(), self._bounds.right(),
            float(self._sprite.pos.x()) + off_x, body_w, self._margin())
        ox, oy = self._origin()
        avail = QRect(self._bounds)
        avail.translate(int(ox), int(oy))
        return SimpleNamespace(
            vp=self._virtual_pos(),
            sbr=self._stable_body_local_rect(),
            avail=avail,
            cx=cx + ox, left=left + ox, right=right + ox,
        )


class SpriteTextAvoid:
    """一个 overlay 壳的避字接线：每只 sprite 一个控制器，一个注入给行为层的钩子。

    ``BehaviorController.text_avoid_plan`` 只接一次（本对象的 :meth:`plan`），
    由它按 sprite 分发——行为层因此完全不认识 OCR，也不需要按只持有回调。
    """

    def __init__(self, shell) -> None:
        self._shell = shell
        self._hosts: dict = {}
        self._ctrls: dict = {}
        self._ocr_logged = False

    # ---------------------------------------------------------------- 启停同步
    def sync(self) -> None:
        """按配置启停；顺带回收已消失的 sprite（幂等，可反复调用）。

        调用点：壳启动、sprite 增删、以及**配置重载**（``_sync_sprite_settings``）。
        """
        sprites = list(getattr(getattr(self._shell, 'overlay', None), 'sprites', []) or [])
        if not self._wanted():
            for sprite in list(self._ctrls):
                self._stop(sprite)
            return
        for sprite in sprites:
            self._ensure(sprite)
        for sprite in list(self._ctrls):
            if sprite not in sprites:
                self._stop(sprite)

    def shutdown(self) -> None:
        """壳收口：停掉全部控制器（后台线程与子进程随之收口）。"""
        for sprite in list(self._ctrls):
            self._stop(sprite)

    def _wanted(self) -> bool:
        cfg = self._shell._config.get('text_avoid', {})
        if not (cfg if isinstance(cfg, dict) else {}).get('enabled', False):
            return False
        if text_avoid.ocr_available():
            return True
        if not self._ocr_logged:
            self._ocr_logged = True
            log.info('避字移动：本机没有可用的本地 OCR（缺 rapidocr/onnxruntime '
                     '或模型文件），功能静默降级')
        return False

    def _ensure(self, sprite) -> None:
        if sprite in self._ctrls:
            return
        bounds = getattr(self._shell, 'bounds', None) or getattr(self._shell, '_bounds', None)
        if bounds is None:
            return
        host = _SpriteHost(self._shell, sprite, bounds)
        ctrl = TextAvoidController(host)
        ctrl.escape_requested.connect(lambda s=sprite: self._escape(s))
        self._hosts[sprite] = host
        self._ctrls[sprite] = ctrl
        ctrl.start()
        log.info('避字移动已启用（sprite 拓扑）：后台刷新屏幕文字图；桌宠自己挡住的那块'
                 '用 PrintWindow 抓底下窗口补回（不改变窗口显示属性）')

    def _stop(self, sprite) -> None:
        ctrl = self._ctrls.pop(sprite, None)
        self._hosts.pop(sprite, None)
        if ctrl is not None:
            try:
                ctrl.stop()
            except Exception:
                log.debug('避字移动：控制器停止失败', exc_info=True)

    # ---------------------------------------------------------------- 行为层钩子
    def plan(self, sprite, dir_sign: int, distance: float, room: float, stride: float):
        """注入给 ``BehaviorController.text_avoid_plan``：返回 5 元组或原样。

        未启用 / 控制器缺席 / 任何异常 → 返回"原样 + 纵向不改 + 不加速"，行为与
        没接线时逐字节一致（这条降级契约与 legacy 钩子同一口径）。
        """
        ctrl = self._ctrls.get(sprite)
        if ctrl is None:
            return (dir_sign, distance, room, *_PASSTHROUGH)
        try:
            sp = self._hosts[sprite].move_space()
            return ctrl.adjust_move(sp, dir_sign, distance, room, stride)
        except Exception:
            log.debug('避字移动决策失败，按原落点走', exc_info=True)
            return (dir_sign, distance, room, *_PASSTHROUGH)

    def _escape(self, sprite) -> None:
        """控制器判定"此刻正压着字" → 让行为层立刻起一次移动。"""
        behavior = getattr(self._shell, 'behavior', None)
        if behavior is None:
            return
        try:
            started = bool(behavior.request_move_now(sprite))
        except Exception:
            log.debug('避字移动：立刻走开失败', exc_info=True)
            return
        if started:
            log.info('避字移动：立刻走开（sprite 拓扑）')
        else:
            log.debug('避字移动：立刻走开被拒（接管态/无素材/无可达空间）')
