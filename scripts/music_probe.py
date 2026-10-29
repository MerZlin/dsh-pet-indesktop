# -*- coding: utf-8 -*-
"""识别探针：命令行版「识别诊断」（排障与实机验收用，不进产品运行时）。

`media.music` 的识别由三源合成（系统媒体会话 / 音频会话 / 播放器窗口标题），
出问题时很难从气泡看出是哪一层：这个脚本用**与设置页按钮完全相同的一份编排**
（``host/sampling.py`` + ``host/recognizer.py`` + ``host/diagnostics.py``）在终端
逐拍打印采样与判定，便于把"哪一层没看到"直接贴进报告。

用法::

    .venv/Scripts/python scripts/music_probe.py              # 只在判定变化时打印
    .venv/Scripts/python scripts/music_probe.py --all        # 每拍都打印
    .venv/Scripts/python scripts/music_probe.py --seconds 30 --interval 1
    .venv/Scripts/python scripts/music_probe.py --title-format "%artist% - %title%"
    .venv/Scripts/python scripts/music_probe.py --allow cloudmusic,foobar2000
    .venv/Scripts/python scripts/music_probe.py --profile    # 顺带打采样耗时

只读系统信息，不联网、不落盘、不改任何配置。
"""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD_HOST = ROOT / "examples" / "petcore-mods" / "media.music" / "host"
# 从仓库根直接跑时要能 import petcore（`host/__init__.py` 依赖 contracts）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOST_PKG = "petcore_music_probe_host"


def _load_host_package():
    """按包加载 mod 的 host 目录（与 loader/测试同一套绑定方式）。"""
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_HOST / "__init__.py",
        submodule_search_locations=[str(MOD_HOST)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


def _parse_ids(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    items = (
        str(value)
        .replace("；", ",")
        .replace("，", ",")
        .replace("、", ",")
        .split(",")
    )
    return frozenset(item.strip().lower() for item in items if item.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="音乐识别探针（只读）")
    parser.add_argument("--seconds", type=float, default=15.0, help="总时长（秒），默认 15")
    parser.add_argument("--interval", type=float, default=1.0, help="采样间隔（秒），默认 1")
    parser.add_argument("--all", action="store_true", help="每拍都打印（默认只在判定变化时打印）")
    parser.add_argument("--title-format", default="", help="窗口标题格式串（同设置页）")
    parser.add_argument(
        "--allow",
        default="",
        help="只识别这些识别名（逗号分隔，等价于设置页「只识别指定播放器」）",
    )
    parser.add_argument("--targets", default="", help="额外允许的窗口标题源可执行名（逗号分隔）")
    parser.add_argument("--profile", action="store_true", help="打印采样耗时（毫秒）")
    args = parser.parse_args(argv)

    _load_host_package()
    sampling = importlib.import_module(f"{HOST_PKG}.sampling")
    recognizer = importlib.import_module(f"{HOST_PKG}.recognizer")
    diagnostics = importlib.import_module(f"{HOST_PKG}.diagnostics")
    players = importlib.import_module(f"{HOST_PKG}.players")
    now_playing = importlib.import_module(f"{HOST_PKG}.now_playing")

    allowed = _parse_ids(args.allow)
    targets = frozenset(players.recognition_targets({}, args.targets))
    interval = max(0.2, float(args.interval))
    deadline = time.monotonic() + max(interval, float(args.seconds))
    last_key = None
    timings: list[float] = []
    ticks = 0

    print(f"[probe] 平台={sys.platform} 间隔={interval:g}s 时长={args.seconds:g}s")
    print(f"[probe] 识别范围={sorted(allowed) or '（不过滤）'} 窗口源允许={len(targets)} 个可执行名")
    # SMTC 走有界读取：系统媒体服务卡死时探针要给"读取超时（已降级）"，不是跟着挂死。
    reader = now_playing.SmtcReader()
    try:
        while time.monotonic() < deadline:
            started = time.perf_counter()
            sample = sampling.collect(
                allowed_ids=allowed or None,
                targets=targets,
                smtc=reader.read,
            )
            decision = recognizer.decide(
                sample,
                allowed_ids=allowed,
                title_format=args.title_format,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings.append(elapsed_ms)
            ticks += 1

            key = (decision.status, decision.source, decision.app_id, decision.track.key() if decision.track else None)
            if args.all or key != last_key:
                stamp = time.strftime("%H:%M:%S")
                print(f"\n===== {stamp} 第 {ticks} 拍（采样 {elapsed_ms:.1f}ms）=====")
                print(diagnostics.render(sample, decision))
                last_key = key
            time.sleep(interval)
    finally:
        reader.stop(1.0)

    if args.profile and timings:
        print(
            "\n[profile] 采样耗时 ms："
            f"median={statistics.median(timings):.2f} "
            f"p95={sorted(timings)[max(0, int(len(timings) * 0.95) - 1)]:.2f} "
            f"max={max(timings):.2f} n={len(timings)}"
        )
    print("\n[probe] 结束（本工具只读，未修改任何配置）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
