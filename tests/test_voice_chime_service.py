# -*- coding: utf-8 -*-
"""语音报时服务层 / 设置页 / AppShell 接线测试。

纯逻辑层契约测试在 tests/test_voice_chime.py；本文件补的是「不是纯函数」的
那半边（#118 审查缺口）：服务读配置的健壮性、停止后不再回放、edge-tts 缺失
降级、缓存复用、设置页与 config 的持久化往返、AppShell 懒启停门控与退出收口。

纪律：全部用例**不打网络**（edge_tts 探测标志与合成线程都局部打桩）、不做
固定 sleep 猜时序、不依赖真实音频设备（conftest 已全局静音 play()）。
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.voice_chime import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    DEFAULT_VOLUME,
    cache_key,
)
from pet.voice_chime_service import VoiceChimeService


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Win:
    """假桌宠窗口：只记录气泡，不碰任何 Qt 绘制。"""

    def __init__(self) -> None:
        self.bubbles: list[tuple[str, int | None]] = []

    def show_bubble(self, text: str, duration_ms: int | None = None) -> None:
        self.bubbles.append((text, duration_ms))

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt API
        return True


class _App:
    """假 AppShell：服务只用到 config / win / system_notify。"""

    def __init__(self, config) -> None:
        self.config = config
        self.win = _Win()
        self.notices: list[tuple[str, str]] = []

    def system_notify(self, title: str, message: str, **_kw) -> None:
        self.notices.append((title, message))


class _FakePlayer:
    def __init__(self) -> None:
        self.sources: list[str] = []
        self.plays = 0

    def setSource(self, url) -> None:  # noqa: N802 - 对齐 Qt API
        self.sources.append(url.toLocalFile() if hasattr(url, "toLocalFile") else str(url))

    def play(self) -> None:
        self.plays += 1


class _FakeAudioOut:
    def __init__(self) -> None:
        self.volumes: list[float] = []

    def setVolume(self, value: float) -> None:  # noqa: N802 - 对齐 Qt API
        self.volumes.append(value)


class _WorkerSpy:
    """替换 _TTSWorker：不建线程、不打网络，只记下参数与回调。"""

    instances: list["_WorkerSpy"] = []

    def __init__(self, text, voice, rate, pitch, out_path, on_done) -> None:
        self.text = text
        self.out_path = out_path
        self.on_done = on_done
        _WorkerSpy.instances.append(self)

    def start(self) -> None:
        pass

    def finish(self, path=None, text=None, error="") -> None:
        """模拟后台线程完成：把结果交回服务（真实实现走 queued 信号）。"""
        self.on_done(path or str(self.out_path), text or self.text, error)


def _service(tmp_path, monkeypatch, *, tts_available=True):
    """构造一个不打网络、不碰真实播放器的服务。"""
    import pet.voice_chime_service as svc_mod

    _qapp()  # 服务的无主 QTimer 需要在有事件分发器的线程里才起得来（产品同口径）
    _WorkerSpy.instances = []
    monkeypatch.setattr(svc_mod, "_EDGE_TTS_AVAILABLE", tts_available)
    monkeypatch.setattr(svc_mod, "_TTSWorker", _WorkerSpy)
    cfg = Config(base=tmp_path)
    app = _App(cfg)
    service = VoiceChimeService(app)
    service._player = _FakePlayer()
    service._audio_out = _FakeAudioOut()
    service._ensure_player = lambda: True
    return service, app, cfg


# ------------------------------------------------------------ 服务：配置读取健壮性


def test_apply_config_tolerates_invalid_persisted_values(tmp_path, monkeypatch):
    """config.json 被手改成非法值时，服务读取不得抛异常（要有默认值恢复）。

    实测缺陷：apply_config 里音量走裸 int()，'abc'/None/[] 全部 ValueError/
    TypeError；该方法在 start()（开机）与菜单「立即报时」路径上执行，
    一个坏值就能让语音报时在启动期直接抛。
    """
    service, app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_volume", "abc")
    cfg.set("voice_chime_rate", None)
    cfg.set("voice_chime_pitch", "12.5")
    cfg.set("voice_chime_schedule", 3)
    cfg.set("voice_chime_custom_times", None)
    cfg.set("voice_chime_voice", "")

    service.apply_config()  # 不得抛异常

    assert service._cfg["volume"] == DEFAULT_VOLUME
    assert service._cfg["rate"] == DEFAULT_RATE
    assert service._cfg["pitch"] == DEFAULT_PITCH
    assert service._cfg["schedule"] == "hourly"
    assert service._cfg["custom_times"] == frozenset()
    assert service._cfg["voice"] == DEFAULT_VOICE


def test_service_cfg_matches_contract_shape_at_construction(tmp_path, monkeypatch):
    """构造即进入契约形状（enabled/schedule/custom_times/voice/rate/pitch/volume）。

    实测缺陷：__init__ 用 default_chime_config()（带 voice_chime_ 前缀的镜像
    键）初始化 _cfg，与 _fire/_on_tick 读取的契约键不同形；任何绕过
    apply_config 的路径都会读到 None 并被当成"未启用"静默吞掉。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    # 契约键（服务内部读写的最小集）必须全部在场；气泡/台词开关与自定义台词
    # 库是本分支新增的扩展键，同样在构造期即成形。
    assert {
        "enabled", "schedule", "custom_times", "voice", "rate", "pitch", "volume",
    } <= set(service._cfg)
    assert {
        "show_bubble", "show_quote", "custom_quotes_zh", "custom_quotes_en",
    } <= set(service._cfg)
    assert service._cfg["voice"] == DEFAULT_VOICE
    assert service._cfg["enabled"] is True


# ------------------------------------------------------------ 服务：合成/播放路径


def test_missing_edge_tts_degrades_to_bubble_without_synthesis(tmp_path, monkeypatch):
    """edge-tts 缺失：只气泡提示，不起合成线程（降级路径不许静默丢弃）。"""
    service, app, _cfg = _service(tmp_path, monkeypatch, tts_available=False)
    service.say_now("整点报时")

    assert _WorkerSpy.instances == []
    assert len(app.win.bubbles) == 1
    assert "edge-tts" in app.win.bubbles[0][0]


def test_cached_audio_replays_without_resynthesis(tmp_path, monkeypatch):
    """缓存命中：直接回放缓存文件，不再起合成线程。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。测试台词"
    key = cache_key(sentence, {
        "voice": cfg.get("voice_chime_voice"),
        "rate": cfg.get("voice_chime_rate"),
        "pitch": cfg.get("voice_chime_pitch"),
    })
    cache_dir = service._cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.mp3"
    cached.write_bytes(b"fake-mp3")

    service.say_now(sentence)

    assert _WorkerSpy.instances == [], "缓存命中不得重新合成"
    # QUrl.fromLocalFile 会把路径归一化为正斜杠，故比 Path 而非字符串
    assert Path(service._player.sources[-1]) == cached
    assert service._player.plays == 1
    assert app.win.bubbles[-1][0] == sentence


def test_synthesis_result_is_dropped_after_stop(tmp_path, monkeypatch):
    """停止后迟到的合成结果作废：不再回放、不再气泡。

    实测缺陷：stop()（关闭开关 / 应用退出）只停 QTimer；飞行中的 daemon
    合成线程仍会经信号桥回 GUI 线程继续 setSource+play+show_bubble，即
    「关掉语音报时之后又响一声」，退出路径上还可能碰到正在析构的窗口。
    """
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.start()
    service._timer.stop()  # 本用例只测合成回调，不让 tick 参与
    service.say_now("现在是上午九点整。台词")
    assert len(_WorkerSpy.instances) == 1
    worker = _WorkerSpy.instances[0]
    player_before = list(service._player.sources)
    bubbles_before = len(app.win.bubbles)

    service.stop()
    worker.finish()  # 合成结果在 stop() 之后才回来

    assert service._player.sources == player_before
    assert service._player.plays == 0
    assert len(app.win.bubbles) == bubbles_before


def test_is_running_tracks_timer_state(tmp_path, monkeypatch):
    """is_running() 是懒启停门控的公开判据（不再由 app.py 探 service._timer）。"""
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    assert service.is_running() is False
    service.start()
    assert service.is_running() is True
    service.stop()
    assert service.is_running() is False


# ------------------------------------------------------------ 设置页


def test_settings_page_opens_with_invalid_persisted_values(tmp_path):
    """非法持久化值不得让设置页打不开（不能把用户挡在设置界面外）。

    实测缺陷：__init__/refresh_from_config 用裸 int() 读 rate/pitch/volume，
    config.json 里出现 'abc' 就读不出页面 → 用户既改不回来也进不去设置。
    """
    from pet.voice_chime_settings import VoiceChimeSettingsPage

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_rate", "abc")
    cfg.set("voice_chime_pitch", "xyz")
    cfg.set("voice_chime_volume", None)
    cfg.set("voice_chime_schedule", "no-such-mode")

    page = VoiceChimeSettingsPage(cfg)

    assert page.rate_spin.value() == DEFAULT_RATE
    assert page.pitch_spin.value() == DEFAULT_PITCH
    assert page.volume_spin.value() == DEFAULT_VOLUME
    assert page.schedule_select.currentData() == "hourly"


def test_settings_page_round_trips_seven_keys_through_save_reload(tmp_path):
    """设置页 7 键写回 → 落盘 → 重新加载：值必须活下来（reload 白名单门禁）。"""
    from pet.voice_chime_settings import VoiceChimeSettingsPage

    _qapp()
    cfg = Config(base=tmp_path)
    page = VoiceChimeSettingsPage(cfg)
    page.enabled_check.setChecked(True)
    page.schedule_select.setCurrentData("custom")
    page.custom_edit.setText("08:30, 12:00")
    page.voice_select.setCurrentData("zh-CN-YunxiNeural")
    page.rate_spin.setValue(15)
    page.pitch_spin.setValue(-10)
    page.volume_spin.setValue(65)
    page.apply_to_config()
    cfg.save()

    reloaded = Config(base=tmp_path)
    assert reloaded.get("voice_chime_enabled") is True
    assert reloaded.get("voice_chime_schedule") == "custom"
    assert reloaded.get("voice_chime_custom_times") == "08:30, 12:00"
    assert reloaded.get("voice_chime_voice") == "zh-CN-YunxiNeural"
    assert reloaded.get("voice_chime_rate") == 15
    assert reloaded.get("voice_chime_pitch") == -10
    assert reloaded.get("voice_chime_volume") == 65


def test_settings_page_preview_writes_config_and_requests_preview(tmp_path):
    """「试听」先落配置再发信号（服务端按最新值合成），且不落盘。"""
    from pet.voice_chime_settings import VoiceChimeSettingsPage

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_rate", 0)
    page = VoiceChimeSettingsPage(cfg)
    seen: list[str] = []
    page.preview_requested.connect(seen.append)

    page.rate_spin.setValue(30)
    page.preview_btn.click()

    assert seen == [""], "试听应发出一次 preview_requested"
    assert cfg.get("voice_chime_rate") == 30, "试听前缀值须先写回内存 config"


def test_settings_page_hint_matches_wired_voice_helper(tmp_path):
    """音色提示必须是真接线：文案承诺「内置 20+ 款音色」，下拉里就得真能选到。"""
    from pet.voice_chime import VOICE_OPTIONS
    from pet.voice_chime_settings import VoiceChimeSettingsPage

    _qapp()
    page = VoiceChimeSettingsPage(Config(base=tmp_path))
    data = {page.voice_select.itemData(i) for i in range(page.voice_select.count())}
    labels = {page.voice_select.itemText(i) for i in range(page.voice_select.count())}
    for value, label in VOICE_OPTIONS:
        assert value in data, f"音色 {value} 未出现在音色下拉列表中"
        assert label in labels, f"音色标签 {label} 未出现在音色下拉列表中"


# ------------------------------------------------------------ AppShell 接线


def test_voice_chime_service_is_lazy_gated_by_config(tmp_path):
    """门控：关闭时不构造；开启后 sync 构造并运行；再关闭则停表并释放。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    assert shell.voice_chime_service is None

    cfg.set("voice_chime_enabled", True)
    shell._sync_chime_service()
    service = shell.voice_chime_service
    assert service is not None and service.is_running() is True

    cfg.set("voice_chime_enabled", False)
    shell._sync_chime_service()
    assert service.is_running() is False, "关闭开关后必须停掉 20s tick"
    assert shell.voice_chime_service is None


def test_toggle_voice_chime_flips_config_and_syncs(tmp_path):
    """右键「关闭/启用语音报时」：翻转配置、落盘、并同步服务启停。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    assert shell.voice_chime_service is not None

    shell.toggle_voice_chime()
    assert cfg.get("voice_chime_enabled") is False
    assert shell.voice_chime_service is None
    assert Config(base=tmp_path).get("voice_chime_enabled") is False, "开关须落盘"

    shell.toggle_voice_chime()
    assert cfg.get("voice_chime_enabled") is True
    assert shell.voice_chime_service is not None
    shell.voice_chime_service.stop()


def test_window_callbacks_for_voice_chime_are_wired(tmp_path):
    """窗口回调接线：右键「立即报时」「关闭语音报时」两项动作可调用。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    class _BareWin:
        pass

    win = _BareWin()
    shell.instance._wire_window(win)
    assert callable(win.on_voice_chime_now)
    assert callable(win.on_toggle_voice_chime)
    shell.voice_chime_service.stop()


def test_build_scripts_and_requirements_declare_edge_tts():
    """三端打包脚本必须收集 edge_tts，requirements.txt 必须声明它。

    语音报时缺 edge-tts 时只降级气泡（不崩溃），但**打包版用户无法自行 pip
    安装**——漏收就是「功能在安装包里永久不发声」。三脚本的 --collect-all
    清单历史上漂移过一次（macOS 漏 QtMultimedia，见 build_macos.sh 头注释），
    故用机器门禁钉住三端一致。
    """
    repo = Path(__file__).resolve().parents[1]
    for name in ("scripts/build_linux.sh", "scripts/build_macos.sh",
                 "scripts/build_onedir.ps1"):
        text = (repo / name).read_text(encoding="utf-8")
        assert "--collect-all edge_tts" in text, f"{name} 必须收集 edge_tts"
    requirements = (repo / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^edge-tts>=", requirements, re.M), \
        "requirements.txt 必须声明 edge-tts（CI 与打包都按它装依赖）"


def test_about_to_quit_stops_voice_chime_service(tmp_path, monkeypatch):
    """退出收口：aboutToQuit 必须停掉语音报时服务（与 todo_service 同口径）。

    实测缺陷：_on_about_to_quit 里只有 todo_service.stop()，语音报时漏了——
    它的无主 QTimer 被 Qt C++ 侧强引用，退出期仍在跑 20s tick，且合成线程
    可能在窗口析构后回 GUI 线程回放。
    """
    import pet.app as app_mod
    from pet.app import AppShell

    _qapp()
    monkeypatch.setattr(app_mod.QTimer, "singleShot", lambda *a, **k: None)

    class FakeApp:
        def __init__(self):
            self.connections = []

        @property
        def aboutToQuit(self):  # noqa: N802 - 对齐 Qt API
            return self

        def connect(self, slot):
            self.connections.append(slot)

    class FakeWin:
        def save_position(self):
            pass

    cfg = Config(base=tmp_path)
    # custom + 空时间点：本次启动绝不命中报时点（用例不打网络、不依赖当前时刻）
    cfg.set("voice_chime_schedule", "custom")
    cfg.set("voice_chime_custom_times", "")
    owner = AppShell(FakeApp(), cfg, enable_chat=False)
    owner.instance._create_ui = lambda cid: None
    owner.instance._apply_spawn_offset = lambda: None
    owner._apply_balance_timer = lambda: None
    owner.instance.win = FakeWin()
    owner.start()

    service = owner.voice_chime_service
    assert service is not None and service.is_running() is True

    owner._on_about_to_quit()

    assert service.is_running() is False, "退出收口必须停掉语音报时的 tick"
    owner._dsh_state_tracker.stop()
