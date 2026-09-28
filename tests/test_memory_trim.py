# -*- coding: utf-8 -*-
"""回收内存纯逻辑测试（离线：OS 出口注入假 backend）。

覆盖三条口径：太小不动 / 名单不动 / 自己与前台不动；统计聚合（trimmed / denied /
failed / freed）；文案在「没收拾成 / 没什么好收拾 / 收拾好了」三种输入下的差别。
"""
from __future__ import annotations

from pet import memory_trim as mt

MB = 1024 * 1024


class FakeBackend:
    def __init__(self, processes, *, avail=10 ** 9, open_ok=True, empty_ok=True,
                 after_delta=0):
        self._processes = list(processes)
        self.avail = avail
        self.open_ok = open_ok
        self.empty_ok = empty_ok
        self.after_delta = after_delta
        self.opened: list = []
        self.emptied: list = []
        self.closed = 0

    def list_processes(self):
        return list(self._processes)

    def open_process(self, pid):
        self.opened.append(pid)
        return f"handle-{pid}" if self.open_ok else None

    def empty_working_set(self, handle):
        self.emptied.append(handle)
        return self.empty_ok

    def close_handle(self, handle):
        self.closed += 1

    def memory_info(self, pid):
        for item_pid, _exe, size in self._processes:
            if item_pid == pid:
                return max(0, size - self.after_delta)
        return None

    def available_memory(self):
        return self.avail


def test_select_targets_skips_small_named_and_self():
    processes = [
        (1, "system", 900 * MB),
        (2, "chrome.exe", 300 * MB),
        (3, "tiny.exe", 5 * MB),
        (4, "memory compression", 500 * MB),
        (99, "self.exe", 400 * MB),
        ("bad", "x.exe", 100 * MB),
    ]
    targets, small, named = mt.select_targets(
        processes, skip_pids={99}, min_bytes=20 * MB,
    )
    assert targets == [(2, 300 * MB)]
    assert small == 1       # tiny.exe
    assert named == 2       # system + memory compression


def test_clean_min_mb_clamps_and_falls_back():
    assert mt.clean_min_mb(50) == 50
    assert mt.clean_min_mb("30") == 30
    assert mt.clean_min_mb(None) == mt.MEM_TRIM_MIN_MB
    assert mt.clean_min_mb("abc") == mt.MEM_TRIM_MIN_MB
    assert mt.clean_min_mb(-5) == 0
    assert mt.clean_min_mb(10 ** 6) == 1024


def test_human_mb_formats():
    assert mt.human_mb(0) == "0 MB"
    assert mt.human_mb(512 * 1024) == "512 KB"
    assert mt.human_mb(251 * MB) == "251 MB"
    assert mt.human_mb(2.6 * 1024 ** 3) == "2.6 GB"
    assert mt.human_mb("bad") == "0 MB"


def test_trim_working_sets_counts_and_freed():
    backend = FakeBackend([
        (2, "chrome.exe", 300 * MB),
        (3, "game.exe", 900 * MB),
        (4, "tiny.exe", 5 * MB),
    ], after_delta=100 * MB)
    got = mt.trim_working_sets(min_bytes=20 * MB, backend=backend)
    assert got["trimmed"] == 2
    assert got["skipped_small"] == 1
    assert got["freed"] == 200 * MB            # 两个目标各降 100 MB
    assert got["denied"] == 0 and got["failed"] == 0
    assert backend.closed == 2                 # 句柄都关掉了


def test_trim_working_sets_reports_denied_and_failed():
    denied = mt.trim_working_sets(
        min_bytes=0, backend=FakeBackend([(2, "a.exe", 100 * MB)], open_ok=False),
    )
    assert denied["denied"] == 1 and denied["trimmed"] == 0
    failed = mt.trim_working_sets(
        min_bytes=0, backend=FakeBackend([(2, "a.exe", 100 * MB)], empty_ok=False),
    )
    assert failed["failed"] == 1 and failed["trimmed"] == 0


def test_trim_working_sets_survives_backend_error():
    class Boom(FakeBackend):
        def list_processes(self):
            raise OSError("nope")

    got = mt.trim_working_sets(min_bytes=0, backend=Boom([]))
    assert got["error"] and got["trimmed"] == 0
    assert mt.summarize(got) == "没收拾成，等会儿再试试"


def test_summarize_three_branches():
    assert mt.summarize({"error": "x"}) == "没收拾成，等会儿再试试"
    assert mt.summarize({"trimmed": 0}) == "这会儿干干净净的，没什么好收拾"
    assert mt.summarize({
        "trimmed": 3, "freed": 300 * MB, "avail_before": 0, "avail_after": 0,
    }) == "收拾好啦：3 个程序腾出 300 MB"
    line = mt.summarize({
        "trimmed": 3, "freed": 300 * MB,
        "avail_before": 0, "avail_after": 200 * MB,
    })
    assert "可用内存多出 200 MB" in line
