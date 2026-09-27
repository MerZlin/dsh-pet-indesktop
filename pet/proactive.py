# -*- coding: utf-8 -*-
"""主动识屏与关怀机制 — 纯函数区 + ProactiveScreenWatcher。

批6-1 拆分（纯搬移，逻辑/默认值/时序零改动）：
- pet/proactive_memory.py  — ProactiveMemory（短期陪伴记忆）；
- pet/proactive_limiter.py — ProactiveLimiter + effective_proactive_config 及其依赖。

本文件保留：纯逻辑算法（白名单匹配、dHash、停留/闲置/观察判定）与
ProactiveScreenWatcher（Qt 观察器 / 截图 worker / provider 解析 / 请求管线），
并对拆分组件做 re-export 以维持既有 `from pet.proactive import ...` 兼容。
"""

from __future__ import annotations

import fnmatch
import sys
import time
from collections.abc import Mapping
from typing import Any

# 拆分后组件 re-export（维持既有 import 兼容；外部调用点本批不改）
from .proactive_limiter import (
    DEFAULT_PROACTIVE_CONFIG,
    PRESET_DEFAULTS,
    ProactiveLimiter,
    effective_proactive_config,
)
from .proactive_memory import ProactiveMemory

__all__ = [
    "DEFAULT_PROACTIVE_CONFIG",
    "PRESET_DEFAULTS",
    "match_process_whitelist",
    "image_dhash",
    "hamming_distance",
    "classify_activity",
    "build_memory_context",
    "build_sync_marker",
    "dwell_satisfied",
    "idle_satisfied",
    "should_watch",
    "effective_proactive_config",
    "ProactiveMemory",
    "ProactiveLimiter",
    "ProactiveScreenWatcher",
]


def match_process_whitelist(rules: list[str] | None, process_name: str | None, window_title: str | None) -> bool:
    """匹配前台窗口是否在白名单中。

    规则：
    - rules 为空返回 False；
    - 大小写不敏感；支持 *、? 通配（fnmatch 语义）；
    - 无前缀规则**仅匹配进程名**（隐私边界：标题可能含文档名等敏感信息，
      想按标题匹配必须显式写 title: 前缀）；
    - 'title:' 前缀规则仅匹配窗口标题（匹配时不包含 'title:' 前缀）；
    - 规则/进程名/标题可为空串（空规则忽略）；
    - 任一规则命中即返回 True。
    """
    if not rules:
        return False

    proc_lower = (process_name or "").strip().lower()
    title_lower = (window_title or "").strip().lower()

    for rule in rules:
        if not isinstance(rule, str):
            continue
        rule_clean = rule.strip()
        if not rule_clean:
            continue

        rule_lower = rule_clean.lower()
        if rule_lower.startswith("title:"):
            pattern = rule_lower[6:].strip()
            if pattern and title_lower and fnmatch.fnmatch(title_lower, pattern):
                return True
        else:
            if proc_lower and fnmatch.fnmatch(proc_lower, rule_lower):
                return True

    return False


def image_dhash(img: Any) -> int:
    """计算图像的 64 位 dHash（差异哈希）。

    任意通道与尺寸输入先转为灰度 'L'，缩放到 (9, 8)，
    逐行相邻像素比较（left > right）生成 64 位整数。
    """
    # 转换为灰度图像并缩放到 9x8（宽 9，高 8）
    # 显式使用 NEAREST 采样，兼容各 Pillow 版本
    try:
        from PIL import Image

        resample = getattr(Image, "Resampling", Image).NEAREST
    except Exception:
        resample = 0
    gray = img.convert("L").resize((9, 8), resample)
    # 获取展平后的像素数据（兼容旧版 getdata 与新版 get_flattened_data）
    if hasattr(gray, "get_flattened_data"):
        pixels = list(gray.get_flattened_data())
    elif hasattr(gray, "getdata"):
        pixels = list(gray.getdata())
    else:
        pixels = list(gray.tobytes())

    diff = 0
    width = 9
    for row in range(8):
        row_offset = row * width
        for col in range(8):
            left = pixels[row_offset + col]
            right = pixels[row_offset + col + 1]
            diff = (diff << 1) | (1 if left > right else 0)

    return diff


def hamming_distance(h1: int, h2: int) -> int:
    """计算两个哈希值之间的 Hamming 距离（0~64）。"""
    return bin(h1 ^ h2).count("1")


def classify_activity(process: str | None, title: str | None) -> str:
    """本地关键词活动分类（零网络、零模型调用）。"""
    proc = (process or "").strip().lower()
    tit = (title or "").strip().lower()

    if not proc and not tit:
        return "桌面上"

    # 写代码
    code_keywords = ("code.exe", "vscode", "devenv.exe", "pycharm", "idea64.exe", "clion", "webstorm", "sublime", "cursor.exe", "nvim", "vim")
    if any(k in proc for k in code_keywords) or any(k in tit for k in ("visual studio", "sublime text", "写代码")):
        return "写代码"

    # 看视频
    video_titles = ("bilibili", "哔哩哔哩", "youtube", "爱奇艺", "iqiyi", "腾讯视频", "youku", "优酷", "netflix", "potplayer", "vlc")
    video_procs = ("potplayer64.exe", "vlc.exe", "bilibili.exe")
    if any(k in proc for k in video_procs) or any(k in tit for k in video_titles):
        return "看视频"

    # 办公或看文档
    doc_keywords = ("acrobat.exe", "acrodist.exe", "winword.exe", "excel.exe", "powerpnt.exe", "wps.exe", "wpp.exe", "et.exe", "foxitreader.exe")
    if any(k in proc for k in doc_keywords) or any(k in tit for k in (".pdf", ".docx", ".xlsx", ".pptx", "word", "excel", "wps")):
        return "办公或看文档"

    # 打游戏
    game_keywords = ("steam.exe", "epicgameslauncher.exe", "leagueclient.exe", "genshinimpact.exe", "genshin", "starrail", "game", "unity")
    if any(k in proc for k in game_keywords) or any(k in tit for k in ("steam", "英雄联盟", "原神", "星穹铁道", "game")):
        return "打游戏"

    # 浏览器上网
    browser_procs = ("chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "safari")
    if any(k in proc for k in browser_procs):
        return "上网"

    return "在电脑前"


def build_memory_context(last_entry: dict | None, current_activity: str) -> str | None:
    """如果上次活动与当前不同，返回一句话上下文；相同返回 None。"""
    if not last_entry or not isinstance(last_entry, dict):
        return None
    last_act = str(last_entry.get("activity", "")).strip()
    if not last_act or last_act == current_activity:
        return None
    return f"上次看到你在{last_act}，这次看到你在{current_activity}。"


def build_sync_marker(proc_name: str, current_act: str) -> str:
    """构造同步进 AI 对话会话的用户侧标记（不含窗口标题，隐私约定与陪伴记忆一致）。"""
    marker = f"[主动识屏] 前台进程：{str(proc_name or '').strip() or '未知'}"
    act = str(current_act or "").strip()
    if act:
        marker += f"（{act}）"
    return marker


def dwell_satisfied(entered_ts: float, now: float, dwell_seconds: float) -> bool:
    """判断在目标窗口的连续停留时长是否达标。"""
    if entered_ts <= 0:
        return False
    return (now - entered_ts) >= dwell_seconds


def idle_satisfied(last_input_seconds: float, min_idle_seconds: float) -> bool:
    """判断系统键盘鼠标闲置时长是否满足设定门限。"""
    return last_input_seconds >= min_idle_seconds


def should_watch(
    visible: bool,
    interacting: bool,
    mouse_through: bool,
    allow_when_mouse_through: bool,
) -> bool:
    """综合判断桌宠当前窗口状态是否允许执行识屏观察。

    - visible 为 False 或 interacting 为 True 时不允许；
    - mouse_through 为 True 时，取决于 allow_when_mouse_through 是否为 True。
    """
    if not visible or interacting:
        return False
    if mouse_through and not allow_when_mouse_through:
        return False
    return True


class ProactiveScreenWatcher:
    """主动识屏后台观察器（挂载在 PetWindow 下）。

    生命周期：作为 PetWindow 的子成员（必须随 PetWindow 创建与销毁）。
    运行机制：
    - 单一 QTimer 8s 心跳；
    - 仅在 Windows（sys.platform == 'win32'）、enabled==True 且白名单非空时运行；
    - 随 _pause_activity 停止、_resume_activity 重启；
    - 主线程执行 G1~G4 微秒级守卫判定；
    - G5 起交由后台 worker 线程抓图与计算 dHash；
    - Phase 2 为【日志模式】：仅 logging.info 输出，不调用大模型。
    """

    def __init__(self, window: Any, config: Any, worker_mode: str = "in_process") -> None:
        from PySide6.QtCore import QObject, QTimer, Signal

        class _WatcherBridge(QObject):
            # (image, app_info_str, hwnd, dhash_value, info_dict)
            # 注意：hwnd/dhash 必须用 object——Qt 的 int 是 32 位有符号，
            # 64 位 dHash 会触发 libshiboken Overflow 且投递行为未定义。
            frame_ready = Signal(object, str, object, object, dict)
            bubble_requested = Signal(str, int)
            # 主动识屏回复全文同步进 AI 对话会话（worker 线程不能直接碰
            # Qt/会话存储，经桥接信号回到主线程再转发给 app 层）
            reply_synced = Signal(str, str)

            def __init__(self, watcher: Any, parent: Any = None) -> None:
                super().__init__(parent)
                self._watcher = watcher

            def _forward_frame(self, img: Any, app_str: str, hwnd: int, cur_hash: int, info_dict: dict) -> None:
                self._watcher._on_frame_ready(img, app_str, hwnd, cur_hash, info_dict)

            def _forward_bubble(self, text: str, duration_ms: int) -> None:
                if hasattr(self._watcher.win, "show_bubble"):
                    self._watcher.win.show_bubble(text, duration_ms=duration_ms)

            def _forward_reply_sync(self, user_text: str, reply: str) -> None:
                self._watcher._on_reply_synced(user_text, reply)

        self.win = window
        self.cfg = config
        self.worker_mode = worker_mode if worker_mode in {"auto", "in_process", "disabled"} else "in_process"
        self._worker_fallback = False
        self._worker_adapter: Any = None
        self._manual_callbacks: dict[str, Any] = {}
        self._pending_manual_look: tuple[Any, str, str, Any] | None = None
        self._manual_request_id: str | None = None
        self._active_worker_frame_id: str | None = None
        self._active_worker_request_id: str | None = None
        self._active_worker_window: dict[str, Any] | None = None
        parent_obj = self.win if hasattr(self.win, "winId") else None
        self._bridge = _WatcherBridge(self, parent=parent_obj)
        self._bridge.frame_ready.connect(self._bridge._forward_frame)
        self._bridge.bubble_requested.connect(self._bridge._forward_bubble)
        self._bridge.reply_synced.connect(self._bridge._forward_reply_sync)

        self._timer = QTimer(parent_obj)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self._on_tick)

        # 状态追踪
        self._current_hwnd: int = 0
        self._entered_ts: float = 0.0
        self._last_dhash: int | None = None  # None = 尚无基线（dHash 可能合法为 0）
        self._worker_busy: bool = False
        self._request_in_flight: bool = False  # 视觉请求进行中：同时只允许一条完整 pipeline
        self._generation: int = 0  # 代次令牌：pause/关闭 时自增，使已派发/排队的任务失效

        state_path = self.cfg.dir / "proactive_screen_state.json"
        self.limiter = ProactiveLimiter(state_path, self.cfg.get("proactive_screen", {}))

        memory_path = self.cfg.dir / "proactive_screen_memory.json"
        self.memory = ProactiveMemory(memory_path)

        if self.worker_mode == "auto":
            from .workers.proactive_screen_adapter import ProactiveScreenWorkerAdapter

            self._worker_adapter = ProactiveScreenWorkerAdapter(
                self._bridge,
                mode="auto",
                budget_checker=self._worker_budget_check,
            )
            self._worker_adapter.state_changed.connect(self._on_worker_state_changed)
            self._worker_adapter.observation_ready.connect(self._on_worker_observation)
            self._worker_adapter.capture_ready.connect(self._on_worker_capture)
            self._worker_adapter.analysis_ready.connect(self._on_worker_analysis)
            self._worker_adapter.manual_ready.connect(self._on_worker_manual)
            self._worker_adapter.request_failed.connect(self._on_worker_request_failed)
            self._worker_adapter.failed.connect(self._on_worker_failed)
            self._worker_adapter.diagnostic.connect(self._on_worker_diagnostic)

        self.apply_config()

    def is_running(self) -> bool:
        """检查内部定时器是否处于运行状态。"""
        return self._timer.isActive()

    def apply_config(self) -> None:
        """根据最新配置更新频控器并决定定时器启停。"""
        raw_cfg = self.cfg.get("proactive_screen", {})
        eff = effective_proactive_config(raw_cfg)
        self.limiter.update_config(eff, dry_run=eff.get("dry_run", False))

        # 仅在 Windows（主动识屏 v1 仅限 Windows）、enabled 为 True 且白名单非空时启动定时器
        # （手册 §5.2 与验收 #3）；非 Windows 即使手改 config 开启 enabled 也不起定时器。
        # 注意：不能以 win.isVisible() 作为启动条件——PetWindow 构造时窗口尚未显示，
        # 而 showEvent 只在曾经隐藏过（_hidden_paused）时才调 _resume_activity，
        # 会导致首次启动后定时器永远不起。可见性由 _on_tick 的 G1 逐 tick 判定，
        # 隐藏由 _pause_activity → pause() 负责停止。
        # 无 Chat 变体（排除 pet.chat）的菜单/设置入口已隐藏，用户无法开启；
        # 即使手改 config 开启，真实请求会在 provider 解析阶段失败并计入熔断，不会崩溃。
        should_run = sys.platform == "win32" and eff["enabled"] and bool(eff["whitelist"])
        if self.worker_mode == "disabled":
            self.pause()
            return
        if should_run:
            if self.worker_mode == "auto" and not self._worker_fallback:
                self._start_worker(eff)
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._generation += 1  # 关闭 = 作废在飞任务
            self._timer.stop()
            self._cancel_worker_requests(notify_manual=True)
            if self._worker_adapter is not None and self._worker_adapter.active:
                self._worker_adapter.stop()

    def pause(self) -> None:
        """窗口隐藏或活动暂停时停止定时器，并作废在飞/已排队的任务。"""
        self._timer.stop()
        self._current_hwnd = 0
        self._entered_ts = 0.0
        self._generation += 1  # 代次翻转：此后到达的 frame_ready 一律丢弃
        # 截图 worker 标志清掉是安全的（迟到帧被代次丢弃）；
        # 但 _request_in_flight 不能清：网络请求线程仍在跑，清了会让恢复后
        # 重复发起第二条请求。它的 finally 一定会把标志复位（有超时兜底），
        # 其迟到答复被代次检查丢弃，不会冒泡/计费/写记忆。
        self._worker_busy = False
        self._cancel_worker_requests(notify_manual=True)
        if self._worker_adapter is not None:
            self._worker_adapter.stop()

    def resume(self) -> None:
        """窗口恢复显示时按最新配置重新评估启停。"""
        self.apply_config()

    def _on_tick(self) -> None:
        """8s 心跳主线程快速判定（G1~G4）。"""
        if self.worker_mode == "disabled":
            return
        if self._worker_fallback and self._worker_adapter is not None and self._worker_adapter.state == "stopping":
            return
        if self._worker_busy or self._request_in_flight:
            return

        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        if not eff["enabled"] or not eff["whitelist"]:
            self._timer.stop()
            return

        # G1 桌宠守卫
        visible = getattr(self.win, "isVisible", lambda: True)()
        interacting = (
            getattr(self.win, "_dragging", False) or getattr(self.win, "_physics_mode", None) is not None or getattr(self.win, "_click_effect_phase", 0) > 0
        )
        mouse_through = getattr(self.win, "mouse_through", False)
        allow_mouse_through = eff.get("allow_when_mouse_through", True)
        if not should_watch(visible, interacting, mouse_through, allow_mouse_through):
            return

        # Worker 模式下，前台窗口信息也在独立进程中读取；Core 仍负责后续
        # 白名单、停留、闲置和额度策略。in_process/fallback 保留旧路径。
        if self.worker_mode == "auto" and not self._worker_fallback:
            adapter = self._worker_adapter
            if adapter is None:
                self._switch_to_in_process("worker adapter unavailable")
            elif not adapter.ready:
                if not adapter.active:
                    self._start_worker(eff)
                return
            else:
                # The previous capture is not the authority for a new foreground observation.
                self._active_worker_window = None
                request_id = adapter.observe_foreground(self._generation)
                if request_id is not None:
                    self._worker_busy = True
                    self._active_worker_request_id = request_id
                return

        self._on_tick_in_process(eff)

    def _on_tick_in_process(self, eff: dict[str, Any]) -> None:
        """执行原有进程内前台探测路径。"""
        # 获取前台窗口信息
        from . import vision

        info = vision.foreground_window_info()
        if not info:
            self._current_hwnd = 0
            self._entered_ts = 0.0
            self._last_dhash = None
            return

        hwnd = info["hwnd"]
        proc = info["process"]
        title = info["title"]
        rect = info["rect"]

        # G2 白名单匹配
        if not match_process_whitelist(eff["whitelist"], proc, title):
            self._current_hwnd = 0
            self._entered_ts = 0.0
            self._last_dhash = None
            return

        # G2.5 联动去重：该窗口所属 Agent 联动开启且正忙时，联动气泡已在汇报，
        # 识屏不再插话（否则 Agent 每动一下她就评一句，等于刷屏）
        mgr = getattr(self.win, "agent_link_manager", None)
        if mgr is not None and mgr.busy_agent_owns_process(proc, title):
            return

        now = time.time()
        # G3 停留时长统计
        if hwnd != self._current_hwnd:
            self._current_hwnd = hwnd
            self._entered_ts = now
            self._last_dhash = None
            return

        if not dwell_satisfied(self._entered_ts, now, eff["dwell_seconds"]):
            return

        # G4 闲置判定（默认关闭，require_idle 为 True 时生效）
        if eff["require_idle"]:
            idle_sec = vision.get_system_idle_seconds()
            if not idle_satisfied(idle_sec, eff["min_idle_seconds"]):
                return

        # 快速前置频控检查（避开无意义抓图）
        ok, _ = self.limiter.allow()
        if not ok:
            return

        # G1~G4 全部通过，派发后台 worker 执行 G5（截图 + dHash 计算）
        self._worker_busy = True
        import threading

        threading.Thread(
            target=self._worker_capture,
            args=(rect, info, eff, self._generation),
            daemon=True,
            name="proactive-screen-worker",
        ).start()

    # ---------------------------------------------------------- worker bridge
    def _worker_config(self, eff: dict[str, Any]) -> dict[str, Any]:
        """Return the small, non-sensitive configuration sent to the worker."""
        return {
            "max_edge": 768,
            "jpeg_quality": 70,
            "frame_ttl": 30,
            "platform": sys.platform,
        }

    def _start_worker(self, eff: dict[str, Any]) -> bool:
        adapter = self._worker_adapter
        if self.worker_mode != "auto" or self._worker_fallback or adapter is None:
            return False
        if adapter.state in {"stopping", "crashed"}:
            return True
        try:
            started = bool(adapter.start(self._worker_config(eff)))
        except Exception:
            import logging

            logging.exception("主动识屏 Worker 启动失败")
            self._switch_to_in_process("worker start exception")
            return False
        if adapter.state in {"stopping", "crashed"}:
            return True  # Never start a legacy request while a process is still exiting/restarting.
        if not started and not adapter.active:
            self._switch_to_in_process("worker start rejected")
            return False
        return True

    def _switch_to_in_process(self, reason: str) -> None:
        """Disable the worker path while keeping the legacy path available."""
        if self.worker_mode == "disabled" or self._worker_fallback:
            return
        import logging

        self._worker_fallback = True
        logging.warning("主动识屏 Worker 降级到进程内实现: %s", reason)
        self._cancel_worker_requests(notify_manual=True)
        adapter = self._worker_adapter
        if adapter is not None and adapter.active:
            try:
                adapter.stop()
            except Exception:
                logging.exception("停止主动识屏 Worker 失败")

    def _cancel_automatic_request(self) -> None:
        request_id = self._active_worker_request_id
        self._active_worker_request_id = None
        self._active_worker_window = None
        self._worker_busy = False
        self._request_in_flight = False
        if request_id and self._worker_adapter is not None:
            self._worker_adapter.cancel(request_id, generation=self._generation)
        self._release_worker_frame()

    def _cancel_worker_requests(self, *, notify_manual: bool = False) -> None:
        self._cancel_automatic_request()
        request_id = self._manual_request_id
        self._manual_request_id = None
        if request_id and self._worker_adapter is not None:
            self._worker_adapter.cancel(request_id, generation=self._generation)
        if notify_manual:
            self._fail_manual_requests("主动识屏 Worker 已停止")

    def _release_worker_frame(self, frame_id: str | None = None) -> None:
        frame_id = frame_id or self._active_worker_frame_id
        if frame_id == self._active_worker_frame_id:
            self._active_worker_frame_id = None
        if not frame_id or self._worker_adapter is None:
            return
        try:
            self._worker_adapter.release_frame(frame_id, generation=self._generation)
        except Exception:
            pass

    def _reset_foreground_state(self) -> None:
        self._current_hwnd = 0
        self._entered_ts = 0.0
        self._last_dhash = None

    def _on_worker_state_changed(self, state: str) -> None:
        import logging

        logging.debug("主动识屏 Worker 状态: %s", state)
        if state == "ready":
            self._dispatch_pending_manual_look()
        elif state == "fault":
            self._switch_to_in_process("worker entered fault state")

    def _matches_automatic_request(self, envelope: Mapping[str, Any]) -> bool:
        return (
            envelope.get("generation") == self._generation
            and envelope.get("request_id") == self._active_worker_request_id
            and self._active_worker_request_id is not None
        )

    def _automatic_allowed_now(self, window: Mapping[str, Any] | None = None) -> bool:
        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        if not (
            eff["enabled"]
            and eff["whitelist"]
            and self.worker_mode != "disabled"
            and getattr(self.win, "isVisible", lambda: True)()
            and not getattr(self.win, "_dragging", False)
            and getattr(self.win, "_physics_mode", None) is None
            and not getattr(self.win, "_click_effect_phase", 0)
            and (not getattr(self.win, "mouse_through", False) or eff.get("allow_when_mouse_through", True))
        ):
            return False
        window = window if window is not None else self._active_worker_window
        if window is not None:
            process, title = str(window.get("process", "")), str(window.get("title", ""))
            if not match_process_whitelist(eff["whitelist"], process, title):
                return False
            manager = getattr(self.win, "agent_link_manager", None)
            if manager is not None and manager.busy_agent_owns_process(process, title):
                return False
        if eff["require_idle"]:
            from .vision import get_system_idle_seconds

            if not idle_satisfied(get_system_idle_seconds(), eff["min_idle_seconds"]):
                return False
        return True

    def _on_worker_observation(self, envelope: Mapping[str, Any]) -> None:
        if not self._matches_automatic_request(envelope):
            return
        if not self._automatic_allowed_now():
            self._cancel_automatic_request()
            return
        self._active_worker_request_id = None
        self._worker_busy = False
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        window = result.get("window")
        if not isinstance(window, Mapping):
            self._reset_foreground_state()
            return
        try:
            hwnd = int(window.get("hwnd", 0))
        except (TypeError, ValueError):
            self._reset_foreground_state()
            return
        process = str(window.get("process", ""))
        title = str(window.get("title", ""))
        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        if not match_process_whitelist(eff["whitelist"], process, title):
            self._reset_foreground_state()
            return

        mgr = getattr(self.win, "agent_link_manager", None)
        if mgr is not None and mgr.busy_agent_owns_process(process, title):
            return

        now = time.time()
        if hwnd != self._current_hwnd:
            self._current_hwnd = hwnd
            self._entered_ts = now
            self._last_dhash = None
            return
        if not dwell_satisfied(self._entered_ts, now, eff["dwell_seconds"]):
            return

        from . import vision

        if eff["require_idle"] and not idle_satisfied(vision.get_system_idle_seconds(), eff["min_idle_seconds"]):
            return
        allowed, _ = self.limiter.allow()
        if not allowed or self._worker_adapter is None or not self._worker_adapter.ready:
            return
        request_id = self._worker_adapter.capture_foreground(dict(window), self._generation)
        if request_id is not None:
            self._worker_busy = True
            self._active_worker_request_id = request_id

    def _on_worker_capture(self, envelope: Mapping[str, Any]) -> None:
        if not self._matches_automatic_request(envelope):
            result = envelope.get("result")
            if isinstance(result, Mapping) and result.get("frame_id"):
                self._release_worker_frame(str(result["frame_id"]))
            return
        self._active_worker_request_id = None
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        frame_id = str(result.get("frame_id", ""))
        if not frame_id:
            self._worker_busy = False
            return
        try:
            hwnd = int(result.get("window", {}).get("hwnd"))
            cur_hash = int(result.get("dhash", -1))
        except (AttributeError, TypeError, ValueError):
            self._release_worker_frame(frame_id)
            self._worker_busy = False
            return
        window = result.get("window")
        if not isinstance(window, Mapping):
            self._release_worker_frame(frame_id)
            self._worker_busy = False
            return
        if not self._automatic_allowed_now(window):
            self._release_worker_frame(frame_id)
            self._cancel_automatic_request()
            return
        self._active_worker_window = dict(window)
        info = dict(window)
        info["_gen"] = self._generation
        app_str = str(result.get("app_info", ""))
        self._on_frame_ready(None, app_str, hwnd, cur_hash, info, worker_frame_id=frame_id)

    def _on_worker_analysis(self, envelope: Mapping[str, Any]) -> None:
        if not self._matches_automatic_request(envelope):
            return
        if not self._automatic_allowed_now():
            self._cancel_automatic_request()
            return
        self._active_worker_request_id = None
        self._request_in_flight = False
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        reply = str(result.get("reply", ""))
        window = result.get("window")
        window = window if isinstance(window, Mapping) else {}
        proc_name = str(window.get("process", ""))
        win_title = str(window.get("title", ""))
        self._release_worker_frame()
        self._worker_busy = False
        if envelope.get("generation") not in (None, self._generation):
            return
        if reply.strip() and self._bridge_alive():
            duration = max(6000, min(20000, 4000 + len(reply) * 150))
            self._bridge.bubble_requested.emit(reply, duration)
            self.limiter.record_success()
            current_act = classify_activity(proc_name, win_title)
            if proc_name or current_act:
                self.memory.record(proc_name, "", current_act)
            import logging

            logging.info("主动识屏回复全文: 前台进程=%s 活动=%s | %s", proc_name, current_act, reply)
            self._bridge.reply_synced.emit(build_sync_marker(proc_name, current_act), reply)
        else:
            self.limiter.record_failure()

    def _on_worker_manual(self, envelope: Mapping[str, Any]) -> None:
        request_id = str(envelope.get("request_id", ""))
        callback = self._manual_callbacks.pop(request_id, None)
        if request_id == self._manual_request_id:
            self._manual_request_id = None
        if envelope.get("generation") not in (None, self._generation):
            return
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        reply = str(result.get("reply", ""))
        app_info = str(result.get("app_info", ""))
        if callable(callback):
            try:
                if reply.strip():
                    user_text = f"[看看屏幕] 前台窗口：{app_info}" if app_info else "[看看屏幕]"
                    callback(reply, user_text, False)
                else:
                    callback("未能得到有效回复", "", True)
            except Exception:
                import logging

                logging.exception("主动识屏手动结果回调失败")

        self._stop_idle_worker()

    def _stop_idle_worker(self) -> None:
        """A manual-only request must not leave a permanent background process."""
        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        automatic = sys.platform == "win32" and eff["enabled"] and bool(eff["whitelist"])
        if (
            not automatic
            and not self._manual_callbacks
            and self._pending_manual_look is None
            and self._active_worker_request_id is None
            and self._worker_adapter is not None
            and self._worker_adapter.active
        ):
            self._worker_adapter.stop()

    def cancel_manual_look(self, callback: Any) -> None:
        """Detach only the closing window's manual request from a shared watcher."""
        if self._pending_manual_look is not None and self._pending_manual_look[3] == callback:
            self._pending_manual_look = None
        for request_id, owner in list(self._manual_callbacks.items()):
            if owner == callback:
                del self._manual_callbacks[request_id]
                if self._manual_request_id == request_id:
                    self._manual_request_id = None
                if self._worker_adapter is not None:
                    self._worker_adapter.cancel(request_id, generation=self._generation)
        self._stop_idle_worker()

    def _on_worker_request_failed(self, operation: str, envelope: Mapping[str, Any]) -> None:
        if operation in {"observe_foreground", "capture_foreground", "analyze_frame"} and not self._matches_automatic_request(envelope):
            return
        message = str(envelope.get("message") or envelope.get("error_code") or "识屏操作失败")
        if operation == "observe_foreground":
            self._active_worker_request_id = None
            self._worker_busy = False
        elif operation == "capture_foreground":
            self._active_worker_request_id = None
            self._release_worker_frame()
            self._worker_busy = False
        elif operation == "analyze_frame":
            self.limiter.record_failure()
            self._active_worker_request_id = None
            self._request_in_flight = False
            self._release_worker_frame()
            self._worker_busy = False
        elif operation == "manual_look":
            request_id = str(envelope.get("request_id", ""))
            callback = self._manual_callbacks.pop(request_id, None)
            if request_id == self._manual_request_id:
                self._manual_request_id = None
            if callable(callback):
                try:
                    callback(message, "", True)
                except Exception:
                    import logging

                    logging.exception("主动识屏手动失败回调异常")

        self._stop_idle_worker()

    def _on_worker_failed(self, reason: str) -> None:
        if self._worker_adapter is not None and self._worker_adapter.state == "fault":
            self._switch_to_in_process(str(reason))

    def _on_worker_diagnostic(self, stage: str, detail: object) -> None:
        import logging

        logging.debug("主动识屏 Worker 诊断 [%s]: %s", stage, detail)

    def _worker_budget_check(self, kind: str, generation: int | None) -> bool:
        if kind != "automatic" or generation != self._generation or not self._automatic_allowed_now():
            return False
        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        if not eff["enabled"] or self.worker_mode == "disabled":
            return False
        return self.limiter.consume_budget()

    def request_manual_look(self, provider: Any, system_prompt: str, pet_name: str, callback: Any) -> bool:
        """Request manual screen analysis through the worker when available."""
        if self.worker_mode == "disabled":
            callback("识屏已停用", "", True)
            return True
        if self._worker_adapter is not None and self._worker_adapter.state == "stopping":
            callback("识屏服务正在停止，请稍后重试", "", True)
            return True
        if self.worker_mode != "auto" or self._worker_fallback or self._worker_adapter is None:
            return False
        if self._manual_request_id is not None or self._pending_manual_look is not None:
            callback("上一张还没看完呢…", "", True)
            return True
        if not self._worker_adapter.ready:
            if not self._worker_adapter.active:
                if not self._start_worker(effective_proactive_config(self.cfg.get("proactive_screen", {}))):
                    return False
            self._pending_manual_look = (provider, str(system_prompt), str(pet_name), callback)
            return True
        request_id = self._worker_adapter.manual_look(
            provider,
            system_prompt,
            generation=self._generation,
            pet_name=pet_name,
        )
        if request_id is None:
            callback("识屏服务繁忙，请稍后重试", "", True)
            return True  # Backpressure must never start a second in-process request.
        self._manual_callbacks[request_id] = callback
        self._manual_request_id = request_id
        return True

    def _dispatch_pending_manual_look(self) -> None:
        pending = self._pending_manual_look
        self._pending_manual_look = None
        if pending is None or self._worker_adapter is None or not self._worker_adapter.ready:
            return
        provider, system_prompt, pet_name, callback = pending
        request_id = self._worker_adapter.manual_look(
            provider,
            system_prompt,
            generation=self._generation,
            pet_name=pet_name,
        )
        if request_id is None:
            try:
                callback("主动识屏 Worker 暂不可用", "", True)
            except Exception:
                import logging

                logging.exception("主动识屏手动派发失败回调异常")
            return
        self._manual_callbacks[request_id] = callback
        self._manual_request_id = request_id

    def _fail_manual_requests(self, message: str) -> None:
        callbacks = list(self._manual_callbacks.values())
        self._manual_callbacks.clear()
        pending = self._pending_manual_look
        self._pending_manual_look = None
        if pending is not None and callable(pending[3]):
            callbacks.append(pending[3])
        self._manual_request_id = None
        for callback in callbacks:
            try:
                callback(message, "", True)
            except Exception:
                import logging

                logging.exception("主动识屏手动失败回调异常")

    def _bridge_alive(self) -> bool:
        """桥接 QObject 是否仍存活（窗口销毁后 daemon 线程的 emit 会崩）。"""
        try:
            import shiboken6

            return shiboken6.isValid(self._bridge)
        except Exception:
            return True  # 无法判定时按存活处理，异常由调用点兜底

    def _worker_capture(self, rect: Any, info: dict, eff: dict, gen: int) -> None:
        """后台 worker 线程：执行窗口截图与 dHash 计算。"""
        emitted = False
        try:
            from . import vision

            # TOCTOU 复核：派发到现在之间用户可能已切到非白名单窗口，
            # 抓图前必须重新确认前台 hwnd 仍是当时那个窗口，否则按旧矩形
            # 会截到别的应用内容（隐私红线）。
            current = vision.foreground_window_info()
            if not current or current.get("hwnd") != info.get("hwnd"):
                return

            img = vision.capture_window_rect(rect)
            if img is not None:
                # 抓图后二次复核：抓取期间前台可能又切换了（内容已不属于白名单窗口）
                current = vision.foreground_window_info()
                if not current or current.get("hwnd") != info.get("hwnd"):
                    return
                cur_hash = image_dhash(img)
                app_str = f"{info.get('process', '')} | {info.get('title', '')}".strip(" |")
                payload = dict(info)
                payload["_gen"] = gen
                # 编码前先降采样（审查 DS-M3）：窗口全分辨率 JPEG 是 MB 级
                # 跨线程搬运，模型侧最长边 768 已够用（与 capture_screen_bytes
                # 同规）。dHash 必须在降采样前算，保持既有阈值语义不变。
                w, h = img.size
                scale = vision.MAX_EDGE / max(w, h, 1)
                if scale < 1.0:
                    # vision 的 PIL 已下沉为函数内懒导入，这里自行局部导入
                    from PIL import Image

                    img = img.resize(
                        (max(1, round(w * scale)), max(1, round(h * scale))),
                        Image.LANCZOS,
                    )
                # JPEG 编码放在 worker 线程，避免主线程卡顿
                import io

                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=70)
                jpeg_bytes = buf.getvalue()
                if self._bridge_alive():
                    self._bridge.frame_ready.emit(jpeg_bytes, app_str, info["hwnd"], cur_hash, payload)
                    emitted = True
        except Exception:
            pass
        finally:
            # 未送达（含 TOCTOU 提前返回/抓图失败/编码异常）→ 这里释放 worker 位；
            # 已送达 → 由 _on_frame_ready 的 finally 释放。
            if not emitted:
                self._worker_busy = False

    def _on_frame_ready(
        self,
        img: Any,
        app_str: str,
        hwnd: int,
        cur_hash: int,
        info: dict | None = None,
        worker_frame_id: str | None = None,
    ) -> None:
        """主线程槽：接收截图结果，执行 dHash/频控并进入视觉请求。"""
        if worker_frame_id and info and info.get("_gen") != self._generation:
            self._release_worker_frame(worker_frame_id)
            return  # A stale frame must not clear a newer pipeline's busy state.
        keep_worker_busy = False
        try:
            info = info or {}
            eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))

            # 代次/开关复核：截图派发后用户可能已关闭功能或窗口被隐藏（pause 翻转代次），
            # 这类“迟到帧”一律丢弃，绝不再发请求。
            if info.get("_gen") is not None and info["_gen"] != self._generation:
                return
            if not eff["enabled"]:
                return

            threshold = eff.get("change_threshold", 8)

            # dHash 变化判定（若与上一次快照差异小于阈值，说明画面未发生显著变化）
            if self._last_dhash is not None:
                dist = hamming_distance(self._last_dhash, cur_hash)
                if dist < threshold:
                    import logging

                    logging.debug(
                        "主动识屏: 画面变化未达阈值 (dist=%d, thresh=%d)",
                        dist,
                        threshold,
                    )
                    return

            # G6 严格频控门禁二次确认（原子判定+盖章，多开不互相踩冷却）
            ok, _ = self.limiter.try_acquire()
            if not ok:
                return

            self._last_dhash = cur_hash

            # 判断是否处于 dry_run 模式
            if self.limiter.dry_run:
                import logging

                logging.info(
                    "主动识屏 [dry-run 模式]: 条件满足已触发! 前台: %s, 窗口: %s, dHash: %s",
                    str(info.get("process", "")) or app_str.split(" | ")[0],
                    hwnd,
                    hex(cur_hash),
                )
                # last_request 已在 try_acquire 原子盖章，无需再 record_attempt
                return

            # 真实模式：触发先兆提示 + 派发后台视觉请求（同上，盖章已完成）
            # 先兆 → 模型答复整个窗口期内占用气泡位，自言自语让路（防连环顶掉）
            hold = getattr(self.win, "hold_bubble", None)
            if callable(hold):
                hold(30.0)
            if eff.get("pre_cue", True) and hasattr(self.win, "show_bubble"):
                self.win.show_bubble("让我看看……", duration_ms=2500)

            # 解析 Provider（手册 §6：免费优先策略）
            provider, system_prompt = self._resolve_vision_provider(eff)

            # 短期陪伴记忆（Phase 5）：分类活动并构造上下文
            proc_name = str(info.get("process", "")).strip()
            win_title = str(info.get("title", "")).strip()
            current_act = classify_activity(proc_name, win_title)
            last_entry = self.memory.latest()
            memory_ctx = build_memory_context(last_entry, current_act) or ""

            if worker_frame_id is not None:
                adapter = self._worker_adapter
                if adapter is None or not adapter.ready:
                    return
                from . import catalog

                pet_name = self.cfg.character_display_name(str(self.cfg.get("character", catalog.DEFAULT_CHARACTER)))
                request_id = adapter.analyze_frame(
                    worker_frame_id,
                    provider,
                    system_prompt,
                    generation=self._generation,
                    memory_context=memory_ctx,
                    pet_name=pet_name,
                )
                if request_id is None:
                    return
                self._active_worker_frame_id = worker_frame_id
                self._active_worker_request_id = request_id
                self._request_in_flight = True
                keep_worker_busy = True
                return

            # 进程内兼容路径：纯内存 JPEG bytes（严禁写临时文件）。
            # worker 线程已完成编码；测试传入 PIL Image 时在此兼容编码。
            if isinstance(img, (bytes, bytearray)):
                jpeg_bytes = bytes(img)
            else:
                import io

                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=70)
                jpeg_bytes = buf.getvalue()

            import threading

            self._request_in_flight = True  # 请求完成前不再派新 pipeline
            threading.Thread(
                target=self._worker_request_vision,
                args=(
                    jpeg_bytes,
                    app_str,
                    system_prompt,
                    provider,
                    memory_ctx,
                    proc_name,
                    win_title,
                    current_act,
                    self._generation,
                ),
                daemon=True,
                name="proactive-vision-requester",
            ).start()
        finally:
            if worker_frame_id is not None and not keep_worker_busy:
                self._release_worker_frame(worker_frame_id)
            if not keep_worker_busy:
                self._worker_busy = False

    def _on_reply_synced(self, user_text: str, reply: str) -> None:
        """主线程槽：把主动识屏回复全文转发给 app 层同步进 AI 对话会话。

        无 Chat 变体（on_look_synced 为 None）或 app 层已销毁时静默跳过。
        """
        callback = getattr(self.win, "on_look_synced", None)
        if not callable(callback):
            return
        try:
            callback(user_text, reply)
        except Exception:
            import logging

            logging.exception("主动识屏回复同步进会话失败")

    def _resolve_vision_provider(self, eff: dict) -> tuple[Any, str]:
        """解析视觉请求的 provider 与 system_prompt（手册 §6）。

        provider 做浅拷贝再注入 key，避免与聊天共享的可变对象产生竞态。
        prefer_free_provider=False 时强制跟随聊天模型（vision_same_as_chat=True），
        让该开关有真实语义：True=配置了独立视觉端点（如免费 GLM）就用它，False=始终用聊天 provider。
        """
        import copy

        chat_settings = self.cfg.chat_settings()
        provider = copy.copy(chat_settings.active_config)
        provider.api_key = self.cfg.resolve_api_key(provider)
        if not eff.get("prefer_free_provider", True):
            provider.vision_same_as_chat = True
        system_prompt = chat_settings.default_system_prompt
        return provider, system_prompt

    def _worker_request_vision(
        self,
        jpeg_bytes: bytes,
        app_str: str,
        system_prompt: str,
        provider: Any,
        memory_ctx: str = "",
        proc_name: str = "",
        win_title: str = "",
        current_act: str = "",
        gen: int = -1,
    ) -> None:
        """后台线程：发起大模型视觉请求，处理重试/熔断，并通过桥接信号在桌宠冒泡。"""
        from . import catalog, vision

        pet_name = self.cfg.character_display_name(str(self.cfg.get("character", catalog.DEFAULT_CHARACTER)))
        try:
            reply = vision._post_vision_request(
                jpeg_bytes,
                app_str,
                system_prompt,
                provider,
                memory_context=memory_ctx,
                consume_budget=self.limiter.consume_budget,
                pet_name=pet_name,
            )
            # 代次隔离：请求在飞期间用户关闭功能/隐藏窗口（pause 翻转代次）时，
            # 迟到答复一律丢弃——不冒泡、不耗额度计数、不写陪伴记忆。
            if gen >= 0 and gen != self._generation:
                return
            if reply and reply.strip() and self._bridge_alive():
                # 按时长按内容长度缩放：太短读不完。6s 起步，每字 +150ms，封顶 20s
                duration = max(6000, min(20000, 4000 + len(reply) * 150))
                self._bridge.bubble_requested.emit(reply, duration)
                self.limiter.record_success()
                # 陪伴记忆只存 进程名+活动分类，不落窗口标题（可能含文档/网页敏感信息）
                if proc_name or current_act:
                    self.memory.record(proc_name, "", current_act)
                # 回复全文落日志 + 同步进 AI 对话会话（issue #24：被气泡省略/分页的
                # 内容从此可在聊天历史/pet.log 里回看）。标记同样不含窗口标题，
                # 与陪伴记忆的隐私约定一致。
                import logging

                logging.info(
                    "主动识屏回复全文: 前台进程=%s 活动=%s | %s",
                    proc_name,
                    current_act,
                    reply,
                )
                self._bridge.reply_synced.emit(build_sync_marker(proc_name, current_act), reply)
            else:
                # 空回复视为失败（计入熔断，不冒泡、不写记忆）
                self.limiter.record_failure()
        except Exception as exc:
            import logging

            logging.warning("主动识屏请求失败: %s", exc)
            # 代次已翻转（用户关闭/隐藏）的失败不计入熔断，避免误伤当日额度
            if gen < 0 or gen == self._generation:
                self.limiter.record_failure()
        finally:
            self._request_in_flight = False
