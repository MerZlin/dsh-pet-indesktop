# -*- coding: utf-8 -*-
"""灵动岛收缩流水线（P1 Slice D2）的契约测试：纯判定 + 采样联动 + 伸长锚点。

规格（施工单 §D2）把收缩定义成一条**可逆的纯计算**流水线：

1. S1 名字省略（96px 静止上限 → 40px）；
2. S2 名字隐藏（整段去掉，含其后 10px 间距）；
3. S3 尾部整段折 ``+N``（列表尾 = 优先级最低，一次折一段直到 fits；被折段
   **撤销采样订阅**）；
4. S4 custom 主信息省略（96px 右省略；时间/余额/峰谷是定宽主信息，永不省略）；
5. 保底：头像 + 主信息 + 状态点永不折，仍超宽就保持溢出截断。

本文件三层验证：

- **纯判定**（``plan_info_row``）：逐级触发、尾部优先、主信息永不折、逆序恢复；
- **采样联动**（窗口级）：折掉的段退订、展开重订阅且首轮显示 ``--``；
- **几何**：``+N`` chip 的盒宽/居中/无竖线、三分区锚点、拖拽中宽度冻结、
  多屏迁移后重算。

采样器一律替身（``_make_net_monitor`` / ``_make_sys_monitor`` 替换点）：测试不发
探测包、不碰 psutil。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.island import layout
from pet.island import segments as isl
from pet import sys_monitor as sm
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.layout import (
    LEVEL_FOLD_BASE, LEVEL_NAME_ELIDE, LEVEL_NAME_HIDE, LEVEL_NONE,
    _InfoSeg, anchored_x, apply_shrink, elide_to_width, plan_info_row,
    place_info_segments,
)
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])

_PRIMARY = "primary"
_ELLIPSIS = isl.ELLIPSIS


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
#: 行首固定件（内容左内边距 + 头像 + 间距）与行尾固定件（状态点 + 间距）。
_LEAD = 19.0
_TRAIL = 18.0
#: 长名字（9 字 = 90px，≤96 静止上限；收到 40px 时变成 "小鱼儿…"）。
_LONG_NAME = "小鱼儿她姐姐姐姐姐"


def _primary(text="12:00", box="88:88", *, elide=0):
    return _InfoSeg(kind="primary", seg_id=_PRIMARY, label="", text=text,
                    box_text=box, state=isl.STATE_OK, elide_px=elide)


def _metric(seg_id, label="", text="24%", box="888%", *, state=isl.STATE_OK):
    return _InfoSeg(kind="metric", seg_id=seg_id, label=label, text=text,
                    box_text=box, state=state)


def _metrics():
    """两个指标段（CPU/内存），尾部 = 内存（列表序 = 优先级降序）。"""
    return [_metric(CPU, "CPU"), _metric(MEM, "内存")]


def _plan(segments, available, *, name=_LONG_NAME):
    return plan_info_row(
        segments, lead_w=_LEAD, trail_w=_TRAIL, name_text=name,
        available_w=available, primary_fm=FM, value_fm=FM, label_fm=FM, name_fm=FM)


def _ids(plan):
    return list(plan.visible)


def _row(plan, segments):
    """把计划套到当刻的段列表上（绘制层走的就是这条路）。"""
    return layout.apply_shrink(plan, segments, primary_fm=FM)


# ------------------------------------------------------------ 纯判定：逐级触发
def test_plan_within_budget_touches_nothing():
    plan = _plan([_primary(), *_metrics()], 10_000)
    assert plan.level == LEVEL_NONE
    assert plan.name == _LONG_NAME
    assert plan.folded == ()
    assert _ids(plan) == [_PRIMARY, CPU, MEM]
    assert plan.width <= 10_000


def test_plan_elides_the_name_before_dropping_anything():
    """第一刀是名字省略：段一个不动，名字收到 40px。"""
    plan = _plan([_primary(), *_metrics()], 340)
    assert plan.level == LEVEL_NAME_ELIDE
    assert plan.name.endswith(_ELLIPSIS) and plan.name_w <= 40
    assert plan.folded == ()
    assert _ids(plan) == [_PRIMARY, CPU, MEM]
    assert plan.width <= 340


def test_plan_hides_the_name_before_folding_segments():
    plan = _plan([_primary(), *_metrics()], 300)
    assert plan.level == LEVEL_NAME_HIDE
    assert plan.name == "" and plan.name_w == 0
    assert plan.folded == ()
    assert _ids(plan) == [_PRIMARY, CPU, MEM]


def test_plan_folds_from_the_tail_and_adds_a_chip():
    """第三级：从列表尾逐个折进 +N（尾 = 用户心里的最低优先级）。"""
    segments = [_primary(), *_metrics()]
    plan = _plan(segments, 250)
    assert plan.level == LEVEL_FOLD_BASE + 1
    assert plan.folded == (MEM,), "先折尾部那一段"
    assert _ids(plan) == [_PRIMARY, CPU, "chip"]
    chip = _row(plan, segments)[-1]
    assert chip.kind == "chip" and chip.text == "+1"
    assert plan.width <= 250


def test_plan_keeps_folding_until_it_fits():
    segments = [_primary(), *_metrics()]
    plan = _plan(segments, 200)
    assert plan.folded == (MEM, CPU), "折叠顺序 = 段 id 序的严格倒序"
    assert plan.level == LEVEL_FOLD_BASE + 2
    assert _ids(plan) == [_PRIMARY, "chip"]
    assert _row(plan, segments)[-1].text == "+2"


def test_plan_never_folds_the_primary():
    """保底：头像 + 主信息 + 状态点永不折（极端窄屏只溢出截断）。"""
    segments = [_primary(), *_metrics()]
    plan = _plan(segments, 100)
    row = _row(plan, segments)
    assert row[0].seg_id == _PRIMARY, "主信息段永远在第一位"
    assert row[0].text == "12:00", "定宽主信息不做省略"
    assert plan.folded == (MEM, CPU)
    assert plan.width > 100, "折无可折：宽度如实溢出，由绘制层截断（不静默吃字）"


def test_plan_elides_the_custom_primary_only_at_the_floor():
    """S4：custom 主信息可省（96px）；时间/余额是定宽主信息，永不动。"""
    custom = _primary(text="今天也在摸鱼今天也在摸鱼", elide=96)
    segments = [custom, *_metrics()]
    plan = _plan(segments, 150)
    assert plan.level == LEVEL_FOLD_BASE + 3, "折尽指标段之后才是主信息省略"
    assert plan.elide_primary is True
    head = _row(plan, segments)[0]
    assert head.text.endswith(_ELLIPSIS) and head.text != custom.text
    assert head.box_text == head.text, "省略后盒宽跟着收（不然白占位）"
    assert FM.horizontalAdvance(head.text) <= 96


def test_plan_leaves_a_fixed_width_primary_intact_at_the_floor():
    segments = [_primary(elide=0), *_metrics()]
    plan = _plan(segments, 150)
    assert plan.elide_primary is False, "定宽主信息不参与省略"
    assert _row(plan, segments)[0].text == "12:00"
    assert plan.level == LEVEL_FOLD_BASE + 2, "没有可省的字段：停在折尽那一级"


def test_plan_is_stateless_so_widening_restores_everything():
    """逆序恢复：同一份输入换个预算就全展开——收缩状态纯计算，不落盘。"""
    segments = [_primary(), *_metrics()]
    narrow = _plan(segments, 200)
    assert narrow.folded == (MEM, CPU)
    wide = _plan(segments, 10_000)
    assert wide.level == LEVEL_NONE and wide.folded == ()
    assert _ids(wide) == [_PRIMARY, CPU, MEM]
    assert wide.name == _LONG_NAME


def test_plan_without_a_budget_never_shrinks():
    """无屏（拿不到可用宽）：按 0 处理——不收缩，宁可靠 clamp 兜底。"""
    plan = _plan([_primary(), *_metrics()], 0)
    assert plan.level == LEVEL_NONE and plan.folded == ()


def test_plan_without_info_segments_only_shrinks_the_name():
    plan = _plan([], 60, name=_LONG_NAME)
    assert plan.folded == () and plan.visible == ()
    assert plan.width > 0


def test_chip_carries_the_alert_flag_when_a_folded_segment_errors():
    """折叠不埋没告警：被折段里有 error → chip 左侧色点（告警级已从 bool 升格为
    ``""|warn|crit``：error 态一律按 crit 算，见 ``island_layout._folded_alert``）。"""
    segments = [_primary(), _metric(CPU, "CPU", "--", "888%", state=isl.STATE_ERROR),
                _metric(MEM, "内存")]
    plan = _plan(segments, 250)
    assert plan.folded == (MEM,)
    assert plan.chip_alert == "", "折掉的是内存，CPU 的 error 还在画"

    plan = _plan(segments, 200)
    assert CPU in plan.folded and MEM in plan.folded
    assert plan.chip_alert == isl.ALERT_CRIT


# ------------------------------------------------------------ chip 的落位
def test_place_info_segments_lays_out_a_chip_with_its_own_padding():
    chip = _InfoSeg(kind="chip", seg_id="chip", label="", text="+3",
                    box_text="+3", state=isl.STATE_OK)
    (placement,) = place_info_segments([chip], start_x=10.0, primary_fm=FM,
                                       value_fm=FM, label_fm=FM)
    assert placement.box_x == 10.0, "chip 前不画竖线、不占 19px 槽位"
    assert placement.divider_x is None
    assert placement.box_w == max(26, 2 * 10 + 12), "宽 = 文字宽 + 左右各 6px"
    assert placement.text_x == placement.box_x + (placement.box_w - placement.text_w) / 2.0, \
        "chip 是容器：文字居中，不像字段盒那样右对齐"


def test_place_info_segments_gives_a_wide_chip_a_minimum_width():
    chip = _InfoSeg(kind="chip", seg_id="chip", label="", text="+",
                    box_text="+", state=isl.STATE_OK)
    (placement,) = place_info_segments([chip], start_x=0.0, primary_fm=FM,
                                       value_fm=FM, label_fm=FM)
    assert placement.box_w == 26, "最小 26px（K3 §3.3）"


def test_chip_after_a_metric_still_gets_no_divider():
    segments = [_metric(CPU, "CPU"),
                _InfoSeg(kind="chip", seg_id="chip", label="", text="+1",
                         box_text="+1", state=isl.STATE_OK)]
    cpu, chip = place_info_segments(segments, start_x=0.0, primary_fm=FM,
                                    value_fm=FM, label_fm=FM)
    assert cpu.divider_x is None, "首段前不画竖线"
    assert chip.divider_x is None, "chip 前不画竖线（它是收尾标记，不是新数据段）"


# ------------------------------------------------------------ 省略实现
def test_elide_to_width_is_a_binary_search_over_prefixes():
    assert elide_to_width("小鱼儿", 96, FM) == "小鱼儿", "装得下就原样返回"
    assert elide_to_width("小鱼儿她姐姐姐姐姐", 40, FM) == "小鱼儿" + _ELLIPSIS
    assert elide_to_width("小鱼儿", 0, FM) == "", "上限非正：宁可不显示也不越界"
    long_text = "很长" * 500
    assert FM.horizontalAdvance(elide_to_width(long_text, 96, FM)) <= 96


# ------------------------------------------------------------ 错输入护栏
def test_apply_shrink_with_fewer_segments_than_the_plan_folds_never_slices_backwards():
    """计划被拖拽冻结时配置可能已经改小：段比折叠数还少也不许吃错段。"""
    plan = _plan([_primary(), *_metrics()], 200)
    assert len(plan.folded) == 2
    row = _row(plan, [_primary()])               # 只剩主信息（配置刚被改小）
    assert row[0].seg_id == _PRIMARY and row[0].text == "12:00", "主信息永不被折"
    assert _row(plan, []) == ()                  # 连主信息都没有：出空行，不炸


def test_plan_with_an_empty_segment_list_is_still_a_valid_row():
    """无段（信息槽关闭）：计划只有名字与固定件，宽度不小于下限。"""
    plan = _plan([], 500, name="")
    assert plan.visible == () and plan.folded == ()
    assert plan.width >= 120, "极小内容下岛仍要是可点可拖的胶囊"


# ------------------------------------------------------------ 伸长锚点（三分区）
def test_anchored_x_pins_the_left_edge_in_the_left_third():
    # 屏 1200 宽、岛宽 200 在 x=100 → 中心 200 < 400（左 1/3）
    assert anchored_x(x=100, width=200, new_width=600,
                      screen_left=0, screen_width=1200) == 100


def test_anchored_x_pins_the_right_edge_in_the_right_third():
    # 中心 1100 ≥ 800（右 1/3）→ 右缘固定，向左长
    assert anchored_x(x=1000, width=200, new_width=600,
                      screen_left=0, screen_width=1200) == 600


def test_anchored_x_pins_the_center_in_the_middle_third():
    # 中心 600（中间 1/3）→ 中心固定，两侧长
    assert anchored_x(x=500, width=200, new_width=600,
                      screen_left=0, screen_width=1200) == 300


def test_anchored_x_without_screen_info_keeps_the_edge():
    assert anchored_x(x=77, width=200, new_width=600,
                      screen_left=0, screen_width=0) == 77


def _place_island(island, x: int) -> None:
    """把岛挪到 (x, 300) 并让它按新位置重算一次几何。"""
    island.move(x, 300)
    island._update_size()


def test_growth_in_the_right_third_pins_the_right_edge(tmp_path, monkeypatch):
    """锚点接进了几何重算（不是只躺在纯函数里）：右 1/3 的岛变宽时向左长。"""
    island, _made, _screen = _island(tmp_path, monkeypatch, screen_width=1400, x=1100)
    try:
        island.show()
        app.processEvents()
        _place_island(island, 1100)
        before = island.geometry()

        island.config.set("island_metrics", [CPU, MEM])
        island.refresh_from_config()
        assert island.width() > before.width()
        assert island.geometry().right() == before.right(), "右缘不动（向左长）"
        assert island.x() < before.x()
    finally:
        island.close()


def test_growth_in_the_left_third_pins_the_left_edge(tmp_path, monkeypatch):
    island, _made, _screen = _island(tmp_path, monkeypatch, screen_width=1400, x=10)
    try:
        island.show()
        app.processEvents()
        _place_island(island, 10)
        before = island.geometry()

        island.config.set("island_metrics", [CPU, MEM])
        island.refresh_from_config()
        assert island.width() > before.width()
        assert island.x() == before.x(), "左缘不动（向右长）"
    finally:
        island.close()


# ------------------------------------------------------------ 窗口级：采样联动
class StubMonitor:
    """采样器替身：只记录生命周期与订阅调用，样本由测试塞进去。"""

    def __init__(self, interval: float = sm.DEFAULT_INTERVAL_SECONDS):
        self.interval = interval
        self.sample = None
        self.starts = 0
        self.stops = 0
        self.closed = False
        self.started = False
        self.subscribed: set[str] = set()
        self.unsubscribed: list[str] = []

    def subscribe(self, key: str) -> int:
        self.subscribed.add(str(key))
        return len(self.subscribed)

    def unsubscribe(self, key: str) -> int:
        self.unsubscribed.append(str(key))
        self.subscribed.discard(str(key))
        return len(self.subscribed)

    def start(self) -> bool:
        if self.started:
            return False
        self.starts += 1
        self.started = True
        return True

    def stop(self, **_kwargs) -> None:
        self.stops += 1
        self.started = False

    def close(self) -> None:
        self.closed = True
        self.started = False

    def set_interval(self, interval: float) -> float:
        self.interval = interval
        return self.interval

    def snapshot(self):
        return self.sample


class FakeScreen:
    """屏幕替身：动态岛只问 ``availableGeometry()``（多屏/窄屏都是它）。"""

    def __init__(self, width: int, height: int = 768, left: int = 0, top: int = 0):
        self._rect = QRect(left, top, width, height)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


def _config(tmp_path, *, metrics=(), **overrides) -> Config:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 40, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return cfg


def _island(tmp_path, monkeypatch, *, metrics=(), screen_width=1200, **overrides):
    made: list[StubMonitor] = []

    def factory(_self, interval):
        monitor = StubMonitor(interval)
        made.append(monitor)
        return monitor

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    screen = FakeScreen(screen_width)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    island = DynamicIsland(_config(tmp_path, metrics=metrics, **overrides))
    return island, made, screen


def _segment(island: DynamicIsland, seg_id: str):
    """段管线里的**未收缩**段（折叠不改它：折叠只发生在行计划里）。"""
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}")


def _sys_sample(cpu=42.0, mem=61.0, *, sampled_at=1.0):
    return sm.SysSample(cpu_percent=cpu, mem_percent=mem, sampled_at=sampled_at)


def _narrow_to(island, screen, predicate, *, lo=150, hi=1500, step=4):
    """把岛挪到"收缩级满足 ``predicate``"的那块屏上，返回命中的屏宽。

    **从宽往窄搜**：返回满足条件里最宽的那块屏——它是"刚好要收缩"的那一档，
    宽度仍在预算内（再窄下去会落到保底级，那时溢出是设计行为，测不出 fits）。

    屏宽**按真实字体实测搜索**而不是写死 px：段宽来自当前系统字体，写死的阈值
    换台机器（换字体/DPI）就会落到别的收缩级上，测试会变成环境相关的假红。
    """
    for width in range(hi, lo, -step):
        screen._rect = QRect(0, 0, width, 768)
        island.refresh_from_config()
        plan = island._row_plan()
        if predicate(plan):
            assert island._shrink_budget() == width - 32, "触发线 = 屏可用宽 − 32px"
            return width, plan
    raise AssertionError("在 150–1500px 的屏宽里找不到满足条件的收缩级")


# ------------------------------------------------------------ 窄屏收缩 + 退订
def test_narrow_screen_folds_tail_segments_and_unsubscribes_them(tmp_path, monkeypatch):
    metrics = [NETWORK, CPU, MEM]
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=metrics,
                                    screen_width=1400)
    try:
        island.show()
        app.processEvents()
        assert island._row_plan().folded == (), "先给一块够宽的屏"
        net_stub, sys_stub = island._net_monitor, island._sys_monitor

        # 只折尾部那一段（内存）：另外两段还在画 → 采样也必须还在
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert plan.folded == (MEM,), "折叠顺序 = 段序的严格倒序（尾部优先）"
        assert MEM in sys_stub.unsubscribed, "折掉的段必须退订"
        assert CPU in sys_stub.subscribed, "没被折的兄弟段不许被牵连"
        assert net_stub.started is True and net_stub.closed is False
        assert island._row_plan().visible[-1] == "chip"

        # 继续窄到把网络段也折掉：整条网络采样器收口
        _width, plan = _narrow_to(island, screen, lambda p: NETWORK in p.folded)
        assert net_stub.closed is True, "折掉的网络段必须收口（撤销订阅）"
        assert island._net_monitor is None and island._net_sample is None
    finally:
        island.close()


def test_unfolded_segment_resubscribes_and_starts_from_dashes(tmp_path, monkeypatch):
    """恢复出采样段：重订阅 + 首轮 ``--``（不显示折叠前的陈旧值）。"""
    metrics = [CPU, MEM]
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=metrics,
                                    screen_width=1200)
    try:
        island.show()
        app.processEvents()
        sys_stub = island._sys_monitor
        sys_stub.sample = _sys_sample()
        island._tick_sys()
        assert island._row_plan().folded == (), "宽屏不折叠"
        assert _segment(island, MEM).text == "61"

        # 只折尾部（内存）、CPU 还活着：共享采样器不重建，陈旧读数的坑正在这里
        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (MEM,))
        assert island._sys_monitor is sys_stub, "还有段在采：采样器不该被重建"
        assert MEM in sys_stub.unsubscribed
        assert sys_stub.snapshot() is not None, "快照还在（兄弟段仍要读它）"

        screen._rect = QRect(0, 0, 1200, 768)     # 搬回宽屏 → 恢复
        island.refresh_from_config()
        assert island._row_plan().folded == ()
        assert MEM in sys_stub.subscribed, "恢复必须重新订阅"
        restored = _segment(island, MEM)
        assert restored.state == isl.STATE_SAMPLING and restored.text == "--", \
            "恢复的第一轮是 sampling（不拿折叠前的读数冒充当下）"
        assert _segment(island, CPU).state == isl.STATE_OK, "一直没被折的段照常报读数"

        # 新快照到手之后才报读数
        sys_stub.sample = _sys_sample(cpu=7.0, mem=8.0, sampled_at=2.0)
        island._tick_sys()
        assert _segment(island, MEM).state == isl.STATE_OK
        assert _segment(island, MEM).text == "8"
    finally:
        island.close()


def test_folded_network_stops_sampling_and_unfold_recreates_it(tmp_path, monkeypatch):
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=[NETWORK],
                                    screen_width=1400)
    try:
        island.show()
        app.processEvents()
        first = island._net_monitor
        assert first is not None and first.started is True

        _width, plan = _narrow_to(island, screen, lambda p: p.folded == (NETWORK,))
        assert plan.visible == (_PRIMARY, "chip"), "保底只剩主信息 + chip"
        assert first.closed is True and first.started is False
        assert island._net_monitor is None and island._net_sample is None

        screen._rect = QRect(0, 0, 1400, 768)
        island.refresh_from_config()
        island.show()
        app.processEvents()
        assert island._row_plan().folded == ()
        assert island._net_monitor is not None and island._net_monitor is not first
        assert island._net_monitor.started is True
        assert _segment(island, NETWORK).state == isl.STATE_SAMPLING
    finally:
        island.close()


def test_fold_transition_reenters_the_width_whitelist(tmp_path, monkeypatch):
    """折叠/展开是**内容结构**变化：必须走白名单（键变），采样值变化则不许变。"""
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=[CPU, MEM],
                                    screen_width=1200)
    try:
        island.show()
        app.processEvents()
        wide_key = island._width_key()
        wide_width = island.width()

        _width, plan = _narrow_to(island, screen, lambda p: bool(p.folded))
        folded_key = island._width_key()
        assert folded_key != wide_key, "折叠态必须进白名单键"
        assert island.width() < wide_width, "收缩后自然宽必须变小"
        assert island.width() <= island._shrink_budget()

        island._sys_monitor.sample = _sys_sample(cpu=99.0, mem=99.0)
        island._tick_sys()
        assert island._width_key() == folded_key, "采样值不进白名单（哪怕折叠态）"
        assert plan.folded
    finally:
        island.close()


def test_dragging_freezes_the_width_and_the_shrink_level(tmp_path, monkeypatch):
    """拖拽中冻结：既不重算宽度也不重算收缩级，松手落定后才按新屏重算。"""
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=[CPU, MEM],
                                    screen_width=1200)
    try:
        island.show()
        app.processEvents()
        before = island.width()

        island._dragging = True
        screen._rect = QRect(0, 0, 200, 768)   # 拖到极窄屏：这一帧什么都不许动
        island.refresh_from_config()
        assert island.width() == before, "拖拽中改宽 = 岛在光标底下抽动"
        assert island._row_plan().folded == (), "收缩级同样冻结（不然内容先跳）"

        island._dragging = False
        island.refresh_from_config()
        assert island.width() < before, "松手后才按新屏收缩"
        assert island._row_plan().folded
    finally:
        island.close()


def test_screen_migration_recomputes_the_shrink_level(tmp_path, monkeypatch):
    metrics = [NETWORK, CPU, MEM]
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=metrics,
                                    screen_width=1400)
    try:
        island.show()
        app.processEvents()
        assert island._row_plan().level == LEVEL_NONE

        _width, plan = _narrow_to(island, screen, lambda p: len(p.folded) >= 2)
        assert plan.level > LEVEL_NAME_HIDE, "窄屏要折段，不只是省名字"

        screen._rect = QRect(0, 0, 1400, 768)
        island.refresh_from_config()
        assert island._row_plan().level == LEVEL_NONE, "搬回宽屏要逆序全恢复"
    finally:
        island.close()


def test_chip_paints_without_crashing(tmp_path, monkeypatch):
    """画得出来才算数：带 chip 的内容层与整窗重绘都不许抛。"""
    island, _made, screen = _island(tmp_path, monkeypatch, metrics=[CPU, MEM],
                                    screen_width=1200)
    try:
        island.show()
        app.processEvents()
        _narrow_to(island, screen, lambda p: bool(p.folded))
        pixmap = island._content_cache()
        assert pixmap is not None and not pixmap.isNull()
        assert not island.grab().isNull()
    finally:
        island.close()
