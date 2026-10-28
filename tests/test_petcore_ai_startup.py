"""AI 对话扩展：失败分类必须说真话（内部错误 ≠ 网络不可达）。

用户报的现象：「刚打开桌宠时『看看屏幕』和『AI 对话』都报网络不可达，去设置里
点一次连接测试就好了」。可复现的代码缺陷在 mod 的兜底：

    reason = str(getattr(exc, "reason", "") or "network")

任何没有 ``reason`` 的异常（``KeyError``：服务 ID 对不上 / ``ValueError`` /
``ssl`` / ``json`` 解析失败）都被显示成「网络不可达」，而且**日志里不留一个字**，
所以既误导用户、又无法事后定位。这里锁住四条失败路径的分类与日志。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.api.credentials import KeyringCredentials
from petcore.api.store import ApiStore
from petcore.contracts.screen import RegionPick, ScreenCapture
from petcore.mods.feature import import_entrypoint
from petcore.net import errors as net_errors
from petcore.ui.ports import CoreApiPort

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "ai.chat"
FAILURE_LOG = "ai call failed"


def _create_host(context):
    factory = import_entrypoint(MOD_DIR, "host/__init__.py:create_host", "ai.chat")
    return factory(context)


class FakeConfig:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[str(key)] = value
        self.writes.append((str(key), value))


class FakeScheduler:
    """立即执行版 scheduler（生产实现排队到 GUI 线程）。"""

    def __init__(self) -> None:
        self.calls = 0

    def call_later(self, delay_ms, callback):
        self.calls += 1
        callback()
        return f"timer-{self.calls}"


class RecordingPort:
    """ChatPort / QuickChatPort 的最小实现：只记调用，最后一次 error/done 置事件。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.done_event = threading.Event()

    def record(self, owner, role, text, *, pet_slot=None):
        self.calls.append(("record", role, text))

    def token(self, owner, delta, *, pet_slot=None):
        self.calls.append(("token", delta))

    def reasoning(self, owner, delta, *, pet_slot=None):
        self.calls.append(("reasoning", delta))

    def status(self, owner, text, *, pet_slot=None):
        self.calls.append(("status", text))

    def done(self, owner, text="", *, pet_slot=None):
        self.calls.append(("done", text))
        self.done_event.set()

    def error(self, owner, reason, message="", *, pet_slot=None):
        self.calls.append(("error", reason, message))
        self.done_event.set()

    def history(self, owner, *, pet_slot=None):
        return []

    def take_attachments(self, owner, *, pet_slot=None):
        return []

    def on_submit(self, owner, callback):
        return lambda: None

    def on_stop(self, owner, callback):
        return lambda: None

    def open(self, owner, *, pet_slot=None, title=""):
        self.calls.append(("open", pet_slot, title))
        return True

    def errors(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "error"]


class RaisingApi:
    """``stream_chat`` 抛指定异常的 ApiPort；``services()`` 按需为空。"""

    def __init__(self, exc: BaseException | None = None, *, service_id: str = "svc") -> None:
        self.exc = exc
        self.service_id = str(service_id)
        self.calls = 0

    def services(self):
        if not self.service_id:
            return []
        return [
            SimpleNamespace(
                service_id=self.service_id,
                name="服务",
                model="chat-model",
                vision_model="vision-model",
            )
        ]

    def stream_chat(self, *_args, **_kwargs):
        self.calls += 1
        raise self.exc if self.exc is not None else AssertionError("不可达")

    def open_settings(self):
        return None


class RenderingApi:
    """把调用交给真实 ``CoreApiPort``（``resolve`` → ``metadata`` 的真实路径）。"""

    def __init__(self, port: CoreApiPort, service_id: str = "ghost") -> None:
        self.port = port
        self.service_id = str(service_id)
        self.calls = 0

    def services(self):
        return [SimpleNamespace(service_id=self.service_id, name="幽灵", model="m")]

    def stream_chat(self, service_id, messages, **_kwargs):
        self.calls += 1
        return self.port.stream_chat(service_id, messages)

    def open_settings(self):
        return None


class FakeScreen:
    def capture(self, *, pet_slot=None):
        return ScreenCapture(
            png=b"png-bytes",
            width=8,
            height=6,
            screen_name="fake",
            app_info="code.exe | main.py",
        )

    def capture_region(self, *, pet_slot=None, ai_available=True):
        return RegionPick(
            ScreenCapture(png=b"region-png", width=8, height=6, screen_name="fake"), "ask"
        )

    def foreground_app(self):
        return "code.exe | main.py"


class FakeScreenAsk:
    def __init__(self) -> None:
        self.callback = None
        self.action_callback = None

    def on_ask(self, owner, callback):
        self.callback = callback
        return lambda: None

    def on_action(self, owner, callback, *, priority=100, actions=()):
        self.action_callback = callback
        return lambda: None

    def open(self, owner, capture, *, pet_slot=None, title=""):
        return True


class FakePresentation:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text, **_kwargs):
        self.said.append(str(text))


class FakePets:
    def active(self):
        return SimpleNamespace(slot=0)

    def primary(self):
        return SimpleNamespace(slot=0)


def _context(tmp_path: Path, api) -> SimpleNamespace:
    ports = SimpleNamespace(
        api=api,
        chat=RecordingPort(),
        quick_chat=RecordingPort(),
        screen=FakeScreen(),
        screen_ask=FakeScreenAsk(),
        pets=FakePets(),
        presentation=FakePresentation(),
        config=FakeConfig(),
        scheduler=FakeScheduler(),
    )
    host = SimpleNamespace(character_for=lambda _id: SimpleNamespace(name="鲸鱼娘"))
    return SimpleNamespace(host=host, ports=ports)


def _runtime(tmp_path: Path, api) -> SimpleNamespace:
    context = _context(tmp_path, api)
    instance = _create_host(context).runtime_factory(context)
    return instance


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wait_until(app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _failure_logs(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith(FAILURE_LOG)
    ]


# --------------------------------------------------------------- 主体：主/快速对话


@pytest.mark.parametrize("port_name", ["chat", "quick_chat"])
def test_unknown_exception_is_reported_as_internal_not_network(
    tmp_path: Path, caplog, port_name: str
) -> None:
    """KeyError（没有 .reason）→ internal；只报一次；日志里有一条失败记录。"""
    api = RaisingApi(KeyError("unknown api service: svc"))
    instance = _runtime(tmp_path, api)
    port = getattr(instance.context.ports, port_name)
    start = instance._start if port_name == "chat" else instance._start_quick

    with caplog.at_level(logging.WARNING):
        start(0, [{"role": "user", "content": "hi"}])
        assert port.done_event.wait(5), "失败回调没有结束"

    failures = port.errors()
    assert len(failures) == 1, "失败只许报一次（没有重试刷屏）"
    assert failures[0][1] == "internal"
    assert failures[0][1] != net_errors.NETWORK
    assert api.calls == 1, "不许重试"
    logs = _failure_logs(caplog)
    assert len(logs) == 1, logs
    assert "type=KeyError" in logs[0]
    assert "reason=internal" in logs[0]
    assert "service=svc" in logs[0]


@pytest.mark.parametrize(
    "exc, expected",
    [
        (net_errors.NetworkError(reason=net_errors.NETWORK), net_errors.NETWORK),
        (net_errors.NetworkTimeout("连接超时"), net_errors.TIMEOUT),
        (net_errors.AuthError(401), net_errors.AUTH),
        (net_errors.NetworkError(reason=net_errors.TLS), net_errors.TLS),
        (net_errors.HttpStatusError(400), net_errors.PROTOCOL),
    ],
)
def test_classified_failures_keep_their_own_reason(
    tmp_path: Path, exc: BaseException, expected: str
) -> None:
    """既有分类行为不变：带 .reason 的异常照旧透传。"""
    api = RaisingApi(exc)
    instance = _runtime(tmp_path, api)
    instance._start(0, [])
    chat = instance.context.ports.chat
    assert chat.done_event.wait(5)
    assert [call[1] for call in chat.errors()] == [expected]


def test_no_service_is_internal_before_any_request(tmp_path: Path, caplog) -> None:
    """没有服务时不许发请求，也不许说成网络问题。"""
    api = RaisingApi(KeyError("unused"), service_id="")
    instance = _runtime(tmp_path, api)
    with caplog.at_level(logging.WARNING):
        instance._start(0, [])
    chat = instance.context.ports.chat
    assert chat.done_event.wait(5)
    failures = chat.errors()
    assert len(failures) == 1
    assert failures[0][1] == "internal"
    assert "API 服务" in failures[0][2]
    assert api.calls == 0, "没有服务就不该发出请求"
    logs = _failure_logs(caplog)
    assert len(logs) == 1, logs
    assert "type=AiChatFailure" in logs[0]


def test_failure_log_records_the_wrapped_cause_type(tmp_path: Path, caplog) -> None:
    """core 把底层传输异常**包装**成 NetworkError；日志要能看出底层是什么。"""
    try:
        try:
            raise ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。")
        except OSError as exc:
            raise net_errors.NetworkError(reason=net_errors.NETWORK) from exc
    except net_errors.NetworkError as wrapped:
        api = RaisingApi(wrapped)
    instance = _runtime(tmp_path, api)
    with caplog.at_level(logging.WARNING):
        instance._start(0, [])
    chat = instance.context.ports.chat
    assert chat.done_event.wait(5)
    assert [call[1] for call in chat.errors()] == [net_errors.NETWORK]
    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert "type=NetworkError" in logs[0]
    assert "reason=network" in logs[0]
    assert "cause=ConnectionRefusedError" in logs[0]


# --------------------------------------------------------------- 看看屏幕 / 截图问答


def test_look_screen_unknown_exception_reports_internal(qt_app, tmp_path: Path, caplog) -> None:
    api = RaisingApi(ValueError("bad payload"))
    instance = _runtime(tmp_path, api)
    presentation = instance.context.ports.presentation
    with caplog.at_level(logging.WARNING):
        instance.look_screen(0)
        assert _wait_until(qt_app, lambda: len(presentation.said) >= 2)
    final = presentation.said[-1]
    assert "内部错误" in final
    assert "network" not in final
    assert any("type=ValueError" in line for line in _failure_logs(caplog))


def test_screen_ask_unknown_exception_carries_internal_reason(
    tmp_path: Path, caplog
) -> None:
    api = RaisingApi(KeyError("boom"))
    instance = _runtime(tmp_path, api)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(Exception) as caught:
            instance._on_ask("这是什么？", b"png", [], 0)
    exc = caught.value
    assert getattr(exc, "reason", None) == "internal"
    assert "内部错误" in str(exc)
    assert "网络不可达" not in str(exc)
    assert api.calls == 1
    assert len(_failure_logs(caplog)) == 1


def test_screen_action_fallback_unknown_exception_carries_internal_reason(
    tmp_path: Path,
) -> None:
    api = RaisingApi(ValueError("bad json"))
    instance = _runtime(tmp_path, api)
    with pytest.raises(Exception) as caught:
        instance._on_action("ocr", "", b"png", 0)
    assert getattr(caught.value, "reason", None) == "internal"


def test_screen_ask_without_service_reports_internal_without_request(tmp_path: Path) -> None:
    api = RaisingApi(KeyError("unused"), service_id="")
    instance = _runtime(tmp_path, api)
    with pytest.raises(Exception) as caught:
        instance._on_ask("这是什么？", b"png", [], 0)
    assert getattr(caught.value, "reason", None) == "internal"
    assert "API 服务" in str(caught.value)
    assert api.calls == 0


# --------------------------------------------------------------- 真实路径：metadata KeyError


def test_core_metadata_keyerror_has_no_reason_and_becomes_internal(
    tmp_path: Path, caplog
) -> None:
    """``CoreApiPort.metadata`` 的 KeyError 没有 ``.reason``，核心分类器也返回 None
    ——这正是过去被兜底成「网络不可达」的那条真实路径。"""
    store = ApiStore(tmp_path)  # 空配置：没有任何服务
    credentials = SimpleNamespace(get=lambda owner, key: None)
    port = CoreApiPort(SimpleNamespace(open_settings=lambda *_: None), store, credentials)

    with pytest.raises(KeyError) as caught:
        port.metadata("ghost")
    assert getattr(caught.value, "reason", None) is None
    assert net_errors.classify_exception(caught.value) is None

    api = RenderingApi(port)
    instance = _runtime(tmp_path, api)
    with caplog.at_level(logging.WARNING):
        instance._start(0, [{"role": "user", "content": "hi"}])
    chat = instance.context.ports.chat
    assert chat.done_event.wait(5)
    failures = chat.errors()
    assert len(failures) == 1
    assert failures[0][1] == "internal"
    assert failures[0][1] != net_errors.NETWORK
    assert any("type=KeyError" in line for line in _failure_logs(caplog))


def test_keyring_credentials_degrade_instead_of_raising(tmp_path: Path) -> None:
    """惰性且不抛：后端坏掉/首启拿不到 Key 只会表现为 auth（401），不会被分类成网络失败。"""

    class BrokenBackend:
        def get_keyring(self):
            return self

        def get_password(self, service, entry):
            raise RuntimeError("keyring backend exploded")

        def set_password(self, service, entry, value):
            raise RuntimeError("keyring backend exploded")

        def delete_password(self, service, entry):
            raise RuntimeError("keyring backend exploded")

    credentials = KeyringCredentials(backend=BrokenBackend())
    assert credentials.available() is True
    assert credentials.get("core.api", "api1") is None
