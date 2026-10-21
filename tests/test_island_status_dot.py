# -*- coding: utf-8 -*-
"""灵动岛状态点五态体系（P2 Slice E1）契约测试。

改造前：8px 实心绿 = 「宠物可见」单语义。新语义 = 形状+颜色**双通道**五态，
优先级 **错误 > 待确认 > 工作 > 完成瞬态 > 空闲/隐藏**（同刻只画一个图元）：

- 空闲（宠物可见、无事件）= 主题次色实心点（不再是绿）；
- 完成 = 绿实心点：``success`` 事件进入时亮起，**4s 后回落**（单次 QTimer，
  回落后定时器销毁——停表纪律）；
- 宠物隐藏 / DSH 离线 = 主题次色**空心环**（1.5px 描边）；
- 待确认/待回答 = accent **空心环**（读 ``dsh_state`` 的 waiting 锁存位：
  gated 口径在 ``pet/dsh_state.py``，本层不碰原始事件流）；
- 错误 = 红实心 ``#F2777A`` + 感叹矢量（QPainterPath 两笔，不用字符 glyph）；
- 工作 = accent 实心点。

硬约束：8px 尺寸不变；状态点变化只 ``update()``，**永不进宽度白名单键**
（它是纯绘制态）。断言口径：像素取真——``_content_cache()`` 是真 QPainter 画出来
的位图，不用桩画家；只留状态点时，内容层里除它之外没有任何图元，圆心/环上像素
的取色才有唯一解释。
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QImage, QPainterPath, QPainter
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland

# dark 主题下的期望色：中性 = 主题次色、工作/待确认 = 主题色（默认 blue）
_NEUTRAL = "#a0aabe"
_ACCENT = "#40b8ff"
_DONE_GREEN = "#50dc78"
_ERROR_RED = "#f2777a"

# 收敛器的全部状态 + ""（该通道从未被喂过，纯桌宠/未接 DSH 场景）
_DSH_STATES = ("", "offline", "idle", "thinking", "working",
               "waiting_approval", "waiting_question", "success", "error")


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    """只留状态点的岛：内容层除状态点外零图元（像素断言的前提）。"""
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": False, "show_name": False,
        "show_info": False, "show_status": True, "info_mode": "time",
        "style": "dark", "x": 400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def _dot_style(island: DynamicIsland) -> tuple[str, str]:
    """(形状, 颜色) —— 绘制层解析出的唯一结果（十六进制便于真值表比对）。"""
    shape, color = island._status_dot_style()
    return shape, color.name()


def _expected(state: str, visible: bool, done: bool) -> tuple[str, str]:
    """规格真值表（错误 > 待确认 > 工作 > 空闲/隐藏；完成是空闲点位的 4s 替身）。"""
    if state == "error":
        return "glyph", _ERROR_RED
    if state in ("waiting_approval", "waiting_question"):
        return "hollow", _ACCENT
    if state in ("working", "thinking"):
        return "solid", _ACCENT
    if state == "offline" or not visible:
        return "hollow", _NEUTRAL
    if done:
        return "solid", _DONE_GREEN
    return "solid", _NEUTRAL


def _content_image(island: DynamicIsland) -> QImage:
    return island._content_cache().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _opaque_bbox(image: QImage, alpha_min: int = 40):
    """内容层里非空像素的外接框；只留状态点时它就是状态点本体。"""
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() >= alpha_min:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _near(color: QColor, expected: str, tol: int = 26) -> bool:
    want = QColor(expected)
    return (abs(color.red() - want.red()) <= tol
            and abs(color.green() - want.green()) <= tol
            and abs(color.blue() - want.blue()) <= tol)


def _dot_box(island: DynamicIsland):
    """(image, bbox)：状态点在内容层位图里的位置与尺寸。"""
    image = _content_image(island)
    bbox = _opaque_bbox(image)
    assert bbox is not None, "状态点没画出来（内容层全透明）"
    return image, bbox


def _center(bbox) -> tuple[int, int]:
    """状态点的几何中心（像素中心口径：外接框右下角算到 +1）。"""
    x0, y0, x1, y1 = bbox
    return (x0 + x1 + 1) // 2, (y0 + y1 + 1) // 2


# ---------------------------------------------------------------- 优先级真值表
def test_five_state_truth_table_all_combinations(tmp_path):
    """全组合真值表：8 态（+未喂态）× 宠物可见×2 × 完成瞬态×2。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        for state in _DSH_STATES:
            for visible in (True, False):
                for want_done in (True, False):
                    # 完成瞬态经公开路径进入：success 边沿亮绿；"工作"顶掉瞬态
                    island.set_dsh_state("working")
                    island.set_dsh_state("success")
                    if not want_done:
                        island.set_dsh_state("working")
                    island.set_dsh_state(state)
                    island.set_pet_visible(visible)
                    got = _dot_style(island)
                    want = _expected(state, visible, island._dot_done)
                    assert got == want, (
                        f"state={state!r} visible={visible} want_done={want_done} "
                        f"done={island._dot_done} → {got} != {want}")
        # success 边沿必须真亮绿（不是靠"读到 done=True"自我实现）
        island.set_pet_visible(True)
        island.set_dsh_state("working")
        island.set_dsh_state("success")
        assert _dot_style(island) == ("solid", _DONE_GREEN)
        # 更高优先级状态进来立即顶掉瞬态（不留过期绿点）
        island.set_dsh_state("error")
        assert island._dot_done is False
        assert _dot_style(island) == ("glyph", _ERROR_RED)
    finally:
        island.hide()
        island.deleteLater()


def test_error_outranks_everything(tmp_path):
    """错误不可自动恢复：宠物隐藏、完成瞬态、工作态都盖不过它。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("working")
        island.set_dsh_state("success")
        island.set_pet_visible(False)
        island.set_dsh_state("error")
        assert _dot_style(island) == ("glyph", _ERROR_RED)
        # 待确认 > 工作，且都是空心/实心两种形状通道
        island.set_dsh_state("waiting_approval")
        assert _dot_style(island) == ("hollow", _ACCENT)
        island.set_dsh_state("waiting_question")
        assert _dot_style(island) == ("hollow", _ACCENT)
        # 待确认也盖过隐藏：形状仍是 accent 环而不是中性环
        island.set_pet_visible(False)
        assert _dot_style(island) == ("hollow", _ACCENT)
    finally:
        island.hide()
        island.deleteLater()


def test_working_and_waiting_follow_accent_preset(tmp_path):
    """工作/待确认跟主题色走（换 accent 预设即换点色）。"""
    _qapp()
    island = _island(tmp_path, accent="pink")
    island.show()
    try:
        island.set_dsh_state("working")
        assert _dot_style(island) == ("solid", "#ff7ab8")
        island.set_dsh_state("waiting_approval")
        assert _dot_style(island) == ("hollow", "#ff7ab8")
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------------------ 形状像素
def test_rendered_pixels_cover_all_five_states(tmp_path):
    """双通道形状断言（真像素）：实心 / 空心环 / 红实心+感叹。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        # 空闲：中性实心 + 尺寸 8px 不变
        island.set_dsh_state("idle")
        image, bbox = _dot_box(island)
        w, h = bbox[2] - bbox[0] + 1, bbox[3] - bbox[1] + 1
        assert w == 8 and h == 8, f"状态点尺寸必须仍是 8px（实测 {w}×{h}）"
        cx, cy = _center(bbox)
        assert _near(image.pixelColor(cx, cy), _NEUTRAL), "空闲 = 中性实心点"

        # 工作：accent 实心
        island.set_dsh_state("working")
        image, bbox = _dot_box(island)
        cx, cy = _center(bbox)
        assert _near(image.pixelColor(cx, cy), _ACCENT), "工作 = accent 实心点"

        # 完成：绿实心（4s 瞬态内）
        island.set_dsh_state("success")
        image, bbox = _dot_box(island)
        cx, cy = _center(bbox)
        assert _near(image.pixelColor(cx, cy), _DONE_GREEN), "完成 = 绿实心点"

        # 隐藏 / 离线：中性空心环 —— 圆心不填、环上有色
        for state in ("offline", "hidden"):
            if state == "hidden":
                island.set_dsh_state("idle")
                island.set_pet_visible(False)
            else:
                island.set_dsh_state("offline")
                island.set_pet_visible(True)
            image, bbox = _dot_box(island)
            x0, y0, x1, y1 = bbox
            cx, cy = _center(bbox)
            assert image.pixelColor(cx, cy).alpha() < 20, \
                f"{state}：空心环圆心必须透明（而是 {image.pixelColor(cx, cy).name()}）"
            assert _near(image.pixelColor(x0 + 1, cy), _NEUTRAL), \
                f"{state}：环体应是中性色 1.5px 描边"
            assert (x1 - x0 + 1, y1 - y0 + 1) == (8, 8), \
                "空心环与实心点同一 8px 足迹"

        # 待确认：accent 空心环（形状同"隐藏"、颜色通道不同）
        island.set_pet_visible(True)
        island.set_dsh_state("waiting_approval")
        image, bbox = _dot_box(island)
        x0, y0, x1, y1 = bbox
        cx, cy = _center(bbox)
        assert image.pixelColor(cx, cy).alpha() < 20, "待确认 = 空心环"
        assert _near(image.pixelColor(x0 + 1, cy), _ACCENT), "待确认环是 accent 色"

        # 错误：红实心 + 感叹（圆心竖列上有近白笔画）
        island.set_dsh_state("error")
        image, bbox = _dot_box(island)
        x0, y0, x1, y1 = bbox
        cx = _center(bbox)[0]
        # 盘内离开笔画中轴的像素必须是纯红（底色不是红就是没画红实心）。
        # 取样点取 y0+5 而不是 y0+3：珠感高光盖住了上半约 1/3（y0+1..y0+4），
        # 那里的红已被白 α70 提亮，不再是"盘内底色"这个待测成分。
        assert _near(image.pixelColor(x0 + 2, y0 + 5), _ERROR_RED, tol=30), \
            "错误点底色必须是红 #F2777A"
        inks = [image.pixelColor(cx, y).blue() for y in range(y0, y1 + 1)]
        assert max(inks) > 170, "感叹矢量没画出来（中轴上没有近白笔画）"
    finally:
        island.hide()
        island.deleteLater()


def test_error_glyph_is_two_strokes(tmp_path):
    """感叹是两笔矢量路径（竖杠 + 圆点），不是字符 glyph。"""
    _qapp()
    path = island_mod._status_error_glyph_path(QRectF(0, 0, 8, 8))
    assert isinstance(path, QPainterPath)
    moves = [path.elementAt(i).type
             for i in range(path.elementCount())].count(
        QPainterPath.ElementType.MoveToElement)
    assert moves == 2, f"感叹必须两笔（实测 {moves} 笔）"

    # 渲染取证：圆心竖列上"近白"像素分成两段（杠 + 点），中间隔着红
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("error")
        image, bbox = _dot_box(island)
        x0, y0, x1, y1 = bbox
        cx = _center(bbox)[0]
        runs: list[list[int]] = []
        for y in range(y0, y1 + 1):
            if image.pixelColor(cx, y).blue() > 170:
                if runs and runs[-1][-1] == y - 1:
                    runs[-1].append(y)
                else:
                    runs.append([y])
        assert len(runs) == 2, f"近白笔画应分成两段（杠 / 点），实测 {runs}"
    finally:
        island.hide()
        island.deleteLater()


def test_glyph_path_is_drawn_once_only_for_error(tmp_path, monkeypatch):
    """绘制调用记录：感叹路径只在错误态被调一次，且拿到的是 8px 点矩形。"""
    _qapp()
    calls: list[QRectF] = []
    original = island_mod._status_error_glyph_path

    def spy(rect: QRectF) -> QPainterPath:
        calls.append(QRectF(rect))
        return original(rect)

    monkeypatch.setattr(island_mod, "_status_error_glyph_path", spy)
    island = _island(tmp_path)
    island.show()
    try:
        for state in ("idle", "working", "waiting_approval", "success", "offline"):
            island.set_dsh_state(state)
            island._content_cache()
        assert calls == [], "非错误态一次都不许调感叹路径"
        island.set_dsh_state("error")
        island._content_cache()
        assert len(calls) == 1, f"错误态应恰好画一次感叹（实测 {len(calls)}）"
        assert calls[0].width() == 8 and calls[0].height() == 8, \
            "感叹矢量按 8px 点矩形生成（尺寸不变）"
    finally:
        island.hide()
        island.deleteLater()


# --------------------------------------------------------------- 完成瞬态 4s
def test_done_flash_interval_is_4s_and_single_shot(tmp_path):
    """规格常量与定时器形态：4s、单次。"""
    _qapp()
    assert island_mod._DOT_DONE_MS == 4000
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("success")
        timer = island._dot_done_timer
        assert timer is not None and timer.isSingleShot()
        assert timer.interval() == 4000
        assert timer.isActive()
    finally:
        island.hide()
        island.deleteLater()


def test_done_flash_falls_back_and_destroys_timer(tmp_path, monkeypatch):
    """完成 → 4s 到点回落空闲，且定时器停表销毁（不真睡：压缩间隔 + 事件循环）。"""
    _qapp()
    monkeypatch.setattr(island_mod, "_DOT_DONE_MS", 40)
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("success")
        timer = island._dot_done_timer
        assert timer is not None
        deadline = time.monotonic() + 5.0
        while island._dot_done_timer is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        assert island._dot_done_timer is None, "回落之后不得留下空转定时器"
        assert island._dot_done is False
        assert timer.isActive() is False
        # 停表纪律的强断言：不是"停下"而是对象真的从 Qt 对象树上被销毁
        from PySide6.QtCore import QCoreApplication, QEvent
        import shiboken6
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(timer), "回落后单次定时器必须销毁（deleteLater）"
        assert _dot_style(island) == ("solid", _NEUTRAL), "回落后是空闲中性实心点"
    finally:
        island.hide()
        island.deleteLater()


def test_done_flash_survives_idle_edge_but_dies_on_work(tmp_path):
    """瞬态语义：success 之后的 idle 边沿不打断绿点；工作/待确认/错误立即接管。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("success")
        island.set_dsh_state("idle")
        assert _dot_style(island) == ("solid", _DONE_GREEN)
        assert island._dot_done_timer is not None

        island.set_dsh_state("working")
        assert _dot_style(island) == ("solid", _ACCENT)
        assert island._dot_done_timer is None, "被更高优先级顶掉时必须停表销毁"
        assert island._dot_done is False
    finally:
        island.hide()
        island.deleteLater()


def test_hidden_island_never_arms_flash(tmp_path):
    """隐藏即休眠：不可见时不点表（隐藏态零定时器空转）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        assert not island.isVisible()
        island.set_dsh_state("success")
        assert island._dot_done_timer is None
        assert island._dot_done is False
    finally:
        island.deleteLater()


def test_hide_stops_flash_timer(tmp_path):
    """闪完之前隐藏：定时器立刻停表销毁（下次显示不残留旧绿点）。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("success")
        assert island._dot_done_timer is not None
        island.hide()
        assert island._dot_done_timer is None
        assert island._dot_done is False
    finally:
        island.deleteLater()


# ---------------------------------------------------------- 工作态声纳环
def _frame(island: DynamicIsland) -> QImage:
    """真渲染一帧（走完整 paintEvent）：透明底 + 显式 render（同软陶笔用例口径）。"""
    image = QImage(island.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    island.render(image)
    return image


def _dot_rect(island: DynamicIsland) -> QRectF:
    rect = island._status_dot_rect
    assert rect is not None, "内容层没记下状态点矩形（画点路径没走到？）"
    return QRectF(rect)


def _outside_dot_diff(one: QImage, two: QImage, dot: QRectF) -> list[tuple[int, int]]:
    """两帧在**状态点之外**逐像素不同的位置——只有环带会出现在这里。"""
    hits = []
    for y in range(one.height()):
        for x in range(one.width()):
            if dot.contains(QPointF(x + 0.5, y + 0.5)):
                continue
            if one.pixelColor(x, y) != two.pixelColor(x, y):
                hits.append((x, y))
    return hits


def _radius(dot: QRectF, x: int, y: int) -> float:
    return math.hypot(x + 0.5 - dot.center().x(), y + 0.5 - dot.center().y())


def test_sonar_ring_curve_endpoints_and_shape():
    """曲线口径：半径线性 5→13，alpha 90→0 按 (1−t)³（端点 + 单调 + 钳位）。"""
    _qapp()
    assert island_mod._SONAR_R0_PX == 5.0 and island_mod._SONAR_R1_PX == 13.0
    assert island_mod._SONAR_ALPHA == 90
    assert island_mod._SONAR_PERIOD_MS == 1600
    assert island_mod._SONAR_TICK_MS == 33

    r0, a0 = island_mod.sonar_ring_params(0.0)
    assert (r0, a0) == (5.0, 90), f"起点必须是 (5, 90)，实测 {(r0, a0)}"
    r1, a1 = island_mod.sonar_ring_params(1.0)
    assert (r1, a1) == (13.0, 0), f"终点必须是 (13, 0)，实测 {(r1, a1)}"
    # 中点是 (1−0.5)³ = 0.125 的 alpha——不是线性衰减（否则 (1-t) 就够了）
    assert island_mod.sonar_ring_params(0.5) == (9.0, round(90 * 0.125))
    # 越界钳位（相位取模后的浮点尾巴不许把半径甩出区间）
    assert island_mod.sonar_ring_params(-3.0) == (5.0, 90)
    assert island_mod.sonar_ring_params(7.0) == (13.0, 0)

    previous = (0.0, 255)
    for step in range(1, 101):
        radius, alpha = island_mod.sonar_ring_params(step / 100.0)
        assert radius >= previous[0], "半径必须单调外扩"
        assert alpha <= previous[1], "alpha 必须单调收敛"
        previous = (radius, alpha)


def test_sonar_ring_runs_only_while_working(tmp_path):
    """起停判据：只有工作态起表；待确认/完成/错误/空闲/离线一律停表销毁。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("idle")
        assert island._sonar_timer is None, "空闲态不该有环（静止零定时器）"

        island.set_dsh_state("working")
        timer = island._sonar_timer
        assert timer is not None and timer.isActive()
        assert timer.isSingleShot() is False
        assert timer.interval() == island_mod._SONAR_TICK_MS

        # 工作态内部换字/重复喂值不重开表：重开就把相位踩回 0，节拍被踩碎
        island.set_dsh_state("thinking")
        assert island._sonar_timer is timer
        island.set_dsh_state("working")
        assert island._sonar_timer is timer

        for state in ("idle", "success", "waiting_approval", "error", "offline"):
            island.set_dsh_state("working")
            assert island._sonar_timer is not None, f"{state} 之前应还在播"
            island.set_dsh_state(state)
            assert island._sonar_timer is None, f"{state}：非工作态必须停表"
            assert timer.isActive() is False

        # 旧粗粒度通道同样进工作态：环与状态点同源同判据
        island.set_agent_active(True)
        assert island._sonar_timer is not None
        island.set_agent_active(False)
        assert island._sonar_timer is None

        # 桌宠隐藏**不**停环：工作态在五态优先级里压过「隐藏」（见 _status_dot_state），
        # 点本身还是 accent 实心点，环与点同判据所以照播
        island.set_dsh_state("working")
        island.set_pet_visible(False)
        assert _dot_style(island)[0] == "solid" and island._sonar_timer is not None
        # 但"空闲 + 隐藏"是空心环：环跟着收（工作信号不出现在隐藏态）
        island.set_dsh_state("idle")
        assert _dot_style(island)[0] == "hollow"
        assert island._sonar_timer is None
    finally:
        island.hide()
        island.deleteLater()


def test_sonar_ring_stops_on_hide_expand_and_dock(tmp_path):
    """隐藏 / 展开卡片 / 收进细条三条停表路径，以及回到自由态后的重判。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("working")
        assert island._sonar_timer is not None

        island.hide()
        assert island._sonar_timer is None, "hideEvent 必须停表（隐藏态零空转）"
        island.show()
        assert island._sonar_timer is not None, "显示回来工作态还在 → 环接着播"

        island.expand_card()
        assert island._mode == "expanded"
        assert island._sonar_timer is None, "展开卡片必须停表（读者在别处）"
        island.collapse_card(animate=False)
        assert island._mode == "normal"
        assert island._sonar_timer is not None, "收回自由胶囊才可能重新播"

        # 停靠细条：另一种图元语言，不播环（走真配置路径：改 dock_edge + 重应用位置）
        island.config.set("dynamic_island", dict(island._cfg, dock_edge="left"))
        island.refresh_from_config()
        assert island._mode == "docked"
        assert island._sonar_timer is None, "停靠细条态不许播环"
    finally:
        island.hide()
        island.deleteLater()


def test_sonar_ring_timer_is_destroyed_and_never_leaks(tmp_path):
    """停表纪律：不是"停下"而是对象真的从 Qt 对象树上消失；反复起停不留常驻表。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("working")
        timer = island._sonar_timer
        assert timer is not None
        island.set_dsh_state("idle")
        assert island._sonar_timer is None and timer.isActive() is False
        _process_deferred_delete()
        import shiboken6
        assert not shiboken6.isValid(timer), "停表必须 deleteLater 销毁定时器"

        baseline = len(island.findChildren(QTimer))
        for _ in range(20):
            island.set_dsh_state("working")
            island.set_dsh_state("idle")
        _process_deferred_delete()
        assert len(island.findChildren(QTimer)) == baseline, (
            "反复起停必须不留常驻定时器（对象树上的数量不变）")
    finally:
        island.hide()
        island.deleteLater()


def test_sonar_ring_is_painted_outside_the_content_cache(tmp_path):
    """环画在缓存之外、只在环带落墨：内容层位图里没有它，缓存也不因它失效。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_dsh_state("working")
        island._finish_animations()   # 起态时的弹跳位移会整帧挪位，先归零再取像
        cached = island._content_cache()
        working = _frame(island)
        dot = _dot_rect(island)
        # 环每帧重画，但内容层位图必须原封不动（环不进缓存、也不进缓存键）
        for _ in range(3):
            _frame(island)
        assert island._content_cache() is cached, "每帧一笔环不得让内容层位图失效"

        island.set_dsh_state("idle")   # 离开工作态 → 停表，同一颗岛不再画环
        idle = _frame(island)

        diff = _outside_dot_diff(working, idle, dot)
        assert len(diff) >= 8, "工作态必须真的画出环（与空闲帧的这些差异就是环）"
        far = [p for p in diff if _radius(dot, *p) > island_mod._SONAR_R1_PX + 1.5]
        assert not far, f"环墨跑到半径 {island_mod._SONAR_R1_PX} 之外：{far[:4]}"
        assert min(_radius(dot, *p) for p in diff) > 3.5, "环不许盖在 8px 点上"
        assert _outside_dot_diff(working, idle, dot) == diff, "前提：帧渲染是确定的"

        # 内容层：环带在缓存位图里一个像素都没有（环是帧级图元）
        content = _content_image(island)
        for x, y in diff:
            assert content.pixelColor(x, y).alpha() == 0, (
                f"({x},{y}) 内容层里不该有环（环不进缓存，也不进缓存键）")

        # 空闲帧与"从没进过工作态的同配置岛"逐像素一致：离开工作态后环零残留
        fresh = _island(tmp_path)
        fresh.show()
        try:
            fresh._finish_animations()
            reference = _frame(fresh)
            assert _outside_dot_diff(idle, reference, dot) == [], (
                "离开工作态后帧里必须零环残留")
        finally:
            fresh.hide()
            fresh.deleteLater()
    finally:
        island.hide()
        island.deleteLater()


def test_sonar_ring_never_touches_width_or_plan_inputs(tmp_path):
    """环是纯时间绘制态：起停与相位变化一个字节都不许进宽度白名单/行计划键。"""
    _qapp()
    island = _island(tmp_path, show_info=True, info_mode="custom", custom_text="测试")
    island.show()
    try:
        island.set_dsh_state("idle")
        width = island.width()
        plan_inputs = island._plan_inputs()

        island.set_dsh_state("working")
        island._sonar_started -= 0.7   # 相位推到"动画中"（半径/alpha 都变了）
        _frame(island)
        assert island.width() == width, "环改了胶囊宽度（它不该进宽度白名单）"
        assert island._plan_inputs() == plan_inputs, "环进了行计划输入键"
        assert island.dock_edge == "none" and island._mode == "normal"
    finally:
        island.hide()
        island.deleteLater()


def _process_deferred_delete() -> None:
    _qapp().processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# -------------------------------------------------------------- 宽度白名单
def test_state_change_rebuilds_content_but_never_touches_width(tmp_path):
    """状态点变化只重建内容层位图：宽度与行计划输入键一个字节都不动。"""
    _qapp()
    island = _island(tmp_path, show_info=True, info_mode="custom", custom_text="测试")
    island.show()
    try:
        width = island.width()
        plan_inputs = island._plan_inputs()
        for state in _DSH_STATES:
            island.set_dsh_state(state)
            assert island.width() == width, f"{state}：状态点改了胶囊宽度"
            assert island._plan_inputs() == plan_inputs, f"{state}：状态点进了宽度键"
        # 形状/颜色确实进了**内容**缓存键：实心→空心（同色）也必须换位图
        island.set_dsh_state("idle")
        solid = island._content_cache()
        island.set_dsh_state("offline")
        hollow = island._content_cache()
        assert hollow is not solid, "空心环与实心点同色也必须各自成键（形状改像素）"
        island.set_dsh_state("error")
        assert island._content_cache() is not hollow, "错误（红+感叹）必须重建位图"
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------------------ 停靠细条
def test_docked_strip_draws_same_status_element(tmp_path):
    """停靠细条与胶囊同一图元语言：实心/空心在细条里同样成立。"""
    _qapp()
    island = _island(tmp_path, edge_dock=True, dock_edge="left")
    try:
        island.show()
        island._apply_position()
        assert island._mode == "docked"

        def render() -> QImage:
            target = QImage(island.size(), QImage.Format.Format_ARGB32)
            target.fill(QColor(0, 0, 0, 0))
            painter = QPainter(target)
            island._paint_strip(painter)
            painter.end()
            return target

        # 竖条：状态点在底部（几何沿用既有 8px 点）
        cx = island.width() // 2
        cy = island.height() - 8 - 8 + 4
        island.set_dsh_state("idle")
        assert _near(render().pixelColor(cx, cy), _NEUTRAL), "细条里空闲仍是中性实心"

        island.set_dsh_state("offline")
        strip = render()
        assert not _near(strip.pixelColor(cx, cy), _NEUTRAL, tol=10), \
            "细条里隐藏态必须是空心环（圆心不填）"
        assert _near(strip.pixelColor(cx - 3, cy), _NEUTRAL), "环体在细条里同样可见"

        island.set_dsh_state("waiting_approval")
        strip = render()
        assert _near(strip.pixelColor(cx - 3, cy), _ACCENT), "细条环跟 accent 走"
        island.set_dsh_state("error")
        # 杠与点之间的红色分隔行必须是底色红（形状通道在细条里同样成立）
        assert _near(render().pixelColor(cx, cy + 1), _ERROR_RED, tol=30)
    finally:
        island.hide()
        island.deleteLater()


# -------------------------------------------------------------- 旧通道兼容
def test_legacy_agent_active_adapter(tmp_path):
    """旧粗粒度活跃位（迁移期兼容层）：True → 工作实心；False 下降沿 → 完成瞬态。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_agent_active(True)
        assert _dot_style(island) == ("solid", _ACCENT)
        island.set_agent_active(False)
        assert _dot_style(island) == ("solid", _DONE_GREEN)
        assert island._dot_done_timer is not None
    finally:
        island.hide()
        island.deleteLater()


def test_legacy_and_dsh_channels_do_not_fight(tmp_path):
    """DSH 通道优先：旧活跃位留着 True 也不许顶掉错误/待确认。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        island.set_agent_active(True)
        island.set_dsh_state("error")
        assert _dot_style(island) == ("glyph", _ERROR_RED)
        island.set_dsh_state("waiting_approval")
        assert _dot_style(island) == ("hollow", _ACCENT)
        # 收敛态缺席（纯桌宠/未接 DSH）时旧活跃位补位：True → 工作实心
        island.set_dsh_state("")
        assert _dot_style(island) == ("solid", _ACCENT)
        # 下降沿 → 完成瞬态（旧通道唯一的"任务结束"边沿）
        island.set_agent_active(False)
        assert _dot_style(island) == ("solid", _DONE_GREEN)
    finally:
        island.hide()
        island.deleteLater()


def test_status_dot_off_config_draws_nothing(tmp_path):
    """show_status 关掉：状态点一个像素都不画（配置闸门不因五态改造松动）。"""
    _qapp()
    island = _island(tmp_path, show_status=False, show_info=True,
                     info_mode="custom", custom_text="测试")
    island.show()
    try:
        island.set_dsh_state("error")
        error_img = _content_image(island)
        island.set_dsh_state("working")
        assert _content_image(island) == error_img, \
            "关掉状态点后，状态变化不得再影响内容层"
        reds = [(x, y)
                for y in range(error_img.height())
                for x in range(error_img.width())
                if _near(error_img.pixelColor(x, y), _ERROR_RED, tol=20)]
        assert not reds, f"关掉状态点仍画出了错误红点：{reds[:1]}"
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------------- 事件源接线（全链）
def test_appshell_forwards_converged_state_verbatim(tmp_path):
    """接线契约：AppShell 把**收敛态原文**交给岛，不再压成活跃布尔。"""
    _qapp()
    import pet.app as app_mod
    from PySide6.QtWidgets import QWidget

    class _StubIsland(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.states: list[str] = []

        def set_dsh_state(self, state: str) -> None:
            self.states.append(state)

    class _StubShell:
        island = _StubIsland()

    shell = _StubShell()
    try:
        for from_state, to_state in (("", "idle"), ("idle", "thinking"),
                                     ("thinking", "waiting_approval"),
                                     ("waiting_approval", "success"),
                                     ("success", "error"), ("error", "offline")):
            app_mod.AppShell._on_dsh_state_changed(shell, from_state, to_state)
        assert shell.island.states == ["idle", "thinking", "waiting_approval",
                                       "success", "error", "offline"]
    finally:
        shell.island.deleteLater()


def test_dsh_converger_to_dot_end_to_end(tmp_path):
    """全链：真收敛器（pet/dsh_state.py）产出的状态 → 状态点五态。

    覆盖 gated 口径：``approval/asked`` 这类会话级审计信号**不**算待确认，
    带 requestId 的 ``cordis/request-run`` 才算。
    """
    _qapp()
    from pet.agent_link.dsh_state import DshStateConverger

    converger = DshStateConverger()
    island = _island(tmp_path)
    island.show()

    def feed(record: dict) -> None:
        for item in converger.handle_record(record):
            if item[0] == "state":
                island.set_dsh_state(item[2])

    try:
        # 探活基线：在线且无活动 → 空闲中性实心点
        for item in converger.set_online(True):
            if item[0] == "state":
                island.set_dsh_state(item[2])
        assert _dot_style(island) == ("solid", _NEUTRAL)

        feed({"event": "tool/call"})
        assert _dot_style(island) == ("solid", _ACCENT), "工作 = accent 实心"
        feed({"event": "turn/end"})
        assert _dot_style(island) == ("solid", _DONE_GREEN), "完成 = 绿实心"

        # 会话级审计信号不锁存：点色不因它变成待确认空心环
        feed({"event": "approval/asked"})
        assert _dot_style(island) == ("solid", _DONE_GREEN)

        # 真 UI 级审批（桥只在这种形态下写：payload.requiresApproval 严格 True，
        # 带 requestId）→ accent 空心环
        feed({"event": "cordis/request-run", "requestId": "r-1",
              "payload": {"requiresApproval": True}})
        assert _dot_style(island) == ("hollow", _ACCENT)

        # 审批锁存期间的非阻塞事件被收敛器忽略（锁存语义，不是点的 bug）
        feed({"event": "llm_error"})
        assert _dot_style(island) == ("hollow", _ACCENT)
        feed({"event": "cordis/request-run-resolved", "requestId": "r-1"})
        assert _dot_style(island) == ("solid", _ACCENT), "解锁回工作"

        # 错误 → 红实心 + 感叹
        feed({"event": "llm_error"})
        assert _dot_style(island) == ("glyph", _ERROR_RED)

        # DSH 退出（探活掉线）→ 离线空心环
        for item in converger.set_online(False):
            if item[0] == "state":
                island.set_dsh_state(item[2])
        assert _dot_style(island) == ("hollow", _NEUTRAL)
    finally:
        island.hide()
        island.deleteLater()
