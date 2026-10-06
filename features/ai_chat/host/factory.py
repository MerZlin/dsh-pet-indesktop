"""Signed AI entrypoint: metadata only until Core mounts an authorized component."""

from pet.feature_ports import FeatureHostContext
from pet.plugins.contributions import Contribution
from pet.plugins.feature_host import FeatureDefinition

OWNER = "official.ai-chat"


def _bound(context):
    if not isinstance(context, FeatureHostContext) or context.owner != OWNER:
        raise TypeError("AI host requires owner-bound FeatureHostContext")
    if context.desktop is not None or context.worker_launch_factory is not None or not context.allow_in_process:
        raise PermissionError("ai_execution_contract_invalid")
    return context


def _menu(menu, handle, config, *, icons=True, quick=False):
    from pet.context_menus.shared import add_action
    from pet.plugins.ports import CommandNotFound

    def invoke():
        try:
            if handle.active:
                return handle.invoke()
        except CommandNotFound:
            return None
        return None

    return add_action(menu, "快捷对话" if quick else "AI 对话", "chat" if icons else None, invoke, close_on_trigger=True)


def chat_menu(menu, handle, config, *, icons=True):
    return _menu(menu, handle, config, icons=icons)


def quick_menu(menu, handle, config, *, icons=True):
    return _menu(menu, handle, config, icons=icons, quick=True)


def create_settings(context, parent=None):
    from .contribution_settings import AiContributionSettings

    return AiContributionSettings(_bound(context), parent)


def create_runtime(context, *, worker_mode="auto"):
    from .runtime import AiRuntime

    if worker_mode not in ("auto", "in-process"):
        raise ValueError("ai_host_has_no_worker")
    return AiRuntime(_bound(context))


def create_host():
    return FeatureDefinition(
        OWNER,
        (
            Contribution("chat", "menu", command="chat", group="interaction", factory=chat_menu),
            Contribution("quick_chat", "menu", command="quick_chat", group="interaction", factory=quick_menu),
        ),
        create_settings,
        runtime_factory=create_runtime,
        allow_in_process=True,
    )
