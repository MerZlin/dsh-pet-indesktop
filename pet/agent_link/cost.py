# -*- coding: utf-8 -*-
"""Agent 本轮消费统计：用余额差值估算一次对话花了多少钱。

思路（复用已有的余额查询，不碰 token 计数）：

    Agent 开始干活 → 记下余额快照
    Agent 本轮结束 → 再查一次，两者相减即本轮消费

**为什么用余额差而不是 token 数**：DeepSeek 只在 ``/user/balance`` 暴露账户余额，
没有公开的 token 用量接口（platform 网页端的用量接口需要登录态 userToken，
API Key 打不通，实测返回 ``Authorization Failed``）。而余额是**账户级**的，
不管 Agent 走哪个 key 消耗，都会从同一份余额扣，差值因此能反映真实花费。

已知精度限制（属接口固有，非本模块缺陷）：
- 余额只有 2 位小数（分），一轮花费不足 ¥0.005 时差值会被抹平为 0；
- 小额度消费可能延迟到下一轮才体现。

本模块只做"快照 / 差值 / 文案"，网络查询由调用方注入，便于离线单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# 差值小于这个数（元）就认为"分精度测不出"，按不足一分显示。
# 余额是 2 位小数，所以小于半分的变化本来就可能被四舍五入吃掉。
_MIN_MEASURABLE = 0.005


@dataclass(frozen=True)
class Snapshot:
    """一次余额快照。"""

    total: float
    at: float


def format_cost(delta: float, *, concurrent: bool = False) -> str:
    """把消费金额格式化成气泡文案。

    ``concurrent=True`` 表示同时有别的 Agent 在干活，差值里可能混进了它们的
    消耗，按用户要求标注出来而不是隐瞒。
    """
    if delta < _MIN_MEASURABLE:
        text = "本轮消费不足 ¥0.01"
    else:
        text = f"本轮消费 ¥{delta:.2f}"
    if concurrent:
        text += "（含其他会话）"
    return text


class AgentCostTracker:
    """按 Agent 分别记录余额快照并计算本轮消费。

    每个 Agent 各自一份快照（``agent_key`` 为键），互不干扰——这样 Claude 和
    DSH 各自算各自的，"开始"与"结束"配对不会串。
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        # agent_key -> 开始时读到的余额
        self._baselines: dict[str, float] = {}
        # 正在干活的 Agent 总数，用于判断"并发"。
        self._busy: set[str] = set()
        # 本次会话期间是否曾出现过并发（用于结算时的标注判定）。
        self._saw_concurrent: bool = False

    # ------------------------------------------------------------ 状态

    def is_tracking(self, agent_key: str) -> bool:
        return agent_key in self._busy

    @property
    def concurrent(self) -> bool:
        """当前是否同时有多个 Agent 在干活（差值会互相混入）。"""
        return len(self._busy) > 1

    def begin(self, agent_key: str) -> None:
        """Agent 开始干活：清掉旧基线，等快照回来。

        必须先清旧的——否则上一轮遗留的数字会被当成这一轮的起点。
        """
        # 先看并发态再加自己：加完再判会把"本轮刚加入的自己"也算成并发。
        # 排除 agent_key 自身：同一 Agent「idle→800ms 内回忙」的重入不经过
        # 任何结束路径（_cancel_done_check 直接停掉确认定时器），旧状态还留
        # 在 _busy 里——不排除自身就会把重入误判成"有别的会话在跑"。
        was_concurrent = bool(self._busy - {agent_key})
        self._busy.add(agent_key)
        # 会话期间"是否曾并发"要持续累计：两个 Agent 并行时，**后结束**的那个
        # 结算时 _busy 只剩自己，若在结算时判并发就会漏标注。
        self._saw_concurrent = self._saw_concurrent or was_concurrent
        self._baselines.pop(agent_key, None)

    def set_baseline(self, agent_key: str, total: float) -> None:
        """余额查询回来后写入基线。"""
        if agent_key in self._busy:
            self._baselines[agent_key] = float(total)

    def abort(self, agent_key: str) -> None:
        """查询失败 / 会话结束：丢弃这个 Agent 的状态。"""
        self._busy.discard(agent_key)
        self._baselines.pop(agent_key, None)
        # 与 finish 的收口对齐：abort 摘走的是最后一个 busy agent 时，
        # "曾并发"标志一并复位——否则多 Agent 场景会留下一次多余的
        # 「（含其他会话）」标注（虽下一次 finish 会自愈，但没必要留这帧）。
        if not self._busy:
            self._saw_concurrent = False

    def clear(self) -> None:
        self._busy.clear()
        self._baselines.clear()
        self._saw_concurrent = False

    # ------------------------------------------------------------ 结算

    def finish(self, agent_key: str, total: float) -> str | None:
        """Agent 本轮结束：算差值并返回文案；拿不到基线时返回 ``None``。

        返回 ``None`` 的两种情况（都应该静默跳过，不显示金额）：
        - 该 Agent 没登记过开始（比如功能是中途才开的）；
        - 基线查询还没回来（回合极短，小于一次网络往返）。
        """
        # 用"会话期间曾否并发"而不是"此刻是否并发"：后者对**后结束**的那个
        # 会漏判（那时只剩自己），而它的差值同样混着别人的消费。
        concurrent = self._saw_concurrent
        baseline = self._baselines.pop(agent_key, None)
        self._busy.discard(agent_key)
        if not self._busy:
            self._saw_concurrent = False   # 全部结束，复位
        if baseline is None:
            return None
        delta = max(0.0, baseline - float(total))
        return format_cost(delta, concurrent=concurrent)
