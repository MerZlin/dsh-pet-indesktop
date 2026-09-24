#!/usr/bin/env python3
"""Run the repository's reproducible local and CI quality checks.

The script deliberately invokes tools through the current Python interpreter so
Windows, macOS, Linux, virtual environments, and CI use the same entrypoint.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
CORE_TYPE_TARGETS = ("pet/plugins", "pet/content", "pet/catalog.py", "pet/config.py")
COVERAGE_FAIL_UNDER = "83"


def _display_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def build_commands(*, fast: bool) -> list[list[str]]:
    python = sys.executable
    commands = [
        [python, "-m", "ruff", "check", "pet", "tests"],
        [python, "-m", "ruff", "format", "--check", "pet", "tests"],
        [python, "-m", "mypy", *CORE_TYPE_TARGETS],
        [python, "scripts/check_docs.py"],
        [python, "-m", "compileall", "-q", "pet", "scripts"],
        [python, "-c", "import pet; import pet.catalog; import pet.content; import pet.plugins"],
    ]
    if fast:
        commands.append([python, "-m", "pytest", "-q", "-m", "not slow"])
    else:
        commands.append(
            [
                python,
                "-m",
                "pytest",
                "-q",
                "--cov=pet",
                "--cov-report=term-missing",
                f"--cov-fail-under={COVERAGE_FAIL_UNDER}",
            ]
        )
    return commands


def run_command(command: Sequence[str], *, env: dict[str, str] | None = None) -> int:
    print(f"$ {_display_command(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env or os.environ.copy())
    if completed.returncode:
        print(f"check failed with exit code {completed.returncode}: {_display_command(command)}", flush=True)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true", help="skip coverage and tests marked slow")
    mode.add_argument("--ci", action="store_true", help="run the complete CI-equivalent gate")
    args = parser.parse_args(argv)

    # --ci is explicit for CI readability; the default is already the complete gate.
    for command in build_commands(fast=args.fast):
        if run_command(command):
            return 1
    print("All project checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
