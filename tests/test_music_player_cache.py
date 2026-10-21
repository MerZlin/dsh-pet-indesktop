# -*- coding: utf-8 -*-
"""播放器路径缓存回归：右键菜单首次打开的 GUI 线程零文件系统扫描。

实测定案（py-spy，用户机器）：`_music_player_builder.build` 在 GUI 线程调
``music_players.find_player``，缓存冷时对每个播放器浅扫全部 ``_SEARCH_ROOTS``
（每 root 最多 200 目录的 iterdir+stat），16 个连续段共 6.4s 全在 MainThread，
首次右键菜单要冻 6 秒以上；第二次打开走缓存所以不卡。

本文件钉住四件事：
1. 菜单构建只读缓存：``_search`` / ``_shallow_scan`` / 模块内 ``Path.iterdir``
   零调用（冷缓存也一样）；
2. ``cached_player`` 的 found / not-found / cold 三态契约，``clear_cache`` 能清掉
   负缓存重新扫；
3. 后台预热幂等、扫描不发生在调用线程；
4. 点击路径的路径解析进 worker 线程，找不到时回 GUI 线程弹气泡（不静默 return）；
5. 四播放器（网易云/QQ音乐/酷狗/汽水）共用同一份注册表，检测仍是纯本地有界
   查找（不联网、不全盘扫），手填路径失效时不回退自动搜索。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu

from pet.music import players as music_players

# 模块导入时捕获真实现：conftest 的禁扫桩会在用例运行期替换 _search，
# 串行化用例需要显式恢复它（只替身扫描体 _search_filesystem）。
_REAL_SEARCH = music_players._search


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, *, timeout: float = 15.0, pump=None) -> bool:
    """轮询等待（事件同步 + 宽预算，不赌固定 sleep 时序）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pump is not None:
            pump()
        if predicate():
            return True
        time.sleep(0.01)
    if pump is not None:
        pump()
    return bool(predicate())


@pytest.fixture(autouse=True)
def _clean_player_cache():
    """每个用例从冷缓存 + 无在飞预热开始；测完把缓存**恢复成进来时的样子**。

    刻意不直接 ``clear_cache()`` 走人：那会把整轮套件早先暖好的缓存清掉，让
    后续别的测试（如 test_proactive 的菜单构建）重新触发真实目录扫描，把多份
    后台 IO 塞进 Qt 收尾时序里（见 docs/QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md）。
    本文件的用例自身全部打桩 ``_search`` / ``warm_cache_async``，不会真的扫盘。
    """
    saved_cache = dict(music_players._cache)
    saved_inflight = set(music_players._warm_inflight)
    music_players.clear_cache()
    with music_players._warm_lock:
        music_players._warm_inflight.clear()
    yield
    with music_players._warm_lock:
        music_players._warm_inflight.clear()
        music_players._warm_inflight.update(saved_inflight)
    music_players.clear_cache()
    music_players._cache.update(saved_cache)


class _Cfg:
    """菜单/点击路径只读的配置面替身。"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return None


class _FakePet:
    """完整菜单构建用的最小替身（同 tests/test_menu_layout.py 的口径）。"""

    idles = ["待机"]
    turns = moves = clicks = acts = []
    playback_speed = scale = 1.0
    drag_physics = no_move = mouse_through = False

    def __init__(self, values=None):
        self.cfg = _Cfg(values)
        self.bubbles = []

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _QtPet(QObject):
    """点击路径替身：QObject 宿主让 worker→GUI 的信号桥有 Qt 父子保活。"""

    def __init__(self, values=None):
        super().__init__()
        self.cfg = _Cfg(values)
        self.bubbles: list = []

    def show_bubble(self, text: str, duration_ms: int | None = None) -> None:
        self.bubbles.append((text, duration_ms))


def _menu_actions(menu: QMenu) -> list:
    """递归收集菜单树里的全部 QAction（含各级子菜单）。"""
    found = []
    for action in menu.actions():
        found.append(action)
        submenu = action.menu()
        if submenu is not None:
            found.extend(_menu_actions(submenu))
    return found


def _build_full_menu(monkeypatch, pet) -> QMenu:
    from pet import catalog
    from pet.context_menu import populate_context_menu
    from pet.context_menus import shared

    monkeypatch.setattr(shared.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(catalog, "list_available_characters", lambda: ["shenshen"])
    _qapp()
    menu = QMenu()
    populate_context_menu(menu, pet)
    return menu


# ------------------------------------------------------------ 菜单构建：零扫描


def test_cold_cache_menu_build_never_scans_filesystem(monkeypatch):
    """冷缓存下构建**完整**菜单：不浅扫、不查目录、乐观启用 + 触发一次后台预热。"""
    from pet.context_menus import shared

    scans: list = []
    searches: list = []
    warms: list = []
    iterdir_calls: list = []

    monkeypatch.setattr(music_players, "_shallow_scan", lambda *a, **kw: scans.append(a))
    monkeypatch.setattr(music_players, "_search", lambda key: searches.append(key))
    monkeypatch.setattr(
        music_players, "warm_cache_async",
        lambda key, manual="": (warms.append(key), True)[1],
    )

    class _SpyPath(type(pathlib.Path())):
        """计数 music_players 内部的目录枚举（模块级 Path 就是扫描用的那个）。"""

        def iterdir(self):
            iterdir_calls.append(str(self))
            return super().iterdir()

    monkeypatch.setattr(music_players, "Path", _SpyPath)

    menu = _build_full_menu(monkeypatch, _FakePet())
    try:
        assert scans == [], "GUI 线程构建菜单时发生了播放器目录浅扫"
        assert searches == [], "GUI 线程构建菜单时调用了会扫描的搜索入口"
        assert iterdir_calls == [], f"GUI 线程构建菜单时枚举了目录：{iterdir_calls}"

        by_text = {action.text(): action for action in _menu_actions(menu)}
        # 菜单条目由菜单侧维护：本用例钉的是「凡进了菜单的播放器，冷缓存下都必须
        # 乐观启用、且各触发一次幂等预热」，不写死两键或四键，避免与菜单改动打架。
        menu_keys = [
            key
            for key, (label, _exe, _dirs) in music_players.PLAYERS.items()
            if f"打开{label}给主人放歌" in by_text
        ]
        assert {"netease", "qqmusic"} <= set(menu_keys), f"菜单里缺原有播放器：{sorted(by_text)}"
        for key in menu_keys:
            label = music_players.player_label(key)
            action = by_text[f"打开{label}给主人放歌"]
            assert action.isEnabled() is True, f"冷缓存下应乐观启用：{label}"
            assert "找不到" not in action.toolTip(), "冷缓存不等于「找不到」，不该挂禁用理由"
        assert sorted(warms) == sorted(menu_keys), "冷缓存下每个播放器各触发一次幂等后台预热"
    finally:
        # 定向同步销毁（docs/QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md §3.4）：
        # 菜单测试不许把 DeferredDelete / 排队事件留给后续用例的 processEvents。
        shiboken6.delete(menu)


def test_warm_cache_menu_build_needs_no_scan_and_enables(monkeypatch):
    """缓存已暖（found）：照常启用，且不再触发预热、不扫描。"""
    from pet.context_menus import shared

    music_players._cache["netease"] = "D:/CloudMusic/cloudmusic.exe"
    searches: list = []
    warms: list = []
    monkeypatch.setattr(music_players, "_search", lambda key: searches.append(key))
    monkeypatch.setattr(
        music_players, "warm_cache_async",
        lambda key, manual="": (warms.append(key), True)[1],
    )
    _qapp()
    menu = QMenu()
    try:
        action = shared._music_player_builder("netease")(menu, _QtPet())
        assert action.isEnabled() is True
        assert searches == [] and warms == []
    finally:
        shiboken6.delete(menu)


def test_negative_cache_disables_action_with_hint(monkeypatch):
    """负缓存命中（此前扫过、本机没有）：禁用 + 保留原文案，绝不重扫。"""
    from pet.context_menus import shared

    music_players._cache["netease"] = None
    searches: list = []
    warms: list = []
    monkeypatch.setattr(music_players, "_search", lambda key: searches.append(key))
    monkeypatch.setattr(
        music_players, "warm_cache_async",
        lambda key, manual="": (warms.append(key), True)[1],
    )
    _qapp()
    menu = QMenu()
    try:
        action = shared._music_player_builder("netease")(menu, _QtPet())
        assert action.isEnabled() is False
        assert "找不到网易云音乐" in action.toolTip()
        assert "设置 → 桌宠 → 音乐关联" in action.toolTip()
        assert searches == [] and warms == [], "负缓存命中不该再扫/再预热"
    finally:
        shiboken6.delete(menu)


# ------------------------------------------------------------ 三态契约


def test_cached_player_reports_found_not_found_and_cold():
    music_players.clear_cache()
    assert music_players.cached_player("netease") == (music_players.CACHED_COLD, None)

    music_players._cache["netease"] = "D:/CloudMusic/cloudmusic.exe"
    assert music_players.cached_player("netease") == (
        music_players.CACHED_FOUND, "D:/CloudMusic/cloudmusic.exe")

    music_players._cache["qqmusic"] = None
    assert music_players.cached_player("qqmusic") == (music_players.CACHED_MISSING, None)

    assert music_players.cached_player("unknown-player") == (
        music_players.CACHED_MISSING, None)


def test_cached_player_resolves_manual_path_without_scanning(tmp_path, monkeypatch):
    """手填路径：命中/失效都是确定态（单次 stat），且绝不回退目录扫描。"""
    exe = tmp_path / "cloudmusic.exe"
    exe.write_bytes(b"")
    searches: list = []
    monkeypatch.setattr(music_players, "_search", lambda key: searches.append(key))

    assert music_players.cached_player("netease", str(exe)) == (
        music_players.CACHED_FOUND, str(exe))
    invalid = music_players.cached_player("netease", str(tmp_path / "missing.exe"))
    assert invalid == (music_players.CACHED_MISSING, None)
    # 手填失效时不静默退回自动搜索（与 find_player 同语义）
    assert invalid != (music_players.CACHED_COLD, None)
    assert searches == []


def test_cached_player_found_agrees_with_find_player(monkeypatch):
    """find_player 扫出来的结果，cached_player 必须读成 found（两条路径不漂移）。"""
    monkeypatch.setattr(music_players, "_search", lambda key: "D:/QQMusic/QQMusic.exe")
    music_players.clear_cache()
    assert music_players.find_player("qqmusic") == "D:/QQMusic/QQMusic.exe"
    assert music_players.cached_player("qqmusic") == (
        music_players.CACHED_FOUND, "D:/QQMusic/QQMusic.exe")


def test_clear_cache_drops_negative_cache_and_allows_rescan(monkeypatch):
    """clear_cache 语义不变：连 None 负缓存一起清掉，下次查询重新扫描。"""
    calls: list = []
    monkeypatch.setattr(
        music_players, "_search",
        lambda key: (calls.append(key), "D:/CloudMusic/cloudmusic.exe")[1],
    )
    music_players.clear_cache()
    assert music_players.find_player("netease") == "D:/CloudMusic/cloudmusic.exe"
    music_players._cache["netease"] = None
    assert music_players.cached_player("netease") == (music_players.CACHED_MISSING, None)

    music_players.clear_cache()
    assert music_players.cached_player("netease") == (music_players.CACHED_COLD, None)
    assert music_players.find_player("netease") == "D:/CloudMusic/cloudmusic.exe"
    assert calls == ["netease", "netease"], "清掉负缓存后必须真的重扫一次"


# ------------------------------------------------ 四播放器注册表与有界本地检测

# 新增播放器的键/可执行文件名/常见父目录名：右键菜单、设置页、config 清洗全部
# 从 music_players.PLAYERS 取值，谁都不许另抄一份键表（同源）。
_EXPECTED_PLAYERS = {
    "netease": ("cloudmusic.exe", ("CloudMusic", "网易云音乐")),
    "qqmusic": ("QQMusic.exe", ("QQMusic", "QQ音乐")),
    "kugou": ("KuGou.exe", ("KuGou", "KGMusic", "酷狗音乐")),
    "soda": ("SodaMusic.exe", ("SodaMusic", "汽水音乐")),
}


def test_registry_covers_four_players_with_known_exe_names():
    """注册表就是四播放器的键源：酷狗 KuGou.exe、汽水 SodaMusic.exe 都在。"""
    assert set(music_players.PLAYERS) == set(_EXPECTED_PLAYERS)
    assert music_players.player_keys() == tuple(music_players.PLAYERS)
    for key, (exe_name, dir_names) in _EXPECTED_PLAYERS.items():
        label, registered_exe, registered_dirs = music_players.PLAYERS[key]
        assert registered_exe == exe_name, key
        assert set(dir_names) <= set(registered_dirs), f"{key} 少了常见安装目录名"
        assert label and music_players.player_label(key) == label


def _write_fake_install(root: pathlib.Path, *relative: str) -> pathlib.Path:
    """按给定相对路径造一个假安装（只造目录与空 exe 文件）。"""
    exe = root.joinpath(*relative)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    return exe


@pytest.mark.parametrize(
    "player_key, relative",
    [
        ("kugou", ("KuGou", "KGMusic", "KuGou.exe")),   # 真实酷狗常见布局（多一层）
        ("kugou", ("酷狗音乐", "KuGou.exe")),            # 中文安装目录名
        ("soda", ("SodaMusic", "SodaMusic.exe")),       # 用户目录下的常见布局
        ("soda", ("汽水音乐", "SodaMusic.exe")),
        # 本机实测的真实汽水布局：安装目录名带空格，程序在版本号子目录里
        # （%LOCALAPPDATA%\Programs\Soda Music\3.7.0\SodaMusic.exe）。
        ("soda", ("Programs", "Soda Music", "3.7.0", "SodaMusic.exe")),
    ],
)
def test_detection_finds_players_in_common_install_layouts(tmp_path, monkeypatch, player_key, relative):
    """两种命名布局都要命中：候选根被收窄到 tmp，验证的是布局覆盖而非盘扫描。"""
    exe = _write_fake_install(tmp_path, *relative)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)  # 覆盖 conftest 的禁扫桩
    music_players.clear_cache()

    assert music_players.find_player(player_key) == str(exe)
    assert music_players.cached_player(player_key) == (music_players.CACHED_FOUND, str(exe))


def test_detection_ignores_unrelated_exe_names(tmp_path, monkeypatch):
    """exe 名不符就不算命中：不靠「目录里有个音乐程序」猜，也不退化成全盘找。"""
    _write_fake_install(tmp_path, "KuGou", "KuGouPro.exe")
    _write_fake_install(tmp_path, "SodaMusic", "SodaMusicSetup.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)
    music_players.clear_cache()

    assert music_players.find_player("kugou") is None
    assert music_players.find_player("soda") is None
    assert music_players.cached_player("kugou") == (music_players.CACHED_MISSING, None)


def test_versioned_install_probe_is_limited_to_registered_layouts(tmp_path, monkeypatch):
    """版本目录探测只对登记的布局生效：别的播放器不会被顺手带进版本目录。"""
    _write_fake_install(tmp_path, "Programs", "Soda Music", "3.7.0", "KuGou.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)
    music_players.clear_cache()

    assert music_players.find_player("kugou") is None, "酷狗不该被汽水的版本目录布局命中"
    assert set(music_players._VERSIONED_INSTALL_DIRS) <= set(music_players.PLAYERS)


# ------------------------------------- 版本化安装目录：挑「当前」的那一份（不是枚举首命中）
#
# 问题面（本机实测 2026-10-16）：``%LOCALAPPDATA%\Programs\Soda Music\`` 里除当前版本目录
# ``3.7.0`` 外还留着安装器自己的 ``Packages``、卸载器与启动器等条目；多版本共存（升级后
# 旧版本目录不会被删）时"目录里先被枚举到的那一个"**不是承诺**，而原实现取 iterdir 的首个
# 命中——于是右键菜单可能启动一份旧版。安装器把当前在用的版本写在自己的安装描述文件里
# （本机实测 ``launcher_config.json`` = ``{"app_name": "SodaMusic", "cur_path": "3.7.0"}``；
# 这是安装布局描述，不是账号或凭据），所以：**先信 cur_path**（且只接受安装目录内的相对
# 目录），配置缺失/损坏再按数字版本降序挑（``3.10`` 必须排在 ``3.7`` 前面——按字符串比会
# 反过来），枚举条目数与文件读取都有上界，绝不为此深挖全盘。
_SODA_INSTALL = ("Programs", "Soda Music")
_SODA_EXE = "SodaMusic.exe"


def _pin_enumeration(monkeypatch, directory: pathlib.Path, order) -> None:
    """把 ``directory`` 的枚举顺序钉成 ``order``（其余条目排在后面）。

    ``iterdir`` 的顺序不是承诺，而"选哪一份"的断言不能随它漂：钉住顺序才能把
    「首命中」与「按版本挑」两种实现区分开，红/绿都确定。
    """
    original = pathlib.Path.iterdir
    target = str(directory).lower()
    rank = {str(name).lower(): index for index, name in enumerate(order)}

    def iterdir(self):
        entries = list(original(self))
        if str(self).lower() != target:
            return iter(entries)
        return iter(sorted(entries, key=lambda p: (rank.get(p.name.lower(), len(rank)), p.name.lower())))

    monkeypatch.setattr(pathlib.Path, "iterdir", iterdir)


def _soda_install(tmp_path, monkeypatch, versions, *, order=None, config=None):
    """造一个汽水安装目录（版本子目录 + 可选安装描述文件），返回 (安装目录, {版本: exe})。

    ``config`` 为 dict 时按 JSON 写，为 bytes 时原样写（模拟损坏的安装描述），
    ``None`` 表示安装目录里没有这份文件。
    """
    install_dir = tmp_path.joinpath(*_SODA_INSTALL)
    exes = {
        version: _write_fake_install(tmp_path, *_SODA_INSTALL, version, _SODA_EXE)
        for version in versions
    }
    if config is not None:
        raw = json.dumps(config, ensure_ascii=False).encode("utf-8") if isinstance(config, dict) else config
        (install_dir / "launcher_config.json").write_bytes(raw)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)  # 覆盖 conftest 的禁扫桩
    music_players.clear_cache()
    if order is not None:
        _pin_enumeration(monkeypatch, install_dir, order)
    return install_dir, exes


@pytest.mark.parametrize("cur_path", ["3.7.0", "./3.7.0", "3.7.0/", "3.7.0//"])
def test_launcher_current_version_wins_over_the_newer_sibling(tmp_path, monkeypatch, cur_path):
    """``cur_path`` 说当前在用的是 3.7.0 就启动 3.7.0：枚举顺序与新版本都不该改写它。

    红在何处（改前）：首命中即返回，而这里枚举把 3.10.0 排在前面 —— 启动的是一个
    安装器**没有**声明为当前的目录。
    """
    install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.10.0", "3.7.0"],  # 枚举先给"更高版本"，逼出"首命中"的偏差
        config={"app_name": "SodaMusic", "cur_path": cur_path},
    )
    found = music_players.find_player("soda")
    assert found == str(exes["3.7.0"]), f"没跟安装器声明的当前版本：{found}"
    assert pathlib.Path(found).parent == install_dir / "3.7.0"


def test_versioned_install_without_a_launcher_config_sorts_versions_numerically(tmp_path, monkeypatch):
    """没有安装描述（老安装器 / 文件被删）时按数字版本降序：旧版枚举在前也得挑新版。"""
    _install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.7.0", "3.10.0"],  # 旧版排在前面（首命中会挑错的那个顺序）
    )
    assert music_players.find_player("soda") == str(exes["3.10.0"])


@pytest.mark.parametrize(
    "versions, expected",
    [
        (("3.7.0", "3.10.0", "3.9.0"), "3.10.0"),  # 字符串比会把 3.7 排到 3.10 前面
        (("9.0", "10.0"), "10.0"),
        (("2.0", "2.0.1"), "2.0.1"),
        (("1.0", "2.0", "Packages"), "2.0"),  # 非版本名的目录不能靠名字取胜
    ],
)
def test_versioned_install_picks_the_highest_numeric_version(tmp_path, monkeypatch, versions, expected):
    """多版本共存时挑数字版本最高的那一个（枚举顺序固定为给定顺序）。"""
    _install_dir, exes = _soda_install(tmp_path, monkeypatch, versions, order=list(versions))
    assert music_players.find_player("soda") == str(exes[expected])


@pytest.mark.parametrize(
    "cur_path",
    [
        "../../../Windows/System32",   # 目录逃逸
        "..\\..\\escape",              # 反斜杠形态的逃逸
        "../escape",                   # 姊妹目录（里面真有一个同名 exe，用来当诱饵）
        "C:\\Windows\\System32",       # 绝对路径
        "/etc",                        # POSIX 绝对
        "\\\\server\\share",           # UNC
        "3.10.0:ads",                  # NTFS 备用数据流
        ".. ",                         # Windows 会把尾随空格/点当 .. 处理
        "3.9.9",                       # 目录不存在
        "",                            # 空
        "   ",
        42,                            # 类型不对
        None,
        {"cur_path": "3.10.0"},        # 类型不对（嵌套字典）
    ],
)
def test_launcher_current_version_rejects_escape_and_broken_values(tmp_path, monkeypatch, cur_path):
    """越界/绝对/坏值的 ``cur_path`` 一律不采信：回退到**安装目录内**的数字版本排序。

    ``../escape`` 的诱饵是真的存在的同名 exe——采信它就是"照配置文件去读任意路径"。
    """
    _write_fake_install(tmp_path, "Programs", "escape", _SODA_EXE)
    install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.10.0", "3.7.0"],
        config={"app_name": "SodaMusic", "cur_path": cur_path},
    )
    found = music_players.find_player("soda")
    assert found == str(exes["3.10.0"]), f"坏 cur_path 没有回退到安装目录内的版本排序：{found}"
    assert pathlib.Path(found) == install_dir / "3.10.0" / _SODA_EXE


def test_launcher_current_version_requires_the_exe_to_be_there(tmp_path, monkeypatch):
    """``cur_path`` 指到真存在但没有 exe 的目录（半升级/残留）也算不可用：回退版本排序。"""
    install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.10.0", "3.7.0"],
        config={"app_name": "SodaMusic", "cur_path": "staging"},
    )
    (install_dir / "staging").mkdir()
    music_players.clear_cache()

    assert music_players.find_player("soda") == str(exes["3.10.0"])


@pytest.mark.parametrize(
    "raw",
    [
        b"{ not json",                 # 截断的 JSON
        b"[]",                         # 不是对象
        b'"3.7.0"',                    # 不是对象
        b"",                           # 空文件
        b'{"cur_path": 37}',           # 类型不对
        b'{"app_name": "SodaMusic"}',  # 没有 cur_path
        b'{"cur_path": "\xff\xfe"}',   # 不是合法 UTF-8
        b"\x00\x01\x02",
    ],
)
def test_corrupt_launcher_config_falls_back_to_numeric_order(tmp_path, monkeypatch, raw):
    """损坏的安装描述不是异常、也不让挑选变任意：回退安装目录内的数字版本降序。"""
    _install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.10.0", "3.7.0"],
        config=raw,
    )
    assert music_players.find_player("soda") == str(exes["3.10.0"])


def test_oversized_launcher_config_is_treated_as_broken(tmp_path, monkeypatch):
    """安装描述文件本身也有大小上界：异常大的文件按损坏处理，不整份读进来。"""
    limit = music_players._LAUNCHER_CONFIG_MAX_BYTES
    assert 0 < limit <= 1024 * 1024
    _install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0"],
        order=["3.7.0", "3.10.0"],
        config=b"{\"cur_path\": \"3.7.0\"" + b" " * (limit + 1) + b"}",
    )
    assert music_players.find_player("soda") == str(exes["3.10.0"])


def test_versioned_install_enumeration_is_bounded(tmp_path, monkeypatch):
    """条目数有上界：只看前 ``_VERSIONED_MAX_ENTRIES`` 条（安装目录不是版本仓库）。"""
    install_dir, exes = _soda_install(
        tmp_path, monkeypatch, ["3.7.0", "3.10.0", "9.9.9"],
        order=["3.7.0", "3.10.0", "9.9.9"],
    )
    assert 0 < music_players._VERSIONED_MAX_ENTRIES <= 128
    assert len(list(install_dir.iterdir())) < music_players._VERSIONED_MAX_ENTRIES
    monkeypatch.setattr(music_players, "_VERSIONED_MAX_ENTRIES", 2)
    music_players.clear_cache()

    assert music_players.find_player("soda") == str(exes["3.10.0"]), (
        "只看前 2 条时应在读到的两条里按数字降序挑：有界优先于猜中"
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        ("3.7.0", (3, 7, 0)),
        ("10.0", (10, 0)),          # 数值比较：10 > 9，字符串比较会反过来
        ("3.7", (3, 7)),
        ("3.7.0", (3, 7, 0)),
        ("1.", None),               # 空版本段
        (".1", None),
        ("3.7.0-beta", None),       # 带后缀的不当版本号解析（前缀匹配会误判）
        ("v3", None),
        ("³", None),                # isdigit() 为真但不是 ASCII 数字
        ("٣.٤", None),              # 阿拉伯-印度数字
        ("", None),
        ("1" * 40, None),           # 超长名字
    ],
)
def test_numeric_version_parsing_boundaries(name, expected):
    """版本目录名的解析只认 ASCII 数字点分：``3.10`` 必须大于 ``3.7``，其余一律 None。"""
    assert music_players._numeric_version(name) == expected


def test_launcher_config_registry_stays_inside_the_player_registry():
    """读安装描述的文件名按播放器登记，且登记面不许出现未注册的播放器。"""
    assert set(music_players._LAUNCHER_CUR_PATH_FILES) <= set(music_players.PLAYERS)


def test_detection_stays_local_and_bounded():
    """检测是纯本地有界目录查找：不联网、不读注册表、不起进程，递归深度有上界。"""
    source = pathlib.Path(music_players.__file__).read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    forbidden = {"socket", "urllib", "http", "requests", "aiohttp", "winreg", "subprocess"}
    assert not imported & forbidden, f"检测模块引入了联网/注册表/进程能力：{sorted(imported & forbidden)}"
    assert music_players._MAX_DEPTH <= 3, "目录递归深度失控（全盘扫描的前兆）"
    assert len(music_players._SEARCH_ROOTS) <= 8, "候选根数量失控（全盘扫描的前兆）"


@pytest.mark.parametrize("player_key", ["netease", "qqmusic", "kugou", "soda"])
def test_manual_path_wins_and_missing_manual_never_falls_back(tmp_path, monkeypatch, player_key):
    """四播放器同规：手填路径存在就用它；不存在返回 None，绝不静默回退自动搜索。"""
    searches: list = []
    monkeypatch.setattr(
        music_players, "_search",
        lambda key: (searches.append(key), str(tmp_path / "auto.exe"))[1],
    )
    music_players.clear_cache()

    manual = tmp_path / f"{player_key}.exe"
    manual.write_bytes(b"")
    assert music_players.find_player(player_key, str(manual)) == str(manual)
    assert music_players.cached_player(player_key, str(manual)) == (
        music_players.CACHED_FOUND, str(manual))

    missing = str(tmp_path / "missing.exe")
    assert music_players.find_player(player_key, missing) is None
    assert music_players.cached_player(player_key, missing) == (
        music_players.CACHED_MISSING, None)
    assert searches == [], "手填路径命中/失效都不该扫盘，更不能回退自动搜索"



# ------------------------------------------------------------ 后台预热


def test_warm_cache_async_scans_in_background_thread_and_is_idempotent(monkeypatch):
    """预热在非调用线程里跑，且同一 key 在飞期间只允许一次扫描。"""
    caller = threading.get_ident()
    scans: list = []
    started = threading.Event()

    def fake_search(key):
        scans.append((key, threading.get_ident()))
        started.set()
        return "D:/CloudMusic/cloudmusic.exe"

    monkeypatch.setattr(music_players, "_search", fake_search)
    music_players.clear_cache()

    assert music_players.warm_cache_async("netease") is True
    assert music_players.warm_cache_async("netease") is False, "在飞期间必须幂等"
    assert started.wait(15.0), "预热线程没跑起来"
    assert _wait_until(lambda: "netease" in music_players._cache)
    assert music_players._cache["netease"] == "D:/CloudMusic/cloudmusic.exe"
    assert scans and all(ident != caller for _key, ident in scans), "预热不能跑在调用线程"
    # 缓存已暖：再预热是 no-op
    assert music_players.warm_cache_async("netease") is False


def test_warm_cache_async_skips_manual_path_and_caches_negative_result(monkeypatch):
    """手填路径无需扫描；扫不到时也要留下 None 负缓存（否则每次都白扫）。"""
    calls: list = []
    monkeypatch.setattr(
        music_players, "_search", lambda key: (calls.append(key), None)[1])
    music_players.clear_cache()

    assert music_players.warm_cache_async("netease", "D:/some/cloudmusic.exe") is False
    assert calls == []

    assert music_players.warm_cache_async("qqmusic") is True
    assert _wait_until(lambda: "qqmusic" in music_players._cache)
    assert music_players._cache["qqmusic"] is None
    assert music_players.cached_player("qqmusic") == (music_players.CACHED_MISSING, None)


# ------------------------------------------------------------ UI 就绪后的启动预热


def test_ui_ready_prewarm_schedules_music_player_cache_warm(monkeypatch):
    """UI 就绪后的统一预热点要带上播放器路径缓存：延迟 2~3s、后台线程、两个播放器。"""
    import pet.app as app_mod

    scheduled: list = []
    monkeypatch.setattr(
        app_mod.QTimer, "singleShot",
        lambda ms, fn=None: scheduled.append((ms, fn)),
    )
    app_mod._schedule_music_player_warm()

    assert scheduled, "UI 就绪后没有调度播放器路径预热"
    delay_ms, task = scheduled[0]
    assert 2000 <= delay_ms <= 3000, "预热要错峰到动画预热之后（2~3s），不能挤启动期"
    assert task is app_mod._warm_music_player_paths

    warmed: list = []
    monkeypatch.setattr(
        music_players, "warm_cache_async",
        lambda key, manual="": (warmed.append(key), True)[1],
    )
    task()
    assert warmed == ["netease", "qqmusic"], "启动预热要覆盖两个播放器"

    source = inspect.getsource(app_mod.PetInstance._create_library)
    assert "_schedule_music_player_warm()" in source, "统一预热调度点漏了播放器路径预热"


# ------------------------------------------------------------ 点击路径：解析离线程


def test_launch_player_resolves_in_worker_thread_not_calling_thread(monkeypatch):
    """路径解析必须发生在 worker 线程（冷缓存时 6s+ 的扫描不能压回 GUI 线程）。"""
    from pet.music import now_playing
    from pet.context_menus import shared

    _qapp()
    caller = threading.get_ident()
    idents: list = []
    resolved = threading.Event()
    played: list = []

    def fake_find(key, manual=""):
        idents.append(threading.get_ident())
        resolved.set()
        return "D:/CloudMusic/cloudmusic.exe"

    def fake_play(exe_name):
        played.append((exe_name, threading.get_ident()))
        return True  # 会话已在跑：worker 到此收工，不进启动/轮询分支

    monkeypatch.setattr(music_players, "find_player", fake_find)
    monkeypatch.setattr(now_playing, "play_session_for", fake_play)
    pet = _QtPet()
    try:
        shared._launch_player_and_play("netease", pet)
        assert resolved.wait(15.0), "点击后的解析线程没跑起来"
        assert _wait_until(lambda: bool(played)), "解析结果没被 worker 用起来"
    finally:
        shiboken6.delete(pet)

    assert idents and caller not in idents, "路径解析跑在了调用线程（GUI）上"
    assert played[0][0] == "cloudmusic.exe" and played[0][1] != caller


def test_launch_player_bubbles_hint_when_player_missing(monkeypatch):
    """找不到播放器不再静默 return：经 queued 信号回 GUI 线程弹气泡。"""
    from pet.context_menus import shared

    app = _qapp()
    monkeypatch.setattr(music_players, "find_player", lambda key, manual="": None)
    pet = _QtPet()
    try:
        shared._launch_player_and_play("netease", pet)
        assert _wait_until(lambda: bool(pet.bubbles), pump=app.processEvents)
        bubbles = list(pet.bubbles)
    finally:
        shiboken6.delete(pet)

    assert "找不到网易云音乐" in bubbles[0][0]
    assert "设置 → 桌宠 → 音乐关联" in bubbles[0][0]


def test_launch_player_reads_manual_path_from_config(monkeypatch):
    """点击路径仍把设置里的手填路径交给解析（配置读取留在调用线程，无扫描）。"""
    from pet.music import now_playing
    from pet.context_menus import shared

    _qapp()
    seen: list = []
    monkeypatch.setattr(
        music_players, "find_player",
        lambda key, manual="": (seen.append((key, manual)), "D:/x/cloudmusic.exe")[1],
    )
    monkeypatch.setattr(now_playing, "play_session_for", lambda exe_name: True)
    pet = _QtPet({"music_player_paths": {"netease": "D:/Custom/cloudmusic.exe"}})
    try:
        shared._launch_player_and_play("netease", pet)
        assert _wait_until(lambda: bool(seen))
    finally:
        shiboken6.delete(pet)

    assert seen == [("netease", "D:/Custom/cloudmusic.exe")]


# ------------------------------------------------------------ 信号桥生命周期


def test_launch_player_survives_bridge_destroyed_midflight(monkeypatch):
    """桥随宿主窗口销毁后 worker 才收工：emit 不得抛异常（P1-2）。

    回归：worker 里对可能已销毁的 ``_MusicLaunchBridge`` 直接 emit（无
    RuntimeError 防护），daemon 线程以未捕获异常收尾，"找不到播放器"这条用户
    可见提示恰好丢掉。标准防护写法见 pet/agent_link.py 的同款槽。
    """
    from pet.context_menus import shared

    _qapp()
    ready = threading.Event()
    release = threading.Event()
    worker_threads: list = []
    bridges: list = []
    original_bridge = shared._music_launch_bridge

    def capture_bridge(pet):
        bridge = original_bridge(pet)
        bridges.append(bridge)
        return bridge

    def fake_find(key, manual=""):
        worker_threads.append(threading.current_thread())
        ready.set()
        release.wait(15.0)
        return None  # 走"找不到播放器"的 emit 分支

    monkeypatch.setattr(shared, "_music_launch_bridge", capture_bridge)
    monkeypatch.setattr(music_players, "find_player", fake_find)

    errors: list = []
    original_hook = threading.excepthook

    def hook(args):
        errors.append(args)

    threading.excepthook = hook
    pet = _QtPet()
    try:
        shared._launch_player_and_play("netease", pet)
        assert ready.wait(15.0), "解析线程没跑起来"
        assert bridges, "没有建出信号桥"
        # 模拟宿主窗口销毁：桥是窗口的 QObject 子对象，一起被 C++ 侧销毁。
        shiboken6.delete(bridges[0])
        release.set()
        worker_threads[0].join(15.0)
        assert not worker_threads[0].is_alive(), "worker 没有收工"
    finally:
        release.set()
        threading.excepthook = original_hook
        shiboken6.delete(pet)

    assert errors == [], f"桥销毁后 worker 抛了未捕获异常：{errors}"


def test_launch_bridge_registry_collects_finished_workers(monkeypatch):
    """worker 收工后桥必须出队：成功路径原先直接 return，桥永不出队（P2-d）。

    非 QObject 宿主（最小外壳 / 测试替身）没有 Qt parent，桥靠
    ``_LAUNCH_BRIDGES`` 强引用保活；成功路径（找到播放器且会话在跑）不发
    notice，于是每点一次菜单就永久多留一个桥对象。
    """
    from pet.music import now_playing
    from pet.context_menus import shared

    app = _qapp()
    monkeypatch.setattr(
        music_players, "find_player",
        lambda key, manual="": "D:/CloudMusic/cloudmusic.exe",
    )
    monkeypatch.setattr(now_playing, "play_session_for", lambda exe_name: True)

    saved = set(shared._LAUNCH_BRIDGES)
    shared._LAUNCH_BRIDGES.clear()
    pet = _FakePet()
    try:
        for _ in range(3):
            shared._launch_player_and_play("netease", pet)
        assert len(shared._LAUNCH_BRIDGES) == 3, "桥没有入队（强引用保活语义坏了）"
        assert _wait_until(lambda: not shared._LAUNCH_BRIDGES, pump=app.processEvents), (
            f"worker 收工后桥没有出队：{shared._LAUNCH_BRIDGES}"
        )
        # 再来一轮：登记表不随点击次数增长
        for _ in range(3):
            shared._launch_player_and_play("netease", pet)
        assert _wait_until(lambda: not shared._LAUNCH_BRIDGES, pump=app.processEvents), (
            f"第二轮点击后桥仍在登记表里：{shared._LAUNCH_BRIDGES}"
        )
    finally:
        shared._LAUNCH_BRIDGES.clear()
        shared._LAUNCH_BRIDGES.update(saved)



# ---------------------------------------------------------------------------
# 事故 2026-09-19（PR #147 CI 取证 run 35408158831）：#140 的启动/菜单预热对
# netease/qqmusic 各起一条后台扫描线程，两条线程并发对 C:/、D:/ 盘根做
# iterdir+stat 风暴，Windows CI 上触发 C 级 access violation（faulthandler
# dump：一线程 Garbage-collecting、一线程 _shallow_scan，exit -1073741819）。
# 两层修复钉住：1) _search 全局串行化；2) conftest autouse 桩掉 _search。


def test_search_is_serialized_across_player_keys(monkeypatch):
    """不同 player_key 的盘扫描必须串行执行，不得交叠（并发扫盘 = AV 触发条件）。"""
    events: list[tuple[str, str]] = []

    def fake_scan(key: str) -> None:
        events.append(("enter", key))
        time.sleep(0.05)  # 放大交叠窗口：无锁时两条线程必然同时在场
        events.append(("exit", key))

    # 恢复真实现（覆盖 conftest 禁扫桩）——本用例测的就是锁本身；扫描体仍打桩
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)
    monkeypatch.setattr(music_players, "_search_filesystem", fake_scan)
    music_players.clear_cache()

    start = threading.Event()

    def run(key: str) -> None:
        assert start.wait(5.0), "启动屏障超时"
        music_players.find_player(key)

    threads = [
        threading.Thread(target=run, args=(key,), name=f"scan-probe-{key}", daemon=True)
        for key in ("netease", "qqmusic")
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(10.0)
    assert not any(t.is_alive() for t in threads), "扫描线程 10s 内未收尾"

    # 严格 enter/exit 交替：任何时刻至多一条线程在扫描
    assert len(events) == 4, f"扫描事件数异常：{events}"
    assert events[0][0] == "enter" and events[1][0] == "exit", f"扫描交叠：{events}"
    assert events[2][0] == "enter" and events[3][0] == "exit", f"扫描交叠：{events}"
    assert events[0][1] != events[2][1], "两个 key 都只扫了一次"


def test_player_scan_never_runs_in_tests(monkeypatch, tmp_path):
    """conftest 兜底回归：任何测试里 find_player 都不得真实扫盘。

    事故里预热线程在 CI runner 上扫 C:/、D:/ 盘根三层（每 root 最多 200
    目录）并与 GC 交叠触发 access violation。本用例变红说明有人移除了
    conftest 的 _search 桩（需要真实扫描语义的用例应显式 monkeypatch）。
    """
    calls: list[str] = []

    def spy_iterdir(self):
        calls.append(str(self))
        return iter(())

    monkeypatch.setattr(pathlib.Path, "iterdir", spy_iterdir)
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    music_players.clear_cache()

    music_players.find_player("netease")

    assert calls == [], "测试进程里发生了真实盘扫描"
