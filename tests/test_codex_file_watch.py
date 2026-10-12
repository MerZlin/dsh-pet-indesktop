import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Slot, Qt
from tests.codex_helpers import bridge, poll_until
from pet.agent_link import SnapshotAgentMonitor
import json


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_file_notification_without_polling(app, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    root = tmp_path / "bridge"
    log = tmp_path / "r.jsonl"
    log.touch()
    payload = dict(session_id="s", turn_id="t", transcript_path=str(log))
    bridge.record(dict(payload, hook_event_name="UserPromptSubmit"), root)

    class Receiver(QObject):
        def __init__(self):
            super().__init__()
            self.values = []

        @Slot(str, object, int)
        def receive(self, key, summary, gen):
            self.values.append(summary)

    receiver = Receiver()
    monitor = SnapshotAgentMonitor("codex", tmp_path, root / "state.json")
    monitor._POLL_INTERVAL_S = 60
    monitor.snapshot_event.connect(receiver.receive, Qt.ConnectionType.QueuedConnection)
    try:
        assert monitor.start()
        poll_until(lambda: any(s.get("detail") for s in receiver.values), timeout=5000)
        with log.open("a") as f:
            f.write(
                json.dumps(dict(type="response_item", payload=dict(type="message", role="assistant", phase="commentary", content=[dict(text="文件通知进展")])))
                + "\n"
            )
        poll_until(lambda: any(s.get("detail", {}).get("text") == "文件通知进展" for s in receiver.values), timeout=5000)
        bridge.record(dict(payload, hook_event_name="Stop"), root)
        poll_until(lambda: any(s.get("ended") for s in receiver.values), timeout=5000)
    finally:
        monitor.stop()
    assert not monitor._worker.is_alive()


def test_watch_rearms_missing_and_replaced_file(app, tmp_path):
    import threading, subprocess, sys
    from pet.agent_file_watch import AgentFileWatch

    wake = threading.Event()
    watch = AgentFileWatch(wake)
    target = tmp_path / "new" / "state.json"
    watch.generation = 1
    watch.update_paths([target], 1)
    wake.clear()
    script = "from pathlib import Path; p=Path(__import__('sys').argv[1]);p.parent.mkdir(exist_ok=True);p.write_text('{}')"
    subprocess.run([sys.executable, "-c", script, str(target)], check=True)
    poll_until(wake.is_set, timeout=5000)
    assert str(target) in watch.watcher.files()
    wake.clear()
    # Windows indexers/antivirus can briefly hold the newly created file.
    # Retry only that OS sharing failure; watcher delivery still has its own
    # deadline and must observe the actual successful atomic replacement.
    script = """
from pathlib import Path
import sys, time, threading
p = Path(sys.argv[1]); q = p.with_suffix('.tmp'); q.write_text('[]')
deadline = time.monotonic() + 10
while True:
    try:
        q.replace(p)
        break
    except PermissionError:
        if time.monotonic() >= deadline:
            raise
        threading.Event().wait(0.02)
"""
    subprocess.run([sys.executable, "-c", script, str(target)], check=True)
    poll_until(wake.is_set, timeout=5000)
    assert str(target) in watch.watcher.files()
    watch.stop()
    wake.clear()
    target.write_text("stopped")
    assert watch.watcher is None
    watch.deleteLater()


def test_monitor_destroy_releases_worker(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monitor = SnapshotAgentMonitor("codex", tmp_path, tmp_path / "state.json")
    assert monitor._file_watch.watcher is None
    monitor.start()
    worker = monitor._worker
    # A regression test must not flush other tests' pending QObject deletion.
    import shiboken6

    unrelated = QObject()
    unrelated.deleteLater()
    monitor.deleteLater()
    QCoreApplication.sendPostedEvents(monitor, QEvent.Type.DeferredDelete)
    assert shiboken6.isValid(unrelated)
    poll_until(lambda: not worker.is_alive(), timeout=5000)


def test_missing_snapshot_does_not_keep_draining_backlog(tmp_path):
    from pet.agent_snapshot import SnapshotReader

    log = tmp_path / "r.jsonl"
    log.touch()
    bridge.record(dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t", transcript_path=str(log)), tmp_path)
    reader = SnapshotReader(tmp_path / "state.json")
    reader.read()
    with log.open("a") as f:
        f.write(('{"ignored":true}\n') * 10000)
    reader.read()
    assert reader.has_backlog()
    (tmp_path / "state.json").unlink()
    assert reader.read()["unknown"] == 1
    assert not reader.has_backlog()
    assert reader.expiry_delay() is None
