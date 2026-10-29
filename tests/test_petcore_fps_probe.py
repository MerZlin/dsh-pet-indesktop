# -*- coding: utf-8 -*-
"""petcore 帧率探针（`petcore/platform/fps.py` / `fps_permission.py`）的单测。

全部离线：不碰真实 ETW、不起真实 PresentMon、不等真实 1 秒。子进程面用
``popen`` 替身（真读线程 + 假管道文本），只为验证"起停/复用/收口"三件事。
"""

from __future__ import annotations

import base64
import io
import threading
import time
from types import SimpleNamespace

import pytest

from petcore.platform import fps, fps_permission

HEADER = (
    "Application,ProcessID,SwapChainAddress,MsBetweenPresents,"
    "MsBetweenDisplayChange,TimeInMs"
)
PID = 4242


def _csv(
    fps_value: float,
    *,
    frames: int = 20,
    pid: int = PID,
    swapchain: str = "0x1",
    displayed: float | None = None,
) -> str:
    """构造一段 PresentMon 默认指标集输出（一行一帧）。"""
    interval = 1000.0 / float(fps_value)
    rows = [HEADER]
    for index in range(frames):
        shown = "NA" if displayed is None else f"{displayed:.3f}"
        rows.append(
            f"game.exe,{pid},{swapchain},{interval:.3f},{shown},{index * interval:.3f}"
        )
    return "\n".join(rows) + "\n"


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- 1 秒事件窗
def test_window_needs_two_frames():
    assert fps.window_fps([(0.0, 16.0, 1, "A")]) is None


def test_window_needs_min_span():
    # 两个样本但跨度 99ms < MIN_SPAN_SECONDS(100ms)：不给读数。
    assert fps.window_fps([(0.0, 50.0, 1, "A"), (99.0, 50.0, 1, "A")]) is None
    assert fps.window_fps([(0.0, 50.0, 1, "A"), (100.0, 50.0, 1, "A")]) == pytest.approx(
        20.0
    )


def test_window_computes_fps_from_mean_interval():
    events = [(index * 16.667, 16.667, 1, "A") for index in range(20)]
    assert fps.window_fps(events) == pytest.approx(60.0, rel=1e-3)


def test_window_is_empty_on_no_input():
    assert fps.window_fps([]) is None
    assert fps.FpsWindow().reading().fps is None


def test_window_keeps_only_live_chains():
    window = fps.FpsWindow()
    for index in range(10):
        window.add_event(
            fps.FrameEvent(time_ms=index * 8.0, interval_ms=8.0, pid=1, swapchain="A")
        )
    # 2 秒后另一条链开始：A 的样本全部落在 1 秒窗之外，不参与主值。
    for index in range(4):
        window.add_event(
            fps.FrameEvent(
                time_ms=2000.0 + index * 40.0, interval_ms=40.0, pid=2, swapchain="B"
            )
        )
    reading = window.reading()
    assert reading.pid == 2
    assert reading.fps == pytest.approx(25.0)


def test_window_takes_the_busiest_chain_without_summing():
    window = fps.FpsWindow()
    for index in range(15):
        window.add_event(
            fps.FrameEvent(time_ms=index * 8.0, interval_ms=8.0, pid=1, swapchain="A")
        )
    for index in range(5):
        window.add_event(
            fps.FrameEvent(
                time_ms=index * 33.333, interval_ms=33.333, pid=1, swapchain="B"
            )
        )
    reading = window.reading()
    assert reading.pid == 1
    assert reading.fps == pytest.approx(125.0)
    assert len(reading.chains) == 2


def test_window_parses_csv_lines_and_displayed_rate():
    lines = _csv(60.0, displayed=1000.0 / 60.0).splitlines()
    window = fps.FpsWindow()
    for line in lines:
        window.add_line(line)
    reading = window.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-3)
    assert reading.displayed_fps == pytest.approx(60.0, rel=1e-3)
    assert reading.clock == "event"
    assert window.columns is not None
    assert window.clock_column == "TimeInMs"


def test_window_drops_na_displayed_values():
    lines = _csv(60.0).splitlines()
    window = fps.FpsWindow()
    for line in lines:
        window.add_line(line)
    reading = window.reading()
    assert reading.fps == pytest.approx(60.0, rel=1e-3)
    assert reading.displayed_fps is None


def test_header_missing_columns_is_reported():
    problem = fps.header_problem(
        "Application,ProcessID,SwapChainAddress,FrameTime,TimeInMs"
    )
    assert "MsBetweenPresents" in problem
    assert "v2_metrics" in problem
    assert fps.header_problem(HEADER) == ""


def test_overflow_is_said_out_loud():
    assert fps.format_fps(12000) == "9999+"
    assert fps.format_fps(9999) == "9999"
    assert fps.format_fps(None) == "--"
    assert fps.MAX_TEXT == "9999+"


# ---------------------------------------------------------------- 后端路径
def test_backend_path_resolves_bundle_root_and_meipass(tmp_path, monkeypatch):
    source = fps.backend_path(frozen=False, bundle_root=tmp_path)
    assert source == tmp_path / "resources" / "presentmon" / fps.BACKEND_EXE_NAME
    monkeypatch.setattr(fps.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert fps.backend_path(frozen=True) == source


def test_missing_backend_is_unsupported(tmp_path):
    probe = fps.FpsProbe(
        target_pid=PID,
        target_name="game.exe",
        frozen=False,
        bundle_root=tmp_path,
        platform_supported=lambda: True,
    )
    try:
        assert probe.start() is False
        sample = probe.snapshot()
        assert sample.state == fps.STATE_UNSUPPORTED
        assert "PresentMon" in sample.error_reason
        assert sample.fps is None
        assert sample.pid == PID
    finally:
        probe.close()


def test_missing_backend_is_reported_before_start(tmp_path):
    probe = fps.FpsProbe(
        frozen=False, bundle_root=tmp_path, platform_supported=lambda: True
    )
    try:
        assert probe.snapshot().state == fps.STATE_UNSUPPORTED
        assert "PresentMon" in probe.snapshot().error_reason
    finally:
        probe.close()


def test_frozen_without_meipass_does_not_fall_back_to_the_source_tree(monkeypatch):
    monkeypatch.delattr(fps.sys, "_MEIPASS", raising=False)
    probe = fps.FpsProbe(frozen=True, platform_supported=lambda: True)
    try:
        assert probe.backend is None
        assert probe.start() is False
        sample = probe.snapshot()
        assert sample.state == fps.STATE_UNSUPPORTED
        assert "sys._MEIPASS" in sample.error_reason
    finally:
        probe.close()


# ---------------------------------------------------------------- 命令行
def test_capture_arguments_contain_required_flags():
    name = fps.new_session_name()
    args = fps.build_capture_args(session_name=name, pid=PID)
    assert args[:2] == ["--session_name", name]
    for flag in fps.CAPTURE_FLAGS:
        assert flag in args
    assert args[args.index("--process_id") + 1] == str(PID)


def test_capture_arguments_never_contain_forbidden_flags():
    args = fps.build_capture_args(session_name=fps.new_session_name(), pid=PID)
    for flag in fps.FORBIDDEN_ARGS:
        assert flag not in args
    with pytest.raises(ValueError):
        fps.build_capture_args(session_name="")
    with pytest.raises(ValueError):
        fps.build_capture_args(session_name=fps.new_session_name())


def test_process_name_mode_is_supported():
    args = fps.build_capture_args(
        session_name=fps.new_session_name(), process_name="game.exe"
    )
    assert args[args.index("--process_name") + 1] == "game.exe"
    assert "--process_id" not in args


def test_session_names_are_unique():
    names = {fps.new_session_name() for _ in range(50)}
    assert len(names) == 50
    assert all(name.startswith(fps.SESSION_NAME_PREFIX) for name in names)


def test_terminate_arguments_use_official_switch():
    name = fps.new_session_name()
    assert fps.build_terminate_args(name) == [
        "--session_name",
        name,
        "--terminate_existing_session",
    ]


def test_permission_failure_is_terminal_and_actionable():
    state, reason = fps.classify_failure(6, "Error: access denied")
    assert state == fps.STATE_ERROR
    assert "access denied" in reason
    assert "Performance Log Users" in reason
    assert fps.is_terminal_failure(6, "")
    # 终止命令没找到会话 = 本来就已经停了，不是错误。
    assert fps.classify_failure(7, "") == (fps.STATE_OK, "")
    assert fps.classify_failure(0, "") == (fps.STATE_OK, "")


# ---------------------------------------------------------------- 权限模块
def test_permission_is_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(fps_permission.sys, "platform", "linux")
    assert fps_permission.check() == fps_permission.STATUS_UNSUPPORTED
    assert fps_permission.request_access().outcome == "unsupported"


def test_permission_is_unsupported_when_token_api_fails(monkeypatch):
    monkeypatch.setattr(fps_permission, "is_supported", lambda: True)
    monkeypatch.setattr(fps_permission, "token_has_group", lambda: None)
    assert fps_permission.check() == fps_permission.STATUS_UNSUPPORTED


def test_permission_status_uses_token_then_account(monkeypatch):
    monkeypatch.setattr(fps_permission, "is_supported", lambda: True)
    assert (
        fps_permission.check(
            token_member=lambda: True, account_member=lambda: False
        )
        == fps_permission.STATUS_ACTIVE
    )
    assert (
        fps_permission.check(
            token_member=lambda: False, account_member=lambda: True
        )
        == fps_permission.STATUS_PENDING
    )
    assert (
        fps_permission.check(
            token_member=lambda: False, account_member=lambda: False
        )
        == fps_permission.STATUS_MISSING
    )


def test_grant_command_is_encoded_and_targets_the_sid(monkeypatch):
    monkeypatch.setattr(fps_permission, "_state", {"granted": False})
    command = fps_permission.build_grant_command(
        "S-1-5-21-1-2-3-1001",
        powershell=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        deadline_unix=1234567890,
    )
    assert command[0].endswith("powershell.exe")
    assert "-EncodedCommand" in command
    script = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "Add-LocalGroupMember" in script
    assert fps_permission.PERF_LOG_USERS_SID in script
    assert "S-1-5-21-1-2-3-1001" in script
    assert "1234567890" in script
    with pytest.raises(ValueError):
        fps_permission.build_grant_command(
            "not-a-sid", powershell="x", deadline_unix=0
        )


def test_grant_exit_codes_are_mapped(monkeypatch):
    monkeypatch.setattr(fps_permission, "_state", {"granted": False})
    assert fps_permission.map_exit_code(0).outcome == "ok"
    assert fps_permission.granted_this_process() is True
    assert fps_permission.map_exit_code(fps_permission.EXIT_CANCELLED).outcome == "cancelled"
    assert fps_permission.map_exit_code(fps_permission.EXIT_EXPIRED).outcome == "error"
    assert fps_permission.map_exit_code(fps_permission.EXIT_ADD_FAILED).outcome == "error"
    assert fps_permission.map_exit_code(99).outcome == "error"


# ---------------------------------------------------------------- 子进程生命周期
class _FakeStream:
    """假管道：按 ``readline(limit)`` 逐行吐出预置文本。"""

    def __init__(self, text: str = ""):
        self._buffer = io.StringIO(text)
        self.closed = False

    def readline(self, limit: int = -1) -> str:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return self._buffer.readline(limit if limit and limit > 0 else -1)

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(
        self,
        argv,
        *,
        stdout_text: str = "",
        stderr_text: str = "",
        stubborn: bool = False,
        **_kwargs,
    ):
        self.argv = list(argv)
        self.stdout = _FakeStream(stdout_text)
        self.stderr = _FakeStream(stderr_text)
        self.pid = 424242
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.stubborn = stubborn
        self._handle = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.returncode = 1

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        return (None, None)


class _FakePopen:
    """替身 Popen：第一次调用是采集会话，之后是官方终止命令。"""

    def __init__(self, *, stdout_text: str = "", stderr_text: str = "", stubborn=False):
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.stubborn = stubborn
        self.calls: list[list[str]] = []
        self.procs: list[_FakeProc] = []

    def __call__(self, argv, **_kwargs):
        first = not self.calls
        proc = _FakeProc(
            argv,
            stdout_text=self.stdout_text if first else "",
            stderr_text=self.stderr_text if first else "",
            stubborn=self.stubborn if first else False,
        )
        self.calls.append(list(argv))
        self.procs.append(proc)
        return proc


def _probe(tmp_path, fake: _FakePopen, **overrides) -> fps.FpsProbe:
    backend = tmp_path / "fake-presentmon.exe"
    backend.write_bytes(b"MZ")
    options = {
        "target_pid": PID,
        "target_name": "game.exe",
        "backend": backend,
        "platform_supported": lambda: True,
        # 残留会话清扫会起真 logman 子进程：单测一律换成空实现（离线纪律）。
        "sweep_starter": lambda: None,
        "session_factory": lambda pid, path, name: fps.FpsSession(
            pid=pid,
            name="game.exe",
            backend=path,
            session_name=name,
            stop_timeout=0.05,
            terminate_timeout=0.05,
            popen=fake,
        ),
        "poll_seconds": 0.01,
        "publish_interval": 0.0,
        "stale_seconds": 3.0,
    }
    options.update(overrides)
    return fps.FpsProbe(**options)


def test_probe_reads_frames_and_reuses_one_session(tmp_path):
    fake = _FakePopen(stdout_text=_csv(60.0, displayed=1000.0 / 60.0))
    probe = _probe(tmp_path, fake)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: probe.snapshot().state == fps.STATE_OK)
        sample = probe.snapshot()
        assert sample.fps == pytest.approx(60.0, rel=0.02)
        assert sample.displayed_fps == pytest.approx(60.0, rel=0.02)
        assert sample.pid == PID
        assert sample.target == f"game.exe (pid {PID})"
        assert sample.detail["clock"] == "event"
        calls_before = len(fake.calls)
        # 重复 start 复用同一场会话（不是再开一个子进程）。
        assert probe.start() is True
        time.sleep(0.05)
        assert len(fake.calls) == calls_before
        assert probe.running()
    finally:
        probe.stop()
    assert probe.wait_stopped(3.0)
    assert probe.running() is False
    assert probe.snapshot().fps is None
    main = fake.procs[0]
    assert main.terminated
    assert main.stdout.closed and main.stderr.closed
    assert not [
        thread for thread in threading.enumerate() if thread.name.startswith(fps.THREAD_NAME)
    ]


def test_probe_without_frames_stays_sampling(tmp_path):
    fake = _FakePopen(stdout_text="")
    probe = _probe(tmp_path, fake)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) >= 1)
        sample = probe.snapshot()
        assert sample.state == fps.STATE_SAMPLING
        assert sample.fps is None
    finally:
        probe.close()


def test_probe_kills_a_stubborn_backend(tmp_path):
    fake = _FakePopen(stdout_text="", stubborn=True)
    probe = _probe(tmp_path, fake)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) >= 1)
    finally:
        probe.stop()
    assert probe.wait_stopped(3.0)
    main = fake.procs[0]
    assert main.terminated
    assert main.killed
    assert probe.running() is False


def test_target_change_retries_after_a_terminal_failure(tmp_path):
    calls: list[int] = []

    def factory(pid, _backend, _name):
        calls.append(pid)
        raise fps.FpsSessionError(
            "PresentMon failed to start trace session: access denied"
        )

    probe = _probe(tmp_path, _FakePopen(), session_factory=factory)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: probe.snapshot().state == fps.STATE_ERROR)
        assert "access denied" in probe.snapshot().error_reason
        assert len(calls) == 1
        # 显式换目标 = 显式重试：终态错误必须解除，不能永远停在上一个目标的失败上。
        probe.set_target(PID + 1, "other.exe")
        assert _wait_until(lambda: len(calls) == 2)
    finally:
        probe.close()


def test_session_drops_overlong_lines(tmp_path):
    backend = tmp_path / "fake-presentmon.exe"
    backend.write_bytes(b"MZ")
    fake = _FakePopen(stdout_text="X" * 5000 + "\n" + _csv(60.0, frames=3))
    session = fps.FpsSession(
        pid=PID,
        name="game.exe",
        backend=backend,
        session_name=fps.new_session_name(),
        stop_timeout=0.05,
        terminate_timeout=0.05,
        popen=fake,
    )
    session.start()
    collected: list[str] = []
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            collected.extend(session.drain_lines())
            if any(line.startswith("Application") for line in collected) and any(
                "game.exe" in line for line in collected
            ):
                break
            time.sleep(0.01)
        assert any(line.startswith("Application") for line in collected)
        assert any("game.exe" in line for line in collected)
        assert all(len(line) <= fps.MAX_LINE_CHARS for line in collected)
    finally:
        session.stop()
        session.close()


def test_shared_probe_is_a_process_singleton(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "_SHARED", None)
    first = fps.shared_probe(frozen=False, bundle_root=tmp_path, target_pid=PID)
    second = fps.shared_probe(frozen=False, bundle_root=tmp_path, target_pid=999)
    assert first is second
    fps.reset_shared_probe(close=False)
    third = fps.shared_probe(frozen=False, bundle_root=tmp_path, target_pid=PID)
    assert third is not first
    fps.reset_shared_probe(close=False)


# ---------------------------------------------------------------- 目标生命周期
def test_target_exit_is_remembered_and_never_reopened(tmp_path):
    """目标正常退出（``--terminate_on_proc_exit`` → 退出码 0）后不许再对同一个死 pid 开会话。"""
    fake = _FakePopen(stdout_text=_csv(60.0))
    probe = _probe(tmp_path, fake)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: probe.snapshot().state == fps.STATE_OK)
        fake.procs[0].returncode = 0  # 目标进程退出，采集进程随之收尾
        assert _wait_until(
            lambda: probe.snapshot().detail.get("target_gone") is True
        ), "目标退出必须作为机器可读的结论发布（hub 靠它决定何时重新解析前台）"
        calls = len(fake.calls)
        time.sleep(0.3)
        assert len(fake.calls) == calls, "同一个死 pid 不重开会话"
        probe.set_target(PID + 1, "other.exe")  # 只有显式换目标才解除
        assert _wait_until(lambda: len(fake.calls) > calls)
    finally:
        probe.close()


def test_set_target_is_idempotent_for_the_same_pid(tmp_path):
    """同一个 pid 反复 set_target（名字可能被重新解析）不许重置重试代次或去抖。"""
    fake = _FakePopen(stdout_text=_csv(60.0))
    probe = _probe(tmp_path, fake)
    try:
        epoch = probe._retry_epoch  # noqa: SLF001 - 显式验证「不重置代次」
        requested = probe._target_requested_at  # noqa: SLF001
        probe.set_target(PID, "renamed.exe")
        assert probe._retry_epoch == epoch  # noqa: SLF001
        assert probe._target_requested_at == requested  # noqa: SLF001
        assert probe.target_name == "renamed.exe"
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) == 1)
    finally:
        probe.close()


def test_target_change_waits_for_the_minimum_session_age(tmp_path):
    """焦点在窗口间跳来跳去时不许每秒开/关一场会话。"""
    fake = _FakePopen(stdout_text=_csv(60.0))
    probe = _probe(
        tmp_path,
        fake,
        min_session_seconds=1.0,
        target_stable_seconds=0.0,
        target_change_debounce=0.0,
    )
    try:
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) == 1)
        probe.set_target(PID + 1, "other.exe")
        time.sleep(0.2)  # 远小于 MIN_SESSION_SECONDS，慢 runner 也不会误判
        assert len(fake.calls) == 1, "会话没活满 MIN_SESSION_SECONDS：不许换会话"
        assert _wait_until(lambda: len(fake.calls) == 2, timeout=5.0)
    finally:
        probe.close()


def test_shutdown_waits_for_the_worker_within_a_bound(tmp_path):
    """退出路径有界等待 worker 收口（默认 <=1s），不留竞速的守护线程。"""
    fake = _FakePopen(stdout_text=_csv(60.0), stubborn=True)
    probe = _probe(tmp_path, fake)
    try:
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) == 1)
        started = time.monotonic()
        probe.shutdown(1.0)
        assert time.monotonic() - started <= 1.5
        assert probe.running() is False
    finally:
        probe.close()


# ---------------------------------------------------------------- 残留会话清扫
LOGMAN_OUTPUT = """\
Data Collector Set                      Type                          Status
-------------------------------------------------------------------------------
dsh-pet-fps-8000-1-deadbeef             Trace                         Running
dsh-pet-fps-4321-2-deadbeef             Trace                         Running
dsh-pet-fps-9000-3-deadbeef             Trace                         Running
NT Kernel Logger                        Trace                         Running
"""


def _logman_runner(*, stopped: list[list[str]], output: str = LOGMAN_OUTPUT, code: int = 0):
    def runner(args, timeout):
        if args and args[0] == "query":
            return SimpleNamespace(returncode=code, stdout=output, stderr="")
        stopped.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return runner


def test_logman_output_parsing_is_a_pure_function():
    """解析是纯函数：测试喂假输出，真机才起 logman。"""
    names = fps.parse_logman_sessions(LOGMAN_OUTPUT)
    assert names == [
        "dsh-pet-fps-8000-1-deadbeef",
        "dsh-pet-fps-4321-2-deadbeef",
        "dsh-pet-fps-9000-3-deadbeef",
    ]
    assert "NT Kernel Logger" not in names
    assert fps.parse_logman_sessions("") == []
    assert fps.parse_logman_sessions(None) == []
    assert fps.session_owner_pid("dsh-pet-fps-8000-1-deadbeef") == 8000
    assert fps.session_owner_pid("NT Kernel Logger") is None
    assert fps.session_owner_pid("dsh-pet-fps-bogus-1-x") is None


def test_sweep_stops_only_sessions_of_dead_owners():
    stopped: list[list[str]] = []
    count = fps.sweep_residual_sessions(
        runner=_logman_runner(stopped=stopped),
        alive=lambda pid: pid in (4321, 9000),  # 4321 = 本进程（活着），9000 也活着
        own_pid=4321,
    )
    assert count == 1
    assert stopped == [["stop", "dsh-pet-fps-8000-1-deadbeef", "-ets"]]


def test_sweep_skips_quietly_when_logman_is_unavailable(caplog):
    def runner(args, timeout):
        raise FileNotFoundError("logman.exe")

    caplog.set_level("INFO", logger="petcore.platform.fps")
    # None = 这次没查成（闸门重开，下次真起采集再试），不是「查到 0 个」。
    assert fps.sweep_residual_sessions(runner=runner, alive=lambda pid: False) is None
    assert "logman" in caplog.text


def test_sweep_gate_reopens_when_the_query_fails(monkeypatch):
    monkeypatch.setattr(fps, "sweep_supported", lambda: True)
    fps.reset_residual_sweep()
    attempts: list[int] = []

    def failing():
        attempts.append(1)
        return None

    try:
        assert fps.start_residual_sweep_once(sweeper=failing) is True
        assert _wait_until(lambda: fps._sweep_started is False)  # noqa: SLF001
        assert fps.start_residual_sweep_once(sweeper=failing) is True
        assert _wait_until(lambda: len(attempts) == 2)
    finally:
        fps.reset_residual_sweep()


def test_sweep_runs_once_per_process(monkeypatch):
    monkeypatch.setattr(fps, "sweep_supported", lambda: True)
    fps.reset_residual_sweep()
    done = threading.Event()
    calls: list[int] = []

    def sweeper():
        calls.append(1)
        done.set()
        return 0

    try:
        assert fps.start_residual_sweep_once(sweeper=sweeper) is True
        assert done.wait(2.0)
        assert fps.start_residual_sweep_once(sweeper=sweeper) is False
        assert calls == [1]
    finally:
        fps.reset_residual_sweep()


def test_sweep_is_skipped_off_windows(monkeypatch):
    monkeypatch.setattr(fps, "sweep_supported", lambda: False)
    fps.reset_residual_sweep()
    started: list[int] = []
    try:
        assert fps.start_residual_sweep_once(sweeper=lambda: started.append(1)) is False
        assert started == []
    finally:
        fps.reset_residual_sweep()


def test_probe_start_triggers_the_sweep_once(tmp_path):
    fake = _FakePopen(stdout_text=_csv(60.0))
    started: list[int] = []
    probe = _probe(tmp_path, fake, sweep_starter=lambda: started.append(1))
    try:
        assert probe.start() is True
        assert _wait_until(lambda: len(fake.calls) == 1)
        assert probe.start() is True  # 幂等：不重复触发
        assert started == [1]
    finally:
        probe.close()
