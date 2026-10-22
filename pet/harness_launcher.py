# -*- coding: utf-8 -*-
"""一键启动 DeepSeek Harness（dsh web 默认端口 38080；或桌面端应用）。

双目标（2026-10）：``launch_harness(..., target=)`` 接受 ``"auto" | "service" |
"web" | "desktop"``——auto 与 service 都是「只拉起 dsh 服务」（不开浏览器、不开
界面），web/desktop 是显式要界面的选择。auto 曾经按「检测到桌面端安装就走桌面端」
解析，那会让每次桌宠自启都把整个桌面端 GUI 拉起来；自启的本意只是拉起服务，
故（2026-10 产品口径）auto 恒等于 service，不再做任何桌面端探测。桌面端探测与
自动拉起**目前仅 Windows**：显式 desktop 在非 Windows 上报 ``unsupported``
（不 Popen 目录、也不静默改开网页）。桌面端是用户的主力 GUI 应用：由桌宠拉起
只登记不杀（退出收口只覆盖 web 服务进程）。


启动命令解析按可靠性级联（适配不同安装方式/不同 PATH 的电脑）：

1. PATH 上的 `dsh`（npm/pnpm/yarn/bun 全局安装、或用户自建软链）；
2. `node` + npm 全局包内的 `@deepseek-ai/dsh/lib/bin.js`；
3. 官方推荐的 `npx --yes @deepseek-ai/dsh web`（未安装时自动拉取，
   见 https://github.com/deepseek-ai/DeepSeek-Harness 运行文档）。

macOS：Finder 启动的 .app 环境 PATH 极简，本模块会额外探测 Homebrew、
nvm、volta、bun、pnpm 等常见安装目录后回退 npx；需要机器装有 Node.js。
Windows：Explorer / 开机自启的进程环境块是登录时的旧值，除增强 PATH 外还会
探测 nvm-windows（`%NVM_HOME%\v*`）等全局包目录。

行为：探测端口 —— 已在运行则直接打开浏览器（stdout 日志里有上次 spawn 留下的
token 链接时，预探认账优先用，浏览器会重铸 30 天持久 cookie，裸地址从此免
鉴权）；未运行则后台拉起（Windows 隐藏窗口脱离进程 / POSIX 新会话），就绪后
从 stdout 捕获 token 链接打开认证页（老版 dsh 无 token 行则回退裸地址）。
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .node_runtime import augmented_path as _augmented_path
from .node_runtime import global_node_modules_roots
from .node_runtime import static_node_modules_roots
from .node_runtime import which as _which

# 3080 会落入 Windows winnat/Hyper-V 动态保留段（EACCES），默认改用 38080；
# 与环境变量 DSH_PORT 保持一致（dsh-launcher 三件套也读它）。
DEFAULT_PORT = int(os.environ.get("DSH_PORT") or 38080)
# npx 首次拉取 @deepseek-ai/dsh 可能较慢；冷启动含插件栈初始化，实测轻载
# 40~80s、重载下更久（2026-10 实机：bind 之后 announce 还能再滞后一分钟），
# 就绪窗口放宽到 240 秒——daemon 线程到期兜底开裸地址，等再久也不会卡 UI。
_READY_TIMEOUT_SECONDS = 240.0
# 端口开始监听 ≠ token 行就位：两者虽同出 announceReady，但 bind 之后的插件
# 栈初始化在重载机器上能把 announce 拖到一分钟以后（2026-10 实机实测），
# 10 秒宽限会把本该捕获的 token 误判成「老版 dsh」提前回退裸地址。放宽到
# 60 秒：覆盖轻载全距 + 重载大部分；仍给真·老版 dsh（stdout 永远没有 token
# 行）保留兜底路径，只是多等一会再上裸地址。
_TOKEN_GRACE_SECONDS = 60.0

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



# ------------------------------------------------------------------ 桌面端
LAUNCH_TARGETS = ("auto", "service", "web", "desktop")
DESKTOP_APP_NAME = "DeepSeek Harness"
_DESKTOP_EXE_NAME = "DeepSeek Harness.exe"
# 桌面端自动拉起**本轮只支持 Windows**：macOS 的 .app 是目录（Popen 不了），
# Linux 无官方包。非 Windows 一律按「不支持」处理——自动目标（auto/service）本就
# 只起服务、与平台无关，显式 desktop 明确回报 unsupported；绝不让占位路径把默认
# 拐到 desktop。在线判定用的 19387 端口候选在 pet/dsh_state.py（那边是纯逻辑，
# 不在这里重复）。
DESKTOP_SUPPORTED = os.name == "nt"

# 注册表卸载项路径（NSIS per-user 安装写 HKCU）
_UNINSTALL_KEY = "Software" + chr(92) + "Microsoft" + chr(92) + "Windows" + chr(92) + "CurrentVersion" + chr(92) + "Uninstall"


def _desktop_default_exe() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / DESKTOP_APP_NAME / _DESKTOP_EXE_NAME


def _display_name_matches(name) -> bool:
    """卸载项 DisplayName 是否指桌面端。

    实测值带版本后缀（``DeepSeek Harness 0.2.0-rc.2``）。只比全名会让
    **自定义安装位**（默认位不存在时的唯一一路）永远探测不到；**受约束**放宽为
    「全名 + 空格 + 数字开头的版本号」，避免把 ``… Helper`` 之类认成桌面端。
    """
    n = str(name or "").strip()
    if n == DESKTOP_APP_NAME:
        return True
    if not n.startswith(DESKTOP_APP_NAME):
        return False
    suffix = n[len(DESKTOP_APP_NAME):]
    return suffix.startswith(" ") and suffix.strip()[:1].isdigit()


def _strip_icon_index(value) -> str:
    """剥掉 DisplayIcon 尾部的图标索引并去引号。

    实测值形如 ``"C:\\...\\DeepSeek Harness.exe",0``；不剥索引会拼出带 ``,0``
    的路径，``is_file()`` 永远为假。``,`` 后面不是纯数字时原样保留（文件名
    本身也可能含逗号，如 ``a,0.exe``）。
    """
    v = str(value or "").strip()
    head, sep, tail = v.rpartition(",")
    if sep and tail.strip().lstrip("-").isdigit():
        return head.strip().strip('"')
    return v.strip('"')


def _desktop_exe_from_registry() -> Path | None:
    """注册表卸载项兜底：兼容用户自定义安装位。"""
    if not DESKTOP_SUPPORTED:
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, _UNINSTALL_KEY) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        sub = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub) as sk:
                            def _val(name):
                                try:
                                    return str(winreg.QueryValueEx(sk, name)[0])
                                except OSError:
                                    return ""
                            if not _display_name_matches(_val("DisplayName")):
                                continue
                            location = _val("InstallLocation").strip()
                            if location:
                                cand = Path(location) / _DESKTOP_EXE_NAME
                                if cand.is_file():
                                    return cand
                            icon = _strip_icon_index(_val("DisplayIcon"))
                            if icon:
                                cand = Path(icon)
                                if cand.is_file() and cand.name.lower() == _DESKTOP_EXE_NAME.lower():
                                    return cand
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def desktop_install_path() -> Path | None:
    """桌面端安装路径（未安装或平台不支持返回 None）。

    非 Windows 直接 None——不返回 macOS ``.app``（那是目录，Popen 不了）；
    显式 desktop 由上层的 ``unsupported`` 兜住（自动目标 auto/service 与平台
    无关，本来就只起服务）。
    """
    if not DESKTOP_SUPPORTED:
        return None
    default = _desktop_default_exe()
    if default.is_file():
        return default
    return _desktop_exe_from_registry()


def desktop_processes() -> list[int]:
    """正在运行的桌面端进程 pid 列表（只读枚举；平台不支持/读不到返回 []）。"""
    if not DESKTOP_SUPPORTED:
        return []
    try:
        import ctypes
        from ctypes import wintypes

        arr = (wintypes.DWORD * 4096)()
        needed = wintypes.DWORD(0)
        if not ctypes.windll.psapi.EnumProcesses(
            ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed),
        ):
            return []
        pids: list[int] = []
        for pid in arr[: needed.value // 4]:
            if not pid:
                continue
            image = _pid_image_path(int(pid))
            if image and Path(image).name.lower() == _DESKTOP_EXE_NAME.lower():
                pids.append(int(pid))
        return pids
    except Exception:
        logging.debug("桌面端进程枚举失败", exc_info=True)
        return []


def desktop_process_running() -> bool:
    """桌面端进程是否在跑（端口探测全 miss 时的 online 旁路判定）。"""
    return bool(desktop_processes())


def resolve_launch_target(target: str = "auto") -> str:
    """auto → service（只拉起 dsh 服务，不开界面）；显式 service/web/desktop 直通。

    auto 曾经是「检测到桌面端安装就走桌面端」——那让每次桌宠自启都把整个桌面端
    GUI 拉起来。产品口径（2026-10）：自启只负责把 dsh 服务拉起来，web/desktop
    界面必须是用户显式选择，所以 auto 不再做任何桌面端探测（安装位/注册表/进程
    一律不看），恒等于 service。非法值照旧回落 auto（即 service）。
    """
    t = str(target or "auto").strip().lower()
    if t not in LAUNCH_TARGETS:
        t = "auto"
    if t == "auto":
        return "service"
    return t


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


def _stdout_log_path() -> Path:
    """dsh web 子进程 stdout 的落盘文件（桌宠数据目录下）。

    token 链接与启动日志都在这里；文件每次 spawn 截断重建，生命周期与
    最近一次 spawn 的实例对齐——「已有实例」分支从这同一份文件里找
    token，不另设存档文件（少一份落盘、少一套清理状态）。
    """
    return _probe_cache_path().with_name("harness-web-stdout.log")


# dsh web 就绪时向 stdout 公告带 token 的完整链接（"dsh web: http://…/?token=…"
# ——这是官方给跨进程宿主的唯一 token 通道：--no-open 模式下宿主本该自己读它
# 打开认证页。宽松匹配任意 host:port，容忍 dsh 未来调整 announce 行措辞）。
_TOKEN_URL_RE = re.compile(r"https?://[^\s'\"<>]*\?token=[^\s'\"<>]+")


def _scan_stdout_for_token(offset: int) -> tuple[int, str | None]:
    """从 stdout 日志 offset 字节处增量扫描 token 链接，返回 (新偏移, 链接|None)。

    只消费到**最后一个完整行**（按 \\n 切界）：半行留在文件里等下一轮，
    避免把写了一半的 URL 当成完整链接。禁止读到 EOF——服务常驻下 EOF
    永远不来，等 EOF 就成了「停服务才能捕获」，正是本函数要绕开的坑。
    二进制读 + 手动解码：字节偏移才能跨轮精确定位（文本模式的换行翻译
    会破坏 offset 语义）。
    """
    try:
        with open(_stdout_log_path(), "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return offset, None
    cut = data.rfind(b"\n")
    if cut < 0:
        return offset, None
    matches = list(_TOKEN_URL_RE.finditer(
        data[: cut + 1].decode("utf-8", "replace")
    ))
    # 一次写入可能包含多次 announce；优先交付最后一条，避免旧 token
    # 抢在当前实例的 token 之前被打开。
    return offset + cut + 1, matches[-1].group(0) if matches else None


def _token_url_alive(token_url: str) -> bool:
    """本地预探 token 链接是否被服务端认账（<400 即认，303/200 都算）。

    http.client 裸请求读**首个**响应的状态码：不跟随重定向（token 换
    cookie 的 Set-Cookie 是浏览器侧的事，这里只验证 token 有效性）、
    不保存任何 cookie——启动器不持有凭据，凭据的生成、铸造、过期全部
    留在 dsh 进程内。
    """
    try:
        parsed = urllib.parse.urlparse(token_url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "http" or not hostname or not parsed.netloc:
        return False
    try:
        conn = http.client.HTTPConnection(hostname, port or 80, timeout=2)
    except (OSError, TypeError, ValueError, http.client.HTTPException):
        return False
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        conn.request("GET", path)
        return conn.getresponse().status < 400
    except (OSError, TypeError, ValueError, UnicodeError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _authenticated_reuse_url(fallback_url: str) -> str:
    """「已有实例」分支的打开地址（open_browser=True 时用）。

    stdout 日志里留着最近一次 spawn 实例打印的 token 行：预掘认账就用它
    打开——浏览器会重铸 30 天持久 cookie，顺带把续期做了；不认账（实例
    被外部重启过 / token 失效 / 日志缺失）→ 回退裸地址，行为与旧版一致。
    authority 必须与候选地址一致，防止把用户带去别的端口的实例。
    每次都由用户点菜单显式触发，启动器只做递送，不做任何后台自主续期。
    """
    _, token_url = _scan_stdout_for_token(0)
    if not token_url:
        return fallback_url
    try:
        same_authority = (
            urllib.parse.urlparse(token_url).netloc
            == urllib.parse.urlparse(fallback_url).netloc
        )
    except ValueError:
        return fallback_url
    if not same_authority or not _token_url_alive(token_url):
        return fallback_url
    return token_url


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
    """候选的 npm 全局 node_modules 根目录。

    静态候选（~/.npm-global、%APPDATA%\\npm 等）不要求存在——没有就跳过；
    再加 node_runtime 探测到的各版本管理器真实目录：Windows 上 nvm-windows
    的全局包在 `%NVM_HOME%\\v*\\node_modules`，nvm 在 `~/.nvm/.../lib/node_modules`，
    只认 %APPDATA%\\npm 会漏掉整台机器的 dsh 安装。
    """
    roots: list[Path] = list(static_node_modules_roots())
    roots.extend(global_node_modules_roots())
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
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


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

# 本进程**自拉起**的 harness pid 登记（退出收口 / 联动关闭收口的唯一依据）。
# 为什么必须有：Windows 上子进程用 CREATE_NO_WINDOW 起，不带 DETACHED_PROCESS
# 也不会随父进程退出而被杀（实机确认桌宠退出后 node.exe 仍活着）——「谁拉的谁
# 收尾」只能靠这里记下的身份。存 pid 而不是 Popen 对象：收口时要用**当下**的
# 命令行复核身份（防 pid 复用），句柄回收则由 _LAUNCHED_CHILDREN 负责。
_SELF_LAUNCHED_PIDS: list[int] = []

# 自拉起 pid → 目标类型（"web" / "desktop"）。desktop 是用户的主力 GUI 应用：
# 登记只为「是谁拉的」可考，退出/联动关闭收口**绝不终止** desktop（只收 web
# 这种纯后台服务进程）——用户自己开着的应用不该被桌宠关掉。
_SELF_LAUNCHED_KIND: dict[int, str] = {}

# 登记表与子进程列表的互斥锁（G2 并发补丁）：联动关闭的收口在 daemon 线程、
# 自动拉起在 worker 线程、正常退出在主线程——三方可能并发读写这两个列表。
# 粒度纪律：锁只护**列表读写瞬间**（record/forget/reap/child_for/快照/spawn 登记），
# 慢操作（存活探测、命令行反查、taskkill、终止后等待）一律在锁外——
# 拿锁做秒级系统调用等于把 G2 治好的阻塞换个地方复发。
# 用 RLock：``_spawn`` 的两条登记需要在同一临界区完成，而
# ``_record_self_launched`` 自身也进这把锁（同线程重入不许死锁）。
_OWNERSHIP_LOCK = threading.RLock()


def _reap_children() -> None:
    with _OWNERSHIP_LOCK:
        for proc in list(_LAUNCHED_CHILDREN):
            if proc.poll() is not None:
                _LAUNCHED_CHILDREN.remove(proc)


def _record_self_launched(pid, kind: str = "web") -> None:
    """登记一个自拉起的 harness pid（非正整数一律忽略：宁缺勿滥）。"""
    if not isinstance(pid, int) or pid <= 0:
        return
    with _OWNERSHIP_LOCK:
        if pid not in _SELF_LAUNCHED_PIDS:
            _SELF_LAUNCHED_PIDS.append(pid)
        _SELF_LAUNCHED_KIND[pid] = kind


def _forget_self_launched(pid: int) -> None:
    with _OWNERSHIP_LOCK:
        try:
            _SELF_LAUNCHED_PIDS.remove(int(pid))
        except ValueError:
            pass
        _SELF_LAUNCHED_KIND.pop(int(pid), None)


def _child_for(pid: int):
    """登记表 pid 对应的自拉起 Popen 句柄（无活体句柄 = 身份不可考，见收口 docstring）。"""
    with _OWNERSHIP_LOCK:
        for proc in list(_LAUNCHED_CHILDREN):
            if getattr(proc, "pid", None) == pid:
                return proc
    return None


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

    起成功后把 pid 记进自拉起登记表：桌宠退出不会带走这个子进程，收口
    （``stop_self_launched_harness``）只认这里记下的身份。

    stdout 重定向到数据目录的落盘文件（stderr 照旧 DEVNULL）：dsh web 的
    token 链接只打印在 stdout（``announceReady`` 的跨进程公告通道，实测
    dsh 0.2.1-rc.2 带 --no-open 也照打），落盘后既不丢日志，也让「等就绪
    开页面」线程能增量读到 token 行去打开认证页。Node 对文件型 stdout 是
    同步写（实测服务运行中逐行可见），不存在「停服务才能读到」的缓冲问题。
    打开失败（数据目录不可写等）回落 DEVNULL：丢日志可接受，拉不起来不可
    接受。
    """
    log_handle = None
    try:
        log_path = _stdout_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "wb")  # 每次 spawn 截断重建
    except OSError:
        log_handle = None
    kwargs: dict = {
        "cwd": str(Path.home()),  # dsh 以调用目录为默认工作区，用家目录保持中性
        "close_fds": True,
        "stdout": log_handle if log_handle is not None else subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {**os.environ, "PATH": _augmented_path()},
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    _reap_children()
    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        if log_handle is not None:
            log_handle.close()  # 子进程已持有自己的复制，父侧立即关（Windows 上不关会占着文件）
    if isinstance(proc, _POPEN_TYPE):
        # 两条登记同进同出（R2 修复）：「pid 有登记 ⇒ 句柄有登记」的不变量
        # 是 G3 身份门槛的前提，任何替身/补丁环境下都不许拆开。
        # 且两条登记在同一临界区完成（RLock 重入），杜绝「pid 登了句柄没登」
        # 与「句柄登了 pid 没登」两个半登记窗口（ds/sol R3）。
        with _OWNERSHIP_LOCK:
            _LAUNCHED_CHILDREN.append(proc)
            _record_self_launched(getattr(proc, "pid", None))


def _spawn_desktop(exe: Path) -> None:
    """拉起桌面端 GUI 应用（不隐藏窗口、不接管输出——它是用户的主力应用）。

    登记 pid 但 kind=desktop：退出收口只读不收（见 _SELF_LAUNCHED_KIND）。
    """
    _reap_children()
    proc = subprocess.Popen(
        [str(exe)], cwd=str(exe.parent), close_fds=True,
        env={**os.environ, "PATH": _augmented_path()},
    )
    if isinstance(proc, _POPEN_TYPE):
        with _OWNERSHIP_LOCK:
            _LAUNCHED_CHILDREN.append(proc)
            _record_self_launched(getattr(proc, "pid", None), kind="desktop")


def launch_harness(port: int = DEFAULT_PORT, *, open_browser: bool = True,
                   cancel_check=None, target: str = "auto") -> tuple[str, str]:
    """启动 harness；open_browser=True 时确保浏览器被打开。

    target（解析后）决定拉起什么：

    - ``service``（含 auto）：**只拉起 dsh 服务**——走 web 启动路径，但
      ``open_browser`` 一律按 False 处理（调用方传 True 也不开浏览器、不派生
      等就绪开页面的线程）。遗留限制（与 ``open_browser=False`` 同源）：旧版
      dsh 不支持 ``--no-open`` 时它会自己开浏览器，service 目标无法阻止
      （启动前无法可靠探测）。
    - ``web``：显式要界面——``open_browser=True`` 时复用已有实例优先打开日志中的
      认证链接，失效则回退裸地址；新起实例（命令带 ``--no-open``）由桌宠捕获 token
      后开认证页面，捕获失败回退裸地址。
    - ``desktop``：显式要界面——拉起桌面端 exe（见下）。

    open_browser=False（随桌宠自启动场景）：只起服务不开浏览器——已有实例
    直接返回，新起实例不等就绪、不开页面。

    ``cancel_check``（可选）：无参可调用对象，返回 True 表示「放弃拉起」。
    在慢命令解析（``_find_launch_command``，npm 探测最长 15s）**之后**、
    ``_spawn`` **之前**评估一次——自动拉起路径的退出/会话结束标记可能在
    慢探测期间落地，没有这道闸就会在关机窗口里照样派生进程（astra 终审 P1）。
    菜单手动启动不传（用户明示动作永远可用）。

    返回 (status, url)：
    - already   已有实例在运行（配置端口或官方默认 3080）；service 目标不开浏览器，
                其余 open_browser=True 时优先打开认证链接，失效则回退裸地址
    - started   已后台启动；open_browser 且命令带 --no-open 时由桌宠增量捕获 token
                后打开认证链接，捕获失败或超时回退裸地址，否则由 dsh 自己开浏览器
    - aborted   cancel_check 命中：慢探测后放弃拉起（未 spawn）
    - not-found 未找到 dsh 命令 / 未检测到桌面端安装（显式 target="desktop"）
    - unsupported 平台不支持桌面端（非 Windows；info="desktop"）
    - error     启动异常（info 为异常信息）

    target="desktop"（必须是显式选择；auto 不再解析成 desktop）时走桌面端：桌面端
    进程已在跑直接返回 already（Electron 单实例，不重复拉起）；否则启动 exe，不开
    浏览器、不就绪等待（desktop host 自报 19387，联动读方探活自会发现）。桌面端只
    登记不杀——pet 退出/联动关闭都不会动它。非 Windows 上桌面端报 unsupported
    （不静默回落，也不去 Popen 一个目录）。
    """
    resolved = resolve_launch_target(target)
    if resolved == "desktop":
        if not DESKTOP_SUPPORTED:
            logging.warning(
                "本平台不支持自动拉起 DeepSeek Harness 桌面端（仅 Windows），未拉起")
            return "unsupported", "desktop"
        if desktop_process_running():
            return "already", "desktop"
        exe = desktop_install_path()
        if exe is None:
            logging.warning("桌面端目标未检测到安装，且显式指定 desktop，不回落 web")
            return "not-found", "desktop"
        if cancel_check is not None and cancel_check():
            return "aborted", "desktop"
        try:
            _spawn_desktop(exe)
        except OSError as exc:
            return "error", str(exc)
        logging.info("已拉起 DeepSeek Harness 桌面端：%s", exe)
        return "started", "desktop"
    if resolved == "service":
        # service（含 auto）：只拉起服务本身，强制 headless——调用方传
        # open_browser=True 也不开浏览器。置 False 后下面的等待线程分支
        # （`if not open_browser` 提前返回）就不可能到达。
        # 遗留限制：旧版 dsh 不支持 --no-open 时会自己开浏览器，service
        # 目标无法阻止（与 open_browser=False 的同一处限制，见 docstring）。
        open_browser = False
    for candidate in _candidate_ports(port):
        if is_running(candidate):
            url = f"http://127.0.0.1:{candidate}"
            if open_browser:
                webbrowser.open(_authenticated_reuse_url(url))
            return "already", url
    url = f"http://127.0.0.1:{int(port)}"
    command = _find_launch_command(port)
    if command is None:
        return "not-found", url
    if cancel_check is not None and cancel_check():
        return "aborted", url
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
        """等就绪后打开**认证页**：stdout 里出现 token 行就开认证链接（浏览器
        自动换 30 天持久 cookie，裸地址从此免鉴权）；端口已通但迟迟没有
        token 行（老版 dsh 不打）超过宽限 → 回退裸地址（旧行为）。

        扫描按字节偏移增量推进，只消费完整行（见 ``_scan_stdout_for_token``）；
        0.5s 轮询节奏与旧版一致，读本地文件的开销可忽略。
        """
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        running_since: float | None = None
        offset = 0
        while time.monotonic() < deadline:
            offset, token_url = _scan_stdout_for_token(offset)
            if token_url:
                try:
                    token_authority = urllib.parse.urlparse(token_url).netloc
                    expected_authority = urllib.parse.urlparse(url).netloc
                except ValueError:
                    token_authority = expected_authority = ""
                if token_authority == expected_authority:
                    webbrowser.open(token_url)
                    return
            if is_running(port):
                now = time.monotonic()
                if running_since is None:
                    running_since = now
                elif now - running_since >= _TOKEN_GRACE_SECONDS:
                    webbrowser.open(url)  # 老版 dsh：stdout 无 token 行，退回裸地址
                    return
            time.sleep(0.5)
        if is_running(port):
            webbrowser.open(url)  # 到期兜底：端口通了就别让用户白等一场

    threading.Thread(target=_wait_and_open, daemon=True).start()
    return "started", url


# ------------------------------------------------------------------ 生命周期
# 「停止 / 重启」的取证与终止。为什么要按端口反查进程：桌宠既可能自己拉起
# dsh（子进程脱离父进程存活，见 _spawn 的说明），也可能复用你手动跑着的实例
# ——两条路径都没有可用的句柄，唯一可靠的共同事实是「谁在监听那个端口」。
#
# dsh CLI 自身没有 stop/exit 子命令（lib/bin.js 只有 web / plugin / dump-config），
# 所以只能由宿主（桌宠）完成收尾；--no-open 让服务静默常驻之后，这是用户
# 关闭它的唯一入口——以前关掉那个可见控制台窗口就等于关服务，现在窗口不存在了。

# 命令行必须命中的特征：dsh 的包名/可执行名 + web 子命令。这是防「pid 复用
# 误杀」的身份核验（实机教训：杀前必须核对命令行/可执行路径，不能只看 pid）。
_HARNESS_CMDLINE_TOKENS = ("dsh", "web")

# 镜像提示（按端口反查路径）：持有监听 socket 的是 node（或 dsh 本体）。
_HARNESS_IMAGE_HINTS = ("node", "dsh")
# 自拉起收口专用的镜像提示（比上面多认 cmd）：Windows 上 dsh 是 npm 生成的
# `.cmd` shim，``_wrap_cmd`` 会把它包成 ``cmd.exe /c <...>\dsh.CMD web``——
# 于是**直接子进程**（我们记下 pid 的那个）镜像是 cmd.exe，真正监听端口的是它
# 的 node 子进程（本机实测 ``which("dsh")`` == %APPDATA%\npm\dsh.CMD）。
# 只对自拉起登记表放宽：那条路径另有更强的一重证据（pid 是本进程亲手 spawn 的，
# 且登记表为空时一次 terminate 都不会发）；按端口反查面对的是任意监听者，仍只认
# node/dsh 镜像，宁可不杀。
_SELF_LAUNCHED_IMAGE_HINTS = ("node", "dsh", "cmd")


@dataclass(frozen=True)
class HarnessProcess:
    """一个正在监听 dsh web 端口的进程（已通过命令行核验身份）。"""

    port: int
    pid: int
    command_line: str = ""


def _parse_windows_tcp_table(buffer: bytes, offset: int) -> list[tuple[str, int, int]]:
    """解析 GetExtendedTcpTable 结果 → [(local_addr, local_port, pid), ...]。

    MIB_TCPROW_OWNER_PID 是 6 个 DWORD（state / local addr / local port /
    remote addr / remote port / pid）。表头是 4 字节的 dwNumEntries，行数据从
    offset+4 开始；行内的 state 是第一个 DWORD，所以真正要读的
    local addr / local port / pid 落在 row_start+4/+8/+20。端口与 IPv4 地址按
    **网络字节序**存放，读出来要自己转回主机序。
    """
    if len(buffer) < 4:
        return []
    count = struct.unpack_from("<I", buffer, 0)[0]
    row_size = 24
    rows: list[tuple[str, int, int]] = []
    for index in range(count):
        row_start = offset + 4 + index * row_size
        if row_start + row_size > len(buffer):
            break
        local_addr, local_port = struct.unpack_from("<II", buffer, row_start + 4)
        pid = struct.unpack_from("<I", buffer, row_start + 20)[0]
        port = ((local_port & 0xFF) << 8) | ((local_port >> 8) & 0xFF)
        addr = ".".join(str((local_addr >> shift) & 0xFF) for shift in (0, 8, 16, 24))
        rows.append((addr, port, pid))
    return rows


def _windows_listener_pids(port: int) -> list[int]:
    """Windows 按端口反查监听进程（iphlpapi GetExtendedTcpTable，只读）。

    必须显式声明 restype/argtypes：ctypes 默认把返回值当 32 位 int、把指针参数
    当 c_int，64 位进程里会把缓冲区地址**截断成低 32 位**，函数返回非 0 而表是
    空的——本地实测就是这样拿到空列表的（探针脚本 probe-listener-pid.py 复现）。
    """
    import ctypes

    size = ctypes.c_ulong(0)
    iphlpapi = ctypes.windll.iphlpapi
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.restype = ctypes.c_uint
    get_table.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    # AF_INET=2, TCP_TABLE_OWNER_PID_LISTENER=3；先问所需缓冲区大小再取表
    get_table(None, ctypes.byref(size), False, 2, 3, 0)
    buffer = ctypes.create_string_buffer(size.value or 1)
    if get_table(buffer, ctypes.byref(size), False, 2, 3, 0) != 0:
        return []
    pids: list[int] = []
    for _addr, row_port, pid in _parse_windows_tcp_table(buffer.raw, 0):
        if row_port == int(port) and pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _parse_proc_net_tcp(text: str) -> dict[int, int]:
    """解析 /proc/net/tcp → {local_port: inode}（POSIX 反查用）。"""
    ports: dict[int, int] = {}
    for line in str(text or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            local = fields[1]
            state = fields[3]
            inode = int(fields[9])
        except (ValueError, IndexError):
            continue
        if state != "0A":  # TCP_LISTEN
            continue
        try:
            port = int(local.rsplit(":", 1)[1], 16)
        except (ValueError, IndexError):
            continue
        ports[port] = inode
    return ports


def _posix_listener_pids(port: int) -> list[int]:
    """POSIX 监听进程反查：/proc/net/tcp 拿 inode → /proc/*/fd 找属主。

    Linux 上纯 stdlib 可完成；macOS 没有 /proc，这里返回空列表（找不到 =
    回到 ``not-running`` 的安全分支，绝不猜 PID 去杀）。macOS 上的停止/重启
    因此不可用，这一点写在 README 里而不是猜一个进程。
    """
    try:
        table = Path("/proc/net/tcp").read_text(encoding="utf-8")
    except OSError:
        return []
    inode = _parse_proc_net_tcp(table).get(int(port))
    if inode is None:
        return []
    wanted = f"socket:[{inode}]"
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                if os.readlink(fd) == wanted:
                    pid = int(entry.name)
                    if pid not in pids:
                        pids.append(pid)
                    break
        except OSError:
            continue
    return pids


def listener_pids(port: int) -> list[int]:
    """监听指定 TCP 端口的进程 PID 列表（只读；查询失败返回空列表）。"""
    try:
        if os.name == "nt":
            return _windows_listener_pids(port)
        return _posix_listener_pids(port)
    except Exception:
        logging.debug("按端口反查监听进程失败 port=%s", port, exc_info=True)
        return []


def process_command_line(pid: int) -> str | None:
    """读取进程命令行（身份核验用）。读不到返回 None（不猜、不杀）。"""
    if pid <= 0:
        return None
    try:
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}')"
                    ".CommandLine",
                ],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                return None
            return (result.stdout or "").strip() or None
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(
            b"\x00", b" "
        ).decode("utf-8", "replace").strip() or None
    except Exception:
        logging.debug("读取进程命令行失败 pid=%s", pid, exc_info=True)
        return None


def _pid_image_path(pid: int) -> str | None:
    """进程可执行文件路径（第二重身份核验；读不到返回 None）。"""
    if pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                )
                return buf.value if ok else None
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return os.readlink(f"/proc/{int(pid)}/exe")
    except Exception:
        return None


def _looks_like_harness(pid: int, command_line: str | None,
                        *, image_hints: tuple[str, ...] = _HARNESS_IMAGE_HINTS) -> bool:
    """进程是否确为 dsh web（命令行两个特征都命中，且镜像是期待的宿主之一）。

    宁可放过（返回 False → 用户看到「端口被别的程序占用」）也不误杀：杀错
    进程的代价远高于多点一次启动。

    ``image_hints`` 由调用方按证据强度选择：按端口反查用默认的 node/dsh；自拉起
    收口传 ``_SELF_LAUNCHED_IMAGE_HINTS``（多认 cmd，理由见该常量）。
    """
    lowered = str(command_line or "").lower()
    if not lowered or not all(token in lowered for token in _HARNESS_CMDLINE_TOKENS):
        return False
    image = (_pid_image_path(pid) or "").lower()
    if not image:
        # 读不到镜像路径（权限/受保护进程）：命令行已带 dsh + web，按可信处理
        return True
    return any(hint in image for hint in image_hints)


def find_harness_process(port: int = DEFAULT_PORT) -> HarnessProcess | None:
    """找出本机正在运行的 dsh web（配置端口优先，其次官方默认 3080）。"""
    for candidate in _candidate_ports(port):
        if not is_running(candidate):
            continue
        for pid in listener_pids(candidate):
            command_line = process_command_line(pid)
            if _looks_like_harness(pid, command_line):
                return HarnessProcess(port=candidate, pid=pid, command_line=command_line or "")
    return None


def describe_harness_process(port: int = DEFAULT_PORT) -> HarnessProcess | None:
    """确认框/诊断用的现状描述（只读）。"""
    return find_harness_process(port)


def _terminate_process_tree(pid: int, proc=None) -> bool:
    """终止进程及其子进程树，返回终止命令是否成功（Windows taskkill /T /F；POSIX 按进程组先 TERM 后 KILL）。

    Windows 上的 .cmd shim
    会让 dsh 以「cmd → node」两层形态存在，/T 才能收干净；CREATE_NO_WINDOW
    防止 GUI 进程里凭空弹一个空白控制台窗口（实机反馈）。
    POSIX 上 dsh 常为「npx → node」两层：只 kill 顶层 pid 会留下 node 子进程，
    故按进程组终止（桌宠自拉实例 spawn 时 start_new_session=True，进程组即
    dsh 自己的组）。**仅当目标是自己进程组的组长（pgid == pid）才 killpg**：
    交互 shell 的前台作业与 start_new_session 的子进程都是组长；而脚本里
    `dsh web &` 这类后台启动的 dsh 属于脚本的组，killpg 会把整个脚本组连带
    终止——该场景回退单 pid kill（爆炸半径宁小勿大）。目标组恰为本进程组
    时同样回退单 pid kill，绝不向自己的组发信号。

    返回值的语义（G1 修复）：Windows 取 taskkill 返回码——非零且目标仍存活
    才算失败（非零但进程已不存在，如 128/查无此进程，说明它先死了，不算失败），
    调用方**不得**把失败记成已终止；POSIX 以信号是否实际送达为返回值，
    调用方的存活复核兜底（见 ``stop_self_launched_harness`` 的终止后复查）。

    ``proc``（可选）：目标的 Popen 句柄。给出时 POSIX 的 TERM 后等待用
    ``proc.poll()`` 判活——``is_running_pid`` 对未回收的僵尸返回存活，
    会让 TERM 成功的常规路径白等 3s 再补一发对僵尸无效的 SIGKILL
    （ds/sol R3）；poll 顺带完成回收，僵尸消失。且给出 ``proc`` 时**发信号前**
    先查它：句柄已死说明「我们的子进程已被终止（可能由并发收口完成）」，
    直接返回成功而**不向数字 pid 发任何信号**——数字 pid 此刻可能已被系统
    复用给别的进程，信号会打错目标（astra 终审 P1 的过期身份绑定）。
    """
    if proc is not None and proc.poll() is not None:
        return True  # 我们的子进程已被终止（并发收口先完成）：身份绑定，不再发信号
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            if not is_running_pid(pid):
                # 非零但目标已不存在（128/查无此进程）：它先死了，不算失败
                return True
            logging.warning(
                "停止 dsh：taskkill pid=%d 返回码 %s: %s%s",
                pid, result.returncode,
                (result.stdout or "").strip(), (result.stderr or "").strip(),
            )
            return False
        return True

    def _group_kill(sig) -> bool:
        try:
            pgid = os.getpgid(int(pid))
        except (OSError, ProcessLookupError):
            pgid = None
        # 只杀目标自己领导的组；本进程组与非目标领导的组都回退单 pid
        if pgid is not None and pgid == int(pid) and pgid != os.getpgrp():
            try:
                os.killpg(pgid, sig)
                return True
            except (OSError, ProcessLookupError):
                pass
        try:
            os.kill(int(pid), sig)
            return True
        except OSError:
            return False

    delivered = _group_kill(signal.SIGTERM)
    deadline = time.monotonic() + 3.0

    def _still_alive() -> bool:
        if proc is not None:
            return proc.poll() is None  # 句柄判活顺带 reap（僵尸消失）
        return is_running_pid(pid)

    while time.monotonic() < deadline and _still_alive():
        time.sleep(0.05)
    if _still_alive():
        delivered = _group_kill(signal.SIGKILL) or delivered
    if not delivered and not is_running_pid(pid):
        # 信号未送达但目标已不存在：它先死了，与 nt 的 128 豁免对称（ds R3）
        return True
    return delivered


# 收口单飞标记（终审修复）：并发收口合并为一个执行体，见
# ``stop_self_launched_harness`` docstring 的「单飞」节。
_STOP_INFLIGHT = False


def is_running_pid(pid: int) -> bool:
    """进程是否仍存活（供停止后确认用）。

    Windows 用 GetExitCodeProcess==STILL_ACTIVE：OpenProcess 能打开并不代表
    进程活着（父进程持有句柄时，已死的子进程仍可被打开）——实机教训，
    故这里以退出码判定而非"能否打开句柄"。
    """
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            return _windows_pid_alive(pid)
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def stop_harness(port: int = DEFAULT_PORT) -> tuple[str, str]:
    """停止本机运行的 dsh web；返回 (status, info)。

    status：
    - ``stopped``      已确认终止（端口不再监听，或进程已退出）；
    - ``not-running``  没有在跑的实例（含端口没监听）；
    - ``not-ours``     端口被非 dsh 进程占用 —— **不做任何终止**，把 PID 与
                       命令行原样回报给用户，避免误杀；
    - ``error``        终止过程异常。
    """
    for candidate in _candidate_ports(port):
        if not is_running(candidate):
            continue
        pids = listener_pids(candidate)
        if not pids:
            # 端口在监听但反查不到属主：不知道是谁，绝不猜 PID
            return "error", f"端口 {candidate} 正在监听，但读不到持有它的进程（权限不足？）"
        # 先把所有持有者核验完再动手：中途返回 not-ours 时不许已经杀了一半
        targets: list[int] = []
        for pid in pids:
            command_line = process_command_line(pid)
            if not _looks_like_harness(pid, command_line):
                return "not-ours", (
                    f"端口 {candidate} 由 PID {pid} 占用，命令行不是 dsh web："
                    f"{command_line or '（读不到）'}"
                )
            targets.append(pid)
        for pid in targets:
            try:
                ok = _terminate_process_tree(pid)
            except Exception as exc:
                logging.exception("停止 dsh 失败 pid=%s", pid)
                return "error", f"终止 PID {pid} 失败：{exc}"
            if not ok:
                return "error", f"终止 PID {pid} 失败（终止命令未成功，详见日志）"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and is_running(candidate):
                time.sleep(0.05)
            if is_running(candidate):
                return "error", f"已发送终止命令，但端口 {candidate} 仍在监听（PID {pid}）"
            return "stopped", f"已停止 DeepSeek Harness（PID {pid}，端口 {candidate}）。"
    return "not-running", "本机没有在运行的 DeepSeek Harness 服务。"


def stop_self_launched_harness() -> list[int]:
    """停掉**本进程自拉起**的 dsh web，返回真正终止的 pid 列表。

    与 ``stop_harness`` 的区别：后者按端口反查「谁在监听」，会终止包括用户
    自己在终端跑着的实例（所以它必须先弹确认框）；本函数是**无确认的收口**路径
    （桌宠正常退出 / DSH 联动被关掉），因此只能动自己有把握的进程——三重条件
    缺一不可，任何一条不成立就放过：

    1. pid 在自拉起登记表里（``_spawn`` 记的）——用户手动起的实例永不在表中；
    2. 进程仍存活（早已退出 / pid 复用给别的进程都不必动手）；
    3. 终止前**重新读取**当下命令行并经 ``_looks_like_harness`` 复核（dsh + web
       双特征 + 镜像是 node/dsh/cmd）。登记的是几分钟前记下的 pid，中间可能已被
       系统复用；读不到命令行（PowerShell 超时/权限）同样放过——宁可不杀。

    复核不过的 pid 保留在登记表里（下次收口再判）；已死或已终止的移出。

    身份门槛（G3 修复）：登记表只记 pid 是不够的——原子进程死亡后 pid 可能被
    系统复用给**用户自己的** dsh 实例，命令行复核会通过 → 误杀。所以收口只认
    「登记表 pid + **仍活着的自拉起 Popen 句柄**」；原进程已退出（或句柄已被
    回收）的 pid 一律只销登记不下刀——误杀用户实例的方向一次都不许发生。

    终止确认（G1 修复）：``_terminate_process_tree`` 现在返回成败；失败或
    终止后仍存活的 pid 保留登记（下次收口再试），**绝不**记成已终止。

    已知边界（R2 评审提出）：直接子进程（cmd.exe）先死而 node 子树存活时，
    登记 pid 按「原进程已退出」销记后，孤儿子树的身份不再可考——按「非我所有
    不杀」放过；端口仍被占时可走 ``stop_harness`` 的端口反查路径（有用户确认
    与命令行复核），无确认路径上绝不追杀孤儿。

    单飞（终审修复）：并发收口（联动关闭线程 / 退出主线程 / 自动拉起补偿）
    由 ``_STOP_INFLIGHT`` 合并——后到者直接返回空表。没有它，两条收口会共享
    一份过期快照：一方已终止并回收，另一方仍凭旧检查结果向**数字 pid** 发信号
    （POSIX 下 pid 可能已被复用 → 误杀，astra 终审 P1）。
    """
    global _STOP_INFLIGHT
    with _OWNERSHIP_LOCK:
        if _STOP_INFLIGHT:
            logging.info("已有收口在进行，本次合并跳过（单飞）")
            return []
        _STOP_INFLIGHT = True
    try:
        return _stop_self_launched_harness_inner()
    finally:
        with _OWNERSHIP_LOCK:
            _STOP_INFLIGHT = False


def _stop_self_launched_harness_inner() -> list[int]:
    terminated: list[int] = []
    with _OWNERSHIP_LOCK:
        registry_snapshot = list(_SELF_LAUNCHED_PIDS)
    for pid in registry_snapshot:
        _reap_children()
        child = _child_for(pid)
        if child is None or child.poll() is not None:
            logging.info(
                "自拉起登记 PID %s 的原进程已退出（pid 或已复用），仅销登记不终止",
                pid,
            )
            _forget_self_launched(pid)
            continue
        if _SELF_LAUNCHED_KIND.get(pid) == "desktop":
            # 桌面端是用户的主力应用：登记只为可考，收口绝不终止。
            # 位置在「按句柄判死」**之后**：活着的 desktop 跳过终止，但用户
            # 已经关掉的那条登记必须销掉，否则每次退出都留一条永不消费的死 pid。
            continue
        if not is_running_pid(pid):
            _forget_self_launched(pid)
            continue
        command_line = process_command_line(pid)
        if not _looks_like_harness(
            pid, command_line, image_hints=_SELF_LAUNCHED_IMAGE_HINTS,
        ):
            logging.info(
                "跳过自拉起登记中的 PID %s：命令行复核未通过（%s）",
                pid, command_line or "读不到命令行",
            )
            continue
        try:
            ok = _terminate_process_tree(pid, proc=child)
        except Exception:
            logging.exception("停止自拉起的 dsh 失败 pid=%s", pid)
            continue
        if not ok:
            logging.warning(
                "停止自拉起的 dsh：终止命令未成功 pid=%s（保留登记，下次收口再试）", pid)
            continue
        deadline = time.monotonic() + 2.0
        # 用子进程句柄确认死亡（R2 修复）：POSIX 下 ``is_running_pid`` 对未回收的
        # 僵尸返回存活（kill(pid,0) 成功），会白等 2s 再误判「没杀掉」；``poll()``
        # 顺带完成回收（僵尸消失），Windows 上同样精确且更便宜。
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        if child.poll() is None:
            logging.warning(
                "已发终止命令但 PID %s 仍存活（保留登记，下次收口再试）", pid)
            continue
        _forget_self_launched(pid)
        terminated.append(pid)
    if terminated:
        logging.info("已停止自拉起的 dsh web：PID %s", terminated)
    return terminated


def restart_harness(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> tuple[str, str]:
    """重启 dsh web：先停后起。stop 的 ``not-ours`` 会原样上报且**不启动新实例**。"""
    status, info = stop_harness(port)
    if status not in ("stopped", "not-running"):
        return status, info
    # 同 GUI 的「重启服务」：重启的是 web 服务进程，不跟随 harness_launch_target
    # （跟随会让装了桌面端的机器重启出桌面端，或直接报「桌面端已在运行」）。
    return launch_harness(port, open_browser=open_browser, target="web")


def launch_harness_gui(parent=None, action: str = "start", target: str = "auto") -> None:
    """GUI 菜单入口：探测/确认/启动/停止全部离开 GUI 线程，结果回 GUI 线程提示。

    ``target``（仅 action="start" 有意义）："auto" / "service"（只起服务，不开
    浏览器）/ "web" / "desktop"（后两者是显式要界面）；重启/停止只作用于 web
    服务进程（desktop 是用户的主力应用，桌宠绝不终止它）。

    命令解析可能同步执行 `npm root -g`（最长 15 秒）、进程反查在 Windows 上
    跑 PowerShell `Get-CimInstance`（最长 10 秒），放在 GUI 线程会卡住界面；
    确认框经 ``QTimer.singleShot`` 回 GUI 线程弹出（worker 用 Event 等答复），
    且菜单项已 close_on_trigger（回调延迟到菜单关闭后）——macOS 原生菜单跟踪
    会话中弹模态框会被 AppKit 抑制，与设置对话框首次点击无反应同源。

    ``action``：
    - ``start``   启动/复用本机实例并打开页面（旧行为，唯一不弹确认框的动作）；
    - ``restart`` 先停止再启动（带确认框，列出将终止的 PID/端口）；
    - ``stop``    只停止（带确认框）。

    停止/重启为什么必须有确认框：按端口找到的进程可能是用户自己在终端里
    跑着的 dsh，静默 kill 会打断他手上的会话——这是破坏性动作，必须先讲清楚
    「要终止谁」再动手。
    """
    from PySide6.QtCore import QObject, QTimer
    from PySide6.QtWidgets import QMessageBox

    result: dict = {}
    # 创建于 GUI 线程，作为 singleShot 的 context：保证回调回到 GUI 线程。
    # 必须经闭包链持活到 _show 投递完成（worker 结束后若 bridge 被 GC，
    # 已排队的回调会随 C++ 对象销毁被静默丢弃）。
    bridge = QObject()
    holders = [bridge]

    def _bubble(text: str, duration: int = 6000) -> None:
        show = getattr(parent, "show_bubble", None)
        if callable(show):
            show(text, duration)

    def _show() -> None:
        holders.clear()  # 已投递：解除持活，bridge 随闭包链断开回收
        status = result.get("status")
        info = result.get("info", "")
        if status in ("already", "started"):
            if info == "desktop":
                if status == "started":
                    _bubble("正在启动 DeepSeek Harness 桌面端……")
                else:
                    _bubble("DeepSeek Harness 桌面端已在运行。")
                return
            if status == "started":
                # 首次运行 npx 拉包 + dsh 自举可能要几分钟，不给反馈用户会以为没反应
                _bubble("正在后台启动 dsh web（首次运行需下载组件，可能要几分钟），就绪后会自动打开浏览器……")
            return
        if status == "stopped":
            _bubble(info or "已停止 DeepSeek Harness 服务。")
            return
        if status == "not-running":
            _bubble("本机没有在运行的 DeepSeek Harness 服务。")
            return
        if status == "not-ours":
            QMessageBox.warning(
                parent,
                "停止 DeepSeek Harness",
                "端口被一个不是 dsh 的进程占用，为避免误杀已放弃操作。\n\n" + info,
            )
            return
        if status == "unsupported":
            QMessageBox.warning(
                parent,
                "启动 DeepSeek Harness",
                "当前系统暂不支持自动拉起桌面端界面（本版本仅 Windows 支持）。\n"
                "请改用「启动 dsh web 界面」。",
            )
            return
        if status == "not-found":
            if info == "desktop":
                QMessageBox.warning(
                    parent,
                    "启动 DeepSeek Harness",
                    "未检测到 DeepSeek Harness 桌面端安装。\n"
                    "可先装桌面端，或改用「启动 dsh web 界面」。",
                )
                return
            QMessageBox.warning(
                parent,
                "启动 DeepSeek Harness",
                "未找到 dsh 命令。请先安装 Node.js 后执行：\n"
                "npm install -g @deepseek-ai/dsh\n"
                "或直接使用：npx @deepseek-ai/dsh web",
            )
        elif status == "error":
            QMessageBox.critical(parent, "DeepSeek Harness", f"操作失败：{info}")

    def worker() -> None:
        if action in ("restart", "stop"):
            # 反查（PowerShell 最长 10s）在 worker 线程跑；确认框经
            # singleShot 回 GUI 线程弹出，worker 用 Event 等答复——
            # GUI 全程不被阻塞。
            # 局部名必须避开参数 ``target``（拉起目标）：同名会让 start 分支
            # 里的 ``target=target`` 读到未绑定的局部变量（UnboundLocalError
            # 被下方 except 吞成 error，所有「启动」入口静默失败）。
            try:
                found = describe_harness_process()
            except Exception as exc:
                result["status"], result["info"] = "error", str(exc)
                QTimer.singleShot(0, bridge, _show)
                return
            confirmed: dict = {}
            proceed = threading.Event()

            def _ask() -> None:
                try:
                    confirmed["ok"] = _confirm_harness_stop(
                        parent, found, restart=(action == "restart"))
                except Exception as exc:  # 父窗口销毁/对话框构造失败
                    confirmed["error"] = exc
                finally:
                    proceed.set()  # 任何结局都必须放行 worker，否则永久挂起零反馈

            QTimer.singleShot(0, bridge, _ask)
            proceed.wait()
            if "error" in confirmed:
                result["status"] = "error"
                result["info"] = str(confirmed["error"])
                QTimer.singleShot(0, bridge, _show)
                return
            if not confirmed.get("ok"):
                return
        try:
            if action == "stop":
                status, info = stop_harness()
            elif action == "restart":
                status, info = stop_harness()
                if status in ("stopped", "not-running"):
                    # 重启的是**服务**（web 进程），与 target 无关：否则机器上装了
                    # 桌面端时，「重启服务」会去起桌面端（或在桌面端已运行时直接
                    # 报「已在运行」），服务再也起不来。
                    status, info = launch_harness(target="web")
                elif status == "not-ours":
                    pass  # 端口被别人占着：不启动第二个实例，把原因报给用户
            else:
                status, info = launch_harness(target=target)
        except Exception as exc:  # 线程内任何异常都要反馈，不能静默
            status, info = "error", str(exc)
        result["status"], result["info"] = status, info
        QTimer.singleShot(0, bridge, _show)

    threading.Thread(target=worker, daemon=True, name="pet-harness-launch").start()


def _confirm_harness_stop(parent, target: "HarnessProcess | None", *, restart: bool) -> bool:
    from PySide6.QtWidgets import QMessageBox

    verb = "重启" if restart else "停止"
    if target is None:
        body = "当前没有检测到正在运行的 DeepSeek Harness 服务。"
        if restart:
            body += "\n仍要继续吗？将继续直接启动一个新的服务。"
        else:
            body += "\n无需停止。"
    else:
        body = (
            f"将要终止 dsh web 服务：\n"
            f"  端口 {target.port}　进程 PID {target.pid}\n"
            f"  {target.command_line or '（读不到命令行，已按端口与进程核验）'}\n\n"
            "若这是你自己在终端里跑着的实例，也会一并结束。"
        )
    box = QMessageBox(parent)
    box.setWindowTitle(f"{verb} DeepSeek Harness")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(f"确认{verb} DeepSeek Harness 服务？")
    box.setInformativeText(body)
    confirm = box.addButton(f"确认{verb}", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(confirm)
    box.exec()
    return box.clickedButton() is confirm
