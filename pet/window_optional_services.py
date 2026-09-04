# -*- coding: utf-8 -*-
"""PetWindow 可选后台服务的懒装配 mixin（Phase 1 门控）。

把主动识屏 / Agent 联动等“配置关闭就不构造”的生命周期逻辑从 window.py
拆出，避免继续撑大 window.py（架构红线：行数预算）。
"""
from __future__ import annotations

from typing import Any


class WindowFeatureGateMixin:
    """供 PetWindow 混入的可选服务懒装配能力。"""

    cfg: Any
    proactive_watcher: Any = None
    agent_link_manager: Any = None
    _broker_facade: Any = None

    # ------------------------------------------------------------ 判定
    def _proactive_wanted(self) -> bool:
        raw = self.cfg.get("proactive_screen", {})
        return bool((raw or {}).get("enabled", False))

    def _agent_link_wanted(self) -> bool:
        raw = self.cfg.get("agent_link", {})
        if not isinstance(raw, dict):
            return False
        for key in ("dsh", "claude", "cursor", "opencode"):
            if bool(raw.get(key, False)):
                return True
        return bool(raw.get("custom_agents"))

    # ------------------------------------------------------------ 懒创建
    def _ensure_proactive_watcher(self):
        """首次启用主动识屏时懒创建观察器；已存在则原样返回。"""
        if self.proactive_watcher is None:
            from .proactive import ProactiveScreenWatcher
            self.proactive_watcher = ProactiveScreenWatcher(self, self.cfg)
        return self.proactive_watcher

    def _ensure_agent_link_manager(self):
        """首次启用 Agent 联动时懒创建管理器；已存在则原样返回。"""
        if self.agent_link_manager is None:
            from .agent_link import AgentLinkManager
            self.agent_link_manager = AgentLinkManager(self, self.cfg)
        return self.agent_link_manager

    # ------------------------------------------------------------ 同步
    def sync_optional_services(self) -> None:
        """设置刷新公共入口：按配置懒装配/同步主动识屏与 Agent 联动。"""
        if self._proactive_wanted():
            self._ensure_proactive_watcher().apply_config()
        elif self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()
        if self._agent_link_wanted():
            self._ensure_agent_link_manager().apply_config()
        elif self.agent_link_manager is not None:
            self.agent_link_manager.apply_config()

    def set_broker_facade(self, broker_facade: Any) -> None:
        """替换窗口持有的 broker facade（app 层经公开 seam 注入，不碰私有面）。"""
        self._broker_facade = broker_facade
