# -*- coding: utf-8 -*-
"""灵动岛**单位弱化双盒**（P2 Slice E4）的契约测试：值/单位分离 + 单位盒几何 + 降色。

施工单 §E4 / 裁决稿 §二-1·§二-5（ops5.5 双盒 + 10px 降色）：cpu/mem 的 ``%`` 从
格式化串里拆出来——数值进"右对齐的定宽数值盒"（13px），单位进"左对齐的单位盒"
（10px、值色 α×70%），单位盒宽 = 各段最宽单位启动时 ``QFontMetrics`` 实测。
``9% → 100%`` 切换时数值盒右缘像素不动，是这一层的验收点。

四条硬约束：

1. **合并串不破**：``sys_monitor.format_cpu/format_mem`` 与 ``render_compact`` 照旧
   输出合并串（卡片与其他消费方用同一个串），本 slice 只**增加**"值/单位分离"输出；
2. **单位是模板面**：单位进盒宽样本 → 进 ``_plan_inputs`` 键；数值仍不进键
   （读数 9→100 一次都不许改宽，见 ``_width_key``）；
3. **四态兜底不画单位**：非 ok 态数值盒画 ``--``、单位盒留空——盒宽照占，所以状态
   切换不改岛宽（``--`` 不配上孤零零的 ``%``）；
4. **降色在真绘制路径上**：单位与数值同色、α×70%，与数值共用同一条基线。

采样器一律替身（本文件不验采样生命周期，见 tests/test_island_device_slot.py）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, qAlpha
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
import pet.island.layout as island_layout
from pet.island import segments as isl
from pet import net_monitor, sys_monitor
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.layout import _InfoSeg, place_info_segments
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])

#: 数值盒声明样本（= ``sys_monitor.MAX_TEXT`` 拆掉单位的那一半）。
_VALUE_SAMPLE = "888"
#: 单位盒里画的字（cpu/mem 同款）。
_UNIT = "%"
#: 墨阈值：滤掉抗锯齿的微弱外溢（0-255 的 alpha）。
_INK_THRESHOLD = 40


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


class _StubMonitor:
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


def _config(tmp_path, **overrides) -> Config:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": False, "show_name": False,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": False, "style": "dark", "x": 400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return cfg


def _island(config, monkeypatch) -> tuple[DynamicIsland, list[_StubMonitor]]:
    """建岛并把两条采样路径换成替身（本文件不验采样生命周期）。"""
    made: list[_StubMonitor] = []

    def factory(_self, interval):
        monitor = _StubMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    return DynamicIsland(config), made


def _sys_sample(cpu=24.0, mem=61.0, *, state=isl.STATE_OK, reason=""):
    return sys_monitor.SysSample(
        cpu_percent=cpu, mem_percent=mem, sampled_at=1.0, state=state, error_reason=reason)


def _visible(island) -> DynamicIsland:
    """显示并把首轮订阅跑完（段才拿得到快照）。"""
    island.show()
    app.processEvents()
    return island


def _feed(island, made, sample) -> None:
    made[0].sample = sample
    island._tick_sys()


def _segment(island, seg_id: str) -> _InfoSeg:
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}")


def _placements(island) -> list:
    """当刻行内落位（与绘制层同一条路径：行计划 + 当刻段列表）。"""
    plan = island._row_plan()
    row = island_layout.apply_shrink(
        plan, island._compose_info_segments(),
        primary_fm=island._metrics_for("primary"))
    return place_info_segments(row, start_x=plan.segments_x,
                               primary_fm=island._metrics_for("primary"),
                               value_fm=island._metrics_for("value"),
                               label_fm=island._metrics_for("label"))


def _placement(island, seg_id: str):
    for placement in _placements(island):
        if placement.seg.seg_id == seg_id:
            return placement
    raise AssertionError(f"行里没有 {seg_id!r}")


# ---------------------------------------------------------------- 位图取像
def _content_image(island) -> QImage:
    return island._content_cache().toImage()


def _columns(image, island, x0: float, x1: float) -> tuple[int, int]:
    """逻辑坐标 ``[x0, x1)`` → 位图的像素列区间（左闭右开）。"""
    scale = image.width() / float(island.width())
    return max(0, int(round(x0 * scale))), min(image.width(), int(round(x1 * scale)))


def _alpha_sum(image, island, x0: float, x1: float) -> int:
    """逻辑竖条内的 alpha 总量（整幅高度）：有墨 / 没墨 / 墨多墨少的统一口径。

    用 ``qAlpha`` 而不是 ``QColor(image.pixel(...))``：QColor 的 QRgb 构造函数
    **丢掉 alpha 分量**（恒为 255），拿它做"没墨"的断言永远是假绿。
    """
    left, right = _columns(image, island, x0, x1)
    return sum(qAlpha(image.pixel(px, py))
               for px in range(left, right) for py in range(image.height()))


def _ink_rows(image, island, x0: float, x1: float) -> list[int]:
    """逻辑竖条内有可见墨的像素行。"""
    left, right = _columns(image, island, x0, x1)
    return [py for py in range(image.height())
            if any(qAlpha(image.pixel(px, py)) > _INK_THRESHOLD
                   for px in range(left, right))]


def _rightmost_ink(image, island, x0: float, x1: float) -> float:
    """逻辑竖条内最右一列有墨的 x（逻辑坐标）；无墨返回 ``float("-inf")``。"""
    scale = image.width() / float(island.width())
    left, right = _columns(image, island, x0, x1)
    best = float("-inf")
    for px in range(left, right):
        if any(qAlpha(image.pixel(px, py)) > _INK_THRESHOLD
               for py in range(image.height())):
            best = px / scale
    return best


# ---------------------------------------------------------- 契约：值/单位分离
def test_split_value_unit_separates_the_percent_and_keeps_the_merged_formatter():
    """格式化器的合并串照旧（卡片消费面），分离输出只给双盒排版用。"""
    assert sys_monitor.format_cpu(37.5) == "38%", "合并串不许变（卡片与其他消费方）"
    assert sys_monitor.format_mem(61.2) == "61%"
    assert isl.split_value_unit(sys_monitor.format_cpu(37.5), isl.PERCENT_UNIT) == ("38", "%")
    assert isl.split_value_unit(sys_monitor.format_mem(61.2), isl.PERCENT_UNIT) == ("61", "%")
    assert isl.PERCENT_UNIT == "%"


def test_split_value_unit_leaves_the_fallback_string_whole():
    """兜底串 ``--`` / 读不出数 —— 整串当数值，不拆出一个孤零零的单位。"""
    assert isl.split_value_unit("--", isl.PERCENT_UNIT) == ("--", "")
    assert isl.split_value_unit("", isl.PERCENT_UNIT) == ("", "")
    assert isl.split_value_unit("38", isl.PERCENT_UNIT) == ("38", "")
    assert isl.split_value_unit("%", isl.PERCENT_UNIT) == ("%", "")


def test_render_compact_still_emits_the_merged_string():
    """段注册表的紧凑串（卡片侧消费方）仍是合并形态：CPU24%。"""
    snapshot = isl.SegmentSnapshot(id=CPU, label="CPU", text="24%", max_text="888%")
    assert isl.render_compact(snapshot) == "CPU24%"


def test_device_segments_carry_value_and_unit_separately(tmp_path, monkeypatch):
    """cpu/mem 段：数值盒拿值（不带 %）、单位盒拿 %，数值样本拆自 MAX_TEXT。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        for metric_id in (CPU, MEM):
            segment = _segment(island, metric_id)
            assert segment.text == "--", "首轮还没读数"
            assert segment.unit == _UNIT, "单位恒声明（兜底态也占着单位盒宽）"
            assert segment.box_text == _VALUE_SAMPLE, "数值盒样本 = MAX_TEXT 拆掉单位"

        _feed(island, made, _sys_sample(cpu=37.5, mem=61.2))
        cpu = _segment(island, CPU)
        assert (cpu.text, cpu.unit) == ("38", "%")
        assert _segment(island, MEM).text == "61"
        # 合并串仍在（格式化器原样）：值 + 单位拼回去就是它
        assert cpu.text + cpu.unit == sys_monitor.format_cpu(37.5)
    finally:
        island.close()


# ------------------------------------------------------------ 双盒几何（纯函数）
def test_unit_box_hugs_the_value_box_by_one_pixel():
    """单位盒左缘 = 数值盒右缘 + 1px（裁决稿 §二-5 的「紧贴数字 1px」落在盒上）。"""
    fm = FakeMetrics(step=10)
    segment = _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text="38",
                       box_text=_VALUE_SAMPLE, state=isl.STATE_OK, unit=_UNIT)
    (placement,) = place_info_segments(
        [segment], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert placement.box_x == 30 + isl.LABEL_GAP_PX, "标签宽 + 固定间距"
    assert placement.box_w == 30, "数值盒宽 = 数值样本实测（不含单位）"
    assert placement.unit_x == placement.box_x + placement.box_w + 1.0
    assert placement.unit_w == 10, "单位盒宽 = 单位实测"
    assert placement.right == placement.unit_x + placement.unit_w, "段右缘含单位盒"


def test_unit_box_width_follows_the_widest_unit_in_the_row():
    """单位盒宽 = **各段最宽单位**实测：一行的单位盒左右缘因此对齐。"""
    fm = FakeMetrics(step=10)
    narrow = _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text="38",
                      box_text=_VALUE_SAMPLE, state=isl.STATE_OK, unit="%")
    wide = _InfoSeg(kind="metric", seg_id=MEM, label="内存", text="1.2",
                    box_text=_VALUE_SAMPLE, state=isl.STATE_OK, unit="Mbps")
    first, second = place_info_segments(
        [narrow, wide], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert first.unit_w == second.unit_w == 40, "取最宽单位（Mbps）的实测宽"


def test_segments_without_a_unit_take_no_extra_pixel():
    """无单位的段（chip / 主信息段 / 网络段）一个像素都不多占。"""
    fm = FakeMetrics(step=10)
    plain = _InfoSeg(kind="metric", seg_id=NETWORK, label="", text="↓ 1.2M",
                     box_text="8", state=isl.STATE_OK)
    (only,) = place_info_segments(
        [plain], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert only.unit_x is None and only.unit_w == 0
    assert only.right == only.box_x + only.box_w


def test_fallback_states_reserve_the_unit_box_but_draw_nothing_in_it():
    """四态兜底：``--`` 就只是 ``--``，但单位盒**照占宽**（状态切换不跳宽）。"""
    fm = FakeMetrics(step=10)

    def build(state: str, text: str) -> _InfoSeg:
        return _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text=text,
                        box_text=_VALUE_SAMPLE, state=state, unit=_UNIT)

    (ok,) = place_info_segments(
        [build(isl.STATE_OK, "38")], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert ok.unit_text == "%"
    for state in (isl.STATE_SAMPLING, isl.STATE_UNKNOWN, isl.STATE_ERROR):
        (fallback,) = place_info_segments(
            [build(state, "--")], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
        assert fallback.unit_text == "", f"{state}：兜底态不画单位"
        assert fallback.unit_x == ok.unit_x and fallback.unit_w == ok.unit_w
        assert fallback.right == ok.right, f"{state}：状态切换不得改宽"


def test_value_box_right_edge_never_moves_from_9_to_100(tmp_path, monkeypatch):
    """验收点：``9% → 100%`` 数值盒右缘与字形右缘都不动（双盒的意义所在）。"""
    island, _made = _island(_config(tmp_path), monkeypatch)
    try:
        value_fm = island._metrics_for("value")
        label_fm = island._metrics_for("label")

        def place(value: str):
            segment = _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text=value,
                               box_text=_VALUE_SAMPLE, state=isl.STATE_OK, unit=_UNIT)
            (placement,) = place_info_segments(
                [segment], start_x=11.0, primary_fm=value_fm,
                value_fm=value_fm, label_fm=label_fm)
            return placement

        nine, hundred, zero = place("9"), place("100"), place("0")
        assert nine.box_x + nine.box_w == hundred.box_x + hundred.box_w \
            == zero.box_x + zero.box_w
        assert nine.text_x + nine.text_w == hundred.text_x + hundred.text_w
        assert nine.right == hundred.right == zero.right, "段右缘（含单位盒）也不动"
    finally:
        island.close()


def test_value_box_right_edge_never_moves_at_the_pixel_level(tmp_path, monkeypatch):
    """同一条验收的像素面：真字体渲染下 9 与 100 的最右墨列不许移动。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)

        def right_edge(cpu: float) -> float:
            _feed(island, made, _sys_sample(cpu=cpu))
            island._content_pixmap = None      # 强制重建：读数变化走的正是内容层
            placement = _placement(island, CPU)
            return _rightmost_ink(_content_image(island), island, placement.box_x,
                                  placement.box_x + placement.box_w + 0.5)

        nine, hundred = right_edge(9.0), right_edge(100.0)
        assert nine > float("-inf") and hundred > float("-inf"), "数值必须真的画出来"
        assert abs(nine - hundred) <= 1.0, f"数值盒右缘动了：9→{nine} / 100→{hundred}"
    finally:
        island.close()


# ---------------------------------------------------------------- 宽度白名单
def test_unit_enters_the_box_sample_but_readings_stay_out_of_the_key(tmp_path, monkeypatch):
    """单位是模板面（进 ``_plan_inputs`` 键）；数值仍是内容面（永不进键、永不改宽）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=9.0))
        key = island._plan_inputs()
        width = island.width()

        templates = [item for item in key
                     if isinstance(item, tuple) and item and isinstance(item[0], tuple)]
        assert templates, "行计划键里必须有段模板分量"
        assert any(_UNIT in str(entry) for entry in templates[0]), \
            "单位必须进段模板分量（盒宽样本面）"

        _feed(island, made, _sys_sample(cpu=100.0))
        assert _segment(island, CPU).text == "100"
        assert island._plan_inputs() == key, "读数不许进行计划输入键"
        assert island.width() == width, "100% 不许把岛撑宽"
    finally:
        island.close()


def test_a_wider_unit_widens_the_island_through_the_plan_inputs(tmp_path, monkeypatch):
    """单位变化 = 模板变化：换一个更宽的单位，岛变宽且键跟着变（既有键路径）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=38.0))
        before_width, before_key = island.width(), island._plan_inputs()

        monkeypatch.setattr(isl, "PERCENT_UNIT", "％％")
        assert island._plan_inputs() != before_key, "单位变了，键必须跟着变"
        island.refresh_from_config()
        assert island.width() > before_width, "更宽的单位盒必然让岛更宽"
    finally:
        island.close()


# ---------------------------------------------------------------- 绘制层
def test_unit_color_is_the_value_color_at_70_percent_alpha(tmp_path, monkeypatch):
    """降色口径：与数值同色、alpha ×70%（不换字号层，只弱化墨量）。"""
    island, _made = _island(_config(tmp_path), monkeypatch)
    try:
        base = QColor(235, 238, 245)
        unit = island._unit_color(base)
        assert (unit.red(), unit.green(), unit.blue()) == (235, 238, 245), "色相不动"
        assert unit.alpha() == round(255 * 0.7)
        faded = island._unit_color(QColor(255, 255, 255, 180))
        assert faded.alpha() == round(180 * 0.7), "已有 alpha 的底色按比例再降"
        assert QColor(base).alpha() == 255, "不得就地改掉传进来的底色"
    finally:
        island.close()


def test_content_pixmap_draws_the_unit_with_the_reduced_alpha(tmp_path, monkeypatch):
    """降色必须落在**真绘制**上：同样字形、同样位置，alpha 总量严格更少。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=38.0))
        placement = _placement(island, CPU)
        region = (placement.unit_x + 1.0, placement.unit_x + placement.unit_w)

        weakened = _alpha_sum(_content_image(island), island, *region)
        assert weakened > 0, "单位必须真的画在单位盒里"

        monkeypatch.setattr(island_mod.DynamicIsland, "_unit_color",
                            staticmethod(lambda base: QColor(base)))
        island._content_pixmap = None      # 换色不改 key，测试自己废掉位图
        full = _alpha_sum(_content_image(island), island, *region)
        assert full > weakened, f"单位没走降色：{weakened} vs {full}"
    finally:
        island.close()


def test_content_pixmap_draws_no_unit_ink_in_fallback_states(tmp_path, monkeypatch):
    """兜底态：数值盒有 ``--`` 的墨，单位盒一个像素都没有（盒宽仍占）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        placement = _placement(island, CPU)
        image = _content_image(island)
        assert _alpha_sum(image, island, placement.box_x,
                          placement.box_x + placement.box_w) > 0, "采样态画 --"
        assert _alpha_sum(image, island, placement.unit_x + 1.0,
                          placement.unit_x + placement.unit_w) == 0

        _feed(island, made, _sys_sample(cpu=None, mem=None, state=isl.STATE_ERROR,
                                        reason="PsutilUnavailable: 没装"))
        island._content_pixmap = None
        placement = _placement(island, CPU)
        assert _alpha_sum(_content_image(island), island, placement.unit_x + 1.0,
                          placement.unit_x + placement.unit_w) == 0, "error 态同样不画单位"
    finally:
        island.close()


def test_unit_shares_the_value_baseline(tmp_path, monkeypatch):
    """基线同线：单位盒的墨底与数值盒的墨底落在同一行（±1px 抗锯齿）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=100.0))
        placement = _placement(island, CPU)
        image = _content_image(island)
        value_rows = _ink_rows(image, island, placement.box_x,
                               placement.box_x + placement.box_w)
        unit_rows = _ink_rows(image, island, placement.unit_x + 1.0,
                              placement.unit_x + placement.unit_w)
        assert value_rows and unit_rows, "两边都必须有墨"
        assert abs(max(value_rows) - max(unit_rows)) <= 1, \
            f"基线与数值不同线：值 {max(value_rows)} / 单位 {max(unit_rows)}"
    finally:
        island.close()


def test_unit_is_drawn_in_the_10px_layer_under_the_13px_value(tmp_path, monkeypatch):
    """双管弱化：13px 数值 + 10px 单位（字号一档之差是"弱化"的另一半）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island, made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        _feed(island, made, _sys_sample(cpu=100.0))
        placement = _placement(island, CPU)
        image = _content_image(island)
        value_ink = _alpha_sum(image, island, placement.box_x,
                               placement.box_x + placement.box_w)
        unit_ink = _alpha_sum(image, island, placement.unit_x + 1.0,
                              placement.unit_x + placement.unit_w)
        assert 0 < unit_ink < value_ink, \
            f"单位的墨量该弱于数值：值 {value_ink} / 单位 {unit_ink}"
        assert island._font_for("label").pixelSize() == 10
        assert island._font_for("value").pixelSize() == 13
    finally:
        island.close()


def test_network_segment_keeps_its_inline_unit(tmp_path, monkeypatch):
    """网络段不动（5 列槽串内嵌单位是跨模块契约）：没有单位盒，右缘 = 数值盒右缘。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK])
    island, _made = _island(cfg, monkeypatch)
    try:
        _visible(island)
        placement = _placement(island, NETWORK)
        assert placement.seg.unit == ""
        assert placement.unit_x is None and placement.unit_w == 0
        assert placement.right == placement.box_x + placement.box_w
    finally:
        island.close()
