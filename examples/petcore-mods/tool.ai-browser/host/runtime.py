"""运行时：无 Qt、无网络库，只做闸门与能力选择。

打开顺序：内嵌 Chromium（宿主有）→ Edge 应用窗口 → 系统默认浏览器。
每一次降级都给气泡说明，不静默。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from . import engine, external, sites

OWNER = sites.OWNER
log = logging.getLogger(OWNER)
SETTING_ENABLED = "ai_browser_enabled"
SETTING_DEFAULT_SITE = "ai_browser_default_site"
SETTING_EXTRA_DOMAINS = "ai_browser_extra_domains"


class BrowserRuntime:
    def __init__(self, context):
        self.context = context
        self.ports = context.ports
        self.commands = {"open": self.open}

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    # ---- 命令：菜单/热键触发，声明 slot 形参以便 core 传入发起菜单的那只桌宠
    def open(self, slot=None) -> None:
        if not self._enabled():
            self._say("AI 浏览器已在设置里关闭（设置 → 扩展管理 → AI 浏览器）")
            return
        site = self._default_site()
        extra = self._extra_domains()
        if engine.engine_available():
            try:
                from .browser_window import open_browser

                open_browser(
                    self.storage_dir(),
                    extra_domains=extra,
                    default_site_id=site.id,
                )
                return
            except Exception:
                log.exception("embedded browser unavailable, falling back")
        channel = external.open_external(site.url)
        if channel == "edge":
            self._say(f"当前程序未内置 Chromium 内核，已用 Edge 应用窗口打开 {site.name}")
        elif channel == "system":
            self._say(f"当前程序未内置 Chromium 内核，已交给系统浏览器打开 {site.name}")
        else:
            self._say("打不开浏览器：宿主没有内嵌内核，也没找到 Edge 或默认浏览器", force=True)

    # ---- 配置
    def _enabled(self) -> bool:
        config = getattr(self.ports, "config", None)
        if config is None:
            return True
        return bool(config.get(SETTING_ENABLED, True))

    def _default_site(self) -> sites.Site:
        config = getattr(self.ports, "config", None)
        value = config.get(SETTING_DEFAULT_SITE, sites.DEFAULT_SITE_ID) if config else None
        return sites.default_site(value)

    def _extra_domains(self) -> tuple[str, ...]:
        config = getattr(self.ports, "config", None)
        value = config.get(SETTING_EXTRA_DOMAINS, "") if config else ""
        return sites.normalize_extra(value)

    def storage_dir(self) -> Path:
        storage = getattr(self.ports, "storage", None)
        if storage is not None:
            try:
                return Path(storage.data_dir(OWNER))
            except Exception:
                log.warning("storage port failed, using temp dir")
        return Path(tempfile.gettempdir()) / "petcore-ai-browser"

    # ---- 呈现
    def _say(self, text: str, *, force: bool = False) -> None:
        presentation = getattr(self.ports, "presentation", None)
        if presentation is None:
            log.info(text)
            return
        try:
            presentation.say(text, force=force)
        except Exception:
            log.warning("presentation.say failed", exc_info=True)
