# -*- coding: utf-8 -*-
"""拖文件解读功能测试：确认气泡 → 新会话请求 → 心跳进度 → 摘要落库。

时序纪律：心跳用例以事件循环轮询 + 宽预算等待（无固定 sleep 猜时序）；
ChatService 以注入替身替换（同 tests/test_chat_service.py 的注入模式）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from pet.chat.models import ChatSettings, ProviderConfig
from pet.chat.session_store import SessionStore
from pet.config import Config
from pet.file_interpret import (
    CONFIRM_ALERT_ID,
    FileInterpretController,
    build_interpret_user_text,
    collect_interpretable,
    extract_text,
    is_interpretable_file,
)
from pet.settings_file_interpret import (
    create_file_interpret_controls,
    save_file_interpret_settings,
)


def _qapp():
    return QApplication.instance() or QApplication([])


def _settings(api_key: str = "sk-x") -> ChatSettings:
    provider = ProviderConfig.from_dict("test", {"model": "test-model", "api_key": api_key})
    return ChatSettings(
        active_provider="test",
        default_system_prompt="sys-prompt",
        providers={"test": provider},
    )


class _FakeWin(QWidget):
    """只实现控制器消费面的窗替身：cfg + 气泡/提醒记录。"""

    def __init__(self, tmp_path, data: dict | None = None, api_key: str = "sk-x"):
        super().__init__()
        self._data = {
            "character": "shenshen",
            "file_interpret": {"enabled": True, "progress_interval_seconds": 15.0},
            **(data or {}),
        }
        self.cfg = SimpleNamespace(
            dir=tmp_path,
            instance_id="",
            get=lambda k, d=None: self._data.get(k, d),
            chat_settings=lambda: _settings(api_key),
            resolve_api_key=lambda p: p.api_key,
        )
        self.alerts: list[tuple[str, dict]] = []
        self.resolved: list[str] = []
        self.bubbles: list[str] = []

    def show_alert(self, text, **kwargs):
        self.alerts.append((text, kwargs))

    def resolve_alert(self, alert_id):
        self.resolved.append(alert_id)

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **kwargs):
        self.bubbles.append(str(text))


class FakeChatService(QObject):
    """与 ChatService 同信号面的替身；send 只记录，由用例手动驱动信号。"""

    started = Signal(str)
    delta = Signal(str, str)
    finished = Signal(str, str)
    error = Signal(str, str)
    stopped = Signal(str)

    busy = False

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[list[dict], object]] = []

    def send(self, messages, config, request_id=None):
        rid = f"rid-{len(self.sent) + 1}"
        self.sent.append((messages, config))
        return rid

    def stop(self):
        pass


def _confirm_button(win: _FakeWin):
    _, kwargs = win.alerts[-1]
    return dict(kwargs["buttons"])["解读"]


def _decline_button(win: _FakeWin):
    _, kwargs = win.alerts[-1]
    return dict(kwargs["buttons"])["不用了"]


# ---------------------------------------------------------------- 纯函数


def test_is_interpretable_file_filters_by_text_extension():
    assert is_interpretable_file("notes.md") is True
    assert is_interpretable_file("script.py") is True
    assert is_interpretable_file("photo.png") is False
    assert is_interpretable_file("archive.zip") is False


def test_collect_interpretable_dedupes_and_skips_dirs(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    d = tmp_path / "dir"
    d.mkdir()
    collected = collect_interpretable([str(a), str(a), str(d), str(tmp_path / "gone.txt")])
    assert collected == [a]


def test_extract_text_reads_in_budget_and_marks_truncation(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("1" * 30, encoding="utf-8")
    second.write_text("2" * 30, encoding="utf-8")

    content, skipped = extract_text([first, second], max_chars=50)

    assert skipped == []
    assert "1" * 30 in content
    assert "2" * 20 in content  # 剩余预算内截断
    assert "2" * 21 not in content
    assert "已截断" in content


def test_extract_text_skips_when_budget_exhausted(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("1" * 50, encoding="utf-8")
    second.write_text("2" * 10, encoding="utf-8")

    content, skipped = extract_text([first, second], max_chars=50)

    assert skipped == ["second.txt"]
    assert "1" * 50 in content
    assert "2" not in content


def test_build_interpret_user_text_contains_instruction_and_names():
    text = build_interpret_user_text("FILE-BODY", ["a.txt"], ["gone.bin"])
    assert "FILE-BODY" in text
    assert "a.txt" in text
    assert "gone.bin" in text


# ---------------------------------------------------------------- offer 流程


def test_offer_shows_sticky_confirm_alert(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    controller = FileInterpretController(win)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])

    assert len(win.alerts) == 1
    text, kwargs = win.alerts[0]
    assert kwargs["sticky"] is True
    assert kwargs["alert_id"] == CONFIRM_ALERT_ID
    assert kwargs["priority"] <= 1
    labels = [label for label, _ in kwargs["buttons"]]
    assert labels == ["解读", "不用了"]
    assert "AI 模型" in kwargs["subtitle"]


def test_offer_disabled_is_ignored(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path, data={"file_interpret": {"enabled": False}})
    controller = FileInterpretController(win)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])

    assert win.alerts == []


def test_offer_without_interpretable_files_only_hints(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    controller = FileInterpretController(win)
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01")

    controller.offer([str(blob)])

    assert win.alerts == []
    assert any("读不了" in b for b in win.bubbles)


def test_decline_resolves_alert_without_request(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])
    _decline_button(win)()

    assert CONFIRM_ALERT_ID in win.resolved
    assert service.sent == []
    store = SessionStore(tmp_path, "")
    assert store.list("shenshen") == []


def test_offer_twice_while_running_is_busy_hint(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])
    _confirm_button(win)()
    qapp = _qapp()
    qapp.processEvents()
    controller.offer([str(note)])

    assert len(win.alerts) == 1  # 不弹第二个确认
    assert any("正在解读" in b or "已经在解读" in b for b in win.bubbles)


# ---------------------------------------------------------------- 确认后主链路


def test_confirm_creates_session_sends_and_writes_back(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("文件正文ABC", encoding="utf-8")

    controller.offer([str(note)])
    _confirm_button(win)()
    _qapp().processEvents()

    # 请求：system prompt 来自角色设置，user 消息含文件内容，key 已解析
    assert len(service.sent) == 1
    messages, config = service.sent[0]
    assert messages[0] == {"role": "system", "content": "sys-prompt"}
    assert "文件正文ABC" in messages[-1]["content"]
    assert config.api_key == "sk-x"

    # 模型回复到达 → 摘要气泡 + assistant 落库
    service.finished.emit("rid-1", "这是摘要")
    _qapp().processEvents()

    # 会话落库：user + assistant（AI 对话窗口可见）
    store = SessionStore(tmp_path, "")
    sessions = store.list("shenshen")
    assert len(sessions) == 1
    roles = [m.role for m in sessions[0].messages]
    assert roles == ["user", "assistant"]
    assert "文件正文ABC" in sessions[0].messages[0].content
    assert sessions[0].messages[1].content == "这是摘要"
    assert "文件解读" in (sessions[0].custom_title or "")

    # 摘要气泡 + 确认提醒收尾
    assert any("解读完成" in b and "这是摘要" in b for b in win.bubbles)
    assert CONFIRM_ALERT_ID in win.resolved

    store.flush()


def test_confirm_without_api_key_hints_and_skips(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path, api_key="")
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])
    _confirm_button(win)()
    _qapp().processEvents()

    assert service.sent == []
    assert any("API Key" in b or "API key" in b for b in win.bubbles)
    store = SessionStore(tmp_path, "")
    assert store.list("shenshen") == []
    store.flush()


def test_error_bubbles_and_resets_state(tmp_path):
    _qapp()
    win = _FakeWin(tmp_path)
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")

    controller.offer([str(note)])
    _confirm_button(win)()
    service.error.emit(service.sent and "rid-1" or "rid-1", "网络炸了")
    _qapp().processEvents()

    assert any("解读失败" in b and "网络炸了" in b for b in win.bubbles)
    # 状态复位：可以再次 offer 并重新弹确认
    controller.offer([str(note)])
    assert len(win.alerts) == 2


# ---------------------------------------------------------------- 心跳进度


def _pump_until(qapp, predicate, budget_seconds=6.0):
    """事件循环轮询至谓词成立（宽预算，无固定 sleep 猜时序）。"""
    deadline = time.monotonic() + budget_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_heartbeat_reports_chars_and_elapsed(tmp_path):
    _qapp()
    win = _FakeWin(
        tmp_path,
        data={
            "file_interpret": {"enabled": True, "progress_interval_seconds": 0.2},
        },
    )
    service = FakeChatService()
    controller = FileInterpretController(win, service_factory=lambda: service)
    note = tmp_path / "note.md"
    note.write_text("内容", encoding="utf-8")
    qapp = _qapp()

    controller.offer([str(note)])
    _confirm_button(win)()
    service.delta.emit("rid-1", "abc")

    def heartbeats():
        return [b for b in win.bubbles if "正在解读" in b and "已读" in b]

    assert _pump_until(qapp, lambda: len(heartbeats()) >= 2)
    text = heartbeats()[-1]
    assert "3 字" in text  # delta 聚合的真实字数
    assert "秒" in text

    service.finished.emit("rid-1", "这是摘要")
    assert _pump_until(qapp, lambda: any("解读完成" in b for b in win.bubbles))
    assert not controller._heartbeat.isActive()


# ---------------------------------------------------------------- 配置与设置页


def test_config_file_interpret_defaults_and_normalization(tmp_path):
    cfg = Config(base=tmp_path)
    fi = cfg.get("file_interpret")
    assert fi["enabled"] is True
    assert fi["progress_interval_seconds"] == 15.0

    # 脏值经 set() 归一化
    cfg.set("file_interpret", {"enabled": "false", "progress_interval_seconds": 9999})
    fi = cfg.get("file_interpret")
    assert fi["enabled"] is False
    assert fi["progress_interval_seconds"] == 120.0
    cfg.save()

    # reload 后仍归一化
    cfg2 = Config(base=tmp_path)
    fi2 = cfg2.get("file_interpret")
    assert fi2["enabled"] is False
    assert fi2["progress_interval_seconds"] == 120.0


def test_settings_controls_roundtrip(tmp_path):
    _qapp()
    cfg = Config(base=tmp_path)
    host = QWidget()
    host.config = cfg

    create_file_interpret_controls(host)
    host.file_interpret_enabled_check.setChecked(False)
    host.file_interpret_interval_spin.setValue(42)
    save_file_interpret_settings(host)

    cfg2 = Config(base=tmp_path)
    fi = cfg2.get("file_interpret")
    assert fi["enabled"] is False
    assert fi["progress_interval_seconds"] == 42.0


def test_extract_text_never_reads_whole_file(tmp_path, monkeypatch):
    """extract_text 必须按预算截断读取，不得整文件读入。

    #150 性能评估反馈：read_text 先把整个文件读进内存再截断，拖几百 MB
    的日志时 GUI 线程静默卡死且心跳尚未开始。本用例钉住实现契约：
    Path.read_text 不得被调用（改用有界 read）。
    """
    import pathlib

    big = tmp_path / "big.log"
    big.write_text("x" * 500_000, encoding="utf-8")

    def _boom(self, *args, **kwargs):
        raise AssertionError("extract_text 不得整文件读入（read_text）")

    monkeypatch.setattr(pathlib.Path, "read_text", _boom)

    content, skipped = extract_text([big], max_chars=2000)

    assert skipped == []
    assert "x" * 2000 in content
    assert "x" * 2001 not in content
    assert "已截断" in content
