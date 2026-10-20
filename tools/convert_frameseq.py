# -*- coding: utf-8 -*-
"""帧序列转换器（帧序列化 B 档）——命令行薄壳。

转换核心全部在 ``pet/frameseq_provision.py``：运行时首跑自动供给与构建期
工具共用同一份实现（``<stem>.g<戳>.tmp/`` 原子发布、bgra + libvpx-vp9 两组
防坑参数、read_frames meta 探测 fps、按源身份戳幂等跳过、按目录分档编码）。
本模块只保留命令行行为：

    assets/characters/<id>/frameseq/<folder>/<stem>.g<源戳12>/f_0001.webp + meta.json

用法：
    python tools/convert_frameseq.py [--character shenshen] [--force]
                                     [--include random,events]
幂等：当前源已有**同档位**的可用世代目录（名字戳与 meta 戳自洽、帧数与磁盘相符、
encoder 串与本目录档位一致）即跳过；--force 全量重转。
**全部目录一档**（v6 起统一无损，见 ``frameseq_provision.ENCODER_DESC``）：不再有
"热集无损 / 冷集有损"的分档，``--include`` 只决定**转哪些目录**。默认范围仍是热集
（random/events 体积大，构建期是否转换由打包方决定；运行时自动供给的范围是
热集 + random + events，见 ``frameseq_provision.SUPPLY_FOLDERS``）。
本工具是**构建期入口，不设启动预算**（一轮转完范围内全部 webm）；运行时的首跑
自动供给按每次启动的预算分批、失败退避，见 ``frameseq_provision.provision_once``。
退出码：0 全部成功；1 角色目录缺失/无匹配 webm/有失败 clip（明细打到 stderr）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pet.frameseq_provision import (  # noqa: E402  (须先补 sys.path 再导入)
    HOT_FOLDERS,
    clip_out_dir,
    convert_clip,
    hot_webms,
    is_lossy_clip,
)

__all__ = ["HOT_FOLDERS", "REPO_ROOT", "clip_out_dir", "convert_clip",
           "hot_webms", "is_lossy_clip", "main"]


def main() -> int:
    parser = argparse.ArgumentParser(description="帧序列转换器（B 档）")
    parser.add_argument("--character", default="shenshen")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--include", default="",
                        help="逗号分隔的额外目录（默认只转热集 idle/move/turn/click/drag；"
                             "random/events 体积大，需要时显式列进来）")
    args = parser.parse_args()

    videos = REPO_ROOT / "assets" / "characters" / args.character / "videos"
    if not videos.is_dir():
        print(f"角色素材目录不存在: {videos}", file=sys.stderr)
        return 1
    folders = set(HOT_FOLDERS)
    folders.update(f for f in args.include.split(",") if f)

    webms = hot_webms(videos, folders)
    if not webms:
        print("没有匹配目录的 webm", file=sys.stderr)
        return 1

    out_root = videos.parent / "frameseq"
    done = skipped = failed = 0
    webm_bytes = webp_bytes = 0
    for webm in webms:
        rel = webm.relative_to(videos).with_suffix("")
        out_dir = clip_out_dir(webm, videos, out_root)
        converted, err = convert_clip(
            webm, out_dir, force=args.force,
            lossy=is_lossy_clip(webm, videos),   # 档位由目录决定（同运行时供给）
        )
        if err:
            failed += 1
            print(f"FAIL {rel}: {err}", file=sys.stderr)
            continue
        if converted:
            done += 1
        else:
            skipped += 1
        webm_bytes += webm.stat().st_size
        webp_bytes += sum(f.stat().st_size for f in out_dir.glob("f_*.webp"))

    print(f"clips: 新转 {done} / 跳过 {skipped} / 失败 {failed}")
    print(f"磁盘账: webm {webm_bytes / 1e6:.1f}MB → webp {webp_bytes / 1e6:.1f}MB"
          f"（{webp_bytes / max(1, webm_bytes):.1f}x）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
