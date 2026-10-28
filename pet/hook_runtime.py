"""Headless packaged hook entry: never import Qt or create a pet window."""
import os
from pathlib import Path
import runpy
import sys


def main():
    diagnostic = None
    try:
        # Windows windowed PyInstaller builds set Python streams to None even when
        # their parent supplies redirected handles. Reopen those supplied pipes.
        if sys.platform == 'win32' and (sys.stdin is None or sys.stdout is None):
            import ctypes
            from ctypes import wintypes
            import msvcrt
            get_handle = ctypes.windll.kernel32.GetStdHandle
            get_handle.argtypes = [wintypes.DWORD]
            get_handle.restype = wintypes.HANDLE
            for name, code, flags, mode in [('stdin', -10, os.O_RDONLY, 'r'), ('stdout', -11, os.O_WRONLY, 'w')]:
                if getattr(sys, name) is None:
                    handle = get_handle(code)
                    if handle in (None, 0, ctypes.c_void_p(-1).value):
                        return 0
                    fd = msvcrt.open_osfhandle(handle, flags)
                    setattr(sys, name, os.fdopen(fd, mode, encoding='utf-8'))
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
        scripts = root / 'integrations/dsh-pet-codex/scripts'
        guard = runpy.run_path(str(scripts / 'diagnostics.py'))
        diagnostic = guard['diagnostic']
        if '--bridge-worker' in sys.argv:
            runpy.run_path(str(scripts / 'bridge.py'), init_globals={'diagnostic': diagnostic}, run_name='__main__')
        else:
            prefix = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'pet']
            args = [arg for arg in sys.argv[1:] if arg != '--agent-hook']
            return guard['main']([*prefix, '--agent-hook', '--bridge-worker', *args])
    except SystemExit as exc:
        if exc.code and diagnostic:
            diagnostic('packaged_worker_exit', exc)
        if '--bridge-worker' not in sys.argv:
            try:
                print('{}')
                sys.stdout.flush()
            except BaseException:
                pass
    except BaseException as exc:
        if diagnostic:
            diagnostic('packaged_start', exc)
        try:
            if sys.stdout is not None:
                print('{}')
                sys.stdout.flush()
        except BaseException:
            pass
    return 0
