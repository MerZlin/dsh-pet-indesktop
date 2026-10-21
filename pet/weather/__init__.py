# -*- coding: utf-8 -*-
"""天气取数：国内源优先 + 国外源兜底（纯逻辑，不依赖 Qt）。

**两个源，按顺序试，命中即返回**

1. **中国天气网**（`toy1.weather.com.cn` 城市检索 → `d1.weather.com.cn/sk_2d/<code>.html`
   实况）——国内源，一个接口就带齐温度 / 天气状况 / 风向风力 / 湿度 / 观测时间，
   城市名也是中文语境（实测「鹿城」直接命中 `101210710`）。
2. **open-meteo**（geocoding → forecast）——国外源兜底，走 WMO 天气码，中文文案
   由本模块的码表给出。

两源都失败抛 :class:`WeatherError`，消息里**点名两个源各自的失败原因**——气泡
照实说「天气获取失败」，绝不静默（见 ``docs/NETWORK-PROXY-AND-VPN-2026-09-22.md``
的教训：静默降级会让"功能坏了"变成唯一可见的现象）。

**网络出口：国内源直连，国外源跟随系统代理。** 决策依据见
:func:`_default_http` 与 ``docs/NETWORK-PROXY-AND-VPN-2026-09-22.md``：本机实测
（各 3 次中位）中国天气网直连 0.05~0.09s、走系统代理 0.57~0.63s（慢 7~11 倍，
且第一次点击要连发两个请求），与歌词取词同一族问题；而 open-meteo 是海外端点，
直连/代理实测相当（1.14s vs 1.43s），对"只有代理能出网"的用户必须保留代理这条路。
分流而不是全局绕代理，正是该文档推荐的口径。

**超时与预算（硬上限口径）**：单请求 socket 超时 ``HTTP_TIMEOUT``；整次取数另有
``TOTAL_BUDGET_SECONDS`` 总预算，每个请求的超时取「剩余预算」与单请求超时的较小
值，且**读响应体时每块都要复查截止时间**（:func:`read_bounded`，同时带 2MB 体积
上限）——只剩"正在阻塞的那一次 recv"可能多花一个 socket 超时，不会无限等。三个
源各失败一次的最坏墙钟有界。

**城市名：存储层幂等、查询层容错（终审 M1）。** 存储（``normalize_city_name``）
只做宽窄字符/空白/长度这类可逆清洗，**绝不剥后缀**——剥离不幂等，每次保存别的
设置都会再剥一层（``景德镇市`` → ``景德镇`` → ``景德``），真名（新乡、景德镇）
也会被裁掉。后缀容错只在查询侧（``city_name_variants`` + ``pick_candidate`` 的
``exact_only``）：先按原名查，查不到再试剥后缀的变体，**且变体必须精确命中**。

**城市身份：同名城市靠地区码区分（终审 m1）。** 添加城市时把命中候选的地区码
存进 ``weather_city_codes``，取数时按码直取（一跳，不进检索接口）；没有码才回落到
按名字检索。省市区同名（朝阳 北京 / 辽宁）因此"选哪项中哪项"。

**测试边界**：HTTP 出口通过 ``http=`` 注入（签名 ``(url, headers, *, use_proxy,
timeout) -> str``），单测不真发外网请求。
"""

from __future__ import annotations

import json
import logging
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

#: 单次 HTTP 请求超时（秒）。天气是"点一下看结果"的交互，宁可失败也不要挂住。
HTTP_TIMEOUT = 6.0
#: 一次取数的总预算（秒）：源 A 最多 2 个请求、源 B 最多 2 个请求，最坏 4×6=24s，
#: 用户等不了；超预算后直接按失败冒泡。
TOTAL_BUDGET_SECONDS = 15.0
#: 城市上限（issue #209 规格：12 个）。
MAX_CITIES = 12
#: 单个城市名的长度上限（防手改配置塞超长串撑爆菜单）。
MAX_CITY_NAME = 24

SOURCE_CN = "中国天气网"
SOURCE_OPEN_METEO = "open-meteo"

CITY_LIMIT_TEXT = f"最多只能有 {MAX_CITIES} 个城市"
CITY_EXISTS_TEXT = "这个城市已经在列表里了"
CITY_EMPTY_TEXT = "城市名不能为空"
NO_CITY_TEXT = "还没选天气城市：右键菜单 → 天气城市 → 添加城市…"

CN_SEARCH_URL = "http://toy1.weather.com.cn/search?cityname={query}"
CN_CURRENT_URL = "http://d1.weather.com.cn/sk_2d/{code}.html"
CN_REFERER = "http://www.weather.com.cn/"
OM_GEOCODE_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"
    "?name={query}&count=1&language=zh&format=json"
)
OM_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,"
    "relative_humidity_2m&timezone=auto"
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 行政区/地名后缀（**只用于查询回退**，见 strip_city_suffix）：源 A 的城市检索对
# 带后缀的名字有时搜不到（2026-10-07 实测「鹿城区」返回空数组，而「鹿城」命中），
# 所以查询会把「原名」与「剥后缀变体」都试一遍。**绝不回写存储**——终审 M1：
# 剥离不是幂等的（景德镇市 → 景德镇 → 景德），每次整表归一化都会继续侵蚀城市名，
# 「新乡」「景德镇」这种真名也会被当成后缀裁掉。
# 顺序 = 从长到短，避免「自治区」被「区」先吃掉。
_CITY_SUFFIXES = (
    "维吾尔自治区", "壮族自治区", "回族自治区", "内蒙古自治区", "特别行政区",
    "自治区", "自治州", "自治县", "开发区", "高新区", "新区", "地区", "街道",
    "省", "市", "区", "县", "镇", "乡", "旗", "盟",
)

# 歧义标签的分隔符（同一个搜索结果里出现同名城市时才用，如「朝阳 · 辽宁」）。
# 刻意用中点而不是括号：NFKC 会把全角括号折成半角，标签与地区码表的键就会对不上号。
CITY_REGION_SEPARATOR = " · "
_BASE_NAME_SEPARATORS = (" · ", "·", "（", "(")

# WMO 天气码 → 中文（open-meteo 的 weather_code，即 WMO 4677 子集）。
_WMO_TEXT = {
    0: "晴", 1: "多云", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "中阵雨", 82: "大阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}

# Beaufort 风级上界（km/h）：索引 = 级数。0→0级 … 12→12级。
_BEAUFORT_KMH = (1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118)


class WeatherError(RuntimeError):
    """取数失败（单源或两源都失败）。``failures`` = ((源名, 原因), ...)。"""

    def __init__(self, message: str, failures: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__(message)
        self.failures = failures


@dataclass(frozen=True)
class CityCandidate:
    """源 A 城市检索的一条候选（``code`` 是天气接口要的地区码）。"""

    code: str
    name: str
    region: str = ""


@dataclass(frozen=True)
class WeatherReading:
    """一次取数的结果（两个源统一成同一形状）。"""

    city: str
    condition: str
    temperature: str
    wind: str = ""
    humidity: str = ""
    source: str = ""
    observed_at: str = ""
    detail: str = ""

    def bubble_text(self) -> str:
        """气泡文案（单行：气泡渲染会把换行折成空格，见 speech_bubble_text）。"""
        head = " ".join(part for part in (self.city, self.condition, self.temperature) if part)
        extras = [part for part in (self.wind, f"湿度 {self.humidity}" if self.humidity else "") if part]
        if extras:
            head += "，" + "，".join(extras)
        return f"{head}（{self.source}）" if self.source else head


# ---------------------------------------------------------------- 城市名与列表
def normalize_city_name(raw) -> str:
    """**存储层**城市名归一化：全角转半角、去空白、长度截断。**不剥后缀**。

    必须是幂等的（``f(f(x)) == f(x)``）且不得缩短真实城市名——终审 M1 的教训：
    此前这里顺手剥行政区后缀，于是
    ① 保存任何其它设置都会触发整表归一化，城市名被一次次继续剥
    （``景德镇市`` → ``景德镇`` → ``景德``）；
    ② 「新乡」「景德镇」这类**真名**被当成「乡」「镇」后缀裁掉。
    取数需要的后缀容错改在查询侧（:func:`city_name_variants`），只试不改。
    """
    name = unicodedata.normalize("NFKC", str(raw or "")).strip()
    return name[:MAX_CITY_NAME]


def strip_city_suffix(raw) -> str:
    """剥掉一个行政区后缀（「鹿城区」→「鹿城」）；**只用于查询回退**。

    剥成空串时保留原名（只有「市」「区」这种输入会走到）。
    """
    name = normalize_city_name(raw)
    for suffix in _CITY_SUFFIXES:
        if len(name) > len(suffix) and name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def city_name_variants(raw) -> tuple[str, ...]:
    """查询用的候选名字：原名在前，剥后缀变体在后（源 A 检索的容错口径）。

    变体只是**候选**，不改写任何存储值；回退变体在检索里要求精确命中
    （见 :func:`pick_candidate` 的 ``exact_only``），避免「新乡」搜不到时
    用「新」把别的城市顶上来。
    """
    name = normalize_city_name(raw)
    if not name:
        return ()
    stripped = strip_city_suffix(name)
    return (name,) if stripped == name else (name, stripped)


def base_city_name(label) -> str:
    """显示标签 → 查询用基础名：去掉歧义后缀（「朝阳 · 辽宁」→「朝阳」）。

    分隔符兼容历史/手改写法（中点与括号两种）。
    """
    name = normalize_city_name(label)
    for separator in _BASE_NAME_SEPARATORS:
        head = name.split(separator, 1)[0].strip()
        if head and head != name:
            return head
    return name


def city_label(name, region: str = "", *, ambiguous: bool = False) -> str:
    """城市显示标签：只有**同名歧义**时才附地区（「朝阳 · 辽宁」），否则就是名字本身。

    返回值过一遍存储归一化，保证它既能当列表项、又能当地区码表的键（见
    ``CITY_REGION_SEPARATOR`` 的注释）。
    """
    base = normalize_city_name(name)
    area = normalize_city_name(region)
    if not (ambiguous and base and area):
        return base
    if area == base or base in area or area in base:
        return base
    return normalize_city_name(f"{base}{CITY_REGION_SEPARATOR}{area}")


def normalize_city_codes(raw) -> dict[str, str]:
    """地区码表清洗：``{显示标签: 地区码}``，只留成对的非空字符串（键也归一化）。"""
    if not isinstance(raw, dict):
        return {}
    codes: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        label = normalize_city_name(key)
        code = value.strip()
        if label and code:
            codes[label] = code
    return codes


def city_code_for(config, city) -> str:
    """当前城市已知的地区码（没有就返回空串，取数回落到城市检索）。

    键按显示标签存（多数情况就是城市名本身），标签与基础名两种键都查一遍。
    """
    if config is None:
        return ""
    getter = getattr(config, "get", None)
    if callable(getter):
        raw = getter("weather_city_codes", {})
    elif isinstance(config, dict):
        raw = config.get("weather_city_codes", {})
    else:
        return ""
    codes = normalize_city_codes(raw)
    name = normalize_city_name(city)
    return codes.get(name) or codes.get(base_city_name(name)) or ""


def same_city(left, right) -> bool:
    """两个写法是否指同一座城市（把后缀变体算进来：「鹿城」≡「鹿城区」）。"""
    return bool(set(city_name_variants(left)) & set(city_name_variants(right)))


def normalize_city_list(raw) -> list[str]:
    """城市列表清洗：逐项存储层归一化（幂等）、去空、去重、上限 12。

    **保留用户输入/选择的原名**（不剥后缀）——列表里存什么，菜单与设置页就显示
    什么；取数需要的后缀容错在查询侧做（``city_name_variants``）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    cities: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = normalize_city_name(item)
        if name and not any(same_city(name, city) for city in cities):
            cities.append(name)
        if len(cities) >= MAX_CITIES:
            break
    return cities


def current_city(config) -> str:
    """当前城市：``weather_city`` 优先，为空退回列表首项（列表非空时）。"""
    getter = getattr(config, "get", None)
    if callable(getter):
        selected = getter("weather_city", "") or ""
        listed = getter("weather_city_list", [])
    elif isinstance(config, dict):
        selected = config.get("weather_city", "") or ""
        listed = config.get("weather_city_list", [])
    else:
        return ""
    name = normalize_city_name(selected)
    if name:
        return name
    cities = normalize_city_list(listed)
    return cities[0] if cities else ""


def add_city(cities, name) -> tuple[list[str], str]:
    """往列表加城市，返回 ``(新列表, 错误文案)``；错误时列表原样返回。

    存储的是用户给的原名（不剥后缀）；「鹿城」与「鹿城区」视为同一座城市，不会
    同时进列表（:func:`same_city`）。
    """
    current = normalize_city_list(cities)
    candidate = normalize_city_name(name)
    if not candidate:
        return current, CITY_EMPTY_TEXT
    if any(same_city(candidate, city) for city in current):
        return current, CITY_EXISTS_TEXT
    if len(current) >= MAX_CITIES:
        return current, CITY_LIMIT_TEXT
    return current + [candidate], ""


def remove_city(cities, name, current: str = "") -> tuple[list[str], str]:
    """删城市，返回 ``(新列表, 新的当前城市)``。

    删掉的正好是当前城市时，当前切到剩下的第一个；列表空了则当前城市清空
    （而不是留一个不在列表里的悬空值）。匹配用 :func:`same_city`，所以删「鹿城」
    也能删掉存量里的「鹿城区」。
    """
    listed = normalize_city_list(cities)
    if not any(same_city(city, name) for city in listed):
        return listed, current or ""
    remaining = [city for city in listed if not same_city(city, name)]
    if not same_city(current, name):
        return remaining, current or ""
    return remaining, (remaining[0] if remaining else "")


# ---------------------------------------------------------------- 码表
def wmo_text(code) -> str:
    """WMO 天气码 → 中文；未知码返回「未知」（不猜、不空着）。"""
    try:
        return _WMO_TEXT.get(int(code), "未知")
    except (TypeError, ValueError):
        return "未知"


def wind_direction_text(degrees) -> str:
    """风向角度 → 八个方位；无角度返回空串。"""
    try:
        value = float(degrees) % 360.0
    except (TypeError, ValueError):
        return ""
    names = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    return names[int((value + 22.5) // 45) % 8]


def beaufort_text(kmh) -> str:
    """风速 km/h → Beaufort 风级中文（「4级」）；无速度返回空串。"""
    try:
        value = max(0.0, float(kmh))
    except (TypeError, ValueError):
        return ""
    force = 0
    for index, upper in enumerate(_BEAUFORT_KMH):
        if value < upper:
            force = index
            break
    else:
        force = 12
    return f"{force}级"


def describe_error(exc: BaseException) -> str:
    """异常 → 一句中文原因（气泡/对话框里给用户看的那一种）。"""
    if isinstance(exc, WeatherError):
        return str(exc)
    if isinstance(exc, (TimeoutError,)) or "timed out" in str(exc).lower():
        return "请求超时"
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", "") or exc)
        return "请求超时" if "timed out" in reason.lower() else f"网络连接失败：{reason[:60]}"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "返回数据无效"
    return f"{type(exc).__name__}: {str(exc)[:60]}"


# ---------------------------------------------------------------- 响应解析
def parse_search_payload(text: str) -> list[CityCandidate]:
    """解析源 A 城市检索的响应 ``([{"ref":"code~...~名称~..."}])``。

    字段位（2026-10-07 实测）：``code~省~名称~拼音~城市~城市拼音~区号~邮编~缩写~省份``。
    末尾带 ``A`` 的地区码是景点（如 ``10102020001A``），会盖掉同名的真城市，剔除。
    """
    raw = str(text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(raw[start: end + 1])
    except (ValueError, TypeError):
        return []
    candidates: list[CityCandidate] = []
    for item in items if isinstance(items, list) else ():
        ref = str(item.get("ref") or "") if isinstance(item, dict) else ""
        parts = ref.split("~")
        if len(parts) < 3:
            continue
        code = parts[0].strip()
        name = parts[2].strip()
        if not code or not name or code.endswith("A"):
            continue
        region = parts[-1].strip()
        candidates.append(CityCandidate(code=code, name=name, region=region))
    return candidates


def _match_rank(candidate: CityCandidate, query: str, *, exact_only: bool = False) -> int:
    """候选与查询的匹配档：0 精确 > 1 前缀 > 2 包含 > 3 不匹配。

    两侧都把后缀变体算进来：「鹿城区」这种查询要能精确命中「鹿城」候选。
    ``exact_only``（剥后缀的回退查询用）只认候选的**主名**——回退查询本身就是
    剥过的短名（「新」），再允许拿候选的剥后变体去比会绕回原问题（新乡的变体
    也是「新」）。
    """
    targets = city_name_variants(query) or (query,)
    primary = [normalize_city_name(candidate.name)]
    primary.extend(normalize_city_name(part) for part in candidate.name.split())
    if exact_only:
        return 0 if any(name == target for name in primary for target in targets) else 3
    names = list(primary)
    for part in candidate.name.split():
        names.extend(city_name_variants(part))
    rank = 3
    for name in names:
        for target in targets:
            if name == target:
                return 0
            if name.startswith(target):
                rank = min(rank, 1)
            elif target in name:
                rank = min(rank, 2)
    return rank


def pick_candidate(candidates, query, *, exact_only: bool = False) -> CityCandidate | None:
    """从候选里挑最匹配的一条：精确 > 前缀 > 包含；同档保持接口原顺序。

    ``exact_only=True`` 用于**剥后缀的回退变体**：那种查询短而无特征（「新」），
    宽松匹配会把「新余」当成「新乡」。
    """
    target = normalize_city_name(query)
    if not target:
        return None
    best: tuple[int, int, CityCandidate] | None = None
    for index, candidate in enumerate(candidates):
        rank = _match_rank(candidate, target, exact_only=exact_only)
        if rank >= 3:
            continue
        key = (rank, index)
        if best is None or key < (best[0], best[1]):
            best = (rank, index, candidate)
    return best[2] if best is not None else None


def parse_cn_reading(text: str, fallback_city: str = "") -> WeatherReading:
    """解析源 A 实况响应（``var dataSK={...}``）。缺温度视为不可用。"""
    raw = str(text or "")
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise WeatherError("返回数据无效")
    try:
        data = json.loads(raw[start: end + 1])
    except (ValueError, TypeError) as exc:
        raise WeatherError(f"返回数据无效：{describe_error(exc)}") from exc
    if not isinstance(data, dict):
        raise WeatherError("返回数据无效")
    temperature = str(data.get("temp") or "").strip()
    if not temperature:
        raise WeatherError("返回数据里没有温度")
    try:
        temperature = f"{float(temperature):.1f}℃"
    except (TypeError, ValueError) as exc:
        raise WeatherError("返回数据里没有温度") from exc
    wind_parts = [str(data.get("WD") or "").strip(), str(data.get("WS") or "").strip()]
    return WeatherReading(
        city=str(data.get("cityname") or "").strip() or str(fallback_city or "").strip(),
        condition=str(data.get("weather") or "").strip(),
        temperature=temperature,
        wind=" ".join(part for part in wind_parts if part),
        humidity=str(data.get("SD") or "").strip(),
        source=SOURCE_CN,
        observed_at=str(data.get("time") or "").strip(),
        detail=str(data.get("date") or "").strip(),
    )


def parse_open_meteo_reading(payload, city: str = "") -> WeatherReading:
    """解析源 B 实况（geocoding 选的坐标 → forecast 的 ``current`` 块）。"""
    current = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(current, dict):
        raise WeatherError("返回数据里没有实况")
    temperature = current.get("temperature_2m")
    if temperature is None:
        raise WeatherError("返回数据里没有温度")
    try:
        temperature_text = f"{float(temperature):.1f}℃"
    except (TypeError, ValueError) as exc:
        raise WeatherError("返回数据里没有温度") from exc
    direction = wind_direction_text(current.get("wind_direction_10m"))
    force = beaufort_text(current.get("wind_speed_10m"))
    wind = f"{direction}风 {force}".strip() if direction else force
    humidity = current.get("relative_humidity_2m")
    return WeatherReading(
        city=str(city or "").strip(),
        condition=wmo_text(current.get("weather_code")),
        temperature=temperature_text,
        wind=wind,
        humidity=f"{int(round(float(humidity)))}%" if humidity is not None else "",
        source=SOURCE_OPEN_METEO,
        observed_at=str(current.get("time") or "").strip(),
        detail=str(payload.get("timezone") or "").strip(),
    )


# ---------------------------------------------------------------- HTTP 出口
HttpGetter = Callable[..., str]

#: 响应体体积上限：天气接口的响应都是几百字节到几十 KB，2MB 已经比正常值大两个
#: 数量级——纯粹是防御（被劫持/被塞大响应时不要把内存拖垮）。
MAX_BODY_BYTES = 2 * 1024 * 1024
#: 分块读取的块大小：块越小，截止时间的检查越密。
READ_CHUNK = 64 * 1024


def read_bounded(reader, *, max_bytes: int = MAX_BODY_BYTES, deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 chunk: int = READ_CHUNK) -> bytes:
    """分块读响应体：**体积上限 + 总截止时间**双闸（终审 m12）。

    为什么不能直接 ``response.read()``：① 没有体积上限，异常响应能把内存拖垮；
    ② 只有一个"发起请求前设好的 socket 超时"，服务端慢速持续吐字节时每次 recv
    都有数据到达，socket 超时永不触发，整体墙钟可以远超预算。这里每读一块前先查
    截止时间，超了就立刻放弃。

    残留上界（如实写在文档里）：正在阻塞的那一次 ``reader(chunk)`` 最多再等一个
    socket 超时（调用方已把它压到「剩余预算」），所以极端慢速滴流的墙钟最坏是
    「预算 + 单次 socket 超时」，不会无限等。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        if deadline is not None and clock() >= deadline:
            raise WeatherError("请求超时：读取响应体超过时间预算")
        data = reader(chunk)
        if not data:
            break
        total += len(data)
        if total > max_bytes:
            raise WeatherError(f"响应体过大（超过 {max_bytes // (1024 * 1024)}MB）")
        chunks.append(data)
    return b"".join(chunks)


def _default_http(url: str, headers: dict, *, use_proxy: bool, timeout: float) -> str:
    """真实 HTTP GET（出口分流 + 有界读取）。

    ``use_proxy=False`` 显式绕过系统代理（``ProxyHandler({})``，与
    ``pet/music_lyric.py::_build_opener`` 同一手法）；``True`` 走
    ``urllib.request.urlopen`` 的默认行为（自动读 Windows 注册表/环境变量的系统代理）。

    为什么国内源必须直连：2026-10-07 本机实测（系统代理 ``http://127.0.0.1:7890``）
    城市检索直连 0.09s / 走代理 0.63s，实况直连 0.05s / 走代理 0.57s（3 次中位，
    慢 7~11 倍）——第一次点击就要连发两个请求，走代理会把"点一下等结果"的用户
    体验直接拖到秒级，慢代理下更容易撞上超时，重演歌词取词那次的"功能像是坏了"。
    为什么国外源跟随代理：open-meteo 直连/代理实测相当（1.14s vs 1.43s、
    0.85s vs 1.02s），而"只有代理能出网"的用户（文档 §四 的已知缺口）需要这条路兜底。

    读取走 :func:`read_bounded`：``timeout`` 既当单次 socket 超时，也当整块响应的
    墙钟截止（调用方传进来的已是「剩余预算」），并带 2MB 体积上限。
    """
    request = urllib.request.Request(url, headers=headers)
    if use_proxy:
        opener = urllib.request.build_opener()
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        deadline = time.monotonic() + float(timeout)
        return read_bounded(response.read, deadline=deadline).decode("utf-8", "replace")


# ---------------------------------------------------------------- 取数客户端
class WeatherClient:
    """按「国内源 → 国外源」顺序取数；HTTP 出口与时钟可注入（测试用）。"""

    def __init__(self, *, http: HttpGetter | None = None, timeout: float = HTTP_TIMEOUT,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._http = http or _default_http
        self._timeout = float(timeout)
        self._clock = clock

    # ---- 出口辅助
    def _request_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self._timeout
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise WeatherError("请求超时：已超过本次取数的时间预算")
        return min(self._timeout, remaining)

    def _get(self, url: str, *, use_proxy: bool, deadline: float | None = None,
             referer: str = "") -> str:
        headers = {"User-Agent": _UA, "Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        return self._http(url, headers, use_proxy=use_proxy,
                          timeout=self._request_timeout(deadline))

    # ---- 源 A：中国天气网（直连）
    def search_cities(self, query: str, *, deadline: float | None = None
                      ) -> tuple[list[CityCandidate], str]:
        """城市检索（源 A 独有）：返回 ``(候选列表, 失败原因)``；失败不抛。

        添加城市的对话框要的是"搜不到就说搜不到"，而不是异常。名字按
        :func:`city_name_variants` 逐个试：**原名**宽松匹配，**剥后缀变体**要求
        精确命中（否则「新乡」搜不到时会用「新」把「新余」顶上来）。
        """
        for index, name in enumerate(city_name_variants(base_city_name(query))):
            url = CN_SEARCH_URL.format(query=urllib.parse.quote(name))
            try:
                payload = self._get(url, use_proxy=False, deadline=deadline,
                                    referer=CN_REFERER)
            except Exception as exc:  # noqa: BLE001 - 网络类异常统一转成原因文案
                reason = describe_error(exc)
                log.warning("天气城市检索失败 %s：%s", url, reason)
                return [], reason
            candidates = parse_search_payload(payload)
            picked = pick_candidate(candidates, name, exact_only=index > 0)
            if picked is not None:
                return [picked] + [c for c in candidates if c is not picked], ""
        return [], ""

    def _fetch_cn(self, city: str, deadline: float | None, code: str = "") -> WeatherReading:
        """源 A 取数：**已知地区码就直接按码取**（同名城市靠它区分），否则走检索。

        按码取失败时回落到城市检索——地区码表是"尽量精确"而不是硬约束（换版本、
        手改配置都可能对不上），宁可慢一步也不要因此整源失败。
        """
        if code:
            try:
                payload = self._get(CN_CURRENT_URL.format(code=code), use_proxy=False,
                                    deadline=deadline, referer=CN_REFERER)
                return parse_cn_reading(payload, fallback_city=base_city_name(city))
            except Exception as exc:  # noqa: BLE001 - 回落检索，原因记日志
                log.warning("按地区码取数失败 %s（%s）：%s，回退城市检索",
                            city, code, describe_error(exc))
        candidates, reason = self.search_cities(city, deadline=deadline)
        if reason:
            raise WeatherError(f"城市检索失败：{reason}")
        picked = pick_candidate(candidates, base_city_name(city)) or (
            candidates[0] if candidates else None
        )
        if picked is None:
            raise WeatherError("没有找到这个城市")
        url = CN_CURRENT_URL.format(code=picked.code)
        payload = self._get(url, use_proxy=False, deadline=deadline, referer=CN_REFERER)
        return parse_cn_reading(payload, fallback_city=picked.name)

    # ---- 源 B：open-meteo（跟随系统代理）
    def _fetch_open_meteo(self, city: str, deadline: float | None) -> WeatherReading:
        name = base_city_name(city)     # 歧义标签（「朝阳 · 辽宁」）不能整串当地名查
        geocode_url = OM_GEOCODE_URL.format(query=urllib.parse.quote(name))
        payload = json.loads(self._get(geocode_url, use_proxy=True, deadline=deadline))
        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            raise WeatherError("没有找到这个城市")
        first = results[0] if isinstance(results, list) else {}
        if not isinstance(first, dict) or "latitude" not in first or "longitude" not in first:
            raise WeatherError("返回数据里没有坐标")
        display = str(first.get("name") or name)
        forecast_url = OM_FORECAST_URL.format(
            lat=first["latitude"], lon=first["longitude"]
        )
        forecast = json.loads(self._get(forecast_url, use_proxy=True, deadline=deadline))
        return parse_open_meteo_reading(forecast, display)

    # ---- 对外
    def fetch(self, city: str, *, code: str = "",
              deadline: float | None = None) -> WeatherReading:
        """取当前天气：源 A → 源 B，都失败抛 :class:`WeatherError`（点名两个源）。

        ``code`` = 该城市已知的地区码（``weather_city_codes``）：给了就按码直取，
        同名城市（朝阳 北京 / 辽宁）靠它区分；没给就按名字检索。
        """
        failures: list[tuple[str, str]] = []
        for source, worker in ((SOURCE_CN, lambda c, d: self._fetch_cn(c, d, code)),
                               (SOURCE_OPEN_METEO, self._fetch_open_meteo)):
            try:
                reading = worker(city, deadline)
            except Exception as exc:  # noqa: BLE001 - 单源失败必须落到下一个源
                reason = describe_error(exc)
                failures.append((source, reason))
                log.warning("天气取数失败 %s（%s）：%s", city, source, reason)
                continue
            log.info("天气取数成功：%s（%s）%s %s", reading.city, source,
                     reading.condition, reading.temperature)
            return reading
        raise WeatherError(failure_text(city, failures), tuple(failures))


def failure_text(city: str, failures) -> str:
    """两源都失败时的气泡文案（点名每个源的原因，不静默）。"""
    details = "；".join(f"{source}：{reason}" for source, reason in failures)
    head = f"天气获取失败（{city}）" if city else "天气获取失败"
    return f"{head}：{details}" if details else head
