# -*- coding: utf-8 -*-
"""DeepSeek Harness 生命周期（停止/重启）：按端口反查进程、身份核验、终止与状态回报。

设计口径（这些测试就是它的守卫）：
- **宁可放过也不误杀**：端口在监听但命令行不是 dsh web → ``not-ours``，不终止任何进程；
- 反查不到属主（权限不足 / macOS 无 /proc）→ 不当成「没人跑」，不猜 PID 去杀；
- 终止后必须**确认端口不再监听**才回报 ``stopped``；
- ``restart`` 只有在停成功（或本来就没跑）时才启动，不制造第二个实例。
"""
from __future__ import annotations

import socket
import struct
import sys
from pathlib import Path

import pytest

from pet import harness_launcher as hl


@pytest.fixture(autouse=True)
def _no_real_process_actions(monkeypatch):
    """默认把**破坏性**边界打桩：任何测试都不得真的终止本机进程。

    ``listener_pids``（只读反查）不打桩——真有监听进程时它返回真实 PID 是
    无害的，而打桩会让「真实套接字能被反查到」这条用例失去意义。
    """
    monkeypatch.setattr(hl, "process_command_line", lambda pid: None)
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: None)
    monkeypatch.setattr(hl, "_terminate_process_tree", lambda pid: None)


# ------------------------------------------------------------ 表解析（真实字节）
def test_parse_windows_tcp_table_decodes_network_byte_order():
    """MIB_TCPROW_OWNER_PID：端口/IP 按网络字节序存放，必须转回主机序。"""
    port = 38080
    local_addr = struct.unpack("<I", socket.inet_aton("127.0.0.1"))[0]
    network_port = ((port & 0xFF) << 8) | ((port >> 8) & 0xFF)
    row = struct.pack("<IIIIII", 2, local_addr, network_port, 0, 0, 4242)
    buffer = struct.pack("<I", 1) + row  # 4 字节表头（dwNumEntries）+ 一整行
    assert len(buffer) == 4 + 24
    rows = hl._parse_windows_tcp_table(buffer, 0)
    assert rows == [("127.0.0.1", port, 4242)]


def test_parse_windows_tcp_table_tolerates_truncated_buffer():
    buffer = struct.pack("<I", 5) + b"\x00" * 10  # 声称 5 行，实际只够半行
    assert hl._parse_windows_tcp_table(buffer, 0) == []


def test_parse_proc_net_tcp_keeps_only_listeners():
    text = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
        "   uid  timeout inode\n"
        # 0A = LISTEN，端口 38080 = 0x94C0
        "   0: 0100007F:94C0 00000000:0000 0A 00000000:00000000 00:00000000"
        " 00000000  1000        0 12345 1 0000 100 0 0 10 0\n"
        # 01 = ESTABLISHED：不算监听
        "   1: 0100007F:94C0 0100007F:1F90 01 00000000:00000000 00:00000000"
        " 00000000  1000        0 99999 1 0000 100 0 0 10 0\n"
    )
    assert hl._parse_proc_net_tcp(text) == {38080: 12345}


# ------------------------------------------------------------ 只读反查
def test_listener_pids_finds_real_listener_socket():
    """真实监听套接字必须能反查到本进程（宽预算轮询，不赌表刷新时机）。

    反查读的是系统 TCP 表：刚 listen 完的那一瞬间表可能还没纳入该行，所以这里
    按仓库的时序测试纪律——轮询状态 + 宽预算，而不是一次性断言或固定 sleep。
    """
    import os
    import time

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert hl.is_running(port) is True, "刚 listen 的端口必须可连接"
        deadline = time.monotonic() + 2.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            pids = hl.listener_pids(port)
            if os.getpid() in pids:
                break
            time.sleep(0.02)
        if not pids:
            pytest.skip(
                f"{sys.platform} 上按端口反查监听进程不可用（已安全返回空，"
                "停止功能会走 not-running/error 的安全分支）"
            )
        assert os.getpid() in pids
    finally:
        server.close()


def test_listener_pids_returns_empty_when_nothing_listens():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    assert hl.listener_pids(port) == []


def test_find_harness_process_skips_foreign_listener(monkeypatch):
    """端口在监听但命令行不是 dsh web → 找不到 harness（宁可不认）。"""
    monkeypatch.setattr(hl, "is_running", lambda port: True)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [777])
    monkeypatch.setattr(hl, "process_command_line", lambda pid: "python.exe -m http.server")
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: r"C:\Python\python.exe")
    assert hl.find_harness_process() is None


def test_find_harness_process_accepts_dsh_web(monkeypatch):
    cmdline = r'"D:\NODEJS\node.exe" C:\Users\x\AppData\Roaming\npm/node_modules/@deepseek-ai/dsh/lib/bin.js web'
    monkeypatch.setattr(hl, "is_running", lambda port: port == 3080)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [13320] if port == 3080 else [])
    monkeypatch.setattr(hl, "process_command_line", lambda pid: cmdline)
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: r"D:\NODEJS\node.exe")
    target = hl.find_harness_process()
    assert target is not None
    assert (target.port, target.pid) == (3080, 13320)
    assert "dsh" in target.command_line


# ------------------------------------------------------------ 停止
def test_stop_harness_not_running_when_port_closed(monkeypatch):
    monkeypatch.setattr(hl, "is_running", lambda port: False)
    terminated: list[int] = []
    monkeypatch.setattr(hl, "_terminate_process_tree", terminated.append)
    status, info = hl.stop_harness()
    assert status == "not-running"
    assert "没有在运行" in info
    assert terminated == []


def test_stop_harness_refuses_foreign_listener(monkeypatch):
    """核心安全断言：命令行不匹配时**一次终止调用都不许发生**。"""
    monkeypatch.setattr(hl, "is_running", lambda port: True)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [4242])
    monkeypatch.setattr(hl, "process_command_line", lambda pid: "C:\\tools\\other-server.exe serve")
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: r"C:\tools\other-server.exe")
    terminated: list[int] = []
    monkeypatch.setattr(hl, "_terminate_process_tree", terminated.append)
    status, info = hl.stop_harness()
    assert status == "not-ours"
    assert "4242" in info and "other-server" in info
    assert terminated == [], "非 dsh 进程绝不能被终止"


def test_stop_harness_validates_every_owner_before_terminating_any(monkeypatch):
    """多持有者时先全部核验再动手：不许「杀了一个才发现另一个不是 dsh」。"""
    monkeypatch.setattr(hl, "is_running", lambda port: True)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [111, 222])
    monkeypatch.setattr(
        hl, "process_command_line",
        lambda pid: "node .../dsh/lib/bin.js web" if pid == 111 else "python -m http.server",
    )
    monkeypatch.setattr(
        hl, "_pid_image_path",
        lambda pid: "node.exe" if pid == 111 else "python.exe",
    )
    terminated: list[int] = []
    monkeypatch.setattr(hl, "_terminate_process_tree", terminated.append)
    status, info = hl.stop_harness()
    assert status == "not-ours"
    assert "222" in info
    assert terminated == [], "核验未通过前不许终止任何进程"


def test_stop_harness_reports_error_when_owner_unknown(monkeypatch):
    """端口在监听但读不到属主：不算 not-running，也不猜 PID 去杀。"""
    monkeypatch.setattr(hl, "is_running", lambda port: True)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [])
    terminated: list[int] = []
    monkeypatch.setattr(hl, "_terminate_process_tree", terminated.append)
    status, info = hl.stop_harness()
    assert status == "error"
    assert "读不到持有它的进程" in info
    assert terminated == []


def test_stop_harness_terminates_and_confirms_port_released(monkeypatch):
    state = {"listening": True}
    cmdline = "node .../@deepseek-ai/dsh/lib/bin.js web --host 127.0.0.1 --port 3080"

    def fake_is_running(port):
        return state["listening"]

    def fake_terminate(pid):
        state["listening"] = False

    monkeypatch.setattr(hl, "is_running", fake_is_running)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [13320])
    monkeypatch.setattr(hl, "process_command_line", lambda pid: cmdline)
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: r"D:\NODEJS\node.exe")
    monkeypatch.setattr(hl, "_terminate_process_tree", fake_terminate)
    status, info = hl.stop_harness()
    assert status == "stopped"
    assert "13320" in info


def test_stop_harness_reports_error_when_port_survives(monkeypatch):
    """杀完端口还在监听 → 不许报成功（用户以为关了，其实还在跑）。"""
    monkeypatch.setattr(hl, "is_running", lambda port: True)
    monkeypatch.setattr(hl, "listener_pids", lambda port: [13320])
    monkeypatch.setattr(
        hl, "process_command_line", lambda pid: "node .../dsh/lib/bin.js web --port 3080"
    )
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: "node.exe")
    monkeypatch.setattr(hl, "_terminate_process_tree", lambda pid: None)
    status, info = hl.stop_harness()
    assert status == "error"
    assert "仍在监听" in info


# ------------------------------------------------------------ 重启
def test_restart_stops_before_starting(monkeypatch):
    order: list[str] = []

    def fake_stop(port=hl.DEFAULT_PORT):
        order.append("stop")
        return "stopped", "已停止"

    def fake_launch(port=hl.DEFAULT_PORT, *, open_browser=True):
        order.append("launch")
        return "started", f"http://127.0.0.1:{port}"

    monkeypatch.setattr(hl, "stop_harness", fake_stop)
    monkeypatch.setattr(hl, "launch_harness", fake_launch)
    status, info = hl.restart_harness()
    assert status == "started"
    assert order == ["stop", "launch"]
    assert info.startswith("http://")


def test_restart_does_not_start_when_stop_refused(monkeypatch):
    """端口被非 dsh 进程占用时：不启动第二个实例，把原因原样传给用户。"""
    monkeypatch.setattr(
        hl, "stop_harness", lambda port=hl.DEFAULT_PORT: ("not-ours", "端口 3080 不是 dsh")
    )
    launched: list[str] = []
    monkeypatch.setattr(
        hl, "launch_harness",
        lambda port=hl.DEFAULT_PORT, *, open_browser=True: launched.append("x") or ("started", ""),
    )
    status, info = hl.restart_harness()
    assert status == "not-ours"
    assert "不是 dsh" in info
    assert launched == []


def test_restart_starts_when_nothing_was_running(monkeypatch):
    monkeypatch.setattr(
        hl, "stop_harness", lambda port=hl.DEFAULT_PORT: ("not-running", "本机没有在运行")
    )
    monkeypatch.setattr(
        hl, "launch_harness",
        lambda port=hl.DEFAULT_PORT, *, open_browser=True: ("started", "http://127.0.0.1:38080"),
    )
    status, _info = hl.restart_harness()
    assert status == "started"


# ------------------------------------------------------------ 身份核验细则
def test_looks_like_harness_requires_both_tokens():
    assert hl._looks_like_harness(1, "node bin.js web") is False          # 只有 web
    assert hl._looks_like_harness(1, "node dsh --version") is False       # 只有 dsh
    assert hl._looks_like_harness(1, None) is False
    assert hl._looks_like_harness(1, "") is False


def test_looks_like_harness_accepts_node_or_dsh_image(monkeypatch):
    cmdline = "node .../@deepseek-ai/dsh/lib/bin.js web"
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: r"D:\NODEJS\node.exe")
    assert hl._looks_like_harness(1, cmdline) is True
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: "/usr/bin/dsh")
    assert hl._looks_like_harness(1, cmdline) is True
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: "/usr/bin/textmate")
    assert hl._looks_like_harness(1, cmdline) is False


def test_confirm_dialog_text_names_pid_and_port(tmp_path, monkeypatch):
    """确认框必须讲清「要终止谁」——破坏性动作不接受模糊确认。"""
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    target = hl.HarnessProcess(port=3080, pid=13320, command_line="node bin.js web")

    captured: dict = {}
    real_exec = QMessageBox.exec

    def fake_exec(self):
        captured["text"] = self.text()
        captured["informative"] = self.informativeText()
        captured["buttons"] = [button.text() for button in self.buttons()]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    try:
        result = hl._confirm_harness_stop(None, target, restart=False)
    finally:
        monkeypatch.setattr(QMessageBox, "exec", real_exec)
    assert result is False  # 没有点「确认停止」按钮
    # 断言的是「用户能不能看懂要终止谁」，不赌平台窗口标题：
    # macOS 的 QMessageBox 是系统原生对话框，windowTitle() 在那边为空
    # （CI 实测 macos-latest 红、Windows/Linux 绿），标题不是语义承载点。
    assert "确认停止" in captured["text"]
    assert "3080" in captured["informative"]
    assert "13320" in captured["informative"]
    assert "确认停止" in captured["buttons"]
    assert "取消" in captured["buttons"]
    del app, tmp_path


def test_confirm_dialog_when_nothing_running(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    real_exec = QMessageBox.exec
    captured: dict = {}

    def fake_exec(self):
        captured["informative"] = self.informativeText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    try:
        hl._confirm_harness_stop(None, None, restart=True)
    finally:
        monkeypatch.setattr(QMessageBox, "exec", real_exec)
    assert "没有检测到" in captured["informative"]
    del app


def test_launch_harness_gui_actions_without_confirmation_for_start(monkeypatch):
    """start 动作不得弹确认框（旧行为保持：点一下就能用）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    calls: list[str] = []

    def fail_exec(self):  # pragma: no cover - 被调用即失败
        calls.append("dialog")
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fail_exec)
    monkeypatch.setattr(
        hl, "launch_harness",
        lambda port=hl.DEFAULT_PORT, *, open_browser=True: ("already", "http://127.0.0.1:3080"),
    )

    class _Pet:
        def show_bubble(self, text, duration=0):
            calls.append("bubble")

    hl.launch_harness_gui(_Pet(), action="start")
    # 等后台线程把 singleShot 推回 GUI 线程
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not calls:
        app.processEvents()
        time.sleep(0.01)
    assert calls == [], "start 不应弹窗也不应冒泡（already 复用是静默的）"
    del app


def test_stop_harness_path_is_reachable_from_module_public_names():
    """菜单/上层只依赖这些名字，改名要连测试一起改。"""
    for name in (
        "stop_harness", "restart_harness", "find_harness_process",
        "describe_harness_process", "listener_pids", "process_command_line",
    ):
        assert callable(getattr(hl, name)), name
    assert Path(hl.__file__).name == "harness_launcher.py"


# ------------------------------------------------------------ 菜单接线
def _menu_labels(menu):
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _harness_submenu(menu):
    action = next(
        action for action in menu.actions() if action.text() == "DeepSeek Harness"
    )
    return action.menu()


def test_harness_submenu_wires_three_lifecycle_actions(tmp_path, monkeypatch):
    """子菜单三件套齐全，且各自绑到正确的 action（启动/重启/停止 不能串线）。"""
    from PySide6.QtWidgets import QApplication, QMenu

    app = QApplication.instance() or QApplication([])
    from pet.context_menus.shared import add_harness

    calls: list[str] = []
    monkeypatch.setattr(
        "pet.context_menus.shared.launch_harness_gui",
        lambda pet, action="start": calls.append(action),
    )

    class _Pet:
        pass

    menu = QMenu()
    add_harness(menu, _Pet())
    submenu = _harness_submenu(menu)
    assert _menu_labels(submenu) == ["启动并打开页面", "重启服务", "停止服务"]
    for action in submenu.actions():
        action.trigger()
    # 菜单可见时的回调会被推迟到关闭后执行；这里直接驱动菜单关闭路径
    menu.close()
    app.processEvents()
    assert calls == ["start", "restart", "stop"]
    del app, tmp_path


def test_lite_build_hides_the_harness_submenu(tmp_path, monkeypatch):
    """纯桌宠（无 on_open_chat）版本连 DeepSeek Harness 子菜单都不显示。

    门禁在 pet/context_menus/legacy.py：``on_open_chat`` 为空时不调 add_harness。
    这里用带门禁的 legacy 构建器 + 一个最小替身验证——替身只提供门禁判定需要
    的属性，其它属性缺失时构建器会在门禁**之后**才用到它，不影响结论。
    """
    import inspect

    from PySide6.QtWidgets import QApplication, QMenu

    app = QApplication.instance() or QApplication([])
    import pet.context_menus.legacy as legacy_mod

    source = inspect.getsource(legacy_mod.build_legacy_menu)
    gate_at = source.index("on_open_chat")
    harness_at = source.index("add_harness")
    assert gate_at < harness_at, "门禁必须包住 add_harness（纯桌宠版不得出现 Harness 入口）"

    # 子菜单的顶层标题（旧的平级项「启动 DeepSeek Harness」已不存在）：
    # 轻量替身也验证三个动作都在子菜单内、不会绕过门禁漏成平级项。
    from pet.context_menus.shared import add_harness

    class _Pet:
        pass

    menu = QMenu()
    add_harness(menu, _Pet())
    assert "DeepSeek Harness" in _menu_labels(menu)
    assert "启动并打开页面" not in _menu_labels(menu)
    assert "停止服务" not in _menu_labels(menu)
    submenu = _harness_submenu(menu)
    assert _menu_labels(submenu) == ["启动并打开页面", "重启服务", "停止服务"]
    menu.close()
    app.processEvents()
    del app, tmp_path, monkeypatch


# ------------------------------------------------------------ GUI 线程模型（复审 P1-3/P1-4）
def test_harness_submenu_actions_close_on_trigger(tmp_path, monkeypatch):
    """三个 Harness 动作都必须 close_on_trigger：菜单先关闭、回调延迟执行，
    确认框才不会在 macOS 原生菜单跟踪会话里被 AppKit 抑制。"""
    from PySide6.QtWidgets import QApplication, QMenu

    app = QApplication.instance() or QApplication([])
    from pet.context_menus.shared import add_harness

    class _Pet:
        pass

    menu = QMenu()
    add_harness(menu, _Pet())
    submenu = _harness_submenu(menu)
    assert _menu_labels(submenu) == ["启动并打开页面", "重启服务", "停止服务"]
    for action in submenu.actions():
        assert bool(action.property("closeOnTrigger")), action.text()
    menu.close()
    app.processEvents()
    del app, tmp_path, monkeypatch


def test_launch_harness_gui_probes_off_gui_thread(monkeypatch):
    """停止/重启的进程反查（PowerShell，最长 10s）必须在 worker 线程执行：
    点菜单后 GUI 线程不得被 describe_harness_process 阻塞。"""
    import threading
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    gui_thread = threading.current_thread()
    probe_threads: list = []

    def fake_probe():
        probe_threads.append(threading.current_thread())
        return None

    monkeypatch.setattr(hl, "describe_harness_process", fake_probe)
    monkeypatch.setattr(hl, "_confirm_harness_stop",
                        lambda parent, target, *, restart: False)

    class _Pet:
        def show_bubble(self, text, duration=0):
            pass

    hl.launch_harness_gui(_Pet(), action="stop")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not probe_threads:
        app.processEvents()
        time.sleep(0.01)
    assert probe_threads, "停止动作必须先反查进程"
    assert probe_threads[0] is not gui_thread, "反查不得在 GUI 线程同步执行"
    del app


def test_launch_harness_gui_stop_declined_never_calls_stop(monkeypatch):
    """确认框取消：stop_harness 不得被调用（破坏性动作的闸门）。"""
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    calls: list[str] = []
    monkeypatch.setattr(hl, "describe_harness_process", lambda: None)
    monkeypatch.setattr(hl, "_confirm_harness_stop",
                        lambda parent, target, *, restart: False)
    monkeypatch.setattr(hl, "stop_harness",
                        lambda port=hl.DEFAULT_PORT: calls.append("stop") or ("stopped", "ok"))

    class _Pet:
        def show_bubble(self, text, duration=0):
            pass

    hl.launch_harness_gui(_Pet(), action="stop")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert calls == []
    del app


def test_launch_harness_gui_stop_confirmed_runs_in_worker(monkeypatch):
    """确认停止：stop_harness 在 worker 线程执行，结果经 singleShot 回 GUI 冒泡。"""
    import threading
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    gui_thread = threading.current_thread()
    stop_threads: list = []
    bubbles: list[str] = []
    monkeypatch.setattr(hl, "describe_harness_process", lambda: None)
    monkeypatch.setattr(hl, "_confirm_harness_stop",
                        lambda parent, target, *, restart: True)

    def fake_stop(port=hl.DEFAULT_PORT):
        stop_threads.append(threading.current_thread())
        return "stopped", "已停止。"

    monkeypatch.setattr(hl, "stop_harness", fake_stop)

    class _Pet:
        def show_bubble(self, text, duration=0):
            bubbles.append(text)

    hl.launch_harness_gui(_Pet(), action="stop")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not bubbles:
        app.processEvents()
        time.sleep(0.01)
    assert stop_threads and stop_threads[0] is not gui_thread
    assert bubbles == ["已停止。"]
    del app


def test_launch_harness_gui_confirm_raises_does_not_hang_worker(monkeypatch):
    """确认框回调抛异常（父窗口销毁/对话框构造失败）：worker 必须收尾并
    给出错误反馈，不得永久挂起零反馈，且不得执行停止。"""
    import time

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(hl, "describe_harness_process", lambda: None)

    def boom(parent, target, *, restart):
        raise RuntimeError("parent destroyed")

    monkeypatch.setattr(hl, "_confirm_harness_stop", boom)
    criticals: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *a, **k: criticals.append(str(a[-1]) if a else ""))
    stops: list = []
    monkeypatch.setattr(
        hl, "stop_harness",
        lambda port=hl.DEFAULT_PORT: stops.append(1) or ("stopped", "ok"))

    class _Pet:
        def show_bubble(self, text, duration=0):
            pass

    hl.launch_harness_gui(_Pet(), action="stop")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not criticals:
        app.processEvents()
        time.sleep(0.01)
    assert criticals, "确认框异常必须给出错误反馈（worker 不得静默挂死）"
    assert "parent destroyed" in criticals[0]
    assert stops == [], "确认框异常时不得执行停止"
    del app
