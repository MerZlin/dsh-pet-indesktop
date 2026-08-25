# -*- coding: utf-8 -*-
"""移动驱动：纵向游走目标计算。"""

import random

from pet.window import wander_target_y

TOP, BOTTOM, H, MARGIN = 0.0, 1440.0, 300.0, 20.0


def test_returns_original_when_no_room():
    # 可用高度放不下窗口时退化为不动
    assert wander_target_y(100.0, 0.0, 250.0, 300.0, 20.0) == 100


def test_always_within_bounds():
    rnd = random.Random(42)
    for _ in range(500):
        y = wander_target_y(700.0, TOP, BOTTOM, H, MARGIN, rnd)
        assert TOP + MARGIN <= y <= BOTTOM - H - MARGIN


def test_actually_wanders_vertically():
    rnd = random.Random(7)
    ys = {wander_target_y(700.0, TOP, BOTTOM, H, MARGIN, rnd) for _ in range(500)}
    assert len(ys) > 50  # 不是恒定值
    assert min(ys) < 660 and max(ys) > 740  # 上下都能走到 ±40 之外


def test_clamped_near_edges():
    rnd = random.Random(1)
    # 贴着下边缘时不会越界到任务栏里
    for _ in range(300):
        y = wander_target_y(BOTTOM - H - MARGIN, TOP, BOTTOM, H, MARGIN, rnd)
        assert y <= BOTTOM - H - MARGIN


def test_inplace_move_clips_do_not_displace():
    """文件名含「原地」的移动素材不进 moves（位移池），降级到 acts。"""
    from pet.catalog import build_categories

    folder_files = {
        'move': ['原地左转奔跑', '螃蟹走路', '原地漂浮踏步'],
        'idle': ['待机呼吸休闲'],
        'click': [],
        'turn': ['东张西望'],
        'random': ['写代码'],
    }
    names = [n for ns in folder_files.values() for n in ns]
    cats = build_categories(names, folder_files=folder_files)
    assert cats['moves'] == ['螃蟹走路']
    assert '原地左转奔跑' in cats['acts'] and '原地漂浮踏步' in cats['acts']


def test_text_clips_no_mirror_loaded():
    """text_clips.json 的 no_mirror 清单被素材库加载。"""
    from pet.library import MovieLibrary

    lib = MovieLibrary(character_id='shenshen')
    assert '是啊，吃什么' in lib.no_mirror
    assert '螃蟹走路' not in lib.no_mirror
