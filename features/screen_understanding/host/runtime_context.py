"""Bound host ports for screen policy. No application/window/config references."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from pet.desktop_query import DesktopQueryPort, get_desktop_query
from pet.feature_ports import FeatureDocumentPort, FeaturePreferencesPort, FeatureStateDocumentPort, FeatureWindowState

from .config import VisionConfigService

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

    from .worker_adapter import ProactiveScreenWorkerAdapter


@dataclass(frozen=True, slots=True)
class ScreenRuntimeContext:
    vision: VisionConfigService
    preferences: FeaturePreferencesPort
    state_document: FeatureStateDocumentPort
    dry_state_document: FeatureStateDocumentPort
    memory: FeatureDocumentPort
    window_state: Callable[[], FeatureWindowState]
    agent_busy: Callable[[str, str], bool]
    show_bubble: Callable[[str, int], None]
    hold_bubble: Callable[[float], None]
    sync_text: Callable[[str, str], None]
    execution_enabled: Callable[[], bool]
    bind_lifecycle: Callable[[Callable[[], None], Callable[[], None], Callable[[], None]], Callable[[], None]]
    worker_factory: Callable[[QObject, Callable[[str, int | None], bool]], ProactiveScreenWorkerAdapter]
    desktop: DesktopQueryPort = field(default_factory=get_desktop_query)
    allow_in_process: bool = True
    external_text: Callable[[str, str, str, str], None] | None = None

    def synchronize(self, result_id: str, kind: str, user_text: str, reply: str) -> None:
        if self.external_text is not None:
            self.external_text(result_id, kind, user_text, reply)
        else:
            self.sync_text(user_text, reply)
