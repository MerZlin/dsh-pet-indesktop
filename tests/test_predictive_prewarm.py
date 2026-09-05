# -*- coding: utf-8 -*-
"""批10-A1：预测式接力预热（帧驱动提前掷骰 + 后台预解码首帧）回归测试。

覆盖 GLM 方案卡 A-1 验证方法 ① 的机器可判部分（对应派发稿 §⑤.1 a-f）：
a) 动画临近结束 → 预测已掷且首帧预热被触发；
b) _pick_next 消费到预测名（hit）；
c) 交互打断后消费走现场掷骰（miss_invalid）；
d) gap=0 与 gap>0 两路径各一条；
e) 节流 divisor>1 时 lead 触发时机按修正公式；
f) 隐藏期间预测作废。

另含控制器单测（roll_next 分布 / on_frame 触发时机 / consume 校验）、
config 键归一化、library.warm_predicted 三重闸门薄方法。

全部用假时钟/假 clip/可控 random 驱动，不起真实 ffmpeg，无平台限定。
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import pet.library as library_mod
from pet import catalog
from pet.config import Config
from pet.predictive_prewarm import PredictivePrewarm, roll_next
from pet.window import PetWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


# ============================================================================
# 窗口级假库 / 假 clip（与 test_idle_low_fps / test_window_pause 同类，无 ffmpeg）
# ============================================================================
class FakeClip(QObject):
    """与 WebMClip 接口兼容的假播放器：可配帧数与 fps，记录预热/启停。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, name: str, frame_count: int = 10, fps: float = 10.0):
        super().__init__()
        self.name = name
        self.frame_count = max(1, frame_count)
        self.fps = max(0.1, float(fps))
        self.speed = 1.0
        self.decode_throttle_divisor = 1
        self.warm_calls = 0
        self.stop_count = 0
        self.start_count = 0
        self.recycle_minutes_calls: list = []
        # 批12 A1：显示槽清理（窗口 _switch 切走时调用，窗口级测试用）
        self.clear_display_calls = 0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def clear_display_frame(self):
        self.clear_display_calls += 1

    def stop(self):
        self.stop_count += 1

    def start(self):
        self.start_count += 1
        return True

    def jumpToFrame(self, frame_index):
        return int(frame_index) <= 0

    def set_playback_speed(self, speed):
        self.speed = max(0.1, float(speed))

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return self.frame_count

    def duration(self):
        # 与 WebMClip 一致：duration 参与 fps 换算，fps = frames / duration
        return self.frame_count / self.fps / self.speed

    def currentTimeSeconds(self):
        return 0.0

    def warm_first_frame(self):
        self.warm_calls += 1

    def set_decode_throttle(self, divisor):
        self.decode_throttle_divisor = max(1, int(divisor))

    # 批11-B1 复审 P2-1：窗口→clip 的回收阈值推送回归用（记录调用值）。
    def set_recycle_minutes(self, minutes):
        self.recycle_minutes_calls.append(minutes)


class FakeLibrary:
    """只含核心动画名的假素材库：window 构建分类 + 记录预测预热调用。"""

    def __init__(self, frame_count: int = 10, fps: float = 10.0):
        self.no_mirror = set()
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self._frame_count = frame_count
        self._fps = fps
        self.warmed: list[str] = []
        self._clips = {}
        for n in self._names():
            self._clips[n] = FakeClip(n, frame_count=frame_count, fps=fps)

    @staticmethod
    def _names() -> list[str]:
        return [
            catalog.IDLE,
            catalog.TURN,
            catalog.MOVES[0],
            catalog.CLICKS[0],
            catalog.DRAG,
            "写代码",
            "吃白饭",
        ]

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name].frameCount()

    def duration(self, name):
        return self._clips[name].duration()

    def warm_predicted(self, name):
        self.warmed.append(name)
        clip = self._clips[name]
        clip.warm_first_frame()


def _make_window(tmp_path, **cfg_overrides):
    cfg = Config(base=tmp_path)
    for k, v in cfg_overrides.items():
        cfg.set(k, v)
    return PetWindow(FakeLibrary(), cfg)


def _set_random(monkeypatch, val: float):
    """固定随机分支（val ∈ [0,1)）；random.choice 固定取池首元素，保证确定性。"""
    import random as _random
    monkeypatch.setattr(_random, "random", lambda: val)
    monkeypatch.setattr(_random, "choice", lambda lst: lst[0])


# ============================================================================
# 纯函数 roll_next：分布语义与 exclude 处理
# ============================================================================
class TestRollNext:
    POOLS = {"idles": [catalog.IDLE, "待机B"], "turns": [catalog.TURN],
             "acts": ["写代码", "吃白饭"], "moves": [catalog.MOVES[0], "漂浮踏步"]}

    def test_acts_branch(self, monkeypatch):
        # roll=0.5 → <0.80 动作池
        import random as _random
        monkeypatch.setattr(_random, "random", lambda: 0.5)
        monkeypatch.setattr(_random, "choice", lambda lst: lst[0])
        assert roll_next(self.POOLS) == "写代码"

    def test_idle_branch(self, monkeypatch):
        # roll=0.1 → <0.30 待机
        import random as _random
        monkeypatch.setattr(_random, "random", lambda: 0.1)
        monkeypatch.setattr(_random, "choice", lambda lst: lst[0])
        assert roll_next(self.POOLS) == catalog.IDLE

    def test_turn_branch(self, monkeypatch):
        # roll=0.35 → <0.40 转向
        import random as _random
        monkeypatch.setattr(_random, "random", lambda: 0.35)
        monkeypatch.setattr(_random, "choice", lambda lst: lst[0])
        assert roll_next(self.POOLS) == catalog.TURN

    def test_move_branch_no_exclude(self, monkeypatch):
        # roll=0.9 → >=0.80 移动；move 分支与 _try_move 一致不应用 exclude
        import random as _random
        monkeypatch.setattr(_random, "random", lambda: 0.9)
        monkeypatch.setattr(_random, "choice", lambda lst: lst[0])
        assert roll_next(self.POOLS, exclude=catalog.MOVES[0]) == catalog.MOVES[0]

    def test_exclude_applied_for_acts(self, monkeypatch):
        import random as _random
        monkeypatch.setattr(_random, "random", lambda: 0.5)
        used = []

        def choice(lst):
            used.append(tuple(lst))
            return lst[0]

        monkeypatch.setattr(_random, "choice", choice)
        roll_next(self.POOLS, exclude="写代码")
        assert ("吃白饭",) in used  # exclude '写代码' 被排除在 acts 池外

    def test_empty_none(self):
        assert roll_next({"idles": [], "turns": [], "acts": [], "moves": []}) is None


# ============================================================================
# 控制器单测：on_frame 触发时机（含 divisor 修正）/ consume 校验
# ============================================================================
def _roll_acts(exclude):
    return "吃白饭"


def _warm_ok(name):
    _warm_ok.names.append(name)


_warm_ok.names = []


def _pred(name):
    return name in ("吃白饭", catalog.MOVES[0])


class TestPredictivePrewarmController:
    def _make(self):
        return PredictivePrewarm(roll=_roll_acts, warm=_warm_ok, should_predict=_pred)

    def test_on_frame_predicts_within_lead(self, monkeypatch):
        pp = self._make()
        # frames=10, fps=10, divisor=1 → wall=(9-n)/10；n=8 → 0.1s ≤ 0.35
        assert pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35, exclude="写代码") is True
        assert pp.prediction == {"name": "吃白饭", "context_anim": "写代码", "gen": 0}
        assert pp.counts["made"] == 1
        assert _warm_ok.names == ["吃白饭"]

    def test_on_frame_respects_lead_window(self, monkeypatch):
        pp = self._make()
        # n=5 → wall=(9-5)/10=0.4s > 0.35 → 不预测
        assert pp.on_frame("写代码", 5, 10, 10.0, 1, 0.35, exclude="写代码") is False
        assert pp.prediction is None
        assert pp.counts["made"] == 0

    def test_on_frame_divisor_corrects_timing(self, monkeypatch):
        """e) divisor>1 → lead 触发时机按 (frames-1-n)/(fps/divisor) 修正。"""
        pp = self._make()
        # fps=10, divisor=2 → wall=(9-n)/5；n=6 → 0.6s > 0.35 不预测（未修正公式会在 n=6 触发）
        assert pp.on_frame("写代码", 6, 10, 10.0, 2, 0.35, exclude="写代码") is False
        assert pp.prediction is None
        # n=8 → wall=(9-8)/5=0.2s ≤ 0.35 → 预测
        assert pp.on_frame("写代码", 8, 10, 10.0, 2, 0.35, exclude="写代码") is True
        assert pp.counts["made"] == 1

    def test_on_frame_stores_but_skips_warm_for_pinned_product(self, monkeypatch):
        """idle/turn 分支产物：预测照存（P1-1 方案 a：拒收重掷会漂分布），
        但**不预热**（pinned，预热纯浪费）。"""
        _warm_ok.names.clear()
        pp = PredictivePrewarm(roll=lambda exclude: catalog.IDLE, warm=_warm_ok,
                               should_predict=_pred)
        assert pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35, exclude="写代码") is True
        assert pp.prediction is not None and pp.prediction["name"] == catalog.IDLE
        assert _warm_ok.names == [], "pinned 产物不得预热"

    def test_on_frame_unknown_frames_skip(self, monkeypatch):
        pp = self._make()
        _warm_ok.names.clear()
        assert pp.on_frame("写代码", 8, 0, 0.0, 1, 0.35, exclude="写代码") is False
        assert pp.counts["made"] == 0
        assert _warm_ok.names == []

    def test_on_frame_predicts_once_per_generation(self, monkeypatch):
        pp = self._make()
        assert pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35) is True
        # 同代次再次 on_frame 不再重复预测
        _warm_ok.names.clear()
        assert pp.on_frame("写代码", 9, 10, 10.0, 1, 0.35) is False
        assert pp.counts["made"] == 1
        assert _warm_ok.names == []

    def test_consume_hit(self):
        pp = self._make()
        pp.begin_anim("写代码")
        pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35, exclude="写代码")
        assert pp.consume(context_anim="写代码", exclude="写代码", gap_active=False) == "吃白饭"
        assert pp.counts["hit"] == 1
        assert pp.counts["miss_invalid"] == 0
        assert pp.prediction is None

    def test_consume_miss_invalid_on_context_mismatch(self):
        """c) 交互打断（上下文不符）→ 作废，消费走现场掷骰。"""
        pp = self._make()
        pp.begin_anim("写代码")
        pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35, exclude="写代码")
        # 交互打断：换到别的动画（context 不符）
        pp.begin_anim("待机呼吸休闲")  # 换代
        assert pp.consume(context_anim="待机呼吸休闲", exclude="待机呼吸休闲",
                          gap_active=False) is None
        assert pp.counts["miss_invalid"] == 1
        assert pp.counts["hit"] == 0

    def test_consume_miss_invalid_on_exclude_collision(self):
        pp = self._make()
        pp.begin_anim("吃白饭")
        # 预测与当前 exclude 同名（概率极小）→ 现场重掷
        pp._prediction = {"name": "吃白饭", "context_anim": "吃白饭", "gen": pp.generation}
        assert pp.consume(context_anim="吃白饭", exclude="吃白饭", gap_active=False) is None
        assert pp.counts["miss_invalid"] == 1

    def test_clear_discards_prediction(self):
        pp = self._make()
        pp.begin_anim("写代码")
        pp.on_frame("写代码", 8, 10, 10.0, 1, 0.35, exclude="写代码")
        pp.clear()
        assert pp.prediction is None
        assert pp.consume("写代码", "写代码", False) is None
        assert pp.counts["hit"] == 0


# ============================================================================
# config 键归一化 + library.warm_predicted 闸门
# ============================================================================
class TestConfig:
    def test_default_and_range(self, tmp_path):
        cfg = Config(base=tmp_path)
        assert cfg.get("predict_prewarm_lead_ms") == 350
        cfg.set("predict_prewarm_lead_ms", 50)
        assert cfg.get("predict_prewarm_lead_ms") == 200  # 夹到下限
        cfg.set("predict_prewarm_lead_ms", 1000)
        assert cfg.get("predict_prewarm_lead_ms") == 600  # 夹到上限


class _NoopWarmClip:
    """库级假 clip：resolves manifest 文件名，warm_first_frame 即时完成。"""

    def __init__(self, path, parent=None):
        self.path = path
        self.warm_calls = 0
        self._first_image = None

    def warm_first_frame(self):
        self.warm_calls += 1


def _make_lib(tmp_path, monkeypatch):
    monkeypatch.setattr(library_mod, "WebMClip", _NoopWarmClip)
    videos = tmp_path / "videos"
    for folder, files in {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm"],
    }.items():
        directory = videos / folder
        directory.mkdir(parents=True, exist_ok=True)
        (directory / files[0]).write_bytes(b"fake")
    return library_mod.MovieLibrary(asset_dir=videos, prewarm_policy="minimal")


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"等待超时: {predicate!r}")


class TestWarmPredicted:
    def test_warm_predicted_triggers_first_frame(self, tmp_path, monkeypatch):
        lib = _make_lib(tmp_path, monkeypatch)
        clip = lib.movie("写代码")
        lib.warm_predicted("写代码")
        _wait_until(lambda: clip.warm_calls >= 1)
        assert clip.warm_calls == 1

    def test_warm_predicted_noop_when_paused(self, tmp_path, monkeypatch):
        lib = _make_lib(tmp_path, monkeypatch)
        clip = lib.movie("写代码")
        lib.pause_warm()  # 隐藏/切角色暂停
        lib.warm_predicted("写代码")
        time.sleep(0.05)
        assert clip.warm_calls == 0


# ============================================================================
# 窗口级集成：a) 预测+预热触发  b) 消费 hit  c) 交互打断 miss_invalid
#               d) gap=0 与 gap>0  e) divisor 触发在窗口路径   f) 隐藏作废
# ============================================================================
class TestWindowPredictivePrewarm:
    def _switch_act(self, win, name="写代码"):
        # 确保当前动画是一个动作池动画（预测上下文），并给它实际播放下标
        win._switch(name)

    def test_a_prediction_made_and_prewarm_near_end(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)
        _set_random(monkeypatch, 0.5)  # 动作分支
        self._switch_act(win, "写代码")
        lib = win.lib
        pp = win.predictive_prewarm
        # frames=10, fps=10 → wall@n=8 = 0.1s ≤ 0.35 → 预测 '吃白饭'
        win._on_frame("写代码", 8)
        pred = pp.prediction
        assert pred is not None
        assert pred["name"] == "吃白饭"
        assert pred["context_anim"] == "写代码"
        assert lib.warmed == ["吃白饭"]           # 预热已掷
        assert win.lib.movie("吃白饭").warm_calls == 1  # 首帧预热被触发
        assert pp.counts["made"] == 1
        win.close()
        app.processEvents()

    def test_b_pick_next_consumes_prediction_hit(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        pp = win.predictive_prewarm
        win._on_frame("写代码", 8)   # 预测 '吃白饭'
        assert pp.prediction is not None
        win._on_frame("写代码", 9)   # 末帧 → _on_anim_ended → _pick_next 消费
        assert win.anim == "吃白饭"  # 消费到预测名
        assert pp.counts["hit"] == 1
        assert pp.counts["miss_invalid"] == 0
        assert pp.prediction is None
        win.close()
        app.processEvents()

    def test_c_interrupt_invalidates_consumption_re_rolls(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        pp = win.predictive_prewarm
        win._on_frame("写代码", 8)   # 预测 '吃白饭'
        assert pp.prediction is not None
        # 交互打断：_switch 换掉 self.anim（预测自然作废，不手动清状态）
        win._switch("待机呼吸休闲")
        # 待机临近结束且掷骰落在待机分支（不建新预测）→ 残留旧预测被消费作废
        _set_random(monkeypatch, 0.1)
        win._on_frame("待机呼吸休闲", 9)  # 末帧 → _pick_next → consume 作废
        assert pp.counts["miss_invalid"] == 1
        assert pp.counts["hit"] == 0
        win.close()
        app.processEvents()

    def test_d_gap0_consumes_prediction(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)  # animation_gap_seconds 默认 0
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        win._on_frame("写代码", 8)
        win._on_frame("写代码", 9)
        assert win.anim == "吃白饭"
        assert win.predictive_prewarm.counts["hit"] == 1
        win.close()
        app.processEvents()

    def test_d_gap1_bypasses_consumption_via_gap_step(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path, animation_gap_seconds=1.0)
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        pp = win.predictive_prewarm
        win._on_frame("写代码", 8)   # 预测 '吃白饭'
        assert pp.prediction is not None
        # 动作播完 + 配置了 gap → _start_animation_gap 播一个待机步，而不是 _pick_next
        win._on_frame("写代码", 9)
        assert win.anim in win.idles or win.anim in win.turns  # 进入 gap 步
        assert pp.counts["hit"] == 0  # 预测未在动作结束处被消费为 hit（gap 接管）
        # gap 到期后 gap 步播完 → _pick_next 消费一个 stale 预测 → 自然作废
        win._animation_gap_active = False
        _set_random(monkeypatch, 0.1)
        win._on_anim_ended(win.anim)
        assert pp.counts["miss_invalid"] == 1
        win.close()
        app.processEvents()

    def test_e_divisor_lead_timing_window_path(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)
        # 挡住窗口层的节流同步（否则该路径会把 divisor 复位到 1），直测公式读取
        monkeypatch.setattr(win, "_sync_movie_throttle", lambda reduced: None)
        monkeypatch.setattr(win, "_idle_reduction_active", lambda: False)
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        win.lib.movie("写代码").decode_throttle_divisor = 2  # 模拟闲置降帧 divisor=2
        pp = win.predictive_prewarm
        # fps=10, divisor=2 → wall=(9-n)/5；n=6 → 0.6s>0.35 不预测（未修正公式会触发）
        win._on_frame("写代码", 6)
        assert pp.prediction is None
        win._on_frame("写代码", 8)
        assert pp.prediction is not None
        win.close()
        app.processEvents()

    def test_f_hidden_invalidates_prediction(self, app, tmp_path, monkeypatch):
        win = _make_window(tmp_path)
        _set_random(monkeypatch, 0.5)
        self._switch_act(win, "写代码")
        pp = win.predictive_prewarm
        win._on_frame("写代码", 8)   # 预测 '吃白饭'
        assert pp.prediction is not None
        win.hide()
        app.processEvents()
        assert pp.prediction is None, "隐藏必须作废旧预测"
        # 隐藏期间迟到帧事件被生命周期守卫丢弃，不推进动画链、不重建预测
        win._on_frame("写代码", 9)
        assert pp.prediction is None
        assert win.anim == "写代码"
        win.close()
        app.processEvents()


class TestDistributionParity:
    """P1-1/P1-2 红绿锚点：预测消费链的动画分布必须与 HEAD 现场单掷一致
    （30% 待机 / 10% 转向 / 40% 动作 / 20% 移动）。

    修复前（拒收 idle/turn 并在 lead 窗口内逐帧重掷）稳态分布漂到
    ≈1%/0.4%/67%/31%，本测试直接钉死该回归。
    """

    def test_prediction_chain_distribution_matches_baseline(self):
        import random as _r

        pools = {"idles": ["i1", "i2", "i3"], "turns": ["t1"],
                 "acts": [f"a{k}" for k in range(20)], "moves": ["m1", "m2", "m3"]}
        acts_set = set(pools["acts"])
        moves_set = set(pools["moves"])
        # 注意：产品掷骰走模块级 random——seed 后必须恢复现场，否则确定性
        # 随机流会污染同进程后续测试（实测曾污染低优预热去重用例）。
        rng_state = _r.getstate()
        _r.seed(20260905)
        try:
            counts = {"i": 0, "t": 0, "a": 0, "m": 0}
            chains = 20000
            for _ in range(chains):
                pp = PredictivePrewarm(
                    roll=lambda ex: roll_next(pools, ex),
                    warm=lambda n: None,
                    should_predict=lambda n: n in acts_set or n in moves_set,
                )
                cur = "a0"
                # 模拟 lead 窗口内逐帧驱动（100 帧动画、24fps、lead=0.35s → n=92..100）
                for n in range(92, 101):
                    pp.on_frame(cur, n, 100, 24.0, 1, 0.35, exclude=cur)
                name = pp.consume(context_anim=cur, exclude=cur, gap_active=False)
                assert name is not None, "同 context 同代次的预测必须可消费"
                if name in pools["idles"]:
                    counts["i"] += 1
                elif name in pools["turns"]:
                    counts["t"] += 1
                elif name in moves_set:
                    counts["m"] += 1
                else:
                    counts["a"] += 1
        finally:
            _r.setstate(rng_state)
        total = chains
        assert abs(counts["i"] / total - 0.30) < 0.015, counts
        assert abs(counts["t"] / total - 0.10) < 0.010, counts
        assert abs(counts["a"] / total - 0.40) < 0.015, counts
        assert abs(counts["m"] / total - 0.20) < 0.015, counts

    def test_move_product_exempt_from_exclude_check(self):
        """P2-3：当前动画是移动名时，预测同名 move 产物是合法结局
        （move 分支掷骰本就不排 exclude，与 _try_move 一致），不得作废。"""
        moves = ["m1"]
        pp = PredictivePrewarm(
            roll=lambda exclude: "m1",
            warm=lambda n: None,
            should_predict=lambda n: True,
        )
        assert pp.on_frame("m1", 8, 10, 10.0, 1, 0.35, exclude="m1") is True
        # 不传 moves（旧行为）：撞名作废旧预测
        assert pp.consume(context_anim="m1", exclude="m1", gap_active=False) is None
        pp2 = PredictivePrewarm(
            roll=lambda exclude: "m1",
            warm=lambda n: None,
            should_predict=lambda n: True,
        )
        assert pp2.on_frame("m1", 8, 10, 10.0, 1, 0.35, exclude="m1") is True
        # 传 moves：move 产物豁免，合法命中
        assert pp2.consume(context_anim="m1", exclude="m1", gap_active=False,
                           moves=moves) == "m1"


def test_recycle_minutes_pushed_on_switch_and_refresh(tmp_path, app):
    """批11-B1 复审 P2-1：窗口→clip 的回收阈值推送回归（hasattr 守卫一旦
    漏一处，回收会静默关闭而全套测试仍绿——必须有这条钉住）。

    覆盖两个推送点：构造首个 _switch（启动即播 idle）+ refresh_pet_settings
    （设置保存后运行期刷新，复审 P1-2）。
    """
    win = _make_window(tmp_path, ffmpeg_recycle_minutes=5,
                       collision_enabled=False)
    try:
        clip = win.lib.movie(win.idle)
        assert 5 in clip.recycle_minutes_calls, \
            f"构造首个 _switch 必须推送回收阈值，实际: {clip.recycle_minutes_calls}"
        n_before = len(clip.recycle_minutes_calls)
        win.cfg.set("ffmpeg_recycle_minutes", 0)
        win.refresh_pet_settings()
        assert len(clip.recycle_minutes_calls) > n_before, \
            "refresh_pet_settings 必须把新阈值推送到当前 clip"
        assert clip.recycle_minutes_calls[-1] == 0
    finally:
        win.close()
        app.processEvents()


def test_switch_clears_previous_clip_display_frame(tmp_path, app):
    """批12 A1（复审修订）：_switch 切走成功时清空旧 clip 的显示槽
    （窗口权威侧，GUI 线程）；新 clip 不清。"""
    win = _make_window(tmp_path)
    try:
        prev_clip = win.lib.movie(win.anim)
        win._switch("写代码")
        assert win.anim == "写代码"
        assert prev_clip.clear_display_calls == 1, \
            "切走后旧 clip 显示槽必须被清一次"
        assert win.lib.movie("写代码").clear_display_calls == 0, \
            "新 clip 不得被清"
        # 再切回 idle：写代码 的槽被清，idle（现为旧 clip）已被清过一次不重复累加错
        win._switch(catalog.IDLE)
        assert win.lib.movie("写代码").clear_display_calls == 1
    finally:
        win.close()
        app.processEvents()


def test_finished_signal_of_abandoned_clip_reclears_slots(tmp_path, app):
    """批12 复审 N1：弃播 clip 残余帧流会把 _switch 清掉的显示槽重填——
    其迟到的结束标记被消费时必须再清一次（FIFO 保证其后无新帧）。"""
    win = _make_window(tmp_path)
    try:
        win._switch("写代码")
        abandoned = win.lib.movie("写代码")
        win._switch(catalog.IDLE)
        assert abandoned.clear_display_calls == 1, "切走时清一次"
        # 模拟：弃播 clip 的 reader 跑到结束，finished 信号迟到到达
        abandoned.finished.emit()
        assert abandoned.clear_display_calls == 2, \
            "弃播 clip 的结束标记消费点必须补清显示槽（N1）"
        # 当前动画的 finished 不受误伤：emit 后 idle 的清理计数不增加
        # （idle 的 1 次来自它自己被切走的那一刻，属正常）
        assert win.lib.movie(catalog.IDLE).clear_display_calls == 1
    finally:
        win.close()
        app.processEvents()
