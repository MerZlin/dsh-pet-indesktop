# -*- coding: utf-8 -*-
"""AgentLink 状态归约器（批6-5：从 AgentLinkManager 拆出的纯状态机）。

职责边界（与 AgentLinkPresentation / AgentLinkManager 的分工）：
- 本模块只做 busy/idle/attention/error 状态聚合、去抖、节流、完成确认
  （800ms 稳定窗口）、代次（gen）校验与完成冷却（cooldown）；
- 不触碰任何 UI（气泡/音效/动画）：效果一律经 Qt 信号发射，由
  AgentLinkPresentation 连接执行；
- 不读文件：事件文件读取全部在监视器 worker 线程内完成；
- 状态字段自 AgentLinkManager 逐字段等价迁移：_last_raw / _done_pending /
  _last_applied / 节流窗口（_min_interval）/ cooldown 数值一个不动。

依赖注入（保持纯状态机、不直接触碰监视器与窗口）：
- gen_provider(agent_key) -> int | None：该监视器当前发射代次（None=无此监视器）；
- running_provider(agent_key) -> bool | None：监视器是否在运行
  （None=无此监视器，按“在跑”处理——与既有 any_busy 语义一致）；
- visible_provider() -> bool：桌宠窗口当前是否可见（隐藏时事件不落地）；
- names：agent_key → 展示名（含自定义 Agent 合并结果，用于完成文案）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("dsh-pet-standalone")

# busy 状态集：Reducer 与 Presentation 共享的协议常量（模块级，避免层间经类属性反向依赖）
BUSY_STATES = ("working", "thinking")


class AgentLinkReducer(QObject):
    """Agent 联动状态归约器：纯状态机 + 效果信号发射。

    效果信号（GUI 线程内直连派发，与既有 emit→dispatch 语义一致）：
    - activity: 任意有效联动事件 → 刷新闲置降帧活跃锚点；
    - sound_event(event_name, agent_key): start/error/done 生命周期音效请求；
    - state_applied(agent_key, state, prev_raw): 去抖/节流后的 UI 行为请求；
    - done_bubble(agent_key, text, others_busy): 完成确认气泡请求。
    """

    activity = Signal()
    sound_event = Signal(str, str)
    state_applied = Signal(str, str, object)
    done_bubble = Signal(str, str, bool)

    _BUSY_STATES = BUSY_STATES  # 兼容旧引用；新代码用模块级 BUSY_STATES
    _DONE_CONFIRM_MS = 800   # busy→idle 稳定确认窗口（过滤 working→idle→working 抖动）
    _DONE_COOLDOWN_S = 5.0   # 同 Agent 完成气泡最小间隔（最后一道保险）

    # 进程名 → Agent：该 Agent 联动开启且正忙时，主动识屏跳过它的窗口
    # （联动气泡已在汇报进度，识屏再评一句就是重复打扰）。
    # opencode/cursor 有独立桌面进程按进程名识别；dsh 跑在浏览器/应用窗口里，
    # 按窗口标题识别；claude 在终端里标题不可控，不映射。
    AGENT_PROCESS_HINTS = {
        "opencode": ("opencode.exe",),
        "cursor": ("cursor.exe",),
    }
    AGENT_TITLE_HINTS = {
        "dsh": ("deepseek harness",),
    }

    def __init__(self, config: Any, gen_provider: Callable[[str], int | None],
                 running_provider: Callable[[str], bool | None],
                 visible_provider: Callable[[], bool], names: dict[str, str], *,
                 min_interval: float = 2.0, clock: Callable[[], float] = time.time,
                 parent=None) -> None:
        super().__init__(parent)
        self._cfg = config
        self._gen_provider = gen_provider
        self._running_provider = running_provider
        self._visible_provider = visible_provider
        self._names = names
        # 状态节流：同一 Agent 相同状态去抖；同 Agent 两次动作切换最小间隔
        # （Cursor 等 transcript 密集写入时防止动画"抽搐"）
        self._min_interval = float(min_interval)
        self._clock = clock
        self._last_applied: dict[str, tuple[str, float]] = {}
        # 原始状态流（不受去抖/节流影响）：用于 busy→idle 完成检测。
        # 不能用 _last_applied 做完成判定——节流会丢掉紧跟其后的 idle，导致完成通知丢失。
        self._last_raw: dict[str, str] = {}
        self._done_pending: dict[str, QTimer] = {}   # agent → 稳定确认定时器
        self._done_cooldown: dict[str, float] = {}   # agent → 上次完成气泡时刻
        self._saw_alert: set[str] = set()            # busy 周期内出现过 attention/error 的 Agent
        self._saw_error: set[str] = set()            # busy 周期内真正出现过 error 的 Agent

    # ---------------- 代次校验 ----------------

    def gen_current(self, agent_key: str, gen: int) -> bool:
        """校验发射代次是否仍是该监视器的当前代次（丢弃旧 worker 的迟到信号）。"""
        current = self._gen_provider(agent_key)
        return current is not None and gen == current

    # ---------------- 状态机 ----------------

    def on_state(self, agent_key: str, state: str, gen: int = 0) -> None:
        """接收 Agent 状态变更并调度效果（代次校验 + 可见性门控 + 去抖节流 + 完成确认）。

        代次校验：worker 线程发射带其启动代次，监视器重启后旧代次的迟到
        信号（已入 Qt 队列的）在此丢弃——发送端标志挡不住 emit→dispatch
        竞态，接收端校验是唯一的完整闸门（B9 设计评审结论）。
        """
        if not self.gen_current(agent_key, gen):
            return
        if not self._visible_provider():
            return
        # 联动状态事件 = 联动事件：刷新桌宠的闲置降帧活跃锚点（busy 与
        # 回到 idle 都算"有过联动活动"；持续 busy 由 any_busy() 门控兜底）
        self.activity.emit()

        now = self._clock()
        # --- 原始状态流（绕开去抖/节流）：busy→idle 完成检测 ---
        # 不能用 _last_applied 判定完成——节流会丢掉紧跟的 idle，导致完成通知丢失。
        prev_raw = self._last_raw.get(agent_key)
        self._last_raw[agent_key] = state
        if state in self._BUSY_STATES and prev_raw not in self._BUSY_STATES:
            self.sound_event.emit("start", agent_key)
        elif state == "error" and prev_raw != "error":
            self.sound_event.emit("error", agent_key)
        if state in self._BUSY_STATES:
            self._cancel_done_check(agent_key)
            self._saw_alert.discard(agent_key)
            if prev_raw != "error":
                self._saw_error.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in self._BUSY_STATES:
            self._saw_alert.add(agent_key)
            if state == "error":
                self._saw_error.add(agent_key)
            # Claude 的回合结束信号是 Stop→attention 而非 idle：busy 后的
            # attention/error 同样进入完成确认（800ms 内回忙则取消——例如
            # SubagentStop 后主 Agent 继续干活、工具报错后重试）。
            self._schedule_done_check(agent_key)
        elif state in ("idle", "sleeping") and prev_raw in self._BUSY_STATES:
            # working/thinking → idle：疑似任务完成，800ms 稳定确认
            # （过滤 working→idle→working 抖动；确认期间回忙则取消）
            self._schedule_done_check(agent_key)

        # 去抖：同一 Agent 连续相同状态只生效第一次
        last = self._last_applied.get(agent_key)
        if last is not None and last[0] == state:
            return
        # 节流：同一 Agent 两次动作/气泡切换最小间隔
        if last is not None and (now - last[1]) < self._min_interval:
            return
        self._last_applied[agent_key] = (state, now)

        log.debug("Agent 状态变更 [%s]: %s", agent_key, state)
        self.state_applied.emit(agent_key, state, prev_raw)

    # ---------------- 完成确认 ----------------

    def _schedule_done_check(self, agent_key: str) -> None:
        self._cancel_done_check(agent_key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self._DONE_CONFIRM_MS)
        timer.timeout.connect(lambda k=agent_key: self.fire_done(k))
        self._done_pending[agent_key] = timer
        timer.start()

    def _cancel_done_check(self, agent_key: str) -> None:
        timer = self._done_pending.pop(agent_key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def fire_done(self, agent_key: str) -> None:
        """800ms 稳定确认到期：期间回忙则不算完成；配置/冷却在弹出前再查。"""
        self._done_pending.pop(agent_key, None)
        if not self._visible_provider():
            return  # 隐藏中不弹不切（pause 已取消计时器，这里是兜底）
        if self._last_raw.get(agent_key) in self._BUSY_STATES:
            return
        if agent_key not in self._saw_error:
            self.sound_event.emit("done", agent_key)
        agent_cfg = self._cfg.get("agent_link", {})
        if not agent_cfg.get("notify_done", True):
            return
        now = self._clock()
        if now - self._done_cooldown.get(agent_key, 0.0) < self._DONE_COOLDOWN_S:
            return
        self._done_cooldown[agent_key] = now
        name = self._names.get(agent_key, agent_key)
        if agent_key in self._saw_alert:
            # busy 期间出现过 attention/error：不暗示"成功完成"
            text = f"{name} 那边停了，结果怎么样要主人自己看一眼哦"
        else:
            text = f"{name} 干完活啦，去看看成果吧～"
        self._saw_alert.discard(agent_key)
        self._saw_error.discard(agent_key)
        # 恢复待机动画：Claude 回合结束没有 idle 事件，不靠这步会一直停在干活动作。
        # 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）。
        # 必须走 request_link_idle（它会清 _link_anim_current 并尊重一次性动作），
        # 不能裸 _switch——否则残留的 link 状态会把以后的普通同名动作劫持进联动链。
        others_busy = any(k != agent_key and s in self._BUSY_STATES
                          for k, s in self._last_raw.items())
        if not others_busy:
            self._last_applied[agent_key] = ("idle", now)
        self.done_bubble.emit(agent_key, text, others_busy)

    def pause(self) -> None:
        """取消所有完成确认计时器（隐藏期间不得在隐藏窗口上切动画/弹气泡）。"""
        for key in list(self._done_pending):
            self._cancel_done_check(key)

    # ---------------- 忙碌聚合查询 ----------------

    def any_busy(self) -> bool:
        """任一已启用 Agent 正处于 busy（working/thinking）状态。

        供闲置降帧等"Agent 在干活 = 桌宠活跃"的判定使用：dsh 干活时桌宠
        视为活跃、不降帧。已停用监视器的残留状态不计入（关掉联动 = 不再
        被视为活跃，否则降帧开关会被僵尸 busy 永久顶掉）。
        """
        for key, state in self._last_raw.items():
            if state not in self._BUSY_STATES:
                continue
            if self._running_provider(key) is False:
                continue
            return True
        return False

    def has_any_busy_raw(self) -> bool:
        """原始状态流是否有任一 Agent 处于 busy（不过滤监视器运行态；动画衔接用）。"""
        return any(s in self._BUSY_STATES for s in self._last_raw.values())

    def busy_agent_owns_process(self, process_name: str, title: str = "") -> bool:
        """前台窗口是否属于「联动开启且正在忙」的 Agent（进程名或窗口标题命中）。"""
        agent_cfg = self._cfg.get("agent_link", {})
        p = str(process_name or "").lower()
        t = str(title or "").lower()
        for agent_key, procs in self.AGENT_PROCESS_HINTS.items():
            if p and p in procs and agent_cfg.get(agent_key) \
                    and self._last_raw.get(agent_key) in self._BUSY_STATES:
                return True
        for agent_key, needles in self.AGENT_TITLE_HINTS.items():
            if t and any(n in t for n in needles) and agent_cfg.get(agent_key) \
                    and self._last_raw.get(agent_key) in self._BUSY_STATES:
                return True
        return False
