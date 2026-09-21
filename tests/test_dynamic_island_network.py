# -*- coding: utf-8 -*-
"""灵动岛的网络信息槽（info_mode == "network"）。

锁住三件事：
1. 三个指标（延迟/下行/上行）能按开关**独立**组合显示；
2. 探测是**节流**的——灵动岛的刷新拍远密于探测间隔，不能每拍都发包；
3. 停靠/隐藏时**不探测**（用户看不见，没必要花流量）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet import network_status as ns
from pet.config import Config
from pet.dynamic_island import DynamicIsland


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "network", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """所有用例都不得真发包：把探测替换成可数的替身。"""
    calls = {"latency": 0, "throughput": 0}

    def fake_latency(*_a, **_k):
        calls["latency"] += 1
        return ns.LatencySample(ok=True, rtt_ms=42.0)

    _state = {"recv": 0, "sent": 0}

    def fake_throughput():
        calls["throughput"] += 1
        _state["recv"] += 2048
        _state["sent"] += 1024
        return ns.Throughput(total_recv=_state["recv"], total_sent=_state["sent"])

    monkeypatch.setattr(ns, "measure_latency", fake_latency)
    monkeypatch.setattr(ns, "read_throughput", fake_throughput)
    return calls


def test_network_mode_shows_latency(tmp_path):
    _qapp()
    island = _island(tmp_path)
    island._refresh()

    text = island._info_text()
    assert "42ms" in text, text


def test_network_mode_hides_latency_when_disabled(tmp_path):
    _qapp()
    island = _island(tmp_path, network_show_latency=False)
    island._refresh()

    assert "42ms" not in island._info_text()


def test_network_mode_shows_both_directions(tmp_path):
    """上下行分开显示——用户要的就是能区分上传和下载。"""
    _qapp()
    island = _island(tmp_path, network_show_latency=False)
    island._refresh()
    island._refresh()          # 第二次才有速度（需要两次采样求差）

    text = island._info_text()
    assert "↓" in text and "↑" in text, text


def test_down_and_up_can_be_toggled_independently(tmp_path):
    _qapp()
    island = _island(tmp_path, network_show_latency=False, network_show_up_speed=False)
    island._refresh()
    island._refresh()

    text = island._info_text()
    assert "↓" in text
    assert "↑" not in text, "上行已关，不该出现"


def test_all_metrics_off_falls_back_to_time(tmp_path):
    """三个都关时不能显示空串——胶囊会变成空白，不如退回时间。"""
    _qapp()
    island = _island(tmp_path, network_show_latency=False,
                     network_show_down_speed=False, network_show_up_speed=False)
    island._refresh()

    text = island._info_text()
    assert text.strip(), "不能是空串"
    assert ":" in text, "应退回 HH:mm 形式"


def test_probe_is_throttled_by_interval(tmp_path, _no_real_network):
    """灵动岛刷新拍远比探测间隔密，必须节流——否则一秒内会被刷新多次。"""
    _qapp()
    island = _island(tmp_path, network_probe_interval_seconds=60)
    island._refresh()
    first = _no_real_network["latency"]
    assert first >= 1, "首次应探测"

    for _ in range(5):
        island._refresh()

    assert _no_real_network["latency"] == first, (
        "间隔 60 秒内的重复刷新不该重新探测"
    )


def test_force_probe_bypasses_throttle(tmp_path, _no_real_network):
    """用户点开弹窗或切到该模式时应立即刷新，不等下一拍。"""
    _qapp()
    island = _island(tmp_path, network_probe_interval_seconds=60)
    island._refresh()
    before = _no_real_network["latency"]

    island._refresh_network(force=True)

    assert _no_real_network["latency"] > before


def test_docked_island_skips_probe(tmp_path, _no_real_network):
    """停靠收成细条时用户看不见，不该继续花流量探测。"""
    _qapp()
    island = _island(tmp_path, network_probe_interval_seconds=1)
    island._refresh()
    before = _no_real_network["latency"]

    island._mode = "docked"
    island._hover_peek = False
    island._refresh_network(force=True)

    assert _no_real_network["latency"] == before, "停靠时不该探测"


def test_switching_to_network_mode_probes_immediately(tmp_path, _no_real_network):
    """从别的模式切到 network 时要马上给数据，不能等一个探测周期。"""
    _qapp()
    island = _island(tmp_path, info_mode="time",
                     network_probe_interval_seconds=60)
    assert _no_real_network["latency"] == 0

    data = dict(island._cfg)
    data["info_mode"] = "network"
    island.config.set("dynamic_island", data)
    island.refresh_from_config()

    assert _no_real_network["latency"] >= 1, "切模式应触发首次探测"


# ---------------------------------------------------------------- 设置页契约
#
# 背景：设置页保存灵动岛时是**整体重建 dict**再 config.set 写回。只要漏写一个
# 键，用户保存一次设置就会把它从配置里抹掉（表现为「设好的开关自己变回去了」）。
# 这里用 AST 静态检查锁住这个契约——比启动整个设置对话框稳定得多。


def test_settings_dialog_writes_back_all_network_keys():
    """设置页写回 dynamic_island 时必须带上全部网络键。

    这是个**易漏点**：新增网络配置键时若忘了同步 _write_config，
    用户一次「打开设置→关闭」就会静默丢掉这些键。
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "pet" / "modern_settings_dialog.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    # 找到 config.set("dynamic_island", {...}) 那个 dict 字面量的所有字符串键
    written: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "set"):
            continue
        args = node.args
        if len(args) < 2:
            continue
        first = args[0]
        if not (isinstance(first, ast.Constant) and first.value == "dynamic_island"):
            continue
        payload = args[1]
        if isinstance(payload, ast.Dict):
            for key in payload.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    written.add(key.value)

    assert written, "没找到 dynamic_island 的写回字典（结构变了？）"
    required = {
        "network_show_latency",
        "network_show_down_speed",
        "network_show_up_speed",
        "network_probe_interval_seconds",
    }
    missing = required - written
    assert not missing, (
        f"设置页写回 dynamic_island 时漏了这些网络键：{sorted(missing)}——"
        "用户保存一次设置就会把它们从配置里抹掉"
    )
