# -*- coding: utf-8 -*-
"""灵动岛段**告警色体系 + chip 告警点**（P2 Slice E2）契约测试。

规格（施工单 §E2 / 裁决稿 §三，ops5.5 独有采纳）：CPU warn 80 / crit 92、内存
warn 85 / crit 95，**滞回 3pt**（升过线才上色、降过线 −3 才褪色）；只染**数值**
（含 E4 单位盒跟随值色），label / 段间竖线 / 段底一律不沾；网络段不做告警色
（延迟/速率没有普适阈值，裁决稿未要求）。折叠进 ``+N`` 的段自带告警时，chip
左侧点亮 4px 色点（取被折段的**最高**告警级），``ShrinkPlan.chip_alert`` 因此
从 bool 升格为 ``""|warn|crit``。

四条硬约束：

1. **滞回是状态的**：上一拍 severity 落在岛实例上（``_alert_severity``），不进
   段对象历史——同一个读数「91」，从 92 降下来是 crit、从 80 升上来是 warn；
2. **severity 进内容键、不进宽度键**：色变必须重建位图，但一个像素都不许改宽
   （chip 色点画在 chip 盒**既有**内边距里，不另占位）；
3. **只染数值**：告警色像素只许出现在数值盒 + 单位盒；
4. **无读数不褪色**：折叠态停采样时上一拍告警保持（与四态兜底 ``--`` 不伪造 0
   同口径），chip 告警点不会因为"被折段不再采样"而熄灭。

采样器一律替身（本文件不验采样生命周期，见 tests/test_island_device_slot.py）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import layout
from pet.island import segments as isl
from pet import net_monitor, sys_monitor
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.layout import _InfoSeg, place_info_segments, plan_info_row
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])

#: 深色主题的两级告警色（施工单 §E2 的规格值）。
_WARN_DARK = "#f0b44c"
_CRIT_DARK = "#f2777a"
#: 墨阈值：滤掉抗锯齿外溢（0-255 的 alpha）。
_INK = 40
#: 告警色匹配容差（抗锯齿边缘落在两色之间，只认字形内部像素）。
_TOL = 24

_PRIMARY = "primary"


class FakeMetrics:
    """字体度量替身：每字符固定 ``step`` px（只验几何，不碰真实字体）。"""

    def __init__(self, step: int = 10):
        self.step = step

    def horizontalAdvance(self, text) -> int:
        return len(str(text)) * self.step

    def height(self) -> int:
        return 17

    def lineSpacing(self) -> int:
        return 20

    def ascent(self) -> int:
        return 13

    def descent(self) -> int:
        return 4

    def leading(self) -> int:
        return 0

    def averageCharWidth(self) -> int:
        return self.step


FM = FakeMetrics(10)
_LEAD = 19.0
_TRAIL = 0.0
_NAME = "小鱼儿"


class FakeScreen:
    """屏幕替身：岛只问 ``availableGeometry()``（窄屏收缩的输入）。"""

    def __init__(self, width: int, height: int = 768):
        self._rect = QRect(0, 0, width, height)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


class StubMonitor:
    """采样器替身：只记录生命周期，样本由测试塞进去。"""

    def __init__(self, interval: float = net_monitor.DEFAULT_INTERVAL_SECONDS):
        self.interval = interval
        self.sample = None
        self.starts = 0
        self.stops = 0
        self.subscribed: set[str] = set()

    def start(self) -> bool:
        self.starts += 1
        return True

    def stop(self, **_kwargs) -> None:
        self.stops += 1

    def close(self) -> None:
        pass

    def set_interval(self, interval: float) -> float:
        self.interval = interval
        return self.interval

    def subscribe(self, key: str) -> int:
        self.subscribed.add(str(key))
        return len(self.subscribed)

    def unsubscribe(self, key: str) -> int:
        self.subscribed.discard(str(key))
        return len(self.subscribed)

    def snapshot(self):
        return self.sample


def _island(tmp_path, monkeypatch, *, metrics=(CPU,), screen_width=1400, **overrides):
    """建岛并把两条采样路径换成替身、屏幕换成可调宽的替身。"""
    made: list[StubMonitor] = []

    def factory(_self, interval):
        monitor = StubMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    screen = FakeScreen(screen_width)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)

    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": False, "show_name": False, "show_info": True,
        "info_mode": "custom", "custom_text": "固定", "show_status": False,
        "style": "dark", "x": 40, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return DynamicIsland(cfg), made, screen


def _visible(island: DynamicIsland) -> DynamicIsland:
    """显示并把首轮订阅跑完（段才拿得到快照）。"""
    island.show()
    app.processEvents()
    return island


def _sys_sample(cpu=42.0, mem=61.0, *, state=isl.STATE_OK, reason=""):
    return sys_monitor.SysSample(
        cpu_percent=cpu, mem_percent=mem, sampled_at=1.0, state=state, error_reason=reason)


def _feed(island, made, sample) -> None:
    made[0].sample = sample
    island._tick_sys()


def _segment(island, seg_id: str) -> _InfoSeg:
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}")


def _placement(island, seg_id: str):
    """当刻行内落位（与绘制层同一条路径：行计划 + 当刻段列表）。"""
    plan = island._row_plan()
    row = layout.apply_shrink(plan, island._compose_info_segments(),
                              primary_fm=island._metrics_for("primary"))
    for placement in place_info_segments(
            row, start_x=plan.segments_x,
            primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"),
            label_fm=island._metrics_for("label")):
        if placement.seg.seg_id == seg_id:
            return placement
    raise AssertionError(f"行里没有 {seg_id!r}")


def _image(island) -> QImage:
    return island._content_cache().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _columns(image, island, x0: float, x1: float) -> tuple[int, int]:
    """逻辑坐标 ``[x0, x1)`` → 位图像素列区间（左闭右开）。"""
    scale = image.width() / float(island.width())
    return max(0, int(round(x0 * scale))), min(image.width(), int(round(x1 * scale)))


def _count_color(image, expected: str, *, island=None, x0=None, x1=None) -> int:
    """位图里与 ``expected`` 匹配（容差内 + 有墨）的像素数。"""
    want = QColor(expected)
    left, right = 0, image.width()
    if island is not None:
        left, right = _columns(image, island, x0, x1)
    count = 0
    for py in range(image.height()):
        for px in range(left, right):
            pixel = image.pixelColor(px, py)
            if pixel.alpha() < _INK:
                continue
            if (abs(pixel.red() - want.red()) <= _TOL
                    and abs(pixel.green() - want.green()) <= _TOL
                    and abs(pixel.blue() - want.blue()) <= _TOL):
                count += 1
    return count


def _narrow_to(island, screen, predicate, *, lo=200, hi=1600, step=4):
    """把岛挪到"收缩级满足 ``predicate``"的那块屏上（从宽往窄搜）。

    屏宽按**真实字体实测**搜索而不是写死 px：段宽来自当前系统字体，写死阈值
    换台机器就落到别的收缩级上。
    """
    for width in range(hi, lo, -step):
        screen._rect = QRect(0, 0, width, 768)
        island.refresh_from_config()
        plan = island._row_plan()
        if predicate(plan):
            return width, plan
    raise AssertionError("在 200–1600px 的屏宽里找不到满足条件的收缩级")


# ------------------------------------------------------- 阈值/滞回真值表（纯函数）
def test_thresholds_are_the_spec_values():
    """两级阈值做模块内部常量；网络段刻意没有阈值（不做告警色）。"""
    assert isl.ALERT_THRESHOLDS[CPU] == (80.0, 92.0)
    assert isl.ALERT_THRESHOLDS[MEM] == (85.0, 95.0)
    assert isl.ALERT_HYSTERESIS == 3.0
    assert isl.alert_thresholds(NETWORK) is None, "延迟/速率没有普适阈值"
    assert isl.alert_thresholds("gpu") is None, "未声明的段没有告警色"


def _sweep(metric: str, values) -> list[str]:
    """把一串读数按**上一拍**依次喂进纯函数（滞回是有状态的，必须串行）。"""
    previous = ""
    sequence = []
    for value in values:
        previous = isl.alert_severity(metric, value, previous)
        sequence.append(previous)
    return sequence


def test_boundary_sweep_79_80_92_91_79():
    """边界全程：79→80→92→91→79。91 停在 crit（92−3=89 之上未褪），
    79 从 crit 落到 warn（80−3=77 之上未褪）——防的就是 79/80 反复横跳闪色。"""
    assert _sweep(CPU, (79.0, 80.0, 92.0, 91.0, 79.0)) == ["", "warn", "crit", "crit", "warn"]
    # 继续降：88（< 89）才从 crit 落回 warn，76（< 77）才褪回无色
    assert _sweep(CPU, (92.0, 91.0, 90.0, 89.0, 88.0)) == ["crit", "crit", "crit", "crit", "warn"]
    assert _sweep(CPU, (80.0, 78.0, 77.0, 76.0)) == ["warn", "warn", "warn", ""]


def test_hysteresis_is_per_metric():
    """内存的线是 85/95：同一个 93 在 CPU 是 crit、在内存是 warn。"""
    assert isl.alert_severity(CPU, 93.0) == "crit"
    assert isl.alert_severity(MEM, 93.0) == "warn"
    assert _sweep(MEM, (85.0, 95.0, 93.0, 91.0, 84.0, 81.0)) == \
        ["warn", "crit", "crit", "warn", "warn", ""]


def test_exact_threshold_edges():
    """线上取色：``>= 80`` 上色、``>= 92`` 升 crit；降到线上但未过 −3 不褪。"""
    assert isl.alert_severity(CPU, 79.999) == ""
    assert isl.alert_severity(CPU, 80.0) == "warn"
    assert isl.alert_severity(CPU, 91.999) == "warn"
    assert isl.alert_severity(CPU, 92.0) == "crit"
    assert isl.alert_severity(CPU, 89.0, "crit") == "crit"
    assert isl.alert_severity(CPU, 77.0, "warn") == "warn"
    assert isl.alert_severity(CPU, 200.0) == "crit", "越界读数按最高级"
    assert isl.alert_severity(CPU, -5.0) == "", "负值不触发"


def test_no_reading_never_fades_the_alert():
    """无读数不褪色：None / NaN / 非数字 原样返回上一拍（-- 不伪造 0 同口径）。"""
    for value in (None, float("nan"), float("inf"), "abc", object()):
        assert isl.alert_severity(CPU, value, "crit") == "crit", value
        assert isl.alert_severity(CPU, value, "warn") == "warn", value
        assert isl.alert_severity(CPU, value) == "", "没有上一拍就没有告警"


def test_metrics_without_thresholds_never_alert():
    """网络段恒无告警（哪怕上一拍被别处写成 warn）。"""
    assert isl.alert_severity(NETWORK, 9999.0, "crit") == ""
    assert isl.alert_severity("gpu", 100.0, "warn") == ""


def test_highest_severity_picks_the_top_level():
    assert isl.highest_severity(()) == ""
    assert isl.highest_severity(["", ""]) == ""
    assert isl.highest_severity(["", "warn"]) == "warn"
    assert isl.highest_severity(["warn", "crit", ""]) == "crit"
    assert isl.highest_severity(["crit", "warn"]) == "crit"
    assert isl.highest_severity(["", "bogus"]) == "", "未识别的级别当无色"


# ------------------------------------------------------------ chip 告警点（纯布局）
def _primary():
    return _InfoSeg(kind="primary", seg_id=_PRIMARY, label="", text="12:00",
                    box_text="88:88", state=isl.STATE_OK)


def _metric(seg_id, text="24%", box="888%", *, state=isl.STATE_OK, severity=""):
    return _InfoSeg(kind="metric", seg_id=seg_id, label=seg_id, text=text,
                    box_text=box, state=state, severity=severity)


def _plan(segments, available):
    return plan_info_row(
        segments, lead_w=_LEAD, trail_w=_TRAIL, name_text=_NAME, available_w=available,
        primary_fm=FM, value_fm=FM, label_fm=FM, name_fm=FM)


def _plan_folding(segments, count):
    """搜到"**刚好**折掉 ``count`` 段"的那个预算（从宽往窄，命中即最松的预算）。

    写死 px 不行：段宽来自字体度量，换台机器就落到别的收缩级上。
    """
    for available in range(1000, 40, -5):
        plan = _plan(segments, available)
        if len(plan.folded) == count:
            return plan
    raise AssertionError(f"找不到折掉 {count} 段的预算")


def test_chip_alert_carries_the_highest_severity():
    """``chip_alert`` 从 bool 升格为告警级：取被折段的最高级，无色时是空串。"""
    plain = [_primary(), _metric(CPU), _metric(MEM)]
    assert _plan_folding(plain, 2).chip_alert == "", "两段都无色 → 不点灯"

    warned = [_primary(), _metric(CPU, severity=isl.ALERT_WARN), _metric(MEM)]
    assert _plan_folding(warned, 1).chip_alert == "", "warn 段没被折 → 不点灯"
    assert _plan_folding(warned, 2).chip_alert == "warn"

    mixed = [_primary(), _metric(CPU, severity=isl.ALERT_WARN),
             _metric(MEM, severity=isl.ALERT_CRIT)]
    assert _plan_folding(mixed, 2).chip_alert == "crit", "取最高级而不是先折的那一段"
    assert _plan_folding(mixed, 1).folded == (MEM,), "尾部（内存=crit）先折"
    assert _plan_folding(mixed, 1).chip_alert == "crit"


def test_chip_alert_still_lights_for_a_folded_error_segment():
    """折叠不埋没故障：被折段是 error 态（读数缺失）时仍按 crit 点灯。"""
    segments = [_primary(), _metric(CPU, "--", state=isl.STATE_ERROR), _metric(MEM)]
    plan = _plan_folding(segments, 2)
    assert plan.folded == (MEM, CPU)
    assert plan.chip_alert == "crit"


def test_chip_alert_dot_takes_no_extra_pixel():
    """色点画在 chip 盒**已有**的内边距里：盒宽/右缘一个像素都不动。"""
    plain = layout.chip_segment(2)
    alerted = layout.chip_segment(2, isl.ALERT_CRIT)
    assert alerted.severity == "crit"
    plain_p, alerted_p = (place_info_segments([seg], start_x=10.0, primary_fm=FM,
                                              value_fm=FM, label_fm=FM)[0]
                          for seg in (plain, alerted))
    assert (plain_p.box_x, plain_p.box_w, plain_p.right) == \
        (alerted_p.box_x, alerted_p.box_w, alerted_p.right)
    assert plain_p.alert_dot_x is None, "无色点就不留位"
    dot_x = alerted_p.alert_dot_x
    assert dot_x is not None and dot_x >= alerted_p.box_x
    assert dot_x + layout._CHIP_ALERT_DOT_PX <= alerted_p.text_x, "点不许压到文字"
    assert alerted_p.text_x + alerted_p.text_w <= alerted_p.box_x + alerted_p.box_w

    for count in (1, 12):
        wide = place_info_segments([layout.chip_segment(count, isl.ALERT_WARN)],
                                   start_x=10.0, primary_fm=FM, value_fm=FM,
                                   label_fm=FM)[0]
        assert wide.alert_dot_x + layout._CHIP_ALERT_DOT_PX <= wide.text_x, count
        assert wide.text_x + wide.text_w <= wide.box_x + wide.box_w, count


def test_apply_shrink_hands_the_chip_its_alert_level():
    """行计划 → 当刻行：chip 段自带被折段的最高告警级。"""
    segments = [_primary(), _metric(CPU, severity=isl.ALERT_WARN), _metric(MEM)]
    plan = _plan_folding(segments, 2)
    chip = layout.apply_shrink(plan, segments, primary_fm=FM)[-1]
    assert chip.kind == "chip" and chip.severity == "warn"


# ------------------------------------------------------- 段 severity（窗口级）
def test_device_segment_severity_follows_the_snapshot(tmp_path, monkeypatch):
    """severity 在 ``_device_segment`` 从快照算：逐指标、带滞回。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        assert _segment(island, CPU).severity == "", "还没读数"
        _feed(island, made, _sys_sample(cpu=79.0, mem=84.0))
        assert (_segment(island, CPU).severity, _segment(island, MEM).severity) == ("", "")
        _feed(island, made, _sys_sample(cpu=80.0, mem=95.0))
        assert (_segment(island, CPU).severity, _segment(island, MEM).severity) == \
            ("warn", "crit")
        _feed(island, made, _sys_sample(cpu=91.0, mem=91.0))
        assert (_segment(island, CPU).severity, _segment(island, MEM).severity) == \
            ("warn", "warn"), "内存 91 < 95−3 → 落回 warn"
        _feed(island, made, _sys_sample(cpu=92.0, mem=30.0))
        assert (_segment(island, CPU).severity, _segment(island, MEM).severity) == ("crit", "")
        _feed(island, made, _sys_sample(cpu=88.0, mem=30.0))
        assert _segment(island, CPU).severity == "warn", "跌出 crit（< 89）落回 warn"
    finally:
        island.close()


def test_severity_is_remembered_on_the_island_not_in_the_segment(tmp_path, monkeypatch):
    """滞回要的"上一拍"存在岛实例上，段对象本身不记历史（同一读数两种颜色）。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        assert island._alert_severity == {}, "初始无历史"
        _feed(island, made, _sys_sample(cpu=92.0))
        assert island._alert_severity[CPU] == "crit", "样本到达那一刻就记账"
        _feed(island, made, _sys_sample(cpu=91.0))
        segment = _segment(island, CPU)
        assert (segment.text, segment.severity) == ("91", "crit"), "从 92 降下来仍是 crit"
        assert island._alert_severity[CPU] == "crit"
    finally:
        island.close()


def test_hysteresis_counts_only_sample_ticks_not_paints(tmp_path, monkeypatch):
    """两次读数之间一次都不绘制（隐藏/无 update）也不漏拍：92→91 不许丢 crit。

    滞回若懒得在绘制时才算，91 那一拍会以为"首次越过 warn 线"而画成黄——正好是
    滞回要防的假信号。
    """
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=92.0))
        _feed(island, made, _sys_sample(cpu=91.0))
        segment = _segment(island, CPU)
        assert (segment.text, segment.severity) == ("91", "crit")
    finally:
        island.close()


def test_an_error_snapshot_keeps_the_last_severity(tmp_path, monkeypatch):
    """psutil 掉线（error 快照、读数为 None）：告警不褪色，但段显示 ``--`` 次色。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=97.0))
        assert _segment(island, CPU).severity == "crit"
        _feed(island, made, _sys_sample(cpu=None, state=isl.STATE_ERROR, reason="psutil"))
        segment = _segment(island, CPU)
        assert (segment.state, segment.text, segment.severity) == \
            (isl.STATE_ERROR, "--", "crit")
    finally:
        island.close()


def test_folded_alert_survives_the_sampling_blackout(tmp_path, monkeypatch):
    """全折之后采样停（``_sys_sample=None``），被折段的告警仍在 → chip 点灯不灭。"""
    island, made, screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=95.0, mem=96.0))
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM, CPU))
        assert island._sys_sample is None, "前提：全折后设备槽收口（停采样）"
        assert _segment(island, CPU).severity == "crit", "无读数不等于降到安全值"
        assert plan.chip_alert == "crit"
    finally:
        island.close()


# ---------------------------------------------------------------- 像素：数值染色
def test_alert_color_stains_only_the_value(tmp_path, monkeypatch):
    """只染数值（含 E4 单位盒跟随值色）：label / 竖线 / 段底一个像素都不沾。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=97.0))
        island._content_pixmap = None
        image = _image(island)
        placement = _placement(island, CPU)
        value_box = (placement.box_x, placement.right)          # 数值盒 + 单位盒
        label_box = (placement.label_x, placement.label_x + placement.label_w)
        divider_box = (placement.divider_x - 1.0, placement.divider_x + 2.0)

        inside = _count_color(image, _CRIT_DARK, island=island, x0=value_box[0], x1=value_box[1])
        assert inside > _INK, "crit 读数必须真的画成告警色"
        assert _count_color(image, _CRIT_DARK, island=island,
                            x0=label_box[0], x1=label_box[1]) == 0, "label 不染"
        assert _count_color(image, _CRIT_DARK, island=island,
                            x0=divider_box[0], x1=divider_box[1]) == 0, "段间竖线不染"
        assert _count_color(image, _CRIT_DARK) == inside, "告警色只许出现在数值盒里"
        # 单位盒跟随值色（E4 α70%）：同一色相、半透明
        assert _count_color(image, _CRIT_DARK, island=island,
                            x0=placement.unit_x, x1=placement.right) > 0, "单位盒跟随值色"
    finally:
        island.close()


def test_boundary_sweep_renders_the_spec_color_sequence(tmp_path, monkeypatch):
    """79→80→92→91→79 的**实测像素**序列：无 → warn → crit → crit → warn。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        seen: list[tuple[str, bool, bool]] = []
        for value in (79.0, 80.0, 92.0, 91.0, 79.0):
            _feed(island, made, _sys_sample(cpu=value))
            island._content_pixmap = None
            image = _image(island)
            placement = _placement(island, CPU)
            box = dict(island=island, x0=placement.box_x, x1=placement.right)
            seen.append((_segment(island, CPU).severity,
                         _count_color(image, _WARN_DARK, **box) > 0,
                         _count_color(image, _CRIT_DARK, **box) > 0))
        assert [item[0] for item in seen] == ["", "warn", "crit", "crit", "warn"]
        assert [item[1:] for item in seen] == [
            (False, False), (True, False), (False, True), (False, True), (True, False)]
    finally:
        island.close()


def test_the_network_segment_is_never_stained(tmp_path, monkeypatch):
    """网络段不做告警色：再烂的延迟也只是次色（阈值没有普适性）。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(NETWORK,))
    try:
        _visible(island)
        made[0].sample = net_monitor.NetSample(
            down_bps=0.0, up_bps=0.0, latency_ms=9999.0, latency_lost=True, sampled_at=1.0)
        island._tick_network()
        island._content_pixmap = None
        image = _image(island)
        placement = _placement(island, NETWORK)
        assert _segment(island, NETWORK).severity == ""
        assert _count_color(image, _WARN_DARK, island=island,
                            x0=placement.box_x, x1=placement.right) == 0
        assert _count_color(image, _CRIT_DARK, island=island,
                            x0=placement.box_x, x1=placement.right) == 0
    finally:
        island.close()


# ---------------------------------------------------------------- 像素：chip 色点
def test_chip_dot_lights_with_the_highest_folded_severity(tmp_path, monkeypatch):
    """折叠不埋没告警：被折段含 crit → chip 左侧 4px crit 色点（且别处没有）。"""
    island, made, screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=30.0, mem=96.0))
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert plan.chip_alert == "crit"
        island._content_pixmap = None
        image = _image(island)
        chip = _placement(island, "chip")
        assert chip.alert_dot_x is not None, "告警 chip 必须有色点落位"
        on_chip = _count_color(image, _CRIT_DARK, island=island,
                               x0=chip.box_x, x1=chip.right)
        assert on_chip > 0, "chip 上必须真的点出色点"
        assert _count_color(image, _CRIT_DARK, island=island, x0=chip.alert_dot_x,
                            x1=chip.alert_dot_x + layout._CHIP_ALERT_DOT_PX) > 0, \
            "色点没落在 chip 左侧"
        assert _count_color(image, _CRIT_DARK) == on_chip, "告警色只许出现在 chip 上"
    finally:
        island.close()


def test_chip_dot_color_follows_the_highest_level_and_disappears_on_restore(
        tmp_path, monkeypatch):
    """取最高级：warn 段先折、crit 段后折仍是 crit；展开恢复后点消失。"""
    island, made, screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=95.0, mem=87.0))

        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert plan.chip_alert == "warn", "只折了内存（87 = warn）"
        island._content_pixmap = None
        image = _image(island)
        chip = _placement(island, "chip")
        dot = (chip.alert_dot_x, chip.alert_dot_x + layout._CHIP_ALERT_DOT_PX)
        assert _count_color(image, _WARN_DARK, island=island, x0=dot[0], x1=dot[1]) > 0, "warn 点"
        assert _count_color(image, _CRIT_DARK, island=island, x0=dot[0], x1=dot[1]) == 0, \
            "warn 级的点不许画成 crit"

        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM, CPU))
        assert plan.chip_alert == "crit", "两段都折：取最高级 crit"
        island._content_pixmap = None
        image = _image(island)
        chip = _placement(island, "chip")
        assert _count_color(image, _CRIT_DARK, island=island, x0=chip.alert_dot_x,
                            x1=chip.alert_dot_x + layout._CHIP_ALERT_DOT_PX) > 0, "crit 点"

        # 恢复：屏够宽 → 段全展开、chip 消失、色点没有落脚处
        screen._rect = QRect(0, 0, 1600, 768)
        island.refresh_from_config()
        plan = island._row_plan()
        assert plan.folded == () and plan.chip_alert == ""
        island._content_pixmap = None
        image = _image(island)
        assert _count_color(image, _CRIT_DARK) == 0 and _count_color(image, _WARN_DARK) == 0, \
            "折叠告警段恢复后点必须消失（且没有残留的告警色）"
    finally:
        island.close()


def test_chip_dot_only_lights_when_the_folded_segment_actually_alerts(
        tmp_path, monkeypatch):
    """被折段无色 → chip 不点灯（不把"折叠"本身当成告警）。"""
    island, made, screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=30.0, mem=31.0))
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert plan.chip_alert == ""
        island._content_pixmap = None
        image = _image(island)
        chip = _placement(island, "chip")
        assert chip.alert_dot_x is None
        assert _count_color(image, _WARN_DARK) == 0 and _count_color(image, _CRIT_DARK) == 0
    finally:
        island.close()


def test_chip_dot_follows_the_live_severity_even_when_the_plan_is_cached(
        tmp_path, monkeypatch):
    """行计划命中缓存时色点也必须跟着当刻段列表走。

    90→88 的四舍五入都是「88」，且 plan 的输入键刻意不含采样值/severity（宽度
    白名单纪律），于是这一拍拿到的是**旧计划**：照抄 ``plan.chip_alert`` 就会把
    crit 色点停在降级之后。
    """
    island, made, screen = _island(tmp_path, monkeypatch, metrics=(CPU, MEM))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=30.0, mem=97.0))
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert plan.chip_alert == "crit"
        assert island._width_key()  # 触发一次计划缓存

        _feed(island, made, _sys_sample(cpu=30.0, mem=88.4))
        assert _segment(island, MEM).severity == "warn", "88.4 跌出 crit 滞回带"
        assert island._row_plan() is plan, "前提：这一拍命中的是旧计划"
        island._content_pixmap = None
        image = _image(island)
        chip = _placement(island, "chip")
        assert chip.seg.severity == "warn", "chip 取当刻段列表的告警级"
        dot = (chip.alert_dot_x, chip.alert_dot_x + layout._CHIP_ALERT_DOT_PX)
        assert _count_color(image, _WARN_DARK, island=island, x0=dot[0], x1=dot[1]) > 0
        assert _count_color(image, _CRIT_DARK) == 0, "降级后不许留 crit 像素"
    finally:
        island.close()


# ------------------------------------------------------------ 缓存键纪律
def test_severity_enters_the_content_key_but_not_the_width_key(tmp_path, monkeypatch):
    """同一个读数「91」两种滞回路径 → 颜色不同：宽度键必须相同、内容键必须不同。"""
    high, high_made, _s1 = _island(tmp_path / "high", monkeypatch, metrics=(CPU,))
    low, low_made, _s2 = _island(tmp_path / "low", monkeypatch, metrics=(CPU,))
    try:
        for island, made, values in ((high, high_made, (92.0, 91.0)),
                                     (low, low_made, (80.0, 91.0))):
            _visible(island)
            for value in values:
                _feed(island, made, _sys_sample(cpu=value))
            island._content_cache()

        assert _segment(high, CPU).text == _segment(low, CPU).text == "91"
        assert (_segment(high, CPU).severity, _segment(low, CPU).severity) == ("crit", "warn")
        assert high.width() == low.width()
        assert high._width_key() == low._width_key(), "severity 不许进宽度白名单键"
        assert high._capsule_width() == low._capsule_width()
        assert high._content_cache_key != low._content_cache_key, "severity 必须进内容键"

        # 像素面：同一个「91」真的画成两种颜色
        assert _count_color(_image(high), _CRIT_DARK) > 0
        assert _count_color(_image(low), _WARN_DARK) > 0
    finally:
        high.close()
        low.close()


def test_a_severity_flip_alone_rebuilds_the_bitmap(tmp_path, monkeypatch):
    """色变 → 位图重建：96（crit）→ 同快照不重建；换到 90 仍 crit 但字变了也要重建。"""
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=(CPU,))
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=96.0))
        first = island._content_cache()
        assert island._content_cache() is first, "同 key 必须命中缓存"
        assert _segment(island, CPU).severity == "crit"
        _feed(island, made, _sys_sample(cpu=90.0))
        assert island._content_cache() is not first, "读数与颜色都变了必须重建"
        island._tick_sys()
        assert island._content_cache().width() == first.width(), "重建也不改宽"
    finally:
        island.close()


# ---------------------------------------------------------------- 主题取色
def _contrast(color: str, background: str = "#ffffff") -> float:
    """WCAG 对比度（亮底可读性的客观判据）。"""
    def _lum(value: str) -> float:
        raw = value.lstrip("#")
        channels = [int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first, second = _lum(color), _lum(background)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def test_alert_colors_follow_the_existing_theme_functions(tmp_path, monkeypatch):
    """暗底用规格值；亮底/玻璃底按同一色相加深档（白底可读），不新增主题键。"""
    dark, _made, _screen = _island(tmp_path / "dark", monkeypatch, style="dark")
    light, _made2, _screen2 = _island(tmp_path / "light", monkeypatch, style="light")
    glass, _made3, _screen3 = _island(tmp_path / "glass", monkeypatch, style="glass")
    try:
        assert dark._alert_color(isl.ALERT_WARN).name() == _WARN_DARK
        assert dark._alert_color(isl.ALERT_CRIT).name() == _CRIT_DARK
        assert dark._alert_color("") is None and dark._alert_color("bogus") is None

        for severity, spec in ((isl.ALERT_WARN, _WARN_DARK), (isl.ALERT_CRIT, _CRIT_DARK)):
            for island in (light, glass):
                got = island._alert_color(severity)
                want = QColor(spec)
                assert got.name() != spec, "亮底不许直接用暗底色值"
                assert got.lightness() < want.lightness(), "亮底必须加深"
                assert abs(got.hue() - want.hue()) <= 3, "加深不许换色相"
                assert _contrast(got.name()) >= 4.5, f"{got.name()} 白底读不出来"
        assert light._alert_color(isl.ALERT_WARN).name() == \
            glass._alert_color(isl.ALERT_WARN).name(), "亮底与玻璃底同一档"
    finally:
        dark.close()
        light.close()
        glass.close()
