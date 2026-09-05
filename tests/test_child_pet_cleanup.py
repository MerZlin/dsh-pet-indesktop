# -*- coding: utf-8 -*-
"""一键清除子肥鱼：关闭 slot-N 进程并删除 slot 数据，主 slot-0 不受影响。"""
from __future__ import annotations

import json

from pet import child_pet_cleanup


def test_clear_spawned_pets_removes_slot_data_and_markers(tmp_path, monkeypatch):
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
    assert not (root / "config-slot-1.json").exists()
    assert not (root / "config-slot-2.json").exists()
    assert not (root / "todo_items-slot-2.json").exists()
    assert not (root / "sessions-slot-1").exists()
    # 主数据必须保留
    assert (root / "config.json").exists()
    assert (root / "sessions").is_dir()
