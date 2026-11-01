# -*- coding: utf-8 -*-
"""petcore 启动 / 静息 CPU 实测（Windows，psutil 采样整个进程树）。

用途：给「启动 CPU 冒到 9%」「静息 2%」这类反馈一个可复测的数字，并对比
`idle_low_fps_enabled` 开/关。脚本只读进程 CPU 时间，不改产品代码。

用法（在仓库根目录）：
    .venv/Scripts/python scripts/bench_petcore_idle_cpu.py \
        --root .scratch/cpu-root --warmup 20 --seconds 60 \
        --set playback_speed=2.0 --set idle_low_fps_enabled=true

    # 测已安装的 exe（临时数据根，不碰用户配置）：
    .venv/Scripts/python scripts/bench_petcore_idle_cpu.py \
        --exe E:/dsh-pet-core/dsh-pet-core.exe --root .scratch/cpu-root-exe

    # 只测启动前 30 秒时间线：
    .venv/Scripts/python scripts/bench_petcore_idle_cpu.py --mode startup

输出：启动时间线（每秒一行）+ 静息窗口平均/峰值（总 CPU% 与单核%）。
总 CPU% 口径 = 进程树 CPU 秒 / 墙钟 / 逻辑核数 × 100（任务管理器同口径）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - 开发机一定有
    raise SystemExit("需要 psutil（requirements.txt 已包含）")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SETS = {
    "playback_speed": 2.0,
    "idle_low_fps_enabled": True,
}


def _parse_value(raw: str):
    text = raw.strip()
    if not text:
        return ""
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return json.loads(text)
    except ValueError:
        return text


def _prepare_root(root: Path, config_from: Path | None, overrides: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    if config_from is not None and config_from.is_file():
        shutil.copy2(config_from, config_path)
    from petcore.config import Config

    config = Config(root)
    for key, value in overrides.items():
        config.set(key, value)
    config.save()


def _cpu_seconds(proc: subprocess.Popen) -> float:
    total = 0.0
    try:
        root = psutil.Process(proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0
    for item in [root, *root.children(recursive=True)]:
        try:
            times = item.cpu_times()
            total += float(times.user) + float(times.system)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _sample(
    proc: subprocess.Popen,
    seconds: float,
    *,
    interval: float = 0.25,
    per_second: bool = False,
) -> list[dict]:
    cores = max(1, psutil.cpu_count())
    start_wall = time.monotonic()
    start_cpu = _cpu_seconds(proc)
    rows: list[dict] = []
    bucket_start = start_wall
    bucket_cpu = start_cpu
    while True:
        now = time.monotonic()
        elapsed = now - start_wall
        if elapsed >= seconds:
            break
        time.sleep(interval)
        if not per_second:
            continue
        if time.monotonic() - bucket_start < 1.0:
            continue
        cpu = _cpu_seconds(proc)
        wall = time.monotonic()
        rows.append(
            {
                "t": round(wall - start_wall, 2),
                "total_percent": round((cpu - bucket_cpu) / max(0.001, wall - bucket_start) / cores * 100, 2),
                "core_percent": round((cpu - bucket_cpu) / max(0.001, wall - bucket_start) * 100, 2),
            }
        )
        bucket_start, bucket_cpu = wall, cpu
    return rows


def _summary(proc: subprocess.Popen, seconds: float) -> dict:
    cores = max(1, psutil.cpu_count())
    start_wall = time.monotonic()
    start_cpu = _cpu_seconds(proc)
    peak_cpu = 0.0
    last_wall = start_wall
    last_cpu = start_cpu
    while time.monotonic() - start_wall < seconds:
        time.sleep(0.25)
        cpu = _cpu_seconds(proc)
        wall = time.monotonic()
        delta = max(0.0, cpu - last_cpu)
        peak_cpu = max(peak_cpu, delta / max(0.05, wall - last_wall) / cores * 100)
        last_wall, last_cpu = wall, cpu
    elapsed = time.monotonic() - start_wall
    delta = _cpu_seconds(proc) - start_cpu
    return {
        "seconds": round(elapsed, 2),
        "avg_total_percent": round(delta / max(0.001, elapsed) / cores * 100, 3),
        "avg_core_percent": round(delta / max(0.001, elapsed) * 100, 3),
        "peak_total_percent": round(peak_cpu, 2),
        "cores": cores,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="petcore 启动/静息 CPU 实测")
    parser.add_argument("--root", required=True, help="临时数据根（不会碰用户配置）")
    parser.add_argument("--exe", default="", help="已安装 exe；缺省用源码树 python -m petcore")
    parser.add_argument("--config-from", default="", help="复制一份现有 config.json 作为起点")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--mode", choices=("startup", "idle", "both"), default="both")
    parser.add_argument("--startup-seconds", type=float, default=30.0)
    parser.add_argument("--warmup", type=float, default=20.0)
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)

    overrides = dict(DEFAULT_SETS)
    for item in args.set:
        key, _, value = item.partition("=")
        if not key:
            parser.error(f"--set 需要 KEY=VALUE：{item!r}")
        overrides[key.strip()] = _parse_value(value)
    root = Path(args.root).resolve()
    config_from = Path(args.config_from).expanduser() if args.config_from else None
    _prepare_root(root, config_from, overrides)

    if args.exe:
        command = [str(Path(args.exe)), "--root", str(root)]
        cwd = str(Path(args.exe).resolve().parent)
    else:
        command = [sys.executable, "-m", "petcore", "--root", str(root)]
        cwd = str(ROOT)
    log_path = root / "bench-app.log"
    log = open(log_path, "w", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    env = dict(os.environ)
    env.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    print(f"[bench] root={root}")
    print(f"[bench] command={' '.join(command)}")
    print(f"[bench] config={json.dumps(overrides, ensure_ascii=False)}")
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        env=env,
    )
    try:
        time.sleep(3.0)
        if proc.poll() is not None:
            print(f"[bench] 启动失败，exit={proc.returncode}；日志：{log_path}")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
            return 2
        result = {"command": command, "root": str(root), "overrides": overrides}
        if args.mode in ("startup", "both"):
            rows = _sample(proc, args.startup_seconds, per_second=True)
            print("[bench] 启动时间线（总 CPU% / 单核%）：")
            for row in rows:
                print(
                    f"  t={row['t']:>6.2f}s  total={row['total_percent']:>6.2f}%"
                    f"  core={row['core_percent']:>7.2f}%"
                )
            result["startup"] = rows
        if args.mode in ("idle", "both"):
            wait = args.warmup
            if wait > 0:
                print(f"[bench] 预热 {wait:.0f}s（让启动尖峰过去）…")
                time.sleep(wait)
            idle = _summary(proc, args.seconds)
            print(
                f"[bench] 静息 {idle['seconds']:.1f}s：平均 {idle['avg_total_percent']:.2f}% 总 CPU"
                f"（单核 {idle['avg_core_percent']:.1f}%），峰值 {idle['peak_total_percent']:.2f}%"
            )
            result["idle"] = idle
        print("[bench] " + json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
