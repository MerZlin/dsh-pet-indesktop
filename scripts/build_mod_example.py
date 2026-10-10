"""Build a user-trusted local MOD folder and ZIP (no publisher signing)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from pet import __version__
from pet.feature_build_policy import FEATURE_CAPABILITIES
from pet.feature_state_io import safe_path
from pet.plugins.package_trust import FeaturePackageVerifier


def build_example(source: Path, output: Path, *, worker_bundle: Path | None = None, worker_name: str = 'mod-echo.exe') -> Path:
    source, output = Path(source).resolve(strict=True), Path(output).absolute()
    safe_path(output)
    if output.exists() or output.with_suffix('.zip').exists():
        raise ValueError('output already exists; choose a new candidate directory')
    values = json.loads((source / 'mod.json').read_text('utf-8-sig'))
    kind = values['execution_kind']
    if kind == 'host-worker' and worker_bundle is None:
        raise ValueError('host-worker requires a separately frozen --worker-bundle')
    if kind not in {'host-only', 'host-worker'}:
        raise ValueError('unsupported execution kind')
    files = {}
    def copy_tree(root, prefix, *, python_only=False):
        root = Path(root).resolve(strict=True)
        for path in sorted(root.rglob('*')):
            safe_path(path)
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if python_only and path.suffix != '.py':
                continue
            relative = prefix + '/' + path.relative_to(root).as_posix()
            data = path.read_bytes()
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    copy_tree(source / 'host', 'host', python_only=True)
    if worker_bundle is not None:
        copy_tree(worker_bundle, 'worker')
    manifest = {**values, 'format_version': 2, 'key_id': 'local-user', 'api_version': '1',
                'core_requires': '>=4.2.5,<6.0.0', 'platforms': [sys.platform],
                'capabilities': ['menu.contribute', 'settings.contribute'],
                'worker': {'path': 'worker/' + worker_name, 'args': []} if kind == 'host-worker' else None, 'files': files}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', 'utf-8')
    check(output)
    with zipfile.ZipFile(output.with_suffix('.zip'), 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
    return output


def check(package):
    from pet.local_package_intents import route_local_package
    package = Path(package).expanduser().absolute()
    route = route_local_package(package)
    verifier = FeaturePackageVerifier(core_version=__version__, api_version='1', feature_id=route.feature_id,
                                     allowed_capabilities=FEATURE_CAPABILITIES, allow_local_packages=True)
    return verifier.verify(package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--worker-bundle', type=Path)
    parser.add_argument('--worker-name', default='mod-echo.exe')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        descriptor = check(args.source)
        print(f'valid: {descriptor.id} {descriptor.version} ({descriptor.execution_kind})')
    else:
        if args.output is None:
            parser.error('output is required unless --check is selected')
        result = build_example(args.source, args.output, worker_bundle=args.worker_bundle, worker_name=args.worker_name)
        print(f'created: {result}\nZIP: {result.with_suffix(".zip")}')


if __name__ == '__main__':
    main()
