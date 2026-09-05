# -*- coding: utf-8 -*-
"""批5.2 spike：进程内双窗（feature flag 默认关）的机器可验测试。

覆盖批5.2 修复轮（DISPATCH_batch52_fix1）处置清单与验收：
- P0-1：spawn 偏移链（env DSH_PET_SPAWN_OFFSET_INDEX → 主窗 spawn_offset）接线；
- P0-2：flag 关右键退出不注入窗级「退出这只」（走 app.quit 逐位一致）；
- P1-1：每窗各持一个 CollisionIpcSession（runtime_id 不同、互不为 peer-self）；
- P1-2/P2-6：flag 取进程级快照，第二窗标记也是 versioned；
- P1-3：「退出这只」退主窗后实例提升，托盘/灵动岛动作仍指向存活实例；
- P1-5：「退出这只」关闭该窗从属聊天窗/设置窗（防 writer 复活）；
- P1-7：close_root 改为 per-root 屏障（关 A 窗 writer 期间 B 窗 save 不被拒）；
- T-3：close_root 直接单测；
- switch_character：热切换只重建本窗自持的 collision_ipc/broker_facade。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

import pytest
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import pet.app as app_mod
import pet.slot_manager as slot_manager_mod
from pet import catalog
from pet.app import AppShell, PetInstance, _read_spawn_offset_env
from pet.chat import session_store as session_store_mod
from pet.chat.session_store import SessionStore
from pet.collision_ipc import CollisionIpcSession
from pet.config import Config
from pet.window import PetWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _FakeLib:
    def pause_warm(self):
        pass

    def resume_warm(self):
        pass


class _FakeAgentLink:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class _FakeWindow:
    """窗级退出/切换所需的薄替身：记录调用，不触碰真实 Qt 窗口。

    T-2：不硬编码 versioned=True——remove_runtime_marker 读取进程级 flag 快照
    ``_single_process_spawn``（真实 PetWindow 在 _build_window 里同样被写入）。
    """

    def __init__(self):
        self.calls = []
        self.lib = _FakeLib()
        self.agent_link_manager = _FakeAgentLink()
        self.is_shown = True
        # P1-2：进程级 flag 快照（默认 = flag 关）。工厂/测试需按 shell 快照设置。
        self._single_process_spawn = False

    def save_position(self):
        self.calls.append("save")

    def close(self):
        self.calls.append("close")

    def remove_runtime_marker(self):
        self.calls.append("marker_del")
        slot_manager_mod.delete_runtime_marker(
            self.cfg.dir, self.cfg.instance_id, versioned=self._single_process_spawn)

    def detach_collision_session(self):
        self.calls.append("detach_collision")

    def isVisible(self):
        return self.is_shown

    def hide(self, notify=False):
        self.is_shown = False

    def show(self):
        self.is_shown = True

    def deleteLater(self):
        pass


class _FanoutMovie:
    """与 DecodeFanoutHub fan-out 接缝兼容的最小替身（批5.3 生命周期断言用）。"""

    def __init__(self, path: str):
        self.path = path
        self.playback_speed = 1.0
        self._publish_sink = None
        self._feed_source = None
        self.decode_throttle_divisor = 1
        self.decode_pace_external = False

    def set_decode_throttle(self, divisor: int) -> None:
        self.decode_throttle_divisor = max(1, int(divisor))

    def set_decode_pace_external(self, value: bool) -> None:
        self.decode_pace_external = bool(value)


def _make_primary_with_slot(tmp_path):
    """建一个持有 slot-0 锁的主窗 AppShell（spawn 第二个实例会拿到 slot-1）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.save()
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(
        config.dir, preferred_slot=0)
    shell = AppShell(QApplication.instance(), config, enable_chat=True,
                     slot_handle=slot_handle, slot_id=slot_id)
    return shell, config, slot_handle


def _stop_sessions(*insts):
    """测试收口：停掉本测试内启动的进程内碰撞会话（未 start 的为 no-op）。"""
    for inst in insts:
        try:
            inst.collision_ipc.stop()
        except Exception:
            pass


def test_spawn_in_process_creates_isolated_second_window(tmp_path, app, monkeypatch):
    """flag 开时进程内 spawn 生成第二个实例：窗身份/Config/SessionStore 隔离。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    built = []

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        win._single_process_spawn = self.shell._single_process_spawn
        self.win = win
        built.append((self.slot_id, build_tray))
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    monkeypatch.setattr(
        app_mod.PetInstance, "_apply_spawn_offset", lambda self: None)
    monkeypatch.setattr(
        app_mod.PetInstance, "_check_autostart_wanted", lambda self: None)

    primary = shell.instance
    assert len(shell.instances) == 1
    assert primary.slot_id == 0

    second = shell.spawn_in_process_window(1)

    assert len(shell.instances) == 2
    assert second is not primary
    # 窗身份 / slot 分配
    assert second.slot_id == 1
    assert second.config.instance_id == "slot-1"
    # Config 隔离（不同配置文件）
    assert second.config.path != primary.config.path
    # SessionStore 目录隔离
    root_primary = SessionStore(primary.config.dir, primary.config.instance_id).root
    root_second = SessionStore(second.config.dir, second.config.instance_id).root
    assert root_second != root_primary
    # 窗身份：second 拥有独立窗口
    assert second.win is not None and second.win is not primary.win
    # spawn 路径不新建/替换进程级托盘
    assert built[-1][1] is False
    # slot 锁句柄独立
    assert second.slot_handle is not None
    assert second.slot_handle is not primary.slot_handle
    # P1-2：第二窗标记版本化读进程级快照（flag 开 = versioned）
    assert second.win._single_process_spawn is True

    # 释放：停本测试启动的 second 会话 + 解锁两把锁
    _stop_sessions(second)
    second.win.close()
    slot_manager_mod._unlock_file(second.slot_handle)
    second.slot_handle = None
    slot_manager_mod._unlock_file(primary_handle)


def test_spawn_flag_off_keeps_process_launcher(tmp_path, app, monkeypatch):
    """flag 关：spawn_pet 走旧的 launch_new_pet 进程路径（逐位一致）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", False)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)

    launched = []
    monkeypatch.setattr(app_mod, "launch_new_pet", lambda index: launched.append(index))
    shell.spawn_pet()
    shell.spawn_pet()
    assert launched == [1, 2]
    assert len(shell.instances) == 1


def test_exit_window_cleans_window_resources_only(tmp_path, app, monkeypatch):
    """「退出这只」：只收口本窗（位置/prewarm/agent/本窗 writer/slot/标记/
    本窗碰撞会话与 broker），不碰其它窗的进程级资源。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    # 预备第二个实例（主窗仍留在集合里：本测试验证"非最后一窗"的窗级退出）
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(
        config.dir, preferred_slot=1)
    instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
    sec_config = Config(base=tmp_path, instance_id=instance_id)
    sec = PetInstance(
        shell, sec_config, enable_chat=True, slot_handle=slot_handle, slot_id=slot_id)
    win = _FakeWindow()
    win.cfg = sec_config
    win._single_process_spawn = shell._single_process_spawn  # True（P1-2）
    sec.win = win
    shell._instances.append(sec)

    # 预写本窗 runtime 标记（退出这只应删掉它）
    marker = slot_manager_mod.runtime_marker_path(
        config.dir, instance_id, versioned=True)
    marker.write_text(
        json.dumps({"pid": os.getpid(), "x": 0, "y": 0, "w": 100, "h": 100}),
        encoding="utf-8")

    # 对本窗 session writer 打点（close_writer_for_root 应被调用）
    sec_root = SessionStore(sec_config.dir, sec_config.instance_id).root
    closed_roots = []
    monkeypatch.setattr(
        session_store_mod, "close_writer_for_root",
        lambda root, timeout=10.0: closed_roots.append(str(root)) or True)

    # 进程级资源打点：本窗碰撞会话应停；共享解码 hub（进程级）绝不被单窗退出
    # 拆除（各窗共用；真收口只在全部退出的 stop_all）。主窗（其它窗）的会话/资源
    # 绝不应被动。
    sec_ipc_stop = []
    primary_ipc_stop = []
    permanent_calls = []
    monkeypatch.setattr(sec.collision_ipc, "stop", lambda: sec_ipc_stop.append(1))
    monkeypatch.setattr(shell.instance.collision_ipc, "stop",
                        lambda: primary_ipc_stop.append(1))
    monkeypatch.setattr(
        session_store_mod, "close_all_writers",
        lambda timeout=10.0, permanent=False: permanent_calls.append(permanent) or True)

    # 批5.3：各窗共用同一进程级 hub（共享解码），单窗退出绝不拆它——先在此
    # 建一个共享源，退出后仍应存活（hub 不因单窗退出而清空）。
    hub = shell._decode_hub
    pub_movie = _FanoutMovie(str(tmp_path / "idle.webm"))
    assert hub is sec.broker_facade is shell.instance.broker_facade, \
        "批5.3：broker_facade 已是进程级共享 hub"
    assert hub.shareable_start("idle", pub_movie) == "publish"
    assert hub._sources, "共享源已建立（发布者）"

    assert len(shell.instances) == 2
    shell._on_window_exit_requested(sec)

    # 窗级收口顺序：save → 删标记 → close
    assert win.calls == ["save", "marker_del", "close"]
    assert win.lib is not None
    assert win.agent_link_manager.shutdown_calls == 1
    assert not marker.exists(), "「退出这只」应删除本窗 runtime 标记"
    assert sec.slot_handle is None, "「退出这只」应释放本窗 slot 锁"
    assert closed_roots == [str(sec_root)], "只关本窗 sessions-slot-N 的 writer"
    # 本窗碰撞会话被停；主窗（其它窗）的未被动
    assert sec_ipc_stop == [1]
    assert primary_ipc_stop == []
    assert permanent_calls == []
    # 共享解码 hub 不被单窗退出拆除（进程级：各窗共用，真收口仅在全部退出）
    assert hub._sources, "单窗退出不得清空共享解码 hub 的源表"
    # 主窗仍在（非最后一窗不触全进程退出）
    assert len(shell.instances) == 1

    slot_manager_mod._unlock_file(primary_handle)


def _alive_pid() -> int:
    # 用当前测试进程的 pid（一定存活），用于构造"活着"的标记
    return os.getpid()


def test_switch_character_rebuilds_own_session_and_broker(tmp_path, app, monkeypatch):
    """C2 地雷随「不再共享」消解：热切换只重建本窗自持的 collision_ipc /
    broker_facade（旧会话被停、新会话被 start、对象 id 变化），不碰其它窗。"""
    config = Config(tmp_path)
    shell = AppShell(QApplication.instance(), config, enable_chat=True)

    monkeypatch.setattr(
        app_mod.PetInstance, "_create_library", lambda self, cid: _FakeLib())

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        win._single_process_spawn = self.shell._single_process_spawn
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)

    # 主窗先有一个窗口
    win0 = _FakeWindow()
    win0.cfg = config
    shell.instance.win = win0

    # 第二个窗（独立会话/资源）：其 collision_ipc/broker 不应被 touch
    sec = PetInstance(shell, Config(tmp_path, instance_id="slot-1"), enable_chat=True)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)

    old_ipc = shell.instance.collision_ipc
    old_broker = shell.instance.broker_facade
    ipc_stop = []
    broker_shutdown = []
    monkeypatch.setattr(old_ipc, "stop", lambda: ipc_stop.append(1))
    monkeypatch.setattr(old_broker, "shutdown", lambda: broker_shutdown.append(1))
    sec_ipc_id = id(sec.collision_ipc)
    sec_broker_id = id(sec.broker_facade)

    char_ids = catalog.list_available_characters()
    current = str(config.get("character", catalog.DEFAULT_CHARACTER))
    target = next((c for c in char_ids if c != current), "not-default-character")

    shell.instance.switch_character(target)

    # 本窗旧碰撞会话被停并被重建（对象 id 变化）；进程级共享 hub 不被重建
    #（各窗共用，批5.3）。
    assert ipc_stop == [1], "switch_character 应停本窗旧碰撞会话"
    assert broker_shutdown == [], \
        "switch_character 不应关进程级共享解码 hub（批5.3 各窗共用）"
    assert shell.instance.collision_ipc is not old_ipc, "本窗 collision_ipc 应重建"
    assert shell.instance.broker_facade is old_broker, \
        "进程级解码 hub 不被重建（各窗共用同一份）"
    assert shell.instance.collision_ipc._thread.isRunning(), "新会话应被 start"
    # 其它窗的会话/资源未被动
    assert id(sec.collision_ipc) == sec_ipc_id
    assert id(sec.broker_facade) == sec_broker_id
    assert shell.instance.win is not win0

    # 收口：停新会话（避免遗留运行中的独占 QLocal server）
    _stop_sessions(shell.instance, sec)


def test_runtime_marker_versioned_name_avoids_legacy_glob(tmp_path, app):
    """R4：flag 开时标记用版本化新名（不被旧 'runtime-*.json' glob 匹配），
    且新版读取侧同时认新旧两种命名。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.save()

    ver_path = slot_manager_mod.runtime_marker_path(
        config.dir, config.instance_id, versioned=True)
    # 新名不匹配旧 glob（旧 glob 只认 'runtime-*.json' 前缀）
    assert ver_path.name.startswith("pet-runtime-v2-")
    assert len(list(config.dir.glob("runtime-*.json"))) == 0, \
        "新窗（flag 开）不得写入旧格式标记"

    # 写新标记
    slot_manager_mod.write_runtime_marker(
        config.dir, config.instance_id, 10, 10, 100, 100, versioned=True)
    assert ver_path.exists()

    # 新版读取侧：旧名（活 pid 的旧标记）与新名都会被读到
    leg = config.dir / f"runtime-{_alive_pid()}.json"
    leg.write_text(json.dumps({"pid": _alive_pid(), "x": 1, "y": 1, "w": 5, "h": 5}),
                   encoding="utf-8")

    live = slot_manager_mod.read_live_instances(config.dir)
    assert len(live) == 2


def test_runtime_marker_versioned_off_keeps_legacy_name(tmp_path, app):
    """R4：flag 关时仍用旧名 runtime-<pid>.json（与现状逐位一致）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", False)
    config.save()
    leg = slot_manager_mod.runtime_marker_path(
        config.dir, config.instance_id, versioned=False)
    assert leg.name == f"runtime-{os.getpid()}.json"


def test_spawn_offset_env_wired_to_primary_instance(tmp_path, app, monkeypatch):
    """P0-1：spawn 偏移链接线（env → 主窗 spawn_offset），flag 关 spawn 子进程
    仍与母桌宠错开落位。"""
    monkeypatch.setenv("DSH_PET_SPAWN_OFFSET_INDEX", "3")
    assert _read_spawn_offset_env() == 3
    monkeypatch.setenv("DSH_PET_SPAWN_OFFSET_INDEX", "-2")
    assert _read_spawn_offset_env() == 0
    monkeypatch.setenv("DSH_PET_SPAWN_OFFSET_INDEX", "")  # 空串
    assert _read_spawn_offset_env() == 0
    monkeypatch.delenv("DSH_PET_SPAWN_OFFSET_INDEX", raising=False)
    assert _read_spawn_offset_env() == 0

    # env → AppShell(spawn_offset=...) → PetInstance._spawn_offset
    offset = _read_spawn_offset_env()
    config = Config(tmp_path)
    shell = AppShell(QApplication.instance(), config, enable_chat=True,
                     spawn_offset=offset)
    assert shell.instance._spawn_offset == 0  # 无 env 时默认 0
    shell2 = AppShell(QApplication.instance(), Config(tmp_path), enable_chat=True,
                      spawn_offset=5)
    assert shell2.instance._spawn_offset == 5


def test_exit_primary_promotes_primary_window(tmp_path, app, monkeypatch):
    """P1-3：「退出这只」退主窗后实例提升——托盘/灵动岛/Dock 动作指向存活实例。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    primary = shell.instance
    primary.win = _FakeWindow()
    primary.win.cfg = config
    primary.win._single_process_spawn = True

    # 第二个实例即将成为新主窗
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config.dir, preferred_slot=1)
    sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                      enable_chat=True, slot_handle=slot_handle, slot_id=slot_id)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec_win._single_process_spawn = True
    sec.win = sec_win
    shell._instances.append(sec)

    monkeypatch.setattr(session_store_mod, "close_writer_for_root",
                        lambda root, timeout=10.0: True)
    monkeypatch.setattr(shell.instance.collision_ipc, "stop", lambda: None)
    monkeypatch.setattr(shell.instance.broker_facade, "shutdown", lambda: None)
    monkeypatch.setattr(sec.collision_ipc, "stop", lambda: None)
    monkeypatch.setattr(sec.broker_facade, "shutdown", lambda: None)

    assert shell.instance is primary
    shell._on_window_exit_requested(shell.instance)

    # 主窗退出后：新列表头（sec）成为主窗，self.instance 更新为存活实例
    assert len(shell.instances) == 1
    assert shell.instance is sec
    assert shell.instance is shell.instances[0]
    assert shell._instances[0].win is sec_win
    assert shell.instance.win is sec_win

    # 收口：释放 sec 持有的 slot-1 锁（主窗锁已在退出处理器中解锁）
    slot_manager_mod._unlock_file(sec.slot_handle)
    sec.slot_handle = None


def test_exit_window_closes_subwindows(tmp_path, app, monkeypatch):
    """P1-5：「退出这只」关闭该窗从属聊天窗/设置窗并断开引用（防 writer 复活）。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    shell.instance.win = _FakeWindow()
    shell.instance.win.cfg = config
    shell.instance.win._single_process_spawn = True

    saves = []

    class _Store:
        def save(self, session):
            saves.append(session)

    class _Subwindow:
        def __init__(self):
            self.store = _Store()
            self.session = object()
            self.deleted = False

        def close(self):
            closed.append(self)

        def deleteLater(self):
            self.deleted = True

    sub_legacy = _Subwindow()
    sub_modern = _Subwindow()
    sub_quick = _Subwindow()
    sub_settings = _Subwindow()
    sub_mod_settings = _Subwindow()
    shell.instance.legacy_chat_window = sub_legacy
    shell.instance.modern_chat_window = sub_modern
    shell.instance.quick_chat = sub_quick
    shell.instance.chat_settings_dialog = sub_settings
    shell.instance.modern_settings_dialog = sub_mod_settings
    shell.instance.chat_window = sub_legacy
    closed = []

    # 预备第二个实例（让退出主窗不是最后一窗，避免触发 app.quit）
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config.dir, preferred_slot=1)
    sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                      enable_chat=True, slot_handle=slot_handle, slot_id=slot_id)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec_win._single_process_spawn = True
    sec.win = sec_win
    shell._instances.append(sec)

    monkeypatch.setattr(session_store_mod, "close_writer_for_root",
                        lambda root, timeout=10.0: True)
    monkeypatch.setattr(shell.instance.collision_ipc, "stop", lambda: None)
    monkeypatch.setattr(shell.instance.broker_facade, "shutdown", lambda: None)
    monkeypatch.setattr(sec.collision_ipc, "stop", lambda: None)
    monkeypatch.setattr(sec.broker_facade, "shutdown", lambda: None)

    shell._on_window_exit_requested(shell.instance)

    # 从属窗全部被 close + deleteLater 调度
    assert set(closed) == {sub_legacy, sub_modern, sub_quick, sub_settings, sub_mod_settings}
    # 三聊天窗 live session 被保存（P0-2）
    assert len(saves) == 3
    # 实例引用被清空（P1-5 防止 writer 复活的关键——窗口被解除持有/复用）
    assert shell.instance.legacy_chat_window is None
    assert shell.instance.modern_chat_window is None
    assert shell.instance.quick_chat is None
    assert shell.instance.chat_settings_dialog is None
    assert shell.instance.modern_settings_dialog is None
    assert shell.instance.chat_window is None
    # 主窗提升为 sec，未触发 app.quit（第二窗仍在）
    assert shell.instance is sec

    slot_manager_mod._unlock_file(sec.slot_handle)
    sec.slot_handle = None


def test_exit_flag_off_does_not_inject_on_exit_window(tmp_path, app, monkeypatch):
    """P0-2/T-4：flag 关右键退出等价——on_exit_window 不注入，
    _request_quit 走旧 app.quit 分支（逐位一致）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", False)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    inst = shell.instance
    win = _FakeWindow()
    win.cfg = config
    inst.win = win
    inst._wire_window(win)
    # flag 关：不注入窗级「退出这只」
    assert win.on_exit_window is None

    # _request_quit 在 on_exit_window 缺失时走 app.quit
    quit_calls = []
    _app = QApplication.instance()
    monkeypatch.setattr(_app, "quit", lambda: quit_calls.append(1))
    win._active_context_menu = None
    PetWindow._request_quit(win)
    assert quit_calls == [1], "flag 关右键退出应走 app.quit（与现状逐位一致）"


def test_exit_flag_on_injects_on_exit_window(tmp_path, app, monkeypatch):
    """P0-2：flag 开注入窗级「退出这只」；_request_quit 走窗级退出分支。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    inst = shell.instance
    win = _FakeWindow()
    win.cfg = config
    win._single_process_spawn = True
    inst.win = win
    inst._wire_window(win)
    assert callable(win.on_exit_window), "flag 开应注入窗级「退出这只」"

    # _request_quit 走窗级分支（on_exit_window 被调用，而非 app.quit）
    exit_calls = []
    win.on_exit_window = lambda: exit_calls.append(1)
    win._active_context_menu = None
    quit_calls = []
    _app = QApplication.instance()
    monkeypatch.setattr(_app, "quit", lambda: quit_calls.append(1))
    PetWindow._request_quit(win)
    assert exit_calls == [1], "flag 开右键退出应走窗级「退出这只」"
    assert quit_calls == [], "flag 开右键退出不应走 app.quit"


# --------------------------------------------------------------------------
# P1-7 / T-3：close_root per-root 屏障
# --------------------------------------------------------------------------
def _make_session_store_pairs(tmp_path):
    store_a = SessionStore(tmp_path, "slot-1")
    store_b = SessionStore(tmp_path, "slot-2")
    return store_a, store_b


def _create_sessions(store):
    from pet.chat.models import ChatSession
    session = store.create("char", "provider", "prompt")
    return session


def test_close_root_closes_only_target_writer(tmp_path, app):
    """T-3：close_root 只关闭目标 root 的 writer，其它窗 writer 保持可用。"""
    store_a, store_b = _make_session_store_pairs(tmp_path)
    sa = _create_sessions(store_a)
    sb = _create_sessions(store_b)
    assert store_a.save(sa) is True
    assert store_b.save(sb) is True
    root_a = store_a.root
    root_b = store_b.root
    assert session_store_mod._registry.get_writer(root_a) is not None
    assert session_store_mod._registry.get_writer(root_b) is not None

    assert session_store_mod.close_writer_for_root(root_a) is True
    assert session_store_mod._registry.get_writer(root_a) is None
    # B 窗 writer 不受影响、仍可写
    assert session_store_mod._registry.get_writer(root_b) is not None
    assert store_b.save(sb) is True


def test_close_root_per_root_barrier_does_not_reject_other_window(tmp_path, app, monkeypatch):
    """P1-7：关 A 窗 writer 期间 B 窗 save 不被拒（per-root 屏障取代全局 _closing）。"""
    store_a, store_b = _make_session_store_pairs(tmp_path)
    sa = _create_sessions(store_a)
    sb = _create_sessions(store_b)
    assert store_a.save(sa) is True
    assert store_b.save(sb) is True
    root_a = store_a.root

    writer_a = session_store_mod._registry.get_writer(root_a)
    writer_b = session_store_mod._registry.get_writer(store_b.root)
    assert writer_a is not None and writer_b is not None

    entered = threading.Event()
    release = threading.Event()
    orig_close = writer_a.close

    def blocking_close(timeout=10.0):
        entered.set()
        release.wait(5.0)
        return orig_close(timeout=timeout)

    monkeypatch.setattr(writer_a, "close", blocking_close)

    result = {}

    def do_close():
        result["ok"] = session_store_mod.close_writer_for_root(root_a)

    t = threading.Thread(target=do_close)
    t.start()
    assert entered.wait(5.0), "close_root(A) 应已进入且阻塞"
    try:
        # A 窗 writer 关闭窗口期内，B 窗 save 不被拒（per-root 屏障）
        assert store_b.save(sb) is True, "关 A 窗 writer 期间 B 窗 save 不应被拒"
    finally:
        release.set()
        t.join(5.0)
    assert result["ok"] is True


# --------------------------------------------------------------------------
# T-1：两窗各持 session → runtime_id 不同、互不为 peer-self（P1-1）
# --------------------------------------------------------------------------
def _pump(seconds: float) -> None:
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)


def test_two_windows_distinct_runtime_ids_not_peer_self(tmp_path, app):
    """T-1：主窗与第二窗各 attach 各自 session → runtime_id 不同，且同进程
    多 session 经 _local_election_names 收敛成一个协调者 + 两个独立成员
    （互不为 peer-self，与多进程双开等价，P1-1）。"""
    from pet import collision

    name = f"sp52-{uuid.uuid4().hex[:8]}"
    primary = CollisionIpcSession(Config(tmp_path, instance_id=""), server_name=name)
    second = CollisionIpcSession(Config(tmp_path, instance_id="slot-1"), server_name=name)
    assert primary.runtime_id != second.runtime_id, "两窗 runtime_id 必须不同"
    # runtime_id 由各自 instance_id 派生（前缀区分主窗/第二窗）
    assert primary.runtime_id.startswith("instance-pid")
    assert second.runtime_id.startswith("slot-1-pid")

    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED

    def _state(seq, x):
        return {"seq": seq, "ts": time.monotonic(), "x": x, "y": 0.0,
                "w": 100, "h": 100, "radius_x": 40.0, "radius_y": 40.0,
                "vx": 0.0, "vy": 0.0, "flags": flags}

    primary.start()
    second.start()
    try:
        # 等都收敛出一个协调者（server 非空的那侧）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _pump(0.1)
            if primary._worker.server is not None or second._worker.server is not None:
                break
        coord_session = primary if primary._worker.server is not None else second
        client_session = second if coord_session is primary else primary
        assert coord_session._worker.server is not None
        _pump(0.5)  # 客户端连接/握手

        # 两窗都上报 state → 各自成为协调者成员表里的独立成员（互不为 peer-self）
        coord_session.submit_state(_state(1, x=10.0))
        client_session.submit_state(_state(1, x=20.0))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            _pump(0.05)
            if (primary.runtime_id in coord_session._worker.members
                    and second.runtime_id in coord_session._worker.members):
                break
        assert primary.runtime_id in coord_session._worker.members
        assert second.runtime_id in coord_session._worker.members
        assert len(coord_session._worker.members) >= 2, \
            "两窗必须是两个独立成员，而不是共享一个成员槽位"
    finally:
        primary.stop()
        second.stop()


def test_spawn_in_process_slot_scan_cap_raises(tmp_path, app, monkeypatch):
    """P2-1：slot 扫描超过 128 上限抛 SlotManagerError（不许无限循环）。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    monkeypatch.setattr(slot_manager_mod, "acquire_pet_slot",
                        lambda *a, **k: (_ for _ in ()).throw(
                            slot_manager_mod.SlotLockError("busy")))
    with pytest.raises(slot_manager_mod.SlotManagerError):
        shell.spawn_in_process_window(1)
    slot_manager_mod._unlock_file(primary_handle)


def test_enable_chat_setter_does_not_write_shell(tmp_path, app):
    """P2-4：PetInstance.enable_chat setter 不得改写进程级 shell（只读转发）。"""
    shell = AppShell(QApplication.instance(), Config(tmp_path), enable_chat=True)
    inst = shell.instance
    # setter 只缓存无 shell 兜底，不改写进程级权威源
    inst.enable_chat = False
    assert shell.enable_chat is True, "PetInstance setter 不得改写进程级 shell"
    assert inst.enable_chat is True, "getter 仍读 shell 权威值（只读转发）"

    # 无 shell（__new__ 测试桩）：setter 缓存值作兜底
    bare = PetInstance.__new__(PetInstance)
    bare._enable_chat = True
    bare.enable_chat = False
    assert bare.enable_chat is False


def test_in_process_spawn_shares_process_hub(tmp_path, app, monkeypatch):
    """批5.3：P1-6 移除——进程内多窗与``decode_broker_enabled``的互斥声明作废。
    新窗与主窗共用同一进程级``DecodeFanoutHub``（experimental_shared_decode 默认
    开 且 experimental_single_process_spawn 开 → hub 启用），不再有「停用新窗
    broker（不 bind）」的限制。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.set("experimental_shared_decode", True)
    config.save()
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config.dir, preferred_slot=0)
    shell = AppShell(QApplication.instance(), config, enable_chat=True,
                     slot_handle=slot_handle, slot_id=slot_id)
    # 双门开 → 进程级 hub 启用
    assert shell._decode_hub.enabled is True
    assert shell.instance.broker_facade.enabled is True

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        win._single_process_spawn = self.shell._single_process_spawn
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    monkeypatch.setattr(app_mod.PetInstance, "_apply_spawn_offset", lambda self: None)

    second = shell.spawn_in_process_window(1)
    assert second is not shell.instance
    # 新窗与主窗共用同一进程级 hub（不是停用/独立 broker）
    assert second.broker_facade is shell.instance.broker_facade
    assert second.broker_facade.enabled is True

    _stop_sessions(second)
    second.win.close()
    slot_manager_mod._unlock_file(second.slot_handle)
    second.slot_handle = None
    slot_manager_mod._unlock_file(slot_handle)


def test_in_process_spawn_hub_disabled_when_shared_decode_off(tmp_path, app, monkeypatch):
    """experimental_shared_decode 关 → 进程级 hub 不激活（各窗独立解码）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.set("experimental_shared_decode", False)
    config.save()
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config.dir, preferred_slot=0)
    shell = AppShell(QApplication.instance(), config, enable_chat=True,
                     slot_handle=slot_handle, slot_id=slot_id)
    assert shell._decode_hub.enabled is False
    assert shell.instance.broker_facade.enabled is False
    assert shell.instance.broker_facade.shareable_start("idle", _FanoutMovie("x.webm")) == "local"
    slot_manager_mod._unlock_file(slot_handle)


# ---------------------------------------------------------------- N-1 修复回归
# GLM 复审（REVIEW_batch52_fix1_glm53）阻塞项 N-1：flag 快照必须在 PetWindow
# 构造期就生效——__init__ 尾部的 _restore_position 会写/读 runtime 标记，
# 构造返回后再注入会让 flag 开的窗用旧名写初始标记（两窗互踩）。


class _FakeClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(100, 100)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        self._running = True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class _FakeAnimLib:
    def __init__(self):
        names = [catalog.IDLE, catalog.TURN, catalog.MOVES[0],
                 catalog.CLICKS[0], catalog.DRAG]
        self._clips = {n: _FakeClip() for n in names}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


def test_real_window_construction_writes_versioned_marker_when_flag_on(tmp_path, app):
    """N-1（红→绿回归）：flag 开时真实 PetWindow 构造期就写 v2 名标记，
    绝不写旧名（修复前：构造期快照未注入 → 写旧名 runtime-<pid>.json）。"""
    from pet.window import PetWindow

    config = Config(tmp_path)
    config.save()  # 确保 config.dir 已建（生产路径由启动流程建，测试需显式）
    win = PetWindow(_FakeAnimLib(), config, single_process_spawn=True)
    try:
        v2 = slot_manager_mod.runtime_marker_path(
            config.dir, config.instance_id, versioned=True)
        assert v2.exists(), "flag 开的窗构造后必须已写 v2 标记（N-1）"
        legacy = [p for p in config.dir.glob("runtime-*.json")]
        assert legacy == [], f"flag 开的窗不得写旧名标记，实际: {legacy}"
    finally:
        win.close()
        win.deleteLater()


def test_real_window_construction_writes_legacy_marker_when_flag_off(tmp_path, app):
    """N-1 对照：flag 关（默认）构造后写旧名标记（与 HEAD 逐位一致）。"""
    from pet.window import PetWindow

    config = Config(tmp_path)
    config.save()  # 同上：先建 config.dir
    win = PetWindow(_FakeAnimLib(), config)
    try:
        legacy = config.dir / f"runtime-{os.getpid()}.json"
        assert legacy.exists(), "flag 关的窗构造后必须写旧名标记"
        v2 = list(config.dir.glob("pet-runtime-v2-*.json"))
        assert v2 == [], f"flag 关的窗不得写 v2 标记，实际: {v2}"
    finally:
        win.close()
        win.deleteLater()


def test_switch_character_new_window_gets_new_session(tmp_path, app, monkeypatch):
    """T-6 补全（attach 半边）：热切换先重建本窗会话再建窗——_build_window
    执行时 self.collision_ipc 必须已是新会话（PetWindow 构造期 attach 它）。"""
    config = Config(tmp_path)
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    monkeypatch.setattr(
        app_mod.PetInstance, "_create_library", lambda self, cid: _FakeLib())

    seen_sessions = []

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        seen_sessions.append(self.collision_ipc)
        win = _FakeWindow()
        win.cfg = self.config
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    win0 = _FakeWindow()
    win0.cfg = config
    shell.instance.win = win0
    old_ipc = shell.instance.collision_ipc

    char_ids = catalog.list_available_characters()
    current = str(config.get("character", catalog.DEFAULT_CHARACTER))
    target = next((c for c in char_ids if c != current), "not-default-character")
    shell.instance.switch_character(target)

    assert seen_sessions, "switch_character 应重建窗口"
    assert seen_sessions[-1] is not old_ipc, "建窗时必须已是新会话"
    assert seen_sessions[-1] is shell.instance.collision_ipc

    _stop_sessions(shell.instance)


def test_non_primary_switch_builds_no_tray(tmp_path, app, monkeypatch):
    """T-6 补全（托盘半边）：非主窗热切换 build_tray=False，绝不动共享托盘。"""
    config = Config(tmp_path)
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    monkeypatch.setattr(
        app_mod.PetInstance, "_create_library", lambda self, cid: _FakeLib())

    build_tray_calls = []

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        build_tray_calls.append(build_tray)
        win = _FakeWindow()
        win.cfg = self.config
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)

    primary_win = _FakeWindow()
    primary_win.cfg = config
    shell.instance.win = primary_win
    sec = PetInstance(shell, Config(tmp_path, instance_id="slot-1"),
                      enable_chat=True)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)

    char_ids = catalog.list_available_characters()
    current = str(sec.config.get("character", catalog.DEFAULT_CHARACTER))
    target = next((c for c in char_ids if c != current), "not-default-character")
    sec.switch_character(target)

    assert build_tray_calls == [False], \
        f"非主窗热切换不得触碰托盘，实际 build_tray 序列: {build_tray_calls}"

    _stop_sessions(shell.instance, sec)


# ---------------------------------------------------------------- 新 slot 落种
def test_seed_slot_config_follows_main_settings(tmp_path):
    """新 slot 首次多开：配置跟随主设置（剔除每窗状态键）。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    main_cfg = {"character": "shenshen", "click_sound_enabled": False,
                "rx": 0.5, "ry": 0.9, "screen_name": "X", "facing": "left"}
    (config_dir / "config.json").write_text(
        json.dumps(main_cfg), encoding="utf-8")

    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 2) is True
    seeded = json.loads(
        (config_dir / "config-slot-2.json").read_text(encoding="utf-8"))
    assert seeded["click_sound_enabled"] is False  # 跟随主设置
    assert seeded["character"] == "shenshen"
    for k in ("rx", "ry", "screen_name", "facing"):
        assert k not in seeded, f"每窗状态键 {k} 不得继承"


def test_seed_slot_config_preserves_existing(tmp_path):
    """已有存档（用户改过的）的 slot 不被落种覆盖。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"character": "shenshen"}), encoding="utf-8")
    existing = {"character": "other", "custom": 1}
    (config_dir / "config-slot-3.json").write_text(
        json.dumps(existing), encoding="utf-8")
    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 3) is False
    assert json.loads((config_dir / "config-slot-3.json").read_text(
        encoding="utf-8")) == existing
