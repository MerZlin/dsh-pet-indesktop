"""全屏自动隐藏的宿主侧控制器（UI 层，一个进程只有一个实例）。

探测在 :mod:`petcore.platform.fullscreen`（零 Qt、1 Hz 一次）；这里只做「探测结果
→ 桌宠显隐」的状态机。多宠共用同一个控制器：全屏是进程级事实，每只宠各挂一个
W32 轮询等于把 CPU/句柄成本乘以宠数。

状态机（进入/离开只在**翻转**时动作，重复命中天然幂等）：

- 翻转要**连拍确认**：探测结果与已生效状态不同时，必须连续 ``confirm_polls`` 拍
  （默认 2，1 Hz 下约 2 秒）都是同一结果才真正切换；中途抖一下就作废重来。任务
  管理器、UWP 覆盖层、焦点闪现这类单拍抖动因此不会把桌宠一秒藏、一秒放。代价是
  真进入/离开全屏会晚一拍生效（启动时已在全屏的话，桌宠最多多露 1 秒）。
- 进入全屏：把所有**当前可见**的桌宠 ``set_visible(False)``（气泡随之收起），记进
  ``_auto_hidden``；进入前就隐藏的宠不记录。
- 全屏期间每拍只做一次对账：
  * ``_auto_hidden`` 里的宠又露出来了 → 用户手动显示，记成 override，**不再隐藏**，
    离开全屏也不动它；
  * 进入全屏时还不存在的宠（新生成）→ 照常隐藏；
  * 进入全屏时就存在、当时隐藏、现在露出 → 用户手动显示，记成 override。
- 离开全屏：只恢复 ``_auto_hidden`` 里**仍然不可见**的那些；显示中/已被关掉的跳过。
  用户自己隐藏的宠永远不在 ``_auto_hidden`` 里，所以不会被「顺手打开」。

省电：配置门关闭、或非 Windows（探测不支持）时**不启动定时器**，探测调用次数为 0；
关闭的那一刻若正处在全屏隐藏态，立刻恢复（不滞留隐藏态）。
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer

from ..platform import fullscreen

logger = logging.getLogger("petcore.ui.fullscreen_hide")

FULLSCREEN_POLL_MS = 1000

#: 默认连拍确认数：1 Hz 下要连续两拍同一结果才翻转隐藏态，单拍抖动（任务管理器
#: 置顶最大化、UWP 覆盖层、焦点闪现）不会再让桌宠每秒藏一次又放一次。
DEFAULT_CONFIRM_POLLS = 2


def _unpack(result) -> tuple[bool, str]:
    """探测结果归一：``(hit, detail)`` 或裸布尔都接受。"""
    if isinstance(result, tuple):
        hit = bool(result[0]) if result else False
        detail = str(result[1]) if len(result) > 1 else ""
        return hit, detail
    return bool(result), ""


class FullscreenHideController(QObject):
    """全屏命中 → 隐藏所有桌宠；离开 → 只恢复自己隐藏的那些。"""

    def __init__(
        self,
        pets: Callable[[], Iterable],
        *,
        probe: Callable[[], object] | None = None,
        supported: bool | None = None,
        parent: QObject | None = None,
        interval_ms: int = FULLSCREEN_POLL_MS,
        confirm_polls: int = DEFAULT_CONFIRM_POLLS,
    ) -> None:
        super().__init__(parent)
        self._pets = pets
        self._probe = probe if probe is not None else fullscreen.probe_fullscreen
        self._supported = (
            bool(fullscreen.supported()) if supported is None else bool(supported)
        )
        self._enabled = False
        # 连拍确认数；1 = 单拍即翻（旧语义，测试与诊断用），0/负数按 1 处理。
        self._confirm_polls = max(1, int(confirm_polls))
        # 已生效（应用）的探测结果；候选结果要先攒够 ``_confirm_polls`` 拍才覆盖它。
        self._hit = False
        self._pending_hit: bool | None = None
        self._pending_polls = 0
        # key -> window：我们隐藏的（离开全屏要恢复的）
        self._auto_hidden: dict[object, object] = {}
        # 全屏期间用户手动显示过的宠：不再隐藏，也不在离开时动它
        self._overridden: set[object] = set()
        # 进入全屏那一刻存在的宠（用来区分「新生成的宠」与「用户刚显示的宠」）
        self._known: set[object] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(interval_ms)))
        self._timer.timeout.connect(self.poll)

    # ---------------------------------------------------------------- 状态
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def confirm_polls(self) -> int:
        """翻转隐藏态需要的连续探测拍数（已归一为 ≥1）。"""
        return self._confirm_polls

    def supported(self) -> bool:
        return self._supported

    def timer_active(self) -> bool:
        return self._timer.isActive()

    def hidden_slots(self) -> list:
        """当前因为全屏而被自动隐藏的宠（诊断/测试用）。"""
        return list(self._auto_hidden)

    # ---------------------------------------------------------------- 开关
    def set_enabled(self, on: bool) -> None:
        """配置门 ``auto_hide_fullscreen``：开则起表并立刻探一拍，关则停表并恢复。"""
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            if not self._supported:
                # 非 Windows：如实不支持，绝不起常驻定时器去空转。
                logger.debug("fullscreen hide unsupported on this platform")
                return
            # 起表前先探一拍：确认从第一拍开始攒，真全屏里最多多露一拍（1 秒）。
            self.poll()
            self._timer.start()
            return
        self._timer.stop()
        # 关开关是显式用户动作：未确认的候选一并作废，隐藏态立即恢复。
        self._reset_pending()
        if self._hit:
            self._hit = False
            self._leave_fullscreen()

    def stop(self) -> None:
        """退出路径：只停表，不动窗口（进程马上就要结束了）。"""
        self._timer.stop()
        self._enabled = False
        self._reset_pending()

    # ---------------------------------------------------------------- 轮询
    def poll(self) -> None:
        """探测一拍（定时器与测试共用的唯一入口）。"""
        if not self._enabled or not self._supported:
            return
        try:
            result = self._probe()
        except Exception:  # noqa: BLE001 - 探测失败不翻转状态，也不计入连拍（同主分支）
            logger.debug("fullscreen probe raised", exc_info=True)
            return
        hit, detail = _unpack(result)
        if hit == self._hit:
            # 与已生效状态一致：任何待定候选作废，重复命中照常对账。
            self._reset_pending()
            if hit:
                self._reconcile()
            return
        self._note_candidate(hit, detail)

    def _note_candidate(self, hit: bool, detail: str) -> None:
        """候选翻转：连续 ``confirm_polls`` 拍同一结果才真正切换隐藏态。"""
        if hit == self._pending_hit:
            self._pending_polls += 1
        else:
            self._pending_hit = hit
            self._pending_polls = 1
        if self._pending_polls < self._confirm_polls:
            logger.debug(
                "fullscreen hide candidate hit=%s (%s/%s) %s",
                hit,
                self._pending_polls,
                self._confirm_polls,
                detail,
            )
            return
        self._reset_pending()
        self._hit = hit
        logger.info("fullscreen hide hit=%s (%s)", hit, detail)
        if hit:
            self._enter_fullscreen()
        else:
            self._leave_fullscreen()

    def _reset_pending(self) -> None:
        self._pending_hit = None
        self._pending_polls = 0

    # ---------------------------------------------------------------- 状态机
    def _enter_fullscreen(self) -> None:
        windows = self._windows()
        self._auto_hidden.clear()
        self._overridden.clear()
        self._known = {self._key(window) for window in windows}
        for window in windows:
            if not self._is_visible(window):
                continue
            self._auto_hidden[self._key(window)] = window
            self._set_visible(window, False)

    def _reconcile(self) -> None:
        """全屏期间的对账：让位给用户手动操作，补隐藏全屏期间新出现的宠。"""
        windows = self._windows()
        current = {self._key(window): window for window in windows}
        for key in list(self._auto_hidden):
            window = current.get(key)
            if window is None:
                # 宠在全屏期间被关掉了：不再需要恢复。
                del self._auto_hidden[key]
                continue
            if self._is_visible(window):
                # 我们自己藏的宠又露出来了 = 用户手动显示；不再抢回去。
                del self._auto_hidden[key]
                self._overridden.add(key)
        for key, window in current.items():
            if key in self._auto_hidden or key in self._overridden:
                continue
            if not self._is_visible(window):
                continue
            if key in self._known:
                # 全屏前就存在、当时是隐藏的，现在露着 = 用户刚显示它。
                self._overridden.add(key)
                continue
            self._auto_hidden[key] = window
            self._set_visible(window, False)

    def _leave_fullscreen(self) -> None:
        windows = self._windows()
        current = {self._key(window): window for window in windows}
        restored = 0
        for key in list(self._auto_hidden):
            window = current.get(key)
            if window is None or self._is_visible(window):
                continue
            self._set_visible(window, True)
            restored += 1
        if self._auto_hidden or restored:
            logger.info(
                "fullscreen hide restore restored=%s skipped=%s",
                restored,
                len(self._auto_hidden) - restored,
            )
        self._auto_hidden.clear()
        self._overridden.clear()
        self._known.clear()

    # ---------------------------------------------------------------- 工具
    def _windows(self) -> list:
        try:
            windows = list(self._pets() or ())
        except Exception:  # noqa: BLE001 - 宿主取宠失败不该中断探测链
            logger.debug("listing pets failed", exc_info=True)
            return []
        return [window for window in windows if window is not None]

    @staticmethod
    def _key(window) -> object:
        slot = getattr(window, "slot", None)
        return ("slot", int(slot)) if isinstance(slot, int) else ("obj", id(window))

    @staticmethod
    def _is_visible(window) -> bool:
        try:
            return bool(window.isVisible())
        except RuntimeError:  # 原生窗口已销毁
            return False

    @staticmethod
    def _set_visible(window, visible: bool) -> None:
        try:
            window.set_visible(bool(visible))
        except RuntimeError:  # 原生窗口已销毁
            logger.debug("pet window is already gone", exc_info=True)
