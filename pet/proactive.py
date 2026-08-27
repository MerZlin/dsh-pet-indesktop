# -*- coding: utf-8 -*-
"""主动识屏与关怀机制 — Phase 1 纯函数与频控组件。

本模块包含纯逻辑、零副作用、无 Win32 依赖的算法与状态门禁：
- 白名单匹配（fnmatch 大小写不敏感，支持 title: 规则）；
- 8x8 画面变化检测 dHash 与 Hamming 距离；
- 频控门禁 ProactiveLimiter（跨实例共享状态、每日上限、冷却、熔断）；
- 停留与闲置判定；
- 配置预设与有效配置 clamp 计算。
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

# 合法参数范围与默认值常量定义（依据实施手册 §2 与 §3）
PRESET_DEFAULTS: dict[str, dict[str, int]] = {
    "quiet": {"dwell_seconds": 90, "cooldown_minutes": 10, "daily_cap": 8},
    "balanced": {"dwell_seconds": 45, "cooldown_minutes": 5, "daily_cap": 15},
    "active": {"dwell_seconds": 20, "cooldown_minutes": 3, "daily_cap": 25},
}

DEFAULT_PROACTIVE_CONFIG: dict[str, Any] = {
    "enabled": False,
    "dry_run": False,
    "preset": "balanced",
    "allow_when_mouse_through": True,
    "whitelist": [],
    "dwell_seconds": 45,
    "require_idle": False,
    "min_idle_seconds": 30,
    "cooldown_minutes": 5,
    "daily_cap": 15,
    "min_request_interval_seconds": 60,
    "change_threshold": 8,
    "prefer_free_provider": True,
    "pre_cue": True,
}


def _clamp(val: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, num))


def _clamp_int(val: Any, default: int, minimum: int, maximum: int) -> int:
    return round(_clamp(val, default, minimum, maximum))


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


class ProactiveMemory:
    """主动识屏短期陪伴记忆管理器。

    存储文件：<config.dir>/proactive_screen_memory.json
    - 仅记录元数据（时间戳、进程名、标题、活动分类），绝不保存截图；
    - 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
    - 采用 .tmp + 原子替换持久化；损坏回退空列表。
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        max_entries: int = 20,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self.max_entries = max(1, max_entries)

    def load(self) -> list[dict[str, Any]]:
        """读取记忆列表（按时间倒序，最新在最前）。"""
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                return raw["entries"]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def latest(self) -> dict[str, Any] | None:
        """获取最近一条记忆项。"""
        entries = self.load()
        return entries[0] if entries else None

    def record(self, process: str, title: str, activity: str) -> None:
        """记录一条新的陪伴活动记忆。

        注意：title 参数仅用于保持调用签名兼容，**不会落盘**——窗口标题可能含
        文档名/网页标题等敏感信息，记忆只保留进程名与活动分类。"""
        entries = self.load()
        new_item = {
            "ts": self._clock(),
            "process": str(process or "").strip(),
            "activity": str(activity or "").strip(),
        }
        entries.insert(0, new_item)
        entries = entries[: self.max_entries]

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        """清空陪伴记忆。"""
        try:
            if self.path.is_file():
                self.path.unlink()
        except OSError:
            pass


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


def effective_proactive_config(raw: dict | None) -> dict[str, Any]:
    """计算主动识屏的有效运行时配置。

    - 以 DEFAULT_PROACTIVE_CONFIG 为基础；
    - 根据 preset 填充 dwell_seconds、cooldown_minutes、daily_cap；
    - 合并用户 raw 字典中的自定义配置；
    - 所有数值 clamp 到手册 §2 合法范围；
    - require_idle 为 False 时，effective 配置中 min_idle_seconds 视为 0（保留原始键不变）；
    - 非法 preset 回退为 'balanced'。
    """
    result = dict(DEFAULT_PROACTIVE_CONFIG)
    raw = raw if isinstance(raw, dict) else {}

    preset = str(raw.get("preset", result["preset"])).strip().lower()
    if preset not in PRESET_DEFAULTS and preset != "custom":
        preset = "balanced"
    result["preset"] = preset

    # 预设覆盖三项（custom 不覆盖）
    if preset in PRESET_DEFAULTS:
        result.update(PRESET_DEFAULTS[preset])

    # 用户手动配置项覆盖
    for k, v in raw.items():
        if k in DEFAULT_PROACTIVE_CONFIG and v is not None:
            result[k] = v

    # 确保 preset 在非法情况下已被规范化
    result["preset"] = preset

    # 规范化与范围 clamp
    result["enabled"] = bool(result.get("enabled", False))
    result["dry_run"] = bool(result.get("dry_run", False))
    result["allow_when_mouse_through"] = bool(result.get("allow_when_mouse_through", True))
    result["require_idle"] = bool(result.get("require_idle", False))
    result["prefer_free_provider"] = bool(result.get("prefer_free_provider", True))
    result["pre_cue"] = bool(result.get("pre_cue", True))

    whitelist = result.get("whitelist")
    if isinstance(whitelist, list):
        result["whitelist"] = [str(item).strip() for item in whitelist if str(item).strip()]
    else:
        result["whitelist"] = []

    # clamp 数值范围（手册 §2）
    # dwell_seconds: 15 ~ 600 (默认 45)
    # min_idle_seconds: 0 ~ 3600 (默认 30)
    # cooldown_minutes: 1 ~ 120 (默认 5)
    # daily_cap: 1 ~ 9999 (默认 15；用户自定义不设硬顶，约等于不限)
    # min_request_interval_seconds: 30 ~ 3600 (默认 60)
    # change_threshold: 0 ~ 32 (默认 8)
    result["dwell_seconds"] = _clamp_int(result.get("dwell_seconds"), 45, 15, 600)
    # cooldown 允许 0.5 分钟粒度（用户反馈整分钟太粗）
    result["cooldown_minutes"] = _clamp(result.get("cooldown_minutes"), 5.0, 0.5, 120.0)
    result["daily_cap"] = _clamp_int(result.get("daily_cap"), 15, 1, 9999)
    result["min_request_interval_seconds"] = _clamp_int(
        result.get("min_request_interval_seconds"), 60, 30, 3600
    )
    result["change_threshold"] = _clamp_int(result.get("change_threshold"), 8, 0, 32)

    raw_min_idle = _clamp_int(result.get("min_idle_seconds"), 30, 0, 3600)
    result["min_idle_seconds"] = raw_min_idle if result["require_idle"] else 0

    return result


class ProactiveLimiter:
    """主动识屏频控与熔断门禁管理器。

    状态文件：<config.dir>/proactive_screen_state.json（dry_run 模式使用独立文件）
    - 状态文件跨实例共享，daily_cap / 最小间隔 / 冷却为全局上限；
    - 支持 dry_run 模式：仅维护 dry-run 状态与 60s 最小间隔，绝不消耗用户当日真实额度与熔断状态；
    - 支持可注入时钟与日期（便于单测）；
    - 采用 .tmp + 原子替换持久化；损坏回退全新状态。
    """

    def __init__(
        self,
        state_path: Path | str,
        cfg: dict | None,
        *,
        dry_run: bool = False,
        clock: Callable[[], float] = time.time,
        today: Callable[[], str] | None = None,
    ) -> None:
        self.raw_state_path = Path(state_path)
        self.dry_run = dry_run
        # dry_run 模式使用独立的 dryrun_state 文件，防止污染真实状态
        if self.dry_run:
            self.state_path = self.raw_state_path.with_name("proactive_screen_dryrun_state.json")
        else:
            self.state_path = self.raw_state_path
        self.cfg = effective_proactive_config(cfg)
        self._clock = clock
        self._today_fn = today or (lambda: datetime.date.today().isoformat())

    def update_config(self, cfg: dict | None, dry_run: bool | None = None) -> None:
        """更新内部缓存的有效配置与 dry_run 模式。"""
        self.cfg = effective_proactive_config(cfg)
        if dry_run is not None and dry_run != self.dry_run:
            self.dry_run = dry_run
            if self.dry_run:
                self.state_path = self.raw_state_path.with_name("proactive_screen_dryrun_state.json")
            else:
                self.state_path = self.raw_state_path

    def _default_state(self) -> dict[str, Any]:
        return {
            "date": self._today_fn(),
            "count": 0,
            "last_trigger": 0.0,
            "last_request": 0.0,
            "consecutive_failures": 0,
            "paused_until_date": "",
        }

    def _load_state(self) -> dict[str, Any]:
        """读取状态，跨天自动重置，损坏自动回退。"""
        current_today = self._today_fn()
        state = self._default_state()

        if self.state_path.is_file():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    state.update(raw)
            except (OSError, ValueError, TypeError):
                # 文件损坏或不可读，使用默认状态
                pass

        # 规则 1：跨天重置 count 与熔断状态
        if state.get("date") != current_today:
            state["date"] = current_today
            state["count"] = 0
            state["consecutive_failures"] = 0
            state["paused_until_date"] = ""

        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        """原子写入状态文件（tmp 名带 PID，避免多实例并发写互相抢临时文件）。"""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            # 在 Windows/POSIX 上安全原子替换
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def allow(self) -> tuple[bool, str]:
        """判定当前是否允许发起主动识屏请求。

        规则判定顺序（手册 §4.3）：
        1. 跨天重置（由 _load_state 处理）；
        2. paused_until_date == today -> 拒绝（当日熔断）；
        3. count >= daily_cap -> 拒绝（达到每日上限）；
        4. now - last_request < min_request_interval_seconds -> 拒绝（请求间隔过短）；
        5. now - last_trigger < cooldown_minutes * 60 -> 拒绝（冷却中）；
        6. 否则放行。

        返回: (allowed: bool, reason: str)
        """
        state = self._load_state()
        now = self._clock()
        current_today = self._today_fn()

        if state.get("paused_until_date") == current_today:
            return False, "paused_by_circuit_breaker"

        daily_cap = int(self.cfg.get("daily_cap", 15))
        if int(state.get("count", 0)) >= daily_cap:
            return False, "daily_cap_reached"

        min_req_interval = float(self.cfg.get("min_request_interval_seconds", 60))
        last_req = float(state.get("last_request", 0.0))
        if (now - last_req) < min_req_interval:
            return False, "min_request_interval_cooldown"

        cooldown_sec = float(self.cfg.get("cooldown_minutes", 5)) * 60.0
        last_trig = float(state.get("last_trigger", 0.0))
        if (now - last_trig) < cooldown_sec:
            return False, "cooldown_active"

        return True, "ok"

    def record_attempt(self) -> None:
        """记录一次请求尝试（更新 last_request 时戳）。"""
        state = self._load_state()
        state["last_request"] = self._clock()
        self._save_state(state)

    def record_success(self) -> None:
        """记录一次成功的主动关怀（更新 count, last_trigger, last_request，清空失败计数）。"""
        state = self._load_state()
        now = self._clock()
        state["count"] = int(state.get("count", 0)) + 1
        state["last_trigger"] = now
        state["last_request"] = now
        state["consecutive_failures"] = 0
        self._save_state(state)

    def record_failure(self) -> bool:
        """记录一次请求失败。

        若连续失败次数达到 3 次，触发当日熔断（paused_until_date=today）。
        返回: 是否触发了当日熔断。
        """
        state = self._load_state()
        now = self._clock()
        state["last_request"] = now
        fails = int(state.get("consecutive_failures", 0)) + 1
        state["consecutive_failures"] = fails

        tripped = False
        if fails >= 3:
            state["paused_until_date"] = self._today_fn()
            tripped = True

        self._save_state(state)
        return tripped


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

        self.win = window
        self.cfg = config
        parent_obj = self.win if hasattr(self.win, "winId") else None
        self._bridge = _WatcherBridge(self, parent=parent_obj)
        self._bridge.frame_ready.connect(self._bridge._forward_frame)
        self._bridge.bubble_requested.connect(self._bridge._forward_bubble)

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
        should_run = (
            sys.platform == "win32"
            and eff["enabled"]
            and bool(eff["whitelist"])
        )
        # 无 Chat 变体（排除 pet.chat）的菜单/设置入口已隐藏，用户无法开启；
        # 即使手改 config 开启，真实请求会在 provider 解析阶段失败并计入熔断，
        # 不会崩溃。注意：这里不能按 on_open_chat 门控——app 是在 PetWindow 构造
        # 之后才注入回调的，构造期门控会导致定时器永远不起（首轮终审踩过的坑）。
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
                self._bridge.frame_ready.emit(img, app_str, info["hwnd"], cur_hash, payload)
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

            # G6 严格频控门禁二次确认
            ok, reason = self.limiter.allow()
            if not ok:
                return

            self._last_dhash = cur_hash

            # 判断是否处于 dry_run 模式
            if self.limiter.dry_run:
                import logging

                logging.info(
                    "主动识屏 [dry-run 模式]: 条件满足已触发! 前台: %s, 窗口: %s, dHash: %s",
                    app_str,
                    hwnd,
                    hex(cur_hash),
                )
                self.limiter.record_attempt()
                return

            # 真实模式：触发先兆提示 + 派发后台视觉请求
            self.limiter.record_attempt()
            # 先兆 → 模型答复整个窗口期内占用气泡位，自言自语让路（防连环顶掉）
            hold = getattr(self.win, "hold_bubble", None)
            if callable(hold):
                hold(30.0)
            if eff.get("pre_cue", True) and hasattr(self.win, "show_bubble"):
                self.win.show_bubble("让我看看……", duration_ms=2500)

            # 纯内存编码 JPEG bytes（严禁写临时文件）
            import io
            from PIL import Image

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
                args=(jpeg_bytes, app_str, system_prompt, provider, memory_ctx, proc_name, win_title, current_act),
                daemon=True,
                name="proactive-vision-requester",
            ).start()
        finally:
            self._worker_busy = False

    def _resolve_vision_provider(self, eff: dict) -> tuple[Any, str]:
        """解析视觉请求使用的 provider 与 system_prompt（手册 §6）。

        说明：provider 对象已在内部封装了 vision_same_as_chat、vision_base_url
        与 vision_api_key；在 _post_vision_request 中会根据 vision_same_as_chat
        自动决定是否使用独立视觉端点，此处无需切换外部对象。
        """
        chat_settings = self.cfg.chat_settings()
        provider = chat_settings.active_config
        provider.api_key = self.cfg.resolve_api_key(provider)
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
    ) -> None:
        """后台线程：发起大模型视觉请求，处理重试/熔断，并通过桥接信号在桌宠冒泡。"""
        from . import vision

        try:
            reply = vision._post_vision_request(
                jpeg_bytes, app_str, system_prompt, provider, memory_context=memory_ctx
            )
            if reply and reply.strip():
                # 按时长按内容长度缩放：太短读不完。6s 起步，每字 +150ms，封顶 20s
                duration = max(6000, min(20000, 4000 + len(reply) * 150))
                self._bridge.bubble_requested.emit(reply, duration)
                self.limiter.record_success()
                # 陪伴记忆只存 进程名+活动分类，不落窗口标题（可能含文档/网页敏感信息）
                if proc_name or current_act:
                    self.memory.record(proc_name, "", current_act)
            else:
                # 空回复视为失败（计入熔断，不冒泡、不写记忆）
                self.limiter.record_failure()
        except Exception as exc:
            import logging

            logging.warning("主动识屏请求失败: %s", exc)
            self.limiter.record_failure()
        finally:
            self._request_in_flight = False

