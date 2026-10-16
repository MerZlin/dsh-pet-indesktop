# -*- coding: utf-8 -*-
"""避字移动控制器的降级契约与决策接力。

核心不变量：**拿不到数据时绝不改变既有行为**——功能关闭、文字图为空或已过期、
几何取不到、OCR 不可用，一律把入参原样吐回去（``dir/distance/room`` 不变、
``target_cy=None`` 交回窗口自己的随机纵向漫游）。这里把这四条逐一钉死。

几何用 monkeypatch 固定（不碰真实屏幕），所以在任意平台/无显示环境都能跑。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from pet import text_avoid
from pet import text_avoid_controller as controller_mod
from pet.text_avoid import TextBox
from pet.text_avoid_controller import TextAvoidController, enabled_for


class _Screen:
    def __init__(self, width=1000, height=1000):
        self._rect = QRect(0, 0, width, height)

    def availableGeometry(self):
        return self._rect

    def devicePixelRatio(self):
        return 1.0

    def name(self):
        return "fake-screen"


BODY = QRect(0, 0, 200, 200)


class FakeHost(QWidget):
    """最小宿主：控制器只用到 cfg / winId / devicePixelRatioF 与三处几何接口。"""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._sbr = BODY
        self._vp = QPoint(0, 0)
        self._avail = _Screen()
        self.geometry_error: Exception | None = None

    def _virtual_pos(self):
        if self.geometry_error:
            raise self.geometry_error
        return self._vp

    def _stable_body_local_rect(self):
        return self._sbr

    def _screen_available(self):
        return self._avail

    def devicePixelRatioF(self):
        return 1.0

    def _move_space(self):
        """「立刻走开」要先确认"真有干净地方可去"，该判定要拿移动空间（同窗口侧）。"""
        if self.geometry_error:
            raise self.geometry_error
        return _sp()


def _sp(cx=500.0):
    # sp.avail 在产品代码里就是 QRect（window._move_space 取 availableGeometry()）
    return SimpleNamespace(
        vp=QPoint(0, 0), sbr=BODY, avail=QRect(0, 0, 1000, 1000),
        cx=cx, left=100.0, right=900.0,
    )


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(app, monkeypatch):
    monkeypatch.setattr(
        "pet.text_avoid_controller.platform_win.window_physical_rect",
        lambda hwnd: (0, 0, 1000, 1000),
    )
    host = FakeHost({"text_avoid": {"enabled": True}})
    ctrl = TextAvoidController(host)
    return ctrl


# ------------------------------------------------------------------ 降级契约

def test_no_window_display_affinity_anywhere():
    """回归守卫：不许再把 WDA_EXCLUDEFROMCAPTURE 加回来（2026-10-15 实机事故）。

    事故经过：为了"抓屏时把桌宠排除掉、从而看见它背后的内容"，给桌宠窗口设了
    ``WDA_EXCLUDEFROMCAPTURE``。实机结果是窗口在**物理屏幕上也不再渲染**——
    进程活着、窗口在屏内、DWM cloaked=0、动画照常，但人眼看不见（同机并排两个
    方块做 A/B，只有未置位的那个可见）。用户看到的就是"桌宠消失了"。
    现在改用 PrintWindow 抓"底下那个窗口自己的渲染结果"，不需要任何窗口显示属性。

    这条断言故意写得显眼：要重新引入 WDA，必须先删掉它；删的时候就会读到这段原因。
    """
    import pathlib

    from pet import platform_win, text_avoid_controller

    assert not hasattr(platform_win, "set_capture_exclusion"), \
        "platform_win 又把 set_capture_exclusion 加回来了？先读这条测试的说明"
    for mod in (platform_win, text_avoid_controller):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "SetWindowDisplayAffinity" not in src, f"{mod.__name__} 又用上了 WDA"


def test_disabled_returns_passthrough(app):
    host = FakeHost({"text_avoid": {"enabled": False}})
    ctrl = TextAvoidController(host)
    ctrl._boxes = (TextBox("鱼", 600, 0, 700, 200),)
    ctrl._boxes_at = float("inf")
    assert ctrl.adjust_move(_sp(), 1, 150.0, 400.0) == (1, 150.0, 400.0, None, 1.0)


def test_missing_config_is_treated_as_disabled(app):
    assert enabled_for(FakeHost({})) is False
    assert enabled_for(FakeHost({"text_avoid": "看得懂才怪"})) is False
    assert enabled_for(FakeHost({"text_avoid": {"enabled": True}})) is True


def test_no_boxes_returns_passthrough(controller):
    assert controller.adjust_move(_sp(), 1, 150.0, 400.0) == (1, 150.0, 400.0, None, 1.0)


def test_stale_boxes_return_passthrough(controller):
    """文字图过期就宁可不动——不能拿十几秒前的旧图去决策。"""
    controller._boxes = (TextBox("鱼", 600, 0, 700, 200),)
    controller._boxes_at = 0.0   # monotonic 起点，必然过期
    assert controller.text_boxes() == ()
    assert controller.adjust_move(_sp(), -1, 120.0, 300.0) == (-1, 120.0, 300.0, None, 1.0)


def test_geometry_failure_returns_passthrough(controller):
    controller._boxes = (TextBox("鱼", 600, 0, 700, 200),)
    controller._boxes_at = float("inf")
    controller._host.geometry_error = RuntimeError("窗口正在销毁")
    assert controller.adjust_move(_sp(), 1, 150.0, 400.0) == (1, 150.0, 400.0, None, 1.0)


# ------------------------------------------------------------------ 决策接力

def test_clean_proposed_target_keeps_random_walk_but_pins_verified_height(controller):
    """原落点干净：横向一步不改，只把纵向钉在核对过的高度上。"""
    controller._boxes = (TextBox("鱼", 10, 10, 30, 30),)   # 离落点很远
    controller._boxes_at = float("inf")
    dir_sign, distance, room, target_cy, boost = controller.adjust_move(_sp(), 1, 150.0, 400.0)
    assert (dir_sign, distance, room) == (1, 150.0, 400.0)
    assert boost == 1.0, '没有改道就不该加速'
    assert target_cy is not None, '纵向必须给绝对值，否则随机漫游会跑到没核对的高度'
    assert target_cy == pytest.approx(-BODY.y(), abs=1.0)


def test_dirty_proposed_target_is_redirected_to_clear_spot(controller):
    """原落点压字：横向改道到压不住字的位置，并把 room 按新方向重算。"""
    controller._boxes = (TextBox("一整行字", 590, 0, 710, 200),)
    controller._boxes_at = float("inf")
    sp = _sp(cx=500.0)
    dir_sign, distance, room, target_cy, boost = controller.adjust_move(sp, 1, 150.0, 400.0)
    assert target_cy is not None
    landed_cx = sp.cx + dir_sign * distance
    # 纵向要用钩子返回的高度：它可能为了避开字而微调（这里会被 MOVE_MARGIN
    # 钳到下界 20），按 y=0 去复核等于在核对另一个落点。
    body = (landed_cx - 100, target_cy, landed_cx + 100, target_cy + 200)
    assert text_avoid.text_cost(body, controller._boxes)[0] == 0
    # room 必须跟着新方向走（否则量化后的位移会越界）
    expected_room = (sp.cx - sp.left) if dir_sign < 0 else (sp.right - sp.cx)
    assert room == pytest.approx(expected_room)


# ------------------------------------------------- 「立即反应」（打断动画走开）

def _react_ready(ctrl, boxes):
    ctrl._boxes = tuple(boxes)
    ctrl._boxes_at = float("inf")


def test_react_now_off_never_emits(controller):
    """开关关着就一次都不许打断动画——默认行为必须与从前一致。"""
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0, 0, 200, 200)])
    controller._maybe_react()
    assert fired == []


def test_react_now_emits_when_current_position_covers_text(controller):
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    # FakeHost：vp=(0,0)、身体局部框 (0,0,200,200)、dpr=1 → 物理就是 (0,0,200,200)
    # 只在身体当前位置放字：走开就有干净地方（max_travel 内可达）→ 该走。
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    controller._maybe_react()
    assert fired == [1]


def test_react_now_stays_put_when_nowhere_clean_is_reachable(controller):
    """整屏都是字（max_travel 内没有干净落点）时**不许**走开。

    这是"屏幕没动它也一直在乱跑"的直接成因：走一趟还是压着字，冷却一到再来一趟。
    所以逃逸必须先确认真的有个干净地方可去，否则原地不动。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True,
                                           "max_travel": 700.0}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    # 把整条水平带都铺满字：任何落点都压着字
    wide = [TextBox("字", x, 0.0, x + 60.0, 200.0) for x in range(0, 1000, 60)]
    _react_ready(controller, wide)
    controller._maybe_react()
    assert fired == [], "附近没有干净落点却走开 = 白跑一趟，还会连锁触发"
    assert controller._react_failures == 1, "白走要记账，用于指数退避"


def test_react_now_backs_off_after_repeated_futile_escapes(controller):
    """连续白走 → 冷却指数翻倍（6s → 12s → 24s …），节奏自己静下来。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    assert controller._react_cooldown() == pytest.approx(controller_mod.REACT_COOLDOWN_SECONDS)
    # 模拟"逃逸之后拿到过新图、仍然压着字"连续发生
    for expected in (2, 4):
        controller._escaped_at = time.monotonic() - 5.0
        controller._escaped_generation = controller._map_generation - 1   # 换过一代
        controller._last_react = 0.0        # 冷却已过（monotonic 早就跑远了）
        controller._maybe_react()
        assert controller._react_cooldown() == pytest.approx(
            controller_mod.REACT_COOLDOWN_SECONDS * expected)
    # 干净一次就清零
    controller._boxes = ()
    controller._boxes_at = float("inf")
    controller._react_cooldown()
    controller._boxes = (TextBox("离得远", 9000.0, 9000.0, 9100.0, 9100.0),)
    controller._maybe_react()
    assert controller._react_failures == 0


def test_react_now_silent_when_current_position_is_clear(controller):
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("离得远", 5000.0, 5000.0, 5100.0, 5100.0)])
    controller._maybe_react()
    assert fired == []


def test_react_now_respects_cooldown(controller):
    """连着触发会变成抽搐：冷却期内不许再发。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    controller._maybe_react()
    controller._maybe_react()
    assert fired == [1]


def test_react_now_silent_while_a_move_is_already_planned(controller):
    """已经在路上就别再打断一次——正在播的就是避字之后的移动。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    controller._host._move_plan = {"anim": "走路"}
    controller._maybe_react()
    assert fired == []


def test_react_now_needs_fresh_boxes(controller):
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    controller._boxes = (TextBox("压住", 0.0, 0.0, 200.0, 200.0),)
    controller._boxes_at = 0.0          # 过期
    controller._maybe_react()
    assert fired == []


def test_redirect_is_skipped_when_the_gait_landing_would_be_worse(controller,
                                                                  monkeypatch):
    """吸附后的落点若比原来那个随机落点更差，就**不改道**（不改也不能更糟）。

    实机日志里出现过「原落点压 3 字/960px² → 改走 1382px（改后压 5 字/1177px²）」：
    白跑一大段还越改越差，正是用户说的"乱跑"。

    这条规则直接针对**判据**测：把吸附结果钉成"压 9 字"，不去构造具体几何——
    几何一改（比如搜索框收紧）场景就不再成立，测的其实是运气而不是规则。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True}}
    # 原落点压 1 字：规划器会去找干净点并改道（前提成立）
    controller._boxes = (TextBox("甲", 640.0, 0.0, 660.0, 200.0),)
    controller._boxes_at = float("inf")

    from pet import text_avoid as ta
    real_snap = ta.snap_to_gait

    def worse_snap(**kwargs):
        cx, _t, _a = real_snap(**kwargs)
        return cx, 9, 9999.0          # 吸附后压 9 字 / 巨大面积 → 比原落点差得多

    monkeypatch.setattr(ta, "snap_to_gait", worse_snap)
    result = controller.adjust_move(_sp(cx=500.0), 1, 150.0, 400.0, stride=100.0)
    assert (result[0], result[1], result[2]) == (1, 150.0, 400.0), \
        "越改越差时应当保留原落点"
    assert result[4] == 1.0, "没改道就不该用跑动速度"

    # 对照组：同样的输入、同样的图，吸附结果**不**更差时确实会改道
    monkeypatch.setattr(ta, "snap_to_gait", real_snap)
    again = controller.adjust_move(_sp(cx=500.0), 1, 150.0, 400.0, stride=100.0)
    assert (again[0], again[1]) != (1, 150.0), "前提：这一局本来是会改道的"


def test_run_speed_is_returned_only_when_it_redirects(controller):
    """跑动速度只在真的为避字改道时给：普通漫游不该被加速。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "run_speed": 2.0}}
    controller._boxes = (TextBox("一整行字", 590, 0, 710, 200),)
    controller._boxes_at = float("inf")
    sp = _sp(cx=500.0)
    result = controller.adjust_move(sp, 1, 150.0, 400.0, stride=100.0)
    assert result[4] == pytest.approx(2.0), '改道了就该用跑动速度'

    # 原落点本来就干净 → 不改道 → 不给加速
    controller._boxes = (TextBox("离得远", 9000, 9000, 9100, 9100),)
    assert controller.adjust_move(sp, 1, 150.0, 400.0, stride=100.0)[4] == 1.0


def test_run_speed_is_clamped_to_a_sane_range(controller):
    controller._host.cfg = {"text_avoid": {"enabled": True, "run_speed": 99}}
    controller._boxes = (TextBox("一整行字", 590, 0, 710, 200),)
    controller._boxes_at = float("inf")
    assert controller.adjust_move(_sp(cx=500.0), 1, 150.0, 400.0, stride=100.0)[4] == 3.0


# ------------------------------------------------- 窗口侧接缝（window.py → mixin）

def test_window_hook_is_wired_and_passes_through_when_disabled(app, tmp_path, monkeypatch):
    """真实 PetWindow：钩子存在、未启用时不构造控制器、调用即原样返回。

    这一段是 window.py 那次钩子调用与 mixin 之间的唯一接缝，单靠纯逻辑与控制器
    测试都覆盖不到——属性名写错、mixin 没混进来都只会在真机跑起来时才炸。
    """
    from pet.config import Config
    from pet.window import PetWindow
    from tests.pet_window_fakes import FakeLibrary

    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("edge_probe_enabled", False)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        monkeypatch.setattr(win, "_screen_available", lambda: _Screen(width=1920))
        monkeypatch.setattr(win, "_stable_body_local_rect", lambda: BODY)
        monkeypatch.setattr(win, "_virtual_pos", lambda: QPoint(100, 100))
        assert callable(getattr(win, "_text_avoid_move", None)), \
            "WindowFeatureGateMixin 的避字钩子没混进 PetWindow"
        assert win._text_avoid is None, "没启用就不该构造控制器"
        sp = win._move_space()
        assert sp is not None
        assert win._text_avoid_move(sp, 1, 150.0, 400.0) == (1, 150.0, 400.0, None, 1.0)
        # 建计划这条真实路径也不能被钩子改坏
        assert win._try_move() is True
        assert win._move_plan is not None
    finally:
        win.close()
        app.processEvents()


def test_window_hook_redirects_with_real_window_geometry(app, tmp_path, monkeypatch):
    """真实 PetWindow 几何 + 启用状态：钩子必须把落点挪出压字区。

    坐标口径：把窗口物理矩形钉成 (0,0,2000,2000)、虚拟位置钉成 (100,100)、
    dpr=1 → ``anchor = vp - rect/dpr = (100,100)``，于是
    ``physical = virtual - 100``。工作区 1920 → ``_move_space().cx = 200``。
    脏区故意放在 proposed=350 的身体框 [250,450] 上，清楚的条件因此是
    身体中心 ≤150 或 ≥550；可达界是 [170,1750]，所以唯一解是 ≥550。
    """
    from pet import text_avoid_controller as controller_mod
    from pet.config import Config
    from pet.window import PetWindow
    from tests.pet_window_fakes import FakeLibrary

    monkeypatch.setattr(controller_mod.platform_win, "window_physical_rect",
                        lambda hwnd: (0, 0, 2000, 2000))
    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("edge_probe_enabled", False)
    cfg.set("text_avoid", {"enabled": True})
    win = PetWindow(FakeLibrary(), cfg)
    try:
        monkeypatch.setattr(win, "_screen_available", lambda: _Screen(width=1920))
        monkeypatch.setattr(win, "_stable_body_local_rect", lambda: BODY)
        monkeypatch.setattr(win, "_virtual_pos", lambda: QPoint(100, 100))
        sp = win._move_space()
        assert sp is not None and sp.cx == pytest.approx(200.0)
        ctrl = win._ensure_text_avoid()
        # 脏区覆盖"原落点"那一块：用产品同一套换算把虚拟 [250,450] 变成物理坐标
        # （离屏窗口的 dpr 不保证是 1，不能写死）。高度拉满 → 纵向躲不掉，只能横向让。
        cmap = text_avoid.CoordMap.from_window(
            100, 100, (0, 0, 2000, 2000), float(win.devicePixelRatioF() or 1.0))
        dirty_l, dirty_t = cmap.to_physical(250.0, -1000.0)
        dirty_r, dirty_b = cmap.to_physical(450.0, 3000.0)
        ctrl._boxes = (TextBox("挡路的字", dirty_l, dirty_t, dirty_r, dirty_b),)
        ctrl._boxes_at = float("inf")

        def body_phys(center: float) -> tuple[float, float, float, float]:
            """落点身体框（物理）——决策全程在物理坐标里，断言也必须在同一套。"""
            return cmap.rect_to_physical((center - 100, 0, center + 100, 200))

        naive = sp.cx + 150.0
        assert text_avoid.text_cost(body_phys(naive), ctrl._boxes)[0] > 0, \
            "自检：这一局的随机落点本来就得压在字上，否则这条测试等于空跑"

        dir_sign, distance, room, target_cy, _boost = win._text_avoid_move(sp, 1, 150.0, 400.0)
        landed = sp.cx + dir_sign * distance
        assert target_cy is not None, "启用且有文字图时必须给纵向绝对值"
        assert text_avoid.text_cost(body_phys(landed), ctrl._boxes)[0] == 0, \
            f"落点身体 {body_phys(landed)} 仍压在脏区上"
        # room 必须按新方向重算（否则量化后的位移会越界）
        expected_room = (sp.cx - sp.left) if dir_sign < 0 else (sp.right - sp.cx)
        assert room == pytest.approx(expected_room)
    finally:
        win.close()
        app.processEvents()


def test_escape_handler_moves_when_allowed_and_respects_no_move(app, tmp_path,
                                                               monkeypatch):
    """「立即反应」的窗口侧守卫：正常情况下真的起一步移动；设了不移动就不动。"""
    from pet import text_avoid_controller as controller_mod
    from pet.config import Config
    from pet.window import PetWindow
    from tests.pet_window_fakes import FakeLibrary

    monkeypatch.setattr(controller_mod.platform_win, "window_physical_rect",
                        lambda hwnd: (0, 0, 2000, 2000))
    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("edge_probe_enabled", False)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        monkeypatch.setattr(win, "_screen_available", lambda: _Screen(width=1920))
        monkeypatch.setattr(win, "_stable_body_local_rect", lambda: BODY)
        monkeypatch.setattr(win, "_virtual_pos", lambda: QPoint(100, 100))

        win._text_avoid_escape()
        assert win._move_plan is not None, "正常情况下应该真的走开"

        win._cancel_move()
        assert win._move_plan is None
        monkeypatch.setattr(win, "no_move", True)
        win._text_avoid_escape()
        assert win._move_plan is None, "用户设了不移动就不许因为避字而走开"
    finally:
        win.close()
        app.processEvents()


def test_run_boost_is_consumed_by_the_next_animation_switch(app, tmp_path,
                                                            monkeypatch):
    """跑动倍率走"一次性标记 → _switch 在 start() 之前取走"这条链。

    **为什么必须是起播前**：WebM 的帧产出速率是 reader 启动时用 -readrate 写进 ffmpeg
    命令行的，起播后再改 set_playback_speed 只改呈现节拍、不会有更多帧（实测 3x 仍按
    1x 走完）。所以这里断言：标记会被 _switch 消费一次、并叠加到动画速率上，
    普通动画取到 1.0（行为不变）。
    """
    from pet import text_avoid_controller as controller_mod
    from pet.config import Config
    from pet.window import PetWindow
    from tests.pet_window_fakes import FakeLibrary

    monkeypatch.setattr(controller_mod.platform_win, "window_physical_rect",
                        lambda hwnd: (0, 0, 2000, 2000))
    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("edge_probe_enabled", False)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        monkeypatch.setattr(win, "_screen_available", lambda: _Screen(width=1920))
        monkeypatch.setattr(win, "_stable_body_local_rect", lambda: BODY)
        monkeypatch.setattr(win, "_virtual_pos", lambda: QPoint(100, 100))
        assert win._take_run_boost() == 1.0, '默认没有倍率'

        win._pending_run_boost = 2.0
        assert win._try_move() is True
        assert win.movie is not None
        assert win.movie.speed == pytest.approx(win.playback_speed * 2.0, rel=0.05)
        assert win._take_run_boost() == 1.0, '一次性：取走即复位'
        # 紧接着的普通移动不该被上一轮的倍率带过去（哪怕切到同一个动画）
        win._cancel_move()
        assert win._try_move() is True
        assert win.movie.speed == pytest.approx(win.playback_speed, rel=0.05), \
            '上一轮的跑动倍率漏进了这一次普通移动'
    finally:
        win.close()
        app.processEvents()


# ---------------------------------------- 按需刷新（不再定时轮询）与"看不到的地方不许选"
def test_decision_without_a_map_requests_a_refresh(controller, monkeypatch):
    """要挑落点却没有文字图 → 发起一次刷新，并且**本次原样返回**（不阻塞）。

    这就是"按需"的第一条触发点：以前靠定时轮询把图备好，现在只有真要用了才刷。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "refresh_seconds": 10.0}}
    controller._boxes = ()
    controller._last_request = 0.0          # 距上次很久了，允许发起
    calls = []
    monkeypatch.setattr(controller, "_current_band", lambda: ((0, 0, 500, 300),
                                                              (10.0, 10.0, 200.0, 200.0)))
    monkeypatch.setattr(controller, "_wake", type("W", (), {"set": lambda self: calls.append(1)})())
    assert controller.adjust_move(_sp(cx=500.0), 1, 150.0, 400.0) == (1, 150.0, 400.0, None, 1.0)
    assert calls == [1], "没有图时应当发起一次后台刷新"
    assert controller._inflight is True


def test_refresh_requests_are_rate_limited(controller, monkeypatch):
    """最短间隔是**限流**：刚发起过就不再发起，等过了间隔才允许。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "refresh_seconds": 10.0}}
    monkeypatch.setattr(controller, "_current_band", lambda: ((0, 0, 500, 300),
                                                              (10.0, 10.0, 200.0, 200.0)))
    woke = []
    monkeypatch.setattr(controller, "_wake", type("W", (), {"set": lambda self: woke.append(1)})())

    controller._last_request = 0.0
    assert controller._request_refresh() is True
    controller._inflight = False            # 假装上一次已经收工
    assert controller._request_refresh() is False, "同一间隔内不该重复发起"
    assert len(woke) == 1
    controller._last_request = 0.0          # 时间过去了
    assert controller._request_refresh() is True


def test_idle_tick_refreshes_only_at_the_low_frequency_floor(controller, monkeypatch):
    """空闲时**不按最短间隔轮询**；只有图旧到兜底线才补一次。

    两条都断言，因为缺任何一条都会出问题：
    - 若按 `refresh_seconds` 轮询，就退回了"定时刷"（这条把那个钉死）；
    - 若完全没有兜底，长空闲之后**每次决策都会遇到过期的图** → 永远不改道（功能等于没开）。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "refresh_seconds": 10.0,
                                           "react_now": False}}
    controller._boxes = (TextBox("离得远", 9000.0, 9000.0, 9100.0, 9100.0),)
    controller._last_request = 12345.0      # 记一个可辨认的值

    # ① 图是新鲜的 → 什么都不该发生
    controller._boxes_at = float("inf")
    controller._on_tick()
    assert controller._last_request == 12345.0, "图新鲜时不该有任何刷新产生"
    assert controller._inflight is False

    # ② 图老过兜底线 → 补一次（且只补一次，仍受最短间隔限流）
    calls = []
    monkeypatch.setattr(controller, "_current_band", lambda: ((0, 0, 500, 300),
                                                              (10.0, 10.0, 200.0, 200.0)))
    monkeypatch.setattr(controller, "_wake", type("W", (), {"set": lambda self: calls.append(1)})())
    controller._boxes_at = time.monotonic() - (controller_mod.IDLE_REFRESH_SECONDS + 5.0)
    controller._last_request = 0.0
    controller._on_tick()
    assert calls == [1], "图太旧应当低频兜底刷一次"
    controller._inflight = False
    assert controller._request_refresh() is False, "兜底刷新同样受最短间隔限流"


def test_react_check_requests_a_refresh_when_it_has_no_map(controller, monkeypatch):
    """开了「被压住就立刻走开」时，判定没有图可用 → 发起刷新（第二条按需触发点）。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "refresh_seconds": 10.0,
                                           "react_now": True}}
    controller._boxes = ()
    controller._last_request = 0.0
    monkeypatch.setattr(controller, "_current_band", lambda: ((0, 0, 500, 300),
                                                              (10.0, 10.0, 200.0, 200.0)))
    sent = []
    monkeypatch.setattr(controller, "_wake", type("W", (), {"set": lambda self: sent.append(1)})())
    controller._on_tick()
    assert sent == [1], "没有图就没法判定，应当先发起一次刷新"


def test_search_box_and_ocr_band_are_the_same_rectangle(controller):
    """**看不到的地方不许选**：搜索框必须落在 OCR 条带里，且条带 ⊇ 搜索框。

    此前条带按整屏宽铺、纵向只盖身体上下各一个 y_margin，而规划器纵向搜整屏——
    "往上一大截"的候选落在没 OCR 过的区域里，代价读成 0（看起来干净）。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "max_travel": 700.0,
                                           "y_margin": 140.0}}
    box = controller._geometry_box()
    assert box is not None
    left, top, w, h = box.band
    # 搜索框（身体中心 / 身体顶的允许范围）必须整体落在条带内
    assert left <= box.cur_cx - box.body_w / 2
    assert box.cur_cx + box.body_w / 2 <= left + w
    assert top <= box.top_lo
    assert box.top_hi + box.body_h <= top + h
    # 横向最远只到 max_travel（逻辑）×dpr（本夹具 dpr=1）
    assert box.cx_hi - box.cx_lo <= 2 * 700.0 + 1e-6, "横向搜索范围不该超过 max_travel"
    # 纵向只到 y_margin
    assert box.top_hi - box.top_lo <= 2 * 140.0 + 1e-6, "纵向搜索范围不该超过 y_margin"


def test_band_shrinks_with_the_search_settings(controller):
    """调小 max_travel / y_margin 必须**同时**缩小识别面积（成本随面积走）。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "max_travel": 700.0,
                                           "y_margin": 140.0}}
    wide = controller._geometry_box().band
    controller._host.cfg = {"text_avoid": {"enabled": True, "max_travel": 200.0,
                                           "y_margin": 40.0}}
    narrow = controller._geometry_box().band
    assert narrow[2] < wide[2], "横向搜索范围变小 → 条带宽度也要变小"
    assert narrow[3] < wide[3], "纵向搜索范围变小 → 条带高度也要变小"


def test_landing_is_never_outside_the_ocr_band(controller):
    """端到端口径：规划出来的落点身体框必须落在本次 OCR 的条带内。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "max_travel": 700.0,
                                           "y_margin": 140.0}}
    box = controller._geometry_box()
    left, top, w, h = box.band
    # 远处上下各放字：旧口径下"往上一大截"会被判成干净
    controller._boxes = (TextBox("上方", 400.0, -600.0, 600.0, -400.0),
                         TextBox("下方", 400.0, 900.0, 600.0, 1100.0),
                         TextBox("挡路", 500.0, 0.0, 700.0, 200.0))
    controller._boxes_at = float("inf")
    sp = _sp(cx=500.0)
    out = controller.adjust_move(sp, 1, 150.0, 400.0, stride=100.0)
    landed_cx = sp.cx + out[0] * out[1]
    body_top = out[3] if out[3] is not None else box.cur_top
    assert left - 1 <= landed_cx - box.body_w / 2
    assert landed_cx + box.body_w / 2 <= left + w + 1
    assert top - 1 <= body_top
    assert body_top + box.body_h <= top + h + 1


# ------------------------------------- 实机 bug：冻结的文字图（2026-10-16 用户现场报告）
def test_a_decision_asks_for_a_refresh_even_when_the_map_is_usable(controller,
                                                                   monkeypatch):
    """**只要要挑落点就发起刷新**（限流交给最短间隔），而不是"没图才刷"。

    实机事故：改成按需之后我只在"完全没有图"时才发起刷新，于是图只要还没过期就
    永远不刷新——日志里同一处位置连续 50 秒报出**完全相同**的"压 3 字/5957px²"，
    桌宠对着一张旧快照每 6 秒逃逸一次，用户看到的是"没字也跑开、还跑到有字的地方"。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "refresh_seconds": 10.0}}
    controller._boxes = (TextBox("字", 0.0, 0.0, 200.0, 200.0),)
    controller._boxes_at = float("inf")           # 图是新鲜可用的
    controller._last_request = 0.0
    asked = []
    monkeypatch.setattr(controller, "_request_refresh",
                        lambda: (asked.append(1), True)[1])
    controller.adjust_move(_sp(cx=500.0), 1, 150.0, 400.0)
    assert asked == [1], "图可用也必须发起刷新，否则判断会一直基于同一张旧快照"


def test_the_react_watch_keeps_the_map_fresh(controller, monkeypatch):
    """「被压住就立刻走开」开着时，判定每拍都要发起刷新（限流在内）。

    这一档要持续盯"此刻压不压字"，图不刷新就等于盯着旧快照看——正是幻影判断的来源。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True,
                                           "refresh_seconds": 10.0}}
    _react_ready(controller, [TextBox("离得远", 9000.0, 9000.0, 9100.0, 9100.0)])
    controller._last_request = 0.0
    asked = []
    monkeypatch.setattr(controller, "_request_refresh",
                        lambda: (asked.append(1), True)[1])
    controller._on_tick()
    assert asked == [1]


def test_only_one_escape_per_text_map(controller):
    """**同一张文字图只逃逸一次**：否则就是拿同一份数据反复做同一个决定 → A/B 来回跑。

    实机日志里那对"每 6 秒逃逸一次、数字一字不差"就是这个；等图刷新过才知道上趟有没有用。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    controller._last_react = 0.0

    controller._maybe_react()
    assert fired == [1]
    # 冷却过去了，但图**没有**换代 → 不许再逃逸
    controller._last_react = 0.0
    controller._maybe_react()
    assert fired == [1], "同一张文字图上重复逃逸 = 用户看到的来回乱跑"
    assert controller._react_failures == 0, "这不该记成白走（还没重新量过）"

    # 安全阀：图太久没更新（刷新可能一直在失败）→ 放行，绝不把她永远钉死
    controller._boxes_at = time.monotonic() - (controller._react_stuck_seconds() + 1.0)
    controller._last_react = 0.0
    controller._maybe_react()
    assert fired == [1, 1], "刷新一直失败时也要放行，否则她会卡在字上一动不动"


def test_escape_backoff_judges_by_whether_the_escape_helped(controller):
    """图刷新过之后：压住的字没减少算白走（冷却翻倍），减少了就不算。"""
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True}}
    fired = []
    controller.escape_requested.connect(lambda: fired.append(1))
    _react_ready(controller, [TextBox("压住", 0.0, 0.0, 200.0, 200.0)])
    controller._last_react = 0.0
    controller._maybe_react()
    assert fired == [1]
    assert controller._escaped_count == 1        # 逃逸前压 1 字

    # 换过一代图，仍然压着同样多 → 白走；冷却翻倍后它会再试一趟（数据是新的，合理）
    controller._escaped_generation = controller._map_generation - 1
    controller._escaped_at = time.monotonic() - 5.0
    controller._last_react = 0.0
    controller._maybe_react()
    assert controller._react_failures == 1, "没改善就该退避"
    assert controller._escaped_generation == controller._map_generation, \
        "再逃逸时要记下自己用的是哪一代图"

    # 改善的情形：只剩 0 字（干净）→ 退避清零，不记账
    controller._react_failures = 1
    controller._escaped_generation = controller._map_generation - 1
    controller._escaped_at = time.monotonic() - 5.0
    controller._escaped_count = 3
    _react_ready(controller, [TextBox("离得远", 9000.0, 9000.0, 9100.0, 9100.0)])
    controller._maybe_react()
    assert controller._react_failures == 0, "干净了就该清零"


def test_the_requested_refresh_actually_replaces_the_map(controller, monkeypatch):
    """闭环：发起的那次刷新要**真的**换掉文字图并推进代次。

    为什么专门测这条：这次实机 bug 的根就是"图从不刷新"，而"同一张图只逃逸一次"
    这条规则要能解开，也必须依赖"新图 = 新一代"。所以从 `_request_refresh()` 到
    `_refresh_once()` 再到 `_on_words_ready()` 这一串必须能打通。
    """
    controller._host.cfg = {"text_avoid": {"enabled": True, "react_now": True,
                                           "refresh_seconds": 10.0}}
    monkeypatch.setattr(text_avoid, "grab_screen",
                        lambda: __import__("PIL.Image", fromlist=["Image"]).new("RGB", (64, 64)))
    monkeypatch.setattr(text_avoid, "fingerprint", lambda img, ignore_rect=None: b"fp")
    monkeypatch.setattr(text_avoid, "crop_region", lambda img, band: img)
    monkeypatch.setattr(text_avoid, "mask_boxes", lambda words, footprint: words)
    monkeypatch.setattr(text_avoid, "read_under_pet",
                        lambda footprint, need_text=True: ())
    monkeypatch.setattr(text_avoid, "read_words_from_image",
                        lambda img, origin, need_text=True: (TextBox("新图", 5.0, 5.0, 9.0, 9.0),))

    controller._boxes = (TextBox("旧图", 0.0, 0.0, 1.0, 1.0),)
    controller._boxes_at = float("inf")
    controller._map_generation = 7
    controller._last_request = 0.0
    controller._inflight = False

    assert controller._request_refresh() is True, "该发起时就要发起"
    band, footprint = controller._band
    controller._refresh_once(band, footprint)      # 后台线程里那段（这里同步跑完）

    assert controller._map_generation == 8, "拿到新图必须推进代次"
    assert [b.text for b in controller._boxes] == ["新图"], "新图要真的替换旧图"
    assert controller._boxes_at <= time.monotonic()
    assert controller._inflight is False, "跑完要放行下一次"
