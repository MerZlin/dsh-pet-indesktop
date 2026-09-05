# -*- coding: utf-8 -*-
"""批10-A1：预测式接力预热 —— 帧驱动提前掷骰 + 后台预解码首帧。

设计来源：GLM-5.3 咨询（_plan/current/memory/CONSULT_batch10_glm53_reply.md）
方案卡 A-1 与 §A1-A6（逐条遵守）。

- 决策点前置：当前动画墙钟剩余 ≤ 提前量（帧驱动，按 decode_throttle_divisor
  修正闲置降帧的墙钟——不许用纯时间 QTimer），掷骰决定下一个动画，并在后台
  预解码其首帧进 LRU。
- 掷骰产物一律照存（含 idle/turn）——防稳态分布漂移（盲审 P1-1）；``_should_predict``
  只闸预热不闸预测（idle/turn 时不预测会重塑稳态分布，预热才可能被跳过）。
  预测只存动画名，不预提交移动计划/位置/朝向；gap 由窗口层 ``_animation_gap_active`` 守卫。
- 预测记录单槽 ``{name, context_anim, gen}``。消费规则只有一条：context_anim
  与当前动画一致且代次未变（gap 期间另要求 gap 仍激活），不符即弃、现场掷骰
  —— 退化为现状行为。点击/拖拽/联动/唱歌打断全部经「换掉 self.anim」自然
  作废，不在各交互入口手动清状态。
- 预热深度 = Phase 1 only：只调 MovieLibrary.warm_predicted（复用交互让路/
  隐藏暂停/幂等三重闸门），webm_clip.py 零改动；不预起 reader（Phase 2 挂起）。
- perfstats 计数器：predict.made / predict.hit / predict.miss_invalid /
  prewarm.ff_ms（后者在 library.warm_predicted 内计时，见 library.py）。

本模块是纯控制器：不 import window / webm_clip；对窗口的依赖（掷骰、预热、
可否预测判定）经构造注入回调，避免循环依赖（docs/WINDOW_PY_SPLIT_GUIDE.md）。

只在 GUI 线程使用（被 _on_frame / _pick_next / _switch 帧驱动触发）。
"""
from __future__ import annotations

import random

from . import catalog
from . import perfstats


def pick_from_pool(pool, exclude: str | None = None):
    """排除 exclude 后均匀采样，池空回退原池（window.PetWindow._pick 委托此处，
    全仓单一事实来源——规格 A6 明令禁止复制概率/采样逻辑）。

    与历史 window._pick 的唯一行为差：池为空时返回 None 而非 random.choice([])
    抛 IndexError（各调用点均有非空守卫，差异不可达）。
    """
    entries = [n for n in pool if n != exclude] or pool
    return random.choice(entries) if entries else None


def roll_next(pools, exclude: str | None = None) -> str | None:
    """纯函数（无副作用）：按 30% 待机 / 10% 转向 / 40% 动作 / 20% 移动
    返回「下一个动画」的候选名。

    与 window._pick_next 的动画链共用同一份概率逻辑（禁止复制）：
    - idle/turn 分支在对应池非空时取池内名，空则回退动作池；
    - acts 分支取动作池名；
    - move 分支取移动池名（与 _try_move 现状一致：不应用 exclude ——
      _try_move 的移动名选择不排除当前动画），移动池空则回退动作池。

    返回 None 表示当前池配置无法产生候选（理论上 _pick_next 的 acts 守卫
    已排除；此处兜底，避免 random.choice([]) 崩溃）。
    """
    idles = pools.get("idles") or []
    turns = pools.get("turns") or []
    acts = pools.get("acts") or []
    moves = pools.get("moves") or []
    roll = random.random()
    if roll < catalog.P_IDLE:
        return pick_from_pool(idles, exclude) if idles else pick_from_pool(acts, exclude)
    if roll < catalog.P_TURN:
        return pick_from_pool(turns, exclude) if turns else pick_from_pool(acts, exclude)
    if roll < catalog.P_ACTS:
        return pick_from_pool(acts, exclude)
    return pick_from_pool(moves) if moves else pick_from_pool(acts, exclude)


class PredictivePrewarm:
    """预测式接力预热的单槽状态机（GUI 线程使用）。

    window.py 只保留薄钩子（docs/WINDOW_PY_SPLIT_GUIDE.md §3）：
    - ``_switch`` → ``begin_anim(name)``（换动画代次：作废旧预测）；
    - ``_on_frame`` → ``on_frame(...)``（帧驱动触发掷骰 + 预热）；
    - ``_pick_next`` → ``consume(...)``（消费预测）；
    - ``_pause_activity`` / ``hideEvent`` → ``clear()``（隐藏/切角色作废）。
    """

    def __init__(self, *, roll, warm, should_predict) -> None:
        self._roll = roll                 # (exclude) -> str | None
        self._warm = warm                 # (name) -> None
        self._should_predict = should_predict  # (name) -> bool
        self._prediction: dict | None = None   # {name, context_anim, gen}
        self._gen: int = 0                # 动画代次（begin_anim 每次自增）
        # 观测计数（供 perfstats 打点 / 测试直接断言）
        self.counts = {"made": 0, "hit": 0, "miss_invalid": 0}

    @property
    def prediction(self) -> dict | None:
        """当前预测记录（只读，非 None 即本代次已预测）。"""
        return self._prediction

    @property
    def generation(self) -> int:
        return self._gen

    def begin_anim(self, name: str) -> None:
        """切换到新动画（_switch 内调用）：换代。

        预测记录**不清**——作废统一由 consume 的 context/gen 校验完成（GLM A4
        「消费规则只有一条」，规则化，不依赖各交互入口手动清状态）。
        """
        self._gen += 1

    def clear(self) -> None:
        """隐藏/切角色/暂停：作废旧预测（幂等，不换代）。

        仅用于生命周期守卫（_hidden_paused 或关闭）；交互打断不走这里，交给
        consume 的 context/gen 校验（自然作废）。
        """
        self._prediction = None

    def on_frame(
        self,
        name: str,
        n: int,
        frames: int,
        fps: float,
        divisor: int,
        lead_sec: float,
        exclude: str | None = None,
    ) -> bool:
        """帧驱动触发：``wall_remaining ≤ lead`` 且本代次未预测 → 掷骰一次并存预测。

        ``wall_remaining = (frames - 1 - n) / (fps / divisor)``，divisor 取
        movie 的 decode_throttle_divisor（闲置降帧会改它并拉长墙钟——纯时间
        QTimer 会算错）。

        **掷骰只掷一次，产物照存**（含 idle/turn）——盲审 P1-1：若掷出 idle/turn
        就拒收并在窗口内逐帧重掷，稳态分布会从 30/10/40/20 漂到 ≈1/0/67/31
        （桌宠不再回待机）。``should_predict`` 只闸**预热**（idle/turn pinned，
        预热是纯浪费，规格 A6-2），不闸预测本身。

        返回 True 表示本帧实际创建了预测（供测试/打点）。
        """
        pred = self._prediction
        if pred is not None and pred["gen"] == self._gen:
            return False  # 本代次已预测
        if fps <= 0 or frames <= 0 or n < 0:
            return False  # 帧数/帧率未知（元数据未 warm 完）：照旧现场掷骰
        divisor = max(1, int(divisor or 1))
        effective = fps / divisor
        if effective <= 0:
            return False
        wall_remaining = (frames - 1 - n) / effective
        if wall_remaining > lead_sec:
            return False
        predicted = self._roll(exclude)
        if predicted is None:
            return False
        self._prediction = {
            "name": predicted,
            "context_anim": name,
            "gen": self._gen,
        }
        if self._should_predict(predicted):
            try:
                self._warm(predicted)
            except Exception:
                pass  # 预热失败不致命，后续播放按需同步解码（最坏退化现状）
        if perfstats.ENABLED:
            perfstats.note("predict.made")
        self.counts["made"] += 1
        return True

    def consume(self, context_anim: str, exclude: str, gap_active: bool,
                moves: frozenset | set | None = None) -> str | None:
        """_pick_next 消费预测。

        消费规则只有一条：context_anim 与当前动画一致且代次未变，不符即弃、
        返回 None（调用方现场掷骰，退化为现状行为）。exclude 撞名（预测名 ==
        当前动画）时重掷——但 move 产物豁免：move 分支的掷骰本就不排 exclude
        （与 _try_move 一致），同名是合法结局（盲审 P2-3）。gap 语义由
        「gap 激活期间不预测」+ gap 步换代实现，本方法不另加校验。

        返回预测名并计 hit；作废计 miss_invalid；无预测不计数。
        """
        pred = self._prediction
        if pred is None:
            return None
        self._prediction = None
        move_names = moves or ()
        valid = (
            pred["context_anim"] == context_anim
            and pred["gen"] == self._gen
            and (pred["name"] != exclude or pred["name"] in move_names)
        )
        if not valid:
            if perfstats.ENABLED:
                perfstats.note("predict.miss_invalid")
            self.counts["miss_invalid"] += 1
            return None
        if perfstats.ENABLED:
            perfstats.note("predict.hit")
        self.counts["hit"] += 1
        return pred["name"]
