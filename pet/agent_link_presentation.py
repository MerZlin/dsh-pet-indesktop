# -*- coding: utf-8 -*-
"""AgentLink 呈现层（批6-5：气泡 / 音效 / 动画调度）。

职责边界（与 AgentLinkReducer / AgentLinkManager 的分工）：
- 本模块只负责把状态机结果呈现到桌宠：联动动作轮换、开始干活 / 完成通知 /
  过程汇报气泡、生命周期音效与动画衔接；
- 一律经 PetWindow 公开 seam 操作（request_link_anim / request_link_idle /
  show_bubble / mark_activity / clear_pending_link_anim 等），不读事件文件、
  不做状态聚合；
- 唯一私有访问：气泡位占用检测读取 win._bubble_busy_until（既有行为，测试
  夹具按该私有字段断言，PetWindow 暂无对应公开 seam——见 show_link_bubble）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer

from .agent_link_reducer import BUSY_STATES

log = logging.getLogger("dsh-pet-standalone")


class AgentLinkPresentation(QObject):
    """Agent 联动呈现层：气泡 / 音效 / 动画调度（只经 PetWindow 公开 seam 操作）。"""

    # 过程汇报：工具名 → 用户可读文案（不展示原始命令/路径）
    TOOL_LABELS = {
        "read": "正在读文件", "write": "正在写文件", "edit": "正在改代码",
        "notebookedit": "正在改代码", "bash": "正在跑命令", "shell": "正在跑命令",
        "pwsh": "正在跑命令", "powershell": "正在跑命令",
        "grep": "正在搜索", "glob": "正在搜索", "search": "正在搜索",
        "memory_search": "正在翻记忆",
        "webfetch": "正在查网页", "websearch": "正在查网页",
        "fetch": "正在查网页", "browser": "正在查网页", "web_fetch": "正在查网页",
        "web_search": "正在查网页", "read_page": "正在读网页",
        "task": "正在派活给子代理", "todowrite": "正在列计划",
    }
    _UNKNOWN_TOOL_LABEL = "正在调用工具"
    _ACTIVITY_MIN_INTERVAL = 10.0    # 同 Agent 过程气泡最小间隔
    _ACTIVITY_GLOBAL_MIN = 8.0       # 全局最小间隔（多 Agent 并发防刷屏）
    _ACTIVITY_SAME_LABEL = 60.0      # 同一工具文案 60s 内不重复
    # 各 Agent 的默认 thinking 文案；DSH 用角色梗，其他用烧烤梗
    _THINKING_DEFAULTS = {"dsh": "大肥鱼正在深度思考……"}

    # 联动动作池（写代码/吃Token 交替为主，每第 3 次插播短摸鱼）
    _LINK_MAIN = ("写代码", "吃Token")
    _LINK_BREAK = ("轻快记录", "漂浮踏步")
    _LINK_MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
    _LINK_BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

    def __init__(self, window: Any, config: Any, names: dict[str, str], *,
                 clock: Callable[[], float] = time.time, parent=None) -> None:
        super().__init__(parent)
        self.win = window
        self.cfg = config
        self.agent_names = names
        self._clock = clock
        self._sound_last_at: dict[str, float] = {}
        self._sound_last_event: dict[str, tuple[str, float]] = {}
        self._link_seq = 0                           # 联动动作轮换计数
        # 过程汇报气泡：agent → (上次文案, 时刻)；全局最后一条时刻
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0

    # ---------------- Reducer 效果槽 ----------------

    def on_activity(self) -> None:
        """联动事件 = 联动活动：刷新桌宠闲置降帧的活跃锚点。"""
        mark = getattr(self.win, "mark_activity", None)
        if callable(mark):
            mark()

    def on_sound_event(self, event_name: str, agent_key: str) -> None:
        """生命周期音效请求（start/error/done）。"""
        self._emit_sound(event_name, agent_key)

    def on_state_applied(self, agent_key: str, state: str, prev_raw: str | None) -> None:
        """去抖/节流后的状态 -> 桌宠行为映射（手册 §8.2）。"""
        if state in ("thinking", "working"):
            # busy 动作池轮换（写代码/吃Token 为主，每第 3 次插播短摸鱼），
            # 经 request_link_anim 平滑衔接：正在播的一次性动作不被打断
            anim = self.next_link_anim_rotation()
            if anim and hasattr(self.win, "request_link_anim"):
                self.win.request_link_anim(anim)
            self._maybe_notify_start(agent_key, prev_raw, state)
        elif state == "attention":
            # busy 后的 attention（如 Claude Stop=回合结束）由完成确认流程接管，
            # 避免「需要看一眼」和「完成通知」双气泡；独立出现的才立即提醒
            if prev_raw not in BUSY_STATES:
                self.show_link_bubble("主人，Agent 这边需要你看一眼～", important=True)
        elif state == "error":
            if prev_raw not in BUSY_STATES:
                self.show_link_bubble("Agent 执行好像遇到报错了…", important=True)
        elif state in ("sleeping", "idle"):
            # 回到待机：一次性动作播完自然回，待机/移动中立即回
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()

    def on_done_bubble(self, agent_key: str, text: str, others_busy: bool) -> None:
        """完成确认气泡 + 待机恢复（必须走 request_link_idle 而不是裸 _switch——
        否则残留的 link 状态会把以后的普通同名动作劫持进联动链）。"""
        # 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）
        if not others_busy and hasattr(self.win, "request_link_idle"):
            self.win.request_link_idle()
        self.show_link_bubble(text, important=True)

    # ---------------- 工具过程汇报（Manager 代次校验后调用） ----------------

    def on_tool_activity(self, agent_key: str, tool: str) -> None:
        """过程汇报气泡（可选，默认关）：「DSH 正在读文件…」这类。
        白名单工具映射 + 三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。"""
        # 工具活动 = 联动事件：刷新闲置降帧的活跃锚点（Agent 正在调工具干活）
        mark = getattr(self.win, "mark_activity", None)
        if callable(mark):
            mark()
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_activity", False):
            return
        label = self.TOOL_LABELS.get(str(tool).strip().lower(), self._UNKNOWN_TOOL_LABEL)
        now = self._clock()
        last = self._last_activity.get(agent_key)
        if last is not None:
            if last[0] == label and now - last[1] < self._ACTIVITY_SAME_LABEL:
                return
            if now - last[1] < self._ACTIVITY_MIN_INTERVAL:
                return
        if now - self._activity_global_last < self._ACTIVITY_GLOBAL_MIN:
            return
        self._last_activity[agent_key] = (label, now)
        self._activity_global_last = now
        name = self.agent_names.get(agent_key, agent_key)
        # 低优先级：气泡位被占直接丢弃，不与重要气泡竞争
        self.show_link_bubble(f"{name} {label}…", important=False, duration_ms=2600)

    # ---------------- 联动动作池 ----------------

    def next_link_anim_rotation(self) -> str | None:
        """下一个联动动作：主动作严格交替；每第 3 次插播摸鱼（独立节奏）。"""
        acts = list(getattr(self.win, "cats", {}).get("acts", []) or [])
        main = [a for a in self._LINK_MAIN if a in acts]
        brk = [a for a in self._LINK_BREAK if a in acts]
        # 不同角色包的动作名不统一：精确名缺失时按语义关键词回退。
        if not main:
            main = [a for a in acts if any(k in a for k in self._LINK_MAIN_KEYWORDS)]
        if not brk:
            brk = [a for a in acts if any(k in a for k in self._LINK_BREAK_KEYWORDS)]
        # 角色包至少有一个动作时，确保 Agent 忙碌期间始终有可见反馈。
        if not main and not brk:
            main = acts
        if not main and not brk:
            return None
        self._link_seq += 1
        if brk and self._link_seq % 3 == 0:
            return brk[(self._link_seq // 3 - 1) % len(brk)]
        if main:
            return main[(self._link_seq - 1) % len(main)]
        return brk[(self._link_seq - 1) % len(brk)]

    def next_busy_anim(self, has_busy: Callable[[], bool]) -> str | None:
        """window 动画结束回调用：仍有 Agent 在忙 → 下一个联动动作；否则 None。
        全员空闲时重置轮换计数——下一个任务从「写代码」重新开始。"""
        if has_busy():
            return self.next_link_anim_rotation()
        self._link_seq = 0
        return None

    # ---------------- 气泡 ----------------

    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None, state: str = "working") -> None:
        """开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
        低优先级：气泡位被占时直接丢弃。thinking 状态用更有趣的文案。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_state", False):
            return
        if prev_raw in BUSY_STATES:
            return
        name = self.agent_names.get(agent_key, agent_key)
        if state == "thinking":
            self.show_link_bubble(self._thinking_text(agent_key), important=False, duration_ms=3000)
        else:
            self.show_link_bubble(f"{name} 开始干活啦～", important=False, duration_ms=3000)

    def _thinking_text(self, agent_key: str) -> str:
        """thinking 气泡文案：按 Agent 自定义 > 旧全局自定义 > 按 Agent 默认。"""
        agent_cfg = self.cfg.get("agent_link", {})
        custom = (agent_cfg.get("thinking_texts") or {}).get(agent_key, "").strip()
        # 兼容旧的全局 thinking_text 字段（设置页保存时已自动迁移）
        if not custom:
            custom = str(agent_cfg.get("thinking_text", "") or "").strip()
        if custom:
            name = self.agent_names.get(agent_key, agent_key)
            return custom.replace("{name}", name)
        if agent_key in self._THINKING_DEFAULTS:
            return self._THINKING_DEFAULTS[agent_key]
        name = self.agent_names.get(agent_key, agent_key)
        return f"{name} 正在深度烧烤……"

    def show_link_bubble(self, text: str, *, important: bool, duration_ms: int = 4500,
                         _retried: int = 0) -> None:
        """联动气泡：不顶掉正在占用气泡位的重要气泡（主动识屏/attention 等）。
        普通气泡直接让路丢弃；重要气泡每 2.5s 重试至多 4 次（约 10s 窗口），
        仍被占才放弃——主动识屏长答复可能占位 15-20s，单次重试不够用。"""
        if not hasattr(self.win, "show_bubble"):
            return
        # 气泡位占用检测：win._bubble_busy_until 无公开 seam（测试夹具按该
        # 私有字段断言），保持既有 getattr 行为不变；仅此一处私有访问。
        busy_until = getattr(self.win, "_bubble_busy_until", 0.0)
        if time.monotonic() < busy_until:
            if not important or _retried >= 4:
                return
            QTimer.singleShot(2500, self,
                              lambda t=text, n=_retried: self.show_link_bubble(
                                  t, important=True, _retried=n + 1))
            return
        self.win.show_bubble(text, duration_ms=duration_ms)

    def pause(self) -> None:
        """隐藏时清空待播联动动作与音效去重状态（完成计时器由 Reducer 取消）。"""
        if hasattr(self.win, "clear_pending_link_anim"):
            self.win.clear_pending_link_anim()
        self._sound_last_event.clear()

    # ---------------- 音效 ----------------

    def _emit_sound(self, event_name: str, agent_key: str) -> None:
        """播放 Agent 生命周期音效；所有 Agent 共用一组全局冷却。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("sound_enabled", False):
            return
        if not agent_cfg.get(f"sound_{event_name}_enabled", True):
            return
        path_value = str(agent_cfg.get(f"sound_{event_name}_path", "") or "").strip()
        if not path_value:
            return
        # 经 pet.agent_link 模块属性取函数：测试按模块名 patch play_sound /
        # resolve_builtin_sound，调用时解析才能命中（模块级 import 会绑定原函数）。
        from . import agent_link as _agent_link
        path = _agent_link.resolve_builtin_sound(path_value) \
            if path_value.startswith("builtin:") else Path(path_value).expanduser()
        if path is None or not path.is_file():
            return
        now = self._clock()
        cooldown = max(0.0, float(agent_cfg.get("sound_cooldown_seconds", 2.0)))
        if now - self._sound_last_at.get("global", float("-inf")) < cooldown:
            return
        last_event = self._sound_last_event.get(agent_key)
        if last_event is not None and last_event[0] == event_name and now == last_event[1]:
            return
        self._sound_last_at["global"] = now
        self._sound_last_event[agent_key] = (event_name, now)
        log.info("播放联动音效 event=%s agent=%s path=%s", event_name, agent_key, path)
        _agent_link.play_sound(path, volume=float(agent_cfg.get("sound_volume", 0.65)))
