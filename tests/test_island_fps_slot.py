# -*- coding: utf-8 -*-
"""游戏帧率段的**接线层**（``pet/island_fps.py``）：订阅账目、恢复屏障、四态与卡片素材。

本文件零 Qt：段状态机与进程级采集器宿主都是纯 Python 面，所以这里直接调
``FpsSlot.sync`` / ``poll`` 验活体行为。岛窗口那一侧的接线（定时器、几何、卡片控件、
折叠/细条/隐藏/销毁）是 ``tests/test_island_fps_integration.py`` 的事；采集器自己
（子进程、CSV、统计、代次）是 ``tests/test_fps_monitor.py`` 的事。

四个问题：

1. **谁在采、几场会话**：宿主按岛计数——多宠共用一个采集器与一场采集；关掉一只
   宠不掐另一只的读数，最后一只走了才停线程（"一个应用实例仅一个采集器"）。
2. **恢复不端旧值**：隐藏/折叠期间静默过期的那份读数不许在恢复后冒充实时值；
   没有读数的兜底快照（采集程序不在）**不设屏障**，否则能力原因永远报不出来。
3. **四态与"不虚 0"**：``ok`` 才画数；``sampling``/``unknown``/``error`` 同宽 ``--``；
   盒宽样本恒为后端声明的 4 位（含溢出记号），读数一个像素都不动它。
4. **能力边界**：非 Windows 是"这台机器没有这个能力"（不占位 + 卡片原因），且**连
   采集器都不建**（不碰子进程、不碰 ETW）。

时序纪律：所有用例同步直调（不赌定时器节奏、不起线程）；替身采集器只记调用。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from pet.island import fps_monitor as fm
from pet.island import game_fps as gf
from pet.island import fps as island_fps, segments as isl


# ---------------------------------------------------------------- 替身
class _StubMonitor:
    """与 ``FpsMonitor`` 同形的替身：只记调用、只吐注入的快照（不起线程/子进程）。"""

    def __init__(self):
        self.calls: list[tuple] = []
        self.subscribed: set[str] = set()
        self.subscriptions: list[str] = []
        self.unsubscriptions: list[str] = []
        self.configured: list[tuple[str, str]] = []
        self.starts = 0
        self.stops = 0
        self.closed = False
        #: 每次 stop/close 拿到的 ``terminate_session``（退出编排靠它区分"官方终止
        #: 命令"与"会话结束路径只停不派生"）。
        self.stop_flags: list[bool] = []
        self.close_flags: list[bool] = []
        self.sample = None
        #: 没被注入读数时给"已开始、还没第一拍"的兜底快照（与真采集器同形）。
        self.fallback = _sample(None, state="")

    def configure(self, mode: str = "foreground", process: str = "") -> None:
        self.calls.append(("configure", mode, process))
        self.configured.append((mode, process))

    def subscribe(self, key: str) -> bool:
        self.subscribed.add(str(key))
        self.subscriptions.append(str(key))
        return True

    def unsubscribe(self, key: str) -> None:
        self.subscribed.discard(str(key))
        self.unsubscriptions.append(str(key))

    def start(self) -> bool:
        self.starts += 1
        self.calls.append(("start",))
        return True

    def stop(self, wait: bool = False, timeout: float = 2.0, *,
             terminate_session: bool = True) -> None:
        self.stops += 1
        self.stop_flags.append(bool(terminate_session))
        self.calls.append(("stop",))

    def close(self, *, terminate_session: bool = True) -> None:
        self.closed = True
        self.close_flags.append(bool(terminate_session))

    def snapshot(self):
        return self.sample if self.sample is not None else self.fallback


def _sample(fps, *, state: str = isl.STATE_OK, reason: str = "", pid=4242,
            name: str = "game.exe", displayed=None, age: float = 0.0,
            mode: str = "foreground", process: str = ""):
    """一条与真采集器同形的快照（字段口径逐条对齐 ``fps_monitor._sample``）。"""
    detail = {"unit_full": gf.UNIT_FULL, "source": gf.SOURCE_TEXT}
    if pid:
        detail["detail"] = f"{name} (pid {pid})"
    if mode:
        detail["mode"] = mode
    if process:
        detail["process"] = process
    return fm.FpsSample(
        fps=fps, state=state, sampled_at=time.monotonic() - age,
        target_pid=pid if state == isl.STATE_OK else None,
        target_name=name if state == isl.STATE_OK else "",
        error_reason=reason, displayed_fps=displayed, generation=1, detail=detail)


def _slot(monkeypatch, *, monitor=None, hub=None, key="slot-1", platform=True):
    """一个挂在替身采集器上的段（``platform`` 控制平台闸门：False = 非 Windows）。"""
    monitor = monitor if monitor is not None else _StubMonitor()
    hub = hub if hub is not None else island_fps.FpsHub(monitor_factory=lambda: monitor)
    slot = island_fps.FpsSlot(
        key=key, hub=hub, platform_supported=(lambda: platform))
    return slot, monitor, hub


# ---------------------------------------------------------------- 谁在采
def test_a_slot_that_is_never_enabled_never_builds_a_collector(monkeypatch):
    """默认关闭 = 真的零开销：连采集器对象都不建（更别说线程与 ETW 会话）。"""
    calls: list[int] = []
    hub = island_fps.FpsHub(monitor_factory=lambda: calls.append(1) or _StubMonitor())
    slot = island_fps.FpsSlot(key="off", hub=hub, platform_supported=lambda: True)
    slot.sync(enabled=False, visible=True)
    slot.poll()
    assert calls == [] and hub.monitor is None
    assert slot.monitor is None and slot.sample is None
    assert slot.segment().state == isl.STATE_SAMPLING


def test_enabled_and_visible_subscribes_and_runs_the_thread(monkeypatch):
    slot, monitor, hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True, mode="foreground", process="")
    assert hub.monitor is monitor
    assert hub.keys == {slot.key}
    assert monitor.subscribed == {slot.key}
    assert monitor.starts == 1, "第一个要看读数的段才起线程"
    assert slot.wanted is True


def test_hiding_stops_the_thread_but_keeps_the_subscription(monkeypatch):
    """岛不可见只是休眠：停线程、保留订阅（显示回来不用重开一场会话）。"""
    slot, monitor, hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    slot.sync(enabled=False or True, visible=False)
    assert slot.wanted is False
    assert monitor.stops == 1
    assert monitor.unsubscriptions == [], "隐藏不该退订"
    assert hub.keys == {slot.key}


def test_folding_unsubscribes_and_releasing_empties_the_hub(monkeypatch):
    """段被折 / 窗口销毁：退订收口（看不见的段不该继续烧 ETW）。"""
    slot, monitor, hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    slot.sync(enabled=False, visible=True)
    assert slot.attached is False and hub.keys == set()
    assert monitor.unsubscriptions == [slot.key]

    slot.sync(enabled=True, visible=True)       # 展开恢复
    assert monitor.subscribed == {slot.key}
    slot.release()
    assert hub.keys == set() and slot.monitor is None
    assert monitor.unsubscriptions == [slot.key, slot.key]


def test_two_islands_share_one_collector_and_one_session(monkeypatch):
    """多宠 = 一个采集器、一场采集会话：关掉一只不掐另一只的读数。"""
    built: list[_StubMonitor] = []
    hub = island_fps.FpsHub(monitor_factory=lambda: built.append(_StubMonitor())
                            or built[-1])
    first = island_fps.FpsSlot(key="pet-1", hub=hub, platform_supported=lambda: True)
    second = island_fps.FpsSlot(key="pet-2", hub=hub, platform_supported=lambda: True)
    first.sync(enabled=True, visible=True)
    second.sync(enabled=True, visible=True)
    assert len(built) == 1, "两只宠只允许一个采集器"
    monitor = built[0]
    assert hub.keys == {"pet-1", "pet-2"}
    assert monitor.starts == 1, "第二个岛复用已在跑的那条线程"
    assert monitor.subscribed == {"pet-1", "pet-2"}

    first.release()
    assert hub.keys == {"pet-2"} and monitor.stops == 0, "还有岛在看，不许停线程"
    monitor.sample = _sample(60.0)
    assert second.poll().changed is True
    second.release()
    assert hub.keys == set()
    assert monitor.unsubscriptions == ["pet-1", "pet-2"]


def test_a_second_island_joining_does_not_restart_the_thread(monkeypatch):
    """已经在采时第二个岛加入：不起第二条线程（读者的数量不是采集器的数量）。"""
    hub = island_fps.FpsHub()
    monitor = _StubMonitor()
    hub._factory = lambda: monitor
    first = island_fps.FpsSlot(key="a", hub=hub, platform_supported=lambda: True)
    second = island_fps.FpsSlot(key="b", hub=hub, platform_supported=lambda: True)
    first.sync(enabled=True, visible=True)
    second.sync(enabled=True, visible=True)
    assert monitor.starts == 1
    second.pause()
    assert monitor.stops == 0, "另一只岛还在要读数，不许停"


def test_target_config_is_written_on_every_sync(monkeypatch):
    """跟随方式 / 指定程序名改了要即时生效（同一个采集器上换代重解析目标）。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True, mode="foreground", process="")
    slot.sync(enabled=True, visible=True, mode="process", process="game.exe")
    assert monitor.configured[-1] == ("process", "game.exe")
    assert (slot.mode, slot.process) == ("process", "game.exe")
    monitor.sample = _sample(60.0, mode="process", process="game.exe")
    assert slot.poll().changed is True
    assert "指定程序 game.exe" in " ".join(slot.hint_parts())


# ---------------------------------------------------------------- 恢复屏障
def test_resume_rejects_the_reading_that_survived_the_pause(monkeypatch):
    """恢复后先拒收一次"暂停前那一份带读数的快照"——静默过期的值不许冒充实时值。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    stale = _sample(144.0)
    monitor.sample = stale
    assert slot.poll().changed is True
    assert slot.segment().text == "144"

    slot.pause()
    assert slot.sample is None and slot.segment().state == isl.STATE_SAMPLING
    monitor.sample = stale                      # 替身没清快照（真采集器 stop 会清）
    slot.sync(enabled=True, visible=True)
    assert slot.poll().changed is False, "屏障必须把这份旧读数挡住"
    assert slot.segment().text == isl.UNKNOWN_TEXT

    monitor.sample = _sample(60.0)
    assert slot.poll().changed is True
    assert slot.segment().text == "60"


def test_a_reading_free_snapshot_is_not_a_barrier(monkeypatch):
    """没有读数的兜底快照不做屏障：能力原因（采集程序不在）靠它报出来。"""
    slot, monitor, _hub = _slot(monkeypatch)
    monitor.sample = _sample(None, state=isl.STATE_UNSUPPORTED,
                             reason="缺少内置采集程序 PresentMon-2.6.0-x64.exe")
    slot.sync(enabled=True, visible=True)
    result = slot.poll()
    assert result.changed is True and result.occupying_changed is True
    assert slot.unavailable_reason() == "缺少内置采集程序 PresentMon-2.6.0-x64.exe"


def test_poll_is_quiet_when_nothing_changed(monkeypatch):
    """同一个快照对象 = 没变：不重绘、不重算几何（1Hz 读表的稳态成本为零）。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(60.0)
    assert slot.poll().changed is True
    assert slot.poll() == island_fps.PollResult()
    assert slot.poll().occupying_changed is False


def test_the_occupying_transition_is_reported_once(monkeypatch):
    """unsupported 出现/消失 = 段的增删：宿主据此重算一次几何（读数永远不改宽）。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(60.0)
    slot.poll()
    monitor.sample = _sample(None, state=isl.STATE_UNSUPPORTED, reason="只在 Windows 上可用")
    first = slot.poll()
    assert (first.changed, first.occupying_changed) == (True, True)
    monitor.sample = _sample(30.0)
    second = slot.poll()
    assert (second.changed, second.occupying_changed) == (True, True)


# ---------------------------------------------------------------- 四态与宽度
def test_only_ok_draws_a_number_and_nothing_is_faked_as_zero(monkeypatch):
    """四态兜底：``ok`` 才画数，其余一律同宽 ``--``；没有读数**绝不**画 0。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    for state, reason in ((isl.STATE_SAMPLING, ""),
                          (isl.STATE_UNKNOWN, "没有可采集的目标"),
                          (isl.STATE_ERROR, "权限不足：需要 Performance Log Users")):
        monitor.sample = _sample(None, state=state, reason=reason)
        slot.poll()
        segment = slot.segment()
        assert (segment.state, segment.text) == (state, isl.UNKNOWN_TEXT)
        assert segment.text != "0"
        assert reason in (slot.reason() or f"（{state} 无自带原因）")


def test_a_real_zero_is_still_a_reading(monkeypatch):
    """真读数 0（不可能出现但契约如此）照原样显示：``--`` 只属于"没有读数"。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(0.0)
    slot.poll()
    assert (slot.segment().state, slot.segment().text) == (isl.STATE_OK, "0")


def test_an_ok_state_without_a_reading_degrades_to_sampling(monkeypatch):
    """谎话防线：``ok`` 却没有读数一律降为"采集中"，不许画出伪造的 0。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(None, state=isl.STATE_OK)
    slot.poll()
    assert slot.segment().state == isl.STATE_SAMPLING
    assert slot.segment().text == isl.UNKNOWN_TEXT


def test_the_box_sample_is_the_backend_worst_case(monkeypatch):
    """盒宽样本恒为后端声明的最坏形态（4 位 + 溢出记号），且格式化器装得进去。"""
    assert island_fps.VALUE_SAMPLE == gf.MAX_TEXT
    assert len(gf.format_fps(gf.MAX_FPS)) <= len(island_fps.VALUE_SAMPLE)
    assert len(gf.format_fps(1e9)) <= len(island_fps.VALUE_SAMPLE)
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    boxes = []
    for value in (0.0, 9.0, 99.0, 144.0, 9999.0, 1e9):
        monitor.sample = _sample(value)
        slot.poll()
        boxes.append((slot.segment().box_text, slot.segment().text))
    assert {box for box, _text in boxes} == {island_fps.VALUE_SAMPLE}
    assert boxes[-1][1] == gf.format_fps(1e9), "溢出走后端声明的记号，不夹紧成假精确值"


def test_the_frame_rate_borrows_no_threshold(monkeypatch):
    """帧率高不是故障：段与卡片都不染色（不暗套 GPU/CPU 的阈值线）。"""
    assert isl.alert_thresholds(isl.FPS) is None
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(1440.0)
    slot.poll()
    assert slot.segment().severity == ""


# ---------------------------------------------------------------- 故障保持
def test_a_fault_survives_the_fold(monkeypatch):
    """折叠不埋没故障：收口时的那一拍不是 ok，原因要留在卡片上（胶囊仍是 ``--``）。"""
    slot, monitor, hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(None, state=isl.STATE_ERROR,
                             reason="采集程序没有采集权限（需要 Performance Log Users 组）")
    slot.poll()
    slot.sync(enabled=False, visible=True)      # 折进 +N
    segment = slot.segment()
    assert (segment.state, segment.text) == (isl.STATE_ERROR, isl.UNKNOWN_TEXT)
    assert "Performance Log Users" in slot.reason()
    assert hub.keys == set()


def test_a_healthy_fold_keeps_no_fault(monkeypatch):
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(60.0)
    slot.poll()
    slot.sync(enabled=False, visible=True)
    assert slot.reason() == ""
    assert slot.segment().state == isl.STATE_SAMPLING


# ---------------------------------------------------------------- 能力边界
def test_a_platform_without_the_capability_builds_nothing(monkeypatch):
    """非 Windows：段不占位 + 卡片有原因，而且**连采集器都不建**。"""
    monkeypatch.setattr(gf, "platform_support", lambda: False)
    # 原因也钉死：注入的平台闸门不说明"为什么"，真实那条按宿主架构现算——不钉就会让
    # 这条用例在 ARM64 机器上改成断言 ARM64 的文案（换机器就红，不是产品行为变了）。
    monkeypatch.setattr(gf, "platform_problem", lambda: gf.PLATFORM_UNSUPPORTED_REASON)
    calls: list[int] = []
    hub = island_fps.FpsHub(monitor_factory=lambda: calls.append(1) or _StubMonitor())
    slot = island_fps.FpsSlot(key="linux", hub=hub, platform_supported=lambda: False)
    slot.sync(enabled=True, visible=True)
    assert calls == [] and hub.keys == set()
    assert slot.unavailable_reason() == gf.PLATFORM_UNSUPPORTED_REASON
    assert "Windows" in slot.unavailable_reason(), "能力限制要说清是哪一条"


def test_an_arm64_windows_slot_says_the_architecture_reason_not_the_os_one(monkeypatch):
    """Windows ARM64：不占位，且卡片写"ARM64 未验证"——不许含糊成"非 Windows"。"""
    monkeypatch.setattr(gf, "platform_support", lambda: False)
    monkeypatch.setattr(gf, "platform_problem", lambda: gf.ARM64_UNSUPPORTED_REASON)
    hub = island_fps.FpsHub(monitor_factory=lambda: _StubMonitor())
    slot = island_fps.FpsSlot(key="arm64", hub=hub)
    slot.sync(enabled=True, visible=True)
    assert slot.unavailable_reason() == gf.ARM64_UNSUPPORTED_REASON
    assert hub.monitor is None, "没有这个能力的机器连采集器都不建"


def test_hub_close_actually_waits_for_the_session_teardown(tmp_path):
    """宿主收口必须是**真的**有界 join，不许把收口丢给守护线程就返回。

    为什么单独锁这条：``FpsMonitor.stop()`` 会把线程引用**清空**（换代纪律），所以
    先 ``stop()`` 再 ``close()`` 的那个顺序会让后者的 join 变成空操作——收口函数
    立刻返回、采集子进程还活着，解释器随后退出就留下"采集进程 + 它那场独占 ETW 会话"。
    这正是退出收口要修的那件事，所以用真线程 + 会记录的会话替身把它钉住：
    ``hub.close()`` 返回时，会话的 ``stop`` 必须**已经跑完**。
    """
    import time

    from pet.island import fps_monitor as fm
    from pet.island import game_fps as gf

    class _SlowStopSession:
        def __init__(self):
            self.started = False
            self.stop_end = None
            self.closed = False

        def start(self):
            self.started = True

        def drain_lines(self):
            return []

        def stderr_tail(self):
            return ""

        def poll(self):
            return None                      # 永远"还活着"：收口必须自己去停它

        def stop(self, timeout=None, *, terminate_session=True):
            time.sleep(0.3)                  # 模拟真机上的"有界等它退 + 硬杀"
            self.stop_end = time.monotonic()

        def close(self, *, terminate_session=True):
            self.closed = True

    exe = tmp_path / gf.BACKEND_EXE_NAME
    exe.write_bytes(b"MZ")
    session = _SlowStopSession()
    monitor = fm.FpsMonitor(
        session_factory=lambda pid, name, session_name: session,
        foreground_reader=lambda: (4242, "game.exe"),
        process_identity=lambda pid: 1.0,
        excluded_names=frozenset(),
        target_stable_seconds=0.0,
        min_session_seconds=0.0,
        poll_seconds=0.02,
        publish_interval=0.0,
        backend_path=exe,
        platform_supported=lambda: True,
    )
    hub = island_fps.FpsHub(monitor_factory=lambda: monitor)
    slot = island_fps.FpsSlot(key="wait", hub=hub, platform_supported=lambda: True)
    slot.sync(enabled=True, visible=True)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not session.started:
        time.sleep(0.01)
    assert session.started, "会话没开起来，这条用例什么都没测到"

    hub.close(terminate_session=False)
    returned_at = time.monotonic()
    assert session.closed is True
    assert session.stop_end is not None, \
        "close 返回时会话收口还没跑完（join 被前一次 stop 清掉线程引用后变成空操作）"
    assert session.stop_end <= returned_at, "收口必须在 close 返回**之前**完成"


def test_the_hub_passes_the_no_spawn_flag_and_stays_closed(monkeypatch):
    """宿主收口：``terminate_session`` 原样透传；收口后**再不接受登记**。

    退出编排（``AppShell._teardown_fps_capture``）靠这条透传把"正常退出 = 官方终止
    命令"与"关机/注销 = 只停不派生"分开；而"收口后仍被登记"就是采集器诈尸。
    """
    monitor = _StubMonitor()
    hub = island_fps.FpsHub(monitor_factory=lambda: monitor)
    slot = island_fps.FpsSlot(key="exit", hub=hub, platform_supported=lambda: True)
    slot.sync(enabled=True, visible=True)
    assert hub.monitor is monitor

    hub.close(terminate_session=False)
    assert monitor.stop_flags == [False] and monitor.close_flags == [False]
    assert monitor.closed is True
    assert hub.closed is True and hub.monitor is None

    # 收口后的登记一律被拒（迟到同步不许重开一场采集），段自己收敛成"没有读数"
    slot.sync(enabled=True, visible=True)
    assert hub.monitor is None and slot.monitor is None
    assert slot.poll() == island_fps.PollResult()


def test_the_capability_gate_beats_the_platform_constant(monkeypatch):
    """平台闸门读的是**当刻**的判定（可注入），不是导入期定死的常量。"""
    slot = island_fps.FpsSlot(key="gate", hub=island_fps.FpsHub(),
                              platform_supported=lambda: False)
    assert slot.unavailable_reason()
    assert slot.segment().state == isl.STATE_SAMPLING, "不占位由宿主摘段，段本身仍可自洽"


def test_a_missing_backend_is_reported_as_a_capability_limit(monkeypatch):
    """内置采集程序不在：采集器如实报 ``unsupported``，段不占位、卡片写路径。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    reason = f"缺少内置采集程序 {gf.BACKEND_EXE_NAME}"
    monitor.sample = _sample(None, state=isl.STATE_UNSUPPORTED, reason=reason)
    result = slot.poll()
    assert result.occupying_changed is True
    assert slot.unavailable_reason() == reason


def test_unknown_and_error_are_never_reported_as_capability_limits(monkeypatch):
    """"当前没有读数"不等于"没有这个能力"：unknown/error 都不许摘掉段。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    for state, reason in ((isl.STATE_UNKNOWN, "没有前台窗口"),
                          (isl.STATE_ERROR, "采集程序异常退出（exit code 1）"),
                          (isl.STATE_SAMPLING, "")):
        monitor.sample = _sample(None, state=state, reason=reason)
        slot.poll()
        assert slot.unavailable_reason() == ""


# ---------------------------------------------------------------- 卡片素材
def test_hint_parts_carry_target_source_and_the_optional_displayed_rate(monkeypatch):
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(144.0, displayed=138.0)
    slot.poll()
    text = " · ".join(slot.hint_parts(ok=True, updated="刚刚"))
    assert "game.exe (pid 4242)" in text, "读数归属必须是具体进程"
    assert "跟随前台程序" in text
    assert gf.SOURCE_TEXT in text, "Present 采集来源要说清"
    assert "上屏帧率 138" in text and "另一口径" in text, "上屏帧率单独标口径、不混算"
    assert gf.UNIT_FULL in text
    assert "采样于 刚刚" in text


def test_hint_parts_say_why_when_there_is_no_target(monkeypatch):
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(None, state=isl.STATE_UNKNOWN, reason="没有前台窗口")
    slot.poll()
    text = " · ".join(slot.hint_parts())
    assert "没有前台窗口" in text
    assert gf.SOURCE_TEXT in text


def test_hint_parts_give_the_exact_value_on_overflow(monkeypatch):
    """溢出时盒上写记号、上下文给精确值（"至少 9999"与"实际 12000"两句都要在）。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    monitor.sample = _sample(12000.0)
    slot.poll()
    assert slot.segment().text == gf.MAX_TEXT
    text = " · ".join(slot.hint_parts(ok=True, updated="刚刚"))
    assert "12000" in text and gf.MAX_TEXT in text


def test_hint_parts_still_explain_themselves_without_any_snapshot(monkeypatch):
    slot, _monitor, _hub = _slot(monkeypatch)
    text = " · ".join(slot.hint_parts())
    assert gf.UNIT_FULL in text and gf.SOURCE_TEXT in text
    assert island_fps.NO_REASON in text


def test_the_collector_failure_text_reaches_the_card(monkeypatch):
    """后端 stderr 原文（权限不足的官方话）要能在卡片上下文里读到。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    sample = _sample(None, state=isl.STATE_ERROR, reason="采集会话启动失败")
    sample.detail["backend_stderr"] = "error: failed to start trace session (0x5)"
    monitor.sample = sample
    slot.poll()
    assert "0x5" in " · ".join(slot.hint_parts())


# ---------------------------------------------------------------- 宿主收口
def test_hub_close_stops_and_closes_the_collector(monkeypatch):
    slot, monitor, hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    hub.close()
    assert monitor.closed is True and hub.monitor is None and hub.keys == set()
    assert monitor.stops == 1


def test_shared_hub_is_a_process_wide_singleton(monkeypatch):
    """共享宿主是进程级单例：两个岛各自取到的是同一个宿主（多宠唯一的落点）。"""
    monkeypatch.setattr(island_fps, "_HUB", None)
    try:
        first = island_fps.shared_hub()
        assert island_fps.shared_hub() is first
        island_fps.reset_shared_hub()
        assert island_fps._HUB is None
    finally:
        island_fps.reset_shared_hub()


def test_slot_keys_are_unique_even_when_objects_are_replaced(monkeypatch):
    """段 key 用进程内序号而不是 ``id(self)``：id 会被回收复用，撞 key 就漏退订。"""
    keys = {island_fps.FpsSlot(platform_supported=lambda: True).key for _ in range(50)}
    assert len(keys) == 50


def test_a_dirty_snapshot_never_raises_and_never_fakes_a_reading(monkeypatch):
    """脏快照（词表外状态 / 非数字读数 / detail 不是 dict）降级成"采集中"，不抛不伪造。"""
    slot, monitor, _hub = _slot(monkeypatch)
    slot.sync(enabled=True, visible=True)
    dirty = fm.FpsSample(fps="NaN", state="weird", sampled_at=0.0,
                         target_pid=None, target_name="", detail=None)
    monitor.sample = dirty
    assert slot.poll().changed is True
    assert (slot.segment().state, slot.segment().text) == (
        isl.STATE_SAMPLING, isl.UNKNOWN_TEXT)
    assert slot.unavailable_reason() == "", "脏状态不许被当成能力不足"
    assert " · ".join(slot.hint_parts())  # 上下文照给，不炸绘制路径
