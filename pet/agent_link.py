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
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable
from urllib.parse import unquote

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from . import agent_cost as agent_cost_mod
from . import harness_launcher
from .click_sound import play_sound, resolve_builtin_sound
from .agent_event_protocol import parse_agent_event
from .agent_event_normalizer import normalize_event
from .dsh_state import DshStateConverger, candidate_ports as _dsh_state_candidate_ports
from .model_access_tracker import ModelAccessTracker
from .node_runtime import augmented_path as _augmented_path
from .node_runtime import global_node_modules_roots

from .persona_phrases import PhrasePicker
from .persona_template import CONDITIONAL_PARAMETERS, LEGACY_HIDDEN_PARAMETERS
from .speech_bubble_text import truncate_bubble_text

log = logging.getLogger("dsh-pet-standalone")

_LIVE_AGENT_LINK_MANAGERS: weakref.WeakSet = weakref.WeakSet()
_LIVE_AGENT_MONITORS: weakref.WeakSet = weakref.WeakSet()

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


def _bridge_writer_alive(pid: int) -> bool | None:
    """桥文件写者（``dsh-<pid>.jsonl``）是否还活着。

    True = 存活；False = 确定已死；**None = 判定失败**（平台探活不可用/异常），
    调用方按保守处理——不清理、照常轮询。复用 ``slot_manager.pid_alive``
    （Windows 走 OpenProcess + GetExitCodeProcess 的 STILL_ACTIVE 口径，其余
    平台 ``kill(pid, 0)``），不另造一套探活。
    """
    if pid <= 0:
        return None
    try:
        from .slot_manager import pid_alive
        return bool(pid_alive(pid))
    except OverflowError:
        # posix 侧 os.kill 对超出 pid_t 表示范围的 pid 抛 OverflowError（非
        # OSError，穿透 pid_alive 的 OSError 捕获）：这样的 pid 不可能存在，
        # 判定为已死，与 Windows（OpenProcess 失败 → False）口径对齐。
        return False
    except Exception:
        log.debug("桥文件写者探活失败 pid=%s", pid, exc_info=True)
        return None


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
        # 扫描节流的时钟用 monotonic：它是纯间隔比较，墙上时钟被 NTP/手动改时
        # 不能把「多久没扫描」算歪（后拨会让新一轮扫描迟迟不来）。
        now = time.monotonic()
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
    """监视器向管理器传递的状态/工具事件及其启动代次。"""

    agent: str
    kind: str
    gen: int = 0
    state: str = ""
    tool: str = ""


class BaseAgentMonitor(QObject):
    """Agent 监视器抽象基类。"""

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
        # QObject 的 destroyed 槽在 PySide6 下不可靠地调用 bound method；
        # 连接无 receiver 的 callable，避免窗口销毁后遗留 daemon worker。
        self._destroyed_conn = self.destroyed.connect(
            lambda *_: BaseAgentMonitor._destroyed_guard(self)
        )
        self._destroy_guard_ran = False
        self._destroy_guard_lock = threading.Lock()

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
        self._worker_stop.set()
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
        """作废当前代次并发出停止信号，不阻塞 GUI。"""
        self._emit_gen = -1
        self._running = False
        self._paused = False
        with self._outbox_lock:
            self._outbox.clear()
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
                signal.emit(*args)

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

    def _emit_state(self, state: str, gen: int) -> None:
        event = AgentEvent(self.agent_key, "state", gen=gen, state=state)
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
            legacy_signal.emit(*legacy_args)
            event_signal.emit(*event_args)
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
        signal.emit(*args)

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

    def _on_parsed_record(self, data: dict) -> None:
        """子类挂钩：每条解析后的桥接/transcript 记录（worker 线程内调用）。

        单读方语义：记录只在本类 ``_poll`` 里解析一次，信号分派与该钩子
        （DshMonitor 用它喂 dsh_state 收敛器）是同一份解析结果的两个消费者。
        """

    def _emit_unified_event(self, data: dict) -> None:
        try:
            event = parse_agent_event(data, source_hint=self.agent_key, agent_name_hint=self.agent_key)
            normalized = normalize_event(event)
            if normalized is not None:
                self.normalized_event.emit(normalized)
        except Exception:
            log.debug("统一 AgentEvent 解析失败", exc_info=True)

    def _poll(self, gen: int | None = None) -> None:
        emit_gen = self._emit_gen if gen is None else gen
        lines = self._tailer.read_new_lines()
        for line in lines:
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
                        self.normalized_event.emit(normalized)
                except Exception:
                    normalized = None  # 解析失败视为语义层未识别，防止上一行残留值污染
                    log.debug("统一 AgentEvent 解析失败", exc_info=True)
                # 原始记录转发（兼容旧消费者）
                self._emit(self.raw_record, (self.agent_key, data))
                # 单读方第二消费者：同一份解析记录喂状态收敛器（仅 DshMonitor 实装）
                self._on_parsed_record(data)
                # 审批请求提醒：一次性事件，收到即发信号（不进入状态机，
                # 因为 DSH 等审批时 agent 仍在 running，状态还是 working）。
                # 只收桥接后的 UI 事件 approval/request 与兼容旧名
                # approval/requested；裸 approval/asked 只是状态/审计信号（驱动
                # dsh_state 锁存 waiting_approval），绝不驱动桌宠审批弹窗——普通
                # 工具调用（如 pwsh 跑 Get-Location）被宿主标成 approval/asked
                # 也不能误触发。减法后气泡是纯提示（无点选回写）。
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
                self._emit_state(normalized, emit_gen)
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
    # dsh_state_changed(from_state, to_state, source_event)；from_state 为 ""
    # 表示首个状态；source_event 为触发事件的桥接事件名（探活基线为 ""），
    # 供管理器做同源气泡去重（cordis/approval 有专属常驻气泡，不再弹通用提醒）。
    dsh_state_changed = Signal(str, str, str)
    # 真人用户消息 → (session_id, text)：对话开始的稳定触发点（sourceKind
    # 过滤在收敛器内完成；减法后桥接不再落明文 text，实参恒为 ""）。
    dsh_user_message = Signal(str, str)

    _ONLINE_PROBE_EVERY_N_POLLS = 2  # 1.5s × 2 = 3s，对齐旧 DshStateTracker 探活节拍

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
        # 状态收敛器（纯逻辑）：8 态收敛 + 审批/问题锁存
        self._converger = DshStateConverger()
        # None = 未探测过；False 时收敛器不消费事件（离线态优先）
        self._online: bool | None = None
        self._probe_tick = 0

    # ------------------------------------------------------------ 单读方：探活 + 收敛
    def start(self) -> bool:
        self._converger.reset()
        self._online = None
        self._probe_tick = 0
        return super().start()

    def _worker_started(self) -> None:
        # 首轮立即探活，让 offline/idle 基线尽早落下（旧跟踪器 start() 同款语义）
        self._probe_online()

    def _poll(self, gen: int | None = None) -> None:
        self._probe_tick += 1
        if self._probe_tick >= self._ONLINE_PROBE_EVERY_N_POLLS:
            self._probe_tick = 0
            self._probe_online()
        super()._poll(gen)
        # 锁存超时兜底：DSH 漏发 approval/decided / question/resolved 时强制回 working
        self._emit_converged(self._converger.tick())

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
        self._emit_converged(self._converger.set_online(online))

    def _on_parsed_record(self, data: dict) -> None:
        if self._online is False:
            return  # DSH 离线时不消费事件（离线态优先）
        self._emit_converged(self._converger.handle_record(data))

    def _emit_converged(self, outputs: list) -> None:
        for item in outputs:
            if item[0] == "state":
                self._emit(self.dsh_state_changed, (item[1], item[2], item[3]))
            elif item[0] == "user_message":
                self._emit(self.dsh_user_message, (item[1], item[2]))


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
    """Return whether another local variant still uses a global Agent integration."""
    config_dir = Path(config.dir)
    seen: set[Path] = set()
    candidates: list[Path] = []
    try:
        candidates.append(config_dir / "config.json")
        candidates.extend(config_dir.glob("config-*.json"))
        candidates.extend(config_dir.parent.glob("dsh-pet-standalone*/config*.json"))
    except OSError:
        pass
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if config.path and path == config.path:
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
    # (from_state, to_state)；from_state 为 "" 表示首个状态
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
    _ACTIVITY_MIN_INTERVAL = 10.0    # 同 Agent 过程气泡最小间隔
    _ACTIVITY_GLOBAL_MIN = 8.0       # 全局最小间隔（多 Agent 并发防刷屏）
    _ACTIVITY_SAME_LABEL = 60.0      # 同一工具文案 60s 内不重复
    _ACTIVITY_TEXT_LIMIT = 80        # 过程汇报气泡文案上限（超出截断加「…」）
    _BUSY_STATES = ("working", "thinking")
    _DONE_CONFIRM_MS = 800   # busy→idle 稳定确认窗口（过滤 working→idle→working 抖动）
    _DONE_COOLDOWN_S = 5.0   # 同 Agent 完成气泡最小间隔（最后一道保险）
    _UNKNOWN_BRIDGE_REMIND_COOLDOWN_S = 600.0  # 未知桥接事件提醒：同 agent 10 分钟内最多一次

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
        self._done_pending: dict[str, QTimer] = {}   # agent → 稳定确认定时器
        self._done_cooldown: dict[str, float] = {}   # agent → 上次完成气泡时刻
        self._unknown_bridge_reminded_at: dict[str, float] = {}  # agent → 上次未知桥接事件提醒时刻
        self._saw_alert: set[str] = set()            # busy 周期内出现过 attention/error 的 Agent
        self._saw_error: set[str] = set()            # busy 周期内真正出现过 error 的 Agent
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
        # "session_id"?, "alert_id"}。审批 / 用户问题都是「阻塞 Agent 等待用户输入」的
        # 交互，统一处理：气泡「一直挂到 resolved」。
        # interaction_id 优先用 rpcId（同一审批/问题的稳定标识），无 rpcId 时用
        # agent+kind+本地序号（降级提示路径）——**同一 agent 的多个审批各自独立
        # 存储**，不再以 agent_key 为键互相覆盖；resolved 按 rpcId 精确匹配关闭，
        # 绝不错关闭其他并发的审批。
        self._pending_interactions: dict[str, dict] = {}
        self._interaction_seq = 0  # 无 rpcId 的降级提示交互本地序号

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
        from .agent_event_normalizer import InteractionResolvedEvent, RetryEvent
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
        candidates = []
        for iid, item in self._pending_interactions.items():
            if item.get("kind") != event.kind or item.get("agent_key") != event.source:
                continue
            if event.session_id and str(item.get("session_id") or "") != event.session_id:
                continue
            identities = (event.request_id, event.rpc_id, event.approval_id, event.call_id)
            item_ids = (str(item.get("request_id") or ""), str(item.get("rpc_id") or ""), str(item.get("approval_id") or ""), str(item.get("call_id") or ""))
            if any(value and value == candidate for value in identities for candidate in item_ids):
                candidates.append(iid)
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])
        elif len(candidates) > 1:
            self._resolve_interaction(candidates[0])
        else:
            log.debug("interaction/resolved unmatched source=%s session=%s", event.source, event.session_id)

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
                monitor.start()
                if isinstance(monitor, DshMonitor):
                    # 启动自检（后台、每实例一次）：重新打包/换构建目录后，profile 里
                    # 记的 link 可能已指向旧构建；只在陈旧时刷新，绝不新建安装。
                    monitor.schedule_link_refresh_check()
            elif not should_run and monitor._running:
                monitor.stop()

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
                self._dialogue("agent.missing", f"已开启 {name} 联动监听，但没检测到本机安装 {name}——装了它我才能感知到哦", name=name),
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
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()

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
        for timer_dict in (self._done_pending, self._model_access_timers, self._llm_error_timers):
            for timer in timer_dict.values():
                try:
                    timer.stop()
                except RuntimeError:
                    pass
            timer_dict.clear()
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

    def _on_agent_state_event(self, event: AgentEvent) -> None:
        self._on_agent_state(event.agent, event.state, event.gen)

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
                                source_event: str = "") -> None:
        """订阅 DSH 收敛状态变化（单读方收敛器 → 呈现管线 + AppShell 转发）。

        - 转发给 AppShell（灵动岛 set_agent_active / offline 收尾）；
        - thinking：legacy AgentStatus 基线只有 working/idle，thinking 由收敛器
          补进联动管线（思考气泡/动画——对话开始的稳定触发点之一，与真人消息
          双保险，呈现管线自带同态去重）；
        - success / error：收敛器**独占**的终态，两者**必须同时**补进联动管线。
          ``success`` 来自 ``turn/end``、``error`` 来自 ``llm/retry`` / ``llm_error``；
          它们都不在 legacy 词汇表 ``VALID_STATES`` 里，legacy 分派一律返回空串
          直接丢弃，收敛器出口是唯一通路。
          - 漏掉 ``success`` → 完成气泡与 done 音效几乎永不触发（#234 主诉）；
          - 漏掉 ``error`` → DSH 的出错回合在呈现管线里完全不可见（``_saw_error``
            / ``_saw_alert`` 永不置位，done 音效的「本轮出过错就不播」闸门失效），
            并被 ``busy→success`` 边沿误报成「成功完成」（第二轮自检发现的误报）；
        - waiting_approval：审批把 Agent 卡在等用户输入——计入「需要看一眼」
          （完成后不误报成功），并弹通用 attention 纯提示气泡（busy 中也必须
          提示，不走 ``_on_agent_state`` 的「busy 后不弹 attention」分支）。
          例外：cordis/request-run 与 approval/request 各有专属常驻提示气泡
          （`_on_cordis_request` / `_on_approval_request`），同一条事件的
          通用 attention 气泡跳过，防止双弹；
        - waiting_question 只更新状态（岛指示/动画），不弹通用气泡——
          常驻问题气泡已由 ``_on_question_request`` 呈现（同事件双弹回归）。
        """
        self.dsh_state_changed.emit(from_state, to_state)
        if to_state == "offline":
            self.dismiss_all_interactions()
            return
        if to_state == "thinking":
            self.notify_dsh_state("thinking")
            return
        if to_state in ("success", "error"):
            # 收敛器**独占**的终态：success 与 error 都不在 legacy 词汇表
            # VALID_STATES 里，legacy 分派对 turn/end、llm/retry、llm_error、
            # AgentStatus{state:"success"/"error"} 一律返回空串直接丢弃，
            # 收敛器出口是它们唯一的通路。
            #
            # 两者必须同时补：只补 success 会漏掉 error——DSH 的出错回合在呈现
            # 管线里将完全不可见（_saw_error/_saw_alert 永不置位 → done 音效的
            # 「本轮出过错就不播」闸门失效），而且会被下游 busy→success 边沿
            # 误报成「成功完成」。这是第二轮自检发现的真实误报（详见
            # docs/PR-REPORT-DSH-SUCCESS-DONE-NOTIFY-2026-10-07.md §十）。
            self.notify_dsh_state(to_state)
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

    def _on_dsh_converged_user_message(self, session_id: str, text: str) -> None:
        """真人消息 = 对话开始：与状态边沿竞态解耦的稳定触发点。

        sourceKind 过滤已在收敛器完成——只有真人输入（含旧版桥接记录无字段的
        兼容）到这里；agent.inject() 注入上下文不会到这里。
        """
        self.dsh_user_message.emit(session_id, text)
        self.notify_dsh_state("thinking")

    def _on_agent_state(self, agent_key: str, state: str, gen: int = 0) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。"""
        if not self._gen_current(agent_key, gen):
            return
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
            self._cancel_done_check(agent_key)
            self._saw_alert.discard(agent_key)
            if prev_raw != "error":
                self._saw_error.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in self._BUSY_STATES:
            self._saw_alert.add(agent_key)
            if state == "error":
                self._saw_error.add(agent_key)
            # Claude 的回合结束信号是 Stop→attention 而非 idle：busy 后的
            # attention/error 同样进入完成确认（800ms 内回忙则取消——例如
            # SubagentStop 后主 Agent 继续干活、工具报错后重试）。
            self._schedule_done_check(agent_key)
        elif state == "success" and (prev_raw in self._BUSY_STATES or prev_raw is None):
            # DSH 回合成功结束（turn/end → success）：与 busy→idle 同级视为完成。
            # DSH 收尾后正常**不会**再到 idle（桥只在极少数情况补发
            # AgentStatus{state:"idle"}），缺这条边沿时完成提醒表现为「概率触发」：
            # 只有恰好撞上那次补发才响（#234）。复用既有 800ms 稳定确认与
            # _DONE_COOLDOWN_S，不引入新的抖动面。
            #
            # 前置放宽到「忙碌态 **或 从未见过任何状态**」：后者是中途挂载
            # （桌宠/联动在回合进行中才启动、或新的 dsh-{pid}.jsonl 首次被发现，
            # 回填防护会跳过 turn/start），此时 prev_raw 为 None——只认忙碌态会让
            # 该回合的 success 成为死路（第二轮自检发现的静默回合）。
            # 失败/中止的回合不受影响：那些情况下 prev_raw 是 error/attention 等
            # 已见过的终态，仍然按既有语义不在这里补完成（error 周期由
            # busy→error 边沿自己排的完成确认负责，文案走「自己看一眼」）。
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
                label=label, name=name, **conditional,
            )
        elif tool:
            text = self._dialogue("approval.tool", f"{prefix}{name} 有审批等你决定（{tool}），请到 DSH 界面处理～",
                                  label=tool, name=name, **conditional)
        else:
            text = self._dialogue("approval.generic", f"{prefix}{name} 有审批等你决定，请到 DSH 界面处理～",
                                  name=name, **conditional)
        self._register_interaction(
            agent_key, kind="approval", text=text, tool=tool,
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
        self._register_interaction(agent_key, kind="cordis", text=f"{name} 请求运行：{purpose}", request_id=request_id, session_id=fields.get("agentId") or fields.get("sessionId"))

    def _on_cordis_resolved(self, agent_key: str, payload: dict) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self._close_interaction_by_id("cordis", str(payload.get("requestId") or ""), "", str(payload.get("agentId") or payload.get("sessionId") or agent_key))

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
            agent_key, kind="question", text=self._question_text(name, questions, prefix=prefix,
                                                                 conditional=conditional),
            questions=questions,
            rpc_id=payload.get("rpcId"),
            call_id=payload.get("callId"),
            session_id=session_id,
        )

    def _question_text(self, name: str, questions: list, *, prefix: str = "",
                       conditional: dict | None = None) -> str:
        """把 questions 载荷排版成气泡文案（单行紧凑）。

        泡泡是图片气泡：normalize_bubble_text 会把换行折叠成空格，且 sticky 只
        显示第一页——所以选项用「 / 」内联拼接而非强行多行，保证「常驻问题弹窗」
        在小气泡里完整可见。"""
        conditional = conditional or {}
        if not questions:
            return self._dialogue("question.empty", f"{prefix}{name} 在等你回答一个问题，快去看一下～",
                                  name=name, **conditional)
        if len(questions) > 1:
            if self._questions_all_have_options(questions):
                return self._dialogue("question.many", f"{prefix}{name} 有 {len(questions)} 个问题等你回答，快去看一下～",
                                      count=len(questions), name=name, **conditional)
            # 含自由文本分支：整批必须回 DSH 界面输入，引导不随台词被覆盖
            return self._with_dsh_input_hint(self._dialogue(
                "question.many",
                f"{prefix}{name} 有 {len(questions)} 个问题等你回答"
                "（含文本输入，请到 DSH 界面输入文本回答）～",
                count=len(questions), name=name, **conditional,
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
            body=body, name=name, **conditional,
        )
        return self._with_dsh_input_hint(text)

    def _with_dsh_input_hint(self, text: str) -> str:
        """台词缺「回 DSH 输入」引导时补上，避免自定义/预设文案丢掉操作指引。"""
        if "DSH 界面输入" in str(text):
            return text
        return f"{text}（请到 DSH 界面输入文本回答）"

    def _interaction_key(self, agent_key: str, kind: str, rpc_id) -> str:
        """生成稳定交互 id：有 rpcId 用 rpcId（同一审批/问题的稳定标识），
        无 rpcId（旧路径降级提示）用 agent+kind+本地序号保证唯一。"""
        if rpc_id:
            return f"{kind}:{rpc_id}"
        self._interaction_seq += 1
        return f"{kind}:{agent_key}:hint{self._interaction_seq}"

    def _register_interaction(self, agent_key: str, *, kind: str, text: str, **extra) -> str | None:
        """统一登记一条阻塞型交互：进 pending + 打 alert 标记 + 挂常驻气泡。

        返回该交互的稳定 interaction_id（resolve 按它精确定位）。

        - **同一 agent 的多个审批/问题各自独立存储**（按 interaction_id），
          不再以 agent_key 为键互相覆盖——resolved 按 id 精确绑定，
          绝不错关闭其他并发的审批。
        """
        iid = self._interaction_key(agent_key, kind, extra.get("rpc_id"))
        rpc_id = extra.get("rpc_id")
        # A DSH rpcId is normally globally unique, but isolate malformed or
        # concurrent transports that reuse it across sessions.
        if iid in self._pending_interactions and rpc_id:
            iid = f"{iid}:{str(extra.get('session_id') or '')}"
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
        """审批结束（approval/resolved 帧带 rpcId/approvalId，或旧路径
        approval/decided 无 id）：按 id 精确关闭对应交互记录。

        并发多个审批时，带 id 的 resolved 帧只关闭自己那一条。**带 id 但
        未匹配到任何 pending 的帧是陈旧的已解决帧**——绝不能回退去关闭
        其他 pending 审批。
        兜底仅用于无任何 id 的旧路径 approval/decided（单 pending 时关闭）。"""
        payload = payload if isinstance(payload, dict) else {}
        call_id = payload.get("callId")
        if call_id:
            call_id = str(call_id)
            for iid, item in self._pending_interactions.items():
                if (item.get("kind") == "approval"
                        and str(item.get("call_id") or "") == call_id):
                    self._resolve_interaction(iid)
                    return
            return  # 带 callId 但未匹配：陈旧已解决帧，不动其他审批
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
        # 带 rpc_id 的审批由同 id 的 approval/resolved 帧精确关闭——若这里
        # 对它们兜底，DSH 回发的 A 的 decided（无 id）会把还在等待的 B 误关
        # （表现为第二个弹窗延迟 0.5~1s 后自动消失）。
        candidates = [iid for iid, item in self._pending_interactions.items()
                      if item.get("kind") == "approval" and item.get("agent_key") == agent_key
                      and not item.get("rpc_id")]
        if len(candidates) == 1:
            self._resolve_interaction(candidates[0])

    def _on_question_resolved(self, agent_key: str, payload: dict | None = None) -> None:
        """问题结束（question/resolved）：按 rpcId/callId 精确关闭对应交互记录。

        与审批同理：带 id 的 resolved 帧只关闭自己那一条；带 id 但未匹配的
        帧是陈旧已解决帧，绝不回退关闭其他 pending 问题；兜底（无 id 的旧
        路径）仅对无 rpc_id 的纯提示问题生效。"""
        payload = payload if isinstance(payload, dict) else {}
        call_id = payload.get("callId")
        if call_id:
            call_id = str(call_id)
            session_id = str(payload.get("sessionId") or "")
            for iid, item in self._pending_interactions.items():
                if (item.get("kind") == "question"
                        and str(item.get("call_id") or "") == call_id
                        and (not session_id or str(item.get("session_id") or "") == session_id)):
                    self._resolve_interaction(iid)
                    return
            return  # 带 callId 但未匹配：陈旧已解决帧，不动其他问题
        rpc_id = payload.get("rpcId")
        if rpc_id:
            session_id = str(payload.get("sessionId") or "")
            for iid, item in self._pending_interactions.items():
                if (item.get("kind") == "question" and str(item.get("rpc_id") or "") == str(rpc_id)
                        and (not session_id or str(item.get("session_id") or "") == session_id)):
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
        self._clear_model_access_alerts()
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
        """把某条 pending 阻塞交互以 sticky 气泡挂上（纯提示，无按钮）。

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
                    self.win.show_bubble(pending["text"], sticky=True)
                except TypeError:
                    self.win.show_bubble(pending["text"])
            return
        self._show_alert_compat(
            pending["text"], subtitle="", sticky=True,
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
        # 隐藏中：不切动画不出声，气泡改道灵动岛反馈面（岛反馈面可用时；
        # pause_agent_link_for_hide 让监视器隐藏期保持运行，本兜底必须感知，
        # 否则 done 气泡在隐藏期被静默吞掉）。
        hidden = not hasattr(self.win, "isVisible") or not self.win.isVisible()
        if self._last_raw.get(agent_key) in self._BUSY_STATES:
            return
        if not hidden and agent_key not in self._saw_error:
            self._emit_sound("done", agent_key)
        now = self._clock()
        if now - self._done_cooldown.get(agent_key, 0.0) < self._DONE_COOLDOWN_S:
            self._cost.abort(agent_key)
            return
        self._done_cooldown[agent_key] = now
        name = self.agent_names.get(agent_key, agent_key)
        if agent_key in self._saw_alert:
            # busy 期间出现过 attention/error：不暗示"成功完成"
            text = self._dialogue("done.attention", f"{name} 那边停了，结果怎么样要主人自己看一眼哦", agent_key=agent_key, name=name)
        else:
            text = self._dialogue("done.success", f"{name} 干完活啦，去看看成果吧～", agent_key=agent_key, name=name)
        self._saw_alert.discard(agent_key)
        if hidden:
            # 隐藏中：不切待机动画；气泡改道灵动岛反馈面（_show_link_bubble
            # 内置改道；岛反馈面不可用时丢弃）。消费统计按 abort 收口（原隐藏
            # 路径语义：防 _busy 永久滞留，下次开始干活被误判成"并发"）。
            from . import window_alerts as _window_alerts

            _window_alerts.redirect_hidden_bubble(self.win, text, duration_ms=4500)
            self._cost.abort(agent_key)
            return
        # 恢复待机动画：Claude 回合结束没有 idle 事件，不靠这步会一直停在干活动作。
        # 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）。
        # 必须走 request_link_idle（它会清 _link_anim_current 并尊重一次性动作），
        # 不能裸 _switch——否则残留的 link 状态会把以后的普通同名动作劫持进联动链。
        if not any(k != agent_key and s in self._BUSY_STATES
                   for k, s in self._last_raw.items()):
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
                from .balance import fetch_balance

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
            from . import window_alerts as _window_alerts

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
        """会话/turn 结束或 Agent 停止时，清掉对应会话/agent 的 pending 阻塞交互。"""
        if not isinstance(record, dict):
            return
        event = str(record.get("event") or "")
        session = str(record.get("sessionId") or record.get("session_id") or "")
        ended = (event in self._INTERACTION_END_EVENTS or
                 (event == "AgentStatus" and str(record.get("state") or "") in {"idle", "sleeping"}))
        if not ended:
            return
        for iid in [i for i, v in self._pending_interactions.items()
                    if v.get("agent_key") == agent_key
                    and (not session or not v.get("session_id") or v.get("session_id") == session)]:
            self._resolve_interaction(iid)

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
            self._show_model_access_alert(session_key, existing["count"])
            return
        entry = {
            "count": max(1, supplied_count),
            "_ts": now,
            "_first_ts": now,
            "_dismissed": False,
        }
        self._remember_model_access_record_fields(entry, record)
        cache[session_key] = entry
        self._show_model_access_alert(session_key, entry["count"])

    def _remember_model_access_record_fields(self, entry: dict, record: dict) -> None:
        """把限流记录的条件字段缓存进条目，供弹窗模板条件注入（缺失自动隐藏）。"""
        record = record if isinstance(record, dict) else {}
        for field in ("errorCode", "errorMessage", "consecutiveRetryCount", "retry"):
            value = record.get(field)
            if value not in (None, ""):
                entry[field] = value

    def _show_model_access_alert(self, session_key: str, count: int) -> None:
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
        text = self._dialogue(key, fallback, count=count, **conditional)
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
        """
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
        text = self._dialogue(key, fallback)
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
        """
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return
        if not isinstance(record, dict):
            return

        action = str(record.get("action") or "")
        session_key = str(record.get("sessionId") or agent_key)

        # 按 action 类型关闭对应交互弹窗
        if action == "approval_decided":
            approval_id = str(record.get("approvalId") or "")
            rpc_id = str(record.get("rpcId") or "")
            self._close_interaction_by_id("approval", rpc_id, approval_id, session_key)
        elif action == "approval_resolved":
            approval_id = str(record.get("approvalId") or "")
            rpc_id = str(record.get("rpcId") or "")
            self._close_interaction_by_id("approval", rpc_id, approval_id, session_key)
        elif action == "question_resolved":
            call_id = str(record.get("callId") or "")
            rpc_id = str(record.get("rpcId") or "")
            self._close_interaction_by_id("question", rpc_id, call_id, session_key)

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

    def _close_interaction_by_id(self, kind: str, rpc_id: str, id_: str, session_key: str) -> None:
        """按 kind + identity + session 精确关闭交互弹窗。"""
        for iid, item in self._pending_interactions.items():
            if item.get("kind") != kind:
                continue
            item_session = str(item.get("session_id") or "")
            if session_key and item_session and item_session != session_key:
                continue
            if rpc_id and (item.get("rpc_id") == rpc_id or item.get("request_id") == rpc_id):
                self._resolve_interaction(iid)
                return
            if id_ and (item.get("approval_id") == id_ or item.get("call_id") == id_):
                self._resolve_interaction(iid)
                return
        if kind not in ("approval", "question"):
            return
        for iid, item in self._pending_interactions.items():
            if item.get("kind") == kind and item.get("agent_key") == session_key and not item.get("rpc_id"):
                self._resolve_interaction(iid)
                return

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
        """
        # 本轮已硬失败（execution/failed，如 tool_failed / 模型重试耗尽）：
        # 复用既有「看过告警/出过错」簿记，让完成边沿不说「成功完成」、也不播
        # done 音效（_saw_error 抑制音效，_saw_alert 把文案换成「自己看一眼」）。
        # 放在可见性/抑制判断**之前** —— 无论这次失败提醒本身是否被吞掉
        # （窗口隐藏、或被模型访问失败提醒抑制），本轮都不是「成功完成」。
        # **不**调 _cancel_done_check：_done_pending 是按 agent 键的，取消会连
        # 别的并发会话已排的合法完成一起掐掉；而桥对 kind==="error" 的 turn/end
        # 是在**同一个 handler 内先写 execution/failed、再写 turn/end**
        # （index.js:1008-1025），正常情况下本标记一定先于 success 边沿落下。
        self._saw_alert.add(agent_key)
        self._saw_error.add(agent_key)
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
            text = self._dialogue("failure.retry", f"{name} 本轮运行失败——模型请求多次重试后仍未成功，需要检查或重新运行", name=name, **conditional)
        elif failure_type == "tool_failed":
            text = self._dialogue("failure.tool", f"{name} 本轮运行失败——工具执行最终失败，需要检查或重新运行", name=name, **conditional)
        else:
            text = self._dialogue("failure.generic", f"{name} 本轮运行失败，需要检查或重新运行", name=name, **conditional)
        if hasattr(self.win, "show_alert"):
            self.win.show_alert(text, duration_ms=self._FAIL_REMINDER_MS, sticky=False)
        elif hasattr(self.win, "show_bubble"):
            self.win.show_bubble(text, duration_ms=self._FAIL_REMINDER_MS)
