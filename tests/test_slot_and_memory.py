# -*- coding: utf-8 -*-
"""槽位机制与个体记忆的全面测试。

覆盖 plan5 §7 的规范测试场景：
1. 真实子进程竞争同一临时配置根目录，依次获得 slot-0/1/2；指定槽竞争失败不降级，锁文件残留可复用。
2. 子进程持有 slot-1 后 exit 或被终止，新子进程重新加锁 slot-1，读取个体配置与 sessions，不删 lock 文件。
3. 真实子进程并发首次创建 slot 配置，最终 JSON 完整，PID 后缀 .tmp 不撞名。
4. 主配置变更后，新 slot 首次创建继承主配置（位置独立、自启仅主槽有效）；已有 slot 保持个体修改记忆。
5. 损坏配置唯一备份名，连续恢复不覆盖旧备份。
6. 旧 config.json 无槽位元数据仍作为 slot-0；旧 spawn 原子迁移到 slot-1/2，中断回滚与标记。
7. slot-0 被占用时自启失败不拿 slot-1。
8. 手动启动与菜单 spawn 顺序与 offset index 独立测试。
9. 位置避让与 spawn offset 独立生效测试。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pet.config import Config, APP_DIR_NAME
from pet import slot_manager as sm
from pet.chat.session_store import SessionStore
from pet.chat.models import ChatMessage, ChatSession


def _run_slot_worker_code(config_dir: Path, code: str, timeout: float = 10.0) -> subprocess.Popen:
    """启动纯 Python 子进程运行一段测试脚本。"""
    cmd = [
        sys.executable,
        "-c",
        f"import sys, os\n"
        f"sys.path.insert(0, {repr(str(Path(__file__).resolve().parents[1]))})\n"
        f"{code}",
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_slot_locks_sequential_competition_and_preferred_fail(tmp_path):
    """场景 1：三个真实子进程竞争同一临时配置根目录，依次获得 slot-0/1/2；指定槽竞争失败不降级，锁残留可复用。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 启动第一个子进程获取首个空闲槽（应当是 0）并保持持锁 5 秒
    worker1_code = f"""
from pet.slot_manager import acquire_pet_slot
import time
slot, handle = acquire_pet_slot({repr(str(config_dir))})
print(f"WORKER1:{{slot}}", flush=True)
time.sleep(4)
"""
    p1 = _run_slot_worker_code(config_dir, worker1_code)
    line1 = p1.stdout.readline().strip()
    assert line1 == "WORKER1:0"

    # 启动第二个子进程获取下一个空闲槽（应当是 1）
    worker2_code = f"""
from pet.slot_manager import acquire_pet_slot
import time
slot, handle = acquire_pet_slot({repr(str(config_dir))})
print(f"WORKER2:{{slot}}", flush=True)
time.sleep(4)
"""
    p2 = _run_slot_worker_code(config_dir, worker2_code)
    line2 = p2.stdout.readline().strip()
    assert line2 == "WORKER2:1"

    # 指定申请 slot-0，应当抛出 SlotLockError 失败退出，不能降级到其他槽位
    worker_fail_code = f"""
from pet.slot_manager import acquire_pet_slot, SlotLockError
try:
    slot, handle = acquire_pet_slot({repr(str(config_dir))}, preferred_slot=0)
    print(f"UNEXPECTED:{{slot}}", flush=True)
except SlotLockError:
    print("EXPECTED_LOCK_FAIL", flush=True)
"""
    pfail = _run_slot_worker_code(config_dir, worker_fail_code)
    line_fail = pfail.stdout.readline().strip()
    assert line_fail == "EXPECTED_LOCK_FAIL"
    pfail.wait()

    # 启动第三个子进程自动竞争，应当获得 slot-2
    worker3_code = f"""
from pet.slot_manager import acquire_pet_slot
slot, handle = acquire_pet_slot({repr(str(config_dir))})
print(f"WORKER3:{{slot}}", flush=True)
"""
    p3 = _run_slot_worker_code(config_dir, worker3_code)
    line3 = p3.stdout.readline().strip()
    assert line3 == "WORKER3:2"
    p3.wait()

    # 清理并等待 p1, p2
    p1.terminate()
    p2.terminate()
    p1.wait()
    p2.wait()
    time.sleep(0.1)

    # 确认锁文件残留但之后仍可成功复用 slot-0，且大小固定为 16 字节，PID 在头部
    lock0 = sm.get_slot_lock_path(config_dir, 0)
    assert lock0.exists()
    assert lock0.stat().st_size == 16
    assert lock0.read_bytes().strip() != b""
    slot, handle = sm.acquire_pet_slot(config_dir)
    assert slot == 0
    assert lock0.stat().st_size == 16
    handle.close()


def test_concurrent_first_lock_creation_is_not_truncated(tmp_path):
    """两个真实进程首次创建同一锁文件时，恰一方持锁且记录保持完整。

    对负载不敏感：持锁方不再固定 sleep 1 秒后释放（全量负载下后到的进程
    可能错过窗口、在锁释放后拿到锁，导致双双报 LOCKED）。改为持锁方一直
    持有锁，直到失败方落一个「已尝试」标记（最多等 60s），保证无论调度
    延迟多大，两进程的竞争窗口必然重叠、恰一方持锁。
    """
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    release = tmp_path / "lock-start"
    attempted = config_dir / "loser-attempted.flag"
    worker_code = f"""
from pathlib import Path
import time
from pet.slot_manager import acquire_pet_slot

config_dir = Path({str(config_dir)!r})
release = Path({str(release)!r})
attempted = Path({str(attempted)!r})
print('READY', flush=True)
while not release.exists():
    time.sleep(0.001)
try:
    slot, handle = acquire_pet_slot(config_dir, preferred_slot=0)
except Exception as exc:
    # 失败方：先落标记（持锁方据此确认竞争已发生），再报告退出
    attempted.write_text("1", encoding="ascii")
    print(type(exc).__name__, flush=True)
else:
    print(f"LOCKED:{{slot}}", flush=True)
    # 持锁等待对方确认尝试过（上限 60s），保证竞争窗口必然重叠
    deadline = time.monotonic() + 60
    while not attempted.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    handle.close()
"""
    p1 = _run_slot_worker_code(config_dir, worker_code)
    p2 = _run_slot_worker_code(config_dir, worker_code)
    assert p1.stdout.readline().strip() == "READY"
    assert p2.stdout.readline().strip() == "READY"
    release.write_text("go", encoding="ascii")
    results = sorted([p1.stdout.readline().strip(), p2.stdout.readline().strip()])
    assert results.count("LOCKED:0") == 1
    assert results.count("SlotLockError") == 1
    assert p1.wait(timeout=30) == 0
    assert p2.wait(timeout=30) == 0
    lock_file = sm.get_slot_lock_path(config_dir, 0)
    assert lock_file.stat().st_size == sm.PID_RECORD_LEN
    assert lock_file.read_bytes().strip() != b""


def test_public_acquire_is_safe_when_two_processes_observe_stale_zero_size(tmp_path):
    """强制两个进程都在初始化前读到 size=0，后到者不能重复追加 16 字节。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    sync_dir = tmp_path / "stale-size-sync"
    sync_dir.mkdir()
    initialized = sync_dir / "first-initialized"

    def worker_code(role: str) -> str:
        return f"""
from pathlib import Path
import time
from pet import slot_manager as sm
role = {role!r}
sync_dir = Path({str(sync_dir)!r})
initialized = Path({str(initialized)!r})
real_fstat = sm.os.fstat
def synchronized_fstat(fd):
    observed = real_fstat(fd)
    (sync_dir / f"ready-{{role}}").write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 5
    while len(list(sync_dir.glob("ready-*"))) < 2:
        if time.monotonic() >= deadline:
            raise TimeoutError("peer did not reach fstat barrier")
        time.sleep(0.001)
    if role == "second":
        while not initialized.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("first process did not initialize lock")
            time.sleep(0.001)
    return observed
sm.os.fstat = synchronized_fstat
try:
    slot, handle = sm.acquire_pet_slot({str(config_dir)!r}, preferred_slot=0)
except Exception as exc:
    print(type(exc).__name__, flush=True)
else:
    if role == "first":
        initialized.write_text("done", encoding="ascii")
    print(f"LOCKED:{{slot}}", flush=True)
    time.sleep(1)
"""

    first = _run_slot_worker_code(config_dir, worker_code("first"))
    second = _run_slot_worker_code(config_dir, worker_code("second"))
    assert first.stdout.readline().strip() == "LOCKED:0"
    assert second.stdout.readline().strip() == "SlotLockError"
    assert first.wait(timeout=5) == 0
    assert second.wait(timeout=5) == 0
    lock_file = sm.get_slot_lock_path(config_dir, 0)
    assert lock_file.stat().st_size == sm.PID_RECORD_LEN


def test_lock_initialization_is_idempotent_when_size_observation_is_stale(
    tmp_path, monkeypatch
):
    """并发打开者都观察到旧 size=0 时，初始化也不能重复追加记录。"""
    lock_file = sm.get_slot_lock_path(tmp_path / APP_DIR_NAME, 0)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    class StaleStat:
        st_size = 0

    monkeypatch.setattr(sm.os, "fstat", lambda _fd: StaleStat())
    first = sm._open_lock_file(lock_file)
    first.close()
    second = sm._open_lock_file(lock_file)
    second.close()

    assert lock_file.stat().st_size == sm.PID_RECORD_LEN


def test_lock_acquisition_repairs_an_oversized_pid_record(tmp_path):
    """旧竞态留下的 32 字节锁文件在下一次成功持锁后恢复为定长格式。"""
    lock_file = sm.get_slot_lock_path(tmp_path / APP_DIR_NAME, 0)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_bytes(b" " * (sm.PID_RECORD_LEN * 2))

    handle = sm.acquire_file_lock(lock_file)
    assert handle is not None
    try:
        assert os.fstat(handle.fileno()).st_size == sm.PID_RECORD_LEN
        handle.seek(0)
        assert handle.read(sm.PID_RECORD_LEN).startswith(str(os.getpid()).encode("ascii"))
    finally:
        sm.release_file_lock(handle)


def test_slot_reclaimed_after_process_killed_and_keeps_memory(tmp_path):
    """场景 2：子进程持有 slot-1 后被终止；新子进程重新加锁 slot-1，读取原个体配置和 sessions，且未删 lock 文件。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 先锁住 slot-0，让后续进程拿 slot-1
    slot0, handle0 = sm.acquire_pet_slot(config_dir, preferred_slot=0)

    # 启动子进程拿 slot-1 并写个体记忆
    worker_code = f"""
from pet.slot_manager import acquire_pet_slot
from pet.config import Config
from pet.chat.session_store import SessionStore
from pet.chat.models import ChatMessage
import time

slot, handle = acquire_pet_slot({repr(str(config_dir))})
cfg = Config(base={repr(str(tmp_path))}, instance_id=f"slot-{{slot}}")
cfg.set("rx", 0.77)
cfg.save()

store = SessionStore({repr(str(config_dir))}, instance_id=f"slot-{{slot}}")
s = store.create("shenshen", "openai-main", "prompt")
s.messages.append(ChatMessage("user", "hello-slot-1"))
store.save(s)
store.flush()  # 异步 writer 落盘后再 READY：主进程收到 READY 即 kill，等不起后台线程

print(f"READY:{{slot}}", flush=True)
time.sleep(10)
"""
    p = _run_slot_worker_code(config_dir, worker_code)
    assert p.stdout.readline().strip() == "READY:1"

    # 强制杀死子进程
    p.kill()
    p.wait()
    time.sleep(0.1)

    # 锁文件依然存在
    lock1 = sm.get_slot_lock_path(config_dir, 1)
    assert lock1.exists()

    # 新子进程重新申请 slot-1 并读取配置与会话
    slot1_again, handle1 = sm.acquire_pet_slot(config_dir, preferred_slot=1)
    assert slot1_again == 1
    cfg_again = Config(base=tmp_path, instance_id="slot-1")
    assert cfg_again.get("rx") == 0.77

    store_again = SessionStore(config_dir, instance_id="slot-1")
    sessions = store_again.list("shenshen")
    assert len(sessions) == 1
    assert sessions[0].messages[0].content == "hello-slot-1"

    handle0.close()
    handle1.close()


def test_concurrent_creation_and_save_pid_tmp(tmp_path):
    """场景 3：并发首次创建配置与 save() PID 后缀 tmp 文件不撞名。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 写入主配置
    master_cfg = Config(base=tmp_path)
    master_cfg.set("character", "dundun")
    master_cfg.save()

    # 启动 2 个真实子进程分别创建 slot-1 与 slot-2 并保存
    c1 = f"""
from pet.slot_manager import acquire_pet_slot
from pet.config import Config
slot, handle = acquire_pet_slot({repr(str(config_dir))}, preferred_slot=1)
cfg = Config(base={repr(str(tmp_path))}, instance_id="slot-1")
cfg.set("scale", 1.25)
for _ in range(5):
    cfg.save()
print("DONE1", flush=True)
"""
    c2 = f"""
from pet.slot_manager import acquire_pet_slot
from pet.config import Config
slot, handle = acquire_pet_slot({repr(str(config_dir))}, preferred_slot=2)
cfg = Config(base={repr(str(tmp_path))}, instance_id="slot-2")
cfg.set("scale", 1.50)
for _ in range(5):
    cfg.save()
print("DONE2", flush=True)
"""
    p1 = _run_slot_worker_code(config_dir, c1)
    p2 = _run_slot_worker_code(config_dir, c2)

    assert p1.stdout.readline().strip() == "DONE1"
    assert p2.stdout.readline().strip() == "DONE2"
    p1.wait()
    p2.wait()

    cfg1 = Config(base=tmp_path, instance_id="slot-1")
    cfg2 = Config(base=tmp_path, instance_id="slot-2")
    assert cfg1.get("scale") == 1.25
    assert cfg2.get("scale") == 1.50


def test_field_default_factory_and_individual_memory(tmp_path):
    """场景 4：新 slot 首次创建继承主配置（位置/自启除外），之后只保留个体记忆。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    master = Config(base=tmp_path)
    master.set("character", "shenshen")
    master.set("playback_speed", 1.5)
    master.set("click_sound_volume", 0.33)
    master.set("on_top", False)
    master.set("show_dock_icon", False)
    master.set("chat_follow_pet", True)
    master.set("rx", 0.1)
    master.set("ry", 0.2)
    master.set("autostart_wanted", True)
    master.save()

    # 新建 slot-1：未保存前直接读取，应继承主配置的非个体设置；
    # 位置/屏幕与开机自启不复制，避免叠位和副槽自启。
    slot1 = Config(base=tmp_path, instance_id="slot-1")
    assert slot1.get("character") == "shenshen"
    assert slot1.get("playback_speed") == 1.5
    assert slot1.get("click_sound_volume") == 0.33
    assert slot1.get("on_top") is False
    assert slot1.get("show_dock_icon") is False
    assert slot1.get("chat_follow_pet") is True
    assert slot1.get("rx") is None
    assert slot1.get("ry") is None
    assert slot1.get("autostart_wanted") is False

    # slot-1 修改自身属性并保存（个体记忆）
    slot1.set("character", "dundun")
    slot1.set("click_sound_volume", 0.55)
    slot1.save()

    # 修改主配置后，已有 slot-1 不受影响
    master.set("character", "master_new")
    master.set("click_sound_volume", 0.99)
    master.save()

    slot1_reload = Config(base=tmp_path, instance_id="slot-1")
    assert slot1_reload.get("character") == "dundun"
    assert slot1_reload.get("click_sound_volume") == 0.55

    # 新建 slot-2 在首次创建时继承主配置当时的值（最新主配置），之后各自独立
    slot2 = Config(base=tmp_path, instance_id="slot-2")
    assert slot2.get("character") == "master_new"
    assert slot2.get("click_sound_volume") == 0.99


def test_fresh_spawn_reseeds_existing_slot_config_from_main(tmp_path, monkeypatch):
    """显式“生小肥鱼”（DSH_PET_SPAWN_FRESH=1）即使复用旧 slot 配置也继承主配置。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    master = Config(base=tmp_path)
    master.set("character", "shenshen")
    master.set("playback_speed", 2.0)
    master.set("click_sound_volume", 0.8)
    master.save()

    old_slot = Config(base=tmp_path, instance_id="slot-1")
    old_slot.set("character", "dundun")
    old_slot.set("playback_speed", 0.5)
    old_slot.set("click_sound_volume", 0.2)
    old_slot.save()

    monkeypatch.setenv("DSH_PET_SPAWN_FRESH", "1")
    fresh_slot = Config(base=tmp_path, instance_id="slot-1")
    assert fresh_slot.get("character") == "shenshen"
    assert fresh_slot.get("playback_speed") == 2.0
    assert fresh_slot.get("click_sound_volume") == 0.8
    # 副槽化字段仍不复制主鱼位置/自启
    assert fresh_slot.get("rx") is None
    assert fresh_slot.get("autostart_wanted") is False


def test_normal_reopen_keeps_existing_slot_config(tmp_path, monkeypatch):
    """普通重启/复用 slot 未带 SPAWN_FRESH 时，已有个体配置不被主配置覆盖。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    master = Config(base=tmp_path)
    master.set("character", "shenshen")
    master.save()

    slot = Config(base=tmp_path, instance_id="slot-1")
    slot.set("character", "dundun")
    slot.save()

    monkeypatch.delenv("DSH_PET_SPAWN_FRESH", raising=False)
    reopened = Config(base=tmp_path, instance_id="slot-1")
    assert reopened.get("character") == "dundun"


def test_spawn_seed_respects_inherit_size_switch(tmp_path, monkeypatch):
    """生小肥鱼继承大小开关：开启保留主 scale，关闭改用 spawn_scale。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 开启继承：主鱼 1.0，新鱼也应为 1.0
    master = Config(base=tmp_path)
    master.set("scale", 1.0)
    master.set("spawn_inherit_size", True)
    master.set("spawn_scale", 0.5)
    master.set("spawn_inherit_dynamic_island", True)
    island = dict(master.get("dynamic_island"))
    island["enabled"] = True
    master.set("dynamic_island", island)
    master.save()
    monkeypatch.setenv("DSH_PET_SPAWN_FRESH", "1")
    slot_inherit = Config(base=tmp_path, instance_id="slot-1")
    assert slot_inherit.get("scale") == 1.0
    assert slot_inherit.get("dynamic_island", {}).get("enabled") is True

    # 关闭继承：主鱼仍是 1.0，但新鱼应使用 spawn_scale=0.5，且灵动岛不开启
    master.set("spawn_inherit_size", False)
    master.set("spawn_inherit_dynamic_island", False)
    master.save()
    slot_custom = Config(base=tmp_path, instance_id="slot-2")
    assert slot_custom.get("scale") == 0.5
    assert slot_custom.get("spawn_inherit_size") is False
    assert slot_custom.get("spawn_scale") == 0.5
    assert slot_custom.get("spawn_inherit_dynamic_island") is False
    assert slot_custom.get("dynamic_island", {}).get("enabled") is False


def test_corrupt_config_backup_unique_timestamp(tmp_path):
    """场景 6：损坏配置唯一备份名，连续恢复不覆盖旧备份。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    corrupt_cfg = config_dir / "config-slot-1.json"
    corrupt_cfg.write_text("{invalid-json", encoding="utf-8")

    b1 = sm.backup_corrupt_config(corrupt_cfg)
    assert b1 is not None and b1.exists()
    assert "corrupt-" in b1.name

    time.sleep(0.01)
    b2 = sm.backup_corrupt_config(corrupt_cfg)
    assert b2 is not None and b2.exists()
    assert b1 != b2


def test_migrate_legacy_spawns_atomic_and_rollback(tmp_path):
    """场景 7：旧 spawn 原子迁移到 slot-1/2，旧 config.json 仍为 slot-0，孤儿文件保留。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 创建旧 config.json（无槽位元数据）
    (config_dir / "config.json").write_text(json.dumps({"version": 4, "character": "master"}), encoding="utf-8")

    # 创建两个旧 spawn 配置文件和会话
    (config_dir / "config-spawn100x1.json").write_text(json.dumps({"character": "spawn1"}), encoding="utf-8")
    s1_dir = config_dir / "sessions-spawn100x1" / "shenshen"
    s1_dir.mkdir(parents=True, exist_ok=True)
    (s1_dir / "s1.json").write_text(json.dumps({"session_id": "s1"}), encoding="utf-8")

    # 人工给第二个 spawn 一个稍晚的 mtime
    spawn2_file = config_dir / "config-spawn200x1.json"
    spawn2_file.write_text(json.dumps({"character": "spawn2"}), encoding="utf-8")
    os.utime(spawn2_file, (time.time() + 10, time.time() + 10))

    # 执行迁移
    assert sm.migrate_legacy_spawns(config_dir) is True

    # 验证映射到 slot-1 和 slot-2
    cfg1 = json.loads((config_dir / "config-slot-1.json").read_text(encoding="utf-8"))
    assert cfg1.get("character") == "spawn1"
    assert (config_dir / "sessions-slot-1" / "shenshen" / "s1.json").exists()

    cfg2 = json.loads((config_dir / "config-slot-2.json").read_text(encoding="utf-8"))
    assert cfg2.get("character") == "spawn2"

    # 主配置不受影响
    master_cfg = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    assert master_cfg.get("character") == "master"

    # 迁移标记文件写入
    assert (config_dir / "migration-spawns.done").exists()


def test_lock_file_fixed_size_and_pid_at_head(tmp_path):
    """测试重复获取锁（不同进程/会话）后锁文件大小保持 16 字节不增长，PID 记录固定在头部。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    slot_id, h1 = sm.acquire_pet_slot(config_dir, preferred_slot=1)
    lock_file = sm.get_slot_lock_path(config_dir, 1)
    assert lock_file.exists()
    assert lock_file.stat().st_size == sm.PID_RECORD_LEN
    sm._unlock_file(h1)

    content1 = lock_file.read_bytes()
    assert content1.startswith(f"{os.getpid()}".encode("ascii"))

    # 再次获取同一个槽位锁，大小不变
    slot_id2, h2 = sm.acquire_pet_slot(config_dir, preferred_slot=1)
    assert lock_file.stat().st_size == sm.PID_RECORD_LEN
    sm._unlock_file(h2)

    content2 = lock_file.read_bytes()
    assert content2.startswith(f"{os.getpid()}".encode("ascii"))
    assert lock_file.stat().st_size == sm.PID_RECORD_LEN


def test_corrupt_config_backup_and_wiring_in_config_load(tmp_path):
    """测试 Config._load 对损坏的 slot 配置及主配置调用 backup_corrupt_config 备份而不静默覆盖。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 1. 槽位配置文件损坏
    slot1_cfg = config_dir / "config-slot-1.json"
    slot1_cfg.write_text("{bad-json-slot1", encoding="utf-8")

    cfg1 = Config(base=tmp_path, instance_id="slot-1")
    # 检查是否生成了备份文件
    backups1 = list(config_dir.glob("config-slot-1.json.corrupt-*"))
    assert len(backups1) == 1
    assert backups1[0].read_text(encoding="utf-8") == "{bad-json-slot1"
    # cfg1 正常回退到默认配置
    assert cfg1.get("character") is not None

    # 2. 主配置文件损坏
    master_cfg = config_dir / "config.json"
    master_cfg.write_text("{bad-json-master", encoding="utf-8")

    cfg_master = Config(base=tmp_path)
    backups_master = list(config_dir.glob("config.json.corrupt-*"))
    assert len(backups_master) == 1
    assert backups_master[0].read_text(encoding="utf-8") == "{bad-json-master"
    assert cfg_master.get("character") is not None


def test_migrate_legacy_spawns_rollback_on_sessions_move_failure(tmp_path, monkeypatch):
    """测试旧 spawn 迁移时，若 config 移动成功但 sessions 移动失败，完整回滚三方状态。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    old_cfg = config_dir / "config-spawn999x1.json"
    old_cfg.write_text(json.dumps({"character": "spawn_fail"}), encoding="utf-8")
    old_sessions = config_dir / "sessions-spawn999x1"
    old_sessions.mkdir(parents=True, exist_ok=True)
    (old_sessions / "session.json").write_text("{}", encoding="utf-8")

    # 模拟在 move staged_sessions -> target_sessions 时抛异常
    orig_move = shutil.move

    def mock_move(src, dst, **kwargs):
        if "sessions-slot-1" in str(dst):
            raise OSError("Injected sessions move failure")
        return orig_move(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "move", mock_move)

    result = sm.migrate_legacy_spawns(config_dir)
    assert result is False

    # 验证完整回滚：原文件存在，目标文件不存在
    assert old_cfg.exists()
    assert old_sessions.exists()
    assert (old_sessions / "session.json").exists()
    assert not (config_dir / "config-slot-1.json").exists()
    assert not (config_dir / "sessions-slot-1").exists()
    assert not (config_dir / ".migration_staging").exists()


def test_migrate_legacy_spawns_recovers_staged_remnants(tmp_path):
    """测试启动迁移时若存在非空 .migration_staging 残留，能够恢复完成或清理。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = config_dir / ".migration_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "config-slot-1.json").write_text(json.dumps({"character": "staged_pet"}), encoding="utf-8")

    assert sm.migrate_legacy_spawns(config_dir) is True
    # 验证已从 staging 恢复到目标位置
    assert (config_dir / "config-slot-1.json").exists()
    assert json.loads((config_dir / "config-slot-1.json").read_text(encoding="utf-8"))["character"] == "staged_pet"
    assert not staging_dir.exists()


def test_migration_staging_remnant_is_kept_when_target_slot_is_occupied(tmp_path):
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = config_dir / ".migration_staging"
    staging_dir.mkdir()
    staged = staging_dir / "config-slot-1.json"
    staged.write_text(json.dumps({"character": "staged"}), encoding="utf-8")
    _, handle = sm.acquire_pet_slot(config_dir, preferred_slot=1)
    try:
        assert sm.migrate_legacy_spawns(config_dir) is False
        assert staged.exists()
        assert staged.read_text(encoding="utf-8") == json.dumps({"character": "staged"})
    finally:
        sm._unlock_file(handle)


def test_migration_staging_remnant_is_kept_when_target_already_exists(tmp_path):
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "config-slot-1.json"
    target.write_text(json.dumps({"character": "current"}), encoding="utf-8")
    staging_dir = config_dir / ".migration_staging"
    staging_dir.mkdir()
    staged = staging_dir / "config-slot-1.json"
    staged.write_text(json.dumps({"character": "staged"}), encoding="utf-8")

    assert sm.migrate_legacy_spawns(config_dir) is False
    assert staged.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["character"] == "current"


def test_migrate_legacy_spawns_skips_occupied_target_slot(tmp_path):
    """测试迁移目标槽位若被占用（持有锁），跳过该槽位并尝试下一槽位，保留旧配置。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 锁住 slot-1
    slot1, h1 = sm.acquire_pet_slot(config_dir, preferred_slot=1)

    old_cfg = config_dir / "config-spawn100x1.json"
    old_cfg.write_text(json.dumps({"character": "spawn1"}), encoding="utf-8")

    assert sm.migrate_legacy_spawns(config_dir) is True

    # slot-1 被占用，应该迁移到 slot-2
    assert not (config_dir / "config-slot-1.json").exists()
    assert (config_dir / "config-slot-2.json").exists()
    assert json.loads((config_dir / "config-slot-2.json").read_text(encoding="utf-8"))["character"] == "spawn1"

    sm._unlock_file(h1)


def test_app_main_rejects_instance_arg():
    """测试 app.main 传入 --instance 参数时打印警告并返回 1 退出。"""
    from pet import app as app_mod

    ret = app_mod.main(["dsh-pet", "--instance", "pet2"])
    assert ret == 1


def test_app_main_validates_slot_arg():
    """测试 app.main 校验 --slot 参数范围（0~127）及非法值。"""
    from pet import app as app_mod

    # 负数
    assert app_mod.main(["dsh-pet", "--slot", "-1"]) == 1
    # 超大值
    assert app_mod.main(["dsh-pet", "--slot", "128"]) == 1
    # 非法字符串
    assert app_mod.main(["dsh-pet", "--slot", "abc"]) == 1
    # 缺少值
    assert app_mod.main(["dsh-pet", "--slot"]) == 1


def test_autostart_slot0_fail_does_not_degrade(tmp_path):
    """场景 8：开机自启入口指定 --slot 0，若被占用报错退出，不降级为 slot-1。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 占住 slot-0
    slot0, handle0 = sm.acquire_pet_slot(config_dir, preferred_slot=0)

    # 尝试指定申请 slot-0
    with pytest.raises(sm.SlotLockError):
        sm.acquire_pet_slot(config_dir, preferred_slot=0)

    handle0.close()
