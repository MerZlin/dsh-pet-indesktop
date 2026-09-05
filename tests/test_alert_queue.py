# -*- coding: utf-8 -*-
"""提醒消息队列（alert queue）行为测试。

所有需要用户注意的提醒（审批/问题/硬失败/卡住介入）经 PetWindow.show_alert
入队：一次只展示一个，当前有提醒时普通 show_bubble 让路不覆盖，hide_bubble/
限时结束自动弹出下一条，clear_alerts 清空全部。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        self._running = True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        pass

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self):
        self._clips = {name: FakeClip() for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


class FakeBubble(QObject):
    """记录 show_text / dismiss 调用的假气泡，供窗口队列测试。"""

    hidden_signal = Signal()

    def __init__(self):
        super().__init__()
        self.shown: list[dict] = []
        self.dismiss_calls = 0

    def show_text(self, text, anchor, duration_ms, *, pet_scale=None,
                  subtitle="", sticky=False, buttons=None):
        self.shown.append({
            "text": str(text), "sticky": bool(sticky),
            "duration_ms": int(duration_ms), "buttons": buttons,
        })

    def reposition(self, anchor_rect):
        pass

    def hide(self):
        pass

    def isVisible(self):
        return True

    def dismiss(self):
        self.dismiss_calls += 1


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path):
    lib = FakeLibrary()
    w = PetWindow(lib, Config(base=tmp_path))
    # 用记录型假气泡替换真实气泡，避免依赖 Qt 窗口显示
    w._speech_bubble = FakeBubble()
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


def test_show_alert_one_at_a_time(win):
    """多条提醒入队：只展示第一条，其余排队。"""
    win.show_alert("审批一", sticky=True)
    win.show_alert("硬失败", duration_ms=6000, sticky=False)
    win.show_alert("审批二", sticky=True)
    assert len(win._alert_queue) == 2, "后两条应排队"
    assert win._alert_current is not None
    assert win._alert_current["text"] == "审批一"
    assert win._speech_bubble.shown[-1]["text"] == "审批一"


def test_hide_bubble_pumps_next(win):
    """hide_bubble 关闭当前提醒后自动弹出下一条。"""
    win.show_alert("审批一", sticky=True)
    win.show_alert("硬失败", duration_ms=6000, sticky=False)
    win.hide_bubble()
    assert win._alert_current is not None
    assert win._alert_current["text"] == "硬失败"
    assert win._speech_bubble.shown[-1]["text"] == "硬失败"


def test_clear_alerts_empties_queue(win):
    """clear_alerts 清空队列并关闭当前气泡。"""
    win.show_alert("审批一", sticky=True)
    win.show_alert("审批二", sticky=True)
    win.clear_alerts()
    assert not win._alert_queue
    assert win._alert_current is None
    assert win._speech_bubble.dismiss_calls >= 1


def test_show_bubble_yields_when_alert_active(win):
    """队列有提醒在展示时，普通 show_bubble 让路不覆盖。"""
    win.show_alert("审批一", sticky=True)
    win.show_bubble("普通气泡", duration_ms=3000)
    # 普通气泡被抑制：未替换当前提醒
    assert win._speech_bubble.shown[-1]["text"] == "审批一"


def test_bubble_body_opens_quick_chat_only_for_plain_bubble(win):
    """普通无按钮气泡主体点击仍打开快速对话。"""
    opened = []
    win.on_open_quick_chat = lambda: opened.append(True)
    win.show_bubble("普通气泡")
    assert win._speech_bubble.shown[-1]["text"] == "普通气泡"
    assert win._speech_bubble.shown[-1]["sticky"] is False
    win._on_speech_bubble_clicked()
    assert opened == [True]


@pytest.mark.parametrize(
    "labels",
    [
        ["同意", "拒绝"],
        ["终止", "重试终止"],
        ["不要", "终止"],
    ],
)
def test_interactive_bubble_body_does_not_open_quick_chat(win, labels):
    """带决定/控制按钮的气泡主体点击不应打开快速对话。"""
    opened = []
    win.on_open_quick_chat = lambda: opened.append(True)
    win.show_alert(
        "请选择", buttons=[(label, lambda: None) for label in labels],
        sticky=True, alert_id="interactive",
    )
    win._on_speech_bubble_clicked()
    assert opened == []


def test_interactive_button_callback_still_runs_and_closes(win):
    actions = []
    win.show_alert(
        "提醒", buttons=[("关闭提醒", lambda: (actions.append(True), win.resolve_alert("close")))],
        sticky=True, alert_id="close",
    )
    callback = win._speech_bubble.shown[-1]["buttons"][0][1]
    callback()
    assert actions == [True]
    assert win._alert_current is None
    assert win._sticky_bubble_active is False
    win._on_speech_bubble_clicked()


def test_plain_and_interactive_bubble_clicks_are_independent(win):
    opened = []
    win.on_open_quick_chat = lambda: opened.append(True)
    win.show_bubble("普通气泡")
    win._on_speech_bubble_clicked()
    win.show_alert("审批", buttons=[("同意", lambda: None)], sticky=True, alert_id="mixed")
    win._on_speech_bubble_clicked()
    assert opened == [True]


def test_sticky_alert_is_sticky(win):
    """审批提醒 sticky=True。"""
    win.show_alert("审批一", sticky=True)
    assert win._speech_bubble.shown[-1]["sticky"] is True


def test_resolve_alert_by_id_closes_current(win):
    """resolve_alert 按 id 关闭当前展示的提醒，并推进下一条。"""
    win.show_alert("审批一", sticky=True, alert_id="a1")
    win.show_alert("审批二", sticky=True, alert_id="a2")
    assert win._alert_current["id"] == "a1"
    win.resolve_alert("a1")
    assert win._alert_current is not None, "队列中还有 a2，应自动弹出"
    assert win._alert_current["id"] == "a2"


def test_resolve_alert_by_id_removes_from_queue(win):
    """resolve_alert 按 id 移除队列中未展示的提醒（不打断当前展示）。"""
    win.show_alert("审批一", sticky=True, alert_id="a1")
    win.show_alert("审批二", sticky=True, alert_id="a2")
    win.show_alert("审批三", sticky=True, alert_id="a3")
    # 当前展示 a1，a2/a3 排队；resolve a2 不应影响 a1 展示
    win.resolve_alert("a2")
    assert win._alert_current["id"] == "a1"
    # 队列里只剩 a3
    assert len(win._alert_queue) == 1
    assert win._alert_queue[0]["id"] == "a3"


def test_resolve_alert_unknown_id_noop(win):
    """不存在的 alert_id 无影响。"""
    win.show_alert("审批一", sticky=True, alert_id="a1")
    win.resolve_alert("nonexistent")
    assert win._alert_current is not None


def test_show_alert_same_id_upgrades_current(win):
    """同 id 提醒重复到达：就地升级当前展示，而不是排队第二条。

    场景：同一审批先后到达「无 rpcId 提示 → 带 rpcId 交互」两条记录
    （桥接双通道/竞态）。第二条应刷新当前气泡的按钮，而不是被队列压住
    直到第一条 resolved 后才弹出（那样按钮会绑到已结束的审批）。"""
    win.show_alert("DSH 请求执行：echo hi，请选择：", sticky=True, alert_id="interaction:dsh:approval")
    assert win._alert_current is not None
    assert win._alert_current["id"] == "interaction:dsh:approval"
    assert win._alert_current["buttons"] is None
    assert len(win._alert_queue) == 0

    # 同 id 的交互版到达：就地升级（带同意/拒绝按钮），不排队
    win.show_alert(
        "DSH 请求执行：echo hi，请选择：",
        buttons=[("同意", lambda: None), ("拒绝", lambda: None)],
        sticky=True, alert_id="interaction:dsh:approval",
    )
    assert win._alert_current["buttons"] is not None, "当前气泡应升级为带按钮"
    assert len(win._alert_queue) == 0, "同 id 升级不应再排一条"
    shown = win._speech_bubble.shown[-1]
    assert shown["buttons"] is not None, "气泡本身应刷新为带按钮版本"
    assert shown["sticky"] is True


def test_show_alert_same_id_upgrades_queued(win):
    """同 id 提醒排队中：移除旧条目由新条目接管，不重复展示。

    场景：审批 A 正在展示（id=a1），审批 B（id=a2）入队；随后 B 的
    交互版到达，应替换队列里的旧 B 条目，而不是再叠加一条。"""
    win.show_alert("审批一", sticky=True, alert_id="a1")
    win.show_alert("审批二提示", sticky=True, alert_id="a2")
    assert win._alert_current["id"] == "a1"
    assert len(win._alert_queue) == 1
    assert win._alert_queue[0]["id"] == "a2"

    # B 的交互版到达：替换队列中的旧 B 条目
    win.show_alert("审批二", buttons=[("同意", lambda: None)],
                   sticky=True, alert_id="a2")
    assert len(win._alert_queue) == 1, "队列仍只有一条 B"
    assert win._alert_queue[0]["id"] == "a2"
    assert win._alert_queue[0]["buttons"] is not None


def test_timed_alert_not_sticky(win):
    """硬失败/卡住提醒 sticky=False（限时展示）。"""
    win.show_alert("硬失败", duration_ms=6000, sticky=False)
    assert win._speech_bubble.shown[-1]["sticky"] is False
    assert win._speech_bubble.shown[-1]["duration_ms"] == 6000
