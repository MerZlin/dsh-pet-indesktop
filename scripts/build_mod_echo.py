"""Freeze the offline v1 Echo sample from a small, closed source snapshot."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pet.feature_state_io import safe_path
from scripts.build_screen_worker import EXCLUDED_MODULES

SOURCES = (
    'pet/__init__.py', 'pet/mod_api/__init__.py', 'pet/mod_api/worker_v1.py',
    'pet/workers/__init__.py', 'pet/workers/lease_bootstrap.py', 'pet/workers/protocol.py',
    'pet/feature_version_lease.py', 'pet/feature_install_state.py', 'pet/feature_state_io.py',
    'pet/feature_startup_contract.py', 'pet/official_features.py', 'pet/frozen_runtime_paths.py',
)


def build(root: Path, output: Path, *, probe_bootloader: Path | None = None, native_extension: Path | None = None) -> Path:
    root, output = root.resolve(strict=True), output.absolute()
    safe_path(output)
    if output.exists():
        raise ValueError('choose a new output directory')
    if sys.platform == 'win32':
        if probe_bootloader is None or native_extension is None:
            raise ValueError('Windows Worker requires the Core probe native build inputs')
        from scripts.build_feature_release import verify_native_inputs
        verify_native_inputs(probe_bootloader, native_extension)
    source = output / 'source'
    for name in SOURCES:
        path = root / name
        safe_path(path)
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    (source / 'entry.py').write_bytes((root / 'examples/mods/echo-worker/worker-src/entry.py').read_bytes())
    command = [sys.executable, '-m', 'PyInstaller', '--onedir', '--console', '--noupx', '--name', 'mod-echo',
               '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'), '--specpath', str(output / 'spec')]
    for module in EXCLUDED_MODULES:
        command += ['--exclude-module', module]
    if sys.platform == 'win32':
        (source / '_dsh_probe_native.pyd').write_bytes(native_extension.read_bytes())
        command += ['--hidden-import', '_dsh_probe_native']
    command.append(str(source / 'entry.py'))
    with (output / 'build.log').open('xb') as log:
        subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1200)
    from PyInstaller.archive.readers import CArchiveReader
    bundle = output / 'dist/mod-echo'
    executable = bundle / ('mod-echo.exe' if sys.platform == 'win32' else 'mod-echo')
    if sys.platform == 'win32':
        from scripts.build_feature_probe import finalize_owned_headless_runtime
        finalize_owned_headless_runtime(bundle, executable, probe_bootloader)
    archive = CArchiveReader(str(executable))
    pyz = next(name for name, item in archive.toc.items() if item[-1] == 'z')
    modules = sorted(archive.open_embedded_archive(pyz).toc)
    if 'pet.mod_api.worker_v1' not in modules or any(name == banned or name.startswith(banned + '.') for name in modules for banned in EXCLUDED_MODULES):
        raise ValueError('worker module boundary failed')
    (output / 'modules.json').write_text(json.dumps(modules, indent=2), encoding='utf-8')
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--probe-bootloader', type=Path)
    parser.add_argument('--native-extension', type=Path)
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output, probe_bootloader=args.probe_bootloader, native_extension=args.native_extension))


if __name__ == '__main__':
    main()
