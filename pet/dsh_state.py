# -*- coding: utf-8 -*-
"""DSH 统一状态跟踪（第一版：状态联动 pipeline）。

数据源：随桌宠内置的 DSH 桥接插件 ``integrations/dsh-pet-bridge`` 写入的
``<数据基目录>/dsh-pet-bridge/dsh-{pid}.jsonl``（多实例分区写入；消费端
glob ``dsh*.jsonl``，兼容旧版单文件 ``dsh.jsonl``）。桥接插件订阅 DSH 真实事件
（``agent/status``、``session/event`` 的 ``turn/start`` / ``turn/end`` /
``tool/call`` / ``approval/asked`` / ``approval/decided`` / ``llm/retry`` …，
词汇见 DSH ``dsh-session/known-event-types``），以简单事件行追加写入。

本模块负责把桥接事件收敛为统一桌宠可见状态：

    offline / idle / thinking / working / waiting_approval / success / error

并保证：
- **edge-trigger**：仅当状态真正变化时才 emit 并写日志；同状态重复事件去重。
- **offline 恢复**：DSH 未运行（复用 ``harness_launcher.is_running`` 端口探测）
  或 bridge 无数据时切 offline；DSH 恢复后自动回 idle 并继续监听。
- **DSH 异常不拖垮桌宠**：任何 DSH/bridge 异常只记日志，绝不抛到桌宠主线程。
- 预留多 session 聚合 / subagent / 通知冷却 / 动画映射等扩展点（第一版不实现）。

对外仅通过 ``state_changed`` 信号暴露状态变化；app.py 只负责订阅。
"""

from __future__ import annotations

import json
import logging
import os
import time
import weakref
from enum import Enum
from pathlib import Path
from threading import Thread
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from . import harness_launcher
from .agent_link import DirGlobTailer

log = logging.getLogger("dsh-pet-standalone")

# 存活 tracker 登记（弱引用）：供测试/退出路径统一收口在线探测线程。
# 与 collision_ipc._live_sessions / agent_link 的 _shutdown_live_for_tests
# 同一防线：QTimer 停了还不够——在途 socket 探测线程必须作废其结果，
# 否则 teardown 里 QObject 销毁后 worker 仍跨线程 emit（macOS 全量 segfault 族）。
_LIVE_TRACKERS: "weakref.WeakSet[DshStateTracker]" = weakref.WeakSet()

# 桥目录与桌宠变体无关（与 DshMonitor 同约定）：config.dir.parent 即数据基目录
BRIDGE_DIR_NAME = "dsh-pet-bridge"
BRIDGE_FILE_NAME = "dsh.jsonl"

# 在线探测 / 事件 tail 的轮询间隔
_ONLINE_POLL_MS = 3000          # DSH 端口在线探测
_EVENT_POLL_MS = 1200           # 桥接事件 tail
# 审批锁存兜底：若 approval/decided 长时间未到，强制解除审批态回 working，
# 避免桌宠卡死在 waiting_approval（DSH 异常漏发 decided 的防线）。
_APPROVAL_LATCH_TIMEOUT_S = 120.0


class DshState(str, Enum):
    """桌宠可见的 DSH 统一状态。"""
    OFFLINE = "offline"
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_QUESTION = "waiting_question"
    SUCCESS = "success"
    ERROR = "error"


# AgentStatus 记录的 state 字段 → 统一状态
_AGENT_STATUS_STATE = {
    "idle": DshState.IDLE,
    "working": DshState.WORKING,
    "thinking": DshState.THINKING,
    "waiting_approval": DshState.WAITING_APPROVAL,
    "waiting_question": DshState.WAITING_QUESTION,
    "success": DshState.SUCCESS,
    "error": DshState.ERROR,
}

# 桥接「简单事件」（DSH 原始 session/event 类型）→ 统一状态。
# 事件名来自 DSH dsh-session/known-event-types.js 的真实词汇。
_EVENT_TO_STATE = {
    # 用户提交 → 思考（真人消息）：桥接转发 user/message，语义为"开始思考"。
    # assistant/chunk（流式）与 plan/mode **不被桥接转发**（impl L978 明确
    # 不转发流式），不得留在状态表（dead key——会让状态表看起来覆盖了并不
    # 存在的事件，测试 test_dead_event_keys_removed_from_state_map 锁死）。
    "user/message": DshState.THINKING,
    # turn/start：回合**开始执行**，属 working，不是思考。
    # 启动/新回合瞬间 DSH 会先发 AgentStatus working 再发 turn/start（间隔
    # ~2ms），若 turn/start 标 thinking 会把刚亮的 working 顶掉——用户看到
    # 「启动事件被 think 覆盖」（working 存活仅 1-2ms，约 1.2s 后又回 working）。
    # 思考语义由 user/message（真人消息）表达。
    "turn/start": DshState.WORKING,
    # 工具 / 步骤 / 命令执行 → working
    "assistant/message": DshState.WORKING,
    "tool/call": DshState.WORKING,
    "tool/result": DshState.WORKING,
    "step/start": DshState.WORKING,
    "step/end": DshState.WORKING,
    "command/run": DshState.WORKING,
    "command/done": DshState.WORKING,
    "tool-workflow/run-start": DshState.WORKING,
    "tool-workflow/run-end": DshState.WORKING,
    # 审批
    "approval/asked": DshState.WAITING_APPROVAL,
    "approval/request": DshState.WAITING_APPROVAL,  # 兼容旧一次性审批事件
    "approval/decided": DshState.WORKING,
    # 用户问题（ask_user_question 阻塞交互，与审批同等待遇）
    "question/requested": DshState.WAITING_QUESTION,
    "question/resolved": DshState.WORKING,
    # 完成 / 出错
    "turn/end": DshState.SUCCESS,
    "llm/retry": DshState.ERROR,
    "llm_error": DshState.ERROR,  # API 级错误（errorCode 为真实上游码如 bad_response_status_code）
    # 斜杠拼写保留为旧 producer 兼容（consumer 契约测试锁死 legacy slash 仍接受）。
    "llm/error": DshState.ERROR,
    # 上下文压缩（context compaction，DSH 长会话自动触发）：仍在工作。
    "context_compacted": DshState.WORKING,
}


def map_event_to_state(record: dict) -> Optional[DshState]:
    """把一条桥接记录映射为统一状态；无法识别返回 None（忽略）。

    支持两种记录形态（桥接插件当前实际写出的）：
    - ``{"event": "AgentStatus", "state": "working"}`` —— agent/status 聚合基线；
    - ``{"event": "tool/call", "tool": "..."}`` 等原始事件 —— session/event 转发。
    """
    if not isinstance(record, dict):
        return None
    event = str(record.get("event") or "")
    if event == "AgentStatus":
        return _AGENT_STATUS_STATE.get(str(record.get("state") or "").strip())
    return _EVENT_TO_STATE.get(event)


class DshStateTracker(QObject):
    """DSH 统一状态跟踪器：桥接事件 → 统一状态（edge-trigger + offline 恢复）。

    - 独立于 agent_link 的多 Agent 联动（默认关闭的那套动画联动），始终轻量运行；
    - 复用 agent_link.ByteOffsetTailer 读桥接文件、harness_launcher.is_running 探测在线；
    - 任何桥接/DSH 异常只记日志，绝不影响桌宠主循环。
    """

    # 状态变化信号：emit(from_state, to_state)；from_state 为 "" 表示首个状态
    state_changed = Signal(str, str)
    # 真人用户消息 → (session_id, text)：对话开始的稳定触发点，与状态边沿竞态
    # 解耦。agent.inject() 注入上下文（sourceKind=plugin）不发本信号、也不进
    # 状态机（system-reminder/技能目录/记忆等每轮 4-5 条，不能当真人输入）。
    # 旧版桥接记录无 sourceKind 字段：按真人消息兼容处理，绝不静默丢事件。
    user_message = Signal(str, str)

    # 后台线程完成在线探测后回主线程的结果（跨线程 emit，AutoConnection 队列投递）
    _online_checked = Signal(bool)

    def __init__(
        self,
        config_dir: Path,
        port: Optional[int] = None,
        parent: Optional[QObject] = None,
        clock=None,
        scan_interval: float = 5.0,
    ) -> None:
        super().__init__(parent)
        _LIVE_TRACKERS.add(self)
        self.config_dir = Path(config_dir)
        self.port = int(port) if port is not None else harness_launcher.DEFAULT_PORT
        self._clock = clock or time.monotonic

        self._bridge_dir = self.config_dir.parent / BRIDGE_DIR_NAME
        self._bridge_file = self._bridge_dir / BRIDGE_FILE_NAME
        # 多 DSH 实例分区写入（P0-2）：生产端每个实例写 dsh-{pid}.jsonl，
        # 消费端 glob 全部 dsh*.jsonl（兼容旧版单文件 dsh.jsonl）。
        # _bridge_file 保留为旧字段名（兼容），实际读取走 DirGlobTailer。
        self._tailer = DirGlobTailer(self._bridge_dir, pattern="dsh*.jsonl", scan_interval=scan_interval)

        # 统一状态（edge-trigger）。初始 None，使首个状态（offline/idle）也真正落日志
        self.current_state: Optional[DshState] = None

        # 阻塞型交互锁存（审批 / 用户问题同待遇）：
        # 进入 waiting_approval / waiting_question 后忽略 working/thinking，
        # 直到对应的 decided / resolved 到来解除。
        self._pending_approval = False
        self._approval_since: Optional[float] = None
        self._pending_question = False
        self._question_since: Optional[float] = None

        self._online: Optional[bool] = None  # 上次在线探测结果（用于 edge 触发 offline/idle）
        self._probe_inflight = False  # 后台探测是否在途（防并发探测）
        self._probe_generation = 0    # 探测代次：stop() 自增，作废在途结果
        self._started = False

        self._online_timer = QTimer(self)
        self._online_timer.setInterval(_ONLINE_POLL_MS)
        # 在线探测走后台线程（socket 探测关着的端口可能等 ~250ms，不能卡 Qt 主线程）
        self._online_timer.timeout.connect(self._schedule_online_probe)
        self._online_checked.connect(self._on_online_checked)

        self._event_timer = QTimer(self)
        self._event_timer.setInterval(_EVENT_POLL_MS)
        self._event_timer.timeout.connect(self._poll_events)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._tailer.reset()
        # 首次在线探测立即执行并落第一个状态（offline / idle）
        self._poll_online()
        if not self._online_timer.isActive():
            self._online_timer.start()
        if not self._event_timer.isActive():
            self._event_timer.start()
        log.debug("DSH 状态跟踪器已启动（bridge=%s, port=%s）", self._bridge_file, self.port)

    def stop(self) -> None:
        self._started = False
        self._online_timer.stop()
        self._event_timer.stop()
        # 在途在线探测结果作废：worker 可能正阻塞在 socket 连接上，回来后
        # 若仍跨线程 emit 到已停止/已销毁的 QObject，会与 Qt 收尾竞争
        # （macOS 全量 teardown segfault：_probe_worker 在 socket.close 里挂起、
        # 主线程 processEvents 销毁窗口）。换代让迟到结果静默丢弃。
        self._probe_generation += 1
        self._probe_inflight = False

    def pause(self) -> None:
        """桌宠隐藏时可暂停（低功耗）：事件与在线探测都停。"""
        self._online_timer.stop()
        self._event_timer.stop()

    def resume(self) -> None:
        if self._started:
            self._online_timer.start()
            self._event_timer.start()

    # ------------------------------------------------------------ 状态推进
    def _transition(self, to_state: DshState) -> None:
        """edge-trigger 状态切换：同状态去重，真正变化才 emit + 写日志。"""
        if to_state is self.current_state:
            return
        from_state = self.current_state  # 切换前状态（即上一步的当前态）
        self.current_state = to_state

        if from_state is None:
            log.info("[DSH STATE] %s", to_state.value)
        else:
            log.info("[DSH STATE] %s -> %s", from_state.value, to_state.value)
        self.state_changed.emit(
            "" if from_state is None else from_state.value,
            to_state.value,
        )

    def _release_approval(self) -> None:
        self._pending_approval = False
        self._approval_since = None

    def _release_question(self) -> None:
        self._pending_question = False
        self._question_since = None

    def _handle_record(self, record: dict) -> None:
        """处理一条桥接记录：阻塞型交互锁存感知的状态推进。

        审批与用户问题（ask_user_question）都是「阻塞 Agent 等待用户输入」的
        交互，统一按锁存处理：进入 waiting_approval / waiting_question 后忽略
        working/thinking，直到对应的 decided / resolved 到来解除。
        """
        event = str(record.get("event") or "")
        state = map_event_to_state(record)
        if state is None:
            return

        # 真人用户消息：发 user_message 信号（对话开始的稳定触发点）。
        # agent.inject() 注入上下文（sourceKind=plugin）整体忽略——不进状态机、
        # 不算对话开始，防止每轮 4-5 条 system-reminder/技能目录记录把「对话
        # 开始」触发改成随机的（同 tick 批量应用 + 边沿去重 → 时触时不触）。
        if event == "user/message":
            if str(record.get("sourceKind") or "") == "plugin":
                return
            self.user_message.emit(
                str(record.get("sessionId") or record.get("session_id") or ""),
                str(record.get("text") or record.get("content") or record.get("summary") or ""),
            )

        # approval/decided：解除审批锁存，回到 working（agent 仍在 running）
        if event == "approval/decided":
            was_latched = self._pending_approval
            self._release_approval()
            if was_latched:
                self._transition(DshState.WORKING)
            return

        # question/resolved：解除问题锁存，回到 working
        if event == "question/resolved":
            was_latched = self._pending_question
            self._release_question()
            if was_latched:
                self._transition(DshState.WORKING)
            return

        # approval/asked：进入审批锁存
        if state is DshState.WAITING_APPROVAL:
            self._pending_approval = True
            self._approval_since = self._clock()
            self._transition(state)
            return

        # question/requested：进入问题锁存
        if state is DshState.WAITING_QUESTION:
            self._pending_question = True
            self._question_since = self._clock()
            self._transition(state)
            return

        # 任一锁存中：忽略一切非阻塞事件（防 waiting_approval / waiting_question
        # 被 working / thinking 顶掉）
        if self._pending_approval or self._pending_question:
            return

        self._transition(state)

    # ------------------------------------------------------------ 轮询
    def _candidate_ports(self) -> list[int]:
        """待探测的 DSH 端口候选。

        DSH 可能跑在 3080（web 真实默认）或 38080（桌宠 harness_launcher 启动
        时的避让端口），也可能通过 DSH_PORT 环境变量指定——全部探测，任一在线
        即视为 DSH 在线（兼容「用户自己开的 DSH」与「桌宠一键启动的 DSH」）。
        """
        ports: set[int] = set()
        if self.port is not None:
            ports.add(int(self.port))
        env_port = os.environ.get("DSH_PORT")
        if env_port:
            try:
                ports.add(int(env_port))
            except (TypeError, ValueError):
                pass
        ports.add(3080)   # DSH web 默认端口
        ports.add(38080)  # harness_launcher 启动默认端口
        return sorted(ports)

    def _detect_online(self) -> bool:
        """同步探测任一候选端口在线即视为 DSH 在线。异常绝不外抛。"""
        try:
            return any(harness_launcher.is_running(p) for p in self._candidate_ports())
        except Exception:
            log.exception("DSH 在线探测异常")
            return False

    def _apply_online(self, online: bool) -> None:
        """把在线探测结果落到状态机：离线 → offline（清审批锁存），在线且无活动 → idle。"""
        self._online = online
        if not online:
            # DSH 未运行：审批/问题锁存一并清除，切 offline
            self._release_approval()
            self._release_question()
            self._transition(DshState.OFFLINE)
        else:
            # DSH 在线但没有任何活跃 Agent/事件：基线 idle（含从 None / offline 恢复）
            if self.current_state in (None, DshState.OFFLINE):
                self._transition(DshState.IDLE)

    def _poll_online(self) -> None:
        """同步在线探测 → offline / idle 基线（启动首查与测试直接调用）。"""
        self._apply_online(self._detect_online())

    def _schedule_online_probe(self) -> None:
        """QTimer 路径：在线探测放后台线程，避免 socket 探测阻塞 Qt 主线程卡动画。

        Windows 回环对关着的端口不会立即 RST，探测可能等 ~250ms；若在主线程
        每 3 秒一次这样探测，会让桌宠画面周期性卡顿。后台线程 + 信号回主线程
        完全消除该阻塞；探测结果经 ``_online_checked`` 队列投递回主线程应用。
        """
        if not self._started:
            return  # stop() 后 QTimer 已停；防御旧接线直接调用
        if self._probe_inflight:
            return  # 上一轮探测仍在途，跳过本次（3s 间隔足够，不会漏）
        self._probe_inflight = True
        ports = self._candidate_ports()
        generation = self._probe_generation
        try:
            worker = Thread(
                target=self._probe_worker,
                args=(generation, ports),
                daemon=True,
                name="dsh-online-probe",
            )
            worker.start()
        except Exception:
            self._probe_inflight = False
            log.exception("DSH 在线探测线程启动失败")
            self._apply_online(False)

    def _probe_worker(self, generation: int, ports: list[int]) -> None:
        """后台线程里跑端口探测，结果 emit 回主线程。任何异常都不外抛。

        携带代次：探测期间若 tracker 被 stop()（测试 teardown / 应用退出），
        回来后代次不匹配直接丢弃结果，绝不对已停止对象跨线程 emit。
        """
        if generation != self._probe_generation or not self._started:
            return  # stop() 后的迟到结果：静默丢弃
        try:
            online = any(harness_launcher.is_running(p) for p in ports)
        except Exception:
            log.exception("DSH 在线探测线程异常")
            online = False
        if generation != self._probe_generation or not self._started:
            return  # 探测期间被 stop()：结果作废，不 emit
        try:
            self._online_checked.emit(online)
        except Exception:
            log.debug("DSH 在线探测结果投递失败", exc_info=True)

    def _on_online_checked(self, online: bool) -> None:
        """主线程收到后台探测结果：应用并清在途标记。"""
        self._probe_inflight = False
        self._apply_online(online)

    def _poll_events(self) -> None:
        """tail 桥接事件并推进状态；阻塞型交互锁存超时兜底。"""
        # 审批锁存超时兜底：DSH 漏发 approval/decided 时强制回到 working
        if self._pending_approval and self._approval_since is not None:
            if self._clock() - self._approval_since >= _APPROVAL_LATCH_TIMEOUT_S:
                log.warning("DSH 审批锁存超时，强制解除审批态")
                self._release_approval()
                self._transition(DshState.WORKING)

        # 问题锁存超时兜底：DSH 漏发 question/resolved 时强制回到 working
        if self._pending_question and self._question_since is not None:
            if self._clock() - self._question_since >= _APPROVAL_LATCH_TIMEOUT_S:
                log.warning("DSH 问题锁存超时，强制解除问题态")
                self._release_question()
                self._transition(DshState.WORKING)

        if self._online is False:
            # DSH 离线时不消费事件（离线态优先）
            return

        try:
            lines = self._tailer.read_new_lines()
        except Exception:
            log.exception("DSH 桥接事件读取异常")
            return
        for line in lines:
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                self._handle_record(record)
            except Exception:
                # 单条坏事件不影响后续；记日志即可，绝不让 DSH 数据拖垮桌宠
                log.debug("DSH 桥接事件解析失败: %r", line, exc_info=True)


def _shutdown_live_for_tests() -> None:
    """测试收口：把仍存活的 DshStateTracker 全部 stop()（QTimer 停 + 在途
    在线探测作废）。与 conftest 里 collision/agent_link/library 的收口同一防线，
    防止 teardown 时后台 socket 探测线程对已销毁 QObject 跨线程 emit。"""
    for tracker in list(_LIVE_TRACKERS):
        try:
            tracker.stop()
        except Exception:
            log.debug("DSH 状态跟踪器测试收口失败", exc_info=True)
