# -*- coding: utf-8 -*-
"""D4 桌宠单实例进程门测试（全 offscreen）。

覆盖验收口径：
1. 抢到门拒绝双开、释放后可再开、未持有者 release 不碰别人的锁文件；
2. 崩溃残留锁（真子进程被 kill 后留下的锁文件）按 QLockFile 既有 stale 语义接管；
3. 环境错误（锁文件根本建不出来）放行启动，不把坏环境伪装成"已有实例"；
4. 门与拓扑无关（T4 Phase A）：legacy 单窗（含捕获模式重启后的形态）同样抢门，
   第二个完整实例被拒；4.4a 起 main() 不再抢 slot 文件锁（`--slot` 兼容解析保留）；
5. 启动链接线：双开在 slot 竞争之前被拒 + 留痕 + 静默退出码；
   正常退出（main finally）与会话结束（_on_session_end）两条路径都释放门；
6. 真进程/真 QApplication 端到端双开（子进程跑 `python -m pet`）。

时序纪律：子进程就绪用「标记文件 + 宽预算轮询」（同 tests/test_slot_and_memory.py
的持锁 worker 范式），不赌固定 sleep。env 一律经 monkeypatch 隔离。
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from pet import overlay_instance_gate as gate_mod
from pet.config import APP_DIR_NAME
from pet.slot_manager import pid_alive

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 子进程持门脚本：抢到门后写就绪标记（"1 <自己 pid>"），然后挂住直到被杀
# （模拟"第一个 overlay"）。pid 由子进程自报，启动 seam 保证它就是 Popen.pid
# （见 _holder_interpreter）。
_GATE_HOLDER = """
import os, sys, time
from pathlib import Path

from pet.overlay_instance_gate import OverlayInstanceGate

gate = OverlayInstanceGate(sys.argv[1])
ok = gate.acquire()
Path(sys.argv[2]).write_text(("1" if ok else "0") + " " + str(os.getpid()), encoding="utf-8")
while True:
    time.sleep(0.05)
"""


def _wait_for(predicate, message: str, *, timeout: float = 60.0) -> None:
    """宽预算轮询到条件成立；超时带上诊断失败（不赌固定 sleep 猜时序）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _holder_interpreter() -> tuple[str, dict | None]:
    """持门子进程的 (解释器, env)；env=None = 原样继承父进程环境。

    Windows 上 venv 的 `Scripts/python.exe` 是 redirector（启动器）：它自己
    re-exec 到项目基础解释器，于是 `Popen.pid` 是启动器 pid，与子进程自报的
    `os.getpid()` 不是同一个进程。
    test_crash_residue_lock_reclaimed_by_pid_staleness 要的是「进程已被杀、但父
    进程仍持着它的句柄」这个未 reap 窗口，被杀进程必须是 Popen 直接持有的那一个
    ——否则句柄随启动器退出而关闭，旧 pid_alive（只判句柄能否打开）也能蒙对，
    用例就抓不到回归。所以 Windows 下直接跑基础解释器 `sys._base_executable`
    （非 venv 时它就等于 `sys.executable`，行为不变）；直接跑基础解释器会丢掉
    venv 的 site-packages（pyvenv.cfg 默认 include-system-site-packages=false，
    依赖只在 venv 里），故把当前解释器的 site-packages 经 PYTHONPATH 带过去。
    其他平台保留原启动方式。
    """
    python = sys.executable
    if sys.platform == "win32":
        base = getattr(sys, "_base_executable", "")
        if base and Path(base).exists():
            python = str(base)
    if python == sys.executable:
        return python, None
    env = os.environ.copy()
    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (purelib, env.get("PYTHONPATH")) if part)
    return python, env


def _spawn_gate_holder(config_dir: Path, ready: Path) -> subprocess.Popen:
    python, env = _holder_interpreter()
    return subprocess.Popen(
        [python, "-c", _GATE_HOLDER, str(config_dir), str(ready)],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=60)


def _kill_hard(pid: int) -> None:
    """强杀指定 pid（模拟 overlay 进程崩溃：不给它走 release/析构的机会）。"""
    if sys.platform == "win32":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    os.kill(int(pid), signal.SIGKILL)


class _FakeQApplication:
    """main() 启动链对 QApplication 的三次调用的最小替身（不建真 Qt 实例）。

    真 Qt 单例在共享测试进程里无法安全重复构造（"destroy the QApplication
    singleton"），启动链分支用替身测；真 Qt/真进程链路由本文件末尾的子进程
    用例覆盖。
    """

    def __init__(self, argv):
        self.argv = list(argv)
        self.app_name = None
        self.quit_on_last_window_closed = None

    def setApplicationName(self, name):  # noqa: N802 - Qt API
        self.app_name = name

    def setQuitOnLastWindowClosed(self, flag):  # noqa: N802 - Qt API
        self.quit_on_last_window_closed = bool(flag)

    def exec(self):  # noqa: A003 - Qt API
        return 0


# ---------------------------------------------------------------- 核心互斥
def test_second_gate_refused_then_reopens_after_release(tmp_path):
    """抢到门 → 第二实例被拒（持有者 pid 就是本进程）→ 释放后可再开。"""
    lock_path = tmp_path / gate_mod.LOCK_NAME
    first = gate_mod.OverlayInstanceGate(tmp_path)
    assert first.acquire() is True
    assert (first.acquired, first.held) == (True, True)
    assert first.holder_pid() == os.getpid()
    # 锁文件第一行就是持有者 pid（QLockFile 的记录格式，进程间排查入口）
    assert lock_path.read_text(encoding="utf-8").splitlines()[0].strip() == str(os.getpid())

    second = gate_mod.OverlayInstanceGate(tmp_path)
    assert second.acquire() is False
    assert (second.acquired, second.held) == (False, False)
    assert second.holder_pid() == os.getpid()

    first.release()
    assert lock_path.exists() is False

    third = gate_mod.OverlayInstanceGate(tmp_path)
    assert third.acquire() is True
    third.release()


def test_release_without_hold_does_not_touch_holder_lock(tmp_path):
    """未持有门的实例 release 是 no-op：不得删掉真正持有者的锁文件。"""
    lock_path = tmp_path / gate_mod.LOCK_NAME
    holder = gate_mod.OverlayInstanceGate(tmp_path)
    assert holder.acquire() is True

    blocked = gate_mod.OverlayInstanceGate(tmp_path)
    assert blocked.acquire() is False
    blocked.release()
    blocked.release()  # 幂等
    assert blocked.held is False
    assert lock_path.exists() is True
    assert gate_mod.OverlayInstanceGate(tmp_path).acquire() is False  # 仍被拒
    holder.release()


def test_refusal_leaves_trace(tmp_path, caplog):
    """拒绝路径：进程日志 + 落盘留痕文件（打包产物无 stderr 时的唯一证据）。"""
    holder = gate_mod.OverlayInstanceGate(tmp_path)
    assert holder.acquire() is True
    blocked = gate_mod.OverlayInstanceGate(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert blocked.acquire() is False
    assert any("拒绝启动" in record.getMessage() for record in caplog.records)

    trace = (tmp_path / gate_mod.TRACE_NAME).read_text(encoding="utf-8")
    assert "refused" in trace
    assert f"holder={os.getpid()}" in trace
    holder.release()


def test_crash_residue_lock_reclaimed_by_pid_staleness(tmp_path):
    """崩溃残留：持有进程被 kill（不走 release）→ 锁文件里的 pid 已死 → 新实例接管。"""
    config_dir = tmp_path
    ready = tmp_path / "holder-ready"
    proc = _spawn_gate_holder(config_dir, ready)
    try:
        _wait_for(lambda: ready.exists(), "子进程未在预算内抢到进程门")
        status, holder_pid_text = ready.read_text(encoding="utf-8").split()
        assert status == "1"
        holder_pid = int(holder_pid_text)
        assert holder_pid != os.getpid()
        # 启动 seam 的不变式：被子进程写进锁文件的那个 pid，就是 Popen 直接持有的
        # 进程（否则下面杀完进程，句柄的持有者另有其人，未 reap 窗口根本不存在）。
        assert proc.pid == holder_pid
        lock_path = config_dir / gate_mod.LOCK_NAME
        assert lock_path.exists() is True
        assert gate_mod.OverlayInstanceGate(config_dir).holder_pid() == holder_pid
        _kill_hard(holder_pid)  # 模拟第一个 overlay 进程崩溃：不走 release
        # 等到子进程真的退出（poll 只是 WaitForSingleObject，不关句柄）。
        # 关键窗口：_reap(proc) 在 finally，晚于下面的断言 —— Popen 的句柄仍被本
        # 进程持住，Windows 上死进程对象不会销毁，OpenProcess 一路成功；所以
        # pid_alive 必须回答「进程是否已退出」，不能把「句柄打得开」当活着。
        _wait_for(lambda: proc.poll() is not None, "持有进程未被强杀掉", timeout=30)
        assert pid_alive(holder_pid) is False  # 已终止且未 reap 时也必须为 False
    finally:
        _reap(proc)

    # 残留锁文件还在（没有 release 就没有 unlink），但持有者 pid 已死
    assert lock_path.exists() is True
    assert pid_alive(holder_pid) is False

    recovered = gate_mod.OverlayInstanceGate(config_dir)
    assert recovered.acquire() is True
    assert recovered.held is True
    assert recovered.holder_pid() == os.getpid()
    recovered.release()
    assert lock_path.exists() is False


def test_gate_path_blocked_fails_open(tmp_path):
    """环境错误（config 路径被文件占住，锁文件建不出来）→ 放行启动，不误判双开。"""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")

    gate = gate_mod.OverlayInstanceGate(blocked)
    assert gate.acquire() is True
    assert gate.held is False  # 放行 ≠ 持锁
    gate.release()  # no-op，不炸


# ---------------------------------------------------------------- 拓扑作用域
def test_gate_is_topology_independent(tmp_path, monkeypatch):
    """T4 Phase A：门不再按拓扑分流——两个拓扑都抢同一把锁。

    overlay 双开会各自恢复 sprite 互踩；捕获模式的 legacy 单窗也是完整实例
    （建窗/存位置/落盘配置），双开与「重启时多只桌宠收拢为主宠物」直接冲突。
    """
    for index, value in enumerate(("legacy", "", "overlay", " OVERLAY ")):
        case_dir = tmp_path / f"case-{index}"
        monkeypatch.setenv("PET_RENDER_TOPOLOGY", value)
        gate = gate_mod.acquire_overlay_instance_gate(case_dir)
        assert gate is not None, value
        assert (gate.acquired, gate.held) == (True, True), value
        # 同目录第二次启动被拒（跨拓扑一致）
        second = gate_mod.acquire_overlay_instance_gate(case_dir)
        assert (second.acquired, second.held) == (False, False), value
        gate.release()


def test_legacy_topology_also_holds_the_gate(tmp_path, monkeypatch):
    """legacy 拓扑（含捕获模式重启后的形态）：照样占住进程门，双击不起第二实例。"""
    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "legacy")
    first = gate_mod.acquire_overlay_instance_gate(tmp_path)
    assert first.acquired is True and first.held is True
    assert first.path.exists() is True

    second = gate_mod.acquire_overlay_instance_gate(tmp_path)
    assert second.acquired is False
    first.release()


def test_overlay_topology_creates_gate_and_refuses_second(tmp_path, monkeypatch):
    """overlay 拓扑：入口建门；同拓扑第二实例被拒。"""
    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "overlay")
    first = gate_mod.acquire_overlay_instance_gate(tmp_path)
    assert first is not None
    assert (first.acquired, first.held) == (True, True)
    assert first.path.exists() is True

    second = gate_mod.acquire_overlay_instance_gate(tmp_path)
    assert second is not None
    assert (second.acquired, second.held) == (False, False)
    first.release()


# ---------------------------------------------------------------- app 启动链接线
def test_main_refuses_second_overlay_instance_before_slot(tmp_path, monkeypatch, caplog):
    """overlay 双开：拒绝先于 slot 竞争（不建 slots/、不落种 slot 配置）+ 留痕。"""
    from pet import app as app_mod

    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "overlay")
    monkeypatch.setattr(app_mod, "_default_base", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "QApplication", _FakeQApplication)
    monkeypatch.setattr("pet.context_menus.menu_styles.common.install_responsive_menu_style", lambda _app: None)

    config_dir = tmp_path / APP_DIR_NAME
    holder = gate_mod.OverlayInstanceGate(config_dir)
    assert holder.acquire() is True
    try:
        with caplog.at_level(logging.WARNING):
            code = app_mod.main(["dsh-pet"])
        assert code == gate_mod.DUPLICATE_EXIT_CODE == 0
    finally:
        holder.release()

    assert not (config_dir / "slots").exists()
    assert not list(config_dir.glob("config-slot-*.json"))
    assert not (config_dir / "migration-spawns.done").exists()
    assert any("已有实例在运行" in record.getMessage() for record in caplog.records)
    assert "refused" in (config_dir / gate_mod.TRACE_NAME).read_text(encoding="utf-8")


def test_main_legacy_topology_also_takes_gate_and_does_not_take_slot_lock(
        tmp_path, monkeypatch):
    """legacy 拓扑同样抢门（T4 Phase A），且 main() 不再抢 slot 文件锁（T5）。

    捕获模式重启后就是 legacy 单窗形态：门必须照样占住（否则双击会起第二个完整
    实例），启动链其余部分逐行不变。`--slot N` 兼容解析保留：合法值只用于选
    config-slot-N.json 身份，不再有任何跨进程竞争副作用（不建 slots/、不落种）。
    """
    from pet import app as app_mod
    from pet.config import Config as RealConfig

    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "legacy")
    monkeypatch.delenv("DSH_PET_INSTANCE", raising=False)
    monkeypatch.setattr(app_mod, "_default_base", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "QApplication", _FakeQApplication)
    monkeypatch.setattr("pet.context_menus.menu_styles.common.install_responsive_menu_style", lambda _app: None)
    monkeypatch.setattr(app_mod, "Config", lambda *a, **k: RealConfig(base=tmp_path))
    monkeypatch.setattr(app_mod, "_setup_logging", lambda _config: None)
    monkeypatch.setattr(app_mod.autostart_mod, "cleanup_stale_entries", lambda: 0)

    # 退役层符号已删除：取用即失败（比"断言没调用"更强的守卫）
    assert not hasattr(app_mod.slot_manager_mod, "acquire_pet_slot")

    started = []

    class _StubShell:
        def __init__(self, *_args, **kwargs):
            started.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(app_mod, "AppShell", _StubShell)

    assert app_mod.main(["dsh-pet", "--slot", "3"]) == 0
    config_dir = tmp_path / APP_DIR_NAME
    assert started and started[0]["slot_id"] == 3          # 身份照旧按 --slot 选
    # G1 身份口径：``--slot N`` 只作为**实例身份**显式传给 AppShell；进程级
    # ``DSH_PET_INSTANCE`` 固定空串——它代表"全局配置身份"，不再被 --slot 污染
    #（否则 ``Config(instance_id=None)`` 会去读 config-slot-N.json，全局与档案重合）。
    assert os.environ["DSH_PET_INSTANCE"] == ""
    assert not (config_dir / "slots").exists()              # 不抢锁 = 不建 slots/
    assert not list(config_dir.glob("config-slot-*.json"))
    # 门真被抢过（留痕），且正常退出已释放（锁文件不残留）
    assert "acquired" in (config_dir / gate_mod.TRACE_NAME).read_text(encoding="utf-8")
    assert not (config_dir / gate_mod.LOCK_NAME).exists()


def test_main_legacy_topology_refuses_second_instance(tmp_path, monkeypatch, caplog):
    """捕获模式形态（legacy 单窗）下双击起第二个完整实例：拒绝且零副作用。"""
    from pet import app as app_mod

    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "legacy")
    monkeypatch.setattr(app_mod, "_default_base", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "QApplication", _FakeQApplication)
    monkeypatch.setattr("pet.context_menus.menu_styles.common.install_responsive_menu_style", lambda _app: None)

    config_dir = tmp_path / APP_DIR_NAME
    holder = gate_mod.OverlayInstanceGate(config_dir)
    assert holder.acquire() is True

    class _ForbiddenShell:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("已有实例在运行：不得建到 AppShell 这一层")

    monkeypatch.setattr(app_mod, "AppShell", _ForbiddenShell)
    try:
        with caplog.at_level(logging.WARNING):
            code = app_mod.main(["dsh-pet"])
    finally:
        holder.release()

    assert code == gate_mod.DUPLICATE_EXIT_CODE == 0
    assert any("已有实例在运行" in record.getMessage() for record in caplog.records)
    assert not list(config_dir.glob("config-slot-*.json"))


def test_main_releases_gate_when_startup_fails(tmp_path, monkeypatch):
    """正常退出链：main 的 finally 释放门（启动失败与事件循环结束共用此出口）。"""
    from pet import app as app_mod
    from pet.config import Config as RealConfig

    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "overlay")
    monkeypatch.setattr(app_mod, "_default_base", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "QApplication", _FakeQApplication)
    monkeypatch.setattr("pet.context_menus.menu_styles.common.install_responsive_menu_style", lambda _app: None)
    # 隔离真实用户配置目录与日志（Config/_setup_logging 不走 _default_base）
    monkeypatch.setattr(app_mod, "Config", lambda *a, **k: RealConfig(base=tmp_path))
    monkeypatch.setattr(app_mod, "_setup_logging", lambda _config: None)
    monkeypatch.setattr(app_mod.autostart_mod, "cleanup_stale_entries", lambda: 0)

    config_dir = tmp_path / APP_DIR_NAME
    gate = gate_mod.OverlayInstanceGate(config_dir)
    assert gate.acquire() is True
    monkeypatch.setattr(gate_mod, "acquire_overlay_instance_gate", lambda _cfg: gate)

    class _BoomShell:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("测试：启动失败路径")

    monkeypatch.setattr(app_mod, "AppShell", _BoomShell)

    assert app_mod.main(["dsh-pet"]) == 1
    assert gate.held is False
    assert gate.path.exists() is False


def test_session_end_releases_gate(tmp_path, monkeypatch):
    """会话结束链：_on_session_end 释放门（幂等）；未注入门时 release 为 no-op。"""
    from pet.app import AppShell

    gate = gate_mod.OverlayInstanceGate(tmp_path)
    assert gate.acquire() is True

    shell = object.__new__(AppShell)  # 只测退出链挂点，不建真壳
    shell._session_end_done = False
    shell._instances = []
    shell._overlay_gate = gate
    monkeypatch.setattr(AppShell, "_mark_session_ending", lambda self: None)

    AppShell._on_session_end(shell)
    assert gate.held is False
    assert gate.path.exists() is False
    AppShell._on_session_end(shell)  # 幂等：不再收口第二次

    bare = object.__new__(AppShell)
    bare._overlay_gate = None
    AppShell._release_overlay_gate(bare)  # 无门（object.__new__ 造壳）：no-op


# ---------------------------------------------------------------- 真进程端到端
def _child_base(tmp_path: Path) -> Path:
    """子进程 ``config._default_base()`` 会返回的根（按平台推导，用于隔离真实配置）。"""
    if sys.platform == "win32":
        return tmp_path
    if sys.platform == "darwin":
        return tmp_path / "Library" / "Application Support"
    return tmp_path / ".config"


def test_real_second_process_refused_on_overlay_topology(tmp_path):
    """真 Qt + 真进程：第二实例跑 `python -m pet` 被门拒绝并静默退出。"""
    config_dir = _child_base(tmp_path) / APP_DIR_NAME
    holder = gate_mod.OverlayInstanceGate(config_dir)
    assert holder.acquire() is True

    env = os.environ.copy()
    env["PET_RENDER_TOPOLOGY"] = "overlay"
    env["QT_QPA_PLATFORM"] = "offscreen"
    if sys.platform == "win32":
        env["APPDATA"] = str(tmp_path)
    else:
        env["HOME"] = str(tmp_path)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pet"], cwd=str(_REPO_ROOT), env=env,
            capture_output=True, text=True, timeout=180,
        )
    finally:
        holder.release()

    assert proc.returncode == gate_mod.DUPLICATE_EXIT_CODE
    assert "已有实例在运行" in proc.stderr  # 无日志文件时的可见留痕
    assert not (config_dir / "slots").exists()
    assert not list(config_dir.glob("config-slot-*.json"))
