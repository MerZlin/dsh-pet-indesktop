# -*- coding: utf-8 -*-
"""单合成窗 overlay（Phase 1a 骨架）：一个全屏透明窗口承载全部宠物 sprite。

架构背景见 .scratch/single-overlay-window/spec.md：「每宠一个顶层窗口」在
Windows 合成器上是结构性税负（move() 1.5-4.3ms/次 + DWM 多窗竞争）；单
overlay 内合成后，宠物移动 = 改 sprite 坐标 + update(脏矩形)，完全不经过
WM/DWM 的窗口移动路径。Phase 0 实测（同目录 probe_report）该路线 GO。

本阶段（1b/2）：Windows 逐像素穿透已接通（复用 platform_win 的
WS_EX_TRANSPARENT 轮询 + 联合命中，见 showEvent）；Linux/X11 使用输入形状
保留 sprite 命中区，并把 overlay 扩展到虚拟桌面。
Phase 3a：右键菜单最小集（contextMenuEvent → sprite_menu.build_sprite_menu）；
鼠标路由只让左键进拖拽 grab，右键只弹菜单（PetWindow 旧语义）。
Phase 3b：sprite 位置监听（add_position_listener）——sprite 不产生 moveEvent，
气泡/聊天窗等外围小窗的跟随源平移为 tick 后 rect 变化 fanout（spec「Phase 3
设计」3b 节）。
Phase 4 D2/HiDPI：DPR 由 overlay 统一按所在屏喂给**全部** sprite
（add_sprite/showEvent/屏事件），屏幕 DPR 变化（显示缩放、跨屏、几何变化）
即重喂并按新 DPR 重建——对齐旧路径 window.py:2129-2218 的信号驱动语义。
Phase 4 M-2：仿真推进（行为/碰撞/抛掷物理）与 tick 时钟、M-1 档位状态机整体
移交给统一驱动器 ``tick_driver.TickDriver``（T2：驱动器独立持有控制器，调
``world.tick(全部 overlays 的 sprites, dt)``）；overlay 只保留职责范围内的
「advance → 脏矩形 → 位置监听 fanout → update」段（``tick_advance``）与
绘制/命中/鼠标路由。``start``/``stop``/``_on_tick``/``_note_kinetic`` 等旧属性面
保留为转发（deprecation shim，见各自 docstring）。

弹弓蓄力瞄准（sprite 世界移植，见 pet/sprite_slingshot.py）：
拖拽中右键 = 进瞄准（``slingshot_enabled`` 热读），移动调拉拽矢量，
松左键发射，Esc / 再点右键取消；瞄准期绘制橡皮带 + 轨迹预览。这要求
调和 V-13（原先「拖拽中收到任何非左键 press → 合成 release 收尾」）：
右键在拖拽中被弹弓消费时**不**合成 release——瞄准有明确退出路径
（松左键发射 / Esc / 再点右键取消 / 左键丢失看门狗），不会卡 drag 态；
其余非左键（中键等）与 ``slingshot_enabled`` 关闭时维持 V-13 原语义。
"""

from __future__ import annotations

import sys

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QCursor, QPainter, QRegion, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from . import catalog
from . import overlay_peripherals
from .linux_overlay import desktop_geometry, is_x11, LinuxOverlayInput
from .sprite_menu import build_sprite_menu
from .sprite_slingshot import SlingshotController
from .tick_driver import TickDriver

if sys.platform == "win32":
    from .platform_win import WindowsPerPixelInputController, _set_windows_no_activate

# 命中阈值：alpha >= 16 视为不透明（与现架构 _is_transparent_at 同口径）
ALPHA_HIT_THRESHOLD = 16


class OverlayWindow(QWidget):
    """全屏透明合成窗：sprites 有序列表即 z-order（尾部最上）。"""

    def __init__(self, screen: QScreen | None = None, parent: QWidget | None = None,
                 *, driver: TickDriver | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        from .platform_linux import keep_on_all_desktops
        keep_on_all_desktops(self)
        if is_x11():
            self.setWindowFlag(Qt.WindowType.X11BypassWindowManagerHint)
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # X11 covers the virtual desktop; other backends retain their screen geometry.
        self._screen = screen or QApplication.primaryScreen()
        self.setGeometry(desktop_geometry(self._screen))

        self.sprites: list = []
        self._mouse_grab = None
        # 弹弓蓄力瞄准（sprite 世界移植）：会话/数学/瞄准 UI 都在控制器里，
        # overlay 只负责事件调和与脏区刷新。壳层接线：把产品 config 交给它
        # （``overlay.slingshot.config = instance.config``）或整体替换本属性，
        # 见交付报告「壳层接线清单」。未接 config 时 enabled 取产品默认 True。
        self.slingshot = SlingshotController()
        # 瞄准 UI 上一次画出的外接矩形（QRegion）：取消/发射后连同新状态一起
        # 局部重绘，保证零残留（不整屏 repaint）。
        self._slingshot_dirty = QRegion()
        # 右键菜单一次性抑制（旧 window.py:2994/3589 同语义）：弹弓消费了右键
        # press 后，随之而来的 ContextMenu 事件必须丢弃（见 event()）。
        self._context_menu_suppressed = False
        # 键盘焦点：Esc 取消瞄准需要 widget 拿得到键盘焦点（旧 PetWindow:549
        # 同款 StrongFocus）。overlay 是防抢焦点窗口（Windows
        # WS_EX_NOACTIVATE），生产路径通常拿不到焦点——壳层全局 Esc 兜底见
        # 交付报告清单；本类不主动 setFocus（绝不抢用户前台焦点）。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # sprite 位置监听（Phase 3b）：sprite -> [cb]，tick 推进后 rect 发生
        # 变化的 sprite 触发其 listeners（cb(sprite)）；外围小窗（气泡等）的
        # 跟随源——sprite 不是窗口，没有 moveEvent 可挂。
        self._position_listeners: dict = {}
        # Windows 逐像素穿透协议字段（WindowsPerPixelInputController 直接复用，
        # 见 showEvent）：mouse_through = 用户手动"鼠标穿透"开关（恒穿透）；
        # _press_global 非 None = 拖拽中（穿透轮询据此降频并强制不穿透）。
        # X11 input shapes are handled by LinuxOverlayInput.
        self.mouse_through = False
        self._press_global: QPoint | None = None
        self._drag_committed = False  # 过 DRAG_THRESHOLD 才升级真拖拽（点击候选期 False）
        # 拖拽闸门（config 写入口，壳层经 _apply_window_capabilities 注入）：
        # 锁定位（lock_position）/ 必须按住 SHIFT 才能拖（shift_drag）。命中按下
        # 不进拖拽 grab，但松手仍按点击处理（旧 window.py:3126-3133 锁定 /
        # :3172-3181 SHIFT 门语义）。_press_click_only = 本次按下只算点击。
        self.lock_position = False
        self.shift_drag_required = False
        self._press_click_only = False
        # 最近一次已写入系统的不透明度（None = 还没写过；M5c）
        self._applied_opacity: float | None = None
        self._input_controller = None
        # sprite 移除通知（V-9）：行为控制器状态表等外部簿记的注销挂点
        self._sprite_removed_listeners: list = []
        # D2/P1：Qt 信号驱动 DPR 变化（QWindow.screenChanged + 所在屏
        # logical/physicalDotsPerInchChanged；Qt 6.11 无 devicePixelRatioChanged），
        # 与 geometryChanged 一起重喂 sprite。showEvent 接线，closeEvent 摘线。
        self._dpr_watch_window = None
        self._dpr_watch_screen = None

        # M-2 统一 tick 驱动器（T2）：仿真段 + tick 时钟 + M-1 档位全归驱动器
        # （tick_driver.TickDriver），overlay 只提供推进+重绘段（tick_advance）。
        # 未注入 driver 时自建私有驱动器——裸 overlay / demo 装配照旧可用；
        # 产品壳（overlay_shell）建一个进程级驱动器，挂全部屏的 overlay。
        self._driver = driver if driver is not None else TickDriver(self)
        self._driver.attach(self)
        # 旧属性面（deprecation）：overlay._timer 即驱动器时钟，供旧调用点
        # （测试同步驱动/间隔断言）沿用；新代码请走 tick_driver。
        self._timer = self._driver.timer
        self._linux_input = LinuxOverlayInput(self) if is_x11() else None

    @staticmethod
    def _tick_interval_ms(refresh_rate: float) -> int:
        """tick 间隔公式（V-6）：实现已随 M-2 迁到 TickDriver，本处保留旧入口
        （旧调用点/测试的静态引用面），语义逐位相同。"""
        return TickDriver.tick_interval_ms(refresh_rate)

    @property
    def screen(self) -> QScreen | None:
        """所在屏（只读）：M-2 后驱动器按它取刷新率（多 overlay 取最高者）。"""
        return self._screen

    @property
    def tick_driver(self) -> TickDriver:
        """统一 tick 驱动器（M-2）：持有控制器 / tick 时钟 / M-1 档位。"""
        return self._driver

    def _refresh_tick_interval(self) -> None:
        """重读屏幕刷新率并刷新 tick 间隔（V-6：电池 DRR 会在 170/60Hz
        间动态切换，构造时的一次性读数会变陈旧）；M-2 后委托驱动器。"""
        self._driver.refresh_tick_interval()

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        # attach 幂等：stop()（会 close/detach）后重启仍被驱动器驱动
        self._driver.attach(self)
        self._driver.start()

    def stop(self) -> None:
        self._driver.stop()

    # ---------------------------------------------------------------- M-1 闲置降档（挂点）
    def _note_user_input(self) -> None:
        """真实按键输入：交给 driver 做挂起自愈（丢了解锁消息也能点醒）。"""
        note = getattr(self._driver, "note_user_input", None)
        if callable(note):
            note()

    def _note_kinetic(self) -> None:
        """运动/输入信号（M-1：升档同步立即，不等下一个 tick）。

        M-2 后档位状态机、四档降档与 QTimer 应用整体在 TickDriver；overlay
        只把事件侧信号转发进去——鼠标事件与 sprite 位移回调都走这里。
        """
        if not self._alive():
            # sprite 的 _kinetic_cb 通道：窗口已销毁时驱动器侧任何一步
            # （_sync_tier 读 overlay.isVisible / 写 QTimer）都会抛（见 _alive）
            return
        self._driver.note_kinetic()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_tick_interval()  # V-6：显示时重读刷新率（电池 DRR）
        self._feed_dpr()               # V-11：DPR 由 overlay 统一喂
        self._arm_dpr_change_watch()   # D2/P1：屏 DPR 变化 → 重喂 + 重建
        self._note_kinetic()           # M-1：可见即回 T0（从 T3 唤醒）
        # O2：可见性上报（隐藏期停掉的光标探测在这里恢复，且立即补一次探测）
        overlay_peripherals.note_overlay_visibility(self, True)
        if sys.platform == "win32":
            if self._input_controller is None:
                # 逐像素穿透：未命中任何 sprite 的屏幕区域点击直达下层应用——
                # 全屏 overlay 不抢占桌面交互（硬指标"体验不回退"的底线）。
                self._input_controller = WindowsPerPixelInputController(self)
            else:
                # 隐藏期停过表：重开轮询并立即收敛一次穿透态（显示后不能有
                # 一段时间按陈旧样式吞点击）。
                self._input_controller.resume()
            # 点击宠物不夺前台焦点（issue #98 语义沿用）。每次显示都补一次：
            # on_top 开关会重建原生窗口，新句柄不带 WS_EX_NOACTIVATE。
            _set_windows_no_activate(int(self.winId()))

    def hideEvent(self, event) -> None:
        """隐藏期不轮询（O2）：不可见窗口不收输入，两条探测链路都是空转。

        - 逐像素穿透轮询（10/50ms：QCursor.pos + GetWindowLongW）停表；
        - 光标可见性探测（20Hz）停表——由可见性闸门转达（overlay 不认识
          watcher，多屏多 overlay 时语义也是进程级的「全都在隐藏」）；
        - 全屏探测 1Hz 表**不**停：它是全屏避让后恢复显示的唯一路径。

        三条隐藏路径都会走到这里：全屏避让（``_on_fullscreen_changed``）、
        托盘隐藏（``set_pet_visible(False)``）、屏迁移（旧 overlay close）。
        恢复侧见 ``showEvent``。
        """
        super().hideEvent(event)
        if self._input_controller is not None:
            self._input_controller.stop()
        overlay_peripherals.note_overlay_visibility(self, False)

    def closeEvent(self, event) -> None:
        # V-12：关窗即停 tick（M-2：从驱动器摘除本 overlay；最后一个摘除时
        # 驱动器停表——多 overlay 场景其余成员继续被驱动）
        self._driver.detach(self)
        self._disarm_dpr_change_watch()  # D2/P1：摘线，与 showEvent arm 对称
        if self._input_controller is not None:
            self._input_controller.stop()
            self._input_controller = None
        super().closeEvent(event)

    # ---------------------------------------------------------------- 存活守卫
    def _alive(self) -> bool:
        """窗口 C++ 侧是否仍存活（shiboken 包装器可能已被销毁）。

        下面三族入口都是**异步**投递：投递方不知道窗口已经销毁——屏热插拔重建
        overlay 时在途帧交付打到旧窗（overlay_shell 的 ``ShellOverlayWindow``
        重建路径），测试收尾也只用 deleteLater 销毁窗口而不走 closeEvent 摘线。
        死对象上的任何 Qt 调用都会以 ``RuntimeError: ... already deleted`` 从
        信号槽/事件泵里冒成 unraisable（pytest 按未捕获异常判败），故它们先
        过本闸：

        1. sprite 侧回调（``_dirty_cb`` → ``_on_sprite_dirty``、
           ``_kinetic_cb`` → ``_note_kinetic``）；
        2. TickDriver 每 tick 的 ``tick_advance``；
        3. QScreen 的 DPR/几何信号（``_on_window_screen_changed`` /
           ``_on_screen_dpi_changed`` / ``_on_screen_geometry_changed``）。

        同步 API（add_sprite/remove_sprite/事件处理）**不**过闸：调用方是窗口
        持有者，拿已销毁窗口调它们是持有者的缺陷，应当炸出来。
        """
        return shiboken6.isValid(self)

    def isVisible(self) -> bool:  # noqa: N802 (Qt 命名)
        """死窗口报「不可见」而不是抛异常（见 ``_alive``）。

        本属性被**本文件之外**的异步路径读：TickDriver 的档位评估
        （``_visible_for_tier`` 对每个已挂载 overlay 调 ``o.isVisible()``）在
        窗口销毁后仍会跑（closeEvent 没执行过的销毁路径摘不掉驱动器成员）。
        tick_driver 不在本文件、没法各自过 ``_alive`` 闸，把访问器本身做成
        死安全是这里唯一能一次覆盖全部调用方的做法——死窗口既不显示也不该
        参与档位评估，"不可见"正是它的真值。活窗口逐位转发 super()，语义不变。
        """
        if not self._alive():
            return False
        return super().isVisible()

    # ---------------------------------------------------------------- sprite 管理
    def add_sprite(self, sprite) -> None:
        """追加到 z-order 尾部（最上）；重复添加是 no-op。

        同时挂接 sprite 的脏上报回调（V-3/V-4）：set_pos 位移与帧到达
        直驱 update，不经 tick——物理/拖拽移动的旧位置即时清除，tick
        降档后动画帧率不随之掉。
        """
        if sprite in self.sprites:
            return
        self.sprites.append(sprite)

        def _on_sprite_dirty(old, new):
            if not self._alive():
                # 窗口已销毁（见 _alive）：这一步既动驱动器也 update，
                # 死对象上两者都会抛 RuntimeError 落成 unraisable。
                return
            if old != new:
                # 位移 = 运动信号（M-1）：物理/拖拽/行为位移同步升档
                self._note_kinetic()
            else:
                # 纯帧通知：记录动画活性（"动画在播"判定的输入）
                self._driver.note_frame()
            self.update(QRegion(old) | QRegion(new))

        sprite._dirty_cb = _on_sprite_dirty
        sprite._kinetic_cb = self._note_kinetic  # set_velocity 直报（M-1）
        # V-11：DPR 由 overlay 统一按所在屏喂（此前靠调用方记得，demo 从没
        # 喂过）；取 QScreen 而非 widget 的 devicePixelRatioF——窗口未
        # realize 前后者不可信（offscreen/壳层构建期恒 1.0）
        set_dpr = getattr(sprite, "set_dpr", None)
        if callable(set_dpr):
            set_dpr(float(self._screen.devicePixelRatio()))
        if self.isVisible():
            self.update(QRegion(sprite.rect()))
        # M12c：sprite 数从 0 恢复 → 驱动器重新起表
        self._driver.note_sprite_count_changed()

    # ---------------------------------------------------------------- DPR 归一化（D2）
    def _feed_dpr(self) -> None:
        """把所在屏 DPR 喂给全部 sprite（add_sprite/showEvent/屏事件）。

        覆盖所有 sprite（不只主宠）：4.2b 生成的子 sprite 同样必须按屏 DPR
        渲染，否则 125%/150% 下只有主宠清晰。只在 DPR 真正变化时补一次
        全窗重绘（同值零开销——屏幕信号会重复上报）；变化时 sprite 内部已
        按新 DPR 重建 pixmap 与命中图（PetSprite.set_dpr → _invalidate_frames）。
        """
        dpr = float(self._screen.devicePixelRatio())
        changed = False
        for sprite in self.sprites:
            set_dpr = getattr(sprite, "set_dpr", None)
            if not callable(set_dpr):
                continue
            before = getattr(sprite, "dpr", None)
            set_dpr(dpr)
            if getattr(sprite, "dpr", None) != before:
                changed = True
        if changed:
            self.update()

    @staticmethod
    def _connect_screen_signal(screen, name: str, slot) -> None:
        """按名连接屏信号；假屏缺该信号/已销毁时静默跳过（测试假屏无 DPI 信号）。"""
        sig = getattr(screen, name, None)
        if sig is None:
            return
        try:
            sig.connect(slot)
        except (TypeError, RuntimeError):
            pass

    @staticmethod
    def _disconnect_screen_signal(screen, name: str, slot) -> None:
        """按名断开屏信号；未连接过/屏已销毁时静默跳过。"""
        sig = getattr(screen, name, None)
        if sig is None:
            return
        try:
            sig.disconnect(slot)
        except (TypeError, RuntimeError):
            pass

    def _wire_screen_dpi_signals(self, screen) -> None:
        """把所在屏的 DPR/几何变化信号挂到重喂；跨屏时换挂新屏。

        Qt 6.11 的 QScreen 没有 devicePixelRatioChanged：显示缩放变化由
        logical/physicalDotsPerInchChanged 上报；geometryChanged 覆盖分辨率/
        模式切换（可能连带 DPR 变化）；refreshRateChanged 覆盖同分辨率下的
        刷新率切换（60↔165Hz，几何不变但 tick 档位必须重读，任务A 缺口）。
        名字与 PetWindow 同族方法一致，
        能力断言按 sprite 侧等价物对照（tests/test_sprite_dpr.py）。
        """
        if screen is None:
            return
        old = self._dpr_watch_screen
        if old is screen:
            return
        if old is not None:
            self._disconnect_screen_signals(old)
        self._dpr_watch_screen = screen
        for name in ("logicalDotsPerInchChanged", "physicalDotsPerInchChanged"):
            self._connect_screen_signal(screen, name, self._on_screen_dpi_changed)
        for name in ("geometryChanged", "availableGeometryChanged",
                     "refreshRateChanged"):
            self._connect_screen_signal(screen, name, self._on_screen_geometry_changed)

    def _disconnect_screen_signals(self, screen) -> None:
        for name in ("logicalDotsPerInchChanged", "physicalDotsPerInchChanged"):
            self._disconnect_screen_signal(screen, name, self._on_screen_dpi_changed)
        for name in ("geometryChanged", "availableGeometryChanged",
                     "refreshRateChanged"):
            self._disconnect_screen_signal(screen, name, self._on_screen_geometry_changed)

    def _arm_dpr_change_watch(self) -> None:
        """接线屏 DPR 变化信号（showEvent 调用；幂等，QWindow 重建时重挂）。

        QWindow 不存在（壳层构建期/未 realize 的测试直调 showEvent）时只挂
        所在屏的 DPI/几何信号——窗口句柄拿到后再补挂 screenChanged。
        """
        win = self.windowHandle()
        old = self._dpr_watch_window
        if win is not None and win is not old:
            if old is not None:
                self._disconnect_screen_signal(old, "screenChanged",
                                               self._on_window_screen_changed)
            self._connect_screen_signal(win, "screenChanged",
                                        self._on_window_screen_changed)
            self._dpr_watch_window = win
        self._wire_screen_dpi_signals(self._screen)

    def _disarm_dpr_change_watch(self) -> None:
        """关闭窗口时摘除信号接线（与 showEvent 的 arm 对称）。"""
        old = self._dpr_watch_window
        if old is not None:
            self._disconnect_screen_signal(old, "screenChanged",
                                           self._on_window_screen_changed)
            self._dpr_watch_window = None
        old = self._dpr_watch_screen
        if old is not None:
            self._disconnect_screen_signals(old)
            self._dpr_watch_screen = None

    def _on_window_screen_changed(self, screen) -> None:
        """QWindow.screenChanged：跨屏 → 换挂新屏信号并按新 DPR 重喂。

        窗口不动时跨屏（副屏 DPI 配置不同）也走这条：新屏从此成为 DPR 与
        几何的取数源，sprite 立即按新 DPR 重建，不等 tick/重绘。
        """
        if not self._alive():
            return  # 屏信号可能晚于窗口销毁（见 _alive）
        if screen is not None:
            self._screen = screen
            self._wire_screen_dpi_signals(screen)
            self._refresh_tick_interval()
        self._feed_dpr()
        self.update()

    def _on_screen_dpi_changed(self, *_args) -> None:
        """系统显示缩放变化（窗口未移动）→ 按新 DPR 重喂 + 重绘。"""
        if not self._alive():
            return  # 屏信号可能晚于窗口销毁（见 _alive）
        self._feed_dpr()
        self.update()

    def _on_screen_geometry_changed(self, *_args) -> None:
        """所在屏几何/可用区变化：overlay 跟随屏几何 + 重读刷新率 + 重喂 DPR。

        分辨率/模式切换常连带 DPR 与刷新率变化；几何同步对未接壳层的裸
        overlay 必需（壳层路径另有 _sync_geometry，重复设置同值是 no-op）。
        """
        if not self._alive():
            return  # 屏信号可能晚于窗口销毁（见 _alive）
        screen = self._screen
        if screen is not None:
            geo = desktop_geometry(screen)
            if self.geometry() != geo:
                self.setGeometry(geo)
        self._refresh_tick_interval()
        self._feed_dpr()
        self.update()

    def remove_sprite(self, sprite, *, release_clip: bool = True) -> None:
        """移除并按其矩形局部刷新（露出下层内容）。

        release_clip=True（默认，V-8）：同时释放 sprite 的 clip 所有权
        （disconnect+stop+清缓存），移除即停解码；屏迁移等保留 clip 的
        场景传 False。移除后通知 _sprite_removed_listeners（V-9：行为
        控制器状态表等外部簿记据此注销）。
        """
        if sprite not in self.sprites:
            return
        if self.slingshot.sprite is sprite:
            # 被瞄准的 sprite 被移除 = 锚点消失：就地收掉瞄准会话
            # （否则 UI 会拿一个已移除对象的几何继续绘制）
            self.slingshot.cancel()
            self._refresh_slingshot_visual()
        self.sprites.remove(sprite)
        self._position_listeners.pop(sprite, None)
        sprite._dirty_cb = None
        sprite._kinetic_cb = None
        if release_clip:
            close = getattr(sprite, "close", None)
            if callable(close):
                close()
        for cb in list(self._sprite_removed_listeners):
            cb(sprite)
        self.update(QRegion(sprite.rect()))
        # M12c：聚合 sprite 数归零 → 驱动器停表（空窗期不再空转）
        self._driver.note_sprite_count_changed()

    def add_sprite_removed_listener(self, cb) -> None:
        """注册 sprite 移除通知（V-9 外部簿记注销挂点）；重复注册 no-op。"""
        if cb not in self._sprite_removed_listeners:
            self._sprite_removed_listeners.append(cb)

    # ---------------------------------------------------------------- 位置监听（Phase 3b）
    def add_position_listener(self, sprite, cb) -> None:
        """注册 sprite 位置监听：tick 推进后 rect 变化时回调 cb(sprite)。

        只在 rect 真正变化时触发——advance 返回 None（视觉无变化）或仅帧
        内容变化（rect 未动）都不触发，跟随方不会被无位移的动画帧白唤。
        重复注册同一 cb 是 no-op。
        """
        listeners = self._position_listeners.setdefault(sprite, [])
        if cb not in listeners:
            listeners.append(cb)

    def remove_position_listener(self, sprite, cb) -> None:
        """注销位置监听；未注册过是 no-op。"""
        listeners = self._position_listeners.get(sprite)
        if not listeners:
            return
        if cb in listeners:
            listeners.remove(cb)
        if not listeners:
            self._position_listeners.pop(sprite, None)

    def _sync_follow_now(self, sprite) -> None:
        """拖拽起止关键帧：强制该 sprite 的跟随方立即同步一次（旧机 0ms 去抖）。

        沿用位置监听通道：监听方可选实现 ``follow_now()``——跟随器"不看 30Hz
        节流窗口"的强制同步入口（见 ``sprite_bubble.SpriteBubbleFollower``）；
        未实现的监听方（壳层簿记、聊天跟随窗等）原样跳过。锚点由跟随方自算，
        本类既不认识跟随器的存在形式，也不依赖壳层。
        """
        for cb in list(self._position_listeners.get(sprite, [])):
            flush = getattr(cb, "follow_now", None)
            if not callable(flush):
                continue
            try:
                flush()
            except Exception:
                # 外围跟随方失败绝不打断拖拽主链路（气泡同款静默降级）：
                # 其余监听方与后续拖拽收尾照旧。
                pass

    # ---------------------------------------------------------------- 统一 tick
    def before_sprites_advance(self, dt: float) -> None:
        """行为层钩子（**deprecation**）：仅由"未挂控制器"的驱动器调用。

        M-2 之前这是唯一的仿真段挂点（demo / Phase 2 装配把碰撞世界与物理
        挂在这里）；M-2 之后产品路径的仿真段由 ``TickDriver.tick_sim`` 持有
        三控制器完成（T2：驱动器独立，不挂在主 overlay 上），本钩子只在
        "驱动器未挂控制器"的兼容分支被调用——避免"驱动器 + 钩子"双份仿真。
        新装配请用 ``TickDriver.set_controllers(...)``；本方法保留一个发布
        周期供旧装配迁移，随 4.4 退役刀删除。
        """

    def tick_advance(self, dt: float) -> None:
        """推进+重绘段（M-2 拆分的下半段，驱动器每 tick 调用一次）。

        advance → 脏矩形 → 位置监听 fanout → update。V-3/V-4 语义不变：脏区
        取 advance 上报的旧|新 rect；帧到达直驱重绘另行由 sprite 的
        ``_dirty_cb``（见 add_sprite）保证，不依赖本段。

        入口过存活闸（见 ``_alive``）：驱动器持有的 overlay 可能已被销毁
        （closeEvent 没跑过的销毁路径摘不掉成员），此时整段无事可做——推进
        已死窗口的 sprite、fanout 给它们的跟随方、往死窗口 update 都是错的，
        后者还会抛 RuntimeError 落成 unraisable。
        """
        if not self._alive():
            return
        dirty = QRegion()
        moved = []
        for sprite in list(self.sprites):
            changed = sprite.advance(dt)
            if changed is not None:
                old, new = changed
                dirty |= QRegion(old) | QRegion(new)
                # M14：隐藏 sprite 不进位置 fanout（跟随方不再被不可见对象唤）
                if (old != new and sprite in self._position_listeners
                        and getattr(sprite, "visible", True)):
                    moved.append(sprite)
        # 位置监听 fanout 放在整轮 advance 之后：tick 内所有控制器已写完
        # 位置，跟随方读到的是本 tick 的最终 rect
        for sprite in moved:
            for cb in list(self._position_listeners.get(sprite, [])):
                cb(sprite)
        if not dirty.isEmpty():
            self.update(dirty)

    def _on_tick(self, dt: float | None = None) -> None:
        """兼容 shim（**deprecation**）：转发给驱动器的一次 tick。

        M-2 之前 tick 循环（dt 钳制 / M-1 档位 / 仿真段 / advance）在本类；
        拆分后循环归 ``TickDriver.on_tick``，本方法只转发，供旧调用点（demo、
        既有测试）同步驱动。新代码请用 ``overlay.tick_driver``（或直接
        ``driver.on_tick``）。
        """
        self._driver.on_tick(dt)

    # ---------------------------------------------------------------- 合成
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        region = event.region()
        # WA_NoSystemBackground 禁止 Qt 擦除背景；局部刷新必须由这里把旧帧写回
        # 透明，否则 sprite 移动/变形后的旧区域会保留 backing store 像素，
        # X11 合成器上可短暂表现为纯色方块。只清 event.region()，保持脏矩形
        # 渲染的成本边界；Source 模式确保透明色真正替换目标 alpha。
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for rect in region:
            painter.fillRect(rect, Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for sprite in self.sprites:  # 列表序 = z-order：先画底层，尾部最上
            if not getattr(sprite, "visible", True):
                continue  # M14：隐藏 sprite 跳过绘制（脏区域照旧被覆盖擦除）
            if region.intersects(sprite.rect()):
                sprite.paint(painter)
        self._paint_slingshot(painter)  # 瞄准 UI 层（橡皮带 + 轨迹预览，最上）
        painter.end()

    # ---------------------------------------------------------------- 命中与鼠标路由
    def apply_opacity(self, percent: int) -> None:
        """整窗不透明度（M5c）：overlay 是单合成窗，对齐 legacy 单宠语义。

        旧 ``PetWindow.set_pet_opacity``（window.py:4134-4146）只对宠窗口
        ``setWindowOpacity``；单窗 overlay 承载全部 sprite，故整窗一个值
        （与 legacy 单宠窗口的观感一致）。值未变时跳过系统调用
        （``_applied_opacity`` 容差 0.005，同旧实现）。
        """
        value = max(10, min(100, int(percent)))
        opacity = value / 100.0
        if self._applied_opacity is None or abs(self._applied_opacity - opacity) >= 0.005:
            self.setWindowOpacity(opacity)
            self._applied_opacity = opacity

    def set_mouse_through(self, on: bool) -> None:
        """用户手动穿透开关的统一直写点（菜单/集成层）；shell 可挂
        _through_changed 回调收编为"用户穿透 + 自动穿透"的复合语义。"""
        self.mouse_through = bool(on)
        self.update()
        cb = getattr(self, "_through_changed", None)
        if callable(cb):
            cb(self.mouse_through)

    def sprite_at(self, local_pos: QPoint | QPointF):
        """逐像素联合命中：z-order 顶层往下，绘制外接矩形粗筛 + alpha 细判。

        参数为 overlay 局部坐标（Phase 1a 单屏：= 屏幕物理坐标 - overlay
        原点）。Windows 穿透轮询与鼠标路由共用此判据。

        粗筛用 ``paint_bounds()`` 而非 ``rect()``：45° 探头 / 黄金回旋式的
        抛掷旋转会把可见像素画到帧绘制矩形之外，只用 ``rect()`` 粗筛会在细判
        之前把它们排除（"画在哪点不到哪"）。无旋转时 paint_bounds() 恒等于
        rect()（pet_sprite.paint_bounds），该路径无额外计算。
        """
        x, y = int(local_pos.x()), int(local_pos.y())
        for sprite in reversed(self.sprites):
            if not getattr(sprite, "visible", True):
                continue  # M14：隐藏 sprite 不参与逐像素命中（穿透判据同源）
            # 鸭式 sprite（测试假对象）没有 paint_bounds：回退 rect()（无旋转时
            # 两者等值，见 pet_sprite.paint_bounds）
            bounds = (sprite.paint_bounds() if hasattr(sprite, "paint_bounds")
                      else sprite.rect())
            if not bounds.contains(x, y):
                continue
            # 细判仍在帧绘制矩形原点坐标系：alpha_at 的逆旋转以帧绘制矩形
            # （与 paint_bounds 同心）为轴、命中图也按它采样，故换算原点不变。
            rect = sprite.rect()
            if sprite.alpha_at(QPoint(x - rect.x(), y - rect.y())) >= ALPHA_HIT_THRESHOLD:
                return sprite
        return None

    def _is_transparent_at(self, local: QPoint | QPointF) -> bool:
        """逐像素联合穿透判据（WindowsPerPixelInputController 协议方法）：
        光标处没有任何 sprite 的不透明像素 = 该点穿透到下层应用。"""
        self._check_stale_press()  # V-7：穿透轮询是恒开的，顺带兜底卡死的拖拽
        return self.sprite_at(local) is None

    def contextMenuEvent(self, event) -> None:
        """右键菜单（Phase 3a）：命中 sprite 弹最小集菜单；未命中忽略。"""
        if self._context_menu_blocked():
            # 弹弓消费的右键 / 瞄准会话中：不弹菜单（正常 Qt 路径已在
            # event() 拦下，这里兜底直调路径）
            event.ignore()
            return
        self._note_user_input()
        self._note_kinetic()  # M-1：输入事件同步升档
        target = self.sprite_at(event.pos())
        if target is None:
            event.ignore()  # 未命中：不弹菜单（Windows 穿透轮询会把右键让给下层）
            return
        # behavior 由集成层持有（demo/未来 AppShell 挂在 overlay 上），基类不拥有
        menu = build_sprite_menu(self, target, behavior=getattr(self, "behavior", None))
        menu.exec(event.globalPos())
        event.accept()

    def mousePressEvent(self, event) -> None:
        self._note_user_input()
        self._note_kinetic()  # M-1：输入事件同步升档
        if event.button() != Qt.MouseButton.LeftButton:
            if (event.button() == Qt.MouseButton.RightButton
                    and (self._mouse_grab is not None or self.slingshot.aiming)
                    and not self.mouse_through
                    and self._consume_right_press_for_slingshot(event)):
                # V-13 调和（弹弓刀）：拖拽中点右键 = 进蓄力瞄准 / 取消瞄准
                # （slingshot_enabled 热读为真时）。这里**不**合成 release：
                # 瞄准有明确退出路径（松左键发射 / Esc / 再点右键取消 / 左键
                # 丢失看门狗），不会把 sprite 卡在 drag 态，也不会把拖拽丢成
                # 一次「原地放下」。其余非左键（中键/侧键）与
                # slingshot_enabled 关闭时，下方 V-13 合成 release 原样保留。
                # 断言与理由见 tests/test_overlay_hardening.py V-13 段落。
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                # 未被弹弓消费的右键 = 正经的菜单手势：清掉上一次的抑制旗标
                # （旧 window.py:3224-3225 同语义），否则菜单会被误吞一次
                self._context_menu_suppressed = False
            if self._mouse_grab is not None:
                self._finish_grab(event.position())
            # 只有左键进拖拽 grab——右键语义是弹菜单（contextMenuEvent），
            # 若进 grab，松手会被当成一次"原地放下"的拖拽（PetWindow 旧语义）
            event.ignore()
            return
        if self.slingshot.aiming:
            # 防御：瞄准中又收到左键 press（多点设备/事件序列丢失）= 会话已
            # 失效，先收会话再走正常 grab，绝不留下无按压源的瞄准态。
            self._cancel_slingshot(resume_drag=False)
        target = self.sprite_at(event.position())
        if target is None:
            self._mouse_grab = None
            self._press_click_only = False
            event.ignore()  # 未命中：忽略（Windows 穿透轮询会把点击让给下层）
            return
        self._mouse_grab = target
        if self._press_drag_blocked(event):
            # 锁定位 / 未按 SHIFT（shift_drag）：不进拖拽 grab——不记按压锚点、
            # 不 on_press（点击候选不挪窝、不置拖拽态）。这里**不** event.ignore：
            # 忽略按下会让 Qt 不建立隐式 grab、连松手一起丢，点击互动随之静默
            # 失效（legacy 两处闸门都保留点击，见 window.py:3126-3133/:3172-3181）。
            # 松手由壳层的 click-only 判别走点击分支。
            self._press_click_only = True
            event.accept()
            return
        self._press_click_only = False
        self._press_global = event.globalPosition().toPoint()
        self._drag_committed = False
        if self._input_controller is not None:
            self._input_controller.set_drag_active(True)
        target.on_press(event.position())
        event.accept()

    def _press_drag_blocked(self, event) -> bool:
        """本次按下是否禁止拖拽（锁定位 / 未按 SHIFT 的 shift_drag）。

        旧机两处闸门的共同语义（window.py:3126-3133 锁定位置；:3172-3181
        SHIFT 门）：命中按下不进拖拽 grab，松手仍走点击。本架构按下只是点击
        候选、过阈值才升级，闸门提前到按下可省掉一次无谓的 on_press/悬空准备。
        """
        if self.lock_position:
            return True
        if self.shift_drag_required:
            return not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        return False

    def _commit_drag_if_threshold_crossed(self, global_pos) -> None:
        """位移过 ``DRAG_THRESHOLD`` 把点击候选升级为真拖拽（幂等）。

        旧机 window.py:3169-3171/3192-3193 语义：按下只是点击候选，过阈值
        才置拖拽态——sprite.begin_drag（碰撞无限质量/探头取消的判定面）、
        拖拽悬空动画（behavior.on_drag_started）、壳层探头取消
        （``_drag_committed_cb``）全部挂在这一刻，不是按下即生效。
        """
        grab = self._mouse_grab
        if grab is None or self._drag_committed or self._press_global is None:
            return
        threshold = catalog.DRAG_THRESHOLD * getattr(grab, "scale", 1.0)
        if (global_pos - self._press_global).manhattanLength() < threshold:
            return
        self._drag_committed = True
        begin = getattr(grab, "begin_drag", None)
        if callable(begin):
            begin()
        behavior = getattr(self, "behavior", None)
        if behavior is not None:
            behavior.on_drag_started(grab)
        cb = getattr(self, "_drag_committed_cb", None)
        if callable(cb):
            cb(grab)
        # 拖拽第一帧立即同步跟随方（旧 window.py:3330 的 _position_sync_now）：
        # 位置通知只在 tick 扇出里发，起步拍不做这一步气泡要等一次 tick + 可能
        # 一次 30Hz 补发（最坏 ≈50ms 拖尾）。
        self._sync_follow_now(grab)

    def mouseMoveEvent(self, event) -> None:
        if self.slingshot.aiming:
            # 瞄准中：move 只更新拉拽矢量，sprite 定锚不动（不走 on_move）
            self._note_kinetic()  # M-1：瞄准移动保持 T0
            self.slingshot.update_pull(event.position())
            self._refresh_slingshot_visual()
            event.accept()
            return
        if self._mouse_grab is None:
            event.ignore()
            return
        if self._press_click_only:
            # click-only 按下（锁定位 / 未按 SHIFT）：不升级拖拽、不转发移动
            # ——位置绝不跟随光标（锁定语义），松手仍按点击收口
            event.ignore()
            return
        self._note_kinetic()  # M-1：拖拽移动保持 T0
        # 先判升级再喂移动：on_move 在 begin_drag 之前是 no-op（点击候选不挪窝）
        self._commit_drag_if_threshold_crossed(event.globalPosition().toPoint())
        # grab 期间事件直达被按住的 sprite（光标移出/落到别的 sprite 上不换手）
        self._mouse_grab.on_move(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._note_kinetic()  # M-1：松手（甩出判定）同步升档
        if self.slingshot.aiming:
            if event.button() == Qt.MouseButton.LeftButton:
                self._fire_slingshot(event.position())
                event.accept()
                return
            # 右键/其它键松手：瞄准继续（左键仍按住）。右键的取消手势在
            # press 侧（"再点右键取消"），press+release 不能互相抵消。
            event.accept()
            return
        grab = self._mouse_grab
        self._finish_grab(event.position())
        if grab is None:
            event.ignore()
            return
        event.accept()

    def keyPressEvent(self, event) -> None:
        """Esc 取消瞄准（旧 window.py:3601-3605 语义）。

        焦点口径：overlay 通常拿不到键盘焦点（防抢焦点窗口），本处理器在
        overlay 确有焦点时直接生效；生产路径的 Esc 兜底（全局钩子）由壳层
        负责，见交付报告「壳层接线清单」。
        """
        if event.key() == Qt.Key.Key_Escape and self.slingshot.aiming:
            self._cancel_slingshot(resume_drag=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:  # noqa: N802 (Qt 命名)
        """Qt 事件入口：丢弃弹弓消费右键后随之而来的右键菜单。

        子类（ShellOverlayWindow）覆写 ``contextMenuEvent`` 且不走 super()，
        只在 contextMenuEvent 里判旗标挡不住产品路径；本钩子在 Qt 分发
        ContextMenu 之前拦下——同一次右键的 press 处理必然先于 ContextMenu。
        """
        if (event.type() == QEvent.Type.ContextMenu
                and self._context_menu_blocked()):
            event.ignore()
            return True
        return super().event(event)

    def _context_menu_blocked(self) -> bool:
        """是否抑制右键菜单：弹弓消费过右键，或正处于瞄准会话。"""
        if self._context_menu_suppressed:
            self._context_menu_suppressed = False
            return True
        return self.slingshot.aiming

    def _finish_grab(self, position, *, forward: bool = True) -> None:
        """收尾当前拖拽 grab（release/打断/看门狗共用）。

        ``forward=False``：只清 overlay 侧 grab 状态与子类回调，不再把
        位置转发给 sprite——弹弓发射/取消已按自己的协议在控制器里收尾
        （on_release 的语义与弹射结果冲突，转发会把初速覆盖回拖拽估算值）。
        """
        grab, self._mouse_grab = self._mouse_grab, None
        # 只有真拖拽才有关键帧要同步（旧机 _position_sync_now 也只在拖拽分支里）：
        # 单击没挪窝，强制一次落位只是白交一次 SetWindowPos 税
        committed = self._drag_committed
        self._press_global = None
        self._drag_committed = False
        self._press_click_only = False
        if self._input_controller is not None:
            self._input_controller.set_drag_active(False)
        if grab is not None and forward:
            grab.on_release(position)
        if grab is not None and committed:
            # 松手终位立即同步（旧 window.py:3401），不等 tick/30Hz 补发
            self._sync_follow_now(grab)
        cb = getattr(self, "_grab_finished_cb", None)
        if callable(cb):
            cb()  # 4.1b：shell 冲刷光标恢复滞留等拖拽后状态

    def _check_stale_press(self) -> None:
        """V-7 拖拽看门狗：release 事件丢失（alt-tab/弹窗抢 grab/屏拔除）
        时 _press_global 卡死 → should_click_through 永假 → 全屏吞点击。
        穿透轮询与 tick 路径兜底：左键已不在按下态则强制收尾。"""
        if self._press_global is None:
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return
        if self.slingshot.aiming and self._cancel_slingshot(resume_drag=False):
            # 瞄准会话的按压源（左键）已消失且 release 没到：收会话回锚点
            # （不发射），grab 收尾已在 _cancel_slingshot 内完成。
            return
        self._finish_grab(QPointF(self.mapFromGlobal(QCursor.pos())))

    # ---------------------------------------------------------------- 弹弓会话（事件侧调和）
    def _consume_right_press_for_slingshot(self, event) -> bool:
        """右键 press 交给弹弓：进瞄准或取消瞄准；返回是否被消费。

        消费成功即刷新脏区 + 抑制随之而来的右键菜单 + 作废子类的
        点击/拖拽判别状态（``ShellOverlayWindow._press_pos``：右键 press
        会覆写它，若不清理，随后的左键松手会被壳层判成一次单击）。
        """
        if not self.slingshot.maybe_enter_on_right_press(
                self._mouse_grab, event.position()):
            return False
        self._context_menu_suppressed = True
        self._invalidate_press_discriminator()
        self._refresh_slingshot_visual()
        return True

    def _fire_slingshot(self, position) -> bool:
        """松左键：发射（或拉拽不足回锚点），并结束本次 grab。"""
        fired = self.slingshot.fire(position)
        self._finish_grab(position, forward=False)
        self._invalidate_press_discriminator()
        self._refresh_slingshot_visual()
        return fired

    def _cancel_slingshot(self, *, resume_drag: bool) -> bool:
        """取消瞄准：``resume_drag=False`` 回锚点并结束 grab（Esc / 看门狗）；
        ``True`` 就地恢复拖拽、grab 继续（再点右键）。"""
        if not self.slingshot.cancel(resume_drag=resume_drag):
            return False
        self._invalidate_press_discriminator()
        if not resume_drag:
            self._finish_grab(None, forward=False)
        self._refresh_slingshot_visual()
        return True

    def _invalidate_press_discriminator(self) -> None:
        """作废子类（ShellOverlayWindow）的单击/拖拽判别状态。

        壳层在 mousePressEvent 里记录 ``_press_pos``、在 mouseReleaseEvent
        里据它区分单击 vs 拖拽；弹弓消费掉的 press/release 会让这套判别
        错位（右键进瞄准后松左键会被当成一次原地单击 → 误触点击反馈/音效）。
        基类在此清空该字段；壳层改用 ``overlay.slingshot.aiming`` 判定后
        本兜底可移除。裸 OverlayWindow 无此字段，调用即 no-op。
        """
        if "_press_pos" in self.__dict__:
            self.__dict__["_press_pos"] = None

    def _paint_slingshot(self, painter: QPainter) -> bool:
        """瞄准 UI 绘制挂点（橡皮带 + 轨迹预览）。只在瞄准期间有内容。"""
        return self.slingshot.paint(painter)

    def _refresh_slingshot_visual(self) -> None:
        """按脏区通道刷新瞄准 UI：新旧区域合并后局部 update。

        走 overlay 既有 update(QRegion) 通道（与 sprite 脏上报同一路），
        不整屏 repaint；区域取 ``visual_region``（带宽 + 每个轨迹点的小方框，
        不是外接大矩形——预测弧能横跨大半屏，按外包框重绘会白刷几十个百分点
        的像素）。取消/发射后当前区域为空，旧区域这一笔负责把残留的带/点
        擦干净。
        """
        current = self.slingshot.visual_region(self.rect())
        dirty = current | self._slingshot_dirty
        self._slingshot_dirty = current
        if not dirty.isEmpty():
            self.update(dirty)
