# -*- coding: utf-8 -*-
"""灵动岛分段渲染（P1 Slice D1）的契约测试：段管线 + 行内几何 + 宽度白名单。

三条硬约束（规格 §D1）：

1. **段是数据不是绘制串**：``_compose_info_segments()`` 首段恒为主信息（time /
   custom / balance / balance_tier 四模式），其后按 ``island_metrics`` 的列表序
   追加指标段。网络不再是"接管信息槽"的特殊形态（那是 P1 要修的核心缺陷），
   未实现的 id 降级成 ``--`` 段（不崩、不留洞）。
2. **几何是纯函数**：``place_info_segments()`` 收"段列表 + 度量对象"返回每段的
   label / 字段盒 / 竖线 x 落位。本文件用替身度量断言 x 偏移序列，不截图。
3. **宽度白名单**：自然宽只在「段增删 / 段模板变化 / 名字或主信息文本变化」时
   重算；**采样值变化只重建内容层位图**，一次都不许摸 ``_update_size``。

采样器一律替身（``_make_net_monitor`` / ``_make_sys_monitor`` 替换点）：测试不发
探测包、不碰 psutil。采样生命周期（订阅/休眠/收口）由
``tests/test_island_network_slot.py`` 与 ``tests/test_island_device_slot.py`` 覆盖。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
import pet.island.layout as island_layout_mod
from pet.island import balance as balance_mod
from pet import catalog
from pet.island import segments as isl
from pet import net_monitor, sys_monitor
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.island.layout import _InfoSeg, place_info_segments
from pet.island.segments import CPU, MEM, NETWORK

app = QApplication.instance() or QApplication([])

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


class _StubMonitor:
    """采样器替身：只记录生命周期，样本由测试塞进去。"""

    def __init__(self, interval: float = net_monitor.DEFAULT_INTERVAL_SECONDS):
        self.interval = interval
        self.sample = None
        self.starts = 0
        self.stops = 0
        self.closed = False
        self.started = False
        self.subscribed: set[str] = set()
        #: GPU 那套订阅各自记账（与真底座的两套计数同形）；``gpu_calls`` 是
        #: "有没有段在碰 GPU 这条路"的总探针——CPU-only 用例靠它证明一次都没碰。
        self.gpu_sample = None
        self.gpu_subscribed: set[str] = set()
        self.gpu_unsubscribed: list[str] = []
        self.gpu_calls = 0

    def start(self) -> bool:
        """起始幂等（与真 ``SysMonitor.start`` 同契约：已在跑就返回 False）。"""
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

    def subscribe(self, key: str) -> int:
        self.subscribed.add(str(key))
        return len(self.subscribed)

    def unsubscribe(self, key: str) -> int:
        self.subscribed.discard(str(key))
        return len(self.subscribed)

    def snapshot(self):
        return self.sample

    def subscribe_gpu(self, key: str) -> int:
        self.gpu_calls += 1
        self.gpu_subscribed.add(str(key))
        return len(self.gpu_subscribed)

    def unsubscribe_gpu(self, key: str) -> int:
        self.gpu_calls += 1
        self.gpu_unsubscribed.append(str(key))
        self.gpu_subscribed.discard(str(key))
        return len(self.gpu_subscribed)

    def gpu_snapshot(self):
        self.gpu_calls += 1
        return self.gpu_sample


class _StubConfig:
    """最小配置替身：允许把**未归一化**的 id 塞进 ``island_metrics``。

    真 ``Config`` 会把未知 id 归一化掉（见 ``tests/test_island_metrics_config.py``），
    所以"配置里残留没实现的段"这条防崩溃路径只能用替身复现。
    """

    def __init__(self, **data):
        self._data = {"dynamic_island": {
            "enabled": True, "show_icon": True, "show_name": True, "show_info": True,
            "info_mode": "time", "custom_text": "", "show_status": True,
            "style": "dark",
        }}
        self._data.update(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value) -> None:
        self._data[key] = value

    def save(self) -> bool:
        return True

    def character_alias(self, character_id: str) -> str:
        aliases = self._data.get("character_aliases")
        return str(aliases.get(character_id, "")) if isinstance(aliases, dict) else ""


def _config(tmp_path, **overrides) -> Config:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
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


def _seg(segments, seg_id: str) -> _InfoSeg:
    for segment in segments:
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(f"段列表里没有 {seg_id!r}：{[s.seg_id for s in segments]}")


def _net_sample(down=2048.0, up=512.0, latency=23.0, lost=False):
    return net_monitor.NetSample(
        down_bps=down, up_bps=up, latency_ms=latency, latency_lost=lost, sampled_at=1.0)


def _sys_sample(cpu=24.0, mem=61.0, *, state=isl.STATE_OK, reason=""):
    return sys_monitor.SysSample(
        cpu_percent=cpu, mem_percent=mem, sampled_at=1.0, state=state, error_reason=reason)


# ------------------------------------------------------------ 段序列 / 段文案
def test_primary_is_the_only_segment_without_metrics(tmp_path, monkeypatch):
    """默认（无指标段）：段列表只有主信息段，时间是 "88:88" 定宽样本。"""
    island, _made = _island(_config(tmp_path), monkeypatch)
    try:
        segments = island._compose_info_segments()
        assert [segment.kind for segment in segments] == ["primary"]
        primary = segments[0]
        assert primary.seg_id == _PRIMARY and primary.label == ""
        assert primary.box_text == "88:88", "时间按模板定宽（数字跳动不推邻居）"
        assert primary.state == isl.STATE_OK
        now = QTime.currentTime()
        expected = {now.addSecs(offset).toString("HH:mm") for offset in (-60, 0, 60)}
        assert primary.text in expected, primary.text
    finally:
        island.close()


def test_custom_mode_uses_the_configured_text_as_sample(tmp_path, monkeypatch):
    island, _made = _island(
        _config(tmp_path, info_mode="custom", custom_text="  摸鱼中  "), monkeypatch)
    try:
        primary = _seg(island._compose_info_segments(), _PRIMARY)
        assert primary.text == "摸鱼中"
        assert primary.box_text == "摸鱼中", "自定义文本自身就是测宽样本"
    finally:
        island.close()


def test_custom_mode_with_blank_text_falls_back_to_default(tmp_path, monkeypatch):
    island, _made = _island(_config(tmp_path, info_mode="custom", custom_text="   "),
                            monkeypatch)
    try:
        assert _seg(island._compose_info_segments(), _PRIMARY).text == "自定义"
    finally:
        island.close()


def test_balance_mode_uses_balance_text_and_its_sample(tmp_path, monkeypatch):
    island, _made = _island(_config(tmp_path, info_mode="balance"), monkeypatch)
    try:
        island.set_balance_info("高峰 → 23:00", "余额 ¥12.34")
        primary = _seg(island._compose_info_segments(), _PRIMARY)
        assert primary.text == "余额 ¥12.34"
        assert primary.box_text == "余额 ¥88888.88", "样本必须覆盖真实最长单行形态"
    finally:
        island.close()


def test_balance_tier_mode_keeps_its_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(balance_mod, "deepseek_pricing_tier", lambda: "peak")
    monkeypatch.setattr(balance_mod, "next_pricing_switch", lambda _now: ("idle", None))
    monkeypatch.setattr(balance_mod, "format_switch_time", lambda _now, _next: "12:00")
    island, _made = _island(_config(tmp_path, info_mode="balance_tier"), monkeypatch)
    try:
        primary = _seg(island._compose_info_segments(), _PRIMARY)
        assert primary.text == "高峰 → 12:00"
        assert primary.box_text == "低谷 → 88:88"
    finally:
        island.close()


def test_metrics_follow_the_config_list_order(tmp_path, monkeypatch):
    """列表序 = 用户优先级（尾部先被折叠）：段序必须与列表序一致。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [MEM, CPU, NETWORK])
    island, _made = _island(cfg, monkeypatch)
    try:
        assert [segment.seg_id for segment in island._compose_info_segments()] == \
            [_PRIMARY, MEM, CPU, NETWORK]
    finally:
        island.close()


def test_network_segment_is_a_metric_segment_not_an_info_takeover(tmp_path, monkeypatch):
    """网络段是普通指标段：主信息段仍由 info_mode 决定（本次核心修复）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        # 还没出第一轮快照：占位是四态的 "--"（不伪造 0B 读数）
        sampling = _seg(island._compose_info_segments(), NETWORK)
        assert sampling.state == isl.STATE_SAMPLING
        assert sampling.text == "--"
        assert sampling.label == "", "网络段是纯 glyph 段（↓↑ 在定宽槽串里）"
        now = QTime.currentTime()
        assert island._info_text() in {
            now.addSecs(offset).toString("HH:mm") for offset in (-60, 0, 60)}

        made[0].sample = _net_sample()
        island._tick_network()
        grown = _seg(island._compose_info_segments(), NETWORK)
        assert grown.state == isl.STATE_OK
        assert grown.text == net_monitor.format_network_slot(2048.0, 512.0, 23.0)
        assert grown.box_text  # 定宽样本：盒宽与读数无关
        assert len(grown.box_text) >= len(grown.text), "样本必须覆盖定宽槽串的最坏形态"
    finally:
        island.close()


def test_network_box_sample_is_a_realistic_envelope(tmp_path, monkeypatch):
    """盒宽样本取「千兆满速 + 近秒级延迟」的现实包络，不是 G 级的病态最坏形态。

    样本串决定盒宽：包络定得太宽，典型读数（K 级速率 + 两位毫秒）右对齐后会在
    段内留下大块空白（P1 实机截图自审发现的视觉缺陷）；包络外（G 级速率 /
    四位毫秒）由字段盒右省略护栏承接（R1 评审修复：`7947a18` 的 max(样本,现文案)
    让采样值经 box_text 变相进宽度键，临界预算下会折叠振荡——盒宽必须恒为样本）。
    """
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        # 包络内（千兆满速 + 三位毫秒）：盒宽恒为声明样本，与读数无关
        made[0].sample = _net_sample(
            down=500 * 1024 ** 2, up=120 * 1024 ** 2, latency=250.0)
        island._tick_network()
        within = _seg(island._compose_info_segments(), NETWORK)
        assert within.box_text == island_layout_mod._NETWORK_BOX_SAMPLE
        assert "G" not in island_layout_mod._NETWORK_BOX_SAMPLE, (
            "样本不取 G 级病态形态（典型读数会在盒内留出大块空白）")
        # 包络外（G 级速率 / 四位毫秒）：盒宽仍恒为样本（采样值不进宽度键），
        # 显示由字段盒右省略护栏承接——不再加宽、不再省略进键
        made[0].sample = _net_sample(
            down=1023 * 1024 ** 3, up=1023 * 1024 ** 3, latency=1000.0)
        island._tick_network()
        beyond = _seg(island._compose_info_segments(), NETWORK)
        assert beyond.box_text == island_layout_mod._NETWORK_BOX_SAMPLE, (
            "包络外读数也不许改盒宽（R1：采样值永不进宽度键）")
        assert "G" in beyond.text, "段数据仍是完整读数（省略只发生在绘制层字段盒）"
        # 绘制层落位：超宽串被字段盒右省略（护栏生效、岛宽不变）
        placements = place_info_segments(
            [beyond], start_x=0,
            primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"),
            label_fm=island._metrics_for("label"))
        assert "…" in placements[0].text, "包络外读数应被字段盒右省略"
        assert placements[0].box_w == int(island._metrics_for("value").horizontalAdvance(
            island_layout_mod._NETWORK_BOX_SAMPLE)), "盒宽必须等于声明样本实测宽"
    finally:
        island.close()


def test_cpu_and_mem_segments_use_the_shared_snapshot(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        before = island._compose_info_segments()
        assert _seg(before, CPU).label == "CPU"
        assert _seg(before, MEM).label == "内存"
        assert _seg(before, CPU).box_text + _seg(before, CPU).unit == sys_monitor.MAX_TEXT
        assert {_seg(before, CPU).state, _seg(before, MEM).state} == {isl.STATE_SAMPLING}

        made[0].sample = _sys_sample(cpu=37.5, mem=61.2)
        island._tick_sys()
        after = island._compose_info_segments()
        # Slice E4 双盒：数值盒拿 38、单位盒拿 %，拼回去 = 格式化器的合并串
        assert _seg(after, CPU).text == "38" and _seg(after, CPU).unit == "%"
        assert _seg(after, CPU).state == isl.STATE_OK
        assert _seg(after, MEM).text == "61" and _seg(after, MEM).unit == "%"
        assert _seg(after, MEM).state == isl.STATE_OK
    finally:
        island.close()


def test_cpu_first_differential_sample_stays_sampling(tmp_path, monkeypatch):
    """CPU 差分首样本是 None（psutil 语义）：有快照但没读数 → 仍是"采样中"。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sys_sample(cpu=None, mem=61.2)
        island._tick_sys()
        segments = island._compose_info_segments()
        assert _seg(segments, CPU).text == "--"
        assert _seg(segments, CPU).state == isl.STATE_SAMPLING
        assert _seg(segments, MEM).text == "61", "内存读数不受 CPU 基线影响"
    finally:
        island.close()


def test_error_snapshot_maps_to_error_state(tmp_path, monkeypatch):
    """psutil 不可用：error 快照 → 两个段都报 error + "--"（不伪造 0）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        made[0].sample = _sys_sample(cpu=None, mem=None, state=isl.STATE_ERROR,
                                     reason="PsutilUnavailable: 没装")
        island._tick_sys()
        for metric_id in (CPU, MEM):
            segment = _seg(island._compose_info_segments(), metric_id)
            assert segment.state == isl.STATE_ERROR
            assert segment.text == "--"
    finally:
        island.close()


def test_unknown_metric_id_becomes_a_dash_segment(tmp_path, monkeypatch):
    """配置里残留了没实现的段：降级成 unknown 态 ``--`` 段，不崩也不留洞。

    样本用 ``vram``（不是 ``gpu``）：``gpu`` 自 P3 Slice F1 起是**已知 id**
    （能力占位：胶囊不占位、卡片写原因，见 ``tests/test_island_gpu_placeholder.py``），
    拿它当"未实现的段"会测到另一条路径。
    """
    island, _made = _island(_StubConfig(island_metrics=["vram", "temperature"]), monkeypatch)
    try:
        segments = island._compose_info_segments()
        assert [segment.seg_id for segment in segments] == [_PRIMARY, "vram", "temperature"]
        vram = _seg(segments, "vram")
        assert vram.kind == "metric"
        assert vram.label == "vram"
        assert vram.text == "--" and vram.box_text == isl.UNKNOWN_TEXT
        assert vram.state == isl.STATE_UNKNOWN
        assert _seg(segments, "temperature").label == "temper", "label 上限 6 字符"
    finally:
        island.close()


def test_info_slot_off_composes_no_segments(tmp_path, monkeypatch):
    """信息槽整体关闭：一条信息段都不画（"没有段"与"首段不是 primary"是两件事）。"""
    cfg = _config(tmp_path, show_info=False)
    cfg.set("island_metrics", [NETWORK, CPU])
    island, _made = _island(cfg, monkeypatch)
    try:
        assert island._compose_info_segments() == []
    finally:
        island.close()


# ------------------------------------------------------------ 行内几何（纯函数）
def test_layout_puts_metrics_in_a_19px_divider_slot():
    fm = FakeMetrics(step=10)
    segments = [
        _InfoSeg(kind="primary", seg_id=_PRIMARY, label="", text="12:00",
                 box_text="88:88", state=isl.STATE_OK),
        _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text="24%",
                 box_text="888%", state=isl.STATE_OK),
    ]
    primary, cpu = place_info_segments(
        segments, start_x=19.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert primary.divider_x is None, "首段前不画竖线"
    assert (primary.label_x, primary.box_x, primary.box_w) == (19.0, 19.0, 50)
    assert primary.right == 69.0
    assert cpu.divider_x == 69.0 + 9.0, "竖线落在 19px 槽位正中（9 + 1 + 9）"
    assert cpu.label_x == 88.0, "段起点 = 上一段右缘 + 19px 槽位"
    assert cpu.box_x == 88.0 + 30 + isl.LABEL_GAP_PX, "标签宽 + 固定间距"
    assert cpu.box_w == 40, "盒宽 = 声明样本的实测宽"
    assert cpu.right == 122.0 + 40


def test_layout_right_aligns_the_value_inside_its_box():
    fm = FakeMetrics(step=10)
    segment = _InfoSeg(kind="metric", seg_id=CPU, label="", text="9%",
                       box_text="888%", state=isl.STATE_OK)
    (placement,) = place_info_segments(
        [segment], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert placement.box_w == 40
    assert placement.text_w == 20
    # 盒内右对齐：9% → 100% 只在盒内左扩，盒右缘不动
    assert placement.box_x + placement.box_w - placement.text_w == 20.0


def test_layout_elides_an_overlong_metric_value_without_widening_the_box():
    fm = FakeMetrics(step=10)
    segment = _InfoSeg(kind="metric", seg_id=NETWORK, label="",
                       text="↓ 1.2M ↑ 340K 23ms", box_text="888%", state=isl.STATE_OK)
    (placement,) = place_info_segments(
        [segment], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert placement.box_w == 40, "指标段盒宽与读数无关：读数再长也不推邻居"
    assert placement.text != segment.text and placement.text.endswith(isl.ELLIPSIS)
    assert placement.text_w <= placement.box_w


def test_layout_grows_the_primary_box_instead_of_eliding_it():
    """主信息超样本（带充值/赠送明细的余额串）：按现文案加宽，不静默吃字。"""
    fm = FakeMetrics(step=10)
    text = "余额 ¥123.45（充值 ¥200.00 / 赠送 ¥0.00）"
    segment = _InfoSeg(kind="primary", seg_id=_PRIMARY, label="", text=text,
                       box_text="余额 ¥88888.88", state=isl.STATE_OK)
    (placement,) = place_info_segments(
        [segment], start_x=0.0, primary_fm=fm, value_fm=fm, label_fm=fm)
    assert placement.text == text
    assert placement.box_w == len(text) * 10


def test_layout_box_width_follows_the_declared_sample():
    """段模板（样本）换了盒宽就换——这是"模板变化"进入宽度白名单的那条路。"""
    fm = FakeMetrics(step=10)
    narrow = _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text="24%",
                      box_text="888%", state=isl.STATE_OK)
    wide = _InfoSeg(kind="metric", seg_id=CPU, label="CPU", text="24%",
                    box_text="8888%", state=isl.STATE_OK)
    (before,) = place_info_segments([narrow], start_x=0.0, primary_fm=fm,
                                    value_fm=fm, label_fm=fm)
    (after,) = place_info_segments([wide], start_x=0.0, primary_fm=fm,
                                   value_fm=fm, label_fm=fm)
    assert after.box_w == before.box_w + 10
    assert after.right == before.right + 10


def test_divider_color_follows_the_style(tmp_path, monkeypatch):
    """竖线色比 keyline 再弱一档（暗 #FFFα36 / 亮 #1F2328α31 / glass #232D3Cα36）。"""
    expected = {
        "dark": QColor(255, 255, 255, 36),
        "light": QColor(31, 35, 40, 31),
        "glass": QColor(35, 45, 60, 36),
    }
    for style, color in expected.items():
        cfg = _config(tmp_path / style, style=style)
        island, _made = _island(cfg, monkeypatch)
        try:
            assert island._divider_color() == color, style
        finally:
            island.close()


# ------------------------------------------------------------ 宽度白名单
def test_sampling_change_rebuilds_content_but_never_resizes(tmp_path, monkeypatch):
    """白名单硬约束：只改采样快照 → 几何不变，内容层位图照样重建。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK, CPU])
    island, _made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        geometry = island.geometry()
        width_key = island._width_key()
        pixmap = island._content_cache()

        island._net_monitor.sample = _net_sample(down=987654321.0, up=1024.0, latency=999.0)
        island._tick_network()
        island._sys_monitor.sample = _sys_sample(cpu=97.0, mem=12.0)
        island._tick_sys()

        assert _seg(island._compose_info_segments(), CPU).text == "97", "读数没进段里"
        assert island.geometry() == geometry, "采样值变化不得改窗口几何"
        assert island._width_key() == width_key, "采样值不在自然宽白名单里"
        assert island._content_cache() is not pixmap, "内容层必须按新读数重建"
    finally:
        island.close()


def test_sampling_tick_never_calls_update_size(tmp_path, monkeypatch):
    """结构断言：采样 tick 一次都不许摸 ``_update_size``（宽度白名单的执行面）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK, CPU])
    island, _made = _island(cfg, monkeypatch)
    calls: list[str] = []
    original = island_mod.DynamicIsland._update_size

    def spy(self):
        calls.append("update_size")
        return original(self)

    monkeypatch.setattr(island_mod.DynamicIsland, "_update_size", spy)
    try:
        island.show()
        app.processEvents()
        calls.clear()  # showEvent 的刷新会走一次 _update_size，那是白名单内的正常路径
        island._net_monitor.sample = _net_sample()
        island._tick_network()
        island._sys_monitor.sample = _sys_sample(cpu=88.0)
        island._tick_sys()
        assert calls == [], "采样值变化不许触发几何重算"
    finally:
        island.close()


def test_adding_a_segment_widens_the_island(tmp_path, monkeypatch):
    """段增删是白名单里的第一类触发：多一段必然更宽。"""
    cfg = _config(tmp_path)
    island, _made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        bare_width, bare_key = island.width(), island._width_key()

        cfg.set("island_metrics", [NETWORK])
        island.refresh_from_config()
        one_width, one_key = island.width(), island._width_key()
        assert one_key != bare_key
        assert one_width > bare_width

        cfg.set("island_metrics", [NETWORK, CPU])
        island.refresh_from_config()
        assert island._width_key() != one_key
        assert island.width() > one_width

        # 去掉指标段：宽度回到起点（可逆，不留死几何）
        cfg.set("island_metrics", [])
        island.refresh_from_config()
        assert island.width() == bare_width
    finally:
        island.close()


def test_primary_template_change_recomputes_the_width(tmp_path, monkeypatch):
    """主信息模板变化（time → 长文本自定义）也是白名单触发之一。"""
    cfg = _config(tmp_path)
    island, _made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        before_width, before_key = island.width(), island._width_key()

        island.config.set("dynamic_island", {
            **island._cfg, "info_mode": "custom", "custom_text": "今天也在摸鱼"})
        island.refresh_from_config()
        assert island._width_key() != before_key
        assert island.width() != before_width
        assert island.width() > before_width
    finally:
        island.close()


def test_character_name_change_recomputes_the_width(tmp_path, monkeypatch):
    """角色名变化（别名）同样是白名单触发；名字占位上限 96px。"""
    cfg = _config(tmp_path)
    island, _made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        cfg.set_character_alias(catalog.DEFAULT_CHARACTER, "小鱼")
        island.refresh_from_config()
        short_width, short_key = island.width(), island._width_key()

        cfg.set_character_alias(catalog.DEFAULT_CHARACTER, "小鱼儿她姐姐姐姐姐")
        island.refresh_from_config()
        assert island._width_key() != short_key
        assert island.width() > short_width

        # 超长别名右省略到 96px：再长也不再撑宽（D2 的 S1 直接整段去掉它）
        cfg.set_character_alias(catalog.DEFAULT_CHARACTER, "很" * 24)
        island.refresh_from_config()
        name_text, name_w = island._name_layout()
        assert name_w <= 96
        assert name_text != "很" * 24 and name_text.endswith(isl.ELLIPSIS)
    finally:
        island.close()


def test_content_cache_key_covers_segment_text_and_state(tmp_path, monkeypatch):
    """内容层缓存 key 覆盖各段 text+state；采样值变化重建位图但不改几何。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK])
    island, made = _island(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        first = island._content_cache()
        assert island._content_cache() is first, "同 key 必须命中缓存"

        made[0].sample = _net_sample()
        island._tick_network()
        second = island._content_cache()
        assert second is not first, "段文案变化必须重建内容层"

        # 换一份读数 → 再重建一次（内容层跟着值走）
        made[0].sample = _net_sample(down=1024.0, up=1024.0, latency=7.0)
        island._tick_network()
        third = island._content_cache()
        assert third is not second

        # 同一份快照对象再 tick：什么都不做 → 缓存原样命中
        island._tick_network()
        assert island._content_cache() is third
    finally:
        island.close()
