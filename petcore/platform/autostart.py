from __future__ import annotations

import logging
import os
import plistlib
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("petcore.platform.autostart")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "dsh-pet-core"


@dataclass(frozen=True)
class LaunchContext:
    root: Path | None = None
    instance: str | None = None
    working_directory: Path | None = None


_launch_context: LaunchContext | None = None


def configure(*, root=None, instance=None, working_directory=None) -> None:
    global _launch_context
    _launch_context = LaunchContext(
        Path(root).absolute() if root is not None else None,
        instance,
        Path(working_directory).absolute() if working_directory is not None else None,
    )


def supported() -> bool:
    from .desktop import capabilities
    return capabilities()["autostart"].supported


def command_argv() -> list[str]:
    executable = Path(sys.executable)
    frozen = bool(getattr(sys, "frozen", False))
    if sys.platform == "win32" and not frozen:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    args = [str(executable)] + ([] if frozen else ["-m", "petcore"])
    if _launch_context is not None:
        if _launch_context.root is not None:
            args.extend(["--root", str(_launch_context.root)])
        if _launch_context.instance:
            args.extend(["--instance", _launch_context.instance])
    return args


def entry_path() -> Path:
    suffix = f"-{_launch_context.instance}" if _launch_context and _launch_context.instance else ""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"io.dsh.petcore{suffix}.plist"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"dsh-pet-core{suffix}.desktop"


def _desktop_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _desktop_arg(value: str) -> str:
    # Desktop Entry has two escaping passes: value escaping, then Exec quoting.
    escaped = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return _desktop_value('"' + escaped + '"')


def _posix_enable() -> bool:
    target = entry_path()
    cwd = (_launch_context.working_directory if _launch_context else None)
    if cwd is None:
        cwd = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    if sys.platform == "darwin":
        data = plistlib.dumps({
            "Label": target.stem, "ProgramArguments": command_argv(),
            "WorkingDirectory": str(cwd), "RunAtLoad": True, "ProcessType": "Interactive",
        })
    else:
        data = ("[Desktop Entry]\nType=Application\nName=dsh-pet core\n"
                "Exec=" + " ".join(_desktop_arg(arg) for arg in command_argv()) + "\n"
                "Path=" + _desktop_value(str(cwd)) + "\nTerminal=false\nX-GNOME-Autostart-enabled=true\n").encode("utf-8")
    temp = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, target)
        return True
    except OSError:
        logger.warning("write autostart failed", exc_info=True)
        return False
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def command_text() -> str:
    """开机自启命令行；打包后直接运行 exe，源码运行走 pythonw -m petcore。"""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.exists():
        executable = pythonw
    return f'"{executable}" -m petcore'


def _command() -> str:
    return command_text()


def _open_key(write: bool = False):
    import winreg

    access = winreg.KEY_SET_VALUE if write else winreg.KEY_READ
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, access)


def is_enabled() -> bool:
    if sys.platform in ("linux", "darwin"):
        try:
            data = entry_path().read_bytes()
            if sys.platform == "darwin":
                doc = plistlib.loads(data)
                return bool(doc.get("RunAtLoad") and doc.get("ProgramArguments"))
            text = data.decode("utf-8")
            return "Exec=" in text and "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
        except (OSError, ValueError, plistlib.InvalidFileException):
            return False
    if os.name != "nt":
        return False
    try:
        import winreg

        with _open_key() as key:
            value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError:
        logger.debug("read autostart failed", exc_info=True)
        return False


def enable() -> bool:
    if sys.platform in ("linux", "darwin"):
        return _posix_enable()
    if os.name != "nt":
        return False
    try:
        import winreg

        with _open_key(write=True) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command())
        return True
    except OSError:
        logger.debug("enable autostart failed", exc_info=True)
        return False


def disable() -> bool:
    if sys.platform in ("linux", "darwin"):
        try:
            entry_path().unlink(missing_ok=True)
            return True
        except OSError:
            logger.warning("remove autostart failed", exc_info=True)
            return False
    if os.name != "nt":
        return False
    try:
        import winreg

        with _open_key(write=True) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        logger.debug("disable autostart failed", exc_info=True)
        return False


def cleanup_stale_entries() -> int:
    if os.name != "nt" or not is_enabled():
        return 0
    return 0
