# -*- coding: utf-8 -*-
"""进程内多窗（4.4b 起为唯一多宠形态）的机器可验测试。

覆盖批5.2 修复轮（DISPATCH_batch52_fix1）处置清单与验收（4.4b 已删去
feature flag / 碰撞 IPC 相关项）：
- P0-1：spawn 偏移链（env DSH_PET_SPAWN_OFFSET_INDEX → 主窗 spawn_offset）接线；
- P0-2：右键退出恒注入窗级「退出这只」；
- P1-2/P2-6：runtime 标记恒用 versioned 名；
- P1-3：「退出这只」退主窗后实例提升，托盘/灵动岛动作仍指向存活实例；
- P1-5：「退出这只」关闭该窗从属聊天窗/设置窗（防 writer 复活）；
- P1-7：close_root 改为 per-root 屏障（关 A 窗 writer 期间 B 窗 save 不被拒）；
- T-3：close_root 直接单测；
- switch_character：热切换只做本窗解码收尾，不重建进程级共享 hub。
"""
from __future__ import annotations

import json
import os
import threading
import time

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
from pet.config import APP_DIR_NAME, Config
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

    4.4b：进程内多窗常开化，runtime 标记恒用 versioned 名（无 flag 快照）。
    """

    def __init__(self):
        self.calls = []
        self.lib = _FakeLib()
        self.agent_link_manager = _FakeAgentLink()
        self.is_shown = True

    def save_position(self):
        self.calls.append("save")

    def close(self):
        self.calls.append("close")

    def remove_runtime_marker(self):
        self.calls.append("marker_del")
        slot_manager_mod.delete_runtime_marker(
            self.cfg.dir, self.cfg.instance_id)

    def detach_decode_sessions(self):
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
    """建主窗 AppShell（无 slot 文件锁，第三返回值恒 None）。"""
    config = Config(tmp_path)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True, slot_id=0)
    return shell, config, None


def _stop_sessions(*_insts):
    """4.4a：碰撞 IPC 会话随多进程多宠退役层停用——保留调用点占位（无操作）。"""


def test_spawn_in_process_creates_isolated_second_window(tmp_path, app, monkeypatch):
    """flag 开时进程内 spawn 生成第二个实例：窗身份/Config/SessionStore 隔离。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    built = []

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
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
    # P1-2：第二窗标记版本化读进程级快照（flag 开 = versioned）

    # 释放：停本测试启动的 second 会话
    _stop_sessions(second)
    second.win.close()




def test_spawn_refreshes_island_wall_hooks(tmp_path, app, monkeypatch):
    """生小肥鱼后必须刷新灵动岛硬墙钩子——否则新鱼直接穿过岛（回归）。

    硬墙 hook 只在碰撞体 start() 挂过一轮（那一刻已存在的窗口），本进程新窗
    不在列表里；spawn 路径必须补挂（island_collision.refresh_hooks）。
    """
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    monkeypatch.setattr(app_mod.PetInstance, "_apply_spawn_offset", lambda self: None)
    monkeypatch.setattr(app_mod.PetInstance, "_check_autostart_wanted", lambda self: None)

    class _RecordingBody:
        def __init__(self):
            self.calls = 0

        def refresh_hooks(self):
            self.calls += 1

    body = _RecordingBody()
    shell.island_collision = body
    second = shell.spawn_in_process_window(1)
    assert body.calls == 1, "spawn 后未刷新硬墙钩子——新鱼会穿过灵动岛"

    _stop_sessions(second)
    second.win.close()


def test_spawn_pet_always_creates_in_process_window(tmp_path, app, monkeypatch):
    """4.4a：`spawn_pet` 不再拉起独立桌宠进程——统一走进程内新窗。"""
    config = Config(tmp_path)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)

    launched = []
    monkeypatch.setattr(shell, "spawn_in_process_window",
                        lambda index=1: launched.append(index))
    shell.spawn_pet()
    shell.spawn_pet()
    assert launched == [1, 2]
    assert len(shell.instances) == 1


def test_exit_window_cleans_window_resources_only(tmp_path, app, monkeypatch):
    """「退出这只」：只收口本窗（位置/prewarm/agent/本窗 writer/slot/标记/
    本窗碰撞会话与 broker），不碰其它窗的进程级资源。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    # 预备第二个实例（主窗仍留在集合里：本测试验证"非最后一窗"的窗级退出）
    instance_id = "slot-1"
    sec_config = Config(base=tmp_path, instance_id=instance_id)
    sec = PetInstance(shell, sec_config, enable_chat=True, slot_id=1)
    win = _FakeWindow()
    win.cfg = sec_config
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

    # 进程级资源打点：共享解码 hub（进程级）绝不被单窗退出拆除（各窗共用；
    # 真收口只在全部退出的 stop_all）。
    permanent_calls = []
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
    assert closed_roots == [str(sec_root)], "只关本窗 sessions-slot-N 的 writer"
    assert permanent_calls == []
    # 共享解码 hub 不被单窗退出拆除（进程级：各窗共用，真收口仅在全部退出）
    assert hub._sources, "单窗退出不得清空共享解码 hub 的源表"
    # 主窗仍在（非最后一窗不触全进程退出）
    assert len(shell.instances) == 1



def _alive_pid() -> int:
    # 用当前测试进程的 pid（一定存活），用于构造"活着"的标记
    return os.getpid()


def test_switch_character_keeps_process_shared_decode_hub(tmp_path, app, monkeypatch):
    """4.4a：热切换只做本窗解码收尾，不重建进程级共享 hub、不碰其它窗。"""
    config = Config(tmp_path)
    shell = AppShell(QApplication.instance(), config, enable_chat=True)

    monkeypatch.setattr(
        app_mod.PetInstance, "_create_library", lambda self, cid: _FakeLib())

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)

    # 主窗先有一个窗口
    win0 = _FakeWindow()
    win0.cfg = config
    shell.instance.win = win0

    # 第二个窗（独立资源）：其 broker 不应被 touch
    sec = PetInstance(shell, Config(tmp_path, instance_id="slot-1"), enable_chat=True)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)

    old_broker = shell.instance.broker_facade
    broker_shutdown = []
    monkeypatch.setattr(old_broker, "shutdown", lambda: broker_shutdown.append(1))
    sec_broker_id = id(sec.broker_facade)

    char_ids = catalog.list_available_characters()
    current = str(config.get("character", catalog.DEFAULT_CHARACTER))
    target = next((c for c in char_ids if c != current), "not-default-character")

    shell.instance.switch_character(target)

    # 进程级共享 hub 不被重建（各窗共用，批5.3）。
    assert broker_shutdown == [], \
        "switch_character 不应关进程级共享解码 hub（批5.3 各窗共用）"
    assert shell.instance.broker_facade is old_broker, \
        "进程级解码 hub 不被重建（各窗共用同一份）"
    # 其它窗的解码资源未被动
    assert id(sec.broker_facade) == sec_broker_id
    assert shell.instance.win is not win0

    # 收口：停新会话（避免遗留运行中的独占 QLocal server）
    _stop_sessions(shell.instance, sec)


def test_runtime_marker_versioned_name_avoids_legacy_glob(tmp_path, app):
    """R4：窗口恒用版本化新名（不被旧 'runtime-*.json' glob 匹配），
    且读取侧同时认新旧两种命名（回滚兼容）。"""
    config = Config(tmp_path)
    config.save()

    ver_path = slot_manager_mod.runtime_marker_path(
        config.dir, config.instance_id, versioned=True)
    # 新名不匹配旧 glob（旧 glob 只认 'runtime-*.json' 前缀）
    assert ver_path.name.startswith("pet-runtime-v2-")
    assert len(list(config.dir.glob("runtime-*.json"))) == 0, \
        "新窗不得写入旧格式标记"

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


def test_spawn_offset_env_wired_to_primary_instance(tmp_path, app, monkeypatch):
    """P0-1：spawn 偏移链接线（env → 主窗 spawn_offset）——兼容解析保留（T5）。"""
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
    assert shell2.instance._spawn_offset == 0, "兼容偏移不能移动固定slot0"


def test_exit_primary_promotes_primary_window(tmp_path, app, monkeypatch):
    """P1-3：「退出这只」退主窗后实例提升——托盘/灵动岛/Dock 动作指向存活实例。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    primary = shell.instance
    primary.win = _FakeWindow()
    primary.win.cfg = config

    # 第二个实例即将成为新主窗
    sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                      enable_chat=True, slot_id=1)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)

    monkeypatch.setattr(session_store_mod, "close_writer_for_root",
                        lambda root, timeout=10.0: True)
    monkeypatch.setattr(shell.instance.broker_facade, "shutdown", lambda: None)
    monkeypatch.setattr(sec.broker_facade, "shutdown", lambda: None)

    assert shell.instance is primary
    shell._on_window_exit_requested(shell.instance)

    # 主窗退出后：新列表头（sec）成为主窗，self.instance 更新为存活实例
    assert len(shell.instances) == 2
    assert shell.instance is primary
    assert shell.instance.win is None
    assert sec.win is sec_win
    assert sec.slot_id == 1



def test_exit_window_closes_subwindows(tmp_path, app, monkeypatch):
    """P1-5：「退出这只」关闭该窗从属聊天窗/设置窗并断开引用（防 writer 复活）。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    shell.instance.win = _FakeWindow()
    shell.instance.win.cfg = config

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
    sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                      enable_chat=True, slot_id=1)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)

    monkeypatch.setattr(session_store_mod, "close_writer_for_root",
                        lambda root, timeout=10.0: True)
    monkeypatch.setattr(shell.instance.broker_facade, "shutdown", lambda: None)
    monkeypatch.setattr(sec.broker_facade, "shutdown", lambda: None)

    shell._on_window_exit_requested(shell.instance)

    # slot0 退出不关共享会话与从属聊天窗（他们由该 instance 继续持有、可恢复）；
    # 唯一被关的是**进程内设置窗**：收口前的设置页闸门（ops P3-c）必须让它先过一遍
    # 保存/放弃/继续编辑，否则草稿会随父窗一起无声消失
    # （见 tests/test_settings_stale_form_exit.py 第 9 节）。
    assert closed == [sub_mod_settings], "只有设置窗该被闸门关掉"
    assert saves == []
    assert shell.instance.legacy_chat_window is sub_legacy
    assert shell.instance.modern_chat_window is sub_modern
    assert shell.instance.quick_chat is sub_quick
    assert shell.instance.win is None
    assert shell.instance is not sec


def test_exit_window_callback_injected_on_every_window(tmp_path, app, monkeypatch):
    """P0-2（4.4b 语义）：窗级「退出这只」恒注入；_request_quit 走窗级分支。

    多窗常开化后不再有 flag 关的"不注入走 app.quit"分支——最后一窗退出即
    全进程退出，两者对外行为等价。
    """
    config = Config(tmp_path)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    inst = shell.instance
    win = _FakeWindow()
    win.cfg = config
    inst.win = win
    inst._wire_window(win)
    assert callable(win.on_exit_window), "每窗都应注入窗级「退出这只」"

    # _request_quit 走窗级分支（on_exit_window 被调用，而非 app.quit）
    exit_calls = []
    win.on_exit_window = lambda: exit_calls.append(1)
    win._active_context_menu = None
    quit_calls = []
    _app = QApplication.instance()
    monkeypatch.setattr(_app, "quit", lambda: quit_calls.append(1))
    PetWindow._request_quit(win)
    assert exit_calls == [1], "右键退出应走窗级「退出这只」"
    assert quit_calls == [], "右键退出不应走 app.quit"


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


def test_spawn_in_process_slot_scan_cap_raises(tmp_path, app, monkeypatch):
    """D6：128 个身份**全在活跃集合**时抛 SpawnStateError（不许无限循环）。

    B01 起「目录里全是历史配置」不再等于耗尽（改为复用最小退役身份）；真耗尽
    只剩「128 个身份同时活跃」这一条，本用例以活跃窗集合模拟该极限。
    """
    from pet import overlay_spawn_state

    shell, _config, _handle = _make_primary_with_slot(tmp_path)
    monkeypatch.setattr(
        overlay_spawn_state, "scan_slot_configs",
        lambda *_a, **_k: set(
            range(1, overlay_spawn_state.MAX_SCAN_SLOTS + 1)))
    shell._instances = [type("Inst", (), {"slot_id": slot, "win": None})()
                        for slot in range(0, overlay_spawn_state.MAX_SCAN_SLOTS + 1)]
    with pytest.raises(overlay_spawn_state.SpawnStateError):
        shell.spawn_in_process_window(1)


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
    新窗与主窗共用同一进程级``DecodeFanoutHub``（experimental_shared_decode
    默认开 → hub 启用），不再有「停用新窗 broker（不 bind）」的限制。"""
    config = Config(tmp_path)
    config.set("experimental_shared_decode", True)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True, slot_id=0)
    # 双门开 → 进程级 hub 启用
    assert shell._decode_hub.enabled is True
    assert shell.instance.broker_facade.enabled is True

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
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


def test_in_process_spawn_hub_disabled_when_shared_decode_off(tmp_path, app, monkeypatch):
    """experimental_shared_decode 关 → 进程级 hub 不激活（各窗独立解码）。"""
    config = Config(tmp_path)
    config.set("experimental_shared_decode", False)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True, slot_id=0)
    assert shell._decode_hub.enabled is False
    assert shell.instance.broker_facade.enabled is False
    assert shell.instance.broker_facade.shareable_start("idle", _FanoutMovie("x.webm")) == "local"


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

    def movie(self, name, **_kw):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


def test_real_window_construction_writes_versioned_marker(tmp_path, app):
    """N-1（红→绿回归）：真实 PetWindow 构造期就写 v2 名标记，绝不写旧名
    （4.4b：多窗常开化 → 恒版本化，旧名无法区分同进程多窗）。"""
    from pet.window import PetWindow

    config = Config(tmp_path)
    config.save()  # 确保 config.dir 已建（生产路径由启动流程建，测试需显式）
    win = PetWindow(_FakeAnimLib(), config)
    try:
        v2 = slot_manager_mod.runtime_marker_path(
            config.dir, config.instance_id, versioned=True)
        assert v2.exists(), "窗构造后必须已写 v2 标记（N-1）"
        legacy = [p for p in config.dir.glob("runtime-*.json")]
        assert legacy == [], f"窗不得写旧名标记，实际: {legacy}"
    finally:
        win.close()
        win.deleteLater()


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
    """新 slot 首次多开：配置跟随主设置（剔除每窗状态键，落种含 user_customized=False）。"""
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
    assert seeded.get("user_customized") is False  # 落种默认不置位
    for k in ("rx", "ry", "screen_name", "facing"):
        assert k not in seeded, f"每窗状态键 {k} 不得继承"


def test_seed_slot_config_applies_spawn_inherit_logic(tmp_path):
    """批 C：落种遵循 spawn_inherit_size / spawn_scale / spawn_inherit_dynamic_island。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "scale": 1.0,
        "spawn_inherit_size": False,
        "spawn_scale": 0.5,
        "spawn_inherit_dynamic_island": True,
        "dynamic_island": {"enabled": True},
    }), encoding="utf-8")

    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 7) is True
    seeded = json.loads(
        (config_dir / "config-slot-7.json").read_text(encoding="utf-8"))
    # 关闭继承大小 → 用主配置为小肥鱼选定的 spawn_scale；继承灵动岛 → enabled=True
    assert seeded["scale"] == 0.5
    assert seeded["spawn_inherit_size"] is False
    assert seeded["spawn_scale"] == 0.5
    assert seeded["spawn_inherit_dynamic_island"] is True
    assert seeded["dynamic_island"]["enabled"] is True
    assert seeded.get("user_customized") is False


def test_seed_slot_config_refreshes_non_customized_slot(tmp_path):
    """批 C：slot 存在但 user_customized 为假（含旧存档无此键）→ 按当前主设置重新刷新。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"character": "shenshen", "spawn_inherit_size": True}),
        encoding="utf-8")
    # 旧存档（无 user_customized 键）按假处理 → 应被刷新成主设置。
    existing = {"character": "other", "custom": 1}
    (config_dir / "config-slot-3.json").write_text(json.dumps(existing), encoding="utf-8")

    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 3) is True
    refreshed = json.loads(
        (config_dir / "config-slot-3.json").read_text(encoding="utf-8"))
    assert refreshed["character"] == "shenshen"  # 跟随主设置
    assert refreshed.get("user_customized") is False


def test_seed_slot_config_preserves_customized_slot(tmp_path):
    """批 C：slot 存在且 user_customized 为真 → 整个跳过，一个键都不碰。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"character": "shenshen"}), encoding="utf-8")
    existing = {"character": "other", "custom": 1, "user_customized": True}
    (config_dir / "config-slot-4.json").write_text(json.dumps(existing), encoding="utf-8")

    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 4) is False
    assert json.loads((config_dir / "config-slot-4.json").read_text(
        encoding="utf-8")) == existing


def test_seed_slot_config_refresh_preserves_slot_position(tmp_path):
    """批 C：落种/刷新永不写位置键——刷新不覆盖 slot 自己拖动后自存的位置。"""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    # 主配置的位置键是另一个值；刷新后 slot 应保留自己的位置，不被主配置覆盖。
    (config_dir / "config.json").write_text(json.dumps({
        "character": "shenshen",
        "rx": 0.1, "ry": 0.2, "screen_name": "MAIN", "facing": "left",
    }), encoding="utf-8")
    existing = {"character": "other",
                "rx": 0.6, "ry": 0.7, "screen_name": "SLOT", "facing": "right"}
    (config_dir / "config-slot-5.json").write_text(json.dumps(existing), encoding="utf-8")

    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 5) is True
    refreshed = json.loads(
        (config_dir / "config-slot-5.json").read_text(encoding="utf-8"))
    assert refreshed["rx"] == 0.6
    assert refreshed["ry"] == 0.7
    assert refreshed["screen_name"] == "SLOT"
    assert refreshed["facing"] == "right"


def test_multi_process_start_seed_then_config_roundtrip(tmp_path):
    """批 C：多进程启动路径（main 先 seed 再构造 Config）落种含 spawn 逻辑，Config 可读回。"""
    config_dir = tmp_path / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({
        "scale": 1.0,
        "spawn_inherit_size": False,
        "spawn_scale": 0.5,
        "spawn_inherit_dynamic_island": True,
        "dynamic_island": {"enabled": True},
    }), encoding="utf-8")

    # 与 main() 的 seed → Config(instance_id) 次序一致。
    assert slot_manager_mod.seed_slot_config_from_main(config_dir, 2) is True
    cfg = Config(base=tmp_path, instance_id="slot-2")
    assert cfg.get("scale") == 0.5
    assert cfg.get("spawn_inherit_size") is False
    assert cfg.get("spawn_scale") == 0.5
    assert cfg.get("dynamic_island", {}).get("enabled") is True
    assert cfg.get("user_customized") is False


def test_spawn_in_process_window_routes_through_shared_seed(tmp_path, app, monkeypatch):
    """批 C：进程内 spawn 调用点统一走共享落种函数（传对 slot_id），并产出 spawn 逻辑。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    real_seed = slot_manager_mod.seed_slot_config_from_main
    calls = []

    def spy(seed_dir, seed_slot):
        calls.append((str(seed_dir), seed_slot))
        return real_seed(seed_dir, seed_slot)

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _FakeWindow()
        win.cfg = self.config
        self.win = win
        return win

    monkeypatch.setattr(slot_manager_mod, "seed_slot_config_from_main", spy)
    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    monkeypatch.setattr(app_mod.PetInstance, "_apply_spawn_offset", lambda self: None)
    monkeypatch.setattr(
        app_mod.PetInstance, "_check_autostart_wanted", lambda self: None)

    second = shell.spawn_in_process_window(1)

    assert calls == [], "退役整表落种入口不再使用"
    # spawn 产物 slot 配置存在且落种 mark 默认假
    seed_path = config.dir.parent / APP_DIR_NAME / "config-slot-1.json"
    assert seed_path.exists()
    seeded = json.loads(seed_path.read_text(encoding="utf-8"))
    assert "pet_opacity" not in seeded
    assert "character" in seeded

    _stop_sessions(second)
    second.win.close()


# --------------------------------------------------------------------------
# 批 B：clear_spawned_pets 单进程模式（进程内子窗）前置关闭
# --------------------------------------------------------------------------
def test_clear_spawned_pets_closes_in_process_children_no_residue(
        tmp_path, app, monkeypatch):
    """批 B：单进程模式下 clear_spawned_pets 先关闭进程内「非主窗」子肥鱼窗口、
    删除其 runtime 标记，主窗保留；重复调用幂等无残留。

    子肥鱼在单进程模式是进程内窗口（PID=主进程），会被文件级清理的
    pid==os.getpid() 自我保护跳过而永远不会被关；修复后先按进程内子窗登记表
    （self._instances）枚举并关闭，再走文件级清理杀多进程子进程。
    """
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)

    # 主窗窗身 + 主窗 runtime 标记（主肥鱼不清，须保留）
    primary = shell.instance
    primary_win = _FakeWindow()
    primary_win.cfg = config
    primary.win = primary_win
    primary_marker = slot_manager_mod.runtime_marker_path(
        config.dir, config.instance_id, versioned=True)
    primary_marker.write_text(
        json.dumps({"pid": os.getpid(), "x": 0, "y": 0, "w": 100, "h": 100}),
        encoding="utf-8")

    # 第二个实例（进程内子窗，同 pid=主进程）
    sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                      enable_chat=True, slot_id=1)
    sec_win = _FakeWindow()
    sec_win.cfg = sec.config
    sec.win = sec_win
    shell._instances.append(sec)
    sec_marker = slot_manager_mod.runtime_marker_path(
        config.dir, sec.config.instance_id, versioned=True)
    sec_marker.write_text(
        json.dumps({"pid": os.getpid(), "x": 100, "y": 100, "w": 100, "h": 100}),
        encoding="utf-8")

    # 子窗 broker 打桩（避免真实共享 hub 收口的副作用）
    monkeypatch.setattr(sec.broker_facade, "shutdown", lambda: None)

    # 确认对话框返回 Yes
    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        lambda *a, **kw: app_mod.QMessageBox.StandardButton.Yes)

    assert len(shell.instances) == 2
    shell.clear_spawned_pets()
    # 批 E：进程内子窗关闭改为 QTimer.singleShot(0) 逐只链式执行，需转事件循环收口。
    _pump(0.5)

    # 子窗被关闭并移除；主窗保留
    assert len(shell.instances) == 1
    assert shell.instance is primary
    assert sec not in shell.instances
    assert sec_win.calls == ["save", "marker_del", "close"]
    # 子窗 v2 标记被删；主窗标记保留（主肥鱼仍在跑）
    assert not sec_marker.exists()
    assert primary_marker.exists()

    # 幂等：再跑一遍无残留、不报错
    shell.clear_spawned_pets()
    _pump(0.2)
    assert len(shell.instances) == 1
    assert shell.instance is primary
    assert primary_marker.exists()



def test_clear_spawned_pets_without_in_process_children_is_noop(
        tmp_path, app, monkeypatch):
    """4.4a：无进程内子窗时 clear_spawned_pets 是空操作——跨进程文件级清理
    （child_pet_cleanup：runtime 标记 + taskkill）随退役层停用。"""
    config = Config(tmp_path)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)

    primary_win = _FakeWindow()
    primary_win.cfg = config
    shell.instance.win = primary_win

    root = config.dir
    v2 = root / "pet-runtime-v2-555-slot-1.json"
    v2.write_text(json.dumps({"pid": 555}), encoding="utf-8")

    assert len(shell.instances) == 1
    shell.clear_spawned_pets()
    _pump(0.2)

    assert primary_win.calls == [], "主窗不得被触碰"
    assert v2.exists(), "退役后不再做跨进程文件级清理"
    assert len(shell.instances) == 1
    assert shell.instance is shell.instances[0]
    assert shell._clear_spawned_pending is False


# --------------------------------------------------------------------------
# 批 E：清除子肥鱼——进程内子窗 QTimer.singleShot(0) 链式关闭（不冻 UI）
# --------------------------------------------------------------------------
def _add_child_instance(shell, tmp_path, config, preferred_slot, monkeypatch):
    """建一个进程内子窗实例（fake window + broker 打桩），返回 (inst, win)。"""
    inst = PetInstance(shell, Config(base=tmp_path, instance_id=f"slot-{preferred_slot}"),
                       enable_chat=True, slot_id=preferred_slot)
    win = _FakeWindow()
    win.cfg = inst.config
    inst.win = win
    shell._instances.append(inst)
    monkeypatch.setattr(inst.broker_facade, "shutdown", lambda: None)
    return inst, win


def test_clear_spawned_pets_chained_close_defers_until_event_loop(
        tmp_path, app, monkeypatch):
    """批 E：子窗关闭经 QTimer.singleShot(0) 逐只执行——clear_spawned_pets
    同步返回时尚未触碰任何子窗（每只之间让出事件循环，UI 不冻结/不出黑框），
    事件循环转起来后才逐只关完，全部关完再执行文件级清理并弹一次结果框。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    children = [
        _add_child_instance(shell, tmp_path, config, slot, monkeypatch)
        for slot in (1, 2)
    ]

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        lambda *a, **kw: app_mod.QMessageBox.StandardButton.Yes)
    infos = []
    monkeypatch.setattr(
        app_mod.QMessageBox, "information",
        lambda *a, **kw: infos.append(a))

    shell.clear_spawned_pets()
    # 同步返回：关闭被 singleShot(0) 延后，一只都还没关（旧实现同步关 N 只）。
    assert all(win.calls == [] for _inst, win in children)
    assert infos == []

    _pump(0.5)
    for inst, win in children:
        assert win.calls == ["save", "marker_del", "close"]
        assert inst not in shell.instances
    assert infos == [], "批 I：结果弹窗已移除（结果写日志）"
    assert shell._clear_spawned_pending is False
    assert shell.instance is shell.instances[0]


def test_clear_spawned_pets_repeat_click_during_chain_is_idempotent(
        tmp_path, app, monkeypatch):
    """批 E：链式关闭进行中重复点击「一键退出」→ 忽略（不重复关闭）。
    批 I：确认框已移除（操作不删数据可重新生成），questions 应恒为空。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    children = [
        _add_child_instance(shell, tmp_path, config, slot, monkeypatch)
        for slot in (1, 2)
    ]

    questions = []
    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        lambda *a, **kw: (questions.append(1),
                          app_mod.QMessageBox.StandardButton.Yes)[1])
    infos = []
    monkeypatch.setattr(
        app_mod.QMessageBox, "information",
        lambda *a, **kw: infos.append(a))

    shell.clear_spawned_pets()
    shell.clear_spawned_pets()  # 链式进行中重复点击
    assert questions == [], "批 I：无确认框"

    _pump(0.5)
    assert infos == [], "批 I：结果弹窗已移除"
    assert shell._clear_spawned_pending is False
    for _inst, win in children:
        assert win.calls == ["save", "marker_del", "close"], "每只只关一次"


def test_clear_spawned_pets_chain_skips_removed_or_destroyed_child(
        tmp_path, app, monkeypatch):
    """批 E：链式过程中子窗已销毁/已关闭（弱引用失效或不在登记表）→ 跳过不报错，
    其余子窗照常关完，清理与结果框照常收口。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    gone, gone_win = _add_child_instance(shell, tmp_path, config, 1, monkeypatch)
    kept, kept_win = _add_child_instance(shell, tmp_path, config, 2, monkeypatch)

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        lambda *a, **kw: app_mod.QMessageBox.StandardButton.Yes)
    infos = []
    monkeypatch.setattr(
        app_mod.QMessageBox, "information",
        lambda *a, **kw: infos.append(a))

    shell.clear_spawned_pets()
    # 链式执行前该子窗已自行关闭（移出登记表）：快照仍在，但存活校验应跳过。
    shell._instances.remove(gone)

    _pump(0.5)
    assert gone_win.calls == [], "已销毁/已关闭的子窗不应再被触碰"
    assert kept_win.calls == ["save", "marker_del", "close"]
    assert infos == [], "批 I：结果弹窗已移除"
    assert shell._clear_spawned_pending is False, "收口后必须复位进行中标记"



def test_clear_spawned_pets_defers_heavy_teardown_to_reaper_thread(
        tmp_path, app, monkeypatch):
    """批 G：链式「退出子肥鱼」把每窗的重资源回收（writer 关闭 / agent
    shutdown——各有界阻塞秒级）挪到进程级 reaper 线程执行；UI 线程只保留
    关窗/摘标记等毫秒级步骤，主桌宠不再冻结。4.4a 起碰撞会话已退役。"""
    import threading

    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    inst, win = _add_child_instance(shell, tmp_path, config, 1, monkeypatch)

    heavy_threads = []
    monkeypatch.setattr(
        win.agent_link_manager, "shutdown",
        lambda: heavy_threads.append(
            ("agent", threading.current_thread().name)))
    monkeypatch.setattr(
        app_mod.AppShell, "_close_instance_session_writer",
        lambda self, _inst: heavy_threads.append(
            ("writer", threading.current_thread().name)))
    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        lambda *a, **kw: app_mod.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(app_mod.QMessageBox, "information", lambda *a, **kw: None)

    shell.clear_spawned_pets()
    _pump(0.5)

    # UI 必做步骤照常同步完成（关窗/摘标记/移除登记表）
    assert win.calls == ["save", "marker_del", "close"]
    assert inst not in shell.instances
    # 重回收全部发生，且都在 reaper 线程而非 UI 主线程
    labels = sorted(label for label, _t in heavy_threads)
    assert labels == ["agent", "writer"]
    assert all(t == "pet-teardown-reaper" for _label, t in heavy_threads)
    # defer 模式下关窗前摘下 agent 引用：closeEvent 不在 UI 线程重复 join
    assert win.agent_link_manager is None
    assert shell._clear_spawned_pending is False



def test_settings_dialog_clear_button_routes_through_shell_chain(
        tmp_path, app, monkeypatch):
    """批 E：设置界面「一键退出」经 win.on_clear_spawned_pets（真实接线 =
    shell.clear_spawned_pets）走到进程内子窗链式关闭——单进程模式子肥鱼清得掉。
    批 I：全程无确认框无结果框。"""
    from PySide6.QtWidgets import QMessageBox, QWidget

    from pet.modern_settings_dialog import ModernSettingsDialog

    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    child, child_win = _add_child_instance(shell, tmp_path, config, 1, monkeypatch)

    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: (questions.append(a),
                          QMessageBox.StandardButton.Yes)[1])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)

    # 对话框父窗 = 已接线 on_clear_spawned_pets 的 PetWindow（app.py:361 形态）。
    pet_win = QWidget()
    pet_win.on_clear_spawned_pets = shell.clear_spawned_pets
    dialog = ModernSettingsDialog(config, pet_win, include_ai=False)
    try:
        dialog.clear_spawned_pets_btn.click()
        assert questions == [], "批 I：无确认框"
        # 关闭被 singleShot(0) 延后：点击后同步阶段尚未触碰子窗。
        assert child_win.calls == []
        _pump(0.5)
        assert child_win.calls == ["save", "marker_del", "close"]
        assert child not in shell.instances
        assert shell.instance is shell.instances[0]
    finally:
        dialog.close()
        pet_win.close()
        app.processEvents()



def test_clear_spawned_entry_wired_only_on_primary(tmp_path, app, monkeypatch):
    """批 G：「退出子肥鱼」入口只挂给主肥鱼（instance_id 为空）；子肥鱼窗
    该回调为 None——否则子鱼进程里 pid==os.getpid() 只跳过自己，会把主鱼
    当子鱼 taskkill 掉（实机事故）。"""
    shell, config, primary_handle = _make_primary_with_slot(tmp_path)
    main_win = _FakeWindow()
    main_win.cfg = config
    shell.instance._wire_window(main_win)
    assert callable(main_win.on_clear_spawned_pets), "主肥鱼必须有入口"

    inst, _win = _add_child_instance(shell, tmp_path, config, 1, monkeypatch)
    child_win = _FakeWindow()
    child_win.cfg = inst.config
    inst._wire_window(child_win)
    assert child_win.on_clear_spawned_pets is None, "子肥鱼不得有入口"


def test_runtime_marker_written_on_first_show(tmp_path, app):
    """批 G：窗口首次显示即登记 runtime 标记——没被拖动过的新生小肥鱼也有
    标记，「退出子肥鱼」按标记枚举时不会漏掉它。"""
    from tests.pet_window_fakes import FakeLibrary

    from pet.window import PetWindow

    config = Config(tmp_path)
    config.set("collision_enabled", False)
    config.save()
    win = PetWindow(FakeLibrary(), config)
    win.show()
    QApplication.instance().processEvents()
    # 4.4b：恒写版本化名 pet-runtime-v2-<pid>-slot-<N>.json
    marker = slot_manager_mod.runtime_marker_path(
        config.dir, config.instance_id, versioned=True)
    assert marker.exists(), "首次显示必须登记 runtime 标记"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    win.close()


def test_open_quick_chat_routes_to_overlay_shell_when_win_none(tmp_path, app, monkeypatch):
    """overlay 拓扑（``win`` 恒 None、不建 PetWindow）：快速对话必须改走 overlay
    壳的 sprite 锚定路径——锚到隐藏/不存在的 PetWindow 等于点了没反应（岛卡片
    「聊天」与托盘「快速对话（气泡）」共用本缝，2026-10-08 实机发现的回归）。"""
    shell, config, _handle = _make_primary_with_slot(tmp_path)
    inst = PetInstance(shell, config, enable_chat=True, slot_id=None)
    inst.win = None  # overlay 拓扑恒为 None（不建 PetWindow）
    monkeypatch.setattr(shell, "_aggregate_pet_visible", lambda: True)

    calls = []

    class _FakeOverlay:
        def open_quick_chat(self, sprite=None):
            calls.append(sprite)
            return True

    shell._overlay_shell = _FakeOverlay()
    inst.open_quick_chat()
    deadline = time.time() + 2.0
    while not calls and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert calls == [None], "win=None 时必须路由到 overlay 壳（sprite=None = 主宠）"


def test_open_quick_chat_win_none_without_overlay_is_quiet_noop(tmp_path, app, monkeypatch):
    """反面：win=None 且没有 overlay 壳（异常中间态）——安静不炸，不抛异常。"""
    shell, config, _handle = _make_primary_with_slot(tmp_path)
    inst = PetInstance(shell, config, enable_chat=True, slot_id=None)
    inst.win = None
    monkeypatch.setattr(shell, "_aggregate_pet_visible", lambda: True)
    shell._overlay_shell = None
    inst.open_quick_chat()  # 不抛即过
    QApplication.processEvents()
