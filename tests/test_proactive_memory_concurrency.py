# -*- coding: utf-8 -*-
"""ProactiveMemory 跨进程并发：读→改→写与 clear 必须跨进程互斥。

产品路径（不是理论场景）：默认「独立设置页」进程
（`python -m pet --settings` → `modern_settings_dialog.py:1174` 的
`ProactiveMemory(...).clear()`）与主进程后台主动识屏
（`proactive.py:631` 的 `self.memory.record(...)`）写的是同一个
`<config.dir>/proactive_screen_memory.json`。两条路径各自读→改→写时，
后写的会把前一条挤掉，"清空记忆"也可能被 in-flight 的旧快照复活。

本文件用**真子进程**验证（不是线程模拟）：锁必须覆盖读→改→写整体与 clear，
超时不得无锁写入，持锁进程异常结束时能恢复。
"""
from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile

from pet.proactive import memory as pm

REPO = str(Path(__file__).resolve().parent.parent)
WAIT = 60.0  # 子进程同步的宽预算（CI 慢机不赌时序）


def _child_env(target: Path, mode: str, release: Path | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "PM_TARGET": str(target),
        "PM_MODE": mode,
        "PM_RELEASE": str(release or ""),
        "PM_REPO": REPO,
        # 慢 runner 上 50 次串行化写（读→改→写 + fsync + 原子替换）的排队会远超
        # 生产默认的 1s 锁超时——测试用 30s（验证的是不丢写，不是超时行为本身）
        "PM_LOCK_TIMEOUT": "30000",
    })
    return env


# 子进程脚本：
#   hold  —— 持有跨进程锁 → 通知父进程 → 等放行 → **持锁期间**直接落一条记录
#            （等价于"另一个进程的 record 正处在读→改→写窗口里"）→ 释放锁
#   block —— 持有跨进程锁 → 通知父进程 → 一直持有（给父进程测超时）
#   die   —— 持有跨进程锁 → 通知父进程 → 睡死等被 kill（测崩溃恢复）
#   write —— 正常走产品 record（测真并发不丢写）
_CHILD = r"""
import json, os, sys, time
sys.path.insert(0, os.environ["PM_REPO"])
from PySide6.QtCore import QLockFile

target = os.environ["PM_TARGET"]
mode = os.environ["PM_MODE"]
release = os.environ["PM_RELEASE"]

from pet.proactive.memory import ProactiveMemory, lock_path_for

if mode == "write":
    m = ProactiveMemory(target, max_entries=500,
                        lock_timeout_ms=int(os.environ.get("PM_LOCK_TIMEOUT", "1000")))
    for i in range(25):
        m.record("p%d-%d.exe" % (os.getpid(), i), "t", "x")
    sys.exit(0)

lock = QLockFile(str(lock_path_for(target)))
if not lock.tryLock(5000):
    print("LOCK-FAIL", flush=True)
    sys.exit(3)
print("LOCKED", flush=True)

if mode == "block":
    time.sleep(120)
elif mode == "die":
    time.sleep(120)          # 等父进程 kill -9
elif mode == "hold":
    deadline = time.time() + 60
    while not os.path.exists(release) and time.time() < deadline:
        time.sleep(0.01)
    # 持锁期间直接落盘：模拟 in-flight 的读→改→写窗口
    path = os.environ["PM_TARGET"]
    payload = {"entries": [{"ts": 1.0, "process": "inflight.exe", "activity": "上网"}]}
    tmp = path + ".inflight.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)
    print("WROTE", flush=True)
    lock.unlock()
"""


def _spawn(target: Path, mode: str, release: Path | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        env=_child_env(target, mode, release),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _wait_line(proc: subprocess.Popen, token: str, timeout: float = WAIT) -> None:
    """阻塞读子进程的一行输出（事件同步，不 sleep 猜时序）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.strip() == token:
            return
    err = proc.stderr.read() if proc.stderr else ""
    raise AssertionError(f"子进程未输出 {token}（退出码 {proc.poll()}）stderr={err[:400]!r}")


def _entries(target: Path) -> list[dict]:
    if not target.is_file():
        return []
    return json.loads(target.read_text(encoding="utf-8"))["entries"]


# ---------------------------------------------------------------- 真并发不丢写
def test_two_processes_unique_records_all_kept(tmp_path):
    """两个真进程各写 25 条唯一记录，max_entries=500 → 50 条必须一条不少。

    无跨进程锁时这是 last-writer-wins：实测同一模式只留下 1~28 条。
    """
    target = tmp_path / "proactive_screen_memory.json"
    procs = [_spawn(target, "write") for _ in range(2)]
    outs = [p.communicate(timeout=WAIT) for p in procs]
    codes = [p.returncode for p in procs]
    assert codes == [0, 0], f"子进程异常退出 {codes} {[o[1][:300] for o in outs]}"

    names = sorted(e["process"] for e in _entries(target))
    assert len(names) == 50, f"跨进程丢写：实得 {len(names)}/50"
    assert len(set(names)) == 50, "记录被覆盖（非唯一）"


# ---------------------------------------------------------------- clear 必须等
def test_clear_waits_for_other_process_in_flight_write(tmp_path):
    """另一进程持锁（读→改→写窗口）时，clear() 必须等它写完再删。

    "等待"是**结构性**断言而不是时序赌博：clear 用 60s 预算去拿锁，而子进程
    在父进程放行前绝不释放锁，所以新实现下 clear 在放行前不可能完成；同时
    数据文件必须原样存在（clear 绝不能在锁外先删）。旧实现无锁：clear 立刻
    删完返回，随后子进程的写入把文件复活——"清了还在"。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 0.5, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    release = tmp_path / "release"
    child = _spawn(target, "hold", release)
    try:
        _wait_line(child, "LOCKED")

        done = threading.Event()
        errors: list[BaseException] = []

        def run_clear():
            try:
                pm.ProactiveMemory(target, lock_timeout_ms=60_000).clear()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                done.set()

        waiter = threading.Thread(target=run_clear, daemon=True)
        waiter.start()

        assert not done.wait(1.5), "clear 未等待 in-flight 的写入者就返回了"
        assert [e["process"] for e in _entries(target)] == ["seed.exe"], \
            "clear 不得在锁外删除（锁内完成才允许动文件）"

        release.write_text("go", encoding="utf-8")
        _wait_line(child, "WROTE")
        child.wait(timeout=WAIT)
        assert not errors, errors
        assert done.wait(WAIT), "放行后 clear 仍未返回"

        assert _entries(target) == [], "clear 返回后必须是最终空（不得被并发写入复活）"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)
        release.write_text("go", encoding="utf-8")


# ---------------------------------------------------------------- 超时不得写
def test_record_does_not_write_when_file_lock_times_out(tmp_path, caplog):
    """锁被别的进程占住且超时 → 告警并**放弃写入**，不得无锁写。"""
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": []}), encoding="utf-8")
    child = _spawn(target, "block")
    try:
        _wait_line(child, "LOCKED")
        with caplog.at_level(logging.WARNING):
            pm.ProactiveMemory(target, max_entries=20, lock_timeout_ms=200).record(
                "X.exe", "t", "上网")
        assert _entries(target) == [], "超时后不得落盘（无锁写会覆盖别人的记录）"
        assert "锁" in caplog.text, f"超时必须告警，实得 {caplog.text!r}"
    finally:
        child.kill()
        child.wait(timeout=30)


# ---------------------------------------------------------------- 崩溃恢复
def test_file_lock_released_when_holder_process_killed(tmp_path):
    """持锁进程被强杀后，锁必须能恢复（QLockFile 按 PID 判定陈旧）。"""
    target = tmp_path / "proactive_screen_memory.json"
    child = _spawn(target, "die")
    try:
        _wait_line(child, "LOCKED")
        child.kill()
        child.wait(timeout=30)

        pm.ProactiveMemory(target, lock_timeout_ms=5000).record("A.exe", "t", "上网")
        assert [e["process"] for e in _entries(target)] == ["A.exe"], \
            "持锁进程死后锁必须被回收，写入不得被永久卡住"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)


# ---------------------------------------------------------------- 锁实现约束
def test_lock_registry_does_not_grow(tmp_path):
    """锁注册表不得随"用过的路径数"无界增长（弱引用回收），活体条目不丢。

    断言用**增量**而不是绝对条数：注册表里本来就有别处仍活着的实例的条目
    （全量套件里更多），那部分不是本用例要管的；被释放的路径必须被回收。
    """
    before = len(pm._LOCKS)
    for i in range(200):
        pm.ProactiveMemory(tmp_path / f"m{i}.json")._lock  # noqa: B018
    gc.collect()
    grew = len(pm._LOCKS) - before
    assert grew <= 4, f"200 个已释放路径未回收：{before} -> {len(pm._LOCKS)}"

    holder = pm.ProactiveMemory(tmp_path / "keep.json")
    assert pm._LOCKS.get(pm._lock_key(holder.path)) is holder._lock, "活体的锁不得被回收"
    assert isinstance(holder._lock, type(threading.RLock()))


def test_lock_registry_is_weakly_held():
    """注册表持弱引用：最后一个使用者消失后条目必须释放。"""
    assert isinstance(pm._LOCKS, weakref.WeakValueDictionary)


def test_lock_key_is_normalized_against_path_aliases(tmp_path):
    """同一文件的不同写法（`..`/相对路径）必须映射到同一把锁与同一锁文件。"""
    real = tmp_path / "proactive_screen_memory.json"
    real.write_text(json.dumps({"entries": []}), encoding="utf-8")
    alias = tmp_path / "sub" / ".." / "proactive_screen_memory.json"

    a = pm.ProactiveMemory(real)
    b = pm.ProactiveMemory(alias)
    assert a._lock is b._lock, "路径别名必须共用同一把进程内锁"
    assert pm.lock_path_for(a.path) == pm.lock_path_for(b.path)


def test_lock_file_is_sidecar_and_never_replaced(tmp_path, monkeypatch):
    """锁文件是旁路文件；正常写入只替换数据文件，绝不触碰锁文件。"""
    target = tmp_path / "proactive_screen_memory.json"
    lock_file = pm.lock_path_for(target)
    assert lock_file != target and lock_file.name.endswith(".lock")

    seen: list[Path] = []
    real = pm.atomic_replace_with_retry

    def spy(temp, tgt, attempts=5):
        seen.append(Path(tgt))
        real(temp, tgt, attempts)

    monkeypatch.setattr(pm, "atomic_replace_with_retry", spy)
    pm.ProactiveMemory(target).record("A.exe", "t", "上网")

    assert seen and all(p != lock_file for p in seen), "替换目标绝不能是锁文件"
    assert seen == [target], f"只应替换数据文件，实得 {seen}"
    assert list(tmp_path.glob("*.tmp")) == [], "不得残留临时文件"
    assert not lock_file.exists(), "QLockFile 解锁时应删除自己的锁文件（不留残渣）"


def test_temp_file_is_unique_per_write(tmp_path, monkeypatch):
    """临时文件同目录且唯一（mkstemp O_EXCL），不靠 PID 名碰运气。"""
    target = tmp_path / "proactive_screen_memory.json"
    temps: list[Path] = []
    real = pm.atomic_replace_with_retry

    def spy(temp, tgt, attempts=5):
        temps.append(Path(temp))
        real(temp, tgt, attempts)

    monkeypatch.setattr(pm, "atomic_replace_with_retry", spy)
    mem = pm.ProactiveMemory(target)
    for i in range(5):
        mem.record(f"A{i}.exe", "t", "上网")

    assert len(temps) == 5
    assert len(set(temps)) == 5, f"临时文件重名：{temps}"
    assert all(p.parent == tmp_path and p.suffix == ".tmp" for p in temps)
    assert all(p != target and p != pm.lock_path_for(target) for p in temps)


@pytest.mark.parametrize("use_lock", [True, False])
def test_fsync_happens_before_replace(tmp_path, monkeypatch, use_lock):
    """fsync 必须早于原子替换（否则 rename 先落地、内容还在页缓存）。"""
    target = tmp_path / "proactive_screen_memory.json"
    order: list[str] = []
    real_replace = pm.atomic_replace_with_retry

    def spy_replace(temp, tgt, attempts=5):
        order.append("replace")
        real_replace(temp, tgt, attempts)

    real_fsync = os.fsync

    def spy_fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    monkeypatch.setattr(pm, "atomic_replace_with_retry", spy_replace)
    monkeypatch.setattr(pm.os, "fsync", spy_fsync)
    pm.ProactiveMemory(target).record("A.exe", "t", "上网")

    assert "fsync" in order and "replace" in order
    assert order.index("fsync") < order.index("replace"), order


def test_replace_failure_keeps_old_content_and_logs(tmp_path, monkeypatch, caplog):
    """替换失败：旧内容完整保留、不留临时文件、可观察（不静默吞）。"""
    target = tmp_path / "proactive_screen_memory.json"
    old = {"entries": [{"ts": 1.0, "process": "old.exe", "activity": "上网"}]}
    target.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    def deny(temp, tgt, attempts=5):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pm, "atomic_replace_with_retry", deny)
    with caplog.at_level(logging.WARNING):
        pm.ProactiveMemory(target).record("B.exe", "t", "上网")

    assert json.loads(target.read_text(encoding="utf-8")) == old
    assert list(tmp_path.glob("*.tmp")) == []
    assert "写入失败" in caplog.text, caplog.text


def test_clear_blocked_by_lock_does_not_resurrect(tmp_path):
    """同进程内 clear 与 record 也必须串行（跨实例），不得互相覆盖。"""
    target = tmp_path / "proactive_screen_memory.json"
    pm.ProactiveMemory(target).record("A.exe", "t", "上网")
    pm.ProactiveMemory(target).clear()
    assert pm.ProactiveMemory(target).load() == []


# ------------------------------------------------------------ clear 的返回值
def test_clear_reports_failure_when_lock_is_held(tmp_path, caplog):
    """拿不到跨进程锁时 clear 必须**如实返回 False**。

    产品路径：设置页（独立进程）按返回值提示用户。之前 clear 无返回值、
    调用方无条件弹"已清空"——别的进程正在写、这次清空根本没发生时，
    用户看到的是"清了还在"（本条把"失败"变成调用方看得见的事实）。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    child = _spawn(target, "block")
    try:
        _wait_line(child, "LOCKED")
        with caplog.at_level(logging.WARNING):
            cleared = pm.ProactiveMemory(target, lock_timeout_ms=200).clear()
        assert cleared is False, "锁超时（本次没清）必须返回 False"
        assert _entries(target) != [], "失败路径绝不能在锁外删文件"
        assert "锁" in caplog.text, f"失败必须留日志，实得 {caplog.text!r}"
    finally:
        child.kill()
        child.wait(timeout=30)


def test_clear_reports_success_and_is_idempotent(tmp_path):
    """成功路径返回 True；文件本来就不存在（= 没有记忆）同样算成功。"""
    target = tmp_path / "proactive_screen_memory.json"
    mem = pm.ProactiveMemory(target)

    assert mem.clear() is True, "没有记忆文件 = 空状态，不算失败"
    pm.ProactiveMemory(target).record("A.exe", "t", "上网")
    assert mem.clear() is True, "有记忆且清掉 → True"
    assert pm.ProactiveMemory(target).load() == []
    assert not pm.lock_path_for(target).exists(), "解锁后不应留下锁文件残渣"


# ------------------------------------------------- 读路径不得等跨进程锁（GUI 卡顿）
def _wait_wide_lock_held(mem: "pm.ProactiveMemory", timeout: float = WAIT) -> None:
    """事件同步：等到写者真的持住宽锁（= 已进入 QLockFile 等待），不 sleep 猜时序。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mem._lock.acquire(blocking=False):
            mem._lock.release()
            time.sleep(0.001)
            continue
        return
    raise AssertionError("写者未能在预算内进入读→改→写窗口")


def test_latest_does_not_wait_while_writer_waits_for_other_process_lock(tmp_path):
    """别的进程占着跨进程锁时，本进程的 record 会在锁上等；latest() 不得跟着一起等。

    latest() 的产品调用点是 GUI 主线程（``proactive.py::_on_frame_ready`` 主线程槽
    在识屏派发里调 ``self.memory.latest()``）。修复前 latest/load 拿的是 record 那把
    **宽锁**，而 record 持宽锁等 QLockFile——于是主线程被拖到 lock_timeout（产品
    预算 1000ms，本机实测 965ms）。修复后读只拿"本地 I/O 窄锁"（仅在真正的文件
    读写那一瞬间持有），跨进程锁的等待与读者无关。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    release = tmp_path / "release"
    child = _spawn(target, "hold", release)
    try:
        _wait_line(child, "LOCKED")

        mem = pm.ProactiveMemory(target, lock_timeout_ms=60_000)
        started = threading.Event()

        def slow_record():
            started.set()
            mem.record("W.exe", "", "上网")

        writer = threading.Thread(target=slow_record, daemon=True)
        writer.start()
        assert started.wait(WAIT), "写者线程未启动"
        _wait_wide_lock_held(mem)  # 此刻写者已持宽锁并在 QLockFile 上等

        done = threading.Event()
        read: dict = {}

        def read_once():
            read["entry"] = mem.latest()
            done.set()

        reader = threading.Thread(target=read_once, daemon=True)
        started_at = time.monotonic()
        reader.start()
        assert done.wait(2.5), \
            f"latest() 被写者的跨进程锁等住了（>{time.monotonic() - started_at:.1f}s）"
        assert read["entry"] is not None and read["entry"]["process"] == "seed.exe", \
            f"等待期间 latest() 必须返回盘上现有快照：{read['entry']!r}"

        release.write_text("go", encoding="utf-8")
        _wait_line(child, "WROTE")
        child.wait(timeout=WAIT)
        writer.join(WAIT)
        assert not writer.is_alive(), "放行后写者必须完成"
        assert "W.exe" in [e["process"] for e in _entries(target)], \
            "读者让路不代表写者丢写"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)
        release.write_text("go", encoding="utf-8")


def test_reads_stay_complete_under_concurrent_local_writes(tmp_path):
    """本地写者持续 record、读者持续 load 时：不得读到空/回退快照，也不得让写者丢写。

    实测（本机探针：2 写者 × 250 条 + 2 个"只去掉读锁"的读者）：Windows 上读句柄与
    os.replace 抢同一文件 → 1297 次替换里 890 次 WinError 5，重试预算耗尽后只落盘
    407/500 条，另有 275 次读返回空快照。所以读路径不是"直接不拿锁"就够：读与写共用
    一把只在本地 I/O 瞬间持有的窄锁（跨进程锁仍在宽锁里等），才能既不丢写也不读到空。
    """
    target = tmp_path / "proactive_screen_memory.json"
    # 锁超时放宽到 30s：两个写线程各 60 条在慢 runner 上的窄锁+重试排队会远超
    # 生产默认 1s（验证的是「不丢写/不读空」，不是超时行为本身）
    mem = pm.ProactiveMemory(target, max_entries=500, lock_timeout_ms=30_000)
    mem.record("seed.exe", "", "上网")  # 前置：文件已存在且非空

    stop = threading.Event()
    problems = {"empty": 0, "shrink": 0}
    seen = {"max": 0}

    def reader():
        while not stop.is_set():
            count = len(mem.load())
            if count == 0 and seen["max"]:
                problems["empty"] += 1
            if count < seen["max"]:
                problems["shrink"] += 1
            seen["max"] = max(seen["max"], count)

    def writer(tag):
        for i in range(60):
            mem.record(f"{tag}-{i}.exe", "", "上网")

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
    writers = [threading.Thread(target=writer, args=(f"w{i}",)) for i in range(2)]
    for thread in readers:
        thread.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(WAIT)
    stop.set()
    for thread in readers:
        thread.join(5)

    assert all(not t.is_alive() for t in writers), "并发 record 不得死锁/超时"
    assert problems == {"empty": 0, "shrink": 0}, f"读到了空/回退快照：{problems}"
    assert len(pm.ProactiveMemory(target).load()) == 121, "本地读不得让写者丢写（2×60+seed）"


# ------------------------------------------------------- clear：重试与边界
def test_clear_removes_file_while_a_reader_handle_is_open(tmp_path):
    """读者手柄开着时 clear 必须重试到删掉，而不是当场判失败。

    Windows 上 unlink 与"文件被打开"冲突（MSVCRT 共享模式不含 FILE_SHARE_DELETE）：
    修复前 clear 一次失败就返回 False，设置页随即弹"清空失败"——用户看到"没人写却
    清不掉"。POSIX 上 unlink 不受读句柄影响，本用例直接绿（不构成回归面），Windows
    上才真实覆盖重试路径。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")

    handle = open(target, "rb")
    try:
        result: dict = {}
        done = threading.Event()

        def run_clear():
            result["cleared"] = pm.ProactiveMemory(target, lock_timeout_ms=5000).clear()
            done.set()

        threading.Thread(target=run_clear, daemon=True).start()
        time.sleep(0.1)  # 让第一次 unlink 撞在打开的手柄上（刺激，不是时序猜测）
        handle.close()
        assert done.wait(WAIT), "clear 未在预算内结束"
        assert result["cleared"] is True, "手柄关掉后重试必须成功，不得误报失败"
        assert not target.exists(), "clear 报成功就必须真的删掉文件"
    finally:
        handle.close()


def test_unlink_retry_is_bounded_and_final_failure_returns_false(tmp_path, monkeypatch, caplog):
    """重试有界、最终失败必须返回 False，且绝不删别人的 .lock 文件。"""
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    lock_file = pm.lock_path_for(target)
    real_unlink = os.unlink
    seen: list[str] = []
    flaky = {"n": 0}

    def sometimes_denied(path, *args, **kwargs):
        seen.append(str(path))
        flaky["n"] += 1
        if flaky["n"] <= 2:
            raise PermissionError(13, "Access is denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", sometimes_denied)
    assert pm.ProactiveMemory(target, lock_timeout_ms=5000).clear() is True
    assert flaky["n"] == 3, f"瞬时失败应重试后成功，实得 {flaky['n']} 次"
    assert not target.exists()
    assert seen and all(Path(p) != lock_file for p in seen), "clear 绝不能删别人的锁文件"

    def always_denied(path, *args, **kwargs):
        seen.append(str(path))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "unlink", always_denied)
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert pm.ProactiveMemory(target, lock_timeout_ms=5000).clear() is False, \
            "重试耗尽必须如实返回 False（不得静默假装清空）"
    assert target.is_file() and _entries(target) != [], "失败路径不得动数据文件"
    assert "清空失败" in caplog.text, f"失败必须留可发现的日志：{caplog.text!r}"


def test_alias_path_does_not_create_extra_directories(tmp_path):
    """`sub/../x.json` 这类别名：数据、临时文件、锁文件、mkdir 必须都落在规范化后的
    同一份路径上——否则 mkdir 会把不存在的 `sub` 真建出来，配置目录里凭空多出空目录
    （别名写法越多，空目录越多）。"""
    real = tmp_path / "proactive_screen_memory.json"
    alias = tmp_path / "sub" / ".." / "proactive_screen_memory.json"

    mem = pm.ProactiveMemory(alias)
    mem.record("A.exe", "", "上网")

    assert not (tmp_path / "sub").exists(), "别名路径不得多建目录"
    assert mem.path == real, f"存储路径必须规范化到目标文件：{mem.path}"
    assert [e["process"] for e in pm.ProactiveMemory(real).load()] == ["A.exe"]
    assert list(tmp_path.glob("*.tmp")) == [], "不得残留临时文件"


# ------------------------------------------------- 读失败 ≠ 没有记忆（读改写链）
def _deny_target_read(target: Path, exc: OSError):
    """只让**目标文件**的读失败，其余路径照常（monkeypatch 用）。"""
    real_read_text = Path.read_text

    def denied(self, *args, **kwargs):
        if Path(self) == target:
            raise exc
        return real_read_text(self, *args, **kwargs)

    return denied


@pytest.mark.parametrize("kind", ["conflict", "io"])
def test_record_abandons_write_when_read_fails(tmp_path, monkeypatch, caplog, kind):
    """读取失败时 record 必须**放弃本次修改**，绝不用"空"冒充"没有"。

    "读不到"与"文件里没有条目"是两个事实。旧实现把读取失败降级成空列表，而
    `record()` 拿这个空列表做读→改→写——写下去的 payload 只含新条目，盘上的
    旧记忆被整体覆写（QLockFile 防的是别的进程，防不了自己丢数据）。
    纯读路径（load/latest）降级为空是 GUI 展示口径，可以接受；**降级结果绝不
    进读改写链**，这条由下面的逐字节断言钉住。

    kind=conflict：瞬时共享冲突（PermissionError），重试 _READ_ATTEMPTS 次仍败；
    kind=io：真 OSError（EIO），重试一万次也一样。
    """
    exc = (PermissionError(5, "Access is denied") if kind == "conflict"
           else OSError(5, "I/O error"))
    target = tmp_path / "proactive_screen_memory.json"
    old = {"entries": [
        {"ts": 3.0, "process": "old3.exe", "activity": "上网"},
        {"ts": 2.0, "process": "old2.exe", "activity": "办公"},
    ]}
    target.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    before = target.read_bytes()

    monkeypatch.setattr(Path, "read_text", _deny_target_read(target, exc))
    mem = pm.ProactiveMemory(target)
    with caplog.at_level(logging.WARNING):
        mem.record("NEW.exe", "", "上网")

    assert target.read_bytes() == before, "读失败时 record 不得覆写旧记忆（逐字节不变）"
    assert mem.load() == [], "纯读路径可以降级为空（展示口径），但降级结果不得参与写"
    assert "读取失败" in caplog.text, f"读失败必须留下可发现的日志：{caplog.text!r}"


def test_record_still_writes_when_file_is_absent_or_empty(tmp_path):
    """反向约束：文件不存在 / 合法空 entries 都不是"读失败"，record 必须照常写。"""
    target = tmp_path / "proactive_screen_memory.json"
    pm.ProactiveMemory(target).record("A.exe", "", "上网")
    assert [e["process"] for e in _entries(target)] == ["A.exe"]

    target.write_text(json.dumps({"entries": []}), encoding="utf-8")
    pm.ProactiveMemory(target).record("B.exe", "", "上网")
    assert [e["process"] for e in _entries(target)] == ["B.exe"]


# ------------------------------------------------- replace 退避不得占着本地窄锁
def test_latest_is_not_blocked_by_replace_backoff(tmp_path, monkeypatch):
    """replace 的退避睡眠必须发生在**本地 I/O 窄锁之外**。

    窄锁要保护的只是"读句柄不与本地替换重叠"（Windows 上会让 os.replace 报
    WinError 5）。退避期间没有任何文件操作，把锁一起睡掉就变成"读者被退避拖住"：
    latest() 的产品调用点在 GUI 主线程（`proactive.py:550`），实测被 20+40+80+160ms
    的退避按在 310ms 上。

    本用例同时钉住"不能拿旧快照盲写"：退避期间放行读者之后，写者仍必须基于
    **它读到的**快照落盘，且随后进入的同进程写者不得丢写。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    mem = pm.ProactiveMemory(target)

    real_replace = pm.os.replace
    calls = {"n": 0}
    in_backoff = threading.Event()

    def flaky_replace(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 4:  # 退避 20+40+80+160 ≈ 300ms
            in_backoff.set()
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(pm.os, "replace", flaky_replace)

    elapsed: dict[str, float] = {}
    first_done = threading.Event()

    def first_record():
        started_at = time.monotonic()
        mem.record("W.exe", "", "上网")
        elapsed["writer"] = time.monotonic() - started_at
        first_done.set()

    writer = threading.Thread(target=first_record, daemon=True)
    writer.start()
    assert in_backoff.wait(WAIT), "写者未进入 replace 退避"

    # 退避期间：读者必须能立刻拿到盘上现有快照
    second_done = threading.Event()
    second = threading.Thread(target=lambda: (mem.record("B.exe", "", "上网"),
                                              second_done.set()), daemon=True)
    second.start()

    read_at = time.monotonic()
    entry = mem.latest()
    elapsed["reader"] = time.monotonic() - read_at

    assert first_done.wait(WAIT) and second_done.wait(WAIT), "写者未在预算内完成"
    writer.join(5)
    second.join(5)

    assert elapsed["writer"] >= 0.25, \
        f"前置：退避必须真的发生（否则本用例不成立），实得 {elapsed['writer']:.3f}s"
    assert elapsed["reader"] < elapsed["writer"] / 2, \
        f"latest() 被 replace 退避拖住：{elapsed['reader']:.3f}s（写者 {elapsed['writer']:.3f}s）"
    assert entry is not None and entry["process"] == "seed.exe", \
        f"退避期间读者应看到盘上现有快照：{entry!r}"

    names = sorted(e["process"] for e in _entries(target))
    assert names == ["B.exe", "W.exe", "seed.exe"], \
        f"读者让路不得让写者丢写/盲写旧快照：{names}"
    assert calls["n"] == 6, \
        f"前 4 次是注入的退避，两个写者各替换一次，实得 {calls['n']}"


def test_latest_is_not_blocked_by_temp_fsync(tmp_path, monkeypatch):
    """落盘前的 fsync 不碰数据文件，不得占着本地 I/O 窄锁把读者一起按在磁盘上。

    窄锁存在的理由是"读句柄不与本地替换重叠"（Windows WinError 5）。写临时文件、
    fsync 临时文件都不碰数据文件，拿锁没有任何保护作用，只是把慢盘的时间转嫁给
    GUI 主线程的 latest()。用事件把写者钉在 fsync 里：读者能不能立刻返回，就是
    "锁到底圈住了哪一段"的直接证据（不靠睡多久来猜）。
    """
    target = tmp_path / "proactive_screen_memory.json"
    target.write_text(json.dumps({"entries": [
        {"ts": 1.0, "process": "seed.exe", "activity": "上网"}]}), encoding="utf-8")
    mem = pm.ProactiveMemory(target)

    real_fsync = pm.os.fsync
    in_fsync = threading.Event()
    release = threading.Event()

    def slow_fsync(fd):
        in_fsync.set()
        release.wait(WAIT)
        return real_fsync(fd)

    monkeypatch.setattr(pm.os, "fsync", slow_fsync)

    writer = threading.Thread(target=lambda: mem.record("W.exe", "", "上网"), daemon=True)
    writer.start()
    assert in_fsync.wait(WAIT), "写者未进入 fsync"

    read: dict = {}
    done = threading.Event()

    def read_once():
        read["entry"] = mem.latest()
        done.set()

    reader = threading.Thread(target=read_once, daemon=True)
    reader.start()
    try:
        assert done.wait(2.5), "latest() 被落盘前的 fsync 按住了（fsync 不该占窄锁）"
        assert read["entry"] is not None and read["entry"]["process"] == "seed.exe", \
            f"fsync 期间读者应看到盘上现有快照：{read['entry']!r}"
    finally:
        release.set()
    writer.join(WAIT)
    assert not writer.is_alive(), "放行后写者必须完成"
    assert [e["process"] for e in _entries(target)] == ["W.exe", "seed.exe"], "写入不得丢"


# ------------------------------------------- 探测失败 ≠ 没有记忆（stat 报错）
#: 存在性探测（`Path.is_file()` → `os.stat`）会**原样上抛**的 OS 错误。pathlib
#: 只吞 ENOENT/ENOTDIR/EBADF/ELOOP 这类"本来就没有"的错误（本机 CPython 3.13
#: 实测），权限 / 介质 / 共享冲突一律穿出来，所以探测本身也是一个会失败的调用。
_PROBE_ERRORS = (
    ("eacces", OSError(13, "Permission denied")),                 # EACCES
    ("eio", OSError(5, "I/O error")),                             # EIO
    ("winerror5", OSError(5, "Access is denied", None, 5)),       # ERROR_ACCESS_DENIED
    ("winerror32", OSError(32, "The process cannot access the file", None, 32)),
)


def _deny_target_stat(target: Path, exc: OSError):
    """只让**目标文件**的 stat 失败，其余路径照常（monkeypatch 用）。"""
    real_stat = Path.stat

    def denied(self, *args, **kwargs):
        if Path(self) == target:
            raise exc
        return real_stat(self, *args, **kwargs)

    return denied


@pytest.mark.parametrize("kind,exc", _PROBE_ERRORS, ids=[k for k, _ in _PROBE_ERRORS])
def test_probe_failure_is_read_failure_and_never_an_empty_memory(
        tmp_path, monkeypatch, caplog, kind, exc):
    """存在性探测失败必须并入"读失败"口径，不得上抛给调用方。

    产品路径：`proactive.py:550` 的 `self.memory.latest()` 在 GUI 主线程上，且
    调用它之前已经盖了频控章（`_worker_busy`）——探测抛错穿出去，这次主动识屏
    就死在构造记忆上下文那一步，后续视觉请求不再派发（用户看到的是"点了没反应"）。

    口径与 `_read_entries_strict` 的既有契约一致：
    - strict → None（"盘上有没有旧记忆"未知，不是"没有"）；
    - load/latest → 降级空（纯读是 GUI 展示口径，可以降级）；
    - record → 放弃本次修改（None 绝不进读改写链），旧数据逐字节不变。
    """
    target = tmp_path / "proactive_screen_memory.json"
    old = {"entries": [{"ts": 1.0, "process": "old.exe", "activity": "上网"}]}
    target.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    before = target.read_bytes()

    monkeypatch.setattr(Path, "stat", _deny_target_stat(target, exc))
    mem = pm.ProactiveMemory(target)

    with caplog.at_level(logging.WARNING):
        assert mem._read_entries_strict() is None, \
            "探测失败 = 读失败（None），不得冒充「盘上没有记忆」"
        assert mem.load() == [], "load 是展示口径：读失败降级为空"
        assert mem.latest() is None, "latest 必须可用（产品调用点在 GUI 主线程上）"
        mem.record("NEW.exe", "", "上网")

    assert target.read_bytes() == before, "探测失败时 record 不得覆写旧记忆（逐字节不变）"
    assert "探测失败" in caplog.text, f"探测失败必须留下可发现的日志：{caplog.text!r}"
    assert "不修改记忆" in caplog.text, "record 放弃修改同样要留痕"


# ------------------------------------------- 清空探测失败 ≠ 已经清空（clear 契约）
@pytest.mark.parametrize("kind,exc", _PROBE_ERRORS, ids=[k for k, _ in _PROBE_ERRORS])
def test_clear_reports_failure_when_probe_fails(tmp_path, monkeypatch, caplog, kind, exc):
    """清空入口的探测失败必须**如实返回 False**，不得降级成"已清空"、也不得上抛。

    `clear()` 的返回值是设置页唯一能分辨"清了"与"没清"的依据
    （`modern_settings_dialog._on_pro_clear_memory` 的 `cleared is False` 分支）。
    探测（`Path.is_file()` → `os.stat`）失败时"文件还在不在"是**未知**的：
    - 降级成 True = 又一次"报了已清空却还在"（记忆文件原样躺着，界面提示成功）；
    - 上抛 = 从设置页的命令处理函数直接穿出去，用户连失败提示都看不到。
    两条路都是静默失败，所以这里记 warning + 返回 False，交给设置页既有失败分支。
    """
    target = tmp_path / "proactive_screen_memory.json"
    old = {"entries": [{"ts": 1.0, "process": "old.exe", "activity": "上网"}]}
    target.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    before = target.read_bytes()

    monkeypatch.setattr(Path, "stat", _deny_target_stat(target, exc))
    mem = pm.ProactiveMemory(target)

    with caplog.at_level(logging.WARNING):
        assert mem.clear() is False, "探测失败 = 清空**未确认完成**，必须如实报失败"
        assert mem.load() == [], "纯读仍可降级（展示口径），与 clear 的失败口径不冲突"

    assert target.read_bytes() == before, "报失败时不得动过数据文件（逐字节不变）"
    assert "清空失败" in caplog.text, f"清空失败必须留下可发现的日志：{caplog.text!r}"
