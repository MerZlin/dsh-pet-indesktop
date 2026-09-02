# -*- coding: utf-8 -*-
"""DSH 行为模式检测器（BehaviorPatternDetector）。

与 ``stuck_detector``（失败/超时/重试的「卡住评分」）互补，本模块专攻**行为模式**：
不再按工具名数「同一种工具重复几次」，而是把工具调用归一化成**行为类**，再用
**双窗口**统计近期行为分布，识别两类典型异常：

1. **慢性循环**（长期重复）：最近 10 个 step 里同类行为出现很多次；
2. **短时爆发**（高密度重复）：最近 6 个 step 里同类行为快速重复；
3. **纯探索无产出**：最近 6 个 step 全是 EXPLORATION（search/read/think/导航），
   完全没有 ACTION（edit/execute/test）——Agent 在反复翻资料但没动手验证/修改。

规则（默认参数，可用配置覆盖，见 :func:`BehaviorPatternDetector.get_config_overrides`）::

    ───── 细分类（SEARCH/READ/THINK/NAV/EDIT/EXECUTE/TEST）─────
    W10 同类 >= 3   → warning（有重复倾向）
    W10 同类 >= 4   → control（已明显偏离）
    W6  同类 >= 3   → control（短时间高密度重复）

    ───── 行为大类（EXPLORATION / ACTION）─────
    W6  EXPLORATION >= 5 且 ACTION == 0 → control
    W10 EXPLORATION >= 7 且 ACTION <= 1 → warning

关键工程约束：

- **step 去重**：同一 step 的并行工具调用（如 Step 12 里 Search A/B/C）只算
  **一次**行为决策，避免正常并行搜索被误判成 Search 重复 3 次。
- **cooldown 门控**：触发后记录 ``inspected_seq``，下一次窗口只统计
  ``inspected_seq`` 之后的事件，且**至少新增 N 个 Agent step**（默认 3）才允许
  再次触发——避免同一批历史事件反复弹提醒。
- **Control 不等于杀掉 Agent**：命中 Control 后调用可选的小型 LLM Judge
  （``NORMAL / REPLAN / ASK_USER / STOP``），Judge 不可用/未配置时降级为
  ``REPLAN``（只提醒，不打断 Agent）。本模块只负责「这个行为模式值得检查」。

事件来源：桥接插件 ``integrations/dsh-pet-bridge`` 写盘的 ``tool/call`` /
``command/run`` 等记录（每条约 80ms 合批一次），带 ``step`` 字段。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from enum import Enum

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("dsh-pet-standalone")

# ---------------------------------------------------------------------------
# 行为类 / 行为大类
# ---------------------------------------------------------------------------


class BehaviorClass(str, Enum):
    """细分类行为类。"""

    SEARCH = "SEARCH"
    READ = "READ"
    THINK = "THINK"
    NAVIGATION = "NAVIGATION"
    EDIT = "EDIT"
    EXECUTE = "EXECUTE"
    TEST = "TEST"
    OTHER = "OTHER"


class BehaviorMacro(str, Enum):
    """行为大类：探索 / 行动 / 其他。"""

    EXPLORATION = "EXPLORATION"
    ACTION = "ACTION"
    OTHER = "OTHER"


# 细分类 → 大类映射
_MACRO_OF: dict[BehaviorClass, BehaviorMacro] = {
    BehaviorClass.SEARCH: BehaviorMacro.EXPLORATION,
    BehaviorClass.READ: BehaviorMacro.EXPLORATION,
    BehaviorClass.THINK: BehaviorMacro.EXPLORATION,
    BehaviorClass.NAVIGATION: BehaviorMacro.EXPLORATION,
    BehaviorClass.EDIT: BehaviorMacro.ACTION,
    BehaviorClass.EXECUTE: BehaviorMacro.ACTION,
    BehaviorClass.TEST: BehaviorMacro.ACTION,
    BehaviorClass.OTHER: BehaviorMacro.OTHER,
}

# 参与细分类计数/触发规则的行为类（OTHER 不算「同类重复」）
_TRACKED_CLASSES = frozenset(
    c
    for c in BehaviorClass
    if c not in (BehaviorClass.OTHER,)
)

# ---------------------------------------------------------------------------
# 行为分类（工具名 / 命令 argv0 → 行为类）
# ---------------------------------------------------------------------------

# 精确别名表：归一化后的工具名 → 行为类。
# 覆盖 DSH / Claude / Codex / 常见命令 的命名习惯（小写、去空格、去扩展名）。
_CLASS_ALIASES: dict[BehaviorClass, frozenset[str]] = {
    BehaviorClass.SEARCH: frozenset(
        {
            "web_search", "websearch", "search", "search_web", "search_internet",
            "duckduckgo_search", "google_search", "bing_search", "search_google",
            "browse_search", "search_the_web", "semantic_search", "web_search_tool",
            "search_engine",
        }
    ),
    BehaviorClass.READ: frozenset(
        {
            "read", "read_file", "read_files", "load_file", "open_file", "view",
            "grep", "rg", "grep_file", "grep_in_files", "search_files_content",
            "glob", "find_files", "list_files", "list_file", "ls_files",
            "cat", "get-content", "get_content", "type", "head", "tail",
            "less", "more", "read_multi", "read_multiple", "fetch_file",
            "file_read", "read_text_file", "peek_file",
        }
    ),
    BehaviorClass.THINK: frozenset(
        {
            "think", "thinking", "reason", "reasoning", "plan", "analyze",
            "analyze_plan", "reflect", "reflection", "deliberate", "rethink",
        }
    ),
    BehaviorClass.NAVIGATION: frozenset(
        {
            "pwd", "getcwd", "cwd", "ls", "dir", "cd", "find", "realpath",
            "which", "stat", "locate", "tree", "explore", "navigate",
            "list_directory", "list_dir", "get_working_directory",
        }
    ),
    BehaviorClass.EDIT: frozenset(
        {
            "edit", "edit_file", "write", "write_file", "patch", "apply_patch",
            "applypatch", "create_file", "append_file", "append_to_file",
            "rewrite", "update_file", "modify", "replace", "insert", "edit_text",
            "create", "create_or_replace", "file_write", "file_edit",
        }
    ),
    BehaviorClass.EXECUTE: frozenset(
        {
            "bash", "pwsh", "powershell", "shell", "exec", "command", "run",
            "terminal", "cmd", "sh", "zsh", "execute_command", "run_command",
            "run_terminal", "exec_command", "run_bash", "run_shell",
            "execute", "tool_runner", "spawn", "system", "python_exec",
        }
    ),
    BehaviorClass.TEST: frozenset(
        {
            "pytest", "test", "run_tests", "npm_test", "npm test", "vitest",
            "jest", "playwright", "run_test", "unit_test", "integration_test",
            "test_runner", "check", "lint", "typecheck", "tsc", "mypy",
        }
    ),
}

# 命令型工具（按 argv0 判定命令意图）：bash/pwsh/shell/exec/command 等
_CMD_TOOLS = frozenset(
    {
        "bash", "pwsh", "powershell", "shell", "exec", "command", "run",
        "terminal", "cmd", "sh", "zsh", "execute_command", "run_command",
        "run_terminal", "exec_command", "run_bash", "run_shell", "execute",
        "spawn", "system", "python_exec",
    }
)

# 命令 argv0 关键字 → 行为类（优先级从上到下）
_ARGV0_CLASSES: tuple[tuple[tuple[str, ...], BehaviorClass], ...] = (
    (("pytest", "vitest", "jest", "playwright", "mocha", "cypress", "tape", "ava"), BehaviorClass.TEST),
    (("npm", "npx", "pnpm", "yarn", "bun"), BehaviorClass.EXECUTE),
    (("pip", "pip3", "pipx", "uv", "conda", "brew", "apt", "apt-get", "dnf", "yum"), BehaviorClass.EXECUTE),
    (("curl", "wget", "fetch"), BehaviorClass.SEARCH),
    (("find", "locate", "rg", "grep", "awk", "sed"), BehaviorClass.READ),
    (("ls", "dir", "pwd", "cd", "stat", "which", "realpath"), BehaviorClass.NAVIGATION),
    (("git", "make", "cmake", "cargo", "go", "node", "python", "python3"), BehaviorClass.EXECUTE),
)

# 特殊工具名（命令型工具不在别名表里的后缀提示）：工具名含这些片段 → 行为类
_SUBSTR_CLASSES: tuple[tuple[str, BehaviorClass], ...] = (
    ("search", BehaviorClass.SEARCH),
    ("grep", BehaviorClass.READ),
    ("glob", BehaviorClass.READ),
    ("read", BehaviorClass.READ),
    ("browse", BehaviorClass.SEARCH),
    ("think", BehaviorClass.THINK),
    ("reason", BehaviorClass.THINK),
    ("plan", BehaviorClass.THINK),
)


def normalize_tool(tool: str) -> str:
    """归一化工具名：小写、去空白、去路径/扩展名。保留 ``-`` 和 ``_`` 不剥离。"""
    t = str(tool or "").strip().lower()
    # 去路径/扩展名：如 tools/mcp__web_search.py → mcp__websearch
    for sep in ("/", "\\", "."):
        if sep in t:
            t = t.split(sep)[-1]
    # 去常见前缀 mcp/tool_/tools_/codex_/file_/files_ 等
    for prefix in ("mcp", "mcp_", "tool", "tools", "codex", "file", "files", "fs"):
        if t.startswith(prefix) and t != prefix:
            t = t[len(prefix):]
            break
    return t.strip()


def _argv0_from_args(args_key: str) -> str:
    """从 args_key 提取 argv0（桥接端 summarizeArgs 已把命令首词记为 argv0:xxx）。"""
    for part in str(args_key or "").split(","):
        part = part.strip()
        if part.startswith("argv0:"):
            return part[len("argv0:"):].strip()
    return ""


def classify_tool(tool: str, args_key: str = "") -> BehaviorClass:
    """把一次工具调用归为行为类。

    判定顺序：
    1. 命令型工具（bash/pwsh/exec/command 等）→ 先用 argv0（命令首词）判定意图；
    2. 精细匹配：归一化工具名的精确别名命中；
    3. 工具名子串提示（web_search → SEARCH、grep → READ 等）；
    4. 兜底 OTHER。
    """
    norm = normalize_tool(tool)
    if not norm:
        return BehaviorClass.OTHER

    # 1) 命令型工具：先判断 argv0（命令首词）
    if norm in _CMD_TOOLS:
        argv0 = _argv0_from_args(args_key)
        if argv0:
            for keywords, cls in _ARGV0_CLASSES:
                if argv0 in keywords:
                    return cls
        return BehaviorClass.EXECUTE

    # 2) 精细匹配
    for cls, aliases in _CLASS_ALIASES.items():
        if norm in aliases:
            return cls

    # 3) 子串提示
    for sub, cls in _SUBSTR_CLASSES:
        if sub in norm:
            return cls

    return BehaviorClass.OTHER


def macro_of(cls: BehaviorClass) -> BehaviorMacro:
    """行为类 → 行为大类。"""
    return _MACRO_OF.get(cls, BehaviorMacro.OTHER)


# ---------------------------------------------------------------------------
# 触发档位
# ---------------------------------------------------------------------------


class PatternLevel(str, Enum):
    """行为模式触发档位。"""

    WARNING = "warning"   # ⚠️ 有重复倾向 / 长期探索无产出
    CONTROL = "control"   # 🛑 已明显偏离，值得 Judge 检查

    def _severity(self) -> int:
        """数值化 severity，用于升级比较（不能按 str 比较，'warning' > 'control'）。"""
        return 1 if self is PatternLevel.WARNING else 2


# 触发原因代码
class PatternReason(str, Enum):
    FINE_REPEAT_W6 = "fine_repeat_short_burst"      # W6 同类 >= 3（短时爆发）
    FINE_REPEAT_W10_CONTROL = "fine_repeat_long"     # W10 同类 >= 4（长期重复）
    FINE_REPEAT_W10_WARN = "fine_repeat_tendency"    # W10 同类 >= 3（重复倾向）
    MACRO_EXPLORE_W6 = "macro_explore_only"          # W6 EXPLORATION>=5 且 ACTION==0
    MACRO_EXPLORE_W10 = "macro_explore_no_action"    # W10 EXPLORATION>=7 且 ACTION<=1


# ---------------------------------------------------------------------------
# Judge（可选小型 LLM）
# ---------------------------------------------------------------------------


class JudgeVerdict(str, Enum):
    """Judge 判定结果。"""

    NORMAL = "NORMAL"        # 正常，无需干预
    REPLAN = "REPLAN"        # 建议重新规划（默认降级值）
    ASK_USER = "ASK_USER"    # 需要询问用户
    STOP = "STOP"            # 建议停止当前行为


_JUDGE_KEYWORDS: tuple[tuple[tuple[str, ...], JudgeVerdict], ...] = (
    (("STOP", "INTERRUPT", "HALT", "ABORT", "停止"), JudgeVerdict.STOP),
    (("ASK_USER", "ASK THE USER", "ASK HUMAN", "CONSULT", "询问"), JudgeVerdict.ASK_USER),
    (("REPLAN", "RE-PLAN", "CHANGE PLAN", "NEW PLAN", "重新规划", "换方案"), JudgeVerdict.REPLAN),
    (("NORMAL", "OK", "FINE", "NO ACTION", "CONTINUE", "正常"), JudgeVerdict.NORMAL),
)

_DEFAULT_VERDICT = JudgeVerdict.REPLAN  # Judge 不可用/无输出时降级


def parse_verdict(text: str) -> JudgeVerdict:
    """从 LLM 输出解析 Judge 判定；找不到关键词时降级 REPLAN。"""
    t = str(text or "").strip().upper()
    if not t:
        return _DEFAULT_VERDICT
    for keywords, verdict in _JUDGE_KEYWORDS:
        for kw in keywords:
            if kw in t:
                return verdict
    return _DEFAULT_VERDICT


def build_judge_prompt(behavior_summary: str, tool_sequence: str, context: str = "") -> str:
    """构造 Judge 输入。行为摘要由 detector 提供，LLM 只需判断模式是否异常。"""
    return (
        "你是一个 Agent 行为模式审查员。桌宠检测到如下行为序列，请判断 Agent 是否"
        "陷入低效循环（在探索/重复而无实质产出）。\n"
        "只输出一个词：NORMAL（正常）/ REPLAN（建议重新规划）/ "
        "ASK_USER（需询问用户）/ STOP（建议停止）。\n\n"
        f"近期工具调用序列（按时间）：\n{tool_sequence}\n\n"
        f"行为统计摘要：\n{behavior_summary}\n"
        + (f"\n附加上下文：{context}\n" if context else "")
    )


# ---------------------------------------------------------------------------
# 行为模式检测器
# ---------------------------------------------------------------------------

# 默认参数（与用户建议的第一版规则一致）
DEFAULT_W6_CONTROL = 3        # W6 同类 >= 3 → control
DEFAULT_W10_WARN = 3          # W10 同类 >= 3 → warning
DEFAULT_W10_CONTROL = 4       # W10 同类 >= 4 → control
DEFAULT_MACRO_W6_EXPLORE = 5  # W6 EXPLORATION >= 5
DEFAULT_MACRO_W6_ACTION = 0   # 且 ACTION == 0 → control
DEFAULT_MACRO_W10_EXPLORE = 7  # W10 EXPLORATION >= 7
DEFAULT_MACRO_W10_ACTION = 1   # 且 ACTION <= 1 → warning
DEFAULT_MIN_STEPS_BETWEEN = 3  # 触发后至少新增 N 个 step 才允许再次触发
DEFAULT_COOLDOWN_SECONDS = 60.0  # 触发后最少间隔（时间兜底，防高频抖动）


class _StepDecision:
    """一个 step 的行为决策（同一步并行事件合并后的结果）。"""

    __slots__ = ("step", "seq", "classes")

    def __init__(self, step: str, seq: int, classes: frozenset[BehaviorClass]) -> None:
        self.step = step
        self.seq = seq
        self.classes = classes


class BehaviorPatternDetector(QObject):
    """DSH 行为模式检测器：工具调用 → 行为类 → 双窗口规则 → warning/control。

    用法::

        detector = BehaviorPatternDetector()
        detector.feed_record("dsh", {"event": "tool/call", "tool": "web_search",
                                     "step": 5})
        detector.pattern_warning.connect(handler)
        detector.pattern_control.connect(handler)
    """

    # 行为模式预警 / 控制：（agent_key, payload）
    pattern_warning = Signal(str, object)
    pattern_control = Signal(str, object)
    # 模式解除（Agent 空闲 / 任务完成）：
    pattern_resolved = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        clock=None,
        w6_control: int = DEFAULT_W6_CONTROL,
        w10_warn: int = DEFAULT_W10_WARN,
        w10_control: int = DEFAULT_W10_CONTROL,
        macro_w6_explore: int = DEFAULT_MACRO_W6_EXPLORE,
        macro_w6_action: int = DEFAULT_MACRO_W6_ACTION,
        macro_w10_explore: int = DEFAULT_MACRO_W10_EXPLORE,
        macro_w10_action: int = DEFAULT_MACRO_W10_ACTION,
        min_steps_between: int = DEFAULT_MIN_STEPS_BETWEEN,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        judge=None,
    ) -> None:
        super().__init__(parent)
        self._clock = clock or time.monotonic
        self._w6_control = int(w6_control)
        self._w10_warn = int(w10_warn)
        self._w10_control = int(w10_control)
        self._macro_w6_explore = int(macro_w6_explore)
        self._macro_w6_action = int(macro_w6_action)
        self._macro_w10_explore = int(macro_w10_explore)
        self._macro_w10_action = int(macro_w10_action)
        self._min_steps_between = int(min_steps_between)
        self._cooldown_seconds = float(cooldown_seconds)
        # judge：可调用对象 judge(behavior_summary, tool_sequence) -> JudgeVerdict；
        # 缺省 None → Control 命中时 payload 携带默认 REPLAN 提示，不做 LLM 调用。
        self._judge = judge

        # 每个 Agent 的状态
        self._states: dict[str, dict] = {}

        self._enabled = False

    # ------------------------------------------------------------ 开关 / 生命周期

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.reset_all()

    def is_enabled(self) -> bool:
        return self._enabled

    def pause(self) -> None:
        """桌宠隐藏时暂停（无状态可清理，仅停触发）。"""
        pass

    def resume(self) -> None:
        pass

    def reset(self, agent_key: str) -> None:
        """重置指定 Agent 的模式状态（空闲/离线/任务完成）。"""
        if self._states.pop(agent_key, None) is not None:
            self.pattern_resolved.emit(agent_key)

    def reset_all(self) -> None:
        for key in list(self._states):
            self.reset(key)

    def get_config_overrides(self, config: dict) -> None:
        """从配置字典读取覆盖参数（config.agent_link 段）。"""
        if not isinstance(config, dict):
            return
        self._w6_control = int(config.get("pattern_w6_control", DEFAULT_W6_CONTROL))
        self._w10_warn = int(config.get("pattern_w10_warn", DEFAULT_W10_WARN))
        self._w10_control = int(config.get("pattern_w10_control", DEFAULT_W10_CONTROL))
        self._macro_w6_explore = int(config.get("pattern_macro_w6_explore", DEFAULT_MACRO_W6_EXPLORE))
        self._macro_w6_action = int(config.get("pattern_macro_w6_action", DEFAULT_MACRO_W6_ACTION))
        self._macro_w10_explore = int(config.get("pattern_macro_w10_explore", DEFAULT_MACRO_W10_EXPLORE))
        self._macro_w10_action = int(config.get("pattern_macro_w10_action", DEFAULT_MACRO_W10_ACTION))
        self._min_steps_between = int(config.get("pattern_min_steps_between", DEFAULT_MIN_STEPS_BETWEEN))
        self._cooldown_seconds = float(config.get("pattern_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))

    def set_judge(self, judge) -> None:
        """注入 Judge 可调用对象（运行时切换，通常由 AgentLinkManager 配置）。"""
        self._judge = judge

    # ------------------------------------------------------------ 事件消费

    def feed_record(self, agent_key: str, record: dict) -> None:
        """消费一条桥接记录：行为决策进入按 step 去重的窗口，并重新评估。"""
        if not self._enabled:
            return
        if not isinstance(record, dict):
            return
        event = str(record.get("event", "") or "")

        # 空闲 / 离线 / 任务完成 → 重置
        if event == "AgentStatus" and record.get("state") in ("idle", "sleeping"):
            self.reset(agent_key)
            return
        if event == "turn/end":
            self.reset(agent_key)
            return

        # 只有「行为决策」类事件参与统计：工具调用 / 命令执行 / 工具工作流
        if event not in ("tool/call", "command/run", "tool-workflow/run-start"):
            return

        behavior = classify_tool(record.get("tool", ""), record.get("argsKey", ""))
        if behavior is BehaviorClass.OTHER:
            return

        step = record.get("step")
        if step is None:
            step = f"seq:{self._clock()}"  # 无 step 字段时用时间兜底（每次事件独立决策）
        step = str(step)

        state = self._states.setdefault(agent_key, {
            "decisions": OrderedDict(),  # step -> _StepDecision（保持插入序）
            "current": None,             # 当前累积的 step
            "current_classes": set(),
            "seq": 0,
            "last_trigger_seq": None,
            "last_trigger_at": 0.0,
            "last_trigger_level": None,  # 上次触发的档位（warning / control）
            "last_level": None,
        })

        # step 去重：同一 step 并行事件合并成一次行为决策
        if state["current"] != step:
            self._flush_step(state)
            state["current"] = step
            state["current_classes"] = set()
        state["current_classes"].add(behavior)
        self._evaluate(agent_key)

    # ------------------------------------------------------------ 内部

    def _flush_step(self, state: dict) -> None:
        """把当前 step 的累积类封存为一条决策记录（移出窗口时再裁剪）。"""
        cur = state["current"]
        if cur is None or not state["current_classes"]:
            state["current"] = None
            state["current_classes"] = set()
            return
        state["seq"] += 1
        decision = _StepDecision(cur, state["seq"], frozenset(state["current_classes"]))
        state["decisions"][cur] = decision
        # 只保留最近 20 个 step（足够 W10 判定 + cooldown 的「新增 3 步」观察窗）
        while len(state["decisions"]) > 20:
            state["decisions"].popitem(last=False)
        state["current"] = None
        state["current_classes"] = set()

    def _all_decisions(self, state: dict) -> list[_StepDecision]:
        """全部决策（已 flush 的 step + 当前进行中的 step 作为虚拟决策）。

        当前 step 尚未 flush 时（要等下一个 step 到达才封存），若不入窗会导致
        行为模式响应滞后一步（最后一个 step 永远不算数）。因此把当前累积的类
        作为一个 seq 号 = state["seq"]+1 的虚拟决策参与统计。
        """
        decisions = list(state["decisions"].values())
        if state["current"] is not None and state["current_classes"]:
            decisions.append(
                _StepDecision(state["current"], state["seq"] + 1,
                              frozenset(state["current_classes"]))
            )
        return decisions

    def _recent_decisions(self, decisions: list[_StepDecision], n: int) -> list[_StepDecision]:
        """最近 n 个 step 的决策（按 seq 升序）。"""
        return decisions[-n:]

    def _counts_in(self, decisions: list[_StepDecision]) -> dict[BehaviorClass, int]:
        """统计窗口内每个行为类出现在几个 step 中（每 step 同类只算一次）。"""
        counts: dict[BehaviorClass, int] = {}
        for d in decisions:
            for cls in d.classes:
                counts[cls] = counts.get(cls, 0) + 1
        return counts

    def _macro_counts(self, decisions: list[_StepDecision]) -> dict[BehaviorMacro, int]:
        counts: dict[BehaviorMacro, int] = {}
        for d in decisions:
            seen = set()
            for cls in d.classes:
                macro = macro_of(cls)
                if macro not in seen:
                    counts[macro] = counts.get(macro, 0) + 1
                    seen.add(macro)
        return counts

    def _evaluate(self, agent_key: str) -> None:
        state = self._states.get(agent_key)
        if not state:
            return
        now = self._clock()
        current_seq = state["seq"] + (1 if state["current"] is not None and state["current_classes"] else 0)
        last_trigger_seq = state["last_trigger_seq"]

        # 先做规则判定，拿到 level 后再决定 gate 策略
        decisions = self._all_decisions(state)
        if not decisions:
            return
        w6 = decisions[-6:]
        w10 = decisions[-10:]
        counts6 = self._counts_in(w6)
        counts10 = self._counts_in(w10)
        macro6 = self._macro_counts(w6)
        macro10 = self._macro_counts(w10)

        level, reason, cls, count, window = self._decide(counts6, counts10, macro6, macro10)
        if level is None:
            return

        # cooldown 门控：
        # last_trigger_seq 只用于抑制同档重复提醒，不能阻断风险等级升级；
        # 风险从 warning 升级到 control 时，跳过 step gate 和 time gate，
        # 且使用完整窗口（all_decisions）确保 warning 之前的有效事件被计入。
        prev_level = state["last_trigger_level"]
        is_upgrade = prev_level is None or level._severity() > prev_level._severity()

        if not is_upgrade:
            # 同档位：受 step gate 限制
            if last_trigger_seq is not None:
                if current_seq - last_trigger_seq < self._min_steps_between:
                    return
        # 同档位：受 time gate 限制（升级跳过）
        if not is_upgrade and state["last_trigger_at"] and (now - state["last_trigger_at"]) < self._cooldown_seconds:
            return

        # 同档位去抖：上次同档且内容一致则不重复发射（但 cooldown 已挡大部分）
        prev = state["last_trigger_level"]
        if prev == level and (state["last_trigger_at"] and (now - state["last_trigger_at"]) < self._cooldown_seconds):
            return

        # 升级使用完整窗口（含 warning 之前的有效事件）；
        # 同档位只统计上次触发之后的新事件。
        if is_upgrade:
            decisions = self._all_decisions(state)
        else:
            decisions = [d for d in self._all_decisions(state)
                         if last_trigger_seq is None or d.seq > last_trigger_seq]

        # 标记触发（cooldown 门控）
        state["last_trigger_seq"] = current_seq
        state["last_trigger_at"] = now
        state["last_trigger_level"] = level
        state["last_level"] = level

        # 构建 payload
        summary_lines = [
            f"最近{len(w6)}步 行为类分布: " + ", ".join(
                f"{k.value}={v}" for k, v in sorted(counts6.items(), key=lambda x: x[0].value)
            ) or "无",
            f"最近{len(w10)}步 行为类分布: " + ", ".join(
                f"{k.value}={v}" for k, v in sorted(counts10.items(), key=lambda x: x[0].value)
            ) or "无",
            f"大类: EXPLORATION={macro6.get(BehaviorMacro.EXPLORATION, 0)}(W6)/{macro10.get(BehaviorMacro.EXPLORATION, 0)}(W10), "
            f"ACTION={macro6.get(BehaviorMacro.ACTION, 0)}(W6)/{macro10.get(BehaviorMacro.ACTION, 0)}(W10)",
        ]
        tool_seq = " > ".join(
            cls.value for d in decisions[-12:] for cls in sorted(d.classes, key=lambda c: c.value)
        ) or ""

        # Judge：Control 才调用（Warning 只提醒）
        verdict = None
        if level is PatternLevel.CONTROL and self._judge is not None:
            try:
                verdict = self._judge("\n".join(summary_lines), tool_seq)
            except Exception:
                log.exception("行为模式 Judge 调用失败，降级 REPLAN")
                verdict = _DEFAULT_VERDICT

        payload = {
            "type": "pet/behavior-pattern",
            "level": level.value,
            "reason": reason.value,
            "class": cls.value if cls else "",
            "count": int(count),
            "window": window,
            "summary": "\n".join(summary_lines),
            "tool_sequence": tool_seq,
            "steps": len(w10),
            "verdict": verdict.value if verdict else (_DEFAULT_VERDICT.value if level is PatternLevel.CONTROL else ""),
        }

        log.info(
            "[BEHAVIOR] %s %s (%s, %s=%d, window=%s) 步骤=%d",
            agent_key, level.value, reason.value, cls.value if cls else "-", count, window, len(w10),
        )
        if level is PatternLevel.CONTROL:
            self.pattern_control.emit(agent_key, payload)
        else:
            self.pattern_warning.emit(agent_key, payload)

    def _decide(self, counts6, counts10, macro6, macro10):
        """按双窗口规则决定档位/原因。

        返回 (level, reason, cls, count, window) 或 (None, None, None, 0, "")。
        优先级：Control > Warning；短时爆发(W6) > 长期重复(W10)。
        """
        # ---- 细分类：W6 同类 >= 3 → control（短时爆发）----
        best6 = max(counts6.items(), key=lambda kv: kv[1], default=None)
        if best6 and best6[1] >= self._w6_control and best6[0] in _TRACKED_CLASSES:
            return (PatternLevel.CONTROL, PatternReason.FINE_REPEAT_W6, best6[0], best6[1], "W6")

        # ---- 细分类：W10 同类 >= 4 → control（长期重复）----
        best10 = max(counts10.items(), key=lambda kv: kv[1], default=None)
        if best10 and best10[1] >= self._w10_control and best10[0] in _TRACKED_CLASSES:
            return (PatternLevel.CONTROL, PatternReason.FINE_REPEAT_W10_CONTROL, best10[0], best10[1], "W10")

        # ---- 大类：W6 EXPLORATION >= N 且 ACTION == 0 → control ----
        if (macro6.get(BehaviorMacro.EXPLORATION, 0) >= self._macro_w6_explore
                and macro6.get(BehaviorMacro.ACTION, 0) <= self._macro_w6_action):
            return (PatternLevel.CONTROL, PatternReason.MACRO_EXPLORE_W6,
                    BehaviorClass.OTHER, macro6.get(BehaviorMacro.EXPLORATION, 0), "W6")

        # ---- 细分类：W10 同类 >= 3 → warning（重复倾向）----
        if best10 and best10[1] >= self._w10_warn and best10[0] in _TRACKED_CLASSES:
            return (PatternLevel.WARNING, PatternReason.FINE_REPEAT_W10_WARN, best10[0], best10[1], "W10")

        # ---- 大类：W10 EXPLORATION >= N 且 ACTION <= 1 → warning ----
        if (macro10.get(BehaviorMacro.EXPLORATION, 0) >= self._macro_w10_explore
                and macro10.get(BehaviorMacro.ACTION, 0) <= self._macro_w10_action):
            return (PatternLevel.WARNING, PatternReason.MACRO_EXPLORE_W10,
                    BehaviorClass.OTHER, macro10.get(BehaviorMacro.EXPLORATION, 0), "W10")

        return (None, None, None, 0, "")
