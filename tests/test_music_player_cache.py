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
4. 点击路径的路径解析进 worker 线程，找不到时回 GUI 线程弹气泡（不静默 return）。
"""
from __future__ import annotations

import inspect
import pathlib
import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu

from pet import music_players


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
        for label in ("打开网易云音乐给主人放歌", "打开QQ音乐给主人放歌"):
            action = by_text[label]
            assert action.isEnabled() is True, f"冷缓存下应乐观启用：{label}"
            assert "找不到" not in action.toolTip(), "冷缓存不等于「找不到」，不该挂禁用理由"
        assert warms == ["netease", "qqmusic"], "冷缓存下每个播放器各触发一次幂等后台预热"
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
        assert "手动指定路径" in action.toolTip()
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
    from pet import now_playing
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
    assert "可在配置文件中手动指定路径" in bubbles[0][0]


def test_launch_player_reads_manual_path_from_config(monkeypatch):
    """点击路径仍把设置里的手填路径交给解析（配置读取留在调用线程，无扫描）。"""
    from pet import now_playing
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
    from pet import now_playing
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

