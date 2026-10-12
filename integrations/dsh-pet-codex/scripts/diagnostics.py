"""Local, bounded hook diagnostics and a fail-open worker supervisor (stdlib only)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

MAX_LOG_BYTES = 128 * 1024
MAX_INPUT_BYTES = 1024 * 1024
WORKER_TIMEOUT = 1.0
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
          'PermissionRequest', 'Stop', 'Interrupt', 'SessionEnd'}


def diagnostic(stage, exc=None, *, event='', code=None):
    """No exception messages, payloads, transcripts, commands or raw identifiers."""
    try:
        item = {'time': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'pid': os.getpid()}
        if event in EVENTS:
            item['event'] = event
        if exc is not None:
            item['error'] = type(exc).__name__
            if isinstance(getattr(exc, 'errno', None), int):
                item['errno'] = exc.errno
            frames = traceback.extract_tb(exc.__traceback__)
            if frames:
                item['location'] = f'{Path(frames[-1].filename).name}:{frames[-1].lineno}'
        if code is not None:
            item['exit_code'] = code
        if '--bridge-version' in sys.argv:
            version = sys.argv[sys.argv.index('--bridge-version') + 1]
            if len(version) == 16 and all(c in '0123456789abcdef' for c in version):
                item['version'] = version
        raw = (json.dumps(item, ensure_ascii=True) + '\n').encode()
        home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        cache = Path(os.environ.get('LOCALAPPDATA' if os.name == 'nt' else 'XDG_CACHE_HOME') or Path.home() / '.cache')
        for root in (home / 'dsh-pet-bridge', cache / 'dsh-pet'):
            try:
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                with (root / 'hooks.log.lock').open('a+b') as lock:
                    if os.name == 'nt':
                        import msvcrt
                        if lock.tell() == 0:
                            lock.write(b'0')
                            lock.flush()
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    path = root / 'hooks.log'
                    if path.exists() and path.stat().st_size + len(raw) > MAX_LOG_BYTES:
                        os.replace(path, root / 'hooks.log.1')
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    with os.fdopen(fd, 'ab') as stream:
                        stream.write(raw)
                return
            except (OSError, ValueError):
                continue
    except BaseException:
        pass  # Logging itself must never affect the host.


def main(command=None):
    """Only the supervisor acknowledges the hook; child output never reaches Codex."""
    phase = 'read_input'
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        oversized = len(raw) > MAX_INPUT_BYTES
        if oversized:
            while sys.stdin.buffer.read(65536):
                pass
        phase = 'enabled_check'
        disabled = ('--bridge-enabled-file' in sys.argv and not
                    Path(sys.argv[sys.argv.index('--bridge-enabled-file') + 1]).is_file())
        if disabled:
            pass
        elif oversized:
            diagnostic('input_too_large')
        else:
            phase = 'worker_start'
            if command is None:
                index = sys.argv.index('--bridge-script')
                command = [sys.executable, sys.argv[index + 1], *sys.argv[index + 2:]]
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(command, input=raw, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=WORKER_TIMEOUT, **options)
            if result.returncode:
                diagnostic('worker_exit', code=result.returncode)
    except subprocess.TimeoutExpired as exc:
        diagnostic('worker_timeout', exc)
    except BaseException as exc:
        diagnostic(phase, exc)
    try:
        sys.stdout.write('{}\n')
        sys.stdout.flush()
    except BaseException:
        pass
    return 0


if __name__ == '__main__' and '__file__' in globals():
    raise SystemExit(main([sys.executable, str(Path(__file__).with_name('bridge.py')), *sys.argv[1:]]))
