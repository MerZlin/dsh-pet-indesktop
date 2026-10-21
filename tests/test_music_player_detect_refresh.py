# -*- coding: utf-8 -*-
"""「自动检测播放器」的第二轮契约回归（``pet/settings_music.py``）。

本文件钉住 ops 在既有机制上发现的四个问题（同一批改动，**不动后端**）：

1. **显式点检测必须真的重扫**。``warm_cache_async`` 是幂等的——``_cache`` 里已有值
   （包括上一次扫描留下的 ``None`` 负结果）时它直接返回 False、根本不扫盘。于是
   用户装好播放器再点「自动检测」，命中的就是那条**旧负缓存**，报「未找到」；用户
   唯一的出路是重启或去改配置文件。点检测 = "现在再找一遍"，必须先作废旧缓存
   （后端既有的 ``clear_cache``）再预热。
2. **检测期间用户改过的行不能被盖掉**。检测是异步的（四个 key 串行扫描，耗时随机器
   而异），用户很可能一边等一边手填。回填只写"从检测开始到现在文本没变过"的行。
   检测开始前就有的旧路径**允许**被检测结果替换（那正是点检测的目的），但结果文案
   必须说清替换掉了什么——不加无谓的确认弹窗。
3. **在飞扫描**：``clear_cache`` 不碰 ``_warm_inflight``，正在跑的扫描会自行收尾。
   若那条扫描的结果在"清缓存"之前就已写下、之后又不再有扫描接手，这一格会一直冷到
   超时——轮询对仍冷的格子补一次预热（幂等：在飞/已暖时不起线程），每轮每 key 的
   补扫次数有上界（扫描持续失败时不在 GUI 线程扫盘、也不无限起线程）。
4. **超时结果要区分「未完成」与「没找到」**：still cold 不能写成一格 missing
   （那会把"再等一会儿就有"的播放器说成"你这台机器没有"）；按钮与定时器仍要收口。

测试口径（对齐本仓 ``tests/test_music_player_cache.py`` / ``test_music_player_settings.py``）：
真实 Qt 控件（``ResourcePathPicker`` / ``QPushButton`` / ``QTimer`` + 真实事件循环）、
真实后台扫描线程；只打桩**文件系统边界**（``music_players._search``，或把
``_SEARCH_ROOTS`` 收窄到 tmp 目录走真扫描）。``QMessageBox`` 是模态窗（offscreen
下会阻塞），按本仓既有口径替身成记录器。
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from pet.music import players as music_players, settings as settings_music

pytest.importorskip("PySide6")

#: 模块导入时捕获真实现：conftest 的禁扫桩会在用例运行期替换 ``_search``，
#: 走真扫描的用例需要显式恢复它（同 tests/test_music_player_cache.py 口径）。
_REAL_SEARCH = music_players._search
#: 同样在导入时捕获真时钟：超时用例会把 ``settings_music.time.monotonic`` 换成假表
#: （那就是全局 ``time`` 模块），本文件的等待助手必须用自己的这一份，否则会自锁。
_MONOTONIC = time.monotonic

#: 契约：四个播放器的控件属性名。在这里逐字钉住（而不是从 ``settings_music`` 反查），
#: 免得产品侧漏一个播放器时用例跟着一起漏（同 test_music_player_settings.py 口径）。
PLAYER_ATTRS = {
    "netease": "music_player_netease_picker",
    "qqmusic": "music_player_qqmusic_picker",
    "kugou": "music_player_kugou_picker",
    "soda": "music_player_soda_picker",
}


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pump() -> None:
    _qapp().processEvents()


def _wait_until(predicate, *, timeout: float = 15.0, pump=False) -> bool:
    """轮询等待（事件同步 + 宽预算，不赌固定 sleep 时序）。"""
    deadline = _MONOTONIC() + timeout
    while _MONOTONIC() < deadline:
        if pump:
            _pump()
        if predicate():
            return True
        time.sleep(0.01)
    if pump:
        _pump()
    return bool(predicate())


def _wait_detect_end(dialog, *, timeout: float = 30.0) -> bool:
    """等这一轮检测收口（真实定时器 + 真实事件循环）。"""
    return _wait_until(
        lambda: getattr(dialog, "_music_detect_timer", None) is not None
        and not dialog._music_detect_timer.isActive(),
        timeout=timeout,
        pump=True,
    )


def _drain_inflight(key: str, *, timeout: float = 10.0) -> None:
    """等在飞预热收尾（用例必须先把挂起的扫描放行，否则线程会溜到别的用例里）。"""
    _wait_until(lambda: key not in music_players._warm_inflight, timeout=timeout)


def _picker(dialog, key: str):
    picker = getattr(dialog, PLAYER_ATTRS[key], None)
    assert picker is not None, f"缺少控件 {PLAYER_ATTRS[key]}"
    return picker


def _line_for(text: str, label: str) -> str:
    """结果文案里该播放器的那一行（逐项断言，避免四项互相顶包）。"""
    for line in text.splitlines():
        if line.startswith(f"{label}："):
            return line
    raise AssertionError(f"结果文案里没有「{label}」那一行：{text!r}")


def _write_exe(root, *relative):
    """按给定相对路径造一个假安装（只造目录与空 exe 文件）。"""
    exe = root.joinpath(*relative)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    return exe


class _Messages:
    """``QMessageBox`` 替身：模态窗在 offscreen 下会阻塞，按本仓既有口径记录调用。

    ``question`` / ``warning`` / ``critical`` 也记一份（而不是让属性缺失直接报错）：
    契约是"点检测不加无谓的确认弹窗"，用例据此断言 ``other == []``。
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.other: list = []

    def information(self, parent, title, text, *args, **kwargs):
        self.calls.append((str(title), str(text)))
        return None

    def question(self, *args, **kwargs):
        self.other.append(("question", args, kwargs))
        return None

    def warning(self, *args, **kwargs):
        self.other.append(("warning", args, kwargs))
        return None

    def critical(self, *args, **kwargs):
        self.other.append(("critical", args, kwargs))
        return None


class _Cfg:
    """控件创建只读的配置面替身（同 tests/test_music_player_cache.py 的 ``_Cfg``）。"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return None


@pytest.fixture(autouse=True)
def _isolated_player_cache():
    """每个用例从冷缓存 + 无在飞预热开始，测完**恢复成进来时的样子**（含 ``_cache``）。"""
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


@pytest.fixture
def make_host():
    """真实 Qt 宿主工厂：用产品自己的 ``create_music_player_controls`` 建控件。

    轻量宿主（不建整个设置对话框）是因为这里测的是检测机制本身：控件、按钮到
    ``start_detect`` 的信号连线、``QTimer`` 与轮询全部走产品真身。整窗集成另有一条
    用例（``test_explicit_detect_rescans_a_stale_negative_cache``）走真实设置对话框。
    """
    from PySide6.QtWidgets import QWidget

    class _Host(QWidget):
        def __init__(self, paths=None):
            super().__init__()
            self.config = _Cfg({"music_player_paths": dict(paths or {})})
            settings_music.create_music_player_controls(self)

    app = _qapp()
    made: list = []

    def build(paths=None):
        host = _Host(paths)
        made.append(host)
        return host

    yield build
    for host in made:
        host.close()
    app.processEvents()


# ---------------------------------------------------------------- 契约漂移护栏


def test_this_file_pins_the_registry_keys():
    """用例里写死的四键必须与注册表一致（注册表加播放器时这里先红，而不是静默漏测）。"""
    assert tuple(PLAYER_ATTRS) == music_players.player_keys()


# ----------------------------------------------------- 1. 显式检测必须真的重扫


def test_explicit_detect_rescans_a_stale_negative_cache(tmp_path, monkeypatch):
    """回归（ops 发现）：装好播放器后点「自动检测」不能吃上一次的负缓存。

    端到端：真实设置对话框 + 真实按钮点击 + 真实后台扫描线程 + 真实文件系统
    （``_SEARCH_ROOTS`` 收窄到 tmp 目录，扫描体不动）。旧实现里这一步必红——
    ``warm_cache_async`` 见 ``_cache["kugou"] is None`` 直接幂等返回，用户看到
    「未找到」，唯一的出路是重启。
    """
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(music_players, "_SEARCH_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    # 1) 先造出"上次扫过、当时确实没有"的旧负缓存（用户那时还没装播放器）。
    assert music_players.find_player("kugou") is None
    assert music_players.cached_player("kugou") == (music_players.CACHED_MISSING, None)

    # 2) 用户随后装好了播放器（浅扫布局：<root>/KuGou/KuGou.exe）。
    exe = _write_exe(tmp_path, "KuGou", "KuGou.exe")

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    try:
        # 3) 点「自动检测」：必须重新扫一遍，不能把旧负缓存当结果复用。
        assert dialog.music_player_detect_btn.text() == "自动检测"
        dialog.music_player_detect_btn.click()
        assert _wait_detect_end(dialog), "检测轮没在预算内收口"

        assert _picker(dialog, "kugou").text() == str(exe), "显式检测必须重扫出真结果"
        title, text = messages.calls[-1]
        assert title == "自动检测播放器"
        assert f"酷狗音乐：{exe}" in text
        assert "酷狗音乐：未找到" not in text
    finally:
        dialog.close()
        app.processEvents()


def test_detect_clears_the_stale_cache_before_warming(make_host, monkeypatch):
    """协议次序：先作废旧缓存（含负缓存），再逐个播放器预热，且不重复预热。"""
    events: list = []
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    def clear():
        events.append("clear")

    def warm(key, manual=""):
        events.append(f"warm:{key}:{manual}")
        return True

    def lookup(key, manual=""):
        return music_players.CACHED_MISSING, None

    host = make_host()
    settings_music.start_detect(host, warm=warm, lookup=lookup, clear=clear)

    assert events[0] == "clear", "显式检测必须先作废旧缓存，否则旧负缓存会被复用"
    assert events[1:] == [f"warm:{key}:" for key in music_players.player_keys()], (
        "预热要覆盖每个播放器、且每个只预热一次（未冷的格子不补扫）"
    )
    assert messages.calls, "四个都没找到也要给出结论"


# ------------------------------------------- 2. 检测期间用户改过的行不被覆盖


def test_a_path_typed_while_detecting_is_kept_not_overwritten(make_host, monkeypatch):
    """检测是异步的：用户在等待期间手填的路径不能被回填结果盖掉。

    网易云的扫描被闸在后台（真实线程），用户此刻填了别的路径；松闸后结果出来，
    网易云那一行必须保留用户填的，而**没被改过**的汽水那一行照常回填。
    """
    gate = threading.Event()
    netease_found = r"D:\CloudMusic\cloudmusic.exe"
    soda_found = r"D:\SodaMusic\3.7.0\SodaMusic.exe"

    def search(key):
        if key == "netease":
            assert gate.wait(20.0), "用例没有放行挂起的扫描"
            return netease_found
        if key == "soda":
            return soda_found
        return None

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    host = make_host()
    settings_music.start_detect(host)
    assert host._music_detect_timer.isActive() is True, "网易云还在扫，轮询不该收口"
    assert messages.calls == []

    typed = r"D:\我自己填的\cloudmusic.exe"
    _picker(host, "netease").setText(typed)

    gate.set()
    try:
        assert _wait_detect_end(host), "检测轮没在预算内收口"
    finally:
        _drain_inflight("netease")

    assert _picker(host, "netease").text() == typed, "检测期间用户填的路径被结果盖掉了"
    assert _picker(host, "soda").text() == soda_found, "没被改过的行仍然要回填"

    text = messages.calls[-1][1]
    netease_line = _line_for(text, "网易云音乐")
    assert netease_found in netease_line, "检测到的路径仍要如实告知"
    assert "保留" in netease_line, "被保留时要说明为什么没填进去"


def test_detect_replaces_a_pre_existing_path_and_says_so(make_host, monkeypatch):
    """检测开始前就有的旧路径允许被替换，但文案要说清：替换了什么、没动什么。"""
    old_netease = r"D:\旧位置\cloudmusic.exe"
    new_netease = r"D:\CloudMusic\cloudmusic.exe"
    same_qq = r"D:\QQMusic\QQMusic.exe"

    def search(key):
        return {"netease": new_netease, "qqmusic": same_qq}.get(key)

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    host = make_host({"netease": old_netease, "qqmusic": same_qq})
    assert _picker(host, "netease").text() == old_netease, "前提：行里本来就有旧路径"

    host.music_player_detect_btn.click()
    assert _wait_detect_end(host)

    assert _picker(host, "netease").text() == new_netease, "检测找到的路径要替换旧路径"
    text = messages.calls[-1][1]
    netease_line = _line_for(text, "网易云音乐")
    assert new_netease in netease_line
    assert old_netease in netease_line and "替换" in netease_line, (
        "替换了用户原来的路径就必须写明，不能只报一个新路径"
    )
    qq_line = _line_for(text, "QQ音乐")
    assert same_qq in qq_line and "替换" not in qq_line, "值没变就不该出现替换字样"
    assert messages.other == [], "点检测不该弹确认窗（替换语义靠文案说明）"


# ------------------------------------------------------- 3. 在飞扫描的相容性


def test_detect_uses_an_already_in_flight_scan_instead_of_reporting_not_found(
    make_host, monkeypatch
):
    """点检测前已有扫描在飞（例如右键菜单刚触发过预热）：等它、用它的结果。

    在飞 ≠ 没找到：这一格是冷的、但结果马上就来，不许提前收尾成「未找到」，
    也不许另起一条重复扫描。
    """
    gate = threading.Event()
    found = r"D:\KuGou\KGMusic\KuGou.exe"
    scans: list = []

    def search(key):
        scans.append(key)
        if key == "kugou":
            assert gate.wait(20.0), "用例没有放行挂起的扫描"
            return found
        return None

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    host = make_host()

    assert music_players.warm_cache_async("kugou") is True, "前提：先有一条在飞的扫描"
    assert _wait_until(lambda: scans.count("kugou") == 1), "前提：那条扫描真的进了扫描体"

    settings_music.start_detect(host)
    for _ in range(12):
        _pump()
        time.sleep(0.05)

    assert messages.calls == [], "在飞的格子上不许提前给结论"
    assert host._music_detect_timer.isActive() is True
    assert scans.count("kugou") == 1, "在飞期间不许另起一条重复扫描（幂等）"

    gate.set()
    try:
        assert _wait_detect_end(host), "检测轮没在预算内收口"
    finally:
        _drain_inflight("kugou")

    assert _picker(host, "kugou").text() == found, "在飞扫描的结果要落到那一行"
    assert "未找到" not in _line_for(messages.calls[-1][1], "酷狗音乐")


def test_a_scan_whose_result_was_cleared_away_is_restarted_without_faking_a_value(
    make_host, monkeypatch
):
    """清缓存与在飞扫描的竞态窗口：结果被清掉、扫描也已收尾 ⇒ 必须补一次真扫描。

    构造的正是那个窗口：上一次扫描已经把它自己的结果写下、又被 ``clear_cache``
    清掉，而它仍在 ``_warm_inflight`` 里（收尾前的一小段）。这一格不能就此一直冷到
    超时（更不能报「未找到」）：它收尾后必须被补一次**真扫描**。
    """
    found = r"D:\KuGou\KGMusic\KuGou.exe"
    scans: list = []

    def search(key):
        scans.append(key)
        return found if key == "kugou" else None

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    host = make_host()

    with music_players._warm_lock:
        music_players._warm_inflight.add("kugou")
    try:
        settings_music.start_detect(host)
        for _ in range(8):
            _pump()
            time.sleep(0.05)
        assert scans.count("kugou") == 0, "在飞期间不该另起扫描"
        assert host._music_detect_timer.isActive() is True, "这一格还是冷的，轮询不该收口"
        assert music_players.cached_player("kugou") == (music_players.CACHED_COLD, None)

        # 那条在飞扫描收尾了——但它的结果已经被清掉，缓存里什么都没有。
        with music_players._warm_lock:
            music_players._warm_inflight.discard("kugou")

        assert _wait_detect_end(host), "竞态窗口之后没有补扫，这一轮只会冷到超时"
    finally:
        with music_players._warm_lock:
            music_players._warm_inflight.discard("kugou")

    assert scans.count("kugou") >= 1, "要靠**真扫描**收口，不是猜一个值填进去"
    assert _picker(host, "kugou").text() == found
    assert "未找到" not in _line_for(messages.calls[-1][1], "酷狗音乐")


def test_a_failing_scan_is_retried_a_bounded_number_of_times(make_host, monkeypatch):
    """扫描线程异常退出（缓存仍冷）要补扫，但补扫次数封顶：不无限起线程。"""
    started: list = []
    lock = threading.Lock()

    def search(key):
        with lock:
            started.append(key)
        raise RuntimeError("扫描线程炸了（用例构造）")

    monkeypatch.setattr(music_players, "_search", search)
    monkeypatch.setattr(settings_music, "QMessageBox", _Messages())

    host = make_host()
    settings_music.start_detect(host)
    try:
        # 补扫要有**测试侧**的预算（等线程真的起来），不能靠"轮询若干次"猜时序。
        budget = _MONOTONIC() + 10.0
        expected = 1 + settings_music.DETECT_MAX_RESCANS_PER_PLAYER
        while _MONOTONIC() < budget:
            settings_music.poll_detect(host)
            if started.count("netease") >= expected:
                break
            time.sleep(0.005)
    finally:
        host._music_detect_timer.stop()

    per_key = started.count("netease")
    assert per_key >= 2, "扫描死掉后要补扫，不能把冷缓存直接当结论"
    assert per_key <= 1 + settings_music.DETECT_MAX_RESCANS_PER_PLAYER, (
        "补扫必须封顶：否则扫描持续失败时每次轮询都起一条线程"
    )
    assert host.music_player_detect_btn.isEnabled() is False, "还没出结论，按钮仍应是检测中"


def test_an_exhausted_rescan_budget_reports_unfinished_without_promising_a_worker(
    make_host, monkeypatch
):
    """补扫封顶后仍全冷：一条线程都不剩，文案不许声称"后台还在扫描 / 再点就能看到"。

    扫描线程持续失败时补扫有上界（:data:`DETECT_MAX_RESCANS_PER_PLAYER`）：封顶之后
    ``_warm_inflight`` 为空、缓存仍冷，这一轮只会空转到超时——**没有任何 worker 会再
    写下结果**。旧文案把「冷」一律写成「后台还在扫描」「稍后再点一次「自动检测」就能
    看到结果」，对这种情况是假承诺：用户照做，等到的还是同一句话。
    """
    clock = {"now": 10_000.0}
    monkeypatch.setattr(settings_music.time, "monotonic", lambda: clock["now"])

    def search(key):
        raise RuntimeError("扫描线程炸了（用例构造）")

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    cap = settings_music.DETECT_MAX_RESCANS_PER_PLAYER
    keys = music_players.player_keys()
    host = make_host()
    settings_music.start_detect(host)
    try:
        # 补扫要有**测试侧**的预算（等线程真的起来），不能靠"轮询若干次"猜时序。
        budget = _MONOTONIC() + 10.0
        while _MONOTONIC() < budget:
            settings_music.poll_detect(host)
            if all(host._music_detect_rescans.get(key, 0) >= cap for key in keys):
                break
            time.sleep(0.005)

        assert all(host._music_detect_rescans.get(key, 0) >= cap for key in keys), (
            "前提没成立：补扫没用尽，这一格还会再起线程"
        )
        assert _wait_until(lambda: not music_players._warm_inflight), (
            "前提没成立：还有补扫线程在跑（那才是真的「后台还在扫描」）"
        )
        assert messages.calls == [], "还没到超时点就不许给结论"
        assert host._music_detect_timer.isActive() is True

        clock["now"] += settings_music.DETECT_TIMEOUT_SECONDS + 1.0
        settings_music.poll_detect(host)
    finally:
        host._music_detect_timer.stop()

    text = messages.calls[-1][1]
    for key in keys:
        line = _line_for(text, music_players.player_label(key))
        assert "未完成" in line, f"{key} 这一格仍冷，要如实说未完成"
        assert "未找到" not in line, "still cold 不是 missing：别把没扫完的报成没有"
    assert "后台还在扫描" not in text, "补扫已封顶、一条线程都没有，不能声称后台还在扫"
    assert "就能看到" not in text, "不保证重试必有结果，别许这个承诺"
    assert host._music_detect_timer.isActive() is False, "超时也必须收口，不留空转定时器"
    assert host.music_player_detect_btn.isEnabled() is True
    assert host.music_player_detect_btn.text() == "自动检测"


# ------------------------------------------------ 4. 超时：未完成 ≠ 没找到


def test_timeout_reports_unfinished_separately_from_not_found(make_host, monkeypatch):
    """超时收口时：仍在扫的格子是「未完成」，确定没有的才是「未找到」，按钮timer收口。

    真机预算 20s/播放器，用例把 ``settings_music.time.monotonic`` 换成假表来走到
    超时点（不 sleep 80 秒），扫描线程仍是真线程。
    """
    gate = threading.Event()
    clock = {"now": 10_000.0}
    monkeypatch.setattr(settings_music.time, "monotonic", lambda: clock["now"])

    def search(key):
        if key == "kugou":
            assert gate.wait(20.0), "用例没有放行挂起的扫描"
        return None

    monkeypatch.setattr(music_players, "_search", search)
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)

    host = make_host()
    settings_music.start_detect(host)
    try:
        # 另外三个 key 都确定没有（真扫描线程已写回负缓存），酷狗还在飞。
        assert _wait_until(
            lambda: all(
                music_players.cached_player(key)[0] == music_players.CACHED_MISSING
                for key in ("netease", "qqmusic", "soda")
            ),
            pump=True,
        ), "前提没成立：另外三个播放器的扫描没收尾"
        assert host._music_detect_timer.isActive() is True
        assert messages.calls == [], "还没到超时点就不许给结论"

        clock["now"] += settings_music.DETECT_TIMEOUT_SECONDS + 1.0
        settings_music.poll_detect(host)
    finally:
        gate.set()
        _drain_inflight("kugou")

    text = messages.calls[-1][1]
    kugou_line = _line_for(text, "酷狗音乐")
    assert "未完成" in kugou_line, "超时后仍在扫的格子要如实说未完成"
    assert "未找到" not in kugou_line, "still cold 不是 missing：别把还在扫的报成没有"
    for key in ("netease", "qqmusic", "soda"):
        line = _line_for(text, music_players.player_label(key))
        assert "未找到" in line, f"{key} 是确定没有，应报「未找到」"
    assert host._music_detect_timer.isActive() is False, "超时也必须收口，不留空转定时器"
    assert host.music_player_detect_btn.isEnabled() is True
    assert host.music_player_detect_btn.text() == "自动检测"


# --------------------------------------------------------- GUI 线程零扫盘


def test_detect_never_scans_on_the_gui_thread(make_host, monkeypatch):
    """整轮检测的目录扫描只能发生在后台线程（GUI 线程扫盘会冻住设置页）。"""
    caller = threading.get_ident()
    idents: list = []

    def search(key):
        idents.append(threading.get_ident())
        return None

    monkeypatch.setattr(music_players, "_search", search)
    monkeypatch.setattr(settings_music, "QMessageBox", _Messages())

    host = make_host()
    settings_music.start_detect(host)
    assert _wait_detect_end(host)

    assert idents, "整轮检测一次都没扫盘（前提没成立）"
    assert caller not in idents, "目录扫描跑到了 GUI 线程"
