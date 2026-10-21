# -*- coding: utf-8 -*-
"""单模块导入成本微基准（每个模块一个全新子进程，避免顺序耦合）。

    .venv/Scripts/python.exe tools/import_cost.py pet.window pet.island.balance ...

输出：该模块（含其独占依赖）在 tracemalloc Python 堆上的增量 MB，以及
``sys.modules`` 里新增的模块数。用于回答"把某个模块改成按需 import 能省多少"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import importlib, json, sys, tracemalloc
sys.path.insert(0, sys.argv[2])
target = sys.argv[1]
tracemalloc.start(5)
before_mods = set(sys.modules)
baseline = 0
importlib.import_module("pet.config")            # 两个口径的共同基线
baseline = tracemalloc.get_traced_memory()[0]
importlib.import_module(target)
cur = tracemalloc.get_traced_memory()[0]
new_mods = sorted(m for m in set(sys.modules) - before_mods if m.startswith("pet."))
print(json.dumps({"mb": (cur - baseline) / 1048576.0, "modules": new_mods}))
'''


def main() -> int:
    targets = sys.argv[1:] or [
        'pet.window', 'pet.island.balance', 'pet.festival.service', 'pet.voice.chime_service',
        'pet.context_menus.shared', 'pet.autostart', 'pet.webm_clip', 'pet.agent_link',
        'pet.island.dynamic_island', 'pet.chat',
    ]
    code_root = str(ROOT)
    results = {}
    for target in targets:
        proc = subprocess.run([sys.executable, '-c', CHILD, target, code_root],
                              capture_output=True, text=True, cwd=code_root)
        if proc.returncode != 0:
            results[target] = {'error': proc.stderr.strip().splitlines()[-1] if proc.stderr else '?'}
            continue
        results[target] = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f'{"MB":>8}  {"new pet modules":>16}  module')
    for target, info in sorted(results.items(), key=lambda kv: -kv[1].get('mb', -1)):
        if 'error' in info:
            print(f'{"ERR":>8}  {"":>16}  {target}: {info["error"]}')
            continue
        print(f'{info["mb"]:8.2f}  {len(info["modules"]):16d}  {target}')
    print()
    for target, info in sorted(results.items(), key=lambda kv: -kv[1].get('mb', -1)):
        mods = info.get('modules')
        if mods:
            print(f'{target} -> {", ".join(mods)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
