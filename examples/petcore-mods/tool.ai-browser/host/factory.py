from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from . import engine
from .runtime import BrowserRuntime, VIEW_ID

OWNER = "tool.ai-browser"


def create_host(context):
    holder = {}

    def runtime_factory(runtime_context):
        holder["runtime"] = BrowserRuntime(runtime_context)
        return holder["runtime"]

    def build_view():
        # Qt 只在真正切到这一页时导入：没打过网页的会话不付这份启动开销。
        from .web_pane import build_web_pane

        return build_web_pane(context)

    contributions = [
        Contribution(
            kind="menu",
            id="open",
            data={
                "label": "AI 网页",
                "command": "open",
                "order": 70,
                "icon": "chat",
            },
        ),
        Contribution(
            kind="settings_page",
            id="browser",
            data={"title": "AI 网页", "order": 720},
        ),
    ]
    view_factories = {}
    # 宿主没打包 QtWebView（瘦身构建/非 Windows）时**不贡献** chat_view：
    # 宁可没有按钮，也不给用户一个点了就报错的死入口。
    if engine.missing_reason() in ("", "runtime"):
        contributions.append(
            Contribution(
                kind="chat_view",
                id=VIEW_ID,
                data={"label": "网页 AI", "icon": "globe", "order": 100},
            )
        )
        view_factories[VIEW_ID] = build_view

    def settings_factory(host):
        from .settings_page import AiBrowserPage

        return AiBrowserPage(host, runtime=holder.get("runtime"))

    return FeatureDefinition(
        owner=OWNER,
        contributions=tuple(contributions),
        runtime_factory=runtime_factory,
        settings_factory=settings_factory,
        view_factories=view_factories,
    )
