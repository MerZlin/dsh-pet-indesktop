# -*- coding: utf-8 -*-
"""主动识屏 Phase 1 纯函数与频控单元测试。

测试覆盖：
- 白名单匹配（空列表、大小写不敏感、通配符、title: 前缀、空串边界）；
- dHash 与 Hamming 距离计算（同图距离 0、异图距离大、范围 0~64）；
- ProactiveLimiter 频控门禁（跨天重置、熔断机制、每日上限、最小请求间隔、冷却、持久化、损坏回退、原子写入）；
- effective_proactive_config 预设与有效配置计算（默认值、预设切换、custom、clamp、require_idle=False 时 min_idle 归 0）；
- dwell_satisfied / idle_satisfied / should_watch 辅助函数边界。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from pet.config import Config
from pet.proactive import (
    DEFAULT_PROACTIVE_CONFIG,
    PRESET_DEFAULTS,
    ProactiveLimiter,
    build_sync_marker,
    dwell_satisfied,
    effective_proactive_config,
    hamming_distance,
    idle_satisfied,
    image_dhash,
    match_process_whitelist,
    should_watch,
)


# ============================================================================
# 1. 白名单匹配测试
# ============================================================================
class TestWhitelistMatching:
    def test_empty_rules_always_returns_false(self):
        assert match_process_whitelist([], "code.exe", "main.py - VSCode") is False
        assert match_process_whitelist(None, "code.exe", "main.py") is False

    def test_case_insensitive_matching(self):
        rules = ["CODE.EXE", "Title:*Bilibili*"]
        assert match_process_whitelist(rules, "code.exe", "") is True
        assert match_process_whitelist(rules, "Code.Exe", "") is True
        assert match_process_whitelist(rules, "chrome.exe", "哔哩哔哩_bilibili_视频") is True

    def test_wildcard_matching(self):
        rules = ["*studio*", "*steam*"]
        assert match_process_whitelist(rules, "devenv.exe", "Visual Studio 2022") is False
        assert match_process_whitelist(rules, "obs64.exe", "OBS Studio 30.0") is False
        assert match_process_whitelist(rules, "steam.exe", "Steam") is True
        assert match_process_whitelist(rules, "notepad.exe", "Untitled - Notepad") is False

    def test_plain_rule_matches_process_only(self):
        """无前缀规则仅匹配进程名，不匹配标题（隐私边界：标题匹配必须显式 title:）。"""
        rules = ["code.exe"]
        assert match_process_whitelist(rules, "code.exe", "whatever") is True
        assert match_process_whitelist(rules, "other.exe", "code.exe") is False

    def test_title_prefix_rule(self):
        rules = ["title:*会议*", "title:*Zoom*"]
        # title: 规则不应匹配进程名，只匹配标题
        assert match_process_whitelist(rules, "zoom.exe", "无标题") is False
        assert match_process_whitelist(rules, "wemeetapp.exe", "腾讯会议 - 项目讨论") is True
        assert match_process_whitelist(rules, "chrome.exe", "Zoom Workplace") is True

    def test_empty_process_or_title_or_rule(self):
        rules = ["", "  ", "code.exe", "title:"]
        assert match_process_whitelist(rules, "", "") is False
        assert match_process_whitelist(rules, "code.exe", "") is True
        assert match_process_whitelist(rules, "", "Visual Studio Code") is False


# ============================================================================
# 2. dHash 与 Hamming 距离测试
# ============================================================================
class TestImageDHashAndHamming:
    def test_same_image_zero_distance(self):
        img1 = Image.new("RGB", (200, 200), color=(100, 150, 200))
        img2 = Image.new("RGB", (200, 200), color=(100, 150, 200))
        h1 = image_dhash(img1)
        h2 = image_dhash(img2)
        assert hamming_distance(h1, h2) == 0

    def test_different_images_have_distance(self):
        # 创建一个左黑右白和一个全白的图
        img_gradient = Image.new("L", (100, 100))
        for x in range(100):
            for y in range(100):
                img_gradient.putpixel((x, y), x * 2)

        img_invert = Image.new("L", (100, 100))
        for x in range(100):
            for y in range(100):
                img_invert.putpixel((x, y), 255 - x * 2)

        h1 = image_dhash(img_gradient)
        h2 = image_dhash(img_invert)
        dist = hamming_distance(h1, h2)
        assert 0 <= dist <= 64
        assert dist > 10

    def test_supports_different_modes_and_sizes(self):
        # 兼容 RGBA, L, 1 等模式与不同尺寸
        img_rgba = Image.new("RGBA", (300, 150), (255, 0, 0, 128))
        img_l = Image.new("L", (50, 80), 120)
        h_rgba = image_dhash(img_rgba)
        h_l = image_dhash(img_l)
        assert isinstance(h_rgba, int)
        assert isinstance(h_l, int)


# ============================================================================
# 3. 频控 RateLimiter 测试
# ============================================================================
class TestProactiveLimiter:
    def test_first_call_allowed(self, tmp_path):
        state_file = tmp_path / "state.json"
        now = 10000.0
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 15, "min_request_interval_seconds": 60, "cooldown_minutes": 5},
            clock=lambda: now,
            today=lambda: "2026-08-27",
        )
        ok, reason = limiter.allow()
        assert ok is True
        assert reason == "ok"

    def test_cooldown_and_min_request_interval(self, tmp_path):
        state_file = tmp_path / "state.json"
        cur_time = [10000.0]
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 15, "min_request_interval_seconds": 60, "cooldown_minutes": 5},
            clock=lambda: cur_time[0],
            today=lambda: "2026-08-27",
        )
        limiter.record_success()

        # 立即检查：应被最小请求间隔拦截（60s）或冷却（300s）
        cur_time[0] += 30.0
        ok, reason = limiter.allow()
        assert ok is False
        assert reason == "min_request_interval_cooldown"

        # 过了 70s（大于 60s 最小请求间隔，但小于 300s 冷却）
        cur_time[0] = 10000.0 + 70.0
        ok, reason = limiter.allow()
        assert ok is False
        assert reason == "cooldown_active"

        # 过了 301s（超过 5 分钟冷却）
        cur_time[0] = 10000.0 + 301.0
        ok, reason = limiter.allow()
        assert ok is True
        assert reason == "ok"

    def test_daily_cap_and_cross_day_reset(self, tmp_path):
        state_file = tmp_path / "state.json"
        cur_time = [10000.0]
        cur_day = ["2026-08-27"]
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 2, "cooldown_minutes": 1, "min_request_interval_seconds": 10},
            clock=lambda: cur_time[0],
            today=lambda: cur_day[0],
        )

        # 第一次真实请求（消耗一次预算）
        limiter.consume_budget()
        cur_time[0] += 70.0
        # 第二次真实请求
        ok, _ = limiter.allow()
        assert ok is True
        limiter.consume_budget()

        # 达到每日上限（cap=2）
        cur_time[0] += 70.0
        ok, reason = limiter.allow()
        assert ok is False
        assert reason == "daily_cap_reached"

        # 跨天重置
        cur_day[0] = "2026-08-28"
        cur_time[0] += 100.0
        ok, reason = limiter.allow()
        assert ok is True
        assert reason == "ok"

    def test_circuit_breaker_trips_after_3_failures(self, tmp_path):
        state_file = tmp_path / "state.json"
        cur_time = [10000.0]
        cur_day = ["2026-08-27"]
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 10, "min_request_interval_seconds": 10, "cooldown_minutes": 1},
            clock=lambda: cur_time[0],
            today=lambda: cur_day[0],
        )

        assert limiter.record_failure() is False  # 1次失败
        cur_time[0] += 20.0
        assert limiter.record_failure() is False  # 2次失败
        cur_time[0] += 20.0
        assert limiter.record_failure() is True   # 3次失败 -> 熔断触发

        # 当天已被熔断暂停
        cur_time[0] += 200.0
        ok, reason = limiter.allow()
        assert ok is False
        assert reason == "paused_by_circuit_breaker"

        # 跨天后自动恢复
        cur_day[0] = "2026-08-28"
        ok, reason = limiter.allow()
        assert ok is True
        assert reason == "ok"

    def test_corrupted_state_file_fallback(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("NOT A JSON {corrupted", encoding="utf-8")
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 10},
            clock=lambda: 10000.0,
            today=lambda: "2026-08-27",
        )
        ok, reason = limiter.allow()
        assert ok is True
        assert reason == "ok"

    def test_wrong_field_type_state_falls_back_to_default(self, tmp_path):
        """缺陷 17：合法 JSON + 错字段类型必须整体回退默认状态。

        修前只判 ``isinstance(raw, dict)``：``{"count": null}`` 一路穿透，
        后续裸 ``int(state.get("count"))`` 抛 TypeError 打断调用链。
        last_request/last_trigger 故意写成"刚刚请求过"：只有**整体**回退默认
        状态才会放行，若只丢弃坏字段（半回退）就会被最小请求间隔拦下。
        """
        state_file = tmp_path / "state.json"
        now = 10000.0
        state_file.write_text(json.dumps({
            "date": "2026-08-27",
            "count": None,
            "last_request": now,
            "last_trigger": now,
        }), encoding="utf-8")
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 10, "min_request_interval_seconds": 60, "cooldown_minutes": 5},
            clock=lambda: now,
            today=lambda: "2026-08-27",
        )
        ok, reason = limiter.allow()
        assert (ok, reason) == (True, "ok"), "坏字段必须让整份状态回退默认"

    def test_wrong_field_type_uses_default_count_for_cap(self, tmp_path):
        """缺陷 17：count 为 null 时按默认 0 记（不是穿透 TypeError）。

        对照断言：同一份文件里 last_request=now（会被最小间隔拦），修后整体
        回退默认才返回 "ok"；修前 int(None) 直接抛 TypeError。
        """
        state_file = tmp_path / "state.json"
        day = "2026-08-27"
        state_file.write_text(json.dumps({
            "date": day, "count": None, "consecutive_failures": None,
        }), encoding="utf-8")
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 1, "min_request_interval_seconds": 60, "cooldown_minutes": 5},
            clock=lambda: 10000.0,
            today=lambda: day,
        )
        assert limiter.consume_budget() is True, "坏字段回退默认后仍可正常记账"
        assert json.loads(state_file.read_text(encoding="utf-8"))["count"] == 1

    def test_valid_state_file_still_honoured(self, tmp_path):
        """回归：字段类型合法的状态文件照常生效（修复不得把好文件一起打回默认）。"""
        state_file = tmp_path / "state.json"
        day = "2026-08-27"
        state_file.write_text(json.dumps({
            "date": day, "count": 3, "last_request": 10000.0, "last_trigger": 0.0,
            "consecutive_failures": 1, "paused_until_date": "",
        }), encoding="utf-8")
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 3, "min_request_interval_seconds": 60, "cooldown_minutes": 5},
            clock=lambda: 10000.0,
            today=lambda: day,
        )
        ok, reason = limiter.allow()
        assert (ok, reason) == (False, "daily_cap_reached")

    def test_consume_budget_fails_closed_when_state_unwritable(self, tmp_path, caplog):
        """缺陷 17：写盘失败时 consume_budget 不得放行（fail-closed）。

        放行 = 额度记不上账还照发请求，重启后计数回退，当日上限形同虚设。
        写盘失败用真实 OSError 造（状态文件路径被目录占住 → 原子替换必失败），
        不 mock 替换函数；同时断言失败路径不留 .tmp 且至少留一条 warning。
        """
        state_path = tmp_path / "state.json"
        state_path.mkdir()
        limiter = ProactiveLimiter(
            state_path,
            {"daily_cap": 10},
            clock=lambda: 10000.0,
            today=lambda: "2026-08-27",
        )
        with caplog.at_level(logging.WARNING):
            assert limiter.consume_budget() is False, "持久化失败不得放行"
        assert not list(tmp_path.glob("*.tmp")), "写盘失败不得残留 .tmp"
        assert "主动识屏状态写入失败" in caplog.text

    def test_rewrite_after_transient_failure_is_ok(self, tmp_path):
        """回归：写盘恢复正常后 consume_budget 立即恢复放行（失败不是终态）。"""
        state_path = tmp_path / "state.json"
        limiter = ProactiveLimiter(
            state_path,
            {"daily_cap": 10},
            clock=lambda: 10000.0,
            today=lambda: "2026-08-27",
        )
        assert limiter.consume_budget() is True
        assert json.loads(state_path.read_text(encoding="utf-8"))["count"] == 1
        assert not list(tmp_path.glob("*.tmp"))


# ============================================================================
# 4. 配置与预设有效性计算测试
# ============================================================================
class TestEffectiveConfig:
    def test_default_config(self):
        cfg = effective_proactive_config({})
        assert cfg["enabled"] is False
        assert cfg["preset"] == "balanced"
        assert cfg["dwell_seconds"] == 45
        assert cfg["cooldown_minutes"] == 5
        assert cfg["daily_cap"] == 15
        assert cfg["require_idle"] is False
        # require_idle=False 时 min_idle_seconds 应被计算为 0
        assert cfg["min_idle_seconds"] == 0

    def test_presets_override_values(self):
        quiet = effective_proactive_config({"preset": "quiet"})
        assert quiet["dwell_seconds"] == 90
        assert quiet["cooldown_minutes"] == 10
        assert quiet["daily_cap"] == 8

        active = effective_proactive_config({"preset": "active"})
        assert active["dwell_seconds"] == 20
        assert active["cooldown_minutes"] == 3
        assert active["daily_cap"] == 25

    def test_invalid_preset_fallback(self):
        cfg = effective_proactive_config({"preset": "invalid_preset_name"})
        assert cfg["preset"] == "balanced"
        assert cfg["dwell_seconds"] == 45

    def test_clamp_ranges(self):
        raw = {
            "preset": "custom",
            "dwell_seconds": 99999,      # 上限 600
            "cooldown_minutes": -5,      # 下限 0.5（0.5 分钟粒度）
            "daily_cap": 0,              # 下限 1
            "min_request_interval_seconds": 1, # 下限 30
            "change_threshold": 100,     # 上限 32
            "require_idle": True,
            "min_idle_seconds": 99999,   # 上限 3600
        }
        cfg = effective_proactive_config(raw)
        assert cfg["dwell_seconds"] == 600
        assert cfg["cooldown_minutes"] == 0.5
        assert cfg["daily_cap"] == 1
        assert cfg["min_request_interval_seconds"] == 30
        assert cfg["change_threshold"] == 32
        assert cfg["min_idle_seconds"] == 3600

    def test_config_integration(self, tmp_path):
        # 验证 pet.config.Config 能够正确存储与合并 proactive_screen
        cfg = Config(base=tmp_path)
        assert "proactive_screen" in cfg.data
        assert cfg.data["proactive_screen"]["enabled"] is False

        # 测试 3：断言 Config 的默认 proactive_screen 与 DEFAULT_PROACTIVE_CONFIG 逐值相等，防漂移
        assert cfg.data["proactive_screen"] == DEFAULT_PROACTIVE_CONFIG

        cfg.data["proactive_screen"]["enabled"] = True
        cfg.data["proactive_screen"]["whitelist"] = ["code.exe"]
        cfg.save()

        # 重新加载
        cfg_loaded = Config(base=tmp_path)
        assert cfg_loaded.data["proactive_screen"]["enabled"] is True
        assert cfg_loaded.data["proactive_screen"]["whitelist"] == ["code.exe"]


# ============================================================================
# 5. 辅助判定函数测试
# ============================================================================
class TestSyncMarker:
    def test_marker_contains_process_and_activity(self):
        marker = build_sync_marker("code.exe", "写代码")
        assert marker.startswith("[主动识屏]")
        assert "code.exe" in marker
        assert "写代码" in marker

    def test_marker_falls_back_for_unknown_process(self):
        marker = build_sync_marker("", "")
        assert "[主动识屏]" in marker
        assert "未知" in marker

    def test_marker_never_contains_window_title(self):
        # 隐私约定：同步标记与陪伴记忆一致，绝不落窗口标题
        marker = build_sync_marker("chrome.exe", "上网")
        assert "机密文档" not in marker


class TestHelperPredicates:
    def test_dwell_satisfied(self):
        assert dwell_satisfied(entered_ts=100.0, now=145.0, dwell_seconds=45.0) is True
        assert dwell_satisfied(entered_ts=100.0, now=144.9, dwell_seconds=45.0) is False
        assert dwell_satisfied(entered_ts=0.0, now=200.0, dwell_seconds=45.0) is False

    def test_idle_satisfied(self):
        assert idle_satisfied(last_input_seconds=30.0, min_idle_seconds=30.0) is True
        assert idle_satisfied(last_input_seconds=29.9, min_idle_seconds=30.0) is False

    def test_should_watch(self):
        # 基础状态
        assert should_watch(visible=True, interacting=False, mouse_through=False, allow_when_mouse_through=True) is True
        assert should_watch(visible=False, interacting=False, mouse_through=False, allow_when_mouse_through=True) is False
        assert should_watch(visible=True, interacting=True, mouse_through=False, allow_when_mouse_through=True) is False

        # 鼠标穿透边界
        assert should_watch(visible=True, interacting=False, mouse_through=True, allow_when_mouse_through=True) is True
        assert should_watch(visible=True, interacting=False, mouse_through=True, allow_when_mouse_through=False) is False


# ============================================================================
# 6. Phase 2 Win32 / Vision 扩展与 Watcher 生命周期测试
# ============================================================================
def _foreground_window_has_visible_payload() -> bool:
    """前台窗口是否满足「真调用能拿到信息」的真实前置条件。

    判据独立读一遍 Win32（**不经过产品函数**），与
    ``pet/vision.py::foreground_window_info`` 的早退分支同源：句柄非零 / 可见 /
    未最小化 / 未被 DWM cloak / 尺寸为正。

    为什么要它：真调用用例断言的是"有可见前台窗口时不该返回 None"，而桌面上
    完全可能没有这样的窗口（实测前台是 ``Chrome_WidgetWin_0`` 一类不可见宿主窗，
    如微信小程序宿主）——那一刻产品按自身契约就返回 None，断言什么都区分不出来。
    前置条件不成立时用例跳过并写明原因，不把断言放宽成恒真。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
        user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND,
                                         ctypes.POINTER(ctypes.wintypes.RECT)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False
        cloaked = ctypes.c_int(0)
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        if dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        ) == 0 and cloaked.value != 0:
            return False
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        return rect.right > rect.left and rect.bottom > rect.top
    except (AttributeError, OSError, TypeError, ValueError, RuntimeError):
        return False


class TestVisionAndWatcherPhase2:
    def test_foreground_app_info_backward_compatibility(self, monkeypatch):
        from pet import vision
        # mock foreground_window_info
        fake_info = {
            "hwnd": 12345,
            "pid": 6789,
            "process": "Code.exe",
            "title": "main.py - Visual Studio Code",
            "rect": (100, 100, 800, 600),
        }
        monkeypatch.setattr(vision, "foreground_window_info", lambda: fake_info)
        assert vision.foreground_app_info() == "Code.exe | main.py - Visual Studio Code"
        assert vision.get_foreground_window_rect() == (100, 100, 800, 600)

        # None 情况
        monkeypatch.setattr(vision, "foreground_window_info", lambda: None)
        assert vision.foreground_app_info() == ""
        assert vision.get_foreground_window_rect() is None

    def test_foreground_app_info_skips_pet_own_process(self, monkeypatch):
        """issue #366：前台是桌宠自己时不喂误导元数据。

        点击/右键桌宠触发识屏时桌宠窗口抢到前台，随截图喂给模型的
        「前台窗口：桌宠自己」只会让 AI 谈论"我自己的窗口"。桌宠全部窗口
        同进程（4.4b 多进程层已退役），pid 命中即视为无前台上下文。
        """
        from pet import vision
        own = {
            "hwnd": 1,
            "pid": os.getpid(),
            "process": "dsh-pet-standalone-webm-chat.exe",
            "title": "dsh-pet 桌宠",
            "rect": (0, 0, 100, 100),
        }
        monkeypatch.setattr(vision, "foreground_window_info", lambda: own)
        assert vision.foreground_app_info() == ""

    def test_get_system_idle_seconds_non_windows(self, monkeypatch):
        import sys
        from pet import vision
        monkeypatch.setattr(sys, "platform", "linux")
        assert vision.get_system_idle_seconds() == 0.0

    def test_watcher_lifecycle_and_conditions(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        # 主动识屏 v1 仅 Windows（apply_config 有平台守卫）；Linux/macOS CI 上
        # 固定平台为 win32，保证该用例跨平台确定性。
        import sys
        monkeypatch.setattr(sys, "platform", "win32")

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            def __init__(self):
                self._visible = True
                self.mouse_through = False
                self._dragging = False
                self._physics_mode = None
                self._click_effect_phase = 0

            def isVisible(self):
                return self._visible

        cfg = Config(base=tmp_path)
        win = DummyWindow()

        # 默认：enabled=False, whitelist=[] -> 不启动定时器
        watcher = ProactiveScreenWatcher(win, cfg)
        assert watcher.is_running() is False

        # enabled=True 但 whitelist=[] -> 依然不启动定时器 (验收 #3)
        cfg.set("proactive_screen", {"enabled": True, "whitelist": []})
        watcher.apply_config()
        assert watcher.is_running() is False

        # enabled=True 且 whitelist 非空 -> 启动定时器
        cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"]})
        watcher.apply_config()
        assert watcher.is_running() is True

        # 窗口隐藏/pause -> 停止
        watcher.pause()
        assert watcher.is_running() is False

        # 恢复 -> 重新评估启动
        watcher.resume()
        assert watcher.is_running() is True

    def test_foreground_window_info_real_call_no_shadow_bug(self):
        """回归：函数体内局部 import ctypes.wintypes 曾让 ctypes 变成局部变量，
        函数开头的 ctypes.windll 访问抛 UnboundLocalError 并被 except 吞掉，
        导致永远返回 None（mock 测试全覆盖时该 bug 完全隐形）。

        真调用只在**真实前置条件**成立时才断言：桌面上得有一个可见、未最小化、
        未被 cloak、尺寸为正的前台窗口（``_foreground_window_has_visible_payload``
        独立读一遍判据）。不成立时产品按自身契约就返回 None，断言无从区分
        shadow bug——跳过并写明原因，断言本身一个字都不放宽。
        """
        import sys
        if sys.platform != "win32":
            pytest.skip("仅 Windows 可真实调用")
        if not _foreground_window_has_visible_payload():
            pytest.skip("当前前台窗口不满足真调用前置条件（不可见/最小化/cloaked/无尺寸）"
                        "——该输入下返回 None 是契约本身，判别不了 shadow bug")
        from pet import vision
        info = vision.foreground_window_info()
        assert info is not None
        assert set(info.keys()) == {"hwnd", "pid", "process", "title", "rect"}

    def test_watcher_starts_before_first_show(self, tmp_path, monkeypatch):
        """回归：PetWindow 构造时窗口尚未显示（isVisible=False），若 apply_config 以
        isVisible() 作为启动条件，定时器将永远不起——showEvent 只在曾经隐藏过
        （_hidden_paused=True）时才调 _resume_activity。可见性应由 _on_tick 的
        G1 逐 tick 判定。"""
        import sys
        monkeypatch.setattr(sys, "platform", "win32")
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            mouse_through = False
            _dragging = False
            _physics_mode = None
            _click_effect_phase = 0

            def isVisible(self):
                return False  # 模拟构造期窗口未显示

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"]})
        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)
        assert watcher.is_running() is True
        watcher.pause()

    def test_frame_ready_signal_carries_64bit_hash(self, tmp_path):
        """回归：dHash 是 64 位整数、hwnd 也可能超出 32 位有符号范围，
        Signal(int) 会触发 libshiboken Overflow 导致值截断/投递失败，
        必须用 object 传参保证原值到达槽函数。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            mouse_through = False
            _dragging = False
            _physics_mode = None
            _click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        # dry_run 让 _forward_frame → _on_frame_ready 链路不触碰 img（None）
        cfg.set("proactive_screen", {"dry_run": True})
        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)

        received = []
        watcher._bridge.frame_ready.connect(lambda *args: received.append(args))
        big_hash = (1 << 63) + 12345
        big_hwnd = 2026619832
        watcher._bridge.frame_ready.emit(None, "p | t", big_hwnd, big_hash, {})
        assert received, "frame_ready 信号未投递"
        assert received[0][2] == big_hwnd
        assert received[0][3] == big_hash

    def test_capture_window_rect_coordinates_and_clamping(self, monkeypatch):
        import ctypes
        import types

        from pet import vision
        from PIL import Image

        # 创建一个 1000x1000 的虚拟屏幕
        fake_all_screen = Image.new("RGB", (1000, 1000), color=(50, 100, 150))
        # vision 的 PIL 已下沉为函数内懒导入，直接 patch PIL.ImageGrab 本体
        # （与原先 patch vision.ImageGrab 语义一致：同一模块对象）
        monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=True: fake_all_screen)
        # 虚拟屏原点固定成 (0,0)：这条用例测的是「全局坐标 → 画布坐标」的裁剪，
        # 不是这台机器的显示器布局。真有左侧/上方副屏时 (76)/(77) 是负数，
        # 用例里的 (100,100) 会被平移出 1000x1000 画布、裁剪成 0 宽而假红。
        monkeypatch.setattr(
            ctypes,
            "windll",
            types.SimpleNamespace(user32=types.SimpleNamespace(GetSystemMetrics=lambda _i: 0)),
            raising=False,
        )

        # 模拟抓取有效区域 (100, 100, 200, 200)
        img = vision.capture_window_rect((100, 100, 200, 200))
        assert img is not None
        assert img.size == (200, 200)

        # 模拟超出边界的区域自动 clamp
        img_clamped = vision.capture_window_rect((900, 900, 300, 300))
        assert img_clamped is not None
        assert img_clamped.size == (100, 100)

        # 无效尺寸
        assert vision.capture_window_rect((0, 0, 0, 0)) is None
        assert vision.capture_window_rect(None) is None

    def test_log_mode_does_not_consume_daily_quota(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from pet import vision
        from PIL import Image

        app = QApplication.instance() or QApplication([])

        # 确保零真实网络调用
        monkeypatch.setattr(vision, "_post_vision_request", lambda *a, **kw: "mock")

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            def __init__(self):
                self.mouse_through = False
                self._dragging = False
                self._physics_mode = None
                self._click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {
            "enabled": True,
            "dry_run": True,  # 显式指定 dry_run 模式
            "whitelist": ["code.exe"],
            "daily_cap": 15,
            "dwell_seconds": 0,
        })
        win = DummyWindow()
        watcher = ProactiveScreenWatcher(win, cfg)
        assert watcher.limiter.dry_run is True

        # 触发 _on_frame_ready
        fake_img = Image.new("RGB", (100, 100), (10, 20, 30))
        watcher._on_frame_ready(fake_img, "code.exe | test", 12345, 0x12345678)

        # 真实状态文件应当不存在
        real_state_file = tmp_path / "dsh-pet-standalone" / "proactive_screen_state.json"
        assert not real_state_file.exists()

        # dry_run 状态文件已创建
        dry_state_file = tmp_path / "dsh-pet-standalone" / "proactive_screen_dryrun_state.json"
        assert dry_state_file.exists()

        # 检查 dry_run limiter 状态，count 保持为 0（dry_run 下只记 attempt，不记真实 success）
        state = watcher.limiter._load_state()
        assert state["count"] == 0
        assert state["last_trigger"] == 0.0

    def test_physics_mode_blocks_watcher_tick(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            def __init__(self):
                self.mouse_through = False
                self._dragging = False
                self._physics_mode = "spring"  # 模拟正在进行物理拖拽/抛掷
                self._click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {
            "enabled": True,
            "whitelist": ["code.exe"],
        })
        win = DummyWindow()
        watcher = ProactiveScreenWatcher(win, cfg)

        # 监测是否有 worker 被触发
        worker_called = []
        monkeypatch.setattr(watcher, "_worker_capture", lambda *a, **kw: worker_called.append(True))

        watcher._on_tick()
        assert len(worker_called) == 0  # 物理模式下应被 G1 守卫拦截


# ============================================================================
# 7. Phase 3 视觉链路重构、dry_run 模式与 Provider 选择测试
# ============================================================================
class TestPhase3VisionLinkAndDryRun:
    def test_ask_about_screen_and_post_vision_request_equivalence(self, tmp_path, monkeypatch):
        from pet import vision
        import json

        fake_resp = {
            "choices": [{
                "message": {"content": "主人正在认真写代码呢～"},
                "finish_reason": "stop"
            }]
        }

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, *args):
                return json.dumps(fake_resp).encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: FakeResponse())

        from pet.chat.models import ProviderConfig
        p = ProviderConfig.from_dict("test", {"model": "deepseek-v4-flash", "api_key": "sk-123"})

        # 生成一张真实临时图
        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (100, 100), (20, 40, 60))
        img.save(img_path, "JPEG")

        # 1. 传统文件路径调用
        res1 = vision.ask_about_screen(img_path, "code.exe | main.py", "sys_prompt", p)
        assert res1 == "主人正在认真写代码呢～"

        # 2. 新 bytes 内部函数调用
        res2 = vision._post_vision_request(img_path.read_bytes(), "code.exe | main.py", "sys_prompt", p)
        assert res2 == "主人正在认真写代码呢～"

    def test_limiter_dry_run_mode(self, tmp_path):
        state_file = tmp_path / "state.json"
        cur_time = [10000.0]
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 2, "min_request_interval_seconds": 60},
            dry_run=True,
            clock=lambda: cur_time[0],
            today=lambda: "2026-08-27",
        )

        assert limiter.dry_run is True
        ok, reason = limiter.allow()
        assert ok is True

        # dry_run 记录尝试
        limiter.record_attempt()

        # 真实状态文件应当不被创建或保持为 0
        real_state_file = tmp_path / "state.json"
        assert not real_state_file.exists()

        # dry_run 状态文件已创建
        dry_state_file = tmp_path / "proactive_screen_dryrun_state.json"
        assert dry_state_file.exists()

        # 最小请求间隔依然在 dry-run 中生效
        cur_time[0] += 30.0
        ok, reason = limiter.allow()
        assert ok is False
        assert reason == "min_request_interval_cooldown"

    def test_provider_resolution_strategy(self, tmp_path):
        from pet.proactive import ProactiveScreenWatcher
        from pet.chat.models import ChatSettings, ProviderConfig

        cfg = Config(base=tmp_path)
        win = None
        watcher = ProactiveScreenWatcher(win, cfg)

        # 1. 默认情况：vision_same_as_chat=True -> 使用聊天 provider
        eff = effective_proactive_config({"prefer_free_provider": True})
        p, _ = watcher._resolve_vision_provider(eff)
        assert p.model == "deepseek-v4-flash"

        # 2. 勾选 prefer_free_provider 且配置独立 GLM 视觉
        chat_data = cfg.data["chat"]
        chat_data["providers"]["openai-main"]["vision_same_as_chat"] = False
        chat_data["providers"]["openai-main"]["vision_model"] = "glm-4.6v-flash"
        chat_data["providers"]["openai-main"]["vision_base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        cfg.data["chat"] = chat_data

        p2, _ = watcher._resolve_vision_provider(eff)
        assert p2.vision_model == "glm-4.6v-flash"
        assert p2.vision_same_as_chat is False

    def test_watcher_real_mode_vision_pipeline(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from PIL import Image

        app = QApplication.instance() or QApplication([])

        bubbles = []
        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            def __init__(self):
                self.mouse_through = False
                self._dragging = False
                self._physics_mode = None
                self._click_effect_phase = 0

            def isVisible(self):
                return True

            def show_bubble(self, text, duration_ms=3000):
                bubbles.append(text)

        # mock vision._post_vision_request（模拟真实请求消耗一次预算）
        from pet import vision

        def _fake_post(jpeg_bytes, app_str, prompt, p, memory_context="", consume_budget=None, pet_name=""):
            if consume_budget is not None:
                consume_budget()
            return "看到你在写 Python 呢！"

        monkeypatch.setattr(vision, "_post_vision_request", _fake_post)

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {
            "enabled": True,
            "whitelist": ["code.exe"],
            "pre_cue": True,
        })
        win = DummyWindow()
        watcher = ProactiveScreenWatcher(win, cfg)
        watcher.limiter.dry_run = False  # 真实模式

        fake_img = Image.new("RGB", (100, 100), (10, 20, 30))

        # _on_frame_ready 会派发真实后台线程调模型；测试中改为同步执行，
        # 否则线程与主线程对 limiter 状态的读-改-写 + 原子替换互相覆盖，
        # 偶发 count!=1（flaky）。同步化后 _on_frame_ready → 记忆上下文 →
        # 视觉请求 → record_success 的完整链路被确定性地覆盖。
        import threading as _threading

        class _SyncThread:
            def __init__(self, target=None, args=(), **kwargs):
                self._target = target
                self._args = args

            def start(self):
                if self._target is not None:
                    self._target(*self._args)

        monkeypatch.setattr(_threading, "Thread", _SyncThread)

        watcher._on_frame_ready(fake_img, "code.exe | test", 12345, 0x12345678)
        app.processEvents()

        # 验证先兆提示与大模型气泡
        assert "让我看看……" in bubbles
        assert "看到你在写 Python 呢！" in bubbles

        # 验证 limiter 成功记录了一次真实额度
        state = watcher.limiter._load_state()
        assert state["count"] == 1
        assert state["last_trigger"] > 0.0


# ============================================================================
# 8. Phase 4 UI 与设置集成测试
# ============================================================================
class TestPhase4UIAndMenuIntegration:
    def test_window_menu_proactive_and_agent_toggles(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        # mock QMessageBox 避免弹窗阻塞
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        # 禁止测试写入真实 ~/.claude/settings.json（旧版曾污染真实配置，见终审记录）
        from pet.agent_link import ClaudeCodeMonitor
        monkeypatch.setattr(ClaudeCodeMonitor, "install_hooks", lambda f: True)

        # 触发菜单事件中的开关逻辑
        win._toggle_proactive_enabled(True)
        assert cfg.data["proactive_screen"]["enabled"] is True
        assert win.proactive_watcher.is_running() is False  # 白名单为空依然不运行定时器

        win._set_proactive_option("allow_when_mouse_through", False)
        assert cfg.data["proactive_screen"]["allow_when_mouse_through"] is False

        win._toggle_agent_link("claude", True)
        assert cfg.data["agent_link"]["claude"] is True
        # 窗口必须关闭：泄漏的真实窗口会在共享事件循环上继续推进动画链
        win.close()
        win.deleteLater()

    def test_context_menu_proactive_build_no_name_error(self, tmp_path):
        """右键菜单（上游模板系统）构建不抛错，且包含主动识屏与 Agent 联动。"""
        from PySide6.QtWidgets import QApplication, QMenu
        from pet.window import PetWindow
        from pet.library import MovieLibrary
        from pet.context_menu import populate_context_menu

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)
        win.on_open_chat = lambda: None  # 有聊天能力才显示主动识屏入口
        win.on_open_settings = lambda: None

        menu = QMenu()
        populate_context_menu(menu, win)
        texts = []
        for a in menu.actions():
            texts.append(a.text())
            if a.menu():
                texts.extend(x.text() for x in a.menu().actions())
        import sys
        if sys.platform == "win32":
            assert any("主动识屏" in t for t in texts)
        assert any("Agent 联动" in t for t in texts)
        win.close()
        win.deleteLater()

    def test_enable_with_empty_whitelist_bubble_hint(self, tmp_path, monkeypatch):
        """测试 4a：白名单为空时开启主动识屏，应提示「白名单还是空的」气泡。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        bubbles = []
        monkeypatch.setattr(win, "show_bubble", lambda text, duration_ms=3200: bubbles.append((text, duration_ms)))

        # 白名单为空（默认），开启后应给出「白名单还是空的」提示
        win._toggle_proactive_enabled(True)
        assert cfg.data["proactive_screen"]["enabled"] is True
        assert win.proactive_watcher.is_running() is False
        assert any("白名单" in text for text, _ in bubbles)
        win.close()
        win.deleteLater()

    def test_agent_link_toggle_bubble_notice(self, tmp_path, monkeypatch):
        """测试 4c：开启 Agent 联动应气泡提示「后续版本实装」。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)

        bubbles = []
        monkeypatch.setattr(win, "show_bubble", lambda text, duration_ms=3200: bubbles.append((text, duration_ms)))

        # DSH 开启需授权确认 + 安装桥接插件：mock 掉弹窗与真实 dsh CLI 调用
        from PySide6.QtWidgets import QMessageBox
        from pet.agent_link import DshMonitor
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(DshMonitor, "install_bridge", classmethod(lambda cls: (True, "ok")))
        monkeypatch.setattr(DshMonitor, "uninstall_bridge", classmethod(lambda cls: None))

        win._toggle_agent_link("dsh", True)
        # 安装走后台线程：等 install_finished 信号回来再断言
        import time
        for _ in range(60):
            app.processEvents()
            if cfg.data["agent_link"]["dsh"]:
                break
            time.sleep(0.05)
        assert cfg.data["agent_link"]["dsh"] is True
        assert any(("DSH" in text) or ("联动" in text) or ("实装" in text) for text, _ in bubbles)

        # 关闭不强制要求气泡，仅需状态落盘
        win._toggle_agent_link("dsh", False)
        assert cfg.data["agent_link"]["dsh"] is False
        win.close()
        win.deleteLater()
    def test_apply_config_non_windows_no_timer(self, tmp_path, monkeypatch):
        """测试 4d：非 Windows 平台即使 enabled=True 且白名单非空也不起动定时器。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True  # 模拟有聊天/视觉能力（否则主动识屏 watcher 不启动）
            def __init__(self):
                self.mouse_through = False
                self._dragging = False
                self._physics_mode = None
                self._click_effect_phase = 0

            def isVisible(self):
                return True

        # 仅作用于本次调用：替换 proactive 模块内的 sys 引用（不改全局 sys.platform）
        monkeypatch.setattr("pet.proactive.sys", SimpleNamespace(platform="darwin"))

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"]})

        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)
        watcher.apply_config()
        assert watcher.is_running() is False

# ============================================================================
# 9. Phase 5 短期记忆分类与注入测试
# ============================================================================
class TestPhase5ShortTermMemory:
    def test_classify_activity_branches(self):
        from pet.proactive import classify_activity

        assert classify_activity("Code.exe", "main.py - VSCode") == "写代码"
        assert classify_activity("pycharm64.exe", "my_project") == "写代码"
        assert classify_activity("chrome.exe", "【4K】猫咪日常 - 哔哩哔哩_bilibili") == "看视频"
        assert classify_activity("potplayer64.exe", "movie.mkv") == "看视频"
        assert classify_activity("WINWORD.EXE", "工作报告.docx - Word") == "办公或看文档"
        assert classify_activity("msedge.exe", "关于项目进展的说明.pdf") == "办公或看文档"
        assert classify_activity("Steam.exe", "Steam 社区") == "打游戏"
        assert classify_activity("LeagueClient.exe", "英雄联盟") == "打游戏"
        assert classify_activity("chrome.exe", "百度一下，你就知道") == "上网"
        assert classify_activity("", "") == "桌面上"
        assert classify_activity("cmd.exe", "命令提示符") == "在电脑前"

    def test_proactive_memory_persist_and_prune(self, tmp_path):
        from pet.proactive import ProactiveMemory

        mem_file = tmp_path / "memory.json"
        cur_time = [1000.0]
        mem = ProactiveMemory(mem_file, clock=lambda: cur_time[0], max_entries=3)

        assert mem.load() == []
        assert mem.latest() is None

        # 记录 1
        mem.record("Code.exe", "main.py", "写代码")
        assert mem.latest()["activity"] == "写代码"

        # 记录超过 max_entries (3条)
        cur_time[0] += 10.0
        mem.record("chrome.exe", "bilibili", "看视频")
        cur_time[0] += 10.0
        mem.record("steam.exe", "game", "打游戏")
        cur_time[0] += 10.0
        mem.record("msedge.exe", "doc.pdf", "办公或看文档")

        entries = mem.load()
        assert len(entries) == 3
        # 最新在最前
        assert entries[0]["activity"] == "办公或看文档"
        assert entries[1]["activity"] == "打游戏"
        assert entries[2]["activity"] == "看视频"

        # 清除记忆
        mem.clear()
        assert mem.load() == []

    def test_memory_never_persists_window_title(self, tmp_path):
        """#206：docstring 与实现矛盾的收口——title 参数只作签名兼容，绝不落盘。"""
        from pet.proactive import ProactiveMemory

        mem = ProactiveMemory(tmp_path / "memory.json")
        mem.record("Code.exe", "工作汇报机密.docx - Word", "办公或看文档")

        entry = mem.latest()
        assert entry["process"] == "Code.exe"
        assert entry["activity"] == "办公或看文档"
        assert "title" not in entry
        assert "机密" not in json.dumps(mem.load(), ensure_ascii=False)

    def test_memory_write_goes_through_atomic_replace_with_unique_tmp(self, tmp_path, monkeypatch):
        """#196：落盘必须 temp（名带 PID，多实例不抢同一临时文件）+ 原子替换。"""
        from pet.proactive import memory as pm

        mem_file = tmp_path / "memory.json"
        mem = pm.ProactiveMemory(mem_file)
        seen: list[tuple[Path, Path]] = []
        real = pm.atomic_replace_with_retry

        def spy(temp, target, attempts=5):
            seen.append((Path(temp), Path(target)))
            real(temp, target, attempts)

        monkeypatch.setattr(pm, "atomic_replace_with_retry", spy)
        mem.record("Code.exe", "main.py", "写代码")

        assert seen, "记忆落盘必须经原子替换（不得直写最终路径）"
        temp, target = seen[0]
        assert target == mem_file
        assert temp != mem_file and temp.parent == tmp_path
        assert temp.name.endswith(".tmp") and temp != pm.lock_path_for(mem_file), \
            f"临时文件必须同目录且不是旁路锁文件：{temp}"
        assert json.loads(mem_file.read_text(encoding="utf-8"))["entries"][0]["process"] == "Code.exe"
        assert list(tmp_path.glob("*.tmp")) == [], "成功路径不得残留 .tmp"

    def test_memory_write_fsyncs_before_replace(self, tmp_path, monkeypatch):
        """#204：替换前必须 fsync——否则掉电/崩溃后 rename 已生效而内容还在页缓存。"""
        from pet.proactive import memory as pm

        synced: list[int] = []
        real_fsync = os.fsync

        def spy_fsync(fd):
            synced.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr(pm.os, "fsync", spy_fsync)
        pm.ProactiveMemory(tmp_path / "memory.json").record("Code.exe", "main.py", "写代码")

        assert synced, "落盘必须 fsync 文件描述符后再替换"

    def test_memory_write_failure_is_logged_not_swallowed(self, tmp_path, monkeypatch, caplog):
        """#196：写盘失败不得静默吞——记 warning，且旧内容完整保留、不留 .tmp。"""
        from pet.proactive import memory as pm

        mem_file = tmp_path / "memory.json"
        old = {"entries": [{"ts": 1.0, "process": "old.exe", "activity": "上网"}]}
        mem_file.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

        def deny(temp, target, attempts=5):
            raise OSError("replace denied")

        monkeypatch.setattr(pm, "atomic_replace_with_retry", deny)
        with caplog.at_level(logging.WARNING):
            pm.ProactiveMemory(mem_file).record("Code.exe", "main.py", "写代码")

        assert "记忆" in caplog.text or "proactive" in caplog.text.lower(), \
            "写盘失败必须留下可发现的日志，不能 except OSError: pass"
        assert json.loads(mem_file.read_text(encoding="utf-8")) == old, \
            "替换失败时旧内容必须完整保留（不得半写/覆盖）"
        assert list(tmp_path.glob("*.tmp")) == [], "失败路径同样不得残留 .tmp"

    def test_memory_lock_is_shared_per_path_inside_process(self, tmp_path):
        """#196：同进程内同一路径共用一把锁——设置页清理与主动记录是两个实例。"""
        from pet.proactive import memory as pm

        path = tmp_path / "memory.json"
        first = pm.ProactiveMemory(path)
        second = pm.ProactiveMemory(path)
        other = pm.ProactiveMemory(tmp_path / "other.json")

        assert pm._lock_for(path) is first._lock
        assert pm._lock_for(path) is pm._lock_for(tmp_path / "memory.json"), \
            "同一路径必须复用同一把锁（弱引用注册表下也要稳定）"
        assert pm._lock_for(path) is not pm._lock_for(tmp_path / "other.json")
        assert first is not second

    def test_concurrent_records_lose_no_entries(self, tmp_path, monkeypatch):
        """#196：并发 record 的 load→改→写必须整体互斥，不得丢写。

        实测口径（本机复刻同一并发模式、去掉锁）：4×10 次里只剩 10 条，且旧
        实现的 ``except OSError: pass`` 把 tmp 抢写/替换冲突（WinError 5）全吞掉。
        这里用 Event 同步构造真实重叠窗口（不赌 sleep），并直接断言替换过程
        的最大并发度为 1——互斥是断言出来的，不是靠时序巧合。
        """
        import threading

        from pet.proactive import memory as pm

        mem_file = tmp_path / "memory.json"
        writers = [pm.ProactiveMemory(mem_file, max_entries=200) for _ in range(4)]
        real = pm.atomic_replace_with_retry
        gate = threading.Event()
        started = threading.Event()
        counter = threading.Lock()
        stats = {"calls": 0, "live": 0, "max_live": 0}

        def slow_replace(temp, target, attempts=5):
            with counter:
                stats["calls"] += 1
                stats["live"] += 1
                stats["max_live"] = max(stats["max_live"], stats["live"])
            try:
                # 第一个进入替换的线程把写窗口撑开：给其他线程足够机会抢 load
                if not started.is_set():
                    started.set()
                    gate.wait(5.0)
                real(temp, target, attempts)
            finally:
                with counter:
                    stats["live"] -= 1

        monkeypatch.setattr(pm, "atomic_replace_with_retry", slow_replace)

        def worker(mem, tag):
            for i in range(10):
                mem.record(f"{tag}.exe", "t", "上网")

        threads = [threading.Thread(target=worker, args=(m, f"w{i}")) for i, m in enumerate(writers)]
        for t in threads:
            t.start()
            if started.is_set():
                gate.set()  # 窗口只开第一个线程，其余必须堵在锁上
        for t in threads:
            t.join(30)
        gate.set()
        assert all(not t.is_alive() for t in threads), "并发 record 不得死锁"

        assert stats["calls"] == 40, f"每次 record 恰好一次替换，实得 {stats['calls']}"
        assert stats["max_live"] == 1, \
            f"读→改→写必须整体互斥，替换并发度实得 {stats['max_live']}"
        entries = pm.ProactiveMemory(mem_file).load()
        assert len(entries) == 40, f"4 线程 × 10 条不得丢写，实得 {len(entries)}"

    def test_build_memory_context(self):
        from pet.proactive import build_memory_context

        # 上次为空
        assert build_memory_context(None, "写代码") is None

        # 上次与当前相同
        last = {"activity": "写代码"}
        assert build_memory_context(last, "写代码") is None

        # 上次与当前不同
        last2 = {"activity": "写代码"}
        ctx = build_memory_context(last2, "看视频")
        assert ctx == "上次看到你在写代码，这次看到你在看视频。"

    def test_watcher_real_mode_memory_injection(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from PIL import Image

        app = QApplication.instance() or QApplication([])

        recorded_contexts = []
        from pet import vision

        def _fake_post(jpeg_bytes, app_str, prompt, p, memory_context="", consume_budget=None, pet_name=""):
            recorded_contexts.append(memory_context)
            return "好呀"

        monkeypatch.setattr(vision, "_post_vision_request", _fake_post)

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {
            "enabled": True,
            "whitelist": ["*"],
            "pre_cue": False,
        })
        win = None
        watcher = ProactiveScreenWatcher(win, cfg)
        watcher.limiter.dry_run = False

        # 先向记忆库塞一条历史记录（上次在写代码）
        watcher.memory.record("Code.exe", "main.py", "写代码")

        # 触发当前是看视频
        fake_img = Image.new("RGB", (100, 100), (10, 20, 30))
        fake_info = {"process": "chrome.exe", "title": "bilibili 视频", "hwnd": 999}
        watcher._worker_request_vision(
            b"fake_jpeg",
            "chrome.exe | bilibili",
            "prompt",
            watcher.cfg.chat_settings().active_config,
            memory_ctx="上次看到你在写代码，这次看到你在看视频。",
            proc_name="chrome.exe",
            win_title="bilibili 视频",
            current_act="看视频",
        )

        # 验证 memory_context 成功传递
        assert len(recorded_contexts) == 1
        assert "上次看到你在写代码，这次看到你在看视频。" in recorded_contexts[0]

        # 验证成功调用后记忆库新增了当前记录
        latest = watcher.memory.latest()
        assert latest["activity"] == "看视频"
        assert latest["process"] == "chrome.exe"

    def test_worker_vision_uses_character_alias_as_pet_name(self, tmp_path, monkeypatch):
        """主动识屏请求的身份提示用用户重命名后的别名，而不是默认角色名。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from pet import vision

        QApplication.instance() or QApplication([])

        recorded_names = []

        def _fake_post(jpeg_bytes, app_str, prompt, p, memory_context="", consume_budget=None, pet_name=""):
            recorded_names.append(pet_name)
            return "好呀"

        monkeypatch.setattr(vision, "_post_vision_request", _fake_post)

        cfg = Config(base=tmp_path)
        cfg.set_character_alias("shenshen", "小鲸鱼")
        watcher = ProactiveScreenWatcher(None, cfg)
        watcher.limiter.dry_run = False

        watcher._worker_request_vision(
            b"fake_jpeg",
            "chrome.exe | 页面",
            "prompt",
            cfg.chat_settings().active_config,
        )

        assert recorded_names == ["小鲸鱼"]







class TestUXFixesRound3:
    def test_cooldown_allows_half_minute_granularity(self):
        """冷却间隔支持 0.5 分钟粒度（用户反馈整分钟太粗）。"""
        from pet.proactive import effective_proactive_config
        cfg = effective_proactive_config({"preset": "custom", "cooldown_minutes": 2.5})
        assert cfg["cooldown_minutes"] == 2.5
        # 下限 clamp 到 0.5
        cfg2 = effective_proactive_config({"preset": "custom", "cooldown_minutes": 0})
        assert cfg2["cooldown_minutes"] == 0.5

    def test_min_request_interval_exposed_and_clamped(self):
        from pet.proactive import effective_proactive_config
        cfg = effective_proactive_config({"preset": "custom", "min_request_interval_seconds": 45})
        assert cfg["min_request_interval_seconds"] == 45
        cfg2 = effective_proactive_config({"preset": "custom", "min_request_interval_seconds": 1})
        assert cfg2["min_request_interval_seconds"] == 30

    def test_self_talk_yields_to_important_bubble(self, tmp_path):
        """重要气泡占用期间，自言自语必须让路。"""
        from PySide6.QtWidgets import QApplication
        from pet.window import PetWindow
        from pet.library import MovieLibrary
        import time

        app = QApplication.instance() or QApplication([])
        cfg = Config(base=tmp_path)
        cfg.set("self_talk_enabled", True)
        lib = MovieLibrary(character_id="shenshen")
        win = PetWindow(lib, cfg)
        win.show()
        app.processEvents()

        shown = []
        # 上游新结构：自言自语经 _show_random_self_talk 显示（返回是否真实显示）
        win._show_random_self_talk = lambda: shown.append(1) or True

        # 无占用时自言自语正常显示
        win._on_self_talk_timeout()
        assert len(shown) == 1

        # 占用期间自言自语跳过
        win.hold_bubble(60.0)
        win._on_self_talk_timeout()
        assert len(shown) == 1  # 没有新增

        # 占用过期后恢复
        win._bubble_busy_until = time.monotonic() - 1  # 字段自审查修复起为单调时钟
        win._on_self_talk_timeout()
        assert len(shown) == 2
        win.close()

    def test_daily_cap_upper_relaxed(self):
        """每日上限取消 100 硬顶：用户自定义可达 9999。"""
        from pet.proactive import effective_proactive_config
        cfg = effective_proactive_config({"preset": "custom", "daily_cap": 500})
        assert cfg["daily_cap"] == 500
        cfg2 = effective_proactive_config({"preset": "custom", "daily_cap": 99999})
        assert cfg2["daily_cap"] == 9999

    def test_stale_generation_frame_dropped(self, tmp_path, monkeypatch):
        """代次翻转（pause/关闭）后到达的迟到帧必须丢弃，不得发请求。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from PIL import Image

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True
            mouse_through = False
            _dragging = False
            _physics_mode = None
            _click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"], "dry_run": True})
        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)

        called = []
        monkeypatch.setattr(
            watcher.limiter,
            "try_acquire",
            lambda: (called.append(1) or (True, "ok")),
        )

        img = Image.new("RGB", (50, 50))
        # pause 翻转代次后，携带旧代次的帧到达 → 丢弃
        watcher.pause()
        watcher._on_frame_ready(img, "code.exe | t", 1, 123, {"_gen": 0})
        assert called == []
        # 不带代次标记的旧帧（None）不受影响（向后兼容直接调用）
        watcher._on_frame_ready(img, "code.exe | t", 1, 123, {})
        assert called == [1]

    def test_in_flight_request_blocks_new_dispatch(self, tmp_path, monkeypatch):
        """视觉请求在飞期间，心跳不得再派发新 pipeline。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True
            mouse_through = False
            _dragging = False
            _physics_mode = None
            _click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {"enabled": True, "whitelist": ["code.exe"]})
        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)

        dispatched = []
        monkeypatch.setattr(watcher, "_worker_capture", lambda *a, **kw: dispatched.append(1))

        watcher._request_in_flight = True
        watcher._on_tick()
        assert dispatched == []

    def test_worker_busy_released_on_emit_failure(self, tmp_path, monkeypatch):
        """截图成功但后续 emit/dHash 抛异常时，worker_busy 必须释放，
        否则主动识屏永久卡死（gpt-5.6-sol 终审 #10 回归）。"""
        from PySide6.QtWidgets import QApplication
        from pet.proactive import ProactiveScreenWatcher
        from pet import vision
        from PIL import Image

        app = QApplication.instance() or QApplication([])

        class DummyWindow:
            on_open_chat = True
            mouse_through = False
            _dragging = False
            _physics_mode = None
            _click_effect_phase = 0

            def isVisible(self):
                return True

        cfg = Config(base=tmp_path)
        watcher = ProactiveScreenWatcher(DummyWindow(), cfg)
        watcher._worker_busy = True

        monkeypatch.setattr(vision, "foreground_window_info", lambda: {"hwnd": 1, "process": "a.exe", "title": "t", "rect": (0, 0, 100, 100)})
        monkeypatch.setattr(vision, "capture_window_rect", lambda r: Image.new("RGB", (10, 10)))
        monkeypatch.setattr("pet.proactive.image_dhash", lambda img: (_ for _ in ()).throw(RuntimeError("boom")))

        watcher._worker_capture((0, 0, 100, 100), {"hwnd": 1, "process": "a.exe", "title": "t"}, {}, 0)
        assert watcher._worker_busy is False


# ============================================================================
# 10. 主动识屏按真实请求计费（每次 HTTP 请求消耗预算）回归测试
# ============================================================================
class TestProactiveBudgetPerRequest:
    def test_consume_budget_charges_per_real_request(self, tmp_path):
        """每次真实请求前消耗一次预算（一次触发里的多次重试各自占用额度）。"""
        state_file = tmp_path / "state.json"
        cur_time = [10000.0]
        limiter = ProactiveLimiter(
            state_file,
            {"daily_cap": 3, "min_request_interval_seconds": 5, "cooldown_minutes": 1},
            clock=lambda: cur_time[0],
            today=lambda: "2026-08-27",
        )
        assert limiter.consume_budget() is True
        assert limiter.consume_budget() is True
        assert limiter.consume_budget() is True
        # 预算耗尽：第四次返回 False，且不再累加
        assert limiter.consume_budget() is False
        state = limiter._load_state()
        assert state["count"] == 3

    def test_vision_consumes_budget_each_attempt_and_model_access_retries_once(self, monkeypatch):
        """497 回归：模型访问失败最多重试 1 次；每一次真实 HTTP 请求前都消耗预算。"""
        import urllib.error
        from pet import vision
        from pet.chat.models import ProviderConfig

        attempts = []
        consumed = []

        def fake_urlopen(req, timeout=None, context=None):
            attempts.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        # 去重试 sleep，避免测试被 2 秒拖慢
        monkeypatch.setattr(vision.time, "sleep", lambda s: None)

        p = ProviderConfig.from_dict("test", {"model": "deepseek-v4-flash", "api_key": "sk-123"})
        with pytest.raises(vision.VisionError):
            vision._post_vision_request(
                b"fake-jpeg", "code.exe | t", "sys", p,
                consume_budget=lambda: (consumed.append(1) or True),
            )
        # 模型访问失败只重试 1 次：总共发起 2 次请求
        assert len(attempts) == 2
        # 每次真实请求前都消耗一次预算
        assert len(consumed) == 2

    def test_vision_budget_exhausted_stops_before_request(self, monkeypatch):
        """预算耗尽时必须停止，绝不再发起任何 HTTP 请求。"""
        from pet import vision
        from pet.chat.models import ProviderConfig

        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            raise AssertionError("预算耗尽后不应发起请求")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        p = ProviderConfig.from_dict("test", {"model": "deepseek-v4-flash", "api_key": "sk-123"})
        with pytest.raises(vision.VisionError) as exc_info:
            vision._post_vision_request(b"fake-jpeg", "code.exe | t", "sys", p, consume_budget=lambda: False)
        assert "上限" in str(exc_info.value)
        assert calls == []
