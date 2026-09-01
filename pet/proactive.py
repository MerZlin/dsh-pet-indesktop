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


def match_process_whitelist(
    rules: list[str] | None, process_name: str | None, window_title: str | None
) -> bool:
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

    def __init__(self, window: Any, config: Any) -> None:
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

            def _forward_frame(
                self, img: Any, app_str: str, hwnd: int, cur_hash: int, info_dict: dict
            ) -> None:
                self._watcher._on_frame_ready(img, app_str, hwnd, cur_hash, info_dict)

            def _forward_bubble(self, text: str, duration_ms: int) -> None:
                if hasattr(self._watcher.win, "show_bubble"):
                    self._watcher.win.show_bubble(text, duration_ms=duration_ms)

            def _forward_reply_sync(self, user_text: str, reply: str) -> None:
                self._watcher._on_reply_synced(user_text, reply)

        self.win = window
        self.cfg = config
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
        self.limiter = ProactiveLimiter(
            state_path, self.cfg.get("proactive_screen", {})
        )

        memory_path = self.cfg.dir / "proactive_screen_memory.json"
        self.memory = ProactiveMemory(memory_path)

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
        should_run = (
            sys.platform == "win32"
            and eff["enabled"]
            and bool(eff["whitelist"])
        )
        if should_run:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._generation += 1  # 关闭 = 作废在飞任务
            self._timer.stop()

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

    def resume(self) -> None:
        """窗口恢复显示时按最新配置重新评估启停。"""
        self.apply_config()

    def _on_tick(self) -> None:
        """8s 心跳主线程快速判定（G1~G4）。"""
        if self._worker_busy or self._request_in_flight:
            return

        eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))
        if not eff["enabled"] or not eff["whitelist"]:
            self._timer.stop()
            return

        # G1 桌宠守卫
        visible = getattr(self.win, "isVisible", lambda: True)()
        interacting = (
            getattr(self.win, "_dragging", False)
            or getattr(self.win, "_physics_mode", None) is not None
            or getattr(self.win, "_click_effect_phase", 0) > 0
        )
        mouse_through = getattr(self.win, "mouse_through", False)
        allow_mouse_through = eff.get("allow_when_mouse_through", True)
        if not should_watch(visible, interacting, mouse_through, allow_mouse_through):
            return

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
                app_str = (
                    f"{info.get('process', '')} | {info.get('title', '')}".strip(" |")
                )
                payload = dict(info)
                payload["_gen"] = gen
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
        self, img: Any, app_str: str, hwnd: int, cur_hash: int, info: dict | None = None
    ) -> None:
        """主线程槽：接收后台截图计算结果，执行 dHash 差异与频控，进入真实请求或日志模式。"""
        try:
            info = info or {}
            eff = effective_proactive_config(self.cfg.get("proactive_screen", {}))

            # 代次/开关复核：截图派发后用户可能已关闭功能或窗口被隐藏（pause 翻转代次），
            # 这类"迟到帧"一律丢弃，绝不再发请求。
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
            ok, reason = self.limiter.try_acquire()
            if not ok:
                return

            self._last_dhash = cur_hash

            # 判断是否处于 dry_run 模式
            if self.limiter.dry_run:
                import logging

                logging.info(
                    "主动识屏 [dry-run 模式]: 条件满足已触发! 前台: %s, 窗口: %s, dHash: %s",
                    str(info.get("process", "")) or app_str.split(" | ")[0],  # 只记进程名，标题不落日志
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

            # 纯内存 JPEG bytes（严禁写临时文件）；worker 线程已完成编码，
            # 直接调用（测试）传入 PIL Image 时在此兼容编码
            if isinstance(img, (bytes, bytearray)):
                jpeg_bytes = bytes(img)
            else:
                import io

                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=70)
                jpeg_bytes = buf.getvalue()

            # 解析 Provider（手册 §6：免费优先策略）
            provider, system_prompt = self._resolve_vision_provider(eff)

            # 短期陪伴记忆（Phase 5）：分类活动并构造上下文
            proc_name = str(info.get("process", "")).strip()
            win_title = str(info.get("title", "")).strip()
            current_act = classify_activity(proc_name, win_title)
            last_entry = self.memory.latest()
            memory_ctx = build_memory_context(last_entry, current_act) or ""

            import threading

            self._request_in_flight = True  # 请求完成前不再派新 pipeline
            threading.Thread(
                target=self._worker_request_vision,
                args=(jpeg_bytes, app_str, system_prompt, provider, memory_ctx, proc_name, win_title, current_act, self._generation),
                daemon=True,
                name="proactive-vision-requester",
            ).start()
        finally:
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
        from . import vision

        try:
            reply = vision._post_vision_request(
                jpeg_bytes, app_str, system_prompt, provider, memory_context=memory_ctx,
                consume_budget=self.limiter.consume_budget,
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
                    proc_name, current_act, reply,
                )
                self._bridge.reply_synced.emit(
                    build_sync_marker(proc_name, current_act), reply
                )
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
