# -*- coding: utf-8 -*-
"""灵动岛段契约（``pet/island_segments.py``）的单测（规格 §Slice A）。

本文件守护三条硬约束：

1. **契约层不许把坏数据放进岛**：``label``/``text``/``max_text`` 一律单行
   （含 ``\\n``/``\\r`` 直接拒注），label 超 6 字符截断并告警。
2. **宽度自测量**：声明盒宽 = ``horizontalAdvance(label) + 4(有 label 时)
   + horizontalAdvance(max_text)``，按 ``(id, font_key)`` 缓存；字体/DPI 一变
   必须重测，不许拿旧盒宽画新字。运行期 ``text`` 超过盒宽 → 右省略 + warning
   （任何一段失控都不得推歪整岛）。
3. **四态兜底呈现**：ok → ``label+text``；sampling/unknown/error → 同宽 ``--``；
   ``unsupported`` 返回 None（不占位，调用方跳过）。

模块本身零 Qt；只有"真实字体度量"的用例需要 QApplication（QFontMetrics 要求
QGuiApplication 实例），口径同 ``tests/test_bubble_text_scale.py`` 的 ``_qapp()``。其余用例全用替身
度量，不依赖平台字体，可离线跑。
"""
from __future__ import annotations

import logging

import pytest
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from pet.island import segments as isl

#: 本模块 logger 名（告警断言用，跟着模块路径走）
LOGGER_NAME = "pet.island.segments"


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clear_width_cache():
    """每个用例一份干净缓存：宽度缓存是进程级全局，跨用例复用会掩盖重测 bug。"""
    isl.clear_width_cache()
    yield
    isl.clear_width_cache()


class FakeMetrics:
    """字体度量替身：每字符固定 ``step`` px（只验逻辑，不碰真实字体）。"""

    def __init__(self, step: int = 10, *, height: int = 17, line_spacing: int = 20):
        self.step = step
        self._height = height
        self._line_spacing = line_spacing
        self.calls: list[str] = []

    def horizontalAdvance(self, text) -> int:
        self.calls.append(str(text))
        return len(str(text)) * self.step

    def height(self) -> int:
        return self._height

    def lineSpacing(self) -> int:
        return self._line_spacing

    def ascent(self) -> int:
        return self._height - 4

    def descent(self) -> int:
        return 4

    def leading(self) -> int:
        return 0

    def averageCharWidth(self) -> int:
        return self.step

    def count(self, text: str) -> int:
        return self.calls.count(text)


def _seg(seg_id: str = isl.CPU, **kwargs) -> isl.SegmentSnapshot:
    fields = {"label": "CPU", "text": "24%", "max_text": "888%"}
    fields.update(kwargs)
    return isl.SegmentSnapshot(id=seg_id, **fields)


# ---------------------------------------------------------------- 段快照契约
def test_builtin_segment_ids_are_stable():
    """内置段 id 是收缩/折叠/卡片行的稳定标识，改了就是破坏契约。"""
    assert (isl.NETWORK, isl.CPU, isl.MEM) == ("network", "cpu", "mem")


def test_state_vocabulary_is_the_four_state_contract():
    assert isl.STATES == frozenset({
        isl.STATE_OK, isl.STATE_SAMPLING, isl.STATE_UNKNOWN,
        isl.STATE_ERROR, isl.STATE_UNSUPPORTED,
    })
    assert isl.STATE_OK == "ok"
    assert isl.STATE_SAMPLING == "sampling"
    assert isl.STATE_UNKNOWN == "unknown"
    assert isl.STATE_ERROR == "error"
    assert isl.STATE_UNSUPPORTED == "unsupported"


def test_snapshot_defaults_are_ok_with_empty_detail():
    seg = isl.SegmentSnapshot(id=isl.MEM, text="61%", max_text="888%")
    assert seg.state == isl.STATE_OK
    assert seg.label == ""
    assert seg.detail == {}


def test_snapshot_detail_defaults_are_not_shared_between_instances():
    first = isl.SegmentSnapshot(id="a", text="1", max_text="1")
    second = isl.SegmentSnapshot(id="b", text="2", max_text="2")
    first.detail["unit_full"] = "摄氏度"
    assert second.detail == {}, "detail 默认值被两个实例共享了（default_factory 失效）"


def test_snapshot_detail_passthrough():
    detail = {"unit_full": "百分比", "source": "psutil", "updated_at": 1234.5, "error_reason": ""}
    seg = isl.SegmentSnapshot(id=isl.MEM, text="61%", max_text="888%", detail=detail)
    assert seg.detail is detail


def test_snapshot_is_frozen():
    seg = _seg()
    with pytest.raises(Exception):
        seg.text = "99%"


@pytest.mark.parametrize("field", ["text", "max_text", "label"])
def test_multiline_fields_are_rejected(field):
    """单行是排版禁令的机器可检部分：多行段会把整条胶囊撑成两行。"""
    with pytest.raises(ValueError):
        _seg(**{field: "a\nb"})
    with pytest.raises(ValueError):
        _seg(**{field: "a\rb"})


def test_empty_id_is_rejected():
    with pytest.raises(ValueError):
        isl.SegmentSnapshot(id="", text="1", max_text="1")


def test_overlong_label_is_truncated_with_warning(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    overlong = "CPU温度上限"  # 7 字符 > 6
    seg = _seg(label=overlong)
    assert seg.label == overlong[:isl.LABEL_MAX_CHARS]
    assert len(seg.label) == isl.LABEL_MAX_CHARS
    assert "label" in caplog.text


def test_label_at_limit_is_untouched_and_silent(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    seg = _seg(label="A" * isl.LABEL_MAX_CHARS)
    assert seg.label == "A" * isl.LABEL_MAX_CHARS
    assert caplog.text == ""


def test_truncated_label_is_idempotent(caplog):
    """重建快照（每帧刷新值）不该反复刷告警——截断后长度已合法。"""
    first = _seg(label="CPU温度上限")

    caplog.clear()
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    again = isl.SegmentSnapshot(id=isl.CPU, label=first.label, text="1", max_text="8")
    assert again.label == first.label
    assert caplog.text == ""


# ---------------------------------------------------------------- 注册表
def test_registry_preserves_registration_order():
    """列表序 = 用户优先级 = 尾部先折叠（S2/S3 收缩从尾部取段）。"""
    registry = isl.SegmentRegistry()
    for seg_id in (isl.NETWORK, isl.CPU, isl.MEM):
        registry.register(_seg(seg_id))
    assert [seg.id for seg in registry.snapshots()] == [isl.NETWORK, isl.CPU, isl.MEM]


def test_registry_rejects_duplicate_id_and_keeps_the_first():
    registry = isl.SegmentRegistry()
    first = _seg(text="24%")
    registry.register(first)
    with pytest.raises(ValueError):
        registry.register(_seg(text="99%"))
    assert registry.get(isl.CPU) is first
    assert len(registry.snapshots()) == 1


def test_registry_accepts_initial_segments():
    registry = isl.SegmentRegistry([_seg(isl.CPU), _seg(isl.MEM)])
    assert len(registry.snapshots()) == 2


def test_registry_rejects_non_snapshot():
    registry = isl.SegmentRegistry()
    with pytest.raises(TypeError):
        registry.register({"id": "cpu"})


def test_registry_unknown_id_returns_none_without_raising():
    registry = isl.SegmentRegistry([_seg(isl.CPU)])
    assert registry.get("gpu") is None
    assert registry.get(None) is None


def test_registry_snapshots_returns_a_copy():
    registry = isl.SegmentRegistry([_seg(isl.CPU)])
    snapshots = registry.snapshots()
    snapshots.append(_seg(isl.MEM))
    assert len(registry.snapshots()) == 1


# ---------------------------------------------------------------- 宽度自测量
def test_measured_width_follows_the_declared_formula():
    fm = FakeMetrics(step=10)
    seg = _seg()  # label "CPU" 3 字符 + max_text "888%" 4 字符
    assert isl.measure_segment_width(seg, fm) == 30 + isl.LABEL_GAP_PX + 40


def test_measured_width_has_no_label_gap_without_label():
    fm = FakeMetrics(step=10)
    seg = _seg(label="")
    assert isl.measure_segment_width(seg, fm) == 40


def test_measured_width_is_cached_per_segment_id():
    fm = FakeMetrics(step=10)
    seg = _seg()
    first = isl.measure_segment_width(seg, fm)
    second = isl.measure_segment_width(seg, fm)
    assert (first, second) == (74, 74)
    assert fm.count("CPU") == 1, "缓存未命中：label 被重复测量"
    assert fm.count("888%") == 1, "缓存未命中：max_text 被重复测量"


def test_width_cache_is_invalidated_when_font_metrics_change():
    """DPI/字号一变，旧盒宽必须失效——否则新字按旧盒宽排，段就挤在一起。"""
    fm = FakeMetrics(step=10)
    seg = _seg()
    assert isl.measure_segment_width(seg, fm) == 74
    fm.step = 20  # 等价于字号/DPI 变化：度量指纹随之变化
    assert isl.measure_segment_width(seg, fm) == 60 + isl.LABEL_GAP_PX + 80


def test_width_cache_scoped_by_explicit_font_key():
    fm = FakeMetrics(step=10)
    seg = _seg()
    assert isl.measure_segment_width(seg, fm, font_key=("ui", 1)) == 74
    fm.step = 20
    assert isl.measure_segment_width(seg, fm, font_key=("ui", 1)) == 74  # 同键命中旧值
    assert isl.measure_segment_width(seg, fm, font_key=("ui", 2)) == 60 + isl.LABEL_GAP_PX + 80


def test_clear_width_cache_forces_recompute():
    fm = FakeMetrics(step=10)
    seg = _seg()
    isl.measure_segment_width(seg, fm)
    assert len(isl._WIDTH_CACHE) == 1
    isl.clear_width_cache()
    assert isl._WIDTH_CACHE == {}


def test_real_font_metrics_width_matches_glyph_sum():
    _qapp()
    fm = QFontMetrics(QFont("", 13))
    seg = _seg()
    expected = fm.horizontalAdvance("CPU") + isl.LABEL_GAP_PX + fm.horizontalAdvance("888%")
    assert isl.measure_segment_width(seg, fm) == expected
    assert expected > isl.LABEL_GAP_PX


def test_font_key_separates_two_real_font_sizes():
    _qapp()
    assert isl.font_metrics_key(QFontMetrics(QFont("", 13))) != \
        isl.font_metrics_key(QFontMetrics(QFont("", 24)))


# ---------------------------------------------------------------- 字段盒
def test_field_box_returns_text_untouched_when_it_fits(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    display, box_width = isl.format_field_box("24%", "888%", FakeMetrics(step=10))
    assert (display, box_width) == ("24%", 40)
    assert caplog.text == ""


def test_field_box_elides_overwide_text_to_the_right_and_warns(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    fm = FakeMetrics(step=10)
    display, box_width = isl.format_field_box("12345%", "888%", fm)
    assert box_width == 40
    assert display == "123…"  # 3 字符 + 省略号 = 40px，恰好铺满盒宽
    assert fm.horizontalAdvance(display) <= box_width
    assert "字段盒" in caplog.text


def test_field_box_never_exceeds_box_width_across_extremes():
    fm = FakeMetrics(step=7)
    for text in ("", "0", "888%", "x" * 500, "温度" * 40, "🙂" * 60, "1.2M↑3.4M↓"):
        display, box_width = isl.format_field_box(text, "888%", fm)
        assert fm.horizontalAdvance(display) <= box_width, (text, display, box_width)


def test_field_box_without_worst_case_sample_degrades_to_empty_display(caplog):
    """max_text 为空 = 声明盒宽 0：宁可不显示，也不越界推歪整岛。"""
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    display, box_width = isl.format_field_box("1%", "", FakeMetrics(step=10))
    assert (display, box_width) == ("", 0)
    assert "字段盒" in caplog.text


def test_field_box_treats_none_text_as_empty():
    display, box_width = isl.format_field_box(None, "888%", FakeMetrics(step=10))
    assert (display, box_width) == ("", 40)


def test_field_box_elides_with_real_font_metrics():
    _qapp()
    fm = QFontMetrics(QFont("", 13))
    display, box_width = isl.format_field_box("12345678901234567890%", "888%", fm)
    assert fm.horizontalAdvance(display) <= box_width
    assert display.endswith(isl.ELLIPSIS)


# ---------------------------------------------------------------- 四态兜底
def test_render_ok_concatenates_label_and_text():
    """compact 串是"标签 + 盒内容"的裸拼接：4px 间距是绘制层的事（见宽度公式），
    串里插空格反而会比声明盒宽多出 1 列。"""
    assert isl.render_compact(_seg()) == "CPU24%"


def test_render_ok_without_label_is_text_only():
    assert isl.render_compact(_seg(label="", text="1.2M")) == "1.2M"


@pytest.mark.parametrize("state", ["sampling", "unknown", "error"])
def test_render_fallback_states_share_the_same_dash_text(state):
    """sampling/unknown/error 同文案：``--`` 与 max_text 同盒宽语义，布局不跳。
    「更淡」「红字」由绘制层与卡片层管，纯逻辑层同文。"""
    assert isl.render_compact(_seg(state=state)) == f"CPU{isl.UNKNOWN_TEXT}"
    assert isl.render_compact(_seg(label="", state=state)) == isl.UNKNOWN_TEXT


def test_render_unsupported_returns_none():
    """unsupported 不占位：调用方跳过该段（卡片里写明原因）。"""
    assert isl.render_compact(_seg(state=isl.STATE_UNSUPPORTED)) is None


def test_render_unrecognized_state_falls_back_to_dash():
    """未识别状态按 unknown 兜底：不抛，也不把原始 text 当读数显示。"""
    assert isl.render_compact(_seg(state="wat")) == f"CPU{isl.UNKNOWN_TEXT}"
