# -*- coding: utf-8 -*-
"""外接启动模式：主桌宠可以把外部程序注册成"模式"，并在模式间切换。

语义（与经典桌宠相互独立，只通过模式切换互通）：

* ``classic``：现状桌宠。窗口可见性、动画解码、物理与检测服务全部照旧。
* ``external:<id>``：隐藏并深度暂停全部桌宠窗口（复用 ``PetWindow.hide(notify=False)``
  已有的「不可见即零消耗」语义），拉起配置好的外部程序；外部进程一旦退出
  （用户在它自己的菜单里退出、被任务管理器杀掉或崩溃）即自动恢复原桌宠。

典型用法是 BongoCat（键鼠跟随桌宠）：它自带"导入模型"，所以模型素材由它自己管理，
本模块只负责"启动 / 收尾 / 与经典桌宠互斥"。因此**不需要编译或内置任何第三方程序**。

模块边界：本模块持有模式权威与子进程生命周期，不认识 ``PetWindow`` 内部状态，
只使用窗口的公开动作 ``hide(notify=False)`` / ``show()``；配置解析、模式目录、
BongoCat 自动检测都在这里收口，便于单测。
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import QObject, QProcess, Signal


log = logging.getLogger("dsh-pet-standalone")

MODE_CLASSIC = "classic"
MODE_PREFIX = "external:"
VALID_EXTERNAL_MODES_ERROR = "外接模式配置无效"

# 主配置键。
PET_MODE_KEY = "pet_mode"              # classic | external:<id>
EXTERNAL_MODES_KEY = "external_modes"  # [{id,name,exe,args,cwd}, ...]

# 启动/停止子进程的等待上限：仅在程序无法启动或退出无响应时才会等满。
START_TIMEOUT_MS = 5000
TERMINATE_TIMEOUT_MS = 3000
KILL_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class ExternalModeSpec:
    """一个外接启动模式的启动规格。"""

    id: str
    name: str
    exe: str
    args: tuple[str, ...] = ()
    cwd: str = ""

    @property
    def mode_value(self) -> str:
        return MODE_PREFIX + self.id

    def command(self) -> list[str]:
        return [self.exe, *self.args]

    def work_dir(self) -> str:
        if self.cwd:
            return self.cwd
        return str(Path(self.exe).parent) if self.exe else ""

    def available(self) -> bool:
        try:
            return bool(self.exe) and Path(self.exe).is_file()
        except OSError:
            return False

    def unavailable_reason(self) -> str:
        if not self.exe:
            return "未配置可执行文件路径"
        if not Path(self.exe).is_file():
            return f"找不到程序：{self.exe}"
        return ""

    def to_config(self) -> dict:
        data: dict = {"id": self.id, "name": self.name, "exe": self.exe}
        if self.args:
            data["args"] = list(self.args)
        if self.cwd:
            data["cwd"] = self.cwd
        return data


def slug_for_exe(exe: str) -> str:
    """由可执行文件路径生成稳定 id：改名不换 id，同路径永远同一个模式。"""
    digest = hashlib.sha1(str(exe).strip().lower().encode("utf-8")).hexdigest()
    return digest[:8]


def make_spec(exe, *, name: str = "", args: Iterable[str] = (), cwd: str = "") -> ExternalModeSpec:
    exe_text = str(exe or "").strip()
    label = (name or "").strip() or (Path(exe_text).stem if exe_text else "外接模式")
    return ExternalModeSpec(
        id=slug_for_exe(exe_text),
        name=label,
        exe=exe_text,
        args=tuple(str(item) for item in (args or ())),
        cwd=str(cwd or "").strip(),
    )


def parse_external_modes(raw) -> list[ExternalModeSpec]:
    """把配置里的原始列表归一化成启动规格（脏数据一律忽略，不抛异常）。"""
    specs: list[ExternalModeSpec] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else ():
        if not isinstance(item, Mapping):
            continue
        exe = str(item.get("exe") or "").strip()
        if not exe:
            continue
        spec = make_spec(
            exe,
            name=str(item.get("name") or ""),
            args=[str(a) for a in (item.get("args") or [])],
            cwd=str(item.get("cwd") or ""),
        )
        if spec.id in seen:
            continue
        seen.add(spec.id)
        specs.append(spec)
    return specs


def normalize_mode_value(value) -> str:
    """把 ``pet_mode`` 归一化为 ``classic`` 或 ``external:<id>``。"""
    text = str(value or "").strip().lower()
    if text.startswith(MODE_PREFIX) and len(text) > len(MODE_PREFIX):
        mode_id = text[len(MODE_PREFIX):].strip()
        if mode_id:
            return MODE_PREFIX + mode_id
    return MODE_CLASSIC


def bongo_cat_candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """已安装 BongoCat 的常见位置（Windows）。"""
    if sys.platform != "win32":
        return []
    env = os.environ if env is None else env
    candidates: list[Path] = []
    local = str(env.get("LOCALAPPDATA") or "").strip()
    if local:
        candidates.append(Path(local) / "Programs" / "BongoCat" / "BongoCat.exe")
        candidates.append(Path(local) / "BongoCat" / "BongoCat.exe")
    program_files = str(env.get("ProgramFiles") or "").strip()
    if program_files:
        candidates.append(Path(program_files) / "BongoCat" / "BongoCat.exe")
    return candidates


def detect_bongo_cat(env: Mapping[str, str] | None = None) -> Path | None:
    for candidate in bongo_cat_candidates(env):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# --------------------------------------------------------------- 子进程包装


class ExternalProcess(QObject):
    """外接程序子进程包装：启动失败同步返回，退出经 ``finished`` 通知。"""

    finished = Signal()

    def __init__(self, command, cwd=None, parent=None) -> None:
        super().__init__(parent)
        self._command = [str(item) for item in command]
        self._cwd = str(cwd) if cwd else ""
        self._stopping = False
        self.qt_process = QProcess(self)
        if self._cwd:
            self.qt_process.setWorkingDirectory(self._cwd)
        self.qt_process.setProgram(self._command[0])
        self.qt_process.setArguments(self._command[1:])
        self.qt_process.finished.connect(self._on_finished)
        self.qt_process.errorOccurred.connect(self._on_error)

    @property
    def command(self) -> list[str]:
        return list(self._command)

    def is_running(self) -> bool:
        return self.qt_process.state() != QProcess.ProcessState.NotRunning

    def start(self) -> bool:
        if self.is_running():
            return True
        self.qt_process.start()
        if self.qt_process.waitForStarted(START_TIMEOUT_MS):
            return True
        log.warning(
            "[MODE] 外接模式启动失败: %s（%s）", self._command[0], self.qt_process.errorString()
        )
        return False

    def stop(self) -> None:
        self._stopping = True
        try:
            if self.is_running():
                self.qt_process.terminate()
                if not self.qt_process.waitForFinished(TERMINATE_TIMEOUT_MS):
                    self.qt_process.kill()
                    self.qt_process.waitForFinished(KILL_TIMEOUT_MS)
        except RuntimeError:
            pass  # 底层 C++ 对象已销毁（应用退出竞态）：无须再收尾
        finally:
            self._stopping = False

    def _on_finished(self, *_args) -> None:
        if self._stopping:
            return
        self.finished.emit()

    def _on_error(self, error) -> None:
        if self._stopping:
            return
        if error == QProcess.ProcessError.FailedToStart:
            return  # 由 start() 同步判定，交给调用方回退
        if not self.is_running():
            self.finished.emit()


def _default_process_factory(command, cwd, parent=None) -> ExternalProcess:
    return ExternalProcess(command, cwd, parent)


# ------------------------------------------------------------------- 控制器


class ExternalModeController(QObject):
    """模式状态机：谁在跑、原桌宠是否暂停、模式是否落盘。"""

    state_changed = Signal(str)
    notice = Signal(str, str)

    def __init__(
        self,
        shell,
        *,
        config,
        command: Iterable[str] | None = None,
        env: Mapping[str, str] | None = None,
        process_factory=None,
        parent=None,
    ) -> None:
        owner = parent if parent is not None else (
            shell if isinstance(shell, QObject) else None
        )
        super().__init__(owner)
        self._shell = shell
        self._config = config
        self._command_override = [str(item) for item in command] if command else None
        self._env = env
        self._process_factory = process_factory or _default_process_factory
        self._process = None
        self._mode = MODE_CLASSIC
        self._restore_visible: list = []
        self._closing = False

    # ------------------------------------------------------------- 对外状态
    @property
    def state(self) -> str:
        return self._mode

    @property
    def process(self):
        return self._process

    def modes(self) -> list[ExternalModeSpec]:
        if self._config is None:
            return []
        return parse_external_modes(self._config.get(EXTERNAL_MODES_KEY))

    def mode(self, mode_id: str) -> ExternalModeSpec | None:
        for spec in self.modes():
            if spec.id == mode_id:
                return spec
        return None

    def active_mode(self) -> ExternalModeSpec | None:
        if not self._mode.startswith(MODE_PREFIX):
            return None
        return self.mode(self._mode[len(MODE_PREFIX):])

    def mode_available(self, mode_id: str) -> bool:
        spec = self.mode(mode_id)
        return bool(spec and spec.available())

    def add_mode(self, spec: ExternalModeSpec) -> bool:
        """新增/更新一个外接模式；返回是否写入了配置。"""
        if self._config is None or not spec.exe:
            return False
        specs = [item for item in self.modes() if item.id != spec.id]
        specs.append(spec)
        self._config.set(EXTERNAL_MODES_KEY, [item.to_config() for item in specs])
        self._config.save()
        return True

    def remove_mode(self, mode_id: str) -> bool:
        if self._config is None:
            return False
        specs = self.modes()
        remaining = [item for item in specs if item.id != mode_id]
        if len(remaining) == len(specs):
            return False
        self._config.set(EXTERNAL_MODES_KEY, [item.to_config() for item in remaining])
        self._config.save()
        if self._mode == MODE_PREFIX + mode_id:
            self.exit_mode()
        return True

    def detected_candidates(self) -> list[ExternalModeSpec]:
        """自动检测到的候选（目前是已安装的 BongoCat，不与已配置项重复）。"""
        known = {spec.exe.lower() for spec in self.modes()}
        found = detect_bongo_cat(self._env)
        if found is None or str(found).lower() in known:
            return []
        return [make_spec(found, name="BongoCat（键鼠跟随）")]

    # --------------------------------------------------------------- 生命周期
    def start_for_saved_mode(self) -> bool:
        """启动时按持久化模式接管：可用则直接进入，不可用则回落并提示。"""
        saved = normalize_mode_value(self._config.get(PET_MODE_KEY) if self._config else None)
        if saved == MODE_CLASSIC:
            self._set_mode(MODE_CLASSIC)
            return False
        mode_id = saved[len(MODE_PREFIX):]
        spec = self.mode(mode_id)
        if spec is None or not spec.available():
            self._set_mode(MODE_CLASSIC)
            self.notice.emit(
                "外接模式",
                "上次使用的外接模式不可用（程序已移动或删除），已回到经典桌宠。",
            )
            return False
        return self.enter(mode_id)

    def enter(self, mode_id: str) -> bool:
        """进入指定外接模式；失败时保持在经典模式并给出提示。

        从经典模式进入时先暂停桌宠窗口再拉起程序；**从另一个外接模式直切时窗口
        全程保持隐藏**——不能经由 ``exit_mode()``（那会把恢复显示的窗口交给下一次
        进入再隐藏，桌面上会闪过一帧桌宠）。启动失败才把窗口恢复回经典模式。
        """
        if self._closing:
            return False
        spec = self.mode(mode_id)
        if spec is None:
            self.notice.emit("外接模式", "该外接模式未配置，已保持经典桌宠。")
            return False
        if self._mode == spec.mode_value:
            return True
        was_classic = self._mode == MODE_CLASSIC
        if not was_classic:
            self._stop_current_process()   # 停旧进程，但窗口保持隐藏
        if self._command_override is None and not spec.available():
            self.notice.emit("外接模式", f"无法启动「{spec.name}」：{spec.unavailable_reason()}")
            if not was_classic:
                self.exit_mode()           # 旧模式已收掉：回经典，不能让窗口留在隐藏态
            return False
        command = (
            list(self._command_override) if self._command_override is not None else spec.command()
        )
        if was_classic:
            self._restore_visible = self._visible_instances()
            self._pause_windows()
        process = self._process_factory(command, spec.work_dir() or None, self)
        self._process = process
        process.finished.connect(self._on_process_finished)
        if not process.start():
            self._release_process(process)
            self._process = None
            if was_classic:
                self._resume_windows()
                self.notice.emit("外接模式", f"「{spec.name}」启动失败，已保持经典桌宠模式。")
            else:
                self.notice.emit("外接模式", f"「{spec.name}」启动失败，已恢复经典桌宠。")
                self.exit_mode()
            return False
        self._set_mode(spec.mode_value)
        self._warn_multi_process(spec)
        return True

    def exit_mode(self) -> bool:
        """退出外接模式并恢复原桌宠；已处于经典模式时返回 False。"""
        if self._mode == MODE_CLASSIC and self._process is None:
            return False
        self._stop_current_process()
        self._resume_windows()
        self._set_mode(MODE_CLASSIC)
        return True

    def _stop_current_process(self) -> None:
        """脱离并终止当前子进程，**不动窗口可见性**（调用方决定何时恢复）。"""
        process, self._process = self._process, None
        if process is None:
            return
        self._release_process(process)
        try:
            process.stop()
        except RuntimeError:
            pass

    def shutdown(self) -> None:
        """应用退出/会话结束：终止子进程并禁止再进入模式。"""
        self._closing = True
        self._stop_current_process()
        if self._mode != MODE_CLASSIC:
            self._restore_visible = []   # 退出中：不恢复窗口，避免退出瞬间闪一下
            self._set_mode(MODE_CLASSIC)

    # ------------------------------------------------------------ 内部实现
    def _instances(self) -> list:
        instances = getattr(self._shell, "instances", None)
        if instances is None:
            return []
        try:
            return list(instances)
        except TypeError:
            return []

    def _visible_instances(self) -> list:
        visible = []
        for instance in self._instances():
            win = getattr(instance, "win", None)
            if win is None:
                continue
            try:
                if win.isVisible():
                    visible.append(instance)
            except RuntimeError:
                continue
        return visible

    def _pause_windows(self) -> None:
        for instance in self._instances():
            win = getattr(instance, "win", None)
            if win is None:
                continue
            try:
                win.hide(notify=False)
            except RuntimeError:
                continue
        self._notify_visibility_changed()

    def _resume_windows(self) -> None:
        pending, self._restore_visible = self._restore_visible, []
        for instance in pending:
            win = getattr(instance, "win", None)
            if win is None:
                continue
            try:
                if not win.isVisible():
                    win.show()
            except RuntimeError:
                continue
        self._notify_visibility_changed()

    def _notify_visibility_changed(self) -> None:
        hook = getattr(self._shell, "on_pet_visibility_changed", None)
        if callable(hook):
            try:
                hook()
            except RuntimeError:
                pass

    def _warn_multi_process(self, spec: ExternalModeSpec) -> None:
        if len(self._instances()) <= 1:
            return
        if bool(getattr(self._shell, "single_process_spawn", False)):
            return
        self.notice.emit(
            "外接模式",
            f"已暂停主桌宠并启动「{spec.name}」；多进程多开下的子肥鱼会继续运行"
            "（开启单进程多开时可全部一起切）。",
        )

    def _release_process(self, process=None) -> None:
        """解除 finished 连线并交给 Qt 回收（stop 由调用方按需触发）。"""
        process = self._process if process is None else process
        if process is None:
            return
        try:
            process.finished.disconnect(self._on_process_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            process.deleteLater()
        except (AttributeError, RuntimeError):
            pass

    def _on_process_finished(self) -> None:
        """外接进程自行退出（用户在它自己的菜单里退出/被杀）：恢复原桌宠。"""
        if self._mode == MODE_CLASSIC:
            return
        name = (self.active_mode().name if self.active_mode() else "外接模式")
        process, self._process = self._process, None
        self._release_process(process)
        self._resume_windows()
        self._set_mode(MODE_CLASSIC)
        self.notice.emit("外接模式", f"「{name}」已退出，已恢复经典桌宠。")

    def _set_mode(self, mode: str) -> None:
        mode = normalize_mode_value(mode)
        changed = mode != self._mode
        self._mode = mode
        if self._config is not None:
            try:
                if self._config.get(PET_MODE_KEY) != mode:
                    self._config.set(PET_MODE_KEY, mode)
                    self._config.save()
            except (AttributeError, OSError):
                log.warning("[MODE] 持久化 pet_mode 失败", exc_info=True)
        if changed:
            self.state_changed.emit(mode)
