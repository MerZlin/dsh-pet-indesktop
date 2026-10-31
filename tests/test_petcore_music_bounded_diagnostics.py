# -*- coding: utf-8 -*-
"""有界采样 + 部分诊断：任一源挂死都不许让「识别诊断」永久转圈。

现场：设置页点「检测当前播放」后状态永远停在「正在采样…」、按钮永久 disabled，
贴出来的诊断只剩一句「采样超时」。根因是音频会话源（5 处同步 COM）与窗口枚举都
没有超时——它们一卡，整次采集就再也不返回，"哪一层看到了什么"一个字都看不到。

这里的契约：

- ``sampling.collect`` 给 audio / windows 各套一条有界线程：超时只让**该源**为空，
  并在样例上写明"哪个源超时了几秒"，其余源照常返回；
- 源抛异常仍然只是"该源没有数据"，**不谎报超时**（诊断文本里的原因是排障依据，
  猜错原因比不说更糟）；
- ``diagnostics.render`` 把超时行、候选清单都印出来，且不把安装路径/用户名带进
  这段会被用户复制出去的文本。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_bounded_diagnostics_host"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
sampling = importlib.import_module(f"{HOST_PKG}.sampling")
diagnostics = importlib.import_module(f"{HOST_PKG}.diagnostics")
recognizer = importlib.import_module(f"{HOST_PKG}.recognizer")
now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")

#: 测试用的源超时：默认 8 秒会让这条用例跑满 8 秒，注入一个能区分"有界"与
#: "无界"的小值即可（断言的上限留足 CI 的余量）。
_SOURCE_TIMEOUT = 0.2


def _audio_row(exe="cloudmusic.exe", state="active", *, muted=False, peak=0.42, pid=1):
    return SimpleNamespace(pid=pid, exe=exe, state=state, muted=muted, peak=peak)


def _window(exe="foobar2000.exe", title="晴天 - 周杰伦", pid=2):
    return SimpleNamespace(pid=pid, exe=exe, title=title)


def _smtc_degraded(**_kwargs):
    """SMTC 读取超时（已降级）：判定层要退到窗口标题源，不是整拍作废。"""
    return now_playing.SmtcSample(reason="读取系统媒体会话超时。", degraded=True)


# ---------------------------------------------------------------- 有界采样


def test_a_wedged_audio_source_is_bounded_and_the_other_sources_still_report() -> None:
    release = threading.Event()

    def wedged_audio():
        release.wait(10.0)
        return ()

    windows_seen: list[frozenset] = []

    def read_windows(targets):
        windows_seen.append(targets)
        return (_window(),)

    try:
        started = time.monotonic()
        sample = sampling.collect(
            smtc=_smtc_degraded,
            audio=wedged_audio,
            windows=read_windows,
            source_timeout=_SOURCE_TIMEOUT,
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < 2.0, f"音频源挂死必须按 source_timeout 返回（实测 {elapsed:.2f}s）"
    assert "音频会话" in sample.audio_error
    assert "读取超时" in sample.audio_error
    assert sample.audio == ()
    # 其余两源照常给出事实：诊断的三节都必须有内容。
    assert sample.smtc_degraded is True
    assert sample.window_error == ""
    assert [row.title for row in sample.windows] == ["晴天 - 周杰伦"]
    assert windows_seen == [frozenset()]


def test_a_wedged_window_source_is_bounded_without_losing_the_audio_rows() -> None:
    release = threading.Event()

    def wedged_windows(_targets):
        release.wait(10.0)
        return ()

    try:
        started = time.monotonic()
        sample = sampling.collect(
            smtc=_smtc_degraded,
            audio=lambda: (_audio_row(),),
            windows=wedged_windows,
            source_timeout=_SOURCE_TIMEOUT,
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < 2.0, f"窗口源挂死必须按 source_timeout 返回（实测 {elapsed:.2f}s）"
    assert "播放器窗口标题" in sample.window_error
    assert "读取超时" in sample.window_error
    assert sample.windows == ()
    assert sample.audio_error == ""
    assert [row.exe for row in sample.audio] == ["cloudmusic.exe"]


def test_a_raising_source_still_means_no_data_not_a_timeout() -> None:
    """异常与超时必须分开：诊断说的"读取超时"只能来自真的等超时。"""

    def boom():
        raise RuntimeError("COM 炸了")

    sample = sampling.collect(
        smtc=_smtc_degraded,
        audio=boom,
        windows=lambda _targets: (),
        source_timeout=_SOURCE_TIMEOUT,
    )
    assert sample.audio == ()
    assert sample.audio_error == ""
    assert sample.window_error == ""


def test_the_timeout_reason_names_the_source_and_the_budget() -> None:
    """原因文本本身要能定位：哪个源、等了几秒。"""
    release = threading.Event()

    def wedged():
        release.wait(10.0)
        return ()

    try:
        sample = sampling.collect(
            smtc=_smtc_degraded, audio=wedged, source_timeout=_SOURCE_TIMEOUT
        )
    finally:
        release.set()
    assert f"{_SOURCE_TIMEOUT:g} 秒" in sample.audio_error
    assert "音频会话" in sample.audio_error


# ---------------------------------------------------------------- 部分诊断


def test_render_keeps_three_sections_and_adds_the_timeout_lines() -> None:
    sample = sampling.Sample(
        smtc_degraded=True,
        smtc_reason="读取系统媒体会话超时。",
        audio_error="音频会话：读取超时（8 秒）",
        window_error="播放器窗口标题：读取超时（8 秒）",
    )
    text = diagnostics.render(sample, recognizer.decide(sample))

    assert "SMTC：" in text
    assert "音频会话：" in text
    assert "播放器窗口标题：" in text
    assert "音频会话：读取超时（8 秒）" in text
    assert "播放器窗口标题：读取超时（8 秒）" in text
    assert "采样超时：" in text


def test_render_lists_the_candidates_that_were_seen() -> None:
    """候选清单：用户下次贴诊断就能直接看出"窗口源/音频源各自看到了谁"。"""
    sample = sampling.Sample(
        windows=(_window(),),
        audio=(
            _audio_row(),
            _audio_row("chrome.exe", "inactive", peak=0.0, pid=3),
        ),
    )
    text = diagnostics.render(sample, recognizer.decide(sample))

    assert "候选清单：" in text
    window_line = next(line for line in text.splitlines() if "已登记的播放器窗口" in line)
    assert "foobar2000.exe" in window_line
    assert "晴天 - 周杰伦" in window_line
    audio_line = next(line for line in text.splitlines() if "音频会话里的已知播放器" in line)
    assert "cloudmusic.exe" in audio_line
    assert "state=active" in audio_line
    assert "peak=0.42" in audio_line
    # 浏览器不是"已知播放器"：候选清单只收能定位不唱歌原因的那几条。
    assert "chrome" not in audio_line


def test_render_says_none_when_the_candidate_lists_are_empty() -> None:
    sample = sampling.Sample()
    text = diagnostics.render(sample, recognizer.decide(sample))

    assert "已登记的播放器窗口：没有" in text
    assert "音频会话里的已知播放器：没有" in text


def test_a_stuck_source_is_skipped_until_the_stuck_call_returns() -> None:
    """超时后不许每拍都白等满预算（运行时每秒等 8 秒 = 识别停摆）。"""
    _host_package()
    from importlib import import_module

    sampling = import_module(f"{HOST_PKG}.sampling")
    worker = sampling._SourceWorker("test-stuck-source")

    started = time.monotonic()
    first = worker.run(lambda: time.sleep(0.6), 0.1)
    first_s = time.monotonic() - started
    started = time.monotonic()
    second = worker.run(lambda: 42, 0.1)
    second_s = time.monotonic() - started

    assert first is None
    assert 0.08 <= first_s < 0.5
    assert second is None, "降级窗内直接按读不到返回，不再排队"
    assert second_s < 0.05
    time.sleep(0.7)
    assert worker.run(lambda: 42, 1.0) == 42, "卡住那次返回后必须解除降级"


def test_render_reduces_a_path_shaped_session_app_id_to_its_executable() -> None:
    """SMTC app_id 可能是完整安装路径：这段文本要能复制出去，不许带用户名/目录。"""
    full_path = (
        "C:" + chr(92) + "Users" + chr(92) + "someone" + chr(92)
        + "AppData" + chr(92) + "cloudmusic.exe"
    )
    sample = sampling.Sample(
        sessions=(now_playing.Session(app_id=full_path, media_type=1, playing=True),),
    )
    text = diagnostics.render(sample, recognizer.decide(sample))

    assert "cloudmusic.exe" in text
    assert "Users" not in text
    assert "someone" not in text
    assert "\\" not in text
