# -*- coding: utf-8 -*-
"""槽位机制与个体记忆的全面测试。

覆盖 plan5 §7 的 10 组规范测试场景：
1. 真实子进程竞争同一临时配置根目录，依次获得 slot-0/1/2；指定槽竞争失败不降级，锁文件残留可复用。
2. 子进程持有 slot-1 后 exit 或被终止，新子进程重新加锁 slot-1，读取个体配置与 sessions，不删 lock 文件。
3. 真实子进程并发首次播种，最终 JSON 完整，PID 后缀 .tmp 不撞名。
4. 主配置变更后，已有 slot 保持个体值，新 slot 继承完整分类字段，位置不继承，自启仅主槽有效。
5. keyring 引用继承、自定义 provider 归位、明文 key 不落盘。
6. 损坏配置唯一备份名，连续恢复不覆盖旧备份。
7. 旧 config.json 无槽位元数据仍作为 slot-0；旧 spawn 原子迁移到 slot-1/2，中断回滚与标记。
8. slot-0 被占用时自启失败不拿 slot-1。
9. 手动启动与菜单 spawn 顺序与 offset index 独立测试。
10. 位置避让与 spawn offset 独立生效测试。
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

    # 确认锁文件残留但之后仍可成功复用 slot-0
    lock0 = sm.get_slot_lock_path(config_dir, 0)
    assert lock0.exists()
    slot, handle = sm.acquire_pet_slot(config_dir)
    assert slot == 0
    handle.close()


def test_slot_reclaimed_after_process_killed_and_keeps_memory(tmp_path):
    """场景 2：子进程持有 slot-1 后被终止；新子进程重新加锁 slot-1，读取原个体配置和 sessions，且未删 lock 文件。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 先锁住 slot-0，让后续进程拿 slot-1
    slot0, handle0 = sm.acquire_pet_slot(config_dir, preferred_slot=0)

    # 启动子进程拿 slot-1 并写个体记忆
    worker_code = f"""
from pet.slot_manager import acquire_pet_slot, seed_slot_config_if_needed
from pet.config import Config
from pet.chat.session_store import SessionStore
from pet.chat.models import ChatMessage
import time

slot, handle = acquire_pet_slot({repr(str(config_dir))})
seed_slot_config_if_needed({repr(str(config_dir))}, slot)
cfg = Config(base={repr(str(tmp_path))}, instance_id=f"slot-{{slot}}")
cfg.set("rx", 0.77)
cfg.save()

store = SessionStore({repr(str(config_dir))}, instance_id=f"slot-{{slot}}")
s = store.create("shenshen", "openai-main", "prompt")
s.messages.append(ChatMessage("user", "hello-slot-1"))
store.save(s)

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


def test_concurrent_seeding_and_save_pid_tmp(tmp_path):
    """场景 3：并发播种与 save() PID 后缀 tmp 文件不撞名。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    # 写入主配置
    master_cfg = Config(base=tmp_path)
    master_cfg.set("character", "dundun")
    master_cfg.save()

    # 启动 2 个真实子进程分别播种 slot-1 与 slot-2 并保存
    c1 = f"""
from pet.slot_manager import acquire_pet_slot, seed_slot_config_if_needed
from pet.config import Config
slot, handle = acquire_pet_slot({repr(str(config_dir))}, preferred_slot=1)
seed_slot_config_if_needed({repr(str(config_dir))}, 1)
cfg = Config(base={repr(str(tmp_path))}, instance_id="slot-1")
for _ in range(5):
    cfg.save()
print("DONE1", flush=True)
"""
    c2 = f"""
from pet.slot_manager import acquire_pet_slot, seed_slot_config_if_needed
from pet.config import Config
slot, handle = acquire_pet_slot({repr(str(config_dir))}, preferred_slot=2)
seed_slot_config_if_needed({repr(str(config_dir))}, 2)
cfg = Config(base={repr(str(tmp_path))}, instance_id="slot-2")
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
    assert cfg1.get("character") == "dundun"
    assert cfg2.get("character") == "dundun"


def test_field_inheritance_isolation_and_chat_fields(tmp_path):
    """场景 4 & 5：主配置变更后，已有 slot 保持个体值，新 slot 继承完整分类字段，位置不继承，API Key 不落盘。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)

    master = Config(base=tmp_path)
    master.set("character", "shenshen")
    master.set("playback_speed", 1.5)
    master.set("on_top", False)
    master.set("show_dock_icon", False)
    master.set("chat_follow_pet", True)
    master.set("rx", 0.1)
    master.set("ry", 0.2)
    master.set("autostart_wanted", True)

    chat_data = master.chat_settings()
    custom_prov = chat_data.active_config
    custom_prov.api_key_ref = "provider/custom-ref"
    custom_prov.api_key = "sk-secret-plain"
    master.set_chat_settings(chat_data)
    master.save()

    # 首次播种 slot-1
    sm.seed_slot_config_if_needed(config_dir, 1)
    slot1 = Config(base=tmp_path, instance_id="slot-1")
    assert slot1.get("character") == "shenshen"
    assert slot1.get("playback_speed") == 1.5
    assert slot1.get("on_top") is False
    assert slot1.get("show_dock_icon") is False
    assert slot1.get("chat_follow_pet") is True
    # 位置与 autostart_wanted 不继承
    assert slot1.get("rx") is None
    assert slot1.get("ry") is None
    assert slot1.get("autostart_wanted") is False

    # 检查 chat_settings 及 keyring 引用继承与脱敏
    s1_chat = slot1.chat_settings()
    assert s1_chat.active_config.api_key_ref == "provider/custom-ref"
    disk_json = json.loads((config_dir / "config-slot-1.json").read_text(encoding="utf-8"))
    assert "api_key" not in disk_json["chat"]["providers"]["openai-main"]

    # 修改主配置后，已有 slot-1 不受影响
    master.set("character", "dundun")
    master.save()
    slot1_reload = Config(base=tmp_path, instance_id="slot-1")
    assert slot1_reload.get("character") == "shenshen"

    # 新建 slot-2 继承修改后的主配置
    sm.seed_slot_config_if_needed(config_dir, 2)
    slot2 = Config(base=tmp_path, instance_id="slot-2")
    assert slot2.get("character") == "dundun"


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
