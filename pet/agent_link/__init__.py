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

import errno
import fnmatch
import json
import logging
import os
import re
import shutil  # compatibility namespace for existing integrations/tests
import subprocess
import sys
import threading
import time
import weakref
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable
from urllib.parse import unquote

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from . import cost as agent_cost_mod
from .. import harness_launcher
from ..click_sound import play_sound, resolve_builtin_sound
from ..agent_event_protocol import parse_agent_event
from ..agent_event_normalizer import normalize_event
from .dsh_state import (
    SCOPE_REGISTRY_MAX,
    DshState,
    DshStateConverger,
    TurnCompletionTracker,
    candidate_ports as _dsh_state_candidate_ports,
    fold_writer_states,
    map_event_to_state,
)
from ..model_access_tracker import ModelAccessTracker
from ..node_runtime import augmented_path as _augmented_path
from ..node_runtime import global_node_modules_roots

from ..persona_phrases import PhrasePicker
from ..persona_template import CONDITIONAL_PARAMETERS, LEGACY_HIDDEN_PARAMETERS
from ..speech_bubble_text import truncate_bubble_text

log = logging.getLogger("dsh-pet-standalone")

_LIVE_AGENT_LINK_MANAGERS: weakref.WeakSet = weakref.WeakSet()
_LIVE_AGENT_MONITORS: weakref.WeakSet = weakref.WeakSet()

try:  # 运行时依赖（PySide6 恒带 shiboken6）；缺失时按"存活"降级，不改变行为
    import shiboken6 as _shiboken
except Exception:  # pragma: no cover - 安装破损时的降级路径
    _shiboken = None


def _cpp_object_alive(obj) -> bool:
    """该 QObject 的 C++ 属主是否仍然存在（无法判定时按存活处理）。

    worker 线程的 ``signal.emit`` 在 C++ 属主已析构后抛
    ``RuntimeError: Signal source has been deleted``——这是**真实生命周期边界**
    的竞态（父链析构正好落在 worker 首轮发射窗口内），不是产品逻辑错误；
    发射边界据此判定"已失效"，退役本代次而不是把异常冒泡成后台未捕获线程异常。
    """
    if _shiboken is None:
        return True
    try:
        return bool(_shiboken.isValid(obj))
    except Exception:
        return True

# DSH 桥接事件名（_poll 按名字直通处理的；语义层/状态机未建模也计入），
# 供「未知事件 → 提醒更新/重装 bridge」识别：事件名在语义层（normalize_event）、
# 状态机（normalize_event_state）与本名单全部不命中才算未知。
# 新增 _poll 的 event 直通分支必须同步本名单，否则该事件会被误判为桥接未知事件。
_RAW_BRIDGE_KNOWN_EVENTS: frozenset[str] = frozenset({
    "approval/request", "approval/requested", "approval/decided", "approval/resolved",
    "question/requested", "question/resolved",
    "cordis/request-run", "cordis/request-run-resolved",
    "execution/failed", "model_access", "llm_error", "user_action",
    # 桥合法发出、语义层/状态机未建模的事件,漏登记会被误判成「未知桥接事件 →
    # 提醒更新/重装 bridge」(10 分钟冷却 → 表现为偶发未知弹窗):
    # - user/message:扁平记录(无 type/source),真人消息=对话开始,误判最扰民;
    # - bridge/diagnostic:桥进程启动时写一次;
    # - command/done:command/run 语义层认识而 done 漏了;
    # - pet/control-clicked / bridge/control-received:pet 控制回显。
    "user/message",
    "bridge/diagnostic",
    "command/done",
    "pet/control-clicked",
    "bridge/control-received",
    # - tool-workflow/run-end：桥接 STATE_EVENT_TYPES 直写，与已登记的
    #   tool-workflow/run-start 成对（语义层只认识 run-start）；
    # - web_search_begin / web_search_end / context_compacted / pet/control-queued /
    #   pet/control-clicked / bridge/control-received：旧版桥接（看门狗/控制队列
    #   时代）可能仍在写；减法后新版桥不再发这些事件，但已安装的旧桥升级前
    #   不能因此被误判成「未知事件」而弹更新提醒。
    "tool-workflow/run-end",
    "web_search_begin",
    "web_search_end",
    "context_compacted",
    "pet/control-queued",
})


def _cordis_requires_approval(data: dict) -> bool:
    """cordis 审批门禁：requiresApproval 是否严格布尔 True。

    桥接写盘把原始 cordis request 整体嵌在 payload 下（index.js：
    ``writeRecord({event: "cordis/request-run", ..., payload: request, requestId})``），
    顶层只有 requestId/agentId/sessionId 等身份字段；旧版桥与手写桩则可能把
    字段平铺在顶层。两处都认，payload 内存在该键时以它为准（避免嵌套 False
    被顶层残留 True 顶掉）。
    """
    nested = data.get("payload")
    if isinstance(nested, dict) and "requiresApproval" in nested:
        return nested.get("requiresApproval") is True
    return data.get("requiresApproval") is True


def _which(name: str) -> str | None:
    """Node runtime lookup with the historical ``shutil.which`` seam retained."""
    try:
        return shutil.which(name, path=_augmented_path())
    except TypeError:  # compatibility with tests/integrations patching which(name)
        return shutil.which(name)

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
#
# pnpm 入口的定位见 _find_pnpm_cli：npm 全局安装、nvm / nvm-windows 版本目录、
# pnpm ≤10 的 pnpm.cjs、包装脚本、独立 pnpm.exe 都要认（issue：桌宠找不到 pnpm）。

DSH_PLUGIN_NAME = "@dsh-pet/bridge"
DSH_PROFILE_HOME = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))

# Windows 探测/安装子进程隐藏窗口（与 harness_launcher 同款）：桌宠是无控制台
# 的 GUI 进程，node/cmd 子进程不隐藏会弹出可见终端窗口。
_HIDDEN_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


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


# pnpm / npm 的 JS 入口在包内的相对路径：不同版本/安装方式各不相同
# （pnpm 10 及以前是 bin/pnpm.cjs，pnpm 11 起是 bin/pnpm.mjs）。
_JS_CLI_NAMES: dict[str, tuple[str, ...]] = {
    "pnpm": ("pnpm.mjs", "pnpm.cjs", "pnpm.js"),
    "npm": ("npm-cli.js", "npm-cli.cjs", "npm-cli.mjs"),
}
_JS_CLI_SUFFIXES = {".js", ".cjs", ".mjs"}
# 包管理器生成的包装脚本（Windows 的 .cmd/.ps1、POSIX 的无扩展名 shell 脚本）
_SHIM_NAMES: dict[str, tuple[str, ...]] = {
    "pnpm": ("pnpm.cmd", "pnpm.exe", "pnpm.bat", "pnpm.ps1", "pnpm"),
    "npm": ("npm.cmd", "npm.exe", "npm.bat", "npm.ps1", "npm"),
}
_PNPM_MISSING_HINT = (
    "需要 pnpm，自动安装失败。可手动运行 npm install -g pnpm，"
    "或在配置里设置 pnpm_bin（也可用环境变量 DSH_PNPM_BIN）指定 pnpm 的"
    "可执行文件 / 目录 / JS 入口路径"
)

# 配置里的手动指定（config 键 pnpm_bin）：属于"环境特殊又不想改环境变量"的兜底。
# 优先级：config.pnpm_bin → DSH_PNPM_BIN → 内置自动发现（见 _find_pnpm_cli）。
# config 是应用自己的持久偏好，所以排在环境变量之前；空值表示未配置。
_configured_pnpm_bin = ""


def set_configured_pnpm_bin(value: str | None) -> None:
    """设置手动指定的 pnpm 入口（config.pnpm_bin）；空值 = 回到自动发现。"""
    global _configured_pnpm_bin
    _configured_pnpm_bin = str(value or "").strip()


def configured_pnpm_bin() -> str:
    """当前生效的手动指定值（空串 = 未配置）。"""
    return _configured_pnpm_bin


def _is_js_cli(path: Path) -> bool:
    return path.suffix.lower() in _JS_CLI_SUFFIXES


def _is_direct_cli(path: Path) -> bool:
    """能脱离 node 直接执行的入口（独立 pnpm.exe、.cmd/.ps1、POSIX shell 脚本）。"""
    suffix = path.suffix.lower()
    return not suffix or suffix in {".exe", ".cmd", ".bat", ".ps1"}


def _dedupe_paths(paths) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _js_cli_candidates(root: Path, package: str) -> list[Path]:
    """某个根目录下 JS 入口的所有已知落点。

    root 可能是 node 安装根（`.../v18.20.5`、`.../v18.20.5/bin`、nodejs 安装目录），
    也可能是全局 `node_modules` 根——两种都要覆盖，POSIX（nvm/lib/node_modules）
    与 Windows（nvm-windows/AppData）布局才都不会漏。
    """
    names = _JS_CLI_NAMES.get(package, ())
    subdirs = (
        ("node_modules", package, "bin"),
        ("lib", "node_modules", package, "bin"),
        ("node_modules", package, "dist"),
        ("lib", "node_modules", package, "dist"),
        (package, "bin"),
        (package, "dist"),
    )
    return [root.joinpath(*sub, name) for sub in subdirs for name in names]


def _shim_target(shim: Path, package: str) -> Path | None:
    """从包装脚本正文里抠出真正的 JS 入口。

    npm/pnpm 生成的 `.cmd`/`.ps1`/shell 包装都写着真实入口的相对路径
    （`"%dp0%\\node_modules\\pnpm\\bin\\pnpm.mjs"`、`$basedir/../lib/...`），
    按固定布局硬猜会漏（issue：nvm 用户只能改源码）。
    """
    try:
        text = shim.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    pattern = re.compile(
        r"""["']([^"'\r\n]*[/\\]%s(?:-cli)?\.(?:mjs|cjs|js))["']""" % re.escape(package)
    )
    for raw in pattern.findall(text):
        token = raw.strip()
        relative = False
        for prefix in ("%~dp0", "%dp0%", "$basedir", "${basedir}"):
            if token.lower().startswith(prefix.lower()):
                token = token[len(prefix):].lstrip("\\/")
                relative = True
                break
        if not token:
            continue
        candidate = Path(token)
        if relative or not candidate.is_absolute():
            candidate = shim.parent / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            pass
        if candidate.is_file():
            return candidate
    return None


def _resolve_cli_hint(hint: Path, package: str) -> Path | None:
    """把 DSH_PNPM_BIN 之类的提示解析成可用入口（文件 / 目录 / 包装脚本）。"""
    if hint.is_dir():
        for name in _SHIM_NAMES.get(package, ()):
            candidate = hint / name
            if candidate.is_file():
                target = _shim_target(candidate, package)
                if target:
                    return target
                if _is_js_cli(candidate) or _is_direct_cli(candidate):
                    return candidate
        for candidate in _js_cli_candidates(hint, package):
            if candidate.is_file():
                return candidate
        for name in _JS_CLI_NAMES.get(package, ()):
            candidate = hint / name
            if candidate.is_file():
                return candidate
        return None
    if not hint.is_file():
        return None
    if _is_js_cli(hint):
        return hint
    target = _shim_target(hint, package)
    if target:
        return target
    return hint if _is_direct_cli(hint) else None


def _package_roots() -> list[Path]:
    """pnpm / npm / dsh 全局包可能落脚的根目录（版本管理器 + 包管理器）。"""
    roots: list[Path] = []
    for name in ("pnpm", "npm", "node"):
        found = _which(name)
        if not found:
            continue
        path = Path(found)
        roots.extend([path, path.parent, path.parent.parent])
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        roots.extend([resolved, resolved.parent, resolved.parent.parent])
    roots.extend(global_node_modules_roots())
    for var in ("PNPM_HOME", "NVM_SYMLINK", "NVM_HOME", "NVM_DIR", "VOLTA_HOME"):
        value = (os.environ.get(var) or "").strip()
        if not value:
            continue
        root = Path(value)
        roots.extend([root, root / "bin"])
        if var in {"NVM_HOME", "NVM_DIR"}:
            try:
                roots.extend(sorted(p for p in root.glob("v*") if p.is_dir()))
            except OSError:
                pass
    return _dedupe_paths(roots)


def _find_pnpm_cli() -> str | None:
    """定位 pnpm 的 JS CLI 入口，不触发安装。

    覆盖真实世界里互相打架的多种安装方式（issue：只会一种布局就全漏）：
    1. config 里手动指定的 `pnpm_bin`（文件 / 目录 / 包装脚本）——优先级最高；
    2. `DSH_PNPM_BIN` 环境变量（同样的语义，给不想改配置的人）；
    3. PATH 上的 pnpm（包装脚本解析出真实 JS 入口；POSIX 软链解析到 .cjs）；
    4. 各版本管理器 / 包管理器根目录下的 `node_modules|lib/node_modules/pnpm/bin/pnpm.{mjs,cjs,js}`；
    5. 独立安装的 `pnpm.exe`（无需 node，直接执行）。

    手动指定**失效**（路径不存在 / 解析不出来）时只记警告并回落到自动发现——
    配错一个路径不该让桥接彻底装不上。
    """
    for source, hint in (
        ("config.pnpm_bin", _configured_pnpm_bin),
        ("DSH_PNPM_BIN", os.environ.get("DSH_PNPM_BIN") or ""),
    ):
        hint = (hint or "").strip()
        if not hint:
            continue
        found = _resolve_cli_hint(Path(hint).expanduser(), "pnpm")
        if found is not None:
            log.info("pnpm 入口取自 %s: %s", source, found)
            return str(found)
        log.warning("%s 指向的 pnpm 不可用，回落到自动发现: %s", source, hint)

    roots: list[Path] = []
    direct_fallbacks: list[Path] = []
    for name in _SHIM_NAMES["pnpm"]:
        shim = _which(name)
        if not shim:
            continue
        path = Path(shim)
        if _is_js_cli(path) and path.is_file():
            return str(path)
        target = _shim_target(path, "pnpm")
        if target:
            return str(target)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved != path and _is_js_cli(resolved) and resolved.is_file():
            return str(resolved)
        if path.is_file():
            if path.suffix.lower() == ".exe":
                return str(path)  # 独立安装的 pnpm.exe：自带运行时，不该再拼 node
            # .cmd/.bat 包装：优先找出它背后真正的 JS 入口（cmd 中转会把含空格
            # 的参数拆碎），只在实在找不到时兜底直接调它。
            direct_fallbacks.append(path)
        roots.extend([path.parent, path.parent.parent, resolved.parent, resolved.parent.parent])
    roots.extend(_package_roots())

    for root in _dedupe_paths(roots):
        for candidate in _js_cli_candidates(root, "pnpm"):
            if candidate.is_file():
                return str(candidate)
    if direct_fallbacks:
        return str(direct_fallbacks[0])
    for root in _dedupe_paths(roots):
        for name in _SHIM_NAMES["pnpm"]:
            candidate = root / name
            if candidate.is_file() and _is_direct_cli(candidate):
                return str(candidate)
    return None


def _npm_cli() -> str | None:
    """定位 npm 的 JS CLI 入口（由 node 直调，绕开 .cmd 的空格引号坑）。"""
    roots: list[Path] = []
    npm = _which("npm")
    if npm:
        path = Path(npm)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved.name in _JS_CLI_NAMES["npm"] and resolved.is_file():
            return str(resolved)
        target = _shim_target(path, "npm")
        if target:
            return str(target)
        roots.extend([path.parent, path.parent.parent, resolved.parent])
    roots.extend(_package_roots())
    for root in _dedupe_paths(roots):
        for candidate in _js_cli_candidates(root, "npm"):
            if candidate.is_file():
                return str(candidate)
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
            **_HIDDEN_KWARGS,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return _find_pnpm_cli()


def _pnpm_command() -> list[str] | None:
    """pnpm 的可执行命令前缀（JS 入口经 node 直调；独立可执行直接跑）。

    Windows 上 `.cmd/.bat` 必须经 cmd 启动（与 harness_launcher._wrap_cmd 同款
    实测结论）；`.exe`（pnpm 独立安装）自带运行时，不能再拼 node。
    """
    cli = _pnpm_cli()
    if not cli:
        return None
    path = Path(cli)
    if _is_js_cli(path):
        node = _which("node")
        return [node, str(path)] if node else None
    suffix = path.suffix.lower()
    if suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", str(path)]
    if suffix == ".ps1":
        return [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(path),
        ]
    return [str(path)]


def _run_pnpm(profile_dir: Path, *args: str) -> tuple[int, str]:
    """node 直调 pnpm CLI（数组传参，无 cmd 中转），返回 (返回码, 合并输出)。"""
    command = _pnpm_command()
    if command is None:
        if _which("node") is None:
            return 127, "找不到 node，请先安装 Node.js"
        return 127, _PNPM_MISSING_HINT
    try:
        proc = subprocess.run(
            [*command, *args], capture_output=True, text=True,
            timeout=300, shell=False, cwd=str(profile_dir),
            env={**os.environ, "PATH": _augmented_path()},
            **_HIDDEN_KWARGS,
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


# dsh-app-boot initProfile 的等价产物（见该包 lib/index.js）：新装 dsh 从未
# 运行时没有任何 profile，全新用户第一次开联动会被「没有可用的 dsh profile」
# 挡住——安装桥接前先按同一套三件套补出默认 web profile。
_WEB_PROFILE_BUNDLES = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]
_PROFILE_PATCH_TEMPLATE = (
    "# Your patch layer for this dsh profile, applied after every bundle layer:\n"
    "# a top-level YAML array of loader patch entries (id-targeted config\n"
    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n"
    "[]\n"
)
_PROFILE_PNPM_WORKSPACE = "packages:\n  - .\n\nnodeLinker: hoisted\n"


def _ensure_profile(profile_dir: Path) -> bool:
    """按 dsh initProfile 三件套补齐 profile（幂等：已有文件一律不动）。

    manifest（web 预设 bundles）+ cordis.patch.yml + pnpm-workspace.yaml。
    bundles 层与 dependencies 不同：只声明层列表，无需安装即可加 link: 依赖。
    """
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        manifest = profile_dir / "package.json"
        if not manifest.exists():
            manifest.write_text(json.dumps({
                "name": f"dsh-profile-{profile_dir.name}",
                "private": True,
                "dependencies": {},
                "dsh": {"profile": {"bundles": list(_WEB_PROFILE_BUNDLES),
                                     "patchReload": "live"}},
            }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        patch = profile_dir / "cordis.patch.yml"
        if not patch.exists():
            patch.write_text(_PROFILE_PATCH_TEMPLATE, encoding="utf-8")
        workspace = profile_dir / "pnpm-workspace.yaml"
        if not workspace.exists():
            workspace.write_text(_PROFILE_PNPM_WORKSPACE, encoding="utf-8")
        return True
    except OSError:
        log.exception("补齐 dsh profile 失败: %s", profile_dir)
        return False


def _prune_manifest_backups(profile_dir: Path, keep: int = 5) -> None:
    """package.json.bak-* 只保留最近 N 份（文件名含时间戳，按名排序即按时间）。

    备份是安全网、清旧是卫生——清理失败只记日志，绝不能反噬主流程。
    """
    try:
        backups = sorted(profile_dir.glob("package.json.bak-*"))
    except OSError:
        return
    for stale in backups[:-keep] if len(backups) > keep else []:
        try:
            stale.unlink()
        except OSError:
            log.debug("清理过期 manifest 备份失败: %s", stale)


def _uninstall_manifest_without_pnpm(profile_dir: Path, pkg: dict) -> dict | None:
    """没有 pnpm 时的纯 JSON 卸载：备份 → 删依赖条目 → 清 bundles → 写回。

    无 pnpm 不能直接报成功：manifest 里的 ``link:`` 条目还指着即将被删除的
    程序目录，dsh 启动解析失败会拖垮整个插件树（2026-09 事故同型）。返回写回
    后的 manifest；备份或写入失败返回 None（保留原文件，绝不半改）。
    """
    manifest = profile_dir / "package.json"
    backup = profile_dir / f"package.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        backup.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        log.exception("卸载桥接插件前备份失败，保留原 package.json: %s", profile_dir)
        return None
    _prune_manifest_backups(profile_dir)
    deps = pkg.get("dependencies")
    if isinstance(deps, dict):
        deps.pop(DSH_PLUGIN_NAME, None)
    bundles = ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles")
    if isinstance(bundles, list) and DSH_PLUGIN_NAME in bundles:
        bundles.remove(DSH_PLUGIN_NAME)
    try:
        manifest.write_text(
            json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        log.exception("卸载桥接插件写入失败: %s", profile_dir)
        return None
    return pkg


def _remove_linked_plugin_dir(profile_dir: Path) -> None:
    """清掉 profile/node_modules 下的插件链接；失败只记日志（尽力而为）。

    pnpm 的本地目录依赖在 Windows 上是 junction：``os.rmdir`` 只摘掉重解析点
    本身，不会递归删掉链接目标里的文件；符号链接走 unlink。真实目录不动。
    """
    link = profile_dir / "node_modules" / "@dsh-pet" / "bridge"
    try:
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            link.rmdir()
        elif link.exists():
            link.unlink()
    except OSError as exc:
        log.debug("清理桥接插件链接失败(%s): %s", profile_dir, exc)


# ---------------------------------------------------------------------------
# 依赖规格体检（issue：桥接装不上，报错只说 pnpm 失败，看不出是哪条依赖）
#
# profile 的 package.json 里可能有**指向本地磁盘**的依赖（`link:` / `file:` /
# 裸相对路径 / 绝对路径），而路径里往往嵌着会变的东西：打包构建目录名、
# 文件名里的版本号、本机绝对路径。目录改名或版本升级后 spec 就指向不存在的
# 路径，pnpm 解析失败。这里只做**诊断**：指名是哪条依赖、并给出"疑似应改为"
# 的候选路径；绝不自动改用户的 package.json。
# ---------------------------------------------------------------------------

_LOCAL_SPEC_PREFIXES = ("link:", "file:")
# 非本地规格：版本区间 / registry / git / 远端压缩包等，不存在"路径是否存在"的问题
_REMOTE_SPEC_PREFIXES = (
    "http:", "https:", "git:", "git+", "github:", "gitlab:", "bitbucket:",
    "workspace:", "npm:", "portal:", "patch:", "catalog:",
)
_PATHLIKE_SPEC = re.compile(r"^(?:\.{1,2}[\\/]|[\\/]{1,2}|[A-Za-z]:[\\/])")
_VERSION_TOKEN = re.compile(r"(\d+(?:\.\d+)*(?:-[0-9A-Za-z.]+)?)")
_WINDOWS_DRIVE_ABS = re.compile(r"^/[A-Za-z]:[\\/]")


def _path_spec_target(spec: str, profile_dir: Path) -> Path | None:
    """把依赖规格解析成本地路径；非路径型规格返回 None。

    pnpm 除了 `link:` / `file:`，也接受裸相对路径（`./x`、`../x`）与绝对路径，
    这些同样会在目标不存在时让安装失败，必须一起检查。
    """
    text = (spec or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith(_REMOTE_SPEC_PREFIXES):
        return None
    raw: str | None = None
    for prefix in _LOCAL_SPEC_PREFIXES:
        if lowered.startswith(prefix):
            raw = text[len(prefix):]
            break
    if raw is None:
        if not _PATHLIKE_SPEC.match(text):
            return None
        raw = text
    raw = unquote(raw.strip())
    if raw.lower().startswith("file://"):
        raw = raw[len("file://"):]
    raw = raw.strip()
    if not raw:
        return None
    if _WINDOWS_DRIVE_ABS.match(raw):  # file:///W:/x → W:/x
        raw = raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        path = profile_dir / path
    try:
        return path.resolve()
    except OSError:
        return path


def _version_key(name: str) -> tuple:
    """挑"更新的同类"时的排序键：按数字段比较，非版本名退化为名字序。"""
    numbers = [int(part) for part in re.findall(r"\d+", name)][:4]
    padded = tuple(numbers + [0] * (4 - len(numbers)))
    return (*padded, name)


def _newest(paths: list[Path]) -> Path:
    return max(paths, key=lambda p: _version_key(p.name))


def _bounded_children(folder: Path, cap: int = 200) -> list[Path]:
    """有界列举目录内容：依赖体检绝不能因为一个超大目录卡住启动路径。"""
    out: list[Path] = []
    try:
        for entry in folder.iterdir():
            out.append(entry)
            if len(out) >= cap:
                break
    except OSError:
        return []
    return out


def _safe_is_dir(path: Path) -> bool:
    """目录判定不得让权限/竞态错误逃逸。

    `Path.is_dir()` 只在路径**不存在**时返回 False；祖先目录没有搜索权限时会抛
    PermissionError（CI ubuntu 实测：tmp 落在 snap private /tmp 下直接 EACCES）。
    本模块的探测跑在「安装失败文案」与「启动自检」路径上——那里绝不允许抛异常。
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_mtime(path: Path) -> float:
    """mtime 排序键：stat 失败（EACCES/竞态）退化为 0.0，不值得让体检崩掉。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _suggest_path_replacement(
    missing: Path, *, max_ancestors: int = 5, scan_cap: int = 200,
) -> Path | None:
    """为一个不存在的路径找"疑似替代"。

    两类真实场景：
    1. 名字里带版本：`pkg-0.12.80.tgz` → 磁盘上是 `pkg-0.13.6.tgz`；
    2. 上层目录改名：`dist-onedir/<旧构建名>/.../dsh-pet-bridge` → 新构建名下的同一相对路径。
    只做有界扫描，且只给建议（绝不自动改写用户的 package.json）。

    全程用 `_safe_*` 探测：任何文件系统错误都退化为"没有建议"，绝不往外抛。
    """
    parent = missing.parent
    if _safe_is_dir(parent):
        match = _VERSION_TOKEN.search(missing.name)
        if match:
            prefix, suffix = missing.name[:match.start()], missing.name[match.end():]
            siblings = [
                entry for entry in _bounded_children(parent, scan_cap)
                if entry.name.startswith(prefix) and entry.name.endswith(suffix)
            ]
            if siblings:
                return _newest(siblings)

    parts = missing.parts
    for depth in range(1, min(max_ancestors, len(missing.parents) - 1) + 1):
        base = missing.parents[depth]
        if not _safe_is_dir(base):
            continue
        tail = parts[-depth:]
        matches = [
            candidate for entry in _bounded_children(base, scan_cap)
            if _safe_is_dir(entry)
            for candidate in [entry.joinpath(*tail)]
            if _safe_is_dir(candidate)
        ]
        if matches:
            return max(matches, key=_safe_mtime)
    return None


def _missing_dependency_specs(profile_dir: Path, pkg: dict) -> list[str]:
    """profile 里指向不存在路径的依赖（pnpm 会因此安装失败），一行一条。"""
    deps = pkg.get("dependencies") if isinstance(pkg, dict) else None
    if not isinstance(deps, dict):
        return []
    findings: list[str] = []
    for name, spec in deps.items():
        target = _path_spec_target(str(spec), profile_dir)
        if target is None or target.exists():
            continue
        line = f"{name} 指向不存在的路径：{spec}"
        suggestion = _suggest_path_replacement(target)
        if suggestion is not None:
            line += f"（疑似应改为 {suggestion}）"
        findings.append(line)
    return findings


def _dependency_spec_hint(profile_dir: Path, pkg: dict, *, limit: int = 2) -> str:
    """把缺失依赖压成一句可拼进报错的提示；没有问题时返回空串。"""
    findings = _missing_dependency_specs(profile_dir, pkg)
    if not findings:
        return ""
    shown = "；".join(findings[:limit])
    more = f"（另有 {len(findings) - limit} 条同类问题）" if len(findings) > limit else ""
    return f"；依赖路径缺失：{shown}{more}"


def _spec_with_replacement(spec: str, target: Path) -> str:
    """按原 spec 的写法生成修正后的 spec（保留 ``link:`` / ``file:`` 前缀）。"""
    text = str(spec).strip()
    lowered = text.lower()
    for prefix in _LOCAL_SPEC_PREFIXES:
        if lowered.startswith(prefix):
            return f"{text[:len(prefix)]}{target}"
    return str(target)


def _repair_missing_dependency_specs(profile_dir: Path, pkg: dict) -> list[str]:
    """把**能唯一确定**的坏依赖路径改写掉，返回改动说明；改前先备份 package.json。

    只在安装失败后的恢复动作里调用——用户此刻的意图就是"装上"，而坏 spec 是
    拦路石（web-rc8-test 现场：package.json 指着不存在的 0.12.80，磁盘上是 0.13.6）。
    找不到唯一候选的条目一律不动（宁可不修，不可乱改）；lockfile 不在这里碰，
    由重跑的 pnpm 自己重生成。
    """
    deps = pkg.get("dependencies") if isinstance(pkg, dict) else None
    if not isinstance(deps, dict):
        return []
    changes: list[tuple[str, str, str]] = []
    for name, spec in deps.items():
        target = _path_spec_target(str(spec), profile_dir)
        if target is None or target.exists():
            continue
        suggestion = _suggest_path_replacement(target)
        if suggestion is None:
            continue
        changes.append((name, str(spec), _spec_with_replacement(str(spec), suggestion)))
    if not changes:
        return []
    manifest = profile_dir / "package.json"
    backup = profile_dir / f"package.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        backup.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        log.exception("依赖路径修正前备份失败，放弃修正: %s", profile_dir)
        return []
    _prune_manifest_backups(profile_dir)
    for name, _old, new in changes:
        deps[name] = new
    try:
        manifest.write_text(
            json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        log.exception("依赖路径修正写入失败: %s", profile_dir)
        return []
    return [f"{name}: {old} → {new}" for name, old, new in changes]


def _run_pnpm_repairing_specs(profile_dir: Path, *args: str) -> tuple[int, str, list[str]]:
    """跑 pnpm；失败且存在可唯一修正的坏依赖路径时，修正后重试一次。

    返回 (返回码, 输出, 修正说明列表)。修正会备份 package.json，并由 pnpm
    在重试时重新生成 lockfile。
    """
    rc, out = _run_pnpm(profile_dir, *args)
    if rc == 0:
        return rc, out, []
    pkg = _read_manifest(profile_dir)
    if pkg is None:
        return rc, out, []
    repaired = _repair_missing_dependency_specs(profile_dir, pkg)
    if not repaired:
        return rc, out, []
    log.warning(
        "依赖路径已按探测结果修正并重试 pnpm %s: %s", " ".join(args), "；".join(repaired),
    )
    rc2, out2 = _run_pnpm(profile_dir, *args)
    return rc2, out2, repaired


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
      （reason="tool-calls" 表示模型停笔等工具结果，回合未完，忽略）
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
        if pt == "step-finish" and str(part.get("reason") or "") == "tool-calls":
            return ""
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


# 桥目录陈旧文件的清理口径（N1，2026-09-29）：
# - 超龄线 24h：写者只要还在写，mtime 就会变新，超龄即"写者已不活跃"；
# - 单拍最多体检/清理 8 个（从最旧端起步）：升级后首拍面对上百个历史死文件时
#   不砸盘，也避免为全部历史文件每拍各做一次进程探活（一次判定 = 3 次系统调用）；
#   目录 mtime 会因删除而变化 → 下一拍立刻重扫，几拍内自然清空；
# - pid 从桥文件名 ``dsh-<pid>.jsonl``（integrations/dsh-pet-bridge/index.js
#   的 INSTANCE_FILE）解析；旧版单实例 ``dsh.jsonl`` 无 pid 线索，一律保留。
_BRIDGE_STALE_FILE_AGE_S = 24 * 3600.0
_BRIDGE_STALE_CLEANUP_LIMIT = 8
_BRIDGE_INSTANCE_FILE_RE = re.compile(r"^dsh-(\d+)\.jsonl$")


#: 探活的错误分类口径（**只分类，绝不提权**）：
#: - Win ``OpenProcess`` 失败时 ``ERROR_INVALID_PARAMETER(87)`` = 没有这个 pid → 确定已死
#:   （同 ``slot_manager.pid_alive`` 的既有判定口径）；``ERROR_ACCESS_DENIED(5)`` =
#:   进程存在但探不动（受保护进程 / 别的用户 / 更高完整性级别 / 更早的会话）→ **判不了**；
#: - POSIX ``os.kill(pid, 0)`` 只有 ``ESRCH`` 才证明"没有这个 pid"；``EPERM``/``EACCES``
#:   说明进程存在但无权发信号 → 同样判不了。
#: 判不了就报判不了：把它判死会撤掉**活着**的写者的忙态与待处理交互（用户还在等的
#: 气泡消失）。为此不申请任何更高权限（不改令牌、不加 SeDebugPrivilege）。
_WIN_ERROR_ACCESS_DENIED = 5
_WIN_ERROR_INVALID_PARAMETER = 87
_WIN_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _os_kill_signal_0(pid: int) -> None:
    """POSIX 探活的 OS 边界（单独成函数 = 单测可分别注入 ESRCH / EPERM）。"""
    os.kill(pid, 0)


def _win32_kernel32():
    """Windows 探活的 OS 边界（单独成函数 = 单测可在任何平台注入假 kernel32）。"""
    import ctypes
    return ctypes.windll.kernel32


def _probe_writer_pid_posix(pid: int) -> bool | None:
    """POSIX 三态探活：只有 ``ESRCH``（没有这个 pid）与超出 pid 范围算确定已死。"""
    try:
        _os_kill_signal_0(int(pid))
        return True
    except OverflowError:
        # 超出 pid_t 表示范围的 pid 抛的不是 OSError：这样的 pid 不可能存在。
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        # EPERM/EACCES（进程存在但无权探它）与任何未知 errno：判不了，绝不判死。
        log.debug("桥文件写者探活不可判定 pid=%s errno=%s", pid, exc.errno)
        return None


def _probe_writer_pid_windows(pid: int) -> bool | None:
    """Windows 三态探活：只有"没有这个 pid"与"退出码已不是 STILL_ACTIVE"算确定已死。"""
    import ctypes

    kernel32 = _win32_kernel32()
    try:
        # 最小必要权限（只查、不改）：权限越大越容易在受保护进程上被拒绝。
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except OverflowError:
        # 超出 Win32 进程 ID 表示范围的 pid（ctypes 按 c_int 转换时溢出）：不可能存在。
        return False
    if not handle:
        err = int(kernel32.GetLastError())
        if err == _WIN_ERROR_INVALID_PARAMETER:
            return False        # 没有这个 pid：确定已死
        if err == _WIN_ERROR_ACCESS_DENIED:
            # 进程**存在**但我们查不了它（受保护进程/别的用户/更高完整性级别/更早的
            # 会话）：判不了存活之外的任何事。绝不为此提权（口径见上方常量注释）。
            log.debug("桥文件写者探活不可判定（拒绝访问）pid=%s", pid)
            return None
        # 其余错误码同样判不了。
        log.debug("桥文件写者探活不可判定 pid=%s winerr=%s", pid, err)
        return None
    try:
        # 句柄打得开 ≠ 进程还活着：进程被 TerminateProcess、父进程还握着句柄时，
        # 进程对象不销毁、OpenProcess 一路成功——只有退出码仍是 STILL_ACTIVE 才算活。
        # 退出码是 DWORD：Windows 上 C ``long`` 就是 32 位（``wintypes.DWORD`` 即
        # ``c_ulong``），这里直接用 c_ulong，省掉只在 Windows 存在的 ``ctypes.wintypes``
        # 导入（本函数的注入式单测要在 Linux/macOS 上跑）。
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            # 句柄在手却读不到退出码：异常情况，判不了存活，也不判死。
            log.debug("桥文件写者退出码读取失败 pid=%s", pid)
            return None
        return code.value == _WIN_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _bridge_writer_alive(pid: int) -> bool | None:
    """桥文件写者（``dsh-<pid>.jsonl``）是否还活着。

    True = 存活；False = **确定**已死；**None = 判定失败**（权限拒绝/平台探活异常），
    调用方按保守处理——不清理、不退役、照常轮询。

    为什么本模块自守这份三态、而不是复用 ``slot_manager.pid_alive``：后者的布尔语义
    被多处消费（本批**不动它**），而它的 bool 把"权限拒绝"与"没有这个 pid"压成同一个
    False。多实例场景下那是一次**误判死亡**：探不动活着的写者 → 撤掉它的忙态与待处理
    交互。分类细则见 :func:`_probe_writer_pid_windows` / :func:`_probe_writer_pid_posix`；
    陈旧文件清理（:meth:`DirGlobTailer._cleanup_stale_dead_writers`）与活跃写者探活
    （:meth:`DshMonitor._maybe_probe_dead_writers`）共用这一处判定。
    """
    if pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            return _probe_writer_pid_windows(pid)
        return _probe_writer_pid_posix(pid)
    except Exception:
        log.debug("桥文件写者探活失败 pid=%s", pid, exc_info=True)
        return None


# ----------------------------------------------------------------------
# 桥写者归属（多 DSH 实例分区）
# ----------------------------------------------------------------------
#: 桥接插件在唯一写盘出口（index.js ``writeRecord``）盖的**信封**字段：本次插件
#: 启动生成的 UUID。它是插件自己的信封，不是 DSH 字段——桌宠也不拿它冒充上游协议。
_BRIDGE_INSTANCE_FIELD = "inst"
#: 摄入侧把推出来的写者归属写回记录用的**内部标注**键（下划线前缀 = 我们自己的
#: 摄入标注，不进任何持久化、不进对话模板）。生命周期记录与 typed state 都靠它按
#: 写者过滤；缺失（直调/非摄入路径）时下游按"不知道归属"保守处理。
_BRIDGE_OWNER_KEY = "_scope"


def record_owner(record: dict, cursor: tuple | None = None) -> str | None:
    """这条桥接记录属于哪个写者（多 DSH 实例分区）；给不出归属时返回 None。

    - **新桥**（有插件 boot UUID ``inst``）→ ``inst:<uuid>``；
    - **旧桥**（无 ``inst``）→ ``file:<路径>:<文件身份>``，文件身份取摄入游标里的
      那个（Win: ino+ctime_ns / POSIX: dev+ino）。**不单用 pid**：pid 会被系统
      复用，同一路径、同一 pid 重启后的新写者是另一个写者；同一路径被轮转/替换
      （bridge 超限 rename）后文件身份也变——这正是游标里那个身份存在的理由；
    - 两者都没有（无游标：直调、非 tail 路径）→ None，调用方按"归属未知"处理。
    """
    if isinstance(record, dict):
        inst = str(record.get(_BRIDGE_INSTANCE_FIELD) or "").strip()
        if inst:
            return f"inst:{inst}"
    if cursor is None:
        return None
    try:
        path_key = str(cursor[0])
        file_id = cursor[1]
    except (IndexError, TypeError):
        return None
    return f"file:{path_key}:{file_id}"


def _evict_inactive_owner_files(owner_files: OrderedDict[str, str], active: set[str],
                                *, max_entries: int) -> list[str]:
    """把「写者 → 最近一次写盘文件」映射压回容量内，返回被淘汰的 owner（纯函数）。

    **只淘汰非活跃写者，从最老的开始**。为什么不能按插入次序 FIFO 直接淘汰最老的
    （旧实现）：这张表是活跃写者存活探活的**唯一** pid 来源
    （:meth:`DshMonitor._maybe_probe_dead_writers` 的候选必须"写者还在表里"），而
    插件每重载一次就是一个新 boot UUID = 一个新写者（被收敛器拒接状态分片的写者照样
    在这里登记映射）。FIFO 会把**正忙 / 正等用户输入**的写者的映射挤掉：那条写者的
    进程死掉后没有 pid 可探，忙态与待处理交互永远留在桌宠里（永远忙、气泡撤不掉）。

    淘汰是可逆的：被淘汰的写者下次写盘会重新登记。全部条目都活跃时**不淘汰**——
    宁可让表暂时超容量，也不丢活跃写者的探活线索；活跃写者数被收敛器的 scope 注册表
    上界钉死（``dsh_state.SCOPE_REGISTRY_MAX``），所以表的实际上界是
    ``max(max_entries, 活跃写者数)``。
    """
    evicted: list[str] = []
    if len(owner_files) <= max_entries:
        return evicted
    for key in [k for k in owner_files if k not in active]:
        if len(owner_files) <= max_entries:
            break
        owner_files.pop(key, None)
        evicted.append(key)
    return evicted


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
        self._last_directory_mtime_ns: int | None = None
        self._tailers: dict[str, ByteOffsetTailer] = {}
        self._initial_backfill_done: bool = False
        # 本拍被确认「写者已死且已清理」的文件路径（消费者据此退役对应写者 scope）
        self._confirmed_dead: set[str] = set()

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
        self._last_directory_mtime_ns = None
        for t in self._tailers.values():
            t.reset()
        self._tailers.clear()
        self._confirmed_dead.clear()

    def take_confirmed_dead(self) -> set[str]:
        """取出并清空「已被确认写者已死」的文件路径集合（退役 scope 用）。

        确认口径沿用 :meth:`_cleanup_stale_dead_writers` 的保守三连（超龄 + 文件名
        带 pid + 探活明确返回 False）；判定不了存活的**不进**这个集合——宁可让一个
        死写者的状态多留一拍，也不误退役活写者的 scope。
        """
        dead = self._confirmed_dead
        self._confirmed_dead = set()
        return dead

    def _scan(self, now: float) -> None:
        # F-PERF P3：scan_interval 此前只存不用——每拍全量枚举（最多 64 文件，
        # 每个都要 listdir+匹配）纯浪费。现在两级早退：
        # 1) 距上次扫描不足 scan_interval **且目录 mtime_ns 未变** → 直接返回
        #    缓存的前次结果（本轮不枚举、不新建/淘汰 tailer）；
        # 2) 其余情况（间隔到点 / 目录变了 / mtime 读不到）全量枚举。
        # 目录 mtime 是"文件增删"的即时信号：新文件写入当拍即可发现（既有
        # 语义 test_discovers_new_files_during_scan_throttle_and_keeps_offsets
        # 要求）；分辨粗/拿不到 mtime 的文件系统由上界 scan_interval 兜底，
        # 发现延迟不超过 scan_interval（默认 5s，不擅自加大）。
        if (now - self._last_scan) < self.scan_interval and not self._directory_changed():
            return
        self._last_scan = now
        try:
            if not self.directory.is_dir():
                self._last_directory_mtime_ns = None
                return
            # 与下面的目录枚举同一次 try：目录存在即顺手记下 mtime，供下一拍早退比对
            self._last_directory_mtime_ns = self.directory.stat().st_mtime_ns
            chosen = self._scan_candidates()
        except Exception:
            log.debug("桥目录扫描异常", exc_info=True)
            return
        candidates = {str(f) for f in chosen}
        for stale in [k for k in self._tailers if k not in candidates]:
            del self._tailers[stale]
        for fkey in candidates:
            if fkey not in self._tailers:
                t = ByteOffsetTailer(fkey)
                t._initial_backfill_done = getattr(self, "_cached_backfill_value", False)
                self._tailers[fkey] = t

    def _scan_candidates(self) -> list[Path]:
        """本轮该轮询的文件：按 **mtime 倒序** 取前 ``max_files``（活跃优先）。

        旧实现按文件名字典序取前 max_files——实机（2026-09-29）桥目录里积了
        172 个陈旧 ``dsh-*.jsonl``（6 个写者还活着，其中最新写入的文件写于 2.5
        小时前），它排在名字序第 74 位，被 64 名的预算挤出扫描集合：那个会话
        的状态事件桌宠一条都读不到（功能缺陷）。文件名的 pid 大小与"哪个会话
        还活着"无关，只有 mtime 能表达活跃度。

        顺手清掉「写者进程已死 **且** 超过 ``_BRIDGE_STALE_FILE_AGE_S`` 未写入」
        的陈旧文件，清理失败的留在磁盘上（只是不再轮询）。

        枚举用 ``os.scandir`` 而非 ``Path.glob``：Windows 上目录项自带 mtime，
        排序所需的每个文件时间戳零额外系统调用（本机 stat 病态昂贵，见类注释）。
        """
        entries: list[tuple[int, str, str]] = []
        with os.scandir(self.directory) as scan:
            for entry in scan:
                if not fnmatch.fnmatch(entry.name, self.pattern):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    mtime_ns = entry.stat().st_mtime_ns
                except OSError:
                    continue  # 枚举期被删/无权限：当拍忽略，下一拍再来
                entries.append((mtime_ns, entry.name, entry.path))
        entries.sort(key=lambda item: item[0])  # 升序：最旧在前（清理从最旧端起）
        dead = self._cleanup_stale_dead_writers(entries)
        if dead:
            # 交接给消费者（DshMonitor）：这些文件的写者已被确认死亡，归属它们的
            # 状态/锁存与常驻交互都要退役。只记录已确认的那部分，不在这里做消费
            # 侧的事（Tailer 不懂状态机）。
            self._confirmed_dead |= dead
        live = [path for _mtime_ns, _name, path in entries if path not in dead]
        live.reverse()                          # 翻回 mtime 倒序（活跃优先）
        return [Path(path) for path in live[: self.max_files]]

    def _cleanup_stale_dead_writers(self, entries: list[tuple[int, str, str]]) -> set[str]:
        """清理「写者 pid 已死且超龄」的桥文件，返回判定为死的路径集合。

        ``entries`` 按 mtime 升序（最旧在前）。单拍最多体检 ``_BRIDGE_STALE_CLEANUP_LIMIT``
        个，两个理由：① 升级后首拍面对上百个历史死文件时不砸盘（目录 mtime 变化
        会让下一拍立刻重扫，几拍内自然清空）；② 一次存活性判定是三次系统调用
        （OpenProcess + GetExitCodeProcess + CloseHandle），不能对全部历史文件
        每拍各来一次。从**最旧**端起步，被删的空位下一拍自然让位。

        保守三连（任一不成立就保留）：未超过 ``_BRIDGE_STALE_FILE_AGE_S``（活着的
        写者一写入 mtime 就变新，下一拍自动回到候选集）、文件名匹配不上
        ``dsh-<pid>.jsonl``（旧版单实例 ``dsh.jsonl`` 等无 pid 线索）、存活判定
        失败（``None``，见 :func:`_bridge_writer_alive`）——宁可多留一个死文件，
        绝不误删活会话正在写的桥文件。
        """
        now = time.time()
        dead: set[str] = set()
        probes = 0
        for mtime_ns, name, path in entries:
            if probes >= _BRIDGE_STALE_CLEANUP_LIMIT:
                break
            if (now - mtime_ns / 1e9) < _BRIDGE_STALE_FILE_AGE_S:
                break                    # 进入未超龄区间：更"新"的一律不必看
            match = _BRIDGE_INSTANCE_FILE_RE.match(name)
            if match is None:
                continue                 # 无 pid 线索：不探活、不清理
            probes += 1
            if _bridge_writer_alive(int(match.group(1))) is not False:
                continue                 # 活着 / 判定不了存活：一律保留
            dead.add(path)
            try:
                os.remove(path)
            except OSError:
                pass                     # 被写者打开/无权限：留在磁盘上，只是不轮询
        return dead

    def _directory_changed(self) -> bool:
        """目录 mtime_ns 是否变了（读不到 = 视为变了：宁可多扫一次不漏文件）。"""
        try:
            current = self.directory.stat().st_mtime_ns
        except OSError:
            return True
        return current != self._last_directory_mtime_ns

    def read_new_lines(self) -> list[str]:
        """读取本轮新增行（无游标的兼容出口）。"""
        return [line for line, _cursor in self.read_new_lines_with_cursors()]

    def read_new_lines_with_cursors(self) -> list[tuple[str, tuple]]:
        """读取本轮全部子文件的 (行, 读取游标)；游标语义见 :class:`ByteOffsetTailer`。

        多文件（多 DSH 实例分区写入）下同一个逻辑事件可能由两个写者各写一份：
        带源 ID 的事件由身份账本去重，无源 ID 的事件按各自文件的游标区分
        （不同的写者 = 不同的文件身份，不是同一次读取）。
        """
        # 扫描节流的时钟用 monotonic：它是纯间隔比较，墙上时钟被 NTP/手动改时
        # 不能把「多久没扫描」算歪（后拨会让新一轮扫描迟迟不来）。
        now = time.monotonic()
        self._scan(now)
        items: list[tuple[str, tuple]] = []
        for tailer in self._tailers.values():
            items.extend(tailer.read_new_lines_with_cursors())
        return items


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
        """读取文件自上次 offset 以来的全部完整新增行（无游标的兼容出口）。"""
        return [line for line, _cursor in self.read_new_lines_with_cursors()]

    def take_confirmed_dead(self) -> set[str]:
        """单文件 tail 没有"写者已死"的判定（那是 ``DirGlobTailer`` 的分区清理
        才有的事）：恒返回空集，让消费方可以无差别调用。"""
        return set()

    def read_new_lines_with_cursors(self) -> list[tuple[str, tuple]]:
        """读取新增完整行，每行带上**读取游标** ``(文件路径, 文件身份, 行首字节偏移)``。

        游标是摄入去重所需的**进程内身份**（G2 事件标识）：同一文件、同一文件
        身份、同一字节位置的字节被第二次投递（写者截断后把同一段内容写回、
        备份还原、copy-rotate）可据此判定为同一次读取；文件轮转/替换后
        ``file_id`` 变化，同一路径的 offset=0 **不会**与旧文件的 offset=0 碰撞。
        **这不是上游给的 ID**，只是我们自己的摄入口径——文档与日志都如实标注，
        不冒称"上游给了 ID"。

        半行处理：若读取末尾不是换行符（行被 chunk 截断或写入方尚未写完），
        未完成部分存入 _partial，下次读取时拼回——绝不把半行当整行解析。

        F-PERF P3：``is_file()`` 与紧随的 ``stat()`` 合并成一次 ``stat()``
        （``is_file`` 本身就是一次 stat + S_ISREG 判定）。这条轮询每拍对每个
        tail 文件各做两次系统调用，实机 py-spy 在 GUI 侧抓到 15.6% 时间耗在
        stat 上（本机 stat 病态昂贵）。语义不变：读不到 / 不是普通文件 → 按
        「文件不存在」返回空，绝不抛。
        """
        try:
            st = self.file_path.stat()
            if not S_ISREG(st.st_mode):
                return []
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
        read_start = self.offset          # 本次读的起点（文件内绝对偏移）
        try:
            with open(self.file_path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(bytes_to_read)
                self.offset = f.tell()
        except OSError as exc:
            log.warning("读取 tail 文件失败 %s: %s", self.file_path, exc)
            return []

        partial_len = len(self._partial)
        chunk = self._partial + chunk
        # chunk 首字节在文件中的绝对偏移：上次遗留的半行正好紧挨在本次读的起点之前
        chunk_start = read_start - partial_len

        # 超长行丢弃模式：上个 chunk 已确认某行超过上限，跳到下一个换行再恢复
        if self._discard_until_newline:
            idx = chunk.find(b"\n")
            if idx == -1:
                return []
            chunk = chunk[idx + 1:]
            chunk_start += idx + 1
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

        # 按**字节**切行再逐行解码，而不是先整体 decode 再 splitlines()：
        # 每行的行首字节偏移要准，解码后倒推字节位置会被多字节字符算歪（游标是
        # 去重身份的一部分，算歪即误判）。utf-8-sig 兼容 PowerShell Add-Content
        # -Encoding UTF8 在文件首行写入的 BOM（逐行解码时只对首行有实际影响）。
        result: list[tuple[str, tuple]] = []
        offset = chunk_start
        parts = chunk.split(b"\n")
        last_index = len(parts) - 1
        for index, raw in enumerate(parts):
            line_offset = offset
            offset += len(raw) + 1
            if index == last_index and not raw:
                continue                  # 末尾换行切出的空段
            line = raw.decode("utf-8-sig", errors="replace").strip()
            if line:
                result.append((line, (str(self.file_path), file_id, line_offset)))
        return result


# ----------------------------------------------------------------------
# 事件身份（G2）：同一逻辑事件只允许进入呈现管线一次
# ----------------------------------------------------------------------
#: 记录身份的**源 ID** 字段与别名归一（按优先级）。顺序与桥接自己的交互身份
#: 一致（``integrations/dsh-pet-bridge/index.js`` 的 ``interactionIdentity``：
#: ``requestId || rpcId || approvalId || callId``），保证两条记录只要桥接认为是
#: 同一个交互，我们算出来的身份也相同。**这些字段是桥接真实写出的字段**
#: （逐个核实过），不是我们编造的一套 ID 体系。
_SOURCE_ID_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("request", ("requestId", "request_id")),
    ("rpc", ("rpcId", "rpc_id")),
    ("approval", ("approvalId", "approval_id")),
    ("call", ("callId", "call_id")),
)


class EventIdentityLedger:
    """摄入去重账本：同一逻辑事件只允许**一次**进入呈现管线。

    键 = **来源 + 会话 + 事件名 + 源 ID**（有源 ID 时），否则 **代次 + 读取游标**
    （文件身份 + 行首字节偏移）——见 ``BaseAgentMonitor._record_identity``。
    它挡的是"同一份源事件被投递了两次"（桥接重发 / 多写者各写一份 / 写者截断后
    把同一段内容写回被重读），**不是**性能节流：同文本不同事件（不同 ID、不同
    游标）永远是两条合法事件，所以文本不进键。

    - **有界**：最多 ``max_entries`` 条，超出按插入次序淘汰最老的（FIFO）；
    - **有 TTL**：条目超过 ``ttl_s`` 即视为过期。上游重投递的合理窗口远小于它；
      "过期后可能重放" 是本轮明确的诚实边界——账本只在内存、不跨重启持久化，
      因此**不承诺**崩溃后的 exactly-once，文档与提示文案都不得暗示更强保证。
    - **关监测即清**：``clear()`` 由监视器在启动/停止时调用（旧代次的账本不得
      滞留，否则重启后合法的同 ID/同游标事件会被误当成重复而丢掉）。
    - **线程安全**：写入来自监视器 worker，清理来自主线程（``apply_config`` /
      ``shutdown`` → ``begin_stop``），两侧用一把短锁互斥。
    """

    __slots__ = ("_items", "_lock", "max_entries", "ttl_s")

    def __init__(self, max_entries: int = 512, ttl_s: float = 900.0) -> None:
        self._items: dict[tuple, float] = {}
        # 账本由监视器 worker 线程写、主线程清（apply_config/shutdown → begin_stop），
        # 两侧必须互斥：`next(iter(...))` 与 `clear()` 竞态会抛 StopIteration，
        # 把一条合法记录当成"处理失败"丢掉。
        self._lock = threading.Lock()
        self.max_entries = max(1, int(max_entries))
        self.ttl_s = float(ttl_s)

    def note(self, key: tuple) -> bool:
        """记录该身份；返回 True 表示**之前已经见过**（重复投递，应整条跳过）。"""
        now = time.monotonic()
        with self._lock:
            seen = self._items.get(key)
            if seen is not None and (now - seen) < self.ttl_s:
                return True
            self._purge(now)
            self._items[key] = now
            while len(self._items) > self.max_entries:
                self._items.pop(next(iter(self._items)), None)
            return False

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def _purge(self, now: float) -> None:
        """按插入次序清理过期条目（dict 保序 ≈ 时间序，从最老端起删）。"""
        while self._items:
            key = next(iter(self._items))
            if (now - self._items[key]) < self.ttl_s:
                return
            self._items.pop(key, None)


@dataclass(frozen=True)
class AgentEvent:
    """监视器向管理器传递的状态/工具事件及其启动代次。"""

    agent: str
    kind: str
    gen: int = 0
    state: str = ""
    tool: str = ""
    #: 写者归属（多 DSH 实例分区；见 :func:`record_owner`）。"" = 该记录没有写者
    #: 归属（无主桶，旧桥/非摄入路径），消费侧按"无主条目"处理，不是通配。
    owner: str = ""


class BaseAgentMonitor(QObject):
    """Agent 监视器抽象基类。"""

    #: 摄入去重账本的容量与 TTL（G2）。512 条实测 ≈ 126 KB（tracemalloc，键是 5 元
    #: 短字符串）；单条记录的新增成本实测 ≈ 1.5 µs（身份 1.1–1.3 µs + 账本 0.4 µs），
    #: 相对于本拍 tail 的 stat+read 可忽略。TTL 900s 远大于所有呈现节流窗口
    #: （音效冷却默认 2s、完成确认 800ms、过程汇报同文案 60s），因此"跨冷却的重复
    #: 投递"仍在账本内被拒；超过 TTL 的重复（或长会话里被 FIFO 挤出的条目，即最近
    #: 512 条记录之外）可能重放——这是有界账本的诚实边界，不承诺更强保证。
    _EVENT_LEDGER_MAX = 512
    _EVENT_LEDGER_TTL_S = 900.0

    state_changed = Signal(str, str)  # (agent_key, state)
    activity = Signal(str, str)       # (agent_key, 工具名) —— 过程汇报用，仅事件带工具名时发
    # Worker 事件载荷：保留上面的旧信号作为测试/外部兼容 API，管理器使用
    # 带代次的事件信号做接收端校验。
    state_event = Signal(object)
    activity_event = Signal(object)
    approval_requested = Signal(str, object)  # (agent_key, payload) —— 审批请求（纯提示气泡）
    approval_resolved = Signal(str, object)   # (agent_key, payload) —— 审批已结束，气泡应消失
    question_requested = Signal(str, object)  # (agent_key, payload) —— ask_user_question 阻塞交互
    question_resolved = Signal(str, object)   # (agent_key, payload) —— 问题已解决，气泡应消失
    cordis_requested = Signal(str, object)
    cordis_resolved = Signal(str, object)
    # 原始桥接记录转发（供对话上下文记忆 / 交互生命周期兜底等消费）：(agent_key, record)
    # 只挂 DSH 监视器；其他 Agent（claude/cursor/…）不产生这类增强记录。
    raw_record = Signal(str, object)
    # Unified protocol output; legacy signals below remain the compatibility API.
    normalized_event = Signal(object)
    # 硬失败（execution/failed）：DSH 已决定本轮不再继续，不经行为分析直接提醒
    execution_failed = Signal(str, object)   # (agent_key, payload)
    # 会话元数据更新（session/meta 事件）：(agent_key, record)
    session_meta = Signal(str, object)
    # 模型访问失败提醒（model_access 事件，errorCode 为服务端限流码）：(agent_key, record)
    model_access = Signal(str, object)
    # LLM API 错误（llm_error 事件，errorCode=真实码如 bad_response_status_code，
    # errorKind=api）：(agent_key, record)
    llm_error = Signal(str, object)
    # 用户介入信号（user_action 事件）：用户 DSH 审批/回答 → 桌宠应关闭对应弹窗
    user_action = Signal(str, object)
    # 未知桥接事件（DSH 桥接写出的、Pet 全部识别路径都不认识的事件名）：
    # (agent_key, record) —— Manager 侧据此提醒用户更新/重装 bridge。
    unknown_bridge_event = Signal(str, object)

    def __init__(self, agent_key: str, config_dir: Path, parent=None) -> None:
        super().__init__(parent)
        _LIVE_AGENT_MONITORS.add(self)
        self.agent_key = agent_key
        self.config_dir = Path(config_dir)
        self.events_dir = self.config_dir / "agent-events"
        self.events_file = self.events_dir / f"{agent_key}.jsonl"
        self._running = False
        self._paused = False
        self._tailer = ByteOffsetTailer(self.events_file)
        self._worker: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._gen = 0
        self._emit_gen = 0
        self._mkdir_on_start = True
        self._outbox: list[tuple] = []
        self._outbox_lock = threading.Lock()
        self._OUTBOX_CAP = 500
        # 摄入去重账本（G2 事件标识）：同一逻辑事件只允许进入呈现管线一次。
        # 每监视器一份（来源 + 会话进键），只活在内存里、随监视器启停清空。
        self._event_ledger = EventIdentityLedger(
            max_entries=self._EVENT_LEDGER_MAX, ttl_s=self._EVENT_LEDGER_TTL_S)
        # QObject 的 destroyed 槽在 PySide6 下不可靠地调用 bound method；
        # 连接无 receiver 的 callable，避免窗口销毁后遗留 daemon worker。
        self._destroyed_conn = self.destroyed.connect(
            lambda *_: BaseAgentMonitor._destroyed_guard(self)
        )
        self._destroy_guard_ran = False
        self._destroy_guard_lock = threading.Lock()
        # 发射失效（C++ 属主已析构）只留一次 debug，避免销毁期间刷屏
        self._signal_loss_logged = False

    def is_running(self) -> bool:
        return self._running and not self._paused

    def start(self) -> bool:
        if self._worker is not None and self._worker.is_alive():
            log.warning("Agent 监视器 [%s] 旧 worker 未退出，拒绝重启", self.agent_key)
            return False
        if self._destroyed_conn is None:
            self._destroyed_conn = self.destroyed.connect(
                lambda *_: BaseAgentMonitor._destroyed_guard(self)
            )
        with self._destroy_guard_lock:
            self._destroy_guard_ran = False
        self._signal_loss_logged = False
        self._worker_stop.set()
        self._worker_stop = threading.Event()
        self._gen += 1
        self._emit_gen = self._gen
        self._running = True
        self._paused = False
        if self._mkdir_on_start:
            self.events_dir.mkdir(parents=True, exist_ok=True)
        self._tailer.reset()
        # 新代次 = 新的摄入账本：旧代次的身份（尤其是无源 ID 的游标）不得跨重启
        # 存活，否则重启后合法的同 ID/同游标事件会被误当重复丢掉。
        self._event_ledger.clear()
        gen = self._gen
        self._worker = threading.Thread(
            target=self._work_loop, args=(gen,), daemon=True,
            name=f"agent-monitor-{self.agent_key}",
        )
        self._worker.start()
        log.info("Agent 监视器 [%s] 已启动", self.agent_key)
        return True

    def begin_stop(self) -> None:
        """作废当前代次并发出停止信号，不阻塞 GUI。"""
        self._emit_gen = -1
        self._running = False
        self._paused = False
        with self._outbox_lock:
            self._outbox.clear()
        # 关监测即清账本（规范 §三：缓存与队列必须有界、关监测清理）：通道不再
        # 摄入事件，账本没有任何存在理由；下次启动空账本等价于全新摄入。
        self._event_ledger.clear()
        self._worker_stop.set()

    def finish_stop(self, deadline: float | None = None) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            remaining = self._STOP_JOIN_TIMEOUT_S if deadline is None else max(
                0.0, deadline - time.monotonic()
            )
            worker.join(timeout=remaining)
            if worker.is_alive():
                log.warning("Agent 监视器 [%s] worker 退出超时", self.agent_key)
        conn = getattr(self, "_destroyed_conn", None)
        if conn is not None:
            try:
                self.destroyed.disconnect(conn)
            except RuntimeError:
                pass
            self._destroyed_conn = None

    def stop(self) -> None:
        self.begin_stop()
        self.finish_stop()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """收口测试直接创建且未显式停止的 monitor worker。"""
        for monitor in tuple(_LIVE_AGENT_MONITORS):
            try:
                monitor.stop()
            except Exception:
                log.debug("测试收口 Agent monitor 失败", exc_info=True)

    def pause(self) -> None:
        if self._running:
            self._paused = True

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            with self._outbox_lock:
                pending = list(self._outbox)
                self._outbox.clear()
            for signal, args in pending:
                self._emit_signal(signal, args)

    @staticmethod
    def _destroyed_guard(mon: "BaseAgentMonitor") -> None:
        with mon._destroy_guard_lock:
            if mon._destroy_guard_ran:
                return
            mon._destroy_guard_ran = True
        try:
            # 本函数只从 destroyed 信号回调进入（见 __init__/start 的连接），此时
            # C++ 对象正处于析构中途，再对本信号 disconnect 会触发 PySide6 的
            # "Failed to disconnect" 告警，并在解释器退出时的 GC 场景下诱发原生
            # 访问违规（Windows 0xC0000005）。连接由 Qt 在对象析构时自动清理；
            # 这里只需作废引用以断开 Python 引用环（lambda 捕获 self）。
            if getattr(mon, "_destroyed_conn", None) is not None:
                mon._destroyed_conn = None
            mon._worker_stop.set()
            mon._emit_gen = -1
            mon._running = False
            mon._paused = False
            worker = mon._worker
            if worker is not None and worker.is_alive():
                threading.Thread(
                    target=BaseAgentMonitor._reap_worker,
                    args=(worker, mon._STOP_JOIN_TIMEOUT_S, mon.agent_key),
                    daemon=True, name=f"agent-monitor-reap-{mon.agent_key}",
                ).start()
        except Exception:
            log.debug("Agent 监视器 [%s] 销毁兜底异常", mon.agent_key, exc_info=True)

    @staticmethod
    def _reap_worker(worker: threading.Thread, timeout: float, agent_key: str) -> None:
        worker.join(timeout=timeout)
        if worker.is_alive():
            log.warning("Agent 监视器 [%s] 销毁兜底 worker 退出超时", agent_key)

    def _emit_state(self, state: str, gen: int, owner: str = "") -> None:
        event = AgentEvent(self.agent_key, "state", gen=gen, state=state, owner=owner)
        self._emit_pair(
            self.state_changed, (self.agent_key, state),
            self.state_event, (event,), state=state,
        )

    def _emit_tool(self, tool: str, gen: int) -> None:
        event = AgentEvent(self.agent_key, "tool", gen=gen, tool=tool)
        self._emit_pair(
            self.activity, (self.agent_key, tool),
            self.activity_event, (event,), state=None,
        )

    def _emit_pair(self, legacy_signal, legacy_args: tuple,
                   event_signal, event_args: tuple, *, state: str | None) -> None:
        """Emit legacy + generation-aware signals as one pause-buffered unit."""
        if not (self._paused and self._running):
            self._emit_signal(legacy_signal, legacy_args)
            self._emit_signal(event_signal, event_args)
            return
        with self._outbox_lock:
            if state is not None:
                for signal, args in reversed(self._outbox):
                    if signal is self.state_event and args[0].state == state:
                        return
            while len(self._outbox) + 2 > self._OUTBOX_CAP:
                for i, (signal, _) in enumerate(self._outbox):
                    if signal in (self.activity, self.activity_event):
                        del self._outbox[i]
                        break
                else:
                    if state is None:
                        return
                    break
            self._outbox.extend(((legacy_signal, legacy_args), (event_signal, event_args)))

    def _emit(self, signal, args: tuple) -> None:
        if self._paused and self._running:
            with self._outbox_lock:
                if len(self._outbox) >= self._OUTBOX_CAP:
                    for i, (sig, _) in enumerate(self._outbox):
                        if sig in (self.activity, self.activity_event):
                            del self._outbox[i]
                            break
                    else:
                        if signal in (self.activity, self.activity_event):
                            return
                self._outbox.append((signal, args))
            return
        self._emit_signal(signal, args)

    def _retire_on_signal_loss(self) -> None:
        """信号源已随 C++ 属主析构：作废本代次、停止发射（一次 debug 留痕）。

        真实生命周期边界上的竞态（父链析构正好落在 worker 首轮 ``_worker_started``
        的发射窗口内）会让 ``signal.emit`` 抛 ``RuntimeError: Signal source has
        been deleted``；冒泡出去就是后台未捕获线程异常（pytest 记的
        ``PytestUnhandledThreadExceptionWarning``）。这里按"发射已失效"收口：
        停代 + 让 worker 自行退出，**只处理这一种情况**，其它异常照旧上抛。
        """
        self._emit_gen = -1
        self._running = False
        self._paused = False
        with self._outbox_lock:
            self._outbox.clear()
        self._worker_stop.set()
        if not self._signal_loss_logged:
            self._signal_loss_logged = True
            log.debug("Agent 监视器 [%s] 信号源已失效，停止本代次发射", self.agent_key)

    def _emit_signal(self, signal, args: tuple) -> bool:
        """真实发射一次；返回是否发出（信号源已失效 = False，不抛）。

        RuntimeError 只吞「C++ 属主已析构」那一种（由 ``shiboken6.isValid``
        判定）；属主仍有效时的 RuntimeError 原样上抛，不把真实缺陷静默化。
        """
        if not _cpp_object_alive(self):
            self._retire_on_signal_loss()
            return False
        try:
            signal.emit(*args)
        except RuntimeError:
            if _cpp_object_alive(self):
                raise
            self._retire_on_signal_loss()
            return False
        return True

    _POLL_INTERVAL_S = 1.5
    _STOP_JOIN_TIMEOUT_S = 2.0

    def _work_loop(self, gen: int) -> None:
        # 首轮等待一个周期，避免测试直调 _poll 与 worker 争抢 tailer。
        self._worker_started()
        while not self._worker_stop.wait(self._POLL_INTERVAL_S):
            if self._paused:
                continue
            try:
                self._poll(gen=gen)
            except Exception:
                log.debug("Agent 监视器 [%s] 轮询异常", self.agent_key, exc_info=True)

    def _worker_started(self) -> None:
        """供具体监视器在 worker 线程初始化其独占状态。"""

    def _on_parsed_record(self, data: dict, cursor: tuple | None = None) -> None:
        """子类挂钩：每条解析后的桥接/transcript 记录（worker 线程内调用）。

        单读方语义：记录只在本类 ``_poll`` 里解析一次，信号分派与该钩子
        （DshMonitor 用它喂 dsh_state 收敛器）是同一份解析结果的两个消费者。

        ``cursor`` 是这条记录的摄入游标 ``(文件, 文件身份, 行首偏移)``——它同时
        是**「这是回填之后真正读到的 live 字节」的证明**（历史记录在读之前就被
        回填跳到末尾，永远不会出现在这里），live 事件身份需要它做兜底。
        """

    def _emit_unified_event(self, data: dict) -> None:
        try:
            event = parse_agent_event(data, source_hint=self.agent_key, agent_name_hint=self.agent_key)
            normalized = normalize_event(event)
            if normalized is not None:
                self._emit_signal(self.normalized_event, (normalized,))
        except Exception:
            log.debug("统一 AgentEvent 解析失败", exc_info=True)

    def _record_identity(self, data: dict, cursor: tuple | None) -> tuple | None:
        """该记录的**逻辑事件身份**（摄入去重账本的键）。返回 None = 无法给出身份。

        规范 §三「去重优先级 = 源 ID 优先」：

        - **有源 ID**（requestId / rpcId / approvalId / callId，都是桥接真实写出的
          字段）→ ``(来源, 会话, 事件名, ID 类型, ID 值)``。事件名进键是必须的：
          ``tool/call`` 与 ``tool/result`` 共用同一个 callId，它们是**两条**逻辑
          事件；会话进键是因为同一 rpcId 可能被畸形/并发传输跨会话复用。
        - **无源 ID**（``AgentStatus``/``turn/*`` 这类只有 sessionId 的记录）→
          ``(来源, 代次, 读取游标)``：游标 = 文件身份 + 行首字节偏移。这是我们
          自己的摄入口径（**不是**上游给的 ID，也不拿文本 hash 造 ID）：同一份
          字节被读了第二次才算重复；文件轮转（file_id 变化）与新代次都不会与
          旧游标碰撞，因此**不会**阻掉合法的下一轮同文本/同状态事件。
        """
        session = str(data.get("sessionId") or data.get("session_id") or "")
        event = str(data.get("event") or data.get("type") or "")
        for kind, names in _SOURCE_ID_FIELDS:
            for name in names:
                value = data.get(name)
                if value not in (None, ""):
                    return (self.agent_key, session, event, kind, str(value))
        if cursor is None:
            return None
        file_key = str(cursor[0])
        return (self.agent_key, self._gen, "cursor", file_key, cursor[1], int(cursor[2]))

    def _is_duplicate_delivery(self, data: dict, cursor: tuple | None) -> bool:
        """同一逻辑事件是否**已经摄入过**（重复投递 → 整条跳过）。

        闸门放在摄入边界（记录刚解析出来的地方）而不是散在各消费点：一条记录
        会同时喂多条通道（语义层 ``normalized_event``、原始记录 ``raw_record``、
        状态 ``state_event``、工具 ``activity``、审批/问题等直通信号），在边界上
        挡一次即"一次事件多通道不双响"；放到消费点就变成每个消费点各挡一次。
        """
        key = self._record_identity(data, cursor)
        if key is None:
            return False
        if self._event_ledger.note(key):
            log.debug("重复源事件已拒（%s）: %s", key[2], str(data)[:200])
            return True
        return False

    def _record_owner(self, data: dict, cursor: tuple | None) -> str | None:
        """这条记录的写者归属（多实例分区）；基类给不出（返回 None，不标注）。

        只有知道归属的监视器（``DshMonitor``：桥记录带插件 boot UUID 或可用摄入
        游标推出文件身份）才返回字符串——返回 None 表示"这不是一个带写者归属的
        记录"，摄入侧不给它盖标注，下游按"无主"处理。
        """
        return None

    def _stamp_record_owner(self, data: dict, cursor: tuple | None) -> str:
        """在记录上盖写者归属标注（摄入口径），返回该归属（"" = 无主桶）。"""
        owner = self._record_owner(data, cursor)
        if owner is None:
            return ""
        data[_BRIDGE_OWNER_KEY] = owner
        return owner

    def _on_duplicate_record(self, data: dict) -> None:
        """钩子：被摄入账本判为**重复投递**而整条跳过的记录（worker 线程内调用）。

        默认无操作。需要"去重但不丢归属"的监视器在这里补登记（``DshMonitor``：
        同一逻辑事件被两个写者各写一份时，第二条整条跳过是对的——正文/音效不得
        双响——但它证明**第二个写者也在等同一条交互**，归属必须并进同一条目，
        否则第一个写者一回待机就把用户还在等的那个气泡撤掉）。
        """

    def _poll(self, gen: int | None = None) -> None:
        emit_gen = self._emit_gen if gen is None else gen
        for line, cursor in self._tailer.read_new_lines_with_cursors():
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                # 信封自己的 schema 版本：展平嵌套 data 只是给旧消费者的兼容
                # 口径，不能让它覆盖版本号（否则 data.schema/v2 误拒 v1 信封、
                # data.schema/v1 又放过 v99 信封）。
                envelope_schema = data.get("schema")
                nested = data.get("data")
                if isinstance(nested, dict):
                    flattened = dict(data)
                    flattened.update(nested)
                    data = flattened
                # 写者归属在展平之后盖（摄入侧权威，载荷里的同名字段顶不掉它），
                # 且在去重判定之前——重复投递也要能报出"这条是谁写的"（见
                # ``_on_duplicate_record``）。
                owner = self._stamp_record_owner(data, cursor)
                # 去重闸在展平之后、任何分派之前：身份字段可能来自嵌套 data
                # （旧版/自定义桥），展平后与消费端看到的是同一份记录。
                if self._is_duplicate_delivery(data, cursor):
                    self._on_duplicate_record(data)
                    continue
                ev = str(data.get("event", ""))
                st = str(data.get("state", ""))
                tool = str(data.get("tool", "") or "").strip()
                # Unified event path is additive and intentionally guarded.
                try:
                    # 语义层按信封版本解析：嵌套 data.schema 是载荷内容，
                    # 该键从展平结果里剔除后由信封版本接管。
                    semantic_record = dict(data)
                    if envelope_schema is None:
                        semantic_record.pop("schema", None)
                    else:
                        semantic_record["schema"] = envelope_schema
                    normalized = normalize_event(parse_agent_event(semantic_record, source_hint=self.agent_key, agent_name_hint=self.agent_key))
                    if normalized is not None:
                        self._emit_signal(self.normalized_event, (normalized,))
                except Exception:
                    normalized = None  # 解析失败视为语义层未识别，防止上一行残留值污染
                    log.debug("统一 AgentEvent 解析失败", exc_info=True)
                # 原始记录转发（兼容旧消费者）
                self._emit(self.raw_record, (self.agent_key, data))
                # 单读方第二消费者：同一份解析记录喂状态收敛器（仅 DshMonitor 实装）
                self._on_parsed_record(data, cursor)
                # 审批请求提醒：一次性事件，收到即发信号（不进入状态机，
                # 因为 DSH 等审批时 agent 仍在 running，状态还是 working）。
                # 只收桥接后的 UI 事件 approval/request 与兼容旧名
                # approval/requested；裸 approval/asked 只是状态/审计信号，绝不驱动
                # 桌宠审批弹窗——普通工具调用（如 pwsh 跑 Get-Location）被宿主标成
                # approval/asked 也不能误触发（收敛器同样不据此锁存审批态，见
                # pet/dsh_state.py 的事件表）。减法后气泡是纯提示（无点选回写）。
                if ev in ("approval/request", "approval/requested"):
                    self._emit(self.approval_requested, (self.agent_key, data))
                # 审批已结束（approval/decided 会话事件 或 approval/resolved 帧）：
                # 审批气泡应消失。
                if ev in ("approval/decided", "approval/resolved"):
                    self._emit(self.approval_resolved, (self.agent_key, data))
                # 用户问题（ask_user_question 阻塞交互）：与审批同等待遇，
                # question/requested 常驻气泡、question/resolved 收尾（纯提示）。
                if ev == "question/requested":
                    self._emit(self.question_requested, (self.agent_key, data))
                if ev == "question/resolved":
                    self._emit(self.question_resolved, (self.agent_key, data))
                if ev == "cordis/request-run" and _cordis_requires_approval(data):
                    self._emit(self.cordis_requested, (self.agent_key, data))
                if ev == "cordis/request-run-resolved":
                    self._emit(self.cordis_resolved, (self.agent_key, data))
                # 硬失败（execution/failed）：DSH 已决定本轮不再继续，不经行为分析直接提醒
                if ev == "execution/failed":
                    self._emit(self.execution_failed, (self.agent_key, data))
                if tool:
                    self._emit_tool(tool, emit_gen)
                # 会话元数据：session/meta 事件 → 信号转发给 Manager 缓存
                meta_type = str(data.get("type", ""))
                if meta_type == "session/meta":
                    self._emit(self.session_meta, (self.agent_key, data))
                # 模型访问失败事件：model_access（服务端限流/过载码）→ 信号转发给 Manager 显示提醒
                if ev == "model_access":
                    self._emit(self.model_access, (self.agent_key, data))
                # LLM API 错误事件：llm_error（errorCode=真实上游码，errorKind=api）→ 信号转发给 Manager
                if ev == "llm_error":
                    self._emit(self.llm_error, (self.agent_key, data))
                # 用户介入信号：user_action（审批决定/回答）→ 关闭对应弹窗
                if ev == "user_action":
                    self._emit(self.user_action, (self.agent_key, data))
                # 未知桥接事件：DSH 桥接写出的、Pet 全部识别路径（语义层/状态机/
                # 直通名单）都不认识的事件名 → 大概率 bridge 与桌宠版本不匹配，
                # 呈递给 Manager 弹「更新/重装 bridge」提醒。claude/cursor 的
                # transcript 噪声不算（只查 DSH 监视器）；session/meta 等按
                # type 字段直通的也不在此列。
                if (
                    self.agent_key == "dsh"
                    and bool(ev)
                    and normalized is None
                    and not normalize_event_state(ev, "")
                    and ev not in _RAW_BRIDGE_KNOWN_EVENTS
                    and meta_type != "session/meta"
                ):
                    self._emit(self.unknown_bridge_event, (self.agent_key, data))
                normalized = normalize_event_state(ev, st)
                if not normalized:
                    continue  # 不认识的事件类型：忽略，不误报为 working
                self._emit_state(normalized, emit_gen, owner)
            except Exception:
                log.debug("桥接记录处理失败，跳过该行", exc_info=True)

# ----------------------------------------------------------------------
# 各 Agent 具体监视器实现
# ----------------------------------------------------------------------

class DshMonitor(BaseAgentMonitor):
    """DeepSeek Harness (DSH) 监视器。

    事件来源：随桌宠内置的桥接插件（integrations/dsh-pet-bridge），开启联动时
    经用户同意后通过 `dsh plugin --profile web install <dir>` 一键安装（关闭时自动卸载）。
    插件把 agent 状态写入固定桥目录 `<数据基目录>/dsh-pet-bridge/dsh.jsonl`
    （与桌宠变体无关，源码/打包版路径一致），本监视器 byte-offset tail 读取。

    **单读方**（2026-10 减法）：DSH 桥目录只有这一个读者——一个
    DirGlobTailer + 一个轮询线程，每条记录解析一次后分发给两个消费者：
    既有信号分派（``_poll`` 直通/状态/工具信号）与 dsh_state 收敛器
    （8 态收敛 + 审批/问题锁存，纯逻辑见 ``pet/dsh_state.py``）。offline
    判定的唯一来源——DSH 端口探活（旧 DshStateTracker 的 3s 节拍）——也
    并入本线程：worker 线程里同步 socket 探测不阻塞 Qt 主线程，不再需要
    旧跟踪器那套 QTimer + 探测线程 + 代次作废的组合。
    """

    PLUGIN_NAME = "@dsh-pet/bridge"

    # 收敛器输出（worker 线程 emit，队列投递回主线程）：
    # dsh_state_changed(from_state, to_state, source_event, gen)；from_state 为 ""
    # 表示首个状态；source_event 为触发事件的桥接事件名（探活基线为 ""），
    # 供管理器做同源气泡去重（cordis/approval 有专属常驻气泡，不再弹通用提醒）；
    # gen 是**发出时的代次**（``_emit_gen``）——已入队的信号撤销不掉，消费侧
    # 靠它丢弃「停 → 启」之间同栈排队的上一代次信号（见
    # ``AgentLinkManager._dsh_convergence_current``）。
    dsh_state_changed = Signal(str, str, str, int)
    # 真人用户消息 → (session_id, text, gen)：对话开始的稳定触发点（sourceKind
    # 过滤在收敛器内完成；减法后桥接不再落明文 text，实参恒为 ""）；gen 口径
    # 同 ``dsh_state_changed``（停机后未重开时不得外转）。
    dsh_user_message = Signal(str, str, int)

    # live 回合成功收尾（桥接 turn/end，不带源 ID）→ (agent_key, payload)：
    # payload = {identity, sessionId, step, source_event, gen}。身份由
    # ``dsh_state.TurnCompletionTracker`` 从**这条 live 记录**推出（session +
    # step + 全局单调起点 token），不经过收敛状态：中途挂载 / 聚合 idle 基线
    # 先到 / 收敛器已停在 SUCCESS / 关开联动残留终态四种来向都拿不到
    # busy→success 边沿，但记录本身已经说明「这一回合结束了」。
    dsh_turn_completed = Signal(str, object)

    # live 回合**起点**（收敛器映射为思考态的记录：turn/start / user/message /
    # AgentStatus thinking）→ (agent_key, payload)：payload = {owner, sessionId,
    # step, gen}。聚合状态通道（``dsh_state_changed``）不带写者归属，"哪个写者开了
    # 新回合"在那里判不出来——消费侧的完成确认必须按归属裁决（见
    # ``AgentLinkManager._on_dsh_turn_started``），所以按原记录的身份补一条通道。
    dsh_turn_started = Signal(str, object)

    #: 写者被确认死亡（陈旧文件清理或低频存活探活确认）→ (owner, gen)。
    #: 消费侧据此只撤归属它的常驻交互与呈现簿记，**不报完成**：写者消失不是回合
    #: 收尾，弹「已完成本轮任务」是假消息。
    dsh_scope_retired = Signal(str, int)
    #: 重复投递（摄入账本已拒）的写者归属补登 → (agent_key, record)。
    #: 去重契约不变（正文/音效仍只响一次），这里只把"另一个写者也在等这条交互"
    #: 的事实并进同一 pending 条目，防止先回待机的那个写者撤掉它。
    bridge_owner_attested = Signal(str, object)

    _ONLINE_PROBE_EVERY_N_POLLS = 2  # 1.5s × 2 = 3s，对齐旧 DshStateTracker 探活节拍
    #: 写者 → 最近一次写盘文件的映射表容量（存活探活时反查 pid；容量按**非活跃**条目
    #: 计——忙/等待中的写者映射永不淘汰，见 :func:`_evict_inactive_owner_files`）。
    #: 活跃写者数被收敛器 scope 注册表上界（``dsh_state.SCOPE_REGISTRY_MAX`` = 32，
    #: 加无主桶）钉死，远小于本容量，所以"全表都是活跃写者"只可能是注册表上界被上调。
    _OWNER_FILE_MAX = 64
    #: 活跃写者存活探活的低频节拍：1.5s × 40 ≈ 60s 一次。写者死掉后最长一分钟回正，
    #: 又不至于把进程探活（每次 3 次系统调用）摊到每拍。
    _DEAD_WRITER_PROBE_EVERY_N_POLLS = 40
    #: 单轮最多探活几个写者（与陈旧文件清理同口径：不砸盘、不每拍全量体检）。
    _DEAD_WRITER_PROBE_LIMIT = 8

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
        # 启动自检只做一次（每实例）：pnpm 解析可能数十秒，绝不能重复触发
        self._link_check_started = False
        # 状态收敛器（纯逻辑）：8 态收敛 + 审批/问题锁存（**按写者分片**，见
        # dsh_state.DshStateConverger：多 DSH 实例各自独立锁存，折叠成单出口）
        self._converger = DshStateConverger()
        # live 回合收尾身份追踪（纯逻辑）：**全写者共享一张会话表**——同一 sessionId
        # 就是同一条会话，即使被两个实例镜像各写一份；按写者分表会让同一回合的两份
        # turn/end 算出两个身份，消费侧去重失效（同一回合提醒两次）。
        self._turn_tracker = TurnCompletionTracker()
        # 写者 → 最近一次写盘文件（确认写者死亡时反查 pid；有界 FIFO）
        self._owner_files: OrderedDict[str, str] = OrderedDict()
        # None = 未探测过；False 时收敛器不消费事件（离线态优先）
        self._online: bool | None = None
        self._probe_tick = 0
        # 活跃写者的低频存活探活节拍（见 _maybe_probe_dead_writers）
        self._dead_probe_tick = 0
        # 探活窗口的轮转游标：单轮有界（≤8）但**不许固定前缀**——否则排在窗口外的
        # 写者永远探不到（见 _maybe_probe_dead_writers）。
        self._dead_probe_cursor = 0

    # ------------------------------------------------------------ 单读方：探活 + 收敛
    def start(self) -> bool:
        self._converger.reset()
        # 新代次 = 新的写者表；收尾会话表随代次清空（全局计数不回收，见其文档）。
        self._turn_tracker.reset()
        self._owner_files.clear()
        self._online = None
        self._probe_tick = 0
        self._dead_probe_tick = 0
        self._dead_probe_cursor = 0
        return super().start()

    def begin_stop(self) -> None:
        # 关监测即复位探活窗口：下一代次（start）从候选集头部重新开始计时，
        # 不让上一代次的游标位置影响新写者表的轮转次序。
        self._dead_probe_cursor = 0
        super().begin_stop()

    def _record_owner(self, data: dict, cursor: tuple | None) -> str | None:
        """桥记录的写者归属；顺手记下"该写者最近写的是哪个文件"。

        归属先经收敛器准入（``admit_owner``：注册表有界、满时先在界内回收安静写者、
        挤不出才拒接状态分片）——摄入标注、状态分片、交互归属三处用的是**同一个**
        key，不会出现"状态拒接了、交互却按另一个 key 记"的错位。拒接只影响状态
        分片，标注照旧按写者自己（绝不并入别人的桶）。

        这里**不产拒接诊断**：同一条记录随后还会在 ``handle_record`` 里再准入一次
        （那才是真正要建状态分片的那次），诊断统一由那次给出，否则一条记录两条
        告警（历史缺陷，见 ``DshStateConverger.note_rejected_owner``）。
        """
        owner = record_owner(data, cursor)
        if owner is None:
            return None
        key, _admitted = self._converger.admit_owner(owner)
        if cursor is not None:
            path_key = str(cursor[0])
            self._owner_files[key] = path_key
            self._owner_files.move_to_end(key)
            if len(self._owner_files) > self._OWNER_FILE_MAX:
                self._trim_owner_files()
        return key

    def _trim_owner_files(self) -> None:
        """映射超容量时淘汰**非活跃**写者（见 :func:`_evict_inactive_owner_files`）。"""
        evicted = _evict_inactive_owner_files(
            self._owner_files, set(self._converger.owners_needing_liveness()),
            max_entries=self._OWNER_FILE_MAX)
        if evicted:
            log.debug("写者文件映射超容量(%s)，淘汰非活跃写者 %s 条（活跃写者的映射一律保留）",
                      self._OWNER_FILE_MAX, len(evicted))

    def _worker_started(self) -> None:
        # 首轮立即探活，让 offline/idle 基线尽早落下（旧跟踪器 start() 同款语义）
        self._probe_online()

    def _poll(self, gen: int | None = None) -> None:
        self._probe_tick += 1
        if self._probe_tick >= self._ONLINE_PROBE_EVERY_N_POLLS:
            self._probe_tick = 0
            self._probe_online()
        super()._poll(gen)
        # 死写者退役（24h 陈旧文件清理路径）：只撤它自己的状态与常驻交互，绝不报完成。
        self._retire_dead_writer_scopes(self._tailer.take_confirmed_dead())
        # 活跃写者的存活探活（低频率）：进程死了但状态还停在"忙/等待"的那些，必须
        # 撤掉，否则桌宠永远忙。见 _maybe_probe_dead_writers。
        self._maybe_probe_dead_writers()
        # 锁存超时兜底：DSH 漏发 approval/decided / question/resolved 时强制回 working
        self._emit_converged(self._converger.tick())

    def _retire_dead_writer_scopes(self, dead: set[str]) -> None:
        if not dead:
            return
        for owner in [o for o, path in self._owner_files.items() if path in dead]:
            self._retire_owner(owner, reason="桥文件写者已确认死亡")

    def _retire_owner(self, owner: str, *, reason: str) -> None:
        """退役一个写者：撤它的状态分片与交互归属，**不报完成**。"""
        self._owner_files.pop(owner, None)
        self._emit_converged(self._converger.retire_scope(owner))
        log.info("%s，退役其状态与交互归属: %s", reason, owner[:120])
        self._emit(self.dsh_scope_retired, (owner, self._emit_gen))

    def _maybe_probe_dead_writers(self) -> None:
        """低频存活探活：只探"还在忙/还在等"的写者，只认**明确已死**。

        为什么必须有（绕过 24h 清理路径）：陈旧文件清理只处理超龄 24h 且文件名带
        pid 的**死文件**，而写者进程死掉的那一刻它的状态还留在桌宠里——「永远忙」
        「永远在等审批」要等到 24 小时后才可能被清掉。这条探活用同一个 pid 探活
        口径（``_bridge_writer_alive``）但只针对**当前有 busy/pending 的已知写者**，
        而且是低频（``_DEAD_WRITER_PROBE_EVERY_N_POLLS`` 拍一次，单轮最多
        ``_DEAD_WRITER_PROBE_LIMIT`` 个，每个 3 次系统调用）。

        窗口**轮转**、不取固定前缀：候选来自注册表的登记次序（最久登记的在前），
        固定取前 8 个时，只要有 8 个"活着但状态卡在忙/等待"的写者长期占住前 8 位
        （插件重载 = 同进程新 boot UUID，上一代的忙态留在桌宠里，pid 却仍活着），
        排在第 9 位及以后的**真死写者**就永远排不进窗口，忙态再也回收不了——桌宠
        永远忙。游标按"本轮取走的个数"前进并回卷，候选集不变时 ⌈n/8⌉ 轮内必然
        覆盖全部（注册表上界 32 → 最坏 4 轮，约 4 分钟）；候选集变化（退役/新写者）
        时游标按取模归位，只会更快覆盖，不会饿死某一个。复位点：``start`` /
        ``begin_stop`` / 探到整体离线（写者镜像表被清空时）。

        三条硬边界：
        - 只认 ``False``（明确已死）才退役；``None``（**权限拒绝**：Win
          ERROR_ACCESS_DENIED / POSIX EPERM，或平台探活不了）与 ``True`` 一律不动
          ——探不动活着的写者时宁可多留一拍，也不误撤活写者（撤了就撤掉用户还在
          等的交互）。判不了一律 ``None``，见 :func:`_bridge_writer_alive`；
        - **绝不按文件静默/时间**退役（没有 mtime 判定、没有 TTL）：文件静默可能
          只是这个实例这一会儿没干活，合法在途的 pending 不会被时间撤掉；
        - 退役按**写者归属**（boot 身份）而不是 pid：同一个 pid 重启后的新写者
          是另一个 owner，不会被误伤。

        候选要求"写者在 ``_owner_files`` 里"（才有 pid 可探），所以映射表**不许**淘汰
        还在忙/在等的写者——见 :func:`_evict_inactive_owner_files`。
        """
        self._dead_probe_tick += 1
        if self._dead_probe_tick < self._DEAD_WRITER_PROBE_EVERY_N_POLLS:
            return
        self._dead_probe_tick = 0
        candidates = [o for o in self._converger.owners_needing_liveness()
                      if o and o in self._owner_files]
        window, self._dead_probe_cursor = self._rotated_probe_window(candidates)
        for owner in window:
            match = _BRIDGE_INSTANCE_FILE_RE.match(Path(self._owner_files[owner]).name)
            if match is None:
                continue  # 旧版单实例 dsh.jsonl：无 pid 线索，不探活
            if _bridge_writer_alive(int(match.group(1))) is False:
                self._retire_owner(owner, reason="桥写者进程已确认死亡（存活探活）")

    def _rotated_probe_window(self, candidates: list[str]) -> tuple[list[str], int]:
        """本轮的探活窗口（≤ ``_DEAD_WRITER_PROBE_LIMIT`` 个）与推进后的游标。

        只读：返回窗口与新游标，不动监视器状态。只有"候选数 > 窗口容量"时才轮转：
        装得下就全探（没有窗口外的写者，也就没有饿死问题）。
        """
        total = len(candidates)
        if total <= self._DEAD_WRITER_PROBE_LIMIT:
            return candidates, 0
        start = self._dead_probe_cursor % total
        window = [candidates[(start + i) % total]
                  for i in range(self._DEAD_WRITER_PROBE_LIMIT)]
        return window, (start + self._DEAD_WRITER_PROBE_LIMIT) % total

    def _probe_online(self) -> None:
        """worker 线程内同步端口探活（关着的端口 Windows 回环可能等 ~250ms，
        在本线程阻塞无碍——这正是双读方合并省掉旧探测线程的原因）。"""
        try:
            online = any(harness_launcher.is_running(p) for p in _dsh_state_candidate_ports())
            if not online:
                # 桌面端旁路：desktop host 端口随版本硬编码（本机实测 19387，已在
                # 候选里），不赌死——端口全 miss 时以「桌面端进程在跑」兜底 online
                online = harness_launcher.desktop_process_running()
        except Exception:
            log.exception("DSH 在线探测异常")
            online = False
        self._online = online
        if not online:
            # DSH 整体离线：写者文件镜像随实例一起作废。留着它，新 DSH 重启后的
            # 存活探活会拿旧路径去探（探活对象错了），也可能把新写者判成旧写者。
            self._owner_files.clear()
            # 镜像表没了，上一代次的轮转位置也就没有意义（游标只对当前候选集有效）
            self._dead_probe_cursor = 0
        self._emit_converged(self._converger.set_online(online))

    def _on_parsed_record(self, data: dict, cursor: tuple | None = None) -> None:
        if self._online is False:
            return  # DSH 离线时不消费事件（离线态优先）
        owner = str(data.get(_BRIDGE_OWNER_KEY) or "")
        if self._turn_tracker.is_duplicate_start(data, owner):
            # 镜像重投递的回合起点（另一个写者写了同一条 (会话, step) 起点）：既不
            # 重新发号，也不推进收敛状态机——让这份副本把折叠态重新推回"忙"，收尾
            # 时就会再报一次完成（同一回合提醒两次）。
            return
        if map_event_to_state(data) is DshState.THINKING:
            # 本写者回到思考态 = 它开了新回合（turn/start / user/message / 聚合基线
            # thinking）。带写者归属发一条：消费侧据此作废**这个写者**上一轮的待定
            # 完成确认（别的写者的确认原样到期），并把这份"忙"记到它的分片上。
            self._emit(self.dsh_turn_started, (
                self.agent_key,
                {
                    "owner": owner,
                    "sessionId": str(data.get("sessionId") or data.get("session_id") or ""),
                    "step": str(data.get("step") or ""),
                    "gen": self._emit_gen,
                },
            ))
        self._emit_converged(self._converger.handle_record(data, owner=owner))
        # live 回合收尾身份**在收敛器之后**推（投递顺序 = 发出顺序）：完整观测到
        # 一个回合时先让既有的 busy→success 边沿排定确认，本通道再看到 pending 就
        # 不动它；收敛器看不到的四种来向（中途挂载 / 聚合 idle 基线先到 / 已停在
        # SUCCESS 的同状态收尾 / 关开联动残留终态）由本通道收口——它认得这条 live
        # 记录本身，不需要任何状态前置。
        identity = self._turn_tracker.observe(data, cursor, owner)
        if identity is not None:
            self._emit(self.dsh_turn_completed, (
                self.agent_key,
                {
                    "identity": identity,
                    "owner": owner,
                    "sessionId": str(data.get("sessionId") or data.get("session_id") or ""),
                    "step": str(data.get("step") or ""),
                    "source_event": str(data.get("event") or ""),
                    "gen": self._emit_gen,
                },
            ))

    def _on_duplicate_record(self, data: dict) -> None:
        """重复投递整条跳过，但把它的写者归属报上去（多实例归属补登）。

        去重契约不变：正文/提示音仍然只响一次。补登的只是"这条逻辑事件还有另一个
        写者在写"这一事实——消费侧把它并进同一个待处理交互的归属集合，先回待机的
        那个写者因此撤不掉用户还在等的那条交互。
        """
        if self.agent_key != "dsh":
            return
        self._emit(self.bridge_owner_attested, (self.agent_key, data))

    def _emit_converged(self, outputs: list) -> None:
        # 代次随信号一起投递（取发出这一刻的 _emit_gen）：消费侧的收敛闸靠它
        # 区分"本代次的信号"与"上一代次停在队列里的信号"。
        for item in outputs:
            if item[0] == "state":
                self._emit(self.dsh_state_changed,
                           (item[1], item[2], item[3], self._emit_gen))
            elif item[0] == "user_message":
                self._emit(self.dsh_user_message, (item[1], item[2], self._emit_gen))


    @staticmethod
    def bundled_plugin_dir() -> Path | None:
        """内置桥接插件目录：打包版在 sys._MEIPASS，源码运行在仓库 integrations/ 下。"""
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "integrations" / "dsh-pet-bridge")
        candidates.append(Path(__file__).resolve().parent.parent.parent / "integrations" / "dsh-pet-bridge")
        for c in candidates:
            if (c / "package.json").is_file():
                return c
        return None

    @classmethod
    def bridge_link_stale(cls) -> list[tuple[str, str]]:
        """已装插件、但 `link:` 目标不是当前内置插件目录（或目标已失效）的 profile。

        为什么需要：profile 里记的是**安装那一刻的绝对路径**（源码运行是仓库
        integrations/，打包版是 dist-onedir/<构建名>/_internal/integrations/...），
        重新打包或换构建目录后这条 link 就成了孤儿——dsh 仍会加载旧代码，或直接
        解析失败。安装/刷新只在用户重新切换联动开关时才发生（见 install_bridge），
        联动一直开着的人不会自愈，所以在启动路径补一次自检。

        只认路径型 spec：从 registry 装的版本型 spec 视为用户有意为之，不动。
        """
        plugin = cls.bundled_plugin_dir()
        if plugin is None:
            return []
        try:
            current = plugin.resolve()
        except OSError:
            current = plugin
        stale: list[tuple[str, str]] = []
        for profile in _real_profiles():
            pkg = _read_manifest(profile)
            if pkg is None or not _manifest_has_plugin(pkg):
                continue
            spec = str((pkg.get("dependencies") or {}).get(DSH_PLUGIN_NAME) or "")
            target = _path_spec_target(spec, profile)
            if target is None:  # 版本型 spec：不属于 link 陈旧
                continue
            if not target.exists() or target != current:
                stale.append((profile.name, spec))
        return stale

    @classmethod
    def refresh_stale_bridge_links(cls) -> list[str]:
        """把陈旧 link 刷新为当前内置插件目录，返回刷新成功的 profile 名。

        只处理**已装插件**的 profile——启动自检绝不替用户安装（安装需用户同意）。
        """
        plugin = cls.bundled_plugin_dir()
        if plugin is None:
            return []
        stale_names = {name for name, _spec in cls.bridge_link_stale()}
        if not stale_names:
            return []
        refreshed: list[str] = []
        for profile in _real_profiles():
            if profile.name not in stale_names:
                continue
            # 与安装路径同源：manifest 里可能存在指向不存在路径的依赖（旧构建目录 /
            # 版本号变更的 tgz），裸 pnpm 会一直失败。先按探测结果修正再重试一次，
            # 否则启动自检每次都在同一处静默失败，link 永远刷不新。
            rc, out, repaired = _run_pnpm_repairing_specs(profile, "add", str(plugin))
            if rc != 0:
                log.warning(
                    "桥接 link 刷新失败 %s: %s", profile.name, (out or "")[-200:],
                )
                continue
            if repaired:
                log.info(
                    "桥接 link 刷新前修正依赖路径 %s: %s", profile.name, "；".join(repaired),
                )
            pkg = _read_manifest(profile)
            if pkg is not None:
                try:
                    _manifest_set_bundle(pkg, profile, True)
                except Exception:
                    log.exception("桥接 bundles 写入失败: %s", profile.name)
            refreshed.append(profile.name)
        return refreshed

    def schedule_link_refresh_check(self, spawn=None) -> None:
        """启动后自检一次桥接 link（每实例一次；后台线程，不阻塞 GUI）。

        spawn 仅为测试注入：默认真起守护线程。
        """
        if self._link_check_started:
            return
        self._link_check_started = True
        runner = spawn or self._spawn_link_check
        try:
            runner(self._refresh_links_worker)
        except Exception:
            log.exception("桥接 link 自检启动失败")

    @staticmethod
    def _spawn_link_check(target) -> None:
        threading.Thread(
            target=target, daemon=True, name="dsh-bridge-link-check",
        ).start()

    def _refresh_links_worker(self) -> None:
        """后台刷新陈旧 link：失败只记日志（自检是静默修复，不打扰用户）。"""
        try:
            refreshed = self.refresh_stale_bridge_links()
        except Exception:
            log.exception("桥接 link 自检失败")
            return
        if refreshed:
            log.info("桥接 link 已刷新为当前构建: %s", ", ".join(refreshed))

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
        if _which("node") is None and _pnpm_command() is None:
            return False, "找不到 node，请先安装 Node.js（需包含 npm）"
        if _pnpm_command() is None:
            return False, _PNPM_MISSING_HINT

        profiles = _real_profiles()
        if not profiles:
            # 全新 dsh（从未运行过）没有 profile：先按 dsh initProfile 三件套
            # 补出默认 web profile 再安装；补不出才报错，不把新用户挡住。
            if not _ensure_profile(DSH_PROFILE_HOME / "profiles" / "web"):
                return False, ("没有可用的 dsh profile（~/.dsh/profiles 下无 package.json），"
                               "且自动补齐默认 web profile 失败")
            profiles = _real_profiles()
            if not profiles:
                return False, "补齐默认 web profile 后仍未识别到 dsh profile"

        failed = []
        succeeded = []
        repaired_notes: list[str] = []
        for profile in profiles:
            pkg = _read_manifest(profile)
            if pkg is None:
                failed.append(f"{profile.name}: package.json 读取失败")
                continue
            if _manifest_has_plugin(pkg):
                # 已安装也要刷新本地 link。否则源码/打包版升级后，profile
                # 仍可能指向旧的 dist-onedir bridge，重启 dsh 只会继续加载旧代码。
                # pnpm add 会更新已有的 link spec；失败时保留原安装并报告。
                rc, out, repaired = _run_pnpm_repairing_specs(profile, "add", str(plugin))
                if rc != 0:
                    failed.append(
                        f"{profile.name}: pnpm refresh 失败 {(out or '')[-150:]}"
                        f"{_dependency_spec_hint(profile, _read_manifest(profile) or pkg)}"
                    )
                    continue
                if repaired:
                    repaired_notes.append(f"{profile.name}: " + "；".join(repaired))
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
            rc, out, repaired = _run_pnpm_repairing_specs(profile, "add", str(plugin))
            if rc != 0:
                failed.append(
                    f"{profile.name}: pnpm add 失败 {(out or '')[-150:]}"
                    f"{_dependency_spec_hint(profile, _read_manifest(profile) or pkg)}"
                )
                continue
            if repaired:
                repaired_notes.append(f"{profile.name}: " + "；".join(repaired))
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
        note = ""
        if repaired_notes:
            note = (
                "；已自动修正失效的依赖路径（原文件备份为 package.json.bak-*）："
                + "；".join(repaired_notes)
            )
        return True, f"桥接插件已安装到 {len(succeeded)} 个 dsh 实例（{', '.join(succeeded)}）{note}"

    @classmethod
    def uninstall_bridge(cls) -> bool:
        """关闭联动时卸载桥接插件。返回是否全部成功（失败记日志）。

        幂等：未安装的 profile 直接视为成功；不再依赖 dsh CLI（同 install_bridge）。
        没有 pnpm 时不能直接报成功：manifest 里的 `link:` 条目还指着即将被删除的
        程序目录（2026-09 dsh 事故同型），改为纯 JSON 手改卸载——备份 package.json、
        删依赖条目与 dsh.profile.bundles 登记、尽力删 profile 内的插件链接。
        """
        has_pnpm = _pnpm_command() is not None
        ok = True
        for profile in _real_profiles():
            pkg = _read_manifest(profile)
            if pkg is None or not _manifest_has_plugin(pkg):
                continue  # 未安装视为成功（幂等）
            if has_pnpm:
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
            else:
                pkg = _uninstall_manifest_without_pnpm(profile, pkg)
                if pkg is None:
                    ok = False
                    log.warning("卸载 DSH 桥接插件失败(%s): package.json 手改失败", profile.name)
                    continue
                _remove_linked_plugin_dir(profile)
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
                    self._emit_unified_event(data)
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
            # OpenCode 更新/删库重建会替换 inode；沿用旧 rowid 会静默漏掉新库，
            # 因此新文件重新执行 backfill。
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
    """自定义联动 Agent 监视器（agent_link.custom_agents 配置驱动）。"""

    """只读监听用户指定路径的统一协议 JSONL 事件文件（docs/AGENT_LINK_PROTOCOL.md §4）：
    不创建目录、不写任何外部位置、无需授权弹窗；文件不存在时静默空转等待，
    出现后自动开始增量读取（backfill 防护跳过历史内容）。"""

    def __init__(self, agent_key: str, config_dir: Path, events_path: str, parent=None) -> None:
        super().__init__(agent_key, config_dir, parent)
        self.events_file = Path(events_path).expanduser()
        self.events_dir = self.events_file.parent
        self._tailer = ByteOffsetTailer(self.events_file)
        self._mkdir_on_start = False

    def start(self) -> bool:
        # 覆写基类 start：基类会 mkdir 事件目录，这里只读监听外部文件，
        # 不替用户在任意路径创建目录
        ok = super().start()
        if not ok:
            return False
        log.info("Agent 监视器 [%s] 已启动 (%s)", self.agent_key, self.events_file)
        return True


def other_instances_use_agent(config, agent_key: str) -> bool:
    """另一个本地安装点（变体/独立实例）是否也开着该全局 Agent 联动。

    "其他实例" = 别的变体目录的配置，或本变体目录里按 ``--instance <id>`` 隔离出来的
    旧式独立实例配置。**不含本变体自己的 ``config-slot-N.json``**：那是固定身份的每宠
    档案（``pet_profiles`` §五，全局键不落档案），历史「整表落种」复制进去的
    ``agent_link`` 只是一份旧快照——按它判定会让卸载被本变体自己残留的档案挡住，
    hooks/桥接插件清不掉。别的变体目录、旧共享目录、旧式非 slot 实例照旧保护
    （hooks/桥接插件是跨变体共享的全局状态）。
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
    # 档案名判据的单一来源在 pet_profiles；这里延迟导入——本函数只在卸载清理 /
    # 关掉联动时各调一次，为一条常量正则把它拉进 agent_link 的模块导入图不划算
    # （agent_link 在启动路径上）。
    from ..pet_profiles import _SLOT_PROFILE_RE

    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if config.path and path == config.path:
            continue
        if path.parent == config_dir and _SLOT_PROFILE_RE.match(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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

    装配与编排：
    - 装配：4 内置 + 配置驱动的自定义监视器，并完成信号接线（DSH 监视器是
      桥目录的唯一读方：信号分派 + dsh_state 收敛器两个消费者共享同一份解析）；
    - 监视器生命周期：pause / resume / shutdown / apply_config；
    - set_enabled 安装/卸载编排（授权弹窗、后台安装、hooks 注入/移除）；
    - 对既有调用面（PetWindow / AppShell / ProactiveScreenWatcher / 测试）的薄转发。
    去抖/节流/完成确认与气泡/音效/动画调度都在本类内实现。
    挂载于 PetWindow，持有 4 个 Agent 的监视器，并根据状态驱动桌宠动作与气泡。
    """

    install_finished = Signal(str, bool, str, int)  # (agent_key, ok, message, install_token)
    # DSH 收敛器输出（转发自 DshMonitor，供 AppShell 驱动灵动岛/收尾）：
    # (from_state, to_state)；from_state 为 "" 表示首个状态。监视器侧的队列投递
    # 代次已在 ``_on_dsh_converged_state`` / ``_on_dsh_converged_user_message``
    # 边界校验完毕，这里出去的都是当前代次的事件，不再带 gen。
    dsh_state_changed = Signal(str, str)
    # DSH 真人用户消息（session_id, text）；减法后桥接不落明文，text 恒为 ""
    dsh_user_message = Signal(str, str)

    # 联动气泡展示名
    AGENT_NAMES = {"dsh": "DSH", "claude": "Claude Code", "cursor": "Cursor", "opencode": "OpenCode"}
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
    #: 会话级缓存的条目上限（缺陷 23）：``_session_meta_cache`` 的 key 是
    #: **外部会话 ID**，进程常驻数周时按会话数单调增长（此前没有任何删除路径）。
    #: 取 256（同项目其它小缓存同量级：sound_winmm 池 8、webm_clip 首帧共享表
    #: 20000），FIFO 淘汰最老条目。
    _SESSION_CACHE_MAX = 256
    #: pending 阻塞交互表的**硬容量**（规范 §三「缓存与队列必须有界」的另一半）：
    #: 显示队列 ``window_alerts.ALERT_QUEUE_LIMIT`` = 64 只是呈现层，本地真值表
    #: 同样必须有界（每条都带全文/问题载荷）。取 256 = 4× 显示队列上限：远大于
    #: 任何真实并发审批/问题数，同时把"长会话 + 多会话 + 异常桥"的洪泛挡在内存外。
    _PENDING_INTERACTION_MAX = 256
    #: **单条** pending 交互的归属集合上限（写者身份数上界 = 收敛器 scope 注册表
    #: 容量）。外层 256 条 pending 的容量不能证明这个嵌套集合有界：同一条合法在途
    #: 请求可被多个写者反复投递（插件每重载一次就是新的 boot 身份），只加不减就是
    #: 一条随投递次数线性增长的内存。满时只锁存 ``owners_overflow``，收口随之收紧
    #: （见 :meth:`_merge_pending_owner` / :meth:`_release_owner`）。
    _PENDING_OWNERS_MAX = SCOPE_REGISTRY_MAX
    #: 阻塞型交互在**本地**的优先级（0 = 最高，与显示队列同口径）：审批 / 问题 /
    #: Cordis 请求都是"用户卡在这里等回答"的关键状态，一律最高——容量裁决因此
    #: 退化为"淘汰最旧的一条"；"拒收更低优先级的新条目"分支留给未来真出现
    #: 更低优先级的交互类型时复用同一政策（今天不编造这种类型）。
    _PENDING_INTERACTION_PRIORITY = 0
    #: **无身份**降级提示（没有任何 rpcId/approvalId/requestId/callId，因此不可能
    #: 与任何收尾帧配对）的存活上限：这类条目没有任何上游回收路径，只能靠 TTL
    #: 收口。带身份的审批/问题是**长待批**的关键状态，**不适用 TTL**（规范
    #: 「不能随意删除关键状态」），只受 `_PENDING_INTERACTION_MAX` 约束。
    _PENDING_INTERACTION_HINT_TTL_S = 900.0
    #: 未交付常驻交互的补投节拍（毫秒）：新请求没交付时 1s 一拍，等不到呈现目标
    #: 则退避到上限；交付完或表空即停（稳态零唤醒），见
    #: ``_ensure_pending_redeliver_timer``。
    _PENDING_REDELIVER_INTERVAL_MS = 1000
    _PENDING_REDELIVER_MAX_MS = 5000
    _ACTIVITY_MIN_INTERVAL = 10.0    # 同 Agent 过程气泡最小间隔
    _ACTIVITY_GLOBAL_MIN = 8.0       # 全局最小间隔（多 Agent 并发防刷屏）
    _ACTIVITY_SAME_LABEL = 60.0      # 同一工具文案 60s 内不重复
    _ACTIVITY_TEXT_LIMIT = 80        # 过程汇报气泡文案上限（超出截断加「…」）
    _BUSY_STATES = ("working", "thinking")
    _DONE_CONFIRM_MS = 800   # busy→idle 稳定确认窗口（过滤 working→idle→working 抖动）
    _DONE_COOLDOWN_S = 5.0   # 同 Agent 完成气泡最小间隔（最后一道保险）
    _UNKNOWN_BRIDGE_REMIND_COOLDOWN_S = 600.0  # 未知桥接事件提醒：同 agent 10 分钟内最多一次
    _DSH_TURN_ID_MEMORY = 64  # live 回合收尾身份记忆（每 Agent 有界，防长跑增长）

    def __init__(self, window: Any, config: Any, *, min_interval: float = 2.0,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(window if hasattr(window, "winId") else None)
        self.win = window
        self.cfg = config
        self.config_dir = config.dir
        self._shutdown = False
        # Agent 本轮消费统计：用余额差值估算，网络查询走后台线程。
        self._cost = agent_cost_mod.AgentCostTracker(clock=clock)
        self._cost_balance_ready.connect(self._on_cost_balance)
        _LIVE_AGENT_LINK_MANAGERS.add(self)
        self._install_token = 0
        self._install_pending: dict[str, int] = {}
        # 状态节流：同一 Agent 相同状态去抖；同 Agent 两次动作切换最小间隔
        # （Cursor 等 transcript 密集写入时防止动画"抽搐"）
        self._min_interval = float(min_interval)
        self._clock = clock
        self._last_applied: dict[str, tuple[str, float]] = {}
        # 原始状态流（不受去抖/节流影响）：用于 busy→idle 完成检测。
        # 不能用 _last_applied 做完成判定——节流会丢掉紧跟其后的 idle，导致完成通知丢失。
        self._last_raw: dict[str, str] = {}
        # 原始状态流的**写者分片**：(agent, owner) → (state, 写入序号)。多 DSH 实例
        # 并行时每个实例各自写自己的 AgentStatus 基线，一个实例回待机不能把另一个
        # 实例的忙判成待机（那会弹假完成、把动画/识屏判据一起切回待机）。_last_raw
        # 存的是这些分片**折叠后**的值（见 _on_agent_state），呈现层只读折叠值。
        # 有界 FIFO；启用/停用联动时随 _retire_agent_presentation 整片清掉。
        self._owner_raw: dict[tuple[str, str], tuple[str, int]] = {}
        self._owner_seq = 0
        self._done_pending: dict[str, QTimer] = {}   # agent → 稳定确认定时器
        # agent → 该确认的来源："success"（回合真实成功）或 "edge"（busy→idle 等边沿）。
        # DSH 中止回合只撤 edge 来源的确认，不误撤另一会话真实完成排下的 success 确认。
        self._done_cause: dict[str, str] = {}
        # agent → 排这条完成确认时当事人（写者归属）。到期判据只看它自己的状态：
        # A 完成、B 还在忙时既不能吞掉 A 的提醒，也不能为了成全 A 把整体判成待机。
        self._done_owner: dict[str, str] = {}
        # DSH 中止回合注入 idle 时为 True：跳过 _on_agent_state 的「该 agent 全部交互」
        # 兜底清理——中止的 turn/end 已由 _on_interaction_lifecycle 按会话定向清理，
        # 全量清会误撤其它会话仍在等待的审批/问题气泡。
        self._idle_clear_scoped = False
        self._done_cooldown: dict[str, float] = {}   # agent → 上次完成气泡时刻
        self._unknown_bridge_reminded_at: dict[str, float] = {}  # agent → 上次未知桥接事件提醒时刻
        self._saw_alert: set[str] = set()            # busy 周期内出现过 attention/error 的 Agent
        self._saw_error: set[str] = set()            # busy 周期内真正出现过 error 的 Agent
        # 写者级「本轮出现过 error / 需要看一眼」标记（(agent, owner) → 写入序号）：
        # agent 级集合是聚合值，多实例下 A 的失败会把 B 的成功收尾打成失败口径。裁决见
        # _writer_marked（具名写者只认自己那份，无归属来源才回落 agent 级）。
        self._owner_error_marks: dict[tuple[str, str], int] = {}
        self._owner_alert_marks: dict[tuple[str, str], int] = {}
        # 收尾后爆雷（success/idle 已声明回合结束后，确认窗内才到的 error）：
        # 这些 agent 的 _saw_error 不得跨下一个忙入口存活——那个忙入口是新回合，
        # 不是「error→working 的同周期重试」（见 _abort_done_check_on_error）。
        self._error_after_done: set[str] = set()
        self._sound_last_at: dict[str, float] = {}
        self._sound_last_event: dict[str, tuple[str, float]] = {}
        self._link_seq = 0                           # 联动动作轮换计数
        # 过程汇报气泡：agent → (上次文案, 时刻)；全局最后一条时刻
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0
        self._phrase_picker = PhrasePicker()
        # Latest raw upstream record, exposed to dialogue templates.  This is
        # intentionally data-driven: newly added bridge fields become usable
        # without another per-event adapter change.
        self._dialogue_context: dict[str, Any] = {}
        # 最近一条工具记录（tool/call，含 target/callId/step/ok），按 agent 缓存：
        # 过程汇报气泡与 tool 信号同轮触发，用它把 target 等字段显式送进气泡，
        # 不再依赖「恰好是最后一条记录」的隐式上下文。
        self._last_tool_records: dict[str, dict[str, Any]] = {}
        self._model_access_tracker = ModelAccessTracker()
        # 待处理阻塞型交互：interaction_id → {"agent_key", "kind": "approval"|"question",
        # "text": str, "tool"?: str, "questions"?: list, "rpc_id"?, "approval_id"?,
        # "session_id"?, "alert_id", "delivered", "_ts_mono"}。审批 / 用户问题都是
        # 「阻塞 Agent 等待用户输入」的交互，统一处理：气泡「一直挂到 resolved」。
        # interaction_id = **来源 + kind + 会话 + 身份命名空间/值**（见
        # ``_interaction_key``），无身份时用 agent+kind+本地序号（降级提示路径）——
        # **同一 agent 的多个审批各自独立存储**，不再以 agent_key 为键互相覆盖；
        # resolved 按「来源 + 会话 + 身份」唯一候选精确匹配关闭
        # （见 ``_resolve_matching_interaction``），绝不错关闭其他并发/其他会话/
        # 其他 agent 的审批。
        # 本表是**有界**的（`_PENDING_INTERACTION_MAX`）：显示队列（64）只是呈现层，
        # 本地真值表同样必须有界，见 ``_admit_pending_interaction``。
        self._pending_interactions: dict[str, dict] = {}
        self._interaction_seq = 0  # 无 rpcId 的降级提示交互本地序号
        self._pending_interaction_overflow_count = 0  # 超限处置计数（可诊断）
        # 未交付常驻交互的补投表（惰性创建；无未交付条目时不存在/不跑）
        self._pending_redeliver_timer: QTimer | None = None

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

        # 阻塞交互兜底清理：会话/turn 结束或 agent 停止时，任何 pending 的
        # 审批/问题交互必然失效（DSH 漏发 resolved 的异常场景），据此清掉，
        # 防止真实异常也留下永久弹窗。
        self.monitors["dsh"].raw_record.connect(self._on_interaction_lifecycle)
        # 会话元数据缓存：sessionId → { sessionName, projectName, agentName }
        # 走有界写入（缺陷 23：key 是外部会话 ID，此前只增不减）
        self._session_meta_cache: dict[str, dict] = {}

        for mon in self.monitors.values():
            mon.raw_record.connect(self._remember_dialogue_record)
            mon.normalized_event.connect(self._on_normalized_event)
            mon.state_event.connect(self._on_agent_state_event)
            mon.activity_event.connect(self._on_agent_activity_event)
            mon.approval_requested.connect(self._on_approval_request)
            mon.approval_resolved.connect(self._on_approval_resolved)
            mon.question_requested.connect(self._on_question_request)
            mon.question_resolved.connect(self._on_question_resolved)
            mon.cordis_requested.connect(self._on_cordis_request)
            mon.cordis_resolved.connect(self._on_cordis_resolved)
            mon.execution_failed.connect(self._on_execution_failed)
        self.monitors["dsh"].session_meta.connect(self._on_session_meta)
        self.monitors["dsh"].model_access.connect(self._on_model_access)
        self.monitors["dsh"].llm_error.connect(self._on_llm_error)
        self.monitors["dsh"].user_action.connect(self._on_user_action)
        self.monitors["dsh"].unknown_bridge_event.connect(self._on_unknown_bridge_event)
        # DSH 收敛器（单读方第二消费者）输出：转发给 AppShell（灵动岛/收尾），
        # 并把 thinking/attention 注入既有呈现管线、offline 时收尾阻塞交互。
        self.monitors["dsh"].dsh_state_changed.connect(self._on_dsh_converged_state)
        self.monitors["dsh"].dsh_user_message.connect(self._on_dsh_converged_user_message)
        # live 回合收尾（真实记录身份）：DSH 完成提醒的判据——不依赖呈现管线里
        # 上一个状态是 busy/idle/None/success（见 _on_dsh_turn_completed）。
        self.monitors["dsh"].dsh_turn_completed.connect(self._on_dsh_turn_completed)
        # live 回合起点（同一份原记录的身份通道）：作废**本写者**待定的完成确认，
        # 并把这份忙记到它的分片上（聚合通道丢归属，见 _on_dsh_turn_started）。
        self.monitors["dsh"].dsh_turn_started.connect(self._on_dsh_turn_started)
        # 写者退役（桥文件写者已确认死亡）：只撤归属它的常驻交互与簿记，不报完成。
        self.monitors["dsh"].dsh_scope_retired.connect(self._on_dsh_scope_retired)
        # 重复投递的写者归属补登（去重不改：正文/音效仍只响一次，只补归属）。
        self.monitors["dsh"].bridge_owner_attested.connect(self._on_bridge_owner_attested)
        # 每 Agent 有界记忆：同一回合的收尾身份只排定一次完成确认
        self._dsh_turn_ids: dict[str, deque] = {}
        # 模型访问失败提醒缓存：session_key → { "count": int, "_ts": float, "_first_ts": float, "_dismissed": bool }
        self._model_access_cache: dict[str, dict] = {}
        self._model_access_timers: dict[str, QTimer] = {}   # session_key → 自动收起定时器
        self._model_access_retry_counts: dict[tuple[str, str], int] = {}
        self._model_access_anonymous_seq = 0
        # LLM API 错误缓存：session_key → { "_ts": float, "_dismissed": bool }
        self._llm_error_cache: dict[str, dict] = {}
        self._llm_error_timers: dict[str, QTimer] = {}
        self.install_finished.connect(self._on_install_finished)
        # 联动动作链：一次性动作播完后若仍有 Agent 在忙，由 window 回调取下一个动作
        if hasattr(self.win, "set_link_next_provider"):
            self.win.set_link_next_provider(self._next_busy_anim)

        self.apply_config()

    def _on_normalized_event(self, event) -> None:
        """Consume semantic events for streak tracking and interaction cleanup."""
        from ..agent_event_normalizer import InteractionResolvedEvent, RetryEvent
        if isinstance(event, RetryEvent):
            streak = self._model_access_tracker.consume(event)
            if streak:
                self._model_access_retry_counts[(event.source, event.session_id)] = int(streak["consecutiveRetryCount"])
            return
        # The tracker resets its streak on successful/lifecycle events.
        if getattr(event, "session_id", ""):
            self._model_access_tracker.consume(event)
            self._model_access_retry_counts.pop((event.source, event.session_id), None)
        if not isinstance(event, InteractionResolvedEvent):
            return
        matched = self._resolve_matching_interaction(
            kind=event.kind, agent_key=event.source, session_id=event.session_id,
            identities={
                "request_id": str(event.request_id or ""),
                "rpc_id": str(event.rpc_id or ""),
                "approval_id": str(event.approval_id or ""),
                "call_id": str(event.call_id or ""),
            },
        )
        if matched is None:
            log.debug("interaction/resolved unmatched source=%s session=%s",
                      event.source, event.session_id)

    def apply_config(self) -> None:
        """根据配置启停各个 Agent 监视器。

        注意用 _running（生命周期状态）而非 is_running()（会被 pause 置 False）——
        否则"隐藏期间关配置"不会真正 stop，恢复显示时又会被 resume 拉起。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}  # 脏配置（agent_link 不是 dict）：按全关处理，不抛
        # 手动指定的 pnpm 入口（config.pnpm_bin）：空 = 回到内置自动发现。
        # 在这里同步而非在探测时读配置，是为了让 agent_link 的探测保持
        # "纯函数 + 模块状态"的可测形态（不必到处传 cfg）。
        set_configured_pnpm_bin(self.cfg.get("pnpm_bin", ""))
        if not agent_cfg.get("dsh", False):
            self._clear_model_access_alerts()
        for key, monitor in self.monitors.items():
            should_run = bool(agent_cfg.get(key, False))
            if should_run and not monitor._running:
                # 换代次：上一代留给这个 Agent 的呈现簿记（在飞确认 / 硬失败标记 /
                # 原始状态 / 收尾身份）不得延续——否则「关掉再开启联动」后新代第一次
                # 真实收尾会被上一代的标记吞掉（L02），旧确认也可能以新代身份到期。
                self._retire_agent_presentation(key)
                monitor.start()
                if isinstance(monitor, DshMonitor):
                    # 启动自检（后台、每实例一次）：重新打包/换构建目录后，profile 里
                    # 记的 link 可能已指向旧构建；只在陈旧时刷新，绝不新建安装。
                    monitor.schedule_link_refresh_check()
            elif not should_run and monitor._running:
                monitor.stop()
                # 停即退役本 Agent 的呈现簿记：用户关掉联动之后，这一代排定的完成
                # 确认不得再弹（L01），硬失败/原始状态也不得留给下一代（L02）。
                # 只清本 Agent——别的 Agent 的确认与状态原样保留。
                self._retire_agent_presentation(key)
                if key == "dsh":
                    # 通道关闭语义（6.1sol 终审）：监视器停了之后，锁存在呈现侧的
                    # 状态（待确认环/错误点/工作点）再没有人会来改——必须随通道
                    # 一起清。复用 offline 收敛管线：岛状态点复位（AppShell 转发）
                    # 与常驻交互气泡一并收口，与"探测到 DSH 离线"同一路径。
                    self._on_dsh_converged_state("", "offline", "link-disabled")

    def _install_dsh_worker(self, token: int) -> None:
        """后台线程：安装 DSH 桥接插件，完成后信号回主线程。"""
        ok, msg = DshMonitor.install_bridge()
        if token != self._install_token:
            log.info("DSH 桥接安装结果已过期，丢弃")
            return
        try:
            self.install_finished.emit("dsh", ok, msg, token)
        except RuntimeError:
            log.debug("DSH 桥接安装完成但管理器已销毁，丢弃结果")

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
                self._dialogue("agent.missing", f"已开启 {name} 联动监听，但没检测到本机安装 {name}——装了它我才能感知到哦", agent_key=agent_key, name=name),
                duration_ms=6000,
            )

    def _on_install_finished(self, agent_key: str, ok: bool, msg: str,
                             token: int | None = None) -> None:
        """安装完成：成功则正式开启联动，失败则提示。"""
        if self._shutdown:
            return
        if token is not None and self._install_pending.get(agent_key) != token:
            log.info("安装完成回调已过期，丢弃: %s", agent_key)
            return
        if token is not None:
            self._install_pending.pop(agent_key, None)
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
        return other_instances_use_agent(self.cfg, agent_key)

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
                self._install_token += 1
                token = self._install_token
                self._install_pending["dsh"] = token
                if hasattr(self.win, "show_bubble"):
                    name = self.AGENT_NAMES.get(agent_key, agent_key)
                    self.win.show_bubble(self._dialogue("bridge.install.pending", "正在安装 DSH 桥接插件…", name=name), duration_ms=4000)
                import threading
                threading.Thread(
                    target=self._install_dsh_worker, args=(token,), daemon=True,
                    name="dsh-bridge-install",
                ).start()
                return False
        else:
            if agent_key == "dsh":
                self._install_pending.pop("dsh", None)
                self._install_token += 1
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
        if hasattr(self.win, "clear_pending_link_anim"):
            self.win.clear_pending_link_anim()
        for key in list(self._done_pending):
            self._cancel_done_check(key)
        self._sound_last_event.clear()

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器，并补投隐藏期间未能交付的常驻交互。

        补投是"隐藏期到达的审批/问题在恢复后仍能看到"的唯一恢复路径（显示 owner
        的队列在不可见宿主上收不到条目，只靠 owner 自己的恢复钩子无从知道）。
        """
        for mon in self.monitors.values():
            mon.resume()
        self.redeliver_pending_interactions()

    def shutdown(self) -> None:
        """窗口销毁/角色切换时停止所有 monitor worker，且作废安装回调。"""
        self._shutdown = True
        self._install_pending.clear()
        self._install_token += 1
        for mon in self.monitors.values():
            mon.begin_stop()
        active = [
            mon for mon in self.monitors.values()
            if mon._worker is not None and mon._worker.is_alive()
        ]
        if active:
            deadline = time.monotonic() + BaseAgentMonitor._STOP_JOIN_TIMEOUT_S
            for mon in active:
                mon.finish_stop(deadline)
        # 停掉 manager 自带的全部单发定时器（完成确认/模型访问失败收起/LLM 错误收起）：
        # 下方会把 Python 持有的 manager 过继给 QApplication，对象将存活到进程
        # 退出——若不停表，滞留定时器会在后续无关时刻触发槽函数。
        self._stop_pending_redeliver_timer()
        for timer_dict in (self._done_pending, self._model_access_timers, self._llm_error_timers):
            for timer in timer_dict.values():
                try:
                    timer.stop()
                except RuntimeError:
                    pass
            timer_dict.clear()
        self._done_cause.clear()
        # parent=None（测试桩/多窗代理）时 C++ 对象是 Python 持有的：wrapper 经
        # 信号连接/闭包成环，只能等循环 GC——而 GC 可能在任意线程（含 monitor
        # worker 线程）触发，跨线程删除带 QTimer 子对象/信号连接的 QObject 会
        # 腐化 Qt 事件队列（CI Windows 在 conftest processEvents access
        # violation、macOS bus error 的根因）。过继给 QApplication（主线程、
        # 与进程同寿）后，C++ 侧不再随 wrapper 的 GC 删除，wrapper 何时何线程
        # 回收都只是空壳析构。真窗口场景由父链销毁在先，RuntimeError 兜底跳过。
        AgentLinkManager._adopt_to_app_for_gc(self)

    @staticmethod
    def _adopt_to_app_for_gc(obj: "AgentLinkManager") -> None:
        """把 parent=None（测试桩/多窗代理）的 C++ 对象过继给 QApplication。

        仅接管 Python 侧生命周期，不改变业务状态：过继后 wrapper 在任何线程
        被循环 GC 回收时，C++ 侧都只是空壳析构，不会跨线程删除带 QTimer
        子对象/信号连接的 QObject（CI Windows interpreter 退出 access
        violation、macOS bus error 的根因，见 shutdown 注释）。真窗口场景由
        父链销毁在先，RuntimeError 兜底跳过。
        """
        try:
            app = QCoreApplication.instance()
            if obj.parent() is None and app is not None and obj.thread() is app.thread():
                obj.setParent(app)
        except RuntimeError:
            pass

    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """收口未由测试显式持有的管理器，避免监视器 worker 跨用例存活。"""
        for manager in tuple(_LIVE_AGENT_LINK_MANAGERS):
            try:
                manager.shutdown()
            except Exception:
                log.debug("测试收口 AgentLinkManager 失败", exc_info=True)

    def _gen_current(self, agent_key: str, gen: int) -> bool:
        mon = self.monitors.get(agent_key)
        return mon is not None and gen == mon._emit_gen

    def _dsh_convergence_current(self, gen: int | None) -> bool:
        """DSH 收敛信号闸：监视器在跑**且**信号代次仍当前才放行。

        worker 线程发出的收敛信号走 Qt 队列投递，监视器停止时已入队的旧信号
        撤销不掉（B1：只查 ``_running`` 就够）。但「停 → 启」之间同栈排队的
        信号更危险：新代次已经在跑，``_running`` 闸放它过去——上一代次的
        waiting_approval/error 于是把新代锁回陈旧状态，user_message 还会凭空
        外转一条真人消息（F3）。所以收敛信号统一携带发出时的代次，这里逐条
        对代次（``_gen_current``，同 :meth:`_on_dsh_turn_started` 的既有口径）。

        ``gen is None`` = 不经队列的同步直调（``link-disabled`` 人工复位与
        测试）：按当前代次处理，闸门退化为既有的 ``_running`` 检查。
        """
        mon = self.monitors.get("dsh")
        if mon is None or not mon._running:
            return False  # 迟到/孤儿信号：监视器已停（含停机后未重开）
        return gen is None or self._gen_current("dsh", gen)

    def _on_agent_state_event(self, event: AgentEvent) -> None:
        self._on_agent_state(event.agent, event.state, event.gen, owner=event.owner)

    def _on_agent_activity_event(self, event: AgentEvent) -> None:
        self._on_agent_activity(event.agent, event.tool, event.gen)

    def notify_dsh_state(self, state: str) -> None:
        """DSH 统一状态收敛注入（dsh_state 收敛器 → 既有呈现管线）。

        legacy AgentStatus 基线只有 working/idle（bridge 设计，见桥端
        STATE_EVENT_TYPES 注释），DSH 的 thinking 等状态对 legacy 状态映射
        结构性不可见——思考气泡因此从不触发。收敛器产出这些状态后
        经本方法喂给与监视器完全相同的主管线（去抖/节流/气泡/动画/完成检测）。

        联动未开启（DSH 监视器未运行）或代次不匹配时 no-op，绝不惊动用户。
        """
        mon = self.monitors.get("dsh")
        if mon is None or not mon._running:
            return
        self._on_agent_state("dsh", state, mon._emit_gen)

    def _on_dsh_converged_state(self, from_state: str, to_state: str,
                                source_event: str = "", gen: int | None = None) -> None:
        """订阅 DSH 收敛状态变化（单读方收敛器 → 呈现管线 + AppShell 转发）。

        **迟到信号隔离（astra 保底 B1 + F3 代次加固）**：worker 发出的收敛信号走
        Qt 队列投递，监视器停止时已入队的旧信号撤销不掉——若不拦，「关闭联动 →
        同步发 offline 复位 → 泵队列 → 旧 waiting_approval/error 到达」会把呈现侧
        重新锁回陈旧状态；「停 → 启」之间同栈排队的上一代次信号连 ``_running``
        都拦不住（新代次已在跑），同样会把新代锁回陈旧态。监视器不在跑时只放行
        人工复位（link-disabled），其余一律丢弃；信号代次不是当前代次的一律丢弃
        （``gen`` 口径见 :meth:`_dsh_convergence_current`）。DSH 真离线（offline）
        发生时监视器仍在跑且代次当前，不受此闸影响。

        - 转发给 AppShell（灵动岛 set_agent_active / offline 收尾）；
        - thinking：legacy AgentStatus 基线只有 working/idle，thinking 由收敛器
          补进联动管线（思考气泡/动画——对话开始的稳定触发点之一，与真人消息
          双保险，呈现管线自带同态去重）；
        - success：回合成功结束（桥接 turn/end）同样对 legacy 基线不可见，由本
          分支注入管线；完成气泡/done 音效由 ``_on_agent_state`` 的
          busy→success 边沿调度（800ms 确认 + 冷却），本方法不直接弹；
        - idle（来源 turn/end）：用户中止的回合——注入 idle 回待机，只撤 edge 来源
          的完成确认，交互气泡按会话定向清理（不全量清）；
        - waiting_approval：审批把 Agent 卡在等用户输入——计入「需要看一眼」
          （完成后不误报成功），并弹通用 attention 纯提示气泡（busy 中也必须
          提示，不走 ``_on_agent_state`` 的「busy 后不弹 attention」分支）。
          例外：cordis/request-run 与 approval/request 各有专属常驻提示气泡
          （`_on_cordis_request` / `_on_approval_request`），同一条事件的
          通用 attention 气泡跳过，防止双弹；
        - waiting_question 只更新状态（岛指示/动画），不弹通用气泡——
          常驻问题气泡已由 ``_on_question_request`` 呈现（同事件双弹回归）。

        **转发给岛的状态要过真实性翻译**（见 :meth:`_island_state_for`）：失败回合
        的 turn/end 在收敛器里已直接落 ERROR（F4），``_island_state_for`` 只兜
        「写了 execution/failed 但 reasonKind 缺失」的残留形状，不让岛亮绿点 +
        卡片「刚完成」与同屏的失败提醒自相矛盾。
        """
        # link-disabled 是同步直调的人工复位（apply_config 停监视器后立刻发），
        # 不走队列、也没有代次概念，照旧不受闸门约束。
        if source_event != "link-disabled" and not self._dsh_convergence_current(gen):
            return  # 迟到/孤儿/上一代次的排队信号，见 docstring 的隔离说明
        self.dsh_state_changed.emit(from_state, self._island_state_for(to_state))
        if to_state == "offline":
            # 写者随 DSH 一起消失：写者分片与原始状态镜像整片作废。不清的话旧实例
            # 留下的 "working" 会一直压着折叠值——DSH 重启后新写者的 idle 也盖不住
            # 它，表现为"永远忙"（动画一直忙、any_busy 恒真、空闲 FPS 门禁失效）。
            self._reset_owner_mirror("dsh")
            self.dismiss_all_interactions()
            return
        if to_state == "thinking":
            self.notify_dsh_state("thinking")
            return
        if to_state == "success":
            # 回合成功结束（桥接 turn/end → 收敛 success）：注入呈现管线，
            # 由 busy→success 边沿的完成确认（800ms + 冷却）出气泡/音效。
            # 此前没有这条分支，success 进不了管线——完成提醒永不触发（issue #234）。
            self.notify_dsh_state("success")
            return
        if to_state == "error":
            # 失败回合的收尾（F4：turn/end reasonKind=error 收敛为 ERROR，不再误报
            # success）。呈现管线对 busy→error 边沿已有完整口径（error 音效 +
            # 失败打标 + 收尾检查，见 _on_agent_state）；不注入的话 _last_raw 停在
            # thinking，桌宠一直索取忙碌动画、岛停在思考态直到下一回合。
            self.notify_dsh_state("error")
            return
        if to_state == "idle" and source_event == "turn/end":
            # 用户中止的回合（turn/end 携带 reasonKind=aborted，收敛器落 IDLE）：
            # 呈现侧必须回待机——否则 _last_raw 停在 thinking，桌宠一直索取忙碌动画、
            # 识屏也一直把 DSH 当忙。但 busy→idle 边沿会排定完成确认，中止不是完成，
            # 注入后立即撤掉本轮确认（含旧 AgentStatus idle 先到时排下的那一个）。
            # 例外：待定确认来源是 success（另一会话刚真实完成、确认窗未到期）——
            # 那个确认属于真实完成，不能被中止撤掉（按来源判，重复中止也不误撤）。
            self._idle_clear_scoped = True
            try:
                self.notify_dsh_state("idle")
            finally:
                self._idle_clear_scoped = False
            if self._done_cause.get("dsh") != "success":
                self._cancel_done_check("dsh")
            return
        if to_state == "waiting_approval":
            self._saw_alert.add("dsh")
            if source_event in ("cordis/request-run", "approval/request"):
                return  # 专属常驻气泡已覆盖这条审批，不再弹通用 attention
            self._show_link_bubble(
                self._dialogue("agent.attention", "主人，Agent 这边需要你看一眼～",
                               agent_key="dsh", name=self.AGENT_NAMES["dsh"]),
                important=True,
            )

    def _island_state_for(self, to_state: str) -> str:
        """收敛态 → 灵动岛状态：把「硬失败回合的假 success」翻译成 error。

        主路径已在收敛器侧分流（F4）：``reasonKind=error`` 的 turn/end 直接收敛为
        ERROR，不再经过 success。本翻译是兜底：桥端写了 ``execution/failed``、但
        turn/end 的 ``reasonKind`` 缺失或非 error 的残留形状下，收敛器仍会产出
        success——呈现在岛上的那一格必须说真话：原样转发会让
        岛走 ``set_dsh_state("success")`` → ``_arm_dot_done`` 点亮绿点 4s 并把状态
        文案写成「刚完成」，与同一拍弹出的失败提醒自相矛盾。

        判据复用既有的 ``_saw_error`` 标记：``_on_execution_failed`` 在同一批
        记录里先于 turn/end 落盘（GUI 侧按 agent 打标），真失败必然已置位。
        **只翻译 success 一个值**——其它态（含真 error）原样转发，正常回合没有该
        标记、绿点照旧；下一个回合一进 busy 就清标（见 ``_on_agent_state`` 的
        busy 入口），不需要额外复位。
        """
        if to_state == "success" and "dsh" in self._saw_error:
            return "error"
        return to_state

    def _on_dsh_converged_user_message(self, session_id: str, text: str,
                                       gen: int | None = None) -> None:
        """真人消息 = 对话开始：与状态边沿竞态解耦的稳定触发点。

        sourceKind 过滤已在收敛器完成——只有真人输入（含旧版桥接记录无字段的
        兼容）到这里；agent.inject() 注入上下文不会到这里。

        **与状态信号同一道闸**（:meth:`_dsh_convergence_current`）：这条也是队列
        投递的收敛信号，此前连 ``_running`` 都不查——停机后未重开时，队列里迟到
        的旧消息照样外转给 AppShell，凭空触发一次「对话开始」；「停 → 启」之间
        排队的上一代次消息同理。思考态注入（``notify_dsh_state``）自带监视器
        ``_running`` + 代次检查，闸门在这里是为了**外转那一句**本身。
        """
        if not self._dsh_convergence_current(gen):
            return  # 迟到/孤儿/上一代次的排队信号，见 _dsh_convergence_current
        self.dsh_user_message.emit(session_id, text)
        self.notify_dsh_state("thinking")

    def _on_dsh_turn_started(self, agent_key: str, payload: Any) -> None:
        """live 回合起点 → 按写者记账，并作废**这个写者**自己待定的完成确认。

        聚合通道（``dsh_state_changed``）丢掉了写者归属，起点在那里只剩"折叠值回忙"：
        确认归属的当事人自己的分片没有跟着变（起点不写分片）时，``_fire_done`` 的
        到期判据会把这份忙整个算在**还在忙的别的写者**头上——A 已开新回合仍弹
        「已完成本轮任务」（本票修掉的残留）；反过来，别的写者一开新回合就会把 B 的
        完成确认误撤（忙无归属可查）。所以在原记录这一层补两件事：① 起点落进本写者
        分片（给折叠忙一个归属）；② 确认属于本写者时立即作废——**只撤自己的**。
        """
        if self._shutdown:
            return
        mon = self.monitors.get(agent_key)
        if mon is None or not mon._running:
            return  # 迟到/孤儿信号：监视器已停（同 _on_dsh_turn_completed 的隔离口径）
        payload = payload if isinstance(payload, dict) else {}
        owner = str(payload.get("owner") or "")
        if not owner or not self._gen_current(agent_key, int(payload.get("gen") or 0)):
            return
        # 只记分片、不动 _last_raw：聚合值由同一条记录的收敛注入负责（thinking），
        # 这里重复写会把 waiting_approval 这类锁存值顶掉。
        self._owner_seq += 1
        self._owner_raw[(agent_key, owner)] = ("thinking", self._owner_seq)
        self._trim_owner_raw()
        self._cancel_done_check_for_owner(agent_key, owner)

    def _on_dsh_turn_completed(self, agent_key: str, payload: Any) -> None:
        """live 回合成功收尾（桥接 ``turn/end``）→ 排定完成确认。

        **判据是这条 live 记录的身份，不是呈现管线里上一个状态**（身份构造见
        ``dsh_state.TurnCompletionTracker``）。此前的判据是 ``_on_agent_state`` 的
        ``busy→success`` 边沿，它漏掉了所有「上一个状态不是 busy」的真实来向：

        - 中途挂载：``DirGlobTailer`` 回填跳过整段历史，管线从没见过该 Agent
          （prev=None）；
        - 聚合基线先到：桥接的 ``AgentStatus{idle}`` 与 session/event 是两条通道，
          idle 可能先落地（prev="idle"，且来向不是 busy，busy→idle 也不排定）；
        - 收敛器已停在 SUCCESS：新会话/新桥文件的收尾是同状态重复，edge-trigger
          的收敛器零产出，呈现侧根本没有 success 可注入；
        - 关开联动：``_last_raw`` 不随监视器生命周期清空，残留的是上一轮的终态。

        四种来向这一个入口全收；判据换成 live 事件身份后不再逐条补状态特判。

        三道既有闸原样保留：
        - **身份去重**：同一回合约投递只排定一次（多文件各写一份、重复帧）；
        - **硬失败闸**：``execution/failed`` 已打 ``_saw_error``，失败回合的收尾不出
          成功口径（桥接先写失败记录再转发 turn/end，顺序保证已置位）。判据按
          **写者归属**读（``_error_marked``）：失败是某一写者本轮的事实，A 实例的
          失败不得吞掉 B 实例的成功收尾；
        - **800ms 确认 + 5s 冷却 + ``_fire_done``**：复用既有链路，零新增定时器。

        **不篡改全局忙闲口径**（多实例）：A 的回合结束是正面信息（确实完成了），
        该提醒就提醒；但 ``_last_raw`` 是**全写者折叠后**的值，不能被这一条记录
        直接写成 success——B 还在忙时那会把整体判成"待机"（动画切回待机、
        ``any_busy()`` 变假、空闲 FPS 门禁被打开）。这里只把**本写者**的分片更新
        成终态，``_last_raw`` 取折叠结果：单实例下折叠值就是 success（行为不变），
        多实例下其它写者还在忙就仍是 working。完成上下文按 owner 归属记账，
        不靠改全局状态。
        """
        if self._shutdown:
            return
        mon = self.monitors.get("dsh")
        if mon is None or not mon._running:
            return  # 迟到/孤儿信号：监视器已停（同 _on_dsh_converged_state 的隔离口径）
        payload = payload if isinstance(payload, dict) else {}
        if not self._gen_current(agent_key, int(payload.get("gen") or 0)):
            return  # 旧代次的队列信号不得惊动用户
        identity = payload.get("identity")
        if identity is not None:
            seen = self._dsh_turn_ids.get(agent_key)
            if seen is None:
                seen = self._dsh_turn_ids[agent_key] = deque(maxlen=self._DSH_TURN_ID_MEMORY)
            if identity in seen:
                return  # 同一回合的重复投递：只排定一次
            seen.append(identity)
        owner = str(payload.get("owner") or "")
        if self._error_marked(agent_key, owner):
            return  # 硬失败回合：失败提醒已经收口，不得再报成功（按写者归属裁决）
        if owner:
            # 只把**本写者**的分片更新成终态；别的写者的分片原样保留（A 的收尾不该
            # 替还在忙的 B 发言）。
            self._owner_seq += 1
            self._owner_raw[(agent_key, owner)] = ("success", self._owner_seq)
            self._trim_owner_raw()
        # _last_raw 取折叠值：单写者 = success（与旧行为一致），还有写者忙/等待时
        # 保持原值（不切待机动画、any_busy 仍为真、空闲 FPS 门禁保持关闭）。
        self._last_raw[agent_key] = self._folded_raw_state(agent_key) or "success"
        if agent_key not in self._done_pending:
            self._schedule_done_check(agent_key, cause="success", owner=owner)
        else:
            self._done_cause[agent_key] = "success"
            if owner:
                self._done_owner[agent_key] = owner

    #: 写者分片记账表（``_owner_raw``）的容量：写者数量本身由收敛器的 scope 注册表
    #: 限死（见 ``dsh_state.SCOPE_REGISTRY_MAX``），这里是第二道有界闸（覆盖多
    #: agent/多代次叠加的极端情形），超出按插入次序淘汰最老的。
    _OWNER_RAW_MAX = 128

    def _trim_owner_raw(self) -> None:
        while len(self._owner_raw) > self._OWNER_RAW_MAX:
            self._owner_raw.pop(next(iter(self._owner_raw)), None)

    #: 写者级标记表的容量：与 ``_OWNER_RAW_MAX`` 同口径，超出按插入次序淘汰——被淘汰
    #: 只丢"某写者这一轮的失败口径"（最坏退回 agent 级聚合裁决），不动归属真值。
    _OWNER_MARK_MAX = 128

    def _mark_owner(self, marks: dict[tuple[str, str], int], agent_key: str,
                    owner: str, *, on: bool) -> None:
        """按归属写/清一个写者级标记（``owner`` 为空 = 无归属来源，只走 agent 级）。"""
        if not owner:
            return
        key = (agent_key, owner)
        if not on:
            marks.pop(key, None)
            return
        self._owner_seq += 1
        marks[key] = self._owner_seq
        while len(marks) > self._OWNER_MARK_MAX:
            marks.pop(next(iter(marks)), None)

    def _drop_owner_marks(self, agent_key: str, owner: str | None = None) -> None:
        """清掉某个（或该 agent 全部）写者的标记；随写者退役/整代退役调用。"""
        for marks in (self._owner_error_marks, self._owner_alert_marks):
            for key in [k for k in marks
                        if k[0] == agent_key and (owner is None or k[1] == owner)]:
                marks.pop(key, None)

    def _writer_marked(self, marks: dict[tuple[str, str], int], agent_marks: set[str],
                       agent_key: str, owner: str) -> bool:
        """写者级裁决：具名写者只认自己那份标记，其余情况才回落 agent 级聚合值。

        ``owner`` 为空（归属未知的直调路径、收敛器聚合输出）读 agent 级集合，语义不变；
        具名则只看 ``(agent, owner)``，**但**该 agent 一个归属标记都没有时回落 agent 级
        ——那说明标记全来自无归属来源，不能因为多实例改造把它们静默丢掉。
        """
        if not owner:
            return agent_key in agent_marks
        if (agent_key, owner) in marks:
            return True
        if agent_key not in agent_marks:
            return False
        return not any(key[0] == agent_key for key in marks)

    def _error_marked(self, agent_key: str, owner: str = "") -> bool:
        """这条收尾/这个写者是否带**硬失败**口径（gate 与收尾音效判据）。"""
        return self._writer_marked(self._owner_error_marks, self._saw_error, agent_key, owner)

    def _alert_marked(self, agent_key: str, owner: str = "") -> bool:
        """这个写者的收尾是否要切离"已完成"文案（busy 周期见过 attention/error）。"""
        return self._writer_marked(self._owner_alert_marks, self._saw_alert, agent_key, owner)

    def _cancel_done_check_for_owner(self, agent_key: str, owner: str) -> None:
        """回忙撤销待定的完成确认：只有**当事人**回忙才算，别的写者不算。

        B 的 ``AgentStatus working`` 会让折叠值整体变忙，但那个忙不是 A 的当事人事实
        ——拿折叠值撤确认就是让 B 替 A 取消提醒，而 A 的回合身份已进 ``_dsh_turn_ids``
        （不会再重排），这条提醒永久丢失。归属未知（``owner`` 为空 = 聚合注入）与无归属
        确认（``pending_owner`` 为空）照旧撤：那是"分不清当事人"的旧路径。``owner`` 为
        空的聚合忙值不在这里判归谁——那一刻别的写者的分片可能还没落地，统一留到
        ``_fire_done`` 到期判（见 :meth:`_other_writer_busy`）。
        """
        if agent_key not in self._done_pending:
            return
        pending_owner = self._done_owner.get(agent_key, "")
        if pending_owner and owner != pending_owner:
            return
        self._cancel_done_check(agent_key)

    def _other_writer_busy(self, agent_key: str, owner: str) -> bool:
        """该 Agent 有没有**别的**写者正忙（用于裁决聚合忙值归谁）。

        多实例下折叠值（``_last_raw``）变忙可能来自别的写者；确认归属具名时只有
        "别的人都不忙"才说明这个忙是当事人自己那一侧（``_fire_done`` 据此作废）。
        """
        return any(
            key == agent_key and writer != owner and state in self._BUSY_STATES
            for (key, writer), (state, _seq) in self._owner_raw.items()
        )

    def _folded_raw_state(self, agent_key: str) -> str:
        """该 Agent 的写者分片折叠成一个呈现态（无分片返回 ""）。

        唯一的折叠实现：``_on_agent_state`` 的入边、``_on_dsh_turn_completed`` 的
        终态落账、写者退役都走它——三处若各写一遍，迟早出现"某个入口没折叠"的
        漏网（A 回待机压掉 B 的忙就是这么来的）。
        """
        return fold_writer_states(
            value for (key, _owner), value in self._owner_raw.items() if key == agent_key)

    def _drop_owner_raw(self, agent_key: str, owner: str) -> None:
        """丢掉一个写者的分片记账，并把折叠结果同步进 ``_last_raw``。

        写者退役（死亡）后它的分片不该继续参与折叠（否则整个 Agent 会永远停在它
        留下的"忙"上）。这里只更新簿记，**不注入呈现管线、不排定完成确认**：写者
        消失不是回合收尾。

        写者级标记（``_owner_error_marks`` / ``_owner_alert_marks``）**先清**：失败
        标记可以只由 ``execution/failed`` 打上（不建分片），那种写者同样要随退役把
        标记带走，不然会一直留到 agent 整代退役或标记表 FIFO 淘汰才消失。
        """
        self._drop_owner_marks(agent_key, owner)
        if self._owner_raw.pop((agent_key, owner), None) is None:
            return
        folded = self._folded_raw_state(agent_key)
        if folded:
            self._last_raw[agent_key] = folded
        else:
            self._last_raw.pop(agent_key, None)

    def _reset_owner_mirror(self, agent_key: str) -> None:
        """清掉该 Agent 的全部写者分片与原始状态镜像（DSH 整体离线时调用）。

        写者随 DSH 一起消失，分片留着就是"永远忙"：新 DSH 重启是新写者，它的
        idle 会让折叠值继续被旧分片的 working 压住（``any_busy()`` 恒真、动画
        一直忙）。整片清掉 = 下一代从"没见过状态"开始，与 ``_retire_agent_presentation``
        对其它簿记的口径一致。
        """
        for key in [k for k in self._owner_raw if k[0] == agent_key]:
            self._owner_raw.pop(key, None)
        self._drop_owner_marks(agent_key)
        self._last_raw.pop(agent_key, None)
        self._last_applied.pop(agent_key, None)

    @staticmethod
    def _record_writer_owner(record: dict) -> str | None:
        """记录上的写者归属标注（摄入侧盖的 ``_scope``）；没有标注返回 None。

        与 :meth:`_release_owner` 的 None/"" 口径配对：``None`` = 归属未知
        （直调/非 ingest 路径，走通配旧行为）；``""`` = 旧无 owner 生产桶（只与
        同样无归属的条目互认，不得替具名写者做决定）。
        """
        if not isinstance(record, dict):
            return None
        return record.get(_BRIDGE_OWNER_KEY)

    def _release_owner(self, interaction_id: str, owner: str | None,
                       *, trusted: bool = False, session: str = "") -> bool:
        """把某个写者从一条待处理交互的归属集合里撤掉；True = 该条已无归属，应关闭。

        - ``owner is None``（**归属未知**的通配口径：直调、非摄入路径的旧行为）→
          直接判定该条应关闭；
        - ``owner == ""``（**旧无 owner 生产桶**：记录没有写者归属）→ 只清同样没
          有归属的条目，**不得**清掉具名写者的条目（分不清归属就不许替它做决定）；
        - 具名写者 → 只有当它确实在该条目的归属集合里才撤；撤完集合非空则**保留**
          （另一个写者也在等这条交互，先回待机的那个不能替它撤）。

        归属集合 overflow 锁存后（见 :meth:`_merge_pending_owner`）判据收紧成"**可
        指认的来源身份 + 同一条会话**"：那条的旧归属本来就没记全，"我记到的都走了"
        或"某个别 owner 的回合结束了"都不构成收口证据——一个别 owner 的结束帧一声
        清掉整条，等于把还有人在等的提醒一起撤了。未知来源（无归属标注）的 idle /
        死亡退役 / 结束帧一律不收，只由 ``resolved``（准确 rpc/call 身份）收口。
        """
        item = self._pending_interactions.get(interaction_id)
        if item is None:
            return False
        if item.get("owners_overflow"):
            if not trusted or not owner:
                return False
            if str(owner) not in (item.get("owners") or set()):
                return False
            return bool(session) and session == str(item.get("session_id") or "")
        if owner is None:
            return True
        owners = item.get("owners")
        if not owners:
            return owner == ""
        if owner not in owners:
            return False
        owners.discard(owner)
        return not owners

    def _clear_owned_interactions(self, agent_key: str, owner: str | None,
                                  session: str = "", *, trusted: bool = False) -> int:
        """按写者归属收口该 agent 的待处理交互，返回关闭条数。

        ``session`` 非空时只清「会话相同或会话未知」的条目（旧行为：会话不匹配的
        条目保留）。归属裁决全部走 :meth:`_release_owner`——这是"别写者的 idle /
        别的实例的 turn/end 不得撤掉这一写者还在等的交互"的唯一落地点。
        """
        closed = 0
        for iid in [i for i, v in self._pending_interactions.items()
                    if v.get("agent_key") == agent_key
                    and (not session or not v.get("session_id") or v.get("session_id") == session)]:
            if self._release_owner(iid, owner, trusted=trusted, session=session):
                self._resolve_interaction(iid)
                closed += 1
        return closed

    def _on_agent_state(self, agent_key: str, state: str, gen: int = 0,
                        owner: str | None = None) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。

        ``owner`` 是状态的写者归属（多 DSH 实例分区）：给了归属就按**写者分片**
        记账并折叠（见 :func:`dsh_state.fold_writer_states`），呈现层只读折叠值；
        ``None`` = 归属未知（直调/收敛器的聚合输出），沿用旧的整条覆盖口径。
        """
        if not self._gen_current(agent_key, gen):
            return
        # 这一写者更新前的分片值（清标记的"同周期重试"判据要用它，必须在下面写分片之前取）
        prev_owner = self._owner_raw.get((agent_key, owner), ("", 0))[0] if owner is not None else ""
        if owner is not None:
            # 写者分片记账 + 折叠：一个实例回待机时，另一个实例还在忙就**不**让整体
            # 变待机。不做这一步的话，A 的 idle 会把 _last_raw 写成 idle → busy→idle
            # 边沿排定完成确认（假完成）并把动画切回待机。
            self._owner_seq += 1
            self._owner_raw[(agent_key, owner)] = (state, self._owner_seq)
            self._trim_owner_raw()
            folded = self._folded_raw_state(agent_key)
            if folded:
                state = folded
        # 兜底：该 agent 已回待机（任务结束）但审批/问题还没收到 resolved → 交互必然失效。
        # 放在可见性判断之前：窗口隐藏期间也要清 pending，避免恢复显示时挂出陈旧气泡。
        if state in ("idle", "sleeping") and not self._idle_clear_scoped:
            # 按交互 id 遍历清理（同一 agent 可能有多个并发审批/问题），归属按写者裁决
            self._clear_owned_interactions(agent_key, owner)

        # 桌宠隐藏：动画/声音不呈现，但状态簿记（_last_raw / 成本 / 完成确认
        # 调度）照常推进——岛反馈面可用时气泡经 _show_link_bubble 改道灵动岛
        # （无注入时维持丢弃）。此前整段 return 会连簿记一起丢，隐藏期间
        # start/done 反馈气泡全部消失（岛反馈面引入后用户实测）。
        hidden = not hasattr(self.win, "isVisible") or not self.win.isVisible()

        mark = getattr(self.win, "mark_activity", None)
        if callable(mark) and not hidden:
            mark()

        now = self._clock()
        # --- 原始状态流（绕开去抖/节流）：busy→idle 完成检测 ---
        # 不能用 _last_applied 判定完成——节流会丢掉紧跟的 idle，导致完成通知丢失。
        prev_raw = self._last_raw.get(agent_key)
        self._last_raw[agent_key] = state
        if state in self._BUSY_STATES and prev_raw not in self._BUSY_STATES:
            if not hidden:
                self._emit_sound("start", agent_key)
            self._cost_note_start(agent_key)
        elif state == "error" and prev_raw != "error":
            if not hidden:
                self._emit_sound("error", agent_key)
        if state in self._BUSY_STATES:
            # 回忙撤销只对**当事人**生效：B 的忙不得替 A 撤掉 A 的完成确认
            self._cancel_done_check_for_owner(agent_key, owner or "")
            self._saw_alert.discard(agent_key)
            self._mark_owner(self._owner_alert_marks, agent_key, owner or "", on=False)
            # _saw_error 跨 error→working 存活是为「error 后同周期重试」留的
            # （重试后收尾仍按失败口径）；但收尾后爆雷的 error 之后不是重试而是
            # **新回合**，标记必须随新回合清除，否则下一回合的成功收尾被整个
            # 吃掉（既无成功气泡也无失败口径）。见 _abort_done_check_on_error。
            # 写者级标记用同款判据、各自的分片（prev_owner），互不代清。
            if prev_raw != "error" or agent_key in self._error_after_done:
                self._saw_error.discard(agent_key)
            if prev_owner != "error" or agent_key in self._error_after_done:
                self._mark_owner(self._owner_error_marks, agent_key, owner or "", on=False)
            self._error_after_done.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in self._BUSY_STATES:
            self._saw_alert.add(agent_key)
            self._mark_owner(self._owner_alert_marks, agent_key, owner or "", on=True)
            if state == "error":
                self._saw_error.add(agent_key)
                self._mark_owner(self._owner_error_marks, agent_key, owner or "", on=True)
            # Claude 的回合结束信号是 Stop→attention 而非 idle：busy 后的
            # attention/error 同样进入完成确认（800ms 内回忙则取消——例如
            # SubagentStop 后主 Agent 继续干活、工具报错后重试）。
            self._schedule_done_check(agent_key, owner=owner or "")
        elif state == "error":
            # 收尾后爆雷：prev_raw 已不是 busy——收尾边沿（success/idle/attention）
            # 已经声明本轮结束，确认窗内到达的 error 必须取消 pending 的完成确认
            # 并按失败口径打标。上一条 elif 已经吃掉「来向是 busy」的 error
            # （那里的确认由 error 自己排定），所以到这里的一定是收尾之后才爆的雷。
            self._abort_done_check_on_error(agent_key, owner or "")
        elif state in ("idle", "sleeping") and prev_raw in self._BUSY_STATES:
            # working/thinking → idle：疑似任务完成，800ms 稳定确认
            # （过滤 working→idle→working 抖动；确认期间回忙则取消）
            self._schedule_done_check(agent_key, owner=owner or "")
        elif state == "success" and prev_raw in self._BUSY_STATES:
            # DSH 回合成功结束（收敛器 success，见 _on_dsh_converged_state）：
            # 与 busy→idle 同一条完成确认（800ms + 冷却 + _fire_done），
            # 零新增定时器。success 只存在于这条原始状态流（_last_raw），
            # 不进 VALID_STATES——那是所有监视器共用的归一入口。
            #
            # 分工：本边沿覆盖「完整观测到一个回合」这一条路径（发出顺序上先于
            # live 身份通道排定，见 DshMonitor._on_parsed_record）；上一个状态不是
            # busy 的四种真实来向（中途挂载 prev=None、聚合基线 idle 先到、收敛器
            # 已停在 SUCCESS 的同状态收尾、关开联动残留终态）在这里全都看不到，
            # 由 ``_on_dsh_turn_completed`` 的 live 事件身份收口——不要再往这条
            # 条件里补状态特判。
            # 例外：本 busy 周期出现过硬失败（execution/failed）——失败提醒已经
            # 收口，紧跟的这条 turn/end 不得再排定完成确认。桥接判定失败后先写
            # execution/failed，随后仍无条件转发 turn/end，收敛器把非中止的 turn/end
            # 收敛为 success；不拦就是失败回合弹「已完成本轮任务」+ done 音效
            # （误报成功），且那 800ms 还会占掉完成冷却、吃掉下一回合的真实完成
            # 提醒。标记在下一个 busy 入口清除，下一回合照常恢复。
            # 判据按**写者归属**：失败是某一写者本轮的回合事实，只挡它自己那条
            # 收尾——A 实例的失败不得吞掉 B 实例的成功（见 _error_marked）。
            if not self._error_marked(agent_key, owner or ""):
                self._schedule_done_check(agent_key, cause="success", owner=owner or "")

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
            # 经 request_link_anim 平滑衔接：正在播的一次性动作不被打断。
            # 隐藏中不切动画（零功耗语义），气泡仍经改道上岛。
            anim = self._next_link_anim_rotation()
            if anim and not hidden and hasattr(self.win, "request_link_anim"):
                self.win.request_link_anim(anim)
            self._maybe_notify_start(agent_key, prev_raw, state)
        elif state == "attention":
            # busy 后的 attention（如 Claude Stop=回合结束）由完成确认流程接管，
            # 避免「需要看一眼」和「完成通知」双气泡；独立出现的才立即提醒
            if prev_raw not in self._BUSY_STATES:
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self._show_link_bubble(self._dialogue("agent.attention", "主人，Agent 这边需要你看一眼～", agent_key=agent_key, name=name), important=True)
        elif state == "error":
            if prev_raw not in self._BUSY_STATES:
                name = self.AGENT_NAMES.get(agent_key, agent_key)
                self._show_link_bubble(self._dialogue("agent.error", "Agent 执行好像遇到报错了…", agent_key=agent_key, name=name), important=True)
        elif state in ("sleeping", "idle"):
            # 回到待机：一次性动作播完自然回，待机/移动中立即回（隐藏中不切）
            if not hidden:
                if hasattr(self.win, "request_link_idle"):
                    self.win.request_link_idle()
                elif hasattr(self.win, "switch_clip") and getattr(self.win, "idles", None):
                    self.win.switch_clip(self.win.idles[0])

    # ------------------------------------------------------------------
    # 联动动作池（写代码/吃Token 交替为主，每第 3 次插播短摸鱼）
    # ------------------------------------------------------------------
    _LINK_MAIN = ("写代码", "吃Token")
    _LINK_BREAK = ("轻快记录", "漂浮踏步")
    _LINK_MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
    _LINK_BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

    def any_busy(self) -> bool:
        """Return whether an enabled monitor currently reports active work.

        ``_last_raw`` intentionally keeps the latest state after a monitor is
        stopped, so it must be paired with the monitor lifecycle flag here.
        Using ``is_running()`` would incorrectly treat a paused monitor as
        disabled; ``_running`` is the lifecycle state used by ``apply_config``
        and is therefore the authoritative check for the idle-FPS gate.
        """
        return any(
            bool(getattr(monitor, "_running", False))
            and self._last_raw.get(agent_key) in self._BUSY_STATES
            for agent_key, monitor in self.monitors.items()
        )

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
    AGENT_PROCESS_HINTS = {
        "opencode": ("opencode.exe",),
        "cursor": ("cursor.exe",),
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

    def _remember_dialogue_record(self, agent_key: str, record: object) -> None:
        """Expose the latest upstream record to phrase templates."""
        if not isinstance(record, dict):
            return
        context = dict(record)
        context.setdefault("agent_key", agent_key)
        context.setdefault("agent", agent_key)
        context.setdefault("agent_name", self.agent_names.get(agent_key, agent_key))
        self._dialogue_context = context
        if str(context.get("tool") or "").strip() or str(context.get("event") or "") == "tool/call":
            self._last_tool_records[agent_key] = context

    def _dialogue(self, key: str, fallback: str, *, agent_key: str = "", **values) -> str:
        """Render an event with explicit aliases plus latest upstream fields.

        ``agent_key``（默认 ""=非 Agent/全局场景）用于统一预设路由：
        custom 模式下按 ``agents[agent_key][key] → global[key] → 内置`` 取文案；
        传空时只读 global（兼容旧单层自定义台词）。
        """
        merged = dict(self._dialogue_context)
        merged.update(values)
        # 条件参数（CONDITIONAL_PARAMETERS）：上游未提供/为空/为 null 时渲染端
        # 自动隐藏对应占位符，不原样露出 {xxx}。LEGACY_HIDDEN_PARAMETERS 是
        # 减法退役字段（command/argsKey/reasons）的全局兜底：旧自定义台词里的
        # 这些占位符在任何事件下都隐藏。
        autohide = tuple(CONDITIONAL_PARAMETERS.get(key, ())) + LEGACY_HIDDEN_PARAMETERS
        mode = str(self.cfg.get("dialogue_mode", "legacy") or "legacy")
        if mode == "custom":
            return self._phrase_picker.custom_for_agent(self.cfg.get("dialogue_phrases", {}), agent_key, key,
                                                        fallback, autohide=autohide, **merged)
        return self._phrase_picker.get(mode, key, fallback, autohide=autohide, **merged)

    def _session_conditional(self, record: dict) -> dict[str, str]:
        """从记录提取条件会话字段（缺失/为空不注入，渲染端自动隐藏占位符）。

        返回 sessionName（会话名）/ projectName（项目名）/ label（会话标签），
        三者语义独立：sessionName 只取会话自己的名字，绝不拼进 projectName
        （否则 {sessionName} 与 {projectName} 两字段语义重复）。sessionId 存在
        且记录缺字段时，从会话元数据缓存补齐（只补真实字段，不编造展示串）。
        注意 label 同名双义：activity.*/approval.tool 的 label 是工具标签，
        由调用点显式传入——那些调用点不要用本方法返回值覆盖 label。
        """
        record = record if isinstance(record, dict) else {}
        vals: dict[str, str] = {}
        for field in ("projectName", "sessionName", "label"):
            value = str(record.get(field) or "").strip()
            if value:
                vals[field] = value
        session_id = str(record.get("sessionId") or "").strip()
        if session_id:
            if "sessionName" not in vals:
                session_name = self._session_name_or_empty(session_id)
                if session_name:
                    vals["sessionName"] = session_name
            if "projectName" not in vals:
                meta = self._session_meta_cache.get(session_id) or {}
                project_name = str(meta.get("projectName") or "").strip()
                if project_name:
                    vals["projectName"] = project_name
        return vals

    def _thinking_text(self, agent_key: str) -> str:
        """thinking 气泡文案：统一预设 agents delta/global > 旧 per-Agent 自定义 > 内置默认。"""
        agent_cfg = self.cfg.get("agent_link", {})
        name = self.agent_names.get(
            agent_key,
            self.AGENT_NAMES.get(agent_key, agent_key),
        )

        # 1) 统一预设（dialogue_mode=custom 且配置了 agents/global 时优先）
        mode = str(self.cfg.get("dialogue_mode", "legacy") or "legacy")
        if mode == "custom":
            preset = self.cfg.get("dialogue_phrases", {})
            if isinstance(preset, dict) and ("global" in preset or "agents" in preset):
                custom = self._phrase_picker.custom_for_agent(
                    preset, agent_key, "thinking", "",
                    name=name,
                )
                if custom:
                    return custom

        # 2) 旧 per-Agent / 全局自定义（agent_link.thinking_texts / thinking_text）
        custom = (agent_cfg.get("thinking_texts") or {}).get(agent_key, "").strip()
        if not custom:
            custom = str(agent_cfg.get("thinking_text", "") or "").strip()
        if custom:
            return custom.replace("{name}", name)

        # 3) 内置默认
        if agent_key in self._THINKING_DEFAULTS:
            fallback = self._THINKING_DEFAULTS[agent_key]
            return self._dialogue("thinking", fallback, agent_key=agent_key, name=name)

        return self._dialogue(
            "thinking",
            f"{name} 正在深度烧烤……",
            agent_key=agent_key,
            name=name,
        )

    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None, state: str = "working") -> None:
        """开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
        低优先级：气泡位被占时直接丢弃。thinking 状态用更有趣的文案。"""
        if prev_raw in self._BUSY_STATES:
            return
        name = self.agent_names.get(agent_key, agent_key)
        if state == "thinking":
            self._show_link_bubble(self._thinking_text(agent_key), important=False, duration_ms=3000)
        else:
            self._show_link_bubble(
                self._dialogue("start", f"{name} 开始干活啦～", agent_key=agent_key, name=name),
                important=False, duration_ms=3000,
            )

    def _on_agent_activity(self, agent_key: str, tool: str, gen: int = 0) -> None:
        """过程汇报气泡（可选，默认关）：「DSH 正在读文件…」这类。
        白名单工具映射 + 三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。"""
        if not self._gen_current(agent_key, gen):
            return
        mark = getattr(self.win, "mark_activity", None)
        if callable(mark):
            mark()
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
        name = self.agent_names.get(agent_key, agent_key)
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
        # tool 信号与 tool/call 记录同轮到达（监视器 _poll 先发 raw_record 再发
        # activity），按 agent 取最近一条工具记录，把 callId/step 显式
        # 送进气泡；缺失的字段不传，占位符保持原样（不注入空串撑脏文案）。
        tool_record = self._last_tool_records.get(agent_key) or {}
        # tool/call 记录字段：callId/step + 会话字段（缺失不注入，渲染端自动隐藏
        # 占位符）。脱敏口径：桥接不再落 command/argsKey 明文，这里也不注入。
        # target/ok 不在 tool/call 记录里，不读取。
        # label 同名双义：activity 的 label=工具标签，须后写覆盖会话标签。
        values: dict[str, Any] = dict(self._session_conditional(tool_record))
        for field in ("callId", "step"):
            value = tool_record.get(field)
            if value not in (None, ""):
                values[field] = value
        values["name"] = name
        values["tool"] = str(tool).strip()
        values["label"] = label
        # 源头截断：用户自定义文案模板可能很长，过程汇报气泡只做
        # 一句提示，超过 _ACTIVITY_TEXT_LIMIT 字就截断加「…」（不进分页/滚动）。
        text = truncate_bubble_text(
            self._dialogue(key, f"{name} {label}…", agent_key=agent_key, **values),
            self._ACTIVITY_TEXT_LIMIT,
        )
        self._show_link_bubble(text, important=False, duration_ms=2600)

    def _on_approval_request(self, agent_key: str, payload: dict) -> None:
        """审批请求提醒：一直挂到审批结束的纯提示气泡（减法后无代点按钮）。

        只登记**具备可关联身份**的审批（rpcId / approvalId / requestId / callId
        任一非空）：这类记录能与其 approval/resolved 配对精确关闭。

        无任何稳定身份的记录（如普通工具调用被误标 approval/asked 后的桥接
        残留）无法与 resolved 配对，创建 sticky 弹窗必然无法关闭——直接忽略。

        脱敏口径：桥接不再落被审批命令的明文（command 字段已删），气泡只提示
        「有审批等你决定」，具体命令到 DSH 界面查看。"""
        payload = payload if isinstance(payload, dict) else {}
        # 可关联身份门禁：无 rpcId/approvalId/requestId/callId 一律不弹窗。
        if not (payload.get("rpcId") or payload.get("approvalId")
                or payload.get("requestId") or payload.get("callId")):
            log.debug("approval/request 缺可关联身份，忽略（不弹窗）: %s", str(payload)[:200])
            return
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        tool = str(payload.get("toolName") or payload.get("tool") or "").strip()
        session_id = str(payload.get("sessionId") or "")
        session_display = self.get_session_display_name(session_id) if session_id else ""
        prefix = f"{session_display} · " if session_display and session_display != f"DSH · {session_id[:8]}" else ""
        # 条件会话字段 + 原始工具名（缺失不注入，渲染端自动隐藏占位符）
        conditional = self._session_conditional(payload)
        if tool:
            conditional["toolName"] = tool
        tool_lower = tool.lower()
        label = self.TOOL_LABELS.get(tool_lower, "")
        if label:
            text = self._dialogue(
                "approval.tool", f"{prefix}{name} 在请求审批：{label}，请到 DSH 界面处理～",
                agent_key=agent_key, label=label, name=name, **conditional,
            )
        elif tool:
            text = self._dialogue("approval.tool", f"{prefix}{name} 有审批等你决定（{tool}），请到 DSH 界面处理～",
                                  agent_key=agent_key, label=tool, name=name, **conditional)
        else:
            text = self._dialogue("approval.generic", f"{prefix}{name} 有审批等你决定，请到 DSH 界面处理～",
                                  agent_key=agent_key, name=name, **conditional)
        self._register_interaction(
            agent_key, kind="approval", text=text, tool=tool,
            owner=self._record_writer_owner(payload),
            rpc_id=payload.get("rpcId"),
            approval_id=payload.get("approvalId"),
            request_id=payload.get("requestId"),
            # callId 是审批收尾的精确身份：登记端必须存下，_on_approval_resolved
            # 的 callId 分支才能配对关闭。当前桥接版本的审批帧实际不带 callId
            # （此分支面向旧版/自定义桥的防御路径，常态走 rpcId/approvalId 关闭）。
            call_id=payload.get("callId"),
            session_id=session_id,
        )

    def _on_cordis_request(self, agent_key: str, payload: dict) -> None:
        record = payload if isinstance(payload, dict) else {}
        # 字段来源以桥接写盘形状为准：原始 cordis request 整体在 payload 下，
        # 顶层只有 requestId/agentId/sessionId 等身份字段；旧版/手写桩把字段
        # 平铺在顶层。两处都取，payload 内的非 None 字段优先。
        nested = record.get("payload")
        fields = dict(record)
        if isinstance(nested, dict):
            for key, value in nested.items():
                if value is not None:
                    fields[key] = value
        # 可关联身份门禁：cordis 交互靠 requestId 与 request-run-resolved 配对关闭。
        # 无 requestId 的记录无法关闭，直接忽略（requiresApproval 严格布尔检查在 _poll）。
        request_id = fields.get("requestId")
        if not request_id:
            log.debug("cordis/request-run 缺 requestId，忽略（不弹窗）: %s", str(record)[:200])
            return
        name = str(fields.get("name") or "Cordis 插件")
        purpose = str(fields.get("purpose") or "需要你的确认")
        self._register_interaction(agent_key, kind="cordis", text=f"{name} 请求运行：{purpose}",
                                   owner=self._record_writer_owner(record),
                                   request_id=request_id,
                                   session_id=fields.get("agentId") or fields.get("sessionId"))

    def _on_cordis_resolved(self, agent_key: str, payload: dict) -> None:
        """cordis 请求结束：与审批/问题同一份收尾摄入（来源 + 会话 + 同字段身份）。

        登记端把关联身份存在 ``request_id``（``requestId``）；这里同样只按 request_id
        匹配，不再与 rpcId 互通。来源由调用方给（信号带 agent_key）：旧实现在这一层
        把 agent_key 顶替成"会话键"传下去，来源当场丢失，于是 dsh 的收尾会把别的
        来源的同 requestId 交互当成唯一候选一起关掉。"""
        payload = payload if isinstance(payload, dict) else {}
        self._resolve_matching_interaction(
            kind="cordis", agent_key=agent_key,
            session_id=str(payload.get("agentId") or payload.get("sessionId") or ""),
            identities={"request_id": str(payload.get("requestId") or "")},
        )

    def _on_question_request(self, agent_key: str, payload: dict) -> None:
        """用户问题（ask_user_question）阻塞交互：与审批同待遇的常驻纯提示气泡
        （减法后无气泡内点选回写，需到 DSH 界面回答）。

        只登记**具备可关联身份**的问题（rpcId 或 callId 任一非空），靠它与
        question/resolved 配对精确关闭；两者皆无的记录无法可靠关闭，直接忽略。"""
        payload = payload if isinstance(payload, dict) else {}
        # 可关联身份门禁：rpcId 或 callId（tool/call 兜底）任一非空才登记。
        if not (payload.get("rpcId") or payload.get("callId")):
            log.debug("question/requested 缺可关联身份，忽略（不弹窗）: %s", str(payload)[:200])
            return
        name = self.AGENT_NAMES.get(agent_key, agent_key)
        questions = payload.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        session_id = str(payload.get("sessionId") or "")
        session_display = self.get_session_display_name(session_id) if session_id else ""
        prefix = f"{session_display} · " if session_display and session_display != f"DSH · {session_id[:8]}" else ""
        conditional = self._session_conditional(payload)
        self._register_interaction(
            agent_key, kind="question", text=self._question_text(agent_key, name, questions,
                                                                 prefix=prefix,
                                                                 conditional=conditional),
            questions=questions,
            owner=self._record_writer_owner(payload),
            rpc_id=payload.get("rpcId"),
            call_id=payload.get("callId"),
            session_id=session_id,
        )

    def _question_text(self, agent_key: str, name: str, questions: list, *, prefix: str = "",
                       conditional: dict | None = None) -> str:
        """把 questions 载荷排版成气泡文案（单行紧凑）。

        泡泡是图片气泡：normalize_bubble_text 会把换行折叠成空格，且 sticky 只
        显示第一页——所以选项用「 / 」内联拼接而非强行多行，保证「常驻问题弹窗」
        在小气泡里完整可见。

        ``agent_key`` 是预设路由身份（问题气泡文案同样支持 Agent 专属覆盖），
        与展示名 ``name`` 分开传：名字可被自定义 Agent 覆盖，路由键不能。
        """
        conditional = conditional or {}
        if not questions:
            return self._dialogue("question.empty", f"{prefix}{name} 在等你回答一个问题，快去看一下～",
                                  agent_key=agent_key, name=name, **conditional)
        if len(questions) > 1:
            if self._questions_all_have_options(questions):
                return self._dialogue("question.many", f"{prefix}{name} 有 {len(questions)} 个问题等你回答，快去看一下～",
                                      agent_key=agent_key, count=len(questions), name=name, **conditional)
            # 含自由文本分支：整批必须回 DSH 界面输入，引导不随台词被覆盖
            return self._with_dsh_input_hint(self._dialogue(
                "question.many",
                f"{prefix}{name} 有 {len(questions)} 个问题等你回答"
                "（含文本输入，请到 DSH 界面输入文本回答）～",
                agent_key=agent_key, count=len(questions), name=name, **conditional,
            ))
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
            return f"{name} 在问你：{body}（{' / '.join(labels)}）{multi}请到 DSH 界面选择～"
        if multi:
            return f"{name} 在问你：{body}（可多选）请到 DSH 界面选择～"
        # 无选项 = 需要自由输入文本：气泡必须明确引导回 DSH 界面输入。
        # 引导是结构性操作提示，不随表达风格台词（legacy/whale_maid/custom）被覆盖。
        text = self._dialogue(
            "question.one",
            f"{name} 在问你：{body}，需要你输入，请到 DSH 界面输入文本回答～",
            agent_key=agent_key, body=body, name=name, **conditional,
        )
        return self._with_dsh_input_hint(text)

    def _with_dsh_input_hint(self, text: str) -> str:
        """台词缺「回 DSH 输入」引导时补上，避免自定义/预设文案丢掉操作指引。"""
        if "DSH 界面输入" in str(text):
            return text
        return f"{text}（请到 DSH 界面输入文本回答）"

    @staticmethod
    def _identity_key_part(value) -> str:
        """键分量转义：自由文本里的分隔符不得与拼接结构撞车。

        键对身份必须是**单射**（不同身份绝不共享一个键）：值里出现 ``:`` 时如果不
        转义，``(rpc_id="x:y", 会话为空)`` 与 ``(rpc_id="x", 会话="y")`` 会拼出同一个
        键。转义是纯函数、可逆，无哈希、无时间、无文本近似。
        """
        return str(value).replace("\\", "\\\\").replace(":", "\\:")

    def _interaction_key(self, agent_key: str, kind: str, extra: dict, session_id: str) -> str:
        """生成稳定且**不碰撞**的交互 id：来源 + kind + 会话 + 身份命名空间/值。

        键必须**先**把完整身份写进去，收尾匹配才有事实可用：只按 ``kind:rpcId``
        存，第二个来源登记同一 rpcId 就撞键，撞了再补一个会话后缀也只是把覆盖推迟
        到第三条（三来源同 id 最后只剩两条，匹配前身份已经丢了）。这正是规范
        「去重键 = 来源 + 会话 + 逻辑事件标识」（``docs/PRODUCT-NEXT-STEPS-SPEC-
        2026-10-09.md`` §三）在内存真值表上的落地。

        身份字段取 ``_INTERACTION_ID_FIELDS`` 里第一个非空值，字段名一起进键：
        ``rpcId="x"`` 与 ``approvalId="x"`` 是两条不同命名空间的交互，不许共键。
        无身份的降级提示仍用 agent+kind+本地序号——这类条目不可能被任何收尾帧回收，
        靠 TTL 收口。
        """
        for field in self._INTERACTION_ID_FIELDS:
            value = extra.get(field)
            if value:
                parts = [kind, self._identity_key_part(agent_key), field,
                         self._identity_key_part(value)]
                if session_id:
                    parts.append(self._identity_key_part(session_id))
                return ":".join(parts)
        self._interaction_seq += 1
        return f"{kind}:{self._identity_key_part(agent_key)}:hint{self._interaction_seq}"

    #: 交互身份的字段（与源 ID 同序）：同一条交互的重投递必须是**就地更新**，
    #: 收尾帧的匹配也只看这几个字段（同名不同字段不算同一条，见 _resolve_matching_interaction）。
    _INTERACTION_ID_FIELDS = ("request_id", "rpc_id", "approval_id", "call_id")

    @staticmethod
    def _session_matches(item: dict, session_id: str) -> bool:
        """会话是否相容：任一侧为空 = 未知（旧桥/自定义桥可能不带 sessionId）。

        "未知"不与"不同会话"混为一谈：把未知当不同会话会让缺 sessionId 的旧帧
        永远配不上、留下关不掉的常驻提醒；把"不同"当未知则会误关别的会话。
        兼容建立的"可能匹配"由调用方按**唯一候选**收敛（见
        :meth:`_resolve_matching_interaction`）：相容 ≠ 可以任取一条。
        """
        item_session = str(item.get("session_id") or "")
        if not session_id or not item_session:
            return True
        return item_session == session_id

    def _interaction_matches(self, item: dict, *, kind: str, agent_key: str,
                             session_id: str, identities: dict[str, str]) -> bool:
        """一条 pending 是否就是这条收尾帧指向的交互（**唯一**匹配实现）。

        判据（缺一不可）：
        - ``kind`` 相同（审批与问题各自独立，callId 也不跨 kind 复用）；
        - **来源**相同（同一 id 可能在多个 agent 上出现，旧通道此前不查来源）；
        - **会话**一致或未知（见 :meth:`_session_matches`）；
        - **身份同字段同值**：rpcId 只与 rpcId 比、approvalId 只与 approvalId 比
          ——不同命名空间的同值字符串不是同一条交互。
        """
        if item.get("kind") != kind or item.get("agent_key") != agent_key:
            return False
        if not self._session_matches(item, session_id):
            return False
        for field, value in identities.items():
            if value and str(item.get(field) or "") == str(value):
                return True
        return False

    def _matching_interaction_ids(self, *, kind: str, agent_key: str,
                                  session_id: str, identities: dict[str, str]) -> list[str]:
        """这条收尾帧的全部候选（按表内次序），只做匹配、不动任何一条。"""
        return [iid for iid, item in self._pending_interactions.items()
                if self._interaction_matches(item, kind=kind, agent_key=agent_key,
                                             session_id=session_id, identities=identities)]

    @staticmethod
    def _log_ambiguous_resolution(*, kind: str, origin: str,
                                  identities: dict[str, str], candidates: list[str]) -> None:
        """多候选歧义必须明确留痕：说清"什么都没做"，不批准、不拒绝、不删本地真值。

        ``origin`` 由调用方给（各通道能提供的事实不同：语义/直通通道有来源+会话，
        compat 通道只有 sessionKey），日志只报事实，不假装知道得更多。
        """
        log.warning(
            "交互收尾帧有 %s 条候选、归属不明：不动任何一条（不批准、不拒绝、不删）"
            " kind=%s %s identity=%s candidates=%s",
            len(candidates), kind, origin,
            {key: value for key, value in identities.items() if value},
            [str(iid)[:80] for iid in candidates[:4]],
        )

    def _resolve_matching_interaction(self, *, kind: str, agent_key: str,
                                      session_id: str, identities: dict[str, str]) -> str | None:
        """按「来源 + 会话 + 身份」关闭**唯一**匹配的那条 pending（无匹配/多候选返回 None）。

        **唯一收尾摄入**：语义层（``_on_normalized_event``）与旧直通通道
        （``_on_approval_resolved`` / ``_on_question_resolved``）都走这里。同一条
        记录会同时喂两条通道，第一条按 id 精准删掉自己那条后，第二条因同样的
        「来源 + 会话 + 身份」约束**找不到候选**——结构性 no-op，不需要任何
        "同一记录只处理一次"的文本/时间去重花招。

        **多候选 = 不动**（这是上面那条"结构性 no-op"成立的前提）：旧帧缺 sessionId
        时会话是未知，两个会话可能各有一条同 rpcId 的 pending；任取一条都可能删掉
        用户还在等的那条，而且删掉第一条之后，第二条通道会看到"新的唯一候选"把
        另一条也删掉（一条记录关两条）。所以候选不止一条时一律不动，只留 warning；
        唯一候选仍按旧帧兼容关闭（缺 session 的旧桥不许把提醒变成关不掉的常驻），
        其余交给 lifecycle/离线收口。
        """
        candidates = self._matching_interaction_ids(kind=kind, agent_key=agent_key,
                                                    session_id=session_id,
                                                    identities=identities)
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])
            return candidates[0]
        if candidates:
            self._log_ambiguous_resolution(
                kind=kind,
                origin=f"source={agent_key} session={session_id or '<unknown>'}",
                identities=identities, candidates=candidates)
        return None

    def _resolve_identity_less_interaction(self, kind: str, agent_key: str,
                                           session_id: str) -> str | None:
        """无任何身份的旧帧兜底：该来源+会话下**只有一条**无 rpcId 的 pending 时才关。

        多条候选 = 无法判断是哪条，一律不动（宁可留着让 lifecycle/离线收口，
        也不误关用户还在等的提醒）。
        """
        candidates = [iid for iid, item in self._pending_interactions.items()
                      if item.get("kind") == kind
                      and item.get("agent_key") == agent_key
                      and not item.get("rpc_id")
                      and self._session_matches(item, session_id)]
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])
            return candidates[0]
        return None

    def _existing_interaction_for(self, agent_key: str, kind: str, extra: dict,
                                  session_id: str) -> str | None:
        """找同一逻辑交互已有的 interaction_id（没有则 None）。

        判据是"**任一**稳定身份字段相等 + 会话一致"：审批/问题记录在不同桥接
        版本里带的身份字段不同（有的带 rpcId、有的只有 approvalId/callId），
        而它们指的是同一条交互。会话不一致时不认（同一 rpcId 被跨会话复用）。
        """
        for iid, item in self._pending_interactions.items():
            if item.get("kind") != kind or item.get("agent_key") != agent_key:
                continue
            if session_id and str(item.get("session_id") or "") not in ("", session_id):
                continue
            for field in self._INTERACTION_ID_FIELDS:
                value = extra.get(field)
                if value and str(item.get(field) or "") == str(value):
                    return iid
        return None

    @staticmethod
    def _pending_priority(item: dict) -> int:
        """该条 pending 交互的本地优先级（0 = 最高，与显示队列同口径）。"""
        return int(item.get("priority", AgentLinkManager._PENDING_INTERACTION_PRIORITY))

    def _note_pending_overflow(self, action: str, victim: str, dropped: dict | None = None) -> None:
        """超限处置留痕：计数 + warning（含来源类型/alertId，便于事后定位）。"""
        self._pending_interaction_overflow_count += 1
        log.warning(
            "pending 交互表已满(%s)：%s kind=%s alertId=%s agent=%s overflowCount=%s",
            self._PENDING_INTERACTION_MAX, action,
            (dropped or {}).get("kind", ""), (dropped or {}).get("alert_id", ""),
            (dropped or {}).get("agent_key", ""), self._pending_interaction_overflow_count,
        )

    def _merge_pending_owner(self, owners: set[str], overflow: bool,
                             owner: str) -> tuple[set[str], bool]:
        """把一个写者并进某条 pending 的归属集合，**有界**（返回新集合与新锁存位）。

        - 已在集合里：原样返回；未满：并入；
        - 已满：**不并入、也不丢任何已有归属**，只把 ``overflow`` 锁存为 True。
          满员说明"谁还在等"本地已经记不全，收口判据随之收紧（见
          :meth:`_release_owner`）。绝不按 FIFO 丢旧 owner（丢了就等于允许后来的 idle
          把别人还在等的交互整个撤掉），也绝不并进"无主桶"。
        """
        if owner in owners:
            return owners, overflow
        if len(owners) >= self._PENDING_OWNERS_MAX:
            if not overflow:
                log.warning(
                    "pending 交互归属集合已达上限(%s)：后续写者归属只锁存 overflow "
                    "标记，不再并入（已有归属一律保留，收口收紧到可信来源身份）",
                    self._PENDING_OWNERS_MAX)
            return owners, True
        owners.add(owner)
        return owners, overflow

    def _admit_pending_interaction(self) -> bool:
        """pending 真值表的**有界入表裁决**（唯一实现，政策与显示队列同构）。

        - 未满：直接进表。
        - 满且新条目优先级**低于**表内最低优先级 → 拒收新条目（不挤掉别人），
          留 warning + 计数。（当前所有交互类型都是优先级 0 的关键状态，这条
          分支只在未来真出现更低优先级的交互类型时生效，不编造类型去用它。）
        - 否则 → 淘汰**最低优先级里最旧**的一条腾位，同样留痕，并在显示 owner 上
          按同一 alert_id 清理（否则那条提醒永远等不到 resolved，成为死提醒）。

        **淘汰只影响本地呈现**：不批准、不拒绝、不写回上游——被淘汰的请求在 DSH
        侧仍然是待处理状态，用户可以在任何 DSH 界面回答它（参考
        ``docs/DSH-REQUEST-EVENT-CATALOG.md`` 的「Bounded local surfacing」）。
        """
        if len(self._pending_interactions) < self._PENDING_INTERACTION_MAX:
            return True
        worst = max(self._pending_priority(item)
                    for item in self._pending_interactions.values())
        if self._PENDING_INTERACTION_PRIORITY > worst:
            self._note_pending_overflow("reject", "")
            return False
        victim = None
        for iid, item in self._pending_interactions.items():
            if self._pending_priority(item) != worst:
                continue
            if victim is None or float(item.get("_ts_mono", 0.0)) < float(
                    self._pending_interactions[victim].get("_ts_mono", 0.0)):
                victim = iid
        if victim is None:
            return False
        self._note_pending_overflow("evict", victim, self._pending_interactions[victim])
        self._resolve_interaction(victim)
        return True

    def _prune_pending_interactions(self, now: float | None = None) -> int:
        """TTL 收口（在登记路径上触发）：只清理**无身份**的降级提示，返回清理条数。

        带身份的审批/问题是长待批的关键状态——DSH 可能几十分钟后才回收尾帧，
        时间到就删本地真值等于把用户还在等的提醒吞掉（规范「不能随意删除关键
        状态但内存上限必须」）。这类条目的界是 ``_PENDING_INTERACTION_MAX``；
        TTL 只适用于没有任何身份、因此不可能被任何收尾帧回收的降级提示。
        """
        moment = time.monotonic() if now is None else float(now)
        stale = []
        for iid, item in self._pending_interactions.items():
            if any(str(item.get(field) or "") for field in self._INTERACTION_ID_FIELDS):
                continue
            if moment - float(item.get("_ts_mono", moment)) < self._PENDING_INTERACTION_HINT_TTL_S:
                continue
            stale.append(iid)
        for iid in stale:
            self._resolve_interaction(iid)
        if stale:
            log.debug("无身份降级提示已按 TTL 收口：%s", stale)
        return len(stale)

    def _register_interaction(self, agent_key: str, *, kind: str, text: str,
                              owner: str | None = None, **extra) -> str | None:
        """统一登记一条阻塞型交互：进 pending + 打 alert 标记 + 挂常驻气泡。

        返回该交互的稳定 interaction_id（resolve 按它精确定位）。

        - **同一 agent 的多个审批/问题各自独立存储**（按 interaction_id），
          不再以 agent_key 为键互相覆盖——resolved 按 id 精确绑定，
          绝不错关闭其他并发的审批。
        - **键按完整身份构造**（来源 + kind + 会话 + 身份命名空间/值，见
          ``_interaction_key``）：撞键不再靠"补一个会话后缀"救场（那只是把覆盖
          推迟到第三条），不同来源/会话/命名空间的交互各有自己的键。
        - **同一逻辑交互的重投递就地更新**（G2）：重复登记会让同一审批/问题占两份
          pending，而 resolved 帧只关得掉其中一份——另一份变成关不掉的常驻提醒
          （还多弹一次正文）。身份由 rpcId/approvalId/requestId/callId + 会话给出，
          复用原 alert_id 让窗口按 id 合并（同一提醒最多一份可见）。
        - ``owner`` 是**写者归属集合**的起始值（记录上的摄入标注，见
          :func:`record_owner`）。多实例下同一条交互可能被两个写者各写一份（去重后
          只留一份正文），归属集合记全了才不会出现"先回待机的那个写者把另一个实例
          还在等的交互撤掉"（见 :meth:`_release_owner`）。``None`` = 归属未知
          （直调/非摄入路径）：集合为空，只被"归属未知"的清理口径收走。
        - **归属集合只累积、绝不整字典重置**（M3）：重登记（摄入账本 512 条 FIFO
          淘汰或 900s TTL 过期后的重投、插件重载后同请求再投）此前先往旧条目加
          owner、紧接着用整个新字典覆盖，把已累计的归属一次性丢掉——长等待经过
          账本淘汰后，B 的 idle 又能把 A 还在等的交互整个撤掉。这里改为读出旧集合、
          把本写者并进去，alert 身份与 owner 集合都沿用原来那份。
        """
        session_id = str(extra.get("session_id") or "")
        existing_iid = self._existing_interaction_for(agent_key, kind, extra, session_id)
        if existing_iid is not None:
            iid = existing_iid
            previous = self._pending_interactions.get(iid) or {}
        else:
            self._prune_pending_interactions()
            if not self._admit_pending_interaction():
                return None
            iid = self._interaction_key(agent_key, kind, extra, session_id)
            previous = {}
        owners = set(previous.get("owners") or ())
        overflow = bool(previous.get("owners_overflow"))
        if owner is not None:
            owners, overflow = self._merge_pending_owner(owners, overflow, str(owner))
        # 稳定 alert 身份：同一逻辑交互的提醒只能有一份可见（窗口按 alert_id 合并）
        alert_id = str(previous.get("alert_id") or "") or f"interaction:{iid}"
        self._pending_interactions[iid] = {
            "kind": kind, "text": text, "alert_id": alert_id,
            "agent_key": agent_key,
            "priority": self._PENDING_INTERACTION_PRIORITY,
            "owners": owners,
            "owners_overflow": overflow,
            "_ts_mono": time.monotonic(),
            **extra,
        }
        # 交互打断算"需要主人看一眼"：任务完成后不误说"干完活啦"（同一事实按归属
        # 也记一份：具名写者的收尾按自己那份裁决，见 _writer_marked）
        self._saw_alert.add(agent_key)
        self._mark_owner(self._owner_alert_marks, agent_key, owner or "", on=True)
        # 常驻气泡：不自动消失，等 resolved / 离线 / 空闲再收尾
        if not self._show_interaction_bubble(iid):
            # 这次没交到呈现目标手里（隐藏/无宠）：保活补投表并回到快拍，
            # 等目标回来按同一 interaction_id 补投（新请求优先快速看到）。
            self._ensure_pending_redeliver_timer(reset=True)
        return iid

    @staticmethod
    def _resolved_identities(payload: dict) -> dict[str, str]:
        """收尾帧载荷 → pending 条目的身份字段（字段名归一，值原样保留）。"""
        return {
            "request_id": str(payload.get("requestId") or payload.get("request_id") or ""),
            "rpc_id": str(payload.get("rpcId") or payload.get("rpc_id") or ""),
            "approval_id": str(payload.get("approvalId") or payload.get("approval_id") or ""),
            "call_id": str(payload.get("callId") or payload.get("call_id") or ""),
        }

    def _on_approval_resolved(self, agent_key: str, payload: dict | None = None) -> None:
        """审批结束（approval/resolved 帧带 rpcId/approvalId，或旧路径
        approval/decided 无 id）：按「来源 + 会话 + 身份」精确关闭对应交互记录。

        与语义层（``_on_normalized_event``）共用 ``_resolve_matching_interaction``：
        同一条记录同时喂两条通道时，第一条删掉自己那条之后第二条结构化地找不到
        候选（同样的来源/会话/身份约束），不需要"同记录只处理一次"的临时去重。

        并发多个审批时，带 id 的 resolved 帧只关闭自己那一条；**带 id 但未匹配到
        任何 pending 的帧是陈旧的已解决帧**——绝不回退去关闭其他 pending 审批，
        也不会跨会话（同一 rpcId 被跨会话复用时 A 的收尾帧只关 A）或跨 agent。
        **缺 sessionId 的旧帧匹配到多条候选时同样一个都不关**（归属未知不许任取
        一条，而且那条记录的第二通道会接着关下一条），只留 warning。
        兜底仅用于无任何 id 的旧路径 approval/decided（该来源+会话下唯一一条
        无 rpcId 的纯提示）。"""
        payload = payload if isinstance(payload, dict) else {}
        identities = self._resolved_identities(payload)
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "")
        matched = self._resolve_matching_interaction(kind="approval", agent_key=agent_key,
                                                     session_id=session_id,
                                                     identities=identities)
        if matched is not None:
            return
        if not any(identities.values()):
            # 无 id 的旧路径 approval/decided：只对「无 rpc_id 的纯提示」兜底关闭。
            # 带 rpc_id 的审批由同 id 的 approval/resolved 帧精确关闭——若这里
            # 对它们兜底，DSH 回发的 A 的 decided（无 id）会把还在等待的 B 误关
            # （表现为第二个弹窗延迟 0.5~1s 后自动消失）。
            self._resolve_identity_less_interaction("approval", agent_key, session_id)

    def _on_question_resolved(self, agent_key: str, payload: dict | None = None) -> None:
        """问题结束（question/resolved）：与审批同一份匹配实现。

        带 id 的 resolved 帧只关闭自己那一条；未匹配的带 id 帧是陈旧的已解决帧，
        绝不回退关闭其他 pending 问题，也不跨会话/跨 agent 误关；缺 sessionId 的
        旧帧匹配到多条候选时一个都不关（见 ``_resolve_matching_interaction``）；
        兜底（无 id 的旧路径）仅对无 rpc_id 的纯提示问题生效。"""
        payload = payload if isinstance(payload, dict) else {}
        identities = self._resolved_identities(payload)
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "")
        if self._resolve_matching_interaction(kind="question", agent_key=agent_key,
                                              session_id=session_id,
                                              identities=identities) is not None:
            return
        if not any(identities.values()):
            self._resolve_identity_less_interaction("question", agent_key, session_id)

    def _resolve_interaction(self, interaction_id: str) -> None:
        """阻塞型交互结束：按 interaction_id 精确清掉该条记录，并让提醒队列自然推进。

        注意：队列（window.show_alert）自己会逐条展示，这里用 resolve_alert
        按 alert_id 精确定位关闭，避免 hide_bubble 误关其他 agent/其他并发审批
        的提醒。"""
        item = self._pending_interactions.pop(interaction_id, None)
        if item is None:
            return
        if not self._pending_interactions:
            # 表空 = 没有待补投的交互：补投表立即停（稳态零唤醒）
            self._stop_pending_redeliver_timer()
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
        self._clear_model_access_alerts()
        if not self._pending_interactions and not getattr(self.win, "_sticky_bubble_active", False):
            return
        self._pending_interactions.clear()
        self._stop_pending_redeliver_timer()
        if hasattr(self.win, "clear_alerts"):
            self.win.clear_alerts()
        elif hasattr(self.win, "hide_bubble"):
            self.win.hide_bubble()

    def dismiss_all_approvals(self) -> None:
        """兼容别名：等价 dismiss_all_interactions。"""
        self.dismiss_all_interactions()

    def _presentation_ready(self) -> bool:
        """现在是否**有可承接的呈现目标**（与显示 owner 的门禁同口径）。

        多窗代理报 ``has_presentation_target()``（至少一个可见窗）；单窗宿主读
        自己的 ``isVisible()``。没有可见目标时 ``window_alerts`` 在不可见宿主上
        不排队，这条提醒实际上不了屏——恢复重投据此判断"要不要补投"。
        """
        probe = getattr(self.win, "has_presentation_target", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                log.debug("呈现目标探测失败", exc_info=True)
                return False
        probe = getattr(self.win, "isVisible", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return True
        return True

    def _show_interaction_bubble(self, interaction_id: str) -> bool:
        """把某条 pending 阻塞交互以 sticky 气泡挂上（纯提示，无按钮）。

        走提醒消息队列（show_alert）：审批/问题入队后一次只展示一个，
        队列非空时其他弹窗不覆盖；resolved 时经 resolve_alert 弹下一条。
        以 interaction_id 精确定位，保证并发审批各自的气泡互不干扰。

        返回**本次是否交给了可承接的呈现目标**（有可见窗）；该结果记进条目的
        ``delivered``：隐藏/无宠期间到达的请求在显示 owner 侧上不了屏，但真值一直
        在 pending 表里，恢复可见时由 :meth:`redeliver_pending_interactions` 按同一
        interaction_id 补投（窗口按 alert_id 就地合并，最多一份可见正文）。"""
        pending = self._pending_interactions.get(interaction_id)
        if not pending:
            return False
        delivered = self._presentation_ready()
        pending["delivered"] = delivered
        if not hasattr(self.win, "show_alert"):
            if hasattr(self.win, "show_bubble"):
                # 旧桩/无 show_alert 的窗口：退化为普通气泡，绝不因签名差异崩溃
                try:
                    self.win.show_bubble(pending["text"], sticky=True)
                except TypeError:
                    self.win.show_bubble(pending["text"])
        else:
            self._show_alert_compat(
                pending["text"], subtitle="", sticky=True,
                alert_id=pending.get("alert_id", ""), priority=0,
                alert_type=pending.get("kind", "approval"),
            )
        # 没交出去 → 保活低频重投表，等呈现目标回来（有目标时零唤醒）。
        if not delivered:
            self._ensure_pending_redeliver_timer()
        return delivered

    def _pending_undelivered(self) -> list[str]:
        """还没交到呈现目标手里的 pending 交互 id（有界表内的一次线性扫描）。"""
        return [iid for iid, item in self._pending_interactions.items()
                if not item.get("delivered")]

    def redeliver_pending_interactions(self) -> int:
        """补投隐藏/无宠期间**没能交付**的常驻交互（恢复可见时调用）。

        隐藏期到达的审批/问题：pending 真值一直在本表（有界），但显示 owner 在
        不可见宿主上不排队，owner 的 current/queue 可能为空——只看 owner 队列的
        恢复路径永远等不到这条提醒（既不是"显示过又隐藏"，也没有再次推送）。
        恢复可见时按同一 interaction_id 补投一次：显示 owner 按 alert_id 就地合并
        （同一提醒最多一份可见正文），已交付过的条目不重复投递。
        """
        waiting = self._pending_undelivered()
        if not waiting or not self._presentation_ready():
            return 0
        redelivered = 0
        for iid in waiting:
            if self._show_interaction_bubble(iid):
                redelivered += 1
        if redelivered:
            log.debug("恢复显示补投阻塞交互：%s 条", redelivered)
        return redelivered

    def _ensure_pending_redeliver_timer(self, *, reset: bool = False) -> None:
        """保活一个**有界、自停**的补投表：只在"有请求没交出去"期间跑。

        恢复路径不能只靠"窗口显示"这一个调用点（全隐藏后重新生成宠物、逐只
        恢复显示等路径不经过 ``resume()``），所以给一个兜底节拍：新请求没交出去时
        1s 一拍；一直等不到呈现目标则指数退避到 ``_PENDING_REDELIVER_MAX_MS``
        （有界唤醒，不空转 1Hz）；交付完或表空立刻停表（稳态零唤醒）。
        """
        timer = self._pending_redeliver_timer
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(self._PENDING_REDELIVER_INTERVAL_MS)
            timer.timeout.connect(self._on_pending_redeliver_tick)
            self._pending_redeliver_timer = timer
        elif reset and timer.interval() != self._PENDING_REDELIVER_INTERVAL_MS:
            timer.setInterval(self._PENDING_REDELIVER_INTERVAL_MS)
        if not timer.isActive():
            timer.start()

    def _stop_pending_redeliver_timer(self) -> None:
        timer = self._pending_redeliver_timer
        if timer is not None and timer.isActive():
            timer.stop()

    def _on_pending_redeliver_tick(self) -> None:
        if not self._pending_undelivered():
            self._stop_pending_redeliver_timer()
            return
        if self.redeliver_pending_interactions():
            return
        timer = self._pending_redeliver_timer
        if timer is not None:
            timer.setInterval(min(timer.interval() * 2, self._PENDING_REDELIVER_MAX_MS))

    def _show_alert_compat(self, text: str, **kwargs) -> None:
        """Use enriched alert metadata while remaining compatible with test/old windows."""
        try:
            self.win.show_alert(text, **kwargs)
        except TypeError:
            legacy = dict(kwargs)
            for key in ("priority", "alert_type", "metadata"):
                legacy.pop(key, None)
            self.win.show_alert(text, **legacy)

    @staticmethod
    def _questions_all_have_options(questions: list) -> bool:
        """整批问题是否全部带可点选选项（气泡文案据此区分「可点选」与「需自由输入」）。"""
        if not questions:
            return False
        return all(
            isinstance(q, dict) and bool(q.get("options"))
            for q in questions
        )

    def _show_approval_bubble(self, agent_key: str) -> None:
        """兼容别名：把该 agent 的全部 pending 审批/问题气泡挂上（等价
        _show_interaction_bubble，按 agent 遍历其所有交互）。"""
        for iid in list(self._pending_interactions):
            if self._pending_interactions[iid].get("agent_key") == agent_key:
                self._show_interaction_bubble(iid)

    def _monitor_generation(self, agent_key: str) -> int | None:
        """该 Agent 当前监测代次；监视器不存在或已停时返回 None（不做代次闸）。"""
        mon = self.monitors.get(agent_key)
        if mon is None or not mon._running:
            return None
        return int(mon._emit_gen)

    def _retire_agent_presentation(self, agent_key: str) -> None:
        """退役一个 Agent 的**本代呈现簿记**（监测停止 / 换代次时调用）。

        这是「启停边界」上的统一出口，替掉散落在各分支里的逐字段清理。清的是
        「这一监测代对用户下过的结论」，**不是**远程会话状态：不发任何取消/控制帧，
        不动其它 Agent，也不碰待审批/待回答的常驻交互（那由 link-disabled 的既有
        offline 复位管线负责）。

        - ``_done_pending``：在飞的 800ms 完成确认必须取消——否则用户关掉联动之后
          仍会弹「已完成本轮任务」（L01）；
        - ``_saw_error`` / ``_saw_alert`` / ``_error_after_done``：上一代的硬失败 /
          需注意标记不得延续——否则新代中途接上的新回合成功收尾被整个吞掉（L02）；
        - ``_last_raw`` / ``_last_applied``：新代从「没见过任何状态」开始（否则新代
          第一帧会拿上一代的终态当 prev 做边沿判定）；
        - ``_owner_raw``（本 Agent 的分片记账）：同理——上一代的写者分片留着会让新代
          第一帧的折叠值直接继承旧代（且这些写者已随通道关闭消失）；
        - ``_dsh_turn_ids``：上一代的收尾身份记忆（tracker 的全局起点计数故意不
          回收，这里同样清身份表，保证新代该提醒就提醒）；
        - ``_done_cooldown`` / ``_sound_last_event``：完成气泡/音效的去重与冷却属于
          **一次连续观测内**的保险；换代次后上一代的弹泡不该再压住新代的第一次真实
          收尾（``_sound_last_at`` 是跨 Agent 的全局冷却，不动）；
        - ``_owner_error_marks`` / ``_owner_alert_marks``（本 Agent 的写者级标记）：
          同理——上一代写者的失败/需注意口径不得延续到新代（标记清空后按 agent 级
          集合裁决，语义与改造前一致）。
        """
        self._cancel_done_check(agent_key)
        self._saw_error.discard(agent_key)
        self._saw_alert.discard(agent_key)
        self._error_after_done.discard(agent_key)
        self._last_raw.pop(agent_key, None)
        self._last_applied.pop(agent_key, None)
        for key in [k for k in self._owner_raw if k[0] == agent_key]:
            self._owner_raw.pop(key, None)
        self._drop_owner_marks(agent_key)
        self._dsh_turn_ids.pop(agent_key, None)
        self._done_cooldown.pop(agent_key, None)
        self._sound_last_event.pop(agent_key, None)

    def _schedule_done_check(self, agent_key: str, cause: str = "edge",
                             owner: str = "") -> None:
        """排定 800ms 完成确认；**回调绑定创建时的监测代次**。

        关掉再立刻开启联动时，旧代的定时器若还能回调，就会以新代的身份弹一次早已
        不成立的完成提醒（L01）。绑定代次后，``_fire_done`` 到期时再核一次当前代次，
        任何「停过机器/换代次」的旧确认都作废；监视器本来就没在跑（测试/白盒路径）
        时绑定 None，不做代次闸。

        ``owner`` 是这条完成的写者归属：到期判据（"这期间回忙了吗"）看**这一写者**
        的状态，而不是全写者折叠后的全局值——A 完成、B 还在忙时，A 的完成提醒必须
        照常出，不能因为"整体还忙"把 A 的完成静默吞掉；反过来也不能把整体改成待机
        来成全它（见 ``_fire_done``）。
        """
        self._cancel_done_check(agent_key)
        gen = self._monitor_generation(agent_key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self._DONE_CONFIRM_MS)
        timer.timeout.connect(lambda k=agent_key, g=gen: self._fire_done(k, g))
        self._done_pending[agent_key] = timer
        self._done_cause[agent_key] = cause
        self._done_owner[agent_key] = str(owner or "")
        timer.start()

    def _cancel_done_check(self, agent_key: str) -> None:
        self._done_cause.pop(agent_key, None)
        self._done_owner.pop(agent_key, None)
        timer = self._done_pending.pop(agent_key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _abort_done_check_on_error(self, agent_key: str, owner: str = "") -> None:
        """收尾后爆雷：error 落在完成确认窗内 → 取消确认并按失败口径打标。

        能走到这里的前提是该 agent 已有 pending 的完成确认——收尾边沿（busy→success /
        busy→idle / busy→attention）已经声明本轮结束，随后的 error 属于「收尾后爆雷」：
        成功口径的收尾（「已完成本轮任务」+ done 音效）此时已经不成立，而这条 error
        又走不进 busy→error 那条既有分支（那里要求 prev_raw 是 busy），只能在这里
        收口。打两个既有标记——``_saw_error`` 掐 done 音效、挡后续 success 边沿的重复
        排定，``_saw_alert`` 把收尾文案切离「已完成」；``_error_after_done`` 记录「这个
        标记来向不是 busy」，让下一个忙入口把标记当**新回合**清掉（同周期重试与此相反，
        ``_saw_error`` 要跨 error→working 存活）。

        两个调用点：``_on_agent_state`` 的收尾边沿来向 error 分支，与 ``_on_llm_error``
        （API 级错误同样语义，且收敛器的 ERROR 不注入呈现管线——那条路径上这是唯一
        的闸）。没有 pending 确认时是 no-op：确认窗外的 error 由既有分支处理，
        本方法不改那条路径的行为。

        ``owner`` 是这条 error 的写者归属：撤销与打标都按它裁决——B 实例的 error
        不得取消 A 实例待定的完成确认，也不得把 A 的收尾打成失败口径。
        """
        if agent_key not in self._done_pending:
            return
        self._saw_error.add(agent_key)
        self._saw_alert.add(agent_key)
        self._mark_owner(self._owner_error_marks, agent_key, owner, on=True)
        self._mark_owner(self._owner_alert_marks, agent_key, owner, on=True)
        self._error_after_done.add(agent_key)
        self._cancel_done_check_for_owner(agent_key, owner)

    def _fire_done(self, agent_key: str, gen: int | None = None) -> None:
        """800ms 稳定确认到期：期间回忙则不算完成；配置/冷却在弹出前再查。

        ``gen`` 是这条确认**排定时**绑定的监测代次（``_schedule_done_check`` 传入，
        白盒/直接调用为 None）。到期时核一次当前代次：不匹配说明这一代已经被停过或
        换过（关闭联动 / monitor stop→start），该确认不再成立——先回收定时器再退出，
        不出声不弹泡。
        """
        # 定时器自己到期：取消路径（_cancel_done_check）已 stop + deleteLater，
        # 这条路径同样必须回收——否则每轮留一个已停表 QObject 在 manager 子对象表里
        # （长跑进程按回合数线性增长）。
        timer = self._done_pending.pop(agent_key, None)
        self._done_cause.pop(agent_key, None)
        owner = self._done_owner.pop(agent_key, "")
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if gen is not None and not self._gen_current(agent_key, gen):
            return  # 旧代次的确认：监视器已停或已换代，作废（关掉联动后不得再提醒）
        # 隐藏中：不切动画不出声，气泡改道灵动岛反馈面（岛反馈面可用时；
        # pause_agent_link_for_hide 让监视器隐藏期保持运行，本兜底必须感知，
        # 否则 done 气泡在隐藏期被静默吞掉）。
        hidden = not hasattr(self.win, "isVisible") or not self.win.isVisible()
        # **完成上下文按 owner**：有归属时只看这一写者自己的原始状态（它才是"这一条
        # 完成"的当事人），全局折叠值只作兜底。A 完成、B 还在忙时全局值是 working，
        # 用全局值判"这期间回忙了吗"会把 A 的完成提醒静默吞掉。
        context_state = self._owner_raw.get((agent_key, owner), ("", 0))[0] if owner else ""
        if not context_state:
            context_state = self._last_raw.get(agent_key)
        # 归属具名的聚合忙值：**别的写者能解释**才忽略（B 在忙与本条完成无关，照常
        # 提醒）；没有别的写者能解释的忙归当事人自己（起点记录已被
        # ``_on_dsh_turn_started`` 落成分片，剩下的是不带归属的忙注入，如 tool/call）
        # → 与"期间回忙"同等作废，别拿无人认领的忙弹完成。
        if (owner and self._last_raw.get(agent_key) in self._BUSY_STATES
                and not self._other_writer_busy(agent_key, owner)):
            return
        if context_state in self._BUSY_STATES:
            return
        # 防线：到期时该 agent 的**当前**原始状态已是 error（收尾后爆雷时打标路径
        # 万一漏了，或者将来新增的 error 落点忘了打标）→ 一律不许成功口径。
        # 这不会打断 Claude 的 error 周期收尾：那条路径上 _saw_error/_saw_alert 已
        # 经打上（busy→error 分支），走的就是下面的非成功分支。
        # 失败/需注意两个标记按**归属**读：A 实例的失败不许把 B 实例的收尾打成
        # 失败口径（掐掉 done 音效、文案变成"自己看一眼"）。
        dirty = self._error_marked(agent_key, owner) or context_state == "error"
        if not hidden and not dirty:
            self._emit_sound("done", agent_key)
        now = self._clock()
        if now - self._done_cooldown.get(agent_key, 0.0) < self._DONE_COOLDOWN_S:
            self._cost.abort(agent_key)
            return
        self._done_cooldown[agent_key] = now
        name = self.agent_names.get(agent_key, agent_key)
        if self._alert_marked(agent_key, owner) or context_state == "error":
            # busy 期间出现过 attention/error：不暗示"成功完成"
            text = self._dialogue("done.attention", f"{name} 那边停了，结果怎么样要主人自己看一眼哦", agent_key=agent_key, name=name)
        else:
            text = self._dialogue("done.success", f"{name} 干完活啦，去看看成果吧～", agent_key=agent_key, name=name)
        self._saw_alert.discard(agent_key)
        self._mark_owner(self._owner_alert_marks, agent_key, owner, on=False)
        if hidden:
            # 隐藏中：不切待机动画；气泡改道灵动岛反馈面（_show_link_bubble
            # 内置改道；岛反馈面不可用时丢弃）。消费统计按 abort 收口（原隐藏
            # 路径语义：防 _busy 永久滞留，下次开始干活被误判成"并发"）。
            from .. import window_alerts as _window_alerts

            _window_alerts.redirect_hidden_bubble(self.win, text, duration_ms=4500)
            self._cost.abort(agent_key)
            return
        # 恢复待机动画：Claude 回合结束没有 idle 事件，不靠这步会一直停在干活动作。
        # 仅当**没有任何写者、任何 Agent 仍在忙**时恢复（避免 A 完成顶掉 B 的工作
        # 动画；多实例下同一 agent 的其它写者同理——全局折叠值仍忙就不回待机）。
        # 必须走 request_link_idle（它会清 _link_anim_current 并尊重一次性动作），
        # 不能裸 _switch——否则残留的 link 状态会把以后的普通同名动作劫持进联动链。
        if (self._last_raw.get(agent_key) not in self._BUSY_STATES
                and not any(k != agent_key and s in self._BUSY_STATES
                            for k, s in self._last_raw.items())):
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            elif hasattr(self.win, "switch_clip") and getattr(self.win, "idles", None):
                self.win.switch_clip(self.win.idles[0])
            self._last_applied[agent_key] = ("idle", now)
        self._show_link_bubble(text, important=True)
        self._cost_finish(agent_key)

    # ------------------------------------------------------------ 本轮消费

    # 余额查询结果回到主线程：(agent_key, 用途, 余额或 None)
    # 用途为 "baseline"（开始）或 "done"（结束）。
    _cost_balance_ready = Signal(str, str, object)

    def _cost_enabled(self) -> bool:
        """消费统计是否启用：开关打开 + 当前 provider 是 DeepSeek。

        只有 DeepSeek 有余额接口；别的 provider 查不到，直接不启用，
        避免留下"开关开着却永远没数字"的困惑。
        """
        if not bool(self.cfg.get("agent_cost_enabled", False)):
            return False
        return self._deepseek_provider() is not None

    def _deepseek_provider(self):
        """取当前激活且支持余额查询的 provider（仅 DeepSeek）。"""
        try:
            settings = self.cfg.chat_settings()
            provider = settings.active_config
        except Exception:
            return None
        base = str(getattr(provider, "base_url", "") or "")
        if "deepseek.com" not in base:
            return None
        return provider

    def _query_cost_balance(self, agent_key: str, purpose: str) -> None:
        """后台线程查一次余额，结果经信号回主线程。

        **必须绕过余额缓存**：现成的查询入口有 30 秒缓存，而一轮对话常在
        30 秒内结束，读缓存会让差值恒为 0。这里直接调底层 ``fetch_balance``。
        """
        provider = self._deepseek_provider()
        if provider is None:
            return
        try:
            api_key = self.cfg.resolve_api_key(provider)
        except Exception:
            api_key = ""
        if not api_key:
            return

        def worker() -> None:
            total = None
            try:
                from ..island.balance import fetch_balance

                data = fetch_balance(
                    provider.base_url, api_key,
                    verify_ssl=bool(getattr(provider, "verify_ssl", True)),
                )
                total = float(str(data.get("total") or 0) or 0)
            except Exception:
                log.debug("消费统计：余额查询失败", exc_info=True)
            try:
                self._cost_balance_ready.emit(agent_key, purpose, total)
            except RuntimeError:
                pass  # 对象已销毁

        threading.Thread(
            target=worker, name="agent-cost-balance", daemon=True,
        ).start()

    def _on_cost_balance(self, agent_key: str, purpose: str, total) -> None:
        """余额查询回到主线程：按用途写入基线或结算本轮消费。"""
        if total is None:
            self._cost.abort(agent_key)
            return
        if purpose == "baseline":
            self._cost.set_baseline(agent_key, float(total))
            return
        self._settle_cost(agent_key, float(total))

    def _settle_cost(self, agent_key: str, total: float) -> None:
        """结束时的余额回来了：算差值并补一条气泡。"""
        text = self._cost.finish(agent_key, total)
        if not text:
            return  # 拿不到基线 / 功能未启用：静默跳过，不显示错的
        if not hasattr(self.win, "show_bubble"):
            return
        try:
            self.win.show_bubble(text, duration_ms=3600)
        except Exception:
            log.debug("消费统计：气泡显示失败", exc_info=True)

    def _cost_note_start(self, agent_key: str) -> None:
        """Agent 开始干活：记下余额快照。"""
        if not self._cost_enabled():
            return
        self._cost.begin(agent_key)
        self._query_cost_balance(agent_key, "baseline")

    def _cost_finish(self, agent_key: str) -> None:
        """Agent 本轮结束：异步查一次余额，回来后算差值补气泡。

        不走同步等待——余额查询要 0.2s 网络往返，阻塞主线程会卡住界面。
        """
        if not self._cost_enabled():
            # 开关中途被关掉：不能裸 return——_busy 里还留着这个 agent 的
            # 进行态，开关再打开后下次 begin() 会把残留误判成并发。
            self._cost.abort(agent_key)
            return
        if not self._cost.is_tracking(agent_key):
            return
        self._query_cost_balance(agent_key, "done")

    def _emit_sound(self, event_name: str, agent_key: str) -> None:
        """播放 Agent 生命周期音效；所有 Agent 共用一组全局冷却。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("sound_enabled", False):
            return
        if not agent_cfg.get(f"sound_{event_name}_enabled", True):
            return
        path_value = str(agent_cfg.get(f"sound_{event_name}_path", "") or "").strip()
        if not path_value:
            return
        path = resolve_builtin_sound(path_value) if path_value.startswith("builtin:") else Path(path_value).expanduser()
        if path is None or not path.is_file():
            return
        now = self._clock()
        cooldown = max(0.0, float(agent_cfg.get("sound_cooldown_seconds", 2.0)))
        if now - self._sound_last_at.get("global", float("-inf")) < cooldown:
            return
        last_event = self._sound_last_event.get(agent_key)
        if last_event is not None and last_event[0] == event_name and now == last_event[1]:
            return
        self._sound_last_at["global"] = now
        self._sound_last_event[agent_key] = (event_name, now)
        log.info("播放联动音效 event=%s agent=%s path=%s", event_name, agent_key, path)
        play_sound(path, volume=float(agent_cfg.get("sound_volume", 0.65)))

    def _show_link_bubble(self, text: str, *, important: bool, duration_ms: int = 4500,
                          _retried: int = 0) -> None:
        """联动气泡：提醒消息队列非空时一律让路（审批/问题/失败等常驻提醒优先）。

        无提醒队列时：普通气泡直接让路丢弃；重要气泡每 2.5s 重试至多 4 次
        （约 10s 窗口），仍被占才放弃——主动识屏长答复可能占位 15-20s。"""
        if not hasattr(self.win, "show_bubble"):
            return
        # 桌宠隐藏时 show_bubble/show_alert 会静默丢弃：改道灵动岛反馈面
        # （AppShell 经 hidden_bubble_redirect 注入；无注入/岛不可用维持丢弃）。
        # 审批/问题等常驻提示气泡不经本函数（走 show_alert 队列），仍需桌宠可见。
        is_visible = getattr(self.win, "isVisible", None)
        if callable(is_visible) and not is_visible():
            from .. import window_alerts as _window_alerts

            if _window_alerts.redirect_hidden_bubble(self.win, text, duration_ms=duration_ms):
                return
        # 提醒消息队列激活：任何其他弹窗（含重要气泡）都不覆盖提醒
        if getattr(self.win, "_alert_current", None) is not None or \
                getattr(self.win, "_alert_queue", None):
            return
        if not important and getattr(self.win, "_sticky_bubble_active", False):
            # 兼容旧路径：审批等一直挂着的气泡优先
            return
        busy_until = getattr(self.win, "_bubble_busy_until", 0.0)
        # window.hold_bubble 以 time.monotonic() 写入 _bubble_busy_until，这里必须
        # 用同一时钟域比较——曾误用 time.time()（epoch 秒），在真实桌宠上恒判
        # "未被占用"，让位/重试门禁失效（普通气泡顶掉识屏占位、重要气泡不排队
        # 重试直接覆盖）。同步修正于 PR57 合并后审计（F1）。
        if time.monotonic() < busy_until:
            if not important or _retried >= 4:
                return
            QTimer.singleShot(2500, self,
                              lambda t=text, n=_retried: self._show_link_bubble(
                                  t, important=True, _retried=n + 1))
            return
        self.win.show_bubble(text, duration_ms=duration_ms)

    # 阻塞交互兜底清理（approval / question / cordis 共用）。
    # 审批/问题等待期间 Agent 不会走到 turn/end（DSH 仍处于 running）；
    # 一旦出现 turn/end、task_complete、execution/failed、thread_rolled_back
    # 或 AgentStatus idle/sleeping，说明会话已结束/回合已结束/Agent 已停止，
    # 任何仍 pending 的阻塞交互必然失效（DSH 漏发 resolved 的异常场景），
    # 据此精确清掉对应会话（无 sessionId 时清该 agent 全部）的交互弹窗，
    # 防止真实异常也留下永久弹窗。带 rpcId/approvalId 的真实审批由
    # approval/resolved 正常关闭，不受影响。
    _INTERACTION_END_EVENTS = {
        "turn/end", "task_complete", "execution/failed", "thread_rolled_back",
    }

    def _on_interaction_lifecycle(self, agent_key: str, record: dict) -> None:
        """会话/turn 结束或 Agent 停止时，清掉对应会话/agent 的 pending 阻塞交互。

        **按写者过滤**（多 DSH 实例）：记录上的摄入标注（``_scope``）说了这条结束
        帧属于哪个实例，就只有那个实例的交互被撤——A 实例的 ``turn/end`` / 待机
        基线不得撤掉 B 实例还在等的审批/问题。没有标注（直调/非摄入路径）时保持
        旧口径（按会话匹配，无会话则清该 agent 全部），既有调用点行为不变。

        **会话结束帧是"可信来源身份"**（``trusted``）：``turn/end`` / ``task_complete``
        / ``execution/failed`` / ``thread_rolled_back`` 是"这个会话/回合结束了"的
        客观收尾；``AgentStatus idle/sleeping`` 只是"这个写者这会儿不干活"，不是
        收尾声明。区别只在归属集合 overflow 锁存后生效（见 :meth:`_release_owner`）：
        锁存条目只有"**已记录的当事人** + **同一条会话**"才放行，别 owner 或缺会话的
        结束帧都不许一声清掉还有人在等的那条。
        """
        if not isinstance(record, dict):
            return
        event = str(record.get("event") or "")
        session = str(record.get("sessionId") or record.get("session_id") or "")
        ended = (event in self._INTERACTION_END_EVENTS or
                 (event == "AgentStatus" and str(record.get("state") or "") in {"idle", "sleeping"}))
        if not ended:
            return
        self._clear_owned_interactions(agent_key, self._record_writer_owner(record),
                                       session=session,
                                       trusted=event in self._INTERACTION_END_EVENTS)

    def _on_dsh_scope_retired(self, owner: str, gen: int = 0) -> None:
        """桥写者被确认死亡：只撤归属它自己的常驻交互与状态簿记，**不报完成**。

        写者消失不是回合收尾：这里既不注入呈现状态、也不排定/触发完成确认，所以
        死掉的实例不会弹「已完成本轮任务」+ done 音效。别的写者还在等的交互原样
        保留（归属集合裁决，见 :meth:`_release_owner`）。
        """
        if not self._gen_current("dsh", int(gen or 0)):
            return  # 旧代次的队列信号不得动新一代的交互归属
        owner = str(owner or "")
        self._drop_owner_raw("dsh", owner)
        self._clear_owned_interactions("dsh", owner)

    @staticmethod
    def _interaction_kind_for(record: dict) -> str:
        """请求类记录 → 交互 kind（不是请求记录返回 ""，调用方直接跳过）。"""
        event = str((record or {}).get("event") or "")
        if event in ("approval/request", "approval/requested"):
            return "approval"
        if event == "question/requested":
            return "question"
        if event == "cordis/request-run":
            return "cordis"
        return ""

    @staticmethod
    def _interaction_identity_fields(record: dict) -> dict:
        """记录 → pending 条目的身份字段（与登记端同名，供就地匹配）。"""
        return {
            "request_id": record.get("requestId") or record.get("request_id"),
            "rpc_id": record.get("rpcId") or record.get("rpc_id"),
            "approval_id": record.get("approvalId") or record.get("approval_id"),
            "call_id": record.get("callId") or record.get("call_id"),
        }

    def _on_bridge_owner_attested(self, agent_key: str, record: dict) -> None:
        """重复投递的写者归属补登（去重不去归属）。

        同一条逻辑交互被两个实例各写一份时，第二条整条跳过是对的（正文/提示音不
        得双响，见摄入账本），但它证明第二个写者也在等同一条交互——把这个写者并进
        同一条目的归属集合，第一个写者回待机时就不会把用户还在等的气泡撤掉。
        这里只动归属，不重弹正文、不重发信号。

        归属集合**有界**（``_PENDING_OWNERS_MAX``）：写者可能反复重载插件/重投
        同一条在途请求，只加不减就是一条随投递次数线性增长的内存；满时锁存
        ``owners_overflow``，既不丢已有归属也不并进别人的桶（见
        :meth:`_merge_pending_owner`）。
        """
        if not isinstance(record, dict):
            return
        owner = self._record_writer_owner(record)
        if owner is None:
            return
        kind = self._interaction_kind_for(record)
        if not kind:
            return
        session_id = str(record.get("sessionId") or record.get("session_id") or "")
        extra = self._interaction_identity_fields(record)
        if not any(extra.values()):
            return  # 无身份：无从配对，别赌一条
        iid = self._existing_interaction_for(agent_key, kind, extra, session_id)
        item = self._pending_interactions.get(iid) if iid is not None else None
        if item is None:
            return
        owners, overflow = self._merge_pending_owner(
            set(item.get("owners") or ()), bool(item.get("owners_overflow")), str(owner))
        item["owners"] = owners
        item["owners_overflow"] = overflow
        log.debug("重复投递的写者归属已并进同一交互：owner=%s kind=%s", str(owner)[:60], kind)

    # ------------------------------------------------------------------
    # 会话元数据缓存与显示名称解析
    # ------------------------------------------------------------------
    @staticmethod
    def _session_cache_put(cache: dict, key: str, value) -> None:
        """会话级缓存的有界写入（FIFO 淘汰最老条目；缺陷 23）。

        key 是外部会话 ID：缓存只增不减会随会话数常驻数周。淘汰只影响展示
        元数据/显示名——被淘汰的会话下次收到 meta 事件会重新写入，读路径
        （``get_session_display_name`` / ``_session_name_or_empty``）行为不变。
        """
        cache[key] = value
        while len(cache) > AgentLinkManager._SESSION_CACHE_MAX:
            cache.pop(next(iter(cache)), None)

    def _on_session_meta(self, agent_key: str, record: dict) -> None:
        """接收 bridge 发来的 session/meta 事件，写入元数据缓存。"""
        if not isinstance(record, dict):
            return
        session_id = str(record.get("sessionId") or "")
        if not session_id:
            return
        self._session_cache_put(self._session_meta_cache, session_id, {
            "sessionName": str(record.get("sessionName") or ""),
            "projectName": str(record.get("projectName") or ""),
            "agentName": str(record.get("agentName") or ""),
        })
        log.debug("session_meta cached: %s → %s", session_id[:12], self._session_meta_cache[session_id])

    # ------------------------------------------------------------------
    # 模型访问失败提醒
    # ------------------------------------------------------------------
    # alert_id 带 sessionId：多 session 并发模型访问失败时互不顶替。
    # show_alert 的 duration_ms 对 sticky 项无效，寿命由 _model_access_timer 自行管理。
    _MODEL_ACCESS_COOLDOWN_S = 8.0          # 同 session 8 秒内合并为一次
    _MODEL_ACCESS_DURATION_MS = 15000       # 基础展示 15 秒
    _MODEL_ACCESS_MAX_LIFETIME_MS = 30000   # 同一 session 从首次触发起最长保留 30 秒
    _MODEL_ACCESS_PRIORITY = 1              # 高于普通状态气泡；审批/提问（0）可抢占

    @staticmethod
    def _model_access_alert_id(session_key: str) -> str:
        return f"model-access:{session_key}"

    def _on_model_access(self, agent_key: str, record: dict) -> None:
        """处理模型访问失败事件：合并同 session 短时间内连续报错，弹窗提醒。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if not isinstance(record, dict):
            return
        session_id = str(record.get("sessionId") or "")
        if not session_id:
            self._model_access_anonymous_seq += 1
            session_key = f"{agent_key}:anonymous:{self._model_access_anonymous_seq}"
        else:
            session_key = session_id
        now = self._clock()
        cache = self._model_access_cache
        existing = cache.get(session_key)
        supplied_count = record.get("consecutiveRetryCount")
        if supplied_count is None and session_id:
            supplied_count = self._model_access_retry_counts.get((agent_key, session_id), 0)
        try:
            supplied_count = int(supplied_count) if supplied_count is not None else 0
        except (TypeError, ValueError):
            supplied_count = 0
        if existing and now - existing.get("_ts", 0) < self._MODEL_ACCESS_COOLDOWN_S:
            # Prefer the bridge's actual streak; legacy payloads increment locally.
            existing_count = int(existing.get("count", 1) or 1)
            existing["count"] = max(existing_count, supplied_count) if supplied_count else existing_count + 1
            existing["_ts"] = now
            existing["_dismissed"] = False
            self._remember_model_access_record_fields(existing, record)
            self._show_model_access_alert(agent_key, session_key, existing["count"])
            return
        entry = {
            "count": max(1, supplied_count),
            "_ts": now,
            "_first_ts": now,
            "_dismissed": False,
        }
        self._remember_model_access_record_fields(entry, record)
        cache[session_key] = entry
        self._show_model_access_alert(agent_key, session_key, entry["count"])

    def _remember_model_access_record_fields(self, entry: dict, record: dict) -> None:
        """把限流记录的条件字段缓存进条目，供弹窗模板条件注入（缺失自动隐藏）。"""
        record = record if isinstance(record, dict) else {}
        for field in ("errorCode", "errorMessage", "consecutiveRetryCount", "retry"):
            value = record.get(field)
            if value not in (None, ""):
                entry[field] = value

    def _show_model_access_alert(self, agent_key: str, session_key: str, count: int) -> None:
        """展示模型访问失败提醒弹窗，高优先级，带「知道了」按钮，15 秒自动收起。"""
        fallback = (
            "DSH 模型访问失败，本次请求未完成；请稍后重试。"
            if count <= 1 else
            f"DSH 模型访问失败，已连续 {count} 次；请稍后重试。"
        )
        key = "model_access.many" if count > 1 else "model_access.one"
        entry = self._model_access_cache.get(session_key) or {}
        conditional: dict[str, Any] = {}
        for field in ("errorCode", "errorMessage", "consecutiveRetryCount", "retry"):
            value = entry.get(field)
            if value not in (None, ""):
                conditional[field] = value
        session_name = self._session_name_or_empty(session_key)
        if session_name and session_name.strip():
            conditional["sessionName"] = session_name.strip()
        text = self._dialogue(key, fallback, agent_key=agent_key, count=count, **conditional)
        # PhrasePicker's built-in persona text is intentionally allowed to use
        # different wording; only an unavailable/empty renderer falls back.
        if not str(text or '').strip():
            text = fallback
        buttons = [("知道了", lambda sk=session_key: self._dismiss_model_access_alert(sk))]
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(
                text,
                duration_ms=0,             # sticky 项忽略 duration，寿命由 timer 管理
                sticky=True,
                buttons=buttons,
                alert_id=self._model_access_alert_id(session_key),
                priority=self._MODEL_ACCESS_PRIORITY,
                alert_type="model_access",
                metadata={"sessionId": session_key},
            )
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._MODEL_ACCESS_DURATION_MS)
        # 自动收起：默认 15s；如被合并刷新，则按「首次触发 + 30s」硬上限收敛。
        self._schedule_model_access_dismiss(session_key)

    def _schedule_model_access_dismiss(self, session_key: str) -> None:
        """排定模型访问失败提醒的自动收起时间。

        优先按最近一次触发 + 15s；但不超过该 session 首次触发 + 30s 硬上限，
        避免合并刷新把弹窗无限续命。无 QTimer 环境（测试桩）时跳过。"""
        if not hasattr(self.win, "_bubble_busy_until"):
            return  # 测试桩无 QTimer 环境：跳过自动收起，由 dismiss 兜底
        entry = self._model_access_cache.get(session_key)
        if not entry:
            return
        now = self._clock()
        cap_remaining = self._MODEL_ACCESS_MAX_LIFETIME_MS / 1000.0 - (now - entry.get("_first_ts", now))
        base_remaining = self._MODEL_ACCESS_DURATION_MS / 1000.0 - (now - entry.get("_ts", now))
        delay_s = max(0.2, min(base_remaining, cap_remaining))
        self._cancel_model_access_timer(session_key)
        # win 可能是非 QObject 的测试桩：parent 传 None，定时器由本管理器持有生命周期
        parent = self.win if isinstance(self.win, QObject) else None
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda sk=session_key: self._dismiss_model_access_alert(sk))
        self._model_access_timers[session_key] = timer
        timer.start(int(delay_s * 1000))

    def _cancel_model_access_timer(self, session_key: str) -> None:
        timer = self._model_access_timers.pop(session_key, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            timer.deleteLater()

    def _clear_model_access_alerts(self) -> None:
        """清理全部模型访问失败提醒、计数和定时器。"""
        session_keys = set(self._model_access_cache) | set(self._model_access_timers)
        self._model_access_cache.clear()
        # tracker 内部按 (source, sessionId) 留存的连续 streak 也要清：只清外部
        # 镜像的话，重新开启联动后同一会话的新失败会接着旧计数，提醒里出现
        # 「已连续 N 次」虚高。全量 clear 比按镜像键逐个 reset 更稳——镜像键
        # 未必覆盖 tracker 的全部键。
        self._model_access_tracker.clear()
        self._model_access_retry_counts.clear()
        for session_key in list(self._model_access_timers):
            self._cancel_model_access_timer(session_key)
        for session_key in session_keys:
            if hasattr(self.win, "resolve_alert"):
                self.win.resolve_alert(self._model_access_alert_id(session_key))

    def _dismiss_model_access_alert(self, session_key: str) -> None:
        """用户点击「知道了」或超时自动收起：清理缓存并关闭提醒。"""
        cache = self._model_access_cache
        entry = cache.pop(session_key, None)
        if entry:
            entry["_dismissed"] = True
        self._cancel_model_access_timer(session_key)
        alert_id = self._model_access_alert_id(session_key)
        if hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(alert_id)

    @staticmethod
    def _llm_error_alert_id(session_key: str) -> str:
        return f"llm-error:{session_key}"

    def _on_llm_error(self, agent_key: str, record: dict) -> None:
        """处理 LLM API 错误事件（llm_error，errorKind=api）：弹窗提醒。

        errorCode 是上游真实错误码（如 bad_response_status_code），不再替换成
        分类别名；errorKind 承载分类语义（api=AI 服务错误，非模型访问失败）。

        llm_error 同时是本回合「收尾后爆雷」在呈现侧的打标点之一：这条记录没有
        state 字段（不进 legacy 状态流），收敛器产出的 ERROR 也不注入呈现管线
        （``_on_dsh_converged_state`` 只注入 thinking/success），所以成功收尾之后
        到来的 llm_error 只有这里能取消 pending 的完成确认。放在可见性/冷却早退
        之前：错误是本回合的客观事实，与提醒是否呈现无关（隐藏期改道灵动岛、
        冷却期都不能因此误报成功）。归属同样按记录上的摄入标注（``_record_writer_owner``）：
        撤确认与打标都只对**这条错误自己的写者**生效。
        """
        self._abort_done_check_on_error(
            agent_key, self._record_writer_owner(record if isinstance(record, dict) else {}))
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if not isinstance(record, dict):
            return
        session_key = str(record.get("sessionId") or agent_key)
        now = self._clock()
        cache = self._llm_error_cache
        # LLM API 错误不合并，每次错误都提醒（但用冷却时间防刷屏）
        existing = cache.get(session_key)
        if existing and now - existing.get("_ts", 0) < self._MODEL_ACCESS_COOLDOWN_S:
            return  # 冷却期内忽略
        cache[session_key] = {
            "_ts": now,
            "_dismissed": False,
        }
        error_message = str(record.get("errorMessage") or "AI 服务不可用")
        error_code = str(record.get("errorCode") or "UNKNOWN")
        error_kind = str(record.get("errorKind") or "api")
        fallback = f"AI 服务错误（{error_code}）：{error_message}"
        key = "llm_error.api"
        text = self._dialogue(key, fallback, agent_key=agent_key)
        buttons = [("知道了", lambda sk=session_key: self._dismiss_llm_error_alert(sk))]
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(
                text,
                duration_ms=0,
                sticky=True,
                buttons=buttons,
                alert_id=self._llm_error_alert_id(session_key),
                priority=self._MODEL_ACCESS_PRIORITY,
                alert_type="llm_error",
                metadata={"sessionId": session_key, "errorCode": error_code, "errorKind": error_kind},
            )
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._MODEL_ACCESS_DURATION_MS)
        self._schedule_llm_error_dismiss(session_key)

    def _schedule_llm_error_dismiss(self, session_key: str) -> None:
        """排定 LLM 错误提醒的自动收起时间。"""
        if not hasattr(self.win, "_bubble_busy_until"):
            return
        entry = self._llm_error_cache.get(session_key)
        if not entry:
            return
        delay_s = self._MODEL_ACCESS_DURATION_MS / 1000.0
        self._cancel_llm_error_timer(session_key)
        parent = self.win if isinstance(self.win, QObject) else None
        timer = QTimer(parent)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda sk=session_key: self._dismiss_llm_error_alert(sk))
        self._llm_error_timers[session_key] = timer
        timer.start(int(delay_s * 1000))

    def _cancel_llm_error_timer(self, session_key: str) -> None:
        timer = self._llm_error_timers.pop(session_key, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            timer.deleteLater()

    def _dismiss_llm_error_alert(self, session_key: str) -> None:
        """用户点击「知道了」或超时自动收起：清理缓存并关闭提醒。"""
        cache = self._llm_error_cache
        entry = cache.pop(session_key, None)
        if entry:
            entry["_dismissed"] = True
        self._cancel_llm_error_timer(session_key)
        alert_id = self._llm_error_alert_id(session_key)
        if hasattr(self.win, "resolve_alert"):
            self.win.resolve_alert(alert_id)

    def _on_user_action(self, agent_key: str, record: dict) -> None:
        """用户介入信号（user_action 事件）：DSH 审批决定/回答 → 关闭对应弹窗。

        action 类型：
        - approval_decided / approval_resolved：审批已决定
        - question_resolved：用户已回答问题

        **与语义层同一份摄入**：这条记录同时喂 ``normalized_event`` 与这个旧通道，
        所以这里必须走同一个 ``_resolve_matching_interaction``（来源 + 会话 + 同字段
        身份）——旧实现把来源丢掉、还把 rpcId 与 requestId、approvalId 与 callId 当
        互通匹配，于是语义层刚关掉本来源那条之后，这里会把**别的来源**的同 id 交互
        当成唯一候选一起关掉（astra 复现：dsh 的 user_action 关掉 claude 的审批）。

        记录不带任何 id 时退化为 ``_resolve_identity_less_interaction``（该来源唯一
        一条无身份提示才关），与 ``_on_approval_resolved`` 的旧路径兜底同口径。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if not isinstance(record, dict):
            return

        action = str(record.get("action") or "")
        kind = ""
        if action in ("approval_decided", "approval_resolved"):
            kind = "approval"
        elif action == "question_resolved":
            kind = "question"
        if not kind:
            return
        # 会话未知就如实留空（旧实现用 agent_key 顶替会话，把来源和会话混成一个键）。
        session_id = str(record.get("sessionId") or record.get("session_id") or "")
        identities = {
            "request_id": str(record.get("requestId") or ""),
            "rpc_id": str(record.get("rpcId") or ""),
            "approval_id": str(record.get("approvalId") or ""),
            "call_id": str(record.get("callId") or ""),
        }
        if self._resolve_matching_interaction(kind=kind, agent_key=agent_key,
                                              session_id=session_id,
                                              identities=identities) is not None:
            return
        if not any(identities.values()):
            self._resolve_identity_less_interaction(kind, agent_key, session_id)

    def _on_unknown_bridge_event(self, agent_key: str, record: dict) -> None:
        """DSH 桥接写出的未知事件 → 提醒用户更新/重装 bridge。

        事件名不在 Pet 任何识别路径（语义层 / 状态机 / _poll 直通名单）里，
        大概率是 bridge 与桌宠版本不匹配写出的新事件。同一 agent 在冷却
        窗口内只提醒一次——未知事件可能成串到达，逐条弹窗会刷屏。
        """
        if not isinstance(record, dict):
            return
        now = self._clock()
        last = self._unknown_bridge_reminded_at.get(agent_key)
        if last is not None and now - last < self._UNKNOWN_BRIDGE_REMIND_COOLDOWN_S:
            return
        self._unknown_bridge_reminded_at[agent_key] = now
        name = self.agent_names.get(agent_key, agent_key)
        event = str(record.get("event") or "").strip()
        text = self._dialogue(
            "bridge.unknown",
            f"检测到未知的桥接事件（{event}），当前桌宠不认识它——"
            "可能是 bridge 版本过旧，请更新或重装 bridge 插件",
            name=name,
            event=event,
        )
        self.win.show_bubble(text, duration_ms=6000)

    def get_session_display_name(self, session_id: str) -> str:
        """解析会话的人类可读展示名（「projectName · sessionName」组合串）。

        仅用于气泡前缀、探索气泡等**展示**场景；台词模板里的 ``{sessionName}``
        字段必须走 ``_session_name_or_empty()``（只取会话名），不要用本方法返回值
        注入，避免 {sessionName} 与 {projectName} 语义重复。

        降级链：cache 中的 projectName+sessionName → cache.agentName → 截短 sessionId → 完整 sessionId。
        控制请求（interrupt/replan）仍严格使用 sessionId，此处仅用于展示。
        """
        meta = self._session_meta_cache.get(session_id)
        if meta:
            project_name = str(meta.get("projectName") or "")
            session_name = str(meta.get("sessionName") or "")
            # 优先用 projectName + sessionName 组合（更精确）
            if project_name and session_name:
                return f"{project_name} · {session_name}"
            if session_name:
                return session_name
            agent_name = str(meta.get("agentName") or "")
            if agent_name:
                return agent_name
        # 降级：截短 sessionId，避免暴露完整内部标识
        short_id = session_id[:8] if len(session_id) > 8 else session_id
        return f"DSH · {short_id}"

    def _session_name_or_empty(self, session_id: str) -> str:
        """台词注入用会话名：只取会话自己的名字（session/meta 的 sessionName）。

        ``get_session_display_name()`` 返回的「projectName · sessionName」组合串
        是给气泡前缀/探索气泡用的人类可读展示名；台词模板的 ``{sessionName}``
        字段语义 = 会话名自身，``{projectName}`` 是独立字段——绝不用组合串冒充
        会话名（否则两字段语义重复，用户在模板里无法单独引用）。无真实会话名时
        返回空串，由条件渲染（autohide）隐藏占位符，也不把 sessionId 截短占位冒充。
        """
        meta = self._session_meta_cache.get(session_id) or {}
        return str(meta.get("sessionName") or "").strip()

    # ------------------------------------------------------------------
    # 硬失败（execution/failed）：DSH 已决定本轮不再继续，直接提醒
    # ------------------------------------------------------------------
    _FAIL_ANIM_KEYWORDS = ("失败", "冒烟", "晕", "倒下", "昏", "扑街", "求救", "哭了", "委屈")
    _FAIL_REMINDER_MS = 6000
    #: 硬失败提醒的提醒分类。**必须显式给**，不能吃 ``show_alert`` 的默认值
    #: ``watchdog``：那是普通提醒档，会被免打扰门禁
    #: （``window_alerts.alert_survives_suppression``）压进暂存队列——「审批、报错
    #: 照常提醒」是规格，用户在最需要看到报错的时刻反而什么都看不到。
    _FAIL_ALERT_TYPE = "execution/failed"
    #: 与既有错误类提醒（``model_access`` / ``llm_error``）同档优先级：高于普通
    #: 状态气泡与看门狗档，正在展示的低优先提醒被它顶掉；审批/提问（0）仍可抢占。
    _FAIL_ALERT_PRIORITY = 1

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

        payload 来自 bridge 的 execution/failed（脱敏）：只含 failureType /
        retryExhausted / retries / errorCode / errorMessage，不带 400 错误正文。
        failureType 取值（与活动/过程事件的 tool 字段解耦）：
          - model_retry_exhausted：模型请求链连续重试后仍失败
          - tool_failed：工具调用最终失败
        模型访问失败抑制只认真正的模型访问失败（errorCode 属限流类码或消息含限流关键字），
        重试耗尽失败不并入模型访问失败抑制——那是另一条语义（failure.retry），不重复提醒。

        本方法同时是本回合硬失败的**打标点**（GUI 侧、按 agent；execution/failed
        到 AgentLinkManager 只有这一条既有分发链路）：桥接在 turn/end 判定
        reason.kind === "error" 后先写 execution/failed，随后仍无条件转发同一条
        turn/end，收敛器把非中止的 turn/end 收敛为 success——不打标就是失败回合照样
        排定完成确认、800ms 后弹「已完成本轮任务」并播 done 音效（误报成功）。
        两个标记与 error 周期（``_on_agent_state`` 的 busy→error 分支）同款：
        ``_saw_error`` 掐掉 done 音效、``_saw_alert`` 把收尾文案切离「已完成」
        口径——失败回合的完成确认不止 success 边沿一条（桥接的 AgentStatus 聚合
        基线与 session/event 是两条通道，idle 可能先于 execution/failed 落地，
        busy→idle 会先排定确认；那条路径没有 success 边沿可拦，只能靠文案标记）。
        两个标记同时按**写者归属**记一份（``_owner_error_marks`` / ``_owner_alert_marks``，
        归属取记录上的摄入标注）：失败是某一写者本轮的回合事实，A 实例的失败不得
        掐掉 B 实例成功收尾的音效与文案（见 ``_writer_marked``）。
        打标放在最前，先于可见性/模型访问抑制的早退：失败是本回合的客观事实，
        与提醒是否呈现无关（隐藏期改道灵动岛、抑制期都不能因此误报成功）。

        提醒分类走 ``_FAIL_ALERT_TYPE`` / ``_FAIL_ALERT_PRIORITY``（不得吃
        ``show_alert`` 的默认 ``watchdog``）：报错属免打扰的穿透类，压成普通提醒
        就违背「审批、报错照常提醒」的规格。隐藏态走灵动岛改道，那条改道要求
        ``not buttons and not bubble_blocked``，本提醒本就无按钮、非 sticky，不受影响。
        """
        owner = self._record_writer_owner(payload if isinstance(payload, dict) else {})
        self._saw_error.add(agent_key)
        self._saw_alert.add(agent_key)
        self._mark_owner(self._owner_error_marks, agent_key, owner or "", on=True)
        self._mark_owner(self._owner_alert_marks, agent_key, owner or "", on=True)
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        payload = payload if isinstance(payload, dict) else {}
        name = self.AGENT_NAMES.get(agent_key, agent_key)

        session_key = str(payload.get("sessionId") or agent_key)
        active_model_access = self._model_access_cache.get(session_key)

        error_code = str(payload.get("errorCode") or "").strip().upper()
        error_message = str(payload.get("errorMessage") or payload.get("errorText") or "").lower()

        MODEL_ACCESS_ERROR_CODES = {
            "429",
            "RATE_LIMIT",
            "TOO_MANY_REQUESTS",
            "RESOURCE_EXHAUSTED",
        }

        is_model_access_failure = (
                error_code in MODEL_ACCESS_ERROR_CODES
                or "429" in error_message
                or "rate limit" in error_message
        )

        if active_model_access and not active_model_access.get("_dismissed") and is_model_access_failure:
            return

        # 失败动画（若角色素材有）；没有就保持当前动作，仅弹气泡
        anim = self._pick_fail_anim()
        if anim and hasattr(self.win, "request_link_anim"):
            self.win.request_link_anim(anim)
        failure_type = str(payload.get("failureType") or "").strip()
        retry_exhausted = bool(payload.get("retryExhausted"))
        # execution/failed 记录条件字段（缺失不注入，渲染端自动隐藏占位符）
        conditional: dict[str, Any] = {}
        for field in ("failureType", "errorCode", "errorMessage", "retries", "retryExhausted"):
            value = payload.get(field)
            if value not in (None, ""):
                conditional[field] = value
        conditional.update(self._session_conditional(payload))
        if retry_exhausted or failure_type == "model_retry_exhausted":
            text = self._dialogue("failure.retry", f"{name} 本轮运行失败——模型请求多次重试后仍未成功，需要检查或重新运行", agent_key=agent_key, name=name, **conditional)
        elif failure_type == "tool_failed":
            text = self._dialogue("failure.tool", f"{name} 本轮运行失败——工具执行最终失败，需要检查或重新运行", agent_key=agent_key, name=name, **conditional)
        else:
            text = self._dialogue("failure.generic", f"{name} 本轮运行失败，需要检查或重新运行", agent_key=agent_key, name=name, **conditional)
        if hasattr(self.win, "show_alert"):
            self._show_alert_compat(
                text,
                duration_ms=self._FAIL_REMINDER_MS,
                sticky=False,
                priority=self._FAIL_ALERT_PRIORITY,
                alert_type=self._FAIL_ALERT_TYPE,
            )
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._FAIL_REMINDER_MS)
