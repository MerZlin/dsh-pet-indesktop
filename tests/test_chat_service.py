from __future__ import annotations

import time
import threading
from PySide6.QtWidgets import QApplication
from pet.chat.models import ProviderConfig
from pet.chat.service import ChatService


def _get_qapp():
    return QApplication.instance() or QApplication([])


class BlockingFakeProvider:
    def __init__(self, started_event: threading.Event | None = None, block_event: threading.Event | None = None):
        self.started_event = started_event or threading.Event()
        self.block_event = block_event or threading.Event()

    def stream(self, messages, config, cancel):
        self.started_event.set()
        while not cancel.is_set() and not self.block_event.is_set():
            time.sleep(0.01)
        if cancel.is_set():
            return
        yield "response"


class FastFakeProvider:
    def stream(self, messages, config, cancel):
        yield "hello world"


def test_concurrent_send_keeps_both_workers_in_set_and_cancels_old():
    app = _get_qapp()
    p1_started = threading.Event()
    p1_block = threading.Event()
    provider1 = BlockingFakeProvider(started_event=p1_started, block_event=p1_block)

    p2_started = threading.Event()
    p2_block = threading.Event()
    provider2 = BlockingFakeProvider(started_event=p2_started, block_event=p2_block)

    service = ChatService(provider=provider1)
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    rid1 = service.send([], cfg)
    assert p1_started.wait(timeout=2.0), "Worker 1 did not start"
    w1 = service._worker
    c1 = service._cancel
    assert w1 in service._workers
    assert not c1.is_set()

    # 替换 provider 并发第二个请求
    service.provider = provider2
    rid2 = service.send([], cfg)
    assert p2_started.wait(timeout=2.0), "Worker 2 did not start"
    w2 = service._worker
    c2 = service._cancel

    # a) 连续 send 两次后两个 worker 都在集合中、旧的被取消标记已置位
    assert len(service._workers) == 2
    assert w1 in service._workers
    assert w2 in service._workers
    assert c1.is_set()
    assert not c2.is_set()

    # 清理退出
    service.shutdown()
    assert len(service._workers) == 0


def test_worker_finished_removes_from_workers_set():
    app = _get_qapp()
    service = ChatService(provider=FastFakeProvider())
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    finished_events = []
    service.finished.connect(lambda rid, text: finished_events.append(text))

    rid = service.send([], cfg)
    worker = service._worker
    assert worker in service._workers

    # 等待完成
    deadline = time.time() + 2.0
    while len(finished_events) == 0 and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    # b) worker 结束后自动从集合移除
    assert len(finished_events) == 1
    assert worker not in service._workers
    assert len(service._workers) == 0


def test_shutdown_cancels_and_clears_workers_set():
    app = _get_qapp()
    p1_started = threading.Event()
    provider = BlockingFakeProvider(started_event=p1_started)

    service = ChatService(provider=provider)
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    service.send([], cfg)
    assert p1_started.wait(timeout=2.0)
    assert len(service._workers) == 1

    # c) shutdown 后集合清空
    service.shutdown()
    assert len(service._workers) == 0
    assert not service.busy
