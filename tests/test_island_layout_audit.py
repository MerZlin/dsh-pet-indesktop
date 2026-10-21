# -*- coding: utf-8 -*-
"""灵动岛排版账目总审计（P2 Slice E7）——「同类排版问题一次查干净」的守卫测试。

起因（2026-10-07 用户 mandate）：状态点右半 6px 画出胶囊轮廓外——``trail`` 没有
与左侧 19px（6 内缩 + 13 边距）对应的右内容边距。本文件把 8 个审计面全部钉成
像素/几何断言：**真缺陷修到绿，合规项钉死现状防回归**。

审计面（每项注释里写明「声明账目」）：
1. 右内容边距 = 左 19px（状态点开/关两档）
2. 状态点完全落在胶囊轮廓内
3. 状态点垂直居中（胶囊中心 y=22）
4. 头像垂直居中
5. 段间竖线垂直居中
6. chip 文字在 chip 盒内光学居中
7. 主信息/数值/单位共用一条基线
8. 停靠细条状态点边距 / squish 形变下内容不出轮廓

像素断言一律 ``qAlpha``（``QColor(QRgb)`` 会丢 alpha 恒 255——E4 踩过的坑）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter, qAlpha
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import layout
from pet.config import Config
from pet.island.dynamic_island import _CAPSULE_INSET, DynamicIsland

app = QApplication.instance() or QApplication([])

_CENTER = 22.0          # 胶囊垂直中心（_CAPSULE_HEIGHT/2）
_MARGIN = 19            # 左右内容边距（6 内缩 + 13 内容边距）


class _FakeScreen:
    def __init__(self, width: int):
        self._rect = QRect(0, 0, width, 768)

    def availableGeometry(self):
        return self._rect


class _StubMonitor:
    """审计测试用的空采样器：有生命周期接口，永不出样本（段走四态兜底）。"""

    def __init__(self, interval):
        self.interval = interval
        self.sample = None
        self.subs = set()

    def subscribe(self, key):
        self.subs.add(key)

    def unsubscribe(self, key):
        self.subs.discard(key)

    def set_interval(self, interval):
        self.interval = interval

    def snapshot(self):
        return self.sample

    def start(self):
        pass

    def stop(self, wait=False):
        pass

    def close(self):
        pass


def _make_island(monkeypatch, *, metrics=(), screen_width=1200, **overrides):
    def factory(_self, interval):
        return _StubMonitor(interval)

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    screen = _FakeScreen(screen_width)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    cfg = Config(base=tempfile.mkdtemp())
    data = {"enabled": True, "show_icon": True, "show_name": True, "show_info": True,
            "info_mode": "time", "custom_text": "", "show_status": True,
            "style": "dark", "x": 400, "y": 300}
    data.update(overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return DynamicIsland(cfg), screen


def _fresh_content(island):
    """尺寸就位 + 强制重建内容层位图（不 show，离屏同样走真绘制）。"""
    island._update_size()
    app.processEvents()
    island._content_pixmap = None
    return island._content_cache().toImage()


def _ink_cols(img, thr=40):
    left = right = None
    for x in range(img.width()):
        if any(qAlpha(img.pixel(x, y)) > thr for y in range(img.height())):
            if left is None:
                left = x
            right = x
    return left, right


def _ink_rows(img, x, thr=40):
    rows = [y for y in range(img.height()) if qAlpha(img.pixel(x, y)) > thr]
    return (min(rows), max(rows)) if rows else (None, None)


def _ink_bounds(img, x0, x1, thr=40):
    """[x0,x1) 列区间内的墨行范围与墨列范围。"""
    rows, cols = [], []
    for x in range(x0, min(x1, img.width())):
        for y in range(img.height()):
            if qAlpha(img.pixel(x, y)) > thr:
                rows.append(y)
                cols.append(x)
    if not rows:
        return None
    return min(cols), max(cols), min(rows), max(rows)


def _segments_start_x(island) -> float:
    """复刻 _content_cache 的段起点账目：19 + 图标档 + 名字档。"""
    icon, name, _info, _status = island._visible_parts()
    plan = island._row_plan()
    x = float(_MARGIN)
    if icon:
        x += 26 + 8
    if name and plan.name:
        x += plan.name_w + 10
    return x


# ---------------------------------------------------------- 1/2. 右缘与状态点
def test_right_margin_matches_left_19px(monkeypatch, tmp_path):
    """右内容边距 == 左 19px：状态点右缘距窗口右缘 19（距胶囊轮廓 13）。

    缺陷账目（修复前）：``trail = 10+8``——状态点右缘 = 窗口宽，右半 6px 画出
    胶囊轮廓（轮廓内缩 6px）；左侧对应位置是 19px。修后 trail 无条件含 19。
    """
    island, _screen = _make_island(monkeypatch)
    try:
        img = _fresh_content(island)
        left, right = _ink_cols(img)
        w = island.width()
        assert left is not None and right is not None
        assert left >= _MARGIN - 1, f"左侧内容越界：最左墨列 {left} < 19"
        assert w - 1 - right >= _MARGIN - 1, (
            f"右侧内容边距缺失：最右墨列 {right}，距窗口右缘 {w - 1 - right}px（应 19）")
        # 对称是设计目标（左右同宽），不是"至少"——钉死精确值防未来再漂移
        assert abs((w - 1 - right) - (_MARGIN - 1)) <= 1, (
            f"右缘应与左 19px 对称：实测 {w - 1 - right}px")
    finally:
        island.close()


def test_right_margin_holds_when_status_off(monkeypatch, tmp_path):
    """show_status 关掉时末段右缘同样保 19px（缺陷是全配置通病，不只状态点）。"""
    island, _screen = _make_island(monkeypatch, show_status=False)
    try:
        img = _fresh_content(island)
        _left, right = _ink_cols(img)
        w = island.width()
        assert w - 1 - right >= _MARGIN - 1, (
            f"status 关时末段顶到窗口边：距右缘 {w - 1 - right}px（应 19）")
    finally:
        island.close()


def test_status_dot_stays_inside_capsule_outline(monkeypatch, tmp_path):
    """状态点整体在胶囊轮廓内：右缘 ≤ 窗口宽 − 6（轮廓内缩）− 余量。"""
    island, _screen = _make_island(monkeypatch)
    try:
        img = _fresh_content(island)
        _left, right = _ink_cols(img)
        w = island.width()
        assert right <= w - _CAPSULE_INSET - 1, (
            f"状态点/内容画出胶囊轮廓：最右墨列 {right} > {w - _CAPSULE_INSET - 1}")
    finally:
        island.close()


# ---------------------------------------------------------- 3-5. 垂直居中账目
def test_status_dot_vertically_centered(monkeypatch, tmp_path):
    """状态点 y 账目：(44−8)/2=18..26，中心 22 == 胶囊中心。"""
    island, _screen = _make_island(monkeypatch)
    try:
        img = _fresh_content(island)
        # 状态点 = 最右那团墨（与文字之间有 10px 槽位隔开）：取最右墨列回退 8px 量行
        _left, right = _ink_cols(img)
        assert right is not None, "状态点开时右端找不到任何墨"
        bounds = _ink_bounds(img, right - 7, right + 1)
        assert bounds is not None
        _c0, _c1, r0, r1 = bounds
        center = (r0 + r1) / 2.0
        assert abs(center - _CENTER) <= 1.0, f"状态点垂直中心 {center} 偏离 22"
    finally:
        island.close()


def test_icon_vertically_centered(monkeypatch, tmp_path):
    """头像 y 账目：(44−26)/2=9..35，中心 22。"""
    island, _screen = _make_island(monkeypatch)
    try:
        img = _fresh_content(island)
        icon_col = _MARGIN + 13  # 26px 图标的中列
        top, bottom = _ink_rows(img, icon_col)
        assert top is not None, "图标列无墨（头像底圈应画在这里）"
        center = (top + bottom) / 2.0
        assert abs(center - _CENTER) <= 1.0, f"头像垂直中心 {center} 偏离 22"
        assert top >= 8 and bottom <= 36, f"头像墨行 [{top},{bottom}] 越出声明 9..35±1"
    finally:
        island.close()


def test_divider_vertically_centered(monkeypatch, tmp_path):
    """竖线 y 账目：(44−14)/2=15..29，中心 22（修 E7 前被怀疑的 0.5px 差在此钉死）。"""
    island, _screen = _make_island(monkeypatch, metrics=["cpu", "mem"])
    try:
        img = _fresh_content(island)
        # 找竖线：段间 1px 宽、14px 高的低 alpha 列——用布局几何取精确 x
        segs = island._compose_info_segments()
        pl = layout.place_info_segments(
            segs, start_x=_segments_start_x(island),
            primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"),
            label_fm=island._metrics_for("label"))
        dividers = [p.divider_x for p in pl if p.divider_x is not None]
        assert dividers, "cpu+mem 两段之间应有竖线"
        top, bottom = _ink_rows(img, int(round(dividers[0])), thr=20)
        assert top is not None, "竖线列无墨（竖线 alpha=36，阈值要放到 20）"
        center = (top + bottom) / 2.0
        assert abs(center - _CENTER) <= 1.0, f"竖线垂直中心 {center} 偏离 22"
    finally:
        island.close()


def test_divider_fades_at_both_ends(monkeypatch, tmp_path):
    """竖线竖直渐变：两端 alpha=0、中段本色——线头收进段间空隙，不是硬切柱子。

    落位与尺寸一个字节没动（仍是 1×14、槽位正中、中心 22，见上一条），变的只是
    上下两端的 alpha 走法。两端的 alpha 必须**真的**归零，否则"收头"是空话。
    """
    island, _screen = _make_island(monkeypatch, metrics=["cpu", "mem"])
    try:
        img = _fresh_content(island)
        segs = island._compose_info_segments()
        pl = layout.place_info_segments(
            segs, start_x=_segments_start_x(island),
            primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"),
            label_fm=island._metrics_for("label"))
        dividers = [p.divider_x for p in pl if p.divider_x is not None]
        assert dividers, "cpu+mem 两段之间应有竖线"
        column = int(round(dividers[0]))
        band = int(round(_CENTER - 7)), int(round(_CENTER + 6))  # 15..28：14px 竖线
        alphas = [qAlpha(img.pixel(column, y)) for y in range(band[0], band[1] + 1)]
        assert alphas and max(alphas) >= 30, f"竖线中段必须是本色（实测 {alphas}）"
        # 渐变端点是像素边界（15.0 / 29.0），首末两行取的是各自行中心的值
        # （t≈0.036 → α≈4），所以"两端归零"落到像素上就是"≤ 本色的一成"。
        ends = (alphas[0], alphas[-1])
        assert max(ends) <= max(alphas) // 4, (
            f"竖线两端必须收到近零（实测两端 {ends}，本色 {max(alphas)}）")
        assert alphas.count(max(alphas)) >= 5, (
            f"本色区应覆盖中段（实测 {alphas}）")
    finally:
        island.close()


# ---------------------------------------------------------- 6. chip 光学居中
def test_chip_text_optically_centered_in_box(monkeypatch, tmp_path):
    """chip 文字在 18px chip 盒内**按字体度量光学居中**（D2 遗留项转正）。

    裁决：盒内基线 = 盒中心 + (ascent − descent)/2（QPainter 标准光学居中公式）。
    真机上（DirectWrite 度量正常）这把文字从「沿行基线 27、盒内偏低 ~1.5px」拉回
    盒中心；chip 是容器图元、不与行内文字比邻，统一基线规则不适用于盒内。

    为什么用 drawText 探针而不是像素墨中心：offscreen 后端字体度量退化
    （ascent=pixelSize、descent=0、字形挂在 ascent 线上），绝对像素行号在
    不同后端之间不可比；公式契约是跨后端不变的。
    """
    island, screen = _make_island(monkeypatch, metrics=["cpu", "mem", "network"])
    try:
        # 从宽往窄搜「折掉 ≥1 段」的档（真实字体实测，不写死屏宽）
        plan = None
        for width in range(1400, 200, -4):
            screen._rect = QRect(0, 0, width, 768)
            island.refresh_from_config()
            plan = island._row_plan()
            if plan.folded:
                break
        assert plan and plan.folded, "找不到折叠档（chip 不出现）"

        calls = []
        real_draw_text = QPainter.drawText

        def spy(self, *args):
            calls.append(args)
            return real_draw_text(self, *args)

        monkeypatch.setattr(QPainter, "drawText", spy)
        island._content_pixmap = None
        island._content_cache()

        chip_calls = [a for a in calls
                      if len(a) >= 2 and str(a[-1]).startswith("+")]
        assert chip_calls, f"没有找到 chip 文字的 drawText 调用（{len(calls)} 次绘制）"
        pos = chip_calls[0][0]
        label_fm = island._metrics_for("label")
        expected = (44 - 18) / 2.0 + 18 / 2.0 + (label_fm.ascent() - label_fm.descent()) / 2.0
        assert abs(pos.y() - expected) <= 0.6, (
            f"chip 文字基线 {pos.y():.2f} ≠ 盒内光学居中 {expected:.2f} "
            f"（盒中心 22 + (ascent {label_fm.ascent()} − descent {label_fm.descent()})/2）")
        # 牙齿（offscreen 度量退化使公式与行基线巧合相等，必须主动拆开两者）：
        # 把行基线的 _BASELINE_DY 改到 2.0——行文字基线变 24，而 chip 走盒内公式
        # 应保持 27 不动；若有人把 chip 改回沿行基线，这里立刻红。
        monkeypatch.setattr(island_mod, "_BASELINE_DY", 2.0)
        calls.clear()
        island._content_pixmap = None
        island._content_cache()
        chip_calls = [a for a in calls
                      if len(a) >= 2 and str(a[-1]).startswith("+")]
        assert chip_calls, "改 _BASELINE_DY 后 chip 文字没有被重画"
        moved = chip_calls[0][0].y()
        assert abs(moved - expected) <= 0.6, (
            f"chip 文字基线随行基线漂移到了 {moved:.2f}——它必须只跟盒内公式走 "
            f"（{expected:.2f}），不许沿行基线")
    finally:
        island.close()


# ---------------------------------------------------------- 7. 统一基线
def test_all_text_layers_share_one_baseline(monkeypatch, tmp_path):
    """主信息/数值/单位共用一条基线：墨底行差 ≤1px（E7 复核 E4 后仍成立）。

    喂 ok 快照让单位盒真正画出（6.1sol 建议 6：修复前设备段停在 sampling，
    单位根本不上墨，用例名不副实），并单独探测单位盒墨底。
    """
    import time as _time

    from pet import sys_monitor as sm

    island, _screen = _make_island(monkeypatch, metrics=["cpu", "mem"])
    try:
        island._sys_pending = set()
        island._sys_monitor.sample = sm.SysSample(
            cpu_percent=42.0, mem_percent=61.0, sampled_at=_time.monotonic())
        island._tick_sys()
        img = _fresh_content(island)
        segs = island._compose_info_segments()
        pl = layout.place_info_segments(
            segs, start_x=_segments_start_x(island),
            primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"),
            label_fm=island._metrics_for("label"))
        bottoms = []
        unit_bottoms = []
        for p in pl:
            if p.seg.kind == layout.KIND_CHIP:
                continue
            bounds = _ink_bounds(img, int(p.box_x), int(p.box_x + p.box_w))
            if bounds is not None:
                bottoms.append(bounds[3])
            if p.unit_text:
                ub = _ink_bounds(img, int(p.unit_x), int(p.unit_x + p.unit_w))
                assert ub is not None, f"{p.seg.seg_id} 声明了单位但单位盒无墨"
                unit_bottoms.append(ub[3])
        assert len(bottoms) >= 2, f"至少主信息 + 一个指标段可比（{bottoms}）"
        assert max(bottoms) - min(bottoms) <= 1, (
            f"各层基线不一致：墨底行 {bottoms}")
        assert unit_bottoms, "ok 快照下 cpu/mem 的单位盒必须有墨"
        assert max(unit_bottoms) - min(unit_bottoms) <= 1, (
            f"单位盒基线不一致：墨底行 {unit_bottoms}")
        assert max(abs(u - b) for u in unit_bottoms for b in bottoms) <= 1, (
            f"单位盒与数值盒不共底：单位 {unit_bottoms} vs 数值 {bottoms}")
    finally:
        island.close()


# ---------------------------------------------------------- 8. 细条与 squish
def test_dock_strip_status_dot_keeps_margins(monkeypatch, tmp_path):
    """停靠细条（竖条）：状态点不顶边——下方 8px 边距、水平居中（同族缺陷守卫）。"""
    island, _screen = _make_island(monkeypatch, dock_edge="left")
    try:
        island.show()
        app.processEvents()
        island._mode = "docked"
        island._hover_peek = False
        island._apply_fixed_size()
        app.processEvents()
        img = island.grab().toImage()
        w, h = img.width(), img.height()
        cx = w // 2
        # 细条整条是底板墨，「点不顶边」只能按颜色分：点所在行（h-8-4）应出现
        # 与底板不同的点色；点下方 8px 净空区（h-7..h-1）应全是均匀的底板色。
        def col_at(y):
            px = img.pixel(cx, y)
            return (px & 0xFFFFFF)
        dot_zone = {col_at(y) for y in range(h - 16, h - 8)}
        clear_zone = [col_at(y) for y in range(h - 7, h)]
        assert len(set(clear_zone)) <= 2, (
            f"细条状态点下方 8px 净空区有杂色（点顶到边了？）：{set(clear_zone)}")
        bg = clear_zone[0]
        assert any(c != bg for c in dot_zone), "细条底部找不到状态点的异色墨"
    finally:
        island.close()


def test_squish_keeps_content_inside_capsule(monkeypatch, tmp_path):
    """squish=0.85（果冻压扁极限）时内容墨仍在形变后的胶囊轮廓行/列内（±1px）。"""
    island, _screen = _make_island(monkeypatch)
    try:
        island._update_size()
        app.processEvents()
        island._squish = 0.85
        img = island.grab().toImage()
        rect, _radius = island._squished_capsule_rect()
        # 内容墨（文字/图标/点）：逐像素查轮廓外的墨——只查中部列（避开圆角区）
        x0, x1 = int(rect.left() + 20), int(rect.right() - 20)
        offenders = []
        for x in range(x0, x1):
            for y in range(img.height()):
                if y < rect.top() - 1 or y > rect.bottom() + 1:
                    if qAlpha(img.pixel(x, y)) > 60:
                        # 投影带（三层内收/下伸、纯黑 α≤28）是设计内图元，排除
                        if not (rect.bottom() + 1 < y <= rect.bottom() + 3
                                and qAlpha(img.pixel(x, y)) <= 80):
                            offenders.append((x, y))
        assert not offenders, f"squish 下内容画出胶囊轮廓：{offenders[:5]}"
    finally:
        island.close()
