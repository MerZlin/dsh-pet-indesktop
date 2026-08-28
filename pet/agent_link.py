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
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

log = logging.getLogger("dsh-pet-standalone")

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
                if tool:
                    self.activity.emit(self.agent_key, tool)
                normalized = normalize_event_state(ev, st)
                if not normalized:
                    continue  # 不认识的事件类型：忽略，不误报为 working
                self.state_changed.emit(self.agent_key, normalized)
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

        只认含 cordis.yml 的目录（真实 profile 的标志）；~/.dsh/profiles 下
        可能混入 node_modules 等包管理器/误操作残留的杂项目录，把它们当实例
        安装会失败并触发整体回滚，必须过滤。目录不存在或无有效 profile 时
        回退 ["web"]（安装命令会自动创建该 profile）。"""
        profiles_dir = Path.home() / ".dsh" / "profiles"
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
        """一键安装桥接插件到所有已存在的 dsh profile（web/headless 等）。
        返回 (成功与否, 说明)。"""
        import shutil
        import subprocess

        plugin = cls.bundled_plugin_dir()
        if plugin is None:
            return False, "找不到内置桥接插件（integrations/dsh-pet-bridge）"
        dsh = shutil.which("dsh")
        if not dsh:
            return False, "找不到 dsh 命令（未安装 DeepSeek Harness 或不在 PATH）"

        profiles = cls._list_profiles()

        failed = []
        succeeded = []
        for profile in profiles:
            try:
                cmd = [dsh, "plugin", "--profile", profile, "install", str(plugin)]
                if dsh.lower().endswith((".cmd", ".bat")):
                    # cmd /c 需自行给带空格的参数加引号（subprocess 不会代劳）
                    cmd = ["cmd", "/c"] + [f'"{a}"' if " " in a else a for a in cmd]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, shell=False)
                if proc.returncode != 0:
                    raw_out = (proc.stderr or proc.stdout or "").strip()
                    summary = cls._summarize_install_error(raw_out)
                    failed.append(f"{profile}：{summary}")
                else:
                    succeeded.append(profile)
            except Exception as exc:
                summary = cls._summarize_install_error(str(exc))
                failed.append(f"{profile}：{summary}")
        if failed:
            # 事务式回滚：部分失败时把已成功的也卸掉，避免"UI 显示未开启但插件在写文件"
            for profile in succeeded:
                try:
                    cmd = [dsh, "plugin", "--profile", profile, "uninstall", cls.PLUGIN_NAME]
                    if dsh.lower().endswith((".cmd", ".bat")):
                        cmd = ["cmd", "/c"] + [f'"{a}"' if " " in a else a for a in cmd]  # 带空格参数需自行加引号
                    subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=False)
                except Exception:
                    pass
            return False, "部分实例安装失败（已自动回滚）——" + "；".join(failed)
        return True, f"桥接插件已安装到 {len(profiles)} 个 dsh 实例（{', '.join(profiles)}）"

    @classmethod
    def uninstall_bridge(cls) -> bool:
        """关闭联动时卸载桥接插件。返回是否全部成功（失败记日志）。"""
        import shutil
        import subprocess

        dsh = shutil.which("dsh")
        if not dsh:
            return True  # 没装 dsh 视为无残留
        ok = True
        for profile in cls._list_profiles():
            try:
                cmd = [dsh, "plugin", "--profile", profile, "uninstall", cls.PLUGIN_NAME]
                if dsh.lower().endswith((".cmd", ".bat")):
                    cmd = ["cmd", "/c"] + [f'"{a}"' if " " in a else a for a in cmd]  # 带空格参数需自行加引号
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=False)
                if proc.returncode != 0:
                    ok = False
                    log.warning("卸载 DSH 桥接插件失败(%s): %s", profile, (proc.stderr or proc.stdout)[-120:])
            except Exception as exc:
                ok = False
                log.warning("卸载 DSH 桥接插件失败(%s): %s", profile, exc)
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
# Agent 联动总调度管理器
# ----------------------------------------------------------------------

class AgentLinkManager(QObject):
    """多 Agent 联动总调度管理器。

    挂载于 PetWindow，持有 4 个 Agent 的监视器，并根据状态驱动桌宠动作与气泡。
    """

    install_finished = Signal(str, bool, str)  # (agent_key, ok, message)

    # 联动气泡展示名
    AGENT_NAMES = {"dsh": "DSH", "claude": "Claude Code", "cursor": "Cursor", "opencode": "OpenCode"}
    # 过程汇报：工具名 → 用户可读文案（白名单制，未知工具静默不弹，不泄露原始命令/路径）
    TOOL_LABELS = {
        "read": "正在读文件", "write": "正在写文件", "edit": "正在改代码",
        "notebookedit": "正在改代码", "bash": "正在跑命令", "shell": "正在跑命令",
        "grep": "正在搜索", "glob": "正在搜索", "search": "正在搜索",
        "webfetch": "正在查网页", "websearch": "正在查网页",
        "task": "正在派活给子代理", "todowrite": "正在列计划",
    }
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

        self.monitors: dict[str, BaseAgentMonitor] = {
            "dsh": DshMonitor("dsh", self.config_dir, self),
            "claude": ClaudeCodeMonitor("claude", self.config_dir, self),
            "cursor": CursorMonitor(self.config_dir, self),
            "opencode": OpenCodeMonitor(self.config_dir, self),
        }

        for mon in self.monitors.values():
            mon.state_changed.connect(self._on_agent_state)
            mon.activity.connect(self._on_agent_activity)
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

    def _install_dsh_worker(self) -> None:
        """后台线程：安装 DSH 桥接插件，完成后信号回主线程。"""
        ok, msg = DshMonitor.install_bridge()
        self.install_finished.emit("dsh", ok, msg)

    def _warn_if_agent_absent(self, agent_key: str) -> None:
        """开启了联动但本机没装对应 Agent 时给用户提示（不然勾了永远没反应）。"""
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

    def _on_install_finished(self, agent_key: str, ok: bool, msg: str) -> None:
        """安装完成：成功则正式开启联动，失败则提示。"""
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
                    self.win.show_bubble("正在安装 DSH 桥接插件…", duration_ms=4000)
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
                        self.win.show_bubble("Claude hooks 卸载未完全成功，可手动检查 ~/.claude/settings.json", duration_ms=6000)
            elif agent_key == "dsh":
                if self._other_instances_enabled("dsh"):
                    log.info("其他实例仍在使用 DSH 联动，保留桥接插件")
                elif not DshMonitor.uninstall_bridge():
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
        if hasattr(self.win, "_pending_link_anim"):
            self.win._pending_link_anim = None
        for key in list(self._done_pending):
            self._cancel_done_check(key)

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()

    def _on_agent_state(self, agent_key: str, state: str) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。"""
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
            self._maybe_notify_start(agent_key, prev_raw)
        elif state == "attention":
            # busy 后的 attention（如 Claude Stop=回合结束）由完成确认流程接管，
            # 避免「需要看一眼」和「完成通知」双气泡；独立出现的才立即提醒
            if prev_raw not in self._BUSY_STATES:
                self._show_link_bubble("主人，Agent 这边需要你看一眼～", important=True)
        elif state == "error":
            if prev_raw not in self._BUSY_STATES:
                self._show_link_bubble("Agent 执行好像遇到报错了…", important=True)
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
    _LINK_BREAK = ("轻快记录", "原地漂浮踏步")

    def _next_link_anim_rotation(self) -> str | None:
        """下一个联动动作：主动作严格交替；每第 3 次插播摸鱼（独立节奏）。"""
        acts = getattr(self.win, "cats", {}).get("acts", [])
        main = [a for a in self._LINK_MAIN if a in acts]
        brk = [a for a in self._LINK_BREAK if a in acts]
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
    # 只映射有独立桌面进程的 Agent；dsh/claude 跑在终端/浏览器里，误伤面太大不映射。
    AGENT_PROCESS_HINTS = {
        "opencode": ("opencode.exe",),
        "cursor": ("cursor.exe",),
    }

    def busy_agent_owns_process(self, process_name: str) -> bool:
        """前台进程是否属于「联动开启且正在忙」的 Agent。"""
        p = str(process_name or "").lower()
        if not p:
            return False
        agent_cfg = self.cfg.get("agent_link", {})
        for agent_key, procs in self.AGENT_PROCESS_HINTS.items():
            if p in procs and agent_cfg.get(agent_key) \
                    and self._last_raw.get(agent_key) in self._BUSY_STATES:
                return True
        return False

    # ------------------------------------------------------------------
    # 联动气泡（开始干活可选 / 任务完成通知）
    # ------------------------------------------------------------------
    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None) -> None:
        """开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
        低优先级：气泡位被占时直接丢弃。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_state", False):
            return
        if prev_raw in self._BUSY_STATES:
            return
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        self._show_link_bubble(f"{name} 开始干活啦～", important=False, duration_ms=3000)

    def _on_agent_activity(self, agent_key: str, tool: str) -> None:
        """过程汇报气泡（可选，默认关）：「DSH 正在读文件…」这类。
        白名单工具映射 + 三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_activity", False):
            return
        label = self.TOOL_LABELS.get(str(tool).strip().lower(), "")
        if not label:
            return
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
        self._show_link_bubble(f"{name} {label}…", important=False, duration_ms=2600)

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
            text = f"{name} 那边停了，结果怎么样要主人自己看一眼哦"
        else:
            text = f"{name} 干完活啦，去看看成果吧～"
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
        """联动气泡：不顶掉正在占用气泡位的重要气泡（主动识屏/attention 等）。
        普通气泡直接让路丢弃；重要气泡每 2.5s 重试至多 4 次（约 10s 窗口），
        仍被占才放弃——主动识屏长答复可能占位 15-20s，单次重试不够用。"""
        if not hasattr(self.win, "show_bubble"):
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
