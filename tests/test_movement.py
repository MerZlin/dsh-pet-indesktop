# -*- coding: utf-8 -*-
"""移动驱动：纵向游走目标计算与朝向规则（目标先于朝向）。"""

import random

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.movement import wander_target_y
from pet.window import PetWindow

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
        'move': ['原地小憩沉眠', '螃蟹走路'],
        'idle': ['待机呼吸休闲'],
        'click': [],
        'turn': ['东张西望'],
        'random': ['写代码'],
    }
    names = [n for ns in folder_files.values() for n in ns]
    cats = build_categories(names, folder_files=folder_files)
    assert cats['moves'] == ['螃蟹走路']
    assert '原地小憩沉眠' in cats['acts']


def test_renamed_move_pair_stays_in_moves():
    """原「原地漂浮踏步/原地左转奔跑」去掉“原地”后进入移动池，不再出现在随机动作里。"""
    from pet.catalog import build_categories

    folder_files = {
        'move': ['螃蟹走路', '漂浮踏步', '左转奔跑'],
        'idle': ['待机呼吸休闲'],
        'click': [],
        'turn': ['东张西望'],
        'random': ['写代码'],
    }
    names = [n for ns in folder_files.values() for n in ns]
    cats = build_categories(names, folder_files=folder_files)
    assert {'螃蟹走路', '漂浮踏步', '左转奔跑'} == set(cats['moves'])
    assert '漂浮踏步' not in cats['acts']
    assert '左转奔跑' not in cats['acts']


def test_balance_animations_also_enter_random_acts():
    """余额动画既要能按档位触发，也要允许在随机动作池/菜单里播放。"""
    from pet.catalog import build_categories

    folder_files = {
        'idle': ['待机呼吸休闲'],
        'turn': ['东张西望'],
        'move': ['螃蟹走路'],
        'click': [],
        'random': ['写代码', '悠闲哼歌'],
        'events/balance': ['余额-钱袋满溢', '余额-分文不剩'],
    }
    names = [n for ns in folder_files.values() for n in ns]
    cats = build_categories(names, folder_files=folder_files)
    assert '余额-钱袋满溢' in cats['acts']
    assert '余额-分文不剩' in cats['acts']
    assert '写代码' in cats['acts']
    assert '悠闲哼歌' in cats['acts']


def test_text_clips_no_mirror_loaded():
    """text_clips.json 的 no_mirror 清单被素材库加载。"""
    from pet.library import MovieLibrary

    lib = MovieLibrary(character_id='shenshen')
    assert '是啊，吃什么' in lib.no_mirror
    assert '螃蟹走路' not in lib.no_mirror


# ============================================================================
# 朝向规则（窗口层）：朝向跟随移动目标与屏幕位置，而不是随机数
# ============================================================================
BODY = QRect(0, 0, 100, 100)


class _Screen:
    """可控工作区：只需宽度，高度固定给足，避免真实屏幕尺寸影响分支。"""

    def __init__(self, width):
        self._rect = QRect(0, 0, width, 1080)

    def availableGeometry(self):
        return self._rect

    def devicePixelRatio(self):
        return 1.0


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, monkeypatch, width=1920, vx=0, screen=True):
    """真窗口 + 可控几何（工作区宽度 / 虚拟窗口 x）。

    只替换窗口自身的几何取数（屏幕、身体框、虚拟位置）——朝向判定与移动计划
    仍走产品代码，`_play_roll`/`_try_move` 都是真实事件路径。
    """
    from tests.test_collision_window import FakeLibrary

    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("edge_probe_enabled", False)
    win = PetWindow(FakeLibrary(), cfg)
    monkeypatch.setattr(win, "_screen_available", lambda: _Screen(width) if screen else None)
    monkeypatch.setattr(win, "_stable_body_local_rect", lambda: BODY)
    monkeypatch.setattr(win, "_virtual_pos", lambda: QPoint(vx, 100))
    assert win.idles and win.turns, "FakeLibrary 应同时提供待机与转向素材"
    return win


def _close(win, app):
    win.close()
    app.processEvents()


# 1920 宽工作区 + MOVE_MARGIN 边距：可达界 [70, 1850]，中线 960，带宽 124.6。
# vx=100   → cx=150  偏离中线 → 需要朝右
# vx=960   → cx=1010 落在滞回带内 → 不判定内侧（None）
# vx=1820  → cx=1870 偏离中线 → 需要朝左，且右侧已无 MOVE_MIN_PX 空间


def test_idle_roll_turns_inward_when_facing_outward(app, tmp_path, monkeypatch):
    """朝外待机必须换成转向：待机不纠偏，只有转向会在播完翻转朝向。"""
    win = _make_win(tmp_path, monkeypatch, vx=100)
    try:
        win.facing = "left"  # 贴在左侧却朝左 = 朝外
        win._play_roll(catalog.IDLE)
        assert win.anim == catalog.TURN, "朝外待机必须替换为一次转向"
    finally:
        _close(win, app)


def test_idle_roll_stays_idle_when_already_facing_inward(app, tmp_path, monkeypatch):
    win = _make_win(tmp_path, monkeypatch, vx=100)
    try:
        win.facing = "right"  # 已朝内侧
        win._play_roll(catalog.IDLE)
        assert win.anim == catalog.IDLE
        assert win.facing == "right"
    finally:
        _close(win, app)


def test_random_turn_downgrades_to_idle_when_centered(app, tmp_path, monkeypatch):
    """滞回带内不纠正：随机转向降级为待机，朝向绝不无理由翻转。"""
    win = _make_win(tmp_path, monkeypatch, vx=960)
    try:
        win.facing = "left"
        win._play_roll(catalog.TURN)
        assert win.anim == catalog.IDLE, "中线附近的转向必须降级为待机"
        assert win.facing == "left", "降级路径不得改变朝向"
    finally:
        _close(win, app)


def test_random_turn_downgrades_when_already_facing_inward(app, tmp_path, monkeypatch):
    """偏离中线但已朝内：无需纠正，转向同样降级。"""
    win = _make_win(tmp_path, monkeypatch, vx=100)
    try:
        win.facing = "right"
        win._play_roll(catalog.TURN)
        assert win.anim == catalog.IDLE
        assert win.facing == "right"
    finally:
        _close(win, app)


def test_random_turn_still_plays_when_correction_needed(app, tmp_path, monkeypatch):
    """确实需要纠正时转向照常播放（播完由既有逻辑翻转朝向）。"""
    win = _make_win(tmp_path, monkeypatch, vx=100)
    try:
        win.facing = "left"
        win._play_roll(catalog.TURN)
        assert win.anim == catalog.TURN
    finally:
        _close(win, app)


def test_outward_idle_keeps_idle_during_edge_probe(app, tmp_path, monkeypatch):
    """边缘探头冻结朝向（_effects_skip_turn_facing）：此时换转向毫无意义。"""
    win = _make_win(tmp_path, monkeypatch, vx=100)
    try:
        monkeypatch.setattr(win, "_effects_probe_active", lambda: True)
        win.facing = "left"
        win._play_roll(catalog.IDLE)
        assert win.anim == catalog.IDLE
        assert win.facing == "left"
    finally:
        _close(win, app)


def test_try_move_walks_toward_reachable_side_not_current_facing(app, tmp_path, monkeypatch):
    """目标先于朝向：朝向那侧没空间时，走向另一侧并据此改写朝向。"""
    win = _make_win(tmp_path, monkeypatch, vx=1820)
    try:
        win.facing = "right"  # 朝右，但右侧只剩负空间
        assert win._try_move() is True, "左侧还有空间，必须建立移动计划"
        assert win.facing == "left", "朝向必须跟随实际移动方向"
        plan = win._move_plan
        assert plan is not None
        assert plan["target_x"] < plan["start_x"], "位移目标必须落在左侧"
    finally:
        _close(win, app)


def test_try_move_refuses_when_neither_side_reachable(app, tmp_path, monkeypatch):
    """窄工作区：两侧都够不到 MOVE_MIN_PX → 拒绝建立计划（边缘可达性闸门）。"""
    win = _make_win(tmp_path, monkeypatch, width=150, vx=40)
    try:
        assert win._try_move(catalog.MOVES[0]) is False
        assert win._move_plan is None
        assert not win._move_timer.isActive()
        assert win.anim == catalog.IDLE, "拒绝计划不得换动画"
    finally:
        _close(win, app)


def test_trigger_move_does_not_walk_in_place_when_no_room(app, tmp_path, monkeypatch):
    """手动移动无空间可走：什么都不做，不再原地播放走路姿态。"""
    win = _make_win(tmp_path, monkeypatch, width=150, vx=40)
    try:
        win._trigger_move(catalog.MOVES[0])
        assert win.anim == catalog.IDLE, "无空间时不得原地播放走路姿态"
        assert win._move_plan is None
        assert not win._move_timer.isActive()
    finally:
        _close(win, app)


def test_trigger_move_does_not_walk_in_place_without_screen(app, tmp_path, monkeypatch):
    """取不到屏幕（全部显示器移除）同样只是拒绝，不降级为原地走路。"""
    win = _make_win(tmp_path, monkeypatch, screen=False)
    try:
        assert win._try_move(catalog.MOVES[0]) is False
        win._trigger_move(catalog.MOVES[0])
        assert win.anim == catalog.IDLE
        assert win._move_plan is None
    finally:
        _close(win, app)
