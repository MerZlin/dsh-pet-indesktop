"""Desktop-owned local plugin installation through the Codex CLI."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

PLUGIN = 'dsh-pet-codex'
MARKETPLACE = 'dsh-pet-local'
OWNER = '.dsh-pet-owned-v1'
ENABLED = '.hooks-enabled'
RETIRED_HOOK = ('# Retired desktop-pet hook; old sessions may still call this path.\n'
                'import sys\nwhile sys.stdin.buffer.read(65536):\n    pass\nprint("{}")\n')


def codex_home() -> Path:
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()


def state_path() -> Path:
    override = os.environ.get('DSH_PET_BRIDGE_DIR')
    return (Path(override).expanduser() if override else codex_home() / 'dsh-pet-bridge') / 'state.json'


def plugin_source() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    source = root / 'integrations' / PLUGIN
    if not (source / '.codex-plugin/plugin.json').is_file():
        raise FileNotFoundError('桌宠安装目录缺少联动插件，请重新安装桌宠。')
    return source


def find_codex() -> str:
    executable = 'codex.exe' if sys.platform == 'win32' else 'codex'
    candidates = [shutil.which('codex')]
    candidates += [str(codex_home() / 'packages' / name / 'current/bin' / executable)
                   for name in ('standalone', 'app-server-daemon')]
    candidates += ['/usr/lib/chatgpt/resources/codex',
                   '/Applications/Codex.app/Contents/Resources/codex',
                   '/Applications/ChatGPT.app/Contents/Resources/codex']
    for base in (Path.home() / '.vscode/extensions', Path.home() / '.vscode-insiders/extensions'):
        for extension in sorted(base.glob('openai.chatgpt-*'), reverse=True):
            candidates.extend(str(path) for path in extension.glob(f'bin/*/{executable}'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).absolute())
    raise FileNotFoundError('未找到 Codex，请先安装 Codex CLI、App 或 VS Code 扩展。')


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pet-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stage_plugin(root: Path, *, source: Path | None = None) -> Path:
    """Only update a directory owned by this installer, never a personal catalog."""
    root = Path(root)
    if root.exists() and not (root / OWNER).is_file() and any(root.iterdir()):
        raise ValueError('插件目录已有其他文件，安装已停止，未覆盖已有配置。')
    source = source or plugin_source()
    root.mkdir(parents=True, exist_ok=True)
    (root / OWNER).touch()
    target = root / 'plugins' / PLUGIN
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    manifest_path = target / '.codex-plugin/plugin.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    hooks_path = target / 'hooks/hooks.json'
    hooks = json.loads(hooks_path.read_text(encoding='utf-8'))
    guard = (target / 'scripts/diagnostics.py').read_bytes()
    bootstrap = 'import base64;exec(base64.b64decode(' + repr(base64.b64encode(
        guard + b'\nraise SystemExit(main())\n')) + '))'
    digest = hashlib.sha256((target / 'scripts/bridge.py').read_bytes() + bootstrap.encode() +
                            hooks_path.read_bytes() + sys.executable.encode() +
                            str(bool(getattr(sys, 'frozen', False))).encode()).hexdigest()[:16]
    # Codex owns and can remove its cache while another client still uses a
    # loaded hook definition. Keep immutable runtime versions in our directory.
    runtime = root / 'runtime' / digest / 'bridge.py'
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if not runtime.exists():
        fd, temporary = tempfile.mkstemp(prefix='.bridge-', dir=runtime.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write((target / 'scripts/bridge.py').read_bytes())
            os.replace(temporary, runtime)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif runtime.read_bytes() != (target / 'scripts/bridge.py').read_bytes():
        raise ValueError('联动脚本版本内容不一致，安装已停止。')
    guard_path = runtime.with_name('diagnostics.py')
    if not guard_path.exists():
        guard_path.write_bytes(guard)
    elif guard_path.read_bytes() != guard:
        raise ValueError('联动诊断脚本版本内容不一致，安装已停止。')
    arguments = ['--bridge-version', digest, '--bridge-enabled-file', str((root / ENABLED).absolute())]
    if getattr(sys, 'frozen', False):
        argv = [sys.executable, '--agent-hook', *arguments]
    else:
        # Inline the small stdlib supervisor so a missing runtime script is a
        # logged, successful no-op rather than Python's blocking exit code 2.
        argv = [sys.executable, '-c', bootstrap, '--bridge-script', str(runtime.absolute()), *arguments]
    command = shlex.join(argv)
    windows = subprocess.list2cmdline(argv)
    for groups in hooks['hooks'].values():
        for group in groups:
            for handler in group['hooks']:
                handler.update(command=command, commandWindows=windows)
    manifest['version'] = manifest['version'].split('+')[0] + '+codex.' + digest
    _write_json(manifest_path, manifest)
    _write_json(hooks_path, hooks)
    _write_json(root / '.agents/plugins/marketplace.json', {
        'name': MARKETPLACE, 'interface': {'displayName': 'Desktop Pet'},
        'plugins': [{'name': PLUGIN, 'source': {'source': 'local', 'path': './plugins/' + PLUGIN},
                     'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
                     'category': 'Productivity'}]})
    return root


def _legacy_hook_paths(home):
    """Remember pre-runtime hook paths before the CLI removes their cache."""
    cache = home / 'plugins/cache' / MARKETPLACE / PLUGIN
    paths = [path.parent.parent / 'scripts/bridge.py' for path in cache.glob('*/hooks/hooks.json')
             if '${PLUGIN_ROOT}/scripts/bridge.py' in path.read_text(encoding='utf-8')]
    # Preserve earlier tombstones across a subsequent install/remove cycle too.
    paths.extend(path for path in cache.glob('*/scripts/bridge.py')
                 if path.read_text(encoding='utf-8').startswith('# Retired desktop-pet hook'))
    return set(paths)


def _retire_missing_hooks(paths):
    # No plugin manifest/registration is restored: these entries only silence
    # already-loaded legacy hooks, and never resume telemetry after uninstall.
    for path in paths:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('x', encoding='utf-8') as stream:
                    stream.write(RETIRED_HOOK)
            except FileExistsError:
                pass


def run_command(argv, *, home, cwd, cancel=None, timeout=60):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise InterruptedError('安装已取消。')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    child = subprocess.Popen(argv, cwd=cwd, env={**os.environ, 'CODEX_HOME': str(home)},
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel.is_set():
                raise InterruptedError('安装已取消。')
            if time.monotonic() >= deadline:
                raise TimeoutError('Codex 安装命令超时，请检查 Codex 状态后重试。')
            try:
                out, _ = child.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if child.returncode:
            raise RuntimeError(f'Codex 命令失败（退出码 {child.returncode}），请更新或检查 Codex 后重试。')
        return out.decode('utf-8', errors='replace')
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def install_bridge(*, home=None, executable=None, cancel=None, run=None):
    home = Path(home) if home else codex_home()
    run = run or run_command
    try:
        if cancel is not None and cancel.is_set():
            raise InterruptedError('安装已取消。')
        executable = executable or find_codex()
        home.mkdir(parents=True, exist_ok=True)
        # QLockFile owns no event-loop resources; constructed and released in this worker.
        from PySide6.QtCore import QLockFile
        lock = QLockFile(str(home / 'dsh-pet-install.lock'))
        if not lock.tryLock(0):
            raise RuntimeError('另一只桌宠正在安装插件，请稍后重试。')
        legacy_paths = []
        try:
            legacy_paths = _legacy_hook_paths(home)
            root = stage_plugin(home / 'dsh-pet-marketplace')
            prefix = [executable, '--no-daemon', 'plugin']
            run(prefix + ['marketplace', 'add', str(root), '--json'], home=home, cwd=root, cancel=cancel)
            run(prefix + ['add', f'{PLUGIN}@{MARKETPLACE}', '--json'], home=home, cwd=root, cancel=cancel)
            cache = home / 'plugins/cache' / MARKETPLACE / PLUGIN
            expected = (root / 'plugins' / PLUGIN / 'hooks/hooks.json').read_bytes()
            writer = (root / 'plugins' / PLUGIN / 'scripts/bridge.py').read_bytes()
            guard = (root / 'plugins' / PLUGIN / 'scripts/diagnostics.py').read_bytes()
            if not any(path.read_bytes() == expected and
                       (path.parent.parent / 'scripts/bridge.py').read_bytes() == writer and
                       (path.parent.parent / 'scripts/diagnostics.py').read_bytes() == guard
                       for path in cache.glob('*/hooks/hooks.json')):
                raise RuntimeError('Codex 未生成插件安装文件，请检查 Codex 后重试。')
            (root / ENABLED).touch()
        finally:
            try:
                _retire_missing_hooks(legacy_paths)
            finally:
                lock.unlock()
        return True, 'Codex 插件已安装。请在 Codex 中审阅并信任 hooks，然后开始新会话。'
    except Exception as exc:
        return False, str(exc)


def remove_bridge(*, home=None, executable=None, cancel=None, run=None):
    home = Path(home) if home else codex_home()
    try:
        from PySide6.QtCore import QLockFile
        lock = QLockFile(str(home / 'dsh-pet-install.lock'))
        if not lock.tryLock(0):
            raise RuntimeError('另一只桌宠正在修改插件，请稍后重试。')
        legacy_paths = []
        try:
            legacy_paths = _legacy_hook_paths(home)
            root = home / 'dsh-pet-marketplace'
            if (root / OWNER).is_file():
                (root / ENABLED).unlink(missing_ok=True)
            (run or run_command)([executable or find_codex(), '--no-daemon', 'plugin', 'remove',
                                  f'{PLUGIN}@{MARKETPLACE}', '--json'], home=home, cwd=home, cancel=cancel)
        finally:
            try:
                _retire_missing_hooks(legacy_paths)
            finally:
                lock.unlock()
        return True, 'Codex 联动已关闭，桌宠安装的插件已卸载。'
    except Exception as exc:
        return False, f'联动已关闭，但插件卸载失败：{exc}'
