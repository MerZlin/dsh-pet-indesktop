# -*- coding: utf-8 -*-
"""外接启动模式的模式状态机与菜单行为测试。

覆盖：配置解析/归一化、BongoCat 自动检测、进入/退出外接模式的窗口暂停与恢复、
子进程退出/启动失败的回退、启动记忆、添加/移除模式、菜单勾选与禁用原因。
子进程用可注入假进程替代；另有一个真实 QProcess 的边接口冒烟测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QProcess, Signal

from pet.external_mode import (
    EXTERNAL_MODES_KEY,
    MODE_CLASSIC,
    MODE_PREFIX,
    PET_MODE_KEY,
    ExternalModeController,
    ExternalProcess,
    detect_bongo_cat,
    make_spec,
    normalize_mode_value,
    parse_external_modes,
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
    def __init__(self, win) -> None:
        self.win = win


class FakeShell:
    def __init__(self, instances, single_process_spawn: bool = False) -> None:
        self.instances = list(instances)
        self.single_process_spawn = single_process_spawn
        self.visibility_syncs = 0

    def on_pet_visibility_changed(self) -> None:
        self.visibility_syncs += 1


class FakeConfig:
    """最小配置替身：只实现外接模式相关键的读改写与落盘计数。"""

    def __init__(self, **data) -> None:
        self.data = dict(data)
        self.save_calls = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.save_calls += 1


class FakeProcess(QObject):
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


def _exe(tmp_path: Path, name: str = "app.exe") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_bytes(b"stub")
    return path


def _controller(shell, config, tmp_path, **kwargs):
    return ExternalModeController(
        shell, config=config, process_factory=kwargs.pop("factory", FakeProcess), **kwargs
    )


# ------------------------------------------------------------------ 配置解析


def test_parse_external_modes_ignores_dirty_entries():
    specs = parse_external_modes([
        {"exe": "C:/a/BongoCat.exe", "name": "Bongo"},
        {"exe": ""},                       # 空路径丢弃
        "nonsense",                        # 非映射丢弃
        {"exe": "C:/a/BongoCat.exe"},      # 同路径去重
        {"exe": "C:/b/other.exe", "args": ["--flag"], "cwd": "C:/b"},
    ])

    assert [spec.name for spec in specs] == ["Bongo", "other"]
    assert specs[1].args == ("--flag",)
    assert specs[1].cwd == "C:/b"
    assert specs[0].id == make_spec("C:/a/BongoCat.exe").id


def test_spec_helpers():
    spec = make_spec("C:/x/MyPet.exe")

    assert spec.name == "MyPet"
    assert spec.mode_value == MODE_PREFIX + spec.id
    assert spec.command() == ["C:/x/MyPet.exe"]
    assert spec.work_dir() == str(Path("C:/x"))
    assert spec.to_config() == {"id": spec.id, "name": "MyPet", "exe": "C:/x/MyPet.exe"}


def test_normalize_mode_value():
    assert normalize_mode_value(None) == MODE_CLASSIC
    assert normalize_mode_value("classic") == MODE_CLASSIC
    assert normalize_mode_value("key_mouse") == MODE_CLASSIC  # 旧值不再有效
    assert normalize_mode_value("EXTERNAL:AbC123") == "external:abc123"
    assert normalize_mode_value("external:") == MODE_CLASSIC


def test_detect_bongo_cat_uses_local_appdata(tmp_path):
    exe = _exe(tmp_path / "Programs" / "BongoCat", "BongoCat.exe")
    env = {"LOCALAPPDATA": str(tmp_path), "ProgramFiles": str(tmp_path / "pf")}

    assert detect_bongo_cat(env) == exe
    assert detect_bongo_cat({"LOCALAPPDATA": str(tmp_path / "nope")}) is None


# ------------------------------------------------------------------ 模式状态机


def test_enter_hides_windows_launches_and_persists(tmp_path):
    _app()
    exe = _exe(tmp_path)
    visible, hidden = FakeWindow(), FakeWindow(visible=False)
    shell = FakeShell([FakeInstance(visible), FakeInstance(hidden), FakeInstance(None)])
    config = FakeConfig(**{EXTERNAL_MODES_KEY: [{"exe": str(exe), "name": "Bongo"}]})
    controller = _controller(shell, config, tmp_path)
    states: list[str] = []
    controller.state_changed.connect(states.append)
    spec = controller.modes()[0]

    assert controller.enter(spec.id) is True

    assert controller.state == spec.mode_value
    assert states[-1] == spec.mode_value
    assert visible.hide_notify == [False]  # notify=False：不弹「已隐藏」提示
    assert hidden.hide_notify == [False]
    assert config.data[PET_MODE_KEY] == spec.mode_value
    assert isinstance(controller.process, FakeProcess)
    assert controller.process.command == [str(exe)]
    assert controller.process.cwd == str(tmp_path)
    assert shell.visibility_syncs >= 1

    assert controller.exit_mode() is True
    assert controller.state == MODE_CLASSIC
    assert visible.show_calls == 1      # 只恢复"切换前可见"的那只
    assert hidden.show_calls == 0
    assert config.data[PET_MODE_KEY] == MODE_CLASSIC


def test_process_exit_restores_windows(tmp_path):
    _app()
    exe = _exe(tmp_path)
    win = FakeWindow()
    config = FakeConfig(**{EXTERNAL_MODES_KEY: [{"exe": str(exe), "name": "Bongo"}]})
    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path)
    assert controller.enter(controller.modes()[0].id) is True
    process = controller.process

    process.finished.emit()  # 用户在外接程序里退出 / 被任务管理器杀掉

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert config.data[PET_MODE_KEY] == MODE_CLASSIC


def test_unavailable_mode_keeps_classic_and_reports(tmp_path):
    _app()
    win = FakeWindow()
    config = FakeConfig(
        **{EXTERNAL_MODES_KEY: [{"exe": str(tmp_path / "missing.exe"), "name": "没了"}]}
    )
    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))
    spec = controller.modes()[0]

    assert spec.available() is False
    assert "找不到程序" in spec.unavailable_reason()
    assert controller.enter(spec.id) is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert notices and "找不到程序" in notices[-1][1]


def test_start_failure_rolls_back(tmp_path):
    _app()
    exe = _exe(tmp_path)
    win = FakeWindow()
    config = FakeConfig(**{EXTERNAL_MODES_KEY: [{"exe": str(exe), "name": "Bongo"}]})

    def factory(command, cwd, parent=None):
        process = FakeProcess(command, cwd, parent)
        process.fail_start = True
        return process

    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path, factory=factory)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.enter(controller.modes()[0].id) is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert config.data.get(PET_MODE_KEY, MODE_CLASSIC) == MODE_CLASSIC
    assert notices and "启动失败" in notices[-1][1]


def test_switching_between_external_modes_stops_previous(tmp_path):
    _app()
    a, b = _exe(tmp_path, "a.exe"), _exe(tmp_path, "b.exe")
    config = FakeConfig(**{
        EXTERNAL_MODES_KEY: [{"exe": str(a), "name": "A"}, {"exe": str(b), "name": "B"}]
    })
    controller = _controller(FakeShell([FakeInstance(FakeWindow())]), config, tmp_path)
    first, second = controller.modes()

    controller.enter(first.id)
    previous = controller.process
    controller.enter(second.id)

    assert previous.stop_calls == 1
    assert controller.state == second.mode_value


def test_switching_between_external_modes_never_shows_windows(tmp_path):
    """外接模式之间直切：桌宠窗口全程不得可见（否则切换瞬间会闪一下）。

    旧实现先走 exit_mode() —— 那会把「切换前可见」的窗口恢复显示，紧接着
    又被下一次进入暂停，桌面上闪过一帧桌宠。这里用 show_calls 钉住该回归。
    """
    _app()
    a, b = _exe(tmp_path, "a.exe"), _exe(tmp_path, "b.exe")
    win = FakeWindow()
    config = FakeConfig(**{
        EXTERNAL_MODES_KEY: [{"exe": str(a), "name": "A"}, {"exe": str(b), "name": "B"}]
    })
    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path)
    first, second = controller.modes()

    assert controller.enter(first.id) is True
    assert win.isVisible() is False
    assert win.show_calls == 0

    assert controller.enter(second.id) is True

    assert controller.state == second.mode_value
    assert win.isVisible() is False        # 新程序起来前保持隐藏
    assert win.show_calls == 0             # 中途一次都没露过面

    # 正常退出时仍然按「切换前可见」恢复，语义不变
    assert controller.exit_mode() is True
    assert win.isVisible() is True
    assert win.show_calls == 1


def test_switching_to_external_mode_that_fails_stays_paused(tmp_path):
    """外接模式之间直切、新程序启动失败：回退到经典桌宠并恢复窗口，不留在半途。"""
    _app()
    a, b = _exe(tmp_path, "a.exe"), _exe(tmp_path, "b.exe")
    win = FakeWindow()
    config = FakeConfig(**{
        EXTERNAL_MODES_KEY: [{"exe": str(a), "name": "A"}, {"exe": str(b), "name": "B"}]
    })
    created: list[FakeProcess] = []

    def factory(command, cwd, parent=None):
        process = FakeProcess(command, cwd, parent)
        process.fail_start = len(created) == 1     # 只有第二次（切到 B）失败
        created.append(process)
        return process

    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path, factory=factory)
    first, second = controller.modes()

    assert controller.enter(first.id) is True
    assert controller.enter(second.id) is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert win.show_calls == 1


def test_add_and_remove_mode(tmp_path):
    _app()
    exe = _exe(tmp_path)
    config = FakeConfig()
    controller = _controller(FakeShell([FakeInstance(FakeWindow())]), config, tmp_path)
    spec = make_spec(exe, name="我的桌宠")

    assert controller.add_mode(spec) is True
    assert [item.name for item in controller.modes()] == ["我的桌宠"]
    assert config.data[EXTERNAL_MODES_KEY][0]["exe"] == str(exe)

    assert controller.enter(spec.id) is True
    assert controller.remove_mode(spec.id) is True
    assert controller.modes() == []
    assert controller.state == MODE_CLASSIC      # 移除正在运行的模式 → 自动退出
    assert controller.remove_mode(spec.id) is False


def test_add_mode_is_idempotent_per_exe(tmp_path):
    _app()
    exe = _exe(tmp_path)
    config = FakeConfig()
    controller = _controller(FakeShell([]), config, tmp_path)

    controller.add_mode(make_spec(exe, name="旧名"))
    controller.add_mode(make_spec(exe, name="新名"))

    assert [item.name for item in controller.modes()] == ["新名"]


def test_detected_candidates_skips_configured(tmp_path):
    _app()
    env = {"LOCALAPPDATA": str(tmp_path), "ProgramFiles": str(tmp_path / "pf")}
    exe = _exe(tmp_path / "Programs" / "BongoCat", "BongoCat.exe")
    controller = _controller(FakeShell([]), FakeConfig(), tmp_path, env=env)

    candidates = controller.detected_candidates()
    assert [item.exe for item in candidates] == [str(exe)]
    assert candidates[0].name.startswith("BongoCat")

    controller.add_mode(candidates[0])
    assert controller.detected_candidates() == []


# ------------------------------------------------------------- 启动记忆与收尾


def test_saved_mode_is_restored_on_startup(tmp_path):
    _app()
    exe = _exe(tmp_path)
    spec = make_spec(exe, name="Bongo")
    win = FakeWindow()
    config = FakeConfig(**{
        EXTERNAL_MODES_KEY: [spec.to_config()], PET_MODE_KEY: spec.mode_value,
    })
    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path)

    assert controller.start_for_saved_mode() is True

    assert controller.state == spec.mode_value
    assert win.isVisible() is False


def test_saved_mode_without_program_falls_back(tmp_path):
    _app()
    win = FakeWindow()
    config = FakeConfig(**{
        EXTERNAL_MODES_KEY: [{"exe": str(tmp_path / "gone.exe"), "name": "gone"}],
        PET_MODE_KEY: "external:deadbeef",
    })
    controller = _controller(FakeShell([FakeInstance(win)]), config, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.start_for_saved_mode() is False

    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert config.data[PET_MODE_KEY] == MODE_CLASSIC
    assert notices and "经典桌宠" in notices[-1][1]


def test_classic_saved_mode_starts_nothing(tmp_path):
    _app()
    win = FakeWindow()
    controller = _controller(FakeShell([FakeInstance(win)]), FakeConfig(), tmp_path)

    assert controller.start_for_saved_mode() is False
    assert controller.state == MODE_CLASSIC
    assert win.isVisible() is True
    assert controller.process is None


def test_shutdown_stops_process_and_blocks_enter(tmp_path):
    _app()
    exe = _exe(tmp_path)
    config = FakeConfig(**{EXTERNAL_MODES_KEY: [{"exe": str(exe), "name": "Bongo"}]})
    controller = _controller(FakeShell([FakeInstance(FakeWindow())]), config, tmp_path)
    assert controller.enter(controller.modes()[0].id) is True
    process = controller.process

    controller.shutdown()

    assert process.stop_calls == 1
    assert controller.process is None
    assert controller.enter(controller.modes()[0].id) is False


def test_multi_process_spawn_reports_children_stay_running(tmp_path):
    _app()
    exe = _exe(tmp_path)
    config = FakeConfig(**{EXTERNAL_MODES_KEY: [{"exe": str(exe), "name": "Bongo"}]})
    shell = FakeShell(
        [FakeInstance(FakeWindow()), FakeInstance(FakeWindow())], single_process_spawn=False
    )
    controller = _controller(shell, config, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.notice.connect(lambda title, message: notices.append((title, message)))

    assert controller.enter(controller.modes()[0].id) is True

    assert notices and "子肥鱼" in notices[-1][1]


# ------------------------------------------------------------- 菜单（公开接缝）


class _ModeMenuPet:
    """外接模式菜单所需的窗口替身。"""

    def __init__(self, modes=(), *, mode=MODE_CLASSIC, allowed=True, detected=()) -> None:
        self._modes = list(modes)
        self._mode = mode
        self._detected = list(detected)
        self.mode_switch_allowed = allowed
        self.switched: list[str] = []
        self.picked: list[str] = []
        self.removed: list[str] = []

    def pet_mode_state(self) -> str:
        return self._mode

    def external_mode_list(self):
        return list(self._modes)

    def detected_external_mode_list(self):
        return list(self._detected)

    def on_set_pet_mode(self, value) -> bool:
        self.switched.append(value)
        self._mode = value
        return True

    def on_pick_external_mode_exe(self) -> None:
        self.picked.append("pick")
        return None

    def on_add_external_mode(self, spec) -> bool:
        self._modes.append(spec)
        return True

    def on_remove_external_mode(self, mode_id) -> bool:
        self.removed.append(mode_id)
        self._modes = [item for item in self._modes if item.id != mode_id]
        return True


def _mode_menu(pet):
    from PySide6.QtWidgets import QMenu

    from pet.context_menus.shared import add_external_mode_menu

    menu = QMenu()
    return menu, add_external_mode_menu(menu, pet)


def test_mode_menu_lists_classic_modes_and_switches(tmp_path):
    _app()
    spec = make_spec(_exe(tmp_path), name="Bongo")
    pet = _ModeMenuPet([spec])
    _, submenu = _mode_menu(pet)

    actions = {action.text(): action for action in submenu.actions() if not action.isSeparator()}
    assert "经典桌宠" in actions
    assert "Bongo" in actions
    assert actions["经典桌宠"].isChecked() is True
    assert actions["Bongo"].isChecked() is False

    actions["Bongo"].trigger()

    assert pet.switched == [spec.mode_value]


def test_mode_menu_checks_active_mode(tmp_path):
    _app()
    spec = make_spec(_exe(tmp_path), name="Bongo")
    pet = _ModeMenuPet([spec], mode=spec.mode_value)

    _, submenu = _mode_menu(pet)
    actions = {action.text(): action for action in submenu.actions() if not action.isSeparator()}

    assert actions["Bongo"].isChecked() is True
    assert actions["经典桌宠"].isChecked() is False


def test_mode_menu_disables_missing_program_with_reason(tmp_path):
    _app()
    spec = make_spec(tmp_path / "missing.exe", name="没了")
    pet = _ModeMenuPet([spec])

    _, submenu = _mode_menu(pet)
    action = next(item for item in submenu.actions() if item.text() == "没了")

    assert action.isEnabled() is False
    assert "找不到程序" in action.toolTip()


def test_mode_menu_add_remove_and_detected_entries(tmp_path):
    _app()
    spec = make_spec(_exe(tmp_path), name="Bongo")
    detected = make_spec(_exe(tmp_path, "BongoCat.exe"), name="BongoCat")
    pet = _ModeMenuPet([spec], detected=[detected])

    _, submenu = _mode_menu(pet)
    labels = [item.text() for item in submenu.actions() if not item.isSeparator()]

    assert "添加外接模式…" in labels
    assert "添加 BongoCat" in labels
    assert "移除外接模式" in labels

    next(item for item in submenu.actions() if item.text() == "添加外接模式…").trigger()
    assert pet.picked == ["pick"]

    next(item for item in submenu.actions() if item.text().startswith("添加 BongoCat")).trigger()
    assert any(item.id == detected.id for item in pet._modes)

    remove_menu = next(item.menu() for item in submenu.actions() if item.text() == "移除外接模式")
    next(item for item in remove_menu.actions() if item.text() == "Bongo").trigger()
    assert pet.removed == [spec.id]


def test_mode_menu_disabled_for_child_pet(tmp_path):
    _app()
    spec = make_spec(_exe(tmp_path), name="Bongo")
    pet = _ModeMenuPet([spec], allowed=False)

    _, submenu = _mode_menu(pet)
    action = next(item for item in submenu.actions() if item.text() == "Bongo")

    assert action.isEnabled() is False
    assert "主桌宠" in action.toolTip()


# ------------------------------------------------- 真实 QProcess 边接口冒烟


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上做真实子进程冒烟")
def test_real_process_wrapper_starts_and_stops():
    _app()
    process = ExternalProcess([sys.executable, "-c", "import time; time.sleep(30)"], None)

    assert process.start() is True
    assert process.is_running() is True

    process.stop()

    assert process.is_running() is False


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上做真实子进程冒烟")
def test_real_process_wrapper_reports_start_failure():
    _app()
    process = ExternalProcess(["definitely-missing-external-mode.exe"], None)

    assert process.start() is False
    assert process.qt_process.state() == QProcess.ProcessState.NotRunning
