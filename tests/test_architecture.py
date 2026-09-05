# -*- coding: utf-8 -*-
"""架构红线断言（结构线纪律的机器化）。

三条红线，红了就是架构倒退，不许靠改测试放行：
1. 依赖方向：纯逻辑层（collision/physics/collision_codec）不依赖 Qt；
   decode_fanout 不反向依赖 window/webm_clip（钩子经 movie 属性注入）。
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
# 2026-09-05 上调到 4345（批11-B1：ffmpeg 圈边界定期回收——窗口层把
# ffmpeg_recycle_minutes 经 _push_recycle 推送给播放 clip（_switch /
# _fallback_playable_idle / 拖拽重启 / refresh_pet_settings 四处对齐），
# 复审 P1-2 要求运行期可刷新。注：此前的注释日期「2026-11」为笔误）。
# 2026-09-05 上调到 4352（批12：_switch 切走成功时清旧 clip 显示槽——
# A1 修复的窗口权威侧，+6 行含注释；clip 侧清槽 API 在 webm_clip.py）。
# 2026-09-05 上调到 4360（批12 复审 N1：_on_clip_finished 对弃播 clip
# 在结束标记消费点补清显示槽，+6 行含注释）。
# 2026-09-05 上调到 4367（频闪修复：Windows 穿透改原生 WS_EX_TRANSPARENT
# 样式位、不再 setWindowFlag 重建窗口 +4 行含注释；hideEvent 补 [VIS] 观测
# +1 行。详见 _plan/current/memory/REVIEW_flicker_glm53.md）。
# 2026-09-05 批5.3 合入：删 broker 首个 idle 延迟与轮询（净 -44 行），
# 频闪修复保留（+5），合并后实测 4261 行，预算按实测 +50 行余量收紧。
WINDOW_PY_LINE_BUDGET = 4311

# modern_settings_dialog.py 行数预算：按结构线拆分后实测 1857 行（拆分前 4811 行）。
# 主对话框 ModernSettingsDialog + 对话框装配/配置写回 + 为 pet/ 与 tests/ 保留的
# re-export 留守本文件；控件库 / 菜单布局编辑器 / AI 设置页 / 主题 QSS 已分别拆至
# settings_widgets / settings_menu_layout_editor / chat/ai_settings_page /
# settings_theme_qss。预算 = 1857 + 50 行余量；再往上帝类里塞新页面时只许降不涨。
# 2026-09-05 建立（perf/memory-footprint 拆分批）。
MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET = 1907


def _read(name: str) -> str:
    return (PET_DIR / name).read_text(encoding="utf-8")


def test_pure_logic_modules_do_not_import_qt():
    for name in ("collision.py", "physics.py", "collision_codec.py"):
        src = _read(name)
        assert "PySide6" not in src, f"{name} 引入了 Qt 依赖，破坏纯函数层定位"


def test_decode_fanout_does_not_depend_on_window_or_player():
    src = _read("decode_fanout.py")
    for banned in ("pet.window", "pet.webm_clip", "from .window", "from .webm_clip",
                   "import window", "import webm_clip"):
        assert banned not in src, f"decode_fanout 反向依赖 {banned}，破坏单向依赖"


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


def test_modern_settings_dialog_py_line_budget():
    lines = len(_read("modern_settings_dialog.py").splitlines())
    assert lines <= MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET, (
        f"modern_settings_dialog.py 涨到 {lines} 行（预算 {MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET}）。"
        "控件库/菜单布局编辑器/AI 设置页/主题 QSS 已拆出；确需上调预算时在 PR 说明理由。"
    )


def test_settings_widgets_orphan_cluster_guard():
    """孤儿簇（批6-7 拆分被上游合并静默回退的死文件）防再发：settings_widgets.py
    与 settings_styles*.qss 不允许「存在且零引用」态——要么已删除，要么被
    pet/ 内某模块 import/读取（read_text/QFile）。"""
    targets = (
        "settings_widgets.py",
        "settings_styles.qss",
        "settings_styles_dark.qss",
        "settings_styles_dark_browser.qss",
    )
    py_sources = [p.read_text(encoding="utf-8") for p in PET_DIR.rglob("*.py")]
    for name in targets:
        path = PET_DIR / name
        if not path.exists():
            continue  # 已删除：允许的终态
        needle = name[:-3] if name.endswith(".py") else name
        assert any(needle in src for src in py_sources), (
            f"{name} 存在但零引用——批6-7 孤儿簇回退态，须删除或恢复接线"
        )
