from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .runtime import BrowserRuntime
from .settings_page import AiBrowserPage

OWNER = "tool.ai-browser"


def create_host(_context):
    holder = {}

    def runtime_factory(context):
        holder["runtime"] = BrowserRuntime(context)
        return holder["runtime"]

    return FeatureDefinition(
        owner=OWNER,
        contributions=(
            Contribution(
                kind="menu",
                id="open",
                data={
                    "label": "打开浏览器",
                    "command": "open",
                    "order": 70,
                    "icon": "chat",
                },
            ),
            Contribution(
                kind="settings_page",
                id="browser",
                data={"title": "AI 浏览器", "order": 720},
            ),
        ),
        runtime_factory=runtime_factory,
        settings_factory=lambda host: AiBrowserPage(host, runtime=holder.get("runtime")),
    )
