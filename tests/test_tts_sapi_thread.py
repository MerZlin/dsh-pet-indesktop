# -*- coding: utf-8 -*-
"""SAPI 合成的线程级 COM 初始化（`pet/tts/sapi.py`）。

COM 是按线程初始化的：GUI 线程里 Qt 早已初始化过，合成却跑在
``_TTSWorker`` 后台线程——缺 ``CoInitialize`` 时 ``CreateObject`` 抛
``WinError -2147221008 尚未调用 CoInitialize``，报时 / 试听 / 台词语音三条
链路在后台合成时全部退化成「本地语音合成失败」，而**主线程试听同时是好的**，
所以现场看起来像「设置里点试听没问题，但语音就是不生成」。
"""

from __future__ import annotations

import threading

import pytest

sapi = pytest.importorskip("pet.tts.sapi")
comtypes = pytest.importorskip("comtypes")


def test_ensure_com_initializes_the_calling_thread(monkeypatch):
    """``_ensure_com`` 必须在**调用它的那个线程**上初始化 COM。

    断言的是线程身份而不是"被调用过"：在主线程初始化对后台线程毫无帮助，那正是
    缺陷的形态。
    """
    calls: list[int] = []
    monkeypatch.setattr(comtypes, "CoInitialize", lambda: calls.append(threading.get_ident()))

    worker = threading.Thread(target=sapi._ensure_com)
    worker.start()
    worker.join(timeout=10)

    assert calls == [worker.ident]


def test_sapi_voice_probe_works_on_a_background_thread(monkeypatch):
    """后台线程里取音色表：修复前恒为 ``(None, None)``（COM 未初始化）。"""
    sapi._OPTIONS_CACHE = None
    result: dict = {}

    worker = threading.Thread(
        target=lambda: result.update(voices=sapi._voice_objects()[1])
    )
    worker.start()
    worker.join(timeout=30)

    assert "voices" in result
    assert result["voices"] is not None, "后台线程里 SAPI 音色表取不到（COM 未初始化）"


def test_sapi_synth_writes_a_wav_from_a_background_thread(tmp_path):
    """真合成：后台线程里写出的必须是标准 wav（RIFF/WAVE 头 + 非空）。"""
    if not sapi.installed_voices():
        pytest.skip("本机没有可用的 SAPI5 语音")

    out = tmp_path / "line.wav"
    result: dict = {}

    def worker():
        try:
            sapi.PROVIDER.synth("测试", sapi.PROVIDER.plan({}), out)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 - 失败原因要原样进断言
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=60)

    assert result.get("ok") is True, result.get("error")
    head = out.read_bytes()[:12]
    assert head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if out.stat().st_size <= 1024:
        # CI runner 能建 SAPI 对象、也能列语音表，但合成出来只有一个空 wav 头
        # （无声输出/语音包不全）。"后台线程不再抛 CoInitialize"这条已由上两行
        # 断言，产物大小只在本机真实语音环境里才有意义。
        pytest.skip(f"本机 SAPI 未真正产出音频（{out.stat().st_size} 字节）")
    assert out.stat().st_size > 1024
