"""状态点来源接线：宿主转发 source、旧签名降级、截图等待信号。

这三条是「五态状态点真的会动」的接线前提；岛自身的渲染与归并在
`tests/test_petcore_island_visuals.py` 里。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from petcore.ui.app import CoreHost
from petcore.ui.screen.ask_window import ScreenAskWindow

app = QApplication.instance() or QApplication([])


class _Island:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set_agent_state(self, state: str, *, source: str = "core") -> None:
        self.calls.append((state, source))


class _LegacyIsland:
    """旧签名（没有 source 关键字）：宿主必须降级调用，不许抛。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_agent_state(self, state: str) -> None:
        self.calls.append(state)


def test_host_forwards_the_source_to_the_island() -> None:
    host = CoreHost.__new__(CoreHost)
    host.island = _Island()
    CoreHost.notify_agent_state(host, "working", source="chat")
    CoreHost.notify_agent_state(host, "idle", source="screen")
    assert host.island.calls == [("working", "chat"), ("idle", "screen")]


class _Pets:
    def __init__(self, windows) -> None:
        self._windows = list(windows)

    def each(self):
        return list(self._windows)


class _WorkWindow:
    def __init__(self) -> None:
        self.states: list[str] = []

    def set_work_state(self, state: str) -> None:
        self.states.append(state)


def test_host_forwards_work_state_to_every_pet() -> None:
    host = CoreHost.__new__(CoreHost)
    host.island = _Island()
    windows = [_WorkWindow(), _WorkWindow()]
    host.pets = _Pets(windows)
    CoreHost.notify_agent_state(host, "working", source="chat")
    CoreHost.notify_agent_state(host, "idle", source="chat")
    assert [window.states for window in windows] == [
        ["working", "idle"],
        ["working", "idle"],
    ]


def test_host_degrades_for_legacy_island_signatures() -> None:
    host = CoreHost.__new__(CoreHost)
    host.island = _LegacyIsland()
    CoreHost.notify_agent_state(host, "done", source="chat")  # 不许抛
    assert host.island.calls == ["done"]


def test_host_is_silent_without_an_island() -> None:
    host = CoreHost.__new__(CoreHost)
    host.island = None
    CoreHost.notify_agent_state(host, "working", source="chat")  # 不许抛


def test_screen_window_reports_waiting_transitions() -> None:
    window = ScreenAskWindow()
    seen: list[bool] = []
    window.waiting_changed.connect(seen.append)
    try:
        window.begin_waiting()
        window.end_waiting()
        window.end_waiting("失败")
        assert seen == [True, False, False]
    finally:
        window.close()
