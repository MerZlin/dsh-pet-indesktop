# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动监视器模块（DSH / Claude Code / Cursor / OpenCode）。

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
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWidgets import QMessageBox

from .persona_phrases import PhrasePicker

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
    pnpm = shutil.which("pnpm")
    if pnpm:
        for base in (Path(pnpm).parent, Path(pnpm).resolve().parent):
            cand = base / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
            if cand.is_file():
                return str(cand)
    npm = shutil.which("npm")
    if npm:
        cand = Path(npm).parent / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
        if cand.is_file():
            return str(cand)
    return None


def _npm_cli() -> str | None:
    """定位 npm 的 JS CLI 入口（由 node 直调，绕开 .cmd 的空格引号坑）。"""
    npm = shutil.which("npm")
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
    node = shutil.which("node")
    npm_cli = _npm_cli()
    if not node or not npm_cli:
        return None
    try:
        proc = subprocess.run(
            [node, npm_cli, "install", "-g", "pnpm"],
            capture_output=True, text=True, timeout=300, shell=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return _find_pnpm_cli()


def _run_pnpm(profile_dir: Path, *args: str) -> tuple[int, str]:
    """node 直调 pnpm CLI（数组传参，无 cmd 中转），返回 (返回码, 合并输出)。"""
    node = shutil.which("node")
    cli = _pnpm_cli()
    if not node:
        return 127, "找不到 node，请先安装 Node.js"
    if not cli:
        return 127, "需要 pnpm，自动安装失败，请手动运行: npm install -g pnpm"
    try:
        proc = subprocess.run(
            [node, cli, *args], capture_output=True, text=True,
            timeout=300, shell=False, cwd=str(profile_dir),
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
        pt = str((data.get("part") or {}).get("type", ""))
        return {"step-start": "working", "reasoning": "thinking", "step-finish": "idle"}.get(pt, "")
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


class DirGlobTailer:
    """目录下按 glob 匹配多个 jsonl 文件的增量 tail（新文件发现 + 淘汰）。

    用于多 DSH 实例各自写入 dsh-{pid}.jsonl 的场景（P0-2 多实例分区写入）。
    每个实例一个文件，桌宠侧读全部 dsh*.jsonl（含旧版单实例的 dsh.jsonl），
    避免 Windows 上多进程并行写同一文件的行交织。

    兼容旧版单文件语义：保留 ``_initial_backfill_done`` 属性（读写代理到所有
    子 tailer），供测试强制关闭 backfill 直接读取已有内容。
    """

    def __init__(self, directory: Path | str, pattern: str = "dsh*.jsonl",
                 scan_interval: float = 5.0, max_files: int = 64) -> None:
        self.directory = Path(directory)
        self.pattern = pattern
        self.scan_interval = scan_interval
        self.max_files = max_files
        self._last_scan = 0.0
        self._tailers: dict[str, ByteOffsetTailer] = {}
        self._initial_backfill_done: bool = False

    @property
    def _initial_backfill_done(self) -> bool:
        """兼容旧测试：返回子 tailer 的 backfill 状态（任一子 tailer 未完成即 False）。"""
        for t in self._tailers.values():
            if not t._initial_backfill_done:
                return False
        return True

    @_initial_backfill_done.setter
    def _initial_backfill_done(self, value: bool) -> None:
        # 新创建的 tailer 也继承此值
        self._cached_backfill_value = value
        for t in self._tailers.values():
            t._initial_backfill_done = value

    def reset(self) -> None:
        self._last_scan = 0.0
        for t in self._tailers.values():
            t.reset()
        self._tailers.clear()

    def _scan(self, now: float) -> None:
        if now - self._last_scan < self.scan_interval:
            return
        self._last_scan = now
        try:
            if not self.directory.is_dir():
                return
            files = sorted(self.directory.glob(self.pattern))
            files = files[: self.max_files]
            candidates = {str(f) for f in files}
            for stale in [k for k in self._tailers if k not in candidates]:
                del self._tailers[stale]
            for fkey in candidates:
                if fkey not in self._tailers:
                    t = ByteOffsetTailer(fkey)
                    t._initial_backfill_done = getattr(self, "_cached_backfill_value", False)
                    self._tailers[fkey] = t
        except Exception:
            log.debug("桥目录扫描异常", exc_info=True)

    def read_new_lines(self) -> list[str]:
        now = time.time()
        self._scan(now)
        lines: list[str] = []
        for tailer in self._tailers.values():
            lines.extend(tailer.read_new_lines())
        return lines


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


class BaseAgentMonitor(QObject):
    """Agent 监视器抽象基类。"""

    state_changed = Signal(str, str)  # (agent_key, state)
    activity = Signal(str, str)       # (agent_key, 工具名) —— 过程汇报用，仅事件带工具名时发
    approval_requested = Signal(str, object)  # (agent_key, payload) —— 审批请求（含 rpcId 时可交互）
    approval_resolved = Signal(str, object)   # (agent_key, payload) —— 审批已结束，气泡应消失
    question_requested = Signal(str, object)  # (agent_key, payload) —— ask_user_question 阻塞交互
    question_resolved = Signal(str, object)   # (agent_key, payload) —— 问题已解决，气泡应消失
    # 原始桥接记录转发（供 stuck_detector 等消费）：(agent_key, record)
    # 只挂 DSH 监视器；其他 Agent（claude/cursor/…）不产生这类增强记录。
    raw_record = Signal(str, object)
    # 硬失败（execution/failed）：DSH 已决定本轮不再继续，不经行为分析直接提醒
    execution_failed = Signal(str, object)   # (agent_key, payload)
    # 会话元数据更新（session/meta 事件）：(agent_key, record)
    session_meta = Signal(str, object)
    # 限流提醒（rate_limit 事件，errorCode=429）：(agent_key, record)
    rate_limit = Signal(str, object)

    def __init__(self, agent_key: str, config_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.agent_key = agent_key
        self.config_dir = Path(config_dir)
        self.events_dir = self.config_dir / "agent-events"
        self.events_file = self.events_dir / f"{agent_key}.jsonl"
        self._running = False
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._poll)
        self._tailer = ByteOffsetTailer(self.events_file)

    def is_running(self) -> bool:
        return self._running and not self._paused

    def start(self) -> None:
        self._running = True
        self._paused = False
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._tailer.reset()
        if not self._timer.isActive():
            self._timer.start()
        log.info("Agent 监视器 [%s] 已启动", self.agent_key)

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._timer.stop()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    def pause(self) -> None:
        if self._running:
            self._paused = True
            self._timer.stop()

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            self._timer.start()

    def _poll(self) -> None:
        lines = self._tailer.read_new_lines()
        for line in lines:
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                ev = str(data.get("event", ""))
                st = str(data.get("state", ""))
                tool = str(data.get("tool", "") or "").strip()
                # 原始记录转发（只挂 DSH 监视器；stuck_detector 消费成败/错误/文本）
                self.raw_record.emit(self.agent_key, data)
                # 审批请求提醒：一次性事件，收到即发信号（不进入状态机，
                # 因为 DSH 等审批时 agent 仍在 running，状态还是 working）。
                # payload 带 rpcId/sessionId/approvalId 时可交互（气泡内直接点选回写）。
                if ev == "approval/request":
                    self.approval_requested.emit(self.agent_key, data)
                # 审批已结束（approval/decided 会话事件 或 approval/resolved mux 帧）：
                # 审批气泡应消失。
                if ev in ("approval/decided", "approval/resolved"):
                    self.approval_resolved.emit(self.agent_key, data)
                # 用户问题（ask_user_question 阻塞交互）：与审批同等待遇，
                # question/requested 常驻气泡、question/resolved 收尾；payload 带 rpcId
                # 时可交互（气泡内选项直接点选回写）。
                if ev == "question/requested":
                    self.question_requested.emit(self.agent_key, data)
                if ev == "question/resolved":
                    self.question_resolved.emit(self.agent_key, data)
                # 硬失败（execution/failed）：DSH 已决定本轮不再继续，不经行为分析直接提醒
                if ev == "execution/failed":
                    self.execution_failed.emit(self.agent_key, data)
                if tool:
                    self.activity.emit(self.agent_key, tool)
                # 会话元数据：session/meta 事件 → 信号转发给 Manager 缓存
                meta_type = str(data.get("type", ""))
                if meta_type == "session/meta":
                    self.session_meta.emit(self.agent_key, data)
                # 调试输出：debug/session-shape（仅首次，之后可通过配置关闭）
                if meta_type == "debug/session-shape":
                    log.info("[dsh-pet-bridge] session shape: %s", json.dumps(data, ensure_ascii=False)[:500])
                # 限流事件：rate_limit（errorCode=429）→ 信号转发给 Manager 显示提醒
                if ev == "rate_limit":
                    self.rate_limit.emit(self.agent_key, data)
                normalized = normalize_event_state(ev, st)
                if not normalized:
                    continue  # 不认识的事件类型：忽略，不误报为 working
                self.state_changed.emit(self.agent_key, normalized)
            except Exception:
                pass


# ----------------------------------------------------------------------
# Codex rollout 记录解析（~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl）
# ----------------------------------------------------------------------
# rollout JSONL 是 Codex CLI / 桌面端每个 thread 一个文件的会话落盘日志
# （文件名末段即 thread id），是 Codex 状态的唯一可靠实时事件源：
#   {"timestamp": "...", "ordinal": N, "type": "session_meta|turn_context|
#    response_item|event_msg|world_state", "payload": {...}}
# - event_msg.payload.type:
#     task_started / task_complete / turn_aborted   -- turn 生命周期
#     item_completed（含 thread_id/turn_id/item）    -- 工作单元完成
# - response_item.payload.type:
#     function_call / custom_tool_call（call_id）     -- 工具调用开始标记
# - turn_context.payload：turn_id + cwd（每个 turn 的工作目录）
# 辅助：~/.codex/sqlite/codex-dev.db 的 local_thread_catalog（只读连接）
# 提供 thread 元数据（cwd/标题/最近更新时间），用于启动恢复与项目过滤。
# 审批/用户交互是 app-server JSON-RPC 通道（第三阶段），不从 rollout 猜测
# approval 事件名。

# rollout item.type → 过程汇报工具名（manager TOOL_LABELS 的键）
_CODEX_ITEM_TOOLS = {
    "CommandExecution": "bash",
    "FileChange": "edit",
    "WebSearch": "websearch",
}
# response_item 工具调用 name → 汇报工具名别名
_CODEX_CALL_TOOL_ALIASES = {
    "exec": "bash",
    "exec_command": "bash",
    "local_shell": "bash",
    "shell": "bash",
}
# turn_aborted reason → 用户主动中断（不按失败处理）
_CODEX_INTERRUPT_REASONS = {"interrupted", "cancelled", "canceled", "user_interrupt"}

_CODEX_ROLLOUT_NAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<tid>[0-9A-Za-z-]{8,64})\.jsonl$"
)


def codex_rollout_thread_id_from_name(name: str) -> str:
    """从 rollout 文件名提取 thread id（文件名末段）；不匹配返回 ""。"""
    m = _CODEX_ROLLOUT_NAME_RE.match(str(name or ""))
    return m.group("tid") if m else ""


def _parse_iso8601_utc(value) -> float:
    """ISO8601（含 Z 后缀）→ epoch 秒；解析失败返回 0。"""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def parse_codex_rollout_line(line: str) -> dict | None:
    """解析一行 rollout JSONL → 标准化事件 dict；无法解析返回 None。

    标准化字段（缺失为 ""/0）：
      kind     meta / turn_context / turn_start / turn_end / turn_abort /
               item_start / item_end / heartbeat
      turn_id / item_id / item_type / tool / status / cwd / reason / ts
    未知记录类型一律归为 heartbeat（只用于线程活跃心跳，不驱动状态）。
    """
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(rec, dict):
        return None
    rtype = str(rec.get("type") or "")
    payload = rec.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    ts = _parse_iso8601_utc(rec.get("timestamp"))
    ev: dict = {"kind": "heartbeat", "turn_id": "", "item_id": "",
                "item_type": "", "tool": "", "status": "", "cwd": "",
                "reason": "", "ts": ts}

    if rtype == "session_meta":
        ev["kind"] = "meta"
        ev["turn_id"] = str(payload.get("session_id") or payload.get("id") or "")
        ev["cwd"] = str(payload.get("cwd") or "")
        return ev
    if rtype == "turn_context":
        ev["kind"] = "turn_context"
        ev["turn_id"] = str(payload.get("turn_id") or "")
        ev["cwd"] = str(payload.get("cwd") or "")
        return ev

    if rtype == "response_item":
        ptype = str(payload.get("type") or "")
        if ptype in ("function_call", "custom_tool_call"):
            ev["kind"] = "item_start"
            ev["item_id"] = str(payload.get("call_id") or payload.get("id") or "")
            name = str(payload.get("name") or "")
            ev["tool"] = _CODEX_CALL_TOOL_ALIASES.get(name, name)
            ev["item_type"] = "CommandExecution"
            return ev
        return ev  # message/reasoning/输出等：心跳

    if rtype == "event_msg":
        ptype = str(payload.get("type") or "")
        if ptype == "task_started":
            ev["kind"] = "turn_start"
            ev["turn_id"] = str(payload.get("turn_id") or "")
            return ev
        if ptype == "task_complete":
            ev["kind"] = "turn_end"
            ev["turn_id"] = str(payload.get("turn_id") or "")
            return ev
        if ptype == "turn_aborted":
            ev["kind"] = "turn_abort"
            ev["turn_id"] = str(payload.get("turn_id") or "")
            ev["reason"] = str(payload.get("reason") or "")
            return ev
        if ptype in ("item_started", "item_completed"):
            item = payload.get("item")
            item = item if isinstance(item, dict) else {}
            itype = str(item.get("type") or "")
            ev["kind"] = "item_start" if ptype == "item_started" else "item_end"
            ev["turn_id"] = str(payload.get("turn_id") or "")
            ev["item_id"] = str(item.get("id") or "")
            ev["item_type"] = itype
            ev["status"] = str(item.get("status") or "")
            if itype in _CODEX_ITEM_TOOLS:
                ev["tool"] = _CODEX_ITEM_TOOLS[itype]
            elif itype in ("McpToolCall", "DynamicToolCall"):
                ev["tool"] = str(item.get("tool") or "")
            return ev
        return ev  # token_count/agent_message/…：心跳

    return ev  # world_state 等：心跳


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
        # 多 DSH 实例分区写入（P0-2）：生产端每个实例写 dsh-{pid}.jsonl，
        # 消费端 glob 全部 dsh*.jsonl（兼容旧版单文件 dsh.jsonl）。
        # events_file 保留为旧字段名（兼容既有调用/测试），实际读取走 DirGlobTailer。
        self.events_file = self.events_dir / "dsh.jsonl"
        self._tailer = DirGlobTailer(self.events_dir, pattern="dsh*.jsonl")

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
        if shutil.which("node") is None:
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
                # 已安装也要刷新本地 link。否则源码/打包版升级后，profile
                # 仍可能指向旧的 dist-onedir bridge，重启 dsh 只会继续加载旧代码。
                # pnpm add 会更新已有的 link spec；失败时保留原安装并报告。
                rc, out = _run_pnpm(profile, "add", str(plugin))
                if rc != 0:
                    failed.append(f"{profile.name}: pnpm refresh 失败 {(out or '')[-150:]}")
                    continue
                pkg = _read_manifest(profile)
                if pkg is None:
                    failed.append(f"{profile.name}: 刷新后 package.json 读取失败")
                    continue
                # 刷新后继续补 bundles（可能此前通过别的途径装过）
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
        if shutil.which("node") is None or _pnpm_cli() is None:
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

    def _poll(self) -> None:
        # 首先检查统一 jsonl
        super()._poll()

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
                        self.activity.emit("cursor", tool)
                    norm = cursor_line_state(data)
                    if not norm:
                        continue  # 未知 transcript 行类型：忽略
                    self.state_changed.emit("cursor", norm)
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
        self._last_rowid: int = 0
        self._db_ready: bool = False

    def start(self) -> None:
        self._db_ready = False
        super().start()

    def _poll(self) -> None:
        # 统一 jsonl 通道（兼容未来插件/手动注入）
        super()._poll()

        if not self.db_path.is_file():
            return
        import sqlite3

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
                self.state_changed.emit("opencode", state)
            tool = opencode_event_tool(ev_type, data_raw)
            if tool:
                self.activity.emit("opencode", tool)


# ----------------------------------------------------------------------
# Codex（ChatGPT App 内融合）app-server 事件监听器
# ----------------------------------------------------------------------

class CodexMonitor(QObject):
    """ChatGPT App 内新版 Codex（app-server）事件监听器。

    数据源：Codex app-server 的本地 WebSocket 事件流（JSON-RPC 风格
    ServerNotification）。app-server 是 Codex 融合进 ChatGPT App 后的本机
    服务，通过 healthz 探测端口，WebSocket 订阅 thread/item/approval 事件。

    事件 → 统一状态（词汇与 BaseAgentMonitor 一致）：
      - thread/started / turn/started → thinking
      - item/started → working（可带 tool 名用于过程汇报）
      - item/completed / thread/completed / turn/completed → idle
      - item/error / error / interrupted → error / idle
      - approval/requested / CommandApprovalRequested / PermissionsApprovalRequested
        → 发 approval_requested 信号（常驻气泡）
      - RequestUserInput / question/requested → 发 question_requested 信号
      - approval/resolved / question/resolved → 发 resolved 信号

    连接细节（需真机验证）：
    - 端口：通过环境变量 CODEX_APP_SERVER_PORT / CODEX_APP_SERVER_WS_URL 配置，
      或扫描候选端口（默认 4317, 3456）的 healthz 端点自动发现。
    - WebSocket 路径：/events（官方 app-server 文档约定，可降级）。
    - 鉴权：无（本机回环，与 DSH 的 /api/respond 同模式）。
    """

    state_changed = Signal(str, str)           # (agent_key, state)
    activity = Signal(str, str)                 # (agent_key, tool_name)
    approval_requested = Signal(str, object)    # (agent_key, payload)
    approval_resolved = Signal(str, object)     # (agent_key, payload)
    question_requested = Signal(str, object)    # (agent_key, payload)
    question_resolved = Signal(str, object)     # (agent_key, payload)
    # 硬失败（execution/failed）：DSH 已决定本轮不再继续，不经行为分析直接提醒。
    # Codex app-server 协议暂无此事件，预留信号保证 AgentLinkManager 统一连接。
    execution_failed = Signal(str, object)      # (agent_key, payload)

    # ---- 端点发现（动态，不写死端口）----
    # Codex app-server 没有固定事件端口：ChatGPT Desktop 每次启动都动态分配
    # loopback 端口（甚至可能是 stdio/Named Pipe）。发现策略：
    #   1. 查 ChatGPT.exe 全部 PID
    #   2. 查这些 PID 的 127.0.0.1/::1 LISTEN socket（Get-NetTCPConnection）
    #   3. 对候选端口逐个试连 WebSocket（/events、/ws、/），连上即锁定
    #   4. ChatGPT PID 变化 / socket 断开 → 重新发现
    # 显式 ws_url / 环境变量始终是最高优先级，可手动指定。
    WS_PATHS = ("/events", "/ws", "/")
    # 发现轮询间隔（PID 变化 / 新端口出现时才能感知）
    _DISCOVER_MS = 15000
    # 连接超时/重连间隔
    _RECONNECT_BASE_MS = 2000
    _RECONNECT_MAX_MS = 30000

    def __init__(self, config_dir: Path, parent: QObject | None = None,
                 *, ws_url: str | None = None) -> None:
        super().__init__(parent)
        self.agent_key = "codex"
        self.config_dir = Path(config_dir)
        self._ws_url = ws_url  # 显式指定时跳过端点发现
        self._running = False
        self._paused = False
        self._ws: Any | None = None
        self._connect_attempts = 0
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._connect)
        self._discover_timer = QTimer(self)
        self._discover_timer.setInterval(self._DISCOVER_MS)
        self._discover_timer.timeout.connect(self._discover)
        # 候选端点（按优先级排序的 ws:// URL 列表）+ 当前尝试下标
        self._candidate_urls: list[str] = []
        self._candidate_idx = 0
        # 已锁定端点（PID + URL 缓存）：PID 不变且连接正常则继续用
        self._found_pid: int | None = None
        self._found_url: str | None = None

    # ---- 生命周期 ----
    def is_running(self) -> bool:
        return self._running and not self._paused and self._ws is not None

    def start(self) -> None:
        self._running = True
        self._paused = False
        self._connect_attempts = 0
        self._schedule_connect()
        self._discover_timer.start()
        log.info("Agent 监视器 [codex] 已启动")

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._reconnect_timer.stop()
        self._discover_timer.stop()
        self._close_ws()
        log.info("Agent 监视器 [codex] 已停止")

    def pause(self) -> None:
        if self._running:
            self._paused = True
            self._reconnect_timer.stop()
            self._discover_timer.stop()
            self._close_ws()

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            self._connect_attempts = 0
            self._schedule_connect()
            self._discover_timer.start()

    # ---- 连接管理 ----
    def _schedule_connect(self) -> None:
        """立即或安排在下次重连。"""
        if self._reconnect_timer.isActive():
            return
        self._connect()

    def _connect(self) -> None:
        """尝试连接 WebSocket：从候选 URL 列表逐个试连，首个连上即锁定端点。"""
        if self._paused or not self._running:
            return
        self._reconnect_timer.stop()
        url = self._resolve_ws_url()
        if not url:
            self._schedule_reconnect()
            return
        try:
            from PySide6.QtWebSockets import QWebSocket
            ws = QWebSocket(parent=self)
            ws.connected.connect(self._on_connected)
            ws.textMessageReceived.connect(self._on_text)
            ws.disconnected.connect(self._on_disconnected)
            ws.errorOccurred.connect(self._on_error)
            self._ws = ws
            ws.openUrl(QUrl(url))
            self._connect_attempts += 1
        except Exception as exc:
            log.debug("Codex WebSocket 连接失败 %s: %s", url, exc)
            self._on_connect_failed()

    def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                self._ws.connected.disconnect()
                self._ws.textMessageReceived.disconnect()
                self._ws.disconnected.disconnect()
                self._ws.errorOccurred.disconnect()
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._connect_attempts = 0

    def _schedule_reconnect(self) -> None:
        """指数退避重连。"""
        if self._paused or not self._running:
            return
        delay = min(
            self._RECONNECT_BASE_MS * (2 ** min(self._connect_attempts, 5)),
            self._RECONNECT_MAX_MS,
        )
        self._reconnect_timer.setInterval(delay)
        self._reconnect_timer.start()

    def _resolve_ws_url(self) -> str | None:
        """确定要尝试的 WebSocket URL。

        优先级：显式 ws_url > 环境变量 CODEX_APP_SERVER_WS_URL >
        环境变量 CODEX_APP_SERVER_PORT > 已锁定端点 > 候选列表下一项。
        """
        if self._ws_url:
            return self._ws_url
        env_url = os.environ.get("CODEX_APP_SERVER_WS_URL")
        if env_url:
            return env_url
        env_port = os.environ.get("CODEX_APP_SERVER_PORT")
        if env_port:
            try:
                return f"ws://127.0.0.1:{int(env_port)}/events"
            except (ValueError, TypeError):
                pass
        if self._found_url:
            return self._found_url
        if self._candidate_urls:
            if self._candidate_idx >= len(self._candidate_urls):
                self._candidate_idx = 0
            return self._candidate_urls[self._candidate_idx]
        return None  # 端点发现尚未找到

    @staticmethod
    def _chatgpt_pids() -> list[int]:
        """返回正在运行的 ChatGPT.exe PID 列表（Windows）。"""
        if os.name != "nt":
            return []
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            pids: list[int] = []
            for line in out.splitlines():
                # CSV: "ChatGPT.exe","1234","Console","1","N/A"
                parts = line.strip().split('","')
                if len(parts) >= 2:
                    try:
                        pids.append(int(parts[1].strip('"')))
                    except (ValueError, TypeError):
                        continue
            return pids
        except Exception:
            return []

    def _listen_ports_for_pids(self, pids: list[int]) -> list[int]:
        """查这些 PID 的 127.0.0.1/::1 LISTEN 端口（Windows 一次性 PowerShell）。

        不用全端口扫描：直接问 OS 这些进程正在 listen 的 loopback 端口。
        """
        if os.name != "nt" or not pids:
            return []
        try:
            import subprocess
            pid_list = ",".join(str(p) for p in pids)
            ps = (
                "$pids = @(" + pid_list + "); "
                "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                "Where-Object { $_.OwningProcess -in $pids -and "
                "  ($_.LocalAddress -eq '127.0.0.1' -or $_.LocalAddress -eq '::1') } | "
                "Select-Object -ExpandProperty LocalPort"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=5,
            ).stdout
            ports: list[int] = []
            for token in out.split():
                try:
                    p = int(token)
                    if 0 < p < 65536:
                        ports.append(p)
                except (ValueError, TypeError):
                    continue
            return sorted(set(ports))
        except Exception:
            return []

    def _discover(self) -> None:
        """动态发现 ChatGPT 的 loopback 桥接端点（PID → LISTEN 端口 → 候选 URL）。

        只在未锁定或候选为空时执行；发现后按 WS_PATHS 构造候选 URL，
        由 _connect 逐个试连。ChatGPT PID 变化时强制重新发现。"""
        if self._paused or not self._running:
            return
        if self._ws_url:
            return  # 显式端点不扫描
        pids = self._chatgpt_pids()
        if not pids:
            # ChatGPT 未运行：清空候选（下次发现重新来）
            self._candidate_urls = []
            self._found_pid = None
            self._found_url = None
            return
        # PID 变化 → 强制重新发现（新实例可能换了端口）
        if self._found_pid is not None and self._found_pid not in pids:
            log.info("ChatGPT PID 变化，重新发现 Codex 端点")
            self._found_pid = None
            self._found_url = None
            self._candidate_urls = []
        if self._found_url:
            return  # 已锁定且 PID 未变
        ports = self._listen_ports_for_pids(pids)
        if not ports:
            return
        urls: list[str] = []
        for port in ports:
            for path in self.WS_PATHS:
                urls.append(f"ws://127.0.0.1:{port}{path}")
        self._candidate_urls = urls
        self._candidate_idx = 0
        self._found_pid = pids[0]
        log.info("Codex 端点候选: %s", ", ".join(urls[:6]) + ("…" if len(urls) > 6 else ""))
        self._connect()

    # ---- 事件处理 ----
    def _on_connect_failed(self) -> None:
        """当前候选连接失败：试下一个候选 URL；全部失败则指数退避重连。"""
        if self._paused or not self._running:
            return
        if self._candidate_urls:
            self._candidate_idx += 1
            if self._candidate_idx < len(self._candidate_urls):
                # 立即试下一个候选（同一发现周期内不等待退避）
                QTimer.singleShot(150, self._connect)
                return
            self._candidate_idx = 0
        self._schedule_reconnect()

    def _on_connected(self) -> None:
        """WebSocket 已连接：锁定当前候选为已发现端点。"""
        self._connect_attempts = 0
        url = self._resolve_ws_url()
        if url and self._found_pid is not None:
            self._found_url = url
        self._discover_timer.stop()  # 连上后停止端点发现
        log.info("Codex 端点已连接: %s", url or "?")

    def _on_disconnected(self) -> None:
        """WebSocket 断开：若已锁定端点则失效，重新发现并试下一个候选。"""
        self._ws = None
        if self._running and not self._paused:
            log.debug("Codex WebSocket 断开，重新发现端点")
            self._found_url = None
            self._candidate_idx = 0
            self._discover_timer.start()  # 恢复端点发现
            self._schedule_reconnect()

    def _on_error(self, error: Any) -> None:
        """WebSocket 错误：当前候选失败，试下一个。"""
        log.debug("Codex WebSocket 错误: %s", error)
        self._on_connect_failed()

    def _on_text(self, message: str) -> None:
        """收到 WebSocket 文本消息（JSON-RPC 风格 ServerNotification）。

        消息格式：{"method": "item/started", "params": {...}}
        或：{"type": "server-request", "payload": {...}}（兼容 DSH mux 风格）
        """
        try:
            data = json.loads(message)
            if not isinstance(data, dict):
                return
        except (ValueError, TypeError):
            return

        # 兼容 DSH 的 mux server-request 风格（部分桥接插件复用）
        from_ = data.get("from")
        if from_ == "codex" or data.get("source") == "codex":
            data = data.get("data") or data
            if not isinstance(data, dict):
                return

        method = str(data.get("method") or data.get("type") or "").strip()
        params = data.get("params") or data.get("payload") or {}
        if not isinstance(params, dict):
            params = {}

        # 1) 审批/问题交互事件（发专用信号，不进状态机）
        if method in CODEX_APPROVAL_EVENTS:
            if method in ("approval/resolved",):
                self.approval_resolved.emit(self.agent_key, params)
            else:
                self.approval_requested.emit(self.agent_key, params)
            return
        if method in CODEX_QUESTION_EVENTS:
            if method in ("question/resolved",):
                self.question_resolved.emit(self.agent_key, params)
            else:
                self.question_requested.emit(self.agent_key, params)
            return

        # 2) 工具名（过程汇报用）
        tool = codex_event_tool(method, params)
        if tool:
            self.activity.emit(self.agent_key, tool)

        # 3) 统一状态
        state = codex_event_state(method)
        if not state:
            return  # 不认识的 method：忽略，绝不默认 working
        self.state_changed.emit(self.agent_key, state)


# ----------------------------------------------------------------------
# Agent 联动总调度管理器
# ----------------------------------------------------------------------

class AgentLinkManager(QObject):
    """多 Agent 联动总调度管理器。

    挂载于 PetWindow，持有 5 个 Agent 的监视器，并根据状态驱动桌宠动作与气泡。
    """

    install_finished = Signal(str, bool, str)  # (agent_key, ok, message)
    # DSH 回写结果（后台线程 emit，队列投递回主线程）：(ok, detail)
    _respond_result = Signal(bool, str)
    _exploration_control_result = Signal(str, str, bool, str)  # session, operation, ok, detail

    # 联动气泡展示名
    AGENT_NAMES = {
        "dsh": "DSH", "claude": "Claude Code", "cursor": "Cursor",
        "opencode": "OpenCode", "codex": "Codex",
    }
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
    _BUSY_STATES = ("working", "thinking")
    _DONE_CONFIRM_MS = 800   # busy→idle 稳定确认窗口（过滤 working→idle→working 抖动）
    _DONE_COOLDOWN_S = 5.0   # 同 Agent 完成气泡最小间隔（最后一道保险）

    def __init__(self, window: Any, config: Any, *, min_interval: float = 2.0,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(window if hasattr(window, "winId") else None)
        self.win = window
        self.cfg = config
        self.config_dir = config.dir
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
        self._link_seq = 0                           # 联动动作轮换计数
        # 过程汇报气泡：agent → (上次文案, 时刻)；全局最后一条时刻
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0
        self._phrase_picker = PhrasePicker()
        # 待处理阻塞型交互：interaction_id → {"agent_key", "kind": "approval"|"question",
        # "text": str, "tool"?: str, "questions"?: list, "rpc_id"?, "approval_id"?,
        # "session_id"?, "alert_id"}。审批 / 用户问题都是「阻塞 Agent 等待用户输入」的
        # 交互，统一处理：气泡「一直挂到 resolved」。
        # interaction_id 优先用 rpcId（同一审批/问题的稳定标识），无 rpcId 时用
        # agent+kind+本地序号（降级提示路径）——**同一 agent 的多个审批各自独立
        # 存储**，不再以 agent_key 为键互相覆盖。按钮回调捕获 interaction_id，
        # 点「同意」只对对应那条审批生效；resolved 也按 rpcId 精确匹配关闭，
        # 绝不错放行/错关闭其他并发的审批。
        self._pending_interactions: dict[str, dict] = {}
        self._interaction_seq = 0  # 无 rpcId 的降级提示交互本地序号

        self.monitors: dict[str, BaseAgentMonitor] = {
            "dsh": DshMonitor("dsh", self.config_dir, self),
            "claude": ClaudeCodeMonitor("claude", self.config_dir, self),
            "cursor": CursorMonitor(self.config_dir, self),
            "opencode": OpenCodeMonitor(self.config_dir, self),
            "codex": CodexMonitor(self.config_dir, self),
        }

        # 卡住检测（stuck_detector）：DSH 专属，消费桥接增强记录推断「人工介入更快」。
        from .stuck_detector import StuckDetector
        self._stuck_detector = StuckDetector(self)
        self.monitors["dsh"].raw_record.connect(self._stuck_detector.feed_record)
        self._stuck_detector.intervention_recommended.connect(self._on_stuck_intervention)
        self._stuck_detector.stuck_resolved.connect(self._on_stuck_resolved)

        # 行为模式检测（behavior_detector）：DSH 专属，双窗口规则识别
        # 慢性循环 / 短时爆发 / 纯探索无产出。与 stuck_detector（失败评分）互补。
        from .behavior_detector import BehaviorPatternDetector
        self._behavior_detector = BehaviorPatternDetector(self)
        self.monitors["dsh"].raw_record.connect(self._behavior_detector.feed_record)
        self._behavior_detector.pattern_warning.connect(self._on_pattern_warning)
        self._behavior_detector.pattern_control.connect(self._on_pattern_control)

        # Agent Exploration Loop Watchdog：按 session/step 聚合全部探索行为。
        from .exploration_watchdog import ExplorationWatchdog
        self._exploration_watchdog = ExplorationWatchdog(self)
        self._exploration_watchdog.set_judge(self._run_exploration_judge)
        self.monitors["dsh"].raw_record.connect(self._exploration_watchdog.feed_record)
        self.monitors["dsh"].raw_record.connect(self._on_exploration_lifecycle)
        self._exploration_watchdog.warning.connect(self._on_exploration_warning)
        self._exploration_watchdog.judge_required.connect(self._on_exploration_judge_required)
        self._exploration_watchdog.judge_result.connect(self._on_exploration_judge_result)
        # 会话元数据缓存：sessionId → { label, projectName, agentName }
        self._session_meta_cache: dict[str, dict] = {}
        self._exploration_alerts: dict[str, str] = {}
        self._exploration_names: dict[str, str] = {}
        self._exploration_active_generation: dict[str, str] = {}
        self._exploration_lifecycle_epoch: dict[str, int] = {}
        self._exploration_control_result.connect(self._on_exploration_control_result)

        for mon in self.monitors.values():
            mon.state_changed.connect(self._on_agent_state)
            mon.activity.connect(self._on_agent_activity)
            mon.approval_requested.connect(self._on_approval_request)
            mon.approval_resolved.connect(self._on_approval_resolved)
            mon.question_requested.connect(self._on_question_request)
            mon.question_resolved.connect(self._on_question_resolved)
            mon.execution_failed.connect(self._on_execution_failed)
        self.monitors["dsh"].session_meta.connect(self._on_session_meta)
        self.monitors["dsh"].rate_limit.connect(self._on_rate_limit)
        # 429 限流缓存：session_key → { "count": int, "_ts": float, "_first_ts": float, "_dismissed": bool }
        self._429_cache: dict[str, dict] = {}
        self._429_timers: dict[str, QTimer] = {}   # session_key → 自动收起定时器
        self._respond_result.connect(self._on_respond_result)
        self.install_finished.connect(self._on_install_finished)
        # 联动动作链：一次性动作播完后若仍有 Agent 在忙，由 window 回调取下一个动作
        if hasattr(self.win, "_pending_link_anim"):
            self.win._link_next_provider = self._next_busy_anim

        self.apply_config()

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
        # 卡住检测：开关 + 阈值/窗口/冷却参数同步（DSH 联动开启才有效）
        self._stuck_detector.set_enabled(bool(agent_cfg.get("stuck_detect", False)))
        self._stuck_detector.get_config_overrides(agent_cfg if isinstance(agent_cfg, dict) else {})
        # 行为模式检测：开关 + 双窗口/step/冷却参数同步
        self._behavior_detector.set_enabled(bool(agent_cfg.get("pattern_detect", False)))
        self._behavior_detector.get_config_overrides(agent_cfg if isinstance(agent_cfg, dict) else {})
        self._exploration_watchdog.configure(agent_cfg if isinstance(agent_cfg, dict) else {})

    def _install_dsh_worker(self) -> None:
        """后台线程：安装 DSH 桥接插件，完成后信号回主线程。"""
        ok, msg = DshMonitor.install_bridge()
        self.install_finished.emit("dsh", ok, msg)

    def _warn_if_agent_absent(self, agent_key: str) -> None:
        """开启了联动但本机没装对应 Agent 时给用户提示（不然勾了永远没反应）。"""
        hints = {
            "cursor": ("Cursor", Path.home() / ".cursor" / "projects"),
            "opencode": ("OpenCode", Path.home() / ".local" / "share" / "opencode" / "opencode.db"),
            "codex": ("Codex", None),
        }
        item = hints.get(agent_key)
        if not item:
            return
        name, marker = item
        # Codex 融合进 ChatGPT 桌面 App：无独立安装目录可探测，用运行中的进程判断
        if agent_key == "codex":
            if not self._codex_app_running() and hasattr(self.win, "show_bubble"):
                self.win.show_bubble(
                    self._dialogue("agent.missing", f"已开启 {name} 联动监听，但没检测到 ChatGPT 桌面端在运行——打开 ChatGPT App 我才能感知到哦", name=name),
                    duration_ms=6000,
                )
            return
        if not marker.exists() and hasattr(self.win, "show_bubble"):
            self.win.show_bubble(
                self._dialogue("agent.missing", f"已开启 {name} 联动监听，但没检测到本机安装 {name}——装了它我才能感知到哦", name=name),
                duration_ms=6000,
            )

    @staticmethod
    def _codex_app_running() -> bool:
        """探测 ChatGPT 桌面端（内含 Codex app-server）进程是否在运行。"""
        if os.name != "nt":
            return False
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            return "ChatGPT.exe" in out
        except Exception:
            return False

    def _on_install_finished(self, agent_key: str, ok: bool, msg: str) -> None:
        """安装完成：成功则正式开启联动，失败则提示。"""
        if ok:
            ag_cfg = dict(self.cfg.get("agent_link", {}))
            ag_cfg[agent_key] = True
            self.cfg.set("agent_link", ag_cfg)
            self.cfg.save()
            self.apply_config()
            if hasattr(self.win, "show_bubble"):
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self.win.show_bubble(self._dialogue("bridge.install.success", "DSH 桥接插件已装好，联动开启～", name=name), duration_ms=4000)
        else:
            log.warning("DSH 桥接插件安装失败: %s", msg)
            if hasattr(self.win, "show_bubble"):
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self.win.show_bubble(self._dialogue("bridge.install.failed", f"DSH 桥接插件安装失败：{msg}", name=name, detail=msg), duration_ms=6000)

    def _other_instances_enabled(self, agent_key: str) -> bool:
        """其他多开实例（含默认实例）是否也开着该 Agent 联动。
        hooks/桥接插件是全局状态，别的实例还在用就不能卸。"""
        try:
            candidates = [self.config_dir / "config.json"] + list(self.config_dir.glob("config-*.json"))
            for f in candidates:
                if self.cfg.path and f == self.cfg.path:
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and bool((data.get("agent_link") or {}).get(agent_key, False)):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

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
                # 菜单先回弹，安装完成后自动开启并气泡告知
                if hasattr(self.win, "show_bubble"):
                    name = self.AGENT_NAMES.get(agent_key, agent_key)
                    self.win.show_bubble(self._dialogue("bridge.install.pending", "正在安装 DSH 桥接插件…", name=name), duration_ms=4000)
                import threading
                threading.Thread(
                    target=self._install_dsh_worker, daemon=True, name="dsh-bridge-install",
                ).start()
                return False
        else:
            # 关闭联动时移除我们注入的内容（只删自己的，用户自有配置不碰）；
            # 其他多开实例仍在使用则保留（hooks/插件是全局状态）
            if agent_key == "claude":
                if self._other_instances_enabled("claude"):
                    log.info("其他实例仍在使用 Claude 联动，保留 hooks")
                elif not ClaudeCodeMonitor.uninstall_hooks():
                    log.warning("Claude hooks 卸载未完全成功（配置已关闭，hooks 可能残留）")
                    if hasattr(self.win, "show_bubble"):
                        name = self.AGENT_NAMES.get(agent_key, agent_key)
                        self.win.show_bubble(self._dialogue("bridge.uninstall.failed", "Claude hooks 卸载未完全成功，可手动检查 ~/.claude/settings.json", name=name), duration_ms=6000)
            elif agent_key == "dsh":
                if self._other_instances_enabled("dsh"):
                    log.info("其他实例仍在使用 DSH 联动，保留桥接插件")
                elif not DshMonitor.uninstall_bridge():
                    log.warning("DSH 桥接插件卸载未完全成功（配置已关闭，插件可能残留）")
                    if hasattr(self.win, "show_bubble"):
                        name = self.AGENT_NAMES.get(agent_key, agent_key)
                        self.win.show_bubble(self._dialogue("bridge.uninstall.failed", "DSH 桥接插件卸载未完全成功", name=name), duration_ms=6000)

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
        self._stuck_detector.pause()
        self._behavior_detector.pause()
        if hasattr(self.win, "_pending_link_anim"):
            self.win._pending_link_anim = None
        for key in list(self._done_pending):
            self._cancel_done_check(key)

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()
        self._stuck_detector.resume()
        self._behavior_detector.resume()

    def _on_agent_state(self, agent_key: str, state: str) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。"""
        # 兜底：该 agent 已回待机（任务结束）但审批/问题还没收到 resolved → 交互必然失效。
        # 放在可见性判断之前：窗口隐藏期间也要清 pending，避免恢复显示时挂出陈旧气泡。
        if state in ("idle", "sleeping"):
            # 改为按交互 id 遍历清理（同一 agent 可能有多个并发审批/问题）
            for iid in [i for i, v in self._pending_interactions.items()
                        if v.get("agent_key") == agent_key]:
                item = self._pending_interactions.pop(iid, None)
                if item is None:
                    continue
                alert_id = item.get("alert_id", "")
                if alert_id and hasattr(self.win, "resolve_alert"):
                    self.win.resolve_alert(alert_id)
                elif hasattr(self.win, "hide_bubble"):
                    self.win.hide_bubble()

        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return

        now = self._clock()
        # --- 原始状态流（绕开去抖/节流）：busy→idle 完成检测 ---
        # 不能用 _last_applied 判定完成——节流会丢掉紧跟的 idle，导致完成通知丢失。
        prev_raw = self._last_raw.get(agent_key)
        self._last_raw[agent_key] = state
        if state in self._BUSY_STATES:
            self._cancel_done_check(agent_key)
            self._saw_alert.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in self._BUSY_STATES:
            self._saw_alert.add(agent_key)
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

        # 状态 -> 桌宠行为映射（手册 §8.2）
        if state in ("thinking", "working"):
            # busy 动作池轮换（写代码/吃Token 为主，每第 3 次插播短摸鱼），
            # 经 request_link_anim 平滑衔接：正在播的一次性动作不被打断
            anim = self._next_link_anim_rotation()
            if anim and hasattr(self.win, "request_link_anim"):
                self.win.request_link_anim(anim)
            self._maybe_notify_start(agent_key, prev_raw, state)
        elif state == "attention":
            # busy 后的 attention（如 Claude Stop=回合结束）由完成确认流程接管，
            # 避免「需要看一眼」和「完成通知」双气泡；独立出现的才立即提醒
            if prev_raw not in self._BUSY_STATES:
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self._show_link_bubble(self._dialogue("agent.attention", "主人，Agent 这边需要你看一眼～", name=name), important=True)
        elif state == "error":
            if prev_raw not in self._BUSY_STATES:
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self._show_link_bubble(self._dialogue("agent.error", "Agent 执行好像遇到报错了…", name=name), important=True)
        elif state in ("sleeping", "idle"):
            # 回到待机：一次性动作播完自然回，待机/移动中立即回
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            elif hasattr(self.win, "idles") and hasattr(self.win, "_pick") and self.win.idles:
                self.win._switch(self.win._pick(self.win.idles))

    # ------------------------------------------------------------------
    # 联动动作池（写代码/吃Token 交替为主，每第 3 次插播短摸鱼）
    # ------------------------------------------------------------------
    _LINK_MAIN = ("写代码", "吃Token")
    _LINK_BREAK = ("轻快记录", "漂浮踏步")
    _LINK_MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
    _LINK_BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

    def _next_link_anim_rotation(self) -> str | None:
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

    def _next_busy_anim(self) -> str | None:
        """window 动画结束回调用：仍有 Agent 在忙 → 下一个联动动作；否则 None。
        全员空闲时重置轮换计数——下一个任务从「写代码」重新开始。"""
        if any(s in self._BUSY_STATES for s in self._last_raw.values()):
            return self._next_link_anim_rotation()
        self._link_seq = 0
        return None

    # 进程名 → Agent：该 Agent 联动开启且正忙时，主动识屏跳过它的窗口
    # （联动气泡已在汇报进度，识屏再评一句就是重复打扰）。
    # opencode/cursor 有独立桌面进程按进程名识别；dsh 跑在浏览器/应用窗口里，
    # 按窗口标题识别；claude 在终端里标题不可控，不映射。
    # codex 融合进 ChatGPT 桌面 App：按 ChatGPT.exe 进程名识别。
    AGENT_PROCESS_HINTS = {
        "opencode": ("opencode.exe",),
        "cursor": ("cursor.exe",),
        "codex": ("chatgpt.exe",),
    }
    AGENT_TITLE_HINTS = {
        "dsh": ("deepseek harness",),
    }

    def busy_agent_owns_process(self, process_name: str, title: str = "") -> bool:
        """前台窗口是否属于「联动开启且正在忙」的 Agent（进程名或窗口标题命中）。"""
        agent_cfg = self.cfg.get("agent_link", {})
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

    # ------------------------------------------------------------------
    # 联动气泡（开始干活可选 / 任务完成通知）
    # ------------------------------------------------------------------
    # 各 Agent 的默认 thinking 文案；DSH 用角色梗，其他用烧烤梗
    _THINKING_DEFAULTS = {"dsh": "大肥鱼正在深度思考……"}

    def _dialogue(self, key: str, fallback: str, **values) -> str:
        """Render an existing event in the selected wording mode."""
        mode = str(self.cfg.get("dialogue_mode", "legacy") or "legacy")
        if mode == "custom":
            return self._phrase_picker.custom(self.cfg.get("dialogue_phrases", {}), key, fallback, **values)
        return self._phrase_picker.get(mode, key, fallback, **values)

    def _thinking_text(self, agent_key: str) -> str:
        """thinking 气泡文案：按 Agent 自定义 > 旧全局自定义 > 按 Agent 默认。"""
        agent_cfg = self.cfg.get("agent_link", {})
        custom = (agent_cfg.get("thinking_texts") or {}).get(agent_key, "").strip()
        # 兼容旧的全局 thinking_text 字段（设置页保存时已自动迁移）
        if not custom:
            custom = str(agent_cfg.get("thinking_text", "") or "").strip()
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        if custom:
            return custom.replace("{name}", name)
        if agent_key in self._THINKING_DEFAULTS:
            fallback = self._THINKING_DEFAULTS[agent_key]
            return self._dialogue("thinking", fallback, name=name)
        return self._dialogue("thinking", f"{name} 正在深度烧烤……", name=name)

    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None, state: str = "working") -> None:
        """开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
        低优先级：气泡位被占时直接丢弃。thinking 状态用更有趣的文案。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_state", False):
            return
        if prev_raw in self._BUSY_STATES:
            return
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        if state == "thinking":
            self._show_link_bubble(self._thinking_text(agent_key), important=False, duration_ms=3000)
        else:
            self._show_link_bubble(
                self._dialogue("start", f"{name} 开始干活啦～", name=name),
                important=False, duration_ms=3000,
            )

    def _on_agent_activity(self, agent_key: str, tool: str) -> None:
        """过程汇报气泡（可选，默认关）：「DSH 正在读文件…」这类。
        白名单工具映射 + 三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。"""
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
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        # 低优先级：气泡位被占直接丢弃，不与重要气泡竞争
        tool_key = str(tool).strip().lower()
        if tool_key in {"read", "read_page"}:
            key = "activity.read"
        elif tool_key in {"grep", "glob", "search", "websearch", "web_search", "webfetch", "fetch", "browser", "web_fetch"}:
            key = "activity.search"
        elif tool_key in {"edit", "write", "notebookedit"}:
            key = "activity.edit"
        elif tool_key in {"bash", "shell", "pwsh", "powershell"}:
            key = "activity.run"
        else:
            key = "activity.default"
        text = self._dialogue(key, f"{name} {label}…", name=name)
        self._show_link_bubble(text, important=False, duration_ms=2600)

    def _on_approval_request(self, agent_key: str, payload: dict) -> None:
        """审批请求提醒：一直挂到审批结束的高优先级气泡。

        payload 带 rpcId/approvalId/sessionId 时进入交互模式：气泡内嵌
        「同意 / 拒绝」按钮，点击即 POST /api/respond 回写 DSH（web UI 同款
        client-response 机制），当场解除阻塞。无 rpcId（桥接旧路径/降级）时
        退化为纯提示气泡，仍需到 DSH 界面点。

        气泡文案优先展示被审批命令的完整内容（payload.command，来自 bridge
        的 arguments），让用户不用去 DSH 界面就能看到要批准什么。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_approval", True):
            return
        payload = payload if isinstance(payload, dict) else {}
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        tool = str(payload.get("toolName") or payload.get("tool") or "").strip()
        command = str(payload.get("command") or "").strip()
        session_id = str(payload.get("sessionId") or "")
        session_display = self.get_session_display_name(session_id) if session_id else ""
        prefix = f"{session_display} · " if session_display and session_display != f"DSH · {session_id[:8]}" else ""
        if command:
            # 命令全文优先：折叠换行/空白成单行，超长截断加省略号（气泡是图片气泡）
            formatted = self._format_command(command)
            text = self._dialogue(
                "approval.command", f"{prefix}{name} 请求执行：{formatted}，请选择：",
                command=formatted, name=name,
            )
        else:
            tool_lower = tool.lower()
            label = self.TOOL_LABELS.get(tool_lower, "")
            if label:
                text = self._dialogue(
                    "approval.tool", f"{prefix}{name} 在请求审批：{label}，请选择：",
                    label=label, name=name,
                )
            elif tool:
                text = self._dialogue("approval.tool", f"{prefix}{name} 有审批等你决定（{tool}）：", label=tool, name=name)
            else:
                text = self._dialogue("approval.generic", f"{prefix}{name} 有审批等你决定：", name=name)
            if not label and not tool:
                text = self._dialogue("approval.generic", text, name=name)
        self._register_interaction(
            agent_key, kind="approval", text=text, tool=tool, command=command,
            interactive=bool(payload.get("rpcId")),
            rpc_id=payload.get("rpcId"),
            approval_id=payload.get("approvalId"),
            session_id=session_id,
        )

    @staticmethod
    def _format_command(command: str, max_len: int = 160) -> str:
        """把命令折叠成单行并安全截断，供气泡展示。

        - 连续空白/换行折叠成单个空格（气泡图片不保留换行）
        - 超过 max_len 截断并追加省略号，避免撑爆气泡
        """
        text = " ".join(str(command).split())
        if len(text) > max_len:
            text = text[:max_len].rstrip() + "…"
        return text

    def _on_question_request(self, agent_key: str, payload: dict) -> None:
        """用户问题（ask_user_question）阻塞交互：与审批同待遇的常驻气泡。

        payload 带 rpcId 且问题带 options 时进入交互模式：气泡内嵌每个选项的
        按钮，点击即 POST /api/respond 回写 DSH（当场选中该选项）。无 rpcId
        （桥接旧路径）或自由输入类问题（无 options，必须敲字）时退化为纯提示，
        仍需到 DSH 界面输入/确认。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_approval", True):  # 与审批同一开关
            return
        payload = payload if isinstance(payload, dict) else {}
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        questions = payload.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        session_id = str(payload.get("sessionId") or "")
        session_display = self.get_session_display_name(session_id) if session_id else ""
        prefix = f"{session_display} · " if session_display and session_display != f"DSH · {session_id[:8]}" else ""
        self._register_interaction(
            agent_key, kind="question", text=self._question_text(name, questions, prefix=prefix),
            questions=questions,
            interactive=bool(payload.get("rpcId")) and self._question_has_options(questions),
            rpc_id=payload.get("rpcId"),
            session_id=session_id,
        )

    @staticmethod
    def _question_has_options(questions: list) -> bool:
        """问题是否带可点选选项（交互气泡的前提）。"""
        for q in questions:
            if isinstance(q, dict) and q.get("options"):
                return True
        return False

    def _question_text(self, name: str, questions: list, *, prefix: str = "") -> str:
        """把 questions 载荷排版成气泡文案（单行紧凑）。

        泡泡是图片气泡：normalize_bubble_text 会把换行折叠成空格，且 sticky 只
        显示第一页——所以选项用「 / 」内联拼接而非强行多行，保证「永久选项弹窗」
        在小气泡里完整可见（交互模式下按钮本身也展示了选项）。"""
        if not questions:
            return self._dialogue("question.empty", f"{prefix}{name} 在等你回答一个问题，快去看一下～", name=name)
        if len(questions) > 1:
            return self._dialogue("question.many", f"{prefix}{name} 有 {len(questions)} 个问题等你回答，快去看一下～", count=len(questions), name=name)
        q = questions[0]
        if not isinstance(q, dict):
            q = {}
        body = str(q.get("question") or "（问题）")
        header = str(q.get("header") or "").strip()
        if header:
            body = f"{header}：{body}"
        opts = q.get("options") or []
        labels = []
        for o in opts:
            label = str(o.get("label") or "") if isinstance(o, dict) else str(o)
            if label:
                labels.append(label)
        multi = "（可多选）" if q.get("multiSelect") else ""
        if labels:
            return f"{name} 在问你：{body}（{' / '.join(labels)}）{multi}请选择一个："
        if multi:
            return f"{name} 在问你：{body}（可多选）快去选一下～"
        return self._dialogue("question.one", f"{name} 在问你：{body}，需要你输入，快去看一下～", body=body, name=name)

    def _interaction_key(self, agent_key: str, kind: str, rpc_id) -> str:
        """生成稳定交互 id：有 rpcId 用 rpcId（同一审批/问题的稳定标识），
        无 rpcId（旧路径降级提示）用 agent+kind+本地序号保证唯一。"""
        if rpc_id:
            return f"{kind}:{rpc_id}"
        self._interaction_seq += 1
        return f"{kind}:{agent_key}:hint{self._interaction_seq}"

    def _register_interaction(self, agent_key: str, *, kind: str, text: str, **extra) -> str | None:
        """统一登记一条阻塞型交互：进 pending + 打 alert 标记 + 挂常驻气泡。

        返回该交互的稳定 interaction_id（按钮/回写/resolve 按它精确定位）；
        记录被判定为重复/降级而忽略时返回 None。

        - **同一 agent 的多个审批/问题各自独立存储**（按 interaction_id），
          不再以 agent_key 为键互相覆盖——按钮点击与 resolved 都按 id 精确绑定，
          绝不错放行/错关闭其他并发的审批。
        - 同一审批/问题可能先后到达「无 rpcId 提示 → 带 rpcId 交互」两条记录
          （桥接双通道/竞态）：交互版到达时优先升级已挂着的同款无 rpcId 提示
          （同一条审批/问题）；提示版到达时若已有可交互同款则忽略（不降级）。
        """
        rpc_id = extra.get("rpc_id")
        if not rpc_id:
            # 无 rpcId 提示：若已有可交互同款（带 rpcId），忽略，不降级
            for item in self._pending_interactions.values():
                if (item.get("agent_key") == agent_key and item.get("kind") == kind
                        and item.get("interactive") and item.get("rpc_id")):
                    return None
        else:
            # 交互版到达：优先升级同 agent+kind 已挂着的无 rpcId 提示（同一条审批/问题）
            for iid, item in self._pending_interactions.items():
                if (item.get("agent_key") == agent_key and item.get("kind") == kind
                        and not item.get("rpc_id")):
                    self._pending_interactions[iid] = {
                        "kind": kind, "text": text,
                        "alert_id": item.get("alert_id", ""),
                        "agent_key": agent_key, **extra,
                    }
                    self._saw_alert.add(agent_key)
                    self._show_interaction_bubble(iid)
                    return iid
        iid = self._interaction_key(agent_key, kind, rpc_id)
        # 同 rpcId 重复到达：就地覆盖内容（不排队第二条气泡）
        alert_id = f"interaction:{iid}"
        self._pending_interactions[iid] = {
            "kind": kind, "text": text, "alert_id": alert_id,
            "agent_key": agent_key, **extra,
        }
        # 交互打断算"需要主人看一眼"：任务完成后不误说"干完活啦"
        self._saw_alert.add(agent_key)
        # 常驻气泡：不自动消失，等 resolved / 离线 / 空闲再收尾
        self._show_interaction_bubble(iid)
        return iid

    def _on_approval_resolved(self, agent_key: str, payload: dict | None = None) -> None:
        """审批结束（approval/resolved mux 帧带 rpcId/approvalId，或旧路径
        approval/decided 无 id）：按 id 精确关闭对应交互记录。

        并发多个审批时，带 id 的 resolved 帧只关闭自己那一条。**带 id 但
        未匹配到任何 pending 的帧是陈旧的已解决帧**（该审批已由用户在气泡
        里点选解决，DSH 稍后才回发确认）——绝不能回退去关闭其他 pending
        审批，否则用户点了 A，A 的 resolved 帧会把 B 误关（表现为第二个
        弹窗延迟 0.5~1s 后自动消失，DSH 却只收到第一个审批的决策）。
        兜底仅用于无任何 id 的旧路径 approval/decided（单 pending 时关闭）。"""
        payload = payload if isinstance(payload, dict) else {}
        rpc_id = payload.get("rpcId")
        approval_id = payload.get("approvalId")
        if rpc_id:
            for iid, item in self._pending_interactions.items():
                if item.get("kind") == "approval" and item.get("rpc_id") == rpc_id:
                    self._resolve_interaction(iid)
                    return
            return  # 带 id 但未匹配：陈旧已解决帧，不动其他审批
        if approval_id:
            for iid, item in self._pending_interactions.items():
                if item.get("kind") == "approval" and item.get("approval_id") == approval_id:
                    self._resolve_interaction(iid)
                    return
            return  # 同上：带 approvalId 未匹配即陈旧帧，不兜底
        # 无 id 的旧路径 approval/decided：只对「无 rpc_id 的纯提示」兜底关闭。
        # 交互审批（带 rpc_id）由 mux approval/resolved 帧精确关闭——若这里
        # 对交互审批兜底，用户点了 A 后 DSH 回发的 A 的 decided（无 id）会把
        # 还在等待的 B 误关（表现为第二个弹窗延迟 0.5~1s 后自动消失）。
        candidates = [iid for iid, item in self._pending_interactions.items()
                      if item.get("kind") == "approval" and item.get("agent_key") == agent_key
                      and not item.get("rpc_id")]
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])

    def _on_question_resolved(self, agent_key: str, payload: dict | None = None) -> None:
        """问题结束（question/resolved）：按 rpcId 精确关闭对应交互记录。

        与审批同理：带 id 的 resolved 帧只关闭自己那一条；带 id 但未匹配的
        帧是陈旧已解决帧，绝不回退关闭其他 pending 问题；兜底（无 id 的旧
        路径）仅对无 rpc_id 的纯提示问题生效，交互问题由 mux 帧精确关闭。"""
        payload = payload if isinstance(payload, dict) else {}
        rpc_id = payload.get("rpcId")
        if rpc_id:
            for iid, item in self._pending_interactions.items():
                if item.get("kind") == "question" and item.get("rpc_id") == rpc_id:
                    self._resolve_interaction(iid)
                    return
            return  # 带 id 但未匹配：陈旧已解决帧，不动其他问题
        candidates = [iid for iid, item in self._pending_interactions.items()
                      if item.get("kind") == "question" and item.get("agent_key") == agent_key
                      and not item.get("rpc_id")]
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])

    def _resolve_interaction(self, interaction_id: str) -> None:
        """阻塞型交互结束：按 interaction_id 精确清掉该条记录，并让提醒队列自然推进。

        注意：队列（window.show_alert）自己会逐条展示，这里用 resolve_alert
        按 alert_id 精确定位关闭，避免 hide_bubble 误关其他 agent/其他并发审批
        的提醒。"""
        item = self._pending_interactions.pop(interaction_id, None)
        if item is None:
            return
        alert_id = item.get("alert_id", "")
        if alert_id and hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(alert_id)
        elif hasattr(self.win, "hide_bubble"):
            self.win.hide_bubble()

    def pending_interactions_for(self, agent_key: str) -> dict[str, dict]:
        """返回该 agent 的全部 pending 交互（interaction_id → item）。

        同一 agent 可同时存在多条阻塞交互（多个并发审批/问题），以稳定
        interaction_id 索引；本方法供上层/测试按 agent 检索。"""
        return {iid: item for iid, item in self._pending_interactions.items()
                if item.get("agent_key") == agent_key}

    def dismiss_all_interactions(self) -> None:
        """清空全部待处理阻塞交互并关闭气泡（DSH 离线/重启时交互必然失效）。"""
        if not self._pending_interactions and not getattr(self.win, "_sticky_bubble_active", False):
            return
        self._pending_interactions.clear()
        if hasattr(self.win, "clear_alerts"):
            self.win.clear_alerts()
        elif hasattr(self.win, "hide_bubble"):
            self.win.hide_bubble()

    def dismiss_all_approvals(self) -> None:
        """兼容别名：等价 dismiss_all_interactions。"""
        self.dismiss_all_interactions()

    def _show_interaction_bubble(self, interaction_id: str) -> None:
        """把某条 pending 阻塞交互以 sticky 气泡挂上（可交互时内嵌按钮）。

        走提醒消息队列（show_alert）：审批/问题入队后一次只展示一个，
        队列非空时其他弹窗不覆盖；resolved 时经 resolve_alert 弹下一条。
        以 interaction_id 精确定位，保证并发审批各自的气泡互不干扰。"""
        pending = self._pending_interactions.get(interaction_id)
        if not pending:
            return
        if not hasattr(self.win, "show_alert"):
            if hasattr(self.win, "show_bubble"):
                # 旧桩/无 show_alert 的窗口：退化为普通气泡，绝不因签名差异崩溃
                try:
                    buttons = self._interaction_buttons(interaction_id)
                    if buttons:
                        self.win.show_bubble(pending["text"], sticky=True, buttons=buttons)
                    else:
                        self.win.show_bubble(pending["text"], sticky=True)
                except TypeError:
                    self.win.show_bubble(pending["text"])
            return
        buttons = self._interaction_buttons(interaction_id)
        self._show_alert_compat(
            pending["text"], subtitle="", buttons=buttons or None, sticky=True,
            alert_id=pending.get("alert_id", ""), priority=0, alert_type=pending.get("kind", "approval"),
        )

    def _show_alert_compat(self, text: str, **kwargs) -> None:
        """Use enriched alert metadata while remaining compatible with test/old windows."""
        try:
            self.win.show_alert(text, **kwargs)
        except TypeError:
            legacy = dict(kwargs)
            for key in ("priority", "alert_type", "metadata"):
                legacy.pop(key, None)
            self.win.show_alert(text, **legacy)

    def _interaction_buttons(self, interaction_id: str) -> list[tuple[str, Callable]] | None:
        """为某条可交互 pending 阻塞交互构建按钮 [(label, callback)]；不可交互返回 None。

        按钮回调捕获 interaction_id：点击只对该条交互回写，与同一 agent 的
        其他并发审批互不干扰（修复「点同意却放行后面那个请求」的覆盖 bug）。"""
        pending = self._pending_interactions.get(interaction_id)
        if not pending or not pending.get("interactive") or not pending.get("rpc_id"):
            return None
        if pending.get("kind") == "approval":
            return [
                ("同意", lambda iid=interaction_id: self._respond_interaction(iid, "allowed-once")),
                ("拒绝", lambda iid=interaction_id: self._respond_interaction(iid, "rejected")),
            ]
        # question：每个选项一个按钮，点击即回答该选项（selected=[该选项 label]）
        questions = pending.get("questions") or []
        q = questions[0] if questions else {}
        if not isinstance(q, dict):
            q = {}
        opts = q.get("options") or []
        labels = []
        for o in opts:
            label = str(o.get("label") or "") if isinstance(o, dict) else str(o)
            if label:
                labels.append(label)
        if not labels:
            return None
        return [
            (label, lambda iid=interaction_id, lbl=label: self._respond_interaction(iid, [lbl]))
            for label in labels
        ]

    @staticmethod
    def _build_respond_message(pending: dict, decision) -> dict | None:
        """把 pending 交互 + 点选决策构造成 client-response 消息；无 rpcId 返回 None。

        decision：审批为 "allowed-once"/"rejected"；问题为选中的选项 label 列表。"""
        if not pending or not pending.get("rpc_id"):
            return None
        rpc_id = str(pending.get("rpc_id"))
        session_id = pending.get("session_id")
        if pending.get("kind") == "approval":
            value = {
                "sessionId": session_id,
                "approvalId": pending.get("approval_id"),
                "outcome": str(decision),
            }
        else:
            qid = None
            questions = pending.get("questions") or []
            if questions and isinstance(questions[0], dict):
                qid = questions[0].get("id")
            selected = decision if isinstance(decision, (list, tuple)) else [decision]
            value = {
                "sessionId": session_id,
                "answer": {"answers": [{"id": qid, "selected": [str(s) for s in selected]}]},
            }
        return {
            "type": "client-response",
            "rpcId": rpc_id,
            "result": {"ok": True, "value": value},
        }

    def _respond_interaction(self, interaction_id: str, decision) -> None:
        """交互按钮点选后回写 DSH：后台线程 POST /api/respond，绝不阻塞主线程。

        decision：审批为 "allowed-once"/"rejected"；问题为选中的选项 label 列表。
        以 interaction_id 精确定位，只对该条交互回写并关闭。"""
        pending = self._pending_interactions.get(interaction_id)
        if not pending:
            return
        msg = self._build_respond_message(pending, decision)
        if msg is None:
            return
        # 气泡使命已完成：立即收起；DSH 的 resolved 帧会确认并兜底收尾状态。
        # 若 POST 失败，_on_respond_result 会提示到 DSH 界面处理。
        self._resolve_interaction(interaction_id)
        try:
            worker = threading.Thread(
                target=self._post_respond_worker, args=(pending.get("agent_key", ""), msg), daemon=True
            )
            worker.start()
        except Exception:
            log.exception("DSH 回写线程启动失败")
            self._show_link_bubble(self._dialogue("dsh.writeback.failed", "回写 DSH 失败，请到 DSH 界面处理"), important=True)

    def _post_respond_worker(self, agent_key: str, msg: dict) -> None:
        """后台线程：找在线 DSH 端口并 POST /api/respond，结果经信号回主线程。"""
        try:
            from . import dsh_responder
            ok, detail = dsh_responder.respond(msg, self._dsh_candidate_ports())
        except Exception as exc:  # noqa: BLE001 —— 后台线程绝不允许把异常带进 Qt 事件循环
            ok, detail = False, str(exc)
        try:
            self._respond_result.emit(ok, detail)
        except Exception:
            pass

    def _dsh_candidate_ports(self) -> list[int]:
        """DSH 可能运行的端口（web 默认 3080，被占时避让 38080，或 DSH_PORT 指定）。"""
        ports = {3080, 38080}
        env_port = os.environ.get("DSH_PORT")
        if env_port:
            try:
                ports.add(int(env_port))
            except ValueError:
                pass
        return sorted(ports)

    def _on_respond_result(self, ok: bool, detail: str) -> None:
        """DSH 回写结果：成功静默（resolved 帧收尾）；失败提示到 DSH 界面处理。"""
        if ok:
            return
        log.warning("DSH 回写失败: %s", detail)
        try:
            if hasattr(self.win, "show_bubble") and self.win.isVisible():
                self.win.show_bubble(self._dialogue("dsh.writeback.failed", "回写 DSH 失败，请到 DSH 界面处理"), duration_ms=4000)
        except Exception:
            pass

    def _show_approval_bubble(self, agent_key: str) -> None:
        """兼容别名：把该 agent 的全部 pending 审批/问题气泡挂上（等价
        _show_interaction_bubble，按 agent 遍历其所有交互）。"""
        for iid in list(self._pending_interactions):
            if self._pending_interactions[iid].get("agent_key") == agent_key:
                self._show_interaction_bubble(iid)

    def _schedule_done_check(self, agent_key: str) -> None:
        self._cancel_done_check(agent_key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self._DONE_CONFIRM_MS)
        timer.timeout.connect(lambda k=agent_key: self._fire_done(k))
        self._done_pending[agent_key] = timer
        timer.start()

    def _cancel_done_check(self, agent_key: str) -> None:
        timer = self._done_pending.pop(agent_key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _fire_done(self, agent_key: str) -> None:
        """800ms 稳定确认到期：期间回忙则不算完成；配置/冷却在弹出前再查。"""
        self._done_pending.pop(agent_key, None)
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return  # 隐藏中不弹不切（pause 已取消计时器，这里是兜底）
        if self._last_raw.get(agent_key) in self._BUSY_STATES:
            return
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_done", True):
            return
        now = self._clock()
        if now - self._done_cooldown.get(agent_key, 0.0) < self._DONE_COOLDOWN_S:
            return
        self._done_cooldown[agent_key] = now
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        if agent_key in self._saw_alert:
            # busy 期间出现过 attention/error：不暗示"成功完成"
            text = self._dialogue("done.attention", f"{name} 那边停了，结果怎么样要主人自己看一眼哦", name=name)
        else:
            text = self._dialogue("done.success", f"{name} 干完活啦，去看看成果吧～", name=name)
        self._saw_alert.discard(agent_key)
        # 恢复待机动画：Claude 回合结束没有 idle 事件，不靠这步会一直停在干活动作。
        # 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）。
        # 必须走 request_link_idle（它会清 _link_anim_current 并尊重一次性动作），
        # 不能裸 _switch——否则残留的 link 状态会把以后的普通同名动作劫持进联动链。
        if not any(k != agent_key and s in self._BUSY_STATES
                   for k, s in self._last_raw.items()):
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            elif hasattr(self.win, "idles") and hasattr(self.win, "_pick") and self.win.idles \
                    and hasattr(self.win, "_switch"):
                self.win._switch(self.win._pick(self.win.idles))
            self._last_applied[agent_key] = ("idle", now)
        self._show_link_bubble(text, important=True)

    def _show_link_bubble(self, text: str, *, important: bool, duration_ms: int = 4500,
                          _retried: int = 0) -> None:
        """联动气泡：提醒消息队列非空时一律让路（审批/问题/失败/卡住优先）。

        无提醒队列时：普通气泡直接让路丢弃；重要气泡每 2.5s 重试至多 4 次
        （约 10s 窗口），仍被占才放弃——主动识屏长答复可能占位 15-20s。"""
        if not hasattr(self.win, "show_bubble"):
            return
        # 提醒消息队列激活：任何其他弹窗（含重要气泡）都不覆盖提醒
        if getattr(self.win, "_alert_current", None) is not None or \
                getattr(self.win, "_alert_queue", None):
            return
        if not important and getattr(self.win, "_sticky_bubble_active", False):
            # 兼容旧路径：审批等一直挂着的气泡优先
            return
        busy_until = getattr(self.win, "_bubble_busy_until", 0.0)
        if time.time() < busy_until:
            if not important or _retried >= 4:
                return
            QTimer.singleShot(2500, self,
                              lambda t=text, n=_retried: self._show_link_bubble(
                                  t, important=True, _retried=n + 1))
            return
        self.win.show_bubble(text, duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # 卡住检测（stuck_detector）反应：建议介入动画 + 持续提醒气泡
    # ------------------------------------------------------------------
    _STUCK_WORRIED_KEYWORDS = ("焦急", "着急", "气急败坏", "抓狂", "拍打", "敲桌", "烦恼", "抓狂")
    _STUCK_REMINDER_MS = 20000   # 建议介入提醒持续 20s（非 sticky，避免与审批/问题常驻气泡冲突）

    def _pick_stuck_anim(self) -> str | None:
        """从当前角色动作池里按语义挑选「焦急」动画；缺素材静默跳过。"""
        acts = list(getattr(self.win, "cats", {}).get("acts", []) or [])
        for kw in self._STUCK_WORRIED_KEYWORDS:
            for a in acts:
                if kw in a:
                    return a
        return None

    def _on_stuck_intervention(self, agent_key: str, payload: dict) -> None:
        """卡住评分达到阈值：档位 1 播焦急动画；档位 2 再弹一次持续提醒气泡。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        payload = payload if isinstance(payload, dict) else {}
        severity = int(payload.get("severity", 0) or 0)
        anim = self._pick_stuck_anim()
        if anim and hasattr(self.win, "request_link_anim"):
            self.win.request_link_anim(anim)
        if severity < 2:
            return  # 档位 1：只播动画，不弹气泡
        # 档位 2：持续提醒（可自定义文案；{name} 占位 = Agent 显示名）
        from .stuck_detector import stuck_reminder_text
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        agent_cfg = self.cfg.get("agent_link", {})
        custom = str((agent_cfg.get("stuck_reminder_text") or "") if isinstance(agent_cfg, dict) else "")
        text = stuck_reminder_text(name, custom)
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(self._dialogue("stuck.reminder", text, name=name), duration_ms=self._STUCK_REMINDER_MS, sticky=False)
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._STUCK_REMINDER_MS)

    def _on_stuck_resolved(self, agent_key: str) -> None:
        """卡住解除（Agent 空闲 / 离线 / 任务完成）：回正常动画链。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if any(s in self._BUSY_STATES for k, s in self._last_raw.items() if k != agent_key):
            return  # 其他 Agent 仍在忙，不打断其工作动画
        if hasattr(self.win, "request_link_idle"):
            self.win.request_link_idle()

    # ------------------------------------------------------------------
    # 行为模式检测（behavior_detector）：双窗口行为模式预警/控制
    # ------------------------------------------------------------------
    _PATTERN_REMINDER_MS = 15000  # 行为模式提醒持续 15s（非 sticky）

    def _on_pattern_warning(self, agent_key: str, payload: dict) -> None:
        """行为模式预警（⚠️）：播焦急动画，不弹气泡。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        anim = self._pick_stuck_anim()
        if anim and hasattr(self.win, "request_link_anim"):
            self.win.request_link_anim(anim)

    def _on_pattern_control(self, agent_key: str, payload: dict) -> None:
        """行为模式控制（🛑）：播焦急动画 + 弹气泡。
        payload 中的 verdict 来自可选 Judge，默认 REPLAN（只提醒，不打断 Agent）。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        payload = payload if isinstance(payload, dict) else {}
        anim = self._pick_stuck_anim()
        if anim and hasattr(self.win, "request_link_anim"):
            self.win.request_link_anim(anim)
        # 构建提醒文案
        verdict = str(payload.get("verdict", "") or "")
        reason = str(payload.get("reason", "") or "")
        fine_cls = str(payload.get("class", "") or "")
        count = payload.get("count", 0)
        window = str(payload.get("window", "") or "")
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        if verdict in ("STOP", "ASK_USER"):
            text = (
                f"{name} 行为模式异常（{reason}），"
                f"Judge 建议{'停止' if verdict == 'STOP' else '询问你'}。"
                f"最近 {window} 内 {fine_cls} 出现 {count} 次，可能已陷入低效循环。"
                f"建议人工检查。"
            )
        elif verdict == "REPLAN":
            text = (
                f"{name} 可能陷入低效循环：最近 {window} 内 {fine_cls} 出现 {count} 次，"
                f"建议重新规划任务方向。"
            )
        else:
            text = (
                f"{name} 行为模式需要留意：最近 {window} 内 {fine_cls} 出现 {count} 次。"
            )
        key = "pattern.control" if verdict in ("STOP", "ASK_USER", "REPLAN") else "pattern.warning"
        text = self._dialogue(key, text, name=name, reasons=reason)
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(text, duration_ms=self._PATTERN_REMINDER_MS, sticky=False)
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._PATTERN_REMINDER_MS)

    # ------------------------------------------------------------------
    # Agent Exploration Loop Watchdog
    # ------------------------------------------------------------------
    _EXPLORATION_REMINDER_MS = 18000

    def _on_exploration_warning(self, session_key: str, payload: dict) -> None:
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        reasons = self._format_exploration_reasons((payload or {}).get("reasons", []), (payload or {}).get("steps", []))
        name = self._exploration_name(payload, session_key)
        text = self._dialogue(
            "watchdog.warning", f"{name} 近期存在重复探索行为：{reasons}，暂不打断运行。",
            name=name, reasons=reasons,
        )
        if hasattr(self.win, "show_alert"):
            self._show_alert_compat(text, duration_ms=self._EXPLORATION_REMINDER_MS,
                                sticky=False, alert_id=f"exploration-warning:{session_key}",
                                priority=2, alert_type="watchdog-warning",
                                metadata={"sessionId": session_key, "riskScore": payload.get("risk", 0),
                                          "riskReasons": payload.get("reasons", []),
                                          "targetCount": payload.get("targetCount", 0),
                                          "targets": payload.get("targets", [])})
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._EXPLORATION_REMINDER_MS)

    def _on_exploration_judge_required(self, session_key: str, payload: dict) -> None:
        """风险达到 control：交给后台 Judge；无 Judge 时由 watchdog 安全降级。"""
        self._exploration_active_generation[session_key] = str(payload.get("generation_id") or "")
        payload["lifecycle_epoch"] = self._exploration_lifecycle_epoch.get(session_key, 0)
        self._exploration_watchdog.judge_payload(session_key, payload)

    def _on_exploration_lifecycle(self, agent_key: str, record: dict) -> None:
        """Invalidate watchdog UI/Judge work when the real session ends."""
        if not isinstance(record, dict):
            return
        session = str(record.get("sessionId") or record.get("session_id") or agent_key)
        event = str(record.get("event") or "")
        ended = (event in {"turn/start", "turn/end", "task_complete", "execution/failed"} or
                 (event == "AgentStatus" and record.get("state") in {"idle", "sleeping"}))
        if ended:
            sessions = {session}
            # AgentStatus has no sessionId and represents the aggregate DSH
            # agent. In that case invalidate every session owned by this agent.
            if event == "AgentStatus" and session == agent_key:
                sessions.update(self._exploration_alerts)
                sessions.update(self._exploration_active_generation)
            for key in sessions:
                self._exploration_lifecycle_epoch[key] = self._exploration_lifecycle_epoch.get(key, 0) + 1
                self._exploration_active_generation.pop(key, None)
                self._dismiss_exploration(key)

    def _exploration_provider_config(self):
        from dataclasses import replace
        from .chat.models import ChatSettings
        chat = ChatSettings.from_dict(self.cfg.get("chat", {}))
        agent_cfg = self.cfg.get("agent_link", {})
        provider_id = str(agent_cfg.get("exploration_watchdog_judge_provider", "") or "")
        provider_cfg = chat.providers.get(provider_id) if provider_id else None
        provider_cfg = provider_cfg or chat.active_config
        override = str(agent_cfg.get("exploration_watchdog_judge_model", "") or "")
        if override:
            provider_cfg = replace(provider_cfg, model=override)
        try:
            provider_cfg.api_key = self.cfg.resolve_api_key(provider_cfg)
        except Exception:
            pass
        return replace(provider_cfg, temperature=0.0, max_tokens=700,
                       timeout=float(agent_cfg.get("exploration_watchdog_judge_timeout", 8)))

    def _run_exploration_judge(self, prompt: str) -> str:
        """使用当前聊天 Provider 执行短 Judge 请求；调用方已在后台线程。"""
        from .chat.providers import OpenAICompatibleProvider
        import threading as _threading
        cfg = self._exploration_provider_config()
        log.info("watchdog judge provider=%s model=%s timeout=%s", getattr(cfg, "provider", ""), getattr(cfg, "model", ""), getattr(cfg, "timeout", ""))
        chunks = OpenAICompatibleProvider().stream(
            [{"role": "system", "content": "你是严格输出 JSON 的 Agent 循环检测 Judge。"},
             {"role": "user", "content": prompt}],
            cfg, _threading.Event(),
        )
        return "".join(chunks)

    def _run_exploration_replan(self, prompt: str) -> str:
        """One separate planning call, using the configured Judge API."""
        from .chat.providers import OpenAICompatibleProvider
        import threading as _threading
        chunks = OpenAICompatibleProvider().stream(
            [{"role": "system", "content": "你是 Agent 重新规划助手，只输出可直接执行的中文规划指令。"},
             {"role": "user", "content": prompt}],
            self._exploration_provider_config(), _threading.Event(),
        )
        return "".join(chunks).strip()

    def _on_exploration_judge_result(self, session_key: str, payload: dict) -> None:
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        payload = payload if isinstance(payload, dict) else {}
        generation = str(payload.get("generation_id") or "")
        if generation and self._exploration_active_generation.get(session_key) != generation:
            return
        if payload.get("lifecycle_epoch") != self._exploration_lifecycle_epoch.get(session_key, 0):
            return
        name = self._exploration_name(payload, session_key)
        judge = payload.get("judge") or {}
        verdict = str(judge.get("verdict", "UNKNOWN"))
        if verdict == "NORMAL":
            return
        if verdict == "UNKNOWN":
            if hasattr(self.win, "show_alert"):
                self._show_alert_compat(
                    self._dialogue("watchdog.unknown", f"{name} 检测到重复探索，判断服务暂时不可用，本次仅作提醒。", name=name),
                    duration_ms=self._EXPLORATION_REMINDER_MS, sticky=False,
                    alert_id=f"exploration-unknown:{session_key}", priority=2,
                    alert_type="watchdog-judge-fallback",
                )
            return
        reasons = self._format_exploration_reasons(payload.get("reasons", []), payload.get("steps", []))
        raw_reason = str(judge.get("reason") or "")[:240]
        reason = "Judge 暂未返回有效判断，采用保守建议" if "无效 JSON" in raw_reason else (raw_reason or "建议检查当前假设")
        text = self._dialogue(
            "watchdog.intervention", f"{name} 可能陷入重复排查\n重复表现：{reasons}\n判断原因：{reason}",
            name=name, reasons=reasons,
        )
        alert_id = f"exploration:{session_key}"
        self._exploration_alerts[session_key] = alert_id
        buttons = [
            ("不管", lambda s=session_key: self._continue_exploration(s)),
            ("自动优化", lambda s=session_key, p=payload: self._replan_exploration(s, p)),
            ("终止", lambda s=session_key: self._stop_exploration(s)),
        ]
        show_interactive = self._exploration_watchdog.mode != "auto" or verdict in ("ASK_USER", "STOP")
        if show_interactive:
            if hasattr(self.win, "show_alert"):
                self._show_alert_compat(text, subtitle=f"建议：{judge.get('next_action', '重新规划')}",
                                    buttons=buttons, sticky=True, alert_id=alert_id,
                                    priority=1, alert_type="watchdog-intervention",
                                    metadata={"sessionId": session_key, "riskScore": payload.get("risk", 0),
                                              "riskReasons": payload.get("reasons", []),
                                              "targetCount": payload.get("targetCount", 0),
                                              "targets": payload.get("targets", [])})
            elif hasattr(self.win, "show_bubble"):
                self.win.show_bubble(text, sticky=True, buttons=buttons)

        if self._exploration_watchdog.mode == "auto":
            if verdict == "REPLAN":
                self._replan_exploration(session_key, payload)
            elif verdict == "ASK_USER":
                self._stop_exploration(session_key, notify=False)
            elif verdict == "STOP":
                self._stop_exploration(session_key, notify=True)

    def _dismiss_exploration(self, session_key: str) -> None:
        self._exploration_active_generation.pop(session_key, None)
        alert_id = self._exploration_alerts.pop(session_key, "")
        if alert_id and hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(alert_id)

    # ------------------------------------------------------------------
    # 会话元数据缓存与显示名称解析
    # ------------------------------------------------------------------
    def _on_session_meta(self, agent_key: str, record: dict) -> None:
        """接收 bridge 发来的 session/meta 事件，写入元数据缓存。"""
        if not isinstance(record, dict):
            return
        session_id = str(record.get("sessionId") or "")
        if not session_id:
            return
        self._session_meta_cache[session_id] = {
            "label": str(record.get("label") or ""),
            "agentName": str(record.get("agentName") or ""),
        }
        log.debug("session_meta cached: %s → %s", session_id[:12], self._session_meta_cache[session_id])

    # ------------------------------------------------------------------
    # 429 限流提醒
    # ------------------------------------------------------------------
    # alert_id 带 sessionId：多 session 并发 429 时互不顶替。
    # show_alert 的 duration_ms 对 sticky 项无效，寿命由 _429_timer 自行管理。
    _429_COOLDOWN_S = 8.0          # 同 session 8 秒内合并为一次
    _429_DURATION_MS = 15000       # 基础展示 15 秒
    _429_MAX_LIFETIME_MS = 30000   # 同一 session 从首次触发起最长保留 30 秒
    _429_PRIORITY = 1              # 高于普通状态气泡和 Watchdog（3）；审批(0)可抢占

    @staticmethod
    def _429_alert_id(session_key: str) -> str:
        return f"429-rate-limit:{session_key}"

    def _on_rate_limit(self, agent_key: str, record: dict) -> None:
        """处理 429 限流事件：合并同 session 短时间内连续报错，弹窗提醒。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if not isinstance(record, dict):
            return
        session_key = str(record.get("sessionId") or agent_key)
        now = self._clock()
        cache = self._429_cache
        existing = cache.get(session_key)
        if existing and now - existing.get("_ts", 0) < self._429_COOLDOWN_S:
            # 合并：更新计数与时间，不重复弹窗；刷新 15 秒倒计时
            existing["count"] = existing.get("count", 1) + 1
            existing["_ts"] = now
            existing["_dismissed"] = False
            self._show_429_alert(session_key, existing["count"])
            return
        cache[session_key] = {
            "count": 1,
            "_ts": now,
            "_first_ts": now,
            "_dismissed": False,
        }
        self._show_429_alert(session_key, 1)

    def _show_429_alert(self, session_key: str, count: int) -> None:
        """展示 429 提醒弹窗，高优先级，带「知道了」按钮，15 秒自动收起。"""
        fallback = "DSH 请求受限（429），本轮未完成；请稍后重试。"
        key = "rate_limit.many" if count > 1 else "rate_limit.one"
        text = self._dialogue(key, fallback, count=count)
        buttons = [("知道了", lambda sk=session_key: self._dismiss_429_alert(sk))]
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(
                text,
                duration_ms=0,             # sticky 项忽略 duration，寿命由 timer 管理
                sticky=True,
                buttons=buttons,
                alert_id=self._429_alert_id(session_key),
                priority=self._429_PRIORITY,
                alert_type="rate_limit",
                metadata={"sessionId": session_key},
            )
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._429_DURATION_MS)
        # 自动收起：默认 15s；如被合并刷新，则按「首次触发 + 30s」硬上限收敛。
        self._schedule_429_dismiss(session_key)

    def _schedule_429_dismiss(self, session_key: str) -> None:
        """排定 429 提醒的自动收起时间。

        优先按最近一次触发 + 15s；但不超过该 session 首次触发 + 30s 硬上限，
        避免合并刷新把弹窗无限续命。无 QTimer 环境（测试桩）时跳过。"""
        if not hasattr(self.win, "_bubble_busy_until"):
            return  # 测试桩无 QTimer 环境：跳过自动收起，由 dismiss 兜底
        entry = self._429_cache.get(session_key)
        if not entry:
            return
        now = self._clock()
        cap_remaining = self._429_MAX_LIFETIME_MS / 1000.0 - (now - entry.get("_first_ts", now))
        base_remaining = self._429_DURATION_MS / 1000.0 - (now - entry.get("_ts", now))
        delay_s = max(0.2, min(base_remaining, cap_remaining))
        self._cancel_429_timer(session_key)
        # win 可能是非 QObject 的测试桩：parent 传 None，定时器由本管理器持有生命周期
        parent = self.win if isinstance(self.win, QObject) else None
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda sk=session_key: self._dismiss_429_alert(sk))
        self._429_timers[session_key] = timer
        timer.start(int(delay_s * 1000))

    def _cancel_429_timer(self, session_key: str) -> None:
        timer = self._429_timers.pop(session_key, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            timer.deleteLater()

    def _dismiss_429_alert(self, session_key: str) -> None:
        """用户点击「知道了」或超时自动收起：清理缓存并关闭提醒。"""
        cache = self._429_cache
        entry = cache.pop(session_key, None)
        if entry:
            entry["_dismissed"] = True
        self._cancel_429_timer(session_key)
        alert_id = self._429_alert_id(session_key)
        if hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(alert_id)

    def get_session_display_name(self, session_id: str) -> str:
        """解析会话的人类可读显示名。

        降级链：cache.label → cache.agentName → 截短 sessionId → 完整 sessionId。
        控制请求（interrupt/replan）仍严格使用 sessionId，此处仅用于展示。
        """
        meta = self._session_meta_cache.get(session_id)
        if meta:
            label = str(meta.get("label") or "")
            if label:
                return label
            agent_name = str(meta.get("agentName") or "")
            if agent_name:
                return agent_name
        # 降级：截短 sessionId，避免暴露完整内部标识
        short_id = session_id[:8] if len(session_id) > 8 else session_id
        return f"DSH · {short_id}"

    def _exploration_name(self, payload: dict, session_key: str) -> str:
        """返回探索气泡中显示的会话名称，优先使用元数据缓存。"""
        # 优先从 payload 中已有的 agent_name 获取
        raw = str((payload or {}).get("agent_name") or
                  (payload or {}).get("agent_key") or "").strip()
        if raw and raw in self.AGENT_NAMES:
            name = self.AGENT_NAMES[raw]
            self._exploration_names[session_key] = name
            return name
        # 通过 sessionId 查找元数据缓存
        display = self.get_session_display_name(session_key)
        if display and display != f"DSH · {session_key[:8]}":
            self._exploration_names[session_key] = display
            return display
        # 最终回退：agent 名称或默认 DSH
        name = self.AGENT_NAMES.get(raw.lower(), raw) or "DSH"
        self._exploration_names[session_key] = name
        return name

    def _continue_exploration(self, session_key: str) -> None:
        """User explicitly allows the current exploration to continue."""
        self._exploration_watchdog.grant_grace(session_key)
        self._dismiss_exploration(session_key)

    @staticmethod
    def _format_exploration_reasons(reasons, steps=None) -> str:
        labels = {
            "W6 同类重复": "最近 6 步反复使用同类探索工具",
            "W10 同类重复": "最近 10 步反复使用同类探索工具",
            "W6 target 重复": "最近 6 步反复访问同一目标",
            "W10 target 重复": "最近 10 步反复访问同一目标",
            "W6 fingerprint 重复": "最近 6 步出现相同工具调用",
            "W10 fingerprint 重复": "最近 10 步出现相同工具调用",
            "W6 探索密集且 target 单一": "最近 6 步探索集中在少数目标",
            "W10 探索密集且无行动": "最近 10 步没有 Edit、Run 或 Test",
        }
        values = [labels.get(str(item), str(item)) for item in (reasons or [])
                  if "diversity" not in str(item) and "有 Edit" not in str(item)
                  and "target 重复" not in str(item)]
        targets = []
        evidence = []
        for step in (steps or []):
            if not isinstance(step, dict):
                continue
            targets.extend(str(item) for item in (step.get("targets") or []) if item)
            for detail in (step.get("events") or []):
                if isinstance(detail, dict):
                    status = str(detail.get("evidenceStatus") or "")
                    if status:
                        evidence.append(status)
        # Targets are already normalized by the watchdog; display only the
        # basename to keep the pet popup readable and avoid exposing paths.
        import ntpath
        counts = Counter(ntpath.basename(item.replace("/", "\\")) for item in targets)
        if len(counts) == 2 and sum(counts.values()) >= 3:
            pair = "、".join(f"{name} {count} 次" for name, count in counts.most_common())
            values.insert(0, f"最近步骤在两个目标之间反复切换：{pair}")
        elif len(counts) == 1 and next(iter(counts.values())) >= 3:
            name, count = next(iter(counts.items()))
            values.insert(0, f"最近步骤重复访问同一目标：{name} {count} 次")
        if evidence and all(item == "same" for item in evidence):
            values.append("读取结果与之前相同，未发现新的可比较证据")
        elif evidence and "new" in evidence:
            values.append("近期至少获得过新的结果证据")
        return "；".join(dict.fromkeys(values[:3])) or "探索目标和调用方式重复"

    def _exploration_control(self, operation: str, session_key: str, text: str = "",
                             *, goal: str = "", context: str = "", provider: str = "",
                             model: str = "") -> None:
        def worker():
            try:
                from . import dsh_control
                ok, detail = dsh_control.request(
                    operation, session_key, text, self._dsh_candidate_ports(),
                    goal=goal, context=context, provider=provider, model=model,
                    timeout=float(self.cfg.get("agent_link", {}).get("exploration_watchdog_judge_timeout", 8)) + 12,
                    alert_id=f"exploration:{session_key}",
                )
            except Exception:
                log.exception("DSH exploration control failed")
                ok, detail = False, "control-error"
            try:
                self._exploration_control_result.emit(session_key, operation, bool(ok), str(detail))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _on_exploration_control_result(self, session_key: str, operation: str,
                                       ok: bool, detail: str) -> None:
        """只在 DSH 确认控制请求到达后收起提醒；失败则保留按钮。"""
        if ok:
            if operation == "replan":
                self._exploration_watchdog.grant_grace(session_key)
            self._dismiss_exploration(session_key)
            if hasattr(self.win, "show_bubble"):
                name = self._exploration_names.get(session_key, "DSH")
                message = (self._dialogue("control.interrupt.success", "终止请求已回传给 DSH。", name=name)
                           if operation == "interrupt" else
                           self._dialogue("control.replan.success", "重新规划提示已发送给 DSH。", name=name))
                self.win.show_bubble(message, duration_ms=4000)
            return
        log.warning("DSH exploration control rejected/unavailable: %s", detail)
        if hasattr(self.win, "show_alert") and self.win.isVisible():
            alert_id = self._exploration_alerts.get(session_key, f"exploration:{session_key}")
            retry_buttons = ([
                ("重试终止", lambda s=session_key: self._stop_exploration(s)),
            ] if operation == "interrupt" else None)
            name = self._exploration_names.get(session_key, "DSH")
            self.win.show_alert(
                self._dialogue("control.failed", "控制请求暂未送达 DSH，当前按钮仍可继续操作。", name=name),
                subtitle="请检查 DSH 是否正在运行",
                buttons=retry_buttons, sticky=bool(retry_buttons), duration_ms=0 if retry_buttons else 5000,
                alert_id=f"{alert_id}:error",
            )

    def _replan_exploration(self, session_key: str, payload: dict) -> None:
        # 将当前检测批次原样交给 bridge。bridge 先暂停真实 Agent，再用
        # 指定模型做一次无工具诊断，并把生成的计划直接 steer 回 Agent。
        # 按钮点击后立即收起永久弹窗；等待 bridge/模型返回期间只显示短提示，
        # 避免用户误以为按钮没有生效。
        self._dismiss_exploration(session_key)
        if hasattr(self.win, "show_bubble") and self.win.isVisible():
            name = self._exploration_names.get(session_key, "DSH")
            self.win.show_bubble(self._dialogue("control.replan.pending", f"正在暂停 {name}，生成下一步规划…", name=name), duration_ms=5000)
        judge = payload.get("judge") or {}
        context = json.dumps({
            "judge": judge,
            "risk": payload.get("risk"),
            "reasons": payload.get("reasons", []),
            "steps": payload.get("steps", []),
        }, ensure_ascii=False)
        try:
            cfg = self._exploration_provider_config()
            provider = str(getattr(cfg, "provider", "") or "")
            model = str(getattr(cfg, "model", "") or "")
        except Exception:
            provider = model = ""
        self._exploration_control(
            "replan", session_key, "", goal=str(payload.get("goal", "")),
            context=context, provider=provider, model=model,
        )

    def _stop_exploration(self, session_key: str, notify: bool = True) -> None:
        # 终止是立即生效的 UI 操作；不要等待 bridge 回执才收起 sticky 弹窗。
        self._dismiss_exploration(session_key)
        if hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(f"exploration:{session_key}:error")
        self._exploration_control("interrupt", session_key)
        if notify and hasattr(self.win, "show_bubble"):
            name = self._exploration_names.get(session_key, "DSH")
            self.win.show_bubble(self._dialogue("control.interrupt.pending", f"已请求终止 {name} 当前执行。", name=name), duration_ms=5000)

    # ------------------------------------------------------------------
    # 硬失败（execution/failed）：DSH 已决定本轮不再继续，直接提醒
    # ------------------------------------------------------------------
    _FAIL_ANIM_KEYWORDS = ("失败", "冒烟", "晕", "倒下", "昏", "扑街", "求救", "哭了", "委屈")
    _FAIL_REMINDER_MS = 6000

    def _pick_fail_anim(self) -> str | None:
        """从当前角色动作池里按语义挑选「失败/冒烟」动画；缺素材静默跳过。"""
        acts = list(getattr(self.win, "cats", {}).get("acts", []) or [])
        for kw in self._FAIL_ANIM_KEYWORDS:
            for a in acts:
                if kw in a:
                    return a
        return None

    def _on_execution_failed(self, agent_key: str, payload: dict) -> None:
        """硬失败直接提醒：不经行为分析，播失败动画 + 气泡告知本轮运行失败。

        payload 来自 bridge 的 execution/failed（脱敏）：只含 source /
        retryExhausted / retries / errorCode，不带 400 错误正文。
        """
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_exec_failed", True):
            return
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        payload = payload if isinstance(payload, dict) else {}
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        # 若该 session 有活跃的 429 提醒，不再重复弹通用失败横幅（避免双重通知）
        session_key = str(payload.get("sessionId") or agent_key)
        active_429 = self._429_cache.get(session_key)
        if active_429 and not active_429.get("_dismissed") and \
                self._clock() - active_429.get("_ts", 0) < self._429_COOLDOWN_S:
            return
        # 失败动画（若角色素材有）；没有就保持当前动作，仅弹气泡
        anim = self._pick_fail_anim()
        if anim and hasattr(self.win, "request_link_anim"):
            self.win.request_link_anim(anim)
        source = str(payload.get("source") or "").strip()
        retry_exhausted = bool(payload.get("retryExhausted"))
        if retry_exhausted:
            text = self._dialogue("failure.retry", f"{name} 本轮运行失败——模型请求多次重试后仍未成功，需要检查或重新运行", name=name)
        elif source == "tool":
            text = self._dialogue("failure.tool", f"{name} 本轮运行失败——工具执行最终失败，需要检查或重新运行", name=name)
        else:
            text = self._dialogue("failure.generic", f"{name} 本轮运行失败，需要检查或重新运行", name=name)
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(text, duration_ms=self._FAIL_REMINDER_MS, sticky=False)
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._FAIL_REMINDER_MS)
