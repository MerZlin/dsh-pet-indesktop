# -*- coding: utf-8 -*-
"""语音报时服务层 / 设置页 / AppShell 接线测试。

纯逻辑层契约测试在 tests/test_voice_chime.py；本文件补的是「不是纯函数」的
那半边（#118 审查缺口）：服务读配置的健壮性、停止后不再回放、edge-tts 缺失
降级、缓存复用、设置页与 config 的持久化往返、AppShell 懒启停门控与退出收口。

纪律：全部用例**不打网络**（edge_tts 探测标志与合成线程都局部打桩）、不做
固定 sleep 猜时序、不依赖真实音频设备（conftest 已全局静音 play()）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.voice_chime import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    DEFAULT_VOLUME,
    cache_key,
)
from pet.voice_chime_service import AUX_PRECACHE_ROLE, VoiceChimeService, _cache_hit

ROOT = Path(__file__).resolve().parents[1]


def _run_python(script: str) -> subprocess.CompletedProcess:
    """在**全新解释器**里跑一段脚本（cwd=仓库根，offscreen 免拖真实显示后端）。

    进程级 import 断言（`edge_tts` 会不会被拉进 sys.modules）必须这么测：全量
    套件是单进程的，别的用例的后台合成线程懒加载 edge_tts 的时机与本用例的断言
    窗口撞车就会假红（2026-09-23：同一棵树 PR run 绿，push run 在 macOS 上连红
    两次；本机连跑三轮全量全绿——属竞态而非回归）。子进程里"这个模块自己会不会
    在顶层 import"是可确定判定的，与 `tests/test_winmm_sound.py` 的 QtMultimedia
    同款写法。

    两条硬约束（都为「不可能挂住」）：
    1. 子进程脚本**只做 import 级断言**，不构造 AppShell / 不跑事件循环——裸
       `python -c` 里没有 conftest 的弹窗桩，模态对话框在那儿没人能关；
    2. 输出走**临时文件**而不是管道：`capture_output=True` 在超时杀进程后仍会
       读管道等 EOF，子进程若留下持有该管道的孙进程就会永久挂住（CI 上已见
       Linux 主套件卡死 20 分钟）。重定向到文件后，超时杀进程即返回，封闭有界。
    """
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryFile("w+t", encoding="utf-8", errors="replace") as out:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, cwd=str(ROOT), env=env, timeout=120,
            )
        except subprocess.TimeoutExpired:
            out.seek(0)
            return subprocess.CompletedProcess(
                [sys.executable, "-c", script], 124, out.read(), "")
        out.seek(0)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out.read(), "")


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
        self.stops = 0

    def setSource(self, url) -> None:  # noqa: N802 - 对齐 Qt API
        self.sources.append(url.toLocalFile() if hasattr(url, "toLocalFile") else str(url))

    def play(self) -> None:
        self.plays += 1

    def stop(self) -> None:
        self.stops += 1


class _FakeAudioOut:
    def __init__(self) -> None:
        self.volumes: list[float] = []

    def setVolume(self, value: float) -> None:  # noqa: N802 - 对齐 Qt API
        self.volumes.append(value)


class _WorkerSpy:
    """替换 _TTSWorker：不建线程、不打网络，只记下参数与回调。"""

    instances: list["_WorkerSpy"] = []

    def __init__(self, text, attempts, paths, on_done) -> None:
        self.text = text
        # 记下这次合成用的尝试序列与各自缓存路径：预合成与即时合成必须同源（ops-F3）。
        self.attempts = tuple(attempts)
        self.paths = tuple(paths)
        self.out_path = self.paths[0]
        self.on_done = on_done
        _WorkerSpy.instances.append(self)

    def start(self) -> None:
        pass

    def finish(self, path=None, text=None, error="") -> None:
        """模拟后台线程完成：把结果交回服务（真实实现走 queued 信号）。"""
        self.on_done(path or str(self.out_path), text or self.text, error)


def _loaded_media_player_class():
    """起播后停在实机 ``LoadedMedia`` 窗口里的播放器替身（缺 QtMultimedia 时返回 None）。

    实机探针（QMediaPlayer + QAudioOutput，offscreen，``assets/sounds/click.wav``）：

    =========================  ======================  =====================
    时刻                        playbackState           mediaStatus
    =========================  ======================  =====================
    ``setSource`` + ``play()``  ``StoppedState``        ``LoadingMedia``
    约 31ms                     ``StoppedState``        ``LoadedMedia``
    约 237ms                    ``PlayingState``        ``BufferingMedia``
    =========================  ======================  =====================

    中间那约 200ms 里 ``_player_engaged`` 的两个枚举判据**都不成立**——本替身锁的
    就是这一刻（``play()`` 之后停在 Stopped + LoadedMedia）。
    """
    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        return None

    class _LoadedWindowPlayer(_FakePlayer):
        """只补上收口要问的两个问题（playbackState / mediaStatus）。"""

        def __init__(self) -> None:
            super().__init__()
            self.status = QMediaPlayer.MediaStatus.NoMedia

        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.PlaybackState.StoppedState

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return self.status

        def play(self) -> None:
            super().play()
            # 实机：play() 之后约 31ms 进 LoadedMedia 窗口，约 237ms 才转 PlayingState
            self.status = QMediaPlayer.MediaStatus.LoadedMedia

    return _LoadedWindowPlayer


def _service(tmp_path, monkeypatch, *, tts_available=True):
    """构造一个不打网络、不碰真实播放器的服务。

    ``tts_available=False`` 表示「一个后端都用不了」——像过去那样只关掉 edge 已经
    不够了：回退链上还有别的后端，而它们的可用性由各自的 ``availability()`` 判定，
    所以这里把所有 provider 的可用性一起钉死。
    """
    import pet.voice_chime_service as svc_mod

    _qapp()  # 服务的无主 QTimer 需要在有事件分发器的线程里才起得来（产品同口径）
    _WorkerSpy.instances = []
    monkeypatch.setattr(svc_mod, "_TTSWorker", _WorkerSpy)
    if not tts_available:
        # 每个 provider 一律自报不可用（不看本机装没装、有没有 Key）
        for provider in svc_mod.tts.providers():
            monkeypatch.setattr(type(provider), "availability", lambda self, values: True)
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
    assert service._cfg["enabled"] is False  # 默认关闭（2026-09-19 起），显式开启后才启动调度


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

    # stop() 自己会给播放器清一次载（空源，见 _unload_player_source），所以这里只断言
    # "没有再顶上一个**新音源**"——真正的回放会带一个非空路径。
    assert [src for src in service._player.sources if src] == player_before
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


# ------------------------------------------------------------ 免打扰：语音静音


def _quiet():
    """进程级免打扰状态（唯一判据在 quiet_mode，本文件只驱动它）。"""
    from pet import quiet_mode

    return quiet_mode.shared_quiet_state()


def _cache_file_for(service, cfg, sentence: str) -> Path:
    """按产品约定预置一份假缓存音频，返回其路径（用来走「缓存命中」那条出声路径）。"""
    key = cache_key(sentence, {
        "voice": cfg.get("voice_chime_voice"),
        "rate": cfg.get("voice_chime_rate"),
        "pitch": cfg.get("voice_chime_pitch"),
    })
    service._cache_dir.mkdir(parents=True, exist_ok=True)
    cached = service._cache_dir / f"{key}.mp3"
    cached.write_bytes(b"fake-mp3")
    return cached


def test_quiet_mode_silences_chime_voice_and_still_defers_bubble(tmp_path, monkeypatch):
    """免打扰期间报时不出声：播放器零调用，连合成都不发起（省一次 TTS 网络请求）。

    口径（2026-10-08 拍板）：气泡照旧进暂存队列（退出免打扰统一汇报），语音按
    「时间敏感」**丢弃**——14:00 的报时在 15:00 补播没有意义。
    """
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    _quiet().start(15)

    service.say_now("现在是上午九点整。台词")

    assert _WorkerSpy.instances == [], "免打扰期间不该发起合成（网络请求）"
    assert service._player.sources == []
    assert service._player.plays == 0
    assert app.win.bubbles == [], "免打扰期间气泡不上屏"
    assert _quiet().pending_count() == 1, "但气泡照旧进暂存队列（语义不变）"


def test_quiet_mode_silences_cached_replay(tmp_path, monkeypatch):
    """缓存命中（预合成已完成）那条出声路径同样过闸：拦的是「出声」不是「合成」。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。测试台词"
    cached = _cache_file_for(service, cfg, sentence)
    _quiet().start(15)

    service.say_now(sentence)

    assert service._player.sources == [], f"不得把 {cached.name} 交给播放器"
    assert service._player.plays == 0
    assert _quiet().pending_count() == 1


def test_quiet_mode_skips_precache_synthesis(tmp_path, monkeypatch):
    """免打扰期间不预合成：合成出来也只会在到点被丢弃（同样是省一次网络请求）。"""
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    _quiet().start(15)

    # 09:59:30 距下一报时点 10:00 只有 30s（在 60s 预合成窗口内）→ 平时会起合成线程
    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))

    assert _WorkerSpy.instances == [], "免打扰期间不该起合成线程"
    assert service._precache_slot is None


def test_quiet_mode_drops_inflight_synthesis_result(tmp_path, monkeypatch):
    """合成期间才进入免打扰：迟到的结果同样不出声，气泡语义不变。"""
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.say_now("现在是上午九点整。台词")
    assert len(_WorkerSpy.instances) == 1

    _quiet().start(15)
    _WorkerSpy.instances[0].finish()

    assert service._player.plays == 0, "免打扰生效后回来的音频不得出声"
    assert app.win.bubbles == []
    assert _quiet().pending_count() == 1


def test_quiet_mode_off_plays_chime_as_before(tmp_path, monkeypatch):
    """非回归：未进入免打扰时逐字回到改动前的行为（合成 → 播放 → 上屏）。"""
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()

    service.say_now("现在是上午九点整。台词")
    assert len(_WorkerSpy.instances) == 1
    _WorkerSpy.instances[0].finish()

    assert service._player.plays == 1
    assert [text for text, _ms in app.win.bubbles] == ["现在是上午九点整。台词"]
    assert _quiet().pending_count() == 0


def test_voice_comes_back_after_quiet_mode_ends(tmp_path, monkeypatch):
    """退出免打扰后语音恢复：静音闸不许粘住（判据每次现读）。"""
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    state = _quiet()
    state.start(15)
    service.say_now("现在是上午九点整。")
    assert _WorkerSpy.instances == []

    state.reset()
    service.say_now("现在是上午九点整。")

    assert len(_WorkerSpy.instances) == 1, "免打扰结束后必须照常出声"


def test_fullscreen_equivalent_quiet_silences_voice(tmp_path, monkeypatch):
    """全屏等效免打扰走同一道闸（不是手动免打扰专属）。"""
    from pet import quiet_mode

    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    quiet_mode.note_fullscreen(True)

    service.say_now("现在是上午九点整。")

    assert _WorkerSpy.instances == []
    assert service._player.plays == 0
    assert app.win.bubbles == []
    assert _quiet().pending_count() == 1


def test_fullscreen_quiet_switched_off_by_config_plays(tmp_path, monkeypatch):
    """`quiet_also_on_fullscreen` 关掉时全屏不是免打扰 → 照常出声（判据同源，不各自读标志）。"""
    from pet import quiet_mode

    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    app.win.cfg = {"quiet_also_on_fullscreen": False}
    quiet_mode.note_fullscreen(True)

    service.say_now("现在是上午九点整。")

    assert len(_WorkerSpy.instances) == 1


def test_festival_speech_is_silenced_under_quiet_mode(tmp_path, monkeypatch):
    """节日语音（外部播报）同样过这道闸：speak() 默认尊重免打扰。"""
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    _quiet().start(15)

    assert service.speak("今天是中秋节。", log_tag="节日提醒") is True

    assert _WorkerSpy.instances == []
    assert service._player.plays == 0
    assert app.win.bubbles == [], "节日气泡由调用方自己管，通道不该插一条"


def test_click_self_talk_speech_is_exempt_from_quiet_mode(tmp_path, monkeypatch):
    """点击自言自语是交互应答（issue #208 口径）：显式豁免免打扰，照常出声。"""
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    _quiet().start(15)

    assert service.speak("今天也要认真工作呀。", log_tag="点击自言自语",
                         respect_quiet=False) is True

    assert len(_WorkerSpy.instances) == 1
    _WorkerSpy.instances[0].finish()
    assert service._player.plays == 1


def test_chime_settings_hint_mentions_quiet_mode(tmp_path):
    """设置页说明同步：启用行必须写明免打扰期间不出声（否则用户以为功能坏了）。"""
    from pet.voice_chime_settings import VoiceChimeSettingsPage

    _qapp()
    page = VoiceChimeSettingsPage(Config(base=tmp_path))

    hint = page.enabled_check.accessibleDescription()
    assert "免打扰" in hint, f"启用语音报时的说明没提免打扰：{hint!r}"


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
    """右键「启用/关闭语音报时」：翻转配置、落盘、并同步服务启停。

    默认关闭（2026-09-19 起）：初始无服务；第一次 toggle 开启并建服务，
    第二次 toggle 关闭并释放。
    """
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    assert shell.voice_chime_service is None, "默认关闭：初始不得创建报时服务"

    shell.toggle_voice_chime()
    assert cfg.get("voice_chime_enabled") is True
    assert shell.voice_chime_service is not None
    assert Config(base=tmp_path).get("voice_chime_enabled") is True, "开关须落盘"

    shell.toggle_voice_chime()
    assert cfg.get("voice_chime_enabled") is False
    assert shell.voice_chime_service is None


def test_audio_channel_survives_on_festival_speak_alone(tmp_path):
    """报时关闭但节日语音开启时，音频通道必须仍然存在。

    节日语音复用报时服务作为进程内唯一音频通道；若通道随报时开关一起被释放，
    节日播报就会静默失效（有气泡没声音）——这是本功能最隐蔽的接线坑。
    """
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_enabled", True)
    cfg.set("festival_reminder_speak", True)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    shell._sync_chime_service()
    assert shell.voice_chime_service is not None, "节日语音需要通道，不能因报时关闭而释放"

    # 关掉节日语音后，两个开关都关 → 通道应释放
    cfg.set("festival_reminder_speak", False)
    shell._sync_festival_service()
    assert shell.voice_chime_service is None


def test_audio_channel_is_not_created_when_nobody_needs_it(tmp_path):
    """两个开关都关时不该白建通道（保持既有懒创建语义）。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_enabled", True)
    cfg.set("festival_reminder_speak", False)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    shell._sync_chime_service()
    assert shell.voice_chime_service is None


def test_appshell_injects_yield_hook_into_chime_service(tmp_path):
    """让位钩子必须在创建通道时注入——没注入就等于节日与报时会各说各的。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", True)  # 默认关闭（2026-09-19 起），测通道创建须显式开启
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    service = shell.voice_chime_service
    assert service is not None
    assert service.yield_slot is not None
    # 没有节日服务时应判定为"不让位"，报时照常
    assert shell._chime_should_yield("2026-02-17T09:00#hourly") is False
    shell.voice_chime_service.stop()


def test_window_callbacks_for_voice_chime_are_wired(tmp_path):
    """窗口回调接线：右键「立即报时」「关闭语音报时」两项动作可调用。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", True)  # 默认关闭，这里需要通道实例存在
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
    monkeypatch.setenv("PET_RENDER_TOPOLOGY", "legacy")  # PetWindow 路径测试
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
    cfg.set("voice_chime_enabled", True)  # 默认关闭（2026-09-19 起），测退出收口须显式开启
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


# ============================================================ 共享音频通道（节日语音）
# 节日提醒的语音复用本报时服务作为进程内唯一音频通道。下列用例锁住四件事：
#   1) speak() 只出声、不出气泡（气泡归调用方，避免与节日自己的气泡重复/被
#      voice_chime_show_bubble 二次影响）；
#   2) 通道忙时不打断也不丢弃，进深度 1 待播队列，播完自动接上；
#   3) 排队同样受免打扰契约约束：入队即过闸（尊重免打扰的那条直接丢弃、不补播），
#      豁免随队携带（交互应答出队时照常出声）；
#   4) yield_slot 钩子让报时在"节日要说话的那一分钟"整分钟让位，且让位同样
#      消费槽位（否则 20s 一次的 tick 会在本分钟内反复询问）。


def _cache_mp3(service, text: str) -> Path:
    """按服务当前配置把一段文本的缓存 mp3 造出来（跳过真实合成）。"""
    key = cache_key(
        text,
        {
            "voice": service._cfg["voice"],
            "rate": service._cfg["rate"],
            "pitch": service._cfg["pitch"],
        },
    )
    service._cache_dir.mkdir(parents=True, exist_ok=True)
    path = service._cache_dir / f"{key}.mp3"
    path.write_bytes(b"\x00fake-mp3")
    return path


def test_speak_plays_from_cache_and_never_bubbles(tmp_path, monkeypatch):
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    text = "今天是春节。爆竹声中一岁除。"
    _cache_mp3(service, text)

    assert service.speak(text) is True

    assert service._player.plays == 1
    assert app.win.bubbles == [], "外部播报的气泡由调用方自己展示，本服务不得再插一条"


def test_speak_queues_instead_of_interrupting_when_channel_is_busy(tmp_path, monkeypatch):
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._busy = True  # 正在合成

    assert service.speak("今天是中秋节。") is True

    assert service._pending_speech == "今天是中秋节。"
    assert service._player.plays == 0, "通道忙时不得抢播（会打断当前音频）"


def test_speak_keeps_only_the_latest_pending(tmp_path, monkeypatch):
    """队列深度 1：后来的覆盖前面的，避免堆积出已过期内容。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._busy = True

    service.speak("第一条")
    service.speak("第二条")

    assert service._pending_speech == "第二条"


def test_pending_speech_plays_after_current_audio_ends(tmp_path, monkeypatch):
    # 刻意**不导入 QtMultimedia**：Linux CI 上它依赖 libpulse.so.0 等系统库，
    # 缺失即 ImportError（ubuntu 作业曾因此红）。终止态枚举由生产代码在创建
    # 播放器时缓存，这里用哨兵值即可覆盖判空与消费逻辑。
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    text = "今天是重阳节。"
    _cache_mp3(service, text)
    service._pending_speech = text
    service._terminal_statuses = ("END", "BAD")

    service._on_media_status("END")

    assert service._pending_speech is None
    assert service._player.plays == 1


def test_media_status_ignores_non_terminal_states(tmp_path, monkeypatch):
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._pending_speech = "排队中"
    service._terminal_statuses = ("END", "BAD")

    service._on_media_status("LOADING")  # 非终止态

    assert service._pending_speech == "排队中", "加载中不得提前消费待播"
    assert service._player.plays == 0


def test_speak_ignores_blank_text(tmp_path, monkeypatch):
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()

    assert service.speak("   ") is False
    assert service._pending_speech is None


def test_stop_clears_pending_speech(tmp_path, monkeypatch):
    """关闭开关/退出后不该再补播一条排队的语音。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._pending_speech = "排队中"

    service.stop()

    assert service._pending_speech is None


def test_pending_speech_carries_quiet_exemption_through_the_queue(tmp_path, monkeypatch):
    """交互应答（点击自言，respect_quiet=False）排队时，豁免必须随队携带。

    实测缺陷（2026-10-08，9eedd70 的忙通道分支）：这段分支只存文本、不存
    ``respect_quiet``，出队时回落默认 True → 通道忙时排队的那次点击自言自语在
    免打扰中出队被误静音（契约：交互应答免打扰照常播）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    text = "今天也要认真工作呀。"
    _cache_mp3(service, text)
    service._busy = True
    _quiet().start(15)

    assert service.speak(text, log_tag="点击自言自语", respect_quiet=False) is True

    assert service._pending_speech == text, "忙通道照旧进队列（深度 1，不打断当前音频）"
    assert service._pending_respect_quiet is False, "豁免必须随文本一起入队"

    service._busy = False          # 当前一段的合成/播放结算完毕
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert _quiet().suppression_active() is True, "出队时仍在免打扰（本用例前提）"
    assert service._player.plays == 1, "豁免随队携带：交互应答照常出声"
    assert service._pending_respect_quiet is True, "出队消费后复位为默认值"


def test_quiet_mode_drops_queued_speech_instead_of_replaying_later(tmp_path, monkeypatch, caplog):
    """免打扰中通道忙时的节日语音：入队即丢弃，不得留到免打扰结束后补播。

    实测缺陷（2026-10-08，9eedd70 的忙通道分支）：这段分支不过静音闸，节日语音
    被排队 → 免打扰结束后出队补播一条已经过期的提醒（契约：免打扰期间丢弃、
    不补播，与 ``_fire`` 同语义）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._busy = True
    _quiet().start(15)

    with caplog.at_level("DEBUG"):
        assert service.speak("今天是中秋节。", log_tag="节日提醒") is True

    assert service._pending_speech is None, "入队即闸：队列里不得留下这条节日语音"
    assert _WorkerSpy.instances == [], "丢弃发生在合成之前（连缓存都不查）"
    assert any("免打扰" in r.getMessage() for r in caplog.records), "丢弃须留下可发现的日志"

    _quiet().reset()               # 免打扰结束后的那些拍
    service._busy = False
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._pending_speech is None
    assert service._player.plays == 0, "出队后无任何播放（过期提醒不补播）"


def test_pending_speech_is_rechecked_against_quiet_mode_on_dequeue(tmp_path, monkeypatch):
    """入队时不在免打扰、出队前才进入：出队这一拍仍要被闸丢掉（既有语义回归）。

    随队携带的是**入队时**的豁免值，不是当时的免打扰状态快照——真闸仍在
    ``_fire`` 里，所以出队必须再判一次（本用例锁住这一点不被"随队携带"改坏）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    text = "今天是重阳节。"
    _cache_mp3(service, text)
    service._busy = True

    assert service.speak(text, log_tag="节日提醒") is True
    assert service._pending_speech == text, "入队时未开免打扰 → 照旧进队列"

    _quiet().start(15)             # 这一条播完之前用户开了免打扰
    service._busy = False
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._pending_speech is None
    assert service._player.plays == 0, "出队时被 _fire 闸丢弃（缓存命中也不出声）"


def test_discarded_synthesis_drains_pending_speech_with_exemption(tmp_path, monkeypatch):
    """合成结果因免打扰被丢弃时，待播队列必须在同一次结算里出队。

    实测缺陷（2026-10-08）：不起播就没有 EndOfMedia 媒体终止事件，而待播队列的
    出队口只有 ``_on_media_status``（该事件的回调）——免打扰期间在飞的合成一完成，
    排在后面的豁免语音（交互应答，respect_quiet=False）就无声滞留，直到被后续偶然
    事件覆盖为止。出队口随后收敛到 ``_on_synthesized`` 的 finally 收口（凡是不起播
    的完成结局都在那里结算），本用例锁的是行为，不锁调用点。

    链路全部走真实入口，不手工清 ``_busy``、不人工发终止事件。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    first = "今天是中秋节。"
    second = "今天也要认真工作呀。"
    cached_second = _cache_mp3(service, second)

    assert service.speak(first, log_tag="节日提醒") is True
    assert len(_WorkerSpy.instances) == 1, "第一条进入合成，通道被占"
    _quiet().start(15)                       # 合成期间才进入免打扰
    assert service.speak(second, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == second, "豁免的那条照旧进队列（入队时不该被闸掉）"

    _WorkerSpy.instances[0].finish()         # 第一条合成回来：被免打扰闸丢弃、不起播
    _qapp().processEvents()

    assert _quiet().suppression_active() is True, "本用例前提：出队那一拍仍在免打扰"
    assert service._player.plays == 1, "通道空出后豁免语音必须当场出队出声"
    assert [Path(s).name for s in service._player.sources] == [cached_second.name]
    assert service._pending_speech is None, "队列字段随这次出队被消费清空"
    assert service._pending_respect_quiet is True, "出队消费后复位为默认值"


def test_discarded_synthesis_drains_quiet_pending_without_playing(tmp_path, monkeypatch):
    """对照片：待播那条尊重免打扰时，出队被 ``_fire`` 的闸现读丢弃、零播放。

    出队的意义不是"一定要出声"，而是队列字段必须被消费清空——不消费就会滞留，
    等后续偶然事件才吐出来（甚至永远不出声）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    first = "今天是中秋节。"
    second = "现在是上午九点整。"
    _cache_mp3(service, second)

    assert service.speak(first, log_tag="节日提醒") is True
    assert service.speak(second, log_tag="节日提醒") is True, "入队时未开免打扰 → 照旧进队列"
    assert service._pending_speech == second
    _quiet().start(15)                       # 第一条合成回来之前才进入免打扰

    _WorkerSpy.instances[0].finish()
    _qapp().processEvents()

    assert service._player.plays == 0, "尊重免打扰的那条出队时被 _fire 闸丢弃"
    assert service._pending_speech is None, "出队消费清空（不滞留到后续偶然事件）"
    assert service._pending_respect_quiet is True
    assert len(_WorkerSpy.instances) == 1, "被闸丢弃的那条不得再起一路合成"


def test_precache_completion_drains_pending_speech_with_exemption(tmp_path, monkeypatch):
    """预合成完成同样"不起播"：待播队列必须在这一次结算里出队。

    实测缺陷（2026-10-08，同族最后一条）：预合成分支只记录 ``_precache_path``
    就 return，既不起播也不产生 EndOfMedia。复现链：报时点前 60s 预合成在飞
    （``_busy=True``）→ 用户开启免打扰 → 点击自言（无本地缓存）以
    ``respect_quiet=False`` 入队 → 预合成完成不起播 ⇒ 豁免语音滞留；报时点到时
    若仍免打扰，报时也被丢弃不起播，依然无人出队。

    链路全部走真实入口，不手工清 ``_busy``、不人工发终止事件。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    text = "今天也要认真工作呀。"
    cached = _cache_mp3(service, text)

    # 09:59:30 距 10:00 只 30s（在 60s 预合成窗口内）→ 预合成在飞
    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))
    assert len(_WorkerSpy.instances) == 1, "预合成在飞（通道被占）"
    precache_worker = _WorkerSpy.instances[0]

    _quiet().start(15)                       # 报时点之前用户开了免打扰
    assert service.speak(text, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == text, "豁免的那条照旧进队列（入队时不该被闸掉）"

    precache_worker.finish()                 # 预合成完成：只记路径、不起播
    _qapp().processEvents()

    assert _quiet().suppression_active() is True, "本用例前提：出队那一拍仍在免打扰"
    assert service._player.plays == 1, "不起播的完成结局必须让豁免语音当场出队出声"
    assert [Path(s).name for s in service._player.sources] == [cached.name]
    assert service._pending_speech is None, "队列字段随这次出队被消费清空"
    assert service._pending_respect_quiet is True, "出队消费后复位为默认值"


def test_precache_completion_records_path_without_playing(tmp_path, monkeypatch):
    """预合成本身的语义不变：完成只记录缓存路径，不播放报时音频、不碰待播队列。"""
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()

    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))
    worker = _WorkerSpy.instances[0]

    worker.finish()
    _qapp().processEvents()

    assert service._precache_path == str(worker.out_path), "预合成完成只记录缓存路径"
    assert service._player.sources == [], "预合成不得出声（到点由 _fire 播缓存）"
    assert service._player.plays == 0
    assert service._pending_speech is None


# ------------------------------------------------------------ 旁路预合成（precache 入口）
# 设备/角色语义：``precache(text)`` 是给"外部功能想提前把一段合好"用的（每日一签），
# 它与**报时预合成**共用 ``_busy`` 这一条通道闸，但**不共用占位状态**。下面两条钉住
# 这层隔离：旁路的成败只说自己那一句，不许碰报时那一组 ``_precache_*`` 字段。

def _arm_chime_precache(service):
    """让报时预合成**已经完成**（占位 + 路径都在），返回 ``(slot, path, text)``。

    09:59:30 距 10:00 只 30s，落在 60s 预合成窗口里；合成线程用 ``_WorkerSpy`` 替身，
    完成由 ``finish()`` 驱动——不睡、不猜时序。
    """
    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))
    worker = _WorkerSpy.instances[-1]
    worker.finish()
    _qapp().processEvents()
    assert service._precache_slot is not None, "前提：报时预合成已占位"
    assert service._precache_path is not None, "前提：报时预合成已完成"
    return service._precache_slot, service._precache_path, service._precache_text


def _start_aux_precache(service, text="今天也要认真工作呀。"):
    """走旁路预合成入口，返回替身 worker（调用方自己决定 finish 成功还是失败）。"""
    assert service.precache(text, log_tag="每日一签") is True, "前提：旁路预合成被受理"
    return _WorkerSpy.instances[-1]


@pytest.mark.parametrize("error", ["网络错误", "edge-tts-missing"],
                         ids=["合成失败", "缺依赖"])
def test_aux_precache_failure_leaves_the_chime_slot_intact(tmp_path, monkeypatch, error):
    """旁路预合成失败**不许**清掉报时那一组占位——那是报时已经合好的那一段。

    实测缺陷（2026-10-17）：旁路预合成曾经复用 ``role="precache"``，而失败分支按角色
    清 ``_precache_slot/_precache_text/_precache_bubble/_precache_path``——旁路的一次
    失败就把报时**已合成好**的缓存路径一起抹掉，到点 ``_consume_precache`` 只能回退
    即时合成（那一段语音晚 3-8 秒才响），日志里还写着"预合成失败"。
    """
    from pet.voice_chime_service import EDGE_TTS_MISSING

    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    slot, path, text = _arm_chime_precache(service)

    aux = _start_aux_precache(service)
    aux.finish(error=EDGE_TTS_MISSING if error == "edge-tts-missing" else error)
    _qapp().processEvents()

    assert service._precache_slot == slot, "报时占位的槽位被旁路预合成清掉了"
    assert service._precache_path == path, "报时已合好的缓存路径被旁路预合成清掉了"
    assert service._precache_text == text
    assert service._player.plays == 0, "旁路预合成无论成败都不出声"


def test_aux_precache_completion_never_claims_the_chime_path(tmp_path, monkeypatch):
    """旁路预合成完成只留在自己的 cache key 上：不改 ``_precache_path``，也不出声。

    实测缺陷（2026-10-17）：完成分支 ``_precache_path = str(path)`` 是按角色写的，
    旁路完成会把这格改成**旁路那个 mp3**；随后 ``_consume_precache(报时槽位)`` 判
    "槽位对得上 + 文件存在"就返回了，日志里报的文件名是别人的，报时那一格的用途
    从此对不上（这次是运气好——调用方只用返回的文本重算 key）。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    slot, path, text = _arm_chime_precache(service)

    aux = _start_aux_precache(service)
    aux.finish()
    _qapp().processEvents()

    assert service._precache_path == path, "报时那一格被旁路预合成的路径覆盖了"
    assert service._precache_path != str(aux.out_path), "旁路的 key 与报时的不是同一个"
    assert service._precache_slot == slot
    assert service._precache_text == text
    assert service._player.plays == 0, "旁路预合成不得出声（那会把预合成变成偷偷播一次）"
    assert service._player.sources == []


def _write_cache_for(service, text: str) -> Path:
    """用产品自己的路径算法把一段文本标成"已有缓存"（不手拼 key：命中要看真实文件名）。"""
    attempts = service._usable_attempts()
    assert attempts, "前提：本环境至少有一个可用后端（CI 装 requirements.txt 后成立）"
    service._cache_dir.mkdir(parents=True, exist_ok=True)
    cached = service._cache_path(text, attempts[0])
    cached.write_bytes(b"\x00" * 32)
    assert _cache_hit(cached)
    return cached


def test_aux_precache_cache_hit_neither_plays_nor_synthesizes_again(tmp_path, monkeypatch):
    """旁路预合成撞上**已有缓存**：不起第二路合成、也不出声。

    两个入口都要成立：
    1. ``precache()`` 自己的命中分支——命中即返回 ``True``（"已可用"），连 ``_fire``
       都不调；
    2. ``_fire`` 的命中分支按角色早退——这条是**竞态兜底**：检查与起播之间若有别的
       后台合成把文件写好，命中就落进 ``_fire``。缓存命中判据在 provider 化之后从
       "一个路径"变成"逐后端一轮循"（``_cache_path`` 的扩展名与 flavor 都由 provider
       给），早退必须留在循环里，否则"我只是想提前合好"会变成"偷偷播了一次"
       （``show_bubble=False`` 只压气泡，压不住播放）。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    text = "今天也要认真工作呀。"
    _write_cache_for(service, text)

    # 1) 入口命中：不起合成
    assert service.precache(text, log_tag="每日一签") is True, "已有缓存＝这次已可用"
    assert _WorkerSpy.instances == [], "缓存命中不得再起一路合成"
    assert service._player.plays == 0, "缓存命中不得出声（预合成永不出声）"

    # 2) 竞态兜底：直接走 _fire 的缓存命中分支，预合成角色必须早退
    service._fire(text, datetime(2026, 9, 15, 10, 0), None,
                  show_bubble=False, role=AUX_PRECACHE_ROLE)
    assert service._player.plays == 0, "预合成角色命中缓存后不得起播（偷偷播一次）"
    assert service._player.sources == []


def test_precache_reports_busy_so_the_caller_can_retry(tmp_path, monkeypatch):
    """通道被报时预合成占着时，旁路预合成返回 False（调用方按返回值重试）。

    返回值语义的另一半：**不能**"放弃也算成功"——那会让调用方以为已合好而不再重试。
    这里同时证明旁路预合成确实共用 ``_busy`` 这条通道闸（不该有的第二路并发合成）。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()

    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))
    assert service._busy is True, "前提：报时预合成在飞"
    started = len(_WorkerSpy.instances)

    assert service.precache("今天也要认真工作呀。", log_tag="每日一签") is False
    assert len(_WorkerSpy.instances) == started, "通道忙时不得再起一路合成"


def test_failed_synthesis_drains_pending_speech(tmp_path, monkeypatch):
    """合成失败同样不起播：待播队列在 error 结算里出队，不滞留。

    error 分支自己的日志与气泡行为不在本用例范围内（那里一行未动），本用例锁的
    是"失败结算也必须让通道上排队的那一条落地"。
    """
    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    first = "今天是中秋节。"
    second = "今天也要认真工作呀。"
    cached = _cache_mp3(service, second)

    assert service.speak(first, log_tag="节日提醒") is True
    assert service.speak(second, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == second, "通道忙 → 豁免的那条进队列"

    _WorkerSpy.instances[0].finish(error="synthesis boom")
    _qapp().processEvents()

    assert service._pending_speech is None, "失败结算同样要消费队列字段（不滞留）"
    assert service._player.plays == 1, "豁免的那条被出队并播放"
    assert [Path(s).name for s in service._player.sources] == [cached.name]
    assert any("合成失败" in msg for msg, _ms in app.win.bubbles), "error 气泡语义不变"


def _self_starting_engagement(service, monkeypatch) -> dict:
    """把"通道被接管"接到播放器替身上：``play()`` 即置位，返回可手动改的状态。

    合成完成那一刻的判据必须区分两件事：**我们刚起播**（该留给 EndOfMedia）与
    **别处正在播**（该回队不顶播）。一个恒为真的静态替身表达不了这个区别——它同
    时命中 P2-3 的回队判据，于是"这次完成起了播"变成了"这次完成被接管而不播"。
    真实播放器的时序是：``play()`` 之后才 LoadingMedia（实机探针见
    ``test_player_engaged_covers_just_started_playback``）。这里照着它做：起播前
    判据为假，``play()`` 之后为真；要模拟"这一段播完"就把 ``state["engaged"]``
    置回 False（真实那一刻是 StoppedState + EndOfMedia）。
    """
    state = {"engaged": False}
    player = service._player

    def _play() -> None:
        _FakePlayer.play(player)
        state["engaged"] = True

    monkeypatch.setattr(player, "play", _play)
    monkeypatch.setattr(service, "_player_engaged", lambda: state["engaged"])
    return state


def test_completion_that_started_playback_leaves_queue_to_end_of_media(tmp_path, monkeypatch):
    """收口的另一半：这一次完成**确实起了播**时，队列必须留给 EndOfMedia。

    行为契约是 ``speak()`` 的「通道忙时不打断、不叠音，当前一段播完再接上」。
    实机实测（QMediaPlayer + QAudioOutput，offscreen）：``setSource`` 后立刻
    ``play()`` 那一刻 ``playbackState`` 仍是 ``StoppedState``、``mediaStatus`` 才是
    ``LoadingMedia``（约 300ms 后才转 PlayingState）——而收口就在那一刻跑，若按
    ``playbackState`` 判会把刚接管的通道误判成空出，待播语音当场抢播、把刚起播的
    那一段掐掉。故收口用 ``_player_engaged()``（PlayingState 或 LoadingMedia）。

    这里不导入 QtMultimedia（Linux CI 上它拖 libpulse 等系统库）：按收口读的判据
    打桩，且由 ``play()`` 驱动（见 ``_self_starting_engagement``）——这样它同时锁住
    两件事：判据为真 ⇒ 不出队，以及"起播在先、接管在后"的时序。判据本身的真值表
    由 ``test_player_engaged_covers_just_started_playback`` 用最小替身逐个锁住。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    first = "今天是中秋节。"
    second = "今天也要认真工作呀。"
    cached_second = _cache_mp3(service, second)

    assert service.speak(first, log_tag="节日提醒") is True     # 合成在飞（通道被占）
    assert service.speak(second, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == second
    engaged = _self_starting_engagement(service, monkeypatch)

    _WorkerSpy.instances[0].finish()                            # 第一条合成回来 → 起播
    _qapp().processEvents()

    assert engaged["engaged"] is True, "play() 之后通道即被接管（真实时序）"
    assert service._player.plays == 1, "只该起播第一条"
    assert [Path(s).name for s in service._player.sources] == [Path(
        _WorkerSpy.instances[0].out_path).name], "第一条不得被待播语音顶掉"
    assert service._pending_speech == second, "起了播 → 留给 EndOfMedia，不在此出队"

    # 第一条播完：通道回到空闲（实机那一刻是 StoppedState + EndOfMedia），"END" 这一拍
    # 才允许出队——判据现在是出队口内部的统一占用判据，故这里把替身切成"未接管"。
    engaged["engaged"] = False
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._pending_speech is None
    assert [Path(s).name for s in service._player.sources][-1] == cached_second.name
    assert service._player.plays == 2, "播完第一条后，待播的第二条接上"


def test_player_engaged_covers_just_started_playback(tmp_path, monkeypatch):
    """判据本身的真值表，含实机实测到的"刚起播"那一刻（StoppedState+LoadingMedia）。

    实机探针（QMediaPlayer + QAudioOutput，offscreen，assets/sounds/click.wav）：
      ``setSource`` 后立刻 ``play()``  → state=StoppedState, status=LoadingMedia
      约 300ms 后                     → state=PlayingState, status=BufferingMedia
    所以单看 ``playbackState`` 会把刚接管的通道判成空出。这里用最小替身锁住真值表，
    不依赖真实解码/音频设备、不依赖 conftest 的 play() 静音桩。
    """
    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        pytest.skip("QtMultimedia 不可用（缺 libpulse 等系统库）")

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    Playing = QMediaPlayer.PlaybackState.PlayingState
    Stopped = QMediaPlayer.PlaybackState.StoppedState
    Loading = QMediaPlayer.MediaStatus.LoadingMedia
    Buffering = QMediaPlayer.MediaStatus.BufferingMedia
    NoMedia = QMediaPlayer.MediaStatus.NoMedia

    class _StubPlayer:
        """只回答收口要问的两个问题（playbackState / mediaStatus）。"""

        def __init__(self, state, status) -> None:
            self._state, self._status = state, status

        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return self._state

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return self._status

    assert service._player_engaged() is False, "拿不到判据（_FakePlayer）→ 按未接管"
    service._player = _StubPlayer(Stopped, NoMedia)
    assert service._player_engaged() is False, "全新播放器：无媒体 → 未接管"
    service._player = _StubPlayer(Stopped, Loading)
    assert service._player_engaged() is True, "刚起播（实机那一刻）必须判为已接管"
    service._player = _StubPlayer(Playing, Buffering)
    assert service._player_engaged() is True, "正在出声 → 已接管"
    service._player = _StubPlayer(Stopped, QMediaPlayer.MediaStatus.EndOfMedia)
    assert service._player_engaged() is False, "一段播完（通道空出）→ 未接管"
    service._player = None
    assert service._player_engaged() is False, "无播放器 → 未接管"


def test_channel_guard_keeps_speech_queued_across_completions(tmp_path, monkeypatch):
    """通道不空闲时出队必须原样留队：待播语音不得迁进 _queued_fire（P2-1）。

    复现链：预合成在飞 → 豁免语音入队 → 报时到点被 busy 门拦下、排进 _queued_fire
    → 预合成**失败**、结算时报时走即时合成起来（_busy=True）→ 此刻若无占用判据，
    待播语音会被 _fire 撞 busy 门**迁移进 _queued_fire**；下一次结算（报时合成完成、
    刚起播，那一刻 ``playbackState`` 恰好还是 StoppedState）``_drain_queued_fire``
    若无判据就直接 _fire 它 → setSource+play **顶掉还在播的报时**（音源被替换，
    原来的那一段永远等不到 EndOfMedia）。

    断言的是不变量本身：通道不空闲 ⇒ 队列字段原样不动，等真空闲的那一拍（END）。
    ``_player_engaged`` 由播放器替身的 ``play()`` 驱动（见 ``_self_starting_engagement``）；
    "刚起播"这一状态的判据真值由 ``test_player_engaged_covers_just_started_playback``
    用真实枚举锁住。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    exempt = "今天也要认真工作呀。"
    cached_exempt = _cache_mp3(service, exempt)
    engaged = _self_starting_engagement(service, monkeypatch)

    # 09:59:30 距 10:00 只 30s → 预合成在飞
    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))
    assert len(_WorkerSpy.instances) == 1, "预合成在飞（通道被占）"
    precache = _WorkerSpy.instances[0]

    assert service.speak(exempt, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == exempt, "合成在飞 → 豁免语音入队"

    service._on_tick(datetime(2026, 9, 15, 10, 0, 0))        # 报时到点，被 busy 门拦下
    assert service._queued_fire is not None, "报时排队待补"

    precache.finish(error="precache boom")                   # 预合成失败 → 报时即时合成
    _qapp().processEvents()
    assert len(_WorkerSpy.instances) == 2, "报时走即时合成回退"
    chime_worker = _WorkerSpy.instances[1]
    assert service._pending_speech == exempt, \
        "报时合成已起来（通道不空闲）→ 豁免语音必须原样留队，不得迁进 _queued_fire"
    assert service._queued_fire is None, "更不得迁入 _queued_fire（迁进去就再无 END 出口）"

    chime_worker.finish()                                    # 报时合成完成 → 起播
    _qapp().processEvents()
    assert engaged["engaged"] is True, "play() 之后通道即被接管（真实时序）"
    assert service._player.plays == 1
    assert [Path(s).name for s in service._player.sources] == [Path(chime_worker.out_path).name]
    assert service._pending_speech == exempt, "刚起播 → 豁免语音仍留队，不得顶掉报时"
    assert service._queued_fire is None

    engaged["engaged"] = False                               # 报时播完 → EndOfMedia
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._pending_speech is None
    assert [Path(s).name for s in service._player.sources][-1] == cached_exempt.name
    assert service._player.plays == 2, "通道真空闲后豁免语音才出队出声"


def test_queued_fire_left_queued_is_played_at_end_of_media(tmp_path, monkeypatch):
    """被占用判据留队的报时排队项，在通道空出的那一拍（EndOfMedia）必须补播。

    ``_queued_fire`` 只在合成在飞时被写入；若那次合成完成时**起了播**，判据会把它
    留队——END 是此后唯一还会到来的"通道空出"信号，不在这里结算就等于这声报时被
    推迟到下一次合成为止（最坏是下一个报时点，即静默丢报时）。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    engaged = _self_starting_engagement(service, monkeypatch)

    assert service.speak("今天是中秋节。", log_tag="节日提醒") is True   # 合成在飞
    assert len(_WorkerSpy.instances) == 1
    service._on_tick(datetime(2026, 9, 15, 10, 0, 0))                  # 到点报时被拦下
    assert service._queued_fire is not None

    _WorkerSpy.instances[0].finish()                                    # 合成完成 → 起播
    _qapp().processEvents()
    assert engaged["engaged"] is True, "play() 之后通道即被接管（真实时序）"
    assert service._player.plays == 1
    assert service._queued_fire is not None, "刚起播 → 报时排队项留队（不顶掉这一段）"

    engaged["engaged"] = False
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._queued_fire is None, "EndOfMedia 是通道空出的一拍 → 排队项必须结算"
    assert len(_WorkerSpy.instances) == 2, "排队报时走即时合成（缓存未命中）"
    _WorkerSpy.instances[1].finish()
    _qapp().processEvents()

    assert service._player.plays == 2, "排队报时补播出声"


def test_stale_busy_release_drains_pending_speech(tmp_path, monkeypatch):
    """合成超时自愈释放 _busy 后也必须结算待播队列（P2-2，第五类不出队出口）。

    worker 挂死不回调时 ``_busy`` 被 watchdog 复位，此后不会有合成完成回调；若自愈
    只结算 ``_queued_fire``，待在 ``_pending_speech`` 里的豁免语音就永远没人出队
    （免打扰期间它进不来第二次合成，也没有 EndOfMedia）。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    hung = "今天是中秋节。"
    exempt = "今天也要认真工作呀。"
    cached_exempt = _cache_mp3(service, exempt)

    assert service.speak(hung, log_tag="节日提醒") is True   # worker 挂起、永不回调
    assert len(_WorkerSpy.instances) == 1
    _quiet().start(15)
    assert service.speak(exempt, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == exempt

    service._busy_since = _time.monotonic() - (svc_mod._SYNTH_TIMEOUT_S + 1)  # 推过超时
    service._on_tick(datetime(2026, 9, 15, 9, 30, 0))       # 20s tick 驱动超时自愈

    assert service._busy is False, "超时自愈必须复位合成锁"
    assert service._pending_speech is None, "自愈后同样要结算待播队列（不滞留）"
    assert service._player.plays == 1, "豁免的那条照常出队出声"
    assert [Path(s).name for s in service._player.sources] == [cached_exempt.name]


def test_just_started_playback_keeps_new_speech_out_of_the_channel(tmp_path, monkeypatch):
    """刚起播那一刻（LoadingMedia）也算通道被占：speak/play_file 都不得抢通道。

    那一刻 ``playbackState`` 仍是 StoppedState（实机探针见
    ``test_player_engaged_covers_just_started_playback``）：沿用只判 ``playbackState``
    的旧判据会让这一瞬间的 speak() 直发合成、play_file() 直发本地播报，把刚起播的
    那一段顶掉。这里驱动判据本身，锁住两个入口都读它。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    monkeypatch.setattr(service, "_player_engaged", lambda: True)   # 刚起播

    assert service.speak("今天是中秋节。", log_tag="节日提醒") is True
    assert service._pending_speech == "今天是中秋节。", "通道被占 → 必须入队"
    assert _WorkerSpy.instances == [], "不得直发合成（那会起第二路音频）"

    wav = tmp_path / "line.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    assert service.play_file(str(wav)) is False, "通道被占 → 本地播报让路（不排队）"


def test_watchdog_runs_even_when_chime_is_disabled(tmp_path, monkeypatch):
    """报时关闭、只剩节日/点击语音时，watchdog 也必须每拍执行（P2-1）。

    通道由三种消费者共用：报时开关关掉后，节日提醒 / 点击自言仍会用它
    （pet/app.py 的懒创建门控支持这种组合）。``_on_tick`` 若在 enabled 判定后
    才跑 watchdog，唯一的超时自愈入口就永远执行不到——一个不回调的 worker 会
    永久占住 ``_busy``，后续语音只能不断入队/被覆盖，通道永不恢复。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    assert service._cfg["enabled"] is False, "本用例前提：报时关闭（默认值）"
    hung = "今天是中秋节。"
    exempt = "今天也要认真工作呀。"
    cached_exempt = _cache_mp3(service, exempt)

    assert service.speak(hung, log_tag="节日提醒") is True   # worker 挂起、永不回调
    assert service._busy is True
    _quiet().start(15)
    assert service.speak(exempt, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == exempt

    service._busy_since = _time.monotonic() - (svc_mod._SYNTH_TIMEOUT_S + 1)  # 推过超时
    service._on_tick(datetime(2026, 9, 15, 9, 30, 0))        # timer 每拍驱动

    assert service._busy is False, "报时关闭也必须有 watchdog（否则通道永不恢复）"
    assert service._pending_speech is None, "自愈后照旧结算待播语音"
    assert service._player.plays == 1, "豁免的那条照常出队出声"
    assert [Path(s).name for s in service._player.sources] == [cached_exempt.name]


def test_late_callback_of_superseded_synthesis_is_dropped(tmp_path, monkeypatch):
    """超时释放后旧任务的迟到回调必须整条丢弃，不许串到新任务上（P2-2）。

    回调协议只带 path/text/error，没有任务身份（runner/bridge 的三参协议没变）：
    旧任务 A 的迟到回调会取走**新任务 B**的 role/respect_quiet 再处理 A 的音频——
    该被静音的 A 出声、应豁免的 B 被静音。合成代次（起合成与超时释放时递增，
    由闭包随回调带回）补上这个身份。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    service.apply_config()
    a_text = "今天是中秋节。"
    b_text = "今天也要认真工作呀。"

    assert service.speak(a_text, log_tag="节日提醒") is True    # A 在飞，回调暂扣
    assert len(_WorkerSpy.instances) == 1
    worker_a = _WorkerSpy.instances[0]

    _quiet().start(15)
    assert service.speak(b_text, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == b_text

    service._busy_since = _time.monotonic() - (svc_mod._SYNTH_TIMEOUT_S + 1)
    service._release_stale_busy()                              # 超时作废 A → 结算 B
    assert service._busy is True, "B 已被结算起来（新合成在飞）"
    assert len(_WorkerSpy.instances) == 2
    worker_b = _WorkerSpy.instances[1]
    assert worker_b.text == b_text, "第二路合成是 B 自己的文本"
    assert service._pending_speech is None, "B 已出队进入合成"

    worker_a.finish()                                          # —— 交付 A 的迟到回调 ——
    _qapp().processEvents()

    assert service._busy is True, "B 的合成锁不许被 A 的回调清掉"
    assert service._synthesis_role == "speak", "B 的 role 不许被 A 的回调取走"
    assert service._synthesis_respect_quiet is False, "B 的免打扰口径不许被取走"
    assert service._player.plays == 0, "A 已被超时作废 → 不许出声"
    assert service._pending_speech is None and service._queued_fire is None, \
        "A 的回调不得触发任何出队结算"

    worker_b.finish()                                          # —— 再交付 B 的回调 ——
    _qapp().processEvents()

    assert service._player.plays == 1, "B 按自身旗处理（豁免 → 免打扰中也出声）"
    assert _quiet().suppression_active() is True, "本用例前提：B 出声时仍在免打扰"


def test_speak_outlet_requeues_instead_of_replacing_started_playback(tmp_path, monkeypatch):
    """播放那一刻通道已被别的音频接管 → 不 setSource 顶掉它，改为回队（P2-3）。

    复现链：drain 判通道空闲后启动 B 合成（队列已消费）→ 合成期间报时 C 经缓存
    例外（``_fire`` 的缓存命中路径不看通道占用）起播 → B 完成时 ``_play_only`` 会
    ``setSource``+``play`` 把 C 顶掉：C 的音源被替换、永远等不到 EndOfMedia。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    engaged = {"now": False}
    monkeypatch.setattr(service, "_player_engaged", lambda: engaged["now"])
    b_text = "今天也要认真工作呀。"
    c_text = "现在是上午九点整。"
    cached_c = _cache_mp3(service, c_text)

    engaged["now"] = True                                      # 通道被占 → B 入队
    assert service.speak(b_text, log_tag="点击自言自语", respect_quiet=False) is True
    assert service._pending_speech == b_text

    engaged["now"] = False                                     # 通道空出 → B 出队起合成
    service._terminal_statuses = ("END",)
    service._on_media_status("END")
    assert len(_WorkerSpy.instances) == 1, "B 出队后起合成"
    worker_b = _WorkerSpy.instances[0]
    assert worker_b.text == b_text

    service._fire(c_text, datetime.now(), c_text)               # C 经缓存例外起播
    engaged["now"] = True                                       # C 已接管通道
    assert service._player.plays == 1
    assert [Path(s).name for s in service._player.sources] == [cached_c.name]

    worker_b.finish()                                           # B 完成：不许顶掉 C
    _qapp().processEvents()

    assert service._player.plays == 1, "B 不得顶掉还在播的 C"
    assert [Path(s).name for s in service._player.sources] == [cached_c.name], "C 的音源未被替换"
    assert service._pending_speech == b_text, "B 已回队（字段在）"
    assert service._pending_respect_quiet is False, "B 的豁免口径随队带回"
    assert service._queued_fire is None

    engaged["now"] = False                                      # C 播完 → 下一拍再出队
    service._on_media_status("END")
    assert service._pending_speech is None
    assert len(_WorkerSpy.instances) == 2, "B 回队后按既有不变量出队（缓存未命中即合成）"
    _WorkerSpy.instances[1].finish()
    _qapp().processEvents()

    assert service._player.plays == 2, "通道真空闲后 B 才出声"


def test_chime_outlet_requeues_with_the_original_tuple(tmp_path, monkeypatch):
    """role=play 的回队必须携带原 tuple 各字段（报时排队项不许丢内容）。

    报时被占用判据留队后又碰上"播放时刻通道被接管"，回队时要原样带回
    （口播文本 / 时间戳 / 气泡文本 / 气泡开关 / role / 免打扰口径），否则补播时
    会丢掉气泡或换成另一段内容。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    engaged = {"now": False}
    monkeypatch.setattr(service, "_player_engaged", lambda: engaged["now"])

    import time as _time

    service._busy = True                                       # 合成在飞 → 报时排队
    service._busy_since = _time.monotonic()
    service._on_tick(datetime(2026, 9, 15, 10, 0, 0))
    queued = service._queued_fire
    assert queued is not None, "到点报时被 busy 门拦下 → 入队"
    sentence, now, bubble_text, show_bubble, role, respect_quiet = queued
    assert role == "play" and show_bubble is True and bubble_text

    service._busy = False                                      # 通道空出 → 出队起合成
    service._terminal_statuses = ("END",)
    service._on_media_status("END")
    assert service._queued_fire is None
    assert len(_WorkerSpy.instances) == 1
    worker = _WorkerSpy.instances[0]

    engaged["now"] = True                                      # 播报时刻通道被别的音频接管
    worker.finish()
    _qapp().processEvents()

    assert service._player.plays == 0, "被接管时不播"
    assert service._queued_fire == (sentence, now, bubble_text, show_bubble, role,
                                    respect_quiet), "原 tuple 各字段原样回队"

    engaged["now"] = False                                     # 下一拍补播
    service._on_media_status("END")
    assert service._queued_fire is None
    assert len(_WorkerSpy.instances) == 2
    _WorkerSpy.instances[1].finish()
    _qapp().processEvents()

    assert service._player.plays == 1, "通道空闲后补播出声"
    assert [Path(s).name for s in service._player.sources] == [Path(worker.out_path).name]


def test_speak_outlet_requeue_does_not_overwrite_a_newer_request(tmp_path, monkeypatch, caplog):
    """回队不盖新：槽里已有更新的待播语音时，这次旧完成结果直接丢弃。

    断链：``speak("older-A", respect_quiet=False)`` 起 A 合成 → A 在飞时
    ``speak("newer-B")`` 入队（深度 1，按契约留最新一条）→ C 经缓存命中起播 →
    A 完成发现被接管，回队时用 A 覆盖 B 及其旗 → C 结束后出队的是**过期的 A**，
    B 永久丢失。入口方向是新覆盖旧（``speak`` 的"只留最新一条"），回队方向不能
    倒灌成旧覆盖新。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    engaged = _self_starting_engagement(service, monkeypatch)
    older = "今天是中秋节。"
    newer = "今天也要认真工作呀。"
    chime_text = "现在是上午九点整。"
    cached_newer = _cache_mp3(service, newer)
    cached_chime = _cache_mp3(service, chime_text)

    assert service.speak(older, log_tag="点击自言自语", respect_quiet=False) is True
    assert len(_WorkerSpy.instances) == 1, "A 当时通道空闲 → 直发合成"
    worker_a = _WorkerSpy.instances[0]

    assert service.speak(newer, log_tag="节日提醒") is True      # 通道忙 → B 入队
    assert service._pending_speech == newer, "入口方向：队列里是后来的 B"
    assert service._pending_respect_quiet is True

    service._fire(chime_text, datetime.now(), chime_text)       # C 经缓存例外起播
    assert engaged["engaged"] is True, "C 的 play() 接管了通道"
    assert [Path(s).name for s in service._player.sources] == [cached_chime.name]

    with caplog.at_level("DEBUG"):
        worker_a.finish()                                       # A 完成：发现通道被接管
    _qapp().processEvents()

    assert service._pending_speech == newer, "槽里必须仍是更新的 B——A 不得覆盖它"
    assert service._pending_respect_quiet is True, "B 的旗原样保留"
    assert service._player.plays == 1, "过期的 A 不出声"
    assert any("已有更新" in r.getMessage() for r in caplog.records), \
        "丢弃旧结果必须留下可发现的日志"

    engaged["engaged"] = False                                  # C 播完 → 出队的是 B
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._pending_speech is None
    assert [Path(s).name for s in service._player.sources][-1] == cached_newer.name, \
        "出队的是 B（不是过期的 A）"
    assert service._player.plays == 2


def test_chime_outlet_requeue_does_not_overwrite_a_newer_queued_fire(tmp_path, monkeypatch, caplog):
    """回队不盖新（role=play 同型）：_queued_fire 已有更新的报时项时丢弃旧完成结果。"""
    service, _app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    engaged = _self_starting_engagement(service, monkeypatch)
    older = "今天是中秋节。"
    chime_text = "现在是上午九点整。"
    cached_chime = _cache_mp3(service, chime_text)

    service._fire(older, datetime(2026, 9, 15, 10, 0, 0), older)   # 旧报时 A 在飞
    assert len(_WorkerSpy.instances) == 1
    worker_a = _WorkerSpy.instances[0]
    assert service._synthesis_role == "play"

    service._on_tick(datetime(2026, 9, 15, 11, 0, 0))              # 新报时 B 被 busy 门拦下
    queued = service._queued_fire
    assert queued is not None, "新报时 B 进入排队槽"
    assert queued[0] != older, "槽里是更晚的那一条报时"

    service._fire(chime_text, datetime.now(), chime_text)          # C 经缓存例外起播
    assert engaged["engaged"] is True
    assert [Path(s).name for s in service._player.sources] == [cached_chime.name]

    with caplog.at_level("DEBUG"):
        worker_a.finish()                                          # A 完成：通道被接管
    _qapp().processEvents()

    assert service._queued_fire == queued, "槽里必须仍是 B 的原 tuple——A 不得覆盖它"
    assert service._player.plays == 1, "过期的 A 不出声"
    assert any("已有更新" in r.getMessage() for r in caplog.records), \
        "丢弃旧结果必须留下可发现的日志"

    engaged["engaged"] = False                                     # C 播完 → 出队 B
    service._terminal_statuses = ("END",)
    service._on_media_status("END")

    assert service._queued_fire is None, "B 出队"
    assert len(_WorkerSpy.instances) == 2, "B 走即时合成（缓存未命中）"
    _WorkerSpy.instances[1].finish()
    _qapp().processEvents()

    assert service._player.plays == 2, "B 补播出声"


def test_yield_slot_hook_makes_chime_give_up_the_whole_minute(tmp_path, monkeypatch):
    """报时让位：本分钟不发声，且槽位被消费，同分钟后续 tick 不再询问。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)  # 默认关闭（2026-09-19 起），测调度行为须显式开启
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    monkeypatch.setattr(service, "_fire", lambda *a, **k: pytest.fail("让位时不得报时"))

    asked: list[str] = []
    service.yield_slot = lambda slot: asked.append(slot) or True

    service._on_tick(datetime(2026, 2, 17, 9, 0, 5))
    # 槽位带调度后缀（chime_slot 的契约是 "YYYY-MM-DDTHH:MM#<schedule>"）；
    # 让位钩子的入参就是它，消费方按前 16 位取时间即可。
    assert asked == ["2026-02-17T09:00#hourly"]
    assert service._last_slot == "2026-02-17T09:00#hourly", "让位必须消费槽位"

    # 同分钟后一次 tick：槽位已消费，不该再问第二次
    service._on_tick(datetime(2026, 2, 17, 9, 0, 25))
    assert asked == ["2026-02-17T09:00#hourly"]


def test_chime_still_fires_when_hook_declines(tmp_path, monkeypatch):
    """钩子说"不让位"时，报时照常发声——让位是例外而非常态。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)  # 默认关闭，测调度行为须显式开启
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    service.yield_slot = lambda slot: False
    fired: list[str] = []
    monkeypatch.setattr(service, "_fire", lambda sentence, now, bubble=None, **k: fired.append(sentence))

    service._on_tick(datetime(2026, 2, 17, 9, 0, 5))

    assert len(fired) == 1
    assert service._last_slot == "2026-02-17T09:00#hourly"


def test_service_without_hook_behaves_exactly_as_before(tmp_path, monkeypatch):
    """默认无钩子（未注入）时报时行为不变——这是对既有功能的回归防线。"""
    service, app, cfg = _service(tmp_path, monkeypatch)
    cfg.set("voice_chime_enabled", True)  # 默认关闭，测调度行为须显式开启
    cfg.set("voice_chime_schedule", "hourly")
    service.apply_config()
    assert service.yield_slot is None
    fired: list[str] = []
    monkeypatch.setattr(service, "_fire", lambda sentence, now, bubble=None, **k: fired.append(sentence))

    service._on_tick(datetime(2026, 2, 17, 9, 0, 5))

    assert len(fired) == 1


# ============================================================ 端到端：不与报时冲突
# 用户的硬要求："节日语音不要跟时间播报产生冲突"。这里用真实 AppShell（两个服务
# 都真的建起来）+ 打桩发声来验证最终行为，而不是只测单侧逻辑：
#   1) 同一分钟两者都到点 → 只有节日说话；
#   2) 该结论**与两个 QTimer 的触发先后无关**（报时先 tick 也要让位）。


def _duet_shell(tmp_path):
    """建一个"报时每小时 + 节日 09:00 播报"的 AppShell，返回 (shell, chime, festival)。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    cfg.set("festival_reminder_enabled", True)
    cfg.set("festival_reminder_speak", True)
    cfg.set("festival_reminder_mode", "custom")
    cfg.set("festival_reminder_times", "09:00")
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    # 镜像 AppShell.start() 的同步顺序（节日先于报时）——顺序本身也是被测契约：
    # 报时 start() 会立刻 tick 一次，那一刻必须已经能问到"是否让位"。
    shell._sync_festival_service()
    shell._sync_chime_service()
    return shell, shell.voice_chime_service, shell.festival_service


def test_festival_and_chime_never_both_speak_in_the_same_minute(tmp_path, monkeypatch):
    """节日先 tick：报时到点必须让位，本分钟只有节日出声。"""
    shell, chime, festival = _duet_shell(tmp_path)
    assert chime is not None and festival is not None

    chime_said: list[str] = []
    festival_said: list[str] = []
    monkeypatch.setattr(chime, "_fire", lambda s, now, bubble=None, **k: chime_said.append(s))
    monkeypatch.setattr(festival, "_speak", lambda text: festival_said.append(text))

    when = datetime(2026, 2, 17, 9, 0, 5)  # 春节 + 整点 + 节日提醒点，三者重合
    festival._on_tick(when)
    chime._on_tick(when)

    assert festival_said, "节日应当播报"
    assert chime_said == [], "同一分钟报时不得再说话（会叠音或抢通道）"
    shell._on_about_to_quit()


def test_chime_yields_even_when_it_ticks_first(tmp_path, monkeypatch):
    """报时先 tick 也必须让位——这是不能用"节日去抢通道"实现的原因。"""
    shell, chime, festival = _duet_shell(tmp_path)

    chime_said: list[str] = []
    festival_said: list[str] = []
    monkeypatch.setattr(chime, "_fire", lambda s, now, bubble=None, **k: chime_said.append(s))
    monkeypatch.setattr(festival, "_speak", lambda text: festival_said.append(text))

    when = datetime(2026, 2, 17, 9, 0, 5)
    chime._on_tick(when)      # 报时先到
    festival._on_tick(when)   # 节日后到

    assert chime_said == [], "报时先 tick 时同样要让位"
    assert festival_said, "节日仍应播报"
    shell._on_about_to_quit()


def test_chime_speaks_normally_on_a_plain_day(tmp_path, monkeypatch):
    """没有节日的整点，报时照常发声——让位不能变成常态静音。"""
    shell, chime, festival = _duet_shell(tmp_path)

    chime_said: list[str] = []
    monkeypatch.setattr(chime, "_fire", lambda s, now, bubble=None, **k: chime_said.append(s))

    chime._on_tick(datetime(2026, 1, 2, 9, 0, 5))  # 已核实：该日无任何节日/节气

    assert len(chime_said) == 1
    shell._on_about_to_quit()


# ============================================================ edge-tts 懒加载
# 现状（回归）：模块顶层 import edge_tts（实测 ~1.4s + 常驻内存），即使用户
# voice_chime_enabled=False 也照付。改成：顶层只做 find_spec 惰性探测，真正的
# import 推迟到 _TTSWorker 的合成线程；失败走既有 _notify_missing_tts 降级。


def test_service_module_top_level_does_not_import_edge_tts():
    """首次 import 一份 voice_chime_service：不得把 edge_tts 本体拉进 sys.modules。

    在全新解释器里做（见 `_run_python`）：比原来"同进程 re-exec 模块文件"更严——
    没有任何先前状态，且断言不受同进程其它线程的懒加载影响。
    """
    proc = _run_python(
        "import importlib.util, sys\n"
        "import pet.voice_chime_service as svc\n"
        "import pet.tts.edge as edge_mod\n"
        "assert 'edge_tts' not in sys.modules, '模块顶层把 edge_tts import 进来了（白付 1.4s）'\n"
        "assert edge_mod._EDGE_TTS_AVAILABLE is (importlib.util.find_spec('edge_tts') is not None), "
        "'惰性探测结果必须与真实可导入性一致（否则会误报“请 pip install”）'\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_disabled_voice_chime_startup_does_not_import_edge_tts(tmp_path):
    """voice_chime_enabled=False 的启动路径：服务整个不 start，edge_tts 一次都不 import。

    两步走：**构造侧**在进程内判（确定：服务对象必须为 None）；**import 侧**在
    子进程里判（理由见 `_run_python`）。子进程脚本刻意只 `import pet.app`——裸
    `python -c` 里没有 conftest 的弹窗桩，构造 AppShell 有卡在模态对话框的风险，
    而这条断言要的证据只是"import 链里没有 edge_tts"。
    """
    from pet.app import AppShell

    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_enabled", False)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    assert shell.voice_chime_service is None

    proc = _run_python(
        "import sys\n"
        "import pet.app\n"
        "assert 'edge_tts' not in sys.modules, '启动路径把 edge_tts import 进来了'\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_tts_worker_reports_missing_edge_tts_instead_of_raising(tmp_path, monkeypatch):
    """合成线程里 import edge_tts 失败：回调带回 EDGE_TTS_MISSING，不抛给调用方。"""
    import sys

    import pet.voice_chime_service as svc_mod

    monkeypatch.setitem(sys.modules, "edge_tts", None)  # import edge_tts → ImportError
    seen: list = []
    edge_attempt = svc_mod.tts.TtsAttempt(
        provider="edge",
        values={"voice": "zh-CN-XiaoxiaoNeural", "rate": 0, "pitch": 0},
        plan={"provider": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%",
              "pitch": "+0Hz", "ext": "mp3"},
    )
    worker = svc_mod._TTSWorker(
        "现在是上午九点整。",
        (edge_attempt,),
        (tmp_path / "chime.mp3",),
        lambda path, text, error: seen.append((path, text, error)),
    )

    worker.run()  # 真实 run()（不是替身）：验证 import 失败的降级分支

    assert [item[2] for item in seen] == [svc_mod.EDGE_TTS_MISSING]


@pytest.mark.parametrize("frozen", [False, True])
def test_service_bubbles_install_hint_when_edge_tts_import_fails(tmp_path, monkeypatch, frozen):
    """服务收到 EDGE_TTS_MISSING → 走既有「请 pip install edge-tts」降级文案。"""
    import pet.voice_chime_service as svc_mod

    service, app, _cfg = _service(tmp_path, monkeypatch)
    import sys
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)

    class _MissingWorker:
        def __init__(self, text, attempts, paths, on_done) -> None:
            self._out_path = paths[0]
            self._on_done = on_done

        def start(self) -> None:
            self._on_done(
                str(self._out_path), "现在是上午九点整。", svc_mod.EDGE_TTS_MISSING)

    monkeypatch.setattr(svc_mod, "_TTSWorker", _MissingWorker)
    service._fire("现在是上午九点整。", datetime(2026, 9, 15, 9, 0))

    assert app.win.bubbles, "缺 edge-tts 时必须给用户可见提示（不能静默）"
    hint = app.win.bubbles[-1][0]
    if frozen:
        assert "更新" in hint and "pip install" not in hint
    else:
        assert "pip install edge-tts" in hint


def test_fire_blocked_by_busy_queues_and_plays_after_synthesis_completes(tmp_path, monkeypatch):
    """到点回退即时合成不得被 busy 门挡死：预合成在飞时 _fire 必须排队，
    合成完成后自动补播——此前直接丢弃且 _last_slot 已盖戳，到点静默丢报时。"""
    import time as _time
    from datetime import datetime as _dt

    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    # 预合成在飞（_maybe_precache 置 busy，角色 precache）
    service._busy = True
    service._busy_since = _time.monotonic()
    service._synthesis_role = "precache"
    played: list = []
    service._play_and_bubble = lambda path, text, bubble_text=None: played.append(text)
    workers_before = len(_WorkerSpy.instances)

    service._fire("现在时刻，上午十点整", _dt.now(), "10:00")
    assert played == [], "合成在飞时不得立即播放"
    assert len(_WorkerSpy.instances) == workers_before, "合成在飞时不得再起一路合成"

    # 预合成完成：排队的报时必须补播（缓存未落盘则新起一路即时合成）。
    # 首参是回调携带的合成代次（手工模拟的在飞合成 = 当前代次，否则会被当成迟到回调丢弃）
    service._on_synthesized(service._synthesis_generation,
                            str(service._cache_dir / "p.mp3"), "现在时刻，上午十点整", "")
    assert played or len(_WorkerSpy.instances) > workers_before, \
        "合成完成后，被 busy 门拦下的报时必须补播"
    if not played:
        _WorkerSpy.instances[-1].finish(path=str(service._cache_dir / "x.mp3"))
    assert played, "补播链路必须到达播放"


def test_queued_fire_discarded_after_stop(tmp_path, monkeypatch):
    """stop()（关闭开关/退出）后，排队的待补播报一并作废，不得再出声。"""
    import time as _time
    from datetime import datetime as _dt

    service, app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._busy = True
    service._busy_since = _time.monotonic()
    service._synthesis_role = "precache"
    played: list = []
    service._play_and_bubble = lambda path, text, bubble_text=None: played.append(text)
    workers_before = len(_WorkerSpy.instances)

    service._fire("现在时刻，上午十点整", _dt.now(), "10:00")
    service.stop()
    # 首参 = 回调携带的合成代次（手工模拟的在飞合成 = 当前代次；stop 不递增代次，
    # 迟到判定仍走 _stopped 那条路——本用例锁的正是它）
    service._on_synthesized(service._synthesis_generation,
                            str(service._cache_dir / "p.mp3"), "现在时刻，上午十点整", "")

    assert played == []
    assert len(_WorkerSpy.instances) == workers_before, "stop 后不得补播排队项"


# ------------------------------------------------- 点击自言自语朗读（本地预缓存）


def test_local_playback_skips_missing_file_and_busy_channel(tmp_path, monkeypatch):
    """本地文件播报：文件不存在不播；通道忙时既不打断也不排队。"""
    svc = object.__new__(VoiceChimeService)
    svc._busy = False
    svc._player = None
    played: list[str] = []
    monkeypatch.setattr(svc, "_play_only", lambda path: played.append(path) or True)

    assert svc.play_file(str(tmp_path / "nope.wav")) is False
    assert played == []

    wav = tmp_path / "line.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    assert svc.play_file(str(wav)) is True
    assert played == [str(wav)]

    svc._busy = True  # 合成中：点击台词晚几秒再冒出来反而突兀，直接放弃
    played.clear()
    assert svc.play_file(str(wav)) is False
    assert played == []


def test_audio_channel_survives_on_self_talk_speak_alone(tmp_path):
    """报时关闭、只剩点击自言自语朗读时，通道同样不能被释放。

    与节日语音同理：通道随报时开关一起被释放，点击朗读就会「有气泡没声音」。
    """
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("self_talk_enabled", True)
    cfg.set("click_show_self_talk", True)
    cfg.set("self_talk_speak_enabled", True)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    shell._sync_chime_service()
    assert shell.voice_chime_service is not None, "点击朗读需要通道"

    cfg.set("self_talk_speak_enabled", False)
    shell._sync_chime_service()
    assert shell.voice_chime_service is None, "三个消费者都关掉后通道应释放"


def test_audio_channel_not_created_for_self_talk_speak_alone(tmp_path):
    """只想朗读、但点击自言自语本身没开时不该白建通道（懒创建语义不变）。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("self_talk_enabled", False)
    cfg.set("click_show_self_talk", False)
    cfg.set("self_talk_speak_enabled", True)
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    shell._sync_chime_service()
    assert shell.voice_chime_service is None


def test_audio_channel_survives_on_divination_speak_alone(tmp_path):
    """报时 / 节日语音 / 点击朗读全关、只剩「抽签时念出来」时，通道同样不能被释放。

    与节日语音、点击朗读同族：通道随报时开关一起被释放，抽签语音就变成「有气泡没
    声音」——这正是产品阻断①暴露的那条生命周期缺口。
    """
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_enabled", False)
    cfg.set("self_talk_speak_enabled", False)
    cfg.set("click_show_self_talk", False)
    cfg.set("divination_enabled", True)
    cfg.set("divination_speak", True)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    shell._sync_chime_service()
    service = shell.voice_chime_service
    assert service is not None, "抽签语音需要这条通道"
    assert service.is_running() is True, "通道必须真的在跑（配置才会被读进来）"

    cfg.set("divination_speak", False)
    shell._sync_chime_service()          # 保存路径先同步通道生命周期（同自测）
    shell._sync_divination_service()
    assert shell.voice_chime_service is None, "四个消费者都关掉后通道应释放"


def test_divination_speak_uses_the_user_backend_on_a_lazily_created_channel(tmp_path, monkeypatch):
    """只开抽签语音：**懒创建**的通道也必须按用户配置合成（SAPI / 不回退 / 音量 0）。

    产品阻断①：``AppShell._chime_wanted()`` 不含抽签 → 通道随时被释放；抽签取通道
    那一刻新建的 ``VoiceChimeService`` 的 ``_cfg`` 还是模块默认（edge 后端 + 允许
    回退 + 默认音量），于是"选了本机 SAPI、关掉回退、音量 0"的主人点抽签时照旧
    **联网 edge 合成**、按默认音量出声——三个设置项一个都不生效。

    只替两个边界：合成线程（网络 / COM）与播放器（音频设备）；服务本身是真的。
    """
    import datetime as _dt

    import pet.voice_chime_service as svc_mod
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_enabled", False)
    cfg.set("self_talk_speak_enabled", False)
    cfg.set("click_show_self_talk", False)
    cfg.set("divination_enabled", True)
    cfg.set("divination_speak", True)
    cfg.set("voice_chime_tts_backend", "sapi")
    cfg.set("voice_chime_tts_fallback", False)
    cfg.set("voice_chime_volume", 0)
    # 每个后端都自报"可用"：合成链路由配置决定，用例不去猜本机装没装 SAPI / edge
    _WorkerSpy.instances = []
    monkeypatch.setattr(svc_mod, "_TTSWorker", _WorkerSpy)
    for provider in svc_mod.tts.providers():
        monkeypatch.setattr(type(provider), "availability", lambda self, values: False)

    shell = AppShell(_qapp(), cfg, enable_chat=False)
    channel = shell.ensure_audio_channel()
    assert isinstance(channel, VoiceChimeService), "必须是真服务，不是通道桩"
    channel._player = _FakePlayer()
    channel._audio_out = _FakeAudioOut()
    channel._ensure_player = lambda: True

    service = shell._ensure_divination_service()
    monkeypatch.setattr(service, "_bubble", lambda text: None)   # 气泡不是本用例的被测面
    service.draw_now(_dt.date(2026, 10, 16))

    assert _WorkerSpy.instances, "抽签语音必须提交一次合成"
    used = {a.provider for worker in _WorkerSpy.instances for a in worker.attempts}
    assert used == {"sapi"}, f"合成链路只许出现用户选的后端，实际 {sorted(used)}"

    # 预合成回来 → 排队的「抽签中...」进合成；再回来 → 交给播放器（音量必须真为 0）
    _WorkerSpy.instances[-1].finish()
    assert len(_WorkerSpy.instances) >= 2, "排队的「抽签中...」必须补上合成"
    _WorkerSpy.instances[-1].finish()
    assert channel._audio_out.volumes == [0.0], "音量 0 必须真的落到播放器上"

    service.shutdown()   # 收口：投递出去的揭签闭包随停用作废，不赌用例结束时机


def _precached_wav(cfg, text: str) -> Path:
    """按与产品一致的命名约定放一个假音频文件，返回其路径。

    内容必须是**结构自洽**的 wav：查找侧（``self_talk_voice._has_audio``）会核对
    RIFF 头声明的长度，伪头/半截文件按"没有缓存"处理（那是被截断的残file）。
    """
    import hashlib

    name = hashlib.md5(text.encode("utf-8")).hexdigest()[:16] + ".wav"
    voice_dir = Path(cfg.dir) / "self_talk_voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    path = voice_dir / name
    payload = b"\x00\x00" * 16
    fmt = (
        b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00"
        + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
        + b"\x02\x00\x10\x00"
    )
    body = b"WAVE" + fmt + b"data" + len(payload).to_bytes(4, "little") + payload
    path.write_bytes(b"RIFF" + (len(body) - 4).to_bytes(4, "little") + body)
    return path


def _recording_channel(played: list, spoken: list, *, play_ok: bool = True):
    class FakeChannel:
        def is_running(self):
            return True

        def apply_config(self):
            pass

        def play_file(self, path, *, log_tag=""):
            played.append(path)
            return play_ok

        def speak(self, text, *, log_tag="", respect_quiet=True):
            spoken.append(text)
            return True

    return FakeChannel()


def test_speak_self_talk_prefers_precached_file(tmp_path, monkeypatch):
    """预缓存命中 → 直接播本地文件：零延迟、断网也能说。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    text = "今天也要认真工作呀。"
    cached = _precached_wav(cfg, text)
    played: list[str] = []
    spoken: list[str] = []
    monkeypatch.setattr(shell, "ensure_audio_channel",
                        lambda: _recording_channel(played, spoken))

    assert shell.speak_self_talk(text) is True
    assert played == [str(cached)]
    assert spoken == [], "命中预缓存就不该再走在线合成"


def test_speak_self_talk_ignores_empty_cache_file(tmp_path, monkeypatch):
    """0 字节缓存不算命中：宁可回落在线合成出声，也不要播静音。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    text = "好女孩……"
    _precached_wav(cfg, text).write_bytes(b"")  # 写到一半被打断留下的残file
    played: list[str] = []
    spoken: list[str] = []
    monkeypatch.setattr(shell, "ensure_audio_channel",
                        lambda: _recording_channel(played, spoken))

    assert shell.speak_self_talk(text) is True
    assert played == [], "空文件不该交给播放器"
    assert spoken == [text], "要回落在线上合成，别让这句话变成静音"


def test_speak_self_talk_falls_back_to_synthesis_without_cache(tmp_path, monkeypatch):
    """没有预缓存文件时（例如主人新加的台词）仍走在线合成。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    played: list[str] = []
    spoken: list[str] = []
    monkeypatch.setattr(shell, "ensure_audio_channel",
                        lambda: _recording_channel(played, spoken))

    assert shell.speak_self_talk("一句还没预缓存的新台词。") is True
    assert played == []
    assert spoken == ["一句还没预缓存的新台词。"]


def test_speak_self_talk_falls_back_when_local_playback_fails(tmp_path, monkeypatch):
    """本地文件播不出来（通道忙/解码失败）时，这句话不能就此丢掉。"""
    from pet.app import AppShell

    _qapp()
    cfg = Config(base=tmp_path)
    shell = AppShell(_qapp(), cfg, enable_chat=False)

    text = "再陪你一会儿。"
    _precached_wav(cfg, text)
    played: list[str] = []
    spoken: list[str] = []
    monkeypatch.setattr(shell, "ensure_audio_channel",
                        lambda: _recording_channel(played, spoken, play_ok=False))

    assert shell.speak_self_talk(text) is True
    assert played, "先尝试本地文件"
    assert spoken == [text], "本地播不了要回退在线合成"


def test_stop_halts_currently_playing_audio(tmp_path, monkeypatch):
    """stop() 必须停住正在播放的音频，没有播放器时也不许抛。

    关闭开关 / 应用退出走 stop()：此前只置标志、清队列、停 timer，全文件没有
    一处 _player.stop —— 正在出声的这一段会一直放到自然结束（退出路径上还
    可能触碰正在析构的窗口）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    assert service._play_only(str(tmp_path / "chime.mp3")) is True
    assert service._player.plays == 1

    service.stop()
    assert service._player.stops == 1, "正在播放的音频必须被停住"

    # 从未播放过（播放器还没创建）：stop 仍须安全
    service._player = None
    service.stop()


# ============================================================ 通道占用：LoadedMedia 窗口


def test_loaded_media_window_keeps_channel_busy(tmp_path, monkeypatch):
    """LoadedMedia 窗口（``playbackState`` 仍 Stopped）必须判忙（ops-F1）。

    实机探针见 ``_loaded_media_player_class``：``setSource`` + ``play()`` 之后约 31ms
    起有约 200ms 处于 ``StoppedState`` + ``LoadedMedia``，两个枚举判据都不成立。此前
    ``_player_engaged()`` 只看枚举 → ``_channel_idle()`` 判空 → 这一窗口里点「立即
    报时」/另一路合成完成都会 ``setSource`` 顶掉刚起播的那一段（音源被替换，那一段
    永远等不到 EndOfMedia，后面的队列也跟着卡住）。

    断言的是**判据来源**（唯一占用判据仍是 ``_channel_idle``）与后果（顶播/排队）。
    """
    player_class = _loaded_media_player_class()
    if player_class is None:
        pytest.skip("QtMultimedia 不可用（缺 libpulse 等系统库）")
    from PySide6.QtMultimedia import QMediaPlayer

    End = QMediaPlayer.MediaStatus.EndOfMedia
    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    first = "现在是上午九点整。"
    second = "今天也要认真工作呀。"
    cached_first = _cache_file_for(service, cfg, first)
    cached_second = _cache_file_for(service, cfg, second)
    service._player = player_class()
    service._terminal_statuses = (End,)

    assert service._channel_idle() is True, "起播前：通道真空闲"

    assert service._play_only(str(cached_first)) is True, "起播第一条（进入 LoadedMedia 窗口）"

    assert service._player_engaged() is True, "窗口内播放器已接管（已发出 play、未见终止）"
    assert service._channel_idle() is False, "通道必须判忙，否则新语音会顶掉刚起播的这一段"

    assert service.speak(second, log_tag="节日提醒") is True
    assert service._player.plays == 1, "被占的通道不许顶播"
    assert [Path(s).name for s in service._player.sources] == [cached_first.name]
    assert service._pending_speech == second, "只能排队等这一段播完"

    # 这一段播完（终止态 = 唯一"真的结束了"的信号）：标志复位，队列结算，第二条接上。
    service._player.status = End
    service._on_media_status(End)

    assert service._pending_speech is None, "终止态这一拍必须结算队列（否则要等下个报时点）"
    assert service._player.plays == 2, "排在后面的那条接上（判据确实在这一拍空出来了）"
    assert [Path(s).name for s in service._player.sources][-1] == cached_second.name

    # 第二条也播完：通道彻底空出——标志是真的被复位了，不是永久判忙。
    service._player.status = End
    service._on_media_status(End)
    assert service._channel_idle() is True, "都播完后必须空闲，不许永久判忙"


def test_stop_clears_pending_play_flag(tmp_path, monkeypatch):
    """stop() 之后通道必须立刻回到空闲（ops-F1 的复位半边）。

    实机探针：``player.stop()`` 之后 ``mediaStatus`` **仍停在 LoadedMedia**（该组合
    可达，所以"把 LoadedMedia 也算接管"那条更简的改法会让通道永久判忙）。自记标志
    因此必须在 ``stop()`` 里显式复位，否则关掉开关再打开时每一次播报都只能排队。
    """
    player_class = _loaded_media_player_class()
    if player_class is None:
        pytest.skip("QtMultimedia 不可用（缺 libpulse 等系统库）")

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service._player = player_class()

    assert service._play_only(str(tmp_path / "chime.mp3")) is True
    assert service._channel_idle() is False

    service.stop()

    assert service._player.stops == 1
    assert service._channel_idle() is True, "stop() 后必须空闲（mediaStatus 仍滞留 LoadedMedia）"


# ============================================================ 免打扰：切断在播段


def test_quiet_entry_stops_respected_segment(tmp_path, monkeypatch):
    """免打扰开启那一拍切断**正在播放**的尊重免打扰语音（B06）。

    三处静音闸只拦"未起播"（``_fire`` / ``_handle_synthesized`` / 排队入口），所以
    用户手动开免打扰或切全屏时，已经起播的那一段会一直放到完——用户的开关不能等到
    这段放完。走真实订阅链：状态变化 → ``QuietState._notify`` → 服务回调。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.start()
    try:
        sentence = "现在是上午九点整。"
        _cache_mp3(service, sentence)

        service.say_now(sentence)                       # 缓存命中 → 直接起播（报时口径）
        assert service._player.plays == 1
        assert service._current_respect_quiet is True, "报时语音尊重免打扰"

        _quiet().start(15)                              # 用户手动开免打扰

        assert service._player.stops == 1, "在播的报时语音必须立刻停住"
        assert service._current_respect_quiet is None, "当前在播段的记账一并放掉"
        assert service._player_engaged() is False
        assert service._channel_idle() is True, "停住之后通道回到空闲"
    finally:
        service.stop()


def test_quiet_entry_keeps_exempt_segment_playing(tmp_path, monkeypatch):
    """交互应答（点击自言自语，``respect_quiet=False``）在免打扰开启那一拍**照播**。

    入口闸的豁免口径（issue #208：点了就要有回应）同样覆盖"已经起播"这一段——
    切断它等于把用户刚点出来的回应掐掉。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.start()
    try:
        line = "今天也要认真工作呀。"
        cached = _cache_mp3(service, line)

        assert service.play_file(str(cached), log_tag="点击自言自语") is True
        assert service._player.plays == 1
        assert service._current_respect_quiet is False, "本地预缓存台词是交互应答"

        _quiet().start(15)

        assert service._player.stops == 0, "交互应答是既有豁免，不许被切断"
        assert service._current_respect_quiet is False
    finally:
        service.stop()


def test_quiet_listener_follows_service_lifecycle(tmp_path, monkeypatch):
    """免打扰订阅随服务生命周期走：**构造即挂**、start 幂等重挂、stop 摘除。

    构造即挂是复审 #6 的修法：手动通道（立即报时 / 设置页试听 / 点击自言的本地
    播报）只懒创建服务、从不 start()，订阅若只在 start() 注册，这些路径在播的
    那一段就不会被免打扰切掉（见 ``_attach_quiet_listener`` 的理由）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    state = _quiet()
    callback = service._on_quiet_state_changed

    assert list(state._listeners).count(callback) == 1, "构造即挂（手动通道没有 start）"

    service.start()
    assert list(state._listeners).count(callback) == 1
    service.start()                                          # 幂等：不重复注册
    assert list(state._listeners).count(callback) == 1

    service.stop()
    assert callback not in state._listeners, "stop() 必须摘掉订阅"


def test_fullscreen_quiet_cut_follows_config(tmp_path, monkeypatch):
    """全屏等效抑制同样切断在播段，且判据读**配置现值**（``quiet_also_on_fullscreen``）。

    全屏这条边沿只有一个来源（既有全屏 watcher → ``quiet_mode.note_fullscreen``），
    服务必须跟着它走；配置关掉时全屏不是免打扰，同样不许切。
    """
    from pet import quiet_mode

    service, app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.start()
    try:
        sentence = "现在是上午九点整。"
        _cache_mp3(service, sentence)

        app.win.cfg = {"quiet_also_on_fullscreen": False}
        service.say_now(sentence)
        assert service._player.plays == 1
        quiet_mode.note_fullscreen(True)
        assert service._player.stops == 0, "配置关掉时全屏不触发切断"
        quiet_mode.note_fullscreen(False)

        app.win.cfg = {"quiet_also_on_fullscreen": True}
        service.say_now(sentence)
        assert service._player.plays == 2
        quiet_mode.note_fullscreen(True)
        assert service._player.stops == 1, "全屏等效抑制必须切断在播段"
    finally:
        service.stop()


# ============================================================ 预合成：音色同源


def test_precache_worker_uses_resolved_voice(tmp_path, monkeypatch):
    """预合成 worker 用的是 ``resolve_voice`` 替换**之后**的音色（ops-F3）。

    缓存 key 用的是替换后的音色、即时合成 ``_fire`` 传的也是替换后的，只有预合成
    这一处曾经传配置原值：音色下架时预合成会拿一把在线表里不存在的嗓子白试两轮
    （产物还落到另一个 key 上），并正好踩进 45s 超时自愈的竞态。
    """
    import pet.tts.edge as edge_mod
    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    # 在线表里只剩默认音色 → 配置的这把嗓子被判"已被下架"
    # （在线表缓存随 edge 后端搬进 provider，此处钉的是 provider 的缓存）
    monkeypatch.setattr(edge_mod, "_live_voices", frozenset({DEFAULT_VOICE}))
    dead = "zh-CN-XiaohanNeural"
    cfg.set("voice_chime_enabled", True)
    cfg.set("voice_chime_schedule", "hourly")
    cfg.set("voice_chime_voice", dead)
    service.apply_config()
    assert service._cfg["voice"] == dead, "配置里存的仍是那把已下架的音色"

    # 09:59:30 距 10:00 只 30s（在 60s 预合成窗口内）→ 预合成在飞
    service._on_tick(datetime(2026, 9, 15, 9, 59, 30))

    assert len(_WorkerSpy.instances) == 1
    worker = _WorkerSpy.instances[0]
    # 尝试序列里带的必须是**替换后**的音色（provider 的 preflight 换的），不是配置原值
    assert worker.attempts[0].plan["voice"] == DEFAULT_VOICE, \
        "worker 必须拿替换后的音色，不是配置原值"
    # 与缓存 key 同源：产物文件名就是按替换后音色算出来的那个 key
    expected = cache_key(worker.text, service._cfg, provider_id=worker.attempts[0].provider)
    assert worker.out_path.name == f"{expected}.mp3"


# ============================================================ 手动通道：合成超时解锁


def test_manual_channel_arms_unlock_watchdog(tmp_path, monkeypatch):
    """手动通道（立即报时 / 设置页试听）不 start() 也能解锁卡死的合成（ops-S1）。

    这两种路径创建的服务全程不 start() → 20s tick 不跑 → ``_release_stale_busy``
    永远执行不到：worker 万一漏回调，``_busy`` 永久卡死，此后每次手动播报都只能
    进队列、再也不出声。修法是起合成就地布一道**只为解锁**的单次超时。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    assert service.is_running() is False, "手动通道：调度 tick 没在跑"

    service.say_now("现在是上午九点整。")           # _WorkerSpy 永不回调 = worker 漏回调
    assert service._busy is True, "合成在飞（通道被占）"
    assert service._unlock_timer.isActive(), "起合成即布一道解锁超时"
    assert service._unlock_timer.isSingleShot()
    assert service._unlock_timer.interval() >= int(45_000)

    # 表到点：把起合成时刻拨老，跑一次表回调（等价于 worker 再也不回调）
    service._busy_since = _time.monotonic() - 100.0
    service._unlock_timer.timeout.emit()

    assert service._busy is False, "超时后 _busy 必须被解开，否则通道永久卡死"
    assert service._channel_idle() is True
    assert not service._unlock_timer.isActive(), "解锁之后撤表，不留无意义的待触发表"

    # 合成有结局（回调回来）：**合成那一支**的挂钟撤掉。这次完成起了播，于是表按
    # "播放接管上限"重新布（不再是 45s 档）——两个挂钟共用一条表（见 _reararm_watchdog）。
    service.say_now("现在是上午九点整。")
    assert service._unlock_timer.isActive()
    _WorkerSpy.instances[-1].finish()
    assert service._unlock_timer.interval() > int(svc_mod._SYNTH_TIMEOUT_S * 1000), \
        "合成已有结局：不许还挂着 45s 档的合成看门狗"


# ============================================================ 缓存：孤儿临时文件


def test_start_sweeps_orphan_temp_files(tmp_path, monkeypatch):
    """服务启动时清掉缓存目录里的**孤儿**临时文件（ops-F2 的收尾）。

    上一版 worker 直接写最终路径，进程被斩（daemon worker 随退出死掉）就在缓存目录
    留下半截文件；改成临时文件 + 原子换名之后，半截产物只可能叫 ``*.part``，于是按
    名字模式直接删——不按修改时间猜。在跑的合成（在册）不受影响。
    """
    import pet.voice_chime_service as svc_mod

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    sweep_dir = service._cache_dir / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    orphan = sweep_dir / f"abc123.0f1e2d3c4b5a{svc_mod.TEMP_SUFFIX}"
    orphan.write_bytes(b"\x00half-written")                 # 进程被斩留下的半截产物
    inflight = sweep_dir / f"abc456.9a8b7c6d5e4f{svc_mod.TEMP_SUFFIX}"
    inflight.write_bytes(b"\x00synthesizing")
    final = sweep_dir / "abc123.mp3"
    service._cache_dir = sweep_dir
    svc_mod._register_live_temp(inflight)
    try:
        service.start()
        try:
            assert not orphan.exists(), "孤儿临时文件必须被清掉"
            assert inflight.exists(), "在跑的合成登记的临时文件不许动"
            assert svc_mod._cache_hit(final) is False, "半截产物没被换名 → 缓存不命中"
        finally:
            service.stop()
    finally:
        svc_mod._unregister_live_temp(inflight)


# ============================================================ 解锁表：提前响与重布


def test_unlock_timer_is_precise(tmp_path, monkeypatch):
    """解锁表必须是 PreciseTimer（ops-8）。

    ``_unlock_timer`` 是手动通道**唯一**的超时自愈入口，而 Qt 的 CoarseTimer 允许
    比间隔提前最多 5% 触发（46s 的表 ≈ 43.7s 就可能响）：那一刻经过时间还没到
    45s 阈值，若这一响不重布表，单次表就此耗尽。
    """
    from PySide6.QtCore import Qt

    service, _app, _cfg = _service(tmp_path, monkeypatch)

    assert service._unlock_timer.timerType() == Qt.TimerType.PreciseTimer
    assert service._unlock_timer.isSingleShot()


def test_early_unlock_timer_fire_rearms_for_the_remaining_time(tmp_path, monkeypatch):
    """提前响（经过不足阈值）必须按**剩余时间**重布单次表，而不是空转返回（ops-8）。

    手动通道（立即报时 / 设置页试听）没有 20s tick，解锁表是唯一的自愈入口；它
    提前响一次就没了的话，worker 再漏回调，``_busy`` 永久卡死、通道再也不出声。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service._busy = True
    service._busy_since = _time.monotonic() - 10.0
    service._unlock_timer.stop()             # 等价于"表已经提前响过一次"

    service._release_stale_busy()

    assert service._busy is True, "经过不足阈值：不许解锁"
    assert service._unlock_timer.isActive(), "提前响过之后必须按剩余时间重布"
    remaining_ms = service._unlock_timer.interval()
    # 剩余 ≈ 35s（45 - 10，宽预算吸收用例自身耗时）
    assert int(svc_mod._SYNTH_TIMEOUT_S * 1000) - 12_000 <= remaining_ms \
        <= int(svc_mod._SYNTH_TIMEOUT_S * 1000) - 8_000, remaining_ms

    service._busy_since = _time.monotonic() - (svc_mod._SYNTH_TIMEOUT_S + 1.0)
    service._release_stale_busy()

    assert service._busy is False, "超过阈值：解锁"
    assert not service._unlock_timer.isActive(), "解锁即撤表（由谁布由谁撤）"


def test_rearm_does_not_shrink_the_interval_for_the_next_synthesis(tmp_path, monkeypatch):
    """重布留下的短间隔不许被下一次起合成沿用（否则表会越来越早空响）。"""
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    timeout_ms = int(svc_mod._SYNTH_TIMEOUT_S * 1000)
    service._busy = True
    service._busy_since = _time.monotonic() - svc_mod._SYNTH_TIMEOUT_S * 0.9
    service._unlock_timer.stop()
    service._release_stale_busy()
    assert service._unlock_timer.interval() < timeout_ms, "前提：这一响按剩余时间重布"

    # 下一次起合成：看门狗按"合成期限 - 此刻"重新布，也就回到整段间隔（不沿用短表）
    service._busy = True
    service._busy_since = _time.monotonic()
    service._reararm_watchdog()
    restored_ms = service._unlock_timer.interval()

    assert timeout_ms <= restored_ms <= svc_mod._UNLOCK_INTERVAL_MS, restored_ms


# ============================================================ 免打扰：手动通道的监听


def _isolated_quiet(monkeypatch):
    """本用例私有的免打扰状态机（不碰进程级单例）。

    ``VoiceChimeService`` 现在**构造即挂**免打扰监听（见 ``_attach_quiet_listener``），
    而 ``reset_shared_quiet_state()`` 只复位状态、**不动监听表**（监听表是订阅方的
    责任）。用私有实例既让本组用例能断言订阅表，也不给共享表添残留。
    """
    from pet import quiet_mode

    state = quiet_mode.QuietState()
    monkeypatch.setattr(quiet_mode, "shared_quiet_state", lambda: state)
    return state


def test_unstarted_manual_channel_is_cut_when_quiet_mode_starts(tmp_path, monkeypatch):
    """未 start() 的手动通道在免打扰开启时也必须被切（复审 #6）。

    手动报时 ``respect_quiet`` 默认 True，场景真实可达：报时总开关关着、用户点了
    「立即报时」或设置页试听——服务是懒创建的、**全程不 start()**。B06 的监听原先只在
    ``start()`` 注册，于是这一段在免打扰开启后一直放到完，用户的开关等于没生效。
    """
    state = _isolated_quiet(monkeypatch)
    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。手动报时"
    _cache_file_for(service, cfg, sentence)          # 缓存命中 → 直接起播，不打网络

    service.say_now(sentence)

    assert service.is_running() is False, "手动通道：调度 tick 没在跑"
    assert service._player.plays == 1 and service._player.stops == 0
    assert service._current_respect_quiet is True, "手动报时默认尊重免打扰"

    state.start(15)                                  # 用户在它播放期间开了免打扰

    assert service._player.stops == 1, "在播段必须被切（没 start() 也要有监听）"
    assert service._current_respect_quiet is None, "切段后放掉在播口径"


def test_stopped_service_is_detached_from_quiet_state(tmp_path, monkeypatch):
    """stop() 之后状态变化不许再触达已停服务的播放器（悬挂回调防线）。"""
    state = _isolated_quiet(monkeypatch)
    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。停止之后"
    _cache_file_for(service, cfg, sentence)
    service.say_now(sentence)
    assert len(state._listeners) == 1, "构造即挂：监听已在订阅表上"

    service.stop()

    assert state._listeners == [], "stop() 必须摘掉订阅（订阅表持有的是半死服务的引用）"
    stops_before = service._player.stops
    state.start(15)

    assert service._player.stops == stops_before, "已经停掉的服务不许再碰播放器"


def test_quiet_listener_attach_detach_is_idempotent(tmp_path, monkeypatch):
    """构造即挂 / start 重挂 / stop 摘除都必须幂等（重复不叠监听）。"""
    state = _isolated_quiet(monkeypatch)
    service, _app, _cfg = _service(tmp_path, monkeypatch)

    assert len(state._listeners) == 1, "构造即挂"

    service.start()
    service.start()
    assert len(state._listeners) == 1, "重复 start 不叠监听（add_listener 按身份去重）"

    service.stop()
    service.stop()
    assert state._listeners == []

    service.start()
    assert len(state._listeners) == 1, "重新启动要把监听挂回来"
    service.stop()
    assert state._listeners == []


def test_quiet_state_change_is_harmless_for_idle_service(tmp_path, monkeypatch):
    """空闲服务（构造了但从没起播）在免打扰边沿什么都不做。

    订阅表里会长期留着这类回调（构造即挂 + 用例从不 stop），所以它们必须是彻底
    的 no-op：不碰播放器、不出气泡、不发系统通知。
    """
    state = _isolated_quiet(monkeypatch)
    service, app, _cfg = _service(tmp_path, monkeypatch)

    state.start(15)

    assert service._player.sources == [] and service._player.stops == 0
    assert app.win.bubbles == [] and app.notices == []


def test_shared_quiet_listeners_are_pruned_between_cases(tmp_path, monkeypatch):
    """收口体检：构造即挂的回调在用例边界被摘掉，共享订阅表不随用例数增长。

    收口手段的**唯一实现**在 ``tests/conftest.py``（全局 autouse fixture 用的就是
    它，``test_overlay_app_hosts`` 一类"构造完就不管"的域同样受它保护）：这里直接
    调那条实现，锁定它的效果。
    """
    from tests.conftest import drop_stale_chime_quiet_listeners

    shared = _quiet()
    service, _app, _cfg = _service(tmp_path, monkeypatch)

    assert service._on_quiet_state_changed in shared._listeners, "构造挂在进程级单例上"

    drop_stale_chime_quiet_listeners()

    assert service._on_quiet_state_changed not in shared._listeners


# ============================================================ play-pending 标志的兜底


def _stub_qtmultimedia(monkeypatch):
    """把 QtMultimedia 换成"只记两条信号槽"的替身（测试进程里没有音频设备）。

    真实 ``QMediaPlayer`` 在无窗 CI 上能建，但"设备被拔"只能靠 emit 才能复现；
    替身同时给两样东西：**真实接线点**（``_ensure_player`` 里那两行 connect 走的是
    真代码路径）与"停了、媒体已加载"这一档枚举值——正是标志卡死那一刻的现场
    （``playbackState`` 不是 Playing、``mediaStatus`` 不是 Loading，只剩自记标志撑着）。
    """
    import sys
    import types

    class _PlaybackState:
        StoppedState = "stopped"
        PlayingState = "playing"
        PausedState = "paused"

    class _MediaStatus:
        NoMedia = "nomed"
        LoadingMedia = "loading"
        LoadedMedia = "loaded"
        EndOfMedia = "end"
        InvalidMedia = "invalid"

    class _Signal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

        def emit(self, *args):
            for slot in list(self.slots):
                slot(*args)

    class _SpyPlayer:
        PlaybackState = _PlaybackState
        MediaStatus = _MediaStatus
        Error = types.SimpleNamespace(ResourceError="ResourceError", FormatError="FormatError")

        def __init__(self):
            self.mediaStatusChanged = _Signal()
            self.errorOccurred = _Signal()
            self.sources = []
            self.plays = 0
            self.stops = 0
            self._state = _PlaybackState.StoppedState
            self._status = _MediaStatus.NoMedia

        def setAudioOutput(self, out):  # noqa: N802 - 对齐 Qt API
            self.audio_out = out

        def setSource(self, url):  # noqa: N802 - 对齐 Qt API
            self.sources.append(getattr(url, "toLocalFile", lambda: str(url))())
            self._status = _MediaStatus.LoadingMedia

        def play(self):
            self.plays += 1
            # 实机实测：play() 之后 playbackState 仍是 StoppedState、约 31ms 转
            # LoadedMedia、约 237ms 才转 PlayingState。设备被拔就永远停在中间这档。
            self._state = _PlaybackState.StoppedState
            self._status = _MediaStatus.LoadedMedia

        def stop(self):
            self.stops += 1

        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return self._state

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return self._status

    stub = types.ModuleType("PySide6.QtMultimedia")
    stub.QMediaPlayer = _SpyPlayer
    stub.QAudioOutput = _FakeAudioOut
    monkeypatch.setitem(sys.modules, "PySide6.QtMultimedia", stub)
    return stub


def test_player_error_clears_play_pending_and_drains(tmp_path, monkeypatch):
    """播放错误（设备被拔 / 输出异常）必须按"通道空出"结算，而不是永久判忙（ops5.5 P2-1）。

    ``play()`` 之后既没有终止态、也进不了 ``PlayingState`` 时，``_play_pending_end``
    会一直卡 True：``_player_engaged`` 恒真 → 通道永久判忙（``speak`` 永远排队、
    ``play_file`` 永远让路、合成完成永远回队出不来）。``errorOccurred`` 是这类结局
    唯一的信号，必须接上并走终止态那一套（复位标志 + 同序 drain）。
    """
    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    stub = _stub_qtmultimedia(monkeypatch)
    del service._ensure_player                      # 走真实 _ensure_player 的接线
    service._player = None
    sentence = "现在是上午九点整。设备被拔"
    _cache_file_for(service, cfg, sentence)

    service.say_now(sentence)                       # 缓存命中 → 起播 → 置位标志

    player = service._player
    assert isinstance(player, stub.QMediaPlayer)
    assert getattr(service, "_on_player_error", None) in player.errorOccurred.slots, \
        "播放错误必须接上诉，否则标志没有任何兜底"
    assert service._play_pending_end is True
    assert service._channel_idle() is False, "起播之后通道被接管（本用例前提）"

    service._pending_speech = "排在后面的外部播报"
    player.errorOccurred.emit(stub.QMediaPlayer.Error.ResourceError, "audio device lost")

    assert service._play_pending_end is False, "错误之后不许再判忙"
    assert service._player_engaged() is False, "标志放掉且枚举判据也不成立 = 通道空出"
    assert service._current_respect_quiet is None, "这一段结束了，放掉在播口径"
    assert service._pending_speech is None, "错误即通道空出，排队的那条必须结算"
    assert [w.text for w in _WorkerSpy.instances] == ["排在后面的外部播报"]


def test_stuck_play_pending_is_capped_released_and_drained(tmp_path, monkeypatch):
    """兜底上限：标志置位超过「最长合理音频时长」仍未结，就复位并同序 drain（ops5.5 P2-1）。

    覆盖"既不报错也不进 Playing"的静默滞留（某些输出被拔的场景没有任何信号）。
    上限未到时不许释放：真在播的长段被误判空闲会让新语音（drain 补播）顶掉它。
    """
    import time as _time

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。静默滞留"
    _cache_file_for(service, cfg, sentence)

    service.say_now(sentence)
    assert service._play_pending_end is True
    service._pending_speech = "滞留期间排队的播报"

    # 30s：远超实测最长真实段（22 条缓存 1.42~7.73s），但远不足任何合理上限
    service._play_pending_since = _time.monotonic() - 30.0
    service._release_stale_busy()
    assert service._play_pending_end is True, "上限未到不许放掉"
    assert service._channel_idle() is False
    assert service._pending_speech == "滞留期间排队的播报", "更不许提前出队"

    # 1 小时：早就超过上限（阈值是秒级常量，这里不钉具体数值）
    service._play_pending_since = _time.monotonic() - 3600.0
    service._release_stale_busy()

    assert service._play_pending_end is False, "超过上限必须复位，否则通道永久判忙"
    assert service._player_engaged() is False, "标志放掉且枚举判据也不成立 = 通道空出"
    assert service._current_respect_quiet is None
    assert service._pending_speech is None
    assert [w.text for w in _WorkerSpy.instances] == ["滞留期间排队的播报"]


def test_terminal_status_still_clears_flag_and_settles_without_leftover_watchdog(
        tmp_path, monkeypatch):
    """非回归：正常播完照旧复位标志、结算排队项，且不留一条只为播放的上限表。"""
    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。正常播完"
    _cache_file_for(service, cfg, sentence)

    service.say_now(sentence)
    assert service._play_pending_end is True
    service._terminal_statuses = ("END",)

    service._on_media_status("END")

    assert service._play_pending_end is False
    assert service._channel_idle() is True
    assert service._unlock_timer.isActive() is False, "没有在飞的合成就不该留着上限表"


def test_play_pending_cap_does_not_release_a_playing_segment(tmp_path, monkeypatch):
    """上限只兜「卡住」、不掐「在播」：标志不在时枚举判据照样说接管。

    这条钉的是 ``_player_engaged`` 的**判据本身**：自记标志缺失（被任何一条复位路径
    放掉、或服务侧字段本就为空）时，真在 PlayingState 的那一段仍必须判接管——否则
    新语音会当场 ``setSource`` 顶掉它。上限那一拍"不停在播段、连标志都不清"的行为
    由 ``test_play_pending_cap_does_not_touch_a_genuinely_playing_segment`` 单独钉。
    """
    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        pytest.skip("QtMultimedia 不可用")

    service, _app, _cfg = _service(tmp_path, monkeypatch)

    class _PlayingPlayer(_FakePlayer):
        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.PlaybackState.PlayingState

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.MediaStatus.LoadedMedia

    service._player = _PlayingPlayer()
    service._play_pending_end = False            # 标志已被上限兜底放掉

    assert service._player_engaged() is True, "枚举判据仍然判接管"
    assert service._channel_idle() is False


# ============================================================ stop() 的合成锁复位


def test_stop_resets_busy_lock_so_the_object_can_be_reused(tmp_path, monkeypatch):
    """stop() 一并复位 _busy：复用 stop 过的对象走手动通道不许重现 ops-S1（ops5.5 P3-3）。

    当前产品路径上 stop() 之后对象即被丢（``_sync_chime_service`` 把引用置 None），
    所以这不是可达路径的修复，而是**防复用**：复用时会因为残留的 `_busy` 让 ``_fire``
    直接走"合成在飞，排队待补"，之后这个对象再也发不出声音（"立即报时"点了没反应）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.say_now("现在是上午九点整。停止之前")
    assert service._busy is True, "前提：合成在飞"
    assert service._unlock_timer.isActive()

    service.stop()

    assert service._busy is False, "stop() 必须松开合成锁（防复用）"
    assert service._unlock_timer.isActive() is False, "解锁表照旧撤掉"

    _WorkerSpy.instances = []
    service.say_now("现在是上午九点整。停止之后复用")     # 复用同一对象走手动通道

    assert service._queued_fire is None, "残留的 _busy 会把这次手动报时赶进排队"
    assert [w.text for w in _WorkerSpy.instances] == ["现在是上午九点整。停止之后复用"]


# ============================================================ 缓存换名的瞬时冲突重试


def test_publish_retries_transient_replace_conflicts(tmp_path, monkeypatch):
    """换名撞上瞬时共享冲突要重试，而不是判本次合成失败（ops5.5 P3-2）。

    Windows 上目标 mp3 **正被播放器打开**时裸 ``os.replace`` 直接 WinError 5：
    同一段文本的两次合成（预合成 vs 独立设置进程试听）落同一个缓存键就会踩到，
    杀软/索引扫描刚落盘的文件同样如此。这类冲突会自愈，重试比"这次合成白跑"划算。
    """
    import os as _os

    import pet.voice_chime_service as svc_mod

    temp = tmp_path / "chime.part"
    temp.write_bytes(b"audio-bytes")
    target = tmp_path / "chime.mp3"
    attempts = []
    real_replace = _os.replace

    def flaky_replace(src, dst):
        attempts.append((src, dst))
        if len(attempts) <= 2:
            raise PermissionError(13, "Permission denied")     # WinError 5 同族
        return real_replace(src, dst)

    monkeypatch.setattr(_os, "replace", flaky_replace)

    # _publish 现在是静态方法（临时名与最终名都由调用方给），不再依赖实例字段
    assert svc_mod._TTSWorker._publish(temp, target) is True, "瞬时冲突重试之后必须发布成功"
    assert len(attempts) == 3, f"前两次冲突各试一次、第三次成功，实际 {len(attempts)} 次"
    assert target.read_bytes() == b"audio-bytes"


def test_publish_gives_up_after_retry_budget_and_keeps_failure_path(tmp_path, monkeypatch):
    """重试预算耗尽仍失败 → 走既有"换名失败 = 本次合成失败"，临时文件被清。

    这条钉的是失败路径**没有**被重试改掉：报成功会让到点的缓存命中路径播一个不存在
    的文件；临时文件留着则会变成下一轮清扫眼里的孤儿。
    """
    import os as _os

    import pet.voice_chime_service as svc_mod

    results: list[tuple[str, str, str]] = []
    out_path = tmp_path / "chime.mp3"
    attempt = svc_mod.tts.TtsAttempt(
        provider="edge",
        values={"voice": DEFAULT_VOICE, "rate": 0, "pitch": 0},
        plan={"provider": "edge", "voice": DEFAULT_VOICE, "rate": "+0%",
              "pitch": "+0Hz", "ext": "mp3"},
    )
    worker = svc_mod._TTSWorker(
        "现在是上午九点整。换名一直失败", (attempt,), (out_path,),
        lambda path, text, error: results.append((path, text, error)),
    )

    def always_conflict(src, dst):
        raise PermissionError(13, "Permission denied")

    # 让 provider 真实"合成成功"：往 worker 给的临时路径写满字节，随后的换名必然冲突
    def fake_synth(text, plan, temp_path):
        Path(temp_path).write_bytes(b"x" * 64)

    monkeypatch.setattr(svc_mod.tts.get("edge"), "synth", fake_synth)
    monkeypatch.setattr(_os, "replace", always_conflict)

    worker.run()

    assert results, "换名一直失败必须走失败回调，不许静默"
    assert "缓存换名失败" in results[-1][2], results[-1][2]
    assert results[-1][0] == str(out_path)
    assert list(tmp_path.glob(f"*{svc_mod.TEMP_SUFFIX}")) == [], "临时文件必须被清"
    assert not out_path.exists()


# ============================================ 挂钟结算：合成结局不得撤掉播放挂钟


@pytest.mark.parametrize("outcome", ["完成", "失败", "超时"])
@pytest.mark.parametrize("play_watch", [True, False])
def test_synthesis_outcome_settles_watchdog_and_keeps_play_watch(
        tmp_path, monkeypatch, outcome, play_watch):
    """合成结局（完成 / 失败 / 超时）只结算**自己那一支**的挂钟（astra F1）。

    可达序列（手动通道从不 start()，这条单次表是它唯一的自愈入口）：起合成 A →
    A 在飞期间另一请求命中缓存起播 B（``_fire`` 的缓存旁路不看合成锁）→ B 卡在
    没有终止信号的接管态 → A 完成 / 失败 / 超时。**裸撤表**会让 B 的
    ``_PLAY_PENDING_MAX_S`` 兜底永远不到点（手动通道没有 20s tick），通道永久判忙
    （speak 永远排队、play_file 永远让路、合成完成永远回队出不来）；drain 在通道
    不空闲时直接 return，不会替它补布。

    不变量：合成结局之后，**还有未结挂钟就必须还在表上**（且指向它的剩余期限），
    两个挂钟都结了才允许真撤。``play_watch`` 的行就是这两半。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    b_sentence = "现在是上午九点整。B 命中缓存"
    _cache_file_for(service, cfg, b_sentence)

    service.say_now("现在是上午九点整。A 起合成")        # A 起合成（_WorkerSpy 不回调）
    assert service._busy is True
    service.say_now(b_sentence)                          # 缓存旁路：A 在飞时 B 起播
    assert service._busy is True, "前提：A 的合成仍在飞"
    assert service._play_pending_end is True, "前提：B 处于播放接管态"

    if not play_watch:
        # 对照组：B 的挂钟已经正常结掉（终止态），表上只剩合成那一支
        service._terminal_statuses = ("END",)
        service._on_media_status("END")
        assert service._play_pending_end is False

    if outcome == "完成":
        _WorkerSpy.instances[-1].finish()
    elif outcome == "失败":
        _WorkerSpy.instances[-1].finish(error="edge-tts 合成失败")
    else:
        service._busy_since = _time.monotonic() - (svc_mod._SYNTH_TIMEOUT_S + 1.0)
        service._release_stale_busy()

    assert service._busy is False, "前提：合成那一支已经结了"
    timer = service._unlock_timer
    if service._play_pending_end:
        assert timer.isActive(), "播放挂钟还在，表不许被合成结局撤掉"
        assert int(svc_mod._SYNTH_TIMEOUT_S * 1000) < timer.interval() \
            <= int(svc_mod._PLAY_PENDING_MAX_S * 1000), timer.interval()
    else:
        assert not timer.isActive(), "两个挂钟都结了才允许真撤表"


def test_play_watch_still_heals_after_a_synthesis_outcome(tmp_path, monkeypatch):
    """交错之后的兜底仍然到得了点：表还活着 → 到点把卡住的 B 收掉并结算排队项。

    上一条钉的是"表还在"，这条钉它的**后果**：表到点那一拍（手动通道里唯一会来的
    那一拍）必须真的解开 B 并结算排队项，而不是留一条永远不会响的死表。
    """
    import time as _time

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    b_sentence = "现在是上午九点整。B 又命中缓存"
    _cache_file_for(service, cfg, b_sentence)

    service.say_now("现在是上午九点整。A 又起合成")
    service.say_now(b_sentence)
    assert service._busy is True and service._play_pending_end is True

    _WorkerSpy.instances[-1].finish(error="edge-tts 合成失败")   # A 失败（最省事的结局）
    assert service._play_pending_end is True, "前提：B 的挂钟仍在"
    assert service._unlock_timer.isActive(), "表必须还活着，否则 B 的兜底永远不到点"

    service._pending_speech = "卡住期间排队的播报"
    service._play_pending_since = _time.monotonic() - (svc_mod._PLAY_PENDING_MAX_S + 1.0)
    service._unlock_timer.timeout.emit()          # 等价于"表到点"

    assert service._play_pending_end is False, "到点必须解开卡住的接管态"
    assert service._player_engaged() is False
    assert service._pending_speech is None, "通道空出后排队项必须结算"
    assert [w.text for w in _WorkerSpy.instances] == [
        "现在是上午九点整。A 又起合成", "卡住期间排队的播报"]


# ==================================== 上限治愈：卡住的播放器要一起治


def test_play_pending_cap_heals_a_player_stuck_in_loading(tmp_path, monkeypatch):
    """停在 ``StoppedState + LoadingMedia`` 的死局：上限那一拍连播放器一起治（astra 终审）。

    只清自记标志治不彻底：``_player_engaged`` 的第二个子判据 ``mediaStatus == LoadingMedia``
    仍为真 → ``_player_engaged()`` 恒真 → drain 判忙留队；而此时两个期限都空，表也随
    ``_clear_play_pending`` 真撤了（手动通道没有 20s tick）→ 此后再无挂钟可布，通道
    **永久判忙**。

    真正的解法是 **stop() + 清空音源**：Qt 的 ``stop()`` 在 LoadingMedia 期间只写
    ``m_requestedStatus`` 就 return，**不改 mediaStatus**（替身按这条真实行为建模，
    不再把"stop 后转 LoadedMedia"这个待证行为当前提）；``setSource(QUrl())`` 才会让
    mediaStatus 落到 ``NoMedia``（~5ms，探针实测 9.4ms），判据彻底解除。
    """
    import time as _time

    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        pytest.skip("QtMultimedia 不可用")

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。卡在加载"
    _cache_file_for(service, cfg, sentence)

    class _StuckLoadingPlayer(_FakePlayer):
        """按**真实 Qt 行为**建模的卡死播放器：加载永远不完成。

        - ``stop()`` 在 LoadingMedia 期间**不动** mediaStatus（Qt 6.11 FFmpeg 后端
          ``QFFmpegMediaPlayer::stop()``：mediaStatus == LoadingMedia 时只写
          m_requestedStatus 直接 return；只有加载**自然完成**才转 LoadedMedia）；
        - ``setSource(QUrl())``（空源）→ mediaStatus 转 ``NoMedia``，接管判据解除。
        """

        def __init__(self) -> None:
            super().__init__()
            self.status = QMediaPlayer.MediaStatus.LoadingMedia

        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.PlaybackState.StoppedState

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return self.status

        def setSource(self, url) -> None:  # noqa: N802 - 对齐 Qt API
            super().setSource(url)
            target = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
            self.status = (QMediaPlayer.MediaStatus.LoadingMedia if target
                           else QMediaPlayer.MediaStatus.NoMedia)

        def stop(self) -> None:
            super().stop()
            # 真实 Qt：LoadingMedia 期间 stop() 不改 mediaStatus（见类 docstring）

    service._player = _StuckLoadingPlayer()
    service.say_now(sentence)                     # 缓存命中起播 → 置位标志与起点
    assert service._play_pending_end is True
    assert service._player_engaged() is True, "前提：枚举判据也判接管（LoadingMedia）"
    assert service._player.stops == 0 and len(service._player.sources) == 1

    service._pending_speech = "卡死期间排队的播报"
    service._play_pending_since = _time.monotonic() - (svc_mod._PLAY_PENDING_MAX_S + 1.0)
    service._release_stale_busy()                 # 上限到点那一拍（表/tick 的同一个入口）

    assert service._player.stops == 1, "先停住这一段"
    assert service._player.sources[-1] == "", "治愈必须经过'清空音源'这条机制（stop 改不了 LoadingMedia）"
    assert service._player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia
    assert service._player_engaged() is False, "治愈之后通道必须真的空出来"
    assert service._play_pending_end is False
    assert service._pending_speech is None, "治愈即通道空出：排队项同序结算"
    assert [w.text for w in _WorkerSpy.instances] == ["卡死期间排队的播报"]


def test_play_pending_cap_does_not_touch_a_genuinely_playing_segment(tmp_path, monkeypatch):
    """真在 PlayingState 的段：上限只顺延期限起点，不停播放器、不清标志与队列、不动音源。

    "上限只管卡住"：长音频（合法在播）到点被掐，用户听到的就是报时被截断。
    """
    import time as _time

    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        pytest.skip("QtMultimedia 不可用")

    import pet.voice_chime_service as svc_mod

    service, _app, cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    sentence = "现在是上午九点整。长音频在播"
    _cache_file_for(service, cfg, sentence)

    class _LongPlayingPlayer(_FakePlayer):
        def playbackState(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.PlaybackState.PlayingState

        def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
            return QMediaPlayer.MediaStatus.BufferingMedia

    service._player = _LongPlayingPlayer()
    service.say_now(sentence)
    assert service._play_pending_end is True and service._player.stops == 0

    service._pending_speech = "在播期间排队的播报"
    service._play_pending_since = _time.monotonic() - (svc_mod._PLAY_PENDING_MAX_S + 1.0)
    service._release_stale_busy()

    assert service._player.stops == 0, "合法在播的段绝不能被停"
    assert len(service._player.sources) == 1, "在播段的音源也不许被清空（上限不碰它）"
    assert service._play_pending_end is True, "上限不杀合法播放：标志/队列/音源都不动"
    assert service._pending_speech == "在播期间排队的播报", "在播期间不许出队补播（会叠音）"
    assert service._channel_idle() is False, "通道仍被这一段占着"
    # 期限顺延一个窗口再查，而不是原地重布（期限已过，原地重布会算出 ≤0 → 1ms 忙转表）
    interval = service._unlock_timer.interval()
    assert service._unlock_timer.isActive() and \
        int(svc_mod._PLAY_PENDING_MAX_S * 1000) - 2_000 <= interval \
        <= int(svc_mod._PLAY_PENDING_MAX_S * 1000), interval


def test_quiet_cut_heals_a_player_stuck_in_loading(tmp_path, monkeypatch):
    """免打扰切段撞上 LoadingMedia 死局：同样必须清载，不许留下永久判忙（ops5.5 P3）。

    ``_stop_current_segment`` 的三个调用方语义都是"停掉这一段"，而 Qt 的 ``stop()``
    在 ``LoadingMedia`` 期间不改 mediaStatus（见 ``_unload_player_source``）——免打扰
    切段若恰好撞上"加载永不完成"，只 stop 会清掉标志、撤掉表，却把通道留在
    ``LoadingMedia`` 判忙（手动通道没有 20s tick，与超时治愈那条死局同款机制）。
    所以清载是**切段语义的一部分**，统一进 ``_stop_current_segment``。
    """
    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - 缺系统库的 Linux CI 上跳过而非红
        pytest.skip("QtMultimedia 不可用")

    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    service.start()
    try:
        sentence = "现在是上午九点整。切段撞上加载"
        _cache_mp3(service, sentence)

        class _StuckLoadingPlayer(_FakePlayer):
            """按真实 Qt 行为建模：停止不改 LoadingMedia，只有空源才转 NoMedia。"""

            def __init__(self) -> None:
                super().__init__()
                self.status = QMediaPlayer.MediaStatus.LoadingMedia

            def playbackState(self):  # noqa: N802 - 对齐 Qt API
                return QMediaPlayer.PlaybackState.StoppedState

            def mediaStatus(self):  # noqa: N802 - 对齐 Qt API
                return self.status

            def setSource(self, url) -> None:  # noqa: N802 - 对齐 Qt API
                super().setSource(url)
                target = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
                self.status = (QMediaPlayer.MediaStatus.LoadingMedia if target
                               else QMediaPlayer.MediaStatus.NoMedia)

            def stop(self) -> None:
                super().stop()
                # 真实 Qt：LoadingMedia 期间 stop() 不改 mediaStatus

        service._player = _StuckLoadingPlayer()
        service.say_now(sentence)                       # 缓存命中 → 起播（报时口径）
        assert service._current_respect_quiet is True, "前提：这一段尊重免打扰"
        assert service._player_engaged() is True, "前提：枚举判据也判接管（LoadingMedia）"

        _quiet().start(15)                              # 走真实订阅链： notify → 切段

        assert service._player.stops == 1, "在播段必须被切"
        assert service._player.sources[-1] == "", "切段同样要清空音源（stop 拽不出 LoadingMedia）"
        assert service._player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia
        assert service._player_engaged() is False, "通道判据必须解除，不许永久判忙"
        assert service._channel_idle() is True
    finally:
        service.stop()


# ============================================ 卫生级：懒创建通道的释放与半挂监听


def test_disabled_lazy_created_channel_is_released_on_sync(tmp_path, monkeypatch):
    """「不想要」收口必须**无条件**：懒创建过（从不 start()）的通道也要 stop + 置 None。

    ``_ensure_chime_service`` 的懒创建路径（菜单「立即报时」/ 设置页试听）从不
    ``start()``，所以释放不能依赖"曾经运行过"。这条把该性质钉住：如果哪天有人给
    else 分支加上 ``is_running()`` 之类的条件，未启动的懒创建服务就会只被丢引用、
    不被 stop（QTimer / QMediaPlayer / quiet 监听白挂着）。
    """
    from pet.app import AppShell
    from pet import quiet_mode
    import pet.voice_chime_service as svc_mod

    _qapp()
    cfg = Config(base=tmp_path)
    cfg.set("voice_chime_enabled", False)
    cfg.set("festival_reminder_speak", False)
    cfg.set("self_talk_speak_enabled", False)
    cfg.set("click_show_self_talk", False)
    # 不打网络：让每个合成后端都自报不可用（可用性判据现由各 provider 提供）
    for provider in svc_mod.tts.providers():
        monkeypatch.setattr(type(provider), "availability", lambda self, values: True)
    shell = AppShell(_qapp(), cfg, enable_chat=False)
    assert shell.voice_chime_service is None, "前提：三处开关都关，启动期不建通道"

    shell.trigger_voice_chime_now("现在是上午九点整。懒创建")
    service = shell.voice_chime_service
    assert service is not None, "手动通道必须懒创建出服务"
    assert service.is_running() is False, "前提：手动通道从不 start()"
    state = quiet_mode.shared_quiet_state()
    assert service._on_quiet_state_changed in state._listeners, "前提：构造即挂"

    shell._sync_chime_service()

    assert service.is_running() is False
    assert service._stopped is True, "必须是真的 stop()（不是只丢引用）"
    assert service._on_quiet_state_changed not in state._listeners, "免打扰监听一并摘除"
    assert shell.voice_chime_service is None, "不想要时引用必须置 None"


def test_constructor_detaches_quiet_listener_when_attach_fails(tmp_path, monkeypatch):
    """构造期 attach 失败：先摘掉半挂监听再抛出，不留指向半成品服务的悬挂回调（R1）。

    ``__init__`` 末尾挂免打扰监听。若 ``add_listener`` 中途抛异常（依赖半死 / 容器
    实现异常），已经把回调放进表里的实现会让订阅表留着一条指向**构造失败**对象的
    回调——那个对象不会被任何人 ``stop()``，回调永远悬着。防护：attach 抛异常时
    best-effort detach 再原样上抛（异常照旧上报，不吞）。
    """
    from pet import quiet_mode
    from pet.voice_chime_service import VoiceChimeService

    _qapp()
    state = quiet_mode.QuietState()
    monkeypatch.setattr(quiet_mode, "shared_quiet_state", lambda: state)
    real_add = quiet_mode.QuietState.add_listener

    def poisoned_add(self, callback):
        real_add(self, callback)                 # 先真的挂进表里……
        raise RuntimeError("listener table poisoned")

    monkeypatch.setattr(quiet_mode.QuietState, "add_listener", poisoned_add)

    app = type("_App", (), {"config": None, "win": None})()

    with pytest.raises(RuntimeError):
        VoiceChimeService(app)

    assert state._listeners == [], "构造失败之后不许留下半挂的监听"


# ============================================ 卫生级：play_file 失败残留口径


def test_play_file_failure_does_not_leave_respect_quiet(tmp_path, monkeypatch):
    """起播失败时 ``_current_respect_quiet`` 必须复位为 None（R3 卫生）。

    ``play_file`` 先按交互应答口径置 ``False`` 再交给 ``_play_only``；后者在播放器
    建不起来时返回 False，标志却留着——下一条判定读到的会是上一段的残留口径（实际
    无害，因为没有在播段时免打扰切断是 no-op，但状态不干净）。修法是在 ``play_file``
    里复位（``_play_only`` 的契约不动）。
    """
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    target = tmp_path / "click.wav"
    target.write_bytes(b"\x00fake-wav")
    service._current_respect_quiet = None
    service._ensure_player = lambda: False       # 播放器建不起来

    assert service.play_file(str(target), log_tag="点击自言自语") is False

    assert service._current_respect_quiet is None, "起播失败必须把在播口径复位"


def test_play_file_success_keeps_exempt_respect_quiet(tmp_path, monkeypatch):
    """非回归：起播成功的交互应答仍带豁免口径（免打扰期间不许被切断）。"""
    service, _app, _cfg = _service(tmp_path, monkeypatch)
    service.apply_config()
    target = tmp_path / "click.wav"
    target.write_bytes(b"\x00fake-wav")

    assert service.play_file(str(target), log_tag="点击自言自语") is True

    assert service._current_respect_quiet is False, "交互应答照旧豁免"
    assert service._player.plays == 1


def test_resolve_edge_tts_proxy_reads_system_proxy(monkeypatch):
    """#252：系统代理要传给 edge-tts——aiohttp WSS 不读环境变量（trust_env 默认 False），
    必须显式传 proxy；读不到代理时返回 None（直连）。

    代理解析随 edge 后端搬进 provider（``pet/tts/edge.py``），语义一字不变。
    """
    from pet.tts import edge as edge_provider

    monkeypatch.setattr("urllib.request.getproxies",
                        lambda: {"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"})
    assert edge_provider.resolve_proxy() == "http://127.0.0.1:7890"

    # 只有 http 键也要认得
    monkeypatch.setattr("urllib.request.getproxies",
                        lambda: {"http": "http://127.0.0.1:1080"})
    assert edge_provider.resolve_proxy() == "http://127.0.0.1:1080"

    monkeypatch.setattr("urllib.request.getproxies", dict)
    assert edge_provider.resolve_proxy() is None


def test_synthesize_passes_proxy_to_communicate(monkeypatch, tmp_path):
    """#252：合成路径把系统代理显式传给 edge_tts.Communicate。

    合成本体现由 edge 后端承担（``EdgeTtsProvider._save``），这条断言跟着搬过去。
    """
    import asyncio
    import types

    from pet.tts import edge as edge_provider

    captured: dict = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            captured.update(kwargs)

        async def save(self, path):
            Path(path).write_bytes(b"fake")

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))
    monkeypatch.setattr("urllib.request.getproxies",
                        lambda: {"https": "http://127.0.0.1:7890"})

    plan = edge_provider.EdgeTtsProvider().plan({"voice": "zh-CN-XiaoyiNeural", "rate": 0, "pitch": 0})
    asyncio.run(edge_provider.EdgeTtsProvider._save(
        sys.modules["edge_tts"], "文本", "zh-CN-XiaoyiNeural", plan, tmp_path / "out.mp3"
    ))
    assert captured.get("proxy") == "http://127.0.0.1:7890"

    # 无系统代理时不传 proxy（直连）
    captured.clear()
    monkeypatch.setattr("urllib.request.getproxies", dict)
    asyncio.run(edge_provider.EdgeTtsProvider._save(
        sys.modules["edge_tts"], "文本", "zh-CN-XiaoyiNeural", plan, tmp_path / "out2.mp3"
    ))
    assert captured.get("proxy") is None
