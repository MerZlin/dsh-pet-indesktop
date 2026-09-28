# -*- coding: utf-8 -*-
"""天气数据源：地名解析 / 中国天气网与 open-meteo 取数 / 配置清洗。

**本模块不 import Qt**：地名变体、两个数据源的解析、WMO 码表与配置清洗都是纯函数，
HTTP 出口（``_http_text`` / ``_http_json``）以参数注入，方便离线单测
（见 ``tests/test_weather_source.py``）。展示与调度在 ``pet/weather_service.py``。

数据源口径（对齐参考实现 deepseek-dafeiyu-pet，2026-09 实测）：

1. 先走**国内直连**的中国天气网（``toy1`` 搜城市 → ``d1`` 取实况，不要 Key、
   延迟低、回来的本来就是中文）；
2. 取不到再用 open-meteo 兜底（国外地名靠它），WMO 天气码查中文对照表。

两个坑写在这里免得后人重踩：

- 中国天气网响应头没有 charset，``urllib`` 拿回来必须**手动按 UTF-8 解**；
- 它的搜索接口回的是 ``([{"ref": "code~province~城市~..."}])`` 这种"伪 JSON"，
  要先去外层括号再 ``json.loads``；实况里 ``temp == "999"`` 是它自己的"无数据"占位。
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

# 城市列表上限（菜单/设置页都按它裁剪，避免越堆越长）
CITY_LIST_LIMIT = 12
CITY_MAX_LEN = 32

CN_WEATHER_SEARCH = "https://toy1.weather.com.cn/search"
CN_WEATHER_SK = "https://d1.weather.com.cn/sk_2d/{code}.html"
CN_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.weather.com.cn/",
}

OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

# open-meteo（WMO）天气码 → 中文；中国天气网取不到时的兜底描述
WMO_ZH = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


# ------------------------------------------------------------------ HTTP 出口
def _ssl_context():
    """天气源用的 SSL 上下文。

    刻意**不复用** ``pet/balance.py`` 的 ``_ssl_context``：那个走
    ``pet.chat.providers``（无 Chat 的打包变体会整个排除 pet.chat），天气不该因此
    在纯桌宠版上失效。``certifi`` 是运行时依赖，拿不到就退系统默认证书。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_text(url: str, params: dict | None = None, timeout: float = 6.0) -> str:
    """GET → UTF-8 文本（响应头没有 charset 时也按 UTF-8 解）。"""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=dict(CN_HEADERS))
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_json(url: str, params: dict | None = None, timeout: float = 6.0):
    return json.loads(_http_text(url, params, timeout) or "null")


# ------------------------------------------------------------------ 地名
def clean_city(value) -> str:
    """城市名清洗：去空白、限长；非法输入回空串。"""
    return str(value or "").strip()[:CITY_MAX_LEN]


def clean_city_list(value, current: str = "") -> list[str]:
    """城市列表清洗：去重、限长、限条数，**当前城市永远排第一**。"""
    current = clean_city(current)
    ordered: list[str] = []
    for raw in ([current] if current else []) + list(value or []):
        city = clean_city(raw)
        if city and city not in ordered:
            ordered.append(city)
    return ordered[:CITY_LIST_LIMIT]


def city_query_variants(name) -> list[str]:
    """同一地名的几种叫法：原样 + 去掉末尾的"区 / 县"。

    中国天气网的库里是"鹿城"而不是"鹿城区"，带后缀一条都搜不到。
    """
    query = clean_city(name)
    out = [query] if query else []
    if len(query) > 1 and query.endswith(("区", "县")):
        out.append(query[:-1])
    return out


def fallback_name_variants(name) -> list[str]:
    """open-meteo 兜底时再补一刀：中文库里多是"汕头市"这种全称，简称常搜不到。"""
    names: list[str] = []
    for query in city_query_variants(name):
        names.append(query)
        if not query.endswith(("市", "县", "区")):
            names.append(f"{query}市")
    return names


def wmo_zh(code) -> str:
    """WMO 天气码 → 中文；认不出来给"未知天气"（不把英文原样吐给主人）。"""
    try:
        return WMO_ZH.get(int(code), "未知天气")
    except (TypeError, ValueError):
        return "未知天气"


# ------------------------------------------------------------------ 解析
def _province_of(parts: list[str]) -> str:
    """省份字段：不同批次的 ``ref`` 字段数不完全一致（有 10 段也有 11 段），
    参考文献取的是第 10 段，但那样换个批次就会把纬度当省份——所以从**末尾**往回找
    第一个「含中文、且不是城市名本身」的字段。找不到就回空串（只影响括号里的后缀）。
    """
    city = parts[2] if len(parts) > 2 else ""
    for value in reversed(parts):
        text = str(value or "").strip()
        if text and text != city and any("\u4e00" <= ch <= "\u9fff" for ch in text):
            return text
    return ""


def parse_cn_city_search(body: str) -> list[tuple[str, str, str]]:
    """中国天气网搜索响应 → [(城市代码, 中文名, 省份)]；查不到返回 []。

    响应形如 ``([{"ref": "101280501~guangdong~汕头~Shantou~…~广东"}])``：
    外层那对括号不是合法 JSON，先去括号再解析。
    """
    text = (body or "").strip().strip("()")
    if not text:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    out: list[tuple[str, str, str]] = []
    for row in rows or []:
        parts = str((row or {}).get("ref") or "").split("~")
        if len(parts) > 2 and parts[0] and parts[2]:
            out.append((parts[0], parts[2], _province_of(parts)))
    return out


def parse_cn_sk(body: str) -> tuple[str, str] | None:
    """中国天气网实况 ``var dataSK={...}`` → (温度, 中文描述)；取不到返回 None。

    ``temp == "999"`` 是它自己的"这儿没数据"占位；没有 ``dataSK`` 通常是国外城市
    （代码以 2 开头）只回了一张报错页，早点认出来换兜底源。
    """
    body = body or ""
    if "dataSK" not in body:
        return None
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1]) or {}
    except ValueError:
        return None
    temp = data.get("temp")
    if temp in (None, "", "999"):
        return None
    desc = str(data.get("weather") or "").strip() or "未知天气"
    try:
        return str(round(float(temp))), desc
    except (TypeError, ValueError):
        return None


def format_weather_line(city: str, temp, desc: str) -> str:
    """气泡文案：``汕头今天 26°，天气阴``。"""
    return f"{clean_city(city)}今天 {temp}°，天气{str(desc or '').strip() or '未知天气'}"


# ------------------------------------------------------------------ 取数
def lookup_city(query, *, text_getter=_http_text) -> list[tuple[str, str]]:
    """联网查候选城市 → [(显示名, 存进配置的城市名)]；都失败时回退原样一条。

    先走中国天气网（国内直连、中文库全），查不到再退 open-meteo（国外地名靠它）。
    """
    query = clean_city(query)
    if not query:
        return []
    items: list[tuple[str, str]] = []
    for variant in city_query_variants(query):
        try:
            for _code, name, province in parse_cn_city_search(
                text_getter(CN_WEATHER_SEARCH, {"cityname": variant})
            )[:6]:
                if name not in [item[1] for item in items]:
                    items.append((f"{name}（{province}）" if province else name, name))
        except Exception:
            pass
        if items:
            break
    if not items:
        try:
            data = _http_json(OPEN_METEO_GEO, {"name": query, "count": 6, "language": "zh"})
            for row in ((data or {}).get("results") or []):
                label = str(row.get("name") or "")
                extra = " · ".join(
                    str(v) for v in (row.get("admin1"), row.get("country")) if v
                )
                if label:
                    items.append((f"{label}（{extra}）" if extra else label, label))
        except Exception:
            items = []
    return items or [(query, query)]


def _cn_weather_now(code: str, *, text_getter=_http_text) -> tuple[str, str] | None:
    return parse_cn_sk(text_getter(CN_WEATHER_SK.format(code=code)))


def _open_meteo_weather(name: str, *, json_getter=_http_json) -> tuple[str, str] | None:
    """兜底源：地名 → 坐标 → 实况。"""
    found = ((json_getter(OPEN_METEO_GEO, {"name": name, "count": 1, "language": "zh"}) or {})
             .get("results") or [])
    if not found:
        return None
    data = json_getter(OPEN_METEO_FORECAST, {
        "latitude": found[0].get("latitude"),
        "longitude": found[0].get("longitude"),
        "current": "temperature_2m,weather_code",
    }) or {}
    current = data.get("current") or {}
    if current.get("temperature_2m") is None:
        return None
    code = current.get("weather_code")
    return str(round(float(current["temperature_2m"]))), wmo_zh(code)


def fetch_weather(city, *, text_getter=_http_text, json_getter=_http_json):
    """查天气：(温度, 中文描述)；都失败返回 None。

    排序与重试都对齐参考实现的实测教训：国内源偶发抽风重试一次比退到墙外划算；
    兜底源对每个地名变体各试一次。
    """
    city = clean_city(city)
    if not city:
        return None
    variants = city_query_variants(city)
    for attempt in range(2):
        try:
            for query in variants:
                hits = parse_cn_city_search(
                    text_getter(CN_WEATHER_SEARCH, {"cityname": query})
                )
                if hits:
                    got = _cn_weather_now(hits[0][0], text_getter=text_getter)
                    if got:
                        return got
            break
        except Exception:
            if attempt == 0:
                continue
    for attempt in range(2):
        for name in fallback_name_variants(city):
            try:
                got = _open_meteo_weather(name, json_getter=json_getter)
            except Exception:
                got = None
            if got:
                return got
        if attempt == 0:
            continue
    return None
