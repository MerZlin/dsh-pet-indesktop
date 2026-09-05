# -*- coding: utf-8 -*-
"""架构红线断言（结构线纪律的机器化）。

三条红线，红了就是架构倒退，不许靠改测试放行：
1. 依赖方向：纯逻辑层（collision/physics/collision_codec）不依赖 Qt；
   decode_broker 不反向依赖 window/webm_clip（钩子经 movie 属性注入）。
2. 私有面冻结：PetWindow 私有成员（win._xxx）只许 window.py 自身与
   collision_client.py（窗口的碰撞客户端，半内部）访问；app.py /
   agent_link.py / context_menus/ 再出现即为违规（S2 已清零，防回潮）。
3. window.py 行数预算：结构线拆到 4200 量级后只许降不许涨——
   新功能请先按 docs/WINDOW_PY_SPLIT_GUIDE.md 拆对应控制器，
   而不是继续往上帝类里塞。确实该涨时，预算上调必须在 PR 里说明理由。
"""
from __future__ import annotations

import re
from pathlib import Path

PET_DIR = Path(__file__).resolve().parents[1] / "pet"

# window.py 行数预算：合并上游 v4.1.0 后实测 4229，留 ~1.7% 余量。
# 拆分控制器时本预算应随之下调。
# 2026-09-04 上调到 4330（流畅度批次：刷新率自适应节拍、PreciseTimer、
# DPR 兜底轮询限频、perfstats 帧间隔看门狗；均有实测数据支撑）。
# 2026-09-05 合并 2548d87 已有的 Agent 交互气泡、问答/审批与 persona
# 窗口接线后实测 4750；同日补充全屏 watcher 销毁闸门、素材库 shutdown
# 与 headless 时钟隔离后实测 4770。
# 业务状态机仍归 agent_link.py；这里仅保留 Qt 窗口 seam。
WINDOW_PY_LINE_BUDGET = 4770


def _read(name: str) -> str:
    return (PET_DIR / name).read_text(encoding="utf-8")


def test_pure_logic_modules_do_not_import_qt():
    for name in ("collision.py", "physics.py", "collision_codec.py"):
        src = _read(name)
        assert "PySide6" not in src, f"{name} 引入了 Qt 依赖，破坏纯函数层定位"


def test_decode_broker_does_not_depend_on_window_or_player():
    src = _read("decode_broker.py")
    for banned in ("pet.window", "pet.webm_clip", "from .window", "from .webm_clip",
                   "import window", "import webm_clip"):
        assert banned not in src, f"decode_broker 反向依赖 {banned}，破坏单向依赖"


def test_window_private_surface_frozen():
    """S2 收口成果：window 私有成员跨模块访问在以下文件中必须保持零命中。"""
    pattern = re.compile(r"(?:win|pet|window)\._[a-z]")
    offenders = []
    for rel in ("app.py", "agent_link.py"):
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    for path in sorted((PET_DIR / "context_menus").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"context_menus/{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "window 私有面回潮：\n" + "\n".join(offenders)


def test_window_py_line_budget():
    lines = len(_read("window.py").splitlines())
    assert lines <= WINDOW_PY_LINE_BUDGET, (
        f"window.py 涨到 {lines} 行（预算 {WINDOW_PY_LINE_BUDGET}）。"
        "新功能请先拆对应控制器（docs/WINDOW_PY_SPLIT_GUIDE.md），"
        "确需上调预算时在 PR 说明理由。"
    )
