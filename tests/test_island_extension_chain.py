# -*- coding: utf-8 -*-
"""第三方灵动岛组件的**完整宿主链**（声明清单 → 配置准入 → 采样 → 胶囊/卡片 → 折叠 → 卸载）。

本文件管"链有没有接通"，不管契约层与采样器的细节（那些在
``tests/test_island_extension_host.py`` / ``tests/test_island_segments.py``）。它回答：

1. **准入**：组件声明了什么，``island_metrics`` 就收什么；**未声明的 id 依旧被清掉**
   （"谁注册了谁自动合法"被明确禁止——配置保存不许依赖进程内的注册顺序）；
2. **呈现**：胶囊上是一段带单位盒的定宽段（读数永不进宽度键），卡片有单位全称 /
   来源 / 更新时间；
3. **告警**：声明阈值进既有判定链（两级 + 3pt 滞回），chip 告警点跟着被折段走；
4. **超宽折叠**：声明盒宽 >120px 的段**先于尾部段**被折——**哪怕它在列表中间**，
   折叠计数、chip 与卡片标注都必须按实际折掉的那几段算；
5. **生命周期**：折叠 / 停靠细条 / 隐藏 / 关闭都退订；组件暂缺保偏好不执行；卸载撤
   阈值与订阅；恢复到同一个清单。

时序纪律：替身采样器同步直调（不赌定时器节奏）；真实采样器只用在"清单 → 采样 →
胶囊"这条端到端用例上，用事件同步 + 宽预算轮询。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore
from PySide6.QtCore import QEventLoop, QRect, QTime, QTimer
from PySide6.QtWidgets import QApplication, QLabel

import pet.dynamic_island as island_mod
import pet.modern_settings_dialog as settings_mod
from pet import island_extension_host as host
from pet import island_layout, island_segments as isl
from pet.config import Config
from pet.dynamic_island import DynamicIsland
from pet.island_segments import CPU, MEM
from tests.test_island_extension_host import (
    MANIFEST, _wait_for, _write_component,
)
from tests.test_island_extension_recovery import _GatedProvider
from tests.test_island_segment_render import _StubConfig, _StubMonitor

app = QApplication.instance() or QApplication([])

EXT = "cpu_temp"
#: 声明盒宽明显超过 120px 的组件（FakeMetrics 之外的真实字体下也稳稳超线）。
WIDE_EXT = "cpu_power"
WIDE_MANIFEST = dict(MANIFEST, id=WIDE_EXT, label="功耗", max_text="8" * 24,
                     unit="W", unit_full="瓦特", source="测试电源", detail="整机功耗",
                     thresholds=[400, 600])

#: "读完才返回"的组件：第一次 ``read_celsius`` 等组件目录里的 release 文件（有上限，
#: 免得用例失败时把一个线程永远挂住）。用来证明"表在拉快照"与"这次 read 读完才回来"。
PROVIDER_WAITS_FOR_RELEASE = '''
import time
from pathlib import Path


def read_celsius():
    flag = Path(__file__).with_name("release")
    deadline = time.monotonic() + 30.0
    while not flag.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    return 74.0


def format_celsius(value):
    return f"{round(float(value))}"
'''

#: 读数由组件目录里的 ``value.txt`` 定（每次 read 都重读文件 + 记一行 read.log）：
#: 测试写文件 = 改读数，于是"读数变了"是 provider **真的被第二次调用**得来的，
#: 不是往宿主表里灌的假样本；read.log 的增行数就是"provider 被调了几次"。
PROVIDER_READS_A_FILE = '''
from pathlib import Path


def read_celsius():
    value_file = Path(__file__).with_name("value.txt")
    text = value_file.read_text(encoding="utf-8")
    with value_file.with_name("read.log").open("a", encoding="utf-8") as handle:
        handle.write(text + "\\n")
    return float(text)


def format_celsius(value):
    return f"{round(float(value))}"
'''

#: 慢读组件：每次取值阻塞 0.25 秒（熔断那条路径的真实素材）。
PROVIDER_SLOW = """
import time
from pathlib import Path


def read_celsius():
    time.sleep(0.25)
    return 74.0


def format_celsius(value):
    return f"{round(float(value))}"
"""


class _StubSampler:
    """采样器替身：不起线程、不 import 组件；测试自己"发一拍"。"""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.subscribed: set[str] = set()
        self.unsubscribed: list[str] = []
        self.starts = 0
        self.stops = 0
        self.started = False
        self.closed = False
        self.epoch = 0
        self.samples: dict = {}

    # ---- 订阅面
    def subscribe(self, key: str) -> int:
        self.subscribed.add(str(key))
        return len(self.subscribed)

    def unsubscribe(self, key: str) -> int:
        self.subscribed.discard(str(key))
        self.unsubscribed.append(str(key))
        self.samples.pop(str(key), None)
        return len(self.subscribed)

    @property
    def subscriber_count(self) -> int:
        return len(self.subscribed)

    # ---- 线程面
    def start(self) -> bool:
        if self.started:
            return False
        self.starts += 1
        self.started = True
        self.epoch += 1
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

    # ---- 读数面
    def snapshot(self) -> dict:
        return dict(self.samples)

    def blocked_reason(self, _segment_id: str) -> str:
        return ""

    def publish(self, segment_id: str, *, value=74.0, text="74", state=isl.STATE_OK,
                reason="", sampled_at=None, epoch=None) -> None:
        self.samples[segment_id] = host.ExtensionSample(
            segment_id, state=state, text=text, raw_value=value, error_reason=reason,
            sampled_at=time.monotonic() if sampled_at is None else sampled_at,
            epoch=self.epoch if epoch is None else epoch)


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    root = tmp_path / "island-extensions"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(host.ENV_DIRS, str(root))
    host.reset_cache()
    isl.clear_segments()
    yield root
    for segment_id in [key for key in isl.ALERT_THRESHOLDS
                       if key not in isl.BUILTIN_SEGMENT_IDS]:
        isl.drop_thresholds(segment_id)
        isl.ALERT_THRESHOLDS.pop(segment_id, None)
    isl.clear_segments()
    host.reset_cache()


class FakeScreen:
    def __init__(self, width: int, height: int = 768):
        self._rect = QRect(0, 0, width, height)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


def _config(tmp_path, *, metrics=(), **overrides) -> Config:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True, "show_info": True,
        "info_mode": "time", "custom_text": "", "show_status": True, "style": "dark",
        "x": 40, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return cfg


def _patch_sampling(monkeypatch, made: list[_StubSampler], *, screen_width: int = 1400):
    """三条采样路径都换替身（本文件不验真实线程节奏），并固定屏幕。"""
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_extension_sampler",
                        lambda self, interval: made.append(_StubSampler(interval)) or made[-1])
    screen = FakeScreen(screen_width)
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen", lambda self: screen)
    return screen


def _island(tmp_path, monkeypatch, *, metrics=(), screen_width=1400, **overrides):
    made: list[_StubSampler] = []
    screen = _patch_sampling(monkeypatch, made, screen_width=screen_width)
    island = DynamicIsland(_config(tmp_path, metrics=metrics, **overrides))
    return island, made, screen


def _island_with_config(config, monkeypatch, *, screen_width=1400):
    """用**给定配置对象**建岛（替身配置能塞未归一化的 id，见 _StubConfig）。"""
    made: list[_StubSampler] = []
    screen = _patch_sampling(monkeypatch, made, screen_width=screen_width)
    return DynamicIsland(config), made, screen


def _pump_until(predicate, *, timeout: float = 15.0) -> bool:
    """跑**真实 Qt 事件循环**直到谓词成立（QTimer 的 timeout 只在事件循环里派发）。

    用它就能断言"宿主自己的表在拉快照"：不手工调 ``_tick_extension``、只 ``processEvents``。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


class _PaintingIsland(DynamicIsland):
    """真岛 + 真 paintEvent 计数（不 mock 产品对象、不拦 update 调度）。

    每帧顺手抄下 ``_content_drawn``——``paintEvent`` 里真画出去的那份内容层位图。
    有它，"自动触发的那一帧画的是新读数"对的就是**那一帧自己的像素**；用 ``grab()``
    对像素是另一回事：grab 自己就会触发绘制，分不清这一笔是谁画的。
    """

    # 类属性兜底：``super().__init__()`` 期间就可能走 ``_update_size``（构造里的
    # 定位/尺寸同步），那时实例属性还没落
    paints = 0
    frames: list = []
    geometry_updates = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paints = 0
        self.frames = []
        self.geometry_updates = 0

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        self.paints += 1
        super().paintEvent(event)
        drawn = self._content_drawn
        self.frames.append(drawn.copy() if drawn is not None else None)

    def _update_size(self) -> None:
        # 量"这一笔重绘有没有顺手重算窗口几何"（读数只在盒内伸缩，白名单键不含采样值）
        self.geometry_updates += 1
        super()._update_size()

    def reset_frames(self) -> None:
        self.paints = 0
        self.frames = []
        self.geometry_updates = 0


def _spin(ms: int) -> None:
    """真事件循环跑 ms 毫秒（真定时器与真 paintEvent 自然发生），不 sleep 赌时序。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _settle(island: _PaintingIsland) -> None:
    """进稳态：动画收尾、30s 信息心跳停掉（本文件只量第三方段这一条通道）。"""
    island._finish_animations()
    island._info_timer.stop()
    _spin(250)          # 让瞬态（首帧、淡化）走完，也把待派发的重绘清空


def _wait_paint(island: _PaintingIsland, *, timeout: float = 5.0) -> int:
    """等这窗内的自动重绘落地（真事件循环），返回重绘次数——判决由调用方下。

    ``update()`` 只是排一个延迟绘制事件：``_pump_until`` 常在"读数到宿主"那一刻就
    返回，绘制事件要下一轮事件循环才派发，所以"重绘了几次"必须自己等一等再读。
    """
    _pump_until(lambda: island.paints >= 1, timeout=timeout)
    return island.paints


def _real_ext_island(config, monkeypatch, *, interval: float = 0.25) -> _PaintingIsland:
    """真第三方采样器（真线程、真 provider）+ 另两条采样路径换替身。"""
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, i: _StubMonitor(i))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, i: _StubMonitor(i))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_extension_sampler",
                        lambda self, i: host.ExtensionSampler(interval=interval))
    return _PaintingIsland(config)


class _FakeClock:
    """假 ``QTime``：只有 ``currentTime()`` 一条（产品读表只用这一条，见
    ``DynamicIsland._primary_segment``）。

    返回真 ``QTime``，所以 ``toString("HH:mm")`` 走的还是产品那条格式化路径；
    ``tick()`` 把"跳分"变成**确定性事件**——本文件量几何的用例不该被真实时钟
    跨不跨分界决定红绿。
    """

    def __init__(self, hour: int, minute: int) -> None:
        self._now = QTime(hour, minute)

    def currentTime(self) -> QTime:      # noqa: N802 (Qt 命名)
        return self._now

    def tick(self) -> None:
        """推一分钟：跳分因此是确定性事件，不赌真实时间过不过分界。"""
        self._now = self._now.addSecs(60)


def _freeze_clock(monkeypatch, hour: int, minute: int) -> _FakeClock:
    """把产品的读表点钉在 ``hour:minute``（monkeypatch 收尾还原）。

    产品在 ``_primary_segment`` 里**函数内** ``from PySide6.QtCore import QTime``，
    所以补 ``PySide6.QtCore.QTime`` 这一个模块属性就够：只改那一次读表，别的
    ``QTime`` 用法拿的是各自模块 import 到的真类。
    """
    clock = _FakeClock(hour, minute)
    monkeypatch.setattr(QtCore, "QTime", clock)
    return clock


def _ext_value_box(island: DynamicIsland, seg_id: str = EXT) -> tuple:
    """第三方段在内容行里的**值盒落位**（与绘制同一条路径：真字体、真度量）。

    量的是"读数只在盒内伸缩"这句话里的那个盒子：标签 / 数值盒 / 单位盒的缘与宽。
    """
    plan = island._row_plan()
    row = island_layout.apply_shrink(plan, island._compose_info_segments(),
                                     primary_fm=island._metrics_for("primary"))
    (placement,) = island_layout.place_info_segments(
        [seg for seg in row if seg.seg_id == seg_id], start_x=plan.segments_x,
        primary_fm=island._metrics_for("primary"),
        value_fm=island._metrics_for("value"),
        label_fm=island._metrics_for("label"))
    return (placement.label_x, placement.label_w, placement.box_x, placement.box_w,
            placement.unit_x, placement.unit_w)


def _row_geometry(island: DynamicIsland) -> tuple:
    """一行内容的几何事实清单：自然宽（行计划）+ 窗口 size/pos + 第三方值盒落位。"""
    return (island._row_plan().width, island.size().width(), island.size().height(),
            island.pos().x(), island.pos().y(), _ext_value_box(island))


def _card_value_label(island: DynamicIsland, seg_id: str) -> QLabel:
    """卡片 B 区里那一段的**值列控件**（真控件、真文案，不是现算的行数据）。"""
    for index, row in enumerate(island._card_metric_rows()):
        if row.seg_id == seg_id:
            return island._card_row_labels[index][1]
    raise AssertionError(f"卡片里没有 {seg_id!r}")


def _live_ext_workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name == host.THREAD_NAME and thread.is_alive()]


def _seg(island: DynamicIsland, seg_id: str):
    for segment in island._compose_info_segments():
        if segment.seg_id == seg_id:
            return segment
    raise AssertionError(
        f"段列表里没有 {seg_id!r}：{[s.seg_id for s in island._compose_info_segments()]}")


def _has_segment(island: DynamicIsland, seg_id: str) -> bool:
    return seg_id in [segment.seg_id for segment in island._compose_info_segments()]


def _row(island: DynamicIsland, seg_id: str):
    for row in island._card_metric_rows():
        if row.seg_id == seg_id:
            return row
    raise AssertionError(f"卡片里没有 {seg_id!r}")


def _card_note(island: DynamicIsland, seg_id: str = EXT) -> str:
    rows = [row for row in island._card_metric_rows() if row.seg_id == seg_id]
    return rows[0].note if rows else ""


def _expanded(island: DynamicIsland) -> None:
    island.show()
    island.expand_card()
    island._finish_animations()
    app.processEvents()
    island._card_box.layout().activate()
    app.processEvents()


def _narrow_to(island, screen, predicate, *, lo=150, hi=1500, step=4):
    """在真实字体下从宽往窄搜"满足 predicate"的屏宽（口径同收缩流水线族）。"""
    for width in range(hi, lo, -step):
        screen._rect = QRect(0, 0, width, 768)
        island.refresh_from_config()
        plan = island._row_plan()
        if predicate(plan):
            return width, plan
    raise AssertionError("在 150–1500px 的屏宽里找不到满足条件的收缩级")


# ---------------------------------------------------------------- 配置准入
def test_island_metrics_admits_declared_components_only(tmp_path, _clean_state):
    """准入看**声明清单**：声明过的收，没声明的照旧清掉。"""
    directory = _write_component(_clean_state)
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [CPU, EXT, "not_declared", EXT])
    assert cfg.get("island_metrics") == [CPU, EXT]
    assert cfg.save() is True
    assert Config(base=tmp_path).get("island_metrics") == [CPU, EXT]

    (directory / host.MANIFEST_NAME).unlink()
    host.reset_cache()
    cfg2 = Config(base=tmp_path)
    assert cfg2.get("island_metrics") == [CPU], "卸载（清单消失）后 id 不再合法"


def test_declared_component_survives_a_restart(tmp_path, _clean_state):
    _write_component(_clean_state)
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [EXT])
    assert cfg.save() is True
    on_disk = json.loads((cfg.dir / "config.json").read_text(encoding="utf-8"))
    assert on_disk["island_metrics"] == [EXT]
    assert Config(base=tmp_path).get("island_metrics") == [EXT]


def test_provider_missing_keeps_the_preference_in_config(tmp_path, _clean_state):
    """组件暂缺：id 仍合法（偏好留着），但静态路径不去找 provider、不执行它。"""
    _write_component(_clean_state, provider=None)
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [EXT])
    assert cfg.get("island_metrics") == [EXT] and cfg.save() is True
    assert Config(base=tmp_path).get("island_metrics") == [EXT]


# ---------------------------------------------------------------- 胶囊 / 卡片
def test_capsule_shows_the_component_reading_with_its_declared_box(tmp_path, monkeypatch,
                                                                  _clean_state):
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        assert sampler.subscribed == {EXT}, "配置里的组件段必须被订阅"
        assert _seg(island, EXT).state == isl.STATE_SAMPLING, "第一拍之前是 --（不是 0）"

        sampler.publish(EXT, value=74.0, text="74")
        assert island._tick_extension() is True
        segment = _seg(island, EXT)
        assert (segment.label, segment.text, segment.unit) == ("温度", "74", "°C")
        assert segment.box_text == "88", "盒宽样本 = 组件声明的 max_text"
        assert segment.state == isl.STATE_OK
        assert segment.severity == isl.ALERT_WARN, "74 过了声明的 warn 线 70"

        (placement,) = island_layout.place_info_segments(
            [segment], start_x=0.0, primary_fm=island._metrics_for("primary"),
            value_fm=island._metrics_for("value"), label_fm=island._metrics_for("label"))
        assert placement.label_x == 0.0 and placement.label_w > 0
        assert placement.unit_x is not None and placement.unit_text == "°C"
        assert placement.text == "74"
    finally:
        island.close()


def test_zero_is_a_reading_and_missing_is_dashes(tmp_path, monkeypatch, _clean_state):
    """0 是真读数（不许画成 --）；没采到才是 --。"""
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        sampler.publish(EXT, value=0.0, text="0")
        island._tick_extension()
        segment = _seg(island, EXT)
        assert segment.state == isl.STATE_OK and segment.text == "0"

        sampler.publish(EXT, value=None, text="", state=isl.STATE_ERROR, reason="读不到")
        island._tick_extension()
        segment = _seg(island, EXT)
        assert segment.text == "--" and segment.state == isl.STATE_ERROR
    finally:
        island.close()


def test_sampling_values_never_enter_the_width_key(tmp_path, monkeypatch, _clean_state):
    """读数的 9 → 100 不改盒宽、不改岛宽、不改白名单键（结构保证，不是纪律）。"""
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        sampler.publish(EXT, value=9.0, text="9")
        island._tick_extension()
        key = island._width_key()
        width = island._capsule_width()
        size = island.size()
        for value in (0.0, 9.0, 42.0, 99.0, 100.0):
            sampler.publish(EXT, value=value, text=str(int(value)))
            island._tick_extension()
            assert island._width_key() == key, "采样值进了宽度白名单键"
            assert island._capsule_width() == width
            assert island.size() == size
    finally:
        island.close()


def test_card_row_carries_unit_source_and_update_time(tmp_path, monkeypatch, _clean_state):
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        sampler.publish(EXT, value=74.0, text="74")
        island._tick_extension()
        _expanded(island)
        row = _row(island, EXT)
        assert row.name == "温度"
        assert row.value == "74°C", "值列 = 胶囊同一个合并串"
        assert row.unit_full == "摄氏度"
        assert row.source == "测试传感器"
        assert row.updated in ("刚刚", "1 秒前", "2 秒前") or row.updated.endswith("秒前")
        assert "CPU 封装温度" in row.hint, "组件声明的一句话补充进完整上下文"
    finally:
        island.close()


def test_alert_hysteresis_uses_the_declared_thresholds(tmp_path, monkeypatch, _clean_state):
    """声明阈值进既有判定链：两级 + 3pt 滞回（与 CPU/内存同一条路）。"""
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        for value, expected in ((74.0, isl.ALERT_WARN), (86.0, isl.ALERT_CRIT),
                                (84.0, isl.ALERT_CRIT), (80.0, isl.ALERT_WARN),
                                (60.0, "")):
            sampler.publish(EXT, value=value, text=str(int(value)))
            island._tick_extension()
            assert _seg(island, EXT).severity == expected, f"{value} 的告警级应当是 {expected!r}"
        assert isl.alert_thresholds(EXT) == (70.0, 85.0), "声明阈值并进了同一张表"
    finally:
        island.close()


def test_unavailable_component_has_no_placeholder_but_a_card_reason(tmp_path, monkeypatch,
                                                                   _clean_state):
    """组件暂缺 = 胶囊连占位都不画（不是 --），卡片写清哪一份文件不在。"""
    _write_component(_clean_state, provider=None)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        assert _has_segment(island, EXT) is False, "暂缺的组件不占位"
        assert not made, "暂缺的组件不建采样器（不采样、不 import）"
        _expanded(island)
        row = _row(island, EXT)
        assert row.state == isl.STATE_UNSUPPORTED
        assert "provider.py" in row.note and "该指标不可用" in row.note

        # 组件回来（写回 provider 文件 + 配置刷新）：同一个清单恢复，偏好还在
        _write_component(_clean_state)
        island.refresh_segments()
        app.processEvents()
        assert _has_segment(island, EXT) is True
        assert island._ext_sampler is not None
        assert EXT in island._ext_sampler.subscribed
    finally:
        island.close()


# ---------------------------------------------------------------- 超宽折叠
def test_oversize_component_folds_before_the_tail_even_in_the_middle(
        tmp_path, monkeypatch, _clean_state):
    """声明盒宽 >120px 的段是首批折叠候选：**它不在尾部也先折**，且计数/告警都正确。"""
    _write_component(_clean_state, "wide", manifest=WIDE_MANIFEST)
    _write_component(_clean_state)
    island, made, screen = _island(tmp_path, monkeypatch,
                                   metrics=[CPU, WIDE_EXT, MEM], screen_width=1400)
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        # 折一段：候选里只有超宽段 → 折的必须是它（不是尾部那一段）
        sampler.publish(WIDE_EXT, value=650.0, text="650")
        island._tick_extension()
        _width, plan = _narrow_to(island, screen, lambda p: len(p.folded) == 1)
        assert plan.folded == (WIDE_EXT,), f"超宽段应当先折，实际折了 {plan.folded}"
        assert CPU in plan.visible and MEM in plan.visible, "尾部段不该被牵连"
        assert plan.visible[-1] == "chip"
        row = island_layout.apply_shrink(plan, island._compose_info_segments(),
                                         primary_fm=island._metrics_for("primary"))
        assert [seg.seg_id for seg in row][-1] == "chip"
        assert row[-1].text == "+1", "chip 计数 = 实际折掉的段数"
        assert row[-1].severity == isl.ALERT_CRIT, "被折段的告警要透传到 chip"

        # 折两段：超宽段仍在先，随后才是尾部段（尾部优先只作用于其余段）
        _width, plan2 = _narrow_to(island, screen, lambda p: len(p.folded) == 2)
        assert plan2.folded == (WIDE_EXT, MEM), f"折叠次序错了：{plan2.folded}"

        _expanded(island)
        folded_row = _row(island, WIDE_EXT)
        assert "已折叠" in folded_row.updated, "卡片要如实标注这一段已被胶囊折叠"
        assert isinstance(folded_row.updated, str) and folded_row.updated
    finally:
        island.close()


def test_fold_unsubscribes_and_unfold_resubscribes_from_dashes(tmp_path, monkeypatch,
                                                              _clean_state):
    _write_component(_clean_state)
    island, made, screen = _island(tmp_path, monkeypatch,
                                   metrics=[CPU, MEM, EXT], screen_width=1400)
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        sampler.publish(EXT, value=74.0, text="74")
        island._tick_extension()
        _width, plan = _narrow_to(island, screen, lambda p: EXT in p.folded)
        assert EXT in sampler.unsubscribed, "被折的段必须退订（看不见就不采）"

        island.refresh_from_config()
        screen._rect = QRect(0, 0, 1400, 768)
        island.refresh_from_config()
        assert island._row_plan().folded == ()
        # 收口时整条采样器被关掉并重建（与真实路径一致）：看重建后的那一个
        restored = island._ext_sampler
        assert restored is not None and restored.subscribed == {EXT}, "展开后要重新订阅"
        assert EXT in island._ext_pending, "恢复首轮按 -- 等新样本"
        assert _seg(island, EXT).text == "--"
        restored.publish(EXT, value=91.0, text="91")
        island._tick_extension()
        assert _seg(island, EXT).text == "91"
    finally:
        island.close()


def test_strip_state_and_hide_stop_the_component_sampling(tmp_path, monkeypatch,
                                                          _clean_state):
    """停靠细条与隐藏都是"看不见"：订阅与线程一起收，显示回来再恢复。"""
    _write_component(_clean_state)
    cfg = _config(tmp_path, metrics=[EXT])
    cfg.set("dynamic_island", dict(cfg.get("dynamic_island"), edge_dock=True,
                                   dock_edge="top"))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    made: list[_StubSampler] = []
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_extension_sampler",
                        lambda self, interval: made.append(_StubSampler(interval)) or made[-1])
    monkeypatch.setattr(island_mod.DynamicIsland, "_current_screen",
                        lambda self: FakeScreen(1400))
    island = DynamicIsland(cfg)
    try:
        island.show()
        island._apply_position()
        app.processEvents()
        island._hover_peek = False
        island._mode = "docked"
        island._sync_content_sampling()
        assert island._ext_sampler is None or not island._ext_sampler.subscribed
        assert island._ext_samples == {}

        island._mode = "normal"
        island._sync_content_sampling()
        assert EXT in island._ext_subscribed, "出细条要恢复订阅"

        island.hide()
        assert island._ext_timer.isActive() is False
        island.show()
        app.processEvents()
        assert island._ext_sampler is not None and island._ext_sampler.started is True
    finally:
        island.close()


def test_close_and_reopen_clean_the_subscription_ledger(tmp_path, monkeypatch,
                                                        _clean_state):
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    island.show()
    app.processEvents()
    sampler = made[0]
    assert sampler.subscribed == {EXT}
    island.close()
    assert sampler.subscribed == set() and sampler.closed is True
    assert island._ext_sampler is None


def test_unregister_clears_thresholds_subscription_and_card(tmp_path, monkeypatch,
                                                            _clean_state):
    """运行期注册面：注销即撤阈值、退订、读数清掉（胶囊退回既有的 -- 降级段）。

    用替身配置：真 ``Config`` 的准入按**声明清单**归一化（运行期注册的 id 写不进
    配置——那正是"配置保存不依赖注册顺序"的落地），所以这条路径在真实配置下要
    有清单；这里验的是"注销之后宿主有没有把该清的清干净"。
    """
    isl.register_segment(id="runtime_only", label="运行期", format_value=lambda v: str(v),
                         max_text="88", unit="X", thresholds=(10.0, 20.0), read=lambda: 5)
    island, made, _screen = _island_with_config(
        _StubConfig(island_metrics=["runtime_only"]), monkeypatch)
    try:
        island.show()
        app.processEvents()
        sampler = made[0]
        assert sampler.subscribed == {"runtime_only"}
        assert isl.alert_thresholds("runtime_only") == (10.0, 20.0)

        isl.unregister_segment("runtime_only")
        island.refresh_segments()
        app.processEvents()
        assert isl.alert_thresholds("runtime_only") is None, "注销要撤阈值"
        assert "runtime_only" not in sampler.subscribed, "注销要退订"
        assert "runtime_only" not in island._ext_samples, "注销要清读数"
        assert _seg(island, "runtime_only").state == isl.STATE_UNKNOWN, \
            "没有声明也没有注册 = 既有的 -- 降级段（不炸、不留洞）"
    finally:
        island.close()


def test_uninstall_withdraws_the_declared_thresholds(tmp_path, monkeypatch, _clean_state):
    """卸载 = 撤声明 + 撤阈值（不留第二真值），偏好靠清单回归时恢复。"""
    directory = _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        assert isl.alert_thresholds(EXT) == (70.0, 85.0), "声明阈值并进了同一张表"
        (directory / host.MANIFEST_NAME).unlink()
        island.refresh_segments()
        app.processEvents()
        assert isl.alert_thresholds(EXT) is None
        assert island._ext_subscribed == set()
    finally:
        island.close()


def test_native_no_samples_yet_is_dashes_not_zero(tmp_path, monkeypatch, _clean_state):
    """没有快照（还没采到第一拍）：同宽 ``--``，绝不伪造 0。"""
    _write_component(_clean_state)
    island, made, _screen = _island(tmp_path, monkeypatch, metrics=[EXT])
    try:
        island.show()
        app.processEvents()
        assert made[0].snapshot() == {}
        segment = _seg(island, EXT)
        assert segment.text == "--" and segment.state == isl.STATE_SAMPLING
        assert segment.unit == "°C" and segment.box_text == "88"
    finally:
        island.close()


# ---------------------------------------------------------------- 真实采样器端到端
def test_real_component_reaches_the_capsule_through_the_qt_loop(tmp_path, monkeypatch,
                                                                _clean_state):
    """真组件 → 真采样线程 → 岛（真实 Qt 事件循环）：只断言"接线通了"。"""
    _write_component(_clean_state)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    island = DynamicIsland(_config(tmp_path, metrics=[EXT]))
    try:
        island.show()
        assert _wait_for(lambda: island._tick_extension()
                         and EXT in island._ext_samples, timeout=10.0), \
            "真实采样线程没能在预算内把读数送到岛上"
        segment = _seg(island, EXT)
        assert segment.state == isl.STATE_OK
        assert segment.text == "74" and segment.unit == "°C"
        assert segment.severity == isl.ALERT_WARN
    finally:
        island.close()


def test_no_hard_cap_on_how_many_component_rows_are_admitted(tmp_path, _clean_state):
    """不私自固定用户的横向数量上限：装多少组件就收多少 id（超出屏宽由折叠兜）。"""
    ids = [f"probe_{index:02d}" for index in range(12)]
    for index, segment_id in enumerate(ids):
        _write_component(_clean_state, f"component_{index:02d}",
                         manifest=dict(MANIFEST, id=segment_id, label=f"探针{index}"))
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", list(ids))
    assert cfg.get("island_metrics") == ids, "admission 不设上限"
    assert cfg.save() is True
    assert Config(base=tmp_path).get("island_metrics") == ids


def test_circuit_broken_component_says_so_in_the_card(tmp_path, monkeypatch,
                                                      _clean_state):
    """慢读熔断要**看得见**：胶囊退回 ``--``，卡片写"已停止采样这一段"。

    用真实采样器 + 极小阻塞预算（真等 1 秒级会让这条用例变慢），RUN 的是同一条
    熔断代码路径；组件线程阻塞期间 GUI 一次都没调 read（读快照不是采样）。
    """
    _write_component(
        _clean_state,
        provider=PROVIDER_SLOW)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(
        island_mod.DynamicIsland, "_make_extension_sampler",
        lambda self, interval: host.ExtensionSampler(
            interval=0.2, block_budget=0.05, block_limit=2))
    island = DynamicIsland(_config(tmp_path, metrics=[EXT]))
    try:
        island.show()
        assert _wait_for(lambda: island._tick_extension() and _card_note(island),
                         timeout=15.0), "熔断结果没送到宿主"
        note = _card_note(island)
        assert note.startswith("该指标不可用"), note
        assert "停止采样" in note, note
        assert _seg(island, EXT).text == "--"
        assert _seg(island, EXT).state == isl.STATE_ERROR
    finally:
        island.close()


def test_the_real_qt_loop_delivers_the_component_reading_to_capsule_and_card(
        tmp_path, monkeypatch, _clean_state):
    """真组件 → 真采样线程 → **真 QTimer 表** → 胶囊从 ``--`` 变数、卡片跟着更新。

    本用例**一次都不手工调** ``_tick_extension()``：只跑真实 Qt 事件循环
    （``processEvents``）等 QTimer 自己派发。所以它守护的是"宿主有没有在拉采样快照"
    ——少一次 ``_sync_extension_slot`` 里的 ``_ext_timer.start()``，采样线程照跑
    （provider 真的被调）、读数却永远进不了宿主，这条用例就会红。
    """
    directory = _write_component(_clean_state, provider=PROVIDER_WAITS_FOR_RELEASE)
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    island = DynamicIsland(_config(tmp_path, metrics=[EXT]))
    try:
        island.show()
        app.processEvents()
        # 组件的第一次 read 正卡在"等 release 文件"里：采样线程在跑、但还没有读数
        assert island._ext_timer.isActive() is True, \
            "表必须由宿主自己起（_sync_extension_slot），否则读数永远没人读"
        assert _seg(island, EXT).text == "--"
        assert _seg(island, EXT).state == isl.STATE_SAMPLING, "还没采到第一拍就是 --（不是 0）"

        (directory / "release").write_text("go", encoding="utf-8")   # 这次 read 读完
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0), \
            "QTimer 没把组件读数送到胶囊（-- 没变成数）"

        segment = _seg(island, EXT)
        assert segment.state == isl.STATE_OK and segment.unit == "°C"
        assert segment.severity == isl.ALERT_WARN, "原始值 74 进了与 CPU/内存同一条阈值链"
        assert island._ext_pending == set(), "拿到第一拍后不再是 pending"
        assert island._ext_samples[EXT].text == "74"

        _expanded(island)                       # 卡片展开（同一份快照喂的）
        row = _row(island, EXT)
        assert row.value == "74°C"
        assert row.source == "测试传感器"
        assert row.updated.endswith("秒前") or row.updated == "刚刚"
    finally:
        island.close()


def test_extension_reading_change_repaints_the_capsule_on_its_own(tmp_path, monkeypatch,
                                                                 _clean_state):
    """第三方-only 的静止胶囊：表拉到新读数要**自己**重绘（不手工 tick、不手工 update）。

    回归对象：``_ext_timer.timeout`` 曾直连只 ``return changed`` 的 ``_tick_extension``
    ——Qt 不消费返回值，全仓也没有第二个调用者，于是 provider 的第二次读进了
    ``_ext_samples`` 却一个像素都不刷新（要等 30s 心跳、鼠标或动画偶然救场）。本用例
    只跑真事件循环（provider → 真采样线程 → 真 QTimer 表 → 真 paintEvent）：读数没变
    → 一次都不重绘；读数变了 → 胶囊按采样节奏自己重绘；读数再长也不撑宽窗口。

    时钟必须钉住：``_width_key`` 按设计含主信息**现文案**（现文案超声明样本时要加宽，
    见 ``DynamicIsland._plan_inputs`` 的末位说明），而时钟的样本是 ``88:88``——跳分
    只让键变、不让几何变。跨分界的两次采样之间比对这个键，红了也不是"读数把窗口撑
    宽了"，所以这里用假表把场景钉在 09:20；跳分本身由
    ``test_clock_minute_tick_changes_the_text_but_no_geometry`` 单量。
    """
    directory = _write_component(_clean_state, provider=PROVIDER_READS_A_FILE)
    (directory / "value.txt").write_text("74", encoding="utf-8")
    _freeze_clock(monkeypatch, 9, 20)
    island = _real_ext_island(_config(tmp_path, metrics=[EXT]), monkeypatch)
    try:
        island.show()
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0), \
            "第一拍没在预算内到宿主"
        _settle(island)
        baseline = island.frames[-1]
        assert baseline is not None and not baseline.isNull(), "稳态一帧都没画出去"
        width_key, size, pos = island._width_key(), island.size(), island.pos()

        island.reset_frames()
        _spin(1000)                     # ≥3 个采样拍，provider 每次读回同一个 74
        assert island.paints == 0, f"读数没变却重绘了 {island.paints} 次"

        (directory / "value.txt").write_text("42", encoding="utf-8")
        assert _pump_until(lambda: _seg(island, EXT).text == "42", timeout=15.0), \
            "第二次 read 没到宿主"
        assert _wait_paint(island) >= 1, "读数变了却一次都没重绘（_ext_timer 的返回值没人消费）"
        assert island.frames[-1].toImage() != baseline.toImage(), "自动重绘的那一帧画的还是旧读数"
        assert (island._width_key(), island.size(), island.pos()) == (width_key, size, pos), \
            "采样值变化不许动窗口几何/宽度白名单"
        assert island.geometry_updates == 0, "这一笔重绘顺手重算了窗口几何（读数只在盒内伸缩）"

        # 长读数：盒宽只吃声明的 max_text（"88"），13 位数字照样不许把窗口撑宽
        # （胶囊里它按盒宽右省略，那是 format_field_box 的事——这里量的是不扩宽）
        long_text = "1234567890123"
        island.reset_frames()
        (directory / "value.txt").write_text(long_text, encoding="utf-8")
        assert _pump_until(
            lambda: island._ext_samples.get(EXT) is not None
            and island._ext_samples[EXT].text == long_text, timeout=15.0), "长读数没到宿主"
        assert _wait_paint(island) >= 1, "长读数到达时没有重绘"
        assert island.frames[-1].toImage() != baseline.toImage()
        assert (island._width_key(), island.size(), island.pos()) == (width_key, size, pos), \
            "长读数把窗口撑宽了"
    finally:
        island.close()


def test_clock_minute_tick_changes_the_text_but_no_geometry(tmp_path, monkeypatch,
                                                            _clean_state):
    """主信息时钟跳分：现文案与宽度白名单键跟着变，窗口几何与第三方值盒一动不动。

    为什么单列一条：``_width_key`` 含主信息**现文案**（现文案超声明样本时要加宽，见
    ``DynamicIsland._plan_inputs`` 的末位说明），跳分**必然**让这个键变——上一刻与
    下一刻的键直接比，哪怕窗口一个像素都没动也会不等。时钟的样本是 ``88:88``，恒宽
    于任何 ``HH:mm``，所以真正该守的是几何：行计划的自然宽、窗口 size/pos、第三方段
    的值盒落位。跳分在这里是确定性事件（假表手工推一分钟），心跳那一路（``_refresh``
    → ``_update_size``）也照跑一遍：重算归重算，几何必须落回同一个值。
    """
    directory = _write_component(_clean_state, provider=PROVIDER_READS_A_FILE)
    (directory / "value.txt").write_text("74", encoding="utf-8")
    clock = _freeze_clock(monkeypatch, 9, 20)
    island = _real_ext_island(_config(tmp_path, metrics=[EXT]), monkeypatch)
    try:
        island.show()
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0), \
            "第一拍没在预算内到宿主"
        _settle(island)
        assert _seg(island, "primary").text == "09:20"
        key_before, geometry_before = island._width_key(), _row_geometry(island)

        island.reset_frames()
        clock.tick()                     # 09:20 → 09:21
        island.update()                  # 时钟变后的下一笔绘制（真 paintEvent 落地）
        assert _wait_paint(island) >= 1, "跳分后的那一笔重绘没落地"
        assert _seg(island, "primary").text == "09:21", "假表没换到 09:21，这条用例就没在量跳分"
        assert island._width_key() != key_before, \
            "现文案在宽度白名单键里（产品的既定契约）——所以跨分界比对 _width_key 不是几何断言"
        assert _row_geometry(island) == geometry_before, "跳分推了窗口/值盒几何"
        assert island.geometry_updates == 0, "跳分那一笔重绘顺手重算了窗口几何"

        island._refresh()                # 心跳那一路：真重算一次几何
        assert _wait_paint(island) >= 1, "心跳那一笔重绘没落地"
        assert island.geometry_updates >= 1, "心跳没重算几何？那这条用例量错了通道"
        assert _row_geometry(island) == geometry_before, "心跳重算后几何没落回原值"
    finally:
        island.close()


def test_extension_error_and_recovery_repaint_through_the_real_loop(tmp_path, monkeypatch,
                                                                   _clean_state):
    """``ok ↔ error`` 的每次切换都要重绘：读数坏掉不是"没有变化"。"""
    directory = _write_component(_clean_state, provider=PROVIDER_READS_A_FILE)
    (directory / "value.txt").write_text("74", encoding="utf-8")
    island = _real_ext_island(_config(tmp_path, metrics=[EXT]), monkeypatch)
    try:
        island.show()
        assert _pump_until(lambda: _seg(island, EXT).state == isl.STATE_OK, timeout=15.0)
        _settle(island)
        ok_frame = island.frames[-1]

        island.reset_frames()
        (directory / "value.txt").write_text("坏值", encoding="utf-8")   # provider 抛 ValueError
        assert _pump_until(lambda: _seg(island, EXT).state == isl.STATE_ERROR, timeout=15.0), \
            "组件读值失败没升到 error 态"
        assert _wait_paint(island) >= 1, "ok → error 没有重绘"
        error_frame = island.frames[-1]
        assert error_frame.toImage() != ok_frame.toImage(), "error 那一帧画的还是旧读数"
        assert _seg(island, EXT).text == "--"

        island.reset_frames()
        (directory / "value.txt").write_text("42", encoding="utf-8")
        assert _pump_until(lambda: _seg(island, EXT).text == "42", timeout=15.0)
        assert _wait_paint(island) >= 1, "error → ok 没有重绘"
        assert island.frames[-1].toImage() != error_frame.toImage(), "恢复后画的还是 --"
    finally:
        island.close()


def test_open_card_follows_extension_readings_through_the_real_loop(tmp_path, monkeypatch,
                                                                    _clean_state):
    """卡片已经开着：读数变了卡片自己跟上（每秒 tick），不需要用户去动鼠标。"""
    directory = _write_component(_clean_state, provider=PROVIDER_READS_A_FILE)
    (directory / "value.txt").write_text("74", encoding="utf-8")
    island = _real_ext_island(_config(tmp_path, metrics=[EXT]), monkeypatch)
    try:
        island.show()
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0)
        _settle(island)
        _expanded(island)
        value_label = _card_value_label(island, EXT)
        assert value_label.text() == "74°C", "值列 = 胶囊同一个合并串（值 + 单位）"

        (directory / "value.txt").write_text("42", encoding="utf-8")
        assert _pump_until(lambda: value_label.text() == "42°C", timeout=15.0), \
            "卡片开着时读数变了，卡片的值列没跟上"
    finally:
        island.close()


def test_disabled_extension_slot_stops_sampling_and_repainting(tmp_path, monkeypatch,
                                                               _clean_state):
    """停用 = 不采、也不按采样节奏定时重绘（读数都不采，就没有可刷的东西）。"""
    directory = _write_component(_clean_state, provider=PROVIDER_READS_A_FILE)
    (directory / "value.txt").write_text("74", encoding="utf-8")
    read_log = directory / "read.log"
    cfg = _config(tmp_path, metrics=[EXT])
    island = _real_ext_island(cfg, monkeypatch)
    try:
        island.show()
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0)
        _settle(island)
        sampler = island._ext_sampler
        assert sampler is not None

        cfg.set("island_metrics", [])
        island.refresh_from_config()
        app.processEvents()
        assert sampler.subscriber_count == 0, "停用即退订（不采样）"
        assert island._ext_timer.isActive() is False, "停用要停表（不按采样节奏刷新）"

        _spin(300)                      # 段消失带来的结构淡化（130ms）先走完
        reads = len(read_log.read_text(encoding="utf-8").splitlines())
        island.reset_frames()
        _spin(1000)
        assert island.paints == 0, f"停用后还在定时重绘（{island.paints} 次/1s）"
        assert len(read_log.read_text(encoding="utf-8").splitlines()) == reads, \
            "停用后 provider 还在被调用"
    finally:
        island.close()


def test_deactivation_keeps_the_sampler_and_never_adds_a_second_worker(
        tmp_path, monkeypatch, _clean_state):
    """不可见 → 收口 → 立刻恢复：**同一个采样器、同一条 worker、token 接着走**。

    旧实现在收口分支把采样器 ``close()`` 并置 None，重新启用时又造一个——旧 worker 若
    还卡在组件 ``read`` 里（杀不掉），就有两条 worker 同时跑同一个 provider。现在实例
    留着，``start()` 接续那条 worker；恢复后自动继续采当刻订阅（不手工 tick、不新建）。
    """
    _write_component(_clean_state)
    gated = _GatedProvider(74.0)
    made: list[host.ExtensionSampler] = []

    def _make(self, interval):
        sampler = host.ExtensionSampler(interval=interval,
                                        resolver=lambda _sid: gated.declaration())
        made.append(sampler)
        return sampler

    monkeypatch.setattr(island_mod.DynamicIsland, "_make_net_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_sys_monitor",
                        lambda self, interval: _StubMonitor(interval))
    monkeypatch.setattr(island_mod.DynamicIsland, "_make_extension_sampler", _make)
    island = DynamicIsland(_config(tmp_path, metrics=[EXT]))
    try:
        island.show()
        app.processEvents()
        sampler = island._ext_sampler
        assert sampler is not None and made == [sampler]
        assert _pump_until(lambda: gated.gates[0].entered.is_set(), timeout=15.0), \
            "第一拍没能进组件的 read"
        token_before = sampler._tokens[EXT]

        data = dict(island.config.get("dynamic_island"))
        data["show_info"] = False                      # 收口：信息槽关掉
        island.config.set("dynamic_island", data)
        island.refresh_from_config()
        app.processEvents()
        assert island._ext_timer.isActive() is False, "收口要停表"
        assert island._ext_sampler is sampler, "收口不许丢采样器实例（丢了就会再造第二条 worker）"
        assert len(made) == 1

        data["show_info"] = True                       # 立刻恢复（旧 read 还在飞）
        island.config.set("dynamic_island", data)
        island.refresh_from_config()
        app.processEvents()
        assert island._ext_sampler is sampler and len(made) == 1, "恢复复用同一个实例"
        assert island._ext_timer.isActive() is True, "恢复要把表接回来"
        assert _wait_for(lambda: gated.max_inflight >= 2, timeout=1.0) is False, \
            "旧 worker 还卡在 read 里时，不许出现第二条 worker"
        assert len(_live_ext_workers()) == 1, "线程数有界：一条采样线程"
        assert sampler._tokens[EXT] > token_before, "退订→重订要换代（token 单调，不清零）"

        gated.release_all()
        assert _pump_until(lambda: _seg(island, EXT).text == "74", timeout=15.0), \
            "恢复之后要自动继续（不手工 tick）"
        assert sampler._tokens[EXT] == token_before + 1
    finally:
        gated.release_all()
        island.close()
        app.processEvents()
    assert _wait_for(lambda: not _live_ext_workers(), timeout=5.0), \
        "close 要有界收口（卡在 read 里的 worker 释放后必须退出，不留线程）"


# ---------------------------------------------------------------- 设置页
def test_settings_page_lists_component_rows_at_the_builtin_level(tmp_path, monkeypatch,
                                                                _clean_state):
    """设置页：组件行与内置指标行**同层级同风格**，可勾可存，重启读回。"""
    _write_component(_clean_state)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    cfg = Config(base=tmp_path)
    dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=True)
    try:
        row = dialog.findChild(settings_mod.SettingRow, f"settingRow_island_{EXT}")
        assert row is not None, "声明过的组件应当在设置页有一行"
        assert row.label.text() == "灵动岛指标 · 温度", "与内置行同一 label 口径"
        toggle = getattr(dialog, f"island_{EXT}_check")
        assert toggle.isChecked() is False, "默认关（与内置指标行同一口径）"

        toggle.setChecked(True)
        assert dialog._write_config() is True
        cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
        assert cfg.get("island_metrics") == [EXT]
        assert Config(base=tmp_path).get("island_metrics") == [EXT], "重启后偏好还在"

        # 与内置行同序规则：后勾的追尾，已有且仍勾的按原序在前
        dialog2 = settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)
        try:
            dialog2.island_cpu_check.setChecked(True)
            assert dialog2._write_config() is True
        finally:
            dialog2.close()
        assert Config(base=tmp_path).get("island_metrics") == [EXT, CPU], \
            "组件行在典范序里排在内置行之后"
    finally:
        dialog.close()


def test_settings_page_toggle_off_keeps_the_declaration_but_stops_sampling(
        tmp_path, monkeypatch, _clean_state):
    """停用 ≠ 卸载：关掉只是偏好（不采样），声明与设置页行都还在。"""
    _write_component(_clean_state)
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [EXT])
    island, made, _screen = _island_with_config(cfg, monkeypatch)
    try:
        island.show()
        app.processEvents()
        assert made and made[0].subscribed == {EXT}
        cfg.set("island_metrics", [])
        island.refresh_from_config()
        app.processEvents()
        assert made[0].subscribed == set(), "停用即退订（不采样）"
        assert _has_segment(island, EXT) is False
        assert host.declaration_for(EXT) is not None, "声明与偏好都还在（与卸载区分）"
        assert isl.alert_thresholds(EXT) == (70.0, 85.0), "停用不撤阈值"
    finally:
        island.close()


# ---------------------------------------------------------------- 设置进程（独立解释器）
def test_settings_process_reads_the_manifest_without_executing_providers(tmp_path):
    """跨进程：独立解释器里的设置进程路径读同一份静态 metadata、不执行组件代码。"""
    root = tmp_path / "island-extensions"
    directory = root / "marker"
    directory.mkdir(parents=True)
    manifest = dict(MANIFEST, id="marker", provider="provider.py",
                    read="read_value", format="format_value")
    (directory / host.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (directory / "provider.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.flag').write_text('1', encoding='utf-8')\n"
        "\n\ndef read_value():\n    return 1\n\n\ndef format_value(value):\n"
        "    return str(value)\n", encoding="utf-8")

    script = (
        "import json\n"
        "from pet import island_extension_host as host\n"
        "from pet.config import _clean_island_metrics\n"
        "from pet.settings_network import metric_order\n"
        "print(json.dumps({\n"
        "    'ids': sorted(host.declared_metric_ids()),\n"
        "    'order': list(metric_order()),\n"
        "    'kept': _clean_island_metrics(['cpu', 'marker', 'nope']),\n"
        "}))\n"
    )
    env = dict(os.environ)
    env[host.ENV_DIRS] = str(root)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=str(repo), env=env,
        capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["ids"] == ["marker"]
    assert payload["kept"] == ["cpu", "marker"]
    assert payload["order"][:4] == ["network", "cpu", "mem", "gpu"], \
        "内置行的典范序不变（组件行追加在后）"
    assert payload["order"][-1] == "marker"
    assert not (directory / "imported.flag").exists(), \
        "设置进程路径不得执行组件代码"


def test_example_component_fixture_stays_out_of_the_production_user_dir():
    """示例 fixture 只出现在临时目录里：仓库里不放生产用组件目录。

    "生产用户目录"是用户自己安装组件的地方（``user_root()``），测试一概不写它；
    示例组件一律走临时目录 + 环境变量覆盖（见本文件的 fixture）。
    """
    assert host.project_root().name == host.ROOT_DIR_NAME
    assert not host.project_root().exists(), \
        "仓库里不放生产用组件目录（示例 fixture 走临时目录 / 环境变量覆盖）"
