# -*- coding: utf-8 -*-
"""第三方段公开注册接口（P3 Slice F1）的契约测试。

第三方只交**数据与声明**（id / label / value 格式化函数 / 单位 / 阈值 / detail
文案），拿不到 QPainter。这里守护四类面：

1. **合法注册**：声明入库、注册序 = 用户优先级、按 id 取回、注销干净（阈值一并撤）。
2. **拒注面**：重复 id / 非法 id（空、超长、含空白、非 str、内置保留 id）/
   缺字段（没有 ``max_text`` 就没有盒宽契约）——一律明确报错，不静默吞。
3. **超宽降级（>120px）**：**不拒绝注册**，但进「+N 首批折叠候选」集合并记一条
   warning（按 ``(id, 字体指纹)`` 去重，不逐帧刷屏）。
4. **恶意段护栏**：格式化函数抛异常 → error 态兜底；返回巨型串 → 在**度量之前**
   按字符上限砍掉；读数永不进盒宽，所以任何一段失控都推不歪整岛。

外加一条文档纪律：``docs/ISLAND-SEGMENTS-EXTENSION.md`` 里的示例必须**真能跑**
（抽出来 exec 一遍）——文档示例一旦腐烂，契约就开始骗人。

度量一律用 ``tests/test_island_segments.py`` 的 ``FakeMetrics``（每字符 10px）：
不依赖平台字体，可离线跑。
"""
from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path

import pytest

from pet.island import segments as isl
from tests.test_island_segments import FakeMetrics

LOGGER_NAME = "pet.island.segments"
ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ISLAND-SEGMENTS-EXTENSION.md"


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例一份干净的进程级注册表（注册表是模块级全局，跨用例复用会互相污染）。"""
    isl.clear_segments()
    isl.clear_width_cache()
    yield
    isl.clear_segments()
    isl.clear_width_cache()


def _fields(**overrides) -> dict:
    base = {
        "id": "wifi",
        "format_value": lambda value: f"{float(value):.0f}",
        "max_text": "888",
    }
    base.update(overrides)
    return base


def _register(**overrides) -> isl.SegmentDeclaration:
    return isl.register_segment(**_fields(**overrides))


# ---------------------------------------------------------------- 合法注册
def test_register_returns_the_declaration_and_keeps_order():
    """注册序 = 用户优先级 = 尾部先被折叠（与内置段同一口径）。"""
    first = _register(id="wifi", label="WiFi")
    second = _register(id="temp", label="温度", max_text="88")
    assert isinstance(first, isl.SegmentDeclaration)
    assert [decl.id for decl in isl.segment_declarations()] == ["wifi", "temp"]
    assert isl.get_segment("wifi") is first
    assert isl.get_segment("temp") is second
    assert isl.get_segment("nope") is None


def test_declaration_carries_the_declared_surface():
    """声明的每个字段都能原样取回（第三方与宿主之间的契约面只有这些）。"""
    decl = _register(label="WiFi", unit="dBm", unit_full="信号强度",
                     source="本机无线网卡", detail="5GHz 频段 RSSI",
                     thresholds=(70.0, 85.0))
    assert (decl.label, decl.unit, decl.unit_full) == ("WiFi", "dBm", "信号强度")
    assert (decl.source, decl.detail) == ("本机无线网卡", "5GHz 频段 RSSI")
    assert decl.thresholds == (70.0, 85.0)
    assert decl.max_text == "888"


def test_unregister_removes_the_declaration_and_its_thresholds():
    _register(thresholds=(70.0, 85.0))
    assert isl.alert_thresholds("wifi") == (70.0, 85.0)
    assert isl.unregister_segment("wifi") is True
    assert isl.segment_declarations() == ()
    assert isl.alert_thresholds("wifi") is None, "注销后阈值不得留下第二真值"
    assert isl.unregister_segment("wifi") is False, "重复注销是 no-op，不抛"


def test_reregistering_the_same_id_with_a_new_sample_remeasures_the_width():
    """同名 id 注销后带新样本重新注册：盒宽必须**重测**。

    盒宽缓存按 ``(id, 字体指纹)`` 记——不清掉的话，重注册拿到的还是旧样本的答案，
    段就会用错误的盒宽排版（这正是缓存契约里"调用方自己失效"那条的机器化守卫）。
    """
    fonts = FakeMetrics(step=10)
    _register(id="wifi", max_text="888")
    assert isl.get_segment("wifi").width_px(fonts) == 30
    assert isl.unregister_segment("wifi") is True
    _register(id="wifi", max_text="888888")
    assert isl.get_segment("wifi").width_px(fonts) == 60, "盒宽按新声明重测"


# ---------------------------------------------------------------- 拒注面
def test_duplicate_id_is_rejected_and_the_first_one_wins():
    first = _register()
    with pytest.raises(ValueError):
        _register(max_text="999")
    assert isl.get_segment("wifi") is first
    assert len(isl.segment_declarations()) == 1


@pytest.mark.parametrize("bad_id", [
    "",              # 空
    "   ",           # 纯空白
    "wifi port",     # 含空白
    "wifi\nport",    # 换行（排版禁令）
    "w" * 33,        # 超长（> MAX_SEGMENT_ID_CHARS）
    None,            # 非 str
    7,               # 非 str
])
def test_invalid_ids_are_rejected(bad_id):
    with pytest.raises((ValueError, TypeError)):
        _register(id=bad_id)


@pytest.mark.parametrize("reserved", [isl.NETWORK, isl.CPU, isl.MEM, isl.GPU])
def test_builtin_ids_are_reserved(reserved):
    """内置段 id 是保留字：第三方注册同名 id 会与折叠/卡片索引撞车。"""
    with pytest.raises(ValueError):
        _register(id=reserved)


@pytest.mark.parametrize("missing", ["id", "format_value", "max_text"])
def test_missing_fields_are_rejected(missing):
    fields = _fields()
    fields.pop(missing)
    with pytest.raises(ValueError):
        isl.register_segment(**fields)


def test_non_callable_formatter_is_rejected():
    with pytest.raises(TypeError):
        _register(format_value="888")


def test_unknown_field_is_rejected():
    """拼错字段名不许静默丢弃（第三方的声明必须是全部输入面）。"""
    with pytest.raises(TypeError):
        isl.register_segment(**_fields(max_txt="888"))


def test_declaration_object_form_is_equivalent_to_field_form():
    """两种调用形式等价（库通常在 import 期就先建好声明对象）。"""
    decl = isl.SegmentDeclaration(**_fields(label="WiFi"))
    assert isl.register_segment(decl) is decl
    assert isl.get_segment("wifi") is decl


def test_non_declaration_positional_argument_is_rejected():
    with pytest.raises(TypeError):
        isl.register_segment("wifi")


def test_mixing_the_two_call_forms_is_rejected():
    """形式混用是调用方笔误，明确报错而不是猜"这次算哪种"。"""
    with pytest.raises(TypeError):
        isl.register_segment(isl.SegmentDeclaration(**_fields()), id="other")


@pytest.mark.parametrize("bad", [(1.0,), (1.0, 2.0, 3.0), (90.0, 70.0), ("a", "b")])
def test_invalid_thresholds_are_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        _register(thresholds=bad)


# ---------------------------------------------------------------- 超宽降级（120px）
def test_oversize_declaration_is_accepted_but_demoted_to_fold_first():
    """声明盒宽 >120px：**不拒绝注册**，但降为 +N 首批折叠候选。

    FakeMetrics 每字符 10px，故 16 字符样本 = 160px > 120px 上限。
    """
    fonts = FakeMetrics(step=10)
    wide = _register(id="wide", max_text="8" * 16)
    narrow = _register(id="narrow", max_text="888")
    assert wide.width_px(fonts) == 160 > isl.MAX_SEGMENT_WIDTH_PX
    assert narrow.width_px(fonts) == 30 <= isl.MAX_SEGMENT_WIDTH_PX
    assert wide.is_oversize(fonts) is True
    assert narrow.is_oversize(fonts) is False
    assert isl.oversize_segment_ids(fonts) == ("wide",), "超宽段是首批折叠候选"
    assert len(isl.segment_declarations()) == 2, "超宽段照旧留在注册表里"


def test_oversize_verdict_warns_once_per_font(caplog):
    """超宽要**可见**（warning 日志），但不去重就会逐帧刷屏。"""
    fonts = FakeMetrics(step=10)
    _register(id="wide", max_text="8" * 16)

    caplog.clear()
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    assert isl.oversize_segment_ids(fonts) == ("wide",)
    assert "wide" in caplog.text
    assert "120" in caplog.text, "日志要说清踩了哪条线"

    caplog.clear()
    assert isl.oversize_segment_ids(fonts) == ("wide",)
    assert caplog.text == "", "同一字体下重复讨判决不许重复告警"


def test_registering_an_oversize_segment_never_rejects_it():
    """「不禁止表达，让长段自付成本」= 注册接口不许拿宽度当准入门槛。"""
    _register(id="wide", max_text="8" * 40)
    assert isl.get_segment("wide") is not None


# ---------------------------------------------------------------- 恶意段护栏
def test_formatter_exception_degrades_to_error_state():
    """格式化函数抛异常：段退化成 error 态 ``--``，整岛照画。"""
    def boom(_value):
        raise RuntimeError("第三方格式化器炸了")

    decl = _register(label="WiFi", format_value=boom)
    snapshot = decl.snapshot(42)
    assert snapshot.state == isl.STATE_ERROR
    assert snapshot.text == "" and isl.render_compact(snapshot) == "WiFi--"
    assert "RuntimeError" in snapshot.detail["error_reason"], "原因要能落进卡片"
    assert "第三方格式化器炸了" in snapshot.detail["error_reason"]


def test_read_exception_degrades_to_error_state():
    """没声明数据源的段照样能注册，但取值时如实报 error，不伪造读数。"""
    def boom():
        raise OSError("网卡读不到了")

    decl = _register(label="WiFi", read=boom)
    assert decl.sample().state == isl.STATE_ERROR
    assert isl.render_compact(decl.sample()) == "WiFi--"
    assert "OSError" in decl.sample().detail["error_reason"]

    no_source = _register(id="nosource", label="无源")
    assert no_source.sample().state == isl.STATE_ERROR
    assert "数据源" in no_source.sample().detail["error_reason"]


def test_giant_value_is_truncated_before_measuring(caplog):
    """巨型串：在**度量之前**按字符上限砍掉（让它进 QFontMetrics 就是自己制造长任务）。"""
    fonts = FakeMetrics(step=10)
    decl = _register(format_value=lambda _value: "9" * 5000)

    caplog.clear()
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    snapshot = decl.snapshot(None)
    assert len(snapshot.text) <= isl.MAX_VALUE_CHARS
    assert caplog.text != "", "截断要留痕"

    display, box_w = isl.format_field_box(snapshot.text, snapshot.max_text, fonts)
    assert fonts.horizontalAdvance(display) <= box_w, "第二道护栏：字段盒右省略"
    assert display.endswith(isl.ELLIPSIS)


def test_value_newlines_are_flattened_not_raised():
    """第三方返回多行串：段契约禁换行（会撑破行高），在声明层就压平，不抛出去。"""
    decl = _register(format_value=lambda _value: "1.2M\n↑ 0.4M")
    snapshot = decl.snapshot(None)
    assert snapshot.state == isl.STATE_OK
    assert "\n" not in snapshot.text and "\r" not in snapshot.text


def test_error_detail_is_single_line_and_capped():
    """异常文案也是第三方可控输入：压成单行 + 限长，免得把卡片撑爆。"""
    def boom(_value):
        raise RuntimeError("第一行\n第二行" + "x" * 500)

    decl = _register(format_value=boom)
    reason = decl.snapshot(1).detail["error_reason"]
    assert "\n" not in reason
    assert len(reason) <= isl.MAX_DETAIL_CHARS


def test_formatter_returning_none_is_an_error_not_a_fake_reading():
    """返回 None = 漏写 return，不是"读数就是 None"——不伪造 ``--`` 之外的读数语义。"""
    decl = _register(format_value=lambda _value: None)
    assert decl.snapshot(1).state == isl.STATE_ERROR


def test_third_party_surface_is_data_only_and_qt_free():
    """红线：段契约层零 Qt，第三方拿不到 QPainter——声明的字段面只有数据。

    "拿不到 QPainter"的机器化口径 = ①模块不 import 任何 Qt；②声明里没有绘制
    入口（字段面就是全部输入面，没有 painter/canvas 之类的口子）。
    """
    source = inspect.getsource(isl)
    assert "PySide6" not in source and "PyQt" not in source
    fields = set(isl.SegmentDeclaration.__dataclass_fields__)
    assert {"id", "format_value", "max_text"} <= fields
    assert not {name for name in fields
                if "paint" in name.lower() or "canvas" in name.lower()}


# ---------------------------------------------------------------- 采样/阈值口径
def test_ok_snapshot_renders_label_plus_value():
    decl = _register(label="WiFi", unit="dBm")
    snapshot = decl.snapshot(72)
    assert snapshot.state == isl.STATE_OK
    assert isl.render_compact(snapshot) == "WiFi72"
    assert snapshot.max_text == "888", "盒宽样本恒为声明值，读数不进宽度"
    assert snapshot.detail["unit_full"] == ""


def test_declared_thresholds_feed_the_alert_pipeline():
    """声明的阈值进既有告警判定（含滞回），第三方段与 CPU/内存同一套语义。"""
    _register(thresholds=(70.0, 85.0))
    assert isl.alert_thresholds("wifi") == (70.0, 85.0)
    assert isl.alert_severity("wifi", 71.0) == isl.ALERT_WARN
    assert isl.alert_severity("wifi", 90.0) == isl.ALERT_CRIT
    # 滞回带 = crit − 3pt：83 仍在 crit 里、81 才降到 warn（与 CPU/内存同一条链）
    assert isl.alert_severity("wifi", 83.0, isl.ALERT_CRIT) == isl.ALERT_CRIT
    assert isl.alert_severity("wifi", 81.0, isl.ALERT_CRIT) == isl.ALERT_WARN
    assert isl.alert_severity("wifi", 10.0, isl.ALERT_CRIT) == ""
    assert isl.alert_thresholds("temp") is None, "没声明的段永远不告警"


# ---------------------------------------------------------------- 文档纪律
def test_document_example_is_runnable():
    """示例必须真能跑：抽出来 exec 一遍（文档腐烂 = 契约开始骗人）。

    文档里**恰好一个** ```python 块 = 那个完整示例（其余片段用 ```text，
    不参与抽取）。示例自带注销，所以跑完注册表应当是空的——这既验了示例可跑，
    也验了示例演示的"卸载时注销"那一步真的存在（删掉最后一步本用例就红）。
    """
    assert DOC.is_file(), f"缺少第三方段契约文档：{DOC}"
    blocks = re.findall(r"```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, f"文档里应当恰好一个可跑示例（```python 块），实际 {len(blocks)}"
    namespace: dict = {"__name__": "__doc_example__"}
    exec(compile(blocks[0], str(DOC), "exec"), namespace)  # noqa: S102 - 仓库内文档
    assert isl.segment_declarations() == (), "示例没自带清理（注销那一步）"
    assert isl.alert_thresholds("cpu_temp") is None


def test_document_is_registered_in_the_index():
    """新文档入场规则第 1 条：任何新文档必须在 docs/INDEX.md 登记一行。"""
    index = (ROOT / "docs" / "INDEX.md").read_text(encoding="utf-8")
    assert "ISLAND-SEGMENTS-EXTENSION.md" in index
