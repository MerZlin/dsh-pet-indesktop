"""API 能力的公开契约。

mod 只看见「服务元数据 + 一次解析结果」，**永远拿不到也不该拿到密钥**：
``ApiRequest.api_key`` 不参与 ``repr``，``ApiMetadata`` 里没有任何凭据字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class ApiMetadata:
    service_id: str
    name: str
    base_url: str
    chat_path: str = "/v1/chat/completions"
    model: str = ""
    vision_model: str = ""
    verify_ssl: bool = True
    timeout: int = 60
    balance_protocol: str = "none"
    version: str = ""
    models_path: str = "/v1/models"
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApiRequest:
    metadata: ApiMetadata
    api_key: str = field(repr=False)

    @property
    def base_url(self) -> str:
        return self.metadata.base_url

    @property
    def model(self) -> str:
        return self.metadata.model


@dataclass(frozen=True, slots=True)
class WebSearchOptions:
    """联网检索的纯配置（不含密钥；密钥只在 core 的钥匙串里）。"""

    engine: str = "baidu"
    max_results: int = 5
    fetch_pages: bool = True
    #: 多引擎扇出（空 = 沿用 ``engine``）；同一查询按顺序取结果并按 URL 去重。
    engines: tuple[str, ...] = ()
    #: 合并后交给模型的最多来源数（多引擎时用；单引擎仍取 max_results）。
    max_sources: int = 4
    #: 单页正文最多取多少字（readability 抽取后）。
    page_chars: int = 12000


class ApiPort(Protocol):
    def services(self) -> tuple[ApiMetadata, ...]: ...
    def metadata(self, service_id: str) -> ApiMetadata: ...
    def resolve(self, service_id: str) -> ApiRequest: ...
    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]: ...
    def open_settings(self) -> None: ...
    def fetch_balance(self, service_id: str, *, force: bool = False) -> Any: ...
    def web_search_key_status(self) -> dict[str, bool]: ...
    def set_web_search_key(self, engine: str, value: str) -> None: ...
    def credentials_available(self) -> bool: ...
    def stream_chat(
        self,
        service_id: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str = "",
        temperature: float | None = None,
        timeout: float | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        thinking: str = "auto",
        reasoning_effort: str = "",
        web_search: WebSearchOptions | None = None,
        cancel: Any | None = None,
    ) -> Any: ...
