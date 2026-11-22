"""GitHub 公益加速镜像与自定义前缀的单一真值定义。

零 Qt、零网络 I/O，只使用标准库（供 config / schema / net / settings UI 共用）。

安全边界：
- 仅在用户显式选用公益镜像或自定义前缀策略时才改写下载 URL，默认 ``auto`` 与
  既有的 ``mirror``（jsDelivr 分片）/ ``github``（直连）逐字节保持原样；
- 永远保留原始 URL 在候选列表末尾作为自动兜底；
- 只包装 ``https://`` 的 GitHub 下载路径（``releases/download``、``archive``、
  ``raw``）与 ``raw.githubusercontent.com`` 文件路径；绝不包装 ``api.github.com``、
  签名 CDN 临时链或已带代理前缀的地址。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class GitHubProxyMirror:
    """一条 GitHub HTTPS 前缀加速镜像定义。"""

    id: str
    host: str
    prefix: str
    label: str


BASE_MIRROR_POLICIES: tuple[str, ...] = ("auto", "mirror", "github")
PROXY_AUTO_POLICY = "ghproxy-auto"
CUSTOM_MIRROR_POLICY = "custom"
MAX_CUSTOM_PREFIX_LENGTH = 512

GITHUB_PROXY_MIRRORS: tuple[GitHubProxyMirror, ...] = (
    GitHubProxyMirror(
        id="gh-proxy",
        host="gh-proxy.com",
        prefix="https://gh-proxy.com/",
        label="公益镜像：gh-proxy.com",
    ),
    GitHubProxyMirror(
        id="ghfast",
        host="ghfast.top",
        prefix="https://ghfast.top/",
        label="公益镜像：ghfast.top",
    ),
    GitHubProxyMirror(
        id="ghproxy-net",
        host="ghproxy.net",
        prefix="https://ghproxy.net/",
        label="公益镜像：ghproxy.net",
    ),
    GitHubProxyMirror(
        id="ghproxy-cc",
        host="ghproxy.cc",
        prefix="https://ghproxy.cc/",
        label="公益镜像：ghproxy.cc",
    ),
)

_PROXY_BY_ID: dict[str, GitHubProxyMirror] = {
    mirror.id: mirror for mirror in GITHUB_PROXY_MIRRORS
}

ALL_MIRROR_POLICIES: tuple[str, ...] = (
    "auto",
    "gh-proxy",
    "ghfast",
    "ghproxy-net",
    "ghproxy-cc",
    "ghproxy-auto",
    "custom",
    "mirror",
    "github",
)

MIRROR_POLICY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("自动（GitHub 优先，失败换镜像）", "auto"),
    *(
        (mirror.label, mirror.id)
        for mirror in GITHUB_PROXY_MIRRORS
    ),
    ("公益镜像轮询（依次尝试）", PROXY_AUTO_POLICY),
    ("自定义镜像前缀", CUSTOM_MIRROR_POLICY),
    ("jsDelivr 分片（慢但更稳）", "mirror"),
    ("只用 GitHub（不走镜像）", "github"),
)

_WRAPPABLE_GITHUB_SEGMENTS: tuple[str, ...] = (
    "/releases/download/",
    "/archive/",
    "/raw/",
)


def normalize_policy(value: object) -> str:
    """把配置里的 ``pack_mirror_policy`` 归一到合法策略；未知值回落 ``auto``。"""
    text = str(value or "auto").strip().lower()
    return text if text in ALL_MIRROR_POLICIES else "auto"


def normalize_custom_prefix(value: object) -> str:
    """校验并规范化自定义 HTTPS 镜像前缀；不合法返回空串。

    仅接受无凭据、无查询串/片段、主机名非空、总长不超过 512 的 ``https://`` URL，
    返回带末尾 ``/`` 的规范串（如 ``https://mirror.example.com/``）。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or len(text) > MAX_CUSTOM_PREFIX_LENGTH:
        return ""
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return ""
    if "?" in text or "#" in text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if (parts.scheme or "").lower() != "https":
        return ""
    if parts.username is not None or parts.password is not None:
        return ""
    if parts.query or parts.fragment:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    clean_path = (parts.path or "").rstrip("/")
    if ".." in clean_path.split("/"):
        return ""
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    normalized = f"https://{netloc}{clean_path}/"
    if len(normalized) > MAX_CUSTOM_PREFIX_LENGTH:
        return ""
    return normalized


def custom_mirror_from_prefix(custom_prefix: object) -> GitHubProxyMirror | None:
    """把合法的自定义前缀构造为 :class:`GitHubProxyMirror`；无效或空返回 None。"""
    clean = normalize_custom_prefix(custom_prefix)
    if not clean:
        return None
    host = (urlsplit(clean).hostname or "").lower()
    if not host:
        return None
    return GitHubProxyMirror(
        id=CUSTOM_MIRROR_POLICY,
        host=host,
        prefix=clean,
        label="自定义镜像前缀（失败自动回退）",
    )


def is_proxy_policy(policy: object) -> bool:
    """当前策略是否启用了 GitHub 代理前缀（含单一公益站、轮询与自定义前缀）。"""
    chosen = normalize_policy(policy)
    return (
        chosen in _PROXY_BY_ID
        or chosen == PROXY_AUTO_POLICY
        or chosen == CUSTOM_MIRROR_POLICY
    )


def proxy_mirrors_for_policy(
    policy: object,
    *,
    custom_prefix: object = "",
) -> tuple[GitHubProxyMirror, ...]:
    """返回该策略下按顺序尝试的镜像列表；非代理策略或自定义前缀为空返回空元组。"""
    chosen = normalize_policy(policy)
    if chosen in _PROXY_BY_ID:
        return (_PROXY_BY_ID[chosen],)
    if chosen == PROXY_AUTO_POLICY:
        return GITHUB_PROXY_MIRRORS
    if chosen == CUSTOM_MIRROR_POLICY:
        mirror = custom_mirror_from_prefix(custom_prefix)
        return (mirror,) if mirror is not None else ()
    return ()


def proxy_hosts_for_policy(
    policy: object,
    *,
    custom_prefix: object = "",
) -> tuple[str, ...]:
    """返回该策略需要临时放行的主机名（去重保序）；非代理策略返回空元组。"""
    hosts = [
        mirror.host.lower()
        for mirror in proxy_mirrors_for_policy(policy, custom_prefix=custom_prefix)
        if mirror.host
    ]
    return tuple(dict.fromkeys(hosts))


def wrap_github_url(url: str, mirror: GitHubProxyMirror) -> str | None:
    """若 ``url`` 属于可安全加速的 GitHub 下载地址则拼接前缀，否则返回 None。"""
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if (parts.scheme or "").lower() != "https":
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    path = parts.path or ""
    if host == "raw.githubusercontent.com":
        if not path.strip("/"):
            return None
        return f"{mirror.prefix}{raw}"
    if host == "github.com":
        if any(segment in path for segment in _WRAPPABLE_GITHUB_SEGMENTS):
            return f"{mirror.prefix}{raw}"
        return None
    return None


def expand_github_urls(
    urls: Sequence[str],
    policy: object,
    *,
    custom_prefix: object = "",
) -> tuple[str, ...]:
    """按策略展开候选 URL：代理 URL 置前，原始 URL 一律保留在后方作为回退。"""
    originals = tuple(str(item) for item in urls if str(item))
    if not originals:
        return ()
    mirrors = proxy_mirrors_for_policy(policy, custom_prefix=custom_prefix)
    if not mirrors:
        return originals
    ordered: list[str] = []
    seen: set[str] = set()
    for mirror in mirrors:
        for url in originals:
            wrapped = wrap_github_url(url, mirror)
            if wrapped and wrapped not in seen:
                seen.add(wrapped)
                ordered.append(wrapped)
    for url in originals:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return tuple(ordered)


def read_mirror_config(config) -> tuple[str, str]:
    """从配置对象安全读取 ``(pack_mirror_policy, pack_mirror_custom_url)``。"""
    if config is None:
        return "auto", ""
    try:
        raw_policy = config.get("pack_mirror_policy")
    except Exception:  # noqa: BLE001 - 读不到配置时回落默认
        raw_policy = "auto"
    try:
        raw_custom = config.get("pack_mirror_custom_url")
    except Exception:  # noqa: BLE001 - 读不到配置时回落空串
        raw_custom = ""
    return normalize_policy(raw_policy), normalize_custom_prefix(raw_custom)


__all__ = [
    "ALL_MIRROR_POLICIES",
    "BASE_MIRROR_POLICIES",
    "CUSTOM_MIRROR_POLICY",
    "GITHUB_PROXY_MIRRORS",
    "GitHubProxyMirror",
    "MAX_CUSTOM_PREFIX_LENGTH",
    "MIRROR_POLICY_OPTIONS",
    "PROXY_AUTO_POLICY",
    "custom_mirror_from_prefix",
    "expand_github_urls",
    "is_proxy_policy",
    "normalize_custom_prefix",
    "normalize_policy",
    "proxy_hosts_for_policy",
    "proxy_mirrors_for_policy",
    "read_mirror_config",
    "wrap_github_url",
]
