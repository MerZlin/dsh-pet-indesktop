# -*- coding: utf-8 -*-
"""一键退出子肥鱼：关闭 slot-N 进程并清理 runtime 标记；slot 数据保留，主 slot-0 不受影响。"""
from __future__ import annotations

import json
import os

import pytest

from pet import child_pet_cleanup
from pet import slot_manager as slot_manager_mod


def _stub_pet_identity(monkeypatch):
    """批 H：杀前用 exe 路径核验 pid 身份（防 pid 复用误杀）。测试用假 pid，
    统一打桩为「是本程序」；身份核验本身由专门用例覆盖。"""
    monkeypatch.setattr(child_pet_cleanup, "_is_pet_process", lambda pid: True)


def test_clear_spawned_pets_kills_processes_and_keeps_slot_data(tmp_path, monkeypatch):
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)

    # 主数据
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "sessions").mkdir()

    # 子槽数据
    (root / "config-slot-1.json").write_text("{}", encoding="utf-8")
    (root / "config-slot-2.json").write_text("{}", encoding="utf-8")
    (root / "todo_items-slot-2.json").write_text("{}", encoding="utf-8")
    (root / "sessions-slot-1").mkdir()
    (root / "sessions-slot-1" / "s1.json").write_text("{}", encoding="utf-8")

    # runtime 标记：一个已死、一个存活（用假 PID，不真杀）
    dead_marker = root / "runtime-111.json"
    dead_marker.write_text(json.dumps({"pid": 111}), encoding="utf-8")
    live_marker = root / "runtime-222.json"
    live_marker.write_text(json.dumps({"pid": 222}), encoding="utf-8")

    terminated = []
    alive = {222}
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup,
        "_pid_alive",
        lambda pid: pid in alive,
    )

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)  # 杀后确认：进程真的退出

    monkeypatch.setattr(
        child_pet_cleanup,
        "_terminate_pet_process",
        fake_terminate,
    )

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == [222]
    assert result["failed_pids"] == []
    assert terminated == [222]
    assert not dead_marker.exists()
    assert not live_marker.exists()
    # 批 F 起只退出进程：slot 配置/会话/待办数据全部保留（占位语义靠它们恢复）
    assert (root / "config-slot-1.json").exists()
    assert (root / "config-slot-2.json").exists()
    assert (root / "todo_items-slot-2.json").exists()
    assert (root / "sessions-slot-1").is_dir()
    # 主数据必须保留
    assert (root / "config.json").exists()
    assert (root / "sessions").is_dir()


def test_clear_spawned_pets_removes_v2_markers_and_keeps_slot_data(tmp_path, monkeypatch):
    """批 B：多进程模式的 v2 标记（只写 pet-runtime-v2-*.json 新名）被找到并清理。

    旧 glob 只认 runtime-*.json，会匹配不到新标记 → 子进程杀不掉；修复后
    两处 glob 同时认新旧两种命名。批 F 起 slot 数据保留，主鱼不碰。
    """
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)

    # 主数据
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "sessions").mkdir()
    # 子槽数据
    (root / "config-slot-1.json").write_text("{}", encoding="utf-8")
    (root / "config-slot-2.json").write_text("{}", encoding="utf-8")
    (root / "sessions-slot-1").mkdir()
    (root / "sessions-slot-1" / "s1.json").write_text("{}", encoding="utf-8")

    # v2 runtime 标记：一个已死、一个存活（用假 PID，不真杀）
    dead_v2 = root / "pet-runtime-v2-333-slot-1.json"
    dead_v2.write_text(json.dumps({"pid": 333}), encoding="utf-8")
    live_v2 = root / "pet-runtime-v2-444-slot-2.json"
    live_v2.write_text(json.dumps({"pid": 444}), encoding="utf-8")

    terminated = []
    alive = {444}
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup,
        "_pid_alive",
        lambda pid: pid in alive,
    )

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(
        child_pet_cleanup,
        "_terminate_pet_process",
        fake_terminate,
    )

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == [444]
    assert result["failed_pids"] == []
    assert terminated == [444]
    assert not dead_v2.exists()
    assert not live_v2.exists()
    # 批 F：slot 数据保留
    assert (root / "config-slot-1.json").exists()
    assert (root / "config-slot-2.json").exists()
    assert (root / "sessions-slot-1").is_dir()
    # 主数据必须保留
    assert (root / "config.json").exists()
    assert (root / "sessions").is_dir()


def test_clear_spawned_pets_handles_legacy_and_v2_together_idempotent(
        tmp_path, monkeypatch):
    """批 B：混合新旧命名标记在同一调用里都被找到并清理；重复调用幂等无残留。"""
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}", encoding="utf-8")

    legacy = root / "runtime-111.json"
    legacy.write_text(json.dumps({"pid": 111}), encoding="utf-8")
    v2 = root / "pet-runtime-v2-222-slot-1.json"
    v2.write_text(json.dumps({"pid": 222}), encoding="utf-8")

    terminated = []
    alive = {111, 222}
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: pid in alive)

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process",
        fake_terminate,
    )

    child_pet_cleanup.clear_spawned_pets(root)
    # 新旧两种命名都被找到并清理
    assert not legacy.exists()
    assert not v2.exists()
    assert terminated == [111, 222]

    # 幂等：再跑一遍无残留、不重复杀
    result = child_pet_cleanup.clear_spawned_pets(root)
    assert result["killed_pids"] == []
    assert result["failed_pids"] == []
    assert terminated == [111, 222]


def test_clear_spawned_pets_kill_failure_keeps_marker_and_reports(
        tmp_path, monkeypatch):
    """批 G：杀进程失败（taskkill 异常/超时、权限不足等）不再静默吞掉——
    保留 runtime 标记（不毁灭痕迹，供下次重试），pid 记入 failed_pids。
    批 I：两段式杀法（先全杀再统一等确认），不再有逐只重试。

    实机复现：旧实现杀失败照删标记 → 子肥鱼存活且清理方无迹可查。
    """
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)

    live_marker = root / "runtime-777.json"
    live_marker.write_text(json.dumps({"pid": 777}), encoding="utf-8")

    terminated = []
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: pid == 777)
    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process",
        lambda pid: terminated.append(pid),  # 杀不动：进程始终存活
    )

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == []
    assert result["failed_pids"] == [777]
    assert terminated == [777], "两段式杀法：每只只杀一次，统一等确认"
    assert live_marker.exists(), "进程仍活时标记必须保留"


def test_clear_spawned_pets_kill_success_removes_marker(
        tmp_path, monkeypatch):
    """批 I 两段式：杀成功 → 计入 killed_pids 并正常删标记。"""
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)

    live_marker = root / "runtime-888.json"
    live_marker.write_text(json.dumps({"pid": 888}), encoding="utf-8")

    terminated = []
    alive = {888}
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: pid in alive)

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process", fake_terminate)

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == [888]
    assert result["failed_pids"] == []
    assert terminated == [888]
    assert not live_marker.exists()


def test_clear_spawned_pets_never_kills_slot0_v2_marker(tmp_path, monkeypatch):
    """批 G 兜底：v2 标记名带 slot 编号，slot-0 是主肥鱼——即便从子肥鱼进程
    调用（其 pid 自保护只跳过自己），主鱼标记也永不杀、永不删。"""
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)
    main_v2 = root / "pet-runtime-v2-999-slot-0.json"
    main_v2.write_text(json.dumps({"pid": 999}), encoding="utf-8")
    child_v2 = root / "pet-runtime-v2-888-slot-1.json"
    child_v2.write_text(json.dumps({"pid": 888}), encoding="utf-8")

    alive = {999, 888}
    terminated = []
    _stub_pet_identity(monkeypatch)
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: pid in alive)

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process", fake_terminate)

    result = child_pet_cleanup.clear_spawned_pets(root)
    assert terminated == [888], "只杀子肥鱼，主肥鱼 slot-0 跳过"
    assert result["killed_pids"] == [888]
    assert main_v2.exists(), "主肥鱼标记必须保留"
    assert not child_v2.exists()


def test_clear_spawned_pets_recycled_pid_treated_as_stale_marker(tmp_path, monkeypatch):
    """批 H：陈旧标记的 pid 被无关进程复用（存活但不是本程序）→ 不误杀、
    按陈旧标记删除，不计入 failed_pids（实机事故：0 杀 2 失败）。"""
    root = tmp_path / "dsh-pet-standalone"
    root.mkdir(parents=True)
    marker = root / "runtime-777.json"
    marker.write_text(json.dumps({"pid": 777}), encoding="utf-8")

    monkeypatch.setattr(child_pet_cleanup, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(child_pet_cleanup, "_is_pet_process", lambda pid: False)
    terminated = []
    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process",
        lambda pid: terminated.append(pid))

    result = child_pet_cleanup.clear_spawned_pets(root)
    assert terminated == [], "pid 被无关进程复用时不得 taskkill"
    assert result["killed_pids"] == []
    assert result["failed_pids"] == []
    assert not marker.exists(), "复用 pid 的陈旧标记应被清理"


def test_clear_spawned_pets_slot_lock_as_second_source(tmp_path, monkeypatch):
    """批 H：没写过 runtime 标记的小肥鱼也持有 slots/slot-N.lock（内含 pid），
    锁文件作为第二枚举源把这类漏网之鱼杀掉；slot-0（主肥鱼）跳过。"""
    root = tmp_path / "dsh-pet-standalone"
    (root / "slots").mkdir(parents=True)
    (root / "slots" / "slot-0.lock").write_bytes(
        slot_manager_mod._format_pid_record(999))
    (root / "slots" / "slot-2.lock").write_bytes(
        slot_manager_mod._format_pid_record(888))

    alive = {999, 888}
    terminated = []
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(child_pet_cleanup, "_is_pet_process", lambda pid: True)

    def fake_terminate(pid):
        terminated.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process", fake_terminate)

    result = child_pet_cleanup.clear_spawned_pets(root)
    assert terminated == [888], "slot-2（子肥鱼）杀掉，slot-0（主肥鱼）跳过"
    assert result["killed_pids"] == [888]


@pytest.mark.skipif(os.name != "nt", reason="CREATE_NO_WINDOW 仅 Windows 有")
def test_terminate_uses_create_no_window_on_windows(monkeypatch):
    """批 H：Windows 下 taskkill 必须带 CREATE_NO_WINDOW——GUI 进程无控制台，
    裸 subprocess 会每杀一只弹一个空白终端窗口（实机反馈）。"""
    import subprocess as sp

    import pet.child_pet_cleanup as mod

    calls = []

    class _FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*a, **kw):
        calls.append(kw)
        return _FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._terminate_pet_process(12345)
    assert len(calls) == 1
    assert calls[0].get("creationflags") == sp.CREATE_NO_WINDOW


def test_pid_alive_false_after_child_killed_with_handle_held():
    """批 I 回归（实机事故）：父进程持有子进程句柄时，子进程被杀后其进程
    对象仍可被 OpenProcess 打开——_pid_alive 必须用 GetExitCodeProcess 判定
    真死活，否则杀成功的子鱼被误判存活，白等重试两轮并误报「未能退出」。"""
    import subprocess
    import sys

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert child_pet_cleanup._pid_alive(proc.pid)
        proc.kill()
        proc.wait()  # 已死，但 Popen 对象仍持有进程句柄（不 close/del）
        assert not child_pet_cleanup._pid_alive(proc.pid), (
            "句柄未释放不等于存活：必须读退出码判定")
    finally:
        try:
            proc.kill()
        except Exception:
            pass
