"""Public QThread/Qt seams; generated providers, no model or credentials."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from pet.chat.models import ProviderConfig
from pet.chat.service import ChatService


def config():
    return ProviderConfig(provider_id="fixture", name="fixture", base_url="http://invalid", model="fixture")


def pump_until(app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    assert predicate()
    app.processEvents()


def test_shutdown_never_calls_qthread_wait_and_drains_asynchronously():
    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()

    class Provider:
        def stream(self, messages, config, cancel):
            started.set()
            release.wait(12)
            yield "late"

    service = ChatService(provider=Provider())
    service.send([], config())
    assert started.wait(12)
    worker = service._worker
    original_wait = worker.wait
    gui_thread = threading.get_ident()

    def checked_wait(*args):
        assert threading.get_ident() != gui_thread, "GUI must not wait"
        return original_wait(*args)

    worker.wait = checked_wait
    try:
        assert service.shutdown() is False
        assert worker in service._workers
        release.set()
        pump_until(app, lambda: service.is_drained)
        assert not service.accepting
    finally:
        release.set()
        worker.wait = original_wait
        original_wait(12000)
        app.processEvents()


def test_same_request_id_cannot_accept_previous_generation_results():
    app = QApplication.instance() or QApplication([])

    class Provider:
        def stream(self, messages, config, cancel):
            yield messages[0]["content"]

    service = ChatService(provider=Provider())
    results = []
    service.finished.connect(lambda rid, text: results.append(text))
    service.send([{"content": "old generation"}], config(), request_id="same-id")
    service._worker.wait(12000)
    service.send([{"content": "new generation"}], config(), request_id="same-id")
    worker = service._worker
    try:
        pump_until(app, lambda: not worker.isRunning())
        app.processEvents()
        assert results == ["new generation"]
    finally:
        service.shutdown()
        worker.wait(12000)
        app.processEvents()


def test_response_close_never_blocks_gui_thread():
    app = QApplication.instance() or QApplication([])
    entered, closing, release = threading.Event(), threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    close_threads = []

    class Response:
        def close(self):
            close_threads.append(threading.get_ident())
            closing.set()
            release.wait(12)

    class Provider:
        def stream(self, messages, config, cancel, response_holder=None):
            response_holder.append(Response())
            entered.set()
            cancel.wait(12)
            yield "late"

    service = ChatService(provider=Provider())
    service.send([], config())
    worker = service._worker
    assert entered.wait(12)
    # If close runs on GUI, this timer cannot execute; the assertion becomes red
    # after the provider's finite safety budget, never hanging the suite.
    heartbeat = []
    QTimer.singleShot(0, lambda: (heartbeat.append(True), release.set()))
    try:
        service.shutdown()
        assert not release.is_set(), "GUI close synchronously exhausted the response's safety budget"
        pump_until(app, lambda: bool(heartbeat) and service.is_drained)
        assert closing.is_set()
        assert all(t != gui_thread for t in close_threads)
    finally:
        release.set()
        worker.wait(12000)
        app.processEvents()


def test_real_application_quit_keeps_event_loop_alive_until_thread_drain():
    script = """
import json, threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pet.chat.service import ChatService
from pet.chat.models import ProviderConfig
app=QApplication([])
started=threading.Event(); release=threading.Event()
class Provider:
 def stream(self, messages, config, cancel):
  started.set(); cancel.wait(12); release.wait(12); yield 'discarded'
service=ChatService(provider=Provider())
service.send([], ProviderConfig(provider_id='fixture',name='fixture',base_url='http://invalid',model='fixture'))
assert started.wait(12)
worker=service._worker
original_wait=worker.wait; gui_thread=threading.get_ident()
def checked_wait(*a):
 assert threading.get_ident()!=gui_thread, 'GUI wait forbidden'
 return original_wait(*a)
worker.wait=checked_wait
QTimer.singleShot(0,app.quit)
QTimer.singleShot(50,release.set)
app.exec()
print(json.dumps({'drained':service.is_drained,'running':worker.isRunning(),'released':release.is_set()}), flush=True)
"""
    child = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=35)
    assert child.returncode == 0, child.stderr
    evidence = json.loads(child.stdout)
    assert evidence == {"drained": True, "running": False, "released": True}


def test_paused_owner_rejects_new_work_and_resume_waits_for_real_drain():
    import pytest

    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()

    class Provider:
        def stream(self, messages, config, cancel):
            started.set()
            release.wait(12)
            yield "obsolete"

    service = ChatService(provider=Provider())
    results = []
    service.finished.connect(lambda *args: results.append(args))
    service.send([], config())
    assert started.wait(12)
    try:
        assert service.pause() is False
        assert service.resume() is False
        with pytest.raises(RuntimeError, match="ai_requests_not_accepting"):
            service.send([], config())
        release.set()
        pump_until(app, lambda: service.is_drained)
        assert results == []
        assert service.resume() is True
        assert service.accepting
        assert service.shutdown() is True
        assert service.resume() is False
    finally:
        release.set()
        service.shutdown()
        pump_until(app, lambda: service.is_drained)


def test_deleted_feature_parent_retains_and_cancels_running_request():
    from PySide6.QtCore import QCoreApplication, QEvent, QObject
    from shiboken6 import isValid

    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()

    class Provider:
        def stream(self, messages, config, cancel):
            started.set()
            release.wait(12)
            yield "late"

    parent = QObject()
    service = ChatService(provider=Provider(), parent=parent)
    service.send([], config())
    assert started.wait(12)
    worker = service._worker
    try:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(service)
        assert worker.cancel.is_set()
        release.set()
        pump_until(app, lambda: service.is_drained)
        assert not worker.isRunning()
    finally:
        release.set()
        worker.wait(12000)
        app.processEvents()


def test_session_drain_is_nonblocking_and_fences_every_ai_root(tmp_path, monkeypatch):
    from pet.chat import session_store as ss

    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    writes = []

    def write(path, payload):
        assert threading.get_ident() != gui_thread
        entered.set()
        release.wait(12)
        ss._atomic_write(path, payload)
        writes.append(path)

    registry = ss._WriterRegistry(writer_factory=lambda root: ss._AsyncWriter(root, write=write))
    monkeypatch.setattr(ss, "_registry", registry)
    store = ss.SessionStore(tmp_path, "fixture")
    session = store.create("fixture", "fixture", "generated")
    store.save(session)
    assert entered.wait(12)
    handle = None
    try:
        handle = ss.begin_session_drain()
        assert not handle.completed
        assert store.save(session) is False
        assert ss.SessionStore(tmp_path, "new-root").save(session) is False
        assert handle.resume() is False
        heartbeat = []
        QTimer.singleShot(0, lambda: (heartbeat.append(True), release.set()))
        pump_until(app, lambda: handle.completed)
        assert heartbeat and handle.status == "completed"
        assert writes
        assert ss.begin_session_drain() is handle
        assert handle.resume() is True
        assert store.save(session) is True
        assert store.flush(12)
    finally:
        release.set()
        if handle is not None:
            handle._done.wait(12)
        registry.close_all()


def test_session_drain_write_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    from pet.chat import session_store as ss

    app = QApplication.instance() or QApplication([])

    def write(path, payload):
        raise PermissionError("generated boundary failure")

    registry = ss._WriterRegistry(writer_factory=lambda root: ss._AsyncWriter(root, write=write))
    monkeypatch.setattr(ss, "_registry", registry)
    store = ss.SessionStore(tmp_path)
    session = store.create("fixture", "fixture", "generated")
    assert store.save(session)
    handle = ss.begin_session_drain()
    pump_until(app, lambda: handle.completed)
    assert handle.status == "failed"
    assert handle.resume() is False
    assert store.save(session) is False
    assert registry.get_writer(store.root) is not None
    registry.close_all()


def test_session_drain_close_exception_still_joins_all_owned_writers(tmp_path, monkeypatch):
    from pet.chat import session_store as ss

    app = QApplication.instance() or QApplication([])
    entered, release, second_closing = threading.Event(), threading.Event(), threading.Event()

    def write(path, payload):
        if path.parent.parent.name == "sessions-second":
            entered.set()
            release.wait(12)
        ss._atomic_write(path, payload)

    registry = ss._WriterRegistry(writer_factory=lambda root: ss._AsyncWriter(root, write=write))
    monkeypatch.setattr(ss, "_registry", registry)
    one, two = ss.SessionStore(tmp_path, "first"), ss.SessionStore(tmp_path, "second")
    session = one.create("fixture", "fixture", "generated")
    assert one.save(session) and two.save(session)
    assert entered.wait(12)
    first_writer, second_writer = registry.get_writer(one.root), registry.get_writer(two.root)
    first_close, second_close = first_writer.close, second_writer.close

    def broken_close(timeout=10):
        raise OSError("generated close boundary error")

    def tracked_close(timeout=10):
        second_closing.set()
        return second_close(timeout)

    monkeypatch.setattr(first_writer, "close", broken_close)
    monkeypatch.setattr(second_writer, "close", tracked_close)
    handle = ss.begin_session_drain()
    try:
        pump_until(app, lambda: handle.completed or second_closing.is_set())
        assert not handle.completed, "a close exception is not proof that all writers stopped"
        release.set()
        pump_until(app, lambda: handle.completed)
        assert handle.status == "failed"
        assert not first_writer._thread.is_alive() and not second_writer._thread.is_alive()
    finally:
        release.set()
        first_close(12)
        second_close(12)
        handle._done.wait(12)
        monkeypatch.setattr(first_writer, "close", first_close)
        monkeypatch.setattr(second_writer, "close", second_close)
        registry.close_all()


def test_session_drain_resume_remains_retryable_during_another_closer(tmp_path, monkeypatch):
    from pet.chat import session_store as ss

    registry = ss._WriterRegistry()
    monkeypatch.setattr(ss, "_registry", registry)
    store = ss.SessionStore(tmp_path)
    assert store.save(store.create("fixture", "fixture", "generated"))
    handle = ss.begin_session_drain()
    assert handle._done.wait(12) and handle.status == "completed"
    writer = registry.get_writer(store.root)
    original_close = writer.close
    entered, release = threading.Event(), threading.Event()

    def delayed_close(timeout=10):
        entered.set()
        release.wait(12)
        return original_close(timeout)

    monkeypatch.setattr(writer, "close", delayed_close)
    closer = threading.Thread(target=registry.close_all)
    closer.start()
    try:
        assert entered.wait(12)
        assert handle.resume() is False
        assert ss.begin_session_drain() is handle, "retry must retain the accepted drain handle"
        release.set()
        closer.join(12)
        assert not closer.is_alive()
        assert handle.resume() is True
    finally:
        release.set()
        closer.join(12)
        registry.close_all()
