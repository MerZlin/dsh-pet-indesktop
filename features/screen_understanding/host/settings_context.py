"""Host-bound settings services; never carry Config or a filesystem root."""

from dataclasses import dataclass

from pet.desktop_query import DesktopQueryPort
from pet.feature_ports import FeatureDocumentPort, FeaturePreferencesPort

from .config import VisionConfigService


@dataclass(frozen=True, slots=True)
class ScreenSettingsContext:
    vision: VisionConfigService
    preferences: FeaturePreferencesPort
    memory: FeatureDocumentPort
    desktop: DesktopQueryPort
