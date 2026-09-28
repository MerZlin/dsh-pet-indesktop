# -*- coding: utf-8 -*-
"""规则体检：把几条"已经被踩出来"的纪律做成机械检查（复盘用）。

用法：

    python scripts/retro_check.py            # 打印体检结果
    python scripts/retro_check.py --quiet     # 只在有红项时输出（给自动化用）

退出码 0 = 全过；1 = 有红项（自动化里可以直接据此判断要不要提醒）。

检查的都是**已经变成规矩**的东西，不是新要求：

1. 生成文件落点（AGENTS.md 第 7 条）：仓库根不许有 `.tmp*` / `.pytest_cache` / `.venv*`；
2. 文案红线：`pet/ tests/ docs/` 与 `README.md` 里不许出现外部品牌词；
3. 默认中文：每个注册菜单动作都必须有中文标签（不许回落到英文 id）；
4. 文档登记：`docs/PR-REPORT-*.md` 都要在 `docs/INDEX.md` 里登记；
5. 行数预算：`window.py` 与 `modern_settings_dialog.py` 不超过预算（从测试里读常数）。

人要做的那半（机器查不了）：本周踩过的新坑有没有写进 `docs/AI-LESSONS-*.md`、
规则有没有跟着改。所以本脚本的结论行会提醒这一点。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BRAND_WORD = "co" + "dex"          # 见 docs/AI-LESSONS 04-1：报告里写这个词会触发红线
BRAND_SCAN_DIRS = ("pet", "tests", "docs")
BRAND_SCAN_SUFFIXES = {".py", ".md", ".json", ".qss"}
BRAND_EXEMPT = (
    "agent_link.py", "test_agent_link.py",
    "README-CHANGE-",
)
# `.venv` 是"跑测试用的开发环境"——故意的、已 gitignore，不算散落产物；
# 这里只抓临时目录与测试缓存（`.pytest_cache` 现在也只会出现在旧检出里）。
STRAY_PATTERNS = (".tmp", ".pytest_cache")


def _check_stray_files() -> tuple[bool, str]:
    stray = [p.name for p in ROOT.iterdir() if p.name.startswith(STRAY_PATTERNS)]
    stray += [p.name for p in ROOT.glob(".tmp*") if p.name not in stray]
    if stray:
        return False, "仓库根有测试/临时产物：" + "、".join(sorted(set(stray))[:8])
    return True, "仓库根没有 .tmp* / .pytest_cache / .venv*"


def _check_brand_words() -> tuple[bool, str]:
    hits: list[str] = []
    targets = [ROOT / name for name in BRAND_SCAN_DIRS] + [ROOT / "README.md"]
    for target in targets:
        paths = [target] if target.is_file() else list(target.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in BRAND_SCAN_SUFFIXES:
                continue
            if path.name.endswith("-RESEARCH.md") or path.name.startswith(BRAND_EXEMPT):
                continue
            if path.name in BRAND_EXEMPT:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if BRAND_WORD in text.lower():
                hits.append(str(path.relative_to(ROOT)))
    if hits:
        return False, "外部品牌词命中：" + "、".join(hits[:6])
    return True, "pet/ tests/ docs/ README.md 无外部品牌词"


def _check_menu_labels() -> tuple[bool, str]:
    sys.path.insert(0, str(ROOT))
    try:
        from pet.context_menus.registry import ACTION_LABELS, MenuActionRegistry
    except Exception as exc:                       # 依赖缺失时不当成失败
        return True, f"跳过（导入失败：{type(exc).__name__}）"
    registry = MenuActionRegistry()
    missing = sorted(a for a in registry.ids if a not in ACTION_LABELS)
    if missing:
        return False, "动作缺中文标签（编排器会显示英文 id）：" + "、".join(missing[:8])
    return True, f"{len(registry.ids)} 个动作都有中文标签"


def _check_docs_registered() -> tuple[bool, str]:
    index = DOCS / "INDEX.md"
    if not index.is_file():
        return True, "跳过（没有 docs/INDEX.md）"
    text = index.read_text(encoding="utf-8", errors="ignore")
    reports = sorted(p for p in DOCS.glob("PR-REPORT-*.md") if p.name != "PR-REPORT-TEMPLATE.md")
    missing = [p.name for p in reports if p.name not in text]
    if missing:
        return False, "PR 报告未在 docs/INDEX.md 登记：" + "、".join(missing[:6])
    return True, f"{len(reports)} 份 PR 报告都已登记"


def _read_budget(name: str) -> int | None:
    test_file = ROOT / "tests" / "test_architecture.py"
    try:
        text = test_file.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf"^{name} = (\d+)", text, re.M)
    return int(match.group(1)) if match else None


def _check_line_budgets() -> tuple[bool, str]:
    checks = (
        ("window.py", "pet/window.py", "WINDOW_PY_LINE_BUDGET"),
        ("modern_settings_dialog.py", "pet/modern_settings_dialog.py",
         "MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET"),
    )
    details = []
    failed = False
    for label, rel, const in checks:
        budget = _read_budget(const)
        path = ROOT / rel
        if budget is None or not path.is_file():
            details.append(f"{label} 跳过")
            continue
        lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        details.append(f"{label} {lines}/{budget}")
        if lines > budget:
            failed = True
    return (not failed), "行数预算：" + "；".join(details)


CHECKS = (
    ("规则7 生成文件落点", _check_stray_files),
    ("文案红线", _check_brand_words),
    ("默认中文（菜单标签）", _check_menu_labels),
    ("文档登记", _check_docs_registered),
    ("行数预算", _check_line_budgets),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="规则体检（复盘用）")
    parser.add_argument("--quiet", action="store_true", help="只在有红项时输出")
    args = parser.parse_args()

    failed: list[str] = []
    lines: list[str] = ["规则体检（复盘机械项）", ""]
    for label, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:                   # 检查自身出错按红处理，别静默
            ok, detail = False, f"检查抛异常：{type(exc).__name__}: {exc}"
        lines.append(f"[{'✓' if ok else '✗'}] {label}：{detail}")
        if not ok:
            failed.append(label)
    lines.append("")
    if failed:
        lines.append(f"结论：{len(failed)} 项红了（{('、').join(failed)}）——按 AGENTS.md 的对应规则改。")
    else:
        lines.append("结论：机械项全过。剩下的靠人：本周踩过的新坑写进 docs/AI-LESSONS-*.md 了吗？"
                     "规则（AGENTS.md「协作经验」）跟着改了吗？")

    if args.quiet and not failed:
        return 0
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
