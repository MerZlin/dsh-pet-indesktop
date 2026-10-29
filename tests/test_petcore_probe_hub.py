"""灵动岛 GPU/FPS 探针接线的测试。

平台采集器一律用假对象：这里验的是启停闸门与样本翻译，不是 PDH/PresentMon。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from petcore.island import segments as isl
from petcore.platform.gpu import AdapterReading, EngineKey, EngineReading, GpuSample
from petcore.ui.island import DynamicIsland
from petcore.ui.probes import IslandProbeHub, fps_segment, gpu_segment


class _Config:
    def __init__(self, metrics):
        self.data = {
            "island_enabled": True,
            "island_metrics": list(metrics),
            "island_show_name": True,
            "island_show_status": True,
            "island_info_mode": "time",
            "island_custom_text": "",
            "island_style": "dark",
            "island_opacity": 1.0,
            "island_accent": "blue",
            "island_x": None,
            "island_y": None,
        }

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass


def _app():
    return QApplication.instance() or QApplication([])


class _FakeSample:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeMonitor:
    def __init__(self, sample):
        self.sample = sample
        self.starts = 0
        self.stops = 0
        self._running = False

    def start(self):
        self.starts += 1
        self._running = True

    def stop(self):
        self.stops += 1
        self._running = False

    def running(self):
        return self._running

    def snapshot(self):
        return self.sample


def test_gpu_segment_keeps_readings_and_reasons_apart() -> None:
    ok = GpuSample(
        percent=24.0,
        sampled_at=1.0,
        state="ok",
        adapter=AdapterReading(
            luid="0", percent=24.0, name="测试显卡", busiest=EngineReading(EngineKey("0", 0, "3D"), 24.0, engtype="3D")
        ),
    )
    segment = gpu_segment(ok)
    assert (segment.id, segment.text, segment.max_text) == (isl.GPU, "24", "100")
    assert segment.state == isl.STATE_OK
    assert segment.detail["unit"] == "%"
    assert "测试显卡" in segment.detail["source"]

    missing = GpuSample(percent=None, sampled_at=1.0, state="error", error_reason="PDH 无数据")
    degraded = gpu_segment(missing)
    assert degraded.text == "--"
    assert degraded.state == isl.STATE_ERROR
    assert degraded.detail["error_reason"] == "PDH 无数据"


def test_fps_segment_formats_and_caps() -> None:
    assert fps_segment(_FakeSample(state="ok", fps=144.6)).text == "145"
    assert fps_segment(_FakeSample(state="ok", fps=12000)).text == "9999+"
    no_target = fps_segment(_FakeSample(state="unknown", fps=None, error_reason="没有可采集的目标"))
    assert no_target.text == "--"
    assert no_target.state == isl.STATE_UNKNOWN
    assert no_target.detail["error_reason"] == "没有可采集的目标"


def test_hub_starts_only_what_is_needed_and_keeps_a_placeholder_when_idle() -> None:
    """停采不缩行：`sync(set())` 推定宽占位，而不是把两段快照一起清掉。

    历史行为（清快照 → 行变窄 → 折叠计划翻转 → 又起采样）是 1Hz 振荡的来源；
    行宽必须与「有读数」时逐像素相同，见 `test_stopping_a_probe_keeps_the_row_width`。
    """
    _app()
    island = DynamicIsland(_Config(["cpu", "gpu", "fps"]))
    gpu = _FakeMonitor(GpuSample(percent=31.0, sampled_at=1.0, state="ok"))
    fps = _FakeMonitor(_FakeSample(state="ok", fps=60.0, detail={}))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=lambda: fps)
    try:
        hub.sync({"gpu"})
        assert (gpu.starts, fps.starts) == (1, 0)
        assert hub._timer.isActive()

        hub.sync({"gpu", "fps"})
        assert fps.starts == 1
        hub._poll()
        ids = {segment.seg_id for segment in island._row}
        assert isl.GPU in ids and isl.FPS in ids

        hub.sync(set())
        assert (gpu.stops, fps.stops) == (1, 1)
        assert not hub._timer.isActive()
        idle = {segment.seg_id: segment for segment in island._row}
        for metric_id in (isl.GPU, isl.FPS):
            assert idle[metric_id].state == isl.STATE_SAMPLING
            assert idle[metric_id].text == "--"
        # 真正「未接入探针」只在关停时出现（岛自己退出/停用）：快照清空，冻结的
        # 占位（enabled-but-no-snapshot）照旧定宽。
        hub.shutdown()
        assert island._probe_segments == {}  # noqa: SLF001 - 快照必须清空
    finally:
        island.deleteLater()


def test_stopping_a_probe_keeps_the_row_width() -> None:
    """停采前后行宽必须一致：否则折叠计划会随采样状态反复翻转（AUD-25 同族）。"""
    _app()
    island = DynamicIsland(_Config(["cpu", "gpu", "fps"]))
    gpu = _FakeMonitor(GpuSample(percent=31.0, sampled_at=1.0, state="ok"))
    fps = _FakeMonitor(_FakeSample(state="ok", fps=60.0, pid=4242, detail={}))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=lambda: fps)
    try:
        hub.sync({"gpu", "fps"})
        hub._poll()  # noqa: SLF001 - 推一次真实读数
        before = island.plan().width
        hub.sync(set())
        assert island.plan().width == before
    finally:
        hub.shutdown()
        island.deleteLater()


def test_enabled_probe_without_a_snapshot_reserves_its_width() -> None:
    """段已启用但还没有第一份快照时也要占位：否则「推快照 → 变宽 → 折叠」会来回跳。"""
    _app()
    island = DynamicIsland(_Config(["cpu", "gpu", "fps"]))
    try:
        # 宿主已接探针、但这一拍还没推快照（start() 之后到第一次 poll 之间的窗口）。
        island.set_probe_sync_callback(lambda ids: None)
        segments = {segment.seg_id: segment for segment in island._row}
        for metric_id, box_text, unit in (
            (isl.GPU, "100", "%"),
            (isl.FPS, "9999", "fps"),
        ):
            segment = segments[metric_id]
            assert segment.state == isl.STATE_SAMPLING
            assert segment.text == "--"
            assert segment.box_text == box_text
            assert segment.unit == unit
    finally:
        island.deleteLater()


def test_unsupported_probe_does_not_occupy_width() -> None:
    """整机没有这个能力时不占位（只留卡片里的一行原因）。"""
    _app()
    island = DynamicIsland(_Config(["cpu", "fps"]))
    try:
        island.set_probe_sync_callback(lambda ids: None)  # 宿主已接探针：默认会占位
        assert any(segment.seg_id == isl.FPS for segment in island._row)
        island.set_probe_snapshot(
            fps_segment(
                _FakeSample(
                    state="unsupported",
                    fps=None,
                    pid=None,
                    error_reason="只在 Windows 上可用",
                    detail={},
                )
            )
        )
        assert not any(segment.seg_id == isl.FPS for segment in island._row)
        assert "Windows" in (island._probe_unavailable_reason(isl.FPS) or "")
    finally:
        island.deleteLater()


def test_hiding_the_island_stops_the_probes() -> None:
    """岛隐藏 = 宿主立刻收到空集：GPU/FPS 的 stop() 必须被调用，定时器必须停。"""
    _app()
    island = DynamicIsland(_Config(["gpu", "fps"]))
    gpu = _FakeMonitor(GpuSample(percent=31.0, sampled_at=1.0, state="ok"))
    fps = _FakeMonitor(_FakeSample(state="ok", fps=60.0, pid=4242, detail={}))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=lambda: fps)
    island.set_probe_sync_callback(hub.sync)
    try:
        island.show()
        _app().processEvents()
        assert (gpu.starts, fps.starts) == (1, 1)
        assert hub._timer.isActive()  # noqa: SLF001
        island.hide()
        _app().processEvents()
        assert (gpu.stops, fps.stops) == (1, 1)
        assert not hub._timer.isActive()  # noqa: SLF001
    finally:
        hub.shutdown()
        island.deleteLater()


def test_reconcile_does_not_retry_a_blocked_probe_every_poll() -> None:
    """unsupported 的结论要缓存（~60s）：否则每拍都 start() 一次 = 白烧 syscall。"""
    _app()

    class _Unsupported(_FakeMonitor):
        def __init__(self):
            super().__init__(
                _FakeSample(
                    state="unsupported", fps=None, detail={"error_reason": "只在 Windows 上可用"}
                )
            )

    island = DynamicIsland(_Config(["cpu", "fps"]))
    fps = _Unsupported()
    hub = IslandProbeHub(island, fps_factory=lambda: fps)
    try:
        hub.sync({"fps"})
        assert fps.starts == 0, "探针自己已经说 unsupported：不必白起一次"
        for _ in range(3):
            hub._poll()  # noqa: SLF001
        assert fps.starts == 0
        hub._blocked_until[isl.FPS] = 0.0  # noqa: SLF001 - 缓存到期后才允许重试
        hub._poll()  # noqa: SLF001
        assert fps.starts == 1
    finally:
        hub.shutdown()
        island.deleteLater()


def test_fps_retarget_does_not_read_the_foreground_while_running(monkeypatch) -> None:
    """已经在采一个活着的目标时，不许每秒解析一次前台窗口。"""
    _app()
    from petcore.ui import probes as probes_module

    reads: list[int] = []

    def fake_foreground():
        reads.append(1)
        return {"pid": 999, "process": "game.exe"}

    monkeypatch.setattr(probes_module, "_foreground_window_info", fake_foreground)

    class _Live(_FakeMonitor):
        def __init__(self):
            super().__init__(_FakeSample(state="ok", fps=60.0, pid=4242, detail={}))
            self.targets: list[object] = []

        def set_target(self, pid, name=""):
            self.targets.append(pid)

    island = DynamicIsland(_Config(["fps"]))
    fps = _Live()
    hub = IslandProbeHub(island, fps_factory=lambda: fps)
    try:
        hub.sync({"fps"})
        for _ in range(3):
            hub._poll()  # noqa: SLF001
        assert reads == []
        assert fps.targets == []
    finally:
        hub.shutdown()
        island.deleteLater()


def test_fps_retarget_backs_off_after_a_target_exit(monkeypatch) -> None:
    """目标退出后 30 秒内只解析一次前台；前台是桌宠自己时保持无目标。"""
    _app()
    from petcore.ui import probes as probes_module

    reads: list[int] = []
    foreground = {"pid": 999, "process": "game.exe"}

    def fake_foreground():
        reads.append(1)
        return dict(foreground)

    monkeypatch.setattr(probes_module, "_foreground_window_info", fake_foreground)

    class _Gone(_FakeMonitor):
        def __init__(self):
            super().__init__(
                _FakeSample(state="unknown", fps=None, pid=4242, detail={"target_gone": True})
            )
            self.targets: list[object] = []

        def set_target(self, pid, name=""):
            self.targets.append(pid)

    island = DynamicIsland(_Config(["fps"]))
    fps = _Gone()
    hub = IslandProbeHub(island, fps_factory=lambda: fps)
    try:
        hub.sync({"fps"})
        hub._poll()  # noqa: SLF001 - 第一次解析：换成前台那个游戏
        assert fps.targets == [999]
        for _ in range(3):
            hub._poll()  # noqa: SLF001 - 30 秒退避内不许再解析
        assert reads == [1]
        assert fps.targets == [999]

        # 前台换成桌宠自己：不留上一个已退出的目标。
        foreground["pid"] = os.getpid()
        hub._next_target_lookup_at = 0.0  # noqa: SLF001 - 模拟退避到期
        hub._poll()  # noqa: SLF001
        assert fps.targets == [999, None]
    finally:
        hub.shutdown()
        island.deleteLater()


def test_probe_shutdown_waits_for_the_workers() -> None:
    """退出路径要有界等待（<=1s），不能留守护线程和解释器抢着收口。"""
    _app()

    class _Slow(_FakeMonitor):
        def __init__(self, sample):
            super().__init__(sample)
            self.waits: list[float] = []

        def running(self):
            return False

        def wait_stopped(self, timeout=2.0):
            self.waits.append(timeout)
            return True

    island = DynamicIsland(_Config(["cpu", "gpu", "fps"]))
    gpu = _Slow(GpuSample(percent=31.0, sampled_at=1.0, state="ok"))
    fps = _Slow(_FakeSample(state="ok", fps=60.0, detail={}))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=lambda: fps)
    try:
        hub.sync({"gpu", "fps"})
        hub.shutdown()
        for monitor in (gpu, fps):
            assert monitor.stops == 1
            assert monitor.waits and max(monitor.waits) <= 1.0
        assert island._probe_segments == {}  # noqa: SLF001 - 关停后不留假读数
    finally:
        island.deleteLater()


def test_island_asks_the_host_for_exactly_the_visible_unfolded_probes() -> None:
    _app()
    config = _Config(["gpu"])
    island = DynamicIsland(config)
    wanted: list[frozenset[str]] = []
    island.set_probe_sync_callback(lambda ids: wanted.append(frozenset(ids)))
    try:
        island.show()
        island._sync_sampling()
        assert wanted[-1] == frozenset({isl.GPU})

        config.set("island_metrics", ["cpu"])
        island.refresh_from_config()
        island._sync_sampling()
        assert wanted[-1] == frozenset()

        island.hide()
        island._sync_sampling()
        assert wanted[-1] == frozenset()
    finally:
        island.hide()
        island.deleteLater()


def test_hub_retries_a_refused_start_on_the_next_poll() -> None:
    """start() 在旧 worker 收口中时会被拒；只靠 wanted 边沿就再也不重试（AUD-31）。"""
    _app()

    class _Refusing(_FakeMonitor):
        def __init__(self, sample):
            super().__init__(sample)
            self.refusals = 1

        def start(self):
            if self.refusals > 0:
                self.refusals -= 1
                return False
            super().start()
            return True

    island = DynamicIsland(_Config(["cpu", "gpu"]))
    gpu = _Refusing(GpuSample(percent=12.0, sampled_at=1.0, state="ok"))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=None)
    try:
        hub.sync({"gpu"})
        assert gpu.starts == 0, "第一次启动被拒"
        hub._poll()  # noqa: SLF001 - 调和节拍就是 poll
        assert gpu.starts == 1, "下一拍必须重试并成功"
    finally:
        hub.shutdown()


def test_expanding_the_card_does_not_thrash_the_probes(app=None) -> None:
    """展开卡片不许把 GPU 采样推进「停→起」振荡（AUD-25）。"""
    _app()
    class _RunningFake(_FakeMonitor):
        def __init__(self, sample):
            super().__init__(sample)
            self._running = False

        def start(self):
            super().start()
            self._running = True

        def stop(self):
            super().stop()
            self._running = False

        def running(self):
            return self._running

    island = DynamicIsland(_Config(["cpu", "gpu"]))
    gpu = _RunningFake(GpuSample(percent=21.0, sampled_at=1.0, state="ok"))
    hub = IslandProbeHub(island, gpu_factory=lambda: gpu, fps_factory=None)
    try:
        island.set_probe_sync_callback(hub.sync)
        island.show()
        _app().processEvents()
        before = gpu.starts
        island.expand_card()
        _app().processEvents()
        for _ in range(3):
            hub._poll()  # noqa: SLF001 - 模拟每秒一次的推送
            _app().processEvents()
        assert gpu.stops == 0, f"卡片态不该停采（stops={gpu.stops}）"
        assert gpu.starts == before, "卡片态不该重新拉起采样"
    finally:
        hub.shutdown()
