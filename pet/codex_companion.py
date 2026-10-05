"""Opt-in Codex edition adapters for the legacy PetWindow render topology.

Only install before constructing widgets. The default entry point keeps its
native behavior. Adapters retain upstream callables rather than copying their
implementation; hooks live here while feature state belongs to controllers.
"""

from __future__ import annotations
from functools import wraps
import importlib
import inspect
import os

_installed = False


def initialize_pet(pet):
    from .language_ui import install
    from .ui_preview import install_runtime
    from .overlay_layout import install as install_overlays
    from .codex_usage import install_for

    install(pet.cfg)
    install_runtime(pet.cfg)
    install_overlays()
    install_for(pet)


def initialize_settings(dialog):
    from .language_ui import add_language_control
    from .codex_usage import add_settings_control as add_usage
    from .ytmusic import add_settings_control as add_music
    from .independent_ui import add_controls
    from .ui_polish import settings_layout
    from .ui_preview import attach

    add_language_control(dialog)
    add_usage(dialog)
    add_music(dialog)
    add_controls(dialog)
    settings_layout(dialog)
    attach(dialog)


def commit_settings(dialog, original):
    from .independent_ui import save_controls
    from .language_ui import settings_saved

    dialog.config.set("ui_language", dialog.ui_language_select.currentData())
    dialog.config.set("codex_usage_enabled", dialog.codex_usage_switch.isChecked())
    if hasattr(dialog, "ytmusic_auto_switch"):
        dialog.config.set("ytmusic_auto_connect", dialog.ytmusic_auto_switch.isChecked())
    result = save_controls(dialog, original)
    settings_saved(dialog, result)
    return result


def install(*, settings_only=False):
    """Activate once, before app construction; never switch live render topology."""
    global _installed
    if _installed:
        return
    if os.environ.get("PET_RENDER_TOPOLOGY", "").lower() == "overlay":
        raise RuntimeError("Codex companion currently requires the PetWindow render topology.")
    from .config import Config
    from .ui_preview import overlay_config, guarded_save

    original_reload, original_save = Config.reload, Config.save

    @wraps(original_reload)
    def reload_with_preview(config):
        result = original_reload(config)
        overlay_config(config)
        return result

    @wraps(original_save)
    def save_without_preview(config):
        return guarded_save(config, original_save)

    from .codex_companion_adapters import ADAPTERS

    targets = []
    for module_name, class_name, method, factory in ADAPTERS:
        if settings_only and module_name not in ("pet.modern_settings_dialog", "pet.settings_widgets"):
            continue
        module = importlib.import_module(module_name)
        owner = getattr(module, class_name) if class_name else module
        targets.append((owner, method, factory, getattr(owner, method)))
    # Prepare and validate all layers before mutating any class. A changed
    # upstream signature must fail at startup rather than at a later UI event.
    prepared = {}
    for owner, method, factory, original in targets:
        key = (owner, method)
        previous = prepared.get(key, original)
        wrapper = factory(previous)

        def parameters(callable_):
            return [(p.name, p.kind, p.default) for p in inspect.signature(callable_, follow_wrapped=False).parameters.values()]

        if parameters(wrapper) != parameters(previous):
            raise RuntimeError(f"Codex companion adapter is incompatible with {owner.__name__}.{method}; update the companion source seams.")
        prepared[key] = wrapper
    for (owner, method), wrapper in prepared.items():
        setattr(owner, method, wrapper)
    Config.reload, Config.save = reload_with_preview, save_without_preview
    _installed = True
