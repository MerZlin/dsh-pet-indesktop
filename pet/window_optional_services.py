# -*- coding: utf-8 -*-
"""PetWindow 可选后台服务/效果控制器的懒装配 mixin（Phase 1 门控）。

把主动识屏 / Agent 联动 / 文件投喂 / 黄金回旋 / 边缘探头等“配置关闭就不构造”
的生命周期逻辑从 window.py 拆出，避免继续撑大 window.py（架构红线：行数预算）。
"""
from __future__ import annotations

from typing import Any

from .window_effects import (
    begin_rotation,
    end_rotation,
    unrotate_point,
)


class WindowFeatureGateMixin:
    """供 PetWindow 混入的可选服务/效果懒装配能力。"""

    cfg: Any
    proactive_watcher: Any = None
    agent_link_manager: Any = None
    _file_eater: Any = None
    _broker_facade: Any = None
    _golden_spin: Any = None
    _edge_probe: Any = None

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

    # ------------------------------------------------------------ 文件投喂
    def install_file_eater(self):
        """挂载“吃垃圾文件”拖放处理器（幂等，只对 PetWindow 实例调用）。"""
        if self._file_eater is None:
            from .file_eater import FileEaterDropHandler
            self._file_eater = FileEaterDropHandler(self)
        return self._file_eater

    # ------------------------------------------------------------ 黄金回旋/边缘探头
    def _install_effect_services(self):
        """安装效果控制器（幂等）。PetWindow 构造末尾调用一次。"""
        if self._edge_probe is None:
            from .edge_probe import EdgeProbeController
            self._edge_probe = EdgeProbeController(self)
        if self._golden_spin is None:
            from .golden_spin import GoldenSpinController
            self._golden_spin = GoldenSpinController(self)
        return self

    def trigger_golden_spin(self) -> None:
        """右键菜单入口：立即开始黄金回旋（边缘探头激活时不叠加）。"""
        self._install_effect_services()
        if self._edge_probe.active:
            return
        self._golden_spin.cancel_pending()
        self._golden_spin.start()

    def set_edge_probe_enabled(self, on: bool) -> None:
        """右键菜单/设置开关：写配置并同步控制器。"""
        self._install_effect_services()
        on = bool(on)
        self.cfg.set("edge_probe_enabled", on)
        self.cfg.save()
        self._edge_probe.set_enabled(on)

    # ------------------------------------------------------------ 窗口钩子
    def _effects_probe_active(self) -> bool:
        return bool(getattr(self, "_edge_probe", None) and self._edge_probe.active)

    def _effects_current_angle(self) -> float:
        if self._effects_probe_active():
            return float(self._edge_probe.current_angle_deg())
        spin = getattr(self, "_golden_spin", None)
        if spin is not None and spin.active:
            return float(spin.current_angle_deg())
        return 0.0

    def _effects_paint(self, painter, rect) -> None:
        """paintEvent / _sync_mask 共用：进入旋转坐标系。"""
        angle = self._effects_current_angle()
        if abs(angle) > 1e-6:
            begin_rotation(painter, rect, angle)

    def _effects_paint_end(self, painter, rect) -> None:
        angle = self._effects_current_angle()
        if abs(angle) > 1e-6:
            end_rotation(painter, angle)

    def _effects_untransform(self, point, rect):
        """命中测试逆变换：把窗口逻辑点映射回未旋转坐标系。"""
        return unrotate_point(point, rect, self._effects_current_angle())

    def _effects_filter_switch(self, name: str) -> str:
        """边缘探头会话期间只允许待机/转向动画；其它请求降级到随机待机。"""
        if not self._effects_probe_active():
            return name
        idles = list(getattr(self, "idles", ()) or ())
        turns = list(getattr(self, "turns", ()) or ())
        if name in idles or name in turns or not idles:
            return name
        return self._pick(idles)

    def _effects_consume_click(self) -> bool:
        """点击事件先给效果层消费；边缘探头点击返回 True（普通点击不再触发）。"""
        if self._effects_probe_active():
            self._edge_probe.on_clicked()
            return True
        return False

    def _effects_route_click_golden_spin(self) -> bool:
        """点击触发黄金回旋路由。

        - 直连模式（golden_spin_direct）或角色无点击素材时：立即 spin_direct()
          并返回 True，调用方不再播放 Q 弹/点击素材。
        - armed 模式（有点击素材且未开启直连）：arm_after_click() 并返回 False，
          调用方继续播点击动画，播完后由 _effects_on_click_anim_finished 接续。
        - 未开启“点击触发黄金回旋”时返回 False，调用方走普通点击链路。
        """
        if self._effects_probe_active():
            return False
        if not bool(self.cfg.get("golden_spin_on_click", False)):
            return False
        spin = getattr(self, "_golden_spin", None)
        if spin is None:
            return False
        direct = bool(self.cfg.get("golden_spin_direct", False))
        has_clips = bool(getattr(self, "clicks", None))
        if not direct and has_clips:
            spin.arm_after_click()
            return False
        spin.cancel_pending()
        spin.spin_direct()
        return True

    def _effects_on_click_anim_finished(self) -> None:
        spin = getattr(self, "_golden_spin", None)
        if spin is not None:
            spin.consume_click_finished()

    def _effects_on_drag_started(self) -> None:
        if self._effects_probe_active():
            self._edge_probe.on_drag_started()

    def _effects_on_release(self, was_dragging: bool) -> None:
        edge = getattr(self, "_edge_probe", None)
        if edge is not None:
            edge.on_release(bool(was_dragging))

    def _effects_on_hidden(self) -> None:
        edge = getattr(self, "_edge_probe", None)
        if edge is not None:
            edge.pause()
        spin = getattr(self, "_golden_spin", None)
        if spin is not None:
            spin.cancel()

    def _effects_on_shown(self) -> None:
        edge = getattr(self, "_edge_probe", None)
        if edge is not None:
            edge.resume()

    def _effects_skip_turn_facing(self) -> bool:
        return self._effects_probe_active()

    # ------------------------------------------------------------ 同步
    def sync_optional_services(self) -> None:
        """设置刷新公共入口：按配置懒装配/同步主动识屏、Agent 联动与效果控制器。"""
        if self._proactive_wanted():
            self._ensure_proactive_watcher().apply_config()
        elif self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()
        if self._agent_link_wanted():
            self._ensure_agent_link_manager().apply_config()
        elif self.agent_link_manager is not None:
            self.agent_link_manager.apply_config()
        self._install_effect_services()
        self._edge_probe.set_enabled(bool(self.cfg.get("edge_probe_enabled", False)))

    def set_broker_facade(self, broker_facade: Any) -> None:
        """替换窗口持有的 broker facade（app 层经公开 seam 注入，不碰私有面）。"""
        self._broker_facade = broker_facade
