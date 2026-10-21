# -*- coding: utf-8 -*-
"""游戏帧率采集的**进程级退出收口**（AppShell：正常退出 / 会话结束）。

锁定的行为契约（都在公开 seam 上断言，用真实 ``AppShell`` + 真 Qt 对象）：

1. **正常退出**（``aboutToQuit`` → ``_on_about_to_quit``）必须做**有界收口**：
   停采集线程 → 停会话（官方终止命令）→ 有界 join。岛自己的槽只**退订**（非阻塞），
   只靠它的话解释器会在采集子进程与它那场独占 ETW 会话收口完之前退出——这正是
   "退出后残留采集进程/会话"的成因。
2. **会话结束**（关机/注销 → ``_on_session_end``）同样收口，但**不派生任何进程**
   （issue #111 的纪律：那条路径不许 CreateProcess，采集收口只做有界硬停）。
3. 收口**幂等**、失败不阻断退出收尾；收口后本进程**不再开新的采集会话**（迟到的
   岛侧同步不许让采集器诈尸）。
4. 正常交互不受影响：收口只发生在进程级退出，隐藏岛 / 关一只宠仍走各岛的非阻塞路径。

时序纪律：全部同步直调（不赌定时器节奏），不碰真实 PresentMon 子进程。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pet.island import game_fps as gf
from pet.island import fps as island_fps
from pet.config import Config


@pytest.fixture(autouse=True)
def _clean_exit_latch():
    """退出闸门是进程级的：任一用例置位后必须复位，否则后续用例建不出宿主。"""
    island_fps._clear_exit_latch_for_tests()
    yield
    island_fps._clear_exit_latch_for_tests()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def teardown_calls(monkeypatch):
    """记录 ``reset_shared_hub`` 的调用（含 ``terminate_session``），不碰真采集链路。"""
    calls: list[bool] = []

    def _recorder(*, terminate_session: bool = True) -> None:
        calls.append(bool(terminate_session))

    monkeypatch.setattr(island_fps, "reset_shared_hub", _recorder)
    return calls


def _shell(app, tmp_path):
    """轻量 AppShell（与 session-end 收口套件同款：enable_chat=False，不建 UI）。"""
    from pet.app import AppShell

    return AppShell(app, Config(tmp_path), enable_chat=False)


def test_about_to_quit_closes_the_fps_capture_with_the_official_terminate(
        app, tmp_path, teardown_calls):
    """正常退出：必须有界收口，且走官方终止命令（唯一能保证释放 ETW 会话的方式）。"""
    from pet.app import AppShell

    shell = _shell(app, tmp_path)
    try:
        shell._on_about_to_quit()
        assert teardown_calls == [True], "退出收口必须收掉帧率采集（一次，走官方终止命令）"
    finally:
        AppShell._shutdown_live_for_tests()


def test_session_end_closes_the_capture_without_spawning_any_process(
        app, tmp_path, teardown_calls):
    """会话结束：同样收口，但**不派生进程**（issue #111 的关机纪律）。"""
    from pet.app import AppShell

    shell = _shell(app, tmp_path)
    try:
        shell._on_session_end()
        assert teardown_calls == [False], "关机/注销路径不许派生终止命令进程"
    finally:
        AppShell._shutdown_live_for_tests()


def test_every_exit_path_is_idempotent_and_never_raises(
        app, tmp_path, teardown_calls, monkeypatch):
    """两个退出信号可能都到、可能重复：幂等且不抛；收口本身失败也不阻断退出。"""
    from pet.app import AppShell

    shell = _shell(app, tmp_path)
    try:
        shell._on_about_to_quit()
        shell._on_about_to_quit()
        shell._on_session_end()
        shell._on_session_end()
        assert teardown_calls.count(True) == 2 and teardown_calls.count(False) == 2, \
            "每次退出信号都收口一次（收口自身幂等，重复调用无害）"

        def _boom(*, terminate_session: bool = True) -> None:
            raise RuntimeError("采集收口炸了")

        monkeypatch.setattr(island_fps, "reset_shared_hub", _boom)
        shell._on_about_to_quit()  # 不得抛出：退出收尾要尽力而为
        shell._on_session_end()
    finally:
        AppShell._shutdown_live_for_tests()


def test_the_real_teardown_stops_the_shared_hub_and_refuses_to_resurrect(
        app, tmp_path, monkeypatch):
    """真收口（不替换函数）：停共享宿主，且收口后**不再**接受任何登记。"""
    from pet.app import AppShell

    built: list[object] = []

    class _Monitor:
        def __init__(self):
            self.stops: list[bool] = []
            self.closed: list[bool] = []

        def configure(self, mode, process):
            return None

        def subscribe(self, key):
            return True

        def unsubscribe(self, key):
            return None

        def start(self):
            return True

        def stop(self, wait=False, timeout=2.0, *, terminate_session=True):
            self.stops.append(bool(terminate_session))

        def close(self, *, terminate_session=True):
            self.closed.append(bool(terminate_session))

        def snapshot(self):
            return None

    def _factory():
        monitor = _Monitor()
        built.append(monitor)
        return monitor

    # 能力闸门是可注入的**当刻判定**（真实现读 ``sys.platform``，非 Windows 上为假），
    # 而本用例测的是退出收口，与宿主平台无关。所以把它的两个视图一起钉成"这台机器有
    # 能力"——``platform_support()`` 就是 ``platform_problem() == ""`` 的别名，只钉一个
    # 会让替身世界自相矛盾（没能力的机器却报得出"有能力"）。
    monkeypatch.setattr(gf, "platform_support", lambda: True)
    monkeypatch.setattr(gf, "platform_problem", lambda: "")
    monkeypatch.setattr(island_fps, "_HUB", island_fps.FpsHub(monitor_factory=_factory))
    slot = island_fps.FpsSlot(key="pet-1")          # hub=None → 走真实 shared_hub()
    slot.sync(enabled=True, visible=True)
    assert isinstance(slot.monitor, _Monitor), "开通的段必须挂在共享宿主上"

    shell = _shell(app, tmp_path)
    try:
        shell._on_about_to_quit()
        monitor = slot.monitor
        assert monitor is not None and monitor.stops and monitor.closed, \
            "退出收口必须真的停线程 + 停会话（不是只退订）"

        # 收口后的迟到同步：不许再建一个采集器（否则退出途中又开一场采集）
        slot.sync(enabled=True, visible=True)
        assert len(built) == 1, "收口后不许再开新采集器"
        # 连"从没登记过的段"也不许建：退出闸门那一层同样要挡住（这是采集器诈尸的
        # 另一条路——它的 ``_hub_used`` 是空的，会去问 shared_hub() 要一个新宿主）
        late = island_fps.FpsSlot(key="pet-2")
        late.sync(enabled=True, visible=True)
        assert late.monitor is None and len(built) == 1
        assert island_fps.shared_hub().closed is True, "共享宿主仍处于收口态"
    finally:
        AppShell._shutdown_live_for_tests()
