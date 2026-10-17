# -*- coding: utf-8 -*-
"""B7a 生命周期收口：屏迁移后的监听重挂/气泡不丢 + ``stop()`` 释放子宠库与在飞供给。

- **F2（确定缺口）**：三条 ``sprite-removed`` 监听（``behavior.forget`` /
  ``probe.forget`` / ``throw_egg.forget``）只在 ``_build`` 注册，**重建 overlay**
  的路径（今天的单窗拓扑已不存在重建，但任何未来"重建窗口"的实现）必须重挂——
  否则屏事件之后再退出任意一只宠，``remove_sprite`` 不再注销行为/探头/彩蛋状态；
- **屏迁移丢气泡（已登记 §2.3.1）**：``_bind_bubble`` 重建跟随器会 close 旧气泡窗，
  新窗口不 show、``_sticky_*`` 也不重挂。单窗下换家不重建窗口/跟随器，这两条
  因此退化成"换家之后气泡与监听都还在"的回归护栏；
- **F3（确定缺口）**：``stop()`` 不遍历子宠库 ``shutdown()``、也不
  ``_cancel_frameseq_provisions()``（口径见 ``_on_about_to_quit`` / ``_on_session_end``）。

纪律：offscreen；真壳 + 真 PetSprite；同步直调 handler，不 sleep。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import tests.test_sprite_menu_facade as fac
from tests.test_overlay_spawn import _make_persistent_shell

app = QApplication.instance() or QApplication([])


def _make_shell(tmp_path, values=None):
    shell, lib = fac._make_shell(tmp_path, values)
    shell.sprite.bind_clip("idle1")
    shell.sprite._rebuild_pixmap()
    return shell, lib


def _migrate(shell):
    """按真实入口把"家"迁到主屏（fake screen → primaryScreen）。

    单窗铺满整个虚拟桌面：换家**不重建窗口**，故这里的前提断言是"还是同一张窗"。
    """
    primary = QGuiApplication.primaryScreen()
    old = shell.overlay
    shell._migrate_to_screen(primary)
    assert shell.overlay is old, "前提：换家不重建合成窗"
    return old


# ---------------------------------------------------------------- F2：监听重挂
def test_migrate_to_screen_keeps_sprite_removed_listeners(tmp_path):
    """换家之后三条 sprite-removed 监听必须仍在（否则退出子宠残留）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        _migrate(shell)
        listeners = shell.overlay._sprite_removed_listeners
        assert shell.behavior.forget in listeners
        assert shell._probe.forget in listeners
        assert shell._throw_egg.forget in listeners
        # 行为状态表是可观测的那一条：真 tick 建态 → 真移除 → 必须注销
        shell.driver.tick_sim(1 / 60.0)
        assert child in shell.behavior._states, "前提：行为表已为该 sprite 建态"
        shell.overlay.remove_sprite(child)
        assert child not in shell.behavior._states, "迁移后退出子宠必须仍注销行为状态"
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


def test_migrate_to_screen_keeps_exit_pet_cleanup_path(tmp_path):
    """走真实退出入口（``exit_pet``）时迁移后的清理链完整。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        _migrate(shell)
        shell.driver.tick_sim(1 / 60.0)
        assert child in shell.behavior._states
        assert shell.exit_pet(child) is True
        assert child not in shell.behavior._states
        assert child not in shell.overlay.sprites
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 屏迁移：气泡不丢
def test_migrate_to_screen_restores_sticky_bubble(tmp_path):
    """粘滞气泡跨屏迁移不丢：重建跟随器后按原内容重新挂上。

    旧版气泡是不随窗重建的独立 Tool 窗，跨屏只跟随不销毁；新版重建跟随器会
    ``follower.close()`` 关掉真气泡窗，若不重挂，用户正在看的审批/提醒气泡
    在拔屏/主屏切换瞬间消失（且 ``_sticky_*`` 仍在 = 状态与画面不一致）。
    """
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.start()
        shell._sticky_bubble_active = True
        shell._sticky_text = "审批：请确认"
        shell._show_bubble_text("审批：请确认", 0, sticky=True)
        assert shell._speech_bubble.isVisible() is True

        _migrate(shell)

        assert shell._sticky_bubble_active is True, "粘滞态跨迁移保留"
        assert shell._speech_bubble.isVisible() is True, "迁移后粘滞气泡必须重新挂上"
        assert shell._speech_bubble._raw_text == "审批：请确认"
    finally:
        shell.stop()
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_migrate_to_screen_restores_transient_bubble(tmp_path):
    """限时（非粘滞）气泡跨屏迁移也不丢：按迁移前抓下的原文案补一次。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.start()
        shell._show_bubble_text("在吗", 3200)
        assert shell._speech_bubble.isVisible() is True

        _migrate(shell)

        assert shell._speech_bubble.isVisible() is True, "迁移后限时气泡必须补回"
        assert shell._speech_bubble._raw_text == "在吗"
    finally:
        shell.stop()
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_migrate_to_screen_does_not_orphan_the_bubble(tmp_path):
    """换家后气泡仍是同一只在显示：既没有孤儿顶层气泡，也没有第二条副本。

    单窗下换家不动窗口/跟随器，所以"旧气泡被替换掉却没收掉"的孤儿路径整体消失；
    这条用例锁住"换家之后桌上恰好只有这一只气泡"。
    """
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.start()
        shell._sticky_bubble_active = True
        shell._sticky_text = "审批：请确认"
        shell._show_bubble_text("审批：请确认", 0, sticky=True)
        old_bubble = shell._speech_bubble

        _migrate(shell)

        assert shell._speech_bubble is old_bubble, "换家不重建气泡"
        assert old_bubble.isVisible() is True, "气泡照旧显示（不因换家消失）"
    finally:
        shell.stop()
        shell.overlay.close()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- F3：stop() 释放
def test_stop_releases_spawned_libraries_and_inflight_provisions(tmp_path):
    """``stop()`` 必须收掉子宠库与在飞帧序列供给线程（复用既有收口路径）。"""
    shell, made = _make_persistent_shell(tmp_path)
    shell.start()
    shell.spawn_pet()
    child = shell._spawned[0]
    child_lib = shell._spawned_libs[child]
    main_lib = shell.lib
    cancels: list = []
    main_cancels: list = []
    child_lib.cancel_frameseq_provision = lambda: cancels.append(1)
    main_lib.cancel_frameseq_provision = lambda: main_cancels.append(1)

    shell.stop()

    assert child_lib.shutdown_calls == 1, "子宠库必须 shutdown（活线程是库的子对象）"
    assert cancels, "子宠库的在飞供给必须取消"
    assert main_cancels, "主库的在飞供给同样取消"


# ---------------------------------------------------------------- 缺陷 13/14：退出收口
def _raiser(message):
    """必抛的收口步骤替身（模拟 shutdown_music_lyric 在半销毁环境抛错）。"""
    def _boom():
        raise RuntimeError(message)
    return _boom


def _record_mandatory_steps(shell):
    """把三项必做项换成记录器（三项各自的真实语义已有专门用例覆盖）。"""
    calls: list = []
    shell.save_position = lambda: calls.append("save_position")
    shell.save_spawned_positions = lambda: calls.append("save_spawned_positions")
    shell._cancel_frameseq_provisions = lambda: calls.append("cancel_provisions")
    return calls


class _RecordingClip:
    """只记录 stop/close 的 clip 替身（``MovieLibrary.shutdown`` 是 close 优先）。"""

    def __init__(self):
        self.stops = 0
        self.closes = 0

    def stop(self):
        self.stops += 1

    def close(self):
        self.closes += 1


def test_about_to_quit_runs_mandatory_steps_when_earlier_step_raises(tmp_path):
    """前置步骤抛错不得让位置持久化与在飞供给取消被整体跳过。

    缺陷 13：``_on_about_to_quit`` 原是一条裸调用链——``shutdown_music_lyric``
    （内部是无守卫的 ``timer.stop()``）/ ``bridge.close()`` / ``overlay.stop()``
    任一抛错，排在后面的 ``save_position`` / ``save_spawned_positions`` /
    ``_cancel_frameseq_provisions`` 全部不执行：位置不落盘，在飞帧序列供给线程
    没人取消（库随壳析构活 QThread = Qt fatal）。
    """
    shell, _lib = _make_persistent_shell(tmp_path)
    calls = _record_mandatory_steps(shell)
    shell.shutdown_music_lyric = _raiser("歌词收尾失败")
    try:
        shell._on_about_to_quit()

        assert calls == ["save_position", "save_spawned_positions", "cancel_provisions"]
    finally:
        shell._delete_runtime_marker()


def test_about_to_quit_shuts_down_main_library(tmp_path):
    """退出收口必须显式 shutdown 主库（缺陷 14）。

    此前 ``_on_about_to_quit`` 对主库只 ``pause_warm``（``stop()`` 则只遍历子宠
    库），主宠在播 reader/clip 的终结（``WebMClip.cleanup`` / ``FrameSeqClip.close``）
    退回 legacy 明确抛弃的"靠 GC + destroyed"收口。
    """
    shell, _lib = _make_persistent_shell(tmp_path)
    main_lib = shell.lib
    try:
        shell._on_about_to_quit()

        assert main_lib.shutdown_calls == 1
    finally:
        shell._delete_runtime_marker()


def test_about_to_quit_shuts_down_real_main_library_and_closes_clips(tmp_path):
    """真库口径：退出后主库 ``_shutdown`` 为真、已建 clip 已 close。"""
    from pet.library import MovieLibrary

    shell, _lib = _make_persistent_shell(tmp_path)
    lib = MovieLibrary(prewarm_enabled=False)     # 真库：走真实 shutdown 收口
    clip = _RecordingClip()
    lib._movies.clear()                           # 只留本用例的替身 clip（不动真素材）
    lib._movies["idle1"] = clip
    shell.lib = lib
    shell.sprite.library = lib
    try:
        shell._on_about_to_quit()

        assert lib._shutdown is True, "主库必须走显式 shutdown（不再依赖 GC + destroyed）"
        assert clip.closes == 1, "在播 clip 必须在退出收口里被 close"
    finally:
        shell._delete_runtime_marker()
        lib.shutdown()   # 幂等；防真库遗留预热定时器


def test_stop_still_releases_libraries_when_earlier_step_raises(tmp_path):
    """``stop()`` 同款加固：前置步骤抛错时，供给取消与库收口仍执行到。"""
    shell, _made = _make_persistent_shell(tmp_path)
    shell.start()
    shell.spawn_pet()
    child_lib = shell._spawned_libs[shell._spawned[0]]
    calls = _record_mandatory_steps(shell)
    shell.shutdown_music_lyric = _raiser("歌词收尾失败")
    try:
        shell.stop()

        assert calls == ["cancel_provisions"]
        assert child_lib.shutdown_calls == 1, "子宠库不得因前置异常被跳过"
        assert shell.lib.shutdown_calls == 1, "主库必须并入同一收口（缺陷 14）"
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()
