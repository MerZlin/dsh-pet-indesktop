"""通用取数端口的公开契约。

业务 mod（天气、歌词、市场外的第三方接口）需要 HTTP，但**不许**自己 import
`urllib/http/socket/...`：路由策略（系统代理 / 直连）、失败分类和大小上限都在
Core 里，mod 只描述「要什么」，拿到状态码、头与字节。

与 `ApiPort` 的分工：`ApiPort` 是 LLM 专用（流式、密钥、检索轮）；`HttpPort` 是
普通取数，不认任何服务规格，也不碰钥匙串。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

#: 单次请求默认超时（秒）。
DEFAULT_TIMEOUT = 10.0
#: 响应体默认上限（字节）；超出部分截断，由调用方按协议自行判定失败。
DEFAULT_MAX_BYTES = 4_000_000
#: 换路由前允许的服务器明确答复状态（主分支歌词口径）。
DEFAULT_FALLBACK_STATUSES = (500, 502, 504)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """一次普通 HTTP 请求。

    ``prefer_direct`` 决定首选路由：``False`` 先走系统代理（默认，尊重加速器/VPN），
    ``True`` 先直连（国内站点口径）。两条路由最多各试一次，共享同一个 ``timeout``
    预算；只有传输失败或 ``retry_statuses`` 里的状态才换路由。

    ``params`` 是查询串键值对：由 Core 负责百分号编码，mod 不需要 import
    ``urllib.parse``。
    """

    url: str
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    params: tuple[tuple[str, str], ...] = ()
    timeout: float = DEFAULT_TIMEOUT
    prefer_direct: bool = False
    retry_statuses: tuple[int, ...] = DEFAULT_FALLBACK_STATUSES
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        wanted = str(name).lower()
        for key, value in self.headers:
            if str(key).lower() == wanted:
                return str(value)
        return default

    @property
    def encoding(self) -> str:
        content_type = self.header("Content-Type")
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.strip().lower() == "charset" and value.strip():
                return value.strip().strip('"')
        return "utf-8"

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding, errors="replace")


class HttpError(RuntimeError):
    """取数失败的稳定分类：``auth/rate_limit/network/timeout/protocol``。

    与 :class:`petcore.net.errors.NetworkError` 同口径，但只暴露契约字段，
    不带异常链、不带响应体（可能回显密钥）。
    """

    reason = "network"

    def __init__(
        self,
        reason: str = "",
        *,
        status: int = 0,
        retry_after: float | None = None,
        message: str = "",
    ):
        if reason:
            self.reason = str(reason)
        self.status = int(status or 0)
        self.retry_after = retry_after
        super().__init__(message or self.reason)


class HttpPort(Protocol):
    def request(self, request: HttpRequest) -> HttpResponse: ...

    def get_json(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: tuple[tuple[str, str], ...] = (),
        params: tuple[tuple[str, str], ...] = (),
        prefer_direct: bool = False,
    ) -> Any: ...


class ResumePolicy(str, Enum):
    """断点续传策略。"""

    #: 只有 206 且 `Content-Range` 起点等于本地长度才追加，否则丢弃 `.part` 重下。
    RESUME_IF_MATCH = "resume-if-match"
    #: 永远从 0 开始重下。
    RESTART = "restart"


@dataclass(frozen=True, slots=True)
class TransferSpec:
    """一次 artifact 下载的完整描述（市场通道专用）。

    `allowed_schemes` 默认只允许 `https`；loopback 测试必须显式放开 `http`。
    `allowed_hosts` 是**跨主机跳转的白名单**：为空时只允许同主机跳转；GitHub Release
    这类 `github.com → objects.githubusercontent.com` 必须由调用方显式列出目标主机。
    `allow_loopback_http` 为真时，传输层对 `127.0.0.1` / `::1` / `localhost` 额外放行
    明文 `http`（本地镜像与测试用）；其余主机的协议仍只由 `allowed_schemes` 决定。
    `file://` 还必须同时显式设置 `allow_local_file=True` 和 `local_root`，且目标解析后
    必须位于该根目录内；网络索引或重定向不能借此授权本机文件。
    校验（size / sha256）由 market 层负责，传输层只用 `expected_size` 判断续传是否对齐。

    `download_connections` 是并行连接数：`1` 走旧的单流（`.part` 追加续传），
    `2..8` 走多连接 Range 分段（每段独立续传，装配后才是 `.part`）。
    `mirror_policy` 是镜像策略：`ordered` = 按列表顺序回退、`race` = 并发预检取最快、
    `auto` = 多源时竞速。`artifact_name` 指定产物文件名（空 = URL basename），
    分段下载靠它直接写调用方的稳定 `.part`，不必整包复制到 staging 目录。
    """

    urls: tuple[str, ...]
    expected_size: int | None = None
    expected_sha256: str = ""
    max_redirects: int = 3
    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_hosts: tuple[str, ...] = ()
    allow_loopback_http: bool = True
    # file:// 只允许在显式本地索引授权且目标位于此根目录时使用。
    allow_local_file: bool = False
    local_root: str | None = None
    resume: ResumePolicy = ResumePolicy.RESUME_IF_MATCH
    timeout: float = 15.0
    #: 并行连接数：1 = 旧的单流（逐字节追加 + 续传），2..8 = 多连接 Range 分段。
    download_connections: int = 1
    #: 镜像策略：``auto``（多源时竞速）/``ordered``（按索引顺序回退）/``race``。
    mirror_policy: str = "auto"
    #: 产物文件名（不含目录）；空 = 用 URL 的 basename（旧行为）。
    #: 分段分支靠它把 `.part` 固定成调用方的稳定路径，免掉整包复制到 staging。
    artifact_name: str = ""

    def __post_init__(self) -> None:
        if not self.urls:
            raise ValueError("TransferSpec.urls must not be empty")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be >= 0")
        if self.expected_size is not None and self.expected_size < 0:
            raise ValueError("expected_size must be >= 0")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if any(not str(host).strip() for host in self.allowed_hosts):
            raise ValueError("allowed_hosts must not contain empty entries")
        if int(self.download_connections) < 1:
            raise ValueError("download_connections must be >= 1")
        if str(self.mirror_policy) not in {"auto", "ordered", "race"}:
            raise ValueError(f"unsupported mirror_policy: {self.mirror_policy}")
        name = str(self.artifact_name or "")
        if name and (
            "/" in name or "\\" in name or name in {".", ".."} or not name.strip()
        ):
            raise ValueError("artifact_name must be a plain file name")


@dataclass(frozen=True, slots=True)
class TransferReport:
    """一次成功传输的结果。

    `resumed_from` 只在**服务端区间与本地 `.part` 对齐**时才非零；续传失败后重下
    时为 0（表示本次没有沿用任何已有字节）。`path` 是落盘的最终文件路径。
    """

    url: str
    bytes_read: int
    path: str = ""
    resumed_from: int = 0
    redirects: int = 0
