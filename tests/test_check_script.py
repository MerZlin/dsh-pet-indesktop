"""Tests for the cross-platform quality-check entrypoint."""

from __future__ import annotations

import sys

from scripts.check import build_commands


def _render(commands: list[list[str]]) -> list[str]:
    return [" ".join(command) for command in commands]


def test_check_commands_include_static_and_smoke_steps() -> None:
    rendered = _render(build_commands(fast=True))
    assert any("ruff check pet tests" in command for command in rendered)
    assert any("ruff format --check pet tests" in command for command in rendered)
    assert any("mypy pet/plugins pet/content pet/catalog.py pet/config.py" in command for command in rendered)
    assert any("scripts/check_docs.py" in command for command in rendered)
    assert any("compileall -q pet scripts" in command for command in rendered)
    assert any("import pet; import pet.catalog; import pet.content; import pet.plugins" in command for command in rendered)
    assert rendered[-1].endswith("pytest -q -m not slow")
    assert rendered[0].startswith(sys.executable)


def test_check_commands_use_coverage_for_full_gate() -> None:
    rendered = _render(build_commands(fast=False))
    assert rendered[-1].endswith("pytest -q --cov=pet --cov-report=term-missing --cov-fail-under=83")
