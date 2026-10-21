# -*- coding: utf-8 -*-
"""灵动岛展开卡片四分区（P2 Slice E5）的契约测试。

四条硬约束（施工单 E5 + 裁决稿 §一-10/§四 k3 卡片四分区）：

1. **四分区各就各位**：概览（主读数大号 + 状态文字，状态五态语义复用 E1）/
   指标详情（每段一行：段名、当前值合并串、单位全称、来源口径、更新时间）/
   最近消息（沿用既有内容）/ 操作（沿用既有按钮行）。
2. **高度按内容实测**：分区高 = 字体度量算出来的账，测试拿
   ``_card_box.layout().sizeHint().height()`` 与 ``_card_height()`` 逐像素对账——
   两者不等就是"账目 ≠ 实际布局"（内容会被挤到轮廓外）。
3. **unsupported 段不占位**：已知 id 但不可用的段（P3 的 GPU 占位）胶囊里连
   ``--`` 都不画（它根本不在段列表里），卡片 B 区写「该指标不可用 + 原因」；
   未知 id 仍旧是胶囊 ``--`` 降级段（既有行为一个像素都不动）。
4. **宽度不吃读数**：卡片宽与三列宽全部由**声明样本**算出，采样值变化改不动
   任何几何（与胶囊的宽度白名单同一纪律）。

采样器一律替身（复用 ``tests/test_island_segment_render.py`` 的 ``_StubConfig`` /
``_StubMonitor``）：本文件不发探测包、不碰 psutil。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLabel

import pet.island.dynamic_island as island_mod
from pet.island import segments as isl
from pet import net_monitor, sys_monitor
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland
from pet.agent_link.dsh_state import DshState
from pet.island.segments import CPU, MEM, NETWORK
from tests.test_island_segment_render import _StubConfig, _StubMonitor
from tests.test_island_shrink_pipeline import FakeScreen, _narrow_to

app = QApplication.instance() or QApplication([])

_PRIMARY = "primary"
_STATUS_LAYERS = ("name", "value", "aux")


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


def _island(config, monkeypatch) -> DynamicIsland:
    """建岛并把两条采样路径换成替身（本文件不验采样生命周期）。"""
    def factory(_self, interval):
        return _StubMonitor(interval)

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor", factory)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor", factory)
    return DynamicIsland(config)


def _sys_sample(cpu=24.0, mem=61.0, *, age=3.0, state=isl.STATE_OK, reason=""):
    """设备槽样本：``age`` 秒前采的（"更新时间"要按真实时钟算）。"""
    return sys_monitor.SysSample(
        cpu_percent=cpu, mem_percent=mem, sampled_at=time.monotonic() - age,
        state=state, error_reason=reason)


def _net_sample(*, age=2.0, down=2048.0, up=512.0, latency=23.0, lost=False):
    return net_monitor.NetSample(
        down_bps=down, up_bps=up, latency_ms=latency, latency_lost=lost,
        sampled_at=time.monotonic() - age)


def _feed_sys(island: DynamicIsland, sample) -> None:
    """走生产路径把样本喂进去（``_tick_sys`` 是读数的唯一入口）。"""
    island._sys_monitor.sample = sample
    island._tick_sys()


def _feed_net(island: DynamicIsland, sample) -> None:
    island._net_monitor.sample = sample
    island._tick_network()


def _expanded(island: DynamicIsland) -> None:
    island.show()
    island.expand_card()
    island._finish_animations()
    app.processEvents()
    island._card_box.layout().activate()
    app.processEvents()


def _rows(island: DynamicIsland) -> dict:
    return {row.seg_id: row for row in island._card_metric_rows()}


def _row_labels(island: DynamicIsland, index: int) -> tuple[QLabel, QLabel, QLabel]:
    return island._card_row_labels[index]


# ------------------------------------------------------------ 四分区存在性
def test_card_has_four_sections_with_their_data_sources(tmp_path, monkeypatch):
    """四分区各就各位：概览（主读数 + 状态文字）/ 指标详情 / 最近消息 / 操作。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM, NETWORK])
    island = _island(cfg, monkeypatch)
    try:
        island.set_balance_info("高峰 → 23:00", "余额 ¥12.34")
        island.set_last_message("我吃了三碗饭")
        _expanded(island)
        assert island._mode == "expanded"

        # A 概览：主读数 + 峰谷 + 状态文字（状态点与文字同源）
        assert island._card_balance_label.text() == "余额 ¥12.34"
        assert island._card_tier_label.text() == "高峰 → 23:00"
        assert island._card_status_label.text() == island._card_status_text() != ""
        assert island._card_status_label.text() == "空闲"
        assert QColor(island._card_status_dot.grab().toImage().pixelColor(4, 4)).isValid()

        # B 指标详情：每段一行，五列素材齐备
        rows = island._card_metric_rows()
        assert [row.seg_id for row in rows] == [CPU, MEM, NETWORK]
        assert [row.name for row in rows] == ["CPU", "内存", "网络"]
        for row in rows:
            assert row.value, row.seg_id
            assert row.unit_full, row.seg_id
            assert row.source, row.seg_id
            assert row.updated, row.seg_id
            assert row.detail == " · ".join(
                (row.unit_full, row.source, row.updated)), row.seg_id
        assert len(island._card_row_widgets) == len(rows)
        assert island._card_metrics.isVisible()

        # C 最近消息：沿用既有内容与既有标签
        assert island._card_message_label.text() == "我吃了三碗饭"
        assert island._card_message_box.isVisible()

        # D 操作：沿用既有三个按钮
        assert (island._card_toggle_btn.text(), island._card_chat_btn.text(),
                island._card_settings_btn.text()) == ("隐藏桌宠", "聊天", "设置")
        assert island._card_actions.isVisible()
    finally:
        island.close()


def test_card_metric_row_carries_the_merged_value_and_full_context(tmp_path, monkeypatch):
    """值列是**合并串**（含单位），而胶囊段是拆开的数值——两层各有各的口径。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_sys(island, _sys_sample(cpu=38.4, mem=61.2, age=3.0))
        _expanded(island)

        capsule = {seg.seg_id: seg for seg in island._compose_info_segments()}
        assert capsule[CPU].text == "38", "胶囊里数值与单位分盒（E4 双盒）"
        assert capsule[CPU].unit == "%"

        rows = _rows(island)
        assert rows[CPU].value == "38%", "卡片值是格式化器的合并串"
        assert rows[MEM].value == "61%"
        assert rows[CPU].unit_full == "百分比"
        assert rows[CPU].source == "本机采样"
        assert rows[CPU].updated == "3 秒前"
        assert rows[CPU].detail == "百分比 · 本机采样 · 3 秒前"
        assert "psutil" not in rows[CPU].detail, \
            "来源行说人话：采样库名是实现细节，不进用户视线（与网络段同口径）"
    finally:
        island.close()


def test_card_shows_network_row_with_its_own_source(tmp_path, monkeypatch):
    """网络段行：值是 5 列槽串（跨模块契约），来源行与 CPU/内存同结构且无实现黑话。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [NETWORK])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_net(island, _net_sample(age=2.0))
        _expanded(island)
        row = _rows(island)[NETWORK]
        assert row.value == net_monitor.format_network_slot(2048.0, 512.0, 23.0)
        assert row.unit_full == "速率 KB/s · 延迟 ms"
        assert row.source == "本机采样"
        assert row.updated == "2 秒前"
        assert row.detail == "速率 KB/s · 延迟 ms · 本机采样 · 2 秒前"
        assert "网卡" not in row.detail and "DNS" not in row.detail, \
            "来源行说人话：读网卡计数器 / 探 DNS 都是实现细节，不进用户视线"
        assert row.name == "网络", "胶囊里网络是纯 glyph，卡片得有名字"
    finally:
        island.close()


def test_card_falls_back_to_text_dialect_before_the_first_sample(tmp_path, monkeypatch):
    """冷启动首轮：胶囊 ``--``、卡片值列 ``--`` + 来源行注「采样中」（不伪造 0）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        row = _rows(island)[CPU]
        assert row.state == isl.STATE_SAMPLING
        assert row.value == "--"
        assert row.updated == "采样中"
        assert row.detail.endswith("采样中")
        assert island._card_metrics.isVisible()
    finally:
        island.close()


def test_card_detail_drops_the_unit_name_before_eliding(tmp_path, monkeypatch):
    """详情列放不下时的降级顺序：先省单位全称（值列已有缩写），再右省略兜底——
    "来源口径 + 更新时间"是卡片独有的信息，不该第一个被切掉。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_sys(island, _sys_sample(cpu=38.4, mem=61.2, age=3.0))
        _expanded(island)
        row = _rows(island)[CPU]
        metrics = island._metrics_for("aux")
        full_w = int(metrics.horizontalAdvance(row.detail))
        short_w = int(metrics.horizontalAdvance(row.detail_short))
        assert short_w < full_w
        assert row.unit_full not in row.detail_short
        assert row.source in row.detail_short and row.updated in row.detail_short

        assert island._card_detail_display(row, full_w) == row.detail
        assert island._card_detail_display(row, short_w) == row.detail_short
        tight = island._card_detail_display(row, short_w - 20)
        assert tight.endswith("…") and len(tight) < len(row.detail_short)
        assert tight.startswith(row.source[:2]), "兜底省略也要从来源口径起头"
    finally:
        island.close()


def test_folded_segment_row_says_its_sampling_is_paused(tmp_path, monkeypatch):
    """卡片列的是**全量段**：被胶囊折进 +N 的段没在采样（性能契约），
    卡片如实标注，不拿"采样中"糊弄（采样闸门语义不动）。

    收缩级靠**真实字体实测搜索**（``test_island_shrink_pipeline._narrow_to``），
    不写死预算 px：段宽来自当前系统字体，写死的阈值换台机器（换字体/DPI）就会落到
    别的收缩级上——mac CI 上写死 580 时整个行都不会折，这条用例变成环境相关的假红。
    """
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM, NETWORK])
    screen = FakeScreen(1400)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        # 从宽往窄搜到"刚好折掉尾部那一段（网络）"的一档：另外两段还在画
        _narrow_to(island, screen, lambda plan: plan.folded == (NETWORK,))
        assert island._row_plan().folded == (NETWORK,)
        # 样本在收缩级**定下来之后**再喂：折叠只由段结构与盒宽样本决定，与读数无关；
        # 而卡片的"更新时间"是按真实时钟算的，搜索本身要跑几百次 refresh_from_config
        # （慢机更久），先喂就会把 2 秒跨成 3 秒——那是时钟边界，不是行为回归。
        _feed_sys(island, _sys_sample(cpu=38.0, mem=61.0, age=2.0))
        _expanded(island)
        rows = _rows(island)
        assert set(rows) == {CPU, MEM, NETWORK}
        assert rows[CPU].updated == "2 秒前", "可见段照常采"
        assert rows[NETWORK].value == isl.UNKNOWN_TEXT
        assert rows[NETWORK].updated == "已折叠 · 暂停采样"
        assert rows[NETWORK].detail.endswith("已折叠 · 暂停采样")
    finally:
        island.close()


def test_sample_error_writes_the_reason_instead_of_a_reading(tmp_path, monkeypatch):
    """采样失败：值列不染色（胶囊上段的错误保持安静），卡片详情列写原因。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_sys(island, _sys_sample(state=isl.STATE_ERROR, reason="psutil 返回空"))
        _expanded(island)
        row = _rows(island)[CPU]
        assert row.state == isl.STATE_ERROR
        assert row.value == "--"
        assert row.detail == "该指标不可用：psutil 返回空"
    finally:
        island.close()


# ------------------------------------------------------------ 高度账目
def test_card_height_is_measured_and_matches_the_layout(tmp_path, monkeypatch):
    """高度账目：``_card_height()`` 与真实布局逐像素相等，且随段数按行高递增。"""
    heights = {}
    for metrics in ([], [CPU], [CPU, MEM], [CPU, MEM, NETWORK]):
        cfg = _config(tmp_path)
        cfg.set("island_metrics", list(metrics))
        island = _island(cfg, monkeypatch)
        try:
            _expanded(island)
            accounted = island._card_height()
            laid_out = island._card_box.layout().sizeHint().height()
            assert accounted == laid_out, f"{metrics}: 账目 {accounted} ≠ 布局 {laid_out}"
            assert island.height() == island_mod._CAPSULE_HEIGHT + accounted
            for section in (island._card_overview, island._card_metrics,
                            island._card_message_box, island._card_actions):
                if not section.isVisible():
                    continue
                assert section.layout().sizeHint().height() <= section.height(), \
                    "分区内容比分区固定高还大 → 会被挤到轮廓外"
            heights[len(metrics)] = accounted
        finally:
            island.close()
    assert heights[0] < heights[1] < heights[2] < heights[3], \
        f"高度没跟内容走：{heights}"

    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        row_h = island._card_row_height()
        # 0 段 → 1 段多出的是"整区"（分区间距 + 1px 分隔线 + 一行）
        assert heights[1] - heights[0] == 2 * island_mod._CARD_SECTION_GAP + island_mod._CARD_SEP_PX + row_h
        # 之后每多一段只多一行（行高是实测值，不是写死的 22）
        assert heights[2] - heights[1] == row_h
        assert heights[3] - heights[2] == row_h
    finally:
        island.close()


def test_card_drops_the_whole_metric_section_when_there_is_no_metric(tmp_path, monkeypatch):
    """没有指标段：整区连它的分隔线一起消失（不留悬空的分隔线）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        assert island._card_metric_rows() == []
        assert island._card_metrics.isVisible() is False
        assert island._card_metrics.height() == 0 or not island._card_metrics.isVisible()
        assert island._card_message_box.isVisible()
    finally:
        island.close()


# ------------------------------------------------------------ 排版账目
def test_card_row_columns_share_a_baseline_and_never_leave_the_card(tmp_path, monkeypatch):
    """三列共底（基线对齐）+ 列宽和 = 内容宽：行不可能画出卡片轮廓。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM, NETWORK])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        rows = island._card_metric_rows()
        name_w, value_w, detail_w = island._card_column_widths(rows)
        assert name_w + 2 * island_mod._CARD_COL_GAP + value_w + detail_w == \
            island._card_content_width()
        assert island._card_width() == island.width()
        for widget in island._card_row_widgets:
            labels = [child for child in widget.children() if isinstance(child, QLabel)]
            baselines = set()
            for label, width in zip(labels, (name_w, value_w, detail_w)):
                assert label.width() == width
                assert label.contentsMargins().top() + label.fontMetrics().ascent() \
                    == island._card_row_baseline(), "三列基线不共底"
                baselines.add(label.contentsMargins().top()
                              + label.fontMetrics().ascent())
            assert baselines == {island._card_row_baseline()}
            columns = sum(label.width() for label in labels) \
                + island_mod._CARD_COL_GAP * (len(labels) - 1)
            assert columns == island._card_content_width()
            assert widget.x() + columns <= island.width() - island_mod._CARD_MARGIN_X
    finally:
        island.close()


def test_card_content_keeps_a_symmetric_margin_inside_the_panel(tmp_path, monkeypatch):
    """内容与卡片轮廓的左右边距对称（E7 排版纪律在卡片上的落点）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM, NETWORK])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        box = island._card_box
        assert box.x() == 0 and box.width() == island.width()
        assert box.layout().contentsMargins().left() == island_mod._CARD_MARGIN_X
        assert box.layout().contentsMargins().right() == island_mod._CARD_MARGIN_X
        for section in (island._card_overview, island._card_metrics,
                        island._card_message_box, island._card_actions):
            assert section.x() == island_mod._CARD_MARGIN_X
            assert section.width() == island.width() - 2 * island_mod._CARD_MARGIN_X
        # 分隔线也在这个边距里（左右不出头）
        separators = [child for child in island._card_metrics.children()
                      if not isinstance(child, QLabel)]
        assert separators, "B 区该带一条横分隔线"
        assert island._card_metrics_layout.contentsMargins().left() == 0
    finally:
        island.close()


# ------------------------------------------------------------ 宽度不吃读数
def test_card_width_ignores_the_sampling_reading(tmp_path, monkeypatch):
    """采样值变化（38% → 100%）改不动卡片宽与三列宽：宽度只认声明样本。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_sys(island, _sys_sample(cpu=9.0, mem=9.0, age=1.0))
        _expanded(island)
        before = (island._card_width(), island._card_column_widths(
            island._card_metric_rows()))
        _feed_sys(island, _sys_sample(cpu=100.0, mem=100.0, age=0.0))
        island._sync_card_labels()
        after = (island._card_width(), island._card_column_widths(
            island._card_metric_rows()))
        assert before == after, f"读数改动了卡片几何：{before} → {after}"
        assert _rows(island)[CPU].value == "100%"
    finally:
        island.close()


def test_card_width_is_clamped_between_340_and_420(tmp_path, monkeypatch):
    """内容驱动的宽度钳在 [340, 420]；胶囊更宽时窗口按胶囊宽（同一个面板）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU, MEM, NETWORK])
    island = _island(cfg, monkeypatch)
    try:
        _expanded(island)
        capsule = island._capsule_width()
        # 只给"内容驱动"的那一项设上下限：窗口宽 = max(胶囊自然宽, 钳后的内容宽)
        assert max(island_mod._CARD_WIDTH, capsule) \
            <= island._card_width() <= max(island_mod._CARD_WIDTH_MAX, capsule)
        assert island.width() == island._card_width()
    finally:
        island.close()


# ------------------------------------------------------------ unsupported 不占位
def test_unavailable_segment_takes_no_capsule_slot(tmp_path, monkeypatch):
    """已知 id 但不可用（P3 GPU 占位）：胶囊里**连 ``--`` 都不占**。

    证据是三处都摸不到它：段列表没有、行计划不画（也折不到 +N）、自然宽与
    "配置里根本没有这个 id"逐像素相同。
    """
    reason = "本机没有可用的 GPU 数据源"
    with_gpu = _island(_StubConfig(island_metrics=[CPU, "gpu"]), monkeypatch)
    without_gpu = _island(_StubConfig(island_metrics=[CPU]), monkeypatch)
    try:
        with_gpu._unavailable_reasons = {"gpu": reason}
        assert [seg.seg_id for seg in with_gpu._compose_info_segments()] == \
            [_PRIMARY, CPU]
        assert "gpu" not in with_gpu._row_plan().visible
        assert with_gpu._row_plan().folded == ()
        assert with_gpu._capsule_width() == without_gpu._capsule_width(), \
            "不可用段占了胶囊宽度（应完全不占位）"
        assert with_gpu.height() == without_gpu.height()
        assert with_gpu._rest_size() == without_gpu._rest_size()
    finally:
        with_gpu.close()
        without_gpu.close()


def test_unavailable_segment_gets_a_reason_row_in_the_card(tmp_path, monkeypatch):
    """不可用段的落点：只在卡片 B 区，一行写明「该指标不可用 + 原因」。"""
    reason = "本机没有可用的 GPU 数据源"
    island = _island(_StubConfig(island_metrics=[CPU, "gpu"]), monkeypatch)
    try:
        island._unavailable_reasons = {"gpu": reason}
        _expanded(island)
        rows = _rows(island)
        assert set(rows) == {CPU, "gpu"}
        assert rows["gpu"].state == isl.STATE_UNSUPPORTED
        assert rows["gpu"].note == f"该指标不可用：{reason}"
        assert rows["gpu"].detail == f"该指标不可用：{reason}"
        assert rows["gpu"].value == isl.UNKNOWN_TEXT
        assert len(island._card_row_widgets) == 2
    finally:
        island.close()


def test_unknown_segment_keeps_the_capsule_dash_placeholder(tmp_path, monkeypatch):
    """未知 id（配置残留）：胶囊里仍旧是 ``--`` 降级段——既有行为一个像素不动。"""
    island = _island(_StubConfig(island_metrics=[CPU, "temperature"]), monkeypatch)
    try:
        segments = {seg.seg_id: seg for seg in island._compose_info_segments()}
        assert segments["temperature"].state == isl.STATE_UNKNOWN
        assert segments["temperature"].text == "--"
        assert segments["temperature"].label == "temper"
        assert "temperature" in island._row_plan().visible
    finally:
        island.close()


def test_unknown_segment_gets_an_unregistered_reason_row(tmp_path, monkeypatch):
    """同一段的卡片面：说清它是"未注册的段 id"（与不可用段的写法同一条口径）。"""
    island = _island(_StubConfig(island_metrics=[CPU, "temperature"]), monkeypatch)
    try:
        _expanded(island)
        row = _rows(island)["temperature"]
        assert row.value == isl.UNKNOWN_TEXT
        assert row.note == "该指标不可用：未注册的段 id"
        assert row.detail == "该指标不可用：未注册的段 id"
    finally:
        island.close()


def test_expanded_height_follows_the_content_instead_of_a_fixed_value(tmp_path,
                                                                     monkeypatch):
    """"高度按内容实测"的最小可证伪断言：段数不同的两张卡片高度必须不同。"""
    empty = _island(_config(tmp_path), monkeypatch)
    filled = _island(_config(tmp_path), monkeypatch)
    try:
        filled.config.set("island_metrics", [CPU, MEM, NETWORK])
        empty._mode = filled._mode = "expanded"
        bare = empty._rest_size()
        rich = filled._rest_size()
        assert bare.height() != rich.height(), \
            f"卡片高度没跟内容走（0 段 {bare.height()} vs 3 段 {rich.height()}）"
        assert empty._card_metric_rows() == []
        assert len(filled._card_metric_rows()) == 3
        assert rich.height() - island_mod._CAPSULE_HEIGHT \
            == filled._card_height()
    finally:
        empty.close()
        filled.close()


# ------------------------------------------------------------ 状态文字 / 计时语义
def test_card_status_text_follows_the_five_state_semantics(tmp_path, monkeypatch):
    """概览区状态文字读 E1 的同一份五态解析（点与文字不可能各说一套）。"""
    cfg = _config(tmp_path)
    island = _island(cfg, monkeypatch)
    try:
        assert island._card_status_text() == "空闲"
        assert QColor(island._card_status_label.palette().color(
            island._card_status_label.foregroundRole())).isValid()

        island.set_dsh_state(DshState.WORKING.value)
        assert island._card_status_text() == "工作中"
        island.set_dsh_state(DshState.WAITING_APPROVAL.value)
        assert island._card_status_text() == "待确认"
        island.set_dsh_state(DshState.ERROR.value)
        assert island._card_status_text() == "出错"
        island.set_dsh_state(DshState.OFFLINE.value)
        assert island._card_status_text() == "DSH 离线"
        island.set_dsh_state(DshState.IDLE.value)
        island.set_pet_visible(False)
        assert island._card_status_text() == "桌宠已隐藏"

        # 文字色与状态点色同源（_status_dot_style 是唯一取色口）
        _shape, color = island._status_dot_style()
        island._sync_card_labels()
        assert f"color: {color.name()};" in island._card_status_label.styleSheet()
    finally:
        island.close()


def test_card_tick_timer_runs_only_while_the_card_is_open(tmp_path, monkeypatch):
    """每秒 tick（刷新"更新时间"）只活在卡片展开期：收起/隐藏都停表。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        assert island._card_tick_timer.isActive() is False
        island.expand_card()
        assert island._card_tick_timer.isActive() is True
        island.collapse_card()
        assert island._card_tick_timer.isActive() is False

        island.expand_card()
        assert island._card_tick_timer.isActive() is True
        island.hide()
        assert island._card_tick_timer.isActive() is False
        island.show()          # 隐藏期间卡片还开着：显示回来 tick 得接上
        assert island._card_tick_timer.isActive() is True
    finally:
        island.close()


def test_card_keeps_the_auto_collapse_and_click_routing_semantics(tmp_path, monkeypatch):
    """15s 自动收起 / 单击展开收起 / 按钮路由：E5 一个都没动。"""
    cfg = _config(tmp_path)
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        island.expand_card()
        assert island._card_collapse_timer.isActive() is True
        assert island._card_collapse_timer.interval() == island_mod._CARD_AUTO_COLLAPSE_MS
        hits = []
        island.toggle_pet_requested.connect(lambda: hits.append("toggle"))
        island._card_toggle_btn.click()
        assert hits == ["toggle"] and island._mode == "normal"
        assert island._card_collapse_timer.isActive() is False
    finally:
        island.close()


def test_card_tick_refreshes_the_updated_stamp(tmp_path, monkeypatch):
    """tick 一响，"更新时间"跟着走字（卡片开着时不显示冻结的旧时间）。"""
    cfg = _config(tmp_path)
    cfg.set("island_metrics", [CPU])
    island = _island(cfg, monkeypatch)
    try:
        island.show()
        _feed_sys(island, _sys_sample(cpu=24.0, age=2.0))
        _expanded(island)
        assert _rows(island)[CPU].updated == "2 秒前"
        _feed_sys(island, _sys_sample(cpu=24.0, age=9.0))
        island._on_card_tick()
        assert _rows(island)[CPU].updated == "9 秒前"
    finally:
        island.close()
