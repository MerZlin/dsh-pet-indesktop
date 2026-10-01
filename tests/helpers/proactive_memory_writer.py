# -*- coding: utf-8 -*-
"""辅助进程：并发写 ProactiveMemory，用于 issue #196 的跨进程存活回归。

单独进程是必需的：``proactive_memory`` 的写锁是**跨进程**锁（Windows msvcrt /
POSIX flock），同进程内起两个实例只能验证线程级互斥，验证不了两个桌宠进程
（``instance_launcher`` 默认起独立进程）共用同一份记忆文件时的读改写覆盖。

用法：
    python tests/helpers/proactive_memory_writer.py <memory_path> <label> <count> <max_entries> [barrier_path]

给了 barrier_path 时先自旋等该文件出现再开始写——父进程等三个写者都起来后才
创建它，把三个进程的「读—改—写」压进同一时间窗（事件式同步，不是 sleep 猜时序）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.proactive_memory import ProactiveMemory  # noqa: E402

BARRIER_TIMEOUT_SECONDS = 60.0


def _wait_for_barrier(path: Path) -> None:
    deadline = time.monotonic() + BARRIER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.005)
    raise SystemExit(f"barrier 未在 {BARRIER_TIMEOUT_SECONDS}s 内出现: {path}")


def main(argv: list[str]) -> int:
    memory_path = Path(argv[1])
    label = argv[2]
    count = int(argv[3])
    max_entries = int(argv[4])
    if len(argv) > 5:
        _wait_for_barrier(Path(argv[5]))
    memory = ProactiveMemory(memory_path, max_entries=max_entries)
    for index in range(count):
        # activity 全局唯一：丢没丢、丢了哪几条，看 activity 集合就知道
        memory.record(f"{label}.exe", "标题不落盘", f"act-{label}-{index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
