"""ai.chat 的入口：只做依赖组装，不放业务逻辑。"""

from __future__ import annotations

from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .constants import OWNER
from .runtime import AiChatRuntime
from .settings_page import AiChatPage

__all__ = ["AiChatPage", "AiChatRuntime", "create_host"]


def create_host(context):
    def proactive_enabled() -> bool:
        port = getattr(getattr(context, "ports", None), "config", None)
        getter = getattr(port, "get", None)
        if not callable(getter):
            return False
        try:
            return bool(getter("ai_proactive_enabled", False))
        except Exception:
            return False

    def memory_view():
        # 视图模块会 import Qt；延迟到第一次打开「记忆」页，避免 --check /
        # 无头环境在扩展加载阶段就拉 QtWidgets。
        from .memory_view import MemoryView

        return MemoryView(context)

    return FeatureDefinition(
        owner=OWNER,
        contributions=(
            Contribution(
                kind="settings_page",
                id="chat",
                data={"title": "AI 对话", "order": 640, "icon": "chat"},
            ),
            Contribution(
                kind="chat_view",
                id="memory",
                data={"label": "记忆", "order": 60},
            ),
            # 这三个以前是 core 写死的菜单项，没装本扩展也会出现（点了才提示
            # 「AI 扩展未安装」）。改成注册项后：没装就没有，装了按默认布局
            # （`ai.chat:open` / `ai.chat:look_screen` / `ai.chat:screen_capture`）
            # 回到「播放动画」下面的原位。
            Contribution(
                kind="menu",
                id="open",
                data={"label": "AI 对话", "command": "open", "order": 21, "icon": "chat"},
            ),
            Contribution(
                kind="menu",
                id="look_screen",
                data={
                    "label": "看看屏幕",
                    "command": "look_screen",
                    "order": 22,
                    "icon": "screen",
                },
            ),
            Contribution(
                kind="menu",
                id="screen_capture",
                data={
                    "label": "截图提问",
                    "command": "screen_capture",
                    "order": 23,
                    "icon": "capture",
                },
            ),
            Contribution(
                kind="menu",
                id="proactive",
                data={
                    "label": "主动识屏",
                    "command": "proactive_toggle",
                    "order": 55,
                    "icon": "eye",
                    "checked": proactive_enabled,
                },
            ),
        ),
        runtime_factory=AiChatRuntime,
        settings_factory=AiChatPage,
        view_factories={"memory": memory_view},
    )
