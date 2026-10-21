# -*- coding: utf-8 -*-
"""定位本机音乐播放器的可执行文件（网易云 / QQ音乐 / 酷狗 / 汽水音乐）。

右键菜单的「打开某播放器并播放」需要知道播放器装在哪。支持的播放器由
:data:`PLAYERS` 注册表一处定义。策略是**先自动搜常见路径，再允许用户手动覆盖**：

1. 配置里的手动路径（``music_player_paths``）优先——config.json 里指定过就用它；
2. 否则按候选目录名搜几个常见盘符（本机实测网易云在 ``D:\\CloudMusic``、
   QQ音乐在 ``D:\\QQ音乐\\QQMusic``、酷狗在 ``\\KuGou\\KGMusic``，都不是默认的
   Program Files）；
3. 都找不到就返回 ``None``；缓存暖了之后菜单项据此置灰并说明原因（缓存冷时
   菜单先乐观启用，扫描在后台线程里补）。

搜索结果进程内缓存：目录扫描有几十毫秒开销，而右键菜单每次打开都要问一遍。
因此缓存查询拆成两条路径：**GUI 线程（右键菜单）只读缓存**（``cached_player``，
绝不碰文件系统），真正的扫描留给后台线程与点击路径（``find_player``）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 已知播放器：key -> (显示名, 可执行文件名, 可能的父目录名)
# **唯一注册表**：右键菜单、设置页与 config 的路径清洗都从这里取键
# （:func:`player_keys`），新增播放器只改这一处，谁都不许另抄一份键表。检测只认
# 这里的 exe 文件名 + 父目录名（外加 _VERSIONED_INSTALL_DIRS 里登记的已知版本化
# 布局），不做全盘递归、不查网络。
PLAYERS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "netease": ("网易云音乐", "cloudmusic.exe", ("CloudMusic", "网易云音乐", "Netease")),
    "qqmusic": ("QQ音乐", "QQMusic.exe", ("QQMusic", "QQ音乐")),
    # 酷狗：真实安装常见 ``...\KuGou\KGMusic\KuGou.exe``（比其它播放器多一层，
    # 靠浅扫兜住），也有把安装目录直接叫「酷狗音乐」的。
    "kugou": ("酷狗音乐", "KuGou.exe", ("KuGou", "KGMusic", "酷狗音乐")),
    # 汽水音乐：Electron 应用，安装目录带空格（``Programs\Soda Music``），程序本体
    # 在**版本号子目录**里——本机实测 ``...\Programs\Soda Music\3.7.0\SodaMusic.exe``
    # （版本段随升级变，见 _VERSIONED_INSTALL_DIRS）。目录名也有写成 SodaMusic /
    # 汽水音乐的，一并认。
    "soda": ("汽水音乐", "SodaMusic.exe", ("SodaMusic", "Soda Music", "汽水音乐")),
}

# 搜哪些根目录：两个常见盘符 + 用户的 AppData（部分安装器会装在这里）。
_SEARCH_ROOTS = ("D:/", "C:/", "C:/Program Files", "C:/Program Files (x86)",
                 "C:/Users/%s/AppData/Local" % os.environ.get("USERNAME", ""),
                 "C:/Users/%s/AppData/Roaming" % os.environ.get("USERNAME", ""))

# 每个候选目录最多往下找几层（避免全盘递归）。
_MAX_DEPTH = 3

# 通用「根目录 + 候选目录名 + exe」表达不了的已知安装布局：root 下的安装相对路径。
# 这类安装器（汽水）把程序放进版本号子目录，版本段会随升级变，所以对**这些已知
# 安装目录**多看一层子目录，而不是把版本号写死或放宽全局递归深度。
_VERSIONED_INSTALL_DIRS = {
    "soda": ("Programs/Soda Music", "Programs/SodaMusic", "Soda Music"),
}

# 安装器把「当前在用的是哪个版本目录」写在自己的安装描述文件里（登记的是相对安装目录的
# 文件名）：汽水实测 ``{"app_name": "SodaMusic", "cur_path": "3.7.0"}``。这只是安装布局
# 描述——不含账号、令牌或任何用户数据——读它只为让"安装器声明的那一份"优先于"枚举到的
# 第一份"（升级后旧版本目录不会被删，枚举顺序不是承诺）。没有登记就退回版本排序。
_LAUNCHER_CUR_PATH_FILES = {"soda": "launcher_config.json"}

# 版本目录枚举条目数与安装描述文件大小的上界：安装目录里本来只有版本目录和几个安装器
# 文件，超出的条目不再看（有界优先于猜中；读一份异常大的文件也要有上限）。
_VERSIONED_MAX_ENTRIES = 64
_LAUNCHER_CONFIG_MAX_BYTES = 64 * 1024
# cur_path 与版本目录名的长度上界：版本目录名不该更长，超了就当坏描述拒绝。
_CUR_PATH_MAX_CHARS = 256
_VERSION_NAME_MAX_CHARS = 32

# 进程内缓存：player_key -> 路径或 None（None 也要缓存，否则每次都白扫一遍）。
_cache: dict[str, str | None] = {}

# cached_player 的三态标记（见该函数 docstring）。
CACHED_FOUND = "found"
CACHED_MISSING = "not-found"
CACHED_COLD = "cold"

# 后台预热去重：同一 key 同时只允许一个扫描线程在飞（幂等）。
_warm_inflight: set[str] = set()
_warm_lock = threading.Lock()

# 全局扫描串行化（事故 2026-09-19，PR #147 CI 取证）：netease/qqmusic 各自的
# 预热线程若并发扫同一批盘根（C:/、D:/ 三层浅扫），Windows 上与 GC 交叠触发
# C 级 access violation（faulthandler dump：一线程 Garbage-collecting、一线程
# _shallow_scan，exit -1073741819）。扫描只发生在后台线程（GUI 路径只读缓存），
# 串行化的代价是第二条 key 多等几秒，可接受。
_search_lock = threading.Lock()


def player_keys() -> tuple[str, ...]:
    """已注册播放器的键（注册表顺序）——消费方取键一律走这里，别另抄固定键表。"""
    return tuple(PLAYERS)


def player_label(player_key: str) -> str:
    return PLAYERS.get(player_key, (player_key, "", ()))[0]


def find_player(player_key: str, manual_path: str = "") -> str | None:
    """返回播放器可执行文件的绝对路径；找不到返回 None。

    ``manual_path`` 是配置里手填的路径（``music_player_paths``）：非空且指向真实文件时优先采用。

    **可能扫描目录**（缓存冷时最多几秒）：不要在 GUI 线程调用。右键菜单用
    :func:`cached_player`（只读缓存），点击/启动预热路径用本函数。
    """
    if player_key not in PLAYERS:
        return None
    manual = str(manual_path or "").strip()
    if manual:
        expanded = Path(manual).expanduser()
        if expanded.is_file():
            return str(expanded)
        # 手填了但文件不存在：不静默忽略，也别退回自动搜——
        # 否则用户会以为"我填的路径生效了"，实际用的是搜到的另一个。
        log.debug("音乐播放器路径无效：%s", manual)
        return None
    if player_key in _cache:
        return _cache[player_key]
    found = _search(player_key)
    _cache[player_key] = found
    return found


def cached_player(player_key: str, manual_path: str = "") -> tuple[str, str | None]:
    """**只读缓存**的三态查询——绝不扫描文件系统，专供 GUI 线程（右键菜单）用。

    返回 ``(state, path)``，``state`` 取值：

    - :data:`CACHED_FOUND`：确有该播放器，``path`` 是可执行文件的绝对路径；
    - :data:`CACHED_MISSING`：确定没有——负缓存命中（此前扫过），或手填路径失效；
    - :data:`CACHED_COLD`：缓存冷（从未扫过），``path`` 为 ``None``。调用方应按
      "乐观可用"处理，并触发一次 :func:`warm_cache_async` 把扫描挪去后台。

    手填路径是一次 ``is_file()``（单次 stat，不是目录扫描）：非空时以它为准，
    命中 found、失效 not-found，与 :func:`find_player` 同语义（不静默回退自动
    搜索），也不写缓存。

    需要真结果（点击、启动预热）时用 :func:`find_player`——它会扫描。
    """
    if player_key not in PLAYERS:
        return CACHED_MISSING, None
    manual = str(manual_path or "").strip()
    if manual:
        expanded = Path(manual).expanduser()
        if expanded.is_file():
            return CACHED_FOUND, str(expanded)
        return CACHED_MISSING, None
    if player_key in _cache:
        found = _cache[player_key]
        return (CACHED_FOUND, found) if found else (CACHED_MISSING, None)
    return CACHED_COLD, None


def warm_cache_async(player_key: str, manual_path: str = "") -> bool:
    """幂等触发一次后台扫描，把 ``player_key`` 的结果（含 None 负结果）写进缓存。

    返回是否**真的**起了新线程；以下情况返回 False（幂等）：未知播放器、已有
    缓存、同 key 扫描已在飞、手填了手动路径（只需一次 stat，无可暖的扫描）。

    线程是 daemon，异常只记日志：预热失败不影响调用方——下次菜单打开仍是 cold
    （再试一次），或走点击路径的即时解析。
    """
    if player_key not in PLAYERS:
        return False
    if str(manual_path or "").strip():
        return False  # 手填路径无需目录扫描
    with _warm_lock:
        if player_key in _cache or player_key in _warm_inflight:
            return False
        _warm_inflight.add(player_key)

    def _run() -> None:
        try:
            find_player(player_key)
        except Exception:
            log.debug("播放器路径预热失败：%s", player_key, exc_info=True)
        finally:
            with _warm_lock:
                _warm_inflight.discard(player_key)

    threading.Thread(
        target=_run, name=f"music-player-warm-{player_key}", daemon=True
    ).start()
    return True


def _search(player_key: str) -> str | None:
    """缓存未命中后的真实查找（测试常打桩的 seam）。**全局串行化**，见 _search_lock。"""
    with _search_lock:
        return _search_filesystem(player_key)


def _search_filesystem(player_key: str) -> str | None:
    if sys.platform != "win32":
        return None
    _, exe_name, dir_names = PLAYERS[player_key]
    for root in _SEARCH_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for dir_name in dir_names:
            direct = root_path / dir_name / exe_name
            if direct.is_file():
                return str(direct)
        # 已知的版本化安装布局（汽水）：只在这些写死的安装目录里多看一层子目录
        # （每个 root 几次 stat，目录在就再 iterdir 一次，本机实测 6 个 root
        # 合计 2.4ms），不是深挖、也不放宽全局递归深度。
        hit = _search_versioned_install(root_path, exe_name, player_key)
        if hit:
            return hit
        # 目录名对不上时，浅层扫一层子目录找同名 exe。
        hit = _shallow_scan(root_path, exe_name)
        if hit:
            return hit
    return None


def _search_versioned_install(root: Path, exe_name: str, player_key: str) -> str | None:
    """在已知的版本化安装目录里找 exe（``<安装目录>/<版本段>/<exe>``）。

    ``_VERSIONED_INSTALL_DIRS`` 只登记**已知**安装布局：目录不存在就立刻放弃，
    不做模糊匹配、不递归（版本段只多一层）。

    选哪一份按**确定性**顺序，绝不赌 ``iterdir`` 的枚举顺序（升级后旧版本目录不会被
    删，枚举先给谁不是承诺——原实现取首个命中，于是可能启动旧版）：

    1. 安装目录下的 ``<exe>``（不版本化的布局）；
    2. 安装器安装描述里的 ``cur_path``——它明说当前在用的是哪个版本目录（见
       :data:`_LAUNCHER_CUR_PATH_FILES`），只接受安装目录内的相对目录且目标真有 exe；
    3. 都不行（描述缺失/损坏/越界）再按**数字版本降序**挑（``3.10`` 排在 ``3.7``
       前面——按字符串比会反过来），非版本名的目录排在后面。

    枚举条目数与安装描述文件都有上界（:data:`_VERSIONED_MAX_ENTRIES` /
    :data:`_LAUNCHER_CONFIG_MAX_BYTES`），不扩全盘扫。
    """
    for relative in _VERSIONED_INSTALL_DIRS.get(player_key, ()):
        install_dir = root / relative
        if not install_dir.is_dir():
            continue
        direct = install_dir / exe_name
        if direct.is_file():
            return str(direct)
        current = _launcher_current_install(
            install_dir, exe_name, _LAUNCHER_CUR_PATH_FILES.get(player_key, ""))
        if current:
            return current
        newest = _newest_versioned_child(install_dir, exe_name)
        if newest:
            return newest
    return None


def _launcher_current_install(install_dir: Path, exe_name: str, config_name: str) -> str | None:
    """安装描述声明的当前版本目录里的 exe；描述缺失/损坏/越界一律返回 None。

    ``cur_path`` 是安装器写的，仍按**不可信输入**校验（见 :func:`_relative_dir_within`）：
    挑版本目录不能变成"照配置文件去读任意路径"。
    """
    if not config_name:
        return None
    try:
        with open(install_dir / config_name, "rb") as handle:
            raw = handle.read(_LAUNCHER_CONFIG_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _LAUNCHER_CONFIG_MAX_BYTES:
        log.debug("安装描述文件过大，按损坏处理：%s", install_dir / config_name)
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    relative = _relative_dir_within(data.get("cur_path"))
    if relative is None:
        return None
    target = install_dir / relative / exe_name
    try:
        return str(target) if target.is_file() else None
    except OSError:
        return None


def _relative_dir_within(value: object) -> Path | None:
    """把 ``cur_path`` 收成安装目录内的相对子目录；越界/绝对/盘符/``..`` 一律 None。

    只接受形状合法的相对目录名（可含多层），拒绝绝对路径、盘符（``C:``）、UNC、
    ``..`` 逃逸、NTFS 备用数据流（``:``）、控制字符，以及 Windows 会把尾随空格/点当
    成 ``..`` 的伪装写法（``".. "``）。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _CUR_PATH_MAX_CHARS or any(ord(char) < 32 for char in text):
        return None
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):  # 根路径；UNC（``\\server\share``）归一化后也是 ``//``
        return None
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        return None
    for part in parts:
        if ":" in part or part.rstrip(" .") in ("", ".", ".."):
            return None
    return Path(*parts)


def _newest_versioned_child(install_dir: Path, exe_name: str) -> str | None:
    """安装目录下「最可能当前」的子目录里的 exe：只挑真含 exe 的目录，按数字版本降序。

    ``iterdir`` 顺序不是承诺，所以结果必须由名字决定：数字版本段优先且按**数值**降序
    （``3.10.0`` > ``3.7.0``），非数字名按名字降序垫底。只枚举前
    :data:`_VERSIONED_MAX_ENTRIES` 条，多出来的条目不再 stat。
    """
    candidates: list[tuple[bool, tuple[int, ...], str]] = []
    try:
        for index, child in enumerate(install_dir.iterdir()):
            if index >= _VERSIONED_MAX_ENTRIES:
                log.debug("版本目录条目超过上限 %d，只检查已读到的：%s",
                          _VERSIONED_MAX_ENTRIES, install_dir)
                break
            try:
                if not child.is_dir() or not (child / exe_name).is_file():
                    continue
            except OSError:
                continue
            version = _numeric_version(child.name)
            candidates.append((version is not None, version or (), child.name))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return str(install_dir / candidates[0][2] / exe_name)


def _numeric_version(name: str) -> tuple[int, ...] | None:
    """纯数字版本目录名（``3.7.0`` / ``10.0``）→ 整数元组；其它名字返回 None。

    只认 ASCII 数字：``str.isdigit()`` 对 ``³``/``٣`` 这类也算真，而 ``int()`` 会失败
    或给出意外结果。名字长度也有上界（版本目录名很短，超长的一律不当版本号解析）。
    """
    if not name or len(name) > _VERSION_NAME_MAX_CHARS:
        return None
    parts = name.split(".")
    if any(not part.isascii() or not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _shallow_scan(root: Path, exe_name: str) -> str | None:
    """在 root 下浅层找 exe_name（限制层数与目录数，避免拖慢菜单）。"""
    try:
        stack = [(root, 0)]
        visited = 0
        while stack and visited < 200:
            current, depth = stack.pop()
            if depth >= _MAX_DEPTH:
                continue
            visited += 1
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            target = current / exe_name
            if target.is_file():
                return str(target)
            for entry in entries:
                try:
                    if entry.is_dir():
                        stack.append((entry, depth + 1))
                except OSError:
                    continue
    except Exception:
        log.debug("搜索播放器路径失败：%s", root, exc_info=True)
    return None


def clear_cache() -> None:
    """清掉路径缓存（含 ``None`` 负缓存），下次查询重新扫描。

    手填路径（``music_player_paths``，由 设置 → 桌宠 → 音乐关联 写入）每次查询都
    重新判定，不经过本缓存；本函数是给"自动搜索结果变了、要立刻重扫"留的入口，
    调用点是设置页保存时的 ``pet/settings_music.save_music_player_settings``
    （路径真的变了才清，避免每次保存都让右键菜单重扫盘）。

    不动 ``_warm_inflight``：正在跑的预热线程会自行收尾。它顶多把刚清掉的自动
    搜索结果再写回来，而手动路径一旦非空，查询根本不看缓存，语义不受影响。
    """
    _cache.clear()
