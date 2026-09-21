# -*- coding: utf-8 -*-
"""网络状态探测的离线单元测试。

全部用例**不联网**：联网部分用固定样本注入，只验证解析、格式化与降级逻辑。
需要真实网络的行为（如延迟数值本身）不在这里断言，避免 CI 上抖。
"""
from __future__ import annotations

import json
import socket
import struct

import pytest

from pet import network_status as ns


# ---------------------------------------------------------------- 格式化


def test_format_latency_milliseconds():
    assert ns.format_latency(42.4) == "42ms"
    assert ns.format_latency(0) == "0ms"


def test_format_latency_switches_to_seconds():
    """超过 1 秒改用秒——否则会出现 4 位数字，灵动岛放不下。"""
    assert ns.format_latency(1500) == "1.5s"
    assert ns.format_latency(12000) == "12.0s"


def test_format_latency_negative_clamped():
    """负数不该出现（计时异常时也要有兜底），夹到 0。"""
    assert ns.format_latency(-5) == "0ms"


@pytest.mark.parametrize("value,tier", [
    (0, "good"),
    (79.9, "good"),
    (80, "fair"),        # 边界：等于阈值算下一档
    (199.9, "fair"),
    (200, "poor"),
    (5000, "poor"),
])
def test_latency_tier_boundaries(value, tier):
    """三档配色（对齐常见游戏习惯）的边界必须精确。"""
    assert ns.latency_tier(value) == tier


def test_format_speed_sub_kilobyte_reads_zero():
    """低于 1 KB/s 一律显示 0——空闲时抖出来的几字节没意义，
    显示 0.02 KB/s 反而让人以为在传东西。"""
    assert ns.format_speed(0) == "0 KB/s"
    assert ns.format_speed(512) == "0 KB/s"
    assert ns.format_speed(1023) == "0 KB/s"


def test_format_speed_units():
    assert ns.format_speed(1024) == "1 KB/s"
    assert ns.format_speed(1536) == "2 KB/s"          # 1.5 KB 四舍五入
    assert ns.format_speed(1024 * 1.4) == "1 KB/s"    # 1.4 KB 向下
    assert ns.format_speed(2.5 * 1024 ** 2) == "2.5 MB/s"
    assert ns.format_speed(1.5 * 1024 ** 3) == "1.5 GB/s"


def test_format_speed_negative_clamped():
    assert ns.format_speed(-100) == "0 KB/s"


def test_format_bytes_units():
    assert ns.format_bytes(0) == "0 B"
    assert ns.format_bytes(2048) == "2.0 KB"
    assert ns.format_bytes(5 * 1024 ** 2) == "5.0 MB"
    assert ns.format_bytes(3 * 1024 ** 3) == "3.00 GB"


# ---------------------------------------------------------------- EMA 平滑


def test_ema_first_sample_takes_value_directly():
    """首个样本不能衰减——否则开局会显示一个偏小的假值。"""
    assert ns.ema_smooth(None, 100.0) == 100.0


def test_ema_smooths_toward_new_value():
    """新值权重 0.2：100 → 200 应得到 120，而不是直接跳到 200。"""
    assert ns.ema_smooth(100.0, 200.0) == pytest.approx(120.0)


def test_ema_converges_monotonically():
    """连续同向输入应单调逼近目标，不来回震荡。"""
    value = 0.0
    for _ in range(50):
        value = ns.ema_smooth(value, 100.0)
    assert value == pytest.approx(100.0, abs=0.1)


# ---------------------------------------------------------------- 延迟探测


class _FakeSock:
    """替身 socket：可配置「正常应答」或「超时」。"""

    def __init__(self, *, timeout: bool = False, recv_error: Exception | None = None):
        self.timeout = timeout
        self.recv_error = recv_error
        self.closed = False
        self.sent_to: tuple | None = None

    def settimeout(self, _v):
        pass

    def sendto(self, _data, addr):
        self.sent_to = addr

    def recvfrom(self, _n):
        if self.recv_error is not None:
            raise self.recv_error
        if self.timeout:
            raise socket.timeout("timed out")
        return b"\x00" * 12, ("223.5.5.5", 53)

    def close(self):
        self.closed = True


def test_measure_latency_success(monkeypatch):
    sock = _FakeSock()
    monkeypatch.setattr(ns.socket, "socket", lambda *a, **k: sock)

    sample = ns.measure_latency()

    assert sample.ok is True
    assert sample.rtt_ms is not None and sample.rtt_ms >= 0
    assert sock.sent_to == (ns.PROBE_HOST, ns.PROBE_PORT)
    assert sock.closed, "无论成败都必须关掉 socket"


def test_measure_latency_timeout_is_not_an_exception(monkeypatch):
    """超时是最常见的结果（断网时），必须是普通返回值而不是抛异常。"""
    sock = _FakeSock(timeout=True)
    monkeypatch.setattr(ns.socket, "socket", lambda *a, **k: sock)

    sample = ns.measure_latency()

    assert sample.ok is False
    assert sample.rtt_ms is None
    assert "超时" in sample.error
    assert sock.closed


def test_measure_latency_oserror_is_swallowed(monkeypatch):
    """权限/资源类 OSError 同样不能冒泡——桌宠不能因为探测失败而崩。"""
    sock = _FakeSock(recv_error=OSError("network unreachable"))
    monkeypatch.setattr(ns.socket, "socket", lambda *a, **k: sock)

    sample = ns.measure_latency()

    assert sample.ok is False
    assert "OSError" in sample.error
    assert sock.closed


def test_latency_sample_display_offline_when_failed():
    assert ns.LatencySample(ok=False).display == "离线"
    assert ns.LatencySample(ok=True, rtt_ms=42.0).display == "42ms"


def test_dns_probe_packet_is_minimal():
    """探测包要尽量小——1 秒一跳的高频场景下，每次多几十字节会累积成流量。

    用根域查询（QNAME 仅 1 字节）而不是 google.com（28 字节）。
    """
    packet = ns._dns_query_packet()
    assert len(packet) == 17
    # 头部 12 字节 + QNAME 1 + QTYPE 2 + QCLASS 2
    assert struct.unpack(">H", packet[:2])[0] == 0x1234


# ---------------------------------------------------------------- 本机 IP


def test_probe_local_ip_returns_address(monkeypatch):
    class _S:
        def settimeout(self, _v): pass
        def connect(self, _addr): pass
        def getsockname(self): return ("192.168.43.24", 51234)
        def close(self): pass

    monkeypatch.setattr(ns.socket, "socket", lambda *a, **k: _S())
    assert ns.probe_local_ip() == "192.168.43.24"


def test_probe_local_ip_returns_empty_on_failure(monkeypatch):
    """取不到就返回空串——绝不能编一个 127.0.0.1 出来冒充。"""
    class _S:
        def settimeout(self, _v): pass
        def connect(self, _addr): raise OSError("no route")
        def close(self): pass

    monkeypatch.setattr(ns.socket, "socket", lambda *a, **k: _S())
    assert ns.probe_local_ip() == ""


# ---------------------------------------------------------------- 网速


def test_read_throughput_sums_real_interfaces(monkeypatch):
    """多个网卡要累加，但回环/伪接口必须排除。"""
    class _C:
        def __init__(self, r, s): self.bytes_recv, self.bytes_sent = r, s

    fake = {
        "WLAN": _C(1000, 200),
        "以太网": _C(500, 50),
        "Loopback Pseudo-Interface 1": _C(999999, 999999),   # 必须被排除
        "Teredo Tunneling Pseudo-Interface": _C(88888, 88888),
    }
    class _P:
        @staticmethod
        def net_io_counters(pernic=False): return fake
    monkeypatch.setitem(__import__("sys").modules, "psutil", _P)

    got = ns.read_throughput()
    assert got.total_recv == 1500
    assert got.total_sent == 250


def test_read_throughput_survives_missing_psutil(monkeypatch):
    """没有 psutil 时返回全零，而不是抛异常——网速只是锦上添花。"""
    import sys
    monkeypatch.setitem(sys.modules, "psutil", None)
    got = ns.read_throughput()
    assert got.total_recv == 0 and got.total_sent == 0


def test_speed_from_delta_computes_rate():
    prev = ns.Throughput(total_recv=1000, total_sent=500)
    cur = ns.Throughput(total_recv=3048, total_sent=1524)

    sp = ns.speed_from_delta(prev, cur, 1.0)
    assert sp.down_bps == pytest.approx(2048.0)
    assert sp.up_bps == pytest.approx(1024.0)


def test_speed_from_delta_guards_tiny_interval():
    """间隔过小会算出天文数字，必须兜底。"""
    prev = ns.Throughput(total_recv=0, total_sent=0)
    cur = ns.Throughput(total_recv=10 ** 9, total_sent=10 ** 9)

    sp = ns.speed_from_delta(prev, cur, 0.001)
    assert sp.down_bps == 0.0 and sp.up_bps == 0.0


def test_speed_from_delta_guards_counter_wraparound():
    """网卡重启会让计数器回绕变小，此时本拍不报速度（否则出现负速度）。"""
    prev = ns.Throughput(total_recv=10 ** 9, total_sent=10 ** 9)
    cur = ns.Throughput(total_recv=100, total_sent=100)

    sp = ns.speed_from_delta(prev, cur, 1.0)
    assert sp.down_bps == 0.0 and sp.up_bps == 0.0
    assert sp.total_recv == 100, "累计值仍要更新，否则下一拍继续误判"


# ---------------------------------------------------------------- 属地解析


def test_parse_ipip_happy_path():
    payload = {
        "ret": "ok",
        "data": {"ip": "106.41.245.87", "location": ["中国", "吉林", "长春", "", "电信"]},
    }
    info = ns._parse_ipip(payload)

    assert info is not None
    assert info.ip == "106.41.245.87"
    assert info.country == "中国"
    assert info.region == "吉林"
    assert info.isp == "电信"
    assert info.in_china is True
    assert info.region_text == "中国 · 吉林 · 长春 · 电信"


def test_parse_ipip_rejects_error_payload():
    assert ns._parse_ipip({"ret": "err"}) is None
    assert ns._parse_ipip({"ret": "ok", "data": "不是字典"}) is None
    assert ns._parse_ipip("不是字典") is None


def test_parse_ipip_tolerates_short_location_list():
    """location 短于 5 段时要补空串，不能 IndexError。"""
    info = ns._parse_ipip({"ret": "ok", "data": {"ip": "1.1.1.1", "location": ["中国"]}})
    assert info is not None
    assert info.country == "中国"
    assert info.isp == ""


def test_parse_pconline_decodes_gbk():
    """⚠️ 这个端点返回 GBK——按 UTF-8 解会得到乱码（实测 `����ʡ`）。"""
    payload = {"ip": "106.41.245.87", "pro": "吉林省", "city": "长春市", "err": ""}
    raw = json.dumps(payload, ensure_ascii=False).encode("gbk")

    info = ns._parse_pconline(raw)

    assert info is not None
    assert info.region == "吉林省"
    assert info.city == "长春市"
    assert info.in_china is True, "pconline 不返回国家字段，但我们知道是国内服务"
    assert info.source == "pconline"


def test_parse_pconline_rejects_error():
    assert ns._parse_pconline(json.dumps({"err": "1"}).encode("gbk")) is None
    assert ns._parse_pconline(b"not json") is None


def test_location_in_china_is_none_when_unknown():
    """查不到时必须返回 None，不能猜 False——「未知」和「不在中国」是两回事。"""
    assert ns.LocationInfo(ok=False).in_china is None
    assert ns.LocationInfo(ok=True, country="").in_china is None


def test_location_in_china_false_for_foreign():
    assert ns.LocationInfo(ok=True, country="新加坡").in_china is False


def test_region_text_skips_empty_parts():
    info = ns.LocationInfo(ok=True, country="中国", region="吉林", city="", isp="电信")
    assert info.region_text == "中国 · 吉林 · 电信"


# ---------------------------------------------------------------- 降级


def test_fetch_location_falls_back_to_secondary(monkeypatch):
    """主源失败必须自动切备源，而不是直接放弃。"""
    monkeypatch.setattr(ns, "_http_get_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("主源挂了")))

    class _Resp:
        def read(self): return json.dumps(
            {"ip": "1.2.3.4", "pro": "广东省", "city": "深圳", "err": ""},
            ensure_ascii=False).encode("gbk")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(ns.urllib.request, "urlopen", lambda *a, **k: _Resp())

    info = ns.fetch_ip_location()

    assert info.ok is True
    assert info.source == "pconline"
    assert info.region == "广东省"


def test_fetch_location_returns_not_ok_when_all_sources_fail(monkeypatch):
    monkeypatch.setattr(ns, "_http_get_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("挂了")))
    monkeypatch.setattr(ns.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("也挂了")))

    info = ns.fetch_ip_location()

    assert info.ok is False
    assert info.ip == ""
    assert info.in_china is None


# ---------------------------------------------------------------- 连通性


def test_check_connectivity_reports_per_target(monkeypatch):
    class _Resp:
        def read(self, _n=None): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "google" in url:
            return _Resp()
        raise OSError("unreachable")

    monkeypatch.setattr(ns.urllib.request, "urlopen", fake_urlopen)

    result = ns.check_connectivity()

    assert result.checked is True
    assert result.reachable is True, "任一目标可达即算可访问"
    by_name = {name: ok for name, ok, _ in result.targets}
    assert by_name["Google"] is True
    assert by_name["GitHub"] is False
    assert result.summary == "可访问"


def test_check_connectivity_all_failed(monkeypatch):
    monkeypatch.setattr(ns.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("全挂")))

    result = ns.check_connectivity()

    assert result.reachable is False
    assert result.summary == "不可访问"


def test_connectivity_result_before_check():
    """未探测与探测失败要能区分——否则弹窗会在检测中显示「不可访问」。"""
    result = ns.ConnectivityResult()
    assert result.checked is False
    assert result.summary == "检测中…"
