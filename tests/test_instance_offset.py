# -*- coding: utf-8 -*-
"""多开实例位置避让（runtime 标记）+ 角色名别名的回归测试。"""
from __future__ import annotations

import json
import os

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import PetWindow

from test_window_pause import FakeLibrary


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_second_instance_avoids_live_overlap(app, tmp_path):
    """后启动的实例检测到存活实例占位后向左错开（含重复双击同一实例的场景）。"""
    cfg_a = Config(base=tmp_path)
    cfg_a.save()
    win_a = PetWindow(FakeLibrary(), cfg_a)
    rect_a = (win_a.x(), win_a.y(), win_a._w, win_a._h)
    # 模拟"另一个进程"的实例：用存活的父进程 pid 写标记，占位与 win_a 相同
    marker = cfg_a.dir / f'runtime-{os.getppid()}.json'
    marker.write_text(json.dumps(
        {'pid': os.getppid(), 'x': rect_a[0], 'y': rect_a[1], 'w': rect_a[2], 'h': rect_a[3]},
    ), encoding='utf-8')

    cfg_b = Config(base=tmp_path, instance_id='pet2')
    win_b = PetWindow(FakeLibrary(), cfg_b)
    try:
        # 触发了避让：向左错开（测试环境屏幕较小，一步就顶到左缘也算避让成功）
        assert win_b.x() < win_a.x()
    finally:
        win_a.close()
        win_b.close()
    app.processEvents()


def test_runtime_marker_written_and_stale_cleaned(app, tmp_path):
    """实例启动后写入 runtime 标记；死进程的标记被顺手清理。"""
    cfg = Config(base=tmp_path)
    cfg.save()
    stale = cfg.dir / 'runtime-99999999.json'  # 超出 Windows pid 上限，必死
    stale.write_text(json.dumps({'pid': 99999999, 'x': 0, 'y': 0, 'w': 100, 'h': 100}),
                     encoding='utf-8')
    win = PetWindow(FakeLibrary(), cfg)
    try:
        own = cfg.dir / f'runtime-{os.getpid()}.json'
        assert own.exists()
        assert not stale.exists()
    finally:
        win.close()
    app.processEvents()


def test_character_alias_roundtrip(tmp_path):
    """角色别名：设置 → 读取 → 空名恢复默认，且持久化到配置文件。"""
    cfg = Config(base=tmp_path)
    assert cfg.character_alias("shenshen") == ""

    cfg.set_character_alias("shenshen", "大肥鱼")
    assert cfg.character_alias("shenshen") == "大肥鱼"

    # 重新加载同一配置文件，别名仍在
    cfg2 = Config(base=tmp_path)
    assert cfg2.character_alias("shenshen") == "大肥鱼"

    # 空名 = 恢复默认
    cfg2.set_character_alias("shenshen", "")
    assert cfg2.character_alias("shenshen") == ""

    # 超长截断到 24 字符
    cfg2.set_character_alias("shenshen", "x" * 40)
    assert len(cfg2.character_alias("shenshen")) == 24
