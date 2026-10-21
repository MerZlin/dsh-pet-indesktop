# -*- coding: utf-8 -*-
"""聊天窗「关闭即销毁」回归（4.4.0，用户实机反馈：关窗后内存不返还）。

背景：完整聊天窗此前是**进程级缓存单例**——``closeEvent`` 只 ``hide()``，
``PetInstance`` 一直持引用，关窗后整棵控件树常驻到进程结束。探针
（``.scratch/chat-window-close/probe_window_lifetime.py``）证实两件事：
反复开关**不涨**（不是泄漏），但一次开窗的驻留再也不还（modern 61 控件 /
约 975 KiB pet 分配）。改成关闭即销毁后，本文件钉住四条契约：

1. 关窗之后 C++ 对象真的没了（``shiboken6.isValid`` False），实例槽位清空
   （否则下次打开会 show 一个已排队删除的对象）；
2. 重开是新对象，且发消息链路照常走到 ``service.send`` 并落库；
3. 反复开关不泄漏：存活控件回到基线、「回复完成」全局订阅数不翻倍；
4. 重开仍走历史分页（#318 的首屏 50 条），销毁重建没有绕过分页懒加载。
"""

from __future__ import annotations

import gc
import os
import time
import types
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import shiboken6  # noqa: E402

from pet.app import PetInstance  # noqa: E402
from pet.chat.service import ChatService  # noqa: E402
from pet.config import Config  # noqa: E402

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 夹具
def _pump_deferred() -> None:
    """把事件队列与 deleteLater 都跑干净（销毁断言必须这么等）。"""
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _pump_until(predicate, budget: float = 5.0) -> bool:
    deadline = time.time() + budget
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _FakePet(QWidget):
    """桌宠宿主替身（真实 PetWindow 要拉起 ffmpeg 素材库，单测跑不了）。

    聊天面只会用到这几面：可见身体框、位置监听、聊天状态回写。
    """

    def __init__(self) -> None:
        super().__init__()
        self.setGeometry(QRect(100, 100, 240, 240))
        self.position_listeners: list = []

    def visible_content_rect(self) -> QRect:
        return self.geometry()

    def add_position_listener(self, listener) -> None:
        if listener not in self.position_listeners:
            self.position_listeners.append(listener)

    def remove_position_listener(self, listener) -> None:
        if listener in self.position_listeners:
            self.position_listeners.remove(listener)

    def set_chat_status(self, state: str, text: str = "") -> None:
        pass

    def set_bubble_suppressed(self, on: bool) -> None:
        pass


def _make_instance(tmp_path) -> PetInstance:
    shell = types.SimpleNamespace(
        enable_chat=True,
        chat_build_capable=True,
        system_notify=lambda *a, **k: None,
        island=None,
        island_chat=None,
        _settings_child_active=False,
        _decode_hub=None,
        _refresh_tray_menu=lambda: None,
        instance=None,
    )
    inst = PetInstance(shell, Config(base=tmp_path))
    shell.instance = inst
    inst.win = _FakePet()
    inst.win.show()
    return inst


def _open(kind: str, inst: PetInstance):
    if kind == "modern":
        inst.open_modern_chat()
        return inst.modern_chat_window
    inst.open_legacy_chat()
    return inst.legacy_chat_window


def _close(win) -> None:
    win.close()
    _pump_deferred()


def _close_if_alive(window) -> None:
    """收尾用：聊天窗「关闭即销毁」，已销毁的对象不能再 close()。"""
    if window is not None and shiboken6.isValid(window):
        window.close()


def _open_close_cycle(kind: str, inst: PetInstance):
    """开→关一轮，只把 weakref 交回来：不留强引用，才能判定对象真被回收。"""
    win = _open(kind, inst)
    refs = (weakref.ref(win), weakref.ref(win.service))
    _close(win)
    return refs


@pytest.fixture(params=["modern", "legacy"])
def kind(request):
    return request.param


# ---------------------------------------------------------------- 契约 1：关闭即销毁
def test_chat_window_is_destroyed_on_close(tmp_path, kind):
    """关窗之后窗口对象必须真的没了，实例上的三个引用位一起清空。"""
    inst = _make_instance(tmp_path)
    win = _open(kind, inst)
    try:
        assert win.isVisible()
        assert win.isWindow()

        _close(win)

        assert not shiboken6.isValid(win), "关窗后 C++ 对象仍存活（还是缓存单例）"
        assert inst.modern_chat_window is None
        assert inst.legacy_chat_window is None
        assert inst.chat_window is None
    finally:
        for attr in ("modern_chat_window", "legacy_chat_window"):
            obj = getattr(inst, attr, None)
            if obj is not None and shiboken6.isValid(obj):
                obj.deleteLater()
        _pump_deferred()


def test_close_detaches_pet_position_listener(tmp_path, kind):
    """销毁前必须解除桌宠位置监听（对齐点：set_pet_window(None)），否则监听表常驻。"""
    inst = _make_instance(tmp_path)
    inst.config.set("chat_follow_pet", True)
    inst.config.save()
    win = _open(kind, inst)
    try:
        assert len(inst.win.position_listeners) == 1, "跟随桌宠应先注册一个位置监听"

        _close(win)

        assert inst.win.position_listeners == [], "关窗后位置监听没解除"
    finally:
        _pump_deferred()


# ---------------------------------------------------------------- 契约 2：重开可用
def test_open_twice_while_visible_reuses_the_same_window(tmp_path, kind):
    """窗口没关时再点一次「AI 对话」：复用同一只，不许叠出第二个窗口。"""
    inst = _make_instance(tmp_path)
    first = _open(kind, inst)
    try:
        second = _open(kind, inst)
        assert second is first, "重复打开叠出了第二个聊天窗"
    finally:
        _close_if_alive(first)
        _pump_deferred()


def test_reopen_builds_fresh_window_and_message_path_still_works(tmp_path, kind):
    """重开必须是新对象，且「发一条消息」的完整链路仍然通到 provider 并落库。"""
    inst = _make_instance(tmp_path)
    first = _open(kind, inst)
    first_id = id(first)
    _close(first)

    class _Provider:
        calls = 0

        def stream(self, messages, config, cancel):
            type(self).calls += 1
            yield "重开后的回答"

    win = _open(kind, inst)
    try:
        assert id(win) != first_id, "重开复用了旧对象（销毁路径没生效）"
        win.service.provider = _Provider()
        win.composer.input.setPlainText("关掉再打开还能聊吗")
        win.send_message()
        assert _pump_until(
            lambda: bool(win.session.messages)
            and win.session.messages[-1].role == "assistant"
        ), "重开后的窗口没能收到回答"
        assert _Provider.calls == 1, "消息没有走到 service.send"
        assert win.session.messages[-1].content == "重开后的回答"
    finally:
        _close(win)


# ---------------------------------------------------------------- 契约 3：反复开关不涨
def test_repeated_open_close_does_not_leak_widgets_or_subscriptions(tmp_path, kind):
    """开-关 三轮：存活控件回到基线，进程级「回复完成」订阅数不翻倍。"""
    inst = _make_instance(tmp_path)
    _pump_deferred()

    baseline_widgets = len(QApplication.allWidgets())
    baseline_listeners = len(ChatService._global_finished_listeners)
    window_refs: list[weakref.ref] = []
    service_refs: list[weakref.ref] = []

    for _ in range(3):
        win = _open(kind, inst)
        assert len(QApplication.allWidgets()) > baseline_widgets, "开窗没有建出控件（前置不成立）"
        window_refs.append(weakref.ref(win))
        service_refs.append(weakref.ref(win.service))
        del win
        _open_close_cycle(kind, inst)  # 每轮再多走一次「关→再开→再关」
        _pump_deferred()

    assert len(QApplication.allWidgets()) == baseline_widgets, "关窗后存活控件没有回到基线"
    assert len(ChatService._global_finished_listeners) == baseline_listeners, (
        "打开/关闭聊天窗不得改变进程级回复订阅表"
    )
    # 窗口与 ChatService 都不许被引用链挂着（关窗即销毁 + Qt 断连的对称点）
    assert all(ref() is None for ref in window_refs), "关窗后仍有 Python 引用链挂着窗口"
    assert all(ref() is None for ref in service_refs), "关窗后 ChatService 仍被引用链挂着"


# ---------------------------------------------------------------- 契约 4：重开仍走分页
def test_reopen_keeps_history_paging(tmp_path, kind):
    """销毁重建不得绕过分页（#318）：重开首屏仍只渲染最新一页。"""
    from pet.chat.models import ChatMessage
    from pet.chat.session_store import SessionStore

    total, page = 120, 50
    config = Config(base=tmp_path)
    store = SessionStore(config.dir, getattr(config, "instance_id", ""))
    session = store.create("shenshen", "openai-main", "sys")
    for index in range(total):
        role = "user" if index % 2 == 0 else "assistant"
        session.messages.append(ChatMessage(role, f"历史消息 {index}"))
    store.save(session)
    assert store.flush(), "历史消息没有落盘（前置不成立）"

    inst = _make_instance(tmp_path)
    first = _open(kind, inst)
    assert len(first._bubbles) == page
    _close(first)
    store.flush()

    win = _open(kind, inst)
    try:
        assert len(win._bubbles) == page, "重开后首屏不再是分页后的最新一页"
        assert win._hidden_count == total - page
        assert win._load_earlier_row is not None
    finally:
        _close(win)


# ---------------------------------------------------------------- 收口细节
def test_close_while_streaming_still_destroys_window(tmp_path, kind):
    """在飞请求关窗：先 stop 再销毁，不许带着运行中的 QThread 一起死。"""
    inst = _make_instance(tmp_path)
    win = _open(kind, inst)

    class _SlowProvider:
        def stream(self, messages, config, cancel):
            for _ in range(200):
                if cancel.wait(timeout=0.01):
                    return
                yield "…"

    win.service.provider = _SlowProvider()
    win.composer.input.setPlainText("慢慢答")
    win.send_message()
    assert _pump_until(lambda: win.service.busy), "请求没有进入在飞状态（前置不成立）"

    _close(win)
    # worker 退出后销毁才会发生：宽预算等它，超时仍不销毁就是真问题
    assert _pump_until(lambda: not shiboken6.isValid(win), budget=8.0), (
        "在飞请求关窗后窗口没有销毁（等 worker 的分支没接上）"
    )
    assert inst.modern_chat_window is None and inst.legacy_chat_window is None
    _pump_deferred()


def test_flash_hint_timer_uses_context_object(tmp_path, kind, monkeypatch):
    """提示栏 5s 复位定时器必须带上下文对象：关闭即销毁后定时器自动取消，
    不回调已删控件（ubuntu CI 实测 RuntimeError: QLabel already deleted——
    缓存单例时代窗口永不销毁，这条是潜伏隐患，关闭即销毁把它变成生产路径）。"""
    import pet.chat.legacy_widgets as legacy_mod
    import pet.chat.widgets as widgets_mod

    inst = _make_instance(tmp_path)
    win = _open(kind, inst)
    try:
        calls = []
        monkeypatch.setattr(widgets_mod.QTimer, "singleShot",
                            lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(legacy_mod.QTimer, "singleShot",
                            lambda *a, **kw: calls.append(a))
        flash = getattr(win, "_flash_notice", None) or getattr(win, "_flash_hint", None)
        if flash is None:
            composer = getattr(win, "composer", None)
            flash = getattr(composer, "_flash_notice", None) or getattr(composer, "_flash_hint", None)
        assert flash is not None
        flash("测试提示")
        timed = [a for a in calls if a and a[0] == 5000]
        assert timed, "没有找到 5s 复位定时器"
        assert all(len(a) >= 3 for a in timed), f"定时器未带上下文对象（销毁即取消）: {timed}"
    finally:
        for attr in ("modern_chat_window", "legacy_chat_window"):
            obj = getattr(inst, attr, None)
            if obj is not None and shiboken6.isValid(obj):
                obj.deleteLater()
        inst.chat_window = None
