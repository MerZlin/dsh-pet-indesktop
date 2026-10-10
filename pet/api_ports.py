"""Optional generic API ABI. Metadata carries no credential references or secrets."""

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True)
class ApiMetadata:
    service_id: str
    name: str
    base_url: str
    chat_path: str
    model: str
    verify_ssl: bool
    timeout: int
    balance_protocol: str
    version: str
    authorization_version: str = ""
    credential_source: str = ""
    fallback_model: str = ""


@dataclass(frozen=True, slots=True)
class ApiRequest:
    metadata: ApiMetadata
    api_key: str = field(repr=False)

    @property
    def base_url(self):
        return self.metadata.base_url

    @property
    def model(self):
        return self.metadata.model


@dataclass(frozen=True, slots=True)
class FeatureApiPort:
    metadata: Callable[[str], ApiMetadata]
    effective_version: Callable[[str], str]
    resolve: Callable[[str], ApiRequest]
    subscribe: Callable[[Callable], Callable]
    open_settings: Callable[[], None]
