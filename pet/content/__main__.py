"""python -m pet.content 的本地资源包工具。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .manager import ContentManager


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local resource DLC packages")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("path", type=Path)
    validate.add_argument("--allow-unsigned", action="store_true")
    install = sub.add_parser("install")
    install.add_argument("path", type=Path)
    install.add_argument("--allow-unsigned", action="store_true")
    sub.add_parser("list")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("plugin_id")
    args = parser.parse_args(argv)
    manager = ContentManager()

    if args.command == "validate":
        validation = manager.validate(args.path, allow_unsigned=args.allow_unsigned)
        print("valid" if validation.valid else "invalid")
        if validation.manifest:
            print(f"{validation.manifest.plugin_id}@{validation.manifest.version}")
        for error in validation.errors:
            print(f"error: {error}")
        return 0 if validation.valid else 1
    if args.command == "install":
        installation = manager.install(args.path, allow_unsigned=args.allow_unsigned)
        print(f"installed {installation.plugin_id}@{installation.version} active={installation.activated}")
        return 0
    if args.command == "list":
        for package in manager.discover():
            print(f"{package.plugin_id}@{package.version} {package.root}")
        return 0
    manager.rollback(args.plugin_id)
    print(f"rolled back {args.plugin_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
