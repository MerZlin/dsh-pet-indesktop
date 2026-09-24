"""Fullscreen watcher / cursor-passthrough helpers (host-based).

PetWindow retains thin compatibility methods passing itself as host; the
watcher thread boundary stays on the window side through the delegating
methods (thread target = bound delegation), so existing callers and test
patches keep working unchanged.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import shiboken6

_fs_monotonic = time.monotonic


def start_fs_watch(host) -> None:
    """启动全屏监视线程（幂等）。"""
    if host._single_process_spawn:  # 批5.2a：flag 开由共享 watcher 接管
        return
    if host._fs_thread is not None and host._fs_thread.is_alive():
        return
    host._fs_stop.clear()
    host._fs_thread = threading.Thread(target=host._fs_watch_loop, daemon=True, name="pet-fs-watch")
    host._fs_thread.start()
    logging.info("全屏监视线程已启动")


def stop_fs_watch(host) -> None:
    """停止全屏监视线程，并等待其退出（线程不会触碰 Qt UI）。"""
    host._fs_stop.set()
    thread = host._fs_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=0.2)
        if not thread.is_alive():
            host._fs_thread = None


def fs_watch_loop(host, *, monotonic=None) -> None:
    """后台轮询光标与前台窗口，分别使用 20Hz 与 1Hz 节拍。

    ``monotonic`` 是兼容 seam：PetWindow 委托注入 pet.window.time.monotonic
    （测试 patch 该命名空间的时钟），缺省回退本模块标准库时钟。
    """
    clock = monotonic if monotonic is not None else _fs_monotonic
    stop = host._fs_stop
    # Phase 1：避免纯桌宠启动即加载 PIL；该线程真正需要检测光标时才导入。
    from . import vision as vision_mod

    polls = 0
    consecutive_errors = 0
    next_fullscreen = clock() + 1.0
    while not stop.wait(0.05):
        # destroyed 连接使用的是同一个纯 Python Event；在访问 QObject
        # 包装器前再次检查，覆盖 wait 返回与 Qt 销毁之间的竞态窗口。
        if stop.is_set():
            return
        if shiboken6.isValid(host) is False:
            return
        if host._cursor_hidden_passthrough_enabled():
            try:
                visibility = vision_mod.get_cursor_visibility()
                if shiboken6.isValid(host) is False:
                    return
                host.cursor_visibility_changed.emit(visibility)
                consecutive_errors = 0
            except (RuntimeError, AttributeError) as exc:
                if shiboken6.isValid(host) is False:
                    return
                consecutive_errors += 1
                backoff = 1.0 if consecutive_errors == 1 else (2.0 if consecutive_errors == 2 else 5.0)
                logging.debug("光标状态检测瞬时异常 (%s), 退避 %ss 后重试", exc, backoff)
                if stop.wait(backoff):
                    return
            except Exception:
                try:
                    if shiboken6.isValid(host) is False:
                        return
                    host.cursor_visibility_changed.emit("UNKNOWN")
                except (RuntimeError, AttributeError) as exc:
                    if shiboken6.isValid(host) is False:
                        return
                    consecutive_errors += 1
                    backoff = 1.0 if consecutive_errors == 1 else (2.0 if consecutive_errors == 2 else 5.0)
                    logging.debug("光标状态降级发射瞬时异常 (%s), 退避 %ss 后重试", exc, backoff)
                    if stop.wait(backoff):
                        return
        now = clock()
        if not host.auto_hide_fullscreen or now < next_fullscreen:
            continue
        next_fullscreen = now + 1.0
        try:
            hit, detail = host._fg_fullscreen_probe()
        except Exception:
            logging.exception("全屏检测异常")
            continue
        polls += 1
        if hit != host._fs_last:
            host._fs_last = hit
            logging.info("全屏检测变化 hit=%s (%s)", hit, detail)
            if shiboken6.isValid(host) is False:
                return
            host.fullscreen_changed.emit(hit)
        elif polls % 15 == 0:
            logging.info("全屏检测心跳 hit=%s %s", hit, detail)


def cursor_hidden_passthrough_enabled(host) -> bool:
    return host._cursor_hidden_passthrough


def watch_required(host) -> bool:
    # Offscreen Qt has no native foreground/fullscreen surface.  Avoid
    # starting a Windows watcher against a headless QObject, where native
    # teardown can race the polling thread during test/process shutdown.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return False
    return os.name == "nt" and (host.auto_hide_fullscreen or host._cursor_hidden_passthrough_enabled())


def cursor_transition_blocked(host) -> bool:
    return host._press_global is not None or host._dragging or host._interaction_state in ("DRAGGING", "SLINGSHOT_AIMING", "PRESS_CANDIDATE")


def on_cursor_visibility_changed(host, visibility: str, *, monotonic=None) -> None:
    """光标可见性状态迁移（兼容 seam：``monotonic`` 由 PetWindow 委托注入
    pet.window.time.monotonic，缺省回退标准库时钟）。"""
    if not host._cursor_hidden_passthrough_enabled():
        return
    clock = monotonic if monotonic is not None else time.monotonic
    now = clock()
    host._cursor_visibility = visibility
    if visibility == "HIDDEN":
        if host._cursor_hidden_since is None:
            host._cursor_hidden_since = now
        if now - host._cursor_hidden_since >= 0.2 and not host._cursor_transition_blocked():
            host._auto_cursor_hidden = True
            host._apply_effective_mouse_through()
    elif visibility == "SHOWING":
        host._cursor_hidden_since = None
        if host._cursor_transition_blocked():
            host._cursor_restore_pending = True
        else:
            host._cursor_restore_pending = False
            host._auto_cursor_hidden = False
            host._apply_effective_mouse_through()
    elif visibility == "SUPPRESSED":
        host._cursor_hidden_since = None
        logging.debug("系统光标被触摸/笔输入抑制，保持当前自动穿透状态")


def on_fullscreen_changed(host, hit: bool) -> None:
    """主线程：全屏出现 → 隐藏桌宠；全屏退出 → 恢复。"""
    logging.info("全屏状态变化 hit=%s auto_hidden=%s visible=%s", hit, host._auto_hidden, host.isVisible())
    if hit:
        if not host._auto_hidden and host.isVisible():
            host._auto_hidden = True
            host._speech_bubble.hide()
            host.hide(notify=False)  # 自动隐藏是内部语义，不弹"桌宠已隐藏"托盘通知
    elif host._auto_hidden:
        host._auto_hidden = False
        host.show()


def set_auto_hide_fullscreen(host, on: bool) -> None:
    """全屏自动隐藏开关（供设置/菜单调用）。"""
    host.auto_hide_fullscreen = bool(on)
    host.cfg.set("auto_hide_fullscreen", host.auto_hide_fullscreen)
    host.cfg.save()
    if host._watch_required():
        host._start_fs_watch()
    else:
        host._stop_fs_watch()
    if not host.auto_hide_fullscreen and host._auto_hidden:
        host._auto_hidden = False
        host.show()


def set_cursor_hidden_passthrough(host, on: bool) -> None:
    """切换光标自动穿透，不改变用户手动穿透意图。"""
    on = bool(on)
    host._cursor_hidden_passthrough = on
    host.cfg.set("cursor_hidden_passthrough", on)
    host.cfg.save()
    host._cursor_hidden_since = None
    host._cursor_restore_pending = False
    if not on:
        host._auto_cursor_hidden = False
        host._apply_effective_mouse_through()
    if host._watch_required():
        host._start_fs_watch()
    elif not host._auto_hidden:
        host._stop_fs_watch()


def set_stream_capture_mode(host, on: bool) -> None:
    """直播捕获兼容模式：Tool → 普通顶层窗口 + 标题。

    直播姬/OBS 的窗口捕获会过滤 Tool 窗口（WS_EX_TOOLWINDOW），
    开启后改为普通窗口并设置可见标题，捕获列表即可看到桌宠；
    代价是任务栏出现图标。setWindowFlags 会重建原生窗口，随后
    showEvent 会自动重新应用置顶。
    """
    from .window import STREAM_CAPTURE_TITLE, build_window_flags

    on = bool(on)
    if on == host._stream_capture_mode:
        return
    host._stream_capture_mode = on
    host.cfg.set("stream_capture_mode", on)
    host.cfg.save()
    was_visible = host.isVisible()  # setWindowFlags 重建原生窗口会先隐藏
    host.setWindowFlags(build_window_flags(host.cfg, host.mouse_through, on))
    host.setWindowTitle(STREAM_CAPTURE_TITLE if on else "")
    if was_visible:
        host.show()  # 只在原本可见时恢复：手动/自动隐藏的桌宠不被意外唤出
    host._speech_bubble.set_capture_compat(on, host=host)
    if getattr(host, "_quick_chat_capture_widget", None) is not None and shiboken6.isValid(host._quick_chat_capture_widget):
        host._quick_chat_capture_widget.set_capture_compat(on, host=host)
    if not on:
        host.set_capture_headroom(0)


def set_quick_chat_capture_widget(host, widget) -> None:
    """注册/清空快速对话气泡的捕获子控件引用并同步当前捕获模式。"""
    host._quick_chat_capture_widget = widget
    if widget is not None and callable(getattr(widget, "set_capture_compat", None)):
        widget.set_capture_compat(host._stream_capture_mode, host=host)
