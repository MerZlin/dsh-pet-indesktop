# -*- coding: utf-8 -*-
"""issue #111：Windows 关机/注销时绝不再派生 ffmpeg 子进程。

现象：每次关机必弹「ffmpeg-win-x86_64-v7.1.exe - 应用程序无法正常启动
(0xc0000142)」，阻塞关机流程。

根因：桌宠对「操作系统已宣告会话结束」零感知——即使关机已开始，动画链仍在
正常运转并随时 CreateProcess 新的 ffmpeg 取帧进程（冷首帧预热 / reader 换代 /
元数据探测 / 圈末回收后 fresh spawn）。会话一旦进入拆除阶段（窗口站、桌面堆、
CSRSS 被拆），CreateProcess 明明成功、子进程却在 user32/gdi32 的 DLL 初始化
阶段失败（STATUS_DLL_INIT_FAILED），系统于是弹出阻塞关机的错误对话框。

本套件锁定修复的行为契约（全部在公开 seam 上断言）：
1. 会话结束 latch 置位后，ffmpeg spawn 点（start / reader / 首帧解码 /
   元数据探测 / exe 探测）一律拒绝派生，且**动画启动被拒**沿用既有 False
   契约（窗口层降级语义已由 test_switch_start_failure_window 锁定）；
2. 未置位时行为不变（对照组用例）；
3. ``MovieLibrary.stop_all_clips`` 收口全部已建 clip（会话结束时主动停播）；
4. Windows 原生探测器：WM_QUERYENDSESSION / WM_ENDSESSION / Qt 的
   commitDataRequest / aboutToQuit 都能 arm 该 latch，且幂等、不误报；
5. AppShell 编排：会话结束时逐窗 match_shutdown（停止动画/timer/预热）；
6. **静默静态门**：webm_clip 每一处 ffmpeg spawn 调用点的作用域内都必须有
   ``session_ending()`` 门——未来新增 spawn 路径时该用例直接变红。
"""
from __future__ import annotations

import ctypes
import re
import threading
from ctypes import wintypes
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from pet import session_watcher as session_watcher_mod
from pet import webm_clip as webm_clip_mod
from pet.session_watcher import SessionWatcher
from pet.webm_clip import WebMClip

WEBM_CLIP_SRC = Path(webm_clip_mod.__file__).read_text(encoding="utf-8")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reset_session_ending():
    """会话结束 latch 是进程级：任一用例置位后必须复位，否则后续用例静默不起 reader。"""
    webm_clip_mod._reset_session_ending_for_tests()
    yield
    webm_clip_mod._reset_session_ending_for_tests()


def _make_clip(tmp_path, frame_count: int = 3) -> WebMClip:
    """最小可播 clip：元数据直接给足，_ensure_meta 短路（不触真实 ffmpeg）。"""
    clip = WebMClip(str(tmp_path / "x.webm"))
    clip._w = 2
    clip._h = 2
    clip._fps = 24.0
    clip._duration = frame_count / 24.0
    clip._frame_count = frame_count
    clip._frame_count_exact = True
    return clip


class _SpawnSpy:
    """记录 read_frames / count_frames_and_secs 的每次调用（= 一次真实 spawn）。"""

    def __init__(self) -> None:
        self.read_frames_calls: list = []
        self.count_calls: list = []
        self._lock = threading.Lock()

    def read_frames(self, *args, **kwargs):
        with self._lock:
            self.read_frames_calls.append((args, kwargs))
        return None  # 被调即失败：spawn 已发生，返回值无关紧要

    def count_frames_and_secs(self, *args, **kwargs):
        with self._lock:
            self.count_calls.append((args, kwargs))
        return (0, 0)

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", self.read_frames)
        monkeypatch.setattr(
            webm_clip_mod.imageio_ffmpeg, "count_frames_and_secs", self.count_frames_and_secs,
        )

    @property
    def total(self) -> int:
        return len(self.read_frames_calls) + len(self.count_calls)


# ---------------------------------------------------------------------------
# 1) 会话结束后的 ffmpeg spawn 硬门
# ---------------------------------------------------------------------------

def test_start_and_reader_refuse_to_spawn_when_session_ending(tmp_path, monkeypatch):
    """会话结束后 start() 走既有 False 契约，reader 线程也绝不拉起 ffmpeg。"""
    spy = _SpawnSpy()
    spy.install(monkeypatch)
    clip = _make_clip(tmp_path)

    webm_clip_mod.set_session_ending(True)

    assert clip.start() is False, "会话结束后的启动必须被拒（窗口层按 False 降级）"
    assert spy.total == 0, "会话结束后不得派生任何 ffmpeg 进程"

    # 即使 reader 被直接调起（迟到事件/其它入口）也不许 spawn
    clip._reader(threading.Event(), clip._generation)
    assert spy.read_frames_calls == [], "reader 线程也必须拒绝拉起 ffmpeg"
    clip.cleanup()


def test_warm_and_meta_probe_refuse_to_spawn_when_session_ending(tmp_path, monkeypatch):
    """首帧预热与元数据探测（count_frames_and_secs）同样不再派生进程。"""
    spy = _SpawnSpy()
    spy.install(monkeypatch)
    clip = _make_clip(tmp_path, frame_count=0)
    clip._duration = 0.0  # 迫使 _ensure_meta 走真实探测路径

    webm_clip_mod.set_session_ending(True)

    clip.warm_first_frame()
    assert clip._decode_first_qimage() is None
    clip._ensure_meta()
    assert spy.total == 0, "预热/探测路径必须在会话结束后静默放弃"
    clip.cleanup()


def test_ffmpeg_exe_probe_refuses_to_spawn_when_session_ending(monkeypatch):
    """`ffmpeg -version` 探测（get_ffmpeg_exe）是容易被漏掉的 spawn 路径，同样要拦。

    注意两件事，否则用例会假绿：
    - `imageio_ffmpeg.get_ffmpeg_exe` 自带 lru_cache：一旦被别的用例预热过就
      不再真正探测，断言必须打在**实际执行探测**的 `_utils._get_ffmpeg_exe` 上，
      并清掉外层缓存；
    - 未置位的对照组必须真跑一次探测，否则「没被调用」可能只是缓存命中。
    """
    import imageio_ffmpeg
    import imageio_ffmpeg._utils as ff_utils

    probed: list = []
    monkeypatch.setattr(ff_utils, "_get_ffmpeg_exe", lambda: probed.append(1))
    # imageio-ffmpeg 0.6.0 的 get_ffmpeg_exe 不带 lru_cache；带缓存的版本要清掉，
    # 否则「没被调用」可能只是缓存命中（假绿）。
    cache_clear = getattr(imageio_ffmpeg.get_ffmpeg_exe, "cache_clear", None)
    try:
        if callable(cache_clear):
            cache_clear()
        webm_clip_mod.set_session_ending(True)
        webm_clip_mod._ensure_ffmpeg_exe()
        assert probed == [], "会话结束后不得跑 ffmpeg exe 探测（= 一次 spawn）"

        webm_clip_mod.set_session_ending(False)
        webm_clip_mod._ensure_ffmpeg_exe()
        assert probed == [1], "对照组：未置位时必须照常探测（证明门是唯一差异）"
    finally:
        if callable(cache_clear):
            cache_clear()


def test_control_group_spawns_normally_without_session_end(tmp_path, monkeypatch):
    """对照组：未收到会话结束通知时，同一路径照常拉起（证明门是唯一差异）。

    与 test_webm_reader_lifecycle 同款：reader 线程会经 `_ensure_ffmpeg_exe`
    跑真实的 `ffmpeg -version` 探测，测试里替换为空操作（探测不是本用例的
    被测对象，且受限沙箱下子进程 STDIO 捕获可能不稳定）。
    """
    spy = _SpawnSpy()
    spy.install(monkeypatch)
    monkeypatch.setattr(webm_clip_mod, "_ensure_ffmpeg_exe", lambda: None)
    clip = _make_clip(tmp_path, frame_count=0)
    clip._duration = 0.0

    assert webm_clip_mod.session_ending() is False

    assert clip.start() is True
    assert spy.read_frames_calls, "正常运行期必须照常拉起 reader（对照组）"
    clip._ensure_meta()
    assert spy.count_calls, "正常运行期元数据探测照常（对照组）"
    clip.cleanup()


def test_session_ending_latch_is_idempotent_and_resettable():
    assert webm_clip_mod.session_ending() is False
    webm_clip_mod.set_session_ending(True)
    webm_clip_mod.set_session_ending(True)  # 幂等：重复置位不抛、不翻转
    assert webm_clip_mod.session_ending() is True
    webm_clip_mod.set_session_ending(False)
    assert webm_clip_mod.session_ending() is False


# ---------------------------------------------------------------------------
# 2) MovieLibrary.stop_all_clips：会话结束时主动停掉现有 reader
# ---------------------------------------------------------------------------

class _RecordingClip:
    def __init__(self, fail: bool = False) -> None:
        self.stops = 0
        self._fail = fail

    def stop(self) -> None:
        self.stops += 1
        if self._fail:
            raise RuntimeError("半销毁 clip")


class _LibraryStub:
    """只带 stop_all_clips 依赖面（_movies）的最小替身。"""

    def __init__(self, clips: dict) -> None:
        self._movies = clips


def test_library_stop_all_clips_stops_every_clip():
    from pet.library import MovieLibrary

    clips = {"a": _RecordingClip(), "b": _RecordingClip(), "c": _RecordingClip()}
    MovieLibrary.stop_all_clips(_LibraryStub(clips))

    assert [c.stops for c in clips.values()] == [1, 1, 1]


def test_library_stop_all_clips_survives_single_clip_failure():
    """单个 clip 抛异常不得影响其余 clip 收口（会话结束路径必须尽力而为）。"""
    from pet.library import MovieLibrary

    clips = {"a": _RecordingClip(), "b": _RecordingClip(fail=True), "c": _RecordingClip()}
    MovieLibrary.stop_all_clips(_LibraryStub(clips))

    assert clips["a"].stops == 1
    assert clips["b"].stops == 1
    assert clips["c"].stops == 1


def test_library_warm_predicted_refused_when_session_ending(monkeypatch):
    """预测式预热（每几秒一次的短命 ffmpeg）在会话结束后必须停手。"""
    from pet import catalog
    from pet.library import MovieLibrary

    asset_dir = catalog.resolve_character_video_dir("shenshen")
    if not asset_dir.is_dir():
        pytest.skip(f"角色素材目录不存在: {asset_dir}")

    lib = MovieLibrary(character_id="shenshen", asset_dir=asset_dir)
    try:
        monkeypatch.setattr(lib, "_prewarm_enabled", True)
        warmed: list = []
        monkeypatch.setattr(lib, "movie", lambda name: warmed.append(name))
        webm_clip_mod.set_session_ending(True)

        lib.warm_predicted("随便一个动作")

        assert warmed == [], "会话结束后不得创建 clip / 起预热线程"
    finally:
        lib.shutdown()


# ---------------------------------------------------------------------------
# 3) Windows 会话结束探测（原生消息层，真实 MSG 缓冲区）
# ---------------------------------------------------------------------------

class _FakeApp(QObject):
    """带 Qt 会话信号的假 app：只暴露探测器真正使用的接口。"""

    commitDataRequest = Signal()
    aboutToQuit = Signal()


@pytest.fixture
def fake_app():
    obj = _FakeApp()
    yield obj
    obj.deleteLater()


def _msg_pointer(message: int):
    """构造真实 Windows MSG 结构并返回 (地址, 结构体)。

    结构体由调用方保活到过滤器返回为止（否则指针悬空，测试会假绿）。
    """
    msg = wintypes.MSG()
    msg.hwnd = 0
    msg.message = message
    msg.wParam = 0
    msg.lParam = 0
    msg.time = 0
    msg.pt.x = 0
    msg.pt.y = 0
    return ctypes.addressof(msg), msg


@pytest.mark.parametrize("message", [
    session_watcher_mod.WM_QUERYENDSESSION,
    session_watcher_mod.WM_ENDSESSION,
])
def test_native_filter_arms_on_windows_session_end_messages(fake_app, message):
    ends: list = []
    watcher = SessionWatcher(app=fake_app, on_session_end=lambda: ends.append(1),
                             install_native_filter=False)

    addr, keepalive = _msg_pointer(message)
    result = watcher.nativeEventFilter(0, addr)

    assert result == (False, 0), "不拦截 Qt 默认处理（绝不 veto 关机）"
    assert watcher.armed is True, "Windows 会话结束消息必须置位 latch"
    assert webm_clip_mod.session_ending() is True
    assert ends == [1]
    assert keepalive is not None  # 结构体存活到断言结束


def test_native_filter_ignores_unrelated_messages(fake_app):
    watcher = SessionWatcher(app=fake_app, on_session_end=lambda: None,
                             install_native_filter=False)

    addr, keepalive = _msg_pointer(0x000F)  # WM_PAINT：与关机无关
    assert watcher.nativeEventFilter(0, addr) == (False, 0)
    assert watcher.armed is False
    assert webm_clip_mod.session_ending() is False
    assert keepalive is not None


def test_native_filter_rejects_bogus_pointer_without_raising(fake_app):
    """指针不可解析时必须静默兜底（消息布局随 Qt 版本变化，绝不能抛）。

    注：对**任意**地址解引用是未定义行为（Windows 上表现为访问违规，CPython
    无法用 except 捕获），因此这里的「野指针」用非指针入参（Qt 传参形态变化
    时的典型情况）+ 空指针覆盖；真正的野指针不构造。
    """
    watcher = SessionWatcher(app=fake_app, on_session_end=lambda: None,
                             install_native_filter=False)
    assert watcher.nativeEventFilter(0, None) == (False, 0)
    assert watcher.nativeEventFilter(0, 0) == (False, 0)
    assert watcher.nativeEventFilter(0, "not-a-pointer") == (False, 0)
    assert watcher.armed is False


def test_session_end_reason_parses_only_session_messages(fake_app):
    """纯函数级：只认 WM_QUERYENDSESSION / WM_ENDSESSION，其它消息一律 None。"""
    addr, keepalive = _msg_pointer(session_watcher_mod.WM_QUERYENDSESSION)
    assert session_watcher_mod.session_end_reason(addr) == "native_query_end_session"

    addr_end, keepalive_end = _msg_pointer(session_watcher_mod.WM_ENDSESSION)
    assert session_watcher_mod.session_end_reason(addr_end) == "native_end_session"

    addr_paint, keepalive_paint = _msg_pointer(0x000F)  # WM_PAINT
    assert session_watcher_mod.session_end_reason(addr_paint) is None

    assert session_watcher_mod.session_end_reason(None) is None
    assert keepalive is not None and keepalive_end is not None and keepalive_paint is not None


def test_arming_is_idempotent_and_logs_once(fake_app, caplog):
    """重复的会话结束信号（QUERYENDSESSION 之后又有 ENDSESSION 等）只收口一次。"""
    import logging

    ends: list = []
    watcher = SessionWatcher(app=fake_app, on_session_end=lambda: ends.append(1),
                             install_native_filter=False)

    with caplog.at_level(logging.INFO, logger="pet.session_watcher"):
        watcher.arm("query_end_session")
        watcher.arm("end_session")
        addr, keepalive = _msg_pointer(session_watcher_mod.WM_QUERYENDSESSION)
        watcher.nativeEventFilter(0, addr)

    assert ends == [1], "安全网回调只许跑一次"
    assert sum("会话结束" in r.getMessage() for r in caplog.records) == 1, \
        "会话结束必须在日志留痕且不重复（issue #111 建议 4：关机阶段可观测）"
    assert keepalive is not None


def test_safety_net_callback_failure_does_not_disarm_the_gate(fake_app):
    """数据先落、回调后跑：安全网回调抛异常不得让 ffmpeg 门失守。"""
    def _boom() -> None:
        raise RuntimeError("安全网回调失败")

    watcher = SessionWatcher(app=fake_app, on_session_end=_boom,
                             install_native_filter=False)
    watcher.arm("test")

    assert watcher.armed is True
    assert webm_clip_mod.session_ending() is True, "回调异常也不得让 spawn 门失守"


def test_qt_session_signals_arm_through_real_signal_connections(fake_app):
    """Qt 会话框架信号（commitDataRequest/aboutToQuit）是次生兜底路径，必须接通。

    每条信号用独立 watcher：已置位的 watcher 天然不再响应后续信号（latch 语义），
    复用同一实例的第二个断言只会测到 latch、测不到接线。
    """
    commit_watcher = SessionWatcher(app=fake_app, on_session_end=lambda: None,
                                    install_native_filter=False)
    commit_watcher.connect_app_signals()
    fake_app.commitDataRequest.emit()
    assert webm_clip_mod.session_ending() is True, "commitDataRequest 必须在 spawn 门前置位"

    webm_clip_mod.set_session_ending(False)
    quit_watcher = SessionWatcher(app=fake_app, on_session_end=lambda: None,
                                  install_native_filter=False)
    quit_watcher.connect_app_signals()
    fake_app.aboutToQuit.emit()
    assert webm_clip_mod.session_ending() is True, "正常退出也必须在 spawn 门前置位"


def test_arm_normalises_non_string_reason_for_logs(fake_app, caplog):
    """日志标签必须可读：Qt 的 commitDataRequest 会传 QSessionManager 对象。

    回归背景：打包产物实测日志里出现「收到会话结束通知（<PySide6.QtGui.
    QSessionManager(0x...) at 0x...>）」——关机阶段日志是唯一排查入口，
    绝不能印对象地址。
    """
    import logging

    watcher = SessionWatcher(app=fake_app, on_session_end=lambda: None,
                             install_native_filter=False)
    with caplog.at_level(logging.INFO, logger="pet.session_watcher"):
        watcher.arm(object())  # 模拟 Qt 传进来的 QSessionManager 实例

    messages = [r.getMessage() for r in caplog.records]
    assert any("会话结束通知（unknown）" in m for m in messages), messages
    assert watcher.armed is True


def test_install_without_qapplication_is_a_noop():
    """无 QApplication（测试收尾/无 GUI 变体）时 install 不得抛。"""
    watcher = SessionWatcher(app=None, on_session_end=lambda: None)
    watcher.install()
    assert watcher.armed is False


# ---------------------------------------------------------------------------
# 4) AppShell 编排：逐窗 match_shutdown
# ---------------------------------------------------------------------------

class _FakeWindow:
    """窗口替身：只暴露会话结束收口的公开面（含素材库）。"""

    def __init__(self, lib=None) -> None:
        self.shutdowns = 0
        self.lib = lib

    def match_shutdown(self) -> None:
        self.shutdowns += 1


class _RecordingLibrary:
    """素材库替身：记录 stop_all_clips 调用次数。"""

    def __init__(self, fail: bool = False) -> None:
        self.stops = 0
        self._fail = fail

    def stop_all_clips(self) -> None:
        self.stops += 1
        if self._fail:
            raise RuntimeError("素材库半销毁")


def _shell_with_window(app, tmp_path, win):
    """轻量 AppShell（test_feature_gating 同款 enable_chat=False）+ 主窗替身。

    只替换 ``_instances[0].win``（PetInstance 内部构造不依赖），避免给
    ``_LIVE_SHELLS`` 收口路径塞入非 PetInstance 的替身。
    """
    from pet.app import AppShell
    from pet.config import Config

    shell = AppShell(app, Config(tmp_path), enable_chat=False)
    shell._instances[0].win = win
    return shell


def test_app_shell_on_session_end_freezes_window_and_arms_gate(app, tmp_path):
    from pet.app import AppShell

    lib = _RecordingLibrary()
    win = _FakeWindow(lib=lib)
    shell = _shell_with_window(app, tmp_path, win)
    try:
        shell._on_session_end()

        assert win.shutdowns == 1, "窗口必须收口（停 reader/timer/预热）"
        assert lib.stops == 1, "素材库里全部已建 clip 必须一并 stop（含圈末驻留的）"
        assert webm_clip_mod.session_ending() is True, "spawn 闸门必须已置位"
    finally:
        AppShell._shutdown_live_for_tests()


def test_app_shell_session_end_is_idempotent(app, tmp_path):
    from pet.app import AppShell

    lib = _RecordingLibrary()
    win = _FakeWindow(lib=lib)
    shell = _shell_with_window(app, tmp_path, win)
    try:
        shell._on_session_end()
        shell._on_session_end()
        assert win.shutdowns == 1, "重复会话结束信号只收口一次"
        assert lib.stops == 1
    finally:
        AppShell._shutdown_live_for_tests()


def test_app_shell_session_end_survives_window_failure(app, tmp_path):
    """单窗收口抛异常不得阻断其余窗、素材库收口与闸门置位（关机路径尽力而为）。"""
    from pet.app import AppShell

    class _BrokenWindow(_FakeWindow):
        def match_shutdown(self) -> None:
            super().match_shutdown()
            raise RuntimeError("半销毁窗口")

    lib = _RecordingLibrary(fail=True)
    win = _BrokenWindow(lib=lib)
    shell = _shell_with_window(app, tmp_path, win)
    try:
        shell._on_session_end()  # 不得抛出

        assert win.shutdowns == 1
        assert lib.stops == 1
        assert webm_clip_mod.session_ending() is True
    finally:
        AppShell._shutdown_live_for_tests()


def test_app_shell_window_without_library_still_freezes(app, tmp_path):
    """窗口/素材库缺失（半销毁、未创建）不得阻断会话结束收口。"""
    from pet.app import AppShell

    win = _FakeWindow(lib=None)
    shell = _shell_with_window(app, tmp_path, win)
    try:
        shell._on_session_end()
        assert win.shutdowns == 1
        assert webm_clip_mod.session_ending() is True
    finally:
        AppShell._shutdown_live_for_tests()


def test_app_shell_start_installs_session_watcher(app, tmp_path, monkeypatch):
    """启动路径必须安装探测器并强引用保活（否则关机窗口期无人置位闸门）。"""
    from pet import app as app_mod
    from pet.app import AppShell
    from pet.config import Config

    installed: list = []

    def _fake_install(*, app=None, on_session_end=None):
        installed.append((app, on_session_end))
        return session_watcher_mod.SessionWatcher(
            app=app, on_session_end=on_session_end, install_native_filter=False,
        )

    monkeypatch.setattr(app_mod, "install_session_watcher", _fake_install)
    shell = AppShell(app, Config(tmp_path), enable_chat=False)
    try:
        shell._install_session_watcher()
        shell._install_session_watcher()  # 幂等：不重复安装

        assert len(installed) == 1, "探测器只许安装一次"
        assert isinstance(shell._session_watcher, session_watcher_mod.SessionWatcher)
        assert installed[0][1] == shell._on_session_end, "安全网回调必须接线到 AppShell"
    finally:
        AppShell._shutdown_live_for_tests()


# ---------------------------------------------------------------------------
# 5) 静默静态门：webm_clip 的每一处 spawn 调用点都必须在 session_ending 门内
# ---------------------------------------------------------------------------

_SPAWN_CALL = re.compile(r"imageio_ffmpeg\.(read_frames|count_frames_and_secs)\(")


def _code_line_numbers(source: str) -> set:
    """返回**真实代码行**号（1-based）：排除注释与所有字符串字面量。

    静态门必须只认代码：模块文档字符串里也写着 ``read_frames(...)`` /
    ``get_ffmpeg_exe()`` 这类说明，把它们当成 spawn 点会制造假红。
    """
    import io
    import tokenize

    lines: set = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, getattr(tokenize, "NL", -1)):
            continue
        if tok.type == tokenize.STRING and tok.start[0] != tok.end[0]:
            continue  # 跨行字符串（文档字符串）：整段不算代码
        for row in range(tok.start[0], tok.end[0] + 1):
            lines.add(row)
    return lines


def _enclosing_scope(lines: list, index: int) -> str:
    """返回调用点所在的最内层函数作用域源码（def 首行到调用点）。

    webm_clip 的 spawn 点都在方法体/模块函数体内，按缩进回退到最近的 ``def``
    足以切出作用域（无需 AST）。
    """
    for i in range(index, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("def "):
            return "\n".join(lines[i:index + 1])
    return "\n".join(lines[:index + 1])


def test_every_ffmpeg_spawn_site_is_gated_by_session_ending():
    """新增 ffmpeg spawn 路径时必须同时加门，否则本用例变红（issue #111 护栏）。"""
    lines = WEBM_CLIP_SRC.splitlines()
    code_lines = _code_line_numbers(WEBM_CLIP_SRC)
    offenders = []
    sites = 0
    for i, line in enumerate(lines):
        if (i + 1) not in code_lines:
            continue  # 注释/文档字符串：不是 spawn 点
        if not _SPAWN_CALL.search(line):
            continue
        sites += 1
        if "session_ending()" not in _enclosing_scope(lines, i):
            offenders.append(f"webm_clip.py:{i + 1}: {line.strip()}")

    assert sites == 3, (
        f"ffmpeg spawn 调用点数从 3 变成 {sites}——清单已变，护栏需同步核对："
        "新增/删除 spawn 路径后必须回看本用例与 session_ending 门"
    )
    assert not offenders, (
        "以下 ffmpeg spawn 调用点缺少 session_ending() 门（关机时会派生进程、"
        "触发 0xc0000142 阻塞关机）：\n" + "\n".join(offenders)
    )


def test_ffmpeg_exe_probe_is_gated():
    """`ffmpeg -version` 探测（get_ffmpeg_exe）是容易被漏掉的 spawn 路径。"""
    lines = WEBM_CLIP_SRC.splitlines()
    code_lines = _code_line_numbers(WEBM_CLIP_SRC)
    call_sites = 0
    for i, line in enumerate(lines):
        if (i + 1) not in code_lines or "get_ffmpeg_exe(" not in line:
            continue  # 注释/文档字符串里的说明不算 spawn 点
        call_sites += 1
        assert "session_ending()" in _enclosing_scope(lines, i), (
            f"webm_clip.py:{i + 1}: exe 探测缺少 session_ending() 门"
        )
    assert call_sites == 1, f"get_ffmpeg_exe 调用点数量异常：{call_sites}"
