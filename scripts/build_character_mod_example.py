"""Copy the bundled character into an independent, visibly changed MOD sample."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from pet.content.hashing import content_sha256
from pet.content.manifest import validate_package_root
from pet.feature_state_io import safe_path


def build(output: Path, *, root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    source = root / 'content/characters/shenshen'
    output = output.absolute()
    safe_path(output)
    if output.exists() or output.with_suffix('.zip').exists():
        raise ValueError('choose a new output directory; existing files will not be overwritten')
    for path in sorted(source.rglob('*')):
        safe_path(path)
        if path.is_file():
            target = output / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    manifest = json.loads((output / 'manifest.json').read_text('utf-8'))
    manifest.update(id='mod-demo-shenshen', name='深深（MOD 示例）', version='1.0.0',
                    description='离线角色副本：待机动作替换为元气挥手；不会修改内置深深。', core_requires='>=4.2.5,<6.0.0')
    manifest['content']['characters'] = ['mod-demo-shenshen']
    # Only overwrite a file just created inside this exclusive output directory.
    (output / 'videos/idle/待机呼吸休闲.webm').write_bytes((source / 'videos/click/点击回应-元气挥手.webm').read_bytes())
    manifest['integrity'] = {'sha256': None, 'signature': None}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', 'utf-8')
    manifest['integrity']['sha256'] = content_sha256(output)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', 'utf-8')
    _, errors, _ = validate_package_root(output, core_version='4.2.5', platform_name='windows', allow_unsigned=True)
    if errors:
        raise ValueError('; '.join(errors))
    with zipfile.ZipFile(output.with_suffix('.zip'), 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(build(args.output))


if __name__ == '__main__':
    main()
