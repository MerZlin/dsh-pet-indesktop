# -*- coding: utf-8 -*-
"""「快速识屏」宿主采集闭环：一次触发走完整条链并保证收口。

一次采集的固定顺序（每一步都能在 ``tests/test_screen_capture_controller.py``
里找到对应的顺序断言）::

    hide 自家窗口 → 等一帧合成 → 抓冻结帧 → 建覆盖层并显示 → 用户框选
        ├─ 动作条/Enter：裁选区 → sRGB 8-bit → 关覆盖层 → 还原窗口 → captured(图, 选区, 动作)
        ├─ Esc/取消 ：关覆盖层 → 还原窗口 → cancelled(理由)
        └─ 任一步失败：关覆盖层 → 还原窗口 → failed(理由)

四个刻意的顺序取舍：

1. **还原窗口早于通知下游**。``captured``/``cancelled`` 的接收方（后续片的
   OCR、气泡）一旦开跑就会显示东西，此时桌宠必须已经回到屏幕上。
2. **关覆盖层早于还原窗口**。覆盖层是置顶独占层，先还原的话窗口会被它压在
   底下，用户看到的是"东西回来了但被黑罩着"。
3. **失败与取消共用一条收口路径**（``_teardown``）。本片最贵的 bug 形态是
   "窗口藏了没还"——把收口收成一个函数，比在每个出口重复写一遍可靠。
4. **hide 与 grab 之间必须留一帧**（:func:`settle_before_grab`）。Windows 上
   ``ShowWindow(SW_HIDE)`` 只改窗口的显示状态，桌面那一帧由 DWM 异步提交——藏完
   立刻抓，理论上抓到的可能还是自家窗口还在时的那一帧（"冻结帧里带着自家窗口"）。
   这一步是按审查意见加的保险，**本机没有复现出这个故障**（对照实测与取舍见
   :func:`settle_before_grab` 的说明，别把它当"修好了一个故障"读）。

本片不接翻译，也不复用主动识屏的 watcher：闭环的输入只有一次显式触发。

**已知局限（本片有意留下，后续片再收）**：采集只覆盖**主屏**
（:meth:`ScreenCaptureController._resolve_screen` 取 ``primaryScreen()``）。多屏下
副屏上的内容不在冻结帧里，覆盖层也只铺在主屏——"多屏截全屏"需要跨屏的虚拟桌面
几何与每屏一张底图，本片不做，遇到就是如实只抓主屏。
"""

from __future__ import annotations

import logging
import sys
import time
import weakref

from PySide6.QtCore import QObject, Signal

from .host import WindowHider, visible_top_level_windows
from .overlay import ACTION_ASK, ACTION_COPY, ACTION_OCR, ACTION_TRANSLATE, SelectionOverlay
from ..platform_mac import _mac_request_screen_capture_access, _mac_screen_capture_access
from .pixels import crop_selection, grab_screen_image, to_srgb8
from .session import CapturePhase, CaptureSession, SelectionRect

log = logging.getLogger("dsh-pet-standalone")

__all__ = ["ScreenCaptureController", "settle_before_grab"]

#: 覆盖层可能上报的动作闭集（置灰按钮不会到，但信号面不信 UI 层的保证）。
_VALID_ACTIONS = frozenset({ACTION_OCR, ACTION_TRANSLATE, ACTION_ASK, ACTION_COPY})

#: hide 与 grab 之间等一帧的上限（ms）。DwmFlush 正常只花一个合成周期（60 Hz ≈ 17 ms），
#: 兜底那条路按屏幕刷新率算；上限存在的意义是别让一次抓屏卡在"等一个等不到的帧"上。
_SETTLE_MAX_MS = 50.0
#: 读不到/读出不合理的刷新率时按这个算兜底帧长。
_DEFAULT_REFRESH_HZ = 60.0
#: macOS 原生 NSMenu 的收起动画时长。菜单项触发后 ``menu.exec()`` 先返回，但
#: AppKit 仍会把菜单窗口留在屏上做一小段淡出；若立刻抓冻结帧，菜单会被截进
#: 「截图提问」的底图。等这一拍再 hide/grab，底图才是用户真正看到的界面。
_MACOS_MENU_SETTLE_MS = 260


def _process_pending_events() -> None:
    """把 ``hide()`` 排下的待办事件跑掉。

    Qt 的显隐有一部分是排队执行的（``deleteLater``、几何/重绘的后续处理），
    不等它们跑完就抓帧，等于在"藏了一半"的状态下拍照。
    """
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is not None:
        QCoreApplication.processEvents()


def _flush_dwm() -> bool:
    """块到 DWM 提交完此前排下的合成命令；不是 Windows / 合成关着 / 调用失败返回 False。

    **依据**：``DwmFlush`` 的文档语义就是"等到合成器处理完所有待处理的合成命令"。
    这与我们要的是同一件事——``ShowWindow(SW_HIDE)`` 只改了窗口的显示状态，屏幕内容的
    更新由 DWM 在下一个合成周期提交，所以"藏完立刻 BitBlt"可能抓到的还是自家窗口
    还在时的那一帧。为什么先问 ``DwmIsCompositionEnabled``：合成被停掉（老系统的基础
    显示模式、无桌面会话）时等一个不会来的合成周期可能等不到，这时该走按刷新率的兜底
    （本机 Win10/11 常态化合成，实测 DwmFlush 立即返回）。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwmapi = ctypes.WinDLL("dwmapi")
        enabled = wintypes.BOOL()
        if dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled)) != 0 or not enabled.value:
            return False
        dwmapi.DwmFlush()
        return True
    except Exception:  # DLL 不在 / 接口变了 / 无桌面会话
        log.debug("快速识屏：DwmFlush 不可用，改用刷新率兜底", exc_info=True)
        return False


def _refresh_hz(screen) -> float:
    rate = getattr(screen, "refreshRate", None)
    try:
        value = float(rate() if callable(rate) else rate)
    except (TypeError, ValueError):
        return _DEFAULT_REFRESH_HZ
    return value if value > 0 else _DEFAULT_REFRESH_HZ


def _sleep_one_frame(screen) -> bool:
    """按屏幕刷新率睡一个刷新周期（兜底路径）；返回是否真的睡了。

    代价说清楚：这是在 GUI 线程上睡（60 Hz 下 ≈ 17 ms，上限 :data:`_SETTLE_MAX_MS`）。
    触发截图那一刻用户本来就在等"屏幕黑一下"，17 ms 看不出来；抓到自己家窗口却是
    用户一眼就能看到的错。
    """
    interval_ms = min(_SETTLE_MAX_MS, 1000.0 / _refresh_hz(screen))
    if interval_ms <= 0:
        return False
    time.sleep(interval_ms / 1000.0)
    return True


def settle_before_grab(screen) -> float:
    """hide 之后、grab 之前等一个合成帧；返回实际等待毫秒（0 = 没等）。

    两步：先把 Qt 的待办事件跑掉（``hide()`` 的后半段是排队的），再等一帧——Windows
    上优先 ``DwmFlush``（等真正的合成周期），其余情况按刷新率睡一帧。任何一步失败都
    只记日志并继续：**抓帧不能因为"等不到那一帧"而不发生**（那会把一次截图变成
    "点了没反应"）。

    **实测口径（2026-10-13，别把它当"修好了一个故障"）**：本机（Windows + Qt 6.11.2，
    2560×1440 @170 Hz，真显示器）用探针
    ``.scratch/screen-capture/probe_hide_settle_grab.py`` 跑了修复前/后对照——
    一块 420×320 纯品红标记窗当"自家窗口"，动手前抓屏数到 134400 个标记像素
    （420×320，逐像素精确命中，证明探针认得出、标记真在屏上），而
    **不等帧那一轮抓到的冻结帧里标记像素也是 0**：这条路径在本机不复现"抓到自家
    窗口"，``QScreen.grabWindow(0)`` 与 ``ShowWindow(SW_HIDE)`` 的先后在本机就是干净的。
    所以这一步是**按审查意见加的廉价保险**（本机实测 5.5 ms/次，DwmFlush 可用），
    不是被本地复现的缺陷修复；跨机（远程桌面/混合 GPU/合成器降频）是否真会出现，
    本机没有证据。取舍摆在明处：留着是每次 ~5 ms 换一层不确定的保险，删掉就是回到
    "hide 完立刻抓"——本机没有任何证据说那条会抓到自家窗口。

    真机对照的原始输出在 ``.scratch/screen-capture/probe_hide_settle_grab.out``。
    """
    started = time.perf_counter()
    try:
        _process_pending_events()
    except Exception:
        log.debug("快速识屏：处理待办事件失败", exc_info=True)
    flushed = False
    try:
        flushed = _flush_dwm()
    except Exception:
        log.debug("快速识屏：等合成帧失败", exc_info=True)
    if not flushed:
        try:
            _sleep_one_frame(screen)
        except Exception:
            log.debug("快速识屏：按刷新率等帧失败", exc_info=True)
    return (time.perf_counter() - started) * 1000.0


def _weak_slot(target, name: str):
    """构造只持弱引用的槽函数。

    Qt 信号连接持有 Python 可调用对象的强引用；直接连宿主的方法会形成
    ``宿主 ↔ 发出者`` 引用环，进而把 Qt 对象的销毁推迟到解释器退出期——
    实测那条路径会以原生崩溃收场（与仓库既有的 Qt 生命周期收口同源）。
    本模块里**所有**跨对象连接（controller → overlay 的两条选择信号与
    ``destroyed``）一律走这里，不混用直连：混用的那天，"哪条连接会拖住对象"
    就成了要靠记性回答的问题。
    """
    reference = weakref.ref(target)

    def _call(*args):
        obj = reference()
        if obj is not None:
            getattr(obj, name)(*args)

    return _call


class ScreenCaptureController(QObject):
    """一次「快速识屏」的宿主闭环。

    依赖全部可注入（``hider`` / ``grabber`` / ``overlay_factory`` / ``screen`` /
    ``settler``），默认走真实实现；测试用假宿主即可在 offscreen 下把顺序与异常
    出口钉死。覆盖层只需要提供 ``action_requested`` / ``selection_cancelled``
    两个信号与 ``show()`` / ``close()``，不要求是 ``SelectionOverlay``。
    """

    captured = Signal(object, object, object)   # (QImage 选区像素, SelectionRect 逻辑选区, 动作 id)
    cancelled = Signal(str)             # 用户取消（"esc" / "toolbar"）
    failed = Signal(str)                # 采集失败理由（含阶段前缀，便于定位）

    def __init__(self, parent=None, *, session=None, hider=None, grabber=None,
                 overlay_factory=None, screen=None, settler=None,
                 permission_check=None, permission_request=None) -> None:
        super().__init__(parent)
        self._session = session if session is not None else CaptureSession()
        self._hider = hider
        self._grabber = grabber if grabber is not None else grab_screen_image
        self._overlay_factory = overlay_factory if overlay_factory is not None else SelectionOverlay
        self._screen = screen
        #: hide→grab 之间的等帧器（``(screen) -> ms``）；``None`` = 不等。
        #: 真机探针靠它跑"修前/修后"对照（同一份源码，只换这一个注入件）。
        self._settler = settler if settler is not None else settle_before_grab
        #: macOS 屏幕录制权限：preflight / 首次用户触发时的系统授权请求（均可注入）。
        self._permission_check = permission_check or _mac_screen_capture_access
        self._permission_request = permission_request or _mac_request_screen_capture_access
        self._overlay = None
        self._active_hider = None
        self._frame = None

    # ------------------------------------------------------------ 只读视图

    @property
    def phase(self) -> CapturePhase:
        return self._session.phase

    # ---------------------------------------------------------------- 入口

    def start(self) -> bool:
        """开始一次采集；已在采集中返回 False（不重复隐藏、不叠第二层覆盖层）。"""
        if self._session.phase is not CapturePhase.IDLE:
            log.debug("快速识屏：上一轮还没收口，忽略本次触发 (%s)", self._session.phase.value)
            return False
        if not self._screen_recording_allowed():
            self.failed.emit("screen-permission")
            return False
        self._settle_macos_menu_close()

        self._session.start()
        self._screen = self._resolve_screen()

        try:
            hider = self._hider if self._hider is not None else WindowHider(visible_top_level_windows())
            # 先记账再动手：hide_all 中途抛错时可能已经藏了一半，收口必须还能还回去。
            self._active_hider = hider
            hider.hide_all()
        except Exception as exc:  # 宿主侧千奇百怪，闭环不许因此塌掉
            return self._abort(f"hide-failed:{exc}")
        self._settle()
        self._session.hidden()

        try:
            frame = self._grabber(self._screen)
        except Exception as exc:
            return self._abort(f"grab-failed:{exc}")
        if frame is None or frame.isNull():
            return self._abort("grab-empty")
        self._frame = frame
        self._session.begin_selection()

        try:
            overlay = self._overlay_factory(frame, self._screen)
            # 两处连接都走弱引用槽（同 ``destroyed`` 那条）：直连宿主方法会形成
            # controller↔overlay 引用环，把 Qt 对象的销毁推到解释器退出期——那条路径
            # 实测以原生崩溃收场。全模块统一一种连接形态，不靠记性区分哪条安全。
            overlay.action_requested.connect(_weak_slot(self, "_on_action"))
            overlay.selection_cancelled.connect(_weak_slot(self, "_on_cancelled"))
            # 覆盖层被 Qt 直接销毁（异常退出/被系统回收）时没有 Esc/Enter 信号可等，
            # 不接这一手就永远停在 selecting——窗口留在隐藏态，桌宠凭空消失。
            overlay.destroyed.connect(_weak_slot(self, "_on_overlay_destroyed"))
            self._overlay = overlay
            overlay.show()
        except Exception as exc:
            return self._abort(f"overlay-failed:{exc}")
        return True

    # ------------------------------------------------------------ 等帧

    def _settle(self) -> None:
        """hide 与 grab 之间的等帧（见 :func:`settle_before_grab`）。

        等帧失败不是失败：它只是"少等一帧"，比让一次截图变成"点了没反应"轻得多，
        所以这里吞掉异常继续抓。
        """
        settler = self._settler
        if settler is None:
            return
        try:
            settler(self._screen)
        except Exception:
            log.debug("快速识屏：等帧失败，按原样继续抓帧", exc_info=True)

    # ---------------------------------------------------------------- 回调

    def _on_action(self, action, selection) -> None:
        if self._session.phase is not CapturePhase.SELECTING:
            log.debug("快速识屏：忽略迟到或重复的动作信号")
            return
        if str(action) not in _VALID_ACTIONS:
            self._abort(f"unknown-action:{action}")
            return
        if not isinstance(selection, SelectionRect) or selection.is_empty:
            self._abort("empty-selection")
            return
        if self._frame is None:
            self._abort("no-frame")
            return
        try:
            image = to_srgb8(crop_selection(self._frame, selection, self._screen_dpr()))
        except (ValueError, TypeError) as exc:
            self._abort(f"crop-failed:{exc}")
            return

        self._session.confirm(selection)
        self._teardown()
        self.captured.emit(image, selection, str(action))

    def _on_cancelled(self, reason) -> None:
        if self._session.phase is not CapturePhase.SELECTING:
            log.debug("快速识屏：忽略迟到或重复的取消信号")
            return
        self._session.cancel(str(reason))
        self._teardown()
        self.cancelled.emit(str(reason))

    def _on_overlay_destroyed(self, *_) -> None:
        if self._session.phase is not CapturePhase.SELECTING:
            return
        self._abort("overlay-destroyed")

    # ---------------------------------------------------------------- 收口

    def _abort(self, reason: str) -> bool:
        log.warning("快速识屏：采集未完成 (%s)", reason)
        if self._session.phase in (CapturePhase.HIDING, CapturePhase.FROZEN, CapturePhase.SELECTING):
            self._session.cancel(reason)
        self._teardown()
        self.failed.emit(reason)
        return False

    def _teardown(self) -> None:
        """唯一的收口路径：关覆盖层 → 还窗口 → 会话回 idle。"""
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            try:
                overlay.close()
            except RuntimeError as exc:  # 覆盖层已被 Qt 销毁
                log.debug("快速识屏：覆盖层已不可用 (%s)", exc)

        hider, self._active_hider = self._active_hider, None
        if hider is not None:
            try:
                hider.restore_all()
            except Exception as exc:  # 还失败也得继续收口：会话卡在中间态会让此后每次触发都被拒
                log.error("快速识屏：还原自家窗口失败 (%s)", exc)

        self._frame = None
        if self._session.phase in (CapturePhase.CAPTURED, CapturePhase.CANCELLED):
            self._session.restore()
            self._session.settle()

    # ---------------------------------------------------------------- 环境

    def _screen_recording_allowed(self) -> bool:
        """macOS 屏幕录制 preflight；未授权时弹一次系统框，仍不行就如实失败。

        preflight 为 False 时绝不能继续 hide 自家窗口/显示全屏覆盖层：权限没生效时
        抓到的冻结帧是黑图，覆盖层一 ``activateWindow`` 就会把用户带到一块空白桌面。
        系统框每个进程只弹一次；用户拒绝后直接走失败说明，不反复骚扰。
        """
        try:
            if self._permission_check():
                return True
            granted = bool(self._permission_request())
            if granted and self._permission_check():
                return True
        except Exception:
            log.debug("快速识屏：屏幕录制权限检查异常", exc_info=True)
        log.warning("快速识屏：屏幕录制权限未生效（授权后需重启进程）")
        return False

    def _settle_macos_menu_close(self) -> None:
        """等 macOS 原生菜单的收起动画结束，再 hide/grab。

        右键菜单的 action 是延到 ``menu.exec()`` 返回后才派发的，但 AppKit 的
        ``NSMenu`` 窗口还有一小段淡出；0ms 就抓会把菜单（以及菜单后面的变暗）
        一起冻进底图。用嵌套事件循环让这一拍过去，期间 GUI 仍可处理事件；
        offscreen/非 cocoa 不等待（测试不需要为真实菜单动画买单）。
        """
        if sys.platform != "darwin":
            return
        try:
            from PySide6.QtCore import QEventLoop, QTimer
            from PySide6.QtGui import QGuiApplication

            if QGuiApplication.platformName() != "cocoa":
                return
            loop = QEventLoop()
            QTimer.singleShot(_MACOS_MENU_SETTLE_MS, loop.quit)
            loop.exec()
        except Exception:
            log.debug("快速识屏：等待 macOS 菜单收起失败", exc_info=True)

    def _resolve_screen(self):
        if self._screen is not None:
            return self._screen
        from PySide6.QtGui import QCursor, QGuiApplication

        # 框选要落在用户正在看的那块屏：多屏下主屏固定取 primaryScreen，
        # 会在副屏工作时把覆盖层/冻结帧甩到主屏，看起来像"跳到了空白桌面"。
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _screen_dpr(self) -> float:
        dpr = getattr(self._screen, "devicePixelRatio", None)
        return float(dpr()) if callable(dpr) else 1.0
