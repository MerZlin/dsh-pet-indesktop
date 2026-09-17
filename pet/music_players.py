# -*- coding: utf-8 -*-
"""定位本机音乐播放器的可执行文件。

右键菜单的「打开网易云并播放」需要知道播放器装在哪。策略是**先自动搜常见
路径，再允许用户手动覆盖**：

1. 配置里的手动路径（``music_player_paths``）优先——config.json 里指定过就用它；
2. 否则按候选目录名搜几个常见盘符（本机实测网易云在 ``D:\\CloudMusic``、
   QQ音乐在 ``D:\\QQ音乐\\QQMusic``，都不是默认的 Program Files）；
3. 都找不到就返回 ``None``；缓存暖了之后菜单项据此置灰并说明原因（缓存冷时
   菜单先乐观启用，扫描在后台线程里补）。

搜索结果进程内缓存：目录扫描有几十毫秒开销，而右键菜单每次打开都要问一遍。
因此缓存查询拆成两条路径：**GUI 线程（右键菜单）只读缓存**（``cached_player``，
绝不碰文件系统），真正的扫描留给后台线程与点击路径（``find_player``）。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 已知播放器：key -> (显示名, 可执行文件名, 可能的父目录名)
PLAYERS = {
    "netease": ("网易云音乐", "cloudmusic.exe", ("CloudMusic", "网易云音乐", "Netease")),
    "qqmusic": ("QQ音乐", "QQMusic.exe", ("QQMusic", "QQ音乐")),
}

# 搜哪些根目录：两个常见盘符 + 用户的 AppData（部分安装器会装在这里）。
_SEARCH_ROOTS = ("D:/", "C:/", "C:/Program Files", "C:/Program Files (x86)",
                 "C:/Users/%s/AppData/Local" % os.environ.get("USERNAME", ""),
                 "C:/Users/%s/AppData/Roaming" % os.environ.get("USERNAME", ""))

# 每个候选目录最多往下找几层（避免全盘递归）。
_MAX_DEPTH = 3

# 进程内缓存：player_key -> 路径或 None（None 也要缓存，否则每次都白扫一遍）。
_cache: dict[str, str | None] = {}

# cached_player 的三态标记（见该函数 docstring）。
CACHED_FOUND = "found"
CACHED_MISSING = "not-found"
CACHED_COLD = "cold"

# 后台预热去重：同一 key 同时只允许一个扫描线程在飞（幂等）。
_warm_inflight: set[str] = set()
_warm_lock = threading.Lock()


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
        # 目录名对不上时，浅层扫一层子目录找同名 exe。
        hit = _shallow_scan(root_path, exe_name)
        if hit:
            return hit
    return None


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

    手填路径（``music_player_paths``，手改 config.json）每次查询都重新判定，
    不经过本缓存；本函数是给"自动搜索结果变了、要立刻重扫"留的入口，当前
    ``pet/`` 内没有调用点（设置页也还没有可写该键的控件）。

    不动 ``_warm_inflight``：正在跑的预热线程会自行收尾。它顶多把刚清掉的自动
    搜索结果再写回来，而手动路径一旦非空，查询根本不看缓存，语义不受影响。
    """
    _cache.clear()
