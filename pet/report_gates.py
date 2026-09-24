# -*- coding: utf-8 -*-
"""事件汇报概率门（report gates）。

设计口径（用户 2026-09-10 定稿）：

- 全部事件汇报控制统一为**概率门**：值为该类事件的**通过概率** 0.00–1.00，
  ``0`` = 该类完全不汇报，``1`` = 全部汇报。**不再有布尔开关**。
- 门按**事件聚合类别**划分（本模块 ``REPORT_GATE_KEYS``），设置页把它们收进
  「自动化与联动 → 事件气泡触发概率」下的一个可折叠框里，与文案行同组。
- 概率只作用在**出气泡的汇报路径**。检测器本身（卡住检测 / 行为识别 /
  探索看门狗）与 ``raw_record`` 数据链路不经过概率门，不受影响。

本模块只放纯数据与纯函数，不导入 ``pet.config`` 等模块，避免循环导入。
"""

from __future__ import annotations

#: 门名 → 默认通过概率。除过程汇报外默认常开（1.0），即默认行为与改造前一致。
REPORT_GATE_DEFAULTS: dict[str, float] = {
    "state": 1.0,  # 开始干活 / 思考
    "activity": 0.6,  # 过程汇报（读文件/跑命令/改代码…）——提醒量最大，默认抽稀到 60%
    "approval": 1.0,  # 审批与提问（需要你处理）
    "done": 1.0,  # 任务完成
    "exec_failed": 1.0,  # 失败与错误
    "model_access": 1.0,  # 模型访问失败（服务端限流 / 过载 / AI 服务错误）
    "stuck": 1.0,  # 检测类提醒：卡住 / 行为重复 / 循环检测
    "bridge": 1.0,  # 桥接安装与写回、Agent 未找到、余额查询
}

#: 门名的稳定顺序（设置页按此顺序展示）
REPORT_GATE_KEYS: tuple[str, ...] = tuple(REPORT_GATE_DEFAULTS)

#: 门名的中文标签（设置页小节标题）
REPORT_GATE_LABELS: dict[str, str] = {
    "state": "状态提醒（开始干活 / 思考）",
    "activity": "过程汇报（读文件 / 跑命令 / 改代码…）",
    "approval": "审批与提问（需要你处理）",
    "done": "任务完成",
    "exec_failed": "失败与错误",
    "model_access": "模型访问失败",
    "stuck": "检测类提醒（卡住 / 行为重复 / 循环）",
    "bridge": "桥接、写回与查询",
}

#: 旧布尔开关 → 新概率门（一次性迁移用；迁移后不再写回旧键）
LEGACY_SWITCH_GATES: dict[str, str] = {
    "notify_state": "state",
    "notify_activity": "activity",
    "notify_approval": "approval",
    "notify_done": "done",
    "notify_exec_failed": "exec_failed",
}

#: 已废弃的旧概率键（0-100 整数）→ 新门（0-1）
LEGACY_PERCENT_GATES: dict[str, str] = {
    "report_probability": "activity",
}

#: 事件键前缀 → 门名。判定顺序：先精确表，再前缀表。
_EVENT_EXACT: dict[str, str] = {
    "start": "state",
    "thinking": "state",
    "agent.attention": "approval",
    "agent.error": "exec_failed",
    "agent.missing": "bridge",
}

_EVENT_PREFIX: tuple[tuple[str, str], ...] = (
    ("activity.", "activity"),
    ("approval.", "approval"),
    ("question.", "approval"),
    ("done.", "done"),
    ("failure.", "exec_failed"),
    ("model_access.", "model_access"),
    ("llm_error.", "model_access"),
    ("stuck.", "stuck"),
    ("pattern.", "stuck"),
    ("watchdog.", "stuck"),
    ("bridge.", "bridge"),
    ("balance.", "bridge"),
    ("dsh.writeback.", "bridge"),
)


def gate_for_event(event_key: str) -> str | None:
    """事件键 → 概率门名；未知事件返回 ``None``（调用方按不抽稀处理）。"""
    key = str(event_key or "").strip()
    if not key:
        return None
    if key in _EVENT_EXACT:
        return _EVENT_EXACT[key]
    for prefix, gate in _EVENT_PREFIX:
        if key.startswith(prefix):
            return gate
    return None


def should_report(probability: float, roll: float) -> bool:
    """按通过概率判决：``roll < probability`` 放行（边界取「小于」）。"""
    return float(roll) < float(probability)


def should_report_event(gates: dict, event_key: str, roll: float) -> bool:
    """按事件键找到所属门并判决。

    未知事件（没有归属门）**不抽稀**，直接放行——新事件上线时不会被静默丢弃。
    """
    gate = gate_for_event(event_key)
    if gate is None:
        return True
    try:
        probability = float(gates.get(gate, REPORT_GATE_DEFAULTS.get(gate, 1.0)))
    except (TypeError, ValueError):
        probability = REPORT_GATE_DEFAULTS.get(gate, 1.0)
    return should_report(probability, roll)


def clean_report_gates(raw: object) -> dict[str, float]:
    """清洗概率门配置。

    - 未给的门取默认值；
    - 未知门名丢弃（防止拼写错误静默生效）；
    - **无法解析的值回落该门默认值**（例如 ``"abc"``/``None`` → 该门默认；
      ``activity`` 的默认是 ``0.6``，不是 1.0）；
    - 越界收敛到 ``[0, 1]``，数值字符串可解析；
    - 布尔不是特例：``True``→``1.0``、``False``→``0.0``（旧开关迁移到概率端点）。
    """
    result = dict(REPORT_GATE_DEFAULTS)
    if not isinstance(raw, dict):
        return result
    for name, value in raw.items():
        key = str(name)
        if key not in REPORT_GATE_DEFAULTS:
            continue
        default = REPORT_GATE_DEFAULTS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            result[key] = default
            continue
        result[key] = min(1.0, max(0.0, number))
    return result
