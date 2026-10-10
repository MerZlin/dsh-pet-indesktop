"""Stable v1 author facade; no global configuration or application objects.

Trusted Python code is not sandboxed. Worker launching still uses verified
package launch grants; importing this facade does not grant execution.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pet.api_ports import ApiRequest, FeatureApiPort
from pet.feature_ports import FeatureHostContext
from pet.plugins.contributions import Contribution
from pet.plugins.feature_host import FeatureDefinition as _Definition


@dataclass(frozen=True)
class FeatureDefinition(_Definition):
    mount_contract: str = "mod/v1"


class Runtime(Protocol):
    commands: dict

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class SettingsComponent(Protocol):
    def dirty(self) -> bool: ...
    def draft(self): ...
    def confirm_save(self) -> bool: ...
    def discard_changes(self) -> bool: ...
    def dispose(self) -> None: ...


__all__ = ["WorkerClient", "ApiRequest", "Contribution", "FeatureApiPort", "FeatureDefinition", "FeatureHostContext", "Runtime", "SettingsComponent"]


if TYPE_CHECKING:
    from .worker_client import WorkerClient


def __getattr__(name):
    if name == "WorkerClient":
        from .worker_client import WorkerClient
        return WorkerClient
    raise AttributeError(name)
