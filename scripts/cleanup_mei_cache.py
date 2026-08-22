# -*- coding: utf-8 -*-
"""检查或清理 PyInstaller onefile 遗留的 _MEI* 临时目录。

默认是预览模式；只有明确传入 --delete 才会删除超过年龄阈值的目录。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _configure_console_encoding() -> None:
    """让脚本在 Windows 非 UTF-8 控制台中也能输出中文。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_configure_console_encoding()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.runtime_cleanup import (  # noqa: E402
    DEFAULT_STALE_AGE_SECONDS,
    cleanup_stale_runtime_dirs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查或清理 PyInstaller onefile 遗留的 _MEI* 临时目录。"
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=None,
        help="要扫描的临时目录，默认使用 Python 的系统临时目录。",
    )
    parser.add_argument(
        "--current-dir",
        type=Path,
        default=None,
        help="要保护、不参与清理的当前 _MEI 目录。",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_STALE_AGE_SECONDS / 3600,
        help="只处理超过此小时数的目录，默认 24 小时。",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="真正删除候选目录；不传此参数时只预览。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    min_age_seconds = max(0.0, args.min_age_hours * 3600)
    result = cleanup_stale_runtime_dirs(
        args.temp_dir,
        current_dir=args.current_dir,
        min_age_seconds=min_age_seconds,
        dry_run=not args.delete,
    )

    root = args.temp_dir or "系统临时目录"
    mode = "删除模式" if args.delete else "预览模式"
    print(f"扫描位置：{root}")
    print(f"模式：{mode}；年龄阈值：{args.min_age_hours:g} 小时")
    print(f"候选目录：{len(result.candidates)}")
    for path in result.candidates:
        print(f"  - {path}")

    if not args.delete:
        print("未删除任何目录。确认所有桌宠均已退出后，再追加 --delete 执行清理。")
        return 0

    print(f"已删除：{len(result.removed)}")
    for path in result.removed:
        print(f"  - {path}")
    if result.failed:
        print(f"删除失败：{len(result.failed)}")
        for path, error in result.failed.items():
            print(f"  - {path}: {error}")
        print("如果失败目录仍在使用，请先退出所有桌宠；如果是权限异常，请用管理员 PowerShell 重试。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
