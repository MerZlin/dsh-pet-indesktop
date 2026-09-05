# -*- coding: utf-8 -*-
"""pytest 全局夹具。

1) 全局测试静音：跑测试时不许真实发声。
   play_sound 的取证日志照常记录（测试仍可断言"调用了播放"），但
   QSoundEffect/QMediaPlayer 的 play 被替换为空操作——测试套件在任何
   机器上跑都不应该让喇叭出声。

2) 无人值守环境（CI/自动化）下，模态 QMessageBox 弹窗会永久阻塞或直接崩溃
   （Fatal: Aborted）。设置对话框（如 modern_settings_dialog）保存开机自启失败时会弹模态
   QMessageBox.warning，所有调用 _save() 的测试在 CI 上都会因此卡死
   （定位手段：pytest -o timeout_method=thread --timeout=90 可 dump 出
   卡住的线程堆栈）。这里用 autouse fixture 全局把 QMessageBox 的静态弹窗
   方法替换为 no-op——任何测试都不会因模态弹窗卡死。需要断言弹窗行为的
   测试可自行 monkeypatch.setattr 覆盖。

   收窄（批 6-8a）：warning/information/critical/about 的返回值在产品代码中
   无分支用途，保持 no-op 即可；question() 是分支型 API，返回 None 不是合法
   StandardButton、会改变调用方分支语义，因此默认返回 StandardButton.No
   （安全拒绝，等同用户点“否”）。需要 Yes/No 特定答案的测试必须局部
   monkeypatch（test_agent_link / test_proactive 已如此）。
"""

import pytest


@pytest.fixture(autouse=True)
def _mute_qt_audio(monkeypatch):
    try:
        from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
    except Exception:
        return
    monkeypatch.setattr(QSoundEffect, "play", lambda self: None)
    monkeypatch.setattr(QMediaPlayer, "play", lambda self: None)


@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    # 纯防卡桩：warning/information/critical/about 的返回值无分支用途，统一 no-op。
    for method in ("warning", "information", "critical", "about"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: None))
    # question() 是分支型 API（调用方按返回值走 Yes/No 分支，如 pet/agent_link.py
    # set_enabled），返回 None 不是合法 StandardButton、会改变分支语义。这里默认
    # 返回 StandardButton.No（安全拒绝，等同用户点“否”）；需要 Yes/No 特定答案的
    # 测试必须局部 monkeypatch（test_agent_link / test_proactive 已如此）。
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )


@pytest.fixture(autouse=True)
def _close_session_writers():
    """会话异步写盘（B8）：每个测试结束后关闭所有后台 writer，
    避免守护线程在 tmp_path 已清理后继续写盘（WinError 145 之类的 teardown 竞态）。"""
    yield
    try:
        from pet.chat import session_store
        session_store.reset_writers_for_tests()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clear_click_sound_pool():
    """每测后清空点击音效池（测试债防线）。

    conftest 只静音了 play()，但设置保存等路径的 warm_click_sound_effects
    会真实创建 QSoundEffect/QMediaPlayer/QAudioOutput。这些 QtMultimedia
    原生对象跨测试累积后，在共享 QApplication 下随机 access violation /
    Fatal abort（全量套件崩溃点会漂移：click_sound 预热循环、气泡图片
    processEvents 均观测到）。每测后 clear() 复位原生对象缓存。
    """
    yield
    try:
        from pet import click_sound
        click_sound._pool.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _close_qt_top_level_widgets():
    """在测试后收口仍存活的应用级后台资源。"""
    yield
    try:
        from pet.agent_link import AgentLinkManager, BaseAgentMonitor
        AgentLinkManager._shutdown_live_for_tests()
        BaseAgentMonitor._shutdown_live_for_tests()
    except Exception:
        pass
    try:
        from pet.library import MovieLibrary
        MovieLibrary._shutdown_live_for_tests()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _close_webm_readers_at_session_end():
    """session 结束强收口所有 webm reader（测试债 #2 防线）。

    全量套件偶发 Windows access violation 的根因是 webm_clip `_reader`
    线程/ffmpeg 进程在测试收尾时未死干净、与解释器进程退出竞态。本 fixture
    在所有测试 teardown 之后做有界收口，目标：套件结束时无 webm-reader-*
    线程存活。只做收口，不改产品代码行为。

    收口对象（两类都覆盖）：
    1. 孤儿注册表（_ORPHAN_REGISTRY.holders()）：模块级强引用持有所有
       「退役池非空」的 clip —— 对每个 clip 调公开 cleanup()；
    2. 仍存活的 webm-reader-* 线程（clip 从未 stop、不在注册表中）：从
       threading 枚举反向定位其 owner clip（reader 线程的 _target 是
       clip._reader 绑定方法，__self__ 即 clip），再调公开 cleanup()。

    每轮 cleanup 后 reap 注册表（有界 join，正常 terminate 后毫秒级退出），
    轮数只作病态场景兜底；仍有存活线程则告警（防线不因自身变红）。
    """
    yield
    import logging
    import threading

    logger = logging.getLogger("pytest.conftest.webm")
    try:
        from pet import webm_clip as webm_clip_mod

        registry = webm_clip_mod._ORPHAN_REGISTRY

        def _clips_with_live_readers():
            clips = set(registry.holders())
            for t in threading.enumerate():
                if t.is_alive() and t.name.startswith("webm-reader-"):
                    owner = getattr(getattr(t, "_target", None), "__self__", None)
                    if owner is not None:
                        clips.add(owner)
            return clips

        for _ in range(5):
            clips = _clips_with_live_readers()
            if not clips:
                break
            for clip in clips:
                try:
                    clip.cleanup()
                except Exception:
                    pass  # clip 已随 Qt C++ 侧销毁等：交由 GC/产品自身兜底
            registry.reap()
        survivors = [
            t.name for t in threading.enumerate()
            if t.is_alive() and t.name.startswith("webm-reader-")
        ]
        if survivors:
            logger.warning(
                "session 结束仍有 %d 个 webm reader 线程存活: %s",
                len(survivors), survivors,
            )
    except Exception:
        pass  # 防线 fixture：任何异常都不应让套件本身变红
