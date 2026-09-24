# -*- coding: utf-8 -*-
"""交付证据纪律的机器化校验（2026-09-22 起）。

`AGENTS.md` 的 **Delivery evidence discipline** 要求每个 PR 交付三份证据：
修改文件说明 / 性能分析 / 实机运行记录。本文件把「规范本身还在」与「新报告
是否真的写了」都变成断言——否则规范会随着文档编辑被静默删掉，而这正是它想
防止的失败模式。

四条断言：

1. 模板 `docs/PR-REPORT-TEMPLATE.md` 存在，且逐字含三个必备章节名；
2. `AGENTS.md` 与 `docs/DEV-HANDOVER.md` 仍写明该要求并指向模板（删规范 = 红）；
3. 报告文件名日期 **>= 2026-09-22** 的 `docs/PR-REPORT-*.md` 必须含三个必备
   章节（**标题级**，不是正文里提一句）；
4. 同上这批报告必须在 `docs/INDEX.md` 登记（新文档入场规则第 1 条）。

历史报告（2026-09-22 之前）一律豁免——回填旧报告不是本纪律的目的。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# 纪律生效日：文件名日期 >= 此日期的报告必须满足三份证据要求。
EVIDENCE_CUTOFF = date(2026, 9, 22)

# 三个必备章节（报告标题里必须逐字出现）。
REQUIRED_SECTIONS = ("修改文件说明", "性能分析", "实机运行记录")

TEMPLATE = DOCS / "PR-REPORT-TEMPLATE.md"
INDEX = DOCS / "INDEX.md"
AGENTS = ROOT / "AGENTS.md"
HANDOVER = DOCS / "DEV-HANDOVER.md"

_DATE_RE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})\.md$")


def _report_date(path: Path) -> date | None:
    """从文件名取报告日期；模板等无日期文件返回 None（不进纪律范围）。"""
    match = _DATE_RE.search(path.name)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:  # 文件名里的日期不合法：当成"没有日期"，由命名约定负责
        return None


def _headings(text: str) -> list[str]:
    """取 Markdown 标题文本（去掉 # 与首尾空白），用于标题级匹配。"""
    return [line.lstrip("#").strip() for line in text.splitlines() if line.lstrip().startswith("#")]


def _governed_reports() -> list[Path]:
    return sorted(path for path in DOCS.glob("PR-REPORT-*.md") if (d := _report_date(path)) is not None and d >= EVIDENCE_CUTOFF)


GOVERNED = _governed_reports()
GOVERNED_IDS = [path.name for path in GOVERNED]


def test_template_exists_and_declares_required_sections():
    """模板必须存在且逐字含三个必备章节——它是提交者的复制源。"""
    assert TEMPLATE.is_file(), f"交付证据模板缺失：{TEMPLATE}"
    headings = _headings(TEMPLATE.read_text(encoding="utf-8"))
    for section in REQUIRED_SECTIONS:
        assert any(section in heading for heading in headings), f"模板 {TEMPLATE.name} 的标题里缺少必备章节「{section}」"


def test_template_itself_is_registered_in_index():
    """模板是 docs/ 下的文档，按新文档入场规则必须登记。"""
    assert TEMPLATE.name in INDEX.read_text(encoding="utf-8"), f"{TEMPLATE.name} 未在 docs/INDEX.md 登记（新文档入场规则第 1 条）"


@pytest.mark.parametrize("doc", [AGENTS, HANDOVER], ids=lambda p: p.name)
def test_norm_is_documented_and_points_at_template(doc: Path):
    """规范必须写在 AGENTS.md 与 DEV-HANDOVER.md 里，并指向模板。

    这一条防的是「规范被悄悄删掉」：纪律只写在 PR 描述或聊天里等于不存在。
    """
    text = doc.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        assert section in text, f"{doc.name} 不再写明必备证据「{section}」"
    assert TEMPLATE.name in text, f"{doc.name} 没有指向模板 {TEMPLATE.name}"


def test_governed_reports_exist_after_cutoff():
    """生效日之后至少应有一份报告（本纪律自身的首份范例）。

    若这条红了，通常意味着首份范例被删或被改名成无日期格式——请先确认
    `_report_date` 的命名约定仍被遵守，而不是直接删断言。
    """
    assert GOVERNED, f"没有找到日期 >= {EVIDENCE_CUTOFF} 的 docs/PR-REPORT-*.md；日期必须写在文件名末尾（-YYYY-MM-DD.md）才能进入纪律范围"


@pytest.mark.parametrize("report", GOVERNED, ids=GOVERNED_IDS)
def test_report_has_three_evidence_sections(report: Path):
    """生效日之后的报告必须逐字含三个必备章节（标题级）。"""
    headings = _headings(report.read_text(encoding="utf-8"))
    missing = [section for section in REQUIRED_SECTIONS if not any(section in heading for heading in headings)]
    assert not missing, f"{report.name} 缺少必备章节：{', '.join(missing)}；按 {TEMPLATE.name} 补齐（判定标准见 DEV-HANDOVER §8.3）"


@pytest.mark.parametrize("report", GOVERNED, ids=GOVERNED_IDS)
def test_report_is_registered_in_index(report: Path):
    """生效日之后的报告必须在 docs/INDEX.md「PR 报告存档」登记。"""
    assert report.name in INDEX.read_text(encoding="utf-8"), f"{report.name} 未在 docs/INDEX.md 登记（新文档入场规则第 1 条）"
