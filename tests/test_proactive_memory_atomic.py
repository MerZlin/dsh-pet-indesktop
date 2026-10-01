# -*- coding: utf-8 -*-
"""主动识屏短期记忆的落盘竞态回归（issue #196 / #204 / #206）。

三个断言面：

- **#196**：``record()`` 的「读—改—写」原先不进任何锁，tmp 名也固定为
  ``<name>.json.tmp``。多开桌宠是默认路径（``instance_launcher`` 为每个副窗起
  独立进程，两个进程都启用识屏），于是两个写者互相覆盖：记录静默丢失，且失败
  路径 ``except OSError: pass`` 连一行日志都没有。
- **#204**：原先没有 flush/fsync，崩溃/断电窗口里的最后若干条记录整块丢。
- **#206**：类 docstring 声称记录「标题」，模块 docstring 与实现都表明标题不落盘。

跨进程用例走真实子进程（跨进程锁只在进程边界上有意义）；线程用例用 Event 屏障
把两个写者的「读」都排在「写」之前，确定性地暴露非原子的读改写——修复后第二个
写者会被锁挡在读之前，屏障按宽预算超时放行（事件同步，不是 sleep 猜时序）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pet.proactive_memory import ProactiveMemory

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tests" / "helpers" / "proactive_memory_writer.py"

# 线程屏障的放行预算：必须显著小于产品侧的抢锁预算（见 pet/proactive_memory.py
# 的 _LOCK_RETRY_DELAYS，合计约 1.26s），否则修复后第二个写者仍在屏障里等，
# 用例会从"暴露竞态"退化成"测锁的等待上限"。
BARRIER_WAIT_SECONDS = 0.3
# 子进程用例的宽预算：CI 慢 runner 上三个解释器冷启动 + 75 次落盘也要跑完。
PROC_WAIT_SECONDS = 180.0


def _entries(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["entries"]


def test_record_roundtrip_and_prune(tmp_path):
    """既有语义不回归：新记录置顶、超出 max_entries 截尾、新实例能读回。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    memory = ProactiveMemory(memory_path, clock=lambda: 1000.0, max_entries=2)
    memory.record("a.exe", "标题不落盘", "act-a")
    memory.record("b.exe", "标题不落盘", "act-b")
    memory.record("c.exe", "标题不落盘", "act-c")

    reloaded = ProactiveMemory(memory_path, max_entries=2)
    assert [entry["activity"] for entry in reloaded.load()] == ["act-c", "act-b"]
    assert reloaded.latest()["activity"] == "act-c"
    # 标题绝不落盘（隐私红线，与 #206 同源）
    assert all("标题不落盘" not in json.dumps(entry, ensure_ascii=False) for entry in reloaded.load())

    reloaded.clear()
    assert reloaded.load() == []


def test_cross_process_record_does_not_lose_entries(tmp_path):
    """三个真实进程并发 record：75 条一条都不能少（#196 的主回归）。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    barrier = tmp_path / "go"
    writers = 3
    per_writer = 25
    max_entries = writers * per_writer + 10  # 不触发截尾，缺一条都能看出来

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    procs = [
        subprocess.Popen(
            [
                sys.executable, str(HELPER), str(memory_path), f"w{index}",
                str(per_writer), str(max_entries), str(barrier),
            ],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for index in range(writers)
    ]
    barrier.write_text("go", encoding="utf-8")  # 三个写者已在自旋等它

    deadline = time.monotonic() + PROC_WAIT_SECONDS
    for proc in procs:
        remaining = deadline - time.monotonic()
        try:
            proc.wait(timeout=max(1.0, remaining))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            pytest.fail(f"写者进程超时未退出（{PROC_WAIT_SECONDS}s）")
    for proc in procs:
        assert proc.returncode == 0, proc.stderr.read().decode("utf-8", "replace")

    activities = {entry["activity"] for entry in _entries(memory_path)}
    expected = {f"act-w{i}-{n}" for i in range(writers) for n in range(per_writer)}
    assert len(activities) == writers * per_writer, (
        f"并发写丢了 {writers * per_writer - len(activities)} 条："
        f"缺失 {sorted(expected - activities)[:5]}"
    )
    assert activities == expected


def test_threaded_read_modify_write_is_serialized(tmp_path, monkeypatch):
    """两个写者都先读到同一份旧内容 → 修复前必丢一条（确定性红）。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    memory = ProactiveMemory(memory_path, max_entries=10)

    reads: list[str] = []
    reads_lock = threading.Lock()
    both_read = threading.Event()
    original_load = ProactiveMemory.load

    def load_with_barrier(self):
        entries = original_load(self)
        with reads_lock:
            reads.append(threading.current_thread().name)
            if len(reads) >= 2:
                both_read.set()
        both_read.wait(timeout=BARRIER_WAIT_SECONDS)
        return entries

    monkeypatch.setattr(ProactiveMemory, "load", load_with_barrier)

    threads = [
        threading.Thread(
            target=memory.record, args=(f"p{index}.exe", "标题不落盘", f"act{index}"),
            name=f"writer-{index}",
        )
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)

    activities = {entry["activity"] for entry in original_load(memory)}
    assert activities == {"act0", "act1"}


def test_concurrent_threads_do_not_lose_entries(tmp_path):
    """同进程四线程各写 10 条：40 条一条都不能少。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    memory = ProactiveMemory(memory_path, max_entries=100)
    threads = [
        threading.Thread(
            target=lambda index=index: [
                memory.record(f"t{index}.exe", "标题不落盘", f"act-{index}-{n}")
                for n in range(10)
            ]
        )
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)

    activities = {entry["activity"] for entry in memory.load()}
    assert activities == {f"act-{i}-{n}" for i in range(4) for n in range(10)}


def test_record_writes_through_fsync(tmp_path, monkeypatch):
    """落盘必须真的 fsync（#204）：写路径上没有 fsync 就没有崩溃安全。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    synced: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    ProactiveMemory(memory_path).record("Code.exe", "标题不落盘", "写代码")

    assert synced, "record() 没有把内容 fsync 到磁盘（#204）"


def test_temp_path_is_unique_per_live_thread(tmp_path):
    """tmp 名必须带 pid + 线程 id（#196）：固定名会被两个写者写串。"""
    memory = ProactiveMemory(tmp_path / "proactive_screen_memory.json")
    names: list[str] = []
    names_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def collect() -> None:
        barrier.wait(timeout=30)  # 四个线程同时存活，线程 id 才互不相同
        path = memory._temp_path()
        with names_lock:
            names.append(path.name)

    threads = [threading.Thread(target=collect) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(names) == 4
    assert len(set(names)) == 4, f"tmp 名不唯一：{names}"
    assert str(os.getpid()) in names[0], f"tmp 名未带 pid：{names[0]}"
    assert names[0] != memory.path.with_suffix(".json.tmp").name, "仍在用固定的 .json.tmp 名"


def test_replace_transient_conflict_is_retried(tmp_path, monkeypatch):
    """Windows 杀软/索引的瞬时占用（WinError 5）不该变成丢写。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    real_replace = os.replace
    attempts: list[str] = []

    def flaky_replace(src, dst):
        attempts.append(str(dst))
        if len(attempts) <= 2:
            raise PermissionError(13, "文件被短暂占用")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    ProactiveMemory(memory_path).record("Code.exe", "标题不落盘", "写代码")

    assert len(attempts) == 3, f"应重试两次后成功，实际尝试 {len(attempts)} 次"
    assert _entries(memory_path)[0]["activity"] == "写代码"


def test_write_failure_is_logged_and_previous_content_kept(tmp_path, monkeypatch, caplog):
    """写失败必须留痕，且不能破坏已落盘的旧内容、不能留下半成品 tmp（#196）。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    memory = ProactiveMemory(memory_path)
    memory.record("Code.exe", "标题不落盘", "写代码")

    def always_fail(src, dst):
        raise OSError(5, "磁盘不可写")

    monkeypatch.setattr(os, "replace", always_fail)
    with caplog.at_level(logging.WARNING, logger="pet.proactive_memory"):
        memory.record("chrome.exe", "标题不落盘", "看视频")

    assert any("写盘失败" in record.message for record in caplog.records), (
        f"写失败被静默吞掉了：{caplog.records}"
    )
    assert [entry["activity"] for entry in _entries(memory_path)] == ["写代码"]
    assert not list(tmp_path.glob("*.tmp*")), "失败路径必须清掉半成品 tmp"


def test_corrupt_memory_file_is_logged(tmp_path, caplog):
    """损坏回退空列表的语义不变，但必须留下日志（原先完全静默）。"""
    memory_path = tmp_path / "proactive_screen_memory.json"
    memory_path.write_text("{ 这不是 JSON", encoding="utf-8")
    memory = ProactiveMemory(memory_path)

    with caplog.at_level(logging.WARNING, logger="pet.proactive_memory"):
        assert memory.load() == []

    assert any("读取失败" in record.message for record in caplog.records), (
        f"损坏回退静默无日志：{caplog.records}"
    )


def test_class_docstring_does_not_claim_title_storage():
    """#206：类 docstring 不得声称记录窗口标题（实现从不落盘标题）。"""
    docstring = ProactiveMemory.__doc__ or ""
    assert "标题" not in docstring, "类 docstring 声称记录标题，与实现/隐私声明矛盾（#206）"
