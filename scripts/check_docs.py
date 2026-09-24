#!/usr/bin/env python3
"""Check repository-local Markdown links without third-party dependencies.

The checker intentionally validates file targets rather than attempting to
interpret every Markdown dialect. HTTP(S), mailto, data, and fragment-only
links are external to this check. A non-zero exit code is suitable for CI.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(\s*(?:<([^>]+)>|([^\s)]+))")
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".research",
    ".scratch",
    ".venv",
    "build",
    "build-onedir",
    "dist",
    "dist-onedir",
    "node_modules",
    "__pycache__",
    "_plan",
}
SKIP_SCHEMES = {"http", "https", "mailto", "tel", "data", "ftp"}


@dataclass(frozen=True)
class BrokenLink:
    source: Path
    line: int
    target: str
    reason: str


def iter_markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.md"):
        relative_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        files.append(path)
    return sorted(files)


def _target_path(source: Path, target: str) -> Path | None:
    target = unquote(target.strip())
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme.lower() in SKIP_SCHEMES or parsed.scheme:
        return None
    if parsed.netloc:
        return None
    path_text = parsed.path
    if not path_text:
        return None
    path = Path(path_text.replace("/", "/"))
    if path.is_absolute():
        return path
    return (source.parent / path).resolve()


def check_links(root: Path) -> list[BrokenLink]:
    broken: list[BrokenLink] = []
    root = root.resolve()
    for source in iter_markdown_files(root):
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        for match in LINK_RE.finditer(text):
            target = match.group(1) or match.group(2) or ""
            target_path = _target_path(source, target)
            if target_path is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            try:
                target_path.relative_to(root)
            except ValueError:
                broken.append(BrokenLink(source, line, target, "目标路径超出仓库根目录"))
                continue
            if not target_path.exists():
                broken.append(BrokenLink(source, line, target, "目标不存在"))
    return broken


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    broken = check_links(root)
    if broken:
        print(f"发现 {len(broken)} 个仓库内 Markdown 链接问题：")
        for item in broken:
            relative = item.source.relative_to(root).as_posix()
            print(f"- {relative}:{item.line}: {item.target} ({item.reason})")
        return 1
    print(f"Markdown link check passed: {len(iter_markdown_files(root))} files scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
