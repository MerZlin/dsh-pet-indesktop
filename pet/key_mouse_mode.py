# -*- coding: utf-8 -*-
"""键鼠跟随模式：复用 BongoCat 运行时的模式状态机（Windows）。

模式语义（与经典桌宠完全独立，只通过模式切换互通）：

* ``classic``：现状桌宠。窗口可见性、动画解码、物理与检测服务全部照旧。
* ``key_mouse``：隐藏并深度暂停全部桌宠窗口（复用 ``PetWindow.hide(notify=False)``
  已有的「不可见即零消耗」语义），改为拉起 BongoCat（Tauri2 + Live2D）承接
  全局键鼠跟随；BongoCat 进程一旦退出（用户点「切回原桌宠」、被任务管理器
  杀掉或崩溃）即自动恢复原桌宠。

模块边界：本模块持有模式权威与子进程生命周期，不认识 ``PetWindow`` 内部状态，
只使用窗口的公开动作 ``hide(notify=False)`` / ``show()``；GUI 之外的一切（运行时
定位、可写副本、素材覆盖层、配置持久化）都在这里收口，便于单测。

运行时定位顺序：``DSH_PET_BONGOCAT_DIR`` 环境变量 → 内置模板
``external/bongocat/`` → 用户已安装的 BongoCat 目录。安装目录可能不可写，
因此首次使用时把模板整体复制到 ``<数据目录>/bongocat/runtime/``，
之后只运行副本；素材替换改 ``<数据目录>/bongocat/models/<model>/`` 即可。
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import QObject, QProcess, Signal


log = logging.getLogger("dsh-pet-standalone")

MODE_CLASSIC = "classic"
MODE_KEY_MOUSE = "key_mouse"
VALID_MODES = (MODE_CLASSIC, MODE_KEY_MOUSE)

# 主配置键：模式状态是进程级偏好（多开时以主桌宠为准）。
PET_MODE_KEY = "pet_mode"

BONGOCAT_EXE = "BongoCat.exe"
RUNTIME_MARKER = ".runtime-ok"
MODEL_NAMES = ("standard", "keyboard", "gamepad")

# 启动/停止子进程的等待上限：仅在程序无法启动或退出无响应时才会等满。
START_TIMEOUT_MS = 5000
TERMINATE_TIMEOUT_MS = 3000
KILL_TIMEOUT_MS = 1000


def normalize_mode(value) -> str:
    """把配置/菜单传入的模式名归一化为合法值，非法值回落到 ``classic``。"""
    text = str(value or "").strip().lower()
    return text if text in VALID_MODES else MODE_CLASSIC


# ----------------------------------------------------------------- 运行时定位


def is_runtime_dir(path) -> bool:
    """判断目录是否为可直接启动的 BongoCat 运行时（含可执行文件）。"""
    try:
        return (Path(path) / BONGOCAT_EXE).is_file()
    except OSError:
        return False


def builtin_runtime_candidates(root=None) -> list[Path]:
    """内置模板候选：打包产物 exe 同级目录，或源码仓库根目录。"""
    bases: list[Path] = []
    if root is not None:
        bases.append(Path(root))
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    else:
        bases.append(Path(__file__).resolve().parent.parent)
    return [base / "external" / "bongocat" for base in bases]


def installed_runtime_candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """用户自行安装的 BongoCat 常见目录（Windows）。"""
    if sys.platform != "win32":
        return []
    env = os.environ if env is None else env
    candidates: list[Path] = []
    local = str(env.get("LOCALAPPDATA") or "").strip()
    if local:
        candidates.append(Path(local) / "Programs" / "BongoCat")
        candidates.append(Path(local) / "BongoCat")
    program_files = str(env.get("ProgramFiles") or "").strip()
    if program_files:
        candidates.append(Path(program_files) / "BongoCat")
    return candidates


def resolve_runtime_source(
    *,
    root=None,
    env: Mapping[str, str] | None = None,
    builtin_candidates: Iterable[Path] | None = None,
    installed_candidates: Iterable[Path] | None = None,
) -> Path | None:
    """按 环境变量 → 内置模板 → 已安装目录 的顺序返回可用运行时目录。"""
    env = os.environ if env is None else env
    override = str(env.get("DSH_PET_BONGOCAT_DIR") or "").strip()
    if override:
        candidate = Path(override)
        if is_runtime_dir(candidate):
            return candidate
        log.warning("[MODE] DSH_PET_BONGOCAT_DIR 无效（缺 %s）：%s", BONGOCAT_EXE, override)
    builtin = (
        list(builtin_candidates) if builtin_candidates is not None
        else builtin_runtime_candidates(root)
    )
    installed = (
        list(installed_candidates) if installed_candidates is not None
        else installed_runtime_candidates(env)
    )
    for candidate in list(builtin) + list(installed):
        if is_runtime_dir(candidate):
            return candidate
    return None


@dataclass(frozen=True)
class RuntimePaths:
    """数据目录下的键鼠跟随运行时布局。"""

    runtime: Path
    overlay: Path


def runtime_paths(config_dir) -> RuntimePaths:
    base = Path(config_dir) / "bongocat"
    return RuntimePaths(runtime=base / "runtime", overlay=base / "models")


def _runtime_fingerprint(source: Path) -> str:
    try:
        stat = (Path(source) / BONGOCAT_EXE).stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def ensure_runtime_copy(source, runtime_dir) -> bool:
    """把模板目录同步到可写副本（安装目录可能不可写）；返回副本是否可用。

    以 ``BongoCat.exe`` 的大小+mtime 作为模板指纹，落在 ``.runtime-ok``
    标记里；指纹一致则直接复用，避免每次切换模式都整目录复制。
    """
    source = Path(source)
    runtime_dir = Path(runtime_dir)
    fingerprint = _runtime_fingerprint(source)
    if not fingerprint:
        log.warning("[MODE] 运行时模板缺 %s：%s", BONGOCAT_EXE, source)
        return False
    marker = runtime_dir / RUNTIME_MARKER
    if (runtime_dir / BONGOCAT_EXE).is_file() and marker.is_file():
        try:
            if marker.read_text(encoding="utf-8").strip() == fingerprint:
                return True
        except OSError:
            pass
    staging = runtime_dir.with_name(runtime_dir.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging)
    except OSError as exc:
        log.warning("[MODE] 复制键鼠跟随运行时失败: %s", exc)
        shutil.rmtree(staging, ignore_errors=True)
        return False
    shutil.rmtree(runtime_dir, ignore_errors=True)
    try:
        staging.replace(runtime_dir)
    except OSError as exc:
        log.warning("[MODE] 启用键鼠跟随运行时副本失败: %s", exc)
        shutil.rmtree(staging, ignore_errors=True)
        return False
    try:
        marker.write_text(fingerprint, encoding="utf-8")
    except OSError as exc:
        log.warning("[MODE] 写入运行时标记失败: %s", exc)
    return True


def sync_asset_overrides(overlay_dir, runtime_dir) -> list[str]:
    """把素材覆盖层按相对路径覆盖进运行时副本，返回覆盖的相对路径列表。

    只接受 ``standard`` / ``keyboard`` / ``gamepad`` 三个模型目录，避免用户
    覆盖层把任意路径写进运行时目录；缺件不是错误——没放素材就用运行时自带素材。
    """
    overlay = Path(overlay_dir)
    if not overlay.is_dir():
        return []
    target_root = Path(runtime_dir) / "assets" / "models"
    copied: list[str] = []
    for model in MODEL_NAMES:
        model_dir = overlay / model
        if not model_dir.is_dir():
            continue
        for path in sorted(model_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(overlay)
            target = target_root / relative
            try:
                if target.is_file() and target.read_bytes() == path.read_bytes():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            except OSError as exc:
                log.warning("[MODE] 覆盖素材失败 %s: %s", relative, exc)
                continue
            copied.append(relative.as_posix())
    return copied


# --------------------------------------------------------------- 子进程包装


class KeyMouseProcess(QObject):
    """BongoCat 子进程包装：启动失败同步返回，退出经 ``finished`` 通知。"""

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
            "[MODE] 键鼠跟随运行时启动失败: %s（%s）",
            self._command[0],
            self.qt_process.errorString(),
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


def _default_process_factory(command, cwd, parent=None) -> KeyMouseProcess:
    return KeyMouseProcess(command, cwd, parent)


# ------------------------------------------------------------------- 控制器


class KeyMouseModeController(QObject):
    """模式状态机：谁在跑、原桌宠是否暂停、模式是否落盘。"""

    state_changed = Signal(str)
    notice = Signal(str, str)

    def __init__(
        self,
        shell,
        *,
        config,
        config_dir,
        command: Iterable[str] | None = None,
        runtime_source=None,
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
        self._config_dir = Path(config_dir)
        self._command_override = [str(item) for item in command] if command else None
        self._runtime_source_override = (
            Path(runtime_source) if runtime_source is not None else None
        )
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

    def runtime_available(self) -> bool:
        if self._command_override is not None:
            return True
        return self.runtime_source() is not None

    def runtime_source(self) -> Path | None:
        if self._runtime_source_override is not None:
            return (
                self._runtime_source_override
                if is_runtime_dir(self._runtime_source_override)
                else None
            )
        return resolve_runtime_source(env=self._env)

    # --------------------------------------------------------------- 生命周期
    def start_for_saved_mode(self) -> bool:
        """启动时按持久化模式接管：可用则直接进入，不可用则回落并提示。"""
        saved = normalize_mode(self._config.get(PET_MODE_KEY) if self._config else None)
        if saved != MODE_KEY_MOUSE:
            self._set_mode(MODE_CLASSIC)
            return False
        if not self.runtime_available():
            self._set_mode(MODE_CLASSIC)
            self.notice.emit(
                "键鼠跟随模式",
                "未找到键鼠跟随运行时，已回到经典桌宠模式。",
            )
            return False
        return self.enter()

    def enter(self) -> bool:
        """进入键鼠跟随模式；失败时保持在经典模式并给出提示。"""
        if self._closing:
            return False
        if self._mode == MODE_KEY_MOUSE:
            return True
        launch = self._launch_spec()
        if launch is None:
            self.notice.emit(
                "键鼠跟随模式",
                "未找到键鼠跟随运行时（BongoCat）；可重装或设置 DSH_PET_BONGOCAT_DIR 后重试。",
            )
            return False
        command, cwd = launch
        self._restore_visible = self._visible_instances()
        self._pause_windows()
        process = self._process_factory(command, cwd, self)
        self._process = process
        process.finished.connect(self._on_process_finished)
        if not process.start():
            self._release_process()
            self._resume_windows()
            self.notice.emit("键鼠跟随模式", "键鼠跟随运行时启动失败，已保持经典桌宠模式。")
            return False
        self._set_mode(MODE_KEY_MOUSE)
        self._warn_multi_process()
        return True

    def exit_mode(self) -> bool:
        """退出键鼠跟随模式并恢复原桌宠；已处于经典模式时返回 False。"""
        if self._mode != MODE_KEY_MOUSE and self._process is None:
            return False
        process = self._process
        self._process = None
        if process is not None:
            self._release_process(process)
            process.stop()
        self._resume_windows()
        self._set_mode(MODE_CLASSIC)
        return True

    def shutdown(self) -> None:
        """应用退出/会话结束：终止子进程并禁止再进入模式。"""
        self._closing = True
        process, self._process = self._process, None
        if process is not None:
            self._release_process(process)
            try:
                process.stop()
            except RuntimeError:
                pass
        if self._mode == MODE_KEY_MOUSE:
            self._restore_visible = []
            self._set_mode(MODE_CLASSIC)

    # ------------------------------------------------------------ 内部实现
    def _launch_spec(self) -> tuple[list[str], str | None] | None:
        if self._command_override is not None:
            return list(self._command_override), None
        source = self.runtime_source()
        if source is None:
            return None
        paths = runtime_paths(self._config_dir)
        runtime = paths.runtime
        try:
            same_dir = source.resolve() == runtime.resolve()
        except OSError:
            same_dir = False
        if not same_dir and not ensure_runtime_copy(source, runtime):
            return None
        sync_asset_overrides(paths.overlay, runtime)
        return [str(runtime / BONGOCAT_EXE)], str(runtime)

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

    def _warn_multi_process(self) -> None:
        if len(self._instances()) <= 1:
            return
        if bool(getattr(self._shell, "single_process_spawn", False)):
            return
        self.notice.emit(
            "键鼠跟随模式",
            "已暂停主桌宠；多进程多开下的子肥鱼会继续运行（开启单进程多开时可全部一起切）。",
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
        """子进程自行退出（含用户点「切回原桌宠」）：恢复原桌宠。"""
        if self._mode != MODE_KEY_MOUSE:
            return
        process, self._process = self._process, None
        self._release_process(process)
        self._resume_windows()
        self._set_mode(MODE_CLASSIC)
        self.notice.emit("键鼠跟随模式", "键鼠跟随已退出，已恢复经典桌宠。")

    def _set_mode(self, mode: str) -> None:
        mode = normalize_mode(mode)
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
