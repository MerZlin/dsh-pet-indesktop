# -*- coding: utf-8 -*-
"""
素材转码脚本 —— 把 51 个 640×360 透明 webm（VP9 alpha）转成 640×360 透明 GIF。

依赖：imageio-ffmpeg（pip install imageio-ffmpeg，自带静态 ffmpeg，无需系统安装）。
关键点：`-c:v libvpx-vp9` 必须放在 `-i` 前——native vp9 解码器会丢弃 alpha
（原项目 DESIGN.md 踩坑记录第 3 条，已实测复现）。

源：dsh-pet 仓库 assets/thumb/<中文名>.webm
输出：assets/animations/<拼音名>.gif（与 pet/catalog.py 的映射一致）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pet import catalog  # noqa: E402

# 默认源 webm 目录：把上游 dsh-pet 的 assets/thumb/*.webm 放到这里；可用 --src 覆盖
SRC = ROOT / 'assets' / 'videos'
DST = ROOT / 'assets' / 'animations'

# 透明 GIF 转码滤镜：保留 alpha（reserve_transparent）+ 抖动减色带
VF = (
    'fps=24,split[s0][s1];'
    '[s0]palettegen=max_colors=255:reserve_transparent=1:stats_mode=diff[p];'
    '[s1][p]paletteuse=dither=bayer:bayer_scale=5:alpha_threshold=128'
)


def _is_converted(dst: Path) -> bool:
    """判断输出 GIF 是否已是目标尺寸（避免误跳过旧的低清素材）。"""
    if not dst.exists() or dst.stat().st_size == 0:
        return False
    try:
        with Image.open(dst) as im:
            return im.size == (catalog.CANVAS_W, catalog.CANVAS_H)
    except Exception:
        return False


def convert() -> int:
    parser = argparse.ArgumentParser(description='转码 dsh-pet 透明 webm → 透明 GIF')
    parser.add_argument('--src', type=Path, default=SRC, help='源 webm 目录（默认 assets/videos）')
    parser.add_argument('--force', action='store_true', help='强制重新转码')
    args = parser.parse_args()

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    DST.mkdir(parents=True, exist_ok=True)

    total = len(catalog.ANIM_FILES)
    done = 0
    failed = 0
    for zh, pinyin in catalog.ANIM_FILES.items():
        src = args.src / f'{zh}.webm'
        dst = DST / pinyin
        if not src.exists():
            print(f'[跳过] 缺少源文件: {src.name}', flush=True)
            failed += 1
            continue
        # 幂等：已存在且是目标尺寸（640×360）才跳过；旧 220×124 需重转
        if not args.force and _is_converted(dst):
            done += 1
            continue
        cmd = [
            ffmpeg, '-hide_banner', '-y', '-loglevel', 'error',
            '-c:v', 'libvpx-vp9', '-i', str(src),
            '-vf', VF, '-loop', '0', str(dst),
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            print(f'[失败] {zh}: {r.stderr.decode("utf-8", "replace")[:200]}', flush=True)
            failed += 1
            continue
        done += 1
        kb = dst.stat().st_size // 1024
        print(f'[{done}/{total}] {pinyin}  ({kb} KB)', flush=True)

    print(f'完成：成功 {done}/{total}，失败 {failed}', flush=True)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(convert())
