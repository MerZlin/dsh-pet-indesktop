# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动监视器模块（DSH / Claude Code / Cursor / OpenCode / 自定义）。

设计原则（手册 §8）：
1. 绝不使用 mtime 盲轮询；
2. 统一事件协议：<config.dir>/agent-events/<agent>.jsonl，采用有界 Byte-Offset Tail 毫秒级增量读取；
3. 状态词汇统一：idle / thinking / working / attention / sleeping / error；
4. 状态 -> 桌宠动作映射：
   - thinking -> 写代码 (或 深度思考碎碎念)
   - working -> 原地敲击桌面互动
   - attention -> 气泡提示 ("需要你看一眼～")
   - error -> 气泡提示 ("好像遇到报错了…")
   - sleeping -> 待机
   - idle -> 待机
5. 低功耗：功能默认全关，每个 Agent 独立开关；隐藏时全线 pause()，显示时 resume()；
6. 写入外部配置/hooks 前必须弹窗征得用户明确同意。

批6-5 拆分：状态归约（AgentLinkReducer，纯状态机）与呈现（AgentLinkPresentation，
气泡/音效/动画调度）已拆至 pet/agent_link_reducer.py 与 pet/agent_link_presentation.py；
本文件保留监视器层与 AgentLinkManager 装配/编排（安装卸载、生命周期、配置应用）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from .agent_link_presentation import AgentLinkPresentation
from .agent_link_reducer import AgentLinkReducer
from .node_runtime import augmented_path as _augmented_path
from .node_runtime import which as _which
# 保持模块命名空间（呈现层经 pet.agent_link 模块属性在调用时解析 play_sound /
# resolve_builtin_sound，测试按模块名 patch；本文件自身不再直接调用）。
from .click_sound import play_sound, resolve_builtin_sound  # noqa: F401

log = logging.getLogger("dsh-pet-standalone")

# ----------------------------------------------------------------------
# DSH 桥接安装辅助（绕开 dsh CLI 的空格路径缺陷）
# ----------------------------------------------------------------------
# 背景：`dsh plugin` 在 Windows 上会把含空格的插件路径经 cmd.exe 二次解析拆碎
# （dsh runPlugin 的 spawnSync shell:true 引号处理缺陷，已实测：node 直调
# bin.js 同样复现），且 `pnpm install <dir>` 在 pnpm 11 中没有 add 语义、
# 旧实现还会把 profiles 目录下的 node_modules 当 profile 并触发整批回滚。
# 因此桥接插件的安装/卸载改为：
#   node <pnpm CLI> add|remove <pkg>   —— 数组传参，不经任何 cmd 中转；
# 并自行维护 profile 的 dsh.profile.bundles 层（等价于 dsh plugin add 的
# reconcile 产物）。安装产物与 dsh 版本无关，EAC 桌面端 / 原生 CLI 均可加载。

DSH_PLUGIN_NAME = "@dsh-pet/bridge"
DSH_PROFILE_HOME = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))


def _real_profiles() -> list[Path]:
    """真实存在的 dsh profile：profiles 目录下含 package.json 的子目录。

    排除 node_modules 等非 profile 目录（旧版曾把它们当成 profile 去安装）。
    """
    profiles_dir = DSH_PROFILE_HOME / "profiles"
    if not profiles_dir.is_dir():
        return []
    return sorted(
        p for p in profiles_dir.iterdir()
        if p.is_dir() and (p / "package.json").is_file()
    )


def _find_pnpm_cli() -> str | None:
    """定位 pnpm 的 JS CLI 入口，不触发安装。"""
    env = os.environ.get("DSH_PNPM_BIN")
    if env and Path(env).is_file():
        return env
    pnpm = _which("pnpm")
    if pnpm:
        resolved = Path(pnpm).resolve()
        if resolved.is_file() and resolved.suffix.lower() in {".js", ".cjs", ".mjs"}:
            return str(resolved)
        for base in (Path(pnpm).parent, resolved.parent):
            cand = base / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
            if cand.is_file():
                return str(cand)
    npm = _which("npm")
    if npm:
        cand = Path(npm).parent / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
        if cand.is_file():
            return str(cand)
    return None


def _npm_cli() -> str | None:
    """定位 npm 的 JS CLI 入口（由 node 直调，绕开 .cmd 的空格引号坑）。"""
    npm = _which("npm")
    if not npm:
        return None
    resolved = Path(npm).resolve()
    if resolved.name == "npm-cli.js" and resolved.is_file():
        return str(resolved)
    for base in (Path(npm).parent, resolved.parent):
        cand = base / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if cand.is_file():
            return str(cand)
    return None


def _pnpm_cli() -> str | None:
    """定位 pnpm 的 JS CLI；缺失时尝试通过 npm 全局安装一次。"""
    cli = _find_pnpm_cli()
    if cli:
        return cli
    node = _which("node")
    npm_cli = _npm_cli()
    if not node or not npm_cli:
        return None
    try:
        proc = subprocess.run(
            [node, npm_cli, "install", "-g", "pnpm"],
            capture_output=True, text=True, timeout=300, shell=False,
            env={**os.environ, "PATH": _augmented_path()},
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return _find_pnpm_cli()


def _run_pnpm(profile_dir: Path, *args: str) -> tuple[int, str]:
    """node 直调 pnpm CLI（数组传参，无 cmd 中转），返回 (返回码, 合并输出)。"""
    node = _which("node")
    cli = _pnpm_cli()
    if not node:
        return 127, "找不到 node，请先安装 Node.js"
    if not cli:
        return 127, "需要 pnpm，自动安装失败，请手动运行: npm install -g pnpm"
    try:
        proc = subprocess.run(
            [node, cli, *args], capture_output=True, text=True,
            timeout=300, shell=False, cwd=str(profile_dir),
            env={**os.environ, "PATH": _augmented_path()},
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return -1, str(exc)


def _read_manifest(profile_dir: Path) -> dict | None:
    try:
        return json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def _manifest_has_plugin(pkg: dict) -> bool:
    return DSH_PLUGIN_NAME in ((pkg.get("dependencies") or {}) or {})


def _manifest_set_bundle(pkg: dict, profile_dir: Path, present: bool) -> bool:
    """保持 dsh.profile.bundles 与插件安装状态一致，返回是否发生写入。"""
    bundles = (
        pkg.setdefault("dsh", {}).setdefault("profile", {})
        .setdefault("bundles", [])
    )
    has = DSH_PLUGIN_NAME in bundles
    if present and not has:
        bundles.append(DSH_PLUGIN_NAME)
    elif not present and has:
        bundles.remove(DSH_PLUGIN_NAME)
    else:
        return False
    (profile_dir / "package.json").write_text(
        json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True

# 标准统一状态词汇
VALID_STATES = {"idle", "thinking", "working", "attention", "sleeping", "error"}

# 通用事件名到统一状态的默认映射
DEFAULT_EVENT_STATE_MAP = {
    # 常用生命周期
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "UserPromptSubmit": "thinking",
    "thinking": "thinking",
    # 工具与执行
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "attention",
    "StopFailure": "error",
    "SubagentStop": "attention",
    "error": "error",
    "idle": "idle",
}


def normalize_event_state(event_name: str, explicit_state: str = "") -> str:
    """根据事件名或显式 state 字段规范化为标准状态词汇。

    返回空串表示「不认识的事件，忽略」——绝不把未知事件默认当成 working
    （Cursor 等的 transcript 行类型繁杂，默认 working 会导致过度触发）。
    """
    if explicit_state and explicit_state in VALID_STATES:
        return explicit_state
    return DEFAULT_EVENT_STATE_MAP.get(event_name, "")


def cursor_line_state(data: dict) -> str:
    """Cursor agent-transcripts 真实格式（{role, message:{content:[...]}}）→ 状态。

    - role=user：用户刚发话 → thinking
    - role=assistant 且 content 含 tool_use → working
    - role=assistant 纯文本（回合结束）→ idle
    其他一律忽略（""）。显式 state/event 字段（统一协议通道）优先。
    """
    explicit = str(data.get("state", "") or "")
    if explicit:
        return normalize_event_state("", explicit)
    role = str(data.get("role", "") or "").lower()
    if role == "user":
        return "thinking"
    if role == "assistant":
        content = data.get("message", {})
        if isinstance(content, dict):
            content = content.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    return "working"
        return "idle"
    # 兼容 type/event 事件名字段（统一协议通道或 Claude 风格事件名）
    return normalize_event_state(str(data.get("type") or data.get("event") or ""))


def cursor_line_tool(data: dict) -> str:
    """从 Cursor transcript 行提取 tool_use 的工具名（content 块里的 name）。取不到返回 ""。"""
    if not isinstance(data, dict):
        return ""
    if str(data.get("role", "") or "").lower() != "assistant":
        return ""
    content = data.get("message", {})
    if isinstance(content, dict):
        content = content.get("content")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                return str(c.get("name", "") or "").strip()
    return ""


def opencode_event_state(event_type: str, data_raw: str) -> str:
    """OpenCode 本地 SQLite event 表（type, data JSON）→ 状态。

    - message.updated 且 role=user → thinking
    - part type=step-start → working；reasoning → thinking；step-finish → idle
      （reason="tool-calls" 例外：模型停笔等工具结果（含 task 子代理长跑），
      回合未完，返回 "" 维持现状——否则派发子代理后主代理等待期会被误报为
      结束，触发假完成音/气泡；本机 opencode.db 实测 tool-calls 占绝大多数）
    - session.created → idle
    其余忽略。data 解析失败返回 ""。"""
    try:
        data = json.loads(data_raw) if isinstance(data_raw, str) else {}
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    if event_type.startswith("message.updated"):
        role = str((data.get("info") or {}).get("role", ""))
        return "thinking" if role == "user" else ""
    if event_type.startswith("message.part.updated"):
        part = data.get("part") or {}
        pt = str(part.get("type", ""))
        if pt == "step-finish":
            # 只对已知的忙碌信号特判；无 reason（旧版兼容）/ stop 照旧 idle
            if str(part.get("reason") or "") == "tool-calls":
                return ""
            return "idle"
        return {"step-start": "working", "reasoning": "thinking"}.get(pt, "")
    if event_type.startswith("session.created"):
        return "idle"
    return ""


def opencode_event_tool(event_type: str, data_raw: str) -> str:
    """从 OpenCode 事件提取工具名（message.part.updated 且 part.type=="tool" 时
    part.tool 即工具名，如 read/bash/edit）。取不到返回 ""。"""
    if not event_type.startswith("message.part.updated"):
        return ""
    try:
        data = json.loads(data_raw) if isinstance(data_raw, str) else {}
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    part = data.get("part") or {}
    if str(part.get("type", "")) != "tool":
        return ""
    return str(part.get("tool", "") or "").strip()


class ByteOffsetTailer:
    """有界 Byte-Offset 文件增量行读取器。

    特性：
    - 记录上次读取的 byte offset；
    - 启动时若 offset 为 0 且文件已有内容，执行 backfill 防护（移动到末尾），防止重放历史事件；
    - 文件截断/轮转（当前大小 < offset）时安全重置到头部；
    - 单次读取最大字节数有界（如 64KB），防止大文件卡顿；
    - 零外部依赖，毫秒级读取。
    """

    def __init__(self, file_path: Path | str, max_chunk_bytes: int = 65536) -> None:
        self.file_path = Path(file_path)
        self.offset: int = 0
        self.max_chunk_bytes = max_chunk_bytes
        self._initial_backfill_done = False
        self._partial: bytes = b""  # 跨读取边界的未完成行缓冲（防止半行被丢弃）
        self._discard_until_newline = False  # 超长行丢弃模式：跳到下一个换行再恢复
        self._file_id: tuple[int, ...] | None = None  # 文件身份（Win: ino+ctime_ns / POSIX: dev+ino），识别同路径轮转新文件

    def reset(self) -> None:
        self.offset = 0
        self._initial_backfill_done = False
        self._partial = b""
        self._discard_until_newline = False
        self._file_id = None

    def read_new_lines(self) -> list[str]:
        """读取文件自上次 offset 以来的全部完整新增行。

        半行处理：若读取末尾不是换行符（行被 chunk 截断或写入方尚未写完），
        未完成部分存入 _partial，下次读取时拼回——绝不把半行当整行解析。"""
        if not self.file_path.is_file():
            return []

        try:
            st = self.file_path.stat()
            size = st.st_size
            # 文件身份识别（应对 bridge rename 轮转出同路径新文件）：
            # Windows 用 (ino, ctime_ns)——ctime 是创建时间，追加不变、轮转变化；
            # POSIX 的 ctime 是 inode 变更时间（每次追加都变），只能用 (dev, ino)。
            if os.name == "nt":
                file_id = (st.st_ino, st.st_ctime_ns)
            else:
                file_id = (st.st_dev, st.st_ino)
        except (OSError, AttributeError):
            return []

        # 启动时的首次初始化：若未指定 offset 则跳至当前末尾（backfill 防护）
        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = size
            self._file_id = file_id
            self._partial = b""
            return []

        # 文件被截断，或被轮换成同路径的新文件（bridge rename 后新文件可能
        # 在下次轮询前就长到不小于旧 offset，只看 size 会永久跳过新文件前部）
        if size < self.offset or (self._file_id is not None and file_id != self._file_id):
            self.offset = 0
            self._partial = b""
            self._discard_until_newline = False  # 旧文件的超长行丢弃状态不得泄漏进新文件
        self._file_id = file_id

        if size == self.offset:
            return []

        bytes_to_read = min(size - self.offset, self.max_chunk_bytes)
        try:
            with open(self.file_path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(bytes_to_read)
                self.offset = f.tell()
        except OSError as exc:
            log.warning("读取 tail 文件失败 %s: %s", self.file_path, exc)
            return []

        chunk = self._partial + chunk

        # 超长行丢弃模式：上个 chunk 已确认某行超过上限，跳到下一个换行再恢复
        if self._discard_until_newline:
            idx = chunk.find(b"\n")
            if idx == -1:
                return []
            chunk = chunk[idx + 1:]
            self._discard_until_newline = False

        if chunk and not chunk.endswith(b"\n"):
            # 末尾是不完整的半行：留到下次拼接
            idx = chunk.rfind(b"\n")
            if idx == -1:
                self._partial = chunk
                chunk = b""
            else:
                self._partial = chunk[idx + 1:]
                chunk = chunk[: idx + 1]
            # 防呆：单行超过上限时进入丢弃模式（跳过该超长行剩余部分，
            # 避免把它的"后半截"误当成一条新事件解析）
            if len(self._partial) > self.max_chunk_bytes:
                log.warning("tail 行超过 %d 字节上限，丢弃该超长行: %s", self.max_chunk_bytes, self.file_path)
                self._partial = b""
                self._discard_until_newline = True
        else:
            self._partial = b""

        # utf-8-sig：兼容 PowerShell Add-Content -Encoding UTF8 在文件首行写入的 BOM
        text = chunk.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]


@dataclass(frozen=True)
class AgentEvent:
    """监视器 → Manager 的联动事件载荷（经 state_changed / activity 信号传递）。

    字段取自实际事件流（BaseAgentMonitor._poll 解析 + 发射代次），不臆造：
    - agent: 监视器 key（dsh / claude / cursor / opencode / 自定义）
    - kind: 事件种类，"state"（状态变更）或 "tool"（工具过程汇报）
    - gen: 发射代次（worker 重启后旧代次的迟到信号由接收端校验丢弃）
    - state: 归一化状态（kind=="state" 时，六态词汇之一）
    - tool: 工具名（kind=="tool" 时，如 bash / read / edit）

    说明：事件文件行里另有 ts / agent 字段（docs/AGENT_LINK_PROTOCOL.md §2.2），
    但监视器→Manager 信号路径目前不携带时间戳（agent 由监视器自身 key 决定，
    故不重复建模）；event_id / session_id 不存在于本路径，按禁止臆造字段不建模。
    """

    agent: str
    kind: str
    gen: int = 0
    state: str = ""
    tool: str = ""


class BaseAgentMonitor(QObject):
    """Agent 监视器抽象基类。

    线程模型（B9 重做，设计见 _plan/B9_DESIGN.md + 设计评审）：
    - 每个监视器一条专属 daemon worker 线程，自持 1.5s 节奏循环；
      所有 I/O（文件 tail / 目录扫描 / SQLite）都在 worker 线程，GUI 零 I/O；
    - 发射带代次号（emit_gen）：worker 捕获自己启动时的代次，重启后旧线程的
      迟到发射带旧代次，由 manager 接收端校验丢弃（发送端标志挡不住
      emit→dispatch 竞态，代次校验必须在做接收端——设计评审结论）；
    - pause 只跳过读取、绝不推进 offset/rowid（事件不丢）；
    - stop 有界 join；join 超时（病态 I/O 卡死）则 start 拒绝重启，
      绝不允许双 worker 同时读写共享状态。
    """

    state_changed = Signal(object)  # AgentEvent(kind="state")
    activity = Signal(object)       # AgentEvent(kind="tool") —— 仅事件带工具名时发

    _POLL_INTERVAL_S = 1.5
    _STOP_JOIN_TIMEOUT_S = 2.0  # 有界等待：绝不无界 join（GUI 冻结教训）

    def __init__(self, agent_key: str, config_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.agent_key = agent_key
        self.config_dir = Path(config_dir)
        self.events_dir = self.config_dir / "agent-events"
        self.events_file = self.events_dir / f"{agent_key}.jsonl"
        self._running = False
        self._paused = False
        self._tailer = ByteOffsetTailer(self.events_file)
        # worker 线程状态
        self._worker: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._gen = 0        # 启动代次（start 时自增）
        self._emit_gen = 0   # 当前发射代次（worker/直调 _poll 发射时携带）
        self._mkdir_on_start = True  # CustomAgentMonitor 只读外部文件时不建目录
        # pause 中途落在 poll 里的发射暂存 outbox，resume 时补发（宁可晚到不可丢，
        # 否则 offset 已推进但隐藏期信号被丢弃 = 事件丢失）。容量有界。
        self._outbox: list[tuple] = []
        self._outbox_lock = threading.Lock()
        self._OUTBOX_CAP = 500

        # 销毁兜底（全审 P1-2，Fix A1 同款模式）：worker 线程 target 是
        # bound method → 线程强引用本 wrapper → __del__ 永不触发；C++ 对象
        # 被销毁（父链删除/管理器 GC）时自身 bound-method 槽在 PySide6 下
        # 从不被调用（调查 §2.1 实证），必须连无 receiver 的 callable。
        # lambda 捕获 self 形成引用环，由 _break_destroyed_conn() 显式断开
        # （stop/销毁兜底路径，对照 WebMClip.cleanup 的 Fix A1；B9 复审 P2
        # ——不依赖 Qt 内部清理，保证 wrapper 可回收与生命周期闭环）。
        self._destroyed_conn = self.destroyed.connect(
            lambda *_: BaseAgentMonitor._destroyed_guard(self)
        )
        # 销毁兜底一次性标记（B9 R2 复审 P2）：_destroyed_guard 只在第一次
        # 调用时真正执行（断环 + 停 worker + 派发 reaper），worker 仍存活时
        # 的重复触发（显式兜底、异常重入、测试/退出期重复调用）一律幂等，
        # 绝不重复创建 agent-monitor-reap-* 线程。start() 重建 worker 时
        # 重新武装（新 worker 代次需要新的兜底）。
        self._destroy_guard_ran = False
        self._destroy_guard_lock = threading.Lock()

    def _break_destroyed_conn(self) -> None:
        """显式断开 destroyed 的 lambda 连接并清空 connection（Fix A1）。

        lambda 捕获 self 形成 monitor→connection→lambda→monitor 引用环；
        不显式断开则只依赖 C++ 删除时 Qt 的连接清理（B9 复审 P2：不等价于
        WebMClip.cleanup 的完整生命周期闭环）。stop 完成与销毁兜底两条路径
        都调用；C++ 已删场景 disconnect 抛 RuntimeError 属预期（连接随 C++
        信号消亡，Qt 侧已清理）。幂等：connection 已清空时是无操作。
        """
        conn = getattr(self, "_destroyed_conn", None)
        if conn is not None:
            try:
                self.destroyed.disconnect(conn)
            except RuntimeError:
                pass
            self._destroyed_conn = None

    def _ensure_destroyed_conn(self) -> None:
        """start 重启后重建 destroyed 兜底连接（stop 已断开，生命周期闭环）。"""
        if self._destroyed_conn is None:
            self._destroyed_conn = self.destroyed.connect(
                lambda *_: BaseAgentMonitor._destroyed_guard(self)
            )

    def is_running(self) -> bool:
        return self._running and not self._paused

    # ---------------- 生命周期（GUI 线程调用） ----------------

    def start(self) -> bool:
        """启动 worker 线程。旧 worker 未死透（上轮 join 超时）则拒绝重启。"""
        if self._worker is not None and self._worker.is_alive():
            log.warning("Agent 监视器 [%s] 旧 worker 未退出，拒绝重启（防双 worker）", self.agent_key)
            return False
        self._ensure_destroyed_conn()  # 上一轮 stop 已断开：重启重建兜底连接（Fix A1 闭环）
        with self._destroy_guard_lock:
            self._destroy_guard_ran = False  # 新 worker 代次：重新武装销毁兜底（B9 R2 P2）
        self._worker_stop.set()  # 兜底：万一旧 event 还在 set 状态…先清再建
        self._worker_stop = threading.Event()
        self._gen += 1
        self._emit_gen = self._gen
        self._running = True
        self._paused = False
        if self._mkdir_on_start:
            self.events_dir.mkdir(parents=True, exist_ok=True)
        self._tailer.reset()
        gen = self._gen
        self._worker = threading.Thread(
            target=self._work_loop, args=(gen,), daemon=True,
            name=f"agent-monitor-{self.agent_key}",
        )
        self._worker.start()
        log.info("Agent 监视器 [%s] 已启动", self.agent_key)
        return True

    def begin_stop(self) -> None:
        """停止第一阶段：作废旧代次+清状态+发停止信号（不 join，供批量关闭先广播）。

        _emit_gen 置 -1 让接收端立即拒收本代次的迟到信号（含已入队未派发的）——
        否则 stop 后队列里的旧信号仍会被当成当前代次处理。"""
        self._emit_gen = -1
        self._running = False
        self._paused = False
        with self._outbox_lock:
            self._outbox.clear()
        self._worker_stop.set()

    def finish_stop(self, deadline: float | None = None) -> None:
        """停止第二阶段：有界 join。deadline 为 time.monotonic() 绝对时间。

        完成后显式断开 destroyed 连接（Fix A1 断环，B9 复审 P2）：本监视器
        已退役，lambda 引用环不再需要；wrapper 可被 GC，不依赖 Qt 内部清理。
        """
        worker = self._worker
        if worker is not None and worker.is_alive():
            remaining = self._STOP_JOIN_TIMEOUT_S if deadline is None \
                else max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)
            if worker.is_alive():
                log.warning("Agent 监视器 [%s] worker 退出超时（病态 I/O 卡死）", self.agent_key)
        self._break_destroyed_conn()

    def stop(self) -> None:
        self.begin_stop()
        self.finish_stop()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    @staticmethod
    def _destroyed_guard(mon: "BaseAgentMonitor") -> None:
        """C++ 对象销毁兜底：未经 shutdown()/stop() 直接销毁时停掉 worker，
        防止 daemon 轮询线程变僵尸（全审 P1-2；崩溃 dump 现场 3× _work_loop
        存活为证——窗口/管理器被 GC 而未走 closeEvent/switch_character/
        aboutToQuit 的显式 shutdown 路径时，worker 永久轮询并保活旧窗口）。

        与 begin_stop 同语义（作废代次 + 停轮询）再加有界 join；正常
        stop 后再销毁是纯幂等无操作。

        执行线程（B9 复审 P2 确认）：destroyed 信号在 QObject 所在线程
        （GUI 线程）随 C++ 析构同步发射——**join 绝不能在这里执行**，否则
        最多阻塞 GUI _STOP_JOIN_TIMEOUT_S 秒（worker 可能卡在 I/O 里）。
        因此：① 先显式断开 destroyed 连接（Fix A1 断环，同时保证重复销毁
        幂等——第二次调用时 connection 已清空）；② 有界 join 挪到一次性
        daemon 回收线程执行，调用立即返回。worker 收到 stop 事件后自行
        退出（轮询循环每轮检查），reaper 只是有界等待并上报超时。

        幂等（B9 R2 复审 P2）：_destroy_guard_ran 一次性标记 + 锁保证
        guard 只真正执行一次——worker 仍存活时重复触发（显式兜底、异常
        重入、测试/退出期重复调用）不会重复创建 reaper 线程。reaper 自身
        无泄漏：daemon 线程（不阻止进程退出）、对同一 worker 做一次有界
        join（≤ _STOP_JOIN_TIMEOUT_S）、做完即退（不持有 monitor 引用，
        不会反向保活 wrapper）。
        """
        with mon._destroy_guard_lock:
            if mon._destroy_guard_ran:
                return
            mon._destroy_guard_ran = True
        try:
            mon._break_destroyed_conn()
            mon._worker_stop.set()
            mon._emit_gen = -1
            mon._running = False
            mon._paused = False
            worker = mon._worker
            if worker is not None and worker.is_alive():
                threading.Thread(
                    target=BaseAgentMonitor._reap_worker,
                    args=(worker, mon._STOP_JOIN_TIMEOUT_S, mon.agent_key),
                    daemon=True,
                    name=f"agent-monitor-reap-{mon.agent_key}",
                ).start()
        except Exception:
            log.debug("Agent 监视器 [%s] 销毁兜底异常", mon.agent_key, exc_info=True)

    @staticmethod
    def _reap_worker(worker: threading.Thread, timeout: float, agent_key: str) -> None:
        """回收线程：有界 join 已停止信号的 worker（GUI 线程不阻塞）。"""
        worker.join(timeout=timeout)
        if worker.is_alive():
            log.warning("Agent 监视器 [%s] 销毁兜底 worker 退出超时（病态 I/O 卡死）", agent_key)

    def pause(self) -> None:
        """暂停读取（不推进 offset，事件不丢）。worker 线程保持存活空转。"""
        if self._running:
            self._paused = True

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            # 补发 pause 期间暂存的发射（GUI 线程直接 emit，即直接派发）
            with self._outbox_lock:
                pending = list(self._outbox)
                self._outbox.clear()
            for signal, args in pending:
                signal.emit(*args)

    # ---------------- 发射（worker 或测试直调） ----------------

    def _emit_state(self, state: str, gen: int) -> None:
        self._emit(self.state_changed, (AgentEvent(agent=self.agent_key, kind="state", state=state, gen=gen),))

    def _emit_tool(self, tool: str, gen: int) -> None:
        self._emit(self.activity, (AgentEvent(agent=self.agent_key, kind="tool", tool=tool, gen=gen),))

    def _emit(self, signal, args: tuple) -> None:
        """pause 中暂存 outbox（resume 补发），否则直接 emit。

        容量策略：
        - 连续重复的相同状态去重（状态流大量是同态重复）；
        - 满容量时丢最旧的 activity（过程汇报本来就是噪音）；
        - 状态事件绝不丢：没有 activity 可丢时允许超过容量上限
          （一条记录仅几十字节，且重复状态已去重，现实增长有界）。"""
        if self._paused and self._running:
            with self._outbox_lock:
                if signal is self.state_changed and self._outbox:
                    last_sig, last_args = self._outbox[-1]
                    if last_sig is self.state_changed and last_args[0].state == args[0].state:
                        return  # 连续重复状态，去重
                if len(self._outbox) >= self._OUTBOX_CAP:
                    # 先丢最旧的 activity 腾位；状态事件绝不丢：
                    # 没有 activity 可丢时允许状态超过容量上限（一条仅几十字节，
                    # 且连续重复状态已去重——现实里攒不到有意义的内存量）。
                    for i, (sig, _) in enumerate(self._outbox):
                        if sig is self.activity:
                            del self._outbox[i]
                            break
                    else:
                        if signal is self.activity:
                            return  # 全是状态且已满：丢本条 activity
                self._outbox.append((signal, args))
            return
        signal.emit(*args)

    # ---------------- worker 线程 ----------------

    def _work_loop(self, gen: int) -> None:
        """worker 主循环：1.5s 节奏；stop_event.wait 可被 stop 立即唤醒。

        首轮先等一个周期再读（stop_event.wait 返回 True=被停止，直接退出）：
        启动瞬间不抢读——_poll() 同时是测试的直调 seam（tests 直接驱动
        _poll 验证解析逻辑），worker 若立即抢读会和直调竞争同一个 tailer。
        """
        self._worker_started()
        while not self._worker_stop.wait(self._POLL_INTERVAL_S):
            if not self._paused:
                try:
                    self._poll(gen=gen)
                except Exception:
                    log.debug("Agent 监视器 [%s] 轮询异常", self.agent_key, exc_info=True)

    def _worker_started(self) -> None:
        """worker 线程开场钩子：worker 独占状态的初始化放这里（worker 线程内执行）。

        不要在 start() 里从 GUI 线程写这些状态——旧 worker 可能仍存活，
        GUI 写入会与在飞 worker 交叉（B9 设计评审）。"""

    def _poll(self, gen: int | None = None) -> None:
        """读一轮统一协议 jsonl。gen=None（测试直调）时用当前发射代次。"""
        emit_gen = self._emit_gen if gen is None else gen
        lines = self._tailer.read_new_lines()
        for line in lines:
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                ev = str(data.get("event", ""))
                st = str(data.get("state", ""))
                tool = str(data.get("tool", "") or "").strip()
                if tool:
                    self._emit_tool(tool, emit_gen)
                normalized = normalize_event_state(ev, st)
                if not normalized:
                    continue  # 不认识的事件类型：忽略，不误报为 working
                self._emit_state(normalized, emit_gen)
            except Exception:
                pass


# ----------------------------------------------------------------------
# 各 Agent 具体监视器实现
# ----------------------------------------------------------------------

class DshMonitor(BaseAgentMonitor):
    """DeepSeek Harness (DSH) 监视器。

    事件来源：随桌宠内置的桥接插件（integrations/dsh-pet-bridge），开启联动时
    经用户同意后通过 `dsh plugin --profile web install <dir>` 一键安装（关闭时自动卸载）。
    插件把 agent 状态写入固定桥目录 `<数据基目录>/dsh-pet-bridge/dsh.jsonl`
    （与桌宠变体无关，源码/打包版路径一致），本监视器 byte-offset tail 读取。
    """

    PLUGIN_NAME = "@dsh-pet/bridge"

    def __init__(self, agent_key: str, config_dir: Path, parent=None) -> None:
        super().__init__(agent_key, config_dir, parent)
        # 桥目录与变体无关：config_dir = <base>/dsh-pet-standalone[-variant] → parent = <base>
        # 不变量：插件写 <base>/dsh-pet-bridge/（win32 即 %APPDATA%），
        # 若未来数据目录支持自定义根，两侧必须同步改（当前 Config 结构保证 parent==base）。
        self.events_dir = self.config_dir.parent / "dsh-pet-bridge"
        self.events_file = self.events_dir / "dsh.jsonl"
        self._tailer = ByteOffsetTailer(self.events_file)

    @staticmethod
    def bundled_plugin_dir() -> Path | None:
        """内置桥接插件目录：打包版在 sys._MEIPASS，源码运行在仓库 integrations/ 下。"""
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "integrations" / "dsh-pet-bridge")
        candidates.append(Path(__file__).resolve().parent.parent / "integrations" / "dsh-pet-bridge")
        for c in candidates:
            if (c / "package.json").is_file():
                return c
        return None

    @staticmethod
    def _list_profiles() -> list[str]:
        """枚举已存在的 dsh profile。

        只认含 cordis.yml 的目录（真实 profile 的标志）；profiles 目录下
        可能混入 node_modules 等包管理器/误操作残留的杂项目录，把它们当实例
        安装会失败并触发整体回滚，必须过滤。目录不存在或无有效 profile 时
        回退 ["web"]（安装命令会自动创建该 profile）。
        统一使用 DSH_PROFILE_HOME（尊重 DSH_HOME），与 _real_profiles 一致。
        """
        profiles_dir = DSH_PROFILE_HOME / "profiles"
        if not profiles_dir.is_dir():
            return ["web"]
        profiles = sorted(
            p.name for p in profiles_dir.iterdir()
            if p.is_dir() and (p / "cordis.yml").is_file()
        )
        return profiles or ["web"]

    @staticmethod
    def _summarize_install_error(output: str) -> str:
        """从安装输出中提取第一行有用的错误摘要。"""
        if not output or not output.strip():
            return "未知错误"

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        # 过滤掉以 'at ' 开头的堆栈行和 node_modules 路径行
        candidate_lines = [
            line for line in lines
            if not line.startswith("at ") and "node_modules" not in line
        ]
        if not candidate_lines:
            return "未知错误"

        # 优先匹配含 'ERR_' / 'error' / 'Error' 的行
        chosen_line = ""
        for line in candidate_lines:
            if "ERR_" in line or "error" in line or "Error" in line:
                chosen_line = line
                break
        if not chosen_line:
            chosen_line = candidate_lines[0]

        # 清理绝对路径（Windows 如 C:\path\file.ext 或 POSIX 如 /path/to/file.ext 或 file:///C:/...）
        # 只保留最后一段文件名
        def _replace_path(match: re.Match) -> str:
            raw_path = match.group(0)
            clean_path = raw_path.replace("\\", "/").rstrip("/")
            segment = clean_path.split("/")[-1]
            return segment or raw_path

        # 匹配 file:/// 路径、Windows 盘符路径、POSIX 绝对路径
        path_pattern = re.compile(r'(?:file:///[A-Za-z]:[^\s\'"]+|[A-Za-z]:\\[^\s\'"]+|/(?:[^\s\'"]+/)+[^\s\'"]*)')
        cleaned_line = path_pattern.sub(_replace_path, chosen_line)

        # 最长截到 60 字符
        if len(cleaned_line) > 60:
            cleaned_line = cleaned_line[:60]

        return cleaned_line or "未知错误"

    @classmethod
    def install_bridge(cls) -> tuple[bool, str]:
        """一键安装桥接插件到所有真实存在的 dsh profile。

        直接调 pnpm（node 直调，见模块头部注释）并维护 profile 的 bundles 层，
        不经过 dsh CLI（规避其在 Windows 上拆碎含空格路径的缺陷）；
        已安装的 profile 幂等跳过（只补 bundles 层）；失败不回滚已成功项。
        返回 (成功与否, 说明)。
        """
        plugin = cls.bundled_plugin_dir()
        if plugin is None:
            return False, "找不到内置桥接插件（integrations/dsh-pet-bridge）"
        if _which("node") is None:
            return False, "找不到 node，请先安装 Node.js（需包含 npm）"
        if _pnpm_cli() is None:
            return False, "需要 pnpm，自动安装失败，请手动运行: npm install -g pnpm"

        profiles = _real_profiles()
        if not profiles:
            return False, "没有可用的 dsh profile（~/.dsh/profiles 下无 package.json）"

        failed = []
        succeeded = []
        for profile in profiles:
            pkg = _read_manifest(profile)
            if pkg is None:
                failed.append(f"{profile.name}: package.json 读取失败")
                continue
            if _manifest_has_plugin(pkg):
                # 已安装：幂等补 bundles（可能此前通过别的途径装过）
                try:
                    _manifest_set_bundle(pkg, profile, True)
                except Exception as exc:
                    failed.append(f"{profile.name}: bundles 写入失败 {exc}")
                    continue
                succeeded.append(profile.name)
                continue
            rc, out = _run_pnpm(profile, "add", str(plugin))
            if rc != 0:
                failed.append(f"{profile.name}: pnpm add 失败 {(out or '')[-150:]}")
                continue
            pkg = _read_manifest(profile)
            if pkg is None:
                failed.append(f"{profile.name}: 安装后 package.json 读取失败")
                continue
            try:
                _manifest_set_bundle(pkg, profile, True)
            except Exception as exc:
                failed.append(f"{profile.name}: bundles 写入失败 {exc}")
                continue
            succeeded.append(profile.name)
        if failed:
            # 不做整批回滚：已装成功的保持不动（旧版回滚会把刚装好的反而卸掉）
            return False, "部分实例安装失败（已装成功的保持不动）——" + "；".join(failed)
        return True, f"桥接插件已安装到 {len(succeeded)} 个 dsh 实例（{', '.join(succeeded)}）"

    @classmethod
    def uninstall_bridge(cls) -> bool:
        """关闭联动时卸载桥接插件。返回是否全部成功（失败记日志）。

        幂等：未安装的 profile 直接视为成功；不再依赖 dsh CLI（同 install_bridge）。
        """
        if _which("node") is None or _pnpm_cli() is None:
            return True  # 没有运行环境视为无残留
        ok = True
        for profile in _real_profiles():
            pkg = _read_manifest(profile)
            if pkg is None or not _manifest_has_plugin(pkg):
                continue  # 未安装视为成功（幂等）
            rc, out = _run_pnpm(profile, "remove", DSH_PLUGIN_NAME)
            if rc != 0:
                ok = False
                log.warning("卸载 DSH 桥接插件失败(%s): %s", profile.name, (out or "")[-150:])
                continue
            pkg = _read_manifest(profile)
            if pkg is None:
                ok = False
                log.warning("卸载 DSH 桥接插件失败(%s): 卸载后 package.json 读取失败", profile.name)
                continue
            try:
                _manifest_set_bundle(pkg, profile, False)
            except Exception as exc:
                ok = False
                log.warning("卸载 DSH 桥接插件失败(%s): bundles 清理失败 %s", profile.name, exc)
        return ok


class ClaudeCodeMonitor(BaseAgentMonitor):
    """Claude Code 监视器。
    通过 .claude/settings.json 注入官方 hooks（PreToolUse/Stop 等）将事件追加写入。

    实现要点（终审修订）：
    - settings.json 的 hooks 必须是「数组对象」格式：
      {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}]}
      写成字符串 Claude Code 不识别；
    - hook 命令不依赖 sys.executable（PyInstaller 打包后它是桌宠 exe，不能跑 -c）：
      Windows 用落地到 agent-events 目录的 PowerShell 脚本，其他平台用 Python 脚本；
    - 注入/卸载都以脚本文件名 claude_event_hook 为标记，只动自己的条目，
      用户已有的其他 hooks 条目原样保留。
    """

    HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionStart", "UserPromptSubmit")
    HOOK_MARKER = "claude_event_hook"  # 识别本桌宠注入条目的标记
    HOOK_FLAG = "x-dsh-pet"            # 结构化字段标识

    def start(self) -> None:
        """启动时刷新 hook 脚本（脚本整体归本桌宠所有，升级版本自动覆盖旧版）。"""
        try:
            self._ensure_hook_script(self.events_file)
        except Exception as exc:
            log.debug("刷新 Claude hook 脚本失败: %s", exc)
        super().start()

    @staticmethod
    def get_settings_path() -> Path:
        return Path.home() / ".claude" / "settings.json"

    @staticmethod
    def _write_settings_atomic(settings_path: Path, data: dict) -> None:
        """原子写入 settings.json（tmp + os.replace，防中途崩溃留下损坏 JSON）。"""
        tmp = settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, settings_path)

    @classmethod
    def _ensure_hook_script(cls, events_file: Path) -> tuple[Path, str]:
        """把事件写入脚本落地到 events_file 同目录，返回 (脚本路径, 命令模板)。
        命令模板中 {script} 为脚本路径占位符、{event} 为事件名占位符。"""
        if sys.platform == "win32":
            script = events_file.parent / "claude_event_hook.ps1"
            # PowerShell 脚本：不依赖任何 Python 环境，打包版同样可用。
            # 注意：以下为普通字符串（非 f-string），{0}/{1}/{2} 是 PowerShell -f 的占位符。
            # stdin 读取 Claude Code 传入的 JSON（含 tool_name）；未重定向时跳过绝不阻塞。
            events_file.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "param([string]$EventName = 'unknown')\n"
                "$tool = ''\n"
                "if ([Console]::IsInputRedirected) {\n"
                "  try {\n"
                "    $raw = [Console]::In.ReadToEnd()\n"
                "    if ($raw) { $j = $raw | ConvertFrom-Json -ErrorAction Stop; if ($j.tool_name) { $tool = [string]$j.tool_name } }\n"
                "  } catch {}\n"
                "}\n"
                "$file = Join-Path $PSScriptRoot 'claude.jsonl'\n"
                "$rec = [ordered]@{ ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0; agent = 'claude'; event = $EventName }\n"
                "if ($tool) { $rec['tool'] = $tool }\n"
                "# ConvertTo-Json 负责全部转义，不手工拼 JSON（tool_name 含引号/控制字符也安全）\n"
                "Add-Content -Path $file -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8\n",
                encoding="utf-8",
            )
            cmd_tmpl = (
                'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" {event}'
            )
        else:
            script = events_file.parent / "claude_event_hook.py"
            events_file.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "import json, sys, time\n"
                "from pathlib import Path\n"
                "event = sys.argv[1] if len(sys.argv) > 1 else 'unknown'\n"
                "tool = ''\n"
                "try:\n"
                "    if not sys.stdin.isatty():\n"
                "        raw = sys.stdin.read()\n"
                "        if raw.strip():\n"
                "            tool = str(json.loads(raw).get('tool_name') or '')\n"
                "except Exception:\n"
                "    pass\n"
                "out = Path(__file__).with_name('claude.jsonl')\n"
                "rec = {'ts': time.time(), 'agent': 'claude', 'event': event}\n"
                "if tool:\n"
                "    rec['tool'] = tool\n"
                "with out.open('a', encoding='utf-8') as f:\n"
                "    f.write(json.dumps(rec, ensure_ascii=False) + '\\n')\n",
                encoding="utf-8",
            )
            # 源码运行时 sys.executable 是 Python；打包（frozen）时退化为 python3
            exe = sys.executable if not getattr(sys, "frozen", False) else "python3"
            cmd_tmpl = f'"{exe}" "{{script}}" {{event}}'
        return script, cmd_tmpl

    @classmethod
    def _build_command(cls, cmd_tmpl: str, script: Path, event: str) -> str:
        return cmd_tmpl.replace("{script}", str(script)).replace("{event}", event)

    @classmethod
    def _is_our_hook_entry(cls, entry: Any) -> bool:
        # 新格式认结构化字段；旧格式（早期版本注入、无标记字段）兜底认
        # command 里的脚本文件名——老用户升级后旧条目才能被正确清理/替换。
        if not isinstance(entry, dict):
            return False
        if entry.get(cls.HOOK_FLAG) is True:
            return True
        for h in entry.get("hooks") or []:
            # 旧格式（早期版本注入、无标记字段）兜底：command 含本桌宠落地脚本
            # 文件名（带扩展名，避免撞名误删用户自有条目）才认作 ours——
            # 老用户升级后旧条目才能被正确清理/替换。
            cmd = str(h.get("command", "")) if isinstance(h, dict) else ""
            if "claude_event_hook.ps1" in cmd or "claude_event_hook.py" in cmd:
                return True
        return False

    @classmethod
    def install_hooks(cls, events_file: Path) -> bool:
        """注入 Claude Code 官方 hooks（数组对象格式），事件追加到 jsonl。
        只移除/新增带本桌宠结构化标记的条目，用户已有 hooks 不受影响。"""
        settings_path = cls.get_settings_path()
        try:
            script, cmd_tmpl = cls._ensure_hook_script(events_file)

            settings_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if settings_path.is_file():
                try:
                    data = json.loads(settings_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("settings.json 根节点不是对象")
                except Exception as exc:
                    # 文件存在但解析失败：绝不能拿空配置覆盖用户已有配置，中止安装
                    log.warning("Claude settings.json 解析失败，中止注入（未改动原文件）: %s", exc)
                    return False
            hooks = data.setdefault("hooks", {})
            if not isinstance(hooks, dict):
                hooks = {}
                data["hooks"] = hooks

            for hook_name in cls.HOOK_EVENTS:
                # 先清掉我们以前注入的条目（幂等），保留用户自己的 hooks
                existing = hooks.get(hook_name)
                if isinstance(existing, list):
                    hooks[hook_name] = [
                        g for g in existing
                        if not cls._is_our_hook_entry(g)
                    ]
                else:
                    hooks[hook_name] = []
                cmd = cls._build_command(cmd_tmpl, script, hook_name)
                hooks[hook_name].append({
                    "matcher": "",
                    "hooks": [{"type": "command", "command": cmd}],
                    cls.HOOK_FLAG: True,
                })
            cls._write_settings_atomic(settings_path, data)
            return True
        except Exception as exc:
            log.warning("注入 Claude Code hooks 失败: %s", exc)
            return False

    @classmethod
    def uninstall_hooks(cls) -> bool:
        """关闭联动时移除本桌宠注入的 hooks 条目（仅带标记的，用户自有条目不碰）。"""
        settings_path = cls.get_settings_path()
        if not settings_path.is_file():
            return True
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return True
            hooks = data.get("hooks")
            if not isinstance(hooks, dict):
                return True
            for hook_name in list(hooks.keys()):
                entries = hooks.get(hook_name)
                if isinstance(entries, list):
                    kept = [
                        g for g in entries
                        if not cls._is_our_hook_entry(g)
                    ]
                    if kept:
                        hooks[hook_name] = kept
                    else:
                        del hooks[hook_name]
            cls._write_settings_atomic(settings_path, data)
            return True
        except Exception as exc:
            log.warning("移除 Claude Code hooks 失败: %s", exc)
            return False


class CursorMonitor(BaseAgentMonitor):
    """Cursor 监视器。
    扫描 Path.home() / .cursor / projects / ** / agent-transcripts / *.jsonl，
    多文件增量 tail（上限 50 个文件）。
    """
    def __init__(self, config_dir: Path, parent=None, base_dir: Path | None = None) -> None:
        super().__init__("cursor", config_dir, parent)
        self.cursor_base = base_dir or (Path.home() / ".cursor" / "projects")
        self._tailers: dict[str, ByteOffsetTailer] = {}
        self._scan_interval = 30.0  # 目录发现降频：30s 一次（tail 仍 1.5s）
        self._last_scan = 0.0

    def _poll(self, gen: int | None = None) -> None:
        # 首先检查统一 jsonl
        super()._poll(gen=gen)
        emit_gen = self._emit_gen if gen is None else gen

        if not self.cursor_base.is_dir():
            return

        now = time.time()
        # 目录发现降频：避免每 1.5s 在主线程递归 glob 整个 projects 目录。
        # 已知边界：新出现的 transcript 文件最长 30s 才被纳入 tail，
        # 其 backfill 防护会跳到文件末尾——发现间隙内写入的事件会错过（可接受）。
        if now - self._last_scan >= self._scan_interval:
            self._last_scan = now
            try:
                one_day_ago = now - 86400
                files = []
                for p in self.cursor_base.glob("**/agent-transcripts/*.jsonl"):
                    try:
                        if p.stat().st_mtime >= one_day_ago:
                            files.append(p)
                    except OSError:
                        pass
                files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:50]
                candidates = {str(f) for f in files}
                # 淘汰不再活跃的 tailer，防止长时间运行无限增长
                for stale in [k for k in self._tailers if k not in candidates]:
                    del self._tailers[stale]
                for fkey in candidates:
                    if fkey not in self._tailers:
                        self._tailers[fkey] = ByteOffsetTailer(fkey)
            except Exception as exc:
                log.debug("Cursor monitor 扫描异常: %s", exc)

        for tailer in self._tailers.values():
            for line in tailer.read_new_lines():
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    tool = cursor_line_tool(data)
                    if tool:
                        self._emit_tool(tool, emit_gen)
                    norm = cursor_line_state(data)
                    if not norm:
                        continue  # 未知 transcript 行类型：忽略
                    self._emit_state(norm, emit_gen)
                except Exception:
                    pass


class OpenCodeMonitor(BaseAgentMonitor):
    """OpenCode 监视器。

    直接只读 OpenCode 本地 SQLite 事件库（~/.local/share/opencode/opencode.db
    的 event 表，rowid 偏移增量轮询）——**无需安装任何插件**。
    同时保留统一 jsonl 通道（agent-events/opencode.jsonl）作为兼容路径。
    """

    def __init__(self, config_dir: Path, parent=None, db_path: Path | None = None) -> None:
        super().__init__("opencode", config_dir, parent)
        self.db_path = db_path or (
            Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        )
        # 声明在 __init__（此刻尚无任何线程，安全）；运行期读写归 worker 线程
        # 独占（_worker_started 里初始化/轮换重置），GUI 线程不得触碰：
        self._last_rowid: int = 0
        self._db_ready: bool = False
        self._db_file_id: tuple[int, ...] | None = None

    def _worker_started(self) -> None:
        self._db_ready = False
        self._last_rowid = 0
        self._db_file_id = None

    def _poll(self, gen: int | None = None) -> None:
        # 统一 jsonl 通道（兼容未来插件/手动注入）
        super()._poll(gen=gen)
        emit_gen = self._emit_gen if gen is None else gen

        if not self.db_path.is_file():
            return
        import sqlite3

        try:
            # 库文件被替换/重建（OpenCode 更新、删库重建）时重新 backfill：
            # 否则沿用旧 rowid 会静默漏掉新库的事件
            st = self.db_path.stat()
            file_id = (st.st_dev, st.st_ino)
            if file_id != self._db_file_id:
                self._db_file_id = file_id
                self._db_ready = False
        except OSError:
            return

        try:
            # 只读连接；WAL 模式下只读不阻塞 OpenCode 写入
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                if not self._db_ready:
                    # backfill 防护：启动时跳到当前末尾，不回放历史事件
                    self._last_rowid = db.execute(
                        "SELECT COALESCE(MAX(rowid), 0) FROM event"
                    ).fetchone()[0]
                    self._db_ready = True
                    return
                rows = db.execute(
                    "SELECT rowid, type, data FROM event WHERE rowid > ? ORDER BY rowid LIMIT 200",
                    (self._last_rowid,),
                ).fetchall()
                # 子代理（task）会话过滤：opencode 给每个子代理开独立 session
                # （session.parent_id 非空），其 step-start/step-finish 会随主会话
                # 事件一起进 event 表，不过滤的话每派发/完成一个子代理就触发一次
                # busy→idle，把「任务完成」气泡刷爆。批量查一次本批事件的会话归属。
                session_ids: set[str] = set()
                parsed: list[tuple[int, str, dict]] = []
                for rowid, ev_type, data_raw in rows:
                    try:
                        data = json.loads(str(data_raw))
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    sid = str(data.get("sessionID") or "")
                    if sid:
                        session_ids.add(sid)
                    parsed.append((int(rowid), str(ev_type), data))
                root_sessions: dict[str, bool] = {}
                if session_ids:
                    try:
                        marks = ",".join("?" * len(session_ids))
                        for sid, parent_id in db.execute(
                            f"SELECT id, parent_id FROM session WHERE id IN ({marks})",
                            tuple(session_ids),
                        ):
                            root_sessions[str(sid)] = parent_id is None
                    except Exception:
                        # 老库没有 session 表等异常：全部当主会话（保守不丢事件）
                        root_sessions = {}
            finally:
                db.close()
        except Exception as exc:
            log.debug("OpenCode sqlite 读取异常: %s", exc)
            return

        for rowid, ev_type, data in parsed:
            self._last_rowid = max(self._last_rowid, rowid)
            sid = str(data.get("sessionID") or "")
            # 查到归属且是子代理会话 → 整条跳过（状态和工具气泡都不报）
            if sid and root_sessions and root_sessions.get(sid) is False:
                continue
            data_raw = json.dumps(data)
            state = opencode_event_state(ev_type, data_raw)
            if state:
                self._emit_state(state, emit_gen)
            tool = opencode_event_tool(ev_type, data_raw)
            if tool:
                self._emit_tool(tool, emit_gen)


class CustomAgentMonitor(BaseAgentMonitor):
    """自定义联动 Agent 监视器（agent_link.custom_agents 配置驱动）。

    只读监听用户指定路径的统一协议 JSONL 事件文件（docs/AGENT_LINK_PROTOCOL.md §4）：
    不创建目录、不写任何外部位置、无需授权弹窗；文件不存在时静默空转等待，
    出现后自动开始增量读取（backfill 防护跳过历史内容）。"""

    def __init__(self, agent_key: str, config_dir: Path, events_path: str, parent=None) -> None:
        super().__init__(agent_key, config_dir, parent)
        self.events_file = Path(events_path).expanduser()
        self.events_dir = self.events_file.parent
        self._tailer = ByteOffsetTailer(self.events_file)
        # 只读监听外部文件：不替用户在任意路径创建目录
        self._mkdir_on_start = False

    def start(self) -> bool:
        ok = super().start()
        if ok:
            log.info("Agent 监视器 [%s] 已启动 (%s)", self.agent_key, self.events_file)
        return ok


def other_instances_use_agent(config, agent_key: str) -> bool:
    """<base> 下其他变体 / 多开实例是否仍开启该 Agent 联动（关闭/卸载时保守保留）。

    语义取两份历史实现的**并集**：既扫当前变体目录的 config.json +
    config-*.json，也扫 <base> 下全部变体的 dsh-pet-standalone*/config*.json
    （含当前变体）——任一实现认为在用的文件命中也视为在用。hooks/桥接插件
    是全局状态，防卸载/关闭时误删仍被其他实例使用的内容。当前实例自身配置
    始终排除；解析失败的文件按「不在用」跳过（与两份历史实现一致）。
    """
    config_dir = Path(config.dir)
    seen: set[Path] = set()
    candidates: list[Path] = []
    try:
        candidates.append(config_dir / "config.json")
        candidates.extend(config_dir.glob("config-*.json"))
        candidates.extend(config_dir.parent.glob("dsh-pet-standalone*/config*.json"))
    except OSError:
        pass
    for f in candidates:
        if f in seen:
            continue
        seen.add(f)
        if config.path and f == config.path:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and bool((data.get("agent_link") or {}).get(agent_key, False)):
            return True
    return False


# ----------------------------------------------------------------------
# Agent 联动总调度管理器
# ----------------------------------------------------------------------

class AgentLinkManager(QObject):
    """多 Agent 联动总调度管理器。

    批6-5 拆分后本类只保留装配与编排：
    - 装配：4 内置 + 配置驱动的自定义监视器、AgentLinkReducer（纯状态机）、
      AgentLinkPresentation（气泡/音效/动画调度），并完成信号接线；
    - 监视器生命周期：pause / resume / shutdown / apply_config；
    - set_enabled 安装/卸载编排（授权弹窗、后台安装、hooks 注入/移除）；
    - 对既有调用面（PetWindow / PetApp / ProactiveScreenWatcher / 测试）的
      薄转发。状态机与呈现逻辑分别位于 agent_link_reducer / agent_link_presentation。
    """

    install_finished = Signal(str, bool, str, int)  # (agent_key, ok, message, install_token)

    # 联动气泡展示名
    AGENT_NAMES = {"dsh": "DSH", "claude": "Claude Code", "cursor": "Cursor", "opencode": "OpenCode"}
    # 默认 thinking 文案单一来源在 Presentation；此处再导出供设置页按类访问
    _THINKING_DEFAULTS = AgentLinkPresentation._THINKING_DEFAULTS

    def __init__(self, window: Any, config: Any, *, min_interval: float = 2.0,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(window if hasattr(window, "winId") else None)
        self.win = window
        self.cfg = config
        self.config_dir = config.dir

        # 安装生命周期守卫（全审 P1-4）：_shutdown = 管理器已关闭（窗口
        # close / 角色切换 / aboutToQuit）；_install_pending = 在途 dsh
        # 安装代次（set_enabled 发起时登记，disable/shutdown 作废）。后台
        # 安装线程完成回调若在关闭/重新禁用之后才到达，不得再写配置、
        # 启动监视器或对旧窗口弹气泡。二者只在本线程（GUI）读写。
        self._shutdown = False
        self._install_token = 0
        self._install_pending: dict[str, int] = {}

        self.monitors: dict[str, BaseAgentMonitor] = {
            "dsh": DshMonitor("dsh", self.config_dir, self),
            "claude": ClaudeCodeMonitor("claude", self.config_dir, self),
            "cursor": CursorMonitor(self.config_dir, self),
            "opencode": OpenCodeMonitor(self.config_dir, self),
        }
        # 自定义联动 Agent：配置驱动的只读监视器（key/path 已在 config 清洗时
        # 保证合法唯一）；显示名合并进实例级 agent_names，类级 AGENT_NAMES
        # 保持仅内置（modern_settings_dialog 等按内置枚举处不受影响）。
        # 注意：运行中新增/修改 custom_agents 需重启桌宠生效。
        self.agent_names: dict[str, str] = dict(self.AGENT_NAMES)
        for item in (self.cfg.get("agent_link", {}).get("custom_agents") or []):
            key = str(item.get("key") or "")
            if not key or key in self.monitors:
                continue
            self.monitors[key] = CustomAgentMonitor(
                key, self.config_dir, str(item.get("path") or ""), self,
            )
            self.agent_names[key] = str(item.get("name") or key)

        # 状态归约器（纯状态机）+ 呈现层（气泡/音效/动画调度）
        self.reducer = AgentLinkReducer(
            config, self._emit_gen_of, self._monitor_running, self._window_visible,
            self.agent_names, min_interval=min_interval, clock=clock, parent=self,
        )
        self.presentation = AgentLinkPresentation(
            window, config, self.agent_names, clock=clock, parent=self,
        )
        # 效果信号：Reducer → Presentation（同线程直连，保持 emit→dispatch 语义）
        self.reducer.activity.connect(self.presentation.on_activity)
        self.reducer.sound_event.connect(self.presentation.on_sound_event)
        self.reducer.state_applied.connect(self.presentation.on_state_applied)
        self.reducer.done_bubble.connect(self.presentation.on_done_bubble)

        for mon in self.monitors.values():
            mon.state_changed.connect(self._on_agent_state_event)
            mon.activity.connect(self._on_agent_activity_event)
        self.install_finished.connect(self._on_install_finished)
        # 联动动作链：一次性动作播完后若仍有 Agent 在忙，由 window 回调取下一个动作
        if hasattr(self.win, "set_link_next_provider"):
            self.win.set_link_next_provider(self._next_busy_anim)

        self.apply_config()

    # ---- Reducer 依赖注入（状态机不直接触碰监视器/窗口） ----

    def _emit_gen_of(self, agent_key: str) -> int | None:
        mon = self.monitors.get(agent_key)
        return None if mon is None else mon._emit_gen

    def _monitor_running(self, agent_key: str) -> bool | None:
        mon = self.monitors.get(agent_key)
        return None if mon is None else bool(getattr(mon, "_running", True))

    def _window_visible(self) -> bool:
        return hasattr(self.win, "isVisible") and bool(self.win.isVisible())

    def apply_config(self) -> None:
        """根据配置启停各个 Agent 监视器。

        注意用 _running（生命周期状态）而非 is_running()（会被 pause 置 False）——
        否则"隐藏期间关配置"不会真正 stop，恢复显示时又会被 resume 拉起。"""
        agent_cfg = self.cfg.get("agent_link", {})
        for key, monitor in self.monitors.items():
            should_run = bool(agent_cfg.get(key, False))
            if should_run and not monitor._running:
                monitor.start()
            elif not should_run and monitor._running:
                monitor.stop()

    def any_busy(self) -> bool:
        """任一已启用 Agent 正处于 busy（working/thinking）状态。（转发 Reducer）

        供闲置降帧等"Agent 在干活 = 桌宠活跃"的判定使用：dsh 干活时桌宠
        视为活跃、不降帧。已停用监视器的残留状态不计入（关掉联动 = 不再
        被视为活跃，否则降帧开关会被僵尸 busy 永久顶掉）。
        """
        return self.reducer.any_busy()

    def _install_dsh_worker(self, token: int) -> None:
        """后台线程：安装 DSH 桥接插件，完成后信号回主线程。

        代次守卫（全审 P1-4 + B9 复审 P1）：安装期间 manager 被 shutdown/
        联动被重新禁用/又发起了新安装时，token 与当前代次不符 → 迟到结果
        直接丢弃，不 emit（避免对已销毁 C++ 对象 emit 的 RuntimeError 噪音）。
        完成信号携带本线程捕获的 token（B9 复审 P1）：emit→dispatch 窗口里
        即使 token 检查通过后才被 disable→re-enable，旧 queued 回调也会因
        载荷 token 与当前登记代次不符而在 GUI 槽被丢弃——接收端代次校验
        才是权威（发送端标志挡不住 emit→dispatch 竞态）。
        """
        ok, msg = DshMonitor.install_bridge()
        if token != self._install_token:
            log.info("DSH 桥接安装结果已过期，丢弃（manager 已关闭或安装已取消）")
            return
        try:
            self.install_finished.emit("dsh", ok, msg, token)
        except RuntimeError:
            # manager C++ 已随窗口销毁：信号无处投递，静默丢弃（daemon
            # 线程不能把未捕获异常打印成噪音）
            log.debug("DSH 桥接安装完成但 manager 已销毁，丢弃结果")

    def _warn_if_agent_absent(self, agent_key: str) -> None:
        """开启了联动但本机没装对应 Agent 时给用户提示（不然勾了永远没反应）。"""
        # 自定义 Agent：事件文件尚未出现时提示路径，避免"勾了没反应"的困惑
        mon = self.monitors.get(agent_key)
        if isinstance(mon, CustomAgentMonitor):
            if mon.events_file.exists() or not hasattr(self.win, "show_bubble"):
                return
            self.win.show_bubble(
                f"已开启 {self.agent_names.get(agent_key, agent_key)} 联动监听，"
                f"但事件文件还没出现——{mon.events_file} 有事件我才能感知到哦",
                duration_ms=6000,
            )
            return
        hints = {
            "cursor": ("Cursor", Path.home() / ".cursor" / "projects"),
            "opencode": ("OpenCode", Path.home() / ".local" / "share" / "opencode" / "opencode.db"),
        }
        item = hints.get(agent_key)
        if not item:
            return
        name, marker = item
        if not marker.exists() and hasattr(self.win, "show_bubble"):
            self.win.show_bubble(
                f"已开启 {name} 联动监听，但没检测到本机安装 {name}——装了它我才能感知到哦",
                duration_ms=6000,
            )

    def _on_install_finished(self, agent_key: str, ok: bool, msg: str, token: int) -> None:
        """安装完成：成功则正式开启联动，失败则提示。

        生命周期守卫（全审 P1-4 + B9 复审 P1）：窗口关闭/角色切换（shutdown）
        或用户重新禁用（set_enabled(..., False) 作废在途安装）后，迟到的完成
        回调不得再写配置 / apply_config 启动监视器 / 对旧窗口弹气泡。
        信号是 worker 线程 → GUI 线程的 queued 投递，本方法在 GUI 线程
        执行，_shutdown / _install_pending 无跨线程竞态。

        代次校验（B9 复审 P1）：信号载荷携带安装 token，必须与当前登记
        代次一致才消费并 pop——disable→re-enable 后，旧安装的 queued 回调
        不得消费新安装的 pending（仅凭 agent key 无法区分两代安装）。
        """
        if self._shutdown:
            log.info("安装完成回调被丢弃（manager 已关闭）: %s", agent_key)
            return
        if self._install_pending.get(agent_key) != token:
            log.info("安装完成回调被丢弃（安装已取消或代次过期）: %s", agent_key)
            return
        self._install_pending.pop(agent_key, None)
        if ok:
            ag_cfg = dict(self.cfg.get("agent_link", {}))
            ag_cfg[agent_key] = True
            self.cfg.set("agent_link", ag_cfg)
            self.cfg.save()
            self.apply_config()
            if hasattr(self.win, "show_bubble"):
                self.win.show_bubble("DSH 桥接插件已装好，联动开启～", duration_ms=4000)
        else:
            log.warning("DSH 桥接插件安装失败: %s", msg)
            if hasattr(self.win, "show_bubble"):
                self.win.show_bubble(f"DSH 桥接插件安装失败：{msg}", duration_ms=6000)

    def set_enabled(self, agent_key: str, enabled: bool) -> bool:
        """开启或关闭指定 Agent 监视器（必要时弹出确认框）。

        返回 False 表示未生效（用户拒绝授权 / hooks 安装失败），调用方应回滚 UI 勾选态。"""
        if agent_key not in self.monitors:
            return False

        if enabled:
            # 针对需要注入 hooks 的 Agent 弹窗征求用户同意
            if agent_key == "claude":
                res = QMessageBox.question(
                    self.win if hasattr(self.win, "winId") else None,
                    "开启 Claude Code 联动",
                    "开启联动需要在 ~/.claude/settings.json 中配置事件 hooks，\n"
                    "用于在 Agent 干活时同步通知桌宠播放对应动作。\n\n"
                    "是否允许注入 hooks 配置？（关闭联动时会自动移除）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if res != QMessageBox.StandardButton.Yes:
                    return False
                if not ClaudeCodeMonitor.install_hooks(self.monitors["claude"].events_file):
                    QMessageBox.warning(
                        self.win if hasattr(self.win, "winId") else None,
                        "开启 Claude Code 联动",
                        "hooks 配置写入失败，联动未开启。\n可查看日志了解详情。",
                    )
                    return False
            elif agent_key == "dsh":
                res = QMessageBox.question(
                    self.win if hasattr(self.win, "winId") else None,
                    "开启 DSH 联动",
                    "开启联动需要向 DeepSeek Harness 安装一个桥接小插件\n"
                    "（把 DSH 的运行状态写到本地文件给桌宠读，仅本地、无网络）。\n\n"
                    "是否允许一键安装？（关闭联动时会自动卸载）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if res != QMessageBox.StandardButton.Yes:
                    return False
                # 安装走后台线程（pnpm 解析可能数十秒，绝不在 UI 线程阻塞）；
                # 菜单先回弹，安装完成后自动开启并气泡告知。
                # 登记安装代次（全审 P1-4）：完成回调据此判断安装是否仍有效。
                self._install_token += 1
                token = self._install_token
                self._install_pending["dsh"] = token
                if hasattr(self.win, "show_bubble"):
                    self.win.show_bubble("正在安装 DSH 桥接插件…", duration_ms=4000)
                import threading
                threading.Thread(
                    target=self._install_dsh_worker, args=(token,), daemon=True,
                    name="dsh-bridge-install",
                ).start()
                return False
        else:
            # 关闭联动：作废在途的 dsh 安装（全审 P1-4）——完成回调将因
            # 代次过期被丢弃，不得在用户关闭后反向把配置写回 True。
            if agent_key == "dsh":
                self._install_pending.pop("dsh", None)
                self._install_token += 1
            # 关闭联动时移除我们注入的内容（只删自己的，用户自有配置不碰）；
            # 其他多开实例仍在使用则保留（hooks/插件是全局状态）
            if agent_key in ("claude", "dsh") and other_instances_use_agent(self.cfg, agent_key):
                log.info("其他实例仍在使用 %s 联动，保留注入内容", agent_key)
            elif agent_key == "claude":
                if not ClaudeCodeMonitor.uninstall_hooks():
                    log.warning("Claude hooks 卸载未完全成功（配置已关闭，hooks 可能残留）")
                    if hasattr(self.win, "show_bubble"):
                        self.win.show_bubble("Claude hooks 卸载未完全成功，可手动检查 ~/.claude/settings.json", duration_ms=6000)
            elif agent_key == "dsh":
                if not DshMonitor.uninstall_bridge():
                    log.warning("DSH 桥接插件卸载未完全成功（配置已关闭，插件可能残留）")
                    if hasattr(self.win, "show_bubble"):
                        self.win.show_bubble("DSH 桥接插件卸载未完全成功", duration_ms=6000)

        ag_cfg = dict(self.cfg.get("agent_link", {}))
        ag_cfg[agent_key] = bool(enabled)
        self.cfg.set("agent_link", ag_cfg)
        self.cfg.save()
        self.apply_config()
        if enabled:
            self._warn_if_agent_absent(agent_key)
        return True

    def pause(self) -> None:
        """桌宠隐藏时暂停所有监视器，丢弃待播联动动作，并取消所有完成确认计时器
        （否则隐藏期间计时器到期会在隐藏窗口上切动画/弹气泡）。"""
        for mon in self.monitors.values():
            mon.pause()
        self.presentation.pause()
        self.reducer.pause()

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()

    def shutdown(self) -> None:
        """窗口销毁/角色切换：先广播停止（不逐个阻塞），再按共享截止 join。

        防止 worker 线程经「线程→monitor→manager→旧窗口」引用链把旧窗口
        对象保活（B9 一审：角色切换后旧窗口的 monitor 继续轮询）。
        没有存活 worker 时零开销直接返回（不调 time.monotonic——测试里
        它可能被 monkeypatch 成有限迭代器，多调一次就是 StopIteration）。

        同时进入关闭态（全审 P1-4）：作废在途 dsh 安装，迟到的安装完成
        回调（install_finished）一律丢弃，不再写配置/启动监视器/弹气泡。
        """
        self._shutdown = True
        self._install_pending.clear()
        self._install_token += 1
        for mon in self.monitors.values():
            mon.begin_stop()
        active = [
            mon for mon in self.monitors.values()
            if mon._worker is not None and mon._worker.is_alive()
        ]
        if not active:
            return
        deadline = time.monotonic() + BaseAgentMonitor._STOP_JOIN_TIMEOUT_S
        for mon in active:
            mon.finish_stop(deadline)

    # ---- 信号槽（监视器 worker → 本管理器，Queued 语义保持） ----

    def _on_agent_state_event(self, event: AgentEvent) -> None:
        """state_changed 信号槽：AgentEvent 载荷 → 既有处理入口（代次校验在内）。"""
        self._on_agent_state(event.agent, event.state, event.gen)

    def _on_agent_activity_event(self, event: AgentEvent) -> None:
        """activity 信号槽：AgentEvent 载荷 → 既有处理入口（代次校验在内）。"""
        self._on_agent_activity(event.agent, event.tool, event.gen)

    def _on_agent_state(self, agent_key: str, state: str, gen: int = 0) -> None:
        """接收 Agent 状态变更（测试兼容入口；状态机逻辑在 Reducer）。"""
        self.reducer.on_state(agent_key, state, gen)

    def _on_agent_activity(self, agent_key: str, tool: str, gen: int = 0) -> None:
        """接收 Agent 工具过程汇报（测试兼容入口；代次校验在 Reducer，气泡在 Presentation）。"""
        if not self.reducer.gen_current(agent_key, gen):
            return  # 旧代次 worker 的迟到信号
        self.presentation.on_tool_activity(agent_key, tool)

    def _fire_done(self, agent_key: str) -> None:
        """800ms 稳定确认到期（测试兼容入口；逻辑在 Reducer）。"""
        self.reducer.fire_done(agent_key)

    def _show_link_bubble(self, text: str, *, important: bool, duration_ms: int = 4500,
                          _retried: int = 0) -> None:
        """联动气泡（测试兼容入口；逻辑在 Presentation）。"""
        self.presentation.show_link_bubble(
            text, important=important, duration_ms=duration_ms, _retried=_retried)

    def _next_link_anim_rotation(self) -> str | None:
        """下一个联动动作（测试兼容入口；轮换逻辑在 Presentation）。"""
        return self.presentation.next_link_anim_rotation()

    def _next_busy_anim(self) -> str | None:
        """window 动画结束回调用：仍有 Agent 在忙 → 下一个联动动作；否则 None。
        全员空闲时重置轮换计数——下一个任务从「写代码」重新开始。"""
        return self.presentation.next_busy_anim(self.reducer.has_any_busy_raw)

    def busy_agent_owns_process(self, process_name: str, title: str = "") -> bool:
        """前台窗口是否属于「联动开启且正在忙」的 Agent（转发 Reducer）。"""
        return self.reducer.busy_agent_owns_process(process_name, title)

    # ---- 测试兼容的状态字段视图（指向 Reducer 真实对象，逐字段等价） ----

    @property
    def _last_raw(self) -> dict[str, str]:
        return self.reducer._last_raw

    @_last_raw.setter
    def _last_raw(self, value: dict[str, str]) -> None:
        self.reducer._last_raw = value

    @property
    def _last_applied(self) -> dict[str, tuple[str, float]]:
        return self.reducer._last_applied

    @property
    def _done_pending(self) -> dict[str, Any]:
        return self.reducer._done_pending
