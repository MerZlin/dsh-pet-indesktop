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

    def reset(self) -> None:
        self.offset = 0
        self._initial_backfill_done = False
        self._partial = b""
        self._discard_until_newline = False

    def read_new_lines(self) -> list[str]:
        """读取文件自上次 offset 以来的全部完整新增行。

        半行处理：若读取末尾不是换行符（行被 chunk 截断或写入方尚未写完），
        未完成部分存入 _partial，下次读取时拼回——绝不把半行当整行解析。"""
        if not self.file_path.is_file():
            return []

        try:
            size = self.file_path.stat().st_size
        except OSError:
            return []

        # 启动时的首次初始化：若未指定 offset 则跳至当前末尾（backfill 防护）
        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = size
            self._partial = b""
            return []

        # 文件被截断或轮转
        if size < self.offset:
            self.offset = 0
            self._partial = b""

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

        profiles_dir = Path.home() / ".dsh" / "profiles"
        profiles = (
            sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())
            if profiles_dir.is_dir() else ["web"]
        )
        if not profiles:
            profiles = ["web"]

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
                    failed.append(f"{profile}: {(proc.stderr or proc.stdout)[-120:]}")
                else:
                    succeeded.append(profile)
            except Exception as exc:
                failed.append(f"{profile}: {exc}")
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
        profiles_dir = Path.home() / ".dsh" / "profiles"
        profiles = (
            sorted(p.name for p in profiles_dir.iterdir() if p.is_dir())
            if profiles_dir.is_dir() else ["web"]
        )
        ok = True
        for profile in profiles or ["web"]:
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
            # 注意：以下为普通字符串（非 f-string），{0}/{1} 是 PowerShell -f 的占位符。
            events_file.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "param([string]$EventName = 'unknown')\n"
                "$file = Join-Path $PSScriptRoot 'claude.jsonl'\n"
                "$ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0\n"
                '$line = \'{{"ts":{0},"agent":"claude","event":"{1}"}}\' -f $ts, $EventName\n'
                "Add-Content -Path $file -Value $line -Encoding UTF8\n",
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
                "out = Path(__file__).with_name('claude.jsonl')\n"
                "with out.open('a', encoding='utf-8') as f:\n"
                "    f.write(json.dumps({'ts': time.time(), 'agent': 'claude', 'event': event}) + '\\n')\n",
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
    def install_hooks(cls, events_file: Path) -> bool:
        """注入 Claude Code 官方 hooks（数组对象格式），事件追加到 jsonl。
        只移除/新增带本桌宠标记的条目，用户已有 hooks 不受影响。"""
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
                        if not (isinstance(g, dict) and cls.HOOK_MARKER in json.dumps(g))
                    ]
                else:
                    hooks[hook_name] = []
                cmd = cls._build_command(cmd_tmpl, script, hook_name)
                hooks[hook_name].append({
                    "matcher": "",
                    "hooks": [{"type": "command", "command": cmd}],
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
                        if not (isinstance(g, dict) and cls.HOOK_MARKER in json.dumps(g))
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
        # 目录发现降频：避免每 1.5s 在主线程递归 glob 整个 projects 目录
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
            finally:
                db.close()
        except Exception as exc:
            log.debug("OpenCode sqlite 读取异常: %s", exc)
            return

        for rowid, ev_type, data_raw in rows:
            self._last_rowid = max(self._last_rowid, int(rowid))
            state = opencode_event_state(str(ev_type), str(data_raw))
            if state:
                self.state_changed.emit("opencode", state)


# ----------------------------------------------------------------------
# Agent 联动总调度管理器
# ----------------------------------------------------------------------

class AgentLinkManager(QObject):
    """多 Agent 联动总调度管理器。
    
    挂载于 PetWindow，持有 5 个 Agent 的监视器，并根据状态驱动桌宠动作与气泡。
    """

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

        self.monitors: dict[str, BaseAgentMonitor] = {
            "dsh": DshMonitor("dsh", self.config_dir, self),
            "claude": ClaudeCodeMonitor("claude", self.config_dir, self),
            "cursor": CursorMonitor(self.config_dir, self),
            "opencode": OpenCodeMonitor(self.config_dir, self),
        }

        for mon in self.monitors.values():
            mon.state_changed.connect(self._on_agent_state)

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
                ok, msg = DshMonitor.install_bridge()
                if not ok:
                    QMessageBox.warning(
                        self.win if hasattr(self.win, "winId") else None,
                        "开启 DSH 联动",
                        f"桥接插件安装失败，联动未开启。\n{msg}",
                    )
                    return False
        else:
            # 关闭联动时移除我们注入的内容（只删自己的，用户自有配置不碰）；
            # 其他多开实例仍在使用则保留（hooks/插件是全局状态）
            if agent_key == "claude":
                if self._other_instances_enabled("claude"):
                    log.info("其他实例仍在使用 Claude 联动，保留 hooks")
                elif not ClaudeCodeMonitor.uninstall_hooks():
                    log.warning("Claude hooks 卸载未完全成功（配置已关闭，hooks 可能残留）")
            elif agent_key == "dsh":
                if self._other_instances_enabled("dsh"):
                    log.info("其他实例仍在使用 DSH 联动，保留桥接插件")
                elif not DshMonitor.uninstall_bridge():
                    log.warning("DSH 桥接插件卸载未完全成功（配置已关闭，插件可能残留）")

        ag_cfg = dict(self.cfg.get("agent_link", {}))
        ag_cfg[agent_key] = bool(enabled)
        self.cfg.set("agent_link", ag_cfg)
        self.cfg.save()
        self.apply_config()
        return True

    def pause(self) -> None:
        """桌宠隐藏时暂停所有监视器。"""
        for mon in self.monitors.values():
            mon.pause()

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()

    def _on_agent_state(self, agent_key: str, state: str) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return

        # 去抖：同一 Agent 连续相同状态只生效第一次
        now = self._clock()
        last = self._last_applied.get(agent_key)
        if last is not None and last[0] == state:
            return
        # 节流：同一 Agent 两次动作/气泡切换最小间隔
        if last is not None and (now - last[1]) < self._min_interval:
            return
        self._last_applied[agent_key] = (state, now)

        log.debug("Agent 状态变更 [%s]: %s", agent_key, state)

        # 状态 -> 桌宠行为映射（手册 §8.2）
        if state == "thinking":
            # 切换到写代码或深度思考动作
            target_anim = None
            for anim_name in ("写代码", "深度思考碎碎念"):
                if anim_name in getattr(self.win, "cats", {}).get("acts", []):
                    target_anim = anim_name
                    break
            if target_anim and hasattr(self.win, "_switch"):
                self.win._switch(target_anim)
        elif state == "working":
            # 敲击桌面互动
            target_anim = None
            for anim_name in ("原地敲击桌面互动", "轻快记录"):
                if anim_name in getattr(self.win, "cats", {}).get("acts", []):
                    target_anim = anim_name
                    break
            if target_anim and hasattr(self.win, "_switch"):
                self.win._switch(target_anim)
        elif state == "attention":
            if hasattr(self.win, "show_bubble"):
                self.win.show_bubble("主人，Agent 这边需要你看一眼～", duration_ms=4500)
        elif state == "error":
            if hasattr(self.win, "show_bubble"):
                self.win.show_bubble("Agent 执行好像遇到报错了…", duration_ms=4500)
        elif state in ("sleeping", "idle"):
            # 回到待机
            if hasattr(self.win, "idles") and hasattr(self.win, "_pick") and self.win.idles:
                self.win._switch(self.win._pick(self.win.idles))
