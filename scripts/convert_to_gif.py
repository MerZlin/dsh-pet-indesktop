# -*- coding: utf-8 -*-
"""将 assets/characters 下的 webm 角色动画转换为 assets/characters_gif 下的 GIF。

保持相同的子目录结构：
    assets/characters/<角色ID>/videos/**/*.webm
      -> assets/characters_gif/<角色ID>/videos/**/*.gif
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'assets' / 'characters'
DST = ROOT / 'assets' / 'characters_gif'

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as exc:
    print(f'无法获取 ffmpeg: {exc}')
    sys.exit(1)


def convert_one(src: Path, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_size > 0:
        return True  # 已存在则跳过
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 关键：解码端必须指定 libvpx-vp9，否则会丢失 alpha
    cmd = [
        FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
        '-c:v', 'libvpx-vp9', '-i', str(src),
        '-vf', 'fps=24,split[s0][s1];'
               '[s0]palettegen=reserve_transparent=1:stats_mode=diff[p];'
               '[s1][p]paletteuse=alpha_threshold=128',
        '-loop', '0', str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f'失败: {src.name}: {r.stderr.decode("utf-8", "replace")[:200]}', flush=True)
        return False
    return True


def main() -> int:
    if not SRC.is_dir():
        print(f'未找到 {SRC}')
        return 1
    webm_files = sorted(SRC.rglob('*.webm'))
    if not webm_files:
        print('没有 webm 文件')
        return 1
    ok = 0
    fail = 0
    for src in webm_files:
        rel = src.relative_to(SRC)
        dst = DST / rel.with_suffix('.gif')
        if convert_one(src, dst):
            ok += 1
        else:
            fail += 1
        print(f'[{ok+fail}/{len(webm_files)}] {rel}', flush=True)
    print(f'完成：成功 {ok}，失败 {fail}')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
