# -*- coding: utf-8 -*-
"""一键退出子肥鱼：关闭 slot-N 进程并清理 runtime 标记；slot 数据保留，主 slot-0 不受影响。"""
from __future__ import annotations

import json

from pet import child_pet_cleanup


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
    monkeypatch.setattr(
        child_pet_cleanup,
        "_pid_alive",
        lambda pid: pid == 222,
    )
    monkeypatch.setattr(
        child_pet_cleanup,
        "_terminate_pet_process",
        lambda pid: terminated.append(pid),
    )

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == [222]
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
    monkeypatch.setattr(
        child_pet_cleanup,
        "_pid_alive",
        lambda pid: pid == 444,
    )
    monkeypatch.setattr(
        child_pet_cleanup,
        "_terminate_pet_process",
        lambda pid: terminated.append(pid),
    )

    result = child_pet_cleanup.clear_spawned_pets(root)

    assert result["killed_pids"] == [444]
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
    monkeypatch.setattr(
        child_pet_cleanup, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        child_pet_cleanup, "_terminate_pet_process",
        lambda pid: terminated.append(pid),
    )

    child_pet_cleanup.clear_spawned_pets(root)
    # 新旧两种命名都被找到并清理
    assert not legacy.exists()
    assert not v2.exists()
    assert terminated == [111, 222]

    # 幂等：再跑一遍无残留、不重复杀
    result = child_pet_cleanup.clear_spawned_pets(root)
    assert result["killed_pids"] == []
    assert terminated == [111, 222]
