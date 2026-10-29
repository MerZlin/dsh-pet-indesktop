"""灵动岛的 GPU/FPS 探针接线。

平台探针（PDH 读数、PresentMon 子进程）都是零 Qt 的独立采样器，生命周期由
宿主按「岛现在还需要什么」来管：岛隐藏、段被关掉或被折进 ``+N`` 时立刻停采。
本模块只做三件事——启停、把平台样本翻译成段快照、把快照推给岛；不做采集，
也不碰 Qt 之外的平台细节。
"""

from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, QTimer

from ..island import segments as isl
from ..performance import runtime_metrics

logger = logging.getLogger("petcore.ui.probes")

#: 段快照的定宽样本：模板宽只认它，读数与告警级不进宽度键。
#: 与岛上的占位共用同一份常量（``segments.PROBE_BOX_TEXT``），停采/等首读不缩行。
GPU_BOX_TEXT = isl.PROBE_BOX_TEXT[isl.GPU]
FPS_BOX_TEXT = isl.PROBE_BOX_TEXT[isl.FPS]
#: 探针读数推送节拍；采样器自己另有 1 Hz 硬下界，这里只读缓存快照。
PROBE_POLL_MS = 1000
#: FPS 没读数时重新解析前台目标的间隔（秒）。
FPS_TARGET_LOOKUP_SECONDS = 5.0
#: 目标刚退出后的解析退避（秒）：退出那一拍前台多半是桌面/桌宠自己，连着解析没意义。
FPS_TARGET_EXIT_BACKOFF_SECONDS = 30.0
#: ``unsupported`` 判定的缓存（秒）：判过就别每拍再 ``start()`` 一次（白烧 syscall）。
PROBE_BLOCKED_CACHE_SECONDS = 60.0


def _foreground_window_info():
    from ..platform.foreground import foreground_window_info

    try:
        return foreground_window_info()
    except Exception:  # noqa: BLE001 - 前台探测失败等于没有目标
        logger.debug("foreground lookup failed", exc_info=True)
        return None


def gpu_segment(sample) -> isl.SegmentSnapshot:
    """``GpuSample`` → 段快照。非 ``ok`` 一律 ``--`` + 具体原因，绝不写 0。"""
    import math

    percent = getattr(sample, "percent", None)
    ok = str(getattr(sample, "state", "")) == "ok" and isinstance(percent, (int, float))
    if ok and not math.isfinite(float(percent)):
        ok = False
    detail = dict(getattr(sample, "detail", {}) or {})
    detail.setdefault("unit", "%")
    reason = str(getattr(sample, "error_reason", "") or "")
    if not ok and reason:
        detail["error_reason"] = reason
    adapter = getattr(sample, "adapter", None)
    label = str(getattr(adapter, "name", "") or "")
    engine = getattr(getattr(adapter, "busiest", None), "engtype", "")
    if engine:
        label = f"{label} · {engine}" if label else str(engine)
    if label and "source" not in detail:
        detail["source"] = label
    return isl.SegmentSnapshot(
        isl.GPU,
        label="GPU",
        text=f"{float(percent):.0f}" if ok else "--",
        max_text=GPU_BOX_TEXT,
        state=isl.STATE_OK if ok else _state(getattr(sample, "state", "")),
        detail=detail,
    )


def fps_segment(sample) -> isl.SegmentSnapshot:
    """``FpsSample`` → 段快照。无目标/无权限/后端缺失都只写原因。"""
    fps = getattr(sample, "fps", None)
    ok = str(getattr(sample, "state", "")) == "ok" and isinstance(fps, (int, float))
    detail = dict(getattr(sample, "detail", {}) or {})
    detail.setdefault("unit", "fps")
    reason = str(getattr(sample, "error_reason", "") or "")
    if not ok and reason:
        detail["error_reason"] = reason
    target = str(getattr(sample, "target", "") or "")
    if target:
        detail.setdefault("source", target)
    text = "--"
    if ok:
        value = float(fps)
        text = f"{value:.0f}" if value < 10000 else "9999+"
    return isl.SegmentSnapshot(
        isl.FPS,
        label="帧率",
        text=text,
        max_text=FPS_BOX_TEXT,
        state=isl.STATE_OK if ok else _state(getattr(sample, "state", "")),
        detail=detail,
    )


def _state(value) -> str:
    text = str(value or "")
    return text if text in isl.STATES else isl.STATE_UNKNOWN


class IslandProbeHub(QObject):
    """按岛的「还需要哪些段」启停 GPU/FPS 采样器，并逐秒推送快照。

    三条纪律：

    - **停采不缩行**：不再需要的段推一份「采样中」定宽占位（:meth:`DynamicIsland.set_probe_sampling`），
      不把快照清空——行宽一变，折叠计划就翻转，下一拍又把段拉回来（1Hz 振荡）。
    - **判过就别再试**：``unsupported`` 的结论缓存 :data:`PROBE_BLOCKED_CACHE_SECONDS` 秒，
      否则每秒一次 ``start()`` 就是每秒一次「后端在不在」的磁盘探测。
    - **不白读前台**：正在采一个活着的目标时一次都不解析前台窗口；目标退出后退避 30 秒。
    """

    def __init__(self, island, *, gpu_factory=None, fps_factory=None, parent=None) -> None:
        super().__init__(parent)
        self.island = island
        self._gpu_factory = gpu_factory
        self._fps_factory = fps_factory
        self._gpu = None
        self._fps = None
        self._wanted: frozenset[str] = frozenset()
        self._timer = QTimer(self)
        self._timer.setInterval(PROBE_POLL_MS)
        self._timer.timeout.connect(self._poll)
        # 起采后的第一次推送不等满一拍（否则整机不支持时要先画一秒假占位）。
        self._kick_timer = QTimer(self)
        self._kick_timer.setSingleShot(True)
        self._kick_timer.setInterval(0)
        self._kick_timer.timeout.connect(self._poll)
        self._closed = False
        #: 各段上一次推送的状态（``unsupported`` 只推一次）。
        self._last_state: dict[str, str] = {}
        #: 各段 ``unsupported`` 结论的到期时刻（monotonic 秒）。
        self._blocked_until: dict[str, float] = {}
        #: FPS 当前设给探针的目标 pid（同一 pid 不重复 set_target，免得重置重试代次）。
        self._fps_target_pid: int | None = None
        self._next_target_lookup_at = 0.0

    def sync(self, wanted) -> None:
        """``wanted``：岛当前仍需要采样的段 id 集合（空集 = 全停）。

        停掉的段推「采样中」定宽占位而不是清快照：行宽不变，折叠计划才不会跟着
        采样状态来回翻。真正的「未接入探针」只在 :meth:`shutdown` 时清。
        """
        if self._closed:
            return
        wanted = frozenset(str(item) for item in (wanted or ()))
        self._wanted = wanted
        self._sync_one(isl.GPU, wanted, "_gpu", self._gpu_factory)
        self._sync_one(isl.FPS, wanted, "_fps", self._fps_factory)
        # 采样器句柄停采后仍留着复用，所以「要不要轮询」只看需求集合，不看句柄。
        active = bool(wanted & {isl.GPU, isl.FPS})
        if active and not self._timer.isActive():
            self._timer.start()
            self._kick_timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()
        for metric_id in (isl.GPU, isl.FPS):
            if metric_id not in wanted:
                self._push_sampling(metric_id)

    def _sync_one(self, metric_id: str, wanted: frozenset[str], attr: str, factory) -> None:
        monitor = getattr(self, attr)
        if metric_id in wanted:
            if self._blocked_now(metric_id):
                return
            if monitor is None and factory is not None:
                try:
                    monitor = factory()
                except Exception:  # noqa: BLE001 - 探针创建失败只影响这一段
                    logger.debug("probe factory failed: %s", metric_id, exc_info=True)
                    self._note_blocked(metric_id)
                    self._push_unavailable(metric_id, "探针无法创建（该指标暂不可用）")
                    return
                setattr(self, attr, monitor)
                self._last_state.pop(metric_id, None)
                if metric_id == isl.FPS:
                    self._fps_target_pid = None
                    self._next_target_lookup_at = 0.0
                if self._sample_blocked(monitor):
                    # 探针自己（构造时）已经知道这台机器没有这个能力：连 start()
                    # 都不必付，直接把结论推给岛（unsupported 不占位）。
                    self._note_blocked(metric_id)
                    return
            if monitor is not None:
                try:
                    monitor.start()
                except Exception:  # noqa: BLE001
                    logger.debug("probe start failed: %s", metric_id, exc_info=True)
            return
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:  # noqa: BLE001
                logger.debug("probe stop failed: %s", metric_id, exc_info=True)
            self._last_state.pop(metric_id, None)

    # ------------------------------------------------------------ 阻塞判定
    def _blocked_now(self, metric_id: str) -> bool:
        return time.monotonic() < self._blocked_until.get(metric_id, 0.0)

    def _note_blocked(self, metric_id: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else float(now)
        self._blocked_until[metric_id] = moment + PROBE_BLOCKED_CACHE_SECONDS

    @staticmethod
    def _sample_blocked(monitor) -> bool:
        try:
            sample = monitor.snapshot()
        except Exception:  # noqa: BLE001 - 读不到缓存就当没结论
            return False
        return str(getattr(sample, "state", "") or "") == isl.STATE_UNSUPPORTED

    def _push_sampling(self, metric_id: str) -> None:
        """停采的段退回「采样中」定宽占位（保留行宽，丢弃上一拍的旧读数）。"""
        setter = getattr(self.island, "set_probe_sampling", None)
        if not callable(setter):
            return
        try:
            setter(metric_id)
        except Exception:  # noqa: BLE001 - 占位失败不该拖垮岛
            logger.debug("probe placeholder push failed: %s", metric_id, exc_info=True)

    def _push_unavailable(self, metric_id: str, reason: str) -> None:
        """推一份 ``unsupported``：这一段**不占位**，原因只留在展开卡片里。"""
        try:
            self.island.set_probe_snapshot(
                isl.SegmentSnapshot(
                    metric_id,
                    label="",
                    text=isl.UNKNOWN_TEXT,
                    max_text=isl.PROBE_BOX_TEXT[metric_id],
                    state=isl.STATE_UNSUPPORTED,
                    detail={"error_reason": reason},
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("probe unavailable push failed: %s", metric_id, exc_info=True)

    def _poll(self) -> None:
        if self._closed:
            return
        now = time.monotonic()
        # 每拍调和一次：`start()` 在旧 worker 收口中时会被拒（返回 False），
        # 只靠 wanted 边沿触发就再也不重试了（审计 AUD-31）。
        self._reconcile()
        if isl.GPU in self._wanted:
            self._push(isl.GPU, gpu_segment, self._gpu, "gpu_samples", now)
        if isl.FPS in self._wanted:
            if self._fps is not None and hasattr(self._fps, "set_target"):
                self._retarget_fps(now)
            self._push(isl.FPS, fps_segment, self._fps, "fps_samples", now)

    def _reconcile(self) -> None:
        """按需求逐拍调和：只在监控器**没在跑**、且没被判过 unsupported 时才重试 start()。"""
        wanted = self._wanted
        if not wanted:
            return
        for metric_id, attr, factory in (
            (isl.GPU, "_gpu", self._gpu_factory),
            (isl.FPS, "_fps", self._fps_factory),
        ):
            if metric_id not in wanted:
                continue
            if self._blocked_now(metric_id):
                continue
            monitor = getattr(self, attr)
            running = getattr(monitor, "running", None)
            if monitor is not None and callable(running):
                try:
                    if running():
                        continue
                except Exception:  # noqa: BLE001 - 监控器自己的问题，不阻塞调和
                    pass
            self._sync_one(metric_id, wanted, attr, factory)

    def _retarget_fps(self, now: float) -> None:
        """FPS 段只看前台应用：已经有活目标就一次都不解析前台。

        ``GetForegroundWindow`` + pid 查询本身很便宜，但每秒一次就是白烧；目标退出后
        前台多半是桌面或桌宠自己，连着解析只会把刚退出的 pid 又设回去。因此：
        活着 → 不读；没目标/采集中 → 最多每 5 秒读一次；目标刚退出 → 退避 30 秒。
        """
        probe = self._fps
        try:
            sample = probe.snapshot()
        except Exception:  # noqa: BLE001 - 读缓存失败只影响这一段
            return
        detail = getattr(sample, "detail", {}) or {}
        gone = bool(detail.get("target_gone"))
        state = str(getattr(sample, "state", "") or "")
        if state == isl.STATE_OK and not gone:
            pid = getattr(sample, "pid", None)
            if isinstance(pid, int) and pid > 0:
                self._fps_target_pid = pid
            return
        if now < self._next_target_lookup_at:
            return
        self._next_target_lookup_at = now + (
            FPS_TARGET_EXIT_BACKOFF_SECONDS if gone else FPS_TARGET_LOOKUP_SECONDS
        )
        info = _foreground_window_info() or {}
        pid = info.get("pid")
        name = str(info.get("process") or "")
        if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            # 前台就是桌宠自己：不把自己当游戏采，也不留上一个已退出的目标。
            if gone and self._fps_target_pid is not None:
                self._set_fps_target(probe, None, "")
            return
        if pid == self._fps_target_pid:
            return  # 目标没变：不重复 set_target（会重置重试代次与去抖）
        self._set_fps_target(probe, pid, name)

    def _set_fps_target(self, probe, pid: int | None, name: str) -> None:
        try:
            probe.set_target(pid, name)
        except Exception:  # noqa: BLE001 - 换目标失败只影响这一段
            logger.debug("fps retarget failed", exc_info=True)
            return
        self._fps_target_pid = pid

    def _push(
        self, metric_id: str, convert, monitor, counter: str, now: float
    ) -> None:
        if monitor is None:
            return
        try:
            sample = monitor.snapshot()
        except Exception:  # noqa: BLE001 - 读缓存失败不该拖垮岛
            logger.debug("probe snapshot read failed: %s", metric_id, exc_info=True)
            return
        if sample is None:
            return
        try:
            snapshot = convert(sample)
        except Exception:  # noqa: BLE001 - 翻译失败不该拖垮岛
            logger.debug("probe snapshot convert failed: %s", metric_id, exc_info=True)
            return
        state = str(getattr(sample, "state", "") or "")
        if state == isl.STATE_UNSUPPORTED:
            if self._last_state.get(metric_id) == isl.STATE_UNSUPPORTED:
                return  # 「这台机器没有这个能力」只推一次，别每拍重建行
            self._note_blocked(metric_id, now)
        self._last_state[metric_id] = state
        runtime_metrics.inc(counter)
        self.island.set_probe_snapshot(snapshot)

    def shutdown(self, wait_seconds: float = 1.0) -> None:
        """关停并用**最多 ``wait_seconds`` 秒**等 worker 收口（退出路径）。

        退出时不能留下和解释器抢着收口的守护线程：采集链路要在进程死之前把
        终止命令发出去。正常隐藏/停用走 :meth:`sync`（只取消、不 join）。
        """
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._kick_timer.stop()
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        for attr in ("_gpu", "_fps"):
            monitor = getattr(self, attr)
            if monitor is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                shutdown = getattr(monitor, "shutdown", None)
                if callable(shutdown):
                    shutdown(remaining)
                    continue
                monitor.stop()
                wait = getattr(monitor, "wait_stopped", None)
                if callable(wait):
                    wait(remaining)
            except Exception:  # noqa: BLE001
                logger.debug("probe shutdown failed: %s", attr, exc_info=True)
        # 岛退出/停用：回到「未接入探针」，别把占位留成一段假读数。
        try:
            self.island.clear_probe_snapshot()
        except Exception:  # noqa: BLE001
            logger.debug("island probe snapshot clear failed", exc_info=True)


__all__ = ["IslandProbeHub", "fps_segment", "gpu_segment"]
