# -*- coding: utf-8 -*-
"""帧驱动位移（防打滑）回归：窗口平移速度锁到步态整圈，位置由解码帧号推进。

覆盖：
- quantize_move：位移量化为整圈步幅（四舍五入 / 最少 1 圈 / 越界递减 /
  单圈仍越界时夹到 room）；
- move_position_at_frame：帧进度线性插值、夹 [0,1]、第 0 帧即出发
  （原前后各 2s 墙钟冻结已删除）；
- MovieLibrary.move_strides：move_strides.json sidecar 加载
  （只收数值项 / 忽略 _comment / 缺文件或坏文件 → {}）；
- _try_move：量化位移 + 新计划键（anim/loops/loops_done/frames_per_loop/
  total_frames），缺 sidecar 时回退 MOVE_STRIDE_DEFAULT_PX；
- _on_frame 帧驱动：逐帧位移、中间圈末续圈不推链、末圈末帧清计划走播完链；
- 预测式预热：多圈移动非末圈跳过、末圈恢复、旧 schema 计划不受影响。
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.movement import move_position_at_frame, quantize_move
from pet.window import PetWindow

MOVE = catalog.MOVES[0]  # 螃蟹走路

# ============================================================================
# quantize_move（纯函数）
# ============================================================================


def test_quantize_rounds_to_whole_loops():
    loops, dist, dur = quantize_move(150, 100, 1000, 2.0)
    assert (loops, dist, dur) == (2, 200.0, 4.0)  # round(1.5) = 2 圈


def test_quantize_minimum_one_loop():
    loops, dist, dur = quantize_move(10, 100, 1000, 2.0)
    assert (loops, dist, dur) == (1, 100.0, 2.0)  # round(0.1) → 0 → 最少 1 圈


def test_quantize_decrements_when_exceeding_room():
    # round(240/100)=2 圈 = 200px > room 150 → 递减到 1 圈
    loops, dist, dur = quantize_move(240, 100, 150, 2.0)
    assert (loops, dist, dur) == (1, 100.0, 2.0)


def test_quantize_single_loop_clamped_to_room():
    # 1 圈 = 120px 仍超 room 90：不能再减（最少 1 圈），位移夹到 room
    loops, dist, dur = quantize_move(60, 120, 90, 2.0)
    assert loops == 1 and dist == 90.0 and dur == 2.0


# ============================================================================
# move_position_at_frame（纯函数）
# ============================================================================

PLAN = {'start_x': 0, 'target_x': 300, 'start_y': 100, 'target_y': 160,
        'total_frames': 30}


def test_position_linear_in_frames_no_freeze():
    assert move_position_at_frame(PLAN, 0) == (0, 100)  # 第 0 帧即从起点出发
    assert move_position_at_frame(PLAN, 15) == (150.0, 130.0)
    assert move_position_at_frame(PLAN, 30) == (300.0, 160.0)


def test_position_clamped_to_plan():
    assert move_position_at_frame(PLAN, -5) == (0, 100)
    assert move_position_at_frame(PLAN, 999) == (300.0, 160.0)


# 圈内逐帧位移曲线：curve[i] = 播到源帧 i 时圈内累计进度（0..1 单调不减）。
# 静帧段曲线走平 → 窗口不动；动帧段线性 → 匀速。无 curve 键时回退线性插值。
CURVE_PLAN = {'start_x': 0, 'target_x': 200, 'start_y': 100, 'target_y': 100,
              'loops': 1, 'frames_per_loop': 10, 'total_frames': 10,
              'curve': [0.0, 0.0, 0.25, 0.5, 0.5, 0.5, 0.75, 1.0, 1.0, 1.0]}


def test_position_curve_pauses_on_still_frames():
    # 帧 3→4→5 曲线走平（0.5）：窗口必须原地停住，不得继续滑
    assert move_position_at_frame(CURVE_PLAN, 3) == (100.0, 100)
    assert move_position_at_frame(CURVE_PLAN, 4) == (100.0, 100)
    assert move_position_at_frame(CURVE_PLAN, 5) == (100.0, 100)
    # 帧 0 静止起步、末帧到达终点
    assert move_position_at_frame(CURVE_PLAN, 0) == (0, 100)
    assert move_position_at_frame(CURVE_PLAN, 10) == (200.0, 100)


def test_position_curve_multi_loop():
    plan = dict(CURVE_PLAN, loops=2, total_frames=20)
    # 第二圈帧 2：progress = (1 + 0.25) / 2
    assert move_position_at_frame(plan, 12)[0] == pytest.approx(125.0)
    assert move_position_at_frame(plan, 20)[0] == pytest.approx(200.0)


def test_position_curve_monotonic():
    prev = -1.0
    for f in range(0, 11):
        x = move_position_at_frame(CURVE_PLAN, f)[0]
        assert x >= prev
        prev = x


# ============================================================================
# MovieLibrary.move_strides sidecar
# ============================================================================


def _lib_on_dir(tmp_path):
    from pet.library import MovieLibrary

    lib = MovieLibrary.__new__(MovieLibrary)
    lib._asset_dir = tmp_path
    return lib


def test_move_strides_loaded_numeric_only(tmp_path):
    (tmp_path / 'move_strides.json').write_text(json.dumps(
        {'_comment': '备注字段必须被忽略', '螃蟹走路': 90, 'bad': 'x', 'flag': True},
        ensure_ascii=False), encoding='utf-8')
    assert _lib_on_dir(tmp_path)._load_move_strides() == {'螃蟹走路': 90.0}


def test_move_strides_missing_file_returns_empty(tmp_path):
    assert _lib_on_dir(tmp_path)._load_move_strides() == {}


def test_move_strides_unparseable_returns_empty(tmp_path):
    (tmp_path / 'move_strides.json').write_text('{oops', encoding='utf-8')
    assert _lib_on_dir(tmp_path)._load_move_strides() == {}


def test_move_strides_object_value_stride(tmp_path):
    """对象值 {'stride': N, 'curve': [...]} 的步幅同样进 move_strides。"""
    (tmp_path / 'move_strides.json').write_text(json.dumps(
        {'螃蟹走路': {'stride': 220, 'curve': [0.0, 0.5, 1.0]}, '漂浮踏步': 90},
        ensure_ascii=False), encoding='utf-8')
    assert _lib_on_dir(tmp_path)._load_move_strides() == {'螃蟹走路': 220.0, '漂浮踏步': 90.0}


# ============================================================================
# MovieLibrary.move_curves sidecar（圈内逐帧位移曲线）
# ============================================================================


def test_move_curves_loaded_from_object_values(tmp_path):
    (tmp_path / 'move_strides.json').write_text(json.dumps(
        {'螃蟹走路': {'stride': 220, 'curve': [0.0, 0.25, 0.5, 0.5, 1.0]},
         '漂浮踏步': 90,  # 纯数值项：无曲线
         '左转奔跑': {'stride': 240}},  # 无 curve 键：无曲线
        ensure_ascii=False), encoding='utf-8')
    lib = _lib_on_dir(tmp_path)
    assert lib._load_move_curves() == {'螃蟹走路': [0.0, 0.25, 0.5, 0.5, 1.0]}


@pytest.mark.parametrize('curve', [
    [0.5, 1.0],            # 不从 0 开始
    [0.0, 0.9],            # 不以 1 结束
    [0.0, 0.8, 0.5, 1.0],  # 非单调（回退）
    [1.0],                 # 太短
    [0.0, 'x', 1.0],       # 非数值
    'not-a-list',          # 不是列表
])
def test_move_curves_rejects_invalid(tmp_path, curve):
    (tmp_path / 'move_strides.json').write_text(json.dumps(
        {'螃蟹走路': {'stride': 220, 'curve': curve}},
        ensure_ascii=False), encoding='utf-8')
    assert _lib_on_dir(tmp_path)._load_move_curves() == {}


def test_move_curves_missing_file_returns_empty(tmp_path):
    assert _lib_on_dir(tmp_path)._load_move_curves() == {}


def test_shenshen_move_curves_cover_move_clips():
    """shenshen 角色包：每个移动动画都必须带逐帧位移曲线（动帧才动）。"""
    from pet.library import MovieLibrary

    lib = MovieLibrary(character_id='shenshen')
    for name in catalog.MOVES:
        curve = lib.move_curves.get(name)
        assert curve, f'{name} 缺位移曲线'
        assert curve[0] == 0.0 and curve[-1] == 1.0
        assert len(curve) == lib.frames(name), '曲线必须逐帧对齐素材'


def test_shenshen_move_strides_sidecar_covers_move_clips():
    from pet.library import MovieLibrary

    lib = MovieLibrary(character_id='shenshen')
    for name in catalog.MOVES:
        assert lib.move_strides.get(name, 0) > 0, f'{name} 缺步幅数据'
    assert '_comment' not in lib.move_strides


# ============================================================================
# 窗口层：_try_move 量化与帧驱动位移
# ============================================================================

NAMES = [catalog.IDLE, catalog.TURN, MOVE, catalog.CLICKS[0], catalog.DRAG, '写代码']
BODY = QRect(0, 0, 100, 100)


class FakeClip(QObject):
    """与 WebMClip 形状一致的假 clip（tests/test_collision_window.py 同款模式）。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, frames: int = 1, parent=None):
        super().__init__(parent)
        self._frames = frames
        self._running = False
        self.starts = 0
        self.stops = 0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False
        self.stops += 1

    def start(self):
        self._running = True
        self.starts += 1
        return True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        pass

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return self._frames

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self, move_frames: int = 1):
        self._clips = {n: FakeClip(frames=(move_frames if n == MOVE else 1))
                       for n in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()
        self.move_strides = {}
        self.move_curves = {}

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name]._frames

    def duration(self, name):
        return 1.0


class FakePrewarm:
    def __init__(self):
        self.calls = []

    def begin_anim(self, name):
        pass

    def consume(self, **kw):
        return None

    def on_frame(self, *args, **kw):
        self.calls.append((args, kw))


class _Screen:
    def __init__(self, width):
        self._rect = QRect(0, 0, width, 1080)

    def availableGeometry(self):
        return self._rect

    def devicePixelRatio(self):
        return 1.0


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, monkeypatch, lib, vx=400, width=1920):
    """真窗口 + 可控几何（与 tests/test_movement.py 同款：只替换几何取数）。"""
    cfg = Config(base=tmp_path)
    cfg.set('collision_enabled', False)
    cfg.set('edge_probe_enabled', False)
    win = PetWindow(lib, cfg)
    monkeypatch.setattr(win, '_screen_available', lambda: _Screen(width))
    monkeypatch.setattr(win, '_stable_body_local_rect', lambda: BODY)
    monkeypatch.setattr(win, '_virtual_pos', lambda: QPoint(vx, 100))
    monkeypatch.setattr(win, '_rebuild_frame', lambda: None)
    monkeypatch.setattr(win, 'update', lambda: None)
    return win


def _close(win, app):
    win.close()
    app.processEvents()


def _pin_rng(monkeypatch, distance=150):
    # 方向与纵向目标都用 random 模块：钉死后计划完全确定（恒向右）
    monkeypatch.setattr('random.randint', lambda a, b: distance)
    monkeypatch.setattr('random.choice', lambda seq: seq[-1])


def test_try_move_quantizes_distance_and_writes_plan_keys(app, tmp_path, monkeypatch):
    lib = FakeLibrary(move_frames=10)
    lib.move_strides = {MOVE: 100.0}
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        _pin_rng(monkeypatch, distance=150)
        assert win._try_move(MOVE) is True
        plan = win._move_plan
        stride = 100.0 * win.scale
        loops = max(1, round(150 / stride))
        assert plan['anim'] == MOVE
        assert plan['loops'] == loops and plan['loops_done'] == 0
        assert plan['frames_per_loop'] == 10
        assert plan['total_frames'] == loops * 10
        assert plan['duration'] == loops * 1.0
        # 位移量化为整圈步幅（不再直接用 randint 原值）
        assert plan['target_x'] - plan['start_x'] == pytest.approx(loops * stride, abs=1.5)
        assert win.facing == 'right'
        assert win._move_timer.isActive()
    finally:
        _close(win, app)


def test_try_move_falls_back_to_default_stride(app, tmp_path, monkeypatch):
    lib = FakeLibrary(move_frames=10)  # move_strides 为空 → 缺省步幅
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        _pin_rng(monkeypatch, distance=240)
        assert win._try_move(MOVE) is True
        stride = catalog.MOVE_STRIDE_DEFAULT_PX * win.scale
        assert win._move_plan['loops'] == max(1, round(240 / stride))
    finally:
        _close(win, app)


def test_try_move_attaches_move_curve(app, tmp_path, monkeypatch):
    """角色带有位移曲线时写进移动计划；无曲线时计划不带曲线（线性回退）。"""
    lib = FakeLibrary(move_frames=10)
    lib.move_strides = {MOVE: 100.0}
    lib.move_curves = {MOVE: [0.0, 0.5, 1.0]}
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        _pin_rng(monkeypatch, distance=150)
        assert win._try_move(MOVE) is True
        assert win._move_plan['curve'] == [0.0, 0.5, 1.0]
    finally:
        _close(win, app)


def test_frame_driven_position_and_loop_rollover(app, tmp_path, monkeypatch):
    """逐帧位移 + 圈边界：中间圈末续圈不推链，末圈末帧清计划走播完链。"""
    lib = FakeLibrary(move_frames=10)
    lib.move_strides = {MOVE: 100.0}
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        _pin_rng(monkeypatch, distance=144)  # stride=72 → 恰 2 圈
        monkeypatch.setattr(win, '_predict_prewarm', lambda *a: None)
        moves = []
        monkeypatch.setattr(win, '_move_window_towards',
                            lambda x, y, **kw: moves.append((x, y)))
        ended = []
        monkeypatch.setattr(win, '_on_anim_ended', lambda name: ended.append(name))
        assert win._try_move(MOVE) is True
        plan = win._move_plan
        assert plan['loops'] == 2 and plan['total_frames'] == 20
        clip = lib.movie(MOVE)
        starts0 = clip.starts
        span = plan['target_x'] - plan['start_x']
        # 第 0 帧即起点（无 lead 冻结）
        win._on_frame(MOVE, 0)
        assert moves[-1] == (plan['start_x'], plan['start_y'])
        # 第一圈中间帧：progress = 5/20
        win._on_frame(MOVE, 5)
        assert moves[-1][0] == pytest.approx(plan['start_x'] + span * 5 / 20)
        # 中间圈末帧：续圈——loops_done+1、不推链、不清计划、clip 重新 start
        win._on_frame(MOVE, 9)
        assert plan['loops_done'] == 1
        assert win._move_plan is plan
        assert ended == []
        assert clip.starts == starts0 + 1
        # 第二圈中间帧：frames_elapsed = 1*10 + 5 → progress = 15/20
        win._on_frame(MOVE, 5)
        assert moves[-1][0] == pytest.approx(plan['start_x'] + span * 15 / 20)
        # 末圈末帧：清计划（停表）+ 走正常播完链
        win._on_frame(MOVE, 9)
        assert win._move_plan is None
        assert not win._move_timer.isActive()
        assert ended == [MOVE]
    finally:
        _close(win, app)


def test_legacy_plan_uses_normal_end_path(app, tmp_path, monkeypatch):
    """旧 schema 计划（碰撞测试注入款）：帧驱动与续圈都不介入，末帧走原播完。"""
    lib = FakeLibrary(move_frames=10)
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        win._switch(MOVE)
        win._move_plan = {'start_x': 0, 'target_x': 20, 'start_y': 0,
                          'target_y': 0, 'duration': 1.0}
        monkeypatch.setattr(win, '_predict_prewarm', lambda *a: None)
        moves = []
        monkeypatch.setattr(win, '_move_window_towards',
                            lambda x, y, **kw: moves.append((x, y)))
        ended = []
        monkeypatch.setattr(win, '_on_anim_ended', lambda name: ended.append(name))
        win._on_frame(MOVE, 5)   # 非末帧：旧计划不驱动位移
        assert moves == []
        win._on_frame(MOVE, 9)   # 末帧：无 loops 键 → 原播完路径
        assert ended == [MOVE]
    finally:
        _close(win, app)


def test_prewarm_skipped_on_intermediate_loops_only(app, tmp_path, monkeypatch):
    lib = FakeLibrary(move_frames=10)
    lib.move_strides = {MOVE: 100.0}
    win = _make_win(tmp_path, monkeypatch, lib, vx=400)
    try:
        _pin_rng(monkeypatch, distance=144)
        assert win._try_move(MOVE) is True
        fake = FakePrewarm()
        win.predictive_prewarm = fake
        plan = win._move_plan
        assert plan['loops'] == 2
        # 非末圈：逐圈提前掷骰会重复重掷预测 → 跳过
        win._predict_prewarm(MOVE, 8)
        assert fake.calls == []
        # 末圈：与单圈行为一致，正常预热
        plan['loops_done'] = plan['loops'] - 1
        win._predict_prewarm(MOVE, 8)
        assert len(fake.calls) == 1
        # 旧 schema 计划（无 loops 键）：不受影响
        fake.calls.clear()
        win._move_plan = {'start_x': 0, 'target_x': 20, 'start_y': 0,
                          'target_y': 0, 'duration': 1.0}
        win._predict_prewarm(MOVE, 8)
        assert len(fake.calls) == 1
    finally:
        _close(win, app)


# ============================================================================
# 复审回归（REVIEW_GLM_BRANCH_2026-09-20）：朝向提交时机 / 末帧到位 /
# gap 池转向闸门
# ============================================================================


def test_position_linear_reaches_target_on_last_frame():
    # 帧号 0-based：末拍 frames_elapsed == total_frames-1，到位必须提交终点
    # （无 curve 角色此前停在离目标 ~stride/frames 处）
    assert move_position_at_frame(PLAN, PLAN['total_frames'] - 1) == (300.0, 160.0)


def test_try_move_rebuilds_first_frame_with_new_facing(app, tmp_path, monkeypatch):
    # 朝向翻转的移动：_switch 预渲染首帧后必须立即按新朝向重建，
    # 否则首帧镜像错误要挂到 frameChanged(0) 异步纠正
    lib = FakeLibrary(move_frames=10)
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        win.facing = 'left'
        seen = []
        monkeypatch.setattr(win, '_rebuild_frame', lambda: seen.append(win.facing))
        _pin_rng(monkeypatch, distance=150)
        assert win._try_move(MOVE) is True
        assert seen, '_switch 必须至少预渲染一次首帧'
        assert seen[-1] == 'right'
    finally:
        _close(win, app)


def test_try_move_switch_failure_keeps_facing(app, tmp_path, monkeypatch):
    # 切换被拒：朝向不翻转、不建移动计划（朝向只跟随实际发生的移动）
    lib = FakeLibrary(move_frames=10)
    win = _make_win(tmp_path, monkeypatch, lib)
    try:
        win.facing = 'left'
        monkeypatch.setattr(lib.movie(MOVE), 'start', lambda: False)
        _pin_rng(monkeypatch, distance=150)
        assert win._try_move(MOVE) is False
        assert win.facing == 'left'
        assert win._move_plan is None
    finally:
        _close(win, app)


def test_animation_gap_turn_respects_facing_gate(app, tmp_path, monkeypatch):
    # gap 池掷中转向同样走朝向闸门：无需纠正（中线滞回带内）降级为待机，
    # 朝向绝不由随机数翻转
    lib = FakeLibrary()
    win = _make_win(tmp_path, monkeypatch, lib, vx=960)  # 中线：inward_facing → None
    try:
        win.facing = 'left'
        _pin_rng(monkeypatch)  # random.choice → seq[-1]：pool=[IDLE, TURN] 掷中 TURN
        win._play_animation_gap_step()
        assert win.anim == catalog.IDLE
        assert win.facing == 'left'
    finally:
        _close(win, app)


def test_animation_gap_pool_excludes_dual_category_moves(app, tmp_path, monkeypatch):
    # 同名素材可同时进 idle/turn 与 move 池（catalog 支持的双分类包）：
    # gap 是待机氛围步，必须把移动素材滤出池——否则 _play_roll 走移动分支，
    # gap 步带来意外窗口位移（acts 为空时甚至动画链停摆）
    lib = FakeLibrary()
    win = _make_win(tmp_path, monkeypatch, lib, vx=960)
    try:
        win.idles = [catalog.IDLE, MOVE]  # 双分类：MOVE 同时在 idle 池
        win.turns = []  # 钉死掷中 MOVE：pool=[IDLE, MOVE]，choice → seq[-1]
        win.facing = 'left'
        _pin_rng(monkeypatch)
        win._play_animation_gap_step()
        assert win.anim == catalog.IDLE
        assert win._move_plan is None
        assert win.facing == 'left'
    finally:
        _close(win, app)
