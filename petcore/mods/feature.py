from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__, paths
from ..contracts.contributions import ChatViewSpec, Contribution, SettingsPageSpec
from ..contracts.feature import FeatureDefinition, Runtime
from ..contracts.host import HostPorts
from .manifest import MOD_FILE, load_manifest, path_within

logger = logging.getLogger("petcore.mods.feature")

#: 已并入 Core 的内置特性：设置内容回 Core 设置页，不再作为「扩展」出现。
CORE_FEATURE_IDS = frozenset(
    {
        "fun.click-talk",
        "fun.self-talk",
        "tool.quick-launch",
        "tool.quiet-mode",
        "tool.links",
        "tool.memory-trim",
        "tool.network-info",
    }
)

GROUP_CORE = "core"
GROUP_BASE = "base"
GROUP_ADDON = "addon"


def _is_base_pack(path: Path | None) -> bool:
    """只有随程序内置（`petcore/builtin`）的包算「基础扩展」。

    安装器投放的 `.trusted` 包与用户导入的一样是「加装扩展」：它们是用户勾选安装的
    可选包，不是 Core 的一部分。
    """
    if path is None:
        return False
    try:
        builtin_root = paths.builtin_features_dir().parent.resolve()
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return path_within(builtin_root, resolved)


class FeatureContextImpl:
    def __init__(
        self, owner: str, ports: HostPorts, config: dict[str, Any] | None = None, host=None
    ):
        self.owner = owner
        self.core_version = __version__
        self.api_version = "1"
        self.ports = ports
        self.config = dict(config or {})
        self.events = None
        self.host = host

    def log(self, message: str, *, level: str = "info") -> None:
        getattr(logger, level, logger.info)("[%s] %s", self.owner, message)


@dataclass
class LoadedFeature:
    owner: str
    definition: FeatureDefinition
    path: Path
    runtime: Runtime | None = None
    commands: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeatureLoadResult:
    features: list[LoadedFeature] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def import_entrypoint(pack: Path, entrypoint: str, owner: str):
    """导入 mod 入口并返回工厂函数。

    ``__init__.py`` 入口按包加载（带 ``submodule_search_locations``），并先注册到
    ``sys.modules``，这样入口里的 ``from .runtime import ...`` 才能解析；普通
    ``.py`` 入口仍是单文件导入。
    """
    module_part, _, function_name = entrypoint.rpartition(":")
    if not module_part or not function_name:
        raise ValueError(f"invalid entrypoint: {entrypoint}")
    target = (pack / module_part).resolve()
    if pack.resolve() not in target.parents and target.parent != pack.resolve():
        raise ValueError("entrypoint escapes the package")
    if target.suffix != ".py":
        raise ValueError("entrypoint must be a .py file")
    module_name = "petcore_feature_" + "".join(
        character if character.isalnum() else "_" for character in owner
    )
    if target.name == "__init__.py":
        spec = importlib.util.spec_from_file_location(
            module_name,
            target,
            submodule_search_locations=[str(target.parent)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {target}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            for name in [
                name
                for name in sys.modules
                if name == module_name or name.startswith(module_name + ".")
            ]:
                sys.modules.pop(name, None)
            raise
    else:
        spec = importlib.util.spec_from_file_location(module_name, target)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {target}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    factory = getattr(module, function_name, None)
    if not callable(factory):
        raise AttributeError(f"entrypoint {entrypoint} is not callable")
    return factory


#: 兼容旧调用点；新代码用 :func:`import_entrypoint`。
_import_entrypoint = import_entrypoint


class FeatureManager:
    def __init__(self, ports: HostPorts, host=None):
        self.ports = ports
        self.host = host
        self.features: dict[str, LoadedFeature] = {}

    def load(
        self,
        *,
        pack: Path,
        owner: str,
        entrypoint: str,
        config: dict[str, Any] | None = None,
    ) -> list[str]:
        diagnostics: list[str] = []
        try:
            factory = import_entrypoint(pack, entrypoint, owner)
            context = FeatureContextImpl(owner, self.ports, config, host=self.host)
            definition = factory(context)
            if not isinstance(definition, FeatureDefinition):
                raise TypeError("create_host must return FeatureDefinition")
            feature = LoadedFeature(owner=owner, definition=definition, path=pack)
            self.features[owner] = feature
            if definition.runtime_factory is not None:
                runtime = definition.runtime_factory(context)
                feature.runtime = runtime
                feature.commands = dict(getattr(runtime, "commands", {}) or {})
                runtime.start()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - 单个扩展的任何异常都不许打断启动
            # `sys.exit()` / `SystemExit` 在扩展里很常见（"提前退出"写法），
            # 它们不是 `Exception`：不拦就会让整个 core 静默退出（审计 AUD-21）。
            diagnostics.append(f"{owner}: {type(exc).__name__}: {exc}")
            self.features.pop(owner, None)
        return diagnostics

    def load_builtin_demo(self) -> list[str]:
        pack = Path(__file__).resolve().parent.parent / "builtin" / "feature_demo"
        if not (pack / "mod.json").exists():
            return []
        return self.load(
            pack=pack,
            owner="core.feature-demo",
            entrypoint="host/factory.py:create_host",
            config={},
        )

    def load_builtin_features(self) -> list[str]:
        base = paths.builtin_features_dir()
        if not base.is_dir():
            return []
        diagnostics: list[str] = []
        for child in sorted(base.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or not (child / MOD_FILE).is_file():
                continue
            try:
                manifest, problems = load_manifest(child)
            except Exception as exc:
                diagnostics.append(f"{child.name}: {type(exc).__name__}: {exc}")
                continue
            if manifest is None:
                diagnostics.extend(f"{child.name}: {problem}" for problem in problems)
                continue
            if manifest.kind != "feature" or not manifest.entrypoint:
                diagnostics.append(f"{manifest.id}: not a loadable feature")
                continue
            diagnostics.extend(
                self.load(
                    pack=child,
                    owner=manifest.id,
                    entrypoint=manifest.entrypoint,
                    config={},
                )
            )
        return diagnostics

    def menu_contributions(self) -> list[tuple[LoadedFeature, Contribution]]:
        items: list[tuple[LoadedFeature, Contribution]] = []
        for feature in self.features.values():
            for contribution in feature.definition.contributions:
                if contribution.kind == "menu":
                    items.append((feature, contribution))
        return items

    def chat_views(self) -> list[ChatViewSpec]:
        """收集 `chat_view` 贡献（按 order 排序，`owner:id` 去重）。

        缺 `label` 或缺 widget 工厂的贡献跳过并记一条日志——契约要求两者成对，
        但一个扩展写错不该让其它扩展的视图一起消失。
        """
        specs: list[ChatViewSpec] = []
        seen: set[str] = set()
        for owner in sorted(self.features):
            feature = self.features[owner]
            definition = feature.definition
            factories = dict(definition.view_factories or {})
            for contribution in definition.contributions:
                if contribution.kind != "chat_view":
                    continue
                label = str(contribution.data.get("label") or "").strip()
                factory = factories.get(contribution.id)
                view_id = f"{owner}:{contribution.id}"
                if not label or not callable(factory):
                    logger.warning(
                        "chat view %s skipped: %s",
                        view_id,
                        "missing label" if not label else "missing view factory",
                    )
                    continue
                if view_id in seen:
                    continue
                try:
                    order = int(contribution.data.get("order") or 500)
                except (TypeError, ValueError):
                    order = 500
                seen.add(view_id)
                specs.append(
                    ChatViewSpec(
                        id=view_id,
                        owner=owner,
                        view_id=contribution.id,
                        label=label,
                        factory=factory,
                        icon=str(contribution.data.get("icon") or ""),
                        order=order,
                    )
                )
        specs.sort(key=lambda spec: (spec.order, spec.owner, spec.view_id))
        return specs

    def settings_pages(self) -> list[SettingsPageSpec]:
        pages: list[SettingsPageSpec] = []
        for feature in self.features.values():
            factory = feature.definition.settings_factory
            if factory is None:
                continue
            label, group = self._identity(feature)
            for contribution in feature.definition.contributions:
                if contribution.kind != "settings_page":
                    continue
                title = str(contribution.data.get("title") or contribution.id)
                pages.append(
                    SettingsPageSpec(
                        id=contribution.id,
                        title=title,
                        factory=factory,
                        icon=str(contribution.data.get("icon") or ""),
                        order=int(contribution.data.get("order") or 500),
                        owner=feature.owner,
                        label=label or title,
                        group=group,
                        host_page=str(contribution.data.get("host_page") or ""),
                    )
                )
        return pages

    def embedded_pages(self, host_page: str) -> list[SettingsPageSpec]:
        """Core 设置页要内嵌的特性页（已并入 Core 且声明了同一 host_page）。"""
        host_page = str(host_page or "")
        if not host_page:
            return []
        pages = [
            spec
            for spec in self.settings_pages()
            if spec.group == GROUP_CORE and spec.host_page == host_page
        ]
        pages.sort(key=lambda spec: (int(spec.order or 0), str(spec.id)))
        return pages

    def _identity(self, feature: LoadedFeature) -> tuple[str, str]:
        """扩展示意名（中文优先）与分组：已并入 Core / 随安装包 / 导入。"""
        manifest = None
        try:
            manifest, _problems = load_manifest(feature.path)
        except Exception:
            manifest = None
        label = ""
        path = feature.path
        if manifest is not None:
            label = str(manifest.name_zh or manifest.name or "")
            path = manifest.path
        if feature.owner in CORE_FEATURE_IDS:
            return label, GROUP_CORE
        return label, GROUP_BASE if _is_base_pack(path) else GROUP_ADDON

    def command(self, owner: str, command_id: str):
        feature = self.features.get(owner)
        if feature is None:
            return None
        return feature.commands.get(command_id)

    def pet_added(self, window) -> None:
        """Notify runtimes that a new pet profile is now available."""
        for feature in self.features.values():
            callback = getattr(feature.runtime, "on_pet_added", None)
            if callable(callback):
                try:
                    callback(window)
                except Exception:
                    logger.warning("feature %s pet-added hook failed", feature.owner, exc_info=True)

    def pet_removed(self, window) -> None:
        """Notify runtimes before a pet profile is closed."""
        for feature in self.features.values():
            callback = getattr(feature.runtime, "on_pet_removed", None)
            if callable(callback):
                try:
                    callback(window)
                except Exception:
                    logger.warning("feature %s pet-removed hook failed", feature.owner, exc_info=True)

    def stop_owner(self, owner: str) -> None:
        """Stop this extension and revoke its deliveries before unloading."""
        # interaction 也要撤：否则旧运行时的点击/投喂回调仍挂在窗口上，
        # 重载后一次点击会触发两次（审计 AUD-22）。
        for service in (self.ports.speech, self.ports.reminders, self.ports.interaction):
            cancel = getattr(service, "cancel_owner", None)
            if callable(cancel):
                cancel(owner)
        feature = self.features.pop(str(owner), None)
        if feature is None or feature.runtime is None:
            return
        try:
            feature.runtime.stop()
        except Exception:
            logger.debug("feature stop failed: %s", owner, exc_info=True)

    def stop(self) -> None:
        for owner in tuple(self.features):
            self.stop_owner(owner)
