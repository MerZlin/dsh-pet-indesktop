"""``petcore market``：无头市场客户端（与设置页共用同一套 market 模块）。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .. import __version__, paths
from ..config import Config
from ..net.market import HttpFetcher
from ..net.market_transport import local_file_root_for_index
from . import install, model
from .errors import MarketError
from .source import DEFAULT_INDEX_URLS, IndexFetchError, MarketSource

PROG = "petcore market"


class _OfflineFetcher:
    def fetch_index(self, url, *, etag="", timeout=8.0):
        raise IndexFetchError("offline", url)

    def download(self, *args, **kwargs):
        raise IndexFetchError("offline", str(args[0]) if args else "")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="browse and install petcore market packages")
    parser.add_argument("--root", type=Path, default=None, help="data root (default: user data dir)")
    parser.add_argument("--index-url", default="", help="custom index URL (tried before the official one)")
    parser.add_argument("--offline", action="store_true", help="use the cached index only")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list index packages and their state")
    list_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="show one package")
    show_parser.add_argument("id")
    show_parser.add_argument("--json", action="store_true")

    install_parser = subparsers.add_parser("install", help="download and install a package")
    install_parser.add_argument("id")
    install_parser.add_argument("--version", default="", help="exact version (default: latest)")
    install_parser.add_argument(
        "--allow-feature",
        action="store_true",
        help="install a feature pack even if allow_feature_mods is off",
    )
    install_parser.add_argument("--json", action="store_true")

    update_parser = subparsers.add_parser("update", help="update one or all installed packages")
    update_parser.add_argument("id", nargs="?", default="")
    update_parser.add_argument("--allow-feature", action="store_true")
    update_parser.add_argument("--json", action="store_true")

    rollback_parser = subparsers.add_parser("rollback", help="restore the previous version")
    rollback_parser.add_argument("id")
    rollback_parser.add_argument("--json", action="store_true")

    subparsers.add_parser("doctor", help="print index/cache/lock diagnostics")
    return parser


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root) if args.root is not None else paths.data_root()


def _allow_feature_mods(root: Path) -> bool:
    try:
        return bool(Config.load(root).get("allow_feature_mods"))
    except Exception:
        return False


def _download_options(root: Path) -> tuple[int, str]:
    """分段下载设置：连接数（1..8，1 = 旧单流）与镜像策略（auto/ordered/race）。"""
    try:
        config = Config.load(root)
        connections = int(config.get("market_download_connections") or 1)
        sources = str(config.get("market_download_sources") or "auto").strip().lower()
    except Exception:
        return 1, "auto"
    if sources not in {"auto", "ordered", "race"}:
        sources = "auto"
    return max(1, min(8, connections)), sources


def _source(args: argparse.Namespace) -> MarketSource:
    fetcher = _OfflineFetcher() if args.offline else HttpFetcher()
    return MarketSource(_root(args), fetcher, urls=DEFAULT_INDEX_URLS)


def _load(args: argparse.Namespace):
    root = _root(args)
    result = _source(args).load(custom_url=args.index_url, force=True)
    return root, result


def _state_for(root: Path, package: model.MarketPackage) -> model.PackageState:
    return model.package_state(
        package,
        installed_version=install.installed_version(root, package.id) or None,
        core_version=__version__,
        platform=sys.platform,
    )


def _list(args: argparse.Namespace) -> int:
    root, result = _load(args)
    rows = []
    for package in result.index.packages:
        state = _state_for(root, package)
        rows.append(
            {
                "id": package.id,
                "kind": package.kind,
                "latest": package.version,
                "state": state.state,
                "installed": state.installed,
                "reason": state.reason,
            }
        )
    if args.json:
        print(
            json.dumps(
                {
                    "origin": result.origin,
                    "stale": result.stale,
                    "generated_at": result.index.generated_at,
                    "packages": rows,
                    "problems": list(result.problems),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for row in rows:
            suffix = f" installed={row['installed']}" if row["installed"] else ""
            reason = f"  ({row['reason']})" if row["reason"] else ""
            print(f"{row['state']:20} {row['id']} {row['latest']}{suffix}{reason}")
        if result.stale:
            print("warning: index may be stale")
        for problem in result.problems:
            print(f"  ! {problem}")
    return 0 if result.index.packages or not result.stale else 1


def _show(args: argparse.Namespace) -> int:
    root, result = _load(args)
    package = result.index.by_id(args.id)
    if package is None:
        print(f"unknown package: {args.id}")
        return 2
    state = _state_for(root, package)
    if args.json:
        print(
            json.dumps(
                {
                    "id": package.id,
                    "kind": package.kind,
                    "name": package.name,
                    "name_zh": package.name_zh,
                    "summary": package.summary,
                    "author": package.author,
                    "homepage": package.homepage,
                    "latest": package.version,
                    "core_requires": package.core_requires,
                    "api_version": package.api_version,
                    "platforms": list(package.platforms),
                    "certified": package.certified,
                    "blocked": package.blocked,
                    "superseded_by": package.superseded_by,
                    "state": state.state,
                    "installed": state.installed,
                    "reason": state.reason,
                    "versions": [
                        {
                            "version": version.version,
                            "size": version.size,
                            "sha256": version.sha256,
                            "urls": list(version.urls),
                            "changelog": version.changelog,
                            "yanked": version.yanked,
                        }
                        for version in package.versions
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"{package.display_name} ({package.id})")
    print(f"kind: {package.kind}  latest: {package.version}  state: {state.state}")
    if state.installed:
        print(f"installed: {state.installed}")
    if state.reason:
        print(f"reason: {state.reason}")
    if package.author:
        print(f"author: {package.author}")
    if package.homepage:
        print(f"homepage: {package.homepage}")
    if package.core_requires:
        print(f"core_requires: {package.core_requires}")
    for version in package.versions:
        flags = " yanked" if version.yanked else ""
        print(f"  {version.version}  {version.size} bytes  {version.sha256[:12]}…{flags}")
        if version.changelog:
            print(f"    {version.changelog}")
    return 0


def _install_one(
    root: Path,
    package: model.MarketPackage,
    version: model.MarketVersion,
    *,
    allow_feature: bool,
    index_origin: str = "",
) -> install.InstallOutcome:
    local_file_root = local_file_root_for_index(index_origin)
    connections, mirror_policy = _download_options(root)
    return install.install_package(
        root,
        package,
        version,
        fetcher=HttpFetcher(),
        allow_feature=allow_feature,
        core_version=__version__,
        index_origin=index_origin,
        local_file_root=local_file_root,
        connections=connections,
        mirror_policy=mirror_policy,
    )


def _install(args: argparse.Namespace) -> int:
    root, result = _load(args)
    package = result.index.by_id(args.id)
    if package is None:
        print(f"unknown package: {args.id}")
        return 2
    if args.version:
        version = model.version_entry(package, args.version)
    else:
        version = model.latest_version(package)
    if version is None:
        print(f"no installable version for {args.id}")
        return 2

    allow = bool(args.allow_feature or _allow_feature_mods(root))
    try:
        outcome = _install_one(
            root, package, version, allow_feature=allow, index_origin=result.origin
        )
    except MarketError as exc:
        print(f"install failed: {exc}")
        return 1
    if args.json:
        print(json.dumps(dataclasses.asdict(outcome), ensure_ascii=False, indent=2))
    else:
        print(outcome.message)
        for attempt in outcome.attempts:
            print(f"  ! {attempt}")
    return 0 if outcome.ok else 1


def _update(args: argparse.Namespace) -> int:
    root, result = _load(args)
    targets = []
    for package in result.index.packages:
        if args.id and package.id != args.id:
            continue
        state = _state_for(root, package)
        if state.state == model.STATE_UPDATE:
            version = model.latest_version(package)
            if version is not None:
                targets.append((package, version))
        elif args.id and state.state == model.STATE_INSTALLED:
            print(f"{package.id} is up to date ({state.installed})")
            return 0
    if args.id and not targets and result.index.by_id(args.id) is None:
        print(f"unknown package: {args.id}")
        return 2

    allow = bool(args.allow_feature or _allow_feature_mods(root))
    failures = 0
    rows = []
    for package, version in targets:
        outcome = _install_one(
            root, package, version, allow_feature=allow, index_origin=result.origin
        )
        rows.append({"id": package.id, "ok": outcome.ok, "message": outcome.message})
        if not outcome.ok:
            failures += 1
        if not args.json:
            print(outcome.message)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    if not targets and not args.id and not args.json:
        print("nothing to update")
    return 1 if failures else 0


def _rollback(args: argparse.Namespace) -> int:
    root = _root(args)
    outcome = install.rollback(root, args.id)
    if args.json:
        print(json.dumps(dataclasses.asdict(outcome), ensure_ascii=False, indent=2))
    else:
        print(outcome.message)
    return 0 if outcome.ok else 1


def _doctor(args: argparse.Namespace) -> int:
    root, result = _load(args)
    lock = install.load_lock(root)
    downloads = install.market_root(root) / "downloads"
    download_files = list(downloads.glob("*")) if downloads.is_dir() else []
    print(f"root: {root}")
    print(f"index origin: {result.origin or '(none)'}")
    print(f"index stale: {result.stale}")
    print(f"index generated_at: {result.index.generated_at or '(unknown)'}")
    print(f"packages: {len(result.index.packages)}")
    print(f"downloads: {len(download_files)} file(s)")
    for mod_id, entry in sorted((lock.get("packages") or {}).items()):
        backup = str(entry.get("backup") or "")
        present = "present" if backup and (root / backup).is_dir() else "missing"
        print(f"lock: {mod_id} {entry.get('version', '?')} backup={present}")
    for package in result.index.packages:
        state = _state_for(root, package)
        print(f"  {state.state:20} {package.id} {package.version}")
    for problem in result.problems:
        print(f"  ! {problem}")
    return 0 if not result.stale else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command
    try:
        return {
            "list": _list,
            "show": _show,
            "install": _install,
            "update": _update,
            "rollback": _rollback,
            "doctor": _doctor,
        }[command](args)
    except MarketError as exc:
        print(f"market error: {exc}")
        return 1
    except OSError as exc:
        print(f"I/O error: {exc}")
        return 1
