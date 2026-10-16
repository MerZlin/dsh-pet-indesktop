# -*- coding: utf-8 -*-
"""避字移动的 overlay/sprite 拓扑接线（pet/text_avoid_sprite.py + 行为层钩子）。

覆盖四件事：
1. **降级契约**：没启用/控制器缺席时，行为层拿到的必须与没接线时逐字节一致；
2. **宿主适配的坐标口径**：sprite 世界 → 控制器要的"全局逻辑像素"（这是整条
   接线里最容易错的地方，单独钉住）；
3. **改道钩子**：控制器给出的落点确实进了 `_plan_move` 的计划（含跑动倍率）；
4. **立刻走开的出口**：控制器的 escape 信号确实变成行为层的一次移动；
   以及新公开方法 `request_move_now` 自身的门（no_move / 接管态）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtWidgets import QApplication

from pet import text_avoid
from pet.pet_sprite import INTERACTION_DRAG, INTERACTION_NORMAL
from pet.sprite_behavior import BehaviorController, STATE_MOVE
from pet.text_avoid import TextBox
from pet import text_avoid_controller
from pet.text_avoid_controller import TextAvoidController
from pet.text_avoid_sprite import SpriteTextAvoid, _SpriteHost
from tests.test_sprite_behavior import ScriptedRng, _make_library, _make_sprite


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------- 替身
class _Overlay:
    """最小 overlay 替身：sprites 列表 + 全局逻辑位置。"""

    def __init__(self, sprites, pos=(0, 0)):
        self.sprites = list(sprites)
        self._pos = QPoint(*pos)

    def pos(self):
        return QPoint(self._pos)

    def devicePixelRatioF(self):
        return 1.0

    def winId(self):
        return 4242


class _Shell:
    """最小壳替身：控制器只用到 _config / overlay / behavior / _bounds。"""

    def __init__(self, cfg, overlay, bounds=QRect(0, 0, 1600, 900)):
        self._config = cfg
        self.overlay = overlay
        self._bounds = QRect(bounds)
        self.behavior = BehaviorController(self._bounds)


def _cfg(enabled: bool) -> dict:
    return {"text_avoid": {"enabled": enabled, "cjk_only": True,
                           "refresh_seconds": 30.0, "max_travel": 700.0,
                           "run_speed": 1.5}}


def _force_left(behavior) -> None:
    """把方向掷骰钉成「向左」。

    假 sprite 默认朝左，向左走**不需要先播 turn**，计划一步进 move 态；否则
    ``_plan_move`` 会先 ``_enter_turn``（那是正确行为），断言就会依赖随机方向。
    """
    behavior.rng = ScriptedRng(choices=(-1,))


@pytest.fixture
def quiet_controller(monkeypatch):
    """控制器不许在测试里真起后台线程（抓屏 + OCR），几何接口用替身。

    ``window_physical_rect`` 必须桩：拿不到窗口矩形时控制器的**正确行为**是原样
    返回（降级契约），而需要"真决策"的用例必须给它一段几何。
    """
    monkeypatch.setattr(TextAvoidController, "start", lambda self: None)
    monkeypatch.setattr(TextAvoidController, "stop", lambda self: None)
    monkeypatch.setattr(text_avoid, "ocr_available", lambda: True)
    monkeypatch.setattr(text_avoid_controller.platform_win, "window_physical_rect",
                        lambda hwnd: (0, 0, 1600, 900))


def _ready(ctrl, boxes):
    """把一张文字图塞进控制器缓存（等价于一次刷新刚落地）。"""
    ctrl._boxes = tuple(boxes)
    ctrl._boxes_at = float("inf")


# ------------------------------------------------------- ① 降级契约（没数据不改行为）
def test_disabled_returns_passthrough(app, quiet_controller):
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(False), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)

    glue.sync()
    assert glue._ctrls == {}, "没启用就不该构造控制器"
    assert glue.plan(sprite, 1, 150.0, 400.0, 60.0) == (1, 150.0, 400.0, None, 1.0)


def test_enabled_but_ocr_missing_degrades(app, quiet_controller, monkeypatch):
    monkeypatch.setattr(text_avoid, "ocr_available", lambda: False)
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)

    glue.sync()
    assert glue._ctrls == {}
    assert glue.plan(sprite, -1, 120.0, 300.0, 60.0) == (-1, 120.0, 300.0, None, 1.0)


def test_stale_sprite_controllers_are_dropped(app, quiet_controller):
    lib = _make_library()
    a, b = _make_sprite(lib, pos=(400, 400)), _make_sprite(lib, pos=(900, 400))
    overlay = _Overlay([a, b])
    glue = SpriteTextAvoid(_Shell(_cfg(True), overlay))

    glue.sync()
    assert set(glue._ctrls) == {a, b}
    overlay.sprites = [a]
    glue.sync()
    assert set(glue._ctrls) == {a}, "已消失的 sprite 不该留着控制器"


# ------------------------------------------------------- ② 宿主适配的坐标口径
def test_host_geometry_is_global_logical(app):
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(300, 200), scale=0.5)
    bounds = QRect(0, 0, 1600, 900)
    overlay = _Overlay([sprite], pos=(0, 0))
    host = _SpriteHost(_Shell(_cfg(True), overlay, bounds), sprite, bounds)

    # sprite 没有 character_id → 身体框回退"画布即身体"，相对左上角即 (0,0,w,h)
    body = host._stable_body_local_rect()
    assert (body.x(), body.y()) == (0, 0)
    assert (body.width(), body.height()) == (sprite.rect().width(), sprite.rect().height())
    # vp = overlay 原点 + sprite 位置
    assert host._virtual_pos() == QPointF(300, 200)
    # 移动空间用的是与 _plan_move 同源的 body_reach，再平移到全局
    sp = host.move_space()
    assert sp.cx == pytest.approx(300 + body.width() / 2)
    assert sp.left <= sp.cx <= sp.right
    assert (sp.avail.x(), sp.avail.y()) == (0, 0)


def test_host_physical_origin_pairs_with_virtual_pos(app, quiet_controller):
    """宿主的物理原点必须与 ``_virtual_pos()`` **配对**（CoordMap 的锚全靠它）。

    这条是**实机抓出来的 bug 的回归守卫**：适配器第一版把"overlay 的物理矩形"配上
    "sprite 的逻辑位置"，于是 ``anchor = 逻辑 − 物理/缩放`` 被算成了 sprite 自己的
    逻辑位置——身体框与 OCR 条带整体偏一个 sprite 的距离（实测条带跑到 x=−820，
    几乎全在屏幕外），而单元测试当时全绿（假几何下偏移恰好看不出来）。
    """
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(300, 200), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite], pos=(0, 0)))
    glue = SpriteTextAvoid(shell)
    glue.sync()
    ctrl = glue._ctrls[sprite]
    host = glue._hosts[sprite]

    # 夹具：overlay 在原点、dpr=1、window_physical_rect 被桩成 (0,0,1600,900)
    rect = ctrl._host_physical_rect()
    vp = host._virtual_pos()
    assert (rect[0], rect[1]) == (int(vp.x()), int(vp.y())), \
        "物理原点必须与逻辑位置配对，否则 anchor 会整体错位"

    # 端到端口径：身体框的物理位置 == 逻辑位置 × dpr（本夹具 dpr=1）
    box = ctrl._geometry_box()
    assert box is not None
    assert box.footprint[0] == pytest.approx(vp.x()), "身体框物理左边界应当等于逻辑位置"
    assert box.footprint[1] == pytest.approx(vp.y())
    # 条带不许跑到屏幕外（错位时的症状）
    left, top, _w, _h = box.band
    assert left >= -1 and top >= -1, f"条带跑到屏幕外了：{box.band}"


def test_host_exposes_the_screen_for_band_math(app):
    """控制器算 OCR 条带要 ``host._screen_available()``。

    这条是**回归守卫**：适配器第一版漏了这个接口，`_current_band()` 直接返回
    None（几何"取不到"→ 功能静默不生效），而单元测试全绿也没发现——只有真机
    端到端跑一次才暴露。所以这里同时钉住接口存在与"能算出条带"。
    """
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(300, 200), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    host = _SpriteHost(shell, sprite, shell._bounds)
    screen = host._screen_available()
    assert screen is not None, "拿不到屏幕就永远算不出条带"
    assert screen.availableGeometry().width() > 0


def test_controller_can_actually_compute_a_band(app, quiet_controller):
    """端到端的最小版：宿主齐活时控制器**必须**算得出条带与身体框。"""
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(300, 200), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)
    glue.sync()
    ctrl = glue._ctrls[sprite]
    band = ctrl._current_band()
    assert band is not None, "几何齐活却算不出条带 = 适配器接口缺项"
    region, footprint = band
    assert region[2] > region[0] and region[3] > region[1]
    assert footprint[2] > footprint[0] and footprint[3] > footprint[1]


def test_host_geometry_follows_a_moved_overlay(app):
    """overlay 不在原点时，宿主必须把 sprite 局部坐标平移到全局。"""
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(100, 100), scale=0.5)
    bounds = QRect(0, 0, 1600, 900)
    host = _SpriteHost(_Shell(_cfg(True), _Overlay([sprite], pos=(260, 40)), bounds),
                       sprite, bounds)
    assert host._virtual_pos() == QPointF(360, 140)
    assert host.move_space().cx == pytest.approx(360 + sprite.rect().width() / 2)


def test_host_move_plan_reflects_the_state_machine(app, quiet_controller):
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    host = _SpriteHost(shell, sprite, shell._bounds)

    assert host._move_plan is None, "还没动作 = 不在路上"
    _force_left(shell.behavior)
    assert shell.behavior.play_move_once(sprite, "walk") is True
    assert shell.behavior.state_of(sprite) == STATE_MOVE
    assert host._move_plan is not None, "在路上时控制器据此拒绝再打断一次"


# ------------------------------------------------------- ③ 改道钩子真的进了计划
def test_plan_hook_redirects_and_reports_boost(app, quiet_controller):
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(800, 400), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)
    glue.sync()
    ctrl = glue._ctrls[sprite]

    # 身体框（画布即身体）落在 cx 附近 → 原目标压字；旁边留一块干净区
    _force_left(shell.behavior)
    sp = ctrl._host.move_space()
    # 字框是**绝对物理像素**（控制器内部就是把逻辑落点换算到这套坐标的）。
    # 本夹具：overlay 在原点、dpr=1 → 物理 == 全局逻辑；sprite 在 (800,400)，
    # 身体 320x180 → 物理 (800..1120, 400..580)；向左 60 的原落点身体 = (740..1060,
    # 400..580)。把字摆在那块上。
    _ready(ctrl, [TextBox("字", 800.0, 420.0, 1000.0, 560.0)])

    result = glue.plan(sprite, -1, 60.0, 400.0, 120.0)
    assert len(result) == 5
    dir_sign, distance, room, target_y, boost = result
    assert dir_sign in (-1, 1)
    # 往哪边改、还是只做纵向微调，都取决于哪边更近（y 优先是设计）；
    # 所以只断言"确实动了 + 只在这一趟给倍率"
    assert (distance, room) != (60.0, 400.0) or target_y is not None, \
        "原落点压着字，应当改道（横向或纵向至少动一处）"
    assert distance > 0.0 and room > 0.0
    assert boost == pytest.approx(1.5), "改道那次才给跑动速度（配置默认 1.5）"


def test_plan_hook_is_a_noop_when_the_target_is_already_clean(app, quiet_controller):
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(800, 400), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)
    glue.sync()
    _ready(glue._ctrls[sprite],
           [TextBox("离得远", 9000.0, 9000.0, 9100.0, 9100.0)])
    result = glue.plan(sprite, 1, 60.0, 400.0, 120.0)
    assert result[:3] == (1, 60.0, 400.0), "没压字就不动横向落点"
    assert result[4] == 1.0, "没改道就不该用跑动速度"
    assert result[3] is not None, "纵向仍锚定到核对过的那一条（与 legacy 同契约）"


def test_plan_hook_swallows_controller_failures(app, quiet_controller, monkeypatch):
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)
    glue.sync()

    def boom(*_a, **_k):
        raise RuntimeError("决策炸了")

    monkeypatch.setattr(glue._ctrls[sprite], "adjust_move", boom)
    assert glue.plan(sprite, 1, 60.0, 400.0, 120.0) == (1, 60.0, 400.0, None, 1.0), \
        "控制器异常绝不能影响移动本身"


# --------------------------------------------- ④ 行为层钩子的落点/倍率与立刻走开
def test_move_plan_uses_hook_target_and_boost(app):
    lib = _make_library()
    sprite = _make_sprite(lib, pos=(800, 400), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    host = _SpriteHost(shell, sprite, shell._bounds)
    body_w = host._stable_body_local_rect().width()
    seen = []

    def hook(received_sprite, dir_sign, distance, room, stride):
        seen.append((received_sprite, dir_sign, distance, room, stride))
        return (dir_sign, distance + 40.0, room, 250.0, 2.0)

    shell.behavior.text_avoid_plan = hook
    speeds = []
    sprite.set_move_anim_speed = lambda f: speeds.append(f)

    _force_left(shell.behavior)
    assert shell.behavior.play_move_once(sprite, "walk") is True
    assert len(seen) == 1
    got_sprite, dir_sign, distance, room, stride = seen[0]
    assert got_sprite is sprite, "钩子必须收到触发这次移动的那只 sprite"
    assert dir_sign == -1
    assert stride == pytest.approx(120 * 0.5), "步幅按 stride×scale 传入"
    assert speeds == [2.0], "跑动倍率必须落到 clip 上"
    st = shell.behavior._states[sprite]
    # 钩子给的距离还要过步幅整圈量化（请求 100px、步幅 60 → 真走 2 圈 = 120px），
    # 所以按 loops×stride 断言；同时要求不少于钩子请求的量（没被吞掉）。
    off_x, _off_y, body_w2, _body_h = host.body_geometry()
    cx = host.move_space().cx
    assert st.loops * stride >= distance + 40.0
    assert st.move_target.x() == pytest.approx(
        cx - st.loops * stride - body_w2 / 2 - off_x)
    assert st.move_target.y() == pytest.approx(250.0)


def test_move_plan_boost_shortens_the_duration(app):
    """跑动倍率必须同时缩短**单圈时长**，否则位移与动画失配（脚滑）。

    只比单圈时长：圈数由随机距离决定，跨次比较会假红。
    """
    lib = _make_library()
    plain = _make_sprite(lib, pos=(800, 400), scale=0.5)
    boosted = _make_sprite(lib, pos=(800, 400), scale=0.5)
    shell = _Shell(_cfg(True), _Overlay([plain, boosted]))
    _force_left(shell.behavior)
    assert shell.behavior.play_move_once(plain, "walk") is True
    base_loop = shell.behavior._states[plain].loop_duration
    assert base_loop > 0.0

    shell.behavior.text_avoid_plan = (
        lambda sp, d, dist, room, stride: (d, dist, room, None, 2.0))
    _force_left(shell.behavior)
    assert shell.behavior.play_move_once(boosted, "walk") is True
    st = shell.behavior._states[boosted]
    assert st.loop_duration == pytest.approx(base_loop / 2.0)
    assert st.duration == pytest.approx(st.loops * st.loop_duration)


def test_escape_signal_starts_a_move(app, quiet_controller, monkeypatch):
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    glue = SpriteTextAvoid(shell)
    glue.sync()

    calls = []
    monkeypatch.setattr(shell.behavior, "request_move_now",
                        lambda s: (calls.append(s), True)[1])
    glue._escape(sprite)
    assert calls == [sprite]


def test_request_move_now_refuses_when_not_normal_or_no_move(app):
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(True), _Overlay([sprite]))
    behavior = shell.behavior

    behavior.no_move = True
    assert behavior.request_move_now(sprite) is False
    behavior.no_move = False
    sprite.interaction_state = INTERACTION_DRAG
    assert behavior.request_move_now(sprite) is False
    sprite.interaction_state = INTERACTION_NORMAL
    _force_left(behavior)
    assert behavior.request_move_now(sprite) is True
    assert behavior.state_of(sprite) == STATE_MOVE


def test_unwired_behavior_is_unchanged(app):
    """没接线时，计划里不该出现任何避字字段（行为零变化）。"""
    lib = _make_library()
    sprite = _make_sprite(lib)
    shell = _Shell(_cfg(False), _Overlay([sprite]))
    assert shell.behavior.text_avoid_plan is None
    assert shell.behavior.play_move_once(sprite, "walk") is True
    st = shell.behavior._states[sprite]
    assert st.duration > 0.0
    assert SimpleNamespace(**{}) is not None  # 占位：本用例只做契约断言
