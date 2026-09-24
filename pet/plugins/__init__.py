"""Core 官方插件运行时公开 API。"""

from .builtin.festival_reminder import FESTIVAL_MANIFEST, FESTIVAL_PLUGIN_ID, FestivalReminderPlugin
from .capabilities import CapabilityDenied, CapabilitySet
from .config import FESTIVAL_LEGACY_CONFIG_MAP, PluginConfigError, PluginConfigStore
from .events import CoreEvent, CoreEventBus, ScopedEventBus, Subscription
from .manifest import PluginDiagnostic, PluginManifest, PluginManifestError
from .ports import (
    CommandConflict,
    CommandNotFound,
    CommandRegistry,
    ContentProviderRegistry,
    PresentationPort,
    SchedulerPort,
    StructuredLogger,
    TimerHandle,
)
from .runtime import (
    CORE_PLUGIN_API_VERSION,
    CORE_PLUGIN_VERSION,
    PluginContext,
    PluginLifecycle,
    PluginRecord,
    PluginRegistry,
)

__all__ = [
    "CapabilityDenied",
    "CapabilitySet",
    "CommandConflict",
    "CommandNotFound",
    "CommandRegistry",
    "ContentProviderRegistry",
    "CoreEvent",
    "CoreEventBus",
    "CORE_PLUGIN_API_VERSION",
    "CORE_PLUGIN_VERSION",
    "FESTIVAL_LEGACY_CONFIG_MAP",
    "FESTIVAL_MANIFEST",
    "FESTIVAL_PLUGIN_ID",
    "FestivalReminderPlugin",
    "PluginConfigError",
    "PluginConfigStore",
    "PluginContext",
    "PluginDiagnostic",
    "PluginLifecycle",
    "PluginManifest",
    "PluginManifestError",
    "PluginRecord",
    "PluginRegistry",
    "PresentationPort",
    "SchedulerPort",
    "ScopedEventBus",
    "StructuredLogger",
    "Subscription",
    "TimerHandle",
]
