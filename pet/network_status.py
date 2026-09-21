# -*- coding: utf-8 -*-
"""网络状态探测：本机 IP、延迟、网速、IP 属地。

给两个消费方用：

- **灵动岛**（每拍读）：延迟与瞬时网速，要求**开销极低**且**不污染 UI 线程**；
- **菜单弹窗**（按需）：公网 IP、属地是否在中国、能否连外网。

## 为什么探测要分层

| 数据 | 方式 | 流量成本 |
|---|---|---|
| 本机 IP | UDP ``connect``（内核选路由，**不发包**） | **0** |
| 延迟 | UDP 往返（自己发包 + 收应答） | 每次约 166 字节 |
| 网速 | 读网卡字节计数器 | **0** |
| 属地 / 外网 | HTTP 请求 | 按需，约 1 KB |

**网速是「搭便车」的**——数据本来就在传，读计数器即可。所以它比延迟便宜得多，
即使高频刷新也不产生流量。

## 几条实测教训（写进来免得后人踩）

1. **ICMP 不可用**：Windows 上创建 ICMP raw socket **需要管理员权限**，
   桌宠以普通用户运行会被拒。所以延迟必须走 UDP 往返（这也是游戏的做法）。
2. **UDP ``connect`` 不会卡住**：目标不可达时内核立刻返回 ``ENETUNREACH``，
   实测 **0 ms**，不必担心等超时。
3. **``urllib`` 的 ``timeout`` 是「每步」而非总计**：实测设 ``timeout=2``
   仍跑了 4021 ms（DNS / TCP 连接 / 读取各自计时）。所以联网类调用**一律异步**。
4. **直连解析境外域名会拿到投毒 IP**（实测 ``www.google.com`` → ``185.45.5.35``），
   连它必然超时。判断连通性时不要被这一步误导。
5. **网卡名是本地化的**（本机是中文 ``WLAN`` / ``本地连接* 1``），
   不能按名字过滤，只能按「是否回环/伪接口」排除。
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# 探测目标：国内公共 DNS，稳定且延迟低（本机实测 36~56 ms）。
# 用 UDP 53 端口是因为它支持「发一个小包必得一个小包」的语义，正好测往返。
PROBE_HOST = "223.5.5.5"
PROBE_PORT = 53

# 单次探测的超时上限。UDP 正常时几十毫秒返回；这里留足余量应对网络抖动。
PROBE_TIMEOUT = 1.5

# 延迟分档阈值（毫秒）。对齐常见游戏的三档配色习惯。
LATENCY_GOOD_MS = 80.0
LATENCY_FAIR_MS = 200.0

# EMA 平滑系数：显示值 = 旧值 * (1-α) + 新测 * α。
# 原始 RTT 会跳（这秒 42 ms 下秒 189 ms），平滑后观感接近游戏 UI。
EMA_ALPHA = 0.2

# 属地查询：主源 + 备源。直连可用性实测见模块文档。
# 主源 309 ms / 原生中文；备源 268 ms / 但返回 GBK 编码，必须转码。
LOCATION_PRIMARY = "https://myip.ipip.net/json"
LOCATION_FALLBACK = "https://whois.pconline.com.cn/ipJson.jsp?json=true"

# 浏览器 UA：项目 chat/providers.py 记录过，默认 UA 会被识别为脚本机器人。
_BROWSER_UA = "Mozilla/5.0"


# ---------------------------------------------------------------- 数据结构


@dataclass
class LatencySample:
    """一次延迟探测的结果。"""

    ok: bool = False                 # 是否成功拿到应答
    rtt_ms: float | None = None      # 往返延迟（毫秒）
    error: str = ""                  # 失败原因（供排查，不直接给用户看）

    @property
    def display(self) -> str:
        """给灵动岛用的短文本。"""
        if not self.ok or self.rtt_ms is None:
            return "离线"
        return format_latency(self.rtt_ms)


@dataclass
class Throughput:
    """一段区间内的平均网速（字节/秒）。"""

    down_bps: float = 0.0
    up_bps: float = 0.0
    total_recv: int = 0              # 累计收（字节）
    total_sent: int = 0              # 累计发（字节）


@dataclass
class LocationInfo:
    """IP 属地查询结果。"""

    ok: bool = False
    ip: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    isp: str = ""
    source: str = ""                 # 哪个源给的（排查用）
    error: str = ""

    @property
    def in_china(self) -> bool | None:
        """属地是否在中国。无法判定时返回 ``None``（不是猜 False）。"""
        if not self.ok or not self.country:
            return None
        return "中国" in self.country

    @property
    def region_text(self) -> str:
        """拼成「中国 · 吉林 · 电信」这样的展示串，自动跳过空段。"""
        parts = [p for p in (self.country, self.region, self.city, self.isp) if p]
        return " · ".join(parts)


@dataclass
class ConnectivityResult:
    """外网连通性探测结果。"""

    checked: bool = False            # 是否已探测过（未探测 vs 探测失败要区分）
    reachable: bool = False
    targets: list[tuple[str, bool, float]] = field(default_factory=list)
    # 每个元素：(名称, 是否可达, 耗时毫秒)

    @property
    def summary(self) -> str:
        if not self.checked:
            return "检测中…"
        return "可访问" if self.reachable else "不可访问"


# ---------------------------------------------------------------- 格式化


def format_latency(rtt_ms: float) -> str:
    """延迟文本。小于 1 秒的按毫秒显示，超过则按秒（避免出现 4 位数字）。"""
    value = max(0.0, float(rtt_ms))
    if value < 1000:
        return f"{value:.0f}ms"
    return f"{value / 1000:.1f}s"


def latency_tier(rtt_ms: float) -> str:
    """延迟分档：``good`` / ``fair`` / ``poor``。供灵动岛配色用。"""
    value = max(0.0, float(rtt_ms))
    if value < LATENCY_GOOD_MS:
        return "good"
    if value < LATENCY_FAIR_MS:
        return "fair"
    return "poor"


def format_speed(bytes_per_sec: float) -> str:
    """把字节/秒格式化成 ``2.3 MB/s`` 这类短文本。

    小于 1 KB/s 一律显示 ``0 KB/s``——网络空闲时抖动出来的几字节没有意义，
    显示成 ``0.02 KB/s`` 只会让人以为在传东西。
    """
    value = max(0.0, float(bytes_per_sec))
    if value < 1024:
        return "0 KB/s"
    if value < 1024 ** 2:
        return f"{value / 1024:.0f} KB/s"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB/s"
    return f"{value / 1024 ** 3:.1f} GB/s"


def format_bytes(total: int) -> str:
    """累计流量的可读形式（给弹窗用）。"""
    value = max(0, int(total))
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB"
    return f"{value / 1024 ** 3:.2f} GB"


def ema_smooth(previous: float | None, current: float, *, alpha: float = EMA_ALPHA) -> float:
    """指数移动平均。``previous`` 为 ``None`` 时直接采用 ``current``（首帧不衰减）。"""
    if previous is None:
        return float(current)
    a = max(0.0, min(1.0, float(alpha)))
    return previous * (1.0 - a) + float(current) * a


# ---------------------------------------------------------------- 本机 IP


def probe_local_ip(*, timeout: float = 1.0) -> str:
    """拿本机在当前路由下的出口 IP（如 ``192.168.43.24``）。

    原理：UDP ``connect`` **不实际发包**，只是让内核按路由表挑一个出口网卡并
    绑定本地地址，因此**零流量、零延迟开销**（实测 0.05~7.75 ms）。

    失败返回空串——调用方据此显示「未知」，不要编一个 ``127.0.0.1`` 出来。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(max(0.1, float(timeout)))
        sock.connect((PROBE_HOST, PROBE_PORT))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------- 延迟


def _dns_query_packet(transaction_id: int = 0x1234) -> bytes:
    """构造一个最小的 DNS 查询包（问根域，载荷仅 17 字节）。

    为什么不问 ``google.com``：实测载荷 28 字节、**响应 124 字节**；
    问根域载荷 17 字节、响应 93 字节。在 1 秒一跳的高频场景下，
    这点差别会累积成可观的流量（见 README 的成本表）。
    """
    header = struct.pack(">HHHHHH", transaction_id & 0xFFFF, 0x0100, 1, 0, 0, 0)
    # QNAME 单个 0 字节 = 根域；QTYPE/QCLASS 各 1
    return header + b"\x00" + struct.pack(">HH", 1, 1)


def measure_latency(
    host: str = PROBE_HOST,
    port: int = PROBE_PORT,
    *,
    timeout: float = PROBE_TIMEOUT,
) -> LatencySample:
    """测一次往返延迟（RTT）。

    走 UDP 往返而不是 ICMP——Windows 上 ICMP raw socket 需要管理员权限，
    桌宠以普通用户运行拿不到。

    绝不抛异常：失败时返回 ``ok=False`` 的样本，由调用方决定怎么显示。
    """
    sock = None
    started = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(max(0.1, float(timeout)))
        sock.sendto(_dns_query_packet(), (host, int(port)))
        sock.recvfrom(512)
        elapsed = (time.perf_counter() - started) * 1000.0
        return LatencySample(ok=True, rtt_ms=elapsed)
    except socket.timeout:
        return LatencySample(ok=False, error="超时")
    except OSError as exc:
        return LatencySample(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------- 网速


def _loopback_names() -> set[str]:
    """回环/伪接口名——统计网速时要排除，否则本地传输会被算成上网流量。"""
    names = {"loopback pseudo-interface 1", "teredo tunneling pseudo-interface"}
    if sys.platform == "win32":
        names |= {"loopback", "本地连接* 1", "本地连接* 2", "蓝牙网络连接"}
    return names


def read_throughput() -> Throughput:
    """读**累计**收发字节数。

    调用方保存上一次的值，用差值除以时间得到瞬时速度——这样「瞬时速度」
    与「累计流量」用的是同一份数据，不需要额外读取。

    读不到时返回全零（网络栈异常不该让桌宠崩）。
    """
    try:
        import psutil
    except ImportError:
        return Throughput()
    try:
        skip = _loopback_names()
        down = up = 0
        for name, counters in psutil.net_io_counters(pernic=True).items():
            if str(name).strip().lower() in skip:
                continue
            down += int(counters.bytes_recv)
            up += int(counters.bytes_sent)
        return Throughput(total_recv=down, total_sent=up)
    except Exception:
        log.debug("读取网卡计数器失败", exc_info=True)
        return Throughput()


def speed_from_delta(previous: Throughput, current: Throughput, elapsed: float) -> Throughput:
    """由两次采样算瞬时速度。``elapsed`` 为两次采样间隔（秒）。

    间隔过小（<50 ms）或计数器回绕时返回零速度，避免算出天文数字。
    """
    if elapsed < 0.05:
        return Throughput(total_recv=current.total_recv, total_sent=current.total_sent)
    d_down = current.total_recv - previous.total_recv
    d_up = current.total_sent - previous.total_sent
    if d_down < 0 or d_up < 0:      # 计数器回绕（网卡重启）：本拍不报速度
        d_down = d_up = 0
    return Throughput(
        down_bps=d_down / elapsed,
        up_bps=d_up / elapsed,
        total_recv=current.total_recv,
        total_sent=current.total_sent,
    )


# ---------------------------------------------------------------- 属地


def _http_get_json(url: str, *, timeout: float = 5.0) -> Any:
    """GET 一个 JSON 端点。**必须异步调用**（实测最坏 4 秒）。

    ``urllib`` 默认会读取系统代理设置——这正合需求：要显示的就是
    「按当前网络环境实际看到的属地」。
    """
    request = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _parse_ipip(payload: Any) -> LocationInfo | None:
    """解析 ``myip.ipip.net`` 的响应。

    形如 ``{"ret":"ok","data":{"ip":"...","location":["中国","吉林","长春","","电信"]}}``
    """
    if not isinstance(payload, dict) or payload.get("ret") != "ok":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    location = data.get("location")
    if not isinstance(location, list):
        location = []
    padded = (list(location) + [""] * 5)[:5]
    return LocationInfo(
        ok=True,
        ip=str(data.get("ip") or ""),
        country=str(padded[0] or ""),
        region=str(padded[1] or ""),
        city=str(padded[2] or ""),
        isp=str(padded[4] or ""),
        source="ipip.net",
    )


def _parse_pconline(raw: bytes) -> LocationInfo | None:
    """解析 ``whois.pconline.com.cn`` 的响应。

    ⚠️ 这个端点返回 **GBK 编码**（实测直接按 UTF-8 读是乱码 ``����ʡ``），
    所以不能走通用的 ``_http_get_json``。
    """
    text = raw.decode("gbk", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("err"):
        return None
    return LocationInfo(
        ok=True,
        ip=str(payload.get("ip") or ""),
        country="中国" if payload.get("pro") else "",
        region=str(payload.get("pro") or ""),
        city=str(payload.get("city") or ""),
        isp="",
        source="pconline",
    )


def fetch_ip_location(*, timeout: float = 5.0) -> LocationInfo:
    """查公网 IP 与属地。主源失败自动切备源；都失败返回 ``ok=False``。

    **必须在线程里调用**——实测耗时 300 ms 起，直连境外源时可达数秒。
    """
    # 主源
    try:
        parsed = _parse_ipip(_http_get_json(LOCATION_PRIMARY, timeout=timeout))
        if parsed is not None:
            return parsed
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("ipip.net 查询失败：%s", exc)

    # 备源（GBK）
    try:
        request = urllib.request.Request(
            LOCATION_FALLBACK, headers={"User-Agent": _BROWSER_UA}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        parsed = _parse_pconline(raw)
        if parsed is not None:
            return parsed
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("pconline 查询失败：%s", exc)

    return LocationInfo(ok=False, error="属地查询失败")


# ---------------------------------------------------------------- 外网连通性

# 探测目标：(显示名, URL)。国内 + 国外各一，用来区分"没网"与"被墙"。
CONNECTIVITY_TARGETS: tuple[tuple[str, str], ...] = (
    ("Google", "https://www.google.com/generate_204"),
    ("GitHub", "https://github.com"),
)


def check_connectivity(
    targets: tuple[tuple[str, str], ...] = CONNECTIVITY_TARGETS,
    *,
    timeout: float = 4.0,
) -> ConnectivityResult:
    """逐个探测外网目标是否可达。

    ⚠️ **实测最坏 3~6 秒**（``urllib`` 的 ``timeout`` 是每步而非总计，
    且 DNS 被污染时还要先耗尽解析超时）。**必须在线程里调用**。

    按当前系统代理设置探测——反映「我现在实际能不能上外网」。
    """
    results: list[tuple[str, bool, float]] = []
    any_ok = False
    for name, url in targets:
        started = time.perf_counter()
        ok = False
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read(1)
            ok = True
        except (urllib.error.URLError, OSError, ValueError):
            ok = False
        elapsed = (time.perf_counter() - started) * 1000.0
        results.append((name, ok, elapsed))
        any_ok = any_ok or ok
    return ConnectivityResult(checked=True, reachable=any_ok, targets=results)


__all__ = [
    "CONNECTIVITY_TARGETS",
    "ConnectivityResult",
    "LatencySample",
    "LocationInfo",
    "Throughput",
    "check_connectivity",
    "ema_smooth",
    "fetch_ip_location",
    "format_bytes",
    "format_latency",
    "format_speed",
    "latency_tier",
    "measure_latency",
    "probe_local_ip",
    "read_throughput",
    "speed_from_delta",
]
