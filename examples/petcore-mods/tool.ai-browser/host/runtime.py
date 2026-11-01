"""运行时：无 Qt、无网络，只把菜单命令翻译成一次 ``ChatPort.show_view``。

视图本体在 ``web_pane.py``（由 core 托管），站点表在 ``sites.py``。这里保持
无 Qt，才能在没有 GUI 的环境里被单测覆盖。
"""

from __future__ import annotations

import logging

from . import engine, sites

OWNER = sites.OWNER
VIEW_ID = "web"
SETTING_DEFAULT_SITE = "ai_browser_default_site"
SETTING_CUSTOM_SITES = "ai_browser_custom_sites"

log = logging.getLogger(OWNER)


class BrowserRuntime:
    def __init__(self, context):
        self.context = context
        self.ports = context.ports
        self.commands = {"open": self.open}

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def open(self, slot=None) -> None:
        """菜单「AI 网页」：打开对话窗并切到网页视图。"""
        reason = engine.missing_reason()
        if reason:
            self._say(engine.missing_hint(reason), force=True)
            return
        chat = getattr(self.ports, "chat", None)
        show = getattr(chat, "show_view", None)
        if not callable(show):
            self._say("当前程序还没有对话窗视图接口，更新后再用网页 AI", force=True)
            return
        try:
            opened = bool(show(OWNER, VIEW_ID, pet_slot=slot))
        except Exception:  # noqa: BLE001 - 端口异常只提示，不冒泡
            log.exception("chat.show_view failed")
            opened = False
        if not opened:
            self._say("网页 AI 没能打开：扩展可能被停用，或宿主没有网页视图", force=True)

    def _say(self, text: str, *, force: bool = False) -> None:
        presentation = getattr(self.ports, "presentation", None)
        if presentation is None:
            log.info(text)
            return
        try:
            presentation.say(text, force=force)
        except Exception:  # noqa: BLE001
            log.warning("presentation.say failed", exc_info=True)
