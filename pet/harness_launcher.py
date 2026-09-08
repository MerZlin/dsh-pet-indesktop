# -*- coding: utf-8 -*-
"""一键启动 DeepSeek Harness（dsh web，默认端口 38080）。

启动命令解析按可靠性级联（适配不同安装方式/不同 PATH 的电脑）：

1. PATH 上的 `dsh`（npm/pnpm/yarn/bun 全局安装、或用户自建软链）；
2. `node` + npm 全局包内的 `@deepseek-ai/dsh/lib/bin.js`；
3. 官方推荐的 `npx --yes @deepseek-ai/dsh web`（未安装时自动拉取，
   见 https://github.com/deepseek-ai/DeepSeek-Harness 运行文档）。

macOS：Finder 启动的 .app 环境 PATH 极简，本模块会额外探测 Homebrew、
nvm、volta、bun、pnpm 等常见安装目录后回退 npx；需要机器装有 Node.js。

行为：探测端口 —— 已在运行则直接打开浏览器；未运行则后台拉起
（Windows 隐藏窗口脱离进程 / POSIX 新会话），就绪后自动打开浏览器。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from .node_runtime import augmented_path as _augmented_path
from .node_runtime import which as _which

# 3080 会落入 Windows winnat/Hyper-V 动态保留段（EACCES），默认改用 38080；
# 与环境变量 DSH_PORT 保持一致（dsh-launcher 三件套也读它）。
DEFAULT_PORT = int(os.environ.get("DSH_PORT") or 38080)
# npx 首次拉取 @deepseek-ai/dsh 可能较慢，预留 90 秒就绪窗口
_READY_TIMEOUT_SECONDS = 90.0

_POSIX_NODE_MODULES = (
    "~/.local/lib/node_modules",
    "~/.npm-global/lib/node_modules",
    "/usr/local/lib/node_modules",
    "/opt/homebrew/lib/node_modules",
    "/usr/lib/node_modules",
)


def is_running(port: int = DEFAULT_PORT) -> bool:
    """探测 127.0.0.1:port 是否有服务监听。"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _candidate_ports(port: int = DEFAULT_PORT) -> list[int]:
    """复用已有实例的候选端口：配置端口优先，其次官方默认 3080。

    用户可能已自行跑着一个 dsh web（比如官方默认 3080——3080 只是
    Windows 上不宜**绑定**，作为客户端去连接没有问题）。先复用再新起，
    避免用户机器上同时跑两个 dsh web 互相不认识。"""
    ports: list[int] = []
    for p in (int(port), 3080):
        if p not in ports:
            ports.append(p)
    return ports


def _wrap_cmd(command: list[str]) -> list[str]:
    """Windows 上 .cmd/.bat shim 必须经 cmd 启动。

    Popen 本身就是异步的，不需要 start /b 让 cmd 立即返回；相反
    start /b + DETACHED_PROCESS 的组合会让控制台窗口可见地弹出
    （实测复现：dsh 日志打进一个可见 cmd 窗口，用户关掉窗口即杀掉
    整棵进程树）。"""
    if os.name == "nt" and command[0].lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", *command]
    return command


# 按基础命令缓存 `web --help` 是否包含 --no-open，避免每次点击菜单都探测
_NO_OPEN_CACHE: dict[tuple[str, ...], bool] = {}

# Windows 探测子进程必须隐藏窗口：桌宠是无控制台的 GUI 进程，console 类子进程
# （cmd/node）不隐藏就会弹出可见终端窗口（开机自启场景实测复现：空终端窗口
# 挂十几秒后消失）。
_HIDDEN_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


def _probe_cache_path() -> Path:
    """--no-open 探测结果的落盘缓存路径（桌宠数据目录下）。"""
    try:
        from . import config as _config_mod
        app_dir = str(getattr(_config_mod, "APP_DIR_NAME", "dsh-pet-standalone"))
    except Exception:
        app_dir = "dsh-pet-standalone"
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / app_dir / "harness_probe_cache.json"


def _dsh_version(base_command: list[str]) -> str | None:
    """快速取 dsh 版本（--version 不加载插件栈，秒回；失败返回 None）。"""
    try:
        result = subprocess.run(
            [*base_command, "--version"],
            capture_output=True, text=True, timeout=8,
            cwd=str(Path.home()),
            env={**os.environ, "PATH": _augmented_path()},
            **_HIDDEN_KWARGS,
        )
        return (result.stdout or "").strip() or (result.stderr or "").strip() or None
    except Exception:
        return None


def _no_open_disk_cache_read(base_command: list[str], *, allow_stale: bool = False) -> bool | None:
    """读落盘缓存：默认仅当缓存的命令行与当前 dsh 版本都匹配才命中；
    allow_stale=True 时跳过版本校验（仅作探测失败时的兜底）；否则 None。"""
    try:
        cache = json.loads(_probe_cache_path().read_text(encoding="utf-8"))
        if cache.get("cmd") != [str(part) for part in base_command]:
            return None
        if not allow_stale:
            version = _dsh_version(base_command)
            if version is None or version != cache.get("version"):
                return None
        return bool(cache.get("no_open"))
    except Exception:
        return None


def _no_open_disk_cache_write(base_command: list[str], supported: bool) -> None:
    """慢探测成功后写落盘缓存（失败探测不写，避免把超时误判固化）。"""
    try:
        version = _dsh_version(base_command)
        if version is None:
            return
        path = _probe_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "cmd": [str(part) for part in base_command],
            "version": version,
            "no_open": bool(supported),
        }), encoding="utf-8")
    except Exception:
        pass


def _probe_no_open(base_command: list[str]) -> tuple[bool, bool]:
    """慢探测 `web --help`：返回 (supported, probe_ok)。

    超时/异常时 probe_ok=False——这是「不知道」，不是「不支持」，不得写缓存。
    """
    try:
        result = subprocess.run(
            [*base_command, "web", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path.home()),
            env={**os.environ, "PATH": _augmented_path()},
            **_HIDDEN_KWARGS,
        )
        supported = "--no-open" in (result.stdout or "") or "--no-open" in (result.stderr or "")
        return supported, True
    except Exception:
        return False, False


def _supports_no_open(base_command: list[str]) -> bool:
    """探测 `web --help` 是否支持 --no-open。

    三级缓存：进程内 dict → 落盘缓存（按 `dsh --version` 匹配——慢探测要初始
    化插件栈，热机 ~9s，开机高负载 30s 也会超时；缓存使命中后零探测）→
    慢探测。旧版 dsh（如 0.1.0-rc.3）没有该选项，强行传参会启动失败；探测
    失败/超时默认 False，宁可少传参数也不能让启动命令报 unknown option。
    """
    key = tuple(str(part) for part in base_command)
    if key in _NO_OPEN_CACHE:
        return _NO_OPEN_CACHE[key]
    supported = _no_open_disk_cache_read(base_command)
    if supported is None:
        supported, probe_ok = _probe_no_open(base_command)
        if probe_ok:
            _no_open_disk_cache_write(base_command, supported)
        else:
            # 慢探测失败（开机高负载超时）：用旧版本缓存兜底——命令行匹配说明
            # 是同一个 dsh 安装，dsh 跨版本移除 CLI 参数的概率远低于探测超时
            stale = _no_open_disk_cache_read(base_command, allow_stale=True)
            if stale is not None:
                supported = stale
    _NO_OPEN_CACHE[key] = supported
    return supported


def _npm_global_roots() -> list[Path]:
    """候选的 npm 全局 node_modules 根目录。"""
    roots: list[Path] = []
    if os.name == "nt":
        roots.append(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules")
    else:
        roots.extend(Path(directory).expanduser() for directory in _POSIX_NODE_MODULES)
    # 只在 PATH（增强后）确实存在 npm 时才探测，避免菜单里点击卡住 15 秒。
    # 使用绝对路径并传入增强环境，Finder 启动时 npm 的 env-node shebang
    # 才能继续找到 Homebrew Node（Issue #67）。
    npm = _which("npm")
    if npm is not None:
        try:
            result = subprocess.run(
                [npm, "root", "-g"], capture_output=True, text=True, timeout=15,
                env={**os.environ, "PATH": _augmented_path()},
                **_HIDDEN_KWARGS,
            )
            if result.returncode == 0 and result.stdout.strip():
                roots.append(Path(result.stdout.strip()))
        except Exception:
            pass
    return roots


def _find_launch_command(port: int = DEFAULT_PORT) -> list[str] | None:
    """级联解析 dsh 启动命令；找不到返回 None。

    尾部参数先固定 web/host/port；只有探测到 `web --help` 支持
    `--no-open` 时才追加该选项，否则不传（由 dsh 自己开浏览器）。
    """
    port = int(port)
    tail = ["web", "--host", "127.0.0.1", "--port", str(port)]

    def _finish(base_command: list[str]) -> list[str]:
        if _supports_no_open(base_command):
            tail.append("--no-open")
        return _wrap_cmd([*base_command, *tail])

    # 1) PATH 上的 dsh（各包管理器全局安装）
    dsh = _which("dsh")
    if dsh:
        return _finish([dsh])

    # 2) node + npm 全局包内的 bin.js
    node = _which("node")
    for root in _npm_global_roots():
        bin_js = root / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        if bin_js.is_file():
            if node:
                return _finish([node, str(bin_js)])
            # POSIX：bin.js 有 shebang 可直跑；Windows 上必须经 node
            if os.name != "nt":
                return _finish([str(bin_js)])

    # 3) 官方推荐：npx --yes @deepseek-ai/dsh web（首次会自动拉取）
    npx = _which("npx")
    if npx:
        return _finish([npx, "--yes", "@deepseek-ai/dsh"])
    if node:
        npx_side = Path(node).with_name("npx")  # npx 随 Node 一起分发
        if npx_side.is_file():
            return _finish([str(npx_side), "--yes", "@deepseek-ai/dsh"])
    return None


# 模块加载时捕获真实 Popen 类型（测试会整体替换 subprocess.Popen，
# 登记判断须用真实类型；fake 返回的对象不入登记表）。
_POPEN_TYPE = subprocess.Popen

# 已启动的子进程句柄登记：poll() 回收已退出进程（POSIX 防僵尸，
# Windows 防句柄泄漏），不持有引用则子进程退出后无人 waitpid。
_LAUNCHED_CHILDREN: list[subprocess.Popen] = []


def _reap_children() -> None:
    for proc in list(_LAUNCHED_CHILDREN):
        if proc.poll() is not None:
            _LAUNCHED_CHILDREN.remove(proc)


def _spawn(command: list[str]) -> None:
    """后台拉起进程：Windows 隐藏控制台窗口；POSIX 新会话脱离终端。

    Windows 只用 CREATE_NO_WINDOW（隐藏窗口但保留隐藏控制台，子进程的
    npm/node 输出有处可去且不可见）——不要再叠加 DETACHED_PROCESS：
    两者组合的语义冲突实测会弹出可见 cmd 窗口，用户关窗即杀整树。
    隐藏控制台的子进程不随父进程退出而被杀，无需 DETACHED 脱离。

    macOS 上 Finder 启动的 .app 环境 PATH 极简：dsh/npx 是带 shebang
    （/usr/bin/env node）的 shell 脚本，执行时用的是**子进程环境**的 PATH，
    而非 _which 用的增强 PATH——不注入增强 PATH 会静默失败
    （env: node: No such file or directory，45 秒后无反应）。
    """
    kwargs: dict = {
        "cwd": str(Path.home()),  # dsh 以调用目录为默认工作区，用家目录保持中性
        "close_fds": True,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {**os.environ, "PATH": _augmented_path()},
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    _reap_children()
    proc = subprocess.Popen(command, **kwargs)
    if isinstance(proc, _POPEN_TYPE):
        _LAUNCHED_CHILDREN.append(proc)


def launch_harness(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> tuple[str, str]:
    """启动 harness；open_browser=True 时确保浏览器被打开。

    open_browser=False（随桌宠自启动场景）：只起服务不开浏览器——已有实例
    直接返回，新起实例不等就绪、不开页面。注意旧版 dsh 不支持 --no-open
    时会自己开浏览器，此参数无法阻止（启动前无法可靠探测）。

    返回 (status, url)：
    - already   已有实例在运行（配置端口或官方默认 3080）；open_browser 时已打开浏览器
    - started   已后台启动；open_browser 且命令带 --no-open 时由桌宠等待就绪后
                打开浏览器，否则由 dsh 自己开浏览器（桌宠不重复打开）
    - not-found 未找到 dsh 命令
    - error     启动异常（info 为异常信息）
    """
    for candidate in _candidate_ports(port):
        if is_running(candidate):
            url = f"http://127.0.0.1:{candidate}"
            if open_browser:
                webbrowser.open(url)
            return "already", url
    url = f"http://127.0.0.1:{int(port)}"
    command = _find_launch_command(port)
    if command is None:
        return "not-found", url
    try:
        _spawn(command)
    except OSError as exc:
        return "error", str(exc)

    if "--no-open" not in command:
        # 未传 --no-open：dsh 会自己打开浏览器，桌宠不再重复打开
        return "started", url

    if not open_browser:
        return "started", url  # 只起服务：不等就绪、不开页面

    def _wait_and_open() -> None:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if is_running(port):
                webbrowser.open(url)
                return
            time.sleep(0.5)

    threading.Thread(target=_wait_and_open, daemon=True).start()
    return "started", url


def launch_harness_gui(parent=None) -> None:
    """GUI 菜单入口：探测/启动放到后台线程，失败时在 GUI 线程弹窗提示。

    命令解析可能同步执行 `npm root -g`（最长 15 秒），放在 GUI 线程会
    卡住界面；弹窗延迟到菜单关闭后再显示（macOS 原生菜单跟踪会话中
    弹模态框会被 AppKit 抑制，与设置对话框首次点击无反应同源）。
    """
    from PySide6.QtCore import QObject, QTimer
    from PySide6.QtWidgets import QMessageBox

    result: dict = {}
    # 创建于 GUI 线程，作为 singleShot 的 context：保证回调回到 GUI 线程
    bridge = QObject()

    def _show() -> None:
        status = result.get("status")
        info = result.get("info", "")
        if status in ("already", "started"):
            if status == "started":
                # 首次运行 npx 拉包 + dsh 自举可能要几分钟，不给反馈用户会以为没反应
                bubble = getattr(parent, "show_bubble", None)
                if callable(bubble):
                    bubble("正在后台启动 dsh web（首次运行需下载组件，可能要几分钟），就绪后会自动打开浏览器……", 6000)
            return
        if status == "not-found":
            QMessageBox.warning(
                parent,
                "启动 DeepSeek Harness",
                "未找到 dsh 命令。请先安装 Node.js 后执行：\n"
                "npm install -g @deepseek-ai/dsh\n"
                "或直接使用：npx @deepseek-ai/dsh web",
            )
        elif status == "error":
            QMessageBox.critical(parent, "启动 DeepSeek Harness", f"启动失败：{info}")

    def worker() -> None:
        try:
            status, info = launch_harness()
        except Exception as exc:  # 线程内任何异常都要反馈，不能静默
            status, info = "error", str(exc)
        result["status"], result["info"] = status, info
        QTimer.singleShot(0, bridge, _show)

    threading.Thread(target=worker, daemon=True, name="pet-harness-launch").start()
