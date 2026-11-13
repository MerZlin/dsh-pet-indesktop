# -*- coding: utf-8 -*-
"""AstrBot 联动通道的微基准（docs/PR-REPORT-ASTRBOT-AGENT-LINK-2026-11-06.md §四）。

用法：``QT_QPA_PLATFORM=offscreen python tools/bench_astrbot_link.py``
测：桥接插件一个完整回合的落盘成本、去重命中成本、回合表满载内存、桌宠读方空闲
轮询成本、一键安装/卸载耗时。只写系统临时目录，不碰真实 AstrBot。
"""
import importlib.util, os, statistics, sys, tempfile, time, tracemalloc
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("bridge", REPO / "integrations/astrbot-pet-bridge/main.py")
br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
from pet.agent_link import AstrBotMonitor

def pct(xs, p): xs = sorted(xs); return xs[int(len(xs) * p) - 1]
tmp = Path(tempfile.mkdtemp())
# 1) 写方：一个完整回合（thinking→working→thinking→idle = 4 次落盘）
b = br.PetBridge(tmp / "e.jsonl")
N = 5000; per_turn = []
for i in range(N):
    t = time.perf_counter_ns()
    b.begin_turn(i); b.tool_start(i, "web_search"); b.tool_end(i, failed=False); b.llm_response(i, failed=False)
    per_turn.append((time.perf_counter_ns() - t) / 1000)
print(f"bridge full turn (4 appends) N={N}: median {statistics.median(per_turn):.1f} us, p95 {pct(per_turn,0.95):.1f} us")
# 去重命中（同态重复钩子，不落盘）
b.begin_turn("x"); dup = []
for i in range(N):
    t = time.perf_counter_ns(); b.begin_turn("x"); dup.append((time.perf_counter_ns() - t) / 1000)
print(f"bridge dedup hit (no write) N={N}: median {statistics.median(dup):.2f} us")
# 2) 回合表内存上界
tracemalloc.start(); b2 = br.PetBridge(tmp / "m.jsonl")
s0 = tracemalloc.take_snapshot()
for i in range(br.MAX_TURNS): b2._ensure_turn(("k", i))
s1 = tracemalloc.take_snapshot(); tracemalloc.stop()
print(f"turn table at cap ({br.MAX_TURNS}): {sum(st.size_diff for st in s1.compare_to(s0,'filename'))/1024:.1f} KiB")
# 3) 读方空闲轮询：文件存在、无新数据
root = tmp / "AstrBot"; (root / "data/plugins").mkdir(parents=True)
ev = AstrBotMonitor.events_file_for(root / "data"); ev.parent.mkdir(parents=True); ev.write_text('{"state":"idle"}\n')
mon = AstrBotMonitor(tmp / "cfg", data_dir=root / "data"); mon._poll()
idle = []
for _ in range(N):
    t = time.perf_counter_ns(); mon._poll(); idle.append((time.perf_counter_ns() - t) / 1000)
print(f"monitor idle poll (file present, no new bytes) N={N}: median {statistics.median(idle):.1f} us, p95 {pct(idle,0.95):.1f} us")
ev.unlink(); missing = []
for _ in range(N):
    t = time.perf_counter_ns(); mon._poll(); missing.append((time.perf_counter_ns() - t) / 1000)
print(f"monitor idle poll (file absent) N={N}: median {statistics.median(missing):.1f} us")
# 4) 安装 / 卸载
ins, uni = [], []
for _ in range(50):
    t = time.perf_counter(); ok, _m = AstrBotMonitor.install_bridge(root); ins.append((time.perf_counter() - t) * 1000); assert ok
    t = time.perf_counter(); AstrBotMonitor.uninstall_bridge(root); uni.append((time.perf_counter() - t) * 1000)
print(f"install_bridge N=50: median {statistics.median(ins):.2f} ms; uninstall_bridge median {statistics.median(uni):.2f} ms")
import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
