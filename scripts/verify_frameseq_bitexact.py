# -*- coding: utf-8 -*-
"""全库位精确验证：磁盘上的帧序列解码后 == 生产端规范 RGBA。

**为什么需要它**：v6 起运行编码层是无损（``lossless=True, exact=True``），
「解码重建 == 参考 RGBA」应当是**构造上成立**的；但构造上的断言必须有机器证据，
否则一次误配（比如 ``exact`` 掉了、method 变了、别人把档位改回有损）就无声无息。
评审也明确要求：位精确的主张不能靠三个 clip 外推。

比对口径
--------
参考真值 = 源 webm 解码 → ``unblend_v3``（生产端同一条处理链，逐帧现算，不落盘）。
被测值   = 磁盘帧序列经 **Qt 解码**（运行时真实路径）得到的 RGBA。
判定     = 逐帧逐字节相等；帧数也必须相等。

用法
----
    python scripts/verify_frameseq_bitexact.py --limit 3        # 抽前 3 段
    python scripts/verify_frameseq_bitexact.py --clip 待机呼吸休闲
    python scripts/verify_frameseq_bitexact.py                 # 全库（约 1 小时）

退出码：0 = 全部位精确；1 = 有不匹配/缺帧/读不出（明细打到 stderr）。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from pet import frameseq_provision as fp  # noqa: E402

FRAME_BYTES = fp.CANVAS_BYTES if hasattr(fp, "CANVAS_BYTES") else 640 * 360 * 4


def _qt_rgba(path: Path) -> bytes:
    """按运行时路径解码一帧（Qt），返回 straight RGBA 字节。"""
    img = QImage(str(path))
    if img.isNull():
        raise RuntimeError(f"Qt 解不开: {path}")
    if img.format() != QImage.Format.Format_RGBA8888:
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    if img.width() * 4 != img.bytesPerLine():
        # 行跨度有填充：逐行拼（Qt 对某些尺寸会给 4 字节对齐的行宽）
        out = bytearray()
        for y in range(img.height()):
            start = y * img.bytesPerLine()
            out += bytes(img.constBits())[start:start + img.width() * 4]
        return bytes(out)
    return bytes(img.constBits())


def verify_clip(webm: Path, gen_dir: Path, exe: str) -> dict:
    meta = fp.read_meta(gen_dir)
    if meta is None:
        return {"clip": webm.stem, "ok": False, "error": "meta 读不出"}
    frames = int(meta.get("frames") or 0)
    on_disk = sorted(gen_dir.glob("f_*.webp"))
    if frames <= 0 or len(on_disk) != frames:
        return {"clip": webm.stem, "ok": False,
                "error": f"帧数不符: meta={frames} 磁盘={len(on_disk)}"}

    # **必须走生产端同一条阶段一**（``-pix_fmt bgra`` → 无损 webp），不能直取
    # rawvideo：两条路径的 YUV→RGB 取整不同，实测同一帧差 2572 字节，拿它当参考
    # 会把"管线没错"误判成"不位精确"。
    mid = Path(tempfile.mkdtemp(prefix="bitexact-"))
    argv = [exe, "-nostdin", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(webm),
            "-pix_fmt", "bgra", "-c:v", "libwebp", "-lossless", "1",
            str(mid / "f_%04d.webp")]
    subprocess.run(argv, check=True, capture_output=True)
    stage1 = sorted(mid.glob("f_*.webp"))
    if len(stage1) != frames:
        shutil.rmtree(mid, ignore_errors=True)
        return {"clip": webm.stem, "ok": False,
                "error": f"阶段一只解出 {len(stage1)} 帧（meta 说 {frames}）"}
    mismatch: list[int] = []
    max_delta = 0
    seen = 0
    t0 = time.perf_counter()
    try:
        for idx, path in enumerate(on_disk):
            ref = fp.unblend_v3(Image.open(stage1[idx]).convert("RGBA"))
            got = _qt_rgba(path)
            if got != ref.tobytes():
                mismatch.append(idx + 1)
                a = ref.tobytes()
                max_delta = max(max_delta,
                                max(abs(x - y) for x, y in zip(a[::97], got[::97])))
            seen += 1
    finally:
        shutil.rmtree(mid, ignore_errors=True)
    return {
        "clip": webm.stem,
        "ok": not mismatch,
        "frames": seen,
        "mismatch": mismatch[:10],
        "n_mismatch": len(mismatch),
        "sample_max_delta": max_delta,
        "encoder": meta.get("encoder"),
        "seconds": round(time.perf_counter() - t0, 1),
    }


def _report_one(r: dict, done: int, total: int) -> None:
    tag = f"[{done}/{total}]"
    if r["ok"]:
        print(f"  OK   {tag} {r['clip']:<22} {r['frames']:>4} 帧  "
              f"{r['seconds']:>5}s  encoder=…{str(r['encoder'])[-12:]}", flush=True)
    else:
        detail = r.get("error") or (
            f"不匹配 {r['n_mismatch']} 帧（前 10: {r['mismatch']}，"
            f"抽样最大差 {r['sample_max_delta']}）")
        print(f"  FAIL {tag} {r['clip']:<22} {detail}", file=sys.stderr, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="帧序列位精确验证")
    ap.add_argument("--character", default="shenshen")
    ap.add_argument("--limit", type=int, default=0, help="只验前 N 段（0=全部）")
    ap.add_argument("--clip", default="", help="只验名字含该子串的 clip")
    ap.add_argument("--jobs", type=int, default=1, help="并行验证的 clip 数（默认 1）")
    ap.add_argument("--v6-only", action="store_true",
                    help="只验当前档位（v6）的世代，跳过尚未重出的旧档")
    ap.add_argument("--partial", action="store_true",
                    help="抽样/部分模式：允许缺少世代目录，结论标 PARTIAL（不能当全库门禁）")
    ap.add_argument("--expect-clips", type=int, default=0,
                    help="锁定期望段数（防止源文件丢失后期望集合自己缩小）")
    args = ap.parse_args()

    _app = QGuiApplication.instance() or QGuiApplication([])
    char_dir = REPO_ROOT / "assets" / "characters" / args.character
    videos = char_dir / "videos"
    seq_root = char_dir / "frameseq"
    if not videos.is_dir():
        print(f"素材目录不存在: {videos}", file=sys.stderr)
        return 1
    exe = fp.ffmpeg_exe()

    targets: list[tuple[Path, Path]] = []
    missing: list[str] = []      # 世代目录缺失
    wrong_grade: list[str] = []  # 世代存在但不是当前档
    expected = 0                 # 期望清单（过滤前的全部源）
    for webm in sorted(videos.rglob("*.webm")):
        rel = webm.relative_to(videos).with_suffix("")
        gen = seq_root / rel.parent / f"{rel.name}.g{fp.source_sha256(webm)[:12]}"
        expected += 1
        if args.clip and args.clip not in webm.stem:
            continue
        if not gen.is_dir():
            missing.append(str(gen))
            continue
        grade = str((fp.read_meta(gen) or {}).get("encoder") or "")
        if not grade:
            wrong_grade.append(str(gen))
            if args.v6_only:
                continue
        elif grade != fp.ENCODER_DESC:
            wrong_grade.append(str(gen))
            if args.v6_only:
                continue
        targets.append((webm, gen))
    if args.limit:
        targets = targets[: args.limit]
        args.partial = True          # 抽样天然不完整，强制标 PARTIAL
    if args.expect_clips and expected != args.expect_clips:
        print(f"期望段数不符：锁定 {args.expect_clips}，当前源只有 {expected} 段"
              f"（源文件可能丢失，期望集合不能自己缩小）", file=sys.stderr)
        return 1
    if not targets:
        print("没有可验证的世代目录（先跑 tools/convert_frameseq.py）", file=sys.stderr)
        return 1

    print(f"待验证 {len(targets)} 段（Qt 解码 vs 生产端规范 RGBA），并行 {args.jobs}")
    bad = 0
    total_frames = 0
    t0 = time.perf_counter()
    results: list[dict] = []
    if args.jobs > 1:
        # 完成即打印（原来是先收集全部 Future 再逐个取结果 → 59 段要等全部跑完
        # 才出第一行，中断就什么都看不到）。
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {pool.submit(verify_clip, webm, gen, exe): webm
                    for webm, gen in targets}
            for done, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                results.append(r)
                _report_one(r, done, len(targets))
    else:
        for done, (webm, gen) in enumerate(targets, 1):
            r = verify_clip(webm, gen, exe)
            results.append(r)
            _report_one(r, done, len(targets))
    for r in results:
        total_frames += r.get("frames", 0)
        if not r["ok"]:
            bad += 1
    elapsed = time.perf_counter() - t0
    # 完整性判定（终审 B8）：缺世代、非当前档都算不完整；任何过滤模式
    # （--limit / --partial / --clip / --v6-only）只能输出 PARTIAL——
    # 不许拿"剩下这几段都过了"冒充"全库位精确"。
    filtered = bool(args.limit or args.partial or args.clip or args.v6_only)
    incomplete = bool(missing or wrong_grade) or len(targets) != expected
    print("")
    print(f"期望 {expected} 段 / 选中 {len(targets)} 段 / "
          f"缺世代 {len(missing)} / 非当前档 {len(wrong_grade)}")
    if bad:
        verdict = f"{bad} 段不通过"
    elif filtered:
        verdict = "PARTIAL（过滤模式，不承载全库门禁）"
    elif incomplete:
        verdict = "不完整（有缺世代或非当前档的段）"
    else:
        verdict = "全部位精确"
    print(f"合计 {total_frames} 帧 / {len(targets)} 段，{elapsed / 60:.1f} 分钟：{verdict}")
    if missing:
        print(f"  缺世代目录示例: {missing[:3]}", file=sys.stderr)
    if wrong_grade:
        print(f"  非当前档示例: {wrong_grade[:3]}", file=sys.stderr)
    # 退出码语义（终审 B8）：
    #   0 = 全库位精确通过（可当门禁放行）
    #   1 = 有段不通过 / 全库模式不完整
    #   2 = PARTIAL（过滤/抽样）——**不能返回 0**，否则门禁会误判放行
    if bad:
        return 1
    if filtered:
        return 2
    if incomplete:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
