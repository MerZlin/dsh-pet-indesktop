from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from petcore.net.dsh import DshClient, DshError
from petcore.platform import dsh_install
from petcore.ui.chat.dsh_view import DshView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _state(tmp_path: Path, port: int, token: str = "tok") -> Path:
    path = tmp_path / "pet-channel.json"
    path.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "host": "127.0.0.1",
                "port": port,
                "origin": f"http://127.0.0.1:{port}",
                "token": token,
                "pid": 123,
            }
        ),
        encoding="utf-8",
    )
    return path


class _Handler(BaseHTTPRequestHandler):
    token = "tok"
    prompts: list[dict] = []
    events = [
        {"kind": "hello", "version": "0.1.0"},
        {"kind": "assistant", "text": "你好"},
    ]

    def _json(self, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/pet/ping"):
            self._json({"ok": True, "plugin": "dsh-pet-bridge", "version": "0.1.0"})
            return
        if self.path.startswith("/pet/history"):
            self._json({"ok": True, "items": [{"role": "user", "text": "历史"}]})
            return
        if self.path.startswith("/pet/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for frame in type(self).events:
                self.wfile.write(
                    ("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode(
                        "utf-8"
                    )
                )
                self.wfile.flush()
            return
        self._json({"ok": False})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).prompts.append(body)
        self._json({"ok": True})

    def log_message(self, *args):
        return


@pytest.fixture
def dsh_server(tmp_path: Path):
    _Handler.prompts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = _state(tmp_path, server.server_address[1])
    yield server.server_address[1], state
    server.shutdown()
    server.server_close()


def test_client_reads_info_and_rejects_remote_origin(tmp_path: Path) -> None:
    state = _state(tmp_path, 6791)
    client = DshClient(state_file=state)
    info = client.info()
    assert info is not None and info.port == 6791 and info.token

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"origin": "http://192.168.1.5:6791", "token": "x"}),
        encoding="utf-8",
    )
    assert DshClient(state_file=bad).info() is None


def test_client_ping_prompt_history_and_events(dsh_server) -> None:
    port, state = dsh_server
    client = DshClient(state_file=state)
    assert client.ping()["plugin"] == "dsh-pet-bridge"
    assert client.prompt("你好")["ok"] is True
    assert _Handler.prompts == [{"text": "你好"}]
    assert client.history(5) == [{"role": "user", "text": "历史"}]

    stop = threading.Event()
    frames = []
    stream = client.events(stop, idle_sleep=0.1)
    try:
        frames.append(next(stream))
        frames.append(next(stream))
        frames.append(next(stream))
    finally:
        stop.set()
        stream.close()
    assert frames[0]["kind"] == "connection" and frames[0]["ok"] is True
    assert frames[1]["kind"] == "hello"
    assert frames[2]["text"] == "你好"


def test_client_missing_state_is_not_running(tmp_path: Path) -> None:
    client = DshClient(state_file=tmp_path / "missing.json")
    assert client.info() is None
    with pytest.raises(DshError) as caught:
        client.ping()
    assert caught.value.reason == "not_running"


# ---------------------------------------------------------------- 视图


def test_view_without_client_shows_install_hint(app) -> None:
    view = DshView(None)
    try:
        assert "未安装" in view.status.text()
        assert view.send_button.isEnabled() is False
    finally:
        view.deleteLater()


def test_view_renders_frames_and_context(app) -> None:
    view = DshView(None)
    try:
        view._on_connected(True, "0.1.0")
        assert "已连接" in view.status.text()
        view._on_frame({"kind": "user", "text": "你好"})
        view._on_frame({"kind": "assistant", "text": "在呢"})
        view._on_frame(
            {
                "kind": "tool-call",
                "callId": "c1",
                "tool": "bash",
                "args": '{"command":"ls"}',
            }
        )
        view._on_frame({"kind": "tool-result", "callId": "c1", "ok": True, "text": "a"})
        assert view.timeline_layout.count() >= 4
        texts = []
        view.context_ready.connect(texts.append)
        view._emit_context()
        assert texts and "DSH" in texts[0]
    finally:
        view.deleteLater()


# ---------------------------------------------------------------- 安装器


def _plugin_root(tmp_path: Path) -> Path:
    root = tmp_path / "integrations"
    for name in dsh_install.PLUGIN_NAMES:
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "index.js").write_text("export const x = 1\n", encoding="utf-8")
    return root


def test_installer_updates_profile_and_backs_up(tmp_path: Path) -> None:
    home = tmp_path / "home"
    profile = dsh_install.profile_dir(home)
    profile.mkdir(parents=True)
    package = profile / "package.json"
    package.write_text(
        json.dumps(
            {
                "dependencies": {"dsh-pet-channel": "file:old"},
                "dsh": {"profile": {"bundles": ["dsh-pet-channel"]}},
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    result = dsh_install.install(
        home, plugin_root_dir=_plugin_root(tmp_path), runner=runner
    )
    assert result.ok is True, result
    doc = json.loads(package.read_text(encoding="utf-8"))
    assert doc["dependencies"]["@dsh-pet/bridge"].startswith("file:")
    assert "dsh-pet-channel" not in doc["dependencies"]
    assert "@dsh-pet/bridge" in doc["dsh"]["profile"]["bundles"]
    assert "dsh-pet-channel" not in doc["dsh"]["profile"]["bundles"]
    assert Path(result.backup).is_file()
    assert (home / ".dsh" / "local-plugins" / "dsh-pet-bridge" / "index.js").is_file()
    assert calls


def test_installer_reports_missing_profile(tmp_path: Path) -> None:
    result = dsh_install.install(
        tmp_path / "home",
        plugin_root_dir=_plugin_root(tmp_path),
        runner=lambda *a, **k: SimpleNamespace(returncode=0),
    )
    assert result.ok is False
    assert result.reason == "no_profile"


def test_installer_rolls_back_profile_when_link_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    profile = dsh_install.profile_dir(home)
    profile.mkdir(parents=True)
    package = profile / "package.json"
    package.write_text(
        json.dumps({"dependencies": {}, "dsh": {"profile": {"bundles": []}}}),
        encoding="utf-8",
    )
    result = dsh_install.install(
        home,
        plugin_root_dir=_plugin_root(tmp_path),
        runner=lambda *a, **k: SimpleNamespace(returncode=1),
    )
    assert result.ok is False
    assert result.reason == "link_failed"
    doc = json.loads(package.read_text(encoding="utf-8"))
    assert doc["dependencies"] == {}
    assert doc["dsh"]["profile"]["bundles"] == []


def _junction(link: Path, target: Path) -> bool:
    link.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return int(done.returncode or 0) == 0


def test_installer_strips_legacy_patch_entries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    profile = dsh_install.profile_dir(home)
    profile.mkdir(parents=True)
    package = profile / "package.json"
    package.write_text(
        json.dumps(
            {
                "dependencies": {"dsh-pet-channel": "file:old"},
                "dsh": {"profile": {"bundles": ["dsh-pet-channel"]}},
            }
        ),
        encoding="utf-8",
    )
    patch = profile / "cordis.patch.yml"
    patch.write_text(
        "# keep me\n"
        "- id: ui-chat\n"
        '  name: "@deepseek-ai/dsh-client-ui-chat"\n'
        "- insert:\n"
        "    - id: pet-channel\n"
        "      name: dsh-pet-channel\n"
        "      config:\n"
        "        port: 6791\n"
        "    - id: keeper\n"
        "      name: some-other-plugin\n"
        "- id: tail\n"
        "  name: tail-plugin\n",
        encoding="utf-8",
    )
    result = dsh_install.install(
        home,
        plugin_root_dir=_plugin_root(tmp_path),
        runner=lambda *a, **k: SimpleNamespace(returncode=0),
    )
    assert result.ok is True, result
    text = patch.read_text(encoding="utf-8")
    assert "dsh-pet-channel" not in text
    assert "keeper" in text and "ui-chat" in text and "# keep me" in text
    assert list(profile.glob("cordis.patch.yml.dsh-pet-backup-*"))


def test_installer_repoints_stale_bridge_link_and_drops_legacy_link(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("junction semantics are Windows-only")
    home = tmp_path / "home"
    profile = dsh_install.profile_dir(home)
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps({"dependencies": {}, "dsh": {"profile": {"bundles": []}}}),
        encoding="utf-8",
    )
    stale = tmp_path / "stale-bridge"
    stale.mkdir()
    (stale / "index.js").write_text("stale\n", encoding="utf-8")
    link = profile / "node_modules" / "@dsh-pet" / "bridge"
    if not _junction(link, stale):
        pytest.skip("cannot create junction")
    legacy_target = tmp_path / "stale-channel"
    legacy_target.mkdir()
    legacy = profile / "node_modules" / "dsh-pet-channel"
    if not _junction(legacy, legacy_target):
        pytest.skip("cannot create junction")

    result = dsh_install.install(
        home, plugin_root_dir=_plugin_root(tmp_path), runner=subprocess.run
    )
    assert result.ok is True, result
    target = home / ".dsh" / "local-plugins" / "dsh-pet-bridge"
    assert Path(os.path.realpath(link)) == Path(os.path.realpath(target))
    assert not legacy.exists() and not legacy.is_junction()


# ---------------------------------------------------------------- 审批状态机 / 共享连接


def test_view_approval_reaches_terminal_state(app) -> None:
    view = DshView(None)
    try:
        view._on_frame(
            {
                "kind": "approval-request",
                "id": "a1",
                "tool": "bash",
                "title": "要跑一下 bash",
                "args": '{"command":"rm -rf /tmp/x"}',
            }
        )
        assert "等待审批" in view.status.text()
        card, allow, deny = view._approvals["a1"]
        assert allow.isEnabled() and deny.isEnabled()

        view._on_frame({"kind": "approval-resolved", "id": "a1", "allow": True})
        assert "审批已允许" in view.status.text()
        assert allow.isEnabled() is False and deny.isEnabled() is False
    finally:
        view.deleteLater()


def test_view_duplicate_approval_id_does_not_stack_cards(app) -> None:
    view = DshView(None)
    try:
        frame = {"kind": "approval-request", "id": "a1", "tool": "bash", "args": "{}"}
        view._on_frame(frame)
        before = view.timeline_layout.count()
        view._on_frame(frame)
        assert view.timeline_layout.count() == before
        assert len(view._approvals) == 1
    finally:
        view.deleteLater()


def test_view_failed_answer_reopens_buttons(app) -> None:
    view = DshView(None)
    try:
        view._on_frame(
            {"kind": "approval-request", "id": "a1", "tool": "bash", "args": "{}"}
        )
        card, allow, deny = view._approvals["a1"]
        view._answer("a1", True)
        assert allow.isEnabled() is False and deny.isEnabled() is False
        view._on_failed("protocol")
        assert allow.isEnabled() is True and deny.isEnabled() is True
    finally:
        view.deleteLater()


class _FakeDshClient:
    def __init__(self) -> None:
        self.connections = 0
        self.answers: list[tuple[str, bool]] = []
        self.sent: list[str] = []

    def ping(self):
        return {"version": "test"}

    def history(self, limit=200, **kwargs):
        return []

    def events(self, stop, *, idle_sleep=1.0, since=0):
        type(self).connections = self.connections + 1
        self.connections += 1
        yield {"kind": "hello", "version": "test", "bootId": "boot"}
        while not stop.wait(0.05):
            pass

    def sessions(self):
        return []

    def new_session(self):
        return "session-new"

    def approvals(self):
        return []

    def answer(self, request_id, allow):
        self.answers.append((request_id, bool(allow)))
        return {"ok": True}

    def prompt(self, text, *, context=None):
        self.sent.append(str(text))
        return {"ok": True}

    def cancel(self):
        return {"ok": True}


def test_hub_shares_one_stream_between_two_views(app) -> None:
    from petcore.ui.chat.dsh_controller import DshHub

    client = _FakeDshClient()
    hub = DshHub(client)
    first = DshView(client, hub=hub)
    second = DshView(client, hub=hub)
    try:
        first.activate()
        second.activate()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.connections < 1:
            QApplication.processEvents()
            time.sleep(0.01)
        assert client.connections == 1
        first.deactivate()
        second.deactivate()
    finally:
        hub.shutdown()
        first.deleteLater()
        second.deleteLater()


def test_history_replay_clears_pending_approval_state(app) -> None:
    view = DshView(None)
    try:
        frame = {"kind": "approval-request", "id": "a1", "tool": "bash", "args": "{}"}
        view._on_frame(frame)
        assert "a1" in view._approvals
        view._clear_rows()
        assert view._approvals == {}
        view._on_frame(frame)
        assert "a1" in view._approvals, "重连后同一审批必须能重新建卡"
    finally:
        view.deleteLater()


def test_hub_replays_connected_state_to_a_second_view(app) -> None:
    from petcore.ui.chat.dsh_controller import DshHub

    client = _FakeDshClient()
    hub = DshHub(client)
    first = DshView(client, hub=hub)
    second = DshView(client, hub=hub)
    try:
        first.activate()
        hub._on_connected(True, "0.1.0")
        second.activate()
        assert "已连接" in second.status.text()
    finally:
        hub.shutdown()
        first.deleteLater()
        second.deleteLater()


class _SlowCloseClient(_FakeDshClient):
    """``close_active`` 会卡住：真机现场是 urllib 的 response.close() 被读线程挡住。"""

    def __init__(self, delay: float = 1.0) -> None:
        super().__init__()
        self.closed = 0
        self.delay = delay

    def close_active(self):
        self.closed += 1
        time.sleep(self.delay)


def test_pending_approval_survives_without_a_view_and_replays(app) -> None:
    """审批是 Hub 级状态：视图没建/停在 AI 视图时也必须记住并补画。

    真机事故：00:34:43 闸门挂起（pending=1），用户没看到卡，00:44:43 自动作废。
    """
    from petcore.ui.chat.dsh_controller import DshHub

    client = _FakeDshClient()
    hub = DshHub(client)
    try:
        hub._relay_frame(
            {"kind": "approval-request", "id": "a7", "tool": "pwsh", "args": "{}"}
        )
        assert [item["id"] for item in hub.pending_approvals()] == ["a7"]
        view = DshView(client, hub=hub)
        try:
            assert "a7" in view._approvals
            hub._relay_frame({"kind": "approval-resolved", "id": "a7", "allow": True})
            assert hub.pending_approvals() == []
        finally:
            view.deleteLater()
    finally:
        hub.shutdown()


def test_history_replay_restores_still_pending_approvals(app) -> None:
    from petcore.ui.chat.dsh_controller import DshHub

    client = _FakeDshClient()
    hub = DshHub(client)
    view = DshView(client, hub=hub)
    try:
        hub._relay_frame(
            {"kind": "approval-request", "id": "a8", "tool": "pwsh", "args": "{}"}
        )
        # 历史回放会重建整条时间线；待审批必须跟着回来（桥不会重放审批帧）。
        view._on_history([{"role": "user", "text": "你好"}])
        assert "a8" in view._approvals
    finally:
        hub.shutdown()
        view.deleteLater()


def test_stop_never_closes_the_stream_on_the_calling_thread(app) -> None:
    """GUI 线程上关 SSE 会让「DSH→AI」切换卡住（真机主因）。"""
    from petcore.ui.chat.dsh_controller import DshController

    client = _SlowCloseClient(delay=1.0)
    controller = DshController(client)
    controller.start()
    try:
        started = time.monotonic()
        controller.stop()
        elapsed = time.monotonic() - started
        assert elapsed < 0.2, f"stop() 阻塞了 {elapsed:.2f}s"
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.closed == 0:
            QApplication.processEvents()
            time.sleep(0.02)
        assert client.closed == 1
    finally:
        controller.shutdown()


def test_persistent_hub_keeps_the_stream_after_views_release(app) -> None:
    from petcore.ui.chat.dsh_controller import DshHub

    client = _FakeDshClient()
    hub = DshHub(client)
    view = DshView(client, hub=hub)
    try:
        hub.acquire_persistent()
        view.activate()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.connections < 1:
            QApplication.processEvents()
            time.sleep(0.01)
        view.deactivate()
        QApplication.processEvents()
        time.sleep(0.1)
        QApplication.processEvents()
        assert client.connections == 1, "常驻订阅不该随视图关闭而断流"
        hub.release_persistent()
        QApplication.processEvents()
    finally:
        hub.shutdown()
        view.deleteLater()
