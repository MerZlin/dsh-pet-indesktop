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
SOURCE_TARGETS = ("pet", "tests", "scripts")
COVERAGE_FAIL_UNDER = "83"
WEBM_LIFECYCLE_TESTS = (
    "tests/test_webm_reader_lifecycle.py",
    "tests/test_webm_clip_lifecycle.py",
    "tests/test_webm_first_frame_lock.py",
)
LOW_PRIORITY_WARM_TEST = "tests/test_low_priority_warm_interaction_yield.py"
ISOLATED_TEST_FAMILIES = (WEBM_LIFECYCLE_TESTS, (LOW_PRIORITY_WARM_TEST,))


def _display_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _static_commands(python: str) -> list[list[str]]:
    return [
        [python, "-m", "ruff", "check", *SOURCE_TARGETS],
        [python, "-m", "ruff", "format", "--check", *SOURCE_TARGETS],
        [python, "-m", "mypy", *CORE_TYPE_TARGETS],
        [python, "scripts/check_docs.py"],
        [python, "-m", "compileall", "-q", "pet", "scripts"],
        [python, "-c", "import pet; import pet.catalog; import pet.content; import pet.plugins"],
    ]


def build_commands(*, fast: bool, quality: bool = False) -> list[list[str]]:
    """Build commands for the fast, quality-matrix, or complete gate.

    The complete gate keeps the known native/Qt-sensitive families in separate
    subprocesses.  This preserves the existing CI failure isolation while
    making the command sequence reproducible from one local entrypoint.
    """

    python = sys.executable
    commands = _static_commands(python)
    if fast:
        commands.append([python, "-m", "pytest", "-q", "-m", "not slow"])
    elif quality:
        commands.append([python, "-m", "pytest", "-q", "-m", "unit"])
    else:
        main_suite = [python, "-m", "pytest", "-q"]
        for test_path in (*WEBM_LIFECYCLE_TESTS, LOW_PRIORITY_WARM_TEST):
            main_suite.append(f"--ignore={test_path}")
        main_suite.extend(["--cov=pet", "--cov-report="])
        commands.append(main_suite)
        commands.extend(
            [
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    *WEBM_LIFECYCLE_TESTS,
                    "--cov=pet",
                    "--cov-append",
                    "--cov-report=",
                ],
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    LOW_PRIORITY_WARM_TEST,
                    "--cov=pet",
                    "--cov-append",
                    "--cov-report=",
                ],
                [python, "-m", "coverage", "report", f"--fail-under={COVERAGE_FAIL_UNDER}"],
                [python, "-m", "coverage", "xml"],
            ]
        )
    return commands


def _is_isolated_family(command: Sequence[str]) -> bool:
    for family in ISOLATED_TEST_FAMILIES:
        width = len(family)
        if any(tuple(command[index : index + width]) == family for index in range(len(command) - width + 1)):
            return True
    return False


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
    mode.add_argument("--quality", action="store_true", help="run static checks and the unit-test matrix gate")
    mode.add_argument("--ci", action="store_true", help="run the complete CI-equivalent gate")
    args = parser.parse_args(argv)

    # --ci is explicit for CI readability; the default is already the complete gate.
    commands = build_commands(fast=args.fast, quality=args.quality)
    for command in commands:
        result = run_command(command)
        if result and not args.fast and not args.quality and _is_isolated_family(command):
            print("isolated family failed; retrying once", flush=True)
            result = run_command(command)
        if result:
            return 1
    print("All project checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
