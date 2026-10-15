# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动单元测试。

测试覆盖：
- 默认全关；
- 有界 Byte-Offset Tailer：新增行增量读取、重复读取不重放、文件轮转/截断安全、backfill 防护；
- 事件 JSONL 解析与状态规范化映射；
- AgentLinkManager 生命周期与 pause / resume；
- 状态变更触发桌宠行为与气泡反馈；
- Claude Code 确认框逻辑（拒绝则不写入 hooks）；
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import pet.agent_link as agent_link
from pet import click_sound, harness_launcher
from pet.dsh_state import DshState, SCOPE_REJECT_LOG_EVERY
from pet.agent_link import (
    AgentLinkManager,
    AgentEvent,
    BaseAgentMonitor,
    ByteOffsetTailer,
    DirGlobTailer,
    ClaudeCodeMonitor,
    CursorMonitor,
    CustomAgentMonitor,
    DshMonitor,
    normalize_event_state,
    opencode_event_state,
)
from pet.config import Config
from pet.config import _clean_agent_link_data, _clean_custom_agents
from pet.persona_phrases import PUBLIC_DIALOGUE_EVENTS, phrase_keys


class TestMainlineAgentLinkHardening:
    def test_monitor_polling_uses_worker_and_stops(self, tmp_path):
        """监视器轮询不占 GUI 线程，stop 后 worker 必须退出。"""
        app = QApplication.instance() or QApplication([])
        seen = []
        ready = threading.Event()

        class ProbeMonitor(BaseAgentMonitor):
            _POLL_INTERVAL_S = 0.01

            def _poll(self, gen=None):
                seen.append(threading.get_ident())
                self._emit_state("working", self._emit_gen if gen is None else gen)
                ready.set()
                self._worker_stop.set()

        monitor = ProbeMonitor("probe", tmp_path)
        events = []
        monitor.state_event.connect(events.append)
        monitor.start()
        assert ready.wait(1.0)
        app.processEvents()
        assert seen and seen[0] != threading.get_ident()
        assert events and isinstance(events[0], AgentEvent)
        assert events[0].gen == monitor._gen
        monitor.stop()
        assert monitor._worker is not None and not monitor._worker.is_alive()

    def test_pause_buffers_events_until_resume(self, tmp_path):
        """隐藏期间落在 poll 中的状态事件在 resume 时补发。"""
        monitor = BaseAgentMonitor("probe", tmp_path)
        events = []
        monitor.state_event.connect(events.append)
        monitor.start()
        monitor.pause()
        monitor._emit_state("working", monitor._emit_gen)
        assert events == []
        monitor.resume()
        assert [event.state for event in events] == ["working"]
        monitor.stop()

    def test_stale_generation_is_rejected_at_manager(self, tmp_path):
        class Win:
            def isVisible(self):
                return True

            def mark_activity(self):
                pass

        mgr = AgentLinkManager(Win(), Config(base=tmp_path), min_interval=0.0)
        monitor = mgr.monitors["cursor"]
        monitor.start()
        old_gen = monitor._emit_gen
        monitor.stop()
        monitor.start()
        current_gen = monitor._emit_gen
        mgr._on_agent_state_event(AgentEvent("cursor", "state", gen=old_gen, state="working"))
        assert "cursor" not in mgr._last_raw
        mgr._on_agent_state_event(AgentEvent("cursor", "state", gen=current_gen, state="working"))
        assert mgr._last_raw["cursor"] == "working"
        mgr.shutdown()

    def test_install_completion_from_old_token_is_dropped(self, tmp_path):
        bubbles = []

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(Win(), cfg)
        mgr._install_pending["dsh"] = 11
        mgr._on_install_finished("dsh", True, "ok", 10)
        assert cfg.data["agent_link"]["dsh"] is False
        assert mgr._install_pending["dsh"] == 11
        assert bubbles == []
        mgr.shutdown()

    def test_state_and_activity_refresh_idle_activity_anchor(self, tmp_path):
        class Win:
            def __init__(self):
                self.activity_count = 0

            def isVisible(self):
                return True

            def mark_activity(self):
                self.activity_count += 1

        win = Win()
        mgr = AgentLinkManager(win, Config(base=tmp_path), min_interval=0.0)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_activity("dsh", "read")
        assert win.activity_count == 2
        mgr.shutdown()


# ============================================================================
# 1. ByteOffsetTailer 核心增量读取测试
# ============================================================================
class TestByteOffsetTailer:
    def test_backfill_protection_on_startup(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"event": "old1"}\n{"event": "old2"}\n', encoding="utf-8")

        tailer = ByteOffsetTailer(fpath)
        # 首次调用 read_new_lines 应当做 backfill 防护，不读取启动前的历史行
        lines = tailer.read_new_lines()
        assert lines == []
        assert tailer.offset == fpath.stat().st_size

        # 写入新行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "new1"}\n')

        new_lines = tailer.read_new_lines()
        assert len(new_lines) == 1
        assert json.loads(new_lines[0])["event"] == "new1"

    def test_no_duplicate_reads(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 初始化

        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "ev1"}\n')

        lines1 = tailer.read_new_lines()
        assert len(lines1) == 1

        # 再次调用不应重复读取
        lines2 = tailer.read_new_lines()
        assert len(lines2) == 0

    def test_file_truncation_resets_safely(self, tmp_path):
        fpath = tmp_path / "test.jsonl"
        fpath.write_text('{"event": "a"}\n{"event": "b"}\n', encoding="utf-8")
        tailer = ByteOffsetTailer(fpath)
        tailer.offset = 100  # 假设之前读取了较大 offset

        # 文件被清空重写（size < offset）
        fpath.write_text('{"event": "fresh"}\n', encoding="utf-8")
        tailer._initial_backfill_done = True

        lines = tailer.read_new_lines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "fresh"

    def test_stats_file_once_per_read(self, tmp_path, monkeypatch):
        """``read_new_lines`` 每拍每文件只 stat 一次（is_file 与 stat 合并）。"""
        path = tmp_path / "dsh.jsonl"
        path.write_text('{"event":"one"}\n', encoding="utf-8")
        tailer = ByteOffsetTailer(path)
        assert tailer.read_new_lines() == []       # backfill 防护

        stats: list[str] = []
        real_stat = Path.stat

        def counting_stat(self, *args, **kwargs):
            stats.append(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", counting_stat)
        path.write_text('{"event":"one"}\n{"event":"two"}\n', encoding="utf-8")

        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["two"]
        assert stats.count(str(path)) == 1, f"每拍每文件只允许一次 stat，实际 {stats}"

    def test_missing_file_is_silent(self, tmp_path):
        """文件不存在（未创建/已删除）：单次 stat 失败即按空读返回，不抛。"""
        tailer = ByteOffsetTailer(tmp_path / "nope.jsonl")
        tailer._initial_backfill_done = True
        assert tailer.read_new_lines() == []

    def test_directory_path_is_not_read(self, tmp_path):
        """同名目录不是普通文件：按「文件不存在」处理（不尝试打开、不抛）。"""
        target = tmp_path / "dsh.jsonl"
        target.mkdir()
        tailer = ByteOffsetTailer(target)
        tailer._initial_backfill_done = True
        assert tailer.read_new_lines() == []




class TestDirGlobTailer:
    def test_discovers_new_files_during_scan_throttle_and_keeps_offsets(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        first = tmp_path / "dsh.jsonl"
        first.write_text('{"event":"old"}\n', encoding="utf-8")
        assert tailer.read_new_lines() == []
        with first.open("a", encoding="utf-8") as f:
            f.write('{"event":"one"}\n')
        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["one"]
        second = tmp_path / "dsh-session-2.jsonl"
        second.write_text('{"event":"new-session"}\n', encoding="utf-8")
        # Windows 目录时间戳惰性更新（本机 _probe_dir_mtime.py 实测 25/50 次不变），
        # 「建文件 → 目录 mtime 变」不是确定前提；显式推新目录时间戳，确定性制造
        # 目录变化信号（与 test_directory_change_within_interval_is_discovered 同款）。
        stat = tmp_path.stat()
        os.utime(tmp_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        # Directory change bypasses the long periodic interval; startup
        # backfill still skips content written before this file was discovered.
        assert tailer.read_new_lines() == []
        with second.open("a", encoding="utf-8") as f:
            f.write('{"event":"after-discovery"}\n')
        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["after-discovery"]

    def test_reads_multiple_sessions_and_rotation(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        one = tmp_path / "dsh-1.jsonl"
        two = tmp_path / "dsh-2.jsonl"
        one.touch(); two.touch()
        tailer._initial_backfill_done = True
        assert tailer.read_new_lines() == []
        one.write_text('{"event":"rotated"}\n', encoding="utf-8")
        two.write_text('{"event":"session-2"}\n', encoding="utf-8")
        events = [json.loads(x)["event"] for x in tailer.read_new_lines()]
        assert set(events) == {"rotated", "session-2"}

    def test_reset_forces_rescan(self, tmp_path):
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        tailer.read_new_lines()
        path = tmp_path / "dsh-reset.jsonl"
        path.write_text('{"event":"before-reset"}\n', encoding="utf-8")
        tailer.reset()
        tailer._initial_backfill_done = True
        assert json.loads(tailer.read_new_lines()[0])["event"] == "before-reset"

    def test_scan_interval_skips_repeated_glob_and_stats(self, tmp_path, monkeypatch):
        """scan_interval 内重复读：不再枚举目录、每个 tail 文件只 stat 一次。

        实机 py-spy：GUI 侧 15.6% 时间耗在这条轮询的 stat 上（本机 stat 病态
        昂贵）。``scan_interval`` 此前只存不用——每拍全量枚举（最多 64 文件）
        + 每文件 ``is_file()`` 紧接 ``stat()`` 两次调用。
        """
        path = tmp_path / "dsh.jsonl"
        path.write_text('{"event":"boot"}\n', encoding="utf-8")
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        assert tailer.read_new_lines() == []      # 首次：扫描 + backfill 防护

        scans: list[str] = []
        real_scandir = os.scandir

        def counting_scandir(target):
            scans.append(str(target))
            return real_scandir(target)

        stats: list[str] = []
        real_stat = Path.stat

        def counting_stat(self, *args, **kwargs):
            stats.append(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(os, "scandir", counting_scandir)
        monkeypatch.setattr(Path, "stat", counting_stat)

        for _ in range(3):
            assert tailer.read_new_lines() == []

        assert scans == [], f"scan_interval 内目录未变不得重复枚举，实际 {scans}"
        assert stats.count(str(path)) == 3, (
            f"每个 tail 文件每拍只允许一次 stat（is_file+stat 已合并），实际 {stats.count(str(path))}"
        )

    def test_directory_change_within_interval_is_discovered(self, tmp_path):
        """扫描间隔未到但目录变了（新文件）：当拍即发现，不吃延迟。

        产品契约是「目录 mtime_ns 变了 → 当拍全量扫，不等 scan_interval」。本机
        实测（``_probe_dir_mtime.py``）：Windows 的目录时间戳**惰性**更新，
        「建文件 → 读目录 mtime → 再建文件」两步之间 mtime 有 25/50 次没变——
        原用例把「当拍即发现」压在这条 OS 时序上，在负载高的机器上会随机变红。
        这里显式把目录时间戳推到新值，确定性地制造契约前提（断言本身不放宽：
        夹在 scan_interval=60 中间，只有真·当拍扫才能读到新文件）。
        """
        first = tmp_path / "dsh-1.jsonl"
        first.write_text('{"event":"old"}\n', encoding="utf-8")
        tailer = DirGlobTailer(tmp_path, scan_interval=60)
        tailer.read_new_lines()
        tailer._initial_backfill_done = True      # 关掉新文件回填，直接读内容

        second = tmp_path / "dsh-2.jsonl"
        second.write_text('{"event":"fresh"}\n', encoding="utf-8")
        stat = tmp_path.stat()
        os.utime(tmp_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        assert tailer._directory_changed() is True
        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["fresh"]

    def test_scan_interval_elapsed_rescans_without_directory_change(self, tmp_path):
        """间隔到点必须兜底全量扫描（目录 mtime 分辨率粗的 FS 上仍能发现新文件）。"""
        tailer = DirGlobTailer(tmp_path, scan_interval=0.0)
        (tmp_path / "dsh-1.jsonl").write_text("{}\n", encoding="utf-8")
        tailer.read_new_lines()
        second = tmp_path / "dsh-2.jsonl"
        second.write_text('{"event":"late"}\n', encoding="utf-8")
        tailer._initial_backfill_done = True

        assert [json.loads(x)["event"] for x in tailer.read_new_lines()] == ["late"]

    def test_scan_keeps_active_file_when_stale_names_fill_the_budget(self, tmp_path):
        """候选集按 **mtime 倒序** 取前 max_files：活会话不得被陈旧文件挤出。

        实机（2026-09-27）：%APPDATA%/dsh-pet-bridge 积了 172 个 09-09 起的陈旧
        ``dsh-*.jsonl``，旧实现按文件名字典序取前 64 名——活会话文件
        ``dsh-<大 pid>.jsonl`` 排在 64 名外，每拍白刷 64 个死文件，活的反而读不到。
        本用例的陈旧文件名（零填充小 pid）名字序全部靠前，只有 mtime 序能把活文件
        拉进集合。
        """
        now = time.time()
        tailer = DirGlobTailer(tmp_path, scan_interval=0.0, max_files=64)
        stale = []
        for i in range(68):
            path = tmp_path / f"dsh-{i:08d}.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            # 2 小时前起、每个错开 1s：都属于「未超龄」的活跃区间（< 24h）
            stamp = now - 2 * 3600 + i
            os.utime(path, (stamp, stamp))
            stale.append(path)
        live = tmp_path / "dsh-99999999.jsonl"
        live.write_text('{"event":"live"}\n', encoding="utf-8")
        os.utime(live, (now, now))

        tailer.read_new_lines()

        assert str(live) in tailer._tailers, "最新写入的活会话文件必须在扫描集合内"
        assert len(tailer._tailers) == 64
        assert str(stale[67]) in tailer._tailers, "次新文件保留"
        assert str(stale[0]) not in tailer._tailers, "最旧的 5 个被挤出预算"
        assert str(stale[4]) not in tailer._tailers

    def test_scan_drops_only_stale_files_of_dead_writers(self, tmp_path):
        """超龄（>24h）且写者 pid 已死 → 逐出并清理；其余一律保守保留。

        保守面（都不许被清）：写者仍活着的超龄文件、无 pid 线索的旧版
        ``dsh.jsonl``、未超龄的死写者文件、判定不了存活性的 pid。
        """
        old = time.time() - 3 * 24 * 3600
        # 2**32-8 超出任何平台的 pid 上限（Linux pid_max ≤ 2**22、Windows pid 按 4 递增），
        # 保证「写者已死」这一前提是确定的——不赌子进程 pid 复用。
        dead_pid, alive_but_idle_pid, fresh_pid = 4294967288, 4294967280, 4294967272
        assert agent_link._bridge_writer_alive(dead_pid) is False, "前提：探活必须判定为已死"

        dead = tmp_path / f"dsh-{dead_pid}.jsonl"
        dead.write_text("{}\n", encoding="utf-8")
        os.utime(dead, (old, old))

        idle_writer = tmp_path / f"dsh-{os.getpid()}.jsonl"   # 写者=本进程：活着
        idle_writer.write_text("{}\n", encoding="utf-8")
        os.utime(idle_writer, (old, old))

        legacy = tmp_path / "dsh.jsonl"                        # 旧版单实例名：无 pid 线索
        legacy.write_text("{}\n", encoding="utf-8")
        os.utime(legacy, (old, old))

        fresh_dead = tmp_path / f"dsh-{fresh_pid}.jsonl"       # 死写者但刚写过
        fresh_dead.write_text("{}\n", encoding="utf-8")

        tailer = DirGlobTailer(tmp_path, scan_interval=0.0)
        tailer.read_new_lines()

        assert str(dead) not in tailer._tailers
        assert not dead.exists(), "陈旧死写者的桥文件应被清理"
        assert str(idle_writer) in tailer._tailers, "写者还活着：哪怕 24h 无写入也不许清"
        assert str(legacy) in tailer._tailers, "无 pid 线索的旧版文件保守保留"
        assert str(fresh_dead) in tailer._tailers, "未超龄的死写者文件保守保留"

    def test_bridge_writer_alive_treats_out_of_range_pid_as_dead(self, monkeypatch):
        """超出 pid_t 表示范围的 pid：posix ``os.kill`` 抛 OverflowError，判定为已死。

        复现 CI（ubuntu/macos）：``_bridge_writer_alive(2**32-8)`` 返回 None
        而非 False——OverflowError 非 OSError，若被上层兜底就成了「判定失败」。
        本用例在 Windows 上也能钉死这条分支（注入 posix 侧边界；真平台走
        ``OpenProcess`` 的 ERROR_INVALID_PARAMETER，同判已死）。
        """
        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(OverflowError("pid is out of range")))

        assert agent_link._probe_writer_pid_posix(4294967288) is False
        assert agent_link._bridge_writer_alive(4294967288) is False

    def test_scan_keeps_stale_file_when_liveness_cannot_be_decided(self, tmp_path, monkeypatch):
        """探活判定失败（返回 None）时按保守处理：不清理、仍参与轮询。"""
        old = time.time() - 3 * 24 * 3600
        path = tmp_path / "dsh-424242.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (old, old))
        monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: None)

        tailer = DirGlobTailer(tmp_path, scan_interval=0.0)
        tailer.read_new_lines()

        assert str(path) in tailer._tailers
        assert path.exists()

    def test_scan_cleans_stale_dead_writers_within_per_scan_budget(self, tmp_path):
        """单拍体检/清理有上限（8 个），但每拍都有进展，几拍内清空。

        上限存在的理由：升级后首拍面对上百个历史死文件时不砸盘，也不为全部
        历史文件每拍各做一次进程探活（一次判定 = 3 次系统调用）。
        """
        old = time.time() - 3 * 24 * 3600
        stale = []
        for i in range(20):
            path = tmp_path / f"dsh-{500000 + i:08d}.jsonl"   # pid 远超平台上限 = 已死
            path.write_text("{}\n", encoding="utf-8")
            os.utime(path, (old + i, old + i))
            stale.append(path)
        fresh = tmp_path / "dsh-60000000.jsonl"
        fresh.write_text('{"event":"live"}\n', encoding="utf-8")

        tailer = DirGlobTailer(tmp_path, scan_interval=0.0)
        tailer.read_new_lines()
        remaining = [p for p in stale if p.exists()]
        assert len(remaining) == 20 - agent_link._BRIDGE_STALE_CLEANUP_LIMIT, (
            f"单拍清理不得超过 {agent_link._BRIDGE_STALE_CLEANUP_LIMIT} 个，"
            f"实际剩 {len(remaining)}"
        )
        assert str(fresh) in tailer._tailers, "活会话文件任何一拍都不得被挤掉"

        for _ in range(3):                     # 每拍 8 个 → 3 拍内清空
            tailer.read_new_lines()
        assert [p for p in stale if p.exists()] == []
        assert str(fresh) in tailer._tailers


def _raiser(exc):
    """构造一个"调用即抛该异常"的桩（注入 OS 边界用）。"""
    def _raise(*_args, **_kwargs):
        raise exc
    return _raise


class _FakeKernel32:
    """假 ``kernel32``：只实现探活用到的四个入口（不碰真进程，跨平台可注入）。"""

    def __init__(self, *, open_result=0, last_error=0, exit_code=0, exit_code_ok=True):
        self._open_result = open_result
        self._last_error = last_error
        self._exit_code = exit_code
        self._exit_code_ok = exit_code_ok
        self.closed: list = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - Win32 名字
        self.opened_args = (access, inherit, pid)
        return self._open_result

    def GetLastError(self):  # noqa: N802
        return self._last_error

    def GetExitCodeProcess(self, handle, ref):  # noqa: N802
        if not self._exit_code_ok:
            return 0
        ref._obj.value = self._exit_code
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


class TestBridgeWriterLivenessProbe:
    """桥写者探活的三态判定：权限拒绝**不是**死亡（Win err5 / POSIX EPERM 分开钉死）。

    缺陷（多实例隔离批复审，2026-10）：``_bridge_writer_alive`` 直接复用
    ``slot_manager.pid_alive`` 的 bool——"权限拒绝"与"没有这个 pid"被压成同一个
    False。Windows ``OpenProcess`` 面对受保护进程/其他用户/更高完整性级别的目标回
    ``ERROR_ACCESS_DENIED(5)``，POSIX ``kill(pid, 0)`` 回 ``EPERM``：目标**活着**，
    只是探不动。判死会退役活写者的忙态与待处理交互（用户还在等的气泡被撤掉），
    所以这两类必须落成判定失败（``None``）：只有"没有这个 pid"（Win 87 / POSIX
    ESRCH）与"进程已退出"（退出码非 STILL_ACTIVE）才是确定已死。

    ``slot_manager.pid_alive`` 的布尔语义被多处消费，本批**不动它**：三态口径只落在
    本模块自己的探活路径上（``_bridge_writer_alive``）。
    """

    def test_windows_error_codes_are_distinguished(self, monkeypatch):
        """Win err5（拒绝访问）判不了，Win err87（参数无效 = 没有这个 pid）才是死。"""
        cases = [
            (5, None, "ERROR_ACCESS_DENIED：进程存在但探不动"),
            (87, False, "ERROR_INVALID_PARAMETER：没有这个 pid"),
            (0, None, "错误码未知（句柄为空但没设错误码）：判不了"),
            (1231, None, "网络/其他错误：判不了"),
        ]
        for winerr, expected, why in cases:
            kernel32 = _FakeKernel32(open_result=0, last_error=winerr)
            monkeypatch.setattr(agent_link, "_win32_kernel32", lambda k=kernel32: k)
            got = agent_link._probe_writer_pid_windows(4242)
            assert got is expected, f"winerr={winerr}（{why}）得到 {got!r}"

    def test_windows_exit_code_decides_not_handle(self, monkeypatch):
        """句柄打得开 ≠ 活着（同 ``pid_alive`` 的既有口径）：退出码说了算，句柄必关。"""
        alive = _FakeKernel32(open_result=0x1234, exit_code=259)      # STILL_ACTIVE
        monkeypatch.setattr(agent_link, "_win32_kernel32", lambda: alive)
        assert agent_link._probe_writer_pid_windows(4242) is True
        assert alive.closed == [0x1234], "每个打开的句柄都必须关上"
        # 只申请最小查询权限，且除这四个入口外不碰任何 kernel32 API（不为探活提权：
        # 任何调令牌/权限的 API 都不在这份替身上，一旦被调用本用例立刻红）
        assert alive.opened_args[0] == agent_link._PROCESS_QUERY_LIMITED_INFORMATION

        exited = _FakeKernel32(open_result=0x1234, exit_code=0)
        monkeypatch.setattr(agent_link, "_win32_kernel32", lambda: exited)
        assert agent_link._probe_writer_pid_windows(4242) is False, "退出码不是 STILL_ACTIVE = 已退出"
        assert exited.closed == [0x1234]

        broken = _FakeKernel32(open_result=0x1234, exit_code_ok=False)
        monkeypatch.setattr(agent_link, "_win32_kernel32", lambda: broken)
        assert agent_link._probe_writer_pid_windows(4242) is None, "读不到退出码：判不了，不许判死"
        assert broken.closed == [0x1234], "失败路径同样必须关句柄"

    def test_posix_esrch_is_confirmed_dead_eperm_is_undecidable(self, monkeypatch):
        """POSIX：只有 ESRCH 是确定已死；EPERM/EACCES/未知 errno 一律判不了。"""
        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(ProcessLookupError(errno.ESRCH, "No such process")))
        assert agent_link._probe_writer_pid_posix(4242) is False

        for exc, why in ((PermissionError(errno.EPERM, "Operation not permitted"),
                          "EPERM：进程存在但无权发信号"),
                         (PermissionError(errno.EACCES, "Permission denied"),
                          "EACCES：同权限拒绝"),
                         (OSError(errno.EINVAL, "Invalid argument"), "未知 errno")):
            monkeypatch.setattr(agent_link, "_os_kill_signal_0", _raiser(exc))
            got = agent_link._probe_writer_pid_posix(4242)
            assert got is None, f"{why}：探不动不等于死，得到 {got!r}"

        monkeypatch.setattr(agent_link, "_os_kill_signal_0", lambda pid: None)
        assert agent_link._probe_writer_pid_posix(4242) is True, "没有异常 = 活着"

    def test_dispatch_keeps_three_states_through_injected_boundaries(self, monkeypatch):
        """真分派 + 同时注入两个平台的边界：同一断言在 Windows/Linux/macOS 都成立。

        两个边界都注上是刻意的：真分派走哪个分支，那个分支都已按目标注入。
        """
        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(PermissionError(errno.EPERM, "Operation not permitted")))
        monkeypatch.setattr(agent_link, "_win32_kernel32",
                            lambda: _FakeKernel32(open_result=0, last_error=5))
        assert agent_link._bridge_writer_alive(4242) is None, "权限拒绝必须判不了，绝不许判死"

        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(ProcessLookupError(errno.ESRCH, "No such process")))
        monkeypatch.setattr(agent_link, "_win32_kernel32",
                            lambda: _FakeKernel32(open_result=0, last_error=87))
        assert agent_link._bridge_writer_alive(4242) is False, "没有这个 pid 才是确定已死"

    def test_own_pid_is_alive_through_real_boundary(self):
        """自己的 pid 必须判活（真 OS 边界：Windows OpenProcess / POSIX kill(pid, 0)）。

        这条是``_maybe_probe_dead_writers`` 的安全前提：把"自己"判死就会自我退役。
        """
        assert agent_link._bridge_writer_alive(os.getpid()) is True
        assert agent_link._bridge_writer_alive(0) is None, "无意义的 pid 判不了，不当死"


class TestOwnerFileRetention:
    """写者→文件映射的淘汰策略（纯函数）：**活跃写者永不被淘汰**。

    映射表是存活探活的唯一 pid 来源（``_maybe_probe_dead_writers`` 的候选必须
    "写者还在映射表里"）。旧实现按插入次序 FIFO 淘汰最老条目，会把**正忙/正等用户
    输入**的写者挤出表——那条写者的进程死掉后没有 pid 可探，忙态永远留在桌宠里。
    """

    def test_evicts_only_inactive_oldest_first(self):
        owner_files = OrderedDict([("inst:A", "dsh-1.jsonl"), ("inst:B", "dsh-2.jsonl"),
                                   ("inst:C", "dsh-3.jsonl")])

        evicted = agent_link._evict_inactive_owner_files(owner_files, {"inst:A"}, max_entries=1)

        assert evicted == ["inst:B", "inst:C"], "淘汰只找非活跃的，且从最老的开始"
        assert list(owner_files) == ["inst:A"]

    def test_active_writers_are_kept_over_capacity(self):
        owner_files = OrderedDict([("inst:A", "a"), ("inst:B", "b"), ("inst:C", "c")])

        evicted = agent_link._evict_inactive_owner_files(
            owner_files, {"inst:A", "inst:B", "inst:C"}, max_entries=2)

        assert evicted == []
        assert list(owner_files) == ["inst:A", "inst:B", "inst:C"], \
            "全部活跃时宁可暂时超容量，也不丢活跃写者的探活线索"

    def test_within_capacity_is_noop(self):
        owner_files = OrderedDict([("inst:A", "a")])

        assert agent_link._evict_inactive_owner_files(owner_files, set(), max_entries=8) == []
        assert list(owner_files) == ["inst:A"]


class TestEventStateNormalization:
    def test_known_events_mapping(self):
        assert normalize_event_state("UserPromptSubmit") == "thinking"
        assert normalize_event_state("PreToolUse") == "working"
        assert normalize_event_state("PostToolUse") == "working"
        assert normalize_event_state("Stop") == "attention"
        assert normalize_event_state("SubagentStop") == "attention"
        assert normalize_event_state("PostToolUseFailure") == "error"
        assert normalize_event_state("SessionStart") == "idle"

    def test_explicit_valid_state_override(self):
        assert normalize_event_state("CustomUnknownEvent", explicit_state="thinking") == "thinking"
        # 未知事件 + 非法显式状态：返回空串表示「忽略」，绝不默认当成 working 过度触发
        assert normalize_event_state("CustomUnknownEvent", explicit_state="invalid") == ""
        assert normalize_event_state("CustomUnknownEvent") == ""


# ============================================================================
# 3. AgentLinkManager 管理器与生命周期测试
# ============================================================================
class TestAgentLinkManager:
    def test_default_all_disabled(self, tmp_path):
        """产品默认的 agent_link 形状：开关全关 + 音效配置（减法后无概率门/检测器键）。"""
        cfg = Config(base=tmp_path)
        assert cfg.data["agent_link"] == {
            "dsh": False,
            "claude": False,
            "cursor": False,
            "opencode": False,
            "custom_agents": [],
            "sound_enabled": False,
            "sound_start_path": "builtin:agent-start",
            "sound_done_path": "builtin:agent-done",
            "sound_error_path": "builtin:agent-error",
            "sound_volume": 0.65,
            "sound_cooldown_seconds": 2.0,
            "sound_start_enabled": True,
            "sound_done_enabled": True,
            "sound_error_enabled": True,
        }

        mgr = AgentLinkManager(None, cfg)
        for key, mon in mgr.monitors.items():
            assert mon.is_running() is False

    def test_removed_agent_link_keys_dropped_on_clean(self, tmp_path):
        """减法退役键（概率门/卡住/行为模式/探索看门狗及旧前身）读入即丢弃，不写回。"""
        cfg = Config(base=tmp_path)
        cfg.data["agent_link"] = {
            "dsh": True,
            "report_gates": {"state": 0.0},
            "notify_done": False,
            "report_probability": 30,
            "stuck_detect": True,
            "stuck_worried_threshold": 9,
            "pattern_detect": True,
            "pattern_w6_control": 9,
            "exploration_watchdog_enabled": True,
            "exploration_watchdog_warning_threshold": 9,
        }
        cleaned = _clean_agent_link_data(cfg.data["agent_link"])
        for dead in ("report_gates", "notify_done", "report_probability",
                     "stuck_detect", "stuck_worried_threshold",
                     "pattern_detect", "pattern_w6_control",
                     "exploration_watchdog_enabled", "exploration_watchdog_warning_threshold"):
            assert dead not in cleaned, f"退役键 {dead} 不得存活"
        assert cleaned["dsh"] is True

    def test_enable_disable_and_pause_resume(self, tmp_path):
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)

        mgr.set_enabled("cursor", True)
        assert cfg.data["agent_link"]["cursor"] is True
        assert mgr.monitors["cursor"].is_running() is True

        # 暂停（桌宠隐藏）
        mgr.pause()
        assert mgr.monitors["cursor"].is_running() is False

        # 恢复
        mgr.resume()
        assert mgr.monitors["cursor"].is_running() is True

        # 关闭
        mgr.set_enabled("cursor", False)
        assert mgr.monitors["cursor"].is_running() is False

    def test_claude_hooks_permission_prompt(self, tmp_path, monkeypatch):
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)

        # 模拟用户拒绝
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
        ok = mgr.set_enabled("claude", True)
        assert ok is False
        assert cfg.data["agent_link"]["claude"] is False

        # 模拟用户同意
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(ClaudeCodeMonitor, "install_hooks", lambda f: True)
        ok2 = mgr.set_enabled("claude", True)
        assert ok2 is True
        assert cfg.data["agent_link"]["claude"] is True


# ============================================================================
# ============================================================================
class TestRealFileTailEndToEnd:
    def test_cursor_multi_file_tail(self, tmp_path):
        app = QApplication.instance() or QApplication([])

        # 模拟 Cursor transcripts 目录
        cursor_dir = tmp_path / ".cursor" / "projects" / "proj1" / "agent-transcripts"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = cursor_dir / "session1.jsonl"
        transcript_file.touch()

        cfg_dir = tmp_path / "dsh-config"
        cfg_dir.mkdir(parents=True, exist_ok=True)

        received_states = []
        mon = CursorMonitor(cfg_dir, base_dir=tmp_path / ".cursor" / "projects")
        mon.state_changed.connect(lambda k, s: received_states.append((k, s)))

        mon.start()
        mon._poll()  # 初始化 tailer

        # 模拟 Cursor 追加写入事件行
        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PreToolUse"}) + "\n")
            f.write(json.dumps({"type": "Stop"}) + "\n")

        mon._poll()

        assert len(received_states) == 2
        assert received_states[0] == ("cursor", "working")
        assert received_states[1] == ("cursor", "attention")

        mon.stop()

    def test_agent_state_triggers_pet_action(self, tmp_path):
        app = QApplication.instance() or QApplication([])

        switched_anims = []
        bubbles = []

        class DummyPetWindow:
            def __init__(self):
                self.cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
                self.idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched_anims.append(name)

            def request_link_anim(self, name):
                switched_anims.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched_anims.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        win = DummyPetWindow()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)  # 测试关闭节流，逐个验证状态映射

        # 模拟 Agent 状态分发（busy 动作池轮换：写代码→吃Token）
        mgr._on_agent_state("claude", "thinking")
        assert "写代码" in switched_anims

        mgr._on_agent_state("claude", "working")
        assert "吃Token" in switched_anims

        mgr._on_agent_state("claude", "attention")
        # busy 后的 attention（Claude Stop=回合结束）不再立即弹「确认」气泡，
        # 改由完成确认流程接管（防双气泡）；确认后弹中性完成文案
        assert not any("确认一下" in b for b in bubbles)
        assert "claude" in mgr._done_pending
        mgr._fire_done("claude")
        assert any("已停止" in b for b in bubbles)

        # 非 busy 后独立出现的 attention 仍立即提醒
        mgr._on_agent_state("dsh", "attention")
        assert any("需要你确认" in b for b in bubbles)


# ============================================================================
# 5. 终审修复回归：hooks 格式 / 半行缓冲 / 去抖节流 / 菜单回弹
# ============================================================================
class TestClaudeHooksFormat:
    def test_hooks_written_as_matcher_arrays(self, tmp_path, monkeypatch):
        """hooks 必须是数组对象格式（matcher + hooks[{type,command}]），不是字符串。"""
        settings = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        assert ClaudeCodeMonitor.install_hooks(events_file) is True

        data = json.loads(settings.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        assert isinstance(hooks, dict)
        for name in ClaudeCodeMonitor.HOOK_EVENTS:
            entries = hooks[name]
            assert isinstance(entries, list), f"{name} 必须是数组"
            group = entries[-1]
            assert isinstance(group, dict) and "hooks" in group
            cmd = group["hooks"][0]
            assert cmd["type"] == "command"
            assert "claude_event_hook" in cmd["command"]
            assert name in cmd["command"]
            # 打包版兼容：命令不得依赖 sys.executable -c 内联执行
            assert ' -c "' not in cmd["command"]
        # 脚本文件已落地
        assert list(events_file.parent.glob("claude_event_hook.*"))

    def test_install_is_idempotent_and_preserves_user_hooks(self, tmp_path, monkeypatch):
        """重复安装不产生重复条目；用户自己的 hooks 原样保留（包括命令碰巧含 claude_event_hook 的情况）。"""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]},
                {"matcher": "Special", "hooks": [{"type": "command", "command": "custom_claude_event_hook_run"}]},
            ]},
            "other_key": 1,
        }), encoding="utf-8")
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        ClaudeCodeMonitor.install_hooks(events_file)  # 重复安装

        data = json.loads(settings.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        ours = [g for g in entries if g.get("x-dsh-pet") is True]
        theirs1 = [g for g in entries if "my-own-hook" in json.dumps(g)]
        theirs2 = [g for g in entries if "custom_claude_event_hook_run" in json.dumps(g)]
        assert len(ours) == 1  # 幂等
        assert len(theirs1) == 1  # 用户的保留
        assert len(theirs2) == 1  # 名字撞车的用户条目也保留
        assert data["other_key"] == 1

    def test_uninstall_removes_only_ours(self, tmp_path, monkeypatch):
        settings = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {"Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "user-cmd"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "user_claude_event_hook_cmd"}]},
            ]},
        }), encoding="utf-8")

        events_file = tmp_path / "agent-events" / "claude.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        ClaudeCodeMonitor.install_hooks(events_file)
        assert ClaudeCodeMonitor.uninstall_hooks() is True

        data = json.loads(settings.read_text(encoding="utf-8"))
        stop_entries = data["hooks"]["Stop"]
        assert all(g.get("x-dsh-pet") is not True for g in stop_entries)
        assert any("user-cmd" in json.dumps(g) for g in stop_entries)
        assert any("user_claude_event_hook_cmd" in json.dumps(g) for g in stop_entries)
        # 我们独占的事件键整个移除
        assert "PreToolUse" not in data["hooks"]


class TestByteOffsetTailerPartialLine:
    def test_partial_line_buffered_not_dropped(self, tmp_path):
        """半行（无换行结尾）必须缓冲等待拼接，绝不能当整行解析或丢弃。"""
        fpath = tmp_path / "t.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 初始化

        # 写入半行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('{"event": "PreTool')
        assert tailer.read_new_lines() == []  # 半行不产出

        # 补全该行
        with open(fpath, "a", encoding="utf-8") as f:
            f.write('Use"}\n{"event": "Stop"}\n')
        lines = tailer.read_new_lines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "PreToolUse"
        assert json.loads(lines[1])["event"] == "Stop"

    def test_chunk_boundary_mid_line(self, tmp_path):
        """读取窗口恰好切在行中间时，半行拼接依然正确。"""
        fpath = tmp_path / "t.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath, max_chunk_bytes=16)
        tailer.read_new_lines()

        line1 = '{"event": "PreToolUse"}\n'  # 24 bytes，跨越 16B 边界
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(line1)
        out = []
        for _ in range(3):
            out.extend(tailer.read_new_lines())
        assert out == [line1.strip()]


class TestAgentStateDebounce:
    def _make_mgr(self, tmp_path):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        switched = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                pass

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path)
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, switched, clock

    def test_same_state_deduped(self, tmp_path):
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        mgr._on_agent_state("claude", "working")
        mgr._on_agent_state("claude", "working")
        assert switched == ["写代码"]  # 只切一次

    def test_throttled_within_interval(self, tmp_path):
        mgr, switched, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 1.0  # 1s < 2s 节流间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["写代码"]  # 被节流
        clock[0] += 2.0  # 超过间隔
        mgr._on_agent_state("claude", "thinking")
        assert switched == ["写代码", "吃Token"]  # 动作池轮换：写代码→吃Token


class TestAgentMenuRebound:
    def test_decline_rolls_back_checkbox(self, tmp_path, monkeypatch):
        """用户拒绝授权后，菜单勾选态必须回滚，不允许 UI 骗人。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)
        try:
            class FakeAction:
                def __init__(self):
                    self.checked = True  # 用户刚勾上
                    self._blocked = []

                def blockSignals(self, b):
                    self._blocked.append(b)

                def setChecked(self, v):
                    self.checked = v

            act = FakeAction()
            win._toggle_agent_link("claude", True, act)
            assert act.checked is False  # 回滚
            assert cfg.data["agent_link"]["claude"] is False  # 配置未开启
        finally:
            # 窗口必须关闭：否则泄漏的真实窗口会在共享事件循环上继续推进动画链，
            # 后续测试 processEvents 时持续拉起 reader 线程（跨测试干扰）。
            win.close()
            win.deleteLater()
            # 处理 deleteLater 投递的 Qt 清理事件，避免窗口的动画/reader 事件泄漏到后续测试。
            app.processEvents()

    def test_bom_prefixed_file_tolerated(self, tmp_path):
        """PowerShell Add-Content -Encoding UTF8 会在新建文件首行写 BOM，
        tailer 必须容忍，否则 Claude hooks 产生的第一条事件永远解析失败。"""
        fpath = tmp_path / "bom.jsonl"
        fpath.touch()
        tailer = ByteOffsetTailer(fpath)
        tailer.read_new_lines()  # 完成初始化（文件须先存在）
        # 外部以带 BOM 的方式重写文件（模拟轮转后首行带 BOM）
        fpath.write_bytes(b"\xef\xbb\xbf" + '{"event": "Stop"}\n'.encode("utf-8"))
        tailer.offset = 0  # 模拟轮转重置
        lines = tailer.read_new_lines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "Stop"


# ============================================================================
# ============================================================================
class TestRealFormatMappers:
    def test_cursor_role_based(self):
        from pet.agent_link import cursor_line_state
        assert cursor_line_state({"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}) == "thinking"
        assert cursor_line_state({"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell"}]}}) == "working"
        assert cursor_line_state({"role": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) == "idle"
        assert cursor_line_state({"random": True}) == ""


    def test_opencode_event_types(self):
        from pet.agent_link import opencode_event_state
        import json as j
        assert opencode_event_state("message.updated.1", j.dumps({"info": {"role": "user"}})) == "thinking"
        assert opencode_event_state("message.updated.1", j.dumps({"info": {"role": "assistant"}})) == ""
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-start"}})) == "working"
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-finish"}})) == "idle"
        assert opencode_event_state("message.part.updated.1", j.dumps({"part": {"type": "step-finish", "reason": "tool-calls"}})) == ""
        assert opencode_event_state("session.updated.1", "{}") == ""
        assert opencode_event_state("message.part.updated.1", "not json") == ""


class TestOpenCodeSqliteTail:
    def test_sqlite_incremental_poll(self, tmp_path):
        """OpenCode 监视器：自建 sqlite event 表，验证 backfill 防护 + 增量轮询。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])

        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("INSERT INTO event VALUES ('s1', 1, 'session.created.1', '{}')")
        db.commit()
        db.close()

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received = []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        mon._poll()  # 首次 = backfill，不产生事件
        assert received == []

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.updated.1', '{\"info\":{\"role\":\"user\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 3, 'message.part.updated.1', '{\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('s1', 4, 'session.updated.1', '{}')")
        db.commit()
        db.close()

        mon._poll()
        assert received == ["thinking", "working"]  # session.updated 被忽略
        mon.stop()

    def test_database_replacement_restarts_backfill(self, tmp_path):
        """OpenCode 重建数据库后不沿用旧 rowid，也不重放新库历史行。"""
        import os
        import sqlite3
        from pet.agent_link import OpenCodeMonitor

        db_path = tmp_path / "opencode.db"

        def make_db(path, rows):
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
            for seq, event_type, data in rows:
                db.execute("INSERT INTO event VALUES ('s1', ?, ?, ?)", (seq, event_type, data))
            db.commit()
            db.close()

        make_db(db_path, [(1, "session.created.1", "{}")])
        mon = OpenCodeMonitor(tmp_path / "cfg", db_path=db_path)
        received = []
        mon.state_changed.connect(lambda _agent, state: received.append(state))
        mon._poll()

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.updated.1', '{\"info\":{\"role\":\"user\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["thinking"]

        replacement = tmp_path / "opencode-new.db"
        make_db(replacement, [(1, "message.updated.1", '{"info":{"role":"user"}}')])
        os.replace(replacement, db_path)
        mon._poll()  # 新库既有内容只用于 backfill，不能重放
        assert received == ["thinking"]

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 2, 'message.part.updated.1', '{\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["thinking", "working"]


class TestCooldownUnits:
    def test_seconds_and_minutes_conversion(self, tmp_path):
        """冷却间隔秒/分钟双单位：45 秒应存为 0.75 分钟。"""
        from PySide6.QtWidgets import QApplication
        from pet.modern_settings_dialog import ModernSettingsDialog

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        dlg = ModernSettingsDialog(cfg)
        try:
            if not hasattr(dlg, "pro_cooldown_unit"):
                import pytest
                pytest.skip("非 Windows 无主动识屏设置组")

            # 切到秒，设 45 秒
            dlg.pro_cooldown_unit.setCurrentIndex(1)
            dlg.pro_cooldown_spin.setValue(45)
            assert abs(dlg._pro_cooldown_minutes() - 0.75) < 1e-9

            # 切回分钟应自动换算显示
            dlg.pro_cooldown_unit.setCurrentIndex(0)
            assert abs(dlg.pro_cooldown_spin.value() - 0.75) < 1e-9

            # 保存后配置为分钟值
            dlg._save()
            cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
            assert abs(cfg.data["proactive_screen"]["cooldown_minutes"] - 0.75) < 1e-9
        finally:
            dlg.close()
            dlg.deleteLater()


class TestMultiInstanceGlobalState:
    def test_disable_skips_uninstall_when_other_instance_enabled(self, tmp_path, monkeypatch):
        """其他实例仍开启某 Agent 联动时，本实例关闭不得卸载全局 hooks/插件。"""
        from pet.agent_link import AgentLinkManager, ClaudeCodeMonitor

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)

        # 直接置配置为开启（模拟另一个实例正在用）
        cfg.set("agent_link", {"claude": True})
        cfg.dir.mkdir(parents=True, exist_ok=True)
        (cfg.dir / "config-pet2.json").write_text(
            json.dumps({"agent_link": {"claude": True}}), encoding="utf-8"
        )

        calls = []
        monkeypatch.setattr(ClaudeCodeMonitor, "uninstall_hooks", classmethod(lambda cls: calls.append(1) or True))

        ok = mgr.set_enabled("claude", False)
        assert ok is True
        assert calls == []  # 另一个实例还在用 → 不卸载


class TestModernSettingsProactivePage:
    def test_proactive_page_save_roundtrip(self, tmp_path, monkeypatch):
        """现代设置面板「主动识屏」页：控件→保存→配置 回路（生产实际使用的设置页）。"""
        import sys
        if sys.platform != "win32":
            import pytest
            pytest.skip("主动识屏页仅 Windows")
        from PySide6.QtWidgets import QApplication
        from pet.modern_settings_dialog import ModernSettingsDialog
        import pet.modern_settings_dialog as settings_mod

        app = QApplication.instance() or QApplication([])
        monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
        monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda v: None)

        cfg = Config(base=tmp_path)
        dlg = ModernSettingsDialog(cfg, include_ai=True)
        dlg2 = None
        try:
            assert hasattr(dlg, "pro_enabled_check"), "主动识屏控件未构建"

            # 设置一组值并保存
            dlg.pro_enabled_check.setChecked(True)
            dlg.pro_whitelist_edit.setPlainText("code.exe\ntitle:*会议*")
            dlg.pro_cap_spin.setValue(42)
            dlg._save()
            cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见

            pro = cfg.data["proactive_screen"]
            assert pro["enabled"] is True
            assert pro["whitelist"] == ["code.exe", "title:*会议*"]
            assert pro["daily_cap"] == 42
            # 未暴露字段保留
            assert "change_threshold" in pro

            # 再开一次：读回的值应与保存一致
            dlg2 = ModernSettingsDialog(cfg, include_ai=True)
            assert dlg2.pro_enabled_check.isChecked() is True
            assert dlg2.pro_cap_spin.value() == 42
        finally:
            dlg.close()
            dlg.deleteLater()
            if dlg2 is not None:
                dlg2.close()
                dlg2.deleteLater()


# ============================================================================
# 12. DSH profile 枚举（桥接插件安装/卸载目标）
# ============================================================================
class TestDshProfileEnumeration:
    """_real_profiles 只认含 package.json 的目录，过滤 node_modules 等杂项残留。"""

    def test_real_profiles_filters_node_modules_and_empty_dirs(self, tmp_path, monkeypatch):
        # issue #23：~/.dsh/profiles 下可能有 pnpm 产生的 node_modules 等杂项目录，
        # 安装/卸载桥接插件时只能枚举真实 profile（含 package.json 的目录）。
        dsh_home = tmp_path / "dsh-home"
        profiles = dsh_home / "profiles"
        for name in ("web", "headless"):
            profile = profiles / name
            profile.mkdir(parents=True)
            (profile / "package.json").write_text("{}", encoding="utf-8")
        (profiles / "node_modules").mkdir()
        (profiles / "empty-dir").mkdir()
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", dsh_home)
        assert [p.name for p in agent_link._real_profiles()] == ["headless", "web"]


# ============================================================================
# 13. Agent 联动气泡测试（开始干活 / 完成通知 / 冷却 / 抖动 / 占用延后）
# ============================================================================
class TestAgentLinkBubbles:
    def _make_mgr(self, tmp_path, agent_link_cfg=None):
        app = QApplication.instance() or QApplication([])

        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_idle(self):
                # 与真实 window 行为对齐：清待播并回待机
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        win = DummyWin()
        win.switched = switched  # 供断言动画切换
        cfg = Config(base=tmp_path)
        if agent_link_cfg is not None:
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}
            cfg.save()

        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_working_to_idle_done_bubble(self, tmp_path):
        """1. working→idle 后，mgr._done_pending 里出现 'dsh' 的定时器；
        手动调 mgr._fire_done('dsh') 后 fake win 的 show_bubble 收到含「干完活啦」的文本。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        assert "dsh" not in mgr._done_pending

        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        mgr._fire_done("dsh")
        assert any("已完成本轮任务" in b for b in bubbles)

    def test_start_bubble_once_per_busy_run(self, tmp_path):
        """thinking→working 连续两个 busy 状态只弹一次开始气泡
        （第二次 prev_raw 已是 busy 不弹）；idle 后再 working 重新弹。"""
        mgr_on, win_on, bubbles_on, clock_on = self._make_mgr(tmp_path)
        mgr_on._on_agent_state("dsh", "thinking")
        assert len(bubbles_on) == 1
        # legacy 内置预设 thinking 首句（此前为 DSH 专属原文案「大肥鱼正在深度思考」）
        assert "DSH 正在思考" in bubbles_on[0]

        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        # 连续 busy 状态，thinking→working 互跳不重复弹
        assert len(bubbles_on) == 1

        # idle 后再 working → 弹「开始干活」气泡（legacy 预设 start 首句）
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "idle")
        clock_on[0] += 3.0
        mgr_on._on_agent_state("dsh", "working")
        assert len(bubbles_on) == 2
        assert "已开始执行任务" in bubbles_on[1]

    def test_thinking_text_custom_override(self, tmp_path):
        """自定义 thinking 文案：agent_link.thinking_text 非空时优先使用，支持 {name} 占位符。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path,
            agent_link_cfg={"thinking_text": "{name} 大脑飞速运转中……"},
        )
        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == 1
        assert "DSH 大脑飞速运转中……" == bubbles[0]
        assert "深度思考" not in bubbles[0]

        # 空字符串 → 回退默认（legacy 内置预设 thinking 首句）
        mgr2, win2, bubbles2, _ = self._make_mgr(
            tmp_path / "b", agent_link_cfg={"thinking_text": ""}
        )
        mgr2._on_agent_state("dsh", "thinking")
        assert "DSH 正在思考" in bubbles2[0]

    def test_thinking_uses_agent_delta_preset(self, tmp_path):
        """thinking 文案走统一预设 agents delta：dsh 有覆盖时命中，其它 Agent 回退 global。

        ticket 02/05：dialogue_mode=custom + {global, agents} 双层。
        """
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path
        )
        cfg = mgr.cfg
        cfg.data["dialogue_mode"] = "custom"
        cfg.data["dialogue_phrases"] = {
            "global": {"thinking": ["{name} 全局思考……"]},
            "agents": {
                "dsh": {"thinking": ["{name} 大肥鱼深度思考中……"]},
                "claude": {},
            },
        }
        cfg.save()

        mgr._on_agent_state("dsh", "thinking")
        assert len(bubbles) == 1
        assert "大肥鱼深度思考中" in bubbles[0]
        assert "全局思考" not in bubbles[0]

        # claude 无 agents 覆盖 → 回退 global
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "thinking")
        assert any("Claude Code 全局思考" in b for b in bubbles)

    def test_jitter_cancel_done_check(self, tmp_path):
        """4. working→idle→working 抖动：idle 后 pending 存在，
        再来 working 后 pending 被清空（_cancel_done_check 生效），此后 _fire_done 不弹气泡。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending

        clock[0] += 3.0
        mgr._on_agent_state("dsh", "working")
        assert "dsh" not in mgr._done_pending

        # 此时尝试调用 _fire_done，因为当前 last_raw 是 working（busy 状态），不弹完成气泡
        count = len(bubbles)
        mgr._fire_done("dsh")
        assert len(bubbles) == count, "busy 中 _fire_done 不得追加气泡"

    def test_done_cooldown(self, tmp_path):
        """5. 冷却：clock 前进不足 5 秒时第二次 _fire_done 被 _done_cooldown 抑制；
        前进超过 5 秒后正常弹。气泡计数含开头一条「开始干活」（start 分支）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 2
        assert "已开始执行任务" in bubbles[0]  # start 预设首句
        assert "已完成本轮任务" in bubbles[1]  # done.success 预设首句

        # 再次进入 busy -> idle
        clock[0] += 3.0  # 3s < 5s 冷却
        mgr._on_agent_state("dsh", "working")
        clock[0] += 1.0  # 累计 4s < 5s
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(bubbles) == 3  # 新增一条 start；done 被冷却抑制

        # 前进超过 5 秒（从第一次 _fire_done 时刻 1000.0 起算，此时 1004.0 + 2.0 = 1006.0 > 1000.0 + 5.0）
        clock[0] += 2.0
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        # 第三轮的 working 被 2s 换帧节流（距上轮 idle 仅 1s）吞掉 start 气泡，
        # 故总数 4 = start + done + start + done
        assert len(bubbles) == 4
        assert "执行完成" in bubbles[-1]  # 第二次 done.success 轮换到第二句

    def test_done_swallowed_by_cooldown_releases_cost_tracking(self, tmp_path, monkeypatch):
        """P1：完成气泡被冷却掐掉时必须丢弃消费统计状态。

        回归背景：``_fire_done`` 的四条早退里只有"窗口隐藏"那条调了
        ``_cost.abort``；冷却/概率门这两条会把 agent 永久留在 ``_busy`` 里，
        此后它每次 ``begin`` 都被判成"有别的会话在跑"——金额气泡永远挂
        「（含其他会话）」，自己本轮的金额也不再显示。

        余额结果由真实槽 ``_on_cost_balance``（后台查询回主线程的信号处理）
        注入，只有网络边界不真跑；其余全程走 ``_on_agent_state``/``_fire_done``
        的真实入口。
        """
        # 钥匙串边界：不让测试进程真去读 keyring（有 key 会起线程打真接口）。
        monkeypatch.setattr(Config, "resolve_api_key", lambda self, provider: "")
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr.cfg.set("agent_cost_enabled", True)

        # 第一轮：正常完成，把基线/结算链路走完整
        mgr._on_agent_state("dsh", "working")
        mgr._on_cost_balance("dsh", "baseline", 10.00)
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        mgr._on_cost_balance("dsh", "done", 9.90)
        assert bubbles[-1] == "本轮消费 ¥0.10"

        # 第二轮：距上次完成不足 _DONE_COOLDOWN_S 又结束 → 冷却早退
        clock[0] += 1.0
        mgr._on_agent_state("dsh", "working")
        mgr._on_cost_balance("dsh", "baseline", 10.00)
        assert mgr._cost.is_tracking("dsh") is True
        clock[0] += 2.0
        mgr._on_agent_state("dsh", "idle")
        count = len(bubbles)
        mgr._fire_done("dsh")
        assert len(bubbles) == count, "冷却期内不得弹完成气泡"
        assert mgr._cost.is_tracking("dsh") is False, (
            "完成被冷却掐掉后必须一并丢弃消费统计状态，否则该 agent 永久滞留 _busy"
        )

        # 第三轮：同一 agent 再次 begin → 不得被误判成"有别的会话在跑"
        clock[0] += 10.0
        mgr._on_agent_state("dsh", "working")
        mgr._on_cost_balance("dsh", "baseline", 10.00)
        clock[0] += 1.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        mgr._on_cost_balance("dsh", "done", 9.90)
        assert bubbles[-1] == "本轮消费 ¥0.10", f"不得误挂并发标注: {bubbles[-1]}"

    def test_reentry_within_confirm_window_not_marked_concurrent(self, tmp_path, monkeypatch):
        """P1：「idle → 800ms 确认窗口内回忙」的重入不经过任何结束路径
        （_cancel_done_check 直接停掉确认定时器，_fire_done 根本不执行），
        begin() 必须把还留在 _busy 里的自己排除出并发判定——否则同一 Agent
        单人会话也会被误标「（含其他会话）」。"""
        monkeypatch.setattr(Config, "resolve_api_key", lambda self, provider: "")
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr.cfg.set("agent_cost_enabled", True)

        # 第一轮干活 → idle（确认定时器挂着，但不手动 _fire_done——模拟 800ms
        # 窗口内就被下一段 working 打断，定时器被 _cancel_done_check 停掉）
        mgr._on_agent_state("dsh", "working")
        mgr._on_cost_balance("dsh", "baseline", 10.00)
        clock[0] += 2.0
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 0.3  # < 800ms 确认窗口
        mgr._on_agent_state("dsh", "working")  # 重入：begin() 时 _busy 还留着自己
        mgr._on_cost_balance("dsh", "baseline", 10.00)
        assert mgr._cost._saw_concurrent is False, "同一 Agent 重入不得算并发"

        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        mgr._on_cost_balance("dsh", "done", 9.90)
        assert bubbles[-1] == "本轮消费 ¥0.10", f"不得误挂并发标注: {bubbles[-1]}"

    def test_error_during_busy_done_bubble_text(self, tmp_path):
        """6. busy 期间出现 error 再 idle：完成气泡文案含「自己看一眼」而不是「干完活啦」。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "error")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")

        # error 状态本身不立即弹气泡（由完成流程接管，防双气泡）；
        # 完成气泡应走 done.attention 预设（中性收尾），不误说成功完成
        done_bubbles = [b for b in bubbles if "已停止" in b]
        assert len(done_bubbles) == 1
        assert not any("已完成本轮任务" in b or "执行完成" in b for b in bubbles)

    def test_bubble_busy_until_occupancy(self, tmp_path, monkeypatch):
        """7. _show_link_bubble 在 win._bubble_busy_until 为未来时间时：
        important=False 直接丢弃；important=True 时不立即弹（走 QTimer.singleShot 延后重试，测试里只需断言没有立即调用 show_bubble）。

        回归锚点（F1）：hold_bubble 写 time.monotonic() 域（真实 window 行为），
        _show_link_bubble 必须用同域比较——曾误用 time.time()（epoch 秒）导致
        门禁恒失效。stub 以 monotonic 未来时刻模拟占用。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        win._bubble_busy_until = time.monotonic() + 100.0

        # important=False 丢弃
        mgr._show_link_bubble("普通消息", important=False)
        assert bubbles == []

        # important=True 走 singleShot 延后重试，不立即调用 show_bubble
        mgr._show_link_bubble("重要消息", important=True)
        assert bubbles == []

    def test_busy_to_attention_counts_as_done(self, tmp_path):
        """8. Claude 风格：working→attention(Stop) 进入完成确认，不弹立即提醒，
        确认后弹完成气泡（因见过 attention 用中性文案）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "attention")
        assert "claude" in mgr._done_pending  # 进入完成确认
        assert not any("看一眼" in b for b in bubbles), "不弹立即 attention 气泡（防双气泡）"

        mgr._fire_done("claude")
        assert any("已停止" in b for b in bubbles)
        assert not any("已完成本轮任务" in b or "执行完成" in b for b in bubbles)

    def test_standalone_attention_immediate_bubble(self, tmp_path):
        """9. 非 busy 后独立出现的 attention：立即提醒，不进完成流程。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "attention")
        assert "claude" not in mgr._done_pending
        assert any("确认一下" in b for b in bubbles)

    def test_done_restores_idle_anim_unless_others_busy(self, tmp_path):
        """10. 完成确认后恢复待机动画（Claude 没有 idle 事件，靠这步回待机）；
        另有 Agent 在忙时不恢复（避免顶掉对方的工作动画）。done 门开 1.0：
        恢复待机发生在门判定之后，门关着就永远走不到。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("claude", "working")
        clock[0] += 3.0
        mgr._on_agent_state("claude", "idle")
        mgr._fire_done("claude")
        assert win.switched[-1] == "待机呼吸"  # 恢复待机
        assert mgr._last_applied["claude"][0] == "idle"

        # 另一 Agent 在忙：不恢复
        mgr2, win2, bubbles2, clock2 = self._make_mgr(tmp_path)
        mgr2._on_agent_state("claude", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("dsh", "working")
        clock2[0] += 3.0
        mgr2._on_agent_state("claude", "idle")
        switched_before = len(win2.switched)
        mgr2._fire_done("claude")
        assert len(win2.switched) == switched_before  # 没有切回待机

    def test_opencode_step_finish_tool_calls_no_done(self, tmp_path):
        """回归（PR57 合并时丢失的 main 侧用例）：opencode step-finish
        (reason=tool-calls)（派 task 子代理后主代理停笔等待）不触发完成确认——
        不产出 idle 状态 → 800ms 确认不排程；step-finish(reason=stop) 才是
        真结束 → 排程并出完成气泡。产品逻辑（opencode_event_state 的
        tool-calls→""）仍存在，恢复端到端守卫。"""
        import json as j

        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr._on_agent_state("dsh", "working")

        # 子代理长跑期间：tool-calls 维持现状，确认窗口不排程
        state = opencode_event_state("message.part.updated.1",
                                     j.dumps({"part": {"type": "step-finish", "reason": "tool-calls"}}))
        assert state == ""
        if state:  # 与 agent_link._poll 的空状态跳过逻辑一致
            mgr._on_agent_state("dsh", state)
        clock[0] += 3.0
        assert "dsh" not in mgr._done_pending
        assert not any("已完成" in b or "执行完成" in b for b in bubbles), "不得弹完成气泡"

        # 子代理回注、整轮真结束：stop → idle → 排程 → 完成气泡恰一次
        state = opencode_event_state("message.part.updated.1",
                                     j.dumps({"part": {"type": "step-finish", "reason": "stop"}}))
        assert state == "idle"
        mgr._on_agent_state("dsh", state)
        assert "dsh" in mgr._done_pending
        mgr._fire_done("dsh")
        assert any("已完成" in b for b in bubbles), f"应弹完成气泡: {bubbles}"


    def test_hidden_pet_redirects_feedback_bubble_to_island(self, tmp_path):
        """桌宠隐藏时联动反馈气泡改道灵动岛反馈面（不再静默丢弃）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        redirected = []

        def fake_redirect(text, subtitle="", duration_ms=3200):
            redirected.append((text, duration_ms))
            return True

        win.isVisible = lambda: False
        win.hidden_bubble_redirect = fake_redirect

        mgr._show_link_bubble("DSH 开始干活啦～", important=True, duration_ms=4500)

        assert redirected == [("DSH 开始干活啦～", 4500)]
        assert bubbles == []

    def test_hidden_dsh_full_scenario_start_and_done_redirect(self, tmp_path):
        """场景回归：桌宠全程隐藏，DSH 两轮状态轮转——start/thinking/done
        都必须到达改道面（island 反馈气泡），不得静默丢弃。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path)
        redirected = []
        win.isVisible = lambda: False
        win.hidden_bubble_redirect = lambda text, subtitle="", duration_ms=3200: (
            redirected.append((text, duration_ms)) or True)

        # 上一轮收尾：idle（挂起 done 检查并触发）
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 3.0
        for timer in list(mgr._done_pending.values()):
            timer.timeout.emit()
        # 开新对话：thinking → working → 本轮结束 idle
        mgr._on_agent_state("dsh", "thinking")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "working")
        clock[0] += 3.0
        mgr._on_agent_state("dsh", "idle")
        clock[0] += 3.0
        for timer in list(mgr._done_pending.values()):
            timer.timeout.emit()

        texts = [t for t, _ in redirected]
        # thinking 文案（内置预设「DSH 正在思考。」）或 start 文案任一出现即算开始反馈
        assert any("正在思考" in t or "开始干活" in t for t in texts), f"start/thinking 未改道：{texts}"
        assert any("干完活" in t or "已完成本轮任务" in t or "看一眼" in t for t in texts), f"done 未改道：{texts}"
        assert bubbles == []


    def test_visible_pet_keeps_normal_bubble_path(self, tmp_path):
        """桌宠可见时不改道（正常气泡路径不受注入影响）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        redirected = []
        win.hidden_bubble_redirect = lambda *a, **k: redirected.append(a) or True

        mgr._show_link_bubble("普通消息", important=False, duration_ms=2600)

        assert redirected == []
        assert "普通消息" in bubbles

    def test_hidden_pet_without_injection_falls_through(self, tmp_path):
        """无注入（无岛 / no-chat 变体）时不改道，走原 show_bubble 路径
        （真窗上等价于隐藏丢弃——由 show_bubble 自身的可见性守卫负责）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        win.isVisible = lambda: False

        mgr._show_link_bubble("普通消息", important=True, duration_ms=4500)

        assert bubbles == ["普通消息"]


class TestAgentLinkSounds:
    def _make(self, tmp_path, monkeypatch, **sound_cfg):
        class Win:
            _bubble_busy_until = 0.0
            cats = {"acts": ["写代码"]}
            idles = ["待机"]

            def isVisible(self):
                return True

            def request_link_anim(self, _name):
                pass

            def request_link_idle(self):
                pass

            def show_bubble(self, *_args, **_kwargs):
                pass

        cfg = Config(base=tmp_path)
        cfg.data["agent_link"].update({
            "sound_enabled": True,
            **sound_cfg,
        })
        sound = tmp_path / "sound.wav"
        sound.write_bytes(b"RIFF")
        monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda _path: sound)
        calls = []
        monkeypatch.setattr(agent_link, "play_sound", lambda path, volume=1.0: calls.append((path, volume)) or True)
        clock = [100.0]
        mgr = AgentLinkManager(Win(), cfg, min_interval=0.0, clock=lambda: clock[0])
        return mgr, clock, calls

    def test_start_done_error_events_play_at_confirmed_points(self, tmp_path, monkeypatch):
        mgr, clock, calls = self._make(tmp_path, monkeypatch, sound_cooldown_seconds=0.0)
        mgr._on_agent_state("dsh", "working")
        assert len(calls) == 1
        clock[0] += 1
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(calls) == 2
        clock[0] += 1
        mgr._on_agent_state("dsh", "working")
        clock[0] += 1
        mgr._on_agent_state("dsh", "error")
        assert len(calls) == 4, "新忙碌周期应有 start + error 两个事件音效"
        mgr._on_agent_state("dsh", "idle")
        mgr._fire_done("dsh")
        assert len(calls) == 4, "error 周期不能追加 done 音效"

        clock[0] += 1
        mgr._on_agent_state("cursor", "working")
        clock[0] += 1
        mgr._on_agent_state("cursor", "error")
        clock[0] += 1
        mgr._on_agent_state("cursor", "working")
        mgr._on_agent_state("cursor", "idle")
        mgr._fire_done("cursor")
        assert len(calls) == 7, "error 后重试仍属于同一错误周期，不能补 done"

    def test_cooldown_is_global_and_disabled_is_silent(self, tmp_path, monkeypatch):
        mgr, clock, calls = self._make(tmp_path, monkeypatch, sound_cooldown_seconds=2.0)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("claude", "working")
        assert len(calls) == 1
        clock[0] += 2.01
        mgr._on_agent_state("claude", "error")
        assert len(calls) == 2

        mgr.cfg.data["agent_link"]["sound_enabled"] = False
        clock[0] += 3
        mgr._on_agent_state("cursor", "working")
        assert len(calls) == 2


class TestInstallErrorSummary:
    def test_install_bridge_auto_installs_pnpm(self, tmp_path, monkeypatch):
        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        profile = tmp_path / "profiles" / "default"
        profile.mkdir(parents=True)
        manifest = profile / "package.json"
        manifest.write_text("{}", encoding="utf-8")

        pnpm_cli = str(tmp_path / "pnpm.mjs")
        located = iter([None, pnpm_cli, pnpm_cli])
        monkeypatch.setattr(agent_link, "_find_pnpm_cli", lambda: next(located))
        monkeypatch.setattr(agent_link, "_npm_cli", lambda: "C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js")
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(
            agent_link.shutil, "which",
            lambda name: "C:/Program Files/nodejs/node.exe" if name == "node" else None,
        )

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[1] == pnpm_cli:
                manifest.write_text(
                    json.dumps({"dependencies": {agent_link.DSH_PLUGIN_NAME: "file:bridge"}}),
                    encoding="utf-8",
                )
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(agent_link.subprocess, "run", fake_run)

        ok, message = DshMonitor.install_bridge()

        assert ok is True
        assert "1 个 dsh 实例" in message
        assert calls[0][0] == [
            "C:/Program Files/nodejs/node.exe",
            "C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js",
            "install", "-g", "pnpm",
        ]
        assert calls[1][0] == [
            "C:/Program Files/nodejs/node.exe", pnpm_cli, "add", str(plugin),
        ]
        assert json.loads(manifest.read_text(encoding="utf-8"))["dsh"]["profile"]["bundles"] == [
            agent_link.DSH_PLUGIN_NAME
        ]

    def test_find_pnpm_cli_accepts_homebrew_javascript_symlink(self, tmp_path, monkeypatch):
        """回归（PR57 合并时丢失的 main 侧用例）：homebrew 的 pnpm 是
        bin/pnpm → lib/node_modules/pnpm/bin/pnpm.cjs 的符号链接，
        _find_pnpm_cli 必须经 resolve() 落到真实 JS CLI。
        注：Windows 无管理员权限创建 symlink 会失败（WinError 1314），
        该用例在 Linux/macOS CI 执行；本地 Windows 跳过。"""
        try:
            cli = tmp_path / "lib" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs"
            cli.parent.mkdir(parents=True)
            cli.write_text("", encoding="utf-8")
            shim = tmp_path / "bin" / "pnpm"
            shim.parent.mkdir()
            shim.symlink_to(cli)
        except OSError:
            pytest.skip("symlink 权限不可用（Windows 无管理员）")
        monkeypatch.delenv("DSH_PNPM_BIN", raising=False)
        monkeypatch.setattr(agent_link, "_which", lambda name: str(shim) if name == "pnpm" else None)
        assert agent_link._find_pnpm_cli() == str(cli)

    def test_extract_err_pnpm_line(self):
        output = """
        [1/4] Resolving packages...
        node_modules/some-pkg/index.js
        at Object.<anonymous> (file:///C:/Users/test/AppData/Roaming/npm/node_modules/dsh/dist/index.js:2:14)
        ERR_PNPM_FETCH_404 GET https://registry.npmjs.org/not-found: Not Found - 404
        at async install (file:///C:/Users/test/AppData/Roaming/npm/node_modules/dsh/dist/install.js:10:5)
        """
        summary = DshMonitor._summarize_install_error(output)
        assert "ERR_PNPM_FETCH_404" in summary
        assert "at " not in summary
        assert "node_modules" not in summary

    def test_pure_stack_returns_unknown_error(self):
        output = """
        at Object.<anonymous> (file:///C:/Users/test/index.js:1:1)
        at Module._compile (node:internal/modules/cjs/loader:1100:14)
        node_modules/foo/bar.js
        """
        summary = DshMonitor._summarize_install_error(output)
        assert summary == "未知错误"

    def test_long_line_truncated_within_60_chars(self):
        output = (
            "Error: "
            + "A" * 100
            + " something happened at C:\\very\\long\\directory\\path\\to\\file.js"
        )
        summary = DshMonitor._summarize_install_error(output)
        assert len(summary) <= 60
        assert summary.startswith("Error:")


class TestUninstallBridgeWithoutPnpm:
    """没有 pnpm 时关闭联动不能假成功：manifest 里的 link: 残留会指向被删目录。

    2026-09 dsh 事故同型——profile 的 package.json 还挂着 link:<即将删除的程序
    目录>，dsh 启动时解析失败拖垮整个插件树。旧实现 _pnpm_command() is None
    直接 return True，什么都不改。
    """

    def _profile(self, tmp_path, deps, bundles=None):
        profile = tmp_path / "profiles" / "web"
        profile.mkdir(parents=True)
        data = {"dependencies": dict(deps)}
        if bundles is not None:
            data["dsh"] = {"profile": {"bundles": list(bundles)}}
        (profile / "package.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return profile

    def test_no_pnpm_removes_manifest_entries_with_backup(self, tmp_path, monkeypatch):
        """无 pnpm：备份 → 删依赖条目 → 清 bundles → 删链接 → 返回 True。"""
        plugin = tmp_path / "old-build" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}", "keep-me": "^1.0.0"},
            bundles=[agent_link.DSH_PLUGIN_NAME, "other-bundle"],
        )
        linked = profile / "node_modules" / "@dsh-pet" / "bridge"
        linked.mkdir(parents=True)
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True

        manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dependencies"], \
            "link: 残留必须删掉（否则指向即将删除的程序目录）"
        assert manifest["dependencies"]["keep-me"] == "^1.0.0", "无关依赖不许动"
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dsh"]["profile"]["bundles"]
        assert "other-bundle" in manifest["dsh"]["profile"]["bundles"]
        assert list(profile.glob("package.json.bak-*")), "手改前必须备份"
        assert not linked.exists(), "profile 内的插件链接应尽力清理"

    def test_no_pnpm_profile_without_plugin_is_noop(self, tmp_path, monkeypatch):
        """未安装的 profile 幂等成功，且不该产生备份。"""
        profile = self._profile(tmp_path, deps={"keep-me": "^1.0.0"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True
        assert not list(profile.glob("package.json.bak-*"))

    def test_backups_pruned_to_recent_five(self, tmp_path, monkeypatch):
        """manifest 备份只保留最近 5 份：卸载/修复都会持续产 bak，需有清理。"""
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: "link:W:/gone/bridge"},
            bundles=[agent_link.DSH_PLUGIN_NAME],
        )
        fakes = {profile / f"package.json.bak-2026090{i}-12000{i}" for i in range(7)}
        for fake in fakes:
            fake.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: None)

        assert DshMonitor.uninstall_bridge() is True

        backups = sorted(profile.glob("package.json.bak-*"))
        assert len(backups) == 5, f"备份应清理到最近 5 份，现有 {len(backups)}"
        kept = {b.name for b in backups}
        fake_names = {fake.name for fake in fakes}
        assert kept & fake_names, "应保留 7 份旧备份中最新的 4 份"
        assert len(kept - fake_names) == 1, "本次卸载新建的备份必须在其中"
        assert not any(n < "package.json.bak-20260903" for n in kept), "最旧的 3 份必须被清掉"

    def test_with_pnpm_still_uses_pnpm_remove(self, tmp_path, monkeypatch):
        """有 pnpm 时保持现状：走 pnpm remove，再清 bundles，不做 JSON 手改备份。"""
        plugin = tmp_path / "current-build" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        profile = self._profile(
            tmp_path,
            deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"},
            bundles=[agent_link.DSH_PLUGIN_NAME],
        )
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls = []

        def fake_run(profile_dir, *args):
            calls.append(args)
            data = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
            data["dependencies"].pop(agent_link.DSH_PLUGIN_NAME, None)
            (profile_dir / "package.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        assert DshMonitor.uninstall_bridge() is True
        assert calls == [("remove", agent_link.DSH_PLUGIN_NAME)]
        manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
        assert agent_link.DSH_PLUGIN_NAME not in manifest["dsh"]["profile"]["bundles"]
        assert not list(profile.glob("package.json.bak-*")), "pnpm 路径不做手改备份"


# ============================================================================
# 14. Agent 动作轮换、过程汇报与 window 平滑衔接测试
# ============================================================================
class TestAgentLinkChainingAndActivity:
    def _make_mgr(self, tmp_path, agent_link_cfg=None, acts=None):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "吃Token", "轻快记录", "漂浮踏步"] if acts is None else acts}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                switched.append(self.idles[0])

        win = DummyWin()
        win.switched = switched
        cfg = Config(base=tmp_path)
        if agent_link_cfg is not None:
            data = cfg.data
            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}
            cfg.save()

        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_anim_rotation_sequence(self, tmp_path):
        """1. 动作池轮换顺序：DummyWin 的 cats.acts 含 ['写代码','吃Token','轻快记录','漂浮踏步']，
        连续 6 次 busy（每次 clock 前进 3s 避免节流）→ 依次为 写代码/吃Token/轻快记录/写代码/吃Token/漂浮踏步（每第3次插播摸鱼）。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, acts=["写代码", "吃Token", "轻快记录", "漂浮踏步"]
        )
        res = [mgr._next_link_anim_rotation() for _ in range(6)]
        expected = ["写代码", "吃Token", "轻快记录", "吃Token", "写代码", "漂浮踏步"]
        assert res == expected

    def test_anim_rotation_falls_back_to_keywords_and_available_acts(self, tmp_path):
        """精确动作名不存在时，按主/摸鱼关键词选择；完全不匹配时回退到任意动作。"""
        mgr, win, bubbles, clock = self._make_mgr(
            tmp_path, acts=["敲击键盘", "伸懒腰", "发呆"]
        )
        res = [mgr._next_link_anim_rotation() for _ in range(6)]
        assert res == ["敲击键盘", "敲击键盘", "伸懒腰", "敲击键盘", "敲击键盘", "伸懒腰"]

        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=["跳舞"])
        assert mgr._next_link_anim_rotation() == "跳舞"


    def test_empty_acts_returns_none(self, tmp_path):
        """2. 无可用动作时 _next_link_anim_rotation 返回 None（DummyWin cats.acts 为空列表）不抛异常。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=[])
        assert mgr._next_link_anim_rotation() is None
        # 触发状态变更也不抛异常
        mgr._on_agent_state("dsh", "working")
        assert win.switched == []

    def test_activity_reporting(self, tmp_path):
        """3. 过程汇报：mgr._on_agent_activity('dsh','bash') → 气泡含「正在跑命令」；
        10 秒内第二次任何工具不弹；同工具 60 秒内不重复（clock 前进 15s 再发 bash 仍不弹；换成 read 则弹「正在读文件」）；
        全局限流 8s（另一 agent 在 8s 内也不弹）。
        未知工具（如 'frobnicate'）弹安全兜底文案。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)

        # 未知工具弹安全兜底文案，不泄露原始参数
        mgr._on_agent_activity("dsh", "frobnicate")
        assert len(bubbles) == 1
        assert "正在调用工具" in bubbles[-1]
        assert "frobnicate" not in bubbles[-1]

        # dsh bash → 弹「正在跑命令」
        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "bash")
        assert len(bubbles) == 2
        assert "正在跑命令" in bubbles[-1]

        # 10 秒内第二次任何工具不弹
        clock[0] += 5.0
        mgr._on_agent_activity("dsh", "read")
        assert len(bubbles) == 2

        # 全局限流 8s（另一 agent 在 8s 内也不弹，从 1000.0 起算此时 1005.0 < 1008.0）
        mgr._on_agent_activity("claude", "read")
        assert len(bubbles) == 2

        # 同工具 60 秒内不重复：前进 15s（总共 +20s > 10s，但 < 60s），再发 bash 仍不弹
        clock[0] += 15.0
        mgr._on_agent_activity("dsh", "bash")
        assert len(bubbles) == 2

        # 换成 read 则弹「正在读文件」
        mgr._on_agent_activity("dsh", "read")
        assert len(bubbles) == 3
        assert "正在读文件" in bubbles[-1]

        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "pwsh")
        assert len(bubbles) == 4
        assert "pwsh" in bubbles[-1]  # activity.run 轮换到含工具名的变体
        clock[0] += 10.0
        mgr._on_agent_activity("dsh", "memory_search")
        assert len(bubbles) == 5
        assert bubbles[-1].strip()  # activity.default 轮换文案，仅断言有气泡

    def test_activity_bubble_receives_tool_record_fields(self, tmp_path):
        """过程汇报气泡必须拿到上游 tool/call 记录的字段（显式注入，非隐式上下文）。

        监视器同轮转发的工具记录被按 agent 缓存，_on_agent_activity 把
        tool/label/callId/step + 会话字段显式传给模板（脱敏口径：command/argsKey
        不落盘不注入）；条件字段缺失时占位符自动隐藏（不原样露出 {callId} 等）。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        # 模拟监视器 _poll 的同轮顺序：先 raw_record（工具记录），再 activity 信号
        # 字段以桥接真实写出的 tool/call 为准（tool/callId/step）。
        mgr._remember_dialogue_record("dsh", {
            "ts": 1, "event": "tool/call", "tool": "read",
            "callId": "call-1", "step": 2,
            "sessionId": "sess-1",
        })
        cfg = mgr.cfg
        cfg.data["dialogue_mode"] = "custom"
        cfg.data["dialogue_phrases"] = {
            "activity.read": ["正在读取（{tool}，第 {step} 步，调用 {callId}）"],
            "activity.search": ["搜索（{tool}）callId={callId}，第 {step} 步"],
        }
        cfg.save()
        mgr._on_agent_activity("dsh", "read")
        assert bubbles, "气泡未弹出"
        assert "第 2 步" in bubbles[-1]
        assert "call-1" in bubbles[-1]

        # 字段缺失的最小记录：条件占位符自动隐藏，不原样保留 {callId}/{step}
        clock[0] += 15.0
        mgr._remember_dialogue_record("dsh", {"ts": 2, "event": "tool/call", "tool": "grep"})
        mgr._on_agent_activity("dsh", "grep")
        assert "call-1" not in bubbles[-1]
        assert "{callId}" not in bubbles[-1]
        assert "{step}" not in bubbles[-1]

    def test_activity_bubble_text_is_truncated_at_source(self, tmp_path):
        """过程汇报文案源头截断：自定义模板塞进超长命令时截到 80 字 + 「…」。

        过程汇报只是一句状态提示，不进分页/滚动；审批/提问气泡走
        _show_interaction_bubble，不受该截断影响。
        """
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        # 先缓存 tool/call 记录（监视器 _poll 的同轮顺序），命令长到必定超上限
        mgr._remember_dialogue_record("dsh", {
            "ts": 1, "event": "tool/call", "tool": "bash",
            "command": "x" * 200, "step": 3,
        })
        cfg = mgr.cfg
        cfg.data["dialogue_mode"] = "custom"
        cfg.data["dialogue_phrases"] = {"activity.run": ["正在跑命令（{command}）"]}
        cfg.save()

        mgr._on_agent_activity("dsh", "bash")
        assert bubbles, "气泡未弹出"
        text = bubbles[-1]
        assert text.startswith("正在跑命令（")
        assert len(text) == AgentLinkManager._ACTIVITY_TEXT_LIMIT + 1
        assert text.endswith("…")

        # 上限内的文案原样展示（不追加省略号）
        clock[0] += 15.0
        mgr._remember_dialogue_record("dsh", {
            "ts": 2, "event": "tool/call", "tool": "read", "command": "cat a.py",
        })
        cfg.data["dialogue_phrases"] = {"activity.read": ["正在读取 {command}"]}
        cfg.save()
        mgr._on_agent_activity("dsh", "read")
        assert bubbles[-1] == "正在读取 cat a.py"

    def test_window_smooth_chaining(self, tmp_path):
        """4. window 侧平滑衔接（用真实 PetWindow + MovieLibrary，offscreen，参考 TestAgentMenuRebound 的构造）：
        win._switch('优雅女仆舞')（一次性动作）后 win.request_link_anim('写代码') →
        当前 anim 仍是 '优雅女仆舞' 且 _pending_link_anim=='写代码'（不打断）；
        手动调 win._on_anim_ended('优雅女仆舞') → anim 变为 '写代码'。
        再测：待机中（win.anim 在 win.idles 里）request_link_anim 立即切换。
        request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        try:
            # 确保 '优雅女仆舞' 是一次性动作 (acts)
            assert "优雅女仆舞" in win.acts
            win._switch("优雅女仆舞")
            assert win.anim == "优雅女仆舞"
            assert win._is_one_shot_playing() is True

            win.request_link_anim("写代码")
            assert win.anim == "优雅女仆舞"
            assert win._pending_link_anim == "写代码"

            # 手动调 _on_anim_ended('优雅女仆舞') → 播放待播的 '写代码'
            win._on_anim_ended("优雅女仆舞")
            assert win.anim == "写代码"
            assert win._pending_link_anim is None

            # 待机中（win.anim 在 win.idles 里）request_link_anim 立即切换
            idle_name = win.idles[0]
            win._switch(idle_name)
            assert win.anim in win.idles
            assert win._is_one_shot_playing() is False

            win.request_link_anim("吃Token")
            assert win.anim == "吃Token"

            # request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）
            win._switch("优雅女仆舞")
            win._pending_link_anim = "写代码"
            win.request_link_idle()
            assert win.anim == "优雅女仆舞"
            assert win._pending_link_anim is None
        finally:
            win.close()
            win.deleteLater()

    def test_on_anim_ended_continuation(self, tmp_path):
        """5. _on_anim_ended 联动续播：构造 PetWindow 后，设置 win._link_anim_current='写代码'，
        win._link_next_provider=lambda: '吃Token'，调 win._on_anim_ended('写代码') → anim=='吃Token'；
        provider 返回 None 时走正常动画链（不抛异常即可）。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        try:
            win._link_anim_current = "写代码"
            win._link_next_provider = lambda: "吃Token"
            win._on_anim_ended("写代码")
            assert win.anim == "吃Token"
            assert win._link_anim_current == "吃Token"

            # provider 返回 None 时走正常动画链（不抛异常）
            win._link_next_provider = lambda: None
            win._on_anim_ended("吃Token")
            # 正常推进，不抛异常
            assert win._link_anim_current is None
        finally:
            win.close()
            win.deleteLater()



# ============================================================================
# 14. 过程汇报：事件 tool 字段 → activity 信号
# ============================================================================
class TestActivitySignal:
    def test_tool_field_emits_activity_without_state(self, tmp_path):
        """jsonl 事件带 tool 字段时发 activity 信号，且不产生状态变化。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got, states = [], []
        mon.activity.connect(lambda a, t: got.append((a, t)))
        mon.state_changed.connect(lambda a, s: states.append(s))
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()  # 先建空文件，backfill 才能落到末尾
        mon._tailer.read_new_lines()  # backfill 初始化
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"tool/call","tool":"bash"}\n')
        mon._poll()
        assert got == [("dsh", "bash")]
        assert states == []

    def test_no_tool_no_activity(self, tmp_path):
        """普通状态事件不发 activity。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        got = []
        mon.activity.connect(lambda a, t: got.append(t))
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._tailer.read_new_lines()
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write('{"ts":1,"agent":"dsh","event":"AgentStatus","state":"working"}\n')
        mon._poll()
        assert got == []

    class _HiddenWin:
        cats = {"acts": ["写代码"]}
        idles = ["待机呼吸"]
        _bubble_busy_until = 0.0
        switched = None
        bubbles = None

        def __init__(self):
            self.switched = []
            self.bubbles = []

        def isVisible(self):
            return False

        def _switch(self, name):
            self.switched.append(name)

        def show_bubble(self, text, duration_ms=3000):
            self.bubbles.append(text)

        def _pick(self, lst):
            return lst[0]

    def test_fire_done_hidden_window_is_noop(self, tmp_path):
        """opus 评审 H1：隐藏窗口上 _fire_done 不得切动画/弹气泡。"""
        app = QApplication.instance() or QApplication([])
        win = TestActivitySignal._HiddenWin()
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(win, cfg)
        mgr._last_raw["dsh"] = "idle"
        mgr._fire_done("dsh")
        assert win.switched == []
        assert win.bubbles == []

    def test_pause_cancels_done_pending(self, tmp_path):
        """opus 评审 H1：pause 必须取消所有完成确认计时器。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        bubbles = []

        class Win:
            cats = {"acts": ["写代码"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                pass

            def request_link_idle(self):
                pass

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        mgr = AgentLinkManager(Win(), cfg)
        mgr._on_agent_state("dsh", "working")
        mgr._on_agent_state("dsh", "idle")
        assert "dsh" in mgr._done_pending
        mgr.pause()
        assert mgr._done_pending == {}

# ============================================================================
# 15. OpenCode 子代理会话过滤（防「干完活啦」刷屏）
# ============================================================================
class TestOpenCodeSubagentFilter:
    def _make_db(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
        db.execute("INSERT INTO session VALUES ('root1', NULL)")
        db.execute("INSERT INTO session VALUES ('child1', 'root1')")
        db.commit()
        db.close()
        return db_path

    def test_subagent_events_filtered(self, tmp_path):
        """子代理（parent_id 非空）会话的 step-start/step-finish/工具事件全部忽略。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])
        db_path = self._make_db(tmp_path)
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received, tools = [], []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.activity.connect(lambda k, t: tools.append(t))
        mon.start()
        mon._poll()  # backfill

        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('c', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-start\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 2, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"tool\",\"tool\":\"bash\"}}')")
        db.execute("INSERT INTO event VALUES ('c', 3, 'message.part.updated.1', "
                   "'{\"sessionID\":\"child1\",\"part\":{\"type\":\"step-finish\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == [] and tools == []  # 子代理全程静默

        # 主会话正常报
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('r', 4, 'message.part.updated.1', "
                   "'{\"sessionID\":\"root1\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["working"]
        mon.stop()

    def test_missing_session_table_conservative(self, tmp_path):
        """老库没有 session 表：不过滤（保守不丢事件）。"""
        import sqlite3
        from PySide6.QtWidgets import QApplication
        from pet.agent_link import OpenCodeMonitor

        app = QApplication.instance() or QApplication([])
        db_path = tmp_path / "opencode.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE event (aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.commit()
        db.close()
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        received = []
        mon = OpenCodeMonitor(cfg_dir, db_path=db_path)
        mon.state_changed.connect(lambda k, s: received.append(s))
        mon.start()
        mon._poll()
        db = sqlite3.connect(db_path)
        db.execute("INSERT INTO event VALUES ('s1', 1, 'message.part.updated.1', "
                   "'{\"sessionID\":\"x\",\"part\":{\"type\":\"step-start\"}}')")
        db.commit()
        db.close()
        mon._poll()
        assert received == ["working"]
        mon.stop()

    def test_busy_agent_owns_process(self, tmp_path):
        """联动去重：联动开启+忙碌+进程匹配 → True；其余组合 → False。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        cfg.data["agent_link"]["opencode"] = True

        class W:
            cats = {"acts": []}
            idles = []
            _bubble_busy_until = 0.0
            def isVisible(self): return True
            def show_bubble(self, *a, **k): pass

        mgr = AgentLinkManager(W(), cfg)
        mgr._last_raw["opencode"] = "working"
        assert mgr.busy_agent_owns_process("OpenCode.exe") is True
        assert mgr.busy_agent_owns_process("msedge.exe") is False
        mgr._last_raw["opencode"] = "idle"
        assert mgr.busy_agent_owns_process("OpenCode.exe") is False
        mgr._last_raw["opencode"] = "working"
        cfg.data["agent_link"]["opencode"] = False  # 联动关闭时不抑制识屏
        assert mgr.busy_agent_owns_process("OpenCode.exe") is False
        # dsh 无独立进程：靠窗口标题识别
        cfg.data["agent_link"]["dsh"] = True
        mgr._last_raw["dsh"] = "working"
        assert mgr.busy_agent_owns_process("msedge.exe", "审查结果 — DeepSeek Harness") is True
        assert mgr.busy_agent_owns_process("msedge.exe", "哔哩哔哩") is False
        mgr._last_raw["dsh"] = "idle"
        assert mgr.busy_agent_owns_process("msedge.exe", "审查结果 — DeepSeek Harness") is False


# ============================================================================
# 自定义联动 Agent（agent_link.custom_agents 配置驱动）
# ============================================================================
class TestCustomAgentConfigCleaning:
    def test_valid_entry_kept_and_normalized(self):
        cleaned = _clean_custom_agents([
            {"key": "Gemini", "name": "  Gemini CLI  ", "path": " ~/.gemini/ev.jsonl "},
        ])
        assert cleaned == [{"key": "gemini", "name": "Gemini CLI", "path": "~/.gemini/ev.jsonl"}]

    def test_name_defaults_to_key(self):
        cleaned = _clean_custom_agents([{"key": "myagent", "path": "~/x.jsonl"}])
        assert cleaned == [{"key": "myagent", "name": "myagent", "path": "~/x.jsonl"}]

    def test_invalid_entries_dropped(self):
        cleaned = _clean_custom_agents([
            "not-a-dict",                                # 非对象
            {"key": "Bad Key", "path": "~/x.jsonl"},     # key 含空格/大写
            {"key": "claude", "path": "~/x.jsonl"},      # 与内置键冲突
            {"key": "ok", "path": ""},                   # 空 path
            {"key": "ok2"},                              # 缺 path
        ])
        assert cleaned == []

    def test_duplicate_keys_deduped(self):
        cleaned = _clean_custom_agents([
            {"key": "gemini", "path": "~/a.jsonl"},
            {"key": "gemini", "path": "~/b.jsonl"},
        ])
        assert len(cleaned) == 1
        assert cleaned[0]["path"] == "~/a.jsonl"

    def test_max_entries_truncated(self):
        raw = [{"key": f"agent{i}", "path": f"~/{i}.jsonl"} for i in range(20)]
        assert len(_clean_custom_agents(raw)) == 8

    def test_non_list_returns_empty(self):
        assert _clean_custom_agents(None) == []
        assert _clean_custom_agents({"key": "gemini"}) == []

    def test_clean_agent_link_data_cleans_and_keeps_custom_key_booleans(self):
        cleaned = _clean_agent_link_data({
            "custom_agents": [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}],
            "gemini": True,        # 自定义键的开关布尔（set_enabled 写入路径）
            "notify_done": False,  # 减法退役的旧布尔开关：读入即丢弃，不写回
        })
        assert cleaned["custom_agents"] == [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}]
        assert cleaned["gemini"] is True
        # 退役键被弹出（配置形状里不留死键）
        assert "notify_done" not in cleaned
        assert "report_gates" not in cleaned


class TestCustomAgentMonitor:
    def test_tail_events_and_signals(self, tmp_path):
        """统一协议三种形态（state / event+tool / state 收尾）→ 信号正确。"""
        app = QApplication.instance() or QApplication([])
        events = tmp_path / "sub" / "gemini.jsonl"
        events.parent.mkdir(parents=True)
        events.touch()

        states, tools = [], []
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(events))
        mon.state_changed.connect(lambda k, s: states.append((k, s)))
        mon.activity.connect(lambda k, t: tools.append((k, t)))
        mon.start()
        mon._poll()  # backfill 初始化

        with open(events, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 1.0, "state": "working"}) + "\n")
            f.write(json.dumps({"ts": 2.0, "event": "PreToolUse", "tool": "bash"}) + "\n")
            f.write(json.dumps({"ts": 3.0, "state": "idle"}) + "\n")

        mon._poll()
        # PreToolUse 事件按内置映射同时产生 working 状态 + bash 工具过程
        assert states == [("gemini", "working"), ("gemini", "working"), ("gemini", "idle")]
        assert tools == [("gemini", "bash")]
        mon.stop()

    def test_missing_file_idle_then_appears(self, tmp_path):
        """文件不存在时空转；出现后 backfill 防护跳过历史，只读新增行。"""
        app = QApplication.instance() or QApplication([])
        missing = tmp_path / "not_yet.jsonl"
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(missing))
        states = []
        mon.state_changed.connect(lambda k, s: states.append((k, s)))
        mon.start()
        mon._poll()
        mon._poll()
        assert states == []

        missing.write_text('{"state": "working"}\n', encoding="utf-8")
        mon._poll()  # 首次发现文件：backfill，不回放历史
        assert states == []

        with open(missing, "a", encoding="utf-8") as f:
            f.write('{"state": "idle"}\n')
        mon._poll()
        assert states == [("gemini", "idle")]
        mon.stop()

    def test_start_does_not_create_dirs(self, tmp_path):
        """只读监听：绝不替用户在任意路径创建目录。"""
        app = QApplication.instance() or QApplication([])
        mon = CustomAgentMonitor(
            "gemini", tmp_path / "cfg", str(tmp_path / "deep" / "nested" / "ev.jsonl"),
        )
        mon.start()
        mon._poll()
        assert not (tmp_path / "deep").exists()
        mon.stop()

    def test_tilde_path_expanded(self, tmp_path, monkeypatch):
        # expanduser 在 Windows 读 USERPROFILE、POSIX 读 HOME，两个都设以保证跨平台
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", "~/events.jsonl")
        assert mon.events_file == tmp_path / "events.jsonl"
        assert "~" not in str(mon.events_file)


class TestCustomAgentManager:
    def test_registered_names_merged_and_generic_toggle(self, tmp_path):
        """custom_agents → 监视器注册 + 显示名合并 + 通用开关联动（无需授权弹窗）。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [
            {"key": "gemini", "name": "Gemini CLI", "path": str(tmp_path / "gemini.jsonl")},
        ]
        cfg.set("agent_link", ag)
        cfg.save()

        mgr = AgentLinkManager(None, cfg)
        assert "gemini" in mgr.monitors
        assert isinstance(mgr.monitors["gemini"], CustomAgentMonitor)
        assert mgr.agent_names["gemini"] == "Gemini CLI"
        # 类级 AGENT_NAMES 保持仅内置：设置页按内置枚举的遍历不受自定义影响
        assert "gemini" not in AgentLinkManager.AGENT_NAMES
        assert mgr.agent_names["dsh"] == "DSH"

        # 通用开关：开启持久化并启动监视器
        assert mgr.set_enabled("gemini", True) is True
        assert cfg.data["agent_link"]["gemini"] is True
        assert mgr.monitors["gemini"].is_running() is True

        # 隐藏暂停 / 显示恢复
        mgr.pause()
        assert mgr.monitors["gemini"].is_running() is False
        mgr.resume()
        assert mgr.monitors["gemini"].is_running() is True

        # 关闭
        assert mgr.set_enabled("gemini", False) is True
        assert cfg.data["agent_link"]["gemini"] is False
        assert mgr.monitors["gemini"].is_running() is False

    def test_builtin_key_in_custom_agents_ignored(self, tmp_path):
        """config 清洗会拒绝与内置键冲突的自定义条目，管理器不覆盖内置监视器。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [{"key": "claude", "name": "Fake", "path": str(tmp_path / "x.jsonl")}]
        cfg.set("agent_link", ag)
        cfg.save()

        mgr = AgentLinkManager(None, cfg)
        assert not isinstance(mgr.monitors["claude"], CustomAgentMonitor)
        assert mgr.agent_names["claude"] == "Claude Code"


class TestCustomAgentMenu:
    def test_menu_lists_custom_agent_and_toggle_routes(self, tmp_path):
        """右键菜单动态渲染自定义 Agent（收进「自定义联动 Agent」三级子菜单），勾选走通用 _toggle_agent_link。"""
        from PySide6.QtWidgets import QMenu
        from pet.context_menus.shared import add_agent_link_menu

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        ag = dict(cfg.get("agent_link", {}))
        ag["custom_agents"] = [
            {"key": "gemini", "name": "Gemini CLI", "path": "~/gemini.jsonl"},
        ]
        cfg.set("agent_link", ag)
        cfg.save()

        toggles, options = [], []

        class DummyPet:
            def __init__(self):
                self.cfg = cfg

            def toggle_agent_link(self, key, on, action=None):
                toggles.append((key, on))

            def set_agent_link_option(self, key, on):
                options.append((key, on))

            _toggle_agent_link = toggle_agent_link
            _set_agent_link_option = set_agent_link_option

        menu = QMenu()
        try:
            add_agent_link_menu(menu, DummyPet())
            sub = menu.actions()[0].menu()
            texts = [a.text() for a in sub.actions()]
            # 内置 4 项仍在顶层，自定义项收进三级子菜单「自定义联动 Agent」
            for label in ("DeepSeek Harness (DSH)", "Claude Code", "Cursor", "OpenCode"):
                assert label in texts
            assert "Gemini CLI" not in texts
            custom_sub = next(a.menu() for a in sub.actions() if a.text() == "自定义联动 Agent")
            custom_texts = [a.text() for a in custom_sub.actions()]
            assert "Gemini CLI" in custom_texts
            # Agent 联动子菜单不再带「台词风格」「循环检测/卡住检测」入口——
            # 检测类配置已收敛到设置页（自动化与联动），仅保留联动相关设置
            assert "台词风格" not in texts

            gemini_act = next(a for a in custom_sub.actions() if a.text() == "Gemini CLI")
            gemini_act.setChecked(True)
            assert toggles == [("gemini", True)]
        finally:
            import shiboken6
            shiboken6.delete(menu)


# ============================================================================
# 阻塞型交互气泡生命周期（审批 / 用户问题统一处理，一直挂到 resolved）
# ============================================================================
class TestApprovalStickyBubble:
    """阻塞型交互气泡永久挂着：approval/request、question/requested → sticky；
    decided / resolved / idle / offline → 消失。

    覆盖：sticky 展示、resolved 收尾、并发交互互不覆盖、idle 兜底、全量清除、
    _saw_alert 补记（完成后不误说"干完活啦"）、question 选项排版。
    """

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self._sticky_bubble_active = False
                self.sticky_shown: list[tuple[str, bool]] = []
                self.hidden_calls = 0
                self._alert_current = None
                self._alert_queue = []

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self._sticky_bubble_active = bool(sticky)
                self.sticky_shown.append((str(text), bool(sticky)))
            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True, alert_id=""):
                self._alert_queue.append({"id": alert_id, "text": str(text), "sticky": sticky})
                if self._alert_current is None and self._alert_queue:
                    self._alert_current = self._alert_queue.pop(0)
                    self._sticky_bubble_active = self._alert_current.get("sticky", True)
                self.sticky_shown.append((str(text), sticky))
            def resolve_alert(self, alert_id):
                if self._alert_current and self._alert_current.get("id") == alert_id:
                    self._alert_current = None
                    self.hidden_calls += 1
                    self._sticky_bubble_active = False
                    if self._alert_queue:
                        self._alert_current = self._alert_queue.pop(0)
                        self._sticky_bubble_active = self._alert_current.get("sticky", True)
                else:
                    self._alert_queue = [q for q in self._alert_queue if q.get("id") != alert_id]

            def hide_bubble(self):
                self.hidden_calls += 1
                self._sticky_bubble_active = False
                self._alert_current = None

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        mgr.win.shown_buttons = []
        return mgr

    def _single_pending(self, mgr, agent_key: str) -> dict:
        """取该 agent 唯一一条 pending 交互（多条时断言失败，供单交互测试用）。"""
        items = mgr.pending_interactions_for(agent_key)
        assert len(items) == 1, f"期望 {agent_key} 只有一条 pending，实际 {len(items)} 条"
        return next(iter(items.values()))

    def _single_iid(self, mgr, agent_key: str) -> str:
        items = mgr.pending_interactions_for(agent_key)
        assert len(items) == 1
        return next(iter(items))

    def _agent_keys(self, mgr) -> set:
        return {item.get("agent_key") for item in mgr._pending_interactions.values()}

    def test_approval_request_shows_sticky(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-hint", "sessionId": "s-1"})
        assert self._agent_keys(mgr) == {"dsh"}
        pending = self._single_pending(mgr, "dsh")
        assert pending["kind"] == "approval"
        assert mgr.win._sticky_bubble_active is True
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        # legacy 内置预设 approval.tool 首句：请求使用工具名（原 fallback 含「审批」字样）
        assert "请求使用工具" in text and "bash" in text
        assert mgr.win.shown_buttons == [], "减法后审批气泡永不出按钮（纯提示）"

    def test_approval_request_never_shows_command_text(self, tmp_path):
        """脱敏口径（#226）：桥接不再落被审批命令明文；即使旧桥/手写桩带了
        command 字段，气泡也不得展示命令内容（只说「有审批等你决定」）。"""
        mgr = self._make_mgr(tmp_path)
        cmd = "pip install pytest --index-url http://mirrors.aliyun.com/pypi/simple/"
        mgr._on_approval_request("dsh", {"tool": "bash", "command": cmd, "approvalId": "ap-cmd", "sessionId": "s-1"})
        text, _sticky = mgr.win.sticky_shown[-1]
        assert cmd not in text and "pip install" not in text, "气泡文案不得包含命令明文"
        assert "请求使用工具" in text and "bash" in text

    def test_approval_resolved_dismisses(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-r", "sessionId": "s-1"})
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-r"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_concurrent_approvals_resolved_last(self, tmp_path):
        """两个 agent 并发审批：各自 pending；队列模型下逐条关闭并推进下一条。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-d", "sessionId": "s-1"})
        mgr._on_approval_request("claude", {"tool": "write", "approvalId": "ap-c", "sessionId": "s-2"})
        assert self._agent_keys(mgr) == {"dsh", "claude"}
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-d"})
        assert self._agent_keys(mgr) == {"claude"}
        assert mgr.win._sticky_bubble_active is True, "dsh 审批关闭后 claude 审批顶上，气泡仍挂着"
        mgr._on_approval_resolved("claude", {"approvalId": "ap-c"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 2
        assert mgr.win._sticky_bubble_active is False

    def test_idle_dismisses_approval(self, tmp_path):
        """agent 回待机但没收到 decided：交互必然失效，兜底清掉（含窗口隐藏时）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-i", "sessionId": "s-1"})
        mgr._on_agent_state("dsh", "idle")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1

    def test_dismiss_all_approvals(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-all", "sessionId": "s-1"})
        mgr.dismiss_all_approvals()
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_approval_records_saw_alert(self, tmp_path):
        """审批打断算"需要主人看一眼"：完成后不误说"干完活啦"。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-saw", "sessionId": "s-1"})
        assert "dsh" in mgr._saw_alert

    def test_resolved_unknown_agent_noop(self, tmp_path):
        """没有对应 pending 的 resolved 事件是空操作，不误关气泡。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_resolved("dsh")
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 0

    def test_approval_resolved_call_id_does_not_close_question(self, tmp_path):
        """审批 resolved 帧带 callId 时不得按 callId 关闭问题交互。

        `_on_approval_resolved` 的 callId 分支是从问题侧复制粘贴来的错位判定：
        approval 与 question 的 callId 是两个独立命名空间，若该分支按
        kind == "question" 遍历，一条无关审批的收尾帧就会把同名 callId 的
        问题气泡误关掉（用户还没回答，问题弹窗先消失）。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": self.QUESTIONS, "callId": "call-shared"}
        )

        mgr._on_approval_resolved("dsh", {"callId": "call-shared"})

        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 1, "审批 resolved 不得误关同名 callId 的问题气泡"
        assert next(iter(pending.values()))["kind"] == "question"
        assert mgr.win.hidden_calls == 0

    def test_approval_resolved_call_id_closes_approval(self, tmp_path):
        """审批 resolved 帧带 callId 时按 callId 关闭审批交互。

        登记端必须存下审批的 callId 身份，否则改判 kind 后新分支也无从匹配。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "callId": "call-ap", "sessionId": "s-1"}
        )
        assert mgr.pending_interactions_for("dsh") != {}

        mgr._on_approval_resolved("dsh", {"callId": "call-ap"})

        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.hidden_calls == 1

    # ---- 用户问题（ask_user_question）与审批同待遇 ----
    QUESTIONS = [
        {"id": "q1", "question": "要执行哪个方案？",
         "options": [{"label": "方案 A"}, {"label": "方案 B"}, {"label": "方案 C"}],
         "multiSelect": False},
    ]

    def test_question_request_shows_sticky_with_options(self, tmp_path):
        """question/requested 带 options：常驻气泡列出选项，让用户选一个才能继续。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-q1"})
        pending = mgr.pending_interactions_for("dsh")
        assert pending, "应有至少一条 pending 交互"
        item = next(iter(pending.values()))
        assert item["kind"] == "question"
        assert mgr.win._sticky_bubble_active is True
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "要执行哪个方案" in text
        assert "方案 A" in text and "方案 B" in text and "方案 C" in text
        assert "请到 DSH 界面选择" in text
        assert mgr.win.shown_buttons == [], "减法后问题气泡永不出按钮（纯提示）"

    def test_question_resolved_dismisses(self, tmp_path):
        """question/resolved → 气泡收尾。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-qr"})
        mgr._on_question_resolved("dsh", {"callId": "call-qr"})
        assert mgr._pending_interactions == {}
        assert mgr.win.hidden_calls == 1
        assert mgr.win._sticky_bubble_active is False

    def test_question_resolved_matches_call_id_with_multiple_pending(self, tmp_path):
        """并发问题必须按 callId 关闭，不能因无 rpcId 而让整个提醒队列卡住。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": self.QUESTIONS, "callId": "call-a", "sessionId": "session-a",
        })
        mgr._on_question_request("dsh", {
            "questions": self.QUESTIONS, "callId": "call-b", "sessionId": "session-a",
        })

        assert len(mgr.pending_interactions_for("dsh")) == 2
        mgr._on_question_resolved("dsh", {"callId": "call-b", "sessionId": "session-a"})

        assert len(mgr.pending_interactions_for("dsh")) == 1
        remaining = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert remaining["call_id"] == "call-a"

    def test_pending_interaction_uses_interaction_id_not_agent_key(self, tmp_path):
        """pending_interactions 的键是 interaction_id 而非 agent_key。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "rpcId": "rpc-x"})
        keys = list(mgr._pending_interactions.keys())
        assert keys, "应有至少一条 pending 交互"
        assert "dsh" not in keys, "键应为 interaction_id，不是 agent_key"
        assert "rpc-x" in keys[0], f"键应包含 rpcId（如 approval:rpc-x），实际为 {keys[0]}"
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 1
        item = next(iter(pending.values()))
        assert item["agent_key"] == "dsh"
        assert item["kind"] == "question"

    def test_concurrent_pending_resolved_independently(self, tmp_path):
        """同一个 Agent 有两个 pending interaction → 解决其中一个，另一个仍然存在。

        并发两个审批后分别解决一个，验证未解决的审批不会因另一个解决而关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-a", "approvalId": "ap-a", "sessionId": "s-1"}
        )
        mgr._on_approval_request(
            "dsh", {"tool": "pwsh", "rpcId": "rpc-b", "approvalId": "ap-b", "sessionId": "s-1"}
        )
        pending_before = mgr.pending_interactions_for("dsh")
        assert len(pending_before) == 2, f"应有 2 条 pending 交互，实际 {len(pending_before)}"
        pending_keys = set(pending_before)
        assert pending_keys == {"approval:dsh:rpc_id:rpc-a:s-1",
                                "approval:dsh:rpc_id:rpc-b:s-1"}, \
            f"键 = 来源 + kind + 会话 + 身份命名空间/值：{pending_keys}"

        # 解决 A（DSH 侧完成审批 → resolved 帧到达）
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-a", "approvalId": "ap-a"})
        remaining = mgr.pending_interactions_for("dsh")
        assert len(remaining) == 1, "解决 A 后应只剩 B"
        assert "approval:dsh:rpc_id:rpc-b:s-1" in remaining, "B 仍应处于 pending 状态"
        item_b = remaining["approval:dsh:rpc_id:rpc-b:s-1"]
        assert item_b["tool"] == "pwsh"
        assert item_b["approval_id"] == "ap-b"

        # 解决 B 后全部清空
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-b", "approvalId": "ap-b"})
        assert mgr.pending_interactions_for("dsh") == {}, "解决 B 后应全部清空"

    def test_question_no_options_needs_input(self, tmp_path):
        """无 options 的问题（自由输入/确认）：提示需要输入，不出交互按钮。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "q2", "question": "请补充上下文"}], "rpcId": "rpc-free"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "请补充上下文" in text
        assert "正在询问" in text
        # 无选项=自由输入：即使 preset 文案被覆盖，结构引导也必须保留
        assert "请到 DSH 界面输入文本回答" in text
        assert mgr.win.shown_buttons == [], "自由输入问题不出可点按钮"

    def test_question_multi_question(self, tmp_path):
        """一次多个问题：提示有几个问题等你回答。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "a", "question": "Q1"}, {"id": "b", "question": "Q2"}], "callId": "call-multi"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "2 个问题" in text

    def test_question_and_approval_independent(self, tmp_path):
        """并发一个审批 + 一个问题：各自独立 pending，不再互相覆盖；分别 resolved 后全部关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-ind", "sessionId": "s-1"})
        mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "callId": "call-ind"})
        # 同一 agent 的多个交互各自独立存储（不再互相覆盖）
        assert self._agent_keys(mgr) == {"dsh"}
        pending = mgr.pending_interactions_for("dsh")
        assert len(pending) == 2, "审批和问题应共存，各自一条 pending"
        # 审批和问题各自有 kind
        kinds = {item["kind"] for item in pending.values()}
        assert kinds == {"approval", "question"}
        # 分别 resolved：先关闭问题
        mgr._on_question_resolved("dsh", {"callId": "call-ind"})
        assert len(mgr.pending_interactions_for("dsh")) == 1, "问题关闭后审批还在"
        assert self._single_pending(mgr, "dsh")["kind"] == "approval"
        # 再关闭审批
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-ind"})
        assert mgr.pending_interactions_for("dsh") == {}

    # ---- 减法后：气泡为纯提示（无点选按钮），resolved 精确关闭保留 ----

    def test_question_multi_branch_with_free_text_is_hint(self, tmp_path):
        """多分支事件里任一分支是自由文本（无 options）：文案引导回 DSH 界面输入。"""
        mgr = self._make_mgr(tmp_path)
        questions = [
            {"id": "dA", "question": "分支 A 选择", "options": [{"label": "ok"}]},
            {"id": "dB", "question": "分支 B 需要文本", "options": []},
        ]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-mixed", "sessionId": "s-1"})
        assert mgr.win.shown_buttons == [], "减法后不得出提交/选项按钮"
        text, sticky = mgr.win.sticky_shown[-1]
        assert "2 个问题" in text
        assert "DSH 界面输入文本回答" in text, f"应有回到 DSH 界面输入的提示，实际 {text!r}"

    def test_question_no_options_needs_input_text_mentions_dsh(self, tmp_path):
        """单个自由文本问题：纯提示气泡，文案明确引导回 DSH 界面输入文本。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request(
            "dsh", {"questions": [{"id": "q2", "question": "请补充上下文"}], "rpcId": "rpc-free2"}
        )
        text, sticky = mgr.win.sticky_shown[-1]
        assert sticky is True
        assert "请补充上下文" in text
        assert "正在询问" in text
        assert "请到 DSH 界面输入文本回答" in text
        assert mgr.win.shown_buttons == []

    def test_stale_resolved_does_not_close_next_approval(self, tmp_path):
        """DSH 延迟回发的陈旧 resolved/decided 不得误关仍在等待的 B。

        时序：A 在 DSH 界面被批准 → A 的 resolved（带 rpcId）关闭 A → 若再到来
        同 id 的重复 resolved 或无 id 的 decided，不得按「仅剩一条 pending」兜底
        关闭 B。带 id 的帧未匹配到 pending 即陈旧已解决帧，不兜底；无 id 的旧
        路径 decided 只对无 rpc_id 的纯提示兜底，不碰带 rpc_id 的审批。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-A", "approvalId": "ap-A", "sessionId": "s-1"}
        )
        mgr._on_approval_request(
            "dsh", {"tool": "bash", "rpcId": "rpc-B", "approvalId": "ap-B", "sessionId": "s-1"}
        )
        assert set(mgr.pending_interactions_for("dsh")) == {
            "approval:dsh:rpc_id:rpc-A:s-1", "approval:dsh:rpc_id:rpc-B:s-1"}

        # A 的 resolved 到达：只关 A
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-A", "approvalId": "ap-A", "outcome": "allowed-once"})
        assert set(mgr.pending_interactions_for("dsh")) == {"approval:dsh:rpc_id:rpc-B:s-1"}

        # 陈旧的 A resolved 重发 + 无 id 的 decided：都不得误关 B
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-A", "approvalId": "ap-A", "outcome": "allowed-once"})
        mgr._on_approval_resolved("dsh", {})
        assert set(mgr.pending_interactions_for("dsh")) == {"approval:dsh:rpc_id:rpc-B:s-1"},             "A 的陈旧 resolved/decided 不得误关 B"

        # B 的 resolved 正常收尾
        mgr._on_approval_resolved("dsh", {"rpcId": "rpc-B", "approvalId": "ap-B"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_question_payload_keeps_custom_and_intent_per_question(self, tmp_path):
        """questions 载荷原样登记（含 intent 等扩展字段），供气泡文案使用。"""
        mgr = self._make_mgr(tmp_path)
        questions = [{"id": "q1", "question": "评审", "intent": {"kind": "plan-review"}}, {"id": "q2", "question": "补充", "options": []}]
        mgr._on_question_request("dsh", {"questions": questions, "rpcId": "rpc-custom", "sessionId": "s-custom"})
        item = next(iter(mgr.pending_interactions_for("dsh").values()))
        assert item["questions"] == questions

    def test_question_resolved_matches_session_and_question_rpc_id(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        for session, rpc in (("s1", "r1"), ("s2", "r2")):
            mgr._on_question_request("dsh", {"questions": self.QUESTIONS, "rpcId": rpc, "sessionId": session})
        mgr._on_question_resolved("dsh", {"rpcId": "r1", "sessionId": "s1"})
        assert set(mgr.pending_interactions_for("dsh")) == {"question:dsh:rpc_id:r2:s2"}


# ============================================================================
# 阻塞交互身份门禁（防普通工具调用 / 审计事件误触发审批弹窗）
# ============================================================================
class TestInteractionIdentityGate:
    """Get-Location 等普通工具调用、无身份的审计事件绝不能被升级成审批弹窗。

    覆盖：
    - approval 无任何可关联身份（rpcId/approvalId/requestId/callId）→ 不弹窗；
    - 仅带 approvalId（无 rpcId）的真实兼容路径 → 纯提示且可被身份 resolved 关闭；
    - monitor 层：裸 approval/asked 无论带不带身份都不触发审批信号；
    - monitor 层：普通 tool/call（pwsh Get-Location）只发 activity，绝不触发审批；
    - question 无 rpcId 也无 callId → 不弹窗；带 callId 的兜底路径仍可提示并关闭；
    - cordis 仅严格布尔 requiresApproval=True 且带 requestId 才触发；
    - turn 结束兜底清理：漏发 resolved 时不留永久弹窗，且只清对应会话。
    """

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self._sticky_bubble_active = False
                self.hidden_calls = 0
                self._alert_current = None
                self._alert_queue = []
                self.shown_buttons = []
                self.sticky_shown = []

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self._sticky_bubble_active = bool(sticky)
                self.sticky_shown.append((str(text), bool(sticky)))
            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None, sticky=True, alert_id=""):
                self._alert_queue.append({"id": alert_id, "text": str(text), "sticky": sticky})
                if self._alert_current is None and self._alert_queue:
                    self._alert_current = self._alert_queue.pop(0)
                    self._sticky_bubble_active = self._alert_current.get("sticky", True)
                self.sticky_shown.append((str(text), sticky))
            def resolve_alert(self, alert_id):
                if self._alert_current and self._alert_current.get("id") == alert_id:
                    self._alert_current = None
                    self.hidden_calls += 1
                    self._sticky_bubble_active = False
                    if self._alert_queue:
                        self._alert_current = self._alert_queue.pop(0)
                        self._sticky_bubble_active = self._alert_current.get("sticky", True)
                else:
                    self._alert_queue = [q for q in self._alert_queue if q.get("id") != alert_id]

            def hide_bubble(self):
                self.hidden_calls += 1
                self._sticky_bubble_active = False
                self._alert_current = None

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        mgr.win.shown_buttons = []
        return mgr

    def _make_mon(self, tmp_path):
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._tailer.read_new_lines()  # backfill 初始化
        return mon

    def _write_events(self, mon, events):
        with mon.events_file.open("a", encoding="utf-8") as fh:
            for line in events:
                fh.write(json.dumps(line) + "\n")
        mon._poll()

    @pytest.mark.parametrize("command", [
        "Get-Location", "Get-ChildItem", "pwd", "ls", "git status",
        "Get-Location | Select-Object -ExpandProperty Path",
    ])
    def test_approval_without_identity_ignored(self, tmp_path, command):
        """无任何可关联身份的审批记录（普通工具调用被误标 approval/asked 后的残留）不弹窗。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "pwsh", "command": command})
        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.sticky_shown == []
        assert mgr.win._sticky_bubble_active is False

    def test_approval_with_only_approval_id_is_hint_and_closable(self, tmp_path):
        """仅带 approvalId（无 rpcId）的真实兼容路径：显示纯提示，且可被身份 resolved 精确关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "approvalId": "ap-x", "sessionId": "s-1"})
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "approval"
        assert item["approval_id"] == "ap-x"
        assert mgr.win.shown_buttons == [], "减法后不出按钮（纯提示）"
        mgr._on_approval_resolved("dsh", {"approvalId": "ap-x"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_approval_asked_never_emits_request_signal(self, tmp_path):
        """monitor 层：裸 approval/asked 无论带不带身份，都绝不触发审批弹窗信号。"""
        mon = self._make_mon(tmp_path)
        got = []
        mon.approval_requested.connect(lambda a, p: got.append((a, p.get("event"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "approval/asked", "tool": "pwsh", "command": "Get-Location"},
            {"ts": 2, "agent": "dsh", "event": "approval/asked", "approvalId": "ap-a", "sessionId": "s-1"},
            {"ts": 3, "agent": "dsh", "event": "approval/asked", "rpcId": "rpc-a", "sessionId": "s-1"},
        ])
        assert got == [], "approval/asked 不应驱动审批弹窗信号"

    def test_approval_requested_still_emits_request_signal(self, tmp_path):
        """monitor 层：权威 approval/request 与兼容旧名 approval/requested 正常触发审批信号。"""
        mon = self._make_mon(tmp_path)
        got = []
        mon.approval_requested.connect(lambda a, p: got.append((a, p.get("event"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "approval/request", "rpcId": "r1", "sessionId": "s1"},
            {"ts": 2, "agent": "dsh", "event": "approval/requested", "rpcId": "r2", "sessionId": "s1"},
        ])
        assert got == [("dsh", "approval/request"), ("dsh", "approval/requested")]

    def test_tool_call_never_becomes_approval(self, tmp_path):
        """monitor 层：普通 tool/call（pwsh Get-Location）只发 activity，绝不发审批信号。"""
        mon = self._make_mon(tmp_path)
        approvals, activities = [], []
        mon.approval_requested.connect(lambda a, p: approvals.append((a, p)))
        mon.activity.connect(lambda a, t: activities.append((a, t)))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "tool/call", "tool": "pwsh", "command": "Get-Location"},
        ])
        assert approvals == [], "普通工具调用不得触发审批"
        assert ("dsh", "pwsh") in activities

    def test_question_without_identity_ignored(self, tmp_path):
        """question/requested 无 rpcId 也无 callId：不弹窗（无法可靠关闭）。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": [{"id": "q1", "question": "选择？", "options": [{"label": "A"}]}],
        })
        assert mgr.pending_interactions_for("dsh") == {}
        assert mgr.win.sticky_shown == []

    def test_question_with_call_id_is_hint_and_closable(self, tmp_path):
        """question/requested 带 callId（tool/call 兜底路径）：显示纯提示且可被关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_question_request("dsh", {
            "questions": [{"id": "q1", "question": "选择？", "options": [{"label": "A"}]}],
            "callId": "call-q", "sessionId": "s-1",
        })
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "question"
        assert mgr.win.shown_buttons == []
        mgr._on_question_resolved("dsh", {"callId": "call-q", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_requires_strict_true_and_request_id(self, tmp_path):
        """monitor 层：cordis/request-run 只有 requiresApproval 严格布尔 True 且带 requestId 才触发。

        记录形状以桥接真实写盘为准（index.js 的 cordis/request-run 分支：
        `writeRecord({event, agentId, sessionId, kind, payload: request, requestId})`
        ——原始 request 整体嵌在 payload 下，requiresApproval 只在 payload 内，
        顶层只有 requestId/agentId/sessionId 等身份字段）；旧版/手写桩把字段
        平铺在顶层的形状仍须兼容。
        """
        mon = self._make_mon(tmp_path)
        got = []
        mon.cordis_requested.connect(lambda a, p: got.append((a, p.get("requestId"))))
        self._write_events(mon, [
            {"ts": 1, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-ok",
             "payload": {"requiresApproval": True, "requestId": "r-ok", "name": "插件", "purpose": "运行"}},
            {"ts": 2, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-no",
             "payload": {"requiresApproval": False, "requestId": "r-no"}},
            {"ts": 3, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-miss",
             "payload": {"requestId": "r-miss"}},
            {"ts": 4, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-str",
             "payload": {"requiresApproval": "true", "requestId": "r-str"}},
            {"ts": 5, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-legacy",
             "requiresApproval": True},
            # 嵌套与顶层同时存在时以嵌套为准：嵌套 False 不得被顶层残留 True 顶掉。
            {"ts": 6, "agent": "dsh", "event": "cordis/request-run", "requestId": "r-nested-wins",
             "requiresApproval": True,
             "payload": {"requiresApproval": False, "requestId": "r-nested-wins"}},
        ])
        assert got == [("dsh", "r-ok"), ("dsh", "r-legacy")], \
            "仅严格布尔 True 且带 requestId 才触发 cordis 交互（payload 内与顶层平铺两处都认，嵌套优先）"


    def test_cordis_without_request_id_ignored(self, tmp_path):
        """_on_cordis_request 无 requestId：不登记 pending 交互。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {"name": "插件", "purpose": "运行", "requiresApproval": True})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_with_request_id_registers(self, tmp_path):
        """_on_cordis_request 带 requestId：登记 pending 并可被 resolved 关闭。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {"name": "插件", "purpose": "运行", "requiresApproval": True, "requestId": "req-1"})
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "cordis"
        assert item["request_id"] == "req-1"
        mgr._on_cordis_resolved("dsh", {"requestId": "req-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_cordis_request_reads_nested_payload_fields(self, tmp_path):
        """_on_cordis_request 消费桥接写盘形状：名称/用途/会话从 payload 内取。

        桥接顶层不带 name/purpose，只把原始 cordis request 放进 payload；只读
        顶层会得到占位文案（"Cordis 插件 请求运行：需要你的确认"），用户看不出
        是哪条请求。顶层平铺的旧版/手写桩形状仍须兼容（见上一用例）。
        """
        mgr = self._make_mgr(tmp_path)
        mgr._on_cordis_request("dsh", {
            "requestId": "req-2", "agentId": "sess-9",
            "payload": {"requestId": "req-2", "name": "构建插件", "purpose": "执行打包脚本"},
        })
        pending = mgr.pending_interactions_for("dsh")
        item = next(iter(pending.values()))
        assert item["kind"] == "cordis"
        assert item["request_id"] == "req-2"
        assert item["session_id"] == "sess-9"
        assert "构建插件" in item["text"]
        assert "执行打包脚本" in item["text"]

    def test_turn_end_clears_stale_pending(self, tmp_path):
        """turn 结束兜底清理：DSH 漏发 resolved 时，会话结束不再留永久弹窗。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-z", "approvalId": "ap-z", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh")
        mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "s-1"})
        assert mgr.pending_interactions_for("dsh") == {}

    def test_turn_end_keeps_other_session_interaction(self, tmp_path):
        """turn 结束只清对应会话的交互，不影响其他会话并发的真实审批。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-a", "approvalId": "ap-a", "sessionId": "s-1"})
        mgr._on_approval_request("dsh", {"tool": "pwsh", "rpcId": "rpc-b", "approvalId": "ap-b", "sessionId": "s-2"})
        mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "s-1"})
        remaining = mgr.pending_interactions_for("dsh")
        assert set(remaining) == {"approval:dsh:rpc_id:rpc-b:s-2"}

    def test_agent_idle_clears_pending_via_lifecycle(self, tmp_path):
        """AgentStatus idle 兜底清理：同现有 _on_agent_state idle 语义。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_approval_request("dsh", {"tool": "bash", "rpcId": "rpc-idle", "approvalId": "ap-idle", "sessionId": "s-1"})
        mgr._on_interaction_lifecycle("dsh", {"event": "AgentStatus", "state": "idle"})
        assert mgr.pending_interactions_for("dsh") == {}


# ============================================================================
# 硬失败（execution/failed）：DSH 已决定本轮不再继续，直接提醒
# ============================================================================
class TestExecutionFailed:
    """execution/failed 不经行为分析直接提醒：失败动画 + 气泡。"""

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self.shown: list[str] = []
                self.alerts: list[dict] = []
                self.anims: list[str] = []
                self._visible = True

            def isVisible(self):
                return self._visible

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self.shown.append(str(text))

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None,
                           sticky=True, alert_id="", priority=3,
                           alert_type="watchdog", metadata=None):
                # 签名与真实 PetWindow/OverlayShell.show_alert 对齐：硬失败提醒
                # 必须带上 alert_type/priority 才能穿透免打扰（见 test_quiet_mode）。
                self.alerts.append({
                    "text": str(text), "sticky": bool(sticky),
                    "duration_ms": int(duration_ms),
                    "priority": int(priority), "alert_type": str(alert_type),
                })

            def request_link_anim(self, anim):
                self.anims.append(str(anim))

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_retry_exhausted_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"failureType": "model_retry_exhausted", "retryExhausted": True, "retries": 4})
        assert mgr.win.alerts, "应入队失败提醒"
        assert "重试" in mgr.win.alerts[-1]["text"]  # failure.retry 预设（多次重试后仍未成功）
        assert mgr.win.alerts[-1]["sticky"] is False, "失败提醒是限时气泡（非 sticky）"

    def test_tool_failure_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {"failureType": "tool_failed", "retryExhausted": False})
        assert mgr.win.alerts
        assert "工具执行失败" in mgr.win.alerts[-1]["text"]  # failure.tool 预设首句
        # 分类必须显式给：默认的 watchdog 会被免打扰当普通提醒压进暂存队列。
        assert mgr.win.alerts[-1]["alert_type"] == "execution/failed"
        assert mgr.win.alerts[-1]["priority"] == 1

    def test_generic_failure(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_execution_failed("dsh", {})
        assert mgr.win.alerts
        assert "执行失败" in mgr.win.alerts[-1]["text"]  # failure.generic 预设首句

    def test_picks_fail_anim(self, tmp_path):
        """角色动作池含「失败/冒烟」类动作时选择它。"""
        mgr = self._make_mgr(tmp_path)
        mgr.win.cats = {"acts": ["待机", "失败冒烟", "写代码"]}
        anim = mgr._pick_fail_anim()
        assert anim == "失败冒烟"

    def _make_mgr(self, tmp_path):
        class FakeWin:
            def __init__(self):
                self.alerts: list[dict] = []
                self.resolved: list[str] = []
                self.shown: list[str] = []
                self._visible = True
                # 有意不提供 _bubble_busy_until：_schedule_model_access_dismiss 会据 sentinel 跳过 QTimer

            def isVisible(self):
                return self._visible

            def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None,
                           sticky=True, alert_id="", priority=3, alert_type="watchdog",
                           metadata=None):
                self.alerts.append({
                    "text": str(text), "sticky": bool(sticky),
                    "duration_ms": int(duration_ms), "alert_id": str(alert_id),
                    "priority": int(priority), "alert_type": str(alert_type),
                })

            def resolve_alert(self, alert_id):
                self.resolved.append(str(alert_id))

            def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None):
                self.shown.append(str(text))

        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(FakeWin(), cfg)
        return mgr

    def test_first_model_access_shows_reminder(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-1"})
        assert mgr.win.alerts, "应弹出模型访问失败提醒"
        alert = mgr.win.alerts[-1]
        assert alert["alert_id"] == "model-access:sess-1", "alert_id 必须带 sessionId 隔离"
        # 可见文案走 legacy model_access.one 预设（模型访问失败语义）；服务端限流码由 alert_id/alert_type 承载
        assert "模型访问失败" in alert["text"]
        assert alert["priority"] == mgr._MODEL_ACCESS_PRIORITY and alert["priority"] == 1

    def test_consecutive_model_access_merged_same_session(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-2"})
        mgr._on_model_access("dsh", {"sessionId": "sess-2"})  # 8s 冷却窗口内 → 合并
        assert mgr._model_access_cache["sess-2"]["count"] == 2
        # 合并后的可见文案走 legacy model_access.many 预设首句：
        # 「当前会话 … 的模型访问已连续失败 {count} 次，请稍后再试。」
        merged = mgr.win.alerts[-1]["text"]
        assert "模型访问" in merged and "连续失败" in merged, merged
        assert "2 次" in merged, merged
        assert mgr.win.alerts[-2]["alert_id"] == mgr.win.alerts[-1]["alert_id"], "同 session 复用同一 alert_id"

    def test_multi_session_isolated(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-A"})
        mgr._on_model_access("dsh", {"sessionId": "sess-B"})
        ids = [a["alert_id"] for a in mgr.win.alerts]
        assert ids == ["model-access:sess-A", "model-access:sess-B"], "不同 session 不得互相顶替"
        assert set(mgr._model_access_cache) == {"sess-A", "sess-B"}

    def test_dismiss_clears_cache_and_alert(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-3"})
        mgr._dismiss_model_access_alert("sess-3")
        assert "sess-3" not in mgr._model_access_cache, "关闭后清理缓存"
        assert "model-access:sess-3" in mgr.win.resolved, "关闭对应 alert"

    def test_execution_failed_suppressed_while_model_access_active(self, tmp_path):
        """存在活跃模型访问失败提醒时，仅真正的模型访问失败（errorCode 属限流类码）不再弹通用横幅；
        模型重试耗尽（retryExhausted）是另一条语义，照常提醒。"""
        mgr = self._make_mgr(tmp_path)
        # 先触发模型访问失败提醒，冷却窗口内再出现真·模型访问失败（errorCode=RATE_LIMIT）→ 抑制
        mgr._on_model_access("dsh", {"sessionId": "sess-4"})
        before = len(mgr.win.alerts)
        mgr._on_execution_failed("dsh", {"sessionId": "sess-4", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True, "errorCode": "RATE_LIMIT"})
        assert len(mgr.win.alerts) == before, "活跃模型访问失败提醒 + 真模型访问失败 → 抑制通用失败横幅"

    def test_retry_exhausted_not_suppressed_as_model_access(self, tmp_path):
        """模型重试耗尽失败（无限流类 errorCode）不是模型访问失败：提醒活跃也不抑制，照常弹 failure.retry。"""
        mgr = self._make_mgr(tmp_path)
        mgr._on_model_access("dsh", {"sessionId": "sess-5"})
        before = len(mgr.win.alerts)
        mgr._on_execution_failed("dsh", {"sessionId": "sess-5", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True})
        assert len(mgr.win.alerts) == before + 1, "重试耗尽失败不应被当作模型访问失败抑制"
        assert "重试" in mgr.win.alerts[-1]["text"]  # failure.retry 文案

    def test_execution_failed_suppressed_beyond_cooldown_while_429_alive(self, tmp_path):
        """F2 回归：模型访问失败提醒展示 15s > 合并冷却 8s，turn/end 的 execution/failed 常
        在 8~15s 窗口到达——只要提醒仍未 dismiss，通用失败横幅必须继续抑制
        （旧实现按 8s cooldown 判断会绕过抑制造成双弹）。dismiss 后新失败正常提醒。"""
        mgr = self._make_mgr(tmp_path)
        now = [1000.0]
        mgr._clock = lambda: now[0]
        mgr._on_model_access("dsh", {"sessionId": "sess-f2",
                                     "errorCode": "RATE_LIMIT", "consecutiveRetryCount": 1})
        assert len(mgr.win.alerts) == 1
        now[0] += 10.0  # 超出 8s cooldown，仍在 15s 展示寿命内
        mgr._on_execution_failed("dsh", {"sessionId": "sess-f2", "failureType": "model_retry_exhausted",
                                         "retryExhausted": True, "retries": 5,
                                         "errorCode": "RATE_LIMIT"})
        assert len(mgr.win.alerts) == 1, "模型访问失败提醒存活期间不得二次弹通用失败横幅"
        # 收起提醒后：新的（非限流）失败应正常提醒
        mgr._dismiss_model_access_alert("sess-f2")
        mgr._on_execution_failed("dsh", {"sessionId": "sess-f2", "failureType": "tool_failed",
                                         "retryExhausted": False, "retries": 0,
                                         "errorCode": ""})
        assert len(mgr.win.alerts) == 2, "提醒已收起后工具失败应正常提醒"


class TestModelAccessStreakCleanup:
    """F13：清理模型访问提醒时必须同步清空 tracker 内部 streak。

    只清外部镜像（``_model_access_cache`` / ``_model_access_retry_counts``）会
    让 tracker 里按 (source, session) 留存的连续计数残留；重新开启联动后同一
    会话的新一轮失败直接接着旧计数，提醒里出现「已连续 N 次」虚高。
    """

    class _Win:
        def __init__(self):
            self.alerts = []
            self.resolved = []

        def isVisible(self):
            return True

        def show_alert(self, text, **_kwargs):
            self.alerts.append(str(text))

        def resolve_alert(self, alert_id):
            self.resolved.append(str(alert_id))

        def show_bubble(self, *_args, **_kwargs):
            pass

    def _make_mgr(self, tmp_path):
        return AgentLinkManager(self._Win(), Config(base=tmp_path))

    @staticmethod
    def _retry():
        from pet.agent_event_normalizer import normalize_event
        return normalize_event({"event": "llm/retry", "agent": "dsh", "sessionId": "s-1",
                                "errorCode": "RATE_LIMIT", "errorMessage": "429 too many requests"})

    def _feed_and_next_streak(self, mgr):
        """喂一条限流重试，返回 tracker 记账后的连续计数（经 consume 观察）。"""
        out = mgr._model_access_tracker.consume(self._retry())
        assert out is not None, "限流重试必须产出 streak"
        return int(out["consecutiveRetryCount"])

    def test_clear_resets_tracker_streak(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_normalized_event(self._retry())
        mgr._on_normalized_event(self._retry())
        assert self._feed_and_next_streak(mgr) == 3, "前置：tracker 已累计到 3"
        mgr._clear_model_access_alerts()
        assert self._feed_and_next_streak(mgr) == 1, "清理后 tracker streak 必须清零"

    def test_disable_and_reenable_does_not_inherit_old_streak(self, tmp_path):
        mgr = self._make_mgr(tmp_path)
        mgr._on_normalized_event(self._retry())
        mgr._on_normalized_event(self._retry())
        # 关闭 DSH 联动：apply_config 走 _clear_model_access_alerts
        cfg = dict(mgr.cfg.get("agent_link", {}))
        cfg["dsh"] = False
        mgr.cfg.set("agent_link", cfg)
        mgr.apply_config()
        # 重新开启后再来一次失败：计数必须从 1 开始（不继承旧 streak）
        mgr._on_normalized_event(self._retry())
        assert self._feed_and_next_streak(mgr) == 2, \
            "重启用后不得继承旧 streak，否则提醒计数虚高"

    def test_normalized_event_signal_reaches_consumer(self, tmp_path):
        """守卫：normalized_event 信号必须直连 _on_normalized_event。

        移除 AgentEventRuntime 分发层后这是该信号的唯一消费接线；其余用例
        全部直调 handler，connect 丢失时它们照样全绿——信号级守卫不可省。
        """
        mgr = self._make_mgr(tmp_path)
        mgr.monitors["dsh"].normalized_event.emit(self._retry())
        assert self._feed_and_next_streak(mgr) == 2, "经信号发射的重试事件必须被消费记账"


class TestSessionNameTruthfulness:
    """{sessionName} 只注入真实会话显示名，绝不把 sessionId 截短占位冒充（字段真实性）。

    会话元数据（session/meta）未到达时，get_session_display_name() 会回退成
    "DSH · <id8>" 兜底占位——它不是「会话显示名」，不得注入台词模板（渲染端
    对缺失的条件字段会自动隐藏 {sessionName} 占位符）。
    """

    class _Win:
        cats = {"acts": ["写代码"]}
        idles = ["待机呼吸"]

        def isVisible(self):
            return True

        def show_bubble(self, *_args, **_kwargs):
            pass

    class _AlertWin:
        def __init__(self):
            self.alerts = []
            self.resolved = []
            # 有意不带 _bubble_busy_until：_schedule_model_access_dismiss 据此跳过 QTimer

        def isVisible(self):
            return True

        def show_alert(self, text, **_kwargs):
            self.alerts.append(str(text))

        def resolve_alert(self, alert_id):
            self.resolved.append(str(alert_id))

        def show_bubble(self, *_args, **_kwargs):
            pass

    def _make(self, tmp_path, win=None):
        cfg = Config(base=tmp_path)
        return AgentLinkManager(win or self._Win(), cfg)

    def test_id_fallback_is_not_injected_as_session_name(self, tmp_path):
        mgr = self._make(tmp_path)
        sid = "session-0123456789"
        assert mgr.get_session_display_name(sid) == f"DSH · {sid[:8]}"
        cond = mgr._session_conditional({"sessionId": sid})
        assert "sessionName" not in cond, "无元数据时不得把 id 截短占位注入为会话名"
        assert not any("session-" in str(v) for v in cond.values())

    def test_real_session_name_from_meta_is_injected(self, tmp_path):
        mgr = self._make(tmp_path)
        sid = "session-0123456789"
        mgr._on_session_meta("dsh", {"sessionId": sid, "projectName": "深海项目", "sessionName": "排障对话"})
        cond = mgr._session_conditional({"sessionId": sid})
        # sessionName 只取会话名自身，绝不拼 projectName（两字段语义独立）
        assert cond["sessionName"] == "排障对话"
        assert cond["projectName"] == "深海项目", "projectName 是独立字段"
        assert mgr._session_name_or_empty(sid) == "排障对话"
        # 组合展示串仍只属于展示 API（气泡前缀/探索气泡），不冒充会话名字段
        assert mgr.get_session_display_name(sid) == "深海项目 · 排障对话"

    def test_model_access_alert_does_not_inject_session_name_without_meta(self, tmp_path):
        mgr = self._make(tmp_path, win=self._AlertWin())
        captured = {}
        mgr._dialogue = lambda key, fallback, **kw: (captured.update(kw), fallback)[1]
        mgr._show_model_access_alert("dsh", "session-abcdef12", 1)
        assert "sessionName" not in captured, "模型访问失败提醒无会话元数据时不得注入 sessionName"

    def test_session_meta_cache_is_bounded(self, tmp_path):
        """缺陷 23：会话元数据缓存必须有界（FIFO 淘汰最老条目）。

        key 是外部会话 ID，常驻数周按会话数单调增长（此前没有任何删除路径）；
        同项目其它缓存都有界（webm_clip 首帧共享表 20000 / sound_winmm 池 8）。
        """
        mgr = self._make(tmp_path)
        limit = type(mgr)._SESSION_CACHE_MAX
        for i in range(limit + 8):
            mgr._on_session_meta(
                "dsh", {"sessionId": f"s-{i:04d}", "sessionName": f"会话{i}"})

        cache = mgr._session_meta_cache
        assert len(cache) == limit, "元数据缓存必须封顶"
        assert "s-0000" not in cache and "s-0007" not in cache, "最老条目必须被淘汰"
        newest = f"s-{limit + 7:04d}"
        assert newest in cache, "近期条目必须保留"
        # 读路径行为不变：命中的元数据照常解析
        assert mgr.get_session_display_name(newest) == f"会话{limit + 7}"
        assert mgr._session_name_or_empty(newest) == f"会话{limit + 7}"

class TestInstallFinishedGuard:
    """安装后台线程完成回调不得越过 manager 生命周期：窗口关闭/角色切换
    （shutdown）或重新禁用后，迟到的 install_finished 不得写配置/启动
    监视器/弹气泡（daemon 安装线程本身无法被取消，只能拦完成回调）。"""

    def _make_manager(self, tmp_path, bubbles, monkeypatch, release):
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            DshMonitor, "uninstall_bridge", classmethod(lambda cls: True),
        )

        def fake_install():
            assert release.wait(timeout=5.0), "测试释放信号未到达"
            return (True, "ok")

        monkeypatch.setattr(
            DshMonitor, "install_bridge", classmethod(lambda cls: fake_install()),
        )
        return cfg, mgr

    def _install_thread(self):
        return next(
            t for t in threading.enumerate() if t.name == "dsh-bridge-install"
        )

    def test_late_completion_after_shutdown_is_dropped(self, tmp_path, monkeypatch):
        """安装完成发生在 shutdown（窗口关闭/角色切换）之后 → 完成回调被
        丢弃：配置不写回、监视器不启动、无完成气泡。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        assert "dsh" in mgr._install_pending
        assert bubbles == ["正在为 DSH 安装通信桥。"]  # persona legacy: bridge.install.pending
        mgr.shutdown()   # 安装完成前 manager 被关闭（窗口 close / 角色切换）
        release.set()    # 安装此刻才完成
        install_thread.join(timeout=5.0)
        app.processEvents()
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_queued_completion_dispatched_after_shutdown_is_dropped(self, tmp_path, monkeypatch):
        """emit→dispatch 竞态：信号在 shutdown 前已 emit 入队（worker 已
        完成），但回调在 shutdown 之后才被派发 → 同样必须丢弃（完成回调
        在 GUI 线程的权威校验兜住该窗口）。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        release.set()                 # 安装完成 → worker emit（queued 入队）
        install_thread.join(timeout=5.0)
        # 未跑 processEvents：queued 回调仍躺在 GUI 事件队列里
        mgr.shutdown()                # 关闭发生在回调派发之前
        app.processEvents()           # 迟到的 queued 回调此刻才派发 → 丢弃
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_late_completion_after_redisable_is_dropped(self, tmp_path, monkeypatch):
        """用户重新关闭联动（安装进行中）→ 在途安装作废，完成回调被丢弃，
        不得反向把配置写回 True / 启动监视器。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        assert "dsh" in mgr._install_pending
        mgr.set_enabled("dsh", False)  # 用户重新关闭联动 → 在途安装作废
        assert "dsh" not in mgr._install_pending
        release.set()
        install_thread.join(timeout=5.0)
        app.processEvents()
        app.processEvents()
        assert cfg.data["agent_link"]["dsh"] is False
        assert not mgr.monitors["dsh"]._running
        assert bubbles == ["正在为 DSH 安装通信桥。"], "不得弹完成气泡"

    def test_stale_queued_completion_must_not_consume_reinstalled_pending(self, tmp_path, monkeypatch):
        """B9 复审 P1：disable→re-enable 两代安装交错。安装 A 完成信号已
        queued 入队但未派发时，用户关闭联动并再次开启、登记安装 B（新代次）；
        A 的旧回调随后派发时不得消费 B 的 pending（不得写配置/启动监视器/
        弹完成气泡），B 的真实回调随后正常生效。完成信号必须携带并校验
        安装代次，仅凭 agent key 无法区分两代安装。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            DshMonitor, "uninstall_bridge", classmethod(lambda cls: True),
        )

        release_a, release_b = threading.Event(), threading.Event()
        queue = [release_a, release_b]

        def fake_install():
            ev = queue.pop(0)
            assert ev.wait(timeout=5.0), "测试释放信号未到达"
            return (True, "ok")

        monkeypatch.setattr(
            DshMonitor, "install_bridge", classmethod(lambda cls: fake_install()),
        )

        def install_thread():
            return next(
                t for t in threading.enumerate() if t.name == "dsh-bridge-install"
            )

        # 第一代安装 A：等它完成并 emit（queued 入队），但先不派发
        mgr.set_enabled("dsh", True)
        thread_a = install_thread()
        release_a.set()
        thread_a.join(timeout=5.0)
        assert not thread_a.is_alive()
        assert "dsh" in mgr._install_pending   # A 的 pending 尚未被消费
        # 未跑 processEvents：A 的 queued 回调仍躺在 GUI 事件队列里
        mgr.set_enabled("dsh", False)          # 关闭联动：作废在途安装
        assert "dsh" not in mgr._install_pending
        mgr.set_enabled("dsh", True)           # 再次开启：登记安装 B（新代次）
        assert "dsh" in mgr._install_pending
        thread_b = install_thread()
        # 此刻派发 A 的旧 queued 回调：不得消费 B 的 pending
        app.processEvents()
        app.processEvents()
        assert "dsh" in mgr._install_pending           # B 的 pending 必须仍在
        assert cfg.data["agent_link"]["dsh"] is False  # 配置不得被 A 写回
        assert not mgr.monitors["dsh"]._running        # 监视器不得被 A 启动
        assert not any("安装完成" in b for b in bubbles), f"不得弹完成气泡: {bubbles}"
        # B 的真实回调随后派发：正常生效（写配置、启动监视器、弹气泡）
        release_b.set()
        thread_b.join(timeout=5.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            if cfg.data["agent_link"]["dsh"]:
                break
            time.sleep(0.01)
        assert cfg.data["agent_link"]["dsh"] is True
        assert mgr.monitors["dsh"]._running
        assert any("安装完成" in b for b in bubbles), f"应有 B 的完成气泡: {bubbles}"

    def test_normal_completion_still_applies(self, tmp_path, monkeypatch):
        """正常路径回归：安装完成后回调照常生效（写配置、启动监视器、
        弹完成气泡）。"""
        app = QApplication.instance() or QApplication([])
        bubbles = []
        release = threading.Event()
        cfg, mgr = self._make_manager(tmp_path, bubbles, monkeypatch, release)
        mgr.set_enabled("dsh", True)
        install_thread = self._install_thread()
        release.set()
        install_thread.join(timeout=5.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            if cfg.data["agent_link"]["dsh"]:
                break
            time.sleep(0.01)
        assert cfg.data["agent_link"]["dsh"] is True
        assert mgr.monitors["dsh"]._running
        assert any("安装完成" in b for b in bubbles), f"应有完成气泡，实际: {bubbles}"
        mgr.set_enabled("dsh", False)
        assert cfg.data["agent_link"]["dsh"] is False

class TestUnknownBridgeEventReminder:
    """未知桥接事件 → 提醒用户更新/重装 bridge。

    识别：DSH 监视器里，事件名在「语义层 / 状态机 / _poll 直通名单」全部
    不认识才算未知（claude/cursor 的 transcript 噪声不算）。提醒受 bridge
    概率门控制，同一 agent 在冷却窗口内只弹一次（未知事件成串时不刷屏）。
    """

    def _make_mgr(self, tmp_path):
        app = QApplication.instance() or QApplication([])
        bubbles = []

        class DummyWin:
            def isVisible(self):
                return True

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        cfg = Config(base=tmp_path)
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, bubbles, clock

    def test_monitor_emits_only_unknown_dsh_events(self, tmp_path):
        """监视器只对「全识别路径都不认识」的 DSH 事件发 unknown_bridge_event。"""
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "brand/sparkle", "ts": 1}) + "\n")             # 未知
            f.write(json.dumps({"event": "execution/failed", "ts": 2}) + "\n")          # 语义层已知
            f.write(json.dumps({"event": "agent/status", "state": "working", "ts": 3}) + "\n")  # 状态机已知
            f.write(json.dumps({"event": "model_access", "errorCode": "429", "ts": 4}) + "\n")   # 直通名单已知
            f.write(json.dumps({"event": "cordis/request-run", "ts": 5}) + "\n")        # 直通名单已知
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_user_message_is_not_unknown(self, tmp_path):
        """桥的核心合法事件绝不判为「未知桥接事件」。

        回归：DSH 的 user/message 是扁平记录（无 type/source 字段），语义层
        normalize_event 返回 None、状态机不建模 → 漏登记直通名单会把每次真人
        消息（对话开始）误判成「更新/重装 bridge」提醒（10 分钟冷却 → 表现为
        「有时候触发」的未知事件）。bridge/diagnostic、command/done、
        pet/control-clicked、bridge/control-received 同属漏网：都是桥合法发出
        的事件，语义层未建模，必须经直通名单兜底。真正未知的事件照常触发。
        """
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            # 与桥写出的形态一致：扁平记录，无 type 字段
            f.write(json.dumps({"event": "user/message", "text": "hi", "step": None,
                                "sessionId": "s1", "ts": 1}) + "\n")
            f.write(json.dumps({"event": "bridge/diagnostic", "bridgeDir": "X", "ts": 2}) + "\n")
            f.write(json.dumps({"event": "command/done", "step": 1, "ts": 3}) + "\n")
            f.write(json.dumps({"event": "pet/control-clicked", "ts": 4}) + "\n")
            f.write(json.dumps({"event": "bridge/control-received", "ts": 5}) + "\n")
            f.write(json.dumps({"event": "brand/sparkle", "ts": 6}) + "\n")  # 真未知仍要报
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_watchdog_and_control_events_are_not_unknown(self, tmp_path):
        """桥接/桌宠回显真实会写、语义层与状态机都没建模的事件不得判成「未知」。

        漏登记后果与 user/message 同型：每次写盘触发一次「更新/重装 bridge」
        误提醒（10 分钟冷却 → 表现为偶发弹窗）。来源：
        - tool-workflow/run-end：桥接 STATE_EVENT_TYPES（与已登记的 run-start 成对）；
        - web_search_begin / web_search_end / context_compacted：桥接
          WATCHDOG_EVENT_TYPES 直写（供探索看门狗，非状态迁移）；
        - pet/control-queued：旧版控制队列写盘回显（dsh_control 已删，仅兼容旧桥）。
        """
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("dsh", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()  # 初始化 tailer（首轮不重放）
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "tool-workflow/run-end", "step": 1, "ts": 1}) + "\n")
            f.write(json.dumps({"event": "web_search_begin", "ts": 2}) + "\n")
            f.write(json.dumps({"event": "web_search_end", "ts": 3}) + "\n")
            f.write(json.dumps({"event": "context_compacted", "ts": 4}) + "\n")
            f.write(json.dumps({"event": "pet/control-queued", "ts": 5}) + "\n")
            f.write(json.dumps({"event": "brand/sparkle", "ts": 6}) + "\n")  # 真未知仍要报
        mon._poll()
        assert [(k, d.get("event")) for k, d in unknown] == [("dsh", "brand/sparkle")]
        mon.stop()

    def test_non_dsh_monitor_never_emits_unknown(self, tmp_path):
        """claude/cursor 等 transcript 噪声不算桥接未知事件（只查 DSH 监视器）。"""
        app = QApplication.instance() or QApplication([])
        mon = BaseAgentMonitor("cursor", tmp_path)
        unknown = []
        mon.unknown_bridge_event.connect(lambda k, d: unknown.append((k, d)))
        events_file = mon.events_file
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        mon._poll()
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "brand/sparkle", "ts": 1}) + "\n")
        mon._poll()
        assert unknown == []
        mon.stop()

    def test_wiring_manager_bubbles_reminder(self, tmp_path):
        """Monitor → Manager 全链路：收到未知事件即弹更新/重装提醒。"""
        mgr, bubbles, _ = self._make_mgr(tmp_path)
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 1
        assert "更新" in bubbles[0] or "重装" in bubbles[0], bubbles[0]
        mgr.shutdown()

    def test_cooldown_reminds_once_per_window(self, tmp_path):
        """同一 agent 冷却窗口内只提醒一次；窗口过后再次提醒。"""
        mgr, bubbles, clock = self._make_mgr(tmp_path)
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 1, "冷却窗口内重复未知事件不得刷屏"
        clock[0] += 601.0
        mgr.monitors["dsh"].unknown_bridge_event.emit("dsh", {"event": "brand/sparkle"})
        assert len(bubbles) == 2, "冷却窗口过后应再次提醒"
        mgr.shutdown()


class TestNotifyDshState:
    """dsh_state 收敛状态注入（notify_dsh_state）回归。

    legacy AgentStatus 基线只有 working/idle（bridge 设计），thinking 等状态由
    dsh_state.py 收敛后经此喂给既有呈现管线——DSH 的思考气泡/对话开始反应
    因此稳定触发（此前该状态对 legacy 监视器结构性不可见）。
    """

    def _make_mgr(self, tmp_path):
        app = QApplication.instance() or QApplication([])
        switched = []
        bubbles = []

        class DummyWin:
            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0

            def isVisible(self):
                return True

            def _switch(self, name):
                switched.append(name)

            def request_link_anim(self, name):
                switched.append(name)

            def request_link_idle(self):
                if self.idles:
                    switched.append(self.idles[0])

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def _pick(self, lst):
                return lst[0]

        win = DummyWin()
        win.switched = switched
        cfg = Config(base=tmp_path)
        clock = [1000.0]
        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])
        return mgr, win, bubbles, clock

    def test_thinking_drives_bubble_and_anim_when_linked(self, tmp_path):
        """联动开启（白盒模拟 DSH 监视器运行）时，thinking 必须到达气泡+动画。

        此前 DSH 的 thinking 只存在于 dsh_state 收敛结果里、永远不进 legacy 管线，
        思考气泡从不触发；notify_dsh_state 必须打通这条链。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        mgr.monitors["dsh"]._running = True  # white-box：等效 agent_link.dsh 已启用
        try:
            mgr.notify_dsh_state("thinking")
        finally:
            mgr.shutdown()
        assert any("思考" in b for b in bubbles), bubbles
        assert win.switched, "thinking 必须驱动联动动画"

    def test_thinking_noop_when_link_disabled(self, tmp_path):
        """DSH 联动未开启（监视器未运行）时注入为 no-op，不惊动用户。"""
        mgr, win, bubbles, clock = self._make_mgr(tmp_path)
        try:
            mgr.notify_dsh_state("thinking")
        finally:
            mgr.shutdown()
        assert bubbles == []
        assert win.switched == []


# ============================================================================
# 14. DSH 回合成功结束（turn/end → success）必须触发完成提醒（issue #234）
# ============================================================================
class TestDshTurnEndSuccessCompletion:
    """DSH 跑完一个回合（``turn/end`` → 收敛状态 ``success``）出完成气泡 + done 音效。

    修复前的三道断链（本类逐条钉住）：
    1. ``dsh_state`` 收敛器对 ``turn/end`` 正确产出 ``success``，但
       ``_on_dsh_converged_state`` 没有 success 分支——状态进不了呈现管线；
    2. ``_on_agent_state`` 的完成边沿只认 ``busy→attention/error`` 与
       ``busy→idle/sleeping``，缺 ``busy→success``：``_schedule_done_check``
       从不被调度，800ms 确认/冷却/done 音效全部落空；
    3. 灵动岛侧不受影响——``dsh_state_changed`` 在分支之前无条件 emit，
       该顺序是既有契约（本类用例一并钉住）。

    ``success`` 只走 ``_last_raw`` 这条**原始状态流**（所有消费方按
    ``in _BUSY_STATES`` 判定，无等值白名单）；**不得**加进 ``VALID_STATES``
    ——那是所有监视器共用的归一入口，加值会改 Claude/Cursor 的判决口径。

    本类同时钉住这条完成契约的**反向**：硬失败回合不得误报成功——桥接判定失败
    后先写 ``execution/failed``、随后仍无条件转发 ``turn/end``，收敛器一律收敛
    ``success``，不拦就是「失败提醒 + 800ms 后弹已完成本轮任务 + done 音效」。
    """

    _SOUND_PATHS = {
        "builtin:agent-start": "start.wav",
        "builtin:agent-done": "done.wav",
        "builtin:agent-error": "error.wav",
    }

    @staticmethod
    def _pump(app, seconds: float) -> None:
        """跑满一段事件循环（负向断言专用：给被取消/不该存在的定时器犯错的机会）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    @classmethod
    def _wait_until(cls, app, predicate, timeout: float = 5.0) -> bool:
        """宽预算轮询等待（禁止固定 sleep 猜时序：800ms 确认窗在慢 runner 上会漂）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        app.processEvents()
        return bool(predicate())

    def _make_mgr(self, tmp_path, monkeypatch):
        """真 manager（含真 DshMonitor，worker 不启动，手动驱动 _poll）+ 假窗口。

        事件目录按 ``Config(base=tmp_path/"cfg")`` 的不变量落在
        ``tmp_path/dsh-pet-bridge``（与减法测试的 live manager 同构）。
        """
        app = QApplication.instance() or QApplication([])
        bubbles, sounds = [], []
        files = {}
        for value, name in self._SOUND_PATHS.items():
            path = tmp_path / name
            path.write_bytes(b"RIFF")
            files[value] = path
        # 音效钉在播放漏斗层（winmm 直放 / Qt 后端都逃不掉）
        monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda p: files.get(str(p)))
        for fn in ("play_sound", "play_with_winmm", "play_with_qt"):
            monkeypatch.setattr(click_sound._pool, fn,
                                lambda path, volume=1.0: sounds.append(Path(path).name) or True)

        class DummyWin:
            cats = {"acts": ["写代码", "吃Token"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0
            _alert_current = None
            _alert_queue: list = []
            _sticky_bubble_active = False

            def isVisible(self):
                return True

            def _switch(self, name):
                pass

            def request_link_anim(self, name):
                pass

            def request_link_idle(self):
                pass

            def show_bubble(self, text, duration_ms=3000, **_kw):
                bubbles.append(str(text))

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path / "cfg")
        cfg.data["agent_link"].update({
            "sound_enabled": True,
            "sound_cooldown_seconds": 0.0,
            "sound_start_path": "builtin:agent-start",
            "sound_done_path": "builtin:agent-done",
            "sound_error_path": "builtin:agent-error",
        })
        cfg.save()
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=0.0, clock=lambda: clock[0])
        mon = mgr.monitors["dsh"]
        # 白盒：等效 agent_link.dsh 已启用——收敛管线的迟到信号隔离闸只放行
        # 「监视器在跑」的信号，手动驱动 _poll 的测试必须置真。
        mon._running = True
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon._tailer._initial_backfill_done = True
        return mgr, bubbles, sounds, clock, app

    @staticmethod
    def _write(bridge_dir, *records):
        with (bridge_dir / "dsh.jsonl").open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_working_to_success_plays_done_bubble_and_sound(self, tmp_path, monkeypatch):
        """① working→success：调度 800ms 完成确认，到期后出完成气泡 + done 音效。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "working")
            bubbles.clear()
            sounds.clear()
            mgr._on_agent_state("dsh", "success")
            assert "dsh" in mgr._done_pending, "busy→success 必须调度完成确认"
        except BaseException:
            mgr.shutdown()
            raise
        try:
            assert self._wait_until(app, lambda: bool(bubbles)), \
                "800ms 确认到期后必须弹完成气泡"
        finally:
            mgr.shutdown()
        assert any("已完成本轮任务" in b for b in bubbles), bubbles
        assert sounds.count("done.wav") == 1, sounds

    def test_returning_to_busy_inside_confirm_window_cancels_done(self, tmp_path, monkeypatch):
        """② 确认窗口内回忙：取消确认，且不得留下迟到的气泡/音效。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "working")
            mgr._on_agent_state("dsh", "success")
            assert "dsh" in mgr._done_pending
            mgr._on_agent_state("dsh", "working")   # 800ms 内回忙（工具重试/继续干活）
            assert "dsh" not in mgr._done_pending, "回忙必须取消完成确认"
            bubbles.clear()
            sounds.clear()
            self._pump(app, 1.2)                    # 超过确认窗：被取消的定时器不得再弹
            mgr._fire_done("dsh")                   # 兜底：即便定时器仍到点也不得播
            assert bubbles == [], bubbles
            assert sounds == [], sounds
        finally:
            mgr.shutdown()

    def test_success_then_idle_does_not_repeat_done(self, tmp_path, monkeypatch):
        """③ success→idle 不重复播：完成提醒只在 busy→success 边沿调度一次。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "working")
            mgr._on_agent_state("dsh", "success")
            assert self._wait_until(app, lambda: any("已完成本轮任务" in b for b in bubbles)), bubbles
            n_bubbles = len(bubbles)
            # 收敛器在 turn/end 之后可能补一条 idle 基线：不得二次调度完成确认
            mgr._on_agent_state("dsh", "idle")
            assert "dsh" not in mgr._done_pending, "success→idle 不得再调度完成确认"
            self._pump(app, 0.3)                    # 确认窗余量：不得重复弹/重复响
            assert len(bubbles) == n_bubbles, bubbles
            assert sounds.count("done.wav") == 1, sounds
        finally:
            mgr.shutdown()

    def test_error_cycle_plays_no_done_sound(self, tmp_path, monkeypatch):
        """④ error 周期（llm/retry 等）出 error 音效与停止口径气泡，绝不播 done。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "working")
            bubbles.clear()
            sounds.clear()
            mgr._on_agent_state("dsh", "error")
            assert "dsh" in mgr._done_pending, "busy→error 仍走完成确认（既有契约）"
            mgr._fire_done("dsh")
            assert "error.wav" in sounds, sounds
            assert "done.wav" not in sounds, "error 周期不得播 done 音效"
            assert bubbles and all("已完成本轮任务" not in b for b in bubbles), bubbles
        finally:
            mgr.shutdown()

    def test_converged_success_enters_presentation_pipeline(self, tmp_path, monkeypatch):
        """⑤ 消融守门：收敛器产出 success 时必须被送进呈现管线。

        删掉 ``_on_dsh_converged_state`` 的 success 分支（修复前原状）本用例必须红
        ——它就是这个断链的回归闸；同时钉住岛侧 emit 仍在分支之前无条件执行。
        """
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        converged = []
        mgr.dsh_state_changed.connect(lambda f, t: converged.append((f, t)))
        try:
            mgr._on_agent_state("dsh", "working")   # 真实回合里由 turn/start 落下 busy
            mgr._on_dsh_converged_state("working", "success", "turn/end")
            assert converged == [("working", "success")], "岛侧转发（绿点）不得被新分支吃掉"
            assert mgr._last_raw.get("dsh") == "success", "success 必须到达呈现管线"
            assert "dsh" in mgr._done_pending, "success 必须调度完成确认"
        finally:
            mgr.shutdown()

    def test_aborted_turn_returns_to_idle_without_done(self, tmp_path, monkeypatch):
        """用户中止回合（收敛 idle，来源 turn/end）：回待机、不再算忙、不出完成提醒。

        删掉 ``_on_dsh_converged_state`` 的 idle 分支本用例必须红：``_last_raw`` 会
        停在 thinking（桌宠一直播忙碌动画、识屏一直把 DSH 当忙）。
        """
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "thinking")
            bubbles.clear()
            sounds.clear()
            mgr._on_dsh_converged_state("thinking", "idle", "turn/end")
            assert mgr._last_raw.get("dsh") == "idle", "中止后必须回待机，不得停在忙碌"
            assert mgr._last_raw.get("dsh") not in mgr._BUSY_STATES
            assert "dsh" not in mgr._done_pending, "中止不是完成：不得排定完成确认"
            assert "done.wav" not in sounds
            assert all("已完成本轮任务" not in b for b in bubbles), bubbles
        finally:
            mgr.shutdown()

    def test_real_bridge_aborted_turn_returns_idle_without_done(self, tmp_path, monkeypatch):
        """端到端：真实中止记录经真 DshMonitor._poll / 真收敛器 → 回待机、不出完成。

        覆盖两种到达顺序：中止 turn/end 直接到达；以及旧 AgentStatus idle 基线
        先到（已排下完成确认）后再到中止 turn/end——后者必须撤掉那个确认。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        bridge_dir = mon.events_dir
        try:
            mon._probe_online()
            self._write(bridge_dir,
                        {"event": "turn/start", "sessionId": "s-1"},
                        {"event": "tool/call", "sessionId": "s-1", "tool": "bash"})
            mon._poll()
            assert mgr._last_raw.get("dsh") in mgr._BUSY_STATES
            bubbles.clear()
            sounds.clear()
            self._write(bridge_dir, {"event": "turn/end", "sessionId": "s-1",
                                     "reasonKind": "aborted"})
            mon._poll()
            app.processEvents()
            assert mgr._last_raw.get("dsh") == "idle", mgr._last_raw
            assert "dsh" not in mgr._done_pending, "中止不得排定完成确认"
            # 第二轮：AgentStatus idle 基线先到，再到中止 turn/end
            self._write(bridge_dir,
                        {"event": "turn/start", "sessionId": "s-1"},
                        {"event": "tool/call", "sessionId": "s-1", "tool": "bash"})
            mon._poll()
            app.processEvents()
            mgr.notify_dsh_state("working")
            mgr.notify_dsh_state("idle")          # 旧基线：busy→idle 排定确认
            assert "dsh" in mgr._done_pending
            self._write(bridge_dir, {"event": "AgentStatus", "state": "idle"},
                        {"event": "turn/end", "sessionId": "s-1", "reasonKind": "aborted"})
            mon._poll()
            app.processEvents()
            assert "dsh" not in mgr._done_pending, "中止必须撤掉基线排下的确认"
            assert not self._wait_until(app, lambda: any("已完成本轮任务" in b for b in bubbles),
                                        timeout=1.2), bubbles
        finally:
            mgr.shutdown()
        assert "done.wav" not in sounds, sounds

    def test_repeated_abort_keeps_other_session_success_confirm(self, tmp_path, monkeypatch):
        """另一会话真实完成排下的确认（来源 success）不得被中止撤掉——重复中止也不行。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "thinking")
            mgr._on_agent_state("dsh", "success")      # 真实完成 → 排定 success 确认
            assert mgr._done_cause.get("dsh") == "success"
            mgr._on_dsh_converged_state("success", "idle", "turn/end")
            mgr._on_dsh_converged_state("idle", "idle", "turn/end")   # 重复中止
            assert "dsh" in mgr._done_pending, "真实完成的确认不得被中止撤掉"
            assert mgr._last_raw.get("dsh") == "idle"
        finally:
            mgr.shutdown()

    def test_abort_does_not_clear_other_session_interactions(self, tmp_path, monkeypatch):
        """会话 A 中止注入 idle 不得全量清掉会话 B 仍在等待的审批/问题气泡；
        A 自己的交互由 _on_interaction_lifecycle 按会话定向清理。"""
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "thinking")
            for sid in ("A", "B"):
                mgr._pending_interactions[f"q-{sid}"] = {
                    "kind": "question", "text": sid, "alert_id": "",
                    "agent_key": "dsh", "session_id": sid, "_ts_mono": 0.0}
            mgr._on_dsh_converged_state("thinking", "idle", "turn/end")
            assert set(mgr._pending_interactions) >= {"q-A", "q-B"}
            assert mgr._idle_clear_scoped is False
            mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "A",
                                                  "reasonKind": "aborted"})
            assert "q-A" not in mgr._pending_interactions
            assert "q-B" in mgr._pending_interactions, "B 的等待气泡不得被 A 的中止撤掉"
        finally:
            mgr.shutdown()

    def test_success_is_not_a_global_valid_state(self):
        """``success`` 不得进入 ``VALID_STATES``：那是所有监视器共用的归一入口。

        它只在 DSH 收敛器→呈现管线的这条原始状态流里存在。把 success 加进
        归一白名单会扩大 Claude/Cursor 的判决口径（transcript 里出现 success
        字样的行会被判成合法状态），是这条修复最容易走错的「顺手改法」。
        """
        assert "success" not in agent_link.VALID_STATES
        assert normalize_event_state("", "success") == ""

    def test_real_bridge_turn_records_reach_done_bubble(self, tmp_path, monkeypatch):
        """⑥ 端到端：真实记录形状 → 真收敛器 → 真出口。

        ``turn/start → tool/call → turn/end`` 三条桥接记录经真 ``DshMonitor._poll``
        （含真 ``DshStateConverger``）流到 ``AgentLinkManager``，最终出完成气泡。
        本机无 DSH+桥接真实环境，验到「真实记录形状 → 真实收敛器 → 真实出口」一层。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        bridge_dir = mon.events_dir
        converged = []
        mon.dsh_state_changed.connect(lambda f, t, src: converged.append(t))
        try:
            mon._probe_online()                     # 在线基线 → idle
            self._write(bridge_dir,
                        {"event": "turn/start", "sessionId": "s-1"},
                        {"event": "tool/call", "sessionId": "s-1", "tool": "bash"})
            mon._poll()
            assert converged == ["idle", "thinking", "working"], converged
            assert mgr._last_raw.get("dsh") == "thinking", mgr._last_raw
            bubbles.clear()
            sounds.clear()
            self._write(bridge_dir, {"event": "turn/end", "sessionId": "s-1"})
            mon._poll()
            assert mon._converger.current_state is DshState.SUCCESS
            assert "dsh" in mgr._done_pending, "turn/end 的 success 必须走到完成确认"
            assert self._wait_until(app, lambda: any("已完成本轮任务" in b for b in bubbles)), bubbles
        finally:
            mgr.shutdown()
        assert sounds.count("done.wav") == 1, sounds

    # ------------------------------------------------------------------
    # 硬失败回合（execution/failed）不得误报成功
    # ------------------------------------------------------------------
    #
    # 桥接在 turn/end 判定 reason.kind === "error" 后**先**写 execution/failed，
    # **随后**仍无条件把同一条 turn/end 作为状态事件转发（STATE_EVENT_TYPES）；
    # 收敛器把一切 turn/end 收敛为 success。execution/failed 的 pet 侧处理只弹
    # 失败提醒、不注入原始状态流，于是 success 边沿看到的 prev_raw 仍是 busy——
    # 失败回合照样排定完成确认，800ms 后弹「已完成本轮任务」并播 done 音效。
    #
    # 契约：execution/failed 的 GUI 落点（AgentLinkManager._on_execution_failed，
    # 按 agent 打标）给该 Agent 打 _saw_error（掐 done 音效 / success 边沿的完成
    # 确认）+ _saw_alert（收尾文案切离「已完成」口径）；两个标记在下一次 busy
    # 入口一起清除，下一回合照常恢复。

    @staticmethod
    def _failed_turn_records(session: str = "s-1") -> list[dict]:
        """失败回合的五条真实记录（与桥接写盘顺序一致）。"""
        return [
            {"event": "turn/start", "sessionId": session},
            {"event": "tool/call", "sessionId": session, "tool": "bash", "callId": "call-1"},
            {"event": "tool/result", "sessionId": session, "tool": "bash",
             "callId": "call-1", "ok": False, "errorCode": "E_BASH",
             "errorMessage": "exit status 1"},
            {"event": "execution/failed", "sessionId": session,
             "failureType": "tool_failed", "errorCode": "E_BASH",
             "errorMessage": "exit status 1"},
            {"event": "turn/end", "sessionId": session},
        ]

    def test_failed_turn_reports_failure_without_done(self, tmp_path, monkeypatch):
        """⑦ 失败序列：只出失败提醒，不排定完成确认（无成功气泡、无 done 音效）。"""
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()                     # 在线基线 → idle
            self._write(mon.events_dir, *self._failed_turn_records())
            mon._poll()
            assert any("工具执行失败" in b or "本轮运行失败" in b for b in bubbles), bubbles
            assert "dsh" in mgr._saw_error, "execution/failed 必须在 manager 侧按 agent 打标"
            assert mon._converger.current_state is DshState.SUCCESS, \
                "turn/end 仍收敛 success（收敛器语义不动，抑制发生在呈现侧）"
            assert "dsh" not in mgr._done_pending, "硬失败回合不得排定完成确认"
            bubbles.clear()
            sounds.clear()
            self._pump(app, 1.2)                    # 超过 800ms 确认窗：不得有完成气泡/音效
            assert bubbles == [], bubbles
            assert sounds == [], sounds
        finally:
            mgr.shutdown()

    def test_failed_turn_sends_error_not_success_to_the_island(self, tmp_path, monkeypatch):
        """⑩ 岛侧：失败回合的 turn/end 必须翻译成 error，不得亮绿点「刚完成」。

        收敛器语义不动（``turn/end`` 仍收敛 success，⑦ 已钉住），翻译只发生在
        ``_on_dsh_converged_state`` 的 emit 处——那是灵动岛 ``set_dsh_state`` 的
        唯一入口。原样转发 ``success`` 会让岛走 ``_arm_dot_done``（绿点 4s + 卡片
        「刚完成」），与同屏的失败提醒自相矛盾。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        converged = []
        mgr.dsh_state_changed.connect(lambda f, t: converged.append((f, t)))
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._failed_turn_records())
            mon._poll()
            assert mon._converger.current_state is DshState.SUCCESS, "收敛器语义不动"
            assert "dsh" in mgr._saw_error
            assert ("working", "success") not in converged, \
                f"失败回合不得给岛转发 success（绿点/「刚完成」）：{converged}"
            assert converged[-1] == ("working", "error"), converged
        finally:
            mgr.shutdown()

    def test_normal_turn_still_sends_success_to_the_island(self, tmp_path, monkeypatch):
        """⑪ 非回归：正常成功回合的岛绿点照旧（翻译只认失败标记）。"""
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        converged = []
        mgr.dsh_state_changed.connect(lambda f, t: converged.append((f, t)))
        try:
            mon._probe_online()
            self._write(mon.events_dir,
                        {"event": "turn/start", "sessionId": "s-1"},
                        {"event": "tool/call", "sessionId": "s-1", "tool": "bash"},
                        {"event": "turn/end", "sessionId": "s-1"})
            mon._poll()
            assert ("working", "success") in converged, converged
            assert ("working", "error") not in converged, converged
        finally:
            mgr.shutdown()

    def test_failed_turn_without_turn_end_leaves_island_channel_alone(self, tmp_path, monkeypatch):
        """⑫ 钉住既有语义：execution/failed 后没有 turn/end 时不产生成功边沿。

        这条路径上岛看到的是最后一个真实事件态（working），错误红点的呈现由
        ``_on_agent_state`` 的 busy→error 负责——本修复**不**在这里发明新状态。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        converged = []
        mgr.dsh_state_changed.connect(lambda f, t: converged.append((f, t)))
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._failed_turn_records()[:-1])
            mon._poll()
            assert "dsh" in mgr._saw_error
            assert converged == [("", "idle"), ("idle", "thinking"), ("thinking", "working")], converged
        finally:
            mgr.shutdown()

    def test_next_turn_after_failed_turn_plays_done_again(self, tmp_path, monkeypatch):
        """⑧ 失败回合之后的下一个正常回合：清标后恢复完成气泡 + done 音效。"""
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._failed_turn_records())
            mon._poll()
            assert "dsh" not in mgr._done_pending
            bubbles.clear()
            sounds.clear()
            self._write(mon.events_dir,
                        {"event": "turn/start", "sessionId": "s-1"},
                        {"event": "tool/call", "sessionId": "s-1", "tool": "bash"},
                        {"event": "turn/end", "sessionId": "s-1"})
            mon._poll()
            assert "dsh" not in mgr._saw_error, "下一回合进入 busy 必须清掉上一轮的硬失败标记"
            assert "dsh" in mgr._done_pending, "正常回合必须重新排定完成确认"
            assert self._wait_until(app, lambda: any("已完成本轮任务" in b for b in bubbles)), bubbles
        finally:
            mgr.shutdown()
        assert sounds.count("done.wav") == 1, sounds

    def test_busy_reentry_after_failed_turn_clears_marker(self, tmp_path, monkeypatch):
        """⑨ execution/failed 之后又出现新的 busy（重试后成功）→ 既有清标语义。

        依据 ``_on_agent_state`` 的 busy 入口（pet/agent_link.py:3070-3072）：
        进入 working/thinking 时，只要前一个原始状态不是 error 就 discard
        ``_saw_error``——标记只跨「error→working」存活，其余 busy 入口一律算新
        一轮。所以硬失败标记被「失败后再次干活」清掉，该回合的 turn/end 恢复排定
        完成确认（⑧ 已钉住清标后能播出去，本条只钉清标触发点）。

        注：实测桥接只在 turn/end 判定后写 execution/failed（与 turn/end 同一批
        紧邻写盘），真实序列里两者之间不会插入 busy；这里用 AgentStatus 基线
        （``{"event": "AgentStatus", "state": ...}``，桥接 aggregateWrite 的真实
        形状）合成「失败后回 idle 再干活」这一段，钉住的是标记生命周期本身。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._failed_turn_records()[:-1])   # 不含 turn/end
            mon._poll()
            assert "dsh" in mgr._saw_error, "硬失败标记已打"
            self._write(mon.events_dir,
                        {"event": "AgentStatus", "state": "idle"},
                        {"event": "AgentStatus", "state": "working"},
                        {"event": "turn/end", "sessionId": "s-1"})
            mon._poll()
            assert "dsh" not in mgr._saw_error, "非 error 的 busy 入口必须清标（3072 既有语义）"
            assert "dsh" in mgr._done_pending, "清标后本回合的成功收尾恢复排定"
        finally:
            mgr.shutdown()

    def test_failed_turn_with_idle_before_execution_failed_never_claims_success(
            self, tmp_path, monkeypatch):
        """⑩ 失败回合的第二条完成路径：AgentStatus idle 先于 execution/failed 落地。

        桥接的 AgentStatus 聚合基线（agent 对象状态监听）与 session/event 流
        （turn/end、execution/failed）是两条独立通道，idle 可能先到。此时
        ``busy→idle`` 已经排定了完成确认（既有契约），随后 execution/failed 打标，
        turn/end 到达时 prev_raw 已不是 busy——success 边沿根本不排定，收尾走的
        就是这条已排定的确认。它没有 success 边沿可拦，只能靠标记把 done 音效
        （``_fire_done`` 的 ``_saw_error`` 闸）与收尾文案（``_saw_alert`` 决定
        说「已完成本轮任务」还是「停了，看一眼」）一并改掉。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._failed_turn_records()[:3],
                        {"event": "AgentStatus", "state": "idle"})     # 聚合基线先到
            mon._poll()
            assert "dsh" in mgr._done_pending, "busy→idle 仍按既有契约排定完成确认"
            self._write(mon.events_dir, *self._failed_turn_records()[3:])   # failed + turn/end
            mon._poll()
            assert mgr._last_raw.get("dsh") == "success"
            bubbles.clear()
            sounds.clear()
            assert self._wait_until(app, lambda: "dsh" not in mgr._done_pending), \
                "busy→idle 排定的确认必须真的到期（否则下面的负向断言是空转）"
            self._pump(app, 0.3)                    # 确认窗余量
        finally:
            mgr.shutdown()
        assert bubbles, "确认到期必须走到收尾出口（非成功口径）"
        assert sounds.count("done.wav") == 0, sounds
        assert not any("已完成本轮任务" in b or "执行完成" in b or "已顺利完成" in b
                       for b in bubbles), bubbles

    # ------------------------------------------------------------------
    # 收尾后爆雷：success 之后 800ms 确认窗内到达的 error
    # ------------------------------------------------------------------
    #
    # ``turn/end`` 让收敛器产出 success 并排定完成确认；``AgentStatus(error)`` /
    # ``llm_error`` 在确认窗内到达时 ``prev_raw`` 已是终态（不在 ``_BUSY_STATES``）：
    # ① ``_on_agent_state`` 的既有 error 分支要求 ``prev_raw in _BUSY_STATES``，
    #    于是既不取消 pending 的确认、也不打 ``_saw_error``/``_saw_alert``；
    # ② ``llm_error`` 专属出口（``_on_llm_error``）同样不取消不打标；
    # 800ms 到期 ``_fire_done`` 照弹「已完成本轮任务」+ done 音效——错误提醒之后
    # 紧跟一条成功口径，用户看到的收尾是错的。
    #
    # 契约：error 落在完成确认窗内 = 「本回合已知不干净（收尾后爆雷）」——取消
    # 确认 + 按失败口径打标；``_fire_done`` 再加一道「到期时复读当前原始状态」的
    # 兜底。注意与 Claude 的 ``busy→attention→done`` 正常收尾路径无关：那条路径
    # 上 error 的来向是 busy（既有分支照旧打标并排定确认），attention 的排定逻辑
    # 一行不动。

    @staticmethod
    def _success_wording(bubbles: list) -> list:
        """气泡里属于「成功口径」的那些（done.success 预设的三句）。"""
        return [b for b in bubbles
                if any(k in b for k in ("已完成本轮任务", "执行完成", "已顺利完成"))]

    @staticmethod
    def _success_turn_records(session: str = "s-1") -> list[dict]:
        """正常回合的三条真实记录（turn/end 收敛 success）。"""
        return [
            {"event": "turn/start", "sessionId": session},
            {"event": "tool/call", "sessionId": session, "tool": "bash"},
            {"event": "turn/end", "sessionId": session},
        ]

    def test_error_inside_confirm_window_never_claims_success(self, tmp_path, monkeypatch):
        """⑪ AgentStatus(error) 落在确认窗内：取消确认，不弹成功口径、不播 done。"""
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._success_turn_records(),
                        {"event": "AgentStatus", "state": "error"})
            mon._poll()
            assert mon._converger.current_state is DshState.ERROR, mon._converger.current_state
            assert "dsh" not in mgr._done_pending, \
                "确认窗内到达的 error 必须取消 pending 的完成确认"
            assert "dsh" in mgr._saw_error and "dsh" in mgr._saw_alert, \
                "收尾后爆雷必须按失败口径打标（音效闸 + 文案口径）"
            assert bubbles, "错误提醒本身照常呈现（不得把提醒一起掐掉）"
            bubbles.clear()
            sounds.clear()
            self._pump(app, 1.2)                    # 超过 800ms 确认窗
        finally:
            mgr.shutdown()
        assert self._success_wording(bubbles) == [], bubbles
        assert "done.wav" not in sounds, sounds

    def test_llm_error_inside_confirm_window_never_claims_success(self, tmp_path, monkeypatch):
        """⑫ llm_error 落在确认窗内：取消确认，不弹成功口径、不播 done。

        llm_error 记录既不进 legacy 状态流（``normalize_event_state`` 不认这个名字、
        记录也没有 state 字段），收敛器产出的 ERROR 又不注入呈现管线
        （``_on_dsh_converged_state`` 只注入 thinking/success）——这条路径上唯一能
        拦住完成确认的就是 ``_on_llm_error`` 自己，⑪ 的状态闸在这里够不着。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._success_turn_records(),
                        {"event": "llm_error", "sessionId": "s-1",
                         "errorCode": "bad_response_status_code",
                         "errorMessage": "500 Internal Server Error",
                         "errorKind": "api"})       # 桥接 llm_error 的真实形状
            mon._poll()
            assert "dsh" not in mgr._done_pending, "llm_error 必须取消 pending 的完成确认"
            assert any("AI 服务" in b for b in bubbles), bubbles
            bubbles.clear()
            sounds.clear()
            self._pump(app, 1.2)
        finally:
            mgr.shutdown()
        assert self._success_wording(bubbles) == [], bubbles
        assert "done.wav" not in sounds, sounds

    def test_next_turn_after_post_completion_error_plays_done_again(self, tmp_path, monkeypatch):
        """⑬ 收尾后爆雷的下一个回合：标记随新回合清除，完成提醒恢复。

        既有清标规则是「``prev_raw != "error"`` 才清 ``_saw_error``」——那条规则
        为「error→working 的同周期重试」留标记（见 ⑨ 与音效族的 error 重试用例），
        而收尾后爆雷的 error 之前已是终态、后面的 busy 就是**新回合**：标记不清
        就会把下一回合的成功收尾整个吃掉（既没有成功气泡也没有失败口径）。

        时钟全程冻结（``clock=[1000.0]``），所以下一回合的完成气泡只有在
        「被取消的确认没占掉完成冷却（``_DONE_COOLDOWN_S``）」时才可能出现。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._success_turn_records(),
                        {"event": "AgentStatus", "state": "error"})
            mon._poll()
            assert "dsh" not in mgr._done_pending
            assert "dsh" in mgr._saw_error
            bubbles.clear()
            sounds.clear()
            self._pump(app, 1.2)                    # 上一回合的确认窗彻底走完
            assert self._success_wording(bubbles) == [], bubbles
            self._write(mon.events_dir, *self._success_turn_records())   # 下一个正常回合
            mon._poll()
            assert "dsh" not in mgr._saw_error, "新回合的忙入口必须清掉收尾后爆雷的标记"
            assert "dsh" not in mgr._saw_alert
            assert "dsh" in mgr._done_pending, "正常回合必须重新排定完成确认"
            assert self._wait_until(
                app, lambda: self._success_wording(bubbles)), bubbles
        finally:
            mgr.shutdown()
        assert sounds.count("done.wav") == 1, sounds

    def test_cancelled_confirm_does_not_consume_done_cooldown(self, tmp_path, monkeypatch):
        """⑭ 被取消的确认不占完成冷却（``_DONE_COOLDOWN_S`` 表不留痕）。

        依据 ``_fire_done``：``self._done_cooldown[agent_key] = now`` 写在
        「到期 → 回忙/隐藏/冷却三道闸全过 → 真的要弹泡」之后；被取消的确认根本
        到不了那行。时钟冻结时 ``now - _done_cooldown.get(agent_key, 0.0) <
        _DONE_COOLDOWN_S`` 恒真，所以「上一轮误写了冷却」的唯一表现就是下一轮的
        完成气泡永不出现——本用例把这一点钉死。
        """
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        mon = mgr.monitors["dsh"]
        try:
            mon._probe_online()
            self._write(mon.events_dir, *self._success_turn_records(),
                        {"event": "AgentStatus", "state": "error"})
            mon._poll()
            self._pump(app, 1.2)
            assert "dsh" not in mgr._done_cooldown, \
                "被取消的确认不得在冷却表里留下时刻（否则下一回合的完成气泡被吃掉）"
            assert self._success_wording(bubbles) == [], bubbles

            # 同一冻结时钟下的第一个干净回合：确认必须照常弹（冷却没被占）
            self._write(mon.events_dir, *self._success_turn_records())
            mon._poll()
            assert self._wait_until(app, lambda: self._success_wording(bubbles)), bubbles
            assert mgr._done_cooldown["dsh"] == clock[0], "真的弹过才写冷却时刻"

            # 第二个干净回合（时钟仍冻结 = 距上次弹泡 0s < 5s）：既有冷却语义生效
            n_success = len(self._success_wording(bubbles))
            self._write(mon.events_dir, *self._success_turn_records())
            mon._poll()
            assert self._wait_until(app, lambda: "dsh" not in mgr._done_pending)
            self._pump(app, 0.3)
            assert len(self._success_wording(bubbles)) == n_success, \
                "冷却窗内的第二次完成提醒必须被抑制（_DONE_COOLDOWN_S 既有语义）"
        finally:
            mgr.shutdown()
        # 被取消的那一轮一声不响（只有 error.wav）；其后两轮各播一条 done.wav——
        # 音效走 `_emit_sound` 自己的 sound_cooldown_seconds，`_DONE_COOLDOWN_S`
        # 只管气泡（既有两套冷却，本条断言只钉「取消的确认不得留下任何成功痕迹」）。
        assert sounds.count("done.wav") == 2, sounds

    def test_fire_done_refuses_success_wording_for_current_error_state(
            self, tmp_path, monkeypatch):
        """⑮ 防线：确认到期时该 agent 当前原始状态是 error → 不许成功口径。

        真实路径上 error 到达时确认已被 ⑪/⑫ 取消，所以这里**刻意构造**这道兜底
        要防的那一种状态：确认还挂着、``_last_raw`` 已是 error、而打标路径漏了
        （标记缺席）。到期出口必须自己再读一次当前状态——将来新增的 error 落点
        忘打标也不至于播出「已完成本轮任务」。
        """
        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)
        try:
            mgr._on_agent_state("dsh", "working")
            mgr._on_agent_state("dsh", "success")
            assert "dsh" in mgr._done_pending
            mgr._last_raw["dsh"] = "error"          # 漏标路径：状态已是 error
            mgr._saw_error.discard("dsh")
            mgr._saw_alert.discard("dsh")
            bubbles.clear()
            sounds.clear()
            mgr._fire_done("dsh")                   # 真实到期出口
        finally:
            mgr.shutdown()
        assert self._success_wording(bubbles) == [], bubbles
        assert "done.wav" not in sounds, sounds

    def test_fired_done_timer_is_recycled(self, tmp_path, monkeypatch):
        """⑯ 自然到期的确认定时器必须回收（每轮泄漏一个已停表 QObject）。

        ``_schedule_done_check`` 每轮新建 ``QTimer(self)``；取消路径
        （``_cancel_done_check``）有 stop + deleteLater，而自然到期路径
        （``_fire_done``）此前只从 ``_done_pending`` 里 pop——定时器对象留在
        manager 的子对象表里，长跑进程按回合数线性增长。
        """
        from PySide6.QtCore import QTimer

        mgr, bubbles, sounds, clock, app = self._make_mgr(tmp_path, monkeypatch)

        def run_round():
            clock[0] += 10.0                        # 越过冷却，每轮真的走到弹泡
            mgr._on_agent_state("dsh", "working")
            mgr._on_agent_state("dsh", "success")
            assert self._wait_until(app, lambda: "dsh" not in mgr._done_pending), \
                "确认必须真的自然到期（否则本条只能证明取消路径）"

        try:
            run_round()                             # 预热：懒建对象先建出来
            baseline = len(mgr.findChildren(QTimer))
            for _ in range(5):
                run_round()
            app.processEvents()                     # 让 deleteLater 落账
            assert len(mgr.findChildren(QTimer)) <= baseline, \
                f"自然到期路径每轮泄漏一个 QTimer：{baseline} → {len(mgr.findChildren(QTimer))}"
        finally:
            mgr.shutdown()


# ============================================================================
class TestUnifiedEventEnvelopeSchema:
    """_poll 统一事件路径必须按**信封**的 schema 解析，而不是展平后的嵌套值。

    背景：_poll 为了兼容旧消费者把 `data` 里的嵌套 dict 展平到顶层
    （`flattened.update(nested)`）。若语义层也吃这份展平结果，嵌套的
    `data.schema` 就会覆盖信封的 schema 版本号，造成两个方向的错判：
    - 信封 v1 + data.schema/v2（v2 文档字段）→ 白名单误拒，语义事件丢失；
    - 信封 v99 + data.schema/v1 → 绕过白名单，未知版本被当 v1 解析。
    展平口径本身要保留（raw_record 兼容消费者依赖它）。
    """

    @staticmethod
    def _monitor(tmp_path, got, raw):
        app = QApplication.instance() or QApplication([])  # noqa: F841
        cfg = Config(base=tmp_path)
        mon = BaseAgentMonitor("dsh", cfg.dir)
        mon.normalized_event.connect(got.append)
        mon.raw_record.connect(lambda key, rec: raw.append(rec))
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        mon._tailer.read_new_lines()  # backfill：先落到文件末尾
        return mon

    def _feed(self, mon, record):
        with mon.events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        mon._poll()

    @pytest.mark.parametrize("nested_schema", ["agent-event/v2", "agent-event/v99"])
    def test_v1_envelope_with_foreign_nested_schema_not_rejected(self, tmp_path, nested_schema):
        """信封 agent-event/v1 + 任意 data.schema 都不得被误拒。

        嵌套 data 是**载荷内容**（例如 v2 文档自带 schema 字段），它的值再陌生
        也不代表这条消息的版本——版本由信封决定。
        """
        got, raw = [], []
        mon = self._monitor(tmp_path, got, raw)
        self._feed(mon, {
            "schema": "agent-event/v1",
            "ts": 1, "agent": "dsh", "sessionId": "s1",
            "event": "llm/retry", "failure": {"code": "429"},
            "data": {"schema": nested_schema, "document": "payload"},
        })

        assert got, f"信封版本合法，语义事件不得因嵌套 data.schema={nested_schema} 丢失"
        assert type(got[0]).__name__ == "RetryEvent", got[0]

    def test_v99_envelope_with_v1_nested_not_bypassed(self, tmp_path):
        """信封 agent-event/v99 + data.schema=agent-event/v1 不得绕过白名单。"""
        got, raw = [], []
        mon = self._monitor(tmp_path, got, raw)
        self._feed(mon, {
            "schema": "agent-event/v99",
            "ts": 1, "agent": "dsh", "sessionId": "s1",
            "event": "llm/retry", "failure": {"code": "429"},
            "data": {"schema": "agent-event/v1"},
        })

        assert got == [], f"未知信封版本必须被语义层拒收，实得 {got}"
        assert raw, "拒收只影响语义层：原始记录仍须转发给兼容消费者"
        # legacy 展平口径逐位不变：信封字段可见，且嵌套 data.schema 在
        # **这一路**仍然覆盖（只给旧消费者的兼容视图，语义层不再受它影响）
        assert raw[-1]["failure"]["code"] == "429"
        assert raw[-1]["schema"] == "agent-event/v1"

    def test_legacy_flat_record_unaffected(self, tmp_path):
        """无嵌套 data 的常规记录（含无 schema 的老记录）语义路径逐位不变。"""
        got, raw = [], []
        mon = self._monitor(tmp_path, got, raw)
        self._feed(mon, {"ts": 1, "agent": "dsh", "sessionId": "s1",
                         "event": "llm/retry", "failure": {"code": "429"}})

        assert got and got[0].code == "429"


class TestDshDisableResetsPresentationState:
    """关闭 DSH 联动必须复位呈现侧锁存状态（6.1sol 终审 R3）。

    监视器停了之后，「待确认环 / 错误点 / 工作点」再没有人会来改——通道关闭
    语义 = 复用 offline 收敛管线一并收口（岛状态点复位 + 常驻交互气泡撤销）。
    """

    def test_disabling_dsh_emits_offline_through_the_converged_pipeline(
            self, tmp_path, monkeypatch):
        import threading

        from pet.agent_link import AgentLinkManager, DshMonitor

        app = QApplication.instance() or QApplication([])
        bubbles = []
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(
            DshMonitor, "install_bridge", classmethod(lambda cls: (True, "ok")))
        monkeypatch.setattr(
            DshMonitor, "uninstall_bridge", classmethod(lambda cls: True))
        try:
            states = []
            mgr.dsh_state_changed.connect(
                lambda from_s, to_s: states.append(to_s))
            # 直接开监视器（apply_config 是本修复的落点，不走 set_enabled 的安装链）
            cfg.set("agent_link", {"dsh": True})
            mgr.apply_config()
            app.processEvents()
            assert mgr.monitors["dsh"]._running
            # 联动在跑时的某个锁存态（待确认）——关闭后不允许残留
            mgr._on_dsh_converged_state("idle", "waiting_approval", "test")
            states.clear()
            cfg.set("agent_link", {"dsh": False})
            mgr.apply_config()
            app.processEvents()
            assert not mgr.monitors["dsh"]._running
            assert "offline" in states, (
                f"关闭联动必须经收敛管线发 offline（复位呈现侧锁存），实际 {states}")
            # 终态允许是 offline 或 idle：监视器收口线程会异步补一个 idle 收敛态，
            # 两者都能复位待确认/错误/工作的锁存（岛的 set_dsh_state 对二者同效）
            assert states[-1] in ("offline", "idle"), (
                f"关闭后的终态必须能复位锁存状态，实际序列 {states}")
            assert "waiting_approval" not in states[-2:], "锁存态不得是终态"
        finally:
            mgr.shutdown()


class TestDshLateQueuedSignalsAreIsolated:
    """astra 保底 B1：监视器停止后，已入队的旧收敛信号不得覆盖 offline 复位。"""

    def test_queued_stale_signal_after_disable_is_dropped(self, tmp_path, monkeypatch):
        from PySide6.QtCore import QTimer

        from pet.agent_link import AgentLinkManager, DshMonitor

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                pass

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        try:
            states = []
            mgr.dsh_state_changed.connect(lambda _f, to_s: states.append(to_s))
            cfg.set("agent_link", {"dsh": True})
            mgr.apply_config()
            app.processEvents()
            assert mgr.monitors["dsh"]._running

            # worker 已 emit waiting_approval（排在 Qt 队列里），GUI 还没泵到
            mon = mgr.monitors["dsh"]
            QTimer.singleShot(0, lambda: mon.dsh_state_changed.emit(
                "idle", "waiting_approval", "worker/late", mon._emit_gen))
            # 用户此刻关闭联动：stop + 同步 offline 复位（先入队）
            cfg.set("agent_link", {"dsh": False})
            mgr.apply_config()
            app.processEvents()   # 泵队列：旧信号到达——必须被闸门丢弃
            app.processEvents()
            assert states[-1] == "offline", (
                f"迟到的旧信号覆盖了 offline 复位：{states}")
            assert "waiting_approval" not in states[states.index("offline"):]
        finally:
            mgr.shutdown()

    def test_offline_from_a_running_monitor_still_passes(self, tmp_path, monkeypatch):
        """闸门不误伤：监视器在跑时的真 offline（DSH 断线）照常收敛。"""
        from pet.agent_link import AgentLinkManager

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                pass

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        try:
            states = []
            mgr.dsh_state_changed.connect(lambda _f, to_s: states.append(to_s))
            cfg.set("agent_link", {"dsh": True})
            mgr.apply_config()
            app.processEvents()
            # 监视器自己的探活 offline（测试环境没有 DSH 在听端口）在慢 runner 上
            # 可能已经被 processEvents 送达——清掉再测直连路径，与竞态解耦
            states.clear()
            mgr._on_dsh_converged_state("working", "offline", "probe/lost")
            assert states == ["offline"]
        finally:
            mgr.shutdown()

    # ------------------------------------------------------- F3：信号代次加固
    @staticmethod
    def _linked_manager(tmp_path):
        """真实监视器在跑的管理器（代次语义只有真启停才测得出来）。"""
        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, text, duration_ms=3000):
                pass

            def isVisible(self):
                return True

        mgr = AgentLinkManager(Win(), cfg)
        cfg.set("agent_link", {"dsh": True})
        mgr.apply_config()
        app.processEvents()
        assert mgr.monitors["dsh"]._running
        return app, cfg, mgr

    @staticmethod
    def _emit_queued(signal, args, timeout: float = 5.0) -> None:
        """在 worker 线程里发一次收敛信号（生产口径：threading.Thread + 队列投递）。

        只保证「emit 已返回」——主线程在此期间不泵事件（Event 同步，不靠 sleep
        猜时序），信号因此确定停在 Qt 队列里。
        """
        emitted = threading.Event()

        def run():
            signal.emit(*args)
            emitted.set()

        worker = threading.Thread(target=run, daemon=True, name="test-queued-emit")
        worker.start()
        assert emitted.wait(timeout), "worker 未能发出收敛信号"
        worker.join(timeout)

    def test_state_signal_of_the_previous_generation_is_dropped_after_restart(
            self, tmp_path):
        """F3：停 → 启之间同栈排队的旧代次状态信号必须被丢弃。

        旧口径只查 ``monitor._running``——重开联动后新代次已经在跑，上一代次排在
        Qt 队列里的 waiting_approval/error 照常过闸，把新代锁回陈旧状态。
        """
        app, cfg, mgr = self._linked_manager(tmp_path)
        try:
            mon = mgr.monitors["dsh"]
            states = []
            mgr.dsh_state_changed.connect(lambda _f, to_s: states.append(to_s))
            gen_old = mon._emit_gen
            self._emit_queued(mon.dsh_state_changed,
                              ("idle", "waiting_approval", "worker/late", gen_old))
            # 队列里已有旧代次信号（主线程没泵过事件）：关联动停监视器，再打开
            cfg.set("agent_link", {"dsh": False})
            mgr.apply_config()
            cfg.set("agent_link", {"dsh": True})
            mgr.apply_config()
            app.processEvents()
            assert mon._running and mon._emit_gen != gen_old, "重开必须是新代次"
            assert "waiting_approval" not in states, (
                f"上一代次的排队信号过闸了：{states}")
            # 闸门不误伤：当前代次的信号照常到达
            self._emit_queued(mon.dsh_state_changed,
                              ("idle", "working", "tool/call", mon._emit_gen))
            app.processEvents()
            assert "working" in states, f"当前代次信号被代次闸误伤：{states}"
        finally:
            mgr.shutdown()

    def test_user_message_of_the_previous_generation_is_dropped_after_restart(
            self, tmp_path, monkeypatch):
        """F3：停 → 启之间同栈排队的旧代次真人消息必须被丢弃。"""
        app, cfg, mgr = self._linked_manager(tmp_path)
        try:
            mon = mgr.monitors["dsh"]
            forwarded = []
            notified = []
            mgr.dsh_user_message.connect(lambda sid, text: forwarded.append((sid, text)))
            monkeypatch.setattr(mgr, "notify_dsh_state",
                                lambda state: notified.append(state))
            gen_old = mon._emit_gen
            self._emit_queued(mon.dsh_user_message, ("s-old", "", gen_old))
            cfg.set("agent_link", {"dsh": False})
            mgr.apply_config()
            cfg.set("agent_link", {"dsh": True})
            mgr.apply_config()
            app.processEvents()
            assert mon._running and mon._emit_gen != gen_old, "重开必须是新代次"
            assert forwarded == [], f"上一代次的 user_message 过闸了：{forwarded}"
            assert notified == [], "旧代次消息不得注入思考态"
            # 闸门不误伤：当前代次的真人消息照常外转 + 注入思考态
            self._emit_queued(mon.dsh_user_message, ("s-new", "", mon._emit_gen))
            app.processEvents()
            assert forwarded == [("s-new", "")], forwarded
            assert notified == ["thinking"], notified
        finally:
            mgr.shutdown()

    def test_queued_user_message_after_disable_is_not_forwarded(self, tmp_path, monkeypatch):
        """F3：停机后未重开时，迟到的 user_message 也不得外转。"""
        app, cfg, mgr = self._linked_manager(tmp_path)
        try:
            mon = mgr.monitors["dsh"]
            forwarded = []
            notified = []
            mgr.dsh_user_message.connect(lambda sid, text: forwarded.append((sid, text)))
            monkeypatch.setattr(mgr, "notify_dsh_state",
                                lambda state: notified.append(state))
            self._emit_queued(mon.dsh_user_message, ("s1", "", mon._emit_gen))
            cfg.set("agent_link", {"dsh": False})
            mgr.apply_config()
            app.processEvents()
            app.processEvents()
            assert not mon._running
            assert forwarded == [], f"停机后 user_message 仍外转：{forwarded}"
            assert notified == [], "停机后不得注入思考态"
        finally:
            mgr.shutdown()


class _BridgeWin:
    """真实桥装配用的假窗口：记录气泡/提醒文案，并统计交互关闭次数。"""

    def __init__(self) -> None:
        self.bubbles: list[str] = []
        self.alerts: list[str] = []
        self.resolved: list[str] = []
        self.hidden = 0
        self._bubble_busy_until = 0.0
        self._sticky_bubble_active = False
        self._alert_current = None
        self._alert_queue: list = []

    def isVisible(self):
        return True

    def show_bubble(self, text, duration_ms=3000, sticky=False, buttons=None):
        self.bubbles.append(str(text))

    def show_alert(self, text, **kwargs):
        self.alerts.append(str(text))

    def resolve_alert(self, alert_id):
        self.resolved.append(str(alert_id))

    def hide_bubble(self):
        self.hidden += 1


class _BridgeHarness:
    """真实装配：真实 Config + 真实 AgentLinkManager + 真实桥记录通道。

    ``DshMonitor`` 的目录尾读换成 ``ByteOffsetTailer``（读写一个受控文件），
    ``_poll`` 与信号分发保持产品原样——用例断言的是真链路，不是手工接线。

    中性化的只有两条不能确定性运行的边界，且都与被断言的行为无关：
    ``_probe_online``（socket 探活 + 进程枚举）置为 no-op；``_online = False``
    让状态收敛器不消费事件（状态收敛另有专测 tests/test_dsh_state.py）。
    """

    def __init__(self, tmp_path, *, phrases: dict | None = None) -> None:
        QApplication.instance() or QApplication([])
        self.win = _BridgeWin()
        cfg = Config(base=tmp_path)
        if phrases is not None:
            cfg.data["dialogue_mode"] = "custom"
            cfg.data["dialogue_phrases"] = phrases
            cfg.save()
        self.mgr = AgentLinkManager(self.win, cfg)
        self.bridge = tmp_path / "dsh.jsonl"
        self.bridge.touch()
        self.mon = self.mgr.monitors["dsh"]
        self.mon._tailer = ByteOffsetTailer(self.bridge)
        self.mon._probe_online = lambda: None
        self.mon._online = False
        self.mon._poll()  # 尾读初始化（backfill：不重放历史）

    def feed(self, *records: dict) -> None:
        """按产品写盘形状追加桥接记录，并跑一拍 ``_poll``（解析 + 信号分发）。"""
        with open(self.bridge, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        self.mon._poll()

    def close(self) -> None:
        self.mgr.shutdown()


class TestAgentScopedPhraseRouting:
    """Agent 专属文案必须贯穿全部事件路径。

    回归背景：``_dialogue(..., agent_key="")`` 的默认空路由只读预设的 global
    层，而审批 / 用户问题 / 模型访问失败 / API 错误 / 硬失败 / 未检测到 Agent
    六条路径都不传 agent_key——用户在设置里给 DSH 填了专属文案，这六条事件
    运行时仍出全局文案（start / thinking / 活动 / 完成四条路径已有显式路由，
    是非回归基线）。

    文案双层都填满且**逐事件不同**：global 层全为 ``GLOBAL-X:<事件>``，
    ``agents.dsh`` 全为 ``DSH-X:<事件>``——于是每个气泡文案本身就是「这条路径
    路由到了哪一层」的证据（既验层级，又能逐条归因）。
    """

    @staticmethod
    def _phrases() -> dict:
        keys = phrase_keys()
        return {
            "global": {key: [f"GLOBAL-X:{key}"] for key in keys},
            "agents": {
                "dsh": {key: [f"DSH-X:{key}"] for key in keys},
                "opencode": {key: [f"OPENCODE-X:{key}"] for key in keys},
            },
        }

    def test_review_question_model_access_and_failure_paths_use_agent_phrases(self, tmp_path):
        """审批 / 问题 / 模型访问失败 / API 错误 / 硬失败五条桥接路径走专属文案。"""
        harness = _BridgeHarness(tmp_path, phrases=self._phrases())
        try:
            harness.feed(
                {"event": "approval/request", "ts": 1, "toolName": "bash",
                 "rpcId": "r-approval", "sessionId": "s-1"},
                # 审批的另两个分支：词表外的工具名（label 回退原文）与无工具名
                {"event": "approval/request", "ts": 2, "toolName": "brand_new_tool",
                 "rpcId": "r-approval-2", "sessionId": "s-1"},
                {"event": "approval/request", "ts": 3, "rpcId": "r-approval-3",
                 "sessionId": "s-1"},
                {"event": "question/requested", "ts": 4, "rpcId": "r-question",
                 "sessionId": "s-1", "questions": [{"question": "选哪个", "header": "H"}]},
                {"event": "question/requested", "ts": 5, "rpcId": "r-question-2",
                 "sessionId": "s-1",
                 "questions": [{"question": "先做哪个", "options": [{"label": "A"}]},
                               {"question": "再做什么", "options": [{"label": "B"}]}]},
                {"event": "question/requested", "ts": 6, "rpcId": "r-question-3",
                 "sessionId": "s-1", "questions": []},
                {"event": "model_access", "ts": 7, "errorCode": "429", "sessionId": "s-9"},
                {"event": "llm_error", "ts": 8, "errorCode": "bad_response_status_code",
                 "errorKind": "api", "sessionId": "s-9"},
                {"event": "execution/failed", "ts": 9, "failureType": "tool_failed"},
                {"event": "execution/failed", "ts": 10, "retryExhausted": True},
                {"event": "execution/failed", "ts": 11},
            )
        finally:
            harness.close()

        alerts = harness.win.alerts
        for key in ("approval.tool", "approval.generic", "question.one",
                    "question.many", "question.empty",
                    "model_access.one", "llm_error.api",
                    "failure.tool", "failure.retry", "failure.generic"):
            assert any(text.startswith(f"DSH-X:{key}") for text in alerts), \
                f"{key} 未走 DSH 专属文案，实际气泡：{alerts}"
        assert not [text for text in alerts if text.startswith("GLOBAL-X:")], \
            f"有路径仍在读全局层：{alerts}"

    def test_start_path_still_uses_agent_phrases(self, tmp_path):
        """非回归基线：已显式路由的 start 路径继续出专属文案。"""
        harness = _BridgeHarness(tmp_path, phrases=self._phrases())
        try:
            harness.feed({"event": "agent/status", "state": "working", "ts": 1})
        finally:
            harness.close()
        assert any(text.startswith("DSH-X:start") for text in harness.win.bubbles), \
            harness.win.bubbles

    def test_agent_missing_warning_uses_agent_phrases(self, tmp_path, monkeypatch):
        """「没检测到本机安装」提醒同走该 Agent 的专属文案。

        ``_warn_if_agent_absent`` 的 marker 表只列 cursor / opencode（DSH 的
        marker 是端口探活，不走这条），故用 opencode 触发；``Path.home`` 是
        文件系统边界，钉到临时目录保证 marker 不存在（否则断言取决于本机装没装）。
        """
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        harness = _BridgeHarness(tmp_path, phrases=self._phrases())
        try:
            harness.mgr._warn_if_agent_absent("opencode")
        finally:
            harness.close()
        assert any(text.startswith("OPENCODE-X:agent.missing")
                   for text in harness.win.bubbles), harness.win.bubbles

    def test_public_events_stay_on_the_global_layer(self, tmp_path):
        """非回归：公共事件（桥接安装等）不随 Agent 归属，保持空路由读 global。"""
        harness = _BridgeHarness(tmp_path, phrases=self._phrases())
        try:
            # 真实公共事件路径：安装失败提醒（成功分支会真起监视器，不在此触发）
            harness.mgr._on_install_finished("dsh", False, "boom", None)
            for key in sorted(PUBLIC_DIALOGUE_EVENTS):
                assert harness.mgr._dialogue(key, "fb") == f"GLOBAL-X:{key}", \
                    f"公共事件 {key} 被 Agent 路由污染"
        finally:
            harness.close()
        assert any(text.startswith("GLOBAL-X:bridge.install.failed")
                   for text in harness.win.bubbles), harness.win.bubbles


class TestBridgeResolutionFrames:
    """收尾帧的两条路分工：raw 旁路按事件名、语义层按身份——都不许失效、不许重复收尾。

    旁路（``BaseAgentMonitor._poll`` 的直通名单）拿的是**原始**记录，身份字段不过
    预算修剪；语义层（``normalize_event`` → ``_on_normalized_event``）拿的是
    bounded 后的 ``AgentEvent``，顶层 callId 被搬进信封字段。两条路并存：

    - ``approval/decided`` / ``approval/resolved`` / ``question/resolved`` 由旁路
      按身份（或旧路径无身份兜底）收尾；
    - ``interaction/resolved`` 没有旁路，只有语义层能收——它的 callId 只写在顶层，
      身份回退失效就等于这条收尾帧永远配不上（气泡永久挂着）。
    """

    def test_interaction_resolved_call_id_frame_closes_pending_approval(self, tmp_path):
        """桥接真实形状的 interaction/resolved（身份只在顶层）必须关掉对应审批。"""
        harness = _BridgeHarness(tmp_path)
        try:
            harness.feed({"event": "approval/request", "ts": 1, "toolName": "bash",
                          "rpcId": "rpc-a", "callId": "call-a", "sessionId": "s-1"})
            assert harness.mgr.pending_interactions_for("dsh"), "前置：审批必须已登记"

            harness.feed({"event": "interaction/resolved", "ts": 2, "source": "dsh",
                          "kind": "approval", "callId": "call-a", "sessionId": "s-1",
                          "outcome": "approved"})
        finally:
            harness.close()

        assert harness.mgr.pending_interactions_for("dsh") == {}, \
            "顶层 callId 未回退 → 语义层配不上身份，审批气泡永久挂着"

    def test_raw_bypass_still_closes_identityless_decided(self, tmp_path):
        """旁路收尾不变：无身份的旧路径 approval/decided 仍关闭纯提示审批。"""
        harness = _BridgeHarness(tmp_path)
        try:
            # 无 rpcId 的纯提示审批（旁路只能靠「无 id 兜底」关闭）
            harness.feed({"event": "approval/request", "ts": 1, "toolName": "bash",
                          "approvalId": "ap-hint", "sessionId": "s-1"})
            assert harness.mgr.pending_interactions_for("dsh")
            harness.feed({"event": "approval/decided", "ts": 2, "sessionId": "s-1"})
        finally:
            harness.close()

        assert harness.mgr.pending_interactions_for("dsh") == {}, \
            "approval/decided 的旁路兜底收尾被破坏"
        assert harness.win.resolved, "关闭必须经 resolve_alert 精确关闭那一条气泡"


class TestMultiDshInstanceIsolation:
    """两个 DSH 实例（两个桥写者）同时联动时的串扰回归（2026-10，实机复现）。

    桥目录里可以同时有多个 ``dsh-{pid}.jsonl``（每个 DSH 实例一份），桌宠 glob 全部
    读入。修复前收敛器只有**一份**状态与锁存、呈现侧也只有一份原始状态——
    A 实例回待机（``AgentStatus idle``）会：
      ① 撤掉 B 实例还在等的问题/审批气泡（typed state 与 lifecycle record 两条清
         路径都会清）；
      ② 把 B 实例的 working 顶成待机（``_last_raw`` 被覆盖 → busy→idle 边沿排定
         完成确认，弹假完成）；
      ③ A 的 ``question/resolved`` 解开 B 的问题锁存。
    这些用例走真链路：真 ``DirGlobTailer``（多文件）+ 真 ``DshMonitor._poll`` + 真
    收敛器 + 真 ``AgentLinkManager`` + 真 800ms 确认窗；只有窗口与音效是桩。

    唯一被中性化的是回填防护（``_initial_backfill_done = True``）：本类的被测对象是
    写者隔离，不是回填（回填契约由 tests/test_dsh_mid_attach_done.py 专门守），不置真
    就要给每个新文件先写一遍"历史"再写"live"，把用例变成回填时序测试。
    """

    _SOUND_PATHS = {
        "builtin:agent-start": "start.wav",
        "builtin:agent-done": "done.wav",
        "builtin:agent-error": "error.wav",
    }

    def _make_rig(self, tmp_path, monkeypatch):
        """真 manager（真 DshMonitor，worker 不启动、手动驱动 _poll）+ 假窗口 + spy。"""
        app = QApplication.instance() or QApplication([])
        bubbles: list[str] = []
        alerts: list[tuple[str, str]] = []
        resolved: list[str] = []
        sounds: list[str] = []

        files = {}
        for value, name in self._SOUND_PATHS.items():
            path = tmp_path / name
            path.write_bytes(b"RIFF")
            files[value] = path
        # 音效钉在播放漏斗层（winmm 直放 / Qt 后端都逃不掉）
        monkeypatch.setattr(agent_link, "resolve_builtin_sound", lambda p: files.get(str(p)))
        for fn in ("play_sound", "play_with_winmm", "play_with_qt"):
            monkeypatch.setattr(click_sound._pool, fn,
                                lambda path, volume=1.0: sounds.append(Path(path).name) or True)
        monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
        monkeypatch.setattr(harness_launcher, "desktop_process_running", lambda: True)

        class DummyWin:
            cats = {"acts": ["写代码", "吃Token"]}
            idles = ["待机呼吸"]
            _bubble_busy_until = 0.0
            _alert_current = None
            _alert_queue: list = []
            _sticky_bubble_active = False

            def __init__(self):
                self.switched: list[str] = []

            def isVisible(self):
                return True

            def mark_activity(self):
                pass

            def _switch(self, name):
                self.switched.append(str(name))

            def request_link_anim(self, name):
                self.switched.append("anim:" + str(name))

            def request_link_idle(self):
                self.switched.append("idle")

            def show_bubble(self, text, duration_ms=3000, **_kw):
                bubbles.append(str(text))

            def show_alert(self, text, *, alert_id="", **_kw):
                alerts.append((str(alert_id), str(text)))
                return str(alert_id)

            def resolve_alert(self, alert_id):
                resolved.append(str(alert_id))

            def hide_bubble(self):
                pass

            def _pick(self, lst):
                return lst[0]

        cfg = Config(base=tmp_path / "cfg")
        cfg.data["agent_link"].update({
            "sound_enabled": True,
            "sound_cooldown_seconds": 0.0,
            "sound_start_path": "builtin:agent-start",
            "sound_done_path": "builtin:agent-done",
            "sound_error_path": "builtin:agent-error",
        })
        cfg.save()
        clock = [1000.0]
        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=0.0, clock=lambda: clock[0])
        mon = mgr.monitors["dsh"]
        # 白盒：等效 agent_link.dsh 已启用——迟到信号隔离闸只放行「监视器在跑」的信号
        mon._running = True
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        # 扫描节流关掉（scan_interval=0 是产品合法取值）：本类要的是"新文件当拍摄入"，
        # 不该赌目录 mtime 的更新时序（Windows 上惰性更新，g2 用例已有实测记录）。
        mon._tailer.scan_interval = 0.0
        mon._tailer._initial_backfill_done = True
        return mgr, mon, bubbles, alerts, resolved, sounds, clock, app

    @staticmethod
    def _write(bridge: Path, name: str, *records) -> None:
        with (bridge / name).open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _pump(app, seconds: float) -> None:
        """跑满一段事件循环（负向断言专用：给不该存在的定时器犯错的机会）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    @staticmethod
    def _wait_until(app, predicate, timeout: float = 5.0) -> bool:
        """宽预算轮询等待（禁止固定 sleep 猜时序：800ms 确认窗在慢 runner 上会漂）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        app.processEvents()
        return bool(predicate())

    @staticmethod
    def _succeeded(bubbles) -> list:
        return [b for b in bubbles if "已完成本轮任务" in b or "执行完成" in b or "已顺利完成" in b]

    def test_other_writer_idle_keeps_pending_question(self, tmp_path, monkeypatch):
        """A 实例回待机不得撤掉 B 实例还在等的问题（typed state 与 lifecycle 两条清路径）。"""
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "question/requested", "sessionId": "sB", "callId": "qB",
                         "inst": "bootB", "questions": [{"id": "q1", "question": "选哪个？"}]})
            mon._poll()
            pending = mgr.pending_interactions_for("dsh")
            assert len(pending) == 1, pending

            # A 实例回待机：typed state（AgentStatus idle）与 lifecycle record 是两条
            # 清路径，同一条记录会同时经过它们，必须都按写者过滤。
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootA"})
            mon._poll()

            assert mgr.pending_interactions_for("dsh"), "另一个实例回待机不得撤掉 B 还在等的问题"
            assert resolved == [], "不得关闭 B 的常驻气泡"
            assert next(iter(pending.values()))["owners"] == {"inst:bootB"}, "交互归属必须是 B"
        finally:
            mgr.shutdown()

    def test_double_written_question_keeps_one_body_and_both_writers(self, tmp_path, monkeypatch):
        """同一问题被两个写者各写一份：正文一次、归属两个（去重不去归属）。

        去重契约不变（正文/提示音只响一次），但第二个写者也在等这条交互——归属集合
        记全了，先回待机的那个写者才撤不掉用户还在等的气泡。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        record = {"event": "question/requested", "sessionId": "sX", "callId": "qX", "inst": "bootA",
                  "questions": [{"id": "q1", "question": "同一个问题"}]}
        try:
            self._write(bridge, "dsh-11.jsonl", record)
            self._write(bridge, "dsh-22.jsonl", {**record, "inst": "bootB"})
            mon._poll()

            pending = mgr.pending_interactions_for("dsh")
            assert len(pending) == 1, f"同一逻辑事件只允许一份 pending：{pending}"
            assert len(alerts) == 1, f"正文只落一次：{alerts}"
            assert next(iter(pending.values()))["owners"] == {"inst:bootA", "inst:bootB"}, \
                "两个写者都写过这条交互，归属必须记全（去重不去归属）"

            # A 回待机：它自己那份归属被撤（旧口径：实例回待机 = 它等的交互已失效），
            # 但 B 仍归它 -> 用户还在等的气泡必须保留（这正是归属集合拦下的串扰）
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootA"})
            mon._poll()
            left = mgr.pending_interactions_for("dsh")
            assert left, "A 回待机撤掉了 B 还在等的交互"
            assert next(iter(left.values()))["owners"] == {"inst:bootB"}, "只剩 B 的归属"
            assert resolved == []

            # B 自己的 resolved 才收尾
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "question/resolved", "sessionId": "sX", "callId": "qX",
                         "inst": "bootB"})
            mon._poll()
            assert mgr.pending_interactions_for("dsh") == {}
            assert resolved, "B 的收尾必须把气泡关上"
        finally:
            mgr.shutdown()

    def test_busy_writer_not_overridden_by_other_writer_idle(self, tmp_path, monkeypatch):
        """A 回待机不得把 B 的忙判成待机（否则弹假完成 + 动画切回待机）。"""
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            assert mgr._last_raw["dsh"] == "working"

            bubbles.clear()
            sounds.clear()
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootA"})
            mon._poll()
            assert mgr._last_raw["dsh"] == "working", "A 回待机把整体判成了待机"
            assert "dsh" not in mgr._done_pending, "还有实例在忙，不得排定完成确认"
            self._pump(app, 1.2)
            assert self._succeeded(bubbles) == [], f"不得弹假完成：{bubbles}"
            assert sounds == [], f"不得播完成音效：{sounds}"

            # 两个实例都回待机：这时才是真实完成，正常排定确认
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootB"})
            mon._poll()
            assert mgr._last_raw["dsh"] == "idle"
            assert "dsh" in mgr._done_pending, "全部实例回待机后必须照常排定完成确认"
        finally:
            mgr.shutdown()

    def test_dead_writer_withdraws_only_its_pending_and_stays_silent(self, tmp_path, monkeypatch):
        """写者被确认死亡：只撤它自己的常驻交互，且**不报完成**。

        「写者已死」的确认口径沿用桥目录清理的保守三连（超龄 + 文件名带 pid + 探活
        明确为假）；确认后它的状态与交互归属一起退役，但消失不是回合收尾——不得弹
        「已完成本轮任务」，也不能碰到另一个实例还在等的交互。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        monkeypatch.setattr(agent_link, "_bridge_writer_alive",
                            lambda pid: pid != 101)   # 只有 dsh-101 的写者已死
        try:
            self._write(bridge, "dsh-101.jsonl",
                        {"event": "question/requested", "sessionId": "sA", "callId": "qA",
                         "inst": "bootA", "questions": [{"id": "q1", "question": "A 的问题"}]},
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._write(bridge, "dsh-202.jsonl",
                        {"event": "question/requested", "sessionId": "sB", "callId": "qB",
                         "inst": "bootB", "questions": [{"id": "q1", "question": "B 的问题"}]},
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            assert len(mgr.pending_interactions_for("dsh")) == 2

            # 让 dsh-101 超龄（写者探活已判死）→ 下一拍扫描确认死亡并清理该文件
            old = time.time() - 48 * 3600
            os.utime(bridge / "dsh-101.jsonl", (old, old))
            bubbles.clear()
            sounds.clear()
            mon._poll()

            left = mgr.pending_interactions_for("dsh")
            assert len(left) == 1, f"只该撤死写者自己的那条：{left}"
            assert next(iter(left.values()))["owners"] == {"inst:bootB"}
            self._pump(app, 1.2)
            assert self._succeeded(bubbles) == [], f"写者死亡不得报完成：{bubbles}"
            assert sounds == [], f"写者死亡不得播完成音效：{sounds}"
            assert "dsh" not in mgr._done_pending

            # B（活写者）的收尾照常有效
            self._write(bridge, "dsh-202.jsonl",
                        {"event": "question/resolved", "sessionId": "sB", "callId": "qB",
                         "inst": "bootB"})
            mon._poll()
            assert mgr.pending_interactions_for("dsh") == {}
        finally:
            mgr.shutdown()

    def test_writer_identity_boot_uuid_beats_pid_reuse(self, tmp_path, monkeypatch):
        """写者身份 = 插件 boot UUID（旧桥退化为「路径 + 文件身份」），**不是 pid**。

        同一 pid 重启后复用同一个 ``dsh-{pid}.jsonl`` 是两个写者：新实例回待机不得
        撤销前一个实例还在等的交互。旧桥没有 boot UUID 时用摄入游标里的文件身份，
        文件被轮转/替换（同路径、新身份）同样算新写者。
        """
        # 端到端：同一个文件里先后出现两个 boot 身份（同 pid 重启复用文件）
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-303.jsonl",
                        {"event": "question/requested", "sessionId": "sN", "callId": "qN",
                         "inst": "boot-1", "questions": [{"id": "q1", "question": "旧实例的问题"}]})
            mon._poll()
            assert len(mgr.pending_interactions_for("dsh")) == 1

            # 新实例（同 pid / 同文件、新 boot）回待机：不得撤掉旧实例的交互
            self._write(bridge, "dsh-303.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "boot-2"})
            mon._poll()
            assert mgr.pending_interactions_for("dsh"), "新 boot 身份回待机撤掉了旧实例的交互"
            assert resolved == []
        finally:
            mgr.shutdown()

        # 纯推导：inst 优先；无 inst 用（路径, 文件身份）；两者都没有 → 不知道归属
        same_file = ("p/dsh-101.jsonl", (11, 22), 0)
        assert agent_link.record_owner({"inst": "boot-1"}, same_file) == "inst:boot-1"
        assert agent_link.record_owner({"inst": "boot-2"}, same_file) == "inst:boot-2"
        assert agent_link.record_owner({}, same_file) == agent_link.record_owner(
            {}, ("p/dsh-101.jsonl", (11, 22), 512)), "同一文件的后续行仍是同一写者"
        assert agent_link.record_owner({}, same_file) != agent_link.record_owner(
            {}, ("p/dsh-101.jsonl", (33, 44), 0)), "同路径新文件身份 = 新写者"
        assert agent_link.record_owner({}, None) is None, "推不出归属时不得退化成 pid 猜测"

    # ------------------------------------------------------------------
    # 写者存活回收（活跃状态）
    # ------------------------------------------------------------------
    def _poll_until(self, mon, predicate, polls: int) -> bool:
        """最多再跑 polls 拍，返回谓词是否成立（低频探活的真实节拍驱动）。"""
        for _ in range(polls):
            mon._poll()
            if predicate():
                return True
        return bool(predicate())

    def test_live_writer_probe_retires_only_confirmed_dead(self, tmp_path, monkeypatch):
        """活跃写者的存活回收：只有明确已死才撤；判定失败/存活一律保留（无静默 TTL）。

        旧行为下这条路径根本不存在（只有 24h 陈旧文件清理），写者进程死掉后状态会
        永远停在"忙"——所以这里全程按真实轮询节拍驱动，不依赖新内部状态来制造红。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        polls = int(getattr(mon, "_DEAD_WRITER_PROBE_EVERY_N_POLLS", 40)) + 3
        try:
            self._write(bridge, "dsh-101.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            mon._poll()
            assert mgr._last_raw.get("dsh") == "working"

            # 1) 探活说活着：跑满探活节拍也不撤（文件静默不构成回收理由）
            monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: True)
            assert not self._poll_until(mon, lambda: mgr._last_raw.get("dsh") is None, polls)
            assert mgr._last_raw.get("dsh") == "working"

            # 2) 探活判定失败（None）：保守保留（平台不支持 pid 探活也不许误撤）
            monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: None)
            assert not self._poll_until(mon, lambda: mgr._last_raw.get("dsh") is None, polls)
            assert mgr._last_raw.get("dsh") == "working", "判定失败必须保守保留"

            # 3) 明确已死（只认 False）：撤掉忙态，且不报完成
            bubbles.clear()
            sounds.clear()
            monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: False)
            assert self._poll_until(mon, lambda: mgr._last_raw.get("dsh") is None, polls), \
                "写者已确认死亡：忙态必须被回收（否则桌宠永远忙）"
            assert mgr.any_busy() is False
            self._pump(app, 1.2)
            assert self._succeeded(bubbles) == [], f"写者消失不是回合收尾：{bubbles}"
            assert sounds == []
            assert not [k for k in mgr._owner_raw if k[0] == "dsh"]
            assert "inst:bootA" not in mon._converger._scope_keys
        finally:
            mgr.shutdown()

    @staticmethod
    def _refuse_probe(monkeypatch):
        """把两个平台的探活边界都注入成"权限拒绝"（真分派走哪个分支都已注入）。"""
        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(PermissionError(errno.EPERM, "Operation not permitted")))
        monkeypatch.setattr(agent_link, "_win32_kernel32",
                            lambda: _FakeKernel32(open_result=0, last_error=5))

    @staticmethod
    def _confirm_dead_probe(monkeypatch):
        """把两个平台的探活边界都注入成"没有这个 pid"（确定已死）。"""
        monkeypatch.setattr(agent_link, "_os_kill_signal_0",
                            _raiser(ProcessLookupError(errno.ESRCH, "No such process")))
        monkeypatch.setattr(agent_link, "_win32_kernel32",
                            lambda: _FakeKernel32(open_result=0, last_error=87))

    def test_permission_denied_probe_keeps_busy_and_pending_writer(self, tmp_path, monkeypatch):
        """权限拒绝的探活（Win err5 / POSIX EPERM）不得退役活写者：忙态与交互都要留。

        探不动的目标是**活着**的（受保护进程 / 另一个用户 / 更高完整性级别）：把它当死
        会撤掉用户还在等的审批/问题气泡，还会让桌宠以为这一轮结束了。这里的"确定已死"
        反证同时证明保留不是"探活压根没跑"。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        polls = int(getattr(mon, "_DEAD_WRITER_PROBE_EVERY_N_POLLS", 40)) + 3
        try:
            self._write(bridge, "dsh-101.jsonl",
                        {"event": "question/requested", "sessionId": "sA", "callId": "qA",
                         "inst": "bootA", "questions": [{"id": "q1", "question": "A 的问题"}]},
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._refuse_probe(monkeypatch)
            mon._poll()
            assert mgr._last_raw.get("dsh") == "working", mgr._last_raw
            assert len(mgr.pending_interactions_for("dsh")) == 1

            assert not self._poll_until(mon, lambda: mgr.pending_interactions_for("dsh") == {}, polls), \
                "权限拒绝的探活不得退役写者：判不了就必须保守保留"
            assert mgr.pending_interactions_for("dsh"), "用户还在等的交互不许被权限拒绝的探活撤掉"
            assert mgr._last_raw.get("dsh") == "working" and mgr.any_busy() is True, \
                "等待中的写者在呈现侧就是「忙」：不许被权限拒绝的探活撤成待机"
            assert resolved == []

            # 反证：同一个写者的边界换成"确定已死"→ 忙/等待态与交互一起退役
            self._confirm_dead_probe(monkeypatch)
            assert self._poll_until(mon, lambda: mgr.pending_interactions_for("dsh") == {}, polls), \
                "确定已死的写者必须被回收（否则桌宠永远在等）"
            assert mgr._last_raw.get("dsh") is None
            assert "inst:bootA" not in mon._converger._scope_keys
        finally:
            mgr.shutdown()

    def test_owner_file_map_keeps_busy_writers_when_over_capacity(self, tmp_path, monkeypatch):
        """映射表满时不许淘汰还在忙的写者：否则它的进程死了也探不到，忙态永远回收不了。

        映射表是存活探活的唯一 pid 来源。插件每重载一次就是一个新 boot UUID = 一个新
        写者（被拒接状态分片的写者照样在这里登记映射），FIFO 淘汰最老条目会把最老登记
        的**忙写者**挤出表。这里把容量压到 3（白盒）制造同样的挤出现场，再用行为断言
        收口：映射还在 → 探活拿得到 pid → 忙态被回收。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        capacity = 3
        polls = int(getattr(mon, "_DEAD_WRITER_PROBE_EVERY_N_POLLS", 40)) + 3
        monkeypatch.setattr(DshMonitor, "_OWNER_FILE_MAX", capacity)
        try:
            # 忙写者最先登记：FIFO 下正是被挤掉的那一个
            self._write(bridge, "dsh-101.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            mon._poll()
            assert mgr._last_raw.get("dsh") == "working"

            # 三个安静写者把映射表挤出容量（每个新写者一条映射）
            for i, pid in enumerate((201, 202, 203)):
                self._write(bridge, f"dsh-{pid}.jsonl",
                            {"event": "AgentStatus", "state": "idle", "inst": f"boot{i}"})
                mon._poll()

            assert "inst:bootA" in mon._owner_files, \
                "还在忙的写者不许被挤出映射表（它的存活探活全靠这条映射）"
            assert len(mon._owner_files) <= capacity, f"非活跃条目仍要压回容量内：{mon._owner_files}"
            assert mgr.any_busy() is True

            # 忙写者的进程死掉（只认确定已死）：映射还在 → 探活能拿到 pid → 忙态回收
            monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: pid != 101)
            assert self._poll_until(mon, lambda: not mgr.any_busy(), polls), \
                "忙写者的映射被挤掉后，它的进程死了也没有 pid 可探——忙态永远回收不了"
            assert "inst:bootA" not in mon._owner_files
        finally:
            mgr.shutdown()

    def test_dead_probe_window_rotates_so_the_ninth_dead_writer_is_recovered(self, tmp_path, monkeypatch):
        """探活窗口有界但**轮转**：第 9 个死写者必须在有限轮内被探到（不再被饿死）。

        实证缺陷：窗口取候选集**固定前缀**时，8 个"活着但状态卡在忙"的写者长期占住
        前 8 位（插件重载 = 同进程新 boot UUID，上一代的忙态留在桌宠里、pid 却仍
        活着），排在窗外的**真死写者**永远进不了窗口，它的忙态再也回收不了——桌宠
        永远忙。修复后单轮仍 ≤8 个，但游标按轮推进，第 9 个写者第 2 轮必被探到。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = mon._DEAD_WRITER_PROBE_LIMIT
        cadence = mon._DEAD_WRITER_PROBE_EVERY_N_POLLS
        probed: list[int] = []
        monkeypatch.setattr(agent_link, "_bridge_writer_alive",
                            lambda pid: probed.append(pid) or (pid != 909))
        try:
            # 同一个活进程（dsh-500）里先后 8 代插件 boot，每代都把状态留在"忙"
            self._write(bridge, "dsh-500.jsonl",
                        *[{"event": "AgentStatus", "state": "working", "inst": f"boot-{i}"}
                          for i in range(limit)])
            mon._poll()
            # 第 9 个写者：文件 dsh-909 的进程已死，状态停在"忙"
            self._write(bridge, "dsh-909.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "boot-dead"})
            mon._poll()
            assert len(mon._converger.owners_needing_liveness()) == limit + 1, "夹具不成立：候选少于窗口+1"
            assert len(mon._owner_files) == limit + 1

            # 第 1 轮：窗口只有 8 个名额且死写者排在窗外 —— 本轮探不到，但绝不超探
            probed.clear()
            mon._dead_probe_tick = 0
            for _ in range(cadence):
                mon._poll()
            assert len(probed) == limit, f"单轮必须 ≤{limit} 个：{probed}"
            assert set(probed) == {500}, f"本轮只该碰窗口内写者：{probed}"
            assert ("dsh", "inst:boot-dead") in mgr._owner_raw, "窗外写者本轮不该被回收"
            assert mgr.any_busy() is True

            # 第 2 轮：游标回卷到窗外 —— 死写者必须在这一轮被探到并退役
            probed.clear()
            mon._dead_probe_tick = 0
            for _ in range(cadence):
                mon._poll()
            assert len(probed) == limit, f"单轮必须 ≤{limit} 个：{probed}"
            assert probed[0] == 909, f"窗口必须轮转（游标回卷到窗外）：{probed}"
            assert probed.count(500) == limit - 1, probed
            assert ("dsh", "inst:boot-dead") not in mgr._owner_raw, "第 9 个死写者必须被回收"
            assert "inst:boot-dead" not in mon._converger._scope_keys
            assert mgr.any_busy() is True, "其余活写者仍在忙，不得被连带退役"
        finally:
            mgr.shutdown()

    def test_dead_probe_cursor_resets_on_stop_start_and_offline(self, tmp_path, monkeypatch):
        """轮转游标的三个复位点：关监测（begin_stop）/ 重启（start）/ 整体离线。

        游标是"本轮从候选集哪个位置开始"的记忆：不复位就把上一代次的相位带进新
        代次/新写者表，最坏情况让新表里的死写者白等一整圈才轮到。三个复位点各测
        一次（离线用真探活路径，不靠内部状态制造红）。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = mon._DEAD_WRITER_PROBE_LIMIT
        cadence = mon._DEAD_WRITER_PROBE_EVERY_N_POLLS
        monkeypatch.setattr(agent_link, "_bridge_writer_alive", lambda pid: True)
        try:
            # 候选数 > 窗口容量才轮转：9 个写者跑一轮，游标必须离开 0
            for i in range(limit + 1):
                self._write(bridge, f"dsh-{700 + i}.jsonl",
                            {"event": "AgentStatus", "state": "working", "inst": f"boot-{i}"})
            mon._poll()
            assert len(mon._converger.owners_needing_liveness()) == limit + 1
            mon._dead_probe_tick = 0
            for _ in range(cadence):
                mon._poll()
            assert mon._dead_probe_cursor == limit, mon._dead_probe_cursor

            # ① 关监测：游标复位
            mon.begin_stop()
            assert mon._dead_probe_cursor == 0

            # ② 重启（真 start/真 worker/真 stop）：代次清空，游标也回 0
            mon._dead_probe_cursor = limit
            assert mon.start() is True
            assert mon._dead_probe_cursor == 0, "start 必须复位探活窗口"
            mon.stop()

            # ③ 整体离线（写者文件镜像被清）：游标随之复位
            mon._running = True
            mon._dead_probe_cursor = limit
            monkeypatch.setattr(harness_launcher, "is_running", lambda port: False)
            monkeypatch.setattr(harness_launcher, "desktop_process_running", lambda: False)
            mon._probe_online()
            assert mon._online is False and not mon._owner_files
            assert mon._dead_probe_cursor == 0, "离线清空写者镜像时必须复位游标"
        finally:
            mgr.shutdown()

    def test_dead_probe_window_covers_the_whole_registry_within_bounded_rounds(self, tmp_path, monkeypatch):
        """窗口轮转的覆盖保证：注册表上界 32 个候选时，⌈32/8⌉ = 4 轮内每个都被探到。

        顺序无关（不赌目录枚举顺序）：只断言每轮恰好 8 个候选、4 轮的并集覆盖全部
        写者 pid——这正是"有限轮必探"的机器可校验形式。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = mon._DEAD_WRITER_PROBE_LIMIT
        cadence = mon._DEAD_WRITER_PROBE_EVERY_N_POLLS
        total = mon._converger.SCOPE_REGISTRY_MAX
        pids = [7000 + i for i in range(total)]
        probed: list[int] = []
        monkeypatch.setattr(agent_link, "_bridge_writer_alive",
                            lambda pid: probed.append(pid) or True)   # 全活：谁都不会被退役
        try:
            for i, pid in enumerate(pids):
                self._write(bridge, f"dsh-{pid}.jsonl",
                            {"event": "AgentStatus", "state": "working", "inst": f"boot-{i}"})
            mon._poll()
            assert len(mon._converger.owners_needing_liveness()) == total

            covered: set[int] = set()
            rounds = (total + limit - 1) // limit
            for _round_index in range(rounds):
                probed.clear()
                mon._dead_probe_tick = 0
                for _ in range(cadence):
                    mon._poll()
                assert len(probed) == limit, f"单轮必须 ≤{limit} 个：{probed}"
                covered |= set(probed)
            assert covered == set(pids), f"有限轮内必须覆盖全部写者：缺 {sorted(set(pids) - covered)}"
            assert mgr.any_busy() is True, "全活写者不得被探活退役"
        finally:
            mgr.shutdown()

    def test_offline_clears_writer_mirrors_so_restart_is_not_forever_busy(self, tmp_path, monkeypatch):
        """DSH 整体离线清写者镜像；重启后新写者的待机不会再被旧分片压成"永远忙"。"""
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-101.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            mon._poll()
            assert mgr.any_busy() is True

            # DSH 整体离线（真探活路径：端口与桌面端进程都探不到）
            monkeypatch.setattr(harness_launcher, "is_running", lambda port: False)
            monkeypatch.setattr(harness_launcher, "desktop_process_running", lambda: False)
            mon._probe_tick = mon._ONLINE_PROBE_EVERY_N_POLLS - 1
            mon._poll()
            assert mgr.any_busy() is False, "offline 后不得还停在上一个写者的忙态"
            assert mgr._last_raw.get("dsh") is None, "offline 必须清原始状态镜像"
            assert not mgr._owner_raw, "offline 必须清写者分片"
            assert not getattr(mon, "_owner_files", {}), "offline 必须清写者→文件镜像"

            # 新 DSH 起来（新 boot 身份）只报待机：不得被旧分片压成忙
            monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
            self._write(bridge, "dsh-202.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootB"})
            mon._probe_tick = mon._ONLINE_PROBE_EVERY_N_POLLS - 1
            mon._poll()
            assert mgr.any_busy() is False, "重启后的新写者不得继承上一代的忙"
            assert mgr._last_raw.get("dsh") in (None, "idle")
        finally:
            mgr.shutdown()

    # ------------------------------------------------------------------
    # 完成提醒不篡改全局忙闲口径
    # ------------------------------------------------------------------
    def test_finishing_writer_does_not_flatten_global_busy(self, tmp_path, monkeypatch):
        """A 的回合收尾照常提醒，但 B 还在忙时不得把整体判成待机。"""
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            assert mgr._last_raw["dsh"] == "working"

            win = mgr.win
            win.switched.clear()
            bubbles.clear()
            # A 实例完整走完一个回合（turn/start + turn/end）：完成提醒该来
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:1", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sA", "step": "turn:1", "inst": "bootA"})
            mon._poll()

            assert self._wait_until(app, lambda: bool(self._succeeded(bubbles))), \
                f"A 的真实收尾必须照常提醒：{bubbles}"
            assert mgr._last_raw["dsh"] == "working", "B 还在忙：不得被 A 的收尾压成待机"
            assert mgr.any_busy() is True, "还有写者在忙，any_busy 必须为真"
            assert "idle" not in win.switched, f"不得切待机动画：{win.switched}"
            assert "dsh" not in mgr._done_pending, "确认已到期弹过，不该还挂着"
        finally:
            mgr.shutdown()

    # ------------------------------------------------------------------
    # 注册表满：拒接，而不是并桶互撤
    # ------------------------------------------------------------------
    def test_scope_overflow_does_not_let_writers_revoke_each_other(self, tmp_path, monkeypatch):
        """注册表满时新写者被拒接状态分片；C 的等待绝不能被 D 的 idle 撤掉。"""
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = int(getattr(mon._converger, "SCOPE_REGISTRY_MAX", 32))
        try:
            for i in range(limit):
                self._write(bridge, f"dsh-{700 + i}.jsonl",
                            {"event": "AgentStatus", "state": "working", "inst": f"boot-busy-{i}"})
            mon._poll()

            # C 提出问题（注册表满时的新写者）
            self._write(bridge, "dsh-901.jsonl",
                        {"event": "question/requested", "sessionId": "sC", "callId": "qC",
                         "inst": "bootC", "questions": [{"id": "q1", "question": "C 的问题"}]})
            mon._poll()
            assert len(mgr.pending_interactions_for("dsh")) == 1

            # D（另一个新写者）回待机：不得撤掉 C 的等待（并桶才会互撤）
            self._write(bridge, "dsh-902.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootD"})
            mon._poll()
            assert mgr.pending_interactions_for("dsh"), "D 的 idle 撤掉了 C 还在等的交互"
            assert resolved == []

            # 拒接不等于丢归属：C/D 的原始状态各自记在自己名下（不与别人共桶）
            self._write(bridge, "dsh-901.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootC"})
            mon._poll()
            assert ("dsh", "inst:bootC") in mgr._owner_raw
            assert ("dsh", "inst:bootD") in mgr._owner_raw
            assert next(iter(mgr.pending_interactions_for("dsh").values()))["owners"] == {"inst:bootC"}
            # 拒接是可见的：计数 + warning（不是静默丢事件）
            assert mon._converger._scope_overflow_count >= 1
            assert len(mon._converger._scope_keys) <= limit, "注册表必须有界"
            assert "inst:bootC" not in mon._converger._scope_keys, "满员时新写者只能被拒接"
        finally:
            mgr.shutdown()

    def test_refused_writer_diagnostic_is_one_per_record_and_throttled(self, tmp_path, monkeypatch, caplog):
        """被拒写者的诊断：**每条记录一次**（不翻倍）且同类告警节流（不刷屏）。

        实证缺陷：一条记录会经两次准入（摄入侧盖归属的 ``_record_owner`` + 建状态
        分片的 ``handle_record``），诊断挂在准入本身上就变成每条被拒记录计两次、
        告警两条，且没有任何节流——被拒写者持续写盘 = 持续刷 warning。修复后计数
        按记录推进、告警首次立即可见其后每 ``SCOPE_REJECT_LOG_EVERY`` 次汇总一条，
        而归属（谁写的仍记在谁名下）一字不动。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = mon._converger.SCOPE_REGISTRY_MAX
        every = SCOPE_REJECT_LOG_EVERY
        try:
            for i in range(limit):
                self._write(bridge, f"dsh-{700 + i}.jsonl",
                            {"event": "AgentStatus", "state": "working", "inst": f"boot-busy-{i}"})
            mon._poll()
            assert mon._converger._scope_overflow_count == 0

            burst = every * 3
            self._write(bridge, "dsh-901.jsonl",
                        *[{"event": "AgentStatus", "state": "working", "inst": "boot-new"}
                          for _ in range(burst)])
            with caplog.at_level("WARNING"):
                mon._poll()

            warnings = [r for r in caplog.records if "保守拒接新写者" in r.getMessage()]
            assert mon._converger._scope_overflow_count == burst, \
                f"每条被拒记录只该计一次，实为 {mon._converger._scope_overflow_count}"
            assert len(warnings) == 1 + burst // every, \
                f"{burst} 条拒接不该有 {len(warnings)} 条告警（必须节流）"
            assert "inst:boot-new" not in mon._converger._scope_keys
            assert len(mon._converger._scope_keys) <= limit, "注册表必须有界"
            # 归属不受影响：被拒写者的记录仍标在它自己名下
            assert mgr._owner_raw[("dsh", "inst:boot-new")][0] == "working"
        finally:
            mgr.shutdown()

    def test_mirrored_full_turn_across_files_notifies_once(self, tmp_path, monkeypatch):
        """完整镜像（两个写者各写一份 turn/start + turn/end）只提醒一次。

        两份副本先后到达（两个进程各自 flush，间隔可以大于完成冷却）——身份必须相同，
        否则同一回合会被提醒两次（镜像 start 不该给第二条 end 发一个新 generation）。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sM", "step": "turn:1", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sM", "step": "turn:1", "inst": "bootA"})
            mon._poll()
            assert self._wait_until(app, lambda: bool(self._succeeded(bubbles))), bubbles
            self._pump(app, 0.3)
            assert len(self._succeeded(bubbles)) == 1, bubbles

            clock[0] += 30.0          # 越过完成冷却：第二次提醒不再被冷却挡住
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "turn/start", "sessionId": "sM", "step": "turn:1", "inst": "bootB"},
                        {"event": "turn/end", "sessionId": "sM", "step": "turn:1", "inst": "bootB"})
            mon._poll()
            self._pump(app, 1.2)
            assert len(self._succeeded(bubbles)) == 1, \
                f"同一回合的镜像副本只该提醒一次：{bubbles}"
            assert sounds.count("done.wav") == 1, sounds

            # 真下一回合（step 变了）照常提醒：镜像去重不得把正常回合也吞了
            clock[0] += 30.0
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sM", "step": "turn:2", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sM", "step": "turn:2", "inst": "bootA"})
            mon._poll()
            assert self._wait_until(
                app, lambda: len(self._succeeded(bubbles)) == 2), bubbles
        finally:
            mgr.shutdown()

    # ------------------------------------------------------------------
    # 完成确认与失败标记按**写者归属**裁决，不按 agent 全局
    # ------------------------------------------------------------------
    def test_other_writer_busy_inside_confirm_window_keeps_completion(self, tmp_path, monkeypatch):
        """A 的完成确认窗内 B 实例回忙：能撤的只有 A 自己。

        确认窗（800ms）的语义是「**这一写者**没有再回忙」。B 的 ``AgentStatus
        working`` 会把**折叠值**重新变忙，但 A 自己的分片还是终态；拿折叠值撤确认
        等于让 B 的忙替 A 取消提醒——A 的回合身份已经进了 ``_dsh_turn_ids``，不会
        重排，这条完成提醒就永久丢了。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            mon._poll()
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:1", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sA", "step": "turn:1", "inst": "bootA"})
            mon._poll()
            assert "dsh" in mgr._done_pending, "A 的收尾必须排定完成确认"
            assert mgr._done_owner.get("dsh") == "inst:bootA", mgr._done_owner
            bubbles.clear()
            sounds.clear()

            # 确认窗内 B（另一个写者）回忙
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            assert self._wait_until(app, lambda: bool(self._succeeded(bubbles))), \
                f"B 的忙替 A 取消了完成确认：{bubbles}"
            assert sounds.count("done.wav") == 1, sounds
        finally:
            mgr.shutdown()

    def test_writer_failure_does_not_swallow_other_writers_success(self, tmp_path, monkeypatch):
        """A 实例的 execution/failed 不得吞掉 B 实例的成功收尾。

        硬失败标记（``_saw_error``）此前按 agent 整条打：B 的成功收尾在
        ``_on_dsh_turn_completed`` 的早期闸上被整条 return——既没有完成提醒，
        B 自己的分片还留在 working（整体永远忙）。失败是**写者自己的**回合事实，
        只能挡它自己那条收尾。这里 B 在 A 失败**之前**就已经在忙，失败与 B 的
        turn/end 之间没有新的忙入口（与桥接真实写盘顺序一致）。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"})
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "execution/failed", "sessionId": "sA", "inst": "bootA",
                         "failureType": "tool_failed", "errorCode": "E_BASH",
                         "errorMessage": "exit status 1"})
            mon._poll()
            assert "dsh" in mgr._saw_error, "硬失败必须按 agent 打标（岛侧翻译仍读它）"
            bubbles.clear()
            sounds.clear()

            self._write(bridge, "dsh-22.jsonl",
                        {"event": "turn/end", "sessionId": "sB", "step": "turn:B", "inst": "bootB"})
            mon._poll()
            assert self._wait_until(app, lambda: bool(self._succeeded(bubbles))), \
                f"A 的失败吞掉了 B 的成功收尾：{bubbles}"
            assert mgr._owner_raw.get(("dsh", "inst:bootB"), ("", 0))[0] == "success", \
                "B 的分片必须落到终态（否则整体永远停在忙）"
            assert sounds.count("done.wav") == 1, \
                f"B 的成功收尾不得被 A 的失败标记掐掉音效：{sounds}"
        finally:
            mgr.shutdown()

    def test_writer_restart_inside_confirm_window_still_cancels_done(self, tmp_path, monkeypatch):
        """A 自己在确认窗内开新回合（只有 turn/start）：不得弹假完成。

        「只撤当事人的确认」不能把 800ms 抖动过滤一起撤掉：A 的收尾排定后，A 在窗内
        用 ``turn/start`` 开新一回合时，**A 自己的分片**还停在 success（turn/start 不
        写分片），忙只体现在折叠值上。起点记录一到就按归属撤掉本写者的确认（见
        ``_on_dsh_turn_started``）。本条是第 1 条（B 的忙不撤 A 的确认）的反向守卫。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"},
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:1", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sA", "step": "turn:1", "inst": "bootA"})
            mon._poll()
            assert "dsh" in mgr._done_pending, "A 的收尾必须排定完成确认"
            bubbles.clear()
            sounds.clear()

            # 确认窗内 A 自己开新回合（桥接真实起点记录；这一拍没有 AgentStatus 分片更新）
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:2", "inst": "bootA"})
            mon._poll()
            self._pump(app, 1.2)                    # 跑满确认窗：被取消/作废的确认不得弹
            assert self._succeeded(bubbles) == [], \
                f"A 已经开了新回合，不得弹完成口径气泡：{bubbles}"
            assert "done.wav" not in sounds, sounds
        finally:
            mgr.shutdown()

    def test_own_round_start_cancels_own_done_while_other_writer_busy(self, tmp_path, monkeypatch):
        """A 的确认窗内 B 在忙、A 自己只写 turn/start 开新回合：仍不得弹 A 的假完成。

        起点用**原记录**（turn/start 带 ``inst``）指认当事人，不靠同文本/时间戳：
        A 一开新回合，它上一轮的完成确认立即作废。此前的实现只在 ``_fire_done``
        到期时看"折叠值忙有没有别的写者能解释"——B 正忙把这个忙解释掉了，于是 A
        已经开新回合还弹「已完成本轮任务」（本票修掉的残留）。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootA"},
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:1", "inst": "bootA"},
                        {"event": "turn/end", "sessionId": "sA", "step": "turn:1", "inst": "bootA"},
                        {"event": "AgentStatus", "state": "working", "inst": "bootB"})
            mon._poll()
            assert mgr._done_owner.get("dsh") == "inst:bootA", mgr._done_owner
            bubbles.clear()
            sounds.clear()

            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:2", "inst": "bootA"})
            mon._poll()
            assert "dsh" not in mgr._done_pending, "A 的新回合必须立即作废它自己的确认"
            self._pump(app, 1.2)
            assert self._succeeded(bubbles) == [], \
                f"B 的忙替 A 兜住了新回合，仍弹了假完成：{bubbles}"
            assert "done.wav" not in sounds, sounds
        finally:
            mgr.shutdown()

    def test_other_writer_round_start_keeps_pending_completion(self, tmp_path, monkeypatch):
        """A 只写 turn/start 开新回合时，B 待定的完成确认不得被误撤。

        B 刚排定完成确认，A（**没有任何 AgentStatus**，只有起点记录）开新回合——摊开
        到聚合状态上是"整体又忙了"，而 A 的忙在分片账上没有归属：B 的到期判据于是把
        这个忙当成 B 自己的新回合作废，B 的真实完成被静默吞掉。起点按写者落分片后，
        这份忙有主，B 的完成照常弹。这是修 A 的假完成时最容易带出来的反向缺陷。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        try:
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "turn/start", "sessionId": "sB", "step": "turn:B1", "inst": "bootB"},
                        {"event": "turn/end", "sessionId": "sB", "step": "turn:B1", "inst": "bootB"})
            mon._poll()
            assert mgr._done_owner.get("dsh") == "inst:bootB", mgr._done_owner
            bubbles.clear()
            sounds.clear()

            # 确认窗内 A（另一实例，没有 AgentStatus）用起点开新回合
            self._write(bridge, "dsh-11.jsonl",
                        {"event": "turn/start", "sessionId": "sA", "step": "turn:A1", "inst": "bootA"})
            mon._poll()
            assert self._wait_until(app, lambda: bool(self._succeeded(bubbles))), \
                f"A 的新回合吞掉了 B 的完成确认：{bubbles}"
            assert sounds.count("done.wav") == 1, sounds
        finally:
            mgr.shutdown()

    # ------------------------------------------------------------------
    # 交互重登记：归属集合累积，绝不整字典覆盖
    # ------------------------------------------------------------------
    def test_reregistered_interaction_keeps_all_owners(self, tmp_path, monkeypatch):
        """同一 iid 的重登记不得重置归属集合（账本淘汰后的重投）。

        摄入账本是有界的（512 条 / 900s）：长等待里旧条目被挤出后，B 再投同一条
        请求就走 ``_register_interaction`` 的重登记路径。重登记若整字典覆盖，A 的
        归属被丢掉——B 一回待机就把 A 还在等的气泡整个撤掉。本用例走真链路：同一条
        问题先由 A 投递，中间用一条别的记录把账本身份挤出，再由 B 投递。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        question = {"event": "question/requested", "sessionId": "sX", "callId": "qX",
                    "questions": [{"id": "q1", "question": "同一个问题"}]}
        try:
            self._write(bridge, "dsh-11.jsonl", {**question, "inst": "bootA"})
            mon._poll()
            pending = mgr.pending_interactions_for("dsh")
            assert len(pending) == 1, pending
            first = next(iter(pending.values()))
            assert first["owners"] == {"inst:bootA"}, first["owners"]
            alert_id = first["alert_id"]

            # 账本被后续记录挤出（等价于长等待里最近 512 条之外 / 900s TTL 过期）
            mon._event_ledger.max_entries = 1
            self._write(bridge, "dsh-33.jsonl",
                        {"event": "AgentStatus", "state": "working", "inst": "bootZ"})
            mon._poll()

            self._write(bridge, "dsh-22.jsonl", {**question, "inst": "bootB"})
            mon._poll()
            pending = mgr.pending_interactions_for("dsh")
            assert len(pending) == 1, f"重投递必须就地更新同一条：{pending}"
            item = next(iter(pending.values()))
            assert item["alert_id"] == alert_id, "重登记必须复用同一 alert 身份"
            assert item["owners"] == {"inst:bootA", "inst:bootB"}, \
                f"重登记重置了已有归属：{item['owners']}"

            # B 回待机：A 还在等，交互必须保留
            self._write(bridge, "dsh-22.jsonl",
                        {"event": "AgentStatus", "state": "idle", "inst": "bootB"})
            mon._poll()
            assert mgr.pending_interactions_for("dsh"), \
                "B 回待机撤掉了 A 还在等的交互（重登记丢归属的后果）"
            assert resolved == [], resolved
        finally:
            mgr.shutdown()

    # ------------------------------------------------------------------
    # 单条 pending 的归属集合有界：满时锁存 overflow，不丢已有归属
    # ------------------------------------------------------------------
    def test_pending_owner_set_is_bounded_and_overflow_latches(self, tmp_path, monkeypatch):
        """归属集合必须有界；满时锁存 overflow，收口收紧到可指认的来源身份。

        ``_on_bridge_owner_attested`` 此前对集合只加不减：同一条合法在途请求被多个
        写者反复投递（插件重载 = 新 boot 身份）就能把一条 pending 的归属集合堆成
        任意大。满时既不能丢旧归属（后来的 idle 会误撤 A 还在等的交互），也不能把
        后来者并进"无主桶"——只能锁存 overflow，并把收口判据收紧到"已记录的当事人
        + 同一条会话"（单帧清掉整条会把还有人在等的提醒一起撤掉）。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        limit = int(mon._converger.SCOPE_REGISTRY_MAX)
        question = {"event": "question/requested", "sessionId": "sX", "callId": "qX",
                    "questions": [{"id": "q1", "question": "同一个问题"}]}
        try:
            self._write(bridge, "dsh-11.jsonl", {**question, "inst": "bootA"})
            mon._poll()
            pending = mgr.pending_interactions_for("dsh")
            assert len(pending) == 1, pending
            iid = next(iter(pending))

            for index in range(10_000):
                mgr._on_bridge_owner_attested("dsh", {**question, "_scope": f"inst:boot-{index}"})
            item = mgr._pending_interactions[iid]
            assert len(item["owners"]) <= limit, f"归属集合无界：{len(item['owners'])}"
            assert item["owners_overflow"] is True, "满时必须锁存 overflow 标记"
            assert "inst:bootA" in item["owners"], "已有归属一律保留（不丢旧 owner）"

            # 未知来源的 idle（归属未知的通配口径）不许拿"没记到归属"当"没人在等"
            mgr._on_agent_state("dsh", "idle", mon._emit_gen)
            assert mgr.pending_interactions_for("dsh"), "未知 idle 撤掉了 overflow 锁存的交互"
            # 死亡退役只证明它自己走了，同样不许误撤
            mgr._on_dsh_scope_retired("inst:bootA", mon._emit_gen)
            assert mgr.pending_interactions_for("dsh"), "死亡退役误撤了 overflow 锁存的交互"
            # 合法长等待不吃 TTL（只有无身份的降级提示才按 TTL 收口）
            mgr._prune_pending_interactions(now=time.monotonic() + 10 ** 6)
            assert mgr.pending_interactions_for("dsh"), "带身份的 pending 不得被 TTL 收掉"
            assert resolved == [], resolved

            # 已记录的当事人 + 同一条会话的结束帧仍能收口（否则这条永远撤不掉）
            mgr._on_interaction_lifecycle(
                "dsh", {"event": "turn/end", "sessionId": "sX", "_scope": "inst:boot-0"})
            assert mgr.pending_interactions_for("dsh") == {}, "可信会话结束必须能收口"
            assert resolved, "收口必须真的关掉常驻气泡"
        finally:
            mgr.shutdown()

    def test_overflow_entry_collects_only_by_identified_source(self, tmp_path, monkeypatch):
        """overflow 锁存后：别 owner / 未知来源 / 缺会话的结束帧都不许收口，resolved 才收。

        那条 pending 的归属集合已经没记全（还有没记下的写者），单个结束帧清掉整条
        就是把还有人在等的提醒一起撤了。可指认的口径只有两个：**已记录的当事人 +
        同一条会话**的会话结束帧，和 ``resolved`` 的准确 rpc/call 身份。
        """
        mgr, mon, bubbles, alerts, resolved, sounds, clock, app = self._make_rig(tmp_path, monkeypatch)
        bridge = mon.events_dir
        question = {"event": "question/requested", "sessionId": "sX", "callId": "qX",
                    "questions": [{"id": "q1", "question": "同一个问题"}]}
        try:
            self._write(bridge, "dsh-11.jsonl", {**question, "inst": "bootA"})
            mon._poll()
            iid = next(iter(mgr.pending_interactions_for("dsh")))
            for index in range(10_000):
                mgr._on_bridge_owner_attested("dsh", {**question, "_scope": f"inst:boot-{index}"})
            assert mgr._pending_interactions[iid]["owners_overflow"] is True

            # ① 未知来源（记录没有写者归属）：来源不明就不许替还等着的人做决定
            mgr._on_interaction_lifecycle("dsh", {"event": "turn/end", "sessionId": "sX"})
            assert mgr.pending_interactions_for("dsh"), "未知来源的会话结束撤掉了 overflow 条目"
            # ② 别 owner（没进过归属集合的写者）：一声清掉的是别人还在等的那条
            mgr._on_interaction_lifecycle(
                "dsh", {"event": "turn/end", "sessionId": "sX", "_scope": "inst:bootZ"})
            assert mgr.pending_interactions_for("dsh"), "别 owner 的会话结束撤掉了 overflow 条目"
            # ③ 已记录的当事人但不带会话（旧帧）：会话未知 = 那条结束帧不指认这条交互
            mgr._on_interaction_lifecycle(
                "dsh", {"event": "turn/end", "_scope": "inst:boot-0"})
            assert mgr.pending_interactions_for("dsh"), "缺会话的结束帧撤掉了 overflow 条目"
            assert resolved == [], resolved

            # ④ 准确 rpc/call 身份的 resolved 必须收得掉，否则溢出的那条成了永久提醒
            mgr._on_question_resolved("dsh", {"event": "question/resolved",
                                              "sessionId": "sX", "callId": "qX"})
            assert mgr.pending_interactions_for("dsh") == {}, "resolved 收不掉 overflow 条目"
            assert resolved, "收口必须真的关掉常驻气泡"
        finally:
            mgr.shutdown()
