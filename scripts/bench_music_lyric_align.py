# -*- coding: utf-8 -*-
"""歌词对齐（手动校准快进 / 半途起播）性能实测。

对应 PR 报告 `docs/PR-REPORT-music-lyric-align-2026-09-22.md` 的「性能分析」
一节：把该 PR 新增/改动的路径成本压成可复现的数字，避免只有定性说法。

测三类东西：

1. **热路径（每秒一拍）**：`LyricTracker.position` / `line_at` / `advance`。
   `advance` 是 1s 采样线程唯一每拍都会调的入口，其源码在本次改动中**逐字
   未动**——这里测它是为了给出稳态基线，便于日后对比。
2. **新增路径（仅在用户手动对齐时走一次）**：`line_now` / `reanchor_to_line`。
   不在这条路径上的东西没有理由出现在 1s tick 里。
3. **周边固定成本**：`get_now_playing()`（跨进程序 SMTC 采样）与
   `resolve_menu_layout()`（菜单展开时解析一次），用来判断新增菜单项是否
   给既有开销加量。

用法：
    python scripts/bench_music_lyric_align.py            # 含 SMTC 实测
    python scripts/bench_music_lyric_align.py --no-smtc  # 纯离线（无播放器/CI）
    python scripts/bench_music_lyric_align.py --lines 53 --iters 200000

stdout 最后一行打印 JSON 摘要（各指标 ns/次 或 ms/次），便于报告引用。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import music_lyric  # noqa: E402
from pet.music_lyric_controller import LyricTracker  # noqa: E402


def make_lines(count: int, *, spacing: float = 4.0) -> list[music_lyric.LyricLine]:
    """合成 ``count`` 行歌词（默认 4 秒一句，接近实测流行歌的行距）。"""
    return [
        music_lyric.LyricLine(at=i * spacing, text=f"第 {i + 1} 句歌词")
        for i in range(count)
    ]


def _timeit(func, iters: int) -> float:
    """返回单次调用的纳秒数（取总耗时 / 次数，不做预热剔除）。"""
    start = time.perf_counter()
    for _ in range(iters):
        func()
    return (time.perf_counter() - start) / iters * 1e9


def bench_tracker(*, lines: int, iters: int, now: float = 1000.0) -> dict:
    tracker = LyricTracker()
    tracker.load(make_lines(lines), now=now, position=None)

    # 每拍：位置换算 → 查行 → 判重。advance 内部就是这两步加一次比较。
    position_ns = _timeit(lambda: tracker.position(now + 11.0), iters)
    line_at_ns = _timeit(lambda: tracker.line_at(11.0), iters)
    advance_ns = _timeit(lambda: tracker.advance(now + 11.0), iters)

    # 新增路径：只在「音乐 → 歌词对齐」被点一次时各走一次。
    line_now_ns = _timeit(lambda: tracker.line_now(now + 11.0), iters)
    # reanchor_to_line 会改状态，用 iters 较小的一轮避免把基准推到天边。
    small = max(1000, iters // 100)
    reanchor_ns = _timeit(lambda: tracker.reanchor_to_line(1, now=now + 11.0), small)

    return {
        "tracker_lines": lines,
        "position_ns": round(position_ns, 1),
        "line_at_ns": round(line_at_ns, 1),
        "advance_ns": round(advance_ns, 1),
        "ticks_per_second": 1.0,
        "line_now_ns": round(line_now_ns, 1),
        "reanchor_to_line_ns": round(reanchor_ns, 1),
        "reanchor_iters": small,
    }


def bench_now_playing(*, samples: int) -> dict | None:
    try:
        from pet.now_playing import get_now_playing
    except Exception as exc:  # noqa: BLE001 - 无 winrt 时如实记录，不伪装成 0
        return {"error": f"{type(exc).__name__}: {exc}"}

    durations: list[float] = []
    last = None
    for _ in range(samples):
        start = time.perf_counter()
        try:
            last = get_now_playing()
        except Exception as exc:  # noqa: BLE001 - SMTC 异常本身也是结论
            return {"error": f"{type(exc).__name__}: {exc}"}
        durations.append((time.perf_counter() - start) * 1000.0)
    durations.sort()
    mid = durations[len(durations) // 2]
    return {
        "samples": samples,
        "median_ms": round(mid, 3),
        "min_ms": round(durations[0], 3),
        "max_ms": round(durations[-1], 3),
        "observed": None if last is None else {
            "title": last.track.title,
            "artist": last.track.artist,
            "playing": last.track.playing,
            "app_id": last.app_id,
            "position": last.position,
            "duration": last.track.duration,
        },
    }


def bench_menu_layout(*, iters: int) -> dict:
    from pet import menu_layout
    from pet.context_menus.registry import MENU_ACTIONS

    raw = menu_layout.load_default_menu_layout()
    registered = MENU_ACTIONS.ids

    def resolve():
        menu_layout.resolve_menu_layout(
            raw, registered_actions=registered, available_actions=registered)

    return {
        "layout_id": menu_layout.DEFAULT_LAYOUT_ID,
        "action_ids": len(registered),
        "has_music_lyric_align": "music_lyric_align" in registered,
        "resolve_ms": round(_timeit(resolve, iters) / 1e6, 4),
        "resolve_iters": iters,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bench lyric alignment paths.")
    parser.add_argument("--lines", type=int, default=71, help="歌词行数（实测样本上限 71）")
    parser.add_argument("--iters", type=int, default=200000)
    parser.add_argument("--smtc-samples", type=int, default=12)
    parser.add_argument("--no-smtc", action="store_true", help="跳过真实 SMTC 采样")
    args = parser.parse_args(argv)

    result: dict = {
        "python": sys.version.split()[0],
        "tracker": bench_tracker(lines=args.lines, iters=args.iters),
    }
    if not args.no_smtc:
        result["now_playing"] = bench_now_playing(samples=args.smtc_samples)
    try:
        result["menu_layout"] = bench_menu_layout(iters=max(200, args.iters // 1000))
    except Exception as exc:  # noqa: BLE001 - 纯离线环境缺模板时如实记录
        result["menu_layout"] = {"error": f"{type(exc).__name__}: {exc}"}

    tracker = result["tracker"]
    print(f"[bench] LyricTracker position      : {tracker['position_ns']:>10.1f} ns/op")
    print(f"[bench] LyricTracker line_at       : {tracker['line_at_ns']:>10.1f} ns/op")
    print(f"[bench] LyricTracker advance (1Hz) : {tracker['advance_ns']:>10.1f} ns/op")
    print(f"[bench] LyricTracker line_now      : {tracker['line_now_ns']:>10.1f} ns/op")
    print(f"[bench] reanchor_to_line           : {tracker['reanchor_to_line_ns']:>10.1f} ns/op")
    now_playing = result.get("now_playing")
    if isinstance(now_playing, dict) and "median_ms" in now_playing:
        print(f"[bench] get_now_playing            : {now_playing['median_ms']:>10.3f} ms/op"
              f" (min {now_playing['min_ms']} / max {now_playing['max_ms']},"
              f" n={now_playing['samples']})")
    layout = result.get("menu_layout") or {}
    if "resolve_ms" in layout:
        print(f"[bench] resolve_menu_layout        : {layout['resolve_ms']:>10.4f} ms/op")
    print(json.dumps(result, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
