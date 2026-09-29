"""Screen-owned contribution descriptors. UI is imported only when mounted."""

from pet.plugins.contributions import Contribution
from pet.plugins.feature_host import FeatureDefinition
from pet.plugins.ports import CommandNotFound

OWNER = "official.screen-understanding"


def _safe(handle, *args):
    try:
        if handle.active:
            return handle.invoke(*args)
    except CommandNotFound:
        return None
    return None


def manual_menu(menu, handle, config, *, icons=True):
    from pet.context_menus.shared import add_action

    return add_action(menu, "看看屏幕", "screen" if icons else None, lambda: _safe(handle), close_on_trigger=True)


def automatic_menu(menu, handle, config, *, icons=True):
    from pet.context_menus.shared import add_action, add_submenu

    from ..common.policy import effective_proactive_config

    sub = add_submenu(menu, "主动识屏", None)
    pro = effective_proactive_config(config)
    for label, key, default in (
        ("开启主动识屏", "enabled", False),
        ("鼠标穿透时仍允许主动识屏", "allow_when_mouse_through", True),
        ("触发前先兆提示", "pre_cue", True),
        ("仅当我闲置时触发", "require_idle", False),
        ("dry-run 验证模式", "dry_run", False),
    ):
        action = sub.addAction(label)
        action.setCheckable(True)
        action.setChecked(bool(pro.get(key, default)))
        action.toggled.connect(lambda on, k=key: _safe(handle, k, on))
    sub.addSeparator()
    add_action(sub, "打开设置…", None, lambda: _safe(handle, "settings", None), close_on_trigger=True)
    return sub


def settings_factory(context, parent=None):
    from .contribution_settings import ScreenContributionSettings

    return ScreenContributionSettings(context, parent)


def definition():
    return FeatureDefinition(
        OWNER,
        (
            Contribution("look_screen", "menu", command="look", group="interaction", factory=manual_menu),
            Contribution("proactive_screen", "menu", command="automatic", group="automation", platforms=("win32",), factory=automatic_menu),
        ),
        settings_factory,
    )
