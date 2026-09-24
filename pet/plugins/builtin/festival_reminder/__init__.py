"""官方节日提醒 in-process 插件。"""

from __future__ import annotations

from types import SimpleNamespace

from ....festival_service import FestivalReminderService
from ...config import FESTIVAL_LEGACY_CONFIG_MAP
from ...events import CoreEvent
from ...manifest import PluginManifest

FESTIVAL_PLUGIN_ID = "official.festival-reminder"
FESTIVAL_MANIFEST = PluginManifest(
    id=FESTIVAL_PLUGIN_ID,
    name="官方节日提醒",
    version="1.0.0",
    kind="in_process",
    api_version="1",
    core_requires=">=5.0.0,<6.0.0",
    dependencies=(),
    capabilities=(
        "notification.present",
        "speech.present",
        "settings.read",
        "settings.write",
        "scheduler.timer",
        "menu.contribute",
    ),
    entrypoint=None,
    default_enabled=False,
    legacy_config_map=FESTIVAL_LEGACY_CONFIG_MAP,
)


class FestivalReminderPlugin:
    """把旧 FestivalReminderService 接到 Phase 2 的受限服务端口。"""

    def __init__(self, context) -> None:
        self.context = context
        # 服务只看到一个最小 host；展示、音频、配置和定时器均由显式端口注入。
        host = SimpleNamespace(config=context.config)
        self.service = FestivalReminderService(
            host,
            scheduler=context.scheduler,
            presentation=context.presentation,
            audio=context.presentation.speak,
            config_source=context.config,
        )
        self._config_subscription = None
        self._command_handles = []

    def start(self) -> None:
        self.service.start()
        self._config_subscription = self.context.events.subscribe(
            "core.config.changed",
            self._on_config_changed,
        )
        self._command_handles = [
            self.context.commands.register("remind_now", self.remind_now),
            self.context.commands.register("toggle", self.toggle),
        ]

    def stop(self) -> None:
        self.service.stop()
        self._config_subscription = None
        self._command_handles = []

    def apply_config(self) -> None:
        self.service.apply_config()

    def remind_now(self) -> None:
        self.service.remind_now()

    def toggle(self) -> None:
        enabled = not bool(self.context.config.get("enabled", False))
        self.context.config.set("enabled", enabled)
        self.context.config.save()
        self.context.events.publish(
            CoreEvent.now(
                "core.config.changed",
                self.context.plugin_id,
                {"plugin_id": self.context.plugin_id, "key": "enabled"},
            )
        )

    def _on_config_changed(self, _event) -> None:
        self.apply_config()

    def __getattr__(self, name):
        # AppShell 旧 facade 与现有测试继续拿到 _on_tick/should_speak_at 等服务方法。
        return getattr(self.service, name)
