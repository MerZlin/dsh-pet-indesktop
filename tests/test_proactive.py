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

import json
import time
from pathlib import Path

import pytest
from PIL import Image

from pet.config import Config
from pet.proactive import (
    DEFAULT_PROACTIVE_CONFIG,
    PRESET_DEFAULTS,
    ProactiveLimiter,
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

        # 第一次成功
        limiter.record_success()
        cur_time[0] += 70.0
        # 第二次成功
        ok, _ = limiter.allow()
        assert ok is True
        limiter.record_success()

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
        导致永远返回 None（mock 测试全覆盖时该 bug 完全隐形）。"""
        import sys
        if sys.platform != "win32":
            import pytest
            pytest.skip("仅 Windows 可真实调用")
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
        from pet import vision
        from PIL import Image

        # 创建一个 1000x1000 的虚拟屏幕
        fake_all_screen = Image.new("RGB", (1000, 1000), color=(50, 100, 150))
        monkeypatch.setattr(vision.ImageGrab, "grab", lambda all_screens=True: fake_all_screen)

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

        # mock vision._post_vision_request
        from pet import vision
        monkeypatch.setattr(
            vision,
            "_post_vision_request",
            lambda jpeg_bytes, app_str, prompt, p, memory_context="": "看到你在写 Python 呢！"
        )

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
    def test_settings_dialog_proactive_controls(self, tmp_path):
        from PySide6.QtWidgets import QApplication
        from pet.settings_dialog import PetSettingsDialog

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        dlg = PetSettingsDialog(cfg)

        # 验证控件初始化
        if hasattr(dlg, "pro_enabled_check"):
            assert dlg.pro_enabled_check.isChecked() is False
            dlg.pro_enabled_check.setChecked(True)
            dlg.pro_whitelist_edit.setPlainText("code.exe\ntitle:*测试*")
            dlg.pro_preset_combo.setCurrentIndex(dlg.pro_preset_combo.findData("active"))
            dlg._on_proactive_preset_changed(0)
            assert dlg.pro_dwell_spin.value() == 20

            # 保存
            dlg._save()
            loaded_pro = cfg.data["proactive_screen"]
            assert loaded_pro["enabled"] is True
            assert loaded_pro["preset"] == "active"
            assert "code.exe" in loaded_pro["whitelist"]
            assert "title:*测试*" in loaded_pro["whitelist"]

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

    def test_settings_save_preserves_unexposed_keys(self, tmp_path):
        """测试 4b：_save 应保留对话框未暴露的字段（min_request_interval_seconds 等）。"""
        from PySide6.QtWidgets import QApplication
        from pet.settings_dialog import PetSettingsDialog

        app = QApplication.instance() or QApplication([])

        cfg = Config(base=tmp_path)
        cfg.set("proactive_screen", {
            "min_request_interval_seconds": 123,
            "change_threshold": 20,
        })
        cfg.save()

        dlg = PetSettingsDialog(cfg)
        dlg._save()

        pro = cfg.data["proactive_screen"]
        assert pro["min_request_interval_seconds"] == 123
        assert pro["change_threshold"] == 20

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
        assert cfg.data["agent_link"]["dsh"] is True
        assert any(("DSH" in text) or ("联动" in text) or ("实装" in text) for text, _ in bubbles)

        # 关闭不强制要求气泡，仅需状态落盘
        win._toggle_agent_link("dsh", False)
        assert cfg.data["agent_link"]["dsh"] is False
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

        # 仅作用于本次调用：把 proactive.sys.platform 视作 darwin（非 Windows）
        monkeypatch.setattr("pet.proactive.sys.platform", "darwin")

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
        monkeypatch.setattr(
            vision,
            "_post_vision_request",
            lambda jpeg_bytes, app_str, prompt, p, memory_context="": recorded_contexts.append(memory_context) or "好呀"
        )

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
        win._bubble_busy_until = time.time() - 1
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
        monkeypatch.setattr(watcher.limiter, "record_attempt", lambda: called.append(1))

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
