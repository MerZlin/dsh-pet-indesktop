# -*- coding: utf-8 -*-
"""一键启动 DeepSeek Harness（dsh web，默认端口 38080）。

启动命令解析按可靠性级联（适配不同安装方式/不同 PATH 的电脑）：

1. PATH 上的 `dsh`（npm/pnpm/yarn/bun 全局安装、或用户自建软链）；
2. `node` + npm 全局包内的 `@deepseek-ai/dsh/lib/bin.js`；
3. 官方推荐的 `npx --yes @deepseek-ai/dsh web`（未安装时自动拉取，
   见 https://github.com/deepseek-ai/DeepSeek-Harness 运行文档）。

macOS：Finder 启动的 .app 环境 PATH 极简，本模块会额外探测 Homebrew、
nvm、volta、bun、pnpm 等常见安装目录后回退 npx；需要机器装有 Node.js。
Windows：Explorer / 开机自启的进程环境块是登录时的旧值，除增强 PATH 外还会
探测 nvm-windows（`%NVM_HOME%\v*`）等全局包目录。

行为：探测端口 —— 已在运行则直接打开浏览器；未运行则后台拉起
（Windows 隐藏窗口脱离进程 / POSIX 新会话），就绪后自动打开浏览器。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import subprocess
import threading
import time
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
# npx 首次拉取 @deepseek-ai/dsh 可能较慢，预留 90 秒就绪窗口
_READY_TIMEOUT_SECONDS = 90.0

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


# ------------------------------------------------------------------ 生命周期
# 「停止 / 重启」的取证与终止。为什么要按端口反查进程：桌宠既可能自己拉起
# dsh（子进程脱离父进程存活，见 _spawn 的说明），也可能复用你手动跑着的实例
# ——两条路径都没有可用的句柄，唯一可靠的共同事实是「谁在监听那个端口」。
#
# dsh CLI 自身没有 stop/exit 子命令（lib/bin.js 只有 web / plugin / dump-config），
# 所以只能由宿主（桌宠）完成收尾；--no-open 让服务静默常驻之后，这是用户
# 关闭它的唯一入口——以前关掉那个可见控制台窗口就等于关服务，现在窗口不存在了。

# 命令行必须命中的特征：dsh 的包名/可执行名 + web 子命令。这是防「pid 复用
# 误杀」的身份核验（实机教训见 child_pet_cleanup 里对 pid 复用的核验注释）。
_HARNESS_CMDLINE_TOKENS = ("dsh", "web")


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


def _looks_like_harness(pid: int, command_line: str | None) -> bool:
    """进程是否确为 dsh web（命令行两个特征都命中，且镜像是 node/dsh 一类）。

    宁可放过（返回 False → 用户看到「端口被别的程序占用」）也不误杀：杀错
    进程的代价远高于多点一次启动。
    """
    lowered = str(command_line or "").lower()
    if not lowered or not all(token in lowered for token in _HARNESS_CMDLINE_TOKENS):
        return False
    image = (_pid_image_path(pid) or "").lower()
    if not image:
        # 读不到镜像路径（权限/受保护进程）：命令行已带 dsh + web，按可信处理
        return True
    return any(hint in image for hint in ("node", "dsh"))


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


def _terminate_process_tree(pid: int) -> None:
    """终止进程及其子进程树（Windows taskkill /T /F；POSIX 先 TERM 后 KILL）。

    与 child_pet_cleanup._terminate_pet_process 同款：Windows 上的 .cmd shim
    会让 dsh 以「cmd → node」两层形态存在，/T 才能收干净；CREATE_NO_WINDOW
    防止 GUI 进程里凭空弹一个空白控制台窗口（实机反馈）。
    """
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            logging.warning(
                "停止 dsh：taskkill pid=%d 返回码 %s: %s%s",
                pid, result.returncode,
                (result.stdout or "").strip(), (result.stderr or "").strip(),
            )
        return
    import signal

    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and is_running_pid(pid):
        time.sleep(0.05)
    if is_running_pid(pid):
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def is_running_pid(pid: int) -> bool:
    """进程是否仍存活（供停止后确认用）。

    Windows 用 GetExitCodeProcess==STILL_ACTIVE：OpenProcess 能打开并不代表
    进程活着（父进程持有句柄时，已死的子进程仍可被打开）——这条实机教训写在
    child_pet_cleanup._pid_alive 的注释里，这里沿用同一判定。
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
                _terminate_process_tree(pid)
            except Exception as exc:
                logging.exception("停止 dsh 失败 pid=%s", pid)
                return "error", f"终止 PID {pid} 失败：{exc}"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and is_running(candidate):
                time.sleep(0.05)
            if is_running(candidate):
                return "error", f"已发送终止命令，但端口 {candidate} 仍在监听（PID {pid}）"
            return "stopped", f"已停止 DeepSeek Harness（PID {pid}，端口 {candidate}）。"
    return "not-running", "本机没有在运行的 DeepSeek Harness 服务。"


def restart_harness(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> tuple[str, str]:
    """重启 dsh web：先停后起。stop 的 ``not-ours`` 会原样上报且**不启动新实例**。"""
    status, info = stop_harness(port)
    if status not in ("stopped", "not-running"):
        return status, info
    return launch_harness(port, open_browser=open_browser)


def launch_harness_gui(parent=None, action: str = "start") -> None:
    """GUI 菜单入口：探测/确认/启动/停止全部离开 GUI 线程，结果回 GUI 线程提示。

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
        if status == "not-found":
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
            try:
                target = describe_harness_process()
            except Exception as exc:
                result["status"], result["info"] = "error", str(exc)
                QTimer.singleShot(0, bridge, _show)
                return
            confirmed: dict = {}
            proceed = threading.Event()

            def _ask() -> None:
                try:
                    confirmed["ok"] = _confirm_harness_stop(
                        parent, target, restart=(action == "restart"))
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
                    status, info = launch_harness()
                elif status == "not-ours":
                    pass  # 端口被别人占着：不启动第二个实例，把原因报给用户
            else:
                status, info = launch_harness()
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
