# -*- coding: utf-8 -*-
"""键鼠跟随模式（复用 BongoCat 运行时）的模式状态机测试。

覆盖：运行时解析顺序、可写副本与素材覆盖层同步、进入/退出模式的窗口暂停与
恢复、子进程退出/启动失败的回退、启动记忆读取。子进程用可注入的假进程对象
替代，不依赖真实 BongoCat；另有一个真实 QProcess 的边接口冒烟测试。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QProcess, Signal

from pet.key_mouse_mode import (
    BONGOCAT_EXE,
    MODE_CLASSIC,
    MODE_KEY_MOUSE,
    KeyMouseModeController,
    KeyMouseProcess,
    ensure_runtime_copy,
    normalize_mode,
    resolve_runtime_source,
    runtime_paths,
    sync_asset_overrides,
)


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class FakeWindow:
    def __init__(self, visible: bool = True) -> None:
        self._visible = visible
        self.hide_notify: list[bool] = []
        self.show_calls = 0

    def isVisible(self) -> bool:  # noqa: N802 (Qt 命名)
        return self._visible

    def hide(self, *, notify: bool = True) -> None:
        self._visible = False
        self.hide_notify.append(notify)

    def show(self) -> None:
        self._visible = True
        self.show_calls += 1


class FakeInstance:
    def __init__(self, win: FakeWindow | None) -> None:
        self.win = win


class FakeShell:
    def __init__(self, instances, single_process_spawn: bool = False) -> None:
        self.instances = list(instances)
        self.single_process_spawn = single_process_spawn


class FakeConfig:
    """最小配置替身：只实现 pet_mode 的读改写与落盘计数。"""

    def __init__(self, mode: str = MODE_CLASSIC) -> None:
        self.data = {"pet_mode": mode}
        self.save_calls = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.save_calls += 1


class FakeProcess(QObject):
    """假子进程：手动控制启动结果与退出时机，避免真实进程时序。"""

    finished = Signal()

    def __init__(self, command, cwd, parent=None) -> None:
        super().__init__(parent)
        self.command = list(command)
        self.cwd = cwd
        self.started = False
        self.fail_start = False
        self.stop_calls = 0
        self._running = False

    def start(self) -> bool:
        if self.fail_start:
            return False
        self.started = True
        self._running = True
        return True

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False

    def is_running(self) -> bool:
        return self._running


def _make_controller(shell, config, tmp_path, *, command=None, source=None, factory=FakeProcess):
    # 测试必须与环境无关：不显式给 source 时也不能让本机/CI 上真实存在的
    # external\bongocat（CI 恰好会先构建它）泄漏进来，因此默认指向一个不存在的
    # 运行时目录；需要真实解析链路的用例请显式传 source。
    runtime_source = Path(source) if source is not None else Path(tmp_path) / "missing-runtime"
    return KeyMouseModeController(
        shell,
        config=config,
        config_dir=Path(tmp_path),
        command=command,
        runtime_source=runtime_source,
        process_factory=factory,
    )


def _fake_runtime(root: Path, *, exe: str = BONGOCAT_EXE) -> Path:
    runtime = Path(root)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / exe).write_bytes(b"stub")
    models = runtime / "assets" / "models" / "standard"
    models.mkdir(parents=True, exist_ok=True)
    (models / "cat.model3.json").write_text("{}", encoding="utf-8")
    return runtime


# --------------------------------------------------------------- 模式名归一化


def test_normalize_mode_accepts_only_known_values():
    assert normalize_mode("key_mouse") == MODE_KEY_MOUSE
    assert normalize_mode(" KEY_MOUSE ") == MODE_KEY_MOUSE
    assert normalize_mode("classic") == MODE_CLASSIC
    for bad in (None, "", "bongo", 3, {"mode": 1}):
        assert normalize_mode(bad) == MODE_CLASSIC


# ------------------------------------------------------------- 运行时解析顺序


def test_runtime_source_prefers_env_override(tmp_path):
    env_dir = _fake_runtime(tmp_path / "env")
    builtin = _fake_runtime(tmp_path / "builtin")
    installed = _fake_runtime(tmp_path / "installed")

    resolved = resolve_runtime_source(
        builtin_candidates=[builtin],
        env={"DSH_PET_BONGOCAT_DIR": str(env_dir)},
        installed_candidates=[installed],
    )

    assert resolved == env_dir


def test_runtime_source_falls_back_to_builtin_then_installed(tmp_path):
    builtin = _fake_runtime(tmp_path / "builtin")
    installed = _fake_runtime(tmp_path / "installed")

    assert resolve_runtime_source(
        builtin_candidates=[builtin], env={}, installed_candidates=[installed]
    ) == builtin
    assert resolve_runtime_source(
        builtin_candidates=[tmp_path / "missing"], env={}, installed_candidates=[installed]
    ) == installed
    assert resolve_runtime_source(
        builtin_candidates=[tmp_path / "missing"], env={"DSH_PET_BONGOCAT_DIR": ""},
        installed_candidates=[],
    ) is None


def test_runtime_source_ignores_env_dir_without_exe(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    builtin = _fake_runtime(tmp_path / "builtin")

    resolved = resolve_runtime_source(
        builtin_candidates=[builtin],
        env={"DSH_PET_BONGOCAT_DIR": str(empty)},
        installed_candidates=[],
    )

    assert resolved == builtin


# ------------------------------------------------------- 可写副本与素材覆盖层


def test_ensure_runtime_copy_creates_and_reuses_marker(tmp_path):
    source = _fake_runtime(tmp_path / "source")
    runtime = tmp_path / "data" / "runtime"

    assert ensure_runtime_copy(source, runtime) is True
    assert (runtime / BONGOCAT_EXE).is_file()
    marker = runtime / ".runtime-ok"
    assert marker.is_file()

    # 已同步且指纹一致时直接复用：放一个哨兵文件，重建会把整个目录换掉
    sentinel = runtime / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert ensure_runtime_copy(source, runtime) is True
    assert sentinel.is_file()

    # 模板变化（大小变）→ 重建：产物被刷新、哨兵消失
    (source / BONGOCAT_EXE).write_bytes(b"stub-changed")
    assert ensure_runtime_copy(source, runtime) is True
    assert marker.read_text(encoding="utf-8") != ""
    assert (runtime / BONGOCAT_EXE).read_bytes() == b"stub-changed"
    assert not sentinel.exists()


def test_ensure_runtime_copy_reports_failure_without_exe(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    assert ensure_runtime_copy(source, tmp_path / "runtime") is False


def test_sync_asset_overrides_copies_known_models_only(tmp_path):
    runtime = _fake_runtime(tmp_path / "runtime")
    overlay = tmp_path / "data" / "models"
    (overlay / "standard" / "resources" / "left-keys").mkdir(parents=True)
    (overlay / "standard" / "resources" / "left-keys" / "KeyA.png").write_bytes(b"png")
    (overlay / "standard" / "cat.model3.json").write_text("{}", encoding="utf-8")
    (overlay / "not-a-model").mkdir(parents=True, exist_ok=True)
    (overlay / "not-a-model" / "evil.txt").write_text("x", encoding="utf-8")

    copied = sync_asset_overrides(overlay, runtime)

    assert "standard/resources/left-keys/KeyA.png" in copied
    assert (
        runtime / "assets" / "models" / "standard" / "resources" / "left-keys" / "KeyA.png"
    ).is_file()
    assert not (runtime / "assets" / "models" / "not-a-model").exists()


def test_sync_asset_overrides_is_noop_without_overlay(tmp_path):
    runtime = _fake_runtime(tmp_path / "runtime")

    assert sync_asset_overrides(tmp_path / "missing", runtime) == []


# ----------------------------------------------------------------- 模式状态机


def test_runtime_missing_keeps_classic_and_reports_notice(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    config = FakeConfig()
    controller = _make_controller(shell, config, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.runtime_available() is False
    assert controller.enter() is False
    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert win.hide_notify == []
    assert notices and "运行时" in notices[-1][1]


def test_enter_hides_all_windows_starts_process_and_persists(tmp_path):
    _app()
    visible = FakeWindow()
    hidden = FakeWindow(visible=False)
    shell = FakeShell([FakeInstance(visible), FakeInstance(hidden), FakeInstance(None)])
    config = FakeConfig()
    controller = _make_controller(
        shell, config, tmp_path, command=[sys.executable, "-c", "pass"]
    )
    states: list[str] = []
    controller.state_changed.connect(states.append)

    assert controller.enter() is True

    assert controller.state == MODE_KEY_MOUSE
    assert states[-1] == MODE_KEY_MOUSE
    assert visible.hide_notify == [False]  # notify=False：不弹「已隐藏」提示
    assert hidden.hide_notify == [False]
    assert config.data["pet_mode"] == MODE_KEY_MOUSE
    assert isinstance(controller.process, FakeProcess)
    assert controller.process.started is True

    # 已有窗口被隐藏后，退出模式只恢复「切换前可见」的那一只
    assert controller.exit_mode() is True
    assert controller.state == MODE_CLASSIC
    assert visible.show_calls == 1
    assert hidden.show_calls == 0
    assert controller.process is None
    assert config.data["pet_mode"] == MODE_CLASSIC


def test_process_exit_restores_windows_and_falls_back(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    config = FakeConfig()
    controller = _make_controller(shell, config, tmp_path, command=[sys.executable, "-c", "pass"])
    states: list[str] = []
    controller.state_changed.connect(states.append)
    assert controller.enter() is True
    process = controller.process

    process.finished.emit()  # 用户点「切回原桌宠」/被任务管理器杀掉 → 同一路径

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert states[-1] == MODE_CLASSIC
    assert config.data["pet_mode"] == MODE_CLASSIC


def test_process_start_failure_rolls_back_to_classic(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    config = FakeConfig()

    def factory(command, cwd, parent=None):
        process = FakeProcess(command, cwd, parent)
        process.fail_start = True
        return process

    controller = _make_controller(
        shell, config, tmp_path, command=["definitely-missing.exe"], factory=factory
    )
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.enter() is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert config.data["pet_mode"] == MODE_CLASSIC
    assert notices and "启动失败" in notices[-1][1]


def test_exit_mode_is_idempotent_and_stops_process(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    controller = _make_controller(
        shell, FakeConfig(), tmp_path, command=[sys.executable, "-c", "pass"]
    )
    assert controller.enter() is True
    process = controller.process

    assert controller.exit_mode() is True
    assert process.stop_calls == 1
    assert controller.exit_mode() is False
    assert win.show_calls == 1


def test_multi_process_spawn_reports_children_stay_running(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win), FakeInstance(FakeWindow())], single_process_spawn=False)
    controller = _make_controller(
        shell, FakeConfig(), tmp_path, command=[sys.executable, "-c", "pass"]
    )
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.enter() is True

    assert notices and "子肥鱼" in notices[-1][1]


def test_single_process_spawn_does_not_warn(tmp_path):
    _app()
    shell = FakeShell(
        [FakeInstance(FakeWindow()), FakeInstance(FakeWindow())], single_process_spawn=True
    )
    controller = _make_controller(
        shell, FakeConfig(), tmp_path, command=[sys.executable, "-c", "pass"]
    )
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.enter() is True

    assert notices == []


# ------------------------------------------------------------- 启动记忆与收尾


def test_saved_key_mouse_mode_is_restored_on_startup(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    config = FakeConfig(mode=MODE_KEY_MOUSE)
    controller = _make_controller(shell, config, tmp_path, command=[sys.executable, "-c", "pass"])

    assert controller.start_for_saved_mode() is True

    assert controller.state == MODE_KEY_MOUSE
    assert win.isVisible() is False
    assert isinstance(controller.process, FakeProcess)


def test_saved_mode_without_runtime_falls_back_and_reports(tmp_path):
    _app()
    win = FakeWindow()
    shell = FakeShell([FakeInstance(win)])
    config = FakeConfig(mode=MODE_KEY_MOUSE)
    controller = _make_controller(shell, config, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.start_for_saved_mode() is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert config.data["pet_mode"] == MODE_CLASSIC
    assert notices and "经典桌宠" in notices[-1][1]


def test_classic_saved_mode_starts_no_process(tmp_path):
    _app()
    win = FakeWindow()
    controller = _make_controller(FakeShell([FakeInstance(win)]), FakeConfig(), tmp_path)

    assert controller.start_for_saved_mode() is False
    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert controller.process is None


def test_shutdown_stops_process_and_blocks_later_enter(tmp_path):
    _app()
    win = FakeWindow()
    controller = _make_controller(
        FakeShell([FakeInstance(win)]), FakeConfig(), tmp_path,
        command=[sys.executable, "-c", "pass"],
    )
    assert controller.enter() is True
    process = controller.process

    controller.shutdown()

    assert process.stop_calls == 1
    assert controller.process is None
    assert controller.enter() is False


def test_enter_uses_runtime_copy_and_syncs_overlay(tmp_path):
    _app()
    source = _fake_runtime(tmp_path / "template")
    data = tmp_path / "data"
    overlay = data / "bongocat" / "models" / "standard" / "resources" / "left-keys"
    overlay.mkdir(parents=True)
    (overlay / "KeyA.png").write_bytes(b"custom")
    win = FakeWindow()
    captured: list[FakeProcess] = []

    def factory(command, cwd, parent=None):
        process = FakeProcess(command, cwd, parent)
        captured.append(process)
        return process

    controller = _make_controller(
        FakeShell([FakeInstance(win)]), FakeConfig(), data, source=source, factory=factory
    )
    assert controller.runtime_available() is True
    assert controller.enter() is True

    paths = runtime_paths(data)
    assert captured[0].command == [str(paths.runtime / BONGOCAT_EXE)]
    assert captured[0].cwd == str(paths.runtime)
    assert (
        paths.runtime / "assets" / "models" / "standard" / "resources" / "left-keys" / "KeyA.png"
    ).read_bytes() == b"custom"


def test_runtime_paths_layout(tmp_path):
    paths = runtime_paths(tmp_path)

    assert paths.runtime == tmp_path / "bongocat" / "runtime"
    assert paths.overlay == tmp_path / "bongocat" / "models"


# ------------------------------------------------- 真实 QProcess 边接口冒烟


class _ModeMenuPet:
    """模式切换菜单所需的窗口替身（只暴露菜单构建用到的四个属性）。"""

    def __init__(self, mode=MODE_CLASSIC, *, available=True, allowed=True) -> None:
        self._mode = mode
        self._available = available
        self.mode_switch_allowed = allowed
        self.switched: list[str] = []
        self.cfg = FakeConfig(mode=mode)

    def pet_mode_state(self) -> str:
        return self._mode

    def on_set_pet_mode(self, mode) -> bool:
        self.switched.append(mode)
        self._mode = normalize_mode(mode)
        return True

    def key_mouse_mode_available(self) -> bool:
        return self._available


def _mode_submenu(pet):
    from PySide6.QtWidgets import QMenu

    from pet.context_menus.shared import add_mode_switch_menu

    menu = QMenu()
    return menu, add_mode_switch_menu(menu, pet)


def test_mode_menu_reflects_current_mode_and_triggers_switch():
    _app()
    pet = _ModeMenuPet()
    _, submenu = _mode_submenu(pet)

    actions = {action.text(): action for action in submenu.actions()}
    assert set(actions) == {"经典桌宠", "键鼠跟随"}
    assert actions["经典桌宠"].isChecked() is True
    assert actions["键鼠跟随"].isChecked() is False

    actions["键鼠跟随"].trigger()

    assert pet.switched == [MODE_KEY_MOUSE]


def test_mode_menu_checks_key_mouse_and_syncs_after_switch():
    from pet.context_menus.shared import sync_mode_switch_menu

    _app()
    pet = _ModeMenuPet(mode=MODE_KEY_MOUSE)
    _, submenu = _mode_submenu(pet)
    actions = {action.text(): action for action in submenu.actions()}
    assert actions["键鼠跟随"].isChecked() is True

    pet._mode = MODE_CLASSIC
    sync_mode_switch_menu(submenu, pet)

    assert actions["经典桌宠"].isChecked() is True
    assert actions["键鼠跟随"].isChecked() is False


def test_mode_menu_disabled_with_reason_when_runtime_missing():
    _app()
    pet = _ModeMenuPet(available=False)
    _, submenu = _mode_submenu(pet)

    key_mouse = next(action for action in submenu.actions() if action.text() == "键鼠跟随")

    assert key_mouse.isEnabled() is False
    assert "运行时" in key_mouse.toolTip()


def test_mode_menu_disabled_for_child_pet():
    _app()
    pet = _ModeMenuPet(allowed=False)
    _, submenu = _mode_submenu(pet)

    key_mouse = next(action for action in submenu.actions() if action.text() == "键鼠跟随")

    assert key_mouse.isEnabled() is False
    assert "主桌宠" in key_mouse.toolTip()


def test_mode_menu_absent_without_switch_callback():
    _app()
    pet = type("Pet", (), {"cfg": FakeConfig()})()

    menu, submenu = _mode_submenu(pet)

    assert submenu is None
    assert menu.actions() == []


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上做真实子进程冒烟")
def test_real_qprocess_wrapper_starts_and_stops(tmp_path):
    _app()
    process = KeyMouseProcess([sys.executable, "-c", "import time; time.sleep(30)"], None)

    assert process.start() is True
    assert process.is_running() is True

    process.stop()

    assert process.is_running() is False


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上做真实子进程冒烟")
def test_real_qprocess_wrapper_reports_start_failure():
    _app()
    process = KeyMouseProcess(["definitely-missing-bongocat.exe"], None)

    assert process.start() is False
    assert process.is_running() is False
    assert process.qt_process.state() == QProcess.ProcessState.NotRunning
