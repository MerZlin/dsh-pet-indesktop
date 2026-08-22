# -*- coding: utf-8 -*-
"""将 assets/characters 下的 WebM 角色动画同步为透明 GIF。

保持相同的子目录结构：
    assets/characters/<角色ID>/videos/**/*.webm
      -> assets/characters_gif/<角色ID>/videos/**/*.gif

默认只转换缺失或过期的 GIF；使用 --force 可覆盖已有 GIF，使用 --clean
可删除 GIF 目录中已经不存在对应 WebM 的旧文件。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "characters"
DST = ROOT / "assets" / "characters_gif"

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as exc:  # pragma: no cover - 环境依赖错误
    print(f"无法获取 ffmpeg: {exc}")
    sys.exit(1)


def convert_one(src: Path, dst: Path, *, force: bool = False) -> bool:
    if (
        not force
        and dst.exists()
        and dst.stat().st_size > 0
        and dst.stat().st_mtime >= src.stat().st_mtime
    ):
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    # 关键：输入端指定 libvpx-vp9；palettegen/paletteuse 保留透明区域并改善 GIF 色阶。
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-c:v",
        "libvpx-vp9",
        "-i",
        str(src),
        "-vf",
        "fps=24,split[s0][s1];"
        "[s0]palettegen=reserve_transparent=1:stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=sierra2_4a:alpha_threshold=128",
        "-loop",
        "0",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(
            f"失败: {src.name}: "
            f"{result.stderr.decode('utf-8', 'replace')[:400]}",
            flush=True,
        )
        return False
    return dst.exists() and dst.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 WebM 动画为 GIF 素材")
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有 GIF，确保使用当前 WebM",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="删除 GIF 目录中已不存在对应 WebM 的旧文件",
    )
    args = parser.parse_args()

    if not SRC.is_dir():
        print(f"未找到 {SRC}")
        return 1
    webm_files = sorted(SRC.rglob("*.webm"))
    if not webm_files:
        print("没有 WebM 文件")
        return 1

    expected = {
        src.relative_to(SRC).with_suffix(".gif").as_posix() for src in webm_files
    }
    if args.clean and DST.is_dir():
        stale = [
            path
            for path in DST.rglob("*.gif")
            if path.relative_to(DST).as_posix() not in expected
        ]
        for path in stale:
            path.unlink()
        for directory in sorted(
            (path for path in DST.rglob("*") if path.is_dir()),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
        if stale:
            print(f"清理旧 GIF：{len(stale)} 个", flush=True)

    ok = 0
    fail = 0
    for src in webm_files:
        rel = src.relative_to(SRC)
        dst = DST / rel.with_suffix(".gif")
        if convert_one(src, dst, force=args.force):
            ok += 1
        else:
            fail += 1
        print(f"[{ok + fail}/{len(webm_files)}] {rel}", flush=True)

    print(f"完成：成功 {ok}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())