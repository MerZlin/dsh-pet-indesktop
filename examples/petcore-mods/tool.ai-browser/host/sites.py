"""国模 AI 对话站点白名单：纯数据 + 纯函数（无 Qt、无网络、无磁盘）。

视图本体在 ``web_pane.py``，由 core 的对话窗托管（``chat_view`` 契约）；
站点表与解析留在这里，才能在不启动 Qt 的前提下被单测覆盖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

OWNER = "tool.ai-browser"
DEFAULT_SITE_ID = "deepseek"


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    url: str
    hosts: tuple[str, ...]
    aliases: tuple[str, ...] = ()


#: 每个站点的 hosts 同时承担两件事：菜单打开的入口，以及主框架导航的放行范围。
#: 登录/单点登录会跳到同集团域名，所以把实测与官方登录链路上的域一并收进来。
SITES: tuple[Site, ...] = (
    Site(
        "deepseek",
        "DeepSeek",
        "https://chat.deepseek.com",
        ("deepseek.com",),
        ("deepseek", "ds", "深度求索"),
    ),
    Site(
        "doubao",
        "豆包",
        "https://www.doubao.com/chat/",
        ("doubao.com",),
        ("doubao", "豆包"),
    ),
    Site(
        "qianwen",
        "千问",
        "https://www.qianwen.com",
        ("qianwen.com", "tongyi.com"),
        ("qianwen", "tongyi", "千问", "通义", "通义千问"),
    ),
    Site(
        "kimi",
        "Kimi",
        "https://www.kimi.com",
        ("kimi.com", "moonshot.cn"),
        ("kimi", "moonshot", "月之暗面"),
    ),
    Site(
        "wenxin",
        "文心一言",
        "https://yiyan.baidu.com",
        ("baidu.com",),
        ("wenxin", "yiyan", "baidu", "文心", "文心一言", "百度"),
    ),
    Site(
        "zhipu",
        "智谱清言",
        "https://chatglm.cn",
        ("chatglm.cn", "zhipuai.cn", "bigmodel.cn"),
        ("zhipu", "chatglm", "智谱", "清言"),
    ),
    Site(
        "yuanbao",
        "腾讯元宝",
        "https://yuanbao.tencent.com",
        ("tencent.com", "qq.com"),
        ("yuanbao", "元宝", "腾讯元宝"),
    ),
    Site(
        "xinghuo",
        "讯飞星火",
        "https://xinghuo.xfyun.cn",
        ("xfyun.cn",),
        ("xinghuo", "xfyun", "星火", "讯飞", "讯飞星火"),
    ),
)

SITE_BY_ID = {site.id: site for site in SITES}


def normalize_domain(value: object) -> str:
    """把 "https://A.Example.com:8443/x" / "*.example.com" 归一成 "a.example.com"。"""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlsplit(text).hostname or ""
    else:
        text = text.split("/", 1)[0]
        text = text.rsplit("@", 1)[-1]
        text = text.split(":", 1)[0]
    text = text.strip().strip(".")
    if text.startswith("*."):
        text = text[2:]
    if not text or any(char.isspace() for char in text):
        return ""
    return text


def normalize_extra(value: object) -> tuple[str, ...]:
    """额外白名单：接受配置里的逗号串或可迭代对象，去重保序。"""
    if isinstance(value, str):
        raw = (
            value.replace("，", ",")
            .replace("；", ",")
            .replace(";", ",")
            .replace("、", ",")
            .replace("\n", ",")
            .split(",")
        )
    else:
        raw = list(value or ())  # type: ignore[arg-type]
    domains: list[str] = []
    for item in raw:
        domain = normalize_domain(item)
        if domain and domain not in domains:
            domains.append(domain)
    return tuple(domains)


def allowed_domains(extra: object = ()) -> tuple[str, ...]:
    domains: list[str] = [domain for site in SITES for domain in site.hosts]
    for domain in normalize_extra(extra):
        if domain not in domains:
            domains.append(domain)
    return tuple(domains)


def host_allowed(host: str, extra: object = ()) -> bool:
    host = str(host or "").strip().lower().rstrip(".")
    if not host:
        return False
    return any(
        host == domain or host.endswith("." + domain) for domain in allowed_domains(extra)
    )


def is_allowed(url: object, extra: object = ()) -> bool:
    parts = urlsplit(str(url or "").strip())
    if parts.scheme.lower() not in ("http", "https"):
        return False
    return host_allowed(parts.hostname or "", extra)


def site(site_id: object) -> Site | None:
    return SITE_BY_ID.get(str(site_id or "").strip().lower())


def site_for_url(url: object) -> Site | None:
    host = (urlsplit(str(url or "").strip()).hostname or "").strip().lower()
    if not host:
        return None
    for item in SITES:
        if any(host == domain or host.endswith("." + domain) for domain in item.hosts):
            return item
    return None


def resolve_site(text: object) -> Site | None:
    """把 "kimi" / "豆包" / "通义千问" 之类关键词解析成站点。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    exact = SITE_BY_ID.get(raw)
    if exact is not None:
        return exact
    matches: list[tuple[int, Site]] = []
    for item in SITES:
        for alias in (item.name.lower(), *(alias.lower() for alias in item.aliases)):
            if alias and (alias in raw or raw in alias):
                matches.append((len(alias), item))
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return matches[0][1]


def resolve_target(text: object, extra: object = ()) -> tuple[Site | None, str]:
    """把用户输入解析成 ``(site, url)``；不在白名单时返回 ``(None, "")``。

    只接受两种输入：已知站点关键词，或**已在白名单内**的网址。这样地址栏
    本身也是白名单闸门，不依赖页面层的拦截。
    """
    raw = str(text or "").strip()
    if not raw:
        return (None, "")
    site = resolve_site(raw)
    if site is not None:
        return (site, site.url)
    head = raw.split("/", 1)[0]
    if "://" in raw or head.startswith("www.") or "." in head:
        candidate = raw if "://" in raw else "https://" + raw
        if is_allowed(candidate, extra):
            return (site_for_url(candidate), candidate)
        return (None, "")
    return (None, "")


def default_site(site_id: object = None) -> Site:
    return site(site_id) or SITE_BY_ID[DEFAULT_SITE_ID]


def _custom_site(name: str, url: str, index: int) -> Site | None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = (parts.hostname or "").strip().lower()
    if not host:
        return None
    return Site(
        id=f"custom{index}",
        name=name or host,
        url=url,
        hosts=(host,),
        aliases=(name,) if name else (),
    )


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def parse_custom_sites(value: object) -> tuple[Site, ...]:
    """用户自定义站点：每行 ``名称 | URL``（也接受全角竖线与纯 URL）。

    非法行（非 http/https、没有主机名）整行丢弃——半个坏地址不该把好站点一起废掉。
    注意 ``javascript:alert(1)`` 这种**没有 //** 的写法：先按 scheme 判掉，绝不
    靠"补 https://"绕过去。
    """
    lines: list[str] = []
    if isinstance(value, str):
        lines = value.replace("｜", "|").splitlines()
    else:
        lines = [str(item) for item in (value or ())]  # type: ignore[arg-type]
    custom: list[Site] = []
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        if "|" in text:
            name, _, url = text.partition("|")
        else:
            name, url = "", text
        url = url.strip()
        if not url:
            continue
        if "://" not in url:
            if _SCHEME_RE.match(url):
                continue
            url = "https://" + url
        item = _custom_site(name.strip(), url, len(custom) + 1)
        if item is not None:
            custom.append(item)
    return tuple(custom)


def all_sites(value: object = "") -> tuple[Site, ...]:
    """内置站点 + 用户自定义（同 id 只保留先出现的，内置优先）。"""
    sites: list[Site] = list(SITES)
    known = {item.id for item in sites}
    for item in parse_custom_sites(value):
        if item.id in known:
            continue
        sites.append(item)
    return tuple(sites)
