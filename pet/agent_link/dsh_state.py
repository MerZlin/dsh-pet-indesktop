# -*- coding: utf-8 -*-
"""DSH 统一状态收敛（纯逻辑，无 Qt/线程/定时器）。

数据源：随桌宠内置的 DSH 桥接插件 ``integrations/dsh-pet-bridge`` 写入的
``<数据基目录>/dsh-pet-bridge/dsh-{pid}.jsonl``（多实例分区写入；消费端
glob ``dsh*.jsonl``，兼容旧版单文件 ``dsh.jsonl``）。

减法后（2026-10）本模块只剩**纯函数/纯类**：唯一的桥接读方是
``pet/agent_link.py`` 的 ``DshMonitor``（一个 DirGlobTailer + 一个轮询线程，
解析一次记录，分发给信号分派与本收敛器两个消费者）。本模块负责把桥接事件
收敛为统一桌宠可见状态：

    offline / idle / thinking / working / waiting_approval / waiting_question /
    success / error

并保证：
- **edge-trigger**：仅当状态真正变化时才产出；同状态重复事件去重。
- **offline 恢复**：DSH 未运行（端口探测，见 ``candidate_ports``）时切
  offline；DSH 恢复后自动回 idle 并继续监听。
- 阻塞型交互锁存：cordis/request-run（当前桥的权威审批请求）与 approval/request
  （旧桥/自定义通道的 UI 级审批事件）进入 waiting_approval、approval/decided 解锁；
  question/requested 进入 waiting_question、question/resolved 解锁。裸
  approval/asked **不参与锁存**（普通工具调用也会被宿主打上这个标记，见
  ``_EVENT_TO_STATE`` 的说明）；锁存超时兜底由 ``tick()`` 完成。

输出以元组列表返回（不直接 emit 信号，Qt 边界全部留在 DshMonitor）：
``("state", from_state, to_state, source_event)`` 与
``("user_message", session_id, text)``；from_state 为 "" 表示首个状态，
source_event 为触发该状态的桥接事件名（探活驱动的基线切换为 ""）。
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from enum import Enum
from typing import Optional

log = logging.getLogger("dsh-pet-standalone")

# 审批锁存兜底：若 approval/decided 长时间未到，强制解除审批态回 working，
# 避免桌宠卡死在 waiting_approval（DSH 异常漏发 decided 的防线）。
APPROVAL_LATCH_TIMEOUT_S = 120.0


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
    # 用户提交 / turn 开始 → 思考
    "user/message": DshState.THINKING,
    "turn/start": DshState.THINKING,
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
    # 审批（gated 口径，2026-10 修正）：
    # - approval/asked 只是 DSH 的会话/审计信号——普通工具调用也会被宿主打上
    #   这个标记，拿它锁存 waiting_approval 是误报源（桥端注释同款结论），
    #   因此**不收敛**（不在本表，记录被忽略）；
    # - 权威来源是 cordis/request-run（桥只在 requiresApproval 严格布尔 True 时
    #   才写，带 requestId）与旧式 approval/request（旧桥/自定义通道的 UI 级
    #   审批事件）；
    # - approval/decided / cordis/request-run-resolved → WORKING（解锁兼容）。
    "cordis/request-run": DshState.WAITING_APPROVAL,
    "cordis/request-run-resolved": DshState.WORKING,
    "approval/request": DshState.WAITING_APPROVAL,  # 兼容旧一次性审批事件
    "approval/decided": DshState.WORKING,
    # 用户问题（ask_user_question 阻塞交互，与审批同等待遇）
    "question/requested": DshState.WAITING_QUESTION,
    "question/resolved": DshState.WORKING,
    # 完成 / 出错（turn/end 带 reasonKind 时的收口见 map_event_to_state：
    # aborted → idle、error → error，两者都不出成功口径）
    "turn/end": DshState.SUCCESS,
    "llm/retry": DshState.ERROR,
    "llm_error": DshState.ERROR,  # API 级错误（errorCode 为真实上游码如 bad_response_status_code）
}


# turn/end 的 reasonKind（桥接透传 DSH 的 ``event.data.reason.kind``，见 index.js
# ``turnEndReasonKind``）里两个**非成功**终态：
# - aborted：用户主动中止 → 静默收尾，回待机、不报完成；
# - error：本回合以出错终止 → 错误终态。桥端只在「重试耗尽」或「本轮工具失败且无
#   成功」时才写 execution/failed；重试 < 4 或有工具成功时**不写任何抑制标记**，
#   此时把 turn/end 收敛成 success 就是误报成功（绿点 + done 音效 + 「干完活啦」）。
#   reasonKind 缺失 = 旧桥接，保持原 SUCCESS 行为。
_TURN_END_SILENT_REASONS = frozenset({"aborted"})
_TURN_END_ERROR_REASONS = frozenset({"error"})


def map_event_to_state(record: dict) -> Optional[DshState]:
    """把一条桥接记录映射为统一状态；无法识别返回 None（忽略）。

    支持两种记录形态（桥接插件当前实际写出的）：
    - ``{"event": "AgentStatus", "state": "working"}`` —— agent/status 聚合基线；
    - ``{"event": "tool/call", "tool": "..."}`` 等原始事件 —— session/event 转发。

    ``turn/end`` 的取值由 ``reasonKind`` 分流（见 ``_TURN_END_*_REASONS``）：aborted
    回待机、error 落错误终态，其余（completed / blocked / max-tokens / 缺失）仍是
    SUCCESS。成功口径只有这里一条判定——完成身份（``TurnCompletionTracker``）与呈现
    侧的完成提醒都以「映射结果 is SUCCESS」为准，所以失败回合不会漏出去。
    """
    if not isinstance(record, dict):
        return None
    event = str(record.get("event") or "")
    if event == "AgentStatus":
        return _AGENT_STATUS_STATE.get(str(record.get("state") or "").strip())
    if event == "cordis/request-run":
        # 双保险：桥只在 requiresApproval 严格布尔 True 时写这条记录；对旧桥/
        # 手写桩/自定义通道的平铺形状再核一次，非审批请求不锁存。
        nested = record.get("payload")
        if isinstance(nested, dict) and "requiresApproval" in nested:
            if nested.get("requiresApproval") is not True:
                return None
        elif record.get("requiresApproval") is not True:
            return None
    if event == "turn/end":
        reason_kind = str(record.get("reasonKind") or "").strip()
        if reason_kind in _TURN_END_SILENT_REASONS:
            return DshState.IDLE
        if reason_kind in _TURN_END_ERROR_REASONS:
            return DshState.ERROR
    return _EVENT_TO_STATE.get(event)


# 回合起点标记：live 读到即视为「本会话进入了一个新回合」。
# turn/start 与 user/message 都是桥接真实写出的事件（index.js：前者在
# STATE_EVENT_TYPES 里、后者显式转发），且两者都收敛为 THINKING/新对话。
_TURN_START_EVENTS = ("turn/start", "user/message")

# 桥接透传的**原始回合号**（DSH ``event.data.turn`` 的字符串形态，见 index.js
# ``sourceTurnOf``）：身份绑定的主键——同一 (会话, 回合号) 就是一个回合，不管它被
# 几个写者各写一份。注意**只有 turn/start / turn/end 带它**：真实 ``UserMessage``
# 是 ``{id, role, content, source}``，没有 turn 字段，因此 user/message 不带回合号
# 也不带 step（真实桥日志 213 条 user/message 无一例外），每回合还会有多条
# （真人 1 条 + agent.inject() 注入的上下文若干条）。
_SOURCE_TURN_FIELD = "sourceTurn"

# 会话表容量（身份 token 的有界声明）：每会话只留最新 token，超出按「最久没有
# live 起点」淘汰。桌宠是长跑进程，DSH 每换一次会话就是一个新 sessionId——不设
# 上界就是一条随会话数线性增长的内存。
TURN_TOKEN_MEMORY = 512

# (会话, 回合号) → token 绑定表的容量：一个会话里能同时记住的回合数。同一回合的
# 镜像副本/迟到重投必须落回同一个身份，靠的就是这条绑定；超出容量只丢最久没被引用的
# 绑定（丢的是记忆，不是身份唯一性——token 全局单调，见 ``_next_token``）。
TURN_BINDING_MEMORY = 512

# 真人输入的 sourceKind 白名单：DSH 的 ``UserMessage.source.kind`` 只有 "user" 是
# 真人敲进来的，其余取值都是 agent.inject() 注入的上下文（真实 DSH 里见到的还有
# plugin / agent-instructions / skill-catalog / skill-invocation …，桥端原样透传
# ``data.source.kind``）。空串兼容旧桥——旧桥不写 sourceKind 字段。
_HUMAN_MESSAGE_KINDS = frozenset({"user", ""})


def is_human_user_message(record: dict) -> bool:
    """这条 user/message 是不是真人敲进来的（白名单见 ``_HUMAN_MESSAGE_KINDS``）。

    白名单而不是黑名单：注入源的 kind 会随 DSH 版本增加（plugin → agent-instructions
    / skill-catalog / skill-invocation …），黑名单每漏一个就让一轮注入的 4-5 条
    「系统提示」被当成真人消息——「对话开始」触发随之变得时有时无。
    """
    if not isinstance(record, dict):
        return False
    return str(record.get("sourceKind") or "").strip() in _HUMAN_MESSAGE_KINDS


class TurnCompletionTracker:
    """live 回合收尾的身份追踪（纯逻辑：无 Qt、无线程、无时钟）。

    **两张账本都是全写者共享的**（不按写者分片）：同一 ``sessionId`` 是**同一条
    会话**，即使它同时被两个写者（多 DSH 实例镜像）各写一份。按写者分表会让
    「A 写的 turn/start」与「B 写的镜像 turn/start」各自发一次号，同一回合的两份
    ``turn/end`` 于是算出**两个不同身份**，消费侧去重失效 → 同一回合被提醒两次。
    会话没有写者 UUID，不能把计数器按写者切开。

    判据为什么不放在收敛状态上：``turn/end`` 不带源 ID（不像 ``tool/call`` 有
    callId），收敛器又是 edge-trigger——已停在 SUCCESS 时再收一条收尾零产出，
    而「桌宠中途接上 / 关开联动残留终态 / 聚合 idle 基线先到」这些来向上，呈现
    管线里的上一个状态都不是 busy。此时唯一稳定的事实是**这条 live 记录本身**。

    身份 = ``("turn-end", session_id, step, token)``：

    - ``session_id`` / ``step``：桥接 ``writeStateEvent`` 真实写出的字段
      （step = DSH 的 ``data.step``，桥接在它缺失时回退成 ``turn:<turn>``）；
    - ``token``：**全局单调 generation**——每个新回合递增一次。它把「同一回合的重复
      投递」（同一个回合号 → 同一个 token）与「同一会话的下一回合」（新回合号 → 更大的
      token）分开，并顺带兜住「两回合 step 取值相同」这一残量。

    **回合号绑定**（F2）：带 ``sourceTurn`` 的记录一律按 ``(会话, 回合号) → token`` 的
    绑定表定身份（有界，见 ``TURN_BINDING_MEMORY``）。同一回合被几个写者各写一份、或
    同一条起点被重新投递，都落回同一个 token——因此同一回合的两份 ``turn/end`` 算出
    同一个身份，消费侧去重仍然有效。**迟到的旧回合重投**（真实序列
    A:start7→A:end7→A:start8→B:start7→B:end7→A:end8）既不会重新发号，也不会把会话表里
    "当前回合"的记录顶回去——顶回去会让 turn8 的收尾退回 turn7 的 token，新回合被当成
    重复投递静默吞掉。**收尾用自己所属回合的 token**：turn/end 带回合号就查绑定表，
    查不到（起点被回填跳过 / 中途挂载）就地立绑定。

    **真实 user/message 不是回合入口**（X1）：真实 ``UserMessage`` 是
    ``{id, role, content, source}``，没有 turn 字段，所以桥接写出的 user/message 既不带
    step 也不带 sourceTurn，而且每回合有多条（真人 1 条 + agent.inject() 注入若干条）。
    它们在会话表里至多刷新 LRU：不重新发号（发号会让镜像副本把同一回合的 token 顶到新值
    上，同一回合出现两个完成身份），也不覆盖已记录的 step / owner / sourceTurn。只有
    「本会话还没有回合号记忆」（旧桥）时，真人消息才是新的回合入口。

    旧桥（无回合号）的镜像判定退回原判据（见 ``is_duplicate_start``）：两侧都是
    ``inst:`` 写者且归属不同。

    token 为什么必须是**全局**计数、而不是「每会话自增 + 只加容量」：消费侧
    （``AgentLinkManager._on_dsh_turn_completed``）按身份去重，身份相同 = 同一回合
    的重复投递 = 直接丢弃。每会话计数在会话被挤出表后会从 1 重来，新回合 step 与
    旧回合相同时身份就撞上**消费者已经见过**的那个，新回合被静默吞掉（无完成气泡、
    无 done 音效）。全局计数在实例生命周期内不复用，因此淘汰只丢「记忆」，不会
    造出假身份。

    有界：会话表 ≤ ``TURN_TOKEN_MEMORY`` 条、绑定表 ≤ ``TURN_BINDING_MEMORY`` 条，且
    会话表只由 live 起点/本会话消息写入——只见过收尾的会话（中途挂载，从没见过起点）
    token 恒为 0 且**不落表**，身份靠 session + step 逐条唯一（不按正文造 ID、不写任何
    持久状态）。``reset()`` 清两张表但同样不回收全局计数（见其文档）。

    两个字段都取不到时退回摄入游标 ``(文件, 文件身份, 行首偏移)``：同一行读两次
    已由摄入账本（``BaseAgentMonitor._event_ledger``）挡住，这里只保证身份唯一。

    追的是 **live** 记录：``ByteOffsetTailer`` 回填之后才读到的字节才会到达
    ``observe``——历史记录到不了这里，所以「返回身份」本身就等于「接上之后真的
    发生了一次回合收尾」。
    """

    def __init__(self) -> None:
        # 全局起点计数（整数，不随会话数占内存）：只增不减（含 reset），保证身份
        # token 在实例生命周期内不复用。
        self._generation = 0
        # 会话 → (该会话**当前回合**的 token, step, 写者归属, 回合号)。插入次序 = LRU
        # 次序（live 起点 / 本会话消息刷新，收尾只读不刷新——读不该驱动淘汰），淘汰端
        # 是最久没有活动的会话。step/owner/sourceTurn 只服务旧桥路径的镜像判定与"当前
        # 回合"归属，不参与身份。
        self._tokens: OrderedDict[str, tuple[int, str, str, str]] = OrderedDict()
        # (会话, 回合号) → token 的**身份绑定表**：带回合号的记录一律走它定身份，于是
        # 同一回合被几个写者各写一份都落在同一个 token 上，迟到的旧回合重投既不会重新
        # 发号、也不会顶掉当前回合的记录（F2）。
        self._turn_tokens: OrderedDict[tuple[str, str], int] = OrderedDict()

    def reset(self) -> None:
        """读方重启（监视器 start）时清两张表：上一代次的回合记忆不跨代次存活。

        **全局计数不回收**：回收会让重启后同一会话的下一回合重新拿到旧 token，而
        消费侧的身份记忆（``_dsh_turn_ids``）不随监视器生命周期清空——「关掉再开启
        联动、同一会话继续跑」时那正好把新回合当成重复投递吞掉。清表本身已足以隔离
        代次：没绑定过的回合 token 仍是 0，身份照旧由 session + step 唯一。
        """
        self._tokens.clear()
        self._turn_tokens.clear()

    # ---------------------------------------------------------------- 身份账本
    def _next_token(self) -> int:
        """发一个新身份 token（全局单调，实例生命周期内不复用）。"""
        self._generation += 1
        return self._generation

    def _remember_turn(self, session: str, entry: tuple[int, str, str, str]) -> None:
        """记下某会话的**当前回合**（重入表尾 = 最近使用），并守住容量上界。"""
        self._tokens.pop(session, None)
        self._tokens[session] = entry
        while len(self._tokens) > TURN_TOKEN_MEMORY:
            self._tokens.popitem(last=False)

    def _touch_turn(self, session: str) -> None:
        """本会话有活动（同回合内的消息 / 重投递）：只刷新 LRU，不动任何身份字段。"""
        if session in self._tokens:
            self._tokens.move_to_end(session)

    def _bound_token(self, session: str, source_turn: str) -> Optional[int]:
        """``(会话, 回合号)`` 已绑定的 token；没绑定过返回 None。"""
        key = (session, source_turn)
        token = self._turn_tokens.get(key)
        if token is None:
            return None
        self._turn_tokens.move_to_end(key)
        return token

    def _bind_turn(self, session: str, source_turn: str) -> int:
        """给一个 ``(会话, 回合号)`` 发 token 并绑定（有界）。"""
        token = self._next_token()
        self._turn_tokens[(session, source_turn)] = token
        while len(self._turn_tokens) > TURN_BINDING_MEMORY:
            self._turn_tokens.popitem(last=False)
        return token

    @staticmethod
    def _is_older_turn(source_turn: str, known_turn: str) -> bool:
        """入来的回合号是否严格早于已记录的回合号（数字才可比；不可比时不判先后）。"""
        if not known_turn:
            return False
        try:
            return int(source_turn) < int(known_turn)
        except (TypeError, ValueError):
            return False

    # ---------------------------------------------------------------- 回合起点
    def _observe_start(self, record: dict, event: str, session: str,
                       source_turn: str, owner: str) -> None:
        """更新回合账本（起点本身不出身份）。

        三段：带回合号（绑定表）、真实 user/message（至多刷新 LRU）、旧式 turn/start
        （原判据 + 发号）。
        """
        step = str(record.get("step") or "")
        if session and source_turn:
            if self._bound_token(session, source_turn) is not None:
                # 已绑定的回合 = 同一回合的另一份副本（镜像 / 迟到重投）：不发新号，
                # 也**不推进当前回合状态**——让迟到的旧回合起点顶掉新回合的记录，
                # 新回合的收尾就会退回旧 token（消费侧当成重复投递吞掉）。
                self._touch_turn(session)
                return
            token = self._bind_turn(session, source_turn)
            known = self._tokens.get(session)
            if known is not None and self._is_older_turn(source_turn, known[3]):
                # 旧回合的**首次**投递（它的起点此前没被读到，如回填跳过）：给它一个
                # 稳定身份，但不推进当前回合状态。它此前没绑定过 = 从没产出过身份，
                # 所以不会撞上消费者已经见过的那个。
                return
            self._remember_turn(session, (token, step, owner, source_turn))
            return
        if event == "user/message":
            if not is_human_user_message(record):
                return  # 注入上下文不是回合入口，也不该占会话表
            known = self._tokens.get(session)
            if known is None:
                self._remember_turn(session, (self._next_token(), "", owner, ""))
                return
            if known[3]:
                # 本会话已经有带回合号的回合：这条消息属于那个回合（真实桥里每回合
                # 4-5 条 user/message），发号点在这一回合自己的 turn/start / turn/end。
                self._touch_turn(session)
                return
            # 旧桥（本会话没有回合号记忆）：真人消息仍是新的回合入口 → 发新号；但
            # **不覆盖**已记录的 step / owner / sourceTurn——覆盖会让镜像副本的起点
            # 判据失去比对基准。
            self._remember_turn(session, (self._next_token(), known[1], known[2], known[3]))
            return
        # 旧式 turn/start（无回合号 / 无会话）
        if self.is_duplicate_start(record, owner):
            return
        self._remember_turn(session, (self._next_token(), step, owner, ""))

    def is_duplicate_start(self, record: dict, owner: str = "") -> bool:
        """这条 live 回合起点是不是**已经在跟的同一回合**的镜像副本。

        两级判据，回合号优先：

        1. **``(会话, 回合号)`` 已绑定**（桥接透传的 ``sourceTurn`` = DSH
           ``data.turn``）：就是同一回合的另一份副本——两个实例镜像各写一份完整信封、
           或同一条起点被重新投递，与写者归属、(会话, step) 都无关。这一级顺带认出了
           **迟到的旧回合重投**（F2），于是它不重新发号、也不推进收敛状态机（让它把
           折叠态重新推回"忙"，收尾时就会再报一次完成）。
        2. 没绑定过：会话表里有该会话的当前回合记录，且两侧都是 ``inst:`` 写者（插件
           boot UUID）而归属不同 → 镜像副本。真实 ``user/message`` 不带 step（见
           ``_TURN_START_EVENTS`` 上方的说明），**空 step 不参与比对**。

        为什么第 2 级要求两侧都是 ``inst:``：旧桥的写者归属是「路径 + 文件身份」，同一
        实例轮转文件就会换 key（同一写者两个 key）——拿它做镜像判定会把轮转后的新文件
        首条记录当成镜像副本吞掉。没有可用的写者身份时**绝不猜**。

        回合号不同 = **另一个回合**，即使 (会话, step) 相同、即使来自别的写者/实例，
        也照常发新号——「别的写者写的起点一律算镜像」会吞掉在另一个实例上继续的真实
        回合（漏报完成）。
        """
        if not isinstance(record, dict):
            return False
        event = str(record.get("event") or "")
        if event not in _TURN_START_EVENTS:
            return False
        session = str(record.get("sessionId") or record.get("session_id") or "")
        source_turn = str(record.get(_SOURCE_TURN_FIELD) or "")
        if session and source_turn and self._bound_token(session, source_turn) is not None:
            return True
        known = self._tokens.get(session)
        if known is None:
            return False
        if source_turn and known[3]:
            return False  # 两侧都带回合号却没绑定过 = 另一个回合
        step = str(record.get("step") or "")
        step_matches = known[1] == step
        if event == "user/message" and not step:
            step_matches = True
        if not step_matches:
            return False
        writer = str(owner or "")
        prev_writer = known[2]
        if not (writer.startswith("inst:") and prev_writer.startswith("inst:")):
            return False
        return prev_writer != writer

    def observe(self, record: dict, cursor: Optional[tuple] = None,
                owner: str = "") -> Optional[tuple]:
        """喂一条 live 桥接记录：是回合**成功**收尾事件时返回其身份，否则 None。

        回合起点只更新账本（恒返回 None）；收尾（``map_event_to_state`` 得 SUCCESS，
        即 ``turn/end`` 且 reasonKind 不是 aborted/error）按**它自己所属回合**的 token
        出身份：带回合号查绑定表（查不到就地立绑定），没带回合号用会话表，两者都取不到
        才退回摄入游标。
        """
        if not isinstance(record, dict):
            return None
        event = str(record.get("event") or "")
        session = str(record.get("sessionId") or record.get("session_id") or "")
        source_turn = str(record.get(_SOURCE_TURN_FIELD) or "")
        if event in _TURN_START_EVENTS:
            self._observe_start(record, event, session, source_turn, str(owner or ""))
            return None
        if map_event_to_state(record) is not DshState.SUCCESS:
            return None
        step = str(record.get("step") or "")
        if session and source_turn:
            token = self._bound_token(session, source_turn)
            if token is None:
                # 这个回合的起点没读到（中途挂载 / 回填跳过）：就地立绑定，让它和
                # 后续重投递落在同一个身份上。
                token = self._bind_turn(session, source_turn)
            return ("turn-end", session, step, token)
        if session or step:
            known = self._tokens.get(session)
            return ("turn-end", session, step, known[0] if known is not None else 0)
        if cursor is None:
            return None
        return ("turn-end-cursor", str(cursor[0]), cursor[1], int(cursor[2]))


def candidate_ports(port: Optional[int] = None) -> list[int]:
    """待探测的 DSH 端口候选（offline 判定的唯一来源）。

    DSH 可能跑在 3080（web 真实默认）或 38080（桌宠 harness_launcher 启动
    时的避让端口），desktop 桌面端固定在 127.0.0.1:19387，也可能通过
    DSH_PORT 环境变量指定——全部探测，任一在线即视为 DSH 在线（兼容
    「用户自己开的 DSH」与「桌宠一键启动的 DSH」）。
    """
    ports: set[int] = {3080, 38080, 19387}
    if port is not None:
        ports.add(int(port))
    env_port = os.environ.get("DSH_PORT")
    if env_port:
        try:
            ports.add(int(env_port))
        except (TypeError, ValueError):
            pass
    return sorted(ports)


#: 呈现侧原始状态（``agent_link.VALID_STATES`` 词汇）的折叠优先级：值越大越优先。
#: 只有「同优先级」才允许后写的覆盖先写的——任一写者仍在忙时，另一个写者回待机
#: 不得把整个 Agent 判成待机（否则会弹假完成、把动画切回待机）。
_WRITER_STATUS_PRIORITY = {
    "thinking": 3, "working": 3,
    "attention": 2, "error": 2,
    "idle": 1, "sleeping": 1,
}


def fold_writer_states(entries) -> str:
    """把多个写者的原始状态折叠成一个呈现态（空输入返回 ""）。

    ``entries`` 是可迭代的 ``(state, seq)``：``seq`` 是调用方给的稳定递增写入序号，
    只在同优先级打平时用（取最近写入的那个），不是时钟——不用时间来裁决是无意的：
    同一拍里两条记录谁后到由序号定，跨拍由序号定，不赌系统时钟精度。
    """
    best_state = ""
    best_key: Optional[tuple[int, int]] = None
    for state, seq in entries:
        if not state:
            continue
        key = (_WRITER_STATUS_PRIORITY.get(str(state), 0), int(seq))
        if best_key is None or key > best_key:
            best_key, best_state = key, str(state)
    return best_state


#: 写者 scope 注册表容量（多实例上界）。桌宠是长跑进程：DSH 每启动一次实例就是一个
#: 新的桥写者（新 pid / 新插件 boot UUID），不设上界就是一条随实例数线性增长的内存。
#: 超过容量时先回收界内安静写者；仍无名额则拒接新写者的状态分片，不合并归属。
#: 记录与交互归属照收，拒接次数保留并节流告警；无归属记录仍使用兼容桶。
SCOPE_REGISTRY_MAX = 32

#: 拒接诊断的告警节流：第 1 次立刻告警，之后每 N 次拒接汇总一条（同一被拒写者会
#: 一直写盘，逐条 warning 就是刷屏）。只用一个计数器，**不存"已拒接过的 owner"
#: 集合**——那会随写者数无界增长，而本表的全部意义就是把内存钉在有界。总次数
#: 始终可从 ``_scope_overflow_count`` 读到，诊断能力没有被节流吃掉。
SCOPE_REJECT_LOG_EVERY = 64

#: 折叠优先级：忙碌 > 终态（success/error）> 待机。多写者下「整体是什么状态」由本表
#: 裁决——只要还有一个写者在忙，另一个写者回待机就**不得**把整体判成待机（那会弹假
#: 完成、把动画与识屏判据一起切回待机）。阻塞交互（审批/问题）比这些都高，见 ``_fold``。
_SCOPE_STATE_PRIORITY = {
    DshState.THINKING: 3,
    DshState.WORKING: 3,
    DshState.SUCCESS: 2,
    DshState.ERROR: 2,
    DshState.IDLE: 1,
}


class _WriterScope:
    """单个桥写者（一个 DSH 实例）的状态机：独立状态 + 独立锁存。

    多实例串扰的三个来源都在这里收口：状态（一个实例的 idle 曾把另一个实例的 busy
    顶掉）、审批锁存、问题锁存（A 的 resolved 曾解开 B 的锁存）。``seq`` 是稳定递增
    的更新序号，只用于折叠时同优先级打平（取最近更新的那个），不是时钟。
    """

    __slots__ = ("state", "seq", "pending_approval", "approval_since",
                 "pending_question", "question_since")

    def __init__(self) -> None:
        self.state: Optional[DshState] = None
        self.seq = 0
        self.pending_approval = False
        self.approval_since: Optional[float] = None
        self.pending_question = False
        self.question_since: Optional[float] = None


class DshStateConverger:
    """桥接记录 → 统一状态的纯收敛器（无线程/Qt，时钟可注入）。

    由唯一读方 ``DshMonitor`` 在其 worker 线程里驱动：
    - 每条解析后的记录喂给 ``handle_record``；
    - 每次轮询节拍调 ``tick``（锁存超时兜底）；
    - 端口探测结果经 ``set_online`` 灌入（offline/idle 基线）。

    **多写者（多 DSH 实例）按 owner 分片、折叠成单出口**：桥目录里同时有多个
    ``dsh*.jsonl``（每个实例一份）时，状态与审批/问题锁存**按写者独立**保存，对外
    只产出一条折叠态——否则一个实例的 idle 会把另一个实例的 busy 顶掉（假完成、
    动画切回待机），A 的 ``question/resolved`` 会解开 B 的问题锁存（气泡自动消失）。
    折叠次序见 :meth:`_fold`；``handle_record`` 的 ``owner`` 参数就是写者归属
    （摄入侧给出，见 ``pet/agent_link.py::record_owner``）。
    """

    SCOPE_REGISTRY_MAX = SCOPE_REGISTRY_MAX

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        # 在线基线：无任何写者 scope 时的折叠值（None = 还没探活过；online → idle）
        self._baseline: Optional[DshState] = None
        self._offline = False
        # 写者 scope 注册表（有界）：owner 原始归属 → None（占位）。只用于归一 +
        # 容量裁决，真正的状态对象在 _scope_for 里按需创建。
        self._scope_keys: OrderedDict[str, None] = OrderedDict()
        self._scope_overflow_count = 0
        # 写者 scope → 该写者独立的状态机（状态 + 审批/问题锁存）
        self._scopes: OrderedDict[str, _WriterScope] = OrderedDict()
        self._seq = 0

    # ------------------------------------------------------------ 写者 scope
    def admit_owner(self, owner: Optional[str]) -> tuple[str, bool]:
        """写者归属归一 + 准入：返回 ``(scope key, 是否给它建分片)``。

        - ``None``（摄入侧给不出归属：无游标、直调）→ 无主桶 ``("", True)``；
        - 已登记 / 未满 → ``(key, True)``；
        - 注册表满 → 先在界内**安全退役**一个安静写者（无锁存、非忙）腾名额 →
          ``(key, True)``；腾不出来（全是忙/等待中的写者）→ ``(key, **False**)``：
          **拒接**这个写者的状态分片。

        拒接为什么不是"并入无主桶"（旧实现）：并桶等于让两个不同的写者共用一个
        锁存与状态——C 的 idle 会撤掉 D 的等待、D 的收尾会解开 C 的锁存，这正是
        多实例串扰本身。拒接只让**这一个**写者不参与收敛状态机（它的忙/等待不进
        折叠），其他通道照旧：记录仍按它自己的 owner 标注交给消费侧，常驻交互、
        生命周期收口、写者归属一个不少。下一条记录会再试一次准入（安静写者一变多
        就能立刻收编）。

        本方法**只做裁决，不产诊断**：一条记录会分别经摄入侧盖归属与状态推进两次
        准入（``DshMonitor._record_owner`` → ``handle_record``），诊断若挂在准入
        本身上就是每条被拒记录两条告警。诊断的唯一落点是真正要建状态分片的那次
        拒绝（``handle_record`` → :meth:`note_rejected_owner`），恰好每条记录一次。
        """
        key = "" if owner is None else str(owner)
        if key in self._scope_keys:
            self._scope_keys.move_to_end(key)
            return key, True
        if key and len(self._scope_keys) >= SCOPE_REGISTRY_MAX and not self._reclaim_quiet_scope():
            return key, False
        self._scope_keys[key] = None
        return key, True

    def note_rejected_owner(self, key: str) -> None:
        """记一次「写者状态分片被拒接」并**节制地**产诊断（每条被拒记录最多一次）。

        计数永不丢（``_scope_overflow_count`` 是总数），只有 warning 走节流：被拒
        写者会持续写盘，逐条告警等于刷屏；首次立即可见，之后每
        ``SCOPE_REJECT_LOG_EVERY`` 次汇总一条。诊断成本恒为 O(1)：只有计数器，
        没有"已拒接 owner"集合（那会随写者数无界增长）。
        """
        self._scope_overflow_count += 1
        count = self._scope_overflow_count
        if count == 1 or count % SCOPE_REJECT_LOG_EVERY == 0:
            log.warning(
                "桥写者 scope 注册表已满(%s)且写者都在忙/等待中：保守拒接新写者 %s 的"
                "状态分片（不并入已有写者的桶——并桶会让写者之间互撤；本写者的交互归属"
                "仍按自己的 owner 记）overflowCount=%s（同类告警每 %s 次汇总一条）",
                SCOPE_REGISTRY_MAX, str(key)[:120], count, SCOPE_REJECT_LOG_EVERY,
            )

    def _reclaim_quiet_scope(self) -> bool:
        """界内安全退役一个安静写者，腾出注册表名额；没有安静的返回 False。

        只挑**安静**的（没有任何阻塞锁存、状态不是 thinking/working）：退役一个忙
        或等待中的写者，等于把它活着的工作判成结束（假 idle / 假完成）。安静写者被
        退役后下一条记录会重新登记分片，所以这是可逆的界内回收，不是"合并"。

        为什么必须有它：注册表若只增不减，DSH 反复重启（每次新 boot UUID = 新写者）
        会把名额占满，之后就再也收编不了新写者。
        """
        victim: Optional[str] = None
        for key, scope in self._scopes.items():
            if scope.pending_approval or scope.pending_question:
                continue
            if scope.state in (DshState.THINKING, DshState.WORKING):
                continue
            if victim is None or scope.seq < self._scopes[victim].seq:
                victim = key
        if victim is None:
            # 只登记过归属、还没建分片的条目同样是安静的
            for key in self._scope_keys:
                if key not in self._scopes:
                    victim = key
                    break
        if victim is None:
            return False
        self._scope_keys.pop(victim, None)
        self._scopes.pop(victim, None)
        return True

    def owners_needing_liveness(self) -> list[str]:
        """当前"值得探活"的写者：还在忙或还在等用户输入的那些。

        它们的状态如果因为写者进程死掉而永远留着，用户就会看到永远忙/永远在等的
        桌宠；安静的写者（待机/终态）不需要探活——它们不驱动任何持续呈现。返回
        顺序 = 分片登记次序（最早登记的在前），调用方按需截断。
        """
        busy: list[str] = []
        for key, scope in self._scopes.items():
            if scope.pending_approval or scope.pending_question or scope.state in (
                    DshState.THINKING, DshState.WORKING):
                busy.append(key)
        return busy

    def _scope_for(self, key: str) -> _WriterScope:
        scope = self._scopes.get(key)
        if scope is None:
            scope = _WriterScope()
            self._scopes[key] = scope
        return scope

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ------------------------------------------------------------ 折叠（单出口）
    def _fold(self) -> Optional[DshState]:
        """把各写者 scope 折叠成**唯一出口**的聚合态。

        裁决次序：任一写者在等用户输入（审批/问题）> 任一写者在忙 > 终态 > 待机；
        同档内取最近更新的那个写者（``seq``）。这条次序就是「别写者的 idle 不得
        覆盖这一写者的 pending / busy」的落地处——消费侧只看到折叠后的态，看不到
        也没机会误用单个写者的 idle。
        """
        if self._offline:
            return DshState.OFFLINE
        best_state: Optional[DshState] = None
        best_key: Optional[tuple[int, int]] = None
        pending_approval = False
        pending_question = False
        for scope in self._scopes.values():
            pending_approval = pending_approval or scope.pending_approval
            pending_question = pending_question or scope.pending_question
            state = scope.state
            if state is None:
                continue
            key = (_SCOPE_STATE_PRIORITY.get(state, 0), scope.seq)
            if best_key is None or key > best_key:
                best_key, best_state = key, state
        if pending_approval:
            return DshState.WAITING_APPROVAL
        if pending_question:
            return DshState.WAITING_QUESTION
        if best_state is None:
            return self._baseline
        return best_state

    @property
    def current_state(self) -> Optional[DshState]:
        """当前聚合状态（各写者折叠后的唯一出口）。"""
        return self._fold()

    @property
    def _pending_approval(self) -> bool:
        """是否有任一写者锁存在审批（兼容既有的单桶读法）。"""
        return any(scope.pending_approval for scope in self._scopes.values())

    @property
    def _pending_question(self) -> bool:
        """是否有任一写者锁存在问题（兼容既有的单桶读法）。"""
        return any(scope.pending_question for scope in self._scopes.values())

    def _emit_edge(self, out: list, before: Optional[DshState], source: str = "") -> None:
        """折叠态真正变化时才产出 ("state", from, to, source)；同态零产出。"""
        after = self._fold()
        if after is None or after is before:
            return
        if before is None:
            log.info("[DSH STATE] %s", after.value)
        else:
            log.info("[DSH STATE] %s -> %s", before.value, after.value)
        out.append(("state", "" if before is None else before.value, after.value, source))

    def reset(self) -> None:
        """读方重启（监视器 start）时清零：全部写者 scope 与基线回到初始。"""
        self._baseline = None
        self._offline = False
        self._scope_keys.clear()
        self._scopes.clear()
        self._scope_overflow_count = 0

    def retire_scope(self, owner: str) -> list[tuple]:
        """写者被确认死亡：只撤它自己的状态与锁存，重新折叠并产出边沿。

        **绝不产出 success**：写者消失不是回合完成，消费侧据此只撤归属它的常驻
        交互，不排定完成确认（``_emit_edge`` 的 source 恒为空串，没有"回合收尾"
        的来源标记）。别的写者的状态/锁存原样保留——这正是它优于"整体清空"的地方。
        """
        out: list[tuple] = []
        key = str(owner or "")
        self._scope_keys.pop(key, None)   # 写者没了：注册表也不该再占着这个名额
        if key not in self._scopes:
            return out
        before = self._fold()
        del self._scopes[key]
        self._emit_edge(out, before)
        return out

    # ------------------------------------------------------------ 状态推进
    def handle_record(self, record: dict, owner: Optional[str] = None) -> list[tuple]:
        """处理一条桥接记录：按**写者**独立推进状态与阻塞交互锁存。

        审批与用户问题（ask_user_question）都是「阻塞 Agent 等待用户输入」的
        交互，统一按锁存处理：进入 waiting_approval / waiting_question 后忽略
        working/thinking，直到对应的 decided / resolved 到来解除。

        ``owner`` 是这条记录的写者归属（多 DSH 实例分区）；``None``/``""`` 走无主
        桶。锁存与状态**按 owner 独立**，产出是唯一折叠出口——一个实例的 idle /
        resolved 既不能顶掉另一个实例的 busy，也不能解开它的锁存。

        注册表满且挤不出安静写者时，本条记录的写者分片被**拒接**（见
        :meth:`admit_owner`）：状态机不推进（绝不与已有写者共用桶互相撤），但状态
        以外的输出（真人消息）照常给；拒接在这里留**一条**诊断（计数 + 节流告警，
        见 :meth:`note_rejected_owner`）——每条记录只走到这里一次，所以既不会翻倍
        也不会静默。
        """
        out: list[tuple] = []
        if not isinstance(record, dict):
            return out
        before = self._fold()
        event = str(record.get("event") or "")
        state = map_event_to_state(record)
        if state is None:
            return out

        # 真人用户消息：产出 user_message（对话开始的稳定触发点）。
        # agent.inject() 注入上下文整体忽略——不进状态机、不算对话开始，防止每轮
        # 4-5 条 system-reminder/技能目录/记忆记录把「对话开始」触发改成随机的
        # （同 tick 批量应用 + 边沿去重 → 时触时不触）。判据是**白名单**
        # （``is_human_user_message``）：注入源的 kind 会随 DSH 版本增加，黑名单每漏
        # 一个就让一整轮注入记录冒充真人消息。
        if event == "user/message":
            if not is_human_user_message(record):
                return out
            out.append((
                "user_message",
                str(record.get("sessionId") or record.get("session_id") or ""),
                str(record.get("text") or record.get("content") or record.get("summary") or ""),
            ))

        key, admitted = self.admit_owner(owner)
        if not admitted:
            self.note_rejected_owner(key)
            return out
        scope = self._scope_for(key)

        # approval/decided / cordis/request-run-resolved：解除**本写者**的审批锁存，
        # 回到 working（agent 仍在 running）
        if event in ("approval/decided", "cordis/request-run-resolved"):
            was_latched = scope.pending_approval
            scope.pending_approval = False
            scope.approval_since = None
            if was_latched:
                scope.state = DshState.WORKING
                scope.seq = self._next_seq()
            self._emit_edge(out, before)
            return out

        # question/resolved：解除**本写者**的问题锁存，回到 working
        if event == "question/resolved":
            was_latched = scope.pending_question
            scope.pending_question = False
            scope.question_since = None
            if was_latched:
                scope.state = DshState.WORKING
                scope.seq = self._next_seq()
            self._emit_edge(out, before)
            return out

        if event == "turn/end" and state is DshState.IDLE:
            # 用户中止的回合：必须带 source 产出，消费侧据此回待机并撤掉完成确认。
            # 放在锁存闸之前：中止会终结整轮，本写者的审批/问题锁存一并解除（否则
            # 岛一直等待，超时又被 working 顶回）。**只解本写者**：另一个实例还在
            # 等的审批/问题不受影响。
            scope.pending_approval = False
            scope.approval_since = None
            scope.pending_question = False
            scope.question_since = None
            scope.state = DshState.IDLE
            scope.seq = self._next_seq()
            after = self._fold()
            if after is before:
                # 旧 AgentStatus idle 基线可能先到（折叠后已是 IDLE），同态去重会吞掉
                # 这条中止——此时仍产出一条 idle→idle 标记，让消费侧撤掉基线排下的
                # 确认。折叠态不是待机（别的写者还忙/还等）时零产出：中止只属于本
                # 写者，不能替别的写者发言。
                if after is DshState.IDLE:
                    out.append(("state", DshState.IDLE.value, DshState.IDLE.value, event))
                return out
            out.append(("state", "" if before is None else before.value, after.value, event))
            return out

        if event == "turn/end" and state is DshState.ERROR:
            # 失败回合的收尾（reasonKind=error，见 map_event_to_state 的 F4 分流）：
            # 与中止同款——终结整轮、只解本写者的审批/问题锁存，并带 source 产出，
            # 消费侧（agent_link）据此把呈现管线注入 error：桌宠撤忙碌动画、
            # 岛亮红态、排定失败口径的收尾检查；绝不能落在通用路径（source 丢失、
            # 锁存闸会把这条收尾整个吞掉，岛一直等待、桌宠永远忙碌）。
            scope.pending_approval = False
            scope.approval_since = None
            scope.pending_question = False
            scope.question_since = None
            scope.state = DshState.ERROR
            scope.seq = self._next_seq()
            after = self._fold()
            if after is before:
                return out
            out.append(("state", "" if before is None else before.value, after.value, event))
            return out

        # 审批类权威事件（cordis/request-run、旧式 approval/request、显式
        # AgentStatus.waiting_approval）：进入本写者的审批锁存
        if state is DshState.WAITING_APPROVAL:
            scope.pending_approval = True
            scope.approval_since = self._clock()
            scope.state = state
            scope.seq = self._next_seq()
            self._emit_edge(out, before, source=event)
            return out

        # question/requested：进入本写者的问题锁存
        if state is DshState.WAITING_QUESTION:
            scope.pending_question = True
            scope.question_since = self._clock()
            scope.state = state
            scope.seq = self._next_seq()
            self._emit_edge(out, before, source=event)
            return out

        # 本写者锁存中：忽略它自己的非阻塞事件（防 waiting_approval /
        # waiting_question 被 working / thinking 顶掉）。**只挡本写者**——别的写者
        # 的记录照常推进，不再被 A 的锁存连坐。
        if scope.pending_approval or scope.pending_question:
            return out

        scope.state = state
        scope.seq = self._next_seq()
        self._emit_edge(out, before)
        return out

    # ------------------------------------------------------------ 在线基线
    def set_online(self, online: bool) -> list[tuple]:
        """把在线探测结果落到状态机：离线 → offline（写者全部作废），在线 → idle 基线。"""
        out: list[tuple] = []
        if not online:
            # DSH 未运行：所有实例一起消失——各写者的状态/锁存一并作废（留着就是
            # 一批再也不会更新的残留），切 offline。
            before = self._fold()
            self._scope_keys.clear()
            self._scopes.clear()
            self._offline = True
            self._emit_edge(out, before)
        else:
            before = self._fold()
            self._offline = False
            # DSH 在线但没有任何活跃 Agent/事件：基线 idle（含从 None / offline 恢复）
            self._baseline = DshState.IDLE
            if before in (None, DshState.OFFLINE):
                self._emit_edge(out, before)
        return out

    def tick(self) -> list[tuple]:
        """轮询节拍：阻塞型交互锁存超时兜底（DSH 漏发 decided/resolved 的防线）。

        只对**真的超时**的写者解锁；时间没到就什么都不做——不能拿 TTL 去动还在
        合法等待中的 pending（那会把用户还在看的常驻气泡判成失效）。
        """
        out: list[tuple] = []
        before = self._fold()
        now = self._clock()
        for scope in self._scopes.values():
            if scope.pending_approval and scope.approval_since is not None:
                if now - scope.approval_since >= APPROVAL_LATCH_TIMEOUT_S:
                    log.warning("DSH 审批锁存超时，强制解除审批态")
                    scope.pending_approval = False
                    scope.approval_since = None
                    scope.state = DshState.WORKING
                    scope.seq = self._next_seq()
            if scope.pending_question and scope.question_since is not None:
                if now - scope.question_since >= APPROVAL_LATCH_TIMEOUT_S:
                    log.warning("DSH 问题锁存超时，强制解除问题态")
                    scope.pending_question = False
                    scope.question_since = None
                    scope.state = DshState.WORKING
                    scope.seq = self._next_seq()
        self._emit_edge(out, before)
        return out
