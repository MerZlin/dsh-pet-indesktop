from pathlib import Path

import pytest

from scripts.check_docs import check_links

pytestmark = pytest.mark.unit


def test_check_links_reports_missing_local_target(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "[ok](guide.md)\n[missing](missing.md)\n[external](https://example.com/docs)\n",
        encoding="utf-8",
    )
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")

    broken = check_links(tmp_path)

    assert [(item.line, item.target) for item in broken] == [(2, "missing.md")]


def test_check_links_rejects_path_outside_repository(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text("[outside](../outside.md)\n", encoding="utf-8")

    broken = check_links(tmp_path)

    assert len(broken) == 1
    assert broken[0].reason == "目标路径超出仓库根目录"
